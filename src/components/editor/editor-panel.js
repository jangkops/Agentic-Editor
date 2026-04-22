class EditorPanel extends HTMLElement {
  constructor() {
    super();
    this._editor = null;
    this._currentFile = null;
    this._monacoReady = false;
  }

  connectedCallback() {
    this.innerHTML = '<div id="monaco-container" style="width:100%;height:100%;"></div>';
    this._initMonaco();
  }

  _initMonaco() {
    if (typeof require === 'undefined' || !window.require) {
      // Wait for Monaco CDN
      setTimeout(() => this._initMonaco(), 200);
      return;
    }

    require.config({
      paths: { vs: 'https://cdn.jsdelivr.net/npm/monaco-editor@0.50.0/min/vs' },
    });

    require(['vs/editor/editor.main'], (monaco) => {
      this._editor = monaco.editor.create(this.querySelector('#monaco-container'), {
        value: '// Welcome to AI Editor\n// Open a file or folder to get started.\n',
        language: 'javascript',
        theme: 'vs-dark',
        automaticLayout: true,
        fontSize: 13,
        fontFamily: "'Cascadia Code', 'Fira Code', 'JetBrains Mono', monospace",
        minimap: { enabled: true },
        scrollBeyondLastLine: false,
        renderWhitespace: 'selection',
        bracketPairColorization: { enabled: true },
        padding: { top: 8 },
      });

      this._editor.onDidChangeCursorPosition((e) => {
        this.dispatchEvent(new CustomEvent('cursor-change', {
          detail: { line: e.position.lineNumber, column: e.position.column },
          bubbles: true,
        }));
      });

      this._monacoReady = true;
    });
  }

  async openFile(filePath) {
    if (!this._editor) return;

    let content = '';
    if (window.electronAPI?.readFile) {
      content = await window.electronAPI.readFile(filePath);
    }
    if (content === null) content = '';

    this._currentFile = filePath;

    // Detect language from extension
    const ext = filePath.split('.').pop().toLowerCase();
    const langMap = {
      js: 'javascript', ts: 'typescript', py: 'python', json: 'json',
      html: 'html', css: 'css', md: 'markdown', yml: 'yaml', yaml: 'yaml',
      sh: 'shell', bash: 'shell', txt: 'plaintext',
    };
    const language = langMap[ext] || 'plaintext';

    const monaco = this._editor.getModel()?.getLanguageId ? window.monaco : null;
    if (monaco) {
      const model = monaco.editor.createModel(content, language);
      this._editor.setModel(model);
    } else {
      this._editor.setValue(content);
    }
  }

  getValue() {
    return this._editor?.getValue() || '';
  }

  get currentFile() {
    return this._currentFile;
  }
}

customElements.define('editor-panel', EditorPanel);
