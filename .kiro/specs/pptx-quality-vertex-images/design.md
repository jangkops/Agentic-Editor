# PPTX 고품질 + Vertex 이미지 활용 Bugfix Design

## Overview

`generate_pptx` 경로는 두 개의 결함이 맞물려 "저품질 + 생성된 Vertex 이미지 미활용" 산출물을
만든다. 본 설계는 사용자가 확정한 **Genspark 스타일 하이브리드 아키텍처**를 중심으로 결함을
해소한다.

- **품질 엔진(주):** `ai_engine/slide_templates.py`의 `render_*` 레이아웃을 1920×1080
  풀블리드 PNG로 렌더해 PPTX 슬라이드 배경으로 임베드하는 HTML-first 경로. 참고 매뉴얼
  수준의 고밀도(STEP/카드, 2단, KPI, 타임라인, 상태표, 프로세스 흐름)를 담당한다.
- **이미지 보강(하이브리드):** Vertex "Nano Banana Pro"(`gemini-3-pro-image-preview`,
  `ai_engine/vertex_image_module.py`)로 표지/히어로/사진·일러스트 영역의 고품질 이미지를
  생성해 HTML 레이아웃 안 또는 슬라이드 비주얼로 **합성**한다.
- 두 엔진은 **경쟁이 아니라 협업**해야 한다. 현재는 (1) Vertex 생성 게이트가
  `not _html_enabled` 조건으로 HTML 경로와 **상호배타**이고, (2) 임베드 가드가
  `nativeDiagram`이 있으면 생성된 Vertex 이미지를 **폐기**해서, 두 엔진이 절대 함께 동작하지
  못한다.

근본 전략: **슬라이드 역할(role) 기반 결정 규칙**을 도입해 슬라이드마다 "주 렌더러(HTML/네이티브/
이미지) + Vertex 이미지의 사용처(배경/히어로/비주얼)"를 정하고, **생성된 Vertex 이미지는 절대
폐기하지 않는다**는 불변식을 코드에 명시한다. 동시에 진짜 구조형 다이어그램의 편집 가능 도형,
HTML 풀블리드 경로, Vertex 비활성/실패 시 네이티브 폴백, 게이트웨이 제약을 모두 보존한다.

## Glossary

- **Bug_Condition (C)**: 어떤 슬라이드에 대해 Vertex 고품질 이미지가 생성되었음에도 그 이미지가
  최종 PPTX에 임베드되지 않고 폐기/누락되는 조건. 확장적으로는 HTML 품질 경로와 Vertex 이미지가
  상호배타로 동작해 둘 중 하나가 항상 손실되는 상태.
- **Property (P)**: 버그 입력에 대한 기대 동작 — 생성된 Vertex 이미지가 슬라이드 역할에 맞는
  위치(슬라이드 비주얼, 풀블리드/히어로 배경, HTML 레이아웃 내 이미지 슬롯)에 임베드되어
  "생성됐으나 미사용" 이미지가 남지 않고, 참고 자료 수준의 고밀도 슬라이드가 산출되는 것.
- **Preservation**: 버그 조건이 아닌 입력(구조형 다이어그램, Vertex 미생성/비활성/실패,
  무관 입력)에 대한 기존 동작 — 편집 가능 네이티브 도형, HTML 풀블리드, 네이티브 폴백,
  템플릿 스타일 상속, 게이트웨이 경유 제약 — 이 변경되지 않는 것.
- **`_tool_generate_pptx`**: `ai_engine/server.py`의 PPTX 생성 도구 본체. HTML 활성화 판정,
  네이티브 다이어그램 구조화, Vertex 일괄 사전생성(`_vertex_pre`), 슬라이드별 임베드 루프를
  포함한다.
- **`_vertex_pre`**: 루프 진입 전 병렬로 생성한 `{slideIndex: relativePngPath}` 맵. 슬라이드별
  임베드 루프에서 조회된다.
- **`_html_enabled`**: HTML 풀블리드 슬라이드 렌더 활성 플래그(브리지/로컬 Chrome 가용 + 옵트인).
- **embed-guard**: `_tool_generate_pptx` 슬라이드 루프에서 Vertex 이미지를 임베드할지 결정하는
  조건문 `if (not native_diag and not img_file and not slide_bg): img_file = _vertex_pre.get(i)`.
- **`_classify_section_diagram` / `_looks_structural`**: 섹션이 진짜 구조형 다이어그램
  (흐름/트리/아키텍처/KPI 등)인지 분류하는 함수. 비어 있는 kind면 비구조(사진/일러스트)로 본다.
- **role (슬라이드 역할)**: 본 설계가 도입하는 분류 — `cover | section | structural | content
  | visual`. 주 렌더러와 Vertex 사용처를 결정하는 핵심 입력.
- **`_native_over_bg`**: 이미 존재하는 합성 프리미티브 — 풀블리드 배경(슬라이드 배경/Vertex
  이미지) 위에 편집 가능 네이티브 도형을 올려 그리는 경로.

## Bug Details

### Bug Condition

버그는 한 슬라이드에 대해 Vertex 이미지가 생성되었는데도(또는 생성될 자격이 있었는데도) 그
이미지가 최종 PPTX에 반영되지 않을 때 발생한다. 메커니즘은 두 층위로 나뉜다.

1. **게이트 상호배타(생성 단계):** Vertex 사전생성 블록이
   `if (not _html_enabled and AE_PREFER_VERTEX_IMAGE != 0 and ...)`로 묶여 있어, 기본 고품질
   경로인 HTML이 켜지면 Vertex 이미지가 **아예 생성되지 않는다**. 즉 "HTML 품질"과 "Vertex
   이미지"가 구조적으로 공존 불가.
2. **임베드 가드 폐기(임베드 단계):** HTML이 꺼진 경로에서 Vertex가 생성되더라도, 임베드 루프는
   `not native_diag and not img_file and not slide_bg`일 때만 `_vertex_pre[i]`를 임베드한다.
   그러나 직전 단계의 LLM 구조화와 결정론적 카드 폴백이 본문 슬라이드 대부분에 `nativeDiagram`을
   부여하므로, 생성된 Vertex 이미지가 가드(`not native_diag`)에 걸려 **폐기**된다.

**Formal Specification:**
```
FUNCTION isBugCondition(input)
  INPUT: input = SlideMediaState {
           hasVertexImage : bool,   # Vertex 이미지가 생성되었거나 생성 자격이 있음
           hasNativeDiagram : bool, # LLM 구조화/카드 폴백이 nativeDiagram을 부여함
           hasImageFile : bool,     # 사전 렌더 PNG가 이미 지정됨
           hasSlideBg : bool,       # HTML 풀블리드 배경이 이미 지정됨
           role : enum,             # cover | section | structural | content | visual
           htmlEnabled : bool,
           vertexEnabled : bool
         }
  OUTPUT: boolean   # True = 이 입력에서 생성된 Vertex 이미지가 폐기/미생성됨

  # (A) 게이트 상호배타: HTML이 켜져 이미지가 어울리는 슬라이드인데 Vertex가 생성 자체가 안 됨
  gateSuppressed := input.htmlEnabled AND input.vertexEnabled
                    AND input.role IN { cover, content, visual }
                    AND NOT input.hasVertexImage

  # (B) 임베드 폐기: Vertex 이미지가 있는데 nativeDiagram 때문에 가드에서 폐기됨
  embedDiscarded := input.hasVertexImage
                    AND input.hasNativeDiagram
                    AND NOT input.hasImageFile
                    AND NOT input.hasSlideBg

  RETURN gateSuppressed OR embedDiscarded
END FUNCTION
```

### Examples

- **임베드 폐기(B):** 본문 슬라이드 "프로젝트 폴더 구조 개요"에 카드 폴백이
  `nativeDiagram={type: cards}`를 부여 → 같은 슬라이드의 `_vertex_pre[i]` 이미지가
  `not native_diag` 가드에 걸려 폐기. 기대: 카드 구조는 유지하되 Vertex 이미지를 풀블리드
  배경으로 깔아 고밀도 합성(또는 카드 대신 HTML 고밀도 레이아웃 + 히어로 이미지).
- **게이트 상호배타(A):** HTML 활성 환경에서 표지/주요 섹션에 `imagePrompt`가 있어도 Vertex
  사전생성 블록이 `not _html_enabled`로 스킵 → 히어로 이미지가 한 장도 생성되지 않음. 기대:
  HTML 표지 레이아웃 + Vertex 히어로 이미지 합성.
- **저밀도(B의 누적 결과):** 본문 거의 전부가 단조로운 텍스트/박스(`cards`/`twocol`) 위주로
  남아 참고 매뉴얼의 STEP 카드·2단·스크린샷·배지 밀도에 미달. 기대: HTML 고밀도 레이아웃이
  주 렌더러가 되어 밀도 확보.
- **엣지(폐기되면 안 됨):** Vertex 이미지가 생성됐고 슬라이드가 진짜 구조형(흐름도)이면 →
  네이티브 도형은 유지하되 Vertex 이미지를 폐기하지 말고 backdrop으로라도 활용(또는 비구조
  판정 시에만 생성하도록 역할 분기). 어떤 경우에도 "생성됐으나 미사용"이 0이어야 한다.

## Expected Behavior

### Preservation Requirements

**Unchanged Behaviors (회귀 방지):**
- 진짜 구조형 다이어그램(흐름/트리/아키텍처)은 계속 **편집 가능한 네이티브 도형**으로 렌더한다
  (Req 3.1).
- `_html_enabled` 풀블리드 경로가 활성이면 계속 해당 경로로 슬라이드를 렌더한다 (Req 3.2).
- Vertex 이미지 생성이 비활성이거나 실패(쿼터/서킷브레이커)하면 계속 네이티브 다이어그램/카드로
  폴백한다 (Req 3.3, media-output-quality 회귀 방지).
- LLM/operation JSON 생성은 계속 **Bedrock Gateway 경유로만** 호출하고, Vertex는 이미지 생성에
  한해서만 사용한다 (Req 3.4, gateway.md 예외 조항 준수).
- 템플릿(`styleProfile`/`templatePath`)이 주어지면 계속 템플릿 스타일을 상속·적용한다 (Req 3.5).

**Scope:**
버그 조건(C)이 거짓인 모든 입력은 이 수정의 영향을 받지 않아야 한다. 여기에는 다음이 포함된다.
- Vertex 이미지가 생성되지 않은 슬라이드(텍스트 전용/순수 네이티브 다이어그램)
- Vertex 비활성/실패 경로(네이티브 폴백)
- 명시적 `imageFile`/`slideBackground`가 이미 지정된 슬라이드(기존 우선순위 유지)
- 템플릿·게이트웨이·표지 네이티브 디자인 등 본 결함과 무관한 모든 경로

> 실제 "올바른 동작"의 정의는 아래 **Correctness Properties**의 Property 1에 기술한다. 본
> 섹션은 변경되어서는 안 되는 것에 집중한다.

## Hypothesized Root Cause

버그 설명과 코드 조사를 종합한 가장 유력한 원인은 다음과 같다.

1. **게이트 상호배타 설계 결함**: Vertex 사전생성 블록의 조건
   `not _html_enabled and AE_PREFER_VERTEX_IMAGE != 0 ...`가 HTML 경로와 Vertex 이미지를
   배타적으로 만든다. "고품질 HTML + 고품질 이미지"라는 의도와 정반대로, 둘 중 하나만 동작한다.
   → 하이브리드 합성을 위해 이 게이트를 **역할 기반 공존 게이트**로 재설계해야 한다.

2. **임베드 가드의 과잉 폐기**: 슬라이드 루프의 `if (not native_diag and not img_file and not
   slide_bg)` 가드는 `nativeDiagram`이 있으면 무조건 Vertex 이미지를 버린다. LLM 구조화/카드
   폴백이 본문 거의 전부에 `nativeDiagram`을 부여하므로 거의 모든 Vertex 이미지가 폐기된다.
   → 이미지와 구조 표현이 **둘 다 손실되지 않도록** 합성 결정 규칙으로 교체해야 한다.

3. **역할 구분 부재**: 현재 코드는 슬라이드를 "구조형이면 네이티브, 아니면 이미지"의 단순
   이분법으로만 처리하고, 표지/히어로/고밀도 콘텐츠/사진형의 역할 구분이 없다. 그래서 고밀도
   HTML 레이아웃에 이미지를 합성하거나, 표지에 히어로 이미지를 얹는 경로가 없다.

4. **저밀도 폴백 우세**: 카드 폴백(`cards`/`twocol`)이 본문 기본값이 되어, HTML 고밀도
   레이아웃(`render_feature_grid`/`render_timeline`/`render_objective_detail`/
   `render_process_flow` 등)이 본문에 거의 적용되지 않는다. → HTML 고밀도 경로를 본문의
   **주 렌더러**로 승격해야 한다.

## Correctness Properties

Property 1: Bug Condition — 생성된 Vertex 이미지의 활용 보장

_For any_ 슬라이드 입력에서 버그 조건이 참이면(`isBugCondition`이 true), 수정된 결정 함수는
생성된 Vertex 이미지를 폐기하지 않고 슬라이드 역할에 맞는 위치(슬라이드 비주얼, 풀블리드/히어로
배경, 또는 HTML 레이아웃 내 이미지 슬롯)에 임베드한다. 즉 어떤 슬라이드에서도 "생성됐으나
미사용(Vertex 이미지가 존재하지만 최종 덱에 없음)" 상태가 남지 않는다.

**Validates: Requirements 2.1, 2.2, 2.3**

Property 2: Preservation — 구조형/무관 입력의 기존 동작 보존

_For any_ 슬라이드 입력에서 버그 조건이 거짓이면(`isBugCondition`이 false), 수정된 코드는 원본
코드와 동일한 결과를 산출한다. 특히 진짜 구조형 다이어그램은 편집 가능 네이티브 도형으로
렌더되고(3.1), HTML 풀블리드 경로는 그대로 유지되며(3.2), Vertex 비활성/실패 시 네이티브로
폴백하고(3.3), 템플릿 스타일 상속이 유지된다(3.5).

**Validates: Requirements 3.1, 3.2, 3.3, 3.5**

Property 3: 결정 규칙의 전역성·결정성 — 손실 없는 분기

_For any_ 가능한 슬라이드 미디어 상태(이미지/네이티브/배경/역할/게이트 플래그의 모든 조합)에
대해, `selectRenderPlan`은 정확히 하나의 주 렌더러를 정하고 Vertex 이미지의 사용처를 명시하며,
"이미지와 구조 표현이 동시에 손실되는" 출력은 절대 만들지 않는다(전역 정의 + 손실 0 불변식).

**Validates: Requirements 2.2, 2.4**

Property 4: HTML–Vertex 공존 — 하이브리드 게이트

_For any_ 입력에서 HTML이 활성이고(`htmlEnabled`) Vertex가 활성이며(`vertexEnabled`) 슬라이드
역할이 cover/content/visual이면, 결정 규칙은 HTML 레이아웃을 주 렌더러로 유지하면서도 Vertex
이미지 생성을 **억제하지 않는다**(둘이 상호배타가 아니다). 구조형 역할에서는 네이티브 도형이
우선하되 생성된 이미지가 backdrop으로 보존된다.

**Validates: Requirements 2.1, 2.4, 3.1, 3.2**

Property 5: 게이트웨이 제약 보존 — 이미지 외 호출은 Vertex 미사용

_For any_ 입력에서, LLM/operation JSON 생성 호출은 Bedrock Gateway 경유로만 발생하고 Vertex는
이미지 생성 경로에서만 호출된다(이미지 외 작업에서 Vertex 호출 0).

**Validates: Requirements 3.4**

## Fix Implementation

가정: 위 근본 원인 분석이 옳다(Testing Strategy의 탐색 단계에서 먼저 확인/반증한다).

### Changes Required

**File**: `ai_engine/server.py` (`_tool_generate_pptx`), 보조로 `ai_engine/slide_templates.py`.

#### 1. 슬라이드 역할 분류기 도입 — `_classify_slide_role`

`_classify_section_diagram` / `_looks_structural`을 재사용해 슬라이드 역할을 정한다(LLM 추가
호출 없음, 게이트웨이 제약 무관).

```
FUNCTION classifyRole(slide, isCover)
  IF isCover THEN RETURN cover
  kind := _classify_section_diagram(title, body, docTitle)   # 흐름/트리/아키텍처/kpi...
  IF kind IN { flow, tree, architecture } THEN RETURN structural
  IF hasVisualIntent(slide.imagePrompt) AND NOT kind THEN RETURN visual
  RETURN content   # 고밀도 HTML 레이아웃 대상(STEP/카드/2단/타임라인/상태표/프로세스)
END FUNCTION
```

- `structural`은 기존 `_classify_section_diagram`의 진짜 구조형만 포함(Req 3.1 보존). `kpi`/
  `cards`/`twocol`처럼 "구조라기보다 고밀도 콘텐츠"는 `content`로 흡수해 HTML 고밀도 경로로 보낸다.

#### 2. Vertex 사전생성 게이트 재설계 — HTML과 공존

현재:
```
if (not _html_enabled and AE_PREFER_VERTEX_IMAGE != 0 and AE_DISABLE_NATIVE_DIAGRAM != 1):
```
변경(공존 게이트 + 역할 인지):
```
if (AE_PREFER_VERTEX_IMAGE != 0 and vertexClient.enabled):
    # _html_enabled 여부와 무관하게 실행. 단, 역할이 structural인 슬라이드는 스킵
    # (네이티브 도형이 우선). cover/content/visual 슬라이드만 Vertex 이미지 생성 대상.
```

- `_gen_vertex_slide` 내부의 스킵 조건을 `_classify_slide_role(...) == structural`로 통일.
  (기존의 `_classify_section_diagram` kind 스킵을 역할 기준으로 일반화.)
- 표지/히어로 슬라이드는 사진/일러스트형 프롬프트로, content 슬라이드는 HTML 레이아웃의 이미지
  슬롯에 들어갈 보조 비주얼로 프롬프트를 구성한다.

#### 3. 임베드 가드 → 손실 없는 결정 규칙으로 교체

현재의 폐기형 가드:
```
if (not native_diag and not img_file and not slide_bg):
    img_file = _vertex_pre.get(i)   # nativeDiagram 있으면 폐기됨
```
변경(역할 기반 합성):
```
plan := selectRenderPlan(slide_state)   # 아래 규칙
pre := _vertex_pre.get(i)

IF plan.primary == HTML and slide_bg exists:
    # HTML 풀블리드가 주 렌더러. pre가 있으면 히어로/이미지 슬롯으로 HTML에 합성
    # (render data["heroImage"]=pre) 했거나, 합성 불가 시 on-slide 레이어로 보존.
ELIF plan.primary == NATIVE_SHAPES:
    # 구조형: 네이티브 도형 유지. pre가 있으면 _native_over_bg로 backdrop 보존(폐기 금지)
    IF pre: _eff_bg := pre
ELIF plan.primary == VERTEX_IMAGE:
    img_file := pre        # 사진/일러스트형: 이미지가 곧 슬라이드 비주얼
ELSE:  # content + HTML 비활성 → 네이티브 카드 + pre를 풀블리드 backdrop
    IF pre: _eff_bg := pre
```

핵심 불변식: **`pre`(생성된 Vertex 이미지)가 존재하면 어떤 분기에서도 폐기되지 않는다.**
최소한 `_native_over_bg`/`_eff_bg` backdrop 경로(이미 코드에 존재)로 보존한다.

#### 4. HTML 고밀도 경로를 본문 주 렌더러로 승격 (Req 2.4)

- `_html_enabled`이면 content 슬라이드의 **기본 경로**를 HTML 고밀도 레이아웃으로 둔다(현재처럼
  `_generate_html_slide_for_section` → `slideBackground` 설정). 카드 폴백(`cards`/`twocol`)은
  HTML 비활성/실패 시에만 동작하도록 우선순위를 명확히 한다.
- `_llm_pick_slide_layout`/`_heuristic_html_layout`이 STEP 카드·2단·KPI·타임라인·상태표·
  프로세스 흐름 같은 고밀도 레이아웃을 적극 선택하도록 힌트(역할/불릿 수)를 보강한다.

#### 5. HTML+Vertex 합성 — 이미지 슬롯 주입

- `slide_templates.py`의 cover/two_column/objective_detail 등에 **선택적 이미지 필드**
  (`heroImage`/`image`)를 추가한다. 값이 있으면 `background-image`/`<img>`로 합성하고, 없으면
  기존 그라디언트/플레이스홀더로 폴백(기존 호출 바이트 호환). 이렇게 하면 "HTML 레이아웃 품질 +
  Vertex 이미지"가 **단일 렌더 PNG**로 통합된다.
- 합성이 불가능한 레이아웃은 on-slide 레이어링(`_embed_fullbleed` back-most + 콘텐츠 위)으로
  폴백한다. 두 경로 모두 이미지를 폐기하지 않는다.

#### 6. 보존 가드 유지

- `imageFile`/`slideBackground`가 caller로부터 명시된 슬라이드는 기존 우선순위 그대로(변경 없음).
- Vertex 비활성/실패면 `_vertex_pre`가 비어 모든 분기가 네이티브/HTML 기존 동작으로 폴백(Req 3.3).
- 템플릿 `styleProfile`은 HTML 디자인 토큰(`design_tokens_for_profile`)과 네이티브 팔레트로
  계속 주입(Req 3.5).

## Testing Strategy

### Validation Approach

두 단계로 진행한다. 먼저 미수정 코드에서 버그를 재현하는 반례를 표면화하고(탐색), 그다음 수정이
올바르게 동작하며 기존 동작을 보존하는지 검증한다(Fix/Preservation). 모든 테스트는 게이트웨이·
Vertex·HTML 렌더를 목(mock)으로 대체해 네트워크 없이 헤르메틱하게 실행한다(기존
`scripts/test_media_output_quality_*` 컨벤션 준수, `hypothesis` 사용).

### Exploratory Bug Condition Checking

**Goal**: 수정 전에 버그를 재현하는 반례를 표면화하고 근본 원인(게이트 상호배타 + 임베드 폐기)을
확인 또는 반증한다. 반증되면 재가설을 세운다.

**Test Plan**: 결정 seam(`_tool_generate_pptx`의 Vertex 게이트와 임베드 가드)을 구동해, Vertex
이미지가 생성되었을 때 최종 슬라이드에 그 이미지가 임베드되는지를 단언한다. 미수정 코드에서 실패를
관찰한다.

**Test Cases**:
1. **임베드 폐기 재현**: `_vertex_pre[i]`가 채워진 슬라이드에 `nativeDiagram`이 부여된 상태 →
   미수정 코드는 이미지를 임베드하지 않는다(실패 예상).
2. **게이트 상호배타 재현**: `_html_enabled=True` + Vertex enabled + 표지/콘텐츠 슬라이드 →
   미수정 코드는 Vertex 사전생성을 스킵해 `_vertex_pre`가 비어 있다(실패 예상).
3. **저밀도 재현**: 본문 다수가 카드 폴백(`cards`/`twocol`)으로만 채워져 HTML 고밀도 레이아웃이
   적용되지 않음을 관찰(실패 예상).
4. **엣지(구조형 + 이미지)**: 구조형 슬라이드에 Vertex 이미지가 존재할 때 이미지가 폐기되는지
   관찰(미수정 코드에서 폐기 가능).

**Expected Counterexamples**:
- 생성된 Vertex 이미지가 최종 덱에 임베드되지 않음("생성됐으나 미사용").
- 원인: (a) `not _html_enabled` 게이트로 미생성, (b) `not native_diag` 가드로 폐기.

### Fix Checking

**Goal**: 버그 조건이 참인 모든 입력에 대해, 수정된 함수가 기대 동작(이미지 보존·임베드)을
산출하는지 검증한다.

**Pseudocode:**
```
FOR ALL input WHERE isBugCondition(input) DO
  plan := selectRenderPlan(input)
  ASSERT vertexImageIsPlaced(plan)        # 슬라이드 비주얼/배경/HTML 이미지 슬롯 중 하나
  ASSERT NOT generatedButUnused(plan)     # "생성됐으나 미사용" 0
END FOR
```

### Preservation Checking

**Goal**: 버그 조건이 거짓인 모든 입력에 대해, 수정된 함수가 원본 함수와 동일한 결과를 산출하는지
검증한다.

**Pseudocode:**
```
FOR ALL input WHERE NOT isBugCondition(input) DO
  ASSERT selectRenderPlan_fixed(input) == decideMedia_original(input)
END FOR
```

**Testing Approach**: 보존 검증에는 속성 기반 테스트(PBT)를 권장한다.
- 입력 도메인 전반을 자동 생성해 광범위한 케이스를 다룬다.
- 수동 단위 테스트가 놓치는 엣지(예: 구조형 + 이미지 동시 존재, HTML 비활성 + Vertex 실패)를
  포착한다.
- 비버그 입력에서 동작 불변성을 강하게 보장한다.

**Test Plan**: 미수정 코드에서 비버그 입력의 동작(구조형→네이티브, 이미지 지정→그대로, Vertex
미생성→네이티브/HTML)을 먼저 관찰한 뒤, 그 동작을 그대로 단언하는 PBT를 작성한다.

**Test Cases**:
1. **구조형 보존**: 흐름/트리/아키텍처 슬라이드가 미수정·수정 모두에서 편집 가능 네이티브 도형으로
   렌더됨을 관찰 후 단언(Req 3.1).
2. **HTML 풀블리드 보존**: `_html_enabled` 슬라이드의 `slideBackground` 설정 경로가 변하지 않음을
   관찰 후 단언(Req 3.2).
3. **Vertex 비활성/실패 폴백 보존**: `_vertex_pre`가 비었을 때 네이티브/HTML 폴백 동작이
   동일함을 관찰 후 단언(Req 3.3).
4. **템플릿 상속 보존**: `styleProfile` 주입 시 HTML 토큰/네이티브 팔레트 적용이 유지됨을 단언
   (Req 3.5).
5. **게이트웨이 제약 보존**: 비주얼 의도가 아닌 작업에서 Vertex가 호출되지 않고 LLM/operation은
   게이트웨이 경유만 사용함을 단언(Req 3.4, Property 5).

### Unit Tests

- `_classify_slide_role`의 역할 분기(cover/section/structural/content/visual) 경계값 테스트.
- `selectRenderPlan`의 각 분기에서 Vertex 이미지가 폐기되지 않음(손실 0) 단위 테스트.
- HTML 레이아웃에 `heroImage`/`image` 주입 시 합성, 미주입 시 기존 출력과 동일(바이트 호환) 테스트.
- 임베드 가드 교체 후 `imageFile`/`slideBackground` 명시 슬라이드의 우선순위 불변 테스트.

### Property-Based Tests

- **Property 1 (Fix)**: 무작위 SlideMediaState 중 `isBugCondition`이 참인 입력 생성 → 수정된
  `selectRenderPlan`이 Vertex 이미지를 항상 배치(슬라이드 비주얼/배경/HTML 슬롯)하고
  "생성됐으나 미사용"이 0임을 단언.
- **Property 2 (Preservation)**: `isBugCondition`이 거짓인 입력 생성 → 수정/원본 결정이 동일함을
  단언.
- **Property 3 (전역성·손실 0)**: 모든 상태 조합에 대해 `selectRenderPlan`이 정확히 하나의 주
  렌더러를 정하고, 이미지·구조 표현이 동시에 손실되는 출력이 없음을 단언.
- **Property 4 (HTML–Vertex 공존)**: `htmlEnabled ∧ vertexEnabled ∧ role∈{cover,content,
  visual}`인 입력에서 Vertex 생성이 억제되지 않음을 단언.

### Integration Tests

- 헤르메틱 `_tool_generate_pptx` 통합: HTML 렌더와 Vertex `generate`를 목으로 고정해, 표지·
  고밀도 본문·구조형·사진형이 섞인 덱에서 (a) 모든 Vertex 이미지가 임베드되고, (b) 구조형은
  네이티브 도형으로 남고, (c) 산출 PPTX 슬라이드 수/배경 임베드가 기대대로인지 검증.
- 컨텍스트 전환: 템플릿 적용 + 무템플릿 양쪽에서 스타일 상속과 하이브리드 합성이 모두 동작.
- 시각 확인: 생성된 덱의 슬라이드 PNG를 캡처해 참고 매뉴얼 수준의 밀도(STEP/카드·2단·이미지
  합성)가 반영됐는지 회귀 비교.
