/**
 * Property-based tests for resolveDefaultSsoPreset — 조직 기본 SSO 프리셋 해석기.
 *
 * Feature: app-deployment-readiness
 * Property: 기본 SSO 프리셋 override 불변
 *
 * Runner: jest (repo "test:unit": "jest tests/unit/")
 * Library: fast-check
 *
 * resolveDefaultSsoPreset(env)는 순수 함수(파일 IO / 네트워크 없음)이므로 직접 import 해
 * 검증한다. 각 프로퍼티는 최소 100회 이상 반복한다.
 *
 * 검증 대상:
 *  (a) env override가 없으면 하드코딩 기본값(DEFAULT_SSO_PRESET)을 그대로 반환한다.
 *  (b) 각 override 키(AE_SSO_*)가 개별적으로 반영된다.
 *  (c) 항상 정확히 5개 문자열 키를 반환하고 예외를 던지지 않는다.
 *  (d) 반환 프리셋을 buildSsoProfileBlock에 넣어도 secret 필드
 *      (aws_access_key_id / aws_secret_access_key)가 절대 나타나지 않는다.
 */

const fc = require('fast-check');
const {
  resolveDefaultSsoPreset,
  buildSsoProfileBlock,
  DEFAULT_SSO_PRESET,
  SSO_PRESET_ENV_KEYS,
} = require('../../electron/core/aws-sso-manager');

const NUM_RUNS = 200; // >= 100 required by the spec

const PRESET_FIELDS = ['name', 'startUrl', 'region', 'accountId', 'roleName'];

// 프리셋 값에 안전한 문자열: 개행/제어문자 없이 임의의 문자열.
const overrideValue = fc.string({ minLength: 1, maxLength: 40 })
  .filter((s) => s.trim() !== '' && !/[\r\n]/.test(s));

// override로 무시되어야 하는 "빈/비문자열" 값들.
const emptyish = fc.oneof(
  fc.constant(undefined),
  fc.constant(''),
  fc.constant('   '),
  fc.constant('\t'),
  fc.constant(null),
  fc.integer(),
  fc.boolean(),
);

// ---------------------------------------------------------------------------
// Feature: app-deployment-readiness, Property: 기본 SSO 프리셋 override 불변 — (a)
// env override가 전혀 없으면 하드코딩 기본값을 반환한다.
// ---------------------------------------------------------------------------
describe('Property (a): override 없으면 하드코딩 기본값', () => {
  test('빈 env / override 키 부재 → DEFAULT_SSO_PRESET 그대로', () => {
    fc.assert(
      fc.property(
        // AE_SSO_* 이외의 무관한 env 키만 포함하는 임의 맵.
        fc.dictionary(
          fc.string().filter((k) => !Object.values(SSO_PRESET_ENV_KEYS).includes(k)),
          fc.string(),
        ),
        (unrelatedEnv) => {
          const preset = resolveDefaultSsoPreset(unrelatedEnv);
          expect(preset).toEqual({ ...DEFAULT_SSO_PRESET });
        },
      ),
      { numRuns: NUM_RUNS },
    );
  });

  test('인자 없이 호출(process.env)해도 예외 없이 5개 키 반환', () => {
    const preset = resolveDefaultSsoPreset();
    expect(Object.keys(preset).sort()).toEqual([...PRESET_FIELDS].sort());
  });

  test.each([undefined, null, 42, 'str', true])('비객체 env(%p) → 기본값', (badEnv) => {
    expect(resolveDefaultSsoPreset(badEnv)).toEqual({ ...DEFAULT_SSO_PRESET });
  });
});

// ---------------------------------------------------------------------------
// Feature: app-deployment-readiness, Property: 기본 SSO 프리셋 override 불변 — (b)
// 각 override 키가 개별적으로 반영되고, 나머지 필드는 기본값을 유지한다.
// ---------------------------------------------------------------------------
describe('Property (b): 각 override 키 개별 반영', () => {
  test('단일 필드 override → 해당 필드만 바뀌고 나머지는 기본값', () => {
    fc.assert(
      fc.property(
        fc.constantFrom(...PRESET_FIELDS),
        overrideValue,
        (field, value) => {
          const env = { [SSO_PRESET_ENV_KEYS[field]]: value };
          const preset = resolveDefaultSsoPreset(env);
          // 대상 필드는 trim된 override 값.
          expect(preset[field]).toBe(value.trim());
          // 나머지 필드는 하드코딩 기본값 유지.
          for (const other of PRESET_FIELDS) {
            if (other === field) continue;
            expect(preset[other]).toBe(DEFAULT_SSO_PRESET[other]);
          }
        },
      ),
      { numRuns: NUM_RUNS },
    );
  });

  test('전 필드 동시 override → 모두 반영', () => {
    fc.assert(
      fc.property(
        fc.record({
          name: overrideValue,
          startUrl: overrideValue,
          region: overrideValue,
          accountId: overrideValue,
          roleName: overrideValue,
        }),
        (vals) => {
          const env = {};
          for (const field of PRESET_FIELDS) env[SSO_PRESET_ENV_KEYS[field]] = vals[field];
          const preset = resolveDefaultSsoPreset(env);
          for (const field of PRESET_FIELDS) {
            expect(preset[field]).toBe(vals[field].trim());
          }
        },
      ),
      { numRuns: NUM_RUNS },
    );
  });

  test('빈/비문자열 override 값은 무시되고 기본값 유지', () => {
    fc.assert(
      fc.property(
        fc.constantFrom(...PRESET_FIELDS),
        emptyish,
        (field, value) => {
          const env = { [SSO_PRESET_ENV_KEYS[field]]: value };
          const preset = resolveDefaultSsoPreset(env);
          expect(preset[field]).toBe(DEFAULT_SSO_PRESET[field]);
        },
      ),
      { numRuns: NUM_RUNS },
    );
  });
});

// ---------------------------------------------------------------------------
// Feature: app-deployment-readiness, Property: 기본 SSO 프리셋 override 불변 — (c)
// 임의 env(임의 키/값 조합)에 대해 항상 정확히 5개 문자열 키 반환·예외 없음.
// ---------------------------------------------------------------------------
describe('Property (c): 항상 5개 문자열 키 반환·예외 없음', () => {
  test('임의 env → 정확히 5개 문자열 키, throw 없음', () => {
    fc.assert(
      fc.property(
        fc.dictionary(fc.string(), fc.oneof(fc.string(), fc.integer(), fc.boolean(), fc.constant(null))),
        (env) => {
          let preset;
          expect(() => { preset = resolveDefaultSsoPreset(env); }).not.toThrow();
          expect(Object.keys(preset).sort()).toEqual([...PRESET_FIELDS].sort());
          for (const field of PRESET_FIELDS) {
            expect(typeof preset[field]).toBe('string');
          }
        },
      ),
      { numRuns: NUM_RUNS },
    );
  });
});

// ---------------------------------------------------------------------------
// Feature: app-deployment-readiness, Property: 기본 SSO 프리셋 override 불변 — (d)
// 반환 프리셋을 buildSsoProfileBlock에 넣어도 secret 필드가 절대 나타나지 않는다.
// ---------------------------------------------------------------------------
describe('Property (d): 프리셋 → buildSsoProfileBlock은 secret-free', () => {
  test('임의 override로 만든 프리셋의 ini 블록에 credential 키 없음', () => {
    fc.assert(
      fc.property(
        fc.dictionary(
          fc.constantFrom(...Object.values(SSO_PRESET_ENV_KEYS)),
          overrideValue,
        ),
        (env) => {
          const preset = resolveDefaultSsoPreset(env);
          const block = buildSsoProfileBlock(preset).toLowerCase();
          expect(block.includes('aws_access_key_id')).toBe(false);
          expect(block.includes('aws_secret_access_key')).toBe(false);
          expect(block.includes('aws_session_token')).toBe(false);
          // SSO 메타데이터 키는 존재해야 한다(형식 온전성 확인).
          expect(block.includes('sso_start_url')).toBe(true);
          expect(block.includes('sso_account_id')).toBe(true);
        },
      ),
      { numRuns: NUM_RUNS },
    );
  });
});
