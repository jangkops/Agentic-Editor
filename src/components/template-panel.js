'use strict';
/**
 * <template-panel> — PPTX 템플릿 등록·목록·미리보기·삭제 UI.
 *
 * Spec: pptx-template-styling — 설계 §구성요소 7, 요구사항 8.
 * `file-preview-panel.js`의 패턴(클래스 기반 customElement, `var(--color-*)` 토큰,
 * `window.electronAPI` 호출, CustomEvent)을 그대로 따른다. Shadow DOM은 사용하지
 * 않는다(ui.md / 요구사항 8.6).
 *
 * 이 파일이 구현하는 범위:
 *   - 태스크 14.1: 컴포넌트 골격 + 목록/빈 상태 렌더링 (요구사항 8.1, 8.6, 8.9)
 *   - 태스크 14.2: 업로드 진입점 — '+' 버튼 클릭 + 드래그&드롭 (요구사항 8.2~8.5)
 *   - 태스크 14.3: 미리보기 견본 + 삭제 확인 플로우 + 선택 이벤트
 *       · 선택 + Style_Profile 미리보기 색 견본/폰트 (요구사항 8.7)
 *       · "템플릿 없음" 기본 선택값 + template:selected 이벤트 (요구사항 5.6)
 *       · 삭제 확인(확정/취소) 단계 + 성공/실패 처리 (요구사항 8.8, 8.10, 8.11, 8.13)
 *
 * 사용법: <template-panel></template-panel>
 */

// "템플릿 없음"(무템플릿) 기본 선택을 나타내는 센티넬 templateId. 빈 문자열은
// main.js에서 무템플릿 경로로 해석된다(요구사항 5.6).
const NO_TEMPLATE_ID = '';

class TemplatePanel extends HTMLElement {
  constructor() {
    super();
    this._templates = [];
    // 현재 선택된 templateId. 기본 선택값은 "템플릿 없음"(빈 문자열) (요구사항 5.6).
    this._selectedId = NO_TEMPLATE_ID;
    // 마지막으로 미리보기에 표시한 Style_Profile (요구사항 8.7).
    this._previewProfile = null;
    // 삭제 확인 단계가 열린 항목 정보 { id, name } 또는 null (요구사항 8.10).
    this._pendingDelete = null;
    // 삭제 실패 시 항목에 표시할 에러 { id, message } 또는 null (요구사항 8.13).
    this._deleteError = null;
  }

  connectedCallback() {
    this._render();
    this._refresh();
    // 보조 업로드 진입점: 패널 영역에 .pptx 드래그&드롭 (요구사항 8.5).
    this._bindDragAndDrop();
  }

  /**
   * ISO 8601 문자열을 'YYYY-MM-DD HH:mm'(24시간 표기)로 변환한다. (요구사항 8.1)
   * 파싱 불가 시 빈 문자열을 반환한다.
   */
  _formatTime(iso) {
    if (!iso) return '';
    try {
      const d = new Date(iso);
      if (Number.isNaN(d.getTime())) return '';
      const yyyy = d.getFullYear();
      const mm = String(d.getMonth() + 1).padStart(2, '0');
      const dd = String(d.getDate()).padStart(2, '0');
      const hh = String(d.getHours()).padStart(2, '0');
      const mi = String(d.getMinutes()).padStart(2, '0');
      return `${yyyy}-${mm}-${dd} ${hh}:${mi}`;
    } catch {
      return '';
    }
  }

  /**
   * electronAPI.listTemplates() → createdAt 내림차순 정렬 → 최대 200개로 제한 →
   * 저장 후 목록 재렌더. (요구사항 8.1)
   * electronAPI 부재 시 안전하게 빈 목록으로 처리한다.
   */
  async _refresh() {
    if (!window.electronAPI || typeof window.electronAPI.listTemplates !== 'function') {
      this._templates = [];
      this._renderList();
      return;
    }
    try {
      const result = await window.electronAPI.listTemplates();
      // FastAPI 프록시 응답은 배열이거나 { templates: [...] } 형태일 수 있다.
      const raw = Array.isArray(result)
        ? result
        : (result && Array.isArray(result.templates) ? result.templates : []);
      const sorted = raw
        .slice()
        .sort((a, b) => {
          const ta = a && a.createdAt ? Date.parse(a.createdAt) : 0;
          const tb = b && b.createdAt ? Date.parse(b.createdAt) : 0;
          return (Number.isNaN(tb) ? 0 : tb) - (Number.isNaN(ta) ? 0 : ta);
        })
        .slice(0, 200);
      this._templates = sorted;
      this._renderList();
    } catch (e) {
      console.error('[template-panel] refresh failed:', e);
      this._templates = [];
      this._renderList();
    }
  }

  _render() {
    this.innerHTML = `
      <style>
        template-panel {
          display: flex;
          flex-direction: column;
          height: 100%;
          background: var(--color-bg-secondary, #252526);
          color: var(--color-text-primary, #cccccc);
          font-family: var(--font-ui, sans-serif);
          font-size: 12px;
        }
        .tp-header {
          padding: 10px 12px;
          border-bottom: 1px solid var(--color-border, #3c3c3c);
          display: flex;
          justify-content: space-between;
          align-items: center;
        }
        .tp-title { font-weight: 600; }
        .tp-refresh {
          background: none;
          border: 1px solid var(--color-border, #3c3c3c);
          color: var(--color-text-secondary, #9d9d9d);
          padding: 3px 8px;
          border-radius: var(--border-radius, 4px);
          cursor: pointer;
          font-size: 11px;
          transition: var(--transition, 150ms ease);
        }
        .tp-refresh:hover {
          background: var(--color-bg-hover, #2a2d2e);
          color: var(--color-text-primary, #cccccc);
        }
        /* 업로드 컨트롤 — '+' 템플릿 추가 버튼 (요구사항 8.2) */
        .tp-upload-control {
          padding: 6px 10px;
          border-bottom: 1px solid var(--color-border, #3c3c3c);
          background: var(--color-bg-tertiary, #2d2d30);
        }
        .tp-add-btn {
          display: flex;
          align-items: center;
          justify-content: center;
          gap: 6px;
          width: 100%;
          background: var(--color-accent, #007acc);
          border: 1px solid var(--color-accent, #007acc);
          color: #ffffff;
          padding: 6px 10px;
          border-radius: var(--border-radius, 4px);
          cursor: pointer;
          font-size: 11px;
          font-weight: 600;
          font-family: var(--font-ui, sans-serif);
          transition: var(--transition, 150ms ease);
        }
        .tp-add-btn:hover {
          background: var(--color-accent-hover, #1a8ad4);
          border-color: var(--color-accent-hover, #1a8ad4);
        }
        .tp-add-icon {
          font-size: 14px;
          line-height: 1;
          font-weight: 700;
        }
        .tp-upload-status {
          margin-top: 6px;
          font-size: 10px;
          line-height: 1.5;
          word-break: break-word;
        }
        .tp-upload-status:empty { display: none; }
        .tp-upload-status-info { color: var(--color-text-secondary, #9d9d9d); }
        .tp-upload-status-error { color: var(--color-error, #f44747); }
        /* 인라인 이름 입력 폼 (window.prompt 차단 대체) */
        .tp-name-form {
          margin-top: 6px;
          display: flex;
          flex-direction: column;
          gap: 6px;
        }
        .tp-name-input {
          width: 100%;
          box-sizing: border-box;
          padding: 5px 8px;
          background: var(--color-bg-primary, #1e1e1e);
          border: 1px solid var(--color-accent, #007acc);
          border-radius: var(--border-radius, 4px);
          color: var(--color-text-primary, #cccccc);
          font-size: 11px;
          font-family: var(--font-ui, sans-serif);
          outline: none;
        }
        .tp-name-actions { display: flex; gap: 6px; }
        .tp-name-btn {
          flex: 1;
          padding: 5px 8px;
          border-radius: var(--border-radius, 4px);
          cursor: pointer;
          font-size: 11px;
          font-weight: 600;
          font-family: var(--font-ui, sans-serif);
          transition: var(--transition, 150ms ease);
        }
        .tp-name-ok {
          background: var(--color-accent, #007acc);
          border: 1px solid var(--color-accent, #007acc);
          color: #ffffff;
        }
        .tp-name-ok:hover { background: var(--color-accent-hover, #1a8ad4); }
        .tp-name-cancel {
          background: none;
          border: 1px solid var(--color-border, #3c3c3c);
          color: var(--color-text-secondary, #9d9d9d);
        }
        .tp-name-cancel:hover {
          background: var(--color-bg-hover, #2a2d2e);
          color: var(--color-text-primary, #cccccc);
        }
        /* 드래그&드롭 강조 (요구사항 8.5) */
        template-panel.tp-drag-over {
          outline: 2px dashed var(--color-accent, #007acc);
          outline-offset: -4px;
          background: rgba(0, 122, 204, 0.08);
        }
        .tp-list {
          flex: 1;
          overflow-y: auto;
          padding: 4px 0;
        }
        .tp-empty {
          padding: 20px;
          text-align: center;
          color: var(--color-text-muted, #6a6a6a);
          font-size: 11px;
          line-height: 1.6;
        }
        .tp-item {
          display: grid;
          grid-template-columns: auto 1fr;
          align-items: center;
          gap: 8px;
          padding: 8px 12px;
          cursor: pointer;
          border-bottom: 1px solid rgba(60, 60, 60, 0.3);
          transition: var(--transition, 150ms ease);
        }
        .tp-item:hover { background: var(--color-bg-hover, #2a2d2e); }
        .tp-item-active {
          background: rgba(0, 122, 204, 0.18);
          border-left: 2px solid var(--color-accent, #007acc);
        }
        .tp-icon {
          flex-shrink: 0;
          font-family: var(--font-mono, 'Cascadia Code', monospace);
          font-size: 9px;
          font-weight: 700;
          letter-spacing: 0.5px;
          color: var(--color-accent, #007acc);
          background: rgba(0, 122, 204, 0.1);
          border: 1px solid rgba(0, 122, 204, 0.4);
          border-radius: var(--border-radius, 4px);
          padding: 4px 6px;
          min-width: 36px;
          text-align: center;
          line-height: 1;
        }
        .tp-info { min-width: 0; overflow: hidden; }
        .tp-name {
          font-size: 12px;
          color: var(--color-text-primary, #cccccc);
          overflow: hidden;
          text-overflow: ellipsis;
          white-space: nowrap;
        }
        .tp-meta-row {
          margin-top: 3px;
          font-size: 10px;
          color: var(--color-text-muted, #6a6a6a);
        }
        /* "템플릿 없음" 기본 선택 옵션 (요구사항 5.6) */
        .tp-none-item {
          display: flex;
          align-items: center;
          gap: 8px;
          padding: 8px 12px;
          cursor: pointer;
          border-bottom: 1px solid rgba(60, 60, 60, 0.3);
          color: var(--color-text-secondary, #9d9d9d);
          transition: var(--transition, 150ms ease);
        }
        .tp-none-item:hover { background: var(--color-bg-hover, #2a2d2e); }
        .tp-none-item.tp-item-active {
          background: rgba(0, 122, 204, 0.18);
          border-left: 2px solid var(--color-accent, #007acc);
          color: var(--color-text-primary, #cccccc);
        }
        .tp-none-icon {
          flex-shrink: 0;
          font-family: var(--font-mono, monospace);
          font-size: 9px;
          font-weight: 700;
          color: var(--color-text-muted, #6a6a6a);
          border: 1px dashed var(--color-border, #3c3c3c);
          border-radius: var(--border-radius, 4px);
          padding: 4px 6px;
          min-width: 36px;
          text-align: center;
          line-height: 1;
        }
        /* 삭제 affordance (✕) 및 확인 단계 (요구사항 8.10) */
        .tp-item { position: relative; }
        .tp-item-actions {
          grid-column: 3;
          display: flex;
          align-items: center;
        }
        .tp-item {
          grid-template-columns: auto 1fr auto;
        }
        .tp-del-btn {
          background: none;
          border: 1px solid transparent;
          color: var(--color-text-muted, #6a6a6a);
          width: 22px;
          height: 22px;
          border-radius: var(--border-radius, 4px);
          cursor: pointer;
          font-size: 12px;
          line-height: 1;
          opacity: 0;
          transition: var(--transition, 150ms ease);
        }
        .tp-item:hover .tp-del-btn { opacity: 1; }
        .tp-del-btn:hover {
          background: rgba(244, 71, 71, 0.12);
          border-color: var(--color-error, #f44747);
          color: var(--color-error, #f44747);
        }
        /* "사용"/"사용 중" 버튼 — 템플릿을 활성으로 선택 (요구사항 5.1) */
        .tp-item-actions { display: flex; align-items: center; gap: 6px; }
        .tp-use-btn {
          background: var(--color-accent, #007acc);
          border: 1px solid var(--color-accent, #007acc);
          color: #ffffff;
          padding: 3px 10px;
          border-radius: var(--border-radius, 4px);
          cursor: pointer;
          font-size: 10px;
          font-weight: 600;
          font-family: var(--font-ui, sans-serif);
          white-space: nowrap;
          transition: var(--transition, 150ms ease);
        }
        .tp-use-btn:hover { background: var(--color-accent-hover, #1a8ad4); }
        .tp-use-btn.tp-use-active {
          background: var(--color-success, #4ec9b0);
          border-color: var(--color-success, #4ec9b0);
          color: #0b1f1a;
          cursor: default;
        }
        .tp-use-badge {
          font-size: 10px;
          font-weight: 600;
          color: var(--color-success, #4ec9b0);
          white-space: nowrap;
          padding: 3px 6px;
        }
        .tp-confirm {
          grid-column: 1 / -1;
          margin-top: 8px;
          padding: 8px;
          background: var(--color-bg-tertiary, #2d2d30);
          border: 1px solid var(--color-error, #f44747);
          border-radius: var(--border-radius, 4px);
        }
        .tp-confirm-msg {
          font-size: 11px;
          color: var(--color-text-primary, #cccccc);
          line-height: 1.5;
          margin-bottom: 8px;
          word-break: break-word;
        }
        .tp-confirm-actions { display: flex; gap: 6px; }
        .tp-confirm-btn {
          flex: 1;
          padding: 5px 8px;
          border-radius: var(--border-radius, 4px);
          cursor: pointer;
          font-size: 11px;
          font-weight: 600;
          font-family: var(--font-ui, sans-serif);
          transition: var(--transition, 150ms ease);
        }
        .tp-confirm-yes {
          background: var(--color-error, #f44747);
          border: 1px solid var(--color-error, #f44747);
          color: #ffffff;
        }
        .tp-confirm-yes:hover { filter: brightness(1.1); }
        .tp-confirm-no {
          background: none;
          border: 1px solid var(--color-border, #3c3c3c);
          color: var(--color-text-secondary, #9d9d9d);
        }
        .tp-confirm-no:hover {
          background: var(--color-bg-hover, #2a2d2e);
          color: var(--color-text-primary, #cccccc);
        }
        .tp-item-error {
          grid-column: 1 / -1;
          margin-top: 6px;
          font-size: 10px;
          color: var(--color-error, #f44747);
          line-height: 1.5;
          word-break: break-word;
        }
        /* 미리보기 영역 — Style_Profile 색 견본 + 폰트 (요구사항 8.7) */
        .tp-preview {
          border-top: 1px solid var(--color-border, #3c3c3c);
          background: var(--color-bg-tertiary, #2d2d30);
          padding: 10px 12px;
        }
        .tp-preview:empty { display: none; }
        .tp-preview-title {
          font-size: 11px;
          font-weight: 600;
          color: var(--color-text-secondary, #9d9d9d);
          margin-bottom: 8px;
          overflow: hidden;
          text-overflow: ellipsis;
          white-space: nowrap;
        }
        .tp-swatches {
          display: grid;
          grid-template-columns: repeat(2, 1fr);
          gap: 8px;
          margin-bottom: 10px;
        }
        .tp-swatch {
          display: flex;
          align-items: center;
          gap: 6px;
          min-width: 0;
        }
        .tp-swatch-chip {
          flex-shrink: 0;
          width: 22px;
          height: 22px;
          border-radius: var(--border-radius, 4px);
          border: 1px solid var(--color-border, #3c3c3c);
        }
        .tp-swatch-meta { min-width: 0; line-height: 1.3; }
        .tp-swatch-label {
          font-size: 9px;
          color: var(--color-text-muted, #6a6a6a);
          text-transform: uppercase;
          letter-spacing: 0.4px;
        }
        .tp-swatch-value {
          font-family: var(--font-mono, monospace);
          font-size: 10px;
          color: var(--color-text-primary, #cccccc);
        }
        .tp-fonts {
          display: flex;
          flex-direction: column;
          gap: 4px;
        }
        .tp-font-row { display: flex; gap: 6px; font-size: 10px; }
        .tp-font-label {
          color: var(--color-text-muted, #6a6a6a);
          min-width: 56px;
        }
        .tp-font-value {
          color: var(--color-text-primary, #cccccc);
          overflow: hidden;
          text-overflow: ellipsis;
          white-space: nowrap;
        }
        .tp-preview-error {
          font-size: 11px;
          color: var(--color-error, #f44747);
          line-height: 1.5;
        }
      </style>
      <div class="tp-header">
        <div class="tp-title">템플릿</div>
        <button class="tp-refresh" type="button" title="새로고침">↻</button>
      </div>
      <div class="tp-upload-control" id="tp-upload-control"></div>
      <div class="tp-list"></div>
      <div class="tp-preview" id="tp-preview"></div>
    `;
    const refreshBtn = this.querySelector('.tp-refresh');
    if (refreshBtn) refreshBtn.addEventListener('click', () => this._refresh());

    // 업로드 컨트롤('+' 버튼)을 자리표시자 컨테이너에 렌더 (요구사항 8.2).
    this._renderUploadControl();
  }

  /**
   * 상단 Template_Upload_Control('+' 템플릿 추가 버튼)을 #tp-upload-control
   * 컨테이너에 렌더한다. (요구사항 8.2)
   * 빈 상태(요구사항 8.9)에서도 이 컨트롤은 항상 표시되도록, 목록 영역이 아닌
   * 별도의 상단 컨테이너에 렌더한다.
   * 또한 등록 결과/에러 메시지를 표시할 상태 영역(.tp-upload-status)을 둔다.
   */
  _renderUploadControl() {
    const host = this.querySelector('#tp-upload-control');
    if (!host) return;
    host.innerHTML = `
      <button class="tp-add-btn" type="button" title="템플릿 추가 (.pptx)">
        <span class="tp-add-icon">+</span>
        <span class="tp-add-label">템플릿 추가</span>
      </button>
      <div class="tp-upload-status" role="status" aria-live="polite"></div>
    `;
    const addBtn = host.querySelector('.tp-add-btn');
    if (addBtn) addBtn.addEventListener('click', () => this._onUploadClick());
  }

  /**
   * 업로드 상태/에러 메시지를 표시한다. (요구사항 8.3 등록 결과, 9 폴백 메시지)
   * kind: 'info' | 'error' | '' (빈 문자열이면 메시지 제거)
   */
  _setUploadStatus(message, kind = 'info') {
    const statusEl = this.querySelector('.tp-upload-status');
    if (!statusEl) return;
    if (!message) {
      statusEl.textContent = '';
      statusEl.className = 'tp-upload-status';
      return;
    }
    statusEl.textContent = message;
    statusEl.className = `tp-upload-status tp-upload-status-${kind === 'error' ? 'error' : 'info'}`;
  }

  /**
   * 템플릿 이름을 인라인 입력 폼으로 받는다. (요구사항 8.3, 8.5)
   * Electron 렌더러는 window.prompt를 차단하므로(`prompt() is and will not be
   * supported`) DOM 기반 입력 UI를 사용한다. 파일명(확장자 제외)을 기본값으로
   * 채워 제시하고, 확인 시 trim된 이름으로 resolve, 취소 시 null로 resolve한다.
   * @returns {Promise<string|null>} 입력된 이름(trim) 또는 취소 시 null
   */
  _promptTemplateName(filePath) {
    const base = this._baseName(filePath);
    const suggested = base.replace(/\.pptx$/i, '');

    // prompt 사용 불가 환경(테스트 등, DOM 없음) — 파일명 기반 기본값 사용.
    if (typeof document === 'undefined') {
      return Promise.resolve(suggested || base);
    }

    const host = this.querySelector('#tp-upload-control');
    if (!host) return Promise.resolve(suggested || base);

    return new Promise((resolve) => {
      // 이미 열린 입력 폼이 있으면 제거(중복 방지).
      const existing = host.querySelector('.tp-name-form');
      if (existing) existing.remove();

      const form = document.createElement('div');
      form.className = 'tp-name-form';
      form.innerHTML = `
        <input class="tp-name-input" type="text" placeholder="템플릿 이름" maxlength="100" />
        <div class="tp-name-actions">
          <button class="tp-name-btn tp-name-ok" type="button">등록</button>
          <button class="tp-name-btn tp-name-cancel" type="button">취소</button>
        </div>
      `;
      host.appendChild(form);

      const input = form.querySelector('.tp-name-input');
      const okBtn = form.querySelector('.tp-name-ok');
      const cancelBtn = form.querySelector('.tp-name-cancel');

      let settled = false;
      const cleanup = () => { try { form.remove(); } catch {} };
      const done = (value) => {
        if (settled) return;
        settled = true;
        cleanup();
        resolve(value);
      };

      okBtn.addEventListener('click', () => done(String(input.value || '').trim()));
      cancelBtn.addEventListener('click', () => done(null));
      input.addEventListener('keydown', (e) => {
        if (e.key === 'Enter') { e.preventDefault(); done(String(input.value || '').trim()); }
        else if (e.key === 'Escape') { e.preventDefault(); done(null); }
      });

      input.value = suggested;
      input.focus();
      input.select();
    });
  }

  /** 경로에서 파일명만 추출한다(`/`·`\` 구분자 모두 처리). */
  _baseName(p) {
    const s = String(p || '');
    const idx = Math.max(s.lastIndexOf('/'), s.lastIndexOf('\\'));
    return idx >= 0 ? s.slice(idx + 1) : s;
  }

  /**
   * '+' 클릭 업로드 진입점. (요구사항 8.3, 8.4)
   *  1) `.pptx` 필터로 Native_File_Dialog 열기 (요구사항 8.3)
   *  2) 취소(null) 시 아무 동작도 하지 않고 상태 무변경 (요구사항 8.4)
   *  3) 경로 반환 시 이름 입력 → registerTemplate 등록 (요구사항 8.3)
   */
  async _onUploadClick() {
    const api = window.electronAPI;
    if (!api || typeof api.openFile !== 'function') {
      this._setUploadStatus('파일 선택 기능을 사용할 수 없습니다', 'error');
      return;
    }
    let filePath;
    try {
      filePath = await api.openFile({
        filters: [{ name: 'PowerPoint', extensions: ['pptx'] }],
      });
    } catch (e) {
      console.error('[template-panel] openFile failed:', e);
      this._setUploadStatus('파일 선택 중 오류가 발생했습니다', 'error');
      return;
    }
    // 다이얼로그 취소 → 등록 요청 미전송, 상태 무변경 (요구사항 8.4).
    if (!filePath) return;
    await this._registerWithName(filePath);
  }

  /**
   * 이름 입력을 받은 뒤 registerTemplate을 호출하는 공통 등록 흐름.
   * 클릭 업로드(요구사항 8.3)와 드래그&드롭(요구사항 8.5)이 동일하게 사용한다.
   */
  async _registerWithName(filePath) {
    const api = window.electronAPI;
    if (!api || typeof api.registerTemplate !== 'function') {
      this._setUploadStatus('템플릿 등록 기능을 사용할 수 없습니다', 'error');
      return;
    }
    const name = await this._promptTemplateName(filePath);
    if (name === null) return; // 이름 입력 취소 → 상태 무변경
    if (!name) {
      this._setUploadStatus('템플릿 이름을 입력해야 합니다', 'error');
      return;
    }
    this._setUploadStatus('템플릿 등록 중…', 'info');
    let res;
    try {
      res = await api.registerTemplate({ filePath, name });
    } catch (e) {
      console.error('[template-panel] registerTemplate failed:', e);
      this._setUploadStatus('템플릿 등록 중 오류가 발생했습니다', 'error');
      return;
    }
    // 에러 응답({error}) 처리 — 패널에 메시지 표시.
    if (res && res.error) {
      this._setUploadStatus(this._uploadErrorMessage(res), 'error');
      return;
    }
    // 성공 → 목록 재로딩 (요구사항 8.1).
    const okName = (res && res.name) || name;
    this._setUploadStatus(`'${okName}' 템플릿이 등록되었습니다`, 'info');
    await this._refresh();
  }

  /**
   * registerTemplate 에러 응답을 사람이 읽을 메시지로 변환한다.
   * 백엔드 에러 이름(요구사항 1/9)을 한국어 안내로 매핑한다.
   */
  _uploadErrorMessage(res) {
    const code = res && res.error ? String(res.error) : '';
    const map = {
      'invalid-name': '템플릿 이름은 1~100자여야 합니다',
      'template-too-large': '템플릿 파일이 너무 큽니다 (최대 50MB)',
      'invalid-template': '유효한 .pptx 템플릿이 아닙니다',
      'duplicate-name': '같은 이름의 템플릿이 이미 있습니다',
      'no-storage-root': '템플릿을 저장할 위치를 찾을 수 없습니다',
      'template-store-write-failed': '템플릿 저장에 실패했습니다',
      'missing-dep': 'python-pptx 라이브러리가 설치되어 있지 않습니다',
    };
    const base = map[code] || `템플릿 등록에 실패했습니다 (${code || 'unknown'})`;
    // 백엔드가 추가 힌트/메시지를 주면 함께 노출(최대 200자).
    const detail = res && (res.hint || res.message);
    if (detail) return `${base} — ${String(detail).slice(0, 200)}`;
    return base;
  }

  /**
   * 보조 업로드 진입점: 패널 영역에 `.pptx` 파일 드래그&드롭. (요구사항 8.5)
   * dragover의 기본 동작을 막아 드롭을 허용하고, 드롭된 항목 중 `.pptx`만
   * 등록 흐름으로 넘긴다. `.pptx`가 아닌 드롭은 무시한다.
   * Electron 렌더러에서 드롭된 File 객체는 `.path`로 절대 경로를 노출한다.
   */
  _bindDragAndDrop() {
    const prevent = (e) => { e.preventDefault(); e.stopPropagation(); };
    this.addEventListener('dragenter', prevent);
    this.addEventListener('dragover', (e) => {
      prevent(e);
      this.classList.add('tp-drag-over');
    });
    this.addEventListener('dragleave', (e) => {
      prevent(e);
      // 패널 바깥으로 완전히 벗어났을 때만 강조 해제.
      if (!this.contains(e.relatedTarget)) this.classList.remove('tp-drag-over');
    });
    this.addEventListener('drop', async (e) => {
      prevent(e);
      this.classList.remove('tp-drag-over');
      const filePath = this._extractDroppedPptxPath(e);
      if (!filePath) {
        // .pptx 가 아닌 드롭은 무시 (요구사항 8.5).
        return;
      }
      await this._registerWithName(filePath);
    });
  }

  /**
   * drop 이벤트에서 첫 번째 `.pptx` 파일 경로를 추출한다.
   * Electron에서 드롭된 File 객체는 `.path` 속성을 노출하며, 일부 경로는
   * 이벤트 자체(e.path)로 들어올 수도 있어 둘 다 처리한다.
   * `.pptx`가 없으면 null을 반환한다.
   */
  _extractDroppedPptxPath(e) {
    const isPptx = (p) => /\.pptx$/i.test(String(p || ''));
    const dt = e && e.dataTransfer;
    if (dt && dt.files && dt.files.length) {
      for (const f of dt.files) {
        const p = f && (f.path || f.name);
        if (isPptx(f && f.path ? f.path : p)) {
          return f.path || p;
        }
      }
    }
    // 폴백: e.path 가 직접 경로일 수 있음.
    if (typeof e.path === 'string' && isPptx(e.path)) return e.path;
    return null;
  }

  _renderList() {
    const list = this.querySelector('.tp-list');
    if (!list) return;

    // "템플릿 없음" 기본 선택 옵션을 항상 목록 맨 위에 둔다 (요구사항 5.6).
    const noneActive = this._selectedId === NO_TEMPLATE_ID ? ' tp-item-active' : '';
    const noneHtml = `
      <div class="tp-none-item${noneActive}" data-template-id="" role="button" tabindex="0">
        <div class="tp-none-icon">—</div>
        <div class="tp-info"><div class="tp-name">템플릿 없음</div></div>
        ${this._selectedId === NO_TEMPLATE_ID ? '<div class="tp-item-actions"><span class="tp-use-badge">✓ 사용 중</span></div>' : ''}
      </div>
    `;

    if (!this._templates.length) {
      // 빈 목록: "템플릿 없음" 옵션 + 안내 메시지 (요구사항 5.6, 8.9).
      list.innerHTML = noneHtml + '<div class="tp-empty">등록된 템플릿이 없습니다</div>';
      this._bindListEvents();
      return;
    }

    const itemsHtml = this._templates.map((t) => {
      const id = (t && (t.templateId || t.id)) || '';
      const name = t && t.name ? t.name : '(이름 없음)';
      const created = this._formatTime(t && t.createdAt);
      const active = this._selectedId && this._selectedId === id ? ' tp-item-active' : '';
      const escId = this._escape(id);
      const escName = this._escape(name);

      // 삭제 확인 단계 (요구사항 8.10): 이 항목이 확인 대기 중이면 확정/취소 행 표시.
      let confirmHtml = '';
      if (this._pendingDelete && this._pendingDelete.id === id) {
        confirmHtml = `
          <div class="tp-confirm">
            <div class="tp-confirm-msg">'${escName}' 템플릿을 삭제할까요? 이 작업은 되돌릴 수 없습니다.</div>
            <div class="tp-confirm-actions">
              <button class="tp-confirm-btn tp-confirm-yes" type="button" data-confirm-id="${escId}">확정</button>
              <button class="tp-confirm-btn tp-confirm-no" type="button" data-cancel-id="${escId}">취소</button>
            </div>
          </div>
        `;
      }

      // 삭제 실패 에러 메시지 유지 (요구사항 8.13).
      let errorHtml = '';
      if (this._deleteError && this._deleteError.id === id) {
        errorHtml = `<div class="tp-item-error">${this._escape(this._deleteError.message)}</div>`;
      }

      return `
        <div class="tp-item${active}" data-template-id="${escId}" data-name="${escName}" role="button" tabindex="0">
          <div class="tp-icon">PPT</div>
          <div class="tp-info">
            <div class="tp-name" title="${escName}">${escName}</div>
            <div class="tp-meta-row">${this._escape(created)}</div>
          </div>
          <div class="tp-item-actions">
            <button class="tp-use-btn${active ? ' tp-use-active' : ''}" type="button" data-use-id="${escId}" title="이 템플릿의 배경·스타일·양식으로 생성">${active ? '✓ 사용 중' : '사용'}</button>
            <button class="tp-del-btn" type="button" title="삭제" data-delete-id="${escId}" data-delete-name="${escName}">✕</button>
          </div>
          ${confirmHtml}
          ${errorHtml}
        </div>
      `;
    }).join('');

    list.innerHTML = noneHtml + itemsHtml;
    this._bindListEvents();
  }

  /**
   * 목록(.tp-list) 내부 요소들의 클릭/키보드 이벤트를 배선한다.
   * 선택(요구사항 8.7), 삭제 요청·확정·취소(요구사항 8.10, 8.11)를 처리한다.
   */
  _bindListEvents() {
    const list = this.querySelector('.tp-list');
    if (!list) return;

    // "템플릿 없음" 선택 → 빈 templateId로 선택 처리 (요구사항 5.6).
    const noneItem = list.querySelector('.tp-none-item');
    if (noneItem) {
      const selectNone = () => this._onSelect(NO_TEMPLATE_ID);
      noneItem.addEventListener('click', selectNone);
      noneItem.addEventListener('keydown', (e) => {
        if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); selectNone(); }
      });
    }

    // 각 템플릿 항목: 본문 클릭 → 선택/미리보기 (요구사항 8.7).
    list.querySelectorAll('.tp-item').forEach((el) => {
      const id = el.getAttribute('data-template-id') || '';
      el.addEventListener('click', (e) => {
        // 삭제 버튼/확인 버튼/사용 버튼 클릭은 행 선택 핸들러에서 제외(별도 처리).
        if (e.target.closest('.tp-del-btn, .tp-confirm, .tp-use-btn')) return;
        this._onSelect(id);
      });
      el.addEventListener('keydown', (e) => {
        if ((e.key === 'Enter' || e.key === ' ') && !e.target.closest('.tp-del-btn, .tp-confirm, .tp-use-btn')) {
          e.preventDefault();
          this._onSelect(id);
        }
      });
    });

    // "사용"/"사용 중" 버튼 → 해당 템플릿을 활성 템플릿으로 선택 (요구사항 5.1).
    // 클릭 시 그 템플릿의 배경·스타일·양식으로 이후 생성물이 작성된다.
    list.querySelectorAll('.tp-use-btn').forEach((btn) => {
      btn.addEventListener('click', (e) => {
        e.stopPropagation();
        this._onSelect(btn.getAttribute('data-use-id') || '');
      });
    });

    // 삭제 affordance(✕) → 확인 단계 열기 (요구사항 8.10).
    list.querySelectorAll('.tp-del-btn').forEach((btn) => {
      btn.addEventListener('click', (e) => {
        e.stopPropagation();
        const id = btn.getAttribute('data-delete-id') || '';
        const name = btn.getAttribute('data-delete-name') || '';
        this._confirmDelete(id, name);
      });
    });

    // 확정 → 실제 삭제 (요구사항 8.11/8.13).
    list.querySelectorAll('.tp-confirm-yes').forEach((btn) => {
      btn.addEventListener('click', (e) => {
        e.stopPropagation();
        this._doDelete(btn.getAttribute('data-confirm-id') || '');
      });
    });

    // 취소 → 확인 단계 닫기, 삭제하지 않음 (요구사항 8.10).
    list.querySelectorAll('.tp-confirm-no').forEach((btn) => {
      btn.addEventListener('click', (e) => {
        e.stopPropagation();
        this._cancelDelete();
      });
    });
  }

  /**
   * 템플릿 선택 처리. (요구사항 5.6, 8.7)
   *  - 선택 상태(_selectedId) 갱신 후 활성 표시 재렌더.
   *  - template:selected CustomEvent를 document에 디스패치(빈 id면 무템플릿).
   *  - "템플릿 없음" 외의 선택이면 Style_Profile을 조회해 미리보기 렌더.
   */
  async _onSelect(id) {
    const templateId = id || NO_TEMPLATE_ID;
    this._selectedId = templateId;
    // 다른 항목 선택 시 이전 삭제 에러 메시지는 해제.
    this._deleteError = null;

    // 선택 이벤트 디스패치 (요구사항 5.6). main.js가 state.activeTemplateId로 반영.
    document.dispatchEvent(new CustomEvent('template:selected', {
      detail: { templateId },
    }));

    // 활성 표시를 갱신.
    this._renderList();

    if (templateId === NO_TEMPLATE_ID) {
      // "템플릿 없음" → 미리보기 비움.
      this._previewProfile = null;
      this._renderPreview(null);
      return;
    }

    // Style_Profile 조회 → 미리보기 (요구사항 8.7).
    const api = window.electronAPI;
    if (!api || typeof api.getTemplateStyleProfile !== 'function') {
      this._renderPreview({ error: 'unavailable' });
      return;
    }
    let res;
    try {
      res = await api.getTemplateStyleProfile(templateId);
    } catch (e) {
      console.error('[template-panel] getTemplateStyleProfile failed:', e);
      this._renderPreview({ error: 'load-failed' });
      return;
    }
    // 선택이 그 사이 바뀌었으면 무시(경쟁 조건 방지).
    if (this._selectedId !== templateId) return;
    const profile = this._extractProfile(res);
    this._previewProfile = profile && !profile.error ? profile : null;
    this._renderPreview(profile);
  }

  /**
   * getTemplateStyleProfile 응답에서 Style_Profile dict를 추출한다.
   * 응답은 프로파일 dict 자체이거나 { styleProfile: {...} } 또는 { error } 형태일 수 있다.
   */
  _extractProfile(res) {
    if (!res || typeof res !== 'object') return { error: 'load-failed' };
    if (res.error) return { error: String(res.error) };
    if (res.styleProfile && typeof res.styleProfile === 'object') return res.styleProfile;
    return res;
  }

  /**
   * 미리보기 영역(#tp-preview)에 Style_Profile 색 견본 + 폰트를 렌더한다. (요구사항 8.7)
   * profile이 null이면 영역을 비우고, { error }면 에러 메시지를 표시한다.
   */
  _renderPreview(profile) {
    const host = this.querySelector('#tp-preview');
    if (!host) return;

    if (!profile) {
      host.innerHTML = '';
      return;
    }
    if (profile.error) {
      const msg = profile.error === 'unavailable'
        ? '미리보기 기능을 사용할 수 없습니다'
        : '스타일 미리보기를 불러오지 못했습니다';
      host.innerHTML = `<div class="tp-preview-error">${this._escape(msg)}</div>`;
      return;
    }

    host.innerHTML = `
      <div class="tp-preview-title">스타일 미리보기</div>
      ${this._renderSwatches(profile)}
      ${this._renderFonts(profile)}
    `;
  }

  /**
   * primary/accent/text/background 색을 #RRGGBB 라벨이 붙은 색상 견본으로 렌더한다.
   * (요구사항 8.7)
   */
  _renderSwatches(profile) {
    const swatches = [
      { label: '주 색상', value: profile.primaryColor },
      { label: '강조 색상', value: profile.accentColor },
      { label: '텍스트 색상', value: profile.textColor },
      { label: '배경 색상', value: profile.backgroundColor },
    ];
    const cells = swatches.map((s) => {
      const value = typeof s.value === 'string' ? s.value : '';
      const safeColor = /^#[0-9a-fA-F]{6}$/.test(value) ? value : 'transparent';
      return `
        <div class="tp-swatch">
          <span class="tp-swatch-chip" style="background:${this._escape(safeColor)}"></span>
          <span class="tp-swatch-meta">
            <span class="tp-swatch-label">${this._escape(s.label)}</span><br>
            <span class="tp-swatch-value">${this._escape(value || '—')}</span>
          </span>
        </div>
      `;
    }).join('');
    return `<div class="tp-swatches">${cells}</div>`;
  }

  /** headingFont/bodyFont 패밀리 이름을 텍스트로 렌더한다. (요구사항 8.7) */
  _renderFonts(profile) {
    const heading = typeof profile.headingFont === 'string' && profile.headingFont ? profile.headingFont : '—';
    const body = typeof profile.bodyFont === 'string' && profile.bodyFont ? profile.bodyFont : '—';
    return `
      <div class="tp-fonts">
        <div class="tp-font-row">
          <span class="tp-font-label">제목 폰트</span>
          <span class="tp-font-value" title="${this._escape(heading)}">${this._escape(heading)}</span>
        </div>
        <div class="tp-font-row">
          <span class="tp-font-label">본문 폰트</span>
          <span class="tp-font-value" title="${this._escape(body)}">${this._escape(body)}</span>
        </div>
      </div>
    `;
  }

  /**
   * 삭제 확인 단계를 연다. (요구사항 8.10)
   * 대상 이름을 포함한 확정/취소 인라인 확인 행을 해당 항목 아래에 표시하며,
   * 확정 전에는 디렉토리를 제거하지 않는다.
   */
  _confirmDelete(id, name) {
    if (!id) return;
    this._pendingDelete = { id, name: name || '' };
    // 새 확인을 열 때 이전 에러는 해제.
    this._deleteError = null;
    this._renderList();
  }

  /** 삭제 확인을 닫고 아무 것도 삭제하지 않는다. (요구사항 8.10 취소) */
  _cancelDelete() {
    this._pendingDelete = null;
    this._renderList();
  }

  /**
   * 삭제 확정 처리. (요구사항 8.8, 8.11, 8.13)
   *  - electronAPI.deleteTemplate(id) 호출.
   *  - 성공 시 목록에서 항목 제거 후 재렌더 (요구사항 8.11).
   *  - 'template-delete-failed' 에러 시 항목 유지 + 에러 메시지 표시 (요구사항 8.13).
   */
  async _doDelete(id) {
    if (!id) return;
    const api = window.electronAPI;
    if (!api || typeof api.deleteTemplate !== 'function') {
      this._pendingDelete = null;
      this._deleteError = { id, message: '삭제 기능을 사용할 수 없습니다' };
      this._renderList();
      return;
    }
    let res;
    try {
      res = await api.deleteTemplate(id);
    } catch (e) {
      console.error('[template-panel] deleteTemplate failed:', e);
      this._pendingDelete = null;
      this._deleteError = { id, message: '템플릿 삭제 중 오류가 발생했습니다' };
      this._renderList();
      return;
    }

    // 확인 단계는 응답과 무관하게 닫는다.
    this._pendingDelete = null;

    // 실패 응답 → 항목 유지 + 에러 메시지 (요구사항 8.13).
    if (res && res.error) {
      const detail = res.detail ? ` — ${String(res.detail).slice(0, 200)}` : '';
      const base = res.error === 'template-delete-failed'
        ? '템플릿 삭제에 실패했습니다'
        : `템플릿 삭제에 실패했습니다 (${String(res.error)})`;
      this._deleteError = { id, message: `${base}${detail}` };
      this._renderList();
      return;
    }

    // 성공 → 목록에서 제거 후 재렌더 (요구사항 8.11).
    this._deleteError = null;
    this._templates = this._templates.filter((t) => {
      const tid = (t && (t.templateId || t.id)) || '';
      return tid !== id;
    });
    // 삭제된 항목이 현재 선택/미리보기 대상이면 "템플릿 없음"으로 되돌린다.
    if (this._selectedId === id) {
      this._selectedId = NO_TEMPLATE_ID;
      this._previewProfile = null;
      this._renderPreview(null);
      document.dispatchEvent(new CustomEvent('template:selected', {
        detail: { templateId: NO_TEMPLATE_ID },
      }));
    }
    this._renderList();
  }

  /**
   * 등록된 템플릿이 하나도 없을 때 안내 메시지를 표시한다. (요구사항 8.9)
   * 빈 상태 처리는 _renderList()가 직접 수행하므로("템플릿 없음" 옵션 + 안내),
   * 이 메서드는 하위 호환을 위해 위임만 한다.
   */
  _renderEmpty() {
    this._renderList();
  }

  _escape(s) {
    return String(s || '').replace(/[&<>"']/g, (c) => ({
      '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;'
    }[c]));
  }
}

if (!customElements.get('template-panel')) {
  customElements.define('template-panel', TemplatePanel);
}

if (typeof window !== 'undefined') {
  window.TemplatePanel = TemplatePanel;
}
