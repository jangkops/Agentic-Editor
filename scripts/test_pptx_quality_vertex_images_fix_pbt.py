"""Fix-checking property tests — spec: pptx-quality-vertex-images (bugfix), Task 4.

PROPERTY 3 — Loss-Zero (결정 규칙의 전역성·결정성, 손실 0).

design Property 3 / Fix Implementation §3:

  _For any_ 가능한 슬라이드 미디어 상태(이미지/네이티브/배경/역할/게이트 플래그의 모든
  조합)에 대해, ``_select_render_plan``(design ``selectRenderPlan``)은 정확히 하나의 주
  렌더러를 정하고 Vertex 이미지의 사용처를 명시하며, "이미지와 구조 표현이 동시에 손실되는"
  출력은 절대 만들지 않는다 (전역 정의 + 손실 0 불변식).

핵심 불변식 (design Fix Implementation §3 "핵심 불변식"):
  ``pre``(생성된 Vertex 이미지)가 존재하면 어떤 분기에서도 폐기되지 않는다 — 최소한
  ``_native_over_bg`` / ``_eff_bg`` backdrop 으로 보존된다. 즉
  ``has_vertex_image`` 이면 반환 ``vertex_slot`` 은 결코 ``"none"`` 이 아니다.

이 테스트는 순수 결정 함수 ``_select_render_plan`` 을 SlideMediaState 의 *모든* 조합
(hasVertexImage / hasNativeDiagram / hasImageFile / hasSlideBg / role / htmlEnabled /
vertexEnabled) 에 대해 구동한다. ``_select_render_plan`` 은 LLM/게이트웨이 호출이 없는
순수 함수이므로 네트워크가 없고 완전히 헤르메틱하다.

검증 항목:
  1. 전역 정의 + 정확히 하나의 주 렌더러:
     primary ∈ {HTML, NATIVE_SHAPES, VERTEX_IMAGE} 중 정확히 하나.
  2. 손실 0(생성 이미지 미폐기): has_vertex_image → vertex_slot ∈ {hero, backdrop, visual}
     (절대 "none" 이 아님). 역으로 NOT has_vertex_image → vertex_slot == "none".
  3. 이미지·구조 동시 손실 없음: image_lost ∧ structural_lost 인 출력이 절대 없음.
  4. 결정성: 같은 입력 → 같은 출력(두 번 호출 시 동일).

EXPECTED OUTCOME: 수정된 코드에서 PASS.

**Validates: Requirements 2.2, 2.4**

Run (hermetic — no network, pure decision function):
  ./venv/bin/python -m pytest scripts/test_pptx_quality_vertex_images_fix_pbt.py -p no:cacheprovider -q

_Requirements: 2.2, 2.4_
"""
from __future__ import annotations

import os
import sys
import itertools

# Make ai_engine importable from repo root.
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import pytest  # noqa: E402
from hypothesis import given, settings, strategies as st  # noqa: E402

from ai_engine.server import _select_render_plan  # noqa: E402


# Role enumeration produced by _classify_slide_role (design Fix Implementation §1).
_ROLES = ("cover", "section", "structural", "content", "visual")
_PRIMARIES = {"HTML", "NATIVE_SHAPES", "VERTEX_IMAGE"}
_SLOTS = {"hero", "backdrop", "visual", "none"}


def _is_valid_state(s: dict) -> bool:
    """A generated Vertex image requires Vertex to be enabled — drop impossible
    states (hasVertexImage ∧ ¬vertexEnabled) so the domain stays honest."""
    return s["vertexEnabled"] or not s["hasVertexImage"]


def _plan_for(state: dict) -> dict:
    return _select_render_plan(
        has_vertex_image=state["hasVertexImage"],
        has_native_diagram=state["hasNativeDiagram"],
        has_image_file=state["hasImageFile"],
        has_slide_bg=state["hasSlideBg"],
        role=state["role"],
        html_enabled=state["htmlEnabled"],
    )


def _assert_loss_zero_invariants(state: dict, plan: dict) -> None:
    """Assert design Property 3 invariants for a single (state, plan) pair."""
    primary = plan["primary"]
    slot = plan["vertex_slot"]

    # (1) 전역 정의 + 정확히 하나의 주 렌더러.
    assert primary in _PRIMARIES, (
        f"primary must be exactly one renderer in {sorted(_PRIMARIES)}, "
        f"got {primary!r} for state={state!r}"
    )
    assert slot in _SLOTS, (
        f"vertex_slot must be one of {sorted(_SLOTS)}, got {slot!r} for state={state!r}"
    )

    # (2) 손실 0: 생성된 Vertex 이미지는 어떤 분기에서도 폐기되지 않는다.
    if state["hasVertexImage"]:
        assert slot != "none", (
            "손실 0 위반: 생성된 Vertex 이미지가 폐기됨(vertex_slot='none'). "
            f"어떤 분기에서도 보존되어야 한다. state={state!r}, plan={plan!r}"
        )
    else:
        # Vertex 이미지가 없으면 슬롯은 의미 없음("none")이어야 한다.
        assert slot == "none", (
            f"Vertex 이미지가 없는데 슬롯이 점유됨: slot={slot!r}, state={state!r}"
        )

    # (3) 이미지·구조 표현이 *동시에* 손실되는 출력은 절대 없다.
    image_lost = state["hasVertexImage"] and slot == "none"
    structural_lost = state["hasNativeDiagram"] and primary != "NATIVE_SHAPES"
    assert not (image_lost and structural_lost), (
        "이미지와 구조 표현이 동시에 손실됨(둘 다 폐기). "
        f"state={state!r}, plan={plan!r}"
    )


# --------------------------------------------------------------------------
# Exhaustive coverage — every SlideMediaState combination (전역 정의·결정성).
# 2^6 boolean combos × 5 roles = 320 states; cheap pure-function calls.
# --------------------------------------------------------------------------
def _all_states():
    bools = (False, True)
    for (hv, hn, hi, hb, html, vtx), role in itertools.product(
        itertools.product(bools, bools, bools, bools, bools, bools), _ROLES
    ):
        state = {
            "hasVertexImage": hv,
            "hasNativeDiagram": hn,
            "hasImageFile": hi,
            "hasSlideBg": hb,
            "htmlEnabled": html,
            "vertexEnabled": vtx,
            "role": role,
        }
        if _is_valid_state(state):
            yield state


def test_property3_loss_zero_exhaustive():
    """전역적으로 모든 SlideMediaState 조합에서 손실 0 불변식이 성립한다."""
    checked = 0
    for state in _all_states():
        plan = _plan_for(state)
        _assert_loss_zero_invariants(state, plan)
        checked += 1
    # Sanity: the domain is non-trivial (all 5 roles × valid bool combos).
    assert checked >= 5 * 48, f"expected full state coverage, only checked {checked}"


def test_property3_determinism_exhaustive():
    """결정성: 같은 입력은 항상 같은 출력을 낸다(부수효과 없음)."""
    for state in _all_states():
        first = _plan_for(state)
        second = _plan_for(state)
        assert first == second, (
            f"결정성 위반: 같은 입력에 다른 출력 — state={state!r}, "
            f"first={first!r}, second={second!r}"
        )


# --------------------------------------------------------------------------
# Property-based coverage — randomized generation over the same domain
# (paired with the exhaustive test; surfaces shrunk counterexamples on
# regression).
# --------------------------------------------------------------------------
@st.composite
def _slide_media_state(draw):
    state = {
        "hasVertexImage": draw(st.booleans()),
        "hasNativeDiagram": draw(st.booleans()),
        "hasImageFile": draw(st.booleans()),
        "hasSlideBg": draw(st.booleans()),
        "htmlEnabled": draw(st.booleans()),
        "vertexEnabled": draw(st.booleans()),
        "role": draw(st.sampled_from(_ROLES)),
    }
    # Keep the domain honest: a generated image implies Vertex enabled.
    if state["hasVertexImage"]:
        state["vertexEnabled"] = True
    return state


@settings(max_examples=300, deadline=None)
@given(state=_slide_media_state())
def test_property3_loss_zero_pbt(state):
    """Property 3 (Loss-Zero): for any SlideMediaState, _select_render_plan
    picks exactly one primary renderer and never discards a generated Vertex
    image (image and structural representation are never both lost)."""
    plan = _plan_for(state)
    _assert_loss_zero_invariants(state, plan)


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-p", "no:cacheprovider", "-q"]))


# ==========================================================================
# PROPERTY 4 — HTML–Vertex Coexistence (하이브리드 게이트), Task 5.
#
# design Property 4 / Fix Implementation §2–3:
#
#   _For any_ 입력에서 HTML이 활성이고(htmlEnabled) Vertex가 활성이며(vertexEnabled)
#   슬라이드 역할이 cover/content/visual이면, 결정 규칙은 HTML 레이아웃을 주 렌더러로
#   유지하면서도 Vertex 이미지 생성을 **억제하지 않는다**(둘이 상호배타가 아니다).
#   구조형 역할에서는 네이티브 도형이 우선하되 생성된 이미지가 backdrop으로 보존된다.
#
# 미수정 코드의 결함은 (A) 게이트 상호배타(`not _html_enabled`)로 Vertex 생성이 통째로
# 억제되거나, (B) 임베드 가드(`not native_diag`)로 생성 이미지가 폐기되는 것이었다. 수정된
# 결정 규칙 ``_select_render_plan`` 은 HTML이 주 렌더러(slide_bg)일 때조차 생성된 Vertex
# 이미지를 hero 슬롯으로 보존(공존)하며, 구조형에서도 native 도형을 주 렌더러로 두되 이미지를
# backdrop으로 보존한다.
#
# 단언:
#   (공존) htmlEnabled ∧ vertexEnabled ∧ role∈{cover,content,visual} ∧ hasVertexImage 이면
#          vertex_slot ∈ {hero, visual, backdrop} (절대 "none" — 생성 억제/폐기 없음).
#          HTML 풀블리드(slide_bg)가 있으면 primary=="HTML" 가 유지되고 slot=="hero"
#          (HTML 레이아웃 + Vertex 이미지가 단일 렌더로 공존, 상호배타 아님).
#   (구조형) role=="structural" ∧ hasNativeDiagram ∧ ¬slide_bg ∧ ¬image_file ∧ hasVertexImage
#          이면 primary=="NATIVE_SHAPES"(네이티브 도형 우선) ∧ vertex_slot=="backdrop"
#          (생성된 이미지가 폐기되지 않고 backdrop으로 보존).
#
# EXPECTED OUTCOME: 수정된 코드에서 PASS.
#
# **Validates: Requirements 2.1, 2.4, 3.1, 3.2**
#
# _Requirements: 2.1, 2.4, 3.1, 3.2_
# ==========================================================================

_COEXIST_ROLES = ("cover", "content", "visual")


def _assert_coexistence(state: dict, plan: dict) -> None:
    """cover/content/visual 공존 단언: Vertex 생성이 억제되지 않고 HTML이 주 렌더러로 유지."""
    primary = plan["primary"]
    slot = plan["vertex_slot"]

    # Vertex 생성/이미지가 억제되거나 폐기되지 않는다(상호배타 아님).
    assert slot in ("hero", "visual", "backdrop"), (
        "HTML–Vertex 공존 위반: htmlEnabled ∧ vertexEnabled ∧ role∈{cover,content,visual} "
        f"∧ hasVertexImage 인데 Vertex 슬롯이 점유되지 않음(slot={slot!r}). "
        f"생성된 이미지는 억제/폐기되지 않아야 한다. state={state!r}, plan={plan!r}"
    )
    assert slot != "none", (
        f"HTML–Vertex 공존 위반: 생성된 Vertex 이미지가 폐기됨(slot='none'). state={state!r}"
    )

    # HTML 풀블리드가 있으면 HTML이 주 렌더러로 유지되고, 이미지는 hero 슬롯으로 합성된다.
    if state["hasSlideBg"]:
        assert primary == "HTML", (
            "HTML–Vertex 공존 위반: HTML 풀블리드(slide_bg) 슬라이드인데 HTML이 주 렌더러가 "
            f"아님(primary={primary!r}). HTML이 주 렌더러로 유지되어야 한다. state={state!r}"
        )
        assert slot == "hero", (
            "HTML–Vertex 공존 위반: HTML이 주 렌더러일 때 Vertex 이미지는 hero 슬롯으로 "
            f"합성되어야 한다(slot={slot!r}). state={state!r}"
        )


def _assert_structural_backdrop(state: dict, plan: dict) -> None:
    """structural 역할 단언: 네이티브 도형 우선 + 생성 이미지 backdrop 보존."""
    primary = plan["primary"]
    slot = plan["vertex_slot"]
    assert primary == "NATIVE_SHAPES", (
        "구조형 공존 위반: structural 역할은 네이티브 도형이 주 렌더러여야 한다"
        f"(primary={primary!r}). state={state!r}, plan={plan!r}"
    )
    assert slot == "backdrop", (
        "구조형 공존 위반: structural 역할에서 생성된 Vertex 이미지가 backdrop으로 "
        f"보존되어야 한다(slot={slot!r}, 폐기 금지). state={state!r}, plan={plan!r}"
    )


def _coexistence_states():
    """htmlEnabled ∧ vertexEnabled ∧ role∈{cover,content,visual} ∧ hasVertexImage 인
    모든 (native/image/slide_bg) 조합."""
    bools = (False, True)
    for hn, hi, hb, role in itertools.product(bools, bools, bools, _COEXIST_ROLES):
        yield {
            "hasVertexImage": True,
            "hasNativeDiagram": hn,
            "hasImageFile": hi,
            "hasSlideBg": hb,
            "htmlEnabled": True,
            "vertexEnabled": True,
            "role": role,
        }


def test_property4_html_vertex_coexistence_exhaustive():
    """공존: cover/content/visual 모든 조합에서 Vertex가 억제되지 않고 HTML이 주 렌더러로 유지."""
    checked = 0
    for state in _coexistence_states():
        plan = _plan_for(state)
        # 손실-0 불변식도 동시에 성립해야 한다.
        _assert_loss_zero_invariants(state, plan)
        _assert_coexistence(state, plan)
        checked += 1
    assert checked == 2 * 2 * 2 * 3, f"expected full coexistence coverage, checked {checked}"


def test_property4_structural_preserves_image_as_backdrop_exhaustive():
    """구조형: 네이티브 도형 우선이되 생성된 Vertex 이미지가 backdrop으로 보존된다."""
    for state in (
        {
            "hasVertexImage": True,
            "hasNativeDiagram": True,
            "hasImageFile": False,
            "hasSlideBg": False,
            "htmlEnabled": html,
            "vertexEnabled": True,
            "role": "structural",
        }
        for html in (False, True)
    ):
        plan = _plan_for(state)
        _assert_loss_zero_invariants(state, plan)
        _assert_structural_backdrop(state, plan)


@st.composite
def _coexistence_state(draw):
    """공존 도메인 생성기: htmlEnabled=vertexEnabled=hasVertexImage=True,
    role∈{cover,content,visual}, 나머지 미디어 플래그는 자유."""
    return {
        "hasVertexImage": True,
        "hasNativeDiagram": draw(st.booleans()),
        "hasImageFile": draw(st.booleans()),
        "hasSlideBg": draw(st.booleans()),
        "htmlEnabled": True,
        "vertexEnabled": True,
        "role": draw(st.sampled_from(_COEXIST_ROLES)),
    }


@settings(max_examples=200, deadline=None)
@given(state=_coexistence_state())
def test_property4_html_vertex_coexistence_pbt(state):
    """Property 4 (HTML–Vertex Coexistence): for htmlEnabled ∧ vertexEnabled ∧
    role∈{cover,content,visual}, the decision rule keeps HTML as the primary
    renderer (when a full-bleed background exists) while never suppressing or
    discarding the generated Vertex image."""
    plan = _plan_for(state)
    _assert_loss_zero_invariants(state, plan)
    _assert_coexistence(state, plan)


@st.composite
def _structural_state(draw):
    """구조형 도메인 생성기: role=structural, native_diagram 존재, slide_bg/image_file 없음,
    Vertex 이미지 생성됨."""
    return {
        "hasVertexImage": True,
        "hasNativeDiagram": True,
        "hasImageFile": False,
        "hasSlideBg": False,
        "htmlEnabled": draw(st.booleans()),
        "vertexEnabled": True,
        "role": "structural",
    }


@settings(max_examples=100, deadline=None)
@given(state=_structural_state())
def test_property4_structural_backdrop_pbt(state):
    """Property 4 (structural branch): for structural slides, native shapes take
    priority but the generated Vertex image is preserved as a backdrop (never
    discarded)."""
    plan = _plan_for(state)
    _assert_loss_zero_invariants(state, plan)
    _assert_structural_backdrop(state, plan)


# ==========================================================================
# Feature: pptx-ultra-quality-hybrid-render, Property 14: 손실-0 불변식 보존.
#
# design Property 14 (Validates: Requirements 4.1):
#
#   _For any_ SlideMediaState 조합에 대해, ``has_vertex_image == True``이면
#   ``_select_render_plan``(및 이를 위임받는 ``_select_hybrid_render_plan``)이
#   반환하는 ``vertex_slot``은 결코 ``"none"``이 아니며, 성공적으로 생성된 Vertex
#   이미지의 폐기 개수는 0이다.
#
# 본 확장은 새 파일을 만들지 않고(중복 구현 금지) 위 fix_pbt(Property 3, 손실-0)를
# **하이브리드 레이어**(``_select_hybrid_render_plan``)까지 확장한다. 하이브리드 결정
# 함수는 caller 미지정 슬라이드의 역할 기반 주 렌더러만 배정하고 손실-0 최종 게이트는
# 기존 ``_select_render_plan``에 위임하지만, 자체 안전망으로도
# ``has_vertex_image ⇒ vertex_slot != "none"``을 유지한다. 이 테스트는 두 결정
# 함수 모두에서 손실-0(생성 이미지 폐기 개수 0)이 성립함을 전역 검증한다.
#
# 하이브리드 반환 도메인은 fix 경로와 다르다:
#   primary ∈ {VERTEX_FULLBLEED, HTML_EDITABLE, NATIVE_EDITABLE, NATIVE_SHAPES}
#   vertex_slot ∈ {visual, hero, backdrop, none}
#
# 순수 결정 함수(LLM/게이트웨이/네트워크 0)이므로 완전히 헤르메틱하다.
#
# **Validates: Requirements 4.1**
#
# Run (hermetic — no network, pure decision function):
#   ./venv/bin/python -m pytest scripts/test_pptx_quality_vertex_images_fix_pbt.py -p no:cacheprovider -q
#
# _Requirements: 4.1_
# ==========================================================================

from ai_engine.server import _select_hybrid_render_plan  # noqa: E402

_HYBRID_PRIMARIES = {"VERTEX_FULLBLEED", "HTML_EDITABLE", "NATIVE_EDITABLE", "NATIVE_SHAPES"}
_HYBRID_SLOTS = {"visual", "hero", "backdrop", "none"}


def _hybrid_plan_for(state: dict) -> dict:
    """하이브리드 레이어(역할 기반 주 렌더러 배정) 플랜을 반환한다."""
    return _select_hybrid_render_plan(
        role=state["role"],
        vertex_enabled=state["vertexEnabled"],
        html_enabled=state["htmlEnabled"],
        has_vertex_image=state["hasVertexImage"],
        has_native_diagram=state["hasNativeDiagram"],
        has_image_file=state["hasImageFile"],
        has_slide_bg=state["hasSlideBg"],
    )


def _vertex_discard_count(state: dict, slot: str) -> int:
    """성공 생성된 Vertex 이미지의 폐기 개수(손실-0 계측).

    생성 이미지가 있는데(slot이 어떤 유효 슬롯으로도 배치되지 않고) ``"none"``이면
    이미지가 폐기된 것이므로 1, 그렇지 않으면 0."""
    return 1 if (state["hasVertexImage"] and slot == "none") else 0


def _assert_hybrid_loss_zero(state: dict, plan: dict) -> None:
    """Property 14 불변식 — 하이브리드 플랜의 손실-0 및 주 렌더러 유일성 단언."""
    primary = plan["primary"]
    slot = plan["vertex_slot"]

    # 주 렌더러는 정확히 1개(하이브리드 도메인, R1.5).
    assert primary in _HYBRID_PRIMARIES, (
        f"하이브리드 primary는 {sorted(_HYBRID_PRIMARIES)} 중 하나여야 한다, "
        f"got {primary!r} for state={state!r}"
    )
    assert slot in _HYBRID_SLOTS, (
        f"하이브리드 vertex_slot은 {sorted(_HYBRID_SLOTS)} 중 하나여야 한다, "
        f"got {slot!r} for state={state!r}"
    )

    # 손실-0 (R4.1): has_vertex_image ⇒ vertex_slot != "none".
    if state["hasVertexImage"]:
        assert slot != "none", (
            "손실-0 위반(하이브리드): 생성된 Vertex 이미지가 폐기됨(vertex_slot='none'). "
            f"위임받는 하이브리드 레이어에서도 보존되어야 한다. state={state!r}, plan={plan!r}"
        )

    # 성공 생성 이미지 폐기 개수 == 0.
    assert _vertex_discard_count(state, slot) == 0, (
        "손실-0 위반(하이브리드): 성공 생성 Vertex 이미지 폐기 개수 != 0. "
        f"state={state!r}, plan={plan!r}"
    )


def test_property14_hybrid_loss_zero_exhaustive():
    """전역: 모든 SlideMediaState 조합에서 하이브리드 레이어의 손실-0이 성립한다."""
    checked = 0
    for state in _all_states():
        plan = _hybrid_plan_for(state)
        _assert_hybrid_loss_zero(state, plan)
        checked += 1
    # 도메인이 비자명(모든 role × 유효 bool 조합)임을 확인.
    assert checked >= 5 * 48, f"expected full state coverage, only checked {checked}"


def test_property14_hybrid_delegates_loss_zero_to_select_render_plan():
    """위임 정합성: 하이브리드가 위임받는 기존 _select_render_plan도 동일 상태에서
    손실-0을 유지한다(has_vertex_image ⇒ slot != 'none', 폐기 0)."""
    for state in _all_states():
        base = _plan_for(state)              # 기존 _select_render_plan
        hybrid = _hybrid_plan_for(state)     # 위임받는 하이브리드 레이어
        # 두 결정 함수 모두 손실-0을 보존해야 한다.
        if state["hasVertexImage"]:
            assert base["vertex_slot"] != "none", (
                f"위임 손실-0 위반(_select_render_plan): slot='none', state={state!r}"
            )
            assert hybrid["vertex_slot"] != "none", (
                f"위임 손실-0 위반(_select_hybrid_render_plan): slot='none', state={state!r}"
            )
        assert _vertex_discard_count(state, base["vertex_slot"]) == 0
        assert _vertex_discard_count(state, hybrid["vertex_slot"]) == 0


@settings(max_examples=200, deadline=None)
@given(state=_slide_media_state())
def test_property14_hybrid_loss_zero_pbt(state):
    """Property 14 (손실-0 불변식 보존): for any SlideMediaState,
    _select_hybrid_render_plan(위임받는 하이브리드 레이어)은 has_vertex_image가
    True이면 vertex_slot을 결코 'none'으로 두지 않으며 성공 생성 이미지 폐기 개수는
    0이다."""
    plan = _hybrid_plan_for(state)
    _assert_hybrid_loss_zero(state, plan)
