/**
 * Window Manager — Electron 창 생명주기 관리
 * 책임: 창 생성, 전개, 최소화, 종료 등
 */

const { app, BrowserWindow } = require('electron');
const path = require('path');

class WindowManager {
  constructor() {
    this.mainWindow = null;
  }

  /**
   * 메인 윈도우 생성
   * @returns {BrowserWindow} mainWindow
   */
  createWindow() {
    this.mainWindow = new BrowserWindow({
      width: 1500,
      height: 900,
      minWidth: 1000,
      minHeight: 600,
      titleBarStyle: 'hiddenInset',
      backgroundColor: '#1a1a1a',
      title: 'AI 에디터',
      webPreferences: {
        preload: path.join(__dirname, '..', 'preload.js'),
        contextIsolation: true,
        nodeIntegration: false,
      },
    });

    // HTML 로드
    const htmlPath = path.join(__dirname, 'index.html');
    this.mainWindow.loadFile(htmlPath);

    // 개발 모드: DevTools 열기 (선택사항)
    if (process.env.NODE_ENV === 'development' && process.argv.includes('--devtools')) {
      this.mainWindow.webContents.openDevTools();
    }

    // 창 닫기 이벤트
    this.mainWindow.on('closed', () => {
      this.mainWindow = null;
    });

    return this.mainWindow;
  }

  /**
   * 메인 윈도우 가져오기
   * @returns {BrowserWindow|null}
   */
  getMainWindow() {
    return this.mainWindow;
  }

  /**
   * 모든 윈도우가 닫혔는지 확인
   * @returns {boolean}
   */
  allWindowsClosed() {
    return BrowserWindow.getAllWindows().length === 0;
  }

  /**
   * 기존 윈도우 또는 새로운 윈도우 생성 (activate 시)
   */
  handleActivate() {
    if (this.allWindowsClosed()) {
      this.createWindow();
    }
  }

  /**
   * 윈도우 보이기/숨기기
   * @param {boolean} show - true면 보이고, false면 숨김
   */
  setWindowVisible(show) {
    if (this.mainWindow) {
      if (show) {
        this.mainWindow.show();
      } else {
        this.mainWindow.hide();
      }
    }
  }

  /**
   * 윈도우 minimize
   */
  minimizeWindow() {
    if (this.mainWindow && !this.mainWindow.isMinimized()) {
      this.mainWindow.minimize();
    }
  }

  /**
   * 윈도우 maximize
   */
  maximizeWindow() {
    if (this.mainWindow) {
      if (this.mainWindow.isMaximized()) {
        this.mainWindow.unmaximize();
      } else {
        this.mainWindow.maximize();
      }
    }
  }

  /**
   * 윈도우 크기 리셋
   */
  resetWindowSize() {
    if (this.mainWindow) {
      this.mainWindow.setSize(1500, 900);
      this.mainWindow.center();
    }
  }
}

module.exports = { WindowManager };
