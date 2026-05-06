'use strict';
/**
 * Remote SSH Logger — single entry point for every log sink.
 *
 * Feature: remote-ssh
 * Covers Requirements: 10.3, 12.2, 12.5
 *
 * Every console / file / IPC event log emitted by the remote-ssh
 * subsystem flows through this module. Centralizing logging guarantees
 * that the masking rule (Requirements 10.3, 12.5) is applied uniformly:
 * SSH passphrases, decrypted private key material, API tokens, 2FA
 * responses, and the like never reach any sink verbatim.
 *
 * Output contract (Requirement 12.2): newline-delimited JSON, one
 * object per line, appended to `<userData>/logs/remote-ssh.log` when
 * running inside Electron, or `~/.agentic-editor/logs/remote-ssh.log`
 * when running in a plain Node process (tests, CLI, etc.):
 *
 *   {"ts":"2026-05-06T12:34:56.789Z",
 *    "level":"info|warn|error",
 *    "event":"<event>",
 *    "alias":"<host alias or null>",
 *    "fromState":"...", "toState":"...", "reason":"...",
 *    ...extra}
 *
 * Behavior:
 *  - `mask()` / `maskKeys()` are pure so tests can exercise them with
 *    no filesystem side effects.
 *  - Append writes go through `fs.appendFile` (async) behind a
 *    sequential queue to avoid interleaved partial lines when multiple
 *    log calls fire in the same tick.
 *  - The log file is created with `0o600` on Unix so credential-bearing
 *    lines (already masked, but defense-in-depth) are readable only by
 *    the current user.
 *  - Electron detection is done once at module load via a guarded
 *    `require('electron')`. When Electron is available and a renderer
 *    window exists, each record is also broadcast as a `remote:log`
 *    IPC event so the "Show Remote Log" view can live-tail.
 *  - Nothing in this module throws. A failure while logging is the last
 *    thing you want to propagate during shutdown.
 *  - Log-file rotation is intentionally out of scope for v1.
 */

const fs = require('fs');
const os = require('os');
const path = require('path');

/**
 * Detect Electron at module load. The main process `require('electron')`
 * returns the full module object; in a plain Node context `require`
 * throws, and we simply stay in fallback mode. Cached so subsequent
 * `log()` calls do not repeat the try/catch.
 *
 * @type {{ app?: any, BrowserWindow?: any } | null}
 */
let electronModule = null;
try {
  // eslint-disable-next-line global-require
  const mod = require('electron');
  // In a renderer or test stub, `mod` can be a string path rather than
  // an object with `app` — guard explicitly.
  if (mod && typeof mod === 'object' && mod.app) {
    electronModule = mod;
  }
} catch (_err) {
  electronModule = null;
}

/**
 * Object keys whose values must be masked automatically before a log
 * record is serialized. Matches the list enumerated in the spec:
 *   passphrase, privateKey, apitoken, password, token, credential, twoFactor
 * plus a couple of common aliases (`secret`, `sessionToken`, `accessKey`)
 * to avoid accidental leaks through adjacent naming.
 *
 * Comparison is case-insensitive; see `buildSensitiveKeySet`.
 */
const DEFAULT_SENSITIVE_KEYS = Object.freeze([
  'passphrase',
  'privatekey',
  'apitoken',
  'password',
  'token',
  'credential',
  'twofactor',
  'secret',
  'sessiontoken',
  'accesskey',
]);

/**
 * Mask a credential-bearing string per Requirements 10.3 / 12.5.
 *
 *   mask('hello')    === 'h****'
 *   mask('ab')       === 'a****'
 *   mask('a')        === '****'   // too short to reveal leading char
 *   mask('')         === '****'
 *   mask(undefined)  === '****'
 *   mask(null)       === '****'
 *   mask(1234)       === '****'   // non-string rejected
 *
 * @param {unknown} s
 * @returns {string}
 */
function mask(s) {
  if (typeof s !== 'string') return '****';
  if (s.length < 2) return '****';
  return s[0] + '****';
}

/**
 * Build a case-insensitive lookup set for the sensitive-key list so
 * `maskKeys` does not repeat the `toLowerCase` work on every field.
 *
 * @param {Iterable<string>|undefined} keys
 * @returns {Set<string>}
 */
function buildSensitiveKeySet(keys) {
  const set = new Set();
  const source = keys || DEFAULT_SENSITIVE_KEYS;
  for (const k of source) {
    if (typeof k === 'string' && k.length > 0) {
      set.add(k.toLowerCase());
    }
  }
  return set;
}

/**
 * Recursively clone `obj` replacing any field whose key (case-insensitive)
 * matches `keys` (default: `DEFAULT_SENSITIVE_KEYS`) with `mask(value)`.
 * Arrays are preserved. Cycles are detected via a `WeakSet` so a
 * self-referential payload cannot hang the logger.
 *
 * Returns a new object — the input is never mutated.
 *
 * @template T
 * @param {T} obj
 * @param {Iterable<string>} [keys]
 * @returns {T}
 */
function maskKeys(obj, keys) {
  const keyset = buildSensitiveKeySet(keys);
  const seen = new WeakSet();

  function walk(value) {
    if (value === null || value === undefined) return value;
    if (typeof value !== 'object') return value;
    if (seen.has(value)) return '[Circular]';
    seen.add(value);

    if (Array.isArray(value)) {
      return value.map(walk);
    }

    const out = {};
    for (const key of Object.keys(value)) {
      if (keyset.has(key.toLowerCase())) {
        const raw = value[key];
        out[key] = typeof raw === 'string' ? mask(raw) : '****';
      } else {
        out[key] = walk(value[key]);
      }
    }
    return out;
  }

  return walk(obj);
}

/**
 * Resolve the directory that should contain `remote-ssh.log`.
 *
 *   1. `process.env.AGENTIC_EDITOR_LOG_DIR` — explicit override used by
 *      tests and non-Electron harnesses.
 *   2. Electron: `app.getPath('userData')/logs` once the app is ready.
 *   3. Fallback: `~/.agentic-editor/logs` (Requirement 12.2, portable).
 *
 * The directory is lazily created on first write; creation failures are
 * swallowed and re-tried on the next call.
 *
 * @returns {string}
 */
function resolveLogDir() {
  const override = process.env.AGENTIC_EDITOR_LOG_DIR;
  if (override && typeof override === 'string' && override.length > 0) {
    return override;
  }

  if (electronModule && electronModule.app && typeof electronModule.app.getPath === 'function') {
    try {
      return path.join(electronModule.app.getPath('userData'), 'logs');
    } catch (_err) {
      // `getPath` throws when the app is not yet ready — fall through.
    }
  }

  return path.join(os.homedir(), '.agentic-editor', 'logs');
}

/**
 * Ensure `dir` exists. Silent on failure.
 *
 * @param {string} dir
 */
function ensureDir(dir) {
  try {
    fs.mkdirSync(dir, { recursive: true });
  } catch (_err) {
    // Never throw from the log path.
  }
}

/**
 * Ensure the log file exists with `0o600` on Unix-like systems
 * (Requirement 10.3/12.5 defense-in-depth). On Windows the mode flag
 * is a no-op, which is fine — NTFS ACLs inherit from the parent dir.
 *
 * The chmod only runs the first time the file is created so the hot
 * path stays free of `fs.stat` probes.
 *
 * @param {string} filePath
 */
function ensureFile(filePath) {
  try {
    // `wx` = create if absent, fail if present.
    const fd = fs.openSync(filePath, 'wx', 0o600);
    fs.closeSync(fd);
  } catch (err) {
    // EEXIST is the happy case; anything else is swallowed.
    if (err && err.code !== 'EEXIST') return;
  }
}

/**
 * Serialize a single log record to a newline-delimited JSON string.
 * A serialization failure (e.g. a BigInt sneaking into the payload)
 * degrades to a stub line so the record never disappears silently.
 *
 * @param {string} level
 * @param {string} event
 * @param {object} fields Already-masked payload fields.
 * @returns {string}
 */
function formatLine(level, event, fields) {
  // Build the record with the fixed keys first for readable log output.
  const record = Object.assign(
    {
      ts: new Date().toISOString(),
      level,
      event,
    },
    fields && typeof fields === 'object' ? fields : {}
  );

  try {
    return JSON.stringify(record) + '\n';
  } catch (_err) {
    const fallback = {
      ts: record.ts,
      level,
      event,
      error: 'serialization-failed',
    };
    return JSON.stringify(fallback) + '\n';
  }
}

/**
 * Best-effort broadcast of a structured record to every open renderer
 * via `webContents.send('remote:log', record)`. Only runs when Electron
 * was detected at module load and `BrowserWindow.getAllWindows()` is
 * callable. Failures (including the very common "window destroyed")
 * are swallowed so the file log stays reliable even if a renderer
 * crashes mid-write.
 *
 * @param {object} record
 */
function broadcastToRenderer(record) {
  if (!electronModule || !electronModule.BrowserWindow) return;
  let windows;
  try {
    windows = electronModule.BrowserWindow.getAllWindows();
  } catch (_err) {
    return;
  }
  if (!Array.isArray(windows) || windows.length === 0) return;
  for (const win of windows) {
    try {
      if (!win || win.isDestroyed()) continue;
      const wc = win.webContents;
      if (!wc || wc.isDestroyed()) continue;
      wc.send('remote:log', record);
    } catch (_err) {
      // A single bad window must not block the rest.
    }
  }
}

/**
 * Heuristic dev-mode detection. In dev, we mirror records to the
 * console so operators watching `electron .` see live log output.
 *
 *   1. `NODE_ENV === 'development'`
 *   2. Electron `!app.isPackaged`
 *
 * @returns {boolean}
 */
function isDevelopment() {
  if (process.env.NODE_ENV === 'development') return true;
  if (electronModule && electronModule.app && electronModule.app.isPackaged === false) return true;
  return false;
}

/**
 * Single-entry-point logger for the remote-ssh subsystem.
 *
 * All console / file / IPC logging MUST go through this class. The
 * constructor has no side effects; the log directory is resolved
 * lazily on the first write so instantiating a Logger in a test
 * harness does not touch disk.
 */
class Logger {
  /**
   * @param {Object} [opts]
   * @param {string[]} [opts.sensitiveKeys] Override default masking keys.
   * @param {string}   [opts.logDir]        Override the resolved directory.
   * @param {string}   [opts.fileName]      Override the log file name.
   * @param {boolean}  [opts.console]       Force-enable console mirror.
   * @param {boolean}  [opts.ipc]           Force-enable/disable IPC broadcast.
   */
  constructor(opts) {
    const options = opts || {};
    /** @private */ this._sensitiveKeys = options.sensitiveKeys || DEFAULT_SENSITIVE_KEYS;
    /** @private */ this._overrideDir = options.logDir || null;
    /** @private */ this._fileName = options.fileName || 'remote-ssh.log';
    /** @private */ this._consoleMirror = options.console;
    /** @private */ this._ipcEnabled = options.ipc;
    /** @private */ this._resolvedPath = null;
    /** @private */ this._writeQueue = Promise.resolve();
  }

  /**
   * Lazily resolve the absolute log file path. Cached after the first
   * call. The directory is re-ensured on every call (a cheap recursive
   * mkdir) in case an external process removed it between writes.
   *
   * @returns {string}
   * @private
   */
  _resolvePath() {
    if (this._resolvedPath) {
      ensureDir(path.dirname(this._resolvedPath));
      return this._resolvedPath;
    }
    const dir = this._overrideDir || resolveLogDir();
    ensureDir(dir);
    const filePath = path.join(dir, this._fileName);
    ensureFile(filePath);
    this._resolvedPath = filePath;
    return filePath;
  }

  /**
   * Enqueue an async append. Returns the queue promise so tests can
   * `await logger.log(...)`; production callers can fire-and-forget.
   *
   * @param {string} line
   * @returns {Promise<void>}
   * @private
   */
  _append(line) {
    const filePath = this._resolvePath();
    const next = this._writeQueue.then(
      () => new Promise((resolve) => {
        fs.appendFile(filePath, line, 'utf8', () => resolve());
      }),
      () => Promise.resolve()
    );
    // A failed append must not poison subsequent writes.
    this._writeQueue = next.catch(() => undefined);
    return next;
  }

  /**
   * Core log method — the single entry point required by the spec.
   * Masks the payload, serializes an NDJSON line, appends it to the
   * file, broadcasts a `remote:log` IPC event when running inside
   * Electron, and mirrors to the console in development mode.
   *
   * Returns a promise so callers that need write ordering (e.g. tests)
   * can await; production callers generally ignore it.
   *
   * @param {'info'|'warn'|'error'} level
   * @param {string} event
   * @param {object} [fields]
   * @returns {Promise<void>}
   */
  log(level, event, fields) {
    try {
      const masked = maskKeys(fields || {}, this._sensitiveKeys);
      const line = formatLine(level, event, masked);

      // Parse once so both the IPC broadcast and the console mirror
      // receive the same structured record that lands on disk.
      let record = null;
      try {
        record = JSON.parse(line);
      } catch (_err) {
        record = null;
      }

      const ipcEnabled = this._ipcEnabled !== false; // default on when Electron present
      if (ipcEnabled && record) {
        broadcastToRenderer(record);
      }

      const consoleMirror = this._consoleMirror === true
        || (this._consoleMirror !== false && isDevelopment());
      if (consoleMirror && typeof console !== 'undefined') {
        const fn = console[level] || console.log;
        try {
          fn.call(console, '[remote-ssh]', line.trimEnd());
        } catch (_err) {
          // ignore
        }
      }

      return this._append(line);
    } catch (_err) {
      // Logging must not throw under any circumstance.
      return Promise.resolve();
    }
  }

  /** Convenience wrapper. @param {string} event @param {object} [fields] */
  info(event, fields) {
    return this.log('info', event, fields);
  }

  /** Convenience wrapper. @param {string} event @param {object} [fields] */
  warn(event, fields) {
    return this.log('warn', event, fields);
  }

  /** Convenience wrapper. @param {string} event @param {object} [fields] */
  error(event, fields) {
    return this.log('error', event, fields);
  }

  /**
   * Emit a Requirement-12.2 state-transition record.
   *
   *   { ts, level:'info', event:'state', alias, fromState, toState, reason }
   *
   * Used by `RemoteSession` on every state change so the log file is
   * the canonical audit trail of Connection_State transitions.
   *
   * @param {string|null} alias
   * @param {string} fromState
   * @param {string} toState
   * @param {string} [reason]
   * @returns {Promise<void>}
   */
  logStateTransition(alias, fromState, toState, reason) {
    return this.log('info', 'state', {
      alias: alias == null ? null : String(alias),
      fromState: String(fromState),
      toState: String(toState),
      reason: reason == null ? '' : String(reason),
    });
  }

  /**
   * Absolute path this logger is currently writing to. Used by the
   * `remote:show-log` handler to open the file in a read-only tab.
   *
   * @returns {string}
   */
  getLogPath() {
    return this._resolvePath();
  }

  /**
   * Wait for any pending writes to flush. Best-effort — if an append
   * was in flight it will resolve here; subsequent writes queue as
   * usual. Useful for tests and shutdown hooks.
   *
   * @returns {Promise<void>}
   */
  flush() {
    return this._writeQueue.then(() => undefined, () => undefined);
  }
}

/**
 * Process-wide singleton. Every import returns the same instance so
 * that the file queue stays serialized across call sites. Tests that
 * need isolation can `new Logger({ logDir: tmp })` directly.
 */
const instance = new Logger();

module.exports = instance;
module.exports.Logger = Logger;
module.exports.mask = mask;
module.exports.maskKeys = maskKeys;
// Backwards-compatible alias in case future code uses the older name.
module.exports.maskObject = maskKeys;
module.exports.DEFAULT_SENSITIVE_KEYS = DEFAULT_SENSITIVE_KEYS;
