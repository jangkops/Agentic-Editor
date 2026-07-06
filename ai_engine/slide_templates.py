"""Slide HTML templates — Genspark/Gamma-class layouts rendered as 1920×1080 PNG.

Each `render_*` function returns a complete, self-contained HTML document
(no external CSS, no fonts from CDN, no JS) that can be loaded into a hidden
Electron BrowserWindow and captured to PNG. The captured PNG becomes the
*full-bleed background* of a PPTX slide (or a max-12cm image in PDF).

Design principles (see .kiro/steering/ui.md for VS Code dark tokens — those
apply to the *editor*; slide output uses a separate light/dark hybrid system
because users expect printable presentations, not editor surfaces):

  - 16:9 (1920×1080) full-bleed
  - System CJK-aware font stack — Apple SD Gothic Neo / Malgun / Noto
  - At most 3-4 colors per slide; lots of whitespace
  - Typography scale: 64-72 / 32-40 / 24-28 / 16-18 px
  - SVG outline icons only (lucide-style) — NO emoji decoration
    (project policy: "기능 아이콘만, 데코 이모지 금지")
  - Cards: 16px radius, subtle shadow, no hover (we capture statically)

Any function added here MUST:
  1. accept primitive args (str, list[str], list[dict]) — JSON serializable
  2. return one complete HTML document (head + body)
  3. NEVER reference http:// or https:// URLs (security pre-flight in
     ipc-slides-handler.js will reject the render otherwise)
"""

from __future__ import annotations

import base64
import os
import re
from html import escape as _esc
from typing import List, Dict, Any, Optional


# ---------------------------------------------------------------------------
# Optional image slot (HTML + Vertex hybrid compositing) — security-aware.
#
# cover / two_column / objective_detail accept an optional image field
# (heroImage / image). When provided, the value is composited into the layout
# via background-image; when absent, the layout falls back to its existing
# gradient/placeholder and the produced HTML is BYTE-IDENTICAL to before.
#
# two_column / objective_detail ALSO accept an optional BOUNDED `slot_image`
# field (the Image_Slot). Unlike the full-bleed `image` backdrop, `slot_image`
# is composited into a capped-height card inside the RIGHT column (an "image
# column" region) sized SMALLER than the full slide, so it NEVER becomes a
# full-bleed PICTURE covering 13.333in x 7.5in. When absent/invalid the output
# is byte-identical to before and callers fall back to the gradient/placeholder.
#
# Only LOCAL file paths or pre-built data: URIs are accepted. http(s):// (and
# protocol-relative // / file://) references are REJECTED per the module's
# no-external-URL rule (the render bridge loads HTML through a data: URL and
# blocks file://, so a local path must be inlined as a data URI to be visible
# in the captured PNG). Any empty/invalid/unreadable input resolves to "" so
# callers fall back to the gradient/placeholder (preserving byte-compatibility).
# ---------------------------------------------------------------------------
_IMAGE_EXT_MIME: Dict[str, str] = {
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".gif": "image/gif",
    ".webp": "image/webp",
    ".bmp": "image/bmp",
    ".svg": "image/svg+xml",
}


def _safe_image_data_uri(src: Any) -> str:
    """Resolve an image reference to an inline data URI safe for slide HTML.

    Accepts ONLY a local filesystem path or a pre-built ``data:image/...`` URI.
    External references (``http(s)://``, protocol-relative ``//``, ``file://``)
    are rejected and return "". Local paths are read and inlined as a base64
    data URI so the image survives the bridge's ``data:`` URL render (which
    cannot load ``file://``). Returns "" for any empty/invalid/unreadable input
    so the caller can fall back to its existing gradient/placeholder.
    """
    if not isinstance(src, str):
        return ""
    s = src.strip()
    if not s:
        return ""
    low = s.lower()
    # Already a self-contained data URI — accept as-is (never an external URL).
    if low.startswith("data:image/"):
        return s
    # Reject any external / non-local protocol.
    if low.startswith(("http://", "https://", "//", "file://", "data:")):
        return ""
    # Treat as a local filesystem path; inline its bytes as a data URI.
    try:
        if not os.path.isfile(s):
            return ""
        ext = os.path.splitext(s)[1].lower()
        mime = _IMAGE_EXT_MIME.get(ext)
        if not mime:
            return ""
        with open(s, "rb") as fh:
            raw = fh.read()
        if not raw:
            return ""
        b64 = base64.b64encode(raw).decode("ascii")
        return f"data:{mime};base64,{b64}"
    except Exception:
        return ""


# ---------------------------------------------------------------------------
# Design tokens — single source of truth for slide aesthetics. Tweak here to
# rebrand all 7 layouts at once. Mirrored on the design side of the project
# (the *editor* uses .kiro/steering/ui.md tokens — different surface).
# ---------------------------------------------------------------------------
SLIDE_DESIGN: Dict[str, str] = {
    "primary": "#0066FF",      # main accent — links, headlines, primary CTAs
    "secondary": "#00C896",    # supporting accent — successful states, callouts
    "accent": "#FF6B35",       # high-emphasis highlights — warnings, novelty
    "bg_gradient": "linear-gradient(135deg, #0F1419 0%, #1A2332 100%)",
    "bg_light": "#FAFAFA",
    "bg_section": "#F4F6F9",
    "text_dark": "#1A1A1A",
    "text_light": "#FFFFFF",
    "text_muted": "#6B7280",
    "border": "#E5E7EB",
    "card_bg": "#FFFFFF",
    "card_shadow": "0 10px 40px rgba(0,0,0,0.08)",
    "card_shadow_hover": "0 20px 60px rgba(0,0,0,0.12)",
    "font_heading": (
        "-apple-system, BlinkMacSystemFont, 'Apple SD Gothic Neo', "
        "'Malgun Gothic', 'Noto Sans KR', 'Segoe UI', sans-serif"
    ),
    "font_body": (
        "-apple-system, BlinkMacSystemFont, 'Apple SD Gothic Neo', "
        "'Malgun Gothic', 'Noto Sans KR', 'Segoe UI', sans-serif"
    ),
}

# ---------------------------------------------------------------------------
# Style_Profile → SLIDE_DESIGN per-token override (요구사항 7.1, 7.5, 7.6)
# 설계 §구성요소 5. A registered template's Style_Profile carries 7 tokens
# (primaryColor / secondaryColor / accentColor / textColor / backgroundColor /
# headingFont / bodyFont). This helper produces a fresh design-token dict where
# each *valid* profile token overrides the matching SLIDE_DESIGN default, while
# any absent/invalid token keeps its SLIDE_DESIGN default (per-token fallback).
# ---------------------------------------------------------------------------

# Style_Profile key → SLIDE_DESIGN key. Only these tokens are overridable; any
# other SLIDE_DESIGN key (gradients, shadows, muted text, …) is left untouched.
_PROFILE_COLOR_MAP: Dict[str, str] = {
    "primaryColor": "primary",
    "textColor": "text_dark",
    "backgroundColor": "bg_light",
    "accentColor": "accent",
    "secondaryColor": "secondary",
}
_PROFILE_FONT_MAP: Dict[str, str] = {
    "headingFont": "font_heading",
    "bodyFont": "font_body",
}


def design_tokens_for_profile(profile: Optional[dict]) -> Dict[str, str]:
    """Build a SLIDE_DESIGN-shaped token dict overridden by a Style_Profile.

    - ``profile is None`` → a copy of ``SLIDE_DESIGN`` unchanged (요구사항 7.5,
      baseline). A copy (not the original) is returned so callers can never
      mutate the module-level defaults.
    - Otherwise → a shallow copy of ``SLIDE_DESIGN`` with each *valid* profile
      token applied via the mapping below:

        primaryColor → primary, textColor → text_dark,
        backgroundColor → bg_light, accentColor → accent,
        secondaryColor → secondary, headingFont → font_heading,
        bodyFont → font_body.

      Color tokens are validated with the same ``#RRGGBB`` rule used by
      ``ai_engine.style_profile.normalize_color``; fonts must be a 1–64 char
      non-empty string. Any absent/invalid token keeps its SLIDE_DESIGN default
      (per-token fallback, 요구사항 7.6).

    This function never raises and always returns a NEW dict; ``SLIDE_DESIGN``
    is never mutated.

    Args:
        profile: A Style_Profile mapping, or None for baseline defaults.

    Returns:
        A new design-token dict suitable for the ``render_*`` helpers.
    """
    tokens: Dict[str, str] = dict(SLIDE_DESIGN)
    if not isinstance(profile, dict):
        # None (baseline) or any non-dict input → untouched defaults copy.
        return tokens

    # Lazy, dual-path import mirrors the project's run-from-root vs.
    # run-from-ai_engine convention and avoids an import cycle at module load.
    try:
        from ai_engine.style_profile import normalize_color
    except ImportError:  # pragma: no cover - exercised when run from ai_engine/
        try:
            from style_profile import normalize_color  # type: ignore
        except ImportError:
            normalize_color = None  # type: ignore

    # Color tokens: keep the SLIDE_DESIGN default unless the profile value
    # normalizes to a valid #RRGGBB string.
    for profile_key, design_key in _PROFILE_COLOR_MAP.items():
        raw = profile.get(profile_key)
        normalized = normalize_color(raw) if normalize_color is not None else None
        if normalized is not None:
            tokens[design_key] = normalized

    # Font tokens: keep the default unless a 1–64 char non-empty string.
    for profile_key, design_key in _PROFILE_FONT_MAP.items():
        raw = profile.get(profile_key)
        if isinstance(raw, str) and raw.strip() and 1 <= len(raw) <= 64:
            tokens[design_key] = raw

    return tokens


# A small, hand-picked palette of inline lucide-style SVG icons. Keep the
# viewBox at 24×24 and use currentColor so the icon adopts the parent text
# color — that way one icon works on dark and light backgrounds.
ICONS: Dict[str, str] = {
    "check": (
        '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="none" '
        'stroke="currentColor" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round">'
        '<polyline points="20 6 9 17 4 12"></polyline></svg>'
    ),
    "arrow_right": (
        '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="none" '
        'stroke="currentColor" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round">'
        '<line x1="5" y1="12" x2="19" y2="12"></line>'
        '<polyline points="12 5 19 12 12 19"></polyline></svg>'
    ),
    "layers": (
        '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="none" '
        'stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">'
        '<polygon points="12 2 2 7 12 12 22 7 12 2"></polygon>'
        '<polyline points="2 17 12 22 22 17"></polyline>'
        '<polyline points="2 12 12 17 22 12"></polyline></svg>'
    ),
    "zap": (
        '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="none" '
        'stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">'
        '<polygon points="13 2 3 14 12 14 11 22 21 10 12 10 13 2"></polygon></svg>'
    ),
    "shield": (
        '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="none" '
        'stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">'
        '<path d="M12 22s8-4 8-10V5l-8-3-8 3v7c0 6 8 10 8 10z"></path></svg>'
    ),
    "code": (
        '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="none" '
        'stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">'
        '<polyline points="16 18 22 12 16 6"></polyline>'
        '<polyline points="8 6 2 12 8 18"></polyline></svg>'
    ),
    "database": (
        '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="none" '
        'stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">'
        '<ellipse cx="12" cy="5" rx="9" ry="3"></ellipse>'
        '<path d="M21 12c0 1.66-4 3-9 3s-9-1.34-9-3"></path>'
        '<path d="M3 5v14c0 1.66 4 3 9 3s9-1.34 9-3V5"></path></svg>'
    ),
    "cloud": (
        '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="none" '
        'stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">'
        '<path d="M18 10h-1.26A8 8 0 1 0 9 20h9a5 5 0 0 0 0-10z"></path></svg>'
    ),
    "cpu": (
        '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="none" '
        'stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">'
        '<rect x="4" y="4" width="16" height="16" rx="2" ry="2"></rect>'
        '<rect x="9" y="9" width="6" height="6"></rect>'
        '<line x1="9" y1="2" x2="9" y2="4"></line>'
        '<line x1="15" y1="2" x2="15" y2="4"></line>'
        '<line x1="9" y1="20" x2="9" y2="22"></line>'
        '<line x1="15" y1="20" x2="15" y2="22"></line>'
        '<line x1="20" y1="9" x2="22" y2="9"></line>'
        '<line x1="20" y1="14" x2="22" y2="14"></line>'
        '<line x1="2" y1="9" x2="4" y2="9"></line>'
        '<line x1="2" y1="14" x2="4" y2="14"></line></svg>'
    ),
    "users": (
        '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="none" '
        'stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">'
        '<path d="M17 21v-2a4 4 0 0 0-4-4H5a4 4 0 0 0-4 4v2"></path>'
        '<circle cx="9" cy="7" r="4"></circle>'
        '<path d="M23 21v-2a4 4 0 0 0-3-3.87"></path>'
        '<path d="M16 3.13a4 4 0 0 1 0 7.75"></path></svg>'
    ),
    "link": (
        '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="none" '
        'stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">'
        '<path d="M10 13a5 5 0 0 0 7.54.54l3-3a5 5 0 0 0-7.07-7.07l-1.72 1.71"></path>'
        '<path d="M14 11a5 5 0 0 0-7.54-.54l-3 3a5 5 0 0 0 7.07 7.07l1.71-1.71"></path></svg>'
    ),
    "x": (
        '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="none" '
        'stroke="currentColor" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round">'
        '<line x1="18" y1="6" x2="6" y2="18"></line>'
        '<line x1="6" y1="6" x2="18" y2="18"></line></svg>'
    ),
    "circle": (
        '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="currentColor">'
        '<circle cx="12" cy="12" r="6"></circle></svg>'
    ),
}


def _icon(name: str, size: int = 24, color: Optional[str] = None) -> str:
    """Return an inline <span> wrapping the named SVG icon. Falls back to
    a plain bullet circle if the name is unknown — keeps templates from
    breaking on a typo."""
    svg = ICONS.get(name) or ICONS["circle"]
    style = f"display:inline-flex;align-items:center;justify-content:center;width:{size}px;height:{size}px;"
    if color:
        style += f"color:{color};"
    return f'<span style="{style}">{svg}</span>'


# ---------------------------------------------------------------------------
# Shared <head> — every layout uses the same baseline. Centralizing here
# means a single edit (e.g. font-feature-settings, line-height) applies to
# all 7 templates without copy-paste drift.
# ---------------------------------------------------------------------------
def _base_head(extra_css: str = "", design: Optional[dict] = None) -> str:
    # Per-call design tokens override SLIDE_DESIGN when a non-empty dict is
    # supplied (e.g. design_tokens_for_profile(style_profile)). When ``design``
    # is None/empty (existing callers), behavior is byte-identical to before.
    d = design if isinstance(design, dict) and design else SLIDE_DESIGN
    return f"""<!doctype html>
<html lang="ko">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=1920, initial-scale=1">
<title>Slide</title>
<style>
  * {{ box-sizing: border-box; margin: 0; padding: 0; }}
  html, body {{
    width: 1920px;
    height: 1080px;
    overflow: hidden;
    font-family: {d['font_body']};
    font-size: 18px;
    color: {d['text_dark']};
    background: {d['bg_light']};
    -webkit-font-smoothing: antialiased;
    -moz-osx-font-smoothing: grayscale;
    text-rendering: optimizeLegibility;
    font-feature-settings: "kern" 1, "liga" 1, "calt" 1;
  }}
  .slide {{
    position: relative;
    width: 1920px;
    height: 1080px;
    overflow: hidden;
  }}
  h1, h2, h3, h4 {{ font-family: {d['font_heading']}; font-weight: 700; letter-spacing: -0.02em; }}
  h1 {{ font-size: 72px; line-height: 1.1; }}
  h2 {{ font-size: 56px; line-height: 1.15; }}
  h3 {{ font-size: 36px; line-height: 1.2; }}
  h4 {{ font-size: 28px; line-height: 1.3; font-weight: 600; }}
  p  {{ font-size: 24px; line-height: 1.5; color: {d['text_dark']}; }}
  .muted {{ color: {d['text_muted']}; }}
  .accent-bar {{
    width: 80px;
    height: 6px;
    background: {d['primary']};
    border-radius: 3px;
    margin-bottom: 32px;
  }}
  /* SVG icon defaults — currentColor so they inherit text color */
  svg {{ display: block; }}
{extra_css}
</style>
</head>"""


# ---------------------------------------------------------------------------
# Density builder helpers (pptx-design-density-parity, 요구사항 1·7).
#
# These are PURE functions: given (input, design-token dict ``d``) they return
# an HTML fragment, or "" for any no-op/empty/invalid input. They NEVER raise
# and NEVER reference an external URL. Each active element carries a unique
# ``class="..."`` Density_Marker so absent elements add ZERO bytes (the helper
# returns "" → the conditional CSS block is skipped). All colors/fonts are
# resolved from ``d`` with per-token fallback to SLIDE_DESIGN (요구사항 7.4):
# a color token must match ``#RRGGBB`` and a font token must be a 1–64 char
# non-empty string, otherwise just that token reverts to its SLIDE_DESIGN
# default while the others keep their passed values.
# ---------------------------------------------------------------------------
_HEX6_RE = re.compile(r"^#[0-9A-Fa-f]{6}$")


def _tok_color(d: Any, key: str) -> str:
    """Resolve a color token from ``d`` with per-token #RRGGBB fallback (7.4)."""
    val = d.get(key) if isinstance(d, dict) else None
    if isinstance(val, str) and _HEX6_RE.match(val.strip()):
        return val.strip()
    return SLIDE_DESIGN.get(key, "#000000")


def _tok_font(d: Any, key: str) -> str:
    """Resolve a font token from ``d`` with per-token length fallback (7.4).

    A valid override is a 1–64 char non-empty string; the SLIDE_DESIGN default
    stacks are longer than 64 chars, so the default always falls through to
    ``SLIDE_DESIGN[key]`` (the same value) — byte-stable for existing callers.
    """
    val = d.get(key) if isinstance(d, dict) else None
    if isinstance(val, str) and val.strip() and 1 <= len(val) <= 64:
        return val
    return SLIDE_DESIGN[key]


def _clamp_text(text: Any, limit: int) -> str:
    """Clamp display text to ``limit`` chars, appending an ellipsis when cut.

    Does NOT strip — callers that need byte-stable behavior (e.g. cover footer)
    rely on values <= limit passing through unchanged.
    """
    s = str(text or "")
    if len(s) > limit:
        return s[:limit] + "…"
    return s


def _cover_icon_badge(icon_badge: Any, d: Any) -> str:
    """Circular tinted icon badge for the cover (요구사항 1.1, 1.10).

    Accepts ``{"icon": "name"}`` or a bare ``"name"`` string. The name MUST
    resolve to a known SVG in ICONS; otherwise no badge is produced (1.10).
    Marker: ``class="cover-icon-badge"``.
    """
    if isinstance(icon_badge, dict):
        name = icon_badge.get("icon")
    elif isinstance(icon_badge, str):
        name = icon_badge
    else:
        return ""
    if not isinstance(name, str):
        return ""
    name = name.strip()
    # Only a genuinely resolvable SVG yields a badge — an unknown name (which
    # _icon would silently render as a fallback bullet) produces nothing (1.10).
    if not name or name not in ICONS:
        return ""
    primary = _tok_color(d, "primary")
    svg = _icon(name, 56, color=primary)
    return (
        f'<div class="cover-icon-badge" '
        f'style="background:{primary}1a;color:{primary};">{svg}</div>'
    )


def _notice_chip(text: Any, d: Any) -> str:
    """Eyebrow pill chip above the title (요구사항 1.2). Clamp <=40 + ellipsis.

    Marker: ``class="notice-chip"``.
    """
    s = str(text or "").strip()
    if not s:
        return ""
    s = _clamp_text(s, 40)
    accent = _tok_color(d, "accent")
    font = _tok_font(d, "font_heading")
    return (
        f'<div class="notice-chip" '
        f'style="background:{accent}1f;color:{accent};font-family:{font};">'
        f'{_esc(s)}</div>'
    )


def _accent_headline(title: Any, accent_spans: Any, d: Any) -> str:
    """Title with only the substrings that actually occur wrapped in an
    accent span (요구사항 1.3, 1.8).

    The raw title is segmented on real occurrences of each span, then each
    segment is HTML-escaped individually so the markup stays safe. Spans that
    do NOT occur in the title are ignored (no marker). When ``accent_spans`` is
    None/empty OR none of the spans occur, the plain escaped title is returned
    (byte-identical to ``_esc(title)``). Marker: ``class="accent-span"``.
    """
    raw = str(title or "")
    if not isinstance(accent_spans, (list, tuple)) or not accent_spans:
        return _esc(raw)
    spans = [s for s in accent_spans if isinstance(s, str) and s and s in raw]
    if not spans:
        return _esc(raw)
    accent = _tok_color(d, "accent")
    out: List[str] = []
    i = 0
    n = len(raw)
    while i < n:
        best_pos: Optional[int] = None
        best_span: str = ""
        for s in spans:
            p = raw.find(s, i)
            if p == -1:
                continue
            if best_pos is None or p < best_pos or (p == best_pos and len(s) > len(best_span)):
                best_pos = p
                best_span = s
        if best_pos is None:
            out.append(_esc(raw[i:]))
            break
        if best_pos > i:
            out.append(_esc(raw[i:best_pos]))
        out.append(
            f'<span class="accent-span" style="color:{accent};">'
            f'{_esc(best_span)}</span>'
        )
        i = best_pos + len(best_span)
    return "".join(out)


def _step_card_grid(step_cards: Any, d: Any) -> str:
    """STEP card grid embedded on the cover (요구사항 1.4, 1.7).

    ``[{"label","description"}]`` clamped to 1–6 items laid out as a 2×2-ish
    grid. None/empty (or all-invalid) input yields "". Markers:
    ``class="step-card-grid"`` (container) and ``class="step-card"`` (items).
    """
    if not isinstance(step_cards, (list, tuple)) or not step_cards:
        return ""
    cards: List[tuple] = []
    for c in list(step_cards)[:6]:  # clamp to 6 (요구사항 1.4)
        if not isinstance(c, dict):
            continue
        label = str(c.get("label", "")).strip()
        desc = str(c.get("description", "")).strip()
        if not label and not desc:
            continue
        cards.append((label, desc))
    if not cards:
        return ""
    accent = _tok_color(d, "accent")
    primary = _tok_color(d, "primary")
    font = _tok_font(d, "font_heading")
    items: List[str] = []
    for label, desc in cards:
        items.append(
            f'<div class="step-card" '
            f'style="background:{primary}14;border-color:{primary}33;">'
            f'<div class="step-card-label" '
            f'style="color:{accent};font-family:{font};">{_esc(label)}</div>'
            f'<div class="step-card-desc">{_esc(desc)}</div>'
            f'</div>'
        )
    return f'<div class="step-card-grid">{"".join(items)}</div>'


# ---------------------------------------------------------------------------
# Body (two-column) density builders (pptx-design-density-parity, 요구사항 2·7).
#
# Same PURE-function contract as the cover builders above: each returns an HTML
# fragment carrying a unique ``class="..."`` Density_Marker, or "" for any
# no-op/empty/invalid input (so the conditional CSS block is skipped and the
# absent case stays byte-identical). They NEVER raise and NEVER reference an
# external URL. Colors/fonts come from ``d`` via _tok_color/_tok_font with
# per-token fallback to SLIDE_DESIGN (요구사항 7.4); icons are SVG-only via
# _icon — no decorative emoji (요구사항 2.9, 6.7).
# ---------------------------------------------------------------------------
def _section_header_bar(no: Any, title: Any, d: Any) -> str:
    """Dark header bar with a numbered badge + section title (요구사항 2.1).

    Active only when BOTH the section number and title are non-empty. The
    section title is clamped to <=40 chars (+ ellipsis). Colors/fonts come from
    ``d``. Marker: ``class="section-header-bar"``.
    """
    no_s = str(no or "").strip()
    title_s = str(title or "").strip()
    if not no_s or not title_s:
        return ""
    title_s = _clamp_text(title_s, 40)
    bar_bg = _tok_color(d, "text_dark")
    badge_bg = _tok_color(d, "primary")
    light = _tok_color(d, "text_light")
    font = _tok_font(d, "font_heading")
    return (
        f'<div class="section-header-bar" '
        f'style="background:{bar_bg};color:{light};font-family:{font};">'
        f'<span class="section-no" style="background:{badge_bg};color:{light};">'
        f'{_esc(no_s)}</span>'
        f'<span class="section-title">{_esc(title_s)}</span>'
        f'</div>'
    )


def _contact_box(contact: Any, d: Any) -> str:
    """Tinted contact box with a left accent border (요구사항 2.2).

    Accepts ``{"items":[{"label","value"}]}``. Each label is clamped to <=30
    chars and at most 5 items are rendered (excess clamped). Returns "" when no
    usable item is present. Marker: ``class="contact-box"``.
    """
    if not isinstance(contact, dict):
        return ""
    items = contact.get("items")
    if not isinstance(items, (list, tuple)) or not items:
        return ""
    rows: List[str] = []
    for it in list(items)[:5]:  # clamp to 5 items (요구사항 2.2)
        if not isinstance(it, dict):
            continue
        label = _clamp_text(str(it.get("label", "")).strip(), 30)
        value = str(it.get("value", "")).strip()
        if not label and not value:
            continue
        rows.append(
            f'<div class="contact-row">'
            f'<span class="contact-label">{_esc(label)}</span>'
            f'<span class="contact-value">{_esc(value)}</span>'
            f'</div>'
        )
    if not rows:
        return ""
    primary = _tok_color(d, "primary")
    text = _tok_color(d, "text_dark")
    font = _tok_font(d, "font_body")
    return (
        f'<div class="contact-box" '
        f'style="background:{primary}14;border-left-color:{primary};'
        f'color:{text};font-family:{font};">{"".join(rows)}</div>'
    )


def _note_callout(text: Any, d: Any) -> str:
    """Warning-accent tinted callout with a left border (요구사항 2.3).

    Multiline text is preserved as ``<br>`` and clamped to <=300 chars
    (+ ellipsis). The warning-emphasis ``accent`` token supplies the yellow-
    family tint/border so the color stays design-token sourced (요구사항 7.3).
    Marker: ``class="note-callout"``.
    """
    s = str(text or "").strip()
    if not s:
        return ""
    s = _clamp_text(s, 300)
    accent = _tok_color(d, "accent")
    text_c = _tok_color(d, "text_dark")
    font = _tok_font(d, "font_body")
    body = "<br>".join(_esc(line) for line in s.split("\n"))
    return (
        f'<div class="note-callout" '
        f'style="background:{accent}1f;border-left-color:{accent};'
        f'color:{text_c};font-family:{font};">{body}</div>'
    )


def _link_chips(links: Any, d: Any) -> str:
    """Link chips: SVG link icon + label + arrow glyph (요구사항 2.4, 2.9).

    Accepts ``[{"label"}]`` (bare strings tolerated). Clamped to 1–6 chips,
    each label <=30 chars. Icons are SVG-only (``_icon``); no decorative emoji.
    Returns "" when no usable label is present. Marker: ``class="link-chip"``.
    """
    if not isinstance(links, (list, tuple)) or not links:
        return ""
    primary = _tok_color(d, "primary")
    text_c = _tok_color(d, "text_dark")
    font = _tok_font(d, "font_body")
    chips: List[str] = []
    for it in list(links)[:6]:  # clamp to 6 chips (요구사항 2.4)
        if isinstance(it, dict):
            label = str(it.get("label", "")).strip()
        elif isinstance(it, str):
            label = it.strip()
        else:
            continue
        if not label:
            continue
        label = _clamp_text(label, 30)
        chips.append(
            f'<div class="link-chip" '
            f'style="color:{text_c};font-family:{font};border-color:{primary}40;">'
            f'<span class="link-chip-icon" style="color:{primary};">'
            f'{_icon("link", 20)}</span>'
            f'<span class="link-chip-label">{_esc(label)}</span>'
            f'<span class="link-chip-arrow" style="color:{primary};">'
            f'{_icon("arrow_right", 18)}</span>'
            f'</div>'
        )
    if not chips:
        return ""
    return f'<div class="link-chips">{"".join(chips)}</div>'


def _numbered_list(items: Any, d: Any) -> str:
    """Numbered list with circular badges 1..n (요구사항 2.5).

    Accepts ``list[str]`` (dicts with a ``"text"`` key tolerated). Clamped to
    1–8 items; badges are sequential starting at 1 over the rendered items.
    Returns "" when no usable item is present. Marker: ``class="numbered-item"``.
    """
    if not isinstance(items, (list, tuple)) or not items:
        return ""
    rows: List[str] = []
    for it in list(items)[:8]:  # clamp to 8 items (요구사항 2.5)
        if isinstance(it, dict):
            txt = str(it.get("text", "")).strip()
        else:
            txt = str(it or "").strip()
        if not txt:
            continue
        rows.append(txt)
    if not rows:
        return ""
    primary = _tok_color(d, "primary")
    light = _tok_color(d, "text_light")
    text_c = _tok_color(d, "text_dark")
    font_h = _tok_font(d, "font_heading")
    font_b = _tok_font(d, "font_body")
    out: List[str] = []
    for idx, txt in enumerate(rows, start=1):  # sequential 1..n (요구사항 2.5)
        out.append(
            f'<div class="numbered-item" '
            f'style="color:{text_c};font-family:{font_b};">'
            f'<span class="numbered-badge" '
            f'style="background:{primary};color:{light};font-family:{font_h};">'
            f'{idx}</span>'
            f'<span class="numbered-text">{_esc(txt)}</span>'
            f'</div>'
        )
    return f'<div class="numbered-list">{"".join(out)}</div>'


def _notice_tab(text: Any, d: Any) -> str:
    """Top-right corner notice tab (요구사항 2.6). Label clamped <=20 chars
    (+ ellipsis). Marker: ``class="notice-tab"``.
    """
    s = str(text or "").strip()
    if not s:
        return ""
    s = _clamp_text(s, 20)
    accent = _tok_color(d, "accent")
    light = _tok_color(d, "text_light")
    font = _tok_font(d, "font_heading")
    return (
        f'<div class="notice-tab" '
        f'style="background:{accent};color:{light};font-family:{font};">'
        f'{_esc(s)}</div>'
    )


def _slide_footer(footer_title: Any, footer_page: Any, d: Any) -> str:
    """Slide footer: running title (<=40 chars) + page string e.g. "1/3"
    (요구사항 2.7). Active when either part is non-empty. The ``footer_page``
    string is shown verbatim ("현재/전체"). Marker: ``class="slide-footer"``.
    """
    title_s = _clamp_text(str(footer_title or "").strip(), 40)
    page_s = str(footer_page or "").strip()
    if not title_s and not page_s:
        return ""
    text_c = _tok_color(d, "text_muted")
    font = _tok_font(d, "font_body")
    parts: List[str] = []
    if title_s:
        parts.append(f'<span class="slide-footer-title">{_esc(title_s)}</span>')
    if page_s:
        parts.append(f'<span class="slide-footer-page">{_esc(page_s)}</span>')
    return (
        f'<div class="slide-footer" '
        f'style="color:{text_c};font-family:{font};">{"".join(parts)}</div>'
    )


def _figure_slots(figures: Any, d: Any) -> str:
    """Captioned screenshot/figure slots for a column (요구사항 3 전체).

    Accepts ``[{"image","caption"}]`` (a bare image-path string is tolerated)
    clamped to 1–10 cards (요구사항 3.1, 3.9). Each image reference is resolved
    through the single ``_safe_image_data_uri`` path: a local file path or a
    ``data:image/`` URI is inlined as a data URI, while any external reference
    (``http(s)://``, protocol-relative ``//``, ``file://``) or empty/unreadable
    input resolves to "" so the image is omitted and the caption / remaining
    slots still render normally (요구사항 3.4, 3.5, 3.6). Figure cards are stacked
    with NO overlap (0px between cards) and each caption sits adjacent to its
    own image (요구사항 3.7, 3.8); the inlined images are card-scoped, never a
    full-bleed background, so the slide's full-bleed background image count stays
    0–1 (요구사항 6.5). A slot with neither a usable image nor a caption is
    skipped; when nothing usable remains the function returns "" (no-op → byte
    preservation). Never raises, never references an external URL. Colors/fonts
    come from ``d`` via _tok_color/_tok_font with per-token fallback (요구사항 7.4).
    Marker: ``class="figure-slot"``.
    """
    if not isinstance(figures, (list, tuple)) or not figures:
        return ""
    border = _tok_color(d, "border")
    card_bg = _tok_color(d, "card_bg")
    muted = _tok_color(d, "text_muted")
    cards: List[str] = []
    for fig in list(figures)[:10]:  # clamp to 10 cards (요구사항 3.1, 3.9)
        if isinstance(fig, dict):
            img_src = fig.get("image", "")
            caption = str(fig.get("caption", "")).strip()
        elif isinstance(fig, str):
            img_src = fig
            caption = ""
        else:
            continue
        # Single image-resolution path: local/data: inline, external/empty → "".
        uri = _safe_image_data_uri(img_src)
        if not uri and not caption:
            continue  # nothing renderable for this slot — skip it
        img_html = ""
        if uri:
            img_html = (
                f'<div class="figure-img" '
                f"style=\"background-image:url('{uri}');\"></div>"
            )
        cap_html = ""
        if caption:
            cap_html = (
                f'<div class="figure-caption" style="color:{muted};">'
                f'{_esc(caption)}</div>'
            )
        cards.append(
            f'<div class="figure-slot" '
            f'style="background:{card_bg};border-color:{border};">'
            f'{img_html}{cap_html}</div>'
        )
    if not cards:
        return ""
    font = _tok_font(d, "font_body")
    return f'<div class="figure-grid" style="font-family:{font};">{"".join(cards)}</div>'


# ---------------------------------------------------------------------------
# 1. COVER SLIDE — title + subtitle on a gradient hero
# ---------------------------------------------------------------------------
def render_cover_slide(
    title: str,
    subtitle: str = "",
    accent_color: Optional[str] = None,
    eyebrow: str = "",
    footer: str = "",
    design: Optional[dict] = None,
    heroImage: str = "",
    # --- 신규 밀도 필드 (모두 no-op 기본값, 생략 시 바이트 보존) ---
    icon_badge: Optional[Any] = None,
    notice_chip: str = "",
    accent_spans: Optional[List[str]] = None,
    step_cards: Optional[List[Dict[str, str]]] = None,
) -> str:
    """Title slide. Big hero text on a gradient background.

    Args:
        title: Main slide title (~ <= 60 chars best). Required.
        subtitle: Optional subtitle, second line.
        accent_color: Override SLIDE_DESIGN['primary'] for the accent bar.
        eyebrow: Optional small text above the title (e.g. project name).
        footer: Optional small text in the bottom-left (e.g. date, version).
            Clamped to <=80 chars (+ ellipsis) when longer (요구사항 1.5).
        design: Optional per-call design-token dict (e.g. from
            design_tokens_for_profile(style_profile)). None → SLIDE_DESIGN.
        heroImage: Optional local image path or data: URI composited as a
            full-bleed hero backdrop behind a gradient scrim (keeps hero text
            legible). When absent/invalid the slide falls back to the plain
            gradient and the HTML is byte-identical to the pre-image output.
        icon_badge: Optional circular icon badge above the title. dict
            ``{"icon": "name"}`` or bare ``"name"``; only a known ICONS name
            yields a badge (요구사항 1.1, 1.10). Marker ``cover-icon-badge``.
        notice_chip: Optional eyebrow pill text (<=40 chars + ellipsis,
            요구사항 1.2). Marker ``notice-chip``.
        accent_spans: Optional list of substrings of ``title`` to highlight in
            the accent color; only substrings that actually occur are wrapped
            (요구사항 1.3, 1.8). Marker ``accent-span``.
        step_cards: Optional ``[{"label","description"}]`` list (1–6, clamped)
            rendered as a STEP card grid (요구사항 1.4, 1.7). Markers
            ``step-card-grid`` / ``step-card``.

    All density fields default to a no-op value; omitting them (or passing the
    documented defaults) produces byte-identical output to the pre-density
    template (요구사항 4.1, 4.2, 4.3).
    """
    d = design if isinstance(design, dict) and design else SLIDE_DESIGN
    accent = accent_color or d["primary"]
    extra_css = f"""
  .cover {{
    width: 1920px;
    height: 1080px;
    background: {d['bg_gradient']};
    color: {d['text_light']};
    display: flex;
    flex-direction: column;
    justify-content: center;
    padding: 120px 160px;
    position: relative;
  }}
  .cover .eyebrow {{
    font-size: 22px;
    letter-spacing: 0.18em;
    text-transform: uppercase;
    color: {accent};
    font-weight: 600;
    margin-bottom: 32px;
  }}
  .cover h1 {{
    font-size: 96px;
    line-height: 1.05;
    color: {d['text_light']};
    max-width: 1500px;
    margin-bottom: 32px;
    font-weight: 800;
  }}
  .cover .subtitle {{
    font-size: 36px;
    color: rgba(255, 255, 255, 0.75);
    line-height: 1.4;
    max-width: 1300px;
    font-weight: 300;
  }}
  .cover .accent-bar {{
    background: {accent};
    margin-bottom: 0;
    margin-top: 56px;
  }}
  .cover .footer {{
    position: absolute;
    bottom: 80px;
    left: 160px;
    font-size: 18px;
    color: rgba(255, 255, 255, 0.5);
    letter-spacing: 0.1em;
  }}
  .cover .corner-glow {{
    position: absolute;
    top: -200px;
    right: -200px;
    width: 600px;
    height: 600px;
    background: radial-gradient(circle, {accent}55 0%, transparent 70%);
    pointer-events: none;
  }}
  .cover .corner-glow-2 {{
    position: absolute;
    bottom: -150px;
    left: 30%;
    width: 400px;
    height: 400px;
    background: radial-gradient(circle, {d['secondary']}33 0%, transparent 70%);
    pointer-events: none;
  }}
"""
    eyebrow_html = f'<div class="eyebrow">{_esc(eyebrow)}</div>' if eyebrow else ''
    subtitle_html = f'<div class="subtitle">{_esc(subtitle)}</div>' if subtitle else ''
    footer_html = f'<div class="footer">{_esc(_clamp_text(footer, 80))}</div>' if footer else ''
    # --- 밀도 요소 빌더 (각자 no-op 입력 시 "" 반환, 바이트 보존) ---
    icon_badge_html = _cover_icon_badge(icon_badge, d)
    notice_chip_html = _notice_chip(notice_chip, d)
    headline_html = _accent_headline(title, accent_spans, d)
    step_grid_html = _step_card_grid(step_cards, d)
    # Optional Vertex hero image composited as a full-bleed backdrop behind a
    # gradient scrim. Built only when a valid image resolves, so the absent
    # case produces byte-identical HTML (extra_css/body unchanged).
    _hero_uri = _safe_image_data_uri(heroImage)
    hero_css = ""
    hero_html = ""
    if _hero_uri:
        hero_css = f"""
  .cover .hero-bg {{
    position: absolute;
    inset: 0;
    background-image: url('{_hero_uri}');
    background-size: cover;
    background-position: center;
    z-index: 0;
  }}
  .cover .hero-scrim {{
    position: absolute;
    inset: 0;
    background: {d['bg_gradient']};
    opacity: 0.78;
    z-index: 1;
  }}
  .cover .corner-glow, .cover .corner-glow-2 {{ z-index: 1; }}
  .cover .eyebrow, .cover h1, .cover .accent-bar,
  .cover .subtitle, .cover .footer {{ position: relative; z-index: 2; }}
"""
        hero_html = '<div class="hero-bg"></div><div class="hero-scrim"></div>'
    # Density CSS blocks — each appended to extra_css ONLY when its element is
    # active, mirroring the hero_css conditional-append pattern so the absent
    # case keeps <head> byte-identical to the prior template (요구사항 4.1, 4.2).
    badge_css = ""
    if icon_badge_html:
        badge_css = """
  .cover .cover-icon-badge {
    width: 104px;
    height: 104px;
    border-radius: 50%;
    display: inline-flex;
    align-items: center;
    justify-content: center;
    margin-bottom: 36px;
    position: relative;
    z-index: 2;
  }
"""
    chip_css = ""
    if notice_chip_html:
        chip_css = """
  .cover .notice-chip {
    display: inline-block;
    align-self: flex-start;
    font-size: 20px;
    font-weight: 700;
    letter-spacing: 0.12em;
    text-transform: uppercase;
    padding: 10px 24px;
    border-radius: 999px;
    margin-bottom: 28px;
    position: relative;
    z-index: 2;
  }
"""
    accent_css = ""
    if 'class="accent-span"' in headline_html:
        accent_css = """
  .cover .accent-span { position: relative; z-index: 2; }
"""
    step_css = ""
    if step_grid_html:
        step_css = """
  .cover .step-card-grid {
    display: grid;
    grid-template-columns: 1fr 1fr;
    gap: 28px;
    margin-top: 48px;
    max-width: 1400px;
    position: relative;
    z-index: 2;
  }
  .cover .step-card {
    border: 1px solid;
    border-radius: 16px;
    padding: 28px 32px;
  }
  .cover .step-card-label {
    font-size: 26px;
    font-weight: 800;
    letter-spacing: 0.04em;
    margin-bottom: 10px;
  }
  .cover .step-card-desc {
    font-size: 22px;
    line-height: 1.4;
    color: rgba(255, 255, 255, 0.78);
  }
"""
    return f"""{_base_head(extra_css + hero_css + badge_css + chip_css + accent_css + step_css, design)}
<body>
  <div class="slide cover">
    {hero_html}<div class="corner-glow"></div>
    <div class="corner-glow-2"></div>
    {eyebrow_html}
    {icon_badge_html}{notice_chip_html}<h1>{headline_html}</h1>
    <div class="accent-bar"></div>
    {subtitle_html}
    {step_grid_html}{footer_html}
  </div>
</body>
</html>"""


# ---------------------------------------------------------------------------
# 2. SECTION DIVIDER — "01 / Section Title" full-bleed
# ---------------------------------------------------------------------------
def render_section_divider(
    title: str,
    section_number: int = 1,
    description: str = "",
    design: Optional[dict] = None,
) -> str:
    """Section break. Big number + section name. Use between major sections.

    Args:
        title: Section name (e.g. "프로젝트 개요").
        section_number: 1-based section number; rendered as "01 / 02" etc.
        description: Optional sub-description under the title.
        design: Optional per-call design-token dict. None → SLIDE_DESIGN.
    """
    d = design if isinstance(design, dict) and design else SLIDE_DESIGN
    num_str = f"{int(section_number):02d}"
    extra_css = f"""
  .divider {{
    width: 1920px;
    height: 1080px;
    background: {d['bg_gradient']};
    color: {d['text_light']};
    display: flex;
    align-items: center;
    padding: 0 160px;
    position: relative;
  }}
  .divider .number {{
    font-size: 320px;
    line-height: 1;
    font-weight: 900;
    color: {d['primary']};
    opacity: 0.92;
    margin-right: 80px;
    font-family: {d['font_heading']};
    letter-spacing: -0.04em;
  }}
  .divider .meta {{
    flex: 1;
    border-left: 4px solid {d['primary']};
    padding-left: 64px;
  }}
  .divider .label {{
    font-size: 22px;
    letter-spacing: 0.2em;
    text-transform: uppercase;
    color: rgba(255, 255, 255, 0.5);
    margin-bottom: 24px;
  }}
  .divider h2 {{
    font-size: 88px;
    color: {d['text_light']};
    line-height: 1.1;
    margin-bottom: 32px;
  }}
  .divider .description {{
    font-size: 28px;
    color: rgba(255, 255, 255, 0.7);
    line-height: 1.5;
    max-width: 900px;
    font-weight: 300;
  }}
"""
    desc_html = f'<div class="description">{_esc(description)}</div>' if description else ''
    return f"""{_base_head(extra_css, design)}
<body>
  <div class="slide divider">
    <div class="number">{num_str}</div>
    <div class="meta">
      <div class="label">SECTION {num_str}</div>
      <h2>{_esc(title)}</h2>
      {desc_html}
    </div>
  </div>
</body>
</html>"""


# ---------------------------------------------------------------------------
# 3. TWO-COLUMN — text left, visual element (or text) right
# ---------------------------------------------------------------------------
def render_two_column(
    title: str,
    left_content: str,
    right_content: str,
    subtitle: str = "",
    design: Optional[dict] = None,
    image: str = "",
    slot_image: str = "",
    left_badge: str = "",
    right_badge: str = "",
    left_metric: str = "",
    right_metric: str = "",
    # --- 신규 밀도 필드 (좌/우 대칭, 모두 no-op 기본값, 생략 시 바이트 보존) ---
    left_section_no: str = "", left_section_title: str = "",
    right_section_no: str = "", right_section_title: str = "",
    left_contact: Optional[Dict[str, Any]] = None,
    right_contact: Optional[Dict[str, Any]] = None,
    left_note: str = "", right_note: str = "",
    left_links: Optional[List[Dict[str, str]]] = None,
    right_links: Optional[List[Dict[str, str]]] = None,
    left_numbered: Optional[List[str]] = None,
    right_numbered: Optional[List[str]] = None,
    left_figures: Optional[List[Dict[str, str]]] = None,
    right_figures: Optional[List[Dict[str, str]]] = None,
    # --- 슬라이드 단위 밀도 필드 ---
    notice_tab: str = "",
    footer_title: str = "", footer_page: str = "",
) -> str:
    """Classic split layout. Left column = narrative, right column = highlights.

    `left_content` and `right_content` may contain newline-separated bullet-y
    lines. We interpret leading "- " or "• " as bullets; otherwise we treat
    the whole string as a paragraph.

    `design` is an optional per-call design-token dict. None → SLIDE_DESIGN.

    `image` is an optional local image path or data: URI composited as a
    full-bleed backdrop behind a light scrim; the opaque content cards stay
    crisp so the high-density two-column design is augmented, not replaced.
    When absent/invalid the HTML is byte-identical to the pre-image output.

    Optional left/right column-head density fields (rendered ONLY when present,
    so omitting them keeps the output byte-identical to before):
      - `left_badge` / `right_badge`: a short label chip atop the column
        (tinted with that column's accent — primary left, secondary right).
      - `left_metric` / `right_metric`: a large headline metric atop the column
        for at-a-glance left/right density balance.

    Optional left/right section-card density fields and slide-level fields
    (pptx-design-density-parity, 요구사항 2). Every field defaults to a no-op
    value; omitting them (or passing the documented defaults) yields output
    byte-identical to the pre-density template (요구사항 4.1, 4.2, 4.3):
      - `left_section_no` + `left_section_title` (and right): dark header bar
        with a numbered badge; title clamped <=40 (요구사항 2.1). Marker
        `section-header-bar`.
      - `left_contact` / `right_contact`: `{"items":[{"label","value"}]}`
        tinted box with a left accent border, label <=30, <=5 items
        (요구사항 2.2). Marker `contact-box`.
      - `left_note` / `right_note`: multiline callout, <=300 chars (요구사항 2.3).
        Marker `note-callout`.
      - `left_links` / `right_links`: `[{"label"}]` link chips (1–6, label <=30)
        with SVG icon + arrow, no emoji (요구사항 2.4, 2.9). Marker `link-chip`.
      - `left_numbered` / `right_numbered`: `list[str]` (1–8) with sequential
        circular number badges (요구사항 2.5). Marker `numbered-item`.
      - `left_figures` / `right_figures`: `[{"image","caption"}]` (1–10,
        clamped) captioned screenshot cards. Images are inlined via
        `_safe_image_data_uri` (local/`data:` only; external refs rejected →
        image omitted, caption still renders); cards never overlap (요구사항 3).
        Marker `figure-slot`.
      - `notice_tab`: top-right corner tab, <=20 chars (요구사항 2.6). Marker
        `notice-tab`.
      - `footer_title` + `footer_page`: bottom running title (<=40) + page
        string e.g. "1/3" (요구사항 2.7). Marker `slide-footer`.
    The column density elements are wrapped in an `overflow:hidden` container so
    content cannot escape the slide bounds (요구사항 2.11, 6.6).
    """
    d = design if isinstance(design, dict) and design else SLIDE_DESIGN

    def _format_column(content: str) -> str:
        lines = [ln.strip() for ln in (content or "").splitlines() if ln.strip()]
        if not lines:
            return ''
        # Treat as bullet list if 2+ lines look like bullets
        bulletish = sum(1 for ln in lines if ln.startswith(("-", "•", "*")) or (len(ln) < 80))
        if bulletish >= 2 and len(lines) >= 2:
            items = []
            for ln in lines:
                clean = ln.lstrip("-•* ").strip()
                items.append(
                    f'<li><span class="bullet-icon">{_icon("check", 20)}</span>'
                    f'<span class="bullet-text">{_esc(clean)}</span></li>'
                )
            return f'<ul class="bullets">{"".join(items)}</ul>'
        # Otherwise paragraphs
        return "".join(f'<p>{_esc(ln)}</p>' for ln in lines)

    # Optional column-head density chrome (badge + metric). Built only when a
    # field is supplied so the absent case is byte-identical.
    def _col_head(badge: str, metric: str, color: str) -> str:
        badge = str(badge or "").strip()
        metric = str(metric or "").strip()
        if not badge and not metric:
            return ''
        parts = []
        if badge:
            parts.append(
                f'<div class="col-badge" style="background:{color}15;color:{color};">'
                f'{_esc(badge)}</div>'
            )
        if metric:
            parts.append(
                f'<div class="col-metric" style="color:{color};">{_esc(metric)}</div>'
            )
        return f'<div class="col-head">{"".join(parts)}</div>'

    _left_head = _col_head(left_badge, left_metric, d["primary"])
    _right_head = _col_head(right_badge, right_metric, d["secondary"])
    _col_density = bool(_left_head or _right_head)

    # --- 본문 밀도 빌더 (각자 no-op 입력 시 "" 반환 → 바이트 보존) ---
    _sec_left = _section_header_bar(left_section_no, left_section_title, d)
    _sec_right = _section_header_bar(right_section_no, right_section_title, d)
    _left_contact_html = _contact_box(left_contact, d)
    _right_contact_html = _contact_box(right_contact, d)
    _left_note_html = _note_callout(left_note, d)
    _right_note_html = _note_callout(right_note, d)
    _left_links_html = _link_chips(left_links, d)
    _right_links_html = _link_chips(right_links, d)
    _left_numbered_html = _numbered_list(left_numbered, d)
    _right_numbered_html = _numbered_list(right_numbered, d)
    _left_figures_html = _figure_slots(left_figures, d)
    _right_figures_html = _figure_slots(right_figures, d)
    notice_tab_html = _notice_tab(notice_tab, d)
    footer_html = _slide_footer(footer_title, footer_page, d)

    def _col_density_wrap(*frags: str) -> str:
        # Wrap a column's body-density elements in an overflow:hidden container
        # so they cannot escape the slide bounds (요구사항 2.11, 6.6). Returns ""
        # when nothing is active, preserving byte-identical output.
        body = "".join(f for f in frags if f)
        if not body:
            return ""
        return f'<div class="col-density">{body}</div>'

    _left_density = _col_density_wrap(
        _left_numbered_html, _left_links_html, _left_contact_html,
        _left_note_html, _left_figures_html
    )
    _right_density = _col_density_wrap(
        _right_numbered_html, _right_links_html, _right_contact_html,
        _right_note_html, _right_figures_html
    )
    _has_section = bool(_sec_left or _sec_right)
    _has_contact = bool(_left_contact_html or _right_contact_html)
    _has_note = bool(_left_note_html or _right_note_html)
    _has_links = bool(_left_links_html or _right_links_html)
    _has_numbered = bool(_left_numbered_html or _right_numbered_html)
    _has_figures = bool(_left_figures_html or _right_figures_html)
    _has_col_density = bool(_left_density or _right_density)

    extra_css = f"""
  .two-col {{
    width: 1920px;
    height: 1080px;
    background: {d['bg_light']};
    padding: 100px 140px;
    display: flex;
    flex-direction: column;
  }}
  .two-col .header {{ margin-bottom: 64px; }}
  .two-col h2 {{ color: {d['text_dark']}; max-width: 1500px; }}
  .two-col .subtitle {{
    font-size: 26px;
    color: {d['text_muted']};
    margin-top: 16px;
    line-height: 1.4;
    max-width: 1400px;
  }}
  .two-col .accent-bar {{ background: {d['primary']}; margin-bottom: 48px; margin-top: 0; }}
  .two-col .columns {{
    flex: 1;
    display: grid;
    grid-template-columns: 1fr 1fr;
    gap: 80px;
  }}
  .two-col .col {{
    background: {d['card_bg']};
    border-radius: 20px;
    padding: 56px 56px;
    box-shadow: {d['card_shadow']};
    border-top: 6px solid {d['primary']};
  }}
  .two-col .col.right {{ border-top-color: {d['secondary']}; }}
  .two-col .col p {{ margin-bottom: 18px; font-size: 26px; line-height: 1.55; }}
  .two-col .bullets {{ list-style: none; padding: 0; }}
  .two-col .bullets li {{
    display: flex;
    gap: 18px;
    align-items: flex-start;
    margin-bottom: 22px;
    font-size: 26px;
    line-height: 1.4;
  }}
  .two-col .bullet-icon {{
    flex-shrink: 0;
    width: 36px;
    height: 36px;
    border-radius: 50%;
    background: {d['primary']}15;
    color: {d['primary']};
    display: inline-flex;
    align-items: center;
    justify-content: center;
    margin-top: 2px;
  }}
  .two-col .col.right .bullet-icon {{ background: {d['secondary']}15; color: {d['secondary']}; }}
  .two-col .bullet-text {{ flex: 1; color: {d['text_dark']}; }}
"""
    sub_html = f'<div class="subtitle">{_esc(subtitle)}</div>' if subtitle else ''
    # Optional Vertex image composited as a full-bleed backdrop behind a light
    # scrim. The content cards keep their opaque card_bg, so the dense two-col
    # layout is augmented (image shows through margins/gutters), not replaced.
    # Built only when a valid image resolves → byte-identical when absent.
    _img_uri = _safe_image_data_uri(image)
    img_css = ""
    img_html = ""
    if _img_uri:
        img_css = f"""
  .two-col {{ position: relative; }}
  .two-col .bg-image {{
    position: absolute;
    inset: 0;
    background-image: url('{_img_uri}');
    background-size: cover;
    background-position: center;
    z-index: 0;
  }}
  .two-col .bg-scrim {{
    position: absolute;
    inset: 0;
    background: {d['bg_light']};
    opacity: 0.9;
    z-index: 1;
  }}
  .two-col .header, .two-col .columns {{ position: relative; z-index: 2; }}
"""
        img_html = '<div class="bg-image"></div><div class="bg-scrim"></div>'
    # Optional BOUNDED Image_Slot — distinct from the full-bleed `image`
    # backdrop above. Rendered as a capped-height card at the top of the RIGHT
    # column (an "image column" region), sized SMALLER than the full slide so it
    # never becomes a full-bleed backdrop covering 13.333in x 7.5in (요구사항
    # 2.3, 2.4). Uses `_safe_image_data_uri` (local path / data: URI only;
    # external refs rejected). Built ONLY when a valid image resolves → when the
    # field is absent/invalid the output is byte-identical to the prior template
    # and callers fall back to the existing gradient/placeholder.
    _slot_uri = _safe_image_data_uri(slot_image)
    slot_css = ""
    slot_html = ""
    if _slot_uri:
        slot_css = f"""
  .two-col .image-slot {{
    border-radius: 16px;
    overflow: hidden;
    margin-bottom: 28px;
    box-shadow: {d['card_shadow']};
  }}
  .two-col .image-slot-img {{
    width: 100%;
    height: 300px;
    background-image: url('{_slot_uri}');
    background-size: cover;
    background-position: center;
    background-repeat: no-repeat;
  }}
"""
        slot_html = '<div class="image-slot"><div class="image-slot-img"></div></div>'
    col_density_css = ""
    if _col_density:
        col_density_css = f"""
  .two-col .col-head {{ margin-bottom: 28px; }}
  .two-col .col-badge {{
    display: inline-block;
    font-size: 18px;
    font-weight: 700;
    letter-spacing: 0.1em;
    text-transform: uppercase;
    padding: 8px 20px;
    border-radius: 999px;
    line-height: 1;
    margin-bottom: 18px;
  }}
  .two-col .col-metric {{
    font-size: 56px;
    font-weight: 800;
    font-family: {d['font_heading']};
    line-height: 1;
    letter-spacing: -0.02em;
  }}
"""
    # Body-density CSS — each block appended ONLY when its element is active,
    # mirroring the img_css/col_density_css conditional-append pattern so the
    # absent case keeps the <head> byte-identical to the prior template
    # (요구사항 4.1, 4.2). All colors/fonts come from design tokens (요구사항 7).
    body_density_css = ""
    if notice_tab_html or footer_html:
        body_density_css += """
  .two-col { position: relative; }
"""
    if _has_section:
        body_density_css += """
  .two-col .section-header-bar {
    display: flex;
    align-items: center;
    gap: 18px;
    padding: 16px 24px;
    border-radius: 12px;
    margin-bottom: 28px;
  }
  .two-col .section-no {
    flex-shrink: 0;
    min-width: 44px;
    height: 44px;
    padding: 0 12px;
    border-radius: 10px;
    display: inline-flex;
    align-items: center;
    justify-content: center;
    font-size: 24px;
    font-weight: 800;
  }
  .two-col .section-title {
    font-size: 28px;
    font-weight: 700;
    letter-spacing: -0.01em;
  }
"""
    if _has_col_density:
        body_density_css += """
  .two-col .col-density {
    overflow: hidden;
    margin-top: 28px;
  }
"""
    if _has_numbered:
        body_density_css += """
  .two-col .numbered-list { margin-bottom: 8px; }
  .two-col .numbered-item {
    display: flex;
    gap: 16px;
    align-items: flex-start;
    margin-bottom: 18px;
    font-size: 24px;
    line-height: 1.45;
  }
  .two-col .numbered-badge {
    flex-shrink: 0;
    width: 40px;
    height: 40px;
    border-radius: 50%;
    display: inline-flex;
    align-items: center;
    justify-content: center;
    font-size: 22px;
    font-weight: 800;
  }
  .two-col .numbered-text { flex: 1; }
"""
    if _has_links:
        body_density_css += """
  .two-col .link-chips {
    display: flex;
    flex-direction: column;
    gap: 14px;
    margin-bottom: 8px;
  }
  .two-col .link-chip {
    display: flex;
    align-items: center;
    gap: 14px;
    padding: 14px 20px;
    border: 1px solid;
    border-radius: 12px;
    font-size: 23px;
    font-weight: 600;
  }
  .two-col .link-chip-icon, .two-col .link-chip-arrow {
    flex-shrink: 0;
    display: inline-flex;
    align-items: center;
  }
  .two-col .link-chip-label { flex: 1; }
"""
    if _has_contact:
        body_density_css += """
  .two-col .contact-box {
    border-left: 6px solid;
    border-radius: 12px;
    padding: 24px 28px;
    margin-bottom: 8px;
  }
  .two-col .contact-row {
    display: flex;
    gap: 18px;
    align-items: baseline;
    margin-bottom: 12px;
    font-size: 23px;
    line-height: 1.4;
  }
  .two-col .contact-row:last-child { margin-bottom: 0; }
  .two-col .contact-label {
    flex-shrink: 0;
    min-width: 140px;
    font-weight: 700;
    opacity: 0.82;
  }
  .two-col .contact-value { flex: 1; font-weight: 600; }
"""
    if _has_note:
        body_density_css += """
  .two-col .note-callout {
    border-left: 6px solid;
    border-radius: 12px;
    padding: 24px 28px;
    margin-bottom: 8px;
    font-size: 23px;
    line-height: 1.5;
  }
"""
    if _has_figures:
        body_density_css += """
  .two-col .figure-grid {
    display: flex;
    flex-direction: column;
    gap: 20px;
    margin-bottom: 8px;
  }
  .two-col .figure-slot {
    border: 1px solid;
    border-radius: 12px;
    overflow: hidden;
  }
  .two-col .figure-img {
    width: 100%;
    height: 220px;
    background-size: cover;
    background-position: center;
    background-repeat: no-repeat;
  }
  .two-col .figure-caption {
    padding: 14px 20px;
    font-size: 21px;
    line-height: 1.4;
    font-weight: 600;
  }
"""
    if notice_tab_html:
        body_density_css += """
  .two-col .notice-tab {
    position: absolute;
    top: 0;
    right: 0;
    padding: 18px 40px;
    border-bottom-left-radius: 16px;
    font-size: 20px;
    font-weight: 700;
    letter-spacing: 0.1em;
    text-transform: uppercase;
    z-index: 3;
  }
"""
    if footer_html:
        body_density_css += """
  .two-col .slide-footer {
    position: absolute;
    bottom: 36px;
    left: 140px;
    right: 140px;
    display: flex;
    justify-content: space-between;
    align-items: center;
    font-size: 20px;
    letter-spacing: 0.06em;
    z-index: 3;
  }
  .two-col .slide-footer-page { font-weight: 700; }
"""
    return f"""{_base_head(extra_css + img_css + slot_css + col_density_css + body_density_css, design)}
<body>
  <div class="slide two-col">
    {img_html}{notice_tab_html}<div class="header">
      <div class="accent-bar"></div>
      <h2>{_esc(title)}</h2>
      {sub_html}
    </div>
    <div class="columns">
      <div class="col left">{_sec_left}{_left_head}{_format_column(left_content)}{_left_density}</div>
      <div class="col right">{slot_html}{_sec_right}{_right_head}{_format_column(right_content)}{_right_density}</div>
    </div>{footer_html}
  </div>
</body>
</html>"""


# ---------------------------------------------------------------------------
# 4. FEATURE GRID — 3 to 6 cards, each = icon + title + description
# ---------------------------------------------------------------------------
def render_feature_grid(
    title: str,
    features: List[Dict[str, str]],
    subtitle: str = "",
    design: Optional[dict] = None,
    step_badges: bool = False,
) -> str:
    """Card grid. Each feature dict = {icon, title, description}.

    Args:
        title: Slide title.
        features: 3-6 dicts. `icon` should be one of the keys in ICONS;
                  unknown icon names fall back to 'circle'. Optional per-feature
                  density fields (rendered ONLY when present, so omitting them
                  keeps the output byte-identical to the pre-density template):
                    - `badge`: short label chip on the card (e.g. "STEP 1",
                      "필수"). Tinted with the card's accent color.
                    - `meta`: a secondary caption line under the description
                      (e.g. a metric, owner, or due date).
        subtitle: Optional sub-line under title.
        design: Optional per-call design-token dict. None → SLIDE_DESIGN.
        step_badges: When True, auto-number each card with a "STEP n" badge
                  (a per-feature explicit `badge` still wins). Defaults False so
                  existing callers are unaffected (byte-identical output).
    """
    d = design if isinstance(design, dict) and design else SLIDE_DESIGN
    feats = features[:6] if features else []
    if not feats:
        feats = [{"icon": "circle", "title": "(no features provided)", "description": ""}]
    n = len(feats)
    # Choose grid layout — 1-3 = 1 row of 3, 4 = 2x2, 5-6 = 3x2
    if n <= 3:
        cols, rows = n, 1
    elif n == 4:
        cols, rows = 2, 2
    else:
        cols, rows = 3, 2

    accent_palette = [d["primary"], d["secondary"], d["accent"], "#7C3AED", "#0EA5E9", "#D97706"]

    cards_html = []
    _grid_density = False  # flips True when any optional density field renders
    for i, f in enumerate(feats):
        icon_name = (f.get("icon") or "circle").strip()
        f_title = f.get("title") or ""
        f_desc = f.get("description") or ""
        color = accent_palette[i % len(accent_palette)]
        # Optional density chrome — built only when present so the absent case
        # stays byte-identical (empty strings slot into the exact same markup).
        badge = (str(f.get("badge", "")).strip())
        if not badge and step_badges:
            badge = f"STEP {i + 1}"
        badge_html = (
            f'<div class="card-badge" style="background:{color}1A;color:{color};">'
            f'{_esc(badge)}</div>'
        ) if badge else ''
        meta = str(f.get("meta", "")).strip()
        meta_html = (
            f'<p class="card-meta" style="color:{color};">{_esc(meta)}</p>'
        ) if meta else ''
        if badge_html or meta_html:
            _grid_density = True
        cards_html.append(f"""
        <div class="card" style="background:linear-gradient(135deg,{color}1A 0%,{color}08 55%,#FFFFFF 100%);border-left:10px solid {color};">{badge_html}
          <div class="card-icon" style="background:{color};color:#FFFFFF;box-shadow:0 8px 24px {color}55;">{_icon(icon_name, 40)}</div>
          <h4>{_esc(f_title)}</h4>
          <p>{_esc(f_desc)}</p>{meta_html}
        </div>""")

    extra_css = f"""
  .grid-slide {{
    width: 1920px;
    height: 1080px;
    background: {d['bg_light']};
    padding: 100px 140px;
    display: flex;
    flex-direction: column;
  }}
  .grid-slide .header {{ margin-bottom: 64px; }}
  .grid-slide h2 {{ color: {d['text_dark']}; }}
  .grid-slide .subtitle {{
    font-size: 26px;
    color: {d['text_muted']};
    margin-top: 16px;
    max-width: 1400px;
    line-height: 1.4;
  }}
  .grid-slide .accent-bar {{ background: {d['primary']}; margin-top: 0; }}
  .grid-slide .grid {{
    flex: 1;
    display: grid;
    grid-template-columns: repeat({cols}, 1fr);
    grid-template-rows: repeat({rows}, 1fr);
    gap: 32px;
  }}
  .grid-slide .card {{
    background: {d['card_bg']};
    border-radius: 22px;
    padding: 52px 48px;
    box-shadow: 0 16px 48px rgba(15,23,42,0.10);
    display: flex;
    flex-direction: column;
    justify-content: center;
    border: 1px solid {d['border']};
  }}
  .grid-slide .card-icon {{
    width: 92px;
    height: 92px;
    border-radius: 22px;
    display: inline-flex;
    align-items: center;
    justify-content: center;
    margin-bottom: 32px;
  }}
  .grid-slide .card h4 {{
    font-size: 36px;
    color: {d['text_dark']};
    margin-bottom: 18px;
    line-height: 1.2;
    font-weight: 700;
  }}
  .grid-slide .card p {{
    font-size: 25px;
    color: {d['text_muted']};
    line-height: 1.5;
  }}
"""
    # Optional density CSS — emitted ONLY when a badge/meta actually rendered,
    # so callers that pass no density fields get byte-identical <head> output.
    density_css = ""
    if _grid_density:
        density_css = f"""
  .grid-slide .card {{ position: relative; }}
  .grid-slide .card-badge {{
    position: absolute;
    top: 28px;
    right: 28px;
    font-size: 18px;
    font-weight: 700;
    letter-spacing: 0.08em;
    text-transform: uppercase;
    padding: 8px 18px;
    border-radius: 999px;
    line-height: 1;
  }}
  .grid-slide .card-meta {{
    font-size: 20px;
    font-weight: 600;
    line-height: 1.4;
    margin-top: 18px;
    padding-top: 16px;
    border-top: 1px solid {d['border']};
  }}
"""
    sub_html = f'<div class="subtitle">{_esc(subtitle)}</div>' if subtitle else ''
    return f"""{_base_head(extra_css + density_css, design)}
<body>
  <div class="slide grid-slide">
    <div class="header">
      <div class="accent-bar"></div>
      <h2>{_esc(title)}</h2>
      {sub_html}
    </div>
    <div class="grid">{"".join(cards_html)}</div>
  </div>
</body>
</html>"""


# ---------------------------------------------------------------------------
# 5. TIMELINE — horizontal step flow (auto-falls-back to vertical for 6+ steps)
# ---------------------------------------------------------------------------
def render_timeline(
    title: str,
    steps: List[Dict[str, str]],
    subtitle: str = "",
    orientation: str = "auto",  # "horizontal" | "vertical" | "auto"
    design: Optional[dict] = None,
) -> str:
    """Step-by-step timeline. Each step = {label, title, description}.

    Args:
        steps: list of dicts, 2-7 items recommended. Optional per-step density
            fields (rendered ONLY when present → omitting keeps byte-identical
            output): `tone`/`status` colors the step circle + connector via a
            design-token tone (primary|secondary|accent|dark|neutral), and
            `meta` adds a secondary caption line under the step description.
        orientation: "auto" picks horizontal for <=5 steps, vertical otherwise.
        design: Optional per-call design-token dict. None → SLIDE_DESIGN.
    """
    d = design if isinstance(design, dict) and design else SLIDE_DESIGN
    steps = steps[:7] if steps else []
    if not steps:
        steps = [{"label": "1", "title": "(no steps)", "description": ""}]
    n = len(steps)
    o = orientation
    if o == "auto":
        o = "horizontal" if n <= 5 else "vertical"

    # Optional density: any step carrying tone/status/meta turns on the extra
    # density chrome (circle color + meta caption). Absent → byte-identical.
    _tl_density = any(
        isinstance(s, dict) and (s.get("tone") or s.get("status") or s.get("meta"))
        for s in steps
    )

    if o == "horizontal":
        items = []
        for i, s in enumerate(steps):
            label = s.get("label") or f"{i+1:02d}"
            s_title = s.get("title") or ""
            s_desc = s.get("description") or ""
            connector = '<div class="connector"></div>' if i < n - 1 else ''
            tone = str(s.get("tone") or s.get("status") or "").strip()
            circle_attr = (
                f' style="background:{_tone_color(tone, d)};'
                f'box-shadow:0 8px 24px {_tone_color(tone, d)}55;"'
            ) if tone else ''
            s_meta = str(s.get("meta", "")).strip()
            meta_html = (
                f'<div class="step-meta" style="color:{_tone_color(tone, d)};">'
                f'{_esc(s_meta)}</div>'
            ) if s_meta else ''
            items.append(f"""
            <div class="step">
              <div class="step-circle"{circle_attr}>{_esc(str(label))}</div>
              <div class="step-body">
                <h4>{_esc(s_title)}</h4>
                <p>{_esc(s_desc)}</p>{meta_html}
              </div>
              {connector}
            </div>""")
        body = f'<div class="timeline horizontal">{"".join(items)}</div>'
        extra = f"""
  .timeline.horizontal {{
    flex: 1;
    display: grid;
    grid-template-columns: repeat({n}, 1fr);
    gap: 0;
    align-items: start;
    padding: 40px 0;
  }}
  .timeline.horizontal .step {{
    position: relative;
    padding: 0 24px;
    display: flex;
    flex-direction: column;
    align-items: center;
    text-align: center;
  }}
  .timeline.horizontal .step-circle {{
    width: 88px;
    height: 88px;
    border-radius: 50%;
    background: {d['primary']};
    color: {d['text_light']};
    display: flex;
    align-items: center;
    justify-content: center;
    font-size: 30px;
    font-weight: 700;
    margin-bottom: 28px;
    box-shadow: 0 8px 24px {d['primary']}55;
    z-index: 2;
    position: relative;
  }}
  .timeline.horizontal .connector {{
    position: absolute;
    top: 44px;
    left: calc(50% + 44px);
    right: calc(-50% + 44px);
    height: 4px;
    background: linear-gradient(90deg, {d['primary']}, {d['secondary']});
    z-index: 1;
  }}
  .timeline.horizontal .step-body {{ max-width: 320px; }}
  .timeline.horizontal h4 {{ font-size: 26px; margin-bottom: 12px; color: {d['text_dark']}; }}
  .timeline.horizontal p {{ font-size: 19px; color: {d['text_muted']}; line-height: 1.5; }}
"""
    else:
        items = []
        for i, s in enumerate(steps):
            label = s.get("label") or f"{i+1:02d}"
            s_title = s.get("title") or ""
            s_desc = s.get("description") or ""
            connector = '<div class="v-connector"></div>' if i < n - 1 else ''
            tone = str(s.get("tone") or s.get("status") or "").strip()
            circle_attr = (
                f' style="background:{_tone_color(tone, d)};"'
            ) if tone else ''
            body_attr = (
                f' style="border-left-color:{_tone_color(tone, d)};"'
            ) if tone else ''
            s_meta = str(s.get("meta", "")).strip()
            meta_html = (
                f'<div class="step-meta" style="color:{_tone_color(tone, d)};">'
                f'{_esc(s_meta)}</div>'
            ) if s_meta else ''
            items.append(f"""
            <div class="v-step">
              <div class="v-marker">
                <div class="v-circle"{circle_attr}>{_esc(str(label))}</div>
                {connector}
              </div>
              <div class="v-body"{body_attr}>
                <h4>{_esc(s_title)}</h4>
                <p>{_esc(s_desc)}</p>{meta_html}
              </div>
            </div>""")
        body = f'<div class="timeline vertical">{"".join(items)}</div>'
        extra = f"""
  .timeline.vertical {{
    flex: 1;
    overflow: hidden;
  }}
  .timeline.vertical .v-step {{
    display: grid;
    grid-template-columns: 100px 1fr;
    gap: 32px;
    margin-bottom: 24px;
  }}
  .timeline.vertical .v-marker {{
    display: flex;
    flex-direction: column;
    align-items: center;
    position: relative;
  }}
  .timeline.vertical .v-circle {{
    width: 72px;
    height: 72px;
    border-radius: 50%;
    background: {d['primary']};
    color: {d['text_light']};
    display: flex;
    align-items: center;
    justify-content: center;
    font-size: 24px;
    font-weight: 700;
    flex-shrink: 0;
  }}
  .timeline.vertical .v-connector {{
    flex: 1;
    width: 4px;
    background: {d['border']};
    margin-top: 4px;
  }}
  .timeline.vertical .v-body {{
    background: {d['card_bg']};
    border-radius: 16px;
    padding: 28px 36px;
    box-shadow: {d['card_shadow']};
    border-left: 6px solid {d['primary']};
  }}
  .timeline.vertical h4 {{ font-size: 28px; margin-bottom: 8px; color: {d['text_dark']}; }}
  .timeline.vertical p {{ font-size: 22px; color: {d['text_muted']}; line-height: 1.5; }}
"""

    extra_css = f"""
  .timeline-slide {{
    width: 1920px;
    height: 1080px;
    background: {d['bg_light']};
    padding: 100px 140px;
    display: flex;
    flex-direction: column;
  }}
  .timeline-slide .header {{ margin-bottom: 56px; }}
  .timeline-slide h2 {{ color: {d['text_dark']}; }}
  .timeline-slide .subtitle {{
    font-size: 26px;
    color: {d['text_muted']};
    margin-top: 16px;
    line-height: 1.4;
    max-width: 1400px;
  }}
  .timeline-slide .accent-bar {{ background: {d['primary']}; margin-top: 0; }}
{extra}
"""
    # Optional density CSS — only emitted when a step carried tone/status/meta,
    # so the absent case keeps the <head> byte-identical to the prior template.
    density_css = ""
    if _tl_density:
        density_css = f"""
  .timeline.horizontal .step-meta {{
    font-size: 18px;
    font-weight: 700;
    letter-spacing: 0.04em;
    margin-top: 10px;
  }}
  .timeline.vertical .step-meta {{
    font-size: 20px;
    font-weight: 700;
    letter-spacing: 0.04em;
    margin-top: 10px;
  }}
"""
    sub_html = f'<div class="subtitle">{_esc(subtitle)}</div>' if subtitle else ''
    return f"""{_base_head(extra_css + density_css, design)}
<body>
  <div class="slide timeline-slide">
    <div class="header">
      <div class="accent-bar"></div>
      <h2>{_esc(title)}</h2>
      {sub_html}
    </div>
    {body}
  </div>
</body>
</html>"""


# ---------------------------------------------------------------------------
# 6. COMPARISON — 좌우 비교 (Before/After, A/B, etc.)
# ---------------------------------------------------------------------------
def render_comparison(
    title: str,
    left_label: str,
    left_items: List[str],
    right_label: str,
    right_items: List[str],
    subtitle: str = "",
    left_tone: str = "negative",   # "negative" | "neutral" | "positive"
    right_tone: str = "positive",
    design: Optional[dict] = None,
) -> str:
    """Side-by-side comparison. Each side has a label header and a bullet list.

    `design` is an optional per-call design-token dict. None → SLIDE_DESIGN.
    """
    d = design if isinstance(design, dict) and design else SLIDE_DESIGN

    def _tone_color(tone: str) -> str:
        return {
            "negative": "#EF4444",
            "neutral": d["text_muted"],
            "positive": d["secondary"],
        }.get(tone, d["primary"])

    def _tone_icon(tone: str) -> str:
        return "x" if tone == "negative" else "check"

    l_color = _tone_color(left_tone)
    r_color = _tone_color(right_tone)
    l_icon = _tone_icon(left_tone)
    r_icon = _tone_icon(right_tone)

    def _render_items(items: List[str], color: str, icon_name: str) -> str:
        out = []
        for it in (items or [])[:8]:
            out.append(
                f'<li>'
                f'<span class="cmp-icon" style="background:{color}15;color:{color};">{_icon(icon_name, 20)}</span>'
                f'<span>{_esc(it)}</span></li>'
            )
        return f'<ul class="cmp-list">{"".join(out)}</ul>'

    extra_css = f"""
  .cmp-slide {{
    width: 1920px;
    height: 1080px;
    background: {d['bg_light']};
    padding: 100px 140px;
    display: flex;
    flex-direction: column;
  }}
  .cmp-slide .header {{ margin-bottom: 56px; text-align: center; }}
  .cmp-slide h2 {{ color: {d['text_dark']}; text-align: center; }}
  .cmp-slide .subtitle {{
    font-size: 26px;
    color: {d['text_muted']};
    margin-top: 16px;
    line-height: 1.4;
  }}
  .cmp-slide .accent-bar {{ background: {d['primary']}; margin-top: 0; margin-left: auto; margin-right: auto; }}
  .cmp-slide .cmp-grid {{
    flex: 1;
    display: grid;
    grid-template-columns: 1fr 80px 1fr;
    gap: 24px;
    align-items: stretch;
  }}
  .cmp-slide .cmp-side {{
    background: {d['card_bg']};
    border-radius: 24px;
    padding: 56px 48px;
    box-shadow: {d['card_shadow']};
    display: flex;
    flex-direction: column;
  }}
  .cmp-slide .cmp-side.left {{ border-top: 6px solid {l_color}; }}
  .cmp-slide .cmp-side.right {{ border-top: 6px solid {r_color}; }}
  .cmp-slide .cmp-label {{
    font-size: 20px;
    letter-spacing: 0.16em;
    text-transform: uppercase;
    font-weight: 700;
    margin-bottom: 24px;
  }}
  .cmp-slide .cmp-side.left .cmp-label {{ color: {l_color}; }}
  .cmp-slide .cmp-side.right .cmp-label {{ color: {r_color}; }}
  .cmp-slide .cmp-list {{ list-style: none; flex: 1; }}
  .cmp-slide .cmp-list li {{
    display: flex;
    gap: 18px;
    align-items: flex-start;
    margin-bottom: 22px;
    font-size: 24px;
    line-height: 1.45;
    color: {d['text_dark']};
  }}
  .cmp-slide .cmp-icon {{
    flex-shrink: 0;
    width: 36px;
    height: 36px;
    border-radius: 50%;
    display: inline-flex;
    align-items: center;
    justify-content: center;
    margin-top: 2px;
  }}
  .cmp-slide .cmp-vs {{
    display: flex;
    align-items: center;
    justify-content: center;
    color: {d['text_muted']};
    font-size: 32px;
    font-weight: 700;
    letter-spacing: 0.1em;
  }}
"""
    sub_html = f'<div class="subtitle">{_esc(subtitle)}</div>' if subtitle else ''
    return f"""{_base_head(extra_css, design)}
<body>
  <div class="slide cmp-slide">
    <div class="header">
      <div class="accent-bar"></div>
      <h2>{_esc(title)}</h2>
      {sub_html}
    </div>
    <div class="cmp-grid">
      <div class="cmp-side left">
        <div class="cmp-label">{_esc(left_label)}</div>
        {_render_items(left_items, l_color, l_icon)}
      </div>
      <div class="cmp-vs">VS</div>
      <div class="cmp-side right">
        <div class="cmp-label">{_esc(right_label)}</div>
        {_render_items(right_items, r_color, r_icon)}
      </div>
    </div>
  </div>
</body>
</html>"""


# ---------------------------------------------------------------------------
# 7. ARCHITECTURE — vertical layered system diagram with arrows
# ---------------------------------------------------------------------------
def render_architecture(
    title: str,
    layers: List[Dict[str, Any]],
    subtitle: str = "",
    design: Optional[dict] = None,
) -> str:
    """System architecture diagram. Each layer = {name, description, items}.

    Args:
        layers: ordered list of dicts:
            { "name": "프론트엔드", "description": "사용자 인터페이스",
              "items": ["Electron", "Vanilla JS", "CSS"] }
            Recommended 2-5 layers.
        design: Optional per-call design-token dict. None → SLIDE_DESIGN.
    """
    d = design if isinstance(design, dict) and design else SLIDE_DESIGN
    layers = layers[:6] if layers else []
    if not layers:
        layers = [{"name": "(no layers)", "description": "", "items": []}]

    layer_palette = [d["primary"], d["secondary"], d["accent"], "#7C3AED", "#0EA5E9", "#D97706"]

    layer_blocks = []
    n = len(layers)
    for i, layer in enumerate(layers):
        name = layer.get("name") or f"Layer {i+1}"
        desc = layer.get("description") or ""
        items = layer.get("items") or []
        color = layer_palette[i % len(layer_palette)]
        items_html = "".join(
            f'<span class="arch-pill" style="background:{color}15;color:{color};border-color:{color}55;">{_esc(it)}</span>'
            for it in items[:8]
        )
        arrow_html = (
            f'<div class="arch-arrow" style="color:{layer_palette[(i+1) % len(layer_palette)]};">'
            f'{_icon("arrow_right", 32)}'
            f'</div>'
        ) if i < n - 1 else ''
        # the arrow on architecture is vertical — rotate via CSS
        layer_blocks.append(f"""
        <div class="arch-layer" style="border-left:8px solid {color};">
          <div class="arch-layer-head">
            <div class="arch-layer-name" style="color:{color};">{_esc(name)}</div>
            <div class="arch-layer-desc">{_esc(desc)}</div>
          </div>
          <div class="arch-layer-items">{items_html}</div>
        </div>
        {arrow_html}""")

    extra_css = f"""
  .arch-slide {{
    width: 1920px;
    height: 1080px;
    background: {d['bg_light']};
    padding: 80px 200px;
    display: flex;
    flex-direction: column;
  }}
  .arch-slide .header {{ margin-bottom: 40px; }}
  .arch-slide h2 {{ color: {d['text_dark']}; }}
  .arch-slide .subtitle {{
    font-size: 24px;
    color: {d['text_muted']};
    margin-top: 12px;
    line-height: 1.4;
    max-width: 1400px;
  }}
  .arch-slide .accent-bar {{ background: {d['primary']}; margin-top: 0; }}
  .arch-slide .layers {{
    flex: 1;
    display: flex;
    flex-direction: column;
    gap: 12px;
    overflow: hidden;
  }}
  .arch-slide .arch-layer {{
    background: {d['card_bg']};
    border-radius: 16px;
    padding: 28px 36px;
    box-shadow: {d['card_shadow']};
    display: flex;
    align-items: center;
    gap: 40px;
  }}
  .arch-slide .arch-layer-head {{ flex: 0 0 380px; }}
  .arch-slide .arch-layer-name {{ font-size: 28px; font-weight: 700; margin-bottom: 6px; }}
  .arch-slide .arch-layer-desc {{ font-size: 18px; color: {d['text_muted']}; line-height: 1.4; }}
  .arch-slide .arch-layer-items {{
    flex: 1;
    display: flex;
    flex-wrap: wrap;
    gap: 12px;
    align-items: center;
  }}
  .arch-slide .arch-pill {{
    display: inline-flex;
    align-items: center;
    padding: 10px 22px;
    border-radius: 999px;
    font-size: 20px;
    font-weight: 600;
    border: 1.5px solid;
    line-height: 1;
  }}
  .arch-slide .arch-arrow {{
    display: flex;
    justify-content: center;
    align-items: center;
    height: 28px;
    transform: rotate(90deg);
    margin: 0 auto;
  }}
"""
    sub_html = f'<div class="subtitle">{_esc(subtitle)}</div>' if subtitle else ''
    return f"""{_base_head(extra_css, design)}
<body>
  <div class="slide arch-slide">
    <div class="header">
      <div class="accent-bar"></div>
      <h2>{_esc(title)}</h2>
      {sub_html}
    </div>
    <div class="layers">{"".join(layer_blocks)}</div>
  </div>
</body>
</html>"""


# ---------------------------------------------------------------------------
# Layout dispatcher — used by Claude template-picker (server.py side)
# Maps a layout name string → render function. Keeps the LLM prompt and the
# Python dispatch logic in sync via a single source of truth.
# ---------------------------------------------------------------------------
def _tone_color(tone: str, d: dict) -> str:
    """Map a semantic tone name to a design-token color. Unknown → primary."""
    t = (tone or "").strip().lower()
    return {
        "primary": d["primary"], "blue": d["primary"],
        "secondary": d["secondary"], "success": d["secondary"],
        "done": d["secondary"], "green": d["secondary"],
        "accent": d["accent"], "warning": d["accent"], "orange": d["accent"],
        "processing": d["accent"], "ongoing": d["accent"],
        "dark": "#1A2332", "navy": "#1A2332",
        "neutral": d["text_muted"], "muted": d["text_muted"],
    }.get(t, d["primary"])


# ---------------------------------------------------------------------------
# 8. KPI SUMMARY — eyebrow + hero title + a row of metric cards
#    (Genspark-style cover / dashboard summary: big numbers with labels)
# ---------------------------------------------------------------------------
def render_kpi_summary(
    title: str,
    metrics,
    subtitle: str = "",
    eyebrow: str = "",
    footer: str = "",
    design: Optional[dict] = None,
) -> str:
    """Title + a horizontal row of 2-5 metric cards.

    Args:
        title: Slide title. Required.
        metrics: list of {value, label, sublabel?, tone?}. 2-5 recommended.
                 tone ∈ primary|secondary|accent|dark|neutral (colors the value
                 and the card's left border).
        subtitle / eyebrow / footer: optional chrome text.
        design: per-call design tokens (design_tokens_for_profile).
    """
    d = design if isinstance(design, dict) and design else SLIDE_DESIGN
    if not isinstance(metrics, list) or not metrics:
        return ""
    cards = []
    for m in metrics[:5]:
        if not isinstance(m, dict):
            continue
        tone = m.get("tone", "primary")
        color = _tone_color(tone, d)
        value = _esc(str(m.get("value", "")))
        label = _esc(str(m.get("label", "")))
        sub = _esc(str(m.get("sublabel", ""))) if m.get("sublabel") else ""
        sub_html = f'<div class="kpi-sub">{sub}</div>' if sub else ''
        cards.append(
            f'<div class="kpi-card" style="border-top:6px solid {color};">'
            f'<div class="kpi-label">{label}</div>'
            f'<div class="kpi-value" style="color:{color};">{value}</div>'
            f'{sub_html}</div>'
        )
    if not cards:
        return ""
    extra_css = f"""
  .kpis {{ width:1920px; height:1080px; background:{d['bg_light']}; padding:130px 160px; position:relative; display:flex; flex-direction:column; }}
  .kpis .logo {{ position:absolute; top:80px; left:160px; width:44px; height:44px; background:{d['primary']}; border-radius:10px; }}
  .kpis .accent-strip {{ position:absolute; top:0; left:0; width:14px; height:1080px; background:{d['accent']}; }}
  .kpis .eyebrow {{ font-size:24px; letter-spacing:0.22em; text-transform:uppercase; color:{d['primary']}; font-weight:700; margin-bottom:28px; }}
  .kpis h1 {{ font-size:88px; line-height:1.05; color:{d['text_dark']}; max-width:1500px; margin-bottom:28px; font-weight:800; }}
  .kpis .subtitle {{ font-size:34px; color:{d['text_muted']}; line-height:1.4; max-width:1400px; font-weight:300; margin-bottom:64px; }}
  .kpis .cards {{ display:grid; grid-template-columns:repeat({len(cards)},1fr); gap:32px; margin-top:auto; }}
  .kpi-card {{ background:{d['card_bg']}; border-radius:16px; padding:44px 40px; box-shadow:{d['card_shadow']}; min-height:240px; display:flex; flex-direction:column; }}
  .kpi-label {{ font-size:22px; letter-spacing:0.12em; text-transform:uppercase; color:{d['text_muted']}; font-weight:600; margin-bottom:24px; }}
  .kpi-value {{ font-size:84px; line-height:1; font-weight:800; font-family:{d['font_heading']}; }}
  .kpi-sub {{ font-size:24px; color:{d['text_dark']}; margin-top:auto; padding-top:24px; }}
  .kpis .footer {{ position:absolute; bottom:56px; left:160px; font-size:18px; color:{d['text_muted']}; letter-spacing:0.08em; }}
"""
    eyebrow_html = f'<div class="eyebrow">{_esc(eyebrow)}</div>' if eyebrow else ''
    subtitle_html = f'<div class="subtitle">{_esc(subtitle)}</div>' if subtitle else ''
    footer_html = f'<div class="footer">{_esc(footer)}</div>' if footer else ''
    return f"""{_base_head(extra_css, design)}
<body>
  <div class="slide kpis">
    <div class="accent-strip"></div>
    <div class="logo"></div>
    {eyebrow_html}
    <h1>{_esc(title)}</h1>
    {subtitle_html}
    <div class="cards">{''.join(cards)}</div>
    {footer_html}
  </div>
</body>
</html>"""


# ---------------------------------------------------------------------------
# 9. STATUS TABLE — header row + data rows with optional progress bar + badge
#    (Genspark-style OKR / status tracker table)
# ---------------------------------------------------------------------------
def render_status_table(
    title: str,
    columns,
    rows,
    subtitle: str = "",
    footer: str = "",
    design: Optional[dict] = None,
) -> str:
    """A dark-header table where each row can carry a progress bar + status badge.

    Args:
        title: Slide title. Required.
        columns: list[str] header labels (3-6 recommended). The progress bar and
                 status badge are rendered as the LAST two implicit columns when a
                 row supplies `progress` / `status` — do NOT add them to `columns`.
        rows: list of {cells: [str,...], progress?: int(0-100), status?: str,
                       status_tone?: primary|secondary|accent|dark|neutral}.
                 `cells` should align 1:1 with `columns`.
        subtitle / footer: optional chrome.
        design: per-call design tokens.
    """
    d = design if isinstance(design, dict) and design else SLIDE_DESIGN
    if not isinstance(columns, list) or not columns:
        return ""
    if not isinstance(rows, list) or not rows:
        return ""
    cols = [_esc(str(c)) for c in columns][:6]
    n = len(cols)
    has_progress = any(isinstance(r, dict) and r.get("progress") is not None for r in rows)
    has_status = any(isinstance(r, dict) and r.get("status") for r in rows)
    head_cells = "".join(f'<th>{c}</th>' for c in cols)
    if has_progress:
        head_cells += '<th>진척률</th>'
    if has_status:
        head_cells += '<th>상태</th>'
    body = []
    for r in rows:
        if not isinstance(r, dict):
            continue
        cells = r.get("cells") or []
        tds = []
        for i in range(n):
            val = _esc(str(cells[i])) if i < len(cells) else ""
            cls = ' class="lead"' if i == 0 else ''
            tds.append(f'<td{cls}>{val}</td>')
        if has_progress:
            p = r.get("progress")
            if isinstance(p, (int, float)):
                pct = max(0, min(100, int(p)))
                tone = r.get("status_tone") or ("secondary" if pct >= 100 else "accent")
                bar_color = _tone_color(tone, d)
                tds.append(
                    f'<td><div class="bar-wrap"><div class="bar" style="width:{pct}%;background:{bar_color};"></div></div>'
                    f'<div class="bar-pct">{pct}%</div></td>'
                )
            else:
                tds.append('<td></td>')
        if has_status:
            st = r.get("status")
            if st:
                tone = r.get("status_tone") or "primary"
                color = _tone_color(tone, d)
                tds.append(
                    f'<td><span class="badge" style="color:{color};border:2px solid {color};">'
                    f'<span class="dot" style="background:{color};"></span>{_esc(str(st))}</span></td>'
                )
            else:
                tds.append('<td></td>')
        body.append(f'<tr>{"".join(tds)}</tr>')
    if not body:
        return ""
    extra_css = f"""
  .stbl {{ width:1920px; height:1080px; background:{d['bg_light']}; padding:110px 130px; position:relative; }}
  .stbl .logo {{ position:absolute; top:70px; left:130px; width:40px; height:40px; background:{d['primary']}; border-radius:9px; }}
  .stbl .accent-strip {{ position:absolute; top:0; left:0; width:14px; height:1080px; background:{d['accent']}; }}
  .stbl h2 {{ font-size:54px; color:{d['text_dark']}; margin-bottom:14px; font-weight:800; }}
  .stbl .subtitle {{ font-size:26px; color:{d['text_muted']}; margin-bottom:48px; }}
  .stbl table {{ width:100%; border-collapse:separate; border-spacing:0 14px; }}
  .stbl th {{ background:#1A2332; color:#FFFFFF; font-size:22px; font-weight:600; text-align:left; padding:22px 28px; letter-spacing:0.04em; }}
  .stbl th:first-child {{ border-top-left-radius:12px; border-bottom-left-radius:12px; }}
  .stbl th:last-child {{ border-top-right-radius:12px; border-bottom-right-radius:12px; }}
  .stbl td {{ background:{d['card_bg']}; font-size:25px; color:{d['text_dark']}; padding:26px 28px; vertical-align:middle; box-shadow:{d['card_shadow']}; }}
  .stbl td:first-child {{ border-top-left-radius:12px; border-bottom-left-radius:12px; }}
  .stbl td:last-child {{ border-top-right-radius:12px; border-bottom-right-radius:12px; }}
  .stbl td.lead {{ font-weight:700; }}
  .bar-wrap {{ width:100%; min-width:220px; height:14px; background:{d['border']}; border-radius:7px; overflow:hidden; }}
  .bar {{ height:14px; border-radius:7px; }}
  .bar-pct {{ font-size:20px; color:{d['text_muted']}; margin-top:8px; font-weight:600; }}
  .badge {{ display:inline-flex; align-items:center; gap:10px; font-size:22px; font-weight:700; padding:8px 20px; border-radius:24px; white-space:nowrap; }}
  .badge .dot {{ width:12px; height:12px; border-radius:50%; display:inline-block; }}
  .stbl .footer {{ position:absolute; bottom:50px; left:130px; font-size:18px; color:{d['text_muted']}; letter-spacing:0.08em; }}
"""
    subtitle_html = f'<div class="subtitle">{_esc(subtitle)}</div>' if subtitle else ''
    footer_html = f'<div class="footer">{_esc(footer)}</div>' if footer else ''
    return f"""{_base_head(extra_css, design)}
<body>
  <div class="slide stbl">
    <div class="accent-strip"></div>
    <div class="logo"></div>
    <h2>{_esc(title)}</h2>
    {subtitle_html}
    <table>
      <thead><tr>{head_cells}</tr></thead>
      <tbody>{''.join(body)}</tbody>
    </table>
    {footer_html}
  </div>
</body>
</html>"""


# ---------------------------------------------------------------------------
# 10. OBJECTIVE DETAIL — numbered badge header + meta bar + 2-column body
#     (Genspark-style objective deep-dive: number + status badge + KR/weight
#      meta strip + left "deliverables" steps + right "evidence" box)
# ---------------------------------------------------------------------------
def render_objective_detail(
    title: str,
    number=None,
    subtitle: str = "",
    status: str = "",
    status_tone: str = "secondary",
    meta=None,
    blocks=None,
    evidence=None,
    design: Optional[dict] = None,
    image: str = "",
    slot_image: str = "",
) -> str:
    """Deep-dive on a single objective/topic.

    Args:
        title: Objective title. Required.
        number: Optional badge number (e.g. "01" / 1).
        subtitle: One-line description under the title.
        status: Optional status pill text (e.g. "100% DONE").
        status_tone: pill color tone.
        meta: list of {label, value, tone?} for the meta strip
              (e.g. OBJECTIVE 방향 / 가중치 / KR 목표치). 2-3 recommended.
        blocks: list of {title, items:[str,...]} rendered as left-column numbered
                deliverable cards. 1-4 recommended. Optional per-block `caption`
                adds a secondary line under the block header (rendered only when
                present → byte-identical when omitted).
        evidence: {title?, items:[str,...], note?} rendered as the highlighted
                  right-column box (증빙/완료 기준). Optional.
        design: per-call design tokens.
        image: Optional local image path or data: URI composited as a full-bleed
                backdrop behind a light scrim; the opaque blocks/evidence cards
                stay crisp so the dense layout is augmented, not replaced. When
                absent/invalid the HTML is byte-identical to the pre-image output.
    """
    d = design if isinstance(design, dict) and design else SLIDE_DESIGN
    blocks = blocks if isinstance(blocks, list) else []
    meta = meta if isinstance(meta, list) else []
    # require at least some body to be worth this layout
    if not blocks and not (isinstance(evidence, dict) and evidence.get("items")):
        return ""

    badge_html = ""
    if number is not None and str(number).strip():
        badge_html = f'<div class="od-badge">{_esc(str(number))}</div>'
    status_html = ""
    if status:
        sc = _tone_color(status_tone, d)
        status_html = (f'<div class="od-status" style="background:{sc};">'
                       f'<span class="od-dot"></span>{_esc(status)}</div>')
    meta_cells = []
    for m in meta[:3]:
        if not isinstance(m, dict):
            continue
        mc = _tone_color(m.get("tone", "primary"), d)
        meta_cells.append(
            f'<div class="od-meta-cell"><div class="od-meta-label">{_esc(str(m.get("label","")))}</div>'
            f'<div class="od-meta-value" style="color:{mc};">{_esc(str(m.get("value","")))}</div></div>'
        )
    meta_html = f'<div class="od-meta">{"".join(meta_cells)}</div>' if meta_cells else ""

    block_html = []
    _od_density = False  # flips True when an optional block caption renders
    for i, b in enumerate(blocks[:4], 1):
        if not isinstance(b, dict):
            continue
        items = b.get("items") or []
        li = "".join(f'<li>{_esc(str(it))}</li>' for it in items[:6] if str(it).strip())
        # Optional per-block caption — a secondary line under the block header
        # for left/right density balance. Built only when present so the absent
        # case stays byte-identical.
        cap = str(b.get("caption", "")).strip()
        cap_html = f'<div class="od-block-cap">{_esc(cap)}</div>' if cap else ''
        if cap_html:
            _od_density = True
        block_html.append(
            f'<div class="od-block"><div class="od-block-h">'
            f'<span class="od-num">{i}</span>{_esc(str(b.get("title","")))}</div>'
            f'{cap_html}<ul>{li}</ul></div>'
        )
    left_html = f'<div class="od-left">{"".join(block_html)}</div>' if block_html else '<div class="od-left"></div>'

    ev_html = ""
    if isinstance(evidence, dict) and (evidence.get("items") or evidence.get("note")):
        ev_title = _esc(str(evidence.get("title", "증빙 · 완료 기준")))
        ev_items = evidence.get("items") or []
        ev_li = "".join(f'<li>{_esc(str(it))}</li>' for it in ev_items[:8] if str(it).strip())
        note = evidence.get("note")
        note_html = f'<div class="od-ev-note">{_esc(str(note))}</div>' if note else ''
        ev_html = (f'<div class="od-ev"><div class="od-ev-h">{ev_title}</div>'
                   f'<ul>{ev_li}</ul>{note_html}</div>')
    right_html = f'<div class="od-right">{ev_html}</div>'

    # Optional BOUNDED Image_Slot — distinct from the full-bleed `image`
    # backdrop below. Rendered as a capped-height card at the top of the RIGHT
    # column, sized SMALLER than the full slide so it never becomes a full-bleed
    # backdrop covering 13.333in x 7.5in (요구사항 2.3, 2.4). Uses
    # `_safe_image_data_uri` (local path / data: URI only; external refs
    # rejected). Built ONLY when a valid image resolves → when the field is
    # absent/invalid the output is byte-identical to the prior template and
    # callers fall back to the existing gradient/placeholder.
    _slot_uri = _safe_image_data_uri(slot_image)
    slot_css = ""
    if _slot_uri:
        slot_css = f"""
  .od .image-slot {{ border-radius:14px; overflow:hidden; margin-bottom:24px; box-shadow:{d['card_shadow']}; position:relative; z-index:2; }}
  .od .image-slot-img {{ width:100%; height:260px; background-image:url('{_slot_uri}'); background-size:cover; background-position:center; background-repeat:no-repeat; }}
"""
        _slot_html = '<div class="image-slot"><div class="image-slot-img"></div></div>'
        right_html = f'<div class="od-right">{_slot_html}{ev_html}</div>'

    extra_css = f"""
  .od {{ width:1920px; height:1080px; background:{d['bg_light']}; padding:96px 120px 80px; position:relative; }}
  .od .accent-strip {{ position:absolute; top:0; left:0; width:14px; height:1080px; background:{d['accent']}; }}
  .od .head {{ display:flex; align-items:center; gap:28px; margin-bottom:30px; }}
  .od-badge {{ width:84px; height:84px; border-radius:16px; background:{d['primary']}; color:#fff; font-family:{d['font_heading']};
              font-size:42px; font-weight:800; display:flex; align-items:center; justify-content:center; flex:0 0 auto; }}
  .od .head .htext {{ flex:1; }}
  .od .head h2 {{ font-size:50px; color:{d['text_dark']}; font-weight:800; line-height:1.1; }}
  .od .head .sub {{ font-size:25px; color:{d['text_muted']}; margin-top:8px; }}
  .od-status {{ color:#fff; font-size:24px; font-weight:700; padding:14px 30px; border-radius:30px; display:flex; align-items:center; gap:12px; flex:0 0 auto; }}
  .od-dot {{ width:12px; height:12px; border-radius:50%; background:#fff; display:inline-block; }}
  .od-meta {{ display:flex; gap:0; background:{d['bg_section']}; border:1px solid {d['border']}; border-radius:14px; overflow:hidden; margin-bottom:40px; }}
  .od-meta-cell {{ flex:1; padding:26px 34px; border-right:1px solid {d['border']}; }}
  .od-meta-cell:last-child {{ border-right:none; }}
  .od-meta-label {{ font-size:20px; letter-spacing:0.1em; text-transform:uppercase; color:{d['text_muted']}; font-weight:600; margin-bottom:12px; }}
  .od-meta-value {{ font-size:32px; font-weight:800; font-family:{d['font_heading']}; }}
  .od .body {{ display:grid; grid-template-columns:1.25fr 1fr; gap:44px; }}
  .od-block {{ background:{d['card_bg']}; border-radius:14px; box-shadow:{d['card_shadow']}; padding:34px 38px; margin-bottom:26px; }}
  .od-block-h {{ display:flex; align-items:center; gap:16px; font-family:{d['font_heading']}; font-size:30px; font-weight:700; color:{d['text_dark']}; margin-bottom:20px; }}
  .od-num {{ width:42px; height:42px; border-radius:50%; background:{d['primary']}; color:#fff; font-size:24px; font-weight:700; display:inline-flex; align-items:center; justify-content:center; }}
  .od-block ul {{ list-style:none; }}
  .od-block li {{ font-size:24px; color:{d['text_dark']}; line-height:1.5; padding-left:32px; position:relative; margin-bottom:12px; }}
  .od-block li::before {{ content:""; position:absolute; left:6px; top:14px; width:10px; height:10px; border-radius:50%; background:{d['primary']}; }}
  .od-ev {{ background:#EAF2FB; border-left:8px solid {d['primary']}; border-radius:12px; padding:34px 38px; height:100%; }}
  .od-ev-h {{ font-family:{d['font_heading']}; font-size:28px; font-weight:700; color:{d['primary']}; margin-bottom:22px; }}
  .od-ev ul {{ list-style:none; }}
  .od-ev li {{ font-size:23px; color:{d['text_dark']}; line-height:1.5; padding-left:30px; position:relative; margin-bottom:14px; }}
  .od-ev li::before {{ content:"✓"; position:absolute; left:0; top:0; color:{d['secondary']}; font-weight:800; }}
  .od-ev-note {{ margin-top:24px; padding-top:22px; border-top:1px dashed #B7CDE8; font-size:24px; font-weight:700; color:{d['secondary']}; }}
"""
    # Optional density CSS — only when a block caption rendered, so the absent
    # case keeps the <head> byte-identical to the prior template.
    od_density_css = ""
    if _od_density:
        od_density_css = f"""
  .od-block-cap {{ font-size:21px; color:{d['text_muted']}; line-height:1.45; margin:-8px 0 18px 58px; }}
"""
    subtitle_html = f'<div class="sub">{_esc(subtitle)}</div>' if subtitle else ''
    # Optional Vertex image composited as a full-bleed backdrop behind a light
    # scrim. The accent-strip stays above the scrim; opaque blocks/evidence keep
    # the dense layout readable. Built only when a valid image resolves →
    # byte-identical when absent.
    _img_uri = _safe_image_data_uri(image)
    img_css = ""
    img_html = ""
    if _img_uri:
        img_css = f"""
  .od .bg-image {{
    position: absolute;
    inset: 0;
    background-image: url('{_img_uri}');
    background-size: cover;
    background-position: center;
    z-index: 0;
  }}
  .od .bg-scrim {{
    position: absolute;
    inset: 0;
    background: {d['bg_light']};
    opacity: 0.9;
    z-index: 1;
  }}
  .od .accent-strip {{ z-index: 2; }}
  .od .head, .od-meta, .od .body {{ position: relative; z-index: 2; }}
"""
        img_html = '<div class="bg-image"></div><div class="bg-scrim"></div>'
    return f"""{_base_head(extra_css + img_css + slot_css + od_density_css, design)}
<body>
  <div class="slide od">
    {img_html}<div class="accent-strip"></div>
    <div class="head">
      {badge_html}
      <div class="htext"><h2>{_esc(title)}</h2>{subtitle_html}</div>
      {status_html}
    </div>
    {meta_html}
    <div class="body">
      {left_html}
      {right_html}
    </div>
  </div>
</body>
</html>"""


# ---------------------------------------------------------------------------
# 11. PROCESS FLOW — dark band with horizontal step boxes + arrows
#     (Genspark-style "요청 접수 → 원인 분석 → 조치 → 확인 → 기록")
# ---------------------------------------------------------------------------
def render_process_flow(
    title: str,
    steps,
    subtitle: str = "",
    note: str = "",
    design: Optional[dict] = None,
    step_numbers: bool = False,
) -> str:
    """Horizontal process flow on a dark band. 3-6 steps with arrows.

    Args:
        title: Slide title. Required.
        steps: list of {title, caption?, tone?, step_no?} OR list[str]. 3-6
               recommended. Optional per-step `step_no` renders a small numbered
               badge above the step box (built only when present → byte-identical
               when omitted).
        subtitle: optional one-liner under the title.
        note: optional footnote strip under the flow (e.g. 증빙).
        design: per-call design tokens.
        step_numbers: When True, auto-number each step "01 / 02 / …" above its
               box (an explicit per-step `step_no` still wins). Defaults False so
               existing callers stay byte-identical.
    """
    d = design if isinstance(design, dict) and design else SLIDE_DESIGN
    if not isinstance(steps, list) or not steps:
        return ""
    norm = []
    for s in steps[:6]:
        if isinstance(s, str):
            norm.append({"title": s, "caption": "", "tone": "primary", "step_no": ""})
        elif isinstance(s, dict) and str(s.get("title", "")).strip():
            norm.append({"title": str(s.get("title")),
                         "caption": str(s.get("caption", "")),
                         "tone": s.get("tone", "primary"),
                         "step_no": str(s.get("step_no", "")).strip()})
    if not norm:
        return ""
    nodes = []
    _pf_density = False  # flips True when a step number badge renders
    for i, s in enumerate(norm):
        # last step highlighted with accent to signal "outcome"
        tone = s["tone"] if s["tone"] else ("accent" if i == len(norm) - 1 else "primary")
        color = _tone_color(tone, d)
        cap = f'<div class="pf-cap">{_esc(s["caption"])}</div>' if s["caption"] else ''
        # Optional step number badge above the box (explicit step_no wins, else
        # auto when step_numbers=True). Built only when present → byte-identical.
        no = s["step_no"]
        if not no and step_numbers:
            no = f"{i + 1:02d}"
        no_html = f'<div class="pf-no" style="color:{color};">{_esc(no)}</div>' if no else ''
        if no_html:
            _pf_density = True
        nodes.append(
            f'<div class="pf-node">{no_html}<div class="pf-box" style="background:{color};">{_esc(s["title"])}</div>{cap}</div>'
        )
        if i < len(norm) - 1:
            nodes.append('<div class="pf-arrow">&#8594;</div>')
    note_html = f'<div class="pf-note">{_esc(note)}</div>' if note else ''
    extra_css = f"""
  .pf {{ width:1920px; height:1080px; background:{d['bg_light']}; padding:120px 130px; position:relative; display:flex; flex-direction:column; }}
  .pf .accent-strip {{ position:absolute; top:0; left:0; width:14px; height:1080px; background:{d['accent']}; }}
  .pf .logo {{ position:absolute; top:74px; left:130px; width:40px; height:40px; background:{d['primary']}; border-radius:9px; }}
  .pf h2 {{ font-size:54px; color:{d['text_dark']}; font-weight:800; margin-bottom:14px; }}
  .pf .subtitle {{ font-size:27px; color:{d['text_muted']}; margin-bottom:64px; }}
  .pf-band {{ background:#1A2332; border-radius:20px; padding:80px 56px; display:flex; align-items:flex-start; justify-content:center; gap:18px; margin-top:auto; margin-bottom:auto; }}
  .pf-node {{ display:flex; flex-direction:column; align-items:center; max-width:240px; }}
  .pf-box {{ color:#fff; font-family:{d['font_heading']}; font-size:30px; font-weight:700; padding:30px 30px; border-radius:14px; text-align:center; line-height:1.2; box-shadow:0 8px 24px rgba(0,0,0,0.25); }}
  .pf-cap {{ color:rgba(255,255,255,0.7); font-size:21px; margin-top:18px; text-align:center; line-height:1.35; }}
  .pf-arrow {{ color:{d['accent']}; font-size:54px; font-weight:700; align-self:center; padding:0 4px; }}
  .pf-note {{ margin-top:54px; background:#FFF6E9; border-left:6px solid {d['accent']}; border-radius:10px; padding:24px 32px; font-size:23px; color:{d['text_dark']}; }}
"""
    # Optional density CSS — only when a step number badge rendered, so the
    # absent case keeps the <head> byte-identical to the prior template.
    pf_density_css = ""
    if _pf_density:
        pf_density_css = (
            "\n  .pf-no { font-size:22px; font-weight:800; letter-spacing:0.14em;"
            " margin-bottom:14px; background:#fff; width:54px; height:54px;"
            " border-radius:50%; display:flex; align-items:center;"
            " justify-content:center; box-shadow:0 6px 18px rgba(0,0,0,0.18); }\n"
        )
    subtitle_html = f'<div class="subtitle">{_esc(subtitle)}</div>' if subtitle else ''
    return f"""{_base_head(extra_css + pf_density_css, design)}
<body>
  <div class="slide pf">
    <div class="accent-strip"></div>
    <div class="logo"></div>
    <h2>{_esc(title)}</h2>
    {subtitle_html}
    <div class="pf-band">{''.join(nodes)}</div>
    {note_html}
  </div>
</body>
</html>"""


LAYOUT_REGISTRY = {
    "cover": render_cover_slide,
    "section_divider": render_section_divider,
    "two_column": render_two_column,
    "feature_grid": render_feature_grid,
    "timeline": render_timeline,
    "comparison": render_comparison,
    "architecture": render_architecture,
    "kpi_summary": render_kpi_summary,
    "status_table": render_status_table,
    "objective_detail": render_objective_detail,
    "process_flow": render_process_flow,
}


def render_layout(layout: str, data: Dict[str, Any]) -> str:
    """Dispatch by layout name. Returns HTML string, or empty string if the
    layout is unknown or required fields are missing.

    `data` shapes (each layout's required fields):
      cover          : {title, subtitle?, eyebrow?, footer?, accent_color?}
      section_divider: {title, section_number?, description?}
      two_column     : {title, left_content, right_content, subtitle?}
      feature_grid   : {title, features:[{icon,title,description}], subtitle?}
      timeline       : {title, steps:[{label,title,description}], subtitle?, orientation?}
      comparison     : {title, left_label, left_items, right_label, right_items,
                        subtitle?, left_tone?, right_tone?}
      architecture   : {title, layers:[{name,description,items}], subtitle?}
    """
    fn = LAYOUT_REGISTRY.get((layout or "").strip())
    if not fn:
        return ""
    if not isinstance(data, dict):
        return ""
    try:
        # Cover/divider only need a title; everything else expects more.
        if "title" not in data and layout != "section_divider":
            return ""
        return fn(**data)
    except TypeError as e:
        # Caller passed extra/missing kwargs — log and return empty so caller
        # can fall back to mermaid/matplotlib.
        print(f"[slide_templates] render_layout({layout}) TypeError: {e}")
        return ""
    except Exception as e:
        print(f"[slide_templates] render_layout({layout}) failed: {e}")
        return ""
