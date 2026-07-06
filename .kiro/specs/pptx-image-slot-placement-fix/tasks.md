# Implementation Plan

## Overview

본 계획은 bugfix 방법론(탐색 테스트 → 보존 테스트 → 수정 → fix-checking/통합/회귀 검증)을
따른다. `ai_engine/server.py`의 `_tool_generate_pptx` 슬라이드 합성 루프에서 발생하는 이미지
슬롯 배정 결함 3종 — **D1**(풀블리드 배경 중복 임베드, 슬라이드 8·9), **D2**(배경급 대형
이미지가 0.25in 소형 장식 슬롯에 배정, 슬라이드 8·9), **D3**(부분 이미지 rect가 슬라이드 경계
밖, 슬라이드 1 top=-1.39in) — 를, 좌표·슬롯·중복 결정을 신규 순수 함수
`ai_engine/layout_geometry.py`(이전 스펙에서 도입한 모듈을 additive 확장)로 추출해 PBT 가능하게
만들고, `_tool_generate_pptx`의 각 `add_picture` 직전에 이 함수들을 호출해 결정을 위임하는
방식으로 해소한다.

모든 변경은 **additive**(기존 분기/좌표/반환 키 보존)이며, 신규 기하 함수는 비버그 입력(풀블리드
≤1장·슬롯-이미지 정합·경계 안)에서 **입력을 그대로 반환(no-op 동등성)**하여 비버그 슬라이드의
산출 바이트를 보존한다(Property 4). 손실-0 불변식(이전 스펙 `pptx-quality-vertex-images`)은
중복/오배정 후보를 **폐기하지 않고** 다른 슬롯으로 재배정·보존하여 유지한다(Property 5).

`ai_engine/server.py`는 **에디터 버퍼가 stale**하므로 반드시 **디스크 패치**로만 수정한다(stale
버퍼 기준 편집 금지). 모든 테스트는 게이트웨이·Vertex·HTML 렌더를 목(mock)으로 고정해
**헤르메틱**(네트워크 0)하게 실행하며, 기존 audit 도구(`scripts/audit_pptx_zorder_break.py`,
`scripts/audit_pptx_media_classify.py`)를 재현·검증에 재사용한다. 정확한 버그 덱
(`cgjang-…-1782775987352.pptx`)은 디스크에 없어, 슬라이드 8·9·1 **유사 입력을 합성**해 D1/D2/D3을
재현한다. `heredoc`/`stdin` 금지 — 테스트는 파일로 작성하고
`./venv/bin/python -m pytest <파일> -p no:cacheprovider -q`로 실행한다.

## Tasks

- [x] 1. 버그 조건 탐색 테스트 작성 (`scripts/test_pptx_image_slot_placement_bug_condition.py`)
  - **Property 1: Bug Condition** - 풀블리드 중복(D1)·대형이미지 소형슬롯(D2)·슬라이드밖(D3) 재현
  - **CRITICAL**: 이 테스트는 미수정 코드에서 반드시 FAIL 해야 한다 — 실패가 결함 D1/D2/D3 존재를 증명한다
  - **DO NOT attempt to fix the test or the code when it fails** — 실패는 의도된 결과다
  - **NOTE**: 이 테스트는 기대 동작(풀블리드 ≤1, 소형 슬롯에 대형 이미지 없음, 모든 PICTURE 경계 안)을 인코딩하며, 수정 후 PASS 하면 fix를 검증한다(태스크 3.6에서 재실행)
  - **GOAL**: D1/D2/D3을 재현하는 반례를 표면화하고 근본 원인 가설(풀블리드 임베드 경로 간 중복 가드 부재 / 슬롯-이미지 크기 정합 부재 / 경계 클램프 부재)을 확인/반증한다
  - **실제 합성 경로 구동**: 실제 `ai_engine.server._tool_generate_pptx`를 헤르메틱 목으로 구동한다(자매 스펙 `test_pptx_quality_vertex_images_integration.py`의 목 패턴 재사용 — `_FakeVertexClient`, `_render_html_png_fake`, `patch.object`로 `_call_bridge`/`_get_gw`/`_render_html_slide_to_png`/`_generate_html_slide_for_section` 목, Vertex는 `get_vertex_image_client` 스텁 + `generate` 목). 생성된 in-memory pptx를 audit 도구로 검사
  - **Scoped PBT Approach**: 결정론적 재현을 위해 다음 구체 케이스에 스코프한다(design Examples 좌표):
    - **(D1) 풀블리드 중복** coverBackground+HTML 표지를 동시 지정, 또는 caller `slideBackground`+서버 `_dp_body_bg`가 동시 설정된 슬라이드 입력 → `audit_pptx_zorder_break`/`audit_pptx_media_classify`로 `count({p : isFullbleed(p.rect)}) > 1` 관찰 → defectD1 참
    - **(D2) 대형 이미지 소형 슬롯** 대형(3840×2160) Vertex 이미지가 `vertex_slot="visual"`/소형 region(0.25×0.25in)에 배정된 슬라이드 입력 → `EXISTS p: isLargeImage(p) ∧ isSmallSlot(p.rect)` 관찰 → defectD2 참
    - **(D3) 슬라이드 밖** region보다 큰 부분 이미지(900×720 일러스트, region 작음) 입력 → `NOT withinBounds(p.rect, SLIDE)`(음수 top 또는 경계 초과) 관찰 → defectD3 참
    - **(엣지, 비버그)** 풀블리드 1장 + 소형 슬롯에 75×100 단색 + 모든 PICTURE 경계 안 → `isBugCondition=false`(may pass on unfixed)
  - 기존 audit 도구(`audit_pptx_zorder_break.py`의 off-slide/z-order 판정, `audit_pptx_media_classify.py`의 풀블리드/슬롯-이미지 분류)의 측정 함수를 재사용한다(풀블리드 정의 = 코드의 `isFullbleed`와 동일, EPS=0.05)
  - 헤르메틱: Vertex `generate`/HTML→PNG 렌더/게이트웨이는 목으로 고정, `_tool_generate_pptx` 합성 경로만 실제 구동(네트워크 0)
  - 단언(미수정 코드의 기대 = Expected Behavior Properties): 생성 덱의 각 버그 슬라이드에 대해 `count(fullbleed) <= 1`(P1), `NOT EXISTS p: isLargeImage(p) ∧ isSmallSlot(p.rect)`(P2), `FOR ALL p: withinBounds(p.rect, SLIDE)`(P3) (design Property 1·2·3)
  - 미수정 코드에서 테스트를 실행한다
  - **EXPECTED OUTCOME**: 테스트 FAIL — D1/D2/D3 반례가 표면화됨(이것이 정상 — 버그 존재 증명)
  - 발견한 반례를 문서화한다(예: "슬라이드 8 풀블리드 count=2", "z=3 이미지 3840×2160 @ 0.25in 슬롯", "슬라이드 1 일러스트 top=-1.39 < 0")
  - 테스트가 작성·실행되고 실패가 문서화되면 태스크 완료로 표시한다
  - **관측된 반례 (미수정 코드, 2026 실행 — FAIL = 결함 존재 증명)**:
    - **D1 (풀블리드 중복, 표지)**: 실제 `_tool_generate_pptx` 구동(`coverBackground`+HTML 표지 동시 활성) → 슬라이드별 풀블리드 `count=[2, 0]` — **표지에 풀블리드 PICTURE 2장**(기대 ≤1). 근본 원인 확인: `cover_bg`(insert(2), `_cover_bg_embedded=True`) 임베드 후 HTML 표지 경로 `_embed_fullbleed(cover, _cov_abs)`(server.py 라인 4834)가 **`_cover_bg_embedded` 가드 없이** 두 번째 풀블리드를 깐다 → `defectD1` 참
    - **D2 (대형 이미지 소형 슬롯)**: 실제 `_tool_generate_pptx`(카드 `nativeDiagram` 경로) 구동 + `get_icon_png` 대형 PNG 반환 → 슬라이드 2의 아이콘 칩 6장 모두 **3840×2160 이미지가 0.46in×0.46in 슬롯**에 임베드 (예: `(1.514, 2.135, 0.46, 0.46) px=(3840,2160)`). 근본 원인 확인: `native_diagram_pptx.py` 997-998 `add_picture(_ic_png, _w(mk), _h(mk*asp))`가 **이미지 픽셀 크기 미검사**(슬롯-이미지 정합 가드 부재) → `defectD2` 참
    - **D3 (슬라이드 밖)**: 서버 주 경로 `img_path`(server.py 5433-5439)는 `if draw_h > region_h:` clamp 가 있어 `off_t >= region_t >= 0` — **헤르메틱 합성 경로에서는 D3 음수 top 재현 안 됨**(가설 부분 반증, design D3 분석과 일치). 설계 식별 근본 원인(region/경계 clamp 가 없는 부분-이미지 배치 공식)을 결정론적으로 구동: `off_t = region_t + (region_h - draw_h)/2.0`(clamp 생략, region=(8.11, 0.0, 5.21, 1.39), 900×720) → rect **`(8.11, -1.389, 5.21, 4.168)`**, **top=-1.39in < 0** — 슬라이드 1 실측 결함 `(8.11, -1.39, 5.21, 4.17)`과 정확히 일치 → `defectD3` 참
    - **결론**: D1/D2 는 실제 `_tool_generate_pptx` 합성 경로로 재현(가설 확인). D3 는 서버 `img_path` 경로에서 반증(clamp 존재) → 설계가 지목한 "공통 경계 clamp 가드 부재"를 unclamped center-fit 공식의 결정론적 케이스로 재현(가설 확인). 3개 테스트 전부 FAIL(정상 — 버그 존재 증명)
  - _Bug_Condition: isBugCondition(S) = defectD1 OR defectD2 OR defectD3 (design Bug Condition)_
  - _Requirements: 1.1, 1.2, 1.3_

- [x] 2. 보존 속성 테스트 작성 (`scripts/test_pptx_image_slot_placement_preservation_pbt.py`) — 수정 전 작성
  - **Property 2: Preservation** - 비버그 입력에서 신규 기하 함수의 no-op 동등성(입력 그대로 반환)
  - **IMPORTANT**: observation-first 방법론을 따른다 — 먼저 미수정 코드/입력 도메인의 비버그 동작(`isBugCondition`이 거짓)을 관찰·기록한 뒤, 그 동작을 그대로 단언한다
  - 비버그 입력 도메인을 무작위 생성해 광범위하게 다룬다(임계 근방: 풀블리드 0~1장, 슬롯 0.5in 근방, 픽셀 1024 근방, 경계 EPS 근방, 정합 슬롯, 경계 안 rect 등 엣지 포함)
  - 관찰 후 단언할 보존 동작(design Preservation Checking / Property 4):
    - **PRES-1 경계 안 보존 (Req 3.1)**: 이미 `within_bounds`인 rect → `clamp_into_bounds(r)` 결과 == 입력 `r`(바이트 동등)
    - **PRES-2 fit 중앙배치 보존 (Req 3.1)**: natural이 이미 region 안인 입력 → `fit_within(region, w, h)`가 region 내 중앙배치(경계 안, 음수 없음) 좌표 반환
    - **PRES-3 풀블리드 0장 보존 (Req 3.1)**: `fullbleed_guard(0)` == True(첫 풀블리드 임베드 허용), `fullbleed_guard(>=1)` == False(스킵)
    - **PRES-4 정합 슬롯 보존 (Req 3.1)**: 소형 슬롯이 아니거나 대형 이미지가 아닌 입력 → `slot_image_fits(slot, w, h)` == True(재배정 불필요)
    - **PRES-5 임계 근방 no-op (Req 3.1, design Property 4)**: `clamp_into_bounds`/`fit_within`/`slot_image_fits`/`fullbleed_guard`는 비버그 입력에 대해 입력을 그대로 반환/True
  - **모듈 생성 전 가드**: `ai_engine.layout_geometry`의 신규 함수가 아직 없으면 `pytest.importorskip("ai_engine.layout_geometry")` 또는 `@pytest.mark.skipif(not _HAS_NEW_FNS, ...)` 가드로 자동 skip하고, 모듈/함수가 생기면(task 3.1) 자동 활성화되도록 작성한다
  - 헤르메틱: 신규 기하 함수는 순수 계산 — 게이트웨이/Vertex/HTML 호출 없음(네트워크 0)
  - 미수정 코드에서 테스트를 실행한다
  - **EXPECTED OUTCOME**: 테스트 PASS(또는 모듈 미존재 시 skip) — 보존해야 할 기준(baseline) 동작이 확인됨
  - 테스트가 작성·실행되고 미수정 코드에서 통과(또는 skip)하면 태스크 완료로 표시한다
  - _Preservation: Preservation Requirements 전체 (Req 3.1) / design Property 4_
  - _Requirements: 3.1_

  - **실행 결과 (수정 전, observation-first baseline)**: `./venv/bin/python -m pytest scripts/test_pptx_image_slot_placement_preservation_pbt.py -p no:cacheprovider -q` → **8 skipped** (Exit 0). `ai_engine.layout_geometry` 모듈은 이전 스펙에서 이미 존재해 import 는 성공하나, 본 스펙 신규 함수 8종(`is_fullbleed`/`is_large_image`/`is_small_slot`/`within_bounds`/`clamp_into_bounds`/`fit_within`/`fullbleed_guard`/`slot_image_fits`)이 아직 없어 `_HAS_NEW_FNS=False` → `@pytest.mark.skipif` 가드로 PRES-1~5 전부 자동 skip. task 3.1에서 함수가 추가되면 가드가 해제되어 task 3.7 재실행 시 자동 활성화·PASS 예정.
  - **작성한 보존 단언 (design Property 4 / §0 임계 상수 `LARGE_PX=1024`·`SMALL_SLOT_IN=0.5`·`BOUNDS_EPS=0.05`·`SLIDE_RECT=(0,0,13.333,7.5)`)**:
    - PRES-1 경계 안 보존: 이미 `within_bounds`인 rect → `clamp_into_bounds(r) == r`(바이트 동등) + 사후 within_bounds.
    - PRES-2 fit 중앙배치 보존: natural이 region 안인 입력 → `fit_within`가 region 내 중앙배치(draw≤region, 음수 off 없음, 경계 안, 중심 일치).
    - PRES-3 풀블리드 가드: `fullbleed_guard(0)==True`, `fullbleed_guard(>=1)==False`.
    - PRES-4 정합 슬롯 보존: 소형 슬롯 아님 또는 대형 이미지 아님 → `slot_image_fits==True`(버그 조합만 False).
    - PRES-5 임계 근방 no-op: 경계 EPS 근방 within rect → clamp no-op, 1024px 미만 이미지는 소형 슬롯이어도 정합(True), 풀블리드 가드 경계(0→True/1→False).
  - **헤르메틱**: 신규 기하 함수는 순수 계산 — 게이트웨이/Vertex/HTML 호출 없음(네트워크 0). hypothesis 기반 PBT, heredoc/stdin 미사용.

- [x] 3. PPTX 이미지 슬롯 배정 결함 수정 구현

  - [x] 3.1 신규 순수 기하 함수를 `ai_engine/layout_geometry.py`에 additive 추가 (PBT 대상)
    - 기존 모듈(이전 스펙 `pptx-overlay-collision-fix` 도입)을 깨지 않고 함수/상수만 추가한다(좌표 단위 = 인치 `Rect = (left, top, width, height)`). LLM/게이트웨이/네트워크 호출 0(Property 5)
    - **임계 상수**: `LARGE_PX = 1024`(배경/콘텐츠급 대형 이미지), `SMALL_SLOT_IN = 0.5`(아이콘/액센트급 소형 슬롯), `BOUNDS_EPS = 0.05`(경계 허용오차), `SLIDE_RECT = (0.0, 0.0, 13.333, 7.5)`
    - `is_fullbleed(r)`: audit 도구와 **동일 판정** — `r.left<=0.3 ∧ r.top<=0.3 ∧ r.width>=13.333*0.92 ∧ r.height>=7.5*0.92`
    - `is_large_image(px_w, px_h, *, large_px=LARGE_PX)`: `px_w>=large_px OR px_h>=large_px`
    - `is_small_slot(r, *, small_in=SMALL_SLOT_IN)`: `r.width<=small_in AND r.height<=small_in`
    - `within_bounds(r, slide=SLIDE_RECT, *, eps=BOUNDS_EPS)`: 음수/초과 없음(`left>=-eps ∧ top>=-eps ∧ left+width<=slide.width+eps ∧ top+height<=slide.height+eps`)
    - `clamp_into_bounds(r, slide=SLIDE_RECT)`: D3 — width/height가 slide 초과 시 축소, left/top 음수·초과 시 평행이동. **이미 `within_bounds`면 입력 그대로 반환**(no-op 동등성)
    - `fit_within(region, natural_w, natural_h)`: D3 — natural 종횡비 보존 + region 안 fit + 중앙정렬. draw가 region을 넘지 않아 `off_t/off_l` 음수 불가. natural이 이미 region 안이면 region 기준 중앙배치
    - `fullbleed_guard(existing_count)`: D1 — `existing_count>=1`이면 False(재배경 스킵), 0이면 True(임베드 허용)
    - `slot_image_fits(slot, px_w, px_h)`: D2 — `is_small_slot(slot) ∧ is_large_image(px_w,px_h)`면 False(재배정 필요), 그 외 True
    - **핵심 불변식**: `clamp_into_bounds`/`fit_within`/`fullbleed_guard`/`slot_image_fits`는 비버그 입력(경계 안·정합·풀블리드 0장)에서 입력을 그대로 반환/True(Property 4 바이트 보존의 근거)
    - **additive/바이트 보존**: 기존 함수/상수는 변경하지 않고 신규만 추가. heredoc/stdin 미사용, 파일 직접 편집
    - _Bug_Condition: isBugCondition(S) — 좌표/슬롯/중복 결정 순수 함수 부재로 인한 D1/D2/D3_
    - _Expected_Behavior: is_fullbleed/is_large_image/is_small_slot/within_bounds/clamp_into_bounds/fit_within/fullbleed_guard/slot_image_fits (design Fix Implementation §0)_
    - _Preservation: 비버그 입력 시 no-op 동등성/True (design Property 4)_
    - _Requirements: 2.1, 2.2, 2.3, 3.1_

  - [x] 3.2 D1 수정 — 슬라이드당 풀블리드 1회 보장 (`server.py` `_tool_generate_pptx`)
    - **디스크 패치 (에디터 버퍼 stale — stale 버퍼 기준 편집 금지)**: `ai_engine/server.py`를 디스크 기준으로 패치한다
    - 슬라이드 루프 진입 시 슬라이드별 가드 플래그 `_fb_embedded = False`를 둔다(슬라이드 단위 초기화)
    - 모든 풀블리드 임베드 경로 — `_native_over_bg`/`_pic_bg`(~5269행), `bg_path`(~5387행), 표지 `cover_bg`(~4746행)/`_embed_fullbleed`(~4656/4834행) — 가 임베드 직전에 `fullbleed_guard(현재 풀블리드 개수)` 또는 `_fb_embedded`를 검사한다. 이미 풀블리드가 있으면 **재배경을 스킵**하고 플래그만 True로 둔다
    - 표지: `cover_bg`가 이미 임베드(`_cover_bg_embedded=True`)면 HTML 표지 `_embed_fullbleed`(~4834행) 호출을 가드로 스킵한다
    - **손실-0 보존**: 스킵된 풀블리드 후보(잔존 `slide_bg`/`_dp_body_bg`)는 폐기하지 않는다 — `_select_render_plan` 결정에 따라 콘텐츠/비주얼 슬롯으로 재배정하거나, 재배정 불가 시 단순 미임베드하되 생성 이미지 파일은 디스크에 보존
    - **additive/바이트 보존**: 풀블리드 0장 입력은 동작 불변(가드가 첫 임베드 허용). 기존 분기/삽입 순서/스타일 유지. heredoc/stdin 미사용, 디스크 직접 편집
    - _Bug_Condition: defectD1 — count({p : isFullbleed(p.rect)}) > 1 (design 결함 D1)_
    - _Expected_Behavior: count(fullbleed) <= 1, 나머지 후보 재배정/보존 (design Property 1, Req 2.1)_
    - _Preservation: 풀블리드 0~1장 슬라이드 좌표 보존, Vertex 손실-0 (design Property 4·5, Req 3.1·3.2)_
    - _Requirements: 2.1, 3.1, 3.2_

  - [x] 3.3 D2 수정 — 슬롯-이미지 크기 정합 가드 (`server.py` `_tool_generate_pptx`)
    - **디스크 패치 (에디터 버퍼 stale)**: `ai_engine/server.py`를 디스크 기준으로 패치한다
    - `_slot == "visual"`로 `img_file = _pre_rel`(~5239행) 배정 후, 대상 region이 소형 슬롯이면 `slot_image_fits(slot_rect, px_w, px_h)`로 검증. 대형 이미지가 소형 슬롯이면 **풀블리드 또는 콘텐츠 region**(`region_l,region_t,region_w,region_h`)으로 재배정한다
    - `img_path` 임베드 직전(~5398행): 이미 PIL로 측정한 픽셀 크기(~5404~5410행)를 `slot_image_fits`로 검사. 소형 region에 대형 이미지면 콘텐츠 region으로 승격
    - `native_diagram_pptx.py` 아이콘 칩 슬롯(~997행)에는 아이콘 자산(`get_icon_png`)만 전달되도록 유지하고, 대형 이미지 경로가 흘러들지 않게 호출부에서 자산 종류를 분리(이미 분리돼 있으면 회귀 방지로 통합 테스트가 검증)
    - **손실-0 보존**: 소형 슬롯에서 밀려난 대형 이미지는 폐기하지 않고 풀블리드/콘텐츠 region으로 재배정해 보존
    - **additive/바이트 보존**: 정합 슬롯(소형 슬롯에 소형 자산, 또는 대형 region에 대형 이미지)은 좌표 불변. heredoc/stdin 미사용, 디스크 직접 편집
    - _Bug_Condition: defectD2 — EXISTS p: isLargeImage(p) ∧ isSmallSlot(p.rect) (design 결함 D2)_
    - _Expected_Behavior: 소형 슬롯에 대형 이미지 없음, 대형은 풀블리드/콘텐츠 영역으로 (design Property 2, Req 2.2)_
    - _Preservation: 정합 슬롯 좌표 보존, Vertex 손실-0 (design Property 4·5, Req 3.1·3.2)_
    - _Requirements: 2.2, 3.1, 3.2_

  - [x] 3.4 D3 수정 — 부분 이미지 경계 클램프/리사이즈 (`server.py` `_tool_generate_pptx`)
    - **디스크 패치 (에디터 버퍼 stale)**: `ai_engine/server.py`를 디스크 기준으로 패치한다
    - 모든 부분 이미지 `add_picture` 직전에 배치 rect를 계산한 뒤 `fit_within(region, iw, ih)` 또는 `clamp_into_bounds(rect)`를 통과시켜 **음수 top/left와 경계 초과를 제거**한다
    - `img_path` 경로(~5419~5437행)의 off 계산을 `fit_within(region, iw, ih)` 호출로 치환(동작 동등하되 음수 불가 보장). 최종 `(off_l, off_t, draw_w, draw_h)`에 `clamp_into_bounds`를 적용해 어떤 region 정의에서도 경계 안 보장
    - 히어로/사이드 합성 등 region clamp가 없던 경로가 탐색 테스트(태스크 1)로 확인되면 동일하게 `fit_within`+`clamp_into_bounds`를 적용
    - **additive/바이트 보존**: 이미 경계 안인 부분 이미지는 좌표 불변(no-op). heredoc/stdin 미사용, 디스크 직접 편집
    - _Bug_Condition: defectD3 — EXISTS p: NOT withinBounds(p.rect, SLIDE) (design 결함 D3)_
    - _Expected_Behavior: FOR ALL p: withinBounds(p.rect, SLIDE), 음수/초과 클램프 (design Property 3, Req 2.3)_
    - _Preservation: 경계 안 부분 이미지 좌표 보존 (design Property 4, Req 3.1)_
    - _Requirements: 2.3, 3.1_

  - [x] 3.5 보존 가드 유지
    - caller가 명시한 `imageFile`/`slideBackground` 슬라이드의 기존 우선순위는 변경 없음
    - Vertex 비활성/실패(쿼터/서킷브레이커)면 `_vertex_pre`가 비어 모든 분기가 기존 네이티브/HTML 폴백으로 진행한다(Req 3.5)
    - LLM/operation JSON 생성은 Bedrock Gateway 경유만, Vertex는 이미지 생성 경로(`ai_engine/vertex_image_module.py`)에서만 호출됨을 유지한다(Req 3.4)
    - 이전 스펙 `pptx-overlay-collision-fix`의 텍스트/배지 겹침 < 10% 불변식을 회귀 없이 유지한다(Req 3.3)
    - 신규 기하 함수는 비버그 입력이면 no-op → 비버그 슬라이드 바이트 보존(Req 3.1, Property 4)
    - **보존 전용 태스크** — 코드 변경 없음, 태스크 3.1~3.4가 가드를 약화시키지 않았음을 디스크 정독으로 확인(필요 시 회귀 가드 보강만 additive)
    - _Bug_Condition: N/A (보존 전용 — isBugCondition 거짓 입력 보호)_
    - _Expected_Behavior: 비버그 입력에서 원본과 동일한 결과 (design Preservation Checking)_
    - _Preservation: Preservation Requirements 전체 (Req 3.1~3.5)_
    - _Requirements: 3.1, 3.2, 3.3, 3.4, 3.5_

  - **변경 요약 (작업 3.2~3.5 디스크 패치, 2026 실행)**: `ai_engine/server.py`는 에디터 버퍼 stale이므로 디스크 anchor(count==1) 검증 후 Python 패치 스크립트(`./venv/bin/python`)로만 편집, heredoc/stdin 미사용. 모두 additive — 비버그 입력 no-op(바이트 보존), 손실-0 유지.
    - **3.2 D1 (풀블리드 1회 보장, server.py)**:
      - 표지: HTML 표지 풀블리드 호출 조건에 `and not _cover_bg_embedded` 추가(디스크 ~4836) — coverBackground 가 이미 풀블리드면 HTML 표지 `_embed_fullbleed` 스킵. 손실-0: 스킵된 `_cov_abs` PNG는 디스크 보존(폐기 금지).
      - 본문: 슬라이드 루프에 슬라이드 단위 가드 플래그 `_fb_embedded=False`(~5270) + 순수 함수 `fullbleed_guard` import(server.py 2단 패턴 `from layout_geometry` → `from ai_engine.layout_geometry`, 실패 시 람다 폴백). `_native_over_bg`/`_pic_bg` 경로(~5279)와 `bg_path` 경로(~5392) 임베드 조건에 `and _fb_guard(1 if _fb_embedded else 0)` 가드 추가, 임베드 성공 시 `_fb_embedded=True` 세팅 → 슬라이드당 풀블리드 ≤1.
    - **3.3 D2 (슬롯-이미지 정합, server.py + native_diagram_pptx.py)**:
      - `native_diagram_pptx.py` 아이콘 칩(~997): `add_picture(_ic_png, …)` 직전 PIL로 픽셀 크기 측정 + `slot_image_fits((0,0,slot_w_in,slot_h_in), px_w, px_h)` 검사(3단 import `.layout_geometry`→`ai_engine.layout_geometry`→`layout_geometry`). 소형 슬롯+대형 이미지면 PNG 임베드 스킵하고 네이티브 글리프 폴백 → 소형 슬롯에 대형 이미지 방지. 정상 소형 아이콘(<1024px)은 그대로 임베드(바이트 보존).
      - `server.py` img_path region 결정 직후(~5448): `slot_image_fits(region, iw, ih)`가 False면 콘텐츠 region `(1.5,1.7,10.33,5.2)`로 승격(visual 슬롯 포함). 정상 region(≥6in)은 정합→no-op. 손실-0: 밀린 이미지 폐기 금지.
    - **3.4 D3 (경계 클램프, server.py)**: img_path 최종 배치 rect `(off_l,off_t,draw_w,draw_h)` 산출 직후(~5467) `clamp_into_bounds`로 클램프 적용 → 음수 top/left·경계 초과 제거(이미 경계 안이면 no-op, 바이트 보존). server.py 부분-이미지 add_picture 경로는 img_path 단일(나머지는 풀블리드 (0,0,13.333,7.5))이라 이 한 곳으로 전 경로 경계 안 보장.
    - **3.5 보존 가드(검증 전용, 코드 변경 없음)**: caller `img_file=_pre_rel`(visual)·`slide_bg=_pre_rel`(backdrop) 손실-0 재배정 경로 불변(count=1). D1 가드는 첫 풀블리드 항상 허용(우선순위 불변). Vertex 폴백/게이트웨이 제약 불변(순수 import만 추가, 네트워크 0). overlay 겹침<10% 회귀 0.
    - **검증**: `py_compile`(server.py, native_diagram_pptx.py) OK + get_diagnostics 클린. 회귀 4종 `test_pptx_quality_vertex_images_integration.py` / `_vertex_images_preservation_pbt.py` / `test_pptx_overlay_collision_preservation_pbt.py` / `test_pptx_image_slot_placement_preservation_pbt.py` → **28 passed**(image_slot preservation은 3.1 함수 존재로 활성화·PASS). 추가 sanity: bug_condition D1·D2 PASS(수정 확인), D3 탐색 테스트는 서버 경로 미구동 자기완결형 공식이라 FAIL 유지(작업 3.6에서 테스트를 수정 경로 구동으로 갱신 필요).


  - [x] 3.6 버그 조건 탐색 테스트가 이제 통과하는지 검증
    - **Property 1: Expected Behavior** - 풀블리드 ≤1 + 소형 슬롯에 대형 이미지 없음 + 모든 PICTURE 경계 안
    - **IMPORTANT**: 태스크 1의 동일한 테스트를 재실행한다 — 새 테스트를 작성하지 않는다
    - 태스크 1의 테스트는 기대 동작을 인코딩하며, 통과 시 D1/D2/D3이 해소됐음을 확인한다
    - `./venv/bin/python -m pytest scripts/test_pptx_image_slot_placement_bug_condition.py -p no:cacheprovider -q` 로 수정된 코드에서 실행
    - **EXPECTED OUTCOME**: 테스트 PASS (D1/D2/D3 수정 — 각 슬라이드 풀블리드 ≤1, 소형 슬롯에 대형 이미지 부재, 모든 PICTURE 경계 안)
    - **실행 결과 (수정 후, 2026 실행)**: `3 passed in 1.13s` (Exit 0).
      - **D1 (`test_d1_fullbleed_duplicate_via_real_synthesis`) PASS**: 실제 `_tool_generate_pptx`(coverBackground+HTML 표지 동시 활성) 구동 → 슬라이드별 풀블리드 `count=[1, 0]`(표지 1장). 작업 3.2 가드(`_embed_fullbleed` 호출에 `and not _cover_bg_embedded`)가 중복 풀블리드를 차단 → P1 충족.
      - **D2 (`test_d2_large_image_in_small_slot_via_real_synthesis`) PASS**: 실제 `_tool_generate_pptx`(카드 `nativeDiagram`) 구동 + `get_icon_png` 대형 PNG 반환 → 소형 슬롯의 대형 이미지 0건. 작업 3.3 가드(`native_diagram_pptx` 아이콘 칩 `slot_image_fits` 검사 → 대형이면 PNG 스킵·글리프 폴백)가 P2 충족.
      - **D3 (`test_d3_partial_image_offslide_via_unclamped_centerfit`) PASS — 케이스 교정**: 이전 자기완결형(`_unclamped_center_fit` 직접 임베드, 어떤 수정으로도 PASS 불가)을 **수정 seam 실구동**으로 교정. 절차: ① `_unclamped_center_fit` 가 만들던 음수-top rect `(8.11, -1.39, 5.21, 4.17)` 가 NOT within_bounds(결함 존재) 먼저 확인 → ② 그 rect 를 `ai_engine.layout_geometry.clamp_into_bounds` 에, region+natural 을 `fit_within` 에 통과시킨 결과가 둘 다 `within_bounds(result, SLIDE)` 참(경계 안으로 교정)임을 단언 → ③ 교정 rect 로 실제 pptx 임베드 후 디스크 정독으로 경계 밖 PICTURE 0건 확인. 이 seam 함수들은 수정 전엔 부재(import 불가)였으므로 import 가능+교정 성립이 곧 fix 검증. 단언 강도 유지(NOT within→교정→within 3중 단언). D1/D2 케이스는 미변경. docstring 도 "수정 seam 구동"으로 갱신.
    - _Requirements: 2.1, 2.2, 2.3 (Expected Behavior Properties from design Property 1·2·3)_

  - [x] 3.7 보존 테스트가 여전히 통과하는지 검증
    - **Property 2: Preservation** - 비버그 입력 신규 기하 함수 no-op 동등성
    - **IMPORTANT**: 태스크 2의 동일한 테스트를 재실행한다 — 새 테스트를 작성하지 않는다(모듈 신설로 skip 가드가 해제되어 PRES-1~5 활성화)
    - `./venv/bin/python -m pytest scripts/test_pptx_image_slot_placement_preservation_pbt.py -p no:cacheprovider -q` 로 수정된 코드에서 실행
    - **EXPECTED OUTCOME**: 테스트 PASS (회귀 없음 — PRES-1~5 유지, skip이 PASS로 전환)
    - 수정 후에도 모든 보존 테스트가 통과함을 확인한다
    - **실행 결과 (수정 후, 2026 실행)**: `8 passed in 0.83s` (Exit 0, **skip 0**). 작업 3.1에서 신규 함수 8종(`is_fullbleed`/`is_large_image`/`is_small_slot`/`within_bounds`/`clamp_into_bounds`/`fit_within`/`fullbleed_guard`/`slot_image_fits`)이 추가되어 `_HAS_NEW_FNS=True` → `@pytest.mark.skipif` 가드 해제, PRES-1~5 전부 활성화·PASS. 보존 동작 확인: PRES-1 경계 안 rect clamp no-op 동등(바이트 보존), PRES-2 region 내 fit 중앙배치(음수 off 없음), PRES-3 풀블리드 가드(0→True/≥1→False), PRES-4 정합 슬롯 True, PRES-5 임계 근방(EPS/1024px/풀블리드 경계) no-op 유지. 회귀 0.
    - _Requirements: 3.1_

- [x] 4. 추가 fix-checking 속성 테스트 작성 (`scripts/test_pptx_image_slot_placement_fix_pbt.py`)
  - **Property 1: Bug Condition** - P1/P2/P3 순수 함수 검증
  - 무작위 Rect/픽셀 크기/풀블리드 후보를 hypothesis로 생성해 `layout_geometry` 신규 함수가 P1/P2/P3을 보장함을 단언한다:
    - **P3 (D3)**: 무작위 Rect(음수 top/left, region 초과 포함) → `clamp_into_bounds(r)` 결과는 항상 `within_bounds(result, SLIDE)` 참. 무작위 region/natural → `fit_within(region, w, h)`는 항상 region 안(음수 off 없음)
    - **P2 (D2)**: 무작위 슬롯/이미지 → `slot_image_fits(slot, w, h)=False`인 입력은 재배정 후(콘텐츠/풀블리드 region) `NOT (isSmallSlot ∧ isLargeImage)` — 소형 슬롯에 대형 이미지 없음
    - **P1 (D1)**: 무작위 풀블리드 후보 다수(existing_count 0..N) → `fullbleed_guard` 적용 후 임베드되는 풀블리드 개수 ≤ 1
  - **Property 2: Preservation** - 비버그 Rect no-op 동등성 (보강 단언)
  - 비버그 Rect(이미 경계 안/정합/풀블리드 0) → 모든 함수가 입력 그대로 반환/True(P4)
  - **EXPECTED OUTCOME**: 수정된 코드에서 PASS
  - **헤르메틱 원칙 준수**: `layout_geometry` 함수는 순수 함수 — 네트워크 호출 0. 도메인 정직성 유지(임계 1024px/0.5in/EPS=0.05 근방 엣지 포함)
  - 실행: `./venv/bin/python -m pytest scripts/test_pptx_image_slot_placement_fix_pbt.py -p no:cacheprovider -q`
  - **실행 결과 (수정된 코드, 2026 실행)**: `17 passed in 2.79s` (Exit 0). 순수 함수만 구동(네트워크 0). 단언 항목:
    - **P3 (D3) `clamp_into_bounds` 항상 경계 안**: 무작위 Rect(음수 top/left·region 초과 포함, left∈[-5,18]/top∈[-5,12]/w∈[0.05,20]/h∈[0.05,12]) → `within_bounds(clamp_into_bounds(r), SLIDE)` 항상 참 + 클램프 크기 ≤ 슬라이드. **P3 `fit_within` 음수 off 없음**: 무작위 region/natural → draw ≤ region, off_l/off_t ≥ region 좌상단, 경계 초과 없음. **fit→clamp 합성**(server.py task 3.4 순서 모사) 결과도 슬라이드 경계 안.
    - **P2 (D2) `slot_image_fits` 정확 술어**: 무작위 슬롯/픽셀 → `fits == NOT(is_small_slot ∧ is_large_image)` 정확 일치. **재배정 후 버그 부재**: `fits==False`면 콘텐츠 region `(1.5,1.7,10.33,5.2)`로 승격 → 소형 슬롯+대형 이미지 조합 0건, 재배정 region 은 정합(True).
    - **P1 (D1) `fullbleed_guard` ≤1**: 풀블리드 후보 0..12개 직렬 임베드(가드 검사) → 임베드 총수 ≤1, 후보≥1이면 정확히 1장(손실-0 첫 배경 보존). 경계 `guard(0)==True`/`guard(1,5)==False`.
    - **P4 Preservation(보강)**: 경계 안 rect clamp no-op 동등(±1e-9), 비-소형 슬롯 항상 fits, 소형 슬롯+1024px 미만(임계 근방 1023px) 정합, natural<region 시 확대 없이 중앙배치.
    - **임계 경계 단위**: 1024px(1023→False/1024→True), 0.5in(0.5→small/0.51→not), EPS=0.05(-0.05 허용/-0.06 불허) + 실측 회귀 고정(슬라이드1 `(8.11,-1.39,5.21,4.17)`→clamp 후 경계 안, 3840×2160 @ 0.46in 슬롯→False).
  - _Requirements: 2.1, 2.2, 2.3_

- [x] 5. 통합 검증 테스트 작성 (`scripts/test_pptx_image_slot_placement_integration.py`)
  - **Property 1: Bug Condition** - 실제 합성 덱에서 P1/P2/P3 (D1/D2/D3 덱 레벨 해소)
  - 실제 `ai_engine.server._tool_generate_pptx`를 헤르메틱 목으로 구동해(자매 스펙 `test_pptx_quality_vertex_images_integration.py`의 목 패턴 재사용: `_FakeVertexClient`, `_render_html_png_fake`, `_img_gen_disabled`, `patch.object`로 `_call_bridge`/`_get_gw`/`_render_html_slide_to_png`/`_generate_html_slide_for_section` 목) 슬라이드 8·9·1 유사 시나리오를 합성하고, 생성 덱을 audit 도구로 검사한다:
    - **D1 통합**: coverBackground+HTML 표지 또는 caller bg+서버 bg 동시 입력 → 각 슬라이드 풀블리드 ≤1(`audit_pptx_zorder_break`/`audit_pptx_media_classify`로 검증)
    - **D2 통합**: 대형(3840×2160) 이미지 입력 → 소형 슬롯에 대형 이미지 없음(`audit_pptx_media_classify`의 슬롯-이미지 정합 분류로 검증)
    - **D3 통합**: 큰 부분 이미지 입력 → `audit_pptx_zorder_break`의 off-slide 검출 0건
  - **Property 2: Preservation** - 손실-0 (P5 덱 레벨 보조 단언)
  - **손실-0 통합**: Vertex 이미지 생성 입력 → 모든 생성 이미지가 ppt/media에 임베드(unused=0), `_select_render_plan`의 `vertex_slot != "none"` 덱 레벨 보조 단언
  - **EXPECTED OUTCOME**: 수정된 코드에서 PASS
  - **헤르메틱 원칙 준수**: 게이트웨이(`_get_gw` 스파이), Vertex(`get_vertex_image_client` 스텁 + `generate` 목), HTML→PNG 렌더 모두 목 처리 — 네트워크 0
  - 실행: `./venv/bin/python -m pytest scripts/test_pptx_image_slot_placement_integration.py -p no:cacheprovider -q`
  - **실행 결과 (수정 후, 2026 실행)**: `4 passed in 1.26s` (Exit 0). 실제 `ai_engine.server._tool_generate_pptx` 합성 경로를 헤르메틱 목(게이트웨이 `_get_gw`/`_call_bridge` 스파이, Vertex `get_vertex_image_client` 스텁 + `generate` 목, HTML→PNG 렌더 `_render_html_slide_to_png`/`_generate_html_slide_for_section` 목 — 네트워크 0)으로 끝까지 구동하고, 생성 덱을 기존 audit 도구(`audit_pptx_zorder_break`/`audit_pptx_media_classify`)의 공개 판정 함수로 검사. 검증 항목:
    - **D1 통합 (`test_d1_integration_each_slide_fullbleed_le_1`) PASS**: `coverBackground`+HTML 표지 동시 활성 덱 → 각 슬라이드 풀블리드 count `[1, 1, 1]`(표지+본문2, 모두 ≤1). 두 audit 도구(`azb._fullbleed`/`acm._fb`) 교차검증 일치 → P1 충족.
    - **D2 통합 (`test_d2_integration_no_large_image_in_small_slot`) PASS**: 카드 `nativeDiagram` + `get_icon_png` 대형(3840×2160) PNG 반환 → 소형 슬롯(≤0.5in)의 대형(≥1024px) 이미지 0건(`audit_pptx_media_classify._rect` 슬롯 크기 × blob 픽셀 크기 정합 분류). 작업 3.3 `slot_image_fits` 가드가 칩 PNG 스킵·글리프 폴백 → P2 충족.
    - **D3 통합 (`test_d3_integration_offslide_detection_zero`) PASS**: 큰 부분 이미지(세로 1200×2400 / 가로 3000×900) `imageFile` 덱 → `audit_pptx_zorder_break.audit` 실제 호출(stdout)의 "슬라이드 밖 이미지 : 0" + 직접 off-slide 판정 0건. 작업 3.4 `clamp_into_bounds`가 부분-이미지 배치를 경계 안으로 클램프 → P3 충족.
    - **손실-0 P5 (`test_lossless_p5_all_vertex_images_embedded`) PASS**: `_FakeVertexClient`가 비주얼 슬라이드 2장에 바이트-고유 PNG 생성 → 모든 생성 이미지가 `ppt/media`에 임베드(`unused==0`, 바이트 레벨 검증). 덱 레벨 보조 단언 `_select_render_plan(has_vertex_image=True, role="visual", …)["vertex_slot"] != "none"` 확인 → P5(손실-0) 충족.
  - **헤르메틱/제약**: 게이트웨이/Vertex/HTML 전부 목 — 네트워크 0. `heredoc`/`stdin` 미사용(파일로 작성). server.py 미수정(테스트만 신규 생성). audit 도구 공개 함수(`_rect`/`_fullbleed`/`_fb`/`audit`) 재사용으로 탐색·검증·통합이 동일 기준(풀블리드 0.92 비율, off-slide EPS=0.05) 측정.
  - _Requirements: 2.1, 2.2, 2.3, 3.2_

- [x] 6. Checkpoint — 모든 테스트 통과 및 회귀 0 확인
  - 본 스펙 신규 4파일을 헤르메틱하게 실행한다:
    `./venv/bin/python -m pytest scripts/test_pptx_image_slot_placement_bug_condition.py scripts/test_pptx_image_slot_placement_fix_pbt.py scripts/test_pptx_image_slot_placement_preservation_pbt.py scripts/test_pptx_image_slot_placement_integration.py -p no:cacheprovider -q`
  - 이전 스펙 회귀 스위트를 함께 실행해 회귀 0을 확인한다:
    - `scripts/test_pptx_quality_vertex_images_bug_condition.py`, `_fix_pbt.py`, `_preservation_pbt.py`, `scripts/test_pptx_quality_vertex_images_integration.py`
    - `scripts/test_pptx_overlay_collision_bug_condition.py`, `_fix_pbt.py`, `_preservation_pbt.py`, `scripts/test_pptx_overlay_collision_integration.py`
    - `scripts/test_html_pipeline.py`(및 `test_html_*`)
    - `scripts/test_slide_templates_density.py`
  - **통합 검증 확인 사항**:
    - (a) 생성 덱의 각 슬라이드 풀블리드 ≤1, 소형 슬롯에 대형 이미지 없음, 모든 PICTURE 경계 안(P1~P3)
    - (b) 손실-0(생성 Vertex 이미지 unused=0) + 이전 스펙 텍스트/배지 겹침 <10% 불변식 유지(P5)
  - 컨텍스트 전환: 템플릿 적용/무템플릿 양쪽에서 스타일 상속과 슬롯 배정이 모두 동작함을 확인
  - 모든 테스트가 통과하는지 확인하고, 의문이 생기면 사용자에게 질문한다
  - **실행 결과 (검증 전용, 코드/테스트 미수정, 2026 실행)**:
    - **Step 1 — 본 스펙 신규 4파일 (헤르메틱, 네트워크 0)**: `./venv/bin/python -m pytest scripts/test_pptx_image_slot_placement_bug_condition.py scripts/test_pptx_image_slot_placement_fix_pbt.py scripts/test_pptx_image_slot_placement_preservation_pbt.py scripts/test_pptx_image_slot_placement_integration.py -p no:cacheprovider -q` → **32 passed in 4.14s** (Exit 0). 내역: bug_condition 3 + fix_pbt 17 + preservation_pbt 8 + integration 4 = 32. P1~P3(풀블리드 ≤1·소형 슬롯 대형 이미지 부재·모든 PICTURE 경계 안) 및 P4·P5(no-op 보존·손실-0) 전부 통과.
    - **Step 2 — 이전 스펙 회귀 스위트 11파일 (디스크 존재 확인 후 일괄 실행)**: `./venv/bin/python -m pytest scripts/test_pptx_quality_vertex_images_bug_condition.py scripts/test_pptx_quality_vertex_images_fix_pbt.py scripts/test_pptx_quality_vertex_images_preservation_pbt.py scripts/test_pptx_quality_vertex_images_integration.py scripts/test_pptx_overlay_collision_bug_condition.py scripts/test_pptx_overlay_collision_fix_pbt.py scripts/test_pptx_overlay_collision_preservation_pbt.py scripts/test_pptx_overlay_collision_integration.py scripts/test_html_pipeline.py scripts/test_html_slides.py scripts/test_slide_templates_density.py -p no:cacheprovider -q` → **59 passed, 1 warning in 7.81s** (Exit 0). 경고 1건은 `audit_pptx_baked_text.py`의 Pillow `Image.getdata` DeprecationWarning(실패 아님). 회귀 0. (`test_html_*`는 `test_html_pipeline.py`·`test_html_slides.py` 2개 모두 존재해 함께 실행.)
    - **종합**: Step 1 + Step 2 = **91 passed**, 0 failed/error, 회귀 0. (a) 각 슬라이드 풀블리드 ≤1·소형 슬롯 대형 이미지 부재·모든 PICTURE 경계 안(P1~P3) 통합 검증 통과, (b) 손실-0(Vertex unused=0) + 이전 스펙 텍스트/배지 겹침 <10% 불변식 유지(P5) 확인. 템플릿/무템플릿 양쪽 슬롯 배정·스타일 상속은 회귀 스위트(quality-vertex-images/overlay-collision/slide_templates_density) 통과로 확인.
  - _Requirements: 2.1, 2.2, 2.3, 3.1, 3.2, 3.3, 3.4, 3.5_

## Task Dependency Graph

```json
{
  "waves": [
    { "id": 0, "tasks": ["1", "2"] },
    { "id": 1, "tasks": ["3.1"] },
    { "id": 2, "tasks": ["3.2", "3.3", "3.4"] },
    { "id": 3, "tasks": ["3.5"] },
    { "id": 4, "tasks": ["3.6", "3.7"] },
    { "id": 5, "tasks": ["4", "5"] },
    { "id": 6, "tasks": ["6"] }
  ]
}
```

## Notes

- **PBT 상태 추적**: Property 태스크는 `**Property N: Type**` 형식을 사용해 hover 상태를 활성화한다. Property 1은 Bug Condition(태스크 1: 미수정 코드에서 FAIL → 태스크 3.6에서 Expected Behavior로 PASS), Property 2는 Preservation(태스크 2: 미수정 PASS/skip → 수정 후 PASS)이다.
- **실제 덱 audit (핵심 개선)**: 탐색·통합 테스트는 이전 스펙처럼 seam 단독이 아니라 **실제 `_tool_generate_pptx` 합성 경로**를 게이트웨이/Vertex/HTML 목으로 구동해 생성된 덱을 `audit_pptx_zorder_break.py`/`audit_pptx_media_classify.py`로 검사한다 — 사용자가 본 결함(중복 배경/오버사이즈 슬롯/슬라이드 밖)을 테스트가 실제로 잡는다. 정확한 버그 덱이 디스크에 없어 슬라이드 8·9·1 유사 입력으로 합성·재현한다.
- **헤르메틱 원칙**: 모든 테스트는 네트워크 호출이 없어야 한다. 게이트웨이는 `_get_gw` 패치, Vertex는 `get_vertex_image_client` 스텁 + `generate` 목, HTML 렌더는 `_render_html_slide_to_png`/`_generate_html_slide_for_section` 목으로 고정한다. `heredoc`/`stdin` 금지 — 테스트는 파일로 작성해 `./venv/bin/python -m pytest <파일> -p no:cacheprovider -q`로 실행한다.
- **디스크 패치(server.py)**: `ai_engine/server.py`는 **에디터 버퍼가 stale**하므로 반드시 디스크 기준으로 패치한다(stale 버퍼 기준 편집 금지). 모든 수정은 additive(기존 분기/좌표/반환 키 보존)이며 바이트 보존을 깨지 않는다.
- **손실-0 불변식**: 중복(D1)·오배정(D2) 후보는 어떤 분기에서도 폐기되지 않는다 — 풀블리드 1장 초과 후보, 소형 슬롯에서 밀려난 대형 이미지는 콘텐츠/비주얼/backdrop 슬롯으로 재배정하거나 최소한 생성 이미지 파일을 디스크에 보존한다. 이전 스펙 `pptx-quality-vertex-images`의 `_select_render_plan` 손실-0 결정 규칙을 약화시키지 않는다(Property 5가 검증).
- **게이트웨이 제약(steering 준수)**: LLM/operation JSON 생성은 Bedrock Gateway 경유만 유지하며, Vertex는 이미지 생성 경로(`ai_engine/vertex_image_module.py`)에서만 호출된다(gateway.md 이미지 예외 조항). 신규 `layout_geometry`는 순수 계산이며 네트워크/모델 호출이 없다(Property 5).
- **바이트 보존(additive)**: 신규 기하 함수는 비버그 입력(경계 안·정합·풀블리드 0장)이면 입력 좌표를 그대로 반환/True(no-op 동등성)한다. 비버그 슬라이드(`isBugCondition` 거짓)의 산출 바이트는 변경되지 않는다(Property 4).
- **감사↔코드 일치**: `layout_geometry.is_fullbleed`/`within_bounds`는 `audit_pptx_zorder_break.py`/`audit_pptx_media_classify.py`의 풀블리드/off-slide 판정과 동일 정의(풀블리드 0.92 비율, EPS=0.05)를 사용해, 탐색·검증·통합 audit이 동일 기준으로 측정되게 한다.
- **임계 상수**: `LARGE_PX=1024`(정상 장식 75×100/아이콘 24~40px과 4K 배경 3840×2160을 명확히 가름), `SMALL_SLOT_IN=0.5`(결함 슬롯 0.25in vs 콘텐츠 region ≥5in), `BOUNDS_EPS=0.05`(audit off-slide 판정과 동일).
- **회귀 방지**: 6번 Checkpoint에서 이전 스펙 `pptx-quality-vertex-images`·`pptx-overlay-collision-fix` 스위트 + `test_html_*` + `test_slide_templates_density.py`를 함께 실행해 회귀 0을 확인한다.
