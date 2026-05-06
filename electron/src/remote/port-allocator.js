'use strict';
/**
 * Port Allocator — free-port discovery for SSH local forwards.
 *
 * Feature: remote-ssh
 * Covers Requirements: 5.2
 * Implements:          Correctness Property 9
 *
 * Remote_Session establishes an SSH local forward (`-L`) that the
 * Electron renderer reaches via `http://127.0.0.1:<port>`. The default
 * port is 18765; if it is already bound on the workstation we scan the
 * inclusive range [18765, 18865] and pick the lowest free port so the
 * `apiBase` swap (design §Property 10) is deterministic.
 *
 * Design constraints:
 *  - No external dependencies. The built-in `net` module is the single
 *    source of truth for "is this port free?" — we actually attempt to
 *    bind 127.0.0.1:<port> rather than reading `/proc/net/tcp` or
 *    shelling out to `lsof`, which would be platform-specific.
 *  - No caching. Every call to `allocatePort` re-probes because another
 *    process may have bound a port between calls. The caller holds the
 *    returned port only for the few milliseconds before it wires up the
 *    SSH forward, so a TOCTOU race is tolerable; the forward server's
 *    own `listen()` will surface EADDRINUSE if the port gets snatched
 *    in between.
 *  - Sequential probing. The range is 101 ports wide and the common
 *    case returns on the first probe (18765 free). Parallel probing
 *    would race against itself — two probes of the same port would
 *    both briefly bind it — so we scan in order and accept the extra
 *    latency in the pathological "everything is busy" case.
 *
 * Property 9 restatement (from design.md):
 *   For any bound-set B ⊆ ℕ,
 *     (a) B ∩ [18765, 18865] ≠ [18765, 18865]
 *         ⇒ allocatePort() = min([18765, 18865] \ B)
 *     (b) [18765, 18865] ⊆ B
 *         ⇒ allocatePort() throws PortExhaustedError
 *
 * The `preferred` option is a practical shortcut: in the common case
 * `preferred == range[0]`, so checking it first collapses to a single
 * probe. When `preferred` falls outside `range` we still honor it first
 * so a caller can override the range default (e.g. integration tests
 * that need a port in a known-open band) without widening the range.
 */

const net = require('net');

/** Inclusive port range used for SSH forwards by Remote_Session. */
const DEFAULT_RANGE = Object.freeze([18765, 18865]);

/**
 * Raised when every port in the configured range is already bound.
 * Carries the probed range so the caller can construct the remediation
 * hint documented in design §Error Handling ("lsof -i:18765-18865 …").
 */
class PortExhaustedError extends Error {
  /**
   * @param {[number, number]} range Inclusive [start, end] that was scanned.
   */
  constructor(range) {
    const [start, end] = Array.isArray(range) ? range : DEFAULT_RANGE;
    super(`No free TCP port available in range [${start}, ${end}]`);
    this.name = 'PortExhaustedError';
    /** @type {[number, number]} */
    this.range = [start, end];
  }
}

/**
 * Probe whether a TCP port is free on 127.0.0.1.
 *
 * Implementation: open a transient `net.Server`, try to bind it, and
 * close immediately on success. We bind to the loopback specifically
 * because that is where the SSH forward will listen — a port that is
 * free on 0.0.0.0 but bound on 127.0.0.1 (say, by a local dev server)
 * must still count as "taken" for our purposes.
 *
 * Errors treated as "port taken":
 *   - `EADDRINUSE`: the kernel already has the (addr, port) bound.
 *   - `EACCES`:     low-privilege port or SELinux/AppArmor denial.
 * Any other error (e.g. EAFNOSUPPORT) is also treated as unavailable
 * rather than propagated, because from the allocator's perspective the
 * port is unusable regardless of the underlying reason.
 *
 * The server is `unref()`'d so an orphaned probe cannot by itself keep
 * the Electron process alive. We also guard against the edge case
 * where both `error` and `listening` fire (some Node versions emit
 * `error` after close) by latching on the first settlement.
 *
 * @param {number} port Positive integer TCP port.
 * @returns {Promise<boolean>} Resolves `true` if `port` was free, `false` otherwise.
 */
function isPortAvailable(port) {
  return new Promise((resolve) => {
    // Reject non-integers and out-of-range values without touching the
    // network stack. Port 0 is "pick any port" to `listen()`, which is
    // the wrong semantics here.
    if (!Number.isInteger(port) || port <= 0 || port > 65535) {
      resolve(false);
      return;
    }

    const server = net.createServer();
    server.unref();

    let settled = false;
    const settle = (value) => {
      if (settled) return;
      settled = true;
      resolve(value);
    };

    server.once('error', () => {
      // Covers EADDRINUSE, EACCES, and anything else that prevented
      // bind. Close is safe even after an error because Node's
      // `Server.close()` is a no-op when the server is not listening.
      try { server.close(); } catch (_err) { /* best effort */ }
      settle(false);
    });

    server.once('listening', () => {
      // Close before resolving so the probe never lingers. `close()`
      // is asynchronous; we still resolve true once the callback fires
      // so the caller knows the port is definitely free (the close
      // completes before any subsequent listen attempt).
      server.close(() => settle(true));
    });

    try {
      server.listen(port, '127.0.0.1');
    } catch (_err) {
      // Synchronous throws from `listen` are rare but possible on
      // argument errors. Treat as unavailable for safety.
      settle(false);
    }
  });
}

/**
 * Allocate a free TCP port suitable for an SSH local forward.
 *
 * Algorithm:
 *   1. Validate + normalize `range` and `preferred`.
 *   2. Probe `preferred`. If free, return it.
 *   3. Otherwise scan `range` from start to end inclusive, skipping
 *      `preferred` (already probed). Return the first free port.
 *   4. If none of the probed ports is free, throw `PortExhaustedError`.
 *
 * The preferred-first strategy is a fast path for the common case
 * where nothing else is listening in the 18765 band; Property 9's
 * `min([range] \ B)` contract is preserved because when `preferred ==
 * range[0]` the preferred probe returns exactly `min(range \ B)` on
 * success, and on failure we scan from `range[0]` upward and return
 * the first free port we find.
 *
 * @param {Object} [opts]
 * @param {number=} opts.preferred Preferred port to try first. Defaults
 *   to `range[0]`.
 * @param {[number, number]=} opts.range Inclusive [start, end]. Defaults
 *   to `[18765, 18865]`.
 * @returns {Promise<number>} Free port in `range` (or `preferred` when
 *   available).
 * @throws {PortExhaustedError} When every port in `range` is bound.
 */
async function allocatePort(opts) {
  const options = opts || {};
  const range = Array.isArray(options.range) && options.range.length === 2
    ? [Number(options.range[0]), Number(options.range[1])]
    : [DEFAULT_RANGE[0], DEFAULT_RANGE[1]];
  const [start, end] = range;

  if (!Number.isInteger(start) || !Number.isInteger(end) || start <= 0 || end < start) {
    throw new RangeError(
      `Invalid port range: [${options.range && options.range[0]}, ${options.range && options.range[1]}]`,
    );
  }

  const preferred = Number.isInteger(options.preferred) ? options.preferred : start;

  // 1) Fast path — try the caller's preferred port first.
  if (await isPortAvailable(preferred)) {
    return preferred;
  }

  // 2) Sweep the range in ascending order, skipping the preferred port
  // we just probed so we do not waste a syscall on it.
  for (let port = start; port <= end; port++) {
    if (port === preferred) continue;
    // eslint-disable-next-line no-await-in-loop
    if (await isPortAvailable(port)) {
      return port;
    }
  }

  throw new PortExhaustedError([start, end]);
}

/**
 * Release a previously allocated port.
 *
 * In v1 there is no per-process reservation table: the OS releases the
 * port as soon as the forward server's `close()` runs, and the
 * allocator does not track outstanding allocations. This hook exists
 * so call sites can express intent explicitly today and we can add
 * leak detection (e.g. warn when a port is allocated twice without a
 * release in between) without changing the public API later.
 *
 * @param {number} _port Previously returned by `allocatePort`.
 * @returns {void}
 */
// eslint-disable-next-line no-unused-vars
function releasePort(_port) {
  // intentionally no-op; see doc comment.
}

module.exports = {
  allocatePort,
  isPortAvailable,
  releasePort,
  PortExhaustedError,
  DEFAULT_RANGE,
};
