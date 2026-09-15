'use strict';
/**
 * Mount the local `-L` port forward for a provisioned remote ai_engine and
 * switch the app over to it — but only after the remote engine answers
 * `/health` through the tunnel.
 *
 * History: `ipc-remote-handlers` used to call
 * `new PortForwarder(session, port, allocator)` + `forwarder.open()`, neither
 * of which exists (`constructor(opts)` / `start(session)` is the real API), so
 * the TypeError was swallowed as a warning and port forwarding never worked.
 * Fixing the call alone would have been risky: on "success" the local Python
 * is stopped, so a tunnel to a dead remote engine would leave the app with no
 * engine at all. The health gate keeps the failure mode identical to today
 * (stay on the local engine) unless the remote one is verifiably up.
 *
 * Pure orchestration: every collaborator is injected, so it is unit-testable
 * with fakes. Never throws.
 */
const { probeLocalHealth } = require('./health-probe');

async function mountForwarder(params) {
  const {
    PortForwarder, session, remotePort, sessionRouter, fileBridge, termBridge,
    processManager, logger, alias, probe, probeTimeoutMs,
  } = params || {};
  const doProbe = typeof probe === 'function' ? probe : probeLocalHealth;
  const log = (level, event, fields) => {
    try { if (logger && typeof logger[level] === 'function') logger[level](event, fields); } catch (_e) { /* never throw */ }
  };
  let forwarder = null;
  try {
    if (typeof PortForwarder !== 'function') return { ok: false, localPort: null, reason: 'no-port-forwarder' };
    forwarder = new PortForwarder({ remotePort });
    const port = await forwarder.start(session);
    const healthy = await doProbe(port, probeTimeoutMs || 5000);
    if (!healthy) {
      try { await forwarder.close(); } catch (_e) { /* ignore */ }
      log('warn', 'remote-portforward-unhealthy', { alias, localPort: port, remotePort });
      return { ok: false, localPort: null, reason: 'health-check-failed' };
    }
    if (sessionRouter && typeof sessionRouter.setActive === 'function') {
      sessionRouter.setActive({ session, fileBridge, termBridge, localPort: port });
    }
    if (processManager && typeof processManager.stopPython === 'function') processManager.stopPython();
    log('info', 'remote-provision-complete', { alias, localPort: port, remotePort });
    return { ok: true, localPort: port, forwarder };
  } catch (err) {
    if (forwarder) { try { await forwarder.close(); } catch (_e) { /* ignore */ } }
    log('warn', 'remote-portforward-failed', { alias, message: (err && err.message) || String(err) });
    return { ok: false, localPort: null, reason: (err && err.message) || String(err) };
  }
}

module.exports = { mountForwarder };
