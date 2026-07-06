/* ===== src/lib/image-thumbnails.js =====
 * 채팅 인라인 이미지 썸네일 목록을 결정하는 순수 함수.
 *
 * Spec: media-generation-editing — Task 9.4 / Property 10
 * Validates: Requirements 6.6
 *
 * 이 함수는 외부 상태(DOM/state)에 의존하지 않으며, 입력 배열을 변경하지
 * 않는다. main.js의 renderMessages() 이미지 갤러리 분기는 이 함수의 결과
 * (displayed / overflow / moreText)를 그대로 사용해 DOM을 구성한다.
 *
 * 계약 (Property 10):
 *   - 입력 paths 배열의 길이 N에 대해 displayed.length === min(N, maxDisplay)
 *   - overflow === max(0, N - maxDisplay)
 *   - displayed + overflow === N (이미지가 무성하게 누락되지 않는다)
 *   - moreText 는 overflow > 0 일 때만 "+{overflow}개 더보기"
 *   - displayed 는 paths 의 prefix (순서 보존)
 */

const DEFAULT_MAX_DISPLAY = 4;

/**
 * @param {Array<any>} paths   - 이미지 경로 또는 이미지 항목 배열
 * @param {number}     [maxDisplay=4]
 * @returns {{ displayed: Array<any>, overflow: number, moreText: string|null, total: number }}
 */
function buildImageThumbnailList(paths, maxDisplay = DEFAULT_MAX_DISPLAY) {
  const list = Array.isArray(paths) ? paths : [];
  const cap = Number.isInteger(maxDisplay) && maxDisplay >= 0 ? maxDisplay : DEFAULT_MAX_DISPLAY;
  const total = list.length;
  const displayed = list.slice(0, cap);
  const overflow = Math.max(0, total - cap);
  const moreText = overflow > 0 ? `+${overflow}개 더보기` : null;
  return { displayed, overflow, moreText, total };
}

// 브라우저: <script src="lib/image-thumbnails.js"> 후 전역 노출 (main.js에서 사용)
if (typeof window !== 'undefined') {
  window.buildImageThumbnailList = buildImageThumbnailList;
}

// Node (테스트): CommonJS 익스포트
if (typeof module !== 'undefined' && module.exports) {
  module.exports = { buildImageThumbnailList, DEFAULT_MAX_DISPLAY };
}
