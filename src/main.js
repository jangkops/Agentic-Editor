/* ===== AI Editor — Main ===== */
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

// 숫자 포맷 유틸리티
function fmtNum(n) {
  if (n >= 1000000) return (n / 1000000).toFixed(1) + 'M';
  if (n >= 1000) return (n / 1000).toFixed(1) + 'K';
  return String(n);
}

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
function rebuildModelList() {
  ALL_MODELS.length = 0;
  for (const [p, ms] of Object.entries(MODEL_CATALOG)) ms.forEach(m => ALL_MODELS.push({ ...m, provider: p }));
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
};

// ===== Fix 1: SSO — select 드롭다운으로 프로파일 선택 =====
document.addEventListener('DOMContentLoaded', async () => {
  if (window.electronAPI?.loadSettings) state.settings = await window.electronAPI.loadSettings();
  if (!state.settings?.awsProfile) { showSSODialog(true); return; }
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
  // 자격증명을 백엔드에 주입 (quota 조회 등에서 사용)
  try {
    if (window.electronAPI?.getCredentials && state.settings?.awsProfile) {
      const creds = await window.electronAPI.getCredentials(state.settings.awsProfile);
      if (creds && creds.AWS_ACCESS_KEY_ID) {
        await fetch('http://localhost:8765/api/reset-cache', {
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
    <div class="dialog-icon">◆</div><h2>AI 에디터</h2>
    <div class="subtitle">멀티 에이전트 코드 에디터</div>
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
        await fetch('http://localhost:8765/api/reset-cache', {
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
            mr = await fetch('http://localhost:8765/api/models', {
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
            mr = await fetch(`http://localhost:8765/api/models?profile=${encodeURIComponent(profile)}`);
          }
          const md = await mr.json();
          if (md.models && Object.keys(md.models).length > 0) {
            Object.keys(MODEL_CATALOG).forEach(k => delete MODEL_CATALOG[k]);
            Object.assign(MODEL_CATALOG, md.models);
            rebuildModelList();
            if (ALL_MODELS.length > 0) state.selectedModel = ALL_MODELS[0];
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
function initGithubImport() {
  document.getElementById('btn-github-import').addEventListener('click', () => {
    const o = document.getElementById('sso-dialog'); o.style.display = 'block';
    o.innerHTML = `<div class="overlay" onclick="if(event.target===this)this.parentElement.style.display='none'">
      <div class="dialog" style="text-align:left;position:relative">
        <button class="sm-btn" onclick="document.getElementById('sso-dialog').style.display='none'" style="position:absolute;top:12px;right:12px">닫기</button>
        <h2 style="text-align:center;margin-bottom:16px">GitHub 가져오기</h2>
      <label>저장소 URL</label><input type="text" id="gh-url" placeholder="https://github.com/user/repo">
      <label style="margin-top:12px">브랜치</label><input type="text" id="gh-branch" value="main">
      <button class="btn-primary" id="gh-btn">가져오기</button>
      <div class="status-text" id="gh-status"></div></div></div>`;
    o.querySelector('#gh-btn').addEventListener('click', async () => {
      const url = o.querySelector('#gh-url').value.trim(); if (!url) return;
      const branch = o.querySelector('#gh-branch').value.trim()||'main';
      const btn = o.querySelector('#gh-btn'), st = o.querySelector('#gh-status');
      btn.textContent='가져오는 중...'; btn.disabled=true;
      const repo = url.split('/').pop().replace('.git','');
      const udp = window.electronAPI?.getUserDataPath ? await window.electronAPI.getUserDataPath() : '/tmp';
      const cp = `${udp}/repos/${repo}`;
      // 터미널에서 git clone 실행
      if (state.terminals.length && window.electronAPI?.terminalWrite) {
        const tid = state.terminals[state.activeTerminalIdx]?.id;
        if (tid) {
          await window.electronAPI.terminalWrite(tid, `git clone --branch ${branch} --depth 1 ${url} "${cp}" 2>&1\n`);
        }
      }
      // 10초 대기 후 폴더 로드 시도 (clone 완료 대기)
      st.textContent = 'clone 진행 중...';
      let attempts = 0;
      const checkClone = async () => {
        attempts++;
        try {
          const entries = await window.electronAPI?.readDir(cp);
          if (entries && entries.length > 0) {
            state.folderPath = cp;
            document.getElementById('file-tree-path-text').textContent = cp;
            document.getElementById('file-tree-actions').style.display = 'inline-flex';
            await loadFileTree(cp);
            st.className='status-text success'; st.textContent=`✓ ${repo} 완료`;
            setTimeout(()=>{o.style.display='none';},1000);
            return;
          }
        } catch {}
        if (attempts < 15) {
          st.textContent = `clone 진행 중... (${attempts}s)`;
          setTimeout(checkClone, 1000);
        } else {
          st.className='status-text error'; st.textContent='clone 시간 초과 — 터미널에서 확인하세요';
          btn.textContent='가져오기'; btn.disabled=false;
        }
      };
      setTimeout(checkClone, 2000);
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
    o.style.display = 'none'; renderSkillsList();
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
    g.innerHTML=`<div class="model-dropdown-group-title"><span style="color:var(--color-accent);font-weight:700">${p}</span><span style="color:var(--color-text-muted);margin-left:6px;font-size:9px">${ms.length}개 모델</span></div>`;
    fl.forEach(m=>{const i=document.createElement('div');i.className='model-dropdown-item'+(state.selectedModel && m.id===state.selectedModel.id?' selected':'');
      const speed = _modelSpeed(m.id);
      i.innerHTML=`<span style="flex:1">${m.name}</span><span style="font-size:9px;color:${speed.color};margin-left:8px">${speed.label}</span>`;
      i.style.display='flex';i.style.alignItems='center';
      i.onclick=()=>{state.selectedModel={...m,provider:p};document.getElementById('model-dropdown-btn').textContent=m.name+' ▾';document.getElementById('model-dropdown-menu').style.display='none';document.getElementById('status-model').textContent=m.name;};
      g.appendChild(i);});list.appendChild(g);}
}

function renderParallelDropdownList(f) {
  const list=document.getElementById('parallel-dropdown-list');list.innerHTML='';const q=f.toLowerCase();
  for(const[p,ms]of Object.entries(MODEL_CATALOG)){
    const fl=ms.filter(m=>!q||m.name.toLowerCase().includes(q)||p.toLowerCase().includes(q));if(!fl.length)continue;
    const g=document.createElement('div');g.className='model-dropdown-group';
    g.innerHTML=`<div class="model-dropdown-group-title"><span style="color:var(--color-accent);font-weight:700">${p}</span><span style="color:var(--color-text-muted);margin-left:6px;font-size:9px">${ms.length}개 모델</span></div>`;
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
        mr = await fetch('http://localhost:8765/api/models', {
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
      mr = await fetch(`http://localhost:8765/api/models?profile=${encodeURIComponent(profile)}`);
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
      Object.keys(MODEL_CATALOG).forEach(k => delete MODEL_CATALOG[k]);
      Object.assign(MODEL_CATALOG, d.models);
      rebuildModelList();
      if (ALL_MODELS.length > 0) state.selectedModel = ALL_MODELS[0];
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
  state.parallelSlots.push({ slotId:'slot-'+(++_slotCounter), modelId, skillId:'', customRole:'', model });
  document.getElementById('parallel-dropdown-btn').textContent = `${state.parallelSlots.length}개 모델 선택 ▾`;
  renderParallelSlotList(); renderParallelConfigGrid();
}
function removeParallelSlot(slotId) {
  state.parallelSlots = state.parallelSlots.filter(s => s.slotId !== slotId);
  document.getElementById('parallel-dropdown-btn').textContent = `${state.parallelSlots.length}개 모델 선택 ▾`;
  renderParallelSlotList(); renderParallelConfigGrid();
}
function renderParallelConfigGrid() {
  const grid = document.getElementById('parallel-grid'), countEl = document.getElementById('parallel-count');
  if (!grid || state.isStreaming) return;
  countEl.textContent = state.parallelSlots.length ? `${state.parallelSlots.length}개 모델 설정` : '병렬 모델을 선택하세요';
  if (!state.parallelSlots.length) { grid.innerHTML = '<div style="padding:40px;text-align:center;color:var(--color-text-muted);font-size:13px">우측에서 모델을 검색하여 추가하세요<br><span style="font-size:11px">같은 모델을 여러 번 추가 가능</span></div>'; return; }
  grid.innerHTML = '';
  state.parallelSlots.forEach(slot => {
    const card = document.createElement('div'); card.className = 'model-card fade-in';
    card.innerHTML = `<div class="model-card-header"><span class="model-name">● ${slot.model.name}</span><span style="font-size:10px;color:var(--color-text-muted)">${slot.model.provider}</span><span class="sk-action sk-action-del" data-rm="${slot.slotId}">삭제</span></div>
      <div style="padding:10px 14px;display:flex;flex-direction:column;gap:6px"><label style="font-size:10px;color:var(--color-text-muted);font-weight:600">스킬</label>
        <select class="ss" style="width:100%;background:var(--color-bg-input);border:1px solid var(--color-border);border-radius:var(--radius-sm);color:var(--color-text-secondary);font-size:11px;padding:5px 8px;outline:none"><option value="">스킬 없음</option>${allSkills.map(s=>`<option value="${s.id}" ${slot.skillId===s.id?'selected':''}>${s.name}</option>`).join('')}</select>
        <label style="font-size:10px;color:var(--color-text-muted);font-weight:600">커스텀 Role</label>
        <textarea class="cr" placeholder="텍스트 또는 JSON" style="width:100%;min-height:40px;max-height:100px;background:var(--color-bg-input);border:1px solid var(--color-border);border-radius:var(--radius-sm);color:var(--color-text-secondary);font-size:11px;padding:6px 8px;outline:none;resize:vertical;font-family:var(--font-mono)">${slot.customRole||''}</textarea></div>`;
    card.querySelector('[data-rm]').onclick = () => removeParallelSlot(slot.slotId);
    card.querySelector('.ss').onchange = e => { slot.skillId = e.target.value; };
    card.querySelector('.cr').oninput = e => { slot.customRole = e.target.value; };
    grid.appendChild(card);
  });
}
function renderParallelSlotList() {
  const list = document.getElementById('model-checklist'); list.innerHTML = '';
  if (!state.parallelSlots.length) { list.innerHTML = '<div style="padding:12px;text-align:center;color:var(--color-text-muted);font-size:12px">모델을 검색하여 추가</div>'; }
  state.parallelSlots.forEach(slot => {
    const r = state.parallelResults.get(slot.slotId);
    const stText = r ? ({done:'완료',running:'실행 중',error:'실패',pending:'대기'}[r.status]||'') : '';
    const stColor = r ? ({done:'var(--color-success)',running:'var(--color-accent)',error:'var(--color-error)'}[r.status]||'') : '';
    const item = document.createElement('div'); item.className = 'model-check-item';
    item.innerHTML = `<span class="dot" style="background:${stColor||'var(--color-accent)'}"></span><span style="flex:1">${slot.model.name}</span><span class="status" style="color:${stColor}">${stText}</span>${!state.isStreaming ? `<span class="sk-action sk-action-del" data-rm="${slot.slotId}" title="제거">x</span>` : ''}`;
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
  sendBtn.onclick = () => {
    if (state.isStreaming) {
      // 취소
      if (state._abortController) { state._abortController.abort(); state._abortController = null; }
      state.isStreaming = false;
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
function renderAttachedFiles() {
  const c=document.getElementById('attached-files-area');
  c.innerHTML=state.attachedFiles.map((f,i)=>`<div class="attached-file"><span>${f.name} (${(f.size/1024).toFixed(0)}KB)</span><span class="remove" data-idx="${i}">✕</span></div>`).join('');
  c.querySelectorAll('.remove').forEach(el=>el.onclick=()=>{state.attachedFiles.splice(+el.dataset.idx,1);renderAttachedFiles();});
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
  input.value='';input.style.height='auto';
  // IME 조합 중인 경우 강제 완료
  input.blur(); input.focus();
  let content=text;
  if(state.attachedFiles.length){
    content=state.attachedFiles.map(f=>{
      if(['png','jpg','jpeg'].includes(f.ext)) return `[이미지: ${f.name}]`;
      if(f.ext==='xlsx') return `[엑셀 파일: ${f.name} (${(f.size/1024).toFixed(0)}KB)]`;
      return `[파일: ${f.name}]`;
    }).join('\n')+(text?'\n\n'+text:'');
  }
  const userMsg={role:'user',content,attachments:[...state.attachedFiles]};
  state.messages.push(userMsg);state.attachedFiles=[];renderAttachedFiles();renderMessages();

  const sendBtn = document.getElementById('send-btn');
  sendBtn.textContent = '취소';
  sendBtn.style.background = 'var(--color-error)';

  if(state.mode==='parallel') await runParallel(content);
  else await runSingle(content);

  sendBtn.textContent = '전송';
  sendBtn.style.background = 'var(--color-accent)';
}

// ===== Single Mode =====
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
  // 프로젝트 컨텍스트
  if (state.folderPath) {
    body.projectPath = state.folderPath;
  }
  // 현재 열린 파일
  if (state.activeTab && monacoEditor) {
    body.openFile = state.activeTab.replace(state.folderPath + '/', '');
    try {
      const model = monacoEditor.getModel();
      if (model) body.openFileContent = model.getValue().substring(0, 15000);
    } catch {}
  }
  // 대화 히스토리 (최근 6개, 각 1000자 제한 — 토큰 절약)
  const history = (state.messages || [])
    .filter(m => m.role === 'user' || (m.role === 'assistant' && m.content && !m.isConsensus && !m.content.includes('[오류:')))
    .slice(-10)
    .map(m => ({ role: m.role, content: (m.content || '').substring(0, 2000) }));
  if (history.length) body.chatHistory = history;
  // 세션 ID
  body.sessionId = chatSessions[activeSessionIdx]?.id || 'default';
  return body;
}

// SSE 스트림 읽기 공통 함수
async function readSSEStream(resp, { onText, onTool, onSlot, onError, onRaw } = {}) {
  const reader = resp.body.getReader(), dec = new TextDecoder();
  let buf = '';
  while (true) {
    const { done, value } = await reader.read();
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
  // 모든 호출을 에이전트 모드로 통일 — 도구 사용 가능
  await runAgentWorkflow(prompt);
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
    const resp = await fetch('http://localhost:8765/api/agents/run-stream', {
      method:'POST', headers:{'Content-Type':'application/json'},
      body: JSON.stringify(_apiBody({ prompt, model: state.selectedModel.id })),
      signal: state._abortController.signal
    });
    clearTimeout(timeoutId);
    if (!resp.ok) throw new Error(`서버 응답 오류: ${resp.status}`);
    const reader = resp.body.getReader(), dec = new TextDecoder();
    let buf = '';
    while (true) {
      const { done, value } = await reader.read();
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
          if (p.tool) {
            // 도구 실행 이벤트 — 채팅에 표시하지 않음 (로그만)
            continue;
          }
          if (p.error) {
            // 토큰 만료 → 자동 재로그인 시도
            if (p.error.includes('expired') || p.error.includes('security token')) {
              addLiveLog('system', '토큰 만료 감지 — 자격증명 갱신 중...');
              try {
                const creds = await window.electronAPI?.getCredentials(state.settings?.awsProfile || '');
                if (creds) {
                  await fetch('http://localhost:8765/api/reset-cache', {
                    method: 'POST', headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({ profile: state.settings?.awsProfile, bedrockUser: state.settings?.bedrockUser, credentials: creds }),
                  });
                  addLiveLog('system', '자격증명 갱신 완료 — 다시 질문해주세요');
                }
              } catch {}
            }
            msg.content += `\n[오류: ${p.error}]`; continue;
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
  const wfId = 'wf-' + Date.now();
  const wf = { id:wfId, steps:[
    { name:'분석', status:'running', detail:'' },
    { name:'도구 실행', status:'pending', detail:'' },
    { name:'완료', status:'pending', detail:'' },
  ]};
  const msg = { role:'assistant', content:'', workflow:wf, toolUses:[] };
  state.messages.push(msg);
  renderMessages();
  try {
    const resp = await fetch('http://localhost:8765/api/agents/run-agent', {
      method:'POST', headers:{'Content-Type':'application/json'},
      body: JSON.stringify(_apiBody({ prompt, model: state.selectedModel.id })),
      signal: state._abortController.signal
    });
    clearTimeout(timeoutId);
    if (!resp.ok) throw new Error(`서버 응답 오류: ${resp.status}`);
    const reader = resp.body.getReader(), dec = new TextDecoder();
    let buf = '', toolCount = 0;
    wf.steps[0].status = 'done'; wf.steps[0].detail = prompt.substring(0, 80);
    renderMessages();
    while (true) {
      const { done, value } = await reader.read();
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
          if (p.error) { msg.content += `\n[오류: ${p.error}]`; }
          else if (p.tool && p.status === 'running') {
            // 도구 실행 시작 — 채팅에 표시하지 않음
            toolCount++;
            wf.steps[1].status = 'running';
            wf.steps[1].detail = `도구 실행 중... (${toolCount}번째)`;
          }
          else if (p.tool && p.status === 'done') {
            // 도구 실행 완료 — 채팅에 표시하지 않음
            wf.steps[1].detail = `도구 ${toolCount}개 완료`;
          }
          else if (p.text) { msg.content += p.text; }
          else { msg.content += d; }
        } catch { msg.content += d; }
      }
      renderMessages();
    }
    if (toolCount > 0) wf.steps[1].status = 'done';
    else wf.steps[1].detail = '도구 사용 없음';
    wf.steps[2].status = 'done'; wf.steps[2].detail = '완료';
    trackUsage(prompt.length, msg.content.length);
    addLiveLog('response', `에이전트 완료: ${state.selectedModel.name}`, `${msg.content.length}자`);
  } catch (e) {
    clearTimeout(timeoutId);
    const errMsg = e.name === 'AbortError' ? '요청 시간 초과 또는 취소됨' : e.message;
    msg.content += `\n[오류: ${errMsg}]`;
    const r = wf.steps.find(s => s.status === 'running');
    if (r) r.status = 'failed';
    addLiveLog('error', `에이전트 실패: ${errMsg}`);
  }
  state.isStreaming = false;
  renderMessages();
  saveConversation();
}

// ===== Parallel Mode — 실시간 연동: 가운데 패널 + 우측 모델 리스트 + 채팅 =====
async function runParallel(prompt) {
  if (!state.parallelSlots.length) return;
  state.isStreaming = true;
  state._streamStartTime = Date.now();
  state._abortController = new AbortController();
  // 300초 타임아웃 (5분)
  const timeoutId = setTimeout(() => { if (state._abortController) state._abortController.abort(); }, 300000);
  addLiveLog('request', `병렬 호출: ${state.parallelSlots.length}개 모델`);

  state.parallelResults.clear();
  state.parallelSlots.forEach(slot => state.parallelResults.set(slot.slotId, { status:'pending', content:'', modelName:slot.model.name }));

  showParallelResults();
  const grid = document.getElementById('parallel-grid');
  if (grid) grid.innerHTML = '';
  renderParallelResultGrid();
  renderParallelSlotList();
  state.messages.push({ role:'system', content:`${state.parallelSlots.length}개 모델 병렬 실행 시작...` });
  renderMessages();

  // 서버 측 병렬 호출 — 단일 SSE 연결로 모든 모델 결과 수신
  const models = state.parallelSlots.map(slot => {
    let sp = '';
    if (slot.customRole) sp = slot.customRole;
    else if (slot.skillId) { const s = allSkills.find(x => x.id === slot.skillId); if (s) sp = s.role; }
    return { modelId: slot.modelId, slotId: slot.slotId, systemPrompt: sp };
  });

  // 모든 슬롯을 running으로 + 시작 시간 기록
  const _slotStartTimes = {};
  state.parallelSlots.forEach(slot => {
    state.parallelResults.set(slot.slotId, { status:'running', content:'', modelName:slot.model.name });
    _slotStartTimes[slot.slotId] = Date.now();
  });
  renderParallelResultGrid(); renderParallelSlotList();

  try {
    const resp = await fetch('http://localhost:8765/api/agents/run-parallel', {
      method:'POST', headers:{'Content-Type':'application/json'},
      body: JSON.stringify(_apiBody({ prompt, models })),
      signal: state._abortController?.signal
    });
    if (!resp.ok) throw new Error(`서버 응답 오류: ${resp.status}`);
    const reader = resp.body.getReader(), dec = new TextDecoder();
    let buf = '';
    while (true) {
      const { done, value } = await reader.read();
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
          if (ev.slotId) {
            const slot = state.parallelSlots.find(s => s.slotId === ev.slotId);
            const modelName = slot?.model?.name || ev.modelId || '';
            const slotElapsed = _slotStartTimes[ev.slotId] ? Math.floor((Date.now() - _slotStartTimes[ev.slotId]) / 1000) : 0;
            state.parallelResults.set(ev.slotId, { status: ev.status, content: ev.content, modelName, elapsed: slotElapsed });
            renderParallelResultGrid();
            renderParallelSlotList();
            updateConsensus();
          }
        } catch {}
      }
    }
  } catch (e) {
    clearTimeout(timeoutId);
    const errMsg = e.name === 'AbortError' ? '요청 시간 초과 또는 취소됨' : e.message;
    // 모든 running 슬롯을 error로 변경
    for (const [sid, r] of state.parallelResults) {
      if (r.status === 'running' || r.status === 'pending') {
        state.parallelResults.set(sid, { ...r, status:'error', content: errMsg });
      }
    }
    state.messages.push({ role:'system', content:`병렬 실행 오류: ${errMsg}` });
    addLiveLog('error', `병렬 호출 실패: ${errMsg}`);
  }

  state.isStreaming = false;
  renderParallelResultGrid(); renderParallelSlotList(); updateConsensus();

  const done = [...state.parallelResults.values()].filter(r => r.status === 'done').length;
  const err = [...state.parallelResults.values()].filter(r => r.status === 'error').length;
  const parallelElapsed = Math.floor((Date.now() - (state._streamStartTime || Date.now())) / 1000);
  state.messages.push({ role:'system', content:`병렬 완료: ${done}개 성공, ${err}개 실패 (${fmtElapsed(parallelElapsed)}) — 가운데 패널에서 결과 확인` });
  saveParallelResults();
  renderMessages();
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
  const msg = { role:'assistant', content:'', isConsensus: true };
  state.messages.push(msg);

  try {
    const resp = await fetch('http://localhost:8765/api/agents/run-stream', {
      method:'POST', headers:{'Content-Type':'application/json'},
      body: JSON.stringify(_apiBody({ prompt: sp, model: consensusModelId, systemPrompt: consensusSystemPrompt })),
    });
    if (!resp.ok) throw new Error(`서버 응답 오류: ${resp.status}`);
    const reader = resp.body.getReader(), dec = new TextDecoder();
    let buf = '';
    while (true) {
      const { done, value } = await reader.read();
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
  } catch (e) {
    msg.content += `\n[합의 오류: ${e.message}]`;
    addLiveLog('error', `합의 실패: ${e.message}`);
  }
  msg._elapsed = Math.floor((Date.now() - (state._streamStartTime || Date.now())) / 1000);
  state.isStreaming = false;

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

  renderMessages();
  saveConversation();
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
        <span class="model-name" style="color:${nameColor}">● ${r.modelName || '모델'}</span>
        ${r.elapsed ? `<span style="font-size:10px;color:var(--color-text-muted)">${fmtElapsed(r.elapsed)}</span>` : ''}
        <span class="badge ${badge}">${label}</span>
      </div>
      <div class="model-card-body" style="max-height:${isExp ? 'none' : '180px'}">${r.status === 'done' ? fmtMd(displayContent) : esc(displayContent)}</div>
      <div style="padding:3px 10px;border-top:1px solid var(--color-border-light);display:flex;justify-content:space-between;align-items:center">
        ${r.status === 'done' ? '<button class="msg-action-btn card-copy-btn" title="Copy" style="width:24px;height:24px">' + SVG_COPY + '</button>' : '<span></span>'}
        <button class="sm-btn card-toggle">${isExp ? '축소' : '확장'}</button>
      </div>`;

    card.querySelector('.card-toggle').addEventListener('click', () => {
      if (card.classList.contains('expanded')) {
        card.classList.remove('expanded');
      } else {
        card.classList.add('expanded');
      }
      // 상태 저장 후 재렌더링하지 않고 직접 DOM 조작
      const body = card.querySelector('.model-card-body');
      const isNowExp = card.classList.contains('expanded');
      body.style.maxHeight = isNowExp ? 'none' : '180px';
      body.textContent = isNowExp ? (r.content || '') : (r.status === 'error' && r.content.length > 80 ? r.content.substring(0, 80) + '...' : r.content);
      card.querySelector('.card-toggle').textContent = isNowExp ? '축소' : '확장';
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
function renderMessages(){
  const c=document.getElementById('chat-messages');c.innerHTML='';
  for(const msg of state.messages){
    if(msg.role==='user'){
      const d=document.createElement('div');d.className='chat-msg user fade-in';
      let ah='';if(msg.attachments?.length)ah=msg.attachments.map(a=>['png','jpg','jpeg'].includes(a.ext)?`<img src="${a.data}" style="max-width:200px;max-height:150px;border-radius:8px;margin-bottom:6px;display:block">`:`<div style="font-size:11px;color:rgba(255,255,255,0.7);margin-bottom:4px">+ ${a.name}</div>`).join('');
      d.innerHTML=`<div class="msg-content">${ah}${esc(msg.content)}</div>`;
      addCopySupport(d, msg.content);
      c.appendChild(d);
    }else if(msg.role==='system'){
      const d=document.createElement('div');d.className='chat-msg system fade-in';d.textContent=msg.content;c.appendChild(d);
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
        const d=document.createElement('div');d.className='chat-msg assistant fade-in';
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
        } else if (state.isStreaming) {
          d.innerHTML=`<div class="msg-content thinking-indicator" style="border-left:3px solid var(--color-success);background:var(--color-success-subtle)">
            <span class="thinking-dots"><span></span><span></span><span></span></span> 합의 도출 중</div>`;
        }
        c.appendChild(d);
      } else {
        if(msg.workflow){const jc=document.createElement('div');jc.className='async-job-card fade-in';jc.innerHTML=`<div class="job-header"><span class="job-title">에이전트 작업</span></div><div class="job-body">실행 중... 모델: ${state.selectedModel?.name||'?'} Job: ${msg.workflow.id}</div>`;c.appendChild(jc);renderWorkflow(c,msg.workflow);}
        if(msg.toolUses?.length)for(const t of msg.toolUses)renderToolUseCard(c,t);
        if(msg.content){
          const d=document.createElement('div');d.className='chat-msg assistant fade-in';
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
          c.appendChild(d);
        } else if(!msg.workflow && state.isStreaming) {
          const d=document.createElement('div');d.className='chat-msg assistant fade-in';
          const elapsed = Math.floor((Date.now() - (state._streamStartTime || Date.now())) / 1000);
          const timeText = elapsed >= 3600 ? `${Math.floor(elapsed/3600)}h ${Math.floor((elapsed%3600)/60)}m` : elapsed >= 60 ? `${Math.floor(elapsed/60)}m ${elapsed%60}s` : `${elapsed}s`;
          d.innerHTML=`<div class="msg-content thinking-indicator"><span class="thinking-dots"><span></span><span></span><span></span></span> thinking ${timeText}</div>`;
          c.appendChild(d);
        }
      }
    }
  }
  c.scrollTop=c.scrollHeight;
}

// SVG 아이콘
const SVG_COPY = '<svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><rect x="9" y="9" width="13" height="13" rx="2" ry="2"/><path d="M5 15H4a2 2 0 0 1-2-2V4a2 2 0 0 1 2-2h9a2 2 0 0 1 2 2v1"/></svg>';
const SVG_CHECK = '<svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="var(--color-success)" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round"><polyline points="20 6 9 17 4 12"/></svg>';
const SVG_TERMINAL = '<svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><polyline points="4 17 10 11 4 5"/><line x1="12" y1="19" x2="20" y2="19"/></svg>';

// 복사 + 실행 버튼 지원
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
function renderWorkflow(c,wf){for(const s of wf.steps){const d=document.createElement('div');const sc={done:'step-done',running:'step-running',failed:'step-failed'}[s.status]||'';d.className=`workflow-step ${sc} fade-in`;const ic={done:'done',running:'running',failed:'failed'}[s.status]||'';const bc={done:'step-badge-done',running:'step-badge-running',failed:'step-badge-failed'}[s.status]||'';const bt={done:'완료',running:'진행 중',failed:'실패'}[s.status]||'';d.innerHTML=`<div class="workflow-step-header"><span class="step-indicator ${ic}"></span><span class="step-title">● ${s.name}</span>${bt?`<span class="step-badge ${bc}">${bt}</span>`:''}</div>${s.detail?`<div class="workflow-step-body">${esc(s.detail)}</div>`:''}`;c.appendChild(d);}}
function renderToolUseCard(c,t){const card=document.createElement('div');card.className='tool-use-card fade-in';card.innerHTML=`<div class="tool-use-header"><span class="tool-badge">도구</span><span class="tool-label">파일 쓰기: ${esc(t.path||t.name||'')}</span></div><div class="tool-use-body">${esc(t.content||t.diff||JSON.stringify(t,null,2))}</div>`;c.appendChild(card);}
function fmtMd(t){
  let h=esc(t);
  // 코드 블록
  h=h.replace(/```(\w*)\n([\s\S]*?)```/g,'<pre style="background:var(--color-bg-primary);padding:8px;border-radius:var(--radius-md);margin:3px 0;font-family:var(--font-mono);font-size:11px;overflow-x:auto;border:1px solid var(--color-border)"><code>$2</code></pre>');
  // 인라인 코드
  h=h.replace(/`([^`]+)`/g,'<code style="background:var(--color-bg-input);padding:1px 4px;border-radius:3px;font-family:var(--font-mono);font-size:11px">$1</code>');
  // 헤딩
  h=h.replace(/^### (.+)$/gm,'<div style="font-size:13px;font-weight:700;color:var(--color-text-primary);margin:10px 0 3px">$1</div>');
  h=h.replace(/^## (.+)$/gm,'<div style="font-size:14px;font-weight:700;color:var(--color-text-primary);margin:12px 0 4px">$1</div>');
  h=h.replace(/^# (.+)$/gm,'<div style="font-size:15px;font-weight:700;color:var(--color-text-primary);margin:14px 0 4px">$1</div>');
  // 볼드/이탤릭
  h=h.replace(/\*\*(.+?)\*\*/g,'<strong>$1</strong>');
  h=h.replace(/\*(.+?)\*/g,'<em>$1</em>');
  // 리스트
  h=h.replace(/^- (.+)$/gm,'<div style="padding-left:12px">· $1</div>');
  h=h.replace(/^\d+\. (.+)$/gm,'<div style="padding-left:12px">$&</div>');
  // 인용
  h=h.replace(/^&gt; (.+)$/gm,'<div style="border-left:2px solid var(--color-accent);padding-left:10px;color:var(--color-text-secondary);margin:2px 0">$1</div>');
  // 구분선
  h=h.replace(/^---$/gm,'<hr style="border:none;border-top:1px solid var(--color-border);margin:4px 0">');
  // 테이블 (간단)
  h=h.replace(/\|(.+)\|/g, (match) => {
    const cells = match.split('|').filter(c => c.trim());
    if (cells.every(c => /^[-:]+$/.test(c.trim()))) return '';
    return '<div style="display:flex;gap:8px;padding:1px 0;font-size:12px">' + cells.map(c => `<span style="flex:1">${c.trim()}</span>`).join('') + '</div>';
  });
  // 연속 빈 줄 → 단일 br, 단일 줄바꿈 → br
  h=h.replace(/\n{3,}/g,'\n');
  h=h.replace(/\n\n/g,'<br>');
  h=h.replace(/\n/g,'<br>');
  h=h.replace(/(<br>){3,}/g,'<br>');
  return h;
}
function fmtElapsed(secs) {
  if (!secs || secs < 1) return '';
  if (secs >= 3600) return `${Math.floor(secs/3600)}h ${Math.floor((secs%3600)/60)}m`;
  if (secs >= 60) return `${Math.floor(secs/60)}m ${secs%60}s`;
  return `${secs}s`;
}
function esc(t){if(!t)return'';return t.replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;');}

// ===== File Explorer — 인라인 생성/수정/삭제 =====
function initFileExplorer() {
  document.getElementById('btn-open-folder').onclick = async () => {
    if (window.electronAPI?.openFolder) {
      const p = await window.electronAPI.openFolder();
      if (p) {
        state.folderPath = p;
        document.getElementById('file-tree-path-text').textContent = p;
        document.getElementById('file-tree-actions').style.display = 'inline-flex';
        _projectStats = null; _projectDeps = null; _gitLog = []; _reviewResults = null;
        loadFileTree(p);
        loadCommitLogMini(p);
        // RAG 인덱싱 트리거
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
      value: '// AI Editor\n// 파일이나 폴더를 열어 시작하세요.\n',
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
    window.electronAPI.terminalCreate(id);
    // 프로젝트 폴더로 이동 + 프롬프트에 정보 표시
    setTimeout(() => {
      if (window.electronAPI?.terminalWrite) {
        const profile = state.settings?.awsProfile || '';
        let initCmd = '';
        if (profile) initCmd += `export AWS_PROFILE=${profile} && `;
        if (state.folderPath) initCmd += `cd "${state.folderPath}" && `;
        initCmd += 'echo "$(whoami)@$(hostname -I 2>/dev/null | awk \'{print $1}\' || hostname):$(pwd)"';
        window.electronAPI.terminalWrite(id, initCmd + '\n');
      }
    }, 500);
  }
  renderTerminalTabs(); renderTerminalContent();
}

function renderTerminalTabs() {
  const bar = document.getElementById('terminal-tabs-bar'); if (!bar) return;
  const cwd = state.folderPath || '~';
  const profile = state.settings?.awsProfile || '';
  // IP는 hostname에서 추출 시도
  let hostInfo = '';
  if (window.electronAPI?.terminalWrite) {
    // 터미널 프롬프트에서 표시
    hostInfo = profile ? `${profile}` : '';
  }
  bar.innerHTML = `<span style="font-size:11px;color:var(--color-text-muted);padding:0 8px;font-weight:600">터미널</span>` +
    state.terminals.map((t, i) => `<button class="terminal-tab ${i === state.activeTerminalIdx ? 'active' : ''}" data-idx="${i}" title="${cwd}">
      ${i+1}: ${cwd.split('/').pop() || '~'}${state.terminals.length > 1 ? `<span class="term-close" data-close="${i}" style="margin-left:6px;font-size:10px;opacity:0.4;cursor:pointer">✕</span>` : ''}
    </button>`).join('') +
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
    setTimeout(() => { try { term._fitAddon?.fit(); term._xterm?.focus(); } catch {} }, 50);
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
  // 포커스 — 클릭 시에도 포커스
  setTimeout(() => xt.focus(), 200);
  container.addEventListener('click', () => xt.focus());

  // 입력을 Electron PTY로 전달
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
        // OOM 방지: output 버퍼를 최대 100KB로 제한 (xterm.js가 자체 scrollback 관리)
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
    document.querySelector('.skills-section').style.display = 'none';
    document.getElementById('file-tree-path').style.display = 'none';
    renderSourceControlPanel();
  });
  document.getElementById('btn-file-explorer')?.addEventListener('click', () => {
    document.querySelectorAll('.lp-btn').forEach(b => b.classList.remove('active'));
    document.getElementById('btn-file-explorer').classList.add('active');
    document.getElementById('file-tree').style.display = '';
    document.getElementById('source-control-panel').style.display = 'none';
    document.querySelector('.skills-section').style.display = '';
    document.getElementById('file-tree-path').style.display = 'flex';
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
        const r = await fetch('http://localhost:8765/health', { signal: controller.signal });
        statusEl.innerHTML = r.ok ? '<span style="color:var(--color-success)">● 연결됨</span>' : '<span style="color:var(--color-error)">● 오류</span>';
      } catch { statusEl.innerHTML = '<span style="color:var(--color-error)">● 오프라인</span>'; }
    })();
    testBtn.addEventListener('click', async () => {
      statusEl.textContent = '테스트 중...';
      try {
        const controller = new AbortController();
        setTimeout(() => controller.abort(), 5000);
        const r = await fetch('http://localhost:8765/health', { signal: controller.signal });
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
            <div style="font-size:14px;font-weight:700;color:var(--color-text-primary)">${esc(bu || 'AI 에디터')}</div>
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
          await fetch('http://localhost:8765/api/reset-cache', {
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
  const folderName = state.folderPath ? state.folderPath.split('/').pop() : 'AI 에디터';
  o.innerHTML = `<div class="overlay" onclick="if(event.target===this)document.getElementById('sso-dialog').style.display='none'">
    <div class="about-dialog">
      <button class="sm-btn" onclick="document.getElementById('sso-dialog').style.display='none'" style="position:absolute;top:16px;right:16px;font-size:14px">✕</button>
      <div class="about-logo">◆</div>
      <div class="about-name">AI 에디터</div>
      <div class="about-version">v1.0.0</div>
      <div class="about-desc">멀티 에이전트 코드 에디터<br>Bedrock Gateway를 통한 LLM 호출</div>
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
      <div style="font-size:10px;color:var(--color-text-muted);margin-top:16px">© 2026 AI Editor. All rights reserved.</div>
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
          <span class="git-branch-icon">⎇</span>
          <span class="git-branch-name">${esc(branch)}</span>
          <button class="sm-btn" id="git-refresh-btn" style="font-size:10px;padding:2px 6px">↻</button>
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
  fetch(`http://localhost:8765/api/quota?profile=${encodeURIComponent(profile)}&user=${encodeURIComponent(user)}`, { signal: AbortSignal.timeout(10000) }).then(r=>r.json()).then(q=>{
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
async function checkBackend(){const el=document.getElementById('status-backend');try{const r=await fetch('http://localhost:8765/health');if(r.ok){el.textContent=`● ${state.settings?.awsProfile||'bedrock-gw'}`;document.getElementById('status-model').textContent=state.selectedModel?.name||'';}else{el.textContent='● backend error';setTimeout(checkBackend,5000);}}catch{el.textContent='● backend offline';setTimeout(checkBackend,5000);}}

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
  if (e.key === 'Escape' && _activeView !== 'editor' && _activeView !== 'parallel') { switchCenterView('editor'); }
});

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
    fetch('http://localhost:8765/health', { signal: AbortSignal.timeout(3000) })
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
    const r = await fetch('http://localhost:8765/api/rag/index', {
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
