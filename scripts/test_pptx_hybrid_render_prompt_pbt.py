"""Property-based tests — spec: pptx-ultra-quality-hybrid-render (feature).

풀블리드 Vertex 프롬프트 빌더 ``ai_engine.server._build_fullbleed_vertex_prompt``
를 헤르메틱하게 검증한다. 이 함수는 LLM/게이트웨이/네트워크 호출이 없는 순수 결정
함수(문자열 조립)이므로 본 테스트는 완전히 헤르메틱하다 (네트워크 호출 0, Vertex 비활성).

풀블리드 대상 role = {cover, section, visual}.

Design Correctness Properties 10 / 11 / 12 및 Vertex 프롬프트 빌더 절, 그리고 프롬프트
문자열 스냅샷(example/edge)을 검증한다. 각 property 테스트는 hypothesis 로 최소
100 iteration 반복한다.

Run (hermetic — no network, pure function):
  ./venv/bin/python -m pytest scripts/test_pptx_hybrid_render_prompt_pbt.py -p no:cacheprovider -q
"""
from __future__ import annotations

import os
import sys

# Make ai_engine importable from repo root.
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from hypothesis import given, settings, strategies as st  # noqa: E402

from ai_engine.server import _build_fullbleed_vertex_prompt  # noqa: E402


# ---------------------------------------------------------------------------
# 공통 상수 / 전략
# ---------------------------------------------------------------------------

# 풀블리드 대상 role (설계 결정 테이블).
_FULLBLEED_ROLES = ("cover", "section", "visual")

# no-text negative prompt 가 반드시 포함해야 하는 억제 용어 (R3.2).
_NEG_TERMS = ("text", "words", "letters", "watermark")

# 팔레트 None 폴백 시 기본 색상 표현 (설계 Vertex 프롬프트 빌더 / R3.4).
_DEFAULT_COLOR_EXPR = "professional deep navy and blue palette with a single warm accent"

_MIN_ITERS = 120  # 설계 요구: 각 property ≥ 100 iteration.


# 임의의 title — 빈 문자열/공백/유니코드/장문 포함.
_titles = st.one_of(
    st.none(),
    st.text(max_size=200),
    st.sampled_from(["", "   ", "2026 전략 로드맵", "Growth Strategy 🚀"]),
)

# 임의의 bullets — 리스트/비리스트/혼합 스칼라 포함 (함수가 str() 로 강제 변환).
_bullets = st.one_of(
    st.none(),
    st.lists(
        st.one_of(
            st.text(max_size=60),
            st.integers(min_value=-1000, max_value=1000),
            st.none(),
            st.booleans(),
        ),
        max_size=8,
    ),
    st.text(max_size=40),  # 비리스트 iterable (문자 단위 순회)
)

# 유효 #RRGGBB 색.
_hex_color = st.from_regex(r"#[0-9A-Fa-f]{6}", fullmatch=True)

# 임의의 style_profile — None/비 dict/색상 dict(유효·무효 혼합) 포함.
_style_profiles = st.one_of(
    st.none(),
    st.integers(),
    st.text(max_size=20),
    st.fixed_dictionaries({}),
    st.dictionaries(
        keys=st.sampled_from(
            ["primaryColor", "secondaryColor", "accentColor", "foo", "bar"]
        ),
        values=st.one_of(_hex_color, st.text(max_size=10), st.none()),
        max_size=5,
    ),
)


# ---------------------------------------------------------------------------
# Property 10 — 프롬프트 빌더의 no-text negative prompt
# ---------------------------------------------------------------------------
# Feature: pptx-ultra-quality-hybrid-render, Property 10: 프롬프트 빌더의 no-text
#   negative prompt — For any role ∈ {cover, section, visual} 과 임의 title/bullets/
#   style_profile 에 대해, negative_prompt 길이 ≥ 1 이고 text/words/letters/watermark
#   억제 용어를 포함한다.
# Validates: Requirements 3.2
@settings(max_examples=_MIN_ITERS)
@given(
    role=st.sampled_from(_FULLBLEED_ROLES),
    title=_titles,
    bullets=_bullets,
    style_profile=_style_profiles,
)
def test_property10_no_text_negative_prompt(role, title, bullets, style_profile):
    prompt, negative = _build_fullbleed_vertex_prompt(role, title, bullets, style_profile)

    assert isinstance(negative, str)
    assert len(negative) >= 1
    low = negative.lower()
    for term in _NEG_TERMS:
        assert term in low, f"negative_prompt 에 '{term}' 억제 용어 누락: {negative!r}"

    # 프롬프트 본체는 항상 16:9 를 명시한다 (설계 Vertex 프롬프트 빌더 / R3.1).
    assert isinstance(prompt, str) and len(prompt) >= 1
    assert "16:9" in prompt


# ---------------------------------------------------------------------------
# Property 11 — 역할별 프롬프트 구별성
# ---------------------------------------------------------------------------
# Feature: pptx-ultra-quality-hybrid-render, Property 11: 역할별 프롬프트 구별성 —
#   For any 서로 다른 두 풀블리드 role (둘 다 {cover, section, visual}) 와 동일한
#   title/bullets/style_profile 에 대해, prompt 문자열은 서로 다르다.
# Validates: Requirements 3.3
@settings(max_examples=_MIN_ITERS)
@given(
    roles=st.sampled_from(
        [
            ("cover", "section"),
            ("cover", "visual"),
            ("section", "visual"),
            ("section", "cover"),
            ("visual", "cover"),
            ("visual", "section"),
        ]
    ),
    title=_titles,
    bullets=_bullets,
    style_profile=_style_profiles,
)
def test_property11_role_prompt_distinctness(roles, title, bullets, style_profile):
    role_a, role_b = roles
    assert role_a != role_b  # 전제: 서로 다른 두 풀블리드 role.

    prompt_a, _ = _build_fullbleed_vertex_prompt(role_a, title, bullets, style_profile)
    prompt_b, _ = _build_fullbleed_vertex_prompt(role_b, title, bullets, style_profile)

    assert prompt_a != prompt_b, (
        f"서로 다른 role({role_a} vs {role_b}) 이 동일 프롬프트를 산출: {prompt_a!r}"
    )


# ---------------------------------------------------------------------------
# Property 12 — 프롬프트 빌더의 바이트 결정성
# ---------------------------------------------------------------------------
# Feature: pptx-ultra-quality-hybrid-render, Property 12: 프롬프트 빌더의 바이트
#   결정성 — For any 동일한 (role, title, bullets, style_profile) 입력에 대해,
#   반복 호출은 라이브 Vertex 없이 바이트 단위로 동일한 (prompt, negative_prompt)
#   (팔레트/색상 표현 포함) 를 산출한다.
# Validates: Requirements 3.4, 3.6
@settings(max_examples=_MIN_ITERS)
@given(
    role=st.sampled_from(_FULLBLEED_ROLES + ("unknown", "content", "", None)),
    title=_titles,
    bullets=_bullets,
    style_profile=_style_profiles,
)
def test_property12_byte_determinism(role, title, bullets, style_profile):
    p1, n1 = _build_fullbleed_vertex_prompt(role, title, bullets, style_profile)
    p2, n2 = _build_fullbleed_vertex_prompt(role, title, bullets, style_profile)
    p3, n3 = _build_fullbleed_vertex_prompt(role, title, bullets, style_profile)

    # 문자열 동일성.
    assert p1 == p2 == p3
    assert n1 == n2 == n3
    # 바이트 단위 동일성 (팔레트/색상 표현 포함, 인코딩까지 동일).
    assert p1.encode("utf-8") == p2.encode("utf-8") == p3.encode("utf-8")
    assert n1.encode("utf-8") == n2.encode("utf-8") == n3.encode("utf-8")


# ---------------------------------------------------------------------------
# 프롬프트 문자열 스냅샷 단위 테스트 (example/edge) — Task 4.5
# ---------------------------------------------------------------------------
# Validates: Requirements 3.2, 3.3
#
# cover/section/visual 각 역할의 대표 입력에 대한 구체 프롬프트 문자열을 고정 스냅샷으로
# 단언한다. style_profile=None 폴백 경로의 기본 색상 표현(_DEFAULT_COLOR_EXPR)이 삽입됨을
# 함께 검증한다.

_SNAP_TITLE = "Growth Strategy"
_SNAP_BULLETS = ["market expansion", "profit"]
_SNAP_THEME = "Growth Strategy market expansion profit"

_SNAP_NEGATIVE = (
    "text, words, letters, captions, typography, watermark, fake logo, brand name, "
    "emoji, charts, diagrams, distorted text, unreadable artifacts, childish clipart"
)

_SNAP_COVER = (
    'A commercial-grade hero title background for the cover of a premium corporate '
    'presentation. Theme: "Growth Strategy market expansion profit". Style: cinematic '
    'depth, dramatic yet elegant lighting, expansive negative space in the upper-left '
    'for an overlaid title, refined executive aesthetic. '
    'professional deep navy and blue palette with a single warm accent. '
    'Balanced composition, 16:9.'
)

_SNAP_SECTION = (
    'A chapter divider ambient background introducing a new section of a corporate '
    'presentation. Theme: "Growth Strategy market expansion profit". Style: calm '
    'minimalist atmosphere, soft gradients and gentle abstract texture, generous empty '
    'space for a section heading, understated professional mood. '
    'professional deep navy and blue palette with a single warm accent. '
    'Balanced composition, 16:9.'
)

_SNAP_VISUAL = (
    'An editorial photographic hero visual for a corporate presentation slide. '
    'Theme: "Growth Strategy market expansion profit". Style: premium professional '
    'photography, natural soft lighting, shallow depth of field, ample negative space '
    'for overlaid text, refined corporate aesthetic. '
    'professional deep navy and blue palette with a single warm accent. '
    'Balanced composition, 16:9.'
)


def test_snapshot_cover_palette_none_fallback():
    prompt, negative = _build_fullbleed_vertex_prompt(
        "cover", _SNAP_TITLE, _SNAP_BULLETS, None
    )
    assert prompt == _SNAP_COVER
    assert negative == _SNAP_NEGATIVE
    # 팔레트 None 폴백 기본 색상 표현 삽입 확인.
    assert _DEFAULT_COLOR_EXPR in prompt


def test_snapshot_section_palette_none_fallback():
    prompt, negative = _build_fullbleed_vertex_prompt(
        "section", _SNAP_TITLE, _SNAP_BULLETS, None
    )
    assert prompt == _SNAP_SECTION
    assert negative == _SNAP_NEGATIVE
    assert _DEFAULT_COLOR_EXPR in prompt


def test_snapshot_visual_palette_none_fallback():
    prompt, negative = _build_fullbleed_vertex_prompt(
        "visual", _SNAP_TITLE, _SNAP_BULLETS, None
    )
    assert prompt == _SNAP_VISUAL
    assert negative == _SNAP_NEGATIVE
    assert _DEFAULT_COLOR_EXPR in prompt


def test_snapshot_three_roles_are_distinct():
    # 세 스냅샷이 서로 다른 프롬프트 본문임을 명시적으로 고정 (R3.3 example).
    assert len({_SNAP_COVER, _SNAP_SECTION, _SNAP_VISUAL}) == 3


def test_snapshot_non_dict_profile_uses_default_color_expr():
    # 비 dict style_profile 도 팔레트 None 폴백 경로 → 기본 색상 표현 사용.
    prompt, _ = _build_fullbleed_vertex_prompt(
        "cover", _SNAP_TITLE, _SNAP_BULLETS, "not-a-dict"
    )
    assert prompt == _SNAP_COVER
    assert _DEFAULT_COLOR_EXPR in prompt
