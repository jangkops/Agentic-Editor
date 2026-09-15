// probeLocalHealth: 2xx → true, 5xx → false, 닫힌 포트 → false, 절대 reject 하지 않는다.
const http = require('http');
const { probeLocalHealth } = require('../../../electron/src/remote/health-probe');

function serve(status) {
  return new Promise((resolve) => {
    const srv = http.createServer((req, res) => { res.statusCode = status; res.end(status === 200 ? '{"ok":true}' : 'nope'); });
    srv.listen(0, '127.0.0.1', () => resolve(srv));
  });
}

describe('probeLocalHealth', () => {
  test('resolves true for a 2xx /health', async () => {
    const srv = await serve(200);
    try { await expect(probeLocalHealth(srv.address().port, 2000)).resolves.toBe(true); }
    finally { srv.close(); }
  });
  test('resolves false for a non-2xx /health', async () => {
    const srv = await serve(500);
    try { await expect(probeLocalHealth(srv.address().port, 2000)).resolves.toBe(false); }
    finally { srv.close(); }
  });
  test('resolves false (never rejects) when nothing listens', async () => {
    const srv = await serve(200);
    const port = srv.address().port;
    await new Promise((r) => srv.close(r));
    await expect(probeLocalHealth(port, 2000)).resolves.toBe(false);
  });
});
