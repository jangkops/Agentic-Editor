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
 * Usage: <file-preview-panel project-path="/path/to/project"></file-preview-panel>
 */

class FilePreviewPanel extends HTMLElement {
  constructor() {
    super();
    this._items = [];
    this._projectPath = '';
    this._watchedDir = '';
    this._unsub = null;
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
  }

  disconnectedCallback() {
    if (this._unsub) try { this._unsub(); } catch {}
    if (this._watchedDir && window.electronAPI && typeof window.electronAPI.unwatchDirectory === 'function') {
      try { window.electronAPI.unwatchDirectory(this._watchedDir); } catch {}
    }
  }

  set projectPath(p) {
    this._projectPath = p || '';
    this._refresh();
    this._setupWatcher();
  }

  get projectPath() { return this._projectPath; }

  _generatedDir() {
    if (!this._projectPath) return '';
    const sep = this._projectPath.includes('\\') && !this._projectPath.includes('/') ? '\\' : '/';
    const base = this._projectPath.replace(/[\\/]+$/, '');
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
      // Filter out dirs, sort by mtime desc, limit 100
      const files = (items || [])
        .filter(it => !it.isDirectory)
        .sort((a, b) => {
          const ta = a.mtime ? Date.parse(a.mtime) : 0;
          const tb = b.mtime ? Date.parse(b.mtime) : 0;
          return tb - ta;
        })
        .slice(0, 100);
      this._items = files;
      this._renderList();
    } catch (e) {
      console.error('[file-preview-panel] refresh failed:', e);
      this._items = [];
      this._renderList();
    }
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

  _iconFor(name) {
    const ext = (name.split('.').pop() || '').toLowerCase();
    if (['png', 'jpg', 'jpeg', 'webp', 'gif'].includes(ext)) return '🖼';
    if (ext === 'pdf') return '📄';
    if (ext === 'pptx') return '📊';
    if (ext === 'docx') return '📝';
    if (ext === 'xlsx') return '📈';
    return '📎';
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
          display: flex; align-items: center; gap: 10px;
          padding: 8px 12px; cursor: pointer;
          border-bottom: 1px solid rgba(60,60,60,0.3);
        }
        .fpp-item:hover { background: var(--color-bg-hover, #2a2d2e); }
        .fpp-icon { font-size: 18px; flex-shrink: 0; }
        .fpp-info { flex: 1; min-width: 0; }
        .fpp-name { font-size: 12px; color: var(--color-text-primary, #ccc); overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
        .fpp-meta { font-size: 10px; color: var(--color-text-muted, #6a6a6a); margin-top: 2px; }
        .fpp-actions { display: flex; gap: 4px; opacity: 0; transition: opacity 100ms; }
        .fpp-item:hover .fpp-actions { opacity: 1; }
        .fpp-action {
          background: transparent; border: none; color: var(--color-text-secondary, #9d9d9d);
          cursor: pointer; padding: 2px 4px; font-size: 12px; border-radius: 2px;
        }
        .fpp-action:hover { background: var(--color-bg-tertiary, #2d2d30); color: var(--color-text-primary, #ccc); }
      </style>
      <div class="fpp-header">
        <div class="fpp-title">생성 파일 (.generated)</div>
        <button class="fpp-refresh" type="button">↻</button>
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
    list.innerHTML = this._items.map((item, idx) => `
      <div class="fpp-item" data-idx="${idx}">
        <div class="fpp-icon">${this._iconFor(item.name)}</div>
        <div class="fpp-info">
          <div class="fpp-name" title="${item.name}">${this._escape(item.name)}</div>
          <div class="fpp-meta">${this._formatTime(item.mtime)} · ${this._formatSize(item.size)}</div>
        </div>
        <div class="fpp-actions">
          <button class="fpp-action" data-action="download" data-idx="${idx}" title="다운로드">↓</button>
        </div>
      </div>
    `).join('');

    list.querySelectorAll('.fpp-item').forEach(el => {
      el.addEventListener('click', (e) => {
        if (e.target.classList.contains('fpp-action')) return;
        const idx = parseInt(el.dataset.idx, 10);
        this._open(this._items[idx]);
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
    // Dispatch event for main.js to handle viewer dispatch
    this.dispatchEvent(new CustomEvent('preview-file', {
      bubbles: true,
      detail: { path: item.path, name: item.name, size: item.size },
    }));
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

// Export for explicit usage
if (typeof window !== 'undefined') {
  window.FilePreviewPanel = FilePreviewPanel;
}
