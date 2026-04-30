/**
 * File System IPC Handlers
 * 책임: 파일 읽기, 쓰기, 디렉터리 조작 등 fs 관련 모든 IPC
 */

const { ipcMain, dialog } = require('electron');
const fs = require('fs');
const path = require('path');

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
   */
  ipcMain.handle('fs:open-file', async () => {
    const result = await dialog.showOpenDialog(mainWindow, {
      properties: ['openFile'],
    });
    return result.canceled ? null : result.filePaths[0];
  });

  /**
   * 파일 읽기
   * @param {IpcMainInvokeEvent} _ - IPC 이벤트 (사용 안 함)
   * @param {string} filePath - 읽을 파일 경로
   * @returns {string|null} 파일 내용 또는 null
   */
  ipcMain.handle('fs:read-file', (_, filePath) => {
    try {
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
  ipcMain.handle('fs:write-file', (_, filePath, content) => {
    try {
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
   * @param {IpcMainInvokeEvent} _ - IPC 이벤트
   * @param {string} oldPath - 현재 경로
   * @param {string} newPath - 변경할 경로
   * @returns {boolean} 성공 여부
   */
  ipcMain.handle('fs:rename', (_, oldPath, newPath) => {
    try {
      fs.renameSync(oldPath, newPath);
      return true;
    } catch (error) {
      console.error(`[fs:rename] Failed to rename ${oldPath}:`, error.message);
      return false;
    }
  });

  /**
   * 디렉터리 생성 (재귀)
   * @param {IpcMainInvokeEvent} _ - IPC 이벤트
   * @param {string} dirPath - 생성할 디렉터리 경로
   * @returns {boolean} 성공 여부
   */
  ipcMain.handle('fs:mkdir', (_, dirPath) => {
    try {
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
  ipcMain.handle('fs:list-files', (_, dirPath) => {
    try {
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
}

module.exports = { registerFsHandlers };
