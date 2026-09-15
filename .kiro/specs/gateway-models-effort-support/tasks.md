# Implementation Plan: Gateway 모델 활성화 게이트와 effort capability 지원 (gateway-models-effort-support)

## Overview

이 계획은 design.md의 모듈/파일 매핑과 Correctness Properties 1~10을 코드 작성·수정·테스트 작업으로 분해한다.

구현 언어는 design.md가 확정한 그대로다. 백엔드는 Python 3.11+(FastAPI/HTTPX), 프론트는 Vanilla JS(Electron renderer + Web Component), property test는 Hypothesis를 사용한다. **모든 Python 실행(테스트·검증·CLI)은 `ai_engine/.venv/bin/python`으로만 수행한다.**

### 절대 원칙 — 순수 add(추가)

- 기존 `GatewayClient`의 `converse`/`invoke`/스트리밍 메서드, adapter, job polling, retry·prefix 교정의 **시그니처와 동작은 불변**이다. 신규 코드는 상속 seam과 신규 모듈로만 추가한다.
- 신규 패키지 `ai_engine/capability/*`는 독립 모듈이며, 기존 파일 수정은 design.md 통합 지점 표에 명시된 **비침습 seam 최소 변경**으로 제한한다.
- `Managed_Segment`가 비어 있으면 `/api/models` 응답과 프론트 모델 목록·선택 동작은 **기준선과 동일**하다(응답 바이트 보존). 병합 실패는 try/except graceful 폴백으로 기존 경로를 유지한다.
- effort 미선택 시 5개 route의 생성 body는 Baseline_Request_Body와 **바이트 동일**하다.
- 신규 서명 코드·신규 credential 캐시·신규 URL 하드코딩을 추가하지 않는다. SigV4(`execute-api`/`lambda`), 5분 credential 캐시, 만료 강제 갱신은 기존 구현에 위임한다.

### 활성화 강제 조건 (작업 수준)

- **검증되지 않은 모델은 어떤 작업에서도 활성 목록에 추가하지 않는다.** `UNVERIFIED`, `DISCOVERED`, `REJECTED`, `STALE`, Malformed_Entry, Seed_Entry는 Activation_Gate를 통과할 수 없다.
- model ID, provider, route 지원 여부, effort field path, effort 허용값을 코드·테스트·fixture에 **확정 상수로 두지 않는다.** 전부 evidence가 채우는 자리이며, 생성기는 무작위 심볼만 사용한다.
- Candidate_Label(`opus 5`, `sonnet 5`, `gpt 5.6`, `sol`, `terra`, `luna`)은 검색 라벨로만 취급한다. 라벨 문자열에서 model identity·provider·route·effort를 유도하는 코드를 작성하지 않으며, 라벨 간 evidence 전파도 금지한다.
- unit·mock·property test 성공은 Gateway 지원 근거가 아니다. Gateway 지원 주장은 작업 18(실제 Gateway 검증)의 production path probe로만 확정한다.

## Tasks

- [x] 1. capability 패키지 계약·영속 계층 구축 (`ai_engine/capability/` 신규)
  - [x] 1.1 패키지 진입점과 계약 스키마 작성
    - `ai_engine/capability/__init__.py` 생성(외부 import 부작용 없음)
    - `ai_engine/capability/contracts.py`에 닫힌 enum 정의: `Verification_Status`, `Route_Support_Status`, `Effort_Support_Status`, `Allowlist_Result`, `Known_Route`, `Execution_Mode`, `Signing_Service`, `Domain_Kind`, `Value_Type`, `Source_Kind`, `Failure_Category`
    - Capability_Map entry / Route_Entry / Effort_Entry / Route_Contract / Effort_Contract / Verification_Record 스키마와 필수 필드 검증기 작성
    - 계약 필드는 nullable 기본값 `None`, `verificationStatus` 초기값 `UNVERIFIED`로 두어 미확정 상태를 명시적으로 표현한다(값 추론 금지)
    - Malformed_Entry 판정기 작성: 필수 필드 누락, 타입 불일치, enum 이탈, fingerprint 불일치, evidence 참조 무결성 실패
    - 모든 시각 필드는 UTC ISO 8601 문자열로만 저장
    - _Requirements: 1.15, 3.1, 3.2, 3.3, 3.5, 3.6, 3.8, 3.9, 3.10, 3.11, 3.17_
  - [x] 1.2 userData 한정 영속 계층 작성
    - `ai_engine/capability/store.py`: `userData` 루트 해석, `capability/` 하위 경로 빌더(map, effort settings, baseline, evidence, catalog, runs), 루트 밖 경로 읽기·쓰기 거부
    - 임시 파일 + `os.replace` 원자적 쓰기 구현
    - 저장 직전 sanitizer: credential·authorization·cookie·signature field 제거, raw prompt는 Probe_ID로 대체, raw body는 Sanitized_Schema로 대체
    - 로그 출력에는 Probe_ID와 Sanitized_Schema만 남기고 토큰류는 기존 `mask_token` 재사용
    - _Requirements: 10.7, 10.8, 10.9, 10.10, 10.12, 10.15_
  - [x]* 1.3 계약·영속 계층 단위 테스트 작성 (`scripts/test_capability_store_contracts.py`)
    - enum 이탈·필수 필드 누락·타입 불일치 입력이 Malformed_Entry로 판정되는지 검증
    - userData 루트 밖 경로 거부, 원자적 쓰기 후 파일 무결성, 시각 필드 UTC ISO 8601 형식 검증
    - sanitizer가 credential·authorization·cookie·signature를 제거하는지 검증
    - _Requirements: 3.8, 3.9, 3.10, 3.11, 10.15_

- [x] 2. Capability_Canonicalizer 구현과 결정론 property
  - [x] 2.1 canonical 정규화·직렬화·fingerprint 구현 (`ai_engine/capability/canonicalizer.py`)
    - `SET_LIKE_PATHS`(집합 의미 → 정렬·중복 제거)와 `ORDER_BEARING_PATHS`(순서 의미 field path → 원 순서 보존) 정의
    - `canonical(value)`: dict key UTF-8 바이트 순 정렬, 집합 의미 collection 정렬·dedup, `null` 명시적 보존(미확정과 미존재 구분)
    - `serialize` / `deserialize`: `json.dumps(sort_keys=True, ensure_ascii=False, separators=(",", ":"))`, 의미 손실 시 Malformed_Entry
    - `capability_fingerprint`(`cfp1:sha256:`), `catalog_fingerprint`(`cat1:sha256:`), `evidence_record_id`(`evr1:sha256:`) 구현
    - fingerprint 입력에 `candidateLabel`, `displayName`, 모든 UTC 시각, `revision`, evidence 저장 위치, `evidenceRecordId`/`evidence`, 로그, Sanitized_Schema를 **포함하지 않음**
    - _Requirements: 3.12, 3.13, 3.14, 3.15, 3.16_
  - [x]* 2.2 PBT 공통 생성기 작성 (`scripts/_capability_strategies.py`)
    - status enum 전수, malformed mutation, 순서 치환, effort domain(enum/range), 중첩 body 생성기 작성
    - 경계값 명시 포함: 빈 map, 단일 entry, 동시각 tie, 빈 문자열 ID, enum 단일값, range 상·하한 동일, 최대 중첩 body
    - model ID·provider·effort field path·effort value는 확정 상수 없이 **무작위 심볼로만** 생성
    - 공통 헤더 규약 정의: `AE_PBT_SEED = 20260803`(`AE_PBT_SEED` env 오버라이드), `@seed(AE_PBT_SEED)`, `@settings(max_examples=100, deadline=None, database=None, print_blob=True)`
    - _Requirements: 12.19, 12.22_
  - [x]* 2.3 Property 5 테스트 작성 (`scripts/test_capability_canonicalizer_idempotence_pbt.py`)
    - **Property 5: canonicalization 멱등성** — _Validates: Requirements 3.14, 3.16, 12.13_
    - `canonical(canonical(x)) == canonical(x)`, `serialize(canonical(x)) == serialize(x)`, 반복 적용 시 Capability_Fingerprint 불변 단정
    - Hypothesis 100 cases 이상, `@seed(AE_PBT_SEED)` 고정 seed, 실패 시 최소화 counterexample과 재현 blob을 보고서 `pbt.counterexamples`에 기록
    - 파일 최상단에 `Feature: gateway-models-effort-support, Property 5: ...` 태그 주석 부착
    - _Requirements: 3.14, 3.16, 12.13, 12.19, 12.21_
  - [x]* 2.4 Property 6 테스트 작성 (`scripts/test_capability_serialization_roundtrip_pbt.py`)
    - **Property 6: serialization round-trip** — _Validates: Requirements 3.15, 12.14_
    - serialize→deserialize 후 model identity·provider·모든 Known_Route 계약·상태·effort 계약·상태·evidence reference 의미 보존, 재직렬화 바이트 동일 단정
    - Hypothesis 100 cases 이상, 고정 seed, counterexample 기록, Property 태그 주석 부착
    - _Requirements: 3.15, 12.14, 12.19, 12.21_

- [x] 3. Repository_Baseline_Inspector 구현
  - [x] 3.1 10개 Baseline_Category 검사기 작성 (`ai_engine/capability/baseline_inspector.py`)
    - design.md `BASELINE_TARGETS` 표대로 `catalog`, `allowlist`, `gateway-client`, `route`, `request-builder`, `model-selection`, `response-adapter`, `job-polling`, `retry`, `error-handling` 대상 file+symbol 정의
    - `.py`는 `ast.parse`로 top-level·클래스 멤버 심볼 확인, `.js`는 선언 패턴 스캔(파서 의존 추가 없음)
    - 미발견은 예외가 아니라 `found: False` + `reason` 기록
    - `revision`(Current_Revision)과 `inspectedAt`(UTC ISO 8601)을 모든 Baseline_Record에 기록
    - 10 category 중 하나라도 결과가 비면 `evidenceEligible: False`를 기록해 Evidence_Collector가 근거 집합에서 제외하도록 함
    - _Requirements: 1.1, 1.2, 1.3, 1.4, 1.5, 1.6, 1.7, 1.8, 1.9, 1.10, 1.11, 1.12, 1.16_
  - [x]* 3.2 baseline inspector 단위 테스트 작성 (`scripts/test_capability_baseline_inspector.py`)
    - 10 category 전체 record 생성, 미발견 시 `found: False`+`reason`, 결손 시 `evidenceEligible: False` 검증
    - `revision` 기록과 `inspectedAt` UTC ISO 8601 형식 검증
    - 표시명·주석·Seed_Entry·mock만으로 구성된 근거가 `UNVERIFIED`를 유지하는지 검증
    - _Requirements: 1.11, 1.12, 1.16, 1.17_

- [ ] 4. Checkpoint - 계약·canonicalizer·baseline 테스트 통과 확인
  - Ensure all tests pass, ask the user if questions arise.

- [x] 5. Capability_Map과 Activation_Gate 구현
  - [x] 5.1 Capability_Map 로드·갱신·상태 전이 구현 (`ai_engine/capability/capability_map.py`)
    - `load`, `upsert`(Verification_Record 반영 + fingerprint 재계산), `valid_entries`(Malformed 분리), `mark_stale` 구현
    - 활성 evidence 선택: 동일 modelId·동일 Capability_Fingerprint 복수 evidence는 최신 `verifiedAt`, 동시각이면 Evidence_Record_ID 오름차순 첫 record
    - `derive_mode_support`: `syncSupport`/`asyncSupport`/`streamingSupport`를 execution mode별 route 상태에서 `SUPPORTED > UNVERIFIED > UNSUPPORTED > NOT_ADVERTISED` 우선순위로 유도
    - STALE 전이 트리거 구현: catalog에서 Exact_Model_ID 제거, Provider_String 변경, Route_Contract 변경, Effort_Contract 변경, evidence revision ≠ Current_Revision. 재검증 미완료 시 `STALE` 유지
    - `SUPPORTED` route·effort entry에는 완전한 계약과 Current_Evidence reference를 요구
    - _Requirements: 3.3, 3.4, 3.6, 3.7, 3.17, 3.18, 3.19, 3.20, 3.21, 3.22, 3.23, 6.15, 6.16, 6.17, 6.18, 6.19, 6.20, 6.21_
  - [x] 5.2 Activation_Gate 순수 함수 구현 (`ai_engine/capability/activation_gate.py`)
    - `is_active(entry, ctx)`: `VERIFIED` + non-empty modelId + non-empty provider + fingerprint 일치 + Complete_Record + Eligible_Contract ≥ 1 + Current_Evidence + Malformed 아님 + `sourceKind != "SEED"` 전부 AND, 탈락 이유 코드 반환
    - `Eligible_Contract` 판정: route `SUPPORTED` AND Current_Evidence AND 요청 목적(`purposes`) 충족 AND allowlist `ALLOWED`
    - `active_models(map_obj, ctx)`: 유효 entry만 평가, canonical serialization 동일 duplicate 1개로 축약, 동일 modelId는 최신 `verifiedAt` 단일 노출, fingerprint 불일치·`verifiedAt` tie는 미노출
    - Exact_Model_ID가 발견되지 않은 Candidate_Label은 어떤 경우에도 모델 항목으로 산출하지 않음
    - _Requirements: 6.1, 6.2, 6.3, 6.4, 6.5, 6.6, 6.7, 6.8, 6.9, 6.10, 6.11, 6.12, 6.13, 6.22, 6.23, 6.24, 6.25, 6.26_
  - [x] 5.3 `/api/models` Managed_Segment 병합 seam 추가 (`ai_engine/server.py` 수정 + `capability_map.py` 병합 helper)
    - `capability_map.merge_active_into_catalog(catalog, active)`와 `capability_map.to_ui_payload(active)` 추가
    - `list_models` 내부 `_filter_uninvokable` 이후·응답 dict 구성 직전에 Managed_Segment 병합 삽입, 전체를 try/except로 감싸 실패 시 원인(≤200자) 로그 후 Baseline_Catalog_Segment만 반환
    - `Managed_Segment`가 비면 `catalog`와 `payload`를 변경하지 않아 응답 바이트가 기준선과 동일하게 유지되도록 구현
    - `Baseline_Catalog_Segment`의 기존 denylist·uninvokable 필터와 provider 분류는 손대지 않음
    - _Requirements: 1.13, 6.14, 6.26, 12.1, 12.2_
  - [x]* 5.4 Property 1 테스트 작성 (`scripts/test_capability_activation_subset_pbt.py`)
    - **Property 1: Activation subset** — _Validates: Requirements 1.15, 1.17, 6.1, 6.2, 6.3, 6.4, 6.5, 6.6, 6.7, 6.9, 6.10, 6.11, 6.12, 6.13, 6.14, 6.26, 12.9, 12.22_
    - 모든 Verification_Status·Malformed·Seed·mock 출처 evidence·미발견 라벨을 섞은 임의 map에서 Active_Model이 조건 집합의 부분집합이고 Managed_Segment 노출 집합과 정확히 일치, Candidate_Label 문자열은 절대 노출되지 않음을 단정
    - Hypothesis 100 cases 이상, 고정 seed, counterexample 기록, Property 태그 주석 부착
    - _Requirements: 6.1, 6.14, 12.9, 12.19, 12.21, 12.22_
  - [x]* 5.5 Property 2 테스트 작성 (`scripts/test_capability_malformed_exclusion_pbt.py`)
    - **Property 2: Malformed 제외** — _Validates: Requirements 3.1, 3.2, 3.3, 3.4, 3.5, 3.6, 3.7, 3.8, 3.9, 3.10, 3.11, 3.17, 6.8, 12.10_
    - 필수 필드 삭제·타입 변형·enum 이탈·fingerprint 불일치·evidence 참조 무결성 실패 mutation 주입 entry가 항상 Malformed로 판정되고 Active_Model 교집합이 공집합임을 단정
    - Hypothesis 100 cases 이상, 고정 seed, counterexample 기록, Property 태그 주석 부착
    - _Requirements: 3.17, 6.8, 12.10, 12.19, 12.21_
  - [x]* 5.6 Property 7 테스트 작성 (`scripts/test_capability_ordering_invariance_pbt.py`)
    - **Property 7: ordering invariance** — _Validates: Requirements 3.12, 3.13, 3.18, 3.19, 6.22, 6.23, 6.24, 6.25, 12.15_
    - catalog 입력 순서·entry 순서·object key 순서·집합 의미 원소 순서 치환 시 Active_Model 집합과 Capability_Fingerprint 불변, fingerprint 제외 입력만 변경해도 fingerprint 불변, duplicate 축약·tie 규칙이 입력 순서와 무관함을 단정
    - Hypothesis 100 cases 이상, 고정 seed, counterexample 기록, Property 태그 주석 부착
    - _Requirements: 3.12, 3.13, 6.22, 6.23, 12.15, 12.19, 12.21_

- [ ] 6. Checkpoint - Capability_Map·Activation_Gate·병합 seam 테스트 통과 확인
  - Ensure all tests pass, ask the user if questions arise.

- [x] 7. Request_Router / Request_Builder와 gateway seam
  - [x] 7.1 jobs model ID seam 추출 (`ai_engine/gateway_module.py` 수정 — seam만)
    - `openai_responses_job_submit`의 model ID 부착 로직을 `_apply_jobs_model_id(body, model_id)`로 추출하고, 기본 구현은 현재 동작과 **바이트 동일**하게 유지
    - 기존 `converse`/`invoke`/스트리밍/adapter/job polling/retry·prefix 교정 메서드의 시그니처와 동작은 변경하지 않음
    - `_build_payload`, `_build_openai_payload`는 오버라이드 대상 seam으로만 유지(본문 로직 변경 없음)
    - _Requirements: 1.13, 8.11, 8.12, 12.3, 12.4_
  - [x] 7.2 Request_Router 구현 (`ai_engine/capability/request_builder.py`)
    - `select_contract(entry, purpose, ctx)`: `Fallback_Order`(`fallbackRank` 오름차순) 첫 Eligible_Contract 반환, 후보 없으면 `None`
    - Candidate_Label 문자열 패턴과 Provider_String 문자열 패턴을 route 결정 입력으로 사용하지 않음
    - route가 `SUPPORTED`가 아니면 Gateway 전송을 생성하지 않도록 호출자 계약 정의(전송 건수 0)
    - fallback은 첫 Eligible_Contract만 사용하고 연쇄 이동하지 않음
    - _Requirements: 8.1, 8.2, 8.3, 8.20, 8.21, 8.22_
  - [x] 7.3 EffortBoundClient와 effort 주입 구현 (`ai_engine/capability/request_builder.py`)
    - `EffortBoundClient(GatewayClient)`: `_get_creds`/`force_refresh_creds`를 base 인스턴스에 위임(5분 캐시 단일화), 서명·retry·prefix 교정·job polling·응답 변환은 상속 구현 그대로 사용
    - `_build_payload`/`_build_openai_payload` 오버라이드에서만 `_inject_effort` 호출
    - `_inject_effort`: selection 없음, modelId·route·Capability_Fingerprint 불일치, effort status가 `SUPPORTED` 아님, verified domain 이탈 중 하나라도 성립하면 baseline body를 **동일 객체로 그대로** 반환
    - 전부 일치하면 baseline body의 deep copy에 계약 field path로 값을 **정확히 1회** 기록하고 다른 경로는 추가·삭제·변형하지 않음
    - `_apply_jobs_model_id` 오버라이드: 계약이 model ID를 요구하면 계약 path에 Invocation_Model_ID 1회, 요구하지 않으면 body 전체에서 해당 key 0회
    - 신규 서명 코드·신규 URL·신규 credential 캐시를 추가하지 않음
    - _Requirements: 1.13, 7.15, 7.16, 7.17, 8.13, 8.14, 8.15, 8.16, 8.17, 8.18, 8.19, 10.1, 10.2, 10.3, 10.4, 10.5, 10.6, 10.19_
  - [x]* 7.4 Property 3 테스트 작성 (`scripts/test_effort_injection_baseline_preservation_pbt.py`)
    - **Property 3: unsupported effort 비주입** — _Validates: Requirements 7.15, 7.16, 7.17, 8.17, 8.18, 8.19, 12.3, 12.11_
    - effort 미선택·UI 숨김·status 비`SUPPORTED`·domain 이탈·fingerprint 불일치 중 하나라도 성립하면 생성 body가 Baseline_Request_Body와 구조적으로 동일하고 모든 중첩 경로의 effort field 발생 횟수가 0임을 단정
    - Hypothesis 100 cases 이상, 고정 seed, counterexample 기록, Property 태그 주석 부착
    - _Requirements: 7.15, 7.16, 7.17, 12.11, 12.19, 12.21_
  - [x]* 7.5 Property 4 테스트 작성 (`scripts/test_effort_injection_exact_once_pbt.py`)
    - **Property 4: supported effort exact-once** — _Validates: Requirements 8.16, 12.12_
    - `SUPPORTED` 계약과 verified domain(enum 멤버 또는 inclusive range 값)에서 뽑은 임의 value가 exact field path에 정확히 1회 기록되고 그 경로 외 body는 baseline과 동일함을 단정
    - Hypothesis 100 cases 이상, 고정 seed, counterexample 기록, Property 태그 주석 부착
    - _Requirements: 8.16, 12.12, 12.19, 12.21_
  - [x]* 7.6 Property 9 테스트 작성 (`scripts/test_openai_sync_body_no_modelid_pbt.py`)
    - **Property 9: sync Responses `modelId` 부재** — _Validates: Requirements 8.4, 8.5, 8.6, 8.7, 12.17_
    - 임의 사용자 입력·system 지시문·선택 field 조합·임의 effort 주입 상태에서 동기 Responses body 전체를 재귀 탐색해 `modelId` key 개수 0, `model`에 Exact_Model_ID 기록, `input` 존재, 계약이 열거하지 않은 key 부재를 단정
    - Hypothesis 100 cases 이상, 고정 seed, counterexample 기록, Property 태그 주석 부착
    - _Requirements: 8.4, 8.5, 8.6, 8.7, 12.17, 12.19, 12.21_
  - [x]* 7.7 Property 10 테스트 작성 (`scripts/test_openai_jobs_modelid_contract_pbt.py`)
    - **Property 10: jobs `modelId` 계약 일치** — _Validates: Requirements 8.11, 8.12, 12.18_
    - 계약이 model ID를 요구하면 exact field path에 Invocation_Model_ID 1회·그 외 경로 0회, 요구하지 않으면 body 전체 0회임을 단정
    - Hypothesis 100 cases 이상, 고정 seed, counterexample 기록, Property 태그 주석 부착
    - _Requirements: 8.11, 8.12, 12.18, 12.19, 12.21_

- [x] 8. Effort_Settings 정규화·무효화 구현
  - [x] 8.1 Effort_Settings 순수 로직 작성 (`ai_engine/capability/effort_settings.py`)
    - `prune(settings, ctx)`: modelId 불일치·route 불일치·Capability_Fingerprint 불일치·verified domain 이탈·entry 삭제·entry `STALE`·route의 `SUPPORTED` 상실 시 request 생성 전에 저장 value 제거
    - `restore(settings, tuple_key)`: `(modelId, route, capabilityFingerprint)` 세 값이 모두 일치할 때만 복원, 그 외에는 `None`(명시적 미선택)
    - 스키마는 design.md Effort_Settings 정의를 따르고 DOM·IO 의존 없이 순수 로직으로 유지
    - _Requirements: 7.4, 7.5, 7.6, 7.7, 7.8, 7.9, 7.10, 7.11, 7.12, 7.13, 7.14_
  - [x]* 8.2 Property 8 테스트 작성 (`scripts/test_capability_selection_validity_pbt.py`)
    - **Property 8: selection validity** — _Validates: Requirements 7.7, 7.8, 7.9, 7.10, 7.11, 7.12, 7.13, 7.14, 12.16_
    - 임의 catalog·capability 변경 시퀀스 후 선택이 Active_Model 중 하나이거나 명시적 미선택이고, 불일치 Effort_Settings가 request 생성 전에 제거되며, 복원은 tuple 3요소 전부 일치 시에만 발생함을 단정
    - Hypothesis 100 cases 이상, 고정 seed, counterexample 기록, Property 태그 주석 부착
    - _Requirements: 7.11, 7.12, 7.13, 7.14, 12.16, 12.19, 12.21_

- [x] 9. Failure_Handler와 User_Notification 구현
  - [x] 9.1 결정론적 실패 분류·전이·복구 구현 (`ai_engine/capability/failure_handler.py`)
    - `PRECEDENCE` 순서대로 첫 일치 범주를 반환하는 `classify(signals)` 구현(`authentication` → `allowlist` → `effort-mismatch` → `route-capability-mismatch` → `quota` → `transient` → `request-validation` → `unknown`)
    - 판정 신호는 기존 심볼 재사용: `_is_expired_error`, `_maybe_record_denied_from_error`, `OpenAIModelUnsupported`, `QuotaExceededError`, `SyncTimeout`, `JobTimeout`, `OpenAISurfaceError`
    - `apply(entry, category, ctx)`: design.md Error Handling 표와 1:1 상태 전이. `transient`·`unknown`·인증 실패는 상태 불변, explicit allowlist denial은 `REJECTED` + route allowlist `REJECTED` + 기존 `_record_denied_model` 호출 + 목록 제거, effort-only mismatch는 해당 Effort_Contract만 `STALE`
    - `plan_recovery`: effort-only mismatch에서만 무-effort baseline 재시도 1회, transient retry는 기존 한도 그대로, 한도 소진 후 Fallback_Order 첫 Eligible_Contract만, 후보 없으면 오류 종료
    - `notification(ctx)`: `modelId`·`route`·`category`·`retryCount`·`fallback`만 포함하고 credential·authorization·cookie·signature·raw body 제외, 원인 문자열 200자 절단
    - _Requirements: 9.1, 9.2, 9.3, 9.4, 9.5, 9.6, 9.7, 9.8, 9.9, 9.10, 9.11, 9.12, 9.13, 9.14, 9.15, 9.16, 9.17, 9.18, 9.19, 9.20, 10.16, 10.17, 10.18_
  - [x]* 9.2 Failure_Handler 단위 테스트 작성 (`scripts/test_capability_failure_handler.py`)
    - 신호 중첩 조합에서 Failure_Precedence 첫 일치 범주 선택 검증(범주 전수)
    - 상태 보존 불변식 검증: transient·unknown·인증 실패 시 Verification/Route/Effort 상태와 목록 불변
    - effort-only mismatch의 무-effort 재시도 정확히 1회, fallback은 첫 Eligible_Contract만, 후보 없으면 오류 종료 검증
    - User_Notification 필드 화이트리스트와 비밀정보 부재 검증
    - _Requirements: 9.1, 9.4, 9.5, 9.6, 9.7, 9.8, 9.9, 9.10, 9.11, 9.12, 9.13, 9.19, 9.20, 10.16, 10.17, 10.18_

- [ ] 10. Checkpoint - request builder·effort settings·failure handler 테스트 통과 확인
  - Ensure all tests pass, ask the user if questions arise.

- [x] 11. Evidence_Collector 구현
  - [x] 11.1 discovery와 Verification_Record 생성 구현 (`ai_engine/capability/evidence_collector.py`)
    - 여섯 Candidate_Label 각각에 정확히 하나의 초기 `UNVERIFIED` entry 생성, 계약 필드는 전부 `null` 유지
    - `discover`: Same_Gateway_Environment catalog 조회로 Exact_Model_ID·Provider_String을 문자 변경 없이 기록, 미발견 시 `UNVERIFIED` 유지·probe 미생성
    - catalog endpoint 부재 시 Operator_Catalog_Export만 대체 근거로 허용하고 environment identity·region 일치, UTC 시각 순서, Catalog_Fingerprint 재계산 일치, sanitization manifest가 credential 관련 field만 제거했는지 검증. 하나라도 실패하면 근거에서 제외
    - `candidateLabel` 단위 upsert로 라벨 간 provider·model family·route·effort 전파를 구조적으로 차단
    - `to_verification_record`: 정제 record 생성 + Evidence_Record_ID 계산, raw prompt는 Probe_ID로, raw body는 Sanitized_Schema로 기록
    - `evidenceEligible: False` baseline과 과거 사양·표시명·주석·Seed_Entry·mock 근거는 Authoritative_Evidence에서 제외
    - _Requirements: 1.14, 1.15, 1.16, 1.17, 2.1, 2.2, 2.3, 2.4, 2.5, 2.6, 2.7, 2.8, 2.9, 2.10, 2.11, 2.12, 2.18, 10.12, 10.13, 10.14_
  - [x] 11.2 route probe 오케스트레이션 구현 (`ai_engine/capability/evidence_collector.py`)
    - `probe_route`: production Request_Builder로 Minimal_Request 생성 → 기존 transport 호출 → production adapter·job polling으로 판정. diagnostic code·수동 body 결과는 activation evidence에서 제외
    - HTTP 성공, Valid_Output, Terminal_Success를 각각 독립 결과로 기록하고 세 조건 충족 시에만 route `SUPPORTED`
    - Known_Route별 판정 근거를 design.md 표의 기존 심볼로만 연결(요청 생성·Valid_Output·Terminal_Success)
    - advertised 아님 → `NOT_ADVERTISED` 기록 + 전송 0건, 명시적 unknown model/unsupported route → `UNSUPPORTED`, 명시적 allowlist 거부 → `REJECTED`, transient·empty·partial·timeout → `UNVERIFIED` 유지
    - 예산 강제: 조합당 성공 generation 1회, 명시적·교정 가능 validation error에만 동일 조합 교정 1회, prefix 형태 교정도 동일 route 1회, Invocation_Model_ID는 별도 필드에 기록
    - `minOutputBound`를 계약 허용 최소값으로 설정하고 route 상태 완전성을 Verification_Record에 기록. 모든 승격 조건 충족 시에만 `VERIFIED`
    - _Requirements: 2.13, 2.14, 2.15, 2.16, 2.17, 4.1, 4.2, 4.3, 4.4, 4.5, 4.6, 4.7, 4.8, 4.16, 4.17, 4.18, 4.19, 4.20, 4.21, 4.22, 4.23, 4.24, 4.25, 4.26, 4.27, 4.28_
  - [x] 11.3 effort probe 오케스트레이션 구현 (`ai_engine/capability/evidence_collector.py`)
    - `probe_effort`: field path·value type·complete domain 중 하나라도 결손이면 전송 없이 `UNVERIFIED` 유지
    - 동일 model·route의 무-effort Baseline_Request_Body 성공을 먼저 확인하고, 실패하면 effort probe를 전송하지 않음
    - domain별 예산 적용: enum은 광고된 각 value 1회, range 상·하한 상이 시 두 경계 각 1회, 상·하한 동일 시 그 경계 1회. 부분 검증·transient·invalid value는 `UNVERIFIED`, unknown field는 `UNSUPPORTED`
    - 성공 시 실제 field path와 실제 value를 Verification_Record에 기록하고, 모든 필수 검증 성공 시에만 `SUPPORTED`
    - effort 실패가 base Route_Support_Status를 강등하지 않음을 보장
    - jobs route의 model ID 필수 여부와 exact field path는 성공 probe로만 확정하고, 미확정이면 jobs route `UNVERIFIED` 유지
    - Candidate_Label·Provider_String·다른 Candidate_Model evidence에서 field path나 domain을 생성하지 않음
    - _Requirements: 5.1, 5.2, 5.3, 5.4, 5.5, 5.6, 5.7, 5.8, 5.9, 5.10, 5.11, 5.12, 5.13, 5.14, 5.15, 5.16, 5.17, 5.18, 5.19, 5.20, 8.8, 8.9, 8.10_
  - [x]* 11.4 Evidence_Collector 단위 테스트 작성 (`scripts/test_capability_evidence_collector.py`)
    - mock transport로 예산 단정: `NOT_ADVERTISED` route 전송 0건, 조합당 성공 generation 1회, 교정 요청 최대 1회
    - transient·empty·partial·timeout에서 상태 강등 없음, 무-effort baseline 실패 시 effort probe 전송 0건, 부분 domain 검증 시 `UNVERIFIED` 유지 검증
    - 정제 검증: 저장 record에 credential·authorization·cookie·signature·raw prompt·raw body 부재, Probe_ID·Sanitized_Schema 존재
    - mock 성공이 activation evidence로 승격되지 않음을 단정
    - _Requirements: 4.17, 4.20, 4.21, 4.22, 4.23, 4.24, 4.25, 5.8, 5.16, 5.17, 5.20, 10.12, 10.13, 10.14, 12.22_

- [ ] 12. Validation_Runner CLI 구현 (`scripts/validate_gateway_model_capabilities.py` 신규)
  - [x] 12.1 CLI 골격·interpreter guard·dry-run·보고서 헤더 구현
    - `--labels`, `--report`, `--dry-run`, `--stage` 인자 처리
    - interpreter가 `ai_engine/.venv/bin/python`으로 실행 가능하지 않으면 Gateway_Probe 전송 건수를 0으로 유지하고 interpreter 환경 오류만 보고
    - `--dry-run`은 전송 0건으로 예산 계획만 출력
    - 보고서 헤더 기록: Current_Revision, interpreter absolute path, 시작 UTC ISO 8601, Same_Gateway_Environment identity, PBT seed
    - Gateway_Probe 입력은 고정된 짧은 비민감 입력과 계약 허용 최소 output/token bound로 고정
    - _Requirements: 11.1, 11.2, 11.3, 11.4, 11.5, 11.6, 11.13, 11.14_
  - [-] 12.2 stage 오케스트레이션과 보고서 본문 구현
    - stage 순서 구현: interpreter 확인 → baseline 검사 → catalog discovery → route probe → effort probe → Capability_Map 갱신 → Activation_Gate 확인
    - 보고서 본문 기록: 6개 Candidate_Label discovery 결과, Exact_Model_ID·Provider_String, Known_Route별 상태·evidence reference, model·route별 Effort_Support_Status·evidence reference, Verification_Status·fingerprint, probe별 Sanitized_Schema
    - usage·cost는 Gateway 제공값만 기록하고 미제공은 `notProvided`로 기록
    - 조합당 성공 generation 1회와 교정 최대 1회 예산을 runner 수준에서 재확인
    - 신규·변경 Active_Model은 Current_Evidence·revision·Capability_Fingerprint 일치를 확인하고 불일치면 activation 결과에서 제외, 미완료 조합은 `UNVERIFIED`로 보고
    - PBT 실행 seed와 실패 시 최소화된 counterexample을 보고서에 기록
    - _Requirements: 11.7, 11.8, 11.9, 11.10, 11.11, 11.12, 11.15, 11.16, 11.17, 11.18, 11.19, 11.20, 11.21, 11.22, 11.23, 11.24, 12.20, 12.21, 12.23, 12.24_

- [ ] 13. Checkpoint - Evidence_Collector·Validation_Runner 테스트 통과 확인
  - Ensure all tests pass, ask the user if questions arise.

- [x] 14. Effort UI와 IPC 배선
  - [x] 14.1 Effort_Control Web Component 작성 (`src/effort-control.js` 신규)
    - `customElements.define('effort-control', …)`, 단일 파일, shadow DOM 미사용
    - 표시 조건: `(modelId, route, capabilityFingerprint)` tuple의 effort status가 `SUPPORTED`일 때만 렌더. 비지원·tuple 불일치면 렌더 트리 미생성
    - 표시 내용은 verified domain만: enum은 항목 버튼, range는 경계 포함 슬라이더/스텝 입력. 허용값을 컴포넌트에 상수로 두지 않고 계약에서 주입받음
    - 변경 시 `CustomEvent('effort-change', {detail:{modelId, route, capabilityFingerprint, value}})` 발행
    - 기존 다크 산업풍 토큰(variables.css)과 드롭다운 규약 재사용
    - _Requirements: 7.1, 7.2, 7.3_
  - [x] 14.2 모델 목록·요청 payload seam 확장 (`src/main.js` 수정 — 조건부 분기만)
    - `_fetchFilteredModelCatalog()`에서 응답의 신규 `capabilities` 키를 `state.capabilities`에 보관(없으면 미설정 → 기존 경로와 동일)
    - `renderModelList()`·모델 항목 클릭 핸들러에서 선택 확정 후 `<effort-control>`에 tuple 전달
    - `refreshModelsPreservingSelection()`에서 `resolveSelection` 적용 직후 Effort_Settings 정리 호출. 카탈로그 시그니처 동일 시 아무 것도 변경하지 않음
    - 요청 payload에는 tuple 일치 검증을 통과한 경우에만 `effort` 필드 추가
    - `MODEL_CATALOG`·`ALL_MODELS`·`catalogSignature`·`resolveSelection` 기존 로직은 수정하지 않고 재사용
    - _Requirements: 6.14, 7.4, 7.5, 7.6, 7.11, 7.12, 7.13, 7.14_
  - [x] 14.3 capability IPC 핸들러 등록과 preload 노출
    - `electron/src/ipc-capability-handlers.js` 신규: `capability:load-effort-settings`, `capability:save-effort-settings`, `capability:load-map` 3개 핸들러를 `userData/capability/` 하위 파일로 처리
    - `electron/main.js::registerAllIpcHandlers()`에 `registerCapabilityHandlers(dataStore)` 1행 추가(등록은 main에서만)
    - `electron/preload.js` 화이트리스트에 위 3개 채널만 추가. `ipcRenderer` 미노출, `contextIsolation: true`·`nodeIntegration: false` 유지
    - settings 스키마는 변경하지 않고 Effort_Settings를 별도 파일로 분리, credential은 어떤 파일에도 저장하지 않음
    - _Requirements: 10.7, 10.11, 10.15_
  - [x]* 14.4 Playwright effort UI 테스트 작성 (`tests/e2e/test_effort_control_ui.py`)
    - capability payload 없음 → `<effort-control>` 미표시, 모델 드롭다운 항목 수·표시가 기준선과 동일
    - `SUPPORTED` tuple 주입 → effort UI 표시, 선택 후보가 verified domain과 정확히 일치
    - 모델 변경으로 tuple 불일치 → effort UI 즉시 숨김 + 저장 값 제거(요청 payload에 effort 필드 부재)
    - `STALE` 전이 주입 → 선택이 Active_Model 또는 명시적 미선택으로 복구
    - 다크 산업풍 토큰 적용 확인 스크린샷 저장
    - _Requirements: 7.1, 7.2, 7.3, 7.12, 7.13, 7.14, 12.2_

- [x] 15. 서버 요청 경로 seam 배선
  - [x] 15.1 capability client seam 추가 (`ai_engine/server.py` 수정 — 조건부 분기만)
    - `capability_client_for(gw, model, purpose, effort)` helper 추가: Managed_Segment entry가 아니거나 Eligible_Contract가 없으면 `(None, None)` 반환
    - 채팅·에이전트 스트리밍 seam에서 `gw_for_call = _client or gw` 형태로만 확장하여 기존 경로는 완전히 동일하게 유지
    - `effort`가 요청 body에 없으면 `None`으로 전달되어 기존 흐름과 바이트 동일
    - 선택 route가 `SUPPORTED`가 아니거나 Eligible_Contract가 없으면 Gateway 전송을 생성하지 않고 Failure_Handler 경로로 종료
    - `is_openai_model`·`route_openai_chat`·`_resolve_callable_model_id`의 Baseline_Catalog_Segment 동작은 불변
    - _Requirements: 1.13, 8.1, 8.20, 8.21, 8.22, 12.2_

- [x] 16. 무회귀 테스트
  - [x]* 16.1 5개 route baseline body 동일성 테스트 작성 (`scripts/test_capability_baseline_body_equality.py`)
    - effort 미선택 상태에서 `_build_payload`, `_build_openai_payload`, jobs 제출 body, SSE body, invoke body를 canonical 직렬화 후 **바이트 비교**
    - `EffortBoundClient`를 통과한 body와 base `GatewayClient` body가 동일함을 단정
    - `_apply_jobs_model_id` 기본 구현이 기존 동작과 바이트 동일함을 단정
    - _Requirements: 12.3_
  - [x]* 16.2 GatewayClient signature introspection 회귀 확장 (`scripts/test_gateway_signature_introspection.py` 수정)
    - 기존 public method signature 스냅샷 비교 유지
    - `EffortBoundClient` 오버라이드 화이트리스트 검사 추가: builder seam과 credential 위임 메서드 외 오버라이드가 없음을 단정
    - _Requirements: 12.4_
  - [x]* 16.3 adapter·job polling·retry 기존 회귀 자산 실행
    - `scripts/test_openai_adapter_property.py`, `scripts/test_openai_adapter_schema_examples.py`로 응답 adapter 출력 계약 보존 확인
    - `scripts/test_gateway_async_job_tooluse_preservation.py`로 Converse job polling terminal 처리 보존 확인
    - `scripts/test_gateway_openai_methods.py`로 OpenAI Responses job polling terminal 상태 매핑 보존 확인
    - `scripts/test_gateway_prefix_fallback.py`로 retry·prefix 교정 한도(호출 카운트) 보존 확인
    - 실패 시 seam 측을 교정하고 기존 자산은 수정하지 않음
    - _Requirements: 12.5, 12.6, 12.7, 12.8_
  - [x]* 16.4 `/api/models`·기존 모델 선택 무회귀 테스트 작성 (`scripts/test_capability_api_models_baseline.py`)
    - `Managed_Segment`가 빈 상태에서 `/api/models` 응답이 기준선과 동일함을 단정(모델 구성·provider 분류·capability·count·구조·`capabilities` 키 부재)
    - Managed_Segment 병합 예외 발생 시 Baseline_Catalog_Segment로 폴백함을 단정
    - `scripts/test_gateway_converse_signature_contract.py`·`scripts/test_nonopenai_chat_preserved.py`·`scripts/test_openai_model_id_passthrough.py`로 Existing_Model의 model ID·provider·verified route 선택 불변 확인
    - _Requirements: 12.1, 12.2_

- [ ] 17. Checkpoint - 무회귀·UI 테스트 통과 확인
  - Ensure all tests pass, ask the user if questions arise.

- [ ] 18. 실제 Gateway 검증 실행 (production path 근거 확정)
  - [ ] 18.1 interpreter 확인과 dry-run 예산 계획 실행
    - `ai_engine/.venv/bin/python scripts/validate_gateway_model_capabilities.py --dry-run` 실행으로 전송 0건 상태의 예산 계획 산출
    - interpreter 미실행 시 Gateway_Probe 0건과 interpreter 환경 오류 보고를 확인하고 이후 stage를 진행하지 않음
    - 계획 출력에서 조합당 성공 generation 1회·교정 최대 1회·최소 output/token bound가 반영되었는지 확인
    - _Requirements: 11.1, 11.2, 11.3_
  - [ ] 18.2 baseline 검사 stage 실행
    - `--stage baseline` 실행으로 10개 Baseline_Category record를 `userData/capability/baseline/{revision}.json`에 기록
    - Current_Revision과 UTC ISO 8601 검사 시각 기록 확인, 결손 category가 있으면 `evidenceEligible: False`로 후속 stage의 근거 집합에서 제외
    - 보고서 헤더(revision·interpreter absolute path·시작 UTC)가 기록되었는지 확인
    - _Requirements: 1.11, 1.12, 1.16, 11.4, 11.5, 11.6_
  - [ ] 18.3 catalog discovery stage 실행
    - `--stage discover --labels "opus 5" "sonnet 5" "gpt 5.6" "sol" "terra" "luna"` 실행으로 Same_Gateway_Environment catalog 조회
    - 발견된 Exact_Model_ID·Provider_String을 문자 변경 없이 기록, 미발견 라벨은 `UNVERIFIED` 유지·probe 미생성
    - catalog endpoint 부재 시 Operator_Catalog_Export 4개 검증(identity·region, UTC 시각 순서, fingerprint 재계산, sanitization manifest) 통과 여부 기록
    - 6개 Candidate_Label discovery 결과를 보고서에 기록하고 라벨 간 정보 전파가 없음을 확인
    - _Requirements: 2.1, 2.2, 2.3, 2.4, 2.5, 2.6, 2.7, 2.8, 2.9, 2.10, 2.11, 2.12, 11.7, 11.8_
  - [ ] 18.4 route probe stage 실행
    - `--stage route` 실행으로 `DISCOVERED` 모델의 advertised Known_Route에만 production Request_Builder 기반 Minimal_Request 전송
    - route별 HTTP 성공·Valid_Output·Terminal_Success를 각각 기록하고 세 조건 충족 route만 `SUPPORTED`로 기록
    - 조합당 성공 generation 1회, 명시적 교정 최대 1회, prefix 교정 동일 route 1회, 최소 output/token bound 준수를 실행 결과로 확인
    - 각 probe의 Probe_ID와 Sanitized_Schema를 보고서에 기록하고 raw prompt·raw body는 기록하지 않음
    - advertised 아님 → 전송 0건·`NOT_ADVERTISED`, 명시적 거부 → `UNSUPPORTED`/`REJECTED`, transient·empty·partial·timeout → `UNVERIFIED` 유지
    - _Requirements: 4.9, 4.10, 4.11, 4.12, 4.13, 4.14, 4.15, 4.24, 4.25, 4.26, 11.9, 11.12, 11.13, 11.14, 11.15, 11.16, 11.17, 11.18, 11.19, 11.20, 12.23_
  - [ ] 18.5 effort probe stage 실행
    - `--stage effort` 실행으로 무-effort Baseline_Request_Body 성공을 먼저 확인한 뒤에만 effort probe 전송
    - 계약 domain에 따라 enum 전체 또는 range 경계만 각 1회 검증하고, 성공한 실제 field path·실제 value를 Verification_Record에 기록
    - 부분 검증·transient·invalid value는 `UNVERIFIED`, unknown field는 `UNSUPPORTED`로 기록하고 base route 상태를 강등하지 않음
    - model·route별 Effort_Support_Status와 evidence reference를 보고서에 기록, Probe_ID·Sanitized_Schema만 남김
    - _Requirements: 5.7, 5.8, 5.9, 5.10, 5.11, 5.12, 5.13, 11.10, 11.15, 11.16_
  - [ ] 18.6 Capability_Map 갱신 stage 실행
    - `--stage map` 실행으로 Verification_Record를 `upsert`하고 Capability_Fingerprint·Catalog_Fingerprint 재계산
    - 활성 evidence 선택 규칙(최신 `verifiedAt`, 동시각은 Evidence_Record_ID 오름차순) 적용 결과 확인
    - STALE 전이 트리거(catalog 제거, provider 변경, Route_Contract 변경, Effort_Contract 변경, revision 불일치) 반영 확인, 재검증 미완료는 `STALE` 유지
    - route 상태 완전성과 승격 조건(Exact_Model_ID·Provider_String·`SUPPORTED` route ≥ 1·Complete_Record·Current_Evidence) 충족 시에만 `VERIFIED` 기록
    - `userData/capability/capability_map.json`과 evidence·catalog snapshot이 정제된 상태로 원자적 기록되었는지 확인
    - _Requirements: 3.18, 3.19, 4.27, 4.28, 6.15, 6.16, 6.17, 6.18, 6.19, 6.20, 6.21, 11.11_
  - [ ] 18.7 Activation_Gate 결과 확인 stage 실행
    - `--stage activate` 실행으로 Active_Model 목록과 탈락 이유 코드를 보고서에 기록
    - **검증되지 않은 모델을 활성 목록에 추가하지 않음을 강제**: `UNVERIFIED`·`DISCOVERED`·`REJECTED`·`STALE`·Malformed_Entry·Seed_Entry·미발견 Candidate_Label이 목록에서 0건임을 실행 결과로 확인
    - 신규·변경 Active_Model의 Current_Evidence·revision·Capability_Fingerprint 일치 검사를 수행하고 불일치 entry는 activation 결과에서 제외
    - integration probe 미완료 조합의 Gateway 지원 주장을 `UNVERIFIED`로 보고
    - `Managed_Segment`가 빈 결과라면 `/api/models`와 프론트 동작이 기준선과 동일함을 확인
    - _Requirements: 6.9, 6.10, 6.11, 6.12, 6.13, 6.14, 6.26, 11.21, 11.22, 11.23, 11.24, 12.24_

- [ ] 19. Final checkpoint - 전체 테스트와 검증 보고서 확인
  - Ensure all tests pass, ask the user if questions arise.

## Notes

- `*` 표시 하위 작업은 선택(테스트) 작업으로 빠른 MVP 시 건너뛸 수 있다. 핵심 구현 작업과 작업 18의 Gateway 검증 stage는 선택이 아니다.
- 각 하위 작업은 `_Requirements: N.M_`로 requirements.md의 granular criteria를 참조한다.
- Correctness Properties 1~10은 각각 정확히 하나의 property test로 구현하며, 테스트 파일명은 design.md의 property ↔ 테스트 파일 매핑 표를 따른다(P1 activation subset, P2 malformed 제외, P3 unsupported effort 비주입, P4 supported effort exact-once, P5 canonicalization 멱등성, P6 serialization round-trip, P7 ordering invariance, P8 selection validity, P9 sync Responses `modelId` 부재, P10 jobs `modelId` 계약 일치).
- 모든 property test는 Hypothesis `max_examples=100` 이상, `@seed(AE_PBT_SEED)` 고정 seed, 실패 시 최소화 counterexample과 재현 blob 기록을 사용하고 외부 Gateway를 호출하지 않는다.
- 순수 add 원칙: 기존 `converse`/`invoke`/스트리밍/adapter/job polling/retry·prefix 교정의 시그니처와 동작은 불변이며, `Managed_Segment`가 비면 `/api/models`와 프론트 동작이 기준선과 동일하다.
- 활성화 강제: 검증되지 않은 모델은 어떤 작업에서도 활성 목록에 추가하지 않는다. model ID·provider·route 지원·effort field path·effort 허용값은 작업 18의 production path evidence만이 채운다.
- steering 준수: LLM generation과 effort 검증은 Bedrock Gateway route 전용, credential 미저장, 영속 데이터는 `userData` 하위, IPC는 `electron/main.js` 등록·`contextIsolation: true` 유지, 프론트는 Vanilla JS Web Component, 모든 Python 실행은 `ai_engine/.venv/bin/python`.

## Task Dependency Graph

```json
{
  "waves": [
    { "id": 0, "tasks": ["1.1", "1.2", "3.1"] },
    { "id": 1, "tasks": ["1.3", "2.1", "3.2"] },
    { "id": 2, "tasks": ["2.2", "5.1"] },
    { "id": 3, "tasks": ["2.3", "2.4", "5.2", "7.1", "8.1", "9.1"] },
    { "id": 4, "tasks": ["5.3", "7.2", "8.2", "9.2"] },
    { "id": 5, "tasks": ["5.4", "5.5", "5.6", "7.3", "11.1"] },
    { "id": 6, "tasks": ["7.4", "7.5", "7.6", "7.7", "11.2", "14.1"] },
    { "id": 7, "tasks": ["11.3", "12.1", "14.2", "15.1"] },
    { "id": 8, "tasks": ["11.4", "12.2", "14.3", "16.1"] },
    { "id": 9, "tasks": ["14.4", "16.2", "16.3", "16.4", "18.1"] },
    { "id": 10, "tasks": ["18.2"] },
    { "id": 11, "tasks": ["18.3"] },
    { "id": 12, "tasks": ["18.4"] },
    { "id": 13, "tasks": ["18.5"] },
    { "id": 14, "tasks": ["18.6"] },
    { "id": 15, "tasks": ["18.7"] }
  ]
}
```
