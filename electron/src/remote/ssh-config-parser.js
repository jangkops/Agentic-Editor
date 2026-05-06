'use strict';
/**
 * SSH Config Parser / Printer
 *
 * Feature: remote-ssh
 * Covers Requirements: 1.1, 1.2, 1.3, 1.6, 1.7
 *
 * Reads OpenSSH-style ~/.ssh/config files and converts them into a list of
 * HostEntry records. Also serializes a list back to SSH_Config-equivalent text.
 *
 * Design principles:
 *  - Pure module: no network I/O, no dependency on `ssh2`. File I/O only in
 *    loadFromDisk() and resolveIncludes().
 *  - Never throw on malformed input. Record a Diagnostic and continue parsing
 *    subsequent lines (Requirement 1.7).
 *  - Directive keys are matched case-insensitively (HostName == hostname).
 *  - `~` in path values is expanded using os.homedir() (or env.HOME/USERPROFILE).
 *  - Multiple IdentityFile entries accumulate into an array. ProxyJump values
 *    are comma- (or whitespace-) separated and joined into an array of hops.
 *
 * This file implements the skeleton for Task 1.1. Full Include recursion
 * semantics (cycle detection, missing-file handling beyond a warning) are
 * refined in Task 1.2.
 */

const fs = require('fs');
const path = require('path');
const os = require('os');

/**
 * @typedef {Object} HostEntry
 * @property {string}   alias                     Host pattern (first token of a Host line)
 * @property {boolean}  isWildcardOnly            true if alias consists solely of wildcard/negation chars
 * @property {string}   hostName                  Resolved HostName (defaults to alias)
 * @property {string}   user                      Resolved User (may be empty string)
 * @property {number}   port                      Port (default 22)
 * @property {string[]} identityFiles             IdentityFile paths, `~` expanded, original order preserved
 * @property {string[]} proxyJump                 Ordered list of ProxyJump hops
 * @property {string=}  proxyCommand              Raw ProxyCommand text (v1 does not execute)
 * @property {boolean=} forwardAgent              ForwardAgent yes/no
 * @property {('yes'|'no'|'ask'|'accept-new')=} strictHostKeyChecking
 * @property {string[]=} userKnownHostsFile       UserKnownHostsFile paths, `~` expanded
 * @property {boolean=} identitiesOnly            IdentitiesOnly yes/no
 * @property {string[]=} preferredAuthentications Ordered list of auth methods
 * @property {string[]} sourcePaths               Include chain — files this entry was parsed from
 * @property {number=}  lineNumber                1-indexed line number of the `Host` directive
 * @property {string[]} raw                       Original raw lines belonging to this block (best-effort)
 */

/**
 * @typedef {Object} Diagnostic
 * @property {('warn'|'error')} severity
 * @property {string} file
 * @property {number} line    1-indexed line number; 0 when not line-specific
 * @property {string} message
 */

// ---------------------------------------------------------------------------
// Constants
// ---------------------------------------------------------------------------

/**
 * Map from lowercase directive keyword to canonical HostEntry field name.
 * Used for case-insensitive directive matching.
 */
const DIRECTIVE_FIELD = Object.freeze({
  hostname: 'hostName',
  user: 'user',
  port: 'port',
  identityfile: 'identityFiles',
  proxyjump: 'proxyJump',
  proxycommand: 'proxyCommand',
  forwardagent: 'forwardAgent',
  stricthostkeychecking: 'strictHostKeyChecking',
  userknownhostsfile: 'userKnownHostsFile',
  identitiesonly: 'identitiesOnly',
  preferredauthentications: 'preferredAuthentications',
});

const STRICT_HOSTKEY_VALUES = Object.freeze(['yes', 'no', 'ask', 'accept-new']);

const MAX_INCLUDE_DEPTH = 16;

/**
 * Sentinel markers emitted by resolveIncludes() to delimit the region of
 * expanded text that originated from an Include file. parse() recognizes
 * these markers before comment stripping so that sourcePaths can be
 * maintained as a stack for every HostEntry.
 *
 * The `#__AE_INCLUDE_*__` form starts with `#` so that any downstream
 * consumer that does not recognize the marker simply treats it as a
 * comment (forward-compatible with other SSH config tools).
 */
const INCLUDE_MARKER_BEGIN = '#__AE_INCLUDE_BEGIN__';
const INCLUDE_MARKER_END = '#__AE_INCLUDE_END__';

/**
 * Canonical list of directives this parser recognizes. Public, stable API
 * used by Remote_Session and host picker modules to decide which fields
 * are safe to surface to the renderer.
 *
 * Keep in the same order as Requirement 1.3.
 */
const HOSTENTRY_DIRECTIVES = Object.freeze([
  'HostName',
  'User',
  'Port',
  'IdentityFile',
  'ProxyJump',
  'ProxyCommand',
  'ForwardAgent',
  'StrictHostKeyChecking',
  'UserKnownHostsFile',
  'IdentitiesOnly',
  'PreferredAuthentications',
]);

// ---------------------------------------------------------------------------
// Small helpers
// ---------------------------------------------------------------------------

/**
 * @param {'warn'|'error'} severity
 * @param {string} file
 * @param {number} line
 * @param {string} message
 * @returns {Diagnostic}
 */
function makeDiagnostic(severity, file, line, message) {
  return { severity, file: file || '', line: line || 0, message };
}

/**
 * Expand a leading `~` in a path to the user's home directory.
 * Non-path values or values not starting with `~` are returned unchanged.
 * @param {string} value
 * @param {string} home
 * @returns {string}
 */
function expandTilde(value, home) {
  if (typeof value !== 'string' || value.length === 0) return value;
  if (value === '~') return home;
  if (value.startsWith('~/') || value.startsWith('~\\')) {
    return path.join(home, value.slice(2));
  }
  return value;
}

/**
 * Tokenize an SSH config line: split on whitespace while honoring
 * double-quoted groups. Comments (`#`) are assumed to have been stripped
 * by the caller.
 * @param {string} line
 * @returns {string[]}
 */
function tokenize(line) {
  const out = [];
  const re = /"([^"]*)"|(\S+)/g;
  let m;
  while ((m = re.exec(line)) !== null) {
    out.push(m[1] !== undefined ? m[1] : m[2]);
  }
  return out;
}

/**
 * Return true if an alias pattern contains only wildcard/negation characters
 * (i.e. would match any host). Such entries are omitted by print().
 * @param {string} alias
 * @returns {boolean}
 */
function isWildcardOnlyAlias(alias) {
  if (!alias) return false;
  return /^[*?!\s]+$/.test(alias);
}

/**
 * Parse an OpenSSH boolean value ("yes"/"no"/"true"/"false", case-insensitive).
 * @param {string} value
 * @returns {boolean|null} null when the value is unrecognized.
 */
function parseBoolean(value) {
  const v = String(value).trim().toLowerCase();
  if (v === 'yes' || v === 'true') return true;
  if (v === 'no' || v === 'false') return false;
  return null;
}

/**
 * @param {string} alias
 * @param {string[]} sourcePaths
 * @param {number} lineNumber
 * @returns {HostEntry}
 */
function createEntry(alias, sourcePaths, lineNumber) {
  return {
    alias,
    isWildcardOnly: isWildcardOnlyAlias(alias),
    hostName: alias,
    user: '',
    port: 22,
    identityFiles: [],
    proxyJump: [],
    sourcePaths: sourcePaths.slice(),
    lineNumber,
    raw: [],
  };
}

/**
 * Resolve the home directory from an explicit override or environment.
 * @param {NodeJS.ProcessEnv|undefined} env
 * @returns {string}
 */
function resolveHome(env) {
  if (env && typeof env.HOME === 'string' && env.HOME) return env.HOME;
  if (env && typeof env.USERPROFILE === 'string' && env.USERPROFILE) return env.USERPROFILE;
  return os.homedir();
}

// ---------------------------------------------------------------------------
// Directive application
// ---------------------------------------------------------------------------

/**
 * Apply a single directive to one or more current Host entries.
 *
 * Invalid values are recorded as diagnostics; the entry is left unmodified
 * in that field. Never throws.
 *
 * @param {HostEntry} entry
 * @param {string} directive  lowercased directive name
 * @param {string[]} valueTokens  tokenized value (directive stripped)
 * @param {string} home
 * @param {string} file
 * @param {number} line
 * @param {Diagnostic[]} diagnostics
 */
function applyDirective(entry, directive, valueTokens, home, file, line, diagnostics) {
  const joined = valueTokens.join(' ');
  switch (directive) {
    case 'hostname':
      if (joined) entry.hostName = joined;
      break;

    case 'user':
      entry.user = joined;
      break;

    case 'port': {
      const n = Number.parseInt(joined, 10);
      if (!Number.isFinite(n) || n < 1 || n > 65535) {
        diagnostics.push(makeDiagnostic('warn', file, line, `Invalid Port value: ${joined}`));
      } else {
        entry.port = n;
      }
      break;
    }

    case 'identityfile': {
      if (!joined) {
        diagnostics.push(makeDiagnostic('warn', file, line, 'IdentityFile requires a path'));
        break;
      }
      entry.identityFiles.push(expandTilde(joined, home));
      break;
    }

    case 'proxyjump': {
      const hops = joined.split(/[,\s]+/).filter(Boolean);
      if (hops.length === 0) {
        diagnostics.push(makeDiagnostic('warn', file, line, 'ProxyJump requires at least one hop'));
        break;
      }
      entry.proxyJump.push(...hops);
      break;
    }

    case 'proxycommand':
      if (joined) entry.proxyCommand = joined;
      break;

    case 'forwardagent': {
      const b = parseBoolean(joined);
      if (b === null) {
        diagnostics.push(makeDiagnostic('warn', file, line, `Invalid ForwardAgent value: ${joined}`));
      } else {
        entry.forwardAgent = b;
      }
      break;
    }

    case 'stricthostkeychecking': {
      const v = joined.toLowerCase();
      if (STRICT_HOSTKEY_VALUES.indexOf(v) === -1) {
        diagnostics.push(makeDiagnostic('warn', file, line, `Invalid StrictHostKeyChecking value: ${joined}`));
      } else {
        entry.strictHostKeyChecking = /** @type {'yes'|'no'|'ask'|'accept-new'} */ (v);
      }
      break;
    }

    case 'userknownhostsfile': {
      const files = valueTokens.map((t) => expandTilde(t, home));
      if (files.length === 0) {
        diagnostics.push(makeDiagnostic('warn', file, line, 'UserKnownHostsFile requires a path'));
        break;
      }
      if (!entry.userKnownHostsFile) entry.userKnownHostsFile = [];
      entry.userKnownHostsFile.push(...files);
      break;
    }

    case 'identitiesonly': {
      const b = parseBoolean(joined);
      if (b === null) {
        diagnostics.push(makeDiagnostic('warn', file, line, `Invalid IdentitiesOnly value: ${joined}`));
      } else {
        entry.identitiesOnly = b;
      }
      break;
    }

    case 'preferredauthentications': {
      const methods = joined.split(/[,\s]+/).filter(Boolean);
      if (methods.length === 0) {
        diagnostics.push(makeDiagnostic('warn', file, line, 'PreferredAuthentications requires at least one method'));
        break;
      }
      entry.preferredAuthentications = methods;
      break;
    }

    default:
      // Unreachable — the caller only dispatches known directives.
      diagnostics.push(makeDiagnostic('warn', file, line, `Unhandled directive: ${directive}`));
  }
}

// ---------------------------------------------------------------------------
// parse()
// ---------------------------------------------------------------------------

/**
 * Parse SSH config text into a list of HostEntry records.
 *
 * Never throws. Malformed lines are recorded as diagnostics and skipped.
 * Include directives are not expanded here — callers wanting recursive
 * expansion should call resolveIncludes() first, or use loadFromDisk().
 *
 * @param {string} text
 * @param {{basePath?: string, env?: NodeJS.ProcessEnv}} [options]
 * @returns {{entries: HostEntry[], diagnostics: Diagnostic[]}}
 */
function parse(text, options) {
  const opts = options || {};
  const basePath = opts.basePath || '';
  const env = opts.env || (typeof process !== 'undefined' ? process.env : {});
  const home = resolveHome(env);
  const diagnostics = [];
  /** @type {HostEntry[]} */
  const entries = [];
  /** @type {HostEntry[]} */
  let currentEntries = [];
  let inUnsupportedMatchBlock = false;

  // Stack of files we are currently inside of, innermost last. The bottom
  // of the stack is always `basePath` (when provided). resolveIncludes()
  // emits INCLUDE_MARKER_BEGIN / INCLUDE_MARKER_END sentinel lines to let
  // parse() maintain this stack precisely across Include expansion.
  /** @type {string[]} */
  const pathStack = basePath ? [basePath] : [];
  const currentSourcePaths = () => pathStack.slice();

  const raw = String(text == null ? '' : text);
  const lines = raw.split(/\r?\n/);

  for (let i = 0; i < lines.length; i++) {
    const lineNumber = i + 1;
    const rawLine = lines[i];

    // Recognize Include sentinel markers before comment stripping — the
    // markers themselves start with `#` so they would otherwise be
    // discarded as comments. The payload after the marker keyword is the
    // absolute path of the Include target.
    const trimmedRaw = rawLine.trim();
    if (trimmedRaw.startsWith(INCLUDE_MARKER_BEGIN)) {
      const markedPath = trimmedRaw.slice(INCLUDE_MARKER_BEGIN.length).trim();
      if (markedPath) pathStack.push(markedPath);
      continue;
    }
    if (trimmedRaw.startsWith(INCLUDE_MARKER_END)) {
      if (pathStack.length > (basePath ? 1 : 0)) pathStack.pop();
      continue;
    }

    // Strip comments (outside of quotes — SSH config does not officially
    // support `#` inside values, but we play it safe by only trimming a
    // leading `#` segment).
    const commentIdx = rawLine.indexOf('#');
    const codeSection = commentIdx >= 0 ? rawLine.slice(0, commentIdx) : rawLine;
    const trimmed = codeSection.trim();
    if (!trimmed) continue;

    const tokens = tokenize(trimmed);
    if (tokens.length === 0) continue;

    // Current file (for diagnostics) is the innermost path on the stack,
    // falling back to basePath when the stack is empty.
    const currentFile = pathStack.length > 0 ? pathStack[pathStack.length - 1] : basePath;

    // Support `Keyword=Value` syntax in addition to `Keyword Value`.
    let directiveToken = tokens[0];
    let valueTokens = tokens.slice(1);
    const eqIdx = directiveToken.indexOf('=');
    if (eqIdx > 0) {
      const head = directiveToken.slice(0, eqIdx);
      const tail = directiveToken.slice(eqIdx + 1);
      directiveToken = head;
      valueTokens = tail ? [tail].concat(valueTokens) : valueTokens;
    }
    const directive = directiveToken.toLowerCase();

    if (directive === 'host') {
      inUnsupportedMatchBlock = false;
      if (valueTokens.length === 0) {
        diagnostics.push(makeDiagnostic('error', currentFile, lineNumber, 'Host directive requires at least one pattern'));
        currentEntries = [];
        continue;
      }
      // Each pattern in a `Host` line yields its own entry sharing the
      // following directives. Semantic equality with OpenSSH — a `Host a b c`
      // block applies to any of a, b, or c.
      currentEntries = valueTokens.map((pattern) => {
        const entry = createEntry(pattern, currentSourcePaths(), lineNumber);
        entries.push(entry);
        return entry;
      });
      continue;
    }

    if (directive === 'match') {
      // v1 does not support Match blocks. Record once and ignore nested
      // directives until the next Host line.
      diagnostics.push(makeDiagnostic('warn', currentFile, lineNumber, 'Match blocks are not supported in v1 and will be ignored'));
      currentEntries = [];
      inUnsupportedMatchBlock = true;
      continue;
    }

    if (directive === 'include') {
      // Include expansion is Task 1.2 territory. When parse() is called
      // on already-expanded text this branch is a no-op. When called on
      // raw text, record a diagnostic so callers know to use loadFromDisk().
      diagnostics.push(makeDiagnostic('warn', currentFile, lineNumber, 'Include directive encountered during parse(); use loadFromDisk() or resolveIncludes() to expand first'));
      continue;
    }

    if (inUnsupportedMatchBlock) {
      // Silently ignore directives inside an unsupported Match block.
      continue;
    }

    const field = DIRECTIVE_FIELD[directive];
    if (!field) {
      diagnostics.push(makeDiagnostic('warn', currentFile, lineNumber, `Unknown or unsupported directive: ${directiveToken}`));
      continue;
    }

    // Directives before any `Host` block apply to an implicit `Host *`.
    if (currentEntries.length === 0) {
      const implicit = createEntry('*', currentSourcePaths(), lineNumber);
      entries.push(implicit);
      currentEntries = [implicit];
    }

    for (const entry of currentEntries) {
      applyDirective(entry, directive, valueTokens, home, currentFile, lineNumber, diagnostics);
      entry.raw.push(rawLine);
    }
  }

  return { entries, diagnostics };
}

// ---------------------------------------------------------------------------
// print()
// ---------------------------------------------------------------------------

/**
 * Serialize a list of HostEntry records into SSH_Config-equivalent text.
 *
 * Wildcard-only entries are omitted (Requirement 1.4). Default values are
 * suppressed so that round-tripping produces a semantically equal entry list.
 *
 * @param {HostEntry[]} entries
 * @returns {string}
 */
function print(entries) {
  if (!Array.isArray(entries)) return '';
  const lines = [];

  for (const entry of entries) {
    if (!entry || typeof entry !== 'object') continue;
    if (entry.isWildcardOnly) continue;
    if (!entry.alias) continue;

    lines.push(`Host ${entry.alias}`);

    // HostName is omitted when equal to alias (OpenSSH defaults it that way).
    if (entry.hostName && entry.hostName !== entry.alias) {
      lines.push(`  HostName ${entry.hostName}`);
    }
    if (entry.user) {
      lines.push(`  User ${entry.user}`);
    }
    if (typeof entry.port === 'number' && entry.port !== 22) {
      lines.push(`  Port ${entry.port}`);
    }
    if (Array.isArray(entry.identityFiles)) {
      for (const idf of entry.identityFiles) {
        if (idf) lines.push(`  IdentityFile ${idf}`);
      }
    }
    if (Array.isArray(entry.proxyJump) && entry.proxyJump.length > 0) {
      lines.push(`  ProxyJump ${entry.proxyJump.join(',')}`);
    }
    if (entry.proxyCommand) {
      lines.push(`  ProxyCommand ${entry.proxyCommand}`);
    }
    if (entry.forwardAgent !== undefined) {
      lines.push(`  ForwardAgent ${entry.forwardAgent ? 'yes' : 'no'}`);
    }
    if (entry.strictHostKeyChecking) {
      lines.push(`  StrictHostKeyChecking ${entry.strictHostKeyChecking}`);
    }
    if (Array.isArray(entry.userKnownHostsFile) && entry.userKnownHostsFile.length > 0) {
      lines.push(`  UserKnownHostsFile ${entry.userKnownHostsFile.join(' ')}`);
    }
    if (entry.identitiesOnly !== undefined) {
      lines.push(`  IdentitiesOnly ${entry.identitiesOnly ? 'yes' : 'no'}`);
    }
    if (Array.isArray(entry.preferredAuthentications) && entry.preferredAuthentications.length > 0) {
      lines.push(`  PreferredAuthentications ${entry.preferredAuthentications.join(',')}`);
    }

    lines.push(''); // blank line between blocks for readability
  }

  return lines.join('\n');
}

// ---------------------------------------------------------------------------
// Include glob expansion helpers
// ---------------------------------------------------------------------------

/**
 * Return true if the path fragment contains OpenSSH-style glob metacharacters
 * that this parser expands (`*` and `?`). Bracket classes and `**` are not
 * supported in v1.
 * @param {string} pattern
 * @returns {boolean}
 */
function hasGlobMeta(pattern) {
  if (typeof pattern !== 'string') return false;
  return /[*?]/.test(pattern);
}

/**
 * Convert a simple glob (basename only, supporting `*` and `?`) into a
 * RegExp anchored at both ends. Dot-files are matched the same as OpenSSH —
 * they are NOT excluded implicitly.
 * @param {string} pattern
 * @returns {RegExp}
 */
function globToRegExp(pattern) {
  let body = '';
  for (let i = 0; i < pattern.length; i++) {
    const ch = pattern[i];
    if (ch === '*') body += '[^/\\\\]*';
    else if (ch === '?') body += '[^/\\\\]';
    else body += ch.replace(/[.+^${}()|[\]\\]/g, '\\$&');
  }
  return new RegExp('^' + body + '$');
}

/**
 * Expand a single Include target into a list of absolute candidate paths.
 * Handles `~` expansion, relative-path resolution against the including
 * file's directory, and shallow glob expansion on the basename segment.
 * The returned list is sorted in lexical order for deterministic parsing.
 *
 * Non-existent concrete paths are returned as-is (caller records a
 * diagnostic). Glob patterns that do not match anything return an empty
 * list (caller records a warn diagnostic).
 *
 * @param {string} target      raw token after the Include keyword
 * @param {string} includerDir directory of the file containing the Include
 * @param {string} home        home directory for `~` expansion
 * @returns {{paths: string[], globbed: boolean}}
 */
function expandIncludeTarget(target, includerDir, home) {
  const expanded = expandTilde(target, home);
  const abs = path.isAbsolute(expanded)
    ? expanded
    : path.resolve(includerDir, expanded);

  const dir = path.dirname(abs);
  const base = path.basename(abs);

  if (!hasGlobMeta(base) && !hasGlobMeta(dir)) {
    return { paths: [path.resolve(abs)], globbed: false };
  }

  // v1 only expands globs in the basename segment. Nested glob segments
  // (e.g. ~/.ssh/*/config) fall back to treating the pattern as a literal
  // path — this yields a missing-file diagnostic, which is acceptable.
  if (hasGlobMeta(dir)) {
    return { paths: [path.resolve(abs)], globbed: false };
  }

  let candidates;
  try {
    candidates = fs.readdirSync(dir);
  } catch (err) {
    // Directory unreadable — no matches. Caller will emit a "no matches"
    // diagnostic when globbed=true and paths is empty.
    return { paths: [], globbed: true };
  }

  const re = globToRegExp(base);
  const matches = candidates
    .filter((name) => re.test(name))
    .map((name) => path.resolve(dir, name))
    .sort();

  return { paths: matches, globbed: true };
}

/**
 * Return a stable identity for cycle detection. Uses fs.realpathSync to
 * collapse symlinks and relative segments, falling back to the absolute
 * path when the file does not exist yet (rare but possible when a parent
 * Include points to a not-yet-created target).
 * @param {string} absolutePath
 * @returns {string}
 */
function cycleKey(absolutePath) {
  try {
    return fs.realpathSync(absolutePath);
  } catch (_err) {
    return path.resolve(absolutePath);
  }
}

// ---------------------------------------------------------------------------
// resolveIncludes()
// ---------------------------------------------------------------------------

/**
 * Inline `Include` directives in SSH config text. Returns a new text blob
 * with every Include statement replaced by the (recursively resolved)
 * contents of the referenced file(s), delimited by sentinel markers so
 * that parse() can track the source chain.
 *
 * Behavior:
 *  - Depth is enforced per-Include: when expansion of a particular Include
 *    would exceed MAX_INCLUDE_DEPTH, that single Include is skipped with
 *    an error diagnostic; surrounding lines continue to be processed.
 *  - Glob patterns (`*`, `?`) in the basename segment are expanded to all
 *    matching files in lexical order.
 *  - Cycle detection uses fs.realpathSync (with fallback) so that symlinks
 *    and `./` variants pointing at the same file are recognized.
 *  - Missing or unreadable files produce a warn diagnostic; parsing of
 *    subsequent lines continues.
 *
 * @param {string} text
 * @param {string} basePath  Path of the file `text` was read from; relative
 *   Include paths are resolved against its directory.
 * @param {number} [depth]
 * @param {Set<string>} [visited]  Realpath keys already on the Include stack.
 * @returns {{text: string, diagnostics: Diagnostic[]}}
 */
function resolveIncludes(text, basePath, depth, visited) {
  if (depth === undefined || depth === null) depth = 0;
  if (!(visited instanceof Set)) visited = new Set();

  const diagnostics = [];
  const home = os.homedir();
  const includerDir = basePath ? path.dirname(basePath) : process.cwd();

  const lines = String(text == null ? '' : text).split(/\r?\n/);
  const out = [];

  for (let i = 0; i < lines.length; i++) {
    const lineNumber = i + 1;
    const rawLine = lines[i];
    const commentIdx = rawLine.indexOf('#');
    const codeSection = commentIdx >= 0 ? rawLine.slice(0, commentIdx) : rawLine;
    const trimmed = codeSection.trim();

    if (!trimmed) {
      out.push(rawLine);
      continue;
    }

    const tokens = tokenize(trimmed);
    if (tokens.length === 0 || tokens[0].toLowerCase() !== 'include') {
      out.push(rawLine);
      continue;
    }

    // Include directive — validate, expand, recurse.
    const targets = tokens.slice(1);
    if (targets.length === 0) {
      diagnostics.push(makeDiagnostic('warn', basePath || '', lineNumber,
        'Include requires at least one path'));
      continue;
    }

    if (depth + 1 > MAX_INCLUDE_DEPTH) {
      // Per-Include enforcement: skip this Include but keep processing the
      // rest of the file. Records one error per skipped Include line.
      diagnostics.push(makeDiagnostic('error', basePath || '', lineNumber,
        `Include depth ${depth + 1} exceeds maximum ${MAX_INCLUDE_DEPTH}; skipping`));
      continue;
    }

    for (const target of targets) {
      const { paths: candidatePaths, globbed } = expandIncludeTarget(target, includerDir, home);

      if (candidatePaths.length === 0) {
        diagnostics.push(makeDiagnostic('warn', basePath || '', lineNumber,
          globbed
            ? `Include pattern matched no files: ${target}`
            : `Include target is empty: ${target}`));
        continue;
      }

      for (const absolute of candidatePaths) {
        const key = cycleKey(absolute);

        if (visited.has(key)) {
          diagnostics.push(makeDiagnostic('warn', basePath || '', lineNumber,
            `Include cycle detected: ${absolute}`));
          continue;
        }

        let content;
        try {
          content = fs.readFileSync(absolute, 'utf-8');
        } catch (err) {
          diagnostics.push(makeDiagnostic('warn', basePath || '', lineNumber,
            `Failed to read include file: ${absolute}: ${err && err.message ? err.message : err}`));
          continue;
        }

        const nextVisited = new Set(visited);
        nextVisited.add(key);
        const nested = resolveIncludes(content, absolute, depth + 1, nextVisited);
        diagnostics.push(...nested.diagnostics);

        out.push(`${INCLUDE_MARKER_BEGIN} ${absolute}`);
        out.push(nested.text);
        out.push(`${INCLUDE_MARKER_END} ${absolute}`);
      }
    }
  }

  return { text: out.join('\n'), diagnostics };
}

// ---------------------------------------------------------------------------
// loadFromDisk()
// ---------------------------------------------------------------------------

/**
 * Locate the platform-appropriate SSH config file, read it, and return
 * its parsed HostEntry list.
 *
 *  - `$SSH_CONFIG_FILE` (if set) takes precedence.
 *  - Otherwise `~/.ssh/config` on Unix-like systems.
 *  - Otherwise `%USERPROFILE%\.ssh\config` on Windows.
 *
 * Missing config file → empty entry list with no error (Requirement 1.6).
 * Read errors → empty entry list with an error diagnostic.
 *
 * @param {{env?: NodeJS.ProcessEnv, home?: string}} [options]
 * @returns {{entries: HostEntry[], diagnostics: Diagnostic[], sourcePath: string}}
 */
function loadFromDisk(options) {
  const opts = options || {};
  const env = opts.env || (typeof process !== 'undefined' ? process.env : {});
  const home = opts.home || resolveHome(env);

  let configPath = env.SSH_CONFIG_FILE;
  if (!configPath) {
    if (process.platform === 'win32') {
      const userProfile = env.USERPROFILE || home;
      configPath = path.join(userProfile, '.ssh', 'config');
    } else {
      configPath = path.join(home, '.ssh', 'config');
    }
  }

  if (!fs.existsSync(configPath)) {
    return { entries: [], diagnostics: [], sourcePath: configPath };
  }

  let text;
  try {
    text = fs.readFileSync(configPath, 'utf-8');
  } catch (err) {
    return {
      entries: [],
      diagnostics: [makeDiagnostic('error', configPath, 0,
        `Failed to read SSH config: ${err && err.message ? err.message : err}`)],
      sourcePath: configPath,
    };
  }

  const absoluteConfig = path.resolve(configPath);
  const rootKey = cycleKey(absoluteConfig);
  const include = resolveIncludes(text, absoluteConfig, 0, new Set([rootKey]));
  const parsed = parse(include.text, { basePath: absoluteConfig, env });

  return {
    entries: parsed.entries,
    diagnostics: include.diagnostics.concat(parsed.diagnostics),
    sourcePath: configPath,
  };
}

// ---------------------------------------------------------------------------
// Exports
// ---------------------------------------------------------------------------

module.exports = {
  parse,
  print,
  loadFromDisk,
  resolveIncludes,
  HOSTENTRY_DIRECTIVES,
  // Exposed for tests / other remote-ssh modules
  _internal: {
    tokenize,
    expandTilde,
    isWildcardOnlyAlias,
    parseBoolean,
    hasGlobMeta,
    globToRegExp,
    expandIncludeTarget,
    cycleKey,
    DIRECTIVE_FIELD,
    STRICT_HOSTKEY_VALUES,
    MAX_INCLUDE_DEPTH,
    INCLUDE_MARKER_BEGIN,
    INCLUDE_MARKER_END,
  },
};
