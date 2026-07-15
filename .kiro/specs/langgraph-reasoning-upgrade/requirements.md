# Requirements Document

## Introduction

이 기능은 이미 실전 배포된 LangGraph 계층적 오케스트레이터(`ai_engine/agent_system/`)를
"그래프 추론의 끝판왕" 수준으로 고도화한다. 현재 시스템은 graph-of-graphs(Top 라우터 +
5개 도메인 서브그래프), 순차 멀티홉(`build_top_graph`) + 병렬 fan-out(`build_parallel_top_graph`,
Send API), hop cap / iteration cap / 타임아웃, JSON 체크포인터, 세션 간 장기메모리 store,
RAG + citation 검증까지 갖추고 있다. 다만 세 가지 핵심 갭이 남아 있으며, 이 스펙은 기존
구조를 깨지 않고(무회귀) 이를 메꾼다.

1. **Evaluator 재계획 루프**: 현재 `make_aggregate_node`는 no-op(`return {}`)이고, 순차
   그래프의 완료 판정은 Top 라우터 LLM의 done 자기판단에만 의존한다. 원래 사용자 목표
   대비 산출물 품질을 채점하고, 미달 시 교정 재디스패치하는 평가-재계획 루프가 없다.
   steering(`project.md`)이 요구하는 "Coordinator → Planner(Opus) → Generator(Sonnet) →
   Evaluator(Opus), 최대 3 iteration" 아키텍처와 정합시킨다.

2. **진짜 종합(synthesis) aggregate**: 현재 aggregate가 no-op이라 병렬 워커 산출물이 상태
   reducer로 단순 병합만 되고, 하나의 일관된 최종 답변으로 종합되지 않는다.

3. **의존성 인식 DAG 병렬 플래너**: 현재 planner는 "의존 작업은 하나로 합쳐라"로 회피하여
   사실상 독립 작업만 병렬 처리한다. A→B 의존 관계를 표현하고 위상정렬 후 웨이브(wave)별로
   병렬 스케줄링하는 기능이 없다.

이 문서는 EARS 패턴과 INCOSE 품질 규칙을 준수하여 요구사항을 명문화한다. 핵심 제약(steering
실측): 무회귀(기존 그래프 경로 유지 + env 플래그 토글), 모든 LLM 호출은 Bedrock Gateway
경유(직접 SDK 금지), 자격증명은 상태/체크포인트 어디에도 미저장(문자열 식별자만), 무한대기·
무한루프 금지(개별 await 하나만 타임아웃으로 감싸고 모든 루프는 유한 종료 cap 보장), 기존
자산 재사용(재구현 금지), 기존 SSE 이벤트 계약 보존.

## Glossary

- **Orchestrator**: LangGraph 기반 계층적 오케스트레이션 시스템 전체(`ai_engine/agent_system/`).
- **Top_Router**: 사용자 의도를 도메인 route로 분류하는 최상위 라우터 노드(`make_top_router_node`) 및 순차 상위 StateGraph(`build_top_graph`).
- **Parallel_Graph**: planner → Send fan-out → 도메인 워커 병렬 → aggregate 로 구성된 병렬 상위 그래프(`build_parallel_top_graph`).
- **Domain_Subgraph**: coding / media / research / ops / chat 각 도메인의 컴파일된 서브그래프(Runnable). 상위 그래프의 노드로 add된다.
- **DAG_Planner**: 사용자 요청을 서브태스크로 분해하되 각 서브태스크의 `depends_on`(선행 서브태스크 식별자 목록)을 산출하는 플래너 노드.
- **Wave**: DAG_Planner의 서브태스크를 위상정렬(topological sort)했을 때 선행 의존이 모두 완료되어 동시에 실행 가능한 서브태스크 집합. 한 Wave 내 서브태스크는 Send로 병렬 실행된다.
- **Aggregate_Node**: 모든 병렬 워커 완료 후 1회 실행되어 워커 산출물(messages / verified_files)을 하나의 일관된 최종 답변으로 종합하는 fan-in 노드(`make_aggregate_node`).
- **Evaluator_Node**: 원래 사용자 목표 대비 현재 산출물/답변을 채점(달성 여부 + 사유 + 부족한 도메인)하는 평가 노드.
- **Refine_Loop**: Evaluator_Node가 미달 판정 시 planner/router로 되돌려 교정 실행하는 재계획 순환.
- **Refine_Count**: Refine_Loop가 수행된 횟수를 세는 last-wins 정수 카운터(GraphState 채널).
- **Refine_Cap**: Refine_Loop의 상한 횟수(`AE_MAX_REFINE`, 기본 2). Refine_Count가 이 값에 도달하면 재계획을 종료한다.
- **Gateway_Chat_Model**: Bedrock Gateway를 경유하는 LangChain `BaseChatModel` 구현체(`GatewayChatModel`).
- **Gateway_Client**: 기존 `gateway_module.GatewayClient`. SigV4 / assume-role로 Bedrock Gateway 호출.
- **Graph_Deps**: 그래프 빌더 의존성 컨테이너(`GraphDeps`). gateway / 모델 ID / checkpointer / store 참조만 보관하며 자격증명을 담지 않는다.
- **Planner_Model**: DAG_Planner가 사용하는 Bedrock model_id(역할: Planner, 기본 Opus). Graph_Deps로 주입 가능.
- **Generator_Model**: 도메인 서브그래프 model 노드가 사용하는 Bedrock model_id(역할: Generator, 기본 Sonnet). Graph_Deps로 주입 가능.
- **Evaluator_Model**: Evaluator_Node가 사용하는 Bedrock model_id(역할: Evaluator, 기본 Opus). Graph_Deps로 주입 가능.
- **SSE_Bridge**: `astream_events`를 기존 SSE 이벤트 계약으로 변환하는 매퍼(`graph_events_to_sse`).
- **Graph_Endpoint**: `/api/agents/graph-stream` FastAPI 라우트.
- **Evaluator_Flag**: Evaluator_Node 및 Refine_Loop 활성 여부를 제어하는 환경변수 `AE_ENABLE_EVALUATOR`(기본 on).
- **DAG_Planner_Flag**: 의존성 인식 DAG 스케줄링 활성 여부를 제어하는 환경변수 `AE_ENABLE_DAG_PLANNER`(기본 on).
- **Route_Hops**: Top_Router 재라우팅 hop 계수. 서브그래프 공유 채널 echo에 면역인 last-wins 정수 카운터.

## Requirements

### Requirement 1: Evaluator 재계획 루프 (목표 대비 품질 평가)

**User Story:** As a AI 에디터 사용자, I want 오케스트레이터가 산출물을 반환하기 전에 원래 요청 대비 달성 여부를 스스로 평가하고 미달 시 교정 실행하기를, so that 단일 패스로 놓친 부족한 도메인 작업이 자동으로 보완된 완결성 높은 결과를 받는다.

#### Acceptance Criteria

1. WHERE Evaluator_Flag가 활성이면, THE Orchestrator SHALL Aggregate_Node 이후 Evaluator_Node를 실행하여 원래 사용자 목표 대비 현재 산출물과 최종 답변을 평가한다.
2. WHEN Evaluator_Node가 실행되면, THE Evaluator_Node SHALL 원래 사용자 요청과 현재 messages 및 verified_files를 입력으로 하여 달성 여부(달성 또는 미달), 사유, 부족한 도메인 목록을 포함하는 평가 결과를 상태의 평가 필드에 기록한다.
3. WHEN Evaluator_Node가 평가 결과를 달성으로 판정하면, THE Orchestrator SHALL 추가 재계획 없이 그래프 실행을 END로 종료한다.
4. IF Evaluator_Node가 평가 결과를 미달로 판정하고 Refine_Count가 Refine_Cap 미만이면, THEN THE Orchestrator SHALL 평가 결과의 부족한 도메인과 사유를 교정 지시로 하여 planner 또는 Top_Router로 재디스패치하고 Refine_Count를 1 증가시킨다.
5. WHEN Evaluator_Node가 Evaluator_Model을 호출하면, THE Evaluator_Node SHALL Gateway_Chat_Model을 경유하여 호출한다.
6. IF Evaluator_Node의 Evaluator_Model 호출이 실패하거나 시간 초과되면, THEN THE Evaluator_Node SHALL 평가 결과를 달성으로 간주하여 그래프를 END로 종료한다.

### Requirement 2: Refine_Loop 유한 종료 보장

**User Story:** As a 시스템 운영자, I want 평가-재계획 순환이 항상 유한 횟수 안에 종료되기를, so that 과거 hang 이력이 재발하지 않고 그래프가 유한 시간에 종료된다.

#### Acceptance Criteria

1. THE Orchestrator SHALL Refine_Cap을 환경변수 `AE_MAX_REFINE`(기본 2)에서 읽어 Refine_Loop의 상한으로 사용한다.
2. IF Refine_Count가 Refine_Cap에 도달하면, THEN THE Evaluator_Node SHALL Evaluator_Model 호출 없이 그래프 실행을 END로 종료한다.
3. THE Orchestrator SHALL Refine_Count를 last-wins 정수 reducer 채널로 관리하여 병렬 fan-out 채널 echo에 의한 값 증폭 없이 정확한 재계획 횟수를 집계한다.
4. THE Orchestrator SHALL 하나의 그래프 실행에서 수행되는 Refine_Loop 횟수가 Refine_Cap 이하가 되도록 보장한다.

### Requirement 3: 진짜 종합(synthesis) Aggregate

**User Story:** As a 사용자, I want 병렬 워커들의 산출물이 단순 병합이 아니라 하나의 일관된 최종 답변으로 종합되기를, so that 여러 도메인 작업 결과를 개별 조각이 아닌 통합된 결론으로 이해할 수 있다.

#### Acceptance Criteria

1. WHEN 모든 병렬 워커 실행이 완료되면, THE Aggregate_Node SHALL 워커들이 병합한 messages와 verified_files를 입력으로 하여 하나의 일관된 최종 답변 텍스트를 생성한다.
2. WHEN Aggregate_Node가 종합을 위해 LLM을 호출하면, THE Aggregate_Node SHALL Gateway_Chat_Model을 경유하여 호출한다.
3. WHEN Aggregate_Node가 종합 답변을 생성하면, THE Aggregate_Node SHALL 종합 답변을 final_text 및 messages에 기록한다.
4. THE Aggregate_Node SHALL 입력으로 받은 모든 verified_files 항목을 종합 결과의 verified_files에 보존한다.
5. IF Aggregate_Node의 LLM 호출이 실패하거나 시간 초과되면, THEN THE Aggregate_Node SHALL 워커들이 병합한 기존 messages와 verified_files를 변경 없이 유지하여 비차단 진행한다.
6. IF Aggregate_Node의 종합이 실패하면, THEN THE Aggregate_Node SHALL 입력 verified_files를 삭제하지 않고 그대로 보존한다.
7. WHEN 병렬 워커가 1개만 존재하면, THE Aggregate_Node SHALL 추가 LLM 호출 없이 해당 워커의 결과를 최종 결과로 통과시킨다.
8. THE Aggregate_Node SHALL 다른 노드의 상태나 이전 호출 결과와 무관하게 LLM 호출 실패 시 항상 비차단 폴백을 수행한다.

### Requirement 4: 의존성 인식 DAG 병렬 플래너

**User Story:** As a 사용자, I want 플래너가 서브태스크 간 선행 의존 관계를 인식하여 A 완료 후 B를 실행하되 독립 작업은 병렬로 처리하기를, so that "코드 분석 결과로 PPT 생성" 같은 의존 작업이 순서를 지키면서도 전체 지연이 최소화된다.

#### Acceptance Criteria

1. WHEN DAG_Planner가 요청을 분해하면, THE DAG_Planner SHALL 각 서브태스크에 대해 도메인, 수행 작업, 고유 식별자, 그리고 선행 서브태스크 식별자 목록(depends_on)을 산출한다.
2. WHERE DAG_Planner_Flag가 활성이면, THE Orchestrator SHALL DAG_Planner가 산출한 depends_on을 기준으로 서브태스크를 위상정렬하여 Wave 목록으로 분할한다.
3. WHEN 하나의 Wave가 실행되면, THE Orchestrator SHALL 해당 Wave에 속한 서브태스크들을 Send로 병렬 fan-out한다.
4. WHEN 하나의 Wave의 모든 워커가 완료되고 선행 Wave 산출물의 후속 Wave 컨텍스트 전달이 성공하면, THE Orchestrator SHALL 후속 Wave를 실행한다.
5. IF 선행 Wave 산출물의 후속 Wave 컨텍스트 전달이 실패하면, THEN THE Orchestrator SHALL 후속 Wave를 실행하지 않고 그래프 실행을 종료한다.
6. THE Orchestrator SHALL 동시에 병렬 실행되는 워커 수를 `AE_MAX_PARALLEL_TASKS`(기본 4) 이하로 제한한다.
7. WHERE DAG_Planner_Flag가 비활성이면, THE Orchestrator SHALL 기존 독립 병렬 플래너(depends_on 무시, 단일 Wave) 동작을 유지한다.

### Requirement 5: 순환 의존 감지 및 안전 폴백

**User Story:** As a 시스템 운영자, I want DAG 스케줄러가 순환 의존을 감지하면 안전하게 폴백하기를, so that 잘못된 계획으로 인한 무한 대기나 데드락이 발생하지 않는다.

#### Acceptance Criteria

1. WHEN DAG_Planner가 산출한 depends_on에 순환 의존이 존재하면, THE Orchestrator SHALL 순환 의존을 감지한다.
2. IF 순환 의존이 감지되면, THEN THE Orchestrator SHALL 모든 서브태스크를 단일 Wave로 병렬 실행하는 폴백을 수행한다.
3. IF DAG_Planner가 산출한 depends_on이 존재하지 않는 서브태스크 식별자를 참조하면, THEN THE Orchestrator SHALL 해당 참조를 무시하고 스케줄링을 계속한다.
4. THE Orchestrator SHALL 그래프 실행에서 생성되는 Wave 수가 서브태스크 총 개수 이하가 되도록 보장하여 유한 종료를 보장한다.

### Requirement 6: 무회귀 및 Feature Flag 토글

**User Story:** As a 백엔드 개발자, I want 신규 동작이 env 플래그로 토글 가능하고 기존 그래프 경로가 그대로 유지되기를, so that 문제 발생 시 플래그 off로 즉시 기존 동작으로 복귀할 수 있다.

#### Acceptance Criteria

1. THE Orchestrator SHALL 기존 순차 멀티홉 그래프(`build_top_graph`)와 기존 병렬 fan-out 그래프(`build_parallel_top_graph`)의 진입점과 SSE 계약을 유지한다.
2. WHERE Evaluator_Flag가 비활성이면, THE Orchestrator SHALL Evaluator_Node와 Refine_Loop 없이 기존 aggregate 종료 동작을 수행한다.
3. WHERE DAG_Planner_Flag가 비활성이면, THE Orchestrator SHALL 기존 독립 병렬 플래너 동작을 수행한다.
4. WHEN Graph_Endpoint가 그래프를 조립하면, THE Orchestrator SHALL 기존 `AE_LANGGRAPH` 및 `AE_LANGGRAPH_PARALLEL` 플래그 계약을 보존한다.
5. WHEN 신규 노드가 상태를 반환하면, THE Orchestrator SHALL 기존 SSE_Bridge가 emit하는 이벤트 키 집합(`{text, thinking, tool, status, verifiedFiles, type, taskId, heartbeat, answerQuality, qualityPending, error}`)의 부분집합만 사용한다.

### Requirement 7: Gateway 경유 LLM 호출

**User Story:** As a 보안 및 아키텍처 담당자, I want 신규 노드의 모든 LLM 호출이 Bedrock Gateway를 경유하기를, so that 직접 SDK 사용 금지 정책(steering)을 위반하지 않는다.

#### Acceptance Criteria

1. WHEN Evaluator_Node, Aggregate_Node, DAG_Planner 중 하나가 LLM을 호출하면, THE Orchestrator SHALL Gateway_Chat_Model을 경유하여 호출한다.
2. THE Orchestrator SHALL 신규 노드에서 boto3, Anthropic SDK, OpenAI SDK를 포함한 직접 모델 SDK 호출을 사용하지 않는다.
3. WHEN Gateway_Chat_Model 호출이 Gateway_Client 오류를 반환하면, THE 신규 노드 SHALL 해당 오류를 비차단 폴백으로 처리하여 그래프를 유한 종료한다.

### Requirement 8: 무한루프 및 무한대기 차단

**User Story:** As a 시스템 운영자, I want 신규 노드의 외부 호출에 개별 타임아웃이 적용되고 모든 루프가 유한 종료 cap을 가지기를, so that 과거 Python 3.14 hang 이력이 재발하지 않는다.

#### Acceptance Criteria

1. WHEN Evaluator_Node가 Evaluator_Model을 호출하면, THE Evaluator_Node SHALL `llm.ainvoke` 개별 await 하나만 `asyncio.wait_for(AE_EVALUATOR_TIMEOUT, 기본 300초)`로 감싼다.
2. WHEN Aggregate_Node가 LLM을 호출하면, THE Aggregate_Node SHALL `llm.ainvoke` 개별 await 하나만 `asyncio.wait_for(AE_AGGREGATE_TIMEOUT, 기본 300초)`로 감싼다.
3. WHEN DAG_Planner가 LLM을 호출하면, THE DAG_Planner SHALL `llm.ainvoke` 개별 await 하나만 `asyncio.wait_for(AE_ROUTER_TIMEOUT, 기본 60초)`로 감싼다.
4. THE Orchestrator SHALL 신규 노드에서 스트림 소비 루프(`async for`) 전체를 `asyncio.wait_for`로 감싸지 않는다.
5. THE Orchestrator SHALL Refine_Loop, Wave 스케줄링, 재라우팅 순환이 각각 Refine_Cap, 서브태스크 개수, Route_Hops 상한 이하로 유한 종료되도록 보장한다.

### Requirement 9: 모델 역할 주입 (Planner=Opus, Generator=Sonnet, Evaluator=Opus)

**User Story:** As a 백엔드 개발자, I want Planner / Generator / Evaluator 모델 역할이 각각 기본값을 가지되 deps나 env로 주입 가능하기를, so that steering의 모델 역할 배분을 지키면서도 실측 튜닝이 가능하다.

#### Acceptance Criteria

1. THE Graph_Deps SHALL Planner_Model, Generator_Model, Evaluator_Model 각각에 대한 model_id 필드를 제공한다.
2. WHERE Planner_Model이 주입되지 않으면, THE Orchestrator SHALL Planner 역할에 Opus 계열 기본 model_id를 사용한다.
3. WHERE Evaluator_Model이 주입되지 않으면, THE Orchestrator SHALL Evaluator 역할에 Opus 계열 기본 model_id를 사용한다.
4. WHERE Generator_Model이 주입되지 않으면, THE Orchestrator SHALL Generator 역할에 Sonnet 계열 기본 model_id를 사용한다.
5. WHEN Graph_Deps에 특정 역할 model_id가 주입되면, THE Orchestrator SHALL 해당 역할 노드가 주입된 model_id를 사용하도록 한다.

### Requirement 10: 자격증명 미저장

**User Story:** As a 보안 담당자, I want 신규 노드와 상태 채널이 AWS 자격증명을 저장하지 않기를, so that steering 보안 정책(자격증명 미저장, 런타임 주입/assume-role)을 위반하지 않는다.

#### Acceptance Criteria

1. THE Orchestrator SHALL 신규 상태 채널(평가 결과, Refine_Count, depends_on 계획)에 AWS 자격증명(accessKeyId, secretAccessKey, sessionToken)을 포함하지 않는다.
2. WHEN 신규 상태가 직렬화되어 체크포인트로 기록되면, THE Orchestrator SHALL 기록된 체크포인트에 accessKeyId와 secretAccessKey가 포함되지 않도록 보장한다.
3. THE Orchestrator SHALL 워커 및 신규 노드 간 전달되는 상태에 자격증명 대신 문자열 식별자(aws_profile, bedrock_user)만 포함한다.
4. WHERE AWS 작업이 필요한 경우에만, THE Orchestrator SHALL 문자열 식별자(aws_profile, bedrock_user)를 상태에 포함하며, AWS 작업이 없으면 식별자 없이도 상태 전달을 허용한다.

### Requirement 11: 기존 자산 재사용

**User Story:** As a 백엔드 개발자, I want 신규 노드가 기존 검증된 자산을 재구현하지 않고 재사용하기를, so that 중복 구현으로 인한 회귀 위험을 피한다.

#### Acceptance Criteria

1. THE Orchestrator SHALL Evaluator_Node, Aggregate_Node, DAG_Planner가 LLM 호출에 기존 Gateway_Chat_Model을 재사용하도록 한다.
2. THE Orchestrator SHALL 신규 노드가 상태 관리에 기존 GraphState와 reducer를 재사용하도록 한다.
3. THE Orchestrator SHALL 도메인 워커 실행에 기존 컴파일된 Domain_Subgraph와 verify 노드를 재사용하도록 한다.
4. THE Orchestrator SHALL 산출물 검증에 기존 verified_files 디스크 실측 로직을 재사용하도록 한다.
