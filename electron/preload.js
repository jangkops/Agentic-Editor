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
  // Media file preview support
  readFileBase64: (p) => ipcRenderer.invoke('fs:read-file-base64', p),
  listFilesWithStats: (p) => ipcRenderer.invoke('fs:list-files-with-stats', p),
  watchDirectory: (p) => ipcRenderer.invoke('fs:watch-directory', p),
  unwatchDirectory: (p) => ipcRenderer.invoke('fs:unwatch-directory', p),
  showSaveDialog: (opts) => ipcRenderer.invoke('fs:show-save-dialog', opts),
  onDirectoryChanged: (cb) => {
    const wrapped = (_evt, data) => cb(data);
    ipcRenderer.on('fs:directory-changed', wrapped);
    return () => ipcRenderer.removeListener('fs:directory-changed', wrapped);
  },
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

  // Denied models (Gateway 호출 실패 학습)
  loadDeniedModels: () => ipcRenderer.invoke('store:load-denied-models'),
  addDeniedModel: (modelId) => ipcRenderer.invoke('store:add-denied-model', modelId),
  clearDeniedModels: () => ipcRenderer.invoke('store:clear-denied-models'),

  // SSO
  listProfiles: () => ipcRenderer.invoke('sso:list-profiles'),
  ssoLogin: (profile) => ipcRenderer.invoke('sso:login', profile),
  getCredentials: (profile) => ipcRenderer.invoke('sso:get-credentials', profile),
  getBedrockUsername: (profile) => ipcRenderer.invoke('sso:get-bedrock-username', profile),
  getSSOExpiry: (profile) => ipcRenderer.invoke('sso:get-expiry', profile),

  // Terminal
  terminalCreate: (id, opts) => ipcRenderer.invoke('terminal:create', id, opts),
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
  gitBranches: (dirPath) => ipcRenderer.invoke('git:branches', dirPath),
  gitCheckout: (dirPath, branch, opts) => ipcRenderer.invoke('git:checkout', dirPath, branch, opts),
  gitStatus: (dirPath) => ipcRenderer.invoke('git:status', dirPath),
  gitStashPush: (dirPath, message) => ipcRenderer.invoke('git:stash-push', dirPath, message),
  gitStashPop: (dirPath) => ipcRenderer.invoke('git:stash-pop', dirPath),
  gitStashList: (dirPath) => ipcRenderer.invoke('git:stash-list', dirPath),
  gitDiscardAll: (dirPath) => ipcRenderer.invoke('git:discard-all', dirPath),

  // Search
  projectSearch: (dirPath, query, options) => ipcRenderer.invoke('git:search', dirPath, query, options),

  // Capability denylist (multimedia spec Task 9.3)
  loadCapabilityDenylist: () => ipcRenderer.invoke('store:load-capability-denylist'),
  addCapabilityDenylistEntry: (entry) => ipcRenderer.invoke('store:add-capability-denylist-entry', entry),
  removeCapabilityDenylistEntry: (modelId, capability) => ipcRenderer.invoke('store:remove-capability-denylist-entry', modelId, capability),
  clearCapabilityDenylist: () => ipcRenderer.invoke('store:clear-capability-denylist'),

  // Remote SSH
  remoteListHosts: () => ipcRenderer.invoke('remote:list-hosts'),
  remoteAddAdHocHost: (h) => ipcRenderer.invoke('remote:add-ad-hoc-host', h),
  remoteSetFavorite: (p) => ipcRenderer.invoke('remote:set-favorite', p),
  remoteConnect: (p) => ipcRenderer.invoke('remote:connect', p),
  remoteDisconnect: (p) => ipcRenderer.invoke('remote:disconnect', p),
  remoteSwitchActive: (p) => ipcRenderer.invoke('remote:switch-active', p),
  remoteStatus: (p) => ipcRenderer.invoke('remote:status', p || {}),
  remoteRespondAuth: (p) => ipcRenderer.invoke('remote:respond-auth', p),
  remoteSetWorkspace: (p) => ipcRenderer.invoke('remote:set-workspace', p),
  remoteClearCredentials: () => ipcRenderer.invoke('remote:clear-credentials'),
  remoteShowLog: () => ipcRenderer.invoke('remote:show-log'),
  // Event subscriptions — each returns a cleanup function that removes the
  // listener (Req 10.4 cleanup契約). Callers should invoke the returned
  // function on unmount/teardown to avoid leaked handlers.
  onRemoteState: (cb) => {
    const h = (_, d) => cb(d);
    ipcRenderer.on('remote:event:state', h);
    return () => ipcRenderer.removeListener('remote:event:state', h);
  },
  onRemoteAuthRequest: (cb) => {
    const h = (_, d) => cb(d);
    ipcRenderer.on('remote:event:auth-request', h);
    return () => ipcRenderer.removeListener('remote:event:auth-request', h);
  },
  onRemoteHostKeyPrompt: (cb) => {
    const h = (_, d) => cb(d);
    ipcRenderer.on('remote:event:host-key-prompt', h);
    return () => ipcRenderer.removeListener('remote:event:host-key-prompt', h);
  },
  onRemoteFsChange: (cb) => {
    const h = (_, d) => cb(d);
    ipcRenderer.on('remote:event:fs-change', h);
    return () => ipcRenderer.removeListener('remote:event:fs-change', h);
  },
  onRemoteConnected: (cb) => {
    const h = (_, d) => cb(d);
    ipcRenderer.on('remote:event:connected', h);
    return () => ipcRenderer.removeListener('remote:event:connected', h);
  },
});
