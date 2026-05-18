'use strict';
/**
 * <docx-viewer> — /api/media/docx-render 응답 HTML 렌더링.
 * Requirements: 5.1, 5.3, 5.4
 */
(function () {
  class DocxViewer extends HTMLElement {
    async connectedCallback() {
      const path = this.getAttribute('path') || '';
      this.style.cssText = 'display:block;width:100%;height:100%;background:var(--color-bg-secondary);overflow:auto;padding:20px;color:var(--color-text-primary);';
      try {
        const base = (window.apiBase ? window.apiBase() : 'http://localhost:8765');
        const r = await fetch(`${base}/api/media/docx-render`, {
          method: 'POST', headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ path }),
        });
        const data = await r.json();
        if (data.error) { this.innerHTML = `<div style="color:var(--color-error)">${data.error}</div>`; return; }
        this.innerHTML = data.html || '<div style="color:var(--color-text-muted)">(빈 문서)</div>';
      } catch (e) { this.innerHTML = `<div style="color:var(--color-error)">${e.message}</div>`; }
    }
  }
  if (!customElements.get('docx-viewer')) customElements.define('docx-viewer', DocxViewer);
})();
