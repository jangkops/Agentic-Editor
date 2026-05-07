'use strict';
/**
 * Keepalive policy — detects silently dead SSH sessions.
 *
 * Feature: remote-ssh
 * Covers Requirements: 8.1, 8.2
 *
 * OpenSSH default `ServerAliveInterval`/`ServerAliveCountMax` semantics:
 * periodically send a SSH_MSG_IGNORE (or global request) to the peer;
 * if the reply does not arrive within the interval, count a miss. After
 * `CountMax` consecutive misses, assume the connection is dead and
 * trigger a reconnect.
 *
 * This file owns ONLY the policy logic (pure functions + a thin stateful
 * object). Wiring into the ssh2 Client is the caller's job; the pattern
 * is exactly what the existing `remote-session.js` + `remote-session-manager.js`
 * expect:
 *
 *   const k = new KeepalivePolicy({intervalMs: 30000, failureThreshold: 3});
 *   const timer = setInterval(() => {
 *     k.notifySent();
 *     client.ping((err) => err ? k.notifyFailure() : k.notifySuccess());
 *     if (k.shouldReconnect()) { clearInterval(timer); trigger(); }
 *   }, k.intervalMs);
 *
 * Design principles:
 *  - No timers inside the class. The caller schedules the ping; we just
 *    count the results. Unit tests never sleep.
 *  - Integer math, monotonic counters. Any non-number inputs are ignored.
 *  - `reset()` is idempotent — safe to call from the manager's "session
 *    is healthy again" handler without guarding on state.
 *  - Thresholds enforce sane bounds (intervalMs ≤ 30000 per Req 8.1,
 *    failureThreshold ≥ 1) by clamping, not by throwing. A misconfigured
 *    caller still gets a working session.
 */

/** Max keepalive interval per Req 8.1 (30s). */
const MAX_INTERVAL_MS = 30 * 1000;

/** Min keepalive interval — 1s floor so a typo (0) cannot busy-loop. */
const MIN_INTERVAL_MS = 1000;

/** Default failure threshold per design.md §Architecture (3 consecutive misses). */
const DEFAULT_FAILURE_THRESHOLD = 3;

/** Sensible default interval (20s) — well under the 30s cap per Req 8.1. */
const DEFAULT_INTERVAL_MS = 20 * 1000;

/**
 * Clamp `value` into `[lo, hi]`. Returns `fallback` if `value` is not
 * a finite number.
 * @private
 */
function clamp(value, lo, hi, fallback) {
  const n = Number(value);
  if (!Number.isFinite(n)) return fallback;
  if (n < lo) return lo;
  if (n > hi) return hi;
  return n;
}

class KeepalivePolicy {
  /**
   * @param {Object} [opts]
   * @param {number} [opts.intervalMs]       Ping cadence (clamped to [1s, 30s]).
   * @param {number} [opts.failureThreshold] Consecutive misses → reconnect.
   */
  constructor(opts) {
    const options = opts || {};
    /** @readonly */
    this.intervalMs = clamp(options.intervalMs, MIN_INTERVAL_MS, MAX_INTERVAL_MS, DEFAULT_INTERVAL_MS);
    /** @readonly */
    this.failureThreshold = Math.max(1, Math.floor(
      Number.isFinite(Number(options.failureThreshold))
        ? Number(options.failureThreshold)
        : DEFAULT_FAILURE_THRESHOLD,
    ));
    /** @private */ this._consecutiveFailures = 0;
    /** @private */ this._sent = 0;
    /** @private */ this._succeeded = 0;
    /** @private */ this._failed = 0;
    /** @private */ this._lastSuccessAt = 0;
    /** @private */ this._lastFailureAt = 0;
  }

  /**
   * Record that a keepalive probe was sent. Used for telemetry and
   * sanity checks (`sent ≥ succeeded + failed` must always hold).
   */
  notifySent() { this._sent += 1; }

  /**
   * Record a successful keepalive reply. Resets the consecutive-miss
   * counter so transient failures do not accumulate across recoveries.
   */
  notifySuccess() {
    this._succeeded += 1;
    this._consecutiveFailures = 0;
    this._lastSuccessAt = Date.now();
  }

  /**
   * Record a keepalive failure (timeout or error reply). Increments the
   * consecutive-miss counter.
   */
  notifyFailure() {
    this._failed += 1;
    this._consecutiveFailures += 1;
    this._lastFailureAt = Date.now();
  }

  /**
   * True iff consecutive failures have reached the configured threshold.
   * Callers should trigger a reconnect and then `reset()` the policy
   * after the new session is up.
   *
   * @returns {boolean}
   */
  shouldReconnect() {
    return this._consecutiveFailures >= this.failureThreshold;
  }

  /**
   * Number of consecutive failures since the last success. Exposed for
   * UI state-bar diagnostics ("degraded" warning at N-1 misses).
   *
   * @returns {number}
   */
  get consecutiveFailures() { return this._consecutiveFailures; }

  /** Total pings attempted. */
  get sentCount() { return this._sent; }
  /** Total successful replies. */
  get successCount() { return this._succeeded; }
  /** Total failures / timeouts. */
  get failureCount() { return this._failed; }

  /**
   * Clear the failure counter. Call this from the reconnect handler
   * after a fresh session reaches `connected`. Does NOT zero the
   * telemetry counters — those are useful across reconnects for
   * historical debugging.
   */
  reset() { this._consecutiveFailures = 0; }

  /**
   * Stateless decision helper — useful in tests and callers that keep
   * their own counter outside the class.
   *
   * @param {number} consecutiveFailures
   * @param {number} threshold
   * @returns {boolean}
   */
  static shouldReconnectAt(consecutiveFailures, threshold) {
    const c = Number(consecutiveFailures);
    const t = Number(threshold);
    if (!Number.isFinite(c) || !Number.isFinite(t) || t < 1) return false;
    return Math.floor(c) >= Math.floor(t);
  }
}

module.exports = {
  KeepalivePolicy,
  MAX_INTERVAL_MS,
  MIN_INTERVAL_MS,
  DEFAULT_FAILURE_THRESHOLD,
  DEFAULT_INTERVAL_MS,
};
