# Mogam Works (Agentic-Editor) 팀 온보딩 가이드

> **대상**: 이 저장소에 처음 합류하는 팀원. 목표는 *첫날에 앱을 띄우고, 둘째 날에 고칠 곳을 스스로 찾는 것*입니다.
> **범위**: 오직 이 저장소 하나 — <https://github.com/jangkops/Agentic-Editor> (기준 브랜치 `main`, 작업 브랜치 `gamma`, 두 브랜치는 항상 같은 커밋을 가리키게 유지). 다른 저장소·계정·내부 주소는 이 문서에 넣지 않습니다. 문서를 갱신할 때도 같은 원칙을 지켜 주세요.
> **정본**: 저장소의 [README.md](README.md). 이 문서는 README를 "읽는 순서"와 "손에 잡히는 첫 작업"으로 재구성한 것이고, 세부 수치·표는 README를 따릅니다.
> **기준 시점**: 2026-09-21, 앱 버전 0.5.4, 커밋 `60ddb99`.

---

## 0. 5분 요약

- **무엇**: 사내 데스크톱 코드 에디터. Monaco 에디터 + 통합 터미널 위에 AI 패널을 두고, 질문 하나를 **한 모델(단일) / 여러 모델 동시(병렬) / 병렬 답변을 상위 모델이 종합(합의)** 으로 보낼 수 있습니다. 에이전트 모드에서는 모델이 파일 읽기·쓰기, 명령 실행, 검색, 이미지·문서(PPTX/PDF/DOCX/XLSX) 생성, 웹·논문 검색 도구를 스스로 골라 씁니다.
- **두 프로세스**: Electron(렌더러 `src/` + 메인 `electron/`) ↔ Python FastAPI 사이드카(`ai_engine/`, `127.0.0.1:8765`, HTTP + SSE). 사이드카 → Electron 역방향은 토큰이 붙은 브리지 HTTP.
- **LLM 호출은 전부 사내 Bedrock Gateway 경유**. 앱은 AWS 자격증명을 어떤 파일에도 저장하지 않고, SSO로 받은 자격증명으로 SigV4 서명해 보냅니다. 사용자별 허용 모델·한도는 게이트웨이가 정합니다. 게이트웨이 인프라 자체는 이 저장소에 없습니다(운영자 관리).
- **프로젝트 인식(RAG)**: 열린 폴더를 로컬에서 색인(fastembed ONNX 다국어 MiniLM 384차원 + BM25 하이브리드, MMR 다양화)하고, 답변의 `파일:줄` 인용이 실제 근거와 맞는지 검증합니다. 네트워크 없이 동작합니다.
- **오케스트레이션**: LangGraph "그래프 속 그래프" — Planner → 도메인 워커 5종(coding/media/research/ops/chat) 병렬 → Aggregate → Evaluator(미달 시 재계획, 상한 2회).
- **설계 축 3개**: 게이트웨이 전용 · 자격증명 비저장/비노출 · 비차단 폴백(하위 실패를 값으로 돌려주고 다음 후보 시도, 요청 끝에 `effect_ledger`가 "선언 vs 실제 도구 호출" 불일치를 표면화).

---

## 1. 첫 30분: 설치하고 띄우기

### 사전 요구
- Node.js 18 이상(CI는 20). Electron 28이 내부에 Node 18을 품고 있습니다.
- Python 3.11 이상. 릴리스 동결은 3.11로 하므로 **3.11에서 깨지는 문법(3.12 전용 f-string 등)은 쓰지 않습니다**. 개발자 venv는 3.14도 쓰지만 CI가 3.11·3.12로 매 push 검사합니다.
- AWS SSO 접근 권한(조직 SSO 프로파일 + 본인 이름의 `BedrockUser-{이름}` IAM 역할). 없으면 앱은 뜨지만 모델 목록이 비어 답변이 나오지 않습니다 — 소유자에게 요청하세요.
- 개발 스크립트는 macOS/Linux 기준입니다(`lsof`, `ai_engine/.venv/bin/python` 경로). Windows에서 개발하려면 `package.json`의 `predev`·`dev:python`을 손봐야 합니다.

### 설치
```bash
git clone https://github.com/jangkops/Agentic-Editor.git
cd Agentic-Editor
npm install                      # postinstall이 node-pty를 Electron ABI로 리빌드
```
Python venv는 `npm run dev`가 처음 실행될 때 `scripts/setup-venv.js`가 자동으로 만듭니다(`python3 -m venv ai_engine/.venv` → `requirements.txt` + pyinstaller 설치). 수동으로 하려면:
```bash
python3 -m venv ai_engine/.venv
source ai_engine/.venv/bin/activate
pip install -r ai_engine/requirements.txt
```

### AWS SSO 로그인
```bash
aws sso login --profile bedrock-gw
```
앱 안에서 프로파일 이름과 BedrockUser 이름을 입력하면 모델 목록이 로드됩니다. 자격증명은 메인 프로세스가 사이드카에 직접 주입하고(렌더러는 비밀 값을 받지 않음), 사이드카가 재기동되면 자동으로 재주입됩니다.

### 실행
```bash
npm run dev                 # (1) venv 준비·8765 포트 정리 → (2) uvicorn 사이드카 + Electron 동시 실행
npm run dev:python:reload   # Python 변경을 자동 반영하려면 사이드카만 이 스크립트로 띄움(기본 dev에는 reload 없음)
```
- 렌더러(`src/`) 변경은 앱에서 Cmd+R, 메인 프로세스(`electron/`)·Python 변경은 재시작(Python은 `dev:python:reload`로 띄웠으면 자동 재시작).
- 사이드카만 따로 띄울 때(`npm run dev:python`)는 Electron이 주입하는 `AE_GENERATED_ROOT`가 없어 설정 파일을 못 찾을 수 있습니다. `AE_SETTINGS_PATH`로 `settings.json` 경로를 지정하세요.

### 잘 떴는지 확인
```bash
curl -s http://127.0.0.1:8765/health
```
200과 함께 `boot_id`가 오면 사이드카 정상입니다. 앱은 시작 시 이 응답으로 "우리 사이드카인지"를 판정하고, 다른 프로세스가 8765를 점유하면 오류 대화상자를 띄웁니다. 두 번째 앱 실행은 기존 창을 앞으로 가져오고 종료합니다.

### 첫날 자주 걸리는 것
| 증상 | 원인·조치 |
|---|---|
| 에디터 영역이 비어 있음 | Monaco 에디터는 CDN에서 로드됩니다. 오프라인이면 뜨지 않습니다. |
| 모델 목록이 비어 있음 | SSO 로그인 안 됨 또는 BedrockUser 역할 미부여. 터미널에서 `aws sso login` 후 앱에서 프로파일 재입력. |
| `npm install`에서 node-pty 리빌드 실패 | Xcode CLT(macOS) / build-essential(Linux) 필요. 실패해도 설치는 계속되지만 터미널 패널이 동작하지 않습니다. |
| `import ai_engine.server` 실패 | venv에 requirements가 덜 깔림. `pip install -r ai_engine/requirements.txt` 재실행. |

---

## 2. 구조 지도

```
Electron ─────────────────────────────────────────────────────────────
  Renderer (src/)                    Main (electron/)
   Monaco · 파일 탐색기 · xterm        main.js: 매니저 조립·IPC 등록·사이드카 기동
   AI 패널(단일/병렬/합의/effort)      src/ipc-*-handlers.js: fs·git·project·sso·terminal·
   센터 뷰(구조·의존성·통계·Git)         remote·slides·template·capability·research
   Web Components 패널               core/: process-manager · aws-sso-manager · data-store
   preload.js contextBridge          src/remote/: ssh2 SFTP·PTY·exec 브리지
                                     hidden BrowserWindow: HTML→PNG 캡처
        │ HTTP + SSE (127.0.0.1:8765)            │ bridge HTTP (127.0.0.1:random + 토큰)
FastAPI 사이드카 (ai_engine/) ──────────────────────────────────────────
  server.py        도구 12종 · /api/agents/* · /api/models · 템플릿 · quota · PPTX 파이프라인
  agent_system/    LangGraph 오케스트레이터(planner · 5 서브그래프 · aggregate · evaluator)
  rag/             인덱서 · fastembed/LSA 임베딩 · numpy 벡터 스토어 · BM25 · 하이브리드 검색 · 인용 검증 · 대화 메모리
  research/        딥리서치(provider 8종 · 정규화 · dedup · RRF 랭킹 · 심화 루프)
  capability/      게이트웨이 실측 기반 모델 활성화 · effort 계약
  slide_templates · native_layout_renderer · native_diagram_pptx · layout_geometry · template_manager · style_profile
  gateway_module.py  SigV4 서명 · SSO 자격증명 캐시 · assume-role · 재시도 · 비동기 잡 폴링
        │ SigV4 HTTPS
AWS Bedrock Gateway (이 저장소 범위 밖) — /converse · /invoke · /invoke-jobs/* · /openai/responses · Lambda URL SSE
외부(선택): Vertex AI(이미지, 키 있을 때만) · 리서치 provider 8종(옵트인+동의) · mermaid.ink(다이어그램 PNG, 옵트아웃 가능)
```

전체 그림과 각 상자의 상세 설명은 [README 2장](README.md#2-전체-구조), 부분별 동작 원리는 [README 3장](README.md#3-동작-원리--부분별-설명)에 있습니다.

### 디렉터리 한 줄 설명
| 경로 | 무엇이 있나 |
|---|---|
| `electron/main.js`, `electron/preload.js` | 앱 진입점, contextBridge(`electronAPI`) |
| `electron/core/` | SSO 매니저, 사이드카 프로세스 매니저, 데이터 저장소, 리서치 키(OS 키체인) |
| `electron/src/ipc-*-handlers.js` | IPC 채널 구현. `path-guard.js`(fs 경로 가드), `backend-guard.js`(8765 점유 판정), `sidecar-watch.js`(재기동 감지·자격증명 재주입) |
| `electron/src/remote/` | ssh2 세션 상태머신, SFTP/PTY 브리지, 원격 엔진 프로비저너 |
| `src/` | 렌더러. `main.js`(채팅·에디터 조립), `center-views.js`, `components/`(Web Components), `lib/`(순수 함수), `styles/variables.css`(디자인 토큰) |
| `ai_engine/server.py` | 라우트 + 도구 12종 + PPTX 파이프라인. 1만 6천 줄이 넘으니 통째로 읽지 말고 `rg`로 위치를 찾아 범위만 읽습니다 |
| `ai_engine/agent_system/` | `supervisor.py`(그래프 조립), `dag.py`, `nodes/`(retrieve·tool_node·verify), `subgraphs/`(도메인 5종), `effect_ledger.py` |
| `ai_engine/rag/` | `indexer` · `embedder` · `hybrid_search` · `context_builder` · `citation` · `verifier` · `conversation_memory` |
| `ai_engine/research/` | 딥리서치 백엔드·provider 어댑터·`security.py`(SSRF 가드) |
| `ai_engine/capability/` | 모델 능력 계약·활성화 게이트·effort 설정 |
| `scripts/` | `test_*.py`(pytest·hypothesis), `audit_*.py`(PPTX 산출물 감사), `eval_*.py`(품질 회귀), `probe_*.py`(게이트웨이 실측), `build-python.js`, `setup-venv.js`, `check_frozen_imports.py`, `ci_offline_tests.txt` |
| `tests/unit/` | Jest(`*.test.js`, fast-check PBT 포함) + `test_*.py` 소수 · `tests/e2e/` Playwright · `tests/integration/remote/` docker sshd |
| `.kiro/specs/` | 기능 스펙 21개(`requirements.md` → `design.md` → `tasks.md`) |
| `.github/workflows/` | `test.yml`(push/PR 검증 게이트), `release.yml`(태그 `v*` 빌드·배포) |
| `docs/` | `DEPLOYMENT.md`(배포 절차 정본), `REMOTE_SSH.md`(원격 SSH 사용자 가이드), 나머지는 이력 문서(8장 참고) |

`node_modules`, `dist_electron`, `build`, `coverage`, `ai_engine_dist`, `.generated`는 산출물입니다. 직접 수정하지 않습니다.

---

## 3. 요청 한 건이 지나는 길 (채팅 → PPTX 생성 예)

1. **렌더러** `sendMessage`: 첨부 직렬화 → 모델 추천 카드 → `POST /api/agents/classify-intent`(12초 상한) → 기본 경로 `POST /api/agents/graph-stream`(실패 시 `run-stream`으로 1회 폴백).
2. **서버**: 게이트웨이 클라이언트(`awsProfile`+`bedrockUser`별 캐시) → JSON 체크포인터 → `GraphDeps` 조립 → 대화 메모리 로드 → LangGraph 실행.
3. **그래프**: Planner가 `select_plan` 도구 강제 호출로 하위 작업(`id, domain, subtask, depends_on`) 생성 → DAG를 Wave로 나눠 도메인 서브그래프에 동시 분배 → 각 서브그래프는 `retrieve(RAG) → model → tools → verify` 루프 → Aggregate → Evaluator.
4. **도구**: `generate_pptx` → 템플릿·스타일 프로필 해석 → 슬라이드 역할별 렌더(content는 편집 가능한 네이티브 도형, cover/section/visual은 이미지 또는 HTML→PNG 베이크) → 저장 후 디스크 재검증 → verify 노드가 `verifiedFiles`로 방출.
5. **SSE**: `text`, `agent_start/agent_done`, `verifiedFiles`, `heartbeat`(20초), `[DONE]`. 종료 후 백그라운드로 대화 요약·장기 기억 추출.
6. **게이트웨이 계층**(공통): 비스트리밍 `converse`(예산 600초, 자격증명 만료 재시도, 모델 ID 접두어 교정, 비동기 잡이면 S3 결과 폴링), 실시간 `stream_sse_realtime`(총 3600초).

---

## 4. 무엇을 고칠 때 어디를 보나

| 하려는 일 | 먼저 볼 곳 | 관련 테스트 |
|---|---|---|
| 채팅 UI·패널·단축키 | `src/main.js`, `src/components/`, `src/styles/` | `tests/unit/utils.test.js`, `effort-control-wiring.test.js` |
| IPC 채널 추가·변경 | `electron/preload.js` + `electron/src/ipc-*-handlers.js` (렌더러에 비밀 값을 넘기지 않는지 확인) | `tests/unit/ipc-handlers.test.js`, `renderer-no-secrets.test.js` |
| 파일 접근 가드 | `electron/src/path-guard.js` (`AE_FS_GUARD=0`으로만 해제) | `tests/unit/path-guard.test.js`, `ipc-fs-handlers-guard.test.js` |
| SSO·자격증명 주입 | `electron/core/aws-sso-manager.js`, `electron/src/ipc-sso-handlers.js`, `sidecar-watch.js` | `tests/unit/aws-sso-*.test.js`, `ipc-sso-credentials.test.js`, `sidecar-watch.test.js` |
| 게이트웨이 호출·재시도·스트리밍 | `ai_engine/gateway_module.py` | `scripts/test_gateway_sse_expiry_retry.py`, `tests/unit/test_bedrock_gateway.py` |
| 계획·워커·평가 흐름 | `ai_engine/agent_system/supervisor.py`, `dag.py`, `subgraphs/` | `scripts/test_aggregate_*.py`, `scripts/test_effort_injection_*.py` |
| RAG 검색 품질 | `ai_engine/rag/hybrid_search.py`, `embedder.py`, `context_builder.py` | `tests/unit/test_rag_service.py`, `scripts/eval_rag_quality.py` |
| 딥리서치 provider | `ai_engine/research/providers.py`, `deep_research.py`, `security.py` | `scripts/test_research_ssrf_guard.py`, `scripts/eval_research_quality.py` |
| PPTX 레이아웃·품질 | `ai_engine/server.py`의 `_tool_generate_pptx`·`_select_hybrid_render_plan`, `native_layout_renderer.py`, `layout_geometry.py`, 스펙 `.kiro/specs/pptx-ultra-quality-hybrid-render/` | `scripts/test_pptx_*.py`, `scripts/audit_pptx_*.py` |
| 모델 능력·effort | `ai_engine/capability/` | `scripts/test_capability_*.py`, `tests/unit/capability-ipc-handlers.test.js` |
| 원격 SSH | `electron/src/remote/`, [docs/REMOTE_SSH.md](docs/REMOTE_SSH.md) | `tests/unit/remote/*.test.js`, `tests/integration/remote/` |
| 빌드·배포 | `scripts/build-python.js`, `ai-engine-server.spec`, `electron-builder.yml`, `.github/workflows/release.yml`, [docs/DEPLOYMENT.md](docs/DEPLOYMENT.md) | `scripts/check_frozen_imports.py`, `scripts/smoke_frozen_backend.py` |

---

## 5. 테스트

### JavaScript (Jest + fast-check)
```bash
npm test                                  # tests/unit 전체 + 커버리지
npx jest tests/unit/ --coverage=false     # 빠른 반복
npx jest tests/unit/path-guard.test.js    # 파일 하나
```
37개 파일. 속성 테스트(fast-check)는 무작위 입력을 쓰므로 실패 시 출력의 counterexample을 그대로 재현 케이스로 옮겨 고정 테스트를 추가합니다.

### Python (pytest + hypothesis)
```bash
source ai_engine/.venv/bin/activate
pytest scripts/test_health_boot_id.py -q                       # 파일 단위로 실행
AE_SKIP_CHROME_TESTS=1 pytest scripts/test_pptx_cover_toc.py -q # Chrome 헤드리스가 없는 환경
pytest tests/unit -q                                            # ai_engine 단위 테스트
```
- `scripts/test_*.py`는 260여 개입니다. **`pytest scripts/`로 한 번에 돌리면 fastembed 모델 다운로드 대기 등으로 멈출 수 있어 파일별로 실행합니다.** `scripts/conftest.py`가 Vertex·외부 호출을 끕니다.
- 네트워크·Chrome·저장소 venv 없이 돌아가는 집합은 [scripts/ci_offline_tests.txt](scripts/ci_offline_tests.txt)에 있습니다. 새 테스트가 오프라인이면 이 목록에도 추가하세요.
- 산출물 감사: `scripts/audit_pptx_*.py`가 생성된 PPTX의 밀도·겹침·경계를 기계 판정합니다.

### CI
- **`test.yml`** — `gamma`/`main` push와 모든 PR에서 실행. Jest 전체(Node 20) + Python 3.11·3.12에서 `compileall` → `ruff F821`(미정의 이름) → 필수 모듈 import 게이트 → `import ai_engine.server` 스모크 → 오프라인 pytest 집합. **이것이 릴리스의 선행 게이트입니다**(릴리스 CI에는 테스트 스텝이 없음).
- **`release.yml`** — 태그 `v*` 푸시 시 macOS/Windows 러너에서 PyInstaller 동결 → electron-builder → GitHub Releases 업로드. 수동 실행(`workflow_dispatch`)은 기본 `publish=never`로 빌드·아티팩트만 만들어 파이프라인을 검증합니다.

---

## 6. 개발 규약

### 브랜치와 푸시
- 소유자는 `gamma`에서 작업하고 `main`을 항상 `gamma`와 같은 커밋으로 유지합니다(`git push origin gamma && git push origin gamma:main`).
- 팀원은 `gamma`에서 브랜치를 따 PR을 올리는 것을 권장합니다. 테스트 CI가 PR에도 돌아 머지 전에 결과를 볼 수 있습니다.
- 커밋 메시지는 `type(scope): 한국어 설명` 형식을 따릅니다(예: `ci(release): 미서명 빌드에서 …`, `fix(rag): …`).
- 저장소 루트의 산출물 파일(예시 PPTX/PDF, 스크린샷 폴더)은 커밋하지 않습니다.

### 스펙 기반 개발 (`.kiro/specs/`)
기능마다 `requirements.md`(EARS 형식) → `design.md`(Correctness Properties 포함) → `tasks.md`(체크박스, `*`는 선택 테스트) 순서로 진행합니다. 버그 수정은 `bugfix.md`와 3단 테스트 — `*_bug_condition`(수정 전 실패해야 함) → `*_fix_pbt`(수정 후 통과) → `*_preservation_pbt`(기존 동작 보존) — 를 씁니다. 새 기능은 스펙부터 쓰고, 기존 스펙의 계약을 바꿀 때는 스펙을 먼저 고칩니다.

### 지켜야 할 설계 원칙 (README 12장 요약)
- **게이트웨이 전용**: LLM 호출은 `GatewayClient`만 경유. boto3 bedrock-runtime·anthropic·openai SDK 직접 사용 금지(예외는 이미지 생성의 Vertex AI 한 곳).
- **자격증명 비저장·비노출**: AWS 자격증명은 파일에 쓰지 않고 런타임 주입·assume-role만. 렌더러는 비밀 값을 받지 않는다. 로그·화면·에러 메시지에 토큰·키를 절대 남기지 않는다.
- **비차단 폴백 + 표면화**: 하위 실패는 값으로 돌려주고 다음 후보로. 대신 `effect_ledger`가 조용한 무동작을 드러낸다.
- **도구 실행 경계**: 모델이 고르는 셸·파일 도구는 프로젝트·생성 루트 안에서만. 자식 프로세스에 브리지 토큰·비밀류 env를 넘기지 않고, `.env`·`*.pem`·`id_rsa` 류는 읽지 않는다. 해제는 명시적 env(`AE_TOOL_*`)로만.
- **셸에 넘기는 모델 입력은 인용**(`shlex.quote`/`shellQuote`), git IPC는 argv 배열로 실행. 리서치 URL 수집은 사설·루프백·메타데이터 주소를 차단(SSRF).
- **콘텐츠 텍스트는 이미지로 굽지 않는다**(편집 가능성 우선). 생성된 이미지는 어떤 분기에서도 폐기하지 않는다.
- **실측 근거를 남긴다**: 타임아웃·동시성·모델 선택 수치는 재현한 사고나 벤치마크와 함께 주석에 기록한다.

### UI 작업
루트의 `CLAUDE.md`는 `AGENTS.md`를 불러옵니다. `AGENTS.md`는 **다크 전용**, CSS 변수(`var(--font-mono)`/`var(--font-ui)`) 우회 금지, 새 팔레트·간격 값 생성 금지, 목업 데이터는 `SAMPLE` 표기, 실제 계정 ID·호스트명·경로를 화면 예시에 넣지 않기를 요구합니다. `AGENTS.md`가 참조하는 조직 공용 디자인 스펙(`~/DESIGN.md`)은 저장소에 포함되지 않으니 UI 작업 전에 소유자에게 받으세요.

---

## 7. 환경 변수 — 첫 주에 알아야 할 것

전체 목록(170여 개 중 알아야 할 것만 영역별 정리)은 [README 6장](README.md#6-환경-변수)에 있습니다. 첫 주에 실제로 만지게 되는 것:

| 변수 | 기본 | 언제 쓰나 |
|---|---|---|
| `AE_SETTINGS_PATH` | — | 사이드카를 Electron 없이 단독 실행할 때 `settings.json` 위치 지정 |
| `AE_HYBRID_RENDER` | on | PPTX 역할별 하이브리드 렌더. `0`이면 이전 단일 경로 |
| `AE_SKIP_CHROME_TESTS` | — | `1`이면 Chrome 헤드리스 픽셀 테스트 건너뜀 |
| `AE_FS_GUARD` | 1 | fs IPC 경로 가드. `0`은 비상용 |
| `AE_DEBUG_ENDPOINTS` | — | `1`이면 `/api/debug/bridge`·`image-gen-status`·`openai-test` 진단 엔드포인트 개방 |
| `AE_DISABLE_MERMAID` | — | `1`이면 다이어그램 PNG를 mermaid.ink 대신 matplotlib로만 생성(외부 호출 없음) |
| `AE_ENABLE_WEB_RESEARCH` / `AE_RESEARCH_CONSENT` | off / off | 딥리서치 외부 검색은 둘 다 켜야 동작(옵트인 + 동의) |
| `AE_LANGGRAPH` | on | `0`이면 LangGraph 경로 대신 단순 경로 |

---

## 8. 문서 지도 — 무엇이 현행이고 무엇이 이력인가

**현행(이 순서로 읽기)**
1. [README.md](README.md) — 구조·동작 원리·기술 스택·환경 변수·API·테스트·빌드·현재 상태·설계 원칙. 정본.
2. [docs/DEPLOYMENT.md](docs/DEPLOYMENT.md) — 배포 채널, 로컬 빌드, 서명·공증, 릴리스 CI, 수신자 설치, 문제 해결, 롤백.
3. [docs/REMOTE_SSH.md](docs/REMOTE_SSH.md) — 원격 SSH 사용자 가이드(지원 config 지시어, 연결, 프로비저닝).
4. `.kiro/specs/<기능>/` — 기능별 요구사항·설계·작업 목록. 코드와 어긋나면 스펙이 진실이며, 코드를 고치거나 스펙을 고쳐 커밋합니다.
5. `RELEASE_NOTES_0.5.x.md` — 버전별 변경 요약.
6. `ANALYSIS_MANIFEST.md` — 저장소 파일 전체 목록과 줄 수(2026-09-15 기준). 분석 도구에 저장소를 통째로 설명할 때 씁니다.

**이력(맥락 참고용, 현재 코드와 다를 수 있음)**
- `docs/ARCHITECTURE.md`(2026-04), `docs/REFACTORING_PLAN.md`·`docs/REFACTOR_PLAN.md`·`docs/COMPLETION_STATUS.md`(2026-04~05 리팩토링 계획·완료 기록), `docs/PENDING_UI_FIXES.md`(2026-05), `docs/TEST_RESULTS_CHECKPOINT.md`(2026-05), `RESTORE_STATUS.md`(2026-04). 구조는 README 2·8장을 기준으로 보세요.

---

## 9. 현재 상태와 알려진 제한 (2026-09-21 기준)

- **테스트 CI**: `main` 최신 커밋 통과. `gamma` 러너에서 원격 세션 상태머신 속성 테스트 1건이 무작위 비문자열 입력에 간헐 실패합니다(테스트 파일 안의 로컬 복사본 문제, 모듈 쪽은 이미 견고화됨. 수정 예정).
- **릴리스 파이프라인**: 2026-09-17 수동 실행(`publish=never`)에서 macOS 러너가 처음으로 끝까지 성공했고, Windows 러너는 fastembed 모델 캐시의 심볼릭 링크 때문에 7-Zip 패키징에서 실패했습니다. 2026-09-22에 `scripts/build-python.js`가 링크를 실제 파일로 풀도록 고친 뒤 **두 러너 모두 끝까지 성공**했습니다(7-Zip 경고 10건 → 0건). 아직 GitHub Release로 공개한 적은 없고, 번들 크기 검증과 자동 업데이트 배선이 다음 항목입니다.
- **원격 SSH**: 파일·터미널·명령 실행은 동작합니다. 원격 엔진 포트 포워딩은 고쳤지만 실제 원격 호스트에서의 종단 검증은 아직이며, 자동 재연결은 미구현(끊기면 로컬 엔진으로 폴백).
- **effort 컨트롤**: 카탈로그에 effort 계약이 선언된 모델에서만 UI가 나타납니다. 현재 카탈로그에는 선언이 없어 보이지 않습니다.
- **기본 채팅 경로(`graph-stream`)**: `thinking`·`answerQuality` SSE는 아직 `run-stream`/`run-agent`에서만 방출됩니다.
- **Python 버전**: 개발 venv 3.14, 릴리스 3.11. 3.14는 어노테이션을 지연 평가해 `typing` 누락이 로컬에서 안 드러나므로 CI(3.11·3.12)가 유일한 방어선입니다.
- **모델**: Claude Opus 계열은 게이트웨이 스트리밍 경로에서 지원되지 않아 계획·평가 노드는 Sonnet 4.5를 씁니다.
- **오프라인**: Monaco 에디터가 CDN 로드라 오프라인에서 에디터가 뜨지 않습니다.
- **패키징 경고**: `package.json`에 `description`·`author`·앱 아이콘이 없어 electron-builder가 경고를 냅니다(빌드는 됨).

---

## 10. Claude Code로 이 저장소 다루기

저장소 루트의 `CLAUDE.md` → `AGENTS.md`가 자동으로 로드되어 스택·디자인 규약·주의사항이 컨텍스트에 들어갑니다. 첫날에 던져 볼 만한 프롬프트:

1. "README 2장의 구조도를 기준으로, 채팅 메시지 하나가 렌더러 `sendMessage`에서 LangGraph 실행까지 지나가는 함수 호출을 `파일:줄`로 따라가 줘."
2. "`ai_engine/rag/hybrid_search.py`에서 fastembed 벡터 점수와 BM25 점수를 어떻게 결합하는지, MMR 다양화는 어디서 적용되는지 코드로 설명해 줘."
3. "`.kiro/specs/pptx-ultra-quality-hybrid-render/`의 requirements.md와 design.md를 요약하고, 각 Correctness Property가 어느 테스트 파일에 대응하는지 표로 만들어 줘."
4. "`tests/unit/path-guard.test.js`를 실행하고, 가드가 막는 경로와 허용하는 경로를 표로 정리해 줘."
5. "`scripts/ci_offline_tests.txt`에 있는 파이썬 테스트를 파일별로 실행해서 결과를 표로 보여 줘. 실패하면 원인만 짚고 고치지는 마."

작업 시 지켜 주세요.
- `ai_engine/server.py`처럼 큰 파일은 `rg`로 위치를 찾고 필요한 범위만 읽습니다. 통째로 읽으면 컨텍스트가 바로 찹니다.
- 테스트는 한 번에 파일 하나. Python 전체 실행은 멈출 수 있습니다(5장).
- 산출물 디렉터리(`dist_electron`, `ai_engine_dist`, `build`, `coverage`, `.generated`)는 수정 대상이 아닙니다.
- 자격증명·토큰이 들어갈 수 있는 파일(`.env`, `*.pem`, SSH 키, AWS 자격증명 파일)은 읽지도 커밋하지도 않습니다. 앱 자체도 그렇게 설계돼 있습니다.
- 다른 저장소·계정·내부 주소를 이 저장소의 문서나 코드 예시에 넣지 않습니다.

---

## 11. 막히면

- 먼저 [README 11장](README.md#11-현재-상태와-알려진-제한)과 [docs/DEPLOYMENT.md 9장(문제 해결)](docs/DEPLOYMENT.md)을 확인합니다.
- 재현 절차와 함께 GitHub Issue를 열거나, 저장소 소유자(GitHub `jangkops`)에게 사내 채널로 문의합니다. 로그를 붙일 때는 토큰·키·계정 ID가 없는지 먼저 확인하세요.
