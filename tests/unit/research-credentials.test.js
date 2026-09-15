/**
 * 검색 제공자 API 키 저장소 — 보안 불변식 회귀 테스트
 *
 * ── 배경 ──────────────────────────────────────────────────────────────
 * 웹 검색 제공자(tavily/exa/brave)는 API 키가 필수인데, 키를 런타임에 공급하는
 * 주체가 리포에 없어서 사용자가 설정을 다 켜도 `missing_credential` 로 실패했다.
 * 그 공백을 OS 키체인(safeStorage) 기반 저장소로 채웠다.
 *
 * ── 이 테스트가 고정하는 불변식 ──────────────────────────────────────
 *   1. 디스크에 **평문 키가 남지 않는다** (암호화 후 base64 만 기록)
 *   2. `status()` 는 **값을 반환하지 않는다** — 설정 여부(bool)만
 *   3. 암호화 불가 환경에서는 **저장을 거부한다** — 평문 폴백 없음
 *   4. 저장 → 복호화 왕복이 정확하다(주입할 값이 손상되지 않음)
 *   5. 빈 값 저장 = 해제, 파일 권한 0600
 *   6. 다른 머신의 복호화 불가 항목은 건너뛴다(비차단)
 *
 * 검증 대상은 `electron/core/research-credentials.js` 실제 파일이다.
 * electron 모듈은 `tests/mocks/electron.js`(jest moduleNameMapper)로 대체된다.
 */

const fs = require('fs');
const os = require('os');
const path = require('path');

const { app, safeStorage } = require('electron'); // tests/mocks/electron.js
// 저장 경로는 호출 시점에 app.getPath('userData')로 매번 계산되므로 모듈 캐시를
// 리셋할 필요가 없다(리셋하면 electron mock 인스턴스가 갈라져 mockReturnValue 가
// 모듈 쪽에 적용되지 않는다).
const creds = require('../../electron/core/research-credentials');

const KEY_TAVILY = 'tvly-live-SAMPLEKEY-0123456789abcdef';
const KEY_EXA = 'exa-SAMPLEKEY-fedcba9876543210';

let tmpRoot;

function credFile() {
  return path.join(tmpRoot, 'settings', 'research-credentials.json');
}

function rawFileText() {
  return fs.existsSync(credFile()) ? fs.readFileSync(credFile(), 'utf-8') : '';
}

beforeEach(() => {
  tmpRoot = fs.mkdtempSync(path.join(os.tmpdir(), 'ae-research-creds-'));
  app.getPath.mockReturnValue(tmpRoot);
  safeStorage.__setAvailable(true);
});

afterEach(() => {
  try { fs.rmSync(tmpRoot, { recursive: true, force: true }); } catch (_) { /* noop */ }
});

describe('research-credentials — 평문 미저장', () => {
  test('저장된 파일에 키 평문이 존재하지 않는다', () => {
    expect(creds.saveKey('tavily', KEY_TAVILY)).toEqual({ ok: true });

    const text = rawFileText();
    expect(text).not.toBe('');
    // 전체 문자열은 물론, 키의 특징적인 조각도 남아 있으면 안 된다.
    expect(text).not.toContain(KEY_TAVILY);
    expect(text).not.toContain('SAMPLEKEY');
    expect(text).not.toContain('tvly-live');
  });

  test('여러 제공자를 저장해도 어떤 평문도 남지 않는다', () => {
    creds.saveKey('tavily', KEY_TAVILY);
    creds.saveKey('exa', KEY_EXA);

    const text = rawFileText();
    for (const secret of [KEY_TAVILY, KEY_EXA, 'SAMPLEKEY']) {
      expect(text).not.toContain(secret);
    }
    // 저장 자체는 되어 있어야 한다(암호화 blob 존재).
    const parsed = JSON.parse(text);
    expect(Object.keys(parsed.keys).sort()).toEqual(['exa', 'tavily']);
    expect(typeof parsed.keys.tavily).toBe('string');
    expect(parsed.keys.tavily.length).toBeGreaterThan(0);
  });

  test('파일 권한이 소유자 전용(0600)이다', () => {
    creds.saveKey('tavily', KEY_TAVILY);
    const mode = fs.statSync(credFile()).mode & 0o777;
    expect(mode).toBe(0o600);
  });
});

describe('research-credentials — 값 미노출', () => {
  test('status()는 bool 만 반환하고 키 값을 담지 않는다', () => {
    creds.saveKey('tavily', KEY_TAVILY);

    const st = creds.status();
    expect(st.providers.tavily).toBe(true);
    expect(st.providers.exa).toBe(false);
    // 직렬화해도 값이 새지 않는다.
    const blob = JSON.stringify(st);
    expect(blob).not.toContain(KEY_TAVILY);
    expect(blob).not.toContain('SAMPLEKEY');
    // 모든 provider 값은 boolean 이어야 한다.
    for (const v of Object.values(st.providers)) expect(typeof v).toBe('boolean');
  });

  test('requiresKey 에 tavily 가 없다 — 키리스 모드 지원 (backend._REQUIRES_KEY 정합)', () => {
    // Tavily 는 `X-Tavily-Access-Mode: keyless` 로 키 없이 호출된다. 여기에 tavily 가
    // 다시 들어오면 30명 배포에서 개인별 키 발급이 강제되므로 회귀로 간주한다.
    expect(creds.status().requiresKey.sort()).toEqual(['brave', 'exa']);
    expect(creds.status().requiresKey).not.toContain('tavily');
    // 단, 키를 넣는 것 자체는 여전히 가능해야 한다(한도 상향 목적).
    expect(creds.PROVIDERS).toContain('tavily');
  });
});

describe('research-credentials — 암호화 불가 환경', () => {
  test('safeStorage 사용 불가면 저장을 거부한다 (평문 폴백 없음)', () => {
    safeStorage.__setAvailable(false);

    const res = creds.saveKey('tavily', KEY_TAVILY);
    expect(res.ok).toBe(false);
    expect(res.error).toBe('encryption_unavailable');
    // 파일이 생겼더라도 키가 들어 있으면 안 된다.
    const text = rawFileText();
    expect(text).not.toContain(KEY_TAVILY);
    expect(creds.status().providers.tavily).toBe(false);
  });

  test('암호화 불가면 decryptedKeys()가 빈 객체다', () => {
    creds.saveKey('tavily', KEY_TAVILY);
    safeStorage.__setAvailable(false);
    expect(creds.decryptedKeys()).toEqual({});
  });
});

describe('research-credentials — 왕복과 해제', () => {
  test('저장 → 복호화 왕복이 값을 보존한다', () => {
    creds.saveKey('tavily', KEY_TAVILY);
    creds.saveKey('exa', KEY_EXA);

    expect(creds.decryptedKeys()).toEqual({ tavily: KEY_TAVILY, exa: KEY_EXA });
  });

  test('앞뒤 공백은 제거해 저장한다', () => {
    creds.saveKey('tavily', `  ${KEY_TAVILY}  `);
    expect(creds.decryptedKeys().tavily).toBe(KEY_TAVILY);
  });

  test('빈 값 저장은 해제로 동작한다', () => {
    creds.saveKey('tavily', KEY_TAVILY);
    expect(creds.status().providers.tavily).toBe(true);

    expect(creds.saveKey('tavily', '')).toEqual({ ok: true });
    expect(creds.status().providers.tavily).toBe(false);
    expect(creds.decryptedKeys()).toEqual({});
  });

  test('clearKey 도 같은 결과를 낸다', () => {
    creds.saveKey('exa', KEY_EXA);
    expect(creds.clearKey('exa')).toEqual({ ok: true });
    expect(creds.status().providers.exa).toBe(false);
  });

  test('미지원 제공자는 거부한다', () => {
    const res = creds.saveKey('not_a_provider', KEY_TAVILY);
    expect(res).toEqual({ ok: false, error: 'unknown_provider' });
  });
});

describe('research-credentials — 방어적 동작', () => {
  test('복호화 불가 항목(다른 머신 키체인)은 건너뛴다', () => {
    creds.saveKey('tavily', KEY_TAVILY);
    // 다른 머신에서 만들어져 복호화가 안 되는 blob 을 섞는다.
    const store = JSON.parse(rawFileText());
    store.keys.brave = Buffer.from('other-machine-blob', 'utf-8').toString('base64');
    fs.writeFileSync(credFile(), JSON.stringify(store), 'utf-8');

    const keys = creds.decryptedKeys();
    expect(keys.tavily).toBe(KEY_TAVILY);
    expect(keys.brave).toBeUndefined();   // 예외 없이 생략
  });

  test('손상된 파일은 빈 저장소로 취급한다(예외 전파 없음)', () => {
    fs.mkdirSync(path.join(tmpRoot, 'settings'), { recursive: true });
    fs.writeFileSync(credFile(), '{ this is not json', 'utf-8');

    expect(creds.status().providers.tavily).toBe(false);
    expect(creds.decryptedKeys()).toEqual({});
    // 손상 상태에서도 새 저장이 가능해야 한다.
    expect(creds.saveKey('tavily', KEY_TAVILY)).toEqual({ ok: true });
    expect(creds.decryptedKeys().tavily).toBe(KEY_TAVILY);
  });
});
