# 유지보수용 리팩토링 계획 (Maintenance Refactor)

## 📋 목표
- **God File 분해** (main.js 3,883줄 → 모듈화)
- **테스트 기반 구조** (0줄 → 최소한의 핵심 테스트)
- **에러 처리 강화** (현재: 상당수 try-catch 부재)
- **문서화** (함수별 JSDoc + Python docstring)
- **CI/CD 준비** (GitHub Actions 스켈레톤)

---

## 🔧 Phase 1: JavaScript 리팩토링 (main.js 분해)

### 현재 상태
```
electron/
├── main.js (3,883줄) ← 분해 필요
├── preload.js
└── index.html
```

### 목표 구조
```
electron/
├── main.js (진입점, 300줄 이하)
├── src/
│   ├── window-manager.js (윈도우 생명주기)
│   ├── ipc-handlers.js (IPC 이벤트)
│   ├── renderer-api.js (렌더러 노출 API)
│   ├── error-handler.js (글로벌 에러)
│   └── logger.js (로깅)
├── renderer/
│   ├── components/
│   │   ├── editor.js (500줄 → 3개 파일)
│   │   ├── chat.js (700줄 → 2개 파일)
│   │   └── workflow.js (새로 추출)
│   ├── services/
│   │   ├── api-client.js
│   │   ├── rag-client.js
│   │   └── bedrock-client.js
│   └── utils/
│       ├── ui-helpers.js
│       ├── format-helpers.js
│       └── constants.js
└── preload.js
```

### 우선순위
1. **Window Manager 분리** — 윈도우 생성/이벤트 → `window-manager.js`
2. **IPC Handlers 분리** — 채널별로 → `ipc-handlers/` (서브폴더)
3. **Renderer 컴포넌트 분리** — 각 섹션별 모듈화
4. **API Client 통합** — 중복 fetch 로직 제거

---

## 🧪 Phase 2: 테스트 기반 구조

### 현재 상태
```
tests/
├── e2e/ (0줄)
├── unit/ (2줄)
└── fixtures/ (거의 비어있음)
```

### 목표
```
tests/
├── unit/
│   ├── test_bedrock_gateway.py (new)
│   ├── test_editor_api.py (new)
│   ├── test_rag_service.py (new)
│   └── conftest.py (pytest fixture)
├── e2e/
│   ├── test_editor_startup.py (new)
│   └── test_chat_workflow.py (new)
├── fixtures/
│   ├── bedrock_responses.json
│   ├── sample_codebase.json
│   └── mock_tools.py
└── conftest.py (pytest config)
```

### 핵심 테스트 3개 (최소)
1. **Bedrock Gateway** — assume_role, 토큰 갱신
2. **RAG Service** — 벡터 + BM25 통합
3. **Tool Execution** — read_file, run_command 통합

---

## 📝 Phase 3: 문서화

### 대상 (우선순위)
1. `gateway.py` — BedrockUser, assume_role 로직
2. `rag_service.py` — 하이브리드 검색
3. `editor/main.js` — 각 섹션별 20줄 주석
4. 각 함수 — JSDoc/docstring (197개 함수 중 상위 50개)

---

## 🚀 Phase 4: 배포 준비

### CI/CD (GitHub Actions)
```yaml
- Lint: ESLint + Black
- Test: Jest + pytest
- Build: npm run build
- Package: electron-builder
```

### 버전 관리
```
v0.1.0-beta    (현재)
v0.2.0-beta    (리팩토링 후)
v1.0.0-alpha   (테스트 완료 후)
```

---

## ⏱️ 추정 소요 시간

| Phase | 작업 | 시간 |
|-------|------|------|
| 1 | main.js 분해 | 4h |
| 2 | 테스트 작성 | 3h |
| 3 | 문서화 | 2h |
| 4 | CI/CD 설정 | 1h |
| **합계** | | **10h** |

---

## ✅ 체크리스트

### Phase 1 (JavaScript)
- [ ] window-manager.js 추출
- [ ] ipc-handlers 폴더 생성 + 채널별 분리
- [ ] renderer/components 모듈화
- [ ] renderer/services API 통합
- [ ] main.js 라인 수 200 이하로 축소

### Phase 2 (테스트)
- [ ] pytest 설정 (conftest.py)
- [ ] Bedrock Gateway 단위 테스트
- [ ] RAG 통합 테스트
- [ ] 커버리지 50% 이상

### Phase 3 (문서화)
- [ ] ARCHITECTURE.md 작성
- [ ] API.md (IPC 채널 명세)
- [ ] DEV_SETUP.md (개발 환경)

### Phase 4 (CI/CD)
- [ ] .github/workflows/test.yml
- [ ] .github/workflows/build.yml
- [ ] RELEASE.md

---

## 📌 진행 상황
- [ ] Phase 1 시작
- [ ] Phase 1 완료
- [ ] Phase 2 시작
- [ ] Phase 2 완료
- [ ] Phase 3 시작
- [ ] Phase 3 완료
- [ ] Phase 4 시작
- [ ] Phase 4 완료

**시작: 2025-05-01**
