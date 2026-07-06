/**
 * Task 3.5 — IPC 반환 계약 보존 회귀 체크
 * Feature: app-deployment-readiness
 * Validates: Requirements 2.4, 2.5, 2.6, 5.7
 *
 * Runner: jest (repo "test:unit": "jest tests/unit/")
 *
 * 목적: SDK v3 재구현 이후에도 ipc-sso-handlers.js가 렌더러로 그대로 통과시키는
 * AwsSsoManager 메서드들의 반환 "형태(shape)"가 재구현 전과 동일함을 검증한다.
 *
 *   - sso:list-profiles        → listProfiles()        → string[]
 *   - sso:login                → login()               → {success:true,profile} | {success:false,error}
 *   - sso:get-credentials      → getCredentials()      → 4-key env-var 객체 | null
 *   - sso:get-bedrock-username → getBedrockUsername()  → string
 *
 * 무변경(read-only) 확인:
 *   본 태스크는 electron/src/ipc-sso-handlers.js 와 src/main.js 를 수정하지 않는다.
 *   ipc-sso-handlers.js의 핸들러는 위 메서드 반환값을 가공 없이 그대로 반환(list-profiles/
 *   login/get-credentials/get-bedrock-username)하거나 예외 시 안전값([]/false/null)으로
 *   폴백한다. 따라서 메서드 반환 형태가 보존되면 IPC 계약도 보존된다. 이 파일은 그 형태
 *   보존을 mock 기반·파일 IO 없이 회귀 검증한다.
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

const CRED_KEYS = ['AWS_ACCESS_KEY_ID', 'AWS_DEFAULT_REGION', 'AWS_SECRET_ACCESS_KEY', 'AWS_SESSION_TOKEN'];

beforeEach(() => {
  setExternalOpener(() => undefined);
  mockFs.configContent = '';
  mockFs.existsSync = true;
  mockCtl.exec = (cmd, opts, cb) => {
    const done = typeof opts === 'function' ? opts : cb;
    if (/which aws|where aws/.test(cmd)) return done(new Error('not found'), '', '');
    return done(new Error(`unexpected exec: ${cmd}`), '', '');
  };
});

describe('Task 3.5 — IPC 반환 계약 보존 (list-profiles)', () => {
  test('listProfiles()는 항상 배열; bedrockuser* 상단 정렬 유지', () => {
    mockFs.configContent = [
      '[profile zeta]',
      '[profile bedrockuser-b]',
      '[profile alpha]',
      '[profile bedrockuser-a]',
      '',
    ].join('\n');
    const out = new AwsSsoManager().listProfiles();
    expect(Array.isArray(out)).toBe(true);
    out.forEach((p) => expect(typeof p).toBe('string'));
    // bedrockuser* 가 앞쪽
    expect(out.slice(0, 2).every((p) => p.startsWith('bedrockuser'))).toBe(true);
  });

  test('설정이 없으면 빈 배열([]) — 계약 보존', () => {
    mockFs.configContent = '';
    const out = new AwsSsoManager().listProfiles();
    expect(Array.isArray(out)).toBe(true);
    expect(out).toEqual([]);
  });
});

describe('Task 3.5 — IPC 반환 계약 보존 (login)', () => {
  test('성공 → {success:true, profile} 정확한 형태', async () => {
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

  test('실패 → {success:false, error} 정확한 형태(error는 비어있지 않은 문자열)', async () => {
    mockFs.configContent = ''; // 구성 부재 → _loginViaSdk throw, CLI 미탐지 → 실패 반환
    const r = await new AwsSsoManager().login('bedrockuser-x');
    expect(r.success).toBe(false);
    expect(typeof r.error).toBe('string');
    expect(r.error.length).toBeGreaterThan(0);
    expect(Object.keys(r).sort()).toEqual(['error', 'success']);
  });
});

describe('Task 3.5 — IPC 반환 계약 보존 (get-credentials)', () => {
  test('성공 → 정확히 4개 env-var 키 객체', async () => {
    mockFs.configContent = VALID_CONFIG('bedrockuser-c');
    mockCtl.fromSSO = () => async () => ({ accessKeyId: 'AKIA', secretAccessKey: 'sk', sessionToken: 'st' });
    const creds = await new AwsSsoManager().getCredentials('bedrockuser-c');
    expect(creds).not.toBeNull();
    expect(Object.keys(creds).sort()).toEqual([...CRED_KEYS].sort());
    expect(creds.AWS_ACCESS_KEY_ID).toBe('AKIA');
    expect(creds.AWS_DEFAULT_REGION).toBe('us-west-2'); // config에서 보완
  });

  test('실패(자격증명 없음 + CLI 없음) → null', async () => {
    mockFs.configContent = VALID_CONFIG('bedrockuser-c');
    mockCtl.fromSSO = () => async () => { throw new Error('no creds'); };
    const creds = await new AwsSsoManager().getCredentials('bedrockuser-c');
    expect(creds).toBeNull();
  });
});

describe('Task 3.5 — IPC 반환 계약 보존 (get-bedrock-username)', () => {
  test('비이메일 ARN → 단일 후보 문자열 반환', async () => {
    mockFs.configContent = VALID_CONFIG('bedrockuser-c');
    mockCtl.fromSSO = () => async () => ({ accessKeyId: 'AKIA', secretAccessKey: 'sk', sessionToken: 'st' });
    mockCtl.stsSend = async (cmd) => {
      if (cmd.__t === 'GetCallerIdentity') return { Arn: 'arn:aws:iam::123456789012:user/serviceaccount', Account: '123456789012' };
      throw new Error('unexpected');
    };
    const name = await new AwsSsoManager().getBedrockUsername('bedrockuser-c');
    expect(typeof name).toBe('string');
    expect(name).toBe('serviceaccount');
  });

  test('이메일 ARN + assume-role 첫 후보 성공 → 문자열 후보 반환', async () => {
    mockFs.configContent = VALID_CONFIG('bedrockuser-c');
    mockCtl.fromSSO = () => async () => ({ accessKeyId: 'AKIA', secretAccessKey: 'sk', sessionToken: 'st' });
    mockCtl.stsSend = async (cmd) => {
      if (cmd.__t === 'GetCallerIdentity') {
        return { Arn: 'arn:aws:sts::123456789012:assumed-role/Role/changgeun.jang@example.com', Account: '123456789012' };
      }
      if (cmd.__t === 'AssumeRole') return { Credentials: {} }; // 첫 후보 성공
      throw new Error('unexpected');
    };
    const name = await new AwsSsoManager().getBedrockUsername('bedrockuser-c');
    expect(typeof name).toBe('string');
    expect(name).toBe('chjang'); // first[:2]+last
  });

  test('STS 오류 → 빈 문자열(string) 반환 — 계약(string) 보존', async () => {
    mockFs.configContent = VALID_CONFIG('bedrockuser-c');
    mockCtl.fromSSO = () => async () => ({ accessKeyId: 'AKIA', secretAccessKey: 'sk', sessionToken: 'st' });
    mockCtl.stsSend = async () => { throw new Error('STS 실패'); };
    const name = await new AwsSsoManager().getBedrockUsername('bedrockuser-c');
    expect(typeof name).toBe('string');
    expect(name).toBe('');
  });
});
