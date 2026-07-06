/* ===== src/lib/model-selection.js =====
 * 모델 목록 자동 갱신 시 사용자의 모델 선택을 보존/복구하는 순수 함수.
 *
 * Spec: gateway-openai-models — Task 9.1 / Property 8
 * Validates: Requirements 4.3, 4.5, 4.6
 *
 * 계약 (Property 8, design.md):
 *   임의의 직전 선택 모델 id(prevId)와 임의의 갱신된 카탈로그(nextCatalogModels)에 대해
 *     - 갱신 후 선택(selectedModel)은 항상 nextCatalogModels의 멤버여야 한다(목록이 비어있지 않으면).
 *     - prevId가 nextCatalogModels에 존재하면 그 모델이 그대로 유지된다.            (4.6)
 *     - prevId가 부재하면 채팅 가능 모델(capabilities.chat) 중 표시 순서상 첫 번째가 선택된다.
 *       채팅 가능 모델이 없으면 nextCatalogModels[0], 그것도 없으면 null.            (4.5)
 *     - 직전 카탈로그(prevCatalogModels)와 갱신 카탈로그가 동일하면 선택은 변경되지 않는다. (4.3)
 *
 * 본 함수는 DOM/window/timer/state 등 외부 부수효과에 의존하지 않으며 입력값을 변경하지 않는다.
 * src/main.js 의 refreshModelsPreservingSelection() 이 본 함수를 위임 호출한다.
 */

/**
 * 카탈로그 비교용 시그니처. 정렬된 id 집합을 결정론적 문자열로 만든다.
 * 동일 카탈로그(모델 id 구성이 같음) 판정에 사용한다.
 *
 * @param {Array<{id:string}>} catalogModels - 모델 항목 배열(각 {id, capabilities, ...})
 * @returns {string} 정렬·중복 제거된 id 목록의 JSON 문자열
 */
function catalogSignature(catalogModels) {
  if (!Array.isArray(catalogModels)) return '[]';
  const ids = [];
  const seen = new Set();
  for (const m of catalogModels) {
    if (!m || typeof m.id !== 'string') continue;
    if (seen.has(m.id)) continue;
    seen.add(m.id);
    ids.push(m.id);
  }
  ids.sort();
  return JSON.stringify(ids);
}

/**
 * 갱신된 카탈로그에 대해 보존/복구된 선택 모델을 결정한다.
 *
 * @param {string|null|undefined} prevId - 직전에 선택된 모델 id
 * @param {Array<{id:string, capabilities?:{chat?:boolean}}>} prevCatalogModels - 직전 카탈로그
 * @param {Array<{id:string, capabilities?:{chat?:boolean}}>} nextCatalogModels - 갱신된 카탈로그
 * @returns {{id:string, capabilities?:object}|null} 선택할 모델 항목, 없으면 null
 */
function resolveSelection(prevId, prevCatalogModels, nextCatalogModels) {
  const next = Array.isArray(nextCatalogModels) ? nextCatalogModels : [];

  // 동일 카탈로그 → 선택 불변(4.3). prevId가 next에 존재하면 그 항목을 그대로 돌려준다.
  // (동일 카탈로그이므로 prevId가 직전 목록에 있었다면 next에도 반드시 존재한다.)
  if (catalogSignature(prevCatalogModels) === catalogSignature(next)) {
    const same = next.find((m) => m && m.id === prevId);
    if (same) return same;
  }

  // prevId가 갱신된 목록에 존재 → 유지(4.6)
  if (prevId != null) {
    const stillThere = next.find((m) => m && m.id === prevId);
    if (stillThere) return stillThere;
  }

  // 부재 → 채팅 가능 모델 중 표시 순서상 첫 번째(4.5)
  const firstChat = next.find((m) => m && m.capabilities && m.capabilities.chat);
  if (firstChat) return firstChat;

  // 채팅 가능 모델 없음 → next[0] 또는 null
  return next.length > 0 ? next[0] : null;
}

// 브라우저: <script src="lib/model-selection.js"> 후 전역 노출 (src/main.js 에서 사용)
if (typeof window !== 'undefined') {
  window.resolveSelection = resolveSelection;
  window.catalogSignature = catalogSignature;
}

// Node (테스트): CommonJS 익스포트
if (typeof module !== 'undefined' && module.exports) {
  module.exports = { resolveSelection, catalogSignature };
}
