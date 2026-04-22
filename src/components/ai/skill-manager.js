class SkillManager extends HTMLElement {
  constructor() {
    super();
    this._skills = [];
    this._activeSkill = null;
  }

  connectedCallback() {
    this._loadSkills();
    this.render();
  }

  async _loadSkills() {
    try {
      if (window.electronAPI?.loadSkills) {
        this._skills = await window.electronAPI.loadSkills();
      } else {
        this._skills = [
          { id: 'default', name: 'General Assistant', role: 'You are a helpful coding assistant.', builtin: true },
          { id: 'code-review', name: 'Code Reviewer', role: 'You are a senior code reviewer. Focus on bugs, security, and performance.', builtin: true },
          { id: 'architect', name: 'Architect', role: 'You are a software architect. Focus on design patterns and scalability.', builtin: true },
        ];
      }
      this.render();
    } catch (e) {
      console.error('Failed to load skills:', e);
    }
  }

  get activeSkill() {
    return this._activeSkill || this._skills[0] || null;
  }

  render() {
    this.innerHTML = `
      <div class="skill-manager">
        <div class="skill-header">
          <span class="skill-label">Skill</span>
          <button class="skill-add-btn" title="Add Skill">+</button>
        </div>
        <div class="skill-list">
          ${this._skills.map(s => `
            <div class="skill-item ${this._activeSkill?.id === s.id ? 'active' : ''}" data-id="${s.id}">
              <span class="skill-name">${s.name}</span>
              ${!s.builtin ? '<span class="skill-delete" data-del="' + s.id + '">×</span>' : ''}
            </div>
          `).join('')}
        </div>
        <div class="skill-import-section" style="display:none;">
          <input class="skill-github-url input" placeholder="GitHub raw JSON URL..." />
          <button class="skill-import-btn btn btn-primary">Import</button>
        </div>
      </div>
    `;

    const style = document.createElement('style');
    style.textContent = `
      .skill-manager { padding: 8px; }
      .skill-header {
        display: flex; justify-content: space-between; align-items: center;
        margin-bottom: 8px; font-size: 11px; color: var(--color-text-muted, #6a6a6a);
        text-transform: uppercase; letter-spacing: 0.5px;
      }
      .skill-add-btn {
        background: none; border: none; color: var(--color-text-muted, #6a6a6a);
        font-size: 14px; cursor: pointer;
      }
      .skill-add-btn:hover { color: var(--color-text-primary, #ccc); }
      .skill-item {
        display: flex; justify-content: space-between; align-items: center;
        padding: 4px 8px; font-size: 12px; cursor: pointer;
        color: var(--color-text-secondary, #9d9d9d);
        border-radius: var(--border-radius, 4px);
        transition: background 150ms ease;
      }
      .skill-item:hover { background: var(--color-bg-hover, #2a2d2e); }
      .skill-item.active {
        background: var(--color-bg-tertiary, #2d2d30);
        color: var(--color-accent, #007acc);
      }
      .skill-delete { opacity: 0; font-size: 12px; }
      .skill-item:hover .skill-delete { opacity: 0.7; }
      .skill-import-section {
        margin-top: 8px; display: flex; flex-direction: column; gap: 4px;
      }
    `;
    this.prepend(style);

    // Event listeners
    this.querySelectorAll('.skill-item').forEach(el => {
      el.addEventListener('click', (e) => {
        if (e.target.classList.contains('skill-delete')) {
          this._deleteSkill(e.target.dataset.del);
          return;
        }
        const id = el.dataset.id;
        this._activeSkill = this._skills.find(s => s.id === id) || null;
        this.render();
        this.dispatchEvent(new CustomEvent('skill-change', { detail: { skill: this._activeSkill }, bubbles: true }));
      });
    });

    this.querySelector('.skill-add-btn')?.addEventListener('click', () => {
      const section = this.querySelector('.skill-import-section');
      if (section) section.style.display = section.style.display === 'none' ? 'flex' : 'none';
    });

    this.querySelector('.skill-import-btn')?.addEventListener('click', () => this._importFromGitHub());
  }

  async _importFromGitHub() {
    const urlInput = this.querySelector('.skill-github-url');
    const url = urlInput?.value?.trim();
    if (!url) return;

    try {
      let data;
      if (window.electronAPI?.fetchGitHubRaw) {
        data = await window.electronAPI.fetchGitHubRaw(url);
      } else {
        const resp = await fetch(url);
        data = await resp.json();
      }

      if (data && data.name && data.role) {
        data.id = `github-${Date.now()}`;
        data.builtin = false;
        this._skills.push(data);

        if (window.electronAPI?.saveSkill) {
          await window.electronAPI.saveSkill(data);
        }

        this.render();
      }
    } catch (e) {
      console.error('GitHub import failed:', e);
    }
  }

  async _deleteSkill(id) {
    this._skills = this._skills.filter(s => s.id !== id);
    if (this._activeSkill?.id === id) {
      this._activeSkill = this._skills[0] || null;
    }
    if (window.electronAPI?.deleteSkill) {
      await window.electronAPI.deleteSkill(id);
    }
    this.render();
  }
}

customElements.define('skill-manager', SkillManager);
