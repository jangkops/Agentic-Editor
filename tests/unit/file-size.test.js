/**
 * Test: formatFileSize — file-preview-panel size formatter (Property 9)
 * Feature: media-generation-editing
 * Validates: Requirements 7.2
 *
 * Property 9 (design.md):
 *   임의의 양의 정수 바이트 값에 대해, formatFileSize 함수는
 *     - n < 1024              → "{n} B"
 *     - 1024 ≤ n < 1048576    → "{n.n} KB"
 *     - 1048576 ≤ n           → "{n.n} MB"
 *   형식을 반환해야 한다.
 *
 * Validates: Requirements 7.2
 *   "각 파일의 ... 파일 크기(1024 미만은 bytes, 1024 이상은 KB/MB 단위로
 *    소수점 1자리)를 표시한다."
 */

const fc = require('fast-check');
const { formatFileSize, KB, MB } = require('../../src/lib/file-size');

describe('formatFileSize — Property 9 (Validates Requirements 7.2)', () => {
  // ── Property 9 — 형식·단위 정확성 (PBT) ─────────────────────────────
  test('Property 9: byte / KB / MB formatting holds for arbitrary non-negative integers', () => {
    fc.assert(
      fc.property(
        // 0 ~ 10 GB — 요구사항 7.2가 다루는 실용 도메인.
        fc.integer({ min: 0, max: 10 * 1024 * 1024 * 1024 }),
        (n) => {
          const out = formatFileSize(n);

          // (a) 비어있지 않은 문자열이어야 한다.
          expect(typeof out).toBe('string');
          expect(out.length).toBeGreaterThan(0);

          // (b) 단위 접미사는 정확히 "B" / "KB" / "MB" 중 하나로 끝나야 한다.
          //     (요구사항 7.2는 "bytes 또는 KB/MB"만 명시하므로, GB+는 MB로 표현된다.)
          const m = out.match(/^(\S+)\s+(B|KB|MB)$/);
          expect(m).not.toBeNull();
          const [, num, unit] = m;

          // (c) 단위·소수점 규칙
          if (n < KB) {
            expect(unit).toBe('B');
            // n < 1024 → 정수 그대로, 소수점 없음
            expect(num).toBe(String(n));
          } else if (n < MB) {
            expect(unit).toBe('KB');
            // 정확히 소수점 1자리 — 예: "1.0", "999.9"
            expect(num).toMatch(/^\d+\.\d$/);
            // 수치 비교 — 표시값이 (n/1024)을 1자리로 toFixed 한 것과 일치
            expect(num).toBe((n / KB).toFixed(1));
          } else {
            expect(unit).toBe('MB');
            expect(num).toMatch(/^\d+\.\d$/);
            expect(num).toBe((n / MB).toFixed(1));
          }
        }
      ),
      { numRuns: 300 }
    );
  });

  // ── 단조성 — 큰 파일은 항상 더 큰(또는 같은) 수치로 표시되어야 한다 ──
  // 동일 단위 안에서 파싱된 수치는 비감소여야 한다 (반올림으로 동일은 가능).
  test('Property 9 (extra): within the same unit, larger bytes → ≥ displayed value', () => {
    fc.assert(
      fc.property(
        fc.tuple(
          fc.integer({ min: 0, max: 10 * 1024 * 1024 * 1024 }),
          fc.integer({ min: 0, max: 10 * 1024 * 1024 * 1024 })
        ),
        ([a, b]) => {
          const [lo, hi] = a <= b ? [a, b] : [b, a];
          const oLo = formatFileSize(lo);
          const oHi = formatFileSize(hi);
          const unitOf = (s) => s.match(/(B|KB|MB)$/)[1];
          if (unitOf(oLo) === unitOf(oHi)) {
            const numLo = parseFloat(oLo);
            const numHi = parseFloat(oHi);
            expect(numHi).toBeGreaterThanOrEqual(numLo);
          }
        }
      ),
      { numRuns: 200 }
    );
  });

  // ── 명시적 경계 예제 — 회귀 보호 ─────────────────────────────────
  test('n=0 → "0 B"', () => {
    expect(formatFileSize(0)).toBe('0 B');
  });

  test('n=1 → "1 B"', () => {
    expect(formatFileSize(1)).toBe('1 B');
  });

  test('n=1023 (just below KB) → "1023 B"', () => {
    expect(formatFileSize(1023)).toBe('1023 B');
  });

  test('n=1024 (KB boundary) → "1.0 KB"', () => {
    expect(formatFileSize(1024)).toBe('1.0 KB');
  });

  test('n=1536 (1.5 KB) → "1.5 KB"', () => {
    expect(formatFileSize(1536)).toBe('1.5 KB');
  });

  test('n=1048575 (just below MB) → "1024.0 KB"', () => {
    expect(formatFileSize(1048575)).toBe('1024.0 KB');
  });

  test('n=1048576 (MB boundary) → "1.0 MB"', () => {
    expect(formatFileSize(1048576)).toBe('1.0 MB');
  });

  test('n=5 * 1024 * 1024 → "5.0 MB"', () => {
    expect(formatFileSize(5 * 1024 * 1024)).toBe('5.0 MB');
  });

  // ── 방어적 입력 처리 — 비숫자/음수는 0으로 정규화 ───────────────
  test('non-numeric / negative inputs are normalized to "0 B"', () => {
    expect(formatFileSize(undefined)).toBe('0 B');
    expect(formatFileSize(null)).toBe('0 B');
    expect(formatFileSize(-1)).toBe('0 B');
    expect(formatFileSize(NaN)).toBe('0 B');
    expect(formatFileSize('not-a-number')).toBe('0 B');
  });
});
