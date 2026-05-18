'use strict';
/**
 * <remote-workspace-picker> — directory picker for the active remote SSH host.
 *
 * Lists remote directories via the existing `fs:list-files` IPC
 * (which routes through `sessionRouter.getFileBridge()` when remote is
 * active — see electron/src/ipc-fs-handlers.js). The user navigates
 * through the tree and selects a folder to use as the workspace.
 *
 * On submit, calls `electronAPI.remoteSetWorkspace({alias, remotePath})`
 * to persist the choice, then dispatches `CustomEvent('select',
 * {detail:{path}})` so the caller can update the file tree.
 */
(function () {
  function esc(s) {
    return String(s == null ? '' : s)
      .replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;').replace(/"/g, '&quot;');
  }

  class RemoteWorkspacePicker extends HTMLElement {
    constructor() {
      super();
      this._currentPath = '/';
      this._alias = '';
      this._entries = [];
      this._loading = false;
      this._onKeyDown = this._onKeyDown.bind(this);
    }

    connectedCallback() {
      document.addEventListener('keydown', this._onKeyDown, true);
    }

    disconnectedCallback() {
      document.removeEventListener('keydown', this._onKeyDown, true);
    }

    _onKeyDown(ev) {
      if (this.style.display === 'none') return;
      if (ev.key === 'Escape') {
        ev.preventDefault(); ev.stopPropagation();
        this.hide();
      }
    }

    /**
     * Open the picker at the given starting path (default: home of alias).
     * @param {{alias: string, startPath?: string}} opts
     */
    async show(opts) {
      const { alias, startPath } = opts || {};
      if (!alias) return;
      this._alias = alias;
      this._currentPath = startPath || '/';
      this.style.display = 'block';
      if (!this.isConnected) document.body.appendChild(this);
      this._render();
      await this._load();
    }

    hide() {
      this.style.display = 'none';
      this.innerHTML = '';
    }

    async _load() {
      this._loading = true;
      this._render();
      try {
        const list = await window.electronAPI?.readDir?.(this._currentPath);
        this._entries = Array.isArray(list)
          ? list.filter(e => e.isDirectory).sort((a, b) => a.name.localeCompare(b.name))
          : [];
      } catch (e) {
        console.error('[remote-workspace-picker] readDir failed:', e);
        this._entries = [];
      }
      this._loading = false;
      this._render();
    }

    _navigate(newPath) {
      this._currentPath = newPath;
      this._load();
    }

    _parentPath() {
      const p = this._currentPath;
      if (!p || p === '/') return '/';
      const trimmed = p.replace(/\/+$/, '');
      const idx = trimmed.lastIndexOf('/');
      return idx <= 0 ? '/' : trimmed.slice(0, idx);
    }

    async _selectCurrent() {
      const path = this._currentPath;
      try {
        if (window.electronAPI?.remoteSetWorkspace) {
          await window.electronAPI.remoteSetWorkspace({ alias: this._alias, remotePath: path });
        }
      } catch (_e) { /* ignore — selection still succeeds */ }
      this.dispatchEvent(new CustomEvent('select', { detail: { path, alias: this._alias }, bubbles: true }));
      this.hide();
    }

    _render() {
      const path = this._currentPath;
      const entries = this._entries;
      const parentDisabled = path === '/' || path === '';

      this.innerHTML = `
        <div class="rwp-overlay" style="position:fixed;inset:0;z-index:10003;background:rgba(0,0,0,0.55);display:flex;align-items:flex-start;justify-content:center;padding-top:80px;font-family:var(--font-ui);">
          <div role="dialog" aria-label="Remote workspace picker" style="width:560px;max-width:92vw;background:var(--color-bg-primary);border:1px solid var(--color-border);border-radius:var(--border-radius);box-shadow:0 10px 40px rgba(0,0,0,0.5);display:flex;flex-direction:column;max-height:70vh;">
            <div style="padding:12px 16px;border-bottom:1px solid var(--color-border);display:flex;align-items:center;gap:8px;">
              <span style="font-size:14px;font-weight:700;color:var(--color-text-primary);">📁 Select Remote Workspace</span>
              <span style="flex:1"></span>
              <button class="rwp-close" aria-label="Close" style="background:none;border:none;color:var(--color-text-secondary);font-size:16px;cursor:pointer;">✕</button>
            </div>
            <div style="padding:8px 16px;border-bottom:1px solid var(--color-border);display:flex;gap:6px;align-items:center;">
              <button class="rwp-up" ${parentDisabled ? 'disabled' : ''} style="padding:4px 10px;background:var(--color-bg-secondary);border:1px solid var(--color-border);border-radius:var(--border-radius);color:var(--color-text-primary);font-size:12px;cursor:${parentDisabled ? 'not-allowed' : 'pointer'};opacity:${parentDisabled ? 0.5 : 1};">↑</button>
              <input class="rwp-path" type="text" value="${esc(path)}" style="flex:1;padding:6px 10px;background:var(--color-bg-secondary);border:1px solid var(--color-border);border-radius:var(--border-radius);color:var(--color-text-primary);font-size:12px;font-family:var(--font-mono);outline:none;">
              <button class="rwp-go" style="padding:4px 10px;background:var(--color-bg-secondary);border:1px solid var(--color-border);border-radius:var(--border-radius);color:var(--color-text-primary);font-size:12px;cursor:pointer;">Go</button>
            </div>
            <div class="rwp-list" style="flex:1;overflow-y:auto;padding:4px 0;">
              ${this._loading ? '<div style="padding:20px;text-align:center;color:var(--color-text-muted);font-size:12px;">로드 중...</div>' : ''}
              ${!this._loading && entries.length === 0 ? '<div style="padding:20px;text-align:center;color:var(--color-text-muted);font-size:12px;">하위 디렉토리 없음</div>' : ''}
              ${entries.map(e => `
                <div class="rwp-entry" data-path="${esc(e.path)}" style="padding:6px 16px;cursor:pointer;display:flex;align-items:center;gap:8px;font-size:12px;color:var(--color-text-primary);transition:background var(--transition);" onmouseover="this.style.background='var(--color-bg-hover)'" onmouseout="this.style.background='transparent'">
                  <span style="color:var(--color-accent);">📁</span>
                  <span>${esc(e.name)}</span>
                </div>
              `).join('')}
            </div>
            <div style="padding:10px 16px;border-top:1px solid var(--color-border);display:flex;gap:8px;align-items:center;">
              <span style="flex:1;font-size:11px;color:var(--color-text-muted);">선택한 디렉토리: <strong style="color:var(--color-text-primary);">${esc(path)}</strong></span>
              <button class="rwp-cancel" style="padding:6px 14px;background:transparent;border:1px solid var(--color-border);border-radius:var(--border-radius);color:var(--color-text-primary);font-size:12px;cursor:pointer;">취소</button>
              <button class="rwp-select" style="padding:6px 14px;background:var(--color-accent);border:none;border-radius:var(--border-radius);color:#fff;font-size:12px;font-weight:600;cursor:pointer;">이 폴더 선택</button>
            </div>
          </div>
        </div>
      `;
      this._bindEvents();
    }

    _bindEvents() {
      this.querySelector('.rwp-close')?.addEventListener('click', () => this.hide());
      this.querySelector('.rwp-cancel')?.addEventListener('click', () => this.hide());
      this.querySelector('.rwp-overlay')?.addEventListener('click', (e) => {
        if (e.target.classList.contains('rwp-overlay')) this.hide();
      });
      this.querySelector('.rwp-up')?.addEventListener('click', () => {
        this._navigate(this._parentPath());
      });
      this.querySelector('.rwp-go')?.addEventListener('click', () => {
        const input = this.querySelector('.rwp-path');
        if (input) this._navigate(input.value || '/');
      });
      this.querySelector('.rwp-path')?.addEventListener('keydown', (e) => {
        if (e.key === 'Enter') {
          this._navigate(e.target.value || '/');
          e.preventDefault();
        }
      });
      this.querySelectorAll('.rwp-entry').forEach((el) => {
        el.addEventListener('click', () => this._navigate(el.dataset.path));
      });
      this.querySelector('.rwp-select')?.addEventListener('click', () => this._selectCurrent());
    }

    static show(opts) {
      let el = document.querySelector('remote-workspace-picker');
      if (!el) {
        el = document.createElement('remote-workspace-picker');
        document.body.appendChild(el);
      }
      el.show(opts);
      return el;
    }
  }

  if (!customElements.get('remote-workspace-picker')) {
    customElements.define('remote-workspace-picker', RemoteWorkspacePicker);
  }
  window.RemoteWorkspacePicker = RemoteWorkspacePicker;
})();
