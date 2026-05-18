'use strict';
/**
 * <image-viewer> — PNG/JPG/WEBP/SVG 렌더링.
 * Requirements: 2.1-2.5
 */
(function () {
  class ImageViewer extends HTMLElement {
    connectedCallback() {
      const path = this.getAttribute('path') || '';
      this.style.cssText = 'display:block;width:100%;height:100%;background:var(--color-bg-secondary);overflow:auto;';
      if (!path) { this.innerHTML = '<div style="padding:20px;color:var(--color-text-muted)">경로가 지정되지 않았습니다.</div>'; return; }
      const lower = path.toLowerCase();
      const isSvg = lower.endsWith('.svg');
      if (isSvg) {
        this.innerHTML = `<iframe sandbox="" style="border:none;width:100%;height:100%;" src="file://${path}"></iframe>`;
      } else {
        this.innerHTML = `<div style="display:flex;align-items:center;justify-content:center;height:100%;padding:20px;"><img src="file://${path}" style="max-width:100%;max-height:100%;" onerror="this.outerHTML='<div style=color:var(--color-error)>이미지 로드 실패: ${path}</div>'"></div>`;
      }
    }
  }
  if (!customElements.get('image-viewer')) customElements.define('image-viewer', ImageViewer);
})();
