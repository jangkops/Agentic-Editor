# Implementation Plan: LangGraph Hierarchical Orchestrator

## Overview

design.md의 5단계 마이그레이션(Phase 1~5)을 따라 정식 LangGraph 런타임 기반 계층적
오케스트레이션을 점진적으로 도입한다. 각 단계는 이전 단계 위에 쌓이며, 기존 실행 경로
(`run_agent_stream` / `run_agent_orchestrated`)와 병행한다. 잘 동작하는 기존 자산
(verified_files 디스크 검증, `_force_generate_from_text`, `_call_bridge`, ConversationMemory,
context_builder/indexer/embedder/citation/answer_quality)은 **재구현하지 않고 노드에서 재사용**한다.
언어는 design.md에 명시된 Python 3.11+. LLM 호출은 전부 `GatewayClient` 경유(직접 SDK 금지).
모든 노드는 타임아웃, 그래프는 recursion 제한으로 유한 시간 종료를 보장한다.

## Tasks

- [x] 1. Phase 1 — 기반: Gateway 어댑터 + JSON 체크포인터 + 단일 coding 그래프 (feature flag)
  - [x] 1.1 공식 문서 대조 조사 (LangGraph / langchain_core 인터페이스 확정)
    - 웹 검색 / 공식 docs로 다음 시그니처를 확정하고 노트/주석으로 기록:
      - `langchain_core.language_models.BaseChatModel`의 `_generate`/`_agenerate`/`_astream`/`bind_tools` 시그니처
      - `langgraph.checkpoint.base.BaseCheckpointSaver`의 `put`/`put_writes`/`get_tuple`/`list` 및 async(`aput`/`aput_writes`/`aget_tuple`/`alist`)
      - `langgraph.prebuilt`의 `ToolNode` / `tools_condition` 계약
      - `langgraph.checkpoint.serde`(`JsonPlusSerializer`) 직렬화 규약
      - `astream_events` v2 이벤트 스키마(`on_chat_model_stream`/`on_tool_start`/`on_tool_end`/`on_chain_start`/`on_chain_end`)
    - _Requirements: 2.1, 4.1, 5.1_

  - [x] 1.2 GraphState + reducer 정의 (`agent_system/graph_state.py`)
    - `TypedDict` + `add_messages`, `verified_files` dedup reducer, `visited_routes`(operator.add)
    - 상태에 profile name / bedrock_user 문자열만 포함(자격증명 미포함)
    - _Requirements: 1.1, 8.1_

  - [x] 1.3 GatewayChatModel BaseChatModel 재구현 + 변환 헬퍼 (`agent_system/chat_model_adapter.py`)
    - `_generate`/`_agenerate`/`_astream`/`bind_tools` 구현, 기존 `GatewayClient` converse/stream_sse_realtime 재사용
    - `_lc_messages_to_bedrock` / `_bedrock_output_to_ai_message` / `_lc_tool_to_bedrock_toolspec` / `GatewayModelError`
    - _Requirements: 2.1, 2.2, 2.3, 2.5, 2.6, 2.7_

  - [ ]* 1.4 Property test: toolUse ↔ ToolCall 왕복 (hypothesis, Gateway mock)
    - **Property 2: toolUse ↔ ToolCall 왕복 보존**
    - **Validates: Requirements 2.3, 2.4**

  - [ ]* 1.5 Property test: LLM 호출은 항상 Gateway 경유 (직접 SDK import 부재)
    - **Property 1: LLM 호출은 항상 Gateway 경유**
    - **Validates: Requirements 2.2, 8.4**

  - [ ]* 1.6 단위 테스트: 메시지 변환 헬퍼 엣지 (이미지 첨부 / toolResult / user·assistant 교대)
    - _Requirements: 2.6_

  - [x] 1.7 JsonFileCheckpointSaver 구현 (`agent_system/checkpoint_store.py`)
    - `BaseCheckpointSaver` 상속, `put`/`put_writes`/`get_tuple`/`list` + async 위임
    - `.json` 파일만 저장, 저장 경로를 `userData/checkpoints/langgraph/`로 한정, SQLite 미사용
    - _Requirements: 4.1, 4.2, 4.3, 4.4, 4.5, 4.6, 8.3_

  - [ ]* 1.8 Property test: checkpointer는 JSON 파일만 사용 (hypothesis, fake fs)
    - **Property 5: checkpointer는 JSON 파일만 사용**
    - **Validates: Requirements 4.2, 4.3**

  - [ ]* 1.9 Property test: 자격증명은 state/checkpoint 어디에도 저장되지 않음
    - **Property 8: 자격증명 미저장**
    - **Validates: Requirements 8.2, 8.3**

  - [x] 1.10 GatewayToolNode 구현 (`agent_system/nodes/tool_node.py`) — 기존 자산 재사용
    - 마지막 AIMessage.tool_calls 실행 → ToolMessage, 도구별 `asyncio.wait_for(TOOL_NODE_TIMEOUT)`
    - server.py `_execute_tool`(로컬) / `_call_bridge`(원격) 재사용, `is_remote` 분기
    - verified_files 디스크 실측(`os.path.isfile` & size>0) 후 append
    - _Requirements: 3.7, 6.2, 7.5_

  - [ ]* 1.11 Property test: verified_files는 반드시 디스크에 실재 (hypothesis, fake fs)
    - **Property 3: verified_files는 반드시 디스크에 실재**
    - **Validates: Requirements 3.7, 3.8**

  - [x] 1.12 단일 coding 서브그래프 조립 (`agent_system/subgraphs/coding.py`) + model 노드 타임아웃
    - `retrieve→model→ToolNode→verify` (Phase 1은 retrieve/verify 스텁, model+tools 중심)
    - `make_model_node`에 `MODEL_NODE_TIMEOUT` 래핑, `tools_condition_or_verify`에 `SUBGRAPH_RECURSION_LIMIT` cap
    - `graph.compile()`으로 Runnable 반환, checkpointer 바인딩
    - _Requirements: 1.6, 6.1, 6.4_

  - [x] 1.13 graph-stream 라우트 + AE_LANGGRAPH feature flag + 기존 경로 fallback 골격 (`ai_engine/server.py`)
    - `/api/agents/graph-stream` 신규 라우트를 기존 라우트와 병행 등록
    - `AE_LANGGRAPH` on일 때만 그래프 경로, 처리 실패 시 기존 경로로 자동 fallback
    - _Requirements: 7.1, 7.2, 7.3, 7.4_

- [x] 2. Phase 1 체크포인트
  - Ensure all tests pass, ask the user if questions arise.

- [ ] 3. Phase 2 — 계층화: Top Supervisor + 나머지 서브그래프 (graph-of-graphs)
  - [x] 3.1 Top Supervisor 라우터 노드 (`agent_system/supervisor.py`)
    - `top_router_node`: GatewayChatModel(sonnet-4-5)로 route 분류, 타임아웃 적용
    - `route_selector` conditional edge 함수, `MAX_ROUTE_HOPS` 초과 시 route=`done`
    - _Requirements: 1.2, 6.5_

  - [x] 3.2 build_top_graph 조립 — 컴파일된 서브그래프를 노드로 add + 재라우팅 edge
    - `add_node("coding", coding_subgraph)` 방식으로 graph-of-graphs 구성
    - conditional edge(coding/media/research/ops/chat/done→END), 서브그래프→router 복귀 edge
    - `compile(checkpointer=...)`로 thread_id 기반 영속
    - _Requirements: 1.1, 1.3, 1.4, 1.5, 4.6_

  - [x] 3.3 media/research/ops/chat 서브그래프 빌더 (`agent_system/subgraphs/*.py`)
    - 공통 패턴 재사용, Route별 도구 집합 바인딩(media: generate_*, ops: run_command/git/브리지, research: read_file, chat: 도구 없음)
    - _Requirements: 1.6_

  - [ ]* 3.4 Property test: 그래프는 유한 시간에 종료 (hop / recursion cap)
    - **Property 4: 그래프는 유한 시간에 종료**
    - **Validates: Requirements 6.4, 6.5, 6.6, 6.7**

  - [ ]* 3.5 통합 테스트: coding→media 재라우팅 및 done 종료 (Gateway mock)
    - _Requirements: 1.3, 1.4, 1.5_

  - [ ] 3.6 recursion_limit / GRAPH_TOTAL_TIMEOUT / MAX_ROUTE_HOPS / SUBGRAPH_RECURSION 설정 배선
    - `astream_events` config에 `recursion_limit` 설정, 그래프 전체를 `asyncio.wait_for(GRAPH_TOTAL_TIMEOUT)`로 래핑
    - 모든 값 `AE_*` 환경변수 오버라이드
    - _Requirements: 6.4, 6.5, 6.6, 6.7_

- [ ] 4. Phase 2 체크포인트
  - Ensure all tests pass, ask the user if questions arise.

- [ ] 5. Phase 3 — RAG/verify 노드화 + SSE 매핑
  - [ ] 5.1 retrieve 노드 (`agent_system/nodes/retrieve.py`) — 기존 RAG 자산 재사용
    - `context_builder.build_system_prompt`(return_evidence) / ProjectIndexer / FastEmbedProvider / VectorStore 재사용
    - `RETRIEVE_NODE_TIMEOUT` 래핑, 검색 불가/`chat`이면 evidence=None 비차단
    - _Requirements: 3.1, 3.2, 6.3_

  - [ ] 5.2 verify 노드 (`agent_system/nodes/verify.py`) — 기존 검증 자산 재사용
    - final_text 확정, `parse_citations`/`verify_citations`로 verified/unverified 분류(비차단)
    - answer_quality 메타데이터, 파일 의도 있으나 verified_files 0건이면 `_force_generate_from_text` 호출 후 병합
    - _Requirements: 3.3, 3.4, 3.5, 3.6, 3.8, 7.5_

  - [ ]* 5.3 Property test: citation 검증은 답변을 차단하지 않음
    - **Property 7: citation 검증은 답변을 차단하지 않음**
    - **Validates: Requirements 3.5**

  - [ ] 5.4 SSE 브리지 (`agent_system/sse_bridge.py`) — astream_events v2 → 기존 계약
    - `on_chat_model_stream→{text}`, tool start/end→`{tool,status}`, 서브그래프 진입/종료→`agent_start/agent_done`
    - 디스크 실측 path만 `{verifiedFiles}`, `HEARTBEAT_INTERVAL`마다 `{heartbeat}`, 종료 시 `[DONE]`
    - _Requirements: 5.1, 5.2, 5.3, 5.4, 5.6, 6.7_

  - [ ]* 5.5 Property test: SSE 이벤트 키 부분집합 + `[DONE]` 종료
    - **Property 6: SSE 이벤트 계약 호환**
    - **Validates: Requirements 5.5, 5.6**

  - [ ] 5.6 graph-stream 라우트를 sse_bridge에 연결 + 노드 예외/GatewayModelError → `{error}` → `[DONE]` (`ai_engine/server.py`)
    - retrieve/verify 노드를 coding 서브그래프에 실배선(1.12 스텁 대체)
    - _Requirements: 5.7_

- [ ] 6. Phase 3 체크포인트
  - Ensure all tests pass, ask the user if questions arise.

- [ ] 7. Phase 4 — 전환: 프론트 신규 라우트 전환 + fallback
  - [ ] 7.1 프론트 SSE consumer가 `AE_LANGGRAPH` on 시 `/api/agents/graph-stream` 사용 (`src/center-views.js` / `src/main.js`)
    - 기존 이벤트 계약 그대로 소비(무회귀)
    - _Requirements: 7.2, 7.3_

  - [ ] 7.2 graph-stream 실패 시 기존 라우트 자동 fallback 배선 (프론트/서버)
    - _Requirements: 7.4_

  - [ ]* 7.3 통합 테스트: flag on/off 및 실패 fallback 시나리오
    - _Requirements: 7.1, 7.2, 7.3, 7.4_

- [ ] 8. Phase 4 체크포인트
  - Ensure all tests pass, ask the user if questions arise.

- [ ] 9. Phase 5 — 정리: dead code 제거 + 번들 검증
  - [ ] 9.1 dead code 제거 (`agent_system/agent_graph.py` 수동 while 루프, 구버전 `run_workflow`, 미참조 CheckpointStore 경로)
    - _Requirements: 9.3_

  - [ ] 9.2 PyInstaller spec 신규 서브모듈 수집 유지 확인 (`ai-engine-server.spec`)
    - `langgraph`, `langchain_core`, `fastembed`, `onnxruntime`, `tokenizers`, `huggingface_hub` collect 대상 유지
    - _Requirements: 9.1_

  - [ ] 9.3 번들 import smoke test (유한 시간, 서버 미기동)
    - `langgraph.checkpoint.base`, `langgraph.checkpoint.serde.jsonplus`, `langgraph.prebuilt`, `langchain_core.language_models` import 검증
    - _Requirements: 9.2_

  - [ ]* 9.4 회귀 테스트: 기존 `run_agent_stream` 경로 무회귀
    - _Requirements: 9.3_

- [ ] 10. 최종 체크포인트
  - Ensure all tests pass, ask the user if questions arise.

## Notes

- `*` 표시 서브태스크(단위/속성/통합 테스트)는 선택이며 빠른 MVP를 위해 건너뛸 수 있다.
- 각 태스크는 추적성을 위해 특정 requirements 항목을 참조한다.
- 속성 테스트는 design.md의 Correctness Properties(P1~P8)를 검증하며, Gateway/파일시스템은 fake/mock으로 대체해 유한 시간에 종료한다.
- 공식 문서 대조(1.1)로 LangGraph/langchain_core 인터페이스 시그니처를 구현 전에 확정한다.
- 모든 실행/빌드/테스트 태스크는 타임아웃·import-only smoke test로 무한대기를 방지한다.
- 기존 자산(verified_files 검증, `_force_generate_from_text`, `_call_bridge`, ConversationMemory, RAG 레이어)은 재구현하지 않고 노드에서 재사용한다.

## Task Dependency Graph

```json
{
  "waves": [
    { "id": 0, "tasks": ["1.1", "1.2"] },
    { "id": 1, "tasks": ["1.3", "1.7"] },
    { "id": 2, "tasks": ["1.4", "1.5", "1.6", "1.8", "1.9", "1.10"] },
    { "id": 3, "tasks": ["1.11", "1.12"] },
    { "id": 4, "tasks": ["1.13", "3.1"] },
    { "id": 5, "tasks": ["3.3"] },
    { "id": 6, "tasks": ["3.2", "3.6"] },
    { "id": 7, "tasks": ["3.4", "3.5"] },
    { "id": 8, "tasks": ["5.1", "5.2"] },
    { "id": 9, "tasks": ["5.3", "5.4"] },
    { "id": 10, "tasks": ["5.5", "5.6"] },
    { "id": 11, "tasks": ["7.1"] },
    { "id": 12, "tasks": ["7.2"] },
    { "id": 13, "tasks": ["7.3"] },
    { "id": 14, "tasks": ["9.1", "9.2"] },
    { "id": 15, "tasks": ["9.3", "9.4"] }
  ]
}
```
