'use strict';
/**
 * Remote-SSH auth & security policy helpers.
 *
 * Feature: remote-ssh
 * Covers Requirements: 3.8, 13.1
 *
 * Pure functions — no I/O, no hidden state. Callers own the history
 * arrays and feed them in on each invocation so the policy is trivially
 * unit-testable and deterministic.
 *
 *  - shouldStop(timestamps, now): "are we in an auth-failure storm?"
 *  - effectiveStrictHostKeyChecking(entry, knownAliases): resolves the
 *    StrictHostKeyChecking value for a given host considering user config
 *    and whether the host has been trusted before.
 */

/** Rolling window for auth-failure storms (Req 3.8): last 60 seconds. */
const STOP_WINDOW_MS = 60 * 1000;

/** Threshold inside `STOP_WINDOW_MS` that triggers the stop verdict. */
const STOP_THRESHOLD = 3;

/**
 * Decide whether the session should stop attempting reconnects due to
 * repeated authentication failures.
 *
 * Contract:
 *  - `timestamps` is an array of epoch-ms values representing past auth
 *    failures. It MAY be unsorted, MAY contain duplicates, and MAY
 *    contain values in the future (bogus clocks). We treat everything
 *    as numeric and simply count entries falling inside the rolling
 *    window ending at `now`.
 *  - Non-finite values, `null`, `undefined`, strings, etc. are ignored.
 *  - `now` defaults to `Date.now()` but is injectable for tests and
 *    for code paths that read the clock once per operation.
 *
 * Returns `true` iff at least `STOP_THRESHOLD` timestamps fall within
 * `[now - STOP_WINDOW_MS, now]` (inclusive). Timestamps strictly in
 * the future are considered "at now" for robustness against minor
 * clock drift.
 *
 * @param {Array<number>} timestamps
 * @param {number} [now]
 * @returns {boolean}
 */
function shouldStop(timestamps, now) {
  if (!Array.isArray(timestamps) || timestamps.length < STOP_THRESHOLD) return false;
  const reference = Number.isFinite(now) ? Number(now) : Date.now();
  const windowStart = reference - STOP_WINDOW_MS;
  let inside = 0;
  for (const ts of timestamps) {
    const n = Number(ts);
    if (!Number.isFinite(n)) continue;
    // Future timestamps: clamp to `reference` — treat as "now" so a
    // clock-skewed peer cannot bypass the policy.
    const clamped = n > reference ? reference : n;
    if (clamped >= windowStart && clamped <= reference) {
      inside += 1;
      if (inside >= STOP_THRESHOLD) return true;
    }
  }
  return false;
}

/**
 * Resolve the effective StrictHostKeyChecking value for a host entry.
 *
 * Rules (design.md §Security Considerations, Req 13.1):
 *   1. If the HostEntry explicitly sets `strictHostKeyChecking`, that
 *      value wins. Accepted values: 'yes', 'no', 'ask', 'accept-new'.
 *      Unknown values fall through to the default.
 *   2. Otherwise, if `knownAliases` contains `entry.alias` (the host
 *      has been trusted before → a stored known_hosts entry exists),
 *      default to `'yes'` (reject mismatched keys silently).
 *   3. Otherwise default to `'ask'` (prompt the user for TOFU).
 *
 * @param {Object} entry HostEntry from ssh-config-parser.
 * @param {Iterable<string>|Set<string>|string[]} [knownAliases]
 * @returns {'yes'|'no'|'ask'|'accept-new'}
 */
function effectiveStrictHostKeyChecking(entry, knownAliases) {
  const explicit = entry && typeof entry === 'object' ? entry.strictHostKeyChecking : undefined;
  if (explicit === 'yes' || explicit === 'no' || explicit === 'ask' || explicit === 'accept-new') {
    return explicit;
  }
  if (!entry || typeof entry.alias !== 'string' || entry.alias.length === 0) {
    return 'ask';
  }
  // Accept any iterable with .has (Set) or arrays.
  if (knownAliases instanceof Set) {
    return knownAliases.has(entry.alias) ? 'yes' : 'ask';
  }
  if (Array.isArray(knownAliases)) {
    return knownAliases.includes(entry.alias) ? 'yes' : 'ask';
  }
  if (knownAliases && typeof knownAliases[Symbol.iterator] === 'function') {
    for (const alias of knownAliases) {
      if (alias === entry.alias) return 'yes';
    }
  }
  return 'ask';
}

module.exports = {
  shouldStop,
  effectiveStrictHostKeyChecking,
  STOP_WINDOW_MS,
  STOP_THRESHOLD,
};
