class ModelSelector extends HTMLElement {
  constructor() {
    super();
    this._models = {
      'amazon-bedrock': [
        { id: 'anthropic.claude-3-opus-20240229-v1:0', name: 'Claude 3 Opus', role: 'Planner/Evaluator' },
        { id: 'anthropic.claude-3-5-sonnet-20241022-v2:0', name: 'Claude 3.5 Sonnet', role: 'Generator' },
        { id: 'anthropic.claude-3-haiku-20240307-v1:0', name: 'Claude 3 Haiku', role: 'Fast' },
      ],
    };
    this._selected = 'anthropic.claude-3-5-sonnet-20241022-v2:0';
    this._consensusEnabled = false;
  }

  connectedCallback() {
    this.render();
  }

  get selectedModel() {
    return this._selected;
  }

  get consensusEnabled() {
    return this._consensusEnabled;
  }

  render() {
    const models = this._models['amazon-bedrock'] || [];
    this.innerHTML = `
      <div class="model-selector-bar">
        <div class="model-select-group">
          <label>Model:</label>
          <select class="model-dropdown">
            ${models.map(m => `
              <option value="${m.id}" ${m.id === this._selected ? 'selected' : ''}>
                ${m.name} (${m.role})
              </option>
            `).join('')}
          </select>
        </div>
        <div class="consensus-toggle">
          <label>
            <input type="checkbox" class="consensus-cb" ${this._consensusEnabled ? 'checked' : ''}>
            Consensus
          </label>
        </div>
      </div>
    `;

    this.querySelector('.model-dropdown')?.addEventListener('change', (e) => {
      this._selected = e.target.value;
      this.dispatchEvent(new CustomEvent('model-change', { detail: { model: this._selected }, bubbles: true }));
    });

    this.querySelector('.consensus-cb')?.addEventListener('change', (e) => {
      this._consensusEnabled = e.target.checked;
      this.dispatchEvent(new CustomEvent('consensus-toggle', { detail: { enabled: this._consensusEnabled }, bubbles: true }));
    });
  }
}

customElements.define('model-selector', ModelSelector);
