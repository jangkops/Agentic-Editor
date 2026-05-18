'use strict';
/**
 * <pptx-viewer> — /api/media/pptx-render 응답 렌더링.
 * Requirements: 4.1-4.5
 */
(function () {
  class PptxViewer extends HTMLElement {
    async connectedCallback() {
      const path = this.getAttribute('path') || '';
      this.style.cssText = 'display:block;width:100%;height:100%;background:var(--color-bg-secondary);overflow:auto;padding:20px;';
      this.innerHTML = '<div style="color:var(--color-text-muted)">불러오는 중...</div>';
      try {
        const base = (window.apiBase ? window.apiBase() : 'http://localhost:8765');
        const r = await fetch(`${base}/api/media/pptx-render`, {
          method: 'POST', headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ path }),
        });
        const data = await r.json();
        if (data.error) { this.innerHTML = `<div style="color:var(--color-error)">${data.error}</div>`; return; }
        const slides = data.slides || [];
        this.innerHTML = slides.map((s, i) => `
          <div style="background:var(--color-bg-tertiary);border:1px solid var(--color-border);border-radius:var(--border-radius);padding:16px;margin-bottom:12px;">
            <div style="font-size:10px;color:var(--color-text-muted);margin-bottom:8px;">Slide ${i + 1}</div>
            <h3 style="margin:0 0 8px;color:var(--color-text-primary);">${s.title || ''}</h3>
            ${(s.bullets || []).map(b => `<div style="color:var(--color-text-secondary);margin-bottom:4px;">• ${b}</div>`).join('')}
          </div>`).join('');
      } catch (e) {
        this.innerHTML = `<div style="color:var(--color-error)">${e.message}</div>`;
      }
    }
  }
  if (!customElements.get('pptx-viewer')) customElements.define('pptx-viewer', PptxViewer);
})();
