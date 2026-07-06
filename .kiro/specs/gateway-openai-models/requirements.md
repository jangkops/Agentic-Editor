# Requirements Document

요구사항 문서: 게이트웨이 OpenAI 모델 통합 (gateway-openai-models)

## Introduction

게이트웨이에 OpenAI Responses 라우트 기반 모델(예: GPT 5.5, GPT 5.4)이 추가되었으나, 에디터의 모델 목록에 자동으로 반영되지 않고 채팅·에이전트 실행에서 사용할 수도 없다.

현재 동작(코드 실측):
- 에디터 모델 목록은 `/api/models` 엔드포인트가 boto3 `bedrock.list_foundation_models()` + `list_inference_profiles()` 로 AWS Bedrock 네이티브 카탈로그만 조회해 구성한다. 게이트웨이 카탈로그나 OpenAI 라우트 모델은 반영되지 않는다.
- 프론트 `loadModelsFromServer()`(`src/main.js`)는 로그인·앱 시작·SSO 성공·설정 새로고침 시에만 호출되며 주기적 자동 새로고침이 없다.
- 게이트웨이 클라이언트 `GatewayClient`(`ai_engine/gateway_module.py`)는 `/converse`, `/invoke`, 스트리밍(Lambda function URL)만 호출하고 botocore SigV4(`execute-api`)로 서명한다. `/openai/responses` 호출 메서드가 없으며, 응답 파싱은 Bedrock Converse 형식만 처리한다.

이 기능은 게이트웨이에 추가된 OpenAI Responses 라우트 모델이 (1) 에디터 모델 목록에 자동으로 나타나고, (2) 채팅·에이전트 실행에서 선택해 사용할 수 있도록(백엔드가 해당 모델을 `/converse`가 아닌 `/openai/responses` 또는 비동기 `/openai/responses-jobs`로 라우팅하고, OpenAI Responses 응답을 기존 채팅 스트림/도구 흐름과 호환되게 파싱) 한다. OpenAI 모델이 구성되지 않은 환경에서는 기존 Bedrock 동작을 바이트 단위로 보존한다.

이 문서는 게이트웨이 스펙 일부가 미확정인 지점에 대해 합리적 기본값을 채택하고, 검토가 필요한 지점을 "결정 노트"로 명시한다.

### 결정 노트 (검토 지점)

1. **모델 목록 소스** — 두 후보가 있다. (A) 게이트웨이에 모델 목록 조회 엔드포인트가 추가·grant되면 그것을 주기 조회한다. (B) 게이트웨이 목록 API가 없으면 에디터 측 OpenAI 모델 카탈로그 파일(`userData` 하위)에 모델을 등록하고 거기서 읽어 Bedrock 카탈로그와 병합한다. **기본값: (B)를 1차로 채택하되, (A)가 가능해지면 자동 전환 가능하도록 목록 공급자(provider)를 추상화한다.** "자동 업데이트"는 (A) 또는 (카탈로그 파일 변경 감지 + 주기 새로고침)으로 충족한다. 현재 IaC상 grant된 경로에 모델 목록 조회 API가 없으므로 (B)가 우선이다.
2. **요청/응답 스키마** — OpenAI Responses API 표준(`{model, input, ...}` 요청, `output` / `output_text` 응답)을 기본 가정한다. SigV4 서명(`execute-api`)은 기존 `GatewayClient`와 동일하게 적용한다. 게이트웨이 확정 스키마가 다르면 어댑터 계층에서 흡수한다.
3. **동기 vs 비동기** — `POST /openai/responses`(동기)를 우선 사용하고, 길거나 타임아웃 위험이 있는 요청은 `POST /openai/responses-jobs`(제출 → 폴링)로 폴백한다. 비동기는 job_id 반환 → 상태 조회(GET/POST) → 완료 판정 → 결과 수집의 폴링 방식을 기본 가정한다.
4. **모델 ID 표기** — 게이트웨이 식별자(예: `gpt-5.5` 또는 `openai.gpt-5.5`)를 그대로 사용하고, 에디터 카탈로그에서 `provider="OpenAI"`로 노출한다. 정확한 ID는 게이트웨이 확정 대기이며, 카탈로그 등록 값으로 결정한다.

### 미해결 질문 (게이트웨이 확정 대기)

- 게이트웨이가 OpenAI 모델 목록을 동적으로 제공하는 grant된 경로가 존재하는가? (결정 노트 1의 (A) 가능 여부)
- OpenAI Responses 동기/비동기 라우트의 정확한 요청·응답 필드 명세와 비동기 잡 폴링 프로토콜은 무엇인가? (결정 노트 2·3)
- 게이트웨이가 노출하는 OpenAI 모델의 정식 식별자 문자열은 무엇인가? (결정 노트 4)

## Glossary

용어 정의

- **AI_Engine**: Python FastAPI 기반 백엔드 서버 (`ai_engine/server.py`)
- **Model_List_API**: 에디터에 모델 카탈로그를 반환하는 백엔드 엔드포인트 (`/api/models`)
- **Bedrock_Catalog**: boto3 `list_foundation_models` + `list_inference_profiles` 로부터 구성되는 AWS Bedrock 네이티브 모델 카탈로그
- **OpenAI_Model**: 게이트웨이의 OpenAI Responses 라우트로 호출되는 모델(예: GPT 5.5, GPT 5.4)로, `provider="OpenAI"` 로 노출되는 모델
- **OpenAI_Model_Entry**: 단위 OpenAI 모델 항목. 모델 식별자(id), 표시 이름(name), provider(`"OpenAI"`), capability 플래그, 호출 모드(동기/비동기) 메타데이터로 구성된다
- **OpenAI_Catalog_Source**: OpenAI 모델 목록을 공급하는 추상화된 공급자. 구현체는 게이트웨이 목록 엔드포인트 조회(소스 A) 또는 OpenAI_Catalog_File 읽기(소스 B) 중 하나다
- **OpenAI_Catalog_File**: `userData` 하위에 저장되는 OpenAI 모델 등록 파일(JSON). 소스 B에서 사용한다
- **OpenAI_Catalog_Serializer**: OpenAI_Catalog_File 내용을 OpenAI_Model_Entry 목록으로 역직렬화하고 목록을 JSON으로 직렬화하는 구성요소
- **Merged_Catalog**: Bedrock_Catalog와 OpenAI 모델 목록을 병합한, Model_List_API가 반환하는 최종 모델 카탈로그
- **Model_Refresh_Scheduler**: 프론트엔드에서 모델 목록을 주기적으로 다시 조회하는 구성요소
- **Gateway_Client**: 게이트웨이를 SigV4(`execute-api`)로 서명해 호출하는 백엔드 클라이언트 (`GatewayClient`, `ai_engine/gateway_module.py`)
- **OpenAI_Responses_Route**: 게이트웨이의 동기 엔드포인트 `POST /openai/responses`
- **OpenAI_Jobs_Route**: 게이트웨이의 비동기 잡 엔드포인트 `POST /openai/responses-jobs` 및 그 상태·결과 조회 하위 경로
- **Model_Router**: 선택된 모델 식별자에 따라 Bedrock 경로(`/converse`)와 OpenAI 경로(`OpenAI_Responses_Route` / `OpenAI_Jobs_Route`) 중 하나를 선택하는 백엔드 구성요소
- **OpenAI_Response_Adapter**: OpenAI Responses 응답을 기존 채팅 스트림/도구 흐름과 호환되는 내부 표현으로 변환하는 구성요소
- **Chat_Stream**: 채팅·에이전트 실행에서 텍스트와 도구 호출을 프론트로 전달하는 기존 스트림 흐름
- **Frontend**: Electron + Vanilla JavaScript 렌더러 (`src/main.js` 및 Web Component)
- **Settings_Store**: `userData/settings/settings.json` (프로파일명·게이트웨이 설정만 저장, 자격증명 미저장)
- **userData**: Electron `app.getPath('userData')` 가 가리키는 사용자별 데이터 루트
- **Runtime_Credentials**: 요청 시점에 IPC로 전달되는 AWS 자격증명. 어떤 파일에도 저장되지 않는다

## Requirements

요구사항

### 요구사항 1: OpenAI 모델 발견 및 카탈로그 병합

**사용자 스토리:** 사용자로서, 게이트웨이에 추가된 OpenAI 모델이 에디터 모델 목록에 Bedrock 모델과 함께 나타나기를 원한다.

#### 인수 조건

1. WHEN Model_List_API가 모델 카탈로그 요청을 받으면, THE AI_Engine SHALL Bedrock_Catalog를 구성한 뒤 OpenAI_Catalog_Source에서 조회한 OpenAI_Model_Entry 목록을 병합하여 Merged_Catalog를 반환한다
2. THE AI_Engine SHALL 각 OpenAI_Model_Entry를 provider 값 `"OpenAI"` 로 분류하여 Merged_Catalog에 포함한다
3. THE AI_Engine SHALL 각 OpenAI_Model_Entry에 채팅 가능을 나타내는 capability(`chat`)를 부여하여 반환한다
4. WHERE OpenAI_Catalog_Source가 조회 가능한 OpenAI 모델을 0개 반환한 경우, THE AI_Engine SHALL Bedrock_Catalog만 포함한 카탈로그를 반환하며 그 내용은 동일 입력에 대한 기존(baseline) Bedrock 카탈로그와 모델 구성이 동일해야 한다
5. IF Bedrock_Catalog에 이미 존재하는 모델 식별자와 동일한 식별자를 가진 OpenAI_Model_Entry가 있으면, THEN THE AI_Engine SHALL Bedrock_Catalog 항목을 보존하고 중복된 OpenAI_Model_Entry를 추가하지 않는다
6. WHEN Frontend가 Merged_Catalog를 수신하면, THE Frontend SHALL 기존 denylist를 OpenAI_Model_Entry에도 적용하여 차단된 식별자를 목록에서 제외한다
7. IF OpenAI_Catalog_Source 조회가 예외를 발생시키면, THEN THE AI_Engine SHALL 원인(최대 200자)을 로그로 남기고 Bedrock_Catalog만 포함한 카탈로그를 반환한다

### 요구사항 2: OpenAI 카탈로그 공급자 추상화 (소스 A/B 전환)

**사용자 스토리:** 개발자로서, 게이트웨이에 모델 목록 API가 생기면 코드 구조 변경 없이 동적 조회로 전환하고 싶다.

#### 인수 조건

1. THE AI_Engine SHALL OpenAI_Catalog_Source를 단일 인터페이스(모델 목록 반환)로 추상화하여, OpenAI_Model_Entry 목록 조회를 구체 구현(소스 A 또는 소스 B)과 분리한다
2. WHERE 게이트웨이 모델 목록 엔드포인트가 사용 가능하도록 구성된 경우, THE OpenAI_Catalog_Source SHALL 해당 엔드포인트(소스 A)를 통해 OpenAI_Model_Entry 목록을 조회한다
3. WHERE 게이트웨이 모델 목록 엔드포인트가 구성되지 않은 경우, THE OpenAI_Catalog_Source SHALL OpenAI_Catalog_File(소스 B)에서 OpenAI_Model_Entry 목록을 조회한다
4. THE OpenAI_Catalog_Source SHALL 활성화된 소스(A 또는 B)와 무관하게 동일한 OpenAI_Model_Entry 구조(id, name, provider, capability, 호출 모드)를 반환한다

### 요구사항 3: OpenAI 카탈로그 파일 직렬화·역직렬화 (소스 B, 왕복 보존)

**사용자 스토리:** 개발자로서, 에디터에 등록한 OpenAI 모델 목록이 손실 없이 읽고 쓰여 모델 구성이 재현 가능하기를 원한다.

#### 인수 조건

1. WHEN 유효한 OpenAI_Catalog_File JSON 문자열에 대해 역직렬화가 요청되면, THE OpenAI_Catalog_Serializer SHALL 이를 OpenAI_Model_Entry 목록으로 역직렬화한다
2. WHEN OpenAI_Model_Entry 목록에 대해 직렬화가 요청되면, THE OpenAI_Catalog_Serializer SHALL 키 순서가 고정된 UTF-8 인코딩 JSON 문자열로 직렬화한다(결정론적 직렬화)
3. FOR ALL 유효한 OpenAI_Model_Entry 목록에 대해, THE OpenAI_Catalog_Serializer SHALL `직렬화 = 직렬화 ∘ 역직렬화 ∘ 직렬화` 항등(왕복 보존 속성)을 바이트 단위로 만족한다
4. IF 입력이 구문상 올바른 JSON으로 파싱되지 않으면, THEN THE OpenAI_Catalog_Serializer SHALL "invalid-json" 에러를 반환하고 부분 목록을 생성하지 않는다
5. IF 한 항목에 필수 필드(id, name) 중 하나 이상이 누락되거나 id가 1자 미만 256자 초과이면, THEN THE OpenAI_Catalog_Serializer SHALL 해당 항목 식별자와 함께 "invalid-model-entry" 에러를 반환하고 부분 목록을 생성하지 않는다
6. THE OpenAI_Catalog_Serializer SHALL OpenAI_Catalog_File을 userData 하위 경로에서만 읽고 쓴다

### 요구사항 4: 모델 목록 자동 업데이트

**사용자 스토리:** 사용자로서, 게이트웨이에 모델이 추가되면 에디터를 재시작하지 않아도 모델 목록이 자동으로 갱신되기를 원한다.

#### 인수 조건

1. WHILE 사용자가 인증된 상태로 에디터를 사용하는 동안, THE Model_Refresh_Scheduler SHALL 설정된 새로고침 주기(기본값 5분)마다 Model_List_API를 다시 조회하여 모델 목록을 갱신한다
2. WHEN 자동 새로고침 결과로 수신한 Merged_Catalog가 직전 표시 목록과 다르면, THE Frontend SHALL 모델 목록 표시와 모델 개수 표시를 갱신된 목록으로 업데이트한다
3. WHEN 자동 새로고침 결과로 수신한 Merged_Catalog가 직전 표시 목록과 동일하면, THE Frontend SHALL 현재 사용자의 모델 선택 상태를 변경하지 않는다
4. IF 자동 새로고침 조회가 실패하면, THEN THE Frontend SHALL 직전에 성공한 모델 목록을 그대로 유지하고 다음 주기에 다시 시도한다
5. WHEN 모델 목록이 갱신되어 현재 선택된 모델이 갱신된 목록에 더 이상 존재하지 않으면, THE Frontend SHALL 채팅 가능한 모델 중 하나를 선택 상태로 설정한다
6. WHEN 모델 목록이 갱신되고 현재 선택된 모델이 갱신된 목록에 여전히 존재하면, THE Frontend SHALL 현재 선택을 그대로 유지한다
7. WHERE 소스 B(OpenAI_Catalog_File)가 활성화되고 OpenAI_Catalog_File 내용이 변경된 경우, THE AI_Engine SHALL 다음 Model_List_API 조회 시 변경된 OpenAI 모델 목록을 반영한다

### 요구사항 5: OpenAI 모델 요청 라우팅 (동기/비동기)

**사용자 스토리:** 사용자로서, 채팅이나 에이전트에서 OpenAI 모델을 선택하면 게이트웨이의 OpenAI 라우트로 요청이 전달되어 정상 응답을 받고 싶다.

#### 인수 조건

1. WHEN 선택된 모델이 provider `"OpenAI"` 인 채팅·에이전트 요청을 받으면, THE Model_Router SHALL 해당 요청을 `/converse` 가 아닌 OpenAI_Responses_Route 또는 OpenAI_Jobs_Route로 라우팅한다
2. WHEN 선택된 모델이 provider `"OpenAI"` 가 아닌 채팅·에이전트 요청을 받으면, THE Model_Router SHALL 해당 요청을 기존 Bedrock 경로(`/converse` 또는 스트리밍)로 라우팅한다
3. WHEN OpenAI 모델 요청을 OpenAI_Responses_Route로 전송할 때, THE Gateway_Client SHALL OpenAI Responses 요청 본문(model, input 및 관련 필드)을 구성하고 botocore SigV4(`execute-api`)로 서명한 헤더를 포함한다
4. WHERE 동기 OpenAI_Responses_Route 호출이 설정된 동기 타임아웃(기본값 120초)을 초과할 위험이 있거나 동기 호출이 타임아웃으로 실패한 경우, THE Model_Router SHALL 동일 요청을 OpenAI_Jobs_Route로 제출하여 비동기 폴링 경로로 처리한다
5. WHEN OpenAI_Jobs_Route로 잡을 제출하면, THE Gateway_Client SHALL 반환된 잡 식별자(job_id)로 상태를 폴링하여 완료 상태에 도달할 때까지 결과를 조회하되, 설정된 최대 대기 시간(기본값 300초)을 초과하면 폴링을 중단하고 타임아웃 에러를 반환한다
6. THE Gateway_Client SHALL OpenAI 라우트 호출 시 Runtime_Credentials만 사용하며 자격증명을 어떤 파일에도 저장하지 않는다
7. IF OpenAI 라우트 호출이 토큰 만료 에러를 반환하면, THEN THE Gateway_Client SHALL 자격증명을 갱신한 뒤 최대 3회까지 재시도한다

### 요구사항 6: OpenAI 응답 파싱 및 기존 스트림 호환

**사용자 스토리:** 사용자로서, OpenAI 모델의 응답이 기존 채팅 화면과 도구 실행 흐름에서 Bedrock 모델과 동일하게 보이고 동작하기를 원한다.

#### 인수 조건

1. WHEN OpenAI_Responses_Route 또는 OpenAI_Jobs_Route가 성공 응답을 반환하면, THE OpenAI_Response_Adapter SHALL 응답의 출력 텍스트(`output` / `output_text`)를 Chat_Stream이 소비하는 내부 텍스트 표현으로 변환한다
2. THE OpenAI_Response_Adapter SHALL 변환된 응답을 기존 Bedrock Converse 응답을 소비하는 Chat_Stream 인터페이스와 동일한 구조로 제공한다
3. WHERE OpenAI 응답에 도구 호출(tool call) 정보가 포함된 경우, THE OpenAI_Response_Adapter SHALL 해당 도구 호출을 기존 도구 실행 흐름이 처리하는 도구 호출 표현으로 변환한다
4. WHERE OpenAI 응답에 토큰 사용량(usage) 정보가 포함된 경우, THE OpenAI_Response_Adapter SHALL 해당 사용량을 기존 사용량·비용 추적이 소비하는 표현으로 변환한다
5. IF OpenAI 응답이 예상 스키마(출력 텍스트 필드 부재 등)와 일치하지 않으면, THEN THE OpenAI_Response_Adapter SHALL 원인(최대 200자)을 포함한 "invalid-openai-response" 에러를 호출자에게 반환하고 부분 텍스트를 Chat_Stream에 전달하지 않는다

### 요구사항 7: 오류 처리 및 폴백

**사용자 스토리:** 사용자로서, OpenAI 모델 호출이 실패하거나 거부되어도 명확한 안내를 받고 작업이 중단되지 않기를 원한다.

#### 인수 조건

1. IF OpenAI 라우트 호출이 403(권한·쿼터 거부)을 반환하면, THEN THE AI_Engine SHALL 기존 403 처리 흐름과 동일하게 QuotaExceededError를 호출자에게 전달한다
2. IF OpenAI 라우트 호출이 422를 반환하면, THEN THE AI_Engine SHALL 원인을 로그로 남기고 사용자에게 표시 가능한 에러를 반환한다
3. IF OpenAI 라우트 호출이 500을 반환하면, THEN THE Gateway_Client SHALL 1초·2초·4초의 지수 백오프로 최대 3회 재시도한다
4. IF 동기 OpenAI_Responses_Route 호출과 비동기 OpenAI_Jobs_Route 폴백이 모두 실패하면, THEN THE AI_Engine SHALL 원인(최대 200자)을 포함한 에러를 호출자에게 반환하고 부분 응답을 Chat_Stream에 전달하지 않는다
5. IF 선택된 OpenAI 모델 식별자가 게이트웨이에서 거부(미지원 모델)되면, THEN THE AI_Engine SHALL "openai-model-unsupported" 에러와 모델 식별자를 포함한 응답을 반환한다

### 요구사항 8: 하위 호환 보존

**사용자 스토리:** 기존 사용자로서, OpenAI 모델 기능이 추가되어도 기존 Bedrock 모델 사용 경험이 전혀 바뀌지 않기를 원한다.

#### 인수 조건

1. WHERE OpenAI_Catalog_Source가 0개의 OpenAI 모델을 반환하는 경우, THE Model_List_API SHALL 동일 입력에 대해 기존(baseline) Bedrock 카탈로그와 모델 구성·provider 분류·capability가 동일한 응답을 반환한다
2. WHEN provider `"OpenAI"` 가 아닌 모델로 채팅·에이전트를 실행하면, THE AI_Engine SHALL OpenAI 통합 도입 이전과 동일한 Bedrock 경로·요청 본문·응답 파싱으로 처리한다
3. THE AI_Engine SHALL 기존 Gateway_Client의 `converse`·`invoke`·스트리밍 메서드의 동작과 시그니처를 변경 없이 유지한다
4. WHERE OpenAI 모델이 구성되지 않은 환경인 경우, THE Frontend SHALL 모델 목록·선택·표시 동작을 OpenAI 통합 도입 이전과 동일하게 유지한다

### 요구사항 9: 보안 및 자격증명 제약 준수

**사용자 스토리:** 운영자로서, OpenAI 모델 통합이 프로젝트의 보안·자격증명 정책을 위반하지 않기를 원한다.

#### 인수 조건

1. THE AI_Engine SHALL 모든 OpenAI 모델 LLM 호출을 게이트웨이 경유로만 수행하며 OpenAI SDK나 직접 외부 호출을 사용하지 않는다
2. THE Gateway_Client SHALL OpenAI 라우트 호출에 사용하는 AWS 자격증명을 어떤 파일에도 저장하지 않고 Runtime_Credentials로만 사용한다
3. THE Settings_Store SHALL OpenAI 모델 관련 설정으로 프로파일명·게이트웨이 설정·모델 식별자만 저장하며 자격증명을 저장하지 않는다
4. THE AI_Engine SHALL 로그에 API 토큰을 출력할 때 앞 4자만 남기고 나머지를 마스킹한다(`token.substring(0,4) + '****'`)
5. THE OpenAI_Catalog_File 및 모든 OpenAI 모델 관련 영속 데이터 SHALL userData 하위에만 저장된다
6. THE Frontend SHALL OpenAI 모델 관련 IPC 핸들러를 main 프로세스에만 등록하고 contextIsolation을 유지하며 ipcRenderer를 렌더러에 노출하지 않는다
