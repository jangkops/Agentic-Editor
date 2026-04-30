# Agentic Editor — Architecture

> 유지보수를 위한 **한 장짜리 지도**. 처음 들어와도 이 문서만 읽으면 어디를 고칠지 찾을 수 있도록 작성.

---

## 1. 전체 구조

```
agentic-editor/
├── electron/              # Electron 메인 프로세스 (노드 권한)
│   ├── main.js            # BrowserWindow, IPC 핸들러, 파일/터미널 접근
│   ├── preload.js         # contextBridge → window.electronAPI 노출
│   └── core/              # SSO 로그인, 자격증명 로드 헬퍼
├── src/                   # Electron 렌더러 (브라우저 컨텍스트)
│   ├── index.html         # 단일 페이지 레이아웃 (좌·중·우 3분할)
│   ├── main.js            # ⚠️ 거대 God-file (~3,900줄, 섹션 29개)
│   ├── center-views.js    # 중앙 탭 뷰 (구조/의존성/통계/검색/Git/리뷰)
│   ├── lib/               # 순수 유틸 모듈 (Phase 1 추출 대상)
│   ├── styles/            # CSS (variables / layout / components)
│   └── vendor/            # monaco-editor 등
├── ai_engine/             # Python 백엔드 (FastAPI + uvicorn, 8765)
│   ├── server.py          # SSE 스트리밍, /api/agents/* 엔드포인트
│   ├── gateway_module.py  # AWS Bedrock Gateway 호출 (SigV4 + BedrockUser)
│   ├── agent_system/      # tool_registry (5개 도구)
│   ├── agents/            # planner, coordinator (뼈대만)
│   └── rag/               # 하이브리드 검색 (벡터 + BM25)
└── docs/                  # 이 문서
```

## 2. 데이터 흐름

```
 [사용자 입력]
      ↓
 renderer/main.js::sendMessage()
      ↓  (fetch SSE)
 ai_engine/server.py::run_agent_stream
      ↓
 ai_engine/gateway_module.py::call_gateway (AWS Bedrock Gateway)
      ↓  (SigV4 + BedrockUser assume-role 헤더)
 [Amazon Bedrock] → 모델 응답
      ↓  (stream events: {text} / {tool,status} / {error})
 renderer/main.js::readSSEStream
      ↓
 renderer/main.js::renderMessages + renderToolSummary
      ↓
 [DOM 업데이트]
```

## 3. `src/main.js` 섹션 지도

현재 `src/main.js`는 3,883줄의 단일 파일이며, **`// ===== 섹션명 =====` 주석으로 29개 구역이 나뉘어 있음**. 위에서 아래로:

| # | 섹션 | 대략 줄 | 핵심 함수 |
|---|---|---|---|
| 1  | App Init / SSO dialog      | 1–370    | `initApp`, `showSSODialog` |
| 2  | GitHub Import              | 370–427  | `initGithubImport` |
| 3  | Skills                     | 427–535  | `renderSkillsList`, `showSkillEditor` |
| 4  | Model Dropdown             | 535–642  | `initModelDropdown`, `loadModelsFromServer` |
| 5  | Mode Toggle (단일/병렬)    | 642–714  | `initModeToggle`, `addParallelSlot` |
| 6  | Chat Tabs                  | 714–744  | `renderChatTabs` |
| 7  | Chat + File Attach         | 744–871  | `initChat`, `sendMessage` |
| 8  | Single Mode / Agent Flow   | 871–1174 | `runSingle`, `runAgentWorkflow`, `readSSEStream` |
| 9  | Parallel Mode              | 1174–1417| `runParallel` |
| 10 | Consensus                  | 1417–1703| `runConsensus`, `renderConsensusView` |
| 11 | Render Messages            | 1703–2436| `renderMessages`, `renderToolSummary`, `renderWorkflow` |
| 12 | File Explorer              | 2436–2756| `initFileExplorer`, `loadFileTree` |
| 13 | Monaco                     | 2756–2862| `initMonaco`, `openFileInEditor` |
| 14 | Terminal                   | 2862–3077| `initTerminal`, `addTerminal` |
| 15 | Topbar                     | 3077–3114| `initTopbar` |
| 16 | Settings Dialog            | 3114–3341| `showSettingsDialog` |
| 17 | About / Usage popup        | 3341–3399| `showAboutDialog` |
| 18 | Source Control             | 3399–3599| `renderSourceControlPanel` |
| 19 | Usage tracking             | 3599–3665| `trackUsage`, `updateQuotaBar` |
| 20 | Panel Resize               | 3666–3722| `initPanelResize` |
| 21 | File Save shortcut         | 3722–3738| `saveCurrentFile` |
| 22 | Commit log mini            | 3738–3763| `loadCommitLogMini` |
| 23 | Live panel                 | 3763–3819| `addLiveLog`, `updateLivePanel` |
| 24 | Parallel/Consensus save    | 3819–3866| `saveParallelResults` |
| 25 | RAG indexing               | 3866–끝  | `indexProjectForRAG` |

> 원하는 기능을 찾을 때는 VSCode에서 `// =====` 로 검색하면 점프 가능.

## 4. 백엔드 엔드포인트

| 메서드 | 경로 | 용도 |
|---|---|---|
| POST | `/api/agents/run-stream` | 단일 모델 SSE 스트리밍 |
| POST | `/api/agents/run-agent`  | 도구 사용 에이전트 스트리밍 |
| POST | `/api/agents/run-parallel` | 여러 모델 병렬 스트리밍 (단일 연결) |
| POST | `/api/models`            | 모델 카탈로그 (자격증명 body) |
| POST | `/api/reset-cache`       | 자격증명 캐시 리셋 |
| GET  | `/health`                | 헬스 체크 |
| GET  | `/api/quota`             | 월간 쿼터 |

## 5. 로컬 실행

```bash
npm run dev            # Electron + uvicorn 동시 실행
npm run dev:renderer   # 렌더러만
npm run dev:backend    # 백엔드만 (uvicorn --reload)
```

포트:
- **8765** — uvicorn (백엔드)
- Electron은 file:// 프로토콜로 `src/index.html` 로드
