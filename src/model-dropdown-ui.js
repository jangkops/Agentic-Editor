/* ===== Model Dropdown UI — 카테고리 분류 + 통합 렌더링 =====
 * main.js의 renderModelList / renderParallelDropdownList / renderConsensusDropdownList를
 * 오버라이드하여 카테고리별 그룹 분류 + 통일된 UI를 제공합니다.
 * 이 파일은 main.js 이후에 로드되어야 합니다 (index.html에서 순서 보장).
 */

(function() {
  'use strict';

  // ─── 공통 렌더링 함수 ─────────────────────────────────────────────────
  function _renderCategorizedModelList(listEl, query, onSelect, opts) {
    if (!listEl) return;
    listEl.innerHTML = '';
    const q = (query || '').toLowerCase();
    const showPrefix = opts && opts.showPrefix;
    const isSelected = (opts && opts.isSelected) || (() => false);

    const groups = { chat: [], image_gen: [], video_gen: [], embedding: [], rerank: [] };
    const groupLabels = { chat: '채팅', image_gen: '이미지 생성', video_gen: '비디오 생성', embedding: '임베딩', rerank: '리랭크' };

    for (const [p, ms] of Object.entries(MODEL_CATALOG)) {
      for (const m of ms) {
        if (q && !m.name.toLowerCase().includes(q) && !p.toLowerCase().includes(q) && !(m.id || '').toLowerCase().includes(q)) continue;
        const caps = m.capabilities || {};
        const tagged = { ...m, provider: p };
        if (caps.image_gen) groups.image_gen.push(tagged);
        else if (caps.video_gen) groups.video_gen.push(tagged);
        else if (caps.embedding) groups.embedding.push(tagged);
        else if (caps.rerank) groups.rerank.push(tagged);
        else groups.chat.push(tagged);
      }
    }

    // 전체 합계 헤더 — 탑바(ALL_MODELS.length)와 일치시켜 개수 불일치 오해 제거.
    // 두 값 모두 MODEL_CATALOG에서 파생되므로 필터 없을 때 total === 탑바 개수.
    const _total = Object.values(groups).reduce((n, arr) => n + arr.length, 0);
    const _chatCount = groups.chat.length;
    if (_total > 0) {
      const summary = document.createElement('div');
      summary.className = 'model-dropdown-summary';
      summary.style.cssText = 'padding:8px 14px;font-size:11px;font-weight:700;color:var(--color-text-primary);background:var(--color-bg-tertiary);border-bottom:1px solid var(--color-border);display:flex;justify-content:space-between;position:sticky;top:0;z-index:2;';
      summary.innerHTML = `<span>전체 ${_total}개 모델</span><span style="color:var(--color-text-muted);font-weight:500;">채팅 ${_chatCount} · 미디어/기타 ${_total - _chatCount}</span>`;
      listEl.appendChild(summary);
    }

    for (const [capKey, models] of Object.entries(groups)) {
      if (!models.length) continue;
      const g = document.createElement('div');
      g.className = 'model-dropdown-group';

      // 카테고리 헤더
      const cat = document.createElement('div');
      cat.className = 'model-dropdown-group-title';
      cat.style.cssText = 'color:var(--color-accent);font-weight:700;font-size:11px;padding:10px 14px 6px;display:flex;justify-content:space-between;background:var(--color-bg-secondary);text-transform:uppercase;letter-spacing:0.5px;';
      cat.innerHTML = `<span>${groupLabels[capKey]}</span><span style="color:var(--color-text-muted);font-weight:500;font-size:10px;text-transform:none;letter-spacing:0;">${models.length}개</span>`;
      g.appendChild(cat);

      if (capKey === 'chat') {
        // 프로바이더별 서브그룹
        const byProvider = {};
        models.forEach(m => { (byProvider[m.provider] = byProvider[m.provider] || []).push(m); });
        for (const [prov, pms] of Object.entries(byProvider)) {
          const _pu = (typeof _providerUsage === 'function') ? _providerUsage(prov) : '';
          const ph = document.createElement('div');
          ph.style.cssText = 'font-size:10px;color:var(--color-text-muted);padding:6px 14px 2px 18px;text-transform:uppercase;font-weight:600;letter-spacing:0.4px;display:flex;justify-content:space-between;';
          ph.innerHTML = `<span>${prov} (${pms.length})</span>${_pu ? `<span style="font-weight:400;text-transform:none;letter-spacing:0;">${_pu}</span>` : ''}`;
          g.appendChild(ph);
          pms.forEach(m => g.appendChild(_buildItem(m, onSelect, showPrefix, isSelected)));
        }
      } else {
        models.forEach(m => g.appendChild(_buildItem(m, onSelect, showPrefix, isSelected)));
      }
      listEl.appendChild(g);
    }
  }

  function _buildItem(m, onSelect, showPrefix, isSelected) {
    const i = document.createElement('div');
    i.className = 'model-dropdown-item' + (isSelected(m) ? ' selected' : '');
    i.style.cssText = 'display:flex;align-items:center;gap:8px;padding:6px 14px 6px 22px;';
    const prefix = showPrefix ? '<span style="width:14px;display:inline-block;text-align:center;color:var(--color-success);flex-shrink:0;">+</span>' : '';
    i.innerHTML = `${prefix}<span style="flex:1;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;">${m.name}</span><span style="font-size:9px;color:var(--color-text-muted);flex-shrink:0;margin-left:6px;">${m.provider}</span>`;
    i.onclick = (ev) => { ev.stopPropagation(); onSelect(m); };
    return i;
  }

  // ─── 오버라이드 ─────────────────────────────────────────────────────────
  window.renderModelList = function(f) {
    const list = document.getElementById('model-dropdown-list');
    _renderCategorizedModelList(list, f, (m) => {
      state.selectedModel = m;
      document.getElementById('model-dropdown-btn').textContent = m.name + ' ▾';
      document.getElementById('model-dropdown-menu').style.display = 'none';
      document.getElementById('status-model').textContent = m.name;
    }, {
      isSelected: (m) => state.selectedModel && m.id === state.selectedModel.id,
    });
  };

  window.renderParallelDropdownList = function(f) {
    const list = document.getElementById('parallel-dropdown-list');
    _renderCategorizedModelList(list, f, (m) => {
      if (typeof addParallelSlot === 'function') addParallelSlot(m.id);
      window.renderParallelDropdownList(document.getElementById('parallel-model-search')?.value || '');
    }, {
      showPrefix: true,
      isSelected: () => false,
    });
  };

  window.renderConsensusDropdownList = function(filter) {
    const list = document.getElementById('consensus-dropdown-list');
    _renderCategorizedModelList(list, filter, (m) => {
      if (typeof _consensusModelId !== 'undefined') window._consensusModelId = m.id;
      document.getElementById('consensus-dropdown-btn').textContent = m.name + ' ▾';
      document.getElementById('consensus-dropdown-menu').style.display = 'none';
    }, {
      isSelected: (m) => (typeof _consensusModelId !== 'undefined') && m.id === _consensusModelId,
    });
  };

  console.log('[model-dropdown-ui] 카테고리 분류 드롭다운 오버라이드 적용됨');
})();
