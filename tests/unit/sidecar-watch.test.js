// SidecarWatcher — /health 의 boot_id 변화와 down→up 전이를 이벤트로 알리는지 검증한다.
const { SidecarWatcher } = require('../../electron/src/sidecar-watch');

function fetchScript(responses) {
  // responses: 배열의 각 항목은 'fail' | {bootId} | {noBody:true}
  let i = 0;
  const calls = [];
  const fn = jest.fn(async (url, opts) => {
    calls.push({ url, opts });
    const r = responses[Math.min(i, responses.length - 1)]; i += 1;
    if (r === 'fail') throw new Error('ECONNREFUSED');
    if (r.status && r.status >= 400) return { ok: false, status: r.status, json: async () => ({}) };
    if (r.noBody) return { ok: true, status: 200, json: async () => { throw new Error('no json'); } };
    return { ok: true, status: 200, json: async () => ({ status: 'ok', boot_id: r.bootId }) };
  });
  fn.calls = calls;
  return fn;
}

function collect(w) {
  const ev = [];
  w.on('healthy', (e) => ev.push(['healthy', e.bootId, e.recovered, e.changed]));
  w.on('down', () => ev.push(['down']));
  return ev;
}

describe('SidecarWatcher.probe()', () => {
  test('first sighting is neither recovered nor changed; boot_id change is reported', async () => {
    const f = fetchScript([{ bootId: 'A' }, { bootId: 'A' }, { bootId: 'B' }]);
    const w = new SidecarWatcher({ resolveApiBase: () => 'http://127.0.0.1:8765', fetchImpl: f });
    const ev = collect(w);
    await w.probe(); await w.probe(); await w.probe();
    expect(ev).toEqual([['healthy', 'A', false, false], ['healthy', 'A', false, false], ['healthy', 'B', false, true]]);
    expect(w.lastBootId).toBe('B');
    expect(f.calls[0].url).toBe('http://127.0.0.1:8765/health');
  });

  test('down -> up is reported as recovered (also for servers without boot_id)', async () => {
    const f = fetchScript([{ noBody: true }, 'fail', 'fail', { noBody: true }]);
    const w = new SidecarWatcher({ fetchImpl: f });
    const ev = collect(w);
    await w.probe(); await w.probe(); await w.probe(); await w.probe();
    expect(ev).toEqual([['healthy', null, false, false], ['down'], ['healthy', null, true, false]]);
    expect(w.up).toBe(true);
  });

  test('non-2xx counts as down', async () => {
    const f = fetchScript([{ bootId: 'A' }, { status: 503 }, { bootId: 'A' }]);
    const w = new SidecarWatcher({ fetchImpl: f });
    const ev = collect(w);
    await w.probe(); await w.probe(); await w.probe();
    expect(ev).toEqual([['healthy', 'A', false, false], ['down'], ['healthy', 'A', true, false]]);
  });

  test('apiBase is resolved on every probe (remote tunnel switch)', async () => {
    let base = 'http://127.0.0.1:8765';
    const f = fetchScript([{ bootId: 'local' }, { bootId: 'remote' }]);
    const w = new SidecarWatcher({ resolveApiBase: () => base, fetchImpl: f });
    const ev = collect(w);
    await w.probe(); base = 'http://127.0.0.1:18765'; await w.probe();
    expect(f.calls[1].url).toBe('http://127.0.0.1:18765/health');
    expect(ev[1]).toEqual(['healthy', 'remote', false, true]);
  });
});

describe('SidecarWatcher scheduling', () => {
  beforeEach(() => { jest.useFakeTimers(); });
  afterEach(() => { jest.useRealTimers(); });

  test('polls fast while down, slowly while healthy, and stops cleanly', async () => {
    const f = fetchScript(['fail', { bootId: 'A' }, { bootId: 'A' }, { bootId: 'A' }]);
    const w = new SidecarWatcher({ fetchImpl: f, intervalMs: 5000, downIntervalMs: 1000 });
    w.start();
    await jest.advanceTimersByTimeAsync(0);       // 즉시 첫 probe → fail → down
    expect(f).toHaveBeenCalledTimes(1);
    await jest.advanceTimersByTimeAsync(999);
    expect(f).toHaveBeenCalledTimes(1);
    await jest.advanceTimersByTimeAsync(1);       // 1초 뒤 재시도 → healthy
    expect(f).toHaveBeenCalledTimes(2);
    expect(w.up).toBe(true);
    await jest.advanceTimersByTimeAsync(4999);    // 정상 상태에서는 5초 간격
    expect(f).toHaveBeenCalledTimes(2);
    await jest.advanceTimersByTimeAsync(1);
    expect(f).toHaveBeenCalledTimes(3);
    w.stop();
    await jest.advanceTimersByTimeAsync(60000);
    expect(f).toHaveBeenCalledTimes(3);
    w.start(); w.start();                          // 중복 start 는 한 번만
    await jest.advanceTimersByTimeAsync(0);
    expect(f).toHaveBeenCalledTimes(4);
    w.stop();
  });
});
