# PPTX 오버레이/충돌 수정 Bugfix Design

## Overview

에디터가 생성한 PPTX 산출물에서 세 가지 오버레이/충돌 결함이 확정되었다(결함 A/B/C).
세 결함은 모두 **배치 좌표 계산**과 **렌더 경로 결정**에서 발생하며, 실제 코드 정독으로
근본 원인을 라인 단위로 특정했다.

- **결함 A(표지 제목↔부제 겹침)** — `ai_engine/native_diagram_pptx.py`의
  `build_native_cover`에서 부제 top(`sub_y`)을 제목 박스의 실제 높이가 아니라 **고정 오프셋**
  으로 계산해, 제목 박스 하단(top+height)이 부제 top보다 아래로 내려가 두 편집 텍스트 박스가
  세로로 겹친다.
- **결함 B(구조/흐름도 구워진 이미지 + 본문 겹침)** — `_classify_slide_role` /
  `_select_render_plan`이 "텍스트가 구워진 풀블리드 AI 이미지"를 구조형 본문 캐리어로 쓰는
  경우를 차단하지 못하고, 풀블리드 배경(`_eff_bg`) 위에 본문 영역이 큰 면적으로 겹쳐 올라간다.
- **결함 C(번호 배지↔라벨 겹침)** — `native_diagram_pptx.py`의 `_badge`가 배지 중심을 카드
  (=라벨 박스) **내부**의 작은 인셋으로 계산해, 배지 사각형이 라벨 박스와 박스 기준 100%
  포개진다(글리프는 `text_inset_left_in`으로 피하지만 박스 겹침은 그대로 남는다).

근본 전략은 **충돌 검출/회피 기하를 순수 함수로 추출**(`ai_engine/layout_geometry.py`,
신규·additive)하고, `server.py` / `native_diagram_pptx.py`의 placement 코드가 이를 호출하도록
바꾸는 것이다. 순수 함수가 PBT(속성 기반 테스트)의 단일 대상이 된다. 동시에 이전 스펙
`pptx-quality-vertex-images`의 손실-0 불변식(생성된 Vertex 이미지 미폐기), 게이트웨이 제약
(LLM은 Bedrock Gateway, Vertex는 이미지 경로 한정), 비버그 입력 바이트 보존을 모두 유지한다.

## Glossary

- **Bug_Condition (C)**: `isBugCondition(S) = defectA OR defectB OR defectC` — 한 슬라이드의
  배치 요소 집합/역할/렌더 상태가 오버레이·충돌 결함을 가지는 조건(bugfix.md 형식화).
- **Property (P)**: 버그 입력에 대한 기대 동작 — 배치 후 (텍스트박스 ∪ 배지) 쌍의 겹침이
  작은 박스 면적의 10% 미만이고, 구조형은 구워진-텍스트 이미지 캐리어가 아니며, 본문이 배경과
  분리되는 것.
- **Preservation**: 버그 조건이 아닌 입력에 대한 기존 동작(배치 좌표 바이트 보존, Vertex
  손실-0, 게이트웨이 제약, 템플릿 상속, 네이티브 구조형 도형)이 변경되지 않는 것.
- **`Rect`**: `(left, top, width, height)` 인치 사각형. 좌표는 PowerPoint EMU(`914400 EMU/in`)
  로 환산되어 비교된다(감사 도구 `scripts/audit_pptx_textbox_overlap.py`의 `ov()`와 동일 기준).
- **`overlap_area(a, b)`**: 두 `Rect`의 교집합 면적(in²). `area(r)`: `Rect` 면적(in²).
- **`build_native_cover`**: `native_diagram_pptx.py`의 표지 네이티브 조립 함수(Line 1304~).
  제목/부제/아이브로우/KPI 카드를 편집 가능 도형으로 배치한다.
- **`_badge(cx, cy, d_v, label, fill_hex)`**: `native_diagram_pptx.py`의 번호 원형 배지(편집
  가능 OVAL) 생성기(Line 482~). `cx`는 v-unit(0~100) 중심 좌표.
- **`_classify_slide_role` / `_select_render_plan`**: `server.py`의 역할 분류기(Line 3094~)와
  손실 없는 렌더 플랜 결정 함수(Line 3150~). 이전 스펙에서 도입된 순수 결정 함수.
- **`_eff_bg` / `_native_over_bg`**: `server.py` 슬라이드 루프에서 풀블리드 배경(`slide_bg` 또는
  공유 본문 배경 `_dp_body_bg`) 위에 편집 가능 네이티브 도형을 올리는 합성 경로(Line 5229~).
- **bgHasBakedText**: 풀블리드 배경 이미지에 텍스트(한글 라벨/노드)가 래스터로 구워져 있어
  편집 불가한 상태. 결함 B의 핵심 신호.

## Bug Details

### Bug Condition

bugfix.md의 `isBugCondition(S) = defectA OR defectB OR defectC`를 그대로 사용한다. `overlapArea`,
`area`는 감사 도구와 동일한 축-정렬 사각형 교집합 면적이다.

**Formal Specification:**
```
FUNCTION isBugCondition(S)
  INPUT: S = SlideLayoutState {
           role        : enum,          // cover | section | structural | content | visual
           textBoxes   : list of Rect,  // 편집 텍스트 박스(제목/부제/라벨/본문 카드)
           badges      : list of Rect,  // 번호 배지 등 장식 텍스트 요소
           bgImage     : optional Image,// 풀블리드 배경 이미지(rect 포함)
           bgHasBakedText : bool,       // 배경 이미지에 텍스트가 구워져 있음
           bodyBox     : optional Rect  // 큰 본문 텍스트/콘텐츠 영역
         }
  OUTPUT: boolean

  // 결함 A: 편집 텍스트 박스끼리 의미있는 겹침(표지 제목↔부제 포함)
  defectA := EXISTS (a, b) IN pairs(textBoxes) SUCH THAT
               overlapArea(a, b) >= 0.10 * min(area(a), area(b))

  // 결함 C: 번호 배지가 라벨 텍스트 박스와 의미있는 겹침
  defectC := EXISTS badge IN badges, label IN textBoxes SUCH THAT
               overlapArea(badge, label) >= 0.10 * area(badge)

  // 결함 B: 구조형이 구워진-텍스트 이미지로 렌더되거나, 풀블리드 배경 위 큰 본문 겹침
  defectB := (role = structural AND bgImage != NULL AND bgHasBakedText)
             OR (bgImage != NULL AND bodyBox != NULL
                 AND overlapArea(bodyBox, bgImage.rect) >= 0.10 * area(bodyBox))

  RETURN defectA OR defectB OR defectC
END FUNCTION
```

### Examples (실제 좌표로 재현)

- **결함 A(표지)** — `build_native_cover`에서 `SH=7.5` 기준:
  `eb_y = SH*0.30 = 2.25`, `title_y = eb_y + 0.55 = 2.8`. 제목 박스
  `add_textbox(Inches(margin_x=1.15), Inches(2.8), Inches(SW-margin_x-1.0=11.18), Inches(2.0))`
  → 제목 박스 **하단 = 2.8 + 2.0 = 4.8**. 부제 `sub_y = title_y + 1.05 = 3.85`(`title_pt>=40`),
  부제 박스 `_txt(1.17, 3.85, 10.58, 1.0)`. 제목 하단(4.8) > 부제 top(3.85) →
  세로 겹침 ≈ 0.95in × 10.58 ≈ **10.05in²**(작은 박스의 95%). 기대: 겹침 < 10%.
- **결함 C(번호 배지)** — twocol 분기: 카드(라벨 박스)
  `_card(x0, vy_t, col_w=45v(≈5.45in), ch, ... text_inset_left_in=0.95)`, 배지
  `_badge(x0 + 3.4, vy_t - ch/2.0, 5.6, str(n), sh)`. `_badge`는
  `_x(cx - d_v/2.0)`로 중심 배치 → 배지 사각형이 카드(`x0..x0+col_w`) **내부**에 완전히 포함
  → 배지∩라벨 = 배지 면적의 **100%**(≈0.46in²). block 분기(`text_inset_left_in=0.95`)도 동일.
  기대: 겹침 < 배지 면적의 10%.
- **결함 B(구조/흐름도)** — content/structural 슬라이드에 풀블리드 배경
  (`_eff_bg`, `add_picture(_cand_bg, Inches(0), Inches(0), 13.333×7.5)`)이 깔리고, 본문 영역
  `region ≈ (0.5, 1.75, 9.0, 4.95)`이 그 위에 올라가 본문↔배경 겹침 = 본문 면적의 **100%**.
  또한 구조형 콘텐츠가 텍스트 구워진 4K AI 이미지로 렌더되면 편집 불가 + 이중 텍스트.
  기대: 구조형은 편집 네이티브 도형, 본문은 배경과 분리(겹침 < 10%).
- **엣지(비버그)** — 제목 박스 하단이 부제 top 이하이고, 배지가 라벨 박스 밖 거터에 있고,
  본문이 배경과 분리된 슬라이드 → `isBugCondition` 거짓 → 좌표 바이트 그대로 유지되어야 함.

## Expected Behavior

### Preservation Requirements

**Unchanged Behaviors (회귀 방지):**
- 결함 A/B/C 어느 조건에도 해당하지 않는 슬라이드의 배치/렌더 좌표는 기존과 **동일(바이트
  보존)**해야 한다(Req 3.1).
- 비구조형(cover/content/visual) 슬라이드에서 생성된 Vertex 이미지는 이전 스펙
  `pptx-quality-vertex-images`의 손실-0 불변식대로 **폐기되지 않고** 역할에 맞는 슬롯(비주얼/
  배경/HTML 이미지 슬롯)에 보존·임베드된다(Req 3.2).
- Vertex 이미지 생성이 비활성/실패(쿼터/서킷브레이커)면 네이티브 다이어그램/카드로 폴백하고
  기존 회귀 스위트를 통과한다(Req 3.3).
- LLM/operation JSON 생성은 Bedrock Gateway 경유로만, Vertex는 이미지 생성 경로
  (`ai_engine/vertex_image_module.py`)에서만 호출한다(Req 3.4, gateway.md 예외 조항 준수).
- 텍스트가 겹치지 않고 본문이 배경과 분리된 정상 슬라이드는 템플릿 스타일 상속
  (`styleProfile`/`templatePath`)과 HTML 풀블리드 고밀도 경로를 그대로 적용한다(Req 3.5).
- 진짜 구조형 다이어그램(흐름/트리/아키텍처)은 편집 가능 네이티브 도형으로 계속 렌더한다
  (Req 3.6).

**Scope:**
`isBugCondition(S)`가 거짓인 모든 입력은 본 수정의 영향을 받지 않는다. 신규 충돌 회피 기하
함수는 **겹침이 임계 이상일 때만** 좌표를 조정하고, 임계 미만이면 입력 좌표를 그대로 반환하여
바이트 동일성을 보장한다.

> 실제 "올바른 동작"의 정의는 아래 **Correctness Properties**의 Property 1/2에 기술한다. 본
> 섹션은 변경되어서는 안 되는 것에 집중한다.

## Hypothesized Root Cause

코드 정독으로 확인한 원인은 다음과 같다(라인 인용).

1. **결함 A — 부제 top의 고정 오프셋 계산 (`native_diagram_pptx.py` `build_native_cover`)**:
   - `margin_x = 1.15`
   - `eb_y = SH * 0.30`(SH=7.5 → 2.25)
   - `title_y = eb_y + 0.55`(→ 2.8)
   - 제목 박스: `add_textbox(Inches(margin_x), Inches(title_y), Inches(SW - margin_x - 1.0), Inches(2.0))`
     — **height가 2.0in으로 고정**.
   - 부제: `sub_y = title_y + (1.05 if title_pt >= 40 else 0.9)` —
     **부제 top을 `title_y + 1.05`(또는 0.9)로 고정**, 제목 박스의 실제 height(2.0)나 렌더된
     텍스트 높이를 반영하지 않는다.
   - 결과: 제목 박스 하단 `title_y + 2.0 = 4.8` > 부제 top `3.85` → 세로 겹침. 즉 수직 스택이
     "이전 박스의 bottom 이후에 다음 박스를 둔다"는 비겹침 규칙을 따르지 않는다.

2. **결함 C — 배지 중심을 라벨 박스 내부로 계산 (`native_diagram_pptx.py` `_badge` 및 호출부)**:
   - `_badge`: `add_shape(MSO_SHAPE.OVAL, _x(cx - d_v/2.0), _y(cy + d_v/2.0), _w(d_v), ...)`
     — 중심 `cx` 기준 배치.
   - twocol 호출: `_badge(x0 + 3.4, vy_t - ch/2.0, 5.6, ...)` + 카드
     `_card(x0, vy_t, col_w, ch, ... text_inset_left_in=0.95)`. 배지 좌단 `x0 + 0.6`v,
     우단 `x0 + 6.2`v로 **카드(`x0..x0+col_w`) 내부에 완전히 포함**.
   - `text_inset_left_in=0.95`는 글리프 잘림만 막을 뿐, 배지 **박스**와 라벨 **박스**의 기하
     겹침(감사 도구가 측정)은 100%로 남는다.
   - 근본: 배지를 라벨 박스 밖 **거터**에 두는 좌표 계산이 없다.

3. **결함 B — 구워진-텍스트 이미지 신호 부재 + 본문-배경 분리 부재 (`server.py`)**:
   - `_classify_slide_role`(Line 3094): structural = `{flow, tree, architecture}`만. 그러나
     **`bgHasBakedText`(구워진 텍스트) 신호를 입력으로 받지 않아**, 구조형 콘텐츠가 텍스트
     구워진 풀블리드 AI 이미지로 렌더되는 경우를 차단하지 못한다.
   - `_select_render_plan`(Line 3150): `slide_bg` 존재 시 `primary=HTML`, native 존재 시
     `NATIVE_SHAPES` 등으로 분기하나, **본문 박스와 배경 이미지의 기하 겹침 임계**를 고려하지
     않는다.
   - `_native_over_bg` 합성(Line 5229~): `_eff_bg = slide_bg or _dp_body_bg`, native_diag가
     있으면 `add_picture(_cand_bg, Inches(0), Inches(0), 풀블리드)` 후 그 위에 본문 영역을
     올린다 → 본문↔배경 겹침 100%. 백드롭/스크림과 "구워진-텍스트 캐리어"를 구분하지 않는다.
   - 근본: (a) 구조형 + 구워진-텍스트 이미지를 본문 캐리어로 쓰지 않게 하는 결정 규칙 부재,
     (b) 본문을 배경과 분리하는 안전 영역 계산 부재.

## Correctness Properties

Property 1: Bug Condition (Fix-A/C) — 텍스트 박스·배지 비겹침

_For any_ 입력에서 버그 조건의 결함 A 또는 C가 성립하면(`isBugCondition`이 defectA/defectC로
true), 수정된 배치 함수는 배치 후 모든 (텍스트박스 ∪ 배지) 쌍 `(a, b)`에 대해
`overlapArea(a, b) < 0.10 * min(area(a), area(b))`가 되도록 좌표를 산출한다(표지 제목↔부제
수직 비겹침 + 번호 배지의 라벨 밖 거터 배치 포함).

**Validates: Requirements 2.1, 2.2, 2.5**

Property 2: Bug Condition (Fix-B) — 구조형 비구워짐 + 본문↔배경 분리

_For any_ 입력에서 버그 조건의 결함 B가 성립하면(`isBugCondition`이 defectB로 true), 수정된
결정/배치는 (1) `role = structural`이면서 `bgImage != NULL AND bgHasBakedText`인 출력을 만들지
않고(구워진-텍스트 이미지를 본문 캐리어로 쓰지 않음), (2) `bodyBox`가 존재하면
`overlapArea(bodyBox, bgImage.rect) < 0.10 * area(bodyBox)`가 되도록 본문을 배경과 분리된 안전
영역에 배치한다.

**Validates: Requirements 2.3, 2.4**

Property 3: Preservation — 비버그 입력 바이트 보존

_For any_ 입력에서 버그 조건이 거짓이면(`isBugCondition`이 false), 수정된 배치/결정 함수는
원본 함수와 동일한 결과를 산출한다(`F(S) = F'(S)`). 충돌 회피 기하 함수는 겹침이 임계 미만인
입력에 대해 입력 좌표를 그대로 반환한다(no-op 동등성).

**Validates: Requirements 3.1, 3.5, 3.6**

Property 4: Preservation — Vertex 손실-0 불변식 보존

_For any_ 슬라이드 미디어 상태에 대해, 본 수정 후에도 `_select_render_plan`은 생성된 Vertex
이미지를 폐기하지 않는다(`has_vertex_image`이면 `vertex_slot != "none"`). 구조형에서 구워진-
텍스트 이미지를 본문 캐리어로 쓰지 않더라도, 생성된 이미지는 배경/장식(backdrop) 슬롯으로
보존된다. Vertex 비활성/실패 시 네이티브 폴백이 유지된다.

**Validates: Requirements 3.2, 3.3**

Property 5: Preservation — 게이트웨이 제약 보존

_For any_ 입력에서, LLM/operation JSON 생성 호출은 Bedrock Gateway 경유로만 발생하고 Vertex는
이미지 생성 경로에서만 호출된다(이미지 외 작업에서 Vertex 호출 0). 신규 충돌 회피 기하 함수는
순수 계산이며 어떤 네트워크/모델 호출도 하지 않는다.

**Validates: Requirements 3.4**

## Fix Implementation

가정: 위 근본 원인 분석이 옳다(Testing Strategy의 탐색 단계에서 먼저 확인/반증한다).
모든 변경은 **additive**(기존 분기/좌표 보존)이며, 충돌이 임계 이상일 때만 동작한다.

### 0. 신규 순수 기하 모듈 — `ai_engine/layout_geometry.py` (PBT 대상)

LLM/게이트웨이/네트워크 호출이 전혀 없는 순수 함수만 모은 신규 모듈. `server.py`와
`native_diagram_pptx.py`가 placement 직전에 호출한다. 좌표 단위는 인치(`Rect`).

```
# Rect = (left, top, width, height)  # 인치, 모두 float

FUNCTION area(r: Rect) -> float
  RETURN max(0.0, r.width) * max(0.0, r.height)

FUNCTION overlap_area(a: Rect, b: Rect) -> float
  ix := max(0.0, min(a.left+a.width, b.left+b.width) - max(a.left, b.left))
  iy := max(0.0, min(a.top+a.height, b.top+b.height) - max(a.top, b.top))
  RETURN ix * iy
  # 주: scripts/audit_pptx_textbox_overlap.py 의 ov() 와 동일 정의(감사 ↔ 코드 일치).

FUNCTION vertical_stack(boxes: list[Rect], *, gap: float = 0.0,
                        max_bottom: float | None = None) -> list[Rect]
  # 박스를 입력 순서대로 위→아래 비겹침으로 재배치. 각 박스 top 은 직전 박스
  # bottom(+gap) 이상으로 밀어 내린다. 첫 박스의 top 은 보존.
  # 반환: 입력과 동일 개수의 Rect(겹침이 이미 없으면 입력 그대로 — 보존).

FUNCTION resolve_collisions(boxes: list[Rect], *, threshold: float = 0.10,
                            axis: str = "vertical", bounds: Rect | None = None)
                            -> list[Rect]
  # 모든 쌍 (a,b) 에 대해 overlap_area(a,b) < threshold*min(area(a),area(b)) 가
  # 되도록 최소 이동(기본 수직). 임계 미만이면 입력 좌표 그대로 반환(no-op 동등성).

FUNCTION place_badge_in_gutter(label: Rect, diameter: float, *,
                               gutter: str = "left", gap: float = 0.05) -> Rect
  # 배지(정사각 diameter)를 라벨 박스 '밖' 거터에 둔다.
  #   gutter="left":  badge.right = label.left - gap  (배지가 라벨 왼쪽 밖)
  # 반환 배지 Rect 는 overlap_area(badge, label) == 0 을 보장.

FUNCTION body_safe_area(slide: Rect, bg: Rect | None, *, has_baked_text: bool,
                        desired: Rect) -> Rect
  # 본문 영역을 배경 이미지와 분리. bg 가 풀블리드 구워진-텍스트면 desired 를
  # 배경과 겹침 < threshold 인 안전 영역으로 축소/이동(또는 호출부가 네이티브
  # 캐리어로 전환하도록 신호). bg 가 없거나 백드롭(스크림/흰 패널)이면 desired 보존.
```

핵심 불변식: `resolve_collisions`/`vertical_stack`/`body_safe_area`는 **겹침이 임계 미만이면
입력을 그대로 반환**한다(Property 3 바이트 보존의 근거).

### 1. 결함 A — 표지 제목/부제 수직 스택 (`native_diagram_pptx.py` `build_native_cover`)

`sub_y`의 고정 오프셋을 제거하고, 제목 박스의 실제 점유 높이를 반영해 비겹침으로 스택한다.

```
# 현재(겹침): sub_y = title_y + (1.05 if title_pt >= 40 else 0.9)
# 변경: 제목 박스 height 를 렌더 텍스트 추정 높이로 산출하고 vertical_stack 으로 부제 배치
title_rect := Rect(margin_x, title_y, SW - margin_x - 1.0, est_title_h)   # est_title_h: 폰트/줄수 기반
sub_rect_desired := Rect(margin_x + 0.02, title_y + est_title_h, SW - margin_x - 1.6, 1.0)
[title_rect, sub_rect] := vertical_stack([title_rect, sub_rect_desired], gap=0.12)
# 부제 top = max(기존 sub_y, title_rect.bottom + gap) 로 보장 → 결함 A 제거.
```
- 제목 박스 height를 고정 2.0 대신 `est_title_h`(폰트 pt·줄수 기반 추정, 최대 2.0 캡)로 두고,
  부제 top을 `vertical_stack`이 산출한 비겹침 좌표로 설정한다.
- 겹침이 없던 짧은 제목(예: 한 줄)에서는 결과가 기존과 동일하게 유지되도록 추정 높이 하한을
  둔다(보존).

### 2. 결함 C — 번호 배지를 라벨 밖 거터로 (`native_diagram_pptx.py` `_badge` 호출부)

배지 중심 좌표를 카드 내부 인셋(`x0 + 3.4` 등)에서 **라벨 박스 왼쪽 거터**로 옮긴다.

```
# 현재(100% 겹침): _badge(x0 + 3.4, vy_t - ch/2.0, 5.6, str(n), sh)  # 카드 내부
# 변경: 라벨 카드 Rect 를 인치로 환산 → place_badge_in_gutter → 배지 중심 v-unit 역산
label_rect_in := to_inches(Rect_v(x0, vy_t, col_w, ch))      # _x/_y/_w/_h 역산
badge_in := place_badge_in_gutter(label_rect_in, diameter=badge_d_in, gutter="left", gap=0.05)
# 거터 확보를 위해 카드 좌단을 badge 폭만큼 우측으로 들이거나, 좌측 액센트 바/여백 영역을 활용.
```
- 거터 폭이 부족하면 카드 묶음 전체를 배지 폭만큼 우측 들여 배치(레이아웃 전역 시프트)하여
  배지를 라벨 박스 밖에 둔다. `text_inset_left_in`은 더 이상 배지-겹침 회피 용도가 아니므로
  거터 모델과 일관되게 정리(겹침이 이미 없으면 동작 보존).
- twocol/block/flow(세로) 등 `_badge`를 라벨 박스 내부에 두던 모든 호출부에 동일 적용.

### 3. 결함 B — 구워진-텍스트 신호 + 본문 안전 영역 (`server.py`)

#### 3a. `_classify_slide_role` / `_select_render_plan` 결정 규칙 보강

- `bgHasBakedText` 신호를 추가 입력으로 받아, **`role == structural` 또는 구워진-텍스트
  풀블리드 이미지가 본문 캐리어가 될 상황**에서는 그 이미지를 본문 캐리어로 선택하지 않는다.
- 대신 구조형은 편집 가능 네이티브 도형(`NATIVE_SHAPES`)을 주 렌더러로 유지하고, 생성된
  이미지는 `vertex_slot = "backdrop"`(장식)으로만 보존한다(손실-0 유지 — Property 4).
- 반환 플랜에 본문/배경 분리 의도를 명시(예: `body_separated: bool`). additive 키이므로 기존
  출력 바이트 불변.

#### 3b. `_native_over_bg`/`_eff_bg` 합성에서 본문 안전 영역 적용

```
# 현재: _eff_bg 풀블리드 add_picture 후 본문 region 을 그 위에 그대로 올림(겹침 100%)
# 변경: 본문 region 을 body_safe_area 로 통과
body_region := body_safe_area(slide=Rect(0,0,13.333,7.5), bg=bg_rect,
                              has_baked_text=bg_has_baked_text, desired=body_region)
# - bg 가 백드롭(스크림/흰 콘텐츠 패널로 분리)인 경우: 기존 동작 보존(흰 패널이 분리 보장).
# - bg 가 구워진-텍스트 풀블리드인데 본문이 그 위에 큰 면적으로 올라가는 경우:
#   본문을 안전 영역으로 축소/이동하거나, 이미지를 backdrop 슬롯으로만 두고 본문은 네이티브로.
```
- 손실-0 보존: 어떤 분기에서도 생성된 Vertex 이미지를 폐기하지 않는다(backdrop/비주얼 슬롯
  유지). 게이트웨이 제약 무관(순수 좌표 계산).

### 4. 보존 가드 유지

- `imageFile`/`slideBackground`가 caller로부터 명시된 슬라이드의 우선순위는 변경 없음.
- Vertex 비활성/실패면 `_vertex_pre`가 비어 모든 분기가 기존 네이티브/HTML 폴백으로 진행
  (Req 3.3).
- 신규 기하 함수는 겹침 임계 미만이면 no-op → 비버그 입력 바이트 보존(Req 3.1, Property 3).

## Testing Strategy

### Validation Approach

두 단계로 진행한다. 먼저 미수정 코드에서 결함 A/B/C를 재현하는 반례를 표면화하고(탐색),
그다음 수정이 결함을 제거하며 비버그 입력 동작을 보존하는지 검증한다(Fix/Preservation). 모든
테스트는 **헤르메틱**(네트워크 0)으로 실행한다. 충돌 회피·결정 함수는 순수 함수이므로 모킹
없이 그대로 PBT 대상이 되고, 통합 테스트는 Vertex/HTML 렌더/게이트웨이를 목으로 고정한다.
기존 점검 도구(`scripts/audit_pptx_textbox_overlap.py`, `audit_pptx_overlap.py`,
`audit_pptx_images.py`, `audit_pptx_baked_text.py`)를 재현·검증에 재사용한다.
`heredoc`/`stdin` 금지 — 테스트는 파일로 작성해 `./venv/bin/python -m pytest <file> -q`로 실행.

### Exploratory Bug Condition Checking

**Goal**: 수정 전에 결함 A/B/C를 재현하는 반례를 표면화하고 근본 원인을 확인/반증한다.

**Test Plan**: `build_native_cover`로 표지를 조립한 PPTX를 만들고 `audit_pptx_textbox_overlap.py`
기준으로 제목↔부제 겹침을 측정(결함 A). twocol/block 네이티브 카드 슬라이드를 만들고 배지↔라벨
박스 겹침을 측정(결함 C). 풀블리드 배경 + 본문 region 슬라이드로 본문↔배경 겹침과
`audit_pptx_baked_text.py` 구워진-텍스트 추정을 측정(결함 B). 미수정 코드에서 실패 관찰.

**Test Cases**:
1. **표지 제목/부제(결함 A)**: `title_pt>=40` 긴 제목 → 제목 하단 4.8 > 부제 top 3.85, 겹침
   ≈10.05in² 관찰(실패 예상).
2. **번호 배지(결함 C)**: twocol 6항목 → 각 배지가 라벨 박스와 100% 겹침 관찰(실패 예상).
3. **구조/흐름도(결함 B-1)**: structural 슬라이드가 구워진-텍스트 풀블리드 이미지로 렌더됨을
   `audit_pptx_baked_text.py`로 관찰(실패 예상).
4. **본문↔배경(결함 B-2)**: 풀블리드 배경 위 본문 region(0.5,1.75,9.0,4.95) 겹침 100% 관찰.

**Expected Counterexamples**:
- 제목 박스 하단이 부제 top보다 아래 → 텍스트 박스 세로 겹침.
- 배지 사각형이 라벨 박스 내부 → 박스 겹침 100%.
- 본문 region이 풀블리드 배경 rect에 완전히 포함 → 본문↔배경 겹침 100%.

### Fix Checking

**Goal**: 버그 조건이 참인 입력에 대해 수정 함수가 기대 동작(겹침 < 임계, 구조형 비구워짐,
본문 분리)을 산출하는지 검증한다.

**Pseudocode:**
```
FOR ALL S WHERE isBugCondition(S) DO
  S' := layout_fixed(S)
  ASSERT FOR ALL (a,b) IN pairs(S'.textBoxes ∪ S'.badges):
           overlap_area(a,b) < 0.10 * min(area(a), area(b))            # P1
  ASSERT NOT (S'.role = structural AND S'.bgImage != NULL AND S'.bgHasBakedText)  # P2
  ASSERT (S'.bodyBox = NULL OR S'.bgImage = NULL
          OR overlap_area(S'.bodyBox, S'.bgImage.rect) < 0.10 * area(S'.bodyBox)) # P2
END FOR
```

### Preservation Checking

**Goal**: 버그 조건이 거짓인 입력에 대해 수정 함수가 원본과 동일한 결과를 산출하는지 검증한다.

**Pseudocode:**
```
FOR ALL S WHERE NOT isBugCondition(S) DO
  ASSERT layout_original(S) = layout_fixed(S)        # 바이트/좌표 동등
END FOR
FOR ALL media_state DO
  ASSERT _select_render_plan(...).vertex_slot != "none" WHEN has_vertex_image  # 손실-0
END FOR
```

**Testing Approach**: 보존 검증에 PBT를 권장한다 — 입력 도메인 전반을 자동 생성해 엣지(임계
근방 겹침, 단일 항목, 빈 부제 등)를 포착하고, 비버그 입력에서 좌표 불변성을 강하게 보장한다.

**Test Plan**: 미수정 코드에서 비버그 입력의 좌표/플랜을 관찰한 뒤, 그 동작을 그대로 단언하는
PBT를 작성한다. 충돌 회피 기하 함수는 "겹침 임계 미만 → 입력 그대로 반환"을 직접 단언한다.

**Test Cases**:
1. **표지 비겹침 보존**: 이미 비겹침인 짧은 제목/부제 → `vertical_stack` 결과 == 입력.
2. **배지 거터 보존**: 라벨 밖에 이미 있는 배지 → `place_badge_in_gutter` no-op 동등.
3. **본문 분리 보존**: 백드롭(스크림/흰 패널) 경로 → 기존 본문 region 유지.
4. **손실-0 보존**: 모든 미디어 상태에서 `_select_render_plan`의 `vertex_slot != "none"`.
5. **게이트웨이 제약**: 기하 함수가 네트워크/모델 호출을 하지 않음(임포트/호출 그래프 단언).

### Unit Tests

- `area`/`overlap_area`의 경계값(0 면적, 접점만 닿음, 완전 포함, 분리).
- `vertical_stack`: 겹친 2~3박스 → 비겹침, 이미 비겹침 → 입력 그대로.
- `place_badge_in_gutter`: 배지∩라벨 == 0, 거터 부족 시 시프트 결과 검증.
- `body_safe_area`: 구워진-텍스트 풀블리드 → 분리, 백드롭 → 보존.
- `build_native_cover` 산출 표지의 제목↔부제 겹침 < 10%(결함 A 회귀).

### Property-Based Tests

- **Property 1 (Fix-A/C)**: 무작위 텍스트박스/배지 집합(겹침 유발 포함) → 수정 배치 후 모든
  쌍의 겹침 < 10% min(area).
- **Property 2 (Fix-B)**: 무작위 role/bg/baked-text/bodyBox 조합에서 defectB 입력 →
  structural+구워진-텍스트 캐리어 부재 + 본문↔배경 겹침 < 10%.
- **Property 3 (Preservation 바이트)**: 비버그 입력 → 수정 배치 == 원본 배치(no-op 동등).
- **Property 4 (손실-0)**: 모든 미디어 상태 → `_select_render_plan.vertex_slot != "none"`
  (has_vertex_image일 때).
- **Property 5 (게이트웨이)**: 기하/결정 함수 호출 경로에 Vertex/LLM 호출 0.

#### 헤르메틱 PBT 파일 계획 (신규, `scripts/` — 기존 `test_pptx_quality_vertex_images_*` 컨벤션 준수)

- `scripts/test_pptx_overlay_collision_bug_condition.py`
  — 탐색: 미수정 경로 재현(결함 A/B/C 반례). `audit_*` 함수 재사용.
- `scripts/test_pptx_overlay_collision_fix_pbt.py`
  — Property 1·2: `layout_geometry`의 `resolve_collisions`/`vertical_stack`/
  `place_badge_in_gutter`/`body_safe_area`가 defect 입력에서 겹침 < 임계 보장.
- `scripts/test_pptx_overlay_collision_preservation_pbt.py`
  — Property 3·4·5: 비버그 입력 좌표 보존(no-op 동등), `_select_render_plan` 손실-0,
  네트워크 0 단언.

각 테스트는 `from hypothesis import given, settings, strategies as st`로 입력을 생성하고
`from ai_engine.layout_geometry import ...`, `from ai_engine.server import _select_render_plan`
를 임포트해 순수 함수만 구동한다(네트워크 없음).
실행: `./venv/bin/python -m pytest scripts/test_pptx_overlay_collision_*.py -p no:cacheprovider -q`.

### Integration Tests

- 헤르메틱 `_tool_generate_pptx` 통합: HTML 렌더/Vertex `generate`/게이트웨이를 목으로 고정해
  표지·twocol(번호 배지)·structural(흐름도)·풀블리드 배경 본문이 섞인 덱을 생성하고,
  `audit_pptx_textbox_overlap.py`/`audit_pptx_baked_text.py`/`audit_pptx_overlap.py`로
  (a) 텍스트박스·배지 겹침 < 10%, (b) 구조형 구워진-텍스트 캐리어 부재, (c) 본문↔배경 분리,
  (d) 생성된 Vertex 이미지 미폐기(손실-0)를 검증.
- 컨텍스트 전환: 템플릿 적용/무템플릿 양쪽에서 스타일 상속과 충돌 회피가 모두 동작.
- 회귀: 이전 스펙 `pptx-quality-vertex-images` 스위트가 그대로 통과(손실-0/게이트웨이 보존).
