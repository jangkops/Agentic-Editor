/**
 * 검색 제공자 API 키 저장소 — OS 키체인(Electron safeStorage) 기반.
 *
 * 왜 이 모듈이 필요한가:
 *   웹 검색 제공자(tavily/exa/brave)는 전부 API 키가 필수인데, 리포 어디에도 키를
 *   런타임에 공급하는 주체가 없었다. 설정 UI 는 "실행 시 환경변수로만 주입됩니다"라고
 *   고지하지만 주입 주체가 없어, 사용자가 옵트인·동의·제공자를 다 켜도
 *   `backend.web_search_raw` 가 네트워크 호출 전에 `missing_credential` 로 반환했다.
 *   (GUI 로 앱을 띄우면 셸 export 도 상속되지 않는다.)
 *
 * 보안 설계 (`.kiro/steering/security.md` / research 요구사항 11):
 *   - 키를 **평문 파일로 저장하지 않는다.** `safeStorage.encryptString` 으로 OS 키체인
 *     기반 암호화 후 base64 만 디스크에 남긴다(macOS Keychain / Windows DPAPI 등).
 *   - 암호화를 쓸 수 없는 환경이면 **저장을 거부한다.** 평문 폴백은 만들지 않는다 —
 *     사용자에게 한 약속("키는 앱에 저장되지 않습니다")을 조용히 깨는 것이 최악이다.
 *   - 복호화 값은 메모리에만 두고, 사이드카(Python)에 IPC/localhost 로만 전달한다.
 *   - 렌더러에는 **설정 여부(bool)만** 노출한다. 값을 되읽는 경로를 만들지 않는다.
 *   - 파일 권한은 0600(소유자만).
 */
const path = require('path');
const fs = require('fs');

let app = null;
let safeStorage = null;
try {
  ({ app, safeStorage } = require('electron'));
} catch (_) {
  app = null;
  safeStorage = null;
}

// 키를 받을 제공자 — ai_engine/research/security.py PROVIDER_ENV_VARS 와 정합.
const PROVIDERS = Object.freeze(['tavily', 'exa', 'brave', 'semantic_scholar']);

// 키가 없으면 호출 자체가 불가한 제공자 — ai_engine/research/backend.py
// `_REQUIRES_KEY` 와 정합해야 한다.
// tavily 는 키리스 모드(`X-Tavily-Access-Mode: keyless`)가 있어 제외한다 —
// 키 없이도 검색이 되고, 키를 넣으면 레이트리밋만 올라간다. 30명 배포에서
// 개인별 키 발급을 강제하지 않기 위한 기본 경로다.
const REQUIRES_KEY = Object.freeze(['exa', 'brave']);

const FILE_NAME = 'research-credentials.json';
const SCHEMA_VERSION = 1;

function _baseDir() {
  if (app && typeof app.getPath === 'function') {
    try { return app.getPath('userData'); } catch (_) { /* fall through */ }
  }
  return path.join(require('os').homedir(), '.ai-editor');
}

function _filePath() {
  return path.join(_baseDir(), 'settings', FILE_NAME);
}

/** OS 키체인 암호화 가용 여부. false면 저장을 거부한다(평문 폴백 없음). */
function isAvailable() {
  try {
    return !!(safeStorage && safeStorage.isEncryptionAvailable());
  } catch (_) {
    return false;
  }
}

function _readFile() {
  try {
    const p = _filePath();
    if (!fs.existsSync(p)) return { version: SCHEMA_VERSION, keys: {} };
    const data = JSON.parse(fs.readFileSync(p, 'utf-8'));
    if (!data || typeof data !== 'object' || typeof data.keys !== 'object') {
      return { version: SCHEMA_VERSION, keys: {} };
    }
    return { version: SCHEMA_VERSION, keys: data.keys || {} };
  } catch (_) {
    // 손상 파일은 빈 저장소로 취급(비차단). 사용자가 다시 입력하면 덮어쓴다.
    return { version: SCHEMA_VERSION, keys: {} };
  }
}

function _writeFile(store) {
  const p = _filePath();
  fs.mkdirSync(path.dirname(p), { recursive: true });
  fs.writeFileSync(p, JSON.stringify({ version: SCHEMA_VERSION, keys: store.keys }, null, 2), {
    encoding: 'utf-8',
    mode: 0o600,
  });
  // 기존 파일이 있었다면 권한을 다시 강제(writeFileSync mode는 신규 생성에만 적용).
  try { fs.chmodSync(p, 0o600); } catch (_) { /* 권한 강제 실패는 비차단 */ }
}

/**
 * 키를 저장한다. 빈 값이면 해당 제공자를 삭제한다.
 * @returns {{ok: boolean, error?: string}}
 */
function saveKey(provider, key) {
  const name = String(provider || '').trim().toLowerCase();
  if (!PROVIDERS.includes(name)) return { ok: false, error: 'unknown_provider' };

  const value = typeof key === 'string' ? key.trim() : '';
  const store = _readFile();

  if (!value) {
    delete store.keys[name];
    try { _writeFile(store); } catch (e) { return { ok: false, error: 'write_failed' }; }
    return { ok: true };
  }

  if (!isAvailable()) {
    // 평문으로 떨어지지 않고 명확히 실패시킨다.
    return { ok: false, error: 'encryption_unavailable' };
  }
  // 암호화 실패와 파일 쓰기 실패를 구분해 UI 가 원인을 다르게 안내할 수 있게 한다.
  let blob;
  try {
    blob = safeStorage.encryptString(value).toString('base64');
  } catch (_) {
    return { ok: false, error: 'encrypt_failed' };
  }
  try {
    store.keys[name] = blob;
    _writeFile(store);
    return { ok: true };
  } catch (_) {
    return { ok: false, error: 'write_failed' };
  }
}

function clearKey(provider) {
  return saveKey(provider, '');
}

/**
 * 복호화된 키 맵을 반환한다 — **메인 프로세스 전용**. 렌더러에 노출 금지.
 * @returns {Object<string,string>} { tavily: '...', ... } (복호화 실패 항목은 생략)
 */
function decryptedKeys() {
  const out = {};
  if (!isAvailable()) return out;
  const store = _readFile();
  for (const [name, blob] of Object.entries(store.keys)) {
    if (!PROVIDERS.includes(name) || typeof blob !== 'string' || !blob) continue;
    try {
      const val = safeStorage.decryptString(Buffer.from(blob, 'base64'));
      if (val && val.trim()) out[name] = val.trim();
    } catch (_) {
      // 다른 머신에서 만든 파일 등 복호화 불가 항목은 건너뛴다(비차단).
    }
  }
  return out;
}

/**
 * 제공자별 설정 여부(bool)만 반환 — 렌더러 노출용. 값은 절대 포함하지 않는다.
 * @returns {{available: boolean, providers: Object<string,boolean>, requiresKey: string[]}}
 */
function status() {
  const store = _readFile();
  const providers = {};
  for (const name of PROVIDERS) {
    providers[name] = !!(store.keys[name] && typeof store.keys[name] === 'string');
  }
  return { available: isAvailable(), providers, requiresKey: [...REQUIRES_KEY] };
}

module.exports = {
  PROVIDERS,
  REQUIRES_KEY,
  isAvailable,
  saveKey,
  clearKey,
  decryptedKeys,
  status,
  _filePath,
};
