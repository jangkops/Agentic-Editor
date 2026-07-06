"""Token-consistency, security, and edge-case tests for the density upgrades in
``ai_engine/slide_templates.py`` (spec: pptx-design-density-parity, task group 6).

This module covers the *style-token*, *security/hermetic*, and *IF-THEN edge*
guarantees of the additive density elements layered onto ``render_cover_slide`` /
``render_two_column``:

  Property 15 (Req 7.1, 7.3) — valid per-call design tokens (passed via the
      ``design=`` dict) flow into the density markup so the rendered colors /
      fonts match the passed tokens, and the SLIDE_DESIGN default primary color
      does NOT leak into the density markup once it is overridden.
  Property 16 (Req 7.2, 7.4) — ``design`` None / {} → SLIDE_DESIGN defaults, and
      a partially-invalid token dict only falls back for the bad token while the
      other (valid) tokens keep their passed values. Exercised through
      ``_tok_color`` / ``_tok_font`` AND a real render.
  Property 11 (Req 6.2) — output references no ``http://`` / ``https://`` URL
      except the SVG ``xmlns="http://www.w3.org/2000/svg"`` namespace (which is
      excluded); inline ``data:`` images are allowed.
  Property 12 (Req 2.9, 6.7) — output with link chips carries ZERO decorative
      unicode emoji; icons are inline ``<svg>`` only.
  Property 13 (Req 6.3) — output contains the applied token's CJK-aware
      ``font_heading`` / ``font_body`` stack.

Plus example/edge unit tests (task 6.6, NOT property-based):
  - unresolved ``icon_badge`` → no badge markup (Req 1.10)
  - non-occurring ``accent_spans`` → plain title, no accent marker (Req 1.8)
  - ``step_cards=[]`` / None → no grid markup (Req 1.7)
  - empty / invalid figure path → image omitted, caption + slot still render (Req 3.6)
  - unsupported key in ``render_layout`` → "" fallback, no exception (Req 4.7)
  - all density kwargs optional with no-op defaults (Req 4.3, via inspect.signature)

Everything is hermetic — pure Python, NO network, NO Electron, NO gateway.

Run (hermetic):
  ./venv/bin/python -m pytest scripts/test_slide_density_safety_pbt.py -p no:cacheprovider -q
"""
from __future__ import annotations

import inspect
import os
import re
import sys

# Make ai_engine importable from repo root (mirrors test_slide_templates_density).
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "ai_engine"))

import pytest  # noqa: E402
from hypothesis import given, settings, strategies as st, HealthCheck, assume  # noqa: E402

import ai_engine.slide_templates as m  # noqa: E402
from ai_engine.slide_templates import (  # noqa: E402
    SLIDE_DESIGN,
    _tok_color,
    _tok_font,
    _figure_slots,
)

# The single allowed external-looking string: the SVG namespace declaration.
SVG_NS = 'http://www.w3.org/2000/svg'

# Decorative unicode emoji / pictograph / symbol ranges. The density renderer
# must use SVG icons only, so NONE of these codepoints may appear in output.
_EMOJI_RE = re.compile(
    "["
    "\U0001F300-\U0001FAFF"   # symbols & pictographs, emoticons, transport, …
    "\U0001F000-\U0001F0FF"   # mahjong / dominoes / playing cards
    "\U00002600-\U000027BF"   # misc symbols + dingbats
    "\U00002B00-\U00002BFF"   # misc symbols & arrows
    "\U00002190-\U000021FF"   # arrows (← ↑ → ↓ …) — icons are SVG, not unicode
    "\U0000FE00-\U0000FE0F"   # variation selectors (emoji presentation)
    "\U0001F1E6-\U0001F1FF"   # regional indicators
    "\U00002700-\U000027BF"   # dingbats
    "]"
)

# Safe text alphabet — ascii letters, digits, spaces and a few Hangul glyphs.
# Deliberately excludes ':' '/' so generated content can never *itself* form a
# "http://"/"https://" substring (avoids false positives in Property 11) and
# excludes any emoji codepoint (avoids false positives in Property 12).
_SAFE = st.text(alphabet="abcdeFGHIJ가나다라마 0123", min_size=1, max_size=24)
_SAFE0 = st.text(alphabet="abcdeFGHIJ가나다라마 0123", min_size=0, max_size=24)

# A valid #RRGGBB color (uppercase hex so it survives _tok_color verbatim).
_HEXCOLOR = st.integers(min_value=0, max_value=0xFFFFFF).map(lambda n: "#%06X" % n)
# A valid short font token (1–64 chars, non-empty after strip, no markup-breaking chars).
_FONT = st.text(alphabet="abcdefgABCDEFG ", min_size=1, max_size=40).map(lambda s: s.strip() or "Xfont")

DEFAULT_PRIMARY = SLIDE_DESIGN["primary"]   # "#0066FF"


def _design_with(primary=None, accent=None, text_dark=None,
                 font_heading=None, font_body=None):
    """Build a SLIDE_DESIGN-shaped dict overriding only the given tokens."""
    d = dict(SLIDE_DESIGN)
    if primary is not None:
        d["primary"] = primary
    if accent is not None:
        d["accent"] = accent
    if text_dark is not None:
        d["text_dark"] = text_dark
    if font_heading is not None:
        d["font_heading"] = font_heading
    if font_body is not None:
        d["font_body"] = font_body
    return d


def _render_dense_cover(design=None):
    """Cover render with every cover density element active."""
    return m.render_cover_slide(
        title="신규 입사자 노트북 세팅",
        subtitle="온보딩 매뉴얼",
        eyebrow="가이드",
        footer="2026 IT 운영팀",
        design=design,
        icon_badge="shield",
        notice_chip="필수 공지",
        accent_spans=["노트북"],
        step_cards=[
            {"label": "01", "description": "수령"},
            {"label": "02", "description": "초기화"},
        ],
    )


def _render_dense_body(design=None):
    """Two-column render with every body density element active."""
    return m.render_two_column(
        title="세팅 절차",
        left_content="- 계정 발급\n- VPN 설치",
        right_content="- 보안 점검\n- 백업 설정",
        subtitle="좌우 대칭",
        design=design,
        left_section_no="01", left_section_title="계정 준비",
        right_section_no="02", right_section_title="보안 적용",
        left_contact={"items": [{"label": "헬프데스크", "value": "내선 100"}]},
        right_contact={"items": [{"label": "보안팀", "value": "내선 200"}]},
        left_note="작업 전 백업 필수",
        right_note="완료 후 점검표 제출",
        left_links=[{"label": "위키 가이드"}],
        right_links=[{"label": "정책 문서"}],
        left_numbered=["계정 발급", "권한 신청"],
        right_numbered=["방화벽", "암호화"],
        notice_tab="기밀",
        footer_title="온보딩 매뉴얼", footer_page="2/4",
    )


# ===========================================================================
# Property 15: 밀도 요소 색·폰트의 디자인 토큰 일치 (Req 7.1, 7.3)
# ===========================================================================
# Feature: pptx-design-density-parity, Property 15: For any valid per-call
# design tokens (valid #RRGGBB colors and fonts), the colors/fonts of the
# active density elements match the passed tokens, and no SLIDE_DESIGN default
# primary color leaks into the density markup once it is overridden.
@settings(max_examples=120, deadline=None,
          suppress_health_check=[HealthCheck.too_slow])
@given(
    primary=_HEXCOLOR,
    accent=_HEXCOLOR,
    text_dark=_HEXCOLOR,
    font_heading=_FONT,
    font_body=_FONT,
)
def test_pbt_property15_density_color_font_match_tokens(
        primary, accent, text_dark, font_heading, font_body):
    # Keep generated colors distinct from the default primary so the leak check
    # is meaningful (any legit appearance of #0066FF would be one of these).
    assume(primary.upper() != DEFAULT_PRIMARY.upper())
    assume(accent.upper() != DEFAULT_PRIMARY.upper())
    assume(text_dark.upper() != DEFAULT_PRIMARY.upper())

    d = _design_with(primary, accent, text_dark, font_heading, font_body)
    cover = _render_dense_cover(d)
    body = _render_dense_body(d)

    for html in (cover, body):
        # Passed color tokens flow into the density markup.
        assert primary in html, "passed primary token must appear in density markup"
        assert accent in html, "passed accent token must appear in density markup"
        # Passed font tokens flow into the markup (heading + body stacks).
        assert font_heading in html
        assert font_body in html
        # The overridden default primary must NOT leak anywhere.
        assert DEFAULT_PRIMARY not in html, (
            "SLIDE_DESIGN default primary leaked despite per-call override")


# ===========================================================================
# Property 16: per-call 토큰 폴백 (토큰별 부분 폴백) (Req 7.2, 7.4)
# ===========================================================================
# Feature: pptx-design-density-parity, Property 16: For any token profile, a
# None/empty design yields SLIDE_DESIGN defaults, and when only some tokens are
# invalid (bad #RRGGBB / bad font) ONLY those tokens fall back to SLIDE_DESIGN
# while the remaining valid tokens keep their passed values.
@settings(max_examples=120, deadline=None,
          suppress_health_check=[HealthCheck.too_slow])
@given(
    good_primary=_HEXCOLOR,
    good_font=_FONT,
    bad_color=st.sampled_from(["", "  ", "#fff", "0066FF", "#GGGGGG", "blue",
                               "rgb(0,0,0)", "#12345", "#1234567", "nothex"]),
    bad_font=st.sampled_from(["", "   ", "x" * 65, "y" * 120]),
)
def test_pbt_property16_per_token_partial_fallback(
        good_primary, good_font, bad_color, bad_font):
    # --- None / empty dict → SLIDE_DESIGN defaults (token resolver level) ---
    for empty in (None, {}):
        assert _tok_color(empty, "primary") == SLIDE_DESIGN["primary"]
        assert _tok_color(empty, "accent") == SLIDE_DESIGN["accent"]
        assert _tok_font(empty, "font_heading") == SLIDE_DESIGN["font_heading"]
        assert _tok_font(empty, "font_body") == SLIDE_DESIGN["font_body"]

    # --- partial fallback: valid primary kept, invalid accent reverts ---
    d = dict(SLIDE_DESIGN)
    d["primary"] = good_primary
    d["accent"] = bad_color
    assert _tok_color(d, "primary") == good_primary, "valid token must be kept"
    assert _tok_color(d, "accent") == SLIDE_DESIGN["accent"], (
        "invalid color token must fall back to SLIDE_DESIGN default")

    # --- partial fallback for fonts: valid heading kept, invalid body reverts ---
    d2 = dict(SLIDE_DESIGN)
    d2["font_heading"] = good_font
    d2["font_body"] = bad_font
    assert _tok_font(d2, "font_heading") == good_font, "valid font must be kept"
    assert _tok_font(d2, "font_body") == SLIDE_DESIGN["font_body"], (
        "invalid font token must fall back to SLIDE_DESIGN default")

    # --- a real render confirms the partial fallback reaches the markup ---
    assume(good_primary.upper() != DEFAULT_PRIMARY.upper())
    render_d = dict(SLIDE_DESIGN)
    render_d["primary"] = good_primary   # valid → used by icon badge
    render_d["accent"] = bad_color       # invalid → falls back to default accent
    html = m.render_cover_slide(
        title="제목", design=render_d,
        icon_badge="shield",             # uses primary
        notice_chip="공지",              # uses accent
    )
    assert good_primary in html, "valid primary token must reach the markup"
    assert SLIDE_DESIGN["accent"] in html, (
        "invalid accent must fall back to the default accent in the markup")


def test_property16_none_and_empty_render_use_defaults():
    """design=None and design={} both resolve to SLIDE_DESIGN (example check)."""
    base_none = m.render_cover_slide(title="제목", subtitle="부제")
    base_empty = m.render_cover_slide(title="제목", subtitle="부제", design={})
    assert base_none == base_empty, "None and {} design must be byte-identical"
    # Default tokens are present in the rendered output.
    assert SLIDE_DESIGN["primary"] in base_none
    assert SLIDE_DESIGN["font_heading"] in base_none
    assert SLIDE_DESIGN["font_body"] in base_none


# ===========================================================================
# Property 11: 외부 URL 미참조 자기완결 HTML (Req 6.2)
# ===========================================================================
# Feature: pptx-design-density-parity, Property 11: For any render input, the
# output references no http:// or https:// URL except the SVG xmlns namespace
# declaration (excluded); inline data: images are allowed.
@settings(max_examples=120, deadline=None,
          suppress_health_check=[HealthCheck.too_slow])
@given(
    title=_SAFE,
    notice=_SAFE0,
    note=_SAFE0,
    link_label=_SAFE,
    caption=_SAFE,
)
def test_pbt_property11_no_external_url(title, notice, note, link_label, caption):
    # data: URI image is allowed and must NOT be flagged as an external URL.
    data_uri = "data:image/png;base64,iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNk+M8AAAMCAQDJ/Ck0AAAAAElFTkSuQmCC"
    cover = m.render_cover_slide(
        title=title, notice_chip=notice, icon_badge="shield",
        accent_spans=[title[:3]] if title else None,
        step_cards=[{"label": "01", "description": note}],
    )
    body = m.render_two_column(
        title=title, left_content="- a\n- b", right_content="- c",
        left_links=[{"label": link_label}],
        left_note=note,
        left_figures=[{"image": data_uri, "caption": caption}],
        notice_tab=notice, footer_title=title, footer_page="1/2",
    )
    for html in (cover, body):
        # Strip the single allowed namespace, then assert no URL scheme remains.
        stripped = html.replace(SVG_NS, "")
        assert "http://" not in stripped, "no http:// URL beyond the SVG namespace"
        assert "https://" not in stripped, "no https:// URL allowed"
    # data: URI image survived (inline embed) — proves data: is allowed.
    assert "data:image/png;base64," in body


# ===========================================================================
# Property 12: 데코 이모지 없음 (SVG 아이콘만) (Req 2.9, 6.7)
# ===========================================================================
# Feature: pptx-design-density-parity, Property 12: For any render input that
# includes link chips, the output contains ZERO decorative unicode emoji and
# icons are represented as inline <svg> only.
@settings(max_examples=120, deadline=None,
          suppress_health_check=[HealthCheck.too_slow])
@given(
    left_label=_SAFE,
    right_label=_SAFE,
    title=_SAFE,
)
def test_pbt_property12_no_decorative_emoji(left_label, right_label, title):
    html = m.render_two_column(
        title=title, left_content="- a", right_content="- b",
        left_links=[{"label": left_label}, {"label": right_label}],
        right_links=[{"label": right_label}],
    )
    found = _EMOJI_RE.findall(html)
    assert found == [], f"decorative unicode emoji must be absent, found: {found!r}"
    # A link chip renders only for a non-whitespace label (the helper drops
    # whitespace-only labels by design). Require at least one usable label so
    # the chip-presence assertions below match the helper's correct behavior.
    assume(bool(left_label.strip() or right_label.strip()))
    # Link chips are active → icons must be present, and only as inline <svg>.
    assert 'class="link-chip"' in html
    assert "<svg" in html, "link-chip icons must be inline SVG"
    assert SVG_NS in html, "inline SVG carries the xmlns namespace"


# ===========================================================================
# Property 13: CJK 인지 폰트 스택 적용 (Req 6.3)
# ===========================================================================
# Feature: pptx-design-density-parity, Property 13: For any render input, the
# output HTML contains the applied design token's CJK-aware font_heading /
# font_body stacks.
@settings(max_examples=120, deadline=None,
          suppress_health_check=[HealthCheck.too_slow])
@given(
    title=_SAFE,
    subtitle=_SAFE0,
    link_label=_SAFE,
)
def test_pbt_property13_cjk_font_stack_applied(title, subtitle, link_label):
    cover = m.render_cover_slide(title=title, subtitle=subtitle,
                                 notice_chip="공지", icon_badge="shield")
    body = m.render_two_column(title=title, left_content="- a", right_content="- b",
                               left_links=[{"label": link_label}],
                               left_numbered=["하나", "둘"])
    for html in (cover, body):
        assert SLIDE_DESIGN["font_heading"] in html, "CJK-aware heading stack applied"
        assert SLIDE_DESIGN["font_body"] in html, "CJK-aware body stack applied"
        # The shared CJK font families are present in the stack.
        assert "Apple SD Gothic Neo" in html and "Noto Sans KR" in html


# ===========================================================================
# Task 6.6: IF-THEN / edge example unit tests (NOT property-based)
# ===========================================================================
def test_edge_icon_badge_unresolved_no_badge_markup():
    """Req 1.10: icon_badge provided but unresolvable → no badge markup."""
    for bad in ("totally-unknown-icon", {"icon": "???not-real"}, {"icon": ""},
                {"icon": None}, 12345, {}):
        html = m.render_cover_slide(title="제목", icon_badge=bad)
        assert 'class="cover-icon-badge"' not in html, (
            f"unresolved icon_badge {bad!r} must not produce a badge")
    # Sanity: a resolvable name DOES produce the badge.
    ok = m.render_cover_slide(title="제목", icon_badge="shield")
    assert 'class="cover-icon-badge"' in ok


def test_edge_accent_spans_non_occurring_plain_title():
    """Req 1.8: accent_spans not in title → plain title, no accent-span marker."""
    html = m.render_cover_slide(title="hello world", accent_spans=["zzz", "없는문자열"])
    assert 'class="accent-span"' not in html, "non-occurring spans must not highlight"
    assert "hello world" in html, "title still rendered as plain text"
    # Sanity: an occurring span IS highlighted.
    ok = m.render_cover_slide(title="hello world", accent_spans=["world"])
    assert 'class="accent-span"' in ok and "world" in ok


def test_edge_step_cards_empty_or_none_no_grid():
    """Req 1.7: step_cards=[] or None → no grid markup."""
    for empty in ([], None):
        html = m.render_cover_slide(title="제목", step_cards=empty)
        assert 'class="step-card-grid"' not in html
        assert 'class="step-card"' not in html
    # Sanity: a non-empty list DOES produce the grid.
    ok = m.render_cover_slide(title="제목",
                              step_cards=[{"label": "01", "description": "d"}])
    assert 'class="step-card-grid"' in ok and 'class="step-card"' in ok


def test_edge_figure_empty_or_invalid_path_omits_image_keeps_caption():
    """Req 3.6: empty / invalid / external figure path → image omitted, caption
    and slot still render; unreadable/empty slots are dropped."""
    # Non-existent local path → image omitted, caption + slot still render.
    frag = _figure_slots([{"image": "/no/such/file.png", "caption": "캡션 유지"}],
                         SLIDE_DESIGN)
    assert 'class="figure-slot"' in frag, "slot still renders without image"
    assert "캡션 유지" in frag and 'class="figure-caption"' in frag
    assert "figure-img" not in frag, "missing image must be omitted"

    # External reference → image omitted, caption still renders.
    ext = _figure_slots([{"image": "http://example.com/a.png", "caption": "외부"}],
                        SLIDE_DESIGN)
    assert 'class="figure-slot"' in ext and "외부" in ext
    assert "figure-img" not in ext

    # Neither usable image nor caption → no-op "" (byte preservation).
    assert _figure_slots([{"image": "", "caption": ""}], SLIDE_DESIGN) == ""
    assert _figure_slots(None, SLIDE_DESIGN) == ""
    assert _figure_slots([], SLIDE_DESIGN) == ""

    # End-to-end through render_two_column: invalid figure path, slide still OK.
    html = m.render_two_column(title="t", left_content="- a", right_content="- b",
                               left_figures=[{"image": "/missing.png",
                                              "caption": "그림 설명"}])
    assert 'class="figure-slot"' in html and "그림 설명" in html
    assert "</html>" in html


def test_edge_render_layout_unsupported_key_returns_empty_no_exception():
    """Req 4.7: unsupported key in data → render_layout falls back to "" without
    raising (dispatcher catches TypeError)."""
    out = m.render_layout("cover", {"title": "제목", "bogus_unknown_key": 123})
    assert out == "", "unsupported kwarg must yield empty-string fallback"
    out2 = m.render_layout("two_column", {
        "title": "t", "left_content": "- a", "right_content": "- b",
        "not_a_real_field": True,
    })
    assert out2 == ""
    # Sanity: a valid call (incl. a new density field) still renders.
    ok = m.render_layout("cover", {"title": "제목", "notice_chip": "공지"})
    assert ok and 'class="notice-chip"' in ok


def test_edge_all_density_kwargs_optional_with_noop_defaults():
    """Req 4.3: every new density kwarg is optional with a no-op default
    (verified via inspect.signature)."""
    cover_density = ["icon_badge", "notice_chip", "accent_spans", "step_cards"]
    body_density = [
        "left_section_no", "left_section_title", "right_section_no",
        "right_section_title", "left_contact", "right_contact", "left_note",
        "right_note", "left_links", "right_links", "left_numbered",
        "right_numbered", "left_figures", "right_figures", "notice_tab",
        "footer_title", "footer_page",
    ]
    cover_sig = inspect.signature(m.render_cover_slide)
    for name in cover_density:
        assert name in cover_sig.parameters, f"cover missing density kwarg {name}"
        p = cover_sig.parameters[name]
        assert p.default is not inspect.Parameter.empty, (
            f"cover density kwarg {name} must have a default")
        # no-op default: None or empty string
        assert p.default in (None, ""), f"{name} default must be no-op, got {p.default!r}"

    body_sig = inspect.signature(m.render_two_column)
    for name in body_density:
        assert name in body_sig.parameters, f"two_column missing density kwarg {name}"
        p = body_sig.parameters[name]
        assert p.default is not inspect.Parameter.empty, (
            f"two_column density kwarg {name} must have a default")
        assert p.default in (None, ""), f"{name} default must be no-op, got {p.default!r}"


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(pytest.main([__file__, "-q", "-p", "no:cacheprovider"]))
