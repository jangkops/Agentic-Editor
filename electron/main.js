/**
 * Electron Main Process Entry Point
 * 책임: 앱 초기화, 윈도우 생성, IPC 라우팅
 *
 * 기존 3,883줄 main.js를 모듈화한 버전
 * 각 IPC 카테고리를 별도 파일로 분리
 */

const { app, ipcMain } = require('electron');
const path = require('path');

// Core managers
const { ProcessManager } = require('./core/process-manager');
const { DataStore } = require('./core/data-store');
const { AwsSsoManager } = require('./core/aws-sso-manager');

// Window management
const { WindowManager } = require('./src/window-manager');

// IPC handlers (modularized)
const { registerFsHandlers } = require('./src/ipc-fs-handlers');
const { registerStoreHandlers } = require('./src/ipc-store-handlers');
const { registerSsoHandlers } = require('./src/ipc-sso-handlers');
const { registerTerminalHandlers } = require('./src/ipc-terminal-handlers');
const { registerProjectHandlers } = require('./src/ipc-project-handlers');
const { registerGitHandlers } = require('./src/ipc-git-handlers');

// ========================================
// Initialization
// ========================================

const windowManager = new WindowManager();
const processManager = new ProcessManager();
const dataStore = new DataStore();
const ssoManager = new AwsSsoManager();

// ========================================
// App Lifecycle
// ========================================

/**
 * 앱 준비 완료 시
 */
app.whenReady().then(() => {
  // 윈도우 생성
  windowManager.createWindow();

  // Python 백엔드 시작 (개발 모드 확인)
  const isDev =
    process.argv.includes('--dev') ||
    process.env.NODE_ENV === 'development' ||
    process.env.npm_lifecycle_event === 'dev:electron';

  if (!isDev) {
    // 포트 확인: 이미 실행 중인지 확인
    const http = require('http');
    const checkReq = http.request(
      {
        host: '127.0.0.1',
        port: 8765,
        method: 'HEAD',
        path: '/health',
        timeout: 2000,
      },
      (res) => {
        console.log('[ProcessManager] Python backend already running, skipping start');
      }
    );

    checkReq.on('error', () => {
      console.log('[ProcessManager] Starting Python backend...');
      processManager.startPython();
    });

    checkReq.end();
  } else {
    console.log('[ProcessManager] Dev mode — skipping Python start (dev:python handles it)');
  }
});

/**
 * 모든 윈도우 닫혔을 때
 */
app.on('window-all-closed', () => {
  processManager.stopAll();
  if (process.platform !== 'darwin') {
    app.quit();
  }
});

/**
 * 앱 종료 전
 */
app.on('before-quit', () => {
  processManager.stopAll();
});

/**
 * 앱 활성화 시 (macOS)
 */
app.on('activate', () => {
  if (windowManager.allWindowsClosed()) {
    windowManager.createWindow();
  }
});

// ========================================
// IPC Handlers Registration
// ========================================

/**
 * 모든 IPC 핸들러 등록
 */
function registerAllIpcHandlers() {
  const mainWindow = windowManager.getMainWindow();

  // File System
  registerFsHandlers(mainWindow);

  // Data Store (Settings, History, etc.)
  registerStoreHandlers(dataStore);

  // AWS SSO
  registerSsoHandlers(ssoManager);

  // Terminal
  registerTerminalHandlers(processManager);

  // Project Analysis
  registerProjectHandlers();

  // Git
  registerGitHandlers();

  console.log('[IPC] All handlers registered');
}

// IPC 핸들러 등록 (앱 준비 후)
app.whenReady().then(() => {
  registerAllIpcHandlers();
});

// ========================================
// Error Handling
// ========================================

/**
 * 처리되지 않은 예외
 */
process.on('uncaughtException', (error) => {
  console.error('[UNCAUGHT EXCEPTION]', error);
  // 로그 저장 또는 에러 리포팅 로직 추가 가능
});

/**
 * 처리되지 않은 Promise 거부
 */
process.on('unhandledRejection', (reason, promise) => {
  console.error('[UNHANDLED REJECTION]', reason, promise);
});

// ========================================
// Exports (테스트용)
// ========================================

module.exports = {
  windowManager,
  processManager,
  dataStore,
  ssoManager,
};
