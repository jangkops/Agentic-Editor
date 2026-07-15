# Implementation Plan: langgraph-reasoning-upgrade

## Overview

이 계획은 이미 배포된 LangGraph 오케스트레이터(`ai_engine/agent_system/`) 위에 세 가지
고도화(Evaluator 재계획 루프 / 진짜 종합 aggregate / 의존성 인식 DAG 병렬 플래너)를
**재구현 없이** 얹는다. 구현 언어는 기존 코드베이스와 동일한 **Python 3.11+** 이다.

진행 순서는 무회귀와 점진적 검증을 위해 다음과 같다:
**기반 확장(상태 채널/deps 필드) → 순수 함수(dag.py) → 파싱/노드(supervisor.py) → 조립 변경
→ smoke·무회귀 검증**. 각 단계는 앞 단계 위에 쌓이며, 마지막에 evaluator conditional 조립으로
모든 조각을 결선한다. 신규 동작은 전부 env 플래그(`AE_ENABLE_EVALUATOR`,
`AE_ENABLE_DAG_PLANNER`)로 토글되며 off 시 기존 그래프와 동일하다.

property-based test는 Hypothesis 로 작성하고 각 100회 이상(`@settings(max_examples=100)`)
반복하며, 파일은 `scripts/test_*_pbt.py`(PBT) / `scripts/test_*.py`(단위·smoke)에 배치하고
`pytest --run` 단발 실행으로 검증한다(watch 금지). 각 PBT 파일 상단에
`# Feature: langgraph-reasoning-upgrade, Property N: ...` 태깅을 붙인다.

## Tasks

- [x] 1. 상태 채널 및 의존성 필드 확장 (무회귀 기반)
  - [x] 1.1 graph_state.py에 Evaluation TypedDict 및 신규 채널 추가
    - `Evaluation(TypedDict, total=False)` 정의: `achieved: bool`, `reason: str`, `missing_domains: List[str]` (자격증명 필드 없음)
    - `GraphState`에 신규 채널 추가: `evaluation: Annotated[Optional[Evaluation], _take_right]`, `refine_count: Annotated[int, _take_right]`, `completed_waves: Annotated[int, _take_right]`
    - 기존 `plan` 채널의 reducer(`_take_right`)는 불변 유지, 항목 형태 주석을 `{"id","domain","subtask","depends_on"}`로 확장
    - 기존 채널/`_take_right`/`_merge_verified_files` 재사용, 삭제·변경 금지
    - _Requirements: 2.3, 4.1, 10.1, 11.2_

  - [x]* 1.2 last-wins reducer 속성 테스트
    - **Property 13: last-wins reducer 정확성 (echo 면역)**
    - **Validates: Requirements 2.3**
    - `scripts/test_take_right_reducer_pbt.py` — 임의 값 시퀀스에 `_take_right` 순차 적용 시 마지막 non-None 값, 동일 값 echo 반복에도 증폭 없음 검증

  - [x] 1.3 deps.py GraphDeps에 모델 역할 필드 추가
    - `_DEFAULT_PLANNER_MODEL`(Opus), `_DEFAULT_GENERATOR_MODEL`(Sonnet), `_DEFAULT_EVALUATOR_MODEL`(Opus) 상수 정의
    - `GraphDeps`에 `model_planner`, `model_generator`, `model_evaluator` 필드 추가(각 기본값 바인딩). 기존 `model_coding` 필드는 하위 호환 유지
    - _Requirements: 9.1, 9.2, 9.3, 9.4, 9.5_

  - [x]* 1.4 GraphDeps 기본 모델 역할 단위 테스트
    - `scripts/test_graphdeps_model_roles.py` — 미주입 시 기본값(Opus/Sonnet/Opus), 주입 시 주입값 사용 검증
    - _Requirements: 9.2, 9.3, 9.4, 9.5_

- [x] 2. 순수 함수 DAG 스케줄링 모듈 (agent_system/dag.py 신규)
  - [x] 2.1 sanitize_depends_on 구현
    - 신규 파일 `ai_engine/agent_system/dag.py` 생성(부작용·네트워크 의존 없는 순수 함수 전용)
    - `sanitize_depends_on(subtasks) -> list[dict]`: id 누락 항목을 `t{i}`로 보정, depends_on의 미실재 id 참조 제거, 입력 불변(새 리스트 반환), 서브태스크 개수 보존
    - _Requirements: 5.3_

  - [x]* 2.2 sanitize_depends_on 속성 테스트
    - **Property 7: 무효 의존 참조 제거**
    - **Validates: Requirements 5.3**
    - `scripts/test_dag_sanitize_pbt.py` — 임의 무효 id 참조 포함 입력에서 정제 후 모든 depends_on이 실재 id만 포함, 개수 보존

  - [x] 2.3 detect_cycle 구현
    - `detect_cycle(subtasks) -> bool`: Kahn 또는 DFS 방문색으로 순환 판정(`sanitize_depends_on` 이후 호출 가정)
    - _Requirements: 5.1_

  - [x] 2.4 topological_waves 구현
    - `topological_waves(subtasks) -> list[list[dict]]`: 위상정렬로 Wave 분할, 각 서브태스크는 정확히 하나의 Wave에 속함, 선행 depends_on은 앞선 Wave에 위치
    - 순환 감지 시(`detect_cycle` True) 전체를 단일 Wave로 폴백 반환, 반환 Wave 수는 서브태스크 총 개수 이하
    - _Requirements: 4.2, 5.2, 5.4_

  - [x]* 2.5 topological_waves 위상정렬 정확성 속성 테스트
    - **Property 5: 위상정렬 정확성**
    - **Validates: Requirements 4.2**
    - `scripts/test_dag_topological_waves_pbt.py` — 비순환 DAG 생성기(정수 순서 부여, 더 작은 순서만 참조)로 선행 depends_on이 앞선 Wave에 존재하고 partition임을 검증

  - [x]* 2.6 순환 감지 및 단일 Wave 폴백 속성 테스트
    - **Property 6: 순환 감지 및 단일 Wave 폴백**
    - **Validates: Requirements 5.1, 5.2**
    - `scripts/test_dag_cycle_fallback_pbt.py` — 순환 포함 그래프(비순환 DAG에 역방향 엣지 삽입)에서 `detect_cycle` True, `topological_waves`가 길이 1 Wave 목록 반환

  - [x]* 2.7 Wave 유한 종료 속성 테스트
    - **Property 1: 유한 종료 (Refine / Wave / Route 상한)**
    - **Validates: Requirements 2.4, 4.2, 5.4, 8.5**
    - `scripts/test_finite_termination_pbt.py` — 임의 subtasks에 대해 Wave 수 ≤ 서브태스크 수, 임의 초기 refine_count 시뮬레이션 루프가 `AE_MAX_REFINE` 이하로 종료

- [x] 3. Checkpoint - 순수 함수 모듈 검증
  - Ensure all tests pass, ask the user if questions arise.

- [x] 4. 평가 결과 파싱 순수 함수 (supervisor.py)
  - [x] 4.1 parse_evaluation 구현
    - `supervisor.py`에 `parse_evaluation(ai_message, valid_domains) -> dict` 추가(tool_calls 우선, 텍스트 폴백)
    - 항상 `{"achieved": bool, "reason": str, "missing_domains": list[str]}` 반환, missing_domains는 valid_domains 부분집합, 파싱 불가/무효 시 achieved=True(비차단), 예외 미전파
    - _Requirements: 1.2, 1.6_

  - [x]* 4.2 parse_evaluation 견고성 속성 테스트
    - **Property 8: 평가 결과 파싱 견고성**
    - **Validates: Requirements 1.2, 1.6**
    - `scripts/test_parse_evaluation_pbt.py` — 임의 응답 형태(tool_calls dict/텍스트/필드 누락/무효 타입)에서 계약 형태 유지, missing_domains 유효 라벨 부분집합, 파싱 불가 시 achieved=True

- [x] 5. DAG Planner 스키마 확장 (supervisor.py)
  - [x] 5.1 _PLAN_TOOL 스키마 및 _make_plan 파싱 확장
    - `_PLAN_TOOL.inputSchema.subtasks.items.properties`에 `id`, `depends_on` 추가, `required`에 `id` 추가
    - `_make_plan` 파싱을 확장: 각 항목에 `id`(누락 시 `t{i}` 보정), `depends_on`(기본 `[]`) 포함
    - `AE_ENABLE_DAG_PLANNER` off면 depends_on 무시(단일 Wave 동작). 기존 `MAX_PARALLEL_TASKS` 상한 및 휴리스틱 폴백 유지
    - _Requirements: 4.1, 4.7, 11.7_

  - [x]* 5.2 _make_plan 파싱 스키마 견고성 속성 테스트
    - **Property 9: 계획 파싱 스키마 견고성**
    - **Validates: Requirements 4.1**
    - `scripts/test_make_plan_schema_pbt.py` — 임의 subtasks 배열(누락/무효 포함, gateway mock)에서 각 항목이 비어있지 않은 id, 유효 domain, 문자열 subtask, 리스트 depends_on 보유

- [x] 6. plan_dispatch Wave 인식 (supervisor.py)
  - [x] 6.1 plan_dispatch Wave 인식 개조
    - `AE_ENABLE_DAG_PLANNER` off: 기존 전체 plan 단일 Wave fan-out 유지(무회귀)
    - on: `sanitize_depends_on` → `topological_waves` 계산 후 `state["completed_waves"]`(기본 0) 인덱스의 Wave만 Send. 순환 시 단일 Wave 폴백
    - 동시 Send 수 ≤ `MAX_PARALLEL_TASKS`, 각 Send 도메인은 유효 서브그래프 라우트, 후속 Wave 컨텍스트 부재 시 빈 Send로 종료
    - _Requirements: 4.3, 4.4, 4.5, 4.6, 4.7_

  - [x]* 6.2 plan_dispatch fan-out 상한 및 Wave 선택 속성 테스트
    - **Property 10: fan-out 상한 및 Wave 선택**
    - **Validates: Requirements 4.3, 4.4, 4.6**
    - `scripts/test_plan_dispatch_pbt.py` — 임의 크기 plan + 임의 completed_waves에서 Send 수 ≤ AE_MAX_PARALLEL_TASKS, 도메인 유효, DAG 활성 시 현재 Wave 서브태스크만 dispatch

- [x] 7. Aggregate 노드 종합 승격 (supervisor.py)
  - [x] 7.1 make_aggregate_node를 LLM 종합으로 승격
    - `AE_AGGREGATE_TIMEOUT`(기본 300) env 추가, `_AGGREGATE_SYSTEM_PROMPT` 정의
    - 워커 1개면 LLM 스킵 후 기존 결과 통과(`{}`). 여러 개면 `GatewayChatModel`로 messages+verified_files 요약 → `{"final_text":..., "messages":[AIMessage]}` 반환
    - `llm.ainvoke` 개별 await 하나만 `asyncio.wait_for(AE_AGGREGATE_TIMEOUT)`. LLM 실패/타임아웃/gateway=None 시 `{}` 반환(비차단, verified_files 미삭제)
    - _Requirements: 3.1, 3.2, 3.3, 3.4, 3.5, 3.6, 3.7, 3.8, 7.1, 7.3, 8.2_

  - [x]* 7.2 aggregate 비차단/단일 워커 스킵 단위 테스트 (gateway mock)
    - `scripts/test_aggregate_node.py` — mock 예외 시 `{}` 반환·verified_files 보존, 워커 1개 시 LLM 미호출 검증
    - _Requirements: 3.5, 3.7_

  - [x]* 7.3 비차단 종합 및 verified_files 보존 속성 테스트
    - **Property 4: 비차단 종합 및 verified_files 보존**
    - **Validates: Requirements 3.4, 3.5, 3.6, 3.8, 7.3**
    - `scripts/test_aggregate_preservation_pbt.py` — 임의 사전 상태 + 임의 verified_files + 성공/실패 주입(gateway mock)에서 예외 미전파, 결과 verified_files absPath 집합이 입력 집합 포함

- [x] 8. Evaluator 노드 및 재계획 루프 (supervisor.py)
  - [x] 8.1 _EVAL_TOOL, make_evaluator_node, evaluator_selector 구현
    - `AE_MAX_REFINE`(기본 2), `AE_EVALUATOR_TIMEOUT`(기본 300) env + `_EVAL_TOOL` 스키마 정의
    - `make_evaluator_node(deps)`: refine_count ≥ cap이면 LLM 없이 achieved=True 반환 → END. 그 외 `deps.model_evaluator`(Opus)로 `GatewayChatModel` 평가, `parse_evaluation` 사용. 달성 → END, 미달&cap 미만 → `{"evaluation":..., "refine_count": +1, "messages":[HumanMessage(교정지시)]}`
    - `llm.ainvoke` 개별 await 하나만 `asyncio.wait_for(AE_EVALUATOR_TIMEOUT)`, 실패/타임아웃 시 achieved=True(비차단)
    - `evaluator_selector(state)`: achieved=True 또는 refine_count ≥ cap이면 "done", 아니면 "planner"
    - _Requirements: 1.1, 1.2, 1.3, 1.4, 1.5, 1.6, 2.1, 2.2, 2.3, 7.1, 7.3, 8.1_

  - [x]* 8.2 evaluator 타임아웃/실패 시 achieved 단위 테스트 (gateway mock)
    - `scripts/test_evaluator_node.py` — mock 타임아웃/예외 시 achieved=True·done 라우팅, refine_count ≥ cap 시 LLM 미호출 검증
    - _Requirements: 1.6, 2.2_

  - [x]* 8.3 evaluator_selector 라우팅 및 재계획 카운트 속성 테스트
    - **Property 11: Evaluator 라우팅 및 재계획 카운트 정확성**
    - **Validates: Requirements 1.3, 1.4, 2.2**
    - `scripts/test_evaluator_selector_pbt.py` — 임의 evaluation + refine_count에서 라우팅 정확성, "planner" 경로 시 반환 refine_count = 입력+1

- [x] 9. Checkpoint - 노드 계약 검증
  - Ensure all tests pass, ask the user if questions arise.

- [x] 10. 그래프 조립 결선 (supervisor.py build_parallel_top_graph)
  - [x] 10.1 build_parallel_top_graph에 evaluator conditional 결선
    - `_env_flag`(또는 기존 env 헬퍼)로 `AE_ENABLE_EVALUATOR` 읽기. on이면 `evaluator` 노드 add + `aggregate → evaluator` edge + `evaluator` conditional(`{"planner": "planner", "done": END}`)
    - off이면 기존 `aggregate → END` 유지(무회귀). `build_top_graph`(순차)·SSE 브리지·server.py 조립 분기·`AE_LANGGRAPH`/`AE_LANGGRAPH_PARALLEL` 계약 미변경
    - _Requirements: 1.1, 6.1, 6.2, 6.3, 6.4_

  - [x]* 10.2 무회귀 구성 플래그 on/off 스냅샷 테스트
    - **Property 2: 무회귀 구성 (플래그 off)**
    - **Validates: Requirements 6.2, 6.3, 4.7**
    - `scripts/test_parallel_graph_flags.py` — 두 플래그 모두 off 시 노드/엣지 집합이 evaluator 없이 `aggregate → END`, plan_dispatch 단일 Wave 동작 스냅샷 비교

  - [x]* 10.3 신규 채널 자격증명 미저장 속성 테스트
    - **Property 3: 신규 채널 자격증명 미저장**
    - **Validates: Requirements 10.1, 10.2**
    - `scripts/test_new_channels_no_creds_pbt.py` — 임의 evaluation/refine_count/completed_waves/확장 plan 값과 직렬화 결과에 accessKeyId/secretAccessKey/sessionToken 키 부재

  - [x]* 10.4 SSE 이벤트 키 부분집합 속성 테스트
    - **Property 12: SSE 이벤트 키 부분집합**
    - **Validates: Requirements 6.5**
    - `scripts/test_sse_key_subset_pbt.py` — 신규 노드(evaluator/aggregate/planner) 반환 dict 키가 기존 GraphState 채널 집합 및 SSE 이벤트 키 집합의 부분집합

- [x] 11. smoke 및 무회귀 검증
  - [x]* 11.1 아키텍처 정적 제약 smoke 테스트
    - `scripts/test_reasoning_upgrade_smoke.py` — 신규 소스에 boto3/anthropic/openai 직접 import 부재, `wait_for(ainvoke)` 패턴 존재, `GraphDeps`의 model_planner/model_generator/model_evaluator 필드 존재, 플래그 on/off 조립 스냅샷
    - _Requirements: 6.5, 7.2, 8.4, 9.1_

  - [x]* 11.2 기존 그래프 회귀 0 확인
    - `pytest --run`으로 기존 agent_system 관련 테스트 전량 실행, 플래그 off 시 `build_parallel_top_graph`/`build_top_graph` 동작 불변 확인
    - _Requirements: 6.1, 6.2, 6.3, 6.4, 11.1, 11.2, 11.3, 11.4_

- [x] 12. Final checkpoint - 전체 테스트 통과
  - Ensure all tests pass, ask the user if questions arise.

## Notes

- 구현 언어는 기존 코드베이스와 동일한 **Python 3.11+** 이며, 모든 LLM 호출은 기존
  `GatewayChatModel`(Bedrock Gateway 경유)만 재사용한다(직접 SDK 금지 — Req 7).
- `*` 표시 sub-task는 선택(테스트)으로, 빠른 MVP 시 건너뛸 수 있으나 13개 Correctness
  Property는 배포 전 전량 통과를 권장한다.
- 모든 PBT는 Hypothesis + `@settings(max_examples=100)`, 파일 상단에
  `# Feature: langgraph-reasoning-upgrade, Property N: ...` 태깅.
- LLM 포함 노드(aggregate/evaluator) 테스트는 gateway를 mock하여 네트워크 없이 결정론적
  실행한다. 검증은 `pytest --run` 단발(watch 금지).
- 신규 코드는 기존 파일에 추가/변경(`dag.py`만 신규), 기존 자산(GraphState/reducer, 컴파일된
  Domain_Subgraph, verify 노드, verified_files 디스크 실측, hop/timeout 패턴) 재사용(Req 11).
- 각 supervisor.py 변경 태스크(4.1/5.1/6.1/7.1/8.1/10.1)는 동일 파일을 수정하므로 순차 Wave로
  배치되어 편집 충돌을 방지한다.

## Task Dependency Graph

```json
{
  "waves": [
    { "id": 0, "tasks": ["1.1", "1.3", "2.1"] },
    { "id": 1, "tasks": ["1.2", "1.4", "2.2", "2.3"] },
    { "id": 2, "tasks": ["2.4", "4.1"] },
    { "id": 3, "tasks": ["2.5", "2.6", "2.7", "5.1"] },
    { "id": 4, "tasks": ["4.2", "5.2", "6.1"] },
    { "id": 5, "tasks": ["6.2", "7.1"] },
    { "id": 6, "tasks": ["7.2", "7.3", "8.1"] },
    { "id": 7, "tasks": ["8.2", "8.3", "10.1"] },
    { "id": 8, "tasks": ["10.2", "10.3", "10.4", "11.1", "11.2"] }
  ]
}
```
