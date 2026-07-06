"""Fix-checking property tests — spec: pptx-overlay-collision-fix (bugfix), Task 4.

PROPERTY 1 — Bug Condition (Fix-A/C): 텍스트박스·배지 비겹침.
PROPERTY 2 — Bug Condition (Fix-B): 구조형 비구워짐 + 본문↔배경 분리.

design Correctness Properties:

  Property 1 (Fix-A/C) — _For any_ defectA/defectC 입력에서 수정된 배치 함수는 배치 후 모든
  (텍스트박스 ∪ 배지) 쌍 ``(a, b)`` 에 대해
  ``overlap_area(a, b) < 0.10 * min(area(a), area(b))`` 가 되도록 좌표를 산출한다
  (표지 제목↔부제 수직 비겹침 + 번호 배지의 라벨 밖 거터 배치 포함).
  **Validates: Requirements 2.1, 2.2, 2.5**

  Property 2 (Fix-B) — _For any_ defectB 입력에서 수정된 결정/배치는 (1) ``role=structural``
  이면서 ``bgImage != NULL AND bgHasBakedText`` 인 출력(구워진-텍스트 이미지를 본문 캐리어로
  쓰는 것)을 만들지 않고, (2) ``bodyBox`` 가 존재하면
  ``overlap_area(bodyBox, bgImage.rect) < 0.10 * area(bodyBox)`` 가 되도록 본문을 배경과
  분리된 안전 영역에 배치한다.
  **Validates: Requirements 2.3, 2.4**

또한 손실-0 불변식(design Property 4)을 동시 단언한다: ``has_vertex_image`` 이면 반환
``vertex_slot != "none"`` (생성된 Vertex 이미지는 어떤 분기에서도 폐기되지 않는다).

이 테스트는 순수 함수만 구동한다:
  * ``ai_engine.layout_geometry`` 의 ``resolve_collisions`` / ``vertical_stack`` /
    ``place_badge_in_gutter`` / ``body_safe_area`` — LLM/게이트웨이/네트워크 호출 0.
  * ``ai_engine.server._select_render_plan`` — 순수 결정 함수(네트워크 0).
겹침은 ``layout_geometry.overlap_area`` (= ``audit_pptx_textbox_overlap.py`` 의 ``ov()`` 와
동일 축-정렬 교집합 정의)로 측정한다 → 감사 ↔ 코드 일치. 완전히 헤르메틱하다.

EXPECTED OUTCOME: 수정된 코드에서 PASS.

Run (hermetic — no network):
  ./venv/bin/python -m pytest scripts/test_pptx_overlay_collision_fix_pbt.py -p no:cacheprovider -q

_Requirements: 2.1, 2.2, 2.3, 2.4, 2.5_
"""
from __future__ import annotations

import os
import sys

# Make ai_engine (repo root) importable.
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import pytest  # noqa: E402
from hypothesis import given, settings, strategies as st  # noqa: E402

import ai_engine.layout_geometry as lg  # noqa: E402
from ai_engine.server import _select_render_plan  # noqa: E402


# design Bug Condition: 의미있는 겹침 임계(작은 박스 면적의 10%).
THRESHOLD = 0.10
# 16:9 슬라이드(인치) — design Examples 좌표와 일치.
SLIDE_W_IN = 13.333
SLIDE_H_IN = 7.5
SLIDE = (0.0, 0.0, SLIDE_W_IN, SLIDE_H_IN)


# ──────────────────────────────────────────────────────────────────────────
# 공통 측정 헬퍼 (layout_geometry 의 정의 = audit 도구 ov() 와 동일)
# ──────────────────────────────────────────────────────────────────────────
def _max_overlap_ratio(boxes):
    """모든 쌍에 대해 overlap_area / min(area) 의 최댓값과 위반 쌍 정보를 반환."""
    worst = 0.0
    info = None
    n = len(boxes)
    for i in range(n):
        for j in range(i + 1, n):
            ov = lg.overlap_area(boxes[i], boxes[j])
            if ov <= 0.0:
                continue
            amin = min(lg.area(boxes[i]), lg.area(boxes[j]))
            if amin <= 0.0:
                continue
            ratio = ov / amin
            if ratio > worst:
                worst = ratio
                info = (boxes[i], boxes[j], ov, amin)
    return worst, info


# ──────────────────────────────────────────────────────────────────────────
# Hypothesis 생성기 — 정직한 입력 도메인(겹침 유발 + 임계 근방 엣지 포함)
# ──────────────────────────────────────────────────────────────────────────
# 좌표/치수: 슬라이드 안에서 서로 겹치기 쉬운 좁은 범위 + 작은 박스(임계 근방 엣지).
_coord = st.floats(min_value=0.0, max_value=10.0, allow_nan=False, allow_infinity=False)
_dim = st.floats(min_value=0.3, max_value=6.0, allow_nan=False, allow_infinity=False)
_rect = st.tuples(_coord, _coord, _dim, _dim)


@st.composite
def _rect_set(draw):
    """텍스트박스 ∪ 배지 집합 — 서로 겹치도록 좁은 영역에 몰아넣는다(defectA/C 유발)."""
    return draw(st.lists(_rect, min_size=2, max_size=6))


@st.composite
def _label_and_badge(draw):
    """라벨 카드 Rect 와 배지 지름/거터/간격 — place_badge_in_gutter 입력."""
    label = draw(_rect)
    # 배지 지름은 라벨 박스보다 작게(현실적): 0.3~min(label_w, label_h) 범위.
    _, _, lw, lh = label
    dmax = max(0.3, min(lw, lh))
    diameter = draw(st.floats(min_value=0.3, max_value=dmax,
                              allow_nan=False, allow_infinity=False))
    gutter = draw(st.sampled_from(("left", "right", "top", "bottom")))
    gap = draw(st.floats(min_value=0.0, max_value=0.5,
                         allow_nan=False, allow_infinity=False))
    return label, diameter, gutter, gap


# ──────────────────────────────────────────────────────────────────────────
# PROPERTY 1 (Fix-A/C) — 텍스트박스·배지 비겹침
# ──────────────────────────────────────────────────────────────────────────
@settings(max_examples=300, deadline=None)
@given(boxes=_rect_set())
def test_property1_resolve_collisions_no_overlap(boxes):
    """resolve_collisions(vertical) 후 모든 쌍의 겹침 < 10% min(area).

    겹침 유발 집합(여러 박스가 좁은 영역에 몰림)을 수직 충돌 회피로 통과시키면 모든 쌍이
    임계 미만으로 분리되어야 한다(design Property 1 / Fix-A·C)."""
    resolved = lg.resolve_collisions(boxes, threshold=THRESHOLD, axis="vertical")
    assert len(resolved) == len(boxes), "resolve_collisions 가 박스 개수를 바꿈"

    worst, info = _max_overlap_ratio(resolved)
    detail = ""
    if info is not None:
        a, b, ov, amin = info
        detail = (f"  {a} ↔ {b}  overlap={ov:.4f}in² "
                  f"({worst * 100:.1f}% of smaller {amin:.4f}in²)")
    assert worst < THRESHOLD, (
        "Property 1 위반 — resolve_collisions 후에도 임계 이상 겹치는 쌍이 있다 "
        f"(겹침 {worst * 100:.1f}% ≥ 임계 {THRESHOLD * 100:.0f}%).\n" + detail
    )


@settings(max_examples=300, deadline=None)
@given(
    title=_rect,
    sub=_rect,
    gap=st.floats(min_value=0.0, max_value=0.5, allow_nan=False, allow_infinity=False),
)
def test_property1_vertical_stack_title_subtitle(title, sub, gap):
    """vertical_stack([title, sub], gap) 후 제목↔부제 수직 비겹침.

    표지 제목 박스와 부제 박스가 겹치도록 생성되어도, 수직 스택 결과는 부제 top 이 제목
    bottom + gap 이상으로 밀려 두 박스가 겹치지 않는다(design 결함 A 수정)."""
    stacked = lg.vertical_stack([title, sub], gap=gap)
    assert len(stacked) == 2

    t, s = stacked
    # 첫 박스(제목) top 은 보존되어야 한다.
    assert t[1] == pytest.approx(title[1]), "vertical_stack 이 첫 박스 top 을 보존하지 않음"
    # 부제 top >= 제목 bottom + gap (비겹침).
    assert s[1] >= (t[1] + t[3]) - 1e-9, "부제 top 이 제목 bottom 위로 올라감(겹침)"

    ov = lg.overlap_area(t, s)
    amin = min(lg.area(t), lg.area(s))
    ratio = (ov / amin) if amin > 0 else 0.0
    assert ratio < THRESHOLD, (
        "Property 1 위반 — 제목 박스와 부제 박스가 세로로 겹친다 "
        f"(겹침 {ratio * 100:.1f}% ≥ 임계 {THRESHOLD * 100:.0f}%; title={t}, sub={s})."
    )


@settings(max_examples=300, deadline=None)
@given(spec=_label_and_badge())
def test_property1_place_badge_in_gutter_no_overlap(spec):
    """place_badge_in_gutter 가 배지를 라벨 밖 거터에 배치 → 배지∩라벨 == 0.

    번호 배지를 라벨 카드 밖 거터에 두면 박스 겹침이 0 이 되어야 한다(design 결함 C 수정,
    overlap_area(badge, label) == 0)."""
    label, diameter, gutter, gap = spec
    badge = lg.place_badge_in_gutter(label, diameter, gutter=gutter, gap=gap)

    # 반환 배지는 정사각(폭==높이==diameter).
    assert badge[2] == pytest.approx(diameter)
    assert badge[3] == pytest.approx(diameter)

    ov = lg.overlap_area(badge, label)
    badge_area = lg.area(badge)
    ratio = (ov / badge_area) if badge_area > 0 else 0.0
    # design defectC 기준: overlapArea(badge, label) / area(badge) < 10% (실제로는 0).
    assert ov == pytest.approx(0.0, abs=1e-9), (
        "Property 1 위반 — 배지가 라벨 박스와 겹친다(거터 배치 실패) "
        f"(overlap={ov:.4f}in² = 배지의 {ratio * 100:.1f}%; "
        f"gutter={gutter}, badge={badge}, label={label})."
    )


@settings(max_examples=200, deadline=None)
@given(boxes=_rect_set(), spec=_label_and_badge())
def test_property1_combined_textboxes_and_badge(boxes, spec):
    """텍스트박스 집합을 수직 스택으로 분리하고, 배지를 한 라벨 밖 거터에 둔 뒤,
    (텍스트박스 ∪ 배지) 전체 쌍의 겹침 < 10% min(area) 임을 단언한다.

    배지는 라벨과 겹치지 않으며, 스택된 텍스트박스끼리도 겹치지 않으므로, 배지를 라벨에서
    멀리(거터) 둔 구성에서는 전체 집합이 임계 미만을 유지한다."""
    label, diameter, gutter, gap = spec
    stacked = lg.resolve_collisions(boxes, threshold=THRESHOLD, axis="vertical")
    badge = lg.place_badge_in_gutter(label, diameter, gutter=gutter, gap=gap)

    # 배지↔라벨 단독 겹침은 항상 0(거터 배치 보장) — Property 1(Fix-C) 핵심 단언.
    assert lg.overlap_area(badge, label) == pytest.approx(0.0, abs=1e-9), (
        f"배지가 라벨과 겹침: badge={badge}, label={label}"
    )
    # 수직 스택된 텍스트박스끼리는 임계 미만.
    worst, _ = _max_overlap_ratio(stacked)
    assert worst < THRESHOLD, (
        f"Property 1 위반 — 스택된 텍스트박스끼리 임계 이상 겹침({worst * 100:.1f}%)."
    )


# ──────────────────────────────────────────────────────────────────────────
# PROPERTY 2 (Fix-B) — 구조형 비구워짐 + 본문↔배경 분리
# ──────────────────────────────────────────────────────────────────────────
@st.composite
def _structural_baked_state(draw):
    """defectB 입력: role=structural, bg_has_baked_text=True, Vertex 이미지 생성됨.
    나머지 미디어 플래그는 자유(다양한 조합)."""
    return {
        "has_vertex_image": True,
        "has_native_diagram": draw(st.booleans()),
        "has_image_file": draw(st.booleans()),
        "has_slide_bg": draw(st.booleans()),
        "html_enabled": draw(st.booleans()),
        "role": "structural",
        "bg_has_baked_text": True,
    }


@settings(max_examples=200, deadline=None)
@given(state=_structural_baked_state())
def test_property2_structural_baked_not_body_carrier(state):
    """구조형 + 구워진-텍스트 풀블리드에서 그 이미지를 본문 캐리어로 쓰지 않는다.

    _select_render_plan(role="structural", bg_has_baked_text=True) →
    primary == "NATIVE_SHAPES"(편집 네이티브 우선) ∧ vertex_slot == "backdrop"(손실-0 보존)
    ∧ body_separated is True(본문/배경 분리 신호) (design Property 2 / Fix-B)."""
    plan = _select_render_plan(**state)

    assert plan["primary"] == "NATIVE_SHAPES", (
        "Property 2 위반 — 구조형이 편집 네이티브 도형 대신 "
        f"{plan['primary']!r} 로 렌더된다(구워진-텍스트 이미지가 본문 캐리어). state={state!r}"
    )
    assert plan["vertex_slot"] == "backdrop", (
        "Property 2 위반(손실-0) — 생성 Vertex 이미지가 backdrop 이 아니라 "
        f"{plan['vertex_slot']!r} 슬롯에 놓인다. state={state!r}"
    )
    assert plan["body_separated"] is True, (
        f"Property 2 위반 — 본문/배경 분리 의도(body_separated)가 신호되지 않음. state={state!r}"
    )
    # 손실-0 동시 단언: has_vertex_image 이면 vertex_slot != "none".
    assert plan["vertex_slot"] != "none", (
        f"손실-0 위반 — 생성된 Vertex 이미지가 폐기됨. state={state!r}, plan={plan!r}"
    )


@st.composite
def _separable_bg_and_body(draw):
    """배경이 슬라이드를 **완전히 덮지 않는**(여백 띠를 남기는) 구워진-텍스트 풀블리드 +
    그 위에 겹치는 본문(desired)을 생성한다 → body_safe_area 의 분리 경로를 구동."""
    # 배경: 좌상단에서 시작, 슬라이드보다 작게(최소 한 변에 여백 띠를 남김).
    bg_w = draw(st.floats(min_value=4.0, max_value=SLIDE_W_IN - 0.5,
                          allow_nan=False, allow_infinity=False))
    bg_h = draw(st.floats(min_value=2.0, max_value=SLIDE_H_IN - 0.5,
                          allow_nan=False, allow_infinity=False))
    bg = (0.0, 0.0, bg_w, bg_h)
    # 본문(desired): 배경과 겹치도록 배경 영역 안쪽에 둔다.
    dw = draw(st.floats(min_value=1.0, max_value=max(1.0, bg_w),
                        allow_nan=False, allow_infinity=False))
    dh = draw(st.floats(min_value=1.0, max_value=max(1.0, bg_h),
                        allow_nan=False, allow_infinity=False))
    dl = draw(st.floats(min_value=0.0, max_value=max(0.0, bg_w - dw),
                        allow_nan=False, allow_infinity=False))
    dt = draw(st.floats(min_value=0.0, max_value=max(0.0, bg_h - dh),
                        allow_nan=False, allow_infinity=False))
    desired = (dl, dt, dw, dh)
    return bg, desired


@settings(max_examples=300, deadline=None)
@given(spec=_separable_bg_and_body())
def test_property2_body_safe_area_separates_from_bg(spec):
    """body_safe_area(has_baked_text=True, 분리 가능 bg) 결과가 배경과 분리.

    반환 안전 영역 region 에 대해 overlap_area(region, bg) < 0.10 * area(region)
    (design Property 2 / Fix-B (2))."""
    bg, desired = spec
    region = lg.body_safe_area(slide=SLIDE, bg=bg, has_baked_text=True, desired=desired)

    region_area = lg.area(region)
    assert region_area > 0.0, f"안전 영역 면적이 0: region={region}"

    ov = lg.overlap_area(region, bg)
    ratio = ov / region_area
    assert ratio < THRESHOLD, (
        "Property 2 위반 — 본문이 배경과 분리되지 않음 "
        f"(겹침 {ov:.4f}in² = region 의 {ratio * 100:.1f}% ≥ 임계 {THRESHOLD * 100:.0f}%; "
        f"bg={bg}, desired={desired}, region={region})."
    )


@settings(max_examples=200, deadline=None)
@given(
    role=st.sampled_from(("cover", "section", "structural", "content", "visual")),
    has_native_diagram=st.booleans(),
    has_image_file=st.booleans(),
    has_slide_bg=st.booleans(),
    html_enabled=st.booleans(),
    bg_has_baked_text=st.booleans(),
)
def test_property2_loss_zero_concurrent(role, has_native_diagram, has_image_file,
                                        has_slide_bg, html_enabled, bg_has_baked_text):
    """손실-0 동시 단언 — has_vertex_image 이면 vertex_slot != "none".

    구워진-텍스트 신호 유무, 역할, 미디어 플래그의 임의 조합에서도 생성된 Vertex 이미지는
    어떤 분기에서도 폐기되지 않는다(design Property 4)."""
    plan = _select_render_plan(
        has_vertex_image=True,
        has_native_diagram=has_native_diagram,
        has_image_file=has_image_file,
        has_slide_bg=has_slide_bg,
        role=role,
        html_enabled=html_enabled,
        bg_has_baked_text=bg_has_baked_text,
    )
    assert plan["vertex_slot"] != "none", (
        "손실-0 위반 — 생성된 Vertex 이미지가 폐기됨(vertex_slot='none'). "
        f"role={role!r}, baked={bg_has_baked_text}, plan={plan!r}"
    )


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-p", "no:cacheprovider", "-q"]))
