// path-guard: 대화상자로 연 폴더·파일과 앱 데이터 루트 밖의 로컬 경로 접근을 거부한다.
const fs = require('fs');
const os = require('os');
const path = require('path');
const guard = require('../../electron/src/path-guard');

let base, rootA, rootB;
beforeEach(() => {
  guard._reset();
  guard._setStaticRootsForTest([]);          // 정적 루트(userData/tmpdir…)를 비워 격리 검증
  delete process.env.AE_FS_GUARD;
  base = fs.mkdtempSync(path.join(os.tmpdir(), 'ae-guard-'));
  rootA = path.join(base, 'projectA'); rootB = path.join(base, 'other');
  fs.mkdirSync(rootA); fs.mkdirSync(rootB);
  fs.writeFileSync(path.join(rootA, 'a.txt'), 'A');
  fs.writeFileSync(path.join(rootB, 'b.txt'), 'B');
});
afterEach(() => { fs.rmSync(base, { recursive: true, force: true }); guard._reset(); guard._setStaticRootsForTest(null); delete process.env.AE_FS_GUARD; });

describe('path-guard', () => {
  test('nothing is allowed before a folder is opened', () => {
    expect(guard.isAllowed(path.join(rootA, 'a.txt'))).toBe(false);
    expect(() => guard.assertAllowed(path.join(rootA, 'a.txt'), 'read')).toThrow(/fs-path-not-allowed/);
  });
  test('opened folder allows its files, new files and subfolders — not siblings', () => {
    guard.allowRoot(rootA);
    expect(guard.isAllowed(path.join(rootA, 'a.txt'))).toBe(true);
    expect(guard.isAllowed(path.join(rootA, 'new', 'deep', 'file.md'))).toBe(true);
    expect(guard.isAllowed(rootA)).toBe(true);
    expect(guard.isAllowed(path.join(rootB, 'b.txt'))).toBe(false);
    expect(guard.isAllowed(path.join(rootA, '..', 'other', 'b.txt'))).toBe(false);   // traversal
  });
  test('a symlink inside the opened folder cannot escape it', () => {
    guard.allowRoot(rootA);
    fs.symlinkSync(path.join(rootB, 'b.txt'), path.join(rootA, 'link.txt'));
    expect(guard.isAllowed(path.join(rootA, 'link.txt'))).toBe(false);
  });
  test('a file picked in a dialog is allowed exactly, not its neighbours', () => {
    guard.allowFile(path.join(rootB, 'b.txt'));
    expect(guard.isAllowed(path.join(rootB, 'b.txt'))).toBe(true);
    expect(guard.isAllowed(path.join(rootB, 'c.txt'))).toBe(false);
  });
  test('static app roots are allowed', () => {
    guard._setStaticRootsForTest([rootB]);
    expect(guard.isAllowed(path.join(rootB, 'anything.json'))).toBe(true);
  });
  test('AE_FS_GUARD=0 disables the guard (escape hatch)', () => {
    process.env.AE_FS_GUARD = '0';
    expect(guard.isAllowed('/etc/hosts')).toBe(true);
  });
  test('non-string input is denied', () => {
    guard.allowRoot(rootA);
    expect(guard.isAllowed(undefined)).toBe(false);
    expect(guard.isAllowed('')).toBe(false);
  });
});
