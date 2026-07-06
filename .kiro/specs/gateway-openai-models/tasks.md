# Implementation Plan: 게이트웨이 OpenAI 모델 통합 (gateway-openai-models)

## Overview

이 계획은 design.md의 모듈/파일 매핑과 11개 Correctness Properties를 코드 작성·수정·테스트 작업으로 분해한다.

**절대 원칙 — 순수 add(추가) 방식:** 기존 게이트웨이/에디터 구성을 절대 훼손하지 않는다.
- 게이트웨이 자체는 손대지 않는다(호출만). 게이트웨이 측 작업 없음.
- 기존 `GatewayClient`의 `converse`/`invoke`/스트리밍 메서드 시그니처·동작은 불변. 신규 메서드만 추가한다.
- `/api/models`의 OpenAI 병합은 try/except graceful — 실패 시 기존 Bedrock-only로 폴백한다.
- OpenAI 모델 0개 구성 시 기존 동작을 바이트 단위로 보존(요구사항 8, Property 3)하며 회귀 테스트로 검증한다.
- 신규 파일(`openai_catalog.py`, `openai_adapter.py`)은 기존 코드와 독립. 기존 파일 수정은 비침습적 분기 추가만 한다.

구현 언어: 백엔드 Python 3.11+ (FastAPI/HTTPX), 프론트 Vanilla JS (Electron renderer). 속성 테스트는 Hypothesis(100회+ 반복) 사용.

## Tasks

- [x] 1. OpenAI 카탈로그 모듈 구축 (`ai_engine/openai_catalog.py` 신규)
  - [x] 1.1 데이터 모델·예외·후보 키 골격 작성
    - `OpenAIModelEntry` TypedDict(id, name, provider, capabilities, mode) 정의
    - `CatalogError(Exception)`(code, detail≤200자) 정의: code ∈ {"invalid-json","invalid-model-entry"}
    - 기본 시드 상수(`openai.gpt-5.5`="GPT 5.5", `openai.gpt-5.4`="GPT 5.4") 정의
    - 신규 독립 모듈로 기존 코드와 import 의존 없음(비침습)
    - _Requirements: 2.1, 3.4, 3.5_
  - [x] 1.2 OpenAICatalogSerializer 역직렬화/직렬화 구현
    - `deserialize(json_str)`: json.loads 실패→`CatalogError("invalid-json")`; 항목 검증(id/name 필수, 1≤len(id)≤256) 위반→`CatalogError("invalid-model-entry", detail=id)`; 부분 목록 생성 금지(전부 유효해야 반환); provider/capabilities/mode 기본값 정규화
    - `serialize(entries)`: id 오름차순 정렬, 키 집합 정규화(id,name,provider,capabilities,mode), `json.dumps(sort_keys=True, ensure_ascii=False, separators=(",",":"))`로 결정론적 직렬화
    - 정규화를 deserialize 단계에서 1회 수행하여 왕복 보존 보장
    - _Requirements: 3.1, 3.2, 3.3, 3.5_
  - [x] 1.3 OpenAI_Catalog_Source 추상화 + 소스 A/B 구현
    - `OpenAICatalogSource`(Protocol): `list_models() -> list[OpenAIModelEntry]` (조회 불가 시 빈 목록, 예외 아님)
    - `FileCatalogSource`(소스 B): `userData/openai/openai_catalog.json` 읽기→deserialize→목록; 파일 부재 시 기본 시드 반환; userData 하위 경로에서만 read/write
    - `GatewayListSource`(소스 A 스텁): 게이트웨이 목록 엔드포인트 조회→동일 구조 정규화
    - `get_catalog_source(settings)`: `settings['openai_list_endpoint']` 있으면 SourceA, 없으면 SourceB 반환(무중단 전환 추상화)
    - _Requirements: 2.1, 2.2, 2.3, 2.4, 3.6, 9.5_
  - [x] 1.4 merge_openai_into_catalog 병합 함수 구현
    - 시그니처: `merge_openai_into_catalog(bedrock_catalog, openai_entries) -> dict`
    - `openai_entries==[]`→bedrock_catalog 변경 없이 반환(baseline 보존)
    - 각 entry를 provider `"OpenAI"` 그룹에 추가, capabilities.chat=True 보장
    - 기존 카탈로그 어느 provider에든 동일 id 존재 시 그 entry 스킵(Bedrock 보존, 중복 추가 금지)
    - _Requirements: 1.1, 1.2, 1.3, 1.4, 1.5, 8.1_

- [x] 2. 카탈로그 병합·직렬화 속성 테스트
  - [x]* 2.1 병합/정규화 속성 테스트 작성 (`scripts/test_openai_catalog_merge_property.py`)
    - **Property 1: 병합은 중복이 아닌 모든 OpenAI 항목을 OpenAI provider로 포함한다** — _Validates: Requirements 1.1, 1.2_
    - **Property 2: 정규화된 모든 OpenAI 항목은 chat capability를 가진다** — _Validates: Requirements 1.3_
    - **Property 3: 빈 OpenAI 목록 병합은 Bedrock baseline을 보존한다** — _Validates: Requirements 1.4, 8.1_
    - **Property 4: 중복 식별자는 Bedrock 항목을 보존하고 OpenAI를 추가하지 않는다** — _Validates: Requirements 1.5_
    - Hypothesis 100회+ 반복, 태그 주석 `Feature: gateway-openai-models, Property N: ...`
  - [x]* 2.2 직렬화 속성 테스트 작성 (`scripts/test_openai_catalog_serialize_property.py`)
    - **Property 5: 카탈로그 직렬화 왕복 보존** (`serialize(deserialize(serialize(x)))==serialize(x)` 바이트 동일) — _Validates: Requirements 3.1, 3.3_
    - **Property 6: 직렬화는 결정론적이다** (의미 동등 입력→동일 UTF-8 바이트) — _Validates: Requirements 3.2_
    - **Property 7: 유효하지 않은 항목은 부분 목록 없이 거부된다** (`invalid-model-entry`, 부분 목록 미생성) — _Validates: Requirements 3.5_
    - Hypothesis 100회+ 반복, 태그 주석 부착

- [x] 3. Checkpoint - 카탈로그 모듈 테스트 통과 확인
  - Ensure all tests pass, ask the user if questions arise.

- [x] 4. GatewayClient OpenAI 메서드 추가 (`ai_engine/gateway_module.py` 수정 — 신규 메서드만)
  - [x] 4.1 OpenAI 페이로드 빌더 + 예외 타입 추가
    - `_build_openai_payload(model_id, messages, system_prompt)`: `{"model", "input"}` 구성, system_prompt→`instructions`
    - `_to_openai_input(messages)`: Bedrock 스타일 messages→OpenAI input 메시지 정규화
    - 예외 타입 추가: `OpenAISurfaceError`, `SyncTimeout`, `JobTimeout`, `JobFailed`, `OpenAIModelUnsupported` (`QuotaExceededError`는 기존 재사용)
    - **기존 `converse`/`invoke`/스트리밍 메서드 시그니처·동작 불변** — 추가만 한다
    - _Requirements: 5.3, 8.3_
  - [x] 4.2 openai_responses_sync 동기 호출 구현
    - `POST {gateway_url}/openai/responses`; body는 4.1 빌더, headers는 기존 `self._sign("POST", url, body)` SigV4(execute-api) 재사용
    - 403→`QuotaExceededError`, 422→`OpenAISurfaceError`(원인≤200자), 500→1s/2s/4s 지수 백오프 최대 3회
    - 토큰 만료(`_is_expired_error`)→`force_refresh_creds` 후 최대 3회 재시도
    - httpx 타임아웃→`SyncTimeout`; 미지원 모델 응답→`OpenAIModelUnsupported(model_id)`
    - Runtime_Credentials만 사용, 자격증명 미저장
    - _Requirements: 5.3, 5.6, 5.7, 7.1, 7.2, 7.3, 7.5, 9.1, 9.2_
  - [x] 4.3 비동기 잡 제출·폴링 구현
    - `openai_responses_job_submit(...)`: `POST {gateway_url}/openai/responses-jobs`→job_id 방어적 추출(후보 키 `job_id`/`jobId`/`id`/`job`/`task_id`); 동일 403/422/500/토큰만료 규칙
    - `_openai_poll_job(job_id, poll_interval=5, max_wait=300)`: status 방어적 판정(completed/succeeded→결과; failed/cancelled/canceled/error→`JobFailed`; 그 외→sleep 후 재조회); 누적>max_wait→`JobTimeout`
    - `openai_responses_job_submit_and_poll(...)`: 제출+폴링 결합
    - _Requirements: 5.4, 5.5, 5.6, 5.7, 7.3, 7.4_
  - [x]* 4.4 GatewayClient OpenAI 메서드 단위 테스트 작성
    - httpx 모킹으로 403/422/500 백오프, 토큰 만료 재시도, 동기 타임아웃→SyncTimeout, 폴링 완료/실패/타임아웃 경로 검증
    - 기존 메서드 시그니처 introspection 검사(불변 확인)
    - _Requirements: 5.4, 5.5, 5.7, 7.3, 8.3_

- [x] 5. OpenAI 응답 어댑터 구축 (`ai_engine/openai_adapter.py` 신규)
  - [x] 5.1 어댑터 추출·변환 함수 구현
    - 후보 키 상수(`_TEXT_KEYS`, `_JOBID_KEYS`, `_STATUS_KEYS`, `_USAGE_KEYS`)와 예외 `InvalidOpenAIResponse` 정의
    - `extract_text(raw)`: output_text 최우선, 없으면 output[].content[].text 수집(문자열/배열/중첩 dict 방어적)
    - `extract_tool_calls(raw)`: OpenAI tool/function call→`[{"toolUse":{toolUseId,name,input}}]`
    - `extract_usage(raw)`: input_tokens/output_tokens 등→기존 usage 표현
    - `to_converse(raw)`: `{output:{message:{content:[{text}, <tool blocks>]}}, ...}` 반환; 텍스트 추출 실패→`InvalidOpenAIResponse`(≤200자), 부분 텍스트 미전달
    - 신규 독립 모듈(비침습)
    - _Requirements: 6.1, 6.2, 6.3, 6.4, 6.5_
  - [x]* 5.2 어댑터 속성 테스트 작성 (`scripts/test_openai_adapter_property.py`)
    - **Property 10: 어댑터는 출력 텍스트를 정확히 추출해 Converse 구조로 변환한다** (임의 텍스트 t 담은 `output_text`/`output[].content[].text` 응답→Converse 구조, 추출 텍스트==t) — _Validates: Requirements 6.1, 6.2_
    - Hypothesis 100회+ 반복, 태그 주석 부착
  - [x]* 5.3 어댑터 스키마 불일치 예제 테스트 작성
    - 출력 텍스트 필드 부재→`InvalidOpenAIResponse`, 부분 텍스트 미전달 검증
    - tool call/usage 변환 대표 예제 검증
    - _Requirements: 6.3, 6.4, 6.5_

- [x] 6. Checkpoint - 게이트웨이·어댑터 테스트 통과 확인
  - Ensure all tests pass, ask the user if questions arise.

- [x] 7. Model_Router + server.py 병합·라우팅 (`ai_engine/server.py` 수정 — 비침습 분기 추가)
  - [x] 7.1 list_models에 OpenAI 병합 통합 (graceful)
    - 기존 Bedrock 카탈로그 조립 직후 `get_catalog_source`로 OpenAI 목록 조회→`merge_openai_into_catalog`
    - 전체 병합을 try/except로 감싸 예외 시 원인(≤200자) 로그 후 Bedrock-only 반환(폴백)
    - `count`를 병합 후 총합으로 갱신; OpenAI 0개 시 `"OpenAI"` 키 미추가(baseline 동일)
    - _Requirements: 1.1, 1.6, 1.7, 8.1_
  - [x] 7.2 is_openai_model + route_chat 라우팅 로직 구현
    - `is_openai_model(model_id, openai_ids)`: 카탈로그 멤버십 우선, 보조로 `openai.` prefix
    - `route_chat(...)`: 비-OpenAI→기존 `gw.converse`(호출부 변경 없음); OpenAI→`openai_responses_sync` 우선, `SyncTimeout`/실패 시 `openai_responses_job_submit_and_poll` 폴백→`openai_adapter.to_converse`
    - 동기+비동기 모두 실패 시 원인(≤200자) 에러, 부분 응답 Chat_Stream 미전달
    - _Requirements: 5.1, 5.2, 5.4, 7.4, 8.2_
  - [x] 7.3 채팅·에이전트 엔드포인트에 OpenAI 분기 연결
    - `run-stream`/`run-agent` 등 스트리밍 엔드포인트에서 OpenAI 모델일 때 `route_chat` 완성 텍스트를 단일 `content_block_delta`+`message_stop` SSE 이벤트열로 감싸 기존 소비 코드와 호환
    - 비-OpenAI 경로는 기존 흐름 그대로(바이트 동일)
    - _Requirements: 5.1, 5.2, 8.2_
  - [x]* 7.4 라우팅 분기 속성 테스트 작성 (`scripts/test_openai_routing_property.py`)
    - **Property 9: provider에 따른 라우팅 분기 정확성** (OpenAI 식별자→OpenAI 라우트, 그 외→`/converse`) — _Validates: Requirements 5.1, 5.2, 8.2_
    - 게이트웨이 호출 모킹, Hypothesis 100회+ 반복, 태그 주석 부착
  - [x]* 7.5 오류 매핑·폴백 예제 테스트 작성
    - 403→QuotaExceededError, 422→표시 에러, 500 백오프, 동기→비동기 폴백, 미지원 모델 거부 검증
    - _Requirements: 7.1, 7.2, 7.3, 7.5_

- [x] 8. 토큰 마스킹 유틸 (`ai_engine` 로깅 경로 — 비침습 추가)
  - [x] 8.1 토큰 마스킹 함수 구현·적용
    - `mask_token(token)`: 앞 4자 + `"****"`, 4자 이하도 안전 처리; OpenAI 라우트 로그 출력 지점에 적용
    - _Requirements: 9.4_
  - [x]* 8.2 토큰 마스킹 속성 테스트 작성 (`scripts/test_token_mask_property.py`)
    - **Property 11: 토큰 마스킹은 앞 4자만 남기고 원문을 노출하지 않는다** (4자 초과 토큰의 전체 원문 미포함) — _Validates: Requirements 9.4_
    - Hypothesis 100회+ 반복, 태그 주석 부착

- [x] 9. 프론트 Model_Refresh_Scheduler + 선택 보존 (`src/main.js` 수정 — 비침습 추가)
  - [x] 9.1 선택 보존/복구 순수 함수 추출
    - `resolveSelection(prevId, prevCatalog, nextCatalog)` 순수 함수: 동일 카탈로그→선택 불변; prevId가 다음 목록에 존재→유지; 부재→채팅 가능 모델(있으면) 선택
    - `catalogSignature(catalog)` 비교용 시그니처 함수
    - 테스트 가능하도록 DOM/타이머와 분리된 순수 로직으로 추출
    - _Requirements: 4.3, 4.5, 4.6_
  - [x] 9.2 Model_Refresh_Scheduler + refreshModelsPreservingSelection 구현
    - `startModelRefreshScheduler()`: 기본 300초(범위 60~3600, settings 조정), 인증 상태(`state.authenticated`)에서만 실행
    - `refreshModelsPreservingSelection()`: `/api/models` 조회→denylist 재사용 필터→시그니처 동일 시 선택/표시 불변; 변경 시 목록·카운트 갱신 후 `resolveSelection`로 선택 처리; 조회 실패 시 직전 성공 목록 유지·다음 주기 재시도
    - OpenAI 미구성 시 모델 목록·선택·표시 동작 기존과 동일(불변 보존)
    - _Requirements: 4.1, 4.2, 4.3, 4.4, 4.5, 4.6, 4.7, 1.6, 8.4_
  - [x]* 9.3 선택 보존 속성 테스트 작성 (`scripts/test_model_selection_property.*`)
    - **Property 8: 모델 갱신은 선택 상태를 보존하거나 유효하게 복구한다** (갱신 후 selectedModel은 항상 멤버; 존재 시 유지, 부재 시 채팅 가능 모델 선택; 동일 카탈로그면 불변) — _Validates: Requirements 4.3, 4.5, 4.6_
    - fast-check(JS) 또는 Python 포팅 선택 로직, 100회+ 반복, 태그 주석 부착

- [x] 10. Checkpoint - 라우팅·마스킹·프론트 테스트 통과 확인
  - Ensure all tests pass, ask the user if questions arise.

- [x] 11. 하위 호환 회귀 테스트 (요구사항 8)
  - [x]* 11.1 /api/models baseline 동일 회귀 테스트 작성
    - OpenAI 0개 카탈로그로 `/api/models` 호출→baseline 응답과 모델 구성·provider 분류·capability·count·구조 동일 검증
    - _Requirements: 8.1_
  - [x]* 11.2 비-OpenAI 채팅 경로 보존 회귀 테스트 작성
    - 비-OpenAI 모델 채팅→기존 `gw.converse`/`stream_sse_realtime` 경로·요청 본문·응답 파싱 그대로 검증
    - _Requirements: 8.2_
  - [x]* 11.3 기존 메서드 시그니처 introspection 회귀 테스트 작성
    - `converse`/`invoke`/스트리밍 메서드 시그니처 불변 검증(inspect.signature 비교)
    - _Requirements: 8.3, 8.4_

- [x] 12. 통합 검증 및 보안 구조 검사
  - [x]* 12.1 OpenAI 경로 통합 테스트 작성
    - 게이트웨이 모킹으로 카탈로그 병합→라우팅→어댑터 변환→Chat_Stream 호환 출력 엔드투엔드 검증(동기·비동기 폴백 양 경로)
    - _Requirements: 1.1, 5.1, 6.1, 6.2_
  - [x]* 12.2 보안 제약 구조 검사 테스트 작성
    - OpenAI 호출 게이트웨이 경유만(SDK/직접 호출 부재), 자격증명 미저장, `OpenAI_Catalog_File`이 userData 하위 경로인지, settings에 자격증명 미포함 검사
    - _Requirements: 9.1, 9.2, 9.3, 9.5_

- [x] 13. Final checkpoint - 전체 테스트 통과 확인
  - Ensure all tests pass, ask the user if questions arise.

## Notes

- `*` 표시 작업은 선택(테스트) 작업으로 빠른 MVP 시 건너뛸 수 있다. 핵심 구현 작업은 선택이 아니다.
- 각 작업은 특정 요구사항(_Requirements: N.M_)을 참조해 추적성을 보장한다.
- 속성 테스트는 11개 Correctness Properties를 각각 단일 속성 테스트로 구현하며 Hypothesis 100회+ 반복을 사용한다. 속성↔테스트 파일 매핑은 design.md의 표를 따른다(Property 1~4: merge, 5~7: serialize, 8: 선택 보존, 9: routing, 10: adapter, 11: token mask).
- 순수 add 방식: 신규 파일은 독립적이며 기존 파일 수정은 비침습 분기 추가만으로 제한한다. `/api/models` OpenAI 병합은 try/except graceful, OpenAI 0개 시 baseline 바이트 보존(Property 3)을 회귀 테스트로 검증한다.
- steering 준수: 모든 LLM 호출은 게이트웨이 경유(OpenAI SDK 미사용), 자격증명 미저장, 영속 데이터는 userData 하위, IPC는 main 등록·contextIsolation 유지, 프론트는 Vanilla JS.

## Task Dependency Graph

```json
{
  "waves": [
    { "id": 0, "tasks": ["1.1"] },
    { "id": 1, "tasks": ["1.2", "1.3", "1.4", "4.1", "5.1", "8.1", "9.1"] },
    { "id": 2, "tasks": ["2.1", "2.2", "4.2", "4.3", "5.2", "5.3", "8.2", "9.2"] },
    { "id": 3, "tasks": ["4.4", "7.1", "7.2", "9.3"] },
    { "id": 4, "tasks": ["7.3", "7.4", "7.5", "11.1", "11.2", "11.3"] },
    { "id": 5, "tasks": ["12.1", "12.2"] }
  ]
}
```
