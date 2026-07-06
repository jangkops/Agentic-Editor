# Implementation Plan

## Overview

본 계획은 bugfix 방법론(탐색 테스트 → 보존 테스트 → 수정 → fix-checking/회귀 검증)을 따른다.
`generate_pptx` 경로의 두 결함 — (A) HTML 품질 경로와 Vertex 이미지의 게이트 상호배타, (B) 임베드 가드의
Vertex 이미지 과잉 폐기 — 를 역할 기반 손실-0 결정 규칙(`selectRenderPlan`)으로 해소한다.
모든 테스트는 게이트웨이·Vertex·HTML 렌더를 목(mock)으로 대체해 네트워크 없이 헤르메틱하게 실행하며,
기존 `scripts/test_media_output_quality_*` 컨벤션(`hypothesis`, `-p no:cacheprovider -q`)을 준수한다.

## Tasks

- [x] 1. 버그 조건 탐색 테스트 작성 (`scripts/test_pptx_quality_vertex_images_bug_condition.py`)
  - **Property 1: Bug Condition** - 생성된 Vertex 이미지의 폐기/미생성 재현
  - **CRITICAL**: 이 테스트는 미수정 코드에서 반드시 FAIL 해야 한다 — 실패가 버그 존재를 증명한다
  - **DO NOT attempt to fix the test or the code when it fails** — 실패는 의도된 결과다
  - **NOTE**: 이 테스트는 기대 동작(생성된 Vertex 이미지가 항상 임베드됨)을 인코딩하며, 수정 후 PASS 하면 fix를 검증한다
  - **GOAL**: 버그를 재현하는 반례를 표면화하고 근본 원인(게이트 상호배타 + 임베드 폐기)을 확인/반증한다
  - **Scoped PBT Approach**: 결정 seam을 구동하는 PBT로 작성하되, 결정론적 재현을 위해 다음 구체 케이스에 스코프한다:
    - (B) 임베드 폐기: `_vertex_pre[i]`가 채워졌고 `hasNativeDiagram=True`, `hasImageFile=False`, `hasSlideBg=False` → `isBugCondition` 참 (design Bug Condition 의 `embedDiscarded`)
    - (A) 게이트 상호배타: `htmlEnabled=True ∧ vertexEnabled=True ∧ role∈{cover,content,visual} ∧ NOT hasVertexImage` → `isBugCondition` 참 (design Bug Condition 의 `gateSuppressed`)
  - 헤르메틱 통합으로 `_tool_generate_pptx`의 Vertex 게이트와 임베드 가드를 구동한다 (HTML 렌더와 Vertex `generate`는 목으로 고정)
  - 단언(미수정 코드의 기대 = Expected Behavior Properties): Vertex 이미지가 생성되면 최종 PPTX 해당 슬라이드에 임베드되고 "생성됐으나 미사용(generatedButUnused)"이 0이어야 한다 (design Property 1)
  - 미수정 코드에서 테스트를 실행한다
  - **EXPECTED OUTCOME**: 테스트 FAIL — (a) `not _html_enabled` 게이트로 미생성, (b) `not native_diag` 가드로 폐기되는 반례가 표면화됨
  - 발견한 반례를 문서화한다 (예: "본문 슬라이드 nativeDiagram=cards 부여 시 `_vertex_pre[i]` 이미지가 임베드되지 않음", "HTML 활성 환경에서 표지 imagePrompt가 있어도 `_vertex_pre`가 빔")
  - 테스트가 작성·실행되고 실패가 문서화되면 태스크 완료로 표시한다
  - **관찰된 반례 (미수정 코드, `./venv/bin/python -m pytest scripts/test_pptx_quality_vertex_images_bug_condition.py -p no:cacheprovider -q` → 3 failed):**
    - **(B) embedDiscarded** `test_bug_embed_discarded_vertex_image_is_used` / `test_property1_embed_discarded_pbt` — Falsifying example `prompt='프로세스 흐름도'`, `generated=1, embedded=0, unused=1`. 로그: `Vertex 이미지 생성 완료 — 성공 1장` 직후 `슬라이드 2 → 네이티브 다이어그램(type=architecture/block/tree)`. 즉 title+bullets는 비구조라 Vertex가 `_vertex_pre[i]`에 생성되지만, 임베드 루프의 2차 분류 `_classify_section_diagram(heading, imagePrompt, ...)`가 imagePrompt의 구조 키워드로 `nativeDiagram`을 부여 → `if (not native_diag and ...)` 가드가 생성된 이미지를 폐기(`generatedButUnused=1`).
    - **(A) gateSuppressed** `test_bug_gate_suppressed_html_excludes_vertex` — `assert 0 >= 1` (`vertex.generate 호출 0회`). `_html_enabled=True`일 때 Vertex 사전생성 블록이 `if (not _html_enabled ...)` 게이트로 통째로 스킵되어, 표지/콘텐츠에 imagePrompt가 있어도 `_vertex_pre`가 비고 고품질 이미지가 한 장도 생성/임베드되지 않음.
  - **상태**: 테스트 작성·실행 완료. 미수정 코드에서 3개 모두 FAIL(의도된 결과 — 버그 존재 증명). 수정(태스크 3.x) 후 태스크 3.7에서 동일 테스트 재실행 시 PASS 기대.
  - _Requirements: 1.1, 1.2, 1.3, 1.4_

- [x] 2. 보존 속성 테스트 작성 (`scripts/test_pptx_quality_vertex_images_preservation_pbt.py`) — 수정 전 작성
  - **Property 2: Preservation** - 구조형/무관 입력의 기존 동작 보존
  - **IMPORTANT**: observation-first 방법론을 따른다 — 먼저 미수정 코드의 비버그 입력 동작을 관찰·기록한 뒤, 그 동작을 그대로 단언한다
  - 비버그 입력 도메인(`isBugCondition`이 거짓인 입력)을 무작위 생성해 광범위하게 다룬다
  - 관찰 후 단언할 보존 동작:
    - 구조형 보존 (Req 3.1): 흐름/트리/아키텍처 슬라이드가 편집 가능 네이티브 도형으로 렌더됨
    - HTML 풀블리드 보존 (Req 3.2): `_html_enabled` 슬라이드의 `slideBackground` 설정 경로가 변하지 않음
    - Vertex 비활성/실패 폴백 보존 (Req 3.3): `_vertex_pre`가 비었을 때 네이티브/HTML 폴백 동작이 동일함
    - 템플릿 상속 보존 (Req 3.5): `styleProfile` 주입 시 HTML 토큰/네이티브 팔레트 적용이 유지됨
    - 명시 우선순위 보존: caller가 지정한 `imageFile`/`slideBackground` 슬라이드의 기존 우선순위가 유지됨
  - 미수정 코드에서 테스트를 실행한다
  - **EXPECTED OUTCOME**: 테스트 PASS — 보존해야 할 기준(baseline) 동작이 확인됨
  - 테스트가 작성·실행되고 미수정 코드에서 통과하면 태스크 완료로 표시한다
  - **관찰·단언한 보존 동작 (미수정 코드, `./venv/bin/python -m pytest scripts/test_pptx_quality_vertex_images_preservation_pbt.py -p no:cacheprovider -q` → 5 passed):**
    - **PRES-1 구조형 보존 (Req 3.1)** `test_pres1_structural_renders_native_shapes` — 흐름/트리/아키텍처 슬라이드(`_classify_section_diagram` kind∈{flow,tree,architecture})는 Vertex 사전생성 스킵(`fake.calls==0`), `ppt/media` 래스터 0장, 불릿 라벨이 편집 가능 네이티브 도형 텍스트로 보존됨을 단언.
    - **PRES-2 HTML 풀블리드 보존 (Req 3.2)** `test_pres2_html_fullbleed_path_preserved` — HTML 활성 + Vertex 비활성(비버그)일 때 content 슬라이드가 `slideBackground` 경로로 (0,0) 풀블리드 PICTURE(13.333×7.5in)를 임베드하고, 섹션 HTML→PNG 캡처 바이트가 그대로 임베드됨을 단언.
    - **PRES-3 Vertex 비활성/실패 폴백 보존 (Req 3.3)** `test_pres3_vertex_unavailable_native_fallback` — Vertex disabled / generate 실패 두 경로 모두 `_vertex_pre`가 비어 래스터 미임베드 + 본문 불릿 텍스트가 네이티브 폴백으로 보존됨을 단언(media-output-quality 회귀 방지).
    - **PRES-4 템플릿 상속 보존 (Req 3.5)** `test_pres4_style_profile_inheritance_preserved` — `styleProfile`이 섹션 HTML 렌더러로 정확히 전달되고(HTML 토큰 상속), `_build_palette(profile)`가 결정론적 네이티브 팔레트를 산출함을 단언.
    - **PRES-5 명시 우선순위 보존** `test_pres5_caller_specified_image_precedence` — caller가 `imageFile`/`slideBackground`를 지정한 슬라이드는 Vertex 생성 스킵(`fake.calls==0`)하고 caller의 정확한 이미지 바이트가 임베드됨(기존 우선순위 유지)을 단언.
  - **헤르메틱 원칙 준수**: Bedrock 게이트웨이(`_get_gw`), Vertex(`get_vertex_image_client` 스텁 + `generate` 목), HTML→PNG 렌더(`_render_html_slide_to_png`/`_generate_html_slide_for_section`), `_tool_generate_image` 모두 목 처리 — 네트워크 호출 0. 각 테스트는 design `isBugCondition` 미러로 비버그 입력임을 precondition 검증한다.
  - **상태**: 테스트 작성·실행 완료. 미수정 코드에서 5개 모두 PASS(baseline 보존 동작 확인). 수정(태스크 3.x) 후 태스크 3.8에서 동일 테스트 재실행 시 PASS 기대(회귀 없음).
  - _Requirements: 3.1, 3.2, 3.3, 3.5_

- [x] 3. PPTX 고품질 + Vertex 이미지 활용 수정 구현

  - [x] 3.1 슬라이드 역할 분류기 `_classify_slide_role` 도입
    - `ai_engine/server.py`에 `_classify_slide_role(slide, isCover) -> {cover|section|structural|content|visual}` 추가
    - `_classify_section_diagram` / `_looks_structural`을 재사용한다 (LLM/게이트웨이 추가 호출 없음)
    - `kind ∈ {flow, tree, architecture}` → `structural`; visual intent ∧ NOT kind → `visual`; cover → `cover`; 그 외(`kpi`/`cards`/`twocol` 등 고밀도 콘텐츠) → `content`
    - 진짜 구조형만 `structural`로 분류해 Req 3.1 보존을 유지한다
    - _Bug_Condition: isBugCondition(input) — role 미구분으로 인한 폐기/미생성_
    - _Expected_Behavior: classifyRole(slide, isCover) from design (Fix Implementation §1)_
    - _Preservation: 구조형→네이티브 분류 유지 (design Preservation Requirements)_
    - _Requirements: 2.2, 3.1_

  - [x] 3.2 Vertex 사전생성 게이트 재설계 — HTML과 공존
    - `_tool_generate_pptx`의 게이트를 `not _html_enabled` 상호배타에서 `if (AE_PREFER_VERTEX_IMAGE != 0 and vertexClient.enabled):`로 변경한다 (`_html_enabled` 무관)
    - `_gen_vertex_slide` 스킵 조건을 `_classify_slide_role(...) == structural` 로 통일한다 (cover/content/visual 슬라이드만 Vertex 생성 대상)
    - 표지/히어로는 사진·일러스트형 프롬프트, content는 HTML 이미지 슬롯용 보조 비주얼 프롬프트로 구성한다
    - _Bug_Condition: gateSuppressed (htmlEnabled ∧ vertexEnabled ∧ role∈{cover,content,visual} ∧ NOT hasVertexImage)_
    - _Expected_Behavior: HTML 활성 시에도 Vertex 생성이 억제되지 않음 (design Property 4)_
    - _Preservation: structural 역할은 네이티브 우선으로 스킵, Vertex 비활성/실패 시 폴백 (Req 3.3)_
    - _Requirements: 2.1, 2.3, 3.2, 3.3_

  - [x] 3.3 임베드 가드 → 손실 없는 결정 규칙 `selectRenderPlan` 으로 교체
    - 폐기형 가드 `if (not native_diag and not img_file and not slide_bg): img_file = _vertex_pre.get(i)`를 `selectRenderPlan(slide_state)` 기반 분기로 교체한다
    - 분기 규칙 (design Fix Implementation §3):
      - `primary == HTML` ∧ slide_bg 존재: HTML 풀블리드 주 렌더러 유지, `pre`는 히어로/이미지 슬롯으로 합성하거나 합성 불가 시 on-slide 레이어로 보존
      - `primary == NATIVE_SHAPES`: 네이티브 도형 유지, `pre` 존재 시 `_native_over_bg`/`_eff_bg` backdrop으로 보존
      - `primary == VERTEX_IMAGE`: `img_file := pre` (이미지가 곧 슬라이드 비주얼)
      - `content` ∧ HTML 비활성: 네이티브 카드 + `pre`를 풀블리드 backdrop으로 보존
    - **핵심 불변식**: `pre`(생성된 Vertex 이미지)가 존재하면 어떤 분기에서도 폐기되지 않는다 (손실 0)
    - _Bug_Condition: embedDiscarded (hasVertexImage ∧ hasNativeDiagram ∧ NOT hasImageFile ∧ NOT hasSlideBg)_
    - _Expected_Behavior: vertexImageIsPlaced(plan) ∧ NOT generatedButUnused(plan) (design Fix Checking)_
    - _Preservation: imageFile/slideBackground 명시 우선순위, 구조형 네이티브 유지_
    - _Requirements: 2.1, 2.2, 2.4_

  - [x] 3.4 HTML 고밀도 경로를 본문 주 렌더러로 승격
    - `_html_enabled`이면 content 슬라이드의 기본 경로를 HTML 고밀도 레이아웃(`_generate_html_slide_for_section` → `slideBackground`)으로 둔다
    - 카드 폴백(`cards`/`twocol`)은 HTML 비활성/실패 시에만 동작하도록 우선순위를 명확히 한다
    - `_llm_pick_slide_layout`/`_heuristic_html_layout`이 STEP 카드·2단·KPI·타임라인·상태표·프로세스 흐름 같은 고밀도 레이아웃을 적극 선택하도록 힌트(역할/불릿 수)를 보강한다
    - _Bug_Condition: 저밀도 폴백 우세로 참고 매뉴얼 밀도에 미달_
    - _Expected_Behavior: 참고 매뉴얼 수준 고밀도 슬라이드 산출 (Req 2.4)_
    - _Preservation: HTML 풀블리드 경로 유지 (Req 3.2), HTML 실패 시 카드 폴백_
    - _Requirements: 2.4, 3.2_

  - [x] 3.5 HTML+Vertex 합성 — `slide_templates.py` 이미지 슬롯 주입
    - cover/two_column/objective_detail 레이아웃에 선택적 이미지 필드(`heroImage`/`image`)를 추가한다
    - 값이 있으면 `background-image`/`<img>`로 합성, 없으면 기존 그라디언트/플레이스홀더로 폴백(기존 호출 바이트 호환)
    - 합성 불가 레이아웃은 on-slide 레이어링(`_embed_fullbleed` back-most + 콘텐츠 위)으로 폴백 — 두 경로 모두 이미지를 폐기하지 않는다
    - _Bug_Condition: HTML 레이아웃에 이미지 합성 경로 부재_
    - _Expected_Behavior: "HTML 레이아웃 품질 + Vertex 이미지"가 단일 렌더 PNG로 통합 (design Fix Implementation §5)_
    - _Preservation: 이미지 필드 미주입 시 기존 출력과 바이트 호환_
    - _Requirements: 2.3, 2.4, 3.5_

  - [x] 3.6 보존 가드 유지
    - caller가 명시한 `imageFile`/`slideBackground` 슬라이드는 기존 우선순위 그대로 유지한다 (변경 없음)
    - Vertex 비활성/실패면 `_vertex_pre`가 비어 모든 분기가 네이티브/HTML 기존 동작으로 폴백한다 (Req 3.3)
    - 템플릿 `styleProfile`은 HTML 디자인 토큰(`design_tokens_for_profile`)과 네이티브 팔레트로 계속 주입한다 (Req 3.5)
    - LLM/operation JSON 생성은 Bedrock Gateway 경유만, Vertex는 이미지 생성 경로에서만 호출됨을 유지한다 (Req 3.4)
    - **검증 결과 (보존 전용 태스크 — 코드 변경 없음, 태스크 3.1~3.4가 가드를 약화시키지 않았음을 확인):**
      - **명시 우선순위 보존** — `_gen_vertex_slide`는 `_sd.get("slideBackground") or imageFile or nativeDiagram`이면 Vertex 생성 스킵; 임베드 루프의 `_select_render_plan` 분기는 `slide_bg`/`img_file` 존재 시 caller 값을 덮어쓰지 않고 `pre`를 backdrop/hero로만 보존(`if not slide_bg: slide_bg = _pre_rel`). HTML 렌더도 caller 지정 슬라이드는 스킵. ✓
      - **Vertex 비활성/실패 폴백** — `_vertex_pre = {}`로 초기화, `_vc_pptx.enabled`가 거짓이면 게이트 블록 통째 스킵; generate 예외/빈 결과는 `return _idx, ""`로 미수집 → `_pre_rel` 없음 → `if _pre_rel:` 블록 스킵, 기존 네이티브/HTML 동작 그대로. ✓
      - **styleProfile 주입** — `_generate_html_slide_for_section(..., style_profile=style_profile)` + `design_tokens_for_profile`(HTML 토큰, 2131/4722행), `_build_palette(style_profile) → _tpl_palette_for_native`(네이티브 팔레트, 4396행 및 4699/4977/5154/5302행) 유지. ✓
      - **게이트웨이 제약** — Vertex는 `_try_vertex_image_single`/`_gen_vertex_slide`의 `.generate()` 이미지 경로에서만 호출, LLM/operation JSON 구조화는 `_get_gw`(Bedrock Gateway) 경유. ✓
    - **검증**: `py_compile` OK, diagnostics 없음, `scripts/test_pptx_quality_vertex_images_preservation_pbt.py` → 5 passed.
    - _Bug_Condition: N/A (보존 전용 — isBugCondition 거짓 입력 보호)_
    - _Expected_Behavior: 비버그 입력에서 원본과 동일한 결과 (design Preservation Checking)_
    - _Preservation: Preservation Requirements 전체 (Req 3.1~3.5)_
    - _Requirements: 3.1, 3.2, 3.3, 3.4, 3.5_

  - [x] 3.7 버그 조건 탐색 테스트가 이제 통과하는지 검증
    - **Property 1: Expected Behavior** - 생성된 Vertex 이미지가 항상 임베드됨
    - **IMPORTANT**: 태스크 1의 동일한 테스트를 재실행한다 — 새 테스트를 작성하지 않는다
    - 태스크 1의 테스트는 기대 동작을 인코딩하며, 통과 시 기대 동작이 충족됨을 확인한다
    - 태스크 1의 버그 조건 탐색 테스트를 수정된 코드에서 실행한다
    - **EXPECTED OUTCOME**: 테스트 PASS (버그가 수정됨 — "생성됐으나 미사용"이 0)
    - **검증 결과 (수정된 코드, `./venv/bin/python -m pytest scripts/test_pptx_quality_vertex_images_bug_condition.py -p no:cacheprovider -q` → 3 passed in 2.07s):** 태스크 1의 3개 테스트(`test_bug_embed_discarded_vertex_image_is_used`, `test_bug_gate_suppressed_html_excludes_vertex`, `test_property1_embed_discarded_pbt`) 모두 PASS. 생성된 Vertex 이미지가 폐기되지 않고 임베드됨(`generatedButUnused == 0`) — 버그 수정 확인.
    - **추가 수정 (task 3.7 중 발견):** PBT(`test_property1_embed_discarded_pbt`)가 `prompt='시스템 아키텍처 구성도'`에서 precondition 실패(`generated=0`)했다. 근본 원인 — `_classify_slide_role` step 1이 design classifyRole 의사코드(`kind := _classify_section_diagram(title, body)`)를 벗어나 `if not kind and img_prompt:` 폴백으로 imagePrompt 구조 키워드를 kind 로 흡수하고 있었다. 그 결과 비구조형 content 슬라이드(title+bullets 비구조형)인데 구조형 imagePrompt를 가지면 role=structural 로 오분류 → Vertex 사전생성이 통째로 억제(`generated=0`, 손실)되었다. design 규약에 맞춰 imagePrompt 폴백을 제거(`ai_engine/server.py` `_classify_slide_role`)하여 손실-0 불변식을 복원했다. 임베드 루프의 2차 분류는 backdrop 결정에만 imagePrompt 를 사용하고 `selectRenderPlan` 이 생성 이미지를 보존하므로 동작 일관성 유지. 회귀 확인: 보존 테스트 `scripts/test_pptx_quality_vertex_images_preservation_pbt.py` → 5 passed.
    - _Requirements: 2.1, 2.2, 2.3 (Expected Behavior Properties from design Property 1)_

- [x] 3.8 보존 테스트가 여전히 통과하는지 검증
    - **Property 2: Preservation** - 구조형/무관 입력의 기존 동작 보존
    - **IMPORTANT**: 태스크 2의 동일한 테스트를 재실행한다 — 새 테스트를 작성하지 않는다
    - 태스크 2의 보존 속성 테스트를 수정된 코드에서 실행한다
    - **EXPECTED OUTCOME**: 테스트 PASS (회귀 없음)
    - 수정 후에도 모든 보존 테스트가 통과함을 확인한다
    - **검증 결과 (수정된 코드, `./venv/bin/python -m pytest scripts/test_pptx_quality_vertex_images_preservation_pbt.py -p no:cacheprovider -q` → 5 passed in 2.07s):** 태스크 3.1~3.6 수정 후 PRES-1~PRES-5 보존 테스트 5개 모두 PASS. 회귀 없음 확인 — 구조형 네이티브 렌더(Req 3.1), HTML 풀블리드(Req 3.2), Vertex 비활성/실패 폴백(Req 3.3), styleProfile 상속(Req 3.5), 명시 우선순위 보존이 수정 후에도 유지됨.
    - _Requirements: 3.1, 3.2, 3.3, 3.5_

- [x] 4. 추가 fix-checking 속성 테스트 작성 (`scripts/test_pptx_quality_vertex_images_fix_pbt.py`)
  - **Property 3: Loss-Zero** - 결정 규칙의 전역성·결정성 (손실 0)
  - 모든 가능한 SlideMediaState 조합(이미지/네이티브/배경/역할/게이트 플래그)에 대해 `selectRenderPlan`이 정확히 하나의 주 렌더러를 정하고 Vertex 이미지 사용처를 명시하며, "이미지와 구조 표현이 동시에 손실되는" 출력이 절대 없음을 단언한다 (전역 정의 + 손실 0 불변식)
  - **EXPECTED OUTCOME**: 수정된 코드에서 PASS
  - **검증 결과 (수정된 코드, `./venv/bin/python -m pytest scripts/test_pptx_quality_vertex_images_fix_pbt.py -p no:cacheprovider -q` → 3 passed in 1.09s):** 순수 결정 함수 `_select_render_plan`을 SlideMediaState의 모든 조합(2^6 bool × 5 role = valid 240 states)에 대해 구동.
    - **전역 정의·정확히 하나의 주 렌더러** `test_property3_loss_zero_exhaustive` — 모든 조합에서 `primary ∈ {HTML, NATIVE_SHAPES, VERTEX_IMAGE}` 정확히 하나, `vertex_slot ∈ {hero, backdrop, visual, none}`.
    - **손실 0** — `has_vertex_image` 이면 `vertex_slot != "none"`(생성 이미지 어떤 분기에서도 미폐기), `NOT has_vertex_image` 이면 `vertex_slot == "none"`. 이미지·구조 동시 손실(`image_lost ∧ structural_lost`) 출력 없음.
    - **결정성** `test_property3_determinism_exhaustive` — 같은 입력 → 같은 출력(두 번 호출 동일).
    - **PBT** `test_property3_loss_zero_pbt` — hypothesis 300 examples 무작위 도메인에서 동일 불변식 성립(회귀 시 shrunk 반례 표면화).
  - **헤르메틱 원칙 준수**: `_select_render_plan`은 LLM/게이트웨이 호출이 없는 순수 함수 — 네트워크 호출 0. 도메인 정직성 유지(`hasVertexImage ⇒ vertexEnabled`인 상태만).
  - **상태**: 테스트 작성·실행 완료. 수정된 코드에서 3개 모두 PASS — 손실 0 불변식이 전역적으로 검증됨.
  - _Requirements: 2.2, 2.4_

- [x] 5. HTML–Vertex 공존 속성 테스트 작성 (같은 fix_pbt 파일에 추가)
  - **Property 4: HTML-Vertex Coexistence** - 하이브리드 게이트
  - `htmlEnabled ∧ vertexEnabled ∧ role∈{cover,content,visual}`인 입력에서 결정 규칙이 HTML 레이아웃을 주 렌더러로 유지하면서도 Vertex 이미지 생성을 억제하지 않음을 단언한다. 구조형 역할에서는 네이티브 도형 우선이되 생성된 이미지가 backdrop으로 보존됨을 단언한다
  - **EXPECTED OUTCOME**: 수정된 코드에서 PASS
  - **검증 결과 (수정된 코드, `./venv/bin/python -m pytest scripts/test_pptx_quality_vertex_images_fix_pbt.py -p no:cacheprovider -q` → 7 passed in 1.09s):** 기존 task 4 파일에 Property 4 테스트 4개 추가(파일 미덮어쓰기). 순수 결정 함수 `_select_render_plan`을 공존/구조형 서브도메인에 대해 구동.
    - **공존(전수) `test_property4_html_vertex_coexistence_exhaustive`** — `htmlEnabled ∧ vertexEnabled ∧ role∈{cover,content,visual} ∧ hasVertexImage`의 모든 (native/image/slide_bg) 조합(2×2×2×3=24)에서 `vertex_slot ∈ {hero,visual,backdrop}`(절대 none — 생성 억제/폐기 없음), `slide_bg` 존재 시 `primary=="HTML"` 유지 + `slot=="hero"`(HTML+Vertex 단일 렌더 공존, 상호배타 아님).
    - **구조형(전수) `test_property4_structural_preserves_image_as_backdrop_exhaustive`** — `role=structural ∧ native_diagram ∧ ¬slide_bg ∧ ¬image_file ∧ hasVertexImage`에서 `primary=="NATIVE_SHAPES"`(네이티브 도형 우선) ∧ `slot=="backdrop"`(생성 이미지 폐기 금지, backdrop 보존).
    - **PBT `test_property4_html_vertex_coexistence_pbt`** (200 examples) / **`test_property4_structural_backdrop_pbt`** (100 examples) — 무작위 도메인에서 동일 불변식 성립(회귀 시 shrunk 반례 표면화). 모든 단언에 손실-0 불변식(`_assert_loss_zero_invariants`)도 동시 검증.
  - **헤르메틱 원칙 준수**: `_select_render_plan`은 LLM/게이트웨이 호출이 없는 순수 함수 — 네트워크 호출 0. 도메인 정직성 유지(`hasVertexImage ⇒ vertexEnabled`인 상태만).
  - **상태**: 테스트 작성·실행 완료. 수정된 코드에서 7개(task 4의 3개 + Property 4의 4개) 모두 PASS — HTML–Vertex 공존 게이트가 검증됨.
  - _Requirements: 2.1, 2.4, 3.1, 3.2_

- [x] 6. 게이트웨이 제약 보존 속성 테스트 작성 (preservation_pbt 파일에 추가)
  - **Property 5: Gateway Constraint** - 이미지 외 호출은 Vertex 미사용
  - 모든 입력에 대해 LLM/operation JSON 생성 호출은 Bedrock Gateway 경유로만 발생하고 Vertex는 이미지 생성 경로에서만 호출됨(이미지 외 작업에서 Vertex 호출 0)을 단언한다
  - 비주얼 의도가 아닌 작업에서 Vertex가 호출되지 않음을 목 스파이로 검증한다
  - **EXPECTED OUTCOME**: 수정된 코드에서 PASS
  - **검증 결과 (수정된 코드, `./venv/bin/python -m pytest scripts/test_pptx_quality_vertex_images_preservation_pbt.py -p no:cacheprovider -q` → 8 passed in 2.94s):** 기존 task 2 파일(PRES-1~5)에 Property 5 테스트 3개 추가(파일 미덮어쓰기). 헤르메틱 목 스파이(`_SpyVertexClient`: generate `model_class` 기록 + `__getattr__`로 enabled/generate 외 멤버 접근을 foreign/LLM-like로 포착·차단, `_GwSpy`: Bedrock Gateway 취득 기록)로 실제 `_tool_generate_pptx` 결정 seam을 구동.
    - **PROP5-1 비주얼 외 작업 Vertex 0 `test_prop5_nonvisual_structural_uses_no_vertex`** — 구조형(흐름/트리/아키텍처, role=structural) 슬라이드에서 `spy.generate_calls == []`(이미지 외 작업 Vertex 호출 0) + Vertex가 LLM/operation 용도로 접근되지 않음(`llm_like_access == []`, `foreign_access == []`).
    - **PROP5-2 Vertex는 이미지 생성 경로로만 `test_prop5_vertex_only_via_image_generation_path`** — visual intent(비구조형 + 사진/일러스트 imagePrompt, role=visual) 슬라이드에서 Vertex가 호출되되 모든 호출의 `model_class`가 이미지 생성 클래스(`"image" in model_class`, 즉 `image_generation_high_quality`)이며 텍스트/LLM 클래스가 아님 + Vertex가 LLM/operation 용도로 미사용.
    - **PROP5-3 LLM/operation은 게이트웨이 경유 `test_prop5_llm_operation_via_gateway_not_vertex`** — HTML 품질 경로(HTML-on)에서도 LLM/operation 생성은 Bedrock Gateway(`_get_gw`) 취득(`gw_spy.calls >= 1`)을 거치고, 구조형(비주얼 아님) 작업에서 Vertex는 호출 0(`generate_calls == []`)임을 단언. Vertex는 어떤 경우에도 LLM/operation 생성기로 재사용되지 않음.
  - **헤르메틱 원칙 준수**: 게이트웨이(`_get_gw` → `_GwSpy`), Vertex(`get_vertex_image_client` → `_SpyVertexClient`), HTML→PNG 렌더(`_render_html_slide_to_png`/`_generate_html_slide_for_section`), `_tool_generate_image` 모두 목 처리 — 네트워크 호출 0. 각 테스트는 design `_classify_slide_role`/`_classify_section_diagram`/`_has_visual_intent`로 입력 도메인(구조형/visual)을 precondition 검증한다.
  - **상태**: 테스트 작성·실행 완료. 수정된 코드에서 8개(task 2의 PRES 5개 + Property 5의 3개) 모두 PASS — 게이트웨이 제약(gateway.md 이미지 예외 조항: LLM/operation은 Bedrock Gateway 경유, Vertex는 이미지 생성 경로에서만)이 검증됨.
  - _Requirements: 3.4_

- [x] 7. Checkpoint — 모든 테스트 통과 확인
  - `scripts/test_pptx_quality_vertex_images_bug_condition.py`, `_fix_pbt.py`, `_preservation_pbt.py` 전체를 헤르메틱하게 실행한다
  - 기존 `scripts/test_media_output_quality_*` 및 `scripts/test_html_*` 회귀 테스트도 함께 실행해 회귀가 없음을 확인한다
  - 통합 검증: 표지·고밀도 본문·구조형·사진형이 섞인 덱에서 (a) 모든 Vertex 이미지가 임베드되고, (b) 구조형은 네이티브 도형으로 남고, (c) 산출 PPTX 슬라이드 수/배경 임베드가 기대대로인지 확인한다
  - 모든 테스트가 통과하는지 확인하고, 의문이 생기면 사용자에게 질문한다
  - **검증 결과 (모두 헤르메틱, 네트워크 0):**
    1. **세 신규 스펙 테스트** `./venv/bin/python -m pytest scripts/test_pptx_quality_vertex_images_bug_condition.py scripts/test_pptx_quality_vertex_images_fix_pbt.py scripts/test_pptx_quality_vertex_images_preservation_pbt.py -p no:cacheprovider -q` → **18 passed** (bug_condition 3 + fix_pbt 7 + preservation_pbt 8). 수정된 코드에서 Property 1(Bug Condition→Expected Behavior), 2(Preservation), 3(Loss-Zero), 4(HTML–Vertex 공존), 5(Gateway Constraint) 전부 통과.
    2. **회귀 스위트** `scripts/test_media_output_quality_bug_condition.py`, `_fix_pbt.py`, `_preservation_pbt.py`, `scripts/test_html_pipeline.py`, `scripts/test_html_slides.py` → **13 passed** (회귀 없음). `test_html_*`는 워크스페이스에 `test_html_pipeline.py`와 `test_html_slides.py` 두 개 존재 — 둘 다 명시 경로로 실행.
    3. **통합 검증** 신규 `scripts/test_pptx_quality_vertex_images_integration.py` → **2 passed**. 실제 `_tool_generate_pptx` 결정 seam을 표지·고밀도 본문·구조형·사진형 혼합 덱으로 구동(게이트웨이/Vertex/HTML 렌더/`_tool_generate_image` 모두 목, 네트워크 0):
       - **SCENARIO 1 (HTML OFF) `test_integration_mixed_deck_html_off`** — (a) 생성된 모든 Vertex 이미지가 바이트 단위로 `ppt/media`에 임베드됨(`unused==0`, 손실 0), (b) 구조형(flow) 슬라이드는 풀블리드 래스터 없이 편집 가능 네이티브 도형 텍스트로 남음(Vertex 사전생성 스킵), (c) 슬라이드 수 == 표지+3 == 4.
       - **SCENARIO 2 (HTML ON) `test_integration_mixed_deck_html_on_coexistence`** — HTML 고밀도 레이아웃이 주 렌더러이면서 Vertex 생성이 억제되지 않음(`fake.calls>=1`, gate A 수정 확인), 비구조형 슬라이드의 Vertex 히어로가 HTML 섹션 렌더러로 전달·합성되어 최종 덱에 보존됨(`unused==0`), content 슬라이드는 (0,0) 풀블리드 슬라이드배경 보유, 슬라이드 수 == 4.
    - **합산 한 번에 실행** 9개 파일 동시 실행 → **33 passed in 26.21s** (네트워크 0, 회귀 0).
  - **상태**: 모든 테스트 통과. parent task 3(수정 구현)과 전체 스펙 완료. 의문점 없음 — 사용자 확인 불필요.

## Task Dependency Graph

```json
{
  "waves": [
    { "id": 0, "tasks": ["1", "2"] },
    { "id": 1, "tasks": ["3.1"] },
    { "id": 2, "tasks": ["3.2", "3.3", "3.4"] },
    { "id": 3, "tasks": ["3.5", "3.6"] },
    { "id": 4, "tasks": ["3.7", "3.8"] },
    { "id": 5, "tasks": ["4", "5", "6"] },
    { "id": 6, "tasks": ["7"] }
  ]
}
```

## Notes

- **PBT 상태 추적**: Property 1~5 태스크는 `**Property N: Type**` 형식을 사용해 hover 상태를 활성화한다. Property 1은 Bug Condition(미수정 코드에서 FAIL → 수정 후 Expected Behavior로 PASS), Property 2는 Preservation(미수정·수정 모두 PASS)이다.
- **헤르메틱 원칙**: 모든 테스트는 네트워크 호출이 없어야 한다. 게이트웨이는 `_get_gw` 패치, Vertex는 `_try_vertex_image_single` 목 + `get_vertex_image_client` disabled 스텁, HTML 렌더는 목으로 고정한다.
- **게이트웨이 제약(steering 준수)**: LLM/operation JSON 생성은 Bedrock Gateway 경유만 유지하며, Vertex는 이미지 생성 경로에서만 호출된다(gateway.md 이미지 예외 조항). Property 5가 이를 검증한다.
- **손실-0 불변식**: 생성된 Vertex 이미지(`_vertex_pre[i]`)는 어떤 분기에서도 폐기되지 않는다 — 최소한 `_native_over_bg`/`_eff_bg` backdrop으로 보존된다. Property 3가 전역적으로 검증한다.
- **회귀 방지**: 7번 Checkpoint에서 `scripts/test_media_output_quality_*` 및 `scripts/test_html_*` 기존 회귀 스위트를 함께 실행한다.
