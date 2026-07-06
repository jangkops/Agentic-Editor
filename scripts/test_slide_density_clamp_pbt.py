"""Property-based tests for the clamp / text / sequence / emphasis / single-
instance density invariants of ai_engine/slide_templates.py
(spec: pptx-design-density-parity, task 5 — design Properties 4–8).

Each Correctness Property is implemented as a SINGLE Hypothesis property-based
test (>=100 examples) driven through the PUBLIC render API
(`render_cover_slide` / `render_two_column`) so the assertions hold end-to-end,
not just at the helper level. Everything is hermetic — pure Python, NO network,
NO Electron, NO gateway — mirroring the conventions of
`scripts/test_slide_templates_density.py`.

Properties covered:
  - Property 4: item-count clamp (step_cards<=6, contact<=5, links<=6,
    numbered<=8, figures<=10); over-limit input still renders (count == max),
    never crashes.
  - Property 5: text length clamp + ellipsis ("…") for notice_chip<=40,
    cover footer<=80, section_title<=40, note<=300, notice_tab<=20,
    footer_title<=40, contact label<=30, link label<=30.
  - Property 6: numbered list badges are sequential 1..n.
  - Property 7: only accent_spans substrings that actually occur in the title
    get an `accent-span` marker; non-occurring spans stay plain text.
  - Property 8: single-instance cover elements (icon_badge, notice_chip,
    accent bar) each emit exactly one marker.

Run (hermetic):
  ./venv/bin/python -m pytest scripts/test_slide_density_clamp_pbt.py -p no:cacheprovider -q
"""
from __future__ import annotations

import os
import re
import sys

# Make ai_engine importable from repo root.
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "ai_engine"))

import pytest  # noqa: E402
from hypothesis import given, settings, strategies as st, HealthCheck  # noqa: E402

import ai_engine.slide_templates as m  # noqa: E402


# ---------------------------------------------------------------------------
# Generators. A whitespace-free, HTML-safe alphabet keeps html.escape an
# identity and prevents leading/trailing-whitespace stripping from changing the
# expected length/text, so the clamp/sequence assertions stay deterministic.
# ---------------------------------------------------------------------------
_ALNUM = "abcdefghijABCDEFGH0123456789가나다라마"
_word = st.text(alphabet=_ALNUM, min_size=1, max_size=8)


def _txt(maxlen: int):
    """Non-empty HTML-safe text of length 1..maxlen (spans clamp boundaries)."""
    return st.text(alphabet=_ALNUM, min_size=1, max_size=maxlen)


def _assert_valid_html(html: str) -> None:
    assert isinstance(html, str) and html
    assert "<html" in html and "<body" in html and "</html>" in html


def _inner(html: str, cls: str):
    """Return the inner text of the first element carrying class ``cls``."""
    match = re.search(r'class="' + re.escape(cls) + r'"[^>]*>([^<]*)<', html)
    return match.group(1) if match else None


def _check_clamp(text: str, rendered, limit: int) -> None:
    """Assert ``rendered`` is the clamp of ``text`` to ``limit`` (+ ellipsis)."""
    assert rendered is not None, f"expected a rendered element for {text!r}"
    if len(text) > limit:
        assert rendered.endswith("…"), "over-limit text must gain an ellipsis"
        assert len(rendered) == limit + 1, "display length == limit + ellipsis"
        assert rendered[:limit] == text[:limit]
    else:
        assert rendered == text, "within-limit text must pass through unchanged"


# ===========================================================================
# Feature: pptx-design-density-parity, Property 4: 항목 수 클램프 (상한 보장)
# For any density list input, the rendered item count never exceeds that
# element's maximum (step_cards 6, contact 5, links 6, numbered 8, figures 10),
# and over-limit input still renders (count == max) instead of crashing.
# Validates: Requirements 1.4, 2.2, 2.4, 2.5, 2.10, 3.1, 3.9
# ===========================================================================
@settings(max_examples=120, deadline=None,
          suppress_health_check=[HealthCheck.too_slow])
@given(
    steps=st.lists(_word, min_size=0, max_size=10),
    contacts=st.lists(_word, min_size=0, max_size=9),
    links=st.lists(_word, min_size=0, max_size=10),
    numbered=st.lists(_word, min_size=0, max_size=12),
    figures=st.lists(_word, min_size=0, max_size=14),
)
def test_property_4_item_count_clamp(steps, contacts, links, numbered, figures):
    cover = m.render_cover_slide(
        title="표지", step_cards=[{"label": s} for s in steps])
    _assert_valid_html(cover)
    assert cover.count('class="step-card"') == min(len(steps), 6)

    body = m.render_two_column(
        title="본문", left_content="x", right_content="y",
        left_contact={"items": [{"label": c} for c in contacts]},
        left_links=[{"label": link} for link in links],
        left_numbered=list(numbered),
        left_figures=[{"caption": f} for f in figures],
    )
    _assert_valid_html(body)
    assert body.count('class="contact-row"') == min(len(contacts), 5)
    assert body.count('class="link-chip"') == min(len(links), 6)
    assert body.count('class="numbered-item"') == min(len(numbered), 8)
    assert body.count('class="figure-slot"') == min(len(figures), 10)


# ===========================================================================
# Feature: pptx-design-density-parity, Property 5: 텍스트 길이 클램프 + 말줄임
# For any length-limited text input, the displayed text never exceeds the
# defined maximum and over-limit input gains an ellipsis ("…").
# Validates: Requirements 1.2, 1.5, 2.1, 2.3, 2.6, 2.7
# ===========================================================================
@settings(max_examples=120, deadline=None,
          suppress_health_check=[HealthCheck.too_slow])
@given(
    t_chip=_txt(50),
    t_footer=_txt(95),
    t_section=_txt(50),
    t_note=_txt(330),
    t_tab=_txt(28),
    t_ftitle=_txt(50),
    t_clabel=_txt(40),
    t_llabel=_txt(40),
)
def test_property_5_text_clamp_ellipsis(t_chip, t_footer, t_section, t_note,
                                        t_tab, t_ftitle, t_clabel, t_llabel):
    cover = m.render_cover_slide(title="표지", notice_chip=t_chip, footer=t_footer)
    _assert_valid_html(cover)
    _check_clamp(t_chip, _inner(cover, "notice-chip"), 40)
    _check_clamp(t_footer, _inner(cover, "footer"), 80)

    body = m.render_two_column(
        title="본문", left_content="x", right_content="y",
        left_section_no="1", left_section_title=t_section,
        left_note=t_note, notice_tab=t_tab, footer_title=t_ftitle,
        left_contact={"items": [{"label": t_clabel, "value": "v"}]},
        left_links=[{"label": t_llabel}],
    )
    _assert_valid_html(body)
    _check_clamp(t_section, _inner(body, "section-title"), 40)
    _check_clamp(t_note, _inner(body, "note-callout"), 300)
    _check_clamp(t_tab, _inner(body, "notice-tab"), 20)
    _check_clamp(t_ftitle, _inner(body, "slide-footer-title"), 40)
    _check_clamp(t_clabel, _inner(body, "contact-label"), 30)
    _check_clamp(t_llabel, _inner(body, "link-chip-label"), 30)


# ===========================================================================
# Feature: pptx-design-density-parity, Property 6: Numbered_List_Item 순차 번호
# For any 1..8 numbered list, the rendered number badges are sequential 1..n.
# Validates: Requirements 2.5
# ===========================================================================
@settings(max_examples=120, deadline=None,
          suppress_health_check=[HealthCheck.too_slow])
@given(
    items=st.lists(_word, min_size=1, max_size=12),
    side=st.sampled_from(["left", "right"]),
)
def test_property_6_numbered_sequential(items, side):
    key = "left_numbered" if side == "left" else "right_numbered"
    html = m.render_two_column(
        title="본문", left_content="x", right_content="y",
        **{key: list(items)})
    _assert_valid_html(html)
    badges = [int(x) for x in
              re.findall(r'class="numbered-badge"[^>]*>(\d+)<', html)]
    n = min(len(items), 8)
    assert badges == list(range(1, n + 1))


# ===========================================================================
# Feature: pptx-design-density-parity, Property 7: 부분 강조 헤드라인의 존재 조건
# For any title + accent_spans, only substrings that actually occur in the
# title are wrapped in an `accent-span`; non-occurring spans stay plain text
# and the un-wrapped text reconstructs the original title exactly.
# Validates: Requirements 1.3, 1.8
# ===========================================================================
_SP_ALPHA = "abAB가나"  # tiny alphabet → frequent real substring collisions


@settings(max_examples=150, deadline=None,
          suppress_health_check=[HealthCheck.too_slow])
@given(
    title=st.text(alphabet=_SP_ALPHA, min_size=1, max_size=12),
    spans=st.lists(st.text(alphabet=_SP_ALPHA, min_size=1, max_size=4),
                   min_size=0, max_size=6),
)
def test_property_7_accent_headline_occurrence(title, spans):
    html = m.render_cover_slide(title=title, accent_spans=list(spans))
    _assert_valid_html(html)
    h1 = re.search(r"<h1>(.*?)</h1>", html, re.S).group(1)

    occurs = any(s in title for s in spans)
    assert ('class="accent-span"' in h1) == occurs

    # Stripping the accent-span wrappers must reconstruct the plain title
    # (html.escape is identity for this alphabet) — proves non-occurring spans
    # never alter the text and occurring ones only wrap.
    recon = re.sub(r'<span class="accent-span"[^>]*>', "", h1).replace(
        "</span>", "")
    assert recon == title

    # Every wrapped span text is a real substring of the title.
    for inner in re.findall(r'<span class="accent-span"[^>]*>([^<]*)</span>',
                            h1):
        assert inner in title


# ===========================================================================
# Feature: pptx-design-density-parity, Property 8: 단일 인스턴스 요소 개수 불변
# For any cover with icon_badge + notice_chip + accent bar all active, each of
# the `cover-icon-badge`, `notice-chip`, `accent-bar` markers appears exactly
# once.
# Validates: Requirements 1.6, 1.9
# ===========================================================================
@settings(max_examples=120, deadline=None,
          suppress_health_check=[HealthCheck.too_slow])
@given(
    title=st.text(alphabet=_ALNUM, min_size=1, max_size=20),
    icon=st.sampled_from(["zap", "shield", "link", "check", "arrow_right"]),
    chip=_txt(20),
)
def test_property_8_single_instance(title, icon, chip):
    html = m.render_cover_slide(title=title, icon_badge=icon, notice_chip=chip)
    _assert_valid_html(html)
    assert html.count('class="cover-icon-badge"') == 1
    assert html.count('class="notice-chip"') == 1
    assert html.count('class="accent-bar"') == 1


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(pytest.main([__file__, "-q", "-p", "no:cacheprovider"]))
