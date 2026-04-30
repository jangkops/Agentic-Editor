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

module.exports = {
  app: mockApp,
  BrowserWindow: mockBrowserWindow,
  ipcMain: mockIpcMain,
  dialog: mockDialog,
};
