class QuotaDisplay extends HTMLElement {
  constructor() {
    super();
    this._usage = { used: 0, limit: 100000, cost: 0 };
  }

  connectedCallback() {
    this.render();
    this._loadUsage();
  }

  async _loadUsage() {
    try {
      if (window.electronAPI?.readFile) {
        // Load from userData/usage/usage.json
        // Simplified for now
      }
    } catch { /* ignore */ }
  }

  get percentage() {
    return this._usage.limit > 0 ? (this._usage.used / this._usage.limit) * 100 : 0;
  }

  render() {
    const pct = this.percentage;
    const cls = pct > 80 ? 'danger' : pct > 60 ? 'warning' : '';

    this.innerHTML = `
      <div class="quota-widget" style="min-width:120px;">
        <div style="display:flex;justify-content:space-between;font-size:10px;color:var(--color-text-muted,#6a6a6a);">
          <span>Tokens</span>
          <span>${this._formatNumber(this._usage.used)} / ${this._formatNumber(this._usage.limit)}</span>
        </div>
        <div class="quota-bar">
          <div class="fill ${cls}" style="width:${Math.min(pct, 100)}%"></div>
        </div>
      </div>
    `;
  }

  updateUsage(tokens) {
    this._usage.used += tokens;
    this.render();
    if (this.percentage > 80) {
      this.dispatchEvent(new CustomEvent('quota-warning', {
        detail: { percentage: this.percentage },
        bubbles: true,
      }));
    }
  }

  _formatNumber(n) {
    if (n >= 1000000) return (n / 1000000).toFixed(1) + 'M';
    if (n >= 1000) return (n / 1000).toFixed(1) + 'K';
    return n.toString();
  }
}

customElements.define('quota-display', QuotaDisplay);
