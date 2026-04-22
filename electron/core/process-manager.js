const { spawn } = require('child_process');
const path = require('path');

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
      const shell = process.platform === 'win32' ? 'cmd.exe' : process.env.SHELL || '/bin/bash';
      const pty = spawn(shell, [], {
        cwd: process.env.HOME || process.cwd(),
        env: { ...process.env, TERM: 'xterm-256color' },
        stdio: ['pipe', 'pipe', 'pipe'],
      });

      this._terminals.set(id, pty);

      pty.stdout.on('data', (data) => {
        if (mainWindow && !mainWindow.isDestroyed()) {
          mainWindow.webContents.send('terminal:data', { id, data: data.toString() });
        }
      });

      pty.stderr.on('data', (data) => {
        if (mainWindow && !mainWindow.isDestroyed()) {
          mainWindow.webContents.send('terminal:data', { id, data: data.toString() });
        }
      });

      pty.on('exit', (code) => {
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
    const pty = this._terminals.get(id);
    if (pty && pty.stdin.writable) {
      pty.stdin.write(data);
    }
  }

  killTerminal(id) {
    const pty = this._terminals.get(id);
    if (pty) {
      pty.kill('SIGTERM');
      this._terminals.delete(id);
    }
  }

  stopAll() {
    this.stopPython();
    for (const [id, pty] of this._terminals) {
      pty.kill('SIGTERM');
    }
    this._terminals.clear();
  }
}

module.exports = { ProcessManager };
