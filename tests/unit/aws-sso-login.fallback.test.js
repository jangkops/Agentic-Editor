/**
 * Task 3.4 — CLI 폴백/실패 계약 단위 테스트
 * Feature: app-deployment-readiness
 * Validates: Requirements 2.1, 2.7 (부수적으로 2.4)
 *
 * Runner: jest (repo "test:unit": "jest tests/unit/")
 *
 * 시나리오:
 *   1) SDK 경로 실패 + `aws` 미탐지  → login() = {success:false, ...} (CLI 폴백 없음)
 *   2) SDK 성공 경로                → login() = {success:true, profile}
 *   3) getCredentials: SDK 자격증명 없음 + CLI 없음 → null
 *
 * 대상(electron/core/aws-sso-manager.js)은 수정하지 않는다. AWS SDK v3 클라이언트와
 * `aws` CLI 탐지(child_process), fs(config/토큰 캐시)를 모킹하여 hermetic하게 검증.
 */

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

const VALID_CONFIG = (profile) =>
  [
    `[profile ${profile}]`,
    'sso_start_url = https://device.example/start',
    'sso_region = us-west-2',
    'sso_account_id = 123456789012',
    'sso_role_name = BedrockUser',
    'region = us-west-2',
    '',
  ].join('\n');

beforeEach(() => {
  setExternalOpener(() => undefined);
  mockFs.configContent = '';
  mockFs.existsSync = true;
  // 기본 exec: 아무 것도 매칭 안 되면 오류
  mockCtl.exec = (cmd, opts, cb) => {
    const done = typeof opts === 'function' ? opts : cb;
    done(new Error(`unexpected exec: ${cmd}`), '', '');
  };
});

describe('Task 3.4 — CLI 폴백/실패 계약', () => {
  test('시나리오 1: SDK 실패 + aws 미탐지 → {success:false,...} 이며 CLI 폴백(aws sso login) 미호출', async () => {
    mockFs.configContent = VALID_CONFIG('bedrockuser-a');
    // SDK: RegisterClient 단계부터 실패 → _loginViaSdk throw
    mockCtl.oidcSend = async () => { throw new Error('SDK OIDC 실패'); };

    const execCalls = [];
    mockCtl.exec = (cmd, opts, cb) => {
      const done = typeof opts === 'function' ? opts : cb;
      execCalls.push(cmd);
      if (/which aws|where aws/.test(cmd)) {
        // aws 미탐지
        return done(new Error('not found'), '', '');
      }
      return done(new Error(`unexpected exec: ${cmd}`), '', '');
    };

    const r = await new AwsSsoManager().login('bedrockuser-a');

    expect(r.success).toBe(false);
    expect(typeof r.error).toBe('string');
    expect(r.error.length).toBeGreaterThan(0);
    // CLI 폴백이 일어나지 않았음: 'aws sso login' exec가 호출되지 않음
    expect(execCalls.some((c) => /aws sso login/.test(c))).toBe(false);
    // 탐지 시도(which/where)는 있었음
    expect(execCalls.some((c) => /which aws|where aws/.test(c))).toBe(true);
  });

  test('시나리오 2: SDK 성공 경로 → {success:true, profile}', async () => {
    mockFs.configContent = VALID_CONFIG('bedrockuser-b');
    mockCtl.oidcSend = async (cmd) => {
      if (cmd.__t === 'RegisterClient') return { clientId: 'cid', clientSecret: 'sec', clientSecretExpiresAt: 1893456000 };
      if (cmd.__t === 'StartDeviceAuthorization') return { verificationUriComplete: 'https://v', deviceCode: 'dc', interval: 0.001, expiresIn: 600 };
      if (cmd.__t === 'CreateToken') return { accessToken: 'tok', expiresIn: 3600 };
      throw new Error('unexpected');
    };
    mockCtl.ssoSend = async (cmd) => {
      if (cmd.__t === 'GetRoleCredentials') return { roleCredentials: { accessKeyId: 'AKIA', secretAccessKey: 'sk', sessionToken: 'st' } };
      throw new Error('unexpected');
    };

    const r = await new AwsSsoManager().login('bedrockuser-b');
    expect(r).toEqual({ success: true, profile: 'bedrockuser-b' });
  });

  test('시나리오 3: getCredentials — SDK 자격증명 없음 + CLI 없음 → null', async () => {
    mockFs.configContent = VALID_CONFIG('bedrockuser-c');
    // fromSSO provider가 실패(자격증명 없음)
    mockCtl.fromSSO = () => async () => { throw new Error('fromSSO: no cached creds'); };
    // aws 미탐지 → CLI 폴백 없음
    mockCtl.exec = (cmd, opts, cb) => {
      const done = typeof opts === 'function' ? opts : cb;
      if (/which aws|where aws/.test(cmd)) return done(new Error('not found'), '', '');
      return done(new Error(`unexpected exec: ${cmd}`), '', '');
    };

    const creds = await new AwsSsoManager().getCredentials('bedrockuser-c');
    expect(creds).toBeNull();
  });

  test('보강: SDK 실패 + aws 탐지됨 + CLI 로그인/자격증명 성공 → {success:true, profile} (폴백 성공 경로)', async () => {
    mockFs.configContent = VALID_CONFIG('bedrockuser-d');
    mockCtl.oidcSend = async () => { throw new Error('SDK OIDC 실패'); };
    mockCtl.exec = (cmd, opts, cb) => {
      const done = typeof opts === 'function' ? opts : cb;
      if (/which aws|where aws/.test(cmd)) return done(null, '/usr/local/bin/aws\n', '');
      if (/aws sso login/.test(cmd)) return done(null, 'ok', '');
      if (/export-credentials/.test(cmd)) return done(null, 'AWS_ACCESS_KEY_ID=AKIA\nAWS_SECRET_ACCESS_KEY=sk\n', '');
      return done(new Error(`unexpected exec: ${cmd}`), '', '');
    };

    const r = await new AwsSsoManager().login('bedrockuser-d');
    expect(r).toEqual({ success: true, profile: 'bedrockuser-d' });
  });
});
