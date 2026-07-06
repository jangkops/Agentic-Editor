"""Byte-preservation / determinism / marker / dispatcher property tests for the
density-parity upgrades layered on ``ai_engine/slide_templates.py``
(spec: pptx-design-density-parity, tasks 4.1–4.4).

These property-based tests (Hypothesis, >=100 examples each) verify the
*additive, byte-preserving* contract of the new cover/two-column density
fields. Each Correctness Property from design.md is implemented as a single
property-based test, tagged with its property number.

Conventions are mirrored from ``scripts/test_slide_templates_density.py``:
a ``DENSITY_MARKERS`` constant, ``_assert_valid_html`` / ``_density_markers_in``
helpers, base==explicit-no-op-default comparison, and Hypothesis generators.

Everything is hermetic — pure Python, NO network, NO Electron, NO gateway.

Run (hermetic):
  ./venv/bin/python -m pytest scripts/test_slide_density_parity_pbt.py -p no:cacheprovider -q
"""
from __future__ import annotations

import os
import sys

# Make ai_engine importable from repo root.
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "ai_engine"))

import pytest  # noqa: E402
from hypothesis import given, settings, strategies as st, HealthCheck  # noqa: E402

import ai_engine.slide_templates as m  # noqa: E402


# ---------------------------------------------------------------------------
# Density-only markers introduced by THIS spec (pptx-design-density-parity).
# Each appears ONLY when its optional density field is rendered. Written in
# `class="..."` form so they cannot collide with similar substrings.
# ---------------------------------------------------------------------------
COVER_DENSITY_MARKERS = (
    'class="cover-icon-badge"',
    'class="notice-chip"',
    'class="accent-span"',
    'class="step-card-grid"',
)
BODY_DENSITY_MARKERS = (
    'class="section-header-bar"',
    'class="contact-box"',
    'class="note-callout"',
    'class="link-chip"',
    'class="numbered-item"',
    'class="notice-tab"',
    'class="slide-footer"',
    'class="figure-slot"',
)
DENSITY_MARKERS = COVER_DENSITY_MARKERS + BODY_DENSITY_MARKERS

# Documented no-op defaults for every new cover/two_column density field.
COVER_NOOP = dict(icon_badge=None, notice_chip="", accent_spans=None, step_cards=None)
BODY_NOOP = dict(
    left_section_no="", left_section_title="",
    right_section_no="", right_section_title="",
    left_contact=None, right_contact=None,
    left_note="", right_note="",
    left_links=None, right_links=None,
    left_numbered=None, right_numbered=None,
    left_figures=None, right_figures=None,
    notice_tab="", footer_title="", footer_page="",
)


def _assert_valid_html(html: str) -> None:
    assert isinstance(html, str) and html
    assert "<html" in html and "<body" in html and "</html>" in html


def _density_markers_in(html: str):
    return [mk for mk in DENSITY_MARKERS if mk in html]


# ---------------------------------------------------------------------------
# Hypothesis generators.
# ---------------------------------------------------------------------------
_text = st.text(
    alphabet=st.characters(blacklist_categories=("Cs",), blacklist_characters="\x00"),
    min_size=0, max_size=40,
)
_nonempty = st.text(
    alphabet=st.characters(blacklist_categories=("Cs", "Cc"), blacklist_characters="\x00"),
    min_size=1, max_size=24,
)


# ===========================================================================
# Property 1: 바이트 보존 (밀도 필드 미제공 = 명시 no-op 호출)
# Feature: pptx-design-density-parity, Property 1: For any valid cover/body
#   content, a render_* call passing NONE of the new density fields produces a
#   byte-identical output to a call setting every new density field to its
#   documented no-op default, and that output contains no Density_Marker.
# Validates: Requirements 4.1, 4.2
# ===========================================================================
@settings(max_examples=100, deadline=None,
          suppress_health_check=[HealthCheck.too_slow])
@given(
    title=_text, subtitle=_text, eyebrow=_text, footer=_text,
    left=st.lists(_text, min_size=0, max_size=4),
    right=st.lists(_text, min_size=0, max_size=4),
)
def test_pbt_property1_byte_preservation(title, subtitle, eyebrow, footer, left, right):
    # --- cover: implicit (no new fields) == explicit no-op defaults ---
    base_cover = m.render_cover_slide(
        title=title, subtitle=subtitle, eyebrow=eyebrow, footer=footer)
    explicit_cover = m.render_cover_slide(
        title=title, subtitle=subtitle, eyebrow=eyebrow, footer=footer, **COVER_NOOP)
    _assert_valid_html(base_cover)
    assert base_cover == explicit_cover, "cover: no-op defaults must be byte-identical"
    assert _density_markers_in(base_cover) == [], "cover: no density markers when fields absent"

    # --- two_column: implicit (no new fields) == explicit no-op defaults ---
    left_content = "\n".join(left)
    right_content = "\n".join(right)
    base_body = m.render_two_column(
        title=title, left_content=left_content, right_content=right_content,
        subtitle=subtitle)
    explicit_body = m.render_two_column(
        title=title, left_content=left_content, right_content=right_content,
        subtitle=subtitle, **BODY_NOOP)
    _assert_valid_html(base_body)
    assert base_body == explicit_body, "two_column: no-op defaults must be byte-identical"
    assert _density_markers_in(base_body) == [], "two_column: no density markers when fields absent"


# ===========================================================================
# Property 2: 렌더 결정성 (반복 호출 동일 바이트)
# Feature: pptx-design-density-parity, Property 2: For any identical input
#   arguments, calling the same render_* function repeatedly produces the exact
#   same byte sequence every time.
# Validates: Requirements 4.6
# ===========================================================================
@settings(max_examples=100, deadline=None,
          suppress_health_check=[HealthCheck.too_slow])
@given(
    title=_nonempty, subtitle=_text, footer=_text,
    notice=_text,
    cards=st.lists(
        st.fixed_dictionaries({"label": _nonempty, "description": _text}),
        min_size=0, max_size=8),
    numbered=st.lists(_nonempty, min_size=0, max_size=10),
    note=_text, tab=_text,
)
def test_pbt_property2_render_determinism(title, subtitle, footer, notice,
                                          cards, numbered, note, tab):
    # Active density fields make the determinism check meaningful.
    c1 = m.render_cover_slide(title=title, subtitle=subtitle, footer=footer,
                              notice_chip=notice, step_cards=cards or None)
    c2 = m.render_cover_slide(title=title, subtitle=subtitle, footer=footer,
                              notice_chip=notice, step_cards=cards or None)
    assert c1 == c2, "cover render must be deterministic (identical bytes)"

    b1 = m.render_two_column(title=title, left_content="a\nb", right_content="x",
                             subtitle=subtitle, left_numbered=numbered or None,
                             left_note=note, notice_tab=tab)
    b2 = m.render_two_column(title=title, left_content="a\nb", right_content="x",
                             subtitle=subtitle, left_numbered=numbered or None,
                             left_note=note, notice_tab=tab)
    assert b1 == b2, "two_column render must be deterministic (identical bytes)"


# ===========================================================================
# Property 3: 밀도 요소 독립 활성과 고유 마커
# Feature: pptx-design-density-parity, Property 3: For any random on/off subset
#   of the density fields, ONLY the activated elements' Density_Markers appear
#   in the output (each unique within that output) and inactive elements'
#   markers are absent.
# Validates: Requirements 2.8, 4.5
# ===========================================================================
# Single-instance activating values so every active marker appears exactly once.
_TITLE = "온보딩 매뉴얼 표지"
_ACCENT_SPAN = "매뉴얼"  # occurs exactly once in _TITLE


@settings(max_examples=100, deadline=None,
          suppress_health_check=[HealthCheck.too_slow])
@given(
    # cover toggles
    icon_on=st.booleans(), chip_on=st.booleans(),
    accent_on=st.booleans(), step_on=st.booleans(),
    # body toggles
    section_on=st.booleans(), contact_on=st.booleans(), note_on=st.booleans(),
    link_on=st.booleans(), numbered_on=st.booleans(),
    tab_on=st.booleans(), footer_on=st.booleans(), figure_on=st.booleans(),
)
def test_pbt_property3_independent_activation_unique_markers(
        icon_on, chip_on, accent_on, step_on,
        section_on, contact_on, note_on, link_on, numbered_on,
        tab_on, footer_on, figure_on):
    # --- cover subset ---
    cover_html = m.render_cover_slide(
        title=_TITLE,
        icon_badge="check" if icon_on else None,
        notice_chip="공지사항" if chip_on else "",
        accent_spans=[_ACCENT_SPAN] if accent_on else None,
        step_cards=[{"label": "STEP 01", "description": "설치"}] if step_on else None,
    )
    _assert_valid_html(cover_html)
    cover_expected = {
        'class="cover-icon-badge"': icon_on,
        'class="notice-chip"': chip_on,
        'class="accent-span"': accent_on,
        'class="step-card-grid"': step_on,
    }
    for marker, active in cover_expected.items():
        cnt = cover_html.count(marker)
        if active:
            assert cnt == 1, f"cover active marker {marker} must appear exactly once, got {cnt}"
        else:
            assert cnt == 0, f"cover inactive marker {marker} must be absent, got {cnt}"
    # body markers never leak into a cover render
    for marker in BODY_DENSITY_MARKERS:
        assert marker not in cover_html

    # --- body subset (single-item lists → each marker unique) ---
    body_html = m.render_two_column(
        title=_TITLE, left_content="a\nb", right_content="x\ny",
        left_section_no="01" if section_on else "",
        left_section_title="섹션" if section_on else "",
        left_contact={"items": [{"label": "담당", "value": "김"}]} if contact_on else None,
        left_note="참고 사항" if note_on else "",
        left_links=[{"label": "링크"}] if link_on else None,
        left_numbered=["첫 단계"] if numbered_on else None,
        notice_tab="공지" if tab_on else "",
        footer_title="러닝 타이틀" if footer_on else "",
        left_figures=[{"caption": "그림 캡션"}] if figure_on else None,
    )
    _assert_valid_html(body_html)
    body_expected = {
        'class="section-header-bar"': section_on,
        'class="contact-box"': contact_on,
        'class="note-callout"': note_on,
        'class="link-chip"': link_on,
        'class="numbered-item"': numbered_on,
        'class="notice-tab"': tab_on,
        'class="slide-footer"': footer_on,
        'class="figure-slot"': figure_on,
    }
    for marker, active in body_expected.items():
        cnt = body_html.count(marker)
        if active:
            assert cnt == 1, f"body active marker {marker} must appear exactly once, got {cnt}"
        else:
            assert cnt == 0, f"body inactive marker {marker} must be absent, got {cnt}"
    # cover-only markers never leak into a body render
    for marker in COVER_DENSITY_MARKERS:
        assert marker not in body_html


# ===========================================================================
# Property 14: Layout_Dispatcher의 밀도 필드 무중단 전달
# Feature: pptx-design-density-parity, Property 14: For any data dict including
#   the new density fields, render_layout forwards them to the target render_*
#   function without TypeError and produces output of length > 0.
# Validates: Requirements 4.4, 6.1
# ===========================================================================
@settings(max_examples=100, deadline=None,
          suppress_health_check=[HealthCheck.too_slow])
@given(
    title=_nonempty, subtitle=_text, notice=_text, tab=_text, note=_text,
    cards=st.lists(
        st.fixed_dictionaries({"label": _nonempty, "description": _text}),
        min_size=1, max_size=8),
    numbered=st.lists(_nonempty, min_size=1, max_size=10),
    links=st.lists(st.fixed_dictionaries({"label": _nonempty}), min_size=1, max_size=8),
)
def test_pbt_property14_dispatcher_forwards_density_fields(
        title, subtitle, notice, tab, note, cards, numbered, links):
    # cover with new density fields in data → no TypeError, len > 0
    cover_data = {
        "title": title, "subtitle": subtitle,
        "icon_badge": "check", "notice_chip": notice,
        "accent_spans": [title[:3]] if title else None,
        "step_cards": cards,
    }
    cover_out = m.render_layout("cover", cover_data)
    assert isinstance(cover_out, str) and len(cover_out) > 0
    _assert_valid_html(cover_out)

    # two_column with new density fields in data → no TypeError, len > 0
    body_data = {
        "title": title, "left_content": "a\nb", "right_content": "x",
        "subtitle": subtitle,
        "left_section_no": "01", "left_section_title": "섹션",
        "left_contact": {"items": [{"label": "담당", "value": "김"}]},
        "left_note": note, "left_links": links,
        "left_numbered": numbered,
        "left_figures": [{"caption": "캡션"}],
        "notice_tab": tab, "footer_title": "타이틀", "footer_page": "1/3",
    }
    body_out = m.render_layout("two_column", body_data)
    assert isinstance(body_out, str) and len(body_out) > 0
    _assert_valid_html(body_out)


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(pytest.main([__file__, "-q", "-p", "no:cacheprovider"]))
