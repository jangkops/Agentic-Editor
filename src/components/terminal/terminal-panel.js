/**
 * Terminal Panel — xterm.js 기반 실제 터미널 에뮬레이터
 * ANSI 색상, 커서 이동, 프롬프트 표시 지원
 */
class TerminalPanel extends HTMLElement {
  constructor() {
    super();
    this._tabs = [];
    this._activeTab = null;
    this._maxTabs = 5;
    this._xtermLoaded = false;
    this._terminals = new Map(); // id → { term, fitAddon }
  }

  connectedCallback() {
    this.render();
    this._loadXterm().then(() => this._addTab());
    this._setupIPC();
  }

  async _loadXterm() {
    if (this._xtermLoaded) return;
    // xterm.js CSS
    const link = document.createElement('link');
    link.rel = 'stylesheet';
    link.href = 'https://cdn.jsdelivr.net/npm/xterm@5.3.0/css/xterm.min.css';
    document.head.appendChild(link);
    // xterm.js + fit addon
    await this._loadScript('https://cdn.jsdelivr.net/npm/xterm@5.3.0/lib/xterm.min.js');
    await this._loadScript('https://cdn.jsdelivr.net/npm/xterm-addon-fit@0.8.0/lib/xterm-addon-fit.min.js');
    this._xtermLoaded = true;
  }

  _loadScript(src) {
    return new Promise((resolve, reject) => {
      const s = document.createElement('script');
      s.src = src;
      s.onload = resolve;
      s.onerror = reject;
      document.head.appendChild(s);
    });
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
    this.style.cssText = 'display:flex;flex-direction:column;height:100%;';

    const style = document.createElement('style');
    style.textContent = `
      .terminal-container { display:flex;flex-direction:column;height:100%;background:var(--color-bg-primary,#1e1e1e); }
      .terminal-header { display:flex;align-items:center;background:var(--color-bg-tertiary,#2d2d30);border-bottom:1px solid var(--color-border,#3c3c3c);height:32px;padding:0 8px; }
      .terminal-tabs { display:flex;flex:1;overflow-x:auto;gap:2px; }
      .terminal-tab { display:flex;align-items:center;gap:4px;padding:4px 10px;font-size:11px;cursor:pointer;color:var(--color-text-secondary,#9d9d9d);background:transparent;border:none;border-radius:3px 3px 0 0;transition:background 150ms ease; }
      .terminal-tab:hover { background:var(--color-bg-hover,#2a2d2e); }
      .terminal-tab.active { background:var(--color-bg-primary,#1e1e1e);color:var(--color-text-primary,#ccc); }
      .terminal-tab .close { opacity:0;margin-left:4px;font-size:12px; }
      .terminal-tab:hover .close { opacity:0.7; }
      .terminal-add-tab { background:none;border:none;color:var(--color-text-muted,#6a6a6a);font-size:16px;cursor:pointer;padding:0 6px; }
      .terminal-add-tab:hover { color:var(--color-text-primary,#ccc); }
      .terminal-content { flex:1;overflow:hidden;position:relative; }
      .terminal-instance { position:absolute;inset:0;display:none;padding:4px; }
      .terminal-instance.active { display:block; }
    `;
    this.prepend(style);
  }

  _addTab() {
    if (this._tabs.length >= this._maxTabs) return;
    const id = `term-${Date.now()}`;
    const tab = { id, title: `Terminal ${this._tabs.length + 1}` };
    this._tabs.push(tab);
    this._renderTabs();
    this._switchTab(id);
    if (window.electronAPI?.terminalCreate) {
      window.electronAPI.terminalCreate(id);
    }
  }

  _removeTab(id) {
    const entry = this._terminals.get(id);
    if (entry) { entry.term.dispose(); this._terminals.delete(id); }
    this._tabs = this._tabs.filter(t => t.id !== id);
    const instance = this.querySelector(`.terminal-instance[data-id="${id}"]`);
    if (instance) instance.remove();
    if (this._activeTab === id && this._tabs.length > 0) {
      this._switchTab(this._tabs[this._tabs.length - 1].id);
    }
    this._renderTabs();
    if (window.electronAPI?.terminalKill) window.electronAPI.terminalKill(id);
  }

  _switchTab(id) {
    this._activeTab = id;
    this.querySelectorAll('.terminal-tab').forEach(el => el.classList.toggle('active', el.dataset.id === id));
    this.querySelectorAll('.terminal-instance').forEach(el => el.classList.toggle('active', el.dataset.id === id));

    if (!this.querySelector(`.terminal-instance[data-id="${id}"]`)) {
      const div = document.createElement('div');
      div.className = 'terminal-instance active';
      div.dataset.id = id;
      this.querySelector('.terminal-content')?.appendChild(div);
      this._initXterm(id, div);
    }

    // fit on switch
    const entry = this._terminals.get(id);
    if (entry) setTimeout(() => { try { entry.fitAddon.fit(); } catch {} }, 50);
  }

  _initXterm(id, container) {
    if (!window.Terminal) return;
    const term = new window.Terminal({
      cursorBlink: true,
      cursorStyle: 'block',
      fontSize: 13,
      fontFamily: "'Cascadia Code', 'Fira Code', 'JetBrains Mono', monospace",
      theme: {
        background: '#1e1e1e',
        foreground: '#cccccc',
        cursor: '#ffffff',
        cursorAccent: '#1e1e1e',
        selectionBackground: 'rgba(255,255,255,0.2)',
        black: '#1e1e1e', red: '#f44747', green: '#4ec9b0', yellow: '#ce9178',
        blue: '#007acc', magenta: '#c586c0', cyan: '#4fc1ff', white: '#cccccc',
        brightBlack: '#6a6a6a', brightRed: '#f44747', brightGreen: '#4ec9b0',
        brightYellow: '#ce9178', brightBlue: '#1a8ad4', brightMagenta: '#c586c0',
        brightCyan: '#4fc1ff', brightWhite: '#ffffff',
      },
      scrollback: 5000,
      allowTransparency: true,
    });

    let fitAddon = null;
    if (window.FitAddon) {
      fitAddon = new window.FitAddon.FitAddon();
      term.loadAddon(fitAddon);
    }

    term.open(container);
    if (fitAddon) setTimeout(() => { try { fitAddon.fit(); } catch {} }, 100);

    // 입력을 Electron PTY로 전달
    term.onData((data) => {
      if (window.electronAPI?.terminalWrite) {
        window.electronAPI.terminalWrite(id, data);
      }
    });

    this._terminals.set(id, { term, fitAddon });

    // 리사이즈 감지
    const ro = new ResizeObserver(() => {
      if (fitAddon && this._activeTab === id) {
        try { fitAddon.fit(); } catch {}
      }
    });
    ro.observe(container);
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
        if (e.target.classList.contains('close')) this._removeTab(e.target.dataset.close);
        else this._switchTab(el.dataset.id);
      });
    });
  }

  _setupIPC() {
    if (window.electronAPI?.onTerminalData) {
      window.electronAPI.onTerminalData((data) => {
        if (data.id && data.data) {
          const entry = this._terminals.get(data.id);
          if (entry) entry.term.write(data.data);
        }
      });
    }
  }
}

customElements.define('terminal-panel', TerminalPanel);
