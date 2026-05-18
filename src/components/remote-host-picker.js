'use strict';
/**
 * <remote-host-picker> — SSH host selection modal + ad-hoc add flow.
 *
 * Feature: remote-ssh · Tasks 25.1, 25.2
 * Requirements: 2.1, 2.2, 2.3, 2.4, 2.5, 2.6, 10.5
 *
 * Contract:
 *  - No shadow DOM (per .kiro/steering/ui.md).
 *  - CSS variables from `src/styles/variables.css` loaded globally (e.g.
 *    `var(--color-bg-primary)`, `--color-success`, etc.) — no @import here.
 *  - Dispatches `CustomEvent('connect', {detail:{alias}})` and
 *    `CustomEvent('favorite', {detail:{alias, favorite}})` so external
 *    listeners can observe user intent (in addition to the direct IPC
 *    calls used for backward compatibility with the current renderer).
 *
 * Keyboard:
 *  - Escape     : closes the ad-hoc modal if open, otherwise closes the picker.
 *  - Enter      : connects to the focused host row.
 *  - ArrowUp/Dn : moves focus between host rows.
 *
 * Security:
 *  - Ad-hoc form captures only {alias, hostName, user, port, identityFile}.
 *    Private key contents / passphrases are NEVER collected here (Req 10.5).
 */
(function () {
  const STATE_LABELS = {
    disconnected: '',
    connecting: '연결 중',
    authenticating: '인증 중',
    provisioning: '프로비저닝',
    forwarding: '포워딩',
    connected: '연결됨',
    reconnecting: '재연결',
    failed: '실패',
  };
  const STATE_COLORS = {
    disconnected: 'var(--color-text-muted)',
    connecting: 'var(--color-warning)',
    authenticating: 'var(--color-warning)',
    provisioning: 'var(--color-warning)',
    forwarding: 'var(--color-warning)',
    connected: 'var(--color-success)',
    reconnecting: 'var(--color-warning)',
    failed: 'var(--color-error)',
  };

  function esc(s) {
    return String(s == null ? '' : s)
      .replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;').replace(/"/g, '&quot;');
  }

  class RemoteHostPicker extends HTMLElement {
    constructor() {
      super();
      this._hosts = [];          // host entries fetched from IPC
      this._states = new Map();  // alias -> connection state ("connected", ...)
      this._loading = true;
      this._filter = '';
      this._showAdHoc = false;   // modal open flag
      this._focusIndex = -1;     // keyboard navigation cursor
      this._unsubscribeState = null;
      this._onKey = null;
    }

    connectedCallback() {
      this.style.display = this.style.display || 'block';
      this._render();
      this._load();
      // Live state updates — update the badge next to the matching alias
      // without re-fetching the list (Req 2.3 state badges).
      if (window.electronAPI && typeof window.electronAPI.onRemoteState === 'function') {
        try {
          const cleanup = window.electronAPI.onRemoteState((ev) => {
            if (!ev || !ev.alias) return;
            this.updateHost(ev.alias, ev.to || ev.state, ev.localPort);
          });
          if (typeof cleanup === 'function') this._unsubscribeState = cleanup;
        } catch (_e) { /* ignore — renderer without subscription support */ }
      }
      this._onKey = (e) => this._handleGlobalKey(e);
      document.addEventListener('keydown', this._onKey);
    }

    disconnectedCallback() {
      if (this._onKey) { document.removeEventListener('keydown', this._onKey); this._onKey = null; }
      if (typeof this._unsubscribeState === 'function') {
        try { this._unsubscribeState(); } catch (_e) { /* ignore */ }
        this._unsubscribeState = null;
      }
    }

    // --- Public API ---------------------------------------------------------

    show() {
      this.style.display = 'block';
      if (!this.isConnected) document.body.appendChild(this);
      this._render();
      setTimeout(() => this.querySelector('.rhp-search')?.focus(), 0);
      return this;
    }

    hide() {
      this.style.display = 'none';
      this._showAdHoc = false;
    }

    /** Update a single host's connection state badge (live). */
    updateHost(alias, state, localPort) {
      if (!alias) return;
      this._states.set(alias, { state: state || 'disconnected', localPort });
      // Surgical update: patch only the matching badge to avoid re-render jitter.
      const badge = this.querySelector(`[data-state-for="${CSS.escape(String(alias))}"]`);
      if (badge) {
        const lbl = STATE_LABELS[state] || '';
        const color = STATE_COLORS[state] || 'var(--color-text-muted)';
        badge.textContent = lbl;
        badge.style.color = color;
        badge.style.borderColor = color;
        badge.style.display = lbl ? 'inline-block' : 'none';
      }
    }

    // --- Data loading -------------------------------------------------------

    async _load() {
      this._loading = true;
      this._render();
      try {
        const api = window.electronAPI;
        if (api && typeof api.remoteListHosts === 'function') {
          const data = await api.remoteListHosts();
          // IPC returns either an array or {entries, diagnostics} — accept both.
          const list = Array.isArray(data) ? data : (data && data.entries) || [];
          this._hosts = list;
        } else {
          this._hosts = [];
        }
      } catch (e) {
        console.error('[remote-host-picker] load failed:', e);
        this._hosts = [];
      }
      this._loading = false;
      this._render();
    }

    // --- Event handlers -----------------------------------------------------

    _handleGlobalKey(e) {
      if (this.style.display === 'none') return;
      if (e.key === 'Escape') {
        if (this._showAdHoc) { this._showAdHoc = false; this._render(); e.preventDefault(); }
        else { this.hide(); e.preventDefault(); }
        return;
      }
      if (this._showAdHoc) return; // modal owns its own keys
      const rows = Array.from(this.querySelectorAll('.rhp-host'));
      if (e.key === 'ArrowDown' && rows.length) {
        this._focusIndex = Math.min(rows.length - 1, Math.max(0, this._focusIndex + 1));
        rows[this._focusIndex]?.focus(); e.preventDefault();
      } else if (e.key === 'ArrowUp' && rows.length) {
        this._focusIndex = Math.max(0, this._focusIndex - 1);
        rows[this._focusIndex]?.focus(); e.preventDefault();
      } else if (e.key === 'Enter') {
        const focused = document.activeElement;
        if (focused && focused.classList && focused.classList.contains('rhp-host')) {
          this._connect(focused.dataset.alias); e.preventDefault();
        }
      }
    }

    async _connect(alias) {
      if (!alias) return;
      // External observers (testing, logging) via CustomEvent.
      this.dispatchEvent(new CustomEvent('connect', { detail: { alias }, bubbles: true }));
      this.hide();
      try {
        if (window.electronAPI && typeof window.electronAPI.remoteConnect === 'function') {
          const res = await window.electronAPI.remoteConnect({ alias });
          if (res && res.ok === false) {
            alert(`연결 실패: ${res.error || 'unknown'}`);
          }
        }
      } catch (e) {
        console.error('[remote-host-picker] connect failed:', e);
      }
    }

    async _toggleFavorite(alias, favorite) {
      if (!alias) return;
      // Optimistic UI update.
      const host = this._hosts.find(h => h.alias === alias);
      if (host) host.favorite = favorite;
      this._render();
      this.dispatchEvent(new CustomEvent('favorite', { detail: { alias, favorite }, bubbles: true }));
      try {
        if (window.electronAPI && typeof window.electronAPI.remoteSetFavorite === 'function') {
          await window.electronAPI.remoteSetFavorite({ alias, favorite });
        }
      } catch (e) {
        console.error('[remote-host-picker] setFavorite failed:', e);
      }
    }

    // --- Ad-hoc modal (Task 25.2) ------------------------------------------

    async _submitAdHoc() {
      const alias = this.querySelector('#rhp-ah-alias')?.value?.trim();
      const hostName = this.querySelector('#rhp-ah-host')?.value?.trim();
      const user = this.querySelector('#rhp-ah-user')?.value?.trim() || '';
      const portRaw = this.querySelector('#rhp-ah-port')?.value;
      const port = Number.parseInt(portRaw, 10) || 22;
      const identityFile = this.querySelector('#rhp-ah-key')?.value?.trim() || '';

      if (!alias || !hostName) {
        alert('Alias와 HostName은 필수입니다.');
        return;
      }
      try {
        if (window.electronAPI && typeof window.electronAPI.remoteAddAdHocHost === 'function') {
          const res = await window.electronAPI.remoteAddAdHocHost({ alias, hostName, user, port, identityFile });
          if (res && res.ok === false) {
            alert(`추가 실패: ${res.error || 'unknown'}`);
            return;
          }
        }
      } catch (e) {
        console.error('[remote-host-picker] addAdHocHost failed:', e);
        alert(`추가 실패: ${e && e.message ? e.message : e}`);
        return;
      }
      this._showAdHoc = false;
      await this._load(); // refresh list with the newly added ad-hoc entry
    }

    // --- Rendering ----------------------------------------------------------

    _sortedGroups() {
      const q = this._filter.toLowerCase();
      const matches = (h) => !q || (h.alias || '').toLowerCase().includes(q);
      const byAlias = (a, b) => String(a.alias).localeCompare(String(b.alias));
      const favorites = this._hosts.filter(h => h.favorite && matches(h)).sort(byAlias);
      const others = this._hosts.filter(h => !h.favorite && matches(h)).sort(byAlias);
      return { favorites, others };
    }

    _render() {
      const { favorites, others } = this._sortedGroups();
      const hosts = this._hosts;
      const empty = hosts.length === 0;

      this.innerHTML = `
        <div class="rhp-overlay" style="position:fixed;inset:0;z-index:9999;background:rgba(0,0,0,0.5);display:flex;align-items:flex-start;justify-content:center;padding-top:80px;font-family:var(--font-ui);">
          <div class="rhp-panel" role="dialog" aria-label="Remote host picker" style="width:520px;max-width:90vw;background:var(--color-bg-primary);border:1px solid var(--color-border);border-radius:var(--border-radius);box-shadow:0 10px 40px rgba(0,0,0,0.5);display:flex;flex-direction:column;max-height:70vh;">
            <div style="padding:12px 16px;border-bottom:1px solid var(--color-border);display:flex;align-items:center;gap:8px;">
              <span style="font-size:14px;font-weight:700;color:var(--color-text-primary);">Remote: Connect to Host</span>
              <span style="flex:1"></span>
              <button class="rhp-close" aria-label="Close" style="background:none;border:none;color:var(--color-text-secondary);font-size:16px;cursor:pointer;">✕</button>
            </div>
            <div style="padding:8px 16px;border-bottom:1px solid var(--color-border);">
              <input class="rhp-search" type="text" placeholder="호스트 검색..." value="${esc(this._filter)}" style="width:100%;padding:8px 12px;background:var(--color-bg-secondary);border:1px solid var(--color-border);border-radius:var(--border-radius);color:var(--color-text-primary);font-size:13px;outline:none;" autocomplete="off">
            </div>
            <div class="rhp-list" style="flex:1;overflow-y:auto;padding:8px 0;">
              ${this._loading ? '<div style="padding:20px;text-align:center;color:var(--color-text-muted);font-size:12px;">불러오는 중...</div>' : ''}
              ${!this._loading && empty ? `
                <div style="padding:28px 20px;text-align:center;color:var(--color-text-muted);font-size:12px;">
                  등록된 호스트가 없습니다.<br>
                  <button class="rhp-add-btn" style="margin-top:12px;padding:8px 16px;background:var(--color-accent);border:none;border-radius:var(--border-radius);color:#fff;font-size:12px;font-weight:600;cursor:pointer;">+ Add ad-hoc host</button>
                </div>` : ''}
              ${favorites.length ? '<div style="padding:6px 16px 2px;font-size:10px;color:var(--color-text-muted);text-transform:uppercase;letter-spacing:0.5px;">★ Favorites</div>' : ''}
              ${favorites.map(h => this._renderHost(h)).join('')}
              ${others.length ? `<div style="padding:6px 16px 2px;font-size:10px;color:var(--color-text-muted);text-transform:uppercase;letter-spacing:0.5px;${favorites.length ? 'margin-top:4px;' : ''}">All hosts</div>` : ''}
              ${others.map(h => this._renderHost(h)).join('')}
            </div>
            ${!this._loading && !empty ? '<div style="padding:8px 16px;border-top:1px solid var(--color-border);display:flex;justify-content:flex-end;"><button class="rhp-add-btn" style="padding:6px 14px;background:transparent;border:1px solid var(--color-border);border-radius:var(--border-radius);color:var(--color-text-primary);font-size:12px;cursor:pointer;">+ Add ad-hoc host</button></div>' : ''}
          </div>
        </div>
        ${this._showAdHoc ? this._renderAdHocModal() : ''}
      `;
      this._bindEvents();
    }

    _renderHost(h) {
      const alias = String(h.alias || '');
      const known = this._states.get(alias);
      const state = (known && known.state) || h.state || 'disconnected';
      const stateLabel = STATE_LABELS[state] || '';
      const stateColor = STATE_COLORS[state] || 'var(--color-text-muted)';
      const starFilled = Boolean(h.favorite);
      const star = starFilled ? '★' : '☆';
      const starColor = starFilled ? 'var(--color-warning)' : 'var(--color-text-muted)';
      const userPrefix = h.user ? `${esc(h.user)}@` : '';

      return `<div class="rhp-host" role="button" tabindex="0" data-alias="${esc(alias)}"
        style="padding:10px 16px;cursor:pointer;display:flex;align-items:center;gap:10px;transition:background var(--transition);outline:none;"
        onmouseover="this.style.background='var(--color-bg-hover)'"
        onmouseout="this.style.background='transparent'"
        onfocus="this.style.background='var(--color-bg-hover)'"
        onblur="this.style.background='transparent'">
        <button class="rhp-star" data-alias="${esc(alias)}" data-fav="${starFilled ? '1' : '0'}" title="${starFilled ? 'Unfavorite' : 'Favorite'}"
          style="background:transparent;border:none;color:${starColor};font-size:14px;cursor:pointer;padding:0;width:18px;line-height:1;">${star}</button>
        <span style="font-size:13px;color:var(--color-text-primary);font-weight:500;">${esc(alias)}</span>
        <span style="font-size:11px;color:var(--color-text-muted);">${userPrefix}${esc(h.hostName || '')}${h.port ? ':' + esc(h.port) : ''}</span>
        <span style="flex:1"></span>
        <span class="rhp-state-badge" data-state-for="${esc(alias)}"
          style="font-size:10px;padding:1px 6px;border-radius:var(--border-radius);border:1px solid ${stateColor};color:${stateColor};display:${stateLabel ? 'inline-block' : 'none'};">${esc(stateLabel)}</span>
        ${h.source ? `<span style="font-size:10px;color:var(--color-text-muted);background:var(--color-bg-tertiary);padding:2px 6px;border-radius:var(--border-radius);">${esc(h.source)}</span>` : ''}
      </div>`;
    }

    _renderAdHocModal() {
      return `
        <div class="rhp-ah-overlay" style="position:fixed;inset:0;z-index:10000;background:rgba(0,0,0,0.55);display:flex;align-items:center;justify-content:center;font-family:var(--font-ui);">
          <div role="dialog" aria-label="Add ad-hoc host" style="width:420px;max-width:92vw;background:var(--color-bg-secondary);color:var(--color-text-primary);border:1px solid var(--color-border);border-radius:var(--border-radius);padding:20px;box-shadow:0 10px 40px rgba(0,0,0,0.6);">
            <h3 style="margin:0 0 14px;font-size:14px;font-weight:700;">Ad-hoc 호스트 추가</h3>
            <label style="font-size:11px;color:var(--color-text-secondary);display:block;margin-bottom:4px;">Alias *</label>
            <input id="rhp-ah-alias" style="width:100%;padding:8px;background:var(--color-bg-primary);border:1px solid var(--color-border);border-radius:var(--border-radius);color:var(--color-text-primary);font-size:13px;margin-bottom:10px;outline:none;box-sizing:border-box;" placeholder="my-server" autocomplete="off">
            <label style="font-size:11px;color:var(--color-text-secondary);display:block;margin-bottom:4px;">HostName *</label>
            <input id="rhp-ah-host" style="width:100%;padding:8px;background:var(--color-bg-primary);border:1px solid var(--color-border);border-radius:var(--border-radius);color:var(--color-text-primary);font-size:13px;margin-bottom:10px;outline:none;box-sizing:border-box;" placeholder="10.0.0.5 또는 hostname.example.com" autocomplete="off">
            <div style="display:flex;gap:8px;margin-bottom:10px;">
              <div style="flex:1">
                <label style="font-size:11px;color:var(--color-text-secondary);display:block;margin-bottom:4px;">User</label>
                <input id="rhp-ah-user" style="width:100%;padding:8px;background:var(--color-bg-primary);border:1px solid var(--color-border);border-radius:var(--border-radius);color:var(--color-text-primary);font-size:13px;outline:none;box-sizing:border-box;" placeholder="ubuntu" autocomplete="off">
              </div>
              <div style="width:90px">
                <label style="font-size:11px;color:var(--color-text-secondary);display:block;margin-bottom:4px;">Port</label>
                <input id="rhp-ah-port" type="number" min="1" max="65535" value="22" style="width:100%;padding:8px;background:var(--color-bg-primary);border:1px solid var(--color-border);border-radius:var(--border-radius);color:var(--color-text-primary);font-size:13px;outline:none;box-sizing:border-box;">
              </div>
            </div>
            <label style="font-size:11px;color:var(--color-text-secondary);display:block;margin-bottom:4px;">IdentityFile (경로만, 키 내용 저장 안 함)</label>
            <input id="rhp-ah-key" style="width:100%;padding:8px;background:var(--color-bg-primary);border:1px solid var(--color-border);border-radius:var(--border-radius);color:var(--color-text-primary);font-size:13px;margin-bottom:16px;outline:none;box-sizing:border-box;" placeholder="~/.ssh/id_ed25519" autocomplete="off">
            <div style="display:flex;gap:8px;justify-content:flex-end;">
              <button class="rhp-ah-cancel" style="padding:8px 16px;background:transparent;border:1px solid var(--color-border);border-radius:var(--border-radius);color:var(--color-text-primary);font-size:12px;cursor:pointer;">취소</button>
              <button class="rhp-ah-submit" style="padding:8px 16px;background:var(--color-accent);border:none;border-radius:var(--border-radius);color:#fff;font-size:12px;font-weight:600;cursor:pointer;">추가</button>
            </div>
          </div>
        </div>`;
    }

    _bindEvents() {
      // Main overlay click → close.
      this.querySelector('.rhp-overlay')?.addEventListener('click', (e) => {
        if (e.target.classList.contains('rhp-overlay')) this.hide();
      });
      this.querySelector('.rhp-close')?.addEventListener('click', () => this.hide());

      // Search filter.
      const search = this.querySelector('.rhp-search');
      if (search) {
        search.addEventListener('input', (e) => {
          this._filter = e.target.value || '';
          // Re-render list region only to preserve input focus — simplest:
          // full re-render, then re-focus the input with caret at end.
          const caret = e.target.selectionStart;
          this._render();
          const again = this.querySelector('.rhp-search');
          if (again) { again.focus(); try { again.setSelectionRange(caret, caret); } catch (_e) {} }
        });
      }

      // Host row click → connect.
      this.querySelectorAll('.rhp-host').forEach((el, idx) => {
        el.addEventListener('click', (e) => {
          // Star button has its own handler; don't double-fire.
          if (e.target.closest('.rhp-star')) return;
          this._connect(el.dataset.alias);
        });
        el.addEventListener('keydown', (e) => {
          if (e.key === 'Enter' || e.key === ' ') {
            if (e.target.classList.contains('rhp-star')) return;
            this._connect(el.dataset.alias);
            e.preventDefault();
          }
        });
        el.addEventListener('focus', () => { this._focusIndex = idx; });
      });

      // Favorite star toggle.
      this.querySelectorAll('.rhp-star').forEach((btn) => {
        btn.addEventListener('click', (e) => {
          e.stopPropagation();
          const alias = btn.dataset.alias;
          const current = btn.dataset.fav === '1';
          this._toggleFavorite(alias, !current);
        });
      });

      // Add ad-hoc host buttons (empty state and footer).
      this.querySelectorAll('.rhp-add-btn').forEach((btn) => {
        btn.addEventListener('click', () => {
          this._showAdHoc = true;
          this._render();
          setTimeout(() => this.querySelector('#rhp-ah-alias')?.focus(), 0);
        });
      });

      // Ad-hoc modal bindings (when open).
      if (this._showAdHoc) {
        this.querySelector('.rhp-ah-overlay')?.addEventListener('click', (e) => {
          if (e.target.classList.contains('rhp-ah-overlay')) {
            this._showAdHoc = false; this._render();
          }
        });
        this.querySelector('.rhp-ah-cancel')?.addEventListener('click', () => {
          this._showAdHoc = false; this._render();
        });
        this.querySelector('.rhp-ah-submit')?.addEventListener('click', () => this._submitAdHoc());
        // Submit on Enter inside any input (except textarea).
        this.querySelectorAll('.rhp-ah-overlay input').forEach((inp) => {
          inp.addEventListener('keydown', (e) => {
            if (e.key === 'Enter') { this._submitAdHoc(); e.preventDefault(); }
          });
        });
      }
    }

    /**
     * Backward-compatible static helper. Ensures a single instance exists in
     * the document and returns it after calling `.show()`. Existing callers
     * (remote-status-bar, remote-ad-hoc-dialog) rely on this entry point.
     */
    static show() {
      let el = document.querySelector('remote-host-picker');
      if (!el) {
        el = document.createElement('remote-host-picker');
        document.body.appendChild(el);
      }
      el.show();
      return el;
    }
  }

  if (!customElements.get('remote-host-picker')) {
    customElements.define('remote-host-picker', RemoteHostPicker);
  }
  window.RemoteHostPicker = RemoteHostPicker;

  // Command-palette entry point requested by task 25.1.
  window.openRemoteHostPicker = () => {
    const existing = document.querySelector('remote-host-picker');
    if (existing && typeof existing.show === 'function') { existing.show(); return existing; }
    return RemoteHostPicker.show();
  };
})();
