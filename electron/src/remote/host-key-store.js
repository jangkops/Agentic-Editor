'use strict';
/**
 * Host Key Store — TOFU known_hosts persistence.
 *
 * Feature: remote-ssh
 * Covers Requirements: 3.6, 3.7, 10.6, 11.3
 *
 * On first contact with a Remote_Host the Local_Editor asks the user
 * to accept the server's public key (trust-on-first-use). Subsequent
 * connections compare the live host key against the record persisted
 * here and abort the handshake on mismatch.
 *
 * Storage contract:
 *  - File: `<userData>/ssh/known_hosts`, or `~/.agentic-editor/ssh/known_hosts`
 *    when running outside Electron (tests, CLI harnesses).
 *  - Format: OpenSSH known_hosts subset — one entry per line:
 *        `<host-pattern> <keytype> <base64-key>`
 *    The `<host-pattern>` is `hostname` when port is 22 (OpenSSH default),
 *    or `[hostname]:<port>` otherwise. Comments and blank lines are
 *    preserved during rewrites.
 *  - Permissions: created with `0o600` on Unix and re-chmod'd after each
 *    write so a subsequent bad umask cannot loosen it. Windows has no
 *    portable POSIX chmod equivalent; see the TODO below for ACL work
 *    deferred to v2.
 *  - Fingerprints: `SHA256:<base64-no-padding>` exactly as OpenSSH emits
 *    them, so log output matches the format users see in `ssh(1)`.
 *
 * Concurrency: single-process, single-threaded. Reads and writes happen
 * synchronously on the Electron main thread; the file is small (one
 * line per trusted host) so a blocking read is faster than setting up
 * an async queue. Multi-process writers are not supported and would be
 * a bug at a higher layer.
 *
 * Nothing in this module throws during a read. Writes fail loudly when
 * the backing directory is unwritable so the caller (RemoteSession)
 * can surface the error to the user.
 */

const fs = require('fs');
const os = require('os');
const path = require('path');
const crypto = require('crypto');

const logger = require('./logger');

/** OpenSSH default port. Entries at this port omit the `[host]:port` bracket form. */
const DEFAULT_SSH_PORT = 22;

/** Filename for the known_hosts store under `<storeDir>`. */
const KNOWN_HOSTS_FILENAME = 'known_hosts';

/** Directory name under `userData` that holds SSH state. */
const SSH_SUBDIR = 'ssh';

/**
 * Detect Electron once at module load. Outside Electron (tests / CLI)
 * we skip the lookup on every call. Mirrors the pattern used by
 * `logger.js`.
 *
 * @type {{ app?: any } | null}
 */
let electronModule = null;
try {
  // eslint-disable-next-line global-require
  const mod = require('electron');
  if (mod && typeof mod === 'object' && mod.app) {
    electronModule = mod;
  }
} catch (_err) {
  electronModule = null;
}

/**
 * Resolve the directory that holds `known_hosts`.
 *
 *   1. `AGENTIC_EDITOR_SSH_DIR` — explicit override used by tests and
 *      integration harnesses that need isolated state.
 *   2. Electron: `<userData>/ssh`, once `app` is ready.
 *   3. Fallback: `~/.agentic-editor/ssh` — mirrors the logger fallback
 *      so operators find all feature state under a single user-level
 *      directory when running outside Electron.
 *
 * @returns {string}
 */
function resolveStoreDir() {
  const override = process.env.AGENTIC_EDITOR_SSH_DIR;
  if (override && typeof override === 'string' && override.length > 0) {
    return override;
  }
  if (electronModule && electronModule.app && typeof electronModule.app.getPath === 'function') {
    try {
      return path.join(electronModule.app.getPath('userData'), SSH_SUBDIR);
    } catch (_err) {
      // getPath throws before `app.whenReady` — fall through.
    }
  }
  return path.join(os.homedir(), '.agentic-editor', SSH_SUBDIR);
}

/**
 * Produce the OpenSSH host pattern for a (host, port) tuple.
 *
 *   formatHostPattern('gpu-01', 22)    === 'gpu-01'
 *   formatHostPattern('gpu-01', 2222)  === '[gpu-01]:2222'
 *   formatHostPattern('gpu-01', null)  === 'gpu-01'    // null / undefined → default
 *
 * @param {string} host
 * @param {number|string|null|undefined} port
 * @returns {string}
 */
function formatHostPattern(host, port) {
  const parsed = Number(port);
  const effective = Number.isFinite(parsed) && parsed > 0 ? parsed : DEFAULT_SSH_PORT;
  if (effective === DEFAULT_SSH_PORT) return String(host);
  return `[${host}]:${effective}`;
}

/**
 * Parse a single known_hosts line into its structured form, or return
 * `null` for blank lines, comments, and malformed entries. Malformed
 * lines are preserved verbatim in memory (see `_readRaw`) so that a
 * rewrite does not quietly drop data the user placed there manually.
 *
 * @param {string} line
 * @returns {{ hostPattern: string, keyType: string, keyBase64: string } | null}
 */
function parseLine(line) {
  if (typeof line !== 'string') return null;
  const trimmed = line.trim();
  if (trimmed.length === 0 || trimmed.startsWith('#')) return null;
  // Split on runs of whitespace per OpenSSH. A valid entry has at
  // least 3 fields; additional fields (comment) are tolerated but
  // not modeled in v1.
  const parts = trimmed.split(/\s+/);
  if (parts.length < 3) return null;
  return { hostPattern: parts[0], keyType: parts[1], keyBase64: parts[2] };
}

/**
 * Compute the OpenSSH SHA256 fingerprint for a public key.
 *
 * The input is the raw base64 payload as it appears on disk — i.e. the
 * "wire" SSH public-key blob without the `ssh-rsa ` prefix. OpenSSH
 * prints `SHA256:<base64-no-padding>`, and we match that verbatim so
 * the UI/log output agrees with `ssh-keygen -l` output.
 *
 * @param {string} _keyType Kept in the signature for API symmetry and
 *                         forward compat; current spec version does not
 *                         fold it into the digest because the wire blob
 *                         already encodes the key type.
 * @param {string} keyBase64
 * @returns {string}
 */
function computeFingerprint(_keyType, keyBase64) {
  const buf = Buffer.from(String(keyBase64 || ''), 'base64');
  const digest = crypto.createHash('sha256').update(buf).digest('base64');
  return 'SHA256:' + digest.replace(/=+$/, '');
}

/**
 * Coerce a caller-supplied key descriptor into the `{type, base64}`
 * shape used internally. Accepts:
 *   - `{type, base64}` — already canonical.
 *   - `{keyType, keyBase64}` — alternate spelling returned by `get()`.
 *   - ssh2 `parseKey` result — exposes `.getPublicSSH(): Buffer` and
 *     a `.type` string. The buffer is the SSH wire format, which is
 *     exactly what we store base64-encoded on disk.
 *   - `{type, data: Buffer}` — raw ssh2 style used by some callers.
 *
 * Returns `null` if the input cannot be interpreted as a public key
 * descriptor; callers translate that into `{status:'unknown'}` so a
 * malformed server key never trust-fails into `ok`.
 *
 * @param {*} key
 * @returns {{ type: string, base64: string } | null}
 */
function normalizeKey(key) {
  if (!key) return null;

  // ssh2 passes the raw SSH wire format Buffer to hostVerifier.
  // Format: [uint32 type_len][type_bytes][... rest]
  if (Buffer.isBuffer(key)) {
    try {
      if (key.length < 4) return null;
      const typeLen = key.readUInt32BE(0);
      if (typeLen <= 0 || typeLen > 64 || 4 + typeLen > key.length) return null;
      const type = key.slice(4, 4 + typeLen).toString('utf8');
      // base64 of the ENTIRE wire format — that's what known_hosts stores.
      return { type, base64: key.toString('base64') };
    } catch (_err) {
      return null;
    }
  }

  if (typeof key !== 'object') return null;

  if (typeof key.type === 'string' && typeof key.base64 === 'string' && key.base64.length > 0) {
    return { type: key.type, base64: key.base64 };
  }
  if (typeof key.keyType === 'string' && typeof key.keyBase64 === 'string' && key.keyBase64.length > 0) {
    return { type: key.keyType, base64: key.keyBase64 };
  }
  if (typeof key.getPublicSSH === 'function') {
    try {
      const buf = key.getPublicSSH();
      if (Buffer.isBuffer(buf) && typeof key.type === 'string') {
        return { type: key.type, base64: buf.toString('base64') };
      }
    } catch (_err) {
      return null;
    }
  }
  if (typeof key.type === 'string' && Buffer.isBuffer(key.data) && key.data.length > 0) {
    return { type: key.type, base64: key.data.toString('base64') };
  }
  return null;
}

/**
 * TOFU-style known_hosts store.
 *
 * Tests should instantiate the class directly with `{ dir }` to isolate
 * state; production callers import the module-level singleton below
 * which resolves the Electron userData path lazily.
 */
class HostKeyStore {
  /**
   * @param {Object} [opts]
   * @param {string} [opts.dir] Override the store directory (tests).
   */
  constructor(opts) {
    const options = opts || {};
    /** @private */ this._overrideDir = options.dir || null;
    /** @private */ this._resolvedPath = null;
  }

  /**
   * Lazily ensure the store directory and file exist, applying the
   * correct permission bits on creation. Returns the absolute path to
   * `known_hosts`. Re-invoked on every read/write so a directory
   * removed externally (e.g. by a test tearDown) is recreated
   * transparently.
   *
   * @returns {string}
   * @private
   */
  _ensure() {
    const dir = this._overrideDir || resolveStoreDir();
    try {
      fs.mkdirSync(dir, { recursive: true });
    } catch (err) {
      // Re-throw only non-EEXIST: a missing parent / EACCES must be
      // visible so the caller can surface a useful error.
      if (err && err.code !== 'EEXIST') throw err;
    }
    const filePath = path.join(dir, KNOWN_HOSTS_FILENAME);
    try {
      // `wx` creates only when absent; the 0o600 mode is honored on
      // Unix. On Windows the mode is effectively ignored and we rely
      // on inherited ACLs from userData (see the TODO below).
      const fd = fs.openSync(filePath, 'wx', 0o600);
      fs.closeSync(fd);
    } catch (err) {
      if (err && err.code !== 'EEXIST') throw err;
    }
    this._resolvedPath = filePath;
    return filePath;
  }

  /**
   * Absolute path of the backing file. Creates the file lazily if it
   * does not yet exist so callers can reliably reference it (e.g. for
   * a "Show known_hosts" command).
   *
   * @returns {string}
   */
  getStorePath() {
    return this._ensure();
  }

  /**
   * Read every raw line (including blanks and comments). Returns an
   * empty array when the file is missing or unreadable — the store is
   * meant to survive transient FS errors without crashing the
   * connection flow.
   *
   * @returns {string[]}
   * @private
   */
  _readRaw() {
    let filePath;
    try {
      filePath = this._ensure();
    } catch (_err) {
      return [];
    }
    try {
      const text = fs.readFileSync(filePath, 'utf8');
      if (text.length === 0) return [];
      // Trim exactly one trailing newline so a rewrite does not keep
      // growing blank lines at EOF, but preserve internal blank lines
      // the user may have inserted as separators.
      const withoutTrailingNewline = text.endsWith('\n') ? text.slice(0, -1) : text;
      return withoutTrailingNewline.split(/\r?\n/);
    } catch (err) {
      if (err && err.code === 'ENOENT') return [];
      return [];
    }
  }

  /**
   * Write the line array back to disk atomically (temp → rename) and
   * re-apply 0o600 on Unix. The temp-then-rename pattern guarantees
   * the store never appears truncated mid-write even if the process
   * is killed.
   *
   * @param {string[]} lines
   * @private
   */
  _writeRaw(lines) {
    const filePath = this._ensure();
    const dir = path.dirname(filePath);
    const body = lines.length === 0 ? '' : lines.join('\n') + '\n';
    const tempName = path.join(
      dir,
      '.known_hosts.tmp-' + crypto.randomBytes(6).toString('hex'),
    );
    try {
      fs.writeFileSync(tempName, body, { mode: 0o600 });
      if (process.platform !== 'win32') {
        try { fs.chmodSync(tempName, 0o600); } catch (_err) { /* best effort */ }
      }
      fs.renameSync(tempName, filePath);
    } catch (err) {
      // Clean up the temp file if rename failed.
      try { fs.unlinkSync(tempName); } catch (_err) { /* best effort */ }
      throw err;
    }
    // Re-chmod the final path after rename (rename may inherit mode
    // from the source, but a pre-existing destination on some
    // filesystems keeps its own bits).
    if (process.platform !== 'win32') {
      try { fs.chmodSync(filePath, 0o600); } catch (_err) { /* best effort */ }
    }
    // TODO(remote-ssh v2): harden ACL on Windows. Node's built-in
    // `fs` does not expose a portable ACL API; doing this correctly
    // requires shelling out to `icacls` or using a native module,
    // which is out of scope for v1. On Windows we rely on the
    // `userData` directory inheriting the current-user ACL that
    // Electron applies.
  }

  /**
   * Look up the stored public key for a host/port.
   *
   * @param {string} host
   * @param {number|string|null|undefined} port
   * @returns {{ keyType: string, keyBase64: string } | null}
   */
  get(host, port) {
    if (typeof host !== 'string' || host.length === 0) return null;
    const pattern = formatHostPattern(host, port);
    for (const line of this._readRaw()) {
      const parsed = parseLine(line);
      if (parsed && parsed.hostPattern === pattern) {
        return { keyType: parsed.keyType, keyBase64: parsed.keyBase64 };
      }
    }
    return null;
  }

  /**
   * Record (or replace) the trusted public key for a host/port.
   *
   * When a line already exists for the same pattern it is rewritten in
   * place, preserving the position of surrounding comments. Duplicate
   * matching lines (rare, usually the result of manual editing) are
   * coalesced into a single entry.
   *
   * @param {string} host
   * @param {number|string|null|undefined} port
   * @param {string} keyType    e.g. `ssh-ed25519`
   * @param {string} keyBase64  Base64 SSH wire-format public key.
   */
  add(host, port, keyType, keyBase64) {
    if (typeof host !== 'string' || host.length === 0) return;
    if (typeof keyType !== 'string' || keyType.length === 0) return;
    if (typeof keyBase64 !== 'string' || keyBase64.length === 0) return;

    const pattern = formatHostPattern(host, port);
    const newLine = pattern + ' ' + keyType + ' ' + keyBase64;
    const lines = this._readRaw();

    const next = [];
    let replaced = false;
    for (const line of lines) {
      const parsed = parseLine(line);
      if (parsed && parsed.hostPattern === pattern) {
        if (!replaced) {
          next.push(newLine);
          replaced = true;
        }
        // Drop any additional duplicate lines for the same pattern.
        continue;
      }
      next.push(line);
    }
    if (!replaced) {
      // Append after a single trailing blank line trim so files
      // written by a previous run do not grow an ever-larger EOF gap.
      while (next.length > 0 && next[next.length - 1].trim() === '') next.pop();
      next.push(newLine);
    }

    this._writeRaw(next);

    try {
      logger.info('host-key-added', {
        host,
        port: Number(port) || DEFAULT_SSH_PORT,
        keyType,
        fingerprint: computeFingerprint(keyType, keyBase64),
        replaced,
      });
    } catch (_err) {
      // Logging must never fail the write path.
    }
  }

  /**
   * Verify a live public key against the stored entry.
   *
   *   - No stored entry → `{ status: 'unknown', fingerprint }` so the
   *     caller can prompt the user for TOFU acceptance (Req 3.6).
   *   - Stored entry matches → `{ status: 'ok', fingerprint }`.
   *   - Stored entry differs → `{ status: 'mismatch', fingerprint,
   *     storedFingerprint }` and a security-event log line (Req 3.7).
   *
   * `fingerprint` is always populated when the input key parses,
   * including in the `unknown` case, so the TOFU prompt can display
   * exactly what the user is being asked to trust.
   *
   * @param {string} host
   * @param {number|string|null|undefined} port
   * @param {*} key Public key in any supported shape — see `normalizeKey`.
   * @returns {{ status: 'ok'|'unknown'|'mismatch',
   *            fingerprint: string|null,
   *            storedFingerprint?: string }}
   */
  verify(host, port, key) {
    const normalized = normalizeKey(key);
    if (!normalized) {
      return { status: 'unknown', fingerprint: null, keyType: null, keyBase64: null };
    }
    const fingerprint = computeFingerprint(normalized.type, normalized.base64);
    const stored = this.get(host, port);
    if (!stored) {
      return { status: 'unknown', fingerprint, keyType: normalized.type, keyBase64: normalized.base64 };
    }
    if (stored.keyType === normalized.type && stored.keyBase64 === normalized.base64) {
      return { status: 'ok', fingerprint };
    }
    const storedFingerprint = computeFingerprint(stored.keyType, stored.keyBase64);
    try {
      logger.warn('host-key-mismatch', {
        host,
        port: Number(port) || DEFAULT_SSH_PORT,
        fingerprint,
        storedFingerprint,
      });
    } catch (_err) {
      // ignore
    }
    return { status: 'mismatch', fingerprint, storedFingerprint, keyType: normalized.type, keyBase64: normalized.base64 };
  }

  /**
   * Convenience pass-through so callers do not have to reach into the
   * module-level helper.
   *
   * @param {string} keyType
   * @param {string} keyBase64
   * @returns {string}
   */
  computeFingerprint(keyType, keyBase64) {
    return computeFingerprint(keyType, keyBase64);
  }
}

/**
 * Process-wide singleton. Tests should construct `new HostKeyStore({dir})`
 * for isolation; production call sites use the singleton so every
 * subsystem sees the same trusted-key set.
 */
const instance = new HostKeyStore();

module.exports = instance;
module.exports.HostKeyStore = HostKeyStore;
module.exports.computeFingerprint = computeFingerprint;
module.exports.formatHostPattern = formatHostPattern;
module.exports.DEFAULT_SSH_PORT = DEFAULT_SSH_PORT;
