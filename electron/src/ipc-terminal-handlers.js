/**
 * Terminal IPC Handlers
 * 책임: 터미널 세션 생성, 데이터 쓰기, 종료, 크기 조정
 *
 * Remote routing (Task 22.2, Requirements 7.1, 7.5):
 *   - When sessionRouter reports an active remote session AND the caller
 *     did not pass `forceLocal: true`, new terminals are spawned on the
 *     remote host via RemoteTerminalBridge. The IPC channel names
 *     (`terminal:create`, `terminal:write`, `terminal:resize`,
 *     `terminal:kill`, `terminal:data`, `terminal:exit`) are IDENTICAL
 *     for local and remote transports so the renderer is transport-blind.
 *   - Per-id origin is tracked in `_remoteTerminals` so that a terminal
 *     pinned to the workstation with `forceLocal:true` keeps receiving
 *     write/resize/kill via the local PTY path even after remote becomes
 *     active.
 *   - Bridge `data`/`exit`/`disconnected` events are forwarded to the
 *     webContents that created the first terminal on that bridge. The
 *     subscription is installed at most once per bridge instance (guarded
 *     by a module-level WeakSet) so repeated create calls do not fan out
 *     into duplicate events.
 */

const { ipcMain } = require('electron');

/**
 * Lazy-load the session router so this module keeps working in test
 * environments that never require the remote stack. Mirrors the pattern
 * used by `ipc-fs-handlers.js`.
 * @returns {Object|null}
 */
function _getRouter() {
  try { return require('./remote/session-router'); } catch { return null; }
}

/** IDs of terminals that currently live on the remote bridge. */
const _remoteTerminals = new Set();

/** Bridges that have already had their event listeners installed. */
const _wiredBridges = new WeakSet();

/**
 * Forward bridge-level terminal events onto the renderer IPC channel.
 * Installs listeners at most once per bridge instance so repeat
 * `terminal:create` calls do not multiply event delivery.
 *
 * @param {Object} bridge        RemoteTerminalBridge (EventEmitter)
 * @param {Electron.WebContents} webContents  target for ipc send()
 */
function _wireBridgeEvents(bridge, webContents) {
  if (!bridge || _wiredBridges.has(bridge)) return;
  _wiredBridges.add(bridge);

  const safeSend = (channel, payload) => {
    try {
      if (webContents && !webContents.isDestroyed()) {
        webContents.send(channel, payload);
      }
    } catch (_err) { /* webContents gone; nothing to do */ }
  };

  bridge.on('data', ({ id, data }) => {
    safeSend('terminal:data', { id, data });
  });
  bridge.on('exit', ({ id, code, signal }) => {
    _remoteTerminals.delete(id);
    safeSend('terminal:exit', { id, code, signal });
  });
  bridge.on('disconnected', ({ id, reason }) => {
    // Keep the id in _remoteTerminals so a future reattach still routes
    // to the bridge — matches Requirement 7.4 (scrollback preservation).
    safeSend('terminal:disconnected', { id, reason });
  });
}

/**
 * Disambiguate the second positional IPC argument. Historically the
 * renderer passed `undefined` in this slot (see `electron/preload.js`
 * `terminalCreate: (id) => ipcRenderer.invoke('terminal:create', id)`),
 * so we treat a plain object as the new-style options payload and
 * anything else as the legacy `mainWindow` slot.
 *
 * @param {*} arg
 * @returns {{opts: Object, legacyMainWindow: *}}
 */
function _splitCreateArg(arg) {
  const isPlainOptions =
    arg !== null && typeof arg === 'object' && !Array.isArray(arg);
  return {
    opts: isPlainOptions ? arg : {},
    legacyMainWindow: isPlainOptions ? undefined : arg,
  };
}

/**
 * Terminal IPC 핸들러 등록
 * @param {ProcessManager} processManager - 프로세스 관리자 인스턴스
 */
function registerTerminalHandlers(processManager) {
  /**
   * 터미널 세션 생성
   *
   * Payload shapes accepted (renderer side):
   *   - `invoke('terminal:create', id)`                             (legacy)
   *   - `invoke('terminal:create', id, {cols,rows,cwd,shell,forceLocal})`
   *
   * `forceLocal: true` pins the new terminal to the workstation even
   * when a remote session is active (Req 7.5).
   *
   * @returns {*} bridge.create result `{ok, id}` for remote, or
   *              processManager.createTerminal result for local.
   */
  ipcMain.handle('terminal:create', async (event, terminalId, arg2) => {
    const { opts, legacyMainWindow } = _splitCreateArg(arg2);
    try {
      const router = _getRouter();
      if (
        router &&
        typeof router.isRemoteActive === 'function' &&
        router.isRemoteActive({ forceLocal: Boolean(opts.forceLocal) })
      ) {
        const bridge = router.getTermBridge && router.getTermBridge();
        if (bridge && typeof bridge.create === 'function') {
          _wireBridgeEvents(bridge, event && event.sender);
          const result = await bridge.create(terminalId, {
            cols: opts.cols,
            rows: opts.rows,
            cwd: opts.cwd,
            shell: opts.shell,
          });
          if (result && result.ok) {
            _remoteTerminals.add(terminalId);
          }
          return result;
        }
        // Remote active but bridge unavailable — fall through to local.
      }
      return processManager.createTerminal(terminalId, legacyMainWindow, {
        cols: opts.cols,
        rows: opts.rows,
        cwd: opts.cwd,
        shell: opts.shell,
        env: opts.env,
      });
    } catch (error) {
      console.error(`[terminal:create] Error for ${terminalId}:`, error.message);
      return false;
    }
  });

  /**
   * 터미널에 데이터 쓰기
   */
  ipcMain.handle('terminal:write', async (_, terminalId, data) => {
    try {
      if (_remoteTerminals.has(terminalId)) {
        const router = _getRouter();
        const bridge = router && router.getTermBridge && router.getTermBridge();
        if (bridge && typeof bridge.write === 'function') {
          return bridge.write(terminalId, data);
        }
        // Bridge went away while we were holding a remote id — best
        // effort: drop the record and fall through so the renderer at
        // least sees a false return.
        _remoteTerminals.delete(terminalId);
        return false;
      }
      return processManager.writeTerminal(terminalId, data);
    } catch (error) {
      console.error(`[terminal:write] Error for ${terminalId}:`, error.message);
      return false;
    }
  });

  /**
   * 터미널 종료
   */
  ipcMain.handle('terminal:kill', async (_, terminalId) => {
    try {
      if (_remoteTerminals.has(terminalId)) {
        const router = _getRouter();
        const bridge = router && router.getTermBridge && router.getTermBridge();
        _remoteTerminals.delete(terminalId);
        if (bridge && typeof bridge.kill === 'function') {
          return bridge.kill(terminalId);
        }
        return false;
      }
      return processManager.killTerminal(terminalId);
    } catch (error) {
      console.error(`[terminal:kill] Error for ${terminalId}:`, error.message);
      return false;
    }
  });

  /**
   * 터미널 크기 조정 (PTY resize)
   */
  ipcMain.handle('terminal:resize', async (_, terminalId, cols, rows) => {
    try {
      if (_remoteTerminals.has(terminalId)) {
        const router = _getRouter();
        const bridge = router && router.getTermBridge && router.getTermBridge();
        if (bridge && typeof bridge.resize === 'function') {
          return bridge.resize(terminalId, { cols, rows });
        }
        return false;
      }
      return processManager.resizeTerminal(terminalId, cols, rows);
    } catch (error) {
      console.error(
        `[terminal:resize] Error for ${terminalId} (${cols}x${rows}):`,
        error.message
      );
      return false;
    }
  });
}

module.exports = { registerTerminalHandlers };
