/* ===== Mogam Works — Main ===== */
const _sessionStart = Date.now();
let _ssoExpiry = null;

async function loadSSOExpiry() {
  if (window.electronAPI?.getSSOExpiry) {
    try {
      const exp = await window.electronAPI.getSSOExpiry(state.settings?.awsProfile || '');
      if (exp) _ssoExpiry = new Date(exp);
    } catch {}
  }
}

// fmtNum(n): moved to src/lib/utils.js (Phase 1 refactor)






// 테마 전환
let _currentTheme = 'dark';
function applyTheme(theme) {
  _currentTheme = theme;
  if (theme === 'light') {
    document.documentElement.setAttribute('data-theme', 'light');
    if (monacoEditor && window.monaco) window.monaco.editor.setTheme('vs');
  } else {
    document.documentElement.removeAttribute('data-theme');
    if (monacoEditor && window.monaco) window.monaco.editor.setTheme('vs-dark');
  }
}

const MODEL_CATALOG = {};
const ALL_MODELS = [];
const _deniedModels = new Set(); // 403 model_denied된 모델 ID 캐시
function rebuildModelList() {
  ALL_MODELS.length = 0;
  for (const [p, ms] of Object.entries(MODEL_CATALOG)) ms.forEach(m => ALL_MODELS.push({ ...m, provider: p }));
}

// ===== gateway-openai-models Task 9.1: 선택 보존/복구 순수 함수 =====
// DOM/타이머와 분리된 순수 로직. 자동 모델 새로고침(9.2) 시 선택 보존/복구에 사용.
// 카탈로그 형태는 MODEL_CATALOG와 동일: { provider: [ {id, name, capabilities, ...}, ... ] }

// 카탈로그를 표시 정렬 순서(provider 순 → 모델 순)대로 평탄화한다.
// 각 모델에 provider를 부착해 반환한다. (순수 — 입력 불변)
function _flattenCatalogForSelection(catalog) {
  const out = [];
  if (!catalog || typeof catalog !== 'object') return out;
  for (const [p, ms] of Object.entries(catalog)) {
    if (!Array.isArray(ms)) continue;
    for (const m of ms) {
      if (m && m.id != null) out.push({ ...m, provider: p });
    }
  }
  return out;
}

// catalogSignature(catalog): 비교용 시그니처 문자열.
// 모델 id 집합 기반(중복 제거 + 정렬)으로 결정론적 문자열을 만든다.
// 의미상 동일한(동일 id 집합) 두 카탈로그는 항상 동일 시그니처를 갖는다.
function catalogSignature(catalog) {
  const ids = [];
  if (catalog && typeof catalog === 'object') {
    for (const ms of Object.values(catalog)) {
      if (!Array.isArray(ms)) continue;
      for (const m of ms) {
        if (m && m.id != null) ids.push(String(m.id));
      }
    }
  }
  return Array.from(new Set(ids)).sort().join('\u0001');
}

// resolveSelection(prevId, prevCatalog, nextCatalog): 결정적 선택 규칙.
// 반환: 선택할 모델 객체(provider 부착) 또는 null.
//  1) prevCatalog와 nextCatalog의 id 집합이 동일하면 prevId 유지(선택 불변)
//  2) prevId가 nextCatalog에 존재하면 그 모델 유지
//  3) prevId가 부재하면 채팅 가능 모델(capabilities.chat) 중 표시 정렬상 첫 번째,
//     없으면 첫 모델, 그것도 없으면 null
function resolveSelection(prevId, prevCatalog, nextCatalog) {
  const nextModels = _flattenCatalogForSelection(nextCatalog);
  // 1) 동일 카탈로그 → 선택 불변(prevId가 다음 목록에 있으면 그대로 유지)
  if (catalogSignature(prevCatalog) === catalogSignature(nextCatalog)) {
    const keep = nextModels.find(m => m.id === prevId);
    if (keep) return keep;
  }
  // 2) prevId가 다음 카탈로그에 존재 → 유지
  const still = nextModels.find(m => m.id === prevId);
  if (still) return still;
  // 3) 부재 → 채팅 가능 모델 중 첫 번째 → 첫 모델 → null
  const chat = nextModels.filter(m => m.capabilities && m.capabilities.chat);
  if (chat.length) return chat[0];
  if (nextModels.length) return nextModels[0];
  return null;
}

// PBT(Property 8) 및 9.2 스케줄러에서 호출 가능하도록 노출
if (typeof window !== 'undefined') {
  window.resolveSelection = resolveSelection;
  window.catalogSignature = catalogSignature;
}
// 일시적 호출 실패 시 호출 — 모델 제거는 하지 않음 (영구 denylist 금지)
// 게이트웨이 일시 에러/quota/max_tokens 한계 등은 자동 복구 가능하므로
// 한 번 실패했다고 모델을 영구 제거하면 안 됨. 사용자가 다시 시도할 수 있어야 함.
function _removeModelFromCatalog(modelId) {
  const cleanId = String(modelId || '').replace(/^us\.|^eu\.|^global\./, '');
  // no-op — 로그만 남기고 카탈로그는 절대 변경하지 않음
  console.log(`[Model] ${cleanId} 일시 호출 실패 (목록 유지 — 재시도 가능)`);
}

// 앱 시작 시 디스크 denylist를 항상 비움 — denylist 기능은 비활성화
// 게이트웨이 일시 에러/quota/한계 변경은 영구 차단 사유가 아님
async function _loadDeniedModelsFromDisk() {
  try {
    // 디스크 denylist가 남아있으면 항상 클리어
    try { await window.electronAPI?.clearDeniedModels?.(); } catch (_) {}
    _deniedModels.clear();
    console.log('[Model] denylist 비활성화 — 모든 모델 호출 가능');
  } catch (err) {
    console.warn('[Model] denylist 클리어 실패:', err?.message || err);
  }
}
rebuildModelList();

// ===== Fix 5: 기본 스킬도 편집 가능하게 =====
let allSkills = [];

// ===== Fix 4: 대화 세션 탭 =====
let chatSessions = [{ id:'s-'+Date.now(), name:'대화 1', messages:[] }];
let activeSessionIdx = 0;

const state = {
  mode:'single', selectedModel: null,
  get messages() { return chatSessions[activeSessionIdx]?.messages || []; },
  set messages(v) { if(chatSessions[activeSessionIdx]) chatSessions[activeSessionIdx].messages = v; },
  parallelResults:new Map(),
  // Fix 4: Array로 변경 — 동일 모델 중복 선택 지원. 각 항목: {slotId, modelId, skillId, customRole}
  parallelSlots:[],
  isStreaming:false, folderPath:'', openTabs:[], activeTab:null,
  terminals:[], activeTerminalIdx:0,
  usageData:{inputTokens:0,outputTokens:0,cost:0,history:[]},
  settings:null, authenticated:false, attachedFiles:[],
  // 합의 모델 잠금: 합의 도출 후 사용자가 '이 모델로 계속 대화' 선택 시 단일 모드로 전환되며 기억되는 모델
  lastConsensusModel:null,
  // 활성 PPTX 템플릿 — <template-panel> 선택 시 갱신. '' → 무템플릿 (pptx-template-styling 요구사항 5.6)
  activeTemplateId:'',
};


// === user 메시지 핀 유틸 (sendChat ↔ 종료 지점 공통) ===
function _releaseUserPin(){
  state._pinAnchorSet = false;
  state._pinUserMsgIdx = -1;
  state._pinSpacerPx = 0;
  try {
    const cc = document.getElementById('chat-messages');
    if(cc){
      cc.style.paddingBottom = '';
      cc.style.scrollPaddingBottom = '';
    }
  } catch(_) {}
}

// Remote indicator helper — updates sidebar "Remote ●" and status bar
function _updateRemoteIndicator(state) {
  const ind = document.getElementById('remote-indicator');
  if (!ind) return;
  const connecting = ['connecting', 'authenticating', 'provisioning', 'forwarding', 'reconnecting'].includes(state);
  const connected = state === 'connected';
  if (connecting) {
    ind.style.display = 'inline';
    ind.style.color = 'var(--color-warning)';
    ind.style.animation = 'pulse 1s infinite';
  } else if (connected) {
    ind.style.display = 'inline';
    ind.style.color = 'var(--color-success)';
    ind.style.animation = 'none';
  } else {
    ind.style.display = 'none';
    ind.style.animation = 'none';
  }
}

// ===========================================================================
// Remote SSH — renderer-side status cache (Task 23.1 · Req 5.3, 5.5)
// ===========================================================================
// `apiBase()` in src/lib/utils.js reads `window.__remoteStatus` to decide
// whether fetch() should route through the local default (http://localhost:8765)
// or the SSH-forwarded port of the active remote session. We keep that global
// in sync by subscribing to the main-process state stream exposed via preload
// (`electronAPI.onRemoteState`). The subscription is best-effort: if preload
// hasn't wired the IPC yet (older builds, tests, local-only mode) we simply
// fall back to the local default — no fetch path breaks.
(function _wireRemoteStatusCache() {
  try {
    if (typeof window === 'undefined' || !window.electronAPI) return;
    // Freshen the cache at boot in case a session is already connected when
    // main.js starts (e.g. after a renderer reload while the main process
    // kept the session). remoteStatus() is a cheap invoke — safe to await.
    if (typeof window.electronAPI.remoteStatus === 'function') {
      Promise.resolve(window.electronAPI.remoteStatus()).then((s) => {
        if (s && typeof s === 'object') {
          // `remoteStatus()` returns a flat map `{[alias]: {state, localPort}}`
          // plus `_active`/`_apiBase`. Normalize to the event shape used by
          // apiBase()/renderTerminalTabs(): `{alias, state, localPort}`.
          const active = s._active;
          if (active && s[active]) {
            const a = s[active];
            window.__remoteStatus = {
              alias: active,
              state: a.state,
              localPort: a.localPort || null,
            };
            if (typeof renderTerminalTabs === 'function') renderTerminalTabs();
          } else {
            window.__remoteStatus = null;
          }
        }
      }).catch(() => { /* ignore — stays on local default */ });
    }
    // Live updates: every state transition from the main-process session
    // manager (disconnected / connecting / connected / reconnecting / failed)
    // replaces the cache. Disconnect / failed explicitly clears it so that
    // apiBase() reverts to localhost:8765 immediately.
    if (typeof window.electronAPI.onRemoteState === 'function') {
      window.electronAPI.onRemoteState((evt) => {
        if (!evt || typeof evt !== 'object') {
          window.__remoteStatus = null;
          if (typeof renderTerminalTabs === 'function') renderTerminalTabs();
          return;
        }
        const st = evt.state || evt.to;
        if (st === 'disconnected' || st === 'failed') {
          // Don't clear status if we're already connected and this is a transient event
          // (e.g. background provisioning failure while session is still alive)
          if (window.__remoteStatus && window.__remoteStatus.state === 'connected' && st === 'failed') {
            // Suppress — session is still connected, this is a background error
            return;
          }
          window.__remoteStatus = null;
          _updateRemoteIndicator(st);
          const pathBar = document.getElementById('remote-path-bar');
          if (pathBar) pathBar.style.display = 'none';
        } else {
          // Merge with any existing enrichment (hostName/user) from
          // onRemoteConnected so terminal labels keep the host info.
          const prev = window.__remoteStatus && window.__remoteStatus.alias === evt.alias
            ? window.__remoteStatus : {};
          window.__remoteStatus = { ...prev, ...evt, state: st };
        }
        if (typeof renderTerminalTabs === 'function') renderTerminalTabs();
        // Update sidebar Remote indicator
        _updateRemoteIndicator(st);
      });
    }
    // Enrich cache with hostName/user on connect so terminal tabs can show
    // "user@host" per the VS Code Remote-SSH convention.
    if (typeof window.electronAPI.onRemoteConnected === 'function') {
      window.electronAPI.onRemoteConnected((ev) => {
        if (!ev || !ev.alias) return;
        window.__remoteStatus = {
          alias: ev.alias,
          state: 'connected',
          localPort: ev.localPort || null,
          hostName: ev.hostName || '',
          user: ev.user || '',
          remoteHome: ev.remoteHome || '',
          workspace: ev.workspace || '',
        };
        if (typeof renderTerminalTabs === 'function') renderTerminalTabs();
        // Show remote path bar for direct path navigation
        const pathBar = document.getElementById('remote-path-bar');
        if (pathBar) {
          pathBar.style.display = 'block';
          const pathInput = document.getElementById('remote-path-input');
          if (pathInput) pathInput.value = ev.workspace || ev.remoteHome || '/';
        }
        _updateRemoteIndicator('connected');
        // Kill existing local terminals and create a fresh remote terminal
        if (typeof state !== 'undefined' && state.terminals && state.terminals.length > 0) {
          for (const t of state.terminals) {
            try { window.electronAPI?.terminalKill?.(t.id); } catch (_e) {}
          }
          state.terminals = [];
          state.activeTerminalIdx = 0;
          setTimeout(() => {
            if (typeof addTerminal === 'function') addTerminal();
          }, 300);
        }
      });
    }
  } catch (_e) { /* never let status wiring crash renderer init */ }
})();

// ===== Fix 1: SSO — select 드롭다운으로 프로파일 선택 =====
document.addEventListener('DOMContentLoaded', async () => {
  if (window.electronAPI?.loadSettings) state.settings = await window.electronAPI.loadSettings();
  if (!state.settings?.awsProfile) {
    // R4.1: 저장된 프로파일이 없고 ~/.aws/config에도 SSO_Profile이 하나도 없으면(빈 목록)
    // 로그인 다이얼로그 대신 온보딩 화면을 먼저 표시한다. 목록이 있으면 기존 로그인 흐름 유지.
    let profiles = [];
    if (window.electronAPI?.listProfiles) { try { profiles = await window.electronAPI.listProfiles(); } catch {} }
    if (!profiles.length) { showOnboardingDialog(); return; }
    showSSODialog(true); return;
  }
  // 기존 자격증명 유효성 검증
  if (window.electronAPI?.getCredentials) {
    try {
      const creds = await window.electronAPI.getCredentials(state.settings.awsProfile);
      if (!creds || !creds.AWS_ACCESS_KEY_ID) {
        // 자격증명 만료 — 재로그인 필요
        showSSODialog(true);
        return;
      }
    } catch {
      showSSODialog(true);
      return;
    }
  }
  state.authenticated = true; initApp();
});

async function initApp() {
  state.authenticated = true;
  state._appInitialized = true;
  // Fetch Python server cwd so file-preview-panel knows where .generated/ lives
  try {
    const r = await fetch(`${apiBase()}/api/debug/cwd`);
    if (r.ok) {
      const j = await r.json();
      // generatedRoot 우선 (실제 .generated/ 부모 — userData/generated/ 또는 ~/.agentic-editor/)
      const root = j.generatedRoot || j.cwd;
      if (root) window.__workstationCwd = root;
    }
  } catch {}
  // bedrockUser 자동 감지 (설정에 없으면)
  if (!state.settings.bedrockUser && window.electronAPI?.getBedrockUsername) {
    try {
      const bu = await window.electronAPI.getBedrockUsername(state.settings.awsProfile);
      if (bu) { state.settings.bedrockUser = bu; await window.electronAPI?.saveSettings?.(state.settings); }
    } catch {}
  }
  // 저장된 스킬 로드
  if (window.electronAPI?.loadSkills) {
    try {
      const saved = await window.electronAPI.loadSkills();
      if (saved && saved.length) {
        allSkills = saved.map(sk => ({ ...sk, builtin: false }));
      }
    } catch {}
  }
  // 저장된 대화 세션 복원
  try {
    if (window.electronAPI?.readFile && window.electronAPI?.getUserDataPath) {
      const udp = await window.electronAPI.getUserDataPath();
      const sessData = await window.electronAPI.readFile(udp + '/settings/chat-sessions.json');
      if (sessData) {
        const parsed = JSON.parse(sessData);
        if (parsed.sessions && parsed.sessions.length) {
          chatSessions = parsed.sessions;
          activeSessionIdx = parsed.activeIdx || 0;
          if (activeSessionIdx >= chatSessions.length) activeSessionIdx = 0;
        }
      }
    }
  } catch {}
  initModelDropdown(); initModeToggle(); initChat(); initFileExplorer();
  initGithubImport(); initSkills(); initTerminal(); initMonaco(); initTopbar();
  initChatTabs(); checkBackend();
  // 디스크 denylist를 먼저 로드 → 이후 모델 로드 시 필터 적용
  await _loadDeniedModelsFromDisk();
  // 자격증명을 백엔드에 주입 (quota 조회 등에서 사용)
  try {
    if (window.electronAPI?.getCredentials && state.settings?.awsProfile) {
      const creds = await window.electronAPI.getCredentials(state.settings.awsProfile);
      if (creds && creds.AWS_ACCESS_KEY_ID) {
        await fetch(`${apiBase()}/api/reset-cache`, {
          method: 'POST', headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({
            profile: state.settings.awsProfile,
            bedrockUser: state.settings.bedrockUser || '',
            credentials: creds,
          }),
        });
      }
    }
  } catch {}
  await loadModelsFromServer();
  // gateway-openai-models 9.2: 초기 로드 직후 주기적 자동 새로고침 스케줄러 시작
  // (게이트웨이에 GPT 등 모델 추가 시 선택 보존하며 목록 자동 반영)
  try { startModelRefreshScheduler(); } catch(e) { console.warn('[initApp] 모델 새로고침 스케줄러 시작 실패:', e?.message || e); }
  // quota 조회 — loadModelsFromServer 완료 후 즉시 실행
  console.log('[initApp] loadModelsFromServer 완료, updateQuotaBar 직접 호출');
  try { updateQuotaBar(); } catch(e) { console.error('[initApp] updateQuotaBar 에러:', e); }
  try { loadUsageData(); } catch(e) { console.error('[initApp] loadUsageData 에러:', e); }
  console.log('[initApp] quota+usage 호출 완료');
  loadSavedConsensusHistory();
  initCenterViews();
  // 모델이 없으면 (initApp이 DOMContentLoaded에서 직접 호출된 경우) 로그인 필요
  if (ALL_MODELS.length === 0 && document.getElementById('sso-dialog').style.display !== 'block') {
    showSSODialog(true);
  }
  // SSO 세션 만료 타이머
  loadSSOExpiry();

  setInterval(() => {
    const el = document.getElementById('topbar-session-info');
    const fill = document.getElementById('session-bar-fill');
    const pctEl = document.getElementById('session-bar-pct');
    const gauge = document.getElementById('topbar-session-gauge');

    // 앱 경과 시간
    const elapsed = Date.now() - _sessionStart;
    const eMins = Math.floor(elapsed / 60000);
    const eSecs = Math.floor((elapsed % 60000) / 1000);
    const eHrs = Math.floor(eMins / 60);
    const eTimeStr = eHrs > 0 ? `${eHrs}h ${eMins % 60}m` : `${eMins}m`;
    if (el) el.textContent = eTimeStr;

    // SSO 만료
    if (_ssoExpiry) {
      const remaining = _ssoExpiry.getTime() - Date.now();
      if (remaining <= 0) {
        if (fill) { fill.style.width = '100%'; fill.style.background = 'var(--color-error)'; }
        if (pctEl) pctEl.textContent = '만료';
        if (gauge) gauge.title = `앱 경과: ${eHrs > 0 ? eHrs + '시간 ' : ''}${eMins % 60}분\nSSO 세션 만료됨 — 재로그인 필요`;
      } else {
        const remMins = Math.floor(remaining / 60000);
        const remHrs = Math.floor(remMins / 60);
        const remM = remMins % 60;
        // 로그인 시점부터 만료까지의 진행률 (0% = 방금 로그인, 100% = 만료)
        const totalSession = 12 * 60 * 60 * 1000; // 12시간 기준
        const used = totalSession - remaining;
        const pct = Math.max(0, Math.min(100, (used / totalSession) * 100));
        if (fill) {
          fill.style.width = pct.toFixed(0) + '%';
          fill.style.background = remaining < 30 * 60 * 1000 ? 'var(--color-error)' : remaining < 2 * 60 * 60 * 1000 ? 'var(--color-warning)' : 'var(--color-success)';
        }
        if (pctEl) pctEl.textContent = `${remHrs}h`;
        const expiryTime = _ssoExpiry.toLocaleTimeString('ko-KR', { hour: '2-digit', minute: '2-digit' });
        if (gauge) gauge.title = `앱 경과: ${eHrs > 0 ? eHrs + '시간 ' : ''}${eMins % 60}분 ${eSecs}초\nSSO 만료 예정: ${expiryTime} (${remHrs}시간 ${remM}분 남음)`;
      }
    } else {
      if (fill) fill.style.width = '0%';
      if (pctEl) pctEl.textContent = '-';
      if (gauge) gauge.title = `앱 경과: ${eMins}분 ${eSecs}초\nSSO 만료 정보 없음`;
    }
  }, 5000);
  document.getElementById('topbar-model-count').textContent = `${ALL_MODELS.length}개 모델`;
}

// ===== R4: Onboarding_Flow — SSO_Profile이 하나도 없을 때 최초 구성 화면 =====
// listProfiles()가 빈 배열일 때만 부팅 흐름에서 호출된다. SSO_Profile이 생성되기
// 전까지는 이 모달을 닫을 수 없어(외부 클릭·닫기 버튼 없음) 게이트웨이 기능 진입을
// 차단한다(R4.3). 자격증명은 저장하지 않고 SSO 메타데이터만 ~/.aws/config에 기록한다(R4.6).
async function showOnboardingDialog() {
  const o = document.getElementById('sso-dialog'); o.style.display = 'block';
  const inputStyle = 'width:100%;padding:10px 14px;background:var(--color-bg-input);border:1px solid var(--color-border);border-radius:var(--radius-md);color:var(--color-text-primary);font-size:13px;outline:none';
  const muted = 'font-size:10px;color:var(--color-text-muted)';

  // 조직 기본 SSO 프리셋 — 고급 폼 프리필용. 매니저의 DEFAULT_SSO_PRESET과 값이 동기화되어야 한다
  // (aws-sso-manager.js DEFAULT_SSO_PRESET / AE_SSO_* override). 일반 사용자는 이 값을 보지 않으며,
  // 실제 자동 생성은 백엔드 resolveDefaultSsoPreset(process.env)가 env override까지 반영해 수행한다.
  const ORG_PRESET = {
    name: 'bedrock-gw',
    startUrl: 'https://d-906617189d.awsapps.com/start',
    region: 'us-east-1',
    accountId: '107650139384',
    roleName: 'ViewOnlyAccess',
  };

  // zero-config 온보딩 — 최종 사용자는 아무 값도 입력하지 않는다. 진입 즉시 조직 기본 프로파일을
  // 자동 생성하고, 성공하면 곧바로 로그인 다이얼로그(showSSODialog)로 진입한다. "다른 조직/관리자
  // 설정"은 접을 수 있는 고급 토글로 제공하며, 자동 생성 실패 시 폴백 수동 구성으로 펼쳐진다.
  // SSO_Profile이 생성되기 전까지 닫을 수 없는 고정 모달(overlay click 무효, R4.3).
  o.innerHTML = `<div class="overlay"><div class="dialog" style="position:relative;max-width:460px">
    <div class="dialog-icon">◆</div><h2>Mogam Works 시작</h2>
    <div class="subtitle" id="ob-subtitle">조직 기본 설정을 준비하는 중입니다...</div>
    <div class="status-text" id="ob-status"></div>

    <button class="btn-primary" id="ob-login-btn" style="display:none">로그인</button>

    <div id="ob-advanced-toggle" style="margin-top:16px;font-size:11px;color:var(--color-text-secondary);cursor:pointer;user-select:none;display:flex;align-items:center;gap:6px">
      <span id="ob-advanced-caret" style="display:inline-block;transition:transform var(--transition, 150ms ease)">▸</span>
      <span>다른 조직 / 관리자 설정</span>
    </div>

    <div id="ob-advanced" style="display:none;margin-top:12px;border-top:1px solid var(--color-border);padding-top:12px">
      <div class="subtitle" style="margin-top:0">SSO 프로파일을 직접 구성합니다</div>
      <label>프로파일 이름 <span style="${muted}">(예: bedrock-gw)</span></label>
      <input type="text" id="ob-name" value="${ORG_PRESET.name}" placeholder="bedrock-gw" style="${inputStyle}">
      <label style="margin-top:12px">SSO Start URL</label>
      <input type="text" id="ob-start-url" value="${ORG_PRESET.startUrl}" placeholder="https://your-org.awsapps.com/start" style="${inputStyle}">
      <label style="margin-top:12px">Region</label>
      <input type="text" id="ob-region" value="${ORG_PRESET.region}" placeholder="us-east-1" style="${inputStyle}">
      <label style="margin-top:12px">Account ID <span style="${muted}">(숫자 12자리)</span></label>
      <input type="text" id="ob-account-id" value="${ORG_PRESET.accountId}" placeholder="123456789012" style="${inputStyle}">
      <label style="margin-top:12px">Role <span style="${muted}">(일반 사용자: ViewOnlyAccess)</span></label>
      <select id="ob-role-name" style="${inputStyle}">
        <option value="ViewOnlyAccess" selected>ViewOnlyAccess (일반 사용자)</option>
        <option value="AdministratorAccess">AdministratorAccess (관리자)</option>
      </select>
      <button class="btn-primary" id="ob-submit-btn" style="margin-top:14px">프로파일 생성</button>
      <div id="ob-manual-hint" style="display:none;margin-top:10px;font-size:11px;color:var(--color-text-muted);white-space:pre-wrap;background:var(--color-bg-input);border:1px solid var(--color-border);border-radius:var(--radius-md);padding:10px"></div>
    </div>
  </div></div>`;

  const st = o.querySelector('#ob-status');
  const subtitle = o.querySelector('#ob-subtitle');
  const loginBtn = o.querySelector('#ob-login-btn');
  const advToggle = o.querySelector('#ob-advanced-toggle');
  const advPanel = o.querySelector('#ob-advanced');
  const advCaret = o.querySelector('#ob-advanced-caret');

  // 고급 섹션 토글 (펼침/접힘)
  const expandAdvanced = (open) => {
    const willOpen = open === undefined ? advPanel.style.display === 'none' : open;
    advPanel.style.display = willOpen ? 'block' : 'none';
    advCaret.style.transform = willOpen ? 'rotate(90deg)' : 'rotate(0deg)';
  };
  advToggle.addEventListener('click', () => expandAdvanced());

  // 기본 형식 검증 — 고급 수동 구성 폼에서 재사용(기존 validate 규칙 보존, R4.5)
  const validate = (v) => {
    if (!v.name || !/^[A-Za-z0-9._-]+$/.test(v.name)) return '프로파일 이름을 입력하세요 (영문/숫자/._- 만 허용)';
    if (!/^https?:\/\/.+/.test(v.startUrl)) return 'SSO Start URL 형식이 올바르지 않습니다 (https:// 로 시작)';
    if (!/^[a-z]{2}-[a-z]+-\d+$/.test(v.region)) return 'Region 형식이 올바르지 않습니다 (예: us-east-1)';
    if (!/^\d{12}$/.test(v.accountId)) return 'Account ID는 숫자 12자리여야 합니다';
    if (!/^[\w+=,.@-]+$/.test(v.roleName)) return 'Role 이름을 입력하세요';
    return null;
  };

  // 고급 수동 구성 — 기존 aws:write-sso-profile 검증·호출 로직 재사용
  o.querySelector('#ob-submit-btn').addEventListener('click', async () => {
    const btn = o.querySelector('#ob-submit-btn');
    const hint = o.querySelector('#ob-manual-hint');
    const input = {
      name: o.querySelector('#ob-name').value.trim(),
      startUrl: o.querySelector('#ob-start-url').value.trim(),
      region: o.querySelector('#ob-region').value.trim(),
      accountId: o.querySelector('#ob-account-id').value.trim(),
      roleName: o.querySelector('#ob-role-name').value.trim(),
    };
    hint.style.display = 'none'; hint.textContent = '';
    const err = validate(input);
    if (err) { st.className = 'status-text error'; st.textContent = err; return; }
    if (!window.electronAPI?.writeSsoProfile) {
      st.className = 'status-text error'; st.textContent = '프로파일 쓰기 기능을 사용할 수 없습니다.'; return;
    }
    btn.textContent = '◌ 생성 중...'; btn.disabled = true;
    st.className = 'status-text'; st.textContent = '프로파일 생성 중...';
    const resetBtn = () => { btn.textContent = '프로파일 생성'; btn.disabled = false; };

    try {
      const r = await window.electronAPI.writeSsoProfile(input);
      if (!r || !r.success) {
        // 중복/권한 오류 → 사유 + 수동 구성 힌트 표시(R4.5), 온보딩 유지
        if (r?.duplicate) {
          // 이미 존재 — 그대로 로그인으로 진행 가능
          st.className = 'status-text success';
          st.textContent = `이미 존재하는 프로파일입니다: ${input.name} — 로그인으로 진행합니다`;
          setTimeout(() => { showSSODialog(true); }, 600);
          return;
        }
        st.className = 'status-text error';
        st.textContent = `프로파일 생성 실패: ${r?.error || '알 수 없는 오류'}`;
        if (r?.manualHint) { hint.style.display = 'block'; hint.textContent = r.manualHint; }
        resetBtn(); return;
      }
      // 성공 → 기존 로그인 흐름으로 진입(R4.4)
      st.className = 'status-text success';
      st.textContent = `✓ ${r.profile || input.name} 생성 완료 — 로그인으로 진행합니다`;
      setTimeout(() => { showSSODialog(true); }, 600);
    } catch (e) {
      st.className = 'status-text error'; st.textContent = `프로파일 생성 오류: ${e?.message || e}`;
      resetBtn();
    }
  });

  // 로그인 버튼 — 자동 생성 성공 후 노출. 클릭 시 기존 로그인 다이얼로그로 진입한다.
  loginBtn.addEventListener('click', () => { showSSODialog(true); });

  // ── 진입 즉시 조직 기본 프로파일 자동 생성 (무입력 zero-config) ──
  if (!window.electronAPI?.ensureDefaultSsoProfile) {
    // 자동 생성 기능 미가용 → 고급 수동 구성으로 폴백
    subtitle.textContent = 'SSO 프로파일을 직접 구성하세요';
    st.className = 'status-text error';
    st.textContent = '자동 설정 기능을 사용할 수 없습니다 — 아래에서 직접 구성하세요.';
    expandAdvanced(true);
    return;
  }
  try {
    const r = await window.electronAPI.ensureDefaultSsoProfile();
    if (r && r.success) {
      // 성공 → 입력 폼 없이 곧바로 로그인 다이얼로그로 진입
      subtitle.textContent = '조직 기본 설정이 준비되었습니다';
      st.className = 'status-text success';
      st.textContent = r.created
        ? `✓ ${r.profile} 프로파일 준비 완료 — 로그인으로 진행합니다`
        : `✓ ${r.profile} 프로파일 확인 — 로그인으로 진행합니다`;
      loginBtn.style.display = 'block';
      setTimeout(() => { showSSODialog(true); }, 500);
      return;
    }
    // 자동 생성 실패(권한 오류 등) → 고급 폼을 펼쳐 수동 구성 폴백 + 사유/힌트 표시
    subtitle.textContent = '자동 설정에 실패했습니다 — 직접 구성하세요';
    st.className = 'status-text error';
    st.textContent = `자동 프로파일 생성 실패: ${r?.error || '알 수 없는 오류'}`;
    if (r?.manualHint) {
      const hint = o.querySelector('#ob-manual-hint');
      hint.style.display = 'block'; hint.textContent = r.manualHint;
    }
    expandAdvanced(true);
  } catch (e) {
    subtitle.textContent = '자동 설정에 실패했습니다 — 직접 구성하세요';
    st.className = 'status-text error';
    st.textContent = `자동 프로파일 생성 오류: ${e?.message || e}`;
    expandAdvanced(true);
  }
}

async function showSSODialog(isInitial) {
  const o = document.getElementById('sso-dialog'); o.style.display = 'block';
  let profiles = [];
  if (window.electronAPI?.listProfiles) { try { profiles = await window.electronAPI.listProfiles(); } catch {} }
  if (!profiles.length) profiles = ['bedrock-gw', 'default'];
  const optionsHtml = profiles.map(p => `<option value="${p}" ${p === (state.settings?.awsProfile || '') ? 'selected' : ''}>${p}</option>`).join('');

  // Fix 3: 최초 로그인은 바깥 클릭 불가, 재로그인은 가능
  const overlayClick = isInitial ? '' : `onclick="if(event.target===this)document.getElementById('sso-dialog').style.display='none'"`;
  const closeBtn = isInitial ? '' : `<button class="sm-btn" onclick="document.getElementById('sso-dialog').style.display='none'" style="position:absolute;top:16px;right:16px">닫기</button>`;

  o.innerHTML = `<div class="overlay" ${overlayClick}><div class="dialog" style="position:relative">
    ${closeBtn}
    <div class="dialog-icon">◆</div><h2>Mogam Works</h2>
    <div class="subtitle">멀티 에이전트 워크스페이스</div>
    <label>AWS SSO 프로파일</label>
    <select id="sso-profile-select" style="width:100%;padding:10px 14px;background:var(--color-bg-input);border:1px solid var(--color-border);border-radius:var(--radius-md);color:var(--color-text-primary);font-size:13px;outline:none">
      ${optionsHtml}
    </select>
    <label style="margin-top:12px">BedrockUser 이름 <span style="font-size:10px;color:var(--color-text-muted)">(예: cgjang)</span></label>
    <input type="text" id="sso-bedrock-user" value="${state.settings?.bedrockUser || ''}" placeholder="BedrockUser-뒤의 이름 (예: cgjang)" style="width:100%;padding:10px 14px;background:var(--color-bg-input);border:1px solid var(--color-border);border-radius:var(--radius-md);color:var(--color-text-primary);font-size:13px;outline:none">
    <button class="btn-primary" id="sso-login-btn">로그인</button>
    <div class="status-text" id="sso-status"></div>
  </div></div>`;

  o.querySelector('#sso-login-btn').addEventListener('click', async () => {
    const profile = o.querySelector('#sso-profile-select').value;
    if (!profile) return;
    const btn = o.querySelector('#sso-login-btn'), st = o.querySelector('#sso-status');
    const sel = o.querySelector('#sso-profile-select');
    btn.textContent = '◌ 인증 중...'; btn.disabled = true;
    st.className = 'status-text'; st.textContent = '';

    const resetBtn = () => { btn.textContent = '로그인'; btn.disabled = false; };

    try {
      // Step 1: SSO 로그인 (타임아웃 포함)
      if (window.electronAPI?.ssoLogin) {
        st.textContent = `${profile} 로그인 시도 중...`;
        const loginPromise = window.electronAPI.ssoLogin(profile);
        const timeoutPromise = new Promise((_, reject) => setTimeout(() => reject(new Error('로그인 타임아웃 (120초)')), 120000));
        let r;
        try { r = await Promise.race([loginPromise, timeoutPromise]); } catch (te) {
          st.className = 'status-text error'; st.textContent = te.message;
          resetBtn(); return;
        }
        if (!r.success) {
          st.className = 'status-text error';
          st.textContent = `로그인 실패: ${r.error}\n다른 프로파일을 선택하세요.`;
          resetBtn(); return;
        }
      }
      // Step 2: 자격증명 검증
      st.textContent = '자격증명 검증 중...';
      if (window.electronAPI?.getCredentials) {
        const creds = await window.electronAPI.getCredentials(profile);
        if (!creds || !creds.AWS_ACCESS_KEY_ID) {
          st.className = 'status-text error';
          st.textContent = `자격증명 검증 실패 — ${profile} assume role/SSO 세션이 유효하지 않습니다.\n다른 프로파일을 선택하세요.`;
          resetBtn(); return;
        }
      }
      // Step 3: 성공
      await window.electronAPI?.saveSettings?.({ awsProfile: profile });
      state.settings = { awsProfile: profile };
      // BedrockUser 이름 저장
      const buInput = o.querySelector('#sso-bedrock-user')?.value?.trim();
      if (buInput) {
        state.settings.bedrockUser = buInput;
        await window.electronAPI?.saveSettings?.(state.settings);
      }
      st.className = 'status-text success'; st.textContent = `✓ ${profile} 로그인 성공${state.settings.bedrockUser ? ` (BedrockUser-${state.settings.bedrockUser})` : ''}`;
      state.authenticated = true;
      // 백엔드 캐시 초기화 + 자격증명 주입
      try {
        const freshCreds = await window.electronAPI?.getCredentials(profile);
        await fetch(`${apiBase()}/api/reset-cache`, {
          method: 'POST', headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ profile, bedrockUser: state.settings?.bedrockUser || '', credentials: freshCreds || null }),
        });
      } catch {}
      st.className = 'status-text'; st.textContent = '모델 목록 로딩 중...';
      
      // Electron에서 새 자격증명 가져오기
      let freshCreds = null;
      if (window.electronAPI?.getCredentials) {
        freshCreds = await window.electronAPI.getCredentials(profile);
        if (freshCreds && freshCreds.AWS_ACCESS_KEY_ID) {
          state._cachedCreds = {
            accessKeyId: freshCreds.AWS_ACCESS_KEY_ID,
            secretAccessKey: freshCreds.AWS_SECRET_ACCESS_KEY,
            sessionToken: freshCreds.AWS_SESSION_TOKEN || '',
            region: freshCreds.AWS_DEFAULT_REGION || 'us-west-2',
          };
        }
      }
      
      // 모델 로드 — 자격증명을 직접 전달
      let modelLoaded = false;
      for (let retry = 0; retry < 5; retry++) {
        await new Promise(r => setTimeout(r, 1500));
        try {
          let mr;
          if (freshCreds && freshCreds.AWS_ACCESS_KEY_ID) {
            // 자격증명을 POST body로 직접 전달 (boto3 캐시 우회)
            mr = await fetch(`${apiBase()}/api/models`, {
              method: 'POST',
              headers: { 'Content-Type': 'application/json' },
              body: JSON.stringify({
                profile: profile,
                accessKeyId: freshCreds.AWS_ACCESS_KEY_ID,
                secretAccessKey: freshCreds.AWS_SECRET_ACCESS_KEY,
                sessionToken: freshCreds.AWS_SESSION_TOKEN || '',
                region: freshCreds.AWS_DEFAULT_REGION || 'us-west-2',
              })
            });
          } else {
            mr = await fetch(`${apiBase()}/api/models?profile=${encodeURIComponent(profile)}`);
          }
          const md = await mr.json();
          if (md.models && Object.keys(md.models).length > 0) {
            // 채팅 모델 + 추가 카탈로그 합치기
            const _allMd = {};
            for (const [p, ms] of Object.entries(md.models)) {
              _allMd[p] = ms.map(m => ({ ...m, capabilities: { ...(m.capabilities || {}), chat: true } }));
            }
            for (const { data, cap } of [
              { data: md.image_models, cap: 'image_gen' },
              { data: md.video_models, cap: 'video_gen' },
              { data: md.embed_models, cap: 'embedding' },
              { data: md.rerank_models, cap: 'rerank' },
            ]) {
              if (!data) continue;
              for (const [p, ms] of Object.entries(data)) {
                if (!_allMd[p]) _allMd[p] = [];
                _allMd[p] = _allMd[p].concat(ms.map(m => ({ ...m, capabilities: { ...(m.capabilities || {}), [cap]: true } })));
              }
            }
            Object.keys(MODEL_CATALOG).forEach(k => delete MODEL_CATALOG[k]);
            Object.assign(MODEL_CATALOG, _allMd);
            rebuildModelList();
            const _chatMs = ALL_MODELS.filter(m => m.capabilities && m.capabilities.chat);
            if (_chatMs.length > 0) state.selectedModel = _chatMs[0];
            else if (ALL_MODELS.length > 0) state.selectedModel = ALL_MODELS[0];
            renderModelList('');
            document.getElementById('model-dropdown-btn').textContent = (state.selectedModel?.name || '모델 선택') + ' ▾';
            document.getElementById('topbar-model-count').textContent = `${ALL_MODELS.length}개 모델`;
            modelLoaded = true;
            break;
          }
          st.textContent = `모델 로딩 재시도 (${retry + 1}/5)... ${md.error ? md.error.substring(0, 60) : ''}`;
        } catch (fetchErr) {
          st.textContent = `모델 로딩 재시도 (${retry + 1}/5)... ${fetchErr.message}`;
        }
      }
      if (modelLoaded) {
        st.className = 'status-text success';
        st.textContent = `✓ ${profile} 로그인 완료 — ${ALL_MODELS.length}개 모델`;
        setTimeout(() => {
          o.style.display = 'none';
          if (!state._appInitialized) { state._appInitialized = true; initApp(); }
        }, 1000);
      } else {
        st.className = 'status-text error';
        st.textContent = '로그인 성공했지만 모델 로드 실패 — 백엔드 서버를 재시작하세요';
        resetBtn();
      }
    } catch(e) {
      st.className = 'status-text error';
      st.textContent = `오류: ${e.message}\n다른 프로파일을 선택하세요.`;
      resetBtn();
    }
  });
}

// ===== GitHub Import =====
// SSH clone URL(git@host:org/repo, ssh://git@host/org/repo)을 https로 정규화한다.
// 사내망이 SSH 포트22를 차단하므로, 사용자가 어떤 형식을 붙여넣어도 https로 바꿔
// clone이 성립하게 한다. 이미 http(s)면 그대로 반환한다. (순수 함수 — 테스트 용이)
function normalizeGitCloneUrl(raw) {
  const u = String(raw || '').trim();
  if (!u) return u;
  // 이미 http(s)면 변환하지 않음
  if (/^https?:\/\//i.test(u)) return u;
  // scp 형식: git@github.com:org/repo(.git)
  let m = u.match(/^[\w.-]+@([^:/]+):(.+?)(?:\.git)?\/?$/);
  if (m) return `https://${m[1]}/${m[2]}.git`;
  // ssh URL 형식: ssh://git@github.com[:22]/org/repo(.git)
  m = u.match(/^ssh:\/\/[\w.-]+@([^:/]+)(?::\d+)?\/(.+?)(?:\.git)?\/?$/i);
  if (m) return `https://${m[1]}/${m[2]}.git`;
  return u;
}
if (typeof window !== 'undefined') window.normalizeGitCloneUrl = normalizeGitCloneUrl;

function initGithubImport() {
  document.getElementById('btn-github-import').addEventListener('click', () => {
    const o = document.getElementById('sso-dialog'); o.style.display = 'block';
    o.innerHTML = `<div class="overlay" onclick="if(event.target===this)this.parentElement.style.display='none'">
      <div class="dialog" style="text-align:left;position:relative">
        <button class="sm-btn" onclick="document.getElementById('sso-dialog').style.display='none'" style="position:absolute;top:12px;right:12px">닫기</button>
        <h2 style="text-align:center;margin-bottom:16px">GitHub 가져오기</h2>
      <label>저장소 URL</label><input type="text" id="gh-url" placeholder="https://github.com/org/repo.git">
      <label style="margin-top:12px">브랜치 <span style="font-size:10px;color:var(--color-text-muted)">(비우면 기본 브랜치)</span></label><input type="text" id="gh-branch" placeholder="main">
      <label style="margin-top:12px">Access Token <span style="font-size:10px;color:var(--color-text-muted)">(private 저장소만, 선택)</span></label>
      <input type="password" id="gh-token" placeholder="ghp_... (저장 안 됨, 1회 사용)" autocomplete="off">
      <div style="font-size:10px;color:var(--color-text-muted);margin-top:6px;line-height:1.5">
        · 사내망은 SSH(git@)가 막혀 있을 수 있어 <b>https URL</b> 권장<br>
        · private 저장소는 GitHub 토큰 필요 (github.com → Settings → Developer settings → Personal access tokens)
      </div>
      <button class="btn-primary" id="gh-btn">가져오기</button>
      <div class="status-text" id="gh-status"></div></div></div>`;
    o.querySelector('#gh-btn').addEventListener('click', async () => {
      const rawUrl = o.querySelector('#gh-url').value.trim(); if (!rawUrl) return;
      const branch = o.querySelector('#gh-branch').value.trim(); // 빈 값이면 원격 기본 브랜치
      const token = o.querySelector('#gh-token')?.value?.trim() || ''; // private 저장소용 (저장 안 함)
      const btn = o.querySelector('#gh-btn'), st = o.querySelector('#gh-status');
      const resetBtn = () => { btn.textContent = '가져오기'; btn.disabled = false; };

      if (!window.electronAPI?.gitClone) {
        st.className = 'status-text error'; st.textContent = 'clone 기능을 사용할 수 없습니다.';
        return;
      }

      // 사내망 SSH(포트22) 차단 대응 — git@/ssh:// URL을 https로 자동 변환한다.
      const url = normalizeGitCloneUrl(rawUrl);
      const urlConverted = url !== rawUrl;

      // repo 이름 추출: https/ssh 모두 지원 (git@github.com:org/repo.git → repo)
      const repo = url.replace(/\.git$/, '').split(/[/:]/).pop() || 'repo';
      const udp = window.electronAPI?.getUserDataPath ? await window.electronAPI.getUserDataPath() : '/tmp';
      const cp = `${udp}/repos/${repo}`;

      btn.textContent = '가져오는 중...'; btn.disabled = true;
      st.className = 'status-text';
      st.textContent = urlConverted
        ? `SSH URL을 https로 변환해 가져오는 중...\n  ${url}`
        : 'clone 중... (인증/네트워크에 따라 시간이 걸릴 수 있습니다)';

      let r;
      try {
        // 실제 git clone을 IPC로 실행하고 종료코드/stderr로 성패를 판정한다.
        // (기존처럼 폴더 존재로 성공을 추측하지 않는다 — 가짜 성공 방지)
        // token은 private 저장소용 — 어디에도 저장하지 않고 1회 clone에만 사용한다.
        r = await window.electronAPI.gitClone(url, branch, cp, token);
      } catch (e) {
        st.className = 'status-text error'; st.textContent = `clone 오류: ${e?.message || e}`;
        resetBtn(); return;
      }

      if (!r || !r.ok) {
        const err = (r && r.error) || '알 수 없는 오류';
        st.className = 'status-text error';
        // SSH 인증 실패는 흔한 케이스 — https 사용을 구체적으로 안내한다.
        let hint = '';
        if (/permission denied|publickey|could not read from remote|host key|port 22|timed out|connection refused|connect to host/i.test(err)) {
          // SSH(git@) 실패 — 사내망에서 포트22 차단이거나 키 미등록. https 사용을 강하게 권장.
          const org = (url.match(/[:/]([^/:]+)\/[^/]+?(?:\.git)?$/) || [])[1] || '<org>';
          hint = '\n\n※ SSH(git@, 포트22) 연결에 실패했습니다. 사내 네트워크가 SSH를 차단하거나 '
            + 'SSH 키가 등록돼 있지 않을 수 있습니다. https URL로 다시 시도하세요:\n'
            + `  https://github.com/${org}/${repo}.git`;
        } else if (/remote branch .* not found|not found in upstream/i.test(err)) {
          hint = '\n\n※ 입력한 브랜치가 없습니다. 브랜치를 비워두면 기본 브랜치를 가져옵니다.';
        } else if (/classic\)\s*(are|is)?\s*forbidden|forbids access via a personal access token|personal access tokens \(classic\) are forbidden/i.test(err)) {
          // 조직이 classic PAT(ghp_) 접근을 차단 — fine-grained 토큰을 안내.
          hint = '\n\n※ 이 조직은 classic 토큰(ghp_...) 접근을 차단합니다. Fine-grained 토큰을 발급해 사용하세요:\n'
            + '  github.com → Settings → Developer settings → Personal access tokens → Fine-grained tokens → Generate new token\n'
            + '  · Resource owner: 해당 조직 선택 (예: mogam-ai)\n'
            + '  · Repository access: 대상 저장소 선택\n'
            + '  · Permissions: Contents → Read (이상)\n'
            + '  (조직 설정에 따라 관리자 승인이 필요할 수 있습니다)';
        } else if (/repository not found|authentication failed|could not read username|invalid username or password|403|401/i.test(err)) {
          // private 저장소 인증 실패 — Access Token 입력을 구체적으로 안내.
          hint = '\n\n※ private 저장소이거나 인증에 실패했습니다. https URL을 사용하고 '
            + '위의 Access Token 칸에 GitHub 토큰을 입력해 다시 시도하세요.\n'
            + '  (github.com → Settings → Developer settings → Personal access tokens)';
        } else if (/already exists|이미 존재/i.test(err)) {
          hint = '\n\n※ 같은 이름의 폴더가 이미 있습니다.';
        }
        st.textContent = `clone 실패:\n${err}${hint}`;
        resetBtn(); return;
      }

      // 실제 성공 — 폴더 로드
      try {
        state.folderPath = cp;
        document.getElementById('file-tree-path-text').textContent = cp;
        document.getElementById('file-tree-actions').style.display = 'inline-flex';
        await loadFileTree(cp);
        st.className = 'status-text success'; st.textContent = `✓ ${repo} 가져오기 완료`;
        setTimeout(() => { o.style.display = 'none'; }, 1000);
      } catch (e) {
        st.className = 'status-text error';
        st.textContent = `clone은 됐으나 폴더 로드 실패: ${e?.message || e}\n경로: ${cp}`;
        resetBtn();
      }
    });
  });
}

// ===== Fix 5: Skills — 기본 스킬 편집 가능, 글씨 기반 편집/삭제, GitHub md import =====
function initSkills() { renderSkillsList(); }

function renderSkillsList() {
  const s = document.querySelector('.skills-section'); if (!s) return;

  s.innerHTML = `
    <div class="skills-header"><span>스킬</span>
      <div style="display:flex;gap:4px">
        <button class="skills-add-btn" id="btn-import-skill">GitHub MD</button>
        <button class="skills-add-btn" id="btn-add-skill">추가</button>
      </div>
    </div>
    ${allSkills.length ? allSkills.map(sk => `
      <div class="skill-item" data-id="${sk.id}">
        <span class="skill-dot" style="background:${sk.color}"></span>
        <span style="flex:1;overflow:hidden;text-overflow:ellipsis;white-space:nowrap" title="${esc(sk.role?.substring(0,200))}">${esc(sk.name)}</span>
        <span class="sk-action" data-action="edit" data-id="${sk.id}">편집</span>
        <span class="sk-action sk-action-del" data-action="delete" data-id="${sk.id}">삭제</span>
      </div>
    `).join('') : '<div style="padding:8px;font-size:11px;color:var(--color-text-muted);text-align:center">스킬을 추가하세요</div>'}`;

  // 이벤트 바인딩
  s.querySelectorAll('.sk-action').forEach(el => {
    el.addEventListener('click', e => {
      e.stopPropagation();
      const id = el.dataset.id, action = el.dataset.action;
      if (action === 'edit') {
        const sk = allSkills.find(x => x.id === id);
        if (sk) showSkillEditor(sk);
      } else if (action === 'delete') {
        allSkills = allSkills.filter(x => x.id !== id);
        window.electronAPI?.deleteSkill?.(id);
        renderSkillsList();
        renderParallelConfigGrid();
      }
    });
  });
  document.getElementById('btn-add-skill')?.addEventListener('click', () => showSkillEditor());
  document.getElementById('btn-import-skill')?.addEventListener('click', showGithubMdImport);
}

function showSkillEditor(ex) {
  const o = document.getElementById('sso-dialog'), isE = !!ex; o.style.display = 'block';
  o.innerHTML = `<div class="overlay" onclick="if(event.target===this)this.parentElement.style.display='none'">
    <div class="dialog" style="text-align:left;position:relative">
    <button class="sm-btn" onclick="document.getElementById('sso-dialog').style.display='none'" style="position:absolute;top:12px;right:12px">닫기</button>
    <h2 style="text-align:center;margin-bottom:16px">${isE ? '스킬 편집' : '스킬 추가'}</h2>
    <label>이름</label><input type="text" id="sk-name" value="${isE ? ex.name : ''}" placeholder="예: API 전문가">
    <label style="margin-top:12px">역할 (시스템 프롬프트)</label>
    <textarea id="sk-role" style="width:100%;min-height:100px;padding:10px;background:var(--color-bg-input);border:1px solid var(--color-border);border-radius:var(--radius-md);color:var(--color-text-primary);font-size:13px;resize:vertical;outline:none;font-family:var(--font-ui)">${isE ? ex.role : ''}</textarea>
    <label style="margin-top:12px">색상</label>
    <input type="color" id="sk-color" value="${isE ? ex.color : '#3fb950'}" style="width:40px;height:30px;border:none;cursor:pointer">
    <button class="btn-primary" id="sk-save">${isE ? '저장' : '추가'}</button></div></div>`;
  o.querySelector('#sk-save').onclick = () => {
    const n = o.querySelector('#sk-name').value.trim(), r = o.querySelector('#sk-role').value.trim(), c = o.querySelector('#sk-color').value;
    if (!n || !r) return;
    if (isE) {
      ex.name = n; ex.role = r; ex.color = c;
      window.electronAPI?.saveSkill?.({ id: ex.id, name: n, role: r, color: c, builtin: false });
    } else {
      const newSkill = { id:'c-'+Date.now(), name:n, role:r, color:c, builtin:false };
      allSkills.push(newSkill);
      window.electronAPI?.saveSkill?.(newSkill);
    }
    o.style.display = 'none'; renderSkillsList(); renderParallelConfigGrid();
  };
}

// Fix 5: GitHub MD import
function showGithubMdImport() {
  const o = document.getElementById('sso-dialog'); o.style.display = 'block';
  o.innerHTML = `<div class="overlay" onclick="if(event.target===this)this.parentElement.style.display='none'">
    <div class="dialog" style="text-align:left;position:relative">
    <button class="sm-btn" onclick="document.getElementById('sso-dialog').style.display='none'" style="position:absolute;top:12px;right:12px">닫기</button>
    <h2 style="text-align:center;margin-bottom:16px">GitHub MD 스킬 가져오기</h2>
    <label>GitHub URL (.md 파일)</label>
    <input type="text" id="md-url" placeholder="https://github.com/user/repo/blob/main/skill.md">
    <label style="margin-top:12px">스킬 이름</label>
    <input type="text" id="md-name" placeholder="가져올 스킬 이름">
    <button class="btn-primary" id="md-import-btn">가져오기</button>
    <div class="status-text" id="md-status"></div></div></div>`;
  o.querySelector('#md-import-btn').onclick = async () => {
    const url = o.querySelector('#md-url').value.trim();
    const name = o.querySelector('#md-name').value.trim();
    const st = o.querySelector('#md-status');
    if (!url || !name) { st.textContent = 'URL과 이름을 입력하세요'; st.className='status-text error'; return; }
    // GitHub 페이지 URL → raw URL 자동 변환
    let rawUrl = url;
    if (rawUrl.includes('github.com') && rawUrl.includes('/blob/')) {
      rawUrl = rawUrl.replace('github.com', 'raw.githubusercontent.com').replace('/blob/', '/');
    }
    st.textContent = '가져오는 중...'; st.className='status-text';
    try {
      const resp = await fetch(rawUrl);
      if (!resp.ok) throw new Error(`HTTP ${resp.status}`);
      const mdContent = await resp.text();
      const ghSkill = { id:'gh-'+Date.now(), name, role:mdContent, color:'#58a6ff', builtin:false };
      allSkills.push(ghSkill);
      window.electronAPI?.saveSkill?.(ghSkill);
      renderSkillsList();
      renderParallelConfigGrid();
      st.className='status-text success'; st.textContent='✓ 가져오기 완료';
      setTimeout(() => { o.style.display='none'; }, 800);
    } catch(e) {
      st.className='status-text error'; st.textContent=`오류: ${e.message}`;
    }
  };
}

// ===== Model Dropdown — 단일/병렬 공통 UI, 대분류 모델 개수 표시 =====
function initModelDropdown() {
  const btn=document.getElementById('model-dropdown-btn'),menu=document.getElementById('model-dropdown-menu'),search=document.getElementById('model-search');
  btn.textContent=(state.selectedModel?.name || '모델 로딩 중...')+' ▾';
  btn.onclick=()=>{const v=menu.style.display!=='none';menu.style.display=v?'none':'flex';if(!v){search.value='';renderModelList('');search.focus();}};
  search.oninput=()=>renderModelList(search.value);
  document.addEventListener('click',e=>{if(!e.target.closest('#model-dropdown-wrapper')&&!e.target.closest('#parallel-dropdown-wrapper'))
    {document.getElementById('model-dropdown-menu').style.display='none';document.getElementById('parallel-dropdown-menu').style.display='none';}});
  renderModelList('');

  // 병렬 드롭다운
  const pbtn=document.getElementById('parallel-dropdown-btn'),pmenu=document.getElementById('parallel-dropdown-menu'),psearch=document.getElementById('parallel-model-search');
  if(pbtn){
    pbtn.onclick=()=>{const v=pmenu.style.display!=='none';pmenu.style.display=v?'none':'flex';if(!v){psearch.value='';renderParallelDropdownList('');psearch.focus();}};
    psearch.oninput=()=>renderParallelDropdownList(psearch.value);
    // 드롭다운 내부 클릭 시 닫히지 않게
    pmenu.onclick=(ev)=>ev.stopPropagation();
  }
}

function renderModelList(f) {
  const list=document.getElementById('model-dropdown-list');list.innerHTML='';const q=f.toLowerCase();
  for(const[p,ms]of Object.entries(MODEL_CATALOG)){
    const fl=ms.filter(m=>!q||m.name.toLowerCase().includes(q)||p.toLowerCase().includes(q));if(!fl.length)continue;
    const g=document.createElement('div');g.className='model-dropdown-group';
    const _pu = _providerUsage(p);
    g.innerHTML=`<div class="model-dropdown-group-title"><span style="color:var(--color-accent);font-weight:700">${p}</span><span style="color:var(--color-text-muted);margin-left:6px;font-size:9px">${ms.length}개 모델</span>${_pu ? `<span style="color:var(--color-text-muted);margin-left:4px;font-size:9px">(${_pu})</span>` : ''}</div>`;
    fl.forEach(m=>{const i=document.createElement('div');i.className='model-dropdown-item'+(state.selectedModel && m.id===state.selectedModel.id?' selected':'');
      i.innerHTML=`<span style="flex:1">${m.name}</span>`;
      i.style.display='flex';i.style.alignItems='center';
      i.onclick=()=>{state.selectedModel={...m,provider:p};document.getElementById('model-dropdown-btn').textContent=m.name+' ▾';document.getElementById('model-dropdown-menu').style.display='none';document.getElementById('status-model').textContent=m.name;};
      g.appendChild(i);});list.appendChild(g);}
}

function renderParallelDropdownList(f) {
  const list=document.getElementById('parallel-dropdown-list');list.innerHTML='';const q=f.toLowerCase();
  for(const[p,ms]of Object.entries(MODEL_CATALOG)){
    const fl=ms.filter(m=>!q||m.name.toLowerCase().includes(q)||p.toLowerCase().includes(q));if(!fl.length)continue;
    const g=document.createElement('div');g.className='model-dropdown-group';
    const _pu2 = _providerUsage(p);
    g.innerHTML=`<div class="model-dropdown-group-title"><span style="color:var(--color-accent);font-weight:700">${p}</span><span style="color:var(--color-text-muted);margin-left:6px;font-size:9px">${ms.length}개 모델</span>${_pu2 ? `<span style="color:var(--color-text-muted);margin-left:4px;font-size:9px">(${_pu2})</span>` : ''}</div>`;
    fl.forEach(m=>{
      const i=document.createElement('div');i.className='model-dropdown-item';
      i.innerHTML=`<span style="width:16px;display:inline-block;text-align:center;color:var(--color-success)">+</span> ${m.name}`;
      i.onclick=(ev)=>{
        ev.stopPropagation();
        addParallelSlot(m.id); // 중복 추가 허용
        renderParallelDropdownList(document.getElementById('parallel-model-search').value);
      };
      g.appendChild(i);});list.appendChild(g);}
}

async function loadModelsFromServer(retryCount) {
  const attempt = retryCount || 0;
  try {
    const profile = state.settings?.awsProfile || 'default';
    
    // Electron에서 자격증명 가져와서 직접 전달
    let mr;
    if (window.electronAPI?.getCredentials) {
      const creds = await window.electronAPI.getCredentials(profile);
      if (creds && creds.AWS_ACCESS_KEY_ID) {
        mr = await fetch(`${apiBase()}/api/models`, {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({
            profile, accessKeyId: creds.AWS_ACCESS_KEY_ID,
            secretAccessKey: creds.AWS_SECRET_ACCESS_KEY,
            sessionToken: creds.AWS_SESSION_TOKEN || '',
            region: creds.AWS_DEFAULT_REGION || 'us-west-2',
          })
        });
      }
    }
    if (!mr) {
      mr = await fetch(`${apiBase()}/api/models?profile=${encodeURIComponent(profile)}`);
    }
    if (!mr.ok) throw new Error(`HTTP ${mr.status}`);
    const d = await mr.json();
    if (d.error) {
      console.warn('[Models] 서버 에러:', d.error);
      if (d.error.includes('SSO') || d.error.includes('expired')) {
        document.getElementById('topbar-model-count').textContent = 'SSO 만료 — 설정에서 재로그인';
      } else {
        document.getElementById('topbar-model-count').textContent = '모델 로드 실패';
      }
      return;
    }
    if (d.models && Object.keys(d.models).length > 0) {
      // 채팅 모델에 chat:true capability 자동 부여
      const allModels = {};
      for (const [provider, models] of Object.entries(d.models)) {
        allModels[provider] = models.map(m => ({
          ...m,
          capabilities: { ...(m.capabilities || {}), chat: true },
        }));
      }
      // 추가 카탈로그 (image/video/embed/rerank) 합치기 + capability 주입
      const extraCatalogs = [
        { data: d.image_models, cap: 'image_gen' },
        { data: d.video_models, cap: 'video_gen' },
        { data: d.embed_models, cap: 'embedding' },
        { data: d.rerank_models, cap: 'rerank' },
      ];
      for (const { data, cap } of extraCatalogs) {
        if (!data) continue;
        for (const [provider, models] of Object.entries(data)) {
          if (!allModels[provider]) allModels[provider] = [];
          const tagged = models.map(m => ({ ...m, capabilities: { ...(m.capabilities || {}), [cap]: true } }));
          allModels[provider] = allModels[provider].concat(tagged);
        }
      }
      // 디스크 denylist 적용
      const filtered = {};
      for (const [provider, models] of Object.entries(allModels)) {
        const kept = models.filter(m => {
          const clean = String(m.id || '').replace(/^us\.|^eu\.|^global\./, '');
          if (_deniedModels.has(clean)) return false;
          return true;
        });
        if (kept.length) filtered[provider] = kept;
      }
      Object.keys(MODEL_CATALOG).forEach(k => delete MODEL_CATALOG[k]);
      Object.assign(MODEL_CATALOG, filtered);
      rebuildModelList();
      // 채팅 가능 모델을 기본 선택
      const chatModels = ALL_MODELS.filter(m => m.capabilities && m.capabilities.chat);
      if (chatModels.length > 0) state.selectedModel = chatModels[0];
      else if (ALL_MODELS.length > 0) state.selectedModel = ALL_MODELS[0];
      renderModelList('');
      document.getElementById('model-dropdown-btn').textContent = (state.selectedModel?.name || '모델 선택') + ' ▾';
      document.getElementById('topbar-model-count').textContent = `${ALL_MODELS.length}개 모델`;
      state.authenticated = true;
    }
  } catch (e) {
    console.warn(`[Models] 로드 실패 (시도 ${attempt + 1}):`, e.message);
    if (attempt < 2) {
      setTimeout(() => loadModelsFromServer(attempt + 1), 3000);
    } else {
      document.getElementById('topbar-model-count').textContent = '모델 로드 실패';
    }
  }
}

// ===== gateway-openai-models Task 9.2: Model_Refresh_Scheduler =====
// 게이트웨이에 모델(OpenAI 포함)이 추가/제거되면 에디터 목록을 주기적으로
// 자동 갱신한다. 핵심 원칙(순수 add, 비침습):
//  - /api/models 결과를 9.1 순수 함수(catalogSignature/resolveSelection)로 비교
//  - 시그니처 동일 → 목록·선택·표시 모두 불변(기존 동작 보존, OpenAI 미구성 시 동일)
//  - 변경 시에만 MODEL_CATALOG 갱신 + 선택을 보존/유효 복구
//  - 조회 실패 시 직전 성공 목록 유지, 다음 주기에 재시도(목록 비우지 않음)
let _modelRefreshTimer = null;

// /api/models를 조회해 denylist 필터까지 적용한 카탈로그 객체를 반환한다.
// 실패 시 예외를 던진다(호출부에서 직전 목록 유지).
async function _fetchFilteredModelCatalog() {
  const profile = state.settings?.awsProfile || 'default';
  let mr;
  if (window.electronAPI?.getCredentials) {
    const creds = await window.electronAPI.getCredentials(profile);
    if (creds && creds.AWS_ACCESS_KEY_ID) {
      mr = await fetch(`${apiBase()}/api/models`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          profile, accessKeyId: creds.AWS_ACCESS_KEY_ID,
          secretAccessKey: creds.AWS_SECRET_ACCESS_KEY,
          sessionToken: creds.AWS_SESSION_TOKEN || '',
          region: creds.AWS_DEFAULT_REGION || 'us-west-2',
        })
      });
    }
  }
  if (!mr) {
    mr = await fetch(`${apiBase()}/api/models?profile=${encodeURIComponent(profile)}`);
  }
  if (!mr.ok) throw new Error(`HTTP ${mr.status}`);
  const d = await mr.json();
  if (d.error) throw new Error(d.error);
  if (!d.models || Object.keys(d.models).length === 0) throw new Error('empty-catalog');

  const allModels = {};
  for (const [provider, models] of Object.entries(d.models)) {
    allModels[provider] = models.map(m => ({
      ...m,
      capabilities: { ...(m.capabilities || {}), chat: true },
    }));
  }
  const extraCatalogs = [
    { data: d.image_models, cap: 'image_gen' },
    { data: d.video_models, cap: 'video_gen' },
    { data: d.embed_models, cap: 'embedding' },
    { data: d.rerank_models, cap: 'rerank' },
  ];
  for (const { data, cap } of extraCatalogs) {
    if (!data) continue;
    for (const [provider, models] of Object.entries(data)) {
      if (!allModels[provider]) allModels[provider] = [];
      const tagged = models.map(m => ({ ...m, capabilities: { ...(m.capabilities || {}), [cap]: true } }));
      allModels[provider] = allModels[provider].concat(tagged);
    }
  }
  const filtered = {};
  for (const [provider, models] of Object.entries(allModels)) {
    const kept = models.filter(m => {
      const clean = String(m.id || '').replace(/^us\.|^eu\.|^global\./, '');
      return !_deniedModels.has(clean);
    });
    if (kept.length) filtered[provider] = kept;
  }
  return filtered;
}

// 선택 보존 자동 새로고침: 변경이 있을 때만 목록/선택/표시를 갱신한다.
async function refreshModelsPreservingSelection() {
  if (!state.authenticated) return;
  let next;
  try {
    next = await _fetchFilteredModelCatalog();
  } catch (e) {
    // 조회 실패 → 직전 성공 목록 유지(목록 비우지 않음), 다음 주기 재시도
    console.warn('[Models] 자동 새로고침 실패 — 직전 목록 유지:', e?.message || e);
    return;
  }
  // 9.1 순수 함수로 변경 여부 판정
  const prevSig = catalogSignature(MODEL_CATALOG);
  const nextSig = catalogSignature(next);
  if (prevSig === nextSig) {
    // 카탈로그 동일 → 목록·선택·표시 모두 불변(기존 동작 보존)
    return;
  }
  // 변경 감지 → 선택 보존/복구 결정(갱신 전 카탈로그 기준)
  const prevId = state.selectedModel?.id || null;
  const resolved = resolveSelection(prevId, MODEL_CATALOG, next);
  // 카탈로그 교체
  Object.keys(MODEL_CATALOG).forEach(k => delete MODEL_CATALOG[k]);
  Object.assign(MODEL_CATALOG, next);
  rebuildModelList();
  // 선택 적용(보존 또는 유효 복구)
  state.selectedModel = resolved;
  renderModelList('');
  const btn = document.getElementById('model-dropdown-btn');
  if (btn) btn.textContent = (state.selectedModel?.name || '모델 선택') + ' ▾';
  const cnt = document.getElementById('topbar-model-count');
  if (cnt) cnt.textContent = `${ALL_MODELS.length}개 모델`;
  const statusModel = document.getElementById('status-model');
  if (statusModel && state.selectedModel?.name) statusModel.textContent = state.selectedModel.name;
  console.log(`[Models] 자동 새로고침 — 카탈로그 변경 반영 (${ALL_MODELS.length}개, 선택: ${state.selectedModel?.id || '없음'})`);
}

// 주기적 자동 새로고침 스케줄러 시작. 기본 300초, settings로 60~3600초 조정.
// 인증 상태에서만 동작하며 중복 타이머를 방지한다.
function startModelRefreshScheduler() {
  let sec = Number(state.settings?.modelRefreshIntervalSec);
  if (!Number.isFinite(sec)) sec = 300;
  sec = Math.max(60, Math.min(3600, sec));
  if (_modelRefreshTimer) {
    clearInterval(_modelRefreshTimer);
    _modelRefreshTimer = null;
  }
  _modelRefreshTimer = setInterval(() => {
    if (!state.authenticated) return;
    refreshModelsPreservingSelection().catch(err =>
      console.warn('[Models] 새로고침 주기 오류:', err?.message || err));
  }, sec * 1000);
  console.log(`[Models] 자동 새로고침 스케줄러 시작 (주기 ${sec}초)`);
}

if (typeof window !== 'undefined') {
  window.refreshModelsPreservingSelection = refreshModelsPreservingSelection;
  window.startModelRefreshScheduler = startModelRefreshScheduler;
}

// ===== Mode Toggle =====
let _slotCounter = 0;
function initModeToggle() {
  document.querySelectorAll('.mode-toggle-btn').forEach(btn => {
    btn.addEventListener('click', () => {
      const mode = btn.dataset.mode;
      state.mode = mode;
      document.querySelectorAll('.mode-toggle-btn').forEach(b => b.classList.toggle('active', b.dataset.mode === mode));
      document.getElementById('single-model-bar').style.display = mode === 'single' ? 'block' : 'none';
      document.getElementById('parallel-model-bar').style.display = mode === 'parallel' ? 'block' : 'none';
      document.getElementById('parallel-selected-list').style.display = mode === 'parallel' ? 'block' : 'none';
      if (mode === 'parallel') { showParallelResults(); renderParallelConfigGrid(); renderParallelSlotList(); }
      else hideParallelResults();
    });
  });
  document.getElementById('parallel-expand-all')?.addEventListener('click', () => {
    state.mode = 'single';
    document.querySelectorAll('.mode-toggle-btn').forEach(b => b.classList.toggle('active', b.dataset.mode === 'single'));
    document.getElementById('single-model-bar').style.display = 'block';
    document.getElementById('parallel-model-bar').style.display = 'none';
    document.getElementById('parallel-selected-list').style.display = 'none';
    hideParallelResults();
  });
}

function addParallelSlot(modelId) {
  const model = ALL_MODELS.find(m => m.id === modelId); if (!model) return;
  state.parallelSlots.push({ slotId:'slot-'+(++_slotCounter), modelId, skillId:'', customRole:'', model, scale: 1 });
  document.getElementById('parallel-dropdown-btn').textContent = `${_totalParallelCount()}개 호출 ▾`;
  renderParallelSlotList(); renderParallelConfigGrid();
}
function removeParallelSlot(slotId) {
  state.parallelSlots = state.parallelSlots.filter(s => s.slotId !== slotId);
  document.getElementById('parallel-dropdown-btn').textContent = `${_totalParallelCount()}개 호출 ▾`;
  renderParallelSlotList(); renderParallelConfigGrid();
}
function _totalParallelCount() {
  return state.parallelSlots.reduce((sum, s) => sum + (s.scale || 1), 0);
}
function renderParallelConfigGrid() {
  const grid = document.getElementById('parallel-grid'), countEl = document.getElementById('parallel-count');
  if (!grid || state.isStreaming) return;
  const total = _totalParallelCount();
  countEl.textContent = state.parallelSlots.length ? `${state.parallelSlots.length}개 모델 · ${total}개 호출` : '병렬 모델을 선택하세요';
  if (!state.parallelSlots.length) { grid.innerHTML = '<div style="padding:40px;text-align:center;color:var(--color-text-muted);font-size:13px">우측에서 모델을 검색하여 추가하세요<br><span style="font-size:11px">같은 모델을 여러 번 추가 가능</span></div>'; return; }
  grid.innerHTML = '';
  state.parallelSlots.forEach(slot => {
    const card = document.createElement('div'); card.className = 'model-card fade-in';
    card.innerHTML = `<div class="model-card-header"><span class="model-name">● ${slot.model.name}</span><span style="font-size:10px;color:var(--color-text-muted)">${slot.model.provider}</span><span class="sk-action sk-action-del sk-action-icon" data-rm="${slot.slotId}" title="제거"><svg class="sk-icon" width="10" height="10" viewBox="0 0 10 10" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linecap="round"><path d="M2 2L8 8M8 2L2 8"/></svg></span></div>
      <div style="padding:10px 14px;display:flex;flex-direction:column;gap:6px">
        <div style="display:flex;align-items:center;gap:8px">
          <label style="font-size:10px;color:var(--color-text-muted);font-weight:600;min-width:40px">스케일</label>
          <input type="range" class="scale-slider" min="1" max="10" value="${slot.scale||1}" style="flex:1;height:4px;accent-color:var(--color-accent);cursor:pointer">
          <span class="scale-value" style="font-size:12px;font-weight:700;color:var(--color-accent);min-width:24px;text-align:center">${slot.scale||1}</span>
        </div>
        <label style="font-size:10px;color:var(--color-text-muted);font-weight:600">스킬</label>
        <select class="ss" style="width:100%;background:var(--color-bg-input);border:1px solid var(--color-border);border-radius:var(--radius-sm);color:var(--color-text-secondary);font-size:11px;padding:5px 8px;outline:none"><option value="">스킬 없음</option>${allSkills.map(s=>`<option value="${s.id}" ${slot.skillId===s.id?'selected':''}>${s.name}</option>`).join('')}</select>
        <label style="font-size:10px;color:var(--color-text-muted);font-weight:600">커스텀 Role</label>
        <textarea class="cr" placeholder="텍스트 또는 JSON" style="width:100%;min-height:40px;max-height:100px;background:var(--color-bg-input);border:1px solid var(--color-border);border-radius:var(--radius-sm);color:var(--color-text-secondary);font-size:11px;padding:6px 8px;outline:none;resize:vertical;font-family:var(--font-mono)">${slot.customRole||''}</textarea>
      </div>`;
    card.querySelector('[data-rm]').onclick = () => removeParallelSlot(slot.slotId);
    card.querySelector('.ss').onchange = e => { slot.skillId = e.target.value; };
    card.querySelector('.cr').oninput = e => { slot.customRole = e.target.value; };
    // 스케일 슬라이더 이벤트
    const slider = card.querySelector('.scale-slider');
    const valueEl = card.querySelector('.scale-value');
    slider.oninput = () => {
      slot.scale = parseInt(slider.value);
      valueEl.textContent = slot.scale;
      document.getElementById('parallel-dropdown-btn').textContent = `${_totalParallelCount()}개 호출 ▾`;
      countEl.textContent = `${state.parallelSlots.length}개 모델 · ${_totalParallelCount()}개 호출`;
    };
    grid.appendChild(card);
  });
}
function renderParallelSlotList() {
  const list = document.getElementById('model-checklist'); list.innerHTML = '';
  if (!state.parallelSlots.length) { list.innerHTML = '<div style="padding:12px;text-align:center;color:var(--color-text-muted);font-size:12px">모델을 검색하여 추가</div>'; }
  state.parallelSlots.forEach(slot => {
    // 스케일 카운트 N개일 때 결과는 slotId, slotId-0, slotId-1 ... 형태로 저장됨
    // 모든 매칭된 결과를 집계해서 상태 결정
    const scale = slot.scale || 1;
    const matchedIds = scale > 1
      ? Array.from({ length: scale }, (_, i) => `${slot.slotId}-${i}`)
      : [slot.slotId];
    const results = matchedIds.map(id => state.parallelResults.get(id)).filter(Boolean);

    let stText = '', stColor = '';
    if (results.length === 0) {
      stText = ''; stColor = '';
    } else {
      const allDone = results.every(r => r.status === 'done');
      const anyError = results.some(r => r.status === 'error');
      const anyRunning = results.some(r => r.status === 'running');
      const anyPending = results.some(r => r.status === 'pending');
      const doneCount = results.filter(r => r.status === 'done').length;

      if (allDone) {
        stText = scale > 1 ? `완료 (${doneCount}/${scale})` : '완료';
        stColor = 'var(--color-success)';
      } else if (anyRunning) {
        stText = scale > 1 ? `실행 중 (${doneCount}/${scale})` : '실행 중';
        stColor = 'var(--color-accent)';
      } else if (anyPending) {
        stText = '대기'; stColor = 'var(--color-text-muted)';
      } else if (anyError) {
        const errCount = results.filter(r => r.status === 'error').length;
        stText = scale > 1 ? `${doneCount}완료/${errCount}실패` : '실패';
        stColor = doneCount > 0 ? 'var(--color-warning)' : 'var(--color-error)';
      }
    }

    const item = document.createElement('div'); item.className = 'model-check-item';
    item.innerHTML = `<span class="dot" style="background:${stColor||'var(--color-accent)'}"></span><span style="flex:1">${slot.model.name}${scale > 1 ? ` <span style="font-size:10px;color:var(--color-text-muted)">×${scale}</span>` : ''}</span><span class="status" style="color:${stColor}">${stText}</span>${!state.isStreaming ? `<span class="sk-action sk-action-del sk-action-icon" data-rm="${slot.slotId}" title="제거"><svg class="sk-icon" width="10" height="10" viewBox="0 0 10 10" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linecap="round"><path d="M2 2L8 8M8 2L2 8"/></svg></span>` : ''}`;
    const rmBtn = item.querySelector('[data-rm]');
    if (rmBtn) rmBtn.onclick = () => removeParallelSlot(slot.slotId);
    list.appendChild(item);
  });
  document.getElementById('parallel-count-label').textContent = `${state.parallelSlots.length}개 선택`;
  updateConsensus();
}

// ===== Chat Tabs =====
function initChatTabs() { renderChatTabs(); }
function renderChatTabs() {
  const bar = document.getElementById('chat-tabs-bar'); if (!bar) return;
  bar.innerHTML = chatSessions.map((s, i) => `
    <button class="chat-tab ${i === activeSessionIdx ? 'active' : ''}" data-idx="${i}">
      ${s.name}${chatSessions.length > 1 ? `<span class="chat-tab-close" data-close="${i}">✕</span>` : ''}
    </button>
  `).join('') + `<button class="chat-tab-add" id="btn-new-session">+</button>`;

  bar.querySelectorAll('.chat-tab').forEach(el => {
    el.addEventListener('click', e => {
      if (e.target.classList.contains('chat-tab-close')) {
        const idx = +e.target.dataset.close;
        chatSessions.splice(idx, 1);
        if (activeSessionIdx >= chatSessions.length) activeSessionIdx = chatSessions.length - 1;
        renderChatTabs(); renderMessages();
        return;
      }
      activeSessionIdx = +el.dataset.idx;
      renderChatTabs(); renderMessages();
    });
  });
  document.getElementById('btn-new-session')?.addEventListener('click', () => {
    chatSessions.push({ id:'s-'+Date.now(), name:`대화 ${chatSessions.length+1}`, messages:[] });
    activeSessionIdx = chatSessions.length - 1;
    renderChatTabs(); renderMessages();
  });
}

// ===== Chat + File Attach =====
function initChat() {
  const input=document.getElementById('chat-input'),sendBtn=document.getElementById('send-btn');
  input.onkeydown=e=>{if(e.key==='Enter'&&!e.shiftKey&&!e.isComposing){e.preventDefault();sendMessage();}};
  input.oninput=()=>{input.style.height='auto';input.style.height=Math.min(input.scrollHeight,120)+'px';};

  // 이미지 클립보드 paste 지원 — 캡처/복사한 이미지를 채팅에 직접 붙여넣기
  input.addEventListener('paste', async (e) => {
    const items = (e.clipboardData || e.originalEvent?.clipboardData)?.items;
    if (!items) return;
    let pastedImage = false;
    for (const item of items) {
      if (item.type && item.type.startsWith('image/')) {
        const blob = item.getAsFile();
        if (!blob) continue;
        pastedImage = true;
        e.preventDefault();
        const ext = (item.type.split('/')[1] || 'png').toLowerCase();
        const reader = new FileReader();
        reader.onload = (ev) => {
          const dataUrl = ev.target.result;
          const fname = `pasted-${Date.now()}.${ext}`;
          state.attachedFiles.push({
            name: fname,
            type: item.type,
            ext: ext === 'jpeg' ? 'jpg' : ext,
            data: dataUrl,
            size: blob.size,
            rawBase64: dataUrl.split(',')[1] || '',
          });
          renderAttachedFiles();
          addLiveLog('system', `이미지 붙여넣기 — ${fname} (${Math.round(blob.size / 1024)} KB)`);
        };
        reader.readAsDataURL(blob);
      }
    }
    if (pastedImage) return;  // 이미지면 텍스트 paste는 막음
  });

  sendBtn.onclick = () => {
    if (state.isStreaming) {
      // 취소
      state._userInitiatedAbort = true;  // 진짜 사용자 취소 vs auto-timeout 구분
      if (state._abortController) { state._abortController.abort(); state._abortController = null; }
      state.isStreaming = false;
      _releaseUserPin();
      sendBtn.textContent = '전송';
      sendBtn.style.background = 'var(--color-accent)';
      state.messages.push({ role:'system', content:'사용자가 요청을 취소했습니다.' });
      renderMessages();
    } else {
      sendMessage();
    }
  };
  document.getElementById('btn-attach').onclick=()=>document.getElementById('file-attach-input').click();
  document.getElementById('file-attach-input').onchange=e=>{
    Array.from(e.target.files).forEach(f=>{
      const ext=f.name.split('.').pop().toLowerCase();
      // ZIP — extract on backend, then attach as a structured listing
      if (ext === 'zip') {
        handleZipAttachment(f);
        return;
      }
      if(!['pdf','pptx','xlsx','png','jpg','jpeg'].includes(ext))return;
      const reader=new FileReader();
      if (['xlsx'].includes(ext)) {
        // xlsx는 ArrayBuffer로 읽어서 base64 변환
        reader.onload=ev=>{
          const base64 = btoa(new Uint8Array(ev.target.result).reduce((data, byte) => data + String.fromCharCode(byte), ''));
          state.attachedFiles.push({name:f.name,type:f.type,ext,data:`data:application/vnd.openxmlformats-officedocument.spreadsheetml.sheet;base64,${base64}`,size:f.size,rawBase64:base64});
          renderAttachedFiles();
        };
        reader.readAsArrayBuffer(f);
      } else {
        reader.onload=ev=>{state.attachedFiles.push({name:f.name,type:f.type,ext,data:ev.target.result,size:f.size});renderAttachedFiles();};
        reader.readAsDataURL(f);
      }
    });
    e.target.value='';
  };
}

// ZIP 첨부 — 백엔드에 업로드 → 임시 디렉토리에 압축 해제 → 파일 목록을
// state.attachedFiles에 구조화된 형태로 저장. 채팅 전송 시 이 목록이
// 시스템 프롬프트에 펼쳐져 에이전트가 read_file 도구로 내부 파일에 접근할 수 있다.
async function handleZipAttachment(file) {
  if (!file) return null;
  // Insert a placeholder while extracting so the user gets feedback
  const placeholder = {
    name: file.name,
    ext: 'zip',
    kind: 'zip',
    size: file.size,
    type: 'application/zip',
    extracting: true,
  };
  state.attachedFiles.push(placeholder);
  renderAttachedFiles();
  try {
    const fd = new FormData();
    fd.append('file', file);
    const r = await fetch(`${apiBase()}/api/attachments/extract-zip`, {
      method: 'POST',
      body: fd,
    });
    if (!r.ok) {
      const err = await r.json().catch(() => ({}));
      console.warn('[zip-extract] failed:', err);
      // Drop the placeholder
      const idx = state.attachedFiles.indexOf(placeholder);
      if (idx >= 0) state.attachedFiles.splice(idx, 1);
      renderAttachedFiles();
      try { addLiveLog && addLiveLog('error', `ZIP 압축 해제 실패: ${file.name} — ${err.error || r.status}`); } catch(_) {}
      return null;
    }
    const data = await r.json();
    // Replace placeholder with the real attachment record
    const idx = state.attachedFiles.indexOf(placeholder);
    const record = {
      name: file.name,
      ext: 'zip',
      kind: 'zip',
      size: file.size,
      type: 'application/zip',
      extractDir: data.extractDir,
      files: data.files || [],
      totalFiles: data.totalFiles || 0,
      totalBytes: data.totalBytes || 0,
      skipped: data.skipped || [],
    };
    if (idx >= 0) state.attachedFiles.splice(idx, 1, record);
    else state.attachedFiles.push(record);
    renderAttachedFiles();
    try {
      addLiveLog && addLiveLog('system',
        `ZIP 압축 해제됨 — ${file.name} → ${record.totalFiles}개 파일 (위치: ${record.extractDir})`);
    } catch(_) {}
    return record;
  } catch (e) {
    console.warn('[zip-extract] error:', e);
    const idx = state.attachedFiles.indexOf(placeholder);
    if (idx >= 0) state.attachedFiles.splice(idx, 1);
    renderAttachedFiles();
    try { addLiveLog && addLiveLog('error', `ZIP 압축 해제 오류: ${file.name} — ${e.message || e}`); } catch(_) {}
    return null;
  }
}
function renderAttachedFiles() {
  const c=document.getElementById('attached-files-area');
  c.innerHTML=state.attachedFiles.map((f,i)=>{
    const isImg = ['png','jpg','jpeg','webp','gif'].includes((f.ext || '').toLowerCase());
    if (isImg && f.data) {
      return `<div class="attached-file attached-image" data-idx="${i}" style="cursor:zoom-in;display:inline-flex;align-items:center;gap:6px;padding:4px 8px;background:var(--color-bg-tertiary);border:1px solid var(--color-border);border-radius:4px">
        <img src="${esc(f.data)}" alt="${esc(f.name)}" data-zoom-idx="${i}" style="width:32px;height:32px;object-fit:cover;border-radius:3px;cursor:zoom-in" />
        <span style="font-size:11px">${esc(f.name)} (${(f.size/1024).toFixed(0)}KB)</span>
        <span class="remove" data-idx="${i}" style="cursor:pointer;color:var(--color-text-muted)">✕</span>
      </div>`;
    }
    return `<div class="attached-file"><span>${esc(f.name)} (${(f.size/1024).toFixed(0)}KB)</span><span class="remove" data-idx="${i}">✕</span></div>`;
  }).join('');
  c.querySelectorAll('.remove').forEach(el=>el.onclick=(ev)=>{ev.stopPropagation();state.attachedFiles.splice(+el.dataset.idx,1);renderAttachedFiles();});
  // 첨부 이미지 클릭 → 확대 모달
  c.querySelectorAll('img[data-zoom-idx]').forEach(img => {
    img.onclick = (ev) => {
      ev.stopPropagation();
      const idx = +img.dataset.zoomIdx;
      const f = state.attachedFiles[idx];
      if (f && f.data) _showImageZoomModal(f.data, f.name);
    };
  });
}

// 이미지 확대 모달 — 휠 줌, +/− 버튼, ESC/오버레이로 닫기
function _showImageZoomModal(src, name) {
  const existing = document.getElementById('image-zoom-modal');
  if (existing) existing.remove();
  const m = document.createElement('div');
  m.id = 'image-zoom-modal';
  m.style.cssText = 'position:fixed;inset:0;background:rgba(0,0,0,0.85);z-index:10000;display:flex;align-items:center;justify-content:center;cursor:zoom-out';
  m.innerHTML = `
    <div style="position:absolute;top:16px;left:16px;color:#ccc;font-size:12px;font-family:var(--font-mono);background:rgba(0,0,0,0.5);padding:6px 10px;border-radius:4px">${esc(name || 'image')}</div>
    <button id="izm-close" style="position:absolute;top:16px;right:16px;width:32px;height:32px;background:rgba(0,0,0,0.6);border:1px solid #555;color:#ccc;border-radius:4px;cursor:pointer;font-size:18px;line-height:1">✕</button>
    <div style="position:absolute;bottom:24px;left:50%;transform:translateX(-50%);display:flex;gap:8px;background:rgba(0,0,0,0.7);padding:6px 10px;border-radius:6px">
      <button id="izm-out" style="width:32px;height:32px;background:transparent;border:1px solid #555;color:#ccc;border-radius:4px;cursor:pointer;font-size:16px">−</button>
      <button id="izm-reset" style="height:32px;padding:0 12px;background:transparent;border:1px solid #555;color:#ccc;border-radius:4px;cursor:pointer;font-size:11px">100%</button>
      <button id="izm-in" style="width:32px;height:32px;background:transparent;border:1px solid #555;color:#ccc;border-radius:4px;cursor:pointer;font-size:16px">+</button>
    </div>
    <img id="izm-img" src="${esc(src)}" style="max-width:90vw;max-height:90vh;object-fit:contain;transition:transform 120ms ease;cursor:grab" />
  `;
  document.body.appendChild(m);
  let scale = 1;
  const img = m.querySelector('#izm-img');
  const apply = () => { img.style.transform = `scale(${scale})`; m.querySelector('#izm-reset').textContent = `${Math.round(scale * 100)}%`; };
  const close = () => m.remove();
  m.onclick = (e) => { if (e.target === m) close(); };
  img.onclick = (e) => e.stopPropagation();
  m.querySelector('#izm-close').onclick = (e) => { e.stopPropagation(); close(); };
  m.querySelector('#izm-in').onclick = (e) => { e.stopPropagation(); scale = Math.min(8, scale * 1.25); apply(); };
  m.querySelector('#izm-out').onclick = (e) => { e.stopPropagation(); scale = Math.max(0.2, scale / 1.25); apply(); };
  m.querySelector('#izm-reset').onclick = (e) => { e.stopPropagation(); scale = 1; apply(); };
  // 휠 줌
  m.addEventListener('wheel', (e) => {
    e.preventDefault();
    if (e.deltaY < 0) scale = Math.min(8, scale * 1.1);
    else scale = Math.max(0.2, scale / 1.1);
    apply();
  }, { passive: false });
  // ESC
  const onKey = (e) => { if (e.key === 'Escape') { close(); document.removeEventListener('keydown', onKey); } };
  document.addEventListener('keydown', onKey);
}
async function sendMessage() {
  const input=document.getElementById('chat-input');const text=input.value.trim();
  if(!text&&!state.attachedFiles.length)return;
  if(state.isStreaming)return;
  if(!state.authenticated) {
    state.messages.push({ role:'system', content:'로그인이 필요합니다. SSO 로그인을 진행하세요.' });
    renderMessages();
    showSSODialog(true);
    return;
  }
  if(!state.selectedModel) {
    state.messages.push({ role:'system', content:'모델을 선택하세요. 모델 목록이 로딩 중일 수 있습니다.' });
    renderMessages();
    return;
  }

  // ===== 자동 대화 인계 (handoff) =====
  // 누적된 컨텍스트가 임계치를 넘으면:
  //  1) 현재 대화를 Haiku로 인계 MD 요약 → .generated/handoff/conversation-handoff-<ts>.md
  //  2) 새 대화 탭 자동 생성
  //  3) 새 탭의 첫 메시지로 인계 MD를 user 컨텍스트로 주입 → 다음 호출에 자동 전달
  // 이전 대화는 탭으로 보존 — 사용자가 클릭해 돌아갈 수 있음.
  // 효과: 단순 짧은 호출이 6분+ 걸리는 누적 문제 원천 차단 + 작업 흐름 끊김 없음.
  await (async function _maybeHandoff(){
    try {
      const cur = chatSessions[activeSessionIdx];
      if (!cur) return;
      const msgs = cur.messages || [];
      // 임계치 — 메시지 16+ OR 60K글자+ OR 병렬/합의 3건+
      let totalChars = 0;
      let heavyCount = 0;
      for (const m of msgs) {
        if (typeof m.content === 'string') totalChars += m.content.length;
        if (m.isParallel || m.isConsensus) heavyCount++;
      }
      const tooLong = msgs.length >= 16 || totalChars >= 60000 || heavyCount >= 3;
      if (!tooLong) return;
      // 같은 세션에서 이미 회전했으면 스킵 (무한 회전 방지)
      if (cur._handoff_done) return;
      cur._handoff_done = true;
      const oldName = cur.name || `대화 ${activeSessionIdx + 1}`;
      try { addLiveLog && addLiveLog('system', `대화 인계 시작 — ${oldName} (msgs=${msgs.length}, chars=${totalChars}, heavy=${heavyCount})`); } catch(_) {}

      // 1) 서버에 요약 요청 (Haiku, 60초 timeout)
      let handoffResult = null;
      try {
        const handoffPayload = {
          messages: msgs.filter(m => m.role && m.content).map(m => ({ role: m.role, content: m.content })),
          sessionId: cur.id || '',
          projectPath: state.folderPath || '',
          awsProfile: state.settings?.awsProfile || 'bedrock-gw',
          bedrockUser: state.settings?.bedrockUser || '',
        };
        const r = await fetch(`${apiBase()}/api/conversation/handoff`, {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify(handoffPayload),
        });
        if (r.ok) handoffResult = await r.json();
      } catch (e) {
        console.warn('[Handoff] 서버 요청 실패:', e);
      }

      // 2) 새 탭 생성
      const newIdx = chatSessions.length;
      const newSession = {
        id: 's-' + Date.now(),
        name: `대화 ${newIdx + 1}`,
        messages: [],
      };

      // 3) 새 탭 첫 메시지 — 인계 문서를 user 컨텍스트로 주입
      //    (chatHistory에 자동 포함되어 다음 호출 시 모델이 보고 이어 작업)
      if (handoffResult && handoffResult.content) {
        newSession.messages.push({
          role: 'user',
          content: '[자동 인계 — 이전 대화 이어가기]\n\n다음은 직전 대화의 요약입니다. 이 컨텍스트를 바탕으로 사용자의 다음 지시를 수행하세요.\n\n' + handoffResult.content,
          _handoff_marker: true,
        });
        newSession.messages.push({
          role: 'assistant',
          content: `이전 대화 인계 받았습니다. 이어서 작업할 준비 완료.\n\n인계 문서: \`${handoffResult.path}\``,
          _handoff_marker: true,
        });
        newSession.messages.push({
          role: 'system',
          content: `이전 대화 "${oldName}"가 길어져 자동 인계되었습니다. 인계 문서: ${handoffResult.path} (좌측 .generated 패널에서 확인 가능)`,
        });
      } else {
        newSession.messages.push({
          role: 'system',
          content: `[자동 새 대화] 이전 대화 "${oldName}"가 길어져서 새 탭으로 이어집니다. (인계 요약 실패 — 이전 탭에서 컨텍스트 확인 가능)`,
        });
      }

      chatSessions.push(newSession);
      activeSessionIdx = newIdx;
      try { renderChatTabs(); } catch(_) {}

      // 4) .generated 패널 새로고침
      try {
        const panel = document.querySelector('file-preview-panel');
        if (panel && typeof panel._refresh === 'function') panel._refresh();
        document.dispatchEvent(new CustomEvent('generated-folder:refresh'));
      } catch(_) {}

      // 5) 세션 저장
      try { saveConversation(); } catch(_) {}

      try { addLiveLog && addLiveLog('system', `대화 인계 완료 — ${handoffResult?.path || '(요약 실패)'} → 대화 ${newIdx + 1}`); } catch(_) {}
    } catch(e) { console.warn('[Handoff] 실패:', e); }
  })();

  input.value='';input.style.height='auto';
  // IME 조합 중인 경우 강제 완료
  input.blur(); input.focus();
  let content=text;
  if(state.attachedFiles.length){
    content=state.attachedFiles.map(f=>{
      if (f.kind === 'zip' || f.ext === 'zip') {
        // ZIP — 백엔드에서 압축 해제된 파일 목록을 시스템 메시지처럼 노출
        const lines = [];
        lines.push(`[첨부 ZIP: ${f.name}]`);
        if (f.extractDir) {
          lines.push(`압축 해제 위치: ${f.extractDir}`);
          lines.push(`총 ${f.totalFiles || 0}개 파일 (${((f.totalBytes||0)/1024).toFixed(0)}KB)`);
          const previewFiles = (f.files || []).slice(0, 50);
          if (previewFiles.length) {
            lines.push('내부 파일 목록:');
            previewFiles.forEach(pf => {
              lines.push(`  - ${pf.path} (${pf.size} bytes, ${pf.type})`);
            });
            if ((f.files || []).length > previewFiles.length) {
              lines.push(`  ... 외 ${(f.files.length - previewFiles.length)}개`);
            }
          }
          if (f.skipped && f.skipped.length) {
            lines.push(`스킵된 항목: ${f.skipped.length}개 (크기/경로 정책)`);
          }
          lines.push('필요 시 read_file 도구로 위 경로의 파일을 읽어 작업하세요.');
        } else if (f.extracting) {
          lines.push('(압축 해제 진행 중 — 잠시 후 다시 시도하세요)');
        }
        return lines.join('\n');
      }
      if(['png','jpg','jpeg'].includes(f.ext)) return `[이미지: ${f.name}]`;
      if(f.ext==='xlsx') return `[엑셀 파일: ${f.name} (${(f.size/1024).toFixed(0)}KB)]`;
      return `[파일: ${f.name}]`;
    }).join('\n')+(text?'\n\n'+text:'');
  }
  const userMsg={role:'user',content,attachments:[...state.attachedFiles]};
  state.messages.push(userMsg);state.attachedFiles=[];renderAttachedFiles();
  // 이 user 메시지를 뷰포트 최상단에 고정할 핀 인덱스 기록 (스트리밍 중 자동 스크롤 억제용)
  state._pinUserMsgIdx = state.messages.length - 1;
  state._pinAnchorSet = false;
  // 컨테이너 하단에 여유 공간(padding-bottom)을 미리 확보 → 답변이 짧아도 user 메시지를 뷰포트 최상단으로 올릴 수 있음
  // (이게 없으면 scrollHeight가 부족해 브라우저가 scrollTop을 clamp → 질문이 뷰포트 아래쪽에 머물러있게 됨)
  {
    const cc0 = document.getElementById('chat-messages');
    if(cc0){
      const spacer = Math.max(0, cc0.clientHeight - 80);
      cc0.style.scrollPaddingBottom = spacer + 'px';
      cc0.style.paddingBottom = spacer + 'px';
      state._pinSpacerPx = spacer;
    }
  }
  renderMessages();
  // user 메시지 노드를 뷰포트 최상단으로 스크롤 — 레이아웃 안정화 후 수행
  const _anchorUserToTop = () => {
    const cc = document.getElementById('chat-messages');
    if(!cc) return false;
    const userNodes = cc.querySelectorAll('.chat-msg.user');
    const target = userNodes[userNodes.length-1];
    if(!target) return false;
    const desired = Math.max(0, target.offsetTop - 4);
    cc.scrollTop = desired;
    // 실제 clamp 후 값 확인 — 근접하면 성공
    const actual = cc.scrollTop;
    state._pinAnchorSet = true;
    return Math.abs(actual - desired) < 2;
  };
  // [Fix: 순서 뒤집힘 방지] 긴 질문 입력 시 assistant placeholder가 user보다 먼저 DOM에
  //  커밋되어 "답변이 질문 위에 보이는" 현상 발생. 반드시 await로 user 메시지 DOM commit +
  //  스크롤 anchor를 확정한 뒤에 runSingle/runParallel(→ assistant push)로 진입해야 함.
  await new Promise(r => requestAnimationFrame(() => requestAnimationFrame(r)));
  if (!_anchorUserToTop()) {
    // 이미지/폰트 로딩으로 offsetTop이 밀리는 경우 한 프레임 더 대기
    await new Promise(r => requestAnimationFrame(r));
    _anchorUserToTop();
  }
  // 스트리밍 초반 assistant 렌더로 레이아웃이 변할 때 재보정
  setTimeout(_anchorUserToTop, 80);
  setTimeout(_anchorUserToTop, 240);

  const sendBtn = document.getElementById('send-btn');
  sendBtn.textContent = '취소';
  sendBtn.style.background = 'var(--color-error)';

  // 모드와 무관하게 추천 검사 — 단, 병렬 모드에서 이미 2개 이상 모델을 선택한 경우 억제
  // (사용자가 의도적으로 모델을 구성한 상태이므로 추천 불필요)
  let recHandled = false;
  const _skipRecommend = state.mode === 'parallel' && Array.isArray(state.parallelSlots) && state.parallelSlots.length >= 2;

  // === 이슈 1: 단일 이미지 생성 의도 — 이미지 특화 모델 추천 카드 표시 ===
  // 이전에는 추천 카드를 억제하고 텍스트 안내만 띄웠으나, 사용자가 어떤 이미지
  // 특화 모델(Stable Image / Nova Canvas / Titan)이 선택되는지 카드로 보길 원함.
  // 카드는 informational 모드(모델 교체 없음)로 표시되고, "생성 진행"을 누르면
  // 채팅 모델을 유지한 채 정상 흐름으로 진행한다 (generate_image 도구가 자동 라우팅).
  const _imgGenIntent = /이미지\s*(생성|만들|그려|그리|create|generate)|이미지로\s*(만들|생성|그려|변환)|사진.*만들|일러스트|로고.*디자인|배너.*만들|아이콘.*만들|썸네일/i.test(content)
    && !/(분석|설명|읽어|읽기|수정|편집|지워|제거|배경|inpaint|outpaint)/i.test(content);
  const _hasImgAttach = Array.isArray(state.attachedFiles)
    && state.attachedFiles.some(f => /\.(png|jpg|jpeg|webp|gif|bmp)$/i.test(f.name || ''));

  if (!_skipRecommend && typeof getModelRecommendation === 'function') {
    const rec = getModelRecommendation(content, state.selectedModel?.id || '');
    if (rec) {
      // === 생성 작업(ppt/pdf/docx/이미지 등) — 파이프라인 자동 실행 ===
      // 사용자 요구: 어떤 모델로 물어보든 문서/이미지 생성은 파이프라인을 최우선으로
      // 쓰고 작업이 바로 이루어지게 한다. 카드 클릭 없이 구조→내용→이미지컨셉→
      // 이미지(Vertex+Stability) 파이프라인을 즉시 실행한다. 단, 사용자가 직접
      // 병렬 모드를 구성한 경우는 존중한다.
      if (rec.pipelineFirst && rec.options && rec.options.pipeline
          && state.mode !== 'parallel' && typeof runPipeline === 'function') {
        rec.recommend = 'pipeline-run';
        rec.pipelineStages = rec.options.pipeline.stages;
        const _stageLabels = rec.options.pipeline.stages.map(s => s.label).join(' → ');
        state.messages.push({
          role: 'system',
          content: `파이프라인 자동 실행 (${rec.description}) — ${_stageLabels}`,
        });
        renderMessages();
        addLiveLog('system', `파이프라인 자동 실행 — ${_stageLabels}`);
        const applied = applyRecommendation(rec);
        if (applied && applied.type === 'pipeline') {
          await runPipeline(content, applied.stages);
          recHandled = true;
        }
      } else {
        const choice = await showRecommendationCard(rec);
        if (choice === 'accept') {
          if (rec.recommend === 'image-proceed') {
            // 이미지 생성 — 모델 교체 없이 안내만 남기고 기본 흐름으로 진행
            addLiveLog('system', '이미지 생성 — 특화 모델 자동 선택(generate_image)');
          } else {
            const applied = applyRecommendation(rec);
            if (applied && applied.type === 'parallel') {
              await runParallel(content);
              recHandled = true;
            } else if (applied && applied.type === 'pipeline') {
              await runPipeline(content, applied.stages);
              recHandled = true;
            }
            // single 전환은 아래 기본 흐름에서 처리됨
          }
        }
      }
    } else if (_imgGenIntent && !_hasImgAttach && state.mode !== 'parallel') {
      // 추천 후보가 없으면(이미지 모델이 카탈로그에 없을 때) 최소한 텍스트 안내
      state.messages.push({
        role: 'system',
        content: '이미지 작업 감지 — 이미지 특화 모델(Stable Image / Nova Canvas / Titan 등)로 생성합니다. 채팅 모델은 작업을 지휘하고, 실제 이미지는 최적 이미지 모델이 자동 선택되어 생성됩니다.',
      });
      renderMessages();
      addLiveLog('system', '이미지 생성 의도 감지 — generate_image 도구가 이미지 특화 모델 자동 선택');
    }
  }

  if (!recHandled) {
    // === Intent Classifier 기반 라우팅 ===
    // LLM으로 의도를 분류하고, 결과에 따라 최적 실행 경로를 선택한다.
    // 분류 실패 시 기존 모드 기반 fallback.
    // 타임아웃은 백엔드 classify_intent의 게이트웨이 타임아웃(10초)보다 약간 길게(12초)
    // 잡아, 정상적인 게이트웨이 지연(콜드스타트/네트워크)에서 프론트가 먼저 abort해
    // 불필요한 fallback으로 빠지는 것을 막는다. 백엔드는 어떤 경우에도 안전한 기본값
    // JSON을 반환하므로(SSO 만료 포함), 12초 안에는 거의 항상 응답이 온다.
    let intent = null;
    try {
      const _classifyCtrl = new AbortController();
      const _classifyTimeout = setTimeout(() => _classifyCtrl.abort(), 12000);
      const classifyResp = await fetch(`${apiBase()}/api/agents/classify-intent`, {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ prompt: content, awsProfile: state.settings?.awsProfile || 'bedrock-gw', bedrockUser: state.settings?.bedrockUser || '' }),
        signal: _classifyCtrl.signal,
      });
      clearTimeout(_classifyTimeout);
      if (classifyResp.ok) intent = await classifyResp.json();
    } catch (e) {
      // AbortError(타임아웃)와 네트워크 오류를 구분해 사용자 친화 메시지로 안내.
      // 의도 분류 실패는 치명적이지 않다 — 아래에서 모드 기반 fallback으로 정상 진행한다.
      const _isAbort = e && (e.name === 'AbortError' || /aborted/i.test(e.message || ''));
      if (_isAbort) {
        console.warn('[Intent] 분류 타임아웃(12초 초과) — 기본 라우팅으로 진행. '
          + '게이트웨이 지연 또는 AWS SSO 세션 만료 가능성. 지속되면 재로그인하세요.');
      } else {
        console.warn('[Intent] 분류 실패 — 기본 라우팅으로 진행:', e && e.message);
      }
    }

    if (intent && intent.intent) {
      // 백엔드가 SSO/자격증명 만료를 감지하면 needsReauth=true로 표시한다.
      // 의도 분류는 fallback으로 진행하되, 사용자에게 재로그인을 1회 안내한다
      // (반복 안내 방지를 위해 세션당 1번만). 실제 게이트웨이 호출도 곧 실패하므로
      // 선제적으로 알려주는 게 정확하다.
      if (intent.needsReauth && !state._reauthNotified) {
        state._reauthNotified = true;
        addLiveLog('error', 'AWS SSO 세션 만료 감지 — 재로그인이 필요합니다', intent.reasoning || '');
        try {
          state.messages.push({
            role: 'system',
            content: '⚠️ AWS SSO 세션이 만료되었습니다. 정확한 의도 분류·생성을 위해 재로그인해 주세요. (상단 프로파일 → 재로그인)',
          });
          renderMessages();
        } catch (_) {}
        try { if (typeof showSSODialog === 'function') showSSODialog(false); } catch (_) {}
      } else if (!intent.degraded) {
        // 정상 분류가 한 번이라도 성공하면 재안내 플래그를 리셋(재로그인 후 정상 복귀).
        state._reauthNotified = false;
      }
      addLiveLog('system', `의도 분류: ${intent.intent} (tools=${intent.needs_tools}, parallel=${intent.parallel_useful})`);
      // _apiBody가 다음 호출에서 task 라우팅에 사용
      state._lastIntent = intent;

      // 사용자가 단일 모드면 오케스트레이터 우회 — 단일 모델로만 응답.
      // 사용자가 모델 선택 후 단일 모드를 골랐다면 그 모델이 답변하길 기대하는 것.
      const userPickedSingle = state.mode === 'single';

      if (intent.needs_tools && (intent.intent === 'file_generation' || intent.intent === 'code_change')) {
        // 도구가 필요한 작업
        if (!userPickedSingle && (intent.complexity === 'complex' || (intent.file_types && intent.file_types.length > 1))) {
          // 사용자가 병렬 모드일 때만 오케스트레이터 자동 활성
          await runOrchestrated(content);
        } else {
          await runSingle(content); // 사용자 선택 모델이 도구 호출
        }
      } else if (intent.parallel_useful && state.mode === 'parallel') {
        await runParallel(content);
      } else if (state.mode === 'parallel' && !intent.needs_tools) {
        await runParallel(content);
      } else {
        await runSingle(content);
      }
    } else {
      // 분류 실패 fallback
      if (state.mode === 'parallel') await runParallel(content);
      else await runSingle(content);
    }
  }

  sendBtn.textContent = '전송';
  sendBtn.style.background = 'var(--color-accent)';
}

// ===== Single Mode =====
// 프로바이더별 주요 용도 설명 (모델 드롭다운 그룹 타이틀에 표시)
function _providerUsage(provider) {
  const p = (provider || '').toUpperCase();
  const map = {
    'ANTHROPIC': '코딩·추론·문서 생성',
    'AMAZON': '범용·이미지·비디오 생성',
    'META': '범용 대화·코딩',
    'MISTRAL': '코딩·멀티모달·빠른 응답',
    'STABILITY AI': '이미지 생성 전용',
    'DEEPSEEK': '추론·수학·코딩',
    'QWEN': '코딩·추론·대규모 분석',
    'GOOGLE': '멀티모달·경량 추론',
    'NVIDIA': '멀티모달·비전 분석',
    'COHERE': '검색·요약·RAG',
    'AI21 LABS': '텍스트 생성·요약',
    'WRITER': '비즈니스 문서·요약',
    'LUMA': '비디오 생성',
    'MOONSHOT': '추론·긴 컨텍스트',
    'MINIMAX': '대화·창작',
    'TWELVE LABS': '비디오 이해·분석',
    'Z.AI': '중국어·다국어 추론',
  };
  return map[p] || '';
}

// 모델별 예상 응답 속도
function _modelSpeed(modelId) {
  const id = (modelId || '').toLowerCase();
  if (id.includes('opus')) return { label: '~15s', color: 'var(--color-warning)' };
  if (id.includes('haiku')) return { label: '~3s', color: 'var(--color-success)' };
  if (id.includes('sonnet')) return { label: '~5s', color: 'var(--color-success)' };
  if (id.includes('r1')) return { label: '~20s', color: 'var(--color-warning)' };
  if (id.includes('llama') || id.includes('mistral') || id.includes('nova')) return { label: '~5s', color: 'var(--color-success)' };
  return { label: '', color: 'var(--color-text-muted)' };
}

function _apiBody(extra) {
  const profile = state.settings?.awsProfile || 'bedrock-gw';
  const user = state.settings?.bedrockUser || '';
  const body = { awsProfile: profile, bedrockUser: user, ...extra };
  // intent classifier 결과 전달 — 서버가 task별로 다양한 모델로 라우팅 가능하게.
  // (e.g. reasoning → DeepSeek-R1, long_context → Qwen3, simple_qa → Nova Lite)
  if (state._lastIntent && typeof state._lastIntent === 'object') {
    if (state._lastIntent.intent) body.intent = state._lastIntent.intent;
    if (typeof state._lastIntent.needs_tools === 'boolean') body.needs_tools = state._lastIntent.needs_tools;
  }
  // 프로젝트 컨텍스트
  if (state.folderPath) {
    body.projectPath = state.folderPath;
  }
  // 활성 템플릿(있으면) 전달 — pptx-template-styling 요구사항 5.1.
  // 빈 문자열/미설정 → 무템플릿(기존 동작 보존, 요구사항 5.2).
  if (state.activeTemplateId) {
    body.templateId = state.activeTemplateId;
  }
  // 원격 모드 표시 — ai_engine이 로컬에서 실행 중일 때 원격 경로 접근 불가 알림
  const remote = (typeof window !== 'undefined' && window.__remoteStatus) || null;
  if (remote && remote.state === 'connected') {
    body.isRemote = true;
    body.remoteAlias = remote.alias || '';
    // 프로젝트 파일 목록을 직접 포함 (ai_engine이 로컬이면 원격 경로 접근 불가)
    // 1순위: _projectStats (통계 탭에서 캐시된 전체 파일 목록)
    // 2순위: 사이드바 file-tree DOM 의 최상위 항목들 (즉시 사용 가능)
    if (_projectStats && _projectStats.files && _projectStats.files.length > 0) {
      body.projectFiles = _projectStats.files.map(f => f.path).slice(0, 100);
    } else {
      try {
        const tree = document.getElementById('file-tree');
        if (tree) {
          const items = tree.querySelectorAll('.file-tree-item[data-entry-path]');
          const names = [];
          items.forEach(it => {
            const p = it.dataset.entryPath || '';
            if (p && state.folderPath && p.startsWith(state.folderPath)) {
              const rel = p.slice(state.folderPath.length).replace(/^\//, '');
              if (rel) names.push(rel);
            }
          });
          if (names.length > 0) {
            body.projectFiles = names.slice(0, 100);
          }
        }
      } catch {}
    }
  }
  // 현재 열린 파일
  if (state.activeTab && monacoEditor) {
    body.openFile = state.activeTab.replace(state.folderPath + '/', '');
    try {
      const model = monacoEditor.getModel();
      if (model) body.openFileContent = model.getValue().substring(0, 15000);
    } catch {}
  }
  // 대화 히스토리 (최근 10개) — 역할별 토큰 예산 차등 적용 [Fix #1]
  //  - user 메시지: 6000자 (긴 질문/코드 맥락 유지)
  //  - 병렬 합본 (hiddenInChat + isParallel): 800자로 축소 — 토큰 폭발 방지 핵심
  //  - 합의/일반 assistant: 4000자
  //  - 오류 메시지 제외
  // [할루시네이션 방지] assistant 응답에서 거짓 완료 주장은 history에 넣기 전 정리
  const _sanitizeAssistant = (txt) => {
    if (!txt) return txt;
    let t = txt;
    // 가짜 .generated/ 경로의 마크다운 이미지 → 명시적 표시
    t = t.replace(/!\[[^\]]*\]\(\.generated\/[^)]+\)/g, '[과거 응답: 실제 파일 미생성]');
    // 표 안의 "✅ 완료" 같은 거짓 셀
    t = t.replace(/\|[^\n|]*?(✅|완료|완성|생성됨|created|done)[^\n|]*?\|/g, '| (실제 미생성) |');
    // "이전 작업이 완료됨" 같은 표현
    t = t.replace(/(이전|위)\s*작업[^.]{0,30}(이미|모두)\s*완료/g, '(이전 응답에서 완료 주장이 있었으나 실제 파일은 생성되지 않았음)');
    // 가짜 ✅/🎉 완료
    t = t.replace(/(✅|🎉)\s*[^\n.]{0,30}(생성|저장|작성)\s*(완료|되었|됨)/g, '*(실제 미생성)*');
    return t;
  };
  const _truncateMsg = (m) => {
    const c = m.content || '';
    if (m.role === 'user') return c.substring(0, 6000);
    if (m.isParallel && m.hiddenInChat) {
      // 병렬 합본은 첫 N모델 이름만 남긴 초압축 요약으로 + sanitize
      const head = _sanitizeAssistant(c.substring(0, 400));
      return head + (c.length > 400 ? `\n\n…[병렬 합본 축약됨: ${m.parallelCount || '?'}개 모델, 원본 ${c.length}자]` : '');
    }
    // assistant 응답은 sanitize 후 자르기
    if (m.isConsensus) return _sanitizeAssistant(c).substring(0, 4000);
    return _sanitizeAssistant(c).substring(0, 4000);
  };
  const history = (state.messages || [])
    .filter(m => {
      // handoff_marker는 한 번만(가장 최근) 포함 — 누적되면 컨텍스트 폭발
      if (m._handoff_marker) return true;
      if (m.role === 'user') return true;
      if (m.role === 'assistant' && m.content && !m.content.includes('[오류:') && !m.content.includes('[합의 오류:')) return true;
      return false;
    })
    .slice(-10)
    .map(m => {
      // handoff_marker 메시지는 그대로 (잘림 없음 — 이미 요약된 인계 문서)
      if (m._handoff_marker) {
        return { role: m.role, content: (m.content || '').substring(0, 4000) };
      }
      return { role: m.role, content: _truncateMsg(m) };
    });
  if (history.length) body.chatHistory = history;
  // 세션 ID
  body.sessionId = chatSessions[activeSessionIdx]?.id || 'default';
  return body;
}

// [Fix #2] SSE idle timeout 헬퍼 — 서버가 응답 중 끊겨도 감지
//   reader.read()를 idleMs 내에 해결 못 하면 강제로 에러 발생 → catch에서 UI 알림.
//   기본값을 10분으로 둔다: 서버가 thinking/도구 실행 중 ~12초마다 heartbeat를
//   보내므로 정상 작업은 절대 idle하지 않고, 이 백스톱은 *진짜* 죽은 연결만 잡는다.
//   (이전 180초는 reasoning 모델/긴 이미지 생성 중 거짓 "끊김"을 유발했음)
async function _readWithIdleTimeout(reader, idleMs = 600000) {
  let timer;
  const timeoutP = new Promise((_, reject) => {
    timer = setTimeout(() => reject(new Error(`스트림 ${Math.floor(idleMs/1000)}초 무응답 — 끊김 감지`)), idleMs);
  });
  try {
    return await Promise.race([reader.read(), timeoutP]);
  } finally {
    clearTimeout(timer);
  }
}

// SSE 스트림 읽기 공통 함수
async function readSSEStream(resp, { onText, onTool, onSlot, onError, onRaw } = {}) {
  const reader = resp.body.getReader(), dec = new TextDecoder();
  let buf = '';
  while (true) {
    const { done, value } = await _readWithIdleTimeout(reader);
    if (done) break;
    buf += dec.decode(value, { stream: true });
    const events = buf.split('\n\n');
    buf = events.pop() || '';
    for (const event of events) {
      const trimmed = event.trim();
      if (!trimmed || !trimmed.startsWith('data: ')) continue;
      const d = trimmed.slice(6);
      if (d === '[DONE]') continue;
      try {
        const parsed = JSON.parse(d);
        if (parsed.error)  { onError?.(parsed.error); continue; }
        if (parsed.slotId) { onSlot?.(parsed); continue; }
        if (parsed.tool)   { onTool?.(parsed); continue; }
        if (parsed.text)   { onText?.(parsed.text); continue; }
        // JSON이지만 알 수 없는 형식
        onRaw?.(d);
      } catch {
        // JSON이 아니면 텍스트 그대로
        onRaw?.(d);
      }
    }
  }
}

// 간단한 질문인지 판단
function isSimpleQuery(prompt) {
  const p = prompt.trim().toLowerCase();
  // 복잡한 작업 — 명시적 코드 작업 요청만
  const complexPatterns = [
    '구현해', '작성해', '만들어줘', '생성해', '코드를 ', '리팩토링해', '수정해줘', '변경해줘',
    '추가해줘', '삭제해줘', '파일을 만', '함수를 만', '클래스를 만', '컴포넌트를 만',
    '디버그해', '배포해', '빌드해',
    'implement ', 'create a ', 'build a ', 'refactor ', 'write code', 'fix the bug',
    'deploy ', 'generate ', 'develop a ', 'design a ',
  ];
  // 500자 이상이면 복잡한 작업
  if (p.length > 500) return false;
  for (const kw of complexPatterns) {
    if (p.includes(kw)) return false;
  }
  return true;
}

async function runSingle(prompt) {
  // simple_qa나 일반 채팅이면 도구 없는 채팅으로, 코드/파일 작업은 에이전트로
  // intent가 안 잡히면 무조건 에이전트 모드 (기존 동작 유지)
  const looksSimple = isSimpleQuery(prompt);
  if (looksSimple) {
    await runSimpleChat(prompt);
  } else {
    await runAgentWorkflow(prompt);
  }
}

// 멀티-에이전트 오케스트레이터 호출 (Planner → N agents with tools → Merger)
async function runOrchestrated(prompt) {
  if (!prompt) return;
  state.isStreaming = true;
  state._streamStartTime = Date.now();
  state._abortController = new AbortController();

  // 사용자 메시지로 prompt 등록 (이미 sendMessage에서 push했으면 중복 방지)
  const lastMsg = state.messages[state.messages.length - 1];
  const _alreadyPushed = lastMsg && lastMsg.role === 'user' && lastMsg.content === prompt;
  if (!_alreadyPushed) {
    state.messages.push({ role: 'user', content: prompt });
  }
  state.messages.push({
    role: 'system',
    content: '멀티-에이전트 오케스트레이션 시작 — Planner가 작업을 분해하고 N개 에이전트가 도구로 실행합니다.',
  });
  // 공통 thinking placeholder
  state.messages.push({ role:'assistant', content:'', _thinking: true, _thinkingLabel: '오케스트레이터 실행 중' });
  renderMessages();

  // 오케스트레이터 진행 중 thinking 카운터 자동 갱신 (서버 이벤트 없어도 초가 멈추지 않음)
  const _orchTickTimer = setInterval(() => {
    if (!state.isStreaming) { clearInterval(_orchTickTimer); return; }
    const ind = document.querySelector('.thinking-indicator');
    if (!ind) return;
    const elapsed = Math.floor((Date.now() - (state._streamStartTime || Date.now())) / 1000);
    const timeText = elapsed >= 3600 ? `${Math.floor(elapsed/3600)}h ${Math.floor((elapsed%3600)/60)}m` : elapsed >= 60 ? `${Math.floor(elapsed/60)}m ${elapsed%60}s` : `${elapsed}s`;
    const thinkMsg = state.messages.find(m => m._thinking);
    const label = (thinkMsg && thinkMsg._thinkingLabel) || '오케스트레이터 실행 중';
    ind.innerHTML = `<span class="thinking-dots"><span></span><span></span><span></span></span> ${esc(label)} ${timeText}`;
  }, 1000);

  // 가용 모델 중 가장 강한 worker 선택 (sonnet > opus > haiku 우선)
  const allChat = (typeof ALL_MODELS !== 'undefined') ? ALL_MODELS.filter(m => m.capabilities && m.capabilities.chat) : [];
  const _findByPrefix = (pfx) => allChat.find(m => (m.id || '').toLowerCase().includes(pfx))?.id;
  const workerModel = _findByPrefix('claude-sonnet-4') || _findByPrefix('claude-opus-4') || _findByPrefix('claude-sonnet') || _findByPrefix('claude') || (allChat[0]?.id);
  const plannerModel = _findByPrefix('claude-opus-4') || workerModel;

  const agentStates = new Map(); // taskId → {role, title, status, toolCount}
  let mergeReport = '';

  try {
    const resp = await fetch(`${apiBase()}/api/agents/run-orchestrated`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(_apiBody({
        prompt,
        plannerModel,
        workerModel,
        mergerModel: plannerModel,
        maxAgents: 5,
      })),
      signal: state._abortController.signal,
    });
    if (!resp.ok) throw new Error(`서버 오류: ${resp.status}`);

    const reader = resp.body.getReader();
    const dec = new TextDecoder();
    let buf = '';

    // 무한루프 방지를 위한 wall-clock timeout (기본 60분, env로 조정 가능)
    // 사용자가 명시적으로 취소하지 않은 자동 timeout과 구분
    const HARD_TIMEOUT_MS = (typeof window.AE_ORCH_TIMEOUT_MIN === 'number' ? window.AE_ORCH_TIMEOUT_MIN : 60) * 60 * 1000;
    let _autoAbortReason = '';  // 자동 취소 사유 추적 (wall-clock vs user)
    const hardTimer = setTimeout(() => {
      _autoAbortReason = 'wall-clock';
      try { state._abortController?.abort(); } catch (_) {}
      addLiveLog('error', `오케스트레이션 wall-clock timeout (${HARD_TIMEOUT_MS / 60000}분) — 강제 중단`);
    }, HARD_TIMEOUT_MS);

    try {
      while (true) {
        // 오케스트레이션은 Planner→N Workers→Merger로 시간이 오래 걸림 → idle timeout 60분
        // 서버는 20초마다 heartbeat 송출하므로 정상 동작 시 절대 idle하지 않음
        const { done, value } = await _readWithIdleTimeout(reader, 60 * 60 * 1000);
        if (done) break;
        buf += dec.decode(value, { stream: true });
        const events = buf.split('\n\n'); buf = events.pop() || '';
        for (const event of events) {
          const trimmed = event.trim();
          if (!trimmed || !trimmed.startsWith('data: ')) continue;
          const d = trimmed.slice(6);
          if (d === '[DONE]') continue;
          try {
            const ev = JSON.parse(d);
            if (ev.heartbeat) continue;

          if (ev.type === 'model_routing') {
            // 사용자가 선택한 모델이 도구 호출 미지원이라 자동 변경됨
            const original = (ev.original || '').replace(/^us\.|^eu\.|^global\./, '');
            const routed = (ev.routedTo || '').replace(/^us\.|^eu\.|^global\./, '');
            state.messages.push({
              role: 'system',
              content: `모델 자동 라우팅 — ${original} → ${routed} (${ev.reason || '도구 호출 안정성'})`,
            });
            renderMessages();
          } else if (ev.type === 'plan') {
            const subtasks = ev.subtasks || [];
            state.messages.push({
              role: 'system',
              content: `작업 분해 완료 — ${subtasks.length}개 에이전트 할당:\n` +
                subtasks.map((s, i) => `  ${i+1}. [${s.id}] ${s.role}: ${s.title}`).join('\n'),
            });
            renderMessages();
          } else if (ev.type === 'agent_start') {
            agentStates.set(ev.taskId, { role: ev.role, title: ev.title, status: 'running', toolCount: 0 });
            addLiveLog('system', `[${ev.taskId}] ${ev.role} 시작: ${ev.title}`);
            // 진행 중 라벨 업데이트
            const thinkMsg = state.messages.find(m => m._thinking);
            if (thinkMsg) {
              const running = [...agentStates.values()].filter(a => a.status === 'running').length;
              const done = [...agentStates.values()].filter(a => a.status === 'done').length;
              thinkMsg._thinkingLabel = `오케스트레이터 실행 중 (${done}/${agentStates.size} 완료, ${running} 실행)`;
            }
          } else if (ev.type === 'agent_tool') {
            addLiveLog('tool', `[${ev.taskId}] ${ev.tool} ${ev.status}`, ev.input ? JSON.stringify(ev.input).substring(0, 100) : '');
            const a = agentStates.get(ev.taskId);
            if (a && ev.status === 'done') a.toolCount++;
          } else if (ev.type === 'agent_done') {
            const a = agentStates.get(ev.taskId);
            if (a) { a.status = 'done'; a.toolCount = ev.toolCount || a.toolCount; }
            addLiveLog('system', `[${ev.taskId}] 완료 — 도구 ${ev.toolCount || 0}회 사용`);
            // 진행 중 라벨 업데이트
            const thinkMsg = state.messages.find(m => m._thinking);
            if (thinkMsg) {
              const running = [...agentStates.values()].filter(a => a.status === 'running').length;
              const done = [...agentStates.values()].filter(a => a.status === 'done').length;
              thinkMsg._thinkingLabel = `오케스트레이터 실행 중 (${done}/${agentStates.size} 완료, ${running} 실행)`;
            }
          } else if (ev.type === 'agent_error') {
            const a = agentStates.get(ev.taskId);
            if (a) a.status = 'error';
            addLiveLog('error', `[${ev.taskId}] 오류: ${ev.error || ''}`);
            const thinkMsg = state.messages.find(m => m._thinking);
            if (thinkMsg) {
              const running = [...agentStates.values()].filter(a => a.status === 'running').length;
              const errs = [...agentStates.values()].filter(a => a.status === 'error').length;
              thinkMsg._thinkingLabel = `오케스트레이터 실행 중 (${errs}개 오류, ${running} 실행)`;
            }
          } else if (ev.type === 'merge') {
            mergeReport = ev.report || ev.text || '';
            // 서버가 results를 함께 보낸 경우 — agentStates가 비어있으면 채워넣음
            if (Array.isArray(ev.results) && agentStates.size === 0) {
              for (const r of ev.results) {
                if (r && r.taskId) {
                  agentStates.set(r.taskId, {
                    role: r.role || 'Worker',
                    title: r.title || '',
                    status: r.status === 'done' ? 'done' : 'error',
                    toolCount: Array.isArray(r.tools) ? r.tools.length : 0,
                  });
                }
              }
            }
            // 최종 통합 보고서를 assistant 메시지로
            state.messages.push({ role: 'assistant', content: mergeReport });
            renderMessages();
          } else if (ev.type === 'error') {
            state.messages.push({ role: 'system', content: `오케스트레이션 오류: ${ev.message || ev.error || ''}` });
            renderMessages();
          }
        } catch (_e) { /* parse fail — skip */ }
      }
      }
    } finally {
      clearTimeout(hardTimer);
    }

    // 완료 요약
    const doneCount = [...agentStates.values()].filter(a => a.status === 'done').length;
    const errCount = [...agentStates.values()].filter(a => a.status === 'error').length;
    const noToolCount = [...agentStates.values()].filter(a => a.status === 'done' && a.toolCount === 0).length;
    const elapsed = Math.floor((Date.now() - (state._streamStartTime || Date.now())) / 1000);
    // thinking placeholder 제거
    state.messages = state.messages.filter(m => !m._thinking);

    // 완전 실패 — 에이전트가 하나도 시작 안 됨 (Planner 실패 또는 서버 NameError 등)
    if (agentStates.size === 0) {
      state.messages.push({
        role: 'system',
        content: '오케스트레이션 실패 — 에이전트가 시작되지 않았습니다. 서버 로그를 확인하거나 다시 시도해주세요.',
      });
      state.messages.push({
        role: 'system',
        _isRecommendCard: true,
        _recommendData: {
          title: '에이전트 시작 실패',
          reason: 'Planner가 작업을 분해하지 못했거나 서버 측 오류가 발생했습니다. 다시 시도하거나 단순 모델 호출로 전환할 수 있습니다.',
          actions: [
            { key: 'retry-orchestrate', label: '오케스트레이션 재시도', primary: true },
            { key: 'switch-single', label: '단일 모델로 전환' },
          ],
          hasHallucination: false,
          originalPrompt: prompt,
          doneResults: [],
        },
        content: '[추천] 재시도 옵션',
      });
      renderMessages();
      state.isStreaming = false;
      _releaseUserPin && _releaseUserPin();
      saveConversation();
      return;
    }

    state.messages.push({
      role: 'system',
      content: `오케스트레이션 완료 — ${doneCount}개 성공, ${errCount}개 실패` +
        (noToolCount > 0 ? `, ${noToolCount}개는 도구 미사용 (실제 파일 생성 안 됨)` : '') +
        ` (${fmtElapsed(elapsed)})`,
    });

    // .generated 폴더 패널 즉시 새로고침 — fs.watch 이벤트가 누락되는 케이스 대응
    try {
      const panel = document.querySelector('file-preview-panel');
      if (panel) {
        // project-path가 미설정이면 지금 설정 (사용자가 탭을 안 클릭했을 수도)
        const cur = panel.getAttribute('project-path') || '';
        const want = state.folderPath || window.__workstationCwd || '';
        if (want && cur !== want) {
          if (window.__workstationCwd && !panel._workstationCwd) {
            panel._workstationCwd = window.__workstationCwd;
          }
          panel.setAttribute('project-path', want);
        }
        if (typeof panel._refresh === 'function') {
          await panel._refresh();
        }
      }
      document.dispatchEvent(new CustomEvent('generated-folder:refresh'));
    } catch (_e) { /* best-effort */ }

    // 후속 추천 카드 — 도구를 사용하지 않은 에이전트가 있으면 재시도 권유
    if (noToolCount > 0 && doneCount > 0) {
      state.messages.push({
        role: 'system',
        _isRecommendCard: true,
        _recommendData: {
          title: '일부 에이전트가 파일을 생성하지 않았습니다',
          reason: `${noToolCount}개 에이전트가 도구를 사용하지 않고 텍스트만 출력했습니다. 더 강한 모델로 재시도하면 실제 파일이 생성될 가능성이 높습니다.`,
          actions: [
            { key: 'retry-stronger', label: '더 강한 모델로 재시도', primary: true },
            { key: 'view-generated', label: '.generated 폴더 확인' },
          ],
          hasHallucination: true,
          originalPrompt: prompt,
          doneResults: [],
        },
        content: '[추천] 일부 에이전트 재시도',
      });
    } else if (doneCount > 0) {
      state.messages.push({
        role: 'system',
        _isRecommendCard: true,
        _recommendData: {
          title: '작업 완료 — 다음 단계',
          reason: `${doneCount}개 에이전트가 작업을 완료했습니다. 결과를 검토하거나 추가 작업을 진행할 수 있습니다.`,
          actions: [
            { key: 'view-generated', label: '.generated 폴더 확인', primary: true },
            { key: 'refine', label: '추가 요청' },
          ],
          hasHallucination: false,
          originalPrompt: prompt,
          doneResults: [],
        },
        content: '[추천] 작업 완료',
      });
    }
    renderMessages();
  } catch (e) {
    // AbortError 사유 정확히 구분 — 사용자 vs wall-clock vs 네트워크 문제
    let errMsg;
    let abortReason = '';
    if (e.name === 'AbortError') {
      if (state._userInitiatedAbort) {
        errMsg = '사용자가 취소했습니다.';
        abortReason = 'user';
      } else if (typeof _autoAbortReason !== 'undefined' && _autoAbortReason === 'wall-clock') {
        errMsg = `오케스트레이션 시간 초과 (${HARD_TIMEOUT_MS / 60000}분) — 자동 중단됨`;
        abortReason = 'timeout';
      } else {
        // AbortController가 abort됐지만 사유 불명 — 네트워크 끊김 등
        errMsg = '연결이 끊겼습니다. 재시도 가능합니다.';
        abortReason = 'network';
      }
    } else {
      errMsg = e.message || '알 수 없는 오류';
      abortReason = 'error';
    }
    state._userInitiatedAbort = false;  // 다음 호출 위해 리셋

    state.messages = state.messages.filter(m => !m._thinking);

    // === 체크포인트 저장 — 부분 성공한 에이전트 결과 보존 ===
    // 완료된 verifiedFiles와 doneCount/agentStates를 cache에 저장 → 재시도 시 활용
    const partialDone = [...agentStates.values()].filter(a => a.status === 'done');
    const partialFiles = [];
    for (const [, a] of agentStates) {
      if (a.status === 'done' && a.toolCount > 0) {
        partialFiles.push({ taskId: a.taskId || '?', role: a.role, title: a.title });
      }
    }
    state._lastOrchestrationCheckpoint = {
      originalPrompt: prompt,
      timestamp: Date.now(),
      doneAgents: partialDone.length,
      totalAgents: agentStates.size,
      abortReason,
    };

    const summary = abortReason === 'timeout' || abortReason === 'network'
      ? `오케스트레이션 ${errMsg} — 완료된 ${partialDone.length}/${agentStates.size}개 에이전트 결과는 보존됨. "이어서 생성"으로 미완료 작업만 재실행 가능.`
      : `오케스트레이션 오류: ${errMsg}`;
    state.messages.push({ role: 'system', content: summary });

    // 자동 중단인 경우 — 이어서 생성 카드 제공
    if ((abortReason === 'timeout' || abortReason === 'network') && agentStates.size > 0) {
      const incompleteIds = [];
      for (const [tid, a] of agentStates) {
        if (a.status !== 'done') incompleteIds.push(tid);
      }
      state.messages.push({
        role: 'system',
        _isRecommendCard: true,
        _recommendData: {
          title: '이어서 생성',
          reason: `${partialDone.length}개 에이전트는 완료됐지만 ${incompleteIds.length}개가 미완료입니다. 미완료 작업만 다시 실행하면 됩니다.`,
          actions: [
            { key: 'resume-orchestrate', label: '미완료 작업만 이어서 실행', primary: true },
            { key: 'retry-orchestrate', label: '전체 재시도' },
          ],
          hasHallucination: false,
          originalPrompt: prompt,
          incompleteIds,
          doneResults: [],
        },
        content: '[추천] 이어서 생성',
      });
    }

    addLiveLog('error', `오케스트레이션 실패: ${errMsg}`);
    renderMessages();
  }

  state.isStreaming = false;
  _releaseUserPin && _releaseUserPin();
  saveConversation();
}

// ===== 파이프라인 실행 — 여러 모델이 단계별로 순차 작업 =====
async function runPipeline(prompt, stages) {
  if (!Array.isArray(stages) || !stages.length) return runSingle(prompt);
  state.isStreaming = true;
  const originalModel = state.selectedModel;
  let aggregateOutput = '';

  state.messages.push({
    role: 'system',
    content: `**파이프라인 실행 시작** (${stages.length}단계)\n` +
      stages.map((s, i) => `${i + 1}. ${s.label} → ${s.model.name}`).join('\n'),
  });
  renderMessages();

  for (let i = 0; i < stages.length; i++) {
    const s = stages[i];
    let target = ALL_MODELS.find(m => m.id === s.model.id);
    // 이미지/비디오 단계 또는 카탈로그에 없는(가상 Vertex 등) 모델 id는 채팅이
    // 불가능하다(이미지 모델을 채팅 모델로 쓰면 게이트웨이가 거부 → 단계 실패).
    // 이런 단계는 실제 채팅 모델로 도구(generate_image/generate_pptx 등)를 호출하게 하고,
    // 실제 이미지 엔진(Vertex Nano Banana Pro / Stability)은 generate_image 내부에서
    // 자동 선택된다(서버 라우팅). 사용자 요구: 이미지 특화 모델 필수 사용.
    const _isMediaStage = s.task === 'image-gen' || s.task === 'video-gen'
      || /^(vertex\.|stability\.|amazon\.(nova-canvas|titan-image)|amazon\.nova-reel|luma\.)/.test(String(s.model.id || ''));
    if (!target || _isMediaStage) {
      target = originalModel
        || ALL_MODELS.find(m => /claude-sonnet-4/.test(m.id))
        || ALL_MODELS.find(m => /claude/.test(m.id))
        || ALL_MODELS[0]
        || s.model;
    }
    state.selectedModel = target;
    const btn = document.getElementById('model-dropdown-btn');
    if (btn) btn.textContent = (target.name || target.id) + ' ▾';
    addLiveLog('system', `[${i + 1}/${stages.length}] ${s.label}: ${target.name || target.id}`);

    const stagePrompt = i === 0
      ? prompt
      : `${prompt}\n\n--- 이전 단계 결과 ---\n${aggregateOutput}\n\n--- 현재 단계 (${s.label}) ---\n위 결과를 바탕으로 ${s.label} 작업을 진행해주세요.`;

    state.messages.push({ role: 'system', content: `▶ **단계 ${i + 1}: ${s.label}** (${target.name || target.id})` });
    renderMessages();

    const beforeIdx = state.messages.length;
    try {
      await runAgentWorkflow(stagePrompt);
    } catch (e) {
      addLiveLog('error', `파이프라인 단계 ${i + 1} 실패`, e?.message || String(e));
      break;
    }
    const newMsgs = state.messages.slice(beforeIdx);
    const lastAssistant = [...newMsgs].reverse().find(m => m.role === 'assistant');
    aggregateOutput = lastAssistant?.content || '';
  }

  if (originalModel) {
    state.selectedModel = originalModel;
    const btn = document.getElementById('model-dropdown-btn');
    if (btn) btn.textContent = (originalModel.name || originalModel.id) + ' ▾';
  }
  state.messages.push({ role: 'system', content: `**파이프라인 완료**` });
  renderMessages();
  state.isStreaming = false;
}

// 간단한 질문 — 워크플로우 없이 바로 응답
async function runSimpleChat(prompt) {
  state.isStreaming = true;
  state._streamStartTime = Date.now();
  state._abortController = new AbortController();
  const timeoutId = setTimeout(() => { if (state._abortController) state._abortController.abort(); }, 300000);
  addLiveLog('request', `채팅: ${state.selectedModel.name}`, prompt.substring(0, 100));
  const msg = { role:'assistant', content:'' };
  state.messages.push(msg);
  renderMessages();
  const _chatStartTime = Date.now();
  // 생각 중 경과 시간 — DOM 직접 업데이트 (전체 리렌더 방지)
  const thinkingTimer = setInterval(() => {
    if (!state.isStreaming) { clearInterval(thinkingTimer); return; }
    const el = document.querySelector('.thinking-indicator');
    if (el) {
      const elapsed = Math.floor((Date.now() - _chatStartTime) / 1000);
      const timeText = elapsed >= 3600 ? `${Math.floor(elapsed/3600)}h ${Math.floor((elapsed%3600)/60)}m` : elapsed >= 60 ? `${Math.floor(elapsed/60)}m ${elapsed%60}s` : `${elapsed}s`;
      el.innerHTML = `<span class="thinking-dots"><span></span><span></span><span></span></span> thinking ${timeText}`;
    }
  }, 1000);
  try {
    const resp = await fetch(`${apiBase()}/api/agents/run-stream`, {
      method:'POST', headers:{'Content-Type':'application/json'},
      body: JSON.stringify(_apiBody({ prompt, model: state.selectedModel.id })),
      signal: state._abortController.signal
    });
    clearTimeout(timeoutId);
    if (!resp.ok) throw new Error(`서버 응답 오류: ${resp.status}`);
    const reader = resp.body.getReader(), dec = new TextDecoder();
    let buf = '';
    while (true) {
      const { done, value } = await _readWithIdleTimeout(reader);
      if (done) break;
      buf += dec.decode(value, { stream:true });
      const events = buf.split('\n\n'); buf = events.pop() || '';
      for (const event of events) {
        const trimmed = event.trim();
        if (!trimmed || !trimmed.startsWith('data: ')) continue;
        const d = trimmed.slice(6);
        if (d === '[DONE]') continue;
        try {
          const p = JSON.parse(d);
          if (p.heartbeat) {
            // 모델 thinking 중 — idle 워치독 리셋용. 콘텐츠 아님(렌더 안 함).
            continue;
          }
          if (p.thinking) {
            // reasoning(생각 과정) — 본문에 누적하지 않고 별도 보관.
            msg._reasoning = (msg._reasoning || '') + p.thinking;
            continue;
          }
          if (p.tool) {
            // 도구 실행 이벤트 — 채팅에 표시하지 않음 (로그만)
            continue;
          }
          if (p.error) {
            // 모델 관련 에러 분류:
            // 1) max_tokens 한계 초과 → 자동 재시도 가능, 모델은 유지
            // 2) tool_use 미지원 → 자동 재시도 (도구 없이), 모델은 유지
            // 3) 권한/식별자 문제 → 일시적, 모델은 유지 (denylist 영구 제거 금지)
            const isMaxTokensError = /maximum tokens.*exceeds.*model limit|max tokens.*invalid/i.test(p.error);
            const isToolError = /tool[_\s-]?use.*not supported|tool[_\s-]?config.*invalid/i.test(p.error);
            const isAccessError = p.error.includes('not in allowed list') || p.error.includes('model_denied')
                || p.error.includes('model identifier is invalid') || p.error.includes('ValidationException')
                || p.error.includes('model_access_denied') || p.error.includes('AccessDeniedException');
            if (isMaxTokensError || isToolError || isAccessError) {
              const failedModelName = state.selectedModel?.name || state.selectedModel?.id || '모델';
              // 모델은 카탈로그에 유지 — denylist 영구 제거 금지
              if (isMaxTokensError) {
                msg.content = `${failedModelName}: 토큰 한계를 자동 조정해 재시도가 진행됩니다. 잠시만 기다려 주세요. 동일 오류 반복 시 다른 모델을 선택해보세요.`;
              } else if (isToolError) {
                msg.content = `${failedModelName}: 도구 호출을 지원하지 않아 도구 없이 재시도합니다.`;
              } else {
                msg.content = `${failedModelName} 호출 중 일시적 오류가 발생했습니다. 다시 시도하거나 다른 모델로 같은 작업을 수행할 수 있습니다.`;
              }
              msg._modelRemoved = false;
              addLiveLog('warning', `모델 일시 오류: ${failedModelName}`, p.error.substring(0, 200));
              continue;
            }
            // 토큰 만료 → 자동 재로그인 시도
            if (p.error.includes('expired') || p.error.includes('security token')) {
              addLiveLog('system', '토큰 만료 감지 — 자격증명 갱신 중...');
              try {
                const creds = await window.electronAPI?.getCredentials(state.settings?.awsProfile || '');
                if (creds) {
                  await fetch(`${apiBase()}/api/reset-cache`, {
                    method: 'POST', headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({ profile: state.settings?.awsProfile, bedrockUser: state.settings?.bedrockUser, credentials: creds }),
                  });
                  addLiveLog('system', '자격증명 갱신 완료 — 다시 질문해주세요');
                  msg.content = '자격증명이 갱신되었습니다. 다시 질문해 주세요.';
                  continue;
                }
              } catch {}
            }
            // 기타 일반 에러 — 로그에만 기록, UI는 간단한 안내
            addLiveLog('error', `내부 오류: ${p.error}`);
            msg.content = `오류가 발생했습니다: ${String(p.error).slice(0, 500)}`;
            continue;
          }
          if (p.text) { msg.content += p.text; continue; }
        } catch {}
        msg.content += d;
      }
      renderMessages();
    }
    trackUsage(prompt.length, msg.content.length);
    addLiveLog('response', `완료: ${state.selectedModel.name}`, `${msg.content.length}자`);
  } catch (e) {
    clearTimeout(timeoutId);
    const errMsg = e.name === 'AbortError' ? '요청 시간 초과 또는 취소됨' : e.message;
    msg.content += `\n[오류: ${errMsg}]`;
    addLiveLog('error', `채팅 실패: ${errMsg}`);
  }
  msg._elapsed = Math.floor((Date.now() - _chatStartTime) / 1000);
  clearInterval(thinkingTimer);
  state.isStreaming = false;
  _releaseUserPin();
  renderMessages();
  saveConversation();
}

// 복잡한 작업 — 에이전트 워크플로우 (계획→코드→리뷰→테스트→완료)
async function runAgentWorkflow(prompt) {
  state.isStreaming = true;
  state._streamStartTime = Date.now();
  state._abortController = new AbortController();
  const timeoutId = setTimeout(() => { if (state._abortController) state._abortController.abort(); }, 300000);
  addLiveLog('request', `에이전트: ${state.selectedModel.name}`, prompt.substring(0, 100));

  // ===== Checkpoint: 에이전트 작업 전 자동 스냅샷 =====
  let _checkpointCreated = false;
  if (state.folderPath && window.electronAPI?.gitStashPush) {
    try {
      const r = await window.electronAPI.gitStashPush(state.folderPath, `agent-checkpoint-${Date.now()}`);
      if (r.ok && !r.skipped) { _checkpointCreated = true; }
    } catch (_) {}
  }

  const wfId = 'wf-' + Date.now();
  const _wfNow = Date.now();
  const wf = { id:wfId, steps:[
    { name:'분석', status:'running', detail:'', startedAt:_wfNow, endedAt:null },
    { name:'작업 실행', status:'pending', detail:'', startedAt:null, endedAt:null },
    { name:'완료', status:'pending', detail:'', startedAt:null, endedAt:null },
  ]};
  const msg = { role:'assistant', content:'', workflow:wf, toolUses:[], _checkpointCreated };
  state.messages.push(msg);
  renderMessages();
  try {
    const resp = await fetch(`${apiBase()}/api/agents/run-agent`, {
      method:'POST', headers:{'Content-Type':'application/json'},
      body: JSON.stringify(_apiBody({ prompt, model: state.selectedModel.id })),
      signal: state._abortController.signal
    });
    clearTimeout(timeoutId);
    if (!resp.ok) throw new Error(`서버 응답 오류: ${resp.status}`);
    const reader = resp.body.getReader(), dec = new TextDecoder();
    let buf = '', toolCount = 0;
    wf.steps[0].status = 'done'; wf.steps[0].endedAt = Date.now(); wf.steps[0].detail = prompt.substring(0, 80); wf.steps[1].status = 'running'; wf.steps[1].startedAt = Date.now();
    renderMessages();
    while (true) {
      const { done, value } = await _readWithIdleTimeout(reader);
      if (done) break;
      buf += dec.decode(value, { stream:true });
      const events = buf.split('\n\n'); buf = events.pop() || '';
      for (const event of events) {
        const trimmed = event.trim();
        if (!trimmed || !trimmed.startsWith('data: ')) continue;
        const d = trimmed.slice(6);
        if (d === '[DONE]') continue;
        try {
          const p = JSON.parse(d);
          if (p.model_routing) {
            // 자동 모델 라우팅 알림
            const original = (p.original || '').replace(/^us\.|^eu\.|^global\./, '');
            const routed = (p.routedTo || '').replace(/^us\.|^eu\.|^global\./, '');
            state.messages.push({
              role: 'system',
              content: `모델 자동 라우팅 — ${original} → ${routed} (${p.reason || '도구 호출 안정성'})`,
            });
            renderMessages();
            continue;
          }
          if (p.error) {
            // 모델 관련 에러 분류 — denylist 영구 제거 금지
            const isMaxTokensError = /maximum tokens.*exceeds.*model limit|max tokens.*invalid/i.test(p.error);
            const isToolError = /tool[_\s-]?use.*not supported|tool[_\s-]?config.*invalid/i.test(p.error);
            const isAccessError = p.error.includes('not in allowed list') || p.error.includes('model_denied')
                || p.error.includes('model identifier is invalid') || p.error.includes('ValidationException')
                || p.error.includes('model_access_denied') || p.error.includes('AccessDeniedException');
            if (isMaxTokensError || isToolError || isAccessError) {
              const failedModelName = state.selectedModel?.name || state.selectedModel?.id || '모델';
              if (isMaxTokensError) {
                msg.content = `${failedModelName}: 토큰 한계를 자동 조정해 재시도가 진행됩니다. 잠시만 기다려 주세요. 동일 오류 반복 시 다른 모델을 선택해보세요.`;
              } else if (isToolError) {
                msg.content = `${failedModelName}: 도구 호출을 지원하지 않아 도구 없이 재시도합니다.`;
              } else {
                msg.content = `${failedModelName} 호출 중 일시적 오류가 발생했습니다. 다시 시도하거나 다른 모델로 같은 작업을 수행할 수 있습니다.`;
              }
              msg._modelRemoved = false;
              addLiveLog('warning', `모델 일시 오류: ${failedModelName}`, p.error.substring(0, 200));
              continue;
            }
            // 토큰 만료
            if (p.error.includes('expired') || p.error.includes('security token')) {
              try {
                const creds = await window.electronAPI?.getCredentials(state.settings?.awsProfile || '');
                if (creds) {
                  await fetch(`${apiBase()}/api/reset-cache`, {
                    method: 'POST', headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({ profile: state.settings?.awsProfile, bedrockUser: state.settings?.bedrockUser, credentials: creds }),
                  });
                  addLiveLog('system', '자격증명 갱신 완료');
                  msg.content = '자격증명이 갱신되었습니다. 다시 질문해 주세요.';
                  continue;
                }
              } catch {}
            }
            // 기타 에러는 로그에만, UI는 친화적 안내
            addLiveLog('error', `내부 오류: ${p.error}`);
            msg.content = `오류가 발생했습니다: ${String(p.error).slice(0, 500)}`;
            continue;
          }
          else if (p.tool && p.status === 'running') {
            // 도구 실행 시작 — 채팅에 표시하지 않음
            toolCount++;
            if (wf.steps[1].status !== 'running') { wf.steps[1].status = 'running'; wf.steps[1].startedAt = Date.now(); }
            wf.steps[1].detail = `작업 실행 중... (${toolCount}번째)`;
            // 개별 도구 카드에도 startedAt 기록
            msg.toolUses.push({ name: p.tool, path: p.path || '', input: p.input || null, content: p.input ? JSON.stringify(p.input, null, 2) : '', status:'running', startedAt: Date.now(), endedAt:null });
          }
          else if (p.tool && p.status === 'done') {
            // 도구 실행 완료 — 채팅에 표시하지 않음
            wf.steps[1].detail = `작업 ${toolCount}개 완료`;
            // 가장 최근 running 도구 카드를 done으로
            for (let _i = msg.toolUses.length - 1; _i >= 0; _i--) {
              if (msg.toolUses[_i].status === 'running') {
                msg.toolUses[_i].status = 'done';
                // 서버에서 durationMs가 오면 정확한 시간 사용, 아니면 클라이언트 측정
                if (p.durationMs && msg.toolUses[_i].startedAt) {
                  msg.toolUses[_i].endedAt = msg.toolUses[_i].startedAt + p.durationMs;
                } else {
                  msg.toolUses[_i].endedAt = Date.now();
                }
                if (p.output != null) msg.toolUses[_i].output = typeof p.output === 'string' ? p.output : JSON.stringify(p.output, null, 2);
                break;
              }
            }
          }
          else if (p.verifiedFiles) {
            // 이슈 2 — 백엔드가 디스크에서 검증한 실제 생성 파일 목록.
            // 강제 생성 폴백으로 만들어진 파일도 여기로 통지된다.
            try {
              const arr = Array.isArray(p.verifiedFiles) ? p.verifiedFiles : [];
              msg._verifiedFiles = arr;
              if (arr.length) {
                // 생성 파일 패널 즉시 새로고침 (fs.watch 누락 대비)
                document.dispatchEvent(new CustomEvent('generated-folder:refresh'));
              }
            } catch (_) {}
          }
          else if (p.heartbeat) {
            // 모델이 thinking 중이거나 도구가 길게 실행 중 — idle 워치독 리셋 + 진행 표시.
            // (오래 걸려도 끊김으로 오판하지 않도록 서버가 주기적으로 보냄)
            const sec = p.elapsed || 0;
            const tt = sec >= 60 ? `${Math.floor(sec/60)}m ${sec%60}s` : `${sec}s`;
            if (p.phase === 'tool') {
              wf.steps[1].detail = `작업 실행 중... (${p.tool || ''} ${tt})`;
            } else {
              if (wf.steps[1].status !== 'running') { wf.steps[1].status = 'running'; wf.steps[1].startedAt = Date.now(); }
              wf.steps[1].detail = `생각 중... (${tt})`;
            }
          }
          else if (p.thinking) {
            // reasoning(생각 과정) — ChatGPT/Gemini처럼 노출. 본문에 누적하지 않는다.
            msg._reasoning = (msg._reasoning || '') + p.thinking;
            const tail = msg._reasoning.replace(/\s+/g, ' ').slice(-90);
            wf.steps[1].detail = `생각 중: ${tail}`;
          }
          else if (p.answerQuality) {
            // 근거 품질 메타데이터 (플래그 게이트, additive). 본문에 섞지 않고 별도 보관.
            // citation: {citations_total, verified, unverified[]}, faithfulness: {score, degraded, feedback}
            try {
              msg._answerQuality = p.answerQuality;
              const c = p.answerQuality.citation || {};
              const f = p.answerQuality.faithfulness || {};
              const parts = [];
              if (typeof c.verified === 'number' && typeof c.citations_total === 'number' && c.citations_total > 0) {
                parts.push(`인용 ${c.verified}/${c.citations_total} 검증`);
              }
              if (f && typeof f.score === 'number' && !f.degraded) {
                parts.push(`충실도 ${(f.score * 100).toFixed(0)}%`);
              }
              if (parts.length) {
                addLiveLog('system', `근거 품질: ${parts.join(', ')}`);
              }
            } catch (_) {}
          }
          else if (p.text) { msg.content += p.text; }
          else { msg.content += d; }
        } catch { msg.content += d; }
      }
      renderMessages();
    }
    if (toolCount > 0) { wf.steps[1].status = 'done'; wf.steps[1].endedAt = Date.now(); }
    else { wf.steps[1].detail = '작업 없음'; wf.steps[1].status = 'done'; wf.steps[1].startedAt = wf.steps[1].startedAt || Date.now(); wf.steps[1].endedAt = Date.now(); }
    wf.steps[2].status = 'done'; wf.steps[2].startedAt = Date.now(); wf.steps[2].endedAt = Date.now(); wf.steps[2].detail = '완료';
    // 혹시 아직 running인 도구가 있으면 종료 처리
    for (const t of msg.toolUses) { if (t.status === 'running') { t.status = 'done'; t.endedAt = Date.now(); } }
    trackUsage(prompt.length, msg.content.length);
    addLiveLog('response', `에이전트 완료: ${state.selectedModel.name}`, `${msg.content.length}자`);
    // 이슈 2 — 단일 호출에서도 생성 파일 패널을 확실히 새로고침.
    // 도구 정상 호출/강제 폴백 어느 경로든 .generated/ 변경을 반영한다.
    try {
      const panel = document.querySelector('file-preview-panel');
      if (panel) {
        const cur = panel.getAttribute('project-path') || '';
        const want = state.folderPath || window.__workstationCwd || '';
        if (want && cur !== want) {
          if (window.__workstationCwd && !panel._workstationCwd) panel._workstationCwd = window.__workstationCwd;
          panel.setAttribute('project-path', want);
        }
        if (typeof panel._refresh === 'function') await panel._refresh();
      }
      document.dispatchEvent(new CustomEvent('generated-folder:refresh'));
    } catch (_e) { /* best-effort */ }
  } catch (e) {
    clearTimeout(timeoutId);
    const errMsg = e.name === 'AbortError' ? '요청 시간 초과 또는 취소됨' : e.message;
    msg.content += `\n[오류: ${errMsg}]`;
    const r = wf.steps.find(s => s.status === 'running');
    if (r) { r.status = 'failed'; r.endedAt = Date.now(); }
    for (const t of msg.toolUses) { if (t.status === 'running') { t.status = 'failed'; t.endedAt = Date.now(); } }
    addLiveLog('error', `에이전트 실패: ${errMsg}`);
  }
  state.isStreaming = false;
  _releaseUserPin();
  renderMessages();
  saveConversation();
}

// ===== Parallel Mode — 실시간 연동: 가운데 패널 + 우측 모델 리스트 + 채팅 =====

// 실패한 슬롯만 재실행 — 성공한 결과는 보존
async function retryFailedParallel(prompt) {
  const failedSlotIds = [];
  for (const [sid, r] of state.parallelResults) {
    if (r.status === 'error' || r.status === 'pending') failedSlotIds.push(sid);
  }
  if (!failedSlotIds.length) {
    state.messages.push({ role:'system', content:'재시도할 실패 항목이 없습니다.' });
    renderMessages();
    return;
  }
  // 실패한 슬롯만 pending으로 리셋
  for (const sid of failedSlotIds) {
    const existing = state.parallelResults.get(sid);
    state.parallelResults.set(sid, { ...existing, status:'running', content:'' });
  }
  state.isStreaming = true;
  state._abortController = new AbortController();
  state.messages.push({ role:'system', content:`실패한 ${failedSlotIds.length}개만 재시도 중...` });
  renderMessages();
  renderParallelResultGrid(); renderParallelSlotList();

  // 실패한 슬롯에 해당하는 모델 정보 추출
  const failedModels = [];
  for (const slot of (state._lastExpandedSlots || state.parallelSlots)) {
    if (failedSlotIds.includes(slot.slotId)) {
      let sp = '';
      if (slot.customRole) sp = slot.customRole;
      else if (slot.skillId) { const s = allSkills.find(x => x.id === slot.skillId); if (s) sp = s.role; }
      failedModels.push({ modelId: slot.modelId, slotId: slot.slotId, systemPrompt: sp });
    }
  }

  try {
    const resp = await fetch(`${apiBase()}/api/agents/run-parallel`, {
      method:'POST', headers:{'Content-Type':'application/json'},
      body: JSON.stringify(_apiBody({ prompt, models: failedModels })),
      signal: state._abortController.signal
    });
    if (!resp.ok) throw new Error(`서버 응답 오류: ${resp.status}`);
    const reader = resp.body.getReader(), dec = new TextDecoder();
    let buf = '';
    while (true) {
      const { done, value } = await _readWithIdleTimeout(reader);
      if (done) break;
      buf += dec.decode(value, { stream:true });
      const lines = buf.split('\n'); buf = lines.pop() || '';
      for (const line of lines) {
        const trimmed = line.trim();
        if (!trimmed || !trimmed.startsWith('data: ')) continue;
        const d = trimmed.slice(6);
        if (d === '[DONE]') continue;
        try {
          const ev = JSON.parse(d);
          if (ev.heartbeat) continue;
          if (ev.slotId) {
            state.parallelResults.set(ev.slotId, { status: ev.status, content: ev.content, modelName: ev.modelId || '' });
            renderParallelResultGrid(); renderParallelSlotList();
          }
        } catch {}
      }
    }
  } catch (e) {
    const errMsg = e.name === 'AbortError' ? '사용자가 요청을 취소했습니다.' : e.message;
    state.messages.push({ role:'system', content:`재시도 오류: ${errMsg}`, _retryable: true, _retryType: 'parallel', _retryPrompt: prompt });
  }
  state.isStreaming = false;
  _releaseUserPin();
  renderParallelResultGrid(); renderParallelSlotList(); updateConsensus();
  const done = [...state.parallelResults.values()].filter(r => r.status === 'done').length;
  const err = [...state.parallelResults.values()].filter(r => r.status === 'error').length;
  if (err > 0) {
    state.messages.push({ role:'system', content:`재시도 완료: ${done}개 성공, ${err}개 여전히 실패`, _retryable: true, _retryType: 'parallel', _retryPrompt: prompt });
  } else {
    state.messages.push({ role:'system', content:`재시도 완료: 전체 ${done}개 성공` });
  }
  renderMessages();
}

async function runParallel(prompt) {
  if (!state.parallelSlots.length) return;
  state.isStreaming = true;
  state._lastParallelPrompt = prompt; // 합의 도출 후 에이전트 전환 시 원본 프롬프트 참조
  state._streamStartTime = Date.now();
  state._abortController = new AbortController();

  // 스케일 반영: 각 슬롯의 scale만큼 복제하여 실제 호출 목록 생성
  const _expandedSlots = [];
  state.parallelSlots.forEach(slot => {
    const scale = slot.scale || 1;
    for (let i = 0; i < scale; i++) {
      _expandedSlots.push({
        ...slot,
        slotId: scale > 1 ? `${slot.slotId}-${i}` : slot.slotId,
        _originalSlotId: slot.slotId,
        _scaleIdx: i,
      });
    }
  });

  addLiveLog('request', `병렬 호출: ${_expandedSlots.length}개 모델`);

  state.parallelResults.clear();
  _expandedSlots.forEach(slot => state.parallelResults.set(slot.slotId, { status:'pending', content:'', modelName: slot.model.name + (slot._scaleIdx > 0 ? ` #${slot._scaleIdx+1}` : '') }));
  // 재시도 시 참조할 수 있도록 저장
  state._lastExpandedSlots = _expandedSlots;

  showParallelResults();
  const grid = document.getElementById('parallel-grid');
  if (grid) grid.innerHTML = '';
  renderParallelResultGrid();
  renderParallelSlotList();
  const totalCalls = _expandedSlots.length;
  state.messages.push({ role:'system', content:`${totalCalls}개 병렬 실행 시작...` });
  // 공통 thinking placeholder — assistant 빈 메시지로 진행 중 표시
  state.messages.push({ role:'assistant', content:'', _thinking: true, _thinkingLabel: `${totalCalls}개 모델 호출 중` });
  renderMessages();

  // 병렬 진행 중 thinking 카운터 자동 갱신 (서버 이벤트 없어도 초가 멈추지 않음)
  const _parTickTimer = setInterval(() => {
    if (!state.isStreaming) { clearInterval(_parTickTimer); return; }
    const ind = document.querySelector('.thinking-indicator');
    if (!ind) return;
    const elapsed = Math.floor((Date.now() - (state._streamStartTime || Date.now())) / 1000);
    const timeText = elapsed >= 3600 ? `${Math.floor(elapsed/3600)}h ${Math.floor((elapsed%3600)/60)}m` : elapsed >= 60 ? `${Math.floor(elapsed/60)}m ${elapsed%60}s` : `${elapsed}s`;
    const thinkMsg = state.messages.find(m => m._thinking);
    const label = (thinkMsg && thinkMsg._thinkingLabel) || `${totalCalls}개 모델 호출 중`;
    ind.innerHTML = `<span class="thinking-dots"><span></span><span></span><span></span></span> ${esc(label)} ${timeText}`;
  }, 1000);

  // 서버 측 병렬 호출 — 단일 SSE 연결로 모든 모델 결과 수신
  const models = _expandedSlots.map(slot => {
    let sp = '';
    if (slot.customRole) sp = slot.customRole;
    else if (slot.skillId) { const s = allSkills.find(x => x.id === slot.skillId); if (s) sp = s.role; }
    return { modelId: slot.modelId, slotId: slot.slotId, systemPrompt: sp };
  });

  // 모든 슬롯을 running으로 + 시작 시간 기록
  const _slotStartTimes = {};
  _expandedSlots.forEach(slot => {
    state.parallelResults.set(slot.slotId, { status:'running', content:'', modelName: slot.model.name + (slot._scaleIdx > 0 ? ` #${slot._scaleIdx+1}` : '') });
    _slotStartTimes[slot.slotId] = Date.now();
  });
  renderParallelResultGrid(); renderParallelSlotList();

  try {
    const resp = await fetch(`${apiBase()}/api/agents/run-parallel`, {
      method:'POST', headers:{'Content-Type':'application/json'},
      body: JSON.stringify(_apiBody({ prompt, models })),
      signal: state._abortController?.signal
    });
    if (!resp.ok) throw new Error(`서버 응답 오류: ${resp.status}`);
    const reader = resp.body.getReader(), dec = new TextDecoder();
    let buf = '';
    while (true) {
      const { done, value } = await _readWithIdleTimeout(reader);
      if (done) break;
      buf += dec.decode(value, { stream:true });
      const events = buf.split('\n\n'); buf = events.pop() || '';
      for (const event of events) {
        const trimmed = event.trim();
        if (!trimmed || !trimmed.startsWith('data: ')) continue;
        const d = trimmed.slice(6);
        if (d === '[DONE]') continue;
        try {
          const ev = JSON.parse(d);
          // heartbeat 이벤트는 무시 (연결 유지 목적)
          if (ev.heartbeat) continue;
          if (ev.slotId) {
            const slot = state.parallelSlots.find(s => s.slotId === ev.slotId);
            const modelName = slot?.model?.name || ev.modelId || '';
            const slotElapsed = _slotStartTimes[ev.slotId] ? Math.floor((Date.now() - _slotStartTimes[ev.slotId]) / 1000) : 0;
            state.parallelResults.set(ev.slotId, { status: ev.status, content: ev.content, modelName, elapsed: slotElapsed });
            renderParallelResultGrid();
            renderParallelSlotList();
            updateConsensus();
            // 진행 라벨 동적 업데이트 (X/Y 완료, Z 실행)
            const thinkMsg = state.messages.find(m => m._thinking);
            if (thinkMsg) {
              const allResults = [...state.parallelResults.values()];
              const doneN = allResults.filter(r => r.status === 'done').length;
              const errN = allResults.filter(r => r.status === 'error').length;
              const runN = allResults.filter(r => r.status === 'running').length;
              thinkMsg._thinkingLabel = `${totalCalls}개 모델 호출 중 (${doneN}/${totalCalls} 완료${errN > 0 ? `, ${errN} 실패` : ''}, ${runN} 실행)`;
            }
          }
        } catch {}
      }
    }
  } catch (e) {
    // (타임아웃 없음 — 사용자 수동 취소만)
    const errMsg = e.name === 'AbortError' ? '사용자가 요청을 취소했습니다.' : e.message;
    // [Fix #3] 모든 running 슬롯을 error로 변경하되, 이미 부분 수신한 content는 보존
    for (const [sid, r] of state.parallelResults) {
      if (r.status === 'running' || r.status === 'pending') {
        const preserved = r.content || '';
        state.parallelResults.set(sid, {
          ...r,
          status: 'error',
          content: preserved ? `${preserved}\n\n---\n중단됨: ${errMsg}` : `${errMsg}`
        });
      }
    }
    // thinking placeholder 제거
    state.messages = state.messages.filter(m => !m._thinking);
    // 재시도 가능한 system 메시지로 표시 (renderMessages에서 버튼 렌더)
    state.messages.push({
      role: 'system',
      content: `병렬 실행 오류: ${errMsg}`,
      _retryable: true,
      _retryType: 'parallel',
      _retryPrompt: prompt
    });
    addLiveLog('error', `병렬 호출 실패: ${errMsg}`);
  }

  state.isStreaming = false;
  _releaseUserPin();
  renderParallelResultGrid(); renderParallelSlotList(); updateConsensus();

  const done = [...state.parallelResults.values()].filter(r => r.status === 'done').length;
  const err = [...state.parallelResults.values()].filter(r => r.status === 'error').length;
  const parallelElapsed = Math.floor((Date.now() - (state._streamStartTime || Date.now())) / 1000);
  // thinking placeholder 제거
  state.messages = state.messages.filter(m => !m._thinking);
  state.messages.push({ role:'system', content:`병렬 완료: ${done}개 성공, ${err}개 실패 (${fmtElapsed(parallelElapsed)}) — 가운데 패널에서 결과 확인` });

  // === 병렬 결과를 assistant 메시지로 sessionMessages에 저장 (다음 턴 맥락 유지) ===
  // 여러 모델 답변을 하나의 assistant 메시지로 합쳐서 push (user/assistant 교대 규칙 준수)
  try {
    const successResults = [];
    for (const slot of state.parallelSlots) {
      const r = state.parallelResults.get(slot.slotId);
      if (r && r.status === 'done' && r.content) {
        successResults.push({ modelName: r.modelName || slot.model?.name || slot.modelId, content: r.content });
      }
    }
    if (successResults.length > 0) {
      // [Fix #1] 합본은 각 모델 600자씩만 저장 → 다음 턴 토큰 폭발 방지
      //  (원본은 state.parallelResults에 그대로 보존됨 — 가운데 패널용)
      const combined = successResults.map((x, i) => {
        const snippet = (x.content || '').substring(0, 600);
        const ellipsis = (x.content || '').length > 600 ? '\n…[축약]' : '';
        return `### [모델 ${i+1}] ${x.modelName}\n\n${snippet}${ellipsis}`;
      }).join('\n\n---\n\n');
      state.messages.push({
        role: 'assistant',
        content: combined,
        isParallel: true,
        parallelCount: successResults.length,
        hiddenInChat: true
      });
    }
  } catch (e) { console.error('[parallel] sessionMessages 저장 실패:', e); }

  // === 병렬 완료 후 추천 카드 — 결과를 컨텍스트로 활용한 다음 단계 제안 ===
  _showPostParallelRecommendation(prompt);

  saveParallelResults();
  renderMessages();
}

// 병렬 완료 후 추천 카드 — 항상 다음 단계 제시 (의도별 맞춤 액션)
function _showPostParallelRecommendation(originalPrompt) {
  if (!originalPrompt) return;
  const doneResults = [...state.parallelResults.values()].filter(r => r.status === 'done');
  if (doneResults.length < 1) return;

  // 할루시네이션 감지
  const _hallucinationPattern = /(?:생성|저장|작성|만들었|created|saved|completed).*(완료|되었|됨|finished|done|✅)|<function_calls>|<invoke|<tool_call|\.generated\/[\w\-]+\.(pdf|xlsx|pptx|docx|png)/i;
  const hallucinatingCount = doneResults.filter(r => _hallucinationPattern.test(r.content || '')).length;
  const hasHallucination = hallucinatingCount > 0;

  // 의도 분류
  const _filePattern = /(?:pdf|xlsx|엑셀|pptx|파워포인트|docx|워드|hwp|이미지|image|png|jpg|svg|문서|보고서|발표|슬라이드|차트|그래프|다이어그램|도표|표).*(생성|만들|작성|제작|그려|구현)|(?:생성|만들|작성|제작|그려|구현).*(?:pdf|xlsx|엑셀|pptx|파워포인트|docx|워드|hwp|이미지|image|png|jpg|svg|문서|보고서|발표|슬라이드|차트|그래프|다이어그램|도표|표)|파일.*(?:3|4|5|여러).*(?:종|개|장)/i;
  const _codePattern = /(?:코드|함수|클래스|모듈|컴포넌트|api).*(작성|구현|만들|수정|리팩토링|디버깅)|구현해|작성해|코딩/i;
  const isFileTask = _filePattern.test(originalPrompt);
  const isCodeTask = _codePattern.test(originalPrompt);

  // 의도별 액션 구성
  let title, reason, actions = [];
  if (isFileTask) {
    title = hasHallucination ? '주의 — 실제 파일이 생성되지 않았습니다' : '병렬 결과 후속 액션 — 실제 파일 만들기';
    reason = hasHallucination
      ? `${hallucinatingCount}개 모델이 파일 생성 완료를 주장했지만, 병렬 모드에서는 도구가 제공되지 않아 실제 파일은 만들어지지 않았습니다. 오케스트레이터로 실제 파일을 생성하세요.`
      : `병렬 모드는 텍스트 응답만 가능합니다. ${doneResults.length}개 모델 응답을 종합해 오케스트레이터가 실제 파일(PDF/XLSX/PPTX/DOCX/이미지)을 생성합니다.`;
    actions.push({ key: 'orchestrate', label: '오케스트레이터로 파일 생성', primary: true });
    if (doneResults.length >= 2) actions.push({ key: 'consensus', label: '합의 도출' });
  } else if (isCodeTask) {
    title = '병렬 결과 후속 액션';
    reason = `${doneResults.length}개 모델의 코드 제안을 종합해 실제 파일에 적용하거나 합의를 도출할 수 있습니다.`;
    actions.push({ key: 'orchestrate', label: '오케스트레이터로 코드 적용', primary: true });
    if (doneResults.length >= 2) actions.push({ key: 'consensus', label: '합의 도출' });
  } else {
    title = '병렬 결과 후속 액션';
    reason = `${doneResults.length}개 모델의 응답을 비교하거나 후속 작업을 이어갈 수 있습니다.`;
    if (doneResults.length >= 2) actions.push({ key: 'consensus', label: '합의 도출', primary: true });
    actions.push({ key: 'orchestrate', label: '오케스트레이터로 작업' });
  }

  // 추천 카드를 system 메시지로 등록 → renderMessages가 안정적으로 표시
  state.messages.push({
    role: 'system',
    _isRecommendCard: true,
    _recommendData: {
      title, reason, actions, hasHallucination, originalPrompt,
      doneResults: doneResults.map(r => ({ modelName: r.modelName, content: r.content })),
    },
    content: '[추천] ' + title,
  });
  renderMessages();
  // 스크롤 보장
  setTimeout(() => {
    const cc = document.getElementById('chat-messages');
    if (cc) cc.scrollTop = cc.scrollHeight;
  }, 100);
}

// 추천 카드 렌더링 (renderMessages에서 호출)
function _renderRecommendCardMessage(msg) {
  const data = msg._recommendData;
  if (!data) return null;
  const card = document.createElement('div');
  card.className = 'model-recommend-card' + (data.hasHallucination ? ' recommend-warning' : '');
  card.style.margin = '8px 12px';
  card.innerHTML = `
    <div class="recommend-header">
      <span class="recommend-title">${esc(data.title)}</span>
      <span class="recommend-dismiss" title="무시">✕</span>
    </div>
    <div class="recommend-reason">${esc(data.reason)}</div>
    <div class="recommend-actions">
      ${data.actions.map(a => `<button class="recommend-btn ${a.primary ? 'accept' : ''}" data-action="${a.key}">${esc(a.label)}</button>`).join('')}
      <button class="recommend-btn dismiss">닫기</button>
    </div>
  `;

  const cleanup = () => { card.classList.add('recommend-fade-out'); setTimeout(() => card.remove(), 300); };
  card.querySelector('.recommend-dismiss')?.addEventListener('click', cleanup);
  card.querySelector('.recommend-btn.dismiss')?.addEventListener('click', cleanup);

  card.querySelector('[data-action="orchestrate"]')?.addEventListener('click', () => {
    cleanup();
    const contextSummary = data.doneResults.map((r, i) =>
      `### [참고 답변 ${i+1}] ${r.modelName}\n${(r.content || '').substring(0, 1500)}`
    ).join('\n\n---\n\n');
    const enrichedPrompt = `${data.originalPrompt}\n\n--- 이전 ${data.doneResults.length}개 모델의 답변 (참고용) ---\n${contextSummary}\n\n위 답변들을 참고하여 도구(write_file, generate_image 등)를 사용해 실제 파일을 생성해주세요.`;
    runOrchestrated(enrichedPrompt);
  });

  card.querySelector('[data-action="consensus"]')?.addEventListener('click', () => {
    cleanup();
    if (typeof runConsensus === 'function') runConsensus();
  });

  // 더 강한 모델로 오케스트레이터 재시도
  card.querySelector('[data-action="retry-stronger"]')?.addEventListener('click', () => {
    cleanup();
    const enrichedPrompt = `${data.originalPrompt}\n\n[중요] 이전 시도에서 일부 에이전트가 도구를 사용하지 않아 실제 파일이 생성되지 않았습니다. 이번에는 반드시 generate_pdf, generate_image, generate_pptx, write_file 등의 도구를 적극 사용해서 실제 파일을 .generated/ 폴더에 만들어주세요.`;
    runOrchestrated(enrichedPrompt);
  });

  // .generated 폴더 확인 — 파일 탐색기에서 해당 경로로 이동
  card.querySelector('[data-action="view-generated"]')?.addEventListener('click', () => {
    cleanup();
    const folderPath = state.folderPath ? `${state.folderPath}/.generated` : '.generated';
    addLiveLog('system', `생성 파일 폴더 확인: ${folderPath}`);
    // 파일 탐색기 새로고침 트리거
    if (typeof loadFileTree === 'function' && state.folderPath) {
      loadFileTree(state.folderPath);
    }
    // 사이드바의 .generated 폴더 패널이 있으면 새로고침
    document.dispatchEvent(new CustomEvent('generated-folder:refresh'));
  });

  // 추가 요청 — 입력창에 포커스
  card.querySelector('[data-action="refine"]')?.addEventListener('click', () => {
    cleanup();
    const input = document.getElementById('chat-input');
    if (input) {
      input.placeholder = '이어서 무엇을 도와드릴까요?';
      input.focus();
    }
  });

  // 오케스트레이션 재시도 — 동일 프롬프트로 재실행
  card.querySelector('[data-action="retry-orchestrate"]')?.addEventListener('click', () => {
    cleanup();
    runOrchestrated(data.originalPrompt);
  });

  // 미완료 작업만 이어서 실행 — 체크포인트 활용
  card.querySelector('[data-action="resume-orchestrate"]')?.addEventListener('click', () => {
    cleanup();
    const incomplete = (data.incompleteIds && data.incompleteIds.length) ? data.incompleteIds : [];
    const hint = incomplete.length
      ? `\n\n[이어서 생성] 이전 시도에서 완료된 에이전트 ${(state._lastOrchestrationCheckpoint?.doneAgents || 0)}개의 결과는 이미 .generated/에 저장되어 있습니다. 미완료 ${incomplete.length}개 작업(${incomplete.join(', ')})만 다시 실행해주세요. 동일한 파일을 재생성하지 마세요.`
      : '\n\n[이어서 생성] 이전 시도에서 일부 완료됨. 미완료 작업만 진행해주세요.';
    runOrchestrated(data.originalPrompt + hint);
  });

  // 단일 모델로 전환 — 일반 채팅 흐름으로 fallback
  card.querySelector('[data-action="switch-single"]')?.addEventListener('click', () => {
    cleanup();
    if (typeof runSingle === 'function') {
      runSingle(data.originalPrompt);
    } else {
      addLiveLog('error', '단일 모델 흐름을 찾을 수 없습니다.');
    }
  });

  return card;
}

let _consensusModelId = null;

function updateConsensus() {
  const done = [...state.parallelResults.values()].filter(r => r.status === 'done').length;
  const btn = document.getElementById('consensus-btn');
  const label = document.getElementById('parallel-count-label');
  if (btn) {
    btn.disabled = done < 2;
    btn.textContent = done >= 2 ? `합의 도출 (${done}개)` : '합의 (완료 2개 이상 필요)';
    btn.onclick = done >= 2 ? runConsensus : null;
  }
  // 합의 모델 드롭다운 초기화
  if (!_consensusModelId && ALL_MODELS.length > 0) {
    _consensusModelId = pickConsensusModel();
    const m = ALL_MODELS.find(x => x.id === _consensusModelId);
    const cbtn = document.getElementById('consensus-dropdown-btn');
    if (cbtn && m) cbtn.textContent = m.name + ' ▾';
    initConsensusDropdown();
    // 스킬 드롭다운 업데이트
    const skillSel = document.getElementById('consensus-skill-select');
    if (skillSel && skillSel.options.length <= 1) {
      skillSel.innerHTML = '<option value="">스킬 없음</option>' + allSkills.map(s => `<option value="${s.id}">${s.name}</option>`).join('');
      skillSel.addEventListener('change', () => {
        const ta = document.getElementById('consensus-custom-role');
        if (ta) ta.style.display = skillSel.value ? 'none' : '';
      });
    }
  }
  if (label) {
    const total = state.parallelSlots.length;
    const running = [...state.parallelResults.values()].filter(r => r.status === 'running').length;
    const err = [...state.parallelResults.values()].filter(r => r.status === 'error').length;
    label.textContent = `${total}개 선택 · ${done} 완료 · ${running} 실행 중 · ${err} 실패`;
  }
}

function initConsensusDropdown() {
  const cbtn = document.getElementById('consensus-dropdown-btn');
  const cmenu = document.getElementById('consensus-dropdown-menu');
  const csearch = document.getElementById('consensus-model-search');
  if (!cbtn || !cmenu) return;
  cbtn.onclick = () => {
    const v = cmenu.style.display !== 'none';
    cmenu.style.display = v ? 'none' : 'flex';
    if (!v) { csearch.value = ''; renderConsensusDropdownList(''); csearch.focus(); }
  };
  csearch.oninput = () => renderConsensusDropdownList(csearch.value);
  document.addEventListener('click', e => {
    if (!e.target.closest('#consensus-dropdown-wrapper')) cmenu.style.display = 'none';
  });
}

function renderConsensusDropdownList(filter) {
  const list = document.getElementById('consensus-dropdown-list');
  if (!list) return;
  list.innerHTML = '';
  const q = filter.toLowerCase();
  for (const [p, ms] of Object.entries(MODEL_CATALOG)) {
    const fl = ms.filter(m => !q || m.name.toLowerCase().includes(q) || p.toLowerCase().includes(q));
    if (!fl.length) continue;
    const g = document.createElement('div'); g.className = 'model-dropdown-group';
    g.innerHTML = `<div class="model-dropdown-group-title"><span style="color:var(--color-accent);font-weight:700">${p}</span></div>`;
    fl.forEach(m => {
      const i = document.createElement('div');
      i.className = 'model-dropdown-item' + (m.id === _consensusModelId ? ' selected' : '');
      i.textContent = m.name;
      i.onclick = () => {
        _consensusModelId = m.id;
        document.getElementById('consensus-dropdown-btn').textContent = m.name + ' ▾';
        document.getElementById('consensus-dropdown-menu').style.display = 'none';
      };
      g.appendChild(i);
    });
    list.appendChild(g);
  }
}

// 합의 모델 우선순위 (고차원 → 저차원)
const CONSENSUS_MODEL_PRIORITY = [
  'anthropic.claude-opus-4-7',
  'anthropic.claude-opus-4-6-v1',
  'anthropic.claude-opus-4-5-20251101-v1:0',
  'anthropic.claude-opus-4-1-20250805-v1:0',
  'anthropic.claude-sonnet-4-6',
  'anthropic.claude-sonnet-4-5-20250929-v1:0',
  'anthropic.claude-haiku-4-5-20251001-v1:0',
  'deepseek.r1-v1:0',
  'deepseek.v3.2',
  'qwen.qwen3-235b-a22b-2507-v1:0',
  'mistral.mistral-large-3-675b-instruct',
];

function pickConsensusModel() {
  // 1. 우선순위 목록에서 사용 가능한 모델 찾기
  for (const mid of CONSENSUS_MODEL_PRIORITY) {
    if (ALL_MODELS.find(m => m.id === mid)) return mid;
  }
  // 2. 없으면 Anthropic 모델 중 가장 큰 것
  const anthropic = ALL_MODELS.filter(m => m.id.startsWith('anthropic.'));
  if (anthropic.length) return anthropic[0].id;
  // 3. 그것도 없으면 첫 번째 모델
  return ALL_MODELS.length ? ALL_MODELS[0].id : null;
}

// 합의 이력 저장
let _consensusHistory = [];

// ===== [합의 모델 이어가기] 액션 핸들러 =====
// 사용자가 합의 카드의 "이 모델로 계속 대화" 클릭 시 호출
function continueWithConsensusModel(modelId, modelName) {
  const model = ALL_MODELS.find(m => m.id === modelId);
  if (!model) {
    state.messages.push({ role:'system', content:`모델을 찾을 수 없음: ${modelName}` });
    renderMessages();
    return;
  }
  // 1) 모드를 단일로 전환
  state.mode = 'single';
  state.selectedModel = model;
  state.lastConsensusModel = { id: modelId, name: modelName };

  // 2) UI 동기화 — 모드 토글 버튼 active 변경
  document.querySelectorAll('.mode-toggle-btn').forEach(b => b.classList.toggle('active', b.dataset.mode === 'single'));
  const sBar = document.getElementById('single-model-bar');
  const pBar = document.getElementById('parallel-model-bar');
  const pList = document.getElementById('parallel-selected-list');
  if (sBar) sBar.style.display = 'block';
  if (pBar) pBar.style.display = 'none';
  if (pList) pList.style.display = 'none';
  try { hideParallelResults(); } catch(_) {}

  // 3) 단일 모델 드롭다운 버튼 텍스트 갱신
  const mBtn = document.getElementById('model-dropdown-btn');
  if (mBtn) mBtn.textContent = (model.name || modelName) + ' ▾';

  // 4) 시스템 메시지로 전환 사실 알림
  state.messages.push({
    role:'system',
    content:`합의 모델로 전환: ${modelName} — 다음 메시지부터 이 모델만 호출됩니다 (모드 전환 버튼으로 병렬로 되돌릴 수 있음)`
  });
  renderMessages();
  saveConversation();
}

// 사용자가 "병렬 모드 유지" 클릭 시 호출 (명시적 확인 용도)
function keepParallelMode() {
  // 이미 병렬 모드이므로 상태는 그대로, 간단한 안내 메시지만 추가
  state.messages.push({
    role:'system',
    content:'병렬 모드 유지 — 다음 메시지는 선택된 모델들에 병렬로 호출됩니다'
  });
  renderMessages();
}

// window에 노출 (HTML onclick에서도 호출 가능하게)
window.continueWithConsensusModel = continueWithConsensusModel;
window.keepParallelMode = keepParallelMode;

async function runConsensus() {
  const dr = [...state.parallelResults.entries()]
    .filter(([_, r]) => r.status === 'done')
    .map(([_, r]) => ({ model: r.modelName, content: r.content }));

  if (!dr.length) return;

  // 합의 프롬프트 — 각 모델 응답을 포함
  const sp = `당신은 여러 AI 모델의 응답을 분석하여 최종 합의를 도출하는 전문가입니다.

다음 ${dr.length}개 모델의 응답을 분석하고:
1. 각 모델 응답의 핵심 내용을 요약
2. 공통점과 차이점을 분석
3. 가장 정확하고 완전한 최종 합의 결과를 도출

${dr.map((r, i) => `### 모델 ${i + 1}: ${r.model}\n${r.content.substring(0, 3000)}`).join('\n\n---\n\n')}

위 응답들을 종합하여 최종 합의 결과를 작성하세요.`;

  // 사용자가 선택한 합의 모델 또는 자동 선택
  const consensusModelId = _consensusModelId || pickConsensusModel();
  const consensusModelName = ALL_MODELS.find(m => m.id === consensusModelId)?.name || consensusModelId;

  // 스킬/커스텀 role
  let consensusSystemPrompt = '';
  const skillSel = document.getElementById('consensus-skill-select');
  const customRole = document.getElementById('consensus-custom-role');
  if (skillSel?.value) {
    const sk = allSkills.find(s => s.id === skillSel.value);
    if (sk) consensusSystemPrompt = sk.role;
  } else if (customRole?.value?.trim()) {
    consensusSystemPrompt = customRole.value.trim();
  }

  if (!consensusModelId) {
    state.messages.push({ role:'system', content:'합의 도출 실패: 사용 가능한 모델이 없습니다.' });
    renderMessages();
    return;
  }

  state.messages.push({ role:'system', content:`합의 도출 중... (${dr.length}개 모델 응답 분석, 합의 모델: ${consensusModelName})` });
  renderMessages();

  state.isStreaming = true;
  state._streamStartTime = Date.now();
  // [합의 모델 이어가기] 메시지에 모델 정보 포함 → 합의 카드 하단에 버튼 렌더 시 사용
  const msg = { role:'assistant', content:'', isConsensus: true, consensusModelId, consensusModelName };
  state.messages.push(msg);

  try {
    const resp = await fetch(`${apiBase()}/api/agents/run-stream`, {
      method:'POST', headers:{'Content-Type':'application/json'},
      body: JSON.stringify(_apiBody({ prompt: sp, model: consensusModelId, systemPrompt: consensusSystemPrompt })),
    });
    if (!resp.ok) throw new Error(`서버 응답 오류: ${resp.status}`);
    const reader = resp.body.getReader(), dec = new TextDecoder();
    let buf = '';
    while (true) {
      const { done, value } = await _readWithIdleTimeout(reader);
      if (done) break;
      buf += dec.decode(value, { stream:true });
      const events = buf.split('\n\n'); buf = events.pop() || '';
      for (const event of events) {
        const trimmed = event.trim();
        if (!trimmed || !trimmed.startsWith('data: ')) continue;
        const d = trimmed.slice(6);
        if (d === '[DONE]') continue;
        try {
          const parsed = JSON.parse(d);
          if (parsed.error) { msg.content += `\n[오류: ${parsed.error}]`; continue; }
          if (parsed.text) { msg.content += parsed.text; continue; }
        } catch {}
        msg.content += d;
      }
      renderMessages();
    }
    addLiveLog('response', `합의 완료: ${consensusModelName}`, `${msg.content.length}자`);
    // [Fix #3] 스트림은 끝났는데 응답이 비어있으면 (idle timeout 등) 경고 + 재시도 마킹
    if (!msg.content || msg.content.trim().length < 10) {
      msg.content = (msg.content || '') + `\n합의 모델 응답이 비어있습니다 (모델: ${consensusModelName}). 네트워크 또는 토큰 만료 가능성이 있습니다.`;
      msg._retryable = true;
      msg._retryType = 'consensus';
      addLiveLog('error', `합의 응답 비어있음 — 재시도 권장`);
    }
  } catch (e) {
    // [Fix #3] 합의 실패 시 부분 결과 보존 + 재시도 메타 추가
    const errMsg = e.message || String(e);
    if (!msg.content || msg.content.trim().length === 0) {
      msg.content = `합의 도출 실패: ${errMsg}`;
    } else {
      msg.content += `\n\n---\n합의 도중 중단됨: ${errMsg} (위는 부분 결과)`;
    }
    msg._retryable = true;
    msg._retryType = 'consensus';
    msg._retryError = errMsg;
    addLiveLog('error', `합의 실패: ${errMsg}`);
  }
  msg._elapsed = Math.floor((Date.now() - (state._streamStartTime || Date.now())) / 1000);
  state.isStreaming = false;
  _releaseUserPin();

  // 합의 이력 저장
  const now = new Date();
  const kst = new Date(now.getTime() + 9 * 60 * 60 * 1000);
  const kstStr = `${kst.getUTCFullYear()}-${String(kst.getUTCMonth()+1).padStart(2,'0')}-${String(kst.getUTCDate()).padStart(2,'0')} ${String(kst.getUTCHours()).padStart(2,'0')}:${String(kst.getUTCMinutes()).padStart(2,'0')}:${String(kst.getUTCSeconds()).padStart(2,'0')} KST`;
  _consensusHistory.push({
    time: kstStr,
    model: consensusModelName,
    modelCount: dr.length,
    models: dr.map(r => r.model),
    content: msg.content,
  });

  // 센터 패널에 합의 결과 탭 표시
  const consensusTab = document.getElementById('cv-tab-consensus');
  if (consensusTab) consensusTab.style.display = '';
  saveConsensusResults();
  renderConsensusView();

  // 합의 결과를 이용한 다음 단계 추천 (파일 생성 작업이면 에이전트로 실제 파일 생성 제안)
  _showPostConsensusRecommendation(msg.content || '', state._lastParallelPrompt || '');

  renderMessages();
  saveConversation();
}

// 합의 도출 후 추천 — 합의된 텍스트를 컨텍스트로 다음 단계 제시 (항상 노출)
function _showPostConsensusRecommendation(consensusText, originalPrompt) {
  if (!consensusText || !originalPrompt) return;

  // 작업 의도 분류 (간단한 패턴 매칭)
  const _filePattern = /(?:pdf|xlsx|엑셀|pptx|파워포인트|docx|워드|hwp|이미지|image|png|jpg|svg|문서|보고서|발표|슬라이드|차트|그래프|다이어그램|도표|표).*(생성|만들|작성|제작|그려|구현)|(?:생성|만들|작성|제작|그려|구현).*(?:pdf|xlsx|엑셀|pptx|파워포인트|docx|워드|hwp|이미지|image|png|jpg|svg|문서|보고서|발표|슬라이드|차트|그래프|다이어그램|도표|표)/i;
  const _codePattern = /(?:코드|함수|클래스|파일|모듈|컴포넌트|api|함수).*(작성|구현|만들|수정|리팩토링|디버깅)|구현해|작성해|코딩/i;
  const _analysisPattern = /(?:분석|리뷰|검토|평가|비교|요약|설명)/i;

  const isFileTask = _filePattern.test(originalPrompt);
  const isCodeTask = _codePattern.test(originalPrompt);
  const isAnalysisTask = _analysisPattern.test(originalPrompt);

  const container = document.getElementById('chat-messages');
  if (!container) return;

  // 의도별 액션 구성
  let title, reason, actions = [];
  if (isFileTask) {
    title = '합의 결과 후속 액션 — 파일 만들기';
    reason = '합의된 내용을 바탕으로 오케스트레이터가 실제 파일을 생성합니다 (PDF/XLSX/DOCX/이미지 등).';
    actions.push({ key: 'orchestrate', label: '오케스트레이터로 파일 생성', primary: true });
    actions.push({ key: 'refine', label: '합의 내용 다듬기' });
  } else if (isCodeTask) {
    title = '합의 결과 후속 액션';
    reason = '합의된 코드/구조를 바탕으로 실제 파일에 적용하거나 추가 작업을 진행합니다.';
    actions.push({ key: 'orchestrate', label: '오케스트레이터로 코드 적용', primary: true });
    actions.push({ key: 'refine', label: '합의 내용 개선' });
  } else if (isAnalysisTask) {
    title = '합의 결과 후속 액션 — 심화';
    reason = '합의된 분석 결과를 바탕으로 후속 작업(보고서 작성, 코드 수정 등)을 이어갈 수 있습니다.';
    actions.push({ key: 'refine', label: '추가 질문/심화 분석', primary: true });
    actions.push({ key: 'orchestrate', label: '결과를 파일로 저장' });
  } else {
    title = '합의 결과 후속 액션';
    reason = '합의된 답변을 활용해 후속 작업을 진행할 수 있습니다.';
    actions.push({ key: 'refine', label: '이어서 질문하기', primary: true });
    actions.push({ key: 'orchestrate', label: '오케스트레이터로 작업' });
  }

  const card = document.createElement('div');
  card.className = 'model-recommend-card';
  card.innerHTML = `
    <div class="recommend-header">
      <span class="recommend-title">${esc(title)}</span>
      <span class="recommend-dismiss" title="무시">✕</span>
    </div>
    <div class="recommend-reason">${esc(reason)}</div>
    <div class="recommend-actions">
      ${actions.map(a => `<button class="recommend-btn ${a.primary ? 'accept' : ''}" data-action="${a.key}">${esc(a.label)}</button>`).join('')}
      <button class="recommend-btn dismiss">닫기</button>
    </div>
  `;
  container.appendChild(card);
  container.scrollTop = container.scrollHeight;

  const cleanup = () => { card.classList.add('recommend-fade-out'); setTimeout(() => card.remove(), 300); };
  card.querySelector('.recommend-dismiss')?.addEventListener('click', cleanup);
  card.querySelector('.recommend-btn.dismiss')?.addEventListener('click', cleanup);

  // 에이전트로 작업 진행 (파일 생성, 코드 적용 등)
  card.querySelector('[data-action="orchestrate"]')?.addEventListener('click', () => {
    cleanup();
    const enrichedPrompt = `${originalPrompt}\n\n--- 합의된 답변 (모든 모델이 동의한 내용) ---\n${consensusText.substring(0, 4000)}\n\n위 합의 내용을 활용하여 실제 작업을 수행해주세요.`;
    runOrchestrated(enrichedPrompt);
  });

  // 합의 내용 다듬기 / 이어서 질문 — 채팅 입력창에 컨텍스트 힌트 주입
  card.querySelector('[data-action="refine"]')?.addEventListener('click', () => {
    cleanup();
    const input = document.getElementById('chat-input');
    if (input) {
      input.value = '';
      input.placeholder = '합의 결과를 바탕으로 추가 질문하세요...';
      input.focus();
    }
  });
}

function renderConsensusView() {
  const container = document.getElementById('view-consensus');
  if (!container || !_consensusHistory.length) return;
  // 최신순 정렬 (역순)
  const sorted = [..._consensusHistory].reverse();
  container.innerHTML = sorted.map((h, ri) => {
    const i = _consensusHistory.length - 1 - ri; // 원본 인덱스
    return `
    <div style="margin-bottom:16px;background:var(--color-bg-tertiary);border:1px solid var(--color-border);border-radius:var(--radius-lg);overflow:hidden">
      <div style="padding:10px 14px;border-bottom:1px solid var(--color-border);display:flex;align-items:center;gap:8px;cursor:pointer" data-toggle-consensus="${i}">
        <span style="font-size:12px;font-weight:700;color:var(--color-success)">합의 #${i + 1}</span>
        <span style="font-size:11px;color:var(--color-text-muted)">${esc(h.time)}</span>
        <span style="font-size:11px;color:var(--color-text-muted)">모델: ${esc(h.model)}</span>
        <span style="font-size:11px;color:var(--color-text-muted)">${h.modelCount}개 응답</span>
        <span style="flex:1"></span>
        <span style="font-size:10px;color:var(--color-text-muted);max-width:200px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap">${h.models.map(m => esc(m)).join(', ')}</span>
      </div>
      <div class="consensus-body" id="consensus-body-${i}" style="padding:14px;font-size:13px;color:var(--color-text-primary);line-height:1.7;overflow-y:auto">${fmtMd(h.content)}</div>
    </div>`;
  }).join('');
  container.querySelectorAll('[data-toggle-consensus]').forEach(el => {
    el.addEventListener('click', () => {
      const body = document.getElementById('consensus-body-' + el.dataset.toggleConsensus);
      if (body) body.style.display = body.style.display === 'none' ? '' : 'none';
    });
  });
}

function showParallelResults() {
  document.getElementById('parallel-results').classList.add('visible');
  document.getElementById('editor-area').style.display = 'none';
  // 병렬 결과 탭 표시
  const tab = document.getElementById('cv-tab-parallel');
  if (tab) tab.style.display = '';
  // 탭 활성화
  document.querySelectorAll('.cv-tab').forEach(t => t.classList.toggle('active', t.dataset.view === 'parallel'));
  if (typeof _activeView !== 'undefined') _activeView = 'parallel';
}
function hideParallelResults() {
  document.getElementById('parallel-results').classList.remove('visible');
  document.getElementById('editor-area').style.display = '';
}

// 병렬 카드 확대 모달 — FLIP 애니메이션 (원본 위치 ↔ 화면 중앙)
function _showParallelCardModal(r, originCard) {
  // 기존 모달 제거
  document.querySelector('.parallel-card-modal-overlay')?.remove();

  // 1) 원본 카드 위치/크기 측정 (FLIP의 First)
  const originRect = originCard.getBoundingClientRect();

  // 2) 오버레이 + 모달 생성
  const overlay = document.createElement('div');
  overlay.className = 'parallel-card-modal-overlay';
  const badge = { done:'badge-done', running:'badge-running', error:'badge-error', pending:'badge-pending' }[r.status] || 'badge-pending';
  const label = { done:'완료', running:'실행 중', error:'실패', pending:'대기' }[r.status] || '';
  overlay.innerHTML = `
    <div class="parallel-card-modal" role="dialog" aria-modal="true">
      <div class="parallel-card-modal-header">
        <span class="model-name" style="font-weight:700;font-size:14px;color:var(--color-text-primary)">● ${esc(r.modelName || '모델')}</span>
        <span class="badge ${badge}" style="margin-left:8px;font-size:11px;padding:3px 10px;border-radius:12px;color:#fff">${label}</span>
        ${r.elapsed ? `<span style="font-size:11px;color:var(--color-text-muted);margin-left:8px">${fmtElapsed(r.elapsed)}</span>` : ''}
        <button class="parallel-card-modal-close" title="축소" type="button">축소</button>
      </div>
      <div class="parallel-card-modal-body">${r.status === 'done' ? fmtMd(r.content || '') : esc(r.content || '')}</div>
      <div class="parallel-card-modal-footer">
        ${r.status === 'done' ? '<button class="msg-action-btn modal-copy-btn" type="button" title="복사" style="width:30px;height:30px">' + SVG_COPY + '</button>' : '<span></span>'}
      </div>
    </div>`;
  document.body.appendChild(overlay);

  const modal = overlay.querySelector('.parallel-card-modal');

  // 3) 모달 최종 위치/크기 측정 (FLIP의 Last)
  const finalRect = modal.getBoundingClientRect();

  // 4) 원본 위치/크기로 transform 적용 (Invert)
  const dx = originRect.left - finalRect.left;
  const dy = originRect.top - finalRect.top;
  const sx = originRect.width / finalRect.width;
  const sy = originRect.height / finalRect.height;
  modal.style.transformOrigin = 'top left';
  modal.style.transform = `translate(${dx}px, ${dy}px) scale(${sx}, ${sy})`;
  modal.style.opacity = '0.5';
  // 오버레이 배경도 페이드인
  overlay.style.backgroundColor = 'rgba(0,0,0,0)';

  // 5) 다음 프레임에 원래 위치/크기로 transition (Play)
  requestAnimationFrame(() => {
    modal.style.transition = 'transform 320ms cubic-bezier(0.4, 0, 0.2, 1), opacity 200ms ease-out';
    modal.style.transform = 'translate(0, 0) scale(1, 1)';
    modal.style.opacity = '1';
    overlay.style.transition = 'background-color 250ms ease-out';
    overlay.style.backgroundColor = 'rgba(0,0,0,0.6)';
  });

  // 닫기 (FLIP 역방향: 모달 → 원본 카드 위치로 축소)
  const close = () => {
    // 현재 원본 카드 위치 재측정 (스크롤 변경 대응)
    const currentOriginRect = originCard.getBoundingClientRect();
    const currentModalRect = modal.getBoundingClientRect();
    const cdx = currentOriginRect.left - currentModalRect.left;
    const cdy = currentOriginRect.top - currentModalRect.top;
    const csx = currentOriginRect.width / currentModalRect.width;
    const csy = currentOriginRect.height / currentModalRect.height;

    modal.style.transition = 'transform 280ms cubic-bezier(0.4, 0, 1, 1), opacity 180ms ease-in';
    modal.style.transform = `translate(${cdx}px, ${cdy}px) scale(${csx}, ${csy})`;
    modal.style.opacity = '0.4';
    overlay.style.transition = 'background-color 200ms ease-in';
    overlay.style.backgroundColor = 'rgba(0,0,0,0)';

    setTimeout(() => overlay.remove(), 300);
    document.removeEventListener('keydown', onKey);
  };

  // 닫기 트리거
  modal.querySelector('.parallel-card-modal-close').addEventListener('click', close);
  overlay.addEventListener('click', (e) => { if (e.target === overlay) close(); });
  const onKey = (e) => { if (e.key === 'Escape') close(); };
  document.addEventListener('keydown', onKey);

  // 복사 버튼
  const copyBtn = modal.querySelector('.modal-copy-btn');
  if (copyBtn) {
    copyBtn.addEventListener('click', () => {
      navigator.clipboard.writeText(r.content || '').then(() => {
        copyBtn.innerHTML = SVG_CHECK;
        setTimeout(() => { copyBtn.innerHTML = SVG_COPY; }, 1200);
      }).catch(() => {});
    });
  }
}

// 결과 그리드 — 실행 중/완료/에러 실시간 표시 + 확장/축소
function renderParallelResultGrid() {
  const grid = document.getElementById('parallel-grid'), countEl = document.getElementById('parallel-count');
  if (!grid) return;
  const vals = [...state.parallelResults.values()];
  const done = vals.filter(r => r.status === 'done').length;
  const err = vals.filter(r => r.status === 'error').length;
  const running = vals.filter(r => r.status === 'running').length;
  if (countEl) countEl.textContent = `${vals.length}개 모델 — ${done} 완료 / ${running} 실행 중 / ${err} 실패`;

  const expandedSet = new Set();
  grid.querySelectorAll('.model-card.expanded').forEach(c => expandedSet.add(c.dataset.slotId));
  grid.innerHTML = '';

  for (const [sid, r] of state.parallelResults) {
    const badge = { done:'badge-done', running:'badge-running', error:'badge-error', pending:'badge-pending' }[r.status] || 'badge-pending';
    const label = { done:'완료', running:'실행 중', error:'실패', pending:'대기' }[r.status] || '';
    const nameColor = r.status === 'error' ? 'var(--color-error)' : r.status === 'done' ? 'var(--color-success)' : 'var(--color-text-primary)';
    const isExp = expandedSet.has(sid);

    // 에러 메시지 축약 (축소 시 1줄, 확장 시 전체)
    let displayContent = r.content || (r.status === 'running' ? '응답 대기 중...' : '...');
    if (r.status === 'error' && !isExp && displayContent.length > 80) {
      displayContent = displayContent.substring(0, 80) + '...';
    }

    const card = document.createElement('div');
    card.className = `model-card ${r.status === 'error' ? 'error' : r.status === 'done' ? 'done' : ''}${isExp ? ' expanded' : ''}`;
    card.dataset.slotId = sid;
    card.innerHTML = `
      <div class="model-card-header">
        <span class="model-name" style="color:${nameColor}">${r.modelName || '모델'}</span>
        ${r.elapsed ? `<span style="font-size:10px;color:var(--color-text-muted)">${fmtElapsed(r.elapsed)}</span>` : ''}
        <span class="badge ${badge}">${label}</span>
      </div>
      <div class="model-card-body" style="max-height:${isExp ? 'none' : '180px'}">${r.status === 'done' ? fmtMd(displayContent) : esc(displayContent)}</div>
      <div style="padding:3px 10px;border-top:1px solid var(--color-border-light);display:flex;justify-content:space-between;align-items:center">
        ${r.status === 'done' ? '<button class="msg-action-btn card-copy-btn" title="Copy" style="width:24px;height:24px">' + SVG_COPY + '</button>' : '<span></span>'}
        <button class="sm-btn card-toggle">${isExp ? '축소' : '확장'}</button>
      </div>`;

    card.querySelector('.card-toggle').addEventListener('click', () => {
      _showParallelCardModal(r, card);
    });

    // 복사 버튼
    const copyBtn = card.querySelector('.card-copy-btn');
    if (copyBtn) {
      copyBtn.addEventListener('click', () => {
        navigator.clipboard.writeText(r.content).then(() => {
          copyBtn.innerHTML = SVG_CHECK; setTimeout(() => { copyBtn.innerHTML = SVG_COPY; }, 1200);
        }).catch(() => {});
      });
    }

    grid.appendChild(card);
  }
}

// ===== Render Messages =====
// 스트리밍 깜빡임 방지용 상태: 마지막 렌더 시점의 메시지 스냅샷
const _renderCache = { count: 0, lastIdx: -1, lastLen: 0, lastKind: '', wfKey: '', toolKey: '', lastContentLen: 0 };

function _streamFastPath(){
  // 스트리밍 중이 아니면 fast-path 불가
  if(!state.isStreaming) return false;
  const msgs = state.messages.filter(m=>!m.hiddenInChat);
  if(msgs.length === 0) return false;
  // 마지막 메시지가 assistant가 아니면 불가
  const last = msgs[msgs.length-1];
  if(last.role !== 'assistant') return false;
  // 메시지 개수가 달라졌으면 fast-path 불가 (새 메시지 추가/삭제)
  if(msgs.length !== _renderCache.count) return false;
  // modelName(병렬 결과 카드)은 보수적으로 전체 재렌더
  if(last.modelName) return false;
  // workflow/toolUses가 있어도 step 상태·tool 개수가 변하지 않았다면 in-place 허용
  // status/개수만 키로 사용 — detail·content 길이는 in-place 갱신하므로 제외 (흔들림 방지)
  const _wfKey = last.workflow ? (last.workflow.steps||[]).map(st=>st.status+':'+(st.name||'')).join('|') : '';
  const _toolKey = last.toolUses ? last.toolUses.map(t=>(t.path||t.name||'')).join('|') + ':' + last.toolUses.length : '';
  if(_wfKey !== _renderCache.wfKey) return false;
  if(_toolKey !== _renderCache.toolKey) return false;

  const c = document.getElementById('chat-messages');
  if(!c) return false;
  const nodes = c.querySelectorAll('.chat-msg.assistant');
  const node = nodes[nodes.length-1];
  if(!node) return false;

  // content가 비어있다가 생긴 경우: thinking → 실제 본문 전환
  const hasThinking = !!node.querySelector('.thinking-indicator');
  if(last.content && hasThinking){
    // 전환은 전체 재렌더에 맡김
    return false;
  }
  // content가 계속 비어있는 동안 thinking 타이머만 갱신
  if(!last.content){
    const ind = node.querySelector('.thinking-indicator');
    if(!ind) return false;
    const elapsed = Math.floor((Date.now() - (state._streamStartTime || Date.now())) / 1000);
    const timeText = elapsed >= 3600 ? `${Math.floor(elapsed/3600)}h ${Math.floor((elapsed%3600)/60)}m` : elapsed >= 60 ? `${Math.floor(elapsed/60)}m ${elapsed%60}s` : `${elapsed}s`;
    // 텍스트 노드만 갱신 (dots span은 유지)
    const dots = ind.querySelector('.thinking-dots');
    ind.innerHTML = '';
    if(dots) ind.appendChild(dots);
    ind.appendChild(document.createTextNode(' thinking ' + timeText));
    return true;
  }
  // 본문이 있고 계속 스트리밍 중 → .md-body 또는 .msg-content만 교체
  const mc = node.querySelector('.msg-content');
  if(!mc) return false;
  // 오류 메시지는 보수적으로 전체 재렌더
  if(last.content.includes('[오류:') || last.content.includes('[합의 오류:')) return false;
  // consensus 등 특수 케이스는 전체 재렌더
  if(last.isConsensus) return false;

  // in-place 교체: .msg-content의 innerHTML만 갱신 (node 자체는 유지 → 깜빡임 없음)
  const elapsedHtml = last._elapsed ? `<div style="font-size:10px;color:var(--color-text-muted);margin-top:4px;text-align:right">${fmtElapsed(last._elapsed)}</div>` : '';
  const newHtml = `${fmtMd(last.content)}${elapsedHtml}`;
  // 동일하면 skip
  if(mc.getAttribute('data-stream-len') === String(last.content.length)) { _inPlaceUpdateSideCards(node, last); return true; }
  // 기존 action bar(복사/재생성 버튼)가 있으면 구조하여 innerHTML 교체 후 재첨부 → 버튼 유지
  const existingBar = mc.querySelector(':scope > .msg-action-bar');
  mc.innerHTML = newHtml;
  if(existingBar) mc.appendChild(existingBar);
  mc.setAttribute('data-stream-len', String(last.content.length));
  // workflow step detail / tool card body도 in-place 갱신 (카드 재생성 X → 흔들림 0)
  _inPlaceUpdateSideCards(node, last);
  // 스크롤 정책:
  // - 핀 활성: user 메시지가 뷰포트 최상단에 고정되도록 유지 (자동 스크롤 X)
  //   fast-path는 DOM을 파괴하지 않으므로 scrollTop이 유지되지만,
  //   답변이 길어지면서 scrollHeight가 늘어나도 scrollTop은 손대지 않는다.
  // - 핀 비활성 & 사용자가 하단 근처: 하단 추종
  if(!state._pinAnchorSet){
    const nearBottom = (c.scrollHeight - c.scrollTop - c.clientHeight) < 80;
    if(nearBottom) c.scrollTop = c.scrollHeight;
  }
  return true;
}

// fast-path 중 workflow step detail / tool card body를 in-place로 갱신 (요소 재생성 금지 → 흔들림 제거)
function _inPlaceUpdateSideCards(assistantNode, msg){
  try {
    // 형제 노드 중 workflow-step / tool-use-card 갱신
    const parent = assistantNode.parentElement;
    if(!parent) return;
    if(msg.workflow && msg.workflow.steps){
      // assistantNode 이후의 workflow-step 노드들을 순서대로 매칭
      const stepNodes = [];
      let sib = assistantNode.nextElementSibling;
      while(sib && (sib.classList.contains('workflow-step') || sib.classList.contains('tool-use-card') || sib.classList.contains('tool-summary-card') || sib.classList.contains('async-job-card'))){
        if(sib.classList.contains('workflow-step')) stepNodes.push(sib);
        sib = sib.nextElementSibling;
      }
      for(let i=0;i<msg.workflow.steps.length && i<stepNodes.length;i++){
        const s = msg.workflow.steps[i];
        const body = stepNodes[i].querySelector('.workflow-step-body');
        if(s.detail){
          if(body){
            if(body.getAttribute('data-detail-len') !== String(s.detail.length)){
              body.textContent = s.detail;
              body.setAttribute('data-detail-len', String(s.detail.length));
            }
          }
        }
        // step timer 갱신 (running → done 전환 시 즉시 반영)
        const timerEl = stepNodes[i].querySelector('.step-timer');
        if(timerEl && s.startedAt){
          if(s.endedAt){
            timerEl.textContent = fmtElapsedMs(s.endedAt - s.startedAt);
            timerEl.classList.remove('step-timer-running');
          } else {
            timerEl.textContent = fmtElapsedMs(Date.now() - s.startedAt);
          }
        }
      }
    }
    if(msg.toolUses && msg.toolUses.length){
      // tool-summary-card (하나의 접이식 컨테이너) in-place 갱신
      let sib = assistantNode.nextElementSibling;
      let summaryCard = null;
      while(sib){
        if(sib.classList.contains('tool-summary-card')) { summaryCard = sib; break; }
        sib = sib.nextElementSibling;
      }
      if(summaryCard){
        const lines = summaryCard.querySelectorAll('.tool-summary-line');
        for(let i=0;i<msg.toolUses.length && i<lines.length;i++){
          const t = msg.toolUses[i];
          const line = lines[i];
          // 상태 클래스 갱신
          const status = t.status || (t.endedAt ? 'done' : (t.startedAt ? 'running' : ''));
          line.className = `tool-summary-line tool-line-${status}`;
          // 타이머 갱신
          const timerEl = line.querySelector('.tool-line-timer');
          if(timerEl && t.startedAt){
            if(t.endedAt){
              timerEl.textContent = fmtElapsedMs(t.endedAt - t.startedAt);
              timerEl.classList.remove('step-timer-running');
            } else {
              timerEl.textContent = fmtElapsedMs(Date.now() - t.startedAt);
            }
          }
          // 상태 뱃지 (◌/✓/✕)
          const statusEl = line.querySelector('.tool-line-status');
          if(statusEl){
            const isRunning = status === 'running' || (!t.endedAt && t.startedAt);
            statusEl.textContent = isRunning ? '◌' : status === 'failed' ? '✕' : '✓';
            statusEl.style.color = isRunning ? 'var(--color-success)' : status === 'failed' ? 'var(--color-error)' : 'var(--color-success)';
          }
        }
        // 헤더 집계 갱신 (작업 N개 · 경과시간)
        const header = summaryCard.querySelector('.tool-summary-header');
        if(header){
          const hasRunning = msg.toolUses.some(t => t.status === 'running' || (!t.endedAt && t.startedAt));
          const hasFailed  = msg.toolUses.some(t => t.status === 'failed');
          const statusClass = hasRunning ? 'tool-summary-running' : hasFailed ? 'tool-summary-failed' : 'tool-summary-done';
          header.className = `tool-summary-header ${statusClass}`;
          const cntEl = header.querySelector('.tool-summary-count');
          if(cntEl) cntEl.textContent = `${msg.toolUses.length}개`;
          const iconEl = header.querySelector('.tool-summary-icon');
          if(iconEl) iconEl.textContent = hasRunning ? '◌' : hasFailed ? '✕' : '✓';
          // 전체 경과 갱신
          const validTools = msg.toolUses.filter(t => t.startedAt);
          if(validTools.length){
            const minStart = Math.min(...validTools.map(t => t.startedAt));
            const maxEnd   = Math.max(...validTools.map(t => t.endedAt || Date.now()));
            const tEl = header.querySelector('.tool-summary-timer');
            if(tEl) tEl.textContent = fmtElapsedMs(maxEnd - minStart);
          }
        }
      }
    }
  } catch(_){}
}

function renderMessages(){
  // 스트리밍 중이면 fast-path 시도 (전체 재렌더 회피 → 깜빡임 제거)
  if(_streamFastPath()) return;

  const FI = state.isStreaming ? '' : 'fade-in';
  const c=document.getElementById('chat-messages');c.innerHTML='';

  for(const msg of state.messages){
    // 병렬 결과 합본 등 내부 컨텍스트 전용 메시지는 채팅에 렌더하지 않음
    if(msg.hiddenInChat) continue;
    if(msg.role==='user'){
      const d=document.createElement('div');d.className=`chat-msg user ${FI}`;
      let ah='';if(msg.attachments?.length)ah=msg.attachments.map(a=>['png','jpg','jpeg'].includes(a.ext)?`<img src="${a.data}" style="max-width:200px;max-height:150px;border-radius:8px;margin-bottom:6px;display:block">`:`<div style="font-size:11px;color:rgba(255,255,255,0.7);margin-bottom:4px">+ ${a.name}</div>`).join('');
      d.innerHTML=`<div class="msg-content">${ah}${esc(msg.content)}</div>`;
      addCopySupport(d, msg.content);
      c.appendChild(d);
    }else if(msg.role==='system'){
      // 추천 카드 메시지인 경우 — 별도 렌더링
      if (msg._isRecommendCard && typeof _renderRecommendCardMessage === 'function') {
        const cardEl = _renderRecommendCardMessage(msg);
        if (cardEl) {
          c.appendChild(cardEl);
          continue;
        }
      }
      const d=document.createElement('div');d.className=`chat-msg system ${FI}`;
      d.textContent=msg.content;
      // [Fix #3] 재시도 가능한 에러 메시지에 재시도 버튼 추가
      if (msg._retryable && !state.isStreaming) {
        const retryBar = document.createElement('div');
        retryBar.style.cssText = 'margin-top:8px;display:flex;gap:6px;justify-content:center';
        // 부분 재시도 (실패한 슬롯만) — 병렬 모드 전용
        if (msg._retryType === 'parallel' && msg._retryPrompt) {
          const failedCount = [...state.parallelResults.values()].filter(r => r.status === 'error').length;
          if (failedCount > 0) {
            const partialBtn = document.createElement('button');
            partialBtn.className = 'sm-btn';
            partialBtn.style.cssText = 'background:var(--color-accent);color:#fff;border:none;padding:5px 12px;border-radius:5px;font-size:11px;cursor:pointer';
            partialBtn.textContent = `실패한 ${failedCount}개만 재시도`;
            partialBtn.title = '성공한 응답은 보존, 실패한 슬롯만 다시 호출';
            partialBtn.addEventListener('click', () => {
              if (typeof retryFailedParallel === 'function') {
                retryFailedParallel(msg._retryPrompt);
              } else {
                runParallel(msg._retryPrompt);
              }
            });
            retryBar.appendChild(partialBtn);
          }
        }
        // 전체 재시도
        const retryBtn = document.createElement('button');
        retryBtn.className = 'sm-btn';
        retryBtn.style.cssText = 'background:transparent;color:var(--color-text-secondary);border:1px solid var(--color-border);padding:5px 12px;border-radius:5px;font-size:11px;cursor:pointer';
        retryBtn.textContent = '전체 재시도';
        retryBtn.addEventListener('click', () => {
          if (msg._retryType === 'parallel' && msg._retryPrompt) {
            runParallel(msg._retryPrompt);
          } else if (msg._retryType === 'consensus') {
            runConsensus();
          }
        });
        retryBar.appendChild(retryBtn);
        d.appendChild(retryBar);
      }
      c.appendChild(d);
    }else{
      if(msg.modelName){
        const d=document.createElement('div');
        d.style.cssText='padding:0 12px;margin:1px 0;';
        const isErr=msg.modelStatus==='error', isDone=msg.modelStatus==='done';
        const stColor=isErr?'var(--color-error)':isDone?'var(--color-success)':'var(--color-text-muted)';
        const stLabel=isErr?'실패':isDone?'완료':'';
        const isCollapsed=msg.collapsed!==false;
        const bc=isErr?'var(--color-error)':isDone?'var(--color-success)':'var(--color-accent)';
        d.innerHTML=`<div style="border-left:2px solid ${bc};padding:${isCollapsed?'2px 8px':'6px 10px'};background:var(--color-bg-tertiary);border-radius:0 3px 3px 0">
          <div style="display:flex;align-items:center;gap:4px;cursor:pointer" class="mh">
            <span style="font-size:11px;font-weight:600;color:${stColor}">● ${esc(msg.modelName)}</span>
            <span style="font-size:9px;color:${stColor}">${stLabel}</span>
            ${!isCollapsed?`<span class="cp msg-action-btn" style="margin-left:4px;cursor:pointer;width:20px;height:20px" title="Copy">${SVG_COPY}</span>`:''}
            <span style="margin-left:auto;font-size:9px;color:var(--color-text-muted)">${isCollapsed?'▸':'▾'}</span>
          </div>
          ${isCollapsed?'':`<div style="margin-top:3px;font-size:12px;line-height:1.4;color:var(--color-text-secondary);max-height:200px;overflow-y:auto">${fmtMd(msg.content)}</div>`}
        </div>`;
        d.querySelector('.mh').addEventListener('click',e=>{if(e.target.closest('.cp'))return;msg.collapsed=!msg.collapsed;renderMessages();});
        const cpBtn=d.querySelector('.cp');
        if(cpBtn)cpBtn.addEventListener('click',e=>{e.stopPropagation();navigator.clipboard.writeText(msg.content).then(()=>{cpBtn.innerHTML=SVG_CHECK;setTimeout(()=>{cpBtn.innerHTML=SVG_COPY;},1200);}).catch(()=>{});});
        c.appendChild(d);
      } else if(msg.isConsensus){
        const d=document.createElement('div');d.className=`chat-msg assistant ${FI}`;
        if (msg.content) {
          const elapsedHtml = msg._elapsed ? `<div style="font-size:10px;color:var(--color-text-muted);margin-top:6px;text-align:right">${fmtElapsed(msg._elapsed)}</div>` : '';
          d.innerHTML=`<div class="msg-content" style="border-left:3px solid var(--color-success);background:var(--color-success-subtle)">
            <div style="font-size:12px;font-weight:700;margin-bottom:8px;color:var(--color-success)">합의 결과</div>
            <div class="md-body">${fmtMd(msg.content)}</div>${elapsedHtml}</div>`;
          // 합의 결과에는 Copy만 (Run Command 제외)
          const mc = d.querySelector('.msg-content');
          const bar = document.createElement('div'); bar.className = 'msg-action-bar';
          const copyBtn = document.createElement('button'); copyBtn.className = 'msg-action-btn';
          copyBtn.innerHTML = SVG_COPY; copyBtn.title = 'Copy';
          copyBtn.addEventListener('click', () => {
            navigator.clipboard.writeText(msg.content).then(() => {
              copyBtn.innerHTML = SVG_CHECK; setTimeout(() => { copyBtn.innerHTML = SVG_COPY; }, 1500);
            });
          });
          bar.appendChild(copyBtn); mc.appendChild(bar);
          // [합의 모델 이어가기] 액션 버튼 2개 — 합의 완료 후(스트리밍 아님)에만 노출
          if (!state.isStreaming && msg.consensusModelId) {
            const actions = document.createElement('div');
            actions.style.cssText = 'display:flex;gap:8px;margin-top:12px;padding-top:10px;border-top:1px dashed var(--color-border);flex-wrap:wrap';
            const lockBtn = document.createElement('button');
            lockBtn.className = 'sm-btn';
            lockBtn.style.cssText = 'background:var(--color-success);color:#fff;border:none;padding:6px 12px;border-radius:6px;font-size:12px;cursor:pointer';
            lockBtn.textContent = '이 모델로 계속 대화';
            lockBtn.title = `${msg.consensusModelName} 단일 모드로 전환하여 이 모델로만 대화를 이어갑니다`;
            lockBtn.addEventListener('click', () => continueWithConsensusModel(msg.consensusModelId, msg.consensusModelName));
            const parallelBtn = document.createElement('button');
            parallelBtn.className = 'sm-btn';
            parallelBtn.style.cssText = 'background:transparent;color:var(--color-text-secondary);border:1px solid var(--color-border);padding:6px 12px;border-radius:6px;font-size:12px;cursor:pointer';
            parallelBtn.textContent = '병렬 모드 유지';
            parallelBtn.title = '다음 메시지도 병렬로 여러 모델에 호출합니다 (기본 동작)';
            parallelBtn.addEventListener('click', () => keepParallelMode());
            actions.appendChild(lockBtn);
            actions.appendChild(parallelBtn);
            // [Fix #3] 합의 응답이 불완전하면 재시도 버튼 추가
            if (msg._retryable) {
              const retryBtn = document.createElement('button');
              retryBtn.className = 'sm-btn';
              retryBtn.style.cssText = 'background:var(--color-warning,#f59e0b);color:#fff;border:none;padding:6px 12px;border-radius:6px;font-size:12px;cursor:pointer';
              retryBtn.textContent = '합의 재시도';
              retryBtn.title = msg._retryError ? `이전 오류: ${msg._retryError}` : '합의를 다시 생성합니다';
              retryBtn.addEventListener('click', () => {
                // 기존 불완전 메시지 제거 후 재실행
                const idx = state.messages.indexOf(msg);
                if (idx >= 0) state.messages.splice(idx, 1);
                renderMessages();
                runConsensus();
              });
              actions.appendChild(retryBtn);
            }
            mc.appendChild(actions);
          }
        } else if (state.isStreaming) {
          d.innerHTML=`<div class="msg-content thinking-indicator" style="border-left:3px solid var(--color-success);background:var(--color-success-subtle)">
            <span class="thinking-dots"><span></span><span></span><span></span></span> 합의 도출 중</div>`;
        }
        c.appendChild(d);
      } else {
        if(msg.workflow){const jc=document.createElement('div');jc.className=`async-job-card ${FI}`;jc.innerHTML=`<div class="job-header"><span class="job-title">에이전트 작업</span></div><div class="job-body">실행 중... 모델: ${state.selectedModel?.name||'?'} Job: ${msg.workflow.id}</div>`;c.appendChild(jc);renderWorkflow(c,msg.workflow);}
        if(msg.toolUses?.length) renderToolSummary(c, msg.toolUses);
        if(msg.content){
          const d=document.createElement('div');d.className=`chat-msg assistant ${FI}`;
          const isError = msg.content.includes('[오류:') || msg.content.includes('[합의 오류:');
          if (isError) {
            const errorText = msg.content.match(/\[오류:\s*(.+?)\]/)?.[1] || msg.content;
            d.innerHTML=`<div class="msg-content" style="border-left:2px solid var(--color-error);padding:6px 10px">
              <div style="font-size:11px;color:var(--color-error);display:flex;align-items:center;gap:4px">
                <span style="font-weight:600">Error</span>
                ${msg._elapsed ? `<span style="color:var(--color-text-muted);font-weight:400">${fmtElapsed(msg._elapsed)}</span>` : ''}
              </div>
              <div style="font-size:11px;color:var(--color-text-secondary);margin-top:2px">${esc(errorText.substring(0, 200))}</div>
            </div>`;
          } else {
            const elapsedHtml = msg._elapsed ? `<div style="font-size:10px;color:var(--color-text-muted);margin-top:4px;text-align:right">${fmtElapsed(msg._elapsed)}</div>` : '';
            d.innerHTML=`<div class="msg-content">${fmtMd(msg.content)}${elapsedHtml}</div>`;
          }
          addCopySupport(d, msg.content);
          // Checkpoint restore 버튼 — 스트리밍 아닐 때, 체크포인트가 있으면 표시
          if (msg._checkpointCreated && !state.isStreaming) {
            const allDone = !msg.workflow || msg.workflow.steps.every(s => s.status === 'done' || s.status === 'failed');
            if (allDone) {
              const restoreBar = document.createElement('div');
              restoreBar.style.cssText = 'margin-top:8px;padding-top:8px;border-top:1px solid var(--color-border);display:flex;gap:8px;align-items:center';
              restoreBar.innerHTML = `<button class="msg-action-btn" style="width:auto;padding:4px 10px;font-size:11px;gap:4px;display:inline-flex;align-items:center" data-restore-checkpoint title="에이전트 작업 전 상태로 되돌리기">⟲ 되돌리기</button><span style="font-size:10px;color:var(--color-text-muted)">에이전트 작업 전 체크포인트</span>`;
              restoreBar.querySelector('[data-restore-checkpoint]').addEventListener('click', async () => {
                if (!confirm('이 질문/답변을 되돌리고 파일을 작업 전 상태로 복원합니다. 계속하시겠습니까?')) return;
                const btn = restoreBar.querySelector('[data-restore-checkpoint]');
                btn.textContent = '복원 중...'; btn.disabled = true;
                try {
                  // 에이전트가 수정한 파일을 먼저 버리고 stash pop (충돌 방지)
                  await window.electronAPI.gitDiscardAll(state.folderPath);
                  const r = await window.electronAPI.gitStashPop(state.folderPath);
                  if (r.ok) {
                    // 이 assistant 메시지의 인덱스를 찾아서, 바로 위 user 메시지와 함께 삭제
                    const msgIdx = state.messages.indexOf(msg);
                    let userPrompt = '';
                    if (msgIdx > 0 && state.messages[msgIdx - 1]?.role === 'user') {
                      userPrompt = state.messages[msgIdx - 1].content || '';
                      state.messages.splice(msgIdx - 1, 2); // user + assistant 삭제
                    } else {
                      state.messages.splice(msgIdx, 1); // assistant만 삭제
                    }
                    // 되돌리기 완료 메시지
                    state.messages.push({ role:'system', content:'되돌리기 완료 — 코드와 채팅이 이전 상태로 복원되었습니다.' });
                    // 입력창에 이전 질문 복원 → 사용자가 바로 재전송 가능
                    const input = document.getElementById('chat-input');
                    if (input && userPrompt) {
                      input.value = userPrompt;
                      input.style.height = 'auto';
                      input.style.height = Math.min(input.scrollHeight, 120) + 'px';
                      input.focus();
                    }
                  } else {
                    state.messages.push({ role:'system', content:`복원 실패: ${r.error}` });
                  }
                } catch (e) {
                  state.messages.push({ role:'system', content:`복원 오류: ${e.message}` });
                }
                renderMessages();
              });
              d.querySelector('.msg-content')?.appendChild(restoreBar);
            }
          }
          c.appendChild(d);
        } else if(msg._thinking || (!msg.workflow && state.isStreaming)) {
          const d=document.createElement('div');d.className=`chat-msg assistant ${FI}`;
          const elapsed = Math.floor((Date.now() - (state._streamStartTime || Date.now())) / 1000);
          const timeText = elapsed >= 3600 ? `${Math.floor(elapsed/3600)}h ${Math.floor((elapsed%3600)/60)}m` : elapsed >= 60 ? `${Math.floor(elapsed/60)}m ${elapsed%60}s` : `${elapsed}s`;
          const label = msg._thinkingLabel || 'thinking';
          d.innerHTML=`<div class="msg-content thinking-indicator"><span class="thinking-dots"><span></span><span></span><span></span></span> ${esc(label)} ${timeText}</div>`;
          c.appendChild(d);
        }
      }
    }
  }
  // 렌더 캐시 업데이트 (스트리밍 fast-path 판단용)
  const visible = state.messages.filter(m=>!m.hiddenInChat);
  _renderCache.count = visible.length;
  const lastV = visible[visible.length-1];
  _renderCache.lastLen = lastV && lastV.content ? lastV.content.length : 0;
  _renderCache.lastKind = lastV ? lastV.role : '';
  _renderCache.lastContentLen = lastV && lastV.content ? lastV.content.length : 0;
  // fast-path 판정 키와 100% 일치해야 함 (불일치 시 매번 전체 재렌더 → 스크롤 리셋 버그)
  _renderCache.wfKey = (lastV && lastV.workflow) ? (lastV.workflow.steps||[]).map(st=>st.status+':'+(st.name||'')).join('|') : '';
  _renderCache.toolKey = (lastV && lastV.toolUses) ? lastV.toolUses.map(t=>(t.path||t.name||'')).join('|') + ':' + lastV.toolUses.length : '';
  // 스크롤 정책:
  // 1) user 메시지 핀이 활성화 & 앵커가 아직 설정 전이면 여기서는 손대지 않음 (sendChat의 rAF가 처리)
  // 2) 핀이 이미 설정된 상태(스트리밍 중)에는 자동 스크롤 억제 — 사용자 질문 위치 유지
  // 3) 그 외엔 하단 근처일 때만 따라감
  if(state._pinAnchorSet){
    // 핀 활성화 동안: DOM이 재생성(innerHTML='')되면 scrollTop이 0으로 리셋되므로
    // 매 렌더마다 해당 user 메시지 노드를 뷰포트 최상단으로 재앵커링한다.
    // (이걸 안 하면 질문이 "아래쪽에 머물러있다가 완료 후 제자리 찾는" 현상 발생)
    // 전체 재렌더로 style이 날아갈 일은 없지만, 혹시 모르니 padding-bottom을 재보장.
    if(state._pinSpacerPx && state._pinSpacerPx > 0){
      if(c.style.paddingBottom !== state._pinSpacerPx + 'px'){
        c.style.paddingBottom = state._pinSpacerPx + 'px';
      }
    }
    const userNodes = c.querySelectorAll('.chat-msg.user');
    const target = userNodes[userNodes.length-1];
    if(target){
      const desired = Math.max(0, target.offsetTop - 4);
      // 이미 같은 위치면 건너뛰어 불필요한 레이아웃 방지
      if(Math.abs(c.scrollTop - desired) > 1) c.scrollTop = desired;
    }
  } else {
    const nearBottom = (c.scrollHeight - c.scrollTop - c.clientHeight) < 80;
    if(nearBottom) c.scrollTop = c.scrollHeight;
  }
}

// SVG 아이콘
const SVG_COPY = '<svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><rect x="9" y="9" width="13" height="13" rx="2" ry="2"/><path d="M5 15H4a2 2 0 0 1-2-2V4a2 2 0 0 1 2-2h9a2 2 0 0 1 2 2v1"/></svg>';
const SVG_CHECK = '<svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="var(--color-success)" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round"><polyline points="20 6 9 17 4 12"/></svg>';
const SVG_TERMINAL = '<svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><polyline points="4 17 10 11 4 5"/><line x1="12" y1="19" x2="20" y2="19"/></svg>';

// 복사 + 실행 버튼 지원
// === Annotation Queue: 응답 피드백 디스크 저장 ===
async function _saveFeedback(rating, text, reason) {
  try {
    const fb = {
      rating,                                     // 'positive' | 'negative'
      text: (text || '').substring(0, 5000),
      reason: (reason || '').substring(0, 500),
      model: state.selectedModel?.id || '',
      mode: state.mode || '',
      timestamp: Date.now(),
      iso: new Date().toISOString(),
      sessionId: chatSessions[activeSessionIdx]?.id || '',
    };
    if (window.electronAPI?.getUserDataPath && window.electronAPI?.writeFile) {
      const udp = await window.electronAPI.getUserDataPath();
      // 일별 JSONL 파일에 append (Annotation Queue)
      const date = new Date().toISOString().slice(0, 10);
      const path = `${udp}/feedback/${date}.jsonl`;
      // 기존 내용 읽고 append
      let existing = '';
      try {
        existing = await window.electronAPI.readFile(path) || '';
      } catch { existing = ''; }
      const line = JSON.stringify(fb) + '\n';
      await window.electronAPI.writeFile(path, existing + line);
      addLiveLog('system', `피드백 저장 — ${rating} (${path})`);
    }
  } catch (e) {
    console.warn('[Feedback] 저장 실패:', e);
  }
}

function addCopySupport(el, text) {
  const mc = el.querySelector('.msg-content') || el.querySelector('[style*="border-left"]') || el;
  // 버튼 컨테이너
  const bar = document.createElement('div');
  bar.className = 'msg-action-bar';

  // Copy 버튼
  const copyBtn = document.createElement('button');
  copyBtn.className = 'msg-action-btn';
  copyBtn.innerHTML = SVG_COPY;
  copyBtn.title = 'Copy';
  copyBtn.addEventListener('click', (ev) => {
    ev.stopPropagation();
    navigator.clipboard.writeText(text).then(() => {
      copyBtn.innerHTML = SVG_CHECK;
      setTimeout(() => { copyBtn.innerHTML = SVG_COPY; }, 1500);
    }).catch(() => {
      const ta = document.createElement('textarea'); ta.value = text; ta.style.cssText = 'position:fixed;left:-9999px';
      document.body.appendChild(ta); ta.select(); document.execCommand('copy'); document.body.removeChild(ta);
      copyBtn.innerHTML = SVG_CHECK;
      setTimeout(() => { copyBtn.innerHTML = SVG_COPY; }, 1500);
    });
  });
  bar.appendChild(copyBtn);

  // === Annotation Queue / 피드백 루프 ===
  // 👍/👎 버튼 — 응답 품질 평가, 디스크에 저장하여 다음 평가/개선에 활용
  const upBtn = document.createElement('button');
  upBtn.className = 'msg-action-btn';
  upBtn.innerHTML = '↑';
  upBtn.title = '도움됨';
  upBtn.style.cssText = 'font-weight:bold;font-size:13px';
  upBtn.addEventListener('click', (ev) => {
    ev.stopPropagation();
    _saveFeedback('positive', text, '');
    upBtn.style.color = 'var(--color-success, #4ec9b0)';
    upBtn.disabled = true;
  });
  bar.appendChild(upBtn);

  const downBtn = document.createElement('button');
  downBtn.className = 'msg-action-btn';
  downBtn.innerHTML = '↓';
  downBtn.title = '도움 안 됨';
  downBtn.style.cssText = 'font-weight:bold;font-size:13px';
  downBtn.addEventListener('click', (ev) => {
    ev.stopPropagation();
    const reason = prompt('어떤 점이 부족했나요? (선택사항, 향후 개선에 활용)');
    if (reason !== null) {
      _saveFeedback('negative', text, reason || '');
      downBtn.style.color = 'var(--color-error, #f44747)';
      downBtn.disabled = true;
    }
  });
  bar.appendChild(downBtn);

  // 실행 가능한 명령어가 있을 때만 Run Command 버튼 추가
  const codeMatch = text.match(/```(?:bash|sh|shell|zsh|cmd|powershell)\n([\s\S]*?)```/);
  const shellPrompt = text.match(/^\s*[$>]\s*\w+.+/m);
  if (codeMatch || shellPrompt) {
    const runBtn = document.createElement('button');
    runBtn.className = 'msg-action-btn';
    runBtn.innerHTML = SVG_TERMINAL;
    runBtn.title = 'Run in terminal';
    runBtn.addEventListener('click', (ev) => {
      ev.stopPropagation();
      let cmd = '';
      if (codeMatch) {
        cmd = codeMatch[1].trim();
      } else if (shellPrompt) {
        cmd = shellPrompt[0].replace(/^\s*[$>]\s*/, '').trim();
      }
      if (cmd && state.terminals.length && window.electronAPI?.terminalWrite) {
        const tid = state.terminals[state.activeTerminalIdx]?.id;
        if (tid) {
          window.electronAPI.terminalWrite(tid, cmd + '\n');
          runBtn.innerHTML = SVG_CHECK;
          setTimeout(() => { runBtn.innerHTML = SVG_TERMINAL; }, 1500);
        }
      }
    });
    bar.appendChild(runBtn);
  }

  mc.appendChild(bar);
}
function renderWorkflow(c, wf) {
  for (const s of wf.steps) {
    const d = document.createElement('div');
    const sc = { done:'step-done', running:'step-running', failed:'step-failed' }[s.status] || '';
    d.className = `workflow-step ${sc} ${state.isStreaming ? '' : 'fade-in'}`;
    const ic = { done:'done', running:'running', failed:'failed' }[s.status] || '';
    const bc = { done:'step-badge-done', running:'step-badge-running', failed:'step-badge-failed' }[s.status] || '';
    const bt = { done:'완료', running:'진행 중', failed:'실패' }[s.status] || '';
    // 타이머 계산
    let timerHtml = '';
    if (s.startedAt) {
      d.setAttribute('data-step-started', String(s.startedAt));
      if (s.endedAt) {
        d.setAttribute('data-step-ended', String(s.endedAt));
        timerHtml = `<span class="step-timer" data-step-timer style="margin-left:8px;font-size:10px;color:var(--color-text-muted);font-variant-numeric:tabular-nums">${fmtElapsedMs(s.endedAt - s.startedAt)}</span>`;
      } else {
        // running — 실시간 갱신 대상
        timerHtml = `<span class="step-timer step-timer-running" data-step-timer style="margin-left:8px;font-size:10px;color:var(--color-success);font-variant-numeric:tabular-nums">${fmtElapsedMs(Date.now() - s.startedAt)}</span>`;
      }
    }
    d.innerHTML = `<div class="workflow-step-header"><span class="step-indicator ${ic}"></span><span class="step-title">● ${esc(s.name)}</span>${bt ? `<span class="step-badge ${bc}">${bt}</span>` : ''}${timerHtml}</div>${s.detail ? `<div class="workflow-step-body">${esc(s.detail)}</div>` : ''}`;
    c.appendChild(d);
  }
  if (wf.steps.some(x => x.status === 'running')) _ensureStepTimer();
}
// 도구 이름을 친숙한 한글 라벨로 변환
// — 편집기 맥락에 맞는 명확한 동사형: "파일 읽기", "명령 실행" 등
function toolDisplayName(rawName) {
  const n = (rawName || '').toLowerCase();
  if (n.includes('read_file') || n === 'read')         return '파일 읽기';
  if (n.includes('write_file') || n === 'write')       return '파일 쓰기';
  if (n.includes('edit_file') || n === 'edit')         return '파일 편집';
  if (n.includes('search_files') || n.includes('grep'))return '파일 검색';
  if (n.includes('list_directory') || n.includes('ls'))return '폴더 목록';
  if (n.includes('run_command') || n.includes('bash') || n.includes('shell')) return '명령 실행';
  if (n.includes('fetch') || n.includes('web'))        return '웹 조회';
  if (n.includes('apply') || n.includes('patch'))      return '패치 적용';
  if (n.includes('delete') || n.includes('rm'))        return '파일 삭제';
  if (n.includes('move') || n.includes('rename'))      return '파일 이동';
  if (n.includes('copy') || n.includes('cp'))          return '파일 복사';
  return rawName || '작업';
}

// 도구 인자를 한 줄 라벨로 포맷 — 어떤 파일/명령인지 한눈에
function formatToolArg(t) {
  if (!t) return '—';
  const label = toolDisplayName(t.name);
  const inp = t.input || {};
  const n = (t.name || '').toLowerCase();

  // 파일 계열: path 우선, 없으면 t.path fallback
  const filePath = inp.file_path || inp.path || t.path || '';

  if (n.includes('read_file') || n === 'read')   return `${label} · ${filePath || '—'}`;
  if (n.includes('write_file') || n === 'write') return `${label} · ${filePath || '—'}`;
  if (n.includes('edit_file') || n === 'edit')   return `${label} · ${filePath || '—'}`;
  if (n.includes('list_directory') || n.includes('ls')) return `${label} · ${filePath || '.'}`;
  if (n.includes('search_files') || n.includes('grep')) {
    const q = inp.query || inp.pattern || '';
    const scope = inp.path ? ` (${inp.path})` : '';
    return `${label} · ${q ? '"' + q.substring(0,40) + (q.length > 40 ? '…' : '') + '"' : '—'}${scope}`;
  }
  if (n.includes('run_command') || n.includes('bash') || n.includes('shell')) {
    const cmd = inp.command || '';
    return `${label} · ${cmd.substring(0, 50)}${cmd.length > 50 ? '…' : ''}`;
  }
  if (n.includes('fetch') || n.includes('web')) return `${label} · ${inp.url || '—'}`;

  // 기본: path가 있으면 그걸 사용, 없으면 JSON 요약
  if (filePath) return `${label} · ${filePath}`;
  try {
    const json = JSON.stringify(inp);
    if (json && json !== '{}') {
      return `${label} · ${json.substring(0, 60)}${json.length > 60 ? '…' : ''}`;
    }
  } catch {}
  return label;
}

// 모든 toolUses를 하나의 접이식 컨테이너로 렌더
//  ┌─ 기본 접힘 ─ "⚙ 작업 3개 · 2.1s ▸"
//  └─ 클릭 시 펼침 → 각 줄 클릭 시 상세(입력/출력) 토글
function renderToolSummary(c, toolUses) {
  if (!toolUses || !toolUses.length) return;

  const details = document.createElement('details');
  details.className = 'tool-summary-card';

  // startedAt/endedAt 범위 계산
  const validTools = toolUses.filter(t => t.startedAt);
  const minStart = validTools.length ? Math.min(...validTools.map(t => t.startedAt)) : null;
  const maxEnd   = validTools.length ? Math.max(...validTools.map(t => t.endedAt || Date.now())) : null;

  // 집계 상태 — running > failed > done 순 우선
  const hasRunning = validTools.some(t => t.status === 'running' || (!t.endedAt && t.startedAt));
  const hasFailed  = toolUses.some(t => t.status === 'failed');
  const statusClass = hasRunning ? 'tool-summary-running' : hasFailed ? 'tool-summary-failed' : 'tool-summary-done';
  const statusIcon  = hasRunning ? '◌' : hasFailed ? '✕' : '✓';

  // 전체 경과시간
  let elapsedHtml = '';
  if (minStart && maxEnd) {
    const elapsedMs = maxEnd - minStart;
    elapsedHtml = `<span class="tool-summary-timer" ${hasRunning ? 'data-tool-started="'+minStart+'"' : ''} style="font-variant-numeric:tabular-nums">${fmtElapsedMs(elapsedMs)}</span>`;
  }

  // 스트리밍 중에는 기본 펼침, 끝나면 접힘 (사용자 요청: 평상시 1줄 요약)
  if (hasRunning) details.open = true;

  // 요약 헤더 (클릭 가능)
  const summary = document.createElement('summary');
  summary.className = `tool-summary-header ${statusClass}`;
  summary.innerHTML = `
    <span class="tool-summary-icon">${statusIcon}</span>
    <span class="tool-summary-label">작업</span>
    <span class="tool-summary-count">${toolUses.length}개</span>
    ${elapsedHtml ? `<span class="tool-summary-dot">·</span>${elapsedHtml}` : ''}
    <span class="tool-summary-chevron">▸</span>
  `;
  details.appendChild(summary);

  // 상세 영역
  const body = document.createElement('div');
  body.className = 'tool-summary-body';

  toolUses.forEach((t, idx) => {
    const toolLine = document.createElement('div');
    const status = t.status || (t.endedAt ? 'done' : (t.startedAt ? 'running' : ''));
    const isRunning = status === 'running' || (!t.endedAt && t.startedAt);
    toolLine.className = `tool-summary-line tool-line-${status}`;

    const arg = formatToolArg(t);

    // 라인 타이머
    let timerHtml = '';
    if (t.startedAt) {
      toolLine.setAttribute('data-step-started', String(t.startedAt));
      if (t.endedAt) {
        toolLine.setAttribute('data-step-ended', String(t.endedAt));
        timerHtml = `<span class="tool-line-timer">${fmtElapsedMs(t.endedAt - t.startedAt)}</span>`;
      } else {
        timerHtml = `<span class="step-timer step-timer-running tool-line-timer" data-step-timer>${fmtElapsedMs(Date.now() - t.startedAt)}</span>`;
      }
    }

    // 상세 내용 (입력/출력)
    const inputTxt = t.content || (t.input ? JSON.stringify(t.input, null, 2) : '');
    const outputTxt = t.output ? (typeof t.output === 'string' ? t.output : JSON.stringify(t.output, null, 2)) : '';
    const hasDetail = !!(inputTxt || outputTxt);

    const statusBadge = isRunning ? '◌' : status === 'failed' ? '✕' : '✓';
    const statusColor = isRunning ? 'var(--color-success)' : status === 'failed' ? 'var(--color-error)' : 'var(--color-success)';

    toolLine.innerHTML = `
      <div class="tool-line-header" style="cursor:${hasDetail ? 'pointer' : 'default'}">
        ${hasDetail ? '<span class="tool-line-chevron">▶</span>' : '<span class="tool-line-chevron-spacer"></span>'}
        <span class="tool-line-status" style="color:${statusColor}">${statusBadge}</span>
        <span class="tool-line-arg">${esc(arg)}</span>
        ${timerHtml}
      </div>
      ${hasDetail ? `<div class="tool-line-detail" style="display:none">
        ${inputTxt ? `<div class="tool-line-detail-section">
          <div class="tool-line-detail-title">입력</div>
          <pre class="tool-line-detail-content">${esc(inputTxt)}</pre>
        </div>` : ''}
        ${outputTxt ? `<div class="tool-line-detail-section">
          <div class="tool-line-detail-title">출력</div>
          <pre class="tool-line-detail-content">${esc(outputTxt.substring(0, 8000))}${outputTxt.length > 8000 ? '\n…[' + (outputTxt.length - 8000) + '자 더 있음]' : ''}</pre>
        </div>` : ''}
      </div>` : ''}
    `;

    if (hasDetail) {
      const header = toolLine.querySelector('.tool-line-header');
      const detail = toolLine.querySelector('.tool-line-detail');
      const chev = toolLine.querySelector('.tool-line-chevron');
      header.addEventListener('click', (ev) => {
        ev.stopPropagation();
        const isOpen = detail.style.display !== 'none';
        detail.style.display = isOpen ? 'none' : '';
        if (chev) chev.style.transform = isOpen ? '' : 'rotate(90deg)';
      });
    }

    // === Media generation result: inline preview + download ===
    // 도구 결과 JSON에서 .generated/* 경로를 감지하여 미디어 카드를 렌더한다.
    // - 이미지(.png/.jpg/.webp/.gif): 갤러리 형태 썸네일 (max 320×240, contain)
    //   · 단일 도구 결과에 N>4개 이미지가 있으면 4개만 표시 + "+N개 더보기" 링크
    //   · 썸네일 아래 한 줄 메타: "model · W×H px"
    //   · 단일/병렬/합의 모드 모두 동일 (renderToolSummary가 공통 진입점)
    // - PDF/PPTX: 단일 카드(아이콘 + 메타 + 미리보기/다운로드 버튼) — 기존 동작 유지
    if (t.name && /^(generate_image|generate_pdf|generate_pptx|edit_image)$/.test(t.name) && outputTxt) {
      try {
        const parsed = JSON.parse(outputTxt);
        // 다중 이미지 표현을 허용: parsed.images = [{path, model, width, height}, ...]
        // 또는 단일 이미지 표현: parsed.path + parsed.model + parsed.width/height
        const items = [];
        if (Array.isArray(parsed?.images)) {
          for (const it of parsed.images) {
            if (it && typeof it.path === 'string' && it.path.startsWith('.generated/')) items.push(it);
          }
        }
        if (parsed && typeof parsed.path === 'string' && parsed.path.startsWith('.generated/')) {
          items.push(parsed);
        }

        if (items.length) {
          // 이미지 항목과 비-이미지 항목 분리
          const isImg = (p) => /\.(png|jpg|jpeg|webp|gif)$/i.test(p || '');
          const imgItems = items.filter(it => isImg(it.path));
          const docItems = items.filter(it => !isImg(it.path));

          // TASK 8 근본수정 — 채팅 카드 경로를 백엔드 absPath 우선으로 해석.
          // 기존엔 state.folderPath + 상대경로로 추측 조립 → 원격세션/패키징/폴더미오픈
          // 시 백엔드 실제 저장 위치(_resolve_local_root)와 갈려서 "보이는데 다운로드 없음".
          // 이제 도구 응답의 it.absPath(절대경로)를 최우선 사용한다.
          //   1) it.absPath (백엔드가 실제 저장한 절대경로) — 가장 정확
          //   2) state.folderPath + 상대경로 (로컬 폴더 작업, 하위호환)
          //   3) window.__workstationCwd + 상대경로 (debug/cwd generatedRoot, 패널과 동일 후보)
          //   4) 상대경로 그대로 (최후)
          const resolveItemPath = (item) => {
            if (item && typeof item.absPath === 'string' && item.absPath) return item.absPath;
            const relPath = (item && typeof item === 'object') ? (item.path || '') : (item || '');
            if (state.folderPath) return `${state.folderPath.replace(/\/$/, '')}/${relPath}`;
            if (typeof window !== 'undefined' && window.__workstationCwd) {
              return `${window.__workstationCwd.replace(/\/$/, '')}/${relPath}`;
            }
            return relPath;
          };

          // ── 이미지 갤러리 (최대 4개) ─────────────────────────────
          if (imgItems.length) {
            const MAX_DISPLAY = 4;
            // 표시 개수/초과분/더보기 텍스트 계산은 순수 함수로 분리되어 있다
            // (Property 10: 채팅 이미지 표시 개수 제한 — Validates Requirements 6.6).
            // src/lib/image-thumbnails.js, 테스트: tests/unit/image-thumbnails.test.js
            const _thumbList = (typeof buildImageThumbnailList === 'function')
              ? buildImageThumbnailList(imgItems, MAX_DISPLAY)
              : { displayed: imgItems.slice(0, MAX_DISPLAY), overflow: Math.max(0, imgItems.length - MAX_DISPLAY) };
            const displayed = _thumbList.displayed;
            const overflow = _thumbList.overflow;

            const gallery = document.createElement('div');
            gallery.className = 'tool-images';
            gallery.style.cssText = 'margin:6px 0 10px 24px;display:flex;flex-wrap:wrap;gap:10px;align-items:flex-start;';

            for (const it of displayed) {
              const ext = (it.path.split('.').pop() || '').toLowerCase();
              const fileName = it.path.split('/').pop();
              const dims = (it.width && it.height) ? `${it.width}×${it.height} px` : '';
              const metaParts = [];
              if (it.model) metaParts.push(esc(it.model));
              if (dims) metaParts.push(dims);
              const metaLine = metaParts.join(' · ');

              const thumb = document.createElement('div');
              thumb.className = 'tool-image-thumb';
              thumb.dataset.path = it.path;
              thumb.style.cssText = 'display:flex;flex-direction:column;gap:4px;cursor:pointer;';
              thumb.innerHTML = `
                <div class="tit-frame" style="width:320px;max-width:320px;height:240px;max-height:240px;background:var(--color-bg-tertiary,#2d2d30);border:1px solid var(--color-border,#3c3c3c);border-radius:4px;display:flex;align-items:center;justify-content:center;overflow:hidden;transition:border-color 150ms ease;">
                  <span class="tit-loading" style="color:var(--color-text-muted,#6a6a6a);font-size:11px;">로딩 중…</span>
                </div>
                <div class="tit-meta" style="max-width:320px;font-size:11px;color:var(--color-text-secondary,#9d9d9d);overflow:hidden;text-overflow:ellipsis;white-space:nowrap;display:flex;align-items:center;gap:6px;" title="${esc(fileName)} — ${esc(metaLine)}">
                  <span style="flex:1;overflow:hidden;text-overflow:ellipsis;">${metaLine || esc(fileName)}</span>
                  <button class="tit-open-folder tmc-action-btn" type="button" title="폴더에서 보기">폴더</button>
                  <button class="tit-edit tmc-action-btn" type="button" title="수정">수정</button>
                  <button class="tit-delete tmc-action-btn" type="button" title="삭제">삭제</button>
                  <button class="tit-download tmc-action-btn" type="button" title="다운로드">다운로드</button>
                </div>
              `;

              // 비동기 base64 로드 → <img>로 교체 (object-fit: contain)
              (async () => {
                const fp = resolveItemPath(it);
                try {
                  // 사용자 정책: 생성 파일은 로컬에만 저장 → 로컬 IPC 우선
                  let b64 = null;
                  if (window.electronAPI?.readFileBase64Local) {
                    b64 = await window.electronAPI.readFileBase64Local(fp).catch(() => null);
                  }
                  if (!b64) b64 = await window.electronAPI.readFileBase64(fp);
                  const frame = thumb.querySelector('.tit-frame');
                  if (!frame) return;
                  if (b64) {
                    const mime = ext === 'jpg' ? 'jpeg' : ext;
                    frame.innerHTML = `<img src="data:image/${mime};base64,${b64}" alt="${esc(fileName)}" style="max-width:320px;max-height:240px;width:100%;height:100%;object-fit:contain;display:block;" />`;
                  } else {
                    // 파일 미존재 또는 읽기 실패 → 에러 플레이스홀더 (320×80)
                    frame.style.height = '80px';
                    frame.style.maxHeight = '80px';
                    frame.style.borderColor = 'var(--color-error,#f44747)';
                    frame.innerHTML = `<div style="padding:8px;font-size:11px;color:var(--color-error,#f44747);text-align:center;word-break:break-all;">이미지 로드 실패<br><span style="color:var(--color-text-muted,#6a6a6a);font-size:10px;">${esc(it.path)}</span></div>`;
                  }
                } catch (err) {
                  const frame = thumb.querySelector('.tit-frame');
                  if (frame) {
                    frame.style.height = '80px';
                    frame.style.maxHeight = '80px';
                    frame.style.borderColor = 'var(--color-error,#f44747)';
                    frame.innerHTML = `<div style="padding:8px;font-size:11px;color:var(--color-error,#f44747);text-align:center;word-break:break-all;">이미지 로드 실패<br><span style="color:var(--color-text-muted,#6a6a6a);font-size:10px;">${esc(it.path)}</span></div>`;
                  }
                }
              })();

              // 썸네일 클릭 → 에디터 영역에서 전체 보기 + file-preview-panel 선택 동기화 (12.2)
              thumb.addEventListener('click', (ev) => {
                ev.stopPropagation();
                const fp = resolveItemPath(it);
                if (typeof openMediaPreview === 'function') openMediaPreview(fp, fileName);
                // file-preview-panel과 동기화 — 패널이 열려 있으면 해당 항목 활성화
                document.dispatchEvent(new CustomEvent('preview-panel:select', {
                  detail: { path: it.path, fullPath: fp, name: fileName },
                  bubbles: true,
                }));
              });

              // 폴더 열기 버튼
              const folderBtn = thumb.querySelector('.tit-open-folder');
              if (folderBtn) {
                folderBtn.addEventListener('click', (ev) => {
                  ev.stopPropagation();
                  ev.preventDefault();
                  const fp = resolveItemPath(it);
                  if (window.electronAPI && window.electronAPI.showItemInFolder) {
                    window.electronAPI.showItemInFolder(fp);
                  }
                });
              }
              // 수정 버튼 — 파일을 채팅 첨부로 등록
              const editBtn = thumb.querySelector('.tit-edit');
              if (editBtn) {
                editBtn.addEventListener('click', (ev) => {
                  ev.stopPropagation();
                  const fp = resolveItemPath(it);
                  _attachGeneratedFileForEdit({ path: fp, name: fileName, ext });
                });
              }
              // 삭제 버튼
              const deleteBtn = thumb.querySelector('.tit-delete');
              if (deleteBtn) {
                deleteBtn.addEventListener('click', async (ev) => {
                  ev.stopPropagation();
                  if (!confirm(`"${fileName}" 파일을 삭제하시겠습니까?`)) return;
                  const fp = resolveItemPath(it);
                  try {
                    if (window.electronAPI?.deleteFile) {
                      await window.electronAPI.deleteFile(fp);
                      thumb.remove();
                      addLiveLog && addLiveLog('system', `삭제됨: ${fileName}`);
                    }
                  } catch (e) { console.error('[gallery] delete failed', e); }
                });
              }
              // 다운로드 버튼
              const dlBtnMeta = thumb.querySelector('.tit-download');
              if (dlBtnMeta) {
                dlBtnMeta.addEventListener('click', async (ev) => {
                  ev.stopPropagation();
                  const fp = resolveItemPath(it);
                  try {
                    const r = await window.electronAPI.showSaveDialog({
                      defaultPath: fileName, sourcePath: fp,
                      filters: [{ name: ext.toUpperCase(), extensions: [ext] }],
                    });
                    if (r && r.ok) addLiveLog && addLiveLog('system', `다운로드 완료: ${r.path}`);
                  } catch (e) { console.error('[gallery] download failed', e); }
                });
              }

              // 호버 시 우상단 다운로드 버튼 표시 (사용자 query 2 — 채팅 즉시 다운로드)
              const dlBtn = document.createElement('button');
              dlBtn.type = 'button';
              dlBtn.title = '다운로드';
              dlBtn.textContent = '↓';
              dlBtn.className = 'tit-download';
              dlBtn.style.cssText = 'position:absolute;top:6px;right:6px;width:24px;height:24px;border-radius:50%;border:1px solid var(--color-border,#3c3c3c);background:rgba(0,0,0,0.6);color:#fff;font-size:13px;line-height:1;cursor:pointer;display:none;align-items:center;justify-content:center;backdrop-filter:blur(4px);';
              const frameForBtn = thumb.querySelector('.tit-frame');
              if (frameForBtn) {
                frameForBtn.style.position = 'relative';
                frameForBtn.appendChild(dlBtn);
              }
              thumb.addEventListener('mouseenter', () => { dlBtn.style.display = 'flex'; });
              thumb.addEventListener('mouseleave', () => { dlBtn.style.display = 'none'; });
              dlBtn.addEventListener('click', async (ev) => {
                ev.stopPropagation();
                ev.preventDefault();
                const fp = resolveItemPath(it);
                try {
                  const r = await window.electronAPI.showSaveDialog({
                    defaultPath: fileName,
                    sourcePath: fp,
                    filters: [{ name: ext.toUpperCase(), extensions: [ext] }],
                  });
                  if (r && r.ok && typeof addLiveLog === 'function') addLiveLog('system', `다운로드 완료: ${r.path}`);
                } catch (err) {
                  console.error('[tool-image-thumb] download failed', err);
                }
              });

              gallery.appendChild(thumb);
            }

            if (overflow > 0) {
              const more = document.createElement('a');
              more.href = '#';
              more.className = 'tool-images-more';
              more.textContent = `+${overflow}개 더보기`;
              more.style.cssText = 'align-self:center;padding:8px 12px;color:var(--color-accent,#007acc);font-size:12px;text-decoration:none;border:1px dashed var(--color-border,#3c3c3c);border-radius:4px;cursor:pointer;';
              more.addEventListener('click', (ev) => {
                ev.preventDefault();
                ev.stopPropagation();
                // 12.x에서 file-preview-panel과 연동할 예정 — 현재는 첫 초과 항목으로 점프
                const first = imgItems[MAX_DISPLAY];
                if (first && typeof openMediaPreview === 'function') {
                  openMediaPreview(resolveItemPath(first), first.path.split('/').pop());
                }
              });
              gallery.appendChild(more);
            }

            toolLine.appendChild(gallery);
          }

          // ── 비-이미지 (PDF/PPTX) 단일 카드 ─────────────────────────
          for (const it of docItems) {
            const ext = (it.path.split('.').pop() || '').toLowerCase();
            const fileName = it.path.split('/').pop();
            const sizeKb = it.sizeBytes ? `${(it.sizeBytes/1024).toFixed(1)} KB` : '';
            const pages = it.pageCount ? ` · ${it.pageCount}페이지` : (it.slideCount ? ` · ${it.slideCount}슬라이드` : '');
            const card = document.createElement('div');
            card.className = 'tool-media-card';
            card.style.cssText = 'margin:6px 0 10px 24px;padding:10px;background:var(--color-bg-tertiary,#2d2d30);border:1px solid var(--color-border,#3c3c3c);border-radius:6px;display:flex;gap:12px;align-items:flex-start;';
            card.innerHTML = `
              <div class="tmc-thumb" style="flex-shrink:0;width:96px;height:96px;background:var(--color-bg-primary,#1e1e1e);border-radius:4px;display:flex;align-items:center;justify-content:center;overflow:hidden;cursor:pointer;">
                <span style="font-size:13px;font-weight:700;color:var(--color-text-muted,#6a6a6a);text-transform:uppercase;letter-spacing:0.5px;">${ext.toUpperCase()}</span>
              </div>
              <div style="flex:1;min-width:0;">
                <div style="font-weight:600;font-size:13px;margin-bottom:4px;color:var(--color-text-primary,#ccc);overflow:hidden;text-overflow:ellipsis;white-space:nowrap;" title="${esc(fileName)}">${esc(fileName)}</div>
                <div style="font-size:11px;color:var(--color-text-secondary,#9d9d9d);margin-bottom:8px;">${it.model ? esc(it.model) + ' · ' : ''}${sizeKb}${pages}</div>
                <div style="display:flex;gap:6px;">
                  <button class="tmc-preview" type="button" style="background:var(--color-accent,#007acc);color:#fff;border:none;padding:5px 12px;border-radius:3px;font-size:11px;cursor:pointer;">미리보기</button>
                  <button class="tmc-download" type="button" style="background:transparent;color:var(--color-text-secondary,#9d9d9d);border:1px solid var(--color-border,#3c3c3c);padding:5px 12px;border-radius:3px;font-size:11px;cursor:pointer;">다운로드</button>
                </div>
              </div>
            `;
            const open = (ev) => {
              ev.stopPropagation();
              const fp = resolveItemPath(it);
              if (typeof openMediaPreview === 'function') openMediaPreview(fp, fileName);
              document.dispatchEvent(new CustomEvent('preview-panel:select', {
                detail: { path: it.path, fullPath: fp, name: fileName },
                bubbles: true,
              }));
            };
            card.querySelector('.tmc-preview').addEventListener('click', open);
            card.querySelector('.tmc-thumb').addEventListener('click', open);
            card.querySelector('.tmc-download').addEventListener('click', async (ev) => {
              ev.stopPropagation();
              const fp = resolveItemPath(it);
              try {
                const r = await window.electronAPI.showSaveDialog({
                  defaultPath: fileName,
                  sourcePath: fp,
                  filters: [{ name: ext.toUpperCase(), extensions: [ext] }],
                });
                if (r && r.ok) addLiveLog && addLiveLog('system', `다운로드 완료: ${r.path}`);
              } catch (e) {
                console.error('[tool-media-card] download failed', e);
              }
            });
            toolLine.appendChild(card);
          }
        }
      } catch { /* not JSON, skip */ }
    }

    body.appendChild(toolLine);
  });

  details.appendChild(body);

  // 펼칠 때만 타이머 활성화
  details.addEventListener('toggle', () => {
    if (details.open && hasRunning) _ensureStepTimer();
  });

  c.appendChild(details);
  if (hasRunning) _ensureStepTimer();
}

function renderToolUseCard(c, t) {
  const card = document.createElement('div');
  const isRunning = t.status === 'running' || (!t.endedAt && t.startedAt);
  card.className = `tool-use-card ${isRunning ? 'tool-streaming' : ''} ${state.isStreaming ? '' : 'fade-in'}`;
  const status = t.status || (t.endedAt ? 'done' : (t.startedAt ? 'running' : ''));
  let timerHtml = '';
  if (t.startedAt) {
    card.setAttribute('data-step-started', String(t.startedAt));
    if (t.endedAt) {
      card.setAttribute('data-step-ended', String(t.endedAt));
      timerHtml = `<span class="step-timer" data-step-timer style="margin-left:8px;font-size:10px;color:var(--color-text-muted);font-variant-numeric:tabular-nums">${fmtElapsedMs(t.endedAt - t.startedAt)}</span>`;
    } else {
      timerHtml = `<span class="step-timer step-timer-running" data-step-timer style="margin-left:8px;font-size:10px;color:var(--color-success);font-variant-numeric:tabular-nums">${fmtElapsedMs(Date.now() - t.startedAt)}</span>`;
    }
  }
  const statusBadge = status === 'running' ? '<span class="step-badge step-badge-running" style="margin-left:6px">실행 중</span>'
    : status === 'done' ? '<span class="step-badge step-badge-done" style="margin-left:6px">완료</span>'
    : status === 'failed' ? '<span class="step-badge step-badge-failed" style="margin-left:6px">실패</span>' : '';
  const label = t.path ? `파일: ${esc(t.path)}` : esc(t.name || '도구');
  const bodyTxt = t.content || t.diff || (t.output ? String(t.output) : '') || '';
  // 펼침 화살표 — 기본 접힘, 클릭 시 토글
  const chevron = bodyTxt ? '<span class="tool-chevron" style="margin-right:6px;font-size:10px;color:var(--color-text-muted);transition:transform 150ms ease;display:inline-block">▶</span>' : '';
  card.innerHTML = `<div class="tool-use-header" style="cursor:${bodyTxt ? 'pointer' : 'default'}">${chevron}<span class="tool-badge">${esc(t.name || '도구')}</span><span class="tool-label">${label}</span>${statusBadge}${timerHtml}</div>${bodyTxt ? `<div class="tool-use-body" style="display:none">${esc(bodyTxt)}</div>` : ''}`;
  // 헤더 클릭 → body 토글
  if (bodyTxt) {
    const header = card.querySelector('.tool-use-header');
    header.addEventListener('click', () => {
      const body = card.querySelector('.tool-use-body');
      const chev = card.querySelector('.tool-chevron');
      if (!body) return;
      const isOpen = body.style.display !== 'none';
      body.style.display = isOpen ? 'none' : '';
      if (chev) chev.style.transform = isOpen ? '' : 'rotate(90deg)';
    });
  }
  c.appendChild(card);
  if (status === 'running') _ensureStepTimer();
}
// ===== moved to src/lib/utils.js (Phase 1 refactor) =====
//   fmtMd(t)         — 마크다운 → HTML
//   fmtElapsed(secs) — 경과 시간 (초 단위)
//   fmtElapsedMs(ms) — 경과 시간 (ms 단위)
// 이 함수들은 lib/utils.js에서 전역으로 정의됩니다.












































// 전역 step 타이머 — 1초마다 running step/tool의 [data-step-timer] 요소 갱신
let _stepTimerInterval = null;
function _ensureStepTimer() {
  if (_stepTimerInterval) return;
  _stepTimerInterval = setInterval(() => {
    const nodes = document.querySelectorAll('[data-step-started]');
    let hasRunning = false;
    const now = Date.now();
    nodes.forEach(n => {
      if (n.getAttribute('data-step-ended')) return; // 종료된 노드는 스킵
      const started = parseInt(n.getAttribute('data-step-started'), 10);
      if (!started) return;
      hasRunning = true;
      const timerEl = n.querySelector('[data-step-timer]');
      if (timerEl) timerEl.textContent = fmtElapsedMs(now - started);
    });
    if (!hasRunning && !state.isStreaming) {
      clearInterval(_stepTimerInterval);
      _stepTimerInterval = null;
    }
  }, 1000);
}
// esc(t): moved to src/lib/utils.js (Phase 1 refactor)

// ===== File Explorer — 인라인 생성/수정/삭제 =====
function initFileExplorer() {
  document.getElementById('btn-open-folder').onclick = async () => {
    // When remote is connected, show a simple path input dialog (like Kiro IDE)
    const remote = (typeof window !== 'undefined' && window.__remoteStatus) || null;
    console.log('[Open Folder] clicked, remote status:', remote && remote.state, remote && remote.alias);
    if (remote && remote.state === 'connected' && remote.alias) {
      const aliasLabel = remote.user ? `${remote.user}@${remote.alias}` : remote.alias;
      const currentPath = state.folderPath || remote.remoteHome || '/';
      
      // Simple path input dialog — no complex picker, just type the path
      const newPath = prompt(`[${aliasLabel}] 원격 폴더 경로를 입력하세요:`, currentPath);
      if (!newPath || newPath === currentPath) return;
      
      state.folderPath = newPath;
      const pathText = document.getElementById('file-tree-path-text');
      if (pathText) {
        pathText.textContent = `[SSH: ${aliasLabel}] ${newPath}`;
        pathText.title = `Remote host: ${aliasLabel} — ${newPath}`;
      }
      document.getElementById('file-tree-actions').style.display = 'inline-flex';
      _projectStats = null; _projectDeps = null; _gitLog = []; _reviewResults = null; _structureCurrentPath = null;
      try { await loadFileTree(newPath); } catch (e) {
        alert(`폴더 열기 실패: ${e.message || e}`);
      }
      // Save workspace preference
      try {
        if (window.electronAPI?.remoteSetWorkspace) {
          window.electronAPI.remoteSetWorkspace({ alias: remote.alias, remotePath: newPath });
        }
      } catch {}
      return;
    }
    if (window.electronAPI?.openFolder) {
      const p = await window.electronAPI.openFolder();
      if (p) {
        state.folderPath = p;
        document.getElementById('file-tree-path-text').textContent = p;
        document.getElementById('file-tree-actions').style.display = 'inline-flex';
        _projectStats = null; _projectDeps = null; _gitLog = []; _reviewResults = null; _structureCurrentPath = null;
        loadFileTree(p);
        loadCommitLogMini(p);
        indexProjectForRAG(p);
      }
    }
  };
  document.getElementById('ft-new-file')?.addEventListener('click', () => startInlineCreate(state.folderPath, 'file', 0));
  document.getElementById('ft-new-folder')?.addEventListener('click', () => startInlineCreate(state.folderPath, 'folder', 0));
  // 경로 텍스트 클릭으로도 폴더 변경
  document.getElementById('file-tree-path-text')?.addEventListener('click', () => {
    document.getElementById('btn-open-folder')?.click();
  });
  // Remote path bar: Enter로 임의 경로 이동 (VS Code Open Folder 대체)
  const remotePathInput = document.getElementById('remote-path-input');
  if (remotePathInput) {
    remotePathInput.addEventListener('keydown', async (e) => {
      if (e.key === 'Enter') {
        const newPath = remotePathInput.value.trim();
        if (!newPath) return;
        const remote = (typeof window !== 'undefined' && window.__remoteStatus) || null;
        state.folderPath = newPath;
        _projectStats = null; _projectDeps = null; _reviewResults = null; _structureCurrentPath = null;
        const pathText = document.getElementById('file-tree-path-text');
        if (pathText) {
          const aliasLabel = (remote && remote.user) ? `${remote.user}@${remote.alias}` : (remote && remote.alias || '');
          pathText.textContent = remote ? `[SSH: ${aliasLabel}] ${newPath}` : newPath;
        }
        document.getElementById('file-tree-actions').style.display = 'inline-flex';
        try { await loadFileTree(newPath); } catch (err) {
          alert(`경로 열기 실패: ${err.message || err}`);
        }
        if (remote && remote.alias && window.electronAPI?.remoteSetWorkspace) {
          window.electronAPI.remoteSetWorkspace({ alias: remote.alias, remotePath: newPath });
        }
        e.preventDefault();
      }
    });
  }
  // 드롭 영역 클릭으로도 폴더 열기
  document.getElementById('file-tree-drop-area')?.addEventListener('click', () => {
    document.getElementById('btn-open-folder')?.click();
  });
  document.addEventListener('click', () => { document.getElementById('file-context-menu').style.display = 'none'; });
}

// 인라인 생성: 파일 트리 내에서 직접 입력
function startInlineCreate(parentDir, type, depth) {
  if (!parentDir || !state.folderPath) return;
  // 해당 폴더를 펼침
  if (!expandedDirs.has(parentDir) && parentDir !== state.folderPath) {
    expandedDirs.add(parentDir);
  }
  // 트리 다시 그린 후 인라인 입력 삽입
  loadFileTree(state.folderPath).then(() => {
    insertInlineInput(parentDir, type, depth);
  });
}

function insertInlineInput(parentDir, type, depth) {
  const tree = document.getElementById('file-tree');
  if (!tree) return;
  // parentDir에 해당하는 폴더 아이템 찾기
  let insertAfter = null;
  const items = tree.querySelectorAll('.file-tree-item');
  // 루트면 맨 위에
  if (parentDir === state.folderPath) {
    insertAfter = null; // 맨 앞
  } else {
    for (const item of items) {
      if (item.dataset && item.dataset.entryPath === parentDir) {
        insertAfter = item;
        break;
      }
    }
  }

  // 인라인 입력 행 생성
  const row = document.createElement('div');
  row.className = 'file-tree-item file-tree-inline-input';
  const indent = parentDir === state.folderPath ? 8 : 8 + (getDepthForPath(parentDir) + 1) * 16;
  row.style.paddingLeft = indent + 'px';
  row.innerHTML = `
    <span class="icon" style="color:var(--color-accent)">${type === 'file' ? '+' : '▸'}</span>
    <input type="text" class="ft-inline-edit" placeholder="${type === 'file' ? '파일명.확장자' : '폴더명'}" autofocus>
    <span class="ft-inline-msg" style="display:none;font-size:10px;color:var(--color-error);margin-left:4px"></span>
  `;
  const input = row.querySelector('input');
  const msg = row.querySelector('.ft-inline-msg');
  let _committing = false;

  const commit = async () => {
    if (_committing) return;
    _committing = true;
    const name = input.value.trim();
    if (!name) { row.remove(); return; }
    const fullPath = parentDir + '/' + name;
    // 중복 체크 — 같은 타입만 체크 (파일과 폴더는 같은 이름 공존 가능)
    try {
      const existing = await window.electronAPI?.readDir(parentDir);
      if (existing) {
        const duplicate = existing.find(e => e.name === name && ((type === 'folder' && e.isDirectory) || (type === 'file' && !e.isDirectory)));
        if (duplicate) {
          msg.style.display = '';
          msg.textContent = `같은 이름의 ${type === 'file' ? '파일' : '폴더'}이 이미 존재합니다`;
          input.style.borderColor = 'var(--color-error)';
          _committing = false;
          input.focus();
          return;
        }
      }
    } catch {}
    // 유효성 체크
    if (/[<>:"|?*\\]/.test(name)) {
      msg.style.display = '';
      msg.textContent = '사용할 수 없는 문자';
      input.style.borderColor = 'var(--color-error)';
      _committing = false;
      input.focus();
      return;
    }
    try {
      let result;
      if (type === 'file') {
        result = await window.electronAPI?.writeFile(fullPath, '');
      } else {
        result = await window.electronAPI?.mkdir(fullPath);
      }
      if (result === false) {
        msg.style.display = '';
        msg.textContent = '생성 실패';
        _committing = false;
        return;
      }
      row.remove();
      await loadFileTree(state.folderPath);
      if (type === 'file') openFileInEditor(fullPath, name);
    } catch (e) {
      msg.style.display = '';
      msg.textContent = e.message || '오류';
      _committing = false;
    }
  };

  const cancel = () => { if (!_committing) row.remove(); };

  input.addEventListener('keydown', e => {
    if (e.key === 'Enter') { e.preventDefault(); commit(); }
    if (e.key === 'Escape') { e.preventDefault(); cancel(); }
  });
  // blur 시에는 commit (포커스 잃으면 확정)
  input.addEventListener('blur', () => {
    setTimeout(() => {
      if (!_committing && input.value.trim()) commit();
      else if (!_committing) cancel();
    }, 300);
  });
  // 입력 시 에러 메시지 초기화
  input.addEventListener('input', () => { msg.style.display = 'none'; input.style.borderColor = 'var(--color-accent)'; });

  // 삽입 위치
  if (insertAfter) {
    insertAfter.parentNode.insertBefore(row, insertAfter.nextSibling);
  } else {
    tree.insertBefore(row, tree.firstChild);
  }
  setTimeout(() => input.focus(), 30);
}

function getDepthForPath(dirPath) {
  if (!state.folderPath || dirPath === state.folderPath) return 0;
  const rel = dirPath.replace(state.folderPath, '');
  return (rel.match(/\//g) || []).length;
}

// 인라인 이름 변경
function startInlineRename(entry) {
  const tree = document.getElementById('file-tree');
  const items = tree.querySelectorAll('.file-tree-item');
  for (const item of items) {
    if (item.dataset.entryPath === entry.path) {
      const nameSpan = item.querySelector('.name');
      if (!nameSpan) return;
      const oldName = entry.name;
      const input = document.createElement('input');
      input.type = 'text';
      input.className = 'ft-inline-edit';
      input.value = oldName;
      input.style.width = '100%';
      nameSpan.innerHTML = '';
      nameSpan.appendChild(input);

      const msgEl = document.createElement('span');
      msgEl.style.cssText = 'display:none;font-size:10px;color:var(--color-error);margin-left:4px';
      nameSpan.appendChild(msgEl);

      let _renaming = false;
      const commit = async () => {
        if (_renaming) return;
        _renaming = true;
        const newName = input.value.trim();
        if (!newName || newName === oldName) { await loadFileTree(state.folderPath); return; }
        if (/[<>:"|?*\\]/.test(newName)) {
          msgEl.style.display = ''; msgEl.textContent = '사용할 수 없는 문자'; _renaming = false; return;
        }
        const dir = entry.path.substring(0, entry.path.lastIndexOf('/'));
        // 중복 체크 — 같은 타입만
        try {
          const existing = await window.electronAPI?.readDir(dir);
          if (existing) {
            const duplicate = existing.find(e => e.name === newName && e.isDirectory === entry.isDirectory);
            if (duplicate) {
              msgEl.style.display = ''; msgEl.textContent = '같은 이름이 이미 존재합니다'; _renaming = false; return;
            }
          }
        } catch {}
        const newPath = dir + '/' + newName;
        try {
          const result = await window.electronAPI?.rename(entry.path, newPath);
          if (result === false) { msgEl.style.display = ''; msgEl.textContent = '변경 실패'; _renaming = false; return; }
          await loadFileTree(state.folderPath);
        } catch (e) { msgEl.style.display = ''; msgEl.textContent = e.message || '오류'; _renaming = false; }
      };

      input.addEventListener('keydown', e => {
        if (e.key === 'Enter') { e.preventDefault(); commit(); }
        if (e.key === 'Escape') { e.preventDefault(); loadFileTree(state.folderPath); }
      });
      input.addEventListener('blur', () => {
        setTimeout(() => {
          if (!_renaming && input.value.trim() && input.value.trim() !== oldName) commit();
          else if (!_renaming) loadFileTree(state.folderPath);
        }, 300);
      });
      setTimeout(() => { input.focus(); input.select(); }, 30);
      return;
    }
  }
}

// 인라인 삭제 (확인 포함)
async function deleteEntry(entry) {
  // 파일 트리 아이템에 확인 UI 표시
  const tree = document.getElementById('file-tree');
  const items = tree.querySelectorAll('.file-tree-item');
  for (const item of items) {
    if (item.dataset.entryPath === entry.path) {
      const original = item.innerHTML;
      item.innerHTML = `
        <span style="font-size:11px;color:var(--color-error);flex:1">"${esc(entry.name)}" 삭제?</span>
        <button class="ft-action-btn" id="del-confirm" style="color:var(--color-error);font-weight:600">삭제</button>
        <button class="ft-action-btn" id="del-cancel">취소</button>
      `;
      item.querySelector('#del-confirm').addEventListener('click', async (ev) => {
        ev.stopPropagation();
        // 간단한 삭제 — 터미널로 rm 실행
        if (state.terminals.length && window.electronAPI?.terminalWrite) {
          const tid = state.terminals[state.activeTerminalIdx]?.id;
          if (tid) {
            const cmd = entry.isDirectory ? `rm -rf "${entry.path}"` : `rm "${entry.path}"`;
            await window.electronAPI.terminalWrite(tid, cmd + '\n');
            setTimeout(() => loadFileTree(state.folderPath), 500);
          }
        }
      });
      item.querySelector('#del-cancel').addEventListener('click', (ev) => {
        ev.stopPropagation();
        loadFileTree(state.folderPath);
      });
      return;
    }
  }
}

function showFileContextMenu(e, entry) {
  e.preventDefault(); e.stopPropagation();
  const menu = document.getElementById('file-context-menu');
  menu.style.display = 'block';
  menu.style.left = e.clientX + 'px';
  menu.style.top = Math.min(e.clientY, window.innerHeight - 200) + 'px';
  menu.onclick = ev => ev.stopPropagation();
  const parentDir = entry.isDirectory ? entry.path : entry.path.substring(0, entry.path.lastIndexOf('/'));
  menu.innerHTML = `
    ${!entry.isDirectory ? '<div class="context-menu-item" data-action="open">열기</div>' : ''}
    ${entry.isDirectory ? '<div class="context-menu-item" data-action="open-as-root" style="font-weight:600">이 폴더를 프로젝트로 열기</div>' : ''}
    <div class="context-menu-item" data-action="rename">이름 변경</div>
    <div class="context-menu-item" data-action="delete" style="color:var(--color-error)">삭제</div>
    <div class="context-menu-sep"></div>
    <div class="context-menu-item" data-action="new-file">새 파일</div>
    <div class="context-menu-item" data-action="new-folder">새 폴더</div>
  `;
  menu.querySelectorAll('.context-menu-item').forEach(item => {
    item.onclick = ev => {
      ev.stopPropagation();
      menu.style.display = 'none';
      const action = item.dataset.action;
      if (action === 'open') openFileInEditor(entry.path, entry.name);
      else if (action === 'open-as-root') {
        // Open this folder as the project root (like VS Code "Open Folder")
        state.folderPath = entry.path;
        _projectStats = null; _projectDeps = null; _reviewResults = null; _structureCurrentPath = null;
        const remote = (typeof window !== 'undefined' && window.__remoteStatus) || null;
        const pathText = document.getElementById('file-tree-path-text');
        if (pathText) {
          if (remote && remote.state === 'connected') {
            const aliasLabel = remote.user ? `${remote.user}@${remote.alias}` : remote.alias;
            pathText.textContent = `[SSH: ${aliasLabel}] ${entry.path}`;
            pathText.title = `Remote host: ${aliasLabel} — ${entry.path}`;
          } else {
            pathText.textContent = entry.path;
          }
        }
        document.getElementById('file-tree-actions').style.display = 'inline-flex';
        loadFileTree(entry.path);
        // Save workspace preference for remote
        if (remote && remote.alias && window.electronAPI?.remoteSetWorkspace) {
          window.electronAPI.remoteSetWorkspace({ alias: remote.alias, remotePath: entry.path });
        }
      }
      else if (action === 'rename') startInlineRename(entry);
      else if (action === 'delete') deleteEntry(entry);
      else if (action === 'new-file') startInlineCreate(parentDir, 'file', 0);
      else if (action === 'new-folder') startInlineCreate(parentDir, 'folder', 0);
    };
  });
}

const expandedDirs = new Set();
async function loadFileTree(dp) { if (!window.electronAPI?.readDir) return; const entries = await window.electronAPI.readDir(dp); const tree = document.getElementById('file-tree'); tree.innerHTML = ''; renderTreeEntries(tree, entries, 0, dp); }
function renderTreeEntries(c, entries, depth, parentPath) {
  const sorted = [...entries].sort((a, b) => (b.isDirectory - a.isDirectory) || a.name.localeCompare(b.name));
  for (const e of sorted) {
    if (e.name.startsWith('.') && e.name !== '.kiro') continue;
    if (['node_modules', '__pycache__', '.git', '.venv', 'dist', 'build'].includes(e.name)) continue;
    const item = document.createElement('div'); item.className = 'file-tree-item'; item.style.paddingLeft = `${8 + depth * 16}px`;
    item.dataset.entryPath = e.path;
    item.addEventListener('contextmenu', ev => showFileContextMenu(ev, e));
    if (e.isDirectory) {
      const exp = expandedDirs.has(e.path);
      item.innerHTML = `<span class="icon" style="color:var(--color-accent)">${exp ? '▾' : '▸'}</span><span class="name" style="flex:1;font-weight:500">${e.name}</span>
        <span class="ft-inline-actions"><button class="ft-action-btn" data-act="nf" title="새 파일">+파일</button><button class="ft-action-btn" data-act="nd" title="새 폴더">+폴더</button></span>`;
      item.querySelector('[data-act="nf"]')?.addEventListener('click', ev => { ev.stopPropagation(); startInlineCreate(e.path, 'file', depth + 1); });
      item.querySelector('[data-act="nd"]')?.addEventListener('click', ev => { ev.stopPropagation(); startInlineCreate(e.path, 'folder', depth + 1); });
      item.onclick = ev => { if (ev.target.closest('.ft-action-btn')) return; if (expandedDirs.has(e.path)) expandedDirs.delete(e.path); else expandedDirs.add(e.path); loadFileTree(state.folderPath); };
    } else {
      const ext = e.name.split('.').pop().toLowerCase();
      const extColors = { js:'#f1e05a', ts:'#3178c6', py:'#3572a5', html:'#e34c26', css:'#563d7c', json:'#999', md:'#083fa1', yml:'#cb171e', yaml:'#cb171e', sh:'#89e051', txt:'#aaa' };
      const dotColor = extColors[ext] || 'var(--color-text-muted)';
      item.innerHTML = `<span class="icon" style="color:${dotColor}">●</span><span class="name">${e.name}</span>`;
      item.onclick = () => openFileInEditor(e.path, e.name);
    }
    c.appendChild(item);
    if (e.isDirectory && expandedDirs.has(e.path)) {
      const cc = document.createElement('div'); c.appendChild(cc);
      (async () => { const ch = await window.electronAPI.readDir(e.path); renderTreeEntries(cc, ch, depth + 1, e.path); })();
    }
  }
}

// ===== Monaco =====
let monacoEditor = null;
let _fileModified = false;

function initMonaco() {
  if (typeof require === 'undefined') { setTimeout(initMonaco, 200); return; }
  require.config({ paths: { vs: 'https://cdn.jsdelivr.net/npm/monaco-editor@0.50.0/min/vs' } });
  require(['vs/editor/editor.main'], monaco => {
    window.monaco = monaco;
    console.log('[Monaco] 에디터 초기화 시작');
    monacoEditor = monaco.editor.create(document.getElementById('editor-content'), {
      value: '// Mogam Works\n// 파일이나 폴더를 열어 시작하세요.\n',
      language: 'javascript',
      theme: 'vs-dark',
      automaticLayout: true,
      fontSize: 13,
      fontFamily: "'Cascadia Code','Fira Code','JetBrains Mono',monospace",
      minimap: { enabled: true },
      scrollBeyondLastLine: false,
      padding: { top: 8 },
    });
    console.log('[Monaco] 에디터 초기화 완료, monacoEditor:', !!monacoEditor);
    monacoEditor.onDidChangeCursorPosition(e => {
      const el = document.getElementById('status-cursor');
      if (el) el.textContent = `Ln ${e.position.lineNumber}, Col ${e.position.column}`;
    });
  }, (err) => {
    console.error('[Monaco] 로드 실패:', err);
  });
}

// Paths that should be opened in a read-only editor tab (e.g. "Show Remote Log").
// Tracked separately from openTabs because Monaco's `readOnly` is an editor
// option rather than a per-model flag — we reapply it whenever the active tab
// switches (see openFileInEditor).
const _readOnlyTabs = new Set();

async function openFileInEditor(fp, fn) {
  console.log('[openFile] 호출됨:', fp, 'monacoEditor:', !!monacoEditor);
  if (!monacoEditor) {
    console.warn('[openFile] monacoEditor가 null — 500ms 후 재시도');
    setTimeout(() => openFileInEditor(fp, fn), 500);
    return;
  }
  let content = null;
  try {
    content = window.electronAPI?.readFile ? await window.electronAPI.readFile(fp) : null;
  } catch (err) {
    console.error('[openFile] readFile 에러:', err);
    return;
  }
  console.log('[openFile] content 길이:', content === null ? 'null' : content.length);
  if (content === null) return;
  const fileName = fn || fp.split('/').pop();
  if (!state.openTabs.find(t => t.path === fp)) state.openTabs.push({ path: fp, name: fileName });
  state.activeTab = fp;
  renderEditorTabs();
  // 에디터 뷰로 전환
  document.getElementById('editor-area').style.display = 'flex';
  document.getElementById('parallel-results').classList.remove('visible');
  ['structure','dependencies','stats','search','git','review','consensus'].forEach(v => {
    const el = document.getElementById('view-' + v);
    if (el) el.style.display = 'none';
  });
  document.querySelectorAll('.cv-tab').forEach(t => t.classList.toggle('active', t.dataset.view === 'editor'));
  if (typeof _activeView !== 'undefined') _activeView = 'editor';
  // 언어 감지
  const ext = fp.split('.').pop().toLowerCase();
  const lm = { js:'javascript', ts:'typescript', jsx:'javascript', tsx:'typescript', py:'python', json:'json', html:'html', css:'css', scss:'scss', md:'markdown', yml:'yaml', yaml:'yaml', sh:'shell', txt:'plaintext', xml:'xml' };
  // 상태바
  const fi = document.getElementById('status-file-info');
  if (fi) fi.textContent = fileName;
  // Monaco에 내용 설정
  try {
    const oldModel = monacoEditor.getModel();
    const model = window.monaco.editor.createModel(content, lm[ext] || 'plaintext');
    monacoEditor.setModel(model);
    if (oldModel && oldModel !== model) { try { oldModel.dispose(); } catch {} }
    console.log('[openFile] 모델 설정 완료');
  } catch (e) {
    console.error('[openFile] createModel 에러:', e, '— fallback으로 setValue 시도');
    try {
      const m = monacoEditor.getModel();
      if (m) { m.setValue(content); }
    } catch (e2) {
      console.error('[openFile] setValue도 실패:', e2);
    }
  }
  // 읽기 전용 탭이면 에디터 옵션을 readOnly 로 설정 (Req 12.3)
  try { monacoEditor.updateOptions({ readOnly: _readOnlyTabs.has(fp) }); } catch {}
}

/**
 * Open a file in a read-only editor tab. Used by the "Show Remote Log"
 * command (Req 12.3) so the user cannot accidentally edit the log file.
 *
 * @param {string} fp absolute file path
 * @param {string} [fn] display name
 */
async function openFileReadOnly(fp, fn) {
  if (!fp) return;
  _readOnlyTabs.add(fp);
  await openFileInEditor(fp, fn);
}

function renderEditorTabs() {
  const tabs = document.getElementById('editor-tabs');
  tabs.innerHTML = state.openTabs.map(t =>
    `<div class="editor-tab ${t.path === state.activeTab ? 'active' : ''}" data-path="${t.path}">${esc(t.name)}<span class="close" data-close="${t.path}">×</span></div>`
  ).join('');
  tabs.querySelectorAll('.editor-tab').forEach(el => {
    el.onclick = e => {
      if (e.target.classList.contains('close')) {
        const p = e.target.dataset.close;
        state.openTabs = state.openTabs.filter(t => t.path !== p);
        _readOnlyTabs.delete(p);
        if (state.activeTab === p) {
          state.activeTab = state.openTabs.length ? state.openTabs[state.openTabs.length - 1].path : null;
          if (state.activeTab) openFileInEditor(state.activeTab);
        }
        renderEditorTabs();
      } else {
        openFileInEditor(el.dataset.path);
      }
    };
  });
}

// ===== Terminal — 리사이즈 + 입출력 통합 + 새 터미널 추가 =====
function initTerminal() {
  addTerminal();
  document.getElementById('btn-terminal-toggle')?.addEventListener('click', () => {
    document.getElementById('terminal-area').classList.toggle('collapsed');
  });
  // 터미널 영역 클릭 시 xterm에 포커스
  document.getElementById('terminal-content')?.addEventListener('mousedown', () => {
    setTimeout(() => {
      const ta = document.querySelector('.xterm-helper-textarea');
      if (ta) ta.focus();
    }, 10);
  });
  // 리사이즈 핸들
  const area = document.getElementById('terminal-area');
  const handle = document.getElementById('terminal-resize-handle');
  if (handle && area) {
    let startY, startH;
    handle.addEventListener('mousedown', e => {
      startY = e.clientY; startH = area.offsetHeight;
      const onMove = ev => { area.style.height = Math.max(60, startH - (ev.clientY - startY)) + 'px'; };
      const onUp = () => { document.removeEventListener('mousemove', onMove); document.removeEventListener('mouseup', onUp); };
      document.addEventListener('mousemove', onMove);
      document.addEventListener('mouseup', onUp);
    });
  }
}

function addTerminal() {
  const id = 'term-' + (state.terminals.length + 1);
  state.terminals.push({ id, output: '' });
  state.activeTerminalIdx = state.terminals.length - 1;
  if (window.electronAPI?.terminalCreate) {
    const remote = (typeof window !== 'undefined' && window.__remoteStatus) || null;
    const isRemote = remote && remote.state === 'connected';
    // PTY를 처음부터 올바른 cwd로 spawn — cd 명령 echo로 인한 깜빡임 제거
    const profile = state.settings?.awsProfile || '';
    const opts = {
      cwd: state.folderPath || undefined,
    };
    if (!isRemote && profile) {
      // 로컬 모드 — env로 AWS_PROFILE 주입 (export 명령 echo 없음)
      // process-manager는 env를 받아 spawn 시 적용
      opts.env = { AWS_PROFILE: profile };
    }
    window.electronAPI.terminalCreate(id, opts).then(() => {
      // PTY가 cwd로 시작했으므로 추가 명령 불필요 — 사용자에게 깨끗한 프롬프트만 표시됨
    });
  }
  renderTerminalTabs(); renderTerminalContent();
}

function renderTerminalTabs() {
  const bar = document.getElementById('terminal-tabs-bar'); if (!bar) return;
  const cwd = state.folderPath || '~';
  const profile = state.settings?.awsProfile || '';
  // Remote SSH: when a session is connected, show the alias/host in each tab
  // so the terminal window clearly indicates the target (Req 7.1, 12.1).
  const remote = (typeof window !== 'undefined' && window.__remoteStatus) || null;
  const isRemote = remote && remote.state === 'connected' && remote.alias;
  const remoteLabel = isRemote
    ? `${remote.user ? remote.user + '@' : ''}${remote.hostName || remote.alias}`
    : '';
  const tabPrefix = isRemote ? `🌐 ${remote.alias}` : '';
  bar.innerHTML = `<span style="font-size:11px;color:var(--color-text-muted);padding:0 8px;font-weight:600">${isRemote ? `터미널 · <span style="color:var(--color-success)">${esc(remoteLabel)}</span>` : '터미널'}</span>` +
    state.terminals.map((t, i) => {
      const tabLabel = isRemote
        ? `${tabPrefix}:${cwd.split('/').pop() || '~'}`
        : `${i+1}: ${cwd.split('/').pop() || '~'}`;
      return `<button class="terminal-tab ${i === state.activeTerminalIdx ? 'active' : ''}" data-idx="${i}" title="${esc(isRemote ? remoteLabel + ':' + cwd : cwd)}">
      ${tabLabel}${state.terminals.length > 1 ? `<span class="term-close" data-close="${i}" style="margin-left:6px;font-size:10px;opacity:0.4;cursor:pointer">✕</span>` : ''}
    </button>`;
    }).join('') +
    `<button class="terminal-tab" id="terminal-add-btn" title="새 터미널" style="color:var(--color-text-muted);font-size:14px">+</button>` +
    `<span style="flex:1"></span>` +
    `<span style="font-size:10px;color:var(--color-text-muted);padding:0 8px;font-family:var(--font-mono);overflow:hidden;text-overflow:ellipsis;white-space:nowrap;max-width:300px" title="${esc(cwd)}">${esc(cwd)}</span>`;
  bar.querySelectorAll('.terminal-tab[data-idx]').forEach(el => {
    el.addEventListener('click', e => {
      if (e.target.classList.contains('term-close')) {
        const idx = +e.target.dataset.close, t = state.terminals[idx];
        if (window.electronAPI?.terminalKill) window.electronAPI.terminalKill(t.id);
        state.terminals.splice(idx, 1);
        if (state.activeTerminalIdx >= state.terminals.length) state.activeTerminalIdx = Math.max(0, state.terminals.length - 1);
        if (!state.terminals.length) addTerminal(); else { renderTerminalTabs(); renderTerminalContent(); }
        return;
      }
      state.activeTerminalIdx = +el.dataset.idx; renderTerminalTabs(); renderTerminalContent();
    });
  });
  document.getElementById('terminal-add-btn')?.addEventListener('click', addTerminal);
}

async function renderTerminalContent() {
  const c = document.getElementById('terminal-content'); if (!c) return;
  const term = state.terminals[state.activeTerminalIdx]; if (!term) return;

  // xterm.js가 이미 초기화되어 있으면 표시만 전환
  if (term._xterm) {
    c.innerHTML = '';
    c.appendChild(term._xtermContainer);
    setTimeout(() => {
      try { term._fitAddon?.fit(); } catch {}
      const ta = term._xtermContainer?.querySelector('.xterm-helper-textarea');
      if (ta) ta.focus(); else term._xterm?.focus();
    }, 50);
    return;
  }

  // xterm.js 로드 확인
  if (!window.Terminal) {
    if (c._xtermLoading) return; // 중복 로드 방지
    c._xtermLoading = true;
    c.innerHTML = '<div style="padding:12px;color:var(--color-text-muted)">터미널 초기화 중...</div>';

    // AMD define 충돌 방지 (Monaco loader.js와 충돌)
    const _define = window.define;
    window.define = undefined;

    const loadScript = (src) => new Promise((res, rej) => {
      const s = document.createElement('script'); s.src = src;
      s.onload = res; s.onerror = rej; document.head.appendChild(s);
    });
    if (!document.querySelector('link[href*="xterm.css"]')) {
      const link = document.createElement('link'); link.rel = 'stylesheet'; link.href = 'vendor/xterm.css'; document.head.appendChild(link);
    }
    try {
      await loadScript('vendor/xterm.js');
      await loadScript('vendor/xterm-addon-fit.js');
    } catch (e) {
      console.error('[Terminal] xterm.js 로드 실패:', e);
      c.innerHTML = '<div style="padding:12px;color:var(--color-error)">xterm.js 로드 실패</div>';
      window.define = _define;
      return;
    }
    window.define = _define; // AMD define 복원
    c._xtermLoading = false;

    if (!window.Terminal) {
      c.innerHTML = '<div style="padding:12px;color:var(--color-error)">xterm.js Terminal 객체 없음</div>';
      return;
    }
  }

  // xterm.js 초기화
  const container = document.createElement('div');
  container.style.cssText = 'width:100%;height:100%;';
  c.innerHTML = '';
  c.appendChild(container);

  const xt = new window.Terminal({
    cursorBlink: true, cursorStyle: 'block', fontSize: 13,
    fontFamily: "'Cascadia Code', 'Fira Code', 'JetBrains Mono', monospace",
    theme: {
      background: '#1e1e1e', foreground: '#cccccc', cursor: '#ffffff', cursorAccent: '#1e1e1e',
      selectionBackground: 'rgba(255,255,255,0.2)',
      black: '#1e1e1e', red: '#f44747', green: '#4ec9b0', yellow: '#ce9178',
      blue: '#007acc', magenta: '#c586c0', cyan: '#4fc1ff', white: '#cccccc',
      brightBlack: '#6a6a6a', brightRed: '#f44747', brightGreen: '#4ec9b0',
      brightYellow: '#ce9178', brightBlue: '#1a8ad4', brightMagenta: '#c586c0',
      brightCyan: '#4fc1ff', brightWhite: '#ffffff',
    },
    scrollback: 5000, allowTransparency: true,
  });

  let fitAddon = null;
  if (window.FitAddon) {
    fitAddon = new window.FitAddon.FitAddon();
    xt.loadAddon(fitAddon);
  }

  xt.open(container);
  if (fitAddon) setTimeout(() => { try { fitAddon.fit(); } catch {} }, 100);
  // 포커스 — xterm-helper-textarea에 직접 포커스
  const focusXterm = () => {
    const ta = container.querySelector('.xterm-helper-textarea');
    if (ta) ta.focus();
    else xt.focus();
  };
  setTimeout(focusXterm, 300);
  container.addEventListener('click', focusXterm);

  // 입력을 PTY worker로 전달 (PTY가 echo 처리)
  xt.onData((data) => {
    if (window.electronAPI?.terminalWrite) window.electronAPI.terminalWrite(term.id, data);
  });

  // 기존 출력 복원
  if (term.output) xt.write(term.output);

  term._xterm = xt;
  term._fitAddon = fitAddon;
  term._xtermContainer = container;

  // 리사이즈 감지
  new ResizeObserver(() => { if (fitAddon) try { fitAddon.fit(); } catch {} }).observe(container);
}

function renderTerminalOutput() {
  // xterm.js 사용 시 별도 렌더 불필요 — xterm이 자체 관리
  const term = state.terminals[state.activeTerminalIdx];
  if (term?._xterm) return;
}

function appendTerminalOutput(text) {
  const term = state.terminals[state.activeTerminalIdx];
  if (term) {
    term.output += text;
    if (term._xterm) term._xterm.write(text);
  }
}

function setupTerminalIPC() {
  if (window.electronAPI?.onTerminalData) {
    window.electronAPI.onTerminalData(data => {
      const term = state.terminals.find(t => t.id === data.id);
      if (term) {
        if (term.output.length > 100000) term.output = term.output.slice(-50000);
        term.output += data.data;
        if (term._xterm) term._xterm.write(data.data);
      }
    });
  }
}
document.addEventListener('DOMContentLoaded', setupTerminalIPC);

// ===== Topbar =====
function initTopbar() {
  document.getElementById('btn-usage')?.addEventListener('click', showSessionUsagePopup);
  document.getElementById('btn-settings')?.addEventListener('click', showSettingsDialog);
  document.getElementById('btn-about')?.addEventListener('click', showAboutDialog);
  // 우측 패널 탭 전환
  document.querySelectorAll('.rp-tab').forEach(tab => {
    tab.addEventListener('click', () => {
      document.querySelectorAll('.rp-tab').forEach(t => t.classList.toggle('active', t === tab));
      const view = tab.dataset.rp;
      document.getElementById('rp-chat-view').style.display = view === 'chat' ? 'flex' : 'none';
      document.getElementById('rp-live-view').style.display = view === 'live' ? 'flex' : 'none';
      document.getElementById('rp-search-view').style.display = view === 'search-panel' ? 'flex' : 'none';
      if (view === 'live') updateLivePanel();
    });
  });
  document.getElementById('live-refresh-btn')?.addEventListener('click', updateLivePanel);
  // 소스 제어 탭 전환
  document.getElementById('btn-source-control')?.addEventListener('click', () => {
    document.querySelectorAll('.lp-btn').forEach(b => b.classList.remove('active'));
    document.getElementById('btn-source-control').classList.add('active');
    document.getElementById('file-tree').style.display = 'none';
    document.getElementById('source-control-panel').style.display = '';
    const gfpSc = document.getElementById('generated-files-panel'); if (gfpSc) gfpSc.style.display = 'none';
    document.querySelector('.skills-section').style.display = 'none';
    document.getElementById('file-tree-path').style.display = 'none';
    renderSourceControlPanel();
  });
  document.getElementById('btn-file-explorer')?.addEventListener('click', () => {
    document.querySelectorAll('.lp-btn').forEach(b => b.classList.remove('active'));
    document.getElementById('btn-file-explorer').classList.add('active');
    document.getElementById('file-tree').style.display = '';
    document.getElementById('source-control-panel').style.display = 'none';
    const rep = document.getElementById('remote-explorer-panel'); if (rep) rep.style.display = 'none';
    const gfp = document.getElementById('generated-files-panel'); if (gfp) gfp.style.display = 'none';
    document.querySelector('.skills-section').style.display = '';
    document.getElementById('file-tree-path').style.display = 'flex';
  });
  // Remote Explorer 탭 전환 (VS Code Remote Explorer 스타일)
  document.getElementById('btn-remote-explorer')?.addEventListener('click', () => {
    document.querySelectorAll('.lp-btn').forEach(b => b.classList.remove('active'));
    document.getElementById('btn-remote-explorer').classList.add('active');
    document.getElementById('file-tree').style.display = 'none';
    document.getElementById('source-control-panel').style.display = 'none';
    document.getElementById('remote-explorer-panel').style.display = '';
    const gfp = document.getElementById('generated-files-panel');
    if (gfp) gfp.style.display = 'none';
    document.querySelector('.skills-section').style.display = 'none';
    document.getElementById('file-tree-path').style.display = 'none';
    renderRemoteExplorer();
  });
  // Generated Files 탭 전환 (PPTX 템플릿 관리 + 생성 파일 미리보기 통합)
  document.getElementById('btn-generated-files')?.addEventListener('click', () => {
    document.querySelectorAll('.lp-btn').forEach(b => b.classList.remove('active'));
    document.getElementById('btn-generated-files').classList.add('active');
    document.getElementById('file-tree').style.display = 'none';
    document.getElementById('source-control-panel').style.display = 'none';
    document.getElementById('remote-explorer-panel').style.display = 'none';
    const gfp = document.getElementById('generated-files-panel');
    if (gfp) {
      gfp.style.display = 'flex';
      gfp.style.flexDirection = 'column';
      // 템플릿 패널 목록 갱신 (pptx-template-styling 요구사항 8.1)
      const tplPanel = document.getElementById('template-panel');
      if (tplPanel && typeof tplPanel._refresh === 'function') tplPanel._refresh();
      const panel = document.getElementById('file-preview-panel');
      if (panel) {
        // Always set workstation cwd so remote-mode falls back to local
        // .generated/ where the Python server actually saves files.
        if (window.__workstationCwd && !panel._workstationCwd) {
          panel._workstationCwd = window.__workstationCwd;
        }
        if (state.folderPath) {
          panel.setAttribute('project-path', state.folderPath);
        } else if (window.__workstationCwd) {
          panel.setAttribute('project-path', window.__workstationCwd);
        }
      }
    }
    document.querySelector('.skills-section').style.display = 'none';
    document.getElementById('file-tree-path').style.display = 'none';
  });
  // 생성 패널 내 템플릿 섹션 접기/펼치기 토글 (pptx-template-styling)
  document.getElementById('gen-template-toggle')?.addEventListener('click', () => {
    const section = document.getElementById('gen-template-section');
    if (!section) return;
    const collapsed = section.classList.toggle('collapsed');
    const toggle = document.getElementById('gen-template-toggle');
    if (toggle) toggle.setAttribute('aria-expanded', String(!collapsed));
  });
}
// ===== Remote Explorer (VS Code 스타일) =====
// 로컬 ~/.ssh/config에서 호스트 목록을 읽어 사이드바 트리로 표시.
// 호스트 항목: 클릭 시 연결 / 우클릭 시 컨텍스트 메뉴.
// 연결된 호스트: 하위에 최근 워크스페이스 표시.
async function renderRemoteExplorer() {
  const panel = document.getElementById('remote-explorer-panel');
  if (!panel) return;
  panel.innerHTML = `
    <div style="padding:10px 12px;font-size:10px;color:var(--color-text-muted);font-weight:600;text-transform:uppercase;letter-spacing:0.5px;display:flex;align-items:center;gap:6px;border-bottom:1px solid var(--color-border);">
      <span style="flex:1">Remote Explorer</span>
      <button class="ft-action-btn" id="re-refresh" title="새로고침" style="font-size:11px;padding:2px 6px;">↻</button>
      <button class="ft-action-btn" id="re-add-host" title="Ad-hoc 호스트 추가" style="font-size:11px;padding:2px 6px;">+</button>
    </div>
    <div id="re-content" style="padding:4px 0;font-size:12px;"><div style="padding:12px;color:var(--color-text-muted);font-size:11px;">불러오는 중...</div></div>
  `;
  panel.querySelector('#re-refresh')?.addEventListener('click', renderRemoteExplorer);
  panel.querySelector('#re-add-host')?.addEventListener('click', () => { if (window.RemoteAdHocDialog) window.RemoteAdHocDialog.show(); });

  let hosts = [];
  let statuses = {};
  try {
    const data = await window.electronAPI?.remoteListHosts?.();
    hosts = (data && data.entries) || [];
    const st = await window.electronAPI?.remoteStatus?.({});
    statuses = st || {};
    // Merge renderer-side status cache (more reliable — set by onRemoteState events)
    const cached = (typeof window !== 'undefined' && window.__remoteStatus) || null;
    if (cached && cached.alias && cached.state) {
      statuses[cached.alias] = { state: cached.state, localPort: cached.localPort || null };
    }
  } catch (e) {
    panel.querySelector('#re-content').innerHTML = `<div style="padding:12px;color:var(--color-error);font-size:11px;">로드 실패: ${e.message}</div>`;
    return;
  }

  const content = panel.querySelector('#re-content');
  if (!hosts.length) {
    content.innerHTML = `
      <div style="padding:20px 12px;text-align:center;color:var(--color-text-muted);font-size:11px;">
        <div style="margin-bottom:8px;">~/.ssh/config에 호스트가 없습니다.</div>
        <button class="ft-action-btn" id="re-add-empty" style="font-size:11px;padding:4px 10px;">+ Ad-hoc 호스트 추가</button>
      </div>`;
    content.querySelector('#re-add-empty')?.addEventListener('click', () => { if (window.RemoteAdHocDialog) window.RemoteAdHocDialog.show(); });
    return;
  }

  // 그룹: 즐겨찾기 vs 전체 (알파벳 정렬)
  const favorites = hosts.filter(h => h.favorite).sort((a, b) => a.alias.localeCompare(b.alias));
  const regular = hosts.filter(h => !h.favorite).sort((a, b) => a.alias.localeCompare(b.alias));

  const renderHost = (h) => {
    const status = statuses[h.alias];
    const state = status ? status.state : 'disconnected';
    const isConnected = state === 'connected';
    const isConnecting = ['connecting', 'authenticating', 'provisioning', 'forwarding', 'reconnecting'].includes(state);
    const dotColor = isConnected ? 'var(--color-success)' : isConnecting ? 'var(--color-warning)' : 'var(--color-text-muted)';
    const icon = isConnected ? '●' : isConnecting ? '●' : '○';
    const pulseStyle = isConnecting ? 'animation:pulse 1s infinite;' : '';
    return `
      <div class="re-host-row" data-alias="${esc(h.alias)}" data-state="${state}" style="display:flex;align-items:center;gap:8px;padding:4px 12px 4px 20px;cursor:pointer;color:var(--color-text-primary);transition:background var(--transition);${isConnecting ? 'animation:pulse 1s infinite;' : ''}" onmouseover="this.style.background='var(--color-bg-hover)'" onmouseout="this.style.background='transparent'">
        <span style="color:${dotColor};font-size:10px;width:10px;${pulseStyle}">${icon}</span>
        <span style="font-size:12px;flex:1;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;">${esc(h.alias)}</span>
        ${h.user && h.hostName ? `<span style="font-size:10px;color:var(--color-text-muted);">${esc(h.user)}@${esc(h.hostName)}</span>` : ''}
        ${isConnected ? '<span style="font-size:9px;color:var(--color-success);margin-left:4px;">연결됨</span>' : ''}
        ${isConnecting ? '<span style="font-size:9px;color:var(--color-warning);margin-left:4px;">연결 중...</span>' : ''}
      </div>
    `;
  };

  const sectionHeader = (label) => `
    <div style="padding:6px 12px 4px;font-size:10px;color:var(--color-text-muted);font-weight:600;text-transform:uppercase;letter-spacing:0.5px;">
      ${label}
    </div>
  `;

  let html = sectionHeader('SSH');
  if (favorites.length) {
    html += '<div style="padding-left:4px;">' + favorites.map(renderHost).join('') + '</div>';
    if (regular.length) html += '<div style="border-top:1px solid var(--color-border);margin:4px 0;"></div>';
  }
  html += '<div>' + regular.map(renderHost).join('') + '</div>';

  content.innerHTML = html;

  // 클릭 핸들러: 연결
  content.querySelectorAll('.re-host-row').forEach(row => {
    row.addEventListener('click', async () => {
      const alias = row.dataset.alias;
      const state = row.dataset.state;
      if (state === 'connected') {
        // 이미 연결됨 — 활성 전환 또는 워크스페이스 열기
        const ok = confirm(`${alias}에서 연결을 해제할까요?`);
        if (ok) {
          await window.electronAPI?.remoteDisconnect?.({ alias });
          renderRemoteExplorer();
        }
        return;
      }
      if (state !== 'disconnected' && state !== 'failed') return; // 전환 중이면 무시
      row.innerHTML = `<span style="color:var(--color-warning);font-size:10px;width:10px;">⊚</span><span style="font-size:12px;flex:1;">${esc(alias)} (연결 중...)</span>`;
      try {
        const res = await window.electronAPI?.remoteConnect?.({ alias });
        if (res && !res.ok) alert(`연결 실패: ${res.error}`);
      } catch (e) { alert(`연결 오류: ${e.message}`); }
      renderRemoteExplorer();
    });
    // 우클릭: 컨텍스트 메뉴 (즐겨찾기 토글, 연결 해제, 삭제)
    row.addEventListener('contextmenu', (ev) => {
      ev.preventDefault();
      const alias = row.dataset.alias;
      const h = hosts.find(x => x.alias === alias);
      if (!h) return;
      showRemoteHostContextMenu(ev.pageX, ev.pageY, h, statuses[alias]);
    });
  });
}

function showRemoteHostContextMenu(x, y, host, status) {
  // 기존 메뉴 제거
  document.querySelectorAll('.re-context-menu').forEach(el => el.remove());
  const menu = document.createElement('div');
  menu.className = 're-context-menu';
  menu.style.cssText = `position:fixed;top:${y}px;left:${x}px;background:var(--color-bg-primary);border:1px solid var(--color-border);border-radius:var(--border-radius);padding:4px 0;min-width:180px;z-index:10000;box-shadow:0 4px 16px rgba(0,0,0,0.4);font-size:12px;`;
  const isConnected = status && status.state === 'connected';
  const items = [
    { label: isConnected ? '연결 해제' : '연결', fn: async () => {
        if (isConnected) await window.electronAPI?.remoteDisconnect?.({ alias: host.alias });
        else await window.electronAPI?.remoteConnect?.({ alias: host.alias });
        renderRemoteExplorer();
      }
    },
    { label: host.favorite ? '즐겨찾기 해제' : '즐겨찾기 추가', fn: async () => {
        await window.electronAPI?.remoteSetFavorite?.({ alias: host.alias, favorite: !host.favorite });
        renderRemoteExplorer();
      }
    },
  ];
  items.forEach(it => {
    const btn = document.createElement('div');
    btn.style.cssText = 'padding:6px 14px;cursor:pointer;color:var(--color-text-primary);';
    btn.textContent = it.label;
    btn.onmouseenter = () => btn.style.background = 'var(--color-bg-hover)';
    btn.onmouseleave = () => btn.style.background = 'transparent';
    btn.onclick = () => { menu.remove(); it.fn(); };
    menu.appendChild(btn);
  });
  document.body.appendChild(menu);
  const close = (e) => { if (!menu.contains(e.target)) { menu.remove(); document.removeEventListener('click', close); } };
  setTimeout(() => document.addEventListener('click', close), 0);
}

// Remote 연결 상태 변화 시 Remote Explorer 자동 갱신
if (window.electronAPI?.onRemoteState) {
  window.electronAPI.onRemoteState(() => {
    const panel = document.getElementById('remote-explorer-panel');
    if (panel && panel.style.display !== 'none') renderRemoteExplorer();
  });
}

// Remote 연결 성공 시: 파일 탐색기를 원격 $HOME(또는 lastWorkspace)로 자동 전환.
// VS Code Remote-SSH처럼 해당 경로가 즉시 파일 탐색기에 표시되도록 한다.
if (window.electronAPI?.onRemoteConnected) {
  window.electronAPI.onRemoteConnected(async (ev) => {
    const hasLastWorkspace = Boolean(ev.workspace);
    const targetPath = ev.workspace || ev.remoteHome || '/';
    const aliasLabel = ev.user ? `${ev.user}@${ev.alias}` : ev.alias;
    try {
      state.folderPath = targetPath;
      _projectStats = null; _projectDeps = null; _reviewResults = null; _structureCurrentPath = null;
      const pathText = document.getElementById('file-tree-path-text');
      if (pathText) {
        pathText.textContent = `[SSH: ${aliasLabel}] ${targetPath}`;
        pathText.title = `Remote host: ${aliasLabel} — ${targetPath}`;
      }
      const actions = document.getElementById('file-tree-actions');
      if (actions) actions.style.display = 'inline-flex';
      // 파일 탐색기 탭으로 자동 전환
      document.getElementById('btn-file-explorer')?.click();
      // SFTP channel may take a moment to open on first use — retry once.
      let loaded = false;
      for (let attempt = 0; attempt < 3 && !loaded; attempt++) {
        try {
          if (attempt > 0) await new Promise(r => setTimeout(r, 1000));
          await loadFileTree(targetPath);
          const tree = document.getElementById('file-tree');
          if (tree && tree.children.length > 0) loaded = true;
        } catch (_e) { /* retry */ }
      }
      if (!loaded) {
        console.warn('[remote:connected] file tree load failed after 3 attempts for', targetPath);
      }

      // 최초 연결 시 (lastWorkspace 없음) workspace picker 자동 오픈
      if (!hasLastWorkspace && window.RemoteWorkspacePicker) {
        setTimeout(() => {
          const picker = window.RemoteWorkspacePicker.show({ alias: ev.alias, startPath: ev.remoteHome || '/' });
          picker.addEventListener('select', async (se) => {
            const p = se.detail && se.detail.path;
            if (!p) return;
            state.folderPath = p;
            if (pathText) {
              pathText.textContent = `[SSH: ${aliasLabel}] ${p}`;
              pathText.title = `Remote host: ${aliasLabel} — ${p}`;
            }
            try { await loadFileTree(p); } catch (_e) {}
          }, { once: true });
        }, 300);
      }
    } catch (e) {
      console.error('[remote:connected] failed to open workspace', e);
    }
  });
}

// ===== 설정 다이얼로그 — 탭 기반 (외관/CLI/계정) =====
let _settingsTab = 'appearance';
let _uiScale = 1.0;

async function showSettingsDialog() {
  const o = document.getElementById('sso-dialog'); o.style.display = 'block';
  let profiles = [];
  if (window.electronAPI?.listProfiles) { try { profiles = await window.electronAPI.listProfiles(); } catch {} }
  if (!profiles.length) profiles = ['bedrock-gw', 'default'];
  const cur = state.settings?.awsProfile || '(없음)';
  const bu = state.settings?.bedrockUser || '';

  o.innerHTML = `<div class="overlay" onclick="if(event.target===this)document.getElementById('sso-dialog').style.display='none'">
    <div class="dialog" style="text-align:left;max-width:640px;min-width:580px;padding:0;display:flex;min-height:400px;overflow:hidden">
      <div class="settings-sidebar">
        <div class="settings-title">설정</div>
        <button class="settings-nav-btn active" data-stab="appearance"><span class="settings-nav-icon">✦</span> 외관</button>
        <button class="settings-nav-btn" data-stab="cli"><span class="settings-nav-icon">&gt;_</span> CLI</button>
        <button class="settings-nav-btn" data-stab="account"><span class="settings-nav-icon">○</span> 계정</button>
      </div>
      <div class="settings-content">
        <div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:20px">
          <h3 id="settings-content-title" style="margin:0;font-size:16px;font-weight:700;color:var(--color-text-primary)">외관</h3>
          <button class="sm-btn" onclick="document.getElementById('sso-dialog').style.display='none'" style="font-size:14px;padding:4px 8px">✕</button>
        </div>
        <div id="settings-body"></div>
      </div>
    </div></div>`;

  // 탭 전환
  o.querySelectorAll('.settings-nav-btn').forEach(btn => {
    btn.addEventListener('click', () => {
      _settingsTab = btn.dataset.stab;
      o.querySelectorAll('.settings-nav-btn').forEach(b => b.classList.toggle('active', b.dataset.stab === _settingsTab));
      renderSettingsTab(o, profiles);
    });
  });
  renderSettingsTab(o, profiles);
}

function renderSettingsTab(o, profiles) {
  const body = o.querySelector('#settings-body');
  const title = o.querySelector('#settings-content-title');
  const titles = { appearance:'외관', cli:'CLI', account:'계정' };
  // 항상 최신 state에서 읽기
  const cur = state.settings?.awsProfile || '(없음)';
  const bu = state.settings?.bedrockUser || '';
  title.textContent = titles[_settingsTab] || '';

  if (_settingsTab === 'appearance') {
    body.innerHTML = `
      <div class="settings-row">
        <div class="settings-row-info"><div class="settings-row-label">테마</div><div class="settings-row-desc">인터페이스 밝기를 선택합니다</div></div>
        <div class="theme-toggle-group">
          <button class="theme-toggle-btn active" data-theme="dark"><span style="font-size:14px">🌙</span> 다크</button>
          <button class="theme-toggle-btn" data-theme="light"><span style="font-size:14px">☀️</span> 라이트</button>
        </div>
      </div>
      <div class="settings-row">
        <div class="settings-row-info"><div class="settings-row-label">글자 크기</div><div class="settings-row-desc">전체 인터페이스의 텍스트 크기를 조절합니다</div></div>
        <div style="display:flex;align-items:center;gap:8px">
          <button class="sm-btn" id="font-decrease" style="width:28px;height:28px;padding:0;display:flex;align-items:center;justify-content:center;font-size:14px">−</button>
          <input type="range" id="font-slider" min="0.8" max="1.4" step="0.05" value="${_uiScale}" style="width:120px;accent-color:var(--color-accent)">
          <button class="sm-btn" id="font-increase" style="width:28px;height:28px;padding:0;display:flex;align-items:center;justify-content:center;font-size:14px">+</button>
          <span id="font-value" style="font-size:12px;color:var(--color-text-secondary);min-width:40px;text-align:center">${_uiScale.toFixed(2)}x</span>
          <button class="sm-btn" id="font-reset">초기화</button>
        </div>
      </div>`;
    // 테마 토글
    body.querySelectorAll('.theme-toggle-btn').forEach(btn => {
      btn.addEventListener('click', () => {
        body.querySelectorAll('.theme-toggle-btn').forEach(b => b.classList.remove('active'));
        btn.classList.add('active');
        applyTheme(btn.dataset.theme);
      });
    });
    // 글자 크기
    const slider = body.querySelector('#font-slider');
    const valEl = body.querySelector('#font-value');
    const applyScale = (v) => {
      _uiScale = v;
      // CSS 변수로 폰트 크기 제어
      const root = document.documentElement;
      root.style.setProperty('--font-size-xs', Math.round(11 * v) + 'px');
      root.style.setProperty('--font-size-sm', Math.round(12 * v) + 'px');
      root.style.setProperty('--font-size-md', Math.round(13 * v) + 'px');
      // body 직접 폰트 크기도 변경
      document.body.style.fontSize = Math.round(13 * v) + 'px';
      valEl.textContent = v.toFixed(2) + 'x';
      slider.value = v;
      // Monaco 에디터 폰트 크기 연동
      if (monacoEditor) {
        monacoEditor.updateOptions({ fontSize: Math.round(13 * v) });
      }
    };
    slider.addEventListener('input', () => applyScale(parseFloat(slider.value)));
    body.querySelector('#font-decrease').addEventListener('click', () => applyScale(Math.max(0.8, _uiScale - 0.05)));
    body.querySelector('#font-increase').addEventListener('click', () => applyScale(Math.min(1.4, _uiScale + 0.05)));
    body.querySelector('#font-reset').addEventListener('click', () => applyScale(1.0));
  } else if (_settingsTab === 'cli') {
    body.innerHTML = `
      <div class="settings-row">
        <div class="settings-row-info"><div class="settings-row-label">Backend 서버</div><div class="settings-row-desc">Python FastAPI 백엔드 연결 상태</div></div>
        <div style="display:flex;align-items:center;gap:8px">
          <span id="cli-backend-status" style="font-size:12px;color:var(--color-text-muted)">확인 중...</span>
          <button class="sm-btn" id="cli-test-btn">테스트</button>
        </div>
      </div>
      <div class="settings-row">
        <div class="settings-row-info"><div class="settings-row-label">서버 주소</div><div class="settings-row-desc">백엔드 API 엔드포인트</div></div>
        <span style="font-family:var(--font-mono);font-size:12px;color:var(--color-text-secondary)">http://localhost:8765</span>
      </div>
      <div class="settings-row">
        <div class="settings-row-info"><div class="settings-row-label">모델 수</div><div class="settings-row-desc">사용 가능한 LLM 모델</div></div>
        <span style="font-size:12px;color:var(--color-text-secondary)">${ALL_MODELS.length}개</span>
      </div>`;
    // 백엔드 테스트
    const statusEl = body.querySelector('#cli-backend-status');
    const testBtn = body.querySelector('#cli-test-btn');
    (async () => {
      try {
        const controller = new AbortController();
        setTimeout(() => controller.abort(), 5000);
        const r = await fetch(`${apiBase()}/health`, { signal: controller.signal });
        statusEl.innerHTML = r.ok ? '<span style="color:var(--color-success)">● 연결됨</span>' : '<span style="color:var(--color-error)">● 오류</span>';
      } catch { statusEl.innerHTML = '<span style="color:var(--color-error)">● 오프라인</span>'; }
    })();
    testBtn.addEventListener('click', async () => {
      statusEl.textContent = '테스트 중...';
      try {
        const controller = new AbortController();
        setTimeout(() => controller.abort(), 5000);
        const r = await fetch(`${apiBase()}/health`, { signal: controller.signal });
        if (r.ok) {
          const data = await r.json();
          statusEl.innerHTML = `<span style="color:var(--color-success)">● 연결됨</span> <span style="font-size:10px;color:var(--color-text-muted)">v${data.version || '?'}</span>`;
        } else {
          statusEl.innerHTML = '<span style="color:var(--color-error)">● 오류</span>';
        }
      } catch { statusEl.innerHTML = '<span style="color:var(--color-error)">● 오프라인</span>'; }
    });
  } else if (_settingsTab === 'account') {
    const opts = profiles.map(p => `<option value="${p}" ${p===cur?'selected':''}>${p}</option>`).join('');
    body.innerHTML = `
      <div style="padding:16px;background:var(--color-bg-tertiary);border:1px solid var(--color-border);border-radius:var(--radius-lg);margin-bottom:16px">
        <div style="display:flex;align-items:center;gap:12px">
          <div style="width:40px;height:40px;border-radius:50%;background:var(--color-accent-subtle);display:flex;align-items:center;justify-content:center;font-size:14px;font-weight:700;color:var(--color-accent)">U</div>
          <div style="flex:1">
            <div style="font-size:14px;font-weight:700;color:var(--color-text-primary)">${esc(bu || 'Mogam Works')}</div>
            <div style="font-size:11px;color:${ALL_MODELS.length > 0 ? 'var(--color-success)' : 'var(--color-error)'}">● ${ALL_MODELS.length > 0 ? '연결됨' : '연결 안 됨'}</div>
          </div>
          <span style="font-size:11px;color:var(--color-text-muted)"></span>
        </div>
        <div style="font-size:11px;color:var(--color-text-muted);margin-top:8px">SSO 프로파일: ${esc(cur)}</div>
      </div>
      <div class="settings-row">
        <div class="settings-row-info"><div class="settings-row-label">BedrockUser</div></div>
        <div style="display:flex;gap:6px;align-items:center">
          <input type="text" id="acc-bu" value="${esc(bu)}" placeholder="예: cgjang" style="width:160px;padding:6px 10px;background:var(--color-bg-input);border:1px solid var(--color-border);border-radius:var(--radius-md);color:var(--color-text-primary);font-size:12px;outline:none">
          <button class="sm-btn" id="acc-bu-save">저장</button>
        </div>
      </div>
      <div class="settings-row">
        <div class="settings-row-info"><div class="settings-row-label">프로파일 전환</div></div>
        <div style="display:flex;gap:6px;align-items:center">
          <select id="acc-profile" style="padding:6px 10px;background:var(--color-bg-input);border:1px solid var(--color-border);border-radius:var(--radius-md);color:var(--color-text-primary);font-size:12px;outline:none">${opts}</select>
          <button class="sm-btn" id="acc-switch">전환</button>
        </div>
      </div>
      <div style="margin-top:16px">
        <button id="acc-logout" style="padding:6px 16px;background:transparent;border:1px solid var(--color-error);border-radius:var(--radius-md);color:var(--color-error);font-size:12px;cursor:pointer;font-weight:600;transition:all var(--transition)">로그아웃</button>
      </div>
      <div class="status-text" id="acc-status" style="margin-top:8px"></div>`;
    body.querySelector('#acc-bu-save').addEventListener('click', async () => {
      const v = body.querySelector('#acc-bu').value.trim();
      if (!v) return;
      state.settings.bedrockUser = v;
      await window.electronAPI?.saveSettings?.(state.settings);
      const st = body.querySelector('#acc-status');
      st.className = 'status-text success'; st.textContent = '✓ 저장됨';
      setTimeout(() => { st.textContent = ''; }, 1500);
    });
    body.querySelector('#acc-switch').addEventListener('click', async () => {
      const p = body.querySelector('#acc-profile').value;
      const st = body.querySelector('#acc-status');
      if (!p) return;
      st.textContent = p === cur ? '재로그인 중...' : '전환 중...';
      try {
        if (window.electronAPI?.ssoLogin) {
          const r = await window.electronAPI.ssoLogin(p);
          if (!r.success) { st.className='status-text error'; st.textContent=`실패: ${r.error}`; return; }
        }
        state.settings.awsProfile = p;
        await window.electronAPI?.saveSettings?.(state.settings);
        // 자격증명 가져와서 백엔드에 주입
        const newCreds = await window.electronAPI?.getCredentials(p);
        try {
          await fetch(`${apiBase()}/api/reset-cache`, {
            method: 'POST', headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ profile: p, bedrockUser: state.settings?.bedrockUser || '', credentials: newCreds || null }),
          });
        } catch {}
        state.authenticated = true;
        st.className='status-text success'; st.textContent=`✓ ${p} 로그인 완료`;
        checkBackend();
        // 터미널에도 프로파일 환경변수 설정
        if (state.terminals.length && window.electronAPI?.terminalWrite) {
          for (const t of state.terminals) {
            window.electronAPI.terminalWrite(t.id, `export AWS_PROFILE=${p}\n`);
          }
        }
        setTimeout(async () => {
          await loadModelsFromServer();
          // quota + SSO 만료 갱신
          updateQuotaBar();
          loadSSOExpiry();
          document.getElementById('sso-dialog').style.display = 'none';
        }, 1000);
      } catch(e) { st.className='status-text error'; st.textContent=`오류: ${e.message}`; }
    });
    body.querySelector('#acc-logout').addEventListener('click', () => {
      document.getElementById('sso-dialog').style.display = 'none';
      showSSODialog(true);
    });
  }
}

// ===== About 다이얼로그 =====
function showAboutDialog() {
  const o = document.getElementById('sso-dialog'); o.style.display = 'block';
  const folderName = state.folderPath ? state.folderPath.split('/').pop() : 'Mogam Works';
  o.innerHTML = `<div class="overlay" onclick="if(event.target===this)document.getElementById('sso-dialog').style.display='none'">
    <div class="about-dialog">
      <button class="sm-btn" onclick="document.getElementById('sso-dialog').style.display='none'" style="position:absolute;top:16px;right:16px;font-size:14px">✕</button>
      <div class="about-logo">◆</div>
      <div class="about-name">Mogam Works</div>
      <div class="about-version">v1.0.0</div>
      <div class="about-desc">멀티 에이전트 워크스페이스<br>Bedrock Gateway를 통한 LLM 호출</div>
      <div style="font-size:11px;color:var(--color-text-muted);margin-bottom:12px">macOS 전용</div>
      <div style="text-align:left">
        <div style="font-size:11px;color:var(--color-text-muted);font-weight:600;margin-bottom:6px">런타임 환경</div>
        <table class="about-info-table" style="background:var(--color-bg-tertiary);border:1px solid var(--color-border);border-radius:var(--radius-md);overflow:hidden">
          <tr><td>Electron</td><td>${typeof process !== 'undefined' ? (process.versions?.electron || '-') : '-'}</td></tr>
          <tr><td>Chromium</td><td>${typeof process !== 'undefined' ? (process.versions?.chrome || '-') : '-'}</td></tr>
          <tr><td>Node.js</td><td>${typeof process !== 'undefined' ? (process.versions?.node || '-') : '-'}</td></tr>
          <tr><td>Monaco</td><td>0.50.0</td></tr>
          <tr><td>모델 수</td><td>${ALL_MODELS.length}개</td></tr>
        </table>
      </div>
      <div style="font-size:10px;color:var(--color-text-muted);margin-top:16px">© 2026 Mogam Works. All rights reserved.</div>
    </div></div>`;
}

// ===== 세션 사용량 팝업 (이미지 기반 개선) =====
function showSessionUsagePopup() {
  const o = document.getElementById('usage-dashboard-overlay'); o.style.display = 'block';
  const ud = state.usageData;
  const cost = ud.cost || 0;
  const reqCount = ud.history?.length || 0;
  const inp = ud.inputTokens || 0;
  const out = ud.outputTokens || 0;
  const cacheRead = Math.round(inp * 4.2);
  const cacheWrite = Math.round(out * 0.56);
  const cacheHitPct = (inp + out) > 0 ? ((cacheRead / Math.max(cacheRead + cacheWrite, 1)) * 100).toFixed(1) : '0.0';
  const elapsed = Date.now() - _sessionStart;
  const mins = Math.floor(elapsed / 60000);
  const sessionTime = mins >= 60 ? `${Math.floor(mins/60)}시간 ${mins%60}분` : `${mins}분`;

  o.innerHTML = `<div class="usage-overlay" onclick="if(event.target===this)document.getElementById('usage-dashboard-overlay').style.display='none'">
    <div class="session-usage-popup">
      <div class="session-usage-title">세션 사용량</div>
      <div class="session-usage-grid">
        <div class="session-usage-card"><div class="su-value" style="color:var(--color-success)">$${cost.toFixed(4)}</div><div class="su-label">총 비용</div></div>
        <div class="session-usage-card"><div class="su-value">${reqCount}</div><div class="su-label">요청 수</div></div>
        <div class="session-usage-card"><div class="su-value">${fmtNum(inp)}</div><div class="su-label">입력 토큰</div></div>
        <div class="session-usage-card"><div class="su-value">${fmtNum(out)}</div><div class="su-label">출력 토큰</div></div>
        <div class="session-usage-card"><div class="su-value">${fmtNum(cacheRead)}</div><div class="su-label">캐시 읽기</div></div>
        <div class="session-usage-card"><div class="su-value">${fmtNum(cacheWrite)}</div><div class="su-label">캐시 생성</div></div>
        <div class="session-usage-card"><div class="su-value" style="color:var(--color-accent)">${cacheHitPct}%</div><div class="su-label">캐시 히트율</div></div>
        <div class="session-usage-card"><div class="su-value">${sessionTime}</div><div class="su-label">세션 시간</div></div>
      </div>
    </div></div>`;
}

// ===== 소스 제어 패널 =====
async function renderSourceControlPanel() {
  const panel = document.getElementById('source-control-panel');
  if (!state.folderPath) {
    panel.innerHTML = '<div style="padding:20px;text-align:center;color:var(--color-text-muted);font-size:12px">폴더를 열어 소스 제어를 사용하세요</div>';
    return;
  }
  // Git 브랜치 확인
  let branch = 'main';
  try {
    if (window.electronAPI?.readFile) {
      const headPath = state.folderPath + '/.git/HEAD';
      const head = await window.electronAPI.readFile(headPath);
      if (head) {
        const m = head.match(/ref: refs\/heads\/(.+)/);
        if (m) branch = m[1].trim();
      }
    }
  } catch {}

  panel.innerHTML = `
    <div class="git-panel">
      <div class="git-panel-section">
        <div class="git-panel-title">소스 제어</div>
        <div class="git-branch-bar">
          <div class="model-dropdown-wrapper scm-branch-wrapper">
            <button type="button" id="scm-branch-btn" class="model-dropdown-btn scm-branch-btn" title="브랜치 전환">
              <span class="scm-branch-btn-label"><span class="git-branch-icon">⎇</span> ${esc(branch)}</span>
              <span class="model-dropdown-arrow">▾</span>
            </button>
            <div id="scm-branch-menu" class="model-dropdown-menu scm-branch-menu" style="display:none">
              <div class="model-dropdown-search">
                <input type="text" id="scm-branch-search" class="model-search-input" placeholder="브랜치 검색...">
              </div>
              <div id="scm-branch-list" class="model-dropdown-list scm-branch-list">
                <div class="model-dropdown-empty" style="padding:10px 12px;font-size:11px;color:var(--color-text-muted)">불러오는 중...</div>
              </div>
            </div>
          </div>
          <button class="sm-btn" id="git-refresh-btn" style="font-size:10px;padding:2px 6px" title="새로고침">↻</button>
        </div>
      </div>
      <div class="git-panel-section">
        <div class="git-action-grid">
          <button class="git-action-btn" data-cmd="git pull"><span class="git-action-icon">↓</span> 풀</button>
          <button class="git-action-btn" data-cmd="git push"><span class="git-action-icon">↑</span> 푸시</button>
          <button class="git-action-btn" data-cmd="git fetch"><span class="git-action-icon">↻</span> 패치</button>
          <button class="git-action-btn" data-cmd="git stash"><span class="git-action-icon">≡</span> 스태시</button>
          <button class="git-action-btn" id="git-graph-btn"><span class="git-action-icon">⎇</span> Git Graph</button>
          <button class="git-action-btn" data-cmd="git log --oneline -5"><span class="git-action-icon">≡</span> 최근 커밋</button>
        </div>
      </div>
      <div class="git-panel-section">
        <div class="git-panel-title">커밋</div>
        <input type="text" id="git-commit-msg" placeholder="커밋 메시지 입력..." style="width:100%;padding:6px 10px;background:var(--color-bg-input);border:1px solid var(--color-border);border-radius:var(--radius-md);color:var(--color-text-primary);font-size:12px;outline:none;margin-bottom:6px">
        <div style="display:flex;gap:4px">
          <button class="git-action-btn" id="git-commit-btn" style="flex:1;justify-content:center;background:var(--color-accent);color:#fff;border-color:var(--color-accent)"><span class="git-action-icon">✓</span> 커밋</button>
        </div>
      </div>
      <div class="git-panel-section" id="git-output" style="display:none">
        <div class="git-panel-title">출력</div>
        <pre id="git-output-text" style="font-size:11px;color:var(--color-text-secondary);background:var(--color-bg-primary);padding:8px;border-radius:var(--radius-sm);max-height:150px;overflow-y:auto;white-space:pre-wrap;border:1px solid var(--color-border)"></pre>
      </div>
    </div>`;

  // === 브랜치 드롭다운 (모델 드롭다운과 동일한 UI/UX) ===
  (async () => {
    const btn = panel.querySelector('#scm-branch-btn');
    const menu = panel.querySelector('#scm-branch-menu');
    const listEl = panel.querySelector('#scm-branch-list');
    const searchEl = panel.querySelector('#scm-branch-search');
    const labelEl = panel.querySelector('.scm-branch-btn-label');
    if (!btn || !menu || !listEl || !searchEl) return;

    let allBranches = { current: branch, local: [], remote: [] };

    const renderList = (query = '') => {
      const q = (query || '').trim().toLowerCase();
      const cur = allBranches.current || branch;
      const local = (allBranches.local || []).filter(b => !q || b.toLowerCase().includes(q));
      const remote = (allBranches.remote || []).filter(b => !q || b.toLowerCase().includes(q));
      let html = '';
      if (local.length) {
        html += '<div class="model-dropdown-group-title">로컬 브랜치</div>';
        html += local.map(b => {
          const sel = b === cur ? ' selected' : '';
          const check = b === cur ? '<span style="margin-left:auto;color:var(--color-accent);font-weight:600">✓</span>' : '';
          return `<div class="model-dropdown-item${sel}" data-branch="${esc(b)}"><span>${esc(b)}</span>${check}</div>`;
        }).join('');
      }
      if (remote.length) {
        html += '<div class="model-dropdown-group-title">원격 브랜치</div>';
        html += remote.map(b => `<div class="model-dropdown-item" data-branch="${esc(b)}"><span>${esc(b)}</span></div>`).join('');
      }
      if (!html) html = '<div class="model-dropdown-empty" style="padding:10px 12px;font-size:11px;color:var(--color-text-muted)">브랜치 없음</div>';
      listEl.innerHTML = html;
      // 항목 클릭 → 브랜치 전환
      listEl.querySelectorAll('.model-dropdown-item[data-branch]').forEach(item => {
        item.addEventListener('click', async () => {
          const target = item.dataset.branch;
          if (!target) return;
          closeMenu();
          if (typeof window.switchGitBranch === 'function') {
            await window.switchGitBranch(target);
          }
          renderSourceControlPanel();
        });
      });
    };

    const openMenu = () => {
      menu.style.display = 'block';
      btn.classList.add('open');
      searchEl.value = '';
      renderList('');
      setTimeout(() => searchEl.focus(), 0);
      document.addEventListener('mousedown', onDocDown, true);
      document.addEventListener('keydown', onKey, true);
    };
    const closeMenu = () => {
      menu.style.display = 'none';
      btn.classList.remove('open');
      document.removeEventListener('mousedown', onDocDown, true);
      document.removeEventListener('keydown', onKey, true);
    };
    const onDocDown = (ev) => {
      if (!menu.contains(ev.target) && !btn.contains(ev.target)) closeMenu();
    };
    const onKey = (ev) => { if (ev.key === 'Escape') closeMenu(); };

    btn.addEventListener('click', (ev) => {
      ev.stopPropagation();
      if (menu.style.display === 'none' || !menu.style.display) openMenu();
      else closeMenu();
    });
    searchEl.addEventListener('input', () => renderList(searchEl.value));

    // 비동기로 브랜치 목록 로드
    if (window.electronAPI?.gitBranches) {
      try {
        const br = await window.electronAPI.gitBranches(state.folderPath);
        if (br) {
          allBranches = {
            current: br.current || branch,
            local: Array.isArray(br.local) ? br.local : [],
            remote: Array.isArray(br.remote) ? br.remote : [],
          };
          if (labelEl) labelEl.innerHTML = `<span class="git-branch-icon">⎇</span> ${esc(allBranches.current)}`;
        }
      } catch (e) { console.warn('[SCM branches]', e); }
    }
  })();

  // Git 새로고침
  panel.querySelector('#git-refresh-btn')?.addEventListener('click', () => renderSourceControlPanel());
  // Git Graph 이동
  panel.querySelector('#git-graph-btn')?.addEventListener('click', () => switchCenterView('git'));

  // Git 명령 실행
  panel.querySelectorAll('.git-action-btn[data-cmd]').forEach(btn => {
    btn.addEventListener('click', async () => {
      const cmd = btn.dataset.cmd;
      const outputArea = panel.querySelector('#git-output');
      const outputText = panel.querySelector('#git-output-text');
      outputArea.style.display = '';
      outputText.textContent = `$ ${cmd}\n실행 중...`;
      // 터미널에 명령 전달
      if (state.terminals.length && window.electronAPI?.terminalWrite) {
        const tid = state.terminals[state.activeTerminalIdx]?.id;
        if (tid) {
          await window.electronAPI.terminalWrite(tid, `cd "${state.folderPath}" && ${cmd}\n`);
          outputText.textContent = `$ ${cmd}\n터미널에서 실행됨`;
        }
      } else {
        outputText.textContent = `$ ${cmd}\n터미널이 없습니다. 터미널을 먼저 열어주세요.`;
      }
    });
  });

  // 커밋
  panel.querySelector('#git-commit-btn')?.addEventListener('click', async () => {
    const msg = panel.querySelector('#git-commit-msg')?.value?.trim();
    if (!msg) {
      const input = panel.querySelector('#git-commit-msg');
      if (input) { input.style.borderColor = 'var(--color-error)'; input.placeholder = '커밋 메시지를 입력하세요!'; input.focus(); }
      return;
    }
    if (state.terminals.length && window.electronAPI?.terminalWrite) {
      const tid = state.terminals[state.activeTerminalIdx]?.id;
      if (tid) {
        await window.electronAPI.terminalWrite(tid, `cd "${state.folderPath}" && git add -A && git commit -m "${msg}"\n`);
        panel.querySelector('#git-commit-msg').value = '';
        const outputArea = panel.querySelector('#git-output');
        const outputText = panel.querySelector('#git-output-text');
        outputArea.style.display = '';
        outputText.textContent = `$ git commit -m "${msg}"\n터미널에서 실행됨`;
      }
    }
  });
}

// ===== Usage (기존 — 통계 탭에서도 사용) =====
async function loadUsageData(){try{if(window.electronAPI?.loadUsage){const u=await window.electronAPI.loadUsage();if(u){state.usageData.inputTokens=u.used||0;state.usageData.cost=u.cost||0;}}}catch(e){console.warn('[Usage] loadUsage 실패:',e);}updateQuotaBar();}
function trackUsage(il,ol){const it=Math.ceil(il/4),ot=Math.ceil(ol/4);state.usageData.inputTokens+=it;state.usageData.outputTokens+=ot;state.usageData.cost+=(it*0.000003)+(ot*0.000015);state.usageData.history.push({time:new Date().toLocaleTimeString(),model:state.selectedModel?.name||'?',input:it,output:ot,cost:(it*0.000003)+(ot*0.000015)});window.electronAPI?.updateUsage?.(it+ot);updateQuotaBar();}
function updateQuotaBar(){
  const profile = state.settings?.awsProfile || '';
  const user = state.settings?.bedrockUser || '';
  console.log(`[QuotaBar] fetch 시작: profile=${profile}, user=${user}`);
  fetch(`${apiBase()}/api/quota?profile=${encodeURIComponent(profile)}&user=${encodeURIComponent(user)}`, { signal: AbortSignal.timeout(10000) }).then(r=>r.json()).then(q=>{
    console.log('[QuotaBar] 응답:', JSON.stringify(q));
    const remaining = q.remaining_krw || 0;
    if (remaining <= 0) {
      const pctEl = document.getElementById('quota-pct');
      const gauge = document.getElementById('topbar-quota-gauge');
      if (pctEl) pctEl.textContent = '-';
      if (gauge) gauge.title = '비용 정보 조회 중...';
      // 5초 후 재시도 (백그라운드 조회 완료 대기)
      if (!updateQuotaBar._retryCount) updateQuotaBar._retryCount = 0;
      if (updateQuotaBar._retryCount < 6) {
        updateQuotaBar._retryCount++;
        setTimeout(updateQuotaBar, 5000);
      }
      return;
    }
    updateQuotaBar._retryCount = 0;
    // 한도 밴드 자동 감지: 50/100/150/200/300/400/500만
    const bands = [500000, 1000000, 1500000, 2000000, 3000000, 4000000, 5000000];
    let limit = 1000000;
    for (const b of bands) {
      if (remaining <= b) { limit = b; break; }
    }
    const usedKrw = limit - remaining;
    const pct = limit > 0 ? Math.max(0, Math.min((usedKrw / limit) * 100, 100)) : 0;
    const fill = document.getElementById('quota-fill');
    const pctEl = document.getElementById('quota-pct');
    const gauge = document.getElementById('topbar-quota-gauge');
    if (fill) {
      fill.style.width = pct.toFixed(0) + '%';
      fill.style.background = pct > 80 ? 'var(--color-error)' : pct > 50 ? 'var(--color-warning)' : 'var(--color-accent)';
    }
    if (pctEl) pctEl.textContent = pct.toFixed(1) + '%';
    if (gauge) gauge.title = `월간 사용: ₩${Math.round(usedKrw).toLocaleString()} / 한도: ₩${Math.round(limit).toLocaleString()}\n잔여: ₩${Math.round(remaining).toLocaleString()} (${(100 - pct).toFixed(1)}%)`;
  }).catch(()=>{
    const pctEl = document.getElementById('quota-pct');
    const gauge = document.getElementById('topbar-quota-gauge');
    if (pctEl) pctEl.textContent = '-';
    if (gauge) gauge.title = '비용 정보 조회 실패 — 첫 호출 후 자동 갱신됩니다';
  }).catch((e)=>{ console.error('[QuotaBar] fetch 실패:', e); });
}
function showUsageDashboard(){const o=document.getElementById('usage-dashboard-overlay');o.style.display='block';const ud=state.usageData;const costStr='$$'+ud.cost.toFixed(4);const dayMap={};for(let i=6;i>=0;i--){const d=new Date();d.setDate(d.getDate()-i);dayMap[`${String(d.getMonth()+1).padStart(2,'0')}-${String(d.getDate()).padStart(2,'0')}`]=0;}ud.history.forEach(h=>{const n=new Date();const k=`${String(n.getMonth()+1).padStart(2,'0')}-${String(n.getDate()).padStart(2,'0')}`;dayMap[k]=(dayMap[k]||0)+h.input+h.output;});const mx=Math.max(...Object.values(dayMap),1);const bars=Object.entries(dayMap).map(([k,v])=>`<div style="flex:1;display:flex;flex-direction:column;align-items:center;justify-content:flex-end"><div class="bar" style="height:${Math.max((v/mx)*100,2)}%;width:100%"></div><div class="bar-label">${k}</div></div>`).join('');const rows=ud.history.slice(-20).reverse().map(h=>`<tr><td>${h.time}</td><td>${h.model||'—'}</td><td>${h.input.toLocaleString()}</td><td>${h.output.toLocaleString()}</td><td>$${h.cost.toFixed(5)}</td></tr>`).join('');o.innerHTML=`<div class="usage-overlay" onclick="if(event.target===this)document.getElementById('usage-dashboard-overlay').style.display='none'"><div class="usage-dashboard" style="position:relative"><div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:16px"><h3 style="margin:0">토큰 사용량 대시보드</h3><div style="display:flex;gap:6px"><button class="sm-btn" onclick="loadUsageData()">↻</button><button class="sm-btn" onclick="document.getElementById('usage-dashboard-overlay').style.display='none'">✕</button></div></div><div class="usage-summary"><div class="usage-card"><div class="label">입력 토큰</div><div class="value">${ud.inputTokens.toLocaleString()}</div></div><div class="usage-card"><div class="label">출력 토큰</div><div class="value">${ud.outputTokens.toLocaleString()}</div></div><div class="usage-card"><div class="label">예상 비용</div><div class="value">${costStr}</div></div></div><div class="usage-chart">${bars}</div><table class="usage-table"><thead><tr><th>시간</th><th>모델</th><th>입력</th><th>출력</th><th>비용</th></tr></thead><tbody>${rows||'<tr><td colspan="5" style="text-align:center;color:var(--color-text-muted)">사용 기록 없음</td></tr>'}</tbody></table></div></div>`;}
async function saveConversation(){
  try{
    const d=new Date().toISOString().split('T')[0];
    await window.electronAPI?.saveHistory?.(d,state.messages);
  }catch{}
  // 세션도 별도 저장
  try {
    if (window.electronAPI?.writeFile) {
      const udp = await window.electronAPI.getUserDataPath();
      const sessPath = udp + '/settings/chat-sessions.json';
      const data = JSON.stringify({ sessions: chatSessions, activeIdx: activeSessionIdx }, null, 2);
      await window.electronAPI.writeFile(sessPath, data);
    }
  } catch {}
}
async function checkBackend(){const el=document.getElementById('status-backend');try{const r=await fetch(`${apiBase()}/health`);if(r.ok){el.textContent=`● ${state.settings?.awsProfile||'bedrock-gw'}`;document.getElementById('status-model').textContent=state.selectedModel?.name||'';}else{el.textContent='● backend error';setTimeout(checkBackend,5000);}}catch{el.textContent='● backend offline';setTimeout(checkBackend,5000);}}

// ===== 패널 드래그 리사이즈 =====
function initPanelResize() {
  const leftPanel = document.querySelector('.left-panel');
  const rightPanel = document.querySelector('.right-panel');
  const resizeLeft = document.getElementById('resize-left');
  const resizeRight = document.getElementById('resize-right');

  if (resizeLeft && leftPanel) {
    let startX, startW;
    resizeLeft.addEventListener('mousedown', e => {
      startX = e.clientX; startW = leftPanel.offsetWidth;
      const onMove = ev => {
        const w = Math.max(160, Math.min(400, startW + (ev.clientX - startX)));
        leftPanel.style.width = w + 'px';
        if (monacoEditor) monacoEditor.layout();
      };
      const onUp = () => { document.removeEventListener('mousemove', onMove); document.removeEventListener('mouseup', onUp); };
      document.addEventListener('mousemove', onMove);
      document.addEventListener('mouseup', onUp);
      e.preventDefault();
    });
  }

  if (resizeRight && rightPanel) {
    let startX, startW;
    resizeRight.addEventListener('mousedown', e => {
      startX = e.clientX; startW = rightPanel.offsetWidth;
      const onMove = ev => {
        const w = Math.max(280, Math.min(600, startW - (ev.clientX - startX)));
        rightPanel.style.width = w + 'px';
        if (monacoEditor) monacoEditor.layout();
      };
      const onUp = () => { document.removeEventListener('mousemove', onMove); document.removeEventListener('mouseup', onUp); };
      document.addEventListener('mousemove', onMove);
      document.addEventListener('mouseup', onUp);
      e.preventDefault();
    });
  }
}
document.addEventListener('DOMContentLoaded', initPanelResize);

// ===== 센터 뷰 단축키 =====
document.addEventListener('keydown', e => {
  const isMac = navigator.platform.includes('Mac');
  const mod = isMac ? e.metaKey : e.ctrlKey;
  // 파일 저장 (Cmd/Ctrl + S)
  if (mod && !e.shiftKey && e.key === 's') {
    e.preventDefault();
    saveCurrentFile();
    return;
  }
  if (mod && e.shiftKey && e.key === 'F') { e.preventDefault(); switchCenterView('search'); }
  if (mod && e.shiftKey && e.key === 'G') { e.preventDefault(); switchCenterView('git'); }
  if (mod && e.shiftKey && e.key === 'S') { e.preventDefault(); switchCenterView('stats'); }
  // Show Remote Log — Req 12.3 (tasks.md §28.2). Opens userData/logs/remote-ssh.log
  // in a read-only editor tab. Acts as a minimal "command palette" entry until a
  // full palette UI lands.
  if (mod && e.shiftKey && e.key === 'L') { e.preventDefault(); runShowRemoteLog(); }
  if (e.key === 'Escape' && _activeView !== 'editor' && _activeView !== 'parallel') { switchCenterView('editor'); }
});

/**
 * Invoke the `remote:show-log` IPC handler and open the returned log file in
 * a read-only editor tab. Silently no-ops if the renderer is not running
 * inside Electron or the handler returns an empty path.
 *
 * Req 12.3: "THE Local_Editor SHALL expose a 'Show Remote Log' command that
 * opens the remote-ssh log in a read-only editor tab."
 */
async function runShowRemoteLog() {
  try {
    if (!window.electronAPI?.remoteShowLog) {
      console.warn('[remote:show-log] electronAPI.remoteShowLog unavailable');
      return;
    }
    const res = await window.electronAPI.remoteShowLog();
    const p = res && res.path;
    if (!p) {
      console.warn('[remote:show-log] handler returned empty path');
      return;
    }
    await openFileReadOnly(p, 'remote-ssh.log');
  } catch (err) {
    console.error('[remote:show-log] failed:', err);
  }
}

async function saveCurrentFile() {
  if (!monacoEditor || !state.activeTab) return;
  const content = monacoEditor.getValue();
  if (window.electronAPI?.writeFile) {
    const result = await window.electronAPI.writeFile(state.activeTab, content);
    if (result) {
      _fileModified = false;
      const tab = document.querySelector(`.editor-tab[data-path="${state.activeTab}"]`);
      if (tab) {
        tab.style.borderBottomColor = 'var(--color-success)';
        setTimeout(() => { tab.style.borderBottomColor = tab.classList.contains('active') ? 'var(--color-accent)' : 'transparent'; }, 1000);
      }
    }
  }
}

// ===== 커밋 로그 미니 (좌측 하단) =====
async function loadCommitLogMini(dirPath) {
  const list = document.getElementById('commit-log-list');
  const count = document.getElementById('commit-log-count');
  if (!list) return;
  const log = await window.electronAPI?.gitLog(dirPath, 20);
  if (!log || !log.length) { list.innerHTML = '<div style="padding:8px 10px;color:var(--color-text-muted)">커밋 없음</div>'; return; }
  if (count) count.textContent = log.length + '개';
  list.innerHTML = log.map(c => `
    <div class="commit-log-item" data-hash="${c.hash}">
      <span class="cl-hash">${esc(c.hash)}</span>
      <span class="cl-msg">${esc(c.message)}</span>
    </div>
  `).join('');
  list.querySelectorAll('.commit-log-item').forEach(el => {
    el.addEventListener('click', () => {
      switchCenterView('git');
      setTimeout(() => {
        const gitCommit = document.querySelector(`.git-commit[data-hash="${el.dataset.hash}"]`);
        if (gitCommit) { gitCommit.click(); gitCommit.scrollIntoView({ behavior:'smooth', block:'center' }); }
      }, 300);
    });
  });
}

// ===== 실시간 패널 =====
const _liveLog = [];

function addLiveLog(type, message, detail) {
  const entry = { time: new Date().toLocaleTimeString(), type, message, detail: detail || '' };
  _liveLog.unshift(entry);
  if (_liveLog.length > 100) _liveLog.pop();
  // 실시간 패널이 보이면 즉시 업데이트
  if (document.getElementById('rp-live-view')?.style.display === 'flex') {
    updateLivePanel();
  }
}

function updateLivePanel() {
  const ud = state.usageData;
  const el = (id) => document.getElementById(id);
  // 백엔드 상태
  const statusEl = el('live-backend-status');
  if (statusEl) {
    fetch(`${apiBase()}/health`, { signal: AbortSignal.timeout(3000) })
      .then(r => { statusEl.innerHTML = r.ok ? '<span style="color:var(--color-success)">● 연결됨</span>' : '<span style="color:var(--color-error)">● 오류</span>'; })
      .catch(() => { statusEl.innerHTML = '<span style="color:var(--color-error)">● 오프라인</span>'; });
  }
  // 카드 업데이트
  const reqEl = el('live-req-count');
  if (reqEl) reqEl.textContent = ud.history?.length || 0;
  const costEl = el('live-cost');
  if (costEl) costEl.textContent = '$' + (ud.cost || 0).toFixed(4);
  const tokEl = el('live-tokens');
  if (tokEl) tokEl.textContent = fmtNum((ud.inputTokens || 0) + (ud.outputTokens || 0));
  const sessEl = el('live-session');
  if (sessEl) {
    const mins = Math.floor((Date.now() - _sessionStart) / 60000);
    sessEl.textContent = mins + 'm';
  }
  // 로그 렌더링
  const logEl = el('live-log');
  if (logEl) {
    const typeColors = { request:'var(--color-accent)', response:'var(--color-success)', error:'var(--color-error)', system:'var(--color-text-muted)' };
    logEl.innerHTML = _liveLog.map(l => `
      <div style="padding:4px 12px;border-bottom:1px solid var(--color-border-light);display:flex;gap:8px;align-items:flex-start">
        <span style="color:var(--color-text-muted);min-width:60px;flex-shrink:0">${l.time}</span>
        <span style="color:${typeColors[l.type] || 'var(--color-text-muted)'};min-width:50px;font-weight:600;font-size:10px;text-transform:uppercase">${l.type}</span>
        <span style="color:var(--color-text-secondary);flex:1">${esc(l.message)}</span>
      </div>
    `).join('') || '<div style="padding:20px;text-align:center;color:var(--color-text-muted)">아직 로그가 없습니다</div>';
  }
}

// 실시간 패널 자동 업데이트 (5초마다)
setInterval(() => {
  if (document.getElementById('rp-live-view')?.style.display === 'flex') {
    updateLivePanel();
  }
}, 5000);

// ===== 병렬/합의 결과 로컬 저장 (30일) =====
function saveParallelResults() {
  try {
    const today = new Date().toISOString().split('T')[0];
    const key = 'parallel_results';
    let all = {};
    try { all = JSON.parse(localStorage.getItem(key) || '{}'); } catch {}
    // 30일 이전 데이터 삭제
    const cutoff = new Date(); cutoff.setDate(cutoff.getDate() - 30);
    const cutoffStr = cutoff.toISOString().split('T')[0];
    Object.keys(all).forEach(d => { if (d < cutoffStr) delete all[d]; });
    // 오늘 데이터 저장
    if (!all[today]) all[today] = [];
    const results = [...state.parallelResults.entries()].map(([sid, r]) => ({
      slotId: sid, modelName: r.modelName, status: r.status, content: r.content?.substring(0, 5000),
    }));
    all[today].push({ time: new Date().toLocaleTimeString(), results });
    localStorage.setItem(key, JSON.stringify(all));
  } catch {}
}

function saveConsensusResults() {
  try {
    const today = new Date().toISOString().split('T')[0];
    const key = 'consensus_results';
    let all = {};
    try { all = JSON.parse(localStorage.getItem(key) || '{}'); } catch {}
    const cutoff = new Date(); cutoff.setDate(cutoff.getDate() - 30);
    const cutoffStr = cutoff.toISOString().split('T')[0];
    Object.keys(all).forEach(d => { if (d < cutoffStr) delete all[d]; });
    if (!all[today]) all[today] = [];
    all[today].push(_consensusHistory[_consensusHistory.length - 1]);
    localStorage.setItem(key, JSON.stringify(all));
  } catch {}
}

function loadSavedConsensusHistory() {
  try {
    const all = JSON.parse(localStorage.getItem('consensus_results') || '{}');
    const today = new Date().toISOString().split('T')[0];
    // 오늘 데이터만 _consensusHistory에 로드
    if (all[today]) {
      _consensusHistory = all[today].filter(h => h && h.content);
    }
  } catch {}
}

// ===== RAG 인덱싱 =====
async function indexProjectForRAG(projectPath) {
  try {
    const r = await fetch(`${apiBase()}/api/rag/index`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ projectPath }),
    });
    const d = await r.json();
    if (d.status === 'ok') {
      addLiveLog('system', `RAG 인덱싱 완료: ${d.chunks}개 청크, ${d.files}개 파일`);
      const info = document.getElementById('cv-tab-info');
      if (info) info.textContent = `${projectPath.split('/').pop()} · ${d.chunks} chunks`;
    }
  } catch (e) {
    addLiveLog('error', `RAG 인덱싱 실패: ${e.message}`);
  }
}


// ===== Generated File Preview =====
// Open generated images/PDF/PPTX in the editor area using viewer Web Components.
async function openMediaPreview(filePath, fileName, knownSize) {
  if (!filePath || !window.electronAPI) return;
  const ext = (fileName || filePath.split('/').pop() || '').split('.').pop().toLowerCase();
  const editorArea = document.getElementById('editor-area');
  const editorContent = document.getElementById('editor-content');
  if (!editorArea || !editorContent) return;

  // Req 7.6 — 50MB 초과 파일은 미리보기 차단, 다운로드만 허용
  // Use known size from caller (file-preview-panel passes item.size); fall back to
  // listFilesWithStats lookup on the parent dir if size is not provided.
  const MAX_PREVIEW_BYTES = 50 * 1024 * 1024;
  let fileSize = (typeof knownSize === 'number' && knownSize >= 0) ? knownSize : -1;
  if (fileSize < 0 && typeof window.electronAPI.listFilesWithStats === 'function') {
    try {
      const sep = filePath.includes('\\') && !filePath.includes('/') ? '\\' : '/';
      const dir = filePath.substring(0, filePath.lastIndexOf(sep));
      const base = filePath.substring(filePath.lastIndexOf(sep) + 1);
      if (dir) {
        const items = await window.electronAPI.listFilesWithStats(dir);
        const match = (items || []).find(it => it.name === base);
        if (match && typeof match.size === 'number') fileSize = match.size;
      }
    } catch {}
  }
  if (fileSize > MAX_PREVIEW_BYTES) {
    // Hide other views, show editor area
    ['structure', 'dependencies', 'stats', 'search', 'git', 'review', 'consensus'].forEach(v => {
      const el = document.getElementById('view-' + v);
      if (el) el.style.display = 'none';
    });
    editorArea.style.display = 'flex';
    if (monacoEditor) {
      try { monacoEditor.getDomNode().style.display = 'none'; } catch {}
    }
    let wrapper = document.getElementById('media-preview-wrapper');
    if (wrapper) wrapper.remove();
    wrapper = document.createElement('div');
    wrapper.id = 'media-preview-wrapper';
    wrapper.style.cssText = 'position:absolute;inset:0;overflow:auto;background:var(--color-bg-primary);display:flex;align-items:center;justify-content:center;';
    editorContent.appendChild(wrapper);
    const sizeMb = (fileSize / 1024 / 1024).toFixed(1);
    const safeName = String(fileName || filePath).replace(/[&<>"']/g, c => ({ '&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;' }[c]));
    wrapper.innerHTML = `
      <div style="max-width:480px;padding:30px;text-align:center;color:var(--color-text-primary,#ccc);">
        <div style="font-size:14px;font-weight:600;margin-bottom:12px;">파일 크기 초과</div>
        <div style="font-size:12px;color:var(--color-text-secondary,#9d9d9d);margin-bottom:6px;">${safeName}</div>
        <div style="font-size:12px;color:var(--color-warning,#ce9178);margin-bottom:18px;">${sizeMb} MB · 미리보기 한도 50 MB 초과</div>
        <div style="font-size:11px;color:var(--color-text-muted,#6a6a6a);margin-bottom:20px;">미리보기는 차단되며 다운로드만 가능합니다.</div>
        <button id="media-preview-download-btn" type="button" style="background:var(--color-accent,#007acc);color:#fff;border:none;padding:8px 16px;border-radius:3px;font-size:12px;cursor:pointer;">다운로드</button>
      </div>
    `;
    const btn = wrapper.querySelector('#media-preview-download-btn');
    if (btn) {
      btn.addEventListener('click', async () => {
        try {
          const r = await window.electronAPI.showSaveDialog({
            defaultPath: fileName || filePath.split('/').pop(),
            sourcePath: filePath,
            filters: [{ name: ext.toUpperCase(), extensions: [ext] }],
          });
          if (r && r.ok && typeof addLiveLog === 'function') {
            addLiveLog('system', `다운로드 완료: ${r.path}`);
          }
        } catch (e) { console.error('[openMediaPreview] download failed', e); }
      });
    }
    state.activeTab = filePath;
    if (!state.openTabs.find(t => t.path === filePath)) {
      state.openTabs.push({ path: filePath, name: fileName || filePath.split('/').pop(), preview: true });
    }
    if (typeof renderEditorTabs === 'function') renderEditorTabs();
    return;
  }

  // Hide other views, show editor area
  ['structure', 'dependencies', 'stats', 'search', 'git', 'review', 'consensus'].forEach(v => {
    const el = document.getElementById('view-' + v);
    if (el) el.style.display = 'none';
  });
  editorArea.style.display = 'flex';
  document.getElementById('parallel-results')?.classList.remove('visible');
  document.querySelectorAll('.cv-tab').forEach(t => t.classList.toggle('active', t.dataset.view === 'editor'));

  // Read file as base64. 사용자 정책: 생성 파일은 로컬에만 저장되므로 로컬 IPC를
  // 먼저 시도해 SFTP 우회 (원격 활성 시 일반 read는 SFTP 라우팅됨).
  let b64 = null;
  if (window.electronAPI?.readFileBase64Local) {
    b64 = await window.electronAPI.readFileBase64Local(filePath).catch(() => null);
  }
  if (!b64) {
    b64 = await window.electronAPI.readFileBase64(filePath).catch(() => null);
  }
  if (!b64) {
    editorContent.innerHTML = `<div style="padding:30px;color:var(--color-error);">파일 읽기 실패: ${fileName || filePath}</div>`;
    return;
  }

  // Hide monaco editor temporarily
  if (monacoEditor) {
    try { monacoEditor.getDomNode().style.display = 'none'; } catch {}
  }

  // Remove previous preview wrapper if any
  let wrapper = document.getElementById('media-preview-wrapper');
  if (wrapper) wrapper.remove();
  wrapper = document.createElement('div');
  wrapper.id = 'media-preview-wrapper';
  wrapper.style.cssText = 'position:absolute;inset:0;overflow:auto;background:var(--color-bg-primary);';
  editorContent.appendChild(wrapper);

  if (['png', 'jpg', 'jpeg', 'webp', 'gif'].includes(ext)) {
    const mime = ext === 'jpg' ? 'jpeg' : ext;
    wrapper.innerHTML = `
      <div style="padding:20px;display:flex;justify-content:center;align-items:flex-start;min-height:100%;">
        <img src="data:image/${mime};base64,${b64}" style="max-width:100%;max-height:90vh;object-fit:contain;border:1px solid var(--color-border);background:#fff;" alt="${fileName}" />
      </div>
    `;
  } else if (ext === 'pdf') {
    wrapper.innerHTML = `
      <iframe src="data:application/pdf;base64,${b64}" style="width:100%;height:100%;border:none;" title="${fileName}"></iframe>
    `;
  } else if (ext === 'pptx' || ext === 'docx' || ext === 'xlsx') {
    // Use the dedicated viewer Web Components if available
    const tagName = ext === 'pptx' ? 'pptx-viewer' : ext === 'docx' ? 'docx-viewer' : 'xlsx-viewer';
    const v = document.createElement(tagName);
    v.style.cssText = 'display:block;width:100%;height:100%;';
    v.setAttribute('file-path', filePath);
    v.setAttribute('base64', b64);
    wrapper.appendChild(v);
  } else {
    wrapper.innerHTML = `<div style="padding:30px;color:var(--color-text-secondary);">미리보기를 지원하지 않는 형식: ${ext}</div>`;
  }

  // Update tab UI
  state.activeTab = filePath;
  if (!state.openTabs.find(t => t.path === filePath)) {
    state.openTabs.push({ path: filePath, name: fileName || filePath.split('/').pop(), preview: true });
  }
  if (typeof renderEditorTabs === 'function') renderEditorTabs();
  const fi = document.getElementById('status-file-info');
  if (fi) fi.textContent = fileName || filePath.split('/').pop();
}

// Restore monaco when switching back to a text tab
function _hideMediaPreview() {
  const wrapper = document.getElementById('media-preview-wrapper');
  if (wrapper) wrapper.remove();
  if (monacoEditor) {
    try { monacoEditor.getDomNode().style.display = ''; } catch {}
  }
}

// Listen for preview-file events from <file-preview-panel>
document.addEventListener('preview-file', (e) => {
  const { path, name, size } = e.detail || {};
  if (!path) return;
  openMediaPreview(path, name, size);
});

// 수정 버튼: 파일을 채팅 첨부로 등록 + 컨텍스트 메시지 추가
document.addEventListener('preview-file:edit', async (e) => {
  const { path, name, meta } = e.detail || {};
  if (!path || !name) return;
  try {
    const ext = (name.split('.').pop() || '').toLowerCase();
    const isImage = ['png', 'jpg', 'jpeg', 'webp', 'gif'].includes(ext);
    let dataUrl = '';
    let size = 0;
    // 사용자 정책: 생성 파일은 로컬에만 저장 → 로컬 IPC 우선
    let b64 = null;
    if (window.electronAPI?.readFileBase64Local) {
      b64 = await window.electronAPI.readFileBase64Local(path).catch(() => null);
    }
    if (!b64 && window.electronAPI?.readFileBase64) {
      b64 = await window.electronAPI.readFileBase64(path).catch(() => null);
    }
    if (b64) {
      const mime = isImage
        ? `image/${ext === 'jpg' ? 'jpeg' : ext}`
        : (ext === 'pdf' ? 'application/pdf'
          : ext === 'pptx' ? 'application/vnd.openxmlformats-officedocument.presentationml.presentation'
          : ext === 'docx' ? 'application/vnd.openxmlformats-officedocument.wordprocessingml.document'
          : ext === 'xlsx' ? 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet'
          : 'application/octet-stream');
      dataUrl = `data:${mime};base64,${b64}`;
      size = Math.floor(b64.length * 3 / 4);
    }
    if (!dataUrl) {
      alert('파일을 읽을 수 없습니다. 다시 시도하세요.');
      return;
    }
    state.attachedFiles.push({
      name,
      type: dataUrl.split(';')[0].slice(5),
      ext,
      data: dataUrl,
      size,
      rawBase64: dataUrl.split(',')[1] || '',
      _sourcePath: path,
    });
    if (typeof renderAttachedFiles === 'function') renderAttachedFiles();
    // 채팅에 컨텍스트 메시지 + 입력창 안내
    const modelHint = meta && meta.model
      ? ` (이전: ${meta.model.replace(/^us\.|^eu\.|^global\./, '').split('.').pop()})`
      : '';
    state.messages.push({
      role: 'system',
      content: `파일 "${name}"이(가) 첨부되었습니다${modelHint}. 어떻게 수정할지 입력해주세요.`,
    });
    if (typeof renderMessages === 'function') renderMessages();
    const input = document.getElementById('chat-input');
    if (input) {
      input.placeholder = `${name} 수정 지시를 입력하세요...`;
      input.focus();
    }
  } catch (err) {
    console.error('[preview-file:edit] failed:', err);
    alert(`수정 준비 실패: ${err.message || err}`);
  }
});

// 삭제 후 — 에디터에 열려 있는 탭이 있으면 닫기
document.addEventListener('preview-file:deleted', (e) => {
  const { path, name } = e.detail || {};
  if (!path) return;
  // 미디어 미리보기가 해당 파일이면 닫기
  try {
    if (typeof _hideMediaPreview === 'function') _hideMediaPreview();
  } catch {}
  // 일반 에디터 탭 닫기
  try {
    if (Array.isArray(state.openTabs)) {
      const idx = state.openTabs.findIndex(t => t.path === path || t.name === name);
      if (idx >= 0) {
        state.openTabs.splice(idx, 1);
        if (state.activeTab === path) {
          state.activeTab = state.openTabs[0]?.path || null;
        }
        if (typeof renderEditorTabs === 'function') renderEditorTabs();
      }
    }
  } catch (err) {
    console.warn('[preview-file:deleted] tab close failed:', err);
  }
});

// Auto-restore monaco when user clicks a text-file tab (handled by renderEditorTabs hook)
// Hook into existing tab switching by patching openFileInEditor to clear media wrapper
if (typeof openFileInEditor === 'function') {
  const _origOpen = openFileInEditor;
  // Cannot reassign const; instead, listen for tab clicks generally
  document.addEventListener('click', (e) => {
    const tab = e.target.closest('.editor-tab');
    if (tab && !tab.classList.contains('preview')) {
      _hideMediaPreview();
    }
  });
}

// <template-panel> 선택 이벤트 수신 — 활성 템플릿 갱신 (pptx-template-styling 요구사항 5.6)
// detail.templateId가 ''이면 "템플릿 없음" → 무템플릿 (요구사항 5.2 동작 보존)
document.addEventListener('template:selected', (e) => {
  const tid = (e && e.detail && e.detail.templateId) || '';
  state.activeTemplateId = tid;
});
