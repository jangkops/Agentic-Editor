'use strict';
/**
 * RemoteTerminalBridge — ssh2 shell/PTY channels.
 *
 * Feature: remote-ssh
 * Covers Requirements: 7.1, 7.2, 7.3, 7.4, 8.5, 11.4, 11.6
 *
 * Runs on the Electron main side. One bridge instance per RemoteSession;
 * owns a set of live xterm-compatible shell streams keyed by caller-chosen
 * `id` (mirrors the id space used by the local terminal handler so IPC
 * payloads stay interchangeable).
 *
 * Responsibilities
 * ----------------
 *  - Task 16.1: create/write/resize/kill ops on top of `ssh2.Client.shell()`.
 *  - Task 16.2: surface session disconnects to the renderer via a
 *    `'disconnected'` event (xterm keeps its scrollback locally) and
 *    expose `reattach(id, newSession)` that opens a fresh shell and
 *    writes a visible "-- Reconnected: new shell session --" banner.
 *  - Task 16.3: stdout back-pressure. ssh2 delivers `'data'` buffers at
 *    whatever rate the peer feeds them; emitting each chunk straight
 *    through the IPC bridge starves the renderer when the remote prints
 *    large blocks (e.g. `cat big.log`). We coalesce arrivals, split into
 *    8 KiB slices, and pace emission at >= 1 ms per slice so the event
 *    loop can interleave input, resize and IPC responses.
 *
 * Event contract (all on `this`, an EventEmitter):
 *
 *   'data'         {id, data (utf8 string)}          PTY output
 *   'exit'         {id, code, signal}                stream closed
 *   'disconnected' {id, reason}                      session lost; renderer
 *                                                     should keep scrollback
 *                                                     and show "reattach"
 *   'error'        {id, message}                     non-fatal stream error
 *
 * Non-goals (explicitly deferred)
 * --------------------------------
 *  - True PTY re-attachment (the remote process tree is gone when SSH
 *    drops, unless wrapped in tmux/screen). v1 reattach opens a *new*
 *    shell on the new session and prints a banner so the user can tell
 *    the difference.
 *  - Full Windows PowerShell coverage. We detect the remote OS, but the
 *    default-shell selection only informs the optional `exec <shell>`
 *    switch the caller may request — the PTY itself is always the
 *    user's login shell (what `ssh2.shell()` gives us).
 */

const { EventEmitter } = require('events');
const { StringDecoder } = require('string_decoder');

const logger = require('./logger');

// ---------------------------------------------------------------------------
// Tunables (Task 16.3)
// ---------------------------------------------------------------------------

/** Max bytes emitted per `data` event. 8 KiB matches a typical xterm
 *  parse-budget window; larger emits stall the renderer. */
const CHUNK_BYTES = 8 * 1024;

/** Minimum gap between successive chunk emissions for a single id.
 *  Uses setTimeout(..., 1) because setImmediate resolves in the same
 *  tick as incoming IO and would not yield long enough. */
const THROTTLE_MS = 1;

/** Banner printed at the top of a reattached shell (v1 Task 16.2). */
const REATTACH_NOTICE = '\r\n-- Reconnected: new shell session --\r\n';

// ---------------------------------------------------------------------------
// Remote OS detection helper
// ---------------------------------------------------------------------------

/**
 * Run `uname -s` via ssh2 exec and classify the result. Cached on the
 * session object (`session._remoteOs`) so subsequent `create()` calls
 * skip the round-trip.
 *
 * Any non-Unix/unknown output falls back to `'windows'` only when
 * `uname` is absent entirely (empty stdout + non-zero exit); an
 * unrecognised Unix flavour (e.g. `AIX`) still maps to `'unix'` so we
 * pick `bash` as the default shell.
 *
 * @param {Object} session  RemoteSession instance (must expose .client)
 * @returns {Promise<'unix'|'windows'>}
 */
async function detectRemoteOs(session) {
  if (session && session._remoteOs === 'unix') return 'unix';
  if (session && session._remoteOs === 'windows') return 'windows';
  const client = session && session.client;
  if (!client || typeof client.exec !== 'function') {
    if (session) session._remoteOs = 'unix';
    return 'unix';
  }
  const raw = await new Promise((resolve) => {
    let settled = false;
    const settle = (v) => { if (!settled) { settled = true; resolve(v); } };
    const timer = setTimeout(() => settle(''), 3000);
    try {
      client.exec('uname -s', (err, stream) => {
        if (err || !stream) { clearTimeout(timer); settle(''); return; }
        let buf = '';
        stream.on('data', (d) => { buf += d.toString('utf8'); });
        if (stream.stderr && typeof stream.stderr.on === 'function') {
          stream.stderr.on('data', () => { /* swallow; uname is missing on Windows */ });
        }
        stream.on('close', () => { clearTimeout(timer); settle(buf); });
        stream.on('error', () => { clearTimeout(timer); settle(buf); });
      });
    } catch (_err) { clearTimeout(timer); settle(''); }
  });

  const lower = String(raw || '').trim().toLowerCase();
  let os;
  if (lower.length === 0) {
    // No `uname` → almost certainly OpenSSH on Windows.
    os = 'windows';
  } else if (lower.startsWith('linux') || lower.startsWith('darwin')
          || lower.includes('bsd') || lower.startsWith('sunos')
          || lower.startsWith('aix') || lower.startsWith('cygwin')
          || lower.startsWith('mingw') || lower.startsWith('msys')) {
    // cygwin/mingw runs bash even though the host is Windows.
    os = 'unix';
  } else {
    os = 'unix';
  }
  try { if (session) session._remoteOs = os; } catch (_e) { /* frozen target */ }
  return os;
}

/**
 * Default interactive shell per detected OS.
 * Unix → `bash`, Windows → `pwsh` (falls back to `powershell` if the
 * caller's PATH does not have pwsh; that lookup is the renderer's job).
 *
 * @param {'unix'|'windows'} os
 * @returns {string}
 */
function defaultShellFor(os) {
  return os === 'windows' ? 'pwsh' : 'bash';
}

// ---------------------------------------------------------------------------
// Bridge
// ---------------------------------------------------------------------------

class RemoteTerminalBridge extends EventEmitter {
  /**
   * @param {Object} session RemoteSession (ssh2-backed); must expose
   *                          `.client` (ssh2.Client) once connected and
   *                          emit `'state'` / `'close'` events for
   *                          Task 16.2 disconnect handling.
   */
  constructor(session) {
    super();
    if (!session) throw new TypeError('RemoteTerminalBridge: session required');

    /** @private */ this._session = session;
    /** @private @type {Map<string, TerminalState>} */ this._terms = new Map();
    /** @private */ this._closed = false;
    /** @private */ this._sessionListeners = null;

    this._wireSessionEvents(session);
  }

  /** @returns {number} live terminal count */
  get size() { return this._terms.size; }

  // -------------------------------------------------------------------------
  // Task 16.1 — create / write / resize / kill
  // -------------------------------------------------------------------------

  /**
   * Open an interactive shell on the remote host.
   *
   * @param {string} id
   * @param {{cols?:number, rows?:number, cwd?:string|null, shell?:string|null}} [opts]
   * @returns {Promise<{ok:boolean, id:string, error?:string}>}
   */
  async create(id, opts) {
    opts = opts || {};
    if (this._closed) return { ok: false, id, error: 'bridge-closed' };
    if (!id || typeof id !== 'string') return { ok: false, id, error: 'invalid-id' };
    if (this._terms.has(id)) return { ok: false, id, error: 'id-in-use' };

    const client = this._session && this._session.client;
    if (!client || typeof client.shell !== 'function') {
      return { ok: false, id, error: 'not-connected' };
    }

    const cols = sanitizeDim(opts.cols, 80);
    const rows = sanitizeDim(opts.rows, 24);
    const cwd = typeof opts.cwd === 'string' && opts.cwd.length > 0 ? opts.cwd : null;
    const explicitShell = typeof opts.shell === 'string' && opts.shell.length > 0
      ? opts.shell
      : null;

    // Resolve default shell (from `uname -s` cache when caller passed null).
    let shell = explicitShell;
    if (!shell) {
      const os = await detectRemoteOs(this._session);
      shell = defaultShellFor(os);
    }

    const stream = await openShell(client, cols, rows);
    if (!stream) return { ok: false, id, error: 'shell-open-failed' };

    /** @type {TerminalState} */
    const state = {
      stream,
      cols,
      rows,
      shell,
      cwd,
      decoder: new StringDecoder('utf8'),
      pending: [],
      pendingBytes: 0,
      flushScheduled: false,
      throttleTimer: null,
      detached: false,
    };
    this._terms.set(id, state);
    this._wireStreamEvents(id, stream);

    // Apply cwd and optional shell override in-band. Using the PTY itself
    // (rather than ssh2.exec) keeps the user's dotfiles/login-shell
    // behaviour intact.
    if (cwd) {
      try { stream.write(`cd ${shellQuote(cwd)}\r\n`); } catch (_e) { /* pty closed */ }
    }
    if (explicitShell) {
      try { stream.write(`exec ${shellQuote(explicitShell)}\r\n`); } catch (_e) { /* pty closed */ }
    }

    try { logger.info('remote-terminal-create', { id, cols, rows, shell, cwd }); }
    catch (_e) { /* logging must never fail a create */ }

    return { ok: true, id };
  }

  /**
   * Push user keystrokes / paste data into the remote PTY.
   *
   * @param {string} id
   * @param {string|Buffer} data UTF-8 preserved end-to-end.
   * @returns {boolean} true iff the write was accepted by the stream.
   */
  write(id, data) {
    const t = this._terms.get(id);
    if (!t || t.detached || !t.stream) return false;
    try {
      const buf = Buffer.isBuffer(data) ? data : Buffer.from(String(data == null ? '' : data), 'utf8');
      return Boolean(t.stream.write(buf));
    } catch (_e) {
      return false;
    }
  }

  /**
   * Resize the remote PTY window. Accepts the xterm-style options
   * object `{cols, rows, height?, width?}`.
   *
   * @param {string} id
   * @param {{cols?:number, rows?:number, height?:number, width?:number}} opts
   * @returns {boolean}
   */
  resize(id, opts) {
    const t = this._terms.get(id);
    if (!t || t.detached || !t.stream) return false;
    opts = opts || {};
    const cols = sanitizeDim(opts.cols, t.cols);
    const rows = sanitizeDim(opts.rows, t.rows);
    const height = Math.max(0, Number(opts.height) || 0);
    const width = Math.max(0, Number(opts.width) || 0);
    try {
      if (typeof t.stream.setWindow === 'function') {
        t.stream.setWindow(rows, cols, height, width);
      }
      t.cols = cols;
      t.rows = rows;
      return true;
    } catch (_e) {
      return false;
    }
  }

  /**
   * Terminate the remote shell (SIGHUP-equivalent by closing the
   * channel). Safe to call repeatedly.
   *
   * @param {string} id
   * @returns {boolean} true if an entry existed
   */
  kill(id) {
    const t = this._terms.get(id);
    if (!t) return false;
    const stream = t.stream;
    t.detached = true;
    t.stream = null;
    if (t.throttleTimer) { clearTimeout(t.throttleTimer); t.throttleTimer = null; }
    // ssh2 ClientChannel exposes both close() and end(); close() sends
    // SSH_MSG_CHANNEL_CLOSE, end() shuts our side down cleanly. We call
    // both, tolerating absence on either.
    if (stream) {
      try { if (typeof stream.close === 'function') stream.close(); } catch (_e) { /* ignore */ }
      try { if (typeof stream.end === 'function') stream.end(); } catch (_e) { /* ignore */ }
    }
    return true;
  }

  // -------------------------------------------------------------------------
  // Task 16.2 — disconnect surface + reattach
  // -------------------------------------------------------------------------

  /**
   * Wire session `'state'` / `'close'` events to disconnect handling.
   * @private
   */
  _wireSessionEvents(session) {
    if (!session || typeof session.on !== 'function') return;

    const onState = (evt) => {
      if (!evt || !evt.to) return;
      if (evt.to === 'disconnected' || evt.to === 'reconnecting' || evt.to === 'failed') {
        this._handleDisconnect(evt.reason || evt.to);
      }
    };
    const onClose = () => this._handleDisconnect('close');
    const onDisconnect = (d) => this._handleDisconnect((d && d.reason) || 'disconnect');

    try { session.on('state', onState); } catch (_e) { /* ignore */ }
    try { session.on('close', onClose); } catch (_e) { /* ignore */ }
    // Opportunistic: some callers may emit a literal 'disconnect' event.
    try { session.on('disconnect', onDisconnect); } catch (_e) { /* ignore */ }

    this._sessionListeners = { session, onState, onClose, onDisconnect };
  }

  /** @private */
  _unwireSessionEvents() {
    const s = this._sessionListeners;
    if (!s || !s.session) return;
    const off = (typeof s.session.off === 'function')
      ? s.session.off.bind(s.session)
      : (typeof s.session.removeListener === 'function'
          ? s.session.removeListener.bind(s.session)
          : null);
    if (off) {
      try { off('state', s.onState); } catch (_e) { /* ignore */ }
      try { off('close', s.onClose); } catch (_e) { /* ignore */ }
      try { off('disconnect', s.onDisconnect); } catch (_e) { /* ignore */ }
    }
    this._sessionListeners = null;
  }

  /**
   * Mark every live terminal as detached and emit `disconnected` so
   * the renderer can freeze the xterm buffer and offer a "Reattach"
   * button.
   *
   * @private
   * @param {string} reason
   */
  _handleDisconnect(reason) {
    if (this._terms.size === 0) return;
    for (const [id, t] of this._terms) {
      if (t.detached) continue;
      // Flush any decoded bytes still in the buffer so the user sees
      // the last output before the "-- Reconnected --" banner.
      if (t.pending.length > 0) this._flush(id, true);
      if (t.throttleTimer) { clearTimeout(t.throttleTimer); t.throttleTimer = null; }
      try { if (t.stream && typeof t.stream.end === 'function') t.stream.end(); }
      catch (_e) { /* ignore */ }
      t.stream = null;
      t.detached = true;
      this.emit('disconnected', { id, reason: String(reason || 'unknown') });
    }
    try { logger.info('remote-terminal-disconnected', { reason: String(reason || 'unknown'), count: this._terms.size }); }
    catch (_e) { /* ignore */ }
  }

  /**
   * Re-open a shell on (optionally) a new session for a previously
   * connected id. Preserves the cached cols/rows/cwd/shell so the
   * reattached PTY looks as close to the original as possible, then
   * writes a visible banner so the user can tell the history break.
   *
   * Returns `false` when no such id was tracked or when the shell
   * channel could not be reopened; callers can then fall back to
   * asking the user to start a new terminal.
   *
   * @param {string} id
   * @param {Object} [newSession]  defaults to the bridge's current session
   * @returns {Promise<boolean>}
   */
  async reattach(id, newSession) {
    const t = this._terms.get(id);
    if (!t) return false;
    if (this._closed) return false;

    const session = newSession || this._session;
    const client = session && session.client;
    if (!client || typeof client.shell !== 'function') return false;

    // If the caller is swapping sessions, re-wire the disconnect hooks
    // to the new one.
    if (newSession && newSession !== this._session) {
      this._unwireSessionEvents();
      this._session = newSession;
      this._wireSessionEvents(newSession);
    }

    const stream = await openShell(client, t.cols, t.rows);
    if (!stream) return false;

    // Reset stream-scoped state; preserve cached shell/cwd/dimensions.
    t.stream = stream;
    t.detached = false;
    t.decoder = new StringDecoder('utf8');
    t.pending = [];
    t.pendingBytes = 0;
    t.flushScheduled = false;
    if (t.throttleTimer) { clearTimeout(t.throttleTimer); t.throttleTimer = null; }

    this._wireStreamEvents(id, stream);

    // Banner first so xterm renders the break at the new cursor row.
    this.emit('data', { id, data: REATTACH_NOTICE });

    // Restore cwd + shell override on the fresh PTY.
    if (t.cwd) {
      try { stream.write(`cd ${shellQuote(t.cwd)}\r\n`); } catch (_e) { /* ignore */ }
    }
    // Only re-apply the shell if it differs from the default login shell.
    // We don't know the login shell here, so just skip unless the caller
    // explicitly requested one initially — we approximate that by
    // comparing against the OS default.
    const os = await detectRemoteOs(session);
    if (t.shell && t.shell !== defaultShellFor(os)) {
      try { stream.write(`exec ${shellQuote(t.shell)}\r\n`); } catch (_e) { /* ignore */ }
    }

    try { logger.info('remote-terminal-reattach', { id, cols: t.cols, rows: t.rows }); }
    catch (_e) { /* ignore */ }

    return true;
  }

  // -------------------------------------------------------------------------
  // Stream wiring (common to create + reattach)
  // -------------------------------------------------------------------------

  /** @private */
  _wireStreamEvents(id, stream) {
    stream.on('data', (buf) => this._enqueueData(id, buf));
    if (stream.stderr && typeof stream.stderr.on === 'function') {
      stream.stderr.on('data', (buf) => this._enqueueData(id, buf));
    }
    stream.on('close', (code, signal) => this._handleStreamClose(id, code, signal));
    stream.on('error', (err) => {
      this.emit('error', { id, message: err && err.message ? err.message : String(err) });
    });
  }

  /** @private */
  _handleStreamClose(id, code, signal) {
    const t = this._terms.get(id);
    if (t) {
      // Flush anything still queued so we don't swallow the final prompt.
      if (t.pending.length > 0) this._flush(id, true);
      if (t.throttleTimer) { clearTimeout(t.throttleTimer); t.throttleTimer = null; }
      // If the close was triggered by a session disconnect (not a
      // real exit), we've already emitted `disconnected` and want to
      // keep the entry around in case the caller tries to reattach.
      if (!t.detached) this._terms.delete(id);
    }
    this.emit('exit', {
      id,
      code: code == null ? null : Number(code),
      signal: signal == null ? null : String(signal),
    });
  }

  // -------------------------------------------------------------------------
  // Task 16.3 — back-pressure: coalesce arrivals, slice to 8 KiB,
  //                           throttle emission at >= 1 ms.
  // -------------------------------------------------------------------------

  /** @private */
  _enqueueData(id, buf) {
    const t = this._terms.get(id);
    if (!t) return;
    // Normalize to Buffer — ssh2 always gives us Buffers but be defensive.
    const b = Buffer.isBuffer(buf) ? buf : Buffer.from(buf || '');
    if (b.length === 0) return;
    t.pending.push(b);
    t.pendingBytes += b.length;
    if (!t.flushScheduled && !t.throttleTimer) {
      t.flushScheduled = true;
      setImmediate(() => this._flush(id, false));
    }
  }

  /**
   * Drain the per-id pending queue. Coalesces all buffered arrivals
   * into one Buffer (so bursts don't fan out into N micro-events),
   * then slices into 8 KiB windows and emits them with a 1 ms gap.
   *
   * `finalize=true` flushes any trailing partial multi-byte sequence
   * from the StringDecoder — used when the stream is closing.
   *
   * @private
   * @param {string} id
   * @param {boolean} finalize
   */
  _flush(id, finalize) {
    const t = this._terms.get(id);
    if (!t) return;
    t.flushScheduled = false;

    if (t.pending.length === 0) {
      if (finalize) {
        const tail = t.decoder.end();
        if (tail && tail.length > 0) this.emit('data', { id, data: tail });
      }
      return;
    }

    // Coalesce every pending fragment into a single Buffer so that a
    // burst of 50 × 200 B writes becomes one 10 KB emit-and-pace cycle
    // rather than 50 tiny events.
    const coalesced = Buffer.concat(t.pending, t.pendingBytes);
    t.pending = [];
    t.pendingBytes = 0;

    const self = this;
    let offset = 0;

    const emitChunk = () => {
      const tt = self._terms.get(id);
      if (!tt) return;

      if (offset >= coalesced.length) {
        if (finalize) {
          const tail = tt.decoder.end();
          if (tail && tail.length > 0) self.emit('data', { id, data: tail });
        }
        // If new data arrived during throttling, schedule another flush.
        if (tt.pending.length > 0 && !tt.flushScheduled) {
          tt.flushScheduled = true;
          setImmediate(() => self._flush(id, false));
        }
        return;
      }

      const end = Math.min(offset + CHUNK_BYTES, coalesced.length);
      const slice = coalesced.subarray(offset, end);
      offset = end;
      // StringDecoder holds any trailing partial UTF-8 sequence across
      // calls, so slicing at arbitrary byte boundaries is safe.
      const text = tt.decoder.write(slice);
      if (text && text.length > 0) self.emit('data', { id, data: text });

      if (offset < coalesced.length) {
        tt.throttleTimer = setTimeout(() => {
          tt.throttleTimer = null;
          emitChunk();
        }, THROTTLE_MS);
      } else {
        // Tail path: let the caller flush finalize / schedule next burst.
        emitChunk();
      }
    };

    emitChunk();
  }

  // -------------------------------------------------------------------------
  // Lifecycle
  // -------------------------------------------------------------------------

  /**
   * Kill every terminal and detach from the session. The bridge is
   * single-use after `close()` — callers construct a new one on the
   * next session.
   */
  async close() {
    this._closed = true;
    const ids = Array.from(this._terms.keys());
    for (const id of ids) this.kill(id);
    this._terms.clear();
    this._unwireSessionEvents();
  }
}

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

/**
 * Wrap ssh2.Client.shell() in a promise that resolves to the stream
 * (or `null` on error). Never rejects — callers surface failures as
 * `{ok:false, error}`.
 *
 * @param {any} client
 * @param {number} cols
 * @param {number} rows
 * @returns {Promise<any>}
 */
function openShell(client, cols, rows) {
  return new Promise((resolve) => {
    try {
      client.shell({ term: 'xterm-256color', cols, rows }, (err, stream) => {
        if (err || !stream) resolve(null);
        else resolve(stream);
      });
    } catch (_err) {
      resolve(null);
    }
  });
}

/** Clamp a PTY dimension to a positive integer with a sane fallback. */
function sanitizeDim(v, fallback) {
  const n = Number(v);
  if (!Number.isFinite(n) || n <= 0) return fallback;
  return Math.floor(n);
}

/**
 * Minimal POSIX single-quote shell quoting. We never interpolate user
 * input into `exec ...` / `cd ...` without wrapping here; supports
 * embedded single quotes by splitting on them.
 *
 * @param {string} s
 * @returns {string}
 */
function shellQuote(s) {
  if (s == null) return "''";
  const str = String(s);
  if (str.length === 0) return "''";
  // Only quote when necessary, matching bash-safe characters.
  if (/^[A-Za-z0-9_@%+=:,./-]+$/.test(str)) return str;
  return "'" + str.replace(/'/g, "'\\''") + "'";
}

// ---------------------------------------------------------------------------
// Types (JSDoc)
// ---------------------------------------------------------------------------

/**
 * @typedef {Object} TerminalState
 * @property {any}            stream         ssh2 ClientChannel (null when detached)
 * @property {number}         cols
 * @property {number}         rows
 * @property {string}         shell          resolved default or override
 * @property {string|null}    cwd
 * @property {StringDecoder}  decoder        UTF-8 safe across byte boundaries
 * @property {Buffer[]}       pending        coalesced arrivals awaiting flush
 * @property {number}         pendingBytes   sum of pending[i].length
 * @property {boolean}        flushScheduled a setImmediate flush is queued
 * @property {NodeJS.Timeout|null} throttleTimer active throttle gap timer
 * @property {boolean}        detached       session disconnected, stream dead
 */

module.exports = {
  RemoteTerminalBridge,
  // exported for tests / future reuse
  detectRemoteOs,
  defaultShellFor,
  CHUNK_BYTES,
  THROTTLE_MS,
  REATTACH_NOTICE,
};
