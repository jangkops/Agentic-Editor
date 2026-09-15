// Feature: deep-research-engine — <search-indicator> Web Component
// Requirements: 18.1, 18.3  (design.md "10) 프론트엔드 — Search_Indicator")
//
// 채팅 UI의 검색 진행 인디케이터. 백엔드가 방출하는 `searchStatus` payload
// (sse_bridge → main.js readSSEStream → center-views 배선; Task 20.4)를 소비해
// 활성/완료/해제 상태를 전환한다.
//
// payload 스키마 (design 9-2):
//   { phase:'start'|'end',
//     kind:'web'|'academic'|'deep',
//     providers: string[],           // 제공자 "이름"만 (자격증명 아님 — P9)
//     query_summary: string,         // 질의 요약(AE_RESEARCH_QUERY_SUMMARY_MAX 절단)
//     status?: 'ok'|'error' }         // phase==='end' 에서만
//
// 상태 전환:
//   phase='start'            → 활성(스피너 + "웹/논문/딥리서치 검색 중… · 제공자 · 질의요약")
//   phase='end' & status=ok  → "완료" 표기 후 짧은 페이드로 해제
//   phase='end' & status=err → 간결한 "검색 실패" 표기 후 해제
//
// 상위(채팅 뷰)와의 통신은 CustomEvent(detail)로 수행한다(steering ui.md):
//   'indicator-active'    detail:{kind, providers, query_summary}
//   'indicator-dismissed' detail:{status}
//
// 제약: Vanilla JS만, shadow DOM 미사용(steering ui.md), 신규 CSP 변경 불필요.
//       공개 메서드 update(payload)/onSearchStatus(payload)는 렌더 실패가 앱을
//       깨지 않도록 전부 방어(try/catch — 요구사항 18.5, 정확성 속성 P8).

(function () {
  'use strict';

  // ── 스타일 1회 주입 (shadow DOM 미사용 — steering ui.md, remote-status-bar.js 관례) ──
  const STYLE_ID = 'search-indicator-styles';
  if (!document.getElementById(STYLE_ID)) {
    const style = document.createElement('style');
    style.id = STYLE_ID;
    style.textContent = `
      @keyframes search-indicator-spin { to { transform: rotate(360deg); } }
      @keyframes search-indicator-in {
        from { opacity: 0; transform: translateY(2px); }
        to   { opacity: 1; transform: translateY(0); }
      }
      search-indicator {
        display: none;
        align-items: center;
        gap: 8px;
        max-width: 100%;
        box-sizing: border-box;
        margin: 6px 0;
        padding: 6px 10px;
        font-family: var(--font-ui);
        font-size: var(--font-size-sm, 12px);
        line-height: 1.4;
        color: var(--color-text-secondary, #9d9d9d);
        background: var(--color-bg-secondary, #252526);
        border: 1px solid var(--color-border, #3c3c3c);
        border-left: 2px solid var(--color-accent, #007acc);
        border-radius: var(--radius-sm, 4px);
        opacity: 1;
        transition: opacity var(--transition, 150ms ease);
      }
      search-indicator[data-state="active"],
      search-indicator[data-state="done"],
      search-indicator[data-state="error"] {
        display: inline-flex;
        animation: search-indicator-in var(--transition, 150ms ease);
      }
      search-indicator[data-state="done"] {
        border-left-color: var(--color-success, #4ec9b0);
        color: var(--color-success, #4ec9b0);
      }
      search-indicator[data-state="error"] {
        border-left-color: var(--color-error, #f44747);
        color: var(--color-error, #f44747);
      }
      search-indicator.si-fade-out { opacity: 0; }

      search-indicator .si-spinner {
        flex: 0 0 auto;
        width: 12px;
        height: 12px;
        border-radius: 50%;
        border: 2px solid var(--color-accent-subtle, rgba(0,122,204,0.15));
        border-top-color: var(--color-accent, #007acc);
        animation: search-indicator-spin 0.7s linear infinite;
      }
      search-indicator .si-icon {
        flex: 0 0 auto;
        width: 12px;
        height: 12px;
        display: none;
        align-items: center;
        justify-content: center;
        font-size: 11px;
        font-weight: 700;
        line-height: 1;
      }
      search-indicator .si-label {
        flex: 1 1 auto;
        min-width: 0;
        overflow: hidden;
        text-overflow: ellipsis;
        white-space: nowrap;
      }
      /* 활성=스피너 / 완료·실패=아이콘 */
      search-indicator[data-state="active"] .si-icon { display: none; }
      search-indicator[data-state="active"] .si-spinner { display: inline-block; }
      search-indicator[data-state="done"] .si-spinner,
      search-indicator[data-state="error"] .si-spinner { display: none; }
      search-indicator[data-state="done"] .si-icon,
      search-indicator[data-state="error"] .si-icon { display: inline-flex; }

      @media (prefers-reduced-motion: reduce) {
        search-indicator .si-spinner { animation-duration: 2s; }
        search-indicator { transition: none; animation: none; }
      }
    `;
    document.head.appendChild(style);
  }

  // kind별 라벨 (요구사항 18.2 — web/academic/deep)
  const KIND_ACTIVE_LABELS = {
    web: '웹 검색 중…',
    academic: '논문 검색 중…',
    deep: '딥리서치 중…',
  };
  const KIND_DONE_LABELS = {
    web: '웹 검색 완료',
    academic: '논문 검색 완료',
    deep: '딥리서치 완료',
  };
  const ERROR_LABEL = '검색 실패';

  const DONE_HOLD_MS = 900; // 완료/실패 표기를 유지하는 시간
  const FADE_MS = 400;      // 페이드 아웃 전환 시간 (opacity transition)

  function asStr(v) { return typeof v === 'string' ? v : ''; }
  function asNameList(v) {
    return Array.isArray(v) ? v.filter((x) => typeof x === 'string' && x.length > 0) : [];
  }

  class SearchIndicator extends HTMLElement {
    constructor() {
      super();
      this._activeCount = 0;   // 진행 중 검색 수 (딥리서치 내부 웨이브 중첩 대비)
      this._hadError = false;  // 현재 배치 중 에러 발생 여부
      this._kind = 'web';
      this._providers = [];
      this._querySummary = '';
      this._holdTimer = null;
      this._fadeTimer = null;
      this._built = false;
    }

    connectedCallback() {
      this._build();
    }

    disconnectedCallback() {
      this._clearTimers();
    }

    // ── 공개 API ────────────────────────────────────────────────────────────
    // searchStatus payload 소비. 렌더 실패가 검색·답변 진행을 막지 않도록 방어.
    update(payload) {
      try {
        if (!payload || typeof payload !== 'object') return;
        this._build();
        const phase = asStr(payload.phase);
        if (phase === 'start') this._onStart(payload);
        else if (phase === 'end') this._onEnd(payload);
      } catch (_e) {
        // 인디케이터는 어떤 경우에도 앱을 깨뜨리지 않는다(요구사항 18.5, P8).
      }
    }

    // 배선측(center-views.js, Task 20.4)이 콜백 형태로 호출할 수 있도록 별칭 제공.
    onSearchStatus(payload) {
      this.update(payload);
    }

    // 강제 초기화(해제).
    reset() {
      try { this._reset(); } catch (_e) { /* no-op */ }
    }

    // ── 내부 구현 ────────────────────────────────────────────────────────────
    _build() {
      if (this._built) return;
      this.innerHTML =
        '<span class="si-spinner" aria-hidden="true"></span>' +
        '<span class="si-icon" aria-hidden="true"></span>' +
        '<span class="si-label"></span>';
      this.setAttribute('role', 'status');
      this.setAttribute('aria-live', 'polite');
      this._built = true;
    }

    _onStart(payload) {
      this._clearTimers();
      this.classList.remove('si-fade-out');
      if (this._activeCount === 0) this._hadError = false; // 새 배치 시작
      this._activeCount += 1;

      const kind = asStr(payload.kind);
      this._kind = KIND_ACTIVE_LABELS[kind] ? kind : 'web';
      this._providers = asNameList(payload.providers);
      this._querySummary = asStr(payload.query_summary);

      this._setState('active');
      this._renderActiveLabel();

      this._emit('indicator-active', {
        kind: this._kind,
        providers: this._providers.slice(),
        query_summary: this._querySummary,
      });
    }

    _onEnd(payload) {
      // status 미지정 시 성공으로 간주(방어). error면 배치 전체를 실패로 표기.
      const status = asStr(payload.status);
      if (status === 'error') this._hadError = true;

      this._activeCount = Math.max(0, this._activeCount - 1);
      if (this._activeCount > 0) return; // 아직 진행 중(예: 딥리서치 내부 웨이브)

      if (this._hadError) this._showTerminal('error');
      else this._showTerminal('done');
    }

    _renderActiveLabel() {
      const parts = [KIND_ACTIVE_LABELS[this._kind] || KIND_ACTIVE_LABELS.web];
      if (this._providers.length) parts.push(this._providers.join(', '));
      if (this._querySummary) parts.push(this._querySummary);
      this._setLabel(parts.join(' · '));
      this._setIcon('');
    }

    _showTerminal(kindState) {
      this._clearTimers();
      if (kindState === 'error') {
        this._setState('error');
        this._setLabel(ERROR_LABEL);
        this._setIcon('!');
      } else {
        this._setState('done');
        this._setLabel(KIND_DONE_LABELS[this._kind] || '검색 완료');
        this._setIcon('\u2713'); // ✓
      }
      const finalStatus = kindState === 'error' ? 'error' : 'ok';
      this._holdTimer = setTimeout(() => this._dismiss(finalStatus), DONE_HOLD_MS);
    }

    _dismiss(status) {
      this._clearTimers();
      this.classList.add('si-fade-out');
      this._fadeTimer = setTimeout(() => {
        this._reset();
        this._emit('indicator-dismissed', { status: status || 'ok' });
      }, FADE_MS);
    }

    _reset() {
      this._clearTimers();
      this._activeCount = 0;
      this._hadError = false;
      this.classList.remove('si-fade-out');
      this.removeAttribute('data-state'); // → display:none
      this._setLabel('');
      this._setIcon('');
    }

    _clearTimers() {
      if (this._holdTimer) { clearTimeout(this._holdTimer); this._holdTimer = null; }
      if (this._fadeTimer) { clearTimeout(this._fadeTimer); this._fadeTimer = null; }
    }

    _setState(state) {
      this.setAttribute('data-state', state);
    }

    _setLabel(text) {
      const el = this.querySelector('.si-label');
      if (el) el.textContent = text;
      if (text) this.setAttribute('title', text);
      else this.removeAttribute('title');
    }

    _setIcon(text) {
      const el = this.querySelector('.si-icon');
      if (el) el.textContent = text || '';
    }

    _emit(name, detail) {
      try {
        this.dispatchEvent(new CustomEvent(name, { bubbles: true, composed: true, detail: detail }));
      } catch (_e) {
        // 이벤트 전파 실패는 무시(비차단).
      }
    }
  }

  if (!customElements.get('search-indicator')) {
    customElements.define('search-indicator', SearchIndicator);
  }
})();
