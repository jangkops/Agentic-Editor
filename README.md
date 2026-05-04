# Agentic Editor

> Multi-model AI code editor with parallel inference, consensus engine, and project-aware RAG — powered by AWS Bedrock Gateway.

---

## Overview

Agentic Editor는 AWS Bedrock Gateway를 통해 70+ LLM 모델을 단일/병렬로 호출하고, 합의를 도출하며, 프로젝트 코드를 인식하는 데스크톱 코드 에디터입니다.

**핵심 차별점:**
- 병렬 호출로 여러 모델의 답변을 동시에 비교
- 고차원 모델(Opus)이 자동으로 합의를 도출 → 합의 모델로 이어서 대화 가능
- 에이전트 모드: LLM이 파일 읽기/쓰기, 명령 실행, 검색 도구를 자율적으로 사용
- 하이브리드 RAG(TF-IDF 벡터 + BM25)로 프로젝트 코드를 인식한 답변
- 대화 요약 체크포인트로 장기 대화 맥락 유지
- 스트리밍 fast-path로 깜빡임 없는 실시간 응답
- AWS SSO + BedrockUser assume role 기반 사용자별 인증/과금

---

## Architecture

```
┌─────────────────────────────────────────────────────────┐
│                    Electron (Frontend)                   │
│  ┌──────────┐  ┌──────────────┐  ┌───────────────────┐  │
│  │ File     │  │ Monaco       │  │ AI Chat Panel     │  │
│  │ Explorer │  │ Editor       │  │ (Single/Parallel) │  │
│  │          │  │              │  │                   │  │
│  │ Git +    │  │ Stats/Search │  │ Consensus Engine  │  │
│  │ Branch   │  │ AI Review    │  │ Agent Workflow    │  │
│  │ Terminal │  │ Structure    │  │ Live Monitor      │  │
│  └──────────┘  └──────────────┘  └───────────────────┘  │
└────────────────────────┬────────────────────────────────┘
                         │ HTTP + SSE (localhost:8765)
┌────────────────────────▼────────────────────────────────┐
│                 FastAPI Backend (Python)                  │
│  ┌─────────────┐  ┌──────────────┐  ┌────────────────┐  │
│  │ Gateway     │  │ RAG Engine   │  │ Conversation   │  │
│  │ Client      │  │ (Hybrid)     │  │ Memory         │  │
│  │ (SigV4 +   │  │ TF-IDF Vec + │  │ (Summary       │  │
│  │  httpx SSE) │  │ BM25 Keyword │  │  Checkpoint)   │  │
│  └──────┬──────┘  └──────────────┘  └────────────────┘  │
│         │         ┌──────────────┐                       │
│         │         │ Agent Tools  │                       │
│         │         │ read/write/  │                       │
│         │         │ run/search/  │                       │
│         │         │ list_dir     │                       │
│         │         └──────────────┘                       │
└─────────┼───────────────────────────────────────────────┘
          │ SigV4 Signed HTTPS + Lambda Function URL (SSE)
┌─────────▼───────────────────────────────────────────────┐
│              AWS Bedrock Gateway (API Gateway)            │
│  ┌─────────────────────────────────────────────────────┐ │
│  │ /converse → Lambda → Bedrock Runtime                │ │
│  │ Lambda Function URL → SSE 실시간 스트리밍            │ │
│  │ Rate limit, Quota, Cost tracking per BedrockUser    │ │
│  └─────────────────────────────────────────────────────┘ │
└─────────────────────────────────────────────────────────┘
```

---

## Features

### Editor
- Monaco Editor (VS Code 엔진) — 구문 강조, 자동완성
- 파일 탐색기 — 인라인 생성/수정/삭제, 컬러 아이콘
- 프로젝트 전체 코드 검색 — 결과 클릭 시 에디터 라인 이동 + 강조
- 파일 저장 (Cmd+S), 수정 표시 (●)
- 다크/라이트 테마, 글자 크기 조절

### AI Chat
- **에이전트 모드** — LLM이 도구(파일 읽기/쓰기, 명령 실행, 검색)를 자율적으로 사용하여 작업 수행. 최대 10턴 도구 루프.
- **단일 호출** — 모든 호출이 에이전트 모드로 통일 (도구 사용 가능)
- **병렬 호출** — 여러 모델 동시 호출, 결과 비교 (가운데 패널에 카드 표시)
- **합의 도출** — 고차원 모델이 여러 응답을 분석하여 최종 합의. 사용자 원래 질문 + 대화 맥락 + 열린 파일 + 활성 스킬을 모두 반영.
- **합의 모델 이어가기** — 합의 카드 하단 "이 모델로 계속 대화" 버튼으로 단일 모드 자동 전환
- **대화 히스토리** — 세션별 메시지 격리, 모드 전환 시 맥락 유지, 병렬 합본은 히스토리에만 포함 (UI 숨김)
- **스트리밍 fast-path** — 토큰마다 전체 DOM 재렌더 대신 in-place 갱신 (깜빡임 제거)
- **질문 고정** — 질문 전송 시 뷰포트 상단에 고정, 답변이 아래로 누적
- Copy/재생성 버튼 (SVG 아이콘), 소요 시간 표시

### Agent Tools (서버 측)
에이전트 모드에서 LLM이 자율적으로 호출하는 5개 도구:

| 도구 | 설명 |
|------|------|
| `read_file` | 파일 내용 읽기 (최대 30,000자) |
| `write_file` | 파일 생성/덮어쓰기 |
| `list_directory` | 디렉토리 파일/폴더 목록 (숨김 파일 제외) |
| `run_command` | 셸 명령어 실행 (30초 타임아웃) |
| `search_files` | 프로젝트 내 텍스트 검색 (grep, 최대 50줄) |

### RAG (Retrieval-Augmented Generation)

프로젝트 코드를 인식한 답변을 위한 하이브리드 검색 파이프라인:

```
프로젝트 파일 → 스마트 청킹 → TF-IDF 벡터화 + BM25 인덱싱
                                    ↓
질문 → 하이브리드 검색 (벡터 60% + BM25 40%) → 상위 8개 청크 → 시스템 프롬프트 주입
```

**인덱싱 (ProjectIndexer)**
- 프로젝트 파일 자동 탐색 (node_modules, .git 등 제외)
- 함수/클래스 경계 기반 스마트 청킹 (Python: `def`/`class`, JS: `function`/`const`/`export`)
- 경계 감지 실패 시 60줄 고정 청크 + 10줄 오버랩
- 500KB 초과 파일 자동 스킵
- 파일 해시 기반 변경 감지 (재인덱싱 최소화)

**벡터 저장소 (VectorStore)**
- scikit-learn TF-IDF Vectorizer (max_features=1024, sublinear_tf)
- numpy 기반 로컬 벡터 저장소 (코사인 유사도 검색)
- `.npy` + `.meta.json` 파일로 디스크 영속화
- 외부 API 호출 없음 — BedrockUser 권한 제약 우회

**하이브리드 검색 (HybridSearcher)**
- BM25 (k1=1.5, b=0.75) 키워드 검색
- TF-IDF 벡터 코사인 유사도 검색
- 가중 결합: `score = 0.6 × vector + 0.4 × BM25`
- 벡터 저장소 미사용 시 BM25 단독 폴백
- 코드 관련 질문에만 RAG 적용 (일반 질문은 스킵)

**컨텍스트 빌더 (build_system_prompt)**
- 현재 열린 파일 + 프로젝트 경로 자동 인식
- 검색 결과를 시스템 프롬프트에 주입
- 파일 트리 (최대 200줄) 포함

### Conversation Memory (대화 메모리)

장기 대화에서 토큰 한도 내 최대 맥락 유지:

```
[요약 체크포인트] + [최근 10개 원본 메시지] + [현재 질문]
```

- **요약 트리거**: 12개 이상 메시지 누적 시 자동 요약 (Haiku 모델 사용 — 빠르고 저렴)
- **체크포인트 저장**: 세션별 JSON 파일 (`conv_{session_id}.json`)
- **핵심 사실 추출**: 요약에서 `-` 항목을 key_facts로 분리 저장
- **Bedrock 규칙 준수**: user/assistant 교대 규칙 자동 정리
- **개별 메시지 제한**: 2,000자 (토큰 폭발 방지)

### Source Control
- Git Graph — 커밋 히스토리, diff 뷰
- **브랜치 드롭다운** — 소스 제어 패널에서 브랜치 선택/전환 (모델 드롭다운과 동일 UI)
- Dirty 체크 — 커밋 안 된 변경 있으면 확인 다이얼로그
- 원격 브랜치 자동 tracking 생성

### Analytics
- 통계 뷰 — 개요, 품질·생산성, 토큰 비용, 기여자, 팀 통계, 종합 인사이트
- AI 코드 리뷰 — 정적 분석 (eval, 하드코딩 자격증명, 빈 catch 등)
- 의존성 분석 — Production/Dev/Python 패키지
- 실시간 모니터 — 요청 로그, 비용, 토큰, 백엔드 상태

### Infrastructure
- AWS SSO 인증 + BedrockUser assume role
- 월간 사용량/한도 게이지
- SSO 세션 만료 게이지
- 스킬 관리 (영속성, GitHub MD import)
- 대화 세션 영속성 (로컬 저장)
- 병렬/합의 결과 로컬 저장 (30일)
- SSE idle timeout (60초 클라이언트, 120초 서버) — 스트림 끊김 자동 감지

---

## Tech Stack

| Layer | Technology |
|-------|-----------|
| Frontend | Electron + Vanilla JS + HTML + CSS |
| Editor | Monaco Editor (CDN) |
| Backend | Python 3.11+ / FastAPI / Uvicorn |
| LLM Gateway | AWS Bedrock Gateway (SigV4) + Lambda Function URL (SSE) |
| RAG Indexing | scikit-learn TF-IDF + BM25 (로컬, API 호출 없음) |
| Vector Store | numpy 코사인 유사도 (.npy + .meta.json 영속화) |
| Conversation Memory | 요약 체크포인트 (Haiku) + 최근 10턴 원본 |
| Auth | AWS SSO + BedrockUser IAM Role |
| Storage | Electron userData (JSON) + localStorage |
| HTTP Client | httpx (비동기 SSE 스트리밍) |

---

## Quick Start

### Prerequisites
- Node.js 18+
- Python 3.11+
- AWS CLI v2 + SSO configured
- AWS account with Bedrock Gateway access

### Installation

```bash
git clone https://github.com/jangkops/Agentic-Editor.git
cd Agentic-Editor
npm install
python3 -m venv ai_engine/.venv
source ai_engine/.venv/bin/activate
pip install -r ai_engine/requirements.txt
```

### AWS SSO Setup

```bash
# ~/.aws/config에 프로파일 추가
cat >> ~/.aws/config << 'EOF'
[profile bedrock-gw]
sso_start_url = https://d-906617189d.awsapps.com/start
sso_region = us-east-1
sso_account_id = 107650139384
sso_role_name = AdministratorAccess
region = us-west-2
EOF

# SSO 로그인
aws sso login --profile bedrock-gw
```

### Run

```bash
npm run dev
```

에디터가 실행되면:
1. SSO 로그인 다이얼로그에서 프로파일 선택 + 로그인
2. BedrockUser 이름 입력 (예: `cgjang`)
3. 모델 목록 자동 로드 → 채팅 시작

### Development Notes

- `npm run dev`는 `concurrently`로 Python 서버 + Electron을 동시 실행
- 서버는 `--reload --reload-dir ai_engine`으로 실행 — `ai_engine/*.py` 변경 시에만 리로드
- `src/`, `electron/` 등 프론트엔드 파일 수정은 서버에 영향 없음 (Cmd+R 새로고침만 필요)
- `NO_RELOAD=1 npm run dev`로 서버 리로드 완전 비활성화 가능

---

## Project Structure

```
agentic-editor/
├── electron/                  # Electron main process
│   ├── main.js               # Window, IPC handlers (git:branches/checkout/status 포함)
│   ├── preload.js            # Context bridge API
│   └── core/
│       ├── aws-sso-manager.js # SSO login, credentials
│       ├── data-store.js      # Settings, history, skills
│       ├── process-manager.js # Python backend lifecycle
│       └── pty-worker.js      # PTY terminal worker
├── src/                       # Renderer (frontend)
│   ├── index.html            # Main layout (3-pane CSS Grid)
│   ├── main.js               # App logic (~3600 lines)
│   ├── center-views.js       # Stats, search, git graph, review, structure
│   └── styles/
│       ├── variables.css     # Design tokens (dark/light)
│       ├── layout.css        # Grid layout, panels, branch dropdown
│       └── components.css    # UI components, tool cards, animations
├── ai_engine/                 # Python backend
│   ├── server.py             # FastAPI endpoints (SSE streaming)
│   ├── gateway_module.py     # Bedrock Gateway client (SigV4 + httpx SSE)
│   ├── rag/
│   │   ├── indexer.py        # ProjectIndexer — 스마트 청킹, TF-IDF 검색
│   │   ├── embedder.py       # TF-IDF Vectorizer + numpy VectorStore
│   │   ├── hybrid_search.py  # HybridSearcher — 벡터(60%) + BM25(40%)
│   │   ├── context_builder.py # RAG 컨텍스트 → 시스템 프롬프트 주입
│   │   └── conversation_memory.py # 요약 체크포인트 기반 장기 기억
│   ├── agent_system/          # (확장용) Multi-agent workflow
│   │   ├── agent_graph.py
│   │   ├── state.py
│   │   ├── chat_model_adapter.py
│   │   ├── tool_registry.py
│   │   └── checkpoint_store.py
│   └── requirements.txt
├── scripts/
│   ├── start_server.py       # Uvicorn 시작 (NO_RELOAD 지원)
│   ├── setup-venv.js         # Python venv 자동 설정
│   └── build-python.js       # PyInstaller 빌드
├── tests/
│   ├── e2e/                  # Playwright E2E 테스트
│   └── unit/                 # 유닛 테스트
├── package.json
├── electron-builder.yml
└── README.md
```

---

## API Endpoints

| Method | Path | Description |
|--------|------|-------------|
| GET/HEAD | `/health` | Backend health check |
| GET/POST | `/api/models` | Available model list (POST로 자격증명 직접 전달 가능) |
| POST | `/api/agents/run-stream` | Single model SSE streaming (도구 없음) |
| POST | `/api/agents/run-agent` | **에이전트 모드** SSE streaming (도구 실행 루프, 최대 10턴) |
| POST | `/api/agents/run-parallel` | Parallel model SSE streaming |
| GET | `/api/quota` | Monthly usage/quota (BedrockUser별) |
| POST | `/api/reset-cache` | Clear caches + inject credentials |
| POST | `/api/rag/index` | Trigger project indexing |
| GET | `/api/rag/status` | RAG index status |

### SSE Event Types (run-agent)

| Event | Format | Description |
|-------|--------|-------------|
| text delta | `{"text": "..."}` | LLM 응답 텍스트 조각 |
| tool start | `{"tool": "read_file", "input": {...}, "status": "running"}` | 도구 실행 시작 |
| tool done | `{"tool": "read_file", "output": "...", "status": "done"}` | 도구 실행 완료 |
| error | `{"error": "..."}` | 에러 메시지 |
| stream end | `[DONE]` | 스트림 종료 (finally 블록에서 보장) |

---

## Keyboard Shortcuts

| Shortcut | Action |
|----------|--------|
| `Cmd+S` | Save current file |
| `Cmd+Shift+F` | Project search |
| `Cmd+Shift+G` | Git view |
| `Cmd+Shift+S` | Stats view |
| `Esc` | Return to editor |

---

## Configuration

### Settings (userData/settings/settings.json)
```json
{
  "awsProfile": "bedrock-gw",
  "bedrockUser": "cgjang"
}
```

### Gateway (environment variables)
```bash
GATEWAY_URL=https://5l764dh7y9.execute-api.us-west-2.amazonaws.com/v1
AWS_REGION=us-west-2
NO_RELOAD=1  # 서버 auto-reload 비활성화 (선택)
```

---

## License

Internal use only.
