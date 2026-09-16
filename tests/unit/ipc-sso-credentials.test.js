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
    registerSsoHandlers(fakeManager(CREDS), { injectCredentials: injector, resolveApiBase: () => 'http://localhost:8765' });
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
    registerSsoHandlers(fakeManager(CREDS), { injectCredentials: injector, resolveApiBase: () => base });
    const h = handler('sso:get-credentials');
    await h(null, 'p', {});
    base = 'http://127.0.0.1:18765';
    await h(null, 'p', {});
    expect(injector).toHaveBeenCalledTimes(2);
    expect(injector.mock.calls[1][0].base).toBe('http://127.0.0.1:18765');
  });

  test('injection failure is reported (injected:false) and retried on the next call', async () => {
    const injector = jest.fn(async () => false);
    registerSsoHandlers(fakeManager(CREDS), { injectCredentials: injector, resolveApiBase: () => 'http://localhost:8765' });
    const h = handler('sso:get-credentials');
    expect((await h(null, 'p', {})).injected).toBe(false);
    expect((await h(null, 'p', {})).injected).toBe(false);
    expect(injector).toHaveBeenCalledTimes(2);
  });

  test('no credentials -> null and no injection attempt', async () => {
    const injector = jest.fn(async () => true);
    registerSsoHandlers(fakeManager(null), { injectCredentials: injector });
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
