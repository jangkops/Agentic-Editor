"""Figure_Slot property-based tests for ai_engine/slide_templates.py.

Covers the pptx-design-density-parity Figure_Slot behaviour (요구사항 3, 6.5)
rendered through `render_two_column`'s `left_figures` / `right_figures` density
fields. Two design Correctness Properties are exercised here:

  Property 9  — image-reference inline round-trip AND external-reference
                rejection (요구사항 3.4, 3.5): a local file path or a
                `data:image/` URI is inlined as a data URI in the output, while
                an `http://`/`https://`/protocol-relative `//`/`file://`
                reference is NOT inlined (the image is omitted) yet the caption,
                the other (non-figure) density slots, and the slide itself still
                render normally.

  Property 10 — full-bleed background image upper bound (요구사항 6.5): a
                two_column slide carrying Figure_Slot density elements has a
                full-bleed background-image marker count of 0–1, because figure
                images are card-scoped `figure-img` divs, never a full-bleed
                background.

Everything is hermetic — pure Python, NO network, NO Electron, NO gateway. A
real temporary PNG file is created on disk for the local-path case.

Run (hermetic):
  ./venv/bin/python -m pytest scripts/test_figure_slot_pbt.py -p no:cacheprovider -q
"""
from __future__ import annotations

import atexit
import base64
import os
import sys
import tempfile

# Make ai_engine importable from repo root.
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "ai_engine"))

import pytest  # noqa: E402
from hypothesis import given, settings, strategies as st, HealthCheck  # noqa: E402

import ai_engine.slide_templates as m  # noqa: E402


# ---------------------------------------------------------------------------
# A real, minimal 1x1 PNG written to a temp file for the local-path case.
# `_safe_image_data_uri` reads the bytes off disk and inlines them as a data
# URI, so an honest on-disk file is required (not a fake path).
# ---------------------------------------------------------------------------
_PNG_1x1 = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR4"
    "2mNk+M9QDwADhgGAWjR9awAAAABJRU5ErkJggg=="
)
_TMPDIR = tempfile.mkdtemp(prefix="figslot_pbt_")
_LOCAL_PNG = os.path.join(_TMPDIR, "fig.png")
with open(_LOCAL_PNG, "wb") as _fh:
    _fh.write(_PNG_1x1)

_DATA_URI = "data:image/png;base64," + base64.b64encode(_PNG_1x1).decode("ascii")


@atexit.register
def _cleanup() -> None:  # pragma: no cover - best-effort temp cleanup
    try:
        os.remove(_LOCAL_PNG)
    except OSError:
        pass
    try:
        os.rmdir(_TMPDIR)
    except OSError:
        pass


# Reference kinds → the raw `image` value handed to a Figure_Slot.
_REFS = {
    "local": _LOCAL_PNG,
    "data": _DATA_URI,
    "http": "http://example.com/screenshot.png",
    "https": "https://example.com/screenshot.png",
    "protorel": "//example.com/screenshot.png",
    "file": "file:///private/tmp/screenshot.png",
}
_INLINE_KINDS = {"local", "data"}        # → inlined as data URI
_EXTERNAL_KINDS = {"http", "https", "protorel", "file"}  # → rejected/omitted


def _assert_valid_html(html: str) -> None:
    assert isinstance(html, str) and html
    assert "<html" in html and "<body" in html and "</html>" in html


# Text alphabet: drop surrogates and NUL so the rendered HTML stays well-formed.
_text = st.text(
    alphabet=st.characters(blacklist_categories=("Cs",), blacklist_characters="\x00"),
    min_size=0, max_size=20,
)


# ===========================================================================
# Feature: pptx-design-density-parity, Property 9: 이미지 참조 인라인 라운드트립과
# 외부 거부 — 로컬 파일 경로 또는 data:image/ URI는 인라인 data URI로 임베드되고,
# http(s)://·//·file:// 외부 참조는 임베드되지 않으며(이미지 생략), 어느 경우든
# 캡션·나머지 슬롯·슬라이드는 정상적으로 렌더된다.
# Validates: Requirements 3.4, 3.5
# ===========================================================================
@settings(max_examples=120, deadline=None,
          suppress_health_check=[HealthCheck.too_slow])
@given(
    cap_text=_text,
    kind=st.sampled_from(sorted(_REFS)),
    side=st.sampled_from(["left", "right"]),
)
def test_property9_image_inline_roundtrip_and_external_rejection(cap_text, kind, side):
    caption = "캡션-" + cap_text  # always non-empty after strip → slot always renders
    ref = _REFS[kind]
    figures = [{"image": ref, "caption": caption}]
    # Put a non-figure density slot on the SAME column to prove other slots and
    # the slide still render even when the figure image is rejected.
    kwargs = {f"{side}_figures": figures, f"{side}_numbered": ["다른-슬롯"]}

    html = m.render_two_column(
        title="피규어 슬롯 검증", left_content="- 좌측", right_content="- 우측",
        **kwargs,
    )

    _assert_valid_html(html)
    # Caption + figure slot + the OTHER density slot + slide all render
    # regardless of whether the image reference was accepted.
    assert 'class="figure-slot"' in html
    assert 'class="figure-caption"' in html
    assert 'class="numbered-item"' in html

    if kind in _INLINE_KINDS:
        # Local path / data: URI → inlined as a data URI inside a figure-img.
        assert 'class="figure-img"' in html
        assert "data:image/" in html
    else:
        # External reference → image omitted; no inlined figure image and the
        # raw external URL never leaks into the self-contained HTML.
        assert 'class="figure-img"' not in html
        assert ref not in html


# ===========================================================================
# Feature: pptx-design-density-parity, Property 10: 풀블리드 배경 이미지 개수 상한
# — 밀도 요소(Figure_Slot)를 포함한 two_column 슬라이드의 풀블리드 배경 이미지
# 마커(`class="bg-image"`) 수는 0 이상 1 이하이며, figure 이미지는 카드 스코프
# `figure-img` div 일 뿐 풀블리드 배경이 아니다.
# Validates: Requirements 6.5
# ===========================================================================
@settings(max_examples=120, deadline=None,
          suppress_health_check=[HealthCheck.too_slow])
@given(
    n_figs=st.integers(min_value=1, max_value=6),
    cap_text=_text,
    img_kind=st.sampled_from(["none", "local", "external"]),
)
def test_property10_full_bleed_background_count_0_or_1(n_figs, cap_text, img_kind):
    figures = [{"image": _LOCAL_PNG, "caption": "캡션-" + cap_text}
               for _ in range(n_figs)]
    image_param = {
        "none": "",
        "local": _LOCAL_PNG,
        "external": "https://example.com/bg.png",
    }[img_kind]

    html = m.render_two_column(
        title="배경 상한 검증", left_content="- 좌측", right_content="- 우측",
        image=image_param, left_figures=figures,
    )

    _assert_valid_html(html)

    # Full-bleed background-image marker count must stay within 0..1.
    bg_count = html.count('class="bg-image"')
    assert 0 <= bg_count <= 1

    # Figure images are card-scoped figure-img divs (one per clamped card),
    # never contributing to the full-bleed background count.
    assert 'class="figure-img"' in html
    assert html.count('class="figure-img"') == n_figs

    if img_kind == "local":
        assert bg_count == 1  # a valid local full-bleed backdrop resolves to 1
    else:
        assert bg_count == 0  # absent / external backdrop → none


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(pytest.main([__file__, "-q", "-p", "no:cacheprovider"]))
