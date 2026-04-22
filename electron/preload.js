const { contextBridge, ipcRenderer } = require('electron');

contextBridge.exposeInMainWorld('electronAPI', {
  // File system
  openFolder: () => ipcRenderer.invoke('openFolder'),
  openFile: () => ipcRenderer.invoke('fs:open-file'),
  readFile: (p) => ipcRenderer.invoke('fs:read-file', p),
  writeFile: (p, content) => ipcRenderer.invoke('fs:write-file', p, content),
  rename: (oldP, newP) => ipcRenderer.invoke('fs:rename', oldP, newP),
  mkdir: (p) => ipcRenderer.invoke('fs:mkdir', p),
  readDir: (p) => ipcRenderer.invoke('fs:list-files', p),
  getUserDataPath: () => ipcRenderer.invoke('fs:get-user-data-path'),

  // Settings
  loadSettings: () => ipcRenderer.invoke('store:load-settings'),
  saveSettings: (s) => ipcRenderer.invoke('store:save-settings', s),

  // Usage
  loadUsage: () => ipcRenderer.invoke('store:load-usage'),
  updateUsage: (tokens) => ipcRenderer.invoke('store:update-usage', tokens),

  // History
  saveHistory: (date, msgs) => ipcRenderer.invoke('store:save-history', date, msgs),

  // Checkpoints
  saveCheckpoint: (wfId, state) => ipcRenderer.invoke('store:save-checkpoint', wfId, state),
  loadCheckpoint: (wfId) => ipcRenderer.invoke('store:load-checkpoint', wfId),

  // Skills
  loadSkills: () => ipcRenderer.invoke('store:load-skills'),
  saveSkill: (skill) => ipcRenderer.invoke('store:save-skill', skill),
  deleteSkill: (id) => ipcRenderer.invoke('store:delete-skill', id),

  // SSO
  listProfiles: () => ipcRenderer.invoke('sso:list-profiles'),
  ssoLogin: (profile) => ipcRenderer.invoke('sso:login', profile),
  getCredentials: (profile) => ipcRenderer.invoke('sso:get-credentials', profile),
  getBedrockUsername: (profile) => ipcRenderer.invoke('sso:get-bedrock-username', profile),
  getSSOExpiry: (profile) => ipcRenderer.invoke('sso:get-expiry', profile),

  // Terminal
  terminalCreate: (id) => ipcRenderer.invoke('terminal:create', id),
  terminalWrite: (id, data) => ipcRenderer.invoke('terminal:write', id, data),
  terminalKill: (id) => ipcRenderer.invoke('terminal:kill', id),
  onTerminalData: (cb) => ipcRenderer.on('terminal:data', (_, data) => cb(data)),
  onTerminalExit: (cb) => ipcRenderer.on('terminal:exit', (_, data) => cb(data)),

  // Project Analysis
  analyzeProject: (dirPath) => ipcRenderer.invoke('project:analyze', dirPath),
  getDependencies: (dirPath) => ipcRenderer.invoke('project:dependencies', dirPath),

  // Git
  gitLog: (dirPath, limit) => ipcRenderer.invoke('git:log', dirPath, limit),
  gitShow: (dirPath, hash) => ipcRenderer.invoke('git:show', dirPath, hash),

  // Search
  projectSearch: (dirPath, query, options) => ipcRenderer.invoke('git:search', dirPath, query, options),
});
