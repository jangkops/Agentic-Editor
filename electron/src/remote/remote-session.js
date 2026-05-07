'use strict';
/**
 * RemoteSession — ssh2 Client wrapper with explicit state machine.
 *
 * Feature: remote-ssh
 * Covers Requirements: 3.1–3.10, 12.2
 *
 * Represents a single logical SSH session to one `Remote_Host` alias.
 * Owns:
 *  - the underlying ssh2 Client(s) (one per hop when ProxyJump is used),
 *  - the authoritative state machine that gates every operation,
 *  - the StopPolicy counter for auth-failure storms,
 *  - the host-key prompt flow (TOFU).
 *
 * It does NOT own:
 *  - port forwarding (Phase 3: `port-forwarder.js`),
 *  - SFTP bridge (Phase 3: `remote-file-bridge.js`),
 *  - shell channels (Phase 3: `remote-terminal-bridge.js`),
 *  - the multi-session set (Phase 2 Task 12: `remote-session-manager.js`).
 *
 * Event contract (all emitted on `this`, which is an EventEmitter):
 *
 *   'state' {from, to, reason?}
 *       Every transition, including same-state drops (filtered here).
 *       Mirrors the state machine diagram in design.md §Architecture.
 *
 *   'auth-prompt' {alias, kind, payload}
 *       Renderer must resolve by calling `session.respondAuth(kind, value)`.
 *       kind ∈ {'passphrase','password','2fa','host-key'}.
 *
 *   'host-key-prompt' {alias, host, port, fingerprint}
 *       First-contact TOFU case. Caller responds via
 *       `session.respondAuth('host-key', {accept: boolean})`.
 *
 *   'banner' {text}
 *       Server-sent banner (RFC 4252 §5.4). Surfaced verbatim.
 *
 *   'error' {code, message, cause?}
 *       Non-recoverable session error; also drives the state machine
 *       to `'failed'`.
 *
 *   'ready'
 *       Reached `connected`. Callers can open SFTP / shell / forwards.
 *
 *   'close'
 *       The underlying ssh2 Client closed. Also a state event.
 */

const { EventEmitter } = require('events');

const credentialCache = require('./credential-cache');
const hostKeyStore = require('./host-key-store');
const logger = require('./logger');
const { buildConnectConfig } = require('./ssh-client-builder');
const authPolicy = require('./auth-policy');

/**
 * ssh2 is a large native-dep-bearing module; we lazy-require so the
 * rest of the remote-ssh feature can be tested without installing it.
 * When ssh2 is absent, `connect()` throws a clear error; unit tests
 * that need to exercise the state machine without ssh2 should inject
 * a fake client factory via `opts.clientFactory`.
 *
 * @returns {typeof import('ssh2').Client}
 */
function loadSsh2Client() {
  // eslint-disable-next-line global-require
  const ssh2 = require('ssh2');
  if (!ssh2 || typeof ssh2.Client !== 'function') {
    throw new Error('ssh2 module is installed but does not expose Client');
  }
  return ssh2.Client;
}

// ---------------------------------------------------------------------------
// State machine (Req 3.1–3.10, 12.2)
// ---------------------------------------------------------------------------

/**
 * Canonical set of session states. Kept as a frozen object so any
 * typo is caught at bundle time (property access on an undefined key
 * returns `undefined`, which the validator then rejects).
 */
const STATES = Object.freeze({
  DISCONNECTED: 'disconnected',
  CONNECTING: 'connecting',
  AUTHENTICATING: 'authenticating',
  PROVISIONING: 'provisioning',
  FORWARDING: 'forwarding',
  CONNECTED: 'connected',
  RECONNECTING: 'reconnecting',
  FAILED: 'failed',
});

/**
 * Adjacency list of allowed transitions, copied verbatim from the
 * state machine diagram in design.md §Architecture.
 *
 *   disconnected  → connecting
 *   connecting    → authenticating | failed | disconnected
 *   authenticating→ provisioning   | failed | disconnected
 *   provisioning  → forwarding     | failed
 *   forwarding    → connected      | failed
 *   connected     → reconnecting   | disconnected | failed
 *   reconnecting  → authenticating | failed       | disconnected
 *   failed        → disconnected   (operator retry resets)
 *
 * The validator rejects any unlisted pair. The caller (this file)
 * therefore cannot accidentally jump e.g. from `disconnected` to
 * `connected` by forgetting an intermediate step — the invariant is
 * checked at every transition.
 *
 * @type {Readonly<Record<string, ReadonlyArray<string>>>}
 */
const ALLOWED_TRANSITIONS = Object.freeze({
  [STATES.DISCONNECTED]: Object.freeze([STATES.CONNECTING]),
  [STATES.CONNECTING]: Object.freeze([STATES.AUTHENTICATING, STATES.FAILED, STATES.DISCONNECTED]),
  [STATES.AUTHENTICATING]: Object.freeze([STATES.PROVISIONING, STATES.FAILED, STATES.DISCONNECTED]),
  [STATES.PROVISIONING]: Object.freeze([STATES.FORWARDING, STATES.FAILED]),
  [STATES.FORWARDING]: Object.freeze([STATES.CONNECTED, STATES.FAILED]),
  [STATES.CONNECTED]: Object.freeze([STATES.RECONNECTING, STATES.DISCONNECTED, STATES.FAILED]),
  [STATES.RECONNECTING]: Object.freeze([STATES.AUTHENTICATING, STATES.FAILED, STATES.DISCONNECTED]),
  [STATES.FAILED]: Object.freeze([STATES.DISCONNECTED]),
});

/**
 * Return true iff `from → to` is a permitted transition.
 * @param {string} from
 * @param {string} to
 * @returns {boolean}
 */
function isValidTransition(from, to) {
  const allowed = ALLOWED_TRANSITIONS[from];
  return Array.isArray(allowed) && allowed.includes(to);
}

// ---------------------------------------------------------------------------
// Error codes (Req 12.4 → consumed by error-surface.js in Phase 6)
// ---------------------------------------------------------------------------

const ERR = Object.freeze({
  SSH2_MISSING: 'ssh2-not-installed',
  ALREADY_CONNECTED: 'already-connected',
  INVALID_TRANSITION: 'invalid-state-transition',
  AUTH_FAILED: 'auth-failed',
  AUTH_STORM: 'auth-failure-storm',
  HOST_KEY_MISMATCH: 'host-key-mismatch',
  HOST_KEY_REJECTED: 'host-key-rejected',
  HANDSHAKE_ERROR: 'handshake-error',
  CLIENT_ERROR: 'ssh-client-error',
  PROMPT_TIMEOUT: 'prompt-timeout',
  CONNECTION_CLOSED: 'connection-closed',
  CANCELLED: 'cancelled',
});

/**
 * Session options shape documented here for IDE type hints.
 *
 * @typedef {Object} SessionOptions
 * @property {Object} target               Resolved HostEntry (from ssh-config-parser).
 * @property {Object[]=} hops              Resolved HostEntries for ProxyJump hops.
 * @property {Function=} clientFactory     Test hook: returns an object matching ssh2.Client.
 * @property {number=}  handshakeTimeoutMs (default 10000)
 * @property {number=}  promptTimeoutMs    (default 120000)
 * @property {(alias:string) => boolean=} isKnownAlias
 *    Optional lookup used when StrictHostKeyChecking is not explicit.
 */

class RemoteSession extends EventEmitter {
  /**
   * @param {SessionOptions} opts
   */
  constructor(opts) {
    super();
    if (!opts || !opts.target || typeof opts.target !== 'object') {
      throw new TypeError('RemoteSession: target HostEntry is required');
    }
    /** @private */ this._target = opts.target;
    /** @private */ this._hops = Array.isArray(opts.hops) ? opts.hops.slice() : [];
    /** @private */ this._clientFactory = typeof opts.clientFactory === 'function'
      ? opts.clientFactory
      : null;
    /** @private */ this._handshakeTimeoutMs = Number(opts.handshakeTimeoutMs) > 0
      ? Number(opts.handshakeTimeoutMs)
      : 10000;
    /** @private */ this._promptTimeoutMs = Number(opts.promptTimeoutMs) > 0
      ? Number(opts.promptTimeoutMs)
      : 120000;
    /** @private */ this._isKnownAlias = typeof opts.isKnownAlias === 'function'
      ? opts.isKnownAlias
      : () => hostKeyStore.get(this._target.hostName || this._target.alias, this._target.port || 22) !== null;

    /** @private @type {string} */ this._state = STATES.DISCONNECTED;
    /** @private @type {number[]} */ this._authFailures = [];
    /** @private @type {any} */ this._client = null;
    /** @private @type {Map<string, {resolve:Function, reject:Function, timer:NodeJS.Timeout}>} */
    this._pendingPrompts = new Map();
    /** @private @type {{fingerprint:string|null, resolve:Function}|null} */
    this._pendingHostKey = null;
    /** @private @type {boolean} */ this._closed = false;
    /** @private @type {Object|null} */ this._lastError = null;
  }

  /** Current state constant. */
  get state() { return this._state; }

  /** Alias (for logging / UI). */
  get alias() { return this._target.alias; }

  /** Resolved host:port pair. */
  get endpoint() {
    return {
      host: this._target.hostName || this._target.alias,
      port: Number(this._target.port) || 22,
      user: this._target.user || '',
    };
  }

  /** Underlying ssh2 Client for callers that need to open SFTP / shell. */
  get client() { return this._client; }

  // -------------------------------------------------------------------------
  // State transition (centralized so every move is logged + validated)
  // -------------------------------------------------------------------------

  /**
   * Attempt a transition. Returns `true` on success, `false` if the
   * move is rejected (and logs an `error` event). The method is the
   * ONLY place `this._state` is mutated; any other write is a bug.
   *
   * @private
   * @param {string} to
   * @param {string=} reason
   * @returns {boolean}
   */
  _transition(to, reason) {
    const from = this._state;
    if (from === to) return true;
    if (!isValidTransition(from, to)) {
      const err = {
        code: ERR.INVALID_TRANSITION,
        message: `Illegal transition ${from} → ${to}`,
        from, to,
      };
      this._lastError = err;
      try {
        logger.warn('remote-session-invalid-transition', {
          alias: this._target.alias,
          from, to, reason: reason || null,
        });
      } catch (_e) { /* never fail on logging */ }
      this.emit('error', err);
      return false;
    }
    this._state = to;
    try {
      logger.info('remote-session-state', {
        alias: this._target.alias,
        from, to, reason: reason || null,
      });
    } catch (_e) { /* never fail on logging */ }
    this.emit('state', { from, to, reason: reason || null });
    return true;
  }

  // -------------------------------------------------------------------------
  // Auth prompt plumbing
  // -------------------------------------------------------------------------

  /**
   * Emit an `auth-prompt` event and return a Promise that resolves
   * when the renderer calls `respondAuth()`. Rejects on timeout or
   * session cancellation.
   *
   * @private
   * @param {string} kind 'passphrase' | 'password' | '2fa'
   * @param {Object} payload
   * @returns {Promise<any>}
   */
  _promptAuth(kind, payload) {
    return new Promise((resolve, reject) => {
      const key = kind + ':' + Date.now() + ':' + Math.random().toString(36).slice(2, 8);
      const timer = setTimeout(() => {
        this._pendingPrompts.delete(key);
        reject(Object.assign(new Error('auth prompt timed out'), { code: ERR.PROMPT_TIMEOUT }));
      }, this._promptTimeoutMs);
      this._pendingPrompts.set(key, { resolve, reject, timer });
      try {
        this.emit('auth-prompt', {
          alias: this._target.alias,
          kind,
          key,
          payload: payload || {},
        });
      } catch (_err) {
        // listener threw — reject the pending promise
        const p = this._pendingPrompts.get(key);
        if (p) {
          clearTimeout(p.timer);
          this._pendingPrompts.delete(key);
          reject(_err);
        }
      }
    });
  }

  /**
   * Renderer-side response to an outstanding auth prompt. `key` comes
   * from the original `auth-prompt` event. For host-key prompts, pass
   * kind=`'host-key'` and `value={accept:true|false}`.
   *
   * @param {string} kind
   * @param {*} value
   * @param {string=} key Pass the original event's `key` to target a
   *                     specific prompt. If omitted, we resolve the
   *                     oldest pending prompt of that kind.
   */
  respondAuth(kind, value, key) {
    if (kind === 'host-key') {
      if (!this._pendingHostKey) return;
      const resolver = this._pendingHostKey.resolve;
      this._pendingHostKey = null;
      try { resolver(Boolean(value && value.accept)); } catch (_e) { /* swallow */ }
      return;
    }
    if (typeof key === 'string' && this._pendingPrompts.has(key)) {
      const { resolve, timer } = this._pendingPrompts.get(key);
      clearTimeout(timer);
      this._pendingPrompts.delete(key);
      resolve(value);
      return;
    }
    // Fallback: resolve oldest matching kind
    for (const [k, v] of this._pendingPrompts) {
      if (k.startsWith(kind + ':')) {
        clearTimeout(v.timer);
        this._pendingPrompts.delete(k);
        v.resolve(value);
        return;
      }
    }
  }

  /**
   * Cancel every pending prompt (used on session teardown).
   * @private
   */
  _rejectAllPrompts(reason) {
    for (const [, v] of this._pendingPrompts) {
      clearTimeout(v.timer);
      try { v.reject(Object.assign(new Error(reason || 'cancelled'), { code: ERR.CANCELLED })); }
      catch (_e) { /* ignore */ }
    }
    this._pendingPrompts.clear();
    if (this._pendingHostKey) {
      try { this._pendingHostKey.resolve(false); } catch (_e) { /* ignore */ }
      this._pendingHostKey = null;
    }
  }

  // -------------------------------------------------------------------------
  // connect() — full handshake flow
  // -------------------------------------------------------------------------

  /**
   * Dial the host and run the authentication + host-key handshake.
   *
   * On success, the session ends up in state `provisioning` (the
   * caller then invokes the provisioner — Phase 4 — which transitions
   * us through `forwarding` → `connected`).
   *
   * The method resolves when we reach `provisioning` (the first
   * "ready to use" state); it rejects with a structured error when
   * the handshake fails or the session is cancelled.
   *
   * @returns {Promise<void>}
   */
  async connect() {
    if (this._closed) {
      throw Object.assign(new Error('session is closed'), { code: ERR.CONNECTION_CLOSED });
    }
    if (this._state !== STATES.DISCONNECTED) {
      throw Object.assign(new Error(`cannot connect while ${this._state}`), { code: ERR.ALREADY_CONNECTED });
    }

    if (!this._transition(STATES.CONNECTING, 'connect()')) {
      throw Object.assign(new Error('illegal initial transition'), { code: ERR.INVALID_TRANSITION });
    }

    // Pre-flight: auth storm?
    if (authPolicy.shouldStop(this._authFailures, Date.now())) {
      this._fail(ERR.AUTH_STORM, 'Too many auth failures in the last 60s; refusing to retry.');
      throw this._lastError;
    }

    let ClientCtor;
    try {
      ClientCtor = this._clientFactory || loadSsh2Client();
    } catch (err) {
      this._fail(ERR.SSH2_MISSING, err && err.message ? err.message : 'ssh2 unavailable', err);
      throw this._lastError;
    }

    // Build ssh2 config chain.
    let built;
    try {
      built = buildConnectConfig({
        target: this._target,
        hops: this._hops,
        onHostKeyVerdict: (verdict) => this._handleHostKeyVerdict(verdict),
        onAuthPrompt: (prompt) => this._promptAuth('2fa', prompt),
      });
    } catch (err) {
      this._fail(ERR.HANDSHAKE_ERROR, err && err.message ? err.message : 'config build failed', err);
      throw this._lastError;
    }

    // If any IdentityFile needs a passphrase and none is cached, prompt now.
    if (built.needsPrompt) {
      try {
        const passphrase = await this._promptAuth('passphrase', {
          host: this._target.hostName || this._target.alias,
          user: this._target.user || '',
        });
        credentialCache.set(this._target.alias, { passphrase: String(passphrase || '') });
        // Rebuild with the cached passphrase now present.
        built = buildConnectConfig({
          target: this._target,
          hops: this._hops,
          onHostKeyVerdict: (verdict) => this._handleHostKeyVerdict(verdict),
          onAuthPrompt: (prompt) => this._promptAuth('2fa', prompt),
        });
      } catch (err) {
        this._fail(ERR.PROMPT_TIMEOUT, 'passphrase prompt cancelled or timed out', err);
        throw this._lastError;
      }
    }

    // We only dial the final (target) leg here in v1 — ProxyJump
    // streaming is wired in Phase 3 via port-forwarder.js. The hop
    // configs are retained in `built.configs[0..n-1]` for that step.
    const targetConfig = built.configs[built.configs.length - 1];

    // Transition to `authenticating` on the very first PDU the server
    // sends. ssh2 exposes this via the `banner` and `keyboard-interactive`
    // events; we take the conservative approach and flip right after
    // calling connect() but before ready. (The test-harness clientFactory
    // may skip this step entirely.)

    await new Promise((resolve, reject) => {
      const client = new ClientCtor();
      this._client = client;

      let settled = false;
      const settle = (fn) => { if (!settled) { settled = true; fn(); } };

      const handshakeTimer = setTimeout(() => {
        settle(() => {
          this._fail(ERR.HANDSHAKE_ERROR, `handshake timed out after ${this._handshakeTimeoutMs}ms`);
          try { client.end(); } catch (_e) { /* ignore */ }
          reject(this._lastError);
        });
      }, this._handshakeTimeoutMs);

      client.on('banner', (msg) => {
        try { this.emit('banner', { text: String(msg || '') }); } catch (_e) { /* swallow */ }
      });

      client.on('keyboard-interactive', (_name, _instr, _lang, prompts, finish) => {
        // Forward each prompt to the renderer in sequence.
        (async () => {
          const responses = [];
          for (const p of (Array.isArray(prompts) ? prompts : [])) {
            try {
              const value = await this._promptAuth('2fa', {
                text: String(p && p.prompt || ''),
                echo: Boolean(p && p.echo),
              });
              responses.push(String(value || ''));
            } catch (err) {
              try { finish([]); } catch (_e) { /* ignore */ }
              return;
            }
          }
          try { finish(responses); } catch (_e) { /* ignore */ }
        })();
        if (this._state === STATES.CONNECTING) {
          this._transition(STATES.AUTHENTICATING, 'keyboard-interactive');
        }
      });

      client.on('error', (err) => {
        // Track auth failures for StopPolicy.
        const code = err && (err.level === 'client-authentication' ? ERR.AUTH_FAILED : ERR.CLIENT_ERROR);
        if (code === ERR.AUTH_FAILED) {
          this._authFailures.push(Date.now());
        }
        settle(() => {
          clearTimeout(handshakeTimer);
          this._fail(code, err && err.message ? err.message : 'ssh client error', err);
          reject(this._lastError);
        });
      });

      client.on('close', () => {
        this._client = null;
        settle(() => {
          clearTimeout(handshakeTimer);
          // If not already failed, and we were mid-handshake, mark failed.
          if (this._state === STATES.CONNECTING || this._state === STATES.AUTHENTICATING) {
            this._fail(ERR.CONNECTION_CLOSED, 'connection closed before handshake completed');
            reject(this._lastError);
          } else {
            this.emit('close');
          }
        });
      });

      client.on('ready', () => {
        settle(() => {
          clearTimeout(handshakeTimer);
          if (this._state === STATES.CONNECTING) {
            this._transition(STATES.AUTHENTICATING, 'ready-before-kbd');
          }
          if (this._state === STATES.AUTHENTICATING) {
            if (!this._transition(STATES.PROVISIONING, 'ready')) {
              reject(this._lastError || { code: ERR.INVALID_TRANSITION });
              return;
            }
          }
          this.emit('ready');
          resolve();
        });
      });

      try {
        client.connect(targetConfig);
      } catch (err) {
        settle(() => {
          clearTimeout(handshakeTimer);
          this._fail(ERR.HANDSHAKE_ERROR, err && err.message ? err.message : 'client.connect() threw', err);
          reject(this._lastError);
        });
      }
    });
  }

  /**
   * Mark the provisioner as finished — callers call this after Phase 4
   * finishes setting up the remote ai_engine, to move through
   * `provisioning → forwarding` and ultimately to `connected`.
   * Kept here (rather than in the provisioner) because state is owned
   * by the session.
   *
   * @param {Object} [payload] Forwarder info (localPort, remotePort).
   */
  markProvisioned(payload) {
    if (this._state !== STATES.PROVISIONING) return false;
    if (!this._transition(STATES.FORWARDING, 'provisioned')) return false;
    try {
      logger.info('remote-session-provisioned', { alias: this._target.alias, payload: payload || null });
    } catch (_e) { /* never fail on logging */ }
    return true;
  }

  /**
   * Port forwarder is up — final leg to `connected`.
   */
  markForwarded() {
    if (this._state !== STATES.FORWARDING) return false;
    return this._transition(STATES.CONNECTED, 'forwarded');
  }

  /**
   * Host-key verdict callback fed by the ssh-client-builder. Surfaces
   * a TOFU prompt on `unknown`, fails the session on `mismatch`.
   *
   * @private
   * @param {{status:string, fingerprint:string|null, storedFingerprint?:string}} verdict
   */
  _handleHostKeyVerdict(verdict) {
    if (!verdict || typeof verdict !== 'object') return;
    if (verdict.status === 'ok') return;
    if (verdict.status === 'mismatch') {
      try {
        logger.warn('remote-session-host-key-mismatch', {
          alias: this._target.alias,
          fingerprint: verdict.fingerprint,
          storedFingerprint: verdict.storedFingerprint || null,
        });
      } catch (_e) { /* ignore */ }
      // Schedule a fail on the next microtask so the ssh2 hostVerifier
      // can still return false synchronously.
      setImmediate(() => this._fail(ERR.HOST_KEY_MISMATCH, 'Host key changed — refusing connection.'));
      return;
    }
    // 'unknown' — TOFU. We don't have a synchronous way to wait here
    // because the verifier callback has already returned false; the
    // session caller must retry after `host-key-prompt` resolves.
    this.emit('host-key-prompt', {
      alias: this._target.alias,
      host: this._target.hostName || this._target.alias,
      port: Number(this._target.port) || 22,
      fingerprint: verdict.fingerprint,
    });
  }

  /**
   * Close the session and move to `disconnected` (or `failed` when a
   * reason is supplied). Safe to call from any state.
   *
   * @param {string=} reason
   */
  async close(reason) {
    this._closed = true;
    this._rejectAllPrompts(reason || 'closed');

    if (this._client) {
      try { this._client.end(); } catch (_e) { /* ignore */ }
      this._client = null;
    }

    if (this._state === STATES.FAILED) {
      this._transition(STATES.DISCONNECTED, reason || 'close-after-fail');
    } else if (this._state !== STATES.DISCONNECTED) {
      // Valid from connected / forwarding / provisioning / auth / connecting.
      // `provisioning` and `forwarding` can only transition to `failed`,
      // so we flip through `failed` when closing mid-setup.
      if (!ALLOWED_TRANSITIONS[this._state].includes(STATES.DISCONNECTED)) {
        this._transition(STATES.FAILED, reason || 'close-mid-setup');
      }
      this._transition(STATES.DISCONNECTED, reason || 'close');
    }
  }

  /**
   * Reset the auth-failure counter — called by the session manager
   * after a successful reconnect so the user is not permanently
   * blocked.
   */
  resetAuthFailures() { this._authFailures.length = 0; }

  /**
   * Mark the session as failed and emit the structured error.
   * @private
   * @param {string} code
   * @param {string} message
   * @param {Error=} cause
   */
  _fail(code, message, cause) {
    const err = { code, message, cause: cause ? { name: cause.name, message: cause.message } : undefined };
    this._lastError = err;
    if (this._state !== STATES.FAILED) this._transition(STATES.FAILED, code);
    try { logger.error('remote-session-fail', { alias: this._target.alias, code, message }); } catch (_e) { /* ignore */ }
    this.emit('error', err);
  }
}

module.exports = {
  RemoteSession,
  STATES,
  ALLOWED_TRANSITIONS,
  isValidTransition,
  ERR,
};
