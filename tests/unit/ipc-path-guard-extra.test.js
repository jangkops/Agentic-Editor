// 경로를 받는 다른 IPC(project:analyze / project:dependencies / slides:render-html-to-png)도
// fs IPC 와 같은 path-guard 를 거치는지 검증한다.
const fs = require('fs');
const os = require('os');
const path = require('path');
const { ipcMain } = require('electron');
const guard = require('../../electron/src/path-guard');
const { registerProjectHandlers } = require('../../electron/src/ipc-project-handlers');
const { registerSlidesHandlers } = require('../../electron/src/ipc-slides-handler');

function handler(channel) {
  const entry = ipcMain.handle.mock.calls.find(([ch]) => ch === channel);
  if (!entry) throw new Error(`handler not registered: ${channel}`);
  return entry[1];
}

let base, project, outside;
beforeAll(() => { registerProjectHandlers(); registerSlidesHandlers(null); });
beforeEach(() => {
  guard._reset(); guard._setStaticRootsForTest([]); delete process.env.AE_FS_GUARD;
  base = fs.mkdtempSync(path.join(os.tmpdir(), 'ae-guard-x-'));
  project = path.join(base, 'proj'); outside = path.join(base, 'outside');
  fs.mkdirSync(project); fs.mkdirSync(outside);
  fs.writeFileSync(path.join(project, 'a.js'), 'const x = 1; // TODO\n');
  fs.writeFileSync(path.join(project, 'package.json'), JSON.stringify({ dependencies: { left: '1.0.0' } }));
  fs.writeFileSync(path.join(outside, 'b.js'), 'x\n');
  jest.spyOn(console, 'error').mockImplementation(() => {});
});
afterEach(() => {
  fs.rmSync(base, { recursive: true, force: true });
  guard._reset(); guard._setStaticRootsForTest(null); console.error.mockRestore();
});

test('project:analyze / project:dependencies refuse folders the user never opened', async () => {
  expect(await handler('project:analyze')(null, outside)).toBeNull();
  expect(await handler('project:dependencies')(null, outside)).toBeNull();
  guard.allowRoot(project);
  const stats = await handler('project:analyze')(null, project);
  expect(stats && stats.totalFiles).toBe(2);
  const deps = await handler('project:dependencies')(null, project);
  expect(deps && deps.production).toEqual({ left: '1.0.0' });
  expect(await handler('project:analyze')(null, outside)).toBeNull();
});

test('slides:render-html-to-png refuses an output path outside the allowed roots before rendering', async () => {
  const r = await handler('slides:render-html-to-png')(null, { html: '<b>x</b>', outputPath: path.join(outside, 'out.png') });
  expect(r).toEqual({ ok: false, error: expect.stringMatching(/outside/) });
  expect(fs.existsSync(path.join(outside, 'out.png'))).toBe(false);
});
