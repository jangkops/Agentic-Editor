'use strict';
/**
 * <file-preview-panel> — generated media file browser.
 *
 * Lists files in `.generated/` (relative to active project), sorted by mtime
 * descending. Click a file to preview in the editor area:
 *  - .png/.jpg/.jpeg/.webp → image-viewer
 *  - .pdf → pdf-viewer
 *  - .pptx → pptx-viewer
 *  - .docx → docx-viewer
 *  - .xlsx → xlsx-viewer
 *
 * Auto-refreshes when files are added/removed via fs:directory-changed.
 *
 * Each generated file may have a sidecar `<file>.meta.json` written by the
 * server-side orchestrator capturing { tool, model, agentId, agentRole,
 * createdAt, promptHint }. The panel reads these to display a model badge so
 * the user can see which model produced each artifact.
 *
 * Usage: <file-preview-panel project-path="/path/to/project"></file-preview-panel>
 */

class FilePreviewPanel extends HTMLElement {
  constructor() {
    super();
    this._items = [];
    this._projectPath = '';
    this._watchedDir = '';
    this._unsub = null;
    this._metaCache = new Map(); // path -> meta object
  }

  static get observedAttributes() { return ['project-path']; }

  attributeChangedCallback(name, oldVal, newVal) {
    if (name === 'project-path' && oldVal !== newVal) {
      this._projectPath = newVal || '';
      this._refresh();
      this._setupWatcher();
    }
  }

  connectedCallback() {
    if (!this._projectPath) {
      this._projectPath = this.getAttribute('project-path') || '';
    }
    this._render();
    this._refresh();
    this._setupWatcher();
    this._onSelect = (e) => {
      const detail = e?.detail || {};
      const target = detail.path || detail.fullPath || detail.name;
      if (!target) return;
      this._highlightItem(target);
    };
    document.addEventListener('preview-panel:select', this._onSelect);
  }

  disconnectedCallback() {
    if (this._unsub) try { this._unsub(); } catch {}
    if (this._onSelect) {
      document.removeEventListener('preview-panel:select', this._onSelect);
      this._onSelect = null;
    }
    if (this._watchedDir && window.electronAPI && typeof window.electronAPI.unwatchDirectory === 'function') {
      try { window.electronAPI.unwatchDirectory(this._watchedDir); } catch {}
    }
  }

  _highlightItem(target) {
    const norm = (s) => (s || '').toLowerCase().replace(/\\/g, '/');
    const t = norm(target);
    const list = this.querySelector('.fpp-list');
    if (!list) return;
    const rows = list.querySelectorAll('.fpp-item');
    rows.forEach((row) => {
      const rp = norm(row.dataset.path || '');
      const rn = norm(row.dataset.name || '');
      const match = rp === t || rp.endsWith(t) || rn === t.split('/').pop();
      row.classList.toggle('fpp-item-active', match);
      if (match) {
        try { row.scrollIntoView({ block: 'nearest', behavior: 'smooth' }); } catch {}
      }
    });
  }

  set projectPath(p) {
    this._projectPath = p || '';
    this._refresh();
    this._setupWatcher();
  }

  get projectPath() { return this._projectPath; }

  _generatedDir() {
    if (!this._projectPath) return '';
    const p = this._projectPath;
    const isRemoteLike = /^\/fsx\/|^\/home\/|^\/opt\//.test(p) || p.includes('[SSH:');
    if (isRemoteLike && this._workstationCwd) {
      const sep = this._workstationCwd.includes('\\') && !this._workstationCwd.includes('/') ? '\\' : '/';
      const base = this._workstationCwd.replace(/[\\/]+$/, '');
      return `${base}${sep}.generated`;
    }
    const sep = p.includes('\\') && !p.includes('/') ? '\\' : '/';
    const base = p.replace(/[\\/]+$/, '');
    return `${base}${sep}.generated`;
  }

  async _setupWatcher() {
    if (!window.electronAPI || typeof window.electronAPI.watchDirectory !== 'function') return;
    const dir = this._generatedDir();
    if (!dir) return;
    if (this._watchedDir === dir) return;
    if (this._watchedDir) {
      try { window.electronAPI.unwatchDirectory(this._watchedDir); } catch {}
    }
    this._watchedDir = dir;
    try { await window.electronAPI.watchDirectory(dir); } catch {}
    if (this._unsub) try { this._unsub(); } catch {}
    this._unsub = window.electronAPI.onDirectoryChanged((data) => {
      if (data && data.dirPath === dir) {
        clearTimeout(this._refreshDebounce);
        this._refreshDebounce = setTimeout(() => this._refresh(), 400);
      }
    });
  }

  async _refresh() {
    const dir = this._generatedDir();
    if (!dir || !window.electronAPI || typeof window.electronAPI.listFilesWithStats !== 'function') {
      this._items = [];
      this._renderList();
      return;
    }
    try {
      const items = await window.electronAPI.listFilesWithStats(dir);
      // Filter: drop directories AND hide .meta.json sidecars from the list
      const files = (items || [])
        .filter(it => !it.isDirectory && !/\.meta\.json$/i.test(it.name))
        .sort((a, b) => {
          const ta = a.mtime ? Date.parse(a.mtime) : 0;
          const tb = b.mtime ? Date.parse(b.mtime) : 0;
          return tb - ta;
        })
        .slice(0, 100);
      this._items = files;
      // Read meta sidecars in parallel; missing meta is fine
      await this._loadMetas(files);
      this._renderList();
    } catch (e) {
      console.error('[file-preview-panel] refresh failed:', e);
      this._items = [];
      this._renderList();
    }
  }

  async _loadMetas(files) {
    if (!window.electronAPI || typeof window.electronAPI.readFile !== 'function') return;
    const promises = files.map(async (f) => {
      const metaPath = (f.path || '') + '.meta.json';
      if (this._metaCache.has(metaPath)) return;
      try {
        const txt = await window.electronAPI.readFile(metaPath);
        if (txt) this._metaCache.set(metaPath, JSON.parse(txt));
      } catch {
        // missing meta is normal — don't cache
      }
    });
    await Promise.all(promises);
  }

  _formatSize(bytes) {
    if (!bytes || bytes < 1024) return `${bytes || 0} B`;
    if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`;
    return `${(bytes / 1024 / 1024).toFixed(1)} MB`;
  }

  _formatTime(iso) {
    if (!iso) return '';
    try {
      const d = new Date(iso);
      const yyyy = d.getFullYear();
      const mm = String(d.getMonth() + 1).padStart(2, '0');
      const dd = String(d.getDate()).padStart(2, '0');
      const hh = String(d.getHours()).padStart(2, '0');
      const mi = String(d.getMinutes()).padStart(2, '0');
      return `${yyyy}-${mm}-${dd} ${hh}:${mi}`;
    } catch { return ''; }
  }

  // Text-only icon (3-letter extension code) — VS Code-style aesthetic
  _iconFor(name) {
    const ext = (name.split('.').pop() || '').toLowerCase();
    if (['png', 'jpg', 'jpeg', 'webp', 'gif'].includes(ext)) return 'IMG';
    if (ext === 'pdf')  return 'PDF';
    if (ext === 'pptx') return 'PPT';
    if (ext === 'docx') return 'DOC';
    if (ext === 'xlsx') return 'XLS';
    if (ext === 'svg')  return 'SVG';
    if (ext === 'md')   return 'MD';
    if (ext === 'json') return 'JSON';
    if (ext === 'txt')  return 'TXT';
    return ext ? ext.toUpperCase().slice(0, 4) : 'FILE';
  }

  // Compact display name for a model id ("anthropic.claude-sonnet-4-6" → "Sonnet 4.6")
  _shortModelName(modelId) {
    if (!modelId) return '';
    const id = String(modelId).replace(/^us\.|^eu\.|^global\./, '');
    const lower = id.toLowerCase();
    // Anthropic
    if (lower.includes('claude-opus-4-7'))   return 'Opus 4.7';
    if (lower.includes('claude-opus-4'))     return 'Opus 4';
    if (lower.includes('claude-sonnet-4-6')) return 'Sonnet 4.6';
    if (lower.includes('claude-sonnet-4'))   return 'Sonnet 4';
    if (lower.includes('claude-haiku'))      return 'Haiku';
    // Amazon
    if (lower.includes('nova-canvas'))       return 'Nova Canvas';
    if (lower.includes('nova-pro'))          return 'Nova Pro';
    if (lower.includes('nova-lite'))         return 'Nova Lite';
    if (lower.includes('titan-image'))       return 'Titan Image v2';
    // Stability
    if (lower.includes('stable-image-ultra')) return 'SD Ultra';
    if (lower.includes('sd3-5-large') || lower.includes('sd3.5-large')) return 'SD 3.5 Large';
    if (lower.includes('stable-image-core'))  return 'SD Core';
    if (lower.includes('stability'))          return 'Stability';
    // Local/library generators
    if (lower.includes('reportlab'))   return 'reportlab';
    if (lower.includes('python-pptx')) return 'python-pptx';
    if (lower.includes('python-docx')) return 'python-docx';
    if (lower.includes('openpyxl'))    return 'openpyxl';
    if (lower.includes('filesystem'))  return 'fs';
    // Misc
    if (lower.includes('pixtral'))     return 'Pixtral';
    if (lower.includes('mistral'))     return 'Mistral';
    if (lower.includes('llama'))       return 'Llama';
    // Fallback: take last hyphen-segment of the model name
    const parts = id.split('.').pop().split('-');
    return parts.slice(0, 3).join('-');
  }

  _metaFor(item) {
    if (!item || !item.path) return null;
    return this._metaCache.get(item.path + '.meta.json') || null;
  }

  _render() {
    this.innerHTML = `
      <style>
        file-preview-panel {
          display: flex;
          flex-direction: column;
          height: 100%;
          background: var(--color-bg-secondary, #252526);
          color: var(--color-text-primary, #ccc);
          font-family: var(--font-ui, sans-serif);
          font-size: 12px;
        }
        .fpp-header {
          padding: 10px 12px;
          border-bottom: 1px solid var(--color-border, #3c3c3c);
          display: flex;
          justify-content: space-between;
          align-items: center;
        }
        .fpp-title { font-weight: 600; }
        .fpp-refresh {
          background: none; border: 1px solid var(--color-border, #3c3c3c);
          color: var(--color-text-secondary, #9d9d9d);
          padding: 3px 8px; border-radius: 3px;
          cursor: pointer; font-size: 11px;
        }
        .fpp-refresh:hover { background: var(--color-bg-hover, #2a2d2e); color: var(--color-text-primary, #ccc); }
        .fpp-list {
          flex: 1; overflow-y: auto; padding: 4px 0;
        }
        .fpp-empty {
          padding: 20px; text-align: center;
          color: var(--color-text-muted, #6a6a6a); font-size: 11px;
        }
        .fpp-item {
          display: grid;
          grid-template-columns: auto 1fr auto;
          align-items: center;
          gap: 10px;
          padding: 8px 12px; cursor: pointer;
          border-bottom: 1px solid rgba(60,60,60,0.3);
        }
        .fpp-item:hover { background: var(--color-bg-hover, #2a2d2e); }
        .fpp-item-active { background: rgba(0,122,204,0.18); border-left: 2px solid var(--color-accent, #007acc); }
        .fpp-item-active:hover { background: rgba(0,122,204,0.24); }
        .fpp-icon {
          flex-shrink: 0;
          font-family: var(--font-mono, 'Cascadia Code', monospace);
          font-size: 9px;
          font-weight: 700;
          letter-spacing: 0.5px;
          color: var(--color-accent, #007acc);
          background: rgba(0,122,204,0.1);
          border: 1px solid rgba(0,122,204,0.4);
          border-radius: 3px;
          padding: 4px 6px;
          min-width: 36px;
          text-align: center;
          line-height: 1;
        }
        .fpp-info { min-width: 0; overflow: hidden; }
        .fpp-name {
          font-size: 12px;
          color: var(--color-text-primary, #ccc);
          overflow: hidden; text-overflow: ellipsis; white-space: nowrap;
        }
        .fpp-meta-row {
          display: flex;
          gap: 6px;
          align-items: center;
          flex-wrap: wrap;
          margin-top: 3px;
          font-size: 10px;
          color: var(--color-text-muted, #6a6a6a);
        }
        .fpp-model-badge {
          display: inline-block;
          padding: 1px 6px;
          background: rgba(78,201,176,0.12);
          border: 1px solid rgba(78,201,176,0.3);
          color: var(--color-success, #4ec9b0);
          border-radius: 2px;
          font-size: 9px;
          font-weight: 600;
          letter-spacing: 0.2px;
          font-family: var(--font-mono, monospace);
        }
        .fpp-badge-gen {
          background: rgba(78,201,176,0.14);
          border-color: rgba(78,201,176,0.4);
          color: var(--color-success, #4ec9b0);
        }
        .fpp-badge-chat {
          background: rgba(0,122,204,0.10);
          border-color: rgba(0,122,204,0.3);
          color: var(--color-accent, #007acc);
          font-weight: 500;
          opacity: 0.85;
        }
        .fpp-actions {
          display: flex;
          gap: 4px;
          opacity: 0;
          transition: opacity 100ms;
          flex-shrink: 0;
        }
        .fpp-item:hover .fpp-actions { opacity: 1; }
        .fpp-action {
          background: transparent;
          border: 1px solid var(--color-border, #3c3c3c);
          color: var(--color-text-secondary, #9d9d9d);
          cursor: pointer;
          padding: 3px 8px;
          font-size: 10px;
          border-radius: 3px;
          font-family: var(--font-ui, sans-serif);
          transition: all 120ms ease;
          line-height: 1.4;
        }
        .fpp-action:hover {
          background: var(--color-bg-tertiary, #2d2d30);
          color: var(--color-text-primary, #ccc);
          border-color: var(--color-text-secondary, #9d9d9d);
        }
        .fpp-action-edit:hover   { color: var(--color-accent,  #007acc); border-color: var(--color-accent,  #007acc); }
        .fpp-action-delete:hover { color: var(--color-error,   #f44747); border-color: var(--color-error,   #f44747); }
        .fpp-action-download:hover { color: var(--color-success, #4ec9b0); border-color: var(--color-success, #4ec9b0); }
      </style>
      <div class="fpp-header">
        <div class="fpp-title">생성 파일 (.generated)</div>
        <button class="fpp-refresh" type="button" title="새로고침">↻</button>
      </div>
      <div class="fpp-list"></div>
    `;
    this.querySelector('.fpp-refresh').addEventListener('click', () => this._refresh());
  }

  _renderList() {
    const list = this.querySelector('.fpp-list');
    if (!list) return;
    if (!this._items.length) {
      list.innerHTML = '<div class="fpp-empty">생성된 파일이 없습니다.<br>이미지, PDF, PPTX를 생성하면 여기에 표시됩니다.</div>';
      return;
    }
    list.innerHTML = this._items.map((item, idx) => {
      const meta = this._metaFor(item);
      const genModel = meta && meta.model ? this._shortModelName(meta.model) : '';
      const chatModel = meta && meta.chatModel ? this._shortModelName(meta.chatModel) : '';
      // 실제 생성 모델(이미지 모델 등)과 결정 모델(chat 모델)이 다르면 둘 다 표시
      let modelBadges = '';
      if (genModel) {
        const tip = `생성 엔진: ${this._escape(meta?.model || '')}${meta?.agentRole ? ' · ' + this._escape(meta.agentRole) : ''}`;
        modelBadges += `<span class="fpp-model-badge fpp-badge-gen" title="${tip}">${this._escape(genModel)}</span>`;
      }
      if (chatModel && chatModel !== genModel) {
        modelBadges += `<span class="fpp-model-badge fpp-badge-chat" title="결정 모델: ${this._escape(meta?.chatModel || '')}">via ${this._escape(chatModel)}</span>`;
      }
      return `
      <div class="fpp-item" data-idx="${idx}" data-path="${this._escape(item.path || item.name)}" data-name="${this._escape(item.name)}">
        <div class="fpp-icon">${this._iconFor(item.name)}</div>
        <div class="fpp-info">
          <div class="fpp-name" title="${this._escape(item.name)}">${this._escape(item.name)}</div>
          <div class="fpp-meta-row">
            ${modelBadges}
            <span>${this._formatTime(item.mtime)}</span>
            <span>·</span>
            <span>${this._formatSize(item.size)}</span>
          </div>
        </div>
        <div class="fpp-actions">
          <button class="fpp-action fpp-action-edit"     data-action="edit"     data-idx="${idx}" type="button" title="수정 — 채팅에 첨부 후 추가 지시">수정</button>
          <button class="fpp-action fpp-action-delete"   data-action="delete"   data-idx="${idx}" type="button" title="삭제 — 디스크에서 제거">삭제</button>
          <button class="fpp-action fpp-action-download" data-action="download" data-idx="${idx}" type="button" title="다운로드">다운로드</button>
        </div>
      </div>
    `;
    }).join('');

    list.querySelectorAll('.fpp-item').forEach(el => {
      el.addEventListener('click', (e) => {
        if (e.target.closest('.fpp-action')) return;
        const idx = parseInt(el.dataset.idx, 10);
        this._open(this._items[idx]);
      });
    });
    list.querySelectorAll('[data-action="edit"]').forEach(btn => {
      btn.addEventListener('click', (e) => {
        e.stopPropagation();
        const idx = parseInt(btn.dataset.idx, 10);
        this._edit(this._items[idx]);
      });
    });
    list.querySelectorAll('[data-action="delete"]').forEach(btn => {
      btn.addEventListener('click', async (e) => {
        e.stopPropagation();
        const idx = parseInt(btn.dataset.idx, 10);
        await this._delete(this._items[idx]);
      });
    });
    list.querySelectorAll('[data-action="download"]').forEach(btn => {
      btn.addEventListener('click', async (e) => {
        e.stopPropagation();
        const idx = parseInt(btn.dataset.idx, 10);
        await this._download(this._items[idx]);
      });
    });
  }

  _escape(s) {
    return String(s || '').replace(/[&<>"']/g, c => ({
      '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;'
    }[c]));
  }

  _open(item) {
    if (!item) return;
    this.dispatchEvent(new CustomEvent('preview-file', {
      bubbles: true,
      detail: { path: item.path, name: item.name, size: item.size },
    }));
  }

  // 수정: 파일을 채팅 첨부로 등록하고 사용자 입력을 기다림
  _edit(item) {
    if (!item) return;
    this.dispatchEvent(new CustomEvent('preview-file:edit', {
      bubbles: true,
      detail: {
        path: item.path,
        name: item.name,
        size: item.size,
        meta: this._metaFor(item),
      },
    }));
  }

  // 삭제: 디스크에서 제거 + 메타 사이드카도 함께 + 에디터 탭 닫기
  async _delete(item) {
    if (!item) return;
    if (!confirm(`"${item.name}" 파일을 삭제하시겠습니까?`)) return;
    try {
      if (window.electronAPI && typeof window.electronAPI.deleteFile === 'function') {
        await window.electronAPI.deleteFile(item.path);
        // 메타 사이드카도 같이 제거 (있다면)
        try { await window.electronAPI.deleteFile(item.path + '.meta.json'); } catch {}
      } else if (window.electronAPI && typeof window.electronAPI.unlink === 'function') {
        await window.electronAPI.unlink(item.path);
        try { await window.electronAPI.unlink(item.path + '.meta.json'); } catch {}
      } else {
        console.warn('[file-preview-panel] no delete API available');
        return;
      }
      // 에디터에 열려 있다면 닫기 요청
      this.dispatchEvent(new CustomEvent('preview-file:deleted', {
        bubbles: true,
        detail: { path: item.path, name: item.name },
      }));
      // 캐시 비우고 새로고침
      this._metaCache.delete(item.path + '.meta.json');
      await this._refresh();
    } catch (e) {
      console.error('[file-preview-panel] delete failed:', e);
      alert(`삭제 실패: ${e.message || e}`);
    }
  }

  async _download(item) {
    if (!item || !window.electronAPI || typeof window.electronAPI.showSaveDialog !== 'function') return;
    try {
      const ext = (item.name.split('.').pop() || '').toLowerCase();
      const result = await window.electronAPI.showSaveDialog({
        defaultPath: item.name,
        sourcePath: item.path,
        filters: [{ name: ext.toUpperCase(), extensions: [ext] }],
      });
      if (result && result.ok) {
        this.dispatchEvent(new CustomEvent('file-downloaded', {
          bubbles: true,
          detail: { source: item.path, target: result.path },
        }));
      }
    } catch (e) {
      console.error('[file-preview-panel] download failed:', e);
    }
  }
}

if (!customElements.get('file-preview-panel')) {
  customElements.define('file-preview-panel', FilePreviewPanel);
}

if (typeof window !== 'undefined') {
  window.FilePreviewPanel = FilePreviewPanel;
}
