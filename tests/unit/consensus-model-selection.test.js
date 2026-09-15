/**
 * 합의(consensus) 모델 선택 배선 회귀 테스트
 *
 * ── 버그 ──────────────────────────────────────────────────────────────
 * `src/main.js`의 top-level `let _consensusModelId`는 classic script의
 * **global lexical binding**이며 `window` 속성이 아니다.
 * `src/model-dropdown-ui.js`는 `renderConsensusDropdownList`를 오버라이드하면서
 * `window._consensusModelId = m.id`로 대입했고, 이는 렉시컬 바인딩과 **별개 슬롯**을
 * 만든다. 결과적으로 드롭다운 버튼 라벨은 바뀌지만 `runConsensus()`가 읽는
 *     const consensusModelId = _consensusModelId || pickConsensusModel();
 * 는 갱신되지 않아 항상 `CONSENSUS_MODEL_PRIORITY[0]`(Opus 4.7)로 합의가 돌았다.
 *
 * ── 이 테스트가 고정하는 성질 ────────────────────────────────────────
 *   1. 드롭다운에서 모델을 고르면 `runConsensus()`의 모델 결정식이 그 모델을 낸다
 *      (accessor를 거치지 않고 realm 내부 바인딩을 직접 평가해 확인)
 *   2. 선택은 우선순위 1순위(Opus 계열)를 실제로 덮어쓴다 — 보고된 증상 그 자체
 *   3. 드롭다운 체크 표시(`isSelected`)가 방금 고른 모델을 가리킨다
 *   4. 우회 슬롯(`window._consensusModelId`)에 쓰지 않는다 — 같은 버그 재발 방지
 *
 * 검증 대상은 `src/main.js` + `src/model-dropdown-ui.js` **실제 파일**이며,
 * index.html과 동일한 순서로 하나의 realm에 로드해 classic script 스코프 규칙을
 * 그대로 재현한다(jest `testEnvironment: 'node'` / 신규 런타임 의존성 없음).
 */

const fs = require('fs');
const path = require('path');
const vm = require('vm');

const REPO_ROOT = path.resolve(__dirname, '..', '..');

// 우선순위 1순위 — main.js의 CONSENSUS_MODEL_PRIORITY 선두와 일치해야 버그가 재현된다.
const PRIORITY_HEAD = 'anthropic.claude-opus-4-7';
// 사용자가 고르는 모델 — 우선순위 목록에 없는 값이어야 "선택이 반영됐는지"가 분명해진다.
const PICKED_ID = 'acme.zx-7-turbo:1';
const PICKED_NAME = 'ZX-7 Turbo';

// ─────────────────────────────────────────────────────────────────
// 최소 DOM — 두 스크립트가 실제로 쓰는 API만 지원한다.
// ─────────────────────────────────────────────────────────────────
function createDom() {
  const byId = new Map();

  class ClassList {
    constructor() { this._set = new Set(); }
    add(...v) { v.forEach(x => this._set.add(x)); }
    remove(...v) { v.forEach(x => this._set.delete(x)); }
    contains(v) { return this._set.has(v); }
    toggle(v, on) {
      if (on === undefined) { this._set.has(v) ? this._set.delete(v) : this._set.add(v); }
      else if (on) this._set.add(v); else this._set.delete(v);
    }
  }

  class Element {
    constructor(tag) {
      this.localName = String(tag).toLowerCase();
      this.tagName = this.localName.toUpperCase();
      this.childNodes = [];
      this.parentNode = null;
      this._attrs = new Map();
      this._listeners = new Map();
      this.style = { cssText: '', display: '' };
      this.classList = new ClassList();
      this.className = '';
      this.textContent = '';
      this.value = '';
      this._innerHTML = '';
      this.onclick = null;
      this.oninput = null;
      this.disabled = false;
    }
    get id() { return this._attrs.get('id') || ''; }
    set id(v) { this._attrs.set('id', String(v)); byId.set(String(v), this); }
    setAttribute(n, v) { this._attrs.set(n, String(v)); if (n === 'id') byId.set(String(v), this); }
    getAttribute(n) { return this._attrs.has(n) ? this._attrs.get(n) : null; }
    get innerHTML() { return this._innerHTML; }
    set innerHTML(v) { this._innerHTML = String(v); this.childNodes = []; }
    appendChild(child) { child.parentNode = this; this.childNodes.push(child); if (child.id) byId.set(child.id, child); return child; }
    addEventListener(t, fn) { if (!this._listeners.has(t)) this._listeners.set(t, []); this._listeners.get(t).push(fn); }
    removeEventListener() {}
    querySelector() { return null; }
    querySelectorAll() { return []; }
    closest() { return null; }
    focus() {}
    get options() { return this.childNodes.filter(c => c.localName === 'option'); }
  }

  const document = {
    body: new Element('body'),
    head: new Element('head'),
    documentElement: new Element('html'),
    createElement: (tag) => new Element(tag),
    getElementById: (id) => byId.get(id) || null,
    querySelector: () => null,
    querySelectorAll: () => [],
    addEventListener: () => {},
    removeEventListener: () => {},
  };

  /** id를 가진 빈 div를 등록한다. */
  const mount = (id) => { const el = new Element('div'); el.id = id; return el; };

  return { document, Element, byId, mount };
}

/**
 * index.html과 같은 순서(main.js → model-dropdown-ui.js)로 두 파일을 한 realm에 로드한다.
 * model-dropdown-ui.js가 main.js의 렌더 함수를 오버라이드하는 실제 배치를 재현한다.
 */
function loadRenderer() {
  const dom = createDom();

  const sandbox = {
    console: { log: () => {}, warn: () => {}, error: () => {}, info: () => {}, debug: () => {} },
    setTimeout, clearTimeout, setInterval: () => 0, clearInterval, queueMicrotask,
    Promise, Set, Map, JSON, Math, Date, Number, String, Boolean, Array, Object, Error, RegExp,
    document: dom.document,
    HTMLElement: class {},
    CustomEvent: class { constructor(t, o) { this.type = t; Object.assign(this, o || {}); } },
    customElements: { get: () => undefined, define: () => {} },
    fetch: () => Promise.reject(new Error('network disabled in unit test')),
    AbortController: class { constructor() { this.signal = {}; } abort() {} },
    AbortSignal: { timeout: () => ({}) },
    localStorage: { getItem: () => null, setItem: () => {}, removeItem: () => {} },
    TextDecoder: class { decode() { return ''; } },
    electronAPI: {
      loadEffortSettings: () => Promise.resolve({ schemaVersion: 1, entries: [] }),
      saveEffortSettings: () => Promise.resolve({ ok: true }),
      loadCapabilityMap: () => Promise.resolve(null),
    },
  };
  sandbox.window = sandbox;
  sandbox.globalThis = sandbox;

  const context = vm.createContext(sandbox);
  for (const file of ['src/main.js', 'src/model-dropdown-ui.js']) {
    const code = fs.readFileSync(path.join(REPO_ROOT, file), 'utf-8');
    vm.runInContext(code, context, { filename: file });
  }

  // 카탈로그 주입 — MODEL_CATALOG/ALL_MODELS는 top-level const이지만 가변 컨테이너다.
  // 우선순위 1순위와 사용자가 고를 모델을 함께 넣어 버그 조건을 그대로 만든다.
  const models = [
    { id: PRIORITY_HEAD, name: 'Claude Opus 4.7', capabilities: { chat: true } },
    { id: PICKED_ID, name: PICKED_NAME, capabilities: { chat: true } },
  ];
  vm.runInContext(
    `MODEL_CATALOG['acme'] = ${JSON.stringify(models)};` +
    `ALL_MODELS.push(...${JSON.stringify(models)});` +
    `globalThis.__state = state;`,
    context,
  );
  sandbox.state = sandbox.__state;
  sandbox.state.parallelResults = new Map();

  /** realm 내부에서 식을 평가한다(accessor를 우회해 실제 바인딩을 본다). */
  const evalInRealm = (expr) => {
    vm.runInContext(`globalThis.__probe = (${expr});`, context);
    return sandbox.__probe;
  };

  return { sandbox, dom, context, evalInRealm };
}

/** 렌더된 드롭다운 트리에서 클릭 가능한 항목만 모은다(헤더는 onclick이 없다). */
function collectClickableItems(root) {
  const out = [];
  const walk = (node) => {
    if (!node) return;
    if (typeof node.onclick === 'function') out.push(node);
    (node.childNodes || []).forEach(walk);
  };
  walk(root);
  return out;
}

function clickModelItem(listEl, modelName) {
  const item = collectClickableItems(listEl).find(el => String(el.innerHTML).includes(modelName));
  if (!item) throw new Error(`드롭다운에 '${modelName}' 항목이 렌더되지 않았다`);
  item.onclick({ stopPropagation: () => {} });
  return item;
}

// ─────────────────────────────────────────────────────────────────

describe('합의 모델 선택 배선 (src/main.js + src/model-dropdown-ui.js)', () => {
  let env;
  let list;

  beforeEach(() => {
    env = loadRenderer();
    // 합의 드롭다운이 실제로 쓰는 노드만 마운트한다.
    for (const id of ['consensus-dropdown-list', 'consensus-dropdown-btn',
                      'consensus-dropdown-menu', 'consensus-model-search',
                      'consensus-btn', 'parallel-count-label']) {
      env.dom.document.body.appendChild(env.dom.mount(id));
    }
    list = env.dom.document.getElementById('consensus-dropdown-list');
  });

  test('model-dropdown-ui.js가 합의 드롭다운 렌더를 오버라이드한다 (전제)', () => {
    expect(typeof env.sandbox.renderConsensusDropdownList).toBe('function');
    // 오버라이드는 window 속성으로 얹히므로 sandbox에서 직접 보인다.
    expect(env.sandbox.window.renderConsensusDropdownList).toBe(env.sandbox.renderConsensusDropdownList);
  });

  test('드롭다운 선택이 runConsensus()의 모델 결정식에 반영된다', () => {
    // 선택 전: 아무것도 안 골랐으면 우선순위 1순위로 떨어진다.
    expect(env.evalInRealm('_consensusModelId || pickConsensusModel()')).toBe(PRIORITY_HEAD);

    env.sandbox.renderConsensusDropdownList('');
    clickModelItem(list, PICKED_NAME);

    // 핵심: runConsensus()가 쓰는 식을 그대로 평가한다(accessor 우회 없이).
    expect(env.evalInRealm('_consensusModelId')).toBe(PICKED_ID);
    expect(env.evalInRealm('_consensusModelId || pickConsensusModel()')).toBe(PICKED_ID);
  });

  test('선택이 우선순위 1순위(Opus)를 덮어쓴다 — 보고된 증상', () => {
    // updateConsensus()가 하는 것처럼 1순위를 먼저 심어 놓는다.
    env.sandbox.window.setConsensusModel(PRIORITY_HEAD);
    expect(env.evalInRealm('_consensusModelId')).toBe(PRIORITY_HEAD);

    env.sandbox.renderConsensusDropdownList('');
    clickModelItem(list, PICKED_NAME);

    expect(env.evalInRealm('_consensusModelId')).not.toBe(PRIORITY_HEAD);
    expect(env.evalInRealm('_consensusModelId')).toBe(PICKED_ID);
  });

  test('버튼 라벨과 내부 바인딩이 같은 모델을 가리킨다', () => {
    env.sandbox.renderConsensusDropdownList('');
    clickModelItem(list, PICKED_NAME);

    const btn = env.dom.document.getElementById('consensus-dropdown-btn');
    expect(btn.textContent).toContain(PICKED_NAME);
    // 라벨만 바뀌고 바인딩은 그대로였던 것이 원래 버그다.
    const boundName = env.evalInRealm(
      `(ALL_MODELS.find(m => m.id === _consensusModelId) || {}).name`,
    );
    expect(boundName).toBe(PICKED_NAME);
  });

  test('재렌더 시 방금 고른 모델이 선택 상태로 표시된다', () => {
    env.sandbox.renderConsensusDropdownList('');
    clickModelItem(list, PICKED_NAME);

    env.sandbox.renderConsensusDropdownList('');
    const picked = collectClickableItems(list).find(el => String(el.innerHTML).includes(PICKED_NAME));
    const other = collectClickableItems(list).find(el => String(el.innerHTML).includes('Claude Opus 4.7'));
    expect(picked.className).toContain('selected');
    expect(other.className).not.toContain('selected');
  });

  test('우회 슬롯 window._consensusModelId에 쓰지 않는다', () => {
    env.sandbox.renderConsensusDropdownList('');
    clickModelItem(list, PICKED_NAME);

    // 이 속성이 다시 생기면 렉시컬 바인딩과 갈라지는 같은 버그가 재발한 것이다.
    expect(Object.prototype.hasOwnProperty.call(env.sandbox, '_consensusModelId')).toBe(false);
  });

  test('setConsensusModel(null)은 자동 선택 경로로 되돌린다', () => {
    env.sandbox.window.setConsensusModel(PICKED_ID);
    expect(env.evalInRealm('_consensusModelId')).toBe(PICKED_ID);

    env.sandbox.window.setConsensusModel(null);
    expect(env.evalInRealm('_consensusModelId')).toBeNull();
    expect(env.evalInRealm('_consensusModelId || pickConsensusModel()')).toBe(PRIORITY_HEAD);
  });
});
