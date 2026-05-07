'use strict';
/**
 * RemoteSessionManager — owns the set of RemoteSession instances and
 * the single "active" session that renderer IPC calls should route to.
 *
 * Feature: remote-ssh
 * Covers Requirements: 9.1, 9.2, 9.4, 9.5, 10.2
 *
 * Invariants (see design.md §Architecture and Correctness Property 17):
 *
 *   - At most MAX_SESSIONS live sessions simultaneously.
 *   - At most one session is marked active at any time.
 *   - The active session (if any) is always a member of the session set.
 *   - `disconnect(alias)` calls `credentialCache.clear(alias)` so that
 *     logging out forgets cached passphrases / 2FA responses (Req 10.2).
 *   - `switchActive(alias)` is O(1) relative to session count and does
 *     NOT tear down the old session (supports fast A↔B swap per Req 9.3).
 *   - Local fallback (no active session) is represented by `active=null`;
 *     the SessionRouter (Phase 5) uses this to route back to the local
 *     filesystem / terminal stack.
 *
 * Lifecycle events (EventEmitter on the manager instance):
 *
 *   'session-added'    {alias}                 — after `connect()` resolved the session exists.
 *   'session-removed'  {alias, reason}         — after `disconnect()` finished teardown.
 *   'active-changed'   {previous, current}     — whenever the active alias changes.
 *   'state'            {alias, from, to, reason?} — proxied from each child session.
 *   'error'            {alias, error}          — proxied from each child session.
 *
 * Non-goals:
 *   - No reconnect loop (Phase 7 / `reconnect-loop.js` owns that and
 *     attaches itself to each session after `connect()` resolves).
 *   - No request queueing (Phase 7 / `request-queue.js`).
 *   - No UI watcher-pause hook wiring — we expose `.pauseWatchers()` /
 *     `.resumeWatchers()` on the session interface; Phase 3's SFTP
 *     watcher implements those hooks. The manager just calls them when
 *     the active session changes.
 */

const { EventEmitter } = require('events');

const credentialCache = require('./credential-cache');
const logger = require('./logger');
const { RemoteSession, STATES: SESSION_STATES } = require('./remote-session');

/** Maximum concurrent sessions. Mirrors design.md §Architecture. */
const MAX_SESSIONS = 4;

/**
 * Error codes surfaced to callers. The UI error-surface layer
 * (Phase 6 / `error-surface.js`) maps these to user strings.
 */
const MGR_ERR = Object.freeze({
  TOO_MANY_SESSIONS: 'too-many-sessions',
  ALREADY_EXISTS: 'session-already-exists',
  UNKNOWN_ALIAS: 'unknown-alias',
  NOT_CONNECTED: 'alias-not-connected',
  INVALID_ARG: 'invalid-argument',
});

/**
 * Light-weight structural check for the RemoteSession shape. We do not
 * `instanceof`-check so that the manager works with duck-typed test
 * doubles (used by session-manager.property.test.js).
 *
 * @param {*} session
 * @returns {boolean}
 */
function looksLikeSession(session) {
  return (
    !!session
    && typeof session === 'object'
    && typeof session.alias === 'string'
    && typeof session.connect === 'function'
    && typeof session.close === 'function'
    && typeof session.on === 'function'
  );
}

class RemoteSessionManager extends EventEmitter {
  /**
   * @param {Object} [opts]
   * @param {Function=} opts.sessionFactory
   *    Test hook: `(target, hops, sessionOpts) => RemoteSession-like`.
   *    Defaults to `new RemoteSession({target, hops, ...sessionOpts})`.
   * @param {number=} opts.maxSessions Override the MAX_SESSIONS cap.
   */
  constructor(opts) {
    super();
    const options = opts || {};
    /** @private */
    this._sessionFactory = typeof options.sessionFactory === 'function'
      ? options.sessionFactory
      : (target, hops, sessionOpts) => new RemoteSession({ target, hops, ...sessionOpts });
    /** @private */
    this._maxSessions = Number.isInteger(options.maxSessions) && options.maxSessions > 0
      ? options.maxSessions
      : MAX_SESSIONS;
    /** @private @type {Map<string, Object>} */
    this._sessions = new Map();
    /** @private @type {string|null} */
    this._activeAlias = null;
  }

  // ---------------------------------------------------------------------
  // Inventory / accessors
  // ---------------------------------------------------------------------

  /**
   * Number of live sessions (connected or attempting to connect).
   * @returns {number}
   */
  size() { return this._sessions.size; }

  /**
   * Max concurrent sessions allowed. Exposed so the UI can disable
   * the "Connect" button when the pool is full.
   * @returns {number}
   */
  get maxSessions() { return this._maxSessions; }

  /**
   * List of `{alias, state, endpoint, isActive}` summaries for the
   * Host_Picker and Status_Bar components.
   * @returns {Array<{alias:string, state:string, endpoint:Object, isActive:boolean}>}
   */
  all() {
    const out = [];
    for (const [alias, session] of this._sessions) {
      out.push({
        alias,
        state: session.state,
        endpoint: typeof session.endpoint === 'object' ? session.endpoint : null,
        isActive: alias === this._activeAlias,
      });
    }
    return out;
  }

  /**
   * Get a session by alias, or `null` if absent.
   * @param {string} alias
   * @returns {Object|null}
   */
  get(alias) {
    if (typeof alias !== 'string' || alias.length === 0) return null;
    return this._sessions.get(alias) || null;
  }

  /**
   * The currently active session, or `null` when routing to local.
   * @returns {Object|null}
   */
  getActive() {
    if (this._activeAlias === null) return null;
    return this._sessions.get(this._activeAlias) || null;
  }

  /**
   * Alias of the currently active session, or `null` when routing to
   * local. Cheap helper for the UI status bar.
   * @returns {string|null}
   */
  getActiveAlias() { return this._activeAlias; }

  /**
   * True iff the active session exists and is in the `connected` state.
   * Callers (SessionRouter) use this to decide remote-vs-local dispatch.
   * @returns {boolean}
   */
  isRemoteActive() {
    const active = this.getActive();
    return !!(active && active.state === SESSION_STATES.CONNECTED);
  }

  // ---------------------------------------------------------------------
  // Session lifecycle
  // ---------------------------------------------------------------------

  /**
   * Create a new session and kick off the SSH handshake.
   *
   * Returns the session instance on success. The instance is registered
   * and proxied for state/error events regardless of whether
   * `connect()` eventually resolves; callers that want to await the
   * handshake should `await session.connect()` themselves (which this
   * method does for them by default, surfacing failures as errors).
   *
   * On exception the partially-registered session is torn down so the
   * manager never leaks entries for aliases that failed to dial.
   *
   * @param {string} alias           Unique alias used as the session key.
   * @param {Object} target          Resolved HostEntry (from ssh-config-parser).
   * @param {Object[]=} hops         Optional ProxyJump hops (resolved HostEntries).
   * @param {Object=} sessionOpts    Forwarded to the RemoteSession constructor.
   * @returns {Promise<Object>}      The session instance.
   */
  async connect(alias, target, hops, sessionOpts) {
    if (typeof alias !== 'string' || alias.length === 0) {
      throw Object.assign(new Error('alias must be a non-empty string'), { code: MGR_ERR.INVALID_ARG });
    }
    if (!target || typeof target !== 'object') {
      throw Object.assign(new Error('target HostEntry is required'), { code: MGR_ERR.INVALID_ARG });
    }
    if (this._sessions.has(alias)) {
      throw Object.assign(new Error(`session already exists: ${alias}`), { code: MGR_ERR.ALREADY_EXISTS });
    }
    if (this._sessions.size >= this._maxSessions) {
      throw Object.assign(
        new Error(`already at max ${this._maxSessions} sessions`),
        { code: MGR_ERR.TOO_MANY_SESSIONS },
      );
    }

    const session = this._sessionFactory(target, Array.isArray(hops) ? hops : [], sessionOpts || {});
    if (!looksLikeSession(session)) {
      throw Object.assign(
        new Error('sessionFactory returned an invalid session instance'),
        { code: MGR_ERR.INVALID_ARG },
      );
    }
    // If the test double exposes a different alias than we were asked
    // to register under, trust the manager's alias — the map key must
    // match what callers pass to get()/disconnect()/switchActive().
    this._wireEventProxies(alias, session);
    this._sessions.set(alias, session);
    this.emit('session-added', { alias });
    try {
      logger.info('remote-session-manager-added', {
        alias,
        count: this._sessions.size,
      });
    } catch (_e) { /* never fail on logging */ }

    // Promote to active if nothing else is active — UX: first connect
    // becomes the routing target immediately (Req 9.1).
    if (this._activeAlias === null) this._setActive(alias, 'auto-on-first-connect');

    try {
      await session.connect();
      return session;
    } catch (err) {
      // Dial failed — roll back the registration so the map does not
      // accumulate dead sessions. Failure bookkeeping (logs, UI) flows
      // through the session's own 'error' event which we already
      // proxied above.
      await this._removeSilent(alias, 'connect-failed');
      throw err;
    }
  }

  /**
   * Tear down and remove a session. Safe to call from any state. Also
   * clears cached credentials for that alias so logging out is a full
   * wipe (Req 10.2).
   *
   * Promotes another session (if any) to active when the disconnected
   * one was the active one.
   *
   * @param {string} alias
   * @param {string=} reason
   * @returns {Promise<boolean>} `true` if a session was removed.
   */
  async disconnect(alias, reason) {
    const session = this._sessions.get(alias);
    if (!session) return false;
    try {
      await session.close(reason || 'user-disconnect');
    } catch (_e) { /* swallow — close() is best effort */ }

    // Credentials wipe happens regardless of whether close() threw.
    try { credentialCache.clear(alias); } catch (_e) { /* ignore */ }

    this._sessions.delete(alias);
    this.emit('session-removed', { alias, reason: reason || 'user-disconnect' });
    try { logger.info('remote-session-manager-removed', { alias, reason: reason || null, count: this._sessions.size }); }
    catch (_e) { /* ignore */ }

    if (this._activeAlias === alias) {
      // Promote the next session, if any, else clear active.
      const nextAlias = this._pickPromotableAlias();
      this._setActive(nextAlias, 'active-disconnected');
    }
    return true;
  }

  /**
   * Internal helper to delete a session without running close() — used
   * by `connect()`'s failure rollback so we do not double-close a
   * session that already emitted 'error'.
   *
   * @private
   * @param {string} alias
   * @param {string} reason
   */
  async _removeSilent(alias, reason) {
    if (!this._sessions.has(alias)) return;
    const session = this._sessions.get(alias);
    // Belt-and-braces close in case the session is still half-alive.
    try { await session.close(reason); } catch (_e) { /* ignore */ }
    try { credentialCache.clear(alias); } catch (_e) { /* ignore */ }
    this._sessions.delete(alias);
    this.emit('session-removed', { alias, reason });
    if (this._activeAlias === alias) {
      this._setActive(this._pickPromotableAlias(), reason);
    }
  }

  /**
   * Switch the active routing target to another session. The new
   * target must exist and be in the `connected` state — otherwise
   * we throw so the UI can show a useful error instead of silently
   * routing to a half-dialed session.
   *
   * To go back to local routing, pass `null`.
   *
   * @param {string|null} alias
   */
  switchActive(alias) {
    if (alias === null) {
      this._setActive(null, 'user-switch-to-local');
      return;
    }
    if (typeof alias !== 'string' || alias.length === 0) {
      throw Object.assign(new Error('alias must be a non-empty string or null'), { code: MGR_ERR.INVALID_ARG });
    }
    const session = this._sessions.get(alias);
    if (!session) {
      throw Object.assign(new Error(`unknown alias: ${alias}`), { code: MGR_ERR.UNKNOWN_ALIAS });
    }
    if (session.state !== SESSION_STATES.CONNECTED) {
      throw Object.assign(
        new Error(`alias ${alias} is not connected (state=${session.state})`),
        { code: MGR_ERR.NOT_CONNECTED },
      );
    }
    this._setActive(alias, 'user-switch');
  }

  // ---------------------------------------------------------------------
  // Private helpers
  // ---------------------------------------------------------------------

  /**
   * Mutate `_activeAlias` and fire the `active-changed` event. Also
   * invokes the optional `pauseWatchers()` / `resumeWatchers()` hooks
   * on the involved sessions so SFTP watchers (Phase 3) throttle
   * themselves when inactive.
   *
   * Idempotent: setting the same alias is a no-op.
   *
   * @private
   * @param {string|null} alias
   * @param {string} reason
   */
  _setActive(alias, reason) {
    if (this._activeAlias === alias) return;
    const previous = this._activeAlias;
    this._activeAlias = alias;

    if (previous !== null) {
      const prevSession = this._sessions.get(previous);
      if (prevSession && typeof prevSession.pauseWatchers === 'function') {
        try { prevSession.pauseWatchers(); } catch (_e) { /* ignore */ }
      }
    }
    if (alias !== null) {
      const nextSession = this._sessions.get(alias);
      if (nextSession && typeof nextSession.resumeWatchers === 'function') {
        try { nextSession.resumeWatchers(); } catch (_e) { /* ignore */ }
      }
    }

    this.emit('active-changed', { previous, current: alias, reason });
    try { logger.info('remote-session-manager-active', { previous, current: alias, reason }); }
    catch (_e) { /* ignore */ }
  }

  /**
   * Pick a remaining session to promote to active after the current
   * active one was removed. Preference order:
   *   1. Any session in `connected` state (most useful immediately).
   *   2. Any session in `reconnecting` state (will come back soon).
   *   3. `null` (fall back to local routing).
   *
   * @private
   * @returns {string|null}
   */
  _pickPromotableAlias() {
    /** @type {string|null} */
    let connectedAlias = null;
    /** @type {string|null} */
    let reconnectingAlias = null;
    for (const [alias, session] of this._sessions) {
      if (session.state === SESSION_STATES.CONNECTED) {
        connectedAlias = alias;
        break;
      }
      if (session.state === SESSION_STATES.RECONNECTING && !reconnectingAlias) {
        reconnectingAlias = alias;
      }
    }
    if (connectedAlias) return connectedAlias;
    if (reconnectingAlias) return reconnectingAlias;
    return null;
  }

  /**
   * Proxy session-level events so external listeners only need to
   * subscribe to the manager once.
   *
   * @private
   * @param {string} alias
   * @param {Object} session
   */
  _wireEventProxies(alias, session) {
    session.on('state', (evt) => {
      this.emit('state', { alias, ...evt });
    });
    session.on('error', (err) => {
      this.emit('error', { alias, error: err });
    });
    // Prompt events are re-emitted verbatim so the UI can register a
    // single listener on the manager.
    session.on('auth-prompt', (evt) => { this.emit('auth-prompt', { alias, ...evt }); });
    session.on('host-key-prompt', (evt) => { this.emit('host-key-prompt', { alias, ...evt }); });
    session.on('banner', (evt) => { this.emit('banner', { alias, ...evt }); });
  }

  /**
   * Tear down every session. Called on Electron `will-quit` so the
   * manager never leaves sockets lingering after the app closes.
   *
   * @returns {Promise<void>}
   */
  async shutdown() {
    const aliases = [...this._sessions.keys()];
    for (const alias of aliases) {
      try { await this.disconnect(alias, 'manager-shutdown'); }
      catch (_e) { /* ignore */ }
    }
    // Final belt-and-braces wipe.
    try { credentialCache.clear(); } catch (_e) { /* ignore */ }
  }
}

module.exports = {
  RemoteSessionManager,
  MAX_SESSIONS,
  MGR_ERR,
};
