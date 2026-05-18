'use strict';
/**
 * ReconnectLoop — exponential backoff reconnection with a 5-minute budget.
 *
 * Feature: remote-ssh
 * Covers Requirements: 8.3, 8.4, 8.5, 8.6, 8.7
 *
 * When a Remote_Session transitions to `reconnecting` (3 consecutive
 * keepalive failures), this loop takes over:
 *   1. Waits `backoffFn(attempt)` (2s, 4s, 8s, 16s, 30s cap).
 *   2. Attempts `session.reconnect()`.
 *   3. On success: keeps same forwarded port (Req 8.4), emits
 *      'reconnected', drains requestQueue to replay queued requests.
 *   4. On failure: increments attempt, loops back to step 1.
 *   5. If total elapsed time exceeds `budgetMs` (default 5 min):
 *      emits 'exhausted' and transitions session to `failed`.
 *
 * Emits:
 *   - 'attempt'                    {attempt, delayMs}
 *   - 'reconnected'               {alias, localPort, attempts}
 *   - 'exhausted'                  {alias, attempts, elapsedMs}
 *   - 'terminal-reattach-needed'   {terminalIds}
 */

const { EventEmitter } = require('events');
const { backoffMs } = require('./backoff');

/** Default reconnection budget: 5 minutes (Requirement 8.6). */
const DEFAULT_BUDGET_MS = 5 * 60 * 1000; // 300000

class ReconnectLoop extends EventEmitter {
  /**
   * @param {Object} session  The RemoteSession instance (must have
   *   `reconnect()`, `hostEntry.alias`, `setState(state, reason)`,
   *   and port forwarder access).
   * @param {Object} [opts]
   * @param {Function} [opts.backoffFn]       Override backoff function (for testing).
   * @param {number}   [opts.budgetMs=300000] Total time budget in ms.
   * @param {Object}   [opts.portForwarder]   Port forwarder instance (keeps same port across reconnects).
   * @param {Object}   [opts.requestQueue]    RequestQueue instance for draining on success.
   */
  constructor(session, opts) {
    super();
    if (!session || typeof session !== 'object') {
      throw new TypeError('ReconnectLoop: session is required');
    }

    const options = opts || {};

    /** @private */ this._session = session;
    /** @private */ this._backoffFn = typeof options.backoffFn === 'function'
      ? options.backoffFn
      : backoffMs;
    /** @private */ this._budgetMs = Number.isFinite(options.budgetMs) && options.budgetMs > 0
      ? options.budgetMs
      : DEFAULT_BUDGET_MS;
    /** @private */ this._portForwarder = options.portForwarder || null;
    /** @private */ this._requestQueue = options.requestQueue || null;
    /** @private */ this._attempt = 0;
    /** @private */ this._startedAt = 0;
    /** @private */ this._timer = null;
    /** @private */ this._running = false;
    /** @private */ this._stopped = false;
  }

  /**
   * Whether the loop is currently running.
   * @returns {boolean}
   */
  get isRunning() {
    return this._running;
  }

  /**
   * Begin reconnect attempts. Returns when either:
   *   - Reconnection succeeds (emits 'reconnected')
   *   - Budget is exceeded (emits 'exhausted')
   *   - stop() is called externally
   *
   * @returns {Promise<{reconnected: boolean, attempts: number}>}
   */
  async start() {
    if (this._running) {
      return { reconnected: false, attempts: this._attempt };
    }

    this._running = true;
    this._stopped = false;
    this._attempt = 0;
    this._startedAt = Date.now();

    try {
      while (!this._stopped) {
        // Check budget before waiting
        const elapsed = Date.now() - this._startedAt;
        if (elapsed >= this._budgetMs) {
          this._emitExhausted(elapsed);
          return { reconnected: false, attempts: this._attempt };
        }

        // Compute delay for this attempt
        const delayMs = this._backoffFn(this._attempt);
        this.emit('attempt', { attempt: this._attempt, delayMs });

        // Wait the backoff delay
        await this._sleep(delayMs);
        if (this._stopped) break;

        // Check budget again after sleeping
        const elapsedAfterSleep = Date.now() - this._startedAt;
        if (elapsedAfterSleep >= this._budgetMs) {
          this._emitExhausted(elapsedAfterSleep);
          return { reconnected: false, attempts: this._attempt };
        }

        // Attempt reconnection
        this._attempt += 1;
        try {
          const result = await this._session.reconnect();
          if (result !== false) {
            // Success — reconnected
            return this._handleSuccess();
          }
        } catch (_err) {
          // Reconnect failed — continue loop
        }
      }

      // Stopped externally
      return { reconnected: false, attempts: this._attempt };
    } finally {
      this._running = false;
    }
  }

  /**
   * Cancel the reconnect loop. Any pending sleep is interrupted.
   */
  stop() {
    this._stopped = true;
    if (this._timer) {
      clearTimeout(this._timer);
      this._timer = null;
    }
    this._running = false;
  }

  /**
   * Handle successful reconnection:
   *   - Port stability: keep same forwarded port (Req 8.4)
   *   - Emit 'terminal-reattach-needed' with detached terminal IDs
   *   - Drain request queue (Req 8.7)
   *   - Emit 'reconnected'
   *
   * @returns {{reconnected: boolean, attempts: number}}
   * @private
   */
  _handleSuccess() {
    const alias = this._session.hostEntry
      ? this._session.hostEntry.alias
      : null;

    // Port stability: the portForwarder keeps the same local port
    // across reconnects (Req 8.4). We just read the current port.
    const localPort = this._portForwarder && typeof this._portForwarder.localPort !== 'undefined'
      ? this._portForwarder.localPort
      : (this._session.getLocalForwardedPort ? this._session.getLocalForwardedPort() : null);

    // Terminal reattach: v1 emits event with list of detached terminal IDs.
    // The caller (RemoteTerminalBridge) provides the IDs; we get them from session.
    const terminalIds = this._session.getDetachedTerminalIds
      ? this._session.getDetachedTerminalIds()
      : [];
    this.emit('terminal-reattach-needed', { terminalIds });

    // Drain request queue — replay queued requests FIFO
    if (this._requestQueue && typeof this._requestQueue.drain === 'function') {
      this._requestQueue.drain((req) => {
        if (this._session.sendRequest) {
          this._session.sendRequest(req);
        }
      });
    }

    // Emit reconnected
    this.emit('reconnected', { alias, localPort, attempts: this._attempt });

    return { reconnected: true, attempts: this._attempt };
  }

  /**
   * Sleep for the given duration, interruptible by stop().
   * @param {number} ms
   * @returns {Promise<void>}
   * @private
   */
  _sleep(ms) {
    return new Promise((resolve) => {
      this._timer = setTimeout(() => {
        this._timer = null;
        resolve();
      }, ms);
    });
  }

  /**
   * Emit the 'exhausted' event and transition session to 'failed'.
   * @param {number} elapsedMs
   * @private
   */
  _emitExhausted(elapsedMs) {
    const alias = this._session.hostEntry
      ? this._session.hostEntry.alias
      : null;

    this.emit('exhausted', {
      alias,
      attempts: this._attempt,
      elapsedMs,
    });

    // Transition session to failed state (Req 8.6)
    if (this._session.setState) {
      this._session.setState('failed', 'reconnect budget exhausted');
    }
  }
}

module.exports = { ReconnectLoop, DEFAULT_BUDGET_MS };
