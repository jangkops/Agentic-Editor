"""순수 레이아웃 기하 모듈 — spec: pptx-overlay-collision-fix (bugfix), Task 3.1.

PPTX 오버레이/충돌 결함 A/B/C 의 충돌 검출·회피 기하를 **순수 함수**로 추출한 신규 모듈.
``server.py`` / ``native_diagram_pptx.py`` 의 placement·렌더 결정 코드가 placement 직전에
이 함수들을 호출한다(task 3.2~3.5). 본 모듈은 PBT(속성 기반 테스트)의 단일 대상이다.

설계 원칙(design Fix Implementation §0):

  * **순수 함수만** — LLM/게이트웨이/네트워크/모델 호출이 전혀 없다(import 도 표준 라이브러리만).
    Property 5(게이트웨이 제약)의 "기하 함수는 어떤 네트워크/모델 호출도 하지 않는다"를 보장한다.
  * **좌표 단위 = 인치**, ``Rect = (left, top, width, height)`` 4-튜플(모두 float).
    감사 도구(``scripts/audit_pptx_textbox_overlap.py`` 의 ``ov()``)와 동일한 축-정렬 교집합
    정의를 사용해 감사 ↔ 코드 측정 기준을 일치시킨다(EMU 환산 10% 임계).
  * **핵심 불변식 — no-op 동등성**: ``vertical_stack`` / ``resolve_collisions`` /
    ``body_safe_area`` 는 겹침이 임계 미만이면 **입력 좌표를 그대로 반환**한다. 이것이
    비버그 입력 바이트 보존(design Property 3)의 근거다.

함수:
  * ``area(r)``                  — 사각형 면적(in²).
  * ``overlap_area(a, b)``       — 두 사각형의 축-정렬 교집합 면적(in²).
  * ``vertical_stack(...)``      — 위→아래 비겹침 재배치(첫 박스 top 보존).
  * ``resolve_collisions(...)``  — 모든 쌍 겹침 < threshold·min(area) 되도록 최소 이동.
  * ``place_badge_in_gutter(...)``— 배지를 라벨 박스 밖 거터에 배치(겹침 0 보장).
  * ``body_safe_area(...)``      — 본문 영역을 배경 이미지와 분리.

_Requirements: 2.1, 2.2, 2.4, 2.5, 3.1_
_Expected_Behavior: design Fix Implementation §0_
_Preservation: 겹침 임계 미만 시 no-op 동등성 (design Property 3)_
"""
from __future__ import annotations

from typing import List, Optional, Sequence, Tuple

# Rect = (left, top, width, height)  — 인치, 모두 float.
Rect = Tuple[float, float, float, float]

# design Bug Condition: 의미있는 겹침 임계(작은 박스 면적의 10%).
DEFAULT_THRESHOLD = 0.10


# ---------------------------------------------------------------------------
# 내부 정규화 헬퍼
# ---------------------------------------------------------------------------
def _coords(r) -> Rect:
    """Rect 입력을 ``(left, top, width, height)`` float 4-튜플로 정규화.

    design 은 Rect 를 4-튜플로 정의하지만, 호출부가 namedtuple/객체(``.left`` 등)를 넘길
    수도 있으므로 양쪽을 모두 수용한다(시그니처 도입에 견고)."""
    if hasattr(r, "left") and hasattr(r, "top") and hasattr(r, "width") and hasattr(r, "height"):
        return (float(r.left), float(r.top), float(r.width), float(r.height))
    seq = list(r)
    return (float(seq[0]), float(seq[1]), float(seq[2]), float(seq[3]))


# ---------------------------------------------------------------------------
# 면적 / 교집합
# ---------------------------------------------------------------------------
def area(r) -> float:
    """사각형 면적(in²). 음수 폭/높이는 0 으로 클램프."""
    _, _, w, h = _coords(r)
    return max(0.0, w) * max(0.0, h)


def overlap_area(a, b) -> float:
    """두 사각형의 축-정렬 교집합 면적(in²).

    ``scripts/audit_pptx_textbox_overlap.py`` 의 ``ov()`` 와 동일 정의 — 감사 ↔ 코드 일치.
    """
    l1, t1, w1, h1 = _coords(a)
    l2, t2, w2, h2 = _coords(b)
    ix = max(0.0, min(l1 + w1, l2 + w2) - max(l1, l2))
    iy = max(0.0, min(t1 + h1, t2 + h2) - max(t1, t2))
    return ix * iy


# ---------------------------------------------------------------------------
# 수직 스택
# ---------------------------------------------------------------------------
def vertical_stack(
    boxes: Sequence[Rect],
    *,
    gap: float = 0.0,
    max_bottom: Optional[float] = None,
) -> List[Rect]:
    """박스를 입력 순서대로 위→아래 비겹침으로 재배치한다.

    규칙:
      * 첫 박스의 ``top`` 은 보존한다.
      * 각 박스의 ``top`` 은 직전(재배치된) 박스의 ``bottom + gap`` 이상으로 밀어 내린다.
      * 이미 (gap 이상으로) 분리된 박스는 그대로 둔다 → 전체가 이미 비겹침이면 입력 좌표
        그대로 반환(no-op 동등성, design Property 3).
      * ``max_bottom`` 이 주어지면 마지막 박스 ``bottom`` 이 이를 넘지 않도록 전체를 위로
        압축(가능한 범위)한다.

    반환: 입력과 동일 개수의 Rect 4-튜플 리스트.
    """
    rects = [_coords(b) for b in boxes]
    if not rects:
        return []

    out: List[Rect] = []
    prev_bottom: Optional[float] = None
    for (l, t, w, h) in rects:
        if prev_bottom is None:
            new_t = t  # 첫 박스 top 보존
        else:
            required_top = prev_bottom + gap
            # 이미 충분히 아래면 보존, 아니면 비겹침 위치로 밀어 내림.
            new_t = t if t >= required_top else required_top
        out.append((l, new_t, w, h))
        prev_bottom = new_t + h

    if max_bottom is not None and out:
        last_bottom = out[-1][1] + out[-1][3]
        overflow = last_bottom - max_bottom
        if overflow > 0:
            # 첫 박스 top 은 0 미만으로 내려가지 않도록 가능한 만큼만 위로 압축.
            shift = min(overflow, max(0.0, out[0][1]))
            if shift > 0:
                out = [(l, t - shift, w, h) for (l, t, w, h) in out]

    return out


# ---------------------------------------------------------------------------
# 충돌 회피
# ---------------------------------------------------------------------------
def _has_significant_collision(rects: Sequence[Rect], threshold: float) -> bool:
    n = len(rects)
    for i in range(n):
        for j in range(i + 1, n):
            ov = overlap_area(rects[i], rects[j])
            if ov <= 0.0:
                continue
            amin = min(area(rects[i]), area(rects[j]))
            if amin <= 0.0:
                continue
            if ov >= threshold * amin:
                return True
    return False


def resolve_collisions(
    boxes: Sequence[Rect],
    *,
    threshold: float = DEFAULT_THRESHOLD,
    axis: str = "vertical",
    bounds: Optional[Rect] = None,
) -> List[Rect]:
    """모든 쌍 ``(a, b)`` 에 대해 ``overlap_area(a, b) < threshold·min(area(a), area(b))``
    가 되도록 박스를 최소 이동한다(기본 수직).

    핵심 불변식: 임계 이상 겹치는 쌍이 하나도 없으면 **입력 좌표를 그대로 반환**한다
    (no-op 동등성, design Property 3). 임계 이상 겹침이 있을 때만 재배치한다.
    """
    rects = [_coords(b) for b in boxes]
    if len(rects) < 2:
        return rects

    if not _has_significant_collision(rects, threshold):
        return rects  # no-op — 비버그 입력 바이트 보존

    # 충돌 존재 → 축 방향으로 비겹침 스택. 원래 순서(인덱스)는 보존해 반환.
    order = sorted(range(len(rects)), key=lambda i: (rects[i][1], rects[i][0])
                   if axis == "vertical" else (rects[i][0], rects[i][1]))

    resolved = list(rects)
    if axis == "horizontal":
        frontier: Optional[float] = None  # 직전 박스의 right
        for idx in order:
            l, t, w, h = rects[idx]
            new_l = l if frontier is None or l >= frontier else frontier
            resolved[idx] = (new_l, t, w, h)
            frontier = new_l + w
    else:  # vertical (기본)
        frontier = None  # 직전 박스의 bottom
        for idx in order:
            l, t, w, h = rects[idx]
            new_t = t if frontier is None or t >= frontier else frontier
            resolved[idx] = (l, new_t, w, h)
            frontier = new_t + h

    return resolved


# ---------------------------------------------------------------------------
# 배지 거터 배치
# ---------------------------------------------------------------------------
def place_badge_in_gutter(
    label: Rect,
    diameter: float,
    *,
    gutter: str = "left",
    gap: float = 0.05,
) -> Rect:
    """정사각형 배지(폭=높이=``diameter``)를 라벨 박스 **밖** 거터에 배치한다.

    배지는 라벨과 ``gap`` 만큼 떨어져 라벨 영역을 침범하지 않으므로
    ``overlap_area(badge, label) == 0`` 이 보장된다. 세로(또는 가로)로는 라벨 중앙에 맞춘다.

      * ``gutter="left"``  : 배지 우단 = 라벨 좌단 - gap (라벨 왼쪽 밖)
      * ``gutter="right"`` : 배지 좌단 = 라벨 우단 + gap (라벨 오른쪽 밖)
      * ``gutter="top"``   : 배지 하단 = 라벨 상단 - gap (라벨 위쪽 밖)
      * ``gutter="bottom"``: 배지 상단 = 라벨 하단 + gap (라벨 아래쪽 밖)

    반환: 배지 Rect ``(left, top, width, height)`` (width == height == diameter).
    """
    ll, lt, lw, lh = _coords(label)
    d = float(diameter)
    g = float(gap)

    if gutter == "right":
        bl = ll + lw + g
        bt = lt + (lh - d) / 2.0
    elif gutter == "top":
        bl = ll + (lw - d) / 2.0
        bt = lt - g - d
    elif gutter == "bottom":
        bl = ll + (lw - d) / 2.0
        bt = lt + lh + g
    else:  # "left" (기본)
        bl = ll - g - d
        bt = lt + (lh - d) / 2.0

    return (bl, bt, d, d)


# ---------------------------------------------------------------------------
# 본문 안전 영역
# ---------------------------------------------------------------------------
def _intersect_1d(a0: float, a1: float, b0: float, b1: float) -> float:
    return max(0.0, min(a1, b1) - max(a0, b0))


def body_safe_area(
    slide: Rect,
    bg: Optional[Rect],
    *,
    has_baked_text: bool,
    desired: Rect,
    threshold: float = DEFAULT_THRESHOLD,
) -> Rect:
    """본문 영역(``desired``)을 배경 이미지(``bg``)와 분리한다.

    * ``bg`` 가 ``None`` 이거나 ``has_baked_text`` 가 거짓(백드롭/스크림/흰 패널 등 분리
      보장된 배경)이면 ``desired`` 를 **그대로 반환**한다(본문 분리 보존, design Property 3).
    * ``bg`` 가 풀블리드 구워진-텍스트 배경이고 본문이 그 위에 임계 이상 겹쳐 올라가는
      경우, 본문을 배경과 겹침 < ``threshold`` 인 안전 영역(배경이 덮지 않는 여백 띠)으로
      축소/이동한다. 안전 띠를 확보할 수 없으면(배경이 슬라이드를 완전히 덮음) ``desired``
      를 그대로 반환한다 — 이 경우 호출부가 본문을 네이티브 캐리어로 전환하도록 결정한다
      (design Fix Implementation §3, body_separated 신호).

    반환: 본문 Rect ``(left, top, width, height)``.
    """
    d = _coords(desired)
    if bg is None or not has_baked_text:
        return d  # 백드롭/배경 없음 → 본문 region 보존(no-op 동등성)

    # 이미 임계 미만으로 분리되어 있으면 그대로 보존.
    bg_r = _coords(bg)
    body_area = area(d)
    if body_area <= 0.0:
        return d
    if overlap_area(d, bg_r) < threshold * body_area:
        return d

    sl, st, sw, sh = _coords(slide)
    s_right, s_bottom = sl + sw, st + sh
    bg_l, bg_t, bg_w, bg_h = bg_r
    bg_right, bg_bottom = bg_l + bg_w, bg_t + bg_h

    dl, dt, dw, dh = d
    # 배경이 덮지 않는 슬라이드 내 여백 띠(top/bottom/left/right) 후보를 만든다.
    bands: List[Rect] = []
    # 상단 띠 (배경 위)
    if bg_t - st > 0:
        bands.append((sl, st, sw, bg_t - st))
    # 하단 띠 (배경 아래)
    if s_bottom - bg_bottom > 0:
        bands.append((sl, bg_bottom, sw, s_bottom - bg_bottom))
    # 좌측 띠
    if bg_l - sl > 0:
        bands.append((sl, st, bg_l - sl, sh))
    # 우측 띠
    if s_right - bg_right > 0:
        bands.append((bg_right, st, s_right - bg_right, sh))

    if not bands:
        # 배경이 슬라이드를 완전히 덮음 → 안전 띠 없음. 호출부가 네이티브 전환 결정.
        return d

    # 본문 폭(가로 띠) 또는 높이(세로 띠) 기준으로 본문을 담을 수 있는 가장 큰 띠 선택.
    def _band_capacity(band: Rect) -> float:
        return area(band)

    best = max(bands, key=_band_capacity)
    bl, bt, bw, bh = best
    # 본문을 띠 안으로 축소/이동(원래 폭/높이를 넘지 않도록 클램프).
    new_w = min(dw, bw)
    new_h = min(dh, bh)
    new_l = min(max(dl, bl), bl + bw - new_w)
    new_t = min(max(dt, bt), bt + bh - new_h)
    return (new_l, new_t, new_w, new_h)


# ===========================================================================
# spec: pptx-image-slot-placement-fix (bugfix), Task 3.1 — 신규 순수 기하 함수
# ===========================================================================
# 이미지 슬롯 배정 결함 D1/D2/D3 의 좌표·슬롯·중복 결정을 순수 함수로 추출한다.
# 모두 LLM/게이트웨이/네트워크/모델 호출 0(Property 5). 좌표 단위 = 인치,
# Rect = (left, top, width, height). 핵심 불변식: clamp_into_bounds/fit_within/
# fullbleed_guard/slot_image_fits 는 비버그 입력(경계 안·정합·풀블리드 0장)에서
# 입력을 그대로 반환/True 한다(no-op 동등성, design Property 4).
#
# _Bug_Condition: isBugCondition(S) = defectD1 OR defectD2 OR defectD3 (design Bug Condition)
# _Expected_Behavior: design Fix Implementation §0
# _Preservation: 비버그 입력 시 no-op 동등성/True (design Property 4)
# _Requirements: 2.1, 2.2, 2.3, 3.1

# design §0 임계 상수 (audit 실측 기준 확정).
LARGE_PX: int = 1024              # 배경/콘텐츠급 대형 이미지(3840x2160 ≫ 1024, 정상 75x100/아이콘 < 1024)
SMALL_SLOT_IN: float = 0.5        # 아이콘/액센트급 소형 슬롯(결함 0.25in < 0.5, 콘텐츠 region ≥5in)
BOUNDS_EPS: float = 0.05          # 경계 허용오차(audit off-slide 판정과 동일)
SLIDE_RECT: Rect = (0.0, 0.0, 13.333, 7.5)


def is_fullbleed(r) -> bool:
    """풀블리드 배경 여부 — audit 도구(``audit_pptx_zorder_break``/``audit_pptx_media_classify``)와
    동일 판정. ``r.left<=0.3 ∧ r.top<=0.3 ∧ r.width>=13.333*0.92 ∧ r.height>=7.5*0.92``."""
    l, t, w, h = _coords(r)
    return l <= 0.3 and t <= 0.3 and w >= 13.333 * 0.92 and h >= 7.5 * 0.92


def is_large_image(px_w: int, px_h: int, *, large_px: int = LARGE_PX) -> bool:
    """이미지 픽셀 해상도가 배경/콘텐츠급 대형인지 — ``px_w>=large_px OR px_h>=large_px``."""
    return px_w >= large_px or px_h >= large_px


def is_small_slot(r, *, small_in: float = SMALL_SLOT_IN) -> bool:
    """배치 슬롯이 아이콘/액센트급 소형인지 — ``r.width<=small_in AND r.height<=small_in``."""
    _, _, w, h = _coords(r)
    return w <= small_in and h <= small_in


def within_bounds(r, slide: Rect = SLIDE_RECT, *, eps: float = BOUNDS_EPS) -> bool:
    """rect 가 슬라이드 경계 안인지(음수/초과 없음).

    ``left>=-eps ∧ top>=-eps ∧ left+width<=slide.width+eps ∧ top+height<=slide.height+eps``.
    """
    l, t, w, h = _coords(r)
    sl, st, sw, sh = _coords(slide)
    return (
        l >= sl - eps
        and t >= st - eps
        and l + w <= sl + sw + eps
        and t + h <= st + sh + eps
    )


def clamp_into_bounds(r, slide: Rect = SLIDE_RECT) -> Rect:
    """D3: rect 를 슬라이드 경계 안으로 이동/축소한다.

    * width/height 가 slide 를 넘으면 slide 크기로 축소(종횡비 보존은 ``fit_within`` 사용).
    * left/top 음수 또는 초과면 경계 안으로 평행이동.
    * **이미 within_bounds 면 입력을 그대로 반환**(no-op 동등성, design Property 4).

    반환: 경계 안으로 클램프된 Rect ``(left, top, width, height)``.
    """
    if within_bounds(r, slide):
        return _coords(r)  # no-op — 비버그 입력 바이트 보존

    l, t, w, h = _coords(r)
    sl, st, sw, sh = _coords(slide)

    # 1) 크기가 슬라이드를 넘으면 슬라이드 크기로 축소.
    new_w = min(w, sw)
    new_h = min(h, sh)

    # 2) left/top 을 경계 안으로 평행이동(좌단/상단 음수 보정 후 우단/하단 초과 보정).
    new_l = max(sl, l)
    new_t = max(st, t)
    if new_l + new_w > sl + sw:
        new_l = sl + sw - new_w
    if new_t + new_h > st + sh:
        new_t = st + sh - new_h

    return (new_l, new_t, new_w, new_h)


def fit_within(region: Rect, natural_w: float, natural_h: float) -> Rect:
    """D3: natural 종횡비를 보존하며 region 안에 fit + 중앙정렬한 rect 반환.

    draw 크기가 region 을 넘지 않음을 보장 → ``off_t``/``off_l`` 음수 불가. natural 이 이미
    region 안에 들어가면 그 크기 그대로 region 중앙배치, region 보다 크면 종횡비 보존 축소 후
    중앙배치한다.

    반환: ``(left, top, width, height)`` — region 내부 중앙 정렬.
    """
    rl, rt, rw, rh = _coords(region)
    nw = float(natural_w)
    nh = float(natural_h)

    # 비정상 natural(0/음수)은 region 전체로 폴백.
    if nw <= 0.0 or nh <= 0.0 or rw <= 0.0 or rh <= 0.0:
        return (rl, rt, max(0.0, rw), max(0.0, rh))

    # 종횡비 보존 스케일 — region 을 넘지 않도록(<=1 이면 natural 이 region 안).
    scale = min(rw / nw, rh / nh)
    if scale > 1.0:
        scale = 1.0  # natural 이 region 안이면 확대하지 않고 원본 크기 유지
    draw_w = nw * scale
    draw_h = nh * scale

    # region 중앙 정렬 — off 는 (region - draw)/2 >= 0 이므로 음수 불가.
    off_l = rl + (rw - draw_w) / 2.0
    off_t = rt + (rh - draw_h) / 2.0
    return (off_l, off_t, draw_w, draw_h)


def fullbleed_guard(existing_count: int) -> bool:
    """D1: 이미 풀블리드가 존재하면(``existing_count>=1``) False(재배경 스킵), 0이면 True(임베드 허용).

    호출부는 False 면 풀블리드 임베드를 건너뛰고 후보를 다른 슬롯으로 재배정한다(손실-0).
    """
    return existing_count < 1


def slot_image_fits(slot: Rect, px_w: int, px_h: int) -> bool:
    """D2: 소형 슬롯(``is_small_slot``)에 대형 이미지(``is_large_image``)면 False, 그 외 True.

    호출부는 False 면 대형 이미지를 풀블리드/콘텐츠 region 으로 재배정한다(손실-0).
    """
    if is_small_slot(slot) and is_large_image(px_w, px_h):
        return False
    return True
