const { spawn } = require('child_process');
const path = require('path');
let pty;
try { pty = require('node-pty'); } catch { pty = null; }

class ProcessManager {
  constructor() {
    this._pythonProcess = null;
    this._terminals = new Map();
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

    this._pythonProcess.stdout.on('data', (data) => {
      console.log(`[dev:python] ${data.toString().trim()}`);
    });

    this._pythonProcess.stderr.on('data', (data) => {
      console.error(`[dev:python] ${data.toString().trim()}`);
    });

    this._pythonProcess.on('exit', (code) => {
      console.log(`[ProcessManager] Python exited with code ${code}`);
      this._pythonProcess = null;
    });
  }

  stopPython() {
    if (this._pythonProcess) {
      this._pythonProcess.kill('SIGTERM');
      this._pythonProcess = null;
    }
  }

  // Terminal PTY management
  createTerminal(id, mainWindow) {
    try {
      const shell = process.platform === 'win32' ? 'powershell.exe' : process.env.SHELL || '/bin/bash';

      if (pty) {
        // node-pty — 진짜 PTY (echo, 색상, interactive mode 지원)
        const term = pty.spawn(shell, [], {
          name: 'xterm-256color',
          cols: 120,
          rows: 30,
          cwd: process.env.HOME || process.cwd(),
          env: { ...process.env, TERM: 'xterm-256color' },
        });

        this._terminals.set(id, { type: 'pty', term });

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

        return { success: true, id };
      }

      // fallback: child_process (echo 안 됨)
      const proc = spawn(shell, [], {
        cwd: process.env.HOME || process.cwd(),
        env: { ...process.env, TERM: 'xterm-256color' },
        stdio: ['pipe', 'pipe', 'pipe'],
      });

      this._terminals.set(id, { type: 'spawn', term: proc });

      proc.stdout.on('data', (data) => {
        if (mainWindow && !mainWindow.isDestroyed()) {
          mainWindow.webContents.send('terminal:data', { id, data: data.toString() });
        }
      });

      proc.stderr.on('data', (data) => {
        if (mainWindow && !mainWindow.isDestroyed()) {
          mainWindow.webContents.send('terminal:data', { id, data: data.toString() });
        }
      });

      proc.on('exit', (code) => {
        this._terminals.delete(id);
        if (mainWindow && !mainWindow.isDestroyed()) {
          mainWindow.webContents.send('terminal:exit', { id, code });
        }
      });

      return { success: true, id };
    } catch (e) {
      return { success: false, error: e.message };
    }
  }

  writeTerminal(id, data) {
    const entry = this._terminals.get(id);
    if (!entry) return;
    if (entry.type === 'pty') {
      entry.term.write(data);
    } else if (entry.term.stdin?.writable) {
      entry.term.stdin.write(data);
    }
  }

  killTerminal(id) {
    const entry = this._terminals.get(id);
    if (!entry) return;
    if (entry.type === 'pty') {
      entry.term.kill();
    } else {
      entry.term.kill('SIGTERM');
    }
    this._terminals.delete(id);
  }

  stopAll() {
    this.stopPython();
    for (const [id, entry] of this._terminals) {
      if (entry.type === 'pty') entry.term.kill();
      else entry.term.kill('SIGTERM');
    }
    this._terminals.clear();
  }
}

module.exports = { ProcessManager };
