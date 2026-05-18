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

  // ──────────────────────────────────────────────────────────────────────────
  // Remote SSH hosts (remote-ssh feature)
  // ──────────────────────────────────────────────────────────────────────────
  // Raw file I/O for `userData/settings/remote-hosts.json`. Higher-level
  // CRUD (setFavorite, addAdHoc, setProvisioningMode, …) lives in
  // `electron/src/remote/remote-hosts-store.js` (RemoteHostsStore) which
  // delegates to these two methods for persistence.
  //
  // Schema (matches design.md §Data Model):
  //   { schemaVersion: number, hosts: { [alias]: {
  //       favorite?:           boolean,
  //       lastWorkspace?:      string,
  //       remotePortOverride?: number | null,
  //       provisioningMode?:   'auto' | 'manual',
  //       source?:             'ssh-config' | 'ad-hoc',
  //       adHoc?:              {hostName, user, port, identityFile}
  //     }}}
  //
  // NEVER store key material or passphrases here (Req 10.5).
  loadRemoteHosts() {
    const p = path.join(this.basePath, 'settings', 'remote-hosts.json');
    if (!fs.existsSync(p)) return { schemaVersion: 1, hosts: {} };
    let raw;
    try { raw = fs.readFileSync(p, 'utf-8'); }
    catch { return { schemaVersion: 1, hosts: {} }; }
    try {
      const data = JSON.parse(raw);
      return {
        schemaVersion: Number(data && data.schemaVersion) || 1,
        hosts: (data && typeof data.hosts === 'object' && data.hosts) || {},
      };
    } catch {
      // Malformed JSON — back it up and start fresh so the app keeps
      // working even if the preferences file was corrupted by a crash or
      // external edit (Req 13.3).
      try {
        const backup = `${p}.corrupt-${Date.now()}.bak`;
        fs.renameSync(p, backup);
      } catch { /* ignore — worst case we overwrite on next save */ }
      return { schemaVersion: 1, hosts: {} };
    }
  }

  saveRemoteHosts(data) {
    const dir = path.join(this.basePath, 'settings');
    if (!fs.existsSync(dir)) fs.mkdirSync(dir, { recursive: true });
    const p = path.join(dir, 'remote-hosts.json');
    const payload = {
      schemaVersion: (data && Number(data.schemaVersion)) || 1,
      hosts: (data && typeof data.hosts === 'object' && data.hosts) || {},
    };
    // Atomic write: write to tmp file, fsync, then rename over the target.
    // Guarantees the file is either the old version or the new version,
    // never a half-written truncation (Req 13.3).
    const tmp = `${p}.tmp-${process.pid}-${Date.now()}`;
    const fd = fs.openSync(tmp, 'w');
    try {
      fs.writeSync(fd, JSON.stringify(payload, null, 2), 0, 'utf-8');
      try { fs.fsyncSync(fd); } catch { /* best-effort — some FS reject fsync */ }
    } finally {
      try { fs.closeSync(fd); } catch { /* already closed */ }
    }
    fs.renameSync(tmp, p);
    return true;
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

  // Capability Denylist — (modelId, capability) pairs persisted
  _capabilityDenylistPath() {
    return path.join(this.basePath, 'settings', 'capability-denylist.json');
  }
  loadCapabilityDenylist() {
    const p = this._capabilityDenylistPath();
    if (!fs.existsSync(p)) return { version: 1, entries: [] };
    try {
      const data = JSON.parse(fs.readFileSync(p, 'utf-8'));
      if (!data || !Array.isArray(data.entries)) return { version: 1, entries: [] };
      return { version: data.version || 1, entries: data.entries };
    } catch { return { version: 1, entries: [] }; }
  }
  addCapabilityDenylistEntry(entry) {
    if (!entry || !entry.modelId || !entry.capability) return false;
    const clean = String(entry.modelId).replace(/^us\.|^eu\.|^global\./, '');
    const cap = String(entry.capability);
    const data = this.loadCapabilityDenylist();
    if (data.entries.some(e => e.modelId === clean && e.capability === cap)) return false;
    data.entries.push({ modelId: clean, capability: cap, reason: entry.reason || '', deniedAt: entry.deniedAt || new Date().toISOString() });
    const p = this._capabilityDenylistPath();
    fs.mkdirSync(path.dirname(p), { recursive: true });
    fs.writeFileSync(p, JSON.stringify(data, null, 2), 'utf-8');
    return true;
  }
  removeCapabilityDenylistEntry(modelId, capability) {
    if (!modelId || !capability) return false;
    const clean = String(modelId).replace(/^us\.|^eu\.|^global\./, '');
    const data = this.loadCapabilityDenylist();
    const before = data.entries.length;
    data.entries = data.entries.filter(e => !(e.modelId === clean && e.capability === capability));
    if (data.entries.length === before) return false;
    fs.writeFileSync(this._capabilityDenylistPath(), JSON.stringify(data, null, 2), 'utf-8');
    return true;
  }
  clearCapabilityDenylist() {
    const p = this._capabilityDenylistPath();
    if (fs.existsSync(p)) fs.unlinkSync(p);
    return true;
  }
}

module.exports = { DataStore };
