# Bugfix Requirements Document

## Introduction

PPTX/PDF 산출물이 매우 낮은 품질로 생성된다. 사용자가 첨부한 최근 산출물 메타데이터를 보면
임베드된 이미지의 `model` 필드가 모두 `"matplotlib (native)"` 이거나, 파일 자체가
`system_fallback` / `python-pptx via Sonnet 4` 셸로만 채워져 있다. Stability(SD 3.5 / SD Ultra /
Stable Image Core) · Titan Image v2 · Nova Canvas 등 Bedrock 게이트웨이에 등록된 이미지 모델로
생성된 산출물은 0건이다. 사용자는 Genspark / Gamma 수준의 PPTX/PDF 를 기대하지만, 결과물은
박스/화살표 형태의 단색 다이어그램만 박혀 나오는 상태다.

세 가지 결함이 결합되어 있다.

1. **무음 회로 차단**: image-gen circuit breaker(`_IMAGE_GEN_CIRCUIT`)가 access-denied 한 번에
   5분간 모든 이미지 호출을 단락시키지만, 그 사실이 사용자/운영자에게 노출되지 않는다. 결과적으로
   "왜 matplotlib 만 나오는가?"를 진단할 수단이 없다.
2. **구조 휴리스틱 과매칭**: `_looks_structural` 휴리스틱이 "프로젝트" · "구조" · "흐름도" 같은
   포괄적 한글 키워드만으로도 true 를 반환해, 게이트웨이가 정상이어도 실제 Bedrock 이미지 모델
   호출을 건너뛰고 matplotlib 으로 라우팅한다.
3. **진단 표면 부재**: 운영자가 "회로가 끊겼나?", "어떤 모델이 거부됐나?", "환경변수가 어떻게
   세팅됐나?" 를 확인할 HTTP 진단 엔드포인트가 없다.

본 버그픽스는 이 세 결함을 함께 해결해 시각적 의도가 있는 PPTX/PDF 요청이 실제 Bedrock 이미지
모델로 라우팅되도록 하고, 라우팅이 실패하더라도 그 원인이 사용자에게 보이도록 만든다.

## Bug Analysis

### Current Behavior (Defect)

게이트웨이가 정상인 환경에서도 시각적 의도가 있는 PPTX/PDF 요청이 matplotlib 경로로만 흘러가며,
실패 원인이 사용자에게 노출되지 않는다.

1.1 WHEN 사용자가 시각적 의도가 있는 PPTX/PDF 생성을 요청하고("아키텍처 흐름도", "프로젝트
   다이어그램" 등) Bedrock 게이트웨이가 정상 응답 가능한 상태일 때 THEN the system 산출물에
   임베드된 모든 이미지 메타데이터의 `model` 필드가 `"matplotlib (native)"` 로만 표기되며
   Stability/Titan/Nova-Canvas 모델 id 가 들어간 산출물은 0건이다

1.2 WHEN 본문에 디렉토리 경로(`/`, `\`)·화살표 체인(`->`, `→`)·markdown 테이블 행이 전혀
   없는데도 본문/제목/설명에 "프로젝트" · "구조" · "흐름도" 같은 일반 한글 단어만 등장할 때 THEN
   the system 회로 차단 여부와 무관하게 즉시 matplotlib 네이티브 다이어그램 경로로 라우팅하고
   Bedrock 이미지 모델 호출을 시도하지 않는다

1.3 WHEN image-gen circuit breaker 가 access-denied 로 차단된 상태에서 후속 이미지 요청이
   들어올 때 THEN the system 호출을 무음으로 단락시켜 채팅 응답·산출물 메타·서버 로그 어디에도
   "어떤 모델이", "어떤 사유로" 거부됐는지에 대한 정보를 노출하지 않고 단지 matplotlib 또는
   `system_fallback` 결과만 반환한다

1.4 WHEN 운영자/사용자가 "Bedrock 이미지 모델이 실제로 호출되는가, 회로가 끊겼는가, 어느 모델이
   몇 회 어떻게 실패했는가" 를 확인하려 할 때 THEN the system 회로 상태·`IMAGE_MODELS` 체인
   구성·`_select_image_models` 미리보기·관련 환경변수(`AE_IMAGE_PARALLEL_N`,
   `AE_IMAGE_QUALITY_THRESHOLD`, `AE_FORCE_NATIVE_DIAGRAM`, `AE_DISABLE_HTML_SLIDES`)·최근
   이미지 모델 호출 결과를 노출하는 진단 표면을 전혀 제공하지 않는다

1.5 WHEN 게이트웨이가 모든 Bedrock 이미지 모델에 대해 access-denied 를 반환해 회로가 끊긴
   상태일 때 THEN the system 채팅 패널 응답이 단순히 "이미지 폴백 사용" 정도로만 표시되어
   사용자가 "권한 문제인지 / 모델 선택 문제인지 / 폴백이 의도된 동작인지" 를 구분할 수 없다

### Expected Behavior (Correct)

게이트웨이가 정상이면 실제 Bedrock 이미지 모델로 라우팅되어야 하고, 게이트웨이가 거부하면 그
사실이 사용자/운영자에게 명시적으로 보여야 한다.

2.1 WHEN 사용자가 시각적 의도가 있는 PPTX/PDF 생성을 요청하고 Bedrock 게이트웨이가 정상 응답
   가능한 상태일 때 THEN the system SHALL `IMAGE_MODELS` 체인의 Bedrock 이미지 모델
   (`stability.*`, `amazon.titan-image-generator-v2:0`, `amazon.nova-canvas-v1:0`) 중 최소
   한 개를 호출하고, 산출물에 임베드된 이미지 중 최소 한 개의 메타데이터 `model` 필드가 위 Bedrock
   모델 id 중 하나와 일치하도록 한다 (`"matplotlib (native)"` 단독·`"system_fallback"` 단독은
   허용되지 않는다)

2.2 WHEN 본문/제목/설명에 디렉토리 경로(`/`, `\`)·화살표 체인(`->`, `→`)·markdown 테이블
   행이 하나도 등장하지 않는데 "프로젝트" · "구조" · "흐름도" 같은 일반 한글 단어만 등장할 때 THEN
   the system SHALL 구조 휴리스틱을 false 로 평가해 matplotlib 경로를 건너뛰고 Bedrock 이미지
   모델 호출 경로로 진행한다

2.3 WHEN image-gen circuit breaker 가 차단된 상태에서 이미지 호출이 들어올 때 THEN the
   system SHALL 단락 응답 JSON 에 가장 최근의 호출 시도 기록(시도된 model id 와 거부 사유)을
   함께 포함시켜 채팅 패널이 "게이트웨이가 [model ids] 에 대해 access-denied — 관리자에게 권한
   요청 필요" 와 같은 행동 가능한 메시지를 사용자에게 표시할 수 있도록 한다

2.4 WHEN 운영자가 `GET /api/debug/image-gen-status` 를 호출할 때 THEN the system SHALL
   다음을 모두 포함하는 JSON 응답을 반환한다 — (a) circuit breaker 상태(`disabled_at`,
   `ttl`, `ttlRemainingSec`, `isBroken`), (b) 현재 구성된 `IMAGE_MODELS` 체인,
   (c) 대표 프롬프트(예: `"test architecture diagram"`)에 대한 `_select_image_models`
   미리보기, (d) `AE_IMAGE_PARALLEL_N` · `AE_IMAGE_QUALITY_THRESHOLD` ·
   `AE_FORCE_NATIVE_DIAGRAM` · `AE_DISABLE_HTML_SLIDES` 의 현재 값,
   (e) 최근 10 회 이미지 모델 호출 결과(model id · status · reason)

2.5 WHEN 모든 Bedrock 이미지 모델이 access-denied 를 반환해 회로가 끊긴 결과 산출물이
   matplotlib 으로 폴백될 때 THEN the system SHALL 채팅 패널 / 산출물 메타 / 서버 로그에
   "Bedrock 게이트웨이가 image-gen 라우트를 거부했습니다 — 권한 필요 모델: [model ids]" 형식의
   사용자 가시(韓·英) 메시지를 노출해, 사용자가 "권한 문제" 임을 즉시 인식하도록 한다

### Unchanged Behavior (Regression Prevention)

진짜 구조 다이어그램 경로·강제 폴백·텍스트 전용 경로·기존 회로 차단 동작·PPTX 좌우 분리 레이아웃은
모두 그대로 유지되어야 한다.

3.1 WHEN 본문이 디렉토리 경로(예: `src/components/foo.js`)·화살표 체인(예: `A -> B -> C`,
   `A → B → C`)·markdown 테이블 행 중 하나라도 포함할 때 THEN the system SHALL CONTINUE TO
   matplotlib 네이티브 트리/플로우 렌더러 경로로 라우팅해 결정론적 다이어그램을 생성한다

3.2 WHEN `AE_FORCE_NATIVE_DIAGRAM=1` 환경변수가 설정되어 있을 때 THEN the system SHALL
   CONTINUE TO 게이트웨이 상태와 휴리스틱 결과에 무관하게 matplotlib 경로를 사용한다

3.3 WHEN `IMAGE_MODELS` 체인의 모든 Bedrock 이미지 모델이 한 번의 호출 라운드에서 access-denied
   를 반환할 때 THEN the system SHALL CONTINUE TO `_IMAGE_GEN_CIRCUIT.disabled_at` 을 설정해
   회로를 차단하고 5분(`ttl=300`) TTL 동안 후속 호출을 단락시킨다

3.4 WHEN 사용자가 시각적 의도가 없는 PPTX/PDF 생성(예: 단순 보고서 텍스트, 코드 변경 로그)을
   요청할 때 THEN the system SHALL CONTINUE TO 이미지 모델을 호출하지 않고 텍스트 기반
   python-pptx/PDF 빌더로 산출물을 생성한다

3.5 WHEN PPTX 가 텍스트와 이미지가 함께 있는 슬라이드를 생성할 때 THEN the system SHALL
   CONTINUE TO 좌측 본문 플레이스홀더(`w=6.0`, `h=5.4`)와 우측 이미지 영역(`x=7.0`)을
   분리하여 겹침 없이 배치한다

3.6 WHEN `_tool_generate_image` 가 Bedrock 모델로 성공적으로 이미지를 생성할 때 THEN the
   system SHALL CONTINUE TO 메타데이터 `model` 필드에 실제 호출된 Bedrock 모델 id
   (예: `"stability.sd3-5-large-v1:0"`)를 기록한다

3.7 WHEN 회로 차단 TTL(5분) 이 만료될 때 THEN the system SHALL CONTINUE TO `disabled_at` 을
   0으로 리셋하고 다음 호출에서 다시 Bedrock 이미지 모델을 시도한다
