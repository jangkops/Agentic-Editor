/**
 * Data Store IPC Handlers
 * 책임: 설정, 사용량, 이력, 체크포인트, 스킬 등 데이터 저장소 관리
 */

const { ipcMain } = require('electron');
const path = require('path');
const fs = require('fs');

/**
 * Store IPC 핸들러 등록
 * @param {DataStore} dataStore - 데이터 저장소 인스턴스
 */
function registerStoreHandlers(dataStore) {
  /**
   * 설정 로드
   */
  ipcMain.handle('store:load-settings', () => {
    try {
      return dataStore.loadSettings();
    } catch (error) {
      console.error('[store:load-settings] Error:', error.message);
      return {};
    }
  });

  /**
   * 설정 저장
   */
  ipcMain.handle('store:save-settings', (_, settings) => {
    try {
      return dataStore.saveSettings(settings);
    } catch (error) {
      console.error('[store:save-settings] Error:', error.message);
      return false;
    }
  });

  /**
   * 사용량 로드
   */
  ipcMain.handle('store:load-usage', () => {
    try {
      return dataStore.loadUsage();
    } catch (error) {
      console.error('[store:load-usage] Error:', error.message);
      return {};
    }
  });

  /**
   * 사용량 업데이트 (토큰 누적)
   */
  ipcMain.handle('store:update-usage', (_, tokens) => {
    try {
      return dataStore.updateUsage(tokens);
    } catch (error) {
      console.error('[store:update-usage] Error:', error.message);
      return false;
    }
  });

  /**
   * 이력 저장 (날짜별)
   */
  ipcMain.handle('store:save-history', (_, date, messages) => {
    try {
      return dataStore.saveHistory(date, messages);
    } catch (error) {
      console.error('[store:save-history] Error:', error.message);
      return false;
    }
  });

  /**
   * 체크포인트 저장
   * @param {IpcMainInvokeEvent} _ - IPC 이벤트
   * @param {string} workflowId - 워크플로우 ID
   * @param {object} state - 저장할 상태
   * @returns {boolean} 성공 여부
   */
  ipcMain.handle('store:save-checkpoint', (_, workflowId, state) => {
    try {
      const cpPath = path.join(dataStore.basePath, 'checkpoints');
      const cpFile = path.join(cpPath, `${workflowId}.json`);
      fs.mkdirSync(cpPath, { recursive: true });
      fs.writeFileSync(
        cpFile,
        JSON.stringify({ workflow_id: workflowId, state }, null, 2),
        'utf-8'
      );
      return true;
    } catch (error) {
      console.error(`[store:save-checkpoint] Error for ${workflowId}:`, error.message);
      return false;
    }
  });

  /**
   * 체크포인트 로드
   * @param {IpcMainInvokeEvent} _ - IPC 이벤트
   * @param {string} workflowId - 워크플로우 ID
   * @returns {object|null} 상태 또는 null
   */
  ipcMain.handle('store:load-checkpoint', (_, workflowId) => {
    try {
      const cpFile = path.join(dataStore.basePath, 'checkpoints', `${workflowId}.json`);
      if (!fs.existsSync(cpFile)) return null;
      const data = JSON.parse(fs.readFileSync(cpFile, 'utf-8'));
      return data.state || null;
    } catch (error) {
      console.error(`[store:load-checkpoint] Error for ${workflowId}:`, error.message);
      return null;
    }
  });

  /**
   * 스킬 로드 (모두)
   */
  ipcMain.handle('store:load-skills', () => {
    try {
      return dataStore.loadSkills();
    } catch (error) {
      console.error('[store:load-skills] Error:', error.message);
      return [];
    }
  });

  /**
   * 스킬 저장
   */
  ipcMain.handle('store:save-skill', (_, skill) => {
    try {
      return dataStore.saveSkill(skill);
    } catch (error) {
      console.error('[store:save-skill] Error:', error.message);
      return false;
    }
  });

  /**
   * 스킬 삭제
   */
  ipcMain.handle('store:delete-skill', (_, skillId) => {
    try {
      return dataStore.deleteSkill(skillId);
    } catch (error) {
      console.error('[store:delete-skill] Error:', error.message);
      return false;
    }
  });

  /**
   * Denied models — Gateway 호출 실패 학습 결과 영속 저장
   */
  ipcMain.handle('store:load-denied-models', () => {
    try {
      return dataStore.loadDeniedModels();
    } catch (error) {
      console.error('[store:load-denied-models] Error:', error.message);
      return [];
    }
  });

  ipcMain.handle('store:add-denied-model', (_, modelId) => {
    try {
      dataStore.addDeniedModel(modelId);
      return true;
    } catch (error) {
      console.error('[store:add-denied-model] Error:', error.message);
      return false;
    }
  });

  ipcMain.handle('store:clear-denied-models', () => {
    try {
      dataStore.clearDeniedModels();
      return true;
    } catch (error) {
      console.error('[store:clear-denied-models] Error:', error.message);
      return false;
    }
  });
}

module.exports = { registerStoreHandlers };
