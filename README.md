# Mogam Works

> 다중 모델 AI 코드 에디터 — 병렬 추론과 합의, 프로젝트 인식 RAG, LangGraph 멀티에이전트, 딥리서치, 문서·이미지 생성, SSH 원격 개발. 모든 LLM 호출은 자체 AWS Bedrock Gateway를 경유합니다.

- 제품명: Mogam Works (패키지명 `ai-editor`, 버전 0.5.4)
- 저장소: `jangkops/Agentic-Editor` — `main` = 최신 안정 코드, `gamma` = 개발 브랜치
- 플랫폼: macOS (Apple Silicon 우선, Intel·Windows 빌드 타깃 있음)
- 백엔드: Python FastAPI 사이드카(포트 8765), 배포 시 PyInstaller로 동결

이 문서는 **처음 보는 사람이 "어디에 무엇을 썼고, 어떤 순서로 동작하는지"를 코드 없이 이해**할 수 있도록 부분별로 설명합니다. 더 깊은 근거(파일·줄 단위)는 `docs/`와 각 모듈 상단 docstring에 있습니다.

---

## 목차

1. [한눈에 보기](#1-한눈에-보기)
2. [전체 구조](#2-전체-구조)
3. [동작 원리 — 부분별 설명](#3-동작-원리--부분별-설명)
   - 3.1 LLM 호출 경로: Bedrock Gateway 클라이언트
   - 3.2 멀티에이전트 오케스트레이터 (LangGraph)
   - 3.3 프로젝트 RAG — 무엇을 쓰고 어떻게 검색하나
   - 3.4 응답 검증과 대화 메모리
   - 3.5 Deep Research 엔진
   - 3.6 모델 능력 탐지(capability)와 effort
   - 3.7 문서 생성 — PPTX 렌더링 파이프라인
   - 3.8 이미지 생성·편집
   - 3.9 SSH 원격 개발
   - 3.10 Electron 앱 구조와 데이터 저장
4. [기술 스택](#4-기술-스택)
5. [시작하기](#5-시작하기-개발)
6. [환경 변수](#6-환경-변수)
7. [API 엔드포인트와 SSE 이벤트](#7-api-엔드포인트와-sse-이벤트)
8. [프로젝트 구조](#8-프로젝트-구조)
9. [테스트와 스펙 기반 개발](#9-테스트와-스펙-기반-개발)
10. [빌드·배포](#10-빌드배포)
11. [현재 상태와 알려진 제한](#11-현재-상태와-알려진-제한)
12. [설계 원칙](#12-설계-원칙)

---

## 1. 한눈에 보기

Mogam Works는 사내 데스크톱 코드 에디터입니다. Monaco 에디터와 통합 터미널 위에 AI 패널을 두고, 하나의 질문을 **한 모델에 보내거나(단일), 여러 모델에 동시에 보내 비교하거나(병렬), 병렬 답변을 고차원 모델이 종합하게(합의)** 할 수 있습니다. 에이전트 모드에서는 LLM이 파일 읽기·쓰기, 명령 실행, 검색, 이미지·문서 생성, 웹·논문 검색 도구를 스스로 골라 사용합니다.

세 가지 설계 축이 전체를 관통합니다.

- **모든 LLM 호출은 자체 Bedrock Gateway 경유.** 앱은 AWS 자격증명을 어떤 파일에도 저장하지 않고, SSO로 받은 자격증명으로 요청을 SigV4 서명해 게이트웨이에 보냅니다. 게이트웨이가 사용자별(`BedrockUser-{이름}` IAM 역할) 허용 모델·한도·과금을 결정합니다.
- **프로젝트 인식.** 열린 폴더를 로컬에서 인덱싱해(네트워크 없는 임베딩 + BM25) 질문과 관련된 코드 조각을 근거로 붙이고, 답변의 `파일:줄` 인용이 실제 근거와 맞는지 검증합니다.
- **비차단 폴백.** 어떤 하위 기능이 실패해도 요청 전체가 죽지 않도록, 각 단계가 "실패를 값으로 돌려주고 다음 후보를 시도"합니다. 대신 실패 신호가 묻히지 않도록, 요청이 끝날 때 `effect_ledger`가 "선언된 설정·의도"와 "실제 도구 호출"을 대조해 불일치를 `effectSummary` 이벤트로 드러냅니다.

핵심 기능

| 영역 | 내용 |
|---|---|
| AI 채팅 | 단일 / 병렬 / 합의, 스트리밍(SSE), 세션별 히스토리, 자동 인계(긴 대화 요약 후 새 세션) |
| 에이전트 | 도구 12종(파일·셸·검색·이미지 생성/편집·PPTX/PDF/DOCX/XLSX·네이티브 다이어그램) + 리서치 도구 4종 |
| 오케스트레이션 | LangGraph "그래프 속 그래프": Planner → 도메인 워커 5종 병렬 → Aggregate → Evaluator |
| RAG | fastembed(ONNX, 다국어 MiniLM 384차원) + BM25 하이브리드, MMR 다양화, 인용·근거 검증 |
| 딥리서치 | 웹 3종·학술 5종 provider 8종, 옵트인+동의 게이트, 하위 질의 분해 → 병렬 검색 → 중복 제거·재랭킹 → 인용 검증 → 심화 |
| 문서 생성 | PPTX(편집 가능한 네이티브 도형 + HTML→PNG 고품질 베이크 하이브리드), PDF, DOCX, XLSX |
| 이미지 | Bedrock 이미지 모델 병렬 best-of-N, 편집 10모드, Vertex AI 예외 경로 |
| 원격 개발 | ssh2 기반 SFTP 파일·PTY 터미널·명령 실행, 호스트키 TOFU, 원격 엔진 자동 프로비저닝 |
| 인증 | AWS SSO 디바이스 플로우 + BedrockUser assume-role, 월간 한도·SSO 만료 게이지 |

---

## 2. 전체 구조

이 저장소는 에디터(Electron + Python 백엔드)만 담습니다. 게이트웨이 인프라(API Gateway / Lambda / ECS 워커 / IAM, Terraform)는 같은 운영자가 별도 저장소에서 관리합니다.

```
┌─ Electron ──────────────────────────────────────────────────────────────────────┐
│ Renderer (src/)                              Main process (electron/)             │
│  ├ Monaco 에디터 · 파일 탐색기 · 터미널(xterm) ├ IPC 핸들러 86채널 (fs·git·project·  │
│  ├ AI 패널: 단일/병렬/합의, 추천 카드, effort │   sso·terminal·remote·slides·template·  │
│  ├ 센터 뷰: 구조·의존성·통계·검색·Git·리뷰    │   capability·research-creds)             │
│  ├ 미디어/템플릿/리서치 패널 (Web Components) ├ ProcessManager — Python 사이드카 수명   │
│  └ preload contextBridge(electronAPI 92키)   ├ AwsSsoManager — SSO 로그인·자격증명    │
│                                              ├ bridge-server — 사이드카→Electron 역방향 │
│                                              ├ remote/ — ssh2 SFTP·PTY·exec 브리지    │
│                                              └ hidden BrowserWindow — HTML→PNG 캡처   │
└───────────────┬───────────────────────────────────────┬──────────────────────────┘
                │ HTTP + SSE (127.0.0.1:8765)            │ bridge HTTP (127.0.0.1:random, 토큰)
┌───────────────▼───────────────────────────────────────▼──────────────────────────┐
│ FastAPI 사이드카 (ai_engine/, 엔트리 run_server.py → server.py)                    │
│  server.py ─ 도구 12종 구현 · /api/agents/* · /api/models · 템플릿 · quota · handoff  │
│  agent_system/ ─ LangGraph 오케스트레이터 (planner·5 도메인 서브그래프·aggregate·   │
│                  evaluator, JSON 체크포인터, SSE 브리지)                             │
│  rag/ ─ 인덱서 · fastembed/LSA 임베딩 · numpy 벡터 스토어 · BM25 · 하이브리드 검색 ·  │
│         인용/근거 검증 · 대화 메모리                                                │
│  research/ ─ 딥리서치 (provider 어댑터·정규화·dedup·RRF 랭킹·심화 루프)             │
│  capability/ ─ 게이트웨이 실측 기반 모델 활성화·effort 계약                          │
│  slide_templates · native_layout_renderer · native_diagram_pptx · layout_geometry ·  │
│  template_manager · style_profile ─ PPTX 렌더링                                     │
│  gateway_module.py ─ SigV4 서명 · SSO 자격증명 캐시 · assume-role · 재시도 · 잡 폴링 │
└───────────────┬──────────────────────────────────────────────────────────────────┘
                │ SigV4 HTTPS (execute-api / lambda)
┌───────────────▼──────────────────────────────────────────────────────────────────┐
│ AWS Bedrock Gateway (별도 인프라 저장소)                                            │
│  API Gateway: POST /converse · POST /invoke · /invoke-jobs/* · /openai/responses(-jobs)│
│  Lambda Function URL: SSE 실시간 스트리밍                                           │
│  ECS 워커 → Bedrock Runtime · S3 (비동기 잡 결과) · IAM BedrockUser-{name}           │
└───────────────────────────────────────────────────────────────────────────────────┘
외부(선택): Vertex AI(이미지, 키가 있을 때만) · 리서치 provider 8종(옵트인+동의) · mermaid.ink(다이어그램 PNG, 옵트아웃 가능)
```

### 요청 한 건이 흐르는 순서 (채팅 → PPTX 생성 예)

1. **렌더러** `sendMessage`: 첨부 직렬화 → 모델 추천 카드 → `POST /api/agents/classify-intent`로 의도 분류(12초 상한) → 기본 경로 `POST /api/agents/graph-stream`(실패 시 `run-stream`으로 1회 폴백).
2. **서버**: 게이트웨이 클라이언트(`awsProfile`+`bedrockUser`별 캐시) → JSON 체크포인터/스토어 → `GraphDeps` 조립 → 대화 메모리에서 최근 메시지·요약 로드 → LangGraph 실행.
3. **그래프**: Planner가 `select_plan` 도구 강제 호출로 하위 작업 목록(`id, domain, subtask, depends_on`) 생성 → DAG를 Wave로 나눠 현재 Wave의 작업을 도메인 서브그래프(coding/media/research/ops/chat)에 `Send`로 동시 분배 → 각 서브그래프는 `retrieve(RAG) → model → tools → verify` 루프 → Aggregate 합성 → Evaluator가 목표 달성 판정(미달이면 재계획, 상한 2회).
4. **도구 실행**: `generate_pptx` → 템플릿·스타일 프로필 해석 → 표지·본문 슬라이드를 네이티브 도형 또는 HTML→PNG로 렌더 → 저장 후 디스크 재검증 → `{path, absPath}` 반환. verify 노드가 실제 파일 존재를 확인해 `verifiedFiles`로 방출.
5. **SSE 복귀**: `text`, `agent_start/agent_done`, `verifiedFiles`, `heartbeat`(20초), `[DONE]`. 종료 후 백그라운드로 대화 요약·장기 기억 추출.
6. **게이트웨이 계층**(모든 단계 공통): 비스트리밍 `converse`(예산 600초, 자격증명 만료 재시도, 모델 ID 접두어 교정, max_tokens 하향, 비동기 잡이면 S3 결과 폴링), 실시간 `stream_sse_realtime`(Lambda URL, 총 3600초).

---
## 3. 동작 원리 — 부분별 설명

각 절은 "한 줄 요약 → 무엇을 썼나 → 어떻게 동작하나 → 왜 이렇게 했나 → 관련 파일" 순서입니다.

### 3.1 LLM 호출 경로: Bedrock Gateway 클라이언트

**한 줄 요약.** 앱의 모든 LLM 호출은 `gateway_module.py`의 `GatewayClient` 하나를 지나며, 이 클라이언트가 인증·서명·재시도·비동기 잡 처리를 전담합니다.

**무엇을 썼나.** `boto3`(SSO 프로파일·STS assume-role), `botocore` SigV4 서명, `httpx`(SSE 스트림), `urllib`(비스트리밍). 외부 LLM SDK(anthropic, openai)는 쓰지 않습니다.

**어떻게 동작하나.**
- 인증: SSO 자격증명 → (`bedrockUser`가 있으면) `arn:aws:iam::{account}:role/BedrockUser-{이름}`을 assume → 5분 캐시. API Gateway 요청은 서비스 `execute-api`, Lambda Function URL 요청은 서비스 `lambda`로 SigV4 서명.
- 자격증명 경로: SSO 자격증명은 **메인 프로세스**가 `sso:get-credentials` 처리 중 사이드카 `/api/reset-cache`로 직접 주입하고([ipc-sso-handlers.js](electron/src/ipc-sso-handlers.js)), 렌더러에는 `{ok, injected, profile, region}`만 돌려줍니다. 렌더러 소스에 자격증명 필드가 없다는 사실은 `tests/unit/renderer-no-secrets.test.js`가 강제합니다. 같은 자격증명·같은 사이드카에는 1분 안에 다시 주입하지 않고, 로그인·토큰 만료·프로파일 전환 시에는 `force`로 다시 주입합니다. 사이드카가 재기동되면 메인의 `SidecarWatcher`([sidecar-watch.js](electron/src/sidecar-watch.js))가 `/health`의 `boot_id` 변화(정상 5초·다운 1초 간격 폴링)를 보고 마지막 프로파일의 자격증명을 다시 받아 **즉시** 재주입합니다. 원격 터널로 전환돼 다른 인스턴스가 응답할 때도 같습니다(`AE_SIDECAR_WATCH_MS`, `0`이면 끔). `/api/models`는 이때 함께 보관된 SSO 자격증명으로 카탈로그를 조회하므로 렌더러가 비밀을 보낼 필요가 없습니다. 게이트웨이 SSE 스트림은 자격증명 만료(HTTP 403 본문 또는 in-band error)를 만나면 갱신 후 정확히 1회 재시도합니다(`converse` 경로와 같은 정책, 2026-09-16).
- 라우트 4종:

  | 라우트 | 용도 | 핵심 규칙 |
  |---|---|---|
  | `POST /converse` | 도구 호출·계획 수립 등 구조화 응답 | 총 예산 600초 안에서 최대 3회. 자격증명 만료 문구면 갱신 후 재시도, 모델 ID 접두어(`us.`) 거부면 반대 형태로 1회 교정, `max_tokens` 초과면 한계값 또는 50%로 하향(최대 2회). 응답이 `ACCEPTED`+job_id면 S3 결과를 1/2/5/10초 적응형 간격으로 폴링(최대 2시간) |
  | Lambda URL SSE (`stream_sse_realtime`) | 실시간 채팅 토큰 스트림 | 총 3600초 / 연결 30초 / 읽기 300초. 이벤트를 가공 없이 그대로 넘기고 조립은 소비자가 담당 |
  | `POST /invoke` | 이미지 모델 | API Gateway 29초 하드리밋 때문에 서버가 비동기 잡으로 넘기면 `/invoke-jobs/{id}`를 폴링 |
  | `/openai/responses`, `/openai/responses-jobs` | OpenAI 계열 모델 | 동기 120초 → 타임아웃 시 잡 제출 후 폴링. 응답은 `openai_adapter`가 Converse 형식으로 변환 |
- `maxTokens` 정책: `min(AE_MAX_TOKENS(64000), 모델별 상한표)`. 표에 없는 모델은 64000으로 낙관적으로 잡고, 초과 오류가 오면 하향 재시도가 안전망 역할을 합니다("근거 없는 모델별 추측값을 표에 넣지 않는다").

**왜 이렇게 했나.** 실측 기록이 코드 주석에 남아 있습니다. 비스트리밍 `converse`는 최악 1,800초까지 늘어나 예산(deadline)을 도입했고, 1초 고정 폴링이 2시간에 S3 GET 7,200회를 만들어 적응형으로 바꿨으며, `/invoke`의 29초 게이트웨이 한도 때문에 서버측 비동기 핸드오프를 채택했습니다. Claude Opus 계열은 게이트웨이 스트리밍 경로에서 실패하고 비스트리밍은 S3 잡 폴링(300초)에 걸려, 메타 노드(계획·평가) 기본 모델은 Sonnet 4.5입니다.

**관련 파일.** `ai_engine/gateway_module.py`, `ai_engine/openai_adapter.py`, `ai_engine/openai_catalog.py`, `electron/core/aws-sso-manager.js`, `.kiro/steering/gateway.md`(타임아웃·재시도 절이 코드와 정합).

### 3.2 멀티에이전트 오케스트레이터 (LangGraph)

**한 줄 요약.** 한 요청을 Planner가 하위 작업으로 나누고, 도메인별 서브그래프(각각 ReAct 도구 루프)를 병렬 실행한 뒤, Aggregate가 합치고 Evaluator가 목표 달성을 판정하는 "그래프 속 그래프"입니다.

**무엇을 썼나.** `langgraph`(StateGraph, `Send` fan-out, `astream_events v2`), `langchain-core`(BaseChatModel 어댑터). 체크포인터는 SQLite 없이 **파일 기반 JSON**(`JsonFileCheckpointSaver`), 장기 스토어도 JSON(`JsonFileStore`).

**어떻게 동작하나.**
```
START → planner ──Send×N (현재 Wave)──► coding | media | research | ops | chat ──► aggregate
          ▲                                                                          │
          └──── evaluator ◄───────── (DAG 남은 Wave 있으면 planner로) ◄──────────────┘
                   │ achieved=false, refine_count < 2 → planner(재계획)
                   └ achieved=true 또는 상한 → END
```
- 각 도메인 서브그래프: `[retrieve →] model → (tool_calls 있으면) tools → model … → verify → END`. 도구 집합만 다릅니다(coding: 파일·검색·셸 / media: 문서·이미지 7종 / research: 파일·검색 + 웹·논문·본문수집·딥리서치 + MCP / ops: 셸 + MCP / chat: 없음).
- DAG: Planner 출력의 `depends_on`을 정리(`sanitize_depends_on`) → 순환 검사 → 위상 정렬로 **Wave** 목록 생성(`topological_waves`). 순환이면 단일 Wave로 폴백. Wave 수는 하위 작업 수를 넘지 않습니다.
- 유한 종료 예산: Wave ≤ 작업 수, 재계획 ≤ `AE_MAX_REFINE`(2), 동시 fan-out ≤ `AE_MAX_PARALLEL_TASKS`(4), 서브그래프 recursion ≤ 25, 전체 recursion 50, 전체 시간 1,800초.
- 도구 실행(`GatewayToolNode`): tool_calls를 **순차** 실행(부작용 경합 회피), MCP → 원격 브리지 → 로컬 `server._execute_tool` 순으로 디스패치. 미디어 도구 7종은 전역 세마포어 1개·600초로 직렬화(병렬 fan-out에서 동시 실행되면 워크스테이션이 포화되던 실측 대응).
- 상태(`GraphState`) reducer 설계: 스칼라 채널은 last-wins(`_take_right`), 카운터(`refine_count`, `grounding_refine_count`)는 **단조 증가 MAX**, `verified_files`는 절대경로 기준 누적 dedup. 병렬 워커가 자기 substate를 그대로 되돌려주는 "echo" 때문에 last-wins 카운터가 0으로 리셋되어 무한 루프가 났던 결함을 MAX reducer로 막았고, 리스트인 `plan`은 `plan_dispatch`가 워커에 같은 값을 실어 보내 echo를 무해화합니다.
- 모델 배분: 도메인 워커와 라우터는 사용자가 선택한 모델, Planner/Aggregate/Evaluator는 `deps.py` 기본값(Sonnet 4.5). 계획·평가 같은 "도구 강제 호출"은 `prefer_streaming`으로 스트리밍 경로를 우선 사용합니다(같은 호출 실측: 비스트리밍 35초 vs 스트리밍 7.6초).
- SSE 브리지: `astream_events`를 `{text}`, `{tool,status}`, `agent_start/agent_done`, `verifiedFiles`, `searchStatus`, `effectSummary`(불일치 또는 외부 조회 관측 시 `[DONE]` 직전 1회), `heartbeat`(20초 무수신), `[DONE]`으로 변환. Python 3.14에서 스트림 루프 전체를 `wait_for`로 감싸면 취소 시 멈추는 현상 때문에 개별 이벤트 단위로만 타임아웃을 걸고 deadline은 수동 검사합니다(`.kiro/specs/langgraph-hierarchical-orchestrator/API_NOTES.md`).
- 체크포인터는 저장 직전 값 안에 `AKIA|ASIA`+16자 패턴이 있으면 저장을 거부합니다(자격증명 유출 방지).

**왜 이렇게 했나.** 요청별 고유 `thread_id`를 써서 체크포인터와 대화 메모리가 이중으로 맥락을 싣지 않게 했고, 그래프 구조를 바꾸는 플래그(`AE_ENABLE_DAG_PLANNER`, `AE_ENABLE_EVALUATOR`)는 조립 시 1회만 읽어 실행 중 구조가 변하지 않습니다.

**관련 파일.** `ai_engine/agent_system/{supervisor,dag,depth_router,graph_state,deps,chat_model_adapter,sse_bridge,checkpoint_store,store,mcp_tools}.py`, `agent_system/subgraphs/*.py`, `agent_system/nodes/{retrieve,tool_node,verify}.py`.

### 3.3 프로젝트 RAG — 무엇을 쓰고 어떻게 검색하나

**한 줄 요약.** 열린 프로젝트를 로컬에서 청킹·임베딩·색인하고, 질문마다 **벡터 유사도(의미) + BM25(키워드)를 섞어** 관련 코드 조각 8개를 시스템 프롬프트에 근거로 붙입니다. 네트워크 호출 없이 전부 로컬에서 돕니다.

**무엇을 썼나.**

| 구성 | 사용한 것 | 비고 |
|---|---|---|
| 임베딩 모델 | **fastembed**(ONNX Runtime, CPU) · `sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2` · **384차원** | PyTorch 불필요. 다국어라 한국어 질의 ↔ 영문 코드 교차 검색. 모델 파일은 최초 1회 다운로드(약 0.2GB), 동결 빌드는 실행파일 옆 `fastembed_models/`에 사전 번들 |
| 임베딩 폴백 | **LSA**: scikit-learn TF-IDF(1~2gram, `max_features` 4096, sublinear tf) → `TruncatedSVD` 256차원 | fastembed 로드 실패 시 자동. `AE_EMBED_FALLBACK=tfidf`를 명시하면 어휘 TF-IDF(1024차원)로 대체 |
| 벡터 저장 | **numpy** `.npy` float32 행렬 + `vectors.meta.json`(청크 인덱스·파일) | 외부 벡터 DB 없음. 코사인 유사도 브루트포스(프로젝트 규모 ≤ 20,000 청크라 충분) |
| 키워드 검색 | 자체 구현 **BM25** | 코드 식별자 토큰화 `[a-z_][a-z0-9_]*` + 한글 `[가-힣]+` |
| 캐시 위치 | `/fsx/home/<user>/.cache/ae_rag/<hash>` → `<project>/.rag_cache` → `~/.cache/ae_rag/<hash>` → `/tmp/ae_rag/<hash>` | 각 후보에 실제 쓰기 테스트 후 선택 |

**어떻게 동작하나.**
1. **인덱싱**(`indexer.py`): 프로젝트를 재귀 순회하되 `node_modules`, `.venv`, 빌드 산출물, `vendor` 등은 제외하고(`IGNORE_DIRS`), 코드 확장자(`CODE_EXTS`)만, 파일당 500KB 초과 스킵, 누적 `AE_RAG_MAX_CHUNKS`(20,000) 상한. **청킹**은 언어별 함수/클래스 경계 정규식(Python `class|def|async def`, JS/TS `function|class|const x =|export|interface|type`)을 찾아 경계 ±2줄로 자르고, 경계가 없으면 60줄 창 / 10줄 오버랩 슬라이딩. 변경 감지는 트리 전체 mtime의 md5, 5분마다 재색인 여부 확인.
2. **임베딩·저장**: 청크 텍스트를 `"File: {path}\n{content}"`로 감싸 임베딩 → `.npy` 저장. 캐시가 유효(크기·차원 일치)하면 재사용.
3. **질의 분류**(`context_builder.py`): "다양한/비교/examples…"가 있으면 *탐색형*, "함수/클래스/where/exact…"가 있으면 *특정 조회형*. 탐색형이면 MMR 다양화를 켜고(λ 0.4) 임계값을 낮춥니다(0.05); 아니면 λ 0.7, 임계값 0.1.
4. **하이브리드 검색**(`hybrid_search.py`):
   ```
   후보 풀 = top_k × 4
   bm25_norm  = BM25(q, d) / max(BM25)            # k1=1.5, b=0.75
   cos        = cosine(E(q), E(d))                 # 임베딩 차원이 맞을 때만
   score(d)   = (1 − α)·bm25_norm + α·cos          # 기본 weighted, α = 0.5
              (선택 파이프라인 AE_RETRIEVAL_PIPELINE=1 에서 AE_FUSION=rrf 이면 RRF(d) = Σ_r 1/(60 + rank_r(d)) 를 max 정규화)
   → 파일 필터(산출물·캐시 제외) → score ≥ 임계값
   → (탐색형) MMR: argmax λ·score(d) − (1−λ)·max_{s∈선택} cos(d, s)
   → 상위 8개
   ```
5. **컨텍스트 조립**: 개요 + 파일 트리(40줄) + 열린 파일(6,000자) + 각 청크를 `### {file}:L{s}-{e}, score:` 헤더와 언어 코드펜스로 24,000자 예산 안에 삽입 → 시스템 프롬프트.
6. **선택 고급 파이프라인**(`AE_RETRIEVAL_PIPELINE=1`, 게이트웨이 필요): 질의가 짧거나 모호하면 HyDE·동의어로 최대 5개 확장 → 후보 40개 → 다중 질의는 RRF로 합침 → LLM 리랭크(인덱스 배열 JSON, 누락은 원순서 보존) → 8개.

**왜 이렇게 했나.** 게이트웨이 정책상 `BedrockUser`는 `/converse`만 호출할 수 있어 Titan 임베딩을 쓸 수 없었고, 그래서 임베딩을 전부 로컬 CPU에서 처리합니다. 하이브리드 가중 α=0.5는 30개 질의 골든 셋에서 MRR 0.872 → 0.919로 개선된 실측값이며, MMR의 관련성 항을 벡터 유사도가 아닌 하이브리드 점수로 바꾼 것은 정확 키워드 질의의 MRR이 급락했던 회귀의 수정입니다. 배포본에서 ONNX 로드에 실패하면 어휘 검색으로 조용히 떨어지는 대신 LSA로 의미 검색을 유지합니다(0.5.4 릴리스 노트).

**임베딩과 fastembed가 무엇인지 (입문자용).** "임베딩"은 문장을 숫자 벡터로 바꾸는 일입니다. 이 프로젝트에서는 코드 청크 하나(60줄 안팎)가 **실수 384개**로 표현되고, 뜻이 비슷한 문장은 벡터 공간에서 가까운 방향을 가리킵니다. 두 벡터의 코사인 유사도(−1~1, 1이면 방향이 같음)가 "의미상 얼마나 가까운가"의 점수가 됩니다. 이 덕분에 한국어로 "파일 저장하는 함수 어디야"라고 물어도 영어 식별자 `saveFile`이 든 코드가 검색됩니다.

- **fastembed**는 벡터 DB 회사 Qdrant가 공개한 **오픈소스(Apache-2.0)** Python 라이브러리입니다([github.com/qdrant/fastembed](https://github.com/qdrant/fastembed), 설치 버전 0.8.0). 무거운 PyTorch 대신 **ONNX Runtime**(학습된 모델을 프레임워크 독립 형식으로 저장한 것을 CPU에서 실행, 설치 버전 1.27)으로 미리 변환된 모델을 돌리므로 설치가 가볍고 사내 워크스테이션 CPU에서 동작합니다. 이 프로젝트는 `pip install -r ai_engine/requirements.txt`(`fastembed>=0.8.0`)로 가져옵니다.
- **모델**은 sentence-transformers 프로젝트의 `paraphrase-multilingual-MiniLM-L12-v2`(Apache-2.0)입니다. 12층 MiniLM을 영어 문장 유사도 모델에서 다국어(한국어 포함)로 증류한 것으로 출력 384차원입니다. fastembed는 첫 사용 시 Hugging Face Hub의 Qdrant 미러 저장소 `qdrant/paraphrase-multilingual-MiniLM-L12-v2-onnx-Q`에서 양자화된 ONNX 파일(`model_optimized.onnx`, 약 0.22GB)을 내려받아 캐시합니다. 우리 코드는 캐시 위치를 `명시 인자 → AE_FASTEMBED_CACHE → 동결 실행파일 옆 fastembed_models/ → fastembed 기본 캐시` 순으로 정하고([embedder.py#L400-L445](ai_engine/rag/embedder.py#L400-L445), [#L383-L397](ai_engine/rag/embedder.py#L383-L397)), 배포 빌드는 `scripts/build-python.js`가 모델을 미리 내려받아 실행파일 옆에 번들해 사내망에서도 다운로드 없이 동작합니다([build-python.js#L68-L74](scripts/build-python.js#L68-L74)).
- fastembed 초기화가 실패하면 **LSA**로 내려갑니다([embedder.py#L290-L370](ai_engine/rag/embedder.py#L290-L370)): scikit-learn TF-IDF(1~2gram, 어휘 4,096) 행렬을 TruncatedSVD로 256차원에 투영한 것으로, 의미 검색 품질은 낮지만 외부 파일 없이 동작합니다. 어느 쪽이든 벡터는 numpy 행렬로 저장하고 코사인 유사도로 검색합니다([embedder.py#L96-L119](ai_engine/rag/embedder.py#L96-L119), 유사도 0.1 이하는 버림).

**검색 알고리즘 상세 (코드 링크).**

| 단계 | 구현 | 코드 |
|---|---|---|
| 토큰화 | 소문자화 후 `[a-z_][a-z0-9_]*`(식별자) 또는 `[가-힣]+`(한글 어절)만 토큰으로 인정. 숫자로 시작하는 토큰과 기호는 버림 | [indexer.py#L169-L171](ai_engine/rag/indexer.py#L169-L171), [hybrid_search.py#L61-L63](ai_engine/rag/hybrid_search.py#L61-L63) |
| 청킹 | 함수·클래스 경계 정규식으로 분할, 경계가 없으면 60줄 창·10줄 오버랩. 500KB 초과 파일 스킵, 전체 20,000청크 상한 | [indexer.py#L105-L168](ai_engine/rag/indexer.py#L105-L168) |
| BM25 | 질의 토큰 t마다 `idf = ln((N − df + 0.5)/(df + 0.5) + 1)`, `tf' = tf·(k1+1) / (tf + k1·(1 − b + b·dl/avgdl))`, 문서 점수 = Σ idf·tf'. k1=1.5, b=0.75(표준 기본값). 후보 풀은 top_k×4 | [hybrid_search.py#L12-L58](ai_engine/rag/hybrid_search.py#L12-L58) |
| 벡터 점수 | 질의 임베딩과 청크 임베딩의 코사인 유사도(L2 정규화 후 내적) | [embedder.py#L112-L119](ai_engine/rag/embedder.py#L112-L119) |
| 융합 | `score = 0.5·bm25/max(bm25) + 0.5·cos`. α=0.5는 호출부가 지정하며 클래스 기본값 0.6과 다름. 질의 임베딩 차원이 캐시와 다르면 벡터 항을 끄고 BM25만 사용 | [hybrid_search.py#L113-L158](ai_engine/rag/hybrid_search.py#L113-L158), [context_builder.py#L51-L53](ai_engine/rag/context_builder.py#L51-L53) |
| 필터·임계 | 산출물·캐시 파일 제외 → 점수 < 임계(특정 조회형 0.1, 그 외 0.05) 제거 | [hybrid_search.py#L160-L167](ai_engine/rag/hybrid_search.py#L160-L167), [context_builder.py#L223-L235](ai_engine/rag/context_builder.py#L223-L235) |
| MMR | 탐색형 질의만: 이미 뽑은 청크와 코사인이 높은 후보에 감점해 다양성 확보. `λ·relevance − (1−λ)·max cos(선택)`, λ 0.4(탐색형)/0.7(그 외) | [hybrid_search.py#L172-L250](ai_engine/rag/hybrid_search.py#L172-L250) |
| RRF(선택) | 여러 순위 리스트를 `Σ 1/(60 + rank)`로 합침. `AE_RETRIEVAL_PIPELINE=1` 경로에서 `AE_FUSION=rrf`일 때만 | [hybrid_search.py#L254-L287](ai_engine/rag/hybrid_search.py#L254-L287) |
| 컨텍스트 조립 | 상위 8개를 `### 파일:L시작-끝, score:` 헤더와 코드펜스로 24,000자 예산 안에 삽입 | [context_builder.py#L171-L300](ai_engine/rag/context_builder.py#L171-L300) |

**이 검색이 얼마나 믿을 만한가.**
- 측정된 것: [scripts/rag_benchmark.py](scripts/rag_benchmark.py)가 이 저장소 코드를 대상으로 만든 **한↔영 질의 30개(10범주)** 골든 셋으로 recall@k·MRR·context_precision([eval_metrics.py#L11-L76](ai_engine/rag/eval_metrics.py#L11-L76))을 잽니다. 이 벤치에서 α=0.5가 MRR 0.872→0.919(recall 1.0 유지)로 최적이었고, MMR λ=0.7이 0.5보다 precision·MRR에서 앞섰습니다([context_builder.py#L51](ai_engine/rag/context_builder.py#L51), [hybrid_search.py#L101-L102](ai_engine/rag/hybrid_search.py#L101-L102)).
- 한계: 골든 셋이 저자가 이 저장소 하나로 만든 30개라 다른 프로젝트·다른 언어 분포에서의 성능은 보장하지 않으며, 외부 표준 벤치마크 수치는 없습니다. 검색은 "관련 코드 후보"를 제시할 뿐 정답을 보장하지 않으므로, 답변 단계에서 3.4의 검증 층이 근거 일치 여부를 다시 확인합니다.

**관련 파일.** `ai_engine/rag/{indexer,embedder,hybrid_search,context_builder,retrieval_pipeline,query_expansion,reranker}.py`, 벤치마크 `scripts/rag_benchmark.py`, 스펙 `.kiro/specs/rag-answer-quality/`.

### 3.4 응답 검증과 대화 메모리

**응답 검증(answer quality).** 모델 답변이 나온 뒤 세 층으로 신뢰도를 재고 결과를 `answerQuality` 메타로 붙입니다(답변을 막지는 않습니다).
1. **인용 검증**: 답변 속 `파일:줄` 인용(정규식)을 이번 검색에서 실제로 가져온 청크 범위와 대조 → `verified / unverified`.
2. **로컬 grounding 점수**: 답변 문장(최대 40개) 각각에 대해 근거 청크와의 최대 코사인 유사도를 구해 평균. 함의 판단이 아닌 "겹침 근사 하한"입니다.
3. **LLM faithfulness**: 근거(12,000자)와 답변(8,000자)을 게이트웨이 모델에 보내 `SCORE=`를 받음. 10초 타임아웃, 실패 시 `degraded=true`.
게이트웨이 모델 호출이 실측 110~285초까지 걸리는 경우가 있어 기본은 **deferred**: 서버가 `{"qualityPending": id}`만 먼저 보내고 백그라운드로 계산한 뒤 클라이언트가 `GET /api/answer-quality`로 폴링합니다.

**대화 메모리(`conversation_memory.py`).** 최근 10개 메시지를 20,000자 예산 안에서 역순 선택(메시지당 2,000자, 이미지 블록 5개)하고 Bedrock의 user/assistant 교대 규칙을 정리합니다. 12개 이상 쌓이면 Haiku 4.5로 3,000자 요약 + 핵심 사실 10개를 체크포인트로 만들어 첫 user 메시지에 주입합니다. 체크포인트는 `userData/memory/conv_<session>.json`(dev: `~/.agentic-editor/memory`, `AE_MEMORY_DIR`로 변경)에 0600 권한으로 원자적으로 저장되며 저장 전 자격증명 형태의 문자열은 `[REDACTED]`로 지웁니다 — 2026-09-16 이전에는 프로세스 메모리에만 있어 사이드카 재기동 때 사라졌습니다. 렌더러는 별도로 16개 메시지 또는 60,000자를 넘으면 `POST /api/conversation/handoff`로 요약을 받아 새 세션에서 이어갑니다.

**판정 알고리즘 상세 (코드 링크).**

| 판정 | 무엇을 어떻게 재나 | 코드 |
|---|---|---|
| 인용 검증 | 정규식 `([A-Za-z0-9_./-]+\.[A-Za-z0-9_]+):(\d+)(?:-(\d+))?`로 답변 속 `파일:줄` 인용을 뽑아, 이번 검색이 실제로 가져온 청크의 (파일, 줄 범위)와 겹치면 verified, 아니면 unverified | [citation.py#L15-L17](ai_engine/rag/citation.py#L15-L17), [#L72-L97](ai_engine/rag/citation.py#L72-L97) |
| 로컬 grounding | 답변을 문장 최대 40개로 나눠 각 문장 벡터와 근거 청크 벡터들의 **최대 코사인**을 구하고 평균(0~1). LLM 호출 없음 | [verifier.py#L126-L174](ai_engine/rag/verifier.py#L126-L174) |
| LLM 충실도 | 근거 12,000자와 답변 8,000자를 "엄격한 사실 검증자" 프롬프트로 게이트웨이 모델(기본 Sonnet 4.5)에 보내 `SCORE: 0.0~1.0`과 `FEEDBACK`을 받음. 형식이 깨지면 0.5로 간주, 10초(`AE_VERIFY_TIMEOUT_MS`) 초과·예외면 `degraded=true` | [verifier.py#L22-L47](ai_engine/rag/verifier.py#L22-L47), [answer_quality.py#L102-L160](ai_engine/rag/answer_quality.py#L102-L160) |
| 실행 모드 | 기본 deferred: `[DONE]` 뒤 백그라운드(120초 예산)로 계산하고 클라이언트가 `GET /api/answer-quality`를 폴링. `AE_VERIFY_MODE=inline`이면 응답 끝에서 동기 대기 | [answer_quality.py#L163-L205](ai_engine/rag/answer_quality.py#L163-L205) |
| 재생성 트리거 | 충실도가 `AE_VERIFY_THRESHOLD`(0.7) 미달이면 교정 재생성 후보. 점수 없음·degraded면 재생성하지 않음(비차단) | [answer_quality.py#L207-L215](ai_engine/rag/answer_quality.py#L207-L215) |
| Evaluator(그래프) | 워커 산출물을 보고 `submit_evaluation{achieved, reason, missing_domains}` 도구를 강제 호출(300초). 미달이면 재계획(최대 2회). 상한 도달·게이트웨이 부재·파싱 실패·타임아웃은 모두 `achieved=True` | [supervisor.py#L1043-L1150](ai_engine/agent_system/supervisor.py#L1043-L1150), [#L587-L650](ai_engine/agent_system/supervisor.py#L587-L650) |
| 실행 계약 요약 | 요청 끝에 선언(리서치 옵트인·조사 의도 등)과 관측(검색 호출·provider·결과 수)을 대조해 불일치를 `effectSummary`로 방출 | [effect_ledger.py#L307-L330](ai_engine/agent_system/effect_ledger.py#L307-L330), [sse_bridge.py#L276-L292](ai_engine/agent_system/sse_bridge.py#L276-L292) |

**이 판정이 얼마나 믿을 만한가.**
- **결정론적인 것**: 인용 검증과 `verifiedFiles`는 정규식·디스크 실측이라 재현 가능합니다. 단 인용 정규식은 `127.0.0.1:8765` 같은 `호스트:포트`나 `v1.2:3` 같은 버전 표기도 인용으로 오인하므로 unverified 비율이 실제보다 높게 나올 수 있습니다.
- **근사치인 것**: grounding 점수는 함의(entailment) 판단이 아니라 임베딩 유사도의 하한 근사입니다. 근거를 그대로 옮긴 문장은 높게, 근거에서 올바르게 추론한 문장도 낮게 나올 수 있고, LSA 폴백 환경에서는 더 거칩니다.
- **LLM 자기 판정인 것**: 충실도 점수와 Evaluator의 `achieved`는 같은 계열 모델의 1회 판단이며 교차 검증이 없습니다. 둘 다 **비차단·관대한 방향**으로 설계되어 타임아웃·파싱 실패·게이트웨이 부재 시 각각 `degraded`와 `achieved=True`로 귀결됩니다. 즉 "검증 통과"는 "정답 보장"이 아니라 "실패 신호가 없었다"에 가깝습니다. 게이트웨이 모델 호출이 실측 110~285초까지 걸리는 경우가 있어 deferred가 기본이고, 그동안 UI는 `qualityPending` 상태를 보입니다.
- **측정되지 않은 것**: 검색 품질은 30개 골든 셋으로만 측정됐고, 판정 층(grounding·충실도·Evaluator)의 정확도를 사람 라벨과 비교한 수치는 저장소에 없습니다. 답변을 얼마나 믿을지는 사용자가 `verifiedFiles`·인용 표시·`effectSummary`를 보고 판단해야 합니다.

**관련 파일.** `ai_engine/rag/{answer_quality,citation,verifier,gw_text,quality_store,conversation_memory}.py`.

### 3.5 Deep Research 엔진

**한 줄 요약.** 외부 웹·학술 검색을 **사용자가 켜고(옵트인) 동의했을 때만** 수행하며, 질문을 하위 질의로 나눠 병렬 검색 → 중복 제거 → 순위 융합 → 본문 수집 → 인용 포함 리포트 → 인용 검증 → 부족하면 심화 조사의 루프를 돕니다.

**무엇을 썼나.** `httpx`(패키지 안에서 유일한 네트워크 출구는 `backend.py` 한 파일), provider 어댑터 8종, RRF와 권위도 계산은 순수 함수. LLM은 Planner(하위 질의 분해)와 Generator(리포트 작성) 두 곳에서만, 모두 게이트웨이 경유.

| 축 | provider | 키 |
|---|---|---|
| 웹 | Tavily(키리스 모드 `X-Tavily-Access-Mode: keyless`), Exa, Brave | Exa·Brave만 키 필수 |
| 학술 | Semantic Scholar(키 선택), OpenAlex, arXiv(Atom), PubMed(ESearch→EFetch), Europe PMC(생의학 문헌, 키리스) | 전부 키 없이 동작. **기본 활성은 OpenAlex·Europe PMC 둘**(Semantic Scholar는 키 없이 429가 잦아 기본에서 제외, 설정으로 켤 수 있음) |

키는 환경변수(`TAVILY_API_KEY`, `EXA_API_KEY`, `BRAVE_API_KEY`, `SEMANTIC_SCHOLAR_API_KEY`)로만 읽고 파일에 저장하지 않습니다. Electron 쪽은 키를 OS 키체인(`safeStorage`)으로 암호화해 `userData/settings/research-credentials.json`에 암호문만 두고, 실행 시 `POST /api/research/credentials`로 사이드카 프로세스 환경에만 주입합니다. 렌더러는 키가 "있다/없다"만 봅니다.

**어떻게 동작하나(`run_deep_research`).**
1. 게이트 확인: `AE_ENABLE_WEB_RESEARCH`와 `AE_RESEARCH_CONSENT`가 모두 참일 때만 네트워크 호출.
2. Planner가 `plan_subqueries` 도구 강제 호출로 하위 질의(최대 8개, `depends_on` 포함) 생성 → 오케스트레이터의 `dag.topological_waves`를 재사용해 Wave로 분할.
3. Wave 안에서 하위 질의를 병렬 실행. 각 질의는 웹 폴백 체인과 학술 폴백 체인을 동시에 돌리고(체인은 provider를 순차 시도해 첫 성공만 사용) → 어댑터가 공통 스키마(`SearchResult`/`PaperResult`)로 정규화.
4. **source_id 스킴**으로 중복 제거: 웹은 `web:<canonical_url>`(스킴·호스트 소문자, 추적 파라미터·fragment 제거), 논문은 `doi:<canonical_doi>`. dedup 키와 인용 검증 키가 같습니다.
5. **순위 융합**: provider 순위 리스트와 신뢰도 순위 리스트를 RRF(`rag.hybrid_search.rrf_fuse` 재사용)로 합치고, 각 소스의 **권위도**를 noisy-OR로 계산합니다.
   ```
   authority = 1 − (1−dom)(1−venue)(1−cite)
   dom   = 큐레이션·정부 도메인 0.9 · 학술 국가 도메인(ac.kr, edu.au 등) 0.85 · 기타 신뢰 도메인 0.8 · .org 0.5 · 그 외 0.3
   venue = 저명 학회·저널 키워드 0.8 · 그 외 게재처 있으면 0.5 · 없으면 0
   cite  = log1p(인용수) / (log1p(인용수) + log1p(50))
   ```
6. 상위 5개 소스 본문을 `fetch_url_raw`(10초, 100,000자)로 병렬 수집 → `EvidenceSource`. 사설망·루프백·클라우드 메타데이터 주소와 `localhost`/`.internal` 계열 호스트는 요청 전에 차단하고, 리다이렉트도 hop마다 다시 검사합니다(SSRF 방어, [backend.py `url_egress_allowed`](ai_engine/research/backend.py)).
7. Generator가 소스별 발췌 2,000자를 받아 `[web:…]`/`[doi:…]` 인용이 포함된 리포트 작성. 실패하면 소스 목록 나열로 폴백.
8. 인용 검증: 리포트의 `(web|doi):…` 토큰을 추출해 실제 수집 소스와 대조 → `unverified_ratio`, RAG의 `enhance_answer`로 grounding 점수.
9. **심화 판단**: 소스 5개 미만, provider 2종 미만, 미검증 비율 0.2 초과 중 하나면 "심화 조사 N회차" 질의로 2~8을 반복(최대 3회).
10. `userData/research/{session}/report.{json,md}` 원자적 저장.

경량 도구 `web_search`/`search_papers`/`fetch_content`는 딥리서치 없이 단발 검색을 제공하며, 여러 provider 결과를 RRF로 융합하고 `recency_days`가 있으면 최신성 필터를 적용합니다. 검색 시작·종료는 `searchStatus` SSE로 채팅 옆 인디케이터에 "어느 provider에 어떤 질의 요약을 보냈는지"만 표시합니다(개별 URL·키는 표시하지 않음).

셸 도구 `run_command`는 이 게이트 **밖**에 있습니다. 모델이 `curl` 등으로 외부에 직접 나가면 서버는 명령을 막지 않고 신호·대상 호스트·게이트 상태만 감사 로그로 남깁니다(명령 원문은 기록하지 않음, [server.py#L10387-L10416](ai_engine/server.py#L10387-L10416)). 셸을 막으면 npm·git·pip이 함께 죽기 때문에 차단 대신 가시성을 택했습니다.

**관련 파일.** `ai_engine/research/{backend,providers,normalize,dedup,rank,deep_research,models,config,security,eval_harness}.py`, `agent_system/subgraphs/research.py`, `src/components/{research-settings,search-indicator,research-panel}.js`, `electron/core/research-credentials.js`, 스펙 `.kiro/specs/deep-research-engine/`.

### 3.6 모델 능력 탐지(capability)와 effort

**한 줄 요약.** "이 게이트웨이가 어떤 모델의 어떤 라우트를 실제로 지원하는가"를 하드코딩하지 않고, **실제 프로브 요청을 보내 얻은 증거만으로** 모델을 활성화하고 추론 강도(effort) 파라미터를 붙이는 게이트입니다.

**어떻게 동작하나.**
- 후보 라벨(`opus 5`, `sonnet 5`, `gpt 5.6`, `sol`, `terra`, `luna`)에 대해 카탈로그가 라벨↔모델 ID 관계를 명시한 경우에만 정확한 모델 ID를 기록합니다(문자열에서 추론하지 않음).
- 라우트 5종(CONVERSE / INVOKE / OPENAI_RESPONSES / OPENAI_RESPONSES_JOBS / SSE_STREAM)마다 최소 요청(`"ping"`, `maxTokens=1`)을 **기존 전송 코드로 실제 전송**하고, HTTP 성공·유효 출력·정상 종료 세 조건을 모두 만족한 라우트만 `SUPPORTED`. 일시 오류·쿼터·인증 오류는 기록하지 않아 기존 SUPPORTED를 강등시키지 않습니다.
- effort는 카탈로그가 선언한 계약(값 타입·enum·range)만 후보로 삼고, 무-effort 기준선 성공을 확인한 뒤 경계값을 실제 주입 경로로 검증합니다.
- 결과는 `userData/capability/capability_map.json`에 canonical JSON + 지문(fingerprint)으로 저장되고, `/api/models` 응답에 `capabilities`로 병합됩니다. 렌더러의 `<effort-control>`은 `(modelId, route, fingerprint)` 세 값이 정확히 일치하고 `SUPPORTED`일 때만 렌더됩니다.
- 요청 시 seam(`run-stream`, `run-agent`): 계획이 `transmit=false`면 게이트웨이로 보내지 않고 SSE 오류로 종료(폴백 아님), 허용이면 기준 body에 effort를 1회만 기록.

**관련 파일.** `ai_engine/capability/{contracts,store,canonicalizer,baseline_inspector,capability_map,activation_gate,effort_settings,request_builder,failure_handler,evidence_collector}.py`, `scripts/validate_gateway_model_capabilities.py`(검증 실행기), `src/effort-control.js`, 스펙 `.kiro/specs/gateway-models-effort-support/`.

### 3.7 문서 생성 — PPTX 렌더링 파이프라인

**한 줄 요약.** 슬라이드마다 **편집 가능한 네이티브 도형**(python-pptx)과 **HTML→PNG 고품질 베이크**(Electron hidden BrowserWindow 캡처) 두 경로를 조합하되, "콘텐츠 텍스트는 절대 이미지로 굽지 않는다"와 "생성된 이미지는 폐기하지 않는다(손실-0)"를 지킵니다. 목표 품질은 코드 주석이 반복 인용하는 "Genspark/Gamma급"입니다.

**무엇을 썼나.** `python-pptx`(도형·텍스트·그림), `layout_geometry.py`(겹침·경계·슬롯 판정 순수 함수, 표준 라이브러리만), `slide_templates.py`(11종 HTML 레이아웃, 1920×1080), `native_layout_renderer.py`(7종 네이티브 레이아웃), `native_diagram_pptx.py`(다이어그램 8종 + 표지), `template_manager.py`/`style_profile.py`(사용자 .pptx 테마 XML → 7토큰 스타일 프로필), `icon_assets.py`(Lucide 아이콘 → 헤드리스 Chrome → PNG 캐시), Vertex AI(장식 배경, 키가 있을 때).

**어떻게 동작하나(`_tool_generate_pptx`).**
1. 입력 정규화 → 템플릿이 있으면 `Presentation(base.pptx)`로 마스터·테마 상속, 템플릿 슬라이드는 비우지 않고 **디자인 도너**로 재사용.
2. HTML 게이트: Electron 브리지(`/bridge/render-html-to-png`) 또는 로컬 Chrome이 있을 때만 베이크 경로 활성.
3. 표지: `build_native_cover`(KPI 카드·아이콘 배지·스텝 그리드 등 밀도 항목 7개 중 6개 이상) → 제목 길이에 따라 폰트를 단계적으로 자동 축소 → 풀블리드 배경이면 위의 텍스트 제거.
4. 본문 슬라이드 사다리(위에서부터 성공하는 첫 경로 채택):
   - 네이티브 레이아웃 라우팅(`AE_NATIVE_LAYOUT_RENDER=1`일 때): LLM이 11종 중 레이아웃을 고르면 7종 네이티브로 매핑 → 후보 사다리(픽 → feature_grid → two_column → section_divider) → 폴백 → 제목만.
   - **하이브리드 렌더(기본 ON, `AE_HYBRID_RENDER=0`으로 끔)**: 슬라이드 역할별로 주 렌더러를 정합니다. `content`는 네이티브 레이아웃 + 우측 슬롯의 히어로 이미지(슬롯 없는 레이아웃이면 바운디드 보존), `structural`(흐름·트리·아키텍처)은 편집 가능 네이티브 도형(HTML 베이크를 타지 않음), `cover/section/visual`은 Vertex가 켜져 있으면 풀블리드 이미지, 꺼져 있으면 HTML 베이크(켜짐)·네이티브(꺼짐) 폴백. 2026-09-16에 구조형 슬라이드가 Chrome/브리지 활성 시 통짜 PNG로 구워지던 것, 히어로 상대 경로가 열리지 않아 이미지가 폐기되던 것, 도너 템플릿의 빈 샘플 도형이 남던 것, 목차가 HTML 활성 시 빠지던 것을 고쳐 PPTX 계열 62파일 336 tests가 전부 통과합니다.
   - HTML 베이크: `render_layout` → hidden BrowserWindow(`data:` URL, 외부 URL 정규식 5종 사전 차단, sandbox) → PNG → 풀블리드 배경.
   - 네이티브 다이어그램: 슬라이드당 풀블리드 1장 보장(`fullbleed_guard`), 구운 텍스트 배경과 본문 분리(`body_safe_area`).
   - 이미지 임베드: 슬롯에 맞지 않으면 콘텐츠 영역으로 승격(`slot_image_fits`), 종횡비 보존, 경계 클램프.
5. 덱 후처리: 단일 chrome 소스(헤더 밴드·번호 배지·푸터), 전 슬라이드 도형 경계 클램프, 저장 후 0바이트 재검증, `renderReport` 반환.
6. 겹침 판정은 감사 스크립트(`scripts/audit_pptx_textbox_overlap.py`)와 같은 정의(면적비 0.10)를 쓰고, `finalize_placement`가 dedup → 클램프 → 충돌 해소 → 과밀 검사를 수행합니다.

**밀도 모델.** 표지 7항목·본문 8항목 체크리스트에 대해 Genspark 실측 기준점 6을 넘으면 합격(`density_score ≥ 6`). 이 기준으로 `scripts/audit_pptx_native_density.py`가 산출물을 기계 판정합니다.

**PDF / DOCX / XLSX.** reportlab(한글 폰트 등록, 이미지 12cm 캡), python-docx(레벨 1~3 헤딩, 이미지 6in), openpyxl(헤더 색 `#007ACC`). 문서 파생 다이어그램은 기본적으로 네이티브 도형으로 그리며, Mermaid(mermaid.ink 공개 API) 경로는 `AE_PREFER_EDITABLE_DIAGRAM=0`일 때만 사용됩니다.

**왜 이렇게 했나.** 주석에 남은 과거 결함이 설계를 설명합니다: LLM이 11종 레지스트리로 고르는데 네이티브는 7종만 있어 슬라이드 전체가 편집 불가 통짜 이미지가 되던 문제(→ 매핑), 표지 제목·부제 95% 겹침(→ `vertical_stack`), 배지가 카드 안에 100% 포함(→ 거터 배치), 3840×2160 이미지가 0.25인치 슬롯에 찌그러짐(→ 슬롯 승격), 동시 캡처 20개 이상에서 프레임 무음 드롭(→ 동시 4개), CJK 폰트 안착 전 캡처가 DejaVu 글리프로 나옴(→ 500ms 대기).

**관련 파일.** 위 모듈들과 `electron/src/ipc-slides-handler.js`, `electron/src/ipc-template-handlers.js`, 스펙 `.kiro/specs/pptx-*/`(9개, 태스크 246개 완료).

### 3.8 이미지 생성·편집

- `generate_image`: 프롬프트 키워드로 모델 체인을 고르고(diagram → sd3.5-large → ultra → core → nova-canvas → titan-v2 등) 상위 N개(`AE_IMAGE_PARALLEL_N`=5)를 **동시에** 호출해 품질 점수(파일 크기 ≤40 + 해상도 일치 20 + 엔트로피 ≤25 + 우선순위 ≤15)로 best-of-N을 뽑고, 60점 미만이면 프롬프트를 보강해 1회 재시도. 순차 폴백이 "5모델 × 60초"로 30분 이상 걸리던 실측이 병렬화의 근거입니다.
- 서킷브레이커: 모든 모델이 access-denied 패턴으로만 실패하면 300초 동안 이미지 생성을 차단하고 `/api/debug/image-gen-status`로 상태를 보여줍니다.
- Vertex AI(Gemini/Imagen): 서비스 계정 키가 발견되면 1순위로 시도(`AE_DISABLE_VERTEX_IMAGE=1`로 끔). 픽셀값 대신 종횡비 토큰(16:9 등)으로 요청합니다.
- `edit_image`: inpaint, outpaint, upscale, remove-background, erase, search-replace, recolor, style-transfer, control-sketch, control-structure 10모드. 매직바이트로 포맷 판정, 5MB 상한.

### 3.9 SSH 원격 개발

**한 줄 요약.** 원격 호스트에 ssh2로 접속해 파일(SFTP)·터미널(PTY)·명령 실행을 로컬 IPC 뒤에 숨기고, 렌더러는 로컬/원격을 구분하지 않습니다.

- 연결: `~/.ssh/config`(Include 재귀 지원) 또는 즉석 호스트 → 인증 스톰 방지(60초 3회) → 호스트키 **TOFU**(앱 자체 `known_hosts`, 첫 접속 시 지문 확인 다이얼로그, 불일치면 즉시 실패) → publickey → keyboard-interactive(2FA) → 인증 직후 `connected`.
- `ProxyJump`/`ProxyCommand` 호스트는 OS `ssh -N -L` 터널을 먼저 열고 ssh2가 그 로컬 포트에 붙습니다.
- 라우팅: `sessionRouter` 싱글턴이 fs·terminal·git·project IPC와 사이드카의 파일·셸 도구(브리지 서버 경유)를 원격으로 분기합니다. 산출물은 항상 로컬에 저장합니다.
- 프로비저닝(백그라운드): `python3 ≥ 3.11` 확인 → 로컬 `ai_engine` 트리 해시와 원격 매니페스트 비교 후 SFTP 업로드 → venv → `pip install` → `supervisor.sh`(uvicorn 재기동 루프) 기동.
- 자격증명(패스프레이즈·2FA)은 메인 프로세스 메모리에만 두고 종료 시 지우며, 로그는 키 이름 기반 마스킹 후 0600으로 기록합니다.
- 원격 엔진으로의 포트 포워딩은 프로비저닝이 끝난 뒤 백그라운드로 열리며, 터널 너머 `/health`가 2xx일 때만 라우팅을 전환하고 로컬 Python을 멈춥니다(실패하면 로컬 엔진 유지, [forwarder-mount.js](electron/src/remote/forwarder-mount.js)). 자동 재연결은 아직 없습니다(11장).

**관련 파일.** `electron/src/remote/`(24모듈), `electron/src/ipc-remote-handlers.js`, `src/components/remote-*.js`, `ai_engine/bridge_client.py`, `docs/REMOTE_SSH.md`, 스펙 `.kiro/specs/remote-ssh/`.

### 3.10 Electron 앱 구조와 데이터 저장

- **메인 프로세스**(`electron/main.js`, 약 500줄): WindowManager · ProcessManager · DataStore · AwsSsoManager 4개 매니저를 만들고 IPC 핸들러 86채널을 등록합니다. 사이드카는 개발 시 `ai_engine/.venv` Python, 배포 시 `resources/ai_engine_dist/ai-engine-server`(PyInstaller onedir)로 띄우고 `AE_GENERATED_ROOT=userData/generated`를 주입합니다(설치 폴더가 읽기 전용일 수 있는 배포 환경 대비).
- **preload**: `contextBridge.exposeInMainWorld('electronAPI', …)` 92키만 노출, `ipcRenderer` 자체는 노출하지 않습니다. 메인 창은 `contextIsolation: true`, `nodeIntegration: false`.
- **렌더러**(`src/`): 모듈 시스템 없는 classic script. `main.js`가 상태·채팅·SSE 소비·에디터·터미널을 담당하고, `center-views.js`(구조·의존성·통계·검색·Git·리뷰 뷰), `model-recommender.js`(작업 유형 21종 패턴 → 모델 추천), `effort-control.js`, `components/`(Web Components: 템플릿·파일 미리보기·리서치·검색 인디케이터·뷰어)로 나뉩니다. 디자인 토큰은 `src/styles/variables.css`.
- **데이터 저장**(`userData/`, macOS는 `~/Library/Application Support/Mogam Works`): `settings/settings.json`(프로파일 이름·리서치 플래그 등, 자격증명 없음), `settings/chat-sessions.json`, `history/<date>.json`, `usage/usage.json`, `checkpoints/`, `capability/`, `settings/research-credentials.json`(암호문), `generated/`(산출물·LangGraph 체크포인트·리서치 리포트·템플릿).
- **로컬 파일 접근 가드**([path-guard.js](electron/src/path-guard.js)): `fs:*` IPC 19채널의 로컬 분기는 사용자가 대화상자로 연 폴더·파일과 앱 데이터(userData, 임시 디렉터리, `~/.agentic-editor`, `AE_GENERATED_ROOT`) 안의 경로만 허용합니다. 허용 목록은 메인 프로세스 메모리에만 있으며(앱은 마지막 폴더를 자동 복원하지 않고 항상 대화상자로 열기 때문에 충분), 렌더러가 늘릴 수 있는 IPC는 없습니다. 심볼릭 링크는 실경로로 풀어 폴더 밖을 가리키면 거부하며, 원격(SFTP) 경로는 브리지가 먼저 처리해 가드를 거치지 않습니다. `AE_FS_GUARD=0`으로 끌 수 있습니다(비상용). 경로를 받는 다른 채널인 `slides:render-html-to-png`(PNG 출력 경로)과 `project:analyze`·`project:dependencies`(분석 대상 폴더)도 같은 가드를 거칩니다.
- **CSP**: `script-src 'self' 'unsafe-inline' https://cdn.jsdelivr.net`. `'unsafe-eval'`은 2026-09-15에 제거했습니다(렌더러·xterm·Monaco 본체에 `eval`이 없고, Monaco AMD 로더는 eval 가능 여부를 탐지해 `<script>` 로딩으로 폴백). `connect-src`는 로컬 사이드카(`localhost`, 원격 터널용 `127.0.0.1`)와 CDN·GitHub만 허용합니다.
- **HTML→PNG 렌더 창**: `sandbox: true`, `data:` URL만 로드, 외부 URL 정규식 차단, 동시 4개.
- **단일 인스턴스·백엔드 점유 판정**(2026-09-17): 두 번째 실행은 기존 창을 앞으로 가져오고 종료합니다. 사이드카 시작 전에 `127.0.0.1:8765`의 `/health` 본문(`service`)으로 우리 사이드카인지 판정해, 다른 프로세스가 점유하면 오판 대신 오류 대화상자를 띄웁니다([backend-guard.js](electron/src/backend-guard.js)). macOS에서 창을 모두 닫아 사이드카가 종료된 뒤 다시 열면 백엔드도 다시 띄웁니다. `git:discard-all`(미추적 파일 삭제)은 렌더러의 확인을 거친 `{confirm:true}` 없이는 실행되지 않습니다.
- **사이드카 API 경계**(2026-09-17): CORS는 렌더러 원본(`null`)과 `localhost`/`127.0.0.1`만 허용합니다(`AE_CORS_ORIGINS`로 추가). `/api/debug/bridge`·`image-gen-status`·`openai-test`는 `AE_DEBUG_ENDPOINTS=1`일 때만 열립니다. 모델이 고르는 도구는 경계를 지킵니다 — `run_command`의 자식 프로세스에는 브리지 토큰·`*_TOKEN`·`*SECRET*`·`API_KEY`·`PASSWORD` 류 env를 넘기지 않고, `write_file`은 프로젝트·생성 루트·임시 디렉터리 밖을 거부하며, `read_file`은 `.env`·`*.pem`·`id_rsa` 같은 자격증명 파일을 거부합니다(각각 `AE_TOOL_ENV_PASSTHROUGH`·`AE_TOOL_WRITE_ANYWHERE`·`AE_TOOL_READ_SECRETS`=1로 해제).

---
## 4. 기술 스택

| 계층 | 기술 | 버전(package.json / requirements.txt) |
|---|---|---|
| 데스크톱 | Electron, Vanilla JS/HTML/CSS, Web Components | electron ^28, electron-builder ^24 |
| 에디터·터미널 | Monaco Editor(CDN 0.50.0), xterm + node-pty | xterm ^5.3, node-pty ^1.1 |
| 원격 | ssh2 (SFTP·shell·exec) | ssh2 ^1.17 |
| 백엔드 | Python 3.11+, FastAPI, Uvicorn, httpx, boto3 | fastapi ≥0.115, uvicorn ≥0.30, httpx ≥0.27, boto3 ≥1.34 |
| 오케스트레이션 | LangGraph, langchain-core | langgraph ≥0.2(실측 1.1.x), langchain-core ≥0.3 |
| RAG | fastembed(ONNX) 다국어 MiniLM 384d, scikit-learn(LSA·TF-IDF), numpy, 자체 BM25 | fastembed ≥0.8, scikit-learn ≥1.4, numpy ≥1.26 |
| 문서 | python-pptx, reportlab, python-docx, openpyxl, matplotlib, Pillow | python-pptx ≥1.0, reportlab ≥4.0 |
| LLM 게이트웨이 | AWS Bedrock Gateway(API Gateway + Lambda URL SSE), SigV4 | botocore |
| 인증 | AWS SSO(OIDC device flow) + BedrockUser IAM role assume | @aws-sdk/client-sso·sso-oidc·sts |
| 테스트 | Jest + fast-check(JS PBT), pytest + hypothesis(Python PBT), Playwright(e2e) | jest ^29.7, fast-check ^4.8 |
| 패키징 | electron-builder(DMG/zip/NSIS/AppImage) + PyInstaller onedir | — |

---

## 5. 시작하기 (개발)

### 사전 요구
- Node.js 18+ (CI는 20)
- Python 3.11+ (개발 venv는 3.14 사용 중 — 11장 참고)
- AWS SSO 접근 권한(조직 SSO 프로파일 + `BedrockUser-{이름}` IAM 역할)

### 설치
```bash
git clone https://github.com/jangkops/Agentic-Editor.git
cd Agentic-Editor
npm install                      # postinstall이 node-pty를 Electron ABI로 리빌드
python3 -m venv ai_engine/.venv
source ai_engine/.venv/bin/activate
pip install -r ai_engine/requirements.txt
```

### AWS SSO
```bash
aws sso login --profile bedrock-gw
```
앱에서 프로파일과 BedrockUser 이름을 입력하면 모델 목록이 로드됩니다. 자격증명은 파일에 저장하지 않고 런타임 주입과 assume-role로만 사용합니다.

### 실행
```bash
npm run dev                # uvicorn(8765) + Electron 동시 실행 (사이드카는 auto-reload 없음)
npm run dev:python:reload  # Python 변경을 자동 반영하려면 사이드카만 이 스크립트로 따로 기동
```
프론트엔드(`src/`, `electron/`) 변경은 Cmd+R, 메인/Python 변경은 재시작이 필요합니다(사이드카를 `dev:python:reload`로 띄웠다면 Python 변경은 자동 재기동). `dev:python`을 단독으로 띄울 때는 Electron이 주입하는 `AE_GENERATED_ROOT`가 없으므로 설정 파일을 못 찾을 수 있습니다. 이 경우 `AE_SETTINGS_PATH`로 `settings.json` 경로를 지정하세요.

### 단축키
| 단축키 | 동작 |
|---|---|
| `Cmd/Ctrl+S` | 파일 저장 |
| `Cmd/Ctrl+Shift+F` | 프로젝트 검색 |
| `Cmd/Ctrl+Shift+G` | Git 뷰 |
| `Cmd/Ctrl+Shift+S` | 통계 뷰 |
| `Cmd/Ctrl+Shift+L` | 원격 로그 |
| `Cmd/Ctrl+B` | 사이드 패널 토글(Alt 조합 시 오른쪽) |
| `Esc` | 에디터로 복귀 |

---

## 6. 환경 변수

리포 전체에서 `AE_*` 이름이 170여 개 쓰입니다. 처음 보는 사람이 알아야 할 것만 영역별로 모았습니다(기본값은 코드 기준).

| 영역 | 변수 | 기본 | 의미 |
|---|---|---|---|
| 게이트웨이 | `GATEWAY_URL` | us-west-2 execute-api URL | 게이트웨이 베이스 URL |
| | `AE_CONVERSE_TOTAL_BUDGET` / `AE_JOB_MAX_WAIT` | 600 / 7200초 | 비스트리밍 총 예산 / 비동기 잡 대기 |
| | `AE_SSE_TOTAL_TIMEOUT` | 3600초 | 실시간 스트림 총 상한 |
| | `AE_MAX_TOKENS` | 64000 | maxTokens 상한 |
| 오케스트레이터 | `AE_LANGGRAPH` / `AE_LANGGRAPH_PARALLEL` | on / on | LangGraph 경로 / 병렬 그래프 |
| | `AE_ENABLE_DAG_PLANNER` / `AE_ENABLE_EVALUATOR` | on / on | DAG 계획 / 평가 노드 |
| | `AE_MAX_REFINE` | 2 | Evaluator 재계획 상한(`supervisor.py`) |
| | `AE_MAX_GROUNDING_REFINE` | 1 | grounding refine 상한(`nodes/verify.py`). 예전에는 `AE_MAX_REFINE`을 함께 읽어 한쪽 조정이 다른 쪽을 바꿨음 |
| | `AE_MAX_PARALLEL_TASKS` / `AE_MAX_ROUTE_HOPS` | 4 / 4 | 동시 작업 / 순차 홉 |
| | `AE_GRAPH_TOTAL_TIMEOUT` / `AE_MODEL_NODE_TIMEOUT` / `AE_MEDIA_TOOL_TIMEOUT` | 1800 / 300 / 600초 | |
| | `AE_ENABLE_ADAPTIVE_DEPTH` / `AE_ENABLE_GROUNDING_GATE` / `AE_MCP_ENABLED` | off | 단순 질의 fast path / 근거 게이트 / MCP 도구 |
| RAG | `AE_EMBED_PROVIDER` / `AE_EMBED_MODEL` | fastembed / MiniLM-L12-v2 | 임베딩 |
| | `AE_EMBED_FALLBACK` / `AE_LSA_COMPONENTS` | lsa / 256 | 폴백 |
| | `AE_RAG_MAX_CHUNKS` | 20000 | 색인 청크 상한 |
| | `AE_FUSION` | weighted | `AE_RETRIEVAL_PIPELINE=1` 경로에서만 읽힘(rrf 선택). 기본 경로는 항상 weighted |
| | `AE_TOP_K` | (무효) | 코드가 기본 경로·파이프라인 경로 모두 8로 고정해 현재 효력 없음 |
| | `AE_RETRIEVAL_PIPELINE` / `AE_QUERY_EXPAND` / `AE_RERANK` | off | 고급 파이프라인 |
| | `AE_ANSWER_QUALITY` / `AE_VERIFY` / `AE_VERIFY_MODE` | on / on / deferred | 응답 검증 |
| 리서치 | `AE_ENABLE_WEB_RESEARCH` / `AE_RESEARCH_CONSENT` | off / off | 옵트인 / 동의 (둘 다 필요) |
| | `AE_RESEARCH_WEB_PROVIDERS` / `AE_RESEARCH_ACADEMIC_PROVIDERS` | tavily,exa,brave / openalex,europepmc | 학술 기본은 키 없이 안정 응답하는 둘. semantic_scholar·arxiv·pubmed는 값에 넣으면 사용 |
| | `AE_RESEARCH_MAX_SUBQUERIES` / `AE_MAX_DEEPENING` | 8 / 3 | |
| | `TAVILY_API_KEY` `EXA_API_KEY` `BRAVE_API_KEY` `SEMANTIC_SCHOLAR_API_KEY` | — | 앱이 런타임에 주입 |
| 문서·이미지 | `AE_HYBRID_RENDER` | on ("0"만 off) | PPTX 하이브리드 편집 경로 |
| | `AE_NATIVE_LAYOUT_RENDER` | 0 | 네이티브 레이아웃 라우팅(실험) |
| | `AE_ENABLE_HTML_SLIDES` / `AE_DISABLE_HTML_SLIDES` | — | HTML 베이크 강제/차단 |
| | `AE_PREFER_EDITABLE_DIAGRAM` | 1 | 편집 가능 다이어그램 우선 |
| | `AE_PREFER_VERTEX_IMAGE` / `AE_DISABLE_VERTEX_IMAGE` | 1 / — | Vertex 이미지 우선 / 완전 차단 |
| | `AE_ENABLE_VERTEX_IMAGE` | — | Vertex **장식 배경** 옵트인(=1) |
| | `AE_IMAGE_PARALLEL_N` / `AE_IMAGE_QUALITY_THRESHOLD` | 5 / 60 | best-of-N |
| | `AE_DISABLE_MERMAID` | — | mermaid.ink 경로 차단 |
| 경로 | `AE_GENERATED_ROOT` | Electron이 `userData/generated` 주입 | 산출물·체크포인트·템플릿 루트 |
| | `AE_SETTINGS_PATH` / `AE_USERDATA_PATH` / `AE_CHECKPOINT_DIR` | — | 오버라이드 |
| 운영 | `AE_MEMORY_DIR` | — | 대화 요약 체크포인트 저장 폴더. 기본 `dirname(AE_GENERATED_ROOT)/memory` → `~/.agentic-editor/memory` |
| 운영 | `AE_SIDECAR_WATCH_MS` | 5000 | 사이드카 `/health` 감시 주기(ms). 재기동(boot_id 변화) 시 자격증명 즉시 재주입. `0`이면 끔 |
| 보안 | `AE_FS_GUARD` | 1 | 로컬 fs IPC 경로 가드. `0`이면 해제(비상용) |
| 보안 | `AE_CORS_ORIGINS` | — | 사이드카 CORS 추가 허용 원본(쉼표 구분). 기본은 `null`(렌더러)·localhost·127.0.0.1 |
| 보안 | `AE_DEBUG_ENDPOINTS` | — | `1`이면 `/api/debug/bridge`·`image-gen-status`·`openai-test` 개방 |
| 보안 | `AE_TOOL_ENV_PASSTHROUGH` | — | `1`이면 `run_command` 자식에 브리지 토큰·비밀류 env도 상속 |
| 보안 | `AE_TOOL_WRITE_ANYWHERE` | — | `1`이면 `write_file`의 허용 루트 제한 해제 |
| 보안 | `AE_TOOL_READ_SECRETS` | — | `1`이면 `read_file`이 `.env`·`*.pem`·`id_rsa` 등도 읽음 |
| 테스트 | `AE_SKIP_CHROME_TESTS` | — | `1`이면 Chrome 헤드리스 픽셀 테스트를 건너뜀(Chrome을 띄울 수 없는 샌드박스) |
| 빌드 | `AE_BUNDLE_EMBED_MODEL` | MiniLM-L12-v2 | `build:python`이 실행파일 옆에 사전 번들할 fastembed 모델 |
| 빌드 | `AE_REQUIRE_EMBED_BUNDLE` | — | `1`이면 모델 번들(다운로드·링크 실체화·오프라인 실로드) 실패를 빌드 실패로 승격. 릴리스 CI가 설정 |
| 개발 | `AE_DEV_RELOAD` / `NO_RELOAD` | — | `scripts/start_server.py`로 사이드카를 띄울 때만 읽힘. `AE_DEV_RELOAD=1`이면 auto-reload, `NO_RELOAD=1`이면 그래도 끔. `npm run dev`(=`dev:python`)는 원래 reload 없이 뜨므로 이 변수들의 영향을 받지 않음 |

---

## 7. API 엔드포인트와 SSE 이벤트

사이드카 `server.py`의 라우트 28개(+ `/health`).

| Method | Path | 설명 |
|---|---|---|
| GET/HEAD | `/health` | 헬스 체크. `boot_id`(기동 식별자)로 메인이 재기동을 감지 |
| GET/POST | `/api/models` | 모델 카탈로그(텍스트·이미지·비디오·임베딩·리랭크) + capability 병합. POST 바디는 `{profile, bedrockUser}`이며 카탈로그 조회에는 `/api/reset-cache`로 주입된 SSO 자격증명을 사용 |
| POST | `/api/reset-cache` | 게이트웨이 클라이언트 캐시 초기화 + 자격증명 재주입 |
| POST | `/api/agents/classify-intent` | 의도 분류(haiku→sonnet 후보 체인) |
| POST | `/api/agents/graph-stream` | **기본 채팅 경로** — LangGraph SSE |
| POST | `/api/agents/run-stream` | 단일 모델 SSE(폴백·합의) |
| POST | `/api/agents/run-agent` | 도구 루프 에이전트 SSE(파이프라인 단계) |
| POST | `/api/agents/run-parallel` | 병렬 모델 비교 SSE |
| POST | `/api/agents/run-orchestrated` | Planner/Worker/Merger 오케스트레이터 SSE(레거시) |
| POST | `/api/agents/run` | 비스트리밍 단발(레거시) |
| GET | `/api/answer-quality` | deferred 응답 검증 결과 조회 |
| POST | `/api/conversation/handoff` | 긴 대화 요약 후 새 세션 인계 |
| GET | `/api/quota` | BedrockUser별 월 사용량·한도 |
| POST | `/api/rag/index` · GET `/api/rag/status` | 프로젝트 색인 트리거 / 상태 |
| POST | `/api/attachments/extract-zip` | 첨부 zip 해제(파일 5MB·총 50MB·200개) |
| POST | `/api/research/credentials` · GET `/api/research/status` | 리서치 provider 키 런타임 주입 / 상태 |
| POST/GET | `/api/templates`, GET/DELETE `/api/templates/{id}`, GET `/api/templates/{id}/style-profile` | PPTX 템플릿 CRUD·스타일 프로필 |
| POST | `/api/media/pptx-render` | 저장된 PPTX 미리보기(제목·불릿·이미지 data URL) |
| GET | `/api/debug/cwd` · `/api/debug/bridge` · `/api/debug/image-gen-status` · `/api/debug/openai-test` | 진단 |

### SSE 이벤트

| 이벤트 | 형식 | 방출 경로 |
|---|---|---|
| 텍스트 | `{"text": "..."}` | 전부 |
| 추론 | `{"thinking": "..."}` | run-stream, run-agent |
| 하트비트 | `{"heartbeat": true, "elapsed": n, "phase": "..."}` (run-stream·run-agent) · `{"type": "heartbeat"}` (12초 무이벤트 합성) · graph-stream은 20초 무수신마다 | 전부 |
| 도구 | `{"tool": "read_file", "status": "running|done", "durationMs": n}` | run-agent, graph-stream(MCP 도구) |
| 서브그래프 | `{"type": "agent_start", "taskId": "media"}` → 작업 후 `{"type": "agent_done"}` | graph-stream |
| 산출물 | `{"verifiedFiles": [{"path","absPath","tool"}]}` | 디스크 실측 후 |
| 실행 계약 요약 | `{"effectSummary": {...}}` — 선언 vs 관측 불일치 목록과 외부 조회 통계 | graph-stream, `[DONE]` 직전 최대 1회, 불일치가 있거나 외부 조회 활동이 관측됐을 때만 |
| 검색 상태 | `{"searchStatus": {"phase": "start|end", "kind": "web|academic|…", "providers": [이름만], "query_summary": "...", "status"?: "ok|error"}}` | graph-stream(리서치 도구) |
| 응답 검증 | `{"answerQuality": {...}}` / `{"qualityPending": id}` | run-stream, run-agent |
| 라우팅 | `{"model_routing": {...}}` | run-agent, run-orchestrated |
| 오케스트레이터 | `plan`, `hierarchical_info`, `agent_delta`, `merge` | run-orchestrated |
| 병렬 | `{"slotId","modelId","status","content"}`, `{"heartbeat","progress","total"}` | run-parallel (합의 순위 계산은 렌더러 로컬) |
| 오류 | `{"error": "...", "category"?...}` | 전부 |
| 종료 | `[DONE]` | 전부(예외 시에도 보장) |

---

## 8. 프로젝트 구조

```
agentic-editor/
├── electron/
│   ├── main.js                    # 매니저 4개 조립, IPC 등록, 사이드카·브리지 기동
│   ├── preload.js                 # contextBridge electronAPI(92키)
│   ├── core/                      # aws-sso-manager · process-manager · data-store · window-manager · research-credentials
│   └── src/
│       ├── ipc-{fs,git,project,sso,terminal,remote,slides,template,capability,research}-handlers.js
│       ├── ipc-slides-handler.js  # hidden BrowserWindow HTML→PNG
│       └── remote/                # ssh2 세션 상태머신·SFTP/PTY 브리지·프로비저너·bridge-server·session-router
├── src/                           # Renderer
│   ├── index.html · main.js · center-views.js · model-recommender.js · effort-control.js · model-dropdown-ui.js
│   ├── components/                # template-panel · file-preview-panel · research-{settings,panel} · search-indicator · viewers
│   ├── lib/                       # 순수 함수(파일 정렬·썸네일·크기 포맷·utils)
│   └── styles/                    # variables.css(토큰) · layout.css · components.css
├── ai_engine/
│   ├── run_server.py · server.py  # 엔트리 / FastAPI 라우트 + 도구 12종 + PPTX 파이프라인
│   ├── gateway_module.py · openai_adapter.py · openai_catalog.py · vertex_image_module.py · bridge_client.py
│   ├── agent_system/              # supervisor · dag · depth_router · graph_state · deps · chat_model_adapter · sse_bridge
│   │   ├── nodes/                 # retrieve · tool_node · verify
│   │   ├── subgraphs/             # coding · media · research · ops · chat · _common
│   │   └── checkpoint_store.py · store.py · grounding_gate.py · mcp_tools.py · effect_ledger.py
│   ├── rag/                       # indexer · embedder · hybrid_search · context_builder · answer_quality · citation · verifier · conversation_memory · retrieval_pipeline · reranker
│   ├── research/                  # backend · providers · normalize · dedup · rank · deep_research · config · security · eval_harness
│   ├── capability/                # contracts · store · capability_map · activation_gate · evidence_collector · request_builder · effort_settings · failure_handler
│   ├── slide_templates.py · native_layout_renderer.py · native_diagram_pptx.py · layout_geometry.py · template_manager.py · style_profile.py · icon_assets.py
│   └── requirements.txt
├── scripts/                       # test_*.py(PBT·통합) · audit_*.py(산출물 감사) · eval_*.py(품질 회귀) · probe_*.py(게이트웨이 실측) · build-python.js · setup-venv.js · install-mac.command · validate_gateway_model_capabilities.py
├── tests/                         # unit/*.test.js(Jest) · unit/**/test_*.py · e2e/(Playwright) · integration/remote/(docker sshd)
├── .kiro/specs/                   # 스펙 21개(requirements/design/tasks) · steering/(gateway.md 등)
├── docs/                          # REMOTE_SSH.md 등
├── ai-engine-server.spec · electron-builder.yml · jest.config.js · package.json
```

---

## 9. 테스트와 스펙 기반 개발

- **JS 단위/속성 테스트**: `npm test` → `jest tests/unit/` (fast-check PBT 포함: SSO 프로파일 왕복, 자격증명 비저장 불변식, 로그 마스킹, SSH config 파서 왕복, 상태머신 전이, effort 배선, capability IPC).
- **Python 속성·통합 테스트**: `scripts/test_*.py` 250여 개(hypothesis PBT 100여 개). 러너에 자동 연결되어 있지 않으므로 수동 실행합니다:
  ```bash
  source ai_engine/.venv/bin/activate
  pytest scripts/ -q          # scripts/conftest.py가 Vertex·외부 호출을 끕니다(헤르메틱)
  pytest tests/unit -q        # ai_engine 단위 테스트
  npm run test:e2e            # Playwright(tests/e2e, 4개)
  ```
  `scripts/test_test_hygiene_no_module_global_clobber.py`는 테스트가 import 시점에 다른 모듈의 전역을 덮어쓰는지 AST로 검사하는 위생 가드입니다. 셸 인용 회귀 테스트는 `tests/unit/remote/bridge-search-quoting.test.js`(원격 브리지)와 `scripts/test_execute_tool_search_files_quoting.py`(로컬 도구)에, git IPC의 argv 실행은 `tests/unit/ipc-git-handlers.test.js`에, 포트포워딩 헬스 게이트는 `tests/unit/remote/forwarder-mount.test.js`에, SSRF 가드는 `scripts/test_research_ssrf_guard.py`에, 파일 접근 가드는 `tests/unit/path-guard.test.js`·`tests/unit/ipc-fs-handlers-guard.test.js`에, 자격증명 비노출은 `tests/unit/ipc-sso-credentials.test.js`·`tests/unit/renderer-no-secrets.test.js`·`scripts/test_api_models_catalog_creds.py`에 있습니다.
- **산출물 감사**: `scripts/audit_pptx_native_density.py`, `audit_pptx_textbox_overlap.py` 등이 생성된 PPTX의 밀도·겹침·경계를 기계 판정합니다. `scripts/eval_research_quality.py`는 골든 셋 대비 리서치 품질 회귀를 검사합니다.
- **스펙 기반 개발(`.kiro/specs/`)**: 기능마다 `requirements.md`(EARS 형식) → `design.md`(Correctness Properties 포함) → `tasks.md`(체크박스, `*`는 선택 테스트) 순서로 진행합니다. 버그 수정 스펙은 `bugfix.md`와 3단 테스트(`*_bug_condition`: 수정 전 실패해야 함 → `*_fix_pbt`: 수정 후 통과 → `*_preservation_pbt`: 기존 동작 보존)를 씁니다.
- 테스트 CI(`.github/workflows/test.yml`, 2026-09-16 신설)는 `gamma`/`main` push와 PR마다 Jest 전체와, 릴리스 인터프리터와 같은 Python 3.11(및 3.12)에서 `compileall` → `ruff F821`(미정의 이름) → `check_frozen_imports` → `import ai_engine.server` 스모크 → `scripts/ci_offline_tests.txt`의 오프라인 pytest 집합을 실행합니다. 네트워크(fastembed 모델)·Chrome·저장소 venv를 전제하는 테스트는 목록에서 제외돼 있습니다.
- 릴리스 CI(`.github/workflows/release.yml`)는 태그 `v*` 푸시 시 macOS/Windows 매트릭스에서 필수 모듈 import 게이트 → PyInstaller 동결 → electron-builder 빌드를 수행합니다(자체 테스트 스텝은 없음 — 위 테스트 CI가 선행 게이트).

---

## 10. 빌드·배포

### 빌드
```bash
npm run build:python                                # PyInstaller onedir + fastembed 모델 사전 번들
npx electron-builder --mac --arm64 --publish never  # arm64 DMG (무서명 사내 배포)
npm run dist                                        # build:python + electron-builder
```
- `build:python`은 모델을 내려받은 뒤 Hugging Face 캐시의 심볼릭 링크를 실제 파일로 풀고(Windows 7-Zip 패키징과 링크 미지원 PC 대비), 링크가 남지 않은 캐시의 고아 `blobs/`를 어느 깊이든 지워 번들이 두 배가 되지 않게 하고, 마지막에 `HF_HUB_OFFLINE=1`로 실제 로드를 확인합니다. 릴리스 CI는 `AE_REQUIRE_EMBED_BUNDLE=1`이라 이 중 하나라도 실패하면 빌드가 실패합니다(로컬 빌드는 경고 후 LSA 폴백). huggingface_hub 1.32부터 blobs가 캐시 루트 `blobs/<샤드>/`로 옮겨 가 처음 구현이 놓쳤던 것을 2026-09-22 검증 빌드의 크기 로그로 잡았습니다.
- `node-pty`는 대상 아키텍처로 리빌드되어야 하며 asar 밖으로 풀립니다(`asarUnpack`).
- PyInstaller spec은 `ai_engine` 서브모듈 전체와 서드파티 33개를 수집합니다. 필수 4모듈(matplotlib, scipy, langgraph, pptx)은 `scripts/check_frozen_imports.py`가 동결 전에 검사합니다.

### macOS 설치(수신자)
DMG와 `scripts/install-mac.command`를 같은 폴더에 두고 스크립트를 실행하면 마운트 → `/Applications` 복사 → ad-hoc 서명 → quarantine 제거를 수행합니다. 미서명 빌드라 첫 실행 시 Gatekeeper 경고가 있을 수 있어 스크립트로 우회합니다. 메신저 전달 시 DMG를 zip으로 재압축하지 마세요.

상세 절차 — 릴리스 CI(태그 `v*`), 서명·공증, 수신자 패키지 구성, 로그인 전제조건, 문제 해결, 롤백 — 는 [docs/DEPLOYMENT.md](docs/DEPLOYMENT.md)에 있습니다.

---

## 11. 현재 상태와 알려진 제한

정직한 상태 표시입니다. 항목별 상세와 조치 계획은 내부 분석 문서를 따릅니다.

- **원격 SSH**: 파일·터미널·명령 실행은 동작합니다. 원격 `ai_engine`으로의 포트 포워딩은 호출 규약 오류로 2026-05 이후 한 번도 열리지 않았던 것을 고쳤고, 이제 터널 너머 `/health`가 2xx일 때만 라우팅을 전환합니다(실패하면 로컬 엔진 유지). **실제 원격 호스트에서의 종단 검증은 아직 하지 않았습니다.** 자동 재연결은 미구현이며 끊김 시 로컬로 폴백합니다.
- **effort(추론 강도) 컨트롤**: 카탈로그에 effort 계약이 선언된 모델에서만 표시됩니다. 현재 운영자 카탈로그에는 선언이 없어 UI가 나타나지 않습니다.
- **기본 채팅 경로(graph-stream)**: `thinking`·`answerQuality` SSE는 아직 `run-stream`/`run-agent`에서만 방출됩니다.
- **Python 버전**: 개발 환경은 3.14입니다. 릴리스 CI가 쓰는 3.11에서 import를 막던 3.12 전용 f-string 문법 2곳과 `typing.Any`·`Optional` 누락은 2026-09-16까지 모두 고쳤고, 테스트 CI가 3.11·3.12에서 파싱·미정의 이름·import 스모크를 매 push 확인합니다. 3.14는 어노테이션을 지연 평가해 이런 누락이 로컬에서 드러나지 않으므로 CI 게이트가 유일한 방어선입니다. 2026-09-17 릴리스 워크플로 수동 실행(`publish=never`)에서 **macOS 러너의 PyInstaller 동결 + electron-builder 빌드가 처음으로 끝까지 성공**했습니다(아티팩트 `ai-editor-mac`). Windows 러너는 번들된 fastembed 모델 캐시가 심볼릭 링크라 7-Zip 패키징 단계에서 실패했는데, 2026-09-22에 `scripts/build-python.js`가 모델 다운로드 직후 링크를 실제 파일로 풀고 중복 `blobs/`를 지우도록 고친 뒤 **두 러너 모두 끝까지 성공**했습니다(7-Zip 경고 10건 → 0건). 같은 날 검증 빌드 로그로 번들 모델 캐시 240MB·남은 blobs 0·`HF_HUB_OFFLINE=1` 실로드 OK를 두 러너에서 확인했고, 산출물은 mac DMG/ZIP 각 약 480MB(arm64·x64), Windows NSIS 설치 파일 약 450MB입니다. 아직 GitHub Release로 공개한 적은 없습니다.
- **테스트 자동화**: 테스트 CI(`test.yml`)가 Jest 전체와 오프라인 pytest 집합을 매 push 실행합니다. `scripts/test_*.py` 전체(261파일)는 한 번에 돌리면 네트워크 대기로 멈춰 파일별 실행이 필요하고, PPTX 계열 62파일 336건은 2026-09-16 하이브리드 계약 기준으로 판정·갱신되어 전부 통과합니다(엔진 결함 4건 수정, 이전 세대 기대값 26건 갱신). 릴리스 CI 자체에는 테스트 스텝이 없고 테스트 CI가 선행 게이트입니다.
- **모델**: Claude Opus 계열은 게이트웨이 스트리밍 경로에서 지원되지 않아 계획·평가 노드에는 Sonnet 4.5를 사용합니다.
- **오프라인**: Monaco 에디터는 CDN에서 로드되므로 오프라인에서는 에디터가 뜨지 않습니다.

---

## 12. 설계 원칙

코드 주석과 스펙에 반복해서 선언된 원칙입니다.

- **게이트웨이 전용**: LLM 호출은 `GatewayClient`만 경유. 직접 SDK(boto3 bedrock-runtime, anthropic, openai) 사용 금지. 예외는 이미지 생성의 Vertex AI 한 곳.
- **자격증명 비저장·비노출**: AWS 자격증명은 어떤 파일에도 쓰지 않고 런타임 주입·assume-role만. 렌더러는 비밀 값을 받지 않는다(메인 프로세스가 사이드카에 직접 주입). 리서치 키는 OS 키체인 암호문만 저장. 체크포인트 저장 전 키 패턴 검사.
- **비차단 폴백**: 하위 실패는 값으로 표현하고 다음 후보로 넘어간다. 대신 요청 종료 시 "선언(설정·의도) vs 관측(실제 도구 호출)"을 대조해 조용한 무동작을 표면화한다(`effect_ledger.py`).
- **외부 egress 가시성**: 리서치 도구는 옵트인·동의 게이트를 지키지만 셸 도구는 그 게이트 밖에 있다. 그래서 `run_command`가 외부 네트워크 신호를 보이면 차단하는 대신 신호·대상 호스트·게이트 상태만 로그에 남긴다(명령 원문 미기록). 셸을 막으면 npm·git·pip이 죽기 때문이다. 셸에 넘기는 모델 입력(검색어·경로·패턴)은 `shlex.quote`/`shellQuote`로 인용하고, git IPC는 argv 배열로 실행해(로컬은 셸 미경유) 메타문자가 명령으로 해석되지 않게 한다. 리서치 본문 수집(`fetch_url_raw`)은 사설·루프백·메타데이터 주소와 내부 호스트명을 요청 전에 차단하고 리다이렉트도 hop마다 재검사한다(SSRF 방어).
- **손실-0 · 바이트 보존**: 생성된 이미지는 어떤 분기에서도 폐기하지 않고, 새 렌더 기능은 no-op 기본값으로만 추가해 기존 산출물이 바이트 단위로 동일하게 유지되도록 한다.
- **콘텐츠 텍스트는 이미지로 굽지 않는다**: 편집 가능성 우선. 외부 URL은 HTML 슬라이드에 절대 넣지 않는다.
- **도구 실행 경계**: 모델이 고르는 셸·파일 도구는 프로젝트·생성 루트 안에서만 쓰고, 브리지 토큰·비밀류 env와 자격증명 파일은 도구에 노출하지 않는다. 해제는 명시적 env로만.
- **실측 근거를 남긴다**: 타임아웃·동시성·모델 선택 같은 수치는 재현한 사고나 벤치마크와 함께 주석에 기록한다("동시 캡처 20개 이상에서 프레임 드롭, macOS Sonoma+M2 실측" 등).
- **스펙 먼저**: requirements → design(Correctness Properties) → tasks, 버그는 bug_condition 테스트로 재현한 뒤 수정한다.

---

## License

Internal use only.
