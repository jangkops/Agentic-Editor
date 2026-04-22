class AgentWorkflowPanel extends HTMLElement {
  constructor() {
    super();
    this._workflow = null;
    this._steps = [];
  }

  connectedCallback() {
    this.render();
  }

  set workflow(wf) {
    this._workflow = wf;
    this._steps = wf?.steps || [];
    this.render();
  }

  render() {
    const steps = this._steps;
    const statusIcon = (s) => {
      if (s === 'completed') return '✓';
      if (s === 'running') return '⟳';
      if (s === 'failed') return '✗';
      return '○';
    };
    const statusClass = (s) => {
      if (s === 'completed') return 'step-done';
      if (s === 'running') return 'step-running';
      if (s === 'failed') return 'step-failed';
      return 'step-pending';
    };

    this.innerHTML = `
      <div class="workflow-panel">
        <div class="workflow-header">
          <span class="workflow-title">Agent Workflow</span>
          ${this._workflow ? `
            <span class="workflow-status ${this._workflow.status || ''}">${this._workflow.status || 'idle'}</span>
          ` : ''}
        </div>
        <div class="workflow-steps">
          ${steps.length === 0 ? '<div class="workflow-empty">No active workflow</div>' : ''}
          ${steps.map((step, i) => `
            <div class="workflow-step ${statusClass(step.status)}">
              <div class="step-connector">${i < steps.length - 1 ? '│' : ''}</div>
              <div class="step-icon">${statusIcon(step.status)}</div>
              <div class="step-info">
                <div class="step-name">${step.name}</div>
                ${step.detail ? `<div class="step-detail">${step.detail}</div>` : ''}
              </div>
            </div>
          `).join('')}
        </div>
        ${this._workflow?.id ? `
          <div class="workflow-actions">
            <button class="btn workflow-cancel-btn">Cancel</button>
          </div>
        ` : ''}
      </div>
    `;

    const style = document.createElement('style');
    style.textContent = `
      .workflow-panel { padding: 12px; }
      .workflow-header {
        display: flex; justify-content: space-between; align-items: center;
        margin-bottom: 12px;
      }
      .workflow-title {
        font-size: 11px; text-transform: uppercase; letter-spacing: 0.5px;
        color: var(--color-text-muted, #6a6a6a);
      }
      .workflow-status {
        font-size: 10px; padding: 2px 6px; border-radius: 3px;
        background: var(--color-bg-tertiary, #2d2d30);
        color: var(--color-text-secondary, #9d9d9d);
      }
      .workflow-status.running { color: var(--color-accent, #007acc); }
      .workflow-status.completed { color: var(--color-success, #4ec9b0); }
      .workflow-status.failed { color: var(--color-error, #f44747); }
      .workflow-steps { display: flex; flex-direction: column; gap: 2px; }
      .workflow-step {
        display: flex; align-items: flex-start; gap: 8px;
        padding: 4px 0; font-size: 12px;
      }
      .step-icon {
        width: 18px; height: 18px; display: flex; align-items: center;
        justify-content: center; font-size: 11px; border-radius: 50%;
        border: 1px solid var(--color-border, #3c3c3c);
        flex-shrink: 0;
      }
      .step-done .step-icon { color: var(--color-success, #4ec9b0); border-color: var(--color-success); }
      .step-running .step-icon { color: var(--color-accent, #007acc); border-color: var(--color-accent); animation: spin 1s linear infinite; }
      .step-failed .step-icon { color: var(--color-error, #f44747); border-color: var(--color-error); }
      .step-name { color: var(--color-text-primary, #ccc); }
      .step-detail { color: var(--color-text-muted, #6a6a6a); font-size: 11px; margin-top: 2px; }
      .step-connector { width: 18px; text-align: center; color: var(--color-border, #3c3c3c); font-size: 10px; }
      .workflow-empty { color: var(--color-text-muted, #6a6a6a); font-size: 12px; font-style: italic; }
      .workflow-actions { margin-top: 12px; }
      @keyframes spin { to { transform: rotate(360deg); } }
    `;
    this.prepend(style);

    this.querySelector('.workflow-cancel-btn')?.addEventListener('click', () => {
      if (this._workflow?.id && window.electronAPI?.agentCancel) {
        window.electronAPI.agentCancel(this._workflow.id);
      }
      this.dispatchEvent(new CustomEvent('workflow-cancel', { detail: { id: this._workflow?.id }, bubbles: true }));
    });
  }
}

customElements.define('agent-workflow-panel', AgentWorkflowPanel);
