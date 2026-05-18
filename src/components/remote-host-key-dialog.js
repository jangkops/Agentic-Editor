'use strict';
/**
 * <remote-host-key-dialog> — TOFU host key verification prompt.
 *
 * Feature: remote-ssh · Task 27.2 · Requirements 3.6, 3.7
 *
 * Subscribes to `window.electronAPI.onRemoteHostKeyPrompt` and renders
 * a modal confirming (or rejecting) the remote host's public key on
 * first contact (`status: 'unknown'`) or change (`status: 'mismatch'`).
 *
 * Contract
 * --------
 *  prompt = {
 *    requestId?: string,
 *    alias: string,
 *    host: string,
 *    port: number,
 *    keyType?: string,
 *    fingerprint: string,         // SHA256 base64 (no 'SHA256:' prefix)
 *    status?: 'unknown' | 'mismatch'
 *  }
 *
 * Accept → electronAPI.remoteRespondAuth({alias, requestId, kind:'host-key', payload:{accept:true}})
 * Reject → electronAPI.remoteRespondAuth({alias, requestId, kind:'host-key', payload:{accept:false}})
 * Escape = Reject · Enter = Accept
 *
 * No shadow DOM. Vanilla Web Component. Design-token driven.
 */
(function () {
  function esc(s) {
    return String(s == null ? '' : s)
      .replace(/&/g, '&amp;')
      .replace(/</g, '&lt;')
      .replace(/>/g, '&gt;')
      .replace(/"/g, '&quot;');
  }

  /**
   * Format a fingerprint as `SHA256:abcd efgh ijkl ...`.
   * Strips any existing `SHA256:` prefix, then regroups the body in
   * 4-char blocks separated by a single space.
   *
   * @param {string} fp
   * @returns {string}
   */
  function formatFingerprint(fp) {
    const raw = String(fp || '').trim();
    if (!raw) return 'SHA256:(unknown)';
    const body = raw.replace(/^sha256:/i, '');
    const grouped = body.replace(/(.{4})/g, '$1 ').trim();
    return 'SHA256:' + grouped;
  }

  class RemoteHostKeyDialog extends HTMLElement {
    constructor() {
      super();
      /** @type {null | Object} */
      this._prompt = null;
      this._onKeyDown = this._onKeyDown.bind(this);
      this._unsubscribe = null;
    }

    connectedCallback() {
      if (window.electronAPI && typeof window.electronAPI.onRemoteHostKeyPrompt === 'function') {
        // preload returns an unsubscribe fn per Req 10.4.
        this._unsubscribe = window.electronAPI.onRemoteHostKeyPrompt((p) => this.show(p));
      }
    }

    disconnectedCallback() {
      document.removeEventListener('keydown', this._onKeyDown, true);
      if (typeof this._unsubscribe === 'function') {
        try { this._unsubscribe(); } catch (_e) { /* ignore */ }
        this._unsubscribe = null;
      }
    }

    /**
     * Display the dialog for a host-key prompt.
     * @param {Object} prompt
     */
    show(prompt) {
      if (!prompt || typeof prompt !== 'object') return;
      this._prompt = prompt;
      this._render();
      document.addEventListener('keydown', this._onKeyDown, true);
    }

    _hide() {
      this._prompt = null;
      this.innerHTML = '';
      document.removeEventListener('keydown', this._onKeyDown, true);
    }

    _onKeyDown(ev) {
      if (!this._prompt) return;
      if (ev.key === 'Escape') {
        ev.preventDefault();
        ev.stopPropagation();
        this._respond(false);
      } else if (ev.key === 'Enter') {
        ev.preventDefault();
        ev.stopPropagation();
        this._respond(true);
      }
    }

    _render() {
      if (!this._prompt) { this.innerHTML = ''; return; }
      const p = this._prompt;
      const status = p.status === 'mismatch' ? 'mismatch' : 'unknown';
      const isMismatch = status === 'mismatch';
      const title = isMismatch ? '⚠ Host key changed!' : 'Verify host key';
      const titleColor = isMismatch ? 'var(--color-error)' : 'var(--color-text-primary)';
      const host = esc(p.host || p.alias || '');
      const port = Number(p.port) || 22;
      const alias = esc(p.alias || '');
      const keyType = esc(p.keyType || '');
      const fpFormatted = formatFingerprint(p.fingerprint);

      const warningBlock = isMismatch ? `
        <div style="background:rgba(244,71,71,0.08);border:1px solid var(--color-error);border-radius:var(--border-radius,4px);padding:10px 12px;margin-bottom:14px;font-size:12px;color:var(--color-error);line-height:1.5;">
          <strong>The host key for this server has changed since you last connected.</strong><br>
          This could indicate a man-in-the-middle attack, or the server was legitimately reinstalled.
          Only accept if you have verified the new key with the server administrator.
          <a href="https://docs.openssh.com/ssh-keygen-R" target="_blank" rel="noopener"
             style="color:var(--color-error);text-decoration:underline;">Learn more</a>.
        </div>` : '';

      const subtitle = isMismatch
        ? `Existing fingerprint for <strong>${host}:${port}</strong> no longer matches.`
        : `This is your first time connecting to <strong>${host}:${port}</strong>. Verify the fingerprint with the server administrator before accepting.`;

      const acceptBg = isMismatch ? 'var(--color-error)' : 'var(--color-accent)';
      const acceptHover = isMismatch ? 'var(--color-error)' : 'var(--color-accent-hover)';

      this.innerHTML = `
        <div class="rhk-overlay" style="position:fixed;inset:0;z-index:10002;background:rgba(0,0,0,0.6);display:flex;align-items:center;justify-content:center;font-family:var(--font-ui,sans-serif);">
          <div role="dialog" aria-modal="true" aria-labelledby="rhk-title"
               style="width:520px;max-width:calc(100vw - 48px);background:var(--color-bg-secondary);color:var(--color-text-primary);border:1px solid var(--color-border);border-radius:var(--border-radius,4px);padding:22px 22px 18px;box-shadow:0 12px 40px rgba(0,0,0,0.5);">
            <h3 id="rhk-title" style="margin:0 0 6px;font-size:15px;font-weight:600;color:${titleColor};">${esc(title)}</h3>
            <p style="margin:0 0 14px;font-size:12px;color:var(--color-text-secondary);line-height:1.5;">
              ${subtitle}${alias ? ` <span style="color:var(--color-text-muted);">(${alias})</span>` : ''}
            </p>
            ${warningBlock}
            <div style="margin-bottom:6px;font-size:11px;color:var(--color-text-muted);text-transform:uppercase;letter-spacing:0.5px;">
              Fingerprint${keyType ? ` · ${keyType}` : ''}
            </div>
            <div class="rhk-fp" style="background:var(--color-bg-tertiary);border:1px solid var(--color-border);border-radius:var(--border-radius,4px);padding:12px 14px;font-family:var(--font-mono);font-size:12px;color:var(--color-text-primary);word-break:break-all;user-select:text;margin-bottom:18px;letter-spacing:0.5px;">
              ${esc(fpFormatted)}
            </div>
            <div style="display:flex;gap:8px;justify-content:flex-end;">
              <button class="rhk-reject" type="button"
                      style="padding:8px 16px;background:transparent;border:1px solid var(--color-error);border-radius:var(--border-radius,4px);color:var(--color-error);font-size:12px;font-weight:500;cursor:pointer;transition:background var(--transition,150ms ease);"
                      onmouseenter="this.style.background='rgba(244,71,71,0.1)'" onmouseleave="this.style.background='transparent'">Reject</button>
              <button class="rhk-accept" type="button"
                      style="padding:8px 16px;background:${acceptBg};border:none;border-radius:var(--border-radius,4px);color:#ffffff;font-size:12px;font-weight:600;cursor:pointer;transition:background var(--transition,150ms ease);"
                      onmouseenter="this.style.background='${acceptHover}'" onmouseleave="this.style.background='${acceptBg}'">Accept</button>
            </div>
          </div>
        </div>
      `;

      const accept = this.querySelector('.rhk-accept');
      const reject = this.querySelector('.rhk-reject');
      if (accept) accept.addEventListener('click', () => this._respond(true));
      if (reject) reject.addEventListener('click', () => this._respond(false));
      // Focus the primary action so Enter / Space activate it predictably.
      setTimeout(() => { if (accept) accept.focus(); }, 0);
    }

    _respond(accept) {
      if (!this._prompt) return;
      const p = this._prompt;
      const payload = { accept: Boolean(accept) };
      const msg = { alias: p.alias, kind: 'host-key', payload };
      if (p.requestId) msg.requestId = p.requestId;
      try {
        if (window.electronAPI && typeof window.electronAPI.remoteRespondAuth === 'function') {
          window.electronAPI.remoteRespondAuth(msg);
        }
      } catch (_e) { /* swallow — dialog must still dismiss */ }
      this._hide();
    }
  }

  if (!customElements.get('remote-host-key-dialog')) {
    customElements.define('remote-host-key-dialog', RemoteHostKeyDialog);
  }
})();
