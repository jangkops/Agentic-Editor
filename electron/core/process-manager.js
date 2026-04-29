const { spawn } = require('child_process');
const path = require('path');
let pty;
try { pty = require('node-pty'); console.log('[PTY] node-pty 로드 성공'); } catch (e) { pty = null; console.log('[PTY] node-pty 사용 불가, spawn 사용:', e.message); }

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
      console.log(`[PTY] shell=${shell}, HOME=${process.env.HOME}, platform=${process.platform}`);

      if (false && pty) {
        // node-pty — 진짜 PTY
        const shells = [shell, '/bin/zsh', '/bin/bash', '/bin/sh'];
        let term = null;
        let usedShell = '';
        for (const sh of shells) {
          try {
            term = pty.spawn(sh, [], {
              name: 'xterm-256color',
              cols: 120,
              rows: 30,
              cwd: process.env.HOME || process.cwd(),
              env: { ...process.env, TERM: 'xterm-256color' },
            });
            usedShell = sh;
            console.log(`[PTY] spawn 성공: ${sh}`);
            break;
          } catch (e) {
            console.log(`[PTY] spawn 실패 (${sh}): ${e.message}`);
          }
        }
        if (!term) throw new Error('모든 셸 spawn 실패');

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

      // macOS: script 명령으로 PTY 에뮬레이션 (interactive shell + echo)
      const isLinux = process.platform === 'linux';
      let proc;
      if (process.platform === 'darwin') {
        proc = spawn('script', ['-q', '/dev/null', shell, '-il'], {
          cwd: process.env.HOME || process.cwd(),
          env: { ...process.env, TERM: 'xterm-256color' },
          stdio: ['pipe', 'pipe', 'pipe'],
        });
      } else {
        proc = spawn(shell, ['-il'], {
          cwd: process.env.HOME || process.cwd(),
          env: { ...process.env, TERM: 'xterm-256color' },
          stdio: ['pipe', 'pipe', 'pipe'],
        });
      }
      console.log(`[PTY] spawn 성공: script + ${shell} -il (pid: ${proc.pid})`);

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
