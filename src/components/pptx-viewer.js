'use strict';
/**
 * <pptx-viewer> — /api/media/pptx-render 응답 렌더링.
 * Requirements: 4.1-4.5
 */
(function () {
  class PptxViewer extends HTMLElement {
    async connectedCallback() {
      // main.js는 file-path/base64 속성으로 전달한다. 과거 path 속성도 폴백 지원.
      const path = this.getAttribute('file-path') || this.getAttribute('path') || '';
      const b64 = this.getAttribute('base64') || '';
      this.style.cssText = 'display:block;width:100%;height:100%;background:var(--color-bg-secondary);overflow:auto;padding:20px;';
      this.innerHTML = '<div style="color:var(--color-text-muted)">불러오는 중...</div>';
      if (!path && !b64) {
        this.innerHTML = '<div style="color:var(--color-error)">미리보기 경로가 없습니다.</div>';
        return;
      }
      try {
        const base = (window.apiBase ? window.apiBase() : 'http://localhost:8765');
        const r = await fetch(`${base}/api/media/pptx-render`, {
          method: 'POST', headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ path, base64: b64 }),
        });
        const data = await r.json();
        if (data.error) { this.innerHTML = `<div style="color:var(--color-error)">${data.error}</div>`; return; }
        const slides = data.slides || [];
        const esc = (t) => String(t == null ? '' : t).replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;');
        this.innerHTML = slides.map((s, i) => {
          const imgs = (s.images || []).map(src =>
            `<img src="${src}" alt="slide image" style="display:block;width:100%;height:auto;border-radius:4px;margin:8px 0;border:1px solid var(--color-border);" />`
          ).join('');
          const bullets = (s.bullets || []).map(b =>
            `<div style="color:var(--color-text-secondary);margin-bottom:4px;">• ${esc(b)}</div>`
          ).join('');
          return `
          <div style="background:var(--color-bg-tertiary);border:1px solid var(--color-border);border-radius:var(--border-radius);padding:16px;margin-bottom:12px;">
            <div style="font-size:10px;color:var(--color-text-muted);margin-bottom:8px;">Slide ${i + 1}</div>
            ${s.title ? `<h3 style="margin:0 0 8px;color:var(--color-text-primary);">${esc(s.title)}</h3>` : ''}
            ${imgs}
            ${bullets}
          </div>`;
        }).join('');
      } catch (e) {
        this.innerHTML = `<div style="color:var(--color-error)">${e.message}</div>`;
      }
    }
  }
  if (!customElements.get('pptx-viewer')) customElements.define('pptx-viewer', PptxViewer);
})();
