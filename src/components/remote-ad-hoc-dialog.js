'use strict';
/**
 * <remote-ad-hoc-dialog> — Add ad-hoc SSH host.
 * Feature: remote-ssh · Task 25 · Requirements 2.5
 */
(function () {
  class RemoteAdHocDialog extends HTMLElement {
    connectedCallback() { this._render(); }
    _close() { if (this.parentNode) this.parentNode.removeChild(this); }
    async _submit() {
      const alias = this.querySelector('#rah-alias')?.value?.trim();
      const hostName = this.querySelector('#rah-host')?.value?.trim();
      const user = this.querySelector('#rah-user')?.value?.trim() || '';
      const port = parseInt(this.querySelector('#rah-port')?.value, 10) || 22;
      const identityFile = this.querySelector('#rah-key')?.value?.trim() || '';
      if (!alias || !hostName) { alert('Alias와 HostName은 필수입니다.'); return; }
      if (window.electronAPI?.remoteAddAdHocHost) {
        const res = await window.electronAPI.remoteAddAdHocHost({ alias, hostName, user, port, identityFile });
        if (res && res.ok) { this._close(); if (window.RemoteHostPicker) window.RemoteHostPicker.show(); }
        else alert(`추가 실패: ${res && res.error}`);
      }
    }
    _render() {
      this.innerHTML = `<div class="rah-overlay" style="position:fixed;inset:0;z-index:10000;background:rgba(0,0,0,0.5);display:flex;align-items:center;justify-content:center;font-family:var(--font-ui,sans-serif);"><div style="width:420px;background:var(--color-bg-primary);color:var(--color-text-primary);border:1px solid var(--color-border);border-radius:var(--border-radius);padding:20px;"><h3 style="margin:0 0 16px;font-size:14px;">Ad-hoc 호스트 추가</h3><label style="font-size:11px;color:var(--color-text-secondary);display:block;margin-bottom:4px;">Alias *</label><input id="rah-alias" style="width:100%;padding:8px;background:var(--color-bg-secondary);border:1px solid var(--color-border);border-radius:var(--border-radius);color:var(--color-text-primary);font-size:13px;margin-bottom:10px;outline:none;" placeholder="my-server"><label style="font-size:11px;color:var(--color-text-secondary);display:block;margin-bottom:4px;">HostName *</label><input id="rah-host" style="width:100%;padding:8px;background:var(--color-bg-secondary);border:1px solid var(--color-border);border-radius:var(--border-radius);color:var(--color-text-primary);font-size:13px;margin-bottom:10px;outline:none;" placeholder="10.0.0.5 또는 hostname.example.com"><div style="display:flex;gap:8px;margin-bottom:10px;"><div style="flex:1"><label style="font-size:11px;color:var(--color-text-secondary);display:block;margin-bottom:4px;">User</label><input id="rah-user" style="width:100%;padding:8px;background:var(--color-bg-secondary);border:1px solid var(--color-border);border-radius:var(--border-radius);color:var(--color-text-primary);font-size:13px;outline:none;" placeholder="ubuntu"></div><div style="width:80px"><label style="font-size:11px;color:var(--color-text-secondary);display:block;margin-bottom:4px;">Port</label><input id="rah-port" type="number" value="22" style="width:100%;padding:8px;background:var(--color-bg-secondary);border:1px solid var(--color-border);border-radius:var(--border-radius);color:var(--color-text-primary);font-size:13px;outline:none;"></div></div><label style="font-size:11px;color:var(--color-text-secondary);display:block;margin-bottom:4px;">IdentityFile (선택)</label><input id="rah-key" style="width:100%;padding:8px;background:var(--color-bg-secondary);border:1px solid var(--color-border);border-radius:var(--border-radius);color:var(--color-text-primary);font-size:13px;margin-bottom:16px;outline:none;" placeholder="~/.ssh/id_ed25519"><div style="display:flex;gap:8px;justify-content:flex-end;"><button class="rah-cancel" style="padding:8px 16px;background:transparent;border:1px solid var(--color-border);border-radius:var(--border-radius);color:var(--color-text-primary);font-size:12px;cursor:pointer;">취소</button><button class="rah-submit" style="padding:8px 16px;background:var(--color-accent);border:none;border-radius:var(--border-radius);color:#fff;font-size:12px;font-weight:600;cursor:pointer;">추가</button></div></div></div>`;
      this.querySelector('.rah-overlay')?.addEventListener('click', e => { if (e.target.classList.contains('rah-overlay')) this._close(); });
      this.querySelector('.rah-cancel')?.addEventListener('click', () => this._close());
      this.querySelector('.rah-submit')?.addEventListener('click', () => this._submit());
      setTimeout(() => this.querySelector('#rah-alias')?.focus(), 0);
    }
    static show() { document.querySelectorAll('remote-ad-hoc-dialog').forEach(el => el.remove()); const el = document.createElement('remote-ad-hoc-dialog'); document.body.appendChild(el); return el; }
  }
  if (!customElements.get('remote-ad-hoc-dialog')) customElements.define('remote-ad-hoc-dialog', RemoteAdHocDialog);
  window.RemoteAdHocDialog = RemoteAdHocDialog;
})();
