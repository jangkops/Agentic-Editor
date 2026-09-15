'use strict';
/**
 * Loopback health probe for a port-forwarded ai_engine.
 *
 * `probeLocalHealth(port, timeoutMs)` GETs `http://127.0.0.1:<port>/health` and
 * resolves `true` only for a 2xx status. It never throws and never rejects —
 * connection refused, timeouts and non-2xx all resolve `false` — so callers
 * can gate a routing switch on it without extra try/catch.
 */
const http = require('http');

function probeLocalHealth(port, timeoutMs) {
  const ms = Number.isFinite(timeoutMs) && timeoutMs > 0 ? timeoutMs : 5000;
  return new Promise((resolve) => {
    let settled = false;
    const finish = (v) => { if (!settled) { settled = true; resolve(v); } };
    let req;
    try {
      req = http.get({ host: '127.0.0.1', port, path: '/health', timeout: ms }, (res) => {
        const ok = res.statusCode >= 200 && res.statusCode < 300;
        res.resume();
        finish(ok);
      });
    } catch (_e) {
      return finish(false);
    }
    req.on('timeout', () => { try { req.destroy(); } catch (_e) { /* ignore */ } finish(false); });
    req.on('error', () => finish(false));
  });
}

module.exports = { probeLocalHealth };
