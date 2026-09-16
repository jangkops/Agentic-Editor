// backend-guard — 8765 점유자를 우리 사이드카/다른 프로세스/없음으로 구분한다(원장 #29).
const { EventEmitter } = require('events');
const { classifyHealth, probeBackend } = require('../../electron/src/backend-guard');

function fakeGet(script) {
  // script: {status, body} | 'error' | 'timeout'
  return (opts, cb) => {
    const req = new EventEmitter(); req.destroy = jest.fn();
    setImmediate(() => {
      if (script === 'error') { req.emit('error', new Error('ECONNREFUSED')); return; }
      if (script === 'timeout') { req.emit('timeout'); return; }
      const res = new EventEmitter(); res.statusCode = script.status; res.setEncoding = () => {};
      cb(res);
      if (script.body !== undefined) res.emit('data', script.body);
      res.emit('end');
    });
    return req;
  };
}

describe('classifyHealth', () => {
  test('our sidecar answers with service=ai-editor-engine', () => {
    expect(classifyHealth(200, JSON.stringify({ status: 'ok', service: 'ai-editor-engine', boot_id: 'x' }))).toBe('ours');
  });
  test('anything else on the port is foreign', () => {
    expect(classifyHealth(200, JSON.stringify({ service: 'some-other-app' }))).toBe('foreign');
    expect(classifyHealth(200, '<html>hello</html>')).toBe('foreign');
    expect(classifyHealth(404, '')).toBe('foreign');
  });
});

describe('probeBackend', () => {
  test('ours / foreign / down / timeout never reject', async () => {
    await expect(probeBackend({ get: fakeGet({ status: 200, body: '{"service":"ai-editor-engine"}' }) })).resolves.toBe('ours');
    await expect(probeBackend({ get: fakeGet({ status: 200, body: '{"service":"nginx"}' }) })).resolves.toBe('foreign');
    await expect(probeBackend({ get: fakeGet('error') })).resolves.toBe('down');
    await expect(probeBackend({ get: fakeGet('timeout') })).resolves.toBe('down');
  });
  test('a throwing http client is reported as down', async () => {
    await expect(probeBackend({ get: () => { throw new Error('boom'); } })).resolves.toBe('down');
  });
});
