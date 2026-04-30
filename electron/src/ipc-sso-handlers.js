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
      let latestExpiry = null;

      for (const file of files) {
        try {
          const data = JSON.parse(fs.readFileSync(path.join(ssoDir, file), 'utf-8'));
          if (data.expiresAt) {
            const expiry = new Date(data.expiresAt);
            if (!latestExpiry || expiry > latestExpiry) {
              latestExpiry = expiry;
            }
          }
        } catch (err) {
          // 개별 파일 파싱 실패는 무시
        }
      }

      return latestExpiry ? latestExpiry.toISOString() : null;
    } catch (error) {
      console.error('[sso:get-expiry] Error:', error.message);
      return null;
    }
  });
}

module.exports = { registerSsoHandlers };
