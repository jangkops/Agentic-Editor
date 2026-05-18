// Feature: remote-ssh — <remote-status-bar> Web Component
// Requirements: 2.7, 12.1

(function () {
  'use strict';

  // Inject pulse animation style once
  const STYLE_ID = 'remote-status-bar-styles';
  if (!document.getElementById(STYLE_ID)) {
    const style = document.createElement('style');
    style.id = STYLE_ID;
    style.textContent = `
      @keyframes pulse {
        0%, 100% { opacity: 1; }
        50% { opacity: 0.4; }
      }
      remote-status-bar {
        display: inline-flex;
        align-items: center;
        gap: 6px;
        font-family: var(--font-ui);
        font-size: 11px;
        padding: 2px 8px;
        border-radius: var(--border-radius);
        cursor: pointer;
        user-select: none;
        color: var(--color-text-muted);
        transition: background 150ms;
      }
      remote-status-bar:hover {
        background: var(--color-bg-hover);
      }
      remote-status-bar[data-state="connecting"] {
        color: var(--color-warning);
      }
      remote-status-bar[data-state="connected"] {
        color: var(--color-success);
      }
      remote-status-bar[data-state="reconnecting"] {
        color: var(--color-warning);
        animation: pulse 1s infinite;
      }
      remote-status-bar[data-state="failed"] {
        color: var(--color-error);
      }
      remote-status-bar[data-state="disconnected"],
      remote-status-bar:not([data-state]) {
        color: var(--color-text-muted);
      }
    `;
    document.head.appendChild(style);
  }

  const STATE_LABELS = {
    connecting: 'connecting',
    connected: 'connected',
    authenticating: 'authenticating',
    provisioning: 'provisioning',
    forwarding: 'forwarding',
    reconnecting: 'reconnecting',
    failed: 'failed',
    disconnected: 'disconnected'
  };

  class RemoteStatusBar extends HTMLElement {
    constructor() {
      super();
      this._cleanup = null;
      this._alias = '';
      this._state = 'disconnected';
    }

    connectedCallback() {
      this._render();
      this.addEventListener('click', this._onClick);

      // Fetch initial state
      if (window.electronAPI && typeof window.electronAPI.remoteStatus === 'function') {
        window.electronAPI.remoteStatus().then((status) => {
          if (status) {
            // status is { [alias]: { state, localPort?, error? } }
            const aliases = Object.keys(status);
            if (aliases.length > 0) {
              // Find the first connected or non-disconnected session, or just the first
              const active = aliases.find((a) => status[a].state === 'connected')
                || aliases.find((a) => status[a].state !== 'disconnected')
                || aliases[0];
              this._alias = active;
              this._state = status[active].state || 'disconnected';
              this._update();
            }
          }
        }).catch(() => { /* ignore — no backend yet */ });
      }

      // Subscribe to live state updates
      if (window.electronAPI && typeof window.electronAPI.onRemoteState === 'function') {
        this._cleanup = window.electronAPI.onRemoteState((evt) => {
          if (evt && evt.alias) {
            this._alias = evt.alias;
            this._state = evt.to || 'disconnected';
            this._update();
          }
        });
      }
    }

    disconnectedCallback() {
      this.removeEventListener('click', this._onClick);
      if (typeof this._cleanup === 'function') {
        this._cleanup();
        this._cleanup = null;
      }
    }

    _onClick() {
      if (this._state === 'connected' && this._alias) {
        // Show context menu: Disconnect / Change Workspace / Show Log
        this._showContextMenu();
      } else {
        if (typeof window.openRemoteHostPicker === 'function') {
          window.openRemoteHostPicker();
        }
      }
    }

    _showContextMenu() {
      // Remove any existing menu
      const existing = document.getElementById('rsb-context-menu');
      if (existing) existing.remove();

      const menu = document.createElement('div');
      menu.id = 'rsb-context-menu';
      menu.style.cssText = 'position:fixed;bottom:28px;left:8px;z-index:10000;background:var(--color-bg-secondary);border:1px solid var(--color-border);border-radius:var(--border-radius);box-shadow:0 4px 16px rgba(0,0,0,0.4);font-family:var(--font-ui);font-size:12px;min-width:200px;padding:4px 0;';
      menu.innerHTML = `
        <div class="rsb-menu-item" data-action="workspace" style="padding:8px 14px;cursor:pointer;color:var(--color-text-primary);transition:background 100ms;" onmouseenter="this.style.background='var(--color-bg-hover)'" onmouseleave="this.style.background='transparent'">📁 Change Workspace...</div>
        <div class="rsb-menu-item" data-action="picker" style="padding:8px 14px;cursor:pointer;color:var(--color-text-primary);transition:background 100ms;" onmouseenter="this.style.background='var(--color-bg-hover)'" onmouseleave="this.style.background='transparent'">🔌 Switch Host...</div>
        <div style="border-top:1px solid var(--color-border);margin:4px 0"></div>
        <div class="rsb-menu-item" data-action="disconnect" style="padding:8px 14px;cursor:pointer;color:var(--color-error);transition:background 100ms;" onmouseenter="this.style.background='var(--color-bg-hover)'" onmouseleave="this.style.background='transparent'">⏏ Disconnect</div>
      `;
      document.body.appendChild(menu);

      const self = this;
      menu.addEventListener('click', async (e) => {
        const item = e.target.closest('[data-action]');
        if (!item) return;
        menu.remove();
        const action = item.dataset.action;
        if (action === 'workspace') {
          self._promptWorkspace();
        } else if (action === 'picker') {
          if (typeof window.openRemoteHostPicker === 'function') window.openRemoteHostPicker();
        } else if (action === 'disconnect') {
          if (window.electronAPI?.remoteDisconnect) {
            await window.electronAPI.remoteDisconnect({ alias: self._alias });
          }
        }
      });

      // Close on outside click
      const closeMenu = (e) => {
        if (!menu.contains(e.target) && e.target !== this) {
          menu.remove();
          document.removeEventListener('click', closeMenu);
        }
      };
      setTimeout(() => document.addEventListener('click', closeMenu), 0);
    }

    _promptWorkspace() {
      const currentPath = (typeof window.state === 'object' && window.state && window.state.folderPath) || '/';
      const alias = this._alias;
      if (!alias) return;

      if (window.RemoteWorkspacePicker) {
        const picker = window.RemoteWorkspacePicker.show({ alias, startPath: currentPath });
        picker.addEventListener('select', (ev) => {
          const newPath = ev.detail && ev.detail.path;
          if (!newPath) return;
          if (typeof window.state !== 'undefined') window.state.folderPath = newPath;
          const pathText = document.getElementById('file-tree-path-text');
          if (pathText) {
            pathText.textContent = `[SSH: ${alias}] ${newPath}`;
            pathText.title = `Remote host: ${alias} — ${newPath}`;
          }
          if (typeof window.loadFileTree === 'function') {
            window.loadFileTree(newPath);
          }
        }, { once: true });
        return;
      }

      // Fallback: legacy prompt
      const newPath = prompt('원격 작업 디렉토리 경로를 입력하세요:', currentPath);
      if (!newPath || newPath === currentPath) return;
      if (window.electronAPI?.remoteSetWorkspace) {
        window.electronAPI.remoteSetWorkspace({ alias: this._alias, remotePath: newPath });
      }
      if (typeof window.state !== 'undefined') window.state.folderPath = newPath;
      const pathText = document.getElementById('file-tree-path-text');
      if (pathText) {
        pathText.textContent = `[SSH: ${this._alias}] ${newPath}`;
        pathText.title = `Remote host: ${this._alias} — ${newPath}`;
      }
      if (typeof window.loadFileTree === 'function') {
        window.loadFileTree(newPath);
      }
    }

    _render() {
      this.innerHTML = '<span class="rsb-dot">●</span><span class="rsb-label"></span>';
      this._update();
    }

    _update() {
      const state = this._state || 'disconnected';
      this.setAttribute('data-state', state);

      const labelEl = this.querySelector('.rsb-label');
      if (labelEl) {
        const alias = this._alias || '';
        const stateText = STATE_LABELS[state] || state;
        labelEl.textContent = alias ? alias + ' · ' + stateText : stateText;
      }
    }
  }

  customElements.define('remote-status-bar', RemoteStatusBar);
})();
