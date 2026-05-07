const { app } = require('electron');
const path = require('path');
const fs = require('fs');

class DataStore {
  constructor() {
    this.basePath = app ? app.getPath('userData') : path.join(require('os').homedir(), '.ai-editor');
    this._ensureDirs();
  }

  _ensureDirs() {
    const dirs = ['settings', 'history', 'checkpoints', 'usage', 'skills', 'skills/github'];
    for (const dir of dirs) {
      const full = path.join(this.basePath, dir);
      if (!fs.existsSync(full)) {
        fs.mkdirSync(full, { recursive: true });
      }
    }
  }

  // Settings
  loadSettings() {
    const p = path.join(this.basePath, 'settings', 'settings.json');
    if (!fs.existsSync(p)) return null;
    try {
      return JSON.parse(fs.readFileSync(p, 'utf-8'));
    } catch { return null; }
  }

  saveSettings(settings) {
    const p = path.join(this.basePath, 'settings', 'settings.json');
    fs.writeFileSync(p, JSON.stringify(settings, null, 2), 'utf-8');
    return true;
  }

  // History
  saveHistory(date, messages) {
    const p = path.join(this.basePath, 'history', `${date}.json`);
    let existing = [];
    if (fs.existsSync(p)) {
      try { existing = JSON.parse(fs.readFileSync(p, 'utf-8')); } catch {}
    }
    existing.push(...messages);
    fs.writeFileSync(p, JSON.stringify(existing, null, 2), 'utf-8');
  }

  // Usage
  loadUsage() {
    const p = path.join(this.basePath, 'usage', 'usage.json');
    if (!fs.existsSync(p)) return { used: 0, limit: 100000, cost: 0 };
    try { return JSON.parse(fs.readFileSync(p, 'utf-8')); } catch { return { used: 0, limit: 100000, cost: 0 }; }
  }

  updateUsage(tokens) {
    const usage = this.loadUsage();
    usage.used += tokens;
    const p = path.join(this.basePath, 'usage', 'usage.json');
    fs.writeFileSync(p, JSON.stringify(usage, null, 2), 'utf-8');
    return usage;
  }

  // Skills
  loadSkills() {
    const skillsDir = path.join(this.basePath, 'skills');
    const builtinSkills = [
      { id: 'default', name: 'General Assistant', role: 'You are a helpful coding assistant.', builtin: true },
      { id: 'code-review', name: 'Code Reviewer', role: 'You are a senior code reviewer.', builtin: true },
      { id: 'architect', name: 'Architect', role: 'You are a software architect.', builtin: true },
    ];

    // Load GitHub-imported skills
    const ghDir = path.join(skillsDir, 'github');
    if (fs.existsSync(ghDir)) {
      for (const f of fs.readdirSync(ghDir)) {
        if (f.endsWith('.json')) {
          try {
            const skill = JSON.parse(fs.readFileSync(path.join(ghDir, f), 'utf-8'));
            skill.builtin = false;
            builtinSkills.push(skill);
          } catch {}
        }
      }
    }
    return builtinSkills;
  }

  saveSkill(skill) {
    const p = path.join(this.basePath, 'skills', 'github', `${skill.id}.json`);
    fs.writeFileSync(p, JSON.stringify(skill, null, 2), 'utf-8');
  }

  deleteSkill(id) {
    const p = path.join(this.basePath, 'skills', 'github', `${id}.json`);
    if (fs.existsSync(p)) fs.unlinkSync(p);
  }

  // Denied Models — 호출 시 ValidationException/model_denied 발생한 모델 영속 저장
  // 다음 기동 시 자동으로 모델 목록에서 제외 (앱 재시작 후에도 학습 결과 유지)
  loadDeniedModels() {
    const p = path.join(this.basePath, 'settings', 'denied-models.json');
    if (!fs.existsSync(p)) return [];
    try {
      const data = JSON.parse(fs.readFileSync(p, 'utf-8'));
      return Array.isArray(data) ? data : (data.models || []);
    } catch { return []; }
  }

  addDeniedModel(modelId) {
    if (!modelId) return;
    const p = path.join(this.basePath, 'settings', 'denied-models.json');
    const list = this.loadDeniedModels();
    const clean = String(modelId).replace(/^us\.|^eu\.|^global\./, '');
    if (list.includes(clean)) return;
    list.push(clean);
    fs.writeFileSync(p, JSON.stringify(list, null, 2), 'utf-8');
  }

  clearDeniedModels() {
    const p = path.join(this.basePath, 'settings', 'denied-models.json');
    if (fs.existsSync(p)) fs.unlinkSync(p);
  }
}

module.exports = { DataStore };
