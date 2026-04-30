/**
 * Terminal IPC Handlers
 * 책임: 터미널 세션 생성, 데이터 쓰기, 종료, 크기 조정
 */

const { ipcMain } = require('electron');

/**
 * Terminal IPC 핸들러 등록
 * @param {ProcessManager} processManager - 프로세스 관리자 인스턴스
 */
function registerTerminalHandlers(processManager) {
  /**
   * 터미널 세션 생성
   * @param {IpcMainInvokeEvent} _ - IPC 이벤트
   * @param {string} terminalId - 터미널 고유 ID
   * @param {BrowserWindow} mainWindow - 메인 윈도우 (PTY 이벤트 발신)
   * @returns {boolean} 생성 성공 여부
   */
  ipcMain.handle('terminal:create', async (_, terminalId, mainWindow) => {
    try {
      return processManager.createTerminal(terminalId, mainWindow);
    } catch (error) {
      console.error(`[terminal:create] Error for ${terminalId}:`, error.message);
      return false;
    }
  });

  /**
   * 터미널에 데이터 쓰기
   * @param {IpcMainInvokeEvent} _ - IPC 이벤트
   * @param {string} terminalId - 터미널 ID
   * @param {string} data - 쓸 데이터
   * @returns {boolean} 성공 여부
   */
  ipcMain.handle('terminal:write', async (_, terminalId, data) => {
    try {
      return processManager.writeTerminal(terminalId, data);
    } catch (error) {
      console.error(`[terminal:write] Error for ${terminalId}:`, error.message);
      return false;
    }
  });

  /**
   * 터미널 종료
   * @param {IpcMainInvokeEvent} _ - IPC 이벤트
   * @param {string} terminalId - 터미널 ID
   * @returns {boolean} 성공 여부
   */
  ipcMain.handle('terminal:kill', async (_, terminalId) => {
    try {
      return processManager.killTerminal(terminalId);
    } catch (error) {
      console.error(`[terminal:kill] Error for ${terminalId}:`, error.message);
      return false;
    }
  });

  /**
   * 터미널 크기 조정 (PTY resize)
   * @param {IpcMainInvokeEvent} _ - IPC 이벤트
   * @param {string} terminalId - 터미널 ID
   * @param {number} cols - 컬럼 수
   * @param {number} rows - 행 수
   * @returns {boolean} 성공 여부
   */
  ipcMain.handle('terminal:resize', async (_, terminalId, cols, rows) => {
    try {
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
