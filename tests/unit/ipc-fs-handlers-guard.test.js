// fs IPC 핸들러가 path-guard 를 통과한 경로만 로컬 파일시스템에 접근하는지 검증한다.
const fs = require('fs');
const os = require('os');
const path = require('path');
const { ipcMain, dialog } = require('electron');
const guard = require('../../electron/src/path-guard');
const { registerFsHandlers } = require('../../electron/src/ipc-fs-handlers');

function handler(channel) {
  const entry = ipcMain.handle.mock.calls.find(([ch]) => ch === channel);
  if (!entry) throw new Error(`handler not registered: ${channel}`);
  return entry[1];
}

let base, project, outside;
beforeAll(() => { registerFsHandlers({ isDestroyed: () => false, webContents: { send: () => {} } }); });
beforeEach(() => {
  guard._reset(); guard._setStaticRootsForTest([]); delete process.env.AE_FS_GUARD;
  base = fs.mkdtempSync(path.join(os.tmpdir(), 'ae-fsguard-'));
  project = path.join(base, 'proj'); outside = path.join(base, 'outside');
  fs.mkdirSync(project); fs.mkdirSync(outside);
  fs.writeFileSync(path.join(project, 'in.txt'), 'inside');
  fs.writeFileSync(path.join(outside, 'secret.txt'), 'secret');
  jest.spyOn(console, 'error').mockImplementation(() => {});
});
afterEach(() => { fs.rmSync(base, { recursive: true, force: true }); guard._reset(); guard._setStaticRootsForTest(null); console.error.mockRestore(); });

describe('fs IPC + path-guard', () => {
  test('reads outside any opened folder are denied (null), reads inside are served after openFolder', async () => {
    expect(await handler('fs:read-file')(null, path.join(outside, 'secret.txt'))).toBeNull();
    dialog.showOpenDialog.mockResolvedValueOnce({ canceled: false, filePaths: [project] });
    expect(await handler('openFolder')(null)).toBe(project);
    expect(await handler('fs:read-file')(null, path.join(project, 'in.txt'))).toBe('inside');
    expect(await handler('fs:read-file')(null, path.join(outside, 'secret.txt'))).toBeNull();
  });
  test('writes/deletes/lists outside are refused, inside succeed', async () => {
    dialog.showOpenDialog.mockResolvedValueOnce({ canceled: false, filePaths: [project] });
    await handler('openFolder')(null);
    expect(await handler('fs:write-file')(null, path.join(outside, 'x.txt'), 'x')).toBe(false);
    expect(fs.existsSync(path.join(outside, 'x.txt'))).toBe(false);
    expect(await handler('fs:write-file')(null, path.join(project, 'sub', 'x.txt'), 'x')).toBe(true);
    expect(fs.readFileSync(path.join(project, 'sub', 'x.txt'), 'utf8')).toBe('x');
    expect(await handler('fs:list-files')(null, outside)).toEqual([]);
    expect((await handler('fs:list-files')(null, project)).map((e) => e.name).sort()).toEqual(['in.txt', 'sub']);
    await expect(handler('fs:delete-file')(null, path.join(outside, 'secret.txt'))).rejects.toThrow(/fs-path-not-allowed/);
    expect(fs.existsSync(path.join(outside, 'secret.txt'))).toBe(true);
  });
  test('a file picked in the open-file dialog is readable even outside the project', async () => {
    dialog.showOpenDialog.mockResolvedValueOnce({ canceled: false, filePaths: [path.join(outside, 'secret.txt')] });
    expect(await handler('fs:open-file')(null, {})).toBe(path.join(outside, 'secret.txt'));
    expect(await handler('fs:read-file-base64')(null, path.join(outside, 'secret.txt'))).toBe(Buffer.from('secret').toString('base64'));
  });
});
