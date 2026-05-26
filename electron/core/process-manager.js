const { spawn } = require('child_process');
const path = require('path');

let pty;
try { pty = require('node-pty'); } catch { pty = null; }

class ProcessManager {
  constructor() {
    this._pythonProcess = null;
    this._terminals = new Map();
    this._bridgeUrl = '';
    this._bridgeToken = '';
  }

  /**
   * Set bridge server env vars. Called once after bridge-server starts.
   * Next startPython() will inject these into the Python process env.
   */
  setBridgeEnv(url, token) {
    this._bridgeUrl = url || '';
    this._bridgeToken = token || '';
  }

  startPython() {
    if (this._pythonProcess) return;
    const scriptPath = path.join(__dirname, '..', '..', 'scripts', 'start_server.py');
    console.log('[ProcessManager] Starting Python backend...');
    const env = { ...process.env, PYTHONUNBUFFERED: '1' };
    if (this._bridgeUrl) env.AE_BRIDGE_URL = this._bridgeUrl;
    if (this._bridgeToken) env.AE_BRIDGE_TOKEN = this._bridgeToken;

    // 사용자별 쓰기 가능한 .generated 루트 — 30명 배포 시 앱 설치 폴더가 읽기 전용일 수 있어
    // userData 경로(또는 ~/.agentic-editor)를 명시적으로 주입한다.
    try {
      const electronApp = require('electron').app;
      if (electronApp && typeof electronApp.getPath === 'function') {
        // userData/generated/ — OS가 사용자별로 자동 격리
        env.AE_GENERATED_ROOT = path.join(electronApp.getPath('userData'), 'generated');
      } else {
        env.AE_GENERATED_ROOT = path.join(require('os').homedir(), '.agentic-editor');
      }
    } catch (_) {
      env.AE_GENERATED_ROOT = path.join(require('os').homedir(), '.agentic-editor');
    }

    this._pythonProcess = spawn('python3', [scriptPath], {
      cwd: path.join(__dirname, '..', '..'),
      env,
      stdio: ['pipe', 'pipe', 'pipe'],
    });
    this._pythonProcess.stdout.on('data', (d) => console.log(`[dev:python] ${d.toString().trim()}`));
    this._pythonProcess.stderr.on('data', (d) => console.error(`[dev:python] ${d.toString().trim()}`));
    this._pythonProcess.on('exit', (code) => { console.log(`[ProcessManager] Python exited with code ${code}`); this._pythonProcess = null; });
  }

  stopPython() {
    if (this._pythonProcess) { this._pythonProcess.kill('SIGTERM'); this._pythonProcess = null; }
  }

  createTerminal(id, target, opts = {}) {
    try {
      const shell = opts.shell || (process.platform === 'win32' ? 'powershell.exe' : (process.env.SHELL || '/bin/bash'));
      const cwd = opts.cwd || process.env.HOME || process.cwd();

      const webContents = target && typeof target.send === 'function'
        ? target
        : (target && target.webContents ? target.webContents : null);

      const safeSend = (channel, payload) => {
        try {
          if (webContents && !webContents.isDestroyed()) webContents.send(channel, payload);
        } catch (_) {}
      };

      if (pty) {
        const term = pty.spawn(shell, [], {
          name: 'xterm-256color',
          cols: opts.cols || 120, rows: opts.rows || 30,
          cwd,
          env: { ...process.env, TERM: 'xterm-256color', ...(opts.env || {}) },
        });
        this._terminals.set(id, { type: 'pty', proc: term });
        term.onData((data) => safeSend('terminal:data', { id, data }));
        term.onExit(({ exitCode }) => { this._terminals.delete(id); safeSend('terminal:exit', { id, code: exitCode }); });
        console.log(`[PTY] node-pty 터미널 생성: ${shell} (pid: ${term.pid}) cwd: ${cwd}`);
        return { success: true, id };
      }

      console.log(`[PTY] node-pty 없음, spawn fallback: ${shell}`);
      const proc = spawn(shell, [], {
        cwd,
        env: { ...process.env, TERM: 'dumb', ...(opts.env || {}) },
        stdio: ['pipe', 'pipe', 'pipe'],
      });
      this._terminals.set(id, { type: 'spawn', proc });
      proc.stdout.on('data', (d) => safeSend('terminal:data', { id, data: d.toString() }));
      proc.stderr.on('data', (d) => safeSend('terminal:data', { id, data: d.toString() }));
      proc.on('exit', (code) => { this._terminals.delete(id); safeSend('terminal:exit', { id, code }); });
      return { success: true, id };
    } catch (e) {
      console.error(`[PTY] 터미널 생성 실패:`, e.message);
      return { success: false, error: e.message };
    }
  }

  writeTerminal(id, data) {
    const entry = this._terminals.get(id);
    if (!entry) return;
    if (entry.type === 'pty') entry.proc.write(data);
    else if (entry.proc.stdin?.writable) entry.proc.stdin.write(data);
  }

  killTerminal(id) {
    const entry = this._terminals.get(id);
    if (!entry) return;
    if (entry.type === 'pty') entry.proc.kill();
    else entry.proc.kill('SIGTERM');
    this._terminals.delete(id);
  }

  resizeTerminal(id, cols, rows) {
    const entry = this._terminals.get(id);
    if (!entry) return;
    if (entry.type === 'pty' && typeof entry.proc.resize === 'function') {
      entry.proc.resize(cols, rows);
    }
  }

  stopAll() {
    this.stopPython();
    for (const [, entry] of this._terminals) {
      if (entry.type === 'pty') entry.proc.kill();
      else entry.proc.kill('SIGTERM');
    }
    this._terminals.clear();
  }
}

module.exports = { ProcessManager };
