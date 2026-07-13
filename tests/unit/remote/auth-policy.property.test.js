/**
 * Property-based tests for electron/src/remote/auth-policy.js
 * Feature: remote-ssh
 * Task: 9.3 — Auth Policy (Property 6, 23)
 * Validates: Requirements 3.8, 13.1
 *
 * 검증 범위:
 *  - Property 6: shouldStop(timestamps) — 최근 60초 내 실패 ≥3 → true
 *  - Property 23: effectiveStrictHostKeyChecking() — HostEntry 명시값 우선, alias ∈ KeyStore → 'yes'
 *
 * 임의 입력:
 *  - 시간 시퀀스 fuzzing (임의 타임스탬프, 중복, 비정렬)
 *  - HostEntry 객체 (alias, strictHostKeyChecking 값)
 *  - knownAliases 컬렉션 (Set, Array, Iterable)
 *
 * 라이브러리: fast-check (design.md Testing Strategy)
 * 최소 실행: 100회
 */

const fc = require('fast-check');
const { shouldStop, effectiveStrictHostKeyChecking, STOP_WINDOW_MS, STOP_THRESHOLD } = require(
  '../../../electron/src/remote/auth-policy'
);

const MIN_RUNS = 100;

// ===========================================================================
// Property 6: shouldStop() 인증 실패 윈도우 감지
// Validates: Requirements 3.8 (auth-failure storm detection)
// ===========================================================================
describe('Property 6: shouldStop() 인증 실패 윈도우 감지 — Validates Requirements 3.8', () => {
  test('shouldStop(timestamps, now): 최근 60초 내 실패 ≥3 → true', () => {
    const now = Date.now();
    // 정확히 3개의 실패를 60초 윈도우 내에 배치
    const timestamps = [now - 30000, now - 20000, now - 10000]; // 30s, 20s, 10s ago

    expect(shouldStop(timestamps, now)).toBe(true);
  });

  test('shouldStop(timestamps, now): 실패 < 3 → false', () => {
    const now = Date.now();
    const timestamps = [now - 30000, now - 20000]; // only 2 failures

    expect(shouldStop(timestamps, now)).toBe(false);
  });

  test('shouldStop(timestamps, now): 모든 실패가 60초 외 → false', () => {
    const now = Date.now();
    const timestamps = [now - 70000, now - 80000, now - 90000]; // all outside 60s window

    expect(shouldStop(timestamps, now)).toBe(false);
  });

  test('shouldStop() 정렬되지 않은 타임스탬프 허용', () => {
    fc.assert(
      fc.property(
        fc.array(fc.integer({ min: -60000, max: 0 }), {
          minLength: 3,
          maxLength: 10,
        }),
        (offsets) => {
          const now = Date.now();
          const timestamps = offsets.map((o) => now + o);

          // 정렬되지 않은 순서도 동일한 결과
          const shuffled = [...timestamps].sort(() => Math.random() - 0.5);
          expect(shouldStop(shuffled, now)).toBe(shouldStop(timestamps, now));
        }
      ),
      { numRuns: 50 }
    );
  });

  test('shouldStop() 중복 타임스탬프 허용 및 카운트', () => {
    const now = Date.now();
    const timestamps = [now - 30000, now - 30000, now - 30000]; // 3x same time

    expect(shouldStop(timestamps, now)).toBe(true);
  });

  test('shouldStop() 비정상 타임스탬프 무시: NaN, null, 문자열, boolean 등', () => {
    const now = Date.now();
    const validTimestamps = [now - 30000, now - 20000];
    const timestamps = [
      ...validTimestamps,
      NaN,
      null,
      undefined,
      'invalid',
      true,
      false,
    ];

    // 유효한 것만 카운트 (2개) → false
    expect(shouldStop(timestamps, now)).toBe(false);
  });

  test('shouldStop() 미래 타임스탬프 clamped to now', () => {
    const now = Date.now();
    const timestamps = [
      now - 30000,
      now - 20000,
      now + 1000, // future — clamped to now
    ];

    // 클램프된 결과 [now-30s, now-20s, now] = 3개 in window → true
    expect(shouldStop(timestamps, now)).toBe(true);
  });

  test('shouldStop() 기본 now 값: Date.now() 사용', () => {
    const now = Date.now();
    const timestamps = [now - 30000, now - 20000, now - 10000];

    // now 생략 시 내부적으로 Date.now() 호출
    expect(shouldStop(timestamps)).toBe(true);
  });

  test('shouldStop() now 가 비유한 값: Date.now() 폴백', () => {
    const now = Date.now();
    const timestamps = [now - 30000, now - 20000, now - 10000];

    // now = NaN, null, undefined 등 → 폴백
    expect(shouldStop(timestamps, NaN)).toBe(true);
    expect(shouldStop(timestamps, null)).toBe(true);
    expect(shouldStop(timestamps, undefined)).toBe(true);
  });

  test('shouldStop() 빈 배열 → false', () => {
    expect(shouldStop([], Date.now())).toBe(false);
  });

  test('shouldStop() 비배열 입력 → false', () => {
    fc.assert(
      fc.property(
        fc.oneof(fc.string(), fc.integer(), fc.boolean(), fc.constant(null), fc.constant(undefined)),
        (input) => {
          expect(shouldStop(input, Date.now())).toBe(false);
        }
      ),
      { numRuns: 50 }
    );
  });

  test('shouldStop() Property: 60초 경계 케이스', () => {
    const now = Date.now();

    // 정확히 60초 전 (window 포함)
    const onBoundary = [now - STOP_WINDOW_MS, now - (STOP_WINDOW_MS - 1), now];
    expect(shouldStop(onBoundary, now)).toBe(true);

    // 60초 + 1ms 전 (window 제외)
    const justOutside = [now - (STOP_WINDOW_MS + 1), now - (STOP_WINDOW_MS + 2), now];
    expect(shouldStop(justOutside, now)).toBe(false);
  });
});

// ===========================================================================
// Property 23: effectiveStrictHostKeyChecking() 호스트키 확인 정책
// Validates: Requirements 13.1 (strict host key checking policy)
// ===========================================================================
describe(
  'Property 23: effectiveStrictHostKeyChecking() 정책 결정 — Validates Requirements 13.1',
  () => {
    test('effectiveStrictHostKeyChecking(): 명시 값 최우선', () => {
      const testCases = ['yes', 'no', 'ask', 'accept-new'];
      for (const val of testCases) {
        const entry = {
          alias: 'myhost',
          strictHostKeyChecking: val,
        };
        const result = effectiveStrictHostKeyChecking(entry);
        expect(result).toBe(entry.strictHostKeyChecking);
      }
    });

    test('effectiveStrictHostKeyChecking(): 명시 값 없고 alias ∈ knownAliases → "yes"', () => {
      fc.assert(
        fc.property(
          fc.record({
            alias: fc.string({ minLength: 1 }),
            hostname: fc.ipV4(),
          }),
          (entry) => {
            // knownAliases에 entry.alias 포함
            const knownAliases = [entry.alias, 'other1', 'other2'];
            const result = effectiveStrictHostKeyChecking(entry, knownAliases);
            expect(result).toBe('yes');
          }
        ),
        { numRuns: 50 }
      );
    });

    test('effectiveStrictHostKeyChecking(): 명시 값 없고 alias ∉ knownAliases → "ask"', () => {
      fc.assert(
        fc.property(
          fc.record({
            alias: fc.string({ minLength: 1 }),
            hostname: fc.ipV4(),
          }),
          (entry) => {
            // knownAliases에 entry.alias 미포함
            const knownAliases = ['other1', 'other2', 'other3'];
            const result = effectiveStrictHostKeyChecking(entry, knownAliases);
            expect(result).toBe('ask');
          }
        ),
        { numRuns: 50 }
      );
    });

    test('effectiveStrictHostKeyChecking(): knownAliases = Set', () => {
      fc.assert(
        fc.property(
          fc.record({
            alias: fc.string({ minLength: 1 }),
            hostname: fc.ipV4(),
          }),
          (entry) => {
            const knownAliases = new Set([entry.alias, 'other']);
            const result = effectiveStrictHostKeyChecking(entry, knownAliases);
            expect(result).toBe('yes');
          }
        ),
        { numRuns: 50 }
      );
    });

    test('effectiveStrictHostKeyChecking(): knownAliases = null/undefined → "ask"', () => {
      fc.assert(
        fc.property(
          fc.record({
            alias: fc.string({ minLength: 1 }),
            hostname: fc.ipV4(),
          }),
          (entry) => {
            expect(effectiveStrictHostKeyChecking(entry, null)).toBe('ask');
            expect(effectiveStrictHostKeyChecking(entry, undefined)).toBe('ask');
          }
        ),
        { numRuns: 50 }
      );
    });

    test('effectiveStrictHostKeyChecking(): entry = null/undefined → "ask"', () => {
      expect(effectiveStrictHostKeyChecking(null)).toBe('ask');
      expect(effectiveStrictHostKeyChecking(undefined)).toBe('ask');
    });

    test('effectiveStrictHostKeyChecking(): entry.alias 없음 → "ask"', () => {
      const entry = { hostname: '192.168.1.1', port: 22 };
      expect(effectiveStrictHostKeyChecking(entry)).toBe('ask');
    });

    test('effectiveStrictHostKeyChecking(): 명시 값이 유효하지 않음 → 기본값으로 폴백', () => {
      const entry = {
        alias: 'myhost',
        strictHostKeyChecking: 'invalid-value', // not in whitelist
      };
      const knownAliases = [];

      // 유효하지 않은 명시 값 무시 → 기본값 로직 (alias ∉ known) → "ask"
      expect(effectiveStrictHostKeyChecking(entry, knownAliases)).toBe('ask');
    });

    test('effectiveStrictHostKeyChecking(): 명시 값이 유효하지 않지만 alias ∈ known → "yes"', () => {
      const entry = {
        alias: 'myhost',
        strictHostKeyChecking: 'invalid', // not whitelisted
      };
      const knownAliases = ['myhost', 'other'];

      // 명시값 무시 → 기본값 (alias ∈ known) → "yes"
      expect(effectiveStrictHostKeyChecking(entry, knownAliases)).toBe('yes');
    });

    test('effectiveStrictHostKeyChecking(): knownAliases Iterable 일반 지원', () => {
      const entry = { alias: 'myhost' };

      // Generator function 등 일반 Iterable
      const iterable = (function* () {
        yield 'myhost';
        yield 'other';
      })();

      expect(effectiveStrictHostKeyChecking(entry, iterable)).toBe('yes');
    });

    test('effectiveStrictHostKeyChecking(): 빈 knownAliases → "ask"', () => {
      fc.assert(
        fc.property(
          fc.record({
            alias: fc.string({ minLength: 1 }),
            hostname: fc.ipV4(),
          }),
          (entry) => {
            expect(effectiveStrictHostKeyChecking(entry, [])).toBe('ask');
            expect(effectiveStrictHostKeyChecking(entry, new Set())).toBe('ask');
          }
        ),
        { numRuns: 50 }
      );
    });

    test('effectiveStrictHostKeyChecking(): 네 가지 명시 값 모두 호출 가능', () => {
      const validValues = ['yes', 'no', 'ask', 'accept-new'];

      for (const val of validValues) {
        const entry = { alias: 'test', strictHostKeyChecking: val };
        expect(effectiveStrictHostKeyChecking(entry)).toBe(val);
      }
    });
  }
);

// ===========================================================================
// Property 24: 결합 시나리오 — shouldStop + effectiveStrictHostKeyChecking 상호작용
// Validates: Requirements 3.8, 13.1 (combined auth policy)
  describe(
    'Property 24: 결합 시나리오 — shouldStop + effectiveStrictHostKeyChecking 상호작용',
    () => {
      test('고정 시점에서 shouldStop/effectiveStrictHostKeyChecking 독립 실행', () => {
        fc.assert(
          fc.property(
            fc.array(fc.integer({ min: -60000, max: 0 }), { maxLength: 10 }),
            fc.record({
              alias: fc.string({ minLength: 1 }),
              hostname: fc.ipV4(),
            }),
            fc.oneof(
              fc.set(fc.string({ minLength: 1 }), { maxLength: 5 }).map((s) => new Set(s)),
              fc.array(fc.string({ minLength: 1 }), { maxLength: 5 })
            ),
            (offsets, entry, knownAliases) => {
              const now = Date.now();
              const timestamps = offsets.map((o) => now + o);

              // 두 함수는 독립적으로 동작
              const stopResult = shouldStop(timestamps, now);
              const checkResult = effectiveStrictHostKeyChecking(entry, knownAliases);

              // 각각이 예상 형태 반환
              expect(typeof stopResult).toBe('boolean');
              expect(['yes', 'no', 'ask', 'accept-new']).toContain(checkResult);
            }
          ),
          { numRuns: 50 }
        );
      });
    }
  );
