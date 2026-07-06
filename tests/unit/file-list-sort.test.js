/**
 * Test: File list sort & limit (Property 12)
 * Feature: media-generation-editing
 * Validates: Requirements 7.1
 *
 * Property 12 (design.md):
 *   임의의 파일 목록(0~200개)에 대해, File_Preview_Panel은 수정 시간 기준
 *   내림차순(최신순)으로 정렬하고 최대 100개까지만 표시해야 한다.
 *
 * 추가 불변식:
 *   - 결과 길이 = min(N, max)
 *   - 결과는 mtime 내림차순 (인접 쌍 검사)
 *   - 결과의 모든 원소는 입력의 원소다 (위조 없음)
 *   - 입력 배열은 변경되지 않는다 (불변)
 *   - 빈 배열 입력 → 빈 배열 출력
 */

const fc = require('fast-check');
const { sortAndLimitFiles, DEFAULT_MAX_FILES } = require('../../src/lib/file-list-sort');

// ── 임의 파일 항목 ────────────────────────────────────────────
// mtime 표현은 ISO 문자열 / epoch ms / Date / null 모두 다뤄야 한다.
// (file-preview-panel 내부에서 Date.parse 가 throw 없이 0 으로 떨어지는 케이스
//  까지 포함해서 검증한다.)

const isoMtime = fc
  .integer({ min: 0, max: 4_102_444_800_000 }) // 0 ~ 2100-01-01
  .map((ms) => new Date(ms).toISOString());

const numericMtime = fc.integer({ min: 0, max: 4_102_444_800_000 });

const dateMtime = fc
  .integer({ min: 0, max: 4_102_444_800_000 })
  .map((ms) => new Date(ms));

const mtimeArb = fc.oneof(isoMtime, numericMtime, dateMtime);

const fileArb = fc.record({
  name: fc.string({ minLength: 1, maxLength: 40 }).map((s) => `${s}.png`),
  mtime: mtimeArb,
  size: fc.integer({ min: 0, max: 50_000_000 }),
});

/** 헬퍼: file 객체에서 epoch ms 로 변환 — 테스트의 oracle. */
function toMs(f) {
  if (!f || f.mtime == null) return 0;
  const m = f.mtime;
  if (typeof m === 'number' && Number.isFinite(m)) return m;
  if (m instanceof Date) {
    const t = m.getTime();
    return Number.isFinite(t) ? t : 0;
  }
  if (typeof m === 'string') {
    const t = Date.parse(m);
    return Number.isFinite(t) ? t : 0;
  }
  return 0;
}

describe('sortAndLimitFiles — Property 12 (Validates Requirements 7.1)', () => {
  test('default max is 100', () => {
    expect(DEFAULT_MAX_FILES).toBe(100);
  });

  // ── 핵심 Property 12 ──────────────────────────────────────────
  test('Property 12: sort by mtime desc, limited to max=100', () => {
    fc.assert(
      fc.property(
        // 7.1 명시: "최대 100개까지" → 0~200 범위로 입력 도메인을 잡아
        // 잘림 동작과 짧은 입력 동작을 모두 흔든다.
        fc.array(fileArb, { minLength: 0, maxLength: 200 }),
        (files) => {
          const snapshot = files.slice();
          const result = sortAndLimitFiles(files, 100);

          // (a) 길이 = min(N, 100)
          expect(result.length).toBe(Math.min(files.length, 100));

          // (b) 내림차순(최신순) — 인접 쌍 검사
          for (let i = 1; i < result.length; i++) {
            expect(toMs(result[i - 1])).toBeGreaterThanOrEqual(toMs(result[i]));
          }

          // (c) 결과의 모든 원소는 입력의 원소 (참조 동등성)
          for (const f of result) {
            expect(files).toContain(f);
          }

          // (d) 입력 불변
          expect(files).toEqual(snapshot);
        }
      ),
      { numRuns: 200 }
    );
  });

  // ── 명시적 예제 — 경계값 회귀 보호 ─────────────────────────────
  test('empty list returns empty list', () => {
    expect(sortAndLimitFiles([], 100)).toEqual([]);
    expect(sortAndLimitFiles([])).toEqual([]);
  });

  test('non-array input returns empty list', () => {
    expect(sortAndLimitFiles(null)).toEqual([]);
    expect(sortAndLimitFiles(undefined)).toEqual([]);
  });

  test('N < max → all entries returned, sorted desc', () => {
    const files = [
      { name: 'a.png', mtime: '2024-01-01T00:00:00Z' },
      { name: 'b.png', mtime: '2024-03-01T00:00:00Z' },
      { name: 'c.png', mtime: '2024-02-01T00:00:00Z' },
    ];
    const r = sortAndLimitFiles(files, 100);
    expect(r.map((f) => f.name)).toEqual(['b.png', 'c.png', 'a.png']);
  });

  test('N == max boundary → all returned', () => {
    const files = Array.from({ length: 100 }, (_, i) => ({
      name: `${i}.png`,
      mtime: i, // already ascending
    }));
    const r = sortAndLimitFiles(files, 100);
    expect(r).toHaveLength(100);
    // 최신(=99) 가 가장 앞이어야 한다
    expect(r[0].name).toBe('99.png');
    expect(r[99].name).toBe('0.png');
  });

  test('N > max → truncated to max, oldest beyond limit dropped', () => {
    const files = Array.from({ length: 150 }, (_, i) => ({
      name: `${i}.png`,
      mtime: i,
    }));
    const r = sortAndLimitFiles(files, 100);
    expect(r).toHaveLength(100);
    // 최신 100개만 살아남아야 — 가장 오래된 50개(0~49)는 잘림
    const survivedNames = new Set(r.map((f) => f.name));
    for (let i = 0; i < 50; i++) {
      expect(survivedNames.has(`${i}.png`)).toBe(false);
    }
    for (let i = 50; i < 150; i++) {
      expect(survivedNames.has(`${i}.png`)).toBe(true);
    }
    // 첫 항목 = 가장 최신
    expect(r[0].name).toBe('149.png');
  });

  test('missing/invalid mtime treated as 0 (oldest), not throw', () => {
    const files = [
      { name: 'no-mtime.png' },
      { name: 'null.png', mtime: null },
      { name: 'bogus.png', mtime: 'not-a-date' },
      { name: 'real.png', mtime: '2024-01-01T00:00:00Z' },
    ];
    const r = sortAndLimitFiles(files, 100);
    expect(r).toHaveLength(4);
    // real.png 는 mtime 이 있는 유일한 항목 — 가장 앞이어야 한다.
    expect(r[0].name).toBe('real.png');
  });

  test('does not mutate the input array', () => {
    const files = Array.from({ length: 30 }, (_, i) => ({
      name: `${i}.png`,
      mtime: 30 - i, // descending mtime so input is already in display order
    }));
    const snapshot = files.slice();
    sortAndLimitFiles(files, 100);
    expect(files).toEqual(snapshot);
  });
});
