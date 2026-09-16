// sso:get-credentials — 메인이 사이드카에 자격증명을 직접 주입하고 렌더러에는 비밀 값을 돌려주지 않는다.
const { ipcMain } = require('electron');
const { registerSsoHandlers, _toIpv4Loopback } = require('../../electron/src/ipc-sso-handlers');

const CREDS = { AWS_ACCESS_KEY_ID: 'AKIAEXAMPLE1234567', AWS_SECRET_ACCESS_KEY: 'sk-very-secret', AWS_SESSION_TOKEN: 'st-token', AWS_DEFAULT_REGION: 'us-west-2' };
const fakeManager = (creds) => ({
  listProfiles: () => [], login: async () => ({ success: true }), getBedrockUsername: async () => 'u',
  verifyBedrockUsername: async () => ({ ok: true }), getCredentials: async () => creds, getExpiry: async () => null,
});
function handler(channel) {
  const entry = [...ipcMain.handle.mock.calls].reverse().find(([ch]) => ch === channel);
  return entry[1];
}

describe('sso:get-credentials keeps secrets in the main process', () => {
  test('returns a secret-free status and injects once per credential/profile/base', async () => {
    const injector = jest.fn(async () => true);
    registerSsoHandlers(fakeManager(CREDS), { injectCredentials: injector, resolveApiBase: () => 'http://localhost:8765', watcher: null });
    const h = handler('sso:get-credentials');
    const r1 = await h(null, 'bedrock-gw', { bedrockUser: 'alice' });
    expect(Object.keys(r1).sort()).toEqual(['injected', 'ok', 'profile', 'region']);
    expect(r1).toEqual({ ok: true, injected: true, profile: 'bedrock-gw', region: 'us-west-2' });
    const serialized = JSON.stringify(r1);
    for (const secret of ['AKIAEXAMPLE1234567', 'sk-very-secret', 'st-token', 'AWS_SECRET', 'AWS_ACCESS_KEY_ID']) {
      expect(serialized).not.toContain(secret);
    }
    expect(injector).toHaveBeenCalledTimes(1);
    expect(injector.mock.calls[0][0]).toEqual({ base: 'http://localhost:8765', profile: 'bedrock-gw', bedrockUser: 'alice', credentials: CREDS });

    // 같은 자격증명·같은 사이드카 -> 재주입하지 않는다(모델 목록 새로고침이 캐시를 비우지 않도록)
    const r2 = await h(null, 'bedrock-gw', { bedrockUser: 'alice' });
    expect(r2.injected).toBe(true);
    expect(injector).toHaveBeenCalledTimes(1);
    // force(로그인·토큰 만료) -> 재주입
    await h(null, 'bedrock-gw', { bedrockUser: 'alice', force: true });
    expect(injector).toHaveBeenCalledTimes(2);
    // bedrockUser 가 바뀌면 assume-role 대상이 달라지므로 재주입
    await h(null, 'bedrock-gw', { bedrockUser: 'bob' });
    expect(injector).toHaveBeenCalledTimes(3);
  });

  test('a different api base (remote tunnel) triggers a fresh injection', async () => {
    const injector = jest.fn(async () => true);
    let base = 'http://localhost:8765';
    registerSsoHandlers(fakeManager(CREDS), { injectCredentials: injector, resolveApiBase: () => base, watcher: null });
    const h = handler('sso:get-credentials');
    await h(null, 'p', {});
    base = 'http://127.0.0.1:18765';
    await h(null, 'p', {});
    expect(injector).toHaveBeenCalledTimes(2);
    expect(injector.mock.calls[1][0].base).toBe('http://127.0.0.1:18765');
  });

  test('injection failure is reported (injected:false) and retried on the next call', async () => {
    const injector = jest.fn(async () => false);
    registerSsoHandlers(fakeManager(CREDS), { injectCredentials: injector, resolveApiBase: () => 'http://localhost:8765', watcher: null });
    const h = handler('sso:get-credentials');
    expect((await h(null, 'p', {})).injected).toBe(false);
    expect((await h(null, 'p', {})).injected).toBe(false);
    expect(injector).toHaveBeenCalledTimes(2);
  });

  test('no credentials -> null and no injection attempt', async () => {
    const injector = jest.fn(async () => true);
    registerSsoHandlers(fakeManager(null), { injectCredentials: injector, watcher: null });
    expect(await handler('sso:get-credentials')(null, 'p', {})).toBeNull();
    expect(injector).not.toHaveBeenCalled();
  });
});


describe('main-process sidecar base uses the IPv4 loopback', () => {
  // Node 18(Electron 28) fetch 는 localhost -> ::1 실패 시 IPv4 로 폴백하지 않는다(런타임 검증에서 "fetch failed" 재현).
  test('localhost is rewritten to 127.0.0.1, other hosts untouched', () => {
    expect(_toIpv4Loopback('http://localhost:8765')).toBe('http://127.0.0.1:8765');
    expect(_toIpv4Loopback('http://localhost:8765/api')).toBe('http://127.0.0.1:8765/api');
    expect(_toIpv4Loopback('http://LOCALHOST:8765')).toBe('http://127.0.0.1:8765');
    expect(_toIpv4Loopback('http://127.0.0.1:18765')).toBe('http://127.0.0.1:18765');   // 원격 터널
    expect(_toIpv4Loopback('http://localhost.example:8765')).toBe('http://localhost.example:8765');
    expect(_toIpv4Loopback('')).toBe('');
  });
});

// ---- 사이드카 재기동 감지 → 즉시 재주입 ----
const { EventEmitter } = require('events');
class FakeWatcher extends EventEmitter {
  constructor() { super(); this.lastBootId = null; this.started = 0; }
  start() { this.started += 1; }
  stop() {}
}
const flush = () => new Promise((r) => setImmediate(r));

describe('sidecar restart -> immediate re-injection', () => {
  test('boot_id change re-injects the last profile without waiting for the renderer', async () => {
    let bootId = 'A';
    const injector = jest.fn(async () => ({ ok: true, bootId }));
    const manager = fakeManager(CREDS);
    const getCreds = jest.spyOn(manager, 'getCredentials');
    const w = new FakeWatcher();
    const { watcher } = registerSsoHandlers(manager, { injectCredentials: injector, resolveApiBase: () => 'http://127.0.0.1:8765', watcher: w });
    expect(watcher).toBe(w);
    expect(w.started).toBe(1);

    // 주입 전에는 감시 이벤트가 아무 일도 하지 않는다
    w.lastBootId = 'A'; w.emit('healthy', { bootId: 'A', recovered: false, changed: false }); await flush();
    expect(injector).not.toHaveBeenCalled();

    const h = handler('sso:get-credentials');
    await h(null, 'bedrock-gw', { bedrockUser: 'alice' });
    expect(injector).toHaveBeenCalledTimes(1);

    // 같은 인스턴스(A)가 계속 응답 → 재주입 없음
    w.emit('healthy', { bootId: 'A', recovered: false, changed: false }); await flush();
    expect(injector).toHaveBeenCalledTimes(1);

    // 사이드카가 재기동돼 boot_id 가 B 로 바뀜 → 자격증명을 다시 받아 즉시 재주입
    bootId = 'B'; w.lastBootId = 'B';
    w.emit('healthy', { bootId: 'B', recovered: true, changed: true }); await flush();
    expect(injector).toHaveBeenCalledTimes(2);
    expect(injector.mock.calls[1][0]).toEqual({ base: 'http://127.0.0.1:8765', profile: 'bedrock-gw', bedrockUser: 'alice', credentials: CREDS });
    expect(getCreds).toHaveBeenCalledTimes(2);   // 재주입 시 자격증명을 새로 받는다(만료 갱신 반영)

    // 새 인스턴스 B 가 계속 응답 → 더 이상 재주입하지 않는다(루프 없음)
    w.emit('healthy', { bootId: 'B', recovered: false, changed: false }); await flush();
    w.emit('healthy', { bootId: 'B', recovered: false, changed: false }); await flush();
    expect(injector).toHaveBeenCalledTimes(2);
  });

  test('renderer call skips dedupe when the watcher has seen a different instance', async () => {
    const injector = jest.fn(async () => ({ ok: true, bootId: 'A' }));
    const w = new FakeWatcher();
    registerSsoHandlers(fakeManager(CREDS), { injectCredentials: injector, resolveApiBase: () => 'http://127.0.0.1:8765', watcher: w });
    const h = handler('sso:get-credentials');
    await h(null, 'p', {});
    await h(null, 'p', {});
    expect(injector).toHaveBeenCalledTimes(1);      // TTL 안, 같은 인스턴스 → dedupe
    w.lastBootId = 'Z';                              // 감시자는 다른 인스턴스를 봤다
    await h(null, 'p', {});
    expect(injector).toHaveBeenCalledTimes(2);      // dedupe 무시하고 재주입
  });

  test('servers without boot_id: re-inject once on down -> up recovery only', async () => {
    const injector = jest.fn(async () => true);      // boolean 반환(구형/테스트 주입기)
    const w = new FakeWatcher();
    registerSsoHandlers(fakeManager(CREDS), { injectCredentials: injector, resolveApiBase: () => 'http://127.0.0.1:8765', watcher: w });
    await handler('sso:get-credentials')(null, 'p', {});
    expect(injector).toHaveBeenCalledTimes(1);
    w.emit('healthy', { bootId: null, recovered: false, changed: false }); await flush();
    expect(injector).toHaveBeenCalledTimes(1);      // 그냥 정상 응답 → 아무것도 안 함
    w.emit('healthy', { bootId: null, recovered: true, changed: false }); await flush();
    expect(injector).toHaveBeenCalledTimes(2);      // 복구 → 재주입
  });

  test('re-injection is skipped when credentials are gone (session expired)', async () => {
    const injector = jest.fn(async () => ({ ok: true, bootId: 'A' }));
    let creds = CREDS;
    const manager = { ...fakeManager(CREDS), getCredentials: async () => creds };
    const w = new FakeWatcher();
    registerSsoHandlers(manager, { injectCredentials: injector, resolveApiBase: () => 'http://127.0.0.1:8765', watcher: w });
    await handler('sso:get-credentials')(null, 'p', {});
    creds = null;
    w.emit('healthy', { bootId: 'B', recovered: true, changed: true }); await flush();
    expect(injector).toHaveBeenCalledTimes(1);
  });

  test('watcher: null disables watching; AE_SIDECAR_WATCH_MS=0 disables the default watcher', () => {
    const r1 = registerSsoHandlers(fakeManager(CREDS), { injectCredentials: async () => true, watcher: null });
    expect(r1.watcher).toBeNull();
    const prev = process.env.AE_SIDECAR_WATCH_MS;
    process.env.AE_SIDECAR_WATCH_MS = '0';
    try {
      const r2 = registerSsoHandlers(fakeManager(CREDS), { injectCredentials: async () => true });
      expect(r2.watcher).toBeNull();
    } finally {
      if (prev === undefined) delete process.env.AE_SIDECAR_WATCH_MS; else process.env.AE_SIDECAR_WATCH_MS = prev;
    }
  });
});
