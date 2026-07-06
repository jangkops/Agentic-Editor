"""기하 보정 PBT — spec: pptx-native-density-render, Tasks 12.1~12.3.

`ai_engine.native_layout_renderer.finalize_placement`(겹침 해소·경계 보정·제목 dedup)와
그 위임 대상인 `ai_engine.layout_geometry`(resolve_collisions/clamp_into_bounds/fit_within/
within_bounds/overlap_area/area) 의 순수 기하 불변식을 Hypothesis 로 검증한다.

검증 속성(각 Property 단일 테스트, 최소 100회 반복):

  * **Property 4 (Task 12.1)** — 모든 셰이프 쌍의 겹침률 < 10%.
    finalize_placement 적용 후, A안 겹침 검사 대상 쌍(텍스트 보유 + 비배경 이미지,
    텍스트 없는 장식 배경 도형 제외)의 겹침률 < 10%. 보정 불가 과밀 입력은 설계상
    OverlapError 가 정상 산출이므로 "겹침<10% 또는 OverlapError" 둘 중 하나면 통과.
    Req 2.1, 2.2, 2.3.

  * **Property 5 (Task 12.2)** — 보정 후 모든 셰이프는 슬라이드 경계 안.
    경계 안/밖/슬라이드보다 큰 rect → clamp 경로(_clamp_rect = fit_within→clamp_into_bounds /
    clamp_into_bounds) 적용 후 모든 셰이프가 within_bounds(eps=0.05). 예외 없는 불변식.
    Req 2.4, 3.1, 3.2, 3.3, 3.5.

  * **Property 6 (Task 12.3)** — 비결함 입력에 대한 no-op 보존.
    이미 경계 안·겹침 없는 비결함 입력 → clamp_into_bounds / resolve_collisions /
    finalize_placement 가 좌표를 변경 없이 반환. 예외 없는 불변식. Req 3.4, 9.5, 10.5.

헤르메틱 — 네트워크 0. 본 테스트는 순수 기하 함수만 구동하며(PlacedShape.rect 만 사용),
python-pptx 슬라이드/게이트웨이/Vertex/HTML 렌더를 호출하지 않는다.

Run (hermetic):
  ./venv/bin/python -m pytest scripts/test_layout_geometry_bounds_pbt.py -p no:cacheprovider -q

_Requirements: 2.1, 2.2, 2.3, 2.4, 3.1, 3.2, 3.3, 3.4, 3.5, 9.5, 10.5_
"""
from __future__ import annotations

import os
import sys

# Make ai_engine (repo root) importable.
_HERE = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.dirname(_HERE)
for _p in (_ROOT, _HERE):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import pytest  # noqa: E402
from hypothesis import given, settings, strategies as st, HealthCheck  # noqa: E402

# 순수 기하 함수/상수 (검증 대상) — 네트워크 0.
from ai_engine.layout_geometry import (  # noqa: E402
    within_bounds,
    clamp_into_bounds,
    resolve_collisions,
    area,
    overlap_area,
    SLIDE_RECT,
)

# 배치 보정기 + A안 분류 헬퍼 (검증 대상) — 네트워크 0.
from ai_engine.native_layout_renderer import (  # noqa: E402
    PlacedShape,
    finalize_placement,
    OverlapError,
    _participates_in_collision,
    _clamp_rect,
    _OVERLAP_THRESHOLD,
    DECORATIVE_BG_ROLES,
    _IMAGE_ROLES,
)

SLIDE_W_IN = 13.333
SLIDE_H_IN = 7.5
EPS = 0.05

# 역할 분류 — A안 검사 대상 구성을 위해 텍스트보유/비배경 이미지/장식 배경을 혼합 생성.
TEXT_ROLES = ["title", "body", "card", "note", "contact", "badge"]
IMAGE_ROLES = sorted(_IMAGE_ROLES)            # figure / image (비배경 이미지 → 검사 대상)
DECOR_ROLES = sorted(DECORATIVE_BG_ROLES)     # section_bar 등 (텍스트 없는 장식 배경 → 제외)


# ===========================================================================
# 생성기 (generators)
# ===========================================================================
def _f(lo, hi):
    """유한 float 전략(소수 3자리로 라운드해 float 잡음 억제)."""
    return st.floats(
        min_value=lo, max_value=hi, allow_nan=False, allow_infinity=False
    ).map(lambda x: round(x, 3))


# rect 전략 — 경계 안/밖/대형(슬라이드 초과) 포함. width/height 는 0 보다 크게.
_rect_strategy = st.tuples(
    _f(-3.0, 14.0),   # left  (음수=경계 밖 포함)
    _f(-3.0, 9.0),    # top   (음수=경계 밖 포함)
    _f(0.5, 16.0),    # width (16 > 13.333 → 대형 포함)
    _f(0.3, 9.0),     # height(9 > 7.5 → 대형 포함)
)


@st.composite
def _placed_shape(draw):
    """역할(텍스트보유/figure/장식배경 혼합)과 rect 를 가진 PlacedShape 1개."""
    kind = draw(st.sampled_from(["text", "image", "decor"]))
    rect = draw(_rect_strategy)
    if kind == "text":
        role = draw(st.sampled_from(TEXT_ROLES))
        # 제목 dedup 경로도 자연 커버되도록 텍스트는 소수 후보에서 추출.
        text = draw(st.sampled_from(["", "제목 A", "Title B", "  Title B  ", "본문"]))
        return PlacedShape(role=role, rect=rect, has_text=True, text=text)
    if kind == "image":
        role = draw(st.sampled_from(IMAGE_ROLES))
        return PlacedShape(role=role, rect=rect, has_text=False, text="")
    role = draw(st.sampled_from(DECOR_ROLES))
    return PlacedShape(role=role, rect=rect, has_text=False, text="")


_placed_list = st.lists(_placed_shape(), min_size=0, max_size=7)


# ---------------------------------------------------------------------------
# P6 전용 — 비결함(경계 안·비겹침) rect 집합 생성기.
# 수직 밴드를 분리 배정해 겹침 0·경계 안을 구조적으로 보장한다.
# ---------------------------------------------------------------------------
@st.composite
def _non_defective_rects(draw):
    n = draw(st.integers(min_value=1, max_value=5))
    usable_top, usable_bottom = 0.2, 7.3
    band_h = (usable_bottom - usable_top) / n
    rects = []
    for i in range(n):
        band_top = usable_top + i * band_h
        # 밴드 내부 여백을 둬서 인접 밴드와 절대 겹치지 않게 한다.
        top = draw(_f(band_top + 0.02, band_top + band_h * 0.2))
        height = draw(_f(0.2, max(0.2, band_h * 0.6)))
        left = draw(_f(0.2, 6.0))
        width = draw(_f(0.5, min(12.0, SLIDE_W_IN - left - 0.2)))
        rects.append((left, top, width, height))
    return rects


# ===========================================================================
# Property 4 (Task 12.1)
# ===========================================================================
# Feature: pptx-native-density-render, Property 4: 모든 셰이프 쌍의 겹침률은 10% 미만이다 —
# For any 셰이프 Rect 집합(텍스트-텍스트, 텍스트-이미지/도형 쌍 포함)에 대해,
# finalize_placement(내부 resolve_collisions) 적용 후 임의의 두 셰이프 a,b 는
# overlap_area(a,b) < 0.10 * min(area(a), area(b)) 를 만족한다(풀블리드 장식_배경 제외).
@settings(max_examples=200, suppress_health_check=[HealthCheck.too_slow])
@given(placed=_placed_list)
def test_property4_overlap_under_threshold(placed):
    """A안 검사 대상 쌍의 겹침률 < 10% (보정 불가 과밀은 OverlapError 가 정상 산출)."""
    try:
        result = finalize_placement(placed, slide_id="p4")
    except OverlapError as e:
        # 설계상 보정 불가 과밀 입력은 OverlapError(slide_id, 위반 쌍)가 정상 산출.
        assert e.slide_id == "p4"
        assert len(e.pairs) >= 1
        return

    # 성공 반환 시: A안 검사 대상(텍스트 보유 + 비배경 이미지, 장식 배경 제외) 쌍은 겹침<10%.
    participants = [ps for ps in result if _participates_in_collision(ps)]
    for i in range(len(participants)):
        for j in range(i + 1, len(participants)):
            a, b = participants[i].rect, participants[j].rect
            amin = min(area(a), area(b))
            if amin <= 0.0:
                continue
            ov = overlap_area(a, b)
            assert ov < _OVERLAP_THRESHOLD * amin + 1e-9, (
                f"겹침률 임계 초과: ov={ov} amin={amin} "
                f"a={participants[i].role}{a} b={participants[j].role}{b}"
            )

    # 장식 배경 도형은 검사 대상에서 제외됨을 함께 확인(A안).
    for ps in result:
        if ps.role in DECORATIVE_BG_ROLES:
            assert not _participates_in_collision(ps)


# ===========================================================================
# Property 5 (Task 12.2)
# ===========================================================================
# Feature: pptx-native-density-render, Property 5: 보정 후 모든 셰이프는 슬라이드 경계 안에 있다 —
# For any 셰이프 Rect 집합에 대해, 배치 보정(평행이동→fit_within→clamp_into_bounds) 적용 후
# 모든 셰이프는 within_bounds(r, SLIDE_RECT, eps=0.05) 를 만족한다. 경계 밖은 먼저 평행이동되며,
# 슬라이드_경계 크기를 초과해 평행이동만으로 못 들어오는 셰이프는 축소(fit_within) 후 배치된다.
@settings(max_examples=200, suppress_health_check=[HealthCheck.too_slow])
@given(placed=_placed_list)
def test_property5_all_within_bounds_after_clamp(placed):
    """경계 안/밖/대형 셰이프 → clamp 경로 적용 후 모두 경계 안(eps=0.05). 예외 없는 불변식."""
    for ps in placed:
        # _clamp_rect = (대형이면) fit_within 축소 → clamp_into_bounds 평행이동.
        clamped = _clamp_rect(ps.rect)
        assert within_bounds(clamped, SLIDE_RECT, eps=EPS), (
            f"경계 위반: {ps.role} {ps.rect} → {clamped}"
        )
        # 경계 보정은 슬라이드 크기 이하로 만든다(축소 경로 포함, Req 3.3).
        _, _, w, h = clamped
        assert w <= SLIDE_W_IN + EPS and h <= SLIDE_H_IN + EPS

        # 위임 대상 clamp_into_bounds 단일 함수도 임의 rect 를 경계 안으로 보정한다.
        assert within_bounds(clamp_into_bounds(ps.rect, SLIDE_RECT), SLIDE_RECT, eps=EPS)


# ===========================================================================
# Property 6 (Task 12.3)
# ===========================================================================
# Feature: pptx-native-density-render, Property 6: 비결함 입력에 대한 no-op 보존 —
# For any 이미 규칙을 만족하는(경계 안·겹침 임계 미만) 입력에 대해, 배치 보정 함수
# (clamp_into_bounds·resolve_collisions·finalize_placement)는 입력 좌표를 변경 없이 반환한다.
@settings(max_examples=200, suppress_health_check=[HealthCheck.too_slow])
@given(rects=_non_defective_rects())
def test_property6_noop_preservation(rects):
    """비결함 입력 → clamp/resolve/finalize 모두 좌표 불변(no-op). 예외 없는 불변식."""
    # 전제: 생성기가 경계 안·비겹침을 보장하는지 먼저 확인(전제 위반 시 검증 무의미).
    for r in rects:
        assert within_bounds(r, SLIDE_RECT, eps=EPS)
    for i in range(len(rects)):
        for j in range(i + 1, len(rects)):
            amin = min(area(rects[i]), area(rects[j]))
            if amin > 0.0:
                assert overlap_area(rects[i], rects[j]) < _OVERLAP_THRESHOLD * amin

    # 1) clamp_into_bounds — 경계 안 입력은 좌표 불변.
    for r in rects:
        assert clamp_into_bounds(r, SLIDE_RECT) == pytest.approx(r)

    # 2) resolve_collisions — 비겹침 입력은 좌표 불변(no-op 동등성).
    resolved = resolve_collisions(list(rects), threshold=_OVERLAP_THRESHOLD, axis="vertical")
    assert len(resolved) == len(rects)
    for got, exp in zip(resolved, rects):
        assert tuple(got) == pytest.approx(tuple(exp))

    # 3) finalize_placement — 비결함 PlacedShape 입력은 좌표 불변(서로 다른 텍스트 → dedup 영향 없음).
    placed = [
        PlacedShape(role="body", rect=r, has_text=True, text=f"줄 {k}")
        for k, r in enumerate(rects)
    ]
    out = finalize_placement(placed, slide_id="p6")
    assert len(out) == len(rects)
    for ps, exp in zip(out, rects):
        assert tuple(ps.rect) == pytest.approx(tuple(exp))


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(pytest.main([__file__, "-p", "no:cacheprovider", "-q"]))
