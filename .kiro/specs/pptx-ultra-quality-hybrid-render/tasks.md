# Implementation Plan

## Overview

본 계획은 `ai_engine/server.py`의 `_tool_generate_pptx`에 **슬라이드 역할 기반 하이브리드 렌더 라우팅**을
opt-in 플래그(`AE_HYBRID_RENDER`) 게이팅으로 도입한다. 설계 원칙은 **재사용·게이팅·보존**이다.

구현 순서는 test-driven이며, 순수 결정 함수를 먼저(플래그 파서 → 하이브리드 플랜 선택기 → Vertex 프롬프트
빌더) 각자의 property-based test와 함께 만들고, 그다음 content 편집 렌더러 + 바운디드 Image_Slot,
마지막으로 `_tool_generate_pptx` 와이어링과 회귀·체크포인트를 수행한다. 각 correctness property(설계
Property 1–21)는 단일 property-based test로 구현하며, 보존 프로퍼티(14/15/16/17/18)는 이미 검증된
기존 PBT를 재사용·확장한다(중복 구현 금지).

구현 언어는 Python이다(설계가 구체 Python 시그니처를 명시 — 언어 선택 질문 불필요).

### 필수 실행 규약 (모든 코드 태스크에 적용)

- **`ai_engine/server.py` 에디터 버퍼는 STALE(오래됨)일 수 있다.** 편집 전 반드시 디스크 실체를
  `sed -n`/`grep -n`으로 확인하고, 디스크 기준으로 패치한다. 삽입/치환 앵커는 `grep -c`로
  **정확히 1회(count == 1)** 매칭됨을 단언한 뒤에만 편집한다.
- 각 편집 직후 `./venv/bin/python -m py_compile ai_engine/server.py`(또는 대상 파일)로 컴파일하고,
  `get_diagnostics`로 **에러 0개**를 확인한다.
- **`AE_HYBRID_RENDER` 기본 OFF(미설정/`"0"`)에서는 산출 .pptx가 하이브리드 도입 이전과 바이트 단위로
  동일해야 한다.** 신규 분기는 플래그 OFF일 때 완전한 no-op이어야 한다.
- **모든 테스트는 헤르메틱**하다: Vertex 비활성/목(`get_vertex_image_client` disabled 스텁 + `generate` 목),
  Bedrock Gateway 목(`_get_gw`), HTML→PNG 렌더 목 — **네트워크 호출 0**.
- 테스트 실행은 항상 `./venv/bin/python -m pytest <file> -p no:cacheprovider -q`. **인라인 멀티라인
  `python -c` 금지** — 재현 가능한 스크립트 파일로만 실행한다.
- PBT는 `hypothesis`를 사용하고 **각 property 테스트 최소 100 iteration**. 각 테스트 상단에 주석 태그
  `Feature: pptx-ultra-quality-hybrid-render, Property N: {property_text}` 를 부착한다.

## Tasks

- [x] 1. Feature Flag 파서 `_hybrid_render_enabled` 구현

  - [x] 1.1 `_hybrid_render_enabled(env: str) -> bool` 순수 함수 추가
    - 디스크에서 `_tool_generate_pptx`의 `_html_enabled` 결정 블록 위치를 `grep -n`으로 확인한 뒤,
      `ai_engine/server.py`에 순수 함수 `_hybrid_render_enabled(env)`를 추가한다 (모듈 레벨, 라우팅 미포함)
    - 파싱 규칙: `"1"` → `True`; 미설정/`""`/`"0"` → `False`; 그 외 인식 불가 값(`"2"`, `"true"`, `"on"` 등)
      → `False` + 경고 로그 1줄(≤200자). 함수는 **어떤 입력에도 raise하지 않는다**
    - 결정론적(동일 입력 → 동일 출력), LLM/네트워크 호출 없음
    - 편집 후 앵커 count==1 확인 → `py_compile` → `get_diagnostics` 0 에러
    - _Requirements: 1.7, 6.1, 6.2, 6.5_

  - [x]* 1.2 `_hybrid_render_enabled` property test 작성 (`scripts/test_pptx_hybrid_render_flag_pbt.py`)
    - **Property 20: Feature Flag 파서의 결정성**
    - **Validates: Requirements 1.7, 6.1, 6.2, 6.5**
    - 임의 문자열 입력(빈 문자열/공백/유니코드/인식 불가 값 포함)에 대해 `"1"`에서만 `True`, 그 외 전부 `False`,
      예외 미발생을 hypothesis로 100+ iteration 검증한다
    - `./venv/bin/python -m pytest scripts/test_pptx_hybrid_render_flag_pbt.py -p no:cacheprovider -q`

  - [x]* 1.3 인식 불가 값 경고 로그 단위 테스트 (같은 flag_pbt 파일에 추가)
    - `"1"`/`"0"`/`""`/`"true"`/`"on"`/`"2"` 개별 입력의 반환값을 단언하고, 인식 불가 값에서 경고 로그가
      정확히 1줄 기록됨을 로그 캡처로 관측한다 (R6.5 경계 케이스)
    - _Requirements: 6.5_

- [x] 2. 하이브리드 플랜 선택기 `_select_hybrid_render_plan` 구현

  - [x] 2.1 `_select_hybrid_render_plan(...)` 순수 함수 추가
    - 디스크에서 기존 `_select_render_plan`(`server.py:3158` 부근) 정의 위치를 `grep -n`으로 확인한 뒤 인접에
      순수 함수 `_select_hybrid_render_plan(*, role, vertex_enabled, html_enabled, has_vertex_image,
      has_native_diagram, has_image_file, has_slide_bg) -> dict` 를 추가한다
    - 반환 `{"primary": ..., "vertex_slot": ..., "editable": bool}`. 설계 결정 테이블(Slide_Role ×
      Vertex enabled/disabled × HTML on/off) 그대로 구현
    - 불변식: `primary` 정확히 1개; `has_vertex_image ⇒ vertex_slot != "none"`(최종 게이트는 기존
      `_select_render_plan`에 위임/검증); `VERTEX_FULLBLEED ⇒ role∈{cover,section,visual} ∧ vertex_enabled`;
      `content ⇒ editable ∧ primary==NATIVE_EDITABLE`; `structural ⇒ primary==NATIVE_SHAPES`
    - 모호/미정의/복수 후보 role 입력은 진입 전 `content`로 결정론 확정 (방어적 이중화)
    - LLM/게이트웨이/네트워크 호출 없음. 편집 후 앵커 count==1 → `py_compile` → `get_diagnostics` 0 에러
    - _Requirements: 1.2, 1.3, 1.4, 1.5, 1.6, 1.8_

  - [x]* 2.2 role 판정 전역성 property test (`scripts/test_pptx_hybrid_render_plan_pbt.py`)
    - **Property 1: 역할 판정의 전역성**
    - **Validates: Requirements 1.1**
    - 임의 구조·필드의 slide dict와 `is_cover` 불리언에 대해 재사용 함수 `_classify_slide_role`이 정확히
      `{cover, section, structural, content, visual}` 중 하나를 반환함을 100+ iteration 검증
    - `./venv/bin/python -m pytest scripts/test_pptx_hybrid_render_plan_pbt.py -p no:cacheprovider -q`

  - [x]* 2.3 모호 입력 content 폴백 property test (같은 plan_pbt 파일에 추가)
    - **Property 2: 모호 입력의 content 결정론 폴백**
    - **Validates: Requirements 1.8**
    - 구조/비주얼 신호가 없거나 분류 중 예외를 유발하는 slide dict에 대해 최종 role이 결정론적으로 `content`이고
      동일 입력이 항상 동일 결과를 냄을 검증

  - [x]* 2.4 풀블리드 라우팅 property test (같은 plan_pbt 파일에 추가)
    - **Property 3: 풀블리드 라우팅 규칙**
    - **Validates: Requirements 1.2, 1.5**
    - `role∈{cover,section,visual}` ∧ caller 미지정 ∧ `vertex_enabled==True`에서
      `primary=="VERTEX_FULLBLEED"` ∧ `vertex_slot=="visual"` ∧ primary 단일성 검증

  - [x]* 2.5 Vertex 비활성 편집 폴백 property test (같은 plan_pbt 파일에 추가)
    - **Property 4: Vertex 비활성 시 편집 경로 폴백**
    - **Validates: Requirements 1.6, 1.5**
    - `role∈{cover,section,visual}` ∧ `vertex_enabled==False`에서 `editable==True` ∧
      `primary ∈ {HTML_EDITABLE(html on), NATIVE_EDITABLE(html off)}` 검증

  - [x]* 2.6 content 라우팅 property test (같은 plan_pbt 파일에 추가)
    - **Property 5: content 라우팅 규칙**
    - **Validates: Requirements 1.3, 1.5**
    - `role=="content"` caller-미지정에서 모든 `vertex_enabled`/`html_enabled` 조합에 대해
      `primary=="NATIVE_EDITABLE"` ∧ `editable==True` ∧ primary 단일성 검증

  - [x]* 2.7 structural 라우팅 + backdrop property test (같은 plan_pbt 파일에 추가)
    - **Property 6: structural 라우팅 + 손실-0 backdrop**
    - **Validates: Requirements 1.4, 1.5, 4.5**
    - `role=="structural"`에서 `primary=="NATIVE_SHAPES"`, `has_vertex_image`이면
      `vertex_slot=="backdrop"` 아니면 `"none"` 검증

- [x] 3. Checkpoint — 순수 결정 함수 테스트 통과 확인
  - `scripts/test_pptx_hybrid_render_flag_pbt.py`, `scripts/test_pptx_hybrid_render_plan_pbt.py`를 헤르메틱하게
    실행해 Property 1–6, 20이 모두 통과하는지 확인한다
  - 모든 테스트가 통과하는지 확인하고, 의문이 생기면 사용자에게 질문한다

- [x] 4. Vertex 프롬프트 빌더 `_build_fullbleed_vertex_prompt` 구현

  - [x] 4.1 `_build_fullbleed_vertex_prompt(role, title, bullets, style_profile)` 순수 함수 추가
    - 디스크에서 `_gen_vertex_slide`(`server.py:5094` 부근) 인라인 프롬프트 문자열 위치를 `grep -n`으로 확인한 뒤,
      `(prompt, negative_prompt)`를 반환하는 순수 함수를 추가한다 (풀블리드 대상 = cover/section/visual)
    - 역할별 서로 다른 프롬프트 본문(cover/section/visual), 문말 `16:9` 명시
    - 항상 길이 ≥ 1의 비어 있지 않은 negative_prompt 반환, `text`/`words`/`letters`/`watermark` 억제 용어 포함
    - `_build_palette(style_profile)` 결과에서 primary/secondary 색을 **고정 키 순서**로 결정론적 색상 표현에
      삽입(팔레트 None이면 결정론적 기본 표현 폴백) — dict 순회/난수/타임스탬프 비의존, 바이트 결정성
    - LLM 호출 없음(순수 문자열 조립). 편집 후 앵커 count==1 → `py_compile` → `get_diagnostics` 0 에러
    - _Requirements: 3.2, 3.3, 3.4, 3.6_

  - [x]* 4.2 no-text negative prompt property test (`scripts/test_pptx_hybrid_render_prompt_pbt.py`)
    - **Property 10: 프롬프트 빌더의 no-text negative prompt**
    - **Validates: Requirements 3.2**
    - `role∈{cover,section,visual}` × 임의 title/bullets/style_profile에서 negative_prompt 길이 ≥ 1 및
      `text`/`words`/`letters`/`watermark` 억제 용어 포함을 100+ iteration 검증
    - `./venv/bin/python -m pytest scripts/test_pptx_hybrid_render_prompt_pbt.py -p no:cacheprovider -q`

  - [x]* 4.3 역할별 프롬프트 구별성 property test (같은 prompt_pbt 파일에 추가)
    - **Property 11: 역할별 프롬프트 구별성**
    - **Validates: Requirements 3.3**
    - 서로 다른 두 풀블리드 role(둘 다 {cover,section,visual}) + 동일 title/bullets/style_profile에서 prompt
      문자열이 서로 다름을 검증

  - [x]* 4.4 프롬프트 바이트 결정성 property test (같은 prompt_pbt 파일에 추가)
    - **Property 12: 프롬프트 빌더의 바이트 결정성**
    - **Validates: Requirements 3.4, 3.6**
    - 동일 `(role, title, bullets, style_profile)` 반복 호출이 라이브 Vertex 없이 바이트 단위로 동일한
      `(prompt, negative_prompt)`(팔레트/색상 표현 포함)를 산출함을 검증

  - [x]* 4.5 프롬프트 문자열 스냅샷 단위 테스트 (같은 prompt_pbt 파일에 추가)
    - cover/section/visual 각 역할의 대표 입력에 대한 구체 프롬프트 문자열 스냅샷을 고정하고, 팔레트 None 폴백
      경로의 기본 색상 표현을 단언한다 (example/edge)
    - _Requirements: 3.2, 3.3_

- [x] 5. content 편집 렌더러 + 바운디드 Image_Slot 구현

  - [x] 5.1 `slide_templates.py` content 레이아웃에 바운디드 Image_Slot 추가
    - 디스크에서 `render_layout`/content 계열 레이아웃(two_column/objective_detail 등) 정의를 `grep -n`으로 확인
    - 콘텐츠 레이아웃이 슬라이드 전체보다 작은 **바운디드 이미지 필드**(예: 우측 컬럼/이미지 컬럼)를 수용하도록
      선택적 필드를 추가한다. 값이 없으면 기존 그라디언트/플레이스홀더로 폴백(기존 호출 바이트 호환)
    - 슬라이드 전체(13.333in×7.5in)를 덮는 풀블리드 PICTURE는 생성하지 않는다
    - 편집 후 `py_compile` → `get_diagnostics` 0 에러
    - _Requirements: 2.3, 2.4_

  - [x] 5.2 `_render_content_editable(slide, prs, data, tokens, hero_rel, palette)` 조립 함수 추가
    - `ai_engine/server.py`(또는 얇은 래퍼)에 content 슬라이드를 `native_layout_renderer.render_native_layout`
      편집 경로로 렌더하는 조립 함수를 추가한다 — HTML 전역 on이어도 풀블리드 PNG 바이크를 **우회**
    - `hero_rel`이 있으면 바운디드 Image_Slot에 `add_picture(rel, left, top, width, height)`로 합성
      (width/height < 슬라이드 전체 → 풀블리드 PICTURE 아님)
    - 슬롯 미지원 레이아웃이면 이미지를 **바운디드 on-slide 레이어**로 보존(back-most, 여전히 비풀블리드) —
      폐기하지 않음. `add_picture` 실패 시 예외 전파 없이 보존 폴백
    - `maybe_add_decorative_background`(풀블리드 장식 배경)는 content 경로에서 사용 금지
    - 디스크 앵커 count==1 확인 → `py_compile` → `get_diagnostics` 0 에러
    - _Requirements: 2.1, 2.2, 2.3, 2.4_

  - [x]* 5.3 content 편집 가능성 property test (`scripts/test_pptx_hybrid_render_content_editable_pbt.py`)
    - **Property 7: content 슬라이드는 항상 편집 가능**
    - **Validates: Requirements 2.1, 2.2**
    - 헤르메틱(Vertex 비활성)하게 content 슬라이드를 렌더한 실제 .pptx에서 html_enabled 값과 무관하게 편집 가능
      텍스트 run 개수 ≥ 1 ∧ 풀블리드 PICTURE(13.333in×7.5in) 개수 == 0 을 100+ iteration 검증
    - `./venv/bin/python -m pytest scripts/test_pptx_hybrid_render_content_editable_pbt.py -p no:cacheprovider -q`

  - [x]* 5.4 content 히어로 바운디드 합성·보존 property test (같은 content_editable 파일에 추가)
    - **Property 8: content 히어로의 바운디드 합성·보존**
    - **Validates: Requirements 2.3, 2.4**
    - content 슬라이드 + 유효 히어로 rel에서 보존/합성 이미지 개수 ≥ 1 ∧ 어떤 이미지도 풀블리드 PICTURE가
      아님(바운디드 슬롯 또는 바운디드 on-slide 레이어), 슬롯 미지원 레이아웃에서도 이미지 미폐기를 검증

  - [x]* 5.5 content 밀도·스타일 감사 property test (같은 content_editable 파일에 추가)
    - **Property 9: content 산출물 밀도·스타일 감사 통과**
    - **Validates: Requirements 2.5, 5.3**
    - 헤르메틱 content 렌더 산출물과 tokens에 대해 `scripts/audit_pptx_native_density.py`의
      `audit_native_density(pptx_path, tokens)`가 `AuditReport.passed == True` ∧ `failures == []`
      (비텍스트 시각 요소 ≥ 2 및 5개 스타일 품질 검사 통과 포함)임을 검증

- [x] 6. Checkpoint — 프롬프트 빌더 + content 렌더러 테스트 통과 확인
  - `scripts/test_pptx_hybrid_render_prompt_pbt.py`, `scripts/test_pptx_hybrid_render_content_editable_pbt.py`를
    헤르메틱하게 실행해 Property 7–12가 모두 통과하는지 확인한다
  - 모든 테스트가 통과하는지 확인하고, 의문이 생기면 사용자에게 질문한다

- [x] 7. `_tool_generate_pptx` 하이브리드 라우팅 와이어링 (opt-in 게이팅)

  - [x] 7.1 Feature Flag 게이트를 `_html_enabled` 결정 직후에 배선
    - 디스크에서 `_html_enabled` 결정 블록(`server.py:4691` 부근)을 `grep -n`(count==1)으로 확인한 뒤
      `_hybrid_on = _hybrid_render_enabled(os.environ.get("AE_HYBRID_RENDER", ""))`를 정확히 1회 읽도록 추가
    - `_hybrid_on == False`이면 이후 하이브리드 분기가 전부 no-op이 되도록 배선(기존 제어흐름·출력 불변)
    - 편집 후 앵커 count==1 → `py_compile` → `get_diagnostics` 0 에러
    - _Requirements: 6.1, 6.2, 6.4, 6.5_

  - [x] 7.2 역할 기반 주 렌더러 선택 배선 (`_select_hybrid_render_plan` 연동)
    - 디스크에서 슬라이드 루프의 `_classify_slide_role` → `_select_render_plan` seam(`server.py:5519~5560` 부근)을
      확인한 뒤, `_hybrid_on == True`일 때만 caller 미지정 슬라이드에 대해 `_classify_slide_role`(예외 시
      `role="content"` 확정) → `_select_hybrid_render_plan`으로 주 렌더러를 결정하는 얇은 레이어를 덧댄다
    - caller가 `imageFile`/`slideBackground`를 지정한 슬라이드는 항상 기존 `_select_render_plan` 경로에 위임
      (caller 우선순위 보존, Vertex 사전생성 skip)
    - 손실-0 최종 검증은 기존 `_select_render_plan`에 위임 유지(`has_vertex_image ⇒ slot≠none`)
    - 편집 후 앵커 count==1 → `py_compile` → `get_diagnostics` 0 에러
    - _Requirements: 1.1, 1.2, 1.3, 1.4, 1.5, 1.6, 4.1, 4.2, 4.5_

  - [x] 7.3 `_gen_vertex_slide` 인라인 프롬프트를 `_build_fullbleed_vertex_prompt`로 대체
    - 디스크에서 `_gen_vertex_slide`(`server.py:5094` 부근) 인라인 프롬프트 문자열 앵커를 확인(count==1)한 뒤,
      풀블리드 대상(cover/section/visual)의 프롬프트/negative_prompt를 빌더 호출로 대체
    - 호출부는 `aspect_ratio="16:9"`, `model_class="image_generation_high_quality"`로 정확히 1회 호출
    - 편집 후 앵커 count==1 → `py_compile` → `get_diagnostics` 0 에러
    - _Requirements: 3.1, 3.5, 3.7_

  - [x] 7.4 content 슬라이드를 편집 경로로 배선 (`_render_content_editable` 연동)
    - `_hybrid_on == True` ∧ `role=="content"`일 때 `_generate_html_slide_for_section`의 풀블리드 바이크를
      우회하고 `_render_content_editable`로 라우팅. Vertex 히어로(`_vertex_pre[i]`)가 있으면 바운디드 슬롯 합성,
      없으면 네이티브 고밀도만
    - structural은 `NATIVE_SHAPES` 유지 + Vertex 이미지 존재 시 backdrop 보존(래스터화 0)
    - 후처리로 `_strip_text_over_fullbleed` 등 기존 겹침 0 경로를 그대로 통과
    - 편집 후 앵커 count==1 → `py_compile` → `get_diagnostics` 0 에러
    - _Requirements: 2.1, 2.2, 2.3, 2.4, 4.5, 4.6_

  - [x]* 7.5 Vertex generate 호출 계약 property test (`scripts/test_pptx_hybrid_render_wiring_pbt.py`)
    - **Property 13: Vertex generate 호출 계약**
    - **Validates: Requirements 3.1, 3.5**
    - 풀블리드 대상 슬라이드(caller 미지정, role∈{cover,section,visual}, vertex_enabled)에서 목 스파이로
      `VertexImageClient.generate`가 `aspect_ratio=="16:9"` ∧ `model_class=="image_generation_high_quality"`로
      정확히 1회 호출됨을 검증 (게이트웨이/HTML 렌더/Vertex 모두 목, 네트워크 0)
    - `./venv/bin/python -m pytest scripts/test_pptx_hybrid_render_wiring_pbt.py -p no:cacheprovider -q`

  - [x]* 7.6 Flag off 결정성 property test (같은 wiring 파일에 추가)
    - **Property 21: Flag off 결정성 (기존 동작 보존)**
    - **Validates: Requirements 6.4, 1.7**
    - `AE_HYBRID_RENDER` 비활성에서 동일 입력·동일 seed/설정 반복 렌더가 구조적으로 동등한(슬라이드 수·도형·
      텍스트·이미지 배치 동일) 결정론적 산출물을 내고 하이브리드 라우팅 분기(`_select_hybrid_render_plan`)가
      호출되지 않음(스파이)을 검증

  - [x]* 7.7 산출물 겹침·편집성 감사 property test (같은 wiring 파일에 추가)
    - **Property 19: 산출물 겹침·편집성 감사 통과**
    - **Validates: Requirements 5.1**
    - 하이브리드 렌더(헤르메틱)로 생성한 .pptx에 대해 `scripts/audit_pptx_overlap.py`의 `audit(path)`가 판정한
      "텍스트·이미지 겹침 슬라이드" 목록 개수 == 0 ∧ "편집 불가(래스터) 의심 슬라이드" 목록 개수 == 0 을 검증

- [x] 8. 회귀 보존 프로퍼티 — 기존 PBT 재사용·확장 (중복 구현 금지)

  - [x]* 8.1 손실-0 불변식 property test 확장 (`scripts/test_pptx_quality_vertex_images_fix_pbt.py`)
    - **Property 14: 손실-0 불변식 보존**
    - **Validates: Requirements 4.1**
    - 기존 fix_pbt(Property 3)를 확장해 `_select_hybrid_render_plan`이 위임받는 경우에도 `has_vertex_image==True`
      이면 `vertex_slot != "none"`이며 성공 생성 이미지 폐기 개수 0임을 검증. 새 파일 생성 금지
    - `./venv/bin/python -m pytest scripts/test_pptx_quality_vertex_images_fix_pbt.py -p no:cacheprovider -q`

  - [x]* 8.2 caller 우선순위 보존 property test 확장 (`scripts/test_pptx_quality_vertex_images_preservation_pbt.py`)
    - **Property 15: caller 지정 미디어 우선순위 보존**
    - **Validates: Requirements 4.2**
    - 기존 preservation_pbt(pres5)를 확장해 caller가 `imageFile`/`slideBackground`를 지정한 슬라이드는 하이브리드
      라우팅이 덮어쓰지 않고 기존 `_select_render_plan`에 위임되며 Vertex 사전생성이 스킵됨을 검증

  - [x]* 8.3 Vertex 비활성/실패 폴백 손실-0 property test 확장 (같은 preservation_pbt 파일)
    - **Property 16: Vertex 비활성/실패 폴백의 손실-0**
    - **Validates: Requirements 4.3, 6.3**
    - 기존 preservation_pbt(pres3)를 확장해 `vertex_enabled==False` 또는 `generate` 실패에서 콘텐츠 손실 0으로
      편집 가능 네이티브/HTML 폴백으로 전환하고 폴백 발생이 표시됨을 검증

  - [x]* 8.4 게이트웨이 제약 property test 확장 (같은 preservation_pbt 파일)
    - **Property 17: 게이트웨이 제약 — 이미지 외 Vertex 미호출**
    - **Validates: Requirements 3.7, 4.4, 6.3**
    - 기존 preservation_pbt(prop5)를 확장해 라우팅 결정·프롬프트 빌드·operation JSON 생성 등 이미지 외 실행에서
      `VertexImageClient` 호출 개수 0 ∧ 모든 LLM 호출이 `_get_gw`(Bedrock Gateway) 경유임을 목 스파이로 검증

  - [x]* 8.5 겹침 0 산출물 property test 확장 (`scripts/test_pptx_overlay_collision_preservation_pbt.py`)
    - **Property 18: 겹침 0 산출물**
    - **Validates: Requirements 4.6, 5.2**
    - 기존 overlay collision / fullbleed_native_overlay 보존 PBT를 확장해 하이브리드 렌더 슬라이드의 도형-도형 및
      텍스트박스-텍스트박스 겹침 면적 0 EMU이며 `scripts/audit_pptx_textbox_overlap.py`의 `main(path)`가 판정한
      0.05in² 초과 텍스트박스 쌍 개수 == 0 임을 검증
    - `./venv/bin/python -m pytest scripts/test_pptx_overlay_collision_preservation_pbt.py -p no:cacheprovider -q`

- [x] 9. Final Checkpoint — 전체 테스트 + 헤르메틱 E2E 감사
  - **신규 하이브리드 테스트** 전체를 헤르메틱하게 실행한다:
    `./venv/bin/python -m pytest scripts/test_pptx_hybrid_render_flag_pbt.py scripts/test_pptx_hybrid_render_plan_pbt.py scripts/test_pptx_hybrid_render_prompt_pbt.py scripts/test_pptx_hybrid_render_content_editable_pbt.py scripts/test_pptx_hybrid_render_wiring_pbt.py -p no:cacheprovider -q`
    → Property 1–13, 19, 20, 21 통과 확인
  - **회귀 스위트** 재실행(R4 보존, 네트워크 0):
    `scripts/test_pptx_quality_vertex_images_*`, `scripts/test_pptx_overlay_collision_*`,
    `scripts/test_media_output_quality_*` → 회귀 없음 + Property 14/15/16/17/18 통과 확인
  - **실제 헤르메틱 E2E 생성**: `AE_HYBRID_RENDER=1` ∧ Vertex 비활성(`AE_ENABLE_VERTEX_IMAGE` 미설정)으로
    표지·고밀도 content·structural·visual 혼합 덱의 실제 .pptx를 스크립트로 생성(인라인 `python -c` 금지)한 뒤,
    다음 감사를 실행한다:
    - `scripts/audit_pptx_overlap.py`의 `audit(path)` → 두 목록 길이 0 (Property 19)
    - `scripts/audit_pptx_textbox_overlap.py`의 `main(path)` → 초과 쌍 0 (Property 18)
    - `scripts/audit_pptx_native_density.py`의 `audit_native_density(pptx_path, tokens)` →
      `passed == True`, `failures == []` (Property 9)
  - **자격증명 전용 속성 명시**: 실제 Vertex 렌더의 시각 품질(젠스파크급 초고품질, 텍스트 없는 배경의 실제 렌더
    결과 = Requirement 3의 시각적 우수성)은 **헤르메틱하게 증명 불가**하며, 사용자의 자격증명 환경
    (`AE_ENABLE_VERTEX_IMAGE=1` + `GOOGLE_APPLICATION_CREDENTIALS`)에서 `scripts/visual_comparator.py` /
    `scripts/demo_*`로 수동·시각 비교로만 측정됨을 보고한다. 헤르메틱 게이트에서는 프롬프트 계약(16:9,
    model_class, no-text negative)을 스파이로만 검증한다
  - 모든 테스트·감사가 통과하는지 확인하고, 의문이 생기면 사용자에게 질문한다

## Notes

- 태스크 `*` 표시는 선택(optional) 테스트 서브태스크(단위/프로퍼티/통합)로, 빠른 MVP에서는 건너뛸 수 있으나
  본 스펙의 정확성 증명 핵심이므로 최종 Checkpoint(태스크 9) 전 실행을 강력히 권장한다.
- **헤르메틱 원칙**: 모든 테스트는 네트워크 호출 0. Vertex는 `get_vertex_image_client` disabled 스텁 +
  `generate` 목, Bedrock Gateway는 `_get_gw` 목, HTML→PNG 렌더는 목으로 고정한다.
- **게이트웨이 제약(steering 준수)**: LLM/operation JSON 생성은 Bedrock Gateway(`_get_gw`) 경유만 유지하며,
  Vertex는 이미지 생성 경로에서만 호출된다(gateway.md 이미지 예외 조항). Property 17이 이를 검증한다.
- **손실-0 불변식**: 성공 생성된 Vertex 이미지(`_vertex_pre[i]`)는 어떤 렌더 경로에서도 폐기되지 않는다 —
  풀블리드 visual / 바운디드 hero / structural backdrop 중 하나로 최소 보존된다. Property 14가 전역 검증한다.
- **loss-0 최종 게이트 위임**: `_select_hybrid_render_plan`은 역할 기반 주 렌더러만 배정하고, 손실-0 최종
  검증(`has_vertex_image ⇒ slot≠none`)은 기존 `_select_render_plan`에 위임한다(회귀 0).
- **STALE 버퍼 주의**: `ai_engine/server.py`는 에디터 버퍼가 오래됐을 수 있으므로 편집 전 디스크를
  `sed`/`grep`으로 확인하고 앵커 count==1을 단언한 뒤 디스크 기준으로 패치한다. 편집 직후 `py_compile` +
  `get_diagnostics` 0 에러를 확인한다.
- **기본 OFF 불변**: `AE_HYBRID_RENDER` 미설정/`"0"`에서는 산출 .pptx가 하이브리드 이전과 바이트 동일해야
  한다. Property 21이 flag off 결정성을 검증한다.
- **회귀 방지 스위트**(태스크 9에서 재실행): `test_pptx_quality_vertex_images_*`,
  `test_pptx_overlay_collision_*`, `test_media_output_quality_*`.
- **헤르메틱 vs 자격증명 구분(R5.4)**: 라우팅·합성·보존·감사(Property 1–21의 헤르메틱 부분)는 실측 감사로
  증명하고, 실제 Vertex 시각 품질은 자격증명 환경에서만 측정한다.

## Task Dependency Graph

```json
{
  "waves": [
    { "id": 0, "tasks": ["1.1"] },
    { "id": 1, "tasks": ["1.2", "1.3", "2.1"] },
    { "id": 2, "tasks": ["2.2", "2.3", "2.4", "2.5", "2.6", "2.7", "4.1"] },
    { "id": 3, "tasks": ["4.2", "4.3", "4.4", "4.5", "5.1"] },
    { "id": 4, "tasks": ["5.2"] },
    { "id": 5, "tasks": ["5.3", "5.4", "5.5", "7.1"] },
    { "id": 6, "tasks": ["7.2"] },
    { "id": 7, "tasks": ["7.3"] },
    { "id": 8, "tasks": ["7.4"] },
    { "id": 9, "tasks": ["7.5", "7.6", "7.7", "8.1", "8.2", "8.3", "8.4", "8.5"] }
  ]
}
```
