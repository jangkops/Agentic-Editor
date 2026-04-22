class FileTree extends HTMLElement {
  constructor() {
    super();
    this._rootPath = '';
    this._tree = [];
    this._expanded = new Set();
  }

  connectedCallback() {
    this.innerHTML = '<div class="file-tree-root"></div>';
  }

  async loadFolder(folderPath) {
    this._rootPath = folderPath;
    const entries = await this._readDir(folderPath);
    this._tree = entries;
    this._render();
  }

  async _readDir(dirPath) {
    if (window.electronAPI?.readDir) {
      return window.electronAPI.readDir(dirPath);
    }
    return [];
  }

  _render() {
    const root = this.querySelector('.file-tree-root');
    if (!root) return;
    root.innerHTML = '';
    this._renderEntries(root, this._tree, 0);
  }

  _renderEntries(container, entries, depth) {
    const sorted = [...entries].sort((a, b) => {
      if (a.isDirectory && !b.isDirectory) return -1;
      if (!a.isDirectory && b.isDirectory) return 1;
      return a.name.localeCompare(b.name);
    });

    for (const entry of sorted) {
      // Skip hidden files
      if (entry.name.startsWith('.') && entry.name !== '.kiro') continue;
      if (entry.name === 'node_modules' || entry.name === '__pycache__' || entry.name === '.git') continue;

      const item = document.createElement('div');
      item.className = 'file-tree-item';
      item.style.paddingLeft = `${8 + depth * 16}px`;

      const icon = entry.isDirectory
        ? (this._expanded.has(entry.path) ? '▾' : '▸')
        : this._fileIcon(entry.name);

      item.innerHTML = `<span class="icon">${icon}</span><span class="name">${entry.name}</span>`;

      if (entry.isDirectory) {
        item.addEventListener('click', async () => {
          if (this._expanded.has(entry.path)) {
            this._expanded.delete(entry.path);
          } else {
            this._expanded.add(entry.path);
            const children = await this._readDir(entry.path);
            entry.children = children;
          }
          this._render();
        });
      } else {
        item.addEventListener('click', () => {
          // Remove active from all
          this.querySelectorAll('.file-tree-item.active').forEach(el => el.classList.remove('active'));
          item.classList.add('active');
          this.dispatchEvent(new CustomEvent('file-select', {
            detail: { path: entry.path, name: entry.name },
            bubbles: true,
          }));
        });
      }

      container.appendChild(item);

      // Render children if expanded
      if (entry.isDirectory && this._expanded.has(entry.path) && entry.children) {
        this._renderEntries(container, entry.children, depth + 1);
      }
    }
  }

  _fileIcon(name) {
    const ext = name.split('.').pop().toLowerCase();
    const icons = {
      js: 'js', py: 'py', html: 'h', css: 'c', json: '{}',
      md: 'md', yml: 'y', yaml: 'y', txt: 't',
    };
    return icons[ext] || '·';
  }
}

customElements.define('file-tree', FileTree);
