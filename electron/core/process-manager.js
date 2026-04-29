const { spawn, fork } = require('child_process');
const path = require('path');

class ProcessManager {
  constructor() {
    this._pythonProcess = null;
    this._terminals = new Map();
    this._ptyWorker = null;
    this._mainWindow = null;
    this._pendingCreates = new Map();
  }

  _ensurePtyWorker() {
    if (this._ptyWorker && !this._ptyWorker.killed) return;
    const workerPath = path.join(__dirname, 'pty-worker.js');
    // 시스템 Node.js로 fork (Electron Node가 아닌)
    this._ptyWorker = fork(workerPath, [], {
      execPath: '/opt/homebrew/bin/node',
      stdio: ['pipe', 'pipe', 'pipe', 'ipc'],
    });
    this._ptyWorker.on('message', (msg) => {
      if (msg.type === 'ready') {
        console.log('[PTY] worker 준비 완료');
      } else if (msg.type === 'created') {
        const resolve = this._pendingCreates.get(msg.id);
        if (resolve) { resolve(msg); this._pendingCreates.delete(msg.id); }
      } else if (msg.type === 'data') {
        if (this._mainWindow && !this._mainWindow.isDestroyed()) {
          this._mainWindow.webContents.send('terminal:data', { id: msg.id, data: msg.data });
        }
      } else if (msg.type === 'exit') {
        this._terminals.delete(msg.id);
        if (this._mainWindow && !this._mainWindow.isDestroyed()) {
          this._mainWindow.webContents.send('terminal:exit', { id: msg.id, code: msg.code });
        }
      }
    });
    this._ptyWorker.on('error', (e) => console.error('[PTY] worker 에러:', e.message));
    this._ptyWorker.on('exit', (code) => { console.log('[PTY] worker 종료:', code); this._ptyWorker = null; });
    this._ptyWorker.stderr?.on('data', (d) => console.error('[PTY] worker stderr:', d.toString()));
  }

  startPython() {
    if (this._pythonProcess) return;
    const scriptPath = path.join(__dirname, '..', '..', 'scripts', 'start_server.py');
    console.log('[ProcessManager] Starting Python backend...');
    this._pythonProcess = spawn('python3', [scriptPath], {
      cwd: path.join(__dirname, '..', '..'),
      env: { ...process.env, PYTHONUNBUFFERED: '1' },
      stdio: ['pipe', 'pipe', 'pipe'],
    });
    this._pythonProcess.stdout.on('data', (data) => console.log(`[dev:python] ${data.toString().trim()}`));
    this._pythonProcess.stderr.on('data', (data) => console.error(`[dev:python] ${data.toString().trim()}`));
    this._pythonProcess.on('exit', (code) => { console.log(`[ProcessManager] Python exited with code ${code}`); this._pythonProcess = null; });
  }

  stopPython() {
    if (this._pythonProcess) { this._pythonProcess.kill('SIGTERM'); this._pythonProcess = null; }
  }

  createTerminal(id, mainWindow) {
    this._mainWindow = mainWindow;
    try {
      this._ensurePtyWorker();
      return new Promise((resolve) => {
        this._pendingCreates.set(id, (result) => {
          if (result.success) this._terminals.set(id, true);
          resolve(result);
        });
        this._ptyWorker.send({
          type: 'create', id,
          shell: process.env.SHELL || '/bin/zsh',
          cwd: process.env.HOME || process.cwd(),
          cols: 120, rows: 30,
        });
        // 5초 타임아웃
        setTimeout(() => {
          if (this._pendingCreates.has(id)) {
            this._pendingCreates.delete(id);
            resolve({ success: false, error: 'PTY 생성 타임아웃' });
          }
        }, 5000);
      });
    } catch (e) {
      return { success: false, error: e.message };
    }
  }

  writeTerminal(id, data) {
    if (this._ptyWorker && this._terminals.has(id)) {
      this._ptyWorker.send({ type: 'write', id, data });
    }
  }

  killTerminal(id) {
    if (this._ptyWorker && this._terminals.has(id)) {
      this._ptyWorker.send({ type: 'kill', id });
      this._terminals.delete(id);
    }
  }

  stopAll() {
    this.stopPython();
    if (this._ptyWorker) { this._ptyWorker.kill(); this._ptyWorker = null; }
    this._terminals.clear();
  }
}

module.exports = { ProcessManager };
