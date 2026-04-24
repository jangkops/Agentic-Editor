const { app, BrowserWindow, ipcMain, dialog } = require('electron');
const path = require('path');
const fs = require('fs');
const { ProcessManager } = require('./core/process-manager');
const { DataStore } = require('./core/data-store');
const { AwsSsoManager } = require('./core/aws-sso-manager');

let mainWindow;
const processManager = new ProcessManager();
const dataStore = new DataStore();
const ssoManager = new AwsSsoManager();

function createWindow() {
  mainWindow = new BrowserWindow({
    width: 1500, height: 900, minWidth: 1000, minHeight: 600,
    titleBarStyle: 'hiddenInset',
    backgroundColor: '#1a1a1a',
    title: 'AI 에디터',
    webPreferences: {
      preload: path.join(__dirname, 'preload.js'),
      contextIsolation: true,
      nodeIntegration: false,
    },
  });
  mainWindow.loadFile(path.join(__dirname, '..', 'src', 'index.html'));
}

app.whenReady().then(() => {
  createWindow();
  // dev 모드(npm run dev)에서는 dev:python이 이미 서버를 시작하므로 중복 시작 방지
  const isDev = process.argv.includes('--dev') || process.env.NODE_ENV === 'development' || process.env.npm_lifecycle_event === 'dev:electron';
  if (!isDev) {
    // 포트가 이미 사용 중인지 확인
    const http = require('http');
    const checkReq = http.request({ host: '127.0.0.1', port: 8765, method: 'HEAD', path: '/health', timeout: 2000 }, (res) => {
      console.log('[ProcessManager] Python backend already running, skipping start');
    });
    checkReq.on('error', () => {
      processManager.startPython();
    });
    checkReq.end();
  } else {
    console.log('[ProcessManager] Dev mode — skipping Python start (dev:python handles it)');
  }
  app.on('activate', () => {
    if (BrowserWindow.getAllWindows().length === 0) createWindow();
  });
});

app.on('window-all-closed', () => {
  processManager.stopAll();
  if (process.platform !== 'darwin') app.quit();
});

app.on('before-quit', () => {
  processManager.stopAll();
});

// ===== File System IPC =====
ipcMain.handle('openFolder', async () => {
  const r = await dialog.showOpenDialog(mainWindow, { properties: ['openDirectory'] });
  return r.canceled ? null : r.filePaths[0];
});

ipcMain.handle('fs:open-file', async () => {
  const r = await dialog.showOpenDialog(mainWindow, { properties: ['openFile'] });
  return r.canceled ? null : r.filePaths[0];
});

ipcMain.handle('fs:read-file', (_, p) => {
  try { return fs.readFileSync(p, 'utf-8'); } catch { return null; }
});

ipcMain.handle('fs:write-file', (_, p, content) => {
  try {
    fs.mkdirSync(path.dirname(p), { recursive: true });
    fs.writeFileSync(p, content, 'utf-8');
    return true;
  } catch { return false; }
});

ipcMain.handle('fs:rename', (_, oldPath, newPath) => {
  try { fs.renameSync(oldPath, newPath); return true; } catch { return false; }
});

ipcMain.handle('fs:mkdir', (_, dirPath) => {
  try { fs.mkdirSync(dirPath, { recursive: true }); return true; } catch { return false; }
});

ipcMain.handle('fs:list-files', (_, dirPath) => {
  try {
    const entries = fs.readdirSync(dirPath, { withFileTypes: true });
    return entries.map(e => ({
      name: e.name,
      path: path.join(dirPath, e.name),
      isDirectory: e.isDirectory(),
    }));
  } catch { return []; }
});

ipcMain.handle('fs:get-user-data-path', () => app.getPath('userData'));

// ===== Settings IPC =====
ipcMain.handle('store:load-settings', () => dataStore.loadSettings());
ipcMain.handle('store:save-settings', (_, settings) => dataStore.saveSettings(settings));

// ===== Usage IPC =====
ipcMain.handle('store:load-usage', () => dataStore.loadUsage());
ipcMain.handle('store:update-usage', (_, tokens) => dataStore.updateUsage(tokens));

// ===== History IPC =====
ipcMain.handle('store:save-history', (_, date, messages) => dataStore.saveHistory(date, messages));

// ===== Checkpoint IPC =====
ipcMain.handle('store:save-checkpoint', (_, wfId, state) => {
  const { CheckpointStore } = require('./core/data-store');
  // Use dataStore path
  const cpPath = path.join(dataStore.basePath, 'checkpoints');
  const cpFile = path.join(cpPath, `${wfId}.json`);
  fs.mkdirSync(cpPath, { recursive: true });
  fs.writeFileSync(cpFile, JSON.stringify({ workflow_id: wfId, state }, null, 2));
  return true;
});

ipcMain.handle('store:load-checkpoint', (_, wfId) => {
  const cpFile = path.join(dataStore.basePath, 'checkpoints', `${wfId}.json`);
  if (!fs.existsSync(cpFile)) return null;
  try { return JSON.parse(fs.readFileSync(cpFile, 'utf-8')).state; } catch { return null; }
});

// ===== Skills IPC =====
ipcMain.handle('store:load-skills', () => dataStore.loadSkills());
ipcMain.handle('store:save-skill', (_, skill) => dataStore.saveSkill(skill));
ipcMain.handle('store:delete-skill', (_, id) => dataStore.deleteSkill(id));

// ===== SSO IPC =====
ipcMain.handle('sso:list-profiles', () => ssoManager.listProfiles());
ipcMain.handle('sso:login', (_, profile) => ssoManager.login(profile));
ipcMain.handle('sso:get-credentials', (_, profile) => ssoManager.getCredentials(profile));
ipcMain.handle('sso:get-bedrock-username', (_, profile) => ssoManager.getBedrockUsername(profile));
ipcMain.handle('sso:get-expiry', (_, profile) => {
  // SSO 캐시 파일에서 만료 시간 읽기
  const os = require('os');
  const crypto = require('crypto');
  const ssoDir = path.join(os.homedir(), '.aws', 'sso', 'cache');
  try {
    if (!fs.existsSync(ssoDir)) return null;
    const files = fs.readdirSync(ssoDir).filter(f => f.endsWith('.json'));
    let latestExpiry = null;
    for (const f of files) {
      try {
        const data = JSON.parse(fs.readFileSync(path.join(ssoDir, f), 'utf-8'));
        if (data.expiresAt) {
          const exp = new Date(data.expiresAt);
          if (!latestExpiry || exp > latestExpiry) latestExpiry = exp;
        }
      } catch {}
    }
    return latestExpiry ? latestExpiry.toISOString() : null;
  } catch { return null; }
});

// ===== Terminal IPC =====
ipcMain.handle('terminal:create', (_, id) => processManager.createTerminal(id, mainWindow));
ipcMain.handle('terminal:write', (_, id, data) => processManager.writeTerminal(id, data));
ipcMain.handle('terminal:kill', (_, id) => processManager.killTerminal(id));
ipcMain.handle('terminal:resize', (_, id, cols, rows) => {
  // PTY resize — basic implementation
});

// ===== Project Analysis IPC =====
ipcMain.handle('project:analyze', async (_, dirPath) => {
  if (!dirPath || !fs.existsSync(dirPath)) return null;
  const stats = { totalLines: 0, totalFiles: 0, totalDirs: 0, todos: 0, extensions: {}, roles: { source: 0, config: 0, docs: 0, test: 0, style: 0, asset: 0 }, files: [] };
  const IGNORE = new Set(['node_modules', '__pycache__', '.git', '.venv', 'dist', 'build', '.DS_Store', '.next', 'coverage', '.nyc_output']);
  const SRC_EXT = new Set(['js', 'ts', 'jsx', 'tsx', 'py', 'java', 'go', 'rs', 'c', 'cpp', 'h', 'rb', 'php', 'swift', 'kt']);
  const CFG_EXT = new Set(['json', 'yml', 'yaml', 'toml', 'ini', 'env', 'xml', 'lock']);
  const DOC_EXT = new Set(['md', 'txt', 'rst', 'adoc']);
  const TEST_PAT = /test|spec|__test__|__spec__/i;
  const STYLE_EXT = new Set(['css', 'scss', 'sass', 'less', 'styl']);
  const ASSET_EXT = new Set(['png', 'jpg', 'jpeg', 'gif', 'svg', 'ico', 'woff', 'woff2', 'ttf', 'eot', 'mp3', 'mp4', 'webp']);

  function walk(dir, depth) {
    if (depth > 10) return;
    let entries;
    try { entries = fs.readdirSync(dir, { withFileTypes: true }); } catch { return; }
    for (const e of entries) {
      if (IGNORE.has(e.name) || e.name.startsWith('.')) continue;
      const fp = path.join(dir, e.name);
      if (e.isDirectory()) { stats.totalDirs++; walk(fp, depth + 1); }
      else {
        stats.totalFiles++;
        const ext = e.name.split('.').pop().toLowerCase();
        stats.extensions[ext] = (stats.extensions[ext] || 0) + 1;
        let lines = 0;
        if (!ASSET_EXT.has(ext)) {
          try {
            const content = fs.readFileSync(fp, 'utf-8');
            lines = content.split('\n').length;
            const todoMatches = content.match(/TODO|FIXME|HACK|XXX/gi);
            if (todoMatches) stats.todos += todoMatches.length;
          } catch {}
        }
        stats.totalLines += lines;
        // Role classification
        if (TEST_PAT.test(e.name) || TEST_PAT.test(fp)) stats.roles.test++;
        else if (STYLE_EXT.has(ext)) stats.roles.style++;
        else if (ASSET_EXT.has(ext)) stats.roles.asset++;
        else if (DOC_EXT.has(ext)) stats.roles.docs++;
        else if (CFG_EXT.has(ext)) stats.roles.config++;
        else if (SRC_EXT.has(ext)) stats.roles.source++;
        const rel = path.relative(dirPath, fp);
        stats.files.push({ name: e.name, path: rel, ext, lines });
      }
    }
  }
  walk(dirPath, 0);
  return stats;
});

// ===== Dependency Analysis IPC =====
ipcMain.handle('project:dependencies', async (_, dirPath) => {
  if (!dirPath) return null;
  const pkgPath = path.join(dirPath, 'package.json');
  const reqPath = path.join(dirPath, 'requirements.txt');
  const result = { production: {}, development: {}, python: [] };
  try {
    if (fs.existsSync(pkgPath)) {
      const pkg = JSON.parse(fs.readFileSync(pkgPath, 'utf-8'));
      result.production = pkg.dependencies || {};
      result.development = pkg.devDependencies || {};
    }
  } catch {}
  try {
    if (fs.existsSync(reqPath)) {
      result.python = fs.readFileSync(reqPath, 'utf-8').split('\n').filter(l => l.trim() && !l.startsWith('#'));
    }
  } catch {}
  return result;
});

// ===== Git IPC =====
ipcMain.handle('git:log', async (_, dirPath, limit) => {
  const { execSync } = require('child_process');
  try {
    const log = execSync(`git log --oneline --decorate --all -n ${limit || 50}`, { cwd: dirPath, encoding: 'utf-8', timeout: 10000 });
    return log.split('\n').filter(Boolean).map(line => {
      const match = line.match(/^([a-f0-9]+)\s+(?:\(([^)]+)\)\s+)?(.+)$/);
      if (!match) return { hash: '', refs: '', message: line };
      return { hash: match[1], refs: match[2] || '', message: match[3] };
    });
  } catch { return []; }
});

ipcMain.handle('git:show', async (_, dirPath, hash) => {
  const { execSync } = require('child_process');
  try {
    const info = execSync(`git show --stat --format="%H%n%an%n%ae%n%ai%n%s%n%b%n---STAT---" ${hash}`, { cwd: dirPath, encoding: 'utf-8', timeout: 10000 });
    const parts = info.split('---STAT---');
    const lines = parts[0].split('\n');
    const diff = execSync(`git diff ${hash}~1 ${hash} 2>/dev/null || git show ${hash} --format=""`, { cwd: dirPath, encoding: 'utf-8', timeout: 10000 });
    return { hash: lines[0], author: lines[1], email: lines[2], date: lines[3], subject: lines[4], body: lines.slice(5).join('\n').trim(), stat: (parts[1] || '').trim(), diff };
  } catch { return null; }
});

ipcMain.handle('git:search', async (_, dirPath, query, options) => {
  const { execSync } = require('child_process');
  try {
    const flags = (options?.caseSensitive ? '' : '-i') + ' -n --include="*"';
    const cmd = `grep -r ${flags} --color=never -l "${query.replace(/"/g, '\\"')}" . 2>/dev/null | head -50`;
    const result = execSync(cmd, { cwd: dirPath, encoding: 'utf-8', timeout: 15000 });
    const files = result.split('\n').filter(Boolean);
    const matches = [];
    for (const file of files.slice(0, 30)) {
      try {
        const grepLines = execSync(`grep -n ${options?.caseSensitive ? '' : '-i'} --color=never "${query.replace(/"/g, '\\"')}" "${file}" 2>/dev/null | head -10`, { cwd: dirPath, encoding: 'utf-8', timeout: 5000 });
        const fileMatches = grepLines.split('\n').filter(Boolean).map(l => {
          const m = l.match(/^(\d+):(.*)$/);
          return m ? { line: +m[1], text: m[2].trim() } : null;
        }).filter(Boolean);
        if (fileMatches.length) matches.push({ file: file.replace(/^\.\//, ''), matches: fileMatches });
      } catch {}
    }
    return matches;
  } catch { return []; }
});
