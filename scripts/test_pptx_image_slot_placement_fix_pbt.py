"""Fix-checking property tests — spec: pptx-image-slot-placement-fix (bugfix), Task 4.

PROPERTY 1 — Bug Condition (P1/P2/P3): 이미지 슬롯 배정 결함 D1/D2/D3 의 좌표·슬롯·중복
결정을 순수 함수(``ai_engine.layout_geometry`` 신규 함수)로 검증한다.
PROPERTY 2 — Preservation (P4): 비버그 Rect(이미 경계 안/정합/풀블리드 0)에서 모든 함수가
입력을 그대로 반환/True(no-op 동등성, 보강 단언).

design Correctness Properties:

  Property 3 (D3) — _For any_ 무작위 Rect(음수 top/left, region 초과 포함) →
  ``clamp_into_bounds(r)`` 결과는 항상 ``within_bounds(result, SLIDE)`` 참.
  무작위 region/natural → ``fit_within(region, w, h)`` 는 항상 region 안(음수 off 없음).
  **Validates: Requirements 2.3**

  Property 2 (D2) — _For any_ 무작위 슬롯/이미지 → ``slot_image_fits(slot, w, h)`` 는
  ``is_small_slot(slot) ∧ is_large_image(w, h)`` 일 때 **정확히** False 이고, 재배정
  (콘텐츠/풀블리드 region) 후에는 소형 슬롯에 대형 이미지가 존재하지 않는다.
  **Validates: Requirements 2.2**

  Property 1 (D1) — _For any_ 무작위 풀블리드 후보 다수(existing_count 0..N) →
  ``fullbleed_guard`` 적용 후 임베드되는 풀블리드 개수 ≤ 1
  (``fullbleed_guard(0)==True``, ``fullbleed_guard(>=1)==False``).
  **Validates: Requirements 2.1**

  Property 4 (Preservation) — 비버그 입력(경계 안·정합·풀블리드 0)에서 모든 함수가 입력을
  그대로 반환/True(no-op 동등성).

검증 대상은 순수 함수만 구동한다(``ai_engine.layout_geometry`` 의 ``is_fullbleed`` /
``is_large_image`` / ``is_small_slot`` / ``within_bounds`` / ``clamp_into_bounds`` /
``fit_within`` / ``fullbleed_guard`` / ``slot_image_fits``) — LLM/게이트웨이/Vertex/HTML
호출 0(네트워크 0). 임계 상수(``LARGE_PX=1024`` / ``SMALL_SLOT_IN=0.5`` /
``BOUNDS_EPS=0.05`` / ``SLIDE_RECT=(0,0,13.333,7.5)``)는 design §0 와 동일하며, 임계 근방
엣지(1024px / 0.5in / EPS=0.05)를 명시적으로 포함한다.

EXPECTED OUTCOME: 수정된 코드에서 PASS.

Run (hermetic — no network):
  ./venv/bin/python -m pytest scripts/test_pptx_image_slot_placement_fix_pbt.py -p no:cacheprovider -q

_Requirements: 2.1, 2.2, 2.3_
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
from hypothesis import given, settings, strategies as st  # noqa: E402

import ai_engine.layout_geometry as lg  # noqa: E402

# design §0 임계 상수 (audit 실측 기준 확정) — 모듈 상수와 일치해야 한다.
LARGE_PX = 1024
SMALL_SLOT_IN = 0.5
BOUNDS_EPS = 0.05
SLIDE_W_IN = 13.333
SLIDE_H_IN = 7.5
SLIDE = (0.0, 0.0, SLIDE_W_IN, SLIDE_H_IN)

# 재배정 대상 region (server.py task 3.3 의 콘텐츠 승격 region 과 동일 성격 — 대형 region).
CONTENT_REGION = (1.5, 1.7, 10.33, 5.2)


def _rect_coords(r):
    """layout_geometry Rect를 (left, top, width, height) 튜플로 정규화."""
    if hasattr(r, "left") and hasattr(r, "top") and hasattr(r, "width") and hasattr(r, "height"):
        return (float(r.left), float(r.top), float(r.width), float(r.height))
    seq = list(r)
    return (float(seq[0]), float(seq[1]), float(seq[2]), float(seq[3]))


# ──────────────────────────────────────────────────────────────────────────
# 모듈 상수 sanity — 테스트 상수와 design §0 가 일치하는지 확인
# ──────────────────────────────────────────────────────────────────────────
def test_module_constants_match_design():
    """layout_geometry 의 임계 상수가 design §0 (1024px/0.5in/EPS=0.05/슬라이드) 와 일치."""
    assert lg.LARGE_PX == LARGE_PX
    assert lg.SMALL_SLOT_IN == pytest.approx(SMALL_SLOT_IN)
    assert lg.BOUNDS_EPS == pytest.approx(BOUNDS_EPS)
    assert tuple(lg.SLIDE_RECT) == pytest.approx(SLIDE)


# ──────────────────────────────────────────────────────────────────────────
# Hypothesis 생성기 — 정직한 입력 도메인(버그 유발 + 임계 근방 엣지 포함)
# ──────────────────────────────────────────────────────────────────────────
# 음수 top/left, region 초과까지 포함하는 광범위 Rect(D3 클램프 도메인).
_wild_rect = st.tuples(
    st.floats(min_value=-5.0, max_value=18.0, allow_nan=False, allow_infinity=False),   # left
    st.floats(min_value=-5.0, max_value=12.0, allow_nan=False, allow_infinity=False),   # top
    st.floats(min_value=0.05, max_value=20.0, allow_nan=False, allow_infinity=False),   # width
    st.floats(min_value=0.05, max_value=12.0, allow_nan=False, allow_infinity=False),   # height
)

# region (양수, 슬라이드 안에서 의미있는 크기) — fit_within 도메인.
_region = st.tuples(
    st.floats(min_value=0.0, max_value=8.0, allow_nan=False, allow_infinity=False),
    st.floats(min_value=0.0, max_value=4.0, allow_nan=False, allow_infinity=False),
    st.floats(min_value=0.5, max_value=10.0, allow_nan=False, allow_infinity=False),
    st.floats(min_value=0.5, max_value=6.0, allow_nan=False, allow_infinity=False),
)

# natural 픽셀/치수(이미지) — 0 초과.
_natural = st.floats(min_value=1.0, max_value=8000.0, allow_nan=False, allow_infinity=False)

# 슬롯 — 임계(0.5in) 근방 포함하도록 작은 값에 가중.
_slot = st.tuples(
    st.floats(min_value=0.0, max_value=6.0, allow_nan=False, allow_infinity=False),
    st.floats(min_value=0.0, max_value=4.0, allow_nan=False, allow_infinity=False),
    st.floats(min_value=0.05, max_value=6.0, allow_nan=False, allow_infinity=False),
    st.floats(min_value=0.05, max_value=4.0, allow_nan=False, allow_infinity=False),
)

# 픽셀 크기 — 임계(1024) 근방을 명시적으로 포함.
_px = st.integers(min_value=1, max_value=4096)


# ──────────────────────────────────────────────────────────────────────────
# PROPERTY 3 (D3) — clamp_into_bounds / fit_within 항상 경계 안
# ──────────────────────────────────────────────────────────────────────────
@settings(max_examples=400, deadline=None)
@given(r=_wild_rect)
def test_property3_clamp_into_bounds_always_within(r):
    """무작위 Rect(음수 top/left·경계 초과 포함) → clamp_into_bounds 결과는 항상 within_bounds.

    design Property 3 (D3): 음수/초과를 클램프/축소로 제거해 모든 PICTURE 가 슬라이드 경계
    `(0,0,13.333,7.5)` 안에 위치한다."""
    out = lg.clamp_into_bounds(r, SLIDE)
    assert lg.within_bounds(out, SLIDE, eps=BOUNDS_EPS), (
        f"Property 3 위반 — clamp_into_bounds 결과가 경계 밖: in={r} out={_rect_coords(out)}"
    )
    # 사후조건: 클램프된 크기는 슬라이드를 넘지 않는다.
    _, _, ow, oh = _rect_coords(out)
    assert ow <= SLIDE_W_IN + 1e-9, f"clamp 결과 폭이 슬라이드 초과: {ow}"
    assert oh <= SLIDE_H_IN + 1e-9, f"clamp 결과 높이가 슬라이드 초과: {oh}"


@settings(max_examples=400, deadline=None)
@given(region=_region, nw=_natural, nh=_natural)
def test_property3_fit_within_inside_region_no_negative_off(region, nw, nh):
    """무작위 region/natural → fit_within 은 항상 region 안(음수 off 없음, draw <= region).

    design Property 3 (D3): off_t/off_l 음수 불가 → 경계 밖으로 나가는 부분 이미지 제거."""
    out = lg.fit_within(region, nw, nh)
    ol, ot, ow, oh = _rect_coords(out)
    rl, rt, rw, rh = _rect_coords(region)
    tol = 1e-6

    # draw 크기 <= region (오버플로 없음).
    assert ow <= rw + tol, f"draw 폭이 region 초과 — out_w={ow} region_w={rw}"
    assert oh <= rh + tol, f"draw 높이가 region 초과 — out_h={oh} region_h={rh}"
    # region 경계 안(음수 off 불가).
    assert ol >= rl - tol, f"off_l 가 region 좌단보다 작음 — {ol} < {rl}"
    assert ot >= rt - tol, f"off_t 가 region 상단보다 작음 — {ot} < {rt}"
    assert ol + ow <= rl + rw + tol, f"우단이 region 우단 초과 — {ol + ow} > {rl + rw}"
    assert ot + oh <= rt + rh + tol, f"하단이 region 하단 초과 — {ot + oh} > {rt + rh}"


@settings(max_examples=200, deadline=None)
@given(region=_region, nw=_natural, nh=_natural)
def test_property3_fit_within_then_clamp_within_slide(region, nw, nh):
    """fit_within 결과를 다시 clamp_into_bounds 에 통과시켜도 슬라이드 경계 안(전 경로 보장).

    server.py task 3.4 의 합성(fit_within → clamp_into_bounds) 순서를 그대로 모사한다.
    단, region 자체가 슬라이드 밖에 있을 수 있으므로 최종 clamp 후 within_bounds 를 단언."""
    fitted = lg.fit_within(region, nw, nh)
    clamped = lg.clamp_into_bounds(fitted, SLIDE)
    assert lg.within_bounds(clamped, SLIDE, eps=BOUNDS_EPS), (
        f"Property 3 위반 — fit_within→clamp 결과가 경계 밖: region={region} "
        f"natural=({nw},{nh}) fitted={_rect_coords(fitted)} clamped={_rect_coords(clamped)}"
    )


# ──────────────────────────────────────────────────────────────────────────
# PROPERTY 2 (D2) — slot_image_fits 정확성 + 재배정 후 소형 슬롯에 대형 이미지 없음
# ──────────────────────────────────────────────────────────────────────────
@settings(max_examples=400, deadline=None)
@given(slot=_slot, px_w=_px, px_h=_px)
def test_property2_slot_image_fits_exact_predicate(slot, px_w, px_h):
    """slot_image_fits 는 (소형 슬롯 ∧ 대형 이미지) 일 때 정확히 False, 그 외 True.

    design Property 2 (D2): 버그 조합(is_small_slot ∧ is_large_image)을 정확히 식별한다."""
    is_small = lg.is_small_slot(slot, small_in=SMALL_SLOT_IN)
    is_large = lg.is_large_image(px_w, px_h, large_px=LARGE_PX)
    fits = lg.slot_image_fits(slot, px_w, px_h)
    expected = not (is_small and is_large)
    assert fits is expected, (
        "Property 2 위반 — slot_image_fits 가 버그 조합을 정확히 식별하지 못함: "
        f"slot={slot} px=({px_w},{px_h}) small={is_small} large={is_large} "
        f"fits={fits} expected={expected}"
    )


@settings(max_examples=400, deadline=None)
@given(slot=_slot, px_w=_px, px_h=_px)
def test_property2_reassignment_removes_small_slot_large_image(slot, px_w, px_h):
    """slot_image_fits==False(버그) 이면 콘텐츠 region 으로 재배정 후 소형 슬롯에 대형 이미지 없음.

    design Property 2 (D2): 대형 이미지는 풀블리드/콘텐츠 영역으로 재배정한다. 재배정 후의
    유효 슬롯(콘텐츠 region)에는 (is_small_slot ∧ is_large_image) 조합이 존재하지 않는다."""
    fits = lg.slot_image_fits(slot, px_w, px_h)
    # 호출부 모사: fits 면 슬롯 유지, 아니면 콘텐츠 region 으로 승격(server.py task 3.3).
    effective_slot = slot if fits else CONTENT_REGION

    # 재배정 후 유효 슬롯에는 대형 이미지가 소형 슬롯에 있는 경우가 없어야 한다.
    bug_remains = lg.is_small_slot(effective_slot, small_in=SMALL_SLOT_IN) and lg.is_large_image(
        px_w, px_h, large_px=LARGE_PX
    )
    assert not bug_remains, (
        "Property 2 위반 — 재배정 후에도 소형 슬롯에 대형 이미지가 남음: "
        f"orig_slot={slot} effective_slot={effective_slot} px=({px_w},{px_h})"
    )
    # 재배정된 콘텐츠 region 은 정합(slot_image_fits True) 이어야 한다.
    assert lg.slot_image_fits(effective_slot, px_w, px_h) is True, (
        f"재배정된 콘텐츠 region 이 여전히 부정합: region={effective_slot} px=({px_w},{px_h})"
    )


# ──────────────────────────────────────────────────────────────────────────
# PROPERTY 1 (D1) — fullbleed_guard 적용 후 풀블리드 ≤ 1
# ──────────────────────────────────────────────────────────────────────────
@settings(max_examples=200, deadline=None)
@given(candidates=st.integers(min_value=0, max_value=12))
def test_property1_fullbleed_guard_at_most_one(candidates):
    """풀블리드 후보 다수(candidates 개) → fullbleed_guard 로 직렬 임베드 시 총 ≤ 1.

    design Property 1 (D1): 슬라이드당 풀블리드 배경 ≤ 1. 호출부는 임베드 직전
    fullbleed_guard(현재 풀블리드 개수)를 검사하고, True 일 때만 임베드한다."""
    embedded = 0
    for _ in range(candidates):
        if lg.fullbleed_guard(embedded):  # 현재까지 임베드된 풀블리드 개수로 가드
            embedded += 1
    assert embedded <= 1, (
        f"Property 1 위반 — 가드 적용 후 풀블리드가 {embedded}장(>1): 후보={candidates}"
    )
    # 후보가 1개 이상이면 정확히 1장 임베드(손실-0: 첫 후보는 반드시 배경으로).
    if candidates >= 1:
        assert embedded == 1, f"후보 {candidates}개 중 정확히 1장 임베드돼야 함(임베드={embedded})"


def test_property1_fullbleed_guard_boundary():
    """가드 경계: existing 0 → True(첫 임베드 허용), >=1 → False(스킵)."""
    assert lg.fullbleed_guard(0) is True
    assert lg.fullbleed_guard(1) is False
    assert lg.fullbleed_guard(5) is False


# ──────────────────────────────────────────────────────────────────────────
# PROPERTY 4 (Preservation, 보강) — 비버그 입력 no-op 동등성
# ──────────────────────────────────────────────────────────────────────────
@settings(max_examples=300, deadline=None)
@given(
    left=st.floats(min_value=0.0, max_value=9.0, allow_nan=False, allow_infinity=False),
    top=st.floats(min_value=0.0, max_value=5.0, allow_nan=False, allow_infinity=False),
    width=st.floats(min_value=0.1, max_value=4.0, allow_nan=False, allow_infinity=False),
    height=st.floats(min_value=0.1, max_value=2.0, allow_nan=False, allow_infinity=False),
)
def test_property4_clamp_noop_for_in_bounds(left, top, width, height):
    """이미 경계 안인 rect → clamp_into_bounds 는 입력 그대로 반환(no-op 동등성, P4)."""
    w = min(width, SLIDE_W_IN - left)
    h = min(height, SLIDE_H_IN - top)
    r = (left, top, w, h)
    assert lg.within_bounds(r, SLIDE, eps=BOUNDS_EPS), f"전제 실패 — 경계 안이어야 함: {r}"
    out = _rect_coords(lg.clamp_into_bounds(r, SLIDE))
    assert all(abs(a - b) <= 1e-9 for a, b in zip(r, out)), (
        f"P4 위반 — 경계 안 rect 가 clamp 후 변경됨: in={r} out={out}"
    )


@settings(max_examples=200, deadline=None)
@given(
    sw=st.floats(min_value=0.55, max_value=8.0, allow_nan=False, allow_infinity=False),
    sh=st.floats(min_value=0.55, max_value=6.0, allow_nan=False, allow_infinity=False),
    px_w=_px,
    px_h=_px,
)
def test_property4_non_small_slot_always_fits(sw, sh, px_w, px_h):
    """소형 슬롯이 아닌(둘 다 0.5in 초과) 정합 슬롯 → slot_image_fits 항상 True(P4)."""
    slot = (1.0, 1.0, sw, sh)
    assert lg.is_small_slot(slot, small_in=SMALL_SLOT_IN) is False
    assert lg.slot_image_fits(slot, px_w, px_h) is True, (
        f"P4 위반 — 비-소형 슬롯이 부정합 처리됨: slot={slot} px=({px_w},{px_h})"
    )


@settings(max_examples=200, deadline=None)
@given(
    slot_in=st.floats(min_value=0.05, max_value=SMALL_SLOT_IN, allow_nan=False, allow_infinity=False),
    px=st.integers(min_value=1, max_value=LARGE_PX - 1),
)
def test_property4_small_slot_small_image_fits(slot_in, px):
    """소형 슬롯이라도 이미지가 LARGE_PX 미만(대형 아님)이면 정합 → True(P4, 임계 근방 1023px)."""
    slot = (1.0, 1.0, slot_in, slot_in)
    assert lg.is_small_slot(slot, small_in=SMALL_SLOT_IN) is True
    assert lg.is_large_image(px, px, large_px=LARGE_PX) is False
    assert lg.slot_image_fits(slot, px, px) is True, (
        f"P4 위반 — 소형 슬롯 + 1024px 미만 이미지는 정합(True)이어야 함: slot={slot} px={px}"
    )


@settings(max_examples=200, deadline=None)
@given(region=_region)
def test_property4_fit_within_small_natural_centered(region):
    """natural 이 region 보다 작으면 원본 크기 유지 + region 중앙배치(확대 없음, P4)."""
    rl, rt, rw, rh = region
    nat_w = rw * 0.4
    nat_h = rh * 0.4
    ol, ot, ow, oh = _rect_coords(lg.fit_within(region, nat_w, nat_h))
    # 확대하지 않으므로 draw 크기 == natural(<= region).
    assert ow == pytest.approx(nat_w, abs=1e-6), f"natural 보다 확대됨: draw_w={ow} nat_w={nat_w}"
    assert oh == pytest.approx(nat_h, abs=1e-6), f"natural 보다 확대됨: draw_h={oh} nat_h={nat_h}"
    # region 중앙.
    assert abs((ol + ow / 2.0) - (rl + rw / 2.0)) <= 1e-4
    assert abs((ot + oh / 2.0) - (rt + rh / 2.0)) <= 1e-4


# ──────────────────────────────────────────────────────────────────────────
# 임계 경계값 단위 테스트 (1024px / 0.5in / EPS=0.05)
# ──────────────────────────────────────────────────────────────────────────
def test_threshold_large_px_boundary():
    """is_large_image 경계: 1023→False, 1024→True(한 변만 1024여도 True)."""
    assert lg.is_large_image(1023, 1023) is False
    assert lg.is_large_image(1024, 10) is True
    assert lg.is_large_image(10, 1024) is True


def test_threshold_small_slot_boundary():
    """is_small_slot 경계: 0.5in 이하 양변→True, 한 변이 0.5 초과→False."""
    assert lg.is_small_slot((0.0, 0.0, 0.5, 0.5)) is True
    assert lg.is_small_slot((0.0, 0.0, 0.5, 0.51)) is False
    assert lg.is_small_slot((0.0, 0.0, 0.25, 0.25)) is True


def test_threshold_bounds_eps_boundary():
    """within_bounds 경계: EPS=0.05 만큼의 음수/초과는 허용, 초과분은 불허."""
    # top = -0.05 → 정확히 EPS → within 허용.
    assert lg.within_bounds((0.0, -0.05, 1.0, 1.0), SLIDE, eps=BOUNDS_EPS) is True
    # top = -0.06 → EPS 초과 → 불허.
    assert lg.within_bounds((0.0, -0.06, 1.0, 1.0), SLIDE, eps=BOUNDS_EPS) is False
    # 우단 초과(EPS 초과) → 불허, 그 후 clamp 하면 within.
    over = (SLIDE_W_IN - 0.5, 0.0, 1.0, 1.0)  # 우단 = W+0.5 → 초과
    assert lg.within_bounds(over, SLIDE, eps=BOUNDS_EPS) is False
    assert lg.within_bounds(lg.clamp_into_bounds(over, SLIDE), SLIDE, eps=BOUNDS_EPS) is True


def test_d3_realworld_slide1_offslide_clamped():
    """슬라이드 1 실측 결함 rect (8.11,-1.39,5.21,4.17) → clamp 후 경계 안(P3 회귀 고정)."""
    bug = (8.11, -1.39, 5.21, 4.17)
    assert lg.within_bounds(bug, SLIDE, eps=BOUNDS_EPS) is False, "전제 — 원본은 경계 밖"
    fixed = lg.clamp_into_bounds(bug, SLIDE)
    assert lg.within_bounds(fixed, SLIDE, eps=BOUNDS_EPS) is True


def test_d2_realworld_large_image_in_icon_slot():
    """3840×2160 이미지가 0.46in 아이콘 슬롯 → slot_image_fits False(D2 회귀 고정)."""
    icon_slot = (1.514, 2.135, 0.46, 0.46)
    assert lg.slot_image_fits(icon_slot, 3840, 2160) is False


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-p", "no:cacheprovider", "-q"]))
