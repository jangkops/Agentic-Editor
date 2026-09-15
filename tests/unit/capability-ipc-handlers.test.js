/**
 * Task 14.3 — capability IPC 핸들러 등록과 preload 노출
 * Feature: gateway-models-effort-support
 * Validates: Requirements 10.7, 10.11, 10.15
 *
 * Runner: jest (repo "test:unit": "jest tests/unit/")
 *
 * 검증 범위:
 *   1) 채널 3개만 등록되고 채널명이 design.md와 정확히 일치
 *   2) `userData/capability/` 하위 고정 파일만 읽고 쓴다(경로 인자 미수용 → traversal 불가)
 *   3) Effort_Settings 실제 읽기·쓰기 왕복과 원자적 쓰기(임시 파일 잔존 없음)
 *   4) entry field 화이트리스트 — credential/authorization/cookie/signature는 저장되지 않음
 *   5) Known_Route·Value_Type 닫힌 집합 이탈 entry는 저장하지 않음
 *   6) Capability_Map은 읽기 전용이며 부재·손상 시 null(= 기준선 동작)
 *   7) preload는 위 3개 채널만 추가 노출하고 ipcRenderer는 노출하지 않음
 */

const fs = require('fs');
const os = require('os');
const path = require('path');

const { ipcMain } = require('electron'); // tests/mocks/electron.js (jest moduleNameMapper)
const { registerCapabilityHandlers, _internals } = require('../../electron/src/ipc-capability-handlers');

const CHANNELS = [
  'capability:load-effort-settings',
  'capability:save-effort-settings',
  'capability:load-map',
];

/** mock ipcMain.handle 등록분을 채널→핸들러 map으로 수집. */
function collectHandlers(dataStore) {
  ipcMain.handle.mockClear();
  registerCapabilityHandlers(dataStore);
  const map = {};
  for (const [channel, handler] of ipcMain.handle.mock.calls) map[channel] = handler;
  return map;
}

function readRaw(root, ...parts) {
  return JSON.parse(fs.readFileSync(path.join(root, 'capability', ...parts), 'utf-8'));
}

describe('capability IPC handlers', () => {
  let root;
  let dataStore;
  let handlers;

  beforeEach(() => {
    root = fs.mkdtempSync(path.join(os.tmpdir(), 'ae-capability-'));
    dataStore = { basePath: root };
    handlers = collectHandlers(dataStore);
  });

  afterEach(() => {
    fs.rmSync(root, { recursive: true, force: true });
  });

  test('3개 채널만 등록하고 채널명이 정확히 일치한다', () => {
    const registered = ipcMain.handle.mock.calls.map(([c]) => c);
    expect(registered.sort()).toEqual([...CHANNELS].sort());
    expect(registered).toHaveLength(3);
  });

  test('경로는 userData/capability/ 하위 고정 파일로만 해석된다', () => {
    const settingsPath = _internals._capabilityFilePath(dataStore, _internals.EFFORT_SETTINGS_FILENAME);
    const mapPath = _internals._capabilityFilePath(dataStore, _internals.CAPABILITY_MAP_FILENAME);

    expect(settingsPath).toBe(path.join(root, 'capability', 'effort_settings.json'));
    expect(mapPath).toBe(path.join(root, 'capability', 'capability_map.json'));

    // 허용 목록 밖 파일명(경로 탈출 시도 포함)은 예외로 거부한다.
    for (const bad of ['../../settings/settings.json', '/etc/passwd', 'settings.json', '..']) {
      expect(() => _internals._capabilityFilePath(dataStore, bad)).toThrow();
    }
  });

  test('파일이 없으면 빈 Effort_Settings를 반환한다', async () => {
    const loaded = await handlers['capability:load-effort-settings']({});
    expect(loaded).toEqual({ schemaVersion: _internals.SCHEMA_VERSION, entries: [] });
  });

  test('Effort_Settings 저장 후 같은 값이 다시 읽힌다 (실제 파일 왕복)', async () => {
    const entry = {
      modelId: 'model-alpha',
      route: 'CONVERSE',
      capabilityFingerprint: 'cfp1:sha256:abc',
      value: 'sym-1',
      valueType: 'STRING',
      updatedAt: '2026-08-03T00:00:00Z',
    };

    const saved = await handlers['capability:save-effort-settings']({}, { schemaVersion: 1, entries: [entry] });
    expect(saved).toEqual({ ok: true, entries: 1, dropped: 0 });

    // 파일이 실제로 userData/capability/ 하위에 생성됨
    const onDisk = readRaw(root, 'effort_settings.json');
    expect(onDisk).toEqual({ schemaVersion: 1, entries: [entry] });

    const loaded = await handlers['capability:load-effort-settings']({});
    expect(loaded).toEqual({ schemaVersion: 1, entries: [entry] });

    // 원자적 쓰기 — 임시 파일이 남지 않는다
    const files = fs.readdirSync(path.join(root, 'capability'));
    expect(files.filter((f) => f.includes('.tmp-'))).toEqual([]);
  });

  test('entry field 화이트리스트 — credential류 field는 저장되지 않는다', async () => {
    const dirty = {
      schemaVersion: 1,
      apitoken: 'AAAA-secret',
      entries: [{
        modelId: 'model-beta',
        route: 'OPENAI_RESPONSES',
        capabilityFingerprint: 'cfp1:sha256:def',
        value: 2,
        valueType: 'INTEGER',
        updatedAt: '2026-08-03T00:00:00Z',
        authorization: 'Bearer secret-token',
        cookie: 'session=secret',
        signature: 'AWS4-HMAC-SHA256 secret',
        credentials: { accessKeyId: 'AKIA_SECRET', secretAccessKey: 'SECRET' },
        rawPrompt: 'sensitive user text',
      }],
    };

    await handlers['capability:save-effort-settings']({}, dirty);

    const onDisk = readRaw(root, 'effort_settings.json');
    expect(Object.keys(onDisk).sort()).toEqual(['entries', 'schemaVersion']);
    expect(Object.keys(onDisk.entries[0]).sort()).toEqual([..._internals.ENTRY_FIELDS].sort());

    const serialized = JSON.stringify(onDisk);
    for (const secret of ['AAAA-secret', 'Bearer secret-token', 'session=secret',
      'AWS4-HMAC-SHA256', 'AKIA_SECRET', 'sensitive user text']) {
      expect(serialized).not.toContain(secret);
    }
    for (const key of ['authorization', 'cookie', 'signature', 'credentials', 'apitoken', 'rawPrompt']) {
      expect(serialized).not.toContain(key);
    }
  });

  test('닫힌 집합 이탈·value 타입 불일치 entry는 저장하지 않는다', async () => {
    const valid = {
      modelId: 'model-gamma',
      route: 'SSE_STREAM',
      capabilityFingerprint: 'cfp1:sha256:aaa',
      value: true,
      valueType: 'BOOLEAN',
      updatedAt: '2026-08-03T00:00:00Z',
    };
    const invalid = [
      { ...valid, route: 'NOT_A_ROUTE' },                        // Known_Route 이탈
      { ...valid, valueType: 'OBJECT' },                          // Value_Type 이탈
      { ...valid, value: { nested: 'x' } },                       // scalar 아님
      { ...valid, value: 'true' },                                // valueType 불일치
      { ...valid, modelId: '' },                                  // 필수 field 공백
      { ...valid, capabilityFingerprint: '' },                    // tuple 성분 누락
      null,
      'not-an-object',
    ];

    const result = await handlers['capability:save-effort-settings']({}, { entries: [...invalid, valid] });
    expect(result.ok).toBe(true);
    expect(result.entries).toBe(1);
    expect(result.dropped).toBe(invalid.length);
    expect(readRaw(root, 'effort_settings.json').entries).toEqual([valid]);
  });

  test('동일 tuple 키는 마지막 항목으로 축약한다', () => {
    const base = {
      modelId: 'model-delta',
      route: 'INVOKE',
      capabilityFingerprint: 'cfp1:sha256:bbb',
      valueType: 'STRING',
      updatedAt: '2026-08-03T00:00:00Z',
    };
    const { settings } = _internals._sanitizeSettings({
      entries: [{ ...base, value: 'first' }, { ...base, value: 'last' }],
    });
    expect(settings.entries).toHaveLength(1);
    expect(settings.entries[0].value).toBe('last');
  });

  test('Capability_Map은 부재 시 null, 존재 시 그대로 반환하고 쓰기 채널이 없다', async () => {
    expect(await handlers['capability:load-map']({})).toBeNull();

    const map = {
      schemaVersion: 1,
      updatedAt: '2026-08-03T00:00:00Z',
      entries: [{ modelId: 'model-eps', verificationStatus: 'UNVERIFIED' }],
    };
    fs.mkdirSync(path.join(root, 'capability'), { recursive: true });
    fs.writeFileSync(path.join(root, 'capability', 'capability_map.json'), JSON.stringify(map), 'utf-8');

    expect(await handlers['capability:load-map']({})).toEqual(map);

    // 손상 JSON → null (기준선 동작 유지)
    fs.writeFileSync(path.join(root, 'capability', 'capability_map.json'), '{not json', 'utf-8');
    expect(await handlers['capability:load-map']({})).toBeNull();

    // map 쓰기 채널은 존재하지 않는다
    expect(ipcMain.handle.mock.calls.map(([c]) => c)).not.toContain('capability:save-map');
  });

  test('capability/가 루트 밖을 가리키는 symlink면 읽기·쓰기를 거부한다', async () => {
    const outside = fs.mkdtempSync(path.join(os.tmpdir(), 'ae-outside-'));
    try {
      fs.symlinkSync(outside, path.join(root, 'capability'), 'dir');

      const saved = await handlers['capability:save-effort-settings']({}, {
        entries: [{
          modelId: 'model-eta',
          route: 'CONVERSE',
          capabilityFingerprint: 'cfp1:sha256:ddd',
          value: 'x',
          valueType: 'STRING',
          updatedAt: '2026-08-03T00:00:00Z',
        }],
      });

      expect(saved.ok).toBe(false);
      expect(fs.readdirSync(outside)).toEqual([]);
      expect(await handlers['capability:load-effort-settings']({}))
        .toEqual({ schemaVersion: _internals.SCHEMA_VERSION, entries: [] });
      expect(await handlers['capability:load-map']({})).toBeNull();
    } finally {
      fs.rmSync(outside, { recursive: true, force: true });
    }
  });

  test('경로 인자를 넘겨도 무시하고 고정 파일만 읽는다', async () => {
    await handlers['capability:save-effort-settings']({}, {
      entries: [{
        modelId: 'model-zeta',
        route: 'CONVERSE',
        capabilityFingerprint: 'cfp1:sha256:ccc',
        value: 1.5,
        valueType: 'NUMBER',
        updatedAt: '2026-08-03T00:00:00Z',
      }],
    });

    // 두 번째 인자(핸들러가 무시하는 위치)에 traversal 문자열을 넣어도 동작 불변
    const loaded = await handlers['capability:load-effort-settings']({}, '../../settings/settings.json');
    expect(loaded.entries).toHaveLength(1);
    expect(fs.existsSync(path.join(root, 'settings', 'settings.json'))).toBe(false);
  });
});

describe('preload 화이트리스트', () => {
  const source = fs.readFileSync(path.join(__dirname, '..', '..', 'electron', 'preload.js'), 'utf-8');

  test('capability 3개 채널만 추가 노출한다', () => {
    for (const channel of CHANNELS) {
      expect(source).toContain(`ipcRenderer.invoke('${channel}'`);
    }
    const capabilityChannels = (source.match(/'capability:[a-z-]+'/g) || []);
    expect(new Set(capabilityChannels)).toEqual(new Set(CHANNELS.map((c) => `'${c}'`)));
  });

  test('ipcRenderer를 렌더러에 노출하지 않는다', () => {
    expect(source).not.toMatch(/\bipcRenderer\s*[,:]\s*(ipcRenderer)?\s*[,}]/);
    expect(source).not.toContain('exposeInMainWorld(\'ipcRenderer\'');
    expect(source).toContain('contextBridge.exposeInMainWorld(\'electronAPI\'');
  });
});

describe('main.js 등록', () => {
  const source = fs.readFileSync(path.join(__dirname, '..', '..', 'electron', 'main.js'), 'utf-8');

  test('registerCapabilityHandlers(dataStore)를 main에서만 등록한다', () => {
    expect(source).toContain("require('./src/ipc-capability-handlers')");
    expect(source).toContain('registerCapabilityHandlers(dataStore);');
    expect((source.match(/registerCapabilityHandlers\(dataStore\);/g) || [])).toHaveLength(1);
  });
});
