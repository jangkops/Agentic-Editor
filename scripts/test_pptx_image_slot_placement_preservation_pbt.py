"""Preservation property tests — spec: pptx-image-slot-placement-fix (bugfix), Task 2.

PROPERTY 2 — Preservation (비버그 입력에서 신규 기하 함수의 no-op 동등성 — 입력 그대로 반환).

본 PBT는 이미지 슬롯 배정 결함 수정(tasks 3.x)이 **변경해서는 안 되는** 동작을 고정한다.
**observation-first** 방법론을 따른다 — 각 단언은 먼저 입력 도메인의 비버그
(``isBugCondition == False``) 영역에서 신규 순수 기하 함수가 무엇을 해야 하는지(design §0
계약) 관찰·기록한 뒤, 그 동작을 그대로 단언한다. 따라서 수정 후(함수 신설 시) PASS 하며,
신설 전에는 자동 skip 되어 baseline 기준을 보존 가드로 고정한다.

검증 대상 신규 함수(``ai_engine/layout_geometry.py``, task 3.1에서 additive 추가):
  ``is_fullbleed`` / ``is_large_image`` / ``is_small_slot`` / ``within_bounds`` /
  ``clamp_into_bounds`` / ``fit_within`` / ``fullbleed_guard`` / ``slot_image_fits``.

``ai_engine.layout_geometry`` 모듈 자체는 이전 스펙(``pptx-overlay-collision-fix``)에서
이미 존재하므로 import 는 성공한다. 본 스펙의 **신규 함수**는 아직 없을 수 있으므로, 각 함수
존재 여부를 ``hasattr`` 로 검사하는 ``_HAS_NEW_FNS`` 플래그 + ``@pytest.mark.skipif`` 가드로
작성한다. 함수가 생기면(task 3.7 재실행) skip 가드가 해제되어 PRES-1~5가 자동 활성화된다.

보존 동작(design Preservation Checking / Property 4, 임계 상수는 design §0):

  PRES-1  경계 안 보존 (Req 3.1)
      이미 ``within_bounds`` 인 rect → ``clamp_into_bounds(r)`` 결과 == 입력 ``r``(바이트 동등).

  PRES-2  fit 중앙배치 보존 (Req 3.1)
      natural 이 이미 region 안인 입력 → ``fit_within(region, w, h)`` 가 region 내 중앙배치
      (경계 안, 음수 off 없음, draw 크기 <= region) 좌표를 반환한다.

  PRES-3  풀블리드 가드 (Req 3.1)
      ``fullbleed_guard(0) == True``(첫 풀블리드 임베드 허용), ``fullbleed_guard(>=1) == False``
      (재배경 스킵).

  PRES-4  정합 슬롯 보존 (Req 3.1)
      소형 슬롯이 아니거나 대형 이미지가 아닌 입력 → ``slot_image_fits(slot, w, h) == True``
      (재배정 불필요).

  PRES-5  임계 근방 no-op (Req 3.1, design Property 4)
      ``clamp_into_bounds`` / ``fit_within`` / ``slot_image_fits`` / ``fullbleed_guard`` 는
      비버그 입력(경계 안·정합·풀블리드 0장)에 대해 입력을 그대로 반환/True 한다.

헤르메틱 — 네트워크 0. 신규 기하 함수는 순수 계산이며 게이트웨이/Vertex/HTML 렌더를 호출할
여지가 없다(네트워크 호출 0). 임계 상수(``LARGE_PX=1024`` / ``SMALL_SLOT_IN=0.5`` /
``BOUNDS_EPS=0.05`` / ``SLIDE_RECT=(0,0,13.333,7.5)``)는 design §0 와 동일하다.

Run (hermetic):
  ./venv/bin/python -m pytest scripts/test_pptx_image_slot_placement_preservation_pbt.py -p no:cacheprovider -q

_Preservation: Preservation Requirements 전체 (Req 3.1) / design Property 4_
_Requirements: 3.1_
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

# ai_engine.layout_geometry 모듈은 이전 스펙에서 이미 존재 → import 자체는 성공.
# 본 스펙의 신규 함수만 부재 가능 → hasattr 로 가드한다.
import ai_engine.layout_geometry as lg  # noqa: E402

# design §0 의 신규 순수 함수 8종. 모두 존재할 때만 PRES-1~5 활성화.
_NEW_FNS = (
    "is_fullbleed",
    "is_large_image",
    "is_small_slot",
    "within_bounds",
    "clamp_into_bounds",
    "fit_within",
    "fullbleed_guard",
    "slot_image_fits",
)
_HAS_NEW_FNS = all(hasattr(lg, _name) for _name in _NEW_FNS)
_NEW_REASON = (
    "ai_engine.layout_geometry 신규 함수("
    + "/".join(_NEW_FNS)
    + ") 아직 없음 — task 3.1에서 추가, task 3.7에서 활성화"
)

# design §0 임계 상수 (audit 실측 기준 확정).
LARGE_PX = 1024
SMALL_SLOT_IN = 0.5
BOUNDS_EPS = 0.05
SLIDE_W_IN = 13.333
SLIDE_H_IN = 7.5
SLIDE_RECT = (0.0, 0.0, SLIDE_W_IN, SLIDE_H_IN)


# ===========================================================================
# 공통 헬퍼
# ===========================================================================
def _rect_coords(r):
    """layout_geometry Rect를 (left, top, width, height) 튜플로 정규화.

    design은 Rect = (left, top, width, height) 인치 튜플로 정의하지만, 구현이
    namedtuple/객체일 수도 있으므로 양쪽을 모두 수용한다(시그니처 도입에 견고)."""
    if hasattr(r, "left") and hasattr(r, "top") and hasattr(r, "width") and hasattr(r, "height"):
        return (float(r.left), float(r.top), float(r.width), float(r.height))
    seq = list(r)
    return (float(seq[0]), float(seq[1]), float(seq[2]), float(seq[3]))


def _approx_rect(a, b, tol=1e-6):
    ca, cb = _rect_coords(a), _rect_coords(b)
    return all(abs(x - y) <= tol for x, y in zip(ca, cb))


# ===========================================================================
# PRES-1 (Req 3.1) — 경계 안 보존: clamp_into_bounds no-op 동등성
# ===========================================================================
@pytest.mark.skipif(not _HAS_NEW_FNS, reason=_NEW_REASON)
@settings(max_examples=120, deadline=None)
@given(
    left=st.floats(min_value=0.0, max_value=10.0),
    top=st.floats(min_value=0.0, max_value=5.0),
    width=st.floats(min_value=0.1, max_value=3.0),
    height=st.floats(min_value=0.1, max_value=2.0),
)
def test_pres1_clamp_into_bounds_noop_when_within(left, top, width, height):
    """이미 슬라이드 경계 안인 rect → clamp_into_bounds 는 입력 좌표를 그대로 반환(no-op)."""
    # 입력을 확실히 경계 안으로 제한(left+width <= 13.333, top+height <= 7.5).
    w = min(width, SLIDE_W_IN - left)
    h = min(height, SLIDE_H_IN - top)
    r = (left, top, w, h)
    # 전제: within_bounds 참(비버그 입력).
    assert lg.within_bounds(r, SLIDE_RECT, eps=BOUNDS_EPS), f"전제 실패 — 경계 안이어야 함: {r}"

    out = lg.clamp_into_bounds(r, SLIDE_RECT)
    assert _approx_rect(r, out), (
        f"경계 안 rect 는 clamp 후에도 좌표 불변이어야 함(no-op) — in={r} out={_rect_coords(out)}"
    )
    # 사후조건: 결과도 여전히 within_bounds.
    assert lg.within_bounds(out, SLIDE_RECT, eps=BOUNDS_EPS)


# ===========================================================================
# PRES-2 (Req 3.1) — fit 중앙배치 보존: fit_within
# ===========================================================================
@pytest.mark.skipif(not _HAS_NEW_FNS, reason=_NEW_REASON)
@settings(max_examples=120, deadline=None)
@given(
    rl=st.floats(min_value=0.0, max_value=6.0),
    rt=st.floats(min_value=0.0, max_value=3.0),
    rw=st.floats(min_value=2.0, max_value=6.0),
    rh=st.floats(min_value=2.0, max_value=4.0),
    fw=st.floats(min_value=0.05, max_value=0.95),  # natural 폭 비율(region 내)
    fh=st.floats(min_value=0.05, max_value=0.95),  # natural 높이 비율(region 내)
)
def test_pres2_fit_within_centered_inside_region(rl, rt, rw, rh, fw, fh):
    """natural 이 region 안에 들어가는 입력 → fit_within 은 region 내 중앙배치(경계 안, 음수
    off 없음, draw 크기 <= region) 좌표를 반환한다."""
    region = (rl, rt, rw, rh)
    natural_w = rw * fw  # region 폭 이내
    natural_h = rh * fh  # region 높이 이내

    out = lg.fit_within(region, natural_w, natural_h)
    ol, ot, ow, oh = _rect_coords(out)
    tol = 1e-6

    # draw 크기는 region 을 넘지 않는다(오버플로 없음).
    assert ow <= rw + tol, f"draw 폭이 region 초과 — out_w={ow} region_w={rw}"
    assert oh <= rh + tol, f"draw 높이가 region 초과 — out_h={oh} region_h={rh}"
    # region 경계 안(음수 off 불가, 초과 없음).
    assert ol >= rl - tol, f"draw left 가 region 좌단보다 작음 — {ol} < {rl}"
    assert ot >= rt - tol, f"draw top 이 region 상단보다 작음 — {ot} < {rt}"
    assert ol + ow <= rl + rw + tol, f"draw 우단이 region 우단 초과 — {ol + ow} > {rl + rw}"
    assert ot + oh <= rt + rh + tol, f"draw 하단이 region 하단 초과 — {ot + oh} > {rt + rh}"
    # 중앙배치 — 결과 중심이 region 중심과 일치.
    assert abs((ol + ow / 2.0) - (rl + rw / 2.0)) <= 1e-4, "fit 결과가 region 가로 중앙이어야 함"
    assert abs((ot + oh / 2.0) - (rt + rh / 2.0)) <= 1e-4, "fit 결과가 region 세로 중앙이어야 함"


# ===========================================================================
# PRES-3 (Req 3.1) — 풀블리드 가드: fullbleed_guard
# ===========================================================================
@pytest.mark.skipif(not _HAS_NEW_FNS, reason=_NEW_REASON)
def test_pres3_fullbleed_guard_zero_allows_first():
    """fullbleed_guard(0) == True — 첫 풀블리드 임베드 허용."""
    assert lg.fullbleed_guard(0) is True, "풀블리드 0장이면 첫 임베드를 허용해야 함"


@pytest.mark.skipif(not _HAS_NEW_FNS, reason=_NEW_REASON)
@settings(max_examples=40, deadline=None)
@given(existing=st.integers(min_value=1, max_value=8))
def test_pres3_fullbleed_guard_existing_skips(existing):
    """fullbleed_guard(>=1) == False — 이미 풀블리드가 있으면 재배경 스킵."""
    assert lg.fullbleed_guard(existing) is False, (
        f"이미 풀블리드가 {existing}장이면 재배경을 스킵(False)해야 함"
    )


# ===========================================================================
# PRES-4 (Req 3.1) — 정합 슬롯 보존: slot_image_fits == True
# ===========================================================================
@pytest.mark.skipif(not _HAS_NEW_FNS, reason=_NEW_REASON)
@settings(max_examples=120, deadline=None)
@given(
    # 비-소형 슬롯(둘 중 적어도 한 변이 SMALL_SLOT_IN 초과) 또는 소형 이미지를 생성.
    sw=st.floats(min_value=0.05, max_value=8.0),
    sh=st.floats(min_value=0.05, max_value=6.0),
    px_w=st.integers(min_value=8, max_value=4096),
    px_h=st.integers(min_value=8, max_value=4096),
)
def test_pres4_slot_image_fits_true_for_matched(sw, sh, px_w, px_h):
    """소형 슬롯이 아니거나 대형 이미지가 아닌(정합) 입력 → slot_image_fits == True.

    버그 입력(소형 슬롯 ∧ 대형 이미지)만 False 이고, 그 외 조합은 모두 보존(True)임을 단언.
    """
    slot = (1.0, 1.0, sw, sh)
    is_small = lg.is_small_slot(slot, small_in=SMALL_SLOT_IN)
    is_large = lg.is_large_image(px_w, px_h, large_px=LARGE_PX)
    fits = lg.slot_image_fits(slot, px_w, px_h)

    if is_small and is_large:
        # 버그 조건 — 재배정 필요(False). (본 보존 테스트의 단언 대상 아님)
        assert fits is False, f"소형 슬롯×대형 이미지는 재배정(False)되어야 함: slot={slot} px=({px_w},{px_h})"
    else:
        # 정합 — 보존(True).
        assert fits is True, (
            f"정합 슬롯/이미지는 slot_image_fits==True(보존)여야 함 — "
            f"slot={slot} px=({px_w},{px_h}) small={is_small} large={is_large}"
        )


# ===========================================================================
# PRES-5 (Req 3.1, design Property 4) — 임계 근방 no-op 동등성 (복합)
# ===========================================================================
@pytest.mark.skipif(not _HAS_NEW_FNS, reason=_NEW_REASON)
@settings(max_examples=80, deadline=None)
@given(
    # 경계 EPS 근방 입력 — 경계에 거의 닿지만 within_bounds 인 rect.
    left=st.floats(min_value=0.0, max_value=0.04),
    top=st.floats(min_value=0.0, max_value=0.04),
)
def test_pres5_clamp_noop_near_boundary(left, top):
    """경계 EPS 근방이어도 within_bounds 인 rect 는 clamp 후 좌표 불변(no-op)."""
    # 우단/하단도 경계에 거의 닿게 구성하되 within_bounds 유지.
    w = SLIDE_W_IN - left - 0.001
    h = SLIDE_H_IN - top - 0.001
    r = (left, top, w, h)
    assert lg.within_bounds(r, SLIDE_RECT, eps=BOUNDS_EPS), f"전제 실패 — 경계 안이어야 함: {r}"
    out = lg.clamp_into_bounds(r, SLIDE_RECT)
    assert _approx_rect(r, out), f"경계 근방 within 입력은 no-op 이어야 함 — in={r} out={_rect_coords(out)}"


@pytest.mark.skipif(not _HAS_NEW_FNS, reason=_NEW_REASON)
@settings(max_examples=80, deadline=None)
@given(
    # 픽셀 임계(1024) 근방 — 1024 미만이면 대형 아님 → 정합(True).
    px=st.integers(min_value=900, max_value=1023),
    slot_in=st.floats(min_value=0.05, max_value=SMALL_SLOT_IN),
)
def test_pres5_slot_image_fits_noop_just_below_large_px(px, slot_in):
    """소형 슬롯이라도 이미지가 LARGE_PX 미만(대형 아님)이면 정합 → slot_image_fits==True(보존)."""
    slot = (1.0, 1.0, slot_in, slot_in)
    assert lg.is_small_slot(slot, small_in=SMALL_SLOT_IN) is True
    assert lg.is_large_image(px, px, large_px=LARGE_PX) is False
    assert lg.slot_image_fits(slot, px, px) is True, (
        f"소형 슬롯 + 1024px 미만 이미지는 정합(True)이어야 함 — slot={slot} px={px}"
    )


@pytest.mark.skipif(not _HAS_NEW_FNS, reason=_NEW_REASON)
def test_pres5_fullbleed_guard_boundary():
    """풀블리드 가드 경계: 0 → True(허용), 1 → False(스킵)."""
    assert lg.fullbleed_guard(0) is True
    assert lg.fullbleed_guard(1) is False


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-p", "no:cacheprovider", "-q"]))
