"""Property-based tests — spec: pptx-ultra-quality-hybrid-render (feature).

하이브리드 렌더 라우팅의 순수 결정 함수 두 개를 헤르메틱하게 검증한다:

  - ``ai_engine.server._classify_slide_role``       (역할 판정)
  - ``ai_engine.server._select_hybrid_render_plan`` (역할 → 주 렌더러/슬롯 배정)

두 함수 모두 LLM/게이트웨이/네트워크 호출이 없는 순수 함수이므로 이 테스트는 완전히
헤르메틱하다 (네트워크 호출 0, Vertex 비활성). 각 correctness property는 단일
property-based 테스트로 구현하며 hypothesis 로 최소 100 iteration 반복한다.

Design Properties 1–6 및 "결정 테이블" 절을 검증한다.

Run (hermetic — no network, pure decision functions):
  ./venv/bin/python -m pytest scripts/test_pptx_hybrid_render_plan_pbt.py -p no:cacheprovider -q
"""
from __future__ import annotations

import os
import sys

# Make ai_engine importable from repo root.
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from hypothesis import given, settings, strategies as st  # noqa: E402

from ai_engine.server import (  # noqa: E402
    _classify_slide_role,
    _select_hybrid_render_plan,
)


# ---------------------------------------------------------------------------
# 공통 상수 / 전략
# ---------------------------------------------------------------------------

# _classify_slide_role 가 산출하는 Slide_Role 열거형 (design Slide_Role 열거형).
_ROLE_SET = frozenset({"cover", "section", "structural", "content", "visual"})

# _select_hybrid_render_plan 반환 계약 (design Data Models §RenderPlan).
_HYBRID_PRIMARIES = frozenset(
    {"VERTEX_FULLBLEED", "HTML_EDITABLE", "NATIVE_EDITABLE", "NATIVE_SHAPES"}
)
_HYBRID_SLOTS = frozenset({"visual", "hero", "backdrop", "none"})

# 풀블리드 대상 역할 (결정 테이블).
_FULLBLEED_ROLES = ("cover", "section", "visual")

_MIN_ITERS = 120  # 설계 요구: 각 property ≥ 100 iteration.


# 임의 JSON 유사 스칼라 값.
_scalar_values = st.one_of(
    st.none(),
    st.booleans(),
    st.integers(min_value=-1000, max_value=1000),
    st.text(max_size=40),
)

# 임의 구조의 slide dict — 알려진 키(title/heading/bullets/body/imagePrompt)를 종종
# 포함하되 임의 키/값도 섞는다. Property 1(전역성)/determinism 검증용.
_arbitrary_slide = st.dictionaries(
    keys=st.one_of(
        st.sampled_from(
            ["title", "heading", "bullets", "body", "imagePrompt",
             "imageFile", "slideBackground", "notes", "layout"]
        ),
        st.text(max_size=12),
    ),
    values=st.one_of(
        _scalar_values,
        st.lists(st.text(max_size=30), max_size=6),
    ),
    max_size=8,
)

# "신호 없는" content 슬라이드 생성기 (Property 2).
# 안전 알파벳 {a-g, space}만 사용 → 구조/카드/비주얼 트리거 키워드(flow/tree/architecture/
# kpi/metric/구조/흐름 등)를 형성할 수 없고, imagePrompt 키를 절대 넣지 않으므로
# visual intent 도 발생하지 않는다 → 역할은 결정론적으로 "content".
_safe_text = st.text(alphabet="abcdefg ", max_size=40)
_no_signal_slide = st.fixed_dictionaries(
    {
        "title": _safe_text,
        "bullets": st.lists(_safe_text, max_size=5),
    }
)


# ---------------------------------------------------------------------------
# Property 1 — 역할 판정의 전역성 (2.2)
# ---------------------------------------------------------------------------
# Feature: pptx-ultra-quality-hybrid-render, Property 1: For any slide dict(임의
#   구조·필드)와 is_cover 불리언에 대해, _classify_slide_role은 정확히
#   {cover, section, structural, content, visual} 집합의 원소 하나를 반환한다.
# Validates: Requirements 1.1
@settings(max_examples=_MIN_ITERS, deadline=None)
@given(slide=_arbitrary_slide, is_cover=st.booleans())
def test_property1_role_classification_totality(slide, is_cover):
    role = _classify_slide_role(slide, is_cover)
    # 정확히 하나의 열거형 원소(문자열)를 반환.
    assert isinstance(role, str)
    assert role in _ROLE_SET, f"unexpected role {role!r} for slide={slide!r}"
    # is_cover 는 항상 cover 로 단락(short-circuit)된다.
    if is_cover:
        assert role == "cover"


# ---------------------------------------------------------------------------
# Property 2 — 모호 입력의 content 결정론 폴백 (2.3)
# ---------------------------------------------------------------------------
# Feature: pptx-ultra-quality-hybrid-render, Property 2: For any 구조 신호(diagram
#   kind)도 비주얼 신호(visual intent)도 없거나 분류 중 예외를 유발하는 slide dict에
#   대해, 최종 확정 role은 결정론적으로 content이며 동일 입력은 항상 동일 결과를 낸다.
# Validates: Requirements 1.8
@settings(max_examples=_MIN_ITERS, deadline=None)
@given(slide=_no_signal_slide, extra=_arbitrary_slide)
def test_property2_ambiguous_falls_back_to_content(slide, extra):
    # (a) 구조/비주얼 신호가 없는 슬라이드 → 결정론적으로 content.
    role = _classify_slide_role(slide, False)
    assert role == "content", f"expected content for no-signal slide, got {role!r}"

    # (b) 결정성: 동일 입력은 항상 동일 결과. 임의 dict 에 대해서도 성립.
    r1 = _classify_slide_role(extra, False)
    r2 = _classify_slide_role(extra, False)
    assert r1 == r2, f"non-deterministic role: {r1!r} != {r2!r} for {extra!r}"
    assert r1 in _ROLE_SET


# ---------------------------------------------------------------------------
# Property 3 — 풀블리드 라우팅 규칙 (2.4)
# ---------------------------------------------------------------------------
# Feature: pptx-ultra-quality-hybrid-render, Property 3: For any role ∈
#   {cover, section, visual} 이고 caller가 imageFile/slideBackground를 지정하지 않은
#   상태에서, vertex_enabled == True 이면 _select_hybrid_render_plan은
#   primary == "VERTEX_FULLBLEED" 이고 vertex_slot == "visual" 인 플랜을 반환한다
#   (주 렌더러는 정확히 하나).
# Validates: Requirements 1.2, 1.5
@settings(max_examples=_MIN_ITERS, deadline=None)
@given(
    role=st.sampled_from(_FULLBLEED_ROLES),
    html_enabled=st.booleans(),
    has_vertex_image=st.booleans(),
    has_native_diagram=st.booleans(),
)
def test_property3_fullbleed_routing(role, html_enabled, has_vertex_image,
                                     has_native_diagram):
    plan = _select_hybrid_render_plan(
        role=role,
        vertex_enabled=True,
        html_enabled=html_enabled,
        has_vertex_image=has_vertex_image,
        has_native_diagram=has_native_diagram,
        has_image_file=False,   # caller 미지정
        has_slide_bg=False,     # caller 미지정
    )
    # 주 렌더러는 정확히 하나(스칼라)이며 계약 집합의 원소.
    assert isinstance(plan["primary"], str)
    assert plan["primary"] in _HYBRID_PRIMARIES
    assert plan["vertex_slot"] in _HYBRID_SLOTS
    # 풀블리드 라우팅 규칙.
    assert plan["primary"] == "VERTEX_FULLBLEED"
    assert plan["vertex_slot"] == "visual"
    assert plan["editable"] is False


# ---------------------------------------------------------------------------
# Property 4 — Vertex 비활성 시 편집 경로 폴백 (2.5)
# ---------------------------------------------------------------------------
# Feature: pptx-ultra-quality-hybrid-render, Property 4: For any role ∈
#   {cover, section, visual} 이고 vertex_enabled == False인 caller-미지정 슬라이드에
#   대해, _select_hybrid_render_plan은 editable == True 이고
#   primary ∈ {HTML_EDITABLE(html on), NATIVE_EDITABLE(html off)}인 플랜을 반환한다.
# Validates: Requirements 1.6, 1.5
@settings(max_examples=_MIN_ITERS, deadline=None)
@given(
    role=st.sampled_from(_FULLBLEED_ROLES),
    html_enabled=st.booleans(),
    has_vertex_image=st.booleans(),
    has_native_diagram=st.booleans(),
)
def test_property4_vertex_disabled_editable_fallback(role, html_enabled,
                                                     has_vertex_image,
                                                     has_native_diagram):
    plan = _select_hybrid_render_plan(
        role=role,
        vertex_enabled=False,
        html_enabled=html_enabled,
        has_vertex_image=has_vertex_image,
        has_native_diagram=has_native_diagram,
        has_image_file=False,   # caller 미지정
        has_slide_bg=False,     # caller 미지정
    )
    assert plan["primary"] in _HYBRID_PRIMARIES
    # 편집 가능 폴백.
    assert plan["editable"] is True
    expected = "HTML_EDITABLE" if html_enabled else "NATIVE_EDITABLE"
    assert plan["primary"] == expected, (
        f"html_enabled={html_enabled} expected {expected}, got {plan['primary']!r}"
    )
    assert plan["primary"] in {"HTML_EDITABLE", "NATIVE_EDITABLE"}


# ---------------------------------------------------------------------------
# Property 5 — content 라우팅 규칙 (2.6)
# ---------------------------------------------------------------------------
# Feature: pptx-ultra-quality-hybrid-render, Property 5: For any role == "content"
#   인 caller-미지정 슬라이드에 대해, 모든 vertex_enabled / html_enabled 조합에서
#   _select_hybrid_render_plan은 primary == "NATIVE_EDITABLE" 이고 editable == True
#   인 플랜을 반환한다 (주 렌더러는 정확히 하나).
# Validates: Requirements 1.3, 1.5
@settings(max_examples=_MIN_ITERS, deadline=None)
@given(
    vertex_enabled=st.booleans(),
    html_enabled=st.booleans(),
    has_vertex_image=st.booleans(),
    has_native_diagram=st.booleans(),
)
def test_property5_content_routing(vertex_enabled, html_enabled,
                                   has_vertex_image, has_native_diagram):
    plan = _select_hybrid_render_plan(
        role="content",
        vertex_enabled=vertex_enabled,
        html_enabled=html_enabled,
        has_vertex_image=has_vertex_image,
        has_native_diagram=has_native_diagram,
        has_image_file=False,   # caller 미지정
        has_slide_bg=False,     # caller 미지정
    )
    # 주 렌더러는 정확히 하나(스칼라)이며 항상 NATIVE_EDITABLE.
    assert isinstance(plan["primary"], str)
    assert plan["primary"] == "NATIVE_EDITABLE"
    assert plan["editable"] is True
    assert plan["vertex_slot"] in _HYBRID_SLOTS


# ---------------------------------------------------------------------------
# Property 6 — structural 라우팅 + 손실-0 backdrop (2.7)
# ---------------------------------------------------------------------------
# Feature: pptx-ultra-quality-hybrid-render, Property 6: For any role == "structural"
#   인 슬라이드에 대해, _select_hybrid_render_plan은 primary == "NATIVE_SHAPES" 를
#   반환하고, has_vertex_image == True이면 vertex_slot == "backdrop", 아니면 "none" 을
#   반환한다.
# Validates: Requirements 1.4, 1.5, 4.5
@settings(max_examples=_MIN_ITERS, deadline=None)
@given(
    vertex_enabled=st.booleans(),
    html_enabled=st.booleans(),
    has_vertex_image=st.booleans(),
    has_native_diagram=st.booleans(),
    has_image_file=st.booleans(),
    has_slide_bg=st.booleans(),
)
def test_property6_structural_routing_backdrop(vertex_enabled, html_enabled,
                                               has_vertex_image,
                                               has_native_diagram,
                                               has_image_file, has_slide_bg):
    plan = _select_hybrid_render_plan(
        role="structural",
        vertex_enabled=vertex_enabled,
        html_enabled=html_enabled,
        has_vertex_image=has_vertex_image,
        has_native_diagram=has_native_diagram,
        has_image_file=has_image_file,
        has_slide_bg=has_slide_bg,
    )
    assert plan["primary"] == "NATIVE_SHAPES"
    expected_slot = "backdrop" if has_vertex_image else "none"
    assert plan["vertex_slot"] == expected_slot, (
        f"has_vertex_image={has_vertex_image} expected slot {expected_slot!r}, "
        f"got {plan['vertex_slot']!r}"
    )
    assert plan["editable"] is True
