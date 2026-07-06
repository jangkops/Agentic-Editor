# Implementation Plan

## Overview

본 계획은 bugfix 방법론(탐색 테스트 → 보존 테스트 → 수정 → fix-checking/회귀 검증)을 따른다.
PPTX 산출물의 세 가지 오버레이/충돌 결함 — 결함 A(표지 제목↔부제 수직 겹침), 결함 B(구조형
구워진-텍스트 이미지 + 본문↔배경 겹침), 결함 C(번호 배지↔라벨 박스 겹침) — 를 신규 순수 기하
모듈 `ai_engine/layout_geometry.py`(충돌 검출/회피)로 추출하고, `native_diagram_pptx.py`/
`server.py`의 placement·렌더 결정 코드가 이를 호출하도록 바꿔 해소한다.

모든 변경은 **additive**(기존 분기/좌표 보존)이며, 신규 기하 함수는 **겹침이 임계(10%) 이상일
때만** 좌표를 조정하고 임계 미만이면 입력을 그대로 반환(no-op 동등성)하여 비버그 입력의 바이트
보존을 보장한다. 모든 테스트는 게이트웨이·Vertex·HTML 렌더를 목(mock)으로 고정해 **헤르메틱**
(네트워크 0)하게 실행하며, 기존 점검 도구(`scripts/audit_pptx_textbox_overlap.py`,
`audit_pptx_overlap.py`, `audit_pptx_images.py`, `audit_pptx_baked_text.py`)를 재현·검증에
재사용한다. `heredoc`/`stdin` 금지 — 테스트는 파일로 작성하고
`./venv/bin/python -m pytest <파일> -p no:cacheprovider -q`로 실행한다.

## Tasks

- [x] 1. 버그 조건 탐색 테스트 작성 (`scripts/test_pptx_overlay_collision_bug_condition.py`)
  - **Property 1: Bug Condition** - 표지 제목↔부제·번호 배지↔라벨·구조형 구워진-텍스트/본문↔배경 겹침 재현
  - **CRITICAL**: 이 테스트는 미수정 코드에서 반드시 FAIL 해야 한다 — 실패가 결함 A/B/C 존재를 증명한다
  - **DO NOT attempt to fix the test or the code when it fails** — 실패는 의도된 결과다
  - **NOTE**: 이 테스트는 기대 동작(겹침 < 10% 임계, 구조형 비구워짐, 본문↔배경 분리)을 인코딩하며, 수정 후 PASS 하면 fix를 검증한다(태스크 3.7에서 재실행)
  - **GOAL**: 결함 A/B/C를 재현하는 반례를 표면화하고 근본 원인(고정 오프셋 `sub_y`, 배지 내부 인셋 중심, `bgHasBakedText` 신호 부재 + 본문 안전 영역 부재)을 확인/반증한다
  - **Scoped PBT Approach**: 결정론적 재현을 위해 다음 구체 케이스에 스코프한다(design Examples 좌표):
    - **(A) 결함 A** `build_native_cover`로 `title_pt>=40` 긴 제목 표지 조립 → `audit_pptx_textbox_overlap.py`의 `ov()` 기준으로 제목 박스 하단(top+height ≈ 2.8+2.0=4.8) > 부제 top(≈3.85) → 세로 겹침 ≈10.05in²(작은 박스의 ~95%) 관찰 → `isBugCondition` 의 defectA 참
    - **(C) 결함 C** twocol 6항목 네이티브 카드 슬라이드 조립 → `_badge(x0+3.4, …)` 배지 사각형이 라벨 카드(`x0..x0+col_w`) 내부에 완전 포함 → 배지∩라벨 == 배지 면적의 100% 관찰 → defectC 참
    - **(B-1) 결함 B** structural(흐름/트리/아키텍처) 슬라이드가 텍스트 구워진 풀블리드 AI 이미지로 렌더됨을 `audit_pptx_baked_text.py`로 관찰(role=structural ∧ bgImage ∧ bgHasBakedText) → defectB 참
    - **(B-2) 결함 B** 풀블리드 배경 위 본문 region(≈0.5,1.75,9.0,4.95)이 배경 rect에 완전 포함 → 본문↔배경 겹침 == 본문 면적의 100% 관찰 → defectB 참
  - 기존 audit 도구(`audit_pptx_textbox_overlap.py`/`audit_pptx_overlap.py`/`audit_pptx_images.py`/`audit_pptx_baked_text.py`)의 측정 함수를 재사용한다(겹침 정의 = 코드의 `overlap_area`와 동일 축-정렬 교집합)
  - 헤르메틱: Vertex `generate`/HTML→PNG 렌더/게이트웨이는 목으로 고정, 네이티브 조립 경로만 실제 구동(네트워크 0)
  - 단언(미수정 코드의 기대 = Expected Behavior Properties): 배치 후 모든 (텍스트박스 ∪ 배지) 쌍 `(a,b)`에 대해 `overlap_area(a,b) < 0.10*min(area(a),area(b))`, 구조형은 구워진-텍스트 캐리어 아님, `overlap_area(bodyBox, bgImage.rect) < 0.10*area(bodyBox)` (design Property 1·2)
  - 미수정 코드에서 테스트를 실행한다
  - **EXPECTED OUTCOME**: 테스트 FAIL — 결함 A/B/C 반례가 표면화됨(이것이 정상 — 버그 존재 증명)
  - 발견한 반례를 문서화한다(예: "표지 제목 하단 4.8 > 부제 top 3.85 → 겹침 10.05in²", "배지∩라벨 100%", "본문 region이 풀블리드 배경에 100% 포함")
  - 테스트가 작성·실행되고 실패가 문서화되면 태스크 완료로 표시한다
  - **관찰된 반례 (미수정 코드, `4 failed` — 버그 존재 증명, 실행: `./venv/bin/python -m pytest scripts/test_pptx_overlay_collision_bug_condition.py -p no:cacheprovider -q`)**:
    - **결함 A** `test_defect_a_cover_title_subtitle_overlap` — `build_native_cover(title="데이터 흐름 구조 정리", subtitle="조직 전반의 디렉토리 깊이와 데이터 흐름을 한눈에 시각화합니다")`: 제목 박스 `(1.15, 2.8, 11.18×2.0)` 하단 `top+height=4.8` > 부제 박스 `(1.17, 3.85, 10.58×1.0)` top `3.85` → 세로 겹침 **10.05in² = 작은 박스의 95%** (≥ 10% 임계). 근본 원인 확인: `sub_y = title_y + 1.05`(고정 오프셋)이 제목 박스 실제 점유 높이 2.0을 반영하지 않음.
    - **결함 C** `test_defect_c_twocol_badge_label_overlap` — twocol 6항목: 배지 `'1'@(0.98, 2.7, 0.68×0.68)`가 라벨 카드 `'언어'@(0.9, 2.27, 5.45×1.14)`에 **완전 포함** → 겹침 **0.46in² = 배지 면적의 100%** (≥ 10% 임계). 근본 원인 확인: `_badge(x0+3.4, …)` 중심이 카드(`x0..x0+col_w`) 내부 인셋으로 계산됨(거터 모델 부재). 6개 배지 모두 동일.
    - **결함 B-1** `test_defect_b1_structural_baked_text_carrier` — 흐름 슬라이드가 실제 분류기 `_classify_slide_role` 로 `role=structural` 판정, 그 위 풀블리드 배경에 텍스트 구워짐(`audit_pptx_baked_text.baked_text_score` → 텍스트추정행 15.7%, 텍스트줄 14개) → `role=structural ∧ bgImage ∧ bgHasBakedText=True` → defectB 참. 근본 원인 확인: `_classify_slide_role`/`_select_render_plan`에 `bgHasBakedText` 신호 부재로 구워진-텍스트 이미지를 구조형 본문 캐리어로 차단하지 못함.
    - **결함 B-2** `test_defect_b2_body_over_fullbleed_background_overlap` — 본문 region `(0.5, 1.75, 9.0×4.95)`이 풀블리드 배경 rect `(0, 0, 13.333×7.5)`에 **완전 포함** → 본문↔배경 겹침 **44.55in² = 본문의 100%** (≥ 10% 임계). 근본 원인 확인: `_eff_bg` 풀블리드 `add_picture` 후 본문 안전 영역 계산 없이 그 위에 본문을 올림.
  - **결론**: 단언이 인코딩한 기대 동작(겹침 < 10%, 구조형 비구워짐, 본문↔배경 < 10%)이 미수정 코드에서 모두 위반 → design Hypothesized Root Cause 4건이 라인 단위로 확인됨. 동일 테스트는 수정 후 태스크 3.7에서 PASS 해야 fix 검증 완료.
  - _Bug_Condition: isBugCondition(S) = defectA OR defectB OR defectC (design Bug Condition)_
  - _Requirements: 1.1, 1.2, 1.3, 1.4, 1.5_

- [x] 2. 보존 속성 테스트 작성 (`scripts/test_pptx_overlay_collision_preservation_pbt.py`) — 수정 전 작성
  - **Property 2: Preservation** - 비버그 입력의 배치 좌표/렌더 플랜 보존(no-op 동등성)
  - **IMPORTANT**: observation-first 방법론을 따른다 — 먼저 미수정 코드의 비버그 입력(`isBugCondition`이 거짓) 동작을 관찰·기록한 뒤, 그 동작을 그대로 단언한다
  - 비버그 입력 도메인을 무작위 생성해 광범위하게 다룬다(임계 근방 겹침, 단일 항목, 빈 부제, 라벨 밖 배지, 백드롭/흰 패널 분리 등 엣지 포함)
  - 관찰 후 단언할 보존 동작(design Preservation Checking / Property 3):
    - **PRES-1 표지 비겹침 보존 (Req 3.1)**: 이미 비겹침인 짧은 제목/부제 → `vertical_stack` 결과 == 입력 좌표(바이트 동등)
    - **PRES-2 배지 거터 보존 (Req 3.1)**: 라벨 박스 밖에 이미 있는 배지 → `place_badge_in_gutter` no-op 동등(좌표 불변)
    - **PRES-3 본문 분리 보존 (Req 3.5)**: 백드롭(스크림/흰 콘텐츠 패널)으로 이미 분리된 경로 → `body_safe_area`가 기존 본문 region 그대로 반환
    - **PRES-4 구조형 네이티브 보존 (Req 3.6)**: 진짜 구조형(흐름/트리/아키텍처)은 편집 가능 네이티브 도형으로 계속 렌더(구워진 이미지 캐리어로 전환되지 않음)
    - **PRES-5 임계 근방 no-op (Req 3.1, design Property 3)**: `resolve_collisions`/`vertical_stack`/`body_safe_area`는 겹침 < 임계 입력에 대해 입력 좌표를 그대로 반환
  - 충돌 회피 기하 함수는 "겹침 임계 미만 → 입력 그대로 반환"을 직접 단언한다(no-op 동등성)
  - 헤르메틱: 게이트웨이/Vertex/HTML 렌더 목 고정, 네트워크 0
  - 미수정 코드에서 테스트를 실행한다(주: 신규 `layout_geometry` 함수 대상 단언은 모듈 신설 후 태스크 3.8에서 동작 검증 — 본 태스크에서는 미수정 placement 경로의 비버그 입력 좌표/플랜 baseline을 관찰·고정하고, 기하 함수 no-op 단언은 함수 시그니처 도입 직후 추가)
  - **EXPECTED OUTCOME**: 테스트 PASS — 보존해야 할 기준(baseline) 동작이 확인됨
  - 테스트가 작성·실행되고 미수정 코드에서 통과하면 태스크 완료로 표시한다
  - **관찰된 baseline (미수정 코드, `3 passed, 4 skipped` — 실행: `./venv/bin/python -m pytest scripts/test_pptx_overlay_collision_preservation_pbt.py -p no:cacheprovider -q`)**:
    - **PRES-4 구조형 네이티브 보존 (지금 PASS)** `test_pres4_structural_renders_native_preserved` / `test_pres4_structural_diagram_no_raster` — 흐름/트리/아키텍처 슬라이드가 실제 분류기 `_classify_slide_role`로 `role="structural"` 판정됨을 관찰·고정. `build_native_diagram`(flow/tree/architecture)이 편집 가능 네이티브 도형으로 렌더되어 래스터 PICTURE 임베드 0(`_pictures(slide)==[]`)임을 고정. `_select_render_plan(has_native_diagram=True, role="structural", …)["primary"]=="NATIVE_SHAPES"` baseline 고정(구워진 이미지 캐리어로 전환되지 않음 — Req 3.6).
    - **PRES-3 본문 분리 보존 (지금 PASS)** `test_pres3_backdrop_white_panel_separation_preserved` — `build_native_diagram(backdrop=True)`가 본문을 배경과 분리하는 흰 콘텐츠 패널을 먼저 깖을 관찰·고정. 패널 기하 baseline = `(left≈0.5, top≈0.5, w≈12.333, h≈6.58)` (backdrop 분기 `cx0=0.5, ctop=0.5, card_w=SW-2*margin=12.333, ch=max(2.0,(7.5-0.42)-0.5)=6.58`). 이 분리 패널 존재가 백드롭 경로 본문 분리의 baseline(Req 3.5).
    - **PRES-1/PRES-2/PRES-5 layout_geometry no-op (지금 skip → task 3.8 활성)** — `ai_engine.layout_geometry` 모듈이 아직 없어(`_HAS_LG=False`) `@pytest.mark.skipif`로 4개 테스트 자동 skip. 모듈 신설(task 3.1) 후 재실행(task 3.8) 시 자동 활성화되어 실제 no-op 동등성 검증: PRES-1 `vertical_stack`(이미 분리된 박스 → 좌표 불변), PRES-2 `place_badge_in_gutter`(배지 라벨 밖 거터, 겹침 0, diameter 보존), PRES-5 `resolve_collisions`(완전 분리 → 좌표 불변) + `body_safe_area`(백드롭/구워진-텍스트 아님 → desired 보존).
    - **헤르메틱 확인**: `_tool_generate_pptx` 통합 경로를 구동하지 않고 순수 결정 함수(`_classify_slide_role`/`_select_render_plan`)와 인메모리 네이티브 도형 조립(`build_native_*`)만 구동 → 게이트웨이/Vertex/HTML 렌더 호출 여지 없음(네트워크 0). 겹침 측정은 기존 `audit_pptx_textbox_overlap.ov`(축-정렬 교집합) 재사용.
  - **결론**: 비버그 입력의 placement 경로 baseline(구조형 네이티브 + 백드롭 흰 패널 분리)이 미수정 코드에서 확인됨. layout_geometry no-op 단언은 모듈 도입 직후 자동 활성화되도록 가드 완료. 동일 테스트는 수정 후 task 3.8에서 `3 passed` 이상(skip 4개가 PASS로 전환)으로 회귀 0을 검증한다.
  - _Preservation: Preservation Requirements 전체 (Req 3.1, 3.5, 3.6) / design Property 3_
  - _Requirements: 3.1, 3.5, 3.6_

- [x] 3. PPTX 오버레이/충돌 수정 구현

  - [x] 3.1 신규 순수 기하 모듈 `ai_engine/layout_geometry.py` 작성 (PBT 대상)
    - LLM/게이트웨이/네트워크 호출이 전혀 없는 **순수 함수**만 모은 신규 모듈을 additive로 추가한다(좌표 단위 = 인치 `Rect = (left, top, width, height)`)
    - `area(r)`: `max(0,w)*max(0,h)`
    - `overlap_area(a, b)`: 축-정렬 교집합 면적 — `audit_pptx_textbox_overlap.py`의 `ov()`와 **동일 정의**(감사 ↔ 코드 일치)
    - `vertical_stack(boxes, *, gap=0.0, max_bottom=None)`: 입력 순서대로 위→아래 비겹침 재배치(각 박스 top ≥ 직전 박스 bottom+gap, 첫 박스 top 보존). 이미 비겹침이면 입력 그대로 반환
    - `resolve_collisions(boxes, *, threshold=0.10, axis="vertical", bounds=None)`: 모든 쌍이 `overlap_area < threshold*min(area)` 되도록 최소 이동. 임계 미만이면 입력 그대로 반환(no-op 동등성)
    - `place_badge_in_gutter(label, diameter, *, gutter="left", gap=0.05)`: 배지(정사각 diameter)를 라벨 박스 밖 거터에 배치, `overlap_area(badge, label) == 0` 보장
    - `body_safe_area(slide, bg, *, has_baked_text, desired)`: bg가 풀블리드 구워진-텍스트면 desired를 겹침 < threshold 안전 영역으로 축소/이동(또는 네이티브 캐리어 전환 신호), bg 없음/백드롭이면 desired 보존
    - **핵심 불변식**: `resolve_collisions`/`vertical_stack`/`body_safe_area`는 겹침이 임계 미만이면 입력을 그대로 반환(Property 3 바이트 보존의 근거)
    - _Bug_Condition: isBugCondition(S) — 충돌 검출/회피 기하 부재로 인한 결함 A/B/C_
    - _Expected_Behavior: area/overlap_area/vertical_stack/resolve_collisions/place_badge_in_gutter/body_safe_area (design Fix Implementation §0)_
    - _Preservation: 겹침 임계 미만 시 no-op 동등성 (design Property 3)_
    - _Requirements: 2.1, 2.2, 2.4, 2.5, 3.1_

  - [x] 3.2 결함 A — 표지 제목/부제 수직 스택 (`native_diagram_pptx.py` `build_native_cover`)
    - `sub_y`의 고정 오프셋(`title_y + (1.05 if title_pt>=40 else 0.9)`)을 제거하고, 제목 박스의 실제 점유 높이를 반영해 비겹침으로 스택한다
    - 제목 박스 height를 고정 2.0 대신 `est_title_h`(폰트 pt·줄수 기반 추정, 최대 2.0 캡, 짧은 제목 보존용 하한 적용)로 산출
    - `vertical_stack([title_rect, sub_rect_desired], gap=0.12)`로 부제 top을 비겹침 좌표(`max(기존 sub_y, title_rect.bottom + gap)`)로 설정
    - 겹침이 없던 짧은 제목(한 줄)에서는 결과가 기존과 동일하게 유지(추정 높이 하한으로 보존)
    - **additive/바이트 보존**: 비겹침 입력은 좌표 불변, 기존 도형 추가 순서/스타일 유지. heredoc/stdin 미사용, 파일 직접 편집
    - _Bug_Condition: defectA — overlapArea(제목, 부제) >= 0.10*min(area) (design 결함 A)_
    - _Expected_Behavior: 부제 top >= 제목 박스 bottom, 겹침 < 10% (design Property 1, Req 2.1·2.2)_
    - _Preservation: 비겹침 짧은 제목 좌표 보존 (design Property 3, Req 3.1)_
    - _Requirements: 2.1, 2.2, 3.1_

  - [x] 3.3 결함 C — 번호 배지를 라벨 밖 거터로 (`native_diagram_pptx.py` `_badge` 호출부)
    - 배지 중심 좌표를 카드 내부 인셋(`x0 + 3.4` 등)에서 **라벨 박스 왼쪽 거터**로 옮긴다
    - 라벨 카드 Rect를 인치로 환산(`_x`/`_y`/`_w`/`_h` 역산) → `place_badge_in_gutter(label_rect_in, diameter=badge_d_in, gutter="left", gap=0.05)` → 배지 중심 v-unit 역산
    - 거터 폭이 부족하면 카드 묶음 전체를 배지 폭만큼 우측 들여 배치(레이아웃 전역 시프트)하여 배지를 라벨 박스 밖에 둔다
    - `text_inset_left_in`은 더 이상 배지-겹침 회피 용도가 아니므로 거터 모델과 일관되게 정리(겹침이 이미 없으면 동작 보존)
    - **twocol/block/flow(세로) 등 `_badge`를 라벨 박스 내부에 두던 모든 호출부에 동일 적용**
    - **additive/바이트 보존**: 라벨 밖에 이미 있던 배지는 좌표 불변. heredoc/stdin 미사용, 파일 직접 편집
    - _Bug_Condition: defectC — overlapArea(badge, label) >= 0.10*area(badge) (design 결함 C)_
    - _Expected_Behavior: 배지를 라벨 밖 거터에 배치, 겹침 < 배지 면적의 10% (design Property 1, Req 2.5)_
    - _Preservation: 라벨 밖 배지 no-op 동등 (design Property 3, Req 3.1)_
    - _Requirements: 2.5, 3.1_

  - [x] 3.4 결함 B — 구워진-텍스트 신호 + 본문/배경 분리 결정 규칙 보강 (`server.py` `_classify_slide_role`/`_select_render_plan`)
    - `_classify_slide_role`/`_select_render_plan`에 `bgHasBakedText` 신호를 추가 입력으로 받아, `role == structural` 또는 구워진-텍스트 풀블리드 이미지가 본문 캐리어가 될 상황에서는 그 이미지를 본문 캐리어로 선택하지 않는다
    - 구조형은 편집 가능 네이티브 도형(`NATIVE_SHAPES`)을 주 렌더러로 유지하고, 생성된 이미지는 `vertex_slot = "backdrop"`(장식)으로만 보존한다(**손실-0 유지** — Property 4)
    - 반환 플랜에 본문/배경 분리 의도를 명시(예: `body_separated: bool`) — **additive 키**이므로 기존 출력 바이트 불변
    - **additive/바이트 보존**: 기존 분기/반환값은 유지하고 신호·키만 추가. 이전 스펙 `pptx-quality-vertex-images`의 `_select_render_plan` 손실-0 결정 규칙을 약화시키지 않는다
    - _Bug_Condition: defectB — (role=structural ∧ bgImage ∧ bgHasBakedText) (design 결함 B)_
    - _Expected_Behavior: 구워진-텍스트 이미지를 본문 캐리어로 쓰지 않음, 구조형 네이티브 유지 (design Property 2, Req 2.3)_
    - _Preservation: 손실-0 불변식(has_vertex_image ⇒ vertex_slot != "none"), 게이트웨이 제약 (design Property 4·5, Req 3.2·3.4·3.6)_
    - _Requirements: 2.3, 3.2, 3.4, 3.6_

  - [x] 3.5 결함 B — 본문 안전 영역 적용 (`server.py` `_native_over_bg`/`_eff_bg` 합성)
    - `_eff_bg` 풀블리드 `add_picture` 후 본문 region을 그 위에 그대로 올리던 경로를 `body_safe_area`로 통과시킨다
    - `body_region := body_safe_area(slide=Rect(0,0,13.333,7.5), bg=bg_rect, has_baked_text=bg_has_baked_text, desired=body_region)`
    - bg가 백드롭(스크림/흰 콘텐츠 패널로 분리)이면 기존 동작 보존(흰 패널이 분리 보장), bg가 구워진-텍스트 풀블리드인데 본문이 큰 면적으로 올라가면 본문을 안전 영역으로 축소/이동하거나 이미지를 backdrop 슬롯으로만 두고 본문은 네이티브로
    - **손실-0 보존**: 어떤 분기에서도 생성된 Vertex 이미지를 폐기하지 않는다(backdrop/비주얼 슬롯 유지). 게이트웨이 제약 무관(순수 좌표 계산)
    - **additive/바이트 보존**: 분리된 본문(겹침 < 임계)은 좌표 불변. heredoc/stdin 미사용, 파일 직접 편집
    - _Bug_Condition: defectB — overlapArea(bodyBox, bgImage.rect) >= 0.10*area(bodyBox) (design 결함 B)_
    - _Expected_Behavior: 본문을 배경과 분리된 안전 영역에 배치, 겹침 < 10% (design Property 2, Req 2.4)_
    - _Preservation: 백드롭 분리 경로 본문 region 보존, Vertex 손실-0 (design Property 3·4, Req 3.1·3.2)_
    - _Requirements: 2.4, 3.1, 3.2_

  - [x] 3.6 보존 가드 유지
    - caller가 명시한 `imageFile`/`slideBackground` 슬라이드의 기존 우선순위는 변경 없음
    - Vertex 비활성/실패(쿼터/서킷브레이커)면 `_vertex_pre`가 비어 모든 분기가 기존 네이티브/HTML 폴백으로 진행한다(Req 3.3)
    - 템플릿 `styleProfile`/`templatePath` 상속과 HTML 풀블리드 고밀도 경로는 정상 슬라이드에서 그대로 적용한다(Req 3.5)
    - LLM/operation JSON 생성은 Bedrock Gateway 경유만, Vertex는 이미지 생성 경로(`ai_engine/vertex_image_module.py`)에서만 호출됨을 유지한다(Req 3.4)
    - 신규 기하 함수는 겹침 임계 미만이면 no-op → 비버그 입력 바이트 보존(Req 3.1, Property 3)
    - **보존 전용 태스크** — 코드 변경 없음, 태스크 3.1~3.5가 가드를 약화시키지 않았음을 정독으로 확인(필요 시 회귀 가드 보강만 additive)
    - _Bug_Condition: N/A (보존 전용 — isBugCondition 거짓 입력 보호)_
    - _Expected_Behavior: 비버그 입력에서 원본과 동일한 결과 (design Preservation Checking)_
    - _Preservation: Preservation Requirements 전체 (Req 3.1~3.6)_
    - _Requirements: 3.1, 3.2, 3.3, 3.4, 3.5, 3.6_

  - [x] 3.7 버그 조건 탐색 테스트가 이제 통과하는지 검증
    - **Property 1: Expected Behavior** - 텍스트박스·배지 비겹침 + 구조형 비구워짐 + 본문↔배경 분리
    - **IMPORTANT**: 태스크 1의 동일한 테스트를 재실행한다 — 새 테스트를 작성하지 않는다
    - 태스크 1의 테스트는 기대 동작을 인코딩하며, 통과 시 결함 A/B/C가 해소됐음을 확인한다
    - `./venv/bin/python -m pytest scripts/test_pptx_overlay_collision_bug_condition.py -p no:cacheprovider -q` 로 수정된 코드에서 실행
    - **EXPECTED OUTCOME**: 테스트 PASS (결함 A/B/C 수정 — 모든 (텍스트박스 ∪ 배지) 쌍 겹침 < 10%, 구조형 구워진-텍스트 캐리어 부재, 본문↔배경 겹침 < 10%)
    - **B 케이스 교정(fix seam 구동)**: 초기 B-1/B-2 테스트는 수정된 코드 경로를 호출하지 않고 버그 시나리오를 손으로 구성(풀블리드 배경 picture + 본문 textbox 직접 add)한 뒤 비겹침을 단언해, 수정 후에도 구조상 PASS 불가였다(테스트 자신이 겹침을 생성). 두 케이스를 **수정된 fix seam을 실제로 구동**하도록 교정(단언 강도 유지):
      - **B-1** `test_defect_b1_structural_baked_text_carrier` — `_select_render_plan(role="structural", has_vertex_image=True, has_native_diagram=False, has_image_file=False, has_slide_bg=False, html_enabled=False, bg_has_baked_text=True)` 구동 → `primary == "NATIVE_SHAPES"` ∧ `vertex_slot == "backdrop"`(손실-0 보존) ∧ `body_separated is True` 단언. 추가로 `_classify_slide_role(visual_slide, is_cover=False)`가 `"visual"`인 시각형 슬라이드가 `bg_has_baked_text=True`에서 `"content"`로 강등됨을 단언(구워진-텍스트 시 본문 캐리어 차단). 풀블리드 배경의 구워짐은 기존 `audit_pptx_baked_text.baked_text_score`로 전제 확인. 수정 전 코드(`bg_has_baked_text` 파라미터/`body_separated` 키 부재)에선 TypeError/KeyError로 FAIL → fix를 진짜로 검증.
      - **B-2** `test_defect_b2_body_over_fullbleed_background_overlap` — `layout_geometry.body_safe_area(slide=(0,0,13.333,7.5), bg=(0,0,13.333,5.5), has_baked_text=True, desired=(0.5,1.75,9.0,4.95))` 구동(배경이 상단 대부분을 덮되 하단 여백 띠 5.5~7.5를 남기는 현실적 입력 → 분리 경로 실제 동작). 반환 안전 영역 `region'=(0.5,5.5,9.0,2.0)`에 대해 `overlap_area(region', bg)=0.0 < 0.10*area(region')` 단언(분리 전 `desired`는 배경과 33.75in²=본문의 76% 겹침으로 전제 확인). `body_safe_area`가 없던 수정 전엔 import 자체가 불가 → fix 검증.
      - A/C 케이스(`test_defect_a_*`, `test_defect_c_*`)는 `build_native_cover`/`build_native_diagram` 실제 코드를 구동해 이미 PASS — 미변경.
    - **검증 결과 (수정된 코드, `4 passed` — 실행: `./venv/bin/python -m pytest scripts/test_pptx_overlay_collision_bug_condition.py -p no:cacheprovider -q`)**:
      - `test_defect_a_cover_title_subtitle_overlap` PASS — `build_native_cover` 산출 표지의 제목↔부제 겹침 < 10%.
      - `test_defect_c_twocol_badge_label_overlap` PASS — twocol 6항목 배지가 라벨 밖 거터로 이동, 배지∩라벨 < 배지 면적의 10%.
      - `test_defect_b1_structural_baked_text_carrier` PASS — 구조형이 `NATIVE_SHAPES`+`backdrop`(손실-0)+`body_separated`, 시각형이 구워진-텍스트 시 content로 강등.
      - `test_defect_b2_body_over_fullbleed_background_overlap` PASS — 본문이 배경 미점유 하단 여백 띠로 분리(겹침 0%).
      - `get_diagnostics` 클린(no diagnostics). 잔여 경고 1건은 audit 도구의 Pillow `getdata` DeprecationWarning(본 수정과 무관, 기존 도구 코드).
    - **결론**: 단언이 인코딩한 기대 동작(겹침 < 10%, 구조형 비구워짐, 본문↔배경 분리)이 수정된 코드에서 모두 충족 → 결함 A/B/C 해소 확인. B-1/B-2는 손으로 구성한 시나리오가 아니라 수정된 결정 seam(`_select_render_plan`/`_classify_slide_role`)과 순수 기하(`body_safe_area`)를 실제 구동해 fix를 검증한다.
    - _Requirements: 2.1, 2.2, 2.3, 2.4, 2.5 (Expected Behavior Properties from design Property 1·2)_

  - [x] 3.8 보존 테스트가 여전히 통과하는지 검증
    - **Property 2: Preservation** - 비버그 입력 배치 좌표/렌더 플랜 보존
    - **IMPORTANT**: 태스크 2의 동일한 테스트를 재실행한다 — 새 테스트를 작성하지 않는다(신규 `layout_geometry` no-op 단언 포함)
    - `./venv/bin/python -m pytest scripts/test_pptx_overlay_collision_preservation_pbt.py -p no:cacheprovider -q` 로 수정된 코드에서 실행
    - **EXPECTED OUTCOME**: 테스트 PASS (회귀 없음 — PRES-1~5 유지)
    - 수정 후에도 모든 보존 테스트가 통과함을 확인한다
    - _Requirements: 3.1, 3.5, 3.6_

- [x] 4. 추가 fix-checking 속성 테스트 작성 (`scripts/test_pptx_overlay_collision_fix_pbt.py`)
  - **Property 1: Bug Condition** - 결함 A/C 텍스트박스·배지 비겹침 (Fix-A/C)
  - 무작위 텍스트박스/배지 집합(겹침 유발 포함, defectA/defectC 입력)을 hypothesis로 생성해 `layout_geometry`의 `resolve_collisions`/`vertical_stack`/`place_badge_in_gutter`가 배치 후 모든 쌍 `(a,b)`에 대해 `overlap_area(a,b) < 0.10*min(area(a),area(b))` 보장함을 단언한다(표지 제목↔부제 수직 비겹침 + 배지 라벨 밖 거터 포함)
  - **Property 2: Preservation** - 결함 B 구조형 비구워짐 + 본문↔배경 분리 (Fix-B)
  - 무작위 role/bg/baked-text/bodyBox 조합에서 defectB 입력 → 수정 결정/배치가 (1) `role=structural ∧ bgImage ∧ bgHasBakedText` 출력을 만들지 않고, (2) `body_safe_area` 적용 후 `overlap_area(bodyBox, bgImage.rect) < 0.10*area(bodyBox)` 임을 단언한다
  - **EXPECTED OUTCOME**: 수정된 코드에서 PASS
  - **헤르메틱 원칙 준수**: `layout_geometry` 함수와 `_select_render_plan`은 순수 함수 — 네트워크 호출 0. 도메인 정직성 유지(임계 근방 엣지 포함)
  - 실행: `./venv/bin/python -m pytest scripts/test_pptx_overlay_collision_fix_pbt.py -p no:cacheprovider -q`
  - **검증 결과 (수정된 코드, `7 passed in 2.46s` — 실행: `./venv/bin/python -m pytest scripts/test_pptx_overlay_collision_fix_pbt.py -p no:cacheprovider -q`)**:
    - **Property 1 (Fix-A/C)** — `layout_geometry`의 순수 기하 함수를 hypothesis(각 max_examples 200~300)로 구동:
      - `test_property1_resolve_collisions_no_overlap` PASS — 좁은 영역에 몰린 무작위 박스 집합(2~6개)을 `resolve_collisions(axis="vertical")`로 통과 → 모든 쌍 겹침 < 10% min(area)(수직 비겹침 스택).
      - `test_property1_vertical_stack_title_subtitle` PASS — 겹치도록 생성된 제목/부제 Rect를 `vertical_stack(gap)`으로 통과 → 첫 박스 top 보존 + 부제 top ≥ 제목 bottom → 세로 겹침 < 10%.
      - `test_property1_place_badge_in_gutter_no_overlap` PASS — 무작위 라벨/지름/거터(left·right·top·bottom)/gap → 배지 정사각 보존 + `overlap_area(badge, label) == 0`(라벨 밖 거터).
      - `test_property1_combined_textboxes_and_badge` PASS — (텍스트박스 ∪ 배지) 결합: 스택된 텍스트박스끼리 < 10% ∧ 배지∩라벨 == 0.
    - **Property 2 (Fix-B)** — `_select_render_plan`/`body_safe_area`를 무작위 조합으로 구동:
      - `test_property2_structural_baked_not_body_carrier` PASS — `role="structural" ∧ bg_has_baked_text=True ∧ has_vertex_image=True`의 임의 미디어 조합 → `primary=="NATIVE_SHAPES"` ∧ `vertex_slot=="backdrop"`(손실-0) ∧ `body_separated is True`.
      - `test_property2_body_safe_area_separates_from_bg` PASS — 슬라이드를 완전히 덮지 않는(여백 띠를 남기는) 구워진-텍스트 풀블리드 + 그 위 겹치는 본문 → `body_safe_area` 반환 region에 대해 `overlap_area(region, bg) < 0.10*area(region)`(실측 0%).
      - `test_property2_loss_zero_concurrent` PASS — role/baked/미디어 플래그 임의 조합에서 `has_vertex_image=True`이면 `vertex_slot != "none"`(손실-0 동시 단언, design Property 4).
    - **헤르메틱 확인**: `ai_engine.layout_geometry`(순수 기하)와 `ai_engine.server._select_render_plan`(순수 결정 함수)만 임포트·구동 → 게이트웨이/Vertex/HTML 렌더 호출 여지 없음(네트워크 0). 겹침 측정은 `layout_geometry.overlap_area`(= `audit_pptx_textbox_overlap.ov()`와 동일 축-정렬 교집합) 재사용 → 감사 ↔ 코드 일치.
    - **`get_diagnostics` 클린**(no diagnostics).
  - **결론**: design Property 1(Fix-A/C)·Property 2(Fix-B)가 무작위 입력 도메인 전반(임계 근방 엣지 포함)에서 충족됨을 확인. 손실-0 불변식(Property 4)도 동시 단언으로 보존됨. `heredoc`/`stdin` 미사용, 파일 직접 작성·실행.
  - _Requirements: 2.1, 2.2, 2.3, 2.4, 2.5_

- [x] 5. 손실-0/게이트웨이 보존 속성 테스트 작성 (preservation_pbt 파일에 추가)
  - **Property 2: Preservation** - Vertex 손실-0 불변식 + 게이트웨이 제약 (Property 4·5)
  - **IMPORTANT**: 기존 태스크 2 파일(`scripts/test_pptx_overlay_collision_preservation_pbt.py`)에 **추가**한다(파일 미덮어쓰기)
  - **PROP4 손실-0 (design Property 4, Req 3.2·3.3)**: 모든 슬라이드 미디어 상태에 대해 본 수정 후에도 `_select_render_plan`이 생성된 Vertex 이미지를 폐기하지 않음(`has_vertex_image` ⇒ `vertex_slot != "none"`). 구조형에서 구워진-텍스트 이미지를 본문 캐리어로 쓰지 않더라도 생성 이미지는 backdrop 슬롯으로 보존. Vertex 비활성/실패 시 네이티브 폴백 유지를 목 스파이로 단언
  - **PROP5 게이트웨이 제약 (design Property 5, Req 3.4)**: LLM/operation JSON 생성 호출은 Bedrock Gateway(`_get_gw`) 경유로만, Vertex는 이미지 생성 경로에서만 호출됨(이미지 외 작업에서 Vertex 호출 0)을 목 스파이로 단언. 신규 `layout_geometry` 기하 함수가 어떤 네트워크/모델 호출도 하지 않음(임포트/호출 그래프 단언)
  - **EXPECTED OUTCOME**: 수정된 코드에서 PASS
  - **헤르메틱 원칙 준수**: 게이트웨이(`_get_gw` 스파이), Vertex(`get_vertex_image_client` 스텁 + `generate` 목), HTML→PNG 렌더 모두 목 처리 — 네트워크 호출 0
  - 실행: `./venv/bin/python -m pytest scripts/test_pptx_overlay_collision_preservation_pbt.py -p no:cacheprovider -q`
  - **검증 결과 (수정된 코드, `10 passed in 1.48s` — 실행: `./venv/bin/python -m pytest scripts/test_pptx_overlay_collision_preservation_pbt.py -p no:cacheprovider -q`)**:
    - **PROP4 손실-0 (신규 PASS)** `test_prop4_select_render_plan_loss_zero` — hypothesis 로 (has_vertex_image, has_native_diagram, has_image_file, has_slide_bg, role∈{cover,section,structural,content,visual}, html_enabled, bg_has_baked_text) 7-튜플 조합을 `max_examples=400` 으로 광범위 생성해 실제 `ai_engine.server._select_render_plan` 을 구동. 단언: 반환 계약(`primary`∈{HTML,NATIVE_SHAPES,VERTEX_IMAGE}, `vertex_slot`, `body_separated:bool` 키 존재), `has_vertex_image=True` ⇒ `vertex_slot != "none"`(∈{hero,backdrop,visual}, 손실-0), `role="structural" ∧ bg_has_baked_text=True` ⇒ `primary=="NATIVE_SHAPES" ∧ vertex_slot=="backdrop"(생성 이미지 미폐기) ∧ body_separated is True`, `has_vertex_image=False` ⇒ `vertex_slot=="none"`. 순수 결정 함수 호출이라 네트워크 0.
    - **PROP5 게이트웨이 제약 (신규 PASS)** `test_prop5a_layout_geometry_imports_only_stdlib` / `test_prop5b_layout_geometry_source_has_no_network_calls` — `inspect.getsource(layout_geometry)` + `ast` 정적 검사. PROP5-A: import 최상위 모듈이 모두 `sys.stdlib_module_names`(+`__future__`)에 속함(실제 import = `__future__`, `typing`), 상대 import(level>0)·`ai_engine.*` 1st-party 의존 0. PROP5-B: 코드 식별자(`ast.Name`)/속성(`ast.Attribute`)/import 이름에 네트워크·게이트웨이·모델 토큰(requests/httpx/urllib/socket/boto3/botocore/aiohttp/vertex/get_vertex/get_vertex_image_client/_get_gw/_call_bridge/bedrock/openai/anthropic/subprocess/_specialized_model_for_task/generate)이 0건(docstring 텍스트는 ast 코드 심볼로 안 잡혀 오탐 없음).
    - **회귀 0 (기존 통과분 유지)** — PRES-4 구조형 네이티브 보존(2), PRES-3 백드롭 흰 패널 분리(1), PRES-1/2/5 layout_geometry no-op(`vertical_stack`/`place_badge_in_gutter`/`resolve_collisions`/`body_safe_area`, 모듈 존재로 활성화, 4) 모두 PASS. 총 3(기존 baseline)+4(no-op 활성)+3(신규 PROP4/5A/5B)=10 passed.
    - `get_diagnostics` 클린(No diagnostics found).
  - **결론**: design Property 4(손실-0 불변식: `_select_render_plan.vertex_slot != "none" when has_vertex_image`, 구조형 구워진-텍스트도 backdrop 보존)와 Property 5(layout_geometry 순수성 — 네트워크/게이트웨이/모델 호출 0, 표준 라이브러리만 import)가 수정된 코드에서 모두 충족. 기존 스펙 `pptx-quality-vertex-images` 손실-0 회귀와 일관(동일 `_select_render_plan` 손실-0 결정 규칙을 약화시키지 않음). 헤르메틱(네트워크 0).
  - _Requirements: 3.2, 3.3, 3.4_

- [x] 6. Checkpoint — 모든 테스트 통과 및 통합 검증
  - 세 신규 스펙 테스트를 헤르메틱하게 실행한다:
    `./venv/bin/python -m pytest scripts/test_pptx_overlay_collision_bug_condition.py scripts/test_pptx_overlay_collision_fix_pbt.py scripts/test_pptx_overlay_collision_preservation_pbt.py -p no:cacheprovider -q`
  - 이전 스펙 회귀 스위트를 함께 실행해 회귀 0을 확인한다:
    `scripts/test_pptx_quality_vertex_images_bug_condition.py`, `_fix_pbt.py`, `_preservation_pbt.py`, `scripts/test_pptx_quality_vertex_images_integration.py`, `scripts/test_html_pipeline.py`(및 `test_html_*`), `scripts/test_slide_templates_density.py`
  - **통합 검증**: 표지·twocol(번호 배지)·structural(흐름도)·block 다이어그램이 섞인 덱을 실제 네이티브 placement 경로(`build_native_cover`/`build_native_diagram`)로 인메모리 조립하고 audit 도구(`audit_pptx_textbox_overlap.boxes()/ov()`)로 다음을 확인한다:
    - (a) 텍스트박스·배지 겹침 < 10% (`audit_pptx_textbox_overlap.py`)
    - (b) 구조형 구워진-텍스트 캐리어 부재 + 손실-0 (`_select_render_plan` 덱 레벨 보조 단언)
  - 컨텍스트 전환: 템플릿 적용/무템플릿 양쪽에서 스타일 상속과 충돌 회피가 모두 동작함을 확인
  - 모든 테스트가 통과하는지 확인하고, 의문이 생기면 사용자에게 질문한다
  - **검증 결과 (신규 통합 테스트 `scripts/test_pptx_overlay_collision_integration.py` 작성, `2 passed` — 실행: `./venv/bin/python -m pytest scripts/test_pptx_overlay_collision_integration.py -p no:cacheprovider -q`)**:
    - **통합 테스트 설계(헤르메틱, 네트워크 0)** — 실제 네이티브 placement 경로(`ai_engine.native_diagram_pptx.build_native_cover`/`build_native_diagram`)로 혼합 덱을 인메모리 16:9 Presentation에 조립: (a) 표지(긴 제목 "데이터 흐름 구조 정리" + 부제), (b) twocol(번호 배지 6항목 기술 스택), (c) structural flow(데이터 파이프라인 4단계), (d) block 다이어그램(설계 원칙 4항목). Vertex/게이트웨이/HTML 렌더 호출 없음 — 네이티브 도형 조립과 순수 결정 함수만 구동.
    - **(a)·(C) 결함 A·C 덱 레벨 해소** `test_integration_mixed_deck_no_textbox_badge_overlap` PASS — 각 슬라이드를 `audit_pptx_textbox_overlap.boxes()`로 측정하고 모든 (텍스트박스 ∪ 배지) 쌍에 대해 `ov(a,b) < 0.10*min(area(a),area(b))` 단언. 표지 제목↔부제(결함 A) 비겹침 + twocol/block 번호 배지가 라벨 카드 밖 거터로 이동(결함 C) — 배지 실재(`_is_badge`)를 전제 확인. 4개 슬라이드 모두 임계 미만.
    - **(B) 결함 B + 손실-0 덱 레벨 보조 단언** `test_integration_structural_baked_text_native_carrier` PASS — `_select_render_plan(role="structural", has_vertex_image=True, bg_has_baked_text=True, …)` → `primary=="NATIVE_SHAPES"`(구조형이 구워진-텍스트 풀블리드 본문 캐리어가 아님) ∧ `vertex_slot=="backdrop"`(생성 이미지 미폐기 — 손실-0) ∧ `body_separated is True`(본문/배경 분리 신호).
    - **전체 회귀 0 (`59 passed, 1 warning in 6.69s` — 실행: `./venv/bin/python -m pytest scripts/test_pptx_overlay_collision_integration.py scripts/test_pptx_overlay_collision_bug_condition.py scripts/test_pptx_overlay_collision_fix_pbt.py scripts/test_pptx_overlay_collision_preservation_pbt.py scripts/test_pptx_quality_vertex_images_bug_condition.py scripts/test_pptx_quality_vertex_images_fix_pbt.py scripts/test_pptx_quality_vertex_images_preservation_pbt.py scripts/test_pptx_quality_vertex_images_integration.py scripts/test_html_pipeline.py scripts/test_slide_templates_density.py -p no:cacheprovider -q`)**:
      - 본 스펙 4파일: 통합 2 + bug_condition 4 + fix_pbt 7 + preservation_pbt 10 = 23 passed.
      - 이전 스펙 `pptx-quality-vertex-images` 회귀(bug_condition/fix_pbt/preservation_pbt/integration) + `test_html_pipeline.py` + `test_slide_templates_density.py` 합산 36 passed → 총 59 passed, 회귀 0.
      - 잔여 경고 1건은 audit 도구 `audit_pptx_baked_text.py`의 Pillow `getdata` DeprecationWarning(본 수정과 무관, 기존 도구 코드).
    - `get_diagnostics` 클린(No diagnostics found).
    - **결론**: 결함 A(표지 제목↔부제)·C(번호 배지↔라벨)가 실제 placement 경로로 조립한 혼합 덱에서 겹침 < 10%로 해소되고, 결함 B(구조형 구워진-텍스트 본문 캐리어)는 `_select_render_plan`이 NATIVE_SHAPES+backdrop+body_separated로 차단하며 생성 이미지를 손실-0로 보존함을 덱 레벨에서 확인. 이전 스펙 회귀 0. `heredoc`/`stdin` 미사용, 파일 직접 작성·실행.

## Task Dependency Graph

```json
{
  "waves": [
    { "id": 0, "tasks": ["1", "2"] },
    { "id": 1, "tasks": ["3.1"] },
    { "id": 2, "tasks": ["3.2", "3.3", "3.4"] },
    { "id": 3, "tasks": ["3.5", "3.6"] },
    { "id": 4, "tasks": ["3.7", "3.8"] },
    { "id": 5, "tasks": ["4", "5"] },
    { "id": 6, "tasks": ["6"] }
  ]
}
```

## Notes

- **PBT 상태 추적**: Property 태스크는 `**Property N: Type**` 형식을 사용해 hover 상태를 활성화한다. Property 1은 Bug Condition(태스크 1: 미수정 코드에서 FAIL → 태스크 3.7에서 Expected Behavior로 PASS), Property 2는 Preservation(태스크 2: 미수정·수정 모두 PASS)이다.
- **헤르메틱 원칙**: 모든 테스트는 네트워크 호출이 없어야 한다. 게이트웨이는 `_get_gw` 패치, Vertex는 `get_vertex_image_client` 스텁 + `generate` 목, HTML 렌더는 `_render_html_slide_to_png`/`_generate_html_slide_for_section` 목으로 고정한다. `heredoc`/`stdin` 금지 — 테스트는 파일로 작성해 `./venv/bin/python -m pytest <파일> -p no:cacheprovider -q`로 실행한다.
- **손실-0 불변식**: 생성된 Vertex 이미지(`_vertex_pre[i]`)는 어떤 분기에서도 폐기되지 않는다 — 구조형에서 구워진-텍스트 캐리어로 쓰지 않더라도 최소한 backdrop 슬롯으로 보존된다. 이전 스펙 `pptx-quality-vertex-images`의 `_select_render_plan` 결정 규칙을 약화시키지 않는다(Property 4가 검증).
- **게이트웨이 제약(steering 준수)**: LLM/operation JSON 생성은 Bedrock Gateway 경유만 유지하며, Vertex는 이미지 생성 경로(`ai_engine/vertex_image_module.py`)에서만 호출된다(gateway.md 이미지 예외 조항, 이미지 외 작업에서 Vertex 호출 0). 신규 `layout_geometry`는 순수 계산이며 네트워크/모델 호출이 없다(Property 5가 검증).
- **바이트 보존(additive)**: 신규 기하 함수는 겹침 임계 미만이면 입력 좌표를 그대로 반환(no-op 동등성)한다. 모든 코드 변경은 additive(기존 분기/좌표/반환 키 보존)이며, 비버그 입력(`isBugCondition` 거짓)의 산출 바이트는 변경되지 않는다(Property 3).
- **감사↔코드 일치**: `layout_geometry.overlap_area`는 `scripts/audit_pptx_textbox_overlap.py`의 `ov()`와 동일한 축-정렬 교집합 정의를 사용해, 탐색·검증·통합 audit이 동일 기준(EMU 환산 10% 임계)으로 측정되게 한다.
- **회귀 방지**: 6번 Checkpoint에서 이전 스펙 `pptx-quality-vertex-images` 스위트 + `test_html_*` + `test_slide_templates_density.py`를 함께 실행해 회귀 0을 확인한다.
