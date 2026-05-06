'use strict';
/**
 * Path Normalization — Windows / Unix path utilities for Remote-SSH.
 *
 * Feature: remote-ssh
 * Covers Requirements: 11.2, 11.6
 * Implements:          Correctness Property 20
 *
 * Why this module exists:
 *  - SSH_Config lookups, Host_Key_Store paths, and IdentityFile resolution
 *    need a stable key regardless of how a user mixes `\` and `/`
 *    (Requirement 11.2). On Windows a path may arrive as
 *    `C:\Users\foo\.ssh\config` or `c:/Users//foo/.ssh/config`; both must
 *    produce the same lookup key.
 *  - SSH_Config and `known_hosts` files are POSIX-only (they live in SSH
 *    land), so our canonical form uses forward slashes everywhere.
 *  - UTF-8 paths (e.g. Korean directory names) must pass through unchanged
 *    — we only rewrite ASCII separator characters (Requirement 11.6).
 *
 * Design constraints:
 *  - CommonJS module. Only Node's built-in `path` is imported (and only the
 *    `path.posix` face of it) — no `os`, no native bindings, so the module
 *    behaves identically across CI runners.
 *  - Pure functions, no I/O, no mutation of shared state.
 *  - Null-tolerant: `null`, `undefined`, and `''` map to `''` rather than
 *    throwing. Callers routinely pass through optional `HostEntry`
 *    fields (e.g. `identityFile`) and it's far nicer to propagate empty
 *    than to sprinkle null-checks everywhere.
 *
 * Property 20 restatement (from design.md):
 *   For any Windows-style path p (drive letter, backslash, mixed separators):
 *     (a) normalizeForSshConfigLookup(p) preserves the drive letter
 *         (uppercased for canonicalization),
 *     (b) converts backslashes to forward slashes,
 *     (c) collapses duplicate slashes to a single slash.
 */

const path = require('path');

/**
 * Pre-compiled regexes (module scope so they're compiled once).
 *   - DRIVE_LETTER_RE  — leading `X:` at start of string (case-insensitive).
 *   - BACKSLASH_RE     — every `\`, for a global replace.
 *   - MULTI_SLASH_RE   — any run of 2+ `/` characters.
 *   - LEADING_SLASHES  — strip leading slashes when we need to reattach
 *                       an exact count (UNC or single-root).
 */
const DRIVE_LETTER_RE = /^([A-Za-z]):/;
const BACKSLASH_RE = /\\/g;
const MULTI_SLASH_RE = /\/{2,}/g;
const LEADING_SLASHES = /^\/+/;

/**
 * Convert a path to its POSIX form: backslashes become forward slashes
 * and any run of duplicate `/` is collapsed to one.
 *
 * This is the primitive used by both `normalizeForSshConfigLookup` and
 * `joinPosix`. It deliberately does NOT touch drive letters — callers
 * that care about drive-letter canonicalization should use
 * `normalizeForSshConfigLookup` instead.
 *
 * UNC paths (`\\server\share`) collapse to `//server/share` here. The
 * leading `//` is preserved because SSH config and `known_hosts` readers
 * that accept POSIX paths also accept the POSIX-form UNC spelling as a
 * distinct root. Non-UNC leading slashes are preserved as a single `/`.
 *
 * @param {string|null|undefined} p
 * @returns {string}
 */
function toPosix(p) {
  if (p == null) return '';
  if (typeof p !== 'string') return '';
  if (p.length === 0) return '';

  // Detect UNC on the *raw* input before rewriting. A caller who already
  // half-normalized (`//server/...`) and a caller who passed the native
  // form (`\\server\...`) both land in the same branch.
  const isUnc = p.startsWith('\\\\') || p.startsWith('//');

  // Step 1: backslash → forward slash, everywhere.
  let s = p.replace(BACKSLASH_RE, '/');

  // Step 2: collapse duplicate slashes while preserving the exact count
  // of leading slashes we want (two for UNC, one otherwise).
  if (isUnc) {
    const body = s.replace(LEADING_SLASHES, '').replace(MULTI_SLASH_RE, '/');
    return '//' + body;
  }

  const hasLeadingSlash = s.startsWith('/');
  const trimmed = hasLeadingSlash ? s.replace(LEADING_SLASHES, '') : s;
  return (hasLeadingSlash ? '/' : '') + trimmed.replace(MULTI_SLASH_RE, '/');
}

/**
 * Normalize a path for stable SSH_Config / Host_Key_Store lookup.
 *
 * Algorithm:
 *   1. Null / empty → `''`. This is the "caller forgot to set a field"
 *      path and must not throw.
 *   2. Detect UNC prefix on the *raw* input (either `\\` or `//`).
 *   3. Split off an optional `X:` drive-letter prefix and uppercase it.
 *      Uppercase is chosen as canonical because OpenSSH Windows writes
 *      uppercase drive letters in `known_hosts`, so normalizing every
 *      lookup to uppercase yields the stable key the store was written
 *      with.
 *   4. Convert every backslash to forward slash in the body.
 *   5. Collapse runs of `/` to a single `/`.
 *   6. Re-assemble with the uppercased drive prefix and, for UNC, the
 *      `//` root.
 *
 * Worked examples (from the task spec):
 *   `C:\Users\foo\.ssh\config`  → `C:/Users/foo/.ssh/config`
 *   `/home/user/.ssh/config`    → `/home/user/.ssh/config`   (unchanged)
 *   `C:\\foo\\\\bar`            → `C:/foo/bar`
 *   `c:\Users\foo`              → `C:/Users/foo`             (drive upcased)
 *   `\\server\share\path`       → `//server/share/path`      (UNC)
 *   `C:foo\bar`                 → `C:foo/bar`                (drive-relative)
 *   `~/.ssh/한글/config`         → `~/.ssh/한글/config`         (UTF-8 preserved)
 *   `''` / `null` / `undefined` → `''`
 *
 * @param {string|null|undefined} p
 * @returns {string} Normalized lookup form.
 */
function normalizeForSshConfigLookup(p) {
  if (p == null) return '';
  if (typeof p !== 'string') return '';
  if (p.length === 0) return '';

  // (1) UNC detection on raw input.
  const isUnc = p.startsWith('\\\\') || p.startsWith('//');

  // (2) Peel off + uppercase the drive letter on the raw input, before
  // slash rewriting. The raw and posix-rewritten forms have the same
  // prefix here (a colon is not a separator), so the order is fine.
  let drivePrefix = '';
  let rest = p;
  const driveMatch = p.match(DRIVE_LETTER_RE);
  if (driveMatch) {
    drivePrefix = driveMatch[1].toUpperCase() + ':';
    rest = p.slice(driveMatch[0].length);
  }

  // (3) Rewrite the body to POSIX form. We reuse `toPosix` for
  // backslash replacement + slash collapsing. But `toPosix` also
  // re-applies UNC handling to its own input, which we don't want for
  // the body. We pass only the non-drive portion and rely on our
  // outer `isUnc` bit — except in the drive-less case where the body
  // IS the full input, and `toPosix` gets to make the UNC call.
  let body;
  if (drivePrefix) {
    // After the drive prefix, the rest cannot legally be UNC, so a
    // plain slash rewrite + collapse is correct. We still preserve a
    // leading slash if present (e.g. `C:\foo` → `C:/foo`).
    const bs = rest.replace(BACKSLASH_RE, '/');
    const hasLead = bs.startsWith('/');
    const trimmed = hasLead ? bs.replace(LEADING_SLASHES, '') : bs;
    body = (hasLead ? '/' : '') + trimmed.replace(MULTI_SLASH_RE, '/');
  } else {
    body = toPosix(rest);
  }

  // (4) Re-assemble. If the raw input was UNC (no drive prefix in that
  // case), `body` already starts with `//` because `toPosix` handled
  // it. So the final return is simply `drivePrefix + body`.
  void isUnc; // retained for clarity; UNC form is preserved inside `body`.
  return drivePrefix + body;
}

/**
 * Decide whether a path is absolute in Windows terms.
 *
 * Windows absolute forms:
 *   - Drive-letter absolute with a separator:  `C:\`, `C:/`, `C:\foo`
 *   - UNC:                                     `\\server\share`, `//server/share`
 *
 * A bare drive-relative path like `C:foo` (no slash after the colon) is
 * NOT Windows-absolute — the current working directory of the drive is
 * implied, so it behaves as a relative path. This matches Node's own
 * `path.win32.isAbsolute` behavior.
 *
 * @param {string|null|undefined} p
 * @returns {boolean}
 */
function isWindowsAbsolute(p) {
  if (p == null || typeof p !== 'string' || p.length === 0) return false;

  // UNC (native or POSIX-form).
  if (p.startsWith('\\\\') || p.startsWith('//')) return true;

  // Drive letter + separator.
  if (p.length >= 3) {
    const c = p.charCodeAt(0);
    const isLetter =
      (c >= 0x41 && c <= 0x5a) || (c >= 0x61 && c <= 0x7a); // A-Z / a-z
    if (isLetter && p.charAt(1) === ':') {
      const sep = p.charAt(2);
      if (sep === '\\' || sep === '/') return true;
    }
  }

  return false;
}

/**
 * Join path segments using POSIX rules, regardless of the current OS.
 *
 * This is what every Remote-SSH component should use when it needs to
 * build a path that will be sent to the Remote_Host or stored in a
 * POSIX-only file such as SSH_Config or `known_hosts`.
 *
 * Behavior:
 *   - Every segment is first passed through `toPosix` so a caller can
 *     mix styles (`joinPosix('C:\\a', 'b')` → `'C:/a/b'`).
 *   - `null`, `undefined`, and `''` segments are skipped. Without this
 *     the standard `path.posix.join` would insert stray separators.
 *   - The final value is delegated to `path.posix.join`, which handles
 *     `.` / `..` compaction and trailing-slash trimming consistently
 *     across Node versions.
 *
 * Note on Windows absoluteness: if the first segment is a Windows
 * absolute (`C:/Users`), we preserve it by short-circuiting through
 * POSIX join — `path.posix.join('C:/Users', 'foo')` yields
 * `'C:/Users/foo'`, which is exactly the form SSH_Config expects.
 *
 * @param {...(string|null|undefined)} parts
 * @returns {string}
 */
function joinPosix(...parts) {
  const cleaned = [];
  for (const part of parts) {
    if (part == null) continue;
    if (typeof part !== 'string') continue;
    if (part.length === 0) continue;
    cleaned.push(toPosix(part));
  }
  if (cleaned.length === 0) return '';
  return path.posix.join(...cleaned);
}

// ---------------------------------------------------------------------------
// UNC detection and remote-side path helpers
// ---------------------------------------------------------------------------

/**
 * Detect a UNC-style path in either its native Windows form
 * (`\\server\share\...`) or the POSIX-form spelling we emit after
 * `toPosix` normalization (`//server/share/...`).
 *
 * Rules (kept intentionally simple for v1):
 *   - Must start with exactly two separators (either `\\` or `//`),
 *     followed by a non-separator server character.
 *   - A bare `\\` or `//` prefix with no server component is NOT UNC —
 *     that's just a leading slash run.
 *   - Mixed-separator prefixes (`\\`, `/\\`, `\/`) are tolerated; we
 *     look at the first two characters only.
 *
 * @param {string|null|undefined} p
 * @returns {boolean}
 */
function isUncPath(p) {
  if (p == null || typeof p !== 'string' || p.length < 3) return false;

  const c0 = p.charAt(0);
  const c1 = p.charAt(1);
  const c2 = p.charAt(2);

  const isSep0 = c0 === '\\' || c0 === '/';
  const isSep1 = c1 === '\\' || c1 === '/';
  const isSep2 = c2 === '\\' || c2 === '/';

  // `\\server` / `//server` — 2 seps then a non-sep char.
  return isSep0 && isSep1 && !isSep2;
}

/**
 * Normalize a path for the conventions of a specific remote OS.
 *
 * remoteOs values:
 *   - `'linux'`  / `'darwin'` → POSIX target: every `\` becomes `/` and
 *     duplicate `/` runs collapse to one. Drive letters (unusual but
 *     legal input from a Windows workstation pasting a local path by
 *     accident) are left intact after the slash rewrite.
 *   - `'win32'` → Windows-on-remote target: every `/` becomes `\` and
 *     duplicate `\` runs collapse to one. Drive letters are preserved
 *     exactly as given (no case normalization — the remote side may
 *     care about the distinction `C:` vs `c:` for certain edge tools).
 *     UNC inputs stay UNC (`\\server\share\...`).
 *
 * Empty / null input returns `''`. Unknown `remoteOs` values default
 * to the POSIX treatment, which is the safer choice for SSH bridges.
 *
 * This function is the primitive that Remote_File_Bridge uses when
 * building SFTP commands against a heterogeneous fleet. It is
 * deliberately NOT the same as `normalizeForSshConfigLookup` — that
 * function produces a stable LOCAL lookup key, whereas
 * `normalizeRemotePath` produces the byte sequence the REMOTE OS will
 * actually accept.
 *
 * @param {string|null|undefined} p
 * @param {'linux'|'darwin'|'win32'} remoteOs
 * @returns {string}
 */
function normalizeRemotePath(p, remoteOs) {
  if (p == null || typeof p !== 'string' || p.length === 0) return '';

  if (remoteOs === 'win32') {
    // Preserve UNC form on the way in. We rewrite the body then
    // reattach the `\\` prefix so duplicate-separator collapsing does
    // not eat it.
    const unc = isUncPath(p);

    // Swap every forward slash to backslash first.
    let s = p.replace(/\//g, '\\');

    if (unc) {
      const body = s.replace(/^\\+/, '').replace(/\\{2,}/g, '\\');
      return '\\\\' + body;
    }

    // Collapse runs of `\` to a single `\`, but preserve a single
    // leading `\` (root-drive absolute like `\foo`).
    const hasLead = s.startsWith('\\');
    const trimmed = hasLead ? s.replace(/^\\+/, '') : s;
    return (hasLead ? '\\' : '') + trimmed.replace(/\\{2,}/g, '\\');
  }

  // POSIX remote (linux, darwin, or unknown/default).
  return toPosix(p);
}

/**
 * Join path segments using the separator convention of `remoteOs`.
 *
 *   joinRemote(['/home', 'alice', '.ssh'], 'linux')  → '/home/alice/.ssh'
 *   joinRemote(['C:\\', 'Users', 'Alice'], 'win32')  → 'C:\\Users\\Alice'
 *   joinRemote(['a', '', 'b'], 'linux')              → 'a/b'
 *
 * Behavior:
 *   - `null`, `undefined`, `''`, and non-string parts are skipped.
 *   - Each incoming part is first normalized to the remote convention
 *     via `normalizeRemotePath`, which means callers can mix styles
 *     freely (e.g. paste a Windows path into a Linux join).
 *   - For Windows-remote joins, the implementation walks the cleaned
 *     list manually because `path.win32.join` on a Linux host does not
 *     reliably preserve UNC prefixes across Node versions.
 *   - For POSIX-remote joins, we delegate to `path.posix.join` which
 *     handles `.` / `..` compaction consistently.
 *
 * @param {Array<string|null|undefined>} parts
 * @param {'linux'|'darwin'|'win32'} remoteOs
 * @returns {string}
 */
function joinRemote(parts, remoteOs) {
  if (!Array.isArray(parts)) return '';

  const cleaned = [];
  for (const part of parts) {
    if (part == null) continue;
    if (typeof part !== 'string') continue;
    if (part.length === 0) continue;
    cleaned.push(normalizeRemotePath(part, remoteOs));
  }
  if (cleaned.length === 0) return '';

  if (remoteOs !== 'win32') {
    return path.posix.join(...cleaned);
  }

  // Windows remote: manual join so UNC and drive-letter prefixes are
  // preserved verbatim on a non-Windows controller host.
  const head = cleaned[0];
  const tailJoined = cleaned
    .slice(1)
    .map((s) => s.replace(/^\\+/, '').replace(/\\+$/, ''))
    .filter((s) => s.length > 0)
    .join('\\');

  if (tailJoined.length === 0) return head;

  // Preserve a trailing separator in the head before appending, so
  // `C:\` + `Users` becomes `C:\Users` rather than `C:Users`.
  const headEndsWithSep = head.endsWith('\\');
  return headEndsWithSep ? head + tailJoined : head + '\\' + tailJoined;
}

/**
 * Split a path into its component segments using the separator
 * convention of `remoteOs`. Empty segments produced by leading /
 * trailing separators are dropped so the result is always usable as
 * input to `joinRemote`.
 *
 *   splitRemote('/home/alice/.ssh', 'linux')   → ['home', 'alice', '.ssh']
 *   splitRemote('C:\\Users\\Alice', 'win32')   → ['C:', 'Users', 'Alice']
 *   splitRemote('\\\\srv\\share\\x', 'win32')  → ['\\\\srv', 'share', 'x']
 *   splitRemote('', anything)                  → []
 *
 * For Windows remotes we treat both `\` and `/` as valid separators
 * because the Windows shell accepts either; that matches the input
 * tolerance we apply on the join side. UNC prefixes are emitted as a
 * single `\\server` segment so `joinRemote(splitRemote(p))` round-trips.
 *
 * @param {string|null|undefined} p
 * @param {'linux'|'darwin'|'win32'} remoteOs
 * @returns {string[]}
 */
function splitRemote(p, remoteOs) {
  if (p == null || typeof p !== 'string' || p.length === 0) return [];

  if (remoteOs === 'win32') {
    const unc = isUncPath(p);
    // Normalize to Windows form so separators are uniform before split.
    const normalized = normalizeRemotePath(p, 'win32');

    if (unc) {
      // Strip the leading `\\` then split on any remaining `\` run.
      // The first piece is the server, which we re-prefix with `\\` so
      // it stays recognizable as a UNC root segment.
      const body = normalized.replace(/^\\+/, '');
      const parts = body.split(/\\+/).filter((s) => s.length > 0);
      if (parts.length === 0) return [];
      return ['\\\\' + parts[0]].concat(parts.slice(1));
    }

    // Non-UNC Windows split: drop leading / trailing `\` and split.
    return normalized.split(/\\+/).filter((s) => s.length > 0);
  }

  // POSIX remote: normalize to forward slashes and split.
  const normalized = toPosix(p);
  return normalized.split('/').filter((s) => s.length > 0);
}

module.exports = {
  normalizeForSshConfigLookup,
  toPosix,
  isWindowsAbsolute,
  isUncPath,
  joinPosix,
  normalizeRemotePath,
  joinRemote,
  splitRemote,
};
