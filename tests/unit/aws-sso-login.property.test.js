/**
 * Property 2: 로그인 결과 계약 불변 (성공·실패)
 * Feature: app-deployment-readiness, Property 2: 로그인 결과 계약 불변
 * Validates: Requirements 2.4, 2.7
 *
 * Runner: jest (repo "test:unit": "jest tests/unit/")
 * Library: fast-check
 *
 * 명제: 임의의 SDK/CLI 성공·실패 조합에서도 AwsSsoManager.login()은 항상 정확히
 *        { success: true, profile }            (profile === 입력 프로파일명)
 *   또는 { success: false, error: <nonempty string> }
 * 형태만 반환하며, 그 외 어떤 형태도 취하지 않는다.
 *
 * 대상(electron/core/aws-sso-manager.js)은 수정하지 않는다. AWS SDK v3 클라이언트
 * (sso-oidc / sso / sts / credential-providers), `aws` CLI 탐지(child_process),
 * 그리고 ~/.aws/config·토큰 캐시 파일 IO(fs)를 모두 모킹하여 네트워크·실제 SSO
 * 없이 hermetic하게 검증한다.
 *
 * 정직성 주석: CLI 폴백(_loginViaCli)의 실패 오류 메시지는 실제 child_process
 * 오류가 항상 비어있지 않은 message를 갖는 현실을 반영해 비어있지 않은 문자열로
 * 시뮬레이션한다(비현실적 빈 message로 테스트를 인위적으로 깨뜨리지 않는다).
 */

const fc = require('fast-check');

// ── 모킹 제어 상태 (jest.mock 팩토리가 참조 — 반드시 `mock` 접두) ───────────
const mockCtl = {
  oidcSend: async () => ({}),
  ssoSend: async () => ({}),
  stsSend: async () => ({}),
  fromSSO: () => async () => ({}),
  exec: (cmd, opts, cb) => {
    const done = typeof opts === 'function' ? opts : cb;
    done(new Error('noop'), '', '');
  },
};
const mockFs = { configContent: '', existsSync: true };

jest.mock('@aws-sdk/client-sso-oidc', () => {
  class RegisterClientCommand { constructor(i) { this.input = i; this.__t = 'RegisterClient'; } }
  class StartDeviceAuthorizationCommand { constructor(i) { this.input = i; this.__t = 'StartDeviceAuthorization'; } }
  class CreateTokenCommand { constructor(i) { this.input = i; this.__t = 'CreateToken'; } }
  class SSOOIDCClient { constructor(c) { this.c = c; } send(cmd) { return mockCtl.oidcSend(cmd); } }
  return { SSOOIDCClient, RegisterClientCommand, StartDeviceAuthorizationCommand, CreateTokenCommand };
});
jest.mock('@aws-sdk/client-sso', () => {
  class GetRoleCredentialsCommand { constructor(i) { this.input = i; this.__t = 'GetRoleCredentials'; } }
  class SSOClient { constructor(c) { this.c = c; } send(cmd) { return mockCtl.ssoSend(cmd); } }
  return { SSOClient, GetRoleCredentialsCommand };
});
jest.mock('@aws-sdk/client-sts', () => {
  class GetCallerIdentityCommand { constructor(i) { this.input = i; this.__t = 'GetCallerIdentity'; } }
  class AssumeRoleCommand { constructor(i) { this.input = i; this.__t = 'AssumeRole'; } }
  class STSClient { constructor(c) { this.c = c; } send(cmd) { return mockCtl.stsSend(cmd); } }
  return { STSClient, GetCallerIdentityCommand, AssumeRoleCommand };
});
jest.mock('@aws-sdk/credential-providers', () => ({
  fromSSO: (...a) => mockCtl.fromSSO(...a),
}));
jest.mock('child_process', () => ({
  exec: (...a) => mockCtl.exec(...a),
}));
jest.mock('fs', () => ({
  existsSync: jest.fn(() => mockFs.existsSync),
  readFileSync: jest.fn(() => mockFs.configContent),
  writeFileSync: jest.fn(),
  appendFileSync: jest.fn(),
  mkdirSync: jest.fn(),
  readdirSync: jest.fn(() => []),
}));

const { AwsSsoManager, setExternalOpener } = require('../../electron/core/aws-sso-manager');

const NUM_RUNS = 120; // >= 100 (spec)

// 알파뉴메릭 토큰 — 안전한 프로파일명 생성기 ('.', '@', '/', 공백 없음)
const alnum = 'abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789-';
const profileArb = fc
  .array(fc.constantFrom(...alnum.split('')), { minLength: 1, maxLength: 16 })
  .map((chars) => chars.join(''));

function installBehavior(profile, b) {
  // ~/.aws/config 내용 — configPresent=false면 SDK 구성 파싱이 실패해 폴백 분기로 진입
  mockFs.configContent = b.configPresent
    ? [
        `[profile ${profile}]`,
        'sso_start_url = https://device.example/start',
        'sso_region = us-west-2',
        'sso_account_id = 123456789012',
        'sso_role_name = BedrockUser',
        'region = us-west-2',
        '',
      ].join('\n')
    : '';

  mockCtl.oidcSend = async (cmd) => {
    if (cmd.__t === 'RegisterClient') {
      if (!b.registerOk) throw new Error('RegisterClient 실패');
      return { clientId: 'cid', clientSecret: 'secret', clientSecretExpiresAt: 1893456000 };
    }
    if (cmd.__t === 'StartDeviceAuthorization') {
      if (!b.startOk) throw new Error('StartDeviceAuthorization 실패');
      // interval 0.001s → pollForToken sleep ~1ms (테스트 신속)
      return { verificationUriComplete: 'https://device.example/verify', deviceCode: 'dc', interval: 0.001, expiresIn: 600 };
    }
    if (cmd.__t === 'CreateToken') {
      if (!b.createTokenOk) { const e = new Error('토큰 거부'); e.name = 'AccessDeniedException'; throw e; }
      return { accessToken: 'access-token', expiresIn: 3600 };
    }
    throw new Error('예상치 못한 oidc 커맨드');
  };

  mockCtl.ssoSend = async (cmd) => {
    if (cmd.__t === 'GetRoleCredentials') {
      if (!b.getRoleOk) throw new Error('GetRoleCredentials 실패');
      return { roleCredentials: { accessKeyId: 'AKIA', secretAccessKey: 'sk', sessionToken: 'st', expiration: Date.now() + 3600000 } };
    }
    throw new Error('예상치 못한 sso 커맨드');
  };

  mockCtl.exec = (cmd, opts, cb) => {
    const done = typeof opts === 'function' ? opts : cb;
    if (/which aws|where aws/.test(cmd)) {
      if (b.cliAvailable) return done(null, '/usr/local/bin/aws\n', '');
      return done(new Error('aws not found'), '', '');
    }
    if (/aws sso login/.test(cmd)) {
      if (b.cliLoginOk) return done(null, 'login ok', '');
      // 현실적 오류: 비어있지 않은 message + stderr
      return done(new Error('CLI sso login 실패'), '', 'sso login stderr');
    }
    if (/export-credentials/.test(cmd)) {
      if (b.cliExportOk) return done(null, 'AWS_ACCESS_KEY_ID=AKIA\nAWS_SECRET_ACCESS_KEY=sk\nAWS_SESSION_TOKEN=st\n', '');
      return done(new Error('export 실패'), '', '');
    }
    return done(new Error(`예상치 못한 exec: ${cmd}`), '', '');
  };
}

describe('Property 2: 로그인 결과 계약 불변 (login) — Validates Requirements 2.4, 2.7', () => {
  beforeEach(() => {
    // 브라우저 오픈을 no-op로 주입 (electron 부재 환경에서 조용히 통과)
    setExternalOpener(() => undefined);
  });

  test('임의의 SDK/CLI 성공·실패 조합에서 login은 항상 {success:true,profile} 또는 {success:false,error<nonempty>}만 반환', async () => {
    await fc.assert(
      fc.asyncProperty(
        profileArb,
        fc.record({
          configPresent: fc.boolean(),
          registerOk: fc.boolean(),
          startOk: fc.boolean(),
          createTokenOk: fc.boolean(),
          getRoleOk: fc.boolean(),
          cliAvailable: fc.boolean(),
          cliLoginOk: fc.boolean(),
          cliExportOk: fc.boolean(),
        }),
        async (profile, b) => {
          installBehavior(profile, b);
          const mgr = new AwsSsoManager();

          const result = await mgr.login(profile);

          // (a) 항상 non-null 객체
          expect(result).not.toBeNull();
          expect(typeof result).toBe('object');
          expect(typeof result.success).toBe('boolean');

          if (result.success === true) {
            // (b) 성공: 정확히 {success, profile}, profile === 입력
            expect(Object.keys(result).sort()).toEqual(['profile', 'success']);
            expect(result.profile).toBe(profile);
          } else {
            // (c) 실패: 정확히 {success, error}, error는 비어있지 않은 문자열
            expect(Object.keys(result).sort()).toEqual(['error', 'success']);
            expect(typeof result.error).toBe('string');
            expect(result.error.length).toBeGreaterThan(0);
          }
        },
      ),
      { numRuns: NUM_RUNS },
    );
  });

  test('예제: 전 경로 SDK 성공 → {success:true, profile}', async () => {
    installBehavior('bedrockuser-ok', {
      configPresent: true, registerOk: true, startOk: true, createTokenOk: true,
      getRoleOk: true, cliAvailable: false, cliLoginOk: false, cliExportOk: false,
    });
    const r = await new AwsSsoManager().login('bedrockuser-ok');
    expect(r).toEqual({ success: true, profile: 'bedrockuser-ok' });
  });

  test('예제: SDK 실패 + CLI 없음 → {success:false, error<nonempty>}', async () => {
    installBehavior('bedrockuser-x', {
      configPresent: true, registerOk: false, startOk: false, createTokenOk: false,
      getRoleOk: false, cliAvailable: false, cliLoginOk: false, cliExportOk: false,
    });
    const r = await new AwsSsoManager().login('bedrockuser-x');
    expect(r.success).toBe(false);
    expect(typeof r.error).toBe('string');
    expect(r.error.length).toBeGreaterThan(0);
    expect(Object.keys(r).sort()).toEqual(['error', 'success']);
  });
});
