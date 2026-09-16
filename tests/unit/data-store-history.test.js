// DataStore.saveHistory — 그날 이력 파일이 손상돼 있어도 덮어쓰기 전에 원본을 보존하고, 기록은 원자적이어야 한다(원장 #2).
const fs = require('fs');
const os = require('os');
const path = require('path');
const { DataStore } = require('../../electron/core/data-store');

let base;
beforeEach(() => { base = fs.mkdtempSync(path.join(os.tmpdir(), 'ae-ds-')); });
afterEach(() => { fs.rmSync(base, { recursive: true, force: true }); });

test('appends to an existing valid history file', () => {
  const ds = new DataStore(base);
  ds.saveHistory('2026-09-16', [{ role: 'user', content: 'a' }]);
  ds.saveHistory('2026-09-16', [{ role: 'assistant', content: 'b' }]);
  const data = JSON.parse(fs.readFileSync(path.join(base, 'history', '2026-09-16.json'), 'utf-8'));
  expect(data.map((m) => m.content)).toEqual(['a', 'b']);
  expect(fs.existsSync(path.join(base, 'history', '2026-09-16.json.tmp'))).toBe(false);
});

test('a corrupt history file is backed up beside itself instead of being silently replaced', () => {
  const ds = new DataStore(base);
  const p = path.join(base, 'history', '2026-09-16.json');
  fs.writeFileSync(p, '[{"role":"user","content":"old"', 'utf-8');     // 잘린 JSON
  ds.saveHistory('2026-09-16', [{ role: 'user', content: 'new' }]);
  const files = fs.readdirSync(path.join(base, 'history'));
  const backups = files.filter((f) => f.startsWith('2026-09-16.json.corrupt-'));
  expect(backups).toHaveLength(1);
  expect(fs.readFileSync(path.join(base, 'history', backups[0]), 'utf-8')).toContain('"old"');
  expect(JSON.parse(fs.readFileSync(p, 'utf-8'))).toEqual([{ role: 'user', content: 'new' }]);
});

test('a non-array JSON file is treated as empty but preserved semantics stay valid', () => {
  const ds = new DataStore(base);
  const p = path.join(base, 'history', '2026-09-17.json');
  fs.writeFileSync(p, '{"not":"an array"}', 'utf-8');
  ds.saveHistory('2026-09-17', [{ role: 'user', content: 'x' }]);
  expect(JSON.parse(fs.readFileSync(p, 'utf-8'))).toEqual([{ role: 'user', content: 'x' }]);
});
