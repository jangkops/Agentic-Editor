'use strict';
/**
 * RemoteFileBridge — SFTP wrapper exposing the file ops that
 * `ipc-fs-handlers.js` consumes when a Remote_Session is active.
 *
 * Feature: remote-ssh
 * Covers Requirements: 6.1, 6.2, 6.4, 6.5, 11.6
 *
 * Phase 3 — Task 15.1 scope (THIS FILE):
 *   - constructor(session)
 *   - init()                            — opens the SFTP channel + probes path sep
 *   - list(remotePath)                  — directory listing
 *   - read(remotePath, encoding)        — full read, 16 MB cap → LargeFileError
 *   - readStream(remotePath)            — chunked read (no size cap)
 *   - stat(remotePath)                  — file metadata
 *   - mkdir(remotePath, {recursive})    — mkdir, optionally recursive
 *   - rename(oldPath, newPath)          — atomic rename
 *   - pathSep()                         — host-native path separator
 *   - close()                           — release the SFTP channel
 *
 * Tasks 15.2 (atomic write) and 15.3 (polling watcher) are NOT
 * implemented here. See the marker block at the bottom of the file.
 *
 * Why a thin class around `ssh2.SFTPWrapper`?
 *
 *  - The renderer's IPC contract (`ipc-fs-handlers.js`) is promise-based;
 *    `SFTPWrapper` is callback-based. A wrapper avoids leaking the
 *    callback style across the IPC boundary.
 *  - Path-sep handling must be host-native (Requirement 6.4 / 11.6).
 *    We probe `uname -s` once over the SSH client and cache the result
 *    so every consumer sees the same answer for the lifetime of the
 *    session.
 *  - The 16 MB read cap (design.md §SFTP file size limit) gates
 *    `read()` only. `readStream()` is exempt because the caller is
 *    explicitly opting into chunked I/O and we never buffer.
 *  - Errors are normalized to `{ code, message, cause? }` so the
 *    error-surface layer (Phase 6) can map them to remediation hints
 *    without sniffing native ssh2 error shapes.
 */

const { EventEmitter } = require('events');
const crypto = require('crypto');

const logger = require('./logger');

// ---------------------------------------------------------------------------
// Constants
// ---------------------------------------------------------------------------

/**
 * Maximum bytes `read()` will buffer into memory. Anything larger
 * surfaces as `LargeFileError` and forces the caller to use
 * `readStream()` (or, in renderer terms, the "Open large file" flow).
 *
 * Rationale: 16 MB is the v1 cap from design.md §SFTP file size limit.
 * Tunable via the constructor option `largeFileThreshold` for tests.
 */
const DEFAULT_LARGE_FILE_THRESHOLD = 16 * 1024 * 1024;

/**
 * Default chunk size for `readStream()`. Matches design.md (256 KB).
 * The ssh2 SFTP read stream honors `highWaterMark`, so this directly
 * controls the channel-level read window.
 */
const DEFAULT_READ_CHUNK_SIZE = 256 * 1024;

/** Path separator for Unix-family hosts. */
const SEP_UNIX = '/';

/** Path separator for Windows hosts (escaped backslash). */
const SEP_WINDOWS = '\\';

/**
 * `uname -s` outputs we treat as Unix (Requirement 6.4 / 11.6).
 * Anything not in this list, plus a positive Windows match, drops to
 * `'\\'`. Unknown / errored probes default to `'/'` because the v1
 * remote target set is overwhelmingly POSIX.
 */
const UNIX_UNAMES = Object.freeze(new Set([
  'Linux',
  'Darwin',
  'FreeBSD',
  'NetBSD',
  'OpenBSD',
  'DragonFly',
  'SunOS',
  'AIX',
]));

/**
 * Substrings in `uname -s` output that signal a Windows host
 * (Git Bash / MSYS / Cygwin / OpenSSH-for-Windows variants). When any
 * of these match the trimmed output, we set the separator to `'\\'`.
 */
const WINDOWS_UNAME_HINTS = Object.freeze([
  'MINGW',
  'MSYS',
  'CYGWIN',
  'Windows',
]);

/**
 * Error codes this module emits. Consumed by `error-surface.js` (Phase 6)
 * for remediation messages. Keep the list narrow — adding a code is a
 * cross-cutting change.
 */
const FILE_ERR = Object.freeze({
  NOT_INITIALIZED: 'sftp-not-initialized',
  CHANNEL_OPEN_FAILED: 'sftp-open-failed',
  ALREADY_CLOSED: 'sftp-closed',
  LARGE_FILE: 'large-file',
  IO: 'sftp-io',
  PERMISSION: 'sftp-permission',
  NOT_FOUND: 'sftp-not-found',
  EXISTS: 'sftp-exists',
  NOT_DIRECTORY: 'sftp-not-directory',
  DISK_FULL: 'disk-full',
  NOT_A_BUFFER: 'not-a-buffer',
});

// ---------------------------------------------------------------------------
// LargeFileError
// ---------------------------------------------------------------------------

/**
 * Thrown by `read()` when the target file's size exceeds the
 * configured threshold. Renderer treats this as a structured signal
 * to show the "large file" confirmation modal (design.md).
 *
 * Shape:
 *   {
 *     name: 'LargeFileError',
 *     code: 'large-file',
 *     size: <bytes>,
 *     threshold: <bytes>,
 *     path: <string>,
 *   }
 *
 * The `size` and `threshold` fields are always finite numbers; the
 * `path` field is the exact remotePath the caller passed in (so the
 * UI can echo it without re-resolving).
 */
class LargeFileError extends Error {
  /**
   * @param {{ size: number, threshold: number, path?: string }} info
   */
  constructor(info) {
    const size = Number(info && info.size) || 0;
    const threshold = Number(info && info.threshold) || 0;
    const remotePath = info && typeof info.path === 'string' ? info.path : '';
    super(
      'Remote file is ' + size + ' bytes (threshold ' + threshold + '); ' +
      'use readStream() or accept the large-file prompt.'
    );
    this.name = 'LargeFileError';
    this.code = FILE_ERR.LARGE_FILE;
    this.size = size;
    this.threshold = threshold;
    this.path = remotePath;
  }
}

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

/**
 * Normalize a raw `uname -s` string into the canonical separator.
 *
 *   'Linux\n'                 -> '/'
 *   'Darwin'                  -> '/'
 *   'MINGW64_NT-10.0-19045'   -> '\\'
 *   'CYGWIN_NT-10.0'          -> '\\'
 *   'Windows_NT'              -> '\\'
 *   ''                        -> '/' (default)
 *
 * Exported (via the module exports below) for unit-test use.
 *
 * @param {string|Buffer|null|undefined} raw
 * @returns {string}
 */
function separatorFromUname(raw) {
  const text = String(raw == null ? '' : raw).trim();
  if (text.length === 0) return SEP_UNIX;

  // Windows hint match (prefix or substring).
  for (const hint of WINDOWS_UNAME_HINTS) {
    if (text.startsWith(hint) || text.indexOf(hint) >= 0) {
      return SEP_WINDOWS;
    }
  }

  // First whitespace-separated token is what `uname -s` actually emits.
  const first = text.split(/\s+/, 1)[0];
  if (UNIX_UNAMES.has(first)) return SEP_UNIX;

  // Conservative default: POSIX. Requirement 6.4 mandates Windows uses
  // `\`, but we only flip on a positive signal so non-matching outputs
  // do not silently break path joins on the (much more common) POSIX
  // case.
  return SEP_UNIX;
}

/**
 * Map an ssh2 / SFTP error to one of our `FILE_ERR` codes.
 *
 * ssh2 surfaces native `Error` objects with a `code` (numeric SFTP
 * status) on data errors. The numeric values come from
 * `ssh2.utils.sftp.STATUS_CODE`:
 *   2  -> No such file
 *   3  -> Permission denied
 *   4  -> Failure (catch-all)
 *  11  -> File already exists
 *
 * @param {Error|null|undefined} err
 * @returns {string}
 */
function classifySftpError(err) {
  if (!err) return FILE_ERR.IO;
  const code = err.code;
  if (code === 2) return FILE_ERR.NOT_FOUND;
  if (code === 3) return FILE_ERR.PERMISSION;
  if (code === 11) return FILE_ERR.EXISTS;
  if (typeof code === 'string') {
    if (code === 'ENOENT') return FILE_ERR.NOT_FOUND;
    if (code === 'EACCES' || code === 'EPERM') return FILE_ERR.PERMISSION;
    if (code === 'EEXIST') return FILE_ERR.EXISTS;
    if (code === 'ENOTDIR') return FILE_ERR.NOT_DIRECTORY;
    if (code === 'ENOSPC' || code === 'EDQUOT') return FILE_ERR.DISK_FULL;
  }
  // SFTP code 4 (SSH_FX_FAILURE) is a catch-all; sniff the message for the
  // disk-full / quota signals OpenSSH typically embeds in the failure text.
  // Requirement 6.6: surface disk-full distinctly from generic IO.
  const msg = err && err.message ? String(err.message).toLowerCase() : '';
  if (msg.indexOf('enospc') >= 0
    || msg.indexOf('no space') >= 0
    || msg.indexOf('disk full') >= 0
    || msg.indexOf('quota') >= 0) {
    return FILE_ERR.DISK_FULL;
  }
  return FILE_ERR.IO;
}

/**
 * Wrap an ssh2 callback-shaped operation into a Promise that rejects
 * with a normalized error envelope. Using a single helper keeps the
 * per-method bodies readable.
 *
 * @template T
 * @param {(cb: (err: Error|null, value?: T) => void) => void} fn
 * @param {string} op
 * @param {string=} pathLabel
 * @returns {Promise<T>}
 */
function callbackToPromise(fn, op, pathLabel) {
  return new Promise((resolve, reject) => {
    let settled = false;
    try {
      fn((err, value) => {
        if (settled) return;
        settled = true;
        if (err) {
          const wrapped = Object.assign(
            new Error(op + ' failed' + (pathLabel ? ' (' + pathLabel + ')' : '') + ': ' + (err.message || err)),
            {
              code: classifySftpError(err),
              op,
              path: pathLabel || undefined,
              cause: { name: err.name, message: err.message, code: err.code },
            }
          );
          reject(wrapped);
        } else {
          resolve(value);
        }
      });
    } catch (err) {
      if (settled) return;
      settled = true;
      reject(Object.assign(
        new Error(op + ' threw' + (pathLabel ? ' (' + pathLabel + ')' : '') + ': ' + (err && err.message ? err.message : err)),
        { code: FILE_ERR.IO, op, path: pathLabel || undefined, cause: err }
      ));
    }
  });
}

/**
 * Convert an SFTP `attrs` record (returned by `stat`/`readdir`) into a
 * plain object the renderer can consume. Both inputs are accepted so
 * `list()` can pass per-entry attrs and `stat()` can pass top-level.
 *
 * @param {Object} attrs ssh2 SFTP attrs.
 * @returns {{
 *   size: number, mode: number, uid: number, gid: number,
 *   atime: number, mtime: number,
 *   isDirectory: boolean, isFile: boolean, isSymbolicLink: boolean,
 * }}
 */
function normalizeAttrs(attrs) {
  const a = attrs || {};
  const mode = Number(a.mode) || 0;
  // POSIX file-type bits. ssh2 sets `mode` from the SSH_FXP_ATTRS frame.
  const S_IFMT = 0o170000;
  const S_IFDIR = 0o040000;
  const S_IFREG = 0o100000;
  const S_IFLNK = 0o120000;
  const masked = mode & S_IFMT;
  return {
    size: Number(a.size) || 0,
    mode,
    uid: Number(a.uid) || 0,
    gid: Number(a.gid) || 0,
    // ssh2 emits seconds-since-epoch; renderers (and `fs.Stats`) use ms.
    atime: Number(a.atime) > 0 ? Number(a.atime) * 1000 : 0,
    mtime: Number(a.mtime) > 0 ? Number(a.mtime) * 1000 : 0,
    isDirectory: masked === S_IFDIR,
    isFile: masked === S_IFREG,
    isSymbolicLink: masked === S_IFLNK,
  };
}

/**
 * Run a single `client.exec(cmd)` over the underlying ssh2 client and
 * return the collected stdout (UTF-8). Used solely to probe `uname -s`
 * for `pathSep()`. Errors short-circuit to an empty string so the
 * caller can fall through to the safe POSIX default.
 *
 * @param {Object} client ssh2 Client.
 * @param {string} cmd
 * @param {number} timeoutMs
 * @returns {Promise<string>}
 */
function execCapture(client, cmd, timeoutMs) {
  return new Promise((resolve) => {
    let resolved = false;
    const finish = (text) => {
      if (resolved) return;
      resolved = true;
      resolve(typeof text === 'string' ? text : '');
    };
    const timer = setTimeout(() => finish(''), Math.max(500, Number(timeoutMs) || 3000));
    try {
      client.exec(cmd, (err, stream) => {
        if (err || !stream) {
          clearTimeout(timer);
          finish('');
          return;
        }
        const chunks = [];
        stream.on('data', (chunk) => {
          if (Buffer.isBuffer(chunk)) chunks.push(chunk);
          else chunks.push(Buffer.from(String(chunk)));
        });
        // stderr is ignored — `uname -s` should not emit any.
        if (stream.stderr && typeof stream.stderr.on === 'function') {
          stream.stderr.on('data', () => undefined);
        }
        stream.on('close', () => {
          clearTimeout(timer);
          finish(Buffer.concat(chunks).toString('utf8'));
        });
        stream.on('error', () => {
          clearTimeout(timer);
          finish('');
        });
      });
    } catch (_err) {
      clearTimeout(timer);
      finish('');
    }
  });
}

// ---------------------------------------------------------------------------
// RemoteFileBridge
// ---------------------------------------------------------------------------

/**
 * Generate `n` random bytes encoded as lowercase hex. Used by
 * `RemoteFileBridge.write()` to suffix the staging temp path so
 * concurrent writers from the same client never collide.
 *
 * @param {number} n
 * @returns {string}
 */
function randHex(n) {
  const len = Number.isInteger(n) && n > 0 ? n : 6;
  return crypto.randomBytes(len).toString('hex');
}

class RemoteFileBridge extends EventEmitter {
  /**
   * @param {Object} session A `RemoteSession`-shaped object that
   *   exposes `.client` (ssh2 Client) and `.alias`. The session must
   *   be in state `connected` (or at least have a live client) before
   *   `init()` is called.
   * @param {Object} [opts]
   * @param {number}  [opts.largeFileThreshold] Override the 16 MB cap.
   * @param {number}  [opts.readChunkSize]      Override the 256 KB
   *                                            stream chunk size.
   * @param {string}  [opts.pathSeparator]      Force-set the separator,
   *                                            skipping the `uname -s`
   *                                            probe. Used by tests
   *                                            and by the manager
   *                                            after a reconnect when
   *                                            the value is already
   *                                            known.
   * @param {number}  [opts.unameTimeoutMs]     Probe timeout (default 3000).
   */
  constructor(session, opts) {
    super();
    if (!session || typeof session !== 'object') {
      throw new TypeError('RemoteFileBridge: session is required');
    }
    const options = opts || {};

    /** @private */ this._session = session;
    /** @private */ this._largeFileThreshold = Number.isInteger(options.largeFileThreshold)
      && options.largeFileThreshold > 0
      ? options.largeFileThreshold
      : DEFAULT_LARGE_FILE_THRESHOLD;
    /** @private */ this._readChunkSize = Number.isInteger(options.readChunkSize)
      && options.readChunkSize > 0
      ? options.readChunkSize
      : DEFAULT_READ_CHUNK_SIZE;
    /** @private */ this._unameTimeoutMs = Number.isInteger(options.unameTimeoutMs)
      && options.unameTimeoutMs > 0
      ? options.unameTimeoutMs
      : 3000;

    /** @private @type {string|null} */ this._pathSep = typeof options.pathSeparator === 'string'
      && options.pathSeparator.length > 0
      ? options.pathSeparator
      : null;

    /** @private @type {Object|null} */ this._sftp = null;
    /** @private @type {Promise<void>|null} */ this._initPromise = null;
    /** @private @type {boolean} */ this._closed = false;
  }

  /**
   * Alias of the bound session — propagated into log lines.
   * @returns {string|null}
   */
  get alias() {
    return this._session && typeof this._session.alias === 'string'
      ? this._session.alias
      : null;
  }

  /** Maximum read size in bytes. */
  get largeFileThreshold() { return this._largeFileThreshold; }

  /**
   * Idempotently open the SFTP channel and probe `uname -s`. Safe to
   * call multiple times — concurrent callers share the same in-flight
   * promise.
   *
   * @returns {Promise<void>}
   */
  init() {
    if (this._closed) {
      return Promise.reject(Object.assign(
        new Error('RemoteFileBridge is closed'),
        { code: FILE_ERR.ALREADY_CLOSED }
      ));
    }
    if (this._sftp) return Promise.resolve();
    if (this._initPromise) return this._initPromise;

    const client = this._session.client;
    if (!client || typeof client.sftp !== 'function') {
      return Promise.reject(Object.assign(
        new Error('Session has no live ssh2 client; connect first.'),
        { code: FILE_ERR.NOT_INITIALIZED }
      ));
    }

    this._initPromise = new Promise((resolve, reject) => {
      client.sftp((err, sftp) => {
        if (err || !sftp) {
          this._initPromise = null;
          reject(Object.assign(
            new Error('SFTP channel open failed: ' + (err && err.message ? err.message : 'no channel')),
            { code: FILE_ERR.CHANNEL_OPEN_FAILED, cause: err || undefined }
          ));
          return;
        }

        sftp.on('error', (sftpErr) => {
          try {
            logger.warn('remote-file-bridge-sftp-error', {
              alias: this.alias,
              message: sftpErr && sftpErr.message ? sftpErr.message : String(sftpErr),
            });
          } catch (_e) { /* ignore */ }
        });

        sftp.on('close', () => {
          // The session's underlying client is still alive in some
          // disconnect paths; drop our channel handle so a future
          // `init()` reopens cleanly.
          this._sftp = null;
        });

        this._sftp = sftp;

        // Probe path sep only if not pre-set. We do this before
        // resolving so the very first list/stat call sees a stable
        // separator.
        const probeIfNeeded = this._pathSep
          ? Promise.resolve()
          : execCapture(client, 'uname -s', this._unameTimeoutMs)
            .then((raw) => { this._pathSep = separatorFromUname(raw); });

        probeIfNeeded
          .then(() => {
            try {
              logger.info('remote-file-bridge-init', {
                alias: this.alias,
                pathSeparator: this._pathSep,
                largeFileThreshold: this._largeFileThreshold,
              });
            } catch (_e) { /* ignore */ }
            resolve();
          })
          .catch((probeErr) => {
            // Probe failure is non-fatal; default to POSIX.
            this._pathSep = SEP_UNIX;
            try {
              logger.warn('remote-file-bridge-uname-probe-failed', {
                alias: this.alias,
                message: probeErr && probeErr.message ? probeErr.message : String(probeErr),
              });
            } catch (_e) { /* ignore */ }
            resolve();
          });
      });
    }).finally(() => { this._initPromise = null; });

    return this._initPromise;
  }

  /**
   * Path separator for the remote host. Returns the cached probe
   * result; if `init()` has not been awaited yet, returns `'/'` as
   * the conservative default.
   *
   * @returns {string}
   */
  pathSep() {
    return this._pathSep || SEP_UNIX;
  }

  /**
   * Ensure the SFTP channel is open before any data op. Throws a
   * structured error rather than auto-init'ing because the caller
   * should have explicit control over when the channel opens.
   *
   * @private
   * @returns {Object} ssh2 SFTPWrapper
   */
  _requireSftp() {
    if (this._closed) {
      throw Object.assign(
        new Error('RemoteFileBridge is closed'),
        { code: FILE_ERR.ALREADY_CLOSED }
      );
    }
    if (!this._sftp) {
      throw Object.assign(
        new Error('SFTP channel not initialized; call init() first'),
        { code: FILE_ERR.NOT_INITIALIZED }
      );
    }
    return this._sftp;
  }

  // -------------------------------------------------------------------------
  // list (Requirement 6.1)
  // -------------------------------------------------------------------------

  /**
   * Read a remote directory.
   *
   * Returns one entry per child, each shaped:
   *   {
   *     name: <basename>,
   *     path: <basePath joined with pathSep()>,
   *     isDirectory, isFile, isSymbolicLink,
   *     size, mode, uid, gid, atime, mtime
   *   }
   *
   * The `.` and `..` synthetic entries that some servers emit are
   * filtered out — IDE callers never need them and they trip up
   * recursive walkers.
   *
   * @param {string} remotePath
   * @returns {Promise<Array<Object>>}
   */
  async list(remotePath) {
    if (typeof remotePath !== 'string' || remotePath.length === 0) {
      throw Object.assign(new TypeError('list(remotePath) requires a non-empty string'),
        { code: FILE_ERR.IO });
    }
    const sftp = this._requireSftp();
    const entries = await callbackToPromise(
      (cb) => sftp.readdir(remotePath, cb),
      'readdir',
      remotePath
    );
    const sep = this.pathSep();
    const base = remotePath.endsWith(sep) ? remotePath.slice(0, -1) : remotePath;
    const out = [];
    for (const entry of (Array.isArray(entries) ? entries : [])) {
      const name = String(entry && entry.filename || '');
      if (name === '' || name === '.' || name === '..') continue;
      const attrs = normalizeAttrs(entry && entry.attrs);
      out.push(Object.assign({
        name,
        path: base + sep + name,
      }, attrs));
    }
    return out;
  }

  // -------------------------------------------------------------------------
  // read (Requirement 6.2, 6.5)
  // -------------------------------------------------------------------------

  /**
   * Read a remote file fully into memory.
   *
   *  - If the file's `stat().size` exceeds `largeFileThreshold`, throws
   *    `LargeFileError` BEFORE opening the file. The renderer surfaces
   *    this as the "large file" modal.
   *  - Returns a `Buffer` when `encoding` is null/undefined/'binary',
   *    a `string` otherwise.
   *
   * @param {string} remotePath
   * @param {string|null} [encoding] e.g. 'utf8'.
   * @returns {Promise<string|Buffer>}
   */
  async read(remotePath, encoding) {
    if (typeof remotePath !== 'string' || remotePath.length === 0) {
      throw Object.assign(new TypeError('read(remotePath) requires a non-empty string'),
        { code: FILE_ERR.IO });
    }
    const sftp = this._requireSftp();

    // Pre-flight stat so we can short-circuit on size BEFORE opening
    // the file handle. Per design.md, opening a 4 GB log file would
    // otherwise allocate a buffer big enough to OOM the main process.
    const attrs = await callbackToPromise(
      (cb) => sftp.stat(remotePath, cb),
      'stat',
      remotePath
    );
    const size = Number(attrs && attrs.size) || 0;
    if (size > this._largeFileThreshold) {
      throw new LargeFileError({
        size,
        threshold: this._largeFileThreshold,
        path: remotePath,
      });
    }

    // Use `readFile` which under the hood opens, reads the full size,
    // and closes — exactly what we need for the small-file path.
    const buf = await callbackToPromise(
      (cb) => sftp.readFile(remotePath, cb),
      'readFile',
      remotePath
    );
    if (!Buffer.isBuffer(buf)) return Buffer.from([]);
    if (encoding && encoding !== 'binary') return buf.toString(encoding);
    return buf;
  }

  // -------------------------------------------------------------------------
  // readStream (Requirement 6.2)
  // -------------------------------------------------------------------------

  /**
   * Open a remote file as a Readable stream. No size cap — the caller
   * is opting into chunked I/O and we never buffer the whole file.
   *
   * The returned stream is the raw ssh2 SFTP read stream so consumers
   * can pipe it directly. Errors are emitted on the stream itself (no
   * Promise wrapper) so backpressure-aware code paths work as
   * expected.
   *
   * @param {string} remotePath
   * @returns {NodeJS.ReadableStream}
   */
  readStream(remotePath) {
    if (typeof remotePath !== 'string' || remotePath.length === 0) {
      throw Object.assign(new TypeError('readStream(remotePath) requires a non-empty string'),
        { code: FILE_ERR.IO });
    }
    const sftp = this._requireSftp();
    // ssh2 maps `highWaterMark` -> SSH_FXP_READ length, so this cleanly
    // controls per-chunk size on the wire.
    return sftp.createReadStream(remotePath, {
      highWaterMark: this._readChunkSize,
      autoClose: true,
    });
  }

  // -------------------------------------------------------------------------
  // stat (Requirement 6.1, 6.5)
  // -------------------------------------------------------------------------

  /**
   * Stat a remote file or directory.
   *
   * Symlinks are followed (matches `fs.stat`, not `fs.lstat`). If you
   * need lstat semantics, add it explicitly later — there is no v1
   * caller that needs it.
   *
   * @param {string} remotePath
   * @returns {Promise<Object>}  See `normalizeAttrs` for shape.
   */
  async stat(remotePath) {
    if (typeof remotePath !== 'string' || remotePath.length === 0) {
      throw Object.assign(new TypeError('stat(remotePath) requires a non-empty string'),
        { code: FILE_ERR.IO });
    }
    const sftp = this._requireSftp();
    const attrs = await callbackToPromise(
      (cb) => sftp.stat(remotePath, cb),
      'stat',
      remotePath
    );
    return Object.assign({ path: remotePath }, normalizeAttrs(attrs));
  }

  // -------------------------------------------------------------------------
  // mkdir (Requirement 6.1)
  // -------------------------------------------------------------------------

  /**
   * Create a remote directory.
   *
   * `recursive: true` walks the path components and creates each
   * missing one. Existing components (EEXIST / SFTP code 11) are
   * tolerated only when they are themselves directories — a path
   * collision with a regular file surfaces as `NOT_DIRECTORY`.
   *
   * @param {string} remotePath
   * @param {{recursive?: boolean}} [opts]
   * @returns {Promise<void>}
   */
  async mkdir(remotePath, opts) {
    if (typeof remotePath !== 'string' || remotePath.length === 0) {
      throw Object.assign(new TypeError('mkdir(remotePath) requires a non-empty string'),
        { code: FILE_ERR.IO });
    }
    const sftp = this._requireSftp();
    const recursive = !!(opts && opts.recursive);

    if (!recursive) {
      await callbackToPromise(
        (cb) => sftp.mkdir(remotePath, cb),
        'mkdir',
        remotePath
      );
      return;
    }

    // Recursive: walk components, mkdir each. Path segmentation uses
    // the live separator so Windows hosts get `\\` semantics. We do
    // NOT collapse separators or normalize drive letters here — that
    // is the caller's responsibility (path-normalization.js handles
    // the SSH_Config side).
    const sep = this.pathSep();
    const parts = remotePath.split(sep);
    let acc = '';
    for (let i = 0; i < parts.length; i += 1) {
      const part = parts[i];
      if (i === 0) {
        // Preserve the leading anchor: empty string for absolute POSIX
        // paths, drive letter for Windows.
        acc = part;
        if (acc === '' || /^[A-Za-z]:$/.test(acc)) continue;
      } else {
        acc = acc + sep + part;
      }
      if (acc.length === 0) continue;

      // Try mkdir; tolerate "already exists" iff the existing entry is
      // a directory, otherwise re-throw as NOT_DIRECTORY.
      try {
        // eslint-disable-next-line no-await-in-loop
        await callbackToPromise(
          (cb) => sftp.mkdir(acc, cb),
          'mkdir',
          acc
        );
      } catch (err) {
        if (err && err.code === FILE_ERR.EXISTS) {
          // Confirm it's a directory; if not, surface a clearer error.
          let attrs;
          try {
            // eslint-disable-next-line no-await-in-loop
            attrs = await callbackToPromise(
              (cb) => sftp.stat(acc, cb),
              'stat',
              acc
            );
          } catch (_statErr) {
            throw err; // original EEXIST
          }
          if (!normalizeAttrs(attrs).isDirectory) {
            throw Object.assign(
              new Error('mkdir -p: ' + acc + ' exists and is not a directory'),
              { code: FILE_ERR.NOT_DIRECTORY, op: 'mkdir', path: acc }
            );
          }
          continue;
        }
        throw err;
      }
    }
  }

  // -------------------------------------------------------------------------
  // rename (Requirement 6.1)
  // -------------------------------------------------------------------------

  /**
   * Rename / move a remote path. Maps to SSH_FXP_RENAME, which is
   * atomic on every server we target (OpenSSH on Linux/macOS/Windows).
   *
   * Note: SFTP `rename` semantics on most servers REQUIRE that the
   * destination does NOT exist. Atomic-overwrite via the POSIX-rename
   * extension is the job of Task 15.2 (`write()`); for the v1 ops
   * surface we surface SFTP's native behavior unchanged.
   *
   * @param {string} oldPath
   * @param {string} newPath
   * @returns {Promise<void>}
   */
  async rename(oldPath, newPath) {
    if (typeof oldPath !== 'string' || oldPath.length === 0
        || typeof newPath !== 'string' || newPath.length === 0) {
      throw Object.assign(
        new TypeError('rename(oldPath, newPath) requires non-empty strings'),
        { code: FILE_ERR.IO }
      );
    }
    const sftp = this._requireSftp();
    await callbackToPromise(
      (cb) => sftp.rename(oldPath, newPath, cb),
      'rename',
      oldPath + ' -> ' + newPath
    );
  }

  // -------------------------------------------------------------------------
  // write — atomic temp -> fsync -> rename (Task 15.2)
  // Requirement 6.3 (atomic), 6.5 (error envelope), 6.6 (disk-full code)
  // -------------------------------------------------------------------------

  /**
   * Atomically write `content` to `remotePath`.
   *
   * The on-wire sequence is:
   *
   *   1. Generate `${remotePath}.ae-tmp-${randHex(6)}` as the staging path.
   *   2. Open it with `O_WRONLY|O_CREAT|O_TRUNC`, mode = preserved-from-target
   *      (or `0o644` if no prior file exists).
   *   3. Write `content` in full. Caller may pass a Buffer or a string;
   *      strings are encoded with `opts.encoding` (default `utf8`).
   *   4. fsync the handle via the OpenSSH `fsync@openssh.com` SFTP
   *      extension. Servers that do not advertise the extension are
   *      skipped silently (debug log only) — the temp + rename pattern
   *      still gives crash-consistent updates on every server we target.
   *   5. Close the handle.
   *   6. `rename(temp -> remotePath)` — atomic on every supported server.
   *
   * On any error, the temp file is unlinked best-effort BEFORE the error
   * is re-thrown. The thrown error always carries `{code, op, path, cause}`
   * with `code` ∈ {`permission`, `disk-full`, `io`, `sftp-not-initialized`,
   * `not-a-buffer`}, matching the renderer-facing contract in design.md.
   *
   * @param {string} remotePath
   * @param {Buffer|string} content
   * @param {{encoding?: string, mode?: number}} [opts]
   * @returns {Promise<{path: string, size: number, fsynced: boolean}>}
   */
  async write(remotePath, content, opts) {
    if (typeof remotePath !== 'string' || remotePath.length === 0) {
      throw Object.assign(
        new TypeError('write(remotePath) requires a non-empty string'),
        { code: FILE_ERR.IO, op: 'write' }
      );
    }
    const options = opts || {};
    // Coerce string -> Buffer so the wire write is single-encoding.
    let body;
    if (Buffer.isBuffer(content)) {
      body = content;
    } else if (typeof content === 'string') {
      const enc = typeof options.encoding === 'string' && options.encoding.length > 0
        ? options.encoding
        : 'utf8';
      body = Buffer.from(content, enc);
    } else {
      throw Object.assign(
        new TypeError('write(content) must be a Buffer or string'),
        { code: FILE_ERR.NOT_A_BUFFER, op: 'write', path: remotePath }
      );
    }

    const sftp = this._requireSftp();

    // 1. Resolve the desired final mode. If the target already exists,
    //    preserve its permission bits so editors do not silently widen
    //    a 0600 secrets file to 0644. If stat fails for any non-NOTFOUND
    //    reason we surface the error instead of guessing.
    let finalMode = Number.isInteger(options.mode) && options.mode > 0
      ? options.mode & 0o7777
      : 0o644;
    let targetExisted = false;
    try {
      const attrs = await callbackToPromise(
        (cb) => sftp.stat(remotePath, cb),
        'stat',
        remotePath
      );
      targetExisted = true;
      const existingMode = Number(attrs && attrs.mode) || 0;
      if (existingMode !== 0 && !Number.isInteger(options.mode)) {
        // Strip the file-type bits, keep permission bits.
        finalMode = existingMode & 0o7777;
      }
    } catch (statErr) {
      if (!statErr || statErr.code !== FILE_ERR.NOT_FOUND) {
        // Any non-NOTFOUND stat error (e.g. permission on the parent dir)
        // is fatal — bail before writing a temp file we cannot rename.
        throw statErr;
      }
    }

    // 2. Stage to a sibling temp path so failures never leave a
    //    half-written `remotePath`. The randomness defends against
    //    concurrent writers from the same client.
    const tempPath = remotePath + '.ae-tmp-' + randHex(6);

    let handle = null;
    let handleClosed = false;
    try {
      handle = await callbackToPromise(
        (cb) => sftp.open(
          tempPath,
          // O_WRONLY|O_CREAT|O_TRUNC — fail if creation is denied,
          // start at offset 0, no append.
          'w',
          finalMode,
          cb
        ),
        'open',
        tempPath
      );

      // 3. Single full-buffer write. ssh2 transparently chunks the
      //    payload into SSH_FXP_WRITE frames sized to the channel.
      if (body.length > 0) {
        await callbackToPromise(
          (cb) => sftp.write(handle, body, 0, body.length, 0, cb),
          'write',
          tempPath
        );
      }

      // 4. fsync via OpenSSH extension. Best-effort: if the server
      //    does not implement the extension we degrade to "no fsync"
      //    rather than failing the write — the temp+rename pattern
      //    still gives the renderer a crash-consistent end state.
      const fsynced = await this._fsync(sftp, handle, tempPath);

      // 5. Close the handle BEFORE rename so the server flushes its
      //    own buffers and releases any open-file lock.
      await callbackToPromise(
        (cb) => sftp.close(handle, cb),
        'close',
        tempPath
      );
      handleClosed = true;
      handle = null;

      // 6. Atomic rename. SSH_FXP_RENAME on most servers requires the
      //    destination to NOT exist — but for an existing target we want
      //    overwrite semantics. Try the POSIX-rename extension first
      //    (`posix-rename@openssh.com`); if unsupported, fall back to
      //    unlink+rename (still atomic from the renderer's POV because
      //    we only surface success after the rename completes).
      await this._renameOverwrite(sftp, tempPath, remotePath, targetExisted);

      try {
        logger.info('remote-file-bridge-write', {
          alias: this.alias,
          path: remotePath,
          size: body.length,
          fsynced,
          mode: finalMode,
        });
      } catch (_e) { /* ignore */ }

      return { path: remotePath, size: body.length, fsynced };
    } catch (err) {
      // Cleanup order: close any still-open handle (ignore errors),
      // then unlink the temp file. We swallow cleanup errors so the
      // ORIGINAL failure is what the caller sees.
      if (handle && !handleClosed) {
        try {
          await callbackToPromise(
            (cb) => sftp.close(handle, cb),
            'close',
            tempPath
          );
        } catch (_closeErr) { /* best-effort */ }
      }
      await this._unlinkBestEffort(sftp, tempPath);
      throw err;
    }
  }

  /**
   * Best-effort `sftp.unlink`. Used by `write()` cleanup paths and any
   * future caller that wants to drop a temp file without surfacing the
   * error (e.g., the temp may already be gone if rename succeeded).
   *
   * @private
   * @param {Object} sftp
   * @param {string} p
   * @returns {Promise<void>}
   */
  async _unlinkBestEffort(sftp, p) {
    if (!sftp || typeof sftp.unlink !== 'function' || !p) return;
    try {
      await new Promise((resolve) => {
        try {
          sftp.unlink(p, () => resolve());
        } catch (_e) {
          resolve();
        }
      });
    } catch (_e) { /* swallow */ }
  }

  /**
   * Issue an `fsync@openssh.com` SFTP extension request on the given
   * handle. Returns `true` if the server acknowledged, `false` if the
   * extension is unsupported or the call errored.
   *
   * Detection: ssh2 exposes the extension as either
   * `sftp.ext_openssh_fsync(handle, cb)` (newer builds) or by allowing
   * `sftp.fsync(handle, cb)` to fall through to the ext call. We probe
   * `ext_openssh_fsync` first; if absent we try `fsync`. Either path
   * that errors out is treated as "unsupported" — we never block a
   * write on missing fsync support (Requirement 6.5: best-effort).
   *
   * @private
   * @param {Object} sftp
   * @param {Object} handle
   * @param {string} pathLabel
   * @returns {Promise<boolean>}
   */
  async _fsync(sftp, handle, pathLabel) {
    const tryCall = (fnName) => new Promise((resolve) => {
      const fn = sftp && typeof sftp[fnName] === 'function' ? sftp[fnName] : null;
      if (!fn) {
        resolve({ ok: false, missing: true });
        return;
      }
      let settled = false;
      try {
        fn.call(sftp, handle, (err) => {
          if (settled) return;
          settled = true;
          resolve({ ok: !err, missing: false, err: err || null });
        });
      } catch (e) {
        if (settled) return;
        settled = true;
        resolve({ ok: false, missing: false, err: e });
      }
    });

    let result = await tryCall('ext_openssh_fsync');
    if (result.missing) {
      // ssh2 < 1.x exposes plain `fsync`. Try it as a secondary path.
      result = await tryCall('fsync');
    }
    if (!result.ok) {
      try {
        logger.debug('remote-file-bridge-fsync-skipped', {
          alias: this.alias,
          path: pathLabel,
          reason: result.missing ? 'extension-unsupported' : (result.err && result.err.message) || 'unknown',
        });
      } catch (_e) { /* ignore */ }
      return false;
    }
    return true;
  }

  /**
   * Rename `temp` -> `final`, with overwrite semantics on servers that
   * either implement the POSIX-rename SFTP extension or accept the v3
   * SSH_FXP_RENAME without an existing target.
   *
   * Strategy:
   *   1. If `targetExisted` is false, just call rename — fast path.
   *   2. Otherwise try `posix-rename@openssh.com`, which atomically
   *      overwrites.
   *   3. If the extension is unsupported, fall back to unlink(final)
   *      followed by rename(temp -> final). This is NOT atomic on the
   *      server side, but is the documented degradation for non-OpenSSH
   *      servers and matches what other SFTP clients do.
   *
   * @private
   * @param {Object} sftp
   * @param {string} tempPath
   * @param {string} finalPath
   * @param {boolean} targetExisted
   * @returns {Promise<void>}
   */
  async _renameOverwrite(sftp, tempPath, finalPath, targetExisted) {
    if (!targetExisted) {
      await callbackToPromise(
        (cb) => sftp.rename(tempPath, finalPath, cb),
        'rename',
        tempPath + ' -> ' + finalPath
      );
      return;
    }

    // Try the OpenSSH POSIX-rename extension if available.
    if (typeof sftp.ext_openssh_rename === 'function') {
      try {
        await callbackToPromise(
          (cb) => sftp.ext_openssh_rename(tempPath, finalPath, cb),
          'rename',
          tempPath + ' -> ' + finalPath
        );
        return;
      } catch (extErr) {
        // Fall through to unlink+rename below.
        try {
          logger.debug('remote-file-bridge-posix-rename-fallback', {
            alias: this.alias,
            path: finalPath,
            reason: (extErr && extErr.message) || 'unknown',
          });
        } catch (_e) { /* ignore */ }
      }
    }

    // Fallback: unlink then rename. Best we can do on plain SFTP v3.
    try {
      await callbackToPromise(
        (cb) => sftp.unlink(finalPath, cb),
        'unlink',
        finalPath
      );
    } catch (unlinkErr) {
      // If unlink fails because the file vanished between our stat and
      // the rename (race with another writer), continue — rename will
      // succeed on a non-existent target.
      if (!unlinkErr || unlinkErr.code !== FILE_ERR.NOT_FOUND) {
        throw unlinkErr;
      }
    }
    await callbackToPromise(
      (cb) => sftp.rename(tempPath, finalPath, cb),
      'rename',
      tempPath + ' -> ' + finalPath
    );
  }

  // -------------------------------------------------------------------------
  // close
  // -------------------------------------------------------------------------

  /**
   * Tear down the SFTP channel. Idempotent. Does NOT close the
   * underlying ssh2 client — the session manager owns that lifetime.
   *
   * @returns {Promise<void>}
   */
  async close() {
    if (this._closed && !this._sftp) return;
    this._closed = true;
    const sftp = this._sftp;
    this._sftp = null;
    if (!sftp || typeof sftp.end !== 'function') return;
    try { sftp.end(); } catch (_e) { /* ignore */ }
    try {
      logger.info('remote-file-bridge-closed', { alias: this.alias });
    } catch (_e) { /* ignore */ }
  }
}

// ---------------------------------------------------------------------------
// Phase 15.3 — extension point (DO NOT IMPLEMENT HERE)
// ---------------------------------------------------------------------------
// The next sub-tasks bolt onto this class without changing the surface
// area introduced above:
//
//   15.3 polling watcher:
//        startWatch(remotePath, {activeMs=500, idleMs=2000})
//        stopWatch(remotePath)
//        - per-watch interval timer
//        - snapshot map: filename -> {mtime, size}
//        - emit('fs-change', {path, type:'created'|'modified'|'deleted'})
//
// Both extensions reuse `_requireSftp()` and `pathSep()` from this file.
// ---------------------------------------------------------------------------

module.exports = {
  RemoteFileBridge,
  LargeFileError,
  FILE_ERR,
  DEFAULT_LARGE_FILE_THRESHOLD,
  DEFAULT_READ_CHUNK_SIZE,
  // Helpers exported for unit tests; not part of the renderer-facing API.
  separatorFromUname,
  classifySftpError,
  normalizeAttrs,
  randHex,
};
