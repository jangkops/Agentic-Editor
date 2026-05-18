'use strict';
/**
 * <remote-auth-dialog> — passphrase / password / keyboard-interactive (2FA)
 * input modal for the Remote SSH feature.
 *
 * Feature: remote-ssh · Task 27.1
 * Requirements: 3.3 (passphrase prompt), 3.9 (keyboard-interactive 2FA
 * verbatim prompts with echo flag), 10.1 (never persist secrets — wipe
 * immediately on submit), 10.4 (respond via IPC and release memory).
 *
 * Contract with the main process:
 *   - Subscribes on `connectedCallback` via `electronAPI.onRemoteAuthRequest`.
 *   - Each request object the main process may emit:
 *       { alias, kind, key?, requestId?, payload?: {host?,user?,text?,echo?},
 *         prompt?, echo? }
 *     where `kind ∈ {'passphrase','password','keyboard-interactive','2fa'}`.
 *     The component normalises both shapes.
 *   - On submit:   electronAPI.remoteRespondAuth({alias, kind, key, payload})
 *     where payload is the raw answer scalar expected by
 *     `RemoteSession.respondAuth()` in the main process.
 *   - On cancel:   electronAPI.remoteRespondAuth({alias, kind, key,
 *                                                  payload: null})
 *
 * Security notes:
 *   - No shadow DOM (per UI steering), rendered inline.
 *   - Password value is read once, forwarded to IPC, the DOM input is
 *     cleared, and the entire dialog subtree is removed from the DOM —
 *     so no secret string is retained on any element or in a component
 *     field. No console logging of the value, no localStorage.
 *   - Multiple inbound requests queue; only one is rendered at a time.
 */
(function () {
  function esc(s) {
    return String(s == null ? '' : s)
      .replace(/&/g, '&amp;')
      .replace(/</g, '&lt;')
      .replace(/>/g, '&gt;')
      .replace(/"/g, '&quot;');
  }

  // Normalise either the task-27.1 wire format or the legacy
  // RemoteSession._promptAuth() shape into a single internal record.
  function normaliseRequest(req) {
    if (!req || typeof req !== 'object') return null;
    const payload = (req.payload && typeof req.payload === 'object') ? req.payload : {};
    let kind = String(req.kind || '').toLowerCase();
    // Backend currently emits '2fa'; task spec uses 'keyboard-interactive'.
    if (kind === '2fa') kind = 'keyboard-interactive';
    if (kind !== 'passphrase' && kind !== 'password' && kind !== 'keyboard-interactive') {
      return null;
    }
    return {
      alias: String(req.alias || ''),
      kind,
      // Forward the original key so the main process can target the
      // exact pending prompt (supports concurrent 2FA challenges).
      key: req.key || req.requestId || '',
      host: String(payload.host || req.host || ''),
      user: String(payload.user || req.user || ''),
      promptText: String(payload.text || req.prompt || ''),
      echo: Boolean(payload.echo != null ? payload.echo : req.echo),
    };
  }

  class RemoteAuthDialog extends HTMLElement {
    constructor() {
      super();
      this._queue = [];
      this._current = null;
      this._unsubscribe = null;
      this._onKeydown = null;
    }

    connectedCallback() {
      const api = (typeof window !== 'undefined') ? window.electronAPI : null;
      if (api && typeof api.onRemoteAuthRequest === 'function') {
        const off = api.onRemoteAuthRequest((req) => this._enqueue(req));
        // onRemoteAuthRequest returns a cleanup fn per preload contract.
        if (typeof off === 'function') this._unsubscribe = off;
      }
    }

    disconnectedCallback() {
      if (typeof this._unsubscribe === 'function') {
        try { this._unsubscribe(); } catch (_e) { /* swallow */ }
        this._unsubscribe = null;
      }
      this._detachKeydown();
      this._clearDom();
      this._queue.length = 0;
      this._current = null;
    }

    _enqueue(req) {
      const entry = normaliseRequest(req);
      if (!entry) return;
      this._queue.push(entry);
      if (!this._current) this._advance();
    }

    _advance() {
      this._current = this._queue.shift() || null;
      if (!this._current) {
        this._detachKeydown();
        this._clearDom();
        return;
      }
      this._render();
    }

    _render() {
      const c = this._current;
      if (!c) { this._clearDom(); return; }

      let title, label;
      if (c.kind === 'passphrase') {
        title = 'SSH 키 패스프레이즈';
        label = 'Enter passphrase for identity file';
      } else if (c.kind === 'password') {
        title = '비밀번호';
        const who = c.user ? (c.user + '@' + (c.host || c.alias)) : (c.host || c.alias);
        label = 'Password for ' + who;
      } else {
        // keyboard-interactive
        title = '2단계 인증';
        // Display the server's prompt verbatim (Req 3.9).
        label = c.promptText || 'Server challenge';
      }

      const inputType = (c.kind === 'keyboard-interactive' && c.echo) ? 'text' : 'password';
      const aliasLine = c.alias ? ('별칭: ' + esc(c.alias)) : '';

      this.innerHTML =
        '<div class="rad-overlay" role="dialog" aria-modal="true" ' +
        'style="position:fixed;inset:0;z-index:10001;' +
        'background:var(--color-bg-primary);background:rgba(0,0,0,0.55);' +
        'display:flex;align-items:center;justify-content:center;' +
        'font-family:var(--font-ui,sans-serif);">' +
          '<div class="rad-dialog" ' +
          'style="width:420px;max-width:90vw;' +
          'background:var(--color-bg-secondary);color:var(--color-text-primary);' +
          'border:1px solid var(--color-border);' +
          'border-radius:var(--border-radius);padding:20px;' +
          'box-shadow:0 10px 40px rgba(0,0,0,0.5);">' +
            '<h3 style="margin:0 0 6px;font-size:14px;font-weight:600;">' +
              esc(title) + '</h3>' +
            (aliasLine ? ('<p style="margin:0 0 4px;font-size:11px;' +
              'color:var(--color-text-muted);">' + aliasLine + '</p>') : '') +
            '<p class="rad-label" style="margin:0 0 12px;font-size:12px;' +
            'color:var(--color-text-secondary);white-space:pre-wrap;' +
            'word-break:break-word;">' + esc(label) + '</p>' +
            '<input id="rad-input" type="' + inputType + '" ' +
            'style="width:100%;box-sizing:border-box;padding:10px;' +
            'background:var(--color-bg-primary);' +
            'border:1px solid var(--color-border);' +
            'border-radius:var(--border-radius);' +
            'color:var(--color-text-primary);font-size:13px;outline:none;' +
            'font-family:' + (c.kind === 'keyboard-interactive' && c.echo ?
              'var(--font-mono)' : 'inherit') + ';" ' +
            'autocomplete="off" autocapitalize="off" autocorrect="off" ' +
            'spellcheck="false">' +
            '<div style="display:flex;gap:8px;justify-content:flex-end;' +
            'margin-top:14px;">' +
              '<button type="button" class="rad-cancel" ' +
              'style="padding:8px 14px;background:transparent;' +
              'border:1px solid var(--color-border);' +
              'border-radius:var(--border-radius);' +
              'color:var(--color-text-primary);font-size:12px;cursor:pointer;">' +
              '취소</button>' +
              '<button type="button" class="rad-submit" ' +
              'style="padding:8px 14px;background:var(--color-accent);' +
              'border:none;border-radius:var(--border-radius);color:#fff;' +
              'font-size:12px;font-weight:600;cursor:pointer;">확인</button>' +
            '</div>' +
          '</div>' +
        '</div>';

      const input = this.querySelector('#rad-input');
      if (input) {
        // Focus synchronously first, then after microtask as a safety
        // net for browsers that deprioritise focus in the current tick.
        try { input.focus(); } catch (_e) { /* ignore */ }
        setTimeout(() => { try { input.focus(); } catch (_e) {} }, 0);
      }

      const submitBtn = this.querySelector('.rad-submit');
      const cancelBtn = this.querySelector('.rad-cancel');
      if (submitBtn) submitBtn.addEventListener('click', () => this._submit());
      if (cancelBtn) cancelBtn.addEventListener('click', () => this._cancel());

      // Global Enter/Escape — the input has focus, so attaching here
      // also covers the case where focus drifts to a button.
      this._detachKeydown();
      this._onKeydown = (e) => {
        if (e.key === 'Enter') { e.preventDefault(); this._submit(); }
        else if (e.key === 'Escape') { e.preventDefault(); this._cancel(); }
      };
      this.addEventListener('keydown', this._onKeydown);
    }

    _detachKeydown() {
      if (this._onKeydown) {
        this.removeEventListener('keydown', this._onKeydown);
        this._onKeydown = null;
      }
    }

    _clearDom() {
      if (this.firstChild) this.innerHTML = '';
    }

    _submit() {
      const c = this._current;
      if (!c) return;
      const input = this.querySelector('#rad-input');
      // Read the value exactly once, wipe the input synchronously.
      // Req 10.1: we must not retain the secret in component state.
      let value = input ? input.value : '';
      if (input) input.value = '';
      // Release the current request reference BEFORE touching the DOM so
      // that even a synchronous exception path cannot keep a handle.
      this._current = null;
      this._detachKeydown();
      this._clearDom();
      const api = (typeof window !== 'undefined') ? window.electronAPI : null;
      if (api && typeof api.remoteRespondAuth === 'function') {
        try {
          api.remoteRespondAuth({
            alias: c.alias,
            kind: (c.kind === 'keyboard-interactive') ? '2fa' : c.kind,
            key: c.key || undefined,
            payload: value,
          });
        } catch (_e) { /* never rethrow — losing the prompt is safer than
                         echoing the secret via an error propagation. */ }
      }
      // Explicitly drop the local reference.
      value = null;
      // Drain any queued request (e.g. multi-step keyboard-interactive).
      this._advance();
    }

    _cancel() {
      const c = this._current;
      this._current = null;
      this._detachKeydown();
      this._clearDom();
      if (!c) return;
      const api = (typeof window !== 'undefined') ? window.electronAPI : null;
      if (api && typeof api.remoteRespondAuth === 'function') {
        try {
          api.remoteRespondAuth({
            alias: c.alias,
            kind: (c.kind === 'keyboard-interactive') ? '2fa' : c.kind,
            key: c.key || undefined,
            payload: null,
          });
        } catch (_e) { /* swallow */ }
      }
      this._advance();
    }
  }

  if (typeof customElements !== 'undefined' &&
      !customElements.get('remote-auth-dialog')) {
    customElements.define('remote-auth-dialog', RemoteAuthDialog);
  }
})();
