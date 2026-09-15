/**
 * 리서치 자격증명 IPC 핸들러.
 *
 * 렌더러 ↔ OS 키체인 사이의 유일한 통로다. 렌더러에는 **설정 여부(bool)만** 돌려주고
 * 키 값을 되읽는 채널은 만들지 않는다(`.kiro/steering/security.md`).
 *
 * 저장 후에는 실행 중인 Python 사이드카에 키를 밀어넣어 **재시작 없이** 다음 요청부터
 * 적용되게 한다. spawn-time env 주입만으로는 dev 모드(`electron/main.js` 가 Python 을
 * 띄우지 않고 `npm run dev:python` 이 담당)에서 키가 전달되지 않기 때문이다.
 */
const { ipcMain } = require('electron');
const researchCreds = require('../core/research-credentials');

// 사이드카 주소 — 기존 렌더러 apiBase()와 동일 포트.
const SIDECAR_BASE = process.env.AE_SIDECAR_BASE || 'http://127.0.0.1:8765';

/**
 * 복호화된 키를 사이드카 env 로 주입한다(비차단).
 * 값은 로그에 남기지 않는다 — 성공/실패와 제공자 수만 기록한다.
 * @returns {Promise<{ok: boolean, error?: string, providers?: object}>}
 */
async function pushCredentialsToSidecar() {
  let credentials;
  try {
    credentials = researchCreds.decryptedKeys();
  } catch (e) {
    return { ok: false, error: 'decrypt_failed' };
  }
  // 저장된 키가 없어도 호출한다 — 해제(빈 값)를 사이드카에 반영해야 하기 때문.
  const body = { credentials: {} };
  for (const name of researchCreds.PROVIDERS) {
    body.credentials[name] = credentials[name] || '';
  }
  try {
    const res = await fetch(`${SIDECAR_BASE}/api/research/credentials`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(body),
      signal: AbortSignal.timeout(5000),
    });
    if (!res.ok) return { ok: false, error: `http_${res.status}` };
    const json = await res.json();
    const n = Object.values(json.providers || {}).filter(Boolean).length;
    console.log(`[research-creds] 사이드카 주입 완료 — 설정된 제공자 ${n}개`);
    return { ok: true, providers: json.providers || {} };
  } catch (e) {
    // 사이드카가 아직 안 떴을 수 있다(앱 시작 순서). 비차단.
    console.log(`[research-creds] 사이드카 주입 보류: ${e.message}`);
    return { ok: false, error: 'sidecar_unreachable' };
  }
}

function registerResearchHandlers() {
  /** 제공자별 키 설정 여부(bool) + 암호화 가용 여부. 값은 반환하지 않는다. */
  ipcMain.handle('research-creds:status', () => {
    try {
      return researchCreds.status();
    } catch (e) {
      console.error('[research-creds:status] Error:', e.message);
      return { available: false, providers: {}, requiresKey: [] };
    }
  });

  /** 키 저장(빈 문자열이면 해제) → 즉시 사이드카에 반영. */
  ipcMain.handle('research-creds:set', async (_evt, provider, key) => {
    try {
      const saved = researchCreds.saveKey(provider, key);
      if (!saved.ok) return { ...saved, status: researchCreds.status() };
      const pushed = await pushCredentialsToSidecar();
      return { ok: true, applied: pushed.ok, status: researchCreds.status() };
    } catch (e) {
      console.error('[research-creds:set] Error:', e.message);
      return { ok: false, error: 'unexpected' };
    }
  });

  /** 키 해제. */
  ipcMain.handle('research-creds:clear', async (_evt, provider) => {
    try {
      const cleared = researchCreds.clearKey(provider);
      if (!cleared.ok) return { ...cleared, status: researchCreds.status() };
      const pushed = await pushCredentialsToSidecar();
      return { ok: true, applied: pushed.ok, status: researchCreds.status() };
    } catch (e) {
      console.error('[research-creds:clear] Error:', e.message);
      return { ok: false, error: 'unexpected' };
    }
  });

  /** 저장된 키를 사이드카에 다시 밀어넣는다(앱 시작·백엔드 재시작 후 복구용). */
  ipcMain.handle('research-creds:sync', () => pushCredentialsToSidecar());
}

module.exports = { registerResearchHandlers, pushCredentialsToSidecar };
