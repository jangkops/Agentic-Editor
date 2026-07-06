/**
 * Property-based tests for pure helpers in electron/core/aws-sso-manager.js
 * Feature: app-deployment-readiness
 *
 * 대상 순수 헬퍼:
 *   - mapCredentials            (Property 1 — 자격증명 매핑 형태 불변)
 *   - sortProfiles              (Property 3 — 프로파일 정렬 불변)
 *   - buildBedrockUserCandidates(Property 4 — BedrockUser ARN 파싱 불변)
 *   - buildSsoProfileBlock /
 *     parseSsoProfileBlock      (Property 5 — SSO config 블록 왕복)
 *
 * 라이브러리: fast-check (design.md Testing Strategy 지정). 각 프로퍼티 최소 100회 반복.
 */

const fc = require('fast-check');
const {
  mapCredentials,
  sortProfiles,
  buildBedrockUserCandidates,
  buildSsoProfileBlock,
  parseSsoProfileBlock,
} = require('../../electron/core/aws-sso-manager');

const MIN_RUNS = 200; // 설계 요구(최소 100회) 상회

// ── 공용 생성기 ───────────────────────────────────────────────
// ini/config 안전 문자열: 공백·개행·대괄호·'=' 없음 (round-trip 왕복 보장 도메인).
const SAFE_CHARS = 'abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789:/._-';
const safeString = (opts = {}) =>
  fc
    .array(fc.constantFrom(...SAFE_CHARS), { minLength: opts.minLength ?? 1, maxLength: opts.maxLength ?? 20 })
    .map((a) => a.join(''));

// 소문자 알파 토큰 — 이름 분해/localeCompare 검증용
const alphaToken = (opts = {}) =>
  fc
    .array(fc.constantFrom(...'abcdefghijklmnopqrstuvwxyz'), {
      minLength: opts.minLength ?? 1,
      maxLength: opts.maxLength ?? 10,
    })
    .map((a) => a.join(''));

// ===========================================================================
// Property 1: 자격증명 매핑 형태 불변
// Feature: app-deployment-readiness, Property 1: 자격증명 매핑 형태 불변
// Validates: Requirements 2.2, 2.4
// ===========================================================================
describe('Property 1: 자격증명 매핑 형태 불변 (mapCredentials) — Validates Requirements 2.2, 2.4', () => {
  const EXPECTED_KEYS = [
    'AWS_ACCESS_KEY_ID',
    'AWS_SECRET_ACCESS_KEY',
    'AWS_SESSION_TOKEN',
    'AWS_DEFAULT_REGION',
  ];

  // 일부 필드가 누락될 수 있는 SDK 자격증명 객체 생성기
  const sdkCredsArb = fc.record(
    {
      accessKeyId: fc.string(),
      secretAccessKey: fc.string(),
      sessionToken: fc.string(),
      region: fc.string(),
    },
    { requiredKeys: [] } // 각 키는 선택적 → 일부 누락 케이스 포함
  );

  test('정확히 4개의 AWS_* 키만 가지며, 존재하는 값은 보존·누락은 빈 문자열', () => {
    fc.assert(
      fc.property(sdkCredsArb, (creds) => {
        const out = mapCredentials(creds);

        // (a) 정확히 4개 키, 다른 키 없음
        expect(Object.keys(out).sort()).toEqual([...EXPECTED_KEYS].sort());

        // (b) 값 보존 / 누락 → ''
        const keep = (v) => (v !== undefined && v !== null ? v : '');
        expect(out.AWS_ACCESS_KEY_ID).toBe(keep(creds.accessKeyId));
        expect(out.AWS_SECRET_ACCESS_KEY).toBe(keep(creds.secretAccessKey));
        expect(out.AWS_SESSION_TOKEN).toBe(keep(creds.sessionToken));
        expect(out.AWS_DEFAULT_REGION).toBe(keep(creds.region));

        // (c) 모든 값은 문자열 (누락 → '')
        for (const k of EXPECTED_KEYS) expect(typeof out[k]).toBe('string');
      }),
      { numRuns: MIN_RUNS }
    );
  });

  test('null/undefined/비객체 입력에도 예외 없이 4개 빈 문자열 키 반환', () => {
    for (const bad of [null, undefined, 42, 'str', []]) {
      const out = mapCredentials(bad);
      expect(Object.keys(out).sort()).toEqual([...EXPECTED_KEYS].sort());
      for (const k of EXPECTED_KEYS) expect(out[k]).toBe('');
    }
  });
});

// ===========================================================================
// Property 3: 프로파일 정렬 불변
// Feature: app-deployment-readiness, Property 3: 프로파일 정렬 불변
// Validates: Requirements 2.5
// ===========================================================================
describe('Property 3: 프로파일 정렬 불변 (sortProfiles) — Validates Requirements 2.5', () => {
  // bedrockuser 접두 이름과 임의 이름을 섞어 생성
  const profileNameArb = fc.oneof(
    alphaToken({ maxLength: 8 }).map((s) => `bedrockuser${s}`),
    alphaToken({ maxLength: 12 })
  );
  const profileListArb = fc.array(profileNameArb, { minLength: 0, maxLength: 40 });

  const isBedrock = (n) => n.startsWith('bedrockuser');

  test('bedrockuser* 상단 + 그룹 내 localeCompare + 입력 불변 + 결정적', () => {
    fc.assert(
      fc.property(profileListArb, (names) => {
        const snapshot = names.slice();
        const sorted = sortProfiles(names);

        // (a) 입력 배열 불변
        expect(names).toEqual(snapshot);

        // (b) 원소 보존(멀티셋 동일) — 위조/유실 없음
        expect([...sorted].sort()).toEqual([...names].sort());

        // (c) 모든 bedrockuser* 가 비-bedrockuser* 보다 앞
        let seenNonBedrock = false;
        for (const n of sorted) {
          if (isBedrock(n)) {
            expect(seenNonBedrock).toBe(false); // 비-bedrock 이후에 bedrock 나오면 위반
          } else {
            seenNonBedrock = true;
          }
        }

        // (d) 같은 그룹 내 localeCompare 비내림차순
        for (let i = 1; i < sorted.length; i++) {
          const prev = sorted[i - 1];
          const cur = sorted[i];
          if (isBedrock(prev) === isBedrock(cur)) {
            expect(prev.localeCompare(cur)).toBeLessThanOrEqual(0);
          }
        }

        // (e) 결정적 — 재호출 시 동일 결과
        expect(sortProfiles(names)).toEqual(sorted);
      }),
      { numRuns: MIN_RUNS }
    );
  });

  test('예제: bedrockuser 그룹 우선 + 사전순', () => {
    expect(sortProfiles(['zeta', 'bedrockuser-b', 'alpha', 'bedrockuser-a'])).toEqual([
      'bedrockuser-a',
      'bedrockuser-b',
      'alpha',
      'zeta',
    ]);
  });
});

// ===========================================================================
// Property 4: BedrockUser ARN 파싱 불변
// Feature: app-deployment-readiness, Property 4: BedrockUser ARN 파싱 불변
// Validates: Requirements 2.3
// ===========================================================================
describe('Property 4: BedrockUser ARN 파싱 불변 (buildBedrockUserCandidates) — Validates Requirements 2.3', () => {
  test('임의 ARN 유사 문자열: 항상 비어있지 않은 문자열 배열, 예외 없음', () => {
    fc.assert(
      fc.property(fc.string(), (s) => {
        const out = buildBedrockUserCandidates(s);
        expect(Array.isArray(out)).toBe(true);
        expect(out.length).toBeGreaterThanOrEqual(1);
        for (const c of out) expect(typeof c).toBe('string');
      }),
      { numRuns: MIN_RUNS }
    );
  });

  test('비문자열/비정상 입력에도 예외 없이 문자열 배열 반환', () => {
    for (const bad of [null, undefined, 42, {}, []]) {
      const out = buildBedrockUserCandidates(bad);
      expect(Array.isArray(out)).toBe(true);
      expect(out.length).toBeGreaterThanOrEqual(1);
    }
  });

  test('이메일 first.last → [first[:2]+last, first[:1]+last, first[:3]+last, first+last]', () => {
    fc.assert(
      fc.property(
        alphaToken({ minLength: 1, maxLength: 8 }),
        alphaToken({ minLength: 1, maxLength: 8 }),
        alphaToken({ minLength: 1, maxLength: 6 }), // domain
        fc.oneof(fc.constant(''), fc.constant('arn:aws:sts::123456789012:assumed-role/Role/')),
        (first, last, domain, arnPrefix) => {
          const arn = `${arnPrefix}${first}.${last}@${domain}.com`;
          const out = buildBedrockUserCandidates(arn);
          expect(out).toEqual([
            first.slice(0, 2) + last,
            first.slice(0, 1) + last,
            first.slice(0, 3) + last,
            first + last,
          ]);
        }
      ),
      { numRuns: MIN_RUNS }
    );
  });

  test('비이메일 식별자 → [identifier] (마지막 / 세그먼트)', () => {
    fc.assert(
      fc.property(
        alphaToken({ minLength: 1, maxLength: 12 }),
        fc.oneof(fc.constant(''), fc.constant('arn:aws:iam::123456789012:user/')),
        (identifier, arnPrefix) => {
          const arn = `${arnPrefix}${identifier}`;
          const out = buildBedrockUserCandidates(arn);
          expect(out).toEqual([identifier]);
        }
      ),
      { numRuns: MIN_RUNS }
    );
  });

  test('예제: 결정적 동작', () => {
    const arn = 'arn:aws:sts::111:assumed-role/Role/changgeun.jang@example.com';
    const a = buildBedrockUserCandidates(arn);
    const b = buildBedrockUserCandidates(arn);
    expect(a).toEqual(b);
    expect(a).toEqual(['chjang', 'cjang', 'chajang', 'changgeunjang']);
  });
});

// ===========================================================================
// Property 5: SSO config 블록 왕복(round-trip)
// Feature: app-deployment-readiness, Property 5: SSO config 블록 왕복
// Validates: Requirements 4.2
// ===========================================================================
describe('Property 5: SSO config 블록 왕복 (build/parseSsoProfileBlock) — Validates Requirements 4.2', () => {
  const onboardingInputArb = fc.record({
    name: safeString({ minLength: 1, maxLength: 20 }),
    startUrl: safeString({ minLength: 1, maxLength: 40 }),
    region: safeString({ minLength: 1, maxLength: 20 }),
    accountId: safeString({ minLength: 1, maxLength: 20 }),
    roleName: safeString({ minLength: 1, maxLength: 20 }),
  });

  test('parse(build(x), x.name) === {startUrl, region, accountId, roleName}', () => {
    fc.assert(
      fc.property(onboardingInputArb, (input) => {
        const block = buildSsoProfileBlock(input);
        const parsed = parseSsoProfileBlock(block, input.name);
        expect(parsed).toEqual({
          startUrl: input.startUrl,
          region: input.region,
          accountId: input.accountId,
          roleName: input.roleName,
        });
      }),
      { numRuns: MIN_RUNS }
    );
  });

  test('생성 블록에 비밀키 필드(aws_access_key_id/aws_secret_access_key)가 절대 없음', () => {
    fc.assert(
      fc.property(onboardingInputArb, (input) => {
        const block = buildSsoProfileBlock(input).toLowerCase();
        expect(block.includes('aws_access_key_id')).toBe(false);
        expect(block.includes('aws_secret_access_key')).toBe(false);
        expect(block.includes('aws_session_token')).toBe(false);
      }),
      { numRuns: MIN_RUNS }
    );
  });

  test('예제: 왕복 복원', () => {
    const input = {
      name: 'bedrockuser-cgjang',
      startUrl: 'https://my-sso.awsapps.com/start',
      region: 'us-west-2',
      accountId: '123456789012',
      roleName: 'BedrockUser',
    };
    const parsed = parseSsoProfileBlock(buildSsoProfileBlock(input), input.name);
    expect(parsed).toEqual({
      startUrl: input.startUrl,
      region: input.region,
      accountId: input.accountId,
      roleName: input.roleName,
    });
  });
});
