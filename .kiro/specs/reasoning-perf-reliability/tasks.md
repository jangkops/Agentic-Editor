# Implementation Plan: reasoning-perf-reliability

## Overview

기존 LangGraph/RAG/vector 자산을 불변으로 보존하면서 세 축(Eval_Harness → Depth_Router/Fast_Path → Grounding_Gate)을 얹는다. 측정-우선(measurement-first)·페이즈 게이트 원칙(요구사항 12)에 따라 Phase 1(평가 하네스)을 먼저 구현해 baseline 을 확보한 뒤 Phase 2a·2b 를 진행하고, 각 Phase 는 `compare_baselines` 로 개선을 수치 실증한다.

전 구간 불변 제약:
- 모든 신규 LLM 호출은 Bedrock Gateway(`GatewayChatModel`) 경유 전용. `boto3`/`anthropic`/`openai` import 금지. 자격증명은 어떤 파일·상태·Baseline_Record 에도 저장하지 않는다.
- 개별 `ainvoke` await 하나만 `asyncio.wait_for` 로 감싼다. 스트림 소비 루프(`async for`)는 절대 감싸지 않는다.
- Reasoning 메타 노드는 Sonnet 계열 사용(Opus 는 스트리밍 미지원).
- `AE_ENABLE_ADAPTIVE_DEPTH` 와 `AE_ENABLE_GROUNDING_GATE` 가 모두 off 이면 그래프 구조·SSE 스트리밍 계약이 기존과 바이트 동등해야 한다. `build_parallel_top_graph` 내부는 한 줄도 수정하지 않는다.

테스트 규약(PBT):
- 라이브러리: Python `hypothesis`, 속성당 단일 테스트, `@settings(max_examples=100)` 이상. 생성기를 직접 구현(hand-roll)하지 않는다.
- 각 property 테스트 상단 주석: `# Feature: reasoning-perf-reliability, Property N: {property_text}`.
- 실행/검증 명령: `ai_engine/.venv/bin/python -m pytest scripts/... -q` (watch 모드 금지).

## Tasks

- [x] 1. Phase 1 — Eval_Harness 순수 함수 (`scripts/eval_reasoning_perf.py`)
  - [x] 1.1 순수 지표/레코드 함수 구현
    - `scripts/eval_reasoning_perf.py` 신설. 응답 경로(`server.py`)와 완전 분리된 오프라인 CLI 모듈.
    - `load_query_set(path)`(id/prompt/expected_evidence_refs/expected_answer_refs 검증), `chunks_to_refs(evidence)`(`evidence.chunks` → `"path:start-end"` 식별자, `recall_at_k`/`mrr` 규약과 일치), `aggregate_metrics(per_query)`(평균·중앙값 지연, 평균 근거성/정확성/recall@k/mrr), `build_baseline_record(active_flags, per_query, now)`(자격증명·프롬프트 전문 미포함), `compare_baselines(before, after)`(성분별 delta) 구현.
    - `ai_engine/rag/eval_metrics.py` 의 `recall_at_k`/`mrr`/`context_precision`/`groundedness`/`unsupported_claim_rate` 를 재사용(재구현 금지).
    - _Requirements: 1.3, 1.4, 1.5, 3.1, 3.2, 3.3, 3.4_

  - [x]* 1.2 recall@k 단조성 property 테스트
    - `scripts/test_eval_recall_monotonic_pbt.py`
    - **Property 11: recall@k 는 k 에 대해 단조 비감소한다**
    - **Validates: Requirements 1.4**
    - 생성기: relevant 집합, retrieved 시퀀스, `k1 <= k2`(0·음수·retrieved 길이 초과 포함).

  - [x]* 1.3 집계 지표 범위·경계 property 테스트
    - `scripts/test_eval_aggregate_bounds_pbt.py`
    - **Property 12: 근거성 집계 지표는 유효 범위와 경계 규약을 지킨다**
    - **Validates: Requirements 1.3**
    - 생성기: 음이 아닌 정수 지원/전체 주장 수(분모 0·지원>전체 방어·큰 값). `groundedness`(k=0→1.0)/`unsupported_claim_rate`(0.0)/`context_precision`(k<=0→0.0) 관례 확인.

  - [x]* 1.4 baseline delta property 테스트
    - `scripts/test_eval_compare_delta_pbt.py`
    - **Property 13: Baseline 비교 delta 는 성분별 차이이며 자기비교는 0이다**
    - **Validates: Requirements 3.2**
    - `compare_baselines(before, after)[m] == after[m] - before[m]`, `compare_baselines(x, x)` 모든 delta = 0.

  - [x]* 1.5 Baseline_Record 자격증명 부재 property 테스트
    - `scripts/test_eval_baseline_no_creds_pbt.py`
    - **Property 9: Baseline_Record 는 자격증명·프롬프트 전문을 포함하지 않는다**
    - **Validates: Requirements 3.3, 3.4, 11.2**
    - 재귀 키 스캔으로 `accessKeyId`/`secretAccessKey`/`sessionToken` 부재 및 입력 프롬프트 전문 미등장 검증.

- [x] 2. Phase 1 — Eval_Harness 오케스트레이션 및 실행 모드
  - [x] 2.1 MockGateway 결정론적 스텁 구현
    - `scripts/eval_reasoning_perf.py` 에 `MockGateway` 추가. `converse`/`converse_stream_live`/`stream_sse_realtime` 를 구현하되 응답은 프롬프트(+근거 컨텍스트) 해시로 시드된 canned 텍스트로 생성 — 동일 Query_Set 에 항상 동일 지표.
    - Gateway 호출·비용·네트워크 없음. `boto3`/`anthropic`/`openai` import 금지.
    - _Requirements: 2.1, 2.4, 11.1_

  - [x] 2.2 run_query / run_eval / Gateway_Mode 선택 + CLI 구현
    - `run_query(compiled_graph, query, config)`: `time.perf_counter()` 로 제출~final_text 완료를 밀리초 측정, `state["answer_quality"]`(faithfulness/grounding) 집계, `chunks_to_refs` 로 recall@k/mrr 산출. 예외는 잡아 `{status:"failed", error}` 기록하고 전파하지 않음.
    - `run_eval(query_set, gateway_mode, k)`: 전체 실행 → Baseline_Record. mock 모드면 MockGateway, live 면 실제 Gateway(`GatewayChatModel`) 경유.
    - Gateway_Mode 선택: 환경변수 `AE_EVAL_GATEWAY_MODE` 또는 CLI `--gateway-mode {mock,live}`, 기본값 `mock`. mock 은 재현성을 위해 `compiled_graph.ainvoke`(비스트리밍) 사용.
    - _Requirements: 1.1, 1.2, 1.6, 2.2, 2.3, 11.1_

  - [x]* 2.3 mock 결정론 property 테스트
    - `scripts/test_eval_mock_determinism_pbt.py`
    - **Property 10: mock 모드 지표는 결정론적으로 재현된다**
    - **Validates: Requirements 2.4**
    - `run_eval(..., gateway_mode='mock')` 2회 실행 → 질의별·집계 지표 동일.

  - [x]* 2.4 실패 격리 property 테스트
    - `scripts/test_eval_failure_isolation_pbt.py`
    - **Property 14: 개별 질의 실패는 격리되고 나머지는 완주한다**
    - **Validates: Requirements 1.6**
    - 예외 주입 질의 포함 Query_Set → per-query 항목 수 = 입력 질의 수, 실패는 `status=="failed"`, `n_failed` 일치.

- [x] 3. Checkpoint — Phase 1 baseline 기록 (페이즈 게이트 선행조건)
  - 모든 테스트 통과 확인: `ai_engine/.venv/bin/python -m pytest scripts/test_eval_*_pbt.py -q`.
  - `--gateway-mode mock` 으로 Eval_Harness 를 실행해 지연·근거성·정확성 baseline(플래그 모두 off) 을 Baseline_Record 로 기록한다(요구사항 12.1). 문제 발생 시 사용자에게 확인.

- [x] 4. Phase 2a — Depth_Router / Fast_Path (`ai_engine/agent_system/depth_router.py`)
  - [x] 4.1 complexity_signals + classify_heuristic 구현
    - `depth_router.py` 신설. `complexity_signals(prompt)` → `{multi_domain, needs_tool, needs_evidence, long}`(순수). `server._is_code_related` 및 `server._infer_file_intent_from_prompt` 재사용(import·호출 실패는 try/except 로 보수적 처리 → 불확실은 complex 쪽).
    - `classify_heuristic(prompt)`: 신호 중 하나라도 complex 이면 `'complex'`, 아니면 `'simple'`.
    - _Requirements: 4.1, 4.2_

  - [x]* 4.2 복잡 질의 라우팅 property 테스트
    - `scripts/test_depth_router_complex_pbt.py`
    - **Property 1: 복잡 질의는 절대 Fast_Path 로 라우팅되지 않는다**
    - **Validates: Requirements 4.2, 4.3, 6.1**
    - 생성기: 빈 문자열·공백·한/영 혼합·장문·다도메인 접속 표현·형식 키워드(pptx/pdf) 포함. LLM 분기는 모킹(실패·불확실 → complex).

  - [x]* 4.3 분류 이진성 property 테스트
    - `scripts/test_depth_router_binary_pbt.py`
    - **Property 2: 분류 결과는 항상 두 값 중 하나다**
    - **Validates: Requirements 4.1**
    - `classify_complexity` 반환값이 항상 `{'simple','complex'}` 중 하나(예외 전파 없음).

  - [x] 4.4 classify_complexity(async) 구현 — Gateway LLM 확인(옵션) + wait_for
    - `classify_complexity(prompt, deps, *, use_llm=False)`: 휴리스틱 우선, simple & `use_llm=True` 이면 `GatewayChatModel(sonnet, prefer_streaming=True).bind_tools(select_depth, toolChoice)` 로 1회 확인.
    - 개별 `ainvoke` await 하나만 `asyncio.wait_for(AE_DEPTH_ROUTER_TIMEOUT)`(기본 60초)로 감싼다. 스트림 루프는 감싸지 않는다. `TimeoutError`/`GatewayModelError`/기타 예외 → `'complex'` fail-safe.
    - 플래그 판독: `AE_ENABLE_ADAPTIVE_DEPTH`(off), `AE_DEPTH_ROUTER_TIMEOUT`(60), `AE_DEPTH_ROUTER_LLM`(off).
    - _Requirements: 4.1, 4.3, 4.4, 4.5, 11.1, 11.3, 11.6_

  - [x] 4.5 pick_fast_domain + build_fast_path_graph 구현
    - `pick_fast_domain(prompt, deps)`: `supervisor._heuristic_route` 재사용해 단일 도메인 결정.
    - `build_fast_path_graph(deps, domain)`: 단일 도메인 서브그래프(`build_domain_subgraph`)를 top 그래프의 노드로 얹어 compile. planner·Send fan-out·aggregate·evaluator 노드를 일절 추가하지 않음. SSE 계약(on_chain_start/on_chain_end 도메인명)은 Full_Graph 와 동일 유지. model 왕복은 도메인 서브그래프 기존 계약(retrieve→model→verify, `SUBGRAPH_RECURSION_LIMIT` 이내).
    - _Requirements: 5.1, 5.2, 5.3, 11.4_

  - [x]* 4.6 Fast_Path 노드 집합 property 테스트
    - `scripts/test_fast_path_nodes_pbt.py`
    - **Property 3: Fast_Path 는 planner·aggregate·evaluator 를 포함하지 않는다**
    - **Validates: Requirements 5.1, 5.2**
    - 도메인 라벨(coding/media/research/ops/chat) 전반에서 컴파일 그래프 노드 집합이 `{'planner','aggregate','evaluator'}`·fan-out 디스패치와 교집합 공집합, 정확히 하나의 도메인 서브그래프 노드만 포함.

  - [x]* 4.7 Fast_Path 유한 실행 property 테스트
    - `scripts/test_fast_path_finite_pbt.py`
    - **Property 4: Fast_Path 실행은 유한하며 model 왕복이 최소다**
    - **Validates: Requirements 5.3, 11.4**
    - MockGateway 로 model 호출 카운트: 도구 없으면 model 정확히 1회, 도구 사용 시 `SUBGRAPH_RECURSION_LIMIT` 이내 유한 종료.

- [x] 5. Phase 2a — server.py 비침습 그래프 선택 분기
  - [x] 5.1 graph-stream 라우트 그래프 선택 지점에 분기 삽입
    - `ai_engine/server.py` graph-stream 라우트(약 9374행)의 그래프 *선택* 지점에만 개입: `AE_ENABLE_ADAPTIVE_DEPTH` on 이면 `classify_complexity` → simple 은 `build_fast_path_graph`, complex 는 기존 `build_parallel_top_graph`/`build_top_graph`. off 면 기존 라인과 바이트 동등.
    - `initial_state`/`graph_config`/`graph_events_to_sse` 배선은 변경하지 않음. `build_parallel_top_graph` 내부는 수정 금지. 조립 예외 시 Full_Graph → 그마저 실패 시 `run_agent_stream` 위임 폴백.
    - _Requirements: 5.4, 6.1, 6.3, 10.1_

- [x] 6. Checkpoint — Phase 2a 실증
  - 테스트 통과 확인: `ai_engine/.venv/bin/python -m pytest scripts/test_depth_router_*_pbt.py scripts/test_fast_path_*_pbt.py -q`.
  - `AE_ENABLE_ADAPTIVE_DEPTH=1` baseline 을 기록하고 `compare_baselines` 로 단순 질의 지연 감소를 baseline 대비 수치 실증한다(요구사항 12.2). 회귀 확인 시 사용자에게 확인.

- [x] 7. Phase 2b — GraphState refine 카운터 채널
  - [x] 7.1 grounding_refine_count 채널 추가
    - `ai_engine/agent_system/graph_state.py` 에 `grounding_refine_count: Annotated[int, _take_max_int]` 추가. 기존 `_take_max_int`(monotonic MAX reducer) 재사용 — echo/reset 면역, 단조 증가. evaluator 의 `refine_count` 와 독립. 기존 채널·자격증명 정책 불변.
    - _Requirements: 8.1, 8.3, 11.2, 11.5_

- [x] 8. Phase 2b — Grounding_Gate (판정 + verify 확장 + 배선)
  - [x] 8.1 grounding_below + grounding_gate_selector 구현
    - `ai_engine/agent_system/grounding_gate.py` 신설. `grounding_below(answer_quality, env=None)`(순수): faithfulness 점수 존재&not degraded 면 `faithfulness_below_threshold()` 사용, degraded/부재이고 `grounding.score`(로컬 임베딩) 있으면 `< threshold`, 둘 다 불가면 `False`(통과). `faithfulness_below_threshold`(`rag/answer_quality.py`) + `local_grounding_score`(`rag/verifier.py`) 재사용.
    - `grounding_gate_selector(state)` → `'model'`|`'done'`: 게이트 off → `'done'`, messages 말미가 refine 지시(HumanMessage)면 `'model'`, 아니면 `'done'`.
    - 플래그: `AE_ENABLE_GROUNDING_GATE`(off), `AE_VERIFY_THRESHOLD`(0.7), `AE_MAX_REFINE`(1), `AE_GROUNDING_REJECT`(off).
    - _Requirements: 7.1, 7.2, 7.3, 7.4, 7.5, 10.2_

  - [x]* 8.2 grounding_below property 테스트
    - `scripts/test_grounding_below_pbt.py`
    - **Property 6: Grounding_Gate 판정은 임계 비교와 degraded 통과를 정확히 만족한다**
    - **Validates: Requirements 7.2, 7.4**
    - 생성기: faithfulness 점수 0.0/1.0 경계·임계 근방·`None`(degraded). degraded 면 근거 컨텍스트 유무와 무관히 `False`.

  - [x] 8.3 make_verify_node 게이트 확장
    - `ai_engine/agent_system/nodes/verify.py` `make_verify_node`: 게이트 on 일 때만 answer_quality 계산 후 `grounding_below` 이면 `grounding_refine_count < AE_MAX_REFINE` 인 경우 근거 강화 지시 HumanMessage append + 카운터 +1, 상한 소진 & 미달이면 reject 모드는 사유 명시 거절 텍스트, 아니면 본문 보존 + 경고 마커 부가. 게이트 off 시 기존 반환 불변.
    - _Requirements: 8.1, 8.2, 8.3, 9.1, 9.2, 9.3, 11.5_

  - [x]* 8.4 경고 부가·본문 보존 property 테스트
    - `scripts/test_grounding_warning_body_pbt.py`
    - **Property 8: 상한 소진 후 미달이면 경고를 부가하되 본문을 보존한다 (가용성)**
    - **Validates: Requirements 9.1, 9.2**
    - reject off, 지속 미달 mock → 최종 `final_text` 가 원본 본문을 부분 문자열로 보존 + 근거 부족 경고 마커 포함.

  - [x] 8.5 build_domain_subgraph 조건부 엣지 배선
    - `ai_engine/agent_system/subgraphs/_common.py` `build_domain_subgraph`: 조립 시점 1회 플래그 판독. `AE_ENABLE_GROUNDING_GATE` on 이면 `add_conditional_edges("verify", grounding_gate_selector, {"model":"model","done":END})`, off 이면 기존 `verify → END`(바이트 동등). Fast_Path·Full_Graph 워커 공용이므로 두 경로에 자동 적용.
    - _Requirements: 8.4, 10.2, 10.3_

  - [x]* 8.6 refine 유한·단조 property 테스트
    - `scripts/test_grounding_refine_finite_pbt.py`
    - **Property 7: Grounding_Gate refine 는 유한하고 단조적이다**
    - **Validates: Requirements 8.1, 8.2, 8.3, 11.5**
    - MockGateway 로 지속 미달 반복 → `grounding_refine_count` 단조 비감소, `AE_MAX_REFINE` 초과 없음, 게이트로 인한 model 재호출 총 횟수 `<= AE_MAX_REFINE`.

- [x] 9. 무회귀 검증 (신규 플래그 모두 off)
  - [x]* 9.1 그래프 구조 무회귀 property 테스트
    - `scripts/test_no_regression_structure_pbt.py`
    - **Property 5: 신규 플래그가 모두 off 면 그래프 구조·경로가 기존과 동등하다 (무회귀)**
    - **Validates: Requirements 6.2, 6.3, 10.1, 10.3**
    - `AE_ENABLE_ADAPTIVE_DEPTH`/`AE_ENABLE_GROUNDING_GATE` 모두 off → (a) 그래프 선택은 항상 Full_Graph, Fast_Path 미선택, (b) `build_domain_subgraph` 노드·엣지 집합이 게이트 미적용 기존 구조와 동일(`verify → END`).

  - [x]* 9.2 SSE 스트리밍 계약 스냅샷 통합 테스트
    - `scripts/test_sse_contract_snapshot.py`
    - `graph_events_to_sse` emit 이벤트 키 집합(`on_chat_model_stream`/`on_chain_start`/`on_chain_end` 등)이 Fast_Path 와 Full_Graph 에서 동일 부분집합을 유지함을 검증(플래그 off 기준 기존과 불변).
    - _Requirements: 5.4, 10.3, 10.4_

- [x] 10. 페이즈 게이트 실증 검증 (요구사항 12)
  - [x] 10.1 compare_baselines 기반 페이즈 게이트 검증 하네스
    - `scripts/test_phase_gate_compare.py`: 서로 다른 플래그 구성으로 생성한 두 Baseline_Record 를 로드해 `compare_baselines` 로 Phase 2a 지연 감소·Phase 2b 근거성 개선 delta 를 자동 단언(회귀 감지 시 실패). mock 모드로 결정론 보장.
    - _Requirements: 12.2, 12.3, 12.4_

- [x] 11. Final Checkpoint — 전체 테스트 통과
  - `ai_engine/.venv/bin/python -m pytest scripts/test_eval_*_pbt.py scripts/test_depth_router_*_pbt.py scripts/test_fast_path_*_pbt.py scripts/test_grounding_*_pbt.py scripts/test_no_regression_structure_pbt.py scripts/test_sse_contract_snapshot.py scripts/test_phase_gate_compare.py -q`.
  - 모든 테스트 통과 확인, 문제 발생 시 사용자에게 확인.

## Notes

- `*` 표시 하위 태스크는 선택(테스트)으로 빠른 MVP 시 건너뛸 수 있으나, PBT 는 Correctness Properties 1~14 검증의 핵심이므로 구현 권장.
- 각 태스크는 추적성을 위해 특정 요구사항 절을 참조한다.
- 측정-우선 원칙: Phase 1(태스크 1~3) 완료·baseline 기록 후 Phase 2a(4~6), Phase 2b(7~9) 순으로 진행하며 각 Checkpoint 에서 `compare_baselines` 로 개선 실증.
- 불변 제약(Gateway 전용·자격증명 미저장·`wait_for` 단일 await·Sonnet 메타 노드·플래그 off 무회귀)은 전 태스크 공통.
- 배포/DMG 관련 작업은 이 계획에서 제외(구현 이후 별도 처리).

## Task Dependency Graph

```json
{
  "waves": [
    { "id": 0, "tasks": ["1.1"] },
    { "id": 1, "tasks": ["1.2", "1.3", "1.4", "1.5", "4.1", "7.1", "8.1"] },
    { "id": 2, "tasks": ["2.1", "4.2", "4.3", "4.4", "8.2"] },
    { "id": 3, "tasks": ["2.2", "4.5", "8.3"] },
    { "id": 4, "tasks": ["2.3", "2.4", "4.6", "4.7", "8.4", "8.5"] },
    { "id": 5, "tasks": ["5.1", "8.6"] },
    { "id": 6, "tasks": ["9.1", "9.2"] },
    { "id": 7, "tasks": ["10.1"] }
  ]
}
```
