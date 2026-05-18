'use strict';
/**
 * Remote-SSH Error Surface — common builder that normalizes every
 * failure the remote subsystem produces into a shape the UI can render
 * without inspecting internal error types.
 *
 * Feature: remote-ssh
 * Covers Requirement: 12.4 (user-facing error messages include host
 * alias, Connection_State at the time of failure, and a short
 * remediation hint).
 *
 * Return contract:
 *
 *   {
 *     code: string,            // canonical kebab-case code
 *     category: string,        // 'network'|'auth'|'provisioning'|'fs'|'terminal'|'unknown'
 *     alias: string|null,      // host alias (null when unknown)
 *     state: string|null,      // last known Connection_State
 *     cause: string,           // human-readable underlying message
 *     remediationHint: string  // category-default or error-specific hint
 *   }
 *
 * Also re-registers the `remote:show-log` IPC handler so Task 28.2 can
 * wire the "Show Remote Log" command with a single call. The handler
 * is registered idempotently — if a previous registration exists (the
 * legacy `ipc-remote-handlers.js` registers the same channel), it is
 * replaced via `ipcMain.removeHandler`.
 */

// ---------------------------------------------------------------------------
// Category vocabulary + default remediation hints (Req 12.4).
// ---------------------------------------------------------------------------

const CATEGORIES = Object.freeze({
  NETWORK: 'network',
  AUTH: 'auth',
  PROVISIONING: 'provisioning',
  FS: 'fs',
  TERMINAL: 'terminal',
  UNKNOWN: 'unknown',
});

const DEFAULT_HINTS = Object.freeze({
  network:
    'Check network connectivity and that the SSH service is running on the remote host.',
  auth:
    'Verify your credentials and that the remote host accepts the configured authentication method (PreferredAuthentications).',
  provisioning:
    'Review ~/.agentic-editor/server.log on the remote host. Ensure Python 3.11+ is installed.',
  fs:
    'Check path existence and permissions on the remote host.',
  terminal:
    'The remote shell disconnected. Try reconnecting or opening a new terminal.',
  unknown:
    "Run 'Show Remote Log' from the command palette for details.",
});

// ---------------------------------------------------------------------------
// Legacy Korean hint table — kept as an exported constant so callers
// that imported `HINTS` from the v1 stub continue to work. New code
// should rely on `DEFAULT_HINTS` and category-based lookup.
// ---------------------------------------------------------------------------

const HINTS = Object.freeze({
  'ssh2-not-installed': 'ssh2 설치 필요: npm install ssh2',
  'auth-failed': 'SSH 키/비밀번호를 확인하세요',
  'auth-failure-storm': '1분 내 3회 인증 실패. 잠시 후 재시도',
  'host-key-mismatch': '호스트 키 변경 감지! known_hosts 확인',
  'handshake-error': '핸드셰이크 실패. 호스트/포트 확인',
  'port-exhausted': '포트 부족. lsof -i:18765-18865 확인',
  'python-missing': '원격 호스트에 Python 3.11+ 필요',
  'not-connected': '원격 세션 미연결',
});

// ---------------------------------------------------------------------------
// Error-code classification.
// ---------------------------------------------------------------------------

/**
 * Map of raw Node.js / ssh2 error codes to the canonical kebab-case
 * code + category the UI speaks. Ordered so the most specific match
 * wins; lookup is done in `classify()` below.
 */
const CODE_MAP = Object.freeze({
  // --- network ------------------------------------------------------------
  ECONNREFUSED:  { code: 'connection-refused',   category: CATEGORIES.NETWORK },
  ETIMEDOUT:     { code: 'connection-timeout',   category: CATEGORIES.NETWORK },
  ETIMEOUT:      { code: 'connection-timeout',   category: CATEGORIES.NETWORK },
  ENETUNREACH:   { code: 'network-unreachable',  category: CATEGORIES.NETWORK },
  EHOSTUNREACH:  { code: 'host-unreachable',     category: CATEGORIES.NETWORK },
  ENOTFOUND:     { code: 'host-not-found',       category: CATEGORIES.NETWORK },
  ECONNRESET:    { code: 'connection-reset',     category: CATEGORIES.NETWORK },

  // --- already-canonical network codes ----------------------------------
  'connection-refused':  { code: 'connection-refused',  category: CATEGORIES.NETWORK },
  'connection-timeout':  { code: 'connection-timeout',  category: CATEGORIES.NETWORK },
  'network-unreachable': { code: 'network-unreachable', category: CATEGORIES.NETWORK },
  'host-unreachable':    { code: 'host-unreachable',    category: CATEGORIES.NETWORK },
  'host-not-found':      { code: 'host-not-found',      category: CATEGORIES.NETWORK },
  'handshake-error':     { code: 'handshake-error',     category: CATEGORIES.NETWORK },
  'connection-closed':   { code: 'connection-closed',   category: CATEGORIES.NETWORK },
  'ssh-client-error':    { code: 'ssh-client-error',    category: CATEGORIES.NETWORK },

  // --- auth --------------------------------------------------------------
  'auth-failed':         { code: 'auth-failed',         category: CATEGORIES.AUTH },
  'auth-failure-storm':  { code: 'auth-failure-storm',  category: CATEGORIES.AUTH },
  'host-key-mismatch':   { code: 'host-key-mismatch',   category: CATEGORIES.AUTH },
  'host-key-rejected':   { code: 'host-key-rejected',   category: CATEGORIES.AUTH },
  'prompt-timeout':      { code: 'prompt-timeout',      category: CATEGORIES.AUTH },

  // --- provisioning ------------------------------------------------------
  'python-unsupported':          { code: 'python-unsupported', category: CATEGORIES.PROVISIONING },
  'python-missing':              { code: 'python-missing',     category: CATEGORIES.PROVISIONING },
  'provisioning-failed':         { code: 'provisioning-failed', category: CATEGORIES.PROVISIONING },
  'port-occupied':               { code: 'port-occupied',       category: CATEGORIES.PROVISIONING },
  'port-occupied-by-other-service': { code: 'port-occupied',    category: CATEGORIES.PROVISIONING },
  'port-exhausted':              { code: 'port-exhausted',      category: CATEGORIES.PROVISIONING },
  'supervisor-failed':           { code: 'supervisor-failed',   category: CATEGORIES.PROVISIONING },

  // --- fs ----------------------------------------------------------------
  EACCES:                { code: 'permission-denied',   category: CATEGORIES.FS },
  EPERM:                 { code: 'permission-denied',   category: CATEGORIES.FS },
  ENOENT:                { code: 'path-not-found',      category: CATEGORIES.FS },
  ENOSPC:                { code: 'disk-full',           category: CATEGORIES.FS },
  EIO:                   { code: 'io-error',            category: CATEGORIES.FS },
  'permission-denied':   { code: 'permission-denied',   category: CATEGORIES.FS },
  'path-not-found':      { code: 'path-not-found',      category: CATEGORIES.FS },
  'file-too-large':      { code: 'file-too-large',      category: CATEGORIES.FS },
  'large-file':          { code: 'file-too-large',      category: CATEGORIES.FS },
  permission:            { code: 'permission-denied',   category: CATEGORIES.FS },
  'disk-full':           { code: 'disk-full',           category: CATEGORIES.FS },
  io:                    { code: 'io-error',            category: CATEGORIES.FS },
  rename:                { code: 'remote-write-error',  category: CATEGORIES.FS },
  fsync:                 { code: 'remote-write-error',  category: CATEGORIES.FS },
});

/**
 * Map of error-class names (`err.name` or `err.constructor.name`)
 * to canonical (code, category). Used when the raw `err.code` is
 * missing or ambiguous.
 */
const NAME_MAP = Object.freeze({
  PythonUnsupportedError:       { code: 'python-unsupported',        category: CATEGORIES.PROVISIONING },
  ProvisioningError:            { code: 'provisioning-failed',       category: CATEGORIES.PROVISIONING },
  PortOccupiedByOtherServiceError: { code: 'port-occupied',          category: CATEGORIES.PROVISIONING },
  PortExhaustedError:           { code: 'port-exhausted',            category: CATEGORIES.PROVISIONING },
  LargeFileError:               { code: 'file-too-large',            category: CATEGORIES.FS },
  RemoteWriteError:             { code: 'remote-write-error',        category: CATEGORIES.FS },
});

/**
 * Heuristic message-based classifier. Runs after the code/name lookups
 * and picks up the two strings ssh2 and remote-terminal-bridge emit
 * verbatim.
 *
 * @param {string} msg Already lower-cased error message.
 * @returns {{code: string, category: string} | null}
 */
function classifyByMessage(msg) {
  if (!msg) return null;

  // ssh2 verbatim auth failure (Requirements 3.8).
  if (msg.includes('all configured authentication methods failed')) {
    return { code: 'auth-failed', category: CATEGORIES.AUTH };
  }
  if (msg.includes('authentication failed')) {
    return { code: 'auth-failed', category: CATEGORIES.AUTH };
  }

  // Terminal detach signals (Requirements 7.4 / 8.5).
  if (msg.includes('disconnected') || msg.includes('shell exited') || msg.includes('pty exited')) {
    return { code: 'terminal-disconnected', category: CATEGORIES.TERMINAL };
  }
  if (msg.includes('exited') && msg.includes('remote')) {
    return { code: 'terminal-disconnected', category: CATEGORIES.TERMINAL };
  }

  return null;
}

/**
 * Classify `err` into `{code, category}`. Pure function — no side
 * effects, safe to call from tests.
 *
 * Classification priority:
 *   1. Explicit `err.category` if it is one of the known categories
 *      (callers may pre-tag their errors; we respect that).
 *   2. `err.code` lookup in `CODE_MAP`.
 *   3. `err.name` / `err.constructor.name` lookup in `NAME_MAP`.
 *   4. Lower-cased `err.message` heuristics.
 *   5. Fallback `{code: 'unknown', category: 'unknown'}`.
 *
 * @param {unknown} err
 * @returns {{code: string, category: string}}
 */
function classify(err) {
  if (!err || typeof err !== 'object') {
    return { code: 'unknown', category: CATEGORIES.UNKNOWN };
  }

  // 1. Honor a pre-tagged category if valid.
  if (typeof err.category === 'string') {
    const cat = err.category.toLowerCase();
    if (Object.values(CATEGORIES).includes(cat)) {
      const code = typeof err.code === 'string' && err.code.length > 0 ? err.code : cat;
      return { code, category: cat };
    }
  }

  // 2. err.code lookup.
  const rawCode = typeof err.code === 'string' ? err.code : null;
  if (rawCode && Object.prototype.hasOwnProperty.call(CODE_MAP, rawCode)) {
    return CODE_MAP[rawCode];
  }

  // 3. err.name / constructor.name lookup.
  const name =
    (typeof err.name === 'string' && err.name) ||
    (err.constructor && typeof err.constructor.name === 'string' && err.constructor.name) ||
    null;
  if (name && Object.prototype.hasOwnProperty.call(NAME_MAP, name)) {
    return NAME_MAP[name];
  }

  // 4. Message heuristic.
  const msg = typeof err.message === 'string' ? err.message.toLowerCase() : '';
  const byMsg = classifyByMessage(msg);
  if (byMsg) return byMsg;

  // 5. Fallback — preserve whatever raw code the error carries so
  // log consumers can still grep it.
  return {
    code: rawCode || 'unknown',
    category: CATEGORIES.UNKNOWN,
  };
}

// ---------------------------------------------------------------------------
// Context extraction.
// ---------------------------------------------------------------------------

/**
 * Pull the `alias` and `state` out of a context value. Supports three
 * calling conventions for convenience:
 *   - a RemoteSession instance (has `.alias` and `.state` getters, or
 *     a nested `._target.alias` on the internal shape)
 *   - a plain object `{alias, state}` (typical IPC handler context)
 *   - null / undefined (happens when the failure is pre-session)
 *
 * Values are coerced to strings when present and trimmed; empty
 * strings collapse to `null` so the caller can distinguish "missing"
 * from "blank".
 *
 * @param {unknown} ctx
 * @returns {{alias: string|null, state: string|null}}
 */
function extractContext(ctx) {
  if (!ctx || typeof ctx !== 'object') return { alias: null, state: null };

  let aliasSrc =
    ctx.alias ||
    ctx.hostAlias ||
    (ctx._target && ctx._target.alias) ||
    (ctx.target && ctx.target.alias) ||
    null;
  let stateSrc = ctx.state || ctx.connectionState || null;

  const alias = typeof aliasSrc === 'string' && aliasSrc.trim().length > 0
    ? aliasSrc.trim()
    : null;
  const state = typeof stateSrc === 'string' && stateSrc.trim().length > 0
    ? stateSrc.trim()
    : null;

  return { alias, state };
}

// ---------------------------------------------------------------------------
// Public builder.
// ---------------------------------------------------------------------------

/**
 * Normalize any remote-subsystem error into the Req 12.4 surface shape.
 *
 * @param {unknown} err   The underlying error (Error instance, ssh2
 *                        callback object, or raw code string).
 * @param {object} [context] Session or `{alias, state}` snapshot captured at
 *                           the time of failure.
 * @returns {{
 *   code: string,
 *   category: string,
 *   alias: string|null,
 *   state: string|null,
 *   cause: string,
 *   remediationHint: string
 * }}
 */
function surfaceError(err, context) {
  const { code, category } = classify(err);
  const { alias, state } = extractContext(context);

  // Derive a human-readable cause message.
  let cause;
  if (err && typeof err === 'object') {
    if (typeof err.message === 'string' && err.message.length > 0) {
      cause = err.message;
    } else if (typeof err.code === 'string') {
      cause = err.code;
    } else {
      cause = 'Unknown error';
    }
  } else if (typeof err === 'string' && err.length > 0) {
    cause = err;
  } else {
    cause = 'Unknown error';
  }

  // Prefer an error-specific hint when the error itself carries one;
  // otherwise fall back to the category default (Req 12.4 guarantees
  // remediationHint is non-empty).
  let remediationHint;
  if (err && typeof err === 'object' && typeof err.remediationHint === 'string' && err.remediationHint.trim().length > 0) {
    remediationHint = err.remediationHint;
  } else {
    remediationHint = DEFAULT_HINTS[category] || DEFAULT_HINTS.unknown;
  }

  return {
    code,
    category,
    alias,
    state,
    cause,
    remediationHint,
  };
}

// ---------------------------------------------------------------------------
// IPC registration helper.
// ---------------------------------------------------------------------------

/**
 * Read the log file path from a logger module, supporting both the
 * modern `getLogPath()` API (see `logger.js`) and the legacy
 * `logFilePath` property used by some older call sites. Returns an
 * empty string when neither is available so the IPC handler never
 * throws on a misconfigured logger.
 *
 * @param {unknown} logger
 * @returns {string}
 */
function resolveLogPath(logger) {
  if (!logger) return '';
  try {
    if (typeof logger.getLogPath === 'function') {
      const p = logger.getLogPath();
      return typeof p === 'string' ? p : '';
    }
  } catch (_err) {
    // fall through to the legacy field
  }
  try {
    if (typeof logger.logFilePath === 'string') return logger.logFilePath;
  } catch (_err) {
    // ignore
  }
  return '';
}

/**
 * Register the `remote:show-log` IPC handler against the given
 * `ipcMain`. The handler returns `{path}` where `path` is the absolute
 * log-file path resolved from `logger`.
 *
 * Registration is idempotent — if another module already registered
 * the same channel, it is replaced via `ipcMain.removeHandler`. This
 * lets Task 28.2 invoke `registerErrorSurfaceHandlers(ipcMain, logger)`
 * without caring whether the legacy `ipc-remote-handlers.js` path has
 * already wired its own copy.
 *
 * Safe to call with `null` / non-Electron `ipcMain` — in that case
 * the function is a no-op, which keeps plain Node test harnesses
 * and the renderer process clean.
 *
 * @param {object} ipcMain Electron `ipcMain` (or a test double).
 * @param {object} logger  Remote-SSH logger instance.
 * @returns {{dispose: function(): void}} Handle with a `dispose` helper
 *   that removes the handler — useful for tests.
 */
function registerErrorSurfaceHandlers(ipcMain, logger) {
  const noop = { dispose() { /* no-op */ } };
  if (!ipcMain || typeof ipcMain.handle !== 'function') return noop;

  try {
    if (typeof ipcMain.removeHandler === 'function') {
      ipcMain.removeHandler('remote:show-log');
    }
  } catch (_err) {
    // `removeHandler` throws on Electron versions where the channel
    // was never registered — swallow so the subsequent `handle` runs.
  }

  try {
    ipcMain.handle('remote:show-log', () => {
      try {
        return { path: resolveLogPath(logger) };
      } catch (_err) {
        return { path: '' };
      }
    });
  } catch (_err) {
    return noop;
  }

  return {
    dispose() {
      try {
        if (typeof ipcMain.removeHandler === 'function') {
          ipcMain.removeHandler('remote:show-log');
        }
      } catch (_err) {
        // ignore
      }
    },
  };
}

// ---------------------------------------------------------------------------
// Exports
// ---------------------------------------------------------------------------

module.exports = {
  surfaceError,
  registerErrorSurfaceHandlers,
  classify,
  CATEGORIES,
  DEFAULT_HINTS,
  // Legacy exports preserved from the v1 stub so existing imports
  // (if any) continue to resolve.
  HINTS,
};
