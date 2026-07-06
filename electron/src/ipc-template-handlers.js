/**
 * Template IPC Handlers
 * 책임: `template:*` 채널을 FastAPI 백엔드(/api/templates ...)로 프록시한다.
 *
 * 백엔드는 Electron 으로부터 AE_GENERATED_ROOT(userData 루트)를 이미 주입받았으므로,
 * 여기서는 요청을 그대로 HTTP 로 전달하고 응답 JSON 을 반환한다. 등록은 multipart 업로드
 * 대신 로컬 파일 경로를 백엔드에 넘긴다 (백엔드가 같은 워크스테이션에서 실행).
 *
 * 보안 노트(security.md): 모든 핸들러는 main 프로세스에서만 등록되며 ipcRenderer 는
 * 렌더러에 노출하지 않는다(preload 화이트리스트 경유). 네트워크 실패 시에도 렌더러가
 * unhandled rejection 을 받지 않도록 각 핸들러는 `{error: 'ipc-proxy-failed', detail}`
 * 형태의 JSON 으로 폴백한다.
 *
 * 설계 §구성요소 8 / 요구사항 1.1, 5.3, 8.1, 8.8
 */

const { ipcMain } = require('electron');

// FastAPI 베이스 URL. Electron 이 AE_ENGINE_URL 을 주입하면 그것을, 아니면 로컬 기본값을 사용.
const API = () => process.env.AE_ENGINE_URL || 'http://127.0.0.1:8765';

// 네트워크/파싱 실패를 렌더러로 전파하지 않도록 통일된 폴백 응답을 만든다.
function _proxyError(e) {
  return { error: 'ipc-proxy-failed', detail: String(e).slice(0, 200) };
}

/**
 * 템플릿 IPC 핸들러 등록 (main 프로세스 전용).
 * @param {BrowserWindow} [mainWindow] - 호출부 일관성을 위한 인자 (현재 미사용).
 */
function registerTemplateHandlers(mainWindow) { // eslint-disable-line no-unused-vars
  /**
   * 템플릿 등록 — 로컬 파일 경로 + 이름을 백엔드에 POST.
   * @param {{filePath: string, name: string}} payload
   * @returns {Promise<object>} 등록 결과 JSON 또는 ipc-proxy-failed
   */
  ipcMain.handle('template:register', async (_evt, { filePath, name } = {}) => {
    try {
      const r = await fetch(`${API()}/api/templates`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ filePath, name }),
      });
      return await r.json();
    } catch (e) {
      return _proxyError(e);
    }
  });

  /**
   * 템플릿 목록 조회 (createdAt 내림차순, 최대 200개는 백엔드가 보장).
   * @returns {Promise<object>} {templates: [...]} 또는 ipc-proxy-failed
   */
  ipcMain.handle('template:list', async () => {
    try {
      const r = await fetch(`${API()}/api/templates`);
      return await r.json();
    } catch (e) {
      return _proxyError(e);
    }
  });

  /**
   * 단건 템플릿 조회.
   * @param {string} id - templateId
   * @returns {Promise<object>} 템플릿 JSON 또는 ipc-proxy-failed
   */
  ipcMain.handle('template:get', async (_evt, id) => {
    try {
      const r = await fetch(`${API()}/api/templates/${encodeURIComponent(id)}`);
      return await r.json();
    } catch (e) {
      return _proxyError(e);
    }
  });

  /**
   * Style_Profile 조회 (백엔드가 매 호출 바이트 동일 보장).
   * @param {string} id - templateId
   * @returns {Promise<object>} styleProfile JSON 또는 ipc-proxy-failed
   */
  ipcMain.handle('template:get-style-profile', async (_evt, id) => {
    try {
      const r = await fetch(`${API()}/api/templates/${encodeURIComponent(id)}/style-profile`);
      return await r.json();
    } catch (e) {
      return _proxyError(e);
    }
  });

  /**
   * 템플릿 삭제.
   * @param {string} id - templateId
   * @returns {Promise<object>} {ok, templateId} 또는 ipc-proxy-failed
   */
  ipcMain.handle('template:delete', async (_evt, id) => {
    try {
      const r = await fetch(`${API()}/api/templates/${encodeURIComponent(id)}`, { method: 'DELETE' });
      return await r.json();
    } catch (e) {
      return _proxyError(e);
    }
  });
}

module.exports = { registerTemplateHandlers };
