/**
 * Test: Window Manager
 * Electron 창 관리 기능 테스트
 */

const { WindowManager } = require('../../electron/src/window-manager');

describe('WindowManager', () => {
  let windowManager;

  beforeEach(() => {
    // WindowManager 인스턴스 생성
    windowManager = new WindowManager();
  });

  afterEach(() => {
    // 정리
  });

  test('WindowManager 초기화', () => {
    expect(windowManager).toBeDefined();
    expect(windowManager.mainWindow).toBeNull();
  });

  test('getMainWindow 반환 null (창 생성 전)', () => {
    const window = windowManager.getMainWindow();
    expect(window).toBeNull();
  });

  test('allWindowsClosed 확인', () => {
    // BrowserWindow.getAllWindows()가 mocked되지 않아 실제 값 반환
    const result = windowManager.allWindowsClosed();
    expect(typeof result).toBe('boolean');
  });

  test('setWindowVisible 메서드 존재', () => {
    expect(typeof windowManager.setWindowVisible).toBe('function');
  });

  test('minimizeWindow 메서드 존재', () => {
    expect(typeof windowManager.minimizeWindow).toBe('function');
  });

  test('maximizeWindow 메서드 존재', () => {
    expect(typeof windowManager.maximizeWindow).toBe('function');
  });

  test('resetWindowSize 메서드 존재', () => {
    expect(typeof windowManager.resetWindowSize).toBe('function');
  });
});
