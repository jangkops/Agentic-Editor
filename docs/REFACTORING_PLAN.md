# 리팩토링 로드맵

> **목표**: `src/main.js` (3,883줄 God-file) 를 유지보수 가능한 모듈로 점진 분리.
> **원칙**: 기능은 유지한다. 깨뜨리면 즉시 롤백한다. Phase 마다 반드시 `node --check`와 앱 구동 확인.

---

## Phase 0 — 현 상태 (2025-04-30, 완료)

- [x] 임시/백업 파일 제거 (`*.bak2`, `*.tmp`, `*.old`)
- [x] 미사용 `src/components/` 9개 파일 제거 (1,150줄)
- [x] `docs/ARCHITECTURE.md` 작성
- [x] `docs/REFACTORING_PLAN.md` (이 문서) 작성
- [x] beta 브랜치 푸시

---

## Phase 1 — 순수 유틸 추출 (안전도 최상)

**대상**: 다른 모듈에 의존하지 않는 pure function만.

- [ ] `src/lib/utils.js` 생성
  - `esc(t)` — HTML escape
  - `fmtNum(n)` — 1.2K / 3.4M
  - `fmtElapsed(secs)` — 경과 시간
  - `fmtElapsedMs(ms)`
  - `fmtMd(t)` — 마크다운 → HTML
  - `addCopySupport(el, text)`
- [ ] `index.html` `<script src="lib/utils.js">` 를 `main.js` 앞에 추가
- [ ] `src/main.js`에서 해당 함수 제거하거나 주석으로 "moved to lib/utils.js" 남기기
- [ ] `node --check src/main.js` 문법 검증
- [ ] 앱 구동 확인 — 채팅/복사/스트리밍이 정상
- [ ] 커밋: `refactor(phase1): extract pure utils to src/lib/utils.js`

**예상 효과**: main.js -100줄, 유틸은 다른 곳에서도 재사용 가능.

---

## Phase 2 — SSE 스트리밍 모듈화

- [ ] `src/lib/sse.js` 생성
  - `_readWithIdleTimeout(reader, idleMs)`
  - `readSSEStream(resp, callbacks)`
- [ ] 4곳에서 중복된 SSE 파싱 루프 (`while (true) { reader.read()... }`)를 `readSSEStream`로 교체
  - `runSimpleChat`, `runAgentWorkflow`, `runParallel`, `runConsensus`
- [ ] 앱 구동 확인
- [ ] 커밋: `refactor(phase2): unify SSE parsing via lib/sse.js`

**예상 효과**: main.js -200줄, SSE 버그는 한 곳에서만 고치면 됨.

---

## Phase 3 — Renderer 분리

- [ ] `src/renderers/tool-summary.js` 생성
  - `toolDisplayName`, `formatToolArg`, `renderToolSummary`, `renderToolUseCard`, `renderWorkflow`
- [ ] `src/renderers/messages.js` 생성
  - `renderMessages`, `_streamFastPath`, `_inPlaceUpdateSideCards`, `_renderCache`
- [ ] 앱 구동 확인
- [ ] 커밋: `refactor(phase3): split message/tool renderers`

**예상 효과**: main.js -800줄. 채팅 렌더링 로직이 독립 모듈로 분리됨.

---

## Phase 4 — Feature 모듈 분리

차례로 아래 순서로 진행 (각 단계마다 커밋 + 구동 확인):

- [ ] `src/features/sso.js` — SSO 다이얼로그, 자격증명 (~200줄)
- [ ] `src/features/skills.js` — 스킬 관리, GitHub MD import (~150줄)
- [ ] `src/features/models.js` — 모델 드롭다운, 합의 우선순위 (~300줄)
- [ ] `src/features/file-tree.js` — 파일 탐색기, inline 생성/삭제 (~300줄)
- [ ] `src/features/editor.js` — Monaco, 탭 (~200줄)
- [ ] `src/features/terminal.js` — 터미널 (~250줄)
- [ ] `src/features/settings.js` — 설정/About/Usage (~400줄)
- [ ] `src/features/source-control.js` — Git 패널 (~200줄)

**예상 효과**: main.js 3,883줄 → 약 400줄 (init 오케스트레이션만)

---

## Phase 5 — 테스트 복구 & CI

현재 `tests/e2e/test_editor.py` 와 `test_startup.py` 는 **0바이트**.

- [ ] Playwright 또는 Electron spectron 기반 스모크 테스트
  - 앱 시작 → 로그인 다이얼로그 감지
  - 백엔드 `/health` 응답 확인
- [ ] `tests/unit/test_gateway_client.py` 확장 (현재 2줄)
- [ ] GitHub Actions workflow 추가 (`.github/workflows/ci.yml`)
  - lint (eslint)
  - node --check
  - pytest

---

## Phase 6 — ES 모듈 전환 (선택)

여기까지 오면 `<script>` 여러 개 방식이 어색해짐. 한 번에 ESM으로 전환:

- [ ] `src/index.html` → `<script type="module" src="main.js">` 한 줄
- [ ] `src/**/*.js` → `import/export` 구문으로
- [ ] 빌드 도구(esbuild/vite) 도입 검토

---

## 진행 시 원칙

1. **한 번에 하나만 바꾼다.** 합쳐 커밋 금지.
2. **기존 동작 유지가 최우선.** 이름 바꾸기조차 신중.
3. **각 Phase 끝에 beta push.** 문제가 나도 롤백 지점이 촘촘함.
4. **"그때 그때 청소"** — 추출 후 원본 파일에 남은 dead 코드/임시 주석은 즉시 제거.
