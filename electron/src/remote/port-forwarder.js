'use strict';
/**
 * PortForwarder — local TCP listener that tunnels every accepted
 * connection through a `RemoteSession` to a remote service port.
 *
 * Feature: remote-ssh
 * Covers Requirements: 5.1, 5.2, 5.6
 *
 * Architecture (design.md §Components → port-forwarder.js):
 *
 *   renderer fetch('http://127.0.0.1:<localPort>/health')
 *       │
 *       ▼
 *   net.createServer (this module, on 127.0.0.1:<localPort>)
 *       │  per accepted socket
 *       ▼
 *   session.client.forwardOut('127.0.0.1', srcPort,
 *                              '127.0.0.1', remotePort, cb)
 *       │
 *       ▼
 *   ssh2 channel → remote sshd → ai_engine on 127.0.0.1:8765
 *
 * Why a TCP-accept-per-channel model rather than ssh2's built-in
 * `client.forwardIn`? `forwardIn` listens on the *remote* side; we
 * want a *local* listener (i.e. `-L`). The ssh2 way to do that is to
 * accept on the local side ourselves and pipe each socket through
 * `forwardOut`. This gives us:
 *  - Direct control over the local port (so `apiBase()` knows it).
 *  - The ability to reject connections from non-loopback peers, since
 *    the FastAPI server we expose accepts no auth and must not be
 *    reachable from the LAN.
 *  - Per-connection metrics if we ever need them.
 *
 * Lifecycle invariants:
 *
 *  - `start(session)` called twice without an intervening `close()`
 *    is a no-op once the listener is up (the existing local port is
 *    reused, satisfying the "re-connection keeps same port" clause of
 *    Requirement 5.2). The new ssh2 client (post-reconnect) replaces
 *    the previous one for future tunnels.
 *  - On `close()` we destroy the local server *and* every socket the
 *    listener accepted. Otherwise xterm.js / fetch keepalive sockets
 *    leak into the next session and reuse the dead ssh2 channels,
 *    surfacing as "ECONNRESET on first request" after every reconnect.
 *  - The class never throws synchronously from `start()` after the
 *    initial argument check — it resolves with the allocated port on
 *    success or rejects with a structured `{code, message}` error.
 *
 * Property 9 (port allocator) supplies the local port. Property 10
 * (apiBase routing) consumes `getLocalPort()` indirectly through the
 * SessionRouter (Phase 5).
 *
 * Threading note: the ssh2 client's `forwardOut` callback fires on the
 * Node event loop just like our `net.Server` events, so no extra
 * synchronization is needed. We do guard against the race where the
 * local server has accepted a socket but `close()` ran before the
 * ssh2 channel callback resolved — in that case we destroy the local
 * socket and discard the channel.
 */

const net = require('net');
const { EventEmitter } = require('events');

const { allocatePort, releasePort, PortExhaustedError, DEFAULT_RANGE } = require('./port-allocator');
const logger = require('./logger');

/** Default remote host/port that ai_engine listens on (design.md). */
const DEFAULT_REMOTE_HOST = '127.0.0.1';
const DEFAULT_REMOTE_PORT = 8765;

/** Local bind address — loopback only, never expose to LAN. */
const LOCAL_BIND_HOST = '127.0.0.1';

/**
 * Error codes — consumed by the error-surface layer (Phase 6) to
 * render remediation hints. Kept out of the user-facing string so
 * translation does not change error identity.
 */
const FWD_ERR = Object.freeze({
  INVALID_SESSION: 'invalid-session',
  NO_CLIENT: 'session-has-no-client',
  PORT_EXHAUSTED: 'port-range-exhausted',
  LISTEN_FAILED: 'local-listen-failed',
  ALREADY_CLOSED: 'forwarder-closed',
});

/**
 * Validate a `RemoteSession`-shaped object. We use duck-typing rather
 * than `instanceof` so the unit tests in Phase 3 can inject a stub
 * with a hand-rolled `client.forwardOut`.
 *
 * @param {*} session
 * @returns {boolean}
 */
function looksLikeSession(session) {
  return !!session && typeof session === 'object'
    && (typeof session.alias === 'string' || session.alias === undefined);
}

/**
 * Fetch the live ssh2 Client from a session, or `null` if the session
 * has not finished connecting. We re-fetch on every accepted socket so
 * a reconnect that swaps the client is picked up automatically.
 *
 * @param {Object} session
 * @returns {*}
 */
function getClient(session) {
  if (!session) return null;
  // Preferred: `session.client` getter (RemoteSession exposes this).
  if (session.client && typeof session.client.forwardOut === 'function') {
    return session.client;
  }
  // Fallback: some test stubs expose forwardOut directly.
  if (typeof session.forwardOut === 'function') return session;
  return null;
}

/**
 * PortForwarder.
 *
 * Usage:
 *   const fwd = new PortForwarder({ remoteHost: '127.0.0.1', remotePort: 8765 });
 *   const localPort = await fwd.start(session);   // -> 18765 (or next free)
 *   // ... apiBase() now points at http://127.0.0.1:<localPort>
 *   await fwd.close();
 */
class PortForwarder extends EventEmitter {
  /**
   * @param {Object} [opts]
   * @param {string=} opts.remoteHost   Default `127.0.0.1` (ai_engine bind addr).
   * @param {number=} opts.remotePort   Default `8765` (ai_engine port).
   * @param {[number, number]=} opts.range  Local port range, default `[18765, 18865]`.
   * @param {number=} opts.preferredLocalPort
   *    Force a specific local port on first allocation (used when a
   *    persisted preference exists for the alias). Falls back to the
   *    range minimum.
   */
  constructor(opts) {
    super();
    const options = opts || {};
    /** @private */ this._remoteHost = typeof options.remoteHost === 'string' && options.remoteHost.length > 0
      ? options.remoteHost
      : DEFAULT_REMOTE_HOST;
    /** @private */ this._remotePort = Number.isInteger(options.remotePort) && options.remotePort > 0
      ? options.remotePort
      : DEFAULT_REMOTE_PORT;
    /** @private */ this._range = Array.isArray(options.range) && options.range.length === 2
      ? [Number(options.range[0]), Number(options.range[1])]
      : [DEFAULT_RANGE[0], DEFAULT_RANGE[1]];
    /** @private */ this._preferredLocalPort = Number.isInteger(options.preferredLocalPort)
      ? options.preferredLocalPort
      : null;

    /** @private @type {net.Server|null} */ this._server = null;
    /** @private @type {number|null}     */ this._localPort = null;
    /** @private @type {Object|null}     */ this._session = null;
    /** @private @type {Set<net.Socket>} */ this._sockets = new Set();
    /** @private @type {boolean}         */ this._closed = false;
    /** @private @type {Promise<number>|null} */ this._startPromise = null;
  }

  /**
   * Local port the listener is bound to. `null` until `start()`
   * resolves. Stable across reconnects: a re-`start()` after a
   * transient ssh2 disconnect reuses the previously allocated port
   * so the renderer's `apiBase()` URL never changes mid-session.
   *
   * @returns {number|null}
   */
  getLocalPort() { return this._localPort; }

  /** Convenience accessor matching design.md's `get localPort` shape. */
  get localPort() { return this._localPort; }

  /** Remote endpoint we tunnel to, for diagnostics. */
  get remoteEndpoint() { return { host: this._remoteHost, port: this._remotePort }; }

  /** True iff the local listener is up. */
  isListening() { return this._server !== null && this._localPort !== null; }

  /**
   * Open (or re-open) the local TCP listener bound to the next free
   * port in the configured range and wire its accepted sockets through
   * `session.client.forwardOut`.
   *
   * Behavior:
   *  - First call:    allocate a fresh port, bind, and begin accepting.
   *  - Subsequent call (without close): swap the bound `session` so
   *    new tunnels use the new ssh2 client; reuse the existing local
   *    port. The existing listener stays up the whole time.
   *  - After `close()`: rejects with `ALREADY_CLOSED`.
   *
   * Concurrent callers share the same in-flight promise — calling
   * `start()` from two places at once does not race two listens.
   *
   * @param {Object} session A `RemoteSession`-shaped object.
   * @returns {Promise<number>} Resolved local port.
   */
  start(session) {
    if (this._closed) {
      return Promise.reject(Object.assign(
        new Error('PortForwarder is closed; create a new instance to restart.'),
        { code: FWD_ERR.ALREADY_CLOSED },
      ));
    }
    if (!looksLikeSession(session)) {
      return Promise.reject(Object.assign(
        new Error('start() requires a RemoteSession-shaped object'),
        { code: FWD_ERR.INVALID_SESSION },
      ));
    }

    // Fast path: already listening — just swap the session reference
    // so future tunnels go through the new ssh2 client.
    if (this._server !== null && this._localPort !== null) {
      this._session = session;
      try {
        logger.info('port-forwarder-resumed', {
          alias: session && session.alias ? session.alias : null,
          localPort: this._localPort,
          remoteHost: this._remoteHost,
          remotePort: this._remotePort,
        });
      } catch (_e) { /* ignore */ }
      return Promise.resolve(this._localPort);
    }

    // De-duplicate concurrent starts.
    if (this._startPromise) return this._startPromise;

    this._session = session;
    this._startPromise = this._allocateAndListen()
      .then((port) => {
        this._localPort = port;
        try {
          logger.info('port-forwarder-started', {
            alias: session && session.alias ? session.alias : null,
            localPort: port,
            remoteHost: this._remoteHost,
            remotePort: this._remotePort,
          });
        } catch (_e) { /* ignore */ }
        return port;
      })
      .catch((err) => {
        // Allocation or listen failed — undo any partial state so a
        // retry can start clean.
        this._teardownServer();
        this._localPort = null;
        try {
          logger.error('port-forwarder-start-failed', {
            alias: session && session.alias ? session.alias : null,
            code: err && err.code ? err.code : 'unknown',
            message: err && err.message ? err.message : String(err),
          });
        } catch (_e) { /* ignore */ }
        throw err;
      })
      .finally(() => { this._startPromise = null; });

    return this._startPromise;
  }

  /**
   * Allocate a free port and bind a new `net.Server` to it. Wired into
   * `start()` only.
   *
   * @private
   * @returns {Promise<number>} The bound port.
   */
  async _allocateAndListen() {
    let port;
    try {
      port = await allocatePort({
        preferred: this._preferredLocalPort !== null ? this._preferredLocalPort : this._range[0],
        range: this._range,
      });
    } catch (err) {
      if (err instanceof PortExhaustedError) {
        throw Object.assign(
          new Error(`No free local port in range [${this._range[0]}, ${this._range[1]}]`),
          { code: FWD_ERR.PORT_EXHAUSTED, range: this._range, cause: err },
        );
      }
      throw err;
    }

    return new Promise((resolve, reject) => {
      const server = net.createServer((socket) => this._handleAccepted(socket));
      server.on('error', (err) => {
        // Surface as a structured listen failure. The race where the
        // port we allocated is taken between probe and listen is rare
        // but possible (TOCTOU); the caller can retry.
        try { server.close(); } catch (_e) { /* ignore */ }
        reject(Object.assign(
          new Error(`Local listen on 127.0.0.1:${port} failed: ${err && err.message}`),
          { code: FWD_ERR.LISTEN_FAILED, cause: err },
        ));
      });
      server.once('listening', () => {
        // Detach the constructor-time error handler in favor of a
        // logging-only one so transient errors after listen do not
        // reject `start()`'s promise (which has already resolved).
        server.removeAllListeners('error');
        server.on('error', (err) => {
          try {
            logger.warn('port-forwarder-server-error', {
              localPort: port,
              message: err && err.message ? err.message : String(err),
            });
          } catch (_e) { /* ignore */ }
        });
        this._server = server;
        resolve(port);
      });

      try {
        server.listen(port, LOCAL_BIND_HOST);
      } catch (err) {
        try { server.close(); } catch (_e) { /* ignore */ }
        reject(Object.assign(
          new Error(`Local listen.listen(${port}) threw: ${err && err.message}`),
          { code: FWD_ERR.LISTEN_FAILED, cause: err },
        ));
      }
    });
  }

  /**
   * Handle a freshly accepted local socket: open an ssh2 forwardOut
   * channel and pipe both directions. Per-socket cleanup is wired up
   * before any I/O so an early error never leaks the socket.
   *
   * @private
   * @param {net.Socket} socket
   */
  _handleAccepted(socket) {
    if (this._closed) {
      try { socket.destroy(); } catch (_e) { /* ignore */ }
      return;
    }

    // Track for global teardown on close().
    this._sockets.add(socket);
    socket.once('close', () => this._sockets.delete(socket));
    socket.on('error', (err) => {
      // Connections from fetch / xterm / etc. routinely close ungracefully
      // (e.g. abort on tab close). Log at info level.
      try {
        logger.info('port-forwarder-local-socket-error', {
          localPort: this._localPort,
          message: err && err.message ? err.message : String(err),
        });
      } catch (_e) { /* ignore */ }
    });

    const client = getClient(this._session);
    if (!client) {
      try {
        logger.warn('port-forwarder-no-client', {
          alias: this._session && this._session.alias ? this._session.alias : null,
          localPort: this._localPort,
        });
      } catch (_e) { /* ignore */ }
      try { socket.destroy(); } catch (_e) { /* ignore */ }
      return;
    }

    // Source addr/port are advisory in ssh2; remote sshd uses them
    // only for the channel-open request line. We pass the real local
    // peer info so server-side logs (sshd debug) make sense.
    const srcAddr = socket.remoteAddress || LOCAL_BIND_HOST;
    const srcPort = socket.remotePort || 0;

    let channelOpened = false;
    client.forwardOut(srcAddr, srcPort, this._remoteHost, this._remotePort, (err, channel) => {
      if (this._closed || socket.destroyed) {
        if (channel) {
          try { channel.close(); } catch (_e) { /* ignore */ }
        }
        return;
      }

      if (err || !channel) {
        try {
          logger.warn('port-forwarder-forwardout-failed', {
            alias: this._session && this._session.alias ? this._session.alias : null,
            remoteHost: this._remoteHost,
            remotePort: this._remotePort,
            message: err && err.message ? err.message : 'no-channel',
          });
        } catch (_e) { /* ignore */ }
        try { socket.destroy(); } catch (_e) { /* ignore */ }
        return;
      }

      channelOpened = true;
      // Bidirectional pipe. `pipe()` ends the destination on source
      // EOF — that's the behavior we want: half-close on either side
      // tears down the other side cleanly.
      socket.pipe(channel).pipe(socket);

      // Channel-side errors should also tear down the local socket so
      // the renderer sees ECONNRESET rather than hanging forever.
      channel.on('error', (chErr) => {
        try {
          logger.info('port-forwarder-channel-error', {
            alias: this._session && this._session.alias ? this._session.alias : null,
            message: chErr && chErr.message ? chErr.message : String(chErr),
          });
        } catch (_e) { /* ignore */ }
        try { socket.destroy(); } catch (_e) { /* ignore */ }
      });

      channel.on('close', () => {
        if (!socket.destroyed) {
          try { socket.end(); } catch (_e) { /* ignore */ }
        }
      });
    });

    // If forwardOut never invokes its callback (e.g. ssh2 client died
    // mid-flight), guarantee we eventually drop the socket so a stale
    // keepalive does not linger. The local end will close on its own
    // half-open timeout, but we hurry it along.
    socket.once('end', () => {
      if (!channelOpened) {
        try { socket.destroy(); } catch (_e) { /* ignore */ }
      }
    });
  }

  /**
   * Close the local listener and destroy every accepted socket so the
   * next session starts from a clean slate. Idempotent.
   *
   * Per design.md "close() 시 keep-alive 소켓 정리": HTTP keep-alive
   * sockets (held open by the renderer's fetch agent) are exactly
   * what we destroy here — they belong to the previous ssh2 channel
   * and would error on the next request anyway.
   *
   * @returns {Promise<void>}
   */
  close() {
    if (this._closed && this._server === null && this._sockets.size === 0) {
      return Promise.resolve();
    }
    this._closed = true;

    // Destroy active sockets first so they do not interfere with
    // `server.close()` (which waits for sockets to close on its own,
    // forever in the case of HTTP keep-alive).
    for (const socket of this._sockets) {
      try { socket.destroy(); } catch (_e) { /* ignore */ }
    }
    this._sockets.clear();

    return new Promise((resolve) => {
      if (this._server === null) {
        this._localPort = null;
        this._session = null;
        resolve();
        return;
      }
      const server = this._server;
      this._server = null;
      try {
        server.close(() => {
          if (this._localPort !== null) {
            try { releasePort(this._localPort); } catch (_e) { /* ignore */ }
          }
          try {
            logger.info('port-forwarder-closed', {
              alias: this._session && this._session.alias ? this._session.alias : null,
              localPort: this._localPort,
            });
          } catch (_e) { /* ignore */ }
          this._localPort = null;
          this._session = null;
          resolve();
        });
      } catch (_err) {
        this._localPort = null;
        this._session = null;
        resolve();
      }
    });
  }

  /**
   * Internal: tear down the server without waiting for callbacks.
   * Used on the failure path of `_allocateAndListen`.
   *
   * @private
   */
  _teardownServer() {
    if (this._server) {
      try { this._server.close(); } catch (_e) { /* ignore */ }
      this._server = null;
    }
    for (const socket of this._sockets) {
      try { socket.destroy(); } catch (_e) { /* ignore */ }
    }
    this._sockets.clear();
  }
}

module.exports = {
  PortForwarder,
  FWD_ERR,
  DEFAULT_REMOTE_HOST,
  DEFAULT_REMOTE_PORT,
};
