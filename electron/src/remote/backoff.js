'use strict';
/**
 * Exponential backoff with cap for reconnect attempts.
 *
 * Feature: remote-ssh
 * Covers Requirements: 8.3, 8.6
 *
 * Formula (design.md §Architecture, Correctness Property 14):
 *     backoffMs(n) = min(2000 * 2^n, 30000)
 *
 * where `n` is the zero-indexed attempt counter (0 on the first retry
 * after a drop, 1 on the second, etc.). The sequence is:
 *     n=0 →  2000 ms
 *     n=1 →  4000 ms
 *     n=2 →  8000 ms
 *     n=3 → 16000 ms
 *     n=4 → 30000 ms (capped)
 *     n≥4 → 30000 ms (stays capped forever)
 *
 * This module is a single pure function plus a few constants exported
 * for callers (reconnect-loop.js in Phase 7) and tests (Property 14,
 * `tests/unit/remote/backoff.property.test.js`).
 *
 * Non-goals:
 *  - No jitter. The current spec does not request it; adding jitter
 *    here would change Property 14's invariant. If callers want
 *    jitter, they should decorate the output themselves.
 *  - No state. The caller is responsible for tracking `n` and
 *    resetting it on a successful reconnect.
 */

/** Base delay in milliseconds (n=0). */
const BASE_MS = 2000;

/** Hard cap on the returned delay. */
const CAP_MS = 30 * 1000;

/**
 * Bit-width at which `2 << n` overflows a signed 32-bit int; using
 * this as an explicit guard lets us avoid `Number.MAX_SAFE_INTEGER`
 * arithmetic for extreme `n` values.
 */
const SHIFT_LIMIT = 30;

/**
 * Compute the backoff delay in milliseconds for attempt `n`.
 *
 *   - `n < 0` → clamped to 0.
 *   - Non-integer `n` → floored (1.7 behaves like 1).
 *   - NaN / null / undefined / non-number → treated as 0.
 *   - Result is always an integer in `[BASE_MS, CAP_MS]`.
 *
 * @param {number} n Zero-indexed attempt counter.
 * @returns {number} Delay in milliseconds.
 */
function backoffMs(n) {
  const parsed = Number(n);
  const attempt = Number.isFinite(parsed) ? Math.max(0, Math.floor(parsed)) : 0;

  // Short-circuit: once we exceed the shift limit, the uncapped value
  // overflows JS number precision (well beyond CAP_MS anyway), so the
  // cap is authoritative. This also prevents `2 ** n` from returning
  // Infinity and therefore `min(Infinity, CAP_MS) === CAP_MS` — which
  // is correct, but explicit is better.
  if (attempt >= SHIFT_LIMIT) return CAP_MS;

  // 2000 * 2^n: use `Math.pow` rather than bit shift so the result
  // is an ordinary JS number (bit shifts coerce to int32 and lose
  // precision for n≥30).
  const raw = BASE_MS * Math.pow(2, attempt);
  if (raw >= CAP_MS) return CAP_MS;
  return raw;
}

module.exports = {
  backoffMs,
  BASE_MS,
  CAP_MS,
};
