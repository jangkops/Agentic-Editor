// Feature: deep-research-engine — <research-panel> Web Component
// Requirements: 14.1, 14.3  (design.md "10) 프론트엔드 — 프라이버시 고지 + 리서치 진행/출처/인용/미검증")
// Task: 17.2 프라이버시 고지 + 리서치 진행/출처/인용 렌더
//
// 외부 리서치가 활성인 동안 채팅/응답 영역에 다음을 렌더한다(모두 기존 SSE 이벤트로만):
//   (a) 프라이버시 고지(요구사항 14.1/14.3): "질의가 선택된 외부 제공자로 전송됨" + 데이터 범위(질의문)
//   (b) 리서치 진행: `searchStatus` 의 kind(web/academic/deep) 별 시작/종료
//   (c) 출처 목록: `searchStatus.providers`(질의가 전송되는 외부 제공자 = 출처 원천)
//   (d) 인용 + 미검증 표시: `answerQuality.citation`(citations_total/verified/unverified)
//
// 소비하는 SSE 이벤트 키는 **기존 채널만** 사용한다(신규 백엔드 채널·신규 CSP 없음 — design 10):
//   - searchStatus { phase:'start'|'end', kind:'web'|'academic'|'deep',
//                    providers:string[], query_summary:string, status?:'ok'|'error' }
//                    → (a) 프라이버시 고지, (b) 진행, (c) 출처(제공자)
//     · 방출: GatewayToolNode(Task 20.1) → sse_bridge(Task 20.2) → graph-stream SSE.
//       딥리서치는 graph-stream(LangGraph) 경로를 타므로 이 이벤트가 실제로 도달한다.
//   - answerQuality { citation:{ citations_total, verified, unverified:[raw...] },
//                     grounding?:{score}, faithfulness?:{...} }
//                    → (d) 인용/미검증
//     · 방출: server.py 의 RAG 채팅 경로(run_agent_stream, inline 모드)에서만 방출된다.
//       ⚠️ 현재 graph-stream(딥리서치) 경로의 sse_bridge 는 answerQuality 를 방출하지 않는다.
//       따라서 (d)는 answerQuality 가 도달할 때만 렌더된다. 딥리서치 경로에서 인용/미검증을
//       채우려면 백엔드가 graph-stream 에서도 answerQuality(기존 허용 키)를 방출해야 한다
//       (deep-research-engine Task 13.2/14.x 영역). 여기서 신규 SSE 키/채널을 만들지 않는다.
//       answerQuality 는 ALLOWED_EVENT_KEYS 의 기존 키이므로 소비는 무회귀·전방호환이다.
//
// 설계 주의(정직한 매핑):
//   개별 검색 결과의 소스 URL/제목을 실어 나르는 전용 SSE 이벤트는 존재하지 않는다. 개별 URL은
//   스트리밍되는 리포트 본문(`text`) 또는 리포트 파일 안에 포함된다. 따라서 이 패널의 "출처"는
//   질의가 전송되는 **외부 제공자(원천)** 목록을 표기한다(searchStatus.providers). 개별 결과 URL을
//   위한 신규 백엔드 채널 도입은 본 태스크 범위 밖이므로 만들지 않는다.
//
// 관계(중복 아님):
//   - <search-indicator>(Task 20): 검색 중 잠깐 떴다 사라지는 "라이브 스피너"(transient).
//   - <research-panel>(이 파일): 응답에 남는 "리서치 요약 + 프라이버시 고지"(persistent).
//
// 제약: Vanilla JS만, shadow DOM 미사용(steering ui.md), CustomEvent 로 상위와 통신,
//       VS Code 다크 미학(variables.css 디자인 토큰). 모든 공개 메서드는 방어적(try/catch)이라
//       렌더 실패가 검색·답변 진행을 막지 않는다(요구사항 18.5 / 정확성 속성 P8).

(function () {
  'use strict';

  // ── 스타일 1회 주입 (shadow DOM 미사용 — steering ui.md, search-indicator.js 관례) ──
  const STYLE_ID = 'research-panel-styles';
  if (typeof document !== 'undefined' && !document.getElementById(STYLE_ID)) {
    const style = document.createElement('style');
    style.id = STYLE_ID;
    style.textContent = `
      @keyframes research-panel-in {
        from { opacity: 0; transform: translateY(3px); }
        to   { opacity: 1; transform: translateY(0); }
      }
      @keyframes research-panel-spin { to { transform: rotate(360deg); } }

      research-panel { display: none; }
      research-panel[data-visible="1"] {
        display: block;
        box-sizing: border-box;
        margin: 8px 0 2px;
        padding: 10px 12px;
        font-family: var(--font-ui);
        font-size: var(--font-size-sm, 12px);
        line-height: 1.5;
        color: var(--color-text-secondary, #9d9d9d);
        background: var(--color-bg-secondary, #252526);
        border: 1px solid var(--color-border, #3c3c3c);
        border-left: 2px solid var(--color-accent, #007acc);
        border-radius: var(--radius-md, 6px);
        animation: research-panel-in var(--transition, 150ms ease);
      }

      research-panel .rp-head {
        display: flex; align-items: center; gap: 7px; margin-bottom: 2px;
      }
      research-panel .rp-head-icon {
        flex: 0 0 auto; width: 14px; height: 14px; display: inline-flex;
        align-items: center; justify-content: center;
        font-size: 12px; color: var(--color-accent, #007acc);
      }
      research-panel .rp-head-title {
        flex: 1 1 auto; min-width: 0;
        font-size: var(--font-size-sm, 12px); font-weight: 600;
        color: var(--color-text-primary, #cccccc);
      }
      research-panel .rp-head-status {
        flex: 0 0 auto; font-size: var(--font-size-xs, 11px);
        color: var(--color-text-muted, #6a6a6a);
      }

      /* 프라이버시 고지 (요구사항 14.1/14.3) */
      research-panel .rp-privacy {
        margin: 8px 0 0; padding: 8px 10px;
        border-radius: var(--radius-sm, 4px);
        border: 1px solid var(--color-border, #3c3c3c);
        border-left: 2px solid var(--color-accent, #007acc);
        background: var(--color-accent-subtle, rgba(0,122,204,0.15));
        color: var(--color-text-secondary, #9d9d9d);
        font-size: var(--font-size-xs, 11px); line-height: 1.55;
      }
      research-panel .rp-privacy b { color: var(--color-text-primary, #cccccc); font-weight: 600; }
      research-panel .rp-privacy .rp-lock { margin-right: 5px; }

      /* 섹션 공통 */
      research-panel .rp-section { margin-top: 10px; }
      research-panel .rp-section[hidden] { display: none; }
      research-panel .rp-sec-title {
        font-size: var(--font-size-xs, 11px); font-weight: 600;
        text-transform: uppercase; letter-spacing: 0.6px;
        color: var(--color-text-muted, #6a6a6a); margin-bottom: 5px;
      }

      /* 진행 (요구사항 18 진행 표시와 정합 — 여기서는 응답에 남는 요약형) */
      research-panel .rp-progress { list-style: none; margin: 0; padding: 0; }
      research-panel .rp-progress li {
        display: flex; align-items: center; gap: 7px; padding: 2px 0;
        color: var(--color-text-secondary, #9d9d9d);
      }
      research-panel .rp-dot {
        flex: 0 0 auto; width: 8px; height: 8px; border-radius: 50%;
        background: var(--color-text-muted, #6a6a6a);
      }
      research-panel .rp-spin {
        flex: 0 0 auto; width: 10px; height: 10px; border-radius: 50%;
        border: 2px solid var(--color-accent-subtle, rgba(0,122,204,0.15));
        border-top-color: var(--color-accent, #007acc);
        animation: research-panel-spin 0.7s linear infinite;
      }
      research-panel li[data-st="active"] .rp-dot { display: none; }
      research-panel li[data-st="active"] .rp-spin { display: inline-block; }
      research-panel li:not([data-st="active"]) .rp-spin { display: none; }
      research-panel li[data-st="done"] .rp-dot { background: var(--color-success, #4ec9b0); }
      research-panel li[data-st="error"] .rp-dot { background: var(--color-error, #f44747); }
      research-panel .rp-prog-count { color: var(--color-text-muted, #6a6a6a); font-size: var(--font-size-xs, 11px); }

      /* 출처(제공자) 칩 */
      research-panel .rp-sources { display: flex; flex-wrap: wrap; gap: 6px; }
      research-panel .rp-chip {
        display: inline-flex; align-items: center; gap: 5px;
        padding: 3px 8px; border-radius: 10px;
        background: var(--color-bg-tertiary, #2d2d30);
        border: 1px solid var(--color-border, #3c3c3c);
        color: var(--color-text-secondary, #9d9d9d);
        font-size: var(--font-size-xs, 11px);
      }
      research-panel .rp-chip::before {
        content: ''; width: 6px; height: 6px; border-radius: 50%;
        background: var(--color-accent, #007acc);
      }

      /* 인용 + 미검증 */
      research-panel .rp-cite-summary { display: flex; flex-wrap: wrap; gap: 6px 14px; margin-bottom: 6px; }
      research-panel .rp-cite-stat { color: var(--color-text-secondary, #9d9d9d); }
      research-panel .rp-cite-stat b { color: var(--color-text-primary, #cccccc); font-weight: 600; }
      research-panel .rp-cite-stat.rp-unverified b { color: var(--color-warning, #ce9178); }
      research-panel .rp-unverified-list {
        list-style: none; margin: 0; padding: 0;
        max-height: 132px; overflow-y: auto;
      }
      research-panel .rp-unverified-list li {
        display: flex; align-items: flex-start; gap: 6px; padding: 2px 0;
        color: var(--color-text-secondary, #9d9d9d);
        font-family: var(--font-mono); font-size: var(--font-size-xs, 11px);
        word-break: break-all;
      }
      research-panel .rp-badge {
        flex: 0 0 auto; margin-top: 1px; padding: 0 6px; border-radius: 8px;
        font-family: var(--font-ui); font-size: 10px; font-weight: 600; line-height: 1.5;
        color: var(--color-warning, #ce9178);
        background: transparent;
        border: 1px solid var(--color-warning, #ce9178);
      }
      research-panel .rp-cite-empty { color: var(--color-text-muted, #6a6a6a); font-size: var(--font-size-xs, 11px); }

      @media (prefers-reduced-motion: reduce) {
        research-panel[data-visible="1"] { animation: none; }
        research-panel .rp-spin { animation-duration: 2s; }
      }
    `;
    document.head.appendChild(style);
  }

  // kind 라벨 (요구사항 18.2 — web/academic/deep)
  const KIND_LABELS = { web: '웹 검색', academic: '논문 검색', deep: '딥리서치' };

  function esc(s) {
    return String(s == null ? '' : s)
      .replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;')
      .replace(/"/g, '&quot;').replace(/'/g, '&#39;');
  }
  function asStr(v) { return typeof v === 'string' ? v : ''; }
  function asNameList(v) {
    return Array.isArray(v) ? v.filter((x) => typeof x === 'string' && x.length > 0) : [];
  }
  function asInt(v) { return (typeof v === 'number' && isFinite(v)) ? Math.max(0, Math.floor(v)) : 0; }

  class ResearchPanel extends HTMLElement {
    constructor() {
      super();
      this._built = false;
      this._reset();
    }

    connectedCallback() {
      this._build();
      this._render();
    }

    // ── 공개 API (배선측 center-views.js 가 호출) ──────────────────────────────
    // searchStatus payload 소비 → 프라이버시 고지/진행/출처 갱신.
    onSearchStatus(payload) {
      try {
        if (!payload || typeof payload !== 'object') return;
        this._build();
        this._active = true;                       // 외부 리서치 활성(인용 렌더 게이트)

        const kind = KIND_LABELS[asStr(payload.kind)] ? asStr(payload.kind) : 'web';
        const phase = asStr(payload.phase);

        // 제공자(출처 원천) 누적 — 이름만(자격증명 아님 · P9).
        asNameList(payload.providers).forEach((p) => this._providers.add(p));

        // 프라이버시 고지에 표기할 최신 질의 요약(전송 데이터 범위 = 질의문).
        const qs = asStr(payload.query_summary);
        if (qs) this._querySummary = qs;

        // 진행 카운트(kind별): start → active++, end → active--/done|error++.
        const k = this._kinds[kind] || (this._kinds[kind] = { active: 0, done: 0, error: 0 });
        if (phase === 'start') {
          k.active += 1;
        } else if (phase === 'end') {
          k.active = Math.max(0, k.active - 1);
          if (asStr(payload.status) === 'error') k.error += 1; else k.done += 1;
        }
        this._render();
      } catch (_e) {
        // 인디케이터·패널은 어떤 경우에도 앱을 깨뜨리지 않는다(요구사항 18.5, P8).
      }
    }

    // answerQuality metadata 소비 → 인용/미검증 갱신.
    // 외부 리서치가 활성인 경우에만 렌더(순수 로컬 RAG 응답의 answerQuality 는 무시 → 무회귀).
    onAnswerQuality(meta) {
      try {
        if (!this._active) return;                 // 리서치 컨텍스트가 아니면 표시 안 함
        if (!meta || typeof meta !== 'object') return;
        this._build();
        const c = (meta.citation && typeof meta.citation === 'object') ? meta.citation : null;
        if (c) {
          this._citations = {
            total: asInt(c.citations_total),
            verified: asInt(c.verified),
            unverified: (Array.isArray(c.unverified) ? c.unverified : [])
              .map((x) => asStr(x)).filter((x) => x.length > 0),
          };
          this._haveCitations = true;
        }
        const g = (meta.grounding && typeof meta.grounding === 'object') ? meta.grounding : null;
        if (g && typeof g.score === 'number' && isFinite(g.score)) {
          this._grounding = g.score;
        }
        this._render();
      } catch (_e) { /* 비차단 */ }
    }

    // 새 응답 스트림 시작 시 초기화(호스트가 호출). 리서치가 아니면 숨김 유지.
    reset() {
      try {
        this._reset();
        if (this._built) this._render();
      } catch (_e) { /* no-op */ }
    }

    getState() {
      return {
        active: this._active,
        providers: Array.from(this._providers),
        querySummary: this._querySummary,
        citations: this._citations
          ? { total: this._citations.total, verified: this._citations.verified,
              unverified: this._citations.unverified.slice() }
          : null,
      };
    }

    // ── 내부 상태 ─────────────────────────────────────────────────────────────
    _reset() {
      this._active = false;
      this._providers = new Set();
      this._querySummary = '';
      this._kinds = {};                 // { web:{active,done,error}, ... }
      this._citations = null;           // { total, verified, unverified[] }
      this._haveCitations = false;
      this._grounding = null;
    }

    _build() {
      if (this._built) return;
      this.setAttribute('role', 'region');
      this.setAttribute('aria-label', '외부 리서치 진행 및 출처');
      this.innerHTML =
        '<div class="rp-head">' +
          '<span class="rp-head-icon" aria-hidden="true">\u25C9</span>' +
          '<span class="rp-head-title">외부 리서치</span>' +
          '<span class="rp-head-status"></span>' +
        '</div>' +
        '<div class="rp-privacy" role="status" aria-live="polite"></div>' +
        '<div class="rp-section rp-progress-sec" hidden>' +
          '<div class="rp-sec-title">진행</div>' +
          '<ul class="rp-progress"></ul>' +
        '</div>' +
        '<div class="rp-section rp-sources-sec" hidden>' +
          '<div class="rp-sec-title">출처 (검색 제공자)</div>' +
          '<div class="rp-sources"></div>' +
        '</div>' +
        '<div class="rp-section rp-citations-sec" hidden>' +
          '<div class="rp-sec-title">인용</div>' +
          '<div class="rp-citations"></div>' +
        '</div>';
      this._built = true;
    }

    // ── 렌더 (targeted — innerHTML 는 섹션 단위로만 갱신) ──────────────────────
    _render() {
      if (!this._built) return;
      if (!this._active) {                 // 리서치 비활성 → 숨김(무회귀)
        this.removeAttribute('data-visible');
        return;
      }
      this.setAttribute('data-visible', '1');
      this._renderHeadStatus();
      this._renderPrivacy();
      this._renderProgress();
      this._renderSources();
      this._renderCitations();
    }

    _anyActive() {
      return Object.keys(this._kinds).some((k) => this._kinds[k].active > 0);
    }

    _renderHeadStatus() {
      const el = this.querySelector('.rp-head-status');
      if (!el) return;
      el.textContent = this._anyActive() ? '검색 중\u2026' : '완료';
    }

    // (a) 프라이버시 고지 — 요구사항 14.1(외부 전송 고지) + 14.3(대상 제공자 + 데이터 범위=질의문).
    _renderPrivacy() {
      const box = this.querySelector('.rp-privacy');
      if (!box) return;
      const providers = Array.from(this._providers);
      const provText = providers.length ? esc(providers.join(', ')) : '(선택된 제공자)';
      let html =
        '<span class="rp-lock" aria-hidden="true">\uD83D\uDD12</span>' +
        '<b>질의가 선택된 외부 제공자로 전송됩니다.</b><br>' +
        '\u2022 전송 대상: <b>' + provText + '</b><br>' +
        '\u2022 전송 데이터: <b>질의문</b>(입력한 검색어)';
      if (this._querySummary) {
        html += ' \u2014 \u00AB' + esc(this._querySummary) + '\u00BB';
      }
      html += '<br>\u2022 로컬 프로젝트 파일 내용은 전송되지 않습니다.';
      box.innerHTML = html;
    }

    // (b) 진행 — kind별 시작/완료/실패 요약.
    _renderProgress() {
      const sec = this.querySelector('.rp-progress-sec');
      const ul = this.querySelector('.rp-progress');
      if (!sec || !ul) return;
      const kinds = Object.keys(this._kinds);
      if (!kinds.length) { sec.setAttribute('hidden', ''); ul.innerHTML = ''; return; }
      sec.removeAttribute('hidden');
      // 표시 순서: web → academic → deep (정의된 순서 우선, 그 외는 뒤로).
      const order = ['web', 'academic', 'deep'];
      kinds.sort((a, b) => {
        const ia = order.indexOf(a), ib = order.indexOf(b);
        return (ia < 0 ? 99 : ia) - (ib < 0 ? 99 : ib);
      });
      ul.innerHTML = kinds.map((k) => {
        const c = this._kinds[k];
        const st = c.active > 0 ? 'active' : (c.error > 0 && c.done === 0 ? 'error' : 'done');
        const label = KIND_LABELS[k] || esc(k);
        const parts = [];
        if (c.done) parts.push('완료 ' + c.done);
        if (c.error) parts.push('실패 ' + c.error);
        if (c.active) parts.push('진행 ' + c.active);
        const count = parts.length ? '<span class="rp-prog-count">\u00B7 ' + esc(parts.join(' \u00B7 ')) + '</span>' : '';
        return '<li data-st="' + st + '">' +
          '<span class="rp-spin" aria-hidden="true"></span>' +
          '<span class="rp-dot" aria-hidden="true"></span>' +
          '<span class="rp-prog-label">' + esc(label) + '</span>' + count +
          '</li>';
      }).join('');
    }

    // (c) 출처 — 질의가 전송되는 외부 제공자(원천) 목록.
    _renderSources() {
      const sec = this.querySelector('.rp-sources-sec');
      const box = this.querySelector('.rp-sources');
      if (!sec || !box) return;
      const providers = Array.from(this._providers);
      if (!providers.length) { sec.setAttribute('hidden', ''); box.innerHTML = ''; return; }
      sec.removeAttribute('hidden');
      box.innerHTML = providers
        .map((p) => '<span class="rp-chip">' + esc(p) + '</span>')
        .join('');
    }

    // (d) 인용 + 미검증 — answerQuality.citation 기반.
    _renderCitations() {
      const sec = this.querySelector('.rp-citations-sec');
      const box = this.querySelector('.rp-citations');
      if (!sec || !box) return;
      if (!this._haveCitations || !this._citations) {
        sec.setAttribute('hidden', ''); box.innerHTML = '';
        return;
      }
      sec.removeAttribute('hidden');
      const c = this._citations;
      if (c.total === 0 && c.unverified.length === 0) {
        box.innerHTML = '<div class="rp-cite-empty">인용 없음</div>';
        return;
      }
      const unverifiedCount = c.unverified.length;
      // 미검증 비율(요구사항 9.5): 전체 인용 0이면 0.
      const ratio = c.total > 0 ? (unverifiedCount / c.total) : 0;
      const ratioPct = Math.round(ratio * 100);

      let html = '<div class="rp-cite-summary">' +
        '<span class="rp-cite-stat">인용 <b>' + c.total + '</b></span>' +
        '<span class="rp-cite-stat">검증 <b>' + c.verified + '</b></span>' +
        '<span class="rp-cite-stat rp-unverified">미검증 <b>' + unverifiedCount +
          '</b> (' + ratioPct + '%)</span>';
      if (this._grounding != null) {
        html += '<span class="rp-cite-stat">근거성 <b>' + this._grounding.toFixed(2) + '</b></span>';
      }
      html += '</div>';

      if (unverifiedCount) {
        html += '<ul class="rp-unverified-list">' +
          c.unverified.map((raw) =>
            '<li><span class="rp-badge">미검증</span><span class="rp-uv-text">' + esc(raw) + '</span></li>'
          ).join('') +
          '</ul>';
      }
      box.innerHTML = html;
    }
  }

  if (typeof customElements !== 'undefined' && !customElements.get('research-panel')) {
    customElements.define('research-panel', ResearchPanel);
  }
  if (typeof window !== 'undefined') {
    window.ResearchPanel = ResearchPanel;
  }
})();
