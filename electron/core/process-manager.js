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

  createTerminal(id, mainWindow) {
    try {
      const shell = process.platform === 'win32' ? 'powershell.exe' : (process.env.SHELL || '/bin/bash');

      if (pty) {
        // node-pty — 진짜 PTY
        const term = pty.spawn(shell, [], {
          name: 'xterm-256color',
          cols: 120, rows: 30,
          cwd: process.env.HOME || process.cwd(),
          env: { ...process.env, TERM: 'xterm-256color' },
        });
        this._terminals.set(id, { type: 'pty', proc: term });
        term.onData((data) => {
          if (mainWindow && !mainWindow.isDestroyed()) {
            mainWindow.webContents.send('terminal:data', { id, data });
          }
        });
        term.onExit(({ exitCode }) => {
          this._terminals.delete(id);
          if (mainWindow && !mainWindow.isDestroyed()) {
            mainWindow.webContents.send('terminal:exit', { id, code: exitCode });
          }
        });
        console.log(`[PTY] node-pty 터미널 생성: ${shell} (pid: ${term.pid})`);
        return { success: true, id };
      }

      // fallback: child_process.spawn (echo 없음, 기본 동작)
      console.log(`[PTY] node-pty 없음, spawn fallback: ${shell}`);
      const proc = spawn(shell, [], {
        cwd: process.env.HOME || process.cwd(),
        env: { ...process.env, TERM: 'dumb' },
        stdio: ['pipe', 'pipe', 'pipe'],
      });
      this._terminals.set(id, { type: 'spawn', proc });
      proc.stdout.on('data', (d) => {
        if (mainWindow && !mainWindow.isDestroyed()) mainWindow.webContents.send('terminal:data', { id, data: d.toString() });
      });
      proc.stderr.on('data', (d) => {
        if (mainWindow && !mainWindow.isDestroyed()) mainWindow.webContents.send('terminal:data', { id, data: d.toString() });
      });
      proc.on('exit', (code) => {
        this._terminals.delete(id);
        if (mainWindow && !mainWindow.isDestroyed()) mainWindow.webContents.send('terminal:exit', { id, code });
      });
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
