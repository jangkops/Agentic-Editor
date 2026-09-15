'use strict';
// Feature: deep-research-engine — <research-settings> Web Component
// Requirements: 14.1, 14.3, 15.2  (design.md "10) 프론트엔드 — 설정 UI")
// Task: 17.1 외부 리서치 설정 UI 컴포넌트
//
// 외부 리서치(웹/논문/딥리서치) 설정 UI. 기존 설정 모달(showSettingsDialog →
// renderSettingsTab)의 "리서치" 탭 안에 인라인으로 마운트된다.
//
// 제공 기능:
//   1) 옵트인 토글        → AE_ENABLE_WEB_RESEARCH 상당 (enabled)
//   2) 프라이버시 동의    → AE_RESEARCH_CONSENT 상당 (consent)
//   3) 웹 검색 제공자 선택   → AE_RESEARCH_WEB_PROVIDERS 상당 (webProviders[])
//   4) 논문 검색 제공자 선택 → AE_RESEARCH_ACADEMIC_PROVIDERS 상당 (academicProviders[])
//
// 영속: userData/settings/settings.json 에 "프로파일/플래그/제공자 이름"만 저장한다.
//   - 기존 settings IPC(window.electronAPI.saveSettings)를 재사용(신규 채널 없음).
//   - settings.research = { enabled, consent, webProviders[], academicProviders[] } 만 병합.
//   - 자격증명(API 키)은 어떤 경우에도 저장하지 않는다(steering security.md, 요구사항 11).
//     제공자 키는 실행 시 환경변수로만 주입된다(TAVILY_API_KEY 등).
//
// 프라이버시 고지(요구사항 14):
//   - 옵트인 활성 시 "질의가 선택된 외부 제공자로 전송됨"과 데이터 범위(질의문)를 표기(14.1/14.3).
//   - 동의가 활성이 아니면 리서치 도구가 외부 검색을 수행하지 않음을 표기(14.2).
//   - 이 게이트의 통제 범위는 **리서치 도구**까지다. AI 가 실행하는 터미널 명령
//     (run_command → curl/git/npm)은 게이트를 지나지 않는다. 셸을 막으면 정상 개발
//     작업이 죽으므로 차단하지 않고, 문구로 밝히고 서버가 감사 로그를 남긴다
//     (server.py::_audit_shell_egress).
//
// 상위(설정 모달)와의 통신은 CustomEvent(detail)로 수행한다(steering ui.md):
//   'research-change'  detail:{ research:{enabled, consent, webProviders, academicProviders} }
//
// 제약: Vanilla JS만, shadow DOM 미사용(steering ui.md), 신규 CSP 변경 불필요.
//       모든 변경 처리는 방어적(try/catch)이라 저장/렌더 실패가 설정 모달을 깨지 않는다.

(function () {
  'use strict';

  // 이 설정이 통제하지 **못하는** 경로를 밝히는 고지. 세 상태 모두에 붙는다 —
  // 켜져 있을 때만 경고하면 "끄면 안전하다" 는 반대 오해를 남긴다.
  const SHELL_CAVEAT =
    '<span class="rs-caveat">참고: AI 가 실행하는 <b>터미널 명령</b>(curl·git·npm 등)은 ' +
    '이 설정과 무관하게 네트워크를 사용할 수 있습니다. 차단하면 정상 개발 작업이 막히므로, ' +
    '대신 실행 사실을 서버 로그에 기록합니다.</span>';

  // ── 제공자 카탈로그 (design.md "검색 제공자 선정") — 이름/역할만, 키 없음 ──────────
  const WEB_PROVIDERS = [
    { id: 'tavily', label: 'Tavily', role: '1차·키 불요', desc: '에이전트/RAG 특화 취합 — 키리스 모드 지원' },
    { id: 'exa', label: 'Exa', role: '보조·키 필요', desc: '의미 기반 시맨틱 검색' },
    { id: 'brave', label: 'Brave', role: '폴백·키 필요', desc: '독립 인덱스·광범위 커버리지' },
  ];
  const ACADEMIC_PROVIDERS = [
    { id: 'openalex', label: 'OpenAlex', role: '1차·키 불요', desc: '광범위 커버리지·DOI 정합' },
    { id: 'europepmc', label: 'Europe PMC', role: '1차·키 불요', desc: '생의학 문헌(MEDLINE+PMC)·초록 제공' },
    { id: 'pubmed', label: 'PubMed', role: '보조·키 불요', desc: '생의학 권위(MeSH)' },
    { id: 'semantic_scholar', label: 'Semantic Scholar', role: '키 권장', desc: '인용 그래프 — 키 없으면 레이트리밋(429)' },
    { id: 'arxiv', label: 'arXiv', role: '선택', desc: '프리프린트 — 응답이 느려 실패할 수 있음' },
  ];
  // design.md 환경변수 표의 기본값.
  // 기본값은 ai_engine/research/config.py DeepResearchConfig 와 정합해야 한다.
  // 학술 기본값은 "키 없이 실제로 응답하는" 제공자만 둔다(실측 기준).
  const DEFAULTS = {
    enabled: false,
    consent: false,
    webProviders: ['tavily', 'exa', 'brave'],
    academicProviders: ['openalex', 'europepmc'],
  };

  const WEB_IDS = WEB_PROVIDERS.map((p) => p.id);
  const ACAD_IDS = ACADEMIC_PROVIDERS.map((p) => p.id);

  // ── API 키를 받는 제공자 ─────────────────────────────────────────────────
  // electron/core/research-credentials.js PROVIDERS 와 정합해야 한다.
  // required=true 는 키가 없으면 호출 자체가 불가한 제공자(backend._REQUIRES_KEY).
  // 키는 OS 키체인에 암호화 저장되고, 값을 되읽는 IPC 채널은 존재하지 않는다.
  const KEY_PROVIDERS = [
    { id: 'tavily', label: 'Tavily', required: false, placeholder: '선택 — 키 없이도 동작, 넣으면 한도 상향' },
    { id: 'exa', label: 'Exa', required: true, placeholder: 'API 키 붙여넣기' },
    { id: 'brave', label: 'Brave', required: true, placeholder: 'BSA...' },
    { id: 'semantic_scholar', label: 'Semantic Scholar', required: false, placeholder: '선택 — 레이트리밋 완화' },
  ];
  const KEY_ERROR_LABELS = {
    encryption_unavailable: '키체인 사용 불가 — 저장 안 함',
    encrypt_failed: '암호화 실패',
    write_failed: '파일 쓰기 실패',
    unknown_provider: '알 수 없는 제공자',
    ipc_failed: '앱 내부 통신 실패',
  };

  function esc(s) {
    return String(s == null ? '' : s)
      .replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;').replace(/"/g, '&quot;');
  }
  function labelOf(list, id) {
    const p = list.find((x) => x.id === id);
    return p ? p.label : id;
  }
  // 입력 research 객체를 안전한 형태로 정규화(누락 → 기본값, 미지 제공자 id 제거).
  function normalizeResearch(r) {
    const src = (r && typeof r === 'object') ? r : {};
    const pickList = (v, allowed, dflt) => {
      if (!Array.isArray(v)) return dflt.slice();          // 미설정 → 기본값 전체
      const set = new Set(allowed);
      return v.filter((x) => typeof x === 'string' && set.has(x)); // 명시적 [] 는 존중
    };
    return {
      enabled: !!src.enabled,
      consent: !!src.consent,
      webProviders: pickList(src.webProviders, WEB_IDS, DEFAULTS.webProviders),
      academicProviders: pickList(src.academicProviders, ACAD_IDS, DEFAULTS.academicProviders),
    };
  }

  // ── 스타일 1회 주입 (shadow DOM 미사용 — steering ui.md, search-indicator.js 관례) ──
  const STYLE_ID = 'research-settings-styles';
  if (typeof document !== 'undefined' && !document.getElementById(STYLE_ID)) {
    const style = document.createElement('style');
    style.id = STYLE_ID;
    style.textContent = `
      @keyframes rs-fade-in { from { opacity: 0; transform: translateY(2px); } to { opacity: 1; transform: translateY(0); } }
      research-settings { display: block; font-family: var(--font-ui); color: var(--color-text-primary); animation: rs-fade-in var(--transition, 150ms ease); }
      research-settings .rs-sub { transition: opacity var(--transition, 150ms ease); }
      research-settings .rs-sub[data-enabled="false"] { opacity: 0.62; }

      /* VS Code 스타일 토글 스위치 */
      research-settings .rs-switch {
        position: relative; flex: 0 0 auto; width: 38px; height: 20px; padding: 0;
        border-radius: 10px; border: 1px solid var(--color-border, #3c3c3c);
        background: var(--color-bg-input, #3c3c3c); cursor: pointer;
        transition: background var(--transition, 150ms ease), border-color var(--transition, 150ms ease);
      }
      research-settings .rs-switch:hover { border-color: var(--color-accent, #007acc); }
      research-settings .rs-switch:focus-visible { outline: 2px solid var(--color-accent, #007acc); outline-offset: 2px; }
      research-settings .rs-switch[aria-checked="true"] { background: var(--color-accent, #007acc); border-color: var(--color-accent, #007acc); }
      research-settings .rs-switch-knob {
        position: absolute; top: 1px; left: 1px; width: 16px; height: 16px; border-radius: 50%;
        background: #fff; box-shadow: 0 1px 2px rgba(0,0,0,0.4);
        transition: transform var(--transition, 150ms ease);
      }
      research-settings .rs-switch[aria-checked="true"] .rs-switch-knob { transform: translateX(18px); }

      /* 제공자 칩(선택 토글) */
      research-settings .rs-chips { display: flex; flex-wrap: wrap; gap: 8px; padding: 4px 0 14px; }
      research-settings .rs-chip {
        display: inline-flex; align-items: center; gap: 6px; padding: 6px 10px;
        background: var(--color-bg-tertiary, #2d2d30); border: 1px solid var(--color-border, #3c3c3c);
        border-radius: var(--radius-md, 6px); color: var(--color-text-muted, #6a6a6a);
        font-size: var(--font-size-sm, 12px); cursor: pointer; user-select: none;
        transition: transform var(--transition, 150ms ease), border-color var(--transition, 150ms ease), color var(--transition, 150ms ease), background var(--transition, 150ms ease);
      }
      research-settings .rs-chip:hover { transform: translateY(-1px); border-color: var(--color-accent, #007acc); color: var(--color-text-primary, #ccc); }
      research-settings .rs-chip:focus-visible { outline: 2px solid var(--color-accent, #007acc); outline-offset: 2px; }
      research-settings .rs-chip[aria-pressed="true"] {
        background: var(--color-accent-subtle, rgba(0,122,204,0.15));
        border-color: var(--color-accent, #007acc); color: var(--color-text-primary, #ccc);
      }
      research-settings .rs-chip-check {
        display: inline-flex; align-items: center; justify-content: center;
        width: 14px; height: 14px; border-radius: 3px; font-size: 10px; line-height: 1;
        border: 1px solid var(--color-border, #3c3c3c); color: transparent;
      }
      research-settings .rs-chip[aria-pressed="true"] .rs-chip-check {
        background: var(--color-accent, #007acc); border-color: var(--color-accent, #007acc); color: #fff;
      }
      research-settings .rs-chip-role { font-size: 10px; color: var(--color-text-muted, #6a6a6a); }
      research-settings .rs-chip[aria-pressed="true"] .rs-chip-role { color: var(--color-accent, #007acc); }

      /* 동의 체크박스 */
      research-settings .rs-consent { display: inline-flex; align-items: center; gap: 8px; font-size: var(--font-size-sm, 12px); color: var(--color-text-secondary, #9d9d9d); cursor: pointer; }
      research-settings .rs-consent input { width: 15px; height: 15px; accent-color: var(--color-accent, #007acc); cursor: pointer; }

      /* 프라이버시 고지 박스 (요구사항 14) */
      research-settings .rs-privacy {
        margin: 14px 0 0; padding: 10px 12px; border-radius: var(--radius-md, 6px);
        font-size: var(--font-size-sm, 12px); line-height: 1.5;
        border: 1px solid var(--color-border, #3c3c3c); border-left-width: 2px;
        background: var(--color-bg-tertiary, #2d2d30); color: var(--color-text-secondary, #9d9d9d);
        transition: border-color var(--transition, 150ms ease);
      }
      research-settings .rs-privacy[data-tone="ok"] { border-left-color: var(--color-accent, #007acc); }
      research-settings .rs-privacy[data-tone="warn"] { border-left-color: var(--color-warning, #ce9178); color: var(--color-warning, #ce9178); }
      research-settings .rs-privacy[data-tone="off"] { border-left-color: var(--color-text-muted, #6a6a6a); }
      research-settings .rs-privacy b { color: var(--color-text-primary, #ccc); font-weight: 600; }
      research-settings .rs-privacy[data-tone="warn"] b { color: var(--color-warning, #ce9178); }
      /* 셸 우회 고지 — 세 톤 모두에 붙으므로 톤 색을 물려받지 않고 항상 낮은 대비로 둔다.
         주 문구를 밀어내지 않되, 읽으면 사실을 알 수 있는 정도. */
      research-settings .rs-privacy .rs-caveat {
        display: block; margin-top: 6px; padding-top: 6px;
        border-top: 1px solid var(--color-border-light, #333);
        font-size: 11px; color: var(--color-text-muted, #6a6a6a);
      }
      research-settings .rs-privacy .rs-caveat b { color: var(--color-text-secondary, #9d9d9d); font-weight: 600; }

      research-settings .rs-provider-title { font-size: 13px; font-weight: 600; color: var(--color-text-primary, #ccc); margin: 16px 0 2px; }
      research-settings .rs-provider-desc { font-size: 11px; color: var(--color-text-muted, #6a6a6a); margin-bottom: 6px; }
      research-settings .rs-note {
        margin-top: 18px; padding-top: 12px; border-top: 1px solid var(--color-border-light, #333);
        font-size: 11px; color: var(--color-text-muted, #6a6a6a); line-height: 1.5;
      }
      research-settings .rs-note code { font-family: var(--font-mono); font-size: 10px; color: var(--color-text-secondary, #9d9d9d); }

      /* ── 제공자 API 키 행 ─────────────────────────────────────────── */
      research-settings .rs-keys { display: flex; flex-direction: column; gap: 8px; }
      research-settings .rs-key-row {
        padding: 8px 10px; border: 1px solid var(--color-border, #3c3c3c);
        border-radius: var(--border-radius, 4px); background: var(--color-bg-tertiary, #2d2d30);
      }
      research-settings .rs-key-head { display: flex; align-items: center; gap: 8px; margin-bottom: 6px; }
      research-settings .rs-key-name { font-size: 12px; font-weight: 600; color: var(--color-text-primary, #ccc); }
      research-settings .rs-key-req { font-size: 10px; color: var(--color-text-muted, #6a6a6a); }
      research-settings .rs-key-badge {
        margin-left: auto; font-size: 10px; padding: 2px 7px;
        border-radius: 999px; border: 1px solid var(--color-border, #3c3c3c);
        color: var(--color-text-muted, #6a6a6a);
        transition: color var(--transition, 150ms ease), border-color var(--transition, 150ms ease);
      }
      research-settings .rs-key-badge[data-state="set"] { color: var(--color-success, #4ec9b0); border-color: var(--color-success, #4ec9b0); }
      research-settings .rs-key-badge[data-state="unset"] { color: var(--color-warning, #ce9178); border-color: var(--color-warning, #ce9178); }
      research-settings .rs-key-badge[data-state="blocked"] { color: var(--color-error, #f44747); border-color: var(--color-error, #f44747); }
      research-settings .rs-key-input { display: flex; gap: 6px; }
      research-settings .rs-key-input input {
        flex: 1; min-width: 0; padding: 5px 8px;
        font-family: var(--font-mono); font-size: 11px;
        color: var(--color-text-primary, #ccc);
        background: var(--color-bg-primary, #1e1e1e);
        border: 1px solid var(--color-border, #3c3c3c);
        border-radius: var(--border-radius, 4px);
        transition: border-color var(--transition, 150ms ease);
      }
      research-settings .rs-key-input input:focus {
        outline: none; border-color: var(--color-accent, #007acc);
      }
      research-settings .rs-key-input button {
        padding: 5px 10px; font-size: 11px; cursor: pointer;
        color: var(--color-text-secondary, #9d9d9d);
        background: transparent;
        border: 1px solid var(--color-border, #3c3c3c);
        border-radius: var(--border-radius, 4px);
        transition: color var(--transition, 150ms ease), border-color var(--transition, 150ms ease), background var(--transition, 150ms ease);
      }
      research-settings .rs-key-input button:hover { background: var(--color-bg-hover, #2a2d2e); color: var(--color-text-primary, #ccc); }
      research-settings .rs-key-save:hover { border-color: var(--color-accent, #007acc); color: var(--color-accent, #007acc); }
      research-settings .rs-key-clear:hover { border-color: var(--color-error, #f44747); color: var(--color-error, #f44747); }

      @media (prefers-reduced-motion: reduce) {
        research-settings, research-settings .rs-chip, research-settings .rs-switch-knob { animation: none; transition: none; }
      }
    `;
    document.head.appendChild(style);
  }

  class ResearchSettings extends HTMLElement {
    constructor() {
      super();
      this._settings = null;          // 공유 settings 객체(참조) 또는 지연 로드본
      this._model = normalizeResearch(null); // { enabled, consent, webProviders[], academicProviders[] }
      this._built = false;
      // 선택: 호스트가 커스텀 저장 함수를 지정할 수 있음. 기본은 window.electronAPI.saveSettings.
      this.onPersist = null;
    }

    connectedCallback() {
      this._build();
      if (!this._settings) {
        // setSettings 가 사전 호출되지 않은 standalone 경우 → 디스크에서 지연 로드.
        this._lazyLoad();
      } else {
        this._syncUI();
      }
    }

    // ── 공개 API ────────────────────────────────────────────────────────────
    // 공유 settings 객체(참조)를 주입한다. 컴포넌트는 settings.research 를 이 참조에
    // 병합하므로, 호스트(main.js)의 state.settings 와 파일(settings.json)이 동기 유지된다.
    setSettings(settings) {
      this._settings = (settings && typeof settings === 'object') ? settings : {};
      this._model = normalizeResearch(this._settings.research);
      // 정규화 결과를 공유 객체에 반영(메모리 한정 — 사용자 변경 전까지 파일 미기록).
      this._settings.research = this._model;
      if (this._built) this._syncUI();
    }

    // 현재 리서치 플래그의 사본을 반환.
    getResearch() {
      return {
        enabled: this._model.enabled,
        consent: this._model.consent,
        webProviders: this._model.webProviders.slice(),
        academicProviders: this._model.academicProviders.slice(),
      };
    }

    // ── 내부 구현 ────────────────────────────────────────────────────────────
    async _lazyLoad() {
      let loaded = null;
      try {
        if (window.electronAPI && typeof window.electronAPI.loadSettings === 'function') {
          loaded = await window.electronAPI.loadSettings();
        }
      } catch (_e) { /* 로드 실패 → 기본값 */ }
      // 지연 로드가 완료되기 전에 호스트가 setSettings 로 공유 참조를 명시 구성했다면
      // (main.js: appendChild → connectedCallback → _lazyLoad 시작, 직후 setSettings 호출)
      // 디스크 복사본으로 공유 참조를 덮어쓰지 않는다.
      if (this._settings) return;
      this.setSettings(loaded || {});
    }

    _build() {
      if (this._built) return;

      const webChips = WEB_PROVIDERS.map((p) => this._chipHtml('web', p)).join('');
      const acadChips = ACADEMIC_PROVIDERS.map((p) => this._chipHtml('academic', p)).join('');

      this.innerHTML = `
        <div class="rs-root">
          <div class="settings-row">
            <div class="settings-row-info">
              <div class="settings-row-label">외부 리서치 사용</div>
              <div class="settings-row-desc">웹·논문·딥리서치 외부 검색을 활성화합니다. 끄면 리서치 도구가 외부 검색을 수행하지 않습니다.</div>
            </div>
            <button type="button" class="rs-switch" role="switch" aria-checked="false" data-key="enabled" aria-label="외부 리서치 사용 토글">
              <span class="rs-switch-knob" aria-hidden="true"></span>
            </button>
          </div>

          <div class="rs-sub" data-enabled="false">
            <div class="rs-provider-title">웹 검색 제공자</div>
            <div class="rs-provider-desc">활성화할 웹 검색 제공자를 선택합니다(이름만 저장, 키는 저장하지 않음).</div>
            <div class="rs-chips" data-group="web">${webChips}</div>

            <div class="rs-provider-title">논문 검색 제공자</div>
            <div class="rs-provider-desc">활성화할 학술/논문 검색 제공자를 선택합니다.</div>
            <div class="rs-chips" data-group="academic">${acadChips}</div>

            <div class="settings-row">
              <div class="settings-row-info">
                <div class="settings-row-label">프라이버시 동의</div>
                <div class="settings-row-desc">질의가 외부 제공자로 전송되는 것에 동의해야 외부 검색이 수행됩니다.</div>
              </div>
              <label class="rs-consent">
                <input type="checkbox" data-key="consent">
                <span>질의 외부 전송에 동의</span>
              </label>
            </div>

            <div class="rs-privacy" data-tone="off" role="status" aria-live="polite"></div>

            <div class="rs-provider-title">제공자 API 키 <span style="font-weight:400;color:var(--color-text-muted)">(전부 선택 사항)</span></div>
            <div class="rs-provider-desc">
              <b>키를 넣지 않아도 웹·논문 검색이 동작합니다.</b>
              Tavily는 키리스 모드를, 논문 제공자(OpenAlex·Europe PMC·PubMed)는 키 없이 호출합니다.
              키를 넣으면 요청 한도만 올라갑니다. Exa·Brave만 키가 있어야 호출됩니다.
            </div>
            <div class="rs-keys"></div>
          </div>

          <div class="rs-note">
            입력한 키는 <b>OS 키체인에 암호화 저장</b>되며 설정 파일에는 평문으로 남지 않습니다.
            실행 시 복호화해 백엔드 프로세스 환경변수(<code>TAVILY_API_KEY</code>,
            <code>EXA_API_KEY</code>, <code>BRAVE_API_KEY</code>,
            <code>SEMANTIC_SCHOLAR_API_KEY</code>)로만 전달합니다.
            settings.json에는 사용 여부·동의·제공자 이름만 저장됩니다.
          </div>
        </div>`;

      // ── 이벤트 배선 ──
      // 스위치·칩은 네이티브 <button> 이라 Enter/Space 에서 click 이 발생한다.
      // 별도 keydown 핸들러를 두면 이중 토글되므로 click 만 처리한다(키보드 접근성 유지).
      const sw = this.querySelector('.rs-switch[data-key="enabled"]');
      if (sw) sw.addEventListener('click', () => this._setEnabled(!this._model.enabled));

      const consent = this.querySelector('input[data-key="consent"]');
      if (consent) consent.addEventListener('change', () => this._setConsent(!!consent.checked));

      // 칩 클릭은 그룹 컨테이너에 위임.
      this.querySelectorAll('.rs-chips').forEach((container) => {
        container.addEventListener('click', (e) => {
          const chip = e.target && e.target.closest ? e.target.closest('.rs-chip') : null;
          if (!chip || !container.contains(chip)) return;
          this._toggleProvider(container.dataset.group, chip.dataset.id);
        });
      });

      // 키 입력 행 — 저장 여부(bool)만 조회하고 값은 절대 되읽지 않는다.
      this._buildKeyRows();

      this._built = true;
    }

    // ── 제공자 API 키 UI ────────────────────────────────────────────────
    // 값을 되읽는 IPC 채널이 없으므로(보안 설계), 입력란은 항상 비어 있고
    // "저장됨/미설정" 배지로만 상태를 보여준다. 저장 즉시 사이드카에 주입되어
    // 재시작 없이 다음 요청부터 적용된다.
    _buildKeyRows() {
      const host = this.querySelector('.rs-keys');
      if (!host) return;
      host.innerHTML = KEY_PROVIDERS.map((p) => `
        <div class="rs-key-row" data-provider="${esc(p.id)}">
          <div class="rs-key-head">
            <span class="rs-key-name">${esc(p.label)}</span>
            <span class="rs-key-req">${p.required ? '필수' : '선택'}</span>
            <span class="rs-key-badge" data-state="unknown">확인 중</span>
          </div>
          <div class="rs-key-input">
            <input type="password" placeholder="${esc(p.placeholder)}" autocomplete="off"
                   spellcheck="false" aria-label="${esc(p.label)} API 키">
            <button type="button" class="rs-key-save">저장</button>
            <button type="button" class="rs-key-clear">삭제</button>
          </div>
        </div>`).join('');

      host.addEventListener('click', (e) => {
        const t = e.target;
        if (!t || !t.closest) return;
        const row = t.closest('.rs-key-row');
        if (!row) return;
        const provider = row.dataset.provider;
        if (t.closest('.rs-key-save')) {
          const input = row.querySelector('input');
          this._saveKey(provider, input ? input.value : '', row);
        } else if (t.closest('.rs-key-clear')) {
          this._clearKey(provider, row);
        }
      });

      // Enter 로도 저장.
      host.addEventListener('keydown', (e) => {
        if (e.key !== 'Enter') return;
        const row = e.target && e.target.closest ? e.target.closest('.rs-key-row') : null;
        if (!row || e.target.tagName !== 'INPUT') return;
        e.preventDefault();
        this._saveKey(row.dataset.provider, e.target.value, row);
      });

      this._refreshKeyStatus();
    }

    async _refreshKeyStatus() {
      let st = null;
      try {
        if (window.electronAPI && typeof window.electronAPI.researchCredsStatus === 'function') {
          st = await window.electronAPI.researchCredsStatus();
        }
      } catch (_e) { /* 조회 실패 → unknown 유지 */ }
      const providers = (st && st.providers) || {};
      const available = !!(st && st.available);
      this.querySelectorAll('.rs-key-row').forEach((row) => {
        const badge = row.querySelector('.rs-key-badge');
        if (!badge) return;
        if (!available) {
          badge.dataset.state = 'blocked';
          badge.textContent = '키체인 사용 불가';
          badge.title = '이 환경에서는 OS 키체인 암호화를 쓸 수 없어 키를 저장하지 않습니다.';
          return;
        }
        const on = !!providers[row.dataset.provider];
        badge.dataset.state = on ? 'set' : 'unset';
        badge.textContent = on ? '저장됨' : '미설정';
        badge.title = on ? 'OS 키체인에 암호화 저장됨' : '';
      });
    }

    _setKeyRowMessage(row, text, tone) {
      const badge = row && row.querySelector('.rs-key-badge');
      if (!badge) return;
      badge.dataset.state = tone;
      badge.textContent = text;
    }

    async _saveKey(provider, value, row) {
      const key = typeof value === 'string' ? value.trim() : '';
      if (!key) {
        this._setKeyRowMessage(row, '키를 입력하세요', 'unset');
        return;
      }
      this._setKeyRowMessage(row, '저장 중', 'unknown');
      let res = null;
      try {
        res = await window.electronAPI.researchCredsSet(provider, key);
      } catch (_e) { res = { ok: false, error: 'ipc_failed' }; }
      const input = row.querySelector('input');
      if (input) input.value = '';   // 입력값은 화면에 남기지 않는다.
      if (!res || !res.ok) {
        this._setKeyRowMessage(row, KEY_ERROR_LABELS[res && res.error] || '저장 실패', 'blocked');
        return;
      }
      await this._refreshKeyStatus();
      if (res.applied === false) {
        this._setKeyRowMessage(row, '저장됨 (재시작 후 적용)', 'set');
      }
      this._emitChange();
    }

    async _clearKey(provider, row) {
      this._setKeyRowMessage(row, '삭제 중', 'unknown');
      try {
        await window.electronAPI.researchCredsClear(provider);
      } catch (_e) { /* 비차단 */ }
      const input = row.querySelector('input');
      if (input) input.value = '';
      await this._refreshKeyStatus();
      this._emitChange();
    }

    _chipHtml(group, p) {
      return `<button type="button" class="rs-chip" role="button" aria-pressed="false"
          data-group="${esc(group)}" data-id="${esc(p.id)}" tabindex="0"
          title="${esc(p.label)} — ${esc(p.desc)}">
          <span class="rs-chip-check" aria-hidden="true">\u2713</span>
          <span class="rs-chip-label">${esc(p.label)}</span>
          <span class="rs-chip-role">${esc(p.role)}</span>
        </button>`;
    }

    // ── 상태 변경 핸들러 (변경 → UI 동기화 → 영속 → 이벤트 방출) ──
    _setEnabled(v) {
      this._model.enabled = !!v;
      this._afterChange();
    }
    _setConsent(v) {
      this._model.consent = !!v;
      this._afterChange();
    }
    _toggleProvider(group, id) {
      const key = group === 'academic' ? 'academicProviders' : 'webProviders';
      const allowed = group === 'academic' ? ACAD_IDS : WEB_IDS;
      if (!allowed.includes(id)) return;
      const list = Array.isArray(this._model[key]) ? this._model[key].slice() : [];
      const i = list.indexOf(id);
      if (i >= 0) list.splice(i, 1); else list.push(id);
      // 카탈로그 순서로 정규화(결정적 저장).
      this._model[key] = allowed.filter((x) => list.includes(x));
      this._afterChange();
    }

    _afterChange() {
      // 공유 settings 객체에 병합(참조 유지 — 호스트 in-memory 동기).
      try {
        if (this._settings && typeof this._settings === 'object') {
          this._settings.research = this._model;
        }
      } catch (_e) { /* no-op */ }
      this._syncUI();
      this._persist();
      this._emitChange();
    }

    async _persist() {
      try {
        if (typeof this.onPersist === 'function') {
          await this.onPersist(this._settings);
          return;
        }
        if (window.electronAPI && typeof window.electronAPI.saveSettings === 'function') {
          await window.electronAPI.saveSettings(this._settings);
        }
      } catch (_e) {
        // 저장 실패는 비차단 — UI 는 이미 갱신됨. 다음 변경/재오픈 시 재시도된다.
      }
    }

    _emitChange() {
      try {
        this.dispatchEvent(new CustomEvent('research-change', {
          bubbles: true,
          composed: true,
          detail: { research: this.getResearch() },
        }));
      } catch (_e) { /* 이벤트 전파 실패 무시(비차단) */ }
    }

    // ── UI 동기화 (targeted — 포커스 보존, 부드러운 마이크로 인터랙션) ──
    _syncUI() {
      if (!this._built) return;
      try {
        const sw = this.querySelector('.rs-switch[data-key="enabled"]');
        if (sw) sw.setAttribute('aria-checked', this._model.enabled ? 'true' : 'false');

        const sub = this.querySelector('.rs-sub');
        if (sub) sub.setAttribute('data-enabled', this._model.enabled ? 'true' : 'false');

        const consent = this.querySelector('input[data-key="consent"]');
        if (consent) consent.checked = !!this._model.consent;

        this.querySelectorAll('.rs-chip').forEach((chip) => {
          const group = chip.dataset.group;
          const key = group === 'academic' ? 'academicProviders' : 'webProviders';
          const on = Array.isArray(this._model[key]) && this._model[key].indexOf(chip.dataset.id) >= 0;
          chip.setAttribute('aria-pressed', on ? 'true' : 'false');
        });

        this._syncPrivacy();
      } catch (_e) { /* 렌더 실패는 비차단 */ }
    }

    _syncPrivacy() {
      const box = this.querySelector('.rs-privacy');
      if (!box) return;
      const selected = []
        .concat(this._model.webProviders.map((id) => labelOf(WEB_PROVIDERS, id)))
        .concat(this._model.academicProviders.map((id) => labelOf(ACADEMIC_PROVIDERS, id)));
      // 약속의 범위는 이 설정이 실제로 통제하는 것 — **리서치 도구** — 까지다.
      // AI 가 실행하는 터미널 명령(run_command)은 이 게이트를 지나지 않으므로
      // "로컬 검색만 사용됩니다" 는 지킬 수 없는 약속이었다. 실측으로 확인했다:
      // 리서치 도구가 없는 워커로 라우팅된 모델이 curl 로 외부 API 를 직접 호출했다.
      // 셸 자체를 막으면 npm·git 이 죽으므로, 막지 않고 사실을 적는다(서버는 감사 로그를 남긴다).
      let tone, html;
      if (!this._model.enabled) {
        tone = 'off';
        html = '외부 <b>리서치 도구</b>가 꺼져 있습니다 — 웹·논문 검색이 질의를 전송하지 않습니다.<br>' +
          SHELL_CAVEAT;
      } else if (!this._model.consent) {
        tone = 'warn';
        html = '프라이버시 동의 대기 중 — 동의 전까지 <b>리서치 도구</b>가 질의를 전송하지 않습니다.<br>' +
          SHELL_CAVEAT;
      } else {
        tone = 'ok';
        const providers = selected.length ? esc(selected.join(', ')) : '(선택된 제공자 없음)';
        html = `전송 대상 제공자: <b>${providers}</b><br>` +
          '전송 데이터: <b>질의문</b>(입력한 검색어). 리서치 도구는 로컬 프로젝트 파일 내용을 전송하지 않습니다.<br>' +
          SHELL_CAVEAT;
      }
      box.setAttribute('data-tone', tone);
      box.innerHTML = html;
    }
  }

  if (typeof customElements !== 'undefined' && !customElements.get('research-settings')) {
    customElements.define('research-settings', ResearchSettings);
  }
  if (typeof window !== 'undefined') {
    window.ResearchSettings = ResearchSettings;
  }
})();
