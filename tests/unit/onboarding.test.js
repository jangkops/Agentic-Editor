/**
 * Unit tests for the onboarding flow (task 6.4).
 *
 * Validates: Requirements 4.5
 *
 * Two testable seams are covered:
 *
 *  1. Onboarding input validation rules. The validate() logic lives INLINE in
 *     the renderer (src/main.js `showOnboardingDialog`) and is not exported, so
 *     the SAME regex rules are replicated here verbatim (see NOTE below). If the
 *     renderer rules change, these mirrored rules must be updated in lockstep.
 *
 *  2. AwsSsoManager.writeSsoProfile() result shapes — missing input, duplicate
 *     profile, and permission (write) errors. These are exercised against the
 *     REAL method with filesystem calls stubbed, so no real ~/.aws/config is
 *     touched. The permission-error path must return
 *     { success:false, error, manualHint } where manualHint is a secret-free
 *     ini block (R4.5 / R4.6).
 *
 * Runner: jest (repo "test:unit": "jest tests/unit/")
 */

const fs = require('fs');
const { AwsSsoManager } = require('../../electron/core/aws-sso-manager');

// ---------------------------------------------------------------------------
// NOTE: mirror of the inline validate() in src/main.js showOnboardingDialog().
// Kept byte-for-byte in sync with the renderer rules (startUrl format,
// accountId 12 digits, region pattern, name charset, roleName charset).
// Returns an error string, or null when the input is valid.
// ---------------------------------------------------------------------------
function validate(v) {
  if (!v.name || !/^[A-Za-z0-9._-]+$/.test(v.name)) return '프로파일 이름을 입력하세요 (영문/숫자/._- 만 허용)';
  if (!/^https?:\/\/.+/.test(v.startUrl)) return 'SSO Start URL 형식이 올바르지 않습니다 (https:// 로 시작)';
  if (!/^[a-z]{2}-[a-z]+-\d+$/.test(v.region)) return 'Region 형식이 올바르지 않습니다 (예: us-west-2)';
  if (!/^\d{12}$/.test(v.accountId)) return 'Account ID는 숫자 12자리여야 합니다';
  if (!/^[\w+=,.@-]+$/.test(v.roleName)) return 'Role 이름을 입력하세요';
  return null;
}

const validInput = () => ({
  name: 'bedrockuser-cgjang',
  startUrl: 'https://my-sso.awsapps.com/start',
  region: 'us-west-2',
  accountId: '123456789012',
  roleName: 'PowerUserAccess',
});

describe('온보딩 입력 검증 (validate rules, mirrored from src/main.js)', () => {
  test('완전히 유효한 입력은 null(오류 없음)을 반환한다', () => {
    expect(validate(validInput())).toBeNull();
  });

  describe('name (프로파일 이름) charset', () => {
    test.each([
      ['영문/숫자/._- 조합', 'Prof_ile.1-2', true],
      ['단일 문자', 'a', true],
    ])('%s → 통과', (_desc, name) => {
      expect(validate({ ...validInput(), name })).toBeNull();
    });

    test.each([
      ['빈 문자열', ''],
      ['공백 포함', 'my profile'],
      ['슬래시 포함', 'my/profile'],
      ['골뱅이 포함', 'user@corp'],
    ])('%s → 오류', (_desc, name) => {
      expect(validate({ ...validInput(), name })).toMatch(/프로파일 이름/);
    });
  });

  describe('startUrl 형식', () => {
    test.each([
      ['https', 'https://x.awsapps.com/start'],
      ['http', 'http://localhost:8080/start'],
    ])('%s → 통과', (_desc, startUrl) => {
      expect(validate({ ...validInput(), startUrl })).toBeNull();
    });

    test.each([
      ['스킴 없음', 'my-sso.awsapps.com/start'],
      ['ftp 스킴', 'ftp://x/start'],
      ['빈 문자열', ''],
      ['https:// 뒤 내용 없음', 'https://'],
    ])('%s → 오류', (_desc, startUrl) => {
      expect(validate({ ...validInput(), startUrl })).toMatch(/Start URL/);
    });
  });

  describe('region 패턴', () => {
    test.each([
      ['us-west-2', 'us-west-2'],
      ['ap-northeast-1', 'ap-northeast-1'],
      ['eu-central-1', 'eu-central-1'],
    ])('%s → 통과', (_desc, region) => {
      expect(validate({ ...validInput(), region })).toBeNull();
    });

    test.each([
      ['대문자', 'US-WEST-2'],
      ['접미 숫자 없음', 'us-west'],
      ['빈 문자열', ''],
      ['공백', 'us west 2'],
    ])('%s → 오류', (_desc, region) => {
      expect(validate({ ...validInput(), region })).toMatch(/Region/);
    });
  });

  describe('accountId 12자리 숫자', () => {
    test('정확히 12자리 숫자 → 통과', () => {
      expect(validate({ ...validInput(), accountId: '000000000001' })).toBeNull();
    });

    test.each([
      ['11자리', '12345678901'],
      ['13자리', '1234567890123'],
      ['문자 포함', '12345678901a'],
      ['빈 문자열', ''],
    ])('%s → 오류', (_desc, accountId) => {
      expect(validate({ ...validInput(), accountId })).toMatch(/Account ID/);
    });
  });

  describe('roleName charset', () => {
    test.each([
      ['영숫자', 'PowerUserAccess'],
      ['특수문자 조합', 'role+name=x,y.z@-_'],
    ])('%s → 통과', (_desc, roleName) => {
      expect(validate({ ...validInput(), roleName })).toBeNull();
    });

    test.each([
      ['빈 문자열', ''],
      ['공백 포함', 'Power User'],
      ['슬래시 포함', 'path/role'],
    ])('%s → 오류', (_desc, roleName) => {
      expect(validate({ ...validInput(), roleName })).toMatch(/Role 이름/);
    });
  });

  test('첫 실패 규칙이 우선한다 (name → startUrl 순서)', () => {
    const bad = { ...validInput(), name: '', startUrl: 'not-a-url' };
    expect(validate(bad)).toMatch(/프로파일 이름/);
  });
});

describe('AwsSsoManager.writeSsoProfile 결과 계약', () => {
  let mgr;
  let spies;

  beforeEach(() => {
    mgr = new AwsSsoManager();
    spies = [];
  });

  afterEach(() => {
    spies.forEach((s) => s.mockRestore());
    jest.restoreAllMocks();
  });

  const spyOn = (obj, method, impl) => {
    const s = jest.spyOn(obj, method).mockImplementation(impl);
    spies.push(s);
    return s;
  };

  test('필수 입력 누락 → { success:false, error } (config 미접근)', () => {
    // No fs stubs needed: the method must bail out before any filesystem call.
    const existsSpy = spyOn(fs, 'existsSync', () => {
      throw new Error('validation should return before touching the filesystem');
    });

    const r = mgr.writeSsoProfile({ name: 'x' }); // startUrl/region/accountId/roleName missing
    expect(r.success).toBe(false);
    expect(typeof r.error).toBe('string');
    expect(r.error).toMatch(/필수 입력이 누락/);
    expect(r.error).toMatch(/startUrl/);
    expect(existsSpy).not.toHaveBeenCalled();
  });

  test('중복 프로파일명 → { success:false, duplicate:true }', () => {
    const input = validInput();
    // config already exists and already contains [profile <name>].
    spyOn(fs, 'existsSync', () => true);
    spyOn(fs, 'readFileSync', () => `[profile ${input.name}]\nsso_region = us-west-2\n`);
    const appendSpy = spyOn(fs, 'appendFileSync', () => {
      throw new Error('must not append when profile already exists');
    });

    const r = mgr.writeSsoProfile(input);
    expect(r.success).toBe(false);
    expect(r.duplicate).toBe(true);
    expect(r.profile).toBe(input.name);
    expect(typeof r.error).toBe('string');
    expect(appendSpy).not.toHaveBeenCalled();
  });

  test('쓰기 권한 오류 → { success:false, error, manualHint } (secret-free)', () => {
    const input = validInput();
    // Target config does not exist yet -> no duplicate, fresh create path.
    spyOn(fs, 'existsSync', () => false);
    spyOn(fs, 'mkdirSync', () => undefined);
    spyOn(fs, 'appendFileSync', () => {
      const err = new Error("EACCES: permission denied, open '.../.aws/config'");
      err.code = 'EACCES';
      throw err;
    });

    const r = mgr.writeSsoProfile(input);
    expect(r.success).toBe(false);
    expect(typeof r.error).toBe('string');
    expect(r.error).toMatch(/쓰기에 실패/);
    // R4.5: manual configuration hint is provided.
    expect(typeof r.manualHint).toBe('string');
    expect(r.manualHint).toContain(`[profile ${input.name}]`);
    expect(r.manualHint).toContain(`sso_start_url = ${input.startUrl}`);
    expect(r.manualHint).toContain(`sso_account_id = ${input.accountId}`);

    // R4.6: the hint must be secret-free — no credential keys.
    const lower = r.manualHint.toLowerCase();
    expect(lower).not.toContain('aws_access_key_id');
    expect(lower).not.toContain('aws_secret_access_key');
    expect(lower).not.toContain('aws_session_token');
  });

  test('정상 생성 → { success:true, profile } 이고 기록 블록은 secret-free', () => {
    const input = validInput();
    let written = '';
    spyOn(fs, 'existsSync', () => false); // no existing config
    spyOn(fs, 'mkdirSync', () => undefined);
    spyOn(fs, 'appendFileSync', (_p, content) => {
      written += content;
    });

    const r = mgr.writeSsoProfile(input);
    expect(r.success).toBe(true);
    expect(r.profile).toBe(input.name);

    // Written block contains only SSO metadata, never credentials.
    expect(written).toContain(`[profile ${input.name}]`);
    const lower = written.toLowerCase();
    expect(lower).not.toContain('aws_access_key_id');
    expect(lower).not.toContain('aws_secret_access_key');
  });
});
