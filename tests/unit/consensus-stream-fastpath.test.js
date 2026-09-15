/**
 * 합의 스트리밍 in-place 갱신(fast-path) 회귀 테스트
 *
 * ── 버그 ──────────────────────────────────────────────────────────────
 * `_streamFastPath()`에 `if(last.isConsensus) return false;` 가드가 있어,
 * 합의 응답이 스트리밍되는 동안 청크마다 `renderMessages()`가 전체 재렌더로
 * 떨어졌다(`c.innerHTML=''` → 노드 전부 파괴·재생성). 그래서 합의 결과 본문이
 * 써지는 동안 화면이 계속 깜빡였다.
 *
 * 가드를 그냥 지우면 안 된다. 합의 메시지의 `.msg-content`는
 *     [합의 결과 헤더] + [.md-body 본문] + [.msg-action-bar 복사버튼]
 * 를 함께 담고 있어서, 일반 경로처럼 `mc.innerHTML`을 교체하면 헤더와 버튼이
 * 사라진다. 그래서 본문 컨테이너(`.md-body`)만 갱신하는 전용 분기가 필요하다.
 *
 * ── 이 테스트가 고정하는 성질 ────────────────────────────────────────
 *   1. 합의 스트리밍 중 fast-path가 성립한다(=전체 재렌더 안 함 → 깜빡임 없음)
 *   2. 본문(.md-body)은 새 내용으로 갱신된다
 *   3. 합의 결과 헤더와 액션바 노드가 **동일 객체로 살아남는다**(파괴 금지)
 *   4. 같은 길이로 다시 호출하면 본문을 다시 쓰지 않는다(불필요한 DOM 쓰기 0)
 *   5. `.md-body`가 아직 없으면(thinking → 본문 전환) 전체 재렌더에 맡긴다
 *
 * 검증 대상은 `src/lib/utils.js` + `src/main.js` 실제 파일이다.
 * jest `testEnvironment: 'node'` — 신규 런타임 의존성 없음.
 */

const fs = require('fs');
const path = require('path');
const vm = require('vm');

const REPO_ROOT = path.resolve(__dirname, '..', '..');

// ─────────────────────────────────────────────────────────────────
// 클래스 선택자만 지원하는 초소형 DOM.
// _streamFastPath()가 실제로 쓰는 선택자만 해석한다:
//   '.chat-msg.assistant' / '.msg-content' / '.thinking-indicator'
//   ':scope > .md-body'   / ':scope > .msg-action-bar'
// ─────────────────────────────────────────────────────────────────
class El {
  constructor(tag) {
    this.localName = String(tag || 'div').toLowerCase();
    this.childNodes = [];
    this.parentNode = null;
    this._attrs = new Map();
    this._classes = new Set();
    this.style = { cssText: '', display: '' };
    this._innerHTML = '';
    this.textContent = '';
    this.onclick = null;
    // 스크롤 관련 — fast-path의 하단 추종 로직이 읽는다.
    this.scrollHeight = 0;
    this.scrollTop = 0;
    this.clientHeight = 0;
  }

  get className() { return Array.from(this._classes).join(' '); }
  set className(v) {
    this._classes = new Set(String(v).split(/\s+/).filter(Boolean));
  }

  get id() { return this._attrs.get('id') || ''; }
  set id(v) { this._attrs.set('id', String(v)); }
  setAttribute(n, v) { this._attrs.set(n, String(v)); }
  getAttribute(n) { return this._attrs.has(n) ? this._attrs.get(n) : null; }

  get innerHTML() { return this._innerHTML; }
  set innerHTML(v) {
    this._innerHTML = String(v);
    // 실제 브라우저처럼 기존 자식을 버린다 — 파괴 여부를 테스트가 관찰할 수 있게.
    this.childNodes.forEach(c => { c.parentNode = null; });
    this.childNodes = [];
  }

  appendChild(child) { child.parentNode = this; this.childNodes.push(child); return child; }
  addEventListener() {}
  removeEventListener() {}
  focus() {}

  /** '.a.b' 또는 'tag' 형태만 해석한다. */
  matches(sel) {
    const s = String(sel).trim();
    if (!s.startsWith('.')) return this.localName === s.toLowerCase();
    return s.split('.').filter(Boolean).every(cls => this._classes.has(cls));
  }

  _descendants() {
    const out = [];
    const walk = (n) => { for (const c of n.childNodes) { out.push(c); walk(c); } };
    walk(this);
    return out; // 문서 순서
  }

  querySelectorAll(sel) {
    const s = String(sel).trim();
    if (s.startsWith(':scope >')) {
      const rest = s.slice(':scope >'.length).trim();
      return this.childNodes.filter(c => c.matches(rest));
    }
    return this._descendants().filter(n => n.matches(s));
  }

  querySelector(sel) { return this.querySelectorAll(sel)[0] || null; }
  closest() { return null; }
}

/**
 * `src/lib/utils.js` → `src/main.js` 순서로 한 realm에 로드한다.
 * (index.html의 로드 순서와 동일: utils가 fmtMd를 먼저 정의한다)
 */
function loadRenderer() {
  const chat = new El('div');
  chat.id = 'chat-messages';

  const byId = new Map([['chat-messages', chat]]);

  const document = {
    body: new El('body'),
    head: new El('head'),
    documentElement: new El('html'),
    createElement: (t) => new El(t),
    getElementById: (id) => byId.get(id) || null,
    querySelector: () => null,
    querySelectorAll: () => [],
    addEventListener: () => {},
    removeEventListener: () => {},
  };

  const sandbox = {
    console: { log: () => {}, warn: () => {}, error: () => {}, info: () => {}, debug: () => {} },
    setTimeout, clearTimeout, setInterval: () => 0, clearInterval, queueMicrotask,
    Promise, Set, Map, JSON, Math, Date, Number, String, Boolean, Array, Object, Error, RegExp,
    document,
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
  for (const file of ['src/lib/utils.js', 'src/main.js']) {
    vm.runInContext(fs.readFileSync(path.join(REPO_ROOT, file), 'utf-8'), context, { filename: file });
  }
  vm.runInContext('globalThis.__state = state;', context);
  const state = sandbox.__state;

  const callFastPath = () => {
    vm.runInContext('globalThis.__fp = _streamFastPath();', context);
    return sandbox.__fp;
  };
  const setRenderCacheCount = (n) => {
    vm.runInContext(`_renderCache.count = ${Number(n)}; _renderCache.wfKey = ''; _renderCache.toolKey = '';`, context);
  };

  return { sandbox, state, chat, context, callFastPath, setRenderCacheCount };
}

/**
 * 합의 메시지가 렌더된 뒤의 DOM 모양을 만든다(main.js의 isConsensus 렌더와 동형).
 *   .chat-msg.assistant > .msg-content > [헤더, .md-body, .msg-action-bar]
 */
function buildConsensusNode(chat, opts) {
  const o = opts || {};
  const node = new El('div');
  node.className = 'chat-msg assistant';

  const mc = new El('div');
  mc.className = 'msg-content';

  const header = new El('div');
  header.className = 'consensus-header';
  header.textContent = '합의 결과';
  mc.appendChild(header);

  let body = null;
  if (o.withBody !== false) {
    body = new El('div');
    body.className = 'md-body';
    body.innerHTML = o.initialBodyHtml || '';
    mc.appendChild(body);
  }

  const bar = new El('div');
  bar.className = 'msg-action-bar';
  mc.appendChild(bar);

  node.appendChild(mc);
  chat.appendChild(node);
  return { node, mc, header, body, bar };
}

// ─────────────────────────────────────────────────────────────────

describe('합의 스트리밍 fast-path (src/main.js _streamFastPath)', () => {
  let env;

  beforeEach(() => {
    env = loadRenderer();
    env.state.isStreaming = true;
    env.state._streamStartTime = Date.now();
    env.state._pinAnchorSet = false;
  });

  /** 합의 메시지 1건만 있는 상태를 만든다. */
  function seedConsensusMessage(content) {
    env.state.messages = [{
      role: 'assistant',
      isConsensus: true,
      consensusModelId: 'acme.zx-7:1',
      consensusModelName: 'ZX-7',
      content,
    }];
    env.setRenderCacheCount(1);
  }

  test('합의 스트리밍 중 fast-path가 성립한다 (전체 재렌더 회피 → 깜빡임 없음)', () => {
    seedConsensusMessage('부분 응답');
    buildConsensusNode(env.chat, { initialBodyHtml: '<p>이전</p>' });

    expect(env.callFastPath()).toBe(true);
  });

  test('본문(.md-body)이 새 내용으로 갱신된다', () => {
    seedConsensusMessage('갱신된 합의 본문');
    const built = buildConsensusNode(env.chat, { initialBodyHtml: '<p>이전</p>' });

    env.callFastPath();

    expect(built.body.innerHTML).toContain('갱신된 합의 본문');
    expect(built.body.innerHTML).not.toContain('이전');
    expect(built.body.getAttribute('data-stream-len')).toBe(String('갱신된 합의 본문'.length));
  });

  test('합의 결과 헤더와 액션바가 동일 객체로 살아남는다', () => {
    seedConsensusMessage('본문');
    const built = buildConsensusNode(env.chat);
    const headerRef = built.header;
    const barRef = built.bar;

    // in-place 경로를 실제로 지났음을 먼저 못박는다. 전체 재렌더로 떨어지면
    // 이 함수는 DOM을 건드리지 않으므로 아래 보존 검사가 무의미해진다.
    expect(env.callFastPath()).toBe(true);

    // 노드 파괴 여부 — 객체 동일성과 부모 관계를 함께 본다.
    expect(built.mc.childNodes).toContain(headerRef);
    expect(built.mc.childNodes).toContain(barRef);
    expect(headerRef.parentNode).toBe(built.mc);
    expect(barRef.parentNode).toBe(built.mc);
    expect(headerRef.textContent).toBe('합의 결과');
    // 컨테이너(.chat-msg) 자체도 유지된다 — 재생성되면 깜빡임이 보인다.
    expect(env.chat.childNodes).toContain(built.node);
  });

  test('같은 길이로 다시 호출하면 본문을 다시 쓰지 않는다', () => {
    seedConsensusMessage('고정 길이 본문');
    const built = buildConsensusNode(env.chat);

    expect(env.callFastPath()).toBe(true);
    const afterFirst = built.body.innerHTML;

    // 두 번째 호출은 data-stream-len이 같으므로 쓰기를 건너뛴다.
    let writes = 0;
    const raw = Object.getOwnPropertyDescriptor(El.prototype, 'innerHTML');
    Object.defineProperty(built.body, 'innerHTML', {
      configurable: true,
      get: () => raw.get.call(built.body),
      set: (v) => { writes += 1; raw.set.call(built.body, v); },
    });

    expect(env.callFastPath()).toBe(true);
    expect(writes).toBe(0);
    expect(built.body.innerHTML).toBe(afterFirst);
  });

  test('.md-body가 없으면 전체 재렌더에 맡긴다 (thinking → 본문 전환)', () => {
    seedConsensusMessage('첫 청크');
    buildConsensusNode(env.chat, { withBody: false });

    expect(env.callFastPath()).toBe(false);
  });

  test('오류 문구가 섞이면 전체 재렌더로 떨어진다 (기존 정책 유지)', () => {
    seedConsensusMessage('부분 결과\n[합의 오류: timeout]');
    buildConsensusNode(env.chat);

    expect(env.callFastPath()).toBe(false);
  });

  test('스트리밍이 아니면 fast-path를 쓰지 않는다 (최종 렌더는 전체 경로)', () => {
    seedConsensusMessage('완료된 합의');
    buildConsensusNode(env.chat);
    env.state.isStreaming = false;

    expect(env.callFastPath()).toBe(false);
  });
});
