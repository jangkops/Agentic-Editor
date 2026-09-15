# Requirements Document

## Introduction

`gateway-models-effort-support`는 완료된 `gateway-openai-models` 기능의 후속 기능이다. 기존 모델 목록, Gateway 라우팅, OpenAI Responses 어댑터, SigV4 인증, job polling, retry 및 오류 처리를 별도 체계로 다시 만들지 않고, **실제 Gateway 근거에 기반한 모델 활성화 게이트와 exact model·route별 effort capability**를 추가한다.

사용자가 제시한 `opus 5`, `sonnet 5`, `gpt 5.6`, `sol`, `terra`, `luna`는 검색용 **Candidate_Label**로만 취급한다. Candidate_Label의 표기만으로 model ID, provider, 모델 계열 관계, route, effort field path 또는 effort value를 추론하지 않는다. 특히 `gpt 5.6`, `sol`, `terra`, `luna` 사이의 관계는 공유하거나 상속하지 않으며, 실제 Gateway 근거가 각 관계를 명시하기 전까지 네 개의 독립 검색 항목으로 유지한다.

### 현재 저장소 기준선

이 문서의 기준선은 git revision `bafb45153835c4b6366e877f41c84c658bf99074`, UTC 검사 시각 `2026-08-03T01:53:36Z`에서 확인했다. 아래 표는 요구사항 작성 근거가 아니라, 이후 검증 실행이 다시 확인해야 하는 코드 위치 기록이다.

| Baseline_Category | 현재 file + symbol 또는 미발견 결과 | 현재 의미 |
|---|---|---|
| catalog | `ai_engine/server.py::list_models`; `ai_engine/openai_catalog.py::FileCatalogSource.list_models`; `ai_engine/openai_catalog.py::GatewayListSource.list_models` | `/api/models`는 Bedrock control-plane 결과와 파일 기반 OpenAI seed를 병합한다. 현재 유효한 `GatewayListSource.list_models`는 빈 목록을 반환하므로 실제 Gateway catalog 조회 구현은 미발견이다. |
| allowlist | 조회 symbol 미발견; 관찰 경로는 `ai_engine/server.py::_model_is_denied`, `_record_denied_model`, `_maybe_record_denied_from_error` | denylist 부재는 허용 근거가 아니며, 실제 route 성공 또는 명시적 allowlist 거부만 판정 근거가 된다. |
| Gateway client | `ai_engine/gateway_module.py::GatewayClient`, `_get_creds`, `inject_credentials`, `_sign` | runtime credential, `BedrockUser-{name}` assume-role, 5분 cache 및 execute-api SigV4를 사용한다. |
| route | `ai_engine/server.py::is_openai_model`, `route_openai_chat`, `_resolve_callable_model_id`; `GatewayClient.converse`, `invoke_model`, `openai_responses_sync`, `openai_responses_job_submit_and_poll`, `stream_sse_realtime` | 현재 provider/prefix 기반 분기와 production route가 존재하지만 Candidate_Model의 route 근거로 자동 승격할 수 없다. |
| request builder | `GatewayClient._build_payload`, `_build_openai_payload`, `openai_responses_job_submit` | Converse는 `modelId/messages/inferenceConfig`, 동기 Responses는 `model/input`과 선택 표준 필드, 현재 jobs 구현은 top-level `modelId`를 구성한다. Candidate_Model 계약은 production builder의 성공 probe로 확인해야 한다. |
| model selection | `src/main.js::rebuildModelList`, `loadModelsFromServer`, `state.selectedModel`; `ai_engine/server.py::_resolve_callable_model_id` | 현재 catalog를 평탄화해 첫 chat 모델을 선택하고 backend가 호출 ID를 조정한다. capability tuple 기반 선택은 미발견이다. |
| response adapter | `ai_engine/openai_adapter.py::to_converse`, `extract_text`, `extract_tool_calls`, `extract_usage` | OpenAI Responses 결과를 기존 Converse 형식으로 변환하며 빈 유효 output은 오류로 처리한다. |
| job polling | `GatewayClient._poll_job_data`, `_poll_job_result`, `_openai_poll_job` | Converse async 결과와 OpenAI Responses jobs terminal 상태를 기존 흐름에서 polling한다. |
| retry | `GatewayClient.converse`, `converse_stream_live`, `stream_sse_realtime`, `_openai_post_with_retry` | credential refresh, transient retry 및 양방향 prefix 교정 1회가 기존 규칙으로 존재한다. |
| error handling | `ai_engine/gateway_module.py::QuotaExceededError`, `OpenAISurfaceError`, `SyncTimeout`, `JobTimeout`, `JobFailed`, `OpenAIModelUnsupported`; `ai_engine/server.py::_maybe_record_denied_from_error` | 개별 오류 처리는 존재하지만 이번 기능의 결정론적 capability 분류기는 미발견이다. |

추가 확인 결과는 다음과 같다.

- `ai_engine/openai_catalog.py`의 `DEFAULT_SEED_MODELS`와 파일 fallback은 현재 Candidate_Model의 가용성 근거가 아니다.
- `scripts/probe_full_catalog_via_gateway.py`는 Bedrock catalog 중심 probe이며 Candidate_Label, OpenAI Responses effort 계약 또는 이번 Activation_Gate를 검증하지 않는다.
- `scripts/test_gateway_openai_methods.py`의 mock 성공은 실제 Gateway 지원 근거가 아니다.
- 저장소에서 이번 여섯 Candidate_Label의 exact model ID, provider, route, effort field path 및 effort domain을 입증하는 완전한 Verification_Record는 발견되지 않았다.
- 현재 frontend/backend에서 exact model ID + route + Capability_Fingerprint tuple에 결속된 effort 설정 경로는 발견되지 않았다.

이 기준선은 일부 category가 미구현 또는 미발견이므로 그 자체로 Authoritative_Evidence가 아니다. 모든 Candidate_Label의 초기 상태는 `UNVERIFIED`이며, 실제 Gateway catalog와 production request path의 최소 검증을 통과한 Candidate_Model만 Active_Model이 될 수 있다.

## Scope Boundary

### 포함 범위

- catalog, allowlist, Gateway client, route, request builder, model selection, response adapter, job polling, retry 및 error handling의 재현 가능한 기준선 기록
- 동일 Gateway 환경의 실제 catalog 또는 엄격히 검증된 운영자 export를 통한 exact model ID와 provider 식별
- candidate별 sync, async, streaming route 및 effort capability 검증
- 검증 근거와 canonical fingerprint를 포함한 Capability_Map
- 완전하고 현재인 `VERIFIED` 모델만 노출하는 Activation_Gate
- exact model·route·fingerprint tuple 기반 effort UI, 설정 정리 및 request field 주입
- 결정론적 Gateway 오류 분류, allowlist 거부, effort mismatch 및 verified fallback
- SigV4, runtime credential, 비밀정보 비저장, 정제된 evidence 및 `userData` 한정 영속화
- 최소 비용 실제 Gateway 검증, pure logic property-based testing 및 기존 기능 무회귀

### 제외 범위

- Candidate_Label에서 model ID, provider, 모델 계열, route, field path 또는 effort value를 유추하는 규칙
- Gateway에서 발견되지 않은 모델 또는 `UNVERIFIED` 모델의 선등록·활성화
- 과거 사양, 표시명, 코드 주석, 파일 seed, mock 또는 unit/PBT 성공만으로 Gateway 지원을 주장하는 작업
- 기존 `gateway-openai-models`의 Gateway client, response adapter, job polling 및 공통 retry를 별도 구현으로 복제하는 작업
- Gateway를 우회하는 LLM 직접 호출
- 실제 Gateway가 제공하지 않은 usage 또는 cost의 추정

## Glossary

- **Gateway_Model_Support**: 이 기능 전체를 가리키는 시스템. baseline, evidence, capability, activation, effort, request 및 failure 처리를 조정한다.
- **Existing_Gateway_Integration**: 현재 저장소의 Gateway client, catalog merge, route, request body, response adapter, job polling, retry 및 error handling 흐름.
- **Candidate_Label**: 검색 라벨 `opus 5`, `sonnet 5`, `gpt 5.6`, `sol`, `terra`, `luna`. Candidate_Label 자체는 model identity가 아니다.
- **Candidate_Model**: Authoritative_Evidence가 Candidate_Label과 exact Gateway model record의 관계를 입증한 모델.
- **Existing_Model**: 이번 기능 이전에 production request path의 실제 Gateway 성공 근거가 있는 모델. 검증되지 않은 seed는 제외한다.
- **Seed_Entry**: 실제 Gateway 근거 없이 파일, 상수 또는 fallback으로 제공된 catalog entry.
- **Exact_Model_ID**: Gateway_Catalog가 반환한 대소문자와 문자를 변경하지 않은 model ID.
- **Invocation_Model_ID**: production prefix 교정 규칙을 포함해 실제 Gateway 호출 body에 기록된 model ID. Exact_Model_ID와 다르면 별도 보존한다.
- **Provider_String**: Gateway_Catalog가 반환한 대소문자와 문자를 변경하지 않은 provider 값.
- **Same_Gateway_Environment**: 검증 대상과 동일한 Gateway environment identifier, configured endpoint identity 및 AWS region 조합.
- **Gateway_Catalog**: Same_Gateway_Environment가 반환한 모델 catalog. Bedrock control-plane catalog만으로는 Gateway_Catalog가 되지 않는다.
- **Operator_Catalog_Export**: Gateway catalog endpoint 부재 시 운영자가 Same_Gateway_Environment에서 생성한 정제 export. environment identity, UTC 생성·수집 시각, canonical fingerprint 및 sanitization manifest를 포함한다.
- **Catalog_Fingerprint**: identity·provider·advertised route·capability 필드를 canonical form으로 계산한 catalog 변경 감지 값.
- **Gateway_Allowlist**: Same_Gateway_Environment에서 Exact_Model_ID와 route 조합의 호출을 허용하거나 거부하는 정책.
- **Allowlist_Result**: 실제 route의 Valid_Output 성공으로 얻는 `ALLOWED`, 명시적 allowlist 거부로 얻는 `REJECTED`, 그 외의 `UNVERIFIED` 중 하나.
- **Gateway_Probe**: Runtime_Credential_Manager와 production request path를 사용해 실제 Gateway route에 Minimal_Request를 전송하는 검증 호출.
- **Minimal_Request**: 고정된 짧은 비민감 입력과 검증된 Route_Contract가 허용하는 최소 output/token bound를 사용하는 요청.
- **Probe_ID**: prompt 원문 대신 검증 입력을 식별하는 비민감 고정 식별자.
- **Authoritative_Evidence**: Same_Gateway_Environment의 정제된 Gateway_Catalog, 유효한 Operator_Catalog_Export 또는 production request path의 정제된 실제 Gateway_Probe 결과. 과거 사양, 표시명, 주석, seed 및 mock은 단독 근거가 아니다.
- **Baseline_Category**: `catalog`, `allowlist`, `gateway-client`, `route`, `request-builder`, `model-selection`, `response-adapter`, `job-polling`, `retry`, `error-handling` 중 하나.
- **Baseline_Record**: 각 Baseline_Category의 file path, symbol 또는 미발견 결과, git revision 및 UTC 검사 시각을 담은 기록.
- **Repository_Baseline_Inspector**: 현재 repository에서 Baseline_Record를 생성하는 구성요소.
- **Evidence_Collector**: catalog와 Gateway_Probe 결과를 수집·정제해 Verification_Record를 생성하는 구성요소.
- **Verification_Record**: validation run ID, UTC 시각, git revision, interpreter, Same_Gateway_Environment identity, Catalog_Fingerprint, Capability_Fingerprint, Exact_Model_ID, Provider_String, Invocation_Model_ID, route·effort 결과, 정제 schema 및 evidence reference를 담은 기록.
- **Evidence_Record_ID**: 정제된 Verification_Record의 불변 필드로 계산한 결정론적 식별자.
- **Verification_Status**: `UNVERIFIED`, `DISCOVERED`, `VERIFIED`, `REJECTED`, `STALE` 중 하나.
- **Route_Support_Status**: `SUPPORTED`, `UNSUPPORTED`, `UNVERIFIED`, `NOT_ADVERTISED` 중 하나.
- **Effort_Support_Status**: `SUPPORTED`, `UNSUPPORTED`, `UNVERIFIED`, `STALE` 중 하나.
- **Known_Route**: `/converse`, `/invoke`, `/openai/responses`, `/openai/responses-jobs`, `SSE_Stream_Route` 중 하나.
- **SSE_Stream_Route**: Lambda Function URL을 사용하고 SigV4 service `lambda`로 서명하는 기존 streaming route.
- **Route_Contract**: Exact_Model_ID와 Known_Route 조합의 endpoint identifier, HTTP method, execution mode, exact request field schema, output validator, terminal condition, retry/fallback metadata 및 evidence reference를 정의한 계약.
- **Effort_Contract**: Exact_Model_ID와 Route_Contract 조합의 exact field path, value type, complete enum 또는 inclusive range boundary 및 evidence reference를 정의한 계약.
- **Valid_Output**: production response adapter가 route별 schema에 따라 소비할 수 있는 비어 있지 않은 결과.
- **Terminal_Success**: sync completion, async terminal success, polling 완료 또는 streaming terminal event처럼 Route_Contract가 정의한 최종 성공 상태.
- **Capability_Map**: Exact_Model_ID별 route, derived execution-mode 상태, effort 계약, 상태 및 Verification_Record를 보관하는 정규화된 데이터 집합.
- **Capability_Canonicalizer**: Capability_Map의 fingerprint 생성, 결정론적 정렬 및 serialization을 담당하는 순수 구성요소.
- **Capability_Fingerprint**: model identity, provider, route 계약·상태 및 effort 계약·상태의 canonical form으로 계산한 계약 변경 감지 값.
- **Complete_Record**: 필수 schema, 모든 Known_Route 상태, 모든 `SUPPORTED` 계약의 evidence 및 현재 identity·revision·fingerprint가 채워진 Verification_Record.
- **Malformed_Entry**: 필수 필드 누락, 잘못된 type, 허용되지 않은 enum, fingerprint 불일치 또는 참조 무결성 실패가 있는 Capability_Map entry.
- **Current_Revision**: validation 시작 시 기록한 repository의 git revision.
- **Current_Evidence**: Same_Gateway_Environment, Current_Revision, 현재 Catalog_Fingerprint 및 현재 Capability_Fingerprint와 일치하는 Verification_Record.
- **Activation_Gate**: Capability_Map entry가 Active_Model 조건을 충족하는지 판정하는 순수 구성요소.
- **Active_Model**: Activation_Gate를 통과해 선택 가능한 모델 목록에 노출되는 모델.
- **Editor_Model_Catalog**: `/api/models`와 Capability_Map을 바탕으로 에디터가 표시하는 모델 집합.
- **Request_Router**: Exact_Model_ID와 verified Route_Contract로만 route와 fallback을 선택하는 구성요소.
- **Request_Builder**: 기존 production builder를 확장해 Route_Contract와 Effort_Contract에 맞는 Gateway body를 생성하는 구성요소.
- **Baseline_Request_Body**: 동일한 model, route 및 user input에서 effort 선택만 제거해 production Request_Builder가 생성한 body.
- **Production_Request_Path**: 실제 제품의 Request_Builder, GatewayClient transport, response adapter 및 job polling을 함께 사용하는 호출 경로.
- **Fallback_Order**: Capability_Map에 명시되고 evidence로 검증된 Route_Contract 우선순위.
- **Eligible_Contract**: 현재 `SUPPORTED`, Current_Evidence 보유, 요청 목적 충족 및 allowlist `ALLOWED`를 모두 만족하는 Route_Contract.
- **Effort_Control**: Active_Model의 effort UI 표시와 request 전달을 조정하는 구성요소.
- **Effort_Settings**: Exact_Model_ID, Known_Route, Capability_Fingerprint 및 선택 effort value의 tuple로 저장되는 사용자 설정 데이터.
- **Effort_Settings_Manager**: Effort_Settings의 저장, 복원, 무효화 및 제거를 담당하는 구성요소.
- **Model_Selection_Manager**: catalog 또는 capability 변경 후 model 및 effort 선택을 유효한 상태로 복구하는 구성요소.
- **Failure_Category**: `authentication`, `allowlist`, `effort-mismatch`, `route-capability-mismatch`, `quota`, `transient`, `request-validation`, `unknown` 중 하나.
- **Failure_Precedence**: 여러 신호가 겹칠 때 `authentication` → `allowlist` → `effort-mismatch` → `route-capability-mismatch` → `quota` → `transient` → `request-validation` → `unknown` 순서로 첫 일치 범주를 선택하는 규칙.
- **Failure_Handler**: Gateway 오류를 Failure_Precedence로 분류하고 상태, retry, fallback 및 사용자 표시를 조정하는 구성요소.
- **User_Notification**: 원 model, route, Failure_Category, retry 및 fallback 결과를 비밀정보 없이 표시하는 구성요소.
- **Transient_Error**: timeout, 연결 실패, HTTP 429 또는 HTTP 5xx처럼 지원·allowlist 결과를 확정하지 않는 오류.
- **Capability_Mismatch**: Gateway가 이전에 검증된 route, effort field path 또는 effort value를 현재 계약에서 명시적으로 거부한 상태.
- **Runtime_Credential_Manager**: runtime 주입 credential 또는 `BedrockUser-{name}` assume-role credential을 5분 cache하고 만료 시 갱신하는 기존 구성요소.
- **Sanitized_Schema**: credential, authorization, cookie, signature, raw prompt 및 raw response body를 제외하고 field name, type, cardinality와 필요한 status만 보존한 request/response 구조.
- **Validation_Run**: 하나의 Current_Revision, interpreter, Same_Gateway_Environment 및 시작 UTC 시각에 결속된 검증 실행.
- **Validation_Runner**: 지정 interpreter로 baseline 검사, Gateway_Probe 및 자동화 테스트를 실행하고 보고서를 만드는 구성요소.
- **Regression_Suite**: 기존 model, route, body, signature, adapter, polling, retry 및 capability pure logic의 무회귀를 검증하는 테스트 집합.

## Requirements

### Requirement 1: 기존 기능의 연속성과 완전한 기준선

**User Story:** As a maintainer, I want a revision-bound baseline of every existing Gateway integration surface, so that incomplete observations cannot be promoted as support evidence.

#### Acceptance Criteria

1. THE Repository_Baseline_Inspector SHALL catalog 구현의 file path와 symbol 또는 미발견 결과를 Baseline_Record에 기록한다.
2. THE Repository_Baseline_Inspector SHALL allowlist 구현의 file path와 symbol 또는 미발견 결과를 Baseline_Record에 기록한다.
3. THE Repository_Baseline_Inspector SHALL Gateway client 구현의 file path와 symbol 또는 미발견 결과를 Baseline_Record에 기록한다.
4. THE Repository_Baseline_Inspector SHALL route 구현의 file path와 symbol 또는 미발견 결과를 Baseline_Record에 기록한다.
5. THE Repository_Baseline_Inspector SHALL request builder 구현의 file path와 symbol 또는 미발견 결과를 Baseline_Record에 기록한다.
6. THE Repository_Baseline_Inspector SHALL model selection 구현의 file path와 symbol 또는 미발견 결과를 Baseline_Record에 기록한다.
7. THE Repository_Baseline_Inspector SHALL response adapter 구현의 file path와 symbol 또는 미발견 결과를 Baseline_Record에 기록한다.
8. THE Repository_Baseline_Inspector SHALL job polling 구현의 file path와 symbol 또는 미발견 결과를 Baseline_Record에 기록한다.
9. THE Repository_Baseline_Inspector SHALL retry 구현의 file path와 symbol 또는 미발견 결과를 Baseline_Record에 기록한다.
10. THE Repository_Baseline_Inspector SHALL error handling 구현의 file path와 symbol 또는 미발견 결과를 Baseline_Record에 기록한다.
11. THE Repository_Baseline_Inspector SHALL Current_Revision을 모든 Baseline_Record에 기록한다.
12. THE Repository_Baseline_Inspector SHALL 검사 시각을 UTC ISO 8601 형식으로 모든 Baseline_Record에 기록한다.
13. THE Gateway_Model_Support SHALL Existing_Gateway_Integration의 Gateway client, response adapter, job polling 및 retry 흐름을 재사용한다.
14. THE Gateway_Model_Support SHALL 여섯 Candidate_Label 각각에 정확히 하나의 초기 `UNVERIFIED` entry를 생성한다.
15. WHEN Candidate_Label만 제공되면, THE Evidence_Collector SHALL model ID, provider, route 및 effort 값을 미확정 상태로 유지한다.
16. IF Baseline_Category 하나라도 file+symbol 또는 명시적 미발견 결과를 포함하지 않으면, THEN THE Evidence_Collector SHALL 해당 baseline을 Authoritative_Evidence에서 제외한다.
17. IF 근거가 과거 사양, 표시명, 코드 주석, Seed_Entry 또는 mock으로만 구성되면, THEN THE Evidence_Collector SHALL Verification_Status를 `UNVERIFIED`로 유지한다.

### Requirement 2: 동일 환경의 exact model 발견과 allowlist 판정

**User Story:** As an operator, I want model identity and allowlist results to come from the same Gateway environment, so that guessed or cross-environment identifiers cannot produce requests.

#### Acceptance Criteria

1. WHEN model discovery를 시작하면, THE Evidence_Collector SHALL Same_Gateway_Environment의 Gateway_Catalog를 Runtime_Credential_Manager로 조회한다.
2. WHEN Gateway_Catalog가 Candidate_Label과 model record의 관계를 제공하면, THE Evidence_Collector SHALL Exact_Model_ID를 변경 없이 기록한다.
3. WHEN Gateway_Catalog가 Candidate_Label과 model record의 관계를 제공하면, THE Evidence_Collector SHALL Provider_String을 변경 없이 기록한다.
4. IF Gateway_Catalog가 Candidate_Label과 exact model record의 관계를 제공하지 않으면, THEN THE Evidence_Collector SHALL 해당 Candidate_Label을 `UNVERIFIED`로 유지한다.
5. IF Gateway catalog endpoint가 존재하지 않으면, THEN THE Evidence_Collector SHALL Operator_Catalog_Export만 대체 catalog 근거로 허용한다.
6. WHEN Operator_Catalog_Export를 평가하면, THE Evidence_Collector SHALL export의 environment identity와 region이 Same_Gateway_Environment와 정확히 일치하는지 검증한다.
7. WHEN Operator_Catalog_Export를 평가하면, THE Evidence_Collector SHALL 생성 시각과 수집 시각이 UTC ISO 8601이고 생성 시각이 수집 시각보다 늦지 않은지 검증한다.
8. WHEN Operator_Catalog_Export를 평가하면, THE Evidence_Collector SHALL export 내용에서 Catalog_Fingerprint를 재계산해 제공된 fingerprint와 비교한다.
9. WHEN Operator_Catalog_Export를 평가하면, THE Evidence_Collector SHALL sanitization manifest가 credential 관련 field만 제거했는지 검증한다.
10. IF Operator_Catalog_Export의 model identity, provider, route 또는 capability field가 정제 과정에서 변경되면, THEN THE Evidence_Collector SHALL export를 Authoritative_Evidence에서 제외한다.
11. IF Operator_Catalog_Export 검증 조건 하나라도 실패하면, THEN THE Evidence_Collector SHALL 관련 Candidate_Label을 `UNVERIFIED`로 유지한다.
12. IF Exact_Model_ID가 Authoritative_Evidence에 없으면, THEN THE Evidence_Collector SHALL 해당 Candidate_Label의 Gateway_Probe를 생성하지 않는다.
13. WHEN Exact_Model_ID와 route의 Production_Request_Path가 Valid_Output을 반환하면, THE Evidence_Collector SHALL 해당 조합의 Allowlist_Result를 `ALLOWED`로 기록한다.
14. IF Gateway가 Exact_Model_ID와 route의 allowlist 거부를 명시적으로 반환하면, THEN THE Evidence_Collector SHALL 해당 조합의 Allowlist_Result를 `REJECTED`로 기록한다.
15. WHILE Gateway 결과가 Valid_Output 성공 또는 명시적 allowlist 거부가 아닌 경우, THE Evidence_Collector SHALL Allowlist_Result를 `UNVERIFIED`로 유지한다.
16. IF 기존 production prefix 규칙이 model ID 형태 교정을 요구하면, THEN THE Evidence_Collector SHALL 동일 route의 prefix 교정을 최대 1회 수행한다.
17. WHEN prefix 교정 요청을 전송하면, THE Evidence_Collector SHALL Invocation_Model_ID를 Exact_Model_ID와 별도 field에 기록한다.
18. IF `gpt 5.6`, `sol`, `terra`, `luna`의 관계가 Authoritative_Evidence에 명시되지 않으면, THEN THE Evidence_Collector SHALL 네 Candidate_Label 사이에 provider, model family, route 또는 effort 정보를 공유하지 않는다.

### Requirement 3: 완전하고 결정론적인 Capability Map

**User Story:** As a developer, I want an auditable capability map with deterministic fingerprints, so that activation, routing and UI decisions can be reproduced.

#### Acceptance Criteria

1. THE Capability_Map SHALL 각 Candidate_Model에 `schemaVersion`, `candidateLabel`, `modelId`, `invocationModelIds`, `provider`, `catalogFingerprint`, `routes`, `syncSupport`, `asyncSupport`, `streamingSupport`, `effort`, `verifiedAt`, `revision`, `evidence`, `verificationStatus` 및 `capabilityFingerprint`를 포함한다.
2. THE Capability_Map SHALL 각 Known_Route에 Route_Support_Status를 포함한다.
3. THE Capability_Map SHALL 각 `SUPPORTED` route에 완전한 Route_Contract를 포함한다.
4. THE Capability_Map SHALL 각 `SUPPORTED` route에 Current_Evidence reference를 포함한다.
5. THE Capability_Map SHALL effort entry에 Effort_Support_Status를 포함한다.
6. THE Capability_Map SHALL `SUPPORTED` effort entry에 완전한 Effort_Contract를 포함한다.
7. THE Capability_Map SHALL `SUPPORTED` effort entry에 Current_Evidence reference를 포함한다.
8. THE Capability_Map SHALL Verification_Status 값을 `UNVERIFIED`, `DISCOVERED`, `VERIFIED`, `REJECTED`, `STALE`로 제한한다.
9. THE Capability_Map SHALL Route_Support_Status 값을 `SUPPORTED`, `UNSUPPORTED`, `UNVERIFIED`, `NOT_ADVERTISED`로 제한한다.
10. THE Capability_Map SHALL Effort_Support_Status 값을 `SUPPORTED`, `UNSUPPORTED`, `UNVERIFIED`, `STALE`로 제한한다.
11. THE Capability_Map SHALL 모든 evidence 시각을 UTC ISO 8601 형식으로 저장한다.
12. THE Capability_Canonicalizer SHALL Exact_Model_ID, Provider_String, Catalog_Fingerprint, Known_Route별 상태와 Route_Contract, effort 상태와 Effort_Contract 및 schema version을 Capability_Fingerprint 입력에 포함한다.
13. THE Capability_Canonicalizer SHALL Candidate_Label, display name, UTC 시각, git revision, evidence 저장 위치, Evidence_Record_ID, 로그 및 Sanitized_Schema를 Capability_Fingerprint 입력에서 제외한다.
14. THE Capability_Canonicalizer SHALL object key와 집합형 collection을 정렬한 canonical serialization으로 Capability_Fingerprint를 생성한다.
15. WHEN Capability_Map을 serialize한 뒤 deserialize하면, THE Capability_Canonicalizer SHALL model identity, provider, route 계약·상태, effort 계약·상태 및 evidence reference의 의미를 보존한다.
16. WHEN Capability_Canonicalizer를 같은 entry에 반복 적용하면, THE Capability_Canonicalizer SHALL 첫 적용과 동일한 canonical serialization을 반환한다.
17. IF entry가 Malformed_Entry이면, THEN THE Capability_Map SHALL 해당 entry를 activation 입력에서 제외한다.
18. WHEN 동일 Exact_Model_ID와 동일 Capability_Fingerprint의 복수 evidence가 존재하면, THE Capability_Map SHALL 가장 늦은 `verifiedAt`의 evidence를 활성 evidence로 선택한다.
19. WHEN 동일 Exact_Model_ID와 동일 Capability_Fingerprint의 복수 evidence가 같은 `verifiedAt`을 가지면, THE Capability_Map SHALL Evidence_Record_ID의 오름차순 첫 record를 활성 evidence로 선택한다.
20. THE Capability_Map SHALL `syncSupport`를 execution mode가 sync인 Known_Route 상태에서 유도한다.
21. THE Capability_Map SHALL `asyncSupport`를 execution mode가 async인 Known_Route 상태에서 유도한다.
22. THE Capability_Map SHALL `streamingSupport`를 execution mode가 streaming인 Known_Route 상태에서 유도한다.
23. WHEN execution mode별 상태를 유도하면, THE Capability_Map SHALL `SUPPORTED`, `UNVERIFIED`, `UNSUPPORTED`, `NOT_ADVERTISED` 순서의 우선순위를 적용한다.

### Requirement 4: production path를 통한 route 검증

**User Story:** As a user, I want every exposed route to be proven through the production request and response path, so that catalog presence or diagnostic code cannot imply callability.

#### Acceptance Criteria

1. WHEN Candidate_Model이 `DISCOVERED`가 되면, THE Evidence_Collector SHALL Gateway_Catalog가 광고한 각 Known_Route에 Minimal_Request를 준비한다.
2. WHEN Gateway_Probe를 생성하면, THE Evidence_Collector SHALL production Request_Builder로 request body를 생성한다.
3. WHEN Gateway_Probe 응답을 판정하면, THE Evidence_Collector SHALL production response adapter와 production job polling을 사용한다.
4. IF diagnostic code 또는 수동 body가 production Request_Builder와 다르면, THEN THE Evidence_Collector SHALL 해당 결과를 activation evidence에서 제외한다.
5. WHEN route 응답을 수신하면, THE Evidence_Collector SHALL HTTP 성공 상태를 독립 결과로 기록한다.
6. WHEN route 응답을 수신하면, THE Evidence_Collector SHALL Valid_Output 판정 결과를 독립 결과로 기록한다.
7. WHEN route 응답을 수신하면, THE Evidence_Collector SHALL Terminal_Success 판정 결과를 독립 결과로 기록한다.
8. WHEN HTTP 성공, Valid_Output 및 Terminal_Success가 모두 확인되면, THE Evidence_Collector SHALL 해당 route를 `SUPPORTED`로 기록한다.
9. WHEN `/converse`가 `ALLOW`와 Valid_Output을 반환하면, THE Evidence_Collector SHALL `/converse`의 Terminal_Success를 충족된 것으로 기록한다.
10. WHEN `/converse`가 `ACCEPTED`를 반환하면, THE Evidence_Collector SHALL production job polling의 terminal success와 Valid_Output을 모두 확인한다.
11. WHEN `/invoke`가 동기 결과를 반환하면, THE Evidence_Collector SHALL production adapter가 소비 가능한 output을 Valid_Output으로 판정한다.
12. WHEN `/invoke`가 async handoff를 반환하면, THE Evidence_Collector SHALL production job polling의 terminal success와 Valid_Output을 모두 확인한다.
13. WHEN `/openai/responses`가 완료 응답을 반환하면, THE Evidence_Collector SHALL production OpenAI adapter의 Valid_Output을 확인한다.
14. WHEN `/openai/responses-jobs`를 검증하면, THE Evidence_Collector SHALL 제출 성공, job ID, terminal success 및 Valid_Output을 각각 기록한다.
15. WHEN SSE_Stream_Route를 검증하면, THE Evidence_Collector SHALL content event와 terminal event를 각각 기록한다.
16. IF Known_Route가 Gateway_Catalog 또는 검증된 route advertisement에 없으면, THEN THE Evidence_Collector SHALL 해당 route를 `NOT_ADVERTISED`로 기록한다.
17. IF Known_Route가 `NOT_ADVERTISED`이면, THEN THE Evidence_Collector SHALL 해당 route에 Gateway_Probe를 전송하지 않는다.
18. IF Gateway가 unknown model 또는 unsupported route를 명시적으로 반환하면, THEN THE Evidence_Collector SHALL 해당 route를 `UNSUPPORTED`로 기록한다.
19. IF Gateway가 allowlist 거부를 명시적으로 반환하면, THEN THE Evidence_Collector SHALL Candidate_Model을 `REJECTED`로 기록한다.
20. WHILE Gateway_Probe가 Transient_Error인 경우, THE Evidence_Collector SHALL 해당 route를 `UNVERIFIED`로 유지한다.
21. IF Gateway_Probe가 empty output을 반환하면, THEN THE Evidence_Collector SHALL 해당 route를 `UNVERIFIED`로 유지한다.
22. IF Gateway_Probe가 partial output만 반환하면, THEN THE Evidence_Collector SHALL 해당 route를 `UNVERIFIED`로 유지한다.
23. IF Gateway_Probe가 terminal timeout으로 끝나면, THEN THE Evidence_Collector SHALL 해당 route를 `UNVERIFIED`로 유지한다.
24. THE Evidence_Collector SHALL route baseline 검증당 성공 generation을 1회로 제한한다.
25. IF Gateway가 명시적이고 교정 가능한 request validation을 반환하면, THEN THE Evidence_Collector SHALL 같은 route baseline 검증의 교정 요청을 최대 1회 전송한다.
26. THE Evidence_Collector SHALL Minimal_Request의 output/token bound를 현재 Route_Contract가 허용하는 최소값으로 설정한다.
27. WHEN Candidate_Model의 모든 Known_Route 상태가 채워지면, THE Evidence_Collector SHALL route 상태 완전성을 Verification_Record에 기록한다.
28. WHEN Exact_Model_ID, Provider_String, 하나 이상의 `SUPPORTED` route 및 Complete_Record가 Current_Evidence와 일치하면, THE Evidence_Collector SHALL Candidate_Model을 `VERIFIED`로 기록한다.

### Requirement 5: exact model·route별 effort 계약 검증

**User Story:** As a user, I want effort controls only for exact model-route combinations with complete authoritative domains, so that guessed fields or partial probes cannot break requests.

#### Acceptance Criteria

1. WHEN Authoritative_Evidence가 effort capability를 광고하면, THE Evidence_Collector SHALL Exact_Model_ID와 Known_Route 조합을 Effort_Contract에 기록한다.
2. WHEN Authoritative_Evidence가 effort capability를 광고하면, THE Evidence_Collector SHALL exact field path를 Effort_Contract에 기록한다.
3. WHEN Authoritative_Evidence가 effort capability를 광고하면, THE Evidence_Collector SHALL exact value type을 Effort_Contract에 기록한다.
4. WHERE effort domain이 enum인 경우, THE Evidence_Collector SHALL Authoritative_Evidence가 제공한 complete enum을 Effort_Contract에 기록한다.
5. WHERE effort domain이 range인 경우, THE Evidence_Collector SHALL Authoritative_Evidence가 제공한 inclusive lower boundary와 upper boundary를 Effort_Contract에 기록한다.
6. IF effort field path, value type 또는 complete domain이 누락되면, THEN THE Evidence_Collector SHALL Effort_Support_Status를 `UNVERIFIED`로 유지한다.
7. WHEN effort 검증을 시작하면, THE Evidence_Collector SHALL 동일 Exact_Model_ID와 Known_Route의 effort 없는 Baseline_Request_Body 성공을 먼저 확인한다.
8. IF effort 없는 Baseline_Request_Body가 성공하지 않으면, THEN THE Evidence_Collector SHALL effort Gateway_Probe를 전송하지 않는다.
9. WHERE Effort_Contract domain이 enum인 경우, THE Evidence_Collector SHALL 광고된 각 enum value를 각각 하나의 Minimal_Request로 검증한다.
10. WHERE Effort_Contract domain이 range이고 양 경계가 다른 경우, THE Evidence_Collector SHALL lower boundary와 upper boundary를 각각 하나의 Minimal_Request로 검증한다.
11. WHERE Effort_Contract domain이 range이고 양 경계가 같은 경우, THE Evidence_Collector SHALL 해당 boundary를 하나의 Minimal_Request로 검증한다.
12. WHEN effort Gateway_Probe가 성공하면, THE Evidence_Collector SHALL 실제 field path와 실제 value를 Verification_Record에 기록한다.
13. WHEN Effort_Contract가 요구하는 모든 검증이 성공하면, THE Evidence_Collector SHALL 해당 Exact_Model_ID와 Known_Route의 Effort_Support_Status를 `SUPPORTED`로 기록한다.
14. IF Gateway가 effort field를 unknown field로 명시적으로 반환하면, THEN THE Evidence_Collector SHALL 해당 effort capability를 `UNSUPPORTED`로 기록한다.
15. IF Gateway가 effort value를 invalid value 또는 out-of-range로 반환하면, THEN THE Evidence_Collector SHALL 해당 effort capability를 `UNVERIFIED`로 기록한다.
16. WHILE effort Gateway_Probe가 Transient_Error인 경우, THE Evidence_Collector SHALL 해당 effort capability를 `UNVERIFIED`로 유지한다.
17. IF effort evidence가 일부 enum value 또는 range boundary만 검증하면, THEN THE Evidence_Collector SHALL 해당 effort capability를 `UNVERIFIED`로 유지한다.
18. THE Evidence_Collector SHALL Candidate_Label, Provider_String 또는 다른 Candidate_Model의 evidence에서 effort field path를 생성하지 않는다.
19. THE Evidence_Collector SHALL Candidate_Label, Provider_String 또는 다른 Candidate_Model의 evidence에서 effort domain을 생성하지 않는다.
20. IF effort 검증이 실패하면, THEN THE Evidence_Collector SHALL 이미 검증된 base Route_Support_Status를 effort 결과만으로 강등하지 않는다.

### Requirement 6: 현재 evidence를 가진 모델만 활성화

**User Story:** As a user, I want the model picker to expose only current and complete verified entries, so that stale, duplicate or seeded models cannot be selected.

#### Acceptance Criteria

1. THE Activation_Gate SHALL Verification_Status가 `VERIFIED`인 entry만 Active_Model 후보로 판정한다.
2. THE Activation_Gate SHALL non-empty Exact_Model_ID를 가진 entry만 Active_Model 후보로 판정한다.
3. THE Activation_Gate SHALL non-empty Provider_String을 가진 entry만 Active_Model 후보로 판정한다.
4. THE Activation_Gate SHALL 현재 Capability_Fingerprint와 일치하는 entry만 Active_Model 후보로 판정한다.
5. THE Activation_Gate SHALL Complete_Record를 가진 entry만 Active_Model 후보로 판정한다.
6. THE Activation_Gate SHALL 하나 이상의 Eligible_Contract를 가진 entry만 Active_Model 후보로 판정한다.
7. THE Activation_Gate SHALL Current_Evidence를 가진 entry만 Active_Model 후보로 판정한다.
8. THE Activation_Gate SHALL Malformed_Entry를 Active_Model에서 제외한다.
9. THE Activation_Gate SHALL `UNVERIFIED` entry를 Active_Model에서 제외한다.
10. THE Activation_Gate SHALL `DISCOVERED` entry를 Active_Model에서 제외한다.
11. THE Activation_Gate SHALL `REJECTED` entry를 Active_Model에서 제외한다.
12. THE Activation_Gate SHALL `STALE` entry를 Active_Model에서 제외한다.
13. THE Activation_Gate SHALL Seed_Entry를 Active_Model에서 제외한다.
14. THE Editor_Model_Catalog SHALL Active_Model만 선택 가능한 model 목록에 포함한다.
15. IF 현재 Gateway_Catalog에서 Exact_Model_ID가 제거되면, THEN THE Capability_Map SHALL 해당 entry를 `STALE`로 변경한다.
16. IF Provider_String이 변경되면, THEN THE Capability_Map SHALL 기존 entry를 `STALE`로 변경한다.
17. IF Route_Contract가 변경되면, THEN THE Capability_Map SHALL 기존 entry를 `STALE`로 변경한다.
18. IF Effort_Contract가 변경되면, THEN THE Capability_Map SHALL 기존 entry를 `STALE`로 변경한다.
19. IF Verification_Record의 revision이 Current_Revision과 다르면, THEN THE Capability_Map SHALL 해당 entry를 `STALE`로 변경한다.
20. WHEN `STALE` entry가 현재 production Gateway_Probe를 모두 통과하면, THE Capability_Map SHALL 새 Capability_Fingerprint와 UTC 시각으로 entry를 `VERIFIED`로 갱신한다.
21. IF `STALE` entry의 재검증이 완료되지 않으면, THEN THE Capability_Map SHALL 해당 entry를 `STALE`로 유지한다.
22. WHEN byte-equivalent duplicate entry가 존재하면, THE Editor_Model_Catalog SHALL canonical serialization이 같은 duplicate를 하나로 축약한다.
23. WHEN 동일 Exact_Model_ID의 유효 entry가 복수이면, THE Editor_Model_Catalog SHALL 가장 늦은 `verifiedAt`을 가진 단일 entry만 노출한다.
24. IF 동일 Exact_Model_ID의 최신 entry들이 서로 다른 Capability_Fingerprint를 가지면, THEN THE Editor_Model_Catalog SHALL 해당 Exact_Model_ID를 노출하지 않는다.
25. IF 동일 Exact_Model_ID의 최신 유효 entry들이 같은 `verifiedAt`으로 tie이면, THEN THE Editor_Model_Catalog SHALL 해당 Exact_Model_ID를 노출하지 않는다.
26. IF Candidate_Label의 Exact_Model_ID가 발견되지 않으면, THEN THE Editor_Model_Catalog SHALL Candidate_Label을 model 항목으로 노출하지 않는다.

### Requirement 7: capability tuple 기반 effort UI와 설정 정리

**User Story:** As a user, I want effort settings bound to an exact current capability tuple, so that model or route changes cannot leak stale effort into a request.

#### Acceptance Criteria

1. WHEN 선택된 Exact_Model_ID, Known_Route 및 Capability_Fingerprint tuple의 Effort_Support_Status가 `SUPPORTED`이면, THE Effort_Control SHALL effort UI를 표시한다.
2. WHEN effort UI를 표시하면, THE Effort_Control SHALL Effort_Contract의 verified domain만 선택 가능하게 표시한다.
3. IF 선택 tuple의 Effort_Support_Status가 `SUPPORTED`가 아니면, THEN THE Effort_Control SHALL effort UI를 숨긴다.
4. WHEN 사용자가 effort value를 선택하면, THE Effort_Settings_Manager SHALL Exact_Model_ID와 value를 함께 저장한다.
5. WHEN 사용자가 effort value를 선택하면, THE Effort_Settings_Manager SHALL Known_Route와 value를 함께 저장한다.
6. WHEN 사용자가 effort value를 선택하면, THE Effort_Settings_Manager SHALL Capability_Fingerprint와 value를 함께 저장한다.
7. IF 저장된 Exact_Model_ID가 현재 선택과 다르면, THEN THE Effort_Settings_Manager SHALL 저장된 effort value를 request 생성 전에 제거한다.
8. IF 저장된 Known_Route가 현재 선택과 다르면, THEN THE Effort_Settings_Manager SHALL 저장된 effort value를 request 생성 전에 제거한다.
9. IF 저장된 Capability_Fingerprint가 현재 값과 다르면, THEN THE Effort_Settings_Manager SHALL 저장된 effort value를 request 생성 전에 제거한다.
10. IF 저장된 effort value가 verified domain에 속하지 않으면, THEN THE Effort_Settings_Manager SHALL 저장된 effort value를 request 생성 전에 제거한다.
11. IF 선택 model entry가 삭제되면, THEN THE Model_Selection_Manager SHALL 연계된 Effort_Settings를 제거한다.
12. IF 선택 model entry가 `STALE`이 되면, THEN THE Model_Selection_Manager SHALL 연계된 Effort_Settings를 제거한다.
13. IF 선택 Known_Route가 `SUPPORTED` 상태를 잃으면, THEN THE Model_Selection_Manager SHALL 연계된 Effort_Settings를 제거한다.
14. WHEN model 또는 route를 변경하면, THE Model_Selection_Manager SHALL 새 tuple과 정확히 일치하는 Effort_Settings만 복원한다.
15. WHEN effort UI가 숨겨지면, THE Request_Builder SHALL request body의 모든 중첩 경로에서 Effort_Contract field를 0회 생성한다.
16. WHEN effort가 선택되지 않으면, THE Request_Builder SHALL request body의 모든 중첩 경로에서 Effort_Contract field를 0회 생성한다.
17. WHEN effort가 선택되지 않으면, THE Request_Builder SHALL 생성 body를 Baseline_Request_Body와 구조적으로 동일하게 유지한다.

### Requirement 8: verified route 계약에 따른 request 생성

**User Story:** As a developer, I want routing and body generation driven only by verified exact contracts, so that provider names, labels or stale fields cannot alter a request.

#### Acceptance Criteria

1. WHEN Active_Model request를 생성하면, THE Request_Router SHALL Exact_Model_ID와 Eligible_Contract로 route를 결정한다.
2. THE Request_Router SHALL Candidate_Label 문자열 패턴을 route 결정에 사용하지 않는다.
3. THE Request_Router SHALL Provider_String 문자열 패턴을 route 결정에 사용하지 않는다.
4. WHEN `/openai/responses` body를 생성하면, THE Request_Builder SHALL body 전체에서 `modelId` key를 0개 생성한다.
5. WHEN `/openai/responses` body를 생성하면, THE Request_Builder SHALL `model`에 Exact_Model_ID를 기록한다.
6. WHEN `/openai/responses` body를 생성하면, THE Request_Builder SHALL `input`을 포함한다.
7. WHEN `/openai/responses` body를 생성하면, THE Request_Builder SHALL verified Route_Contract가 열거한 선택 field만 추가한다.
8. WHEN `/openai/responses-jobs`의 `modelId` 계약을 검증하면, THE Evidence_Collector SHALL Production_Request_Path의 성공 probe로 필수 여부를 확정한다.
9. WHEN `/openai/responses-jobs`의 `modelId` 계약을 검증하면, THE Evidence_Collector SHALL Production_Request_Path의 성공 probe로 exact field path를 확정한다.
10. IF `/openai/responses-jobs`의 `modelId` 필수 여부 또는 path가 성공 probe로 확정되지 않으면, THEN THE Evidence_Collector SHALL jobs route를 `UNVERIFIED`로 유지한다.
11. WHERE verified jobs Route_Contract가 `modelId`를 요구하는 경우, THE Request_Builder SHALL exact field path에 Invocation_Model_ID를 정확히 1회 생성한다.
12. WHERE verified jobs Route_Contract가 `modelId`를 요구하지 않는 경우, THE Request_Builder SHALL body 전체에서 `modelId` key를 0회 생성한다.
13. WHEN `/converse` body를 생성하면, THE Request_Builder SHALL verified Route_Contract의 exact model ID field를 사용한다.
14. WHEN `/converse` body를 생성하면, THE Request_Builder SHALL verified Route_Contract의 message field schema를 사용한다.
15. WHEN `/converse` body를 생성하면, THE Request_Builder SHALL verified Route_Contract의 inference configuration schema를 사용한다.
16. WHEN valid effort value가 선택되면, THE Request_Builder SHALL Effort_Contract의 exact field path에 value를 정확히 1회 생성한다.
17. IF Effort_Support_Status가 `SUPPORTED`가 아니면, THEN THE Request_Builder SHALL 생성 body를 Baseline_Request_Body와 구조적으로 동일하게 유지한다.
18. IF 선택 effort value가 verified domain 밖이면, THEN THE Request_Builder SHALL 생성 body를 Baseline_Request_Body와 구조적으로 동일하게 유지한다.
19. IF Capability_Fingerprint가 현재 값과 다르면, THEN THE Request_Builder SHALL 생성 body를 Baseline_Request_Body와 구조적으로 동일하게 유지한다.
20. IF 선택 route가 `SUPPORTED`가 아니면, THEN THE Request_Router SHALL 해당 route의 Gateway 전송 건수를 0으로 유지한다.
21. WHEN fallback을 평가하면, THE Request_Router SHALL Fallback_Order에서 첫 번째 Eligible_Contract를 선택한다.
22. IF Fallback_Order에 Eligible_Contract가 없으면, THEN THE Request_Router SHALL Gateway fallback 전송을 생성하지 않는다.

### Requirement 9: 결정론적 오류 분류, retry 및 fallback

**User Story:** As a user, I want failures classified and recovered deterministically, so that capability state changes only from conclusive evidence and every fallback is visible.

#### Acceptance Criteria

1. WHEN Gateway 오류에 여러 failure 신호가 존재하면, THE Failure_Handler SHALL Failure_Precedence의 첫 일치 Failure_Category를 선택한다.
2. IF 오류가 explicit allowlist denial이면, THEN THE Failure_Handler SHALL Candidate_Model의 Verification_Status를 `REJECTED`로 기록한다.
3. IF 오류가 explicit allowlist denial이면, THEN THE Failure_Handler SHALL 해당 Exact_Model_ID를 Editor_Model_Catalog에서 제거한다.
4. IF 오류가 effort field mismatch이면, THEN THE Failure_Handler SHALL 해당 Effort_Contract만 `STALE`로 변경한다.
5. IF 오류가 effort value mismatch이면, THEN THE Failure_Handler SHALL 해당 Effort_Contract만 `STALE`로 변경한다.
6. IF base Route_Contract가 `SUPPORTED`이고 effort만 Capability_Mismatch이면, THEN THE Failure_Handler SHALL base Route_Support_Status를 유지한다.
7. IF base Route_Contract가 `SUPPORTED`이고 effort만 Capability_Mismatch이면, THEN THE Failure_Handler SHALL effort field를 제거한 Baseline_Request_Body를 최대 1회 재시도한다.
8. WHILE 오류가 Transient_Error인 경우, THE Failure_Handler SHALL Verification_Status를 강등하지 않는다.
9. WHILE 오류가 Transient_Error인 경우, THE Failure_Handler SHALL Route_Support_Status를 강등하지 않는다.
10. WHILE 오류가 Transient_Error인 경우, THE Failure_Handler SHALL Existing_Gateway_Integration의 retry 한도를 적용한다.
11. WHEN Transient_Error retry 한도가 소진되면, THE Failure_Handler SHALL Eligible_Contract만 fallback 후보로 평가한다.
12. WHEN verified fallback을 수행하면, THE Failure_Handler SHALL Fallback_Order의 첫 Eligible_Contract만 사용한다.
13. IF verified fallback이 존재하지 않으면, THEN THE Failure_Handler SHALL 현재 작업을 오류 상태로 종료한다.
14. WHEN 사용자에게 failure를 표시하면, THE User_Notification SHALL 원 Exact_Model_ID를 포함한다.
15. WHEN 사용자에게 failure를 표시하면, THE User_Notification SHALL 원 Known_Route를 포함한다.
16. WHEN 사용자에게 failure를 표시하면, THE User_Notification SHALL Failure_Category를 포함한다.
17. WHEN 사용자에게 failure를 표시하면, THE User_Notification SHALL 수행한 retry 횟수를 포함한다.
18. WHEN 사용자에게 failure를 표시하면, THE User_Notification SHALL fallback model·route 또는 fallback 미수행 결과를 포함한다.
19. THE User_Notification SHALL credential, authorization, cookie, signature 및 raw response body를 제외한다.
20. IF 오류가 `unknown`으로 분류되면, THEN THE Failure_Handler SHALL capability 상태를 변경하지 않는다.

### Requirement 10: SigV4와 runtime credential 보안

**User Story:** As a security owner, I want discovery and invocation to preserve the existing SigV4 credential boundary, so that verification cannot persist secrets or corrupt capability state after auth failures.

#### Acceptance Criteria

1. WHEN Gateway route를 호출하면, THE Runtime_Credential_Manager SHALL runtime injected credential 또는 `BedrockUser-{name}` assume-role credential만 사용한다.
2. WHEN Gateway execute-api route를 호출하면, THE Runtime_Credential_Manager SHALL SigV4 service `execute-api`로 요청을 서명한다.
3. WHEN SSE_Stream_Route를 호출하면, THE Runtime_Credential_Manager SHALL SigV4 service `lambda`로 요청을 서명한다.
4. WHILE cached credential의 age가 5분 미만인 경우, THE Runtime_Credential_Manager SHALL 기존 cached credential을 재사용한다.
5. WHEN cached credential의 age가 5분에 도달하면, THE Runtime_Credential_Manager SHALL 다음 요청 전에 credential을 갱신한다.
6. IF Gateway가 credential expiration을 반환하면, THEN THE Runtime_Credential_Manager SHALL Existing_Gateway_Integration의 강제 갱신 정책을 적용한다.
7. THE Gateway_Model_Support SHALL credential, authorization, cookie 및 signature 값을 settings에 저장하지 않는다.
8. THE Gateway_Model_Support SHALL credential, authorization, cookie 및 signature 값을 Capability_Map에 저장하지 않는다.
9. THE Gateway_Model_Support SHALL credential, authorization, cookie 및 signature 값을 Verification_Record에 저장하지 않는다.
10. THE Gateway_Model_Support SHALL credential, authorization, cookie 및 signature 값을 로그에 기록하지 않는다.
11. THE Gateway_Model_Support SHALL settings에 AWS profile name만 credential reference로 저장한다.
12. WHEN evidence를 영속화하면, THE Evidence_Collector SHALL 저장 전에 credential·authorization·cookie·signature field를 제거한다.
13. WHEN Gateway_Probe를 기록하면, THE Evidence_Collector SHALL raw prompt 대신 Probe_ID를 기록한다.
14. WHEN Gateway_Probe를 기록하면, THE Evidence_Collector SHALL raw request·response body 대신 Sanitized_Schema를 기록한다.
15. WHEN 이 기능의 데이터를 영속화하면, THE Gateway_Model_Support SHALL 애플리케이션 `userData` 하위 경로만 사용한다.
16. IF credential 획득 또는 인증이 실패하면, THEN THE Evidence_Collector SHALL Verification_Status를 변경하지 않는다.
17. IF credential 획득 또는 인증이 실패하면, THEN THE Evidence_Collector SHALL Route_Support_Status를 변경하지 않는다.
18. IF credential 획득 또는 인증이 실패하면, THEN THE Evidence_Collector SHALL Effort_Support_Status를 변경하지 않는다.
19. THE Gateway_Model_Support SHALL LLM generation과 effort 검증을 Bedrock Gateway route로만 수행한다.

### Requirement 11: 최소 비용의 재현 가능한 validation run

**User Story:** As a maintainer, I want every validation run to be reproducible and bounded, so that support claims can be audited without uncontrolled Gateway usage.

#### Acceptance Criteria

1. WHEN Python 기반 validation을 실행하면, THE Validation_Runner SHALL repository의 `ai_engine/.venv/bin/python` executable을 사용한다.
2. IF `ai_engine/.venv/bin/python`이 실행 가능하지 않으면, THEN THE Validation_Runner SHALL Gateway_Probe 전송 건수를 0으로 유지한다.
3. IF `ai_engine/.venv/bin/python`이 실행 가능하지 않으면, THEN THE Validation_Runner SHALL interpreter 환경 오류를 보고한다.
4. WHEN Validation_Run을 시작하면, THE Validation_Runner SHALL Current_Revision을 보고서에 기록한다.
5. WHEN Validation_Run을 시작하면, THE Validation_Runner SHALL interpreter absolute path를 보고서에 기록한다.
6. WHEN Validation_Run을 시작하면, THE Validation_Runner SHALL 시작 시각을 UTC ISO 8601로 보고서에 기록한다.
7. THE Validation_Runner SHALL 여섯 Candidate_Label 각각의 discovery 결과를 보고서에 기록한다.
8. THE Validation_Runner SHALL 발견된 Exact_Model_ID와 Provider_String을 보고서에 기록한다.
9. THE Validation_Runner SHALL Known_Route별 Route_Support_Status와 evidence reference를 보고서에 기록한다.
10. THE Validation_Runner SHALL Exact_Model_ID·Known_Route별 Effort_Support_Status와 evidence reference를 보고서에 기록한다.
11. THE Validation_Runner SHALL Verification_Status와 fingerprint를 보고서에 기록한다.
12. THE Validation_Runner SHALL 각 Gateway_Probe의 Sanitized_Schema를 보고서에 기록한다.
13. THE Validation_Runner SHALL Gateway_Probe에 고정된 짧은 비민감 입력을 사용한다.
14. THE Validation_Runner SHALL Gateway_Probe에 Route_Contract가 허용하는 최소 output/token bound를 사용한다.
15. THE Validation_Runner SHALL 동일 Exact_Model_ID·Known_Route·effort value 조합당 성공 generation을 1회로 제한한다.
16. IF 첫 요청이 명시적이고 교정 가능한 validation error를 반환하면, THEN THE Validation_Runner SHALL 동일 조합의 교정 요청을 최대 1회 전송한다.
17. IF Gateway가 usage를 제공하면, THEN THE Validation_Runner SHALL Gateway가 제공한 usage 값만 기록한다.
18. IF Gateway가 cost를 제공하면, THEN THE Validation_Runner SHALL Gateway가 제공한 cost 값만 기록한다.
19. IF Gateway가 usage를 제공하지 않으면, THEN THE Validation_Runner SHALL usage를 미제공 상태로 기록한다.
20. IF Gateway가 cost를 제공하지 않으면, THEN THE Validation_Runner SHALL cost를 미제공 상태로 기록한다.
21. WHEN 신규 Active_Model을 검사하면, THE Validation_Runner SHALL Current_Evidence가 현재 Validation_Run과 일치하는지 확인한다.
22. WHEN 변경된 Active_Model을 검사하면, THE Validation_Runner SHALL Verification_Record의 revision이 Current_Revision과 일치하는지 확인한다.
23. WHEN 변경된 Active_Model을 검사하면, THE Validation_Runner SHALL Verification_Record의 Capability_Fingerprint가 현재 값과 일치하는지 확인한다.
24. IF 신규 또는 변경된 Active_Model의 evidence 일치 검사가 실패하면, THEN THE Validation_Runner SHALL 해당 entry를 activation 결과에서 제외한다.

### Requirement 12: 무회귀와 property-based invariants

**User Story:** As a maintainer, I want deterministic regression and property coverage, so that capability filtering cannot alter proven behavior or substitute mocks for Gateway evidence.

#### Acceptance Criteria

1. THE Regression_Suite SHALL Existing_Model의 model ID와 provider가 기준선에서 변경되지 않는지 검증한다.
2. THE Regression_Suite SHALL Existing_Model의 verified route 선택이 기준선에서 변경되지 않는지 검증한다.
3. WHERE effort가 선택되지 않은 경우, THE Regression_Suite SHALL 기존 `/converse`, `/invoke`, `/openai/responses`, `/openai/responses-jobs` 및 SSE body를 기준선과 비교한다.
4. THE Regression_Suite SHALL 기존 GatewayClient public method signature를 보존하는 테스트를 포함한다.
5. THE Regression_Suite SHALL production response adapter output 계약을 보존하는 테스트를 포함한다.
6. THE Regression_Suite SHALL Converse job polling terminal-state 처리를 보존하는 테스트를 포함한다.
7. THE Regression_Suite SHALL OpenAI Responses job polling terminal-state 처리를 보존하는 테스트를 포함한다.
8. THE Regression_Suite SHALL 기존 retry와 prefix correction 한도를 보존하는 테스트를 포함한다.
9. THE Regression_Suite SHALL 임의 Capability_Map에서 Active_Model 집합이 유효한 `VERIFIED` entry 집합의 부분집합인 property를 검증한다.
10. THE Regression_Suite SHALL 임의 Malformed_Entry가 Active_Model 집합에서 제외되는 property를 검증한다.
11. THE Regression_Suite SHALL 임의 unsupported effort 설정이 Baseline_Request_Body를 변경하지 않는 property를 검증한다.
12. THE Regression_Suite SHALL 임의 supported effort value가 exact field path에 정확히 1회 생성되는 property를 검증한다.
13. THE Regression_Suite SHALL Capability_Canonicalizer를 반복 적용한 결과가 첫 적용 결과와 같은 idempotence property를 검증한다.
14. THE Regression_Suite SHALL Capability_Map serialization과 deserialization이 의미를 보존하는 round-trip property를 검증한다.
15. THE Regression_Suite SHALL catalog 입력 순서가 Active_Model 집합과 Capability_Fingerprint를 변경하지 않는 ordering-invariance property를 검증한다.
16. THE Regression_Suite SHALL catalog 변경 후 model selection 결과가 Active_Model 또는 명시적 미선택 상태인 selection-validity property를 검증한다.
17. THE Regression_Suite SHALL 임의 `/openai/responses` body 전체에 `modelId` key가 0개인 property를 검증한다.
18. THE Regression_Suite SHALL jobs Route_Contract의 `modelId` 필수 여부와 생성 body가 일치하는 property를 검증한다.
19. THE Regression_Suite SHALL 각 property에서 경계값을 포함한 generated case를 최소 100개 실행한다.
20. WHEN property-based test를 실행하면, THE Validation_Runner SHALL 재현 가능한 seed를 보고서에 기록한다.
21. IF property-based test가 실패하면, THEN THE Validation_Runner SHALL 최소화된 counterexample을 보고서에 기록한다.
22. THE Regression_Suite SHALL unit, mock 또는 property-based test 결과를 실제 Gateway 지원 evidence로 사용하지 않는다.
23. WHEN Candidate_Model과 광고된 Known_Route의 지원을 주장하면, THE Validation_Runner SHALL 해당 조합에 Production_Request_Path integration probe를 최소 1회 수행한다.
24. IF Candidate_Model 또는 광고된 Known_Route의 integration probe가 완료되지 않으면, THEN THE Validation_Runner SHALL 해당 Gateway 지원 주장을 `UNVERIFIED`로 보고한다.

## Verification Strategy Summary

| 검증 대상 | 테스트 방식 | 승격 기준 |
|---|---|---|
| Baseline_Category | repository file+symbol 검사 | 10개 category 모두 결과, Current_Revision, UTC 시각 보유 |
| Candidate_Label → Exact_Model_ID/Provider_String | Same_Gateway_Environment의 Gateway_Catalog 또는 엄격히 검증된 Operator_Catalog_Export | 문자열 원형 보존과 Catalog_Fingerprint 일치 |
| Exact_Model_ID + route allowlist | Production_Request_Path Minimal_Request integration probe | Valid_Output 성공은 `ALLOWED`, 명시적 거부는 `REJECTED`, 나머지는 `UNVERIFIED` |
| sync/async/streaming route | route별 HTTP·Valid_Output·Terminal_Success integration probe | 세 조건 모두 충족한 route만 `SUPPORTED` |
| effort field path와 domain | 완전한 Authoritative_Evidence + base 성공 + enum 전체 또는 range 경계 probe | 모든 필수 probe 성공 시에만 `SUPPORTED` |
| Capability_Map schema/fingerprint | unit + property-based round-trip/idempotence/order test | malformed 제외, deterministic serialization 및 의미 보존 |
| Activation_Gate | property-based test | Active_Model이 current·complete `VERIFIED` entry의 부분집합 |
| effort UI/request injection | unit + property-based test | tuple 일치, verified domain, supported exact path 1회, 그 외 baseline 동일 |
| error classification/fallback | pure unit/PBT + 대표 integration example | Failure_Precedence, 상태 보존, 첫 Eligible_Contract만 사용 |
| 실제 Gateway 지원 주장 | Candidate_Model·advertised route별 production-builder integration probe | unit/mock/PBT로 대체 불가 |
| 기존 route 무회귀 | targeted unit/regression tests | body, signature, adapter, polling 및 retry 기준선 보존 |

Property-based test는 외부 Gateway 동작을 반복 호출하지 않고 pure capability logic과 request construction을 검증한다. 실제 Gateway 정책, route, output 및 effort 지원은 최소 integration probe로 별도 검증한다.

## Initial Candidate Status

| Candidate_Label | Exact model ID | Provider | Route | Effort | Verification_Status | 활성 목록 |
|---|---|---|---|---|---|---|
| `opus 5` | 미확정 | 미확정 | 미확정 | 미확정 | `UNVERIFIED` | 제외 |
| `sonnet 5` | 미확정 | 미확정 | 미확정 | 미확정 | `UNVERIFIED` | 제외 |
| `gpt 5.6` | 미확정 | 미확정 | 미확정 | 미확정 | `UNVERIFIED` | 제외 |
| `sol` | 미확정 | 미확정 | 미확정 | 미확정 | `UNVERIFIED` | 제외 |
| `terra` | 미확정 | 미확정 | 미확정 | 미확정 | `UNVERIFIED` | 제외 |
| `luna` | 미확정 | 미확정 | 미확정 | 미확정 | `UNVERIFIED` | 제외 |

각 Candidate_Label은 이 표에 정확히 한 번만 나타나며 초기 상태는 모두 `UNVERIFIED`다. 미확정 값을 Candidate_Label, provider 추정, model family 추정 또는 다른 candidate의 evidence로 채우는 행위는 허용되지 않는다. Exact_Model_ID, Provider_String, route 및 effort는 Requirement 2~5의 Authoritative_Evidence와 Production_Request_Path Gateway_Probe를 통과한 뒤에만 Capability_Map에 기록한다.
