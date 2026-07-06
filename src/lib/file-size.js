/* ===== src/lib/file-size.js =====
 * 파일 크기를 사람이 읽을 수 있는 포맷으로 변환하는 순수 함수.
 *
 * Spec: media-generation-editing — Task 10.5 / Property 9
 * Validates: Requirements 7.2
 *
 * 계약 (Property 9, design.md):
 *   임의의 양의 정수 바이트 값에 대해 formatFileSize 함수는
 *     - n < 1024            → "{n} B"            (정수 그대로, 소수점 없음)
 *     - 1024 ≤ n < 1024^2   → "{x.y} KB"         (소수점 1자리)
 *     - 1024^2 ≤ n          → "{x.y} MB"         (소수점 1자리)
 *   형식의 비어있지 않은 문자열을 반환해야 한다.
 *
 * 본 함수는 외부 상태(DOM/state)에 의존하지 않으며 입력값을 변경하지 않는다.
 * <file-preview-panel> 컴포넌트의 _formatSize() 메서드는 본 함수를 위임 호출한다.
 */

const KB = 1024;
const MB = 1024 * 1024;

/**
 * @param {number} bytes - 비음수 정수 바이트 수. 정수가 아니거나 음수, 또는
 *                         null/undefined 등이면 0으로 정규화한다.
 * @returns {string} "0 B" / "512 B" / "1.0 KB" / "2.5 MB" 형식의 문자열
 */
function formatFileSize(bytes) {
  // 안전 정규화 — 음수/NaN/비숫자/소수는 사용자에게 보여줄 값이 아니므로 0으로.
  // (Property 9는 비음수 정수 도메인에서만 동작을 규정한다.)
  const n = Number.isFinite(bytes) && bytes > 0 ? Math.floor(bytes) : 0;

  if (n < KB) return `${n} B`;
  if (n < MB) return `${(n / KB).toFixed(1)} KB`;
  return `${(n / MB).toFixed(1)} MB`;
}

// 브라우저: <script src="lib/file-size.js"> 후 전역 노출 (file-preview-panel.js에서 사용)
if (typeof window !== 'undefined') {
  window.formatFileSize = formatFileSize;
}

// Node (테스트): CommonJS 익스포트
if (typeof module !== 'undefined' && module.exports) {
  module.exports = { formatFileSize, KB, MB };
}
