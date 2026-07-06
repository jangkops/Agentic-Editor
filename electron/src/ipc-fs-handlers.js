/**
 * File System IPC Handlers
 * 책임: 파일 읽기, 쓰기, 디렉터리 조작 등 fs 관련 모든 IPC
 * Remote routing: sessionRouter가 원격 활성 상태면 SFTP 브리지 경유.
 */

const { ipcMain, dialog } = require('electron');
const fs = require('fs');
const path = require('path');

// Lazy-require so fs handlers work even if remote module has issues.
function _getRouter() {
  try { return require('./remote/session-router'); } catch { return null; }
}
function _remoteBridge() {
  const router = _getRouter();
  if (!router) { console.error('[_remoteBridge] router not loaded'); return null; }
  if (!router.isRemote) { console.log('[_remoteBridge] isRemote=false, active=', router.getActive && router.getActive()); return null; }
  const bridge = router.getFileBridge();
  if (!bridge) { console.error('[_remoteBridge] isRemote=true but getFileBridge()=null'); }
  return bridge;
}

/**
 * FS IPC 핸들러 등록
 * @param {BrowserWindow} mainWindow - 다이얼로그 부모 윈도우
 */
function registerFsHandlers(mainWindow) {
  /**
   * 폴더 열기 다이얼로그
   */
  ipcMain.handle('openFolder', async () => {
    const result = await dialog.showOpenDialog(mainWindow, {
      properties: ['openDirectory'],
    });
    return result.canceled ? null : result.filePaths[0];
  });

  /**
   * 파일 열기 다이얼로그
   * 선택적 opts.filters 지원 — 인자가 없으면 기존 동작과 동일 (하위호환).
   * 예: openFile({ filters: [{ name: 'PowerPoint', extensions: ['pptx'] }] })
   * (요구사항 8.3, design §구성요소 8 — Template_Upload_Control의 .pptx 필터)
   * @param {IpcMainInvokeEvent} _evt - IPC 이벤트 (사용 안 함)
   * @param {{filters?: Array}} [opts] - 선택적 다이얼로그 옵션
   * @returns {string|null} 선택된 파일 경로 또는 취소 시 null
   */
  ipcMain.handle('fs:open-file', async (_evt, opts) => {
    const properties = ['openFile'];
    const filters = (opts && Array.isArray(opts.filters)) ? opts.filters : [];
    const result = await dialog.showOpenDialog(mainWindow, { properties, filters });
    return result.canceled ? null : result.filePaths[0];
  });

  /**
   * 파일 읽기
   * @param {IpcMainInvokeEvent} _ - IPC 이벤트 (사용 안 함)
   * @param {string} filePath - 읽을 파일 경로
   * @returns {string|null} 파일 내용 또는 null
   */
  ipcMain.handle('fs:read-file', async (_, filePath) => {
    try {
      const bridge = _remoteBridge();
      if (bridge) {
        console.log('[fs:read-file] using remote bridge for:', filePath);
        return await bridge.read(filePath, 'utf8');
      }
      return fs.readFileSync(filePath, 'utf-8');
    } catch (error) {
      console.error(`[fs:read-file] Failed to read ${filePath}:`, error.message);
      return null;
    }
  });

  /**
   * 파일 쓰기 (부모 디렉터리 자동 생성)
   * @param {IpcMainInvokeEvent} _ - IPC 이벤트
   * @param {string} filePath - 쓸 파일 경로
   * @param {string} content - 파일 내용
   * @returns {boolean} 성공 여부
   */
  ipcMain.handle('fs:write-file', async (_, filePath, content) => {
    try {
      const bridge = _remoteBridge();
      if (bridge) { await bridge.write(filePath, content); return true; }
      fs.mkdirSync(path.dirname(filePath), { recursive: true });
      fs.writeFileSync(filePath, content, 'utf-8');
      return true;
    } catch (error) {
      console.error(`[fs:write-file] Failed to write ${filePath}:`, error.message);
      return false;
    }
  });

  /**
   * 파일/폴더 이름 변경
   * Remote: bridge.rename(src, dst) via SFTP (Req 6.1).
   * @param {IpcMainInvokeEvent} _ - IPC 이벤트
   * @param {string} oldPath - 현재 경로
   * @param {string} newPath - 변경할 경로
   * @returns {boolean} 성공 여부
   */
  ipcMain.handle('fs:rename', async (_, oldPath, newPath) => {
    try {
      const bridge = _remoteBridge();
      if (bridge) { await bridge.rename(oldPath, newPath); return true; }
      fs.renameSync(oldPath, newPath);
      return true;
    } catch (error) {
      console.error(`[fs:rename] Failed to rename ${oldPath}:`, error.message);
      return false;
    }
  });

  /**
   * 파일 삭제
   * Used by file-preview-panel delete button.
   * @param {string} filePath - 삭제할 파일 경로
   * @returns {boolean} 성공 여부
   */
  ipcMain.handle('fs:delete-file', async (_, filePath) => {
    try {
      const bridge = _remoteBridge();
      if (bridge) {
        if (typeof bridge.unlink === 'function') {
          await bridge.unlink(filePath);
          return true;
        }
        // Bridge older API doesn't expose unlink — fall back to local unlink
        // for files that exist locally (most .generated/ files do).
      }
      if (fs.existsSync(filePath)) {
        fs.unlinkSync(filePath);
      }
      return true;
    } catch (error) {
      console.error(`[fs:delete-file] Failed to delete ${filePath}:`, error.message);
      throw new Error(error.message);
    }
  });

  /**
   * 디렉터리 생성 (재귀)
   * Remote: bridge.mkdir(path, {recursive: true}) via SFTP (Req 6.1).
   * @param {IpcMainInvokeEvent} _ - IPC 이벤트
   * @param {string} dirPath - 생성할 디렉터리 경로
   * @returns {boolean} 성공 여부
   */
  ipcMain.handle('fs:mkdir', async (_, dirPath) => {
    try {
      const bridge = _remoteBridge();
      if (bridge) { await bridge.mkdir(dirPath, { recursive: true }); return true; }
      fs.mkdirSync(dirPath, { recursive: true });
      return true;
    } catch (error) {
      console.error(`[fs:mkdir] Failed to create ${dirPath}:`, error.message);
      return false;
    }
  });

  /**
   * 디렉터리 내 파일/폴더 목록
   * @param {IpcMainInvokeEvent} _ - IPC 이벤트
   * @param {string} dirPath - 조회할 디렉터리 경로
   * @returns {Array} [{name, path, isDirectory}] 배열
   */
  ipcMain.handle('fs:list-files', async (_, dirPath) => {
    try {
      const bridge = _remoteBridge();
      if (bridge) {
        const list = await bridge.list(dirPath);
        return list.map(e => ({ name: e.name, path: e.path, isDirectory: !!e.isDirectory }));
      }
      const entries = fs.readdirSync(dirPath, { withFileTypes: true });
      return entries.map((entry) => ({
        name: entry.name,
        path: path.join(dirPath, entry.name),
        isDirectory: entry.isDirectory(),
      }));
    } catch (error) {
      console.error(`[fs:list-files] Failed to list ${dirPath}:`, error.message);
      return [];
    }
  });

  /**
   * 사용자 데이터 경로 반환
   */
  ipcMain.handle('fs:get-user-data-path', () => {
    const { app } = require('electron');
    return app.getPath('userData');
  });

  // === Media file preview support (watch + save dialog + read binary) ===
  const _watchers = new Map();

  /**
   * Watch a directory and notify renderer of changes.
   * Used by file-preview-panel to auto-refresh .generated/ list.
   */
  ipcMain.handle('fs:watch-directory', async (_, dirPath) => {
    try {
      if (_watchers.has(dirPath)) return { ok: true, alreadyWatching: true };
      if (!fs.existsSync(dirPath)) {
        try { fs.mkdirSync(dirPath, { recursive: true }); } catch {}
      }
      let timer = null;
      const watcher = fs.watch(dirPath, { persistent: false }, (event, filename) => {
        // Debounce 300ms — multiple writes for one save
        if (timer) clearTimeout(timer);
        timer = setTimeout(() => {
          try {
            if (mainWindow && !mainWindow.isDestroyed()) {
              mainWindow.webContents.send('fs:directory-changed', { dirPath, event, filename });
            }
          } catch {}
        }, 300);
      });
      _watchers.set(dirPath, watcher);
      return { ok: true };
    } catch (err) {
      return { ok: false, error: err && err.message };
    }
  });

  ipcMain.handle('fs:unwatch-directory', async (_, dirPath) => {
    try {
      const w = _watchers.get(dirPath);
      if (w) { try { w.close(); } catch {} _watchers.delete(dirPath); }
      return { ok: true };
    } catch (err) {
      return { ok: false, error: err && err.message };
    }
  });

  /**
   * Show save dialog and copy file to chosen location.
   * Used by file-preview-panel download button.
   */
  ipcMain.handle('fs:show-save-dialog', async (_, opts) => {
    try {
      const options = opts || {};
      // TASK 8 — sourcePath 사전 검증.
      // file-preview-panel이 표시한 path가 실제 디스크에 없거나 0바이트인 경우
      // 사용자가 다운로드를 눌러도 빈 파일이 복사되거나 ENOENT로 실패한다.
      // 다이얼로그 띄우기 전에 분명한 에러 반환해 사용자가 원인을 알게 한다.
      if (options.sourcePath && !options.remote) {
        try {
          const st = fs.statSync(options.sourcePath);
          if (!st.isFile()) {
            return { ok: false, error: `소스가 파일이 아닙니다: ${options.sourcePath}` };
          }
          if (st.size <= 0) {
            return { ok: false, error: `빈 파일입니다 (0 bytes): ${options.sourcePath}` };
          }
        } catch (statErr) {
          return {
            ok: false,
            error: `파일을 찾을 수 없습니다: ${options.sourcePath} (${statErr.code || statErr.message || 'ENOENT'})`,
          };
        }
      }
      const result = await dialog.showSaveDialog(mainWindow, {
        defaultPath: options.defaultPath || '',
        filters: options.filters || [],
      });
      if (result.canceled || !result.filePath) return { ok: false, canceled: true };
      if (options.sourcePath) {
        // Copy from source to chosen path
        if (options.remote) {
          const bridge = _remoteBridge();
          if (!bridge) return { ok: false, error: 'remote bridge not available' };
          // Read remote -> write local
          const buf = await bridge.read(options.sourcePath, 'binary');
          if (Buffer.isBuffer(buf)) fs.writeFileSync(result.filePath, buf);
          else fs.writeFileSync(result.filePath, buf);
        } else {
          fs.copyFileSync(options.sourcePath, result.filePath);
        }
      } else if (options.content !== undefined) {
        fs.writeFileSync(result.filePath, options.content);
      }
      return { ok: true, path: result.filePath };
    } catch (err) {
      return { ok: false, error: err && err.message };
    }
  });

  /**
   * Read file as base64 (for image/binary preview in renderer).
   */
  ipcMain.handle('fs:read-file-base64', async (_, filePath) => {
    try {
      const bridge = _remoteBridge();
      if (bridge) {
        const buf = await bridge.read(filePath, 'binary');
        return Buffer.isBuffer(buf) ? buf.toString('base64') : Buffer.from(buf).toString('base64');
      }
      return fs.readFileSync(filePath).toString('base64');
    } catch (err) {
      console.error(`[fs:read-file-base64] failed:`, err.message);
      return null;
    }
  });

  /**
   * List files with stats (size, mtime) — for file-preview-panel.
   */
  ipcMain.handle('fs:list-files-with-stats', async (_, dirPath) => {
    try {
      const bridge = _remoteBridge();
      if (bridge) {
        const entries = await bridge.list(dirPath);
        return entries.map(e => ({
          name: e.name,
          path: e.path,
          isDirectory: e.isDirectory,
          size: e.size || 0,
          mtime: e.mtime ? new Date(e.mtime * 1000).toISOString() : null,
        }));
      }
      if (!fs.existsSync(dirPath)) return [];
      const names = fs.readdirSync(dirPath);
      return names.map(n => {
        const full = require('path').join(dirPath, n);
        try {
          const st = fs.statSync(full);
          return {
            name: n,
            path: full,
            isDirectory: st.isDirectory(),
            size: st.size,
            mtime: st.mtime.toISOString(),
          };
        } catch {
          return { name: n, path: full, isDirectory: false, size: 0, mtime: null };
        }
      });
    } catch (err) {
      console.error(`[fs:list-files-with-stats] failed:`, err.message);
      return [];
    }
  });

  /**
   * Local-only variant — bypasses remote SFTP bridge so file-preview-panel can
   * see files written to the workstation's _resolve_local_root() (e.g.
   * ~/.agentic-editor/.generated/) even while a remote SSH session is active.
   *
   * Without this, the panel passed the local path through fs:list-files-with-stats
   * which routes via SFTP — the remote host has no such directory, returning [].
   * Result: user sees "no generated files" while the agent reports 5 verified files.
   */
  ipcMain.handle('fs:list-files-with-stats-local', async (_, dirPath) => {
    try {
      if (!fs.existsSync(dirPath)) return [];
      const names = fs.readdirSync(dirPath);
      return names.map(n => {
        const full = require('path').join(dirPath, n);
        try {
          const st = fs.statSync(full);
          return {
            name: n,
            path: full,
            isDirectory: st.isDirectory(),
            size: st.size,
            mtime: st.mtime.toISOString(),
          };
        } catch {
          return { name: n, path: full, isDirectory: false, size: 0, mtime: null };
        }
      });
    } catch (err) {
      console.error(`[fs:list-files-with-stats-local] failed:`, err.message);
      return [];
    }
  });

  /**
   * Local-only file read (binary base64) — same rationale as the local list
   * variant above. Used by file-preview-panel for thumbnails of files saved
   * to the workstation's local root while a remote session is active.
   */
  ipcMain.handle('fs:read-file-base64-local', async (_, filePath) => {
    try {
      if (!fs.existsSync(filePath)) return null;
      return fs.readFileSync(filePath).toString('base64');
    } catch (err) {
      console.error(`[fs:read-file-base64-local] failed:`, err.message);
      return null;
    }
  });

  /**
   * Local-only file delete — same rationale as the local list/read variants.
   * file-preview-panel reads the workstation's local .generated/ via
   * fs:list-files-with-stats-local, but the generic fs:delete-file routes
   * through the SFTP bridge when a remote SSH session is active — so a local
   * file silently fails to delete (bridge has no such path). This local-only
   * variant always uses fs.unlinkSync on the local filesystem.
   * (이슈 3: 삭제 경로 불일치 수정)
   * @param {string} filePath - 삭제할 로컬 파일 경로
   * @returns {boolean} 성공 여부
   */
  ipcMain.handle('fs:delete-file-local', async (_, filePath) => {
    try {
      if (fs.existsSync(filePath)) {
        fs.unlinkSync(filePath);
      }
      return true;
    } catch (error) {
      console.error(`[fs:delete-file-local] Failed to delete ${filePath}:`, error.message);
      throw new Error(error.message);
    }
  });

  /**
   * Show file/folder in OS file explorer (Finder/Explorer).
   */
  ipcMain.handle('fs:show-item-in-folder', async (_, filePath) => {
    try {
      const { shell } = require('electron');
      shell.showItemInFolder(filePath);
      return { ok: true };
    } catch (err) {
      return { ok: false, error: err && err.message };
    }
  });

  /**
   * Open a path (file or folder) with the OS default handler.
   */
  ipcMain.handle('fs:open-path', async (_, targetPath) => {
    try {
      const { shell } = require('electron');
      await shell.openPath(targetPath);
      return { ok: true };
    } catch (err) {
      return { ok: false, error: err && err.message };
    }
  });
}

module.exports = { registerFsHandlers };
