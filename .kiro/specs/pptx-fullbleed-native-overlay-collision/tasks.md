# Implementation Plan

## Overview

"템플릿 + 이미지 슬라이드" 경로에서 콘텐츠가 구워진 풀블리드 HTML 배경과 동일 콘텐츠의 네이티브
제목/본문/다이어그램이 이중 합성되는 결함, 그리고 표지의 경계 밖(음수-top) 도형을 수정한다. 탐색
테스트(수정 전 FAIL)와 보존 테스트(수정 전 PASS)를 먼저 작성한 뒤 수정을 적용하고 재검증한다. 모든
테스트는 hermetic(네트워크 0), 파일로 작성해 `./venv/bin/python -m pytest <file> -p no:cacheprovider -q`
로 실행한다. server.py 에디터 버퍼는 STALE 이므로 디스크 패치로만 수정한다.

## Task Dependency Graph

```json
{
  "waves": [
    { "id": 0, "tasks": ["1", "2"] },
    { "id": 1, "tasks": ["3.1", "3.2", "3.3"] },
    { "id": 2, "tasks": ["3.4", "3.5"] },
    { "id": 3, "tasks": ["4"] }
  ]
}
```

- Wave 0: 탐색 테스트(수정 전 FAIL)와 보존 테스트(수정 전 PASS)를 먼저 작성.
- Wave 1: 수정 구현 — 3.1/3.2/3.3 은 server.py / native_diagram_pptx.py 의 독립 지점이라 병렬 가능.
- Wave 2: 수정 후 탐색·보존 테스트 재검증.
- Wave 3: 전체 체크포인트.

## Tasks

- [x] 1. 결함 조건 탐색 테스트 작성 (수정 전, 반례 수집)
  - **Property 1: Bug Condition** - 풀블리드-네이티브 충돌 및 경계밖 제거
  - **결과(수정 전, UNFIXED)**: `scripts/test_pptx_fullbleed_native_overlay_bug_condition.py` 작성·실행 → 3개 테스트 모두 **FAIL**(의도대로) — 결함 입증. 실제 `_tool_generate_pptx`를 hermetic(네트워크 0)하게 구동: 16:9 템플릿(used_template) + HTML 풀블리드 배경(섹션 렌더 mock이 제목/본문을 PNG에 베이크) + Vertex 비활성(표지 네이티브 cover) 경로로 감사된 이중 합성을 재현.
  - **반례(counterexamples)**:
    - 결함 1.1 — 슬라이드 2 네이티브 제목 박스 @(0.5,0.3 9.0×1.25) 및 본문 박스 'cgjang 루트 디렉토리' @(0.5,1.75 9.0×4.95)가 콘텐츠 구워진 풀블리드 배경(0,0,13.33×7.5) 위 **100% 겹침** (assert 1.0 < 0.1 실패). 감사된 본문 TEXT_BOX(0.6,1.6,12.1,5.4)와 등가(템플릿 placeholder 보존 시 좌표만 상이).
    - 결함 1.2 — 슬라이드 2/3/4 각각 제목 **2회**(native 1 + baked 1).
    - 결함 1.4 — 표지(슬라이드 1) 네이티브 장식 **AUTO_SHAPE OVAL rect=(10.633, -1.5, 3.9, 3.9)** (top=-1.5 음수, native_diagram_pptx.py:1597) + (11.833,0.7,1.7,1.7)(우측 13.533>13.333 초과) → 경계 밖.
  - 실행: `./venv/bin/python -m pytest scripts/test_pptx_fullbleed_native_overlay_bug_condition.py -p no:cacheprovider -q` → **3 failed**(EXPECTED OUTCOME 충족). 측정은 기존 감사 도구(audit_pptx_textbox_overlap.ov / audit_pptx_baked_text.baked_text_score / audit_pptx_zorder_break._rect) 재사용.
  - **CRITICAL**: 이 테스트는 수정 전(UNFIXED) 코드에서 반드시 FAIL 해야 한다 — 실패가 결함의 존재를 확인한다
  - **DO NOT**: 테스트가 실패해도 테스트나 코드를 고치지 말 것(이 단계는 재현만 한다)
  - **NOTE**: 이 테스트는 기대 동작을 인코딩한다 — 수정 후 통과하면 그것이 수정 검증이 된다
  - **GOAL**: 결함을 드러내는 반례 수집("템플릿 + 이미지 슬라이드" 경로 = HTML 풀블리드 배경 + 네이티브 제목/본문/다이어그램 동시 생성)
  - **Scoped PBT 접근**: 결정적 결함이므로 감사된 실제 산출물에 대응하는 구체 케이스로 property 를 스코프(본문 슬라이드 1~3, 슬라이드 4 다이어그램, 표지 0)
  - 파일로 작성: `scripts/test_pptx_fullbleed_native_overlay_bug_condition.py`, 실행 `./venv/bin/python -m pytest scripts/test_pptx_fullbleed_native_overlay_bug_condition.py -p no:cacheprovider -q`
  - hermetic: 게이트웨이/Vertex/HTML 렌더 mock, HTML 섹션 렌더 mock 은 제목/본문/카드를 PNG에 굽도록(baked) 구성, 실제 `ai_engine/server.py:_tool_generate_pptx` 구동(네트워크 0, Chrome 필요 시 skip, timeout)
  - 단언: 생성된 덱을 `scripts/audit_pptx_textbox_overlap.py` + `scripts/audit_pptx_zorder_break.py`(경계밖) + 구워진-콘텐츠 검사로 감사하여, isBugCondition 입력에서 (1) 네이티브 텍스트↔콘텐츠 구워진 풀블리드 배경 면적 겹침 < 10%, (2) 제목 1회, (3) 모든 도형 경계(0,0,13.333,7.5) 안 (design "Bug Condition" / expectedBehavior)
  - 수정 전 코드에서 실행 → **EXPECTED OUTCOME**: 테스트 FAIL(겹침≈100%, 제목 2회, 음수-top 존재 — 결함 입증)
  - 반례 문서화: "네이티브 본문 TEXT_BOX(0.6,1.6,12.1,5.4)가 구워진 배경 위 ~100% 겹침", "AUTO_SHAPE top=-1.50 경계밖" 등 (디스크 라인: server.py 5113/5174/5126/5149/5278, native_diagram_pptx.py 1597)
  - 테스트 작성·실행·실패 문서화 시 태스크 완료 처리
  - _Requirements: 1.1, 1.2, 1.3, 1.4_

- [x] 2. 보존 property 테스트 작성 (수정 전, 관측 우선)
  - **결과(수정 전 baseline)**: `scripts/test_pptx_fullbleed_native_overlay_preservation_pbt.py` 작성·실행 → 7 passed (5.37s). 관측 우선으로 비결함 동작을 고정함 — PRES-1 직접 네이티브 경로(slideBackground 미설정) 콘텐츠 슬라이드는 풀블리드 구워진 배경 없음/경계밖 없음/라벨 편집가능 텍스트 보존, PRES-2 비주얼 Vertex 손실-0(unused==0), PRES-3 caller imageFile/장식 slideBackground 우선순위 보존 + `_select_render_plan` 분기 보존 + 손실-0 불변식, PRES-4 풀블리드 없는 네이티브 텍스트 슬라이드 레이아웃/여백 보존, PRES-5 비결함 랜덤 덱(표지+N 수·손실-0·경계 안). 모두 hermetic(HTML OFF, 네트워크 0, Chrome 패치). 수정 후에도 PASS 유지가 회귀 가드(task 3.5).
  - **Property 2: Preservation** - 비결함 입력 동작 보존
  - **IMPORTANT**: 관측 우선(observation-first) 방법론을 따른다
  - 관측: 수정 전 코드에서 비결함 입력(slideBackground 미설정 직접 네이티브 경로)의 산출물 — 겹침/경계밖 없음
  - 관측: 수정 전 코드에서 생성된 Vertex 이미지가 `ppt/media/*`에 임베드됨(손실-0)
  - 관측: caller가 명시한 imageFile/장식 slideBackground 가 주 렌더러로 유지됨
  - 관측: 풀블리드 없는 네이티브 텍스트 슬라이드의 레이아웃/여백
  - property-based 테스트로 포착: 비결함 입력 전반에서 산출물(슬라이드 수, media 임베드, 겹침=0, 경계 안)이 보존됨 (design "Preservation Requirements")
  - 파일로 작성: `scripts/test_pptx_fullbleed_native_overlay_preservation_pbt.py`, 실행 `./venv/bin/python -m pytest scripts/test_pptx_fullbleed_native_overlay_preservation_pbt.py -p no:cacheprovider -q` (hermetic, 네트워크 0)
  - 수정 전 코드에서 실행 → **EXPECTED OUTCOME**: 테스트 PASS(보존할 기준 동작 확인)
  - 테스트 작성·실행·수정 전 통과 확인 시 태스크 완료 처리
  - _Requirements: 3.1, 3.2, 3.3, 3.4, 3.5_

- [x] 3. 풀블리드-네이티브 오버레이 충돌 및 경계밖 도형 수정

  - [x] 3.1 콘텐츠가 구워진 풀블리드 배경 ↔ 네이티브 콘텐츠 상호배제 게이트 구현
    - **결과**: server.py 디스크 패치(앵커 count==1 검증 후 적용). HTML 게이트가 콘텐츠를 베이크해 `slideBackground`로 채택하면(`_rr_html_bg_set=True`) 단일 결정 플래그 `_suppress_native_body = bool(_rr_html_bg_set)`로 네이티브 본문 방출을 차단. 본문 `add_textbox(0.6,1.6,12.1,5.4)` 조건과 채우기 조건에 `and not _suppress_native_body` 가드 추가, 도너 상속 본문 placeholder 텍스트는 `text_frame.clear()`로 비워 빈-placeholder 제거로 정리. 네이티브 다이어그램은 상류에서 이미 `not slide_bg` 가드로 상호배제됨(추가 변경 불필요). 비결함(HTML OFF/직접 네이티브/명시 배경) 경로는 `_suppress_native_body=False`라 no-op 보존.
    - `ai_engine/server.py:5126`/`5149` HTML 풀블리드 배경 경로와 `5170`~`5176` 네이티브 본문, `5278`~ `_eff_bg` 네이티브 다이어그램 경로 사이에 단일 결정 게이트 추가(가산적/디스크 패치, 앵커 유일성 검증)
    - 결정(제품 선호): 네이티브 본문/다이어그램 콘텐츠가 배치되는 슬라이드는 콘텐츠가 구워진 HTML 렌더를 slideBackground 로 설정하지 않음(장식 전용 배경만 허용) — 또는 콘텐츠가 구워진 풀블리드를 표현으로 채택 시 네이티브 본문/다이어그램 방출 skip
    - `5171` `if body_shape is None and bullets:` 블록에 "콘텐츠가 구워진 풀블리드 배경이 이미 본문 포함" 가드 추가
    - _Bug_Condition: isBugCondition(slide) — hasBakedFullbleed AND hasNativeOverlap (design)_
    - _Expected_Behavior: expectedBehavior(result) — 겹침<10% (design)_
    - _Preservation: Preservation Requirements (design) — 직접 네이티브/명시 배경/손실-0 보존_
    - _Requirements: 2.1, 2.3, 3.1, 3.2, 3.3, 3.4, 3.5_

  - [x] 3.2 네이티브 제목 중복 제거
    - **결과**: server.py 디스크 패치. 제목 호출(`_safe_set_title(s, ...)`)을 HTML 베이크 후보 판정(`_html_bake_eligible`) 뒤로 지연 — 베이크 후보가 아니면 즉시 설정(비결함 경로 보존), 베이크 후보면 게이트 이후로 미룬다. 게이트 직후: 베이크 실패(`not _rr_html_bg_set`) 시 네이티브 제목 폴백 설정(제목 손실 방지), 베이크 성공 시 도너 상속 제목 placeholder를 `_tph.text = ""`로 비워 빈-placeholder 제거로 정리 → 제목은 베이크된 PNG에만 1회 표시. 탐색 테스트의 `test_title_appears_exactly_once_per_slide` PASS로 검증.
    - 배경이 콘텐츠가 구워진 풀블리드일 때 `ai_engine/server.py:5113` `_safe_set_title` 네이티브 제목 폴백(`4018` add_textbox(0.6,0.3,12.1,1.0)) 억제 → 제목 1회 표시
    - _Bug_Condition: isBugCondition(slide) — 구워진 제목 + 네이티브 제목 중복 (design)_
    - _Expected_Behavior: titleAppearsExactlyOnce(result) (design)_
    - _Preservation: 제목 placeholder 정상 경로 동작 보존 (design)_
    - _Requirements: 2.2, 3.4_

  - [x] 3.3 경계 밖 도형 클램프/제거 후처리 패스 구현
    - **결과**: server.py 디스크 패치. `prs.save(output_path)` 직전에 슬라이드별 후처리 패스 추가 — 모든 슬라이드의 모든 도형 rect(EMU→인치 환산)를 `layout_geometry.within_bounds`로 검사, 경계 밖이면 `clamp_into_bounds`로 보정해 `_EmuClamp(Emu)`로 좌표 재설정. 경계 안 도형은 `within_bounds==True`라 clamp가 입력을 그대로 반환(no-op) → 비결함 도형 좌표 보존. 표지 장식 원(`native_diagram_pptx.py:1597` `OVAL top=-1.5` 및 우단 13.533 초과 보조 원)은 설계가 허용한 "표지 후처리 패스 일괄 보정"으로 경계 안 보정 → native_diagram_pptx.py 소스 변경 불필요. 탐색 테스트 `test_all_shapes_within_slide_bounds` PASS, 보존 테스트 경계 검사 회귀 0으로 검증.
    - PPTX 조립 마지막 단계에 슬라이드별 후처리 추가: 모든 도형 rect 를 `ai_engine/layout_geometry.py:345` `within_bounds` 로 검사, 경계 밖이면 `360` `clamp_into_bounds` 로 보정(순수 장식이면 제거)
    - 표지 장식 원 `ai_engine/native_diagram_pptx.py:1597` `c1` OVAL 의 `Inches(-1.5)` 음수 top 및 표지 배경 임베드(`server.py:4719`~`4754`) `PICTURE top=-1.39` 보정
    - _Bug_Condition: isBugCondition(slide) — offSlide (design)_
    - _Expected_Behavior: allShapesWithinBounds(result, (0,0,13.333,7.5)) (design)_
    - _Preservation: 경계 안 장식 도형은 변경 없음 (design)_
    - _Requirements: 2.4, 3.4, 3.5_

  - [x] 3.4 결함 조건 탐색 테스트가 이제 통과하는지 검증
    - **결과**: `./venv/bin/python -m pytest scripts/test_pptx_fullbleed_native_overlay_bug_condition.py -p no:cacheprovider -q` → **3 passed** (수정 전 3 failed → 수정 후 3 passed). 네이티브 텍스트↔구워진 배경 겹침 < 10%, 제목 1회, 경계 밖 도형 0 모두 충족 — 결함 1.1/1.2/1.4 수정 확인. 테스트 파일 미변경(동일 파일 재실행).
    - **Property 1: Expected Behavior** - 풀블리드-네이티브 충돌 및 경계밖 제거
    - **IMPORTANT**: 태스크 1의 동일 테스트를 재실행한다 — 새 테스트를 작성하지 말 것
    - 태스크 1의 테스트는 기대 동작을 인코딩하므로, 통과하면 기대 동작 충족이 확인된다
    - 태스크 1 탐색 테스트 실행 → **EXPECTED OUTCOME**: 테스트 PASS(결함 수정 확인)
    - _Requirements: 2.1, 2.2, 2.3, 2.4_

  - [x] 3.5 보존 테스트가 여전히 통과하는지 검증
    - **결과**: `./venv/bin/python -m pytest scripts/test_pptx_fullbleed_native_overlay_preservation_pbt.py -p no:cacheprovider -q` → **7 passed** (수정 전 7 passed 유지 — 회귀 0). PRES-1 직접 네이티브 경로, PRES-2 Vertex 손실-0, PRES-3 명시 imageFile/slideBackground 우선순위 + `_select_render_plan` 불변식, PRES-4 풀블리드 없는 네이티브 텍스트 레이아웃/여백, PRES-5 비결함 랜덤 덱 모두 보존 확인. 테스트 파일 미변경(동일 파일 재실행).
    - **Property 2: Preservation** - 비결함 입력 동작 보존
    - **IMPORTANT**: 태스크 2의 동일 테스트를 재실행한다 — 새 테스트를 작성하지 말 것
    - 태스크 2 보존 property 테스트 실행 → **EXPECTED OUTCOME**: 테스트 PASS(회귀 없음 확인)
    - 직접 네이티브 경로·손실-0 임베드·명시 배경·밀도 회귀 없음 확인
    - _Requirements: 3.1, 3.2, 3.3, 3.4, 3.5_

- [x] 4. 체크포인트 - 전체 테스트 통과 확인
  - 탐색·보존 테스트 및 관련 선행 스펙 테스트(pptx-quality-vertex-images, pptx-overlay-collision-fix, pptx-image-slot-placement-fix, slide_templates 밀도)가 모두 통과하는지 확인, 의문점 발생 시 사용자에게 문의
  - **결과(순수 검증, 소스/테스트 미수정)**: 전부 PASS — 회귀 0. 명령 `./venv/bin/python -m pytest <files> -p no:cacheprovider -q`(subprocess가 디스크 server.py 패치 본 읽음, 네트워크 0).
    - 신규 2종 (10 passed, 5.03s):
      - `scripts/test_pptx_fullbleed_native_overlay_bug_condition.py` → 3 passed
      - `scripts/test_pptx_fullbleed_native_overlay_preservation_pbt.py` → 7 passed
    - 선행 핵심 스펙 묶음 (116 passed, 16.88s):
      - pptx-quality-vertex-images: `test_pptx_quality_vertex_images_integration.py`, `_render_report.py`, `_fix_pbt.py`, `_bug_condition.py`, `_preservation_pbt.py`
      - pptx-overlay-collision-fix: `test_pptx_overlay_collision_fix_pbt.py`, `_bug_condition.py`, `_integration.py`, `_preservation_pbt.py`
      - pptx-image-slot-placement-fix: `test_pptx_image_slot_placement_fix_pbt.py`, `_integration.py`, `_bug_condition.py`, `_preservation_pbt.py`
      - slide_templates 밀도: `test_slide_templates_density.py`, `test_slide_density_parity_pbt.py`, `test_slide_density_safety_pbt.py`, `test_slide_density_clamp_pbt.py`
    - 선택(design-density-parity) 묶음 (14 passed, 21.17s):
      - `scripts/test_figure_slot_pbt.py`, `scripts/test_parity_scorer_pbt.py`, `scripts/test_density_parity_integration.py`
    - **합계: 140 passed, 0 failed, 0 skipped** — 선행 스펙 회귀 0. (warning은 Pillow getdata Deprecation 1건뿐, 기능 무관.) 26개 파일 모두 hermetic 정상 종료(Chrome/네트워크 hang·skip 없음).

## Notes

- server.py 에디터 버퍼는 STALE → 디스크 패치만, 앵커 count==1 검증 후 가산적 적용.
- 테스트는 heredoc/stdin 금지, 파일로만 작성. Chrome 필요 시 skip, 명령 hang 방지(timeout).
- 스티어링: LLM/operation 은 Bedrock Gateway 경유, 이미지 생성만 Vertex 예외, 프론트는 Electron+Vanilla JS.
- 디스크 라인 인용(grep 확인): server.py 3999/4018(`_safe_set_title`), 5113(title), 5126/5149(HTML 배경), 5170/5171/5174(네이티브 본문), 5278/5279(`_eff_bg`), 5266/5291(`_native_over_bg`), 4719~4754(표지 배경 임베드); native_diagram_pptx.py 1388(`build_native_cover`), 1597(음수-top OVAL); layout_geometry.py 345(`within_bounds`)/360(`clamp_into_bounds`).

## B방향 전환 작업 노트 (초고퀄 비주얼 우선 — 통짜 이미지 하나, 겹침0·중복0)

사용자가 **B 방향**을 확정했다: 각 슬라이드 = 통짜 이미지 하나 + 그 위에 겹치는
네이티브 오버레이 0. 편집가능 네이티브 라우팅(AE_NATIVE_LAYOUT_RENDER)은 B와
상충하므로 기본 OFF로 되돌린다. 실제 산출물 audit(8슬라이드) 결과, 본문 슬라이드는
이미 통짜였고 **표지와 다이어그램 슬라이드만** 통짜 배경 PICTURE + 네이티브 텍스트/
도형이 공존(겹침 100%)해서 발생한 결함을 제거했다.

디스크 패치(server.py 에디터 버퍼 STALE → 앵커 count==1 단언 후 디스크 패치 스크립트로
기록, 스크립트는 실행 후 삭제). py_compile + get_diagnostics 0.

- **수정1 — 표지 오버레이 억제 (통짜 하나만)**: `if os.environ.get("AE_DISABLE_NATIVE_COVER","")!="1"`
  게이트에 `and not _cover_bg_embedded` 추가(디스크 server.py 4864) → 통짜 표지 배경
  채택 시 `build_native_cover` 네이티브 표지 텍스트/도형 방출 억제. 추가로 통짜 표지
  배경이 임베드되면(`_cover_bg_embedded`) `_strip_text_over_fullbleed(cover)`로 제목/
  부제/날짜/네이티브 표지 셰이프를 모두 제거(server.py 4939). 배경(HTML 표지/Vertex)이
  제목을 포함한다고 신뢰.
- **수정2 — 다이어그램 오버레이 억제 (통짜 하나만)**: 풀블리드 배경 채택
  (`_native_over_bg=True`) 시 `if _native_over_bg:` 블록에서 `native_drawn=True`(폴백
  이미지/배경 단계 skip)로 두고 `_strip_text_over_fullbleed(s)`로 겹치는 편집 셰이프
  제거, 그리고 `build_native_diagram` 호출 조건에 `and not _native_over_bg` 추가
  (server.py 5596·5602) → 통짜 배경만 남긴다. 배경 없을 때만 네이티브 다이어그램.
- **수정3 — 네이티브 라우팅 기본 OFF (B 상충 제거)**: `AE_NATIVE_LAYOUT_RENDER` 기본값을
  `"1"!="0"`(기본 ON) → `"0"=="1"`(기본 OFF, 명시 옵트인=1만)로 반전(server.py 5285).
- **수정4 — Vertex 최대 활용 배선 점검**: Vertex 사전생성/backdrop 경로(`_vertex_pre`,
  `_select_render_plan` backdrop, `AE_PREFER_VERTEX_IMAGE` 기본 활성, `enabled`는
  옵트인+GOOGLE_APPLICATION_CREDENTIALS)는 이미 **열려 있음**(막힘 없음). backdrop
  채택 시 수정2로 네이티브가 억제돼 순수 통짜 Vertex가 된다. Vertex 미설정/실패 시
  HTML 베이크 통짜로 폴백(콘텐츠 손실0). structural 슬라이드는 의도적으로 네이티브
  우선(배경 없음 → 통짜 배경 부재 → 겹침 부재)로 보존 — 강제 Vertex화는 하지 않음.
- **수정5 — 경계 클램프**: 루프 후 전 슬라이드 경계 클램프 패스가 이미 존재
  (server.py ~5862, `clamp_into_bounds`). 추가로 표지/본문 슬라이드에 per-slide
  `_clamp_shapes_into_bounds` 호출(server.py 4942·5797, 경계 안이면 no-op·바이트 보존).

헬퍼 신규(server.py 3822 `_strip_text_over_fullbleed`, 3847 `_clamp_shapes_into_bounds`).

### 검증
- 신규 B 통합 테스트 `scripts/test_pptx_bmode_solid_integration.py`(4 passed): 실제
  `_tool_generate_pptx`를 기본 환경(AE_NATIVE_LAYOUT_RENDER 미설정=OFF)·Vertex OFF·
  HTML 성공 mock으로 헤르메틱 구동, 표지+본문+다이어그램(nativeDiagram 명시) 혼합 덱
  생성 후 **모든 슬라이드에서 통짜 배경 위 겹치는 네이티브 TEXT_BOX/AUTO_SHAPE(및
  텍스트 있는 PLACEHOLDER, area overlap≥10%) 셰이프 수 == 0**, 표지·다이어그램 각각
  통짜 배경 1장+겹침0, 전 도형 경계 안을 직접 단언. audit_native_density(편집텍스트≥1
  요구)는 B와 상충해 재사용하지 않음.
- 회귀(요구 목록 전부 PASS): pptx-quality-vertex-images(24), pptx-overlay-collision-fix(23),
  pptx-fullbleed-native-overlay + slide_templates_density(26), native_layout 순수
  단위/PBT(47).
- **의도된 반전(정직 보고)**: `pptx-native-density-render`의 realpath 통합 테스트
  9개(test_native_density_realpath_integration/nochrome)는 `AE_NATIVE_LAYOUT_RENDER`
  **기본 ON + 편집가능 텍스트≥1**을 전제로 단언 → B(수정3, 기본 OFF)가 의도적으로
  반전시킨 계약이라 FAIL한다. 순수 단위/PBT는 통과. 테스트는 고치지 않음(스펙 반전은
  사용자 확정 사항). 남은 한계: 실제 Vertex 렌더 품질은 옵트인+자격증명 환경에서
  사용자 재생성으로 확인 필요.
