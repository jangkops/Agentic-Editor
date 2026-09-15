/**
 * Capability IPC Handlers
 * 책임: `capability:*` 3개 채널을 `userData/capability/` 하위 파일로 처리한다.
 *
 * 채널 (design.md §6 Effort_Control / Effort_Settings_Manager):
 *   - `capability:load-effort-settings` → `userData/capability/effort_settings.json` 읽기
 *   - `capability:save-effort-settings` → 같은 파일에 원자적 쓰기
 *   - `capability:load-map`             → `userData/capability/capability_map.json` 읽기
 *
 * 경로 규약(요구사항 10.15): 세 핸들러 모두 **경로 인자를 받지 않는다.** 파일명은 이 모듈의
 * 고정 상수(`_ALLOWED_FILES`)이며 루트는 `dataStore.basePath`(= `app.getPath('userData')`)로만
 * 해석한다. 따라서 렌더러 입력이 경로 성분에 도달할 수 없고 `..`·절대경로 주입이 구조적으로
 * 불가능하다. 그 위에 realpath 정규화 후 루트 하위 여부를 재확인해 symlink 이스케이프까지
 * 차단한다(`_assertInsideRoot`).
 *
 * 스키마(design.md §Data Models → Effort_Settings): `{schemaVersion, entries:[{modelId, route,
 * capabilityFingerprint, value, valueType, updatedAt}]}`. 저장 시 **entry field 화이트리스트**만
 * 기록하므로 credential·authorization·cookie·signature 값은 어떤 경로로도 파일에 남지 않는다
 * (요구사항 10.7). `userData/settings/settings.json` 스키마는 손대지 않고 Effort_Settings를
 * 별도 파일로 분리하며, settings에는 기존대로 AWS profile name만 남는다(요구사항 10.11).
 *
 * 원자적 쓰기: 같은 디렉터리에 임시 파일 → `fsync` → `fs.renameSync`로 교체한다. 독자는 항상
 * 이전 완전본 또는 새 완전본만 본다(`ai_engine/capability/store.py`의 `os.replace` 규약과 동일).
 *
 * 보안 노트(security.md): 등록은 `electron/main.js::registerAllIpcHandlers()`에서만 수행하고
 * (`registerCapabilityHandlers(dataStore)` 1행), 렌더러에는 `electron/preload.js` 화이트리스트
 * 3개 메서드만 노출한다. `ipcRenderer`는 노출하지 않으며 `contextIsolation: true`·
 * `nodeIntegration: false`를 유지한다.
 *
 * Requirements: 10.7, 10.11, 10.15
 * Design: "통합 지점" 표(설정 영속화·IPC 등록·preload) · §6 Effort_Control / Effort_Settings_Manager
 */

const { ipcMain, app } = require('electron');
const fs = require('fs');
const path = require('path');

// ── 고정 경로 상수 (ai_engine/capability/store.py와 동일 규약) ──────────────
const CAPABILITY_DIRNAME = 'capability';
const EFFORT_SETTINGS_FILENAME = 'effort_settings.json';
const CAPABILITY_MAP_FILENAME = 'capability_map.json';

/** 이 모듈이 접근을 허용하는 파일명 전체 집합. 렌더러는 파일명을 지정할 수 없다. */
const _ALLOWED_FILES = new Set([EFFORT_SETTINGS_FILENAME, CAPABILITY_MAP_FILENAME]);

// ── Effort_Settings 스키마 상수 (design.md Data Models) ────────────────────
/** `ai_engine/capability/contracts.py::SCHEMA_VERSION`과 동일. */
const SCHEMA_VERSION = 1;

/** entry에 저장을 허용하는 field 전체 집합(화이트리스트). 그 외 key는 기록하지 않는다. */
const ENTRY_FIELDS = ['modelId', 'route', 'capabilityFingerprint', 'value', 'valueType', 'updatedAt'];

/** `Known_Route` 닫힌 집합. */
const KNOWN_ROUTES = new Set(['CONVERSE', 'INVOKE', 'OPENAI_RESPONSES', 'OPENAI_RESPONSES_JOBS', 'SSE_STREAM']);

/** `Value_Type` 닫힌 집합. */
const VALUE_TYPES = new Set(['STRING', 'INTEGER', 'NUMBER', 'BOOLEAN']);

// ─────────────────────────────────────────────────────────────────
// 경로 해석 — userData 하위 한정
// ─────────────────────────────────────────────────────────────────

/**
 * userData 루트 절대 경로. `dataStore.basePath`(생성 시 `app.getPath('userData')`)를 우선 사용하고,
 * 주입된 store가 없으면 `app.getPath('userData')`로 폴백한다.
 * @param {{basePath?: string}} [dataStore]
 * @returns {string} 절대 경로
 */
function _userDataRoot(dataStore) {
  const base = dataStore && typeof dataStore.basePath === 'string' ? dataStore.basePath.trim() : '';
  if (base) return path.resolve(base);
  if (app && typeof app.getPath === 'function') return path.resolve(app.getPath('userData'));
  throw new Error('userData root를 확인할 수 없습니다');
}

/**
 * `target`이 `root` 하위인지 확인한다. 루트 자신·상위·형제 경로는 모두 거부한다.
 * @param {string} root - 절대 경로
 * @param {string} target - 절대 경로
 * @returns {boolean}
 */
function _isInsideRoot(root, target) {
  const rel = path.relative(root, target);
  return !!rel && !rel.startsWith(`..${path.sep}`) && rel !== '..' && !path.isAbsolute(rel);
}

/**
 * 루트 이탈 시 예외를 던진다. 존재하는 경로 성분은 realpath로 정규화해 symlink 이스케이프도 막는다.
 * @param {string} root - userData 절대 경로
 * @param {string} target - 접근 대상 절대 경로
 * @returns {void}
 */
function _assertInsideRoot(root, target) {
  if (!_isInsideRoot(root, target)) {
    throw new Error('userData 루트 밖 경로 접근 거부');
  }
  if (!_isInsideRoot(_realpathDeep(root), _realpathDeep(target))) {
    throw new Error('userData 루트 밖 경로 접근 거부(symlink)');
  }
}

/**
 * 존재하지 않는 경로도 다룰 수 있는 realpath — 존재하는 가장 깊은 조상을 정규화하고
 * 남은 성분을 그대로 이어 붙인다. 아직 만들어지지 않은 `capability/` 디렉터리 때문에
 * 정상 경로가 거부되는 일 없이, 실제로 존재하는 symlink는 해소해 이스케이프를 잡아낸다.
 * @param {string} p
 * @returns {string} 절대 경로
 */
function _realpathDeep(p) {
  let current = path.resolve(p);
  const trailing = [];
  for (;;) {
    try {
      return path.join(fs.realpathSync(current), ...trailing);
    } catch {
      const parent = path.dirname(current);
      if (parent === current) return path.resolve(p); // 루트까지 미해결 — 원본 사용
      trailing.unshift(path.basename(current));
      current = parent;
    }
  }
}

/**
 * `userData/capability/{filename}` 절대 경로를 만든다. 허용 파일명이 아니면 예외.
 * @param {{basePath?: string}} dataStore
 * @param {string} filename - `_ALLOWED_FILES` 원소
 * @returns {string} 절대 경로
 */
function _capabilityFilePath(dataStore, filename) {
  if (!_ALLOWED_FILES.has(filename)) {
    throw new Error(`허용되지 않은 capability 파일명: ${String(filename).slice(0, 40)}`);
  }
  const root = _userDataRoot(dataStore);
  const target = path.resolve(root, CAPABILITY_DIRNAME, filename);
  _assertInsideRoot(root, target);
  return target;
}

// ─────────────────────────────────────────────────────────────────
// JSON 읽기 · 원자적 쓰기
// ─────────────────────────────────────────────────────────────────

/**
 * JSON 파일을 읽는다. 파일 부재·파싱 실패는 예외 없이 `fallback`을 반환한다.
 * @param {string} file - 절대 경로
 * @param {*} fallback
 * @returns {*}
 */
function _readJson(file, fallback) {
  try {
    if (!fs.existsSync(file)) return fallback;
    const parsed = JSON.parse(fs.readFileSync(file, 'utf-8'));
    return parsed === null || parsed === undefined ? fallback : parsed;
  } catch (error) {
    console.error(`[capability] read failed (${path.basename(file)}):`, error.message);
    return fallback;
  }
}

/**
 * 원자적 JSON 쓰기 — 임시 파일 → fsync → rename.
 * @param {string} file - 절대 경로
 * @param {*} data - JSON 직렬화 가능한 값
 * @returns {void}
 */
function _writeJsonAtomic(file, data) {
  const dir = path.dirname(file);
  fs.mkdirSync(dir, { recursive: true });
  const tmp = path.join(dir, `.${path.basename(file)}.tmp-${process.pid}-${Date.now()}`);
  const fd = fs.openSync(tmp, 'w');
  try {
    fs.writeSync(fd, JSON.stringify(data, null, 2), 0, 'utf-8');
    try { fs.fsyncSync(fd); } catch { /* best-effort — 일부 FS는 fsync 거부 */ }
  } finally {
    try { fs.closeSync(fd); } catch { /* already closed */ }
  }
  try {
    fs.renameSync(tmp, file);
  } catch (error) {
    try { fs.unlinkSync(tmp); } catch { /* ignore */ }
    throw error;
  }
}

// ─────────────────────────────────────────────────────────────────
// Effort_Settings 정규화 (field 화이트리스트)
// ─────────────────────────────────────────────────────────────────

/** 빈 Effort_Settings. 파일 부재·손상 시 반환값. */
function _emptySettings() {
  return { schemaVersion: SCHEMA_VERSION, entries: [] };
}

/**
 * 문자열 필드 정규화. 문자열이 아니면 빈 문자열.
 * @param {*} v
 * @returns {string}
 */
function _text(v) {
  return typeof v === 'string' ? v : '';
}

/**
 * 저장된 `value`가 `valueType`과 일치하는 scalar인지 확인한다.
 * 객체·배열은 어떤 valueType에서도 허용하지 않으므로 중첩 비밀정보가 value로 들어올 수 없다.
 * @param {*} value
 * @param {string} valueType - `Value_Type`
 * @returns {boolean}
 */
function _valueMatchesType(value, valueType) {
  switch (valueType) {
    case 'STRING': return typeof value === 'string';
    case 'INTEGER': return typeof value === 'number' && Number.isInteger(value);
    case 'NUMBER': return typeof value === 'number' && Number.isFinite(value);
    case 'BOOLEAN': return typeof value === 'boolean';
    default: return false;
  }
}

/**
 * 하나의 Effort_Settings entry를 화이트리스트 field만 남겨 정규화한다.
 * 필수 field 누락·enum 이탈·value/valueType 불일치면 `null`(= 저장하지 않음).
 * @param {*} raw
 * @returns {{modelId:string,route:string,capabilityFingerprint:string,value:*,valueType:string,updatedAt:string}|null}
 */
function _sanitizeEntry(raw) {
  if (!raw || typeof raw !== 'object' || Array.isArray(raw)) return null;

  const modelId = _text(raw.modelId);
  const route = _text(raw.route);
  const capabilityFingerprint = _text(raw.capabilityFingerprint);
  const valueType = _text(raw.valueType);
  if (!modelId || !capabilityFingerprint) return null;
  if (!KNOWN_ROUTES.has(route)) return null;
  if (!VALUE_TYPES.has(valueType)) return null;
  if (!_valueMatchesType(raw.value, valueType)) return null;

  const updatedAt = _text(raw.updatedAt) || new Date().toISOString();
  const entry = { modelId, route, capabilityFingerprint, value: raw.value, valueType, updatedAt };
  // ENTRY_FIELDS 외 key는 애초에 복사하지 않는다 — credential·authorization·cookie·
  // signature 등 어떤 추가 field도 파일에 도달할 수 없다(요구사항 10.7).
  return entry;
}

/**
 * 렌더러가 보낸 Effort_Settings를 저장 가능한 형태로 정규화한다.
 * - 최상위는 `{schemaVersion, entries}`만 남긴다(미지원 버전은 현재 버전으로 기록).
 * - entry는 `_sanitizeEntry` 통과분만 남기고, `(modelId, route, capabilityFingerprint)`
 *   tuple 키가 겹치면 마지막 항목(렌더러의 최신 의도)을 채택한다.
 * @param {*} raw
 * @returns {{settings: object, dropped: number}}
 */
function _sanitizeSettings(raw) {
  const source = raw && typeof raw === 'object' && !Array.isArray(raw) ? raw : {};
  const rawEntries = Array.isArray(source.entries) ? source.entries : [];
  const byTuple = new Map();
  let dropped = 0;

  for (const item of rawEntries) {
    const entry = _sanitizeEntry(item);
    if (!entry) { dropped += 1; continue; }
    byTuple.set(`${entry.modelId}\u0000${entry.route}\u0000${entry.capabilityFingerprint}`, entry);
  }

  const version = Number.isInteger(source.schemaVersion) && source.schemaVersion === SCHEMA_VERSION
    ? source.schemaVersion
    : SCHEMA_VERSION;

  return { settings: { schemaVersion: version, entries: Array.from(byTuple.values()) }, dropped };
}

// ─────────────────────────────────────────────────────────────────
// 핸들러 등록
// ─────────────────────────────────────────────────────────────────

/**
 * Capability IPC 핸들러 등록 (main 프로세스 전용 — `electron/main.js`에서만 호출).
 *
 * 세 핸들러 모두 경로 인자를 받지 않으며, 실패 시 렌더러로 예외를 전파하지 않고
 * 안전값(빈 settings / null / `{ok:false, error}`)으로 폴백한다.
 *
 * @param {{basePath?: string}} dataStore - DataStore 인스턴스(`basePath` = userData 절대 경로)
 * @returns {void}
 */
function registerCapabilityHandlers(dataStore) {
  /**
   * Effort_Settings 로드. 파일 부재·손상 시 빈 settings를 반환한다(렌더러 null 처리 불필요).
   * @returns {{schemaVersion:number, entries:Array}}
   */
  ipcMain.handle('capability:load-effort-settings', () => {
    try {
      const file = _capabilityFilePath(dataStore, EFFORT_SETTINGS_FILENAME);
      const raw = _readJson(file, null);
      if (raw === null) return _emptySettings();
      return _sanitizeSettings(raw).settings;
    } catch (error) {
      console.error('[capability:load-effort-settings] Error:', error.message);
      return _emptySettings();
    }
  });

  /**
   * Effort_Settings 저장 — 화이트리스트 field만 원자적으로 기록한다.
   * @param {Electron.IpcMainInvokeEvent} _evt
   * @param {*} data - `{schemaVersion, entries:[...]}`
   * @returns {{ok:boolean, entries?:number, dropped?:number, error?:string}}
   */
  ipcMain.handle('capability:save-effort-settings', (_evt, data) => {
    try {
      const file = _capabilityFilePath(dataStore, EFFORT_SETTINGS_FILENAME);
      const { settings, dropped } = _sanitizeSettings(data);
      _writeJsonAtomic(file, settings);
      // 값 본문은 로그에 남기지 않는다 — 건수만 기록.
      if (dropped > 0) {
        console.warn(`[capability:save-effort-settings] dropped ${dropped} invalid entr(y|ies)`);
      }
      return { ok: true, entries: settings.entries.length, dropped };
    } catch (error) {
      console.error('[capability:save-effort-settings] Error:', error.message);
      return { ok: false, error: String(error.message || error).slice(0, 200) };
    }
  });

  /**
   * Capability_Map 로드(읽기 전용). 파일 부재·손상·형식 위반이면 `null`을 반환하고,
   * 렌더러는 이 경우 capability 미설정 = 기존(기준선) 동작을 유지한다.
   * @returns {object|null}
   */
  ipcMain.handle('capability:load-map', () => {
    try {
      const file = _capabilityFilePath(dataStore, CAPABILITY_MAP_FILENAME);
      const raw = _readJson(file, null);
      if (!raw || typeof raw !== 'object' || Array.isArray(raw)) return null;
      return raw;
    } catch (error) {
      console.error('[capability:load-map] Error:', error.message);
      return null;
    }
  });
}

module.exports = {
  registerCapabilityHandlers,
  // 테스트용 내부 노출 — 채널 구현과 동일한 경로·정규화 규약을 직접 검증하기 위함.
  _internals: {
    CAPABILITY_DIRNAME,
    EFFORT_SETTINGS_FILENAME,
    CAPABILITY_MAP_FILENAME,
    SCHEMA_VERSION,
    ENTRY_FIELDS,
    KNOWN_ROUTES,
    VALUE_TYPES,
    _capabilityFilePath,
    _sanitizeSettings,
    _emptySettings,
  },
};
