# Media Output Quality Bugfix Design

## Overview

PPTX/PDF 산출물이 실제 Bedrock 이미지 모델(Stability SD 3.5 / SD Ultra / Stable Image Core, Titan Image v2, Nova Canvas) 대신 항상 `matplotlib (native)` 경로로 흘러간다. 세 가지 결함이 결합된 결과다 — (1) `_looks_structural` 휴리스틱이 일반 한글 키워드("프로젝트", "구조", "흐름도")만으로도 true 를 반환해 정상 게이트웨이 환경에서도 Bedrock 호출을 우회하고, (2) image-gen circuit breaker 가 access-denied 한 번에 5분간 무음으로 모든 호출을 단락시키며, (3) 운영자가 회로 상태/모델 체인/환경변수를 확인할 진단 표면이 없다.

본 설계는 세 결함을 다음과 같이 함께 해결한다.

- **휴리스틱 정밀화**: `_looks_structural` 을 키워드 매칭에서 _신호_(경로, 화살표 체인, 테이블 행) 매칭으로 재작성. 일반 단어만으로는 true 가 되지 않는다.
- **무음 회로 차단 가시화**: 회로 차단 시 단락 응답 JSON 에 `recentAttempts` 와 한·영 `actionable` 메시지를 포함시켜 채팅 패널이 사용자에게 권한 문제임을 즉시 알려준다.
- **진단 엔드포인트 추가**: `GET /api/debug/image-gen-status` 가 회로 상태, 체인 구성, `_select_image_models` 미리보기, 환경변수, 최근 10 회 호출 기록을 한 번에 노출.
- **호출 기록 링버퍼**: `_IMAGE_GEN_ATTEMPTS = deque(maxlen=10)` 가 단락 응답과 진단 엔드포인트의 단일 데이터 소스 역할을 한다.

수정 범위는 `ai_engine/server.py` 한 파일에 국한된다. 게이트웨이 클라이언트, `_select_image_models` 순서 로직, parallel best-of-N 경로, PPTX 레이아웃 좌표는 변경하지 않는다.

## Glossary

- **Bug_Condition (C)**: 시각적 의도가 있는 PPTX/PDF 요청 + 게이트웨이 정상 → 그러나 산출물 메타의 모든 image `model` 필드가 `"matplotlib (native)"` 인 상태. 또는 회로가 차단되어 단락 응답이 권한 정보 없이 반환되는 상태. 또는 일반 한글 키워드만 등장하는 본문에서 `_looks_structural` 이 true 를 반환하는 상태.
- **Property (P)**: Bedrock 게이트웨이가 정상이면 임베드 이미지 중 최소 한 개가 실제 Bedrock 모델 id 메타를 갖는다. 회로 차단 시에는 `actionable` 한·영 메시지가 응답에 포함된다. 일반 키워드만 있는 본문은 `_looks_structural` 에서 false 가 된다.
- **Preservation**: 진짜 구조 다이어그램 경로(경로/화살표/테이블 신호 존재 시 matplotlib 라우팅), `AE_FORCE_NATIVE_DIAGRAM=1` 강제 폴백, 텍스트 전용 경로, 회로 차단 트립 동작 자체, 회로 TTL 만료 후 재시도, PPTX 좌우 분리 레이아웃 좌표.
- **`_looks_structural(description, title, body)`**: `ai_engine/server.py:1130` 의 휴리스틱. 본 디자인에서 _키워드_ 매칭에서 _신호_ 매칭으로 재작성된다.
- **`_IMAGE_GEN_CIRCUIT`**: `ai_engine/server.py:683` 의 모듈 전역 dict `{"disabled_at": 0, "ttl": 300}`. 모든 Bedrock 이미지 모델이 access-denied 를 반환했을 때 `_image_gen_trip_circuit(reason)` 이 `disabled_at = time.time()` 을 세팅한다.
- **`_IMAGE_GEN_ATTEMPTS`**: 본 설계에서 새로 추가하는 모듈 전역 `collections.deque(maxlen=10)`. `_tool_generate_image` 의 parallel `asyncio.gather` 가 끝난 직후 각 시도 결과가 append 된다.
- **Native renderer marker**: `"matplotlib (native)"` (line 1733) — 산출물 메타 `model` 필드에 기록되는 리터럴. 버그 컨디션 판정의 핵심 술어.
- **`_force_generate_from_text`**: `ai_engine/server.py:5964` 의 본 라우팅 함수. line 6050 의 `use_native = visual_intent and (circuit_broken OR _looks_structural(...))` 조건이 결함의 트리거.
- **`IMAGE_MODELS` 체인**: `ai_engine/server.py:571` — `STABILITY_GENERATIVE_IDS + ["amazon.nova-canvas-v1:0", "amazon.titan-image-generator-v2:0"]`.

## Bug Details

### Bug Condition

버그는 세 가지 별개 표면에서 동시에 관찰된다. 모두 단일 형식 술어로 묶을 수 있다.

**Formal Specification:**
```
FUNCTION isBugCondition(input)
  INPUT:
    input.scenario     ∈ { "render_pptx", "circuit_short_circuit", "looks_structural" }
    input.body         : str           # 사용자 본문
    input.visualIntent : bool          # render_pptx 시
    input.gatewayState : "ok" | "denied_all" | "broken"
    input.outputMeta   : list[{model: str}]   # render_pptx 시 산출물 이미지 메타

  OUTPUT: boolean

  IF input.scenario == "render_pptx":
    # 게이트웨이 정상이고 시각 의도 있는데 모든 이미지가 native 로만 채워지면 버그.
    RETURN input.visualIntent == True
       AND input.gatewayState == "ok"
       AND ALL m IN input.outputMeta : m.model == "matplotlib (native)"

  IF input.scenario == "circuit_short_circuit":
    # 회로 차단 단락 응답에 actionable 또는 recentAttempts 가 비어 있으면 버그.
    RETURN input.gatewayState == "broken"
       AND ("actionable" NOT IN response_json
            OR "recentAttempts" NOT IN response_json
            OR len(response_json["recentAttempts"]) == 0)

  IF input.scenario == "looks_structural":
    # 본문에 구조 신호(경로/화살표/테이블)가 하나도 없는데 _looks_structural 이 true 면 버그.
    RETURN hasPathToken(input.body) == False
       AND hasArrowChain(input.body) == False
       AND hasMarkdownTableRow(input.body) == False
       AND _looks_structural(input.body, "", "") == True
END FUNCTION
```

### Examples

- **`render_pptx`**: 본문 = "프로젝트 아키텍처 다이어그램을 PPTX 로 만들어줘", 게이트웨이 정상 → 기대: 임베드 이미지 메타 중 최소 1 개가 `stability.*` 또는 `amazon.nova-canvas-v1:0` 또는 `amazon.titan-image-generator-v2:0`. 실제: 모든 이미지가 `"matplotlib (native)"`.
- **`looks_structural` 과매칭**: 본문 = "이번 분기 프로젝트 구조 변경 보고서. 흐름도 형태로 정리." → 경로 토큰, 화살표, 테이블 행 모두 0 개. 기대: false. 실제: true (한글 키워드 "프로젝트"/"구조"/"흐름도" 가 매칭).
- **`circuit_short_circuit` 무음**: 모든 Bedrock 이미지 모델이 access-denied → 회로 차단됨 → 다음 요청. 기대 응답: `{ "fallback": "...", "actionable": "Bedrock 게이트웨이가 image-gen 라우트를 거부했습니다 — 권한 필요 모델: [...]", "recentAttempts": [...] }`. 실제: `{ "fallback": "matplotlib" }` 만 반환되어 사용자가 권한 문제임을 알 수 없다.
- **Edge: 진짜 구조 본문**: 본문 = "src/server.py -> ai_engine/gateway_module.py" → 경로(`/` 포함) + 화살표 모두 매칭 → `_looks_structural` true 가 정상 동작 (보존 대상).

## Expected Behavior

### Preservation Requirements

**Unchanged Behaviors:**

- 본문에 `/` 또는 `\` 디렉토리 경로 토큰이 있을 때 matplotlib 네이티브 트리/플로우 라우팅이 그대로 유지되어야 한다 (Req 3.1).
- 본문에 화살표 체인(`->`, `→`, `⇒`)이 있을 때 matplotlib 라우팅이 그대로 유지되어야 한다 (Req 3.1).
- 본문에 markdown 테이블 행(`| ... |`)이 있을 때 matplotlib 라우팅이 그대로 유지되어야 한다 (Req 3.1).
- `AE_FORCE_NATIVE_DIAGRAM=1` 일 때 게이트웨이 상태/휴리스틱 결과 무관하게 matplotlib 경로 사용 (Req 3.2).
- 회로 차단 트립 메커니즘 자체 — 모든 모델 access-denied 시 `_IMAGE_GEN_CIRCUIT.disabled_at` 세팅 (Req 3.3).
- 시각적 의도가 없는 PPTX/PDF 요청에서 이미지 모델 호출이 발생하지 않아야 한다 (Req 3.4).
- PPTX 좌우 분리 레이아웃 좌표 — 본문 `(0.6, 1.6, w=6.0, h=5.4)`, 이미지 `x=7.0` (Req 3.5).
- 성공 시 메타 `model` 필드에 실제 Bedrock 모델 id 기록 (Req 3.6).
- 회로 TTL(300s) 만료 후 자동 복구 — `disabled_at` 0 리셋 후 재시도 (Req 3.7).

**Scope:**

이 fix 의 영향 범위는 (a) `_looks_structural` 술어, (b) `_tool_generate_image` 의 단락 응답 JSON 형태, (c) 새 진단 엔드포인트, (d) 새 attempts 링버퍼 네 곳뿐이다. 다음은 완전히 영향받지 않는다.

- `gateway_module.py` 의 `invoke_model` 시그니처 및 동작
- `_select_image_models(prompt, hint)` 의 keyword-based 정렬 로직
- `_tool_generate_image` 의 parallel best-of-N (`asyncio.gather`) 코어 경로
- PPTX 좌우 분리 레이아웃 좌표 및 placeholder 구성
- 프런트엔드 — Electron 채팅 패널은 응답 JSON 의 `actionable` 필드를 이미 그대로 렌더한다

## Hypothesized Root Cause

증상별로 별도 원인이 있고, 본 설계는 셋 모두에 정면으로 대응한다.

1. **`_looks_structural` 의 키워드 과매칭 (Req 1.2 → 2.2)**: 현재 구현이 일반 한글 단어("프로젝트", "구조", "흐름도", "트리") 와 영어 단어("diagram", "architecture") 를 OR 로 매칭한다. 시각 의도 본문에는 이런 단어가 거의 항상 등장하므로 `_force_generate_from_text:6050` 의 `use_native` 조건이 사실상 상시 true 가 되어 Bedrock 경로를 한 번도 시도하지 않는다.

2. **회로 차단 단락의 무음성 (Req 1.3 → 2.3, 1.5 → 2.5)**: `_image_gen_is_circuit_broken()` 이 true 를 반환하면 `_tool_generate_image:884` 가 access-denied 사유 / 모델 id 를 응답에 포함하지 않고 단순 폴백 응답만 반환한다. 권한 문제 / 모델 선택 문제 / 의도된 폴백을 사용자가 구분할 수단이 없다.

3. **진단 표면 부재 (Req 1.4 → 2.4)**: `debug_cwd` (line 3786) 와 `debug_bridge` (line 3799) 사이에 image-gen 상태를 노출하는 라우트가 없다. 운영자가 환경변수, 회로 상태, 모델 체인을 확인하려면 서버 프로세스에 직접 접근해야 한다.

4. **호출 결과 추적 결여**: 단락 응답에 "어떤 모델이 거부됐는지" 를 채워넣으려면 _과거_ 호출 결과를 어딘가에 보관해야 한다. 현재는 호출 결과가 응답으로 반환되는 즉시 사라진다. 모듈 전역 ring buffer 가 필요하다.

## Correctness Properties

Property 1: Bug Condition - 시각적 의도 PPTX 가 실제 Bedrock 이미지 모델로 라우팅된다

_For any_ 입력 where the bug condition holds (시각적 의도 + 게이트웨이 정상 + 산출물 모든 이미지가 `"matplotlib (native)"`), the fixed system SHALL `IMAGE_MODELS` 체인의 Bedrock 이미지 모델 (`stability.*`, `amazon.titan-image-generator-v2:0`, `amazon.nova-canvas-v1:0`) 중 최소 한 개를 호출하여, 산출물 임베드 이미지 메타 중 최소 한 개의 `model` 필드가 위 Bedrock 모델 id 와 일치하도록 한다.

**Validates: Requirements 2.1, 2.2**

Property 2: Bug Condition - 회로 차단 단락 응답이 행동 가능한 정보를 포함한다

_For any_ 회로 차단 상태에서 들어오는 이미지 호출, the fixed system SHALL 단락 응답 JSON 에 (a) 최근 5 회 호출 기록(`recentAttempts`), (b) 한·영 `actionable` 메시지("Bedrock 게이트웨이가 image-gen 라우트를 거부했습니다 — 권한 필요 모델: [ids]" / "Bedrock gateway denied image-gen route — admin must grant invoke permission for: [ids]") 를 포함시킨다. `[ids]` 는 `recentAttempts` 중 `_image_gen_error_is_access_denied(reason) == true` 인 시도들의 unique model id 집합이다.

**Validates: Requirements 2.3, 2.5**

Property 3: Bug Condition - 일반 키워드만 있는 본문은 `_looks_structural` 에서 false

_For any_ 입력 본문이 (a) `/` 경로 토큰을 포함하지 않고, (b) 화살표 체인을 포함하지 않고, (c) markdown 테이블 행을 포함하지 않는 경우, the fixed `_looks_structural(description, title, body)` SHALL false 를 반환한다 — 본문에 "프로젝트", "구조", "흐름도", "diagram", "architecture" 등 일반 시각 키워드가 등장하더라도.

**Validates: Requirements 2.2**

Property 4: Bug Condition - 진단 엔드포인트가 완전한 JSON 을 반환한다

_For any_ `GET /api/debug/image-gen-status` 호출, the fixed system SHALL HTTP 200 과 함께 다음 5 개 키를 모두 포함하는 JSON 을 반환한다 — `circuit` (`{disabled_at, ttl, ttlRemainingSec, isBroken}`), `models` (현재 `IMAGE_MODELS` 체인 list), `selectPreview` (`_select_image_models("test architecture diagram", None)` 결과), `env` (`{AE_IMAGE_PARALLEL_N, AE_IMAGE_QUALITY_THRESHOLD, AE_FORCE_NATIVE_DIAGRAM, AE_DISABLE_HTML_SLIDES}` 의 fresh-read 값), `recentAttempts` (최근 10 회 `_IMAGE_GEN_ATTEMPTS` 스냅샷). 회로 차단 여부와 무관하게 200.

**Validates: Requirements 2.4**

Property 5: Preservation - 진짜 구조 신호가 있는 본문은 matplotlib 경로 유지

_For any_ 입력 본문이 (a) `/`-구분 경로 토큰, 또는 (b) `->`/`→`/`⇒` 화살표 체인, 또는 (c) markdown 테이블 행 중 최소 하나를 포함하는 경우, the fixed `_looks_structural` SHALL true 를 반환하여 matplotlib 네이티브 라우팅이 보존된다.

**Validates: Requirements 3.1**

Property 6: Preservation - 비시각 텍스트 요청은 이미지 모델 호출 0 회

_For any_ 시각적 의도가 없는 PPTX/PDF 생성 요청, the fixed system SHALL `gw.invoke_model` 을 이미지 모델 id 로 호출하지 않고 텍스트 전용 python-pptx/PDF 빌더만 실행한다.

**Validates: Requirements 3.4**

Property 7: Preservation - PPTX 좌우 분리 레이아웃 좌표 보존

_For any_ 텍스트와 이미지가 모두 있는 슬라이드 생성, the fixed system SHALL 본문 placeholder 를 `(left=0.6, top=1.6, width=6.0, height=5.4)` 에 배치하고 이미지를 `x=7.0` 에 배치한다 (변경 없음).

**Validates: Requirements 3.5**

Property 8: Preservation - 회로 트립 동작 및 TTL 자동 복구

_For any_ 입력에서 모든 Bedrock 이미지 모델이 한 라운드에서 access-denied 를 반환하면, the fixed system SHALL `_IMAGE_GEN_CIRCUIT.disabled_at = time.time()` 를 세팅한다. 이후 `time.time() - disabled_at > ttl` 이 되면 `_image_gen_is_circuit_broken()` 이 false 를 반환하고 `_tool_generate_image` 가 다시 체인을 시도한다.

**Validates: Requirements 3.3, 3.7**

## Fix Implementation

### Changes Required

모든 변경은 `ai_engine/server.py` 단일 파일 안에서 일어난다. 다른 파일은 수정되지 않는다.

**File**: `ai_engine/server.py`

**Specific Changes**:

1. **Attempts 링버퍼 추가** — `_IMAGE_GEN_CIRCUIT` 정의 직후 (~line 683)
   - `from collections import deque` 가 이미 임포트돼 있지 않으면 파일 상단에 추가.
   - 모듈 전역 `_IMAGE_GEN_ATTEMPTS: deque = deque(maxlen=10)` 선언.
   - 헬퍼 함수 `_record_image_attempt(model: str, status: str, reason: str, duration_ms: int) -> None` 추가. 인자: `model` (Bedrock model id), `status` ∈ `{"ok", "error", "exception"}`, `reason` (실패 메시지 또는 빈 문자열), `duration_ms` (호출 소요 ms). 동작: `_IMAGE_GEN_ATTEMPTS.append({"ts": time.time(), "model": model, "status": status, "reason": reason, "durationMs": duration_ms})`.

2. **`_tool_generate_image` 의 attempts 기록** — parallel `asyncio.gather` 결과 처리 직후 (~line 884~895 부근)
   - `asyncio.gather(...)` 가 resolve 된 직후 각 결과 항목(model id + 결과 또는 예외)에 대해 `_record_image_attempt(...)` 를 한 번씩 호출한다. 성공 → `status="ok"`, `reason=""`. 게이트웨이가 응답에서 access-denied 류 에러를 돌려준 경우 → `status="error"`, `reason=detail`. 예외로 떨어진 경우 → `status="exception"`, `reason=str(exc)`.
   - 기존 회로 트립 로직 (`_image_gen_trip_circuit`) 호출 시점은 변경하지 않는다.

3. **단락 응답 JSON enrichment** — `_tool_generate_image:884` 의 회로 차단 early-return 분기
   - 기존 단락 응답에 두 키를 추가:
     - `recentAttempts`: `list(_IMAGE_GEN_ATTEMPTS)[-5:]` (마지막 5 개).
     - `actionable`: 한·영 결합 문자열. 형식 = `"Bedrock 게이트웨이가 image-gen 라우트를 거부했습니다 — 권한 필요 모델: {ids} / Bedrock gateway denied image-gen route — admin must grant invoke permission for: {ids}"`.
       - `{ids}` 계산: `recentAttempts` 중 `_image_gen_error_is_access_denied(item["reason"]) == True` 인 항목들의 unique model id 를 콤마로 join.
       - `recentAttempts` 가 비어 있거나 access-denied 항목이 없으면 `{ids}` 자리에 빈 문자열을 넣지 말고 `actionable` 키 자체를 생략한다 (Req 2.5 의 "권한 필요 모델 [ids]" 가 의미를 가지려면 ids 가 필요).
   - 기존 응답 키들(`fallback`, `model` 등)은 그대로 유지한다.

4. **`_looks_structural` 재작성** — line 1130
   - 기존 키워드 OR 매칭 ("프로젝트" / "구조" / "흐름도" / "트리" / "diagram" / "architecture" 등) 을 _완전히 제거_.
   - 새 술어 정의:
     ```
     def _looks_structural(description: str, title: str, body: str) -> bool:
         text = "\n".join(filter(None, [description, title, body]))
         # signal 1: path token — at least one literal '/' between identifier-shaped tokens
         if re.search(r"[A-Za-z0-9_.\-]+/[A-Za-z0-9_.\-]+", text):
             return True
         # signal 2: arrow chain — anything → / -> / ⇒ anything
         if re.search(r"\S+\s*(->|→|⇒)\s*\S+", text):
             return True
         # signal 3: markdown table row — line starts with |, has at least 2 pipe-separated cells
         for line in text.splitlines():
             if re.match(r"^\s*\|.+\|\s*$", line) and line.count("|") >= 3:
                 return True
         return False
     ```
   - 기존 호출처(`_force_generate_from_text:6050`, `_classify_section_diagram:2136`)는 시그니처가 동일하므로 변경 없음.
   - 일반 단어("프로젝트", "구조", "흐름도", "diagram", "architecture") 는 위 세 신호 어디에도 매칭되지 않으므로 false.

5. **진단 엔드포인트 추가** — `debug_cwd` (line 3786) 직후, `debug_bridge` (line 3799) 직전 (~line 3798)
   - 라우트: `@app.get("/api/debug/image-gen-status")`.
   - 핸들러 이름: `debug_image_gen_status`.
   - 응답 빌드 시 환경변수는 매 호출마다 fresh 로 `os.getenv(...)` 로 읽는다 (모듈 캐시 사용 금지).
   - 응답 JSON 스키마:
     ```
     {
       "circuit": {
         "disabled_at": float,            # epoch seconds, 0 if never tripped
         "ttl": int,                      # 300
         "ttlRemainingSec": float,        # max(0, ttl - (now - disabled_at)) if disabled_at>0 else 0
         "isBroken": bool                 # _image_gen_is_circuit_broken() 결과
       },
       "models": [str, ...],              # IMAGE_MODELS 체인 그대로
       "selectPreview": [str, ...],       # _select_image_models("test architecture diagram", None)
       "env": {
         "AE_IMAGE_PARALLEL_N":        str | null,
         "AE_IMAGE_QUALITY_THRESHOLD": str | null,
         "AE_FORCE_NATIVE_DIAGRAM":    str | null,
         "AE_DISABLE_HTML_SLIDES":     str | null
       },
       "recentAttempts": [                # list(_IMAGE_GEN_ATTEMPTS) 최근 10
         {"ts": float, "model": str, "status": str, "reason": str, "durationMs": int}
       ]
     }
     ```
   - `selectPreview` 의 sample prompt 는 항상 리터럴 `"test architecture diagram"` 을 사용한다 (Req 2.4 c).
   - 회로 차단 여부와 무관하게 HTTP 200 을 반환한다 (진단 엔드포인트가 회로에 의해 막히면 안 된다).

### Out of Scope (No Changes)

- `ai_engine/gateway_module.py` — 변경 없음.
- `_select_image_models(prompt, hint)` 의 정렬 로직 — 변경 없음.
- `_tool_generate_image` 의 parallel best-of-N 핵심 경로 — attempts 기록과 단락 응답 JSON 두 곳만 추가, 호출 흐름 자체는 보존.
- PPTX 좌우 분리 레이아웃 좌표 (`(0.6, 1.6, w=6.0, h=5.4)`, `x=7.0`) — 변경 없음.
- 프런트엔드 — Electron 채팅 패널은 이미 응답 JSON 의 `actionable` 필드를 그대로 렌더링한다. 추가 변경 없음.

## Testing Strategy

### Validation Approach

두 단계 접근 — 먼저 unfixed 코드에서 버그 컨디션을 _재현_ 하는 exploratory 테스트로 근본 원인을 확정하고, 이어서 fix 적용 후 (a) 버그 컨디션이 만족되는 입력에 대해 P 가 성립하는지 (Fix Checking), (b) 버그 컨디션 외 입력에서 기존 동작이 보존되는지 (Preservation Checking) 를 검증한다.

### Exploratory Bug Condition Checking

**Goal**: Unfixed 코드에서 세 결함 모두를 재현하는 카운터예제를 수면 위로 끌어올린다. 근본 원인 가설(`_looks_structural` 과매칭, 단락 응답 무음, 진단 표면 부재) 을 확정 또는 반박한다. 반박 시 재가설 단계로 돌아간다.

**Test Plan**: `scripts/test_media_output_quality_bug_condition.py` (기존 bugfix property test 명명 패턴) 에 unittest/hypothesis 혼합 테스트를 작성. `_get_gw().invoke_model` 을 monkeypatch 로 가로채 Bedrock 이미지 모델 호출에 access-denied 를 반환하게 하여 회로를 트립시킨 뒤, `_force_generate_from_text` 를 호출해 PPTX 산출물 메타를 검사한다. Unfixed 코드에서는 모든 임베드 이미지의 `model` 필드가 `"matplotlib (native)"` 와 일치 → 테스트 PASS (버그 재현 확인). Fix 적용 후에는 최소 한 개의 이미지가 Bedrock 모델 id 를 가지므로 테스트 FAIL → 이때 이 exploratory 테스트는 _반전_ 되어 fix-checking property F1 으로 옮겨가야 한다.

**Test Cases**:

1. **Native-only manifestation**: 본문 = "프로젝트 아키텍처 다이어그램을 PPTX 로 만들어줘", 게이트웨이 mock = 모든 Bedrock 이미지 모델에 대해 _첫_ 호출에서 access-denied → 회로 트립. 후속 `_force_generate_from_text` 호출이 PPTX 를 생성. 산출물의 모든 이미지 메타 `model` 필드가 `"matplotlib (native)"` 와 정확히 같고 어떤 것도 Bedrock id (`stability.*`, `amazon.nova-canvas-v1:0`, `amazon.titan-image-generator-v2:0`) 와 같지 않음을 ASSERT. (will pass on unfixed code, will fail on fixed code)
2. **`_looks_structural` over-match**: 입력 = description="프로젝트 구조 보고서", title="흐름도", body="이번 분기 변경" — 셋 모두 `/` / `->` / `|` 미포함. unfixed 호출: `_looks_structural("프로젝트 구조 보고서", "흐름도", "이번 분기 변경") == True` 를 ASSERT. (will pass on unfixed code, will fail on fixed code)
3. **Silent short-circuit**: 회로를 강제 트립 (`_IMAGE_GEN_CIRCUIT["disabled_at"] = time.time()`) 후 `_tool_generate_image` 호출. 응답 dict 에 `"actionable"` 키가 _없음_ 또는 `"recentAttempts"` 키가 _없음_ 을 ASSERT. (will pass on unfixed code, will fail on fixed code)
4. **Diagnostic endpoint absent (edge)**: TestClient 로 `GET /api/debug/image-gen-status` 호출. unfixed 코드에서는 404 반환을 ASSERT. (will pass on unfixed code, will fail on fixed code)

**Expected Counterexamples**:

- 산출물 메타의 모든 `model` 필드가 `"matplotlib (native)"` — Bedrock 호출이 0 회였음을 의미.
- 가능 원인: (a) `_looks_structural` 가 `_force_generate_from_text:6050` 의 `use_native` 분기를 항상 true 로 만들어 Bedrock 경로 미진입, (b) 회로가 첫 access-denied 에 트립되어 후속 호출이 단락, (c) 단락 응답이 권한 정보를 포함하지 않아 사용자가 원인 파악 불가.

### Fix Checking

**Goal**: 버그 컨디션이 만족되는 입력에 대해 fix 후 함수가 expected behavior 를 만족하는지 검증.

**Pseudocode:**
```
FOR ALL input WHERE isBugCondition(input) DO
  result := fixedFunction(input)
  ASSERT expectedBehavior(result)
END FOR
```

각 fix-checking property 는 위 패턴의 구체화다. F1~F4 가 Properties 1~4 (Bug Condition 군) 에 일대일 매핑된다.

- **F1 (Property 1, Req 2.1)**: hypothesis 로 시각적 의도가 있는 PPTX/PDF 입력을 생성 + 게이트웨이 mock 이 최소 한 모델에 대해 valid PNG 반환 → `_force_generate_from_text` 산출물의 `meta.model` 중 최소 한 개가 Bedrock 이미지 모델 id 와 일치.
- **F2 (Property 3, Req 2.2)**: hypothesis 로 `/` 토큰·화살표 체인·markdown 테이블 행 셋 모두 포함하지 않는 텍스트 (단, 일반 시각 키워드 "프로젝트"/"구조"/"흐름도"/"diagram"/"architecture" 는 포함될 수 있음) 를 생성 → `_looks_structural(...)` 가 false 를 반환.
- **F3 (Property 5, Req 3.1) — 보존이지만 fix-checking 으로도 성립**: hypothesis 로 세 신호 중 최소 하나를 포함하는 텍스트를 생성 → `_looks_structural(...)` 가 true 를 반환. (regression sentinel: F2 와 짝.)
- **F4 (Property 4, Req 2.4)**: TestClient 로 `GET /api/debug/image-gen-status` 호출. 회로 차단 / 비차단 두 상태 모두에서 HTTP 200 + JSON 응답에 `circuit`, `models`, `selectPreview`, `env`, `recentAttempts` 5 개 키가 모두 존재. `circuit.disabled_at`, `circuit.ttl`, `circuit.isBroken` 의 타입이 각각 number, number, bool. `env` 의 4 개 변수 키가 모두 존재 (값은 null 가능).

### Preservation Checking

**Goal**: 버그 컨디션이 만족되지 _않는_ 모든 입력에 대해 fix 후 동작이 fix 전과 동일함을 검증.

**Pseudocode:**
```
FOR ALL input WHERE NOT isBugCondition(input) DO
  ASSERT originalFunction(input) = fixedFunction(input)
END FOR
```

**Testing Approach**: Property-based testing (hypothesis) 가 권장됨 — 입력 도메인이 넓고(임의 본문 텍스트, 임의 visual_intent 플래그, 임의 게이트웨이 상태), 수동 unit test 로는 회귀를 잡기 어렵기 때문이다. Unfixed 코드에서 먼저 동작을 관찰해 캡처한 뒤, 같은 입력 도메인에서 fixed 코드가 동일한 동작을 반환하는지 PBT 로 비교한다.

**Test Cases**:

- **P1 preservation (Req 3.3)**: hypothesis 로 mock 게이트웨이가 모든 Bedrock 이미지 모델 호출에 대해 access-denied 를 반환하도록 설정 → `_tool_generate_image` 1 회 호출. ASSERT `_IMAGE_GEN_CIRCUIT["disabled_at"] != 0` (회로 트립 발생). Unfixed/fixed 모두에서 성립해야 함.
- **P2 preservation (Req 3.4)**: hypothesis 로 시각적 의도 _없는_ PPTX/PDF 요청 본문을 생성 (예: "월간 매출 보고서 텍스트만"). `gw.invoke_model` 을 spy 로 감싼다. ASSERT `gw.invoke_model` 이 이미지 모델 id (`stability.*`, `amazon.nova-canvas-v1:0`, `amazon.titan-image-generator-v2:0`) 로 호출된 횟수 == 0.
- **P3 preservation (Req 3.5)**: hypothesis 로 본문 + 이미지가 모두 있는 슬라이드를 생성. 산출 PPTX 의 본문 placeholder 좌표 = `(left=Inches(0.6), top=Inches(1.6), width=Inches(6.0), height=Inches(5.4))`, 이미지 좌표 `x == Inches(7.0)` 을 ASSERT.
- **P4 preservation (Req 3.7)**: 회로를 강제 트립한 뒤 `_IMAGE_GEN_CIRCUIT["disabled_at"] = time.time() - 301` 로 시간을 되돌려 TTL 만료 시뮬레이션. `_image_gen_is_circuit_broken()` 가 false 를 반환함을 ASSERT, 이어서 `_tool_generate_image` 호출 시 `gw.invoke_model` 이 다시 이미지 모델 id 로 호출됨을 ASSERT.

### Unit Tests

- `_looks_structural` 의 세 신호 각각에 대한 양성/음성 케이스 (경로 단독, 화살표 단독, 테이블 단독; 그리고 일반 키워드만 있는 음성 케이스).
- `_record_image_attempt` 의 deque maxlen=10 동작 (11번째 append 시 가장 오래된 것이 evict).
- 진단 엔드포인트의 환경변수 fresh-read — 환경변수를 변경한 직후 두 번째 호출에서 새 값이 반영되는지.
- 단락 응답의 `actionable` 메시지가 access-denied 가 아닌 reason 만 있는 attempts 에 대해서는 _포함되지 않음_ (ids 가 비어있는 actionable 은 노이즈).

### Property-Based Tests

- F1: 시각적 의도 PPTX 입력을 hypothesis 로 생성, 게이트웨이가 정상이면 메타 중 최소 1 개가 Bedrock id.
- F2 / F3: 임의 텍스트 생성 — F2 는 세 신호 모두 없는 텍스트 도메인, F3 는 최소 하나 포함하는 도메인.
- P2: 시각적 의도 없는 PPTX 요청을 hypothesis 로 생성, `gw.invoke_model` 이미지 호출 0 회.

### Integration Tests

- `_force_generate_from_text` end-to-end: 시각적 의도 본문 + 정상 mock 게이트웨이 → 산출 PPTX 검사 → 임베드 이미지 메타에 Bedrock id 1 개 이상.
- 회로 차단 → `_tool_generate_image` 호출 → 응답 JSON 에 `actionable` 한·영 메시지 포함 → 채팅 패널 렌더링 fixture 가 메시지를 그대로 표시하는지 확인.
- TTL 자동 복구: 회로 트립 → 시간 advance → 재호출이 Bedrock 모델로 재시도.

## File / Module Impact

| File | Lines | Change |
|---|---|---|
| `ai_engine/server.py` | ~683 (after `_IMAGE_GEN_CIRCUIT`) | Add `_IMAGE_GEN_ATTEMPTS = deque(maxlen=10)` and `_record_image_attempt` helper. Import `deque` from `collections` if not already imported. |
| `ai_engine/server.py` | 1130 (`_looks_structural`) | Rewrite predicate to signal-based matching (path / arrow / markdown table). Remove keyword OR matching. |
| `ai_engine/server.py` | ~884–895 (`_tool_generate_image` short-circuit early return) | Enrich response JSON with `recentAttempts` (last 5) and conditional `actionable` (한·영). |
| `ai_engine/server.py` | (parallel `asyncio.gather` resolution site, near 884) | Call `_record_image_attempt(...)` for each model result/exception. |
| `ai_engine/server.py` | ~3798 (between `debug_cwd` and `debug_bridge`) | Add `@app.get("/api/debug/image-gen-status")` route `debug_image_gen_status` returning the 5-key JSON. |

**No changes to:**

- `ai_engine/gateway_module.py` — Bedrock Gateway 클라이언트는 그대로.
- `_select_image_models(prompt, hint)` (line 596) — 정렬 로직 보존.
- `_tool_generate_image` 의 parallel best-of-N 핵심 흐름 — attempts 기록과 단락 응답 enrichment 외 변경 없음.
- PPTX 좌우 분리 레이아웃 좌표 — 변경 없음 (Req 3.5 보존).
- Electron 프런트엔드 — 채팅 패널은 응답 JSON 의 `actionable` 필드를 이미 그대로 렌더한다.
- 다른 진단 라우트(`debug_cwd`, `debug_bridge`) — 변경 없음.
