'use strict';
/**
 * SessionRouter — dispatches IPC between local and the active RemoteSession.
 *
 * Feature: remote-ssh · Tasks 21.1 & 21.2
 * Covers Requirements: 5.3, 5.4, 5.5, 6.1, 7.1, 7.5
 *
 * Responsibility
 * --------------
 * The router is the *single source of truth* for "is there a remote session
 * we should be dispatching file/terminal/api calls to right now?". It sits
 * between the ipc-*-handlers (Task 22) and the RemoteSessionManager (Task 12),
 * and also exposes an `apiBase()` helper that mirrors the renderer-side
 * helper in `src/lib/utils.js` (Task 21.2).
 *
 * Two integration paths are supported:
 *
 *   1. Manager-driven (new, preferred — matches Task 21.1 contract):
 *        sessionRouter.setManager(remoteSessionManager)
 *      The router then reads `manager.getActive()` every time it is asked,
 *      so remote/local switches made by the manager are reflected without
 *      needing to re-wire the router.
 *
 *   2. Context-driven (legacy, used by `ipc-remote-handlers.js`):
 *        sessionRouter.setActive({session, fileBridge, termBridge, localPort})
 *        sessionRouter.setActive(null)  // go local
 *      When the manager is not wired, the router falls back to the ctx
 *      set by the legacy caller. This keeps `electron/src/ipc-remote-
 *      handlers.js` working unchanged (that file is owned by Task 23.x).
 *
 * Expected shape of a connected `RemoteSession` (Task 21.1):
 *
 *     session.state === 'connected'
 *     session.client                                    // raw ssh2 Client
 *     session.bridges = {
 *       fs:        RemoteFileBridge,                    // list/read/write/stat/...
 *       terminal:  RemoteTerminalBridge,                // create/write/resize/kill
 *       forwarder: PortForwarder,                       // exposes .localPort
 *     }
 *
 * When the legacy `setActive(ctx)` path is used, the router synthesises an
 * equivalent `bridges` object on the fly so `dispatch()` and `apiBase()`
 * behave identically regardless of which integration path is active.
 *
 * Non-responsibilities
 * --------------------
 * - Does NOT own the session lifecycle (RemoteSessionManager owns it).
 * - Does NOT own port allocation (port-forwarder + port-allocator own it).
 * - Does NOT implement local fs/terminal ops — falling back to local means
 *   telling the caller "not routed, run your own local implementation".
 */

const { EventEmitter } = require('events');
const { execSync } = require('child_process');

/** Default local API base — matches ai_engine/server.py defaults. */
const LOCAL_API_BASE = 'http://localhost:8765';

/**
 * Error thrown by `dispatch()` when the router is NOT in remote mode (or
 * when the requested op does not exist on the active session's bridges).
 * Callers catch this as the signal to fall through to local implementation.
 */
const NOT_ROUTED = 'NOT_ROUTED';

/**
 * Shell-quote a path/string for safe interpolation into a remote bash/sh
 * command line. Single-quote wrap + escape any embedded single-quotes by
 * closing-quoting-opening. This is the same trick OpenSSH and most
 * remote-exec shims use; it is safe for every POSIX shell.
 *
 * @param {string} s
 * @returns {string}
 */
function shellQuote(s) {
  if (s === undefined || s === null) return "''";
  const str = String(s);
  return "'" + str.replace(/'/g, "'\\''") + "'";
}

/**
 * Promisified `ssh2.Client#exec` that accumulates stdout/stderr and the
 * exit code into the same `{stdout, stderr, code}` shape that
 * `child_process.execSync` produces locally (after our wrapper below),
 * so callers never have to branch on transport type.
 *
 * Rejects only on transport/channel errors; a non-zero remote exit is
 * still a *successful* dispatch from the router's perspective and is
 * returned as `{stdout, stderr, code: <nonzero>}`.
 *
 * @param {Object} client  ssh2 Client (must expose `.exec(cmd, opts, cb)`)
 * @param {string} cmd
 * @param {Object} [execOpts] Passed verbatim to `client.exec`.
 * @returns {Promise<{stdout:string, stderr:string, code:number}>}
 */
function sshExec(client, cmd, execOpts) {
  return new Promise((resolve, reject) => {
    let settled = false;
    const settle = (fn) => { if (!settled) { settled = true; fn(); } };
    try {
      client.exec(cmd, execOpts || {}, (err, stream) => {
        if (err) { settle(() => reject(err)); return; }
        let stdout = '';
        let stderr = '';
        let code = 0;
        stream.on('data', (chunk) => { stdout += chunk.toString('utf8'); });
        if (stream.stderr && typeof stream.stderr.on === 'function') {
          stream.stderr.on('data', (chunk) => { stderr += chunk.toString('utf8'); });
        }
        stream.on('exit', (c) => { code = Number.isFinite(c) ? c : 0; });
        stream.on('close', () => settle(() => resolve({ stdout, stderr, code })));
        stream.on('error', (e) => settle(() => reject(e)));
      });
    } catch (e) {
      settle(() => reject(e));
    }
  });
}

class SessionRouter extends EventEmitter {
  constructor() {
    super();
    /** @private @type {Object|null} */ this._manager = null;
    /** @private @type {Object|null} */ this._legacySession = null;
    /** @private @type {Object|null} */ this._legacyFileBridge = null;
    /** @private @type {Object|null} */ this._legacyTermBridge = null;
    /** @private @type {number|null} */ this._legacyLocalPort = null;
  }

  // -------------------------------------------------------------------------
  // Wiring
  // -------------------------------------------------------------------------

  /**
   * Inject the RemoteSessionManager (called once from `electron/main.js` at
   * startup, Task 23.2). When set, `getActive()` always reads through the
   * manager so routing reflects `manager.switchActive(...)` automatically.
   *
   * Passing `null` clears the manager; the router then falls back to the
   * legacy ctx set via `setActive(...)`.
   *
   * @param {Object|null} manager
   */
  setManager(manager) {
    this._manager = (manager && typeof manager.getActive === 'function') ? manager : null;
    this.emit('route-changed', this._describeRoute());
  }

  /**
   * Legacy ctx-style wiring used by `ipc-remote-handlers.js` until Task 23
   * migrates that handler to the manager-driven path. Supports:
   *   - `setActive({session, fileBridge, termBridge, localPort})`
   *   - `setActive(null)` → return to local routing.
   *
   * Idempotent. Emits `route-changed` on every call.
   *
   * @param {Object|null} ctx
   */
  setActive(ctx) {
    if (!ctx) {
      this._legacySession = null;
      this._legacyFileBridge = null;
      this._legacyTermBridge = null;
      this._legacyLocalPort = null;
      this.emit('route-changed', { mode: 'local' });
      return;
    }
    this._legacySession = ctx.session || null;
    this._legacyFileBridge = ctx.fileBridge || null;
    this._legacyTermBridge = ctx.termBridge || null;
    this._legacyLocalPort = Number.isInteger(ctx.localPort) ? ctx.localPort : null;
    this.emit('route-changed', {
      mode: 'remote',
      alias: this._legacySession && this._legacySession.alias,
      localPort: this._legacyLocalPort,
    });
  }

  // -------------------------------------------------------------------------
  // Active session accessors (Task 21.1 contract)
  // -------------------------------------------------------------------------

  /**
   * Returns the active RemoteSession (from the manager when wired, from
   * the legacy ctx otherwise), or `null` when routing is local.
   *
   * The returned object is the raw session — callers SHOULD NOT mutate
   * it. Use `dispatch()` for bridge calls and `exec()` for shell exec.
   *
   * @returns {Object|null}
   */
  getActive() {
    // Legacy ctx (set by ipc-remote-handlers.js) takes priority because
    // that's where the actual connected session lives. The manager from
    // main.js is a separate instance that doesn't own the session.
    if (this._legacySession) return this._legacySession;
    if (this._manager) {
      try {
        const m = this._manager.getActive();
        if (m) return m;
      } catch (_e) { /* fall through */ }
    }
    return null;
  }

  /**
   * True iff there is an active session AND it is in the `connected`
   * state AND the caller did not ask to force local. Mirrors Property 11
   * in design.md (IPC routing decision).
   *
   * @param {{forceLocal?: boolean}} [opts]
   * @returns {boolean}
   */
  isRemoteActive(opts) {
    if (opts && opts.forceLocal) return false;
    const active = this.getActive();
    if (!active) return false;
    // Accept any state that has a live client — not just 'connected'.
    // The session may be in 'provisioning' or 'forwarding' but still
    // has a working SSH client for file/exec operations.
    if (active.state === 'connected') return true;
    // Legacy ctx: if session has a client, it's usable
    if (active.client && typeof active.client.exec === 'function') return true;
    return false;
  }

  /**
   * Legacy boolean getter used by `ipc-fs-handlers.js`. Equivalent to
   * `isRemoteActive()` — retained for backward compatibility.
   * @returns {boolean}
   */
  get isRemote() { return this.isRemoteActive(); }

  /** Alias of the active session, or `null`. */
  get activeAlias() {
    const a = this.getActive();
    return a ? a.alias || null : null;
  }

  /**
   * Resolve the `bridges` object for the currently active session. When
   * the session was set via the manager-driven path, this is
   * `session.bridges`. When it came via `setActive(ctx)`, we synthesise
   * an equivalent object from the ctx fields so `dispatch()` works
   * uniformly.
   *
   * @private
   * @returns {{fs:Object|null, terminal:Object|null, forwarder:Object|null}|null}
   */
  _resolveBridges() {
    if (!this.isRemoteActive()) return null;
    const active = this.getActive();
    if (!active) return null;
    if (active.bridges && typeof active.bridges === 'object') return active.bridges;
    // Legacy fallback — synthesise from the ctx fields.
    return {
      fs: this._legacyFileBridge,
      terminal: this._legacyTermBridge,
      forwarder: (this._legacyLocalPort != null)
        ? { localPort: this._legacyLocalPort }
        : null,
    };
  }

  /**
   * Legacy accessor retained for `ipc-fs-handlers.js`. Returns the
   * RemoteFileBridge when remote-active, else `null`.
   * @returns {Object|null}
   */
  getFileBridge() {
    const bridges = this._resolveBridges();
    return (bridges && bridges.fs) || null;
  }

  /**
   * Legacy accessor for `ipc-terminal-handlers.js` (Task 22.2).
   * @returns {Object|null}
   */
  getTermBridge() {
    const bridges = this._resolveBridges();
    return (bridges && bridges.terminal) || null;
  }

  // -------------------------------------------------------------------------
  // dispatch() — generic bridge op router (Task 21.1)
  // -------------------------------------------------------------------------

  /**
   * Dispatch a remote bridge operation. `opName` uses dotted notation:
   *
   *     dispatch('fs.read', [remotePath, {encoding:'utf8'}])
   *     → bridges.fs.read(remotePath, {encoding:'utf8'})
   *
   *     dispatch('terminal.create', [id, {cols, rows}])
   *     → bridges.terminal.create(id, {cols, rows})
   *
   * Throws `Error('NOT_ROUTED')` when:
   *   - no active session, OR
   *   - the session is not in state `connected`, OR
   *   - `opts.forceLocal` was passed, OR
   *   - the target bridge or method does not exist.
   *
   * Callers that catch NOT_ROUTED MUST fall through to their local
   * implementation (this is how the fs/terminal IPC handlers keep one
   * code path for both transports).
   *
   * @param {string} opName               Dotted op name `"<bridge>.<method>"`.
   * @param {Array=}  args                Positional args for the method.
   * @param {{forceLocal?:boolean}=} opts
   * @returns {*} The method's return value (possibly a Promise).
   */
  dispatch(opName, args, opts) {
    if (opts && opts.forceLocal) {
      throw this._notRouted('forceLocal requested');
    }
    if (!this.isRemoteActive()) {
      throw this._notRouted('no active remote session');
    }
    if (typeof opName !== 'string' || opName.length === 0) {
      throw this._notRouted('invalid opName');
    }
    const dot = opName.indexOf('.');
    if (dot <= 0 || dot === opName.length - 1) {
      throw this._notRouted(`opName must be "<bridge>.<method>" (got "${opName}")`);
    }
    const bridgeName = opName.slice(0, dot);
    const methodName = opName.slice(dot + 1);

    const bridges = this._resolveBridges();
    const bridge = bridges && bridges[bridgeName];
    if (!bridge) {
      throw this._notRouted(`bridge "${bridgeName}" is not mounted`);
    }
    const method = bridge[methodName];
    if (typeof method !== 'function') {
      throw this._notRouted(`method "${bridgeName}.${methodName}" not found`);
    }
    const argList = Array.isArray(args) ? args : [];
    return method.apply(bridge, argList);
  }

  /**
   * Construct the canonical NOT_ROUTED error. The `message` is NOT_ROUTED
   * (so callers can string-compare) and `reason` carries a human-readable
   * detail for logs.
   *
   * @private
   * @param {string} reason
   * @returns {Error}
   */
  _notRouted(reason) {
    const err = new Error(NOT_ROUTED);
    err.code = NOT_ROUTED;
    err.reason = reason || '';
    return err;
  }

  // -------------------------------------------------------------------------
  // exec() — unified shell exec (Task 21.1)
  // -------------------------------------------------------------------------

  /**
   * Execute a shell command either on the remote host (when a remote
   * session is active) or locally. The return shape is the SAME for both
   * transports:
   *
   *     { stdout: string, stderr: string, code: number }
   *
   * This lets `ipc-git-handlers.js` (Task 22.3) treat `sessionRouter.exec`
   * as a drop-in replacement for `child_process.execSync` regardless of
   * transport — the only behavioural difference is that exec() returns a
   * Promise, so callers that used `execSync` must `await` it.
   *
   * Remote behaviour
   * ----------------
   * - Uses `session.client.exec(cmd, execOpts)` via a promisified wrapper.
   * - When `cwd` is provided, the command string is prefixed with
   *   `cd <shellQuoted(cwd)> && ` so the command runs in that directory
   *   without relying on a shell-specific `-C` flag. We also pass
   *   `{env: {PWD: cwd}}` on the exec opts so `$PWD` matches (some
   *   tools — notably git's hooks — use `$PWD` instead of `getcwd(3)`).
   * - A non-zero remote exit is NOT treated as a thrown error; it is
   *   returned as `{stdout, stderr, code: <nonzero>}`.
   *
   * Local behaviour
   * ---------------
   * - Uses `child_process.execSync(cmd, {cwd})`.
   * - On a non-zero exit, `execSync` throws; we catch and coerce the
   *   thrown error into the same `{stdout, stderr, code}` shape so the
   *   caller sees a uniform contract.
   *
   * @param {string} cmd
   * @param {{cwd?:string, forceLocal?:boolean, timeout?:number}} [opts]
   * @returns {Promise<{stdout:string, stderr:string, code:number}>}
   */
  async exec(cmd, opts) {
    const options = opts || {};
    const useRemote = this.isRemoteActive({ forceLocal: Boolean(options.forceLocal) });

    // options.env: 추가 환경변수 맵. 셸 문자열에 `VAR=val cmd` 프리픽스를 넣으면
    // POSIX 셸에서만 동작하고 Windows cmd.exe에서 깨진다. 그래서 env는 항상 셸 밖에서
    // 주입한다 — 원격(bash)은 `export`로, 로컬은 execSync의 env 옵션으로.
    const envMap = (options.env && typeof options.env === 'object') ? options.env : null;

    if (useRemote) {
      const active = this.getActive();
      const client = active && active.client;
      if (!client || typeof client.exec !== 'function') {
        throw this._notRouted('active session has no usable ssh2 client');
      }
      const cwd = options.cwd ? String(options.cwd) : '';
      // 원격 셸(bash)에서 env를 확실히 적용 — ssh 서버가 AcceptEnv를 제한할 수 있어
      // exec env 옵션 대신 `export`를 명령 앞에 인라인한다.
      let prefix = '';
      if (envMap) {
        for (const [k, v] of Object.entries(envMap)) {
          if (/^[A-Za-z_][A-Za-z0-9_]*$/.test(k)) prefix += `export ${k}=${shellQuote(String(v))}; `;
        }
      }
      const base = cwd ? `cd ${shellQuote(cwd)} && ${cmd}` : cmd;
      const fullCmd = prefix + base;
      const execOpts = cwd ? { env: { PWD: cwd } } : {};
      return sshExec(client, fullCmd, execOpts);
    }

    // Local branch — wrap execSync to produce the same shape.
    try {
      const execSyncOpts = {};
      if (options.cwd) execSyncOpts.cwd = options.cwd;
      if (Number.isFinite(options.timeout)) execSyncOpts.timeout = options.timeout;
      // env는 셸 밖에서 주입 → Windows cmd.exe / POSIX sh 모두에서 동일하게 동작.
      if (envMap) execSyncOpts.env = { ...process.env, ...envMap };
      // Capture stdout/stderr separately — default execSync inherits
      // stderr; we redirect to 'pipe' so callers can inspect it.
      execSyncOpts.stdio = ['ignore', 'pipe', 'pipe'];
      const stdoutBuf = execSync(cmd, execSyncOpts);
      return {
        stdout: stdoutBuf ? stdoutBuf.toString('utf8') : '',
        stderr: '',
        code: 0,
      };
    } catch (err) {
      return {
        stdout: err && err.stdout ? err.stdout.toString('utf8') : '',
        stderr: err && err.stderr ? err.stderr.toString('utf8') : '',
        code: err && Number.isFinite(err.status) ? err.status : 1,
      };
    }
  }

  // -------------------------------------------------------------------------
  // apiBase() — main-side helper (Task 21.2)
  // -------------------------------------------------------------------------

  /**
   * Return the base URL to use when the Electron main process (or a
   * helper inside it) needs to reach the ai_engine server.
   *
   * - When a remote session is connected AND has a live port forwarder
   *   with a bound local port, return `http://127.0.0.1:<localPort>`
   *   (routes through the SSH tunnel).
   * - Otherwise return the local default `http://localhost:8765`.
   *
   * This is the main-process mirror of the renderer-side helper in
   * `src/lib/utils.js` (also added in Task 21.2). Property 10 in
   * design.md formalises the decision.
   *
   * @returns {string}
   */
  apiBase() {
    if (this.isRemoteActive()) {
      const bridges = this._resolveBridges();
      const fwd = bridges && bridges.forwarder;
      const port = fwd && Number(fwd.localPort);
      if (Number.isInteger(port) && port > 0) {
        return `http://127.0.0.1:${port}`;
      }
    }
    return LOCAL_API_BASE;
  }

  /**
   * True when `getActive().opts.forceLocal` was NOT set AND the active
   * session is connected — kept as a thin wrapper for call-site
   * readability at the fs/terminal IPC handlers (Task 22.*).
   *
   * @param {{forceLocal?:boolean}} [opts]
   * @returns {boolean}
   */
  shouldRouteRemote(opts) { return this.isRemoteActive(opts); }

  /**
   * Small helper that describes the current route in a log-friendly
   * object — emitted on every `setManager` / `setActive` call so
   * listeners (UI status bar, log sinks) can update in one place.
   *
   * @private
   * @returns {{mode:'local'|'remote', alias?:string, localPort?:number|null}}
   */
  _describeRoute() {
    if (!this.isRemoteActive()) return { mode: 'local' };
    const a = this.getActive();
    const bridges = this._resolveBridges();
    const port = (bridges && bridges.forwarder && bridges.forwarder.localPort) || null;
    return { mode: 'remote', alias: a ? a.alias : null, localPort: port };
  }
}

// Singleton — there is exactly one routing decision at any given time for
// the running Electron main process. Callers that need a fresh instance
// for testing can instantiate `SessionRouter` directly.
const instance = new SessionRouter();

module.exports = instance;
module.exports.SessionRouter = SessionRouter;
module.exports.LOCAL_API_BASE = LOCAL_API_BASE;
module.exports.NOT_ROUTED = NOT_ROUTED;
// Expose the shell-quote helper for callers that need to build their
// own remote commands consistently with our exec() quoting.
module.exports.shellQuote = shellQuote;
