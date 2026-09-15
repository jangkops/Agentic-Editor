// mountForwarder: 올바른 PortForwarder API(constructor(opts)/start(session))로 열고,
// /health 가 확인될 때만 라우팅 전환 + 로컬 Python 정지. 실패 시 포워더를 닫고 로컬 유지.
const { mountForwarder } = require('../../../electron/src/remote/forwarder-mount');

function fakes() {
  const calls = { ctorOpts: null, started: null, closed: 0, setActive: null, stopPython: 0, logs: [] };
  class FakeForwarder {
    constructor(opts) { calls.ctorOpts = opts; this.localPort = null; }
    async start(session) { calls.started = session; this.localPort = 18765; return 18765; }
    async close() { calls.closed += 1; }
  }
  const sessionRouter = { setActive: (ctx) => { calls.setActive = ctx; } };
  const processManager = { stopPython: () => { calls.stopPython += 1; } };
  const logger = { info: (e, f) => calls.logs.push(['info', e, f]), warn: (e, f) => calls.logs.push(['warn', e, f]) };
  return { calls, FakeForwarder, sessionRouter, processManager, logger };
}

describe('mountForwarder', () => {
  test('healthy remote engine → routes to the tunnel and stops local Python', async () => {
    const f = fakes();
    const session = { alias: 'gpu' };
    const res = await mountForwarder({
      PortForwarder: f.FakeForwarder, session, remotePort: 8765, sessionRouter: f.sessionRouter,
      fileBridge: 'fb', termBridge: 'tb', processManager: f.processManager, logger: f.logger, alias: 'gpu',
      probe: async (port) => port === 18765,
    });
    expect(res.ok).toBe(true);
    expect(res.localPort).toBe(18765);
    expect(f.calls.ctorOpts).toEqual({ remotePort: 8765 });     // opts 객체로 생성(과거: 위치 인자)
    expect(f.calls.started).toBe(session);                        // start(session) (과거: open())
    expect(f.calls.setActive).toEqual({ session, fileBridge: 'fb', termBridge: 'tb', localPort: 18765 });
    expect(f.calls.stopPython).toBe(1);
    expect(f.calls.closed).toBe(0);
  });

  test('unhealthy remote engine → forwarder closed, no routing switch, local Python kept', async () => {
    const f = fakes();
    const res = await mountForwarder({
      PortForwarder: f.FakeForwarder, session: { alias: 'gpu' }, remotePort: 8765, sessionRouter: f.sessionRouter,
      processManager: f.processManager, logger: f.logger, alias: 'gpu', probe: async () => false,
    });
    expect(res.ok).toBe(false);
    expect(res.reason).toBe('health-check-failed');
    expect(f.calls.closed).toBe(1);
    expect(f.calls.setActive).toBeNull();
    expect(f.calls.stopPython).toBe(0);
    expect(f.calls.logs.some(([lvl, ev]) => lvl === 'warn' && ev === 'remote-portforward-unhealthy')).toBe(true);
  });

  test('start() failure → never throws, local kept', async () => {
    const f = fakes();
    class Failing extends f.FakeForwarder { async start() { throw new Error('port-range-exhausted'); } }
    const res = await mountForwarder({
      PortForwarder: Failing, session: { alias: 'x' }, remotePort: 8765, sessionRouter: f.sessionRouter,
      processManager: f.processManager, logger: f.logger, alias: 'x', probe: async () => true,
    });
    expect(res.ok).toBe(false);
    expect(res.reason).toMatch(/port-range-exhausted/);
    expect(f.calls.stopPython).toBe(0);
  });
});
