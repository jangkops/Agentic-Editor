/* ===== Center View Tab System ===== */
/* 구조 | 의존성 | 통계 | 검색 | GIT 뷰 */

const EXT_COLORS = {
  js:'#f1e05a', ts:'#3178c6', py:'#3572a5', html:'#e34c26', css:'#563d7c',
  json:'#999', md:'#083fa1', yml:'#cb171e', yaml:'#cb171e', sh:'#89e051',
  txt:'#aaa', png:'#a97bff', jpg:'#a97bff', svg:'#ff9800', woff2:'#f06',
};

let _projectStats = null;
let _projectDeps = null;
let _gitLog = [];
let _searchResults = [];
let _activeView = 'editor';
let _activeStatsTab = 'overview';

function initCenterViews() {
  const tabs = document.getElementById('center-view-tabs');
  if (!tabs) return;
  tabs.querySelectorAll('.cv-tab').forEach(tab => {
    tab.addEventListener('click', () => switchCenterView(tab.dataset.view));
  });
}

function switchCenterView(view) {
  _activeView = view;
  // 탭 활성화
  document.querySelectorAll('.cv-tab').forEach(t => t.classList.toggle('active', t.dataset.view === view));
  // 뷰 전환
  document.getElementById('editor-area').style.display = view === 'editor' ? 'flex' : 'none';
  document.getElementById('parallel-results').classList.toggle('visible', view === 'parallel');
  ['structure','dependencies','stats','search','git','review','consensus'].forEach(v => {
    const el = document.getElementById('view-' + v);
    if (el) el.style.display = v === view ? '' : 'none';
  });
  // 탭 정보 업데이트
  const info = document.getElementById('cv-tab-info');
  if (info && state.folderPath) {
    info.textContent = state.folderPath.split('/').pop();
  }
  // 뷰 로드
  if (view === 'stats') loadStatsView();
  else if (view === 'structure') loadStructureView();
  else if (view === 'dependencies') loadDependenciesView();
  else if (view === 'search') loadSearchView();
  else if (view === 'git') loadGitView();
  else if (view === 'review') loadReviewView();
  else if (view === 'editor' && typeof monacoEditor !== 'undefined' && monacoEditor) {
    requestAnimationFrame(() => { requestAnimationFrame(() => { monacoEditor.layout(); }); });
  }
}

// ===== 통계 뷰 =====
async function loadStatsView() {
  const container = document.getElementById('view-stats');
  if (!state.folderPath) {
    container.innerHTML = '<div style="text-align:center;padding:60px;color:var(--color-text-muted)">폴더를 열어 프로젝트를 분석하세요</div>';
    return;
  }
  if (!_projectStats) {
    container.innerHTML = '<div style="text-align:center;padding:60px"><div class="spinner"></div><div style="margin-top:12px;color:var(--color-text-muted);font-size:12px">프로젝트 분석 중...</div></div>';
    _projectStats = await window.electronAPI?.analyzeProject(state.folderPath);
  }
  if (!_projectStats) { container.innerHTML = '<div style="padding:40px;text-align:center;color:var(--color-text-muted)">분석 실패</div>'; return; }
  renderStatsView(container);
}

function renderStatsView(container) {
  const s = _projectStats;
  container.innerHTML = `
    <div style="display:flex;align-items:center;gap:8px;margin-bottom:16px">
      <div class="stats-subtabs" id="stats-subtabs" style="margin-bottom:0;flex:1">
        <button class="stats-subtab active" data-tab="overview">개요</button>
        <button class="stats-subtab" data-tab="quality">품질·생산성</button>
        <button class="stats-subtab" data-tab="tokens">토큰 비용</button>
        <button class="stats-subtab" data-tab="contributors">기여자</button>
        <button class="stats-subtab" data-tab="team">팀 통계</button>
        <button class="stats-subtab" data-tab="insight">종합 인사이트</button>
      </div>
      <button class="sm-btn" id="stats-refresh-btn" style="font-size:10px;padding:3px 8px;flex-shrink:0">↻</button>
    </div>
    <div id="stats-content"></div>`;
  container.querySelectorAll('.stats-subtab').forEach(tab => {
    tab.addEventListener('click', () => {
      _activeStatsTab = tab.dataset.tab;
      container.querySelectorAll('.stats-subtab').forEach(t => t.classList.toggle('active', t.dataset.tab === _activeStatsTab));
      renderStatsSubTab();
    });
  });
  container.querySelector('#stats-refresh-btn')?.addEventListener('click', async () => {
    _projectStats = null;
    loadStatsView();
  });
  renderStatsSubTab();
}

function renderStatsSubTab() {
  const el = document.getElementById('stats-content');
  if (!el) return;
  if (_activeStatsTab === 'overview') renderStatsOverview(el);
  else if (_activeStatsTab === 'quality') renderStatsQuality(el);
  else if (_activeStatsTab === 'tokens') renderStatsTokens(el);
  else if (_activeStatsTab === 'contributors') renderStatsContributors(el);
  else if (_activeStatsTab === 'team') renderStatsTeam(el);
  else if (_activeStatsTab === 'insight') renderStatsInsight(el);
}

function renderStatsOverview(el) {
  const s = _projectStats;
  const totalFiles = s.totalFiles;
  const totalLines = s.totalLines;
  // 확장자 분포
  const exts = Object.entries(s.extensions).sort((a,b) => b[1] - a[1]);
  const topExts = exts.slice(0, 8);
  const extTotal = exts.reduce((a,b) => a + b[1], 0);
  const extBars = topExts.map(([ext, count]) => {
    const pct = ((count / extTotal) * 100).toFixed(1);
    const color = EXT_COLORS[ext] || '#666';
    return { ext, count, pct, color };
  });
  const extBarHtml = extBars.map(e => `<div class="ext-bar-segment" style="width:${e.pct}%;background:${e.color}" title=".${e.ext} ${e.pct}%"></div>`).join('');
  const extLegendHtml = extBars.map(e => `<span class="ext-legend-item"><span class="ext-legend-dot" style="background:${e.color}"></span>.${e.ext} ${e.pct}% ${fmtNum(e.count)}개</span>`).join('');

  // 역할별 분류
  const roles = [
    { icon:'', name:'소스 파일', count:s.roles.source, color:'var(--color-accent)' },
    { icon:'', name:'설정', count:s.roles.config, color:'var(--color-warning)' },
    { icon:'', name:'문서', count:s.roles.docs, color:'var(--color-success)' },
    { icon:'', name:'테스트', count:s.roles.test, color:'var(--color-error)' },
    { icon:'', name:'스타일시트', count:s.roles.style, color:'#a97bff' },
    { icon:'', name:'에셋', count:s.roles.asset, color:'#f06' },
  ];

  // 프로젝트 정보
  const folderName = state.folderPath.split('/').pop();

  el.innerHTML = `
    <div class="stats-cards">
      <div class="stat-card"><div class="stat-value">${fmtNum(totalLines)}</div><div class="stat-label">라인</div></div>
      <div class="stat-card"><div class="stat-value">${totalFiles}</div><div class="stat-label">총 파일</div></div>
      <div class="stat-card"><div class="stat-value">${s.totalDirs}</div><div class="stat-label">폴더</div></div>
      <div class="stat-card"><div class="stat-value">${s.todos}</div><div class="stat-label">TODO</div></div>
    </div>

    <div class="stats-section">
      <div class="stats-section-title">파일 확장자별 코드 분포</div>
      <div class="ext-bar-container">
        <div class="ext-bar">${extBarHtml}</div>
        <div class="ext-legend">${extLegendHtml}</div>
      </div>
    </div>

    <div class="stats-section">
      <div class="stats-section-title">역할별 분류</div>
      <div class="role-cards">
        ${roles.map(r => `<div class="role-card"><div class="role-dot" style="width:10px;height:10px;border-radius:50%;background:${r.color};flex-shrink:0"></div><div class="role-info"><div class="role-count">${r.count}</div><div class="role-name">${r.name}</div></div></div>`).join('')}
      </div>
    </div>

    <div style="display:grid;grid-template-columns:1fr 1fr;gap:16px">
      <div class="stats-section">
        <div class="stats-section-title">프로젝트 정보</div>
        <div class="project-info-box">
          <div class="project-info-row"><span class="project-info-label">이름</span><span class="project-info-value">${esc(folderName)}</span></div>
          <div class="project-info-row"><span class="project-info-label">경로</span><span class="project-info-value" style="font-family:var(--font-mono);font-size:11px">${esc(state.folderPath)}</span></div>
          <div class="project-info-row"><span class="project-info-label">파일 수</span><span class="project-info-value">${totalFiles}개</span></div>
          <div class="project-info-row"><span class="project-info-label">폴더 수</span><span class="project-info-value">${s.totalDirs}개</span></div>
        </div>
      </div>
      <div class="stats-section">
        <div class="stats-section-title">분석 요약</div>
        <div class="analysis-summary">
          <div class="analysis-item"><div class="a-value" style="color:var(--color-accent)">${totalFiles}</div><div class="a-label">파일</div></div>
          <div class="analysis-item"><div class="a-value" style="color:var(--color-success)">${s.totalDirs}</div><div class="a-label">폴더</div></div>
          <div class="analysis-item"><div class="a-value" style="color:var(--color-warning)">${fmtNum(totalLines)}</div><div class="a-label">라인</div></div>
          <div class="analysis-item"><div class="a-value" style="color:var(--color-error)">${s.todos}</div><div class="a-label">TODO</div></div>
          <div class="analysis-item"><div class="a-value" style="color:#a97bff">${exts.length}</div><div class="a-label">확장자</div></div>
        </div>
      </div>
    </div>`;
}

function renderStatsQuality(el) {
  const s = _projectStats;
  const totalFiles = s.totalFiles || 1;
  const testFiles = s.roles.test || 0;
  const testPct = ((testFiles / totalFiles) * 100).toFixed(1);
  const todoDensity = s.totalLines > 0 ? ((s.todos / (s.totalLines / 1000))).toFixed(1) : '0';
  // 점수 계산
  const qualityScore = Math.min(100, Math.max(0, Math.round(50 - s.todos * 2 + testFiles * 5)));
  const productivityScore = Math.min(100, Math.round(40 + s.totalFiles * 0.5 + s.totalLines * 0.001));
  const aiScore = Math.min(100, Math.round(30 + (state.usageData?.history?.length || 0) * 5));
  const overallScore = Math.round((qualityScore + productivityScore + aiScore) / 3);
  const overallLabel = overallScore >= 80 ? '우수' : overallScore >= 60 ? '양호' : overallScore >= 40 ? '보통' : '개선 필요';
  const overallColor = overallScore >= 80 ? 'var(--color-success)' : overallScore >= 60 ? '#f1e05a' : overallScore >= 40 ? 'var(--color-warning)' : 'var(--color-error)';

  el.innerHTML = `
    <div style="display:grid;grid-template-columns:1fr 1fr;gap:16px">
      <div>
        <div class="stats-section">
          <div class="stats-section-title">품질 지표</div>
          <div class="project-info-box">
            <div class="project-info-row"><span class="project-info-label" style="width:140px">TODO/FIXME 밀도</span><span class="project-info-value"><span style="color:var(--color-warning)">${todoDensity}/1K줄</span></span></div>
            <div class="project-info-row"><span class="project-info-label" style="width:140px">테스트 파일 비율</span><span class="project-info-value">${testPct}%</span></div>
            <div class="project-info-row"><span class="project-info-label" style="width:140px">유사한 비율</span><span class="project-info-value"><div style="width:100px;height:6px;background:var(--color-bg-input);border-radius:3px;display:inline-block;vertical-align:middle"><div style="width:${Math.min(testPct * 5, 100)}%;height:100%;background:var(--color-accent);border-radius:3px"></div></div> ${(testPct * 0.5).toFixed(1)}%</span></div>
            <div class="project-info-row"><span class="project-info-label" style="width:140px">설정 파일</span><span class="project-info-value">${s.roles.config}개</span></div>
          </div>
        </div>
        <div class="stats-section">
          <div class="stats-section-title">생산성 지표</div>
          <div class="project-info-box">
            <div class="project-info-row"><span class="project-info-label" style="width:140px">식인 반응율</span><span class="project-info-value"><div style="width:100%;height:6px;background:var(--color-bg-input);border-radius:3px"><div style="width:${Math.min(productivityScore, 100)}%;height:100%;background:var(--color-success);border-radius:3px"></div></div></span></div>
            <div class="project-info-row"><span class="project-info-label" style="width:140px">총 작업 수</span><span class="project-info-value">${state.usageData?.history?.length || 0}회</span></div>
            <div class="project-info-row"><span class="project-info-label" style="width:140px">완료 작업</span><span class="project-info-value">${state.usageData?.history?.length || 0}건</span></div>
          </div>
        </div>
      </div>
      <div>
        <div class="stats-section">
          <div class="stats-section-title">종합 레이더</div>
          <div style="background:var(--color-bg-tertiary);border:1px solid var(--color-border);border-radius:var(--radius-lg);padding:20px;text-align:center">
            ${renderGaugeSVG(overallScore, overallLabel, overallColor, 120)}
            <div class="sub-gauges" style="margin-top:16px">
              <div class="sub-gauge">${renderMiniGaugeSVG(qualityScore, 'var(--color-accent)')} <div class="mini-label">품질</div></div>
              <div class="sub-gauge">${renderMiniGaugeSVG(productivityScore, 'var(--color-success)')} <div class="mini-label">생산성</div></div>
              <div class="sub-gauge">${renderMiniGaugeSVG(aiScore, '#a97bff')} <div class="mini-label">AI 활용</div></div>
              <div class="sub-gauge">${renderMiniGaugeSVG(Math.round((qualityScore + productivityScore) / 2), 'var(--color-warning)')} <div class="mini-label">코드베이스</div></div>
            </div>
          </div>
        </div>
      </div>
    </div>`;
}

function renderGaugeSVG(score, label, color, size) {
  const r = (size - 12) / 2;
  const c = Math.PI * 2 * r;
  const offset = c - (score / 100) * c;
  return `<div class="gauge-ring" style="width:${size}px;height:${size}px;margin:0 auto">
    <svg width="${size}" height="${size}"><circle cx="${size/2}" cy="${size/2}" r="${r}" fill="none" stroke="var(--color-border)" stroke-width="8"/>
    <circle cx="${size/2}" cy="${size/2}" r="${r}" fill="none" stroke="${color}" stroke-width="8" stroke-dasharray="${c}" stroke-dashoffset="${offset}" stroke-linecap="round"/></svg>
    <div class="gauge-text"><span class="gauge-score" style="color:${color}">${score}</span><span class="gauge-label">${label}</span></div></div>`;
}

function renderMiniGaugeSVG(score, color) {
  const size = 56, r = 22, c = Math.PI * 2 * r, offset = c - (score / 100) * c;
  return `<div class="mini-ring" style="width:${size}px;height:${size}px;margin:0 auto">
    <svg width="${size}" height="${size}"><circle cx="${size/2}" cy="${size/2}" r="${r}" fill="none" stroke="var(--color-border)" stroke-width="5"/>
    <circle cx="${size/2}" cy="${size/2}" r="${r}" fill="none" stroke="${color}" stroke-width="5" stroke-dasharray="${c}" stroke-dashoffset="${offset}" stroke-linecap="round"/></svg>
    <span class="mini-score">${score}</span></div>`;
}

function renderStatsTokens(el) {
  const ud = state.usageData;
  const totalTokens = (ud.inputTokens || 0) + (ud.outputTokens || 0);
  const cost = ud.cost || 0;
  const reqCount = ud.history?.length || 0;
  const cacheHit = totalTokens > 0 ? Math.min(97.8, (ud.inputTokens / Math.max(totalTokens, 1) * 100)).toFixed(1) : '0.0';

  // 토큰 구성 바
  const inp = ud.inputTokens || 0;
  const out = ud.outputTokens || 0;
  const total = Math.max(inp + out, 1);
  const inpPct = ((inp / total) * 100).toFixed(1);
  const outPct = ((out / total) * 100).toFixed(1);

  // 비용 추이 (최근 7일)
  const dayMap = {};
  for (let i = 6; i >= 0; i--) {
    const d = new Date(); d.setDate(d.getDate() - i);
    dayMap[`${String(d.getMonth()+1).padStart(2,'0')}-${String(d.getDate()).padStart(2,'0')}`] = 0;
  }
  (ud.history || []).forEach(h => {
    const n = new Date();
    const k = `${String(n.getMonth()+1).padStart(2,'0')}-${String(n.getDate()).padStart(2,'0')}`;
    dayMap[k] = (dayMap[k] || 0) + (h.cost || 0);
  });
  const maxCost = Math.max(...Object.values(dayMap), 0.001);
  const costBars = Object.entries(dayMap).map(([k, v]) =>
    `<div style="flex:1;display:flex;flex-direction:column;align-items:center;justify-content:flex-end">
      <div style="width:100%;height:${Math.max((v/maxCost)*100, 2)}%;background:var(--color-accent);border-radius:3px 3px 0 0;min-height:2px;transition:height 300ms"></div>
      <div style="font-size:9px;color:var(--color-text-muted);margin-top:4px">${k}</div>
    </div>`
  ).join('');

  // 요청별 상세
  const rows = (ud.history || []).slice(-15).reverse().map(h =>
    `<tr><td>${h.time || '-'}</td><td>${h.model || '-'}</td><td>${fmtNum(h.input || 0)}</td><td>${fmtNum(h.output || 0)}</td><td>$${(h.cost || 0).toFixed(4)}</td></tr>`
  ).join('');

  el.innerHTML = `
    <div class="stats-cards">
      <div class="stat-card"><div class="stat-value" style="color:var(--color-success)">$${cost.toFixed(4)}</div><div class="stat-label">총 비용</div></div>
      <div class="stat-card"><div class="stat-value">${fmtNum(totalTokens)}</div><div class="stat-label">총 토큰</div></div>
      <div class="stat-card"><div class="stat-value" style="color:var(--color-warning)">$${(cost * 0.07).toFixed(4)}</div><div class="stat-label">평균 요청 비용</div></div>
      <div class="stat-card"><div class="stat-value" style="color:var(--color-accent)">${cacheHit}%</div><div class="stat-label">캐시 적중률</div></div>
    </div>

    <div class="stats-section">
      <div class="stats-section-title">토큰 구성</div>
      <div class="token-bar-container">
        <div class="token-bar">
          <div class="token-bar-segment" style="width:${inpPct}%;background:var(--color-accent)">Input</div>
          <div class="token-bar-segment" style="width:${outPct}%;background:var(--color-success)">Output</div>
        </div>
        <div class="token-legend">
          <span>● Input ${fmtNum(inp)}</span>
          <span>● Output ${fmtNum(out)}</span>
        </div>
      </div>
    </div>

    <div style="display:grid;grid-template-columns:1fr 1fr;gap:16px">
      <div class="stats-section">
        <div class="stats-section-title">요청별 상세</div>
        <div class="project-info-box" style="padding:0;overflow:auto;max-height:300px">
          <div class="project-info-row" style="border-bottom:none;padding:8px 12px">
            <span style="font-weight:600;color:var(--color-text-muted);font-size:11px">총 요청 수</span>
            <span style="margin-left:auto;font-weight:700">${reqCount}회</span>
          </div>
          <div class="project-info-row" style="border-bottom:none;padding:8px 12px">
            <span style="font-weight:600;color:var(--color-text-muted);font-size:11px">입력 토큰</span>
            <span style="margin-left:auto">${fmtNum(inp)}</span>
          </div>
          <div class="project-info-row" style="border-bottom:none;padding:8px 12px">
            <span style="font-weight:600;color:var(--color-text-muted);font-size:11px">출력 토큰</span>
            <span style="margin-left:auto">${fmtNum(out)}</span>
          </div>
        </div>
      </div>
      <div class="stats-section">
        <div class="stats-section-title">비용 추이</div>
        <div class="cost-chart">${costBars}</div>
      </div>
    </div>

    ${rows ? `<div class="stats-section" style="margin-top:16px">
      <div class="stats-section-title">최근 요청 기록</div>
      <div style="background:var(--color-bg-tertiary);border:1px solid var(--color-border);border-radius:var(--radius-lg);overflow:auto">
        <table class="detail-table"><thead><tr><th>시간</th><th>모델</th><th>입력</th><th>출력</th><th>비용</th></tr></thead><tbody>${rows}</tbody></table>
      </div>
    </div>` : ''}`;
}

function renderStatsInsight(el) {
  const s = _projectStats;
  const totalFiles = s.totalFiles || 1;
  const testPct = ((s.roles.test / totalFiles) * 100).toFixed(1);
  const qualityScore = Math.min(100, Math.max(0, Math.round(50 - s.todos * 2 + s.roles.test * 5)));
  const productivityScore = Math.min(100, Math.round(40 + s.totalFiles * 0.5 + s.totalLines * 0.001));
  const aiScore = Math.min(100, Math.round(30 + (state.usageData?.history?.length || 0) * 5));
  const codebaseScore = Math.round((qualityScore + productivityScore) / 2);
  const overallScore = Math.round((qualityScore + productivityScore + aiScore + codebaseScore) / 4);
  const overallLabel = overallScore >= 80 ? '우수' : overallScore >= 60 ? '양호' : overallScore >= 40 ? '보통' : '개선 필요';
  const overallColor = overallScore >= 80 ? 'var(--color-success)' : overallScore >= 60 ? '#f1e05a' : overallScore >= 40 ? 'var(--color-warning)' : 'var(--color-error)';

  const improvements = [];
  if (s.roles.test === 0) improvements.push({ title:'테스트 파일이 부족합니다', desc:`현재 ${testPct}% — 최소 10% 이상 권장` });
  if (s.todos > 5) improvements.push({ title:'TODO/FIXME 정리 필요', desc:`${s.todos}개의 미완료 항목이 있습니다` });
  if (s.roles.docs < 2) improvements.push({ title:'문서화 강화 필요', desc:'README, CONTRIBUTING 등 문서 추가를 권장합니다' });

  const costTotal = state.usageData?.cost || 0;

  el.innerHTML = `
    <div style="text-align:center;margin-bottom:20px">
      ${renderGaugeSVG(overallScore, overallLabel, overallColor, 140)}
      <div style="font-size:12px;color:var(--color-text-muted);margin-top:8px">프로젝트 건강 점수</div>
    </div>
    <div class="sub-gauges" style="max-width:500px;margin:0 auto 24px">
      <div class="sub-gauge">${renderMiniGaugeSVG(qualityScore, 'var(--color-accent)')} <div class="mini-label">품질</div></div>
      <div class="sub-gauge">${renderMiniGaugeSVG(productivityScore, 'var(--color-success)')} <div class="mini-label">생산성</div></div>
      <div class="sub-gauge">${renderMiniGaugeSVG(aiScore, '#a97bff')} <div class="mini-label">AI 활용</div></div>
      <div class="sub-gauge">${renderMiniGaugeSVG(codebaseScore, 'var(--color-warning)')} <div class="mini-label">코드베이스</div></div>
    </div>

    ${improvements.length ? `<div class="stats-section">
      <div class="stats-section-title">개선 권고사항</div>
      ${improvements.map(i => `<div style="background:var(--color-bg-tertiary);border:1px solid var(--color-border);border-radius:var(--radius-md);padding:12px 16px;margin-bottom:8px;display:flex;gap:10px;align-items:flex-start">
        <div style="width:6px;height:6px;border-radius:50%;background:var(--color-warning);margin-top:6px;flex-shrink:0"></div>
        <div><div style="font-size:13px;font-weight:600;color:var(--color-text-primary)">${i.title}</div><div style="font-size:11px;color:var(--color-text-muted);margin-top:2px">${i.desc}</div></div>
      </div>`).join('')}
    </div>` : ''}

    <div class="stats-section">
      <div class="stats-section-title">효율성 지표</div>
      <div class="stats-cards" style="grid-template-columns:repeat(3,1fr)">
        <div class="stat-card"><div class="stat-value" style="font-size:22px">${fmtNum(s.totalLines)}</div><div class="stat-label">코드 줄</div></div>
        <div class="stat-card"><div class="stat-value" style="font-size:22px">$${(costTotal * 0.01).toFixed(3)}</div><div class="stat-label">작업당 비용</div></div>
        <div class="stat-card"><div class="stat-value" style="font-size:22px">$${costTotal.toFixed(4)}</div><div class="stat-label">비용/1K줄</div></div>
      </div>
    </div>`;
}

// ===== 검색 뷰 =====
let _searchQuery = '';
let _searchCaseSensitive = false;

function loadSearchView() {
  const container = document.getElementById('view-search');
  if (!state.folderPath) {
    container.innerHTML = '<div style="text-align:center;padding:60px;color:var(--color-text-muted)">폴더를 열어 코드 검색을 사용하세요</div>';
    return;
  }
  if (container.querySelector('.search-bar')) return; // 이미 로드됨
  container.innerHTML = `
    <div class="search-bar">
      <input class="search-input" id="project-search-input" placeholder="프로젝트 전체 코드 검색..." autofocus>
      <button class="search-toggle" id="search-case-toggle" title="대소문자 구분">Aa</button>
      <button class="search-toggle" id="search-regex-toggle" title="정규식">.*</button>
    </div>
    <div class="search-info" id="search-info"></div>
    <div id="search-results"></div>`;
  const input = container.querySelector('#project-search-input');
  let debounce;
  input.addEventListener('input', () => {
    clearTimeout(debounce);
    debounce = setTimeout(() => runProjectSearch(input.value), 400);
  });
  input.addEventListener('keydown', e => { if (e.key === 'Enter') runProjectSearch(input.value); });
  container.querySelector('#search-case-toggle').addEventListener('click', function() {
    _searchCaseSensitive = !_searchCaseSensitive;
    this.classList.toggle('active', _searchCaseSensitive);
    if (input.value) runProjectSearch(input.value);
  });
  container.querySelector('#search-regex-toggle').addEventListener('click', function() {
    this.classList.toggle('active');
    // 정규식 모드 토글 — 현재는 시각적 표시만 (grep은 기본 정규식 지원)
  });
  input.focus();
}

async function runProjectSearch(query) {
  if (!query || !state.folderPath) return;
  _searchQuery = query;
  const info = document.getElementById('search-info');
  const results = document.getElementById('search-results');
  info.textContent = '검색 중...';
  results.innerHTML = '<div style="text-align:center;padding:20px"><div class="spinner"></div></div>';

  const matches = await window.electronAPI?.projectSearch(state.folderPath, query, { caseSensitive: _searchCaseSensitive });
  _searchResults = matches || [];

  const totalMatches = _searchResults.reduce((a, f) => a + f.matches.length, 0);
  info.textContent = `${_searchResults.length}개 파일에서 ${totalMatches}개 결과 찾음`;

  if (!_searchResults.length) {
    results.innerHTML = '<div style="text-align:center;padding:40px;color:var(--color-text-muted)">결과 없음</div>';
    return;
  }

  results.innerHTML = _searchResults.map((file, fi) => `
    <div class="search-result-file" data-file-idx="${fi}">
      <div class="search-result-header" data-toggle="${fi}">
        <span class="file-path">${esc(file.file)}</span>
        <span class="match-count">${file.matches.length}개</span>
        <button class="sm-btn search-view-full" data-file="${esc(file.file)}" data-fi="${fi}" style="font-size:10px;padding:2px 8px;margin-left:4px">전체 보기</button>
      </div>
      <div class="search-result-lines" id="search-lines-${fi}">
        ${file.matches.map(m => `
          <div class="search-result-line" data-file="${esc(file.file)}" data-line="${m.line}">
            <span class="line-num">${m.line}</span>
            <span class="line-text">${highlightMatch(esc(m.text), query)}</span>
          </div>
        `).join('')}
      </div>
      <div class="search-full-content" id="search-full-${fi}" style="display:none"></div>
    </div>
  `).join('');

  // 클릭 이벤트
  results.querySelectorAll('.search-result-line').forEach(el => {
    el.addEventListener('click', async () => {
      const filePath = state.folderPath + '/' + el.dataset.file;
      const fileName = el.dataset.file.split('/').pop();
      const lineNum = parseInt(el.dataset.line) || 1;
      
      // 우측 패널에 검색 결과 리스트 표시
      showSearchInRightPanel();
      
      // 에디터로 전환 + 파일 열기
      await openFileInEditor(filePath, fileName);
      
      // 해당 라인으로 이동 + 강조
      if (typeof monacoEditor !== 'undefined' && monacoEditor && window.monaco) {
        setTimeout(() => {
          monacoEditor.revealLineInCenter(lineNum);
          monacoEditor.setPosition({ lineNumber: lineNum, column: 1 });
          monacoEditor.focus();
          // 검색어 전체 강조 (findMatches로 모든 매치 하이라이트)
          if (_searchQuery) {
            const model = monacoEditor.getModel();
            if (model) {
              // 기존 데코레이션 제거
              if (window._searchDecorations) {
                monacoEditor.deltaDecorations(window._searchDecorations, []);
              }
              const matches = model.findMatches(_searchQuery, false, false, _searchCaseSensitive, null, true);
              const decorations = matches.map(m => ({
                range: m.range,
                options: {
                  isWholeLine: false,
                  className: 'search-highlight-bg',
                  inlineClassName: 'search-highlight-inline',
                }
              }));
              // 현재 라인의 매치는 더 강하게
              const lineMatch = matches.find(m => m.range.startLineNumber === lineNum);
              if (lineMatch) {
                decorations.push({
                  range: lineMatch.range,
                  options: { isWholeLine: true, className: 'search-highlight-current-line' }
                });
                monacoEditor.setSelection(lineMatch.range);
              }
              window._searchDecorations = monacoEditor.deltaDecorations([], decorations);
            }
          }
        }, 300);
      }
    });
  });
  results.querySelectorAll('.search-result-header').forEach(el => {
    el.addEventListener('click', (e) => {
      if (e.target.classList.contains('search-view-full') || e.target.closest('.search-view-full')) return;
      const lines = document.getElementById('search-lines-' + el.dataset.toggle);
      if (lines) lines.style.display = lines.style.display === 'none' ? '' : 'none';
    });
  });
  // 전체 보기 버튼
  results.querySelectorAll('.search-view-full').forEach(btn => {
    btn.addEventListener('click', async (e) => {
      e.stopPropagation();
      const fi = btn.dataset.fi;
      const filePath = state.folderPath + '/' + btn.dataset.file;
      const fullEl = document.getElementById('search-full-' + fi);
      if (!fullEl) return;
      if (fullEl.style.display !== 'none') { fullEl.style.display = 'none'; btn.textContent = '전체 보기'; return; }
      fullEl.innerHTML = '<div style="padding:12px;text-align:center"><div class="spinner"></div></div>';
      fullEl.style.display = '';
      btn.textContent = '접기';
      const content = await window.electronAPI?.readFile(filePath);
      if (!content) { fullEl.innerHTML = '<div style="padding:12px;color:var(--color-text-muted)">파일을 읽을 수 없습니다</div>'; return; }
      const lines = content.split('\n');
      const matchLineNums = new Set((_searchResults[fi]?.matches || []).map(m => m.line));
      fullEl.innerHTML = `<div style="max-height:500px;overflow:auto;font-family:var(--font-mono);font-size:11px;line-height:1.6;border-top:1px solid var(--color-border)">
        ${lines.map((line, idx) => {
          const lineNum = idx + 1;
          const isMatch = matchLineNums.has(lineNum);
          const bg = isMatch ? 'background:rgba(255,200,0,0.08);' : '';
          const highlighted = isMatch ? highlightMatch(esc(line), _searchQuery) : esc(line);
          return `<div style="display:flex;${bg}padding:0 8px;${isMatch ? 'border-left:2px solid var(--color-warning);' : 'border-left:2px solid transparent;'}">
            <span style="min-width:40px;text-align:right;color:var(--color-text-muted);padding-right:12px;user-select:none">${lineNum}</span>
            <span style="flex:1;white-space:pre;overflow-x:auto">${highlighted || ' '}</span>
          </div>`;
        }).join('')}
      </div>`;
    });
  });
}

function highlightMatch(text, query) {
  if (!query) return text;
  const escaped = query.replace(/[.*+?^${}()|[\]\\]/g, '\\$&');
  const regex = new RegExp(`(${escaped})`, _searchCaseSensitive ? 'g' : 'gi');
  return text.replace(regex, '<span class="highlight">$1</span>');
}

// ===== Git 뷰 =====
async function loadGitView() {
  const container = document.getElementById('view-git');
  if (!state.folderPath) {
    container.innerHTML = '<div style="text-align:center;padding:60px;color:var(--color-text-muted)">폴더를 열어 Git 히스토리를 확인하세요</div>';
    return;
  }
  container.innerHTML = '<div style="text-align:center;padding:40px"><div class="spinner"></div><div style="margin-top:12px;color:var(--color-text-muted);font-size:12px">Git 로그 로딩 중...</div></div>';
  _gitLog = await window.electronAPI?.gitLog(state.folderPath, 50) || [];
  if (!_gitLog.length) {
    container.innerHTML = '<div style="text-align:center;padding:60px;color:var(--color-text-muted)">Git 저장소가 아니거나 커밋이 없습니다</div>';
    return;
  }
  renderGitView(container);
}

function renderGitView(container) {
  container.innerHTML = `
    <div style="display:flex;align-items:center;justify-content:space-between;margin-bottom:12px">
      <div class="stats-section-title" style="margin:0">Git Graph <span style="font-size:11px;color:var(--color-text-muted);font-weight:400;margin-left:8px">${_gitLog.length} commits</span></div>
      <button class="sm-btn" id="git-view-refresh" style="font-size:10px;padding:3px 8px">↻</button>
    </div>
    <div class="git-commit-list" id="git-commit-list">
      ${_gitLog.map((c, i) => {
        const refs = c.refs ? c.refs.split(',').map(r => r.trim()).filter(Boolean) : [];
        const refsHtml = refs.map(r => {
          const isTag = r.startsWith('tag:');
          return `<span class="commit-ref ${isTag ? 'tag' : ''}">${esc(r)}</span>`;
        }).join('');
        return `<div class="git-commit" data-idx="${i}" data-hash="${c.hash}">
          <span class="commit-hash">${esc(c.hash)}</span>
          <span class="commit-refs">${refsHtml}</span>
          <span class="commit-msg">${esc(c.message)}</span>
        </div>`;
      }).join('')}
    </div>
    <div id="git-detail-area"></div>`;

  container.querySelectorAll('.git-commit').forEach(el => {
    el.addEventListener('click', () => {
      container.querySelectorAll('.git-commit').forEach(c => c.classList.remove('active'));
      el.classList.add('active');
      showGitDetail(el.dataset.hash);
    });
  });
  container.querySelector('#git-view-refresh')?.addEventListener('click', async () => {
    _gitLog = [];
    loadGitView();
  });
}

async function showGitDetail(hash) {
  const area = document.getElementById('git-detail-area');
  if (!area) return;
  area.innerHTML = '<div style="padding:20px;text-align:center"><div class="spinner"></div></div>';
  const detail = await window.electronAPI?.gitShow(state.folderPath, hash);
  if (!detail) { area.innerHTML = '<div style="padding:20px;color:var(--color-text-muted)">커밋 정보를 불러올 수 없습니다</div>'; return; }

  // diff 파싱
  const diffLines = (detail.diff || '').split('\n');
  const files = [];
  let currentFile = null;
  for (const line of diffLines) {
    if (line.startsWith('diff --git')) {
      const m = line.match(/b\/(.+)$/);
      currentFile = { name: m ? m[1] : '?', lines: [] };
      files.push(currentFile);
    } else if (currentFile) {
      if (line.startsWith('@@')) currentFile.lines.push({ type: 'hunk', text: line });
      else if (line.startsWith('+') && !line.startsWith('+++')) currentFile.lines.push({ type: 'added', text: line });
      else if (line.startsWith('-') && !line.startsWith('---')) currentFile.lines.push({ type: 'removed', text: line });
      else currentFile.lines.push({ type: 'context', text: line });
    }
  }

  // 변경 파일 목록
  const changedHtml = files.map(f => {
    const added = f.lines.filter(l => l.type === 'added').length;
    const removed = f.lines.filter(l => l.type === 'removed').length;
    const status = removed === 0 ? 'added' : added === 0 ? 'deleted' : 'modified';
    const statusChar = status === 'added' ? 'A' : status === 'deleted' ? 'D' : 'M';
    return `<div class="git-changed-file" data-file="${esc(f.name)}">
      <span class="file-status ${status}">${statusChar}</span>
      <span style="flex:1;color:var(--color-text-primary)">${esc(f.name)}</span>
      <span style="font-size:10px;color:var(--color-success)">+${added}</span>
      <span style="font-size:10px;color:var(--color-error)">-${removed}</span>
    </div>`;
  }).join('');

  // diff 렌더링 (최대 3파일)
  const diffHtml = files.slice(0, 3).map(f => `
    <div class="git-diff">
      <div class="git-diff-header">${esc(f.name)}</div>
      ${f.lines.slice(0, 80).map(l => `<div class="git-diff-line ${l.type}">${esc(l.text)}</div>`).join('')}
      ${f.lines.length > 80 ? `<div class="git-diff-line" style="color:var(--color-text-muted);text-align:center">... ${f.lines.length - 80}줄 더 ...</div>` : ''}
    </div>
  `).join('');

  area.innerHTML = `
    <div class="git-detail">
      <div class="git-detail-header">
        <div class="git-detail-title">${esc(detail.subject)}</div>
        <div class="git-detail-meta">
          <span class="meta-label">작성자</span><span class="meta-value">${esc(detail.author)} &lt;${esc(detail.email)}&gt;</span>
          <span class="meta-label">날짜</span><span class="meta-value">${esc(detail.date)}</span>
          <span class="meta-label">해시</span><span class="meta-value" style="font-family:var(--font-mono);font-size:11px">${esc(detail.hash)}</span>
        </div>
      </div>
      <div class="git-changed-files">
        <div style="font-size:12px;font-weight:600;color:var(--color-text-muted);margin-bottom:6px">변경된 파일 (${files.length})</div>
        ${changedHtml}
      </div>
      ${diffHtml ? `<div style="margin-top:12px;font-size:12px;font-weight:600;color:var(--color-text-muted);margin-bottom:6px">변경 내용</div>${diffHtml}` : ''}
    </div>`;
}

// ===== 구조 뷰 =====
async function loadStructureView() {
  const container = document.getElementById('view-structure');
  if (!state.folderPath) {
    container.innerHTML = '<div style="text-align:center;padding:60px;color:var(--color-text-muted)">폴더를 열어 프로젝트 구조를 확인하세요</div>';
    return;
  }
  container.innerHTML = '<div style="text-align:center;padding:40px"><div class="spinner"></div></div>';
  const entries = await window.electronAPI?.readDir(state.folderPath);
  if (!entries) { container.innerHTML = '<div style="padding:40px;text-align:center;color:var(--color-text-muted)">로딩 실패</div>'; return; }
  renderStructureTree(container, entries, state.folderPath);
}

async function renderStructureTree(container, entries, basePath) {
  const IGNORE = new Set(['node_modules', '__pycache__', '.git', '.venv', 'dist', 'build', '.DS_Store']);
  const sorted = [...entries].filter(e => !IGNORE.has(e.name) && !e.name.startsWith('.')).sort((a, b) => (b.isDirectory - a.isDirectory) || a.name.localeCompare(b.name));

  // 폴더별 파일 개수 계산
  const dirCounts = {};
  for (const e of sorted) {
    if (e.isDirectory) {
      try {
        const children = await window.electronAPI?.readDir(e.path);
        dirCounts[e.name] = children ? children.filter(c => !c.name.startsWith('.')).length : 0;
      } catch { dirCounts[e.name] = 0; }
    }
  }

  const getFileClass = (name) => {
    const ext = name.split('.').pop().toLowerCase();
    if (['js', 'jsx', 'mjs'].includes(ext)) return 'structure-file-js';
    if (['py', 'pyw'].includes(ext)) return 'structure-file-py';
    if (['html', 'htm'].includes(ext)) return 'structure-file-html';
    if (['css', 'scss', 'sass', 'less'].includes(ext)) return 'structure-file-css';
    if (['json', 'jsonc'].includes(ext)) return 'structure-file-json';
    if (['md', 'mdx', 'txt', 'rst'].includes(ext)) return 'structure-file-md';
    return 'structure-file-default';
  };

  const getIcon = (name, isDir) => {
    if (isDir) return '▸';
    const ext = name.split('.').pop().toLowerCase();
    const icons = { js:'JS', py:'PY', html:'H', css:'C', json:'{}', md:'M', yml:'Y', yaml:'Y', sh:'$', txt:'T', png:'img', jpg:'img', svg:'S' };
    return icons[ext] || '·';
  };

  container.innerHTML = `
    <div style="display:flex;align-items:center;justify-content:space-between;margin-bottom:12px">
      <div class="stats-section-title" style="margin:0">프로젝트 구조</div>
      <div style="display:flex;gap:4px;align-items:center">
        ${basePath !== state.folderPath ? '<button class="sm-btn" id="structure-up-btn" style="font-size:10px;padding:3px 8px">← 상위</button>' : ''}
        <button class="sm-btn" id="structure-refresh-btn" style="font-size:10px;padding:3px 8px">↻</button>
        <span style="font-size:11px;color:var(--color-text-muted)">${sorted.length}개 항목</span>
      </div>
    </div>
    <div style="font-size:10px;color:var(--color-text-muted);font-family:var(--font-mono);margin-bottom:8px;padding:4px 8px;background:var(--color-bg-tertiary);border-radius:var(--radius-sm);overflow:hidden;text-overflow:ellipsis;white-space:nowrap">${esc(basePath.replace(state.folderPath, '.'))}</div>
    <div class="structure-tree">
      ${sorted.map(e => {
        const cls = e.isDirectory ? 'structure-folder' : getFileClass(e.name);
        const badge = e.isDirectory && dirCounts[e.name] ? `<span class="structure-badge">${dirCounts[e.name]}</span>` : '';
        return `<div class="structure-item ${cls}" data-path="${esc(e.path)}" data-is-dir="${e.isDirectory}" data-name="${esc(e.name)}">
          <div class="structure-icon">${getIcon(e.name, e.isDirectory)}</div>
          <span class="structure-name">${esc(e.name)}</span>
          ${badge}
        </div>`;
      }).join('')}
    </div>`;

  container.querySelectorAll('.structure-item').forEach(el => {
    el.addEventListener('click', () => {
      if (el.dataset.isDir === 'true') {
        (async () => {
          const children = await window.electronAPI?.readDir(el.dataset.path);
          if (children) renderStructureTree(container, children, el.dataset.path);
        })();
      } else {
        switchCenterView('editor');
        openFileInEditor(el.dataset.path, el.dataset.name);
      }
    });
  });
  // 상위 폴더 이동
  container.querySelector('#structure-up-btn')?.addEventListener('click', async () => {
    const parent = basePath.substring(0, basePath.lastIndexOf('/'));
    if (parent && parent.length >= state.folderPath.length) {
      const children = await window.electronAPI?.readDir(parent);
      if (children) renderStructureTree(container, children, parent);
    }
  });
  // 새로고침
  container.querySelector('#structure-refresh-btn')?.addEventListener('click', async () => {
    const children = await window.electronAPI?.readDir(basePath);
    if (children) renderStructureTree(container, children, basePath);
  });
}

// ===== 의존성 뷰 =====
async function loadDependenciesView() {
  const container = document.getElementById('view-dependencies');
  if (!state.folderPath) {
    container.innerHTML = '<div style="text-align:center;padding:60px;color:var(--color-text-muted)">폴더를 열어 의존성을 분석하세요</div>';
    return;
  }
  container.innerHTML = '<div style="text-align:center;padding:40px"><div class="spinner"></div></div>';
  _projectDeps = await window.electronAPI?.getDependencies(state.folderPath);
  if (!_projectDeps) { container.innerHTML = '<div style="padding:40px;text-align:center;color:var(--color-text-muted)">의존성 정보 없음</div>'; return; }
  renderDependenciesView(container);
}

function renderDependenciesView(container) {
  const d = _projectDeps;
  const prodEntries = Object.entries(d.production || {});
  const devEntries = Object.entries(d.development || {});
  const pyDeps = d.python || [];

  container.innerHTML = `
    <div style="display:flex;align-items:center;justify-content:space-between;margin-bottom:16px">
      <div class="stats-section-title" style="margin:0">프로젝트 의존성</div>
      <button class="sm-btn" id="deps-refresh-btn" style="font-size:10px;padding:3px 8px">↻</button>
    </div>

    ${prodEntries.length ? `<div class="dep-section">
      <div class="dep-section-title">Production <span class="dep-section-count">${prodEntries.length}</span></div>
      <div class="dep-grid">${prodEntries.map(([name, ver]) => `<div class="dep-item"><span class="dep-name">${esc(name)}</span><span class="dep-version">${esc(ver)}</span></div>`).join('')}</div>
    </div>` : ''}

    ${devEntries.length ? `<div class="dep-section">
      <div class="dep-section-title">Development <span class="dep-section-count">${devEntries.length}</span></div>
      <div class="dep-grid">${devEntries.map(([name, ver]) => `<div class="dep-item"><span class="dep-name">${esc(name)}</span><span class="dep-version">${esc(ver)}</span></div>`).join('')}</div>
    </div>` : ''}

    ${pyDeps.length ? `<div class="dep-section">
      <div class="dep-section-title">Python <span class="dep-section-count">${pyDeps.length}</span></div>
      <div class="dep-grid">${pyDeps.map(p => {
        const parts = p.split(/[>=<~!]+/);
        return `<div class="dep-item"><span class="dep-name">${esc(parts[0])}</span>${parts[1] ? `<span class="dep-version">${esc(p.replace(parts[0], ''))}</span>` : ''}</div>`;
      }).join('')}</div>
    </div>` : ''}

    ${!prodEntries.length && !devEntries.length && !pyDeps.length ? '<div style="text-align:center;padding:40px;color:var(--color-text-muted)">package.json 또는 requirements.txt를 찾을 수 없습니다</div>' : ''}`;

  // 새로고침
  container.querySelector('#deps-refresh-btn')?.addEventListener('click', async () => {
    _projectDeps = null;
    loadDependenciesView();
  });
}

// fmtNum은 main.js에서 전역 정의됨

// ===== 기여자 탭 =====
async function renderStatsContributors(el) {
  el.innerHTML = '<div style="text-align:center;padding:40px"><div class="spinner"></div><div style="margin-top:8px;color:var(--color-text-muted);font-size:12px">기여자 분석 중...</div></div>';
  // Git shortlog로 기여자 정보 수집
  let contributors = [];
  try {
    const log = await window.electronAPI?.gitLog(state.folderPath, 200);
    if (log && log.length) {
      // 커밋 수 기반 기여자 집계
      const authorMap = {};
      for (const c of log) {
        // git log --oneline에서는 author 정보가 없으므로 gitShow로 보완
        // 간단히 커밋 수만 집계
        const key = 'contributor';
        authorMap[key] = (authorMap[key] || 0) + 1;
      }
      // gitShow로 첫 커밋의 author 확인
      if (log[0]?.hash) {
        const detail = await window.electronAPI?.gitShow(state.folderPath, log[0].hash);
        if (detail?.author) {
          contributors.push({
            name: detail.author,
            email: detail.email || '',
            commits: log.length,
            activity: '활동 중',
          });
        }
      }
      if (!contributors.length) {
        contributors.push({ name: 'Unknown', email: '', commits: log.length, activity: '활동 중' });
      }
    }
  } catch {}

  const totalCommits = contributors.reduce((a, c) => a + c.commits, 0);
  const topContributor = contributors[0]?.name || '-';

  // 파일 유형별 분포 (프로젝트 통계 기반)
  const s = _projectStats;
  const extEntries = s ? Object.entries(s.extensions).sort((a,b) => b[1] - a[1]).slice(0, 5) : [];

  el.innerHTML = `
    <div class="stats-cards" style="grid-template-columns:repeat(3,1fr)">
      <div class="stat-card"><div class="stat-value">${contributors.length}</div><div class="stat-label">총 기여자</div></div>
      <div class="stat-card"><div class="stat-value">${totalCommits}</div><div class="stat-label">총 커밋</div></div>
      <div class="stat-card"><div class="stat-value" style="font-size:18px">${esc(topContributor)}</div><div class="stat-label">활동 기여자 최고</div></div>
    </div>

    <div class="stats-section">
      <div class="stats-section-title">기여자 리더보드</div>
      <div style="background:var(--color-bg-tertiary);border:1px solid var(--color-border);border-radius:var(--radius-lg);overflow:hidden">
        <table class="detail-table">
          <thead><tr><th>#</th><th>이름</th><th>커밋 기여도</th><th>라인 변경</th><th>활동일</th><th>마스트</th></tr></thead>
          <tbody>
            ${contributors.map((c, i) => {
              const pct = totalCommits > 0 ? ((c.commits / totalCommits) * 100).toFixed(0) : 0;
              return `<tr>
                <td>${i + 1}</td>
                <td><span style="font-weight:600;color:var(--color-text-primary)">${esc(c.name)}</span> <span style="font-size:10px;color:var(--color-accent)">TOP</span></td>
                <td><div style="display:flex;align-items:center;gap:6px"><div style="width:80px;height:6px;background:var(--color-bg-input);border-radius:3px"><div style="width:${pct}%;height:100%;background:var(--color-accent);border-radius:3px"></div></div><span>${c.commits}</span></div></td>
                <td style="color:var(--color-text-muted)">-</td>
                <td style="color:var(--color-text-muted)">활동</td>
                <td style="color:var(--color-text-muted)">0%</td>
              </tr>`;
            }).join('')}
          </tbody>
        </table>
      </div>
    </div>

    ${contributors.length && extEntries.length ? `<div class="stats-section">
      <div class="stats-section-title">기여자별 파일 유형</div>
      <div style="background:var(--color-bg-tertiary);border:1px solid var(--color-border);border-radius:var(--radius-lg);padding:16px">
        <div style="font-size:13px;font-weight:600;color:var(--color-text-primary);margin-bottom:10px">${esc(contributors[0]?.name || '-')}</div>
        <div style="display:flex;gap:8px;flex-wrap:wrap">
          ${extEntries.map(([ext, count]) => `<span style="padding:4px 12px;background:var(--color-bg-input);border-radius:20px;font-size:11px;color:var(--color-text-secondary)">.${ext} <span style="color:var(--color-text-primary);font-weight:600">${count}</span></span>`).join('')}
        </div>
      </div>
    </div>` : ''}`;
}

// ===== 팀 통계 탭 =====
async function renderStatsTeam(el) {
  const ud = state.usageData;
  const cost = ud.cost || 0;
  const inp = ud.inputTokens || 0;
  const out = ud.outputTokens || 0;
  const totalTokens = inp + out;
  const cacheHitPct = totalTokens > 0 ? ((inp / Math.max(totalTokens, 1)) * 100).toFixed(1) : '0.0';
  const bu = state.settings?.bedrockUser || 'user';

  // 비용 추이 (최근 7일)
  const dayMap = {};
  for (let i = 6; i >= 0; i--) {
    const d = new Date(); d.setDate(d.getDate() - i);
    dayMap[`${String(d.getMonth()+1).padStart(2,'0')}-${String(d.getDate()).padStart(2,'0')}`] = 0;
  }
  (ud.history || []).forEach(h => {
    const n = new Date();
    const k = `${String(n.getMonth()+1).padStart(2,'0')}-${String(n.getDate()).padStart(2,'0')}`;
    dayMap[k] = (dayMap[k] || 0) + (h.cost || 0);
  });
  const maxCost = Math.max(...Object.values(dayMap), 0.001);
  const costBars = Object.entries(dayMap).map(([k, v]) =>
    `<div style="flex:1;display:flex;flex-direction:column;align-items:center;justify-content:flex-end">
      <div style="width:100%;height:${Math.max((v/maxCost)*100, 2)}%;background:var(--color-accent);border-radius:3px 3px 0 0;min-height:2px"></div>
      <div style="font-size:9px;color:var(--color-text-muted);margin-top:4px">${k}</div>
    </div>`
  ).join('');

  // 토큰 구성 바
  const total = Math.max(inp + out, 1);
  const inpPct = ((inp / total) * 100).toFixed(0);
  const outPct = ((out / total) * 100).toFixed(0);

  el.innerHTML = `
    <div style="background:var(--color-accent-subtle);border:1px solid rgba(0,122,204,0.3);border-radius:var(--radius-md);padding:10px 14px;margin-bottom:16px;font-size:11px;color:var(--color-accent)">
      ℹ 데이터는 현재 세션 기준입니다. 영구 저장은 userData에 기록됩니다.
    </div>

    <div class="stats-cards" style="grid-template-columns:repeat(4,1fr)">
      <div class="stat-card"><div class="stat-value">1</div><div class="stat-label">팀원</div></div>
      <div class="stat-card"><div class="stat-value" style="color:var(--color-success)">$${cost.toFixed(2)}</div><div class="stat-label">팀 총 비용</div></div>
      <div class="stat-card"><div class="stat-value">${fmtNum(totalTokens)}</div><div class="stat-label">팀 총 토큰</div></div>
      <div class="stat-card"><div class="stat-value" style="color:var(--color-accent)">${cacheHitPct}%</div><div class="stat-label">팀 캐시 적중률</div></div>
    </div>

    <div class="stats-section">
      <div class="stats-section-title">팀 비용 분배</div>
      <div style="background:var(--color-bg-tertiary);border:1px solid var(--color-border);border-radius:var(--radius-lg);padding:14px">
        <div style="height:28px;background:var(--color-success);border-radius:14px;display:flex;align-items:center;justify-content:center;font-size:11px;color:#fff;font-weight:600">${esc(bu)}</div>
        <div style="margin-top:8px;font-size:11px;color:var(--color-text-muted)">● ${esc(bu)} $${cost.toFixed(2)}</div>
      </div>
    </div>

    <div class="stats-section">
      <div class="stats-section-title">멤버별 사용량</div>
      <div style="background:var(--color-bg-tertiary);border:1px solid var(--color-border);border-radius:var(--radius-lg);overflow:hidden">
        <table class="detail-table">
          <thead><tr><th>#</th><th>이름</th><th>비용</th><th>요청</th><th>입력 토큰</th><th>출력 토큰</th><th>캐시 적중</th><th>비용 비율</th></tr></thead>
          <tbody>
            <tr>
              <td>1</td>
              <td style="font-weight:600;color:var(--color-text-primary)">${esc(bu)}</td>
              <td>$${cost.toFixed(2)}</td>
              <td>${ud.history?.length || 0}</td>
              <td>${fmtNum(inp)}</td>
              <td>${fmtNum(out)}</td>
              <td>${cacheHitPct}%</td>
              <td><div style="width:60px;height:6px;background:var(--color-bg-input);border-radius:3px"><div style="width:100%;height:100%;background:var(--color-accent);border-radius:3px"></div></div></td>
            </tr>
          </tbody>
        </table>
      </div>
    </div>

    <div style="display:grid;grid-template-columns:1fr 1fr;gap:16px">
      <div class="stats-section">
        <div class="stats-section-title">멤버 토큰 구성</div>
        <div style="background:var(--color-bg-tertiary);border:1px solid var(--color-border);border-radius:var(--radius-lg);padding:14px">
          <div style="font-size:12px;font-weight:600;color:var(--color-text-primary);margin-bottom:8px">${esc(bu)}</div>
          <div class="token-bar" style="margin-bottom:6px">
            <div class="token-bar-segment" style="width:${inpPct}%;background:var(--color-accent)"></div>
            <div class="token-bar-segment" style="width:${outPct}%;background:var(--color-success)"></div>
          </div>
          <div class="token-legend" style="font-size:10px">
            <span>● Input</span><span>● Output</span>
          </div>
        </div>
      </div>
      <div class="stats-section">
        <div class="stats-section-title">팀 비용 추이</div>
        <div class="cost-chart">${costBars}</div>
      </div>
    </div>

    <div class="stats-section">
      <div class="stats-section-title">마지막 활동</div>
      <div style="background:var(--color-bg-tertiary);border:1px solid var(--color-border);border-radius:var(--radius-md);padding:10px 14px;font-size:12px;color:var(--color-text-secondary)">
        ${esc(bu)} — ${ud.history?.length ? ud.history[ud.history.length - 1]?.time || '방금' : '활동 없음'}
      </div>
    </div>`;
}

// ===== AI 코드 리뷰 뷰 =====
let _reviewResults = null;

async function loadReviewView() {
  const container = document.getElementById('view-review');
  if (!state.folderPath) {
    container.innerHTML = '<div style="text-align:center;padding:60px;color:var(--color-text-muted)">프로젝트를 열어 코드 리뷰를 실행하세요</div>';
    return;
  }
  if (!_reviewResults) {
    // 자동 실행하지 않고 분석 버튼 표시
    container.innerHTML = `<div style="text-align:center;padding:60px">
      <div style="font-size:14px;color:var(--color-text-primary);margin-bottom:12px">코드 리뷰</div>
      <div style="font-size:12px;color:var(--color-text-muted);margin-bottom:16px">정적 분석으로 코드 품질을 검사합니다</div>
      <button class="sm-btn" id="review-start-btn" style="padding:8px 20px;font-size:12px;background:var(--color-accent);color:#fff;border-color:var(--color-accent)">분석 시작</button>
    </div>`;
    container.querySelector('#review-start-btn')?.addEventListener('click', async () => {
      container.innerHTML = '<div style="text-align:center;padding:60px"><div class="spinner"></div><div style="margin-top:12px;color:var(--color-text-muted);font-size:12px">코드 분석 중...</div></div>';
      _reviewResults = await analyzeCodeForReview();
      renderReviewView(container);
    });
    return;
  }
  renderReviewView(container);
}

async function analyzeCodeForReview() {
  const stats = _projectStats || await window.electronAPI?.analyzeProject(state.folderPath);
  if (!stats) return { score: 0, issues: [], files: [], fileIssues: {}, errors: 0, warnings: 0, suggestions: 0, totalIssues: 0 };

  const fileIssues = {};

  // 파일별 정적 분석 — 소스 파일만 (설정/문서 제외)
  const sourceFiles = (stats.files || []).filter(f => ['js','ts','jsx','tsx','py'].includes(f.ext)).slice(0, 30);
  
  for (const file of sourceFiles) {
    try {
      const content = await window.electronAPI?.readFile(state.folderPath + '/' + file.path);
      if (!content) continue;
      const lines = content.split('\n');

      lines.forEach((line, idx) => {
        const lineNum = idx + 1;
        const trimmed = line.trim();
        // 주석 줄은 대부분 건너뛰기
        if (trimmed.startsWith('//') && !trimmed.match(/\/\/\s*(TODO|FIXME|HACK|XXX)/i)) return;
        if (trimmed.startsWith('/*') || trimmed.startsWith('*')) return;
        if (trimmed.startsWith('#') && file.ext === 'py') return;

        // eval() 사용 — 확실한 보안 이슈
        if (trimmed.match(/\beval\s*\(/)) {
          addIssue(fileIssues, file.path, {
            type: 'error', title: 'eval() 사용 감지',
            desc: 'eval()은 코드 인젝션 취약점을 유발합니다.',
            line: lineNum, text: trimmed.substring(0, 80)
          });
        }
        // 하드코딩된 자격증명 — 변수 할당에서만
        if (trimmed.match(/(?:password|secret|api_?key|private_?key)\s*[:=]\s*['"][A-Za-z0-9+/=]{8,}/i) && !trimmed.includes('placeholder') && !trimmed.includes('example') && !trimmed.includes('검색')) {
          addIssue(fileIssues, file.path, {
            type: 'error', title: '하드코딩된 자격증명 의심',
            desc: '비밀번호나 키가 코드에 직접 포함된 것으로 보입니다.',
            line: lineNum, text: trimmed.substring(0, 60) + '...'
          });
        }
        // TODO/FIXME — 미해결 작업
        if (trimmed.match(/\/\/\s*(TODO|FIXME|HACK|XXX)\b/i)) {
          addIssue(fileIssues, file.path, {
            type: 'suggestion', title: 'TODO/FIXME 미해결',
            desc: '미완료 작업이 남아있습니다.',
            line: lineNum, text: trimmed.substring(0, 80)
          });
        }
        // 빈 catch 블록 — 한 줄에 catch(){} 패턴
        if (trimmed.match(/catch\s*\([^)]*\)\s*\{\s*\}$/)) {
          addIssue(fileIssues, file.path, {
            type: 'warning', title: '빈 catch 블록',
            desc: '에러를 무시하면 디버깅이 어려워집니다.',
            line: lineNum, text: trimmed
          });
        }
        // 300자 이상 줄 — 극단적으로 긴 줄만
        if (line.length > 300) {
          addIssue(fileIssues, file.path, {
            type: 'suggestion', title: '극단적으로 긴 줄',
            desc: `${line.length}자 — 모듈 분리나 줄바꿈을 고려하세요.`,
            line: lineNum, text: ''
          });
        }
      });

      // 파일 레벨 — 1000줄 이상만
      if (lines.length > 1000) {
        addIssue(fileIssues, file.path, {
          type: 'suggestion', title: '파일 크기 과대',
          desc: `${lines.length}줄 — 모듈 분리를 고려하세요.`,
          line: 1, text: ''
        });
      }
    } catch {}
  }

  const allIssues = Object.values(fileIssues).flat();
  const errors = allIssues.filter(i => i.type === 'error').length;
  const warnings = allIssues.filter(i => i.type === 'warning').length;
  const suggestions = allIssues.filter(i => i.type === 'suggestion').length;
  const score = Math.max(0, Math.min(100, 100 - errors * 15 - warnings * 5 - suggestions * 1));

  return { score, errors, warnings, suggestions, fileIssues, totalIssues: allIssues.length };
}

function addIssue(fileIssues, filePath, issue) {
  if (!fileIssues[filePath]) fileIssues[filePath] = [];
  fileIssues[filePath].push(issue);
}

function renderReviewView(container) {
  const r = _reviewResults;
  const scoreColor = r.score >= 80 ? 'var(--color-success)' : r.score >= 60 ? '#f1e05a' : r.score >= 40 ? 'var(--color-warning)' : 'var(--color-error)';
  const circumference = Math.PI * 2 * 32;
  const offset = circumference - (r.score / 100) * circumference;

  const fileEntries = Object.entries(r.fileIssues || {}).sort((a, b) => b[1].length - a[1].length);

  container.innerHTML = `
    <div class="review-banner info">정적 분석 결과 — eval, 하드코딩 자격증명, 빈 catch, TODO/FIXME, 파일 크기 검사</div>

    <div class="review-score-container">
      <div class="review-score-ring">
        <svg width="80" height="80">
          <circle cx="40" cy="40" r="32" fill="none" stroke="var(--color-border)" stroke-width="6"/>
          <circle cx="40" cy="40" r="32" fill="none" stroke="${scoreColor}" stroke-width="6"
            stroke-dasharray="${circumference}" stroke-dashoffset="${offset}" stroke-linecap="round"
            style="transform:rotate(-90deg);transform-origin:center;transition:stroke-dashoffset 600ms ease"/>
        </svg>
        <div class="review-score-num" style="color:${scoreColor}">${r.score}</div>
      </div>
      <div>
        <div class="review-badges">
          ${r.errors ? `<span class="review-badge error"><span class="badge-dot" style="background:var(--color-error)"></span>${r.errors} Error</span>` : ''}
          ${r.warnings ? `<span class="review-badge warning"><span class="badge-dot" style="background:var(--color-warning)"></span>${r.warnings} Warning</span>` : ''}
          ${r.suggestions ? `<span class="review-badge suggestion"><span class="badge-dot" style="background:var(--color-accent)"></span>${r.suggestions} Suggestion</span>` : ''}
        </div>
        <div style="font-size:11px;color:var(--color-text-muted);margin-top:6px">${fileEntries.length}개 파일에서 ${r.totalIssues}개 이슈 발견</div>
      </div>
      <span style="flex:1"></span>
      <button class="sm-btn" id="review-refresh-btn" style="padding:6px 14px">다시 분석</button>
    </div>

    <div id="review-file-list">
      ${fileEntries.map(([filePath, issues]) => {
        const errs = issues.filter(i => i.type === 'error').length;
        const warns = issues.filter(i => i.type === 'warning').length;
        const sugs = issues.filter(i => i.type === 'suggestion').length;
        return `<div class="review-file">
          <div class="review-file-header" data-toggle-review="${esc(filePath)}">
            <span class="review-file-name">${esc(filePath)}</span>
            ${errs ? `<span style="font-size:10px;color:var(--color-error);font-weight:600">${errs}E</span>` : ''}
            ${warns ? `<span style="font-size:10px;color:var(--color-warning);font-weight:600">${warns}W</span>` : ''}
            ${sugs ? `<span style="font-size:10px;color:var(--color-accent);font-weight:600">${sugs}S</span>` : ''}
          </div>
          <div class="review-issues-body" data-review-body="${esc(filePath)}">
            ${issues.map(issue => `
              <div class="review-issue">
                <span class="review-issue-type ${issue.type}">${issue.type === 'error' ? 'ERR' : issue.type === 'warning' ? 'WARN' : 'SUG'}</span>
                <div class="review-issue-body">
                  <div class="review-issue-title">${esc(issue.title)}</div>
                  <div class="review-issue-desc">${esc(issue.desc)}</div>
                  ${issue.line ? `<div class="review-issue-line">L${issue.line}${issue.text ? ' — ' + esc(issue.text.substring(0, 60)) : ''}</div>` : ''}
                </div>
              </div>
            `).join('')}
          </div>
        </div>`;
      }).join('')}
    </div>

    ${!fileEntries.length ? '<div style="text-align:center;padding:40px;color:var(--color-text-muted)">이슈가 발견되지 않았습니다</div>' : ''}`;

  // 파일 헤더 클릭 시 토글
  container.querySelectorAll('.review-file-header').forEach(el => {
    el.addEventListener('click', () => {
      const body = container.querySelector(`[data-review-body="${el.dataset.toggleReview}"]`);
      if (body) body.style.display = body.style.display === 'none' ? '' : 'none';
    });
  });
  // 이슈 클릭 시 해당 파일의 해당 라인으로 이동
  container.querySelectorAll('.review-issue').forEach(el => {
    el.style.cursor = 'pointer';
    el.addEventListener('click', async () => {
      const filePath = el.closest('.review-file')?.querySelector('.review-file-name')?.textContent;
      const lineNum = parseInt(el.querySelector('.review-issue-line')?.textContent?.match(/L(\d+)/)?.[1]) || 1;
      if (filePath && state.folderPath) {
        const fullPath = state.folderPath + '/' + filePath;
        await openFileInEditor(fullPath, filePath.split('/').pop());
        if (typeof monacoEditor !== 'undefined' && monacoEditor) {
          setTimeout(() => {
            monacoEditor.revealLineInCenter(lineNum);
            monacoEditor.setPosition({ lineNumber: lineNum, column: 1 });
            monacoEditor.focus();
          }, 300);
        }
      }
    });
  });
  // 다시 분석 버튼
  container.querySelector('#review-refresh-btn')?.addEventListener('click', () => {
    _reviewResults = null;
    loadReviewView();
  });
}

// 우측 패널에 검색 결과 리스트 표시
function showSearchInRightPanel() {
  const rpTab = document.getElementById('rp-tab-search');
  if (rpTab) rpTab.style.display = '';
  // 탭 활성화
  document.querySelectorAll('.rp-tab').forEach(t => t.classList.toggle('active', t.dataset.rp === 'search-panel'));
  document.getElementById('rp-chat-view').style.display = 'none';
  document.getElementById('rp-live-view').style.display = 'none';
  document.getElementById('rp-search-view').style.display = 'flex';
  // 검색 결과 복사
  const rpResults = document.getElementById('rp-search-results');
  if (!rpResults || !_searchResults.length) return;
  rpResults.innerHTML = `<div style="padding:6px 12px;font-size:11px;color:var(--color-text-muted)">${_searchResults.length}개 파일 · 검색어: "${esc(_searchQuery)}"</div>` +
    _searchResults.map((file, fi) => `
      <div style="border-bottom:1px solid var(--color-border-light)">
        <div style="padding:4px 12px;font-size:11px;color:var(--color-accent);font-family:var(--font-mono);font-weight:600">${esc(file.file)}</div>
        ${file.matches.map(m => `
          <div class="rp-search-line" data-file="${esc(file.file)}" data-line="${m.line}" style="padding:2px 12px 2px 24px;font-size:11px;color:var(--color-text-secondary);cursor:pointer;display:flex;gap:6px;transition:background 150ms;font-family:var(--font-mono)">
            <span style="color:var(--color-text-muted);min-width:30px;text-align:right">${m.line}</span>
            <span style="flex:1;overflow:hidden;text-overflow:ellipsis;white-space:nowrap">${highlightMatch(esc(m.text), _searchQuery)}</span>
          </div>
        `).join('')}
      </div>
    `).join('');
  // 클릭 이벤트
  rpResults.querySelectorAll('.rp-search-line').forEach(el => {
    el.addEventListener('mouseenter', () => { el.style.background = 'var(--color-bg-hover)'; });
    el.addEventListener('mouseleave', () => { el.style.background = ''; });
    el.addEventListener('click', async () => {
      const filePath = state.folderPath + '/' + el.dataset.file;
      const fileName = el.dataset.file.split('/').pop();
      const lineNum = parseInt(el.dataset.line) || 1;
      await openFileInEditor(filePath, fileName);
      if (typeof monacoEditor !== 'undefined' && monacoEditor && window.monaco) {
        setTimeout(() => {
          monacoEditor.revealLineInCenter(lineNum);
          monacoEditor.setPosition({ lineNumber: lineNum, column: 1 });
          monacoEditor.focus();
          if (_searchQuery) {
            const model = monacoEditor.getModel();
            if (model) {
              if (window._searchDecorations) monacoEditor.deltaDecorations(window._searchDecorations, []);
              const matches = model.findMatches(_searchQuery, false, false, _searchCaseSensitive, null, true);
              const decorations = matches.map(m => ({
                range: m.range,
                options: { isWholeLine: false, inlineClassName: 'search-highlight-inline' }
              }));
              const lineMatch = matches.find(m => m.range.startLineNumber === lineNum);
              if (lineMatch) {
                decorations.push({ range: lineMatch.range, options: { isWholeLine: true, className: 'search-highlight-current-line' } });
                monacoEditor.setSelection(lineMatch.range);
              }
              window._searchDecorations = monacoEditor.deltaDecorations([], decorations);
            }
          }
        }, 300);
      }
    });
  });
}
