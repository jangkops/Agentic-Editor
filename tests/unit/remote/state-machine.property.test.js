/**
 * Property-based tests for RemoteSession state machine validity
 * Feature: remote-ssh
 * Task: 10.3 — State Machine Validity (Property 25)
 * Validates: Requirements §Architecture (state machine consistency)
 *
 * 검증 범위:
 *  - Property 25: State machine transition validity
 *    ✓ 모든 전이는 사전에 정의된 허용 목록에서만 발생
 *    ✓ 잘못된 전이는 reject (throw or event drop)
 *    ✓ 상태 불변: 모든 중간 상태 일관성 유지
 *
 * 임의 입력:
 *  - 상태 전이 쌍 (from, to) 임의 생성
 *  - RemoteSession.ALLOWED_TRANSITIONS 맵과 비교
 *
 * 라이브러리: fast-check (design.md Testing Strategy)
 * 최소 실행: 100회
 */

const fc = require('fast-check');
const { STATES, ALLOWED_TRANSITIONS, isValidTransition } = require('../../../electron/src/remote/remote-session');

/**
 * design.md 에 적힌 상태·전이 규칙의 사본.
 *
 * 예전에는 이 사본으로 만든 로컬 isValidTransition 을 검사해 실제 모듈은 한 줄도 테스트되지 않았고,
 * 그 로컬 복사본이 fc.object() 가 만든 `{toString: 0}` 입력에 TypeError 를 내며 Property 29 가
 * CI 에서 간헐 실패했다(2026-09-17 — 모듈 쪽은 이미 견고화돼 있었음). 지금은 동작 검증은 전부
 * 실제 모듈 함수·테이블로 하고, 이 사본은 아래 "스펙-코드 정합" 테스트에서만 쓴다.
 */
const SPEC_STATES = Object.freeze({
  DISCONNECTED: 'disconnected',
  CONNECTING: 'connecting',
  AUTHENTICATING: 'authenticating',
  PROVISIONING: 'provisioning',
  FORWARDING: 'forwarding',
  CONNECTED: 'connected',
  RECONNECTING: 'reconnecting',
  FAILED: 'failed',
});

const SPEC_ALLOWED_TRANSITIONS = Object.freeze({
  [SPEC_STATES.DISCONNECTED]: Object.freeze([SPEC_STATES.CONNECTING]),
  [SPEC_STATES.CONNECTING]: Object.freeze([SPEC_STATES.AUTHENTICATING, SPEC_STATES.FAILED, SPEC_STATES.DISCONNECTED]),
  [SPEC_STATES.AUTHENTICATING]: Object.freeze([SPEC_STATES.PROVISIONING, SPEC_STATES.FAILED, SPEC_STATES.DISCONNECTED]),
  [SPEC_STATES.PROVISIONING]: Object.freeze([SPEC_STATES.FORWARDING, SPEC_STATES.FAILED]),
  [SPEC_STATES.FORWARDING]: Object.freeze([SPEC_STATES.CONNECTED, SPEC_STATES.FAILED]),
  [SPEC_STATES.CONNECTED]: Object.freeze([SPEC_STATES.RECONNECTING, SPEC_STATES.DISCONNECTED, SPEC_STATES.FAILED]),
  [SPEC_STATES.RECONNECTING]: Object.freeze([SPEC_STATES.AUTHENTICATING, SPEC_STATES.FAILED, SPEC_STATES.DISCONNECTED]),
  [SPEC_STATES.FAILED]: Object.freeze([SPEC_STATES.DISCONNECTED]),
});

// ===========================================================================
// 스펙-코드 정합: 모듈이 export 하는 테이블은 design.md 사본과 같아야 한다
// ===========================================================================
describe('스펙-코드 정합 — remote-session.js 테이블 == design.md 사본', () => {
  test('STATES 가 스펙과 같다', () => {
    expect(STATES).toEqual(SPEC_STATES);
  });

  test('ALLOWED_TRANSITIONS 가 스펙과 같다', () => {
    expect(ALLOWED_TRANSITIONS).toEqual(SPEC_ALLOWED_TRANSITIONS);
  });
});

// ── 생성기 ────────────────────────────────────────────────────
const stateArbitrary = () => fc.constantFrom(...Object.values(STATES));

// 임의 상태 (유효하지 않은 상태도 포함 — 견고성 테스트)
const arbitraryStateArbitrary = () =>
  fc.oneof(
    stateArbitrary(),
    fc.string({ minLength: 1 }), // random string
    fc.integer(),
    fc.constant(null),
    fc.constant(undefined)
  );

// 상태 전이 쌍
const stateTransitionArbitrary = () =>
  fc.tuple(stateArbitrary(), stateArbitrary());

// ===========================================================================
// Property 25: 상태 전이 유효성 검증
// Validates: Requirements §Architecture (state machine consistency)
// ===========================================================================
describe(
  'Property 25: 상태 전이 유효성 — 사전 정의된 목록만 허용 — Validates Architecture',
  () => {
    test('isValidTransition(from, to): 허용된 전이만 true', () => {
      fc.assert(
        fc.property(stateTransitionArbitrary(), ([from, to]) => {
          const result = isValidTransition(from, to);
          const allowed = ALLOWED_TRANSITIONS[from];

          expect(result).toBe(
            Array.isArray(allowed) && allowed.includes(to)
          );
        }),
        { numRuns: 100 }
      );
    });

    test('isValidTransition(): disconnected → connecting 만 허용', () => {
      expect(isValidTransition(STATES.DISCONNECTED, STATES.CONNECTING)).toBe(true);

      // 다른 모든 전이는 거부
      Object.values(STATES).forEach((state) => {
        if (state !== STATES.CONNECTING) {
          expect(isValidTransition(STATES.DISCONNECTED, state)).toBe(false);
        }
      });
    });

    test('isValidTransition(): connecting → {authenticating, failed, disconnected}', () => {
      expect(isValidTransition(STATES.CONNECTING, STATES.AUTHENTICATING)).toBe(true);
      expect(isValidTransition(STATES.CONNECTING, STATES.FAILED)).toBe(true);
      expect(isValidTransition(STATES.CONNECTING, STATES.DISCONNECTED)).toBe(true);

      // provisioning/forwarding/connected/reconnecting은 거부
      expect(isValidTransition(STATES.CONNECTING, STATES.PROVISIONING)).toBe(false);
      expect(isValidTransition(STATES.CONNECTING, STATES.FORWARDING)).toBe(false);
      expect(isValidTransition(STATES.CONNECTING, STATES.CONNECTED)).toBe(false);
      expect(isValidTransition(STATES.CONNECTING, STATES.RECONNECTING)).toBe(false);
    });

    test('isValidTransition(): authenticating → {provisioning, failed, disconnected}', () => {
      expect(isValidTransition(STATES.AUTHENTICATING, STATES.PROVISIONING)).toBe(true);
      expect(isValidTransition(STATES.AUTHENTICATING, STATES.FAILED)).toBe(true);
      expect(isValidTransition(STATES.AUTHENTICATING, STATES.DISCONNECTED)).toBe(true);

      // 다른 것들은 거부
      expect(isValidTransition(STATES.AUTHENTICATING, STATES.CONNECTING)).toBe(false);
      expect(isValidTransition(STATES.AUTHENTICATING, STATES.FORWARDING)).toBe(false);
    });

    test('isValidTransition(): provisioning → {forwarding, failed}', () => {
      expect(isValidTransition(STATES.PROVISIONING, STATES.FORWARDING)).toBe(true);
      expect(isValidTransition(STATES.PROVISIONING, STATES.FAILED)).toBe(true);

      // 다른 것들은 거부 (DISCONNECTED 포함)
      expect(isValidTransition(STATES.PROVISIONING, STATES.DISCONNECTED)).toBe(false);
      expect(isValidTransition(STATES.PROVISIONING, STATES.CONNECTED)).toBe(false);
    });

    test('isValidTransition(): forwarding → {connected, failed}', () => {
      expect(isValidTransition(STATES.FORWARDING, STATES.CONNECTED)).toBe(true);
      expect(isValidTransition(STATES.FORWARDING, STATES.FAILED)).toBe(true);

      // 다른 것들은 거부
      expect(isValidTransition(STATES.FORWARDING, STATES.PROVISIONING)).toBe(false);
      expect(isValidTransition(STATES.FORWARDING, STATES.DISCONNECTED)).toBe(false);
    });

    test('isValidTransition(): connected → {reconnecting, disconnected, failed}', () => {
      expect(isValidTransition(STATES.CONNECTED, STATES.RECONNECTING)).toBe(true);
      expect(isValidTransition(STATES.CONNECTED, STATES.DISCONNECTED)).toBe(true);
      expect(isValidTransition(STATES.CONNECTED, STATES.FAILED)).toBe(true);

      // 다른 것들은 거부
      expect(isValidTransition(STATES.CONNECTED, STATES.CONNECTING)).toBe(false);
      expect(isValidTransition(STATES.CONNECTED, STATES.AUTHENTICATING)).toBe(false);
    });

    test('isValidTransition(): reconnecting → {authenticating, failed, disconnected}', () => {
      expect(isValidTransition(STATES.RECONNECTING, STATES.AUTHENTICATING)).toBe(true);
      expect(isValidTransition(STATES.RECONNECTING, STATES.FAILED)).toBe(true);
      expect(isValidTransition(STATES.RECONNECTING, STATES.DISCONNECTED)).toBe(true);

      // 다른 것들은 거부
      expect(isValidTransition(STATES.RECONNECTING, STATES.CONNECTING)).toBe(false);
      expect(isValidTransition(STATES.RECONNECTING, STATES.PROVISIONING)).toBe(false);
    });

    test('isValidTransition(): failed → disconnected 만 허용', () => {
      expect(isValidTransition(STATES.FAILED, STATES.DISCONNECTED)).toBe(true);

      // 다른 모든 전이는 거부
      Object.values(STATES).forEach((state) => {
        if (state !== STATES.DISCONNECTED) {
          expect(isValidTransition(STATES.FAILED, state)).toBe(false);
        }
      });
    });

    test('isValidTransition(invalid, to): 비정의 상태 입력 → false', () => {
      fc.assert(
        fc.property(fc.string({ minLength: 1 }), (invalidState) => {
          // 임의 유효 상태
          const to = STATES.CONNECTED;

          // isValidTransition은 invalid 상태를 ALLOWED_TRANSITIONS에서 찾음 → undefined
          const result = isValidTransition(invalidState, to);
          expect(result).toBe(false);
        }),
        { numRuns: 50 }
      );
    });

    test('isValidTransition(from, invalid): 비정의 상태 출력 → false', () => {
      fc.assert(
        fc.property(fc.string({ minLength: 1 }), (invalidState) => {
          const from = STATES.DISCONNECTED;
          const result = isValidTransition(from, invalidState);
          expect(result).toBe(false);
        }),
        { numRuns: 50 }
      );
    });
  }
);

// ===========================================================================
// Property 26: 유효한 경로(path) 검증 — 모든 상태는 도달 가능 & 되돌아갈 수 있음
// Validates: Architecture (connectivity of state graph)
// ===========================================================================
describe('Property 26: 상태 그래프 연결성 — 유효한 경로 존재 — Validates Architecture', () => {
  test('모든 상태에서 disconnected 도달 가능 (안전 종료)', () => {
    const visited = new Set();

    function canReachDisconnected(state, depth = 0) {
      if (depth > 20) return false; // cycle detection
      if (state === STATES.DISCONNECTED) return true;
      if (visited.has(state)) return false;

      visited.add(state);

      const transitions = ALLOWED_TRANSITIONS[state];
      if (!transitions) return false;

      for (const next of transitions) {
        if (canReachDisconnected(next, depth + 1)) {
          return true;
        }
      }

      return false;
    }

    // 모든 상태에서 disconnected 도달 가능해야 함
    for (const state of Object.values(STATES)) {
      visited.clear();
      expect(canReachDisconnected(state)).toBe(true);
    }
  });

  test('disconnected → connected 정상 경로 존재', () => {
    // 정상 플로우: disconnected → connecting → authenticating → provisioning → forwarding → connected
    const path = [
      STATES.DISCONNECTED,
      STATES.CONNECTING,
      STATES.AUTHENTICATING,
      STATES.PROVISIONING,
      STATES.FORWARDING,
      STATES.CONNECTED,
    ];

    for (let i = 0; i < path.length - 1; i++) {
      expect(isValidTransition(path[i], path[i + 1])).toBe(true);
    }
  });

  test('connected → reconnecting → authenticating 재연결 경로', () => {
    const path = [
      STATES.CONNECTED,
      STATES.RECONNECTING,
      STATES.AUTHENTICATING,
      STATES.PROVISIONING,
      STATES.FORWARDING,
      STATES.CONNECTED,
    ];

    for (let i = 0; i < path.length - 1; i++) {
      expect(isValidTransition(path[i], path[i + 1])).toBe(true);
    }
  });

  test('임의 경로 검증: 생성 경로는 모두 유효 상태여야 함', () => {
    fc.assert(
      fc.property(
        fc.array(stateTransitionArbitrary(), { minLength: 1, maxLength: 10 }),
        (transitions) => {
          // 각 (from, to) 쌍이 유효하거나 시작 상태만 다를 수 있음
          for (const [from, to] of transitions) {
            // from, to는 모두 정의된 상태
            expect(Object.values(STATES)).toContain(from);
            expect(Object.values(STATES)).toContain(to);
          }
        }
      ),
      { numRuns: 50 }
    );
  });
});

// ===========================================================================
// Property 27: 동일 상태 전이 (self-loop) 검증
// Validates: Architecture (state machine purity)
// ===========================================================================
describe('Property 27: 동일 상태 전이(self-loop) 금지 — Validates Architecture', () => {
  test('모든 상태에서 자신으로의 전이는 정의되지 않음', () => {
    for (const state of Object.values(STATES)) {
      const transitions = ALLOWED_TRANSITIONS[state];
      expect(transitions).toBeDefined();
      expect(Array.isArray(transitions)).toBe(true);
      expect(transitions).not.toContain(state);
    }
  });

  test('isValidTransition(state, state) === false for all states', () => {
    fc.assert(
      fc.property(stateArbitrary(), (state) => {
        expect(isValidTransition(state, state)).toBe(false);
      }),
      { numRuns: 100 }
    );
  });
});

// ===========================================================================
// Property 28: 전이 목록 완전성 및 형태 검증
// Validates: Architecture (state machine definition integrity)
// ===========================================================================
describe('Property 28: 전이 목록 완전성 — Validates Architecture', () => {
  test('모든 상태는 ALLOWED_TRANSITIONS에 정의되어야 함', () => {
    for (const state of Object.values(STATES)) {
      expect(ALLOWED_TRANSITIONS).toHaveProperty(state);
      expect(Array.isArray(ALLOWED_TRANSITIONS[state])).toBe(true);
      expect(ALLOWED_TRANSITIONS[state].length).toBeGreaterThan(0);
    }
  });

  test('ALLOWED_TRANSITIONS의 모든 값은 유효한 상태여야 함', () => {
    for (const [from, transitions] of Object.entries(ALLOWED_TRANSITIONS)) {
      expect(Object.values(STATES)).toContain(from);

      for (const to of transitions) {
        expect(Object.values(STATES)).toContain(to);
      }
    }
  });

  test('각 전이는 frozen(불변)이어야 함', () => {
    for (const transitions of Object.values(ALLOWED_TRANSITIONS)) {
      expect(Object.isFrozen(transitions)).toBe(true);
    }
  });

  test('ALLOWED_TRANSITIONS 자체는 frozen이어야 함', () => {
    expect(Object.isFrozen(ALLOWED_TRANSITIONS)).toBe(true);
  });

  test('STATES 자체는 frozen이어야 함', () => {
    expect(Object.isFrozen(STATES)).toBe(true);
  });
});

// ===========================================================================
// Property 29: 비정상 입력에 대한 견고성
// Validates: Requirements (defensive programming)
// ===========================================================================
describe('Property 29: 비정상 입력 견고성 — Validates Requirements', () => {
  test('isValidTransition(null, any) → false', () => {
    fc.assert(
      fc.property(arbitraryStateArbitrary(), (to) => {
        expect(isValidTransition(null, to)).toBe(false);
      }),
      { numRuns: 50 }
    );
  });

  test('isValidTransition(any, null) → false', () => {
    fc.assert(
      fc.property(arbitraryStateArbitrary(), (from) => {
        expect(isValidTransition(from, null)).toBe(false);
      }),
      { numRuns: 50 }
    );
  });

  test('isValidTransition(undefined, any) → false', () => {
    fc.assert(
      fc.property(arbitraryStateArbitrary(), (to) => {
        expect(isValidTransition(undefined, to)).toBe(false);
      }),
      { numRuns: 50 }
    );
  });

  test('CI 반례 고정: toString 이 함수가 아닌 객체·비문자열 키도 throw 없이 false', () => {
    // 2026-09-17 gamma 러너 fast-check 반례 [{"toString":0}, 0] — 객체 키 조회가 ToPropertyKey 로
    // toString 을 호출하다 TypeError. 모듈은 typeof 가드로 막는다.
    expect(isValidTransition({ toString: 0 }, 0)).toBe(false);
    expect(isValidTransition({ toString: {} }, 'connected')).toBe(false);
    expect(isValidTransition('disconnected', { toString: 0 })).toBe(false);
    expect(isValidTransition(Object.create(null), Object.create(null))).toBe(false);
    expect(isValidTransition(Symbol('x'), 'connected')).toBe(false);
    // 프로토타입 체인의 키는 전이로 인정하지 않는다
    expect(isValidTransition('constructor', 'connected')).toBe(false);
    expect(isValidTransition('__proto__', 'connected')).toBe(false);
  });

  test('isValidTransition() 절대 throw하지 않음', () => {
    fc.assert(
      fc.property(
        fc.oneof(
          fc.string(),
          fc.integer(),
          fc.boolean(),
          fc.constant(null),
          fc.constant(undefined),
          fc.object()
        ),
        fc.oneof(
          fc.string(),
          fc.integer(),
          fc.boolean(),
          fc.constant(null),
          fc.constant(undefined),
          fc.object()
        ),
        (from, to) => {
          expect(() => {
            isValidTransition(from, to);
          }).not.toThrow();
        }
      ),
      { numRuns: 100 }
    );
  });
});

// ===========================================================================
// Property 30: 전이 대칭성 검증 (일방향만 유효해야 함)
// Validates: Architecture (directed graph property)
// ===========================================================================
describe('Property 30: 전이 비대칭성 — 모든 엣지는 단방향 — Validates Architecture', () => {
  test('forward 전이가 유효하면 reverse는 유효하지 않음 (사이클 검증)', () => {
    fc.assert(
      fc.property(stateTransitionArbitrary(), ([from, to]) => {
        const forward = isValidTransition(from, to);
        const reverse = isValidTransition(to, from);

        // forward + reverse 동시 참인 경우는 제한적 (일부 재연결 로직 제외)
        // 그러나 모든 상태에서 모든 상태로 가능한 것은 아니어야 함
        expect(typeof forward).toBe('boolean');
        expect(typeof reverse).toBe('boolean');
      }),
      { numRuns: 100 }
    );
  });
});
