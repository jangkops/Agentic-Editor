# Agentic Editor

> Multi-model AI code editor with parallel inference, consensus engine, and project-aware RAG — powered by AWS Bedrock Gateway.

<!-- TODO: 실제 에디터 캡처 이미지 추가 -->
<!-- ![Agentic Editor Screenshot](docs/screenshot.png) -->

---

## Overview

Agentic Editor는 AWS Bedrock Gateway를 통해 70+ LLM 모델을 단일/병렬로 호출하고, 합의를 도출하며, 프로젝트 코드를 인식하는 데스크톱 코드 에디터입니다.

**핵심 차별점:**
- 병렬 호출로 여러 모델의 답변을 동시에 비교
- 고차원 모델(Opus)이 자동으로 합의를 도출
- 하이브리드 RAG(벡터 + BM25)로 프로젝트 코드를 인식한 답변
- 대화 요약 체크포인트로 장기 대화 맥락 유지
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
│  │ Git      │  │ Stats/Search │  │ Consensus Engine  │  │
│  │ Terminal │  │ AI Review    │  │ Live Monitor      │  │
│  └──────────┘  └──────────────┘  └───────────────────┘  │
└────────────────────────┬────────────────────────────────┘
                         │ HTTP (localhost:8765)
┌────────────────────────▼────────────────────────────────┐
│                 FastAPI Backend (Python)                  │
│  ┌─────────────┐  ┌──────────────┐  ┌────────────────┐  │
│  │ Gateway     │  │ RAG Engine   │  │ Conversation   │  │
│  │ Client      │  │ (Hybrid)     │  │ Memory         │  │
│  │ (SigV4)     │  │ Vector+BM25  │  │ (Checkpoint)   │  │
│  └──────┬──────┘  └──────────────┘  └────────────────┘  │
└─────────┼───────────────────────────────────────────────┘
          │ SigV4 Signed HTTPS
┌─────────▼───────────────────────────────────────────────┐
│              AWS Bedrock Gateway (API Gateway)            │
│  ┌─────────────────────────────────────────────────────┐ │
│  │ /converse → Lambda → Bedrock Runtime                │ │
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
- **단일 호출** — 간단한 질문은 워크플로우 없이 바로 응답
- **병렬 호출** — 여러 모델 동시 호출, 결과 비교
- **합의 도출** — 고차원 모델이 여러 응답을 분석하여 최종 합의
- **대화 히스토리** — 이전 대화 맥락 유지, 요약 체크포인트
- Copy/Run Command 버튼 (SVG 아이콘)
- 소요 시간 표시, 비동기 모델 표시

### RAG (Retrieval-Augmented Generation)
- 프로젝트 파일 자동 인덱싱 (함수/클래스 경계 스마트 청킹)
- 하이브리드 검색: 벡터 유사도(60%) + BM25 키워드(40%)
- 코드 관련 질문에만 RAG 적용 (일반 질문은 스킵)
- 현재 열린 파일 + 프로젝트 경로 자동 인식

### Analytics
- 통계 뷰 — 개요, 품질·생산성, 토큰 비용, 기여자, 팀 통계, 종합 인사이트
- AI 코드 리뷰 — 정적 분석 (eval, 하드코딩 자격증명, 빈 catch 등)
- Git Graph — 커밋 히스토리, diff 뷰
- 의존성 분석 — Production/Dev/Python 패키지
- 실시간 모니터 — 요청 로그, 비용, 토큰, 백엔드 상태

### Infrastructure
- AWS SSO 인증 + BedrockUser assume role
- 월간 사용량/한도 게이지 (50만~500만 밴드 자동 감지)
- SSO 세션 만료 게이지
- 스킬 관리 (영속성, GitHub MD import)
- 대화 세션 영속성 (로컬 저장)
- 병렬/합의 결과 로컬 저장 (30일)

---

## Tech Stack

| Layer | Technology |
|-------|-----------|
| Frontend | Electron + Vanilla JS + HTML + CSS |
| Editor | Monaco Editor (CDN) |
| Backend | Python 3.11+ / FastAPI / Uvicorn |
| LLM | AWS Bedrock Gateway (SigV4) |
| RAG | TF-IDF + BM25 + Bedrock Titan Embed v2 |
| Auth | AWS SSO + BedrockUser IAM Role |
| Storage | Electron userData (JSON) + localStorage |

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

### IAM Setup (BedrockUser Trust Policy)

각 `BedrockUser-{username}` IAM role의 Trust Policy에 SSO 역할 허용을 추가해야 합니다:

```json
{
  "Effect": "Allow",
  "Principal": {
    "AWS": "arn:aws:iam::<ACCOUNT_ID>:root"
  },
  "Action": "sts:AssumeRole",
  "Condition": {
    "ArnLike": {
      "aws:PrincipalArn": "arn:aws:iam::<ACCOUNT_ID>:role/aws-reserved/sso.amazonaws.com/AWSReservedSSO_*"
    }
  }
}
```

이 설정은 SSO Permission Set을 통해 로그인한 사용자만 BedrockUser role을 assume할 수 있도록 합니다. SSO가 아닌 역할(EC2, Lambda 등)은 이 조건에 해당하지 않아 assume 불가합니다.

```bash
# CLI로 적용 (관리자)
aws iam get-role --role-name BedrockUser-{username} \
  --query 'Role.AssumeRolePolicyDocument' --output json > trust.json

# trust.json에 위 Statement 추가 후:
aws iam update-assume-role-policy \
  --role-name BedrockUser-{username} \
  --policy-document file://trust.json
```

### Run

```bash
npm run dev
```

에디터가 실행되면:
1. SSO 로그인 다이얼로그에서 프로파일 선택 + 로그인
2. BedrockUser 이름 입력 (예: `cgjang`)
3. 모델 목록 자동 로드 → 채팅 시작

---

## Project Structure

```
agentic-editor/
├── electron/                  # Electron main process
│   ├── main.js               # Window, IPC handlers
│   ├── preload.js            # Context bridge API
│   └── core/
│       ├── aws-sso-manager.js # SSO login, credentials
│       ├── data-store.js      # Settings, history, skills
│       └── process-manager.js # Python backend, PTY terminal
├── src/                       # Renderer (frontend)
│   ├── index.html            # Main layout
│   ├── main.js               # App logic (~2500 lines)
│   ├── center-views.js       # Stats, search, git, review, structure
│   ├── styles/
│   │   ├── variables.css     # Design tokens (dark/light)
│   │   ├── layout.css        # Grid layout, panels
│   │   └── components.css    # UI components
│   └── components/           # Web Components (unused, legacy)
├── ai_engine/                 # Python backend
│   ├── server.py             # FastAPI endpoints
│   ├── gateway_module.py     # Bedrock Gateway client (SigV4)
│   ├── rag/
│   │   ├── indexer.py        # Project file indexer
│   │   ├── embedder.py       # Bedrock Titan embeddings
│   │   ├── hybrid_search.py  # Vector + BM25 hybrid
│   │   ├── context_builder.py # RAG context → system prompt
│   │   └── conversation_memory.py # Summary checkpoints
│   ├── agent_system/
│   │   ├── agent_graph.py    # Multi-agent workflow
│   │   ├── state.py          # Agent state
│   │   ├── chat_model_adapter.py
│   │   ├── tool_registry.py  # read/write/list/run/search
│   │   └── checkpoint_store.py
│   └── requirements.txt
├── scripts/                   # Build & setup scripts
├── tests/                     # E2E & unit tests
├── package.json
├── electron-builder.yml
└── README.md
```

---

## API Endpoints

| Method | Path | Description |
|--------|------|-------------|
| GET | `/health` | Backend health check |
| GET/POST | `/api/models` | Available model list |
| POST | `/api/agents/run-stream` | Single model SSE streaming |
| POST | `/api/agents/run-parallel` | Parallel model SSE streaming |
| POST | `/api/agents/run` | Synchronous single call |
| GET | `/api/quota` | Monthly usage/quota |
| POST | `/api/reset-cache` | Clear caches + inject credentials |
| POST | `/api/rag/index` | Trigger project indexing |
| GET | `/api/rag/status` | RAG index status |

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
```

---

## License

Internal use only.
