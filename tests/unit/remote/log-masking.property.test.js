/**
 * Property-based tests for logger.mask() and maskKeys()
 * Feature: remote-ssh
 * Task: 3.2 — Log Masking (Property 19)
 * Validates: Requirements 10.3, 12.5
 *
 * 검증 범위:
 *  - mask(s) for single string: s[0]+'****' (|s| ≥ 2), or '****' (|s| < 2, non-string)
 *  - maskKeys() 재귀 mask 적용: sensitive key 자동 탐지
 *  - 임의 입력 시퀀스 → mask() / maskKeys() → 민감정보 제거
 *  - 로그 무결성: 마스킹 전후 다른 정보 유지
 *
 * 라이브러리: fast-check (design.md Testing Strategy)
 * 최소 실행: 100회 (설계 요구)
 */

const fc = require('fast-check');
const { mask, maskKeys, DEFAULT_SENSITIVE_KEYS } = require('../../../electron/src/remote/logger');

const MIN_RUNS = 100;

// ── 생성기 ────────────────────────────────────────────────────
// 민감정보 유형 (tokens, passwords, keys 등) 시뮬레이션
const sensitiveStringArbitrary = () =>
  fc
    .tuple(
      fc.constantFrom('token', 'password', 'key', 'secret', 'apikey', 'credential'),
      fc.integer({ min: 5, max: 50 })
    )
    .map(([prefix, len]) => `${prefix}_${fc.sample(fc.hexaDecimal(), len).join('')}`);

// 짧은 문자열 (< 2자)
const shortStringArbitrary = () => fc.oneof(
  fc.constant(''),
  fc.string({ maxLength: 1 })
);

// 임의 입력 (mask 견고성 검증용)
const arbitraryInputArbitrary = () =>
  fc.oneof(
    fc.string(),
    fc.integer(),
    fc.float(),
    fc.boolean(),
    fc.constant(null),
    fc.constant(undefined),
    fc.array(fc.string())
  );

// HostEntry 같은 객체 (깊은 마스킹 검증)
const hostEntryArbitrary = () =>
  fc.record({
    alias: fc.string({ minLength: 1 }),
    hostname: fc.ipV4(),
    username: fc.string({ minLength: 1 }),
    password: fc.string({ minLength: 5 }),
    privateKey: fc.string({ minLength: 10 }),
    port: fc.integer({ min: 1, max: 65535 }),
  });

// 중첩된 객체 (재귀 마스킹)
const nestedObjectArbitrary = () =>
  fc.record({
    user: fc.string({ minLength: 1 }),
    auth: fc.record({
      token: fc.string({ minLength: 8 }),
      apitoken: fc.string({ minLength: 8 }),
      secret: fc.string({ minLength: 8 }),
    }),
    metadata: fc.record({
      timestamp: fc.integer({ min: 1000000000000, max: Date.now() }),
      level: fc.constantFrom('info', 'warn', 'error'),
    }),
  });

// ===========================================================================
// Property 19: mask() 함수 형태 검증
// Validates: Requirements 10.3 (sensitive-info masking)
// ===========================================================================
describe('Property 19: mask() 함수 형태 검증 — Validates Requirements 10.3', () => {
  test('mask(s) with |s| ≥ 2: 첫 글자 + "****" 형태', () => {
    fc.assert(
      fc.property(fc.string({ minLength: 2, maxLength: 100 }), (s) => {
        const result = mask(s);
        expect(result).toBe(s[0] + '****');
        expect(result).toHaveLength(5); // 1 char + "****" (4 chars)
      }),
      { numRuns: MIN_RUNS }
    );
  });

  test('mask(s) with |s| < 2: 항상 "****" 반환', () => {
    fc.assert(
      fc.property(shortStringArbitrary(), (s) => {
        const result = mask(s);
        expect(result).toBe('****');
      }),
      { numRuns: MIN_RUNS }
    );
  });

  test('mask(non-string) 견고성: "****" 반환 (null, undefined, number, etc.)', () => {
    fc.assert(
      fc.property(arbitraryInputArbitrary(), (input) => {
        if (typeof input === 'string') return true; // skip string case (other tests cover it)
        const result = mask(input);
        expect(result).toBe('****');
        expect(typeof result).toBe('string');
      }),
      { numRuns: MIN_RUNS }
    );
  });

  test('mask() 출력 항상 문자열 (never null/undefined/Error)', () => {
    fc.assert(
      fc.property(arbitraryInputArbitrary(), (input) => {
        const result = mask(input);
        expect(typeof result).toBe('string');
        expect(result.length).toBeGreaterThan(0);
      }),
      { numRuns: MIN_RUNS }
    );
  });
});

// ===========================================================================
// Property 20: maskKeys() 재귀 마스킹 불변
// Validates: Requirements 12.5 (object-level masking)
// ===========================================================================
describe('Property 20: maskKeys() 재귀 마스킹 불변 — Validates Requirements 12.5', () => {
  test('maskKeys() sensitive keys 자동 감지 및 마스킹', () => {
    fc.assert(
      fc.property(hostEntryArbitrary(), (entry) => {
        const masked = maskKeys(entry);

        // password, privateKey 필드는 mask() 형태여야 함
        if (entry.password && typeof entry.password === 'string') {
          expect(masked.password).toMatch(/^.+\*{4}$|^\*{4}$/);
        }
        if (entry.privateKey && typeof entry.privateKey === 'string') {
          expect(masked.privateKey).toMatch(/^.+\*{4}$|^\*{4}$/);
        }

        // 민감하지 않은 필드는 그대로 유지
        expect(masked.alias).toBe(entry.alias);
        expect(masked.hostname).toBe(entry.hostname);
        expect(masked.port).toBe(entry.port);
      }),
      { numRuns: MIN_RUNS }
    );
  });

  test('maskKeys() 깊은 재귀 마스킹: 중첩 객체', () => {
    fc.assert(
      fc.property(nestedObjectArbitrary(), (obj) => {
        const masked = maskKeys(obj);

        // 최상위 user 필드는 유지
        expect(masked.user).toBe(obj.user);

        // auth 객체의 민감 필드들 모두 마스킹됨
        expect(masked.auth.token).toMatch(/^.+\*{4}$|^\*{4}$/);
        expect(masked.auth.apitoken).toMatch(/^.+\*{4}$|^\*{4}$/);
        expect(masked.auth.secret).toMatch(/^.+\*{4}$|^\*{4}$/);

        // metadata는 그대로 유지
        expect(masked.metadata.timestamp).toBe(obj.metadata.timestamp);
        expect(masked.metadata.level).toBe(obj.metadata.level);
      }),
      { numRuns: MIN_RUNS }
    );
  });

  test('maskKeys() 원본 객체 불변: deep clone 반환', () => {
    fc.assert(
      fc.property(nestedObjectArbitrary(), (obj) => {
        const original = JSON.parse(JSON.stringify(obj)); // deep copy for comparison
        const masked = maskKeys(obj);

        // 원본은 변경되지 않음
        expect(obj).toEqual(original);

        // 반환값은 새 객체
        expect(masked).not.toBe(obj);
        if (obj.auth && masked.auth) {
          expect(masked.auth).not.toBe(obj.auth);
        }
      }),
      { numRuns: MIN_RUNS }
    );
  });

  test('maskKeys() 대소문자 무시 감지: "PASSWORD", "Token" 등도 마스킹', () => {
    fc.assert(
      fc.property(fc.record({ PASSWORD: fc.string({ minLength: 8 }) }), (obj) => {
        const masked = maskKeys(obj);
        expect(masked.PASSWORD).toMatch(/^.+\*{4}$|^\*{4}$/);
      }),
      { numRuns: MIN_RUNS }
    );
  });
});

// ===========================================================================
// Property 21: 순환 참조 안전성
// Validates: Requirements 10.3 (no hang/crash on circular input)
// ===========================================================================
describe('Property 21: maskKeys() 순환 참조 안전성 — Validates Requirements 10.3', () => {
  test('maskKeys() 순환 참조 감지: [Circular] 마크 반환', () => {
    const obj = { user: 'test', secrets: { token: 'abc123' } };
    obj.self = obj; // 순환 참조 추가

    const masked = maskKeys(obj);

    // 마스킹은 성공해야 함 (무한 루프 없음)
    expect(masked).toBeDefined();
    expect(masked.user).toBe('test');
    // 순환 참조는 '[Circular]' 문자열로 반환되므로 string 타입
    expect(masked.self).toBe('[Circular]');
  });

  test('maskKeys() 배열 요소 마스킹', () => {
    fc.assert(
      fc.property(fc.array(hostEntryArbitrary(), { minLength: 1, maxLength: 5 }), (entries) => {
        const masked = maskKeys({ items: entries });

        expect(Array.isArray(masked.items)).toBe(true);
        expect(masked.items.length).toBe(entries.length);

        // 각 배열 항목의 민감 필드 마스킹됨
        for (let i = 0; i < masked.items.length; i++) {
          if (entries[i].password && typeof entries[i].password === 'string') {
            expect(masked.items[i].password).toMatch(/^.+\*{4}$|^\*{4}$/);
          }
        }
      }),
      { numRuns: MIN_RUNS }
    );
  });
});

// ===========================================================================
// Property 22: 임의 키셋 vs 기본 키셋
// Validates: Requirements 10.3 (customizable sensitive keys)
// ===========================================================================
describe('Property 22: maskKeys() 커스텀 키셋 — Validates Requirements 10.3', () => {
  test('maskKeys(obj, customKeys) — 지정한 키만 마스킹', () => {
    const obj = {
      email: 'user@example.com',
      ssn: '123-45-6789',
      phone: '555-1234',
      password: 'secret123',
    };

    const customKeys = ['ssn', 'phone'];
    const masked = maskKeys(obj, customKeys);

    // 기본 DEFAULT_SENSITIVE_KEYS에는 email/password가 있지만,
    // customKeys로 덮어쓰면 ssn/phone만 마스킹됨
    expect(masked.email).toBe(obj.email); // not masked
    expect(masked.password).toBe(obj.password); // not masked
    expect(masked.ssn).toMatch(/^.+\*{4}$|^\*{4}$/); // masked
    expect(masked.phone).toMatch(/^.+\*{4}$|^\*{4}$/); // masked
  });

  test('maskKeys(obj, []) — 빈 키셋 = 마스킹 없음', () => {
    fc.assert(
      fc.property(hostEntryArbitrary(), (entry) => {
        const masked = maskKeys(entry, []);

        // 빈 키셋이면 아무것도 마스킹 안 됨
        expect(masked.password).toBe(entry.password);
        expect(masked.privateKey).toBe(entry.privateKey);
        expect(masked.alias).toBe(entry.alias);
      }),
      { numRuns: MIN_RUNS }
    );
  });
});

// ===========================================================================
// Property 23: 로그 출력 맥락 — 마스킹 후 다른 정보는 유지
// Validates: Requirements 12.2 (log integrity post-masking)
// ===========================================================================
describe('Property 23: 로그 무결성 — 마스킹 외 정보는 보존 — Validates Requirements 12.2', () => {
  test('로그 레코드 구조: ts, level, event, masked fields 모두 존재', () => {
    fc.assert(
      fc.property(
        fc.record({
          level: fc.constantFrom('info', 'warn', 'error'),
          event: fc.string({ minLength: 1, maxLength: 50 }),
          token: fc.string({ minLength: 5 }),
          userId: fc.uuid(),
          timestamp: fc.integer({ min: 1000000000000, max: Date.now() }),
        }),
        (fields) => {
          const masked = maskKeys(fields, DEFAULT_SENSITIVE_KEYS);

          // 민감하지 않은 필드는 보존
          expect(masked.level).toBe(fields.level);
          expect(masked.event).toBe(fields.event);
          expect(masked.userId).toBe(fields.userId);
          expect(masked.timestamp).toBe(fields.timestamp);

          // 민감 필드는 마스킹
          expect(masked.token).toMatch(/^.+\*{4}$|^\*{4}$/);
        }
      ),
      { numRuns: MIN_RUNS }
    );
  });
});
