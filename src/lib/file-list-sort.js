/* ===== src/lib/file-list-sort.js =====
 * 생성 파일 목록을 수정 시간 기준 최신순으로 정렬하고 표시 개수를 제한하는
 * 순수 함수.
 *
 * Spec: media-generation-editing — Task 10.6 / Property 12
 * Validates: Requirements 7.1
 *
 * 이 함수는 외부 상태(DOM/state)에 의존하지 않으며, 입력 배열을 변경하지
 * 않는다. file-preview-panel 의 `_refresh()` 와 property 테스트가 모두 이
 * 함수를 import 해 같은 동작을 검증한다.
 *
 * 계약 (Property 12):
 *   - 임의의 길이 N (0..200) 파일 배열 입력에 대해 결과 길이 = min(N, max).
 *   - 결과는 mtime 내림차순(최신 → 과거) 으로 정렬된 안정 배열이다.
 *     (mtime 동률 시 입력 순서가 보존된다 — Array.prototype.sort 는 V8/Node 12+ 에서 안정 정렬)
 *   - 입력이 빈 배열이면 빈 배열을 반환한다.
 *   - 결과의 모든 원소는 입력의 원소다 (누락/위조 없음).
 *   - 입력 배열은 변경되지 않는다 (불변).
 *
 * mtime 표현:
 *   - ISO 문자열 ("2024-01-02T03:04:05Z") 또는 epoch 정수(ms) 모두 허용.
 *   - 누락/파싱 실패는 0 으로 간주 (가장 오래된 파일로 정렬됨).
 */

const DEFAULT_MAX_FILES = 100;

/**
 * @param {Array<{name?: string, mtime?: string|number|Date, size?: number}>} files
 * @param {number} [max=100]   - 결과 최대 길이. 음수/비정수면 기본값(100) 사용.
 * @returns {Array} 정렬·제한된 새 배열 (입력은 변경되지 않음)
 */
function sortAndLimitFiles(files, max = DEFAULT_MAX_FILES) {
  if (!Array.isArray(files)) return [];
  const cap = Number.isInteger(max) && max >= 0 ? max : DEFAULT_MAX_FILES;

  // 입력 변경 금지 — slice 로 복사한 뒤 정렬
  const sorted = files.slice().sort((a, b) => {
    const ta = _mtimeMs(a);
    const tb = _mtimeMs(b);
    return tb - ta; // descending: most recent first
  });
  return sorted.slice(0, cap);
}

/**
 * mtime 값을 epoch ms 정수로 변환. 변환 실패 시 0.
 * @param {{mtime?: string|number|Date}} f
 * @returns {number}
 */
function _mtimeMs(f) {
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

// 브라우저: <script src="lib/file-list-sort.js"> 후 전역 노출
if (typeof window !== 'undefined') {
  window.sortAndLimitFiles = sortAndLimitFiles;
}

// Node (테스트): CommonJS 익스포트
if (typeof module !== 'undefined' && module.exports) {
  module.exports = { sortAndLimitFiles, DEFAULT_MAX_FILES };
}
