class TerminalPanel extends HTMLElement {
  constructor() {
    super();
    this._tabs = [];
    this._activeTab = null;
    this._maxTabs = 5;
  }

  connectedCallback() {
    this.render();
    this._addTab();
    this._setupIPC();
  }

  render() {
    this.innerHTML = `
      <div class="terminal-container">
        <div class="terminal-header">
          <div class="terminal-tabs"></div>
          <button class="terminal-add-tab" title="New Terminal">+</button>
        </div>
        <div class="terminal-content"></div>
      </div>
    `;

    this.querySelector('.terminal-add-tab')?.addEventListener('click', () => this._addTab());

    this.style.cssText = `
      display: flex; flex-direction: column; height: 100%;
    `;

    const style = document.createElement('style');
    style.textContent = `
      .terminal-container {
        display: flex; flex-direction: column; height: 100%;
        background: var(--color-bg-primary, #1e1e1e);
      }
      .terminal-header {
        display: flex; align-items: center;
        background: var(--color-bg-tertiary, #2d2d30);
        border-bottom: 1px solid var(--color-border, #3c3c3c);
        height: 32px; padding: 0 8px;
      }
      .terminal-tabs {
        display: flex; flex: 1; overflow-x: auto; gap: 2px;
      }
      .terminal-tab {
        display: flex; align-items: center; gap: 4px;
        padding: 4px 10px; font-size: 11px; cursor: pointer;
        color: var(--color-text-secondary, #9d9d9d);
        background: transparent; border: none; border-radius: 3px 3px 0 0;
        transition: background 150ms ease;
      }
      .terminal-tab:hover { background: var(--color-bg-hover, #2a2d2e); }
      .terminal-tab.active {
        background: var(--color-bg-primary, #1e1e1e);
        color: var(--color-text-primary, #ccc);
      }
      .terminal-tab .close { opacity: 0; margin-left: 4px; font-size: 12px; }
      .terminal-tab:hover .close { opacity: 0.7; }
      .terminal-add-tab {
        background: none; border: none; color: var(--color-text-muted, #6a6a6a);
        font-size: 16px; cursor: pointer; padding: 0 6px;
      }
      .terminal-add-tab:hover { color: var(--color-text-primary, #ccc); }
      .terminal-content {
        flex: 1; overflow: hidden; position: relative;
      }
      .terminal-instance {
        position: absolute; inset: 0; display: none;
        padding: 8px; font-family: var(--font-mono, monospace);
        font-size: 13px; color: var(--color-text-primary, #ccc);
        overflow-y: auto; white-space: pre-wrap; word-break: break-all;
      }
      .terminal-instance.active { display: block; }
      .terminal-input-line {
        display: flex; align-items: center;
      }
      .terminal-prompt {
        color: var(--color-success, #4ec9b0); margin-right: 8px;
      }
      .terminal-input {
        flex: 1; background: none; border: none; outline: none;
        color: var(--color-text-primary, #ccc);
        font-family: var(--font-mono, monospace); font-size: 13px;
      }
    `;
    this.prepend(style);
  }

  _addTab() {
    if (this._tabs.length >= this._maxTabs) return;

    const id = `term-${Date.now()}`;
    const tab = { id, title: `Terminal ${this._tabs.length + 1}`, output: '' };
    this._tabs.push(tab);

    this._renderTabs();
    this._switchTab(id);

    if (window.electronAPI?.terminalCreate) {
      window.electronAPI.terminalCreate(id);
    }
  }

  _removeTab(id) {
    this._tabs = this._tabs.filter(t => t.id !== id);
    const instance = this.querySelector(`.terminal-instance[data-id="${id}"]`);
    if (instance) instance.remove();

    if (this._activeTab === id && this._tabs.length > 0) {
      this._switchTab(this._tabs[this._tabs.length - 1].id);
    }
    this._renderTabs();

    if (window.electronAPI?.terminalKill) {
      window.electronAPI.terminalKill(id);
    }
  }

  _switchTab(id) {
    this._activeTab = id;
    this.querySelectorAll('.terminal-tab').forEach(el => el.classList.toggle('active', el.dataset.id === id));
    this.querySelectorAll('.terminal-instance').forEach(el => el.classList.toggle('active', el.dataset.id === id));

    if (!this.querySelector(`.terminal-instance[data-id="${id}"]`)) {
      const div = document.createElement('div');
      div.className = 'terminal-instance active';
      div.dataset.id = id;
      div.innerHTML = `
        <div class="terminal-output"></div>
        <div class="terminal-input-line">
          <span class="terminal-prompt">$</span>
          <input class="terminal-input" type="text" autofocus>
        </div>
      `;
      div.querySelector('.terminal-input')?.addEventListener('keydown', (e) => {
        if (e.key === 'Enter') {
          const input = e.target;
          const cmd = input.value.trim();
          if (!cmd) return;
          this._appendOutput(id, `$ ${cmd}\n`);
          input.value = '';
          if (window.electronAPI?.terminalWrite) {
            window.electronAPI.terminalWrite(id, cmd + '\n');
          }
        }
      });
      this.querySelector('.terminal-content')?.appendChild(div);
    }
  }

  _renderTabs() {
    const container = this.querySelector('.terminal-tabs');
    if (!container) return;
    container.innerHTML = this._tabs.map(t => `
      <button class="terminal-tab ${t.id === this._activeTab ? 'active' : ''}" data-id="${t.id}">
        ${t.title}
        <span class="close" data-close="${t.id}">×</span>
      </button>
    `).join('');

    container.querySelectorAll('.terminal-tab').forEach(el => {
      el.addEventListener('click', (e) => {
        if (e.target.classList.contains('close')) {
          this._removeTab(e.target.dataset.close);
        } else {
          this._switchTab(el.dataset.id);
        }
      });
    });
  }

  _appendOutput(id, text) {
    const instance = this.querySelector(`.terminal-instance[data-id="${id}"] .terminal-output`);
    if (instance) {
      instance.textContent += text;
      instance.scrollTop = instance.scrollHeight;
    }
  }

  _setupIPC() {
    if (window.electronAPI?.onTerminalData) {
      window.electronAPI.onTerminalData((data) => {
        if (data.id && data.data) {
          this._appendOutput(data.id, data.data);
        }
      });
    }
  }
}

customElements.define('terminal-panel', TerminalPanel);
