/**
 * AWS SSO IPC Handlers
 * 책임: SSO 프로필 관리, 로그인, 자격증명, 만료 시간 등
 */

const { ipcMain } = require('electron');
const fs = require('fs');
const path = require('path');
const os = require('os');

/**
 * SSO IPC 핸들러 등록
 * @param {AwsSsoManager} ssoManager - SSO 관리자 인스턴스
 */
function registerSsoHandlers(ssoManager) {
  /**
   * SSO 프로필 목록
   */
  ipcMain.handle('sso:list-profiles', () => {
    try {
      return ssoManager.listProfiles();
    } catch (error) {
      console.error('[sso:list-profiles] Error:', error.message);
      return [];
    }
  });

  /**
   * SSO 로그인
   */
  ipcMain.handle('sso:login', async (_, profile) => {
    try {
      return await ssoManager.login(profile);
    } catch (error) {
      console.error(`[sso:login] Error for ${profile}:`, error.message);
      return false;
    }
  });

  /**
   * SSO 자격증명 가져오기
   */
  ipcMain.handle('sso:get-credentials', async (_, profile) => {
    try {
      return await ssoManager.getCredentials(profile);
    } catch (error) {
      console.error(`[sso:get-credentials] Error for ${profile}:`, error.message);
      return null;
    }
  });

  /**
   * Bedrock 사용자명 가져오기
   */
  ipcMain.handle('sso:get-bedrock-username', async (_, profile) => {
    try {
      return await ssoManager.getBedrockUsername(profile);
    } catch (error) {
      console.error(`[sso:get-bedrock-username] Error for ${profile}:`, error.message);
      return null;
    }
  });

  // 수동 입력 BedrockUser 이름을 저장 전 assume-role로 검증 (타인 계정 도용 방지).
  // 반환: { ok: boolean, reason?: string }
  ipcMain.handle('sso:verify-bedrock-username', async (_, profile, name) => {
    try {
      return await ssoManager.verifyBedrockUsername(profile, name);
    } catch (error) {
      console.error(`[sso:verify-bedrock-username] Error for ${profile}/${name}:`, error.message);
      return { ok: false, reason: (error && error.message) || 'error' };
    }
  });

  /**
   * SSO 토큰 만료 시간 가져오기
   * AWS SSO 캐시 파일에서 읽음
   * @param {IpcMainInvokeEvent} _ - IPC 이벤트
   * @param {string} profile - SSO 프로필명
   * @returns {string|null} ISO 형식 만료 시간 또는 null
   */
  ipcMain.handle('sso:get-expiry', (_, profile) => {
    try {
      const ssoDir = path.join(os.homedir(), '.aws', 'sso', 'cache');
      if (!fs.existsSync(ssoDir)) {
        return null;
      }

      const files = fs.readdirSync(ssoDir).filter((f) => f.endsWith('.json'));
      const now = Date.now();
      // 캐시 디렉터리에는 두 종류의 파일이 섞여 있다:
      //  (1) SSO 액세스 토큰 캐시 — accessToken + expiresAt(세션 만료, 보통 8~12시간)
      //  (2) 클라이언트 등록 캐시 — clientId/clientSecret + expiresAt(등록 만료, 최대 ~90일)
      // 과거에는 모든 파일의 expiresAt 중 '최댓값'을 취해 (2)의 90일 만료가 잡혀
      // 남은 시간이 1993h 처럼 비정상적으로 표시됐다. 실제 세션 잔여 시간은 (1)에만 있으므로
      // accessToken 을 가진 토큰 캐시 파일만 대상으로 하고, 그중 '가장 가까운 미래 만료'를
      // 현재 활성 세션의 만료로 사용한다(미래가 없으면 가장 최근 만료 = 이미 만료된 세션).
      let soonestFuture = null;
      let latestPast = null;

      for (const file of files) {
        try {
          const data = JSON.parse(fs.readFileSync(path.join(ssoDir, file), 'utf-8'));
          // 표준 AWS SSO 토큰 캐시만 인정한다:
          //  - accessToken + expiresAt: 세션 토큰(등록 캐시는 accessToken 이 없어 제외)
          //  - startUrl: botocore SSO 토큰 파일에만 존재. Kiro 등 비-SSO 토큰
          //    (kiro-auth-token.json — authMethod/provider 키, startUrl 없음)을 배제한다.
          if (!data.accessToken || !data.expiresAt || !data.startUrl) continue;
          const expiry = new Date(data.expiresAt);
          if (Number.isNaN(expiry.getTime())) continue;
          if (expiry.getTime() > now) {
            if (!soonestFuture || expiry < soonestFuture) soonestFuture = expiry;
          } else {
            if (!latestPast || expiry > latestPast) latestPast = expiry;
          }
        } catch (err) {
          // 개별 파일 파싱 실패는 무시
        }
      }

      const chosen = soonestFuture || latestPast;
      return chosen ? chosen.toISOString() : null;
    } catch (error) {
      console.error('[sso:get-expiry] Error:', error.message);
      return null;
    }
  });
}

module.exports = { registerSsoHandlers };
