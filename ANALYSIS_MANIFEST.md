# Agentic Editor (Mogam Works) — 분석용 파일 매니페스트

> 목적: 외부 AI가 이 리포를 심층 분석할 때 읽어야 할 파일 경로 전체 목록.
> 기준 시점: v0.5.4 / 브랜치 `gamma`. 모든 경로는 리포 루트(`/Users/jcg/agentic-editor`) 기준 상대경로.
> 라인 수는 `wc -l` 실측값.

---

## 0. 서비스 구조 요약

```
Electron 데스크톱 앱 (Mogam Works)
├── 메인 프로세스   electron/          15,299 lines / 41 files (JS)
├── 렌더러          src/               18,726 lines / 34 files (JS/CSS/HTML)
└── Python 사이드카 ai_engine/         57,588 lines / 88 files (Python, FastAPI)
                                       └─ PyInstaller로 동결 → ai_engine_dist/

앱 코드 합계: 163 files / 약 91,613 lines
스펙:        .kiro/specs/  21 디렉터리 + 2 파일 (791 태스크)
테스트:      tests/ 33 files (러너 연결) + scripts/ 253 test_*.py (수동 자산)
```

### 기동 경로 (실제 실행 흐름)

| 단계 | 파일 | 내용 |
|---|---|---|
| 1. npm script | `package.json` | `dev` = `concurrently dev:python + dev:electron` |
| 2. venv 준비 | `scripts/setup-venv.js` | `predev`에서 실행, 8765 포트 정리 |
| 3. Python 백엔드 | `ai_engine/server.py` | `uvicorn ai_engine.server:app --port 8765` |
| 4. Electron 대기 | `scripts/build-python.js` / `wait-on :8765/health` | |
| 5. Electron 진입 | `electron/main.js` | `main` 필드. BrowserWindow + IPC 등록 |
| 6. 렌더러 진입 | `src/index.html` → `src/main.js` | |
| 7. 동결 배포 시 | `ai-engine-server.spec` | PyInstaller. `process.resourcesPath/ai_engine_dist/ai-engine-server` |

### FastAPI 엔드포인트 27개 (전부 `ai_engine/server.py`)

에이전트 실행 경로가 5가지로 분화돼 있음 — 분석 시 이 분기를 먼저 파악할 것.

```
/api/agents/graph-stream      (10051)  ← LangGraph 주경로. SSE. 여기가 핵심
/api/agents/run-stream        (9614)   ← 레거시 스트리밍
/api/agents/run-agent         (10286)
/api/agents/run-parallel      (10924)
/api/agents/run-orchestrated  (14697)
/api/agents/run               (15272)
/api/agents/classify-intent   (9499)
/api/conversation/handoff     (15290)
/health  /api/quota  /api/models  /api/answer-quality  /api/rag/index  /api/rag/status
/api/media/pptx-render  /api/templates(+4)  /api/attachments/extract-zip  /api/reset-cache
/api/debug/{cwd,image-gen-status,bridge,openai-test}
```

---

## 1. 읽는 순서 (권장)

심층 분석 시 이 순서를 따르면 의존 관계가 자연스럽게 풀린다.

**1단계 — 규약과 제약 (먼저 읽어야 코드의 "왜"가 보인다)**
```
AGENTS.md
.kiro/steering/project.md          # 스택 제약 (No React/TS/Rust/SQLite)
.kiro/steering/gateway.md          # Bedrock Gateway 계약 — 타임아웃/재시도 상수 전부
.kiro/steering/security.md         # 자격증명 미저장 원칙, contextIsolation
.kiro/steering/ui.md               # 디자인 토큰
.kiro/specs/langgraph-hierarchical-orchestrator/API_NOTES.md   # LangGraph 실측 함정 기록
```

**2단계 — 멀티에이전트 오케스트레이션 (프로젝트의 기술적 중심)**
```
ai_engine/agent_system/graph_state.py       231   shared blackboard + reducer 4종
ai_engine/agent_system/supervisor.py       1277   메타 노드 + top 그래프 3종
ai_engine/agent_system/subgraphs/_common.py 245   워커 서브그래프 조립 프리미티브
ai_engine/agent_system/dag.py               163   위상정렬 Wave 스케줄링 (순수 함수)
ai_engine/agent_system/deps.py               83   모델 배분 + Opus 실패 근거 주석
ai_engine/agent_system/checkpoint_store.py  442   JSON 체크포인터 (SQLite 금지 대응)
ai_engine/agent_system/sse_bridge.py        246   astream_events(v2) → SSE
ai_engine/agent_system/nodes/tool_node.py   404   도구 실행 3-way 디스패치
ai_engine/agent_system/nodes/verify.py      380   citation 검증 + grounding gate 적용
ai_engine/agent_system/grounding_gate.py    110   근거 게이트 판정 (기본 off)
ai_engine/agent_system/chat_model_adapter.py 690  GatewayChatModel (LangChain 어댑터)
ai_engine/agent_system/depth_router.py      521   Fast_Path / 복잡도 분류
```

**3단계 — 게이트웨이 (모든 LLM 호출의 단일 통로)**
```
ai_engine/gateway_module.py                1633   실패 모드 6종 × 독립 재시도 정책
ai_engine/openai_catalog.py                 726   OpenAI Responses 라우트 카탈로그
ai_engine/openai_adapter.py                 298
ai_engine/core/bedrock_gateway_client.py    132
```

**4단계 — 나머지 도메인 (병렬로 읽어도 됨)**
- PPTX/문서 렌더링: `slide_templates.py`, `native_diagram_pptx.py`, `native_layout_renderer.py`
- RAG: `ai_engine/rag/` 19 모듈
- Deep research: `ai_engine/research/` 12 모듈
- Capability discovery: `ai_engine/capability/` 11 모듈
- Remote SSH: `electron/src/remote/` 24 모듈

---

## 2. ai_engine/ — Python 백엔드 (88 files, 57,588 lines)

### 2.1 최상위

| 경로 | lines | 역할 |
|---|---:|---|
| `ai_engine/server.py` | **15814** | FastAPI 모놀리스. 엔드포인트 27개 + `_execute_tool` 통합 도구 디스패처. **전체 Python 코드의 27%** — 최대 구조 부채 |
| `ai_engine/gateway_module.py` | 1633 | Bedrock Gateway 클라이언트. SigV4, converse/invoke/SSE, 6개 실패 모드 재시도 |
| `ai_engine/slide_templates.py` | 2901 | 슬라이드 템플릿 + 밀도 |
| `ai_engine/native_diagram_pptx.py` | 1804 | python-pptx 네이티브 다이어그램 |
| `ai_engine/native_layout_renderer.py` | 1745 | 네이티브 레이아웃 렌더 |
| `ai_engine/template_manager.py` | 1194 | 템플릿 등록/조회 |
| `ai_engine/openai_catalog.py` | 726 | OpenAI 모델 카탈로그 |
| `ai_engine/vertex_image_module.py` | 582 | Vertex AI 이미지 생성 (스티어링 예외 승인 경로) |
| `ai_engine/layout_geometry.py` | 435 | 좌표/경계 계산 (순수 함수, PBT 대상) |
| `ai_engine/openai_adapter.py` | 298 | |
| `ai_engine/style_profile.py` | 263 | |
| `ai_engine/bridge_client.py` | 194 | 원격 브리지 클라이언트 |
| `ai_engine/icon_assets.py` | 160 | |
| `ai_engine/run_server.py` | 47 | |
| `ai_engine/gw_call_v2.py` | 38 | |
| `ai_engine/main.py` | 18 | |
| `ai_engine/__init__.py` | 1 | |

### 2.2 agent_system/ — LangGraph 오케스트레이터 (21 files)

```
ai_engine/agent_system/supervisor.py                1277
ai_engine/agent_system/chat_model_adapter.py         690
ai_engine/agent_system/depth_router.py               521
ai_engine/agent_system/checkpoint_store.py           442
ai_engine/agent_system/nodes/tool_node.py            404
ai_engine/agent_system/nodes/verify.py               380
ai_engine/agent_system/sse_bridge.py                 246
ai_engine/agent_system/graph_state.py                231
ai_engine/agent_system/mcp_tools.py                  182
ai_engine/agent_system/nodes/retrieve.py             163
ai_engine/agent_system/dag.py                        163
ai_engine/agent_system/grounding_gate.py             110
ai_engine/agent_system/store.py                      105
ai_engine/agent_system/deps.py                        83
ai_engine/agent_system/tool_registry.py               80   (레거시 — 그래프 경로는 server._execute_tool 사용)
ai_engine/agent_system/subgraphs/research.py         629
ai_engine/agent_system/subgraphs/media.py            293
ai_engine/agent_system/subgraphs/_common.py          245
ai_engine/agent_system/subgraphs/coding.py           136
ai_engine/agent_system/subgraphs/ops.py               68
ai_engine/agent_system/subgraphs/chat.py              42
ai_engine/agent_system/subgraphs/__init__.py          31
ai_engine/agent_system/__init__.py                     1
ai_engine/agent_system/nodes/__init__.py               0
```

### 2.3 rag/ — 검색·검증 (19 files)

```
ai_engine/rag/embedder.py                516   fastembed → LSA → 어휘 3단 폴백
ai_engine/rag/context_builder.py         367
ai_engine/rag/hybrid_search.py           287   RRF 융합
ai_engine/rag/indexer.py                 258
ai_engine/rag/conversation_memory.py     221
ai_engine/rag/answer_quality.py          218
ai_engine/rag/verifier.py                177   LLM faithfulness + 로컬 임베딩 grounding
ai_engine/rag/retrieval_pipeline.py      170
ai_engine/rag/cross_verify.py            143
ai_engine/rag/quality_store.py           103
ai_engine/rag/trace.py                   100   EvidenceTrace 계약 (기록 호출부는 미확인)
ai_engine/rag/eval_metrics.py            100
ai_engine/rag/citation.py                 98   file:line 단위 인용 검증
ai_engine/rag/reranker.py                 92
ai_engine/rag/query_expand.py             92
ai_engine/rag/gw_text.py                  76
ai_engine/rag/consensus_select.py         54
ai_engine/rag/__init__.py                  1
```

### 2.4 research/ — Deep research (12 files)

```
ai_engine/research/deep_research.py     1961
ai_engine/research/backend.py            948
ai_engine/research/providers.py          862
ai_engine/research/rank.py               736
ai_engine/research/eval_harness.py       552
ai_engine/research/normalize.py          286
ai_engine/research/cache.py              274
ai_engine/research/dedup.py              258   멱등성 PBT 대상
ai_engine/research/__init__.py           162
ai_engine/research/config.py             158
ai_engine/research/models.py             133
ai_engine/research/security.py           114   SSRF 필터
```

### 2.5 capability/ — 모델 능력 탐지 (11 files, ~13k lines)

> `gateway-models-effort-support` 스펙의 구현체. **미완 15태스크가 이 영역** — capability map이 실제 Gateway로 검증되지 않은 상태.

```
ai_engine/capability/evidence_collector.py   4973
ai_engine/capability/capability_map.py       1537
ai_engine/capability/failure_handler.py      1377
ai_engine/capability/request_builder.py      1130
ai_engine/capability/contracts.py            1030
ai_engine/capability/effort_settings.py       807
ai_engine/capability/activation_gate.py       718
ai_engine/capability/canonicalizer.py         715
ai_engine/capability/store.py                 707
ai_engine/capability/baseline_inspector.py    418
ai_engine/capability/__init__.py               42
```

### 2.6 기타 (레거시/보조)

```
ai_engine/core/bedrock_gateway_client.py   132
ai_engine/core/harness_controller.py        23
ai_engine/core/__init__.py                   1
ai_engine/agents/coordinator.py             51   (agent_system 이전의 구버전)
ai_engine/agents/planner_agent.py           34
ai_engine/agents/__init__.py                 1
```

---

## 3. electron/ — 메인 프로세스 (41 files, 15,299 lines)

### 3.1 진입점 / 코어

```
electron/main.js                    482   BrowserWindow, IPC 등록 진입
electron/preload.js                 175   contextBridge 화이트리스트 (보안 경계)
electron/src/window-manager.js      123
electron/core/aws-sso-manager.js    773   AWS SSO 로그인 + 자격증명 export
electron/core/data-store.js         231   userData 하위 JSON 영속 (settings/history/usage)
electron/core/process-manager.js    170   Python 사이드카 생명주기
electron/core/pty-worker.js          50
```

### 3.2 IPC 핸들러 (11 모듈, `ipcMain.handle` 총 83개)

```
electron/src/ipc-project-handlers.js     536
electron/src/ipc-git-handlers.js         475
electron/src/ipc-remote-handlers.js      458
electron/src/ipc-fs-handlers.js          444
electron/src/ipc-capability-handlers.js  358
electron/src/ipc-slides-handler.js       260
electron/src/ipc-terminal-handlers.js    224
electron/src/ipc-store-handlers.js       204
electron/src/ipc-sso-handlers.js         130
electron/src/ipc-template-handlers.js    106
```

### 3.3 remote/ — SSH 원격 개발 (24 모듈)

> 단일 스펙 최대 규모(124 태스크). 분산 시스템 문제를 모듈 단위로 분리한 설계.

```
electron/src/remote/remote-file-bridge.js       1235   atomic write + rollback
electron/src/remote/provisioner.js               906   ai_engine 업로드 트리 등가성 + hash skip
electron/src/remote/ssh-config-parser.js         860   Include 재귀 파싱
electron/src/remote/remote-session.js            731
electron/src/remote/remote-terminal-bridge.js    673
electron/src/remote/session-router.js            525
electron/src/remote/port-forwarder.js            514
electron/src/remote/logger.js                    509   자격증명 마스킹
electron/src/remote/host-key-store.js            501   TOFU
electron/src/remote/ssh-client-builder.js        477   ProxyJump 체인
electron/src/remote/remote-session-manager.js    451
electron/src/remote/path-normalization.js        443   Windows 경로
electron/src/remote/error-surface.js             441
electron/src/remote/remote-hosts-store.js        291
electron/src/remote/reconnect-loop.js            229
electron/src/remote/bridge-server.js             225
electron/src/remote/port-allocator.js            222
electron/src/remote/credential-cache.js          191
electron/src/remote/keepalive-policy.js          168
electron/src/remote/_rfb_temp.js                 154   (임시 파일 — 정리 대상)
electron/src/remote/ssh-binary-tunnel.js         139
electron/src/remote/auth-policy.js               110
electron/src/remote/backoff.js                    79   지수 백오프 + cap
electron/src/remote/request-queue.js              26   idempotency by requestid
```

---

## 4. src/ — 렌더러 (34 files, 18,726 lines)

```
src/index.html                            302   CSP 정의 위치
src/main.js                              8118   렌더러 모놀리스 — 두 번째 최대 구조 부채
src/center-views.js                      1509
src/model-recommender.js                  813
src/effort-control.js                     594
src/model-dropdown-ui.js                  123
```

### 4.1 components/ — Web Components (16 files)

```
src/components/template-panel.js         1114
src/components/file-preview-panel.js     1108
src/components/research-panel.js          457
src/components/remote-host-picker.js      444
src/components/research-settings.js       414
src/components/search-indicator.js        306
src/components/remote-auth-dialog.js      268
src/components/remote-status-bar.js       229
src/components/remote-host-key-dialog.js  190
src/components/remote-workspace-picker.js 185
src/components/denylist-manager.js        106
src/components/pptx-viewer.js              49
src/components/remote-ad-hoc-dialog.js     34
src/components/xlsx-viewer.js              32
src/components/docx-viewer.js              24
src/components/image-viewer.js             22
src/components/pdf-viewer.js               18
```

### 4.2 lib/ — 순수 유틸 (PBT 대상)

```
src/lib/utils.js               123
src/lib/model-selection.js      81
src/lib/file-list-sort.js       73
src/lib/image-thumbnails.js     44
src/lib/file-size.js            44
```

### 4.3 styles/ + vendor/

```
src/styles/components.css     1077
src/styles/layout.css          547
src/styles/variables.css        67   디자인 토큰 정본
src/vendor/xterm.css           209
src/vendor/xterm.js              1   (CDN 스텁)
src/vendor/xterm-addon-fit.js    1
```

---

## 5. .kiro/ — 스펙 주도 개발 산출물 (88 files)

### 5.1 steering — 항상 적용되는 규약

```
.kiro/steering/project.md      스택 제약 + 멀티에이전트 필수 규칙
.kiro/steering/gateway.md      Bedrock Gateway 계약 (타임아웃/재시도 상수 정본)
.kiro/steering/security.md     자격증명·IPC·CSP 원칙
.kiro/steering/ui.md           3-pane 레이아웃 + 디자인 토큰
```

### 5.2 specs — 21 디렉터리 + 2 파일 (총 791 태스크, 738 완료)

각 디렉터리는 `requirements.md`(또는 `bugfix.md`) + `design.md` + `tasks.md` 조합.
`.config.kiro`는 스펙 메타데이터.

**멀티에이전트/추론 (202 태스크)**
```
.kiro/specs/langgraph-hierarchical-orchestrator/{requirements,design,tasks}.md + API_NOTES.md
.kiro/specs/langgraph-reasoning-upgrade/{requirements,design,tasks}.md
.kiro/specs/reasoning-perf-reliability/{requirements,design,tasks}.md
.kiro/specs/deep-research-engine/{requirements,design,tasks}.md + tasks.meta.json
```
> `API_NOTES.md`는 LangGraph 공식 API의 실측 함정 기록(async 4종 필수, wait_for 금지 등).
> 코드 주석이 이 문서를 계속 참조하므로 **반드시 함께 읽을 것**.

**PPTX/문서 렌더링 (237 태스크 — 최대 투자 영역)**
```
.kiro/specs/pptx-template-styling/{requirements,design,tasks}.md
.kiro/specs/pptx-native-density-render/{requirements,design,tasks}.md
.kiro/specs/pptx-ultra-quality-hybrid-render/{requirements,design,tasks}.md
.kiro/specs/pptx-design-density-parity/{requirements,design,tasks}.md
.kiro/specs/pptx-overlay-collision-fix/{bugfix,design,tasks}.md
.kiro/specs/pptx-fullbleed-native-overlay-collision/{bugfix,design,tasks}.md
.kiro/specs/pptx-image-slot-placement-fix/{bugfix,design,tasks}.md
.kiro/specs/pptx-quality-vertex-images/{bugfix,design,tasks}.md
```
> `bugfix.md` 5종은 실제 산출물 pptx를 **도형 단위로 감사**한 결과에서 시작해
> `isBugCondition` / fix property / preservation property를 의사코드로 형식화한다.
> 이 리포의 방법론을 이해하는 가장 좋은 입구.

**게이트웨이/모델 (92+ 태스크)**
```
.kiro/specs/gateway-openai-models/{requirements,design,tasks}.md
.kiro/specs/gateway-models-effort-support/{requirements,design,tasks}.md + tasks.meta.json   ← 15태스크 미완
.kiro/specs/gateway-invoke-route/design.md        (design만)
.kiro/specs/model-recommend/requirements.md       (requirements만)
```

**나머지**
```
.kiro/specs/remote-ssh/{requirements,design,tasks}.md              124 태스크 (미완 33 = 전부 optional)
.kiro/specs/media-generation-editing/{requirements,design,tasks}.md
.kiro/specs/media-output-quality/{bugfix,design,tasks}.md
.kiro/specs/rag-answer-quality/{requirements,design,tasks}.md
.kiro/specs/app-deployment-readiness/{requirements,design,tasks}.md + verification.md
.kiro/specs/phase1.md
.kiro/specs/phase2.md
```

---

## 6. tests/ — 러너에 연결된 테스트 (33 files)

`npm test` = `jest tests/unit/ --coverage` / `npm run test:e2e` = `pytest tests/e2e/`

### 6.1 unit — jest (JS 21) + pytest (Python 11)

```
tests/unit/aws-sso-manager.pbt.test.js              fast-check PBT
tests/unit/aws-sso-helpers.property.test.js
tests/unit/aws-sso-login.property.test.js
tests/unit/aws-sso-login.fallback.test.js
tests/unit/aws-sso-ipc-contract.test.js
tests/unit/default-sso-preset.property.test.js
tests/unit/secret-free.property.test.js             자격증명 미노출 PBT
tests/unit/capability-ipc-handlers.test.js
tests/unit/effort-control-wiring.test.js
tests/unit/ipc-handlers.test.js
tests/unit/window-manager.test.js
tests/unit/onboarding.test.js
tests/unit/utils.test.js
tests/unit/file-list-sort.test.js
tests/unit/file-size.test.js
tests/unit/image-thumbnails.test.js
tests/unit/remote/auth-policy.property.test.js
tests/unit/remote/credential-security.property.test.js
tests/unit/remote/log-masking.property.test.js
tests/unit/remote/state-machine.property.test.js
tests/unit/remote/ssh-config-parser.test.js
tests/unit/test_bedrock_gateway.py
tests/unit/test_gateway_client.py
tests/unit/test_rag_service.py
tests/unit/test_conversation_memory.py
tests/unit/test_tool_execution.py
tests/unit/media/test_agent_tools_schema.py
tests/unit/media/test_classify_intent_resilience.py
tests/unit/media/test_execute_tool_template_injection.py
tests/unit/media/test_force_generate_no_unbound.py
tests/unit/media/test_native_diagram_pptx.py
tests/unit/media/test_tool_abspath_regression.py
tests/mocks/electron.js
tests/conftest.py
```

### 6.2 e2e — Playwright/pytest

```
tests/e2e/test_editor.py
tests/e2e/test_startup.py
tests/e2e/test_effort_control_ui.py
tests/e2e/test_research_settings_ui.py
tests/e2e/test_research_search_indicator_ui.py
tests/e2e/fixtures/effort-control-harness.html
tests/e2e/fixtures/research-settings-harness.html
tests/e2e/fixtures/search-indicator-harness.html
```

### 6.3 integration/remote — 실측 지연 측정 (7 files)

```
tests/integration/remote/ssh-handshake.integration.test.js
tests/integration/remote/reconnect.integration.test.js
tests/integration/remote/provisioning.integration.test.js
tests/integration/remote/file-read-perf.integration.test.js
tests/integration/remote/watcher-latency.integration.test.js
tests/integration/remote/terminal-latency.integration.test.js
tests/integration/remote/context-switch.integration.test.js
tests/integration/remote/docker-compose.yml
tests/integration/remote/setup.sh
```

---

## 7. 루트 설정 / 문서

### 7.1 빌드·설정 (분석 필수)

```
package.json              스크립트 정의, 의존성, 버전 0.5.4
package-lock.json
jest.config.js
electron-builder.yml      패키징 타겟, notarize:false, publish 설정
ai-engine-server.spec     PyInstaller 동결 스펙
build/entitlements.mac.plist
.gitignore
```

### 7.2 규약 문서

```
AGENTS.md                 에이전트 작업 규약 (DESIGN.md 참조 + Project overrides)
CLAUDE.md
README.md
README_DRAFT.md
```

### 7.3 배포/릴리스 문서

```
RELEASE_NOTES_0.5.1.md
RELEASE_NOTES_0.5.2.md
RELEASE_NOTES_0.5.3.md
RELEASE_NOTES_0.5.4.md    RAG 임베딩 폴백을 TF-IDF→LSA로 상승. "알려진 제한" 정직 기재
DEPLOYMENT_GUIDE.md
DEPLOYMENT_CHECKLIST.md
DEPLOYMENT_PACKAGE_README.md
DELIVERY_CHECKLIST.md
TEST_DELIVERY.md
RESTORE_STATUS.md
```

### 7.4 임시/실험 파일 (분석 우선순위 낮음 — 정리 대상)

```
.ae_all.py  .ae_count.py  .ae_live_verify.py  .ae_mcp_rp.py
.ae_models_probe.py  .ae_single_bg.py  .tmp_patch_15_3.py
create_diagram.py
.ae_mt_boot.log  .ae_mt_sse.log
.ae_par_boot.log  .ae_par_light.log  .ae_par_multi.log  .ae_par_single.log
```

---

## 8. scripts/ — 검증 자산 (641 files)

> ⚠️ **중요**: `scripts/`의 Python 테스트 253개는 `npm test`에 **연결되어 있지 않다**(수동 실행 자산).
> 이게 이 리포의 주요 갭 중 하나. 하지만 방법론적으로는 가장 밀도 높은 부분이다.

### 8.1 audit_*.py — PPTX 산출물 포렌식 (7 files)

python-pptx만 사용, 네트워크 불필요. 이미지 신호 휴리스틱을 발명한 부분.

```
scripts/audit_pptx_baked_text.py        배경 PNG에 구워진 텍스트 검출 (행별 밝기 급변 횟수)
scripts/audit_pptx_images.py            HTML 딥렌더 vs 사진/일러스트 판별
scripts/audit_pptx_media_classify.py    PICTURE 6종 분류 (solid/gradient/photo/text-baked/bar/icon)
scripts/audit_pptx_native_density.py    통합 합격 게이트 — 밀도 × 스타일 품질 (직교 2차원)
scripts/audit_pptx_overlap.py           텍스트↔이미지 IoU
scripts/audit_pptx_textbox_overlap.py   text-on-text 겹침
scripts/audit_pptx_zorder_break.py      z-order 위반 + 깨진 이미지
```

### 8.2 채점/비교 도구

```
scripts/parity_scorer.py         밀도 채점 (요소 개수)
scripts/visual_comparator.py     시각 비교
```

### 8.3 eval_*.py — 품질 평가 하네스 (8 files)

```
scripts/eval_rag_quality.py            scripts/eval_rag_param_sweep.py
scripts/eval_neural_vs_lexical.py      scripts/eval_semantic_vs_lexical.py
scripts/eval_mmr_research_queries.py   scripts/eval_research_quality.py
scripts/eval_model_size_tradeoff.py    scripts/eval_reasoning_perf.py
scripts/rag_benchmark.py               scripts/golden_research.json
```

### 8.4 verify_* / smoke_* — 라이브 검증

```
scripts/verify_circuit_breaker.py         scripts/verify_deny_allow_coexist.py
scripts/verify_e2e_gateway.py             scripts/verify_invoke_image.py
scripts/smoke_frozen_backend.py           scripts/smoke_langgraph_imports.py
scripts/smoke_reasoning_upgrade_live_gateway.py
scripts/check_frozen_imports.py           scripts/check_resolve_callable.py
scripts/real_e2e_pptx_quality_vertex_audit.py
```

### 8.5 probe_* — Gateway 모델 카탈로그 탐지

```
scripts/probe_all_models.py                scripts/probe_full_catalog_via_gateway.py
scripts/probe_image_models.py              scripts/probe_stability_edit_models.py
scripts/gw_model_probe.py                  scripts/_gw_probe.py  scripts/_gw_probe2.py
scripts/validate_gateway_model_capabilities.py    ← 스펙상 미구현으로 기재된 파일 (확인 필요)
scripts/probe_all_models_result.json
scripts/probe_full_catalog_result.json
scripts/probe_image_models_result.json
```

### 8.6 빌드/배포 스크립트

```
scripts/setup-venv.js               개발 venv 준비 (predev)
scripts/build-python.js             PyInstaller 동결 빌드
scripts/sign-and-notarize-mac.sh    서명+공증 (preflight 검증 후 중단하는 구조)
scripts/resign-mac-dmg.sh
scripts/install-mac.command         미서명 빌드 Gatekeeper 우회
scripts/README-설치안내.txt
```

### 8.7 demo_* — 시각 산출물 생성

```
scripts/demo_audit_visual.py               scripts/demo_native_diagram.py
scripts/demo_design_ceiling_vs_genspark.py scripts/demo_genspark_deck.py
scripts/demo_image_slot_fix_visual.py      scripts/demo_render_pdf_to_png.py
```

### 8.8 patch_* / seed_* — 운영 유틸 (일회성 성격)

```
scripts/patch_agent_system_prompt.py    scripts/patch_bedrock_token_header.py
scripts/patch_embed_usage.py            scripts/patch_gen_dir.py
scripts/patch_image_failure_hint.py     scripts/patch_invoke_model_resolve.py
scripts/patch_pricing_cache.py          scripts/patch_tool_credentials.py
scripts/apply_gateway_invoke_patch.py   scripts/gateway_invoke_patch_code.py
scripts/seed_invoke_pricing.py          scripts/seed_us_prefix_pricing.py
scripts/add_models_to_allowlist.py      scripts/accept_all_image_models.py
scripts/grant_invoke_to_all_users.py    scripts/_check_allowlist.py
scripts/install_media_tools.py          scripts/make_operator_catalog_export.py
scripts/_capability_strategies.py       scripts/start_server.py
scripts/conftest.py
```

### 8.9 test_*.py — 253개 (bug_condition / fix_pbt / preservation_pbt 3단 구조)

파일명 규약이 방법론을 그대로 담고 있다.

- `*_bug_condition.py` — **미수정 코드에서 실패해야 하는** 테스트. 실패가 버그의 증거
- `*_fix_pbt.py` — 수정 후 통과해야 하는 property
- `*_preservation_pbt.py` — 미수정 코드에서 **먼저 관찰(observe)한 동작**을 단언. 수정 전후 모두 통과

완전 3단 4쌍: `media_output_quality`, `pptx_image_slot_placement`, `pptx_overlay_collision`, `pptx_quality_vertex_images`
2단 1쌍: `pptx_fullbleed_native_overlay` (fix 검증 = 동일 bug_condition 재실행)

전체 목록은 아래 §9 부록 참조.

---

## 9. 부록 — scripts/test_*.py 전체 목록 (253 files)

```
scripts/test_aggregate_node.py
scripts/test_aggregate_preservation_pbt.py
scripts/test_answer_quality_orchestrator_pbt.py
scripts/test_astream_converse_fallback.py
scripts/test_capability_activation_subset_pbt.py
scripts/test_capability_api_models_baseline.py
scripts/test_capability_baseline_body_equality.py
scripts/test_capability_baseline_inspector.py
scripts/test_capability_canonicalizer_idempotence_pbt.py
scripts/test_capability_effort_bound_client.py
scripts/test_capability_evidence_collector.py
scripts/test_capability_evidence_route_probe.py
scripts/test_capability_failure_handler.py
scripts/test_capability_malformed_exclusion_pbt.py
scripts/test_capability_managed_segment_merge.py
scripts/test_capability_ordering_invariance_pbt.py
scripts/test_capability_request_router.py
scripts/test_capability_selection_validity_pbt.py
scripts/test_capability_serialization_roundtrip_pbt.py
scripts/test_capability_server_client_seam.py
scripts/test_capability_store_contracts.py
scripts/test_capability_validation_runner.py
scripts/test_chat_model_prefer_streaming.py
scripts/test_citation_verify_pbt.py
scripts/test_classify_slide_role.py
scripts/test_consensus_select_pbt.py
scripts/test_context_builder_evidence.py
scripts/test_context_builder_pipeline_wiring.py
scripts/test_cross_verify_pbt.py
scripts/test_crossverify_ui_contract.py
scripts/test_dag_cycle_fallback_pbt.py
scripts/test_dag_sanitize_pbt.py
scripts/test_dag_topological_waves_pbt.py
scripts/test_density_parity_integration.py
scripts/test_deployment_smoke_evaluate_pbt.py
scripts/test_depth_router_binary_pbt.py
scripts/test_depth_router_complex_pbt.py
scripts/test_design_tokens_override.py
scripts/test_dynamic_layout_mapping.py
scripts/test_edit_fallback_chain_property.py
scripts/test_edit_image_inpaint_pbt.py
scripts/test_effort_injection_baseline_preservation_pbt.py
scripts/test_effort_injection_exact_once_pbt.py
scripts/test_embed_provider_fallback_lsa.py
scripts/test_embedding_provider_pbt.py
scripts/test_eval_aggregate_bounds_pbt.py
scripts/test_eval_baseline_no_creds_pbt.py
scripts/test_eval_compare_delta_pbt.py
scripts/test_eval_failure_isolation_pbt.py
scripts/test_eval_harness.py
scripts/test_eval_mock_determinism_pbt.py
scripts/test_eval_recall_monotonic_pbt.py
scripts/test_evaluator_node.py
scripts/test_evaluator_selector_pbt.py
scripts/test_fast_path_finite_pbt.py
scripts/test_fast_path_nodes_pbt.py
scripts/test_fastembed_bundle_cache.py
scripts/test_figure_slot_pbt.py
scripts/test_finite_termination_pbt.py
scripts/test_gateway_async_job_tooluse_preservation.py
scripts/test_gateway_converse_deadline.py
scripts/test_gateway_converse_signature_contract.py
scripts/test_gateway_long_context.py
scripts/test_gateway_openai_methods.py
scripts/test_gateway_prefix_fallback.py
scripts/test_gateway_signature_introspection.py
scripts/test_generate_image_validation_pbt.py
scripts/test_graphdeps_model_roles.py
scripts/test_grounding_below_pbt.py
scripts/test_grounding_prompt.py
scripts/test_grounding_refine_finite_pbt.py
scripts/test_grounding_warning_body_pbt.py
scripts/test_gw_text_pbt.py
scripts/test_html_pipeline.py
scripts/test_html_slides.py
scripts/test_image_gen_response_structure_property.py
scripts/test_image_generation_fallback_chain_property.py
scripts/test_image_model_id_resolution.py
scripts/test_image_parallel.py
scripts/test_korean_pdf_diagram.py
scripts/test_langgraph_checkpoint_store.py
scripts/test_langgraph_credentials_not_stored_pbt.py
scripts/test_langgraph_gateway_only_imports_pbt.py
scripts/test_langgraph_graph_state.py
scripts/test_langgraph_message_conversion.py
scripts/test_langgraph_multiturn.py
scripts/test_langgraph_parallel.py
scripts/test_langgraph_reroute_integration.py
scripts/test_langgraph_route_fallback_integration.py
scripts/test_langgraph_run_stream_no_regression.py
scripts/test_langgraph_sse_contract_pbt.py
scripts/test_langgraph_store.py
scripts/test_langgraph_supervisor.py
scripts/test_langgraph_termination_pbt.py
scripts/test_langgraph_tool_node.py
scripts/test_langgraph_tool_rejection_fallback.py
scripts/test_langgraph_tooluse_roundtrip.py
scripts/test_langgraph_verify_nonblocking_pbt.py
scripts/test_layout_geometry_bounds_pbt.py
scripts/test_local_grounding_pbt.py
scripts/test_make_operator_catalog_export.py
scripts/test_make_plan_schema_pbt.py
scripts/test_media_output_quality_bug_condition.py
scripts/test_media_output_quality_fix_pbt.py
scripts/test_media_output_quality_preservation_pbt.py
scripts/test_memory_flow.py
scripts/test_mermaid_render.py
scripts/test_mmr_relevance_regression.py
scripts/test_model_id_resolution_and_hide.py
scripts/test_model_selection_property.py
scripts/test_models_baseline_regression.py
scripts/test_native_callout.py
scripts/test_native_cover.py
scripts/test_native_density_audit_pbt.py
scripts/test_native_density_realpath_integration.py
scripts/test_native_density_realpath_nochrome.py
scripts/test_native_density_scorer_pbt.py
scripts/test_native_diagram_palette.py
scripts/test_native_diagram_quality.py
scripts/test_native_flow_desc.py
scripts/test_native_icons_desc.py
scripts/test_native_layout_render_pbt.py
scripts/test_native_layout_render_units.py
scripts/test_native_notice_callout.py
scripts/test_native_style_quality_pbt.py
scripts/test_native_vertex_decorative_pbt.py
scripts/test_new_channels_no_creds_pbt.py
scripts/test_no_regression_structure_pbt.py
scripts/test_no_template_backward_compat.py
scripts/test_nonopenai_chat_preserved.py
scripts/test_openai_adapter_property.py
scripts/test_openai_adapter_schema_examples.py
scripts/test_openai_agent_tool_loop.py
scripts/test_openai_catalog_merge_property.py
scripts/test_openai_catalog_serialize_property.py
scripts/test_openai_error_mapping_examples.py
scripts/test_openai_integration.py
scripts/test_openai_jobs_modelid_contract_pbt.py
scripts/test_openai_model_id_passthrough.py
scripts/test_openai_routing_property.py
scripts/test_openai_security_structure.py
scripts/test_openai_sync_body_no_modelid_pbt.py
scripts/test_outpaint_validation_pbt.py
scripts/test_parallel_graph_flags.py
scripts/test_parity_scorer_pbt.py
scripts/test_parse_evaluation_pbt.py
scripts/test_pdf_roundtrip_property.py
scripts/test_phase_gate_compare.py
scripts/test_pipeline_token_injection.py
scripts/test_plan_dispatch_pbt.py
scripts/test_planner_model_role_and_timeout.py
scripts/test_pptx_bmode_solid_integration.py
scripts/test_pptx_cover_toc.py
scripts/test_pptx_direct_native_diagram.py
scripts/test_pptx_donor_clean_layout.py
scripts/test_pptx_editable_diagram.py
scripts/test_pptx_emoji_and_template_integrity.py
scripts/test_pptx_fullbleed_native_overlay_bug_condition.py
scripts/test_pptx_fullbleed_native_overlay_preservation_pbt.py
scripts/test_pptx_html_slides.py
scripts/test_pptx_hybrid_render_content_editable_pbt.py
scripts/test_pptx_hybrid_render_flag_pbt.py
scripts/test_pptx_hybrid_render_plan_pbt.py
scripts/test_pptx_hybrid_render_prompt_pbt.py
scripts/test_pptx_hybrid_render_wiring_pbt.py
scripts/test_pptx_image_slot_placement_bug_condition.py
scripts/test_pptx_image_slot_placement_fix_pbt.py
scripts/test_pptx_image_slot_placement_integration.py
scripts/test_pptx_image_slot_placement_preservation_pbt.py
scripts/test_pptx_imageprompt_to_native.py
scripts/test_pptx_kpi_progress_native.py
scripts/test_pptx_layout_native_no_overlap.py
scripts/test_pptx_layout_quality.py
scripts/test_pptx_model_autoroute.py
scripts/test_pptx_native_density_audit_integration.py
scripts/test_pptx_overlay_collision_bug_condition.py
scripts/test_pptx_overlay_collision_fix_pbt.py
scripts/test_pptx_overlay_collision_integration.py
scripts/test_pptx_overlay_collision_preservation_pbt.py
scripts/test_pptx_quality_vertex_images_bug_condition.py
scripts/test_pptx_quality_vertex_images_fix_pbt.py
scripts/test_pptx_quality_vertex_images_integration.py
scripts/test_pptx_quality_vertex_images_preservation_pbt.py
scripts/test_pptx_quality_vertex_images_render_report.py
scripts/test_pptx_real_path_native_integration.py
scripts/test_pptx_render_endpoint.py
scripts/test_pptx_slide_count_property.py
scripts/test_pptx_template_body_preserved.py
scripts/test_pptx_template_clears_slides.py
scripts/test_pptx_template_design_reuse.py
scripts/test_pptx_universal_design.py
scripts/test_pptx_vertex_first.py
scripts/test_quality_store_deferred_pbt.py
scripts/test_query_expand_pbt.py
scripts/test_rag_quality_metrics_pbt.py
scripts/test_reasoning_observability.py
scripts/test_reasoning_upgrade_e2e_integration.py
scripts/test_reasoning_upgrade_smoke.py
scripts/test_reranker_parse_pbt.py
scripts/test_research_adapter_mapping.py
scripts/test_research_architecture_guard.py
scripts/test_research_asset_reuse_smoke.py
scripts/test_research_cache_hit.py
scripts/test_research_citation_integrity_pbt.py
scripts/test_research_coverage_monotonic_pbt.py
scripts/test_research_credential_masking_pbt.py
scripts/test_research_dedup_idempotent_pbt.py
scripts/test_research_dedup_invariants_pbt.py
scripts/test_research_deep_research_e2e.py
scripts/test_research_deepening_cap_pbt.py
scripts/test_research_error_conditions_pbt.py
scripts/test_research_normalize_roundtrip_pbt.py
scripts/test_research_optin_gate_pbt.py
scripts/test_research_query_normalize_idempotent_pbt.py
scripts/test_research_recency_filter_pbt.py
scripts/test_research_regression_gate.py
scripts/test_research_relevance_sort_pbt.py
scripts/test_research_rerank_permutation_pbt.py
scripts/test_research_search_indicator_lifecycle_pbt.py
scripts/test_research_tool_dispatch.py
scripts/test_research_userdata_path_pbt.py
scripts/test_resolve_active_template.py
scripts/test_retrieval_pipeline_pbt.py
scripts/test_rrf_fusion_pbt.py
scripts/test_search_fusion_noregression.py
scripts/test_section_diagrams.py
scripts/test_should_native_render_units.py
scripts/test_slide_density_clamp_pbt.py
scripts/test_slide_density_parity_pbt.py
scripts/test_slide_density_safety_pbt.py
scripts/test_slide_templates_density.py
scripts/test_slide_templates_genspark_layouts.py
scripts/test_smoke_evaluate_pbt.py
scripts/test_sse_contract_snapshot.py
scripts/test_sse_key_subset_pbt.py
scripts/test_style_profile_extraction_fallback.py
scripts/test_style_profile_roundtrip_property.py
scripts/test_style_profile_validation.py
scripts/test_take_right_reducer_pbt.py
scripts/test_template_end_to_end_backward_compat.py
scripts/test_template_fallback_isolation.py
scripts/test_template_get_delete.py
scripts/test_template_id_and_model_denied.py
scripts/test_template_id_path_escape.py
scripts/test_template_register_validation.py
scripts/test_token_mask_property.py
scripts/test_trace_and_metrics_pbt.py
scripts/test_verified_files_disk_reality_pbt.py
scripts/test_verifier_pbt.py
scripts/test_verify_e2e_harness.py
scripts/test_verify_fallback.py
scripts/test_vertex_auto_enable.py
scripts/test_vertex_image_fallback.py
```

### 9.1 scripts/ 기타 파일 (셸/서브디렉터리)

```
scripts/README-설치안내.txt
scripts/golden_research.json
scripts/install-mac.command
scripts/probe_all_models_result.json
scripts/probe_full_catalog_result.json
scripts/probe_image_models_result.json
scripts/resign-mac-dmg.sh
scripts/sign-and-notarize-mac.sh
scripts/test_image_gen.sh
scripts/test_pdf_gen.sh
scripts/test_pptx_gen.sh
scripts/test_pptx_with_image.sh
scripts/__pycache__/   (산출물·아카이브 — 분석 제외)
scripts/_audit_out/   (산출물·아카이브 — 분석 제외)
scripts/_render_out/   (산출물·아카이브 — 분석 제외)
scripts/archive/   (산출물·아카이브 — 분석 제외)
```

---

## 10. docs/ + CI + 개발 환경 설정

### 10.1 docs/ — 아키텍처·현황 문서 (분석 가치 높음)

```
docs/ARCHITECTURE.md                아키텍처 문서
docs/REMOTE_SSH.md                  원격 SSH 설계 문서
docs/COMPLETION_STATUS.md           완료 현황
docs/TEST_RESULTS_CHECKPOINT.md     테스트 결과 체크포인트
docs/REFACTORING_PLAN.md            리팩터 계획
docs/REFACTOR_PLAN.md               (중복 성격 — 둘 다 확인 필요)
docs/PENDING_UI_FIXES.md            미해결 UI 이슈
docs/INTERVIEW_PREP.md
docs/screenshots/01.png ~ 14.png    UI 스크린샷 14장
docs/screenshots/PLACEHOLDER.md
```

### 10.2 CI

```
.github/workflows/release.yml       릴리스 워크플로 (OS별 러너 동결 빌드 + electron-builder publish)
```

### 10.3 개발 환경 / 에이전트 설정

```
.ai-harness/rules/anti-patterns.yml   안티패턴 룰
.vscode/settings.json
.claude/settings.local.json
```

---

## 11. 분석 제외 대상 (산출물 — 읽지 말 것)

아래는 전부 빌드/실행 산출물이다. 직접 수정 금지이며 분석 대상도 아니다.

```
node_modules/          npm 의존성
venv/  ai_engine/.venv/                Python 가상환경
ai_engine_dist/                        PyInstaller 동결 백엔드 산출물
build-python-work/                     PyInstaller 중간 산출물
dist_electron/                         DMG/ZIP 패키징 산출물 (0.4.0 ~ 0.5.4, 6개 버전)
coverage/                              jest 커버리지 리포트
.generated/                            에이전트가 생성한 PPTX/PDF/PNG 등 (94 files)
.git/  .pytest_cache/  .hypothesis/  .rag_cache/  __pycache__/
scripts/__pycache__/  scripts/_audit_out/  scripts/_render_out/  scripts/archive/
스크린샷 복원/  스크린샷_resized/
루트의 *.pptx / *.pdf / *.docx / *.xlsx / *.png   (에이전트 산출물 샘플)
```

---

## 12. 분석 시 유의사항

1. **`ai_engine/server.py`(15,814줄)는 에디터 버퍼가 stale할 수 있다.** 스펙 문서에 명시된
   라인 번호는 `grep`으로 디스크에서 재확인해야 정확하다.

2. **코드 주석이 1급 사료다.** 이 리포는 "과거엔 이런 결함이 있었다"를 주석에 실측 근거와 함께
   남기는 관례를 따른다. 특히 다음 주석은 설계 결정의 근거를 담고 있어 코드보다 정보량이 많다.
   - `agent_system/graph_state.py` — reducer 선택 이유 (Send fan-out echo/reset)
   - `agent_system/deps.py` — Opus를 쓰지 않는 라이브 실측 근거
   - `agent_system/nodes/tool_node.py` — 미디어 세마포어 도입 이유
   - `gateway_module.py` — 6개 실패 모드별 재시도 정책의 각 근거

3. **스티어링과 코드가 어긋난 지점이 있다.** `.kiro/steering/project.md`는 Planner=Opus를
   명시하지만 `deps.py` 실제 기본값은 전부 Sonnet 4.5다. 게이트웨이 스트리밍 제약 때문이며
   `deps.py` 주석에 근거가 있다. **코드가 현실, 스티어링이 원래 의도**로 읽을 것.

4. **환경변수가 동작을 크게 바꾼다.** 그래프 토폴로지 자체가 플래그로 결정된다.
   ```
   AE_LANGGRAPH=on              (기본) LangGraph 경로 활성
   AE_LANGGRAPH_PARALLEL=on     (기본) build_parallel_top_graph vs build_top_graph
   AE_ENABLE_DAG_PLANNER=on     (기본) 의존성 인식 Wave 스케줄링
   AE_ENABLE_EVALUATOR=on       (기본) 재계획 루프
   AE_ENABLE_ADAPTIVE_DEPTH=off (기본) Fast_Path 분기
   AE_ENABLE_GROUNDING_GATE=off (기본) 근거 게이트 — 기본 비활성이라는 점이 중요
   ```
   조립 분기는 `ai_engine/server.py` 10097~10184.

5. **알려진 갭 (스펙에 기재된 것)**
   - `server.py` 15,814줄 / `src/main.js` 8,118줄 모놀리스
   - `scripts/`의 Python 테스트 253개가 `npm test`에 미연결
   - `notarize: false` — 미서명·미공증 배포
   - `agent_system/` 내 토큰/비용/latency 추적 부재 (grep 0건)
   - `gateway-models-effort-support` 15태스크 미완 (capability map 실측 미검증)
   - 브랜치 `gamma`, uncommitted 다수
