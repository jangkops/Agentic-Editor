// Feature: gateway-models-effort-support — <effort-control> Web Component (task 14.1)
// Requirements: 7.1, 7.2, 7.3
//
// `/api/models` 응답의 신규 최상위 키 `capabilities`(= ai_engine/capability/capability_map.py
// `to_ui_payload`)를 소비해 **검증된 effort 값만** 셀렉트 박스로 제공한다.
//
// 입력 payload 형식 (to_ui_payload 계약 — 이 파일은 값을 만들지 않고 그대로 읽는다):
//   capabilities = {
//     schemaVersion: 1,
//     modelIds: ["<Exact_Model_ID>", ...],
//     models: {
//       "<Exact_Model_ID>": {
//         modelId, provider,
//         capabilityFingerprint: "cfp1:sha256:...",   // tuple 3번째 성분
//         verificationStatus, syncSupport, asyncSupport, streamingSupport,
//         routes: { "<Known_Route>": {status, allowlist, executionMode, purposes, fallbackRank} },
//         effort: { "<Known_Route>": { status, supported,
//                                      // supported === true 일 때만 아래 domain 필드가 존재:
//                                      valueType, domainKind,
//                                      enumValues | (rangeLowerInclusive, rangeUpperInclusive),
//                                      verifiedValues } },
//         effortRoutes: ["<Known_Route>", ...]
//       }
//     }
//   }
//
// 표시 규칙 (Requirement 7.1 / 7.2 / 7.3):
//   - `(modelId, route, capabilityFingerprint)` tuple이 **셋 다** 지정되고, payload의
//     `models[modelId].capabilityFingerprint`가 tuple의 fingerprint와 정확히 같고,
//     `models[modelId].effort[route].supported === true` (그리고 `status === 'SUPPORTED'`)일
//     때에만 렌더 트리를 만든다 (7.1).
//   - 선택 후보는 Effort_Contract가 실어 보낸 verified domain에서만 만든다. 허용값·value type을
//     이 컴포넌트에 상수로 두지 않는다 (7.2). ENUM은 `enumValues`(비어 있지 않은
//     `verifiedValues`가 있으면 그 교집합), RANGE는 inclusive 경계를 포함한 유한 목록이다.
//   - 그 밖의 모든 경우(payload 없음·tuple 불완전·tuple 불일치·비지원·domain 결손)는
//     렌더 트리를 만들지 않고 숨긴다 (7.3). `innerHTML`은 비고 `data-state`도 제거된다.
//
// 사용자 결정: effort 설정은 **셀렉트 박스**로 제공한다(슬라이더·버튼 그룹 아님).
//   - 첫 option은 항상 미선택(= Gateway 기본 동작)이며 value는 빈 문자열이다.
//   - ENUM  : 검증된 enum 값이 순서대로 option이 된다.
//   - RANGE : inclusive 경계를 **포함**한 유한 목록. valueType이 INTEGER면 경계 사이 각 정수를
//             (개수가 MAX_RANGE_OPTIONS를 넘으면 양 경계를 포함한 균등 간격 정수로) 싣고,
//             그 외(실수)면 양 경계와 균등 분할된 소수를 싣는다.
//
// 이벤트 (steering ui.md — CustomEvent + detail):
//   'effort-change'  detail:{modelId, route, capabilityFingerprint, value}
//                    value는 선택된 값(계약이 알려준 타입 그대로) 또는 미선택 시 null.
//                    사용자 조작에서만 발행한다(프로그램적 갱신은 발행하지 않는다 — 배선 루프 방지).
//                    tuple이 바뀌어 저장 값이 domain을 벗어나면 조용히 미선택으로 되돌리므로,
//                    배선측(task 14.2)은 tuple 전달 후 `el.value`를 다시 읽어 payload를 구성한다.
//
// 제약: Vanilla JS만, shadow DOM 미사용, 프레임워크·빌드 단계 없음(steering ui.md).
//       기존 다크 산업풍 토큰(src/styles/variables.css)과 드롭다운 규약을 재사용한다.
//       렌더 실패가 앱을 깨지 않도록 공개 진입점을 전부 방어한다.

(function () {
  'use strict';

  // ── 스타일 1회 주입 (shadow DOM 미사용 — remote-status-bar.js / search-indicator.js 관례) ──
  const STYLE_ID = 'effort-control-styles';
  if (!document.getElementById(STYLE_ID)) {
    const style = document.createElement('style');
    style.id = STYLE_ID;
    style.textContent = `
      effort-control { display: none; }
      effort-control[data-state="ready"] {
        display: inline-flex;
        align-items: center;
        gap: 6px;
        max-width: 100%;
        box-sizing: border-box;
        font-family: var(--font-ui);
        vertical-align: middle;
      }
      effort-control .efc-label {
        flex: 0 0 auto;
        font-size: 10px;
        font-weight: 600;
        letter-spacing: 0.4px;
        text-transform: uppercase;
        color: var(--color-text-muted, #6a6a6a);
        cursor: pointer;
        user-select: none;
      }
      effort-control .efc-select {
        flex: 0 1 auto;
        min-width: 84px;
        max-width: 200px;
        font-family: var(--font-ui);
        font-size: var(--font-size-xs, 11px);
        line-height: 1.4;
        padding: 3px 6px;
        color: var(--color-text-secondary, #9d9d9d);
        background: var(--color-bg-input, #3c3c3c);
        border: 1px solid var(--color-border, #3c3c3c);
        border-radius: var(--radius-sm, 4px);
        outline: none;
        cursor: pointer;
        transition: border-color var(--transition, 150ms ease),
                    color var(--transition, 150ms ease),
                    background var(--transition, 150ms ease);
      }
      effort-control .efc-select:hover {
        background: var(--color-bg-hover, #2a2d2e);
        border-color: var(--color-accent, #007acc);
      }
      effort-control .efc-select:focus-visible {
        border-color: var(--color-accent, #007acc);
        box-shadow: 0 0 0 2px var(--color-accent-subtle, rgba(0,122,204,0.15));
      }
      /* 미세 상호작용: 값이 선택된 상태만 강조해 기본 Gateway 동작과 구분 */
      effort-control[data-selected="1"] .efc-label { color: var(--color-accent, #007acc); }
      effort-control[data-selected="1"] .efc-select {
        color: var(--color-text-primary, #cccccc);
        border-color: var(--color-accent, #007acc);
      }
      /* 접근성 설명(aria-describedby) — 상단바 밀도를 위해 시각적으로만 숨긴다 */
      effort-control .efc-hint {
        position: absolute;
        width: 1px;
        height: 1px;
        margin: -1px;
        padding: 0;
        overflow: hidden;
        clip: rect(0 0 0 0);
        clip-path: inset(50%);
        white-space: nowrap;
        border: 0;
      }
      @media (prefers-reduced-motion: reduce) {
        effort-control .efc-select { transition: none; }
      }
    `;
    document.head.appendChild(style);
  }

  // ── 계약 enum (contracts.py의 닫힌 집합 — 저장 표현이며 허용 effort 값이 아니다) ──────────
  const EFFORT_STATUS_SUPPORTED = 'SUPPORTED';
  const DOMAIN_ENUM = 'ENUM';
  const DOMAIN_RANGE = 'RANGE';
  const VALUE_TYPE_INTEGER = 'INTEGER';

  // ── 유한 목록 생성 예산 (DOM 폭주 방지 — domain 자체를 좁히지 않고 표본만 제한) ───────────
  const MAX_RANGE_OPTIONS = 64; // 정수 range에서 열거할 최대 option 수(양 경계 항상 포함)
  const RANGE_INTERVALS = 8;    // 실수 range 균등 분할 구간 수 → 경계 포함 9개 후보

  const UNSELECTED_LABEL = '기본값 (미선택)';
  const CONTROL_LABEL = 'effort';

  let _instanceSeq = 0;

  // ── 순수 헬퍼 ────────────────────────────────────────────────────────────────────────
  function _isPlainObject(v) {
    return !!v && typeof v === 'object' && !Array.isArray(v);
  }

  function _asText(v) {
    return typeof v === 'string' ? v : '';
  }

  function _isFiniteNumber(v) {
    return typeof v === 'number' && Number.isFinite(v);
  }

  /** select option으로 표시할 수 있는 스칼라만 허용한다(object·array·null은 제외). */
  function _isSelectable(v) {
    return typeof v === 'string' || typeof v === 'boolean' || _isFiniteNumber(v);
  }

  function _valueKey(v) {
    return typeof v + ':' + String(v);
  }

  function _sameValue(a, b) {
    return _valueKey(a) === _valueKey(b);
  }

  function _formatValue(v) {
    return typeof v === 'string' ? v : String(v);
  }

  /** 균등 분할 소수의 표시 자릿수 — span/RANGE_INTERVALS를 오차 없이 담을 만큼만 쓴다. */
  function _decimalsFor(span) {
    if (!(span > 0)) return 1;
    const magnitude = Math.floor(Math.log10(span));
    return Math.max(1, Math.min(9, 3 - magnitude));
  }

  function _round(value, decimals) {
    const rounded = Number(value.toFixed(decimals));
    return Number.isFinite(rounded) ? rounded : value;
  }

  function _pushUnique(out, seen, value) {
    const key = _valueKey(value);
    if (seen.has(key)) return;
    seen.add(key);
    out.push({ value: value, label: _formatValue(value) });
  }

  /**
   * ENUM domain → option 목록. 계약이 실어 보낸 `enumValues`만 후보가 된다.
   * `verifiedValues`가 비어 있지 않으면 그 교집합만 남긴다(검증된 값의 부분집합 — 7.2).
   */
  function _enumOptions(view) {
    const values = Array.isArray(view.enumValues) ? view.enumValues : [];
    if (!values.length) return null;
    const verified = Array.isArray(view.verifiedValues) ? view.verifiedValues : [];
    const source = verified.length
      ? values.filter(function (v) { return verified.some(function (x) { return _sameValue(x, v); }); })
      : values;

    const out = [];
    const seen = new Set();
    for (let i = 0; i < source.length; i += 1) {
      if (_isSelectable(source[i])) _pushUnique(out, seen, source[i]);
    }
    return out.length ? out : null;
  }

  /**
   * RANGE domain → inclusive 경계를 포함한 유한 option 목록.
   * INTEGER: 경계 사이 각 정수(개수 초과 시 양 경계 포함 균등 간격 정수).
   * 그 외   : 양 경계 + 균등 분할된 소수.
   */
  function _rangeOptions(view) {
    const rawLower = view.rangeLowerInclusive;
    const rawUpper = view.rangeUpperInclusive;
    if (!_isFiniteNumber(rawLower) || !_isFiniteNumber(rawUpper)) return null;

    let lower = Math.min(rawLower, rawUpper);
    let upper = Math.max(rawLower, rawUpper);
    const out = [];
    const seen = new Set();

    if (_asText(view.valueType) === VALUE_TYPE_INTEGER) {
      lower = Math.ceil(lower);
      upper = Math.floor(upper);
      if (lower > upper) return null; // 경계 사이에 정수가 없음 → 표시할 검증된 값이 없다
      const count = upper - lower + 1;
      if (count <= MAX_RANGE_OPTIONS) {
        for (let v = lower; v <= upper; v += 1) _pushUnique(out, seen, v);
      } else {
        const step = (upper - lower) / (MAX_RANGE_OPTIONS - 1);
        for (let i = 0; i < MAX_RANGE_OPTIONS; i += 1) {
          const v = i === MAX_RANGE_OPTIONS - 1 ? upper : Math.round(lower + step * i);
          _pushUnique(out, seen, Math.min(upper, Math.max(lower, v)));
        }
      }
      return out.length ? out : null;
    }

    if (lower === upper) { // 상·하한 동일도 유효한 단일 경계다
      _pushUnique(out, seen, lower);
      return out;
    }

    const span = upper - lower;
    const decimals = _decimalsFor(span);
    _pushUnique(out, seen, lower);
    for (let i = 1; i < RANGE_INTERVALS; i += 1) {
      const raw = lower + (span * i) / RANGE_INTERVALS;
      const v = Math.min(upper, Math.max(lower, _round(raw, decimals)));
      _pushUnique(out, seen, v);
    }
    _pushUnique(out, seen, upper);
    out.sort(function (a, b) { return a.value - b.value; });
    return out;
  }

  /** 표시 가능한 선택 후보 목록. domain이 결손이면 `null`(→ 렌더 미생성). */
  function buildOptions(view) {
    if (!_isPlainObject(view)) return null;
    const kind = _asText(view.domainKind);
    if (kind === DOMAIN_ENUM) return _enumOptions(view);
    if (kind === DOMAIN_RANGE) return _rangeOptions(view);
    return null; // 알 수 없는 domain kind는 후보를 만들지 않는다
  }

  /**
   * tuple에 결속된 effort view 조회. 아래 중 하나라도 어긋나면 `null`(렌더 미생성 — 7.3).
   *   payload 없음 · tuple 불완전 · model 미존재 · fingerprint 불일치 ·
   *   modelId 불일치 · route effort 미존재 · supported !== true · status !== 'SUPPORTED'
   */
  function resolveEffortView(capabilities, tuple) {
    if (!_isPlainObject(capabilities) || !_isPlainObject(tuple)) return null;
    const models = capabilities.models;
    if (!_isPlainObject(models)) return null;

    const modelId = _asText(tuple.modelId);
    const route = _asText(tuple.route);
    const fingerprint = _asText(tuple.capabilityFingerprint);
    if (!modelId || !route || !fingerprint) return null;

    const model = models[modelId];
    if (!_isPlainObject(model)) return null;
    if (_asText(model.capabilityFingerprint) !== fingerprint) return null;
    const payloadModelId = _asText(model.modelId);
    if (payloadModelId && payloadModelId !== modelId) return null;

    const effort = model.effort;
    if (!_isPlainObject(effort)) return null;
    const view = effort[route];
    if (!_isPlainObject(view)) return null;
    if (view.supported !== true) return null;
    if (_asText(view.status) !== EFFORT_STATUS_SUPPORTED) return null;
    return view;
  }

  /** 접근성 설명 문구 — 계약이 알려준 domain 정보만 쓴다. */
  function describeDomain(view, options, tuple) {
    const count = options ? options.length : 0;
    const parts = [];
    if (_asText(view.domainKind) === DOMAIN_RANGE) {
      const lower = Math.min(view.rangeLowerInclusive, view.rangeUpperInclusive);
      const upper = Math.max(view.rangeLowerInclusive, view.rangeUpperInclusive);
      parts.push('검증된 범위 ' + _formatValue(lower) + ' ~ ' + _formatValue(upper) + ' (경계 포함)');
    } else {
      parts.push('검증된 값 ' + count + '개');
    }
    parts.push('선택 후보 ' + count + '개, 미선택 시 Gateway 기본 동작');
    const modelId = _asText(tuple && tuple.modelId);
    const route = _asText(tuple && tuple.route);
    if (modelId && route) parts.push(modelId + ' · ' + route);
    return parts.join(' · ');
  }

  // ── Web Component ───────────────────────────────────────────────────────────────────
  class EffortControl extends HTMLElement {
    static get observedAttributes() {
      return ['model-id', 'route', 'capability-fingerprint'];
    }

    constructor() {
      super();
      _instanceSeq += 1;
      this._uid = 'efc-' + _instanceSeq;
      this._capabilities = null;
      this._selection = { modelId: '', route: '', capabilityFingerprint: '' };
      this._value = null;     // 계약 타입 그대로의 선택 값 또는 null(미선택)
      this._options = [];     // [{value, label}] — 현재 렌더된 후보
      this._view = null;      // 현재 tuple의 effort view (null이면 렌더 미생성 상태)
      this._select = null;
      this._onSelectChange = this._onSelectChange.bind(this);
    }

    connectedCallback() {
      this._render();
    }

    disconnectedCallback() {
      this._detachSelect();
    }

    attributeChangedCallback(name, oldValue, newValue) {
      if (oldValue === newValue) return;
      const next = _asText(newValue);
      if (name === 'model-id') this._selection.modelId = next;
      else if (name === 'route') this._selection.route = next;
      else if (name === 'capability-fingerprint') this._selection.capabilityFingerprint = next;
      else return;
      this._render();
    }

    // ── 공개 API (배선은 task 14.2가 담당) ────────────────────────────────────────────
    /** `/api/models` 응답의 `capabilities` payload. 없으면 null을 넣어 숨긴다. */
    get capabilities() { return this._capabilities; }
    set capabilities(payload) {
      this._capabilities = _isPlainObject(payload) ? payload : null;
      this._render();
    }

    /** `(modelId, route, capabilityFingerprint)` tuple. */
    get selection() {
      return {
        modelId: this._selection.modelId,
        route: this._selection.route,
        capabilityFingerprint: this._selection.capabilityFingerprint,
      };
    }
    set selection(tuple) {
      const t = _isPlainObject(tuple) ? tuple : {};
      this._selection = {
        modelId: _asText(t.modelId),
        route: _asText(t.route),
        capabilityFingerprint: _asText(t.capabilityFingerprint),
      };
      this._render();
    }

    /** 현재 선택 값(계약 타입 그대로) 또는 null(미선택 = Gateway 기본 동작). */
    get value() { return this._value; }
    set value(next) {
      this._value = _isSelectable(next) ? next : null;
      this._render();
    }

    /** effort UI가 렌더된 상태인지(= tuple의 effort status가 SUPPORTED인지). */
    get supported() { return this._view !== null; }

    /** 현재 렌더된 선택 후보 값 목록(계약 타입 그대로). 미렌더면 빈 배열. */
    get domainValues() {
      return this._options.map(function (opt) { return opt.value; });
    }

    /** capabilities·tuple·value를 한 번에 갱신한다(생략한 키는 유지). */
    update(next) {
      try {
        const patch = _isPlainObject(next) ? next : {};
        if ('capabilities' in patch) {
          this._capabilities = _isPlainObject(patch.capabilities) ? patch.capabilities : null;
        }
        if ('modelId' in patch) this._selection.modelId = _asText(patch.modelId);
        if ('route' in patch) this._selection.route = _asText(patch.route);
        if ('capabilityFingerprint' in patch) {
          this._selection.capabilityFingerprint = _asText(patch.capabilityFingerprint);
        }
        if ('value' in patch) this._value = _isSelectable(patch.value) ? patch.value : null;
        this._render();
      } catch (_e) {
        // effort UI는 어떤 경우에도 모델 선택·요청 흐름을 깨뜨리지 않는다.
      }
    }

    /** 저장 값을 미선택으로 되돌린다(이벤트 미발행 — 배선측이 상태를 소유한다). */
    clear() {
      try {
        this._value = null;
        this._render();
      } catch (_e) { /* no-op */ }
    }

    /** tuple 3요소가 정확히 일치하는지. */
    matchesTuple(tuple) {
      const t = _isPlainObject(tuple) ? tuple : {};
      return this._selection.modelId === _asText(t.modelId)
        && this._selection.route === _asText(t.route)
        && this._selection.capabilityFingerprint === _asText(t.capabilityFingerprint);
    }

    /** 요청 payload에 실을 detail. 미렌더·미선택이면 null. */
    toDetail() {
      if (!this.supported || this._value === null) return null;
      return {
        modelId: this._selection.modelId,
        route: this._selection.route,
        capabilityFingerprint: this._selection.capabilityFingerprint,
        value: this._value,
      };
    }

    // ── 내부 구현 ────────────────────────────────────────────────────────────────────
    _render() {
      let view = null;
      let options = null;
      try {
        view = resolveEffortView(this._capabilities, this._selection);
        options = view ? buildOptions(view) : null;
      } catch (_e) {
        view = null;
        options = null;
      }

      // 비지원·tuple 불일치·domain 결손 → 렌더 트리 미생성 (7.3)
      if (!view || !options || !options.length) {
        this._teardown();
        return;
      }

      this._view = view;
      this._options = options;
      // 저장 값이 현재 verified domain 밖이면 조용히 미선택으로 되돌린다 (7.2)
      const current = this._value;
      if (current !== null) {
        const inDomain = options.some(function (opt) { return _sameValue(opt.value, current); });
        if (!inDomain) this._value = null;
      }
      this._build(view, options);
    }

    _teardown() {
      this._detachSelect();
      this._view = null;
      this._options = [];
      this.removeAttribute('data-state');
      this.removeAttribute('data-selected');
      this.innerHTML = '';
    }

    _detachSelect() {
      if (this._select && typeof this._select.removeEventListener === 'function') {
        this._select.removeEventListener('change', this._onSelectChange);
      }
      this._select = null;
    }

    _build(view, options) {
      this._detachSelect();
      this.innerHTML = '';

      const selectId = this._uid + '-select';
      const hintId = this._uid + '-hint';

      const label = document.createElement('label');
      label.className = 'efc-label';
      label.setAttribute('for', selectId);
      label.textContent = CONTROL_LABEL;

      const select = document.createElement('select');
      select.className = 'efc-select';
      select.id = selectId;
      select.setAttribute('aria-describedby', hintId);

      // 첫 항목은 항상 미선택(= Gateway 기본 동작)
      const placeholder = document.createElement('option');
      placeholder.value = '';
      placeholder.textContent = UNSELECTED_LABEL;
      select.appendChild(placeholder);

      let selectedIndex = 0;
      for (let i = 0; i < options.length; i += 1) {
        const option = document.createElement('option');
        option.value = _formatValue(options[i].value);
        option.textContent = options[i].label;
        select.appendChild(option);
        if (this._value !== null && _sameValue(options[i].value, this._value)) selectedIndex = i + 1;
      }
      select.selectedIndex = selectedIndex;
      select.value = selectedIndex === 0 ? '' : _formatValue(options[selectedIndex - 1].value);

      const hint = document.createElement('span');
      hint.className = 'efc-hint';
      hint.id = hintId;
      hint.textContent = describeDomain(view, options, this._selection);

      const title = describeDomain(view, options, this._selection);
      select.setAttribute('title', title);

      this.appendChild(label);
      this.appendChild(select);
      this.appendChild(hint);

      if (typeof select.addEventListener === 'function') {
        select.addEventListener('change', this._onSelectChange);
      }
      this._select = select;

      this.setAttribute('data-state', 'ready');
      if (this._value === null) this.removeAttribute('data-selected');
      else this.setAttribute('data-selected', '1');
    }

    _onSelectChange() {
      const select = this._select;
      if (!select) return;
      const index = typeof select.selectedIndex === 'number' ? select.selectedIndex : 0;
      const inRange = index > 0 && index - 1 < this._options.length;
      this._value = inRange ? this._options[index - 1].value : null;
      if (this._value === null) this.removeAttribute('data-selected');
      else this.setAttribute('data-selected', '1');
      this._emitChange();
    }

    _emitChange() {
      try {
        this.dispatchEvent(new CustomEvent('effort-change', {
          bubbles: true,
          composed: true,
          detail: {
            modelId: this._selection.modelId,
            route: this._selection.route,
            capabilityFingerprint: this._selection.capabilityFingerprint,
            value: this._value,
          },
        }));
      } catch (_e) {
        // 이벤트 전파 실패는 무시(비차단).
      }
    }
  }

  // 순수 판정 로직을 정적으로 노출 — 배선(task 14.2)과 UI 테스트(task 14.4)가 재사용한다.
  EffortControl.resolveEffortView = resolveEffortView;
  EffortControl.buildOptions = buildOptions;
  EffortControl.describeDomain = describeDomain;
  EffortControl.MAX_RANGE_OPTIONS = MAX_RANGE_OPTIONS;
  EffortControl.RANGE_INTERVALS = RANGE_INTERVALS;
  EffortControl.UNSELECTED_LABEL = UNSELECTED_LABEL;

  if (!customElements.get('effort-control')) {
    customElements.define('effort-control', EffortControl);
  }
  window.EffortControl = EffortControl;
})();
