# Requirements Document

## Introduction

이 기능은 사내 배포용 AI 에디터(Electron + Vanilla JS 프론트, Python 3.11+ FastAPI 백엔드)의
챗 실행 경로를 정식 LangGraph 런타임 기반의 **계층적 오케스트레이션**으로 전환한다.
Top Supervisor(라우터)가 도메인별 서브그래프(coding / media / research / ops / chat)로
라우팅하는 supervisor-of-supervisors 구조를 이루고, 각 서브그래프는 컴파일된 Runnable로서
상위 그래프의 노드로 add되어 graph-of-graphs를 구성한다. 각 서브그래프는 내부에
`retrieve(RAG) → model → ToolNode → verify` 패턴을 갖는다. LLM 호출은 전부 Bedrock Gateway를
경유하며(직접 SDK 금지), RAG는 프롬프트 주입 방식에서 그래프 노드로 승격되고, 스트리밍은
LangGraph `astream_events`를 기존 SSE 이벤트 계약으로 매핑한다.

이 문서는 승인된 설계 문서(`design.md`)로부터 역도출된 요구사항이며, 설계에 이미 결정된 범위
안에서만 요구사항을 명문화한다. 설계의 8개 Correctness Properties는 아래 요구사항의 수용 기준과
연결되며, 각 요구사항 번호는 설계 문서에서 `Validates: Requirements X.Y` 형태로 역참조된다.

핵심 제약(steering 실측): 모든 LLM 호출은 Gateway 경유, SQLite 금지, 데이터는 `userData` 하위
영속, 무한대기 금지(타임아웃 + recursion_limit), 자격증명은 어떤 파일에도 미저장, 기존 자산 재사용,
단계적 마이그레이션 + 기존 경로 병행.

## Glossary

- **Orchestrator**: LangGraph 기반 계층적 오케스트레이션 시스템 전체.
- **Top_Supervisor**: 사용자 의도를 도메인 route로 분류하는 라우터 LLM 노드 및 상위 StateGraph.
- **Domain_Subgraph**: coding / media / research / ops / chat 각 도메인의 컴파일된 서브그래프(Runnable). 상위 그래프의 노드로 add된다.
- **Gateway_Chat_Model**: Bedrock Gateway를 경유하는 LangChain `BaseChatModel` 구현체(`GatewayChatModel`). `bind_tools` / `_generate` / `_agenerate` / `_astream` 제공.
- **Tool_Node**: LangGraph `ToolNode` 역할을 하는 `GatewayToolNode`. 마지막 AIMessage의 tool_calls를 실행하고 ToolMessage를 반환.
- **Retrieve_Node**: RAG 근거를 조회해 상태의 evidence에 적재하는 노드.
- **Verify_Node**: citation 검증 + verified_files 디스크 검증 + answer_quality + 강제 생성 폴백을 수행하는 노드.
- **Checkpoint_Saver**: LangGraph `BaseCheckpointSaver`를 구현한 JSON 파일 기반 체크포인터(`JsonFileCheckpointSaver`).
- **SSE_Bridge**: `astream_events`를 기존 SSE 이벤트 계약으로 변환하는 매퍼(`graph_events_to_sse`).
- **Graph_Endpoint**: `/api/agents/graph-stream` FastAPI 라우트.
- **Gateway_Client**: 기존 `gateway_module.GatewayClient`. SigV4/assume-role로 Bedrock Gateway 호출.
- **UserData_Dir**: Electron `app.getPath('userData')`가 가리키는 데이터 영속 루트.
- **Route**: Top_Supervisor가 결정하는 도메인 라벨(`coding` | `media` | `research` | `ops` | `chat` | `done`).
- **Feature_Flag**: 신규 그래프 경로 노출 여부를 제어하는 환경변수 `AE_LANGGRAPH`.

## Requirements

### Requirement 1: 계층 그래프 구성 및 라우팅

**User Story:** As a 사내 AI 에디터 사용자, I want 챗이 내 요청을 도메인별 전문 서브그래프로 자동 라우팅하는 계층적 오케스트레이션(supervisor-of-supervisors, graph-of-graphs)으로 동작하기를, so that 코드/미디어/리서치/운영/대화 작업이 각각 적합한 도구 집합과 모델로 처리된다.

#### Acceptance Criteria

1. WHEN Graph_Endpoint가 그래프를 조립하기 위해 build_top_graph가 호출되면, THE Orchestrator SHALL 각 Domain_Subgraph를 컴파일된 Runnable로서 Top_Supervisor 그래프의 노드로 add하여 graph-of-graphs 구조를 구성한다.
2. WHEN 사용자 프롬프트가 Top_Supervisor에 전달되면, THE Top_Supervisor SHALL 프롬프트와 컨텍스트를 기반으로 Route를 `coding`, `media`, `research`, `ops`, `chat`, `done` 중 하나로 분류한다.
3. WHEN Top_Supervisor가 Route를 결정하면, THE Orchestrator SHALL conditional edge를 통해 결정된 Route에 대응하는 Domain_Subgraph 노드로 실행을 전달한다.
4. WHEN Route가 `done`으로 결정되면, THE Orchestrator SHALL 그래프 실행을 END로 종료한다.
5. WHEN 하나의 Domain_Subgraph 실행이 종료되면, THE Orchestrator SHALL Top_Supervisor로 복귀하여 멀티 도메인 작업의 재라우팅을 수행한다.
6. THE Orchestrator SHALL Route별로 정의된 도구 집합(coding: read_file/write_file/search_files/run_command, media: generate_pptx/generate_pdf/generate_image/generate_docx/generate_xlsx/edit_image/generate_native_diagram, ops: run_command/git/브리지 라우팅, research: read_file, chat: 없음)을 해당 Domain_Subgraph에 바인딩한다.

### Requirement 2: Gateway BaseChatModel 어댑터 및 tool 왕복

**User Story:** As a 백엔드 개발자, I want LangGraph의 `bind_tools` / `ToolNode` / `astream_events`와 정합하는 정식 `BaseChatModel` 어댑터가 Bedrock Gateway를 경유하기를, so that 직접 SDK 없이 표준 LangGraph 도구 호출 루프를 구성할 수 있다.

#### Acceptance Criteria

1. THE Gateway_Chat_Model SHALL LangChain `BaseChatModel`을 상속하여 `_generate`, `_agenerate`, `_astream`, `bind_tools`를 제공한다.
2. WHEN Gateway_Chat_Model이 LLM을 호출하면, THE Gateway_Chat_Model SHALL Gateway_Client의 converse / stream 메서드를 경유하여 호출한다.
3. WHEN Bedrock converse 출력 메시지가 `toolUse` 블록을 포함하면, THE Gateway_Chat_Model SHALL 각 `toolUse` 블록을 대응하는 LangChain `ToolCall`(id, name, args)로 변환하여 AIMessage.tool_calls에 채운다.
4. WHERE 반환된 AIMessage에 tool_calls가 존재하는 경우, THE Gateway_Chat_Model SHALL 생성된 각 ToolCall이 비어있지 않은 id와 name을 갖도록 보장한다.
5. WHEN `bind_tools`에 LangChain 도구 정의가 전달되면, THE Gateway_Chat_Model SHALL 각 도구를 Bedrock `toolSpec`(name/description/inputSchema.json) 형식으로 변환하여 매 호출 시 toolConfig로 Gateway_Client에 전달한다.
6. WHEN LangChain 메시지 목록이 Bedrock converse 형식으로 변환되면, THE Gateway_Chat_Model SHALL SystemMessage를 system 텍스트로 병합하고 AIMessage.tool_calls는 `toolUse`로, ToolMessage는 `toolResult`로 매핑하며 user/assistant 교대 규칙을 준수한다.
7. IF Gateway_Client 응답의 decision이 `ERROR` 또는 `DENY`이거나 error 필드가 존재하면, THEN THE Gateway_Chat_Model SHALL `GatewayModelError`를 발생시켜 그래프로 전파한다.

### Requirement 3: RAG retrieve/verify 노드

**User Story:** As a 사용자, I want RAG 근거 조회와 산출물/citation 검증이 프롬프트 주입이 아닌 명시적 그래프 노드로 수행되기를, so that 근거 기반 응답과 실측된 산출물 검증이 실행 흐름의 일부로 보장된다.

#### Acceptance Criteria

1. WHEN Retrieve_Node가 실행되고 project_path가 존재하며 Route가 `chat`이 아니면, THE Retrieve_Node SHALL 기존 context_builder / ProjectIndexer / FastEmbedProvider / VectorStore를 재사용하여 근거를 조회하고 상태의 evidence(context, chunks)에 적재한다.
2. IF Retrieve_Node의 검색이 불가능하거나 Route가 `chat`이면, THEN THE Retrieve_Node SHALL evidence를 None으로 설정하고 기존 system_prompt를 유지한다.
3. WHEN Verify_Node가 실행되면, THE Verify_Node SHALL 마지막 AIMessage 텍스트를 final_text로 확정한다.
4. WHEN evidence에 chunks가 존재하면, THE Verify_Node SHALL parse_citations와 verify_citations를 사용하여 citation을 verified와 unverified로 분류한 결과를 citations에 기록한다.
5. IF citation 중 unverified 항목이 존재하면, THEN THE Verify_Node SHALL 답변을 차단하지 않고 표기만 수행한다.
6. IF 파일 생성 의도가 있었으나 verified_files가 비어 있으면, THEN THE Verify_Node SHALL 기존 `_force_generate_from_text` 강제 생성 폴백을 호출하고 결과를 verified_files에 병합한다.
7. WHEN 파일 생성 도구가 산출물을 반환하면, THE Tool_Node SHALL 산출물 절대 경로에 대해 디스크 실측(파일 존재 및 크기 > 0)을 수행하고 통과한 항목만 verified_files에 추가한다.
8. THE Orchestrator SHALL final_state의 모든 verified_files 항목이 디스크에 실재하는(파일 존재 및 크기 > 0) 산출물만 포함하도록 보장한다.

### Requirement 4: JSON 체크포인터 및 연속성

**User Story:** As a 시스템 운영자, I want 그래프 상태가 SQLite 없이 JSON 파일 체크포인터로 `userData` 하위에 영속되기를, so that 스택 제약(SQLite 금지, userData 영속)을 준수하면서 thread 기반 대화 연속성과 재개가 가능하다.

#### Acceptance Criteria

1. THE Checkpoint_Saver SHALL LangGraph `BaseCheckpointSaver` 인터페이스(put, put_writes, get_tuple, list 및 async 대응)를 구현한다.
2. THE Checkpoint_Saver SHALL 모든 체크포인트를 `.json` 확장자를 갖는 파일로만 저장하고 SQLite를 사용하지 않는다.
3. THE Checkpoint_Saver SHALL 체크포인트 저장 위치를 UserData_Dir 하위(`checkpoints/langgraph/`)로 한정한다.
4. WHEN 동일한 thread_id로 체크포인트를 저장한 뒤 조회하면, THE Checkpoint_Saver SHALL 저장된 체크포인트를 CheckpointTuple로 복원하여 반환한다.
5. IF 존재하지 않는 thread_id로 조회가 요청되면, THEN THE Checkpoint_Saver SHALL None을 반환한다.
6. WHEN 그래프가 컴파일되면, THE Orchestrator SHALL Checkpoint_Saver를 checkpointer로 바인딩하여 thread_id 기반으로 실행 상태를 영속한다.

### Requirement 5: SSE 이벤트 계약 호환

**User Story:** As a 프론트엔드 개발자, I want 신규 그래프 경로의 스트리밍이 기존 프론트가 소비하는 SSE 이벤트 계약을 그대로 재현하기를, so that 프론트 코드 변경 없이 무회귀로 신규 경로를 사용할 수 있다.

#### Acceptance Criteria

1. WHEN SSE_Bridge가 `astream_events`를 소비하면, THE SSE_Bridge SHALL `on_chat_model_stream`의 AIMessageChunk content를 `{text}` 이벤트로 변환하여 emit한다.
2. WHEN Tool_Node 실행이 시작 및 종료되면, THE SSE_Bridge SHALL `on_tool_start` / `on_tool_end` 이벤트를 `{tool, status: 'running'|'done', ...}` 이벤트로 변환하여 emit한다.
3. WHEN Domain_Subgraph가 진입 및 종료되면, THE SSE_Bridge SHALL `{type: 'agent_start', taskId}` / `{type: 'agent_done', taskId}` 이벤트를 emit한다.
4. WHEN verify 또는 Tool_Node가 verified_files를 갱신하면, THE SSE_Bridge SHALL 디스크 실측된 path만 `{verifiedFiles}` 이벤트로 emit한다.
5. THE SSE_Bridge SHALL emit하는 이벤트 키를 `{text, thinking, tool, status, verifiedFiles, type, taskId, heartbeat, answerQuality, qualityPending, error}` 집합의 부분집합으로 한정한다.
6. WHEN 그래프 스트림이 종료되면, THE SSE_Bridge SHALL 마지막에 `data: [DONE]` 종료 이벤트를 emit한다.
7. IF 노드 실행 중 예외 또는 `GatewayModelError`가 발생하면, THEN THE SSE_Bridge SHALL `{error}` 이벤트를 emit한 뒤 `[DONE]`으로 종료한다.

### Requirement 6: 무한대기 차단 (타임아웃 및 recursion 제한)

**User Story:** As a 시스템 운영자, I want 모든 노드에 타임아웃이 적용되고 그래프에 recursion 제한이 강제되기를, so that 과거 10시간 hang 이력이 재발하지 않고 그래프가 항상 유한 시간에 종료된다.

#### Acceptance Criteria

1. WHEN model 노드가 실행되면, THE Orchestrator SHALL 호출을 `MODEL_NODE_TIMEOUT`(기본 300초, `AE_MODEL_NODE_TIMEOUT`)으로 감싸고, 초과 시 timeout 메시지와 함께 verify로 진행한다.
2. WHEN Tool_Node가 도구를 1회 실행하면, THE Tool_Node SHALL 실행을 `TOOL_NODE_TIMEOUT`(기본 120초, `AE_TOOL_NODE_TIMEOUT`)으로 감싸고, 초과 시 시간 초과 ToolMessage를 반환한다.
3. WHEN Retrieve_Node가 실행되면, THE Retrieve_Node SHALL 검색을 `RETRIEVE_NODE_TIMEOUT`(기본 30초, `AE_RETRIEVE_TIMEOUT`)으로 감싸고, 초과 시 evidence를 None으로 설정하여 비차단 진행한다.
4. IF 서브그래프의 model↔tool 왕복 iteration이 `SUBGRAPH_RECURSION_LIMIT`(기본 25, `AE_SUBGRAPH_RECURSION`)에 도달하면, THEN THE Orchestrator SHALL 강제로 verify 노드로 라우팅하여 도구 루프를 종료한다.
5. IF Top_Supervisor의 visited_routes가 `MAX_ROUTE_HOPS`(기본 4, `AE_MAX_ROUTE_HOPS`)에 도달하면, THEN THE Top_Supervisor SHALL Route를 `done`으로 설정하여 재라우팅 순환을 종료한다.
6. WHEN 그래프가 실행되면, THE Orchestrator SHALL `recursion_limit`(기본 50, `AE_GRAPH_RECURSION`)을 config에 설정하고 전체 실행을 `GRAPH_TOTAL_TIMEOUT`(기본 1800초, `AE_GRAPH_TOTAL_TIMEOUT`)으로 감싼다.
7. THE Orchestrator SHALL 그래프 실행 총 소요 시간이 `GRAPH_TOTAL_TIMEOUT` 이하, 재라우팅 hop 수가 `MAX_ROUTE_HOPS` 이하, 서브그래프 model↔tool 왕복 수가 `SUBGRAPH_RECURSION_LIMIT` 이하가 되도록 보장하여 유한 시간 종료를 보장한다.
8. WHILE SSE 스트림이 활성인 동안, THE SSE_Bridge SHALL `HEARTBEAT_INTERVAL`(기본 20초, `AE_HEARTBEAT_INTERVAL`)마다 `{heartbeat}` 이벤트를 emit하여 Lambda 5분 무응답 끊김을 방지한다.

### Requirement 7: 단계적 마이그레이션 및 기존 경로 병행

**User Story:** As a 백엔드 개발자, I want 신규 그래프 경로가 기존 실행 경로와 병행하며 feature flag로 제어되고 실패 시 자동 fallback되기를, so that 실측 안정화 전까지 무회귀로 점진 전환할 수 있다.

#### Acceptance Criteria

1. THE Orchestrator SHALL 신규 라우트 `/api/agents/graph-stream`(Graph_Endpoint)을 기존 실행 경로(`run_agent_stream` / `run_agent_orchestrated`)와 병행하여 제공한다.
2. WHERE Feature_Flag(`AE_LANGGRAPH`)가 활성이면, THE Orchestrator SHALL 신규 Graph_Endpoint를 통해 요청을 처리한다.
3. WHERE Feature_Flag(`AE_LANGGRAPH`)가 비활성이면, THE Orchestrator SHALL 기존 실행 경로를 통해 요청을 처리한다.
4. IF 신규 Graph_Endpoint 처리가 실패하면, THEN THE Orchestrator SHALL 기존 실행 경로로 자동 fallback한다.
5. THE Orchestrator SHALL 기존 자산(verified_files 디스크 검증, 강제 생성 폴백, 원격 SSH 브리지 tool routing, 합의 교차검증, ConversationMemory 요약 체크포인트)을 재구현하지 않고 노드에서 재사용한다.

### Requirement 8: 보안 — 자격증명 미저장

**User Story:** As a 보안 담당자, I want AWS 자격증명이 그래프 상태나 체크포인트를 포함한 어떤 파일에도 저장되지 않기를, so that steering 보안 정책(자격증명 미저장, 런타임 주입/assume-role)을 위반하지 않는다.

#### Acceptance Criteria

1. THE Orchestrator SHALL 그래프 상태에 AWS 자격증명(accessKeyId, secretAccessKey)을 포함하지 않고 profile name과 bedrock_user 문자열만 전달한다.
2. WHEN 그래프 상태가 직렬화되면, THE Orchestrator SHALL 직렬화 결과에 `accessKeyId`와 `secretAccessKey`가 포함되지 않도록 보장한다.
3. WHEN 체크포인트 파일이 기록되면, THE Checkpoint_Saver SHALL 기록된 어떤 체크포인트 파일에도 `accessKeyId`와 `secretAccessKey`가 포함되지 않도록 보장한다.
4. WHEN LLM 또는 도구 호출을 위해 자격증명이 필요하면, THE Gateway_Client SHALL 자격증명을 파일에서 읽지 않고 런타임 assume-role / 주입으로 획득한다.

### Requirement 9: 번들 및 배포 검증

**User Story:** As a 릴리스 엔지니어, I want LangGraph/LangChain 신규 서브모듈이 PyInstaller 번들에 포함되고 import smoke test로 검증되기를, so that 배포된 앱에서 신규 그래프 경로가 누락 모듈 없이 동작한다.

#### Acceptance Criteria

1. THE Orchestrator SHALL PyInstaller spec(`ai-engine-server.spec`)에 `langgraph`, `langchain_core`, `fastembed`, `onnxruntime`, `tokenizers`, `huggingface_hub`가 수집 대상으로 포함되도록 유지한다.
2. WHEN 번들이 빌드되면, THE Orchestrator SHALL 신규 서브모듈(`langgraph.checkpoint.base`, `langgraph.checkpoint.serde.jsonplus`, `langgraph.prebuilt`, `langchain_core.language_models`)이 import 가능한지 smoke test로 검증한다.
3. IF Phase 5 정리가 수행되면, THEN THE Orchestrator SHALL dead code(`agent_graph.py`의 수동 while 루프, 구버전 `run_workflow`)를 제거하고 회귀 테스트로 기존 경로 무회귀를 검증한다.
