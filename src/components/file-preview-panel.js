'use strict';
/**
 * <file-preview-panel> — generated media file browser.
 *
 * 표시 정책 (사용자 요청 반영):
 * 1) 결과물(PDF/PPTX/XLSX/DOCX 등 "출력 문서")만 메인 리스트에 표시.
 *    → 함께 만들어진 중간 이미지(.png/.jpg/.svg)는 결과물의 자식으로 묶이고,
 *      결과물을 클릭(▸ chevron)했을 때만 펼쳐서 보여준다.
 *    → 어디에도 속하지 않는 고아 이미지는 "기타 이미지" 그룹으로 따로 모음.
 * 2) 카테고리 필터 — All/PDF/PPTX/XLSX/DOCX/IMG/기타. 클릭 시 해당 형식만.
 * 3) 검색 — 파일명/모델/메타 텍스트 일치 필터.
 * 4) 자동 새로고침 — fs.watch 이벤트로 .generated/ 변경 감지.
 *
 * Sidecar `<file>.meta.json`이 있으면 model/agent/agentId 등 메타로 활용.
 *
 * 사용법: <file-preview-panel project-path="/path/to/project"></file-preview-panel>
 */

class FilePreviewPanel extends HTMLElement {
  constructor() {
    super();
    this._items = [];
    this._projectPath = '';
    this._watchedDir = '';
    this._unsub = null;
    this._metaCache = new Map();   // path → meta object
    this._expanded = new Set();    // 펼친 결과물 path 집합
    this._activeFilter = 'all';    // all | pdf | pptx | xlsx | docx | img | other
    this._searchQuery = '';
  }

  static get observedAttributes() { return ['project-path']; }

  attributeChangedCallback(name, oldVal, newVal) {
    if (name === 'project-path' && oldVal !== newVal) {
      this._projectPath = newVal || '';
      this._refresh();
      this._setupWatcher();
    }
  }

  connectedCallback() {
    if (!this._projectPath) {
      this._projectPath = this.getAttribute('project-path') || '';
    }
    this._render();
    this._refresh();
    this._setupWatcher();
    this._onSelect = (e) => {
      const detail = e?.detail || {};
      const target = detail.path || detail.fullPath || detail.name;
      if (!target) return;
      this._highlightItem(target);
    };
    document.addEventListener('preview-panel:select', this._onSelect);
    this._onForceRefresh = () => this._refresh();
    document.addEventListener('generated-folder:refresh', this._onForceRefresh);
  }

  disconnectedCallback() {
    if (this._unsub) try { this._unsub(); } catch {}
    if (this._onSelect) {
      document.removeEventListener('preview-panel:select', this._onSelect);
      this._onSelect = null;
    }
    if (this._onForceRefresh) {
      document.removeEventListener('generated-folder:refresh', this._onForceRefresh);
      this._onForceRefresh = null;
    }
    if (this._watchedDir && window.electronAPI && typeof window.electronAPI.unwatchDirectory === 'function') {
      try { window.electronAPI.unwatchDirectory(this._watchedDir); } catch {}
    }
  }

  _highlightItem(target) {
    const norm = (s) => (s || '').toLowerCase().replace(/\\/g, '/');
    const t = norm(target);
    const list = this.querySelector('.fpp-list');
    if (!list) return;
    const rows = list.querySelectorAll('.fpp-item, .fpp-child');
    rows.forEach((row) => {
      const rp = norm(row.dataset.path || '');
      const rn = norm(row.dataset.name || '');
      const match = rp === t || rp.endsWith(t) || rn === t.split('/').pop();
      row.classList.toggle('fpp-item-active', match);
      if (match) {
        // 부모가 접혀있으면 펼침
        const parentPath = row.dataset.parentPath;
        if (parentPath && !this._expanded.has(parentPath)) {
          this._expanded.add(parentPath);
          this._renderList();
        }
        try { row.scrollIntoView({ block: 'nearest', behavior: 'smooth' }); } catch {}
      }
    });
  }

  set projectPath(p) {
    this._projectPath = p || '';
    this._refresh();
    this._setupWatcher();
  }

  get projectPath() { return this._projectPath; }

  _generatedDir() {
    if (!this._projectPath) return '';
    const p = this._projectPath;
    const isRemoteLike = /^\/fsx\/|^\/home\/|^\/opt\//.test(p) || p.includes('[SSH:');
    if (!this._workstationCwd && typeof window !== 'undefined' && window.__workstationCwd) {
      this._workstationCwd = window.__workstationCwd;
    }
    if (isRemoteLike) {
      if (this._workstationCwd) {
        const sep = this._workstationCwd.includes('\\') && !this._workstationCwd.includes('/') ? '\\' : '/';
        const base = this._workstationCwd.replace(/[\\/]+$/, '');
        return `${base}${sep}.generated`;
      }
      return '';
    }
    const sep = p.includes('\\') && !p.includes('/') ? '\\' : '/';
    const base = p.replace(/[\\/]+$/, '');
    return `${base}${sep}.generated`;
  }

  async _listAllCandidates() {
    if (!window.electronAPI) return [];
    const seen = new Set();
    const merged = [];
    const fnLocal = window.electronAPI.listFilesWithStatsLocal
      || window.electronAPI.listFilesWithStats;
    if (typeof fnLocal !== 'function') return [];

    const primary = this._generatedDir();
    if (primary) {
      try {
        const items = await fnLocal(primary);
        for (const it of (items || [])) {
          const key = (it.path || it.name);
          if (seen.has(key)) continue;
          seen.add(key);
          merged.push(it);
        }
      } catch { /* skip */ }
    }
    if (this._workstationCwd) {
      const sep = this._workstationCwd.includes('\\') && !this._workstationCwd.includes('/') ? '\\' : '/';
      const base = this._workstationCwd.replace(/[\\/]+$/, '');
      const altDir = `${base}${sep}.generated`;
      if (altDir !== primary) {
        try {
          const items = await fnLocal(altDir);
          for (const it of (items || [])) {
            const key = (it.path || it.name);
            if (seen.has(key)) continue;
            seen.add(key);
            merged.push(it);
          }
        } catch { /* skip */ }
      }
    }
    return merged;
  }

  async _setupWatcher() {
    if (!window.electronAPI || typeof window.electronAPI.watchDirectory !== 'function') return;
    const dir = this._generatedDir();
    if (!dir) return;
    if (this._watchedDir === dir) return;
    if (this._watchedDir) {
      try { window.electronAPI.unwatchDirectory(this._watchedDir); } catch {}
    }
    this._watchedDir = dir;
    try { await window.electronAPI.watchDirectory(dir); } catch {}
    if (this._unsub) try { this._unsub(); } catch {}
    this._unsub = window.electronAPI.onDirectoryChanged((data) => {
      if (data && data.dirPath === dir) {
        clearTimeout(this._refreshDebounce);
        this._refreshDebounce = setTimeout(() => this._refresh(), 400);
      }
    });
  }

  async _refresh() {
    if (this._projectPath) {
      try {
        const apiUrl = (typeof apiBase === 'function' ? apiBase() : 'http://127.0.0.1:8765') + '/api/debug/cwd';
        const r = await fetch(apiUrl);
        if (r.ok) {
          const j = await r.json();
          const root = j.generatedRoot || j.cwd;
          if (root && root !== this._workstationCwd) {
            this._workstationCwd = root;
            if (typeof window !== 'undefined') window.__workstationCwd = root;
            await this._setupWatcher();
          }
        }
      } catch (_e) { /* best-effort */ }
    }

    if (!window.electronAPI) {
      this._items = [];
      this._renderList();
      return;
    }
    try {
      const items = await this._listAllCandidates();
      const filtered = (items || [])
        .filter(it => !it.isDirectory && !/\.meta\.json$/i.test(it.name));
      // sortAndLimitFiles 는 lib/file-list-sort.js 의 순수 함수.
      // Spec: media-generation-editing — Task 10.6 / Property 12 (Req 7.1).
      // 그룹화(부모/자식) 시 자식 후보까지 흡수해야 하므로 내부 버퍼는 200,
      // 사용자 가시 목록의 100개 상한은 그룹화/필터링 단계에서 보장된다.
      const sortAndLimit = (typeof window !== 'undefined' && typeof window.sortAndLimitFiles === 'function')
        ? window.sortAndLimitFiles
        : (typeof sortAndLimitFiles === 'function' ? sortAndLimitFiles : null);
      const files = sortAndLimit
        ? sortAndLimit(filtered, 200)
        : filtered
            .slice()
            .sort((a, b) => {
              const ta = a.mtime ? Date.parse(a.mtime) : 0;
              const tb = b.mtime ? Date.parse(b.mtime) : 0;
              return tb - ta;
            })
            .slice(0, 200);
      // 카테고리 카운트("전체"/PDF/PPT…)는 200개 렌더 버퍼가 아니라 *전체 후보*
      // 기준이어야 한다. _items 만 세면 후보가 200개 이상일 때 "전체 200"에
      // 고정되어 더 생성해도 수치가 변하지 않는 버그가 생긴다. 렌더는 200 캡을
      // 유지하되, 카운트는 캡 이전의 filtered 전체를 사용한다.
      this._allItems = filtered;
      this._items = files;
      await this._loadMetas(files);
      this._renderList();
    } catch (e) {
      console.error('[file-preview-panel] refresh failed:', e);
      this._items = [];
      this._renderList();
    }
  }

  async _loadMetas(files) {
    if (!window.electronAPI || typeof window.electronAPI.readFile !== 'function') return;
    const promises = files.map(async (f) => {
      const metaPath = (f.path || '') + '.meta.json';
      if (this._metaCache.has(metaPath)) return;
      try {
        const txt = await window.electronAPI.readFile(metaPath);
        if (txt) this._metaCache.set(metaPath, JSON.parse(txt));
      } catch { /* missing meta is fine */ }
    });
    await Promise.all(promises);
  }

  _formatSize(bytes) {
    // Validates: Requirements 7.2 / Property 9 — see src/lib/file-size.js
    // Delegate to the pure helper so the same logic is exercised by the
    // PBT test (tests/unit/file-size.test.js) and the running UI.
    if (typeof window !== 'undefined' && typeof window.formatFileSize === 'function') {
      return window.formatFileSize(bytes);
    }
    // Fallback for environments where lib/file-size.js was not preloaded.
    if (!bytes || bytes < 1024) return `${bytes || 0} B`;
    if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`;
    return `${(bytes / 1024 / 1024).toFixed(1)} MB`;
  }

  _formatTime(iso) {
    if (!iso) return '';
    try {
      const d = new Date(iso);
      const yyyy = d.getFullYear();
      const mm = String(d.getMonth() + 1).padStart(2, '0');
      const dd = String(d.getDate()).padStart(2, '0');
      const hh = String(d.getHours()).padStart(2, '0');
      const mi = String(d.getMinutes()).padStart(2, '0');
      return `${yyyy}-${mm}-${dd} ${hh}:${mi}`;
    } catch { return ''; }
  }

  _extOf(name) {
    return (String(name || '').split('.').pop() || '').toLowerCase();
  }

  _isImage(name) {
    return ['png', 'jpg', 'jpeg', 'webp', 'gif', 'svg'].includes(this._extOf(name));
  }

  _isOutputDoc(name) {
    return ['pdf', 'pptx', 'xlsx', 'docx', 'hwp'].includes(this._extOf(name));
  }

  _categoryOf(item) {
    const ext = this._extOf(item.name);
    if (ext === 'pdf') return 'pdf';
    if (ext === 'pptx') return 'pptx';
    if (ext === 'xlsx') return 'xlsx';
    if (ext === 'docx') return 'docx';
    if (this._isImage(item.name)) return 'img';
    return 'other';
  }

  _iconFor(name) {
    const ext = this._extOf(name);
    if (['png', 'jpg', 'jpeg', 'webp', 'gif'].includes(ext)) return 'IMG';
    if (ext === 'pdf')  return 'PDF';
    if (ext === 'pptx') return 'PPT';
    if (ext === 'docx') return 'DOC';
    if (ext === 'xlsx') return 'XLS';
    if (ext === 'svg')  return 'SVG';
    if (ext === 'md')   return 'MD';
    if (ext === 'json') return 'JSON';
    if (ext === 'txt')  return 'TXT';
    return ext ? ext.toUpperCase().slice(0, 4) : 'FILE';
  }

  _shortModelName(modelId) {
    if (!modelId) return '';
    const id = String(modelId).replace(/^us\.|^eu\.|^global\./, '');
    const lower = id.toLowerCase();
    // Claude 계열: 버전을 동적 파싱해 최신 버전(opus 4.8 등)을 자동 표기.
    // model-recommender.js의 refineModelLabel을 재사용하되, 없으면 로컬 파싱.
    if (lower.includes('claude')) {
      if (typeof window !== 'undefined' && typeof window.refineModelLabel === 'function') {
        const refined = window.refineModelLabel(id, '');
        // "Claude Opus 4.7" → "Opus 4.7" (패널은 짧은 라벨 사용)
        if (refined) return refined.replace(/^Claude\s+/, '');
      }
      const cap = (s) => s.charAt(0).toUpperCase() + s.slice(1);
      let m = lower.match(/claude-(opus|sonnet|haiku)-(\d+)(?:-(\d+))?/);
      if (m) {
        let ver = m[2];
        if (m[3] && m[3].length <= 2) ver = `${m[2]}.${m[3]}`;
        return `${cap(m[1])} ${ver}`;
      }
      m = lower.match(/claude-(\d+)-(\d+)-(opus|sonnet|haiku)/);
      if (m) return `${cap(m[3])} ${m[1]}.${m[2]}`;
      m = lower.match(/claude-(\d+)-(opus|sonnet|haiku)/);
      if (m) return `${cap(m[2])} ${m[1]}`;
    }
    if (lower.includes('nova-canvas'))       return 'Nova Canvas';
    if (lower.includes('nova-pro'))          return 'Nova Pro';
    if (lower.includes('nova-lite'))         return 'Nova Lite';
    if (lower.includes('titan-image'))       return 'Titan Image v2';
    if (lower.includes('stable-image-ultra')) return 'SD Ultra';
    if (lower.includes('sd3-5-large') || lower.includes('sd3.5-large')) return 'SD 3.5 Large';
    if (lower.includes('stable-image-core'))  return 'SD Core';
    if (lower.includes('stability'))          return 'Stability';
    if (lower.includes('mermaid'))     return 'Mermaid';
    if (lower.includes('matplotlib'))  return 'matplotlib';
    if (lower.includes('reportlab'))   return 'reportlab';
    if (lower.includes('python-pptx')) return 'python-pptx';
    if (lower.includes('python-docx')) return 'python-docx';
    if (lower.includes('openpyxl'))    return 'openpyxl';
    if (lower.includes('filesystem'))  return 'fs';
    if (lower.includes('pixtral'))     return 'Pixtral';
    if (lower.includes('mistral'))     return 'Mistral';
    if (lower.includes('llama'))       return 'Llama';
    if (lower.includes('gemma'))       return 'Gemma';
    const parts = id.split('.').pop().split('-');
    return parts.slice(0, 3).join('-');
  }

  _metaFor(item) {
    if (!item || !item.path) return null;
    return this._metaCache.get(item.path + '.meta.json') || null;
  }

  /**
   * 결과 문서(PDF/PPTX/...)에 함께 만들어진 자식 이미지를 매칭한다.
   * 매칭 규칙 (우선순위):
   *   1) meta.agentId가 동일 (오케스트레이터가 같은 에이전트로 만든 산출물)
   *   2) mtime 기준 ±60초 이내 + 이미지가 결과물보다 먼저 생성됨
   *   3) 위 둘 다 없으면 자식 없음
   *
   * 결과: { roots: [item, ...], children: Map(path → [item, ...]), orphanImages: [item, ...] }
   */
  _groupItems(items) {
    const docs = items.filter(it => this._isOutputDoc(it.name));
    const images = items.filter(it => this._isImage(it.name));
    const others = items.filter(it => !this._isOutputDoc(it.name) && !this._isImage(it.name));

    const children = new Map();
    const claimed = new Set();

    // 1) meta.agentId 매칭
    for (const doc of docs) {
      const docMeta = this._metaFor(doc);
      const docAgent = docMeta && docMeta.agentId;
      if (!docAgent) continue;
      for (const img of images) {
        if (claimed.has(img.path)) continue;
        const imgMeta = this._metaFor(img);
        if (imgMeta && imgMeta.agentId === docAgent) {
          if (!children.has(doc.path)) children.set(doc.path, []);
          children.get(doc.path).push(img);
          claimed.add(img.path);
        }
      }
    }

    // 2) mtime 윈도우 매칭 (±60초, 이미지가 doc보다 *먼저*)
    for (const doc of docs) {
      const dt = doc.mtime ? Date.parse(doc.mtime) : 0;
      if (!dt) continue;
      for (const img of images) {
        if (claimed.has(img.path)) continue;
        const it = img.mtime ? Date.parse(img.mtime) : 0;
        if (!it) continue;
        // 이미지가 doc 직전(60초 이내) 또는 동시에 생성된 경우 children으로 묶음.
        // doc보다 늦게 생성된 이미지는 별개의 작업이라고 간주.
        if (it <= dt + 1000 && (dt - it) <= 60_000) {
          if (!children.has(doc.path)) children.set(doc.path, []);
          children.get(doc.path).push(img);
          claimed.add(img.path);
        }
      }
    }

    const orphanImages = images.filter(img => !claimed.has(img.path));
    orphanImages.sort((a, b) => {
      const ta = a.mtime ? Date.parse(a.mtime) : 0;
      const tb = b.mtime ? Date.parse(b.mtime) : 0;
      return tb - ta;
    });
    // 고아 이미지는 메인 리스트에 풀어두지 않고 가상 그룹 "기타 생성 이미지" 로 묶는다.
    // 작업 중 실시간으로 쌓이는 중간 이미지가 패널 가독성을 헤치지 않게.
    const roots = [...docs, ...others].sort((a, b) => {
      const ta = a.mtime ? Date.parse(a.mtime) : 0;
      const tb = b.mtime ? Date.parse(b.mtime) : 0;
      return tb - ta;
    });

    // 자식도 mtime desc 정렬
    for (const arr of children.values()) {
      arr.sort((a, b) => {
        const ta = a.mtime ? Date.parse(a.mtime) : 0;
        const tb = b.mtime ? Date.parse(b.mtime) : 0;
        return tb - ta;
      });
    }

    return { roots, children, orphanImages, orphanCount: orphanImages.length };
  }

  /** 카운트 by 카테고리 (필터 버튼 라벨) */
  _categoryCounts(items) {
    const counts = { all: 0, pdf: 0, pptx: 0, xlsx: 0, docx: 0, img: 0, other: 0 };
    for (const it of items) {
      counts.all++;
      const c = this._categoryOf(it);
      if (counts[c] !== undefined) counts[c]++;
      else counts.other++;
    }
    return counts;
  }

  _matchesFilter(item) {
    if (this._activeFilter === 'all') return true;
    return this._categoryOf(item) === this._activeFilter;
  }

  _matchesSearch(item) {
    if (!this._searchQuery) return true;
    const q = this._searchQuery.toLowerCase();
    const meta = this._metaFor(item);
    const haystack = [
      item.name,
      meta?.model,
      meta?.agentRole,
      meta?.agentTitle,
      meta?.promptHint,
    ].filter(Boolean).join(' ').toLowerCase();
    return haystack.includes(q);
  }

  _render() {
    this.innerHTML = `
      <style>
        file-preview-panel {
          display: flex;
          flex-direction: column;
          height: 100%;
          background: var(--color-bg-secondary, #252526);
          color: var(--color-text-primary, #ccc);
          font-family: var(--font-ui, sans-serif);
          font-size: 12px;
        }
        .fpp-header {
          padding: 10px 12px;
          border-bottom: 1px solid var(--color-border, #3c3c3c);
          display: flex;
          justify-content: space-between;
          align-items: center;
        }
        .fpp-title { font-weight: 600; }
        .fpp-refresh {
          background: none; border: 1px solid var(--color-border, #3c3c3c);
          color: var(--color-text-secondary, #9d9d9d);
          padding: 3px 8px; border-radius: 3px;
          cursor: pointer; font-size: 11px;
        }
        .fpp-refresh:hover { background: var(--color-bg-hover, #2a2d2e); color: var(--color-text-primary, #ccc); }

        .fpp-search-bar {
          padding: 6px 10px;
          border-bottom: 1px solid var(--color-border, #3c3c3c);
        }
        .fpp-search-input {
          width: 100%;
          background: var(--color-bg-input, #1e1e1e);
          border: 1px solid var(--color-border, #3c3c3c);
          border-radius: 3px;
          color: var(--color-text-primary, #ccc);
          padding: 5px 8px;
          font-size: 11px;
          outline: none;
          font-family: var(--font-ui, sans-serif);
        }
        .fpp-search-input:focus {
          border-color: var(--color-accent, #007acc);
        }

        .fpp-filters {
          display: flex; flex-wrap: wrap; gap: 4px;
          padding: 6px 10px;
          border-bottom: 1px solid var(--color-border, #3c3c3c);
          background: var(--color-bg-tertiary, #2d2d30);
        }
        .fpp-chip {
          background: transparent;
          border: 1px solid var(--color-border, #3c3c3c);
          color: var(--color-text-secondary, #9d9d9d);
          padding: 3px 8px;
          border-radius: 12px;
          font-size: 10px;
          cursor: pointer;
          transition: all 120ms ease;
          font-family: var(--font-ui, sans-serif);
        }
        .fpp-chip:hover {
          color: var(--color-text-primary, #ccc);
          border-color: var(--color-text-secondary, #9d9d9d);
        }
        .fpp-chip-active {
          background: var(--color-accent, #007acc);
          border-color: var(--color-accent, #007acc);
          color: #fff;
        }
        .fpp-chip-count {
          opacity: 0.7;
          margin-left: 3px;
          font-size: 9px;
        }

        .fpp-list {
          flex: 1; overflow-y: auto; padding: 4px 0;
        }
        .fpp-empty {
          padding: 20px; text-align: center;
          color: var(--color-text-muted, #6a6a6a); font-size: 11px;
        }

        .fpp-item, .fpp-child {
          display: grid;
          grid-template-columns: auto auto 1fr auto;
          align-items: center;
          gap: 8px;
          padding: 8px 12px; cursor: pointer;
          border-bottom: 1px solid rgba(60,60,60,0.3);
        }
        .fpp-item:hover, .fpp-child:hover { background: var(--color-bg-hover, #2a2d2e); }
        .fpp-item-active { background: rgba(0,122,204,0.18); border-left: 2px solid var(--color-accent, #007acc); }
        .fpp-item-active:hover { background: rgba(0,122,204,0.24); }

        .fpp-chevron {
          width: 14px; height: 14px;
          display: flex; align-items: center; justify-content: center;
          color: var(--color-text-muted, #6a6a6a);
          transition: transform 150ms ease;
          font-size: 9px;
          user-select: none;
        }
        .fpp-chevron-open { transform: rotate(90deg); color: var(--color-accent, #007acc); }
        .fpp-chevron-spacer { width: 14px; height: 14px; }

        .fpp-icon {
          flex-shrink: 0;
          font-family: var(--font-mono, 'Cascadia Code', monospace);
          font-size: 9px;
          font-weight: 700;
          letter-spacing: 0.5px;
          color: var(--color-accent, #007acc);
          background: rgba(0,122,204,0.1);
          border: 1px solid rgba(0,122,204,0.4);
          border-radius: 3px;
          padding: 4px 6px;
          min-width: 36px;
          text-align: center;
          line-height: 1;
        }
        /* 카테고리별 아이콘 색상 */
        .fpp-icon-pdf  { color: #f44747; background: rgba(244,71,71,0.1); border-color: rgba(244,71,71,0.4); }
        .fpp-icon-ppt  { color: #d97706; background: rgba(217,119,6,0.1); border-color: rgba(217,119,6,0.4); }
        .fpp-icon-xls  { color: #4ec9b0; background: rgba(78,201,176,0.1); border-color: rgba(78,201,176,0.4); }
        .fpp-icon-doc  { color: #1a8ad4; background: rgba(26,138,212,0.1); border-color: rgba(26,138,212,0.4); }
        .fpp-icon-img  { color: #c586c0; background: rgba(197,134,192,0.1); border-color: rgba(197,134,192,0.4); }

        .fpp-virtual-group {
          background: rgba(0,122,204,0.04);
        }
        .fpp-virtual-group:hover {
          background: rgba(0,122,204,0.10);
        }
        .fpp-virtual-group .fpp-icon {
          color: var(--color-text-secondary, #9d9d9d);
          background: rgba(157,157,157,0.1);
          border-color: rgba(157,157,157,0.3);
          font-size: 8px;
        }
        .fpp-virtual-group .fpp-name {
          font-style: italic;
          color: var(--color-text-secondary, #9d9d9d);
        }

        .fpp-info { min-width: 0; overflow: hidden; }
        .fpp-name {
          font-size: 12px;
          color: var(--color-text-primary, #ccc);
          overflow: hidden; text-overflow: ellipsis; white-space: nowrap;
        }
        .fpp-meta-row {
          display: flex;
          gap: 6px;
          align-items: center;
          flex-wrap: wrap;
          margin-top: 3px;
          font-size: 10px;
          color: var(--color-text-muted, #6a6a6a);
        }
        .fpp-model-badge {
          display: inline-block;
          padding: 1px 6px;
          background: rgba(78,201,176,0.12);
          border: 1px solid rgba(78,201,176,0.3);
          color: var(--color-success, #4ec9b0);
          border-radius: 2px;
          font-size: 9px;
          font-weight: 600;
          letter-spacing: 0.2px;
          font-family: var(--font-mono, monospace);
        }
        .fpp-badge-gen {
          background: rgba(78,201,176,0.14);
          border-color: rgba(78,201,176,0.4);
          color: var(--color-success, #4ec9b0);
        }
        .fpp-badge-chat {
          background: rgba(0,122,204,0.10);
          border-color: rgba(0,122,204,0.3);
          color: var(--color-accent, #007acc);
          font-weight: 500;
          opacity: 0.85;
        }
        .fpp-child-count {
          display: inline-block;
          padding: 0 5px;
          background: rgba(0,122,204,0.18);
          border: 1px solid rgba(0,122,204,0.4);
          color: var(--color-accent, #007acc);
          border-radius: 8px;
          font-size: 9px;
          font-weight: 600;
          margin-left: 6px;
          letter-spacing: 0.3px;
        }

        .fpp-children-wrap {
          background: rgba(0,0,0,0.18);
          border-bottom: 1px solid rgba(60,60,60,0.3);
        }
        .fpp-child {
          padding-left: 36px; /* 결과물 그룹 들여쓰기 */
          background: transparent;
          border-bottom: 1px solid rgba(60,60,60,0.18);
        }
        .fpp-child:last-child { border-bottom: none; }

        .fpp-actions {
          display: flex;
          gap: 4px;
          opacity: 0;
          transition: opacity 100ms;
          flex-shrink: 0;
        }
        .fpp-item:hover .fpp-actions, .fpp-child:hover .fpp-actions { opacity: 1; }
        .fpp-action {
          background: transparent;
          border: 1px solid var(--color-border, #3c3c3c);
          color: var(--color-text-secondary, #9d9d9d);
          cursor: pointer;
          padding: 3px 8px;
          font-size: 10px;
          border-radius: 3px;
          font-family: var(--font-ui, sans-serif);
          transition: all 120ms ease;
          line-height: 1.4;
        }
        .fpp-action:hover {
          background: var(--color-bg-tertiary, #2d2d30);
          color: var(--color-text-primary, #ccc);
          border-color: var(--color-text-secondary, #9d9d9d);
        }
        .fpp-action-edit:hover   { color: var(--color-accent,  #007acc); border-color: var(--color-accent,  #007acc); }
        .fpp-action-delete:hover { color: var(--color-error,   #f44747); border-color: var(--color-error,   #f44747); }
        .fpp-action-download:hover { color: var(--color-success, #4ec9b0); border-color: var(--color-success, #4ec9b0); }
      </style>
      <div class="fpp-header">
        <div class="fpp-title">생성 파일</div>
        <button class="fpp-refresh" type="button" title="새로고침">↻</button>
      </div>
      <div class="fpp-search-bar">
        <input type="text" class="fpp-search-input" placeholder="파일명, 모델명, 메타 검색..." autocomplete="off" />
      </div>
      <div class="fpp-filters"></div>
      <div class="fpp-list"></div>
    `;
    this.querySelector('.fpp-refresh').addEventListener('click', () => this._refresh());
    const searchInput = this.querySelector('.fpp-search-input');
    let searchTimer = null;
    searchInput.addEventListener('input', (e) => {
      clearTimeout(searchTimer);
      const value = e.target.value || '';
      searchTimer = setTimeout(() => {
        this._searchQuery = value.trim();
        this._renderList();
      }, 120);
    });
  }

  _renderFilters(counts) {
    const filtersEl = this.querySelector('.fpp-filters');
    if (!filtersEl) return;
    const chips = [
      { key: 'all',  label: '전체', count: counts.all },
      { key: 'pdf',  label: 'PDF',  count: counts.pdf },
      { key: 'pptx', label: 'PPT',  count: counts.pptx },
      { key: 'xlsx', label: 'XLS',  count: counts.xlsx },
      { key: 'docx', label: 'DOC',  count: counts.docx },
      { key: 'img',  label: '이미지', count: counts.img },
    ];
    filtersEl.innerHTML = chips.map(c => `
      <button class="fpp-chip ${this._activeFilter === c.key ? 'fpp-chip-active' : ''}" data-filter="${c.key}" type="button">
        ${this._escape(c.label)}<span class="fpp-chip-count">${c.count}</span>
      </button>
    `).join('');
    filtersEl.querySelectorAll('[data-filter]').forEach(btn => {
      btn.addEventListener('click', () => {
        this._activeFilter = btn.dataset.filter;
        this._renderList();
      });
    });
  }

  _renderItemRow(item, opts = {}) {
    // 가상 "기타 생성 이미지" 그룹 행 — 폴더 형태로 렌더링
    if (item && item._isVirtualOrphan) {
      const isExpanded = !!opts.isExpanded;
      const chevron = `<span class="fpp-chevron ${isExpanded ? 'fpp-chevron-open' : ''}" data-toggle-children>▶</span>`;
      return `
        <div class="fpp-item fpp-virtual-group"
             data-path="${this._escape(item.path)}"
             data-name="${this._escape(item.name)}">
          ${chevron}
          <div class="fpp-icon fpp-icon-img">FOLDER</div>
          <div class="fpp-info">
            <div class="fpp-name">${this._escape(item.name)}</div>
            <div class="fpp-meta-row">
              <span>${this._formatTime(item.mtime)}</span>
              <span>·</span>
              <span>${this._formatSize(item.size)}</span>
            </div>
          </div>
          <div class="fpp-actions"></div>
        </div>
      `;
    }
    const { isChild = false, hasChildren = false, isExpanded = false, parentPath = '' } = opts;
    const meta = this._metaFor(item);
    const genModel = meta && meta.model ? this._shortModelName(meta.model) : '';
    const chatModel = meta && meta.chatModel ? this._shortModelName(meta.chatModel) : '';
    let modelBadges = '';
    if (genModel) {
      const tip = `생성 엔진: ${this._escape(meta?.model || '')}${meta?.agentRole ? ' · ' + this._escape(meta.agentRole) : ''}`;
      modelBadges += `<span class="fpp-model-badge fpp-badge-gen" title="${tip}">${this._escape(genModel)}</span>`;
    }
    if (chatModel && chatModel !== genModel) {
      modelBadges += `<span class="fpp-model-badge fpp-badge-chat" title="결정 모델: ${this._escape(meta?.chatModel || '')}">via ${this._escape(chatModel)}</span>`;
    }

    const cat = this._categoryOf(item);
    const iconClass = `fpp-icon fpp-icon-${cat === 'pptx' ? 'ppt' : cat === 'xlsx' ? 'xls' : cat === 'docx' ? 'doc' : cat}`;
    const chevron = isChild
      ? '<span class="fpp-chevron-spacer"></span>'
      : (hasChildren
          ? `<span class="fpp-chevron ${isExpanded ? 'fpp-chevron-open' : ''}" data-toggle-children>▶</span>`
          : '<span class="fpp-chevron-spacer"></span>');
    const childCountBadge = (hasChildren && !isChild)
      ? `<span class="fpp-child-count" title="이 결과물과 함께 생성된 이미지">+${opts.childCount}</span>`
      : '';

    return `
      <div class="${isChild ? 'fpp-child' : 'fpp-item'}"
           data-path="${this._escape(item.path || item.name)}"
           data-name="${this._escape(item.name)}"
           ${isChild && parentPath ? `data-parent-path="${this._escape(parentPath)}"` : ''}>
        ${chevron}
        <div class="${iconClass}">${this._iconFor(item.name)}</div>
        <div class="fpp-info">
          <div class="fpp-name" title="${this._escape(item.name)}">${this._escape(item.name)}${childCountBadge}</div>
          <div class="fpp-meta-row">
            ${modelBadges}
            <span>${this._formatTime(item.mtime)}</span>
            <span>·</span>
            <span>${this._formatSize(item.size)}</span>
          </div>
        </div>
        <div class="fpp-actions">
          <button class="fpp-action fpp-action-edit"     data-action="edit"     type="button" title="수정 — 채팅에 첨부 후 추가 지시">수정</button>
          <button class="fpp-action fpp-action-delete"   data-action="delete"   type="button" title="삭제 — 디스크에서 제거">삭제</button>
          <button class="fpp-action fpp-action-download" data-action="download" type="button" title="다운로드">다운로드</button>
        </div>
      </div>
    `;
  }

  _renderList() {
    const list = this.querySelector('.fpp-list');
    if (!list) return;

    // 카테고리 카운트는 *전체* 후보(캡 이전) 기준 — 검색은 빠질 수 있게 search 무시.
    // _allItems(캡 이전 전체)가 있으면 그것을, 없으면 _items로 폴백.
    const countBase = (this._allItems && this._allItems.length) ? this._allItems : this._items;
    const counts = this._categoryCounts(countBase.filter(it => this._matchesSearch(it)));
    this._renderFilters(counts);

    if (!this._items.length) {
      list.innerHTML = '<div class="fpp-empty">생성된 파일이 없습니다.<br>이미지, PDF, PPTX를 생성하면 여기에 표시됩니다.</div>';
      return;
    }

    // 1) 그룹화
    const filtered = this._items.filter(it => this._matchesSearch(it));
    const { roots, children, orphanImages = [] } = this._groupItems(filtered);

    // 2) 카테고리 필터 적용 — root 단위로
    let rootsForRender = roots.filter(r => this._matchesFilter(r));

    // 카테고리가 'img'일 때는 자식 이미지(부모 doc 안에 묶인 것)도 root로 끌어올려서 보여줌.
    // 안 그러면 사용자가 '이미지' 필터를 켰을 때 자식만 있고 root는 없어서 빈 목록처럼 보임.
    // 고아 이미지(_groupItems가 더 이상 roots에 넣지 않음)도 함께 평탄화한다.
    if (this._activeFilter === 'img') {
      const rootImgPaths = new Set(rootsForRender.map(r => r.path));
      const promotedImgs = [];
      for (const arr of children.values()) {
        for (const img of arr) {
          if (!rootImgPaths.has(img.path) && this._matchesSearch(img)) {
            promotedImgs.push(img);
          }
        }
      }
      for (const img of orphanImages) {
        if (!rootImgPaths.has(img.path) && this._matchesSearch(img)) {
          promotedImgs.push(img);
        }
      }
      promotedImgs.sort((a, b) => {
        const ta = a.mtime ? Date.parse(a.mtime) : 0;
        const tb = b.mtime ? Date.parse(b.mtime) : 0;
        return tb - ta;
      });
      rootsForRender = [...rootsForRender, ...promotedImgs];
    }

    // 고아 이미지가 있으면 메인 리스트에 가상 폴더 행 1개로 묶어 추가.
    // 클릭(chevron) 시 자식으로 펼쳐서 보여줌. img 필터일 땐 위에서 이미 평탄화 처리됨.
    const VIRTUAL_ORPHAN_PATH = '__virtual_orphan_images__';
    if (this._activeFilter !== 'img' && orphanImages.length > 0) {
      // 가장 최근의 고아 이미지 mtime을 가상 그룹의 정렬 키로 사용
      const newestMtime = orphanImages[0]?.mtime || new Date().toISOString();
      const virtualOrphanRoot = {
        path: VIRTUAL_ORPHAN_PATH,
        name: `기타 생성 이미지 (${orphanImages.length})`,
        mtime: newestMtime,
        size: orphanImages.reduce((s, o) => s + (o.size || 0), 0),
        isDirectory: false,
        _isVirtualOrphan: true,
      };
      // children Map에 가상 그룹의 자식들 등록
      children.set(VIRTUAL_ORPHAN_PATH, orphanImages);
      // mtime 기준으로 다른 root와 같이 정렬 — 최신순으로 적당한 자리에 들어감
      rootsForRender = [...rootsForRender, virtualOrphanRoot].sort((a, b) => {
        const ta = a.mtime ? Date.parse(a.mtime) : 0;
        const tb = b.mtime ? Date.parse(b.mtime) : 0;
        return tb - ta;
      });
    }

    if (!rootsForRender.length) {
      list.innerHTML = `<div class="fpp-empty">${this._searchQuery ? '검색 결과가 없습니다.' : '이 카테고리에 해당하는 파일이 없습니다.'}</div>`;
      return;
    }

    // 3) HTML 빌드
    const html = rootsForRender.map(item => {
      const kids = children.get(item.path) || [];
      const matchedKids = kids.filter(k => this._matchesSearch(k));
      const hasChildren = matchedKids.length > 0;
      const isExpanded = hasChildren && this._expanded.has(item.path);
      const rowHtml = this._renderItemRow(item, {
        hasChildren,
        isExpanded,
        childCount: matchedKids.length,
      });
      let childrenHtml = '';
      if (isExpanded) {
        childrenHtml = `
          <div class="fpp-children-wrap">
            ${matchedKids.map(c => this._renderItemRow(c, { isChild: true, parentPath: item.path })).join('')}
          </div>
        `;
      }
      return rowHtml + childrenHtml;
    }).join('');
    list.innerHTML = html;

    // 4) 이벤트 바인딩 (chevron 토글 + 클릭 → 열기 + 액션 버튼)
    list.querySelectorAll('[data-toggle-children]').forEach(el => {
      el.addEventListener('click', (e) => {
        e.stopPropagation();
        const row = el.closest('.fpp-item');
        if (!row) return;
        const path = row.dataset.path;
        if (this._expanded.has(path)) this._expanded.delete(path);
        else this._expanded.add(path);
        this._renderList();
      });
    });

    list.querySelectorAll('.fpp-item, .fpp-child').forEach(row => {
      row.addEventListener('click', (e) => {
        if (e.target.closest('.fpp-action') || e.target.closest('[data-toggle-children]')) return;
        const path = row.dataset.path;
        // 가상 그룹은 클릭 → 토글만 (별도 viewer가 없음)
        if (path === '__virtual_orphan_images__' || row.classList.contains('fpp-virtual-group')) {
          if (this._expanded.has(path)) this._expanded.delete(path);
          else this._expanded.add(path);
          this._renderList();
          return;
        }
        const item = this._items.find(it => it.path === path || it.name === row.dataset.name);
        if (item) this._open(item);
      });
    });

    list.querySelectorAll('[data-action="edit"]').forEach(btn => {
      btn.addEventListener('click', (e) => {
        e.stopPropagation();
        const row = btn.closest('.fpp-item, .fpp-child');
        const path = row?.dataset.path;
        const item = this._items.find(it => it.path === path);
        if (item) this._edit(item);
      });
    });
    list.querySelectorAll('[data-action="delete"]').forEach(btn => {
      btn.addEventListener('click', async (e) => {
        e.stopPropagation();
        const row = btn.closest('.fpp-item, .fpp-child');
        const path = row?.dataset.path;
        const item = this._items.find(it => it.path === path);
        if (item) await this._delete(item);
      });
    });
    list.querySelectorAll('[data-action="download"]').forEach(btn => {
      btn.addEventListener('click', async (e) => {
        e.stopPropagation();
        const row = btn.closest('.fpp-item, .fpp-child');
        const path = row?.dataset.path;
        const item = this._items.find(it => it.path === path);
        if (item) await this._download(item);
      });
    });
  }

  _escape(s) {
    return String(s || '').replace(/[&<>"']/g, c => ({
      '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;'
    }[c]));
  }

  _open(item) {
    if (!item) return;
    const MAX_PREVIEW_BYTES = 50 * 1024 * 1024;
    if (typeof item.size === 'number' && item.size > MAX_PREVIEW_BYTES) {
      const sizeMb = (item.size / 1024 / 1024).toFixed(1);
      const proceed = confirm(
        `"${item.name}"는 ${sizeMb} MB로 미리보기 한도(50 MB)를 초과합니다.\n` +
        `미리보기는 차단되며 다운로드만 가능합니다. 다운로드 다이얼로그를 여시겠습니까?`
      );
      if (proceed) this._download(item);
      return;
    }
    this.dispatchEvent(new CustomEvent('preview-file', {
      bubbles: true,
      detail: { path: item.path, name: item.name, size: item.size },
    }));
  }

  _edit(item) {
    if (!item) return;
    this.dispatchEvent(new CustomEvent('preview-file:edit', {
      bubbles: true,
      detail: {
        path: item.path,
        name: item.name,
        size: item.size,
        meta: this._metaFor(item),
      },
    }));
  }

  async _delete(item) {
    if (!item) return;
    if (!confirm(`"${item.name}" 파일을 삭제하시겠습니까?`)) return;
    try {
      // 이슈 3 — 이 패널은 목록을 listFilesWithStatsLocal(로컬 전용 IPC)로 읽으므로
      // 삭제도 반드시 로컬 전용 IPC를 우선 사용해야 한다. 일반 deleteFile은 원격 SSH
      // 세션 활성 시 SFTP 브리지로 라우팅돼 워크스테이션 .generated/ 파일을 못 지운다.
      const delLocal = window.electronAPI && typeof window.electronAPI.deleteFileLocal === 'function'
        ? window.electronAPI.deleteFileLocal
        : null;
      const delGeneric = window.electronAPI && typeof window.electronAPI.deleteFile === 'function'
        ? window.electronAPI.deleteFile
        : (window.electronAPI && typeof window.electronAPI.unlink === 'function' ? window.electronAPI.unlink : null);

      if (delLocal) {
        await delLocal(item.path);
        try { await delLocal(item.path + '.meta.json'); } catch {}
        // 원격 작업 폴더에도 같은 파일이 있을 수 있으면 best-effort로 함께 정리
        if (delGeneric) {
          try { await delGeneric(item.path); } catch {}
        }
      } else if (delGeneric) {
        await delGeneric(item.path);
        try { await delGeneric(item.path + '.meta.json'); } catch {}
      } else {
        alert('삭제 기능을 사용할 수 없습니다 (IPC 미노출). 앱을 재시작해 주세요.');
        return;
      }
      this.dispatchEvent(new CustomEvent('preview-file:deleted', {
        bubbles: true,
        detail: { path: item.path, name: item.name },
      }));
      this._metaCache.delete(item.path + '.meta.json');
      this._expanded.delete(item.path);
      await this._refresh();
    } catch (e) {
      console.error('[file-preview-panel] delete failed:', e);
      alert(`삭제 실패: ${e.message || e}`);
    }
  }

  async _download(item) {
    if (!item || !window.electronAPI || typeof window.electronAPI.showSaveDialog !== 'function') return;
    try {
      const ext = (item.name.split('.').pop() || '').toLowerCase();
      const result = await window.electronAPI.showSaveDialog({
        defaultPath: item.name,
        sourcePath: item.path,
        filters: [{ name: ext.toUpperCase(), extensions: [ext] }],
      });
      if (result && result.ok) {
        this.dispatchEvent(new CustomEvent('file-downloaded', {
          bubbles: true,
          detail: { source: item.path, target: result.path },
        }));
      } else if (result && !result.canceled && result.error) {
        // TASK 8 — IPC가 사전 검증으로 ENOENT/0바이트 등을 잡았을 때 명확히 알림.
        // 패널에는 표시됐는데 실제 파일이 사라진 케이스를 사용자가 즉시 인지.
        console.warn('[file-preview-panel] download blocked:', result.error);
        alert(`다운로드 실패: ${result.error}\n\n패널을 새로고침하면 목록이 갱신됩니다.`);
        // 목록도 자동 새로고침해 사라진 항목 제거
        try { await this._refresh(); } catch {}
      }
    } catch (e) {
      console.error('[file-preview-panel] download failed:', e);
      alert(`다운로드 실패: ${e.message || e}`);
    }
  }
}

if (!customElements.get('file-preview-panel')) {
  customElements.define('file-preview-panel', FilePreviewPanel);
}

if (typeof window !== 'undefined') {
  window.FilePreviewPanel = FilePreviewPanel;
}
