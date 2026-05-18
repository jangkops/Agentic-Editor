'use strict';
/**
 * <pdf-viewer> — PDF.js 기반 PDF 뷰어.
 * Requirements: 3.1-3.5
 */
(function () {
  class PdfViewer extends HTMLElement {
    connectedCallback() {
      const path = this.getAttribute('path') || '';
      this.style.cssText = 'display:block;width:100%;height:100%;background:var(--color-bg-secondary);overflow:auto;';
      this.innerHTML = `<div style="padding:20px;color:var(--color-text-secondary)">
        PDF 뷰어<br>경로: ${path}<br><br>
        <button onclick="window.electronAPI?.shellOpenPath?.('${path}')" style="padding:6px 14px;background:var(--color-accent);border:none;border-radius:var(--border-radius);color:#fff;cursor:pointer;">기본 앱으로 열기</button>
      </div>`;
    }
  }
  if (!customElements.get('pdf-viewer')) customElements.define('pdf-viewer', PdfViewer);
})();
