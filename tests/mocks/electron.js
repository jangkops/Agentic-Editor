/**
 * Mock for Electron module (testing purposes)
 */

const mockBrowserWindow = {
  getAllWindows: jest.fn(() => []),
  constructor: jest.fn(() => ({})),
};

const mockApp = {
  whenReady: jest.fn(() => Promise.resolve()),
  on: jest.fn(),
  quit: jest.fn(),
  getPath: jest.fn((path) => `/mock/${path}`),
};

const mockIpcMain = {
  handle: jest.fn(),
  on: jest.fn(),
  invoke: jest.fn(),
};

const mockDialog = {
  showOpenDialog: jest.fn(() => Promise.resolve({ canceled: false, filePaths: ['/test/path'] })),
};

// safeStorage — OS 키체인 암호화. 실제 구현처럼 "평문이 산출물에 남지 않는" 성질을
// 유지하도록 hex 인코딩을 쓴다(가역이지만 평문 바이트가 그대로 보이지는 않는다).
// 테스트가 암호화 불가 환경을 재현할 수 있게 __setAvailable 을 노출한다.
let _encryptionAvailable = true;
const mockSafeStorage = {
  isEncryptionAvailable: jest.fn(() => _encryptionAvailable),
  encryptString: jest.fn((s) =>
    Buffer.from('ENCv1:' + Buffer.from(String(s), 'utf-8').toString('hex'), 'utf-8')),
  decryptString: jest.fn((buf) => {
    const raw = Buffer.from(buf).toString('utf-8');
    if (!raw.startsWith('ENCv1:')) throw new Error('cannot decrypt');
    return Buffer.from(raw.slice('ENCv1:'.length), 'hex').toString('utf-8');
  }),
  __setAvailable: (v) => { _encryptionAvailable = !!v; },
};

module.exports = {
  app: mockApp,
  BrowserWindow: mockBrowserWindow,
  ipcMain: mockIpcMain,
  dialog: mockDialog,
  safeStorage: mockSafeStorage,
};
