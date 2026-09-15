/**
 * gateway-models-effort-support Task 14.2 — 모델 목록·요청 payload seam 검증
 *
 * 검증 대상 (`src/main.js` + `src/effort-control.js` 실제 파일을 그대로 로드한다):
 *   1. capability payload 없음 → `state.capabilities` 미설정, effort UI 미렌더,
 *      `_apiBody()` 결과에 `effort` 키 부재 (기존 경로와 동일 — 무회귀)
 *   2. capability payload 있음(effort SUPPORTED) → 선택 확정 시 tuple 전달,
 *      셀렉트 박스가 verified domain만 노출, 선택 시 IPC 저장 + payload에 effort 부착
 *   3. 모델 변경으로 tuple 불일치 → UI 숨김 + 저장 값 제거 + payload에 effort 부재
 *   4. fingerprint 변경(STALE) → 저장 값 제거, 복원은 tuple 3요소 일치 시에만
 *
 * Requirements: 6.14, 7.4, 7.5, 7.6, 7.11, 7.12, 7.13, 7.14
 *
 * 이 테스트는 jest `testEnvironment: 'node'`에서 동작하도록 최소 DOM을 직접 구현한다
 * (프로젝트 스택 제약: 신규 런타임 의존성 추가 금지).
 */

const fs = require('fs');
const path = require('path');
const vm = require('vm');

const REPO_ROOT = path.resolve(__dirname, '..', '..');

// ─────────────────────────────────────────────────────────────────
// 최소 DOM 구현 — effort-control.js가 실제로 쓰는 API만 지원한다.
// ─────────────────────────────────────────────────────────────────
function createDom() {
  const byId = new Map();

  class ClassList {
    constructor() { this._set = new Set(); }
    add(...v) { v.forEach(x => this._set.add(x)); }
    remove(...v) { v.forEach(x => this._set.delete(x)); }
    contains(v) { return this._set.has(v); }
    toggle(v, on) { if (on === undefined) { this._set.has(v) ? this._set.delete(v) : this._set.add(v); } else if (on) this._set.add(v); else this._set.delete(v); }
    get value() { return Array.from(this._set).join(' '); }
  }

  class Node {
    constructor() {
      this.childNodes = [];
      this.parentNode = null;
      this._listeners = new Map();
    }
    appendChild(child) {
      child.parentNode = this;
      this.childNodes.push(child);
      if (child.id) byId.set(child.id, child);
      if (typeof child.connectedCallback === 'function') child.connectedCallback();
      return child;
    }
    removeChild(child) {
      const i = this.childNodes.indexOf(child);
      if (i >= 0) this.childNodes.splice(i, 1);
      child.parentNode = null;
      return child;
    }
    addEventListener(type, fn) {
      if (!this._listeners.has(type)) this._listeners.set(type, []);
      this._listeners.get(type).push(fn);
    }
    removeEventListener(type, fn) {
      const arr = this._listeners.get(type) || [];
      const i = arr.indexOf(fn);
      if (i >= 0) arr.splice(i, 1);
    }
    dispatchEvent(ev) {
      if (!ev.target) ev.target = this;
      let node = this;
      while (node) {
        const arr = (node._listeners.get(ev.type) || []).slice();
        for (const fn of arr) fn.call(node, ev);
        node = ev.bubbles ? node.parentNode : null;
      }
      return true;
    }
  }

  class Element extends Node {
    constructor(tag) {
      super();
      this.localName = String(tag).toLowerCase();
      this.tagName = this.localName.toUpperCase();
      this._attrs = new Map();
      this.style = {};
      this.classList = new ClassList();
      this.textContent = '';
      this._innerHTML = '';
      // <select> 상태
      this.selectedIndex = 0;
      this.value = '';
    }
    get id() { return this._attrs.get('id') || ''; }
    set id(v) { this._attrs.set('id', String(v)); byId.set(String(v), this); }
    setAttribute(name, value) {
      this._attrs.set(name, String(value));
      if (name === 'id') byId.set(String(value), this);
    }
    getAttribute(name) { return this._attrs.has(name) ? this._attrs.get(name) : null; }
    removeAttribute(name) { this._attrs.delete(name); }
    hasAttribute(name) { return this._attrs.has(name); }
    get innerHTML() { return this._innerHTML; }
    set innerHTML(v) { this._innerHTML = String(v); this.childNodes = []; }
    querySelector() { return null; }
    querySelectorAll() { return []; }
    closest() { return null; }
    get options() { return this.childNodes.filter(c => c.localName === 'option'); }
  }

  class HTMLElement extends Element {
    constructor() { super('custom-element'); }
  }

  class CustomEvent {
    constructor(type, opts) {
      this.type = type;
      const o = opts || {};
      this.bubbles = !!o.bubbles;
      this.composed = !!o.composed;
      this.detail = o.detail;
      this.target = null;
    }
  }

  const registry = new Map();
  const customElements = {
    get: (name) => registry.get(name),
    define: (name, ctor) => {
      registry.set(name, ctor);
      // 정의 시점에 upgrade가 필요한 요소는 이 테스트에서 직접 생성한다.
      Object.defineProperty(ctor.prototype, '_localNameOverride', { value: name, configurable: true });
    },
  };

  const documentNode = new Element('#document');
  const head = new Element('head');
  const body = new Element('body');
  documentNode.appendChild(head);
  documentNode.appendChild(body);

  const document = {
    head,
    body,
    documentElement: new Element('html'),
    createElement: (tag) => {
      const ctor = registry.get(String(tag).toLowerCase());
      if (ctor) {
        const el = new ctor();
        el.localName = String(tag).toLowerCase();
        el.tagName = el.localName.toUpperCase();
        return el;
      }
      return new Element(tag);
    },
    getElementById: (id) => byId.get(id) || null,
    querySelector: () => null,
    querySelectorAll: () => [],
    addEventListener: (t, fn) => documentNode.addEventListener(t, fn),
    removeEventListener: (t, fn) => documentNode.removeEventListener(t, fn),
    dispatchEvent: (ev) => documentNode.dispatchEvent(ev),
    _node: documentNode,
    _byId: byId,
  };

  return { document, HTMLElement, Element, CustomEvent, customElements, registry, byId };
}

/**
 * `src/effort-control.js`와 `src/main.js`를 하나의 sandbox에서 로드한다.
 * main.js는 실행 즉시 DOM에 접근하지 않는 코드만 top-level에 두므로 그대로 평가된다.
 */
function loadRenderer(options) {
  const opts = options || {};
  const dom = createDom();
  const saved = [];
  const sandbox = {
    console: { log: () => {}, warn: () => {}, error: () => {}, info: () => {}, debug: () => {} },
    setTimeout, clearTimeout, setInterval: () => 0, clearInterval, queueMicrotask,
    Promise, Set, Map, JSON, Math, Date, Number, String, Boolean, Array, Object, Error,
    document: dom.document,
    HTMLElement: dom.HTMLElement,
    CustomEvent: dom.CustomEvent,
    customElements: dom.customElements,
    fetch: () => Promise.reject(new Error('network disabled in unit test')),
    AbortController: class { constructor() { this.signal = {}; } abort() {} },
    AbortSignal: { timeout: () => ({}) },
    localStorage: { getItem: () => null, setItem: () => {}, removeItem: () => {} },
    TextDecoder: class { decode() { return ''; } },
    electronAPI: {
      loadEffortSettings: () => Promise.resolve(opts.storedSettings || { schemaVersion: 1, entries: [] }),
      saveEffortSettings: (s) => { saved.push(s); return Promise.resolve({ ok: true, entries: s.entries.length }); },
      loadCapabilityMap: () => Promise.resolve(null),
    },
  };
  sandbox.window = sandbox;
  sandbox.globalThis = sandbox;

  const context = vm.createContext(sandbox);
  for (const file of ['src/effort-control.js', 'src/main.js']) {
    const code = fs.readFileSync(path.join(REPO_ROOT, file), 'utf-8');
    vm.runInContext(code, context, { filename: file });
  }
  // main.js의 top-level `const state`/`const MODEL_CATALOG`는 global lexical binding이라
  // sandbox 객체 프로퍼티가 아니다. 같은 realm의 후속 script로 참조만 끌어온다.
  vm.runInContext('globalThis.__state = state; globalThis.__catalog = MODEL_CATALOG;', context);
  sandbox.state = sandbox.__state;

  // <effort-control id="effort-control">를 index.html과 동일 위치(모델 바)에 마운트한다.
  const EffortControl = dom.registry.get('effort-control');
  const el = new EffortControl();
  el.localName = 'effort-control';
  el.tagName = 'EFFORT-CONTROL';
  el.id = 'effort-control';
  dom.document.body.appendChild(el);

  return { sandbox, dom, el, saved };
}

// ─────────────────────────────────────────────────────────────────
// capability payload fixture — 값은 전부 무작위 심볼이며 확정 상수가 아니다.
// ─────────────────────────────────────────────────────────────────
const MODEL_A = 'zeta.qx-9-r2:0';
const MODEL_B = 'kappa.wt-3:1';
const FP_A = 'cfp1:sha256:' + 'a'.repeat(64);
const FP_A2 = 'cfp1:sha256:' + 'b'.repeat(64);
const FP_B = 'cfp1:sha256:' + 'c'.repeat(64);
const ROUTE = 'OPENAI_RESPONSES';
const KNOWN_ROUTES = ['CONVERSE', 'INVOKE', 'OPENAI_RESPONSES', 'OPENAI_RESPONSES_JOBS', 'SSE_STREAM'];

function routeViews(supportedRoute) {
  const out = {};
  for (const r of KNOWN_ROUTES) {
    out[r] = {
      status: r === supportedRoute ? 'SUPPORTED' : 'NOT_ADVERTISED',
      allowlist: r === supportedRoute ? 'ALLOWED' : 'UNVERIFIED',
      executionMode: r === supportedRoute ? 'SYNC' : null,
      purposes: r === supportedRoute ? ['chat'] : [],
      fallbackRank: 0,
    };
  }
  return out;
}

function effortViews(supportedRoute, domain) {
  const out = {};
  for (const r of KNOWN_ROUTES) {
    // domain이 없으면 `to_ui_payload`는 상태만 남기고 domain 필드를 싣지 않는다.
    out[r] = (domain && r === supportedRoute)
      ? Object.assign({ status: 'SUPPORTED', supported: true, verifiedValues: [] }, domain)
      : { status: 'UNVERIFIED', supported: false };
  }
  return out;
}

function modelView(modelId, fingerprint, domain) {
  return {
    modelId,
    provider: 'omega',
    capabilityFingerprint: fingerprint,
    verificationStatus: 'VERIFIED',
    syncSupport: 'SUPPORTED',
    asyncSupport: 'NOT_ADVERTISED',
    streamingSupport: 'NOT_ADVERTISED',
    routes: routeViews(ROUTE),
    effort: effortViews(ROUTE, domain),
    effortRoutes: domain ? [ROUTE] : [],
  };
}

const ENUM_DOMAIN = { valueType: 'STRING', domainKind: 'ENUM', enumValues: ['qa', 'qb', 'qc'], verifiedValues: ['qa', 'qb', 'qc'] };
const RANGE_DOMAIN = { valueType: 'INTEGER', domainKind: 'RANGE', rangeLowerInclusive: 2, rangeUpperInclusive: 5, verifiedValues: [2, 5] };

function payloadWith(models) {
  return { schemaVersion: 1, modelIds: models.map(m => m.modelId), models: Object.fromEntries(models.map(m => [m.modelId, m])) };
}

const flush = () => new Promise(r => setTimeout(r, 0));

// ─────────────────────────────────────────────────────────────────
// 1) capability payload 없음 → 기존 경로와 동일
// ─────────────────────────────────────────────────────────────────
describe('capability payload 없음 (무회귀 경로)', () => {
  test('state.capabilities 미설정 · effort UI 미렌더 · payload에 effort 부재', async () => {
    const { sandbox, el } = loadRenderer();
    await sandbox.initEffortControl();

    sandbox.state.settings = { awsProfile: 'p', bedrockUser: 'u' };
    sandbox.state.selectedModel = { id: MODEL_A, name: 'A', provider: 'omega' };

    expect('capabilities' in sandbox.state).toBe(false);
    expect(sandbox._currentEffortTuple()).toBeNull();
    expect(el.supported).toBe(false);
    expect(el.getAttribute('data-state')).toBeNull();
    expect(el.innerHTML).toBe('');

    const body = sandbox._apiBody({ prompt: 'hi', model: MODEL_A });
    expect('effort' in body).toBe(false);
    expect(body.model).toBe(MODEL_A);
  });

  test('capabilities 키가 없는 응답은 payload를 바꾸지 않는다', () => {
    const { sandbox } = loadRenderer();
    expect(sandbox._setCapabilitiesPayload(undefined)).toBe(false);
    expect(sandbox._setCapabilitiesPayload({})).toBe(false);
    expect('capabilities' in sandbox.state).toBe(false);
  });
});

// ─────────────────────────────────────────────────────────────────
// 2) capability payload 있음 → 셀렉트 박스 실제 동작
// ─────────────────────────────────────────────────────────────────
describe('capability payload 있음 (effort SUPPORTED)', () => {
  test('선택 확정 시 tuple 전달 · verified domain만 노출', async () => {
    const { sandbox, el } = loadRenderer();
    await sandbox.initEffortControl();
    sandbox.state.settings = { awsProfile: 'p', bedrockUser: 'u' };

    expect(sandbox._setCapabilitiesPayload(payloadWith([modelView(MODEL_A, FP_A, ENUM_DOMAIN)]))).toBe(true);
    sandbox._onCapabilitiesChanged();
    sandbox.state.selectedModel = { id: MODEL_A, name: 'A', provider: 'omega' };

    expect(sandbox._currentEffortTuple()).toEqual({ modelId: MODEL_A, route: ROUTE, capabilityFingerprint: FP_A });
    expect(el.supported).toBe(true);
    expect(el.getAttribute('data-state')).toBe('ready');
    expect(el.domainValues).toEqual(['qa', 'qb', 'qc']);

    // 첫 option은 항상 미선택(Gateway 기본 동작)
    const select = el.childNodes.find(c => c.localName === 'select');
    expect(select).toBeTruthy();
    expect(select.options.map(o => o.value)).toEqual(['', 'qa', 'qb', 'qc']);
  });

  test('셀렉트 박스 선택 → IPC 저장 + 요청 payload에 effort 부착', async () => {
    const { sandbox, el, saved } = loadRenderer();
    await sandbox.initEffortControl();
    sandbox.state.settings = { awsProfile: 'p', bedrockUser: 'u' };
    sandbox._setCapabilitiesPayload(payloadWith([modelView(MODEL_A, FP_A, ENUM_DOMAIN)]));
    sandbox._onCapabilitiesChanged();
    sandbox.state.selectedModel = { id: MODEL_A, name: 'A', provider: 'omega' };

    // 사용자 조작 시뮬레이션: 2번째 domain 값 선택
    const select = el.childNodes.find(c => c.localName === 'select');
    select.selectedIndex = 2;
    select.dispatchEvent(new sandbox.CustomEvent('change', { bubbles: true }));
    await flush();

    expect(el.value).toBe('qb');
    expect(saved.length).toBe(1);
    expect(saved[0].entries).toEqual([{
      modelId: MODEL_A, route: ROUTE, capabilityFingerprint: FP_A,
      value: 'qb', valueType: 'STRING', updatedAt: expect.any(String),
    }]);

    const body = sandbox._apiBody({ prompt: 'hi', model: MODEL_A });
    expect(body.effort).toEqual({
      modelId: MODEL_A, route: ROUTE, capabilityFingerprint: FP_A, value: 'qb', valueType: 'STRING',
    });
  });

  test('RANGE domain은 inclusive 경계를 포함한 정수만 노출한다', async () => {
    const { sandbox, el } = loadRenderer();
    await sandbox.initEffortControl();
    sandbox._setCapabilitiesPayload(payloadWith([modelView(MODEL_A, FP_A, RANGE_DOMAIN)]));
    sandbox._onCapabilitiesChanged();
    sandbox.state.selectedModel = { id: MODEL_A, name: 'A' };
    expect(el.domainValues).toEqual([2, 3, 4, 5]);
  });

  test('병렬 호출(models)에는 effort를 부착하지 않는다', async () => {
    const { sandbox, el } = loadRenderer();
    await sandbox.initEffortControl();
    sandbox.state.settings = { awsProfile: 'p', bedrockUser: 'u' };
    sandbox._setCapabilitiesPayload(payloadWith([modelView(MODEL_A, FP_A, ENUM_DOMAIN)]));
    sandbox._onCapabilitiesChanged();
    sandbox.state.selectedModel = { id: MODEL_A, name: 'A' };
    const select = el.childNodes.find(c => c.localName === 'select');
    select.selectedIndex = 1;
    select.dispatchEvent(new sandbox.CustomEvent('change', { bubbles: true }));

    const body = sandbox._apiBody({ prompt: 'hi', models: [MODEL_A, MODEL_B] });
    expect('effort' in body).toBe(false);
  });
});

// ─────────────────────────────────────────────────────────────────
// 3) tuple 불일치 → 즉시 숨김 + 저장 값 제거
// ─────────────────────────────────────────────────────────────────
describe('tuple 불일치 정리', () => {
  test('effort 미지원 모델로 변경 → UI 숨김 · payload에 effort 부재', async () => {
    const { sandbox, el } = loadRenderer();
    await sandbox.initEffortControl();
    sandbox.state.settings = { awsProfile: 'p', bedrockUser: 'u' };
    sandbox._setCapabilitiesPayload(payloadWith([
      modelView(MODEL_A, FP_A, ENUM_DOMAIN),
      modelView(MODEL_B, FP_B, null),
    ]));
    sandbox._onCapabilitiesChanged();

    sandbox.state.selectedModel = { id: MODEL_A, name: 'A' };
    const select = el.childNodes.find(c => c.localName === 'select');
    select.selectedIndex = 1;
    select.dispatchEvent(new sandbox.CustomEvent('change', { bubbles: true }));
    expect(sandbox._apiBody({ model: MODEL_A }).effort).toBeTruthy();

    // 모델 변경 → tuple 불일치
    sandbox.state.selectedModel = { id: MODEL_B, name: 'B' };
    expect(sandbox._currentEffortTuple()).toBeNull();
    expect(el.supported).toBe(false);
    expect(el.innerHTML).toBe('');
    expect('effort' in sandbox._apiBody({ model: MODEL_B })).toBe(false);

    // 원 모델로 복귀하면 tuple 3요소가 모두 일치하므로 복원된다 (7.14)
    sandbox.state.selectedModel = { id: MODEL_A, name: 'A' };
    expect(el.supported).toBe(true);
    expect(el.value).toBe('qa');
    expect(sandbox._apiBody({ model: MODEL_A }).effort.value).toBe('qa');
  });

  test('fingerprint 변경(STALE) → 저장 값 제거 · 복원 없음', async () => {
    const { sandbox, el, saved } = loadRenderer({
      storedSettings: {
        schemaVersion: 1,
        entries: [{ modelId: MODEL_A, route: ROUTE, capabilityFingerprint: FP_A, value: 'qa', valueType: 'STRING', updatedAt: '2026-08-03T00:00:00+00:00' }],
      },
    });
    sandbox._setCapabilitiesPayload(payloadWith([modelView(MODEL_A, FP_A, ENUM_DOMAIN)]));
    await sandbox.initEffortControl();
    sandbox.state.selectedModel = { id: MODEL_A, name: 'A' };
    expect(el.value).toBe('qa'); // tuple 일치 → 복원

    // fingerprint만 바뀐 새 payload = STALE 전이
    sandbox._setCapabilitiesPayload(payloadWith([modelView(MODEL_A, FP_A2, ENUM_DOMAIN)]));
    sandbox._onCapabilitiesChanged();
    expect(el.value).toBeNull();
    expect('effort' in sandbox._apiBody({ model: MODEL_A })).toBe(false);
    expect(saved.length).toBeGreaterThan(0);
    expect(saved[saved.length - 1].entries).toEqual([]);
  });

  test('route가 SUPPORTED를 잃으면 저장 값을 제거한다 (7.13)', async () => {
    const { sandbox, el } = loadRenderer({
      storedSettings: {
        schemaVersion: 1,
        entries: [{ modelId: MODEL_A, route: ROUTE, capabilityFingerprint: FP_A, value: 'qa', valueType: 'STRING', updatedAt: '2026-08-03T00:00:00+00:00' }],
      },
    });
    const degraded = modelView(MODEL_A, FP_A, ENUM_DOMAIN);
    degraded.routes[ROUTE].status = 'UNSUPPORTED';
    sandbox._setCapabilitiesPayload(payloadWith([degraded]));
    await sandbox.initEffortControl();
    sandbox.state.selectedModel = { id: MODEL_A, name: 'A' };

    expect(el.value).toBeNull();
    expect('effort' in sandbox._apiBody({ model: MODEL_A })).toBe(false);
  });

  test('domain 이탈 저장 값은 복원되지 않는다 (7.10)', async () => {
    const { sandbox, el } = loadRenderer({
      storedSettings: {
        schemaVersion: 1,
        entries: [{ modelId: MODEL_A, route: ROUTE, capabilityFingerprint: FP_A, value: 'zz', valueType: 'STRING', updatedAt: '2026-08-03T00:00:00+00:00' }],
      },
    });
    sandbox._setCapabilitiesPayload(payloadWith([modelView(MODEL_A, FP_A, ENUM_DOMAIN)]));
    await sandbox.initEffortControl();
    sandbox.state.selectedModel = { id: MODEL_A, name: 'A' };

    expect(el.supported).toBe(true);
    expect(el.value).toBeNull();
    expect('effort' in sandbox._apiBody({ model: MODEL_A })).toBe(false);
  });
});
