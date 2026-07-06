/**
 * Test: Chat inline image thumbnail list (Property 10)
 * Feature: media-generation-editing
 * Validates: Requirements 6.6
 *
 * Property 10 (design.md):
 *   임의의 이미지 경로 배열(길이 1~20)에 대해, 렌더링된 썸네일 수는
 *   min(배열 길이, 4)이어야 하며, 배열 길이가 4를 초과하면
 *   "+{초과분}개 더보기" 링크가 표시되어야 한다.
 *
 * 추가 불변식(누락 방지): displayed.length + overflow === paths.length
 *   → 이미지가 조용히 사라지면 안 된다 (overflow 카운트로 모두 회계 처리).
 */

const fc = require('fast-check');
const { buildImageThumbnailList, DEFAULT_MAX_DISPLAY } = require('../../src/lib/image-thumbnails');

// 임의의 이미지 항목 — main.js의 imgItems 모양에 맞춘 최소 형태
const imageItemArb = fc.record({
  path: fc.string({ minLength: 1, maxLength: 60 }).map(s => `.generated/${s}.png`),
  model: fc.string({ minLength: 1, maxLength: 40 }),
  width: fc.integer({ min: 1, max: 4096 }),
  height: fc.integer({ min: 1, max: 4096 }),
});

describe('buildImageThumbnailList — Property 10 (Validates Requirements 6.6)', () => {
  test('default MAX_DISPLAY is 4', () => {
    expect(DEFAULT_MAX_DISPLAY).toBe(4);
  });

  // ── 핵심 Property 10 — 표시 개수/더보기 링크/누락 방지 ─────────────────
  test('Property 10: thumbnail count, more-link, conservation', () => {
    fc.assert(
      fc.property(
        // Requirements 6.6 명시: "1~20개" — 정확히 그 입력 도메인
        fc.array(imageItemArb, { minLength: 1, maxLength: 20 }),
        (paths) => {
          const result = buildImageThumbnailList(paths, 4);

          // (a) 표시 개수 = min(N, 4)
          expect(result.displayed.length).toBe(Math.min(paths.length, 4));

          // (b) overflow = max(0, N - 4)
          expect(result.overflow).toBe(Math.max(0, paths.length - 4));

          // (c) N > 4 이면 "+(N-4)개 더보기" 링크가 정확히 한 번 표시
          if (paths.length > 4) {
            expect(result.moreText).toBe(`+${paths.length - 4}개 더보기`);
          } else {
            expect(result.moreText).toBeNull();
          }

          // (d) 보존: displayed + overflow === N (이미지 누락 금지)
          expect(result.displayed.length + result.overflow).toBe(paths.length);

          // (e) 순서 보존: displayed 는 paths 의 prefix 이어야 한다
          //     (썸네일 표시는 항상 앞에서부터 차례로)
          for (let i = 0; i < result.displayed.length; i++) {
            expect(result.displayed[i]).toBe(paths[i]);
          }
        }
      ),
      { numRuns: 200 }
    );
  });

  // ── 명시적 예제 — 경계값 회귀 보호 ───────────────────────────────
  test('N=0 → no thumbnails, no more-link', () => {
    const r = buildImageThumbnailList([], 4);
    expect(r.displayed).toEqual([]);
    expect(r.overflow).toBe(0);
    expect(r.moreText).toBeNull();
  });

  test('N=4 (boundary) → all displayed, no more-link', () => {
    const items = [{ path: '.generated/a.png' }, { path: '.generated/b.png' }, { path: '.generated/c.png' }, { path: '.generated/d.png' }];
    const r = buildImageThumbnailList(items, 4);
    expect(r.displayed).toHaveLength(4);
    expect(r.overflow).toBe(0);
    expect(r.moreText).toBeNull();
  });

  test('N=5 → 4 displayed, "+1개 더보기"', () => {
    const items = Array.from({ length: 5 }, (_, i) => ({ path: `.generated/${i}.png` }));
    const r = buildImageThumbnailList(items, 4);
    expect(r.displayed).toHaveLength(4);
    expect(r.overflow).toBe(1);
    expect(r.moreText).toBe('+1개 더보기');
  });

  test('N=20 → 4 displayed, "+16개 더보기"', () => {
    const items = Array.from({ length: 20 }, (_, i) => ({ path: `.generated/${i}.png` }));
    const r = buildImageThumbnailList(items, 4);
    expect(r.displayed).toHaveLength(4);
    expect(r.overflow).toBe(16);
    expect(r.moreText).toBe('+16개 더보기');
  });

  test('does not mutate the input array', () => {
    const items = Array.from({ length: 7 }, (_, i) => ({ path: `.generated/${i}.png` }));
    const snapshot = items.slice();
    buildImageThumbnailList(items, 4);
    expect(items).toEqual(snapshot);
  });
});
