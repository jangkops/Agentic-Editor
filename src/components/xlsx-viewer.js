'use strict';
/**
 * <xlsx-viewer> — /api/media/xlsx-render 응답 시트별 테이블 렌더링.
 * Requirements: 5.2, 5.3, 5.4
 */
(function () {
  function esc(s) { return String(s == null ? '' : s).replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;'); }
  class XlsxViewer extends HTMLElement {
    async connectedCallback() {
      const path = this.getAttribute('path') || '';
      this.style.cssText = 'display:block;width:100%;height:100%;background:var(--color-bg-secondary);overflow:auto;padding:20px;';
      try {
        const base = (window.apiBase ? window.apiBase() : 'http://localhost:8765');
        const r = await fetch(`${base}/api/media/xlsx-render`, {
          method: 'POST', headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ path }),
        });
        const data = await r.json();
        if (data.error) { this.innerHTML = `<div style="color:var(--color-error)">${data.error}</div>`; return; }
        const sheets = data.sheets || [];
        this.innerHTML = sheets.map(s => `
          <div style="margin-bottom:20px;">
            <h4 style="color:var(--color-text-primary);margin:0 0 8px;">${esc(s.name)}</h4>
            <table style="width:100%;border-collapse:collapse;font-family:var(--font-mono);font-size:12px;">
              ${(s.rows || []).map((row, ri) => `<tr>${row.map(c => `<${ri === 0 ? 'th' : 'td'} style="border:1px solid var(--color-border);padding:4px 8px;color:var(--color-text-${ri === 0 ? 'primary' : 'secondary'});">${esc(c)}</${ri === 0 ? 'th' : 'td'}>`).join('')}</tr>`).join('')}
            </table>
          </div>`).join('');
      } catch (e) { this.innerHTML = `<div style="color:var(--color-error)">${e.message}</div>`; }
    }
  }
  if (!customElements.get('xlsx-viewer')) customElements.define('xlsx-viewer', XlsxViewer);
})();
