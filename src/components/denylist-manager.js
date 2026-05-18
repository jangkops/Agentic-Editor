'use strict';
/**
 * <denylist-manager> — Capability denylist 관리 모달.
 * Feature: multimedia-file-support · Task 9.3 · Requirements 12.5
 */
(function () {
  const CAPABILITY_LABELS = { chat:'Chat', agent:'Agent', image_gen:'Image 생성', vision_input:'Vision 입력', any:'Any' };
  function esc(s) { return String(s==null?'':s).replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;').replace(/"/g,'&quot;'); }
  function fmtDate(iso) { if (!iso) return '—'; try { const d = new Date(iso); if (isNaN(d.getTime())) return String(iso); const p=n=>String(n).padStart(2,'0'); return `${d.getFullYear()}-${p(d.getMonth()+1)}-${p(d.getDate())} ${p(d.getHours())}:${p(d.getMinutes())}`; } catch { return String(iso); } }

  class DenylistManager extends HTMLElement {
    constructor() { super(); this._entries = []; this._selected = new Set(); this._loading = true; }
    connectedCallback() { this._render(); this._load(); }

    async _load() {
      this._loading = true; this._render();
      try {
        const api = window.electronAPI;
        if (api && api.loadCapabilityDenylist) {
          const data = await api.loadCapabilityDenylist();
          this._entries = ((data && data.entries) || []).slice().sort((a,b) => (b.deniedAt||'').localeCompare(a.deniedAt||''));
        } else this._entries = [];
      } catch (e) { console.error('[denylist-manager]', e); this._entries = []; }
      this._selected.clear(); this._loading = false; this._render();
    }

    _close() { if (this.parentNode) this.parentNode.removeChild(this); }

    async _removeOne(modelId, capability) {
      try { await window.electronAPI?.removeCapabilityDenylistEntry?.(modelId, capability); } catch (e) { console.error(e); }
      await this._load();
    }

    async _resetAll() {
      if (!confirm(`전체 ${this._entries.length}개 항목을 초기화합니다. 계속할까요?`)) return;
      try { await window.electronAPI?.clearCapabilityDenylist?.(); } catch (e) { console.error(e); }
      await this._load();
    }

    async _resetSelected() {
      if (this._selected.size === 0) return;
      for (const k of this._selected) {
        const i = k.indexOf('::');
        try { await window.electronAPI?.removeCapabilityDenylistEntry?.(k.slice(0,i), k.slice(i+2)); } catch {}
      }
      await this._load();
    }

    _toggle(key, checked) {
      if (checked) this._selected.add(key); else this._selected.delete(key);
      const btn = this.querySelector('#dl-reset-selected-btn');
      if (btn) { btn.disabled = this._selected.size === 0; btn.textContent = this._selected.size > 0 ? `선택 초기화 (${this._selected.size})` : '선택 초기화'; }
    }

    _render() {
      const entries = this._entries; const empty = entries.length === 0;
      this.innerHTML = `
        <div class="dl-overlay" style="position:fixed;inset:0;z-index:9999;background:rgba(0,0,0,0.55);display:flex;align-items:center;justify-content:center;font-family:var(--font-ui,sans-serif);">
          <div style="width:680px;max-width:92vw;max-height:82vh;background:var(--color-bg-primary);color:var(--color-text-primary);border:1px solid var(--color-border);border-radius:var(--border-radius);display:flex;flex-direction:column;box-shadow:0 10px 40px rgba(0,0,0,0.5);">
            <div style="display:flex;align-items:center;justify-content:space-between;padding:14px 18px;border-bottom:1px solid var(--color-border);">
              <div>
                <div style="font-size:14px;font-weight:700;">Capability Denylist</div>
                <div style="font-size:11px;color:var(--color-text-muted);margin-top:2px;">런타임 학습으로 차단된 (모델 × 기능) 조합</div>
              </div>
              <button class="dl-close" style="background:transparent;border:none;color:var(--color-text-secondary);font-size:18px;cursor:pointer;">✕</button>
            </div>
            <div style="flex:1;overflow:auto;background:var(--color-bg-secondary);">
              ${this._loading ? '<div style="padding:40px;text-align:center;color:var(--color-text-muted);font-size:12px;">불러오는 중...</div>' :
                empty ? '<div style="padding:40px;text-align:center;color:var(--color-text-muted);font-size:12px;">차단된 항목이 없습니다.</div>' :
                this._renderTable(entries)}
            </div>
            <div style="display:flex;align-items:center;justify-content:space-between;padding:12px 18px;border-top:1px solid var(--color-border);gap:8px;">
              <div style="font-size:11px;color:var(--color-text-muted);">${empty ? '' : `총 ${entries.length}개`}</div>
              <div style="display:flex;gap:8px;">
                <button id="dl-reset-selected-btn" disabled style="padding:6px 14px;background:transparent;border:1px solid var(--color-border);border-radius:var(--border-radius);color:var(--color-text-secondary);font-size:12px;cursor:pointer;">선택 초기화</button>
                <button id="dl-reset-all-btn" ${empty?'disabled':''} style="padding:6px 14px;background:${empty?'transparent':'var(--color-warning)'};border:1px solid ${empty?'var(--color-border)':'var(--color-warning)'};border-radius:var(--border-radius);color:${empty?'var(--color-text-muted)':'#1e1e1e'};font-size:12px;font-weight:600;cursor:${empty?'not-allowed':'pointer'};">전체 초기화</button>
                <button id="dl-close-btn" style="padding:6px 14px;background:transparent;border:1px solid var(--color-border);border-radius:var(--border-radius);color:var(--color-text-primary);font-size:12px;cursor:pointer;">닫기</button>
              </div>
            </div>
          </div>
        </div>`;
      const overlay = this.querySelector('.dl-overlay');
      overlay?.addEventListener('click', e => { if (e.target === overlay) this._close(); });
      this.querySelector('.dl-close')?.addEventListener('click', () => this._close());
      this.querySelector('#dl-close-btn')?.addEventListener('click', () => this._close());
      this.querySelector('#dl-reset-all-btn')?.addEventListener('click', () => this._resetAll());
      this.querySelector('#dl-reset-selected-btn')?.addEventListener('click', () => this._resetSelected());
      this.querySelectorAll('.dl-row-remove').forEach(b => b.addEventListener('click', e => { e.stopPropagation(); this._removeOne(b.dataset.model, b.dataset.cap); }));
      this.querySelectorAll('.dl-row-check').forEach(c => c.addEventListener('change', () => this._toggle(c.dataset.key, c.checked)));
      if (!this._onKey) { this._onKey = e => { if (e.key === 'Escape') this._close(); }; document.addEventListener('keydown', this._onKey); }
    }

    _renderTable(entries) {
      const rows = entries.map(e => {
        const mid = esc(e.modelId||''); const cap = esc(e.capability||''); const key = `${e.modelId}::${e.capability}`;
        return `<tr style="border-bottom:1px solid var(--color-border);"><td style="padding:10px 12px;width:28px;"><input type="checkbox" class="dl-row-check" data-key="${esc(key)}"></td><td style="padding:10px 12px;"><div style="font-family:var(--font-mono);font-size:12px;word-break:break-all;">${mid}</div><div style="font-size:10px;color:var(--color-text-muted);margin-top:3px;">${esc(fmtDate(e.deniedAt))}</div></td><td style="padding:10px 12px;"><span style="display:inline-block;padding:2px 8px;background:var(--color-bg-tertiary);border:1px solid var(--color-border);border-radius:var(--border-radius);font-size:11px;">${esc(CAPABILITY_LABELS[e.capability]||e.capability)}</span></td><td style="padding:10px 12px;font-size:11px;color:var(--color-text-muted);">${esc((e.reason||'—').slice(0,100))}</td><td style="padding:10px 8px;"><button class="dl-row-remove" data-model="${mid}" data-cap="${cap}" style="padding:4px 10px;background:transparent;border:1px solid var(--color-border);border-radius:var(--border-radius);color:var(--color-error);font-size:11px;cursor:pointer;">제거</button></td></tr>`;
      }).join('');
      return `<table style="width:100%;border-collapse:collapse;"><thead><tr style="background:var(--color-bg-tertiary);"><th></th><th style="padding:8px 12px;text-align:left;font-size:10px;color:var(--color-text-muted);text-transform:uppercase;">모델 / 시각</th><th style="padding:8px 12px;text-align:left;font-size:10px;color:var(--color-text-muted);text-transform:uppercase;">기능</th><th style="padding:8px 12px;text-align:left;font-size:10px;color:var(--color-text-muted);text-transform:uppercase;">사유</th><th></th></tr></thead><tbody>${rows}</tbody></table>`;
    }

    disconnectedCallback() { if (this._onKey) { document.removeEventListener('keydown', this._onKey); this._onKey = null; } }
    static show() { document.querySelectorAll('denylist-manager').forEach(el => el.remove()); const el = document.createElement('denylist-manager'); document.body.appendChild(el); return el; }
  }
  if (!customElements.get('denylist-manager')) customElements.define('denylist-manager', DenylistManager);
  window.DenylistManager = DenylistManager;
})();
