const { contextBridge, ipcRenderer } = require('electron');

contextBridge.exposeInMainWorld('electronAPI', {
  // File system
  openFolder: () => ipcRenderer.invoke('openFolder'),
  // opts (optional): { filters: [{ name, extensions: [...] }] } — e.g. .pptx filter.
  // Backward-compatible: openFile() with no arg → opts undefined → handler uses no filter.
  openFile: (opts) => ipcRenderer.invoke('fs:open-file', opts),
  readFile: (p) => ipcRenderer.invoke('fs:read-file', p),
  writeFile: (p, content) => ipcRenderer.invoke('fs:write-file', p, content),
  rename: (oldP, newP) => ipcRenderer.invoke('fs:rename', oldP, newP),
  mkdir: (p) => ipcRenderer.invoke('fs:mkdir', p),
  deleteFile: (p) => ipcRenderer.invoke('fs:delete-file', p),
  readDir: (p) => ipcRenderer.invoke('fs:list-files', p),
  // Media file preview support
  readFileBase64: (p) => ipcRenderer.invoke('fs:read-file-base64', p),
  listFilesWithStats: (p) => ipcRenderer.invoke('fs:list-files-with-stats', p),
  // Local-only variants — bypass SFTP bridge so panel can see workstation
  // ~/.agentic-editor/.generated/ files while a remote SSH session is active.
  listFilesWithStatsLocal: (p) => ipcRenderer.invoke('fs:list-files-with-stats-local', p),
  readFileBase64Local: (p) => ipcRenderer.invoke('fs:read-file-base64-local', p),
  // 로컬 전용 삭제 — listFilesWithStatsLocal로 읽은 워크스테이션 .generated/ 파일을
  // 원격 SFTP 브리지 우회로 확실히 삭제. (이슈 3: 목록은 로컬, 삭제는 원격 경유 불일치 수정)
  deleteFileLocal: (p) => ipcRenderer.invoke('fs:delete-file-local', p),
  watchDirectory: (p) => ipcRenderer.invoke('fs:watch-directory', p),
  unwatchDirectory: (p) => ipcRenderer.invoke('fs:unwatch-directory', p),
  showSaveDialog: (opts) => ipcRenderer.invoke('fs:show-save-dialog', opts),
  showItemInFolder: (p) => ipcRenderer.invoke('fs:show-item-in-folder', p),
  openPath: (p) => ipcRenderer.invoke('fs:open-path', p),
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
  verifyBedrockUsername: (profile, name) => ipcRenderer.invoke('sso:verify-bedrock-username', profile, name),
  getSSOExpiry: (profile) => ipcRenderer.invoke('sso:get-expiry', profile),
  // Onboarding — ~/.aws/config에 SSO 프로파일 블록 기록 (spec app-deployment-readiness §6.1/6.2).
  // input: { name, startUrl, region, accountId, roleName } (secret-free)
  // returns: {success:true, profile} | {success:false, duplicate?, error, manualHint?}
  writeSsoProfile: (input) => ipcRenderer.invoke('aws:write-sso-profile', input),
  // Zero-config 온보딩 — 조직 기본 SSO 프리셋으로 프로파일 자동 생성(무입력).
  // returns: {success:true, profile, created} | {success:false, profile?, error, manualHint?}
  ensureDefaultSsoProfile: () => ipcRenderer.invoke('aws:ensure-default-sso-profile'),

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
  // 저장소 clone — 종료코드/stderr로 성패 판정 (GitHub 가져오기). url/branch/dest/token.
  // token은 private 저장소용(선택) — 메인 프로세스에서 1회 사용, 저장/로깅 안 함.
  gitClone: (url, branch, dest, token) => ipcRenderer.invoke('git:clone', url, branch, dest, token),

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
  remoteGoLocal: () => ipcRenderer.invoke('remote:go-local'),
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

  // Slides — HTML → PNG capture via hidden BrowserWindow (Genspark/Gamma-class).
  // opts: { html, width=1920, height=1080, outputPath, timeoutMs=30000 }
  // returns: { ok, path, width, height, sizeBytes } or { ok:false, error }
  renderSlideToPng: (opts) => ipcRenderer.invoke('slides:render-html-to-png', opts),

  // Templates (pptx-template-styling) — whitelisted proxies to template:* IPC
  // handlers (registered in main process only). Never expose ipcRenderer.
  registerTemplate: (payload) => ipcRenderer.invoke('template:register', payload),
  listTemplates: () => ipcRenderer.invoke('template:list'),
  getTemplate: (id) => ipcRenderer.invoke('template:get', id),
  getTemplateStyleProfile: (id) => ipcRenderer.invoke('template:get-style-profile', id),
  deleteTemplate: (id) => ipcRenderer.invoke('template:delete', id),
});
