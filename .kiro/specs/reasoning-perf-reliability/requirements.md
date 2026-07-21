# Requirements Document

## Introduction

이 스펙은 agentic-editor 의 계층적 LangGraph 오케스트레이터 + RAG 파이프라인에서 라이브 검증으로 확인된 두 핵심 약점을 개선한다.

1. **지연(latency)**: 단순 질의도 planner → 병렬 워커 → aggregate → evaluator 풀 그래프를 태워 20초~2분이 소요된다. 근본 원인은 호출당 속도가 아니라 순차로 누적되는 LLM 왕복 횟수다.
2. **할루시네이션(hallucination)**: 근거성 지표(`faithfulness`, `local_grounding_score`, citation 검증)는 이미 계산되지만 기본 `verify_mode="deferred"`라 `[DONE]` 이후 백그라운드 로깅용이며, 응답 행동(재생성/경고/거절)에 **연결되어 있지 않다**. `faithfulness_below_threshold()`는 정의만 되어 있고 호출되지 않는다.

개선은 세 단계로 우선순위에 따라 진행한다.

- **Phase 1 — 평가(eval) 하네스**: ground-truth 질의셋으로 end-to-end 지연, 근거성, 정확성 baseline 을 재현 가능하게 수치화한다. 이후 모든 고도화의 개선 실증 근거가 된다. Gateway mock 및 라이브 양쪽 모드를 지원한다.
- **Phase 2a — 적응형 깊이(adaptive depth) 라우팅**: 단순/단일 도메인 질의는 풀 그래프를 스킵하고 fast-path 로 응답한다. 복잡한 질의만 풀 그래프를 태운다.
- **Phase 2b — 근거 강제(grounding/faithfulness enforcement) 게이트**: 근거성 점수가 임계 미만이면 bounded refine 을 유도하고, 그래도 미달이면 근거 부족을 경고 표기하거나 거절한다.

모든 신규 동작은 플래그 게이트되며 기본값은 off 또는 무손상(no-regression)이다. 기존 LangGraph/RAG/vector 자산, 노드·엣지·스트리밍 계약은 불변으로 보존한다.

## Glossary

- **Eval_Harness**: ground-truth 질의셋을 실행해 지연·근거성·정확성 지표를 재현 가능하게 산출·기록하는 오프라인 평가 시스템(`scripts/`에 위치, 프로덕션 응답 경로와 분리).
- **Query_Set**: 각 항목이 프롬프트와 기대 근거/정답 참조를 가진 ground-truth 질의 모음.
- **Gateway_Mode**: Eval_Harness 의 LLM 백엔드 선택. `mock`(결정론적 스텁 응답) 또는 `live`(실제 Bedrock Gateway 경유) 중 하나.
- **Baseline_Record**: 특정 시점·구성에서 Eval_Harness 가 산출한 지표 스냅샷(JSON), Phase 간 개선 비교의 기준.
- **Depth_Router**: 사용자 질의의 복잡도를 분류해 fast-path 또는 full-graph 실행 경로를 선택하는 라우팅 시스템.
- **Fast_Path**: planner·병렬 워커·aggregate·evaluator 메타 노드를 스킵하고 단일 도메인 서브그래프(retrieve → model → tools → verify)만으로 응답을 생성하는 경로.
- **Full_Graph**: 기존 `build_parallel_top_graph` 경로(planner → 병렬 워커 → aggregate → evaluator + bounded refine).
- **Complexity_Signal**: 다도메인 요구, 도구 사용 필요, 명시적 근거/조사 요구 등 질의를 복잡으로 분류하게 하는 판정 입력.
- **Grounding_Gate**: 최종 응답의 근거성 점수를 임계값과 대조해 통과·재생성·경고/거절 행동을 결정하는 시스템.
- **Grounding_Score**: `answer_quality` 메타데이터의 근거성 지표. `faithfulness.score`(LLM 채점) 및 `grounding.score`(로컬 임베딩 코사인) 중 가용한 값.
- **Refine_Attempt**: Grounding_Gate 가 임계 미달 시 유도하는 응답 재생성 1회 시도.
- **Gateway**: Bedrock Gateway (`ai_engine/gateway_module.py`). 모든 LLM 호출의 유일한 경유 지점.
- **Reasoning_Meta_Node**: planner / evaluator 등 추론 메타 노드. 스트리밍 미지원 모델(Opus)이 아닌 Sonnet 계열을 사용한다.

## Requirements

### Requirement 1: 평가 지표 산출 (Eval Harness — 지연·근거성·정확성)

**User Story:** 개발자로서, 나는 ground-truth 질의셋에 대해 지연·근거성·정확성을 수치화하고 싶다. 그래야 이후 모든 고도화가 실제 개선을 이뤘는지 실증할 수 있다.

#### Acceptance Criteria

1. WHEN Eval_Harness 가 Query_Set 으로 실행되면, THE Eval_Harness SHALL 각 질의에 대해 end-to-end 지연(밀리초), 근거성 지표(Grounding_Score), 정확성 지표를 산출한다.
2. THE Eval_Harness SHALL end-to-end 지연을 질의 제출부터 최종 응답 완료까지의 경과 시간(밀리초)으로 측정한다.
3. THE Eval_Harness SHALL 근거성 지표로 `faithfulness.score` 와 `grounding.score`(가용한 값)를 `eval_metrics.py` 의 순수 함수(`groundedness`, `context_precision`, `unsupported_claim_rate`)로 집계한다.
4. THE Eval_Harness SHALL 검색 품질 지표로 `recall_at_k` 와 `mrr` 을 Query_Set 의 기대 근거 참조와 대조하여 산출한다.
5. WHEN 실행이 완료되면, THE Eval_Harness SHALL 질의별 지표와 집계 요약(평균·중앙값 지연, 평균 근거성, 평균 정확성)을 하나의 결과 산출물로 기록한다.
6. IF 개별 질의 실행이 실패하면, THEN THE Eval_Harness SHALL 해당 질의를 실패로 기록하고 나머지 질의 실행을 계속한다.

### Requirement 2: 평가 실행 모드 (Mock 및 Live)

**User Story:** 개발자로서, 나는 Eval_Harness 를 게이트웨이 mock 과 라이브 양쪽에서 실행하고 싶다. 그래야 게이트웨이 비용·지연 없이 결정론적으로 검증하면서도, 필요 시 실제 지연·정확성을 실측할 수 있다.

#### Acceptance Criteria

1. WHERE Gateway_Mode 가 `mock` 이면, THE Eval_Harness SHALL 실제 Gateway 호출 없이 결정론적 스텁 응답으로 지표를 산출한다.
2. WHERE Gateway_Mode 가 `live` 이면, THE Eval_Harness SHALL 모든 LLM 호출을 Bedrock Gateway 경유로 수행한다.
3. THE Eval_Harness SHALL Gateway_Mode 를 환경변수 또는 실행 인자로 선택하도록 제공하고 기본값을 `mock` 으로 한다.
4. WHEN Gateway_Mode 가 `mock` 으로 실행되면, THE Eval_Harness SHALL 동일 Query_Set 에 대해 재현 가능한(결정론적) 지표를 산출한다.

### Requirement 3: Baseline 기록 및 Phase 간 개선 비교

**User Story:** 개발자로서, 나는 각 Phase 전후의 지표를 baseline 으로 저장하고 비교하고 싶다. 그래야 다음 Phase 진행 여부를 실증 데이터로 결정할 수 있다.

#### Acceptance Criteria

1. WHEN Eval_Harness 실행이 완료되면, THE Eval_Harness SHALL 집계 지표를 타임스탬프와 활성 플래그 구성을 포함한 Baseline_Record 로 저장한다.
2. WHEN 두 개의 Baseline_Record 가 제공되면, THE Eval_Harness SHALL 지연·근거성·정확성 지표의 차이(delta)를 산출한다.
3. THE Baseline_Record SHALL AWS 자격증명(accessKeyId, secretAccessKey, sessionToken)을 포함하지 않는다.
4. THE Baseline_Record SHALL 프롬프트 전문·대화 원문 대신 질의 식별자와 지표만 저장한다.

### Requirement 4: 질의 복잡도 분류 (Adaptive Depth Routing)

**User Story:** 사용자로서, 나는 단순한 질의가 빠르게 답변되길 원한다. 그래야 간단한 질문에 수십 초를 기다리지 않는다.

#### Acceptance Criteria

1. WHEN 사용자 질의가 제출되면, THE Depth_Router SHALL 해당 질의를 `simple` 또는 `complex` 로 분류한다.
2. THE Depth_Router SHALL 다도메인 요구, 도구 사용 필요, 명시적 근거·조사 요구 중 하나 이상이 감지되면 질의를 `complex` 로 분류한다.
3. IF Depth_Router 분류가 실패하거나 판정이 불확실하면, THEN THE Depth_Router SHALL 질의를 `complex` 로 분류하여 Full_Graph 로 진행한다.
4. WHERE Depth_Router 분류에 LLM 호출이 필요하면, THE Depth_Router SHALL 그 호출을 Bedrock Gateway 경유로 수행한다.
5. WHERE Depth_Router 분류에 LLM 호출이 사용되면, THE Depth_Router SHALL 개별 `ainvoke` 호출 하나만 `asyncio.wait_for(<timeout>)` 로 감싸며 기본 타임아웃을 60초로 한다(조정 가능).

### Requirement 5: Fast-Path 실행

**User Story:** 사용자로서, 나는 단순 질의가 최소한의 LLM 왕복으로 처리되길 원한다. 그래야 지연이 대폭 감소한다.

#### Acceptance Criteria

1. WHILE 질의가 `simple` 로 분류된 상태이면, THE Depth_Router SHALL Fast_Path 로 응답을 생성한다.
2. WHEN Fast_Path 가 실행되면, THE Fast_Path SHALL planner·병렬 워커 fan-out·aggregate·evaluator 메타 노드를 실행하지 않는다.
3. WHEN Fast_Path 가 실행되면, THE Fast_Path SHALL 단일 도메인 서브그래프(retrieve → model → verify) 경로로 응답을 생성하며 도구 루프를 제외한 model 노드 LLM 왕복 수를 1회로 제한한다(도구 사용 시 기존 iteration cap 적용).
4. THE Fast_Path SHALL 기존 `astream_events` 스트리밍 계약과 응답 이벤트 형식을 변경 없이 유지한다.

### Requirement 6: Full-Graph 보존 (복잡 질의)

**User Story:** 사용자로서, 나는 복잡한 질의가 기존의 완전한 추론 파이프라인으로 처리되길 원한다. 그래야 다도메인·도구·근거 요구를 정확히 다룰 수 있다.

#### Acceptance Criteria

1. WHILE 질의가 `complex` 로 분류된 상태이면, THE Depth_Router SHALL 기존 Full_Graph 경로로 요청을 처리한다.
2. THE Full_Graph SHALL 기존 planner → 병렬 워커 → aggregate → evaluator 노드·엣지 구성을 변경 없이 유지한다.
3. WHERE 적응형 깊이 라우팅 플래그가 off 이면, THE Depth_Router SHALL 모든 질의를 Full_Graph 로 처리한다.

### Requirement 7: 근거성 강제 게이트 (측정 및 임계 판정)

**User Story:** 사용자로서, 나는 근거 없는 답변이 그대로 나가지 않길 원한다. 그래야 응답을 신뢰할 수 있다.

#### Acceptance Criteria

1. WHEN 최종 응답이 생성되면, THE Grounding_Gate SHALL 응답의 Grounding_Score 를 산출한다.
2. IF Grounding_Score 가 임계값 미만이면, THEN THE Grounding_Gate SHALL 해당 응답을 근거 미달로 판정한다.
3. THE Grounding_Gate SHALL 임계값 기본을 0.7 로 하고 환경변수(`AE_VERIFY_THRESHOLD`)로 조정 가능하게 한다.
4. IF Grounding_Score 산출이 실패(degraded)하면, THEN THE Grounding_Gate SHALL 근거 컨텍스트 존재 여부와 무관하게 근거 미달로 판정하지 않고 응답을 통과시킨다.
5. THE Grounding_Gate SHALL 기존 `faithfulness_below_threshold()` 및 `local_grounding_score()` 함수를 재사용한다.

### Requirement 8: Bounded Refine (재생성)

**User Story:** 사용자로서, 나는 근거 미달 응답이 자동으로 한 번 더 다듬어지길 원한다. 그래야 무한 대기 없이 품질이 개선된다.

#### Acceptance Criteria

1. WHEN Grounding_Gate 가 응답을 근거 미달로 판정하면, THE Grounding_Gate SHALL 최대 재생성 횟수 이내에서 Refine_Attempt 를 1회 유도한다.
2. THE Grounding_Gate SHALL 최대 재생성 횟수를 환경변수(`AE_MAX_REFINE`)로 정의하고 기본값을 1 로 한다.
3. WHILE 누적 Refine_Attempt 수가 최대 재생성 횟수 이상이면, THE Grounding_Gate SHALL 추가 Refine_Attempt 를 유도하지 않는다.
4. WHERE Refine_Attempt 에 LLM 호출이 사용되면, THE Grounding_Gate SHALL 그 호출을 Bedrock Gateway 경유로 수행한다.

### Requirement 9: 근거 부족 경고 및 거절

**User Story:** 사용자로서, 나는 재생성 후에도 근거가 부족하면 그 사실을 명확히 알고 싶다. 그래야 응답을 오해하지 않는다.

#### Acceptance Criteria

1. IF 최대 재생성 횟수 소진 후에도 Grounding_Score 가 임계값 미만이면, THEN THE Grounding_Gate SHALL 응답에 근거 부족 경고를 표기한다.
2. WHILE 근거 부족 경고가 표기된 상태이면, THE Grounding_Gate SHALL 응답 본문을 사용자에게 계속 제공한다(가용성 유지).
3. WHERE 거절 모드가 활성이면, THE Grounding_Gate SHALL 근거 부족 응답 대신 근거 부족 사유를 명시하는 응답을 반환한다.

### Requirement 10: 플래그 게이트 및 무회귀 보존

**User Story:** 개발자로서, 나는 신규 동작이 전부 플래그로 제어되고 기본 동작을 해치지 않길 원한다. 그래야 기존 LangGraph/RAG/vector 이점을 잃지 않는다.

#### Acceptance Criteria

1. THE Depth_Router SHALL 적응형 깊이 라우팅 동작을 전용 환경변수 플래그로 게이트하고 기본값을 off 로 한다.
2. THE Grounding_Gate SHALL 근거 강제 행동(재생성·경고·거절)을 전용 환경변수 플래그로 게이트하고 기본값을 off 로 한다.
3. WHERE 모든 신규 플래그가 off 이면, THE System SHALL 기존 노드·엣지·스트리밍 계약과 동일하게 동작한다.
4. THE System SHALL 기존 deferred 근거성 로깅 동작을 신규 플래그 off 상태에서 변경 없이 유지한다.

### Requirement 11: 불변 제약 (Gateway 전용·유한 종료·자격증명 미저장)

**User Story:** 개발자로서, 나는 모든 신규 동작이 기존 보안·안정성 불변 제약을 준수하길 원한다. 그래야 무한 대기·자격증명 유출·직접 SDK 호출이 발생하지 않는다.

#### Acceptance Criteria

1. THE System SHALL 모든 신규 LLM 호출을 Bedrock Gateway 경유로만 수행하며 직접 SDK(boto3/Anthropic/OpenAI) 호출을 사용하지 않는다.
2. THE System SHALL AWS 자격증명(accessKeyId, secretAccessKey, sessionToken)을 상태·체크포인트·Baseline_Record 어디에도 저장하지 않는다.
3. WHERE 신규 LLM 호출에 타임아웃이 적용되면, THE System SHALL 개별 `ainvoke` await 하나만 `asyncio.wait_for` 로 감싸며 스트림 소비 루프(`async for`)는 감싸지 않는다.
4. THE Depth_Router SHALL Fast_Path 실행을 기존 서브그래프의 iteration cap(`AE_SUBGRAPH_RECURSION`, 기본 25) 이내로 유한 종료한다.
5. THE Grounding_Gate SHALL Refine_Attempt 반복을 최대 재생성 횟수(`AE_MAX_REFINE`) 이내로 유한 종료한다.
6. THE System SHALL Reasoning_Meta_Node 에 스트리밍 지원 모델(Sonnet 계열)을 사용하고 스트리밍 미지원 모델(Opus)을 기본값으로 사용하지 않는다.
7. THE System SHALL 모델 학습·파인튜닝을 수행하지 않는다.

### Requirement 12: Phase 진행의 실증 게이트

**User Story:** 개발자로서, 나는 각 Phase 가 eval 하네스로 개선을 실증한 뒤에만 다음 Phase 로 진행되길 원한다. 그래야 고도화가 근거 없이 누적되지 않는다.

#### Acceptance Criteria

1. THE System SHALL Phase 2a(적응형 깊이) 진행 전 Phase 1 Eval_Harness 로 지연·근거성·정확성 baseline 을 기록한다.
2. WHEN Phase 2a 가 구현되면, THE System SHALL Eval_Harness 로 단순 질의 지연 감소를 baseline 대비 수치로 실증한다.
3. WHEN Phase 2b 가 구현되면, THE System SHALL Eval_Harness 로 근거성 지표 개선을 baseline 대비 수치로 실증한다.
4. THE System SHALL 각 Phase 의 개선 실증에 근거 미달·회귀가 확인되면 다음 Phase 진행 전 해당 Phase 를 보완하도록 한다.

### Requirement 13: 범위 제한

**User Story:** 개발자로서, 나는 이 스펙의 범위가 명확하길 원한다. 그래야 관련 없는 작업이 섞이지 않는다.

#### Acceptance Criteria

1. THE System SHALL MCP 브로커 노출을 이 스펙의 범위에서 제외한다(후속 별도 스펙).
2. THE System SHALL 지연·근거성 개선을 위한 신규 동작을 Eval_Harness, Depth_Router, Grounding_Gate 세 축으로 한정한다.
