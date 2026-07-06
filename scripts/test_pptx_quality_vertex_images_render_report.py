"""Hermetic test — spec: pptx-quality-vertex-images (follow-on enhancement).

Verifies the additive ``renderReport`` object that
``ai_engine/server.py:_tool_generate_pptx`` now attaches to its JSON result so a
caller can observe WHICH render path each slide actually took (Genspark-style
HTML full-bleed, Vertex Nano-Banana-Pro image, native editable shapes, ...) and
WHY a path was disabled.

The enhancement is purely observational: it adds a single new ``renderReport``
key and must not change control flow or the produced pptx bytes.

Assertions (per the enhancement spec):
  (1) ``renderReport`` is present with all required top-level + per-slide keys;
  (2) ``vertexUnused == 0`` always (loss-zero invariant);
  (3) when HTML is mock-available AND Vertex is mock-enabled, the report marks
      ``htmlEnabled`` true and at least one slide path is ``html-fullbleed`` or a
      ``vertex-*`` path;
  (4) when BOTH HTML and Vertex are disabled, the disabled reasons are
      non-empty and every slide path is a native/text path.

Everything is hermetic — no network. The Bedrock gateway, the Vertex client
(``get_vertex_image_client`` / ``generate``), the HTML→PNG renderer and
``_tool_generate_image`` are all mocked. Fakes mirror the existing
``test_pptx_quality_vertex_images_integration.py``.

Run (hermetic):
  ./venv/bin/python -m pytest scripts/test_pptx_quality_vertex_images_render_report.py -p no:cacheprovider -q
"""
from __future__ import annotations

import io
import os
import sys
import json
import base64
import asyncio
import tempfile
from unittest.mock import patch

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import pytest  # noqa: E402
from PIL import Image  # noqa: E402

import ai_engine.server as srv  # noqa: E402
import ai_engine.vertex_image_module as vim  # noqa: E402


# --------------------------------------------------------------------------
# Hermetic fakes
# --------------------------------------------------------------------------
def _make_png(tag: int) -> bytes:
    img = Image.new(
        "RGB",
        (40 + (tag % 11), 30 + (tag % 7)),
        (tag % 256, (tag * 7) % 256, (tag * 31) % 256),
    )
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()


class _FakeVertexClient:
    """Stand-in for VertexImageClient — always 'enabled', returns unique PNGs."""

    def __init__(self) -> None:
        self.enabled = True
        self.calls = 0

    async def generate(self, prompt=None, model_class=None, aspect_ratio=None,
                       negative_prompt=None, timeout=None, **_kw):
        self.calls += 1
        raw = _make_png(2000 + self.calls)
        return {"images": [base64.b64encode(raw).decode("ascii")]}


class _DisabledVertexClient:
    """Stand-in for a Vertex client that is not enabled (no key resolved)."""

    def __init__(self) -> None:
        self.enabled = False

    async def generate(self, *_a, **_k):  # pragma: no cover - never called
        raise AssertionError("disabled Vertex client must not be called")


async def _img_gen_disabled(*_a, **_k):
    """Stand-in for _tool_generate_image — never returns a path (no network)."""
    return json.dumps({"error": "test-disabled"})


async def _render_html_png_fake(html, output_path, width=1920, height=1080, timeout=30, **_k):
    os.makedirs(os.path.dirname(output_path), exist_ok=True)
    with open(output_path, "wb") as f:
        f.write(_make_png(700_000))
    return {"ok": True}


def _make_compositing_section_fake(project_path: str):
    """Section-HTML fake that COMPOSITES the Vertex hero (task 3.5)."""
    state = {"n": 0}

    async def _section_fake(gw, model_id, heading, body, doc_context,
                            proj_path, style_profile=None,
                            hero_image="", render_info=None):
        state["n"] += 1
        gen = os.path.join(project_path, ".generated")
        os.makedirs(gen, exist_ok=True)
        name = f"slide-html-{state['n']}.png"
        out = os.path.join(gen, name)
        if hero_image:
            hero_abs = hero_image if os.path.isabs(hero_image) \
                else os.path.join(project_path, hero_image)
            with open(hero_abs, "rb") as hf:
                data = hf.read()
            with open(out, "wb") as f:
                f.write(data)
            if isinstance(render_info, dict):
                render_info["layout"] = "two_column"
                render_info["composited"] = True
        else:
            with open(out, "wb") as f:
                f.write(_make_png(600_000 + state["n"]))
            if isinstance(render_info, dict):
                render_info["layout"] = "feature_grid"
                render_info["composited"] = False
        return f".generated/{name}"

    return _section_fake


# --------------------------------------------------------------------------
# Curated mixed deck (classification is deterministic).
# --------------------------------------------------------------------------
_STRUCTURAL = {"title": "업무 처리 프로세스", "bullets": ["접수", "검토", "승인", "완료"]}
_CONTENT = {"title": "핵심 기능 요약",
            "bullets": ["빠른 처리", "안정적 운영", "유연한 확장", "강력한 보안",
                        "비용 절감", "쉬운 사용성"]}
_VISUAL = {"title": "회사 소개",
           "bullets": ["신뢰를 최우선으로"],
           "imagePrompt": "a high quality professional photograph of a modern "
                          "corporate office, natural light, wide angle"}

_REQUIRED_TOP_KEYS = {
    "htmlEnabled", "htmlRenderer", "htmlDisabledReason",
    "vertexEnabled", "vertexDisabledReason",
    "slideCount", "slides",
    "vertexGenerated", "vertexEmbedded", "vertexUnused",
}
_REQUIRED_SLIDE_KEYS = {"index", "role", "path", "vertexEmbedded"}
_VALID_PATHS = {
    "html-fullbleed", "vertex-visual", "vertex-hero",
    "native-backdrop", "native-shapes", "caller-image", "text-only",
}
_NATIVE_OR_TEXT = {"native-backdrop", "native-shapes", "caller-image", "text-only"}


def _deck():
    return {"title": "렌더 리포트 검증 덱",
            "slides": [dict(_STRUCTURAL), dict(_CONTENT), dict(_VISUAL)]}


def _assert_report_shape(report):
    """(1) renderReport present with all required keys + valid shapes."""
    assert isinstance(report, dict), "renderReport must be a dict"
    missing = _REQUIRED_TOP_KEYS - set(report.keys())
    assert not missing, f"renderReport missing top-level keys: {missing}"
    assert isinstance(report["slides"], list) and report["slides"], \
        "renderReport.slides must be a non-empty list"
    # slideCount must match the slides list length.
    assert report["slideCount"] == len(report["slides"]), (
        f"slideCount {report['slideCount']} != len(slides) {len(report['slides'])}")
    for sl in report["slides"]:
        smissing = _REQUIRED_SLIDE_KEYS - set(sl.keys())
        assert not smissing, f"slide entry missing keys: {smissing} ({sl})"
        assert sl["path"] in _VALID_PATHS, f"unknown slide path: {sl['path']!r}"
        assert isinstance(sl["vertexEmbedded"], bool)
        assert isinstance(sl["index"], int)
    # Types of the counters.
    for k in ("vertexGenerated", "vertexEmbedded", "vertexUnused"):
        assert isinstance(report[k], int), f"{k} must be int"


def _assert_loss_zero(report):
    """(2) vertexUnused == 0 always (loss-zero invariant)."""
    assert report["vertexUnused"] == 0, (
        f"loss-zero 위반: vertexUnused={report['vertexUnused']} "
        f"(generated={report['vertexGenerated']}, embedded={report['vertexEmbedded']})")
    # Consistency: embedded slides counted == reported vertexEmbedded, and
    # generated >= embedded.
    embedded_in_slides = sum(1 for s in report["slides"] if s["vertexEmbedded"])
    assert embedded_in_slides == report["vertexEmbedded"], (
        f"per-slide vertexEmbedded count {embedded_in_slides} != "
        f"reported {report['vertexEmbedded']}")
    assert report["vertexGenerated"] >= report["vertexEmbedded"]


def _run(deck, env, *, mocks):
    proj = tempfile.mkdtemp()
    env = dict(env)
    env.setdefault("AE_GENERATED_ROOT", proj)
    cms = [patch.dict(os.environ, env, clear=False)] + mocks(proj)
    from contextlib import ExitStack
    with ExitStack() as stack:
        for cm in cms:
            stack.enter_context(cm)
        raw = asyncio.run(srv._tool_generate_pptx(deck, project_path=proj))
    result = json.loads(raw)
    return result


# ==========================================================================
# Scenario A — BOTH ENABLED (HTML available + Vertex enabled)
# ==========================================================================
def test_render_report_html_on_vertex_on():
    fake = _FakeVertexClient()

    def _mocks(proj):
        return [
            patch.object(vim, "get_vertex_image_client", lambda **_k: fake),
            patch.object(srv, "_call_bridge", lambda *a, **k: {"ok": True}),
            patch.object(srv, "_get_gw", lambda *a, **k: object()),
            patch.object(srv, "_specialized_model_for_task", lambda *a, **k: "dummy-model"),
            patch.object(srv, "_render_html_slide_to_png", _render_html_png_fake),
            patch.object(srv, "_generate_html_slide_for_section",
                         _make_compositing_section_fake(proj)),
            patch.object(srv, "_tool_generate_image", _img_gen_disabled),
        ]

    env = {
        "AE_ENABLE_HTML_SLIDES": "1",
        "AE_DISABLE_HTML_SLIDES": "0",
        "AE_PREFER_VERTEX_IMAGE": "1",
        "AE_PPTX_TOC": "0",
        "AE_ENABLE_VERTEX_BG": "0",
        "AE_DISABLE_NATIVE_COVER": "1",
    }
    result = _run(_deck(), env, mocks=_mocks)
    assert "absPath" in result, f"pptx generation failed: {result}"

    # (1) present with all keys.
    assert "renderReport" in result, "renderReport key must always be present"
    report = result["renderReport"]
    _assert_report_shape(report)

    # Existing keys must be preserved (additive — nothing removed/renamed).
    for k in ("path", "absPath", "model", "slideCount", "sizeBytes"):
        assert k in result, f"existing result key {k!r} must be preserved"

    # (3) HTML enabled + at least one html-fullbleed / vertex-* slide.
    assert report["htmlEnabled"] is True, "htmlEnabled should be True when HTML available"
    assert report["htmlRenderer"] in ("bridge", "local-chrome"), report["htmlRenderer"]
    assert report["htmlDisabledReason"] == ""
    assert report["vertexEnabled"] is True, "vertexEnabled should be True"
    assert report["vertexDisabledReason"] == ""
    assert report["vertexGenerated"] >= 2, (
        f"non-structural slides should generate Vertex images "
        f"(generated={report['vertexGenerated']})")
    rendered = {s["path"] for s in report["slides"]}
    assert any(p == "html-fullbleed" or p.startswith("vertex-") for p in rendered), (
        f"expected a html-fullbleed or vertex-* slide path; got {rendered}")

    # (2) loss-zero.
    _assert_loss_zero(report)

    # Structural slide stays native (not html-fullbleed driven by a vertex image):
    # find the structural-role entry and assert it did not embed a vertex image.
    struct = [s for s in report["slides"] if s["role"] == "structural"]
    assert struct, "structural slide should be classified in the report"
    assert all(not s["vertexEmbedded"] for s in struct), (
        "structural slides must not embed a Vertex raster (editable shapes)")


# ==========================================================================
# Scenario B — BOTH DISABLED (HTML off + Vertex off)
# ==========================================================================
def test_render_report_html_off_vertex_off():
    disabled = _DisabledVertexClient()

    def _mocks(_proj):
        return [
            patch.object(vim, "get_vertex_image_client", lambda **_k: disabled),
            patch.object(srv, "_call_bridge", lambda *a, **k: None),
            patch.object(srv, "_find_local_chrome", lambda: ""),
            patch.object(srv, "_tool_generate_image", _img_gen_disabled),
        ]

    env = {
        "AE_ENABLE_HTML_SLIDES": "0",        # opt out of HTML
        "AE_PREFER_VERTEX_IMAGE": "0",       # Vertex disabled
        "AE_PREFER_EDITABLE_DIAGRAM": "0",   # deterministic: no gateway structuring
        "AE_DISABLE_NATIVE_DIAGRAM": "0",
        "AE_PPTX_TOC": "0",
        "AE_ENABLE_VERTEX_BG": "0",
    }
    result = _run(_deck(), env, mocks=_mocks)
    assert "absPath" in result, f"pptx generation failed: {result}"

    assert "renderReport" in result
    report = result["renderReport"]
    _assert_report_shape(report)

    # (4) both disabled → reasons non-empty.
    assert report["htmlEnabled"] is False
    assert report["htmlRenderer"] == ""
    assert report["htmlDisabledReason"], "htmlDisabledReason must be non-empty when disabled"
    assert report["vertexEnabled"] is False
    assert report["vertexDisabledReason"], "vertexDisabledReason must be non-empty when disabled"
    assert report["vertexDisabledReason"] == "AE_PREFER_VERTEX_IMAGE=0", \
        report["vertexDisabledReason"]

    # (4) every slide path is a native/text path (no html-fullbleed, no vertex-*).
    for s in report["slides"]:
        assert s["path"] in _NATIVE_OR_TEXT, (
            f"with both disabled, slide path must be native/text — got {s['path']!r}")
        assert s["vertexEmbedded"] is False

    # (2) loss-zero (no Vertex generated → unused 0).
    assert report["vertexGenerated"] == 0
    _assert_loss_zero(report)


# ==========================================================================
# Scenario C — HTML off, Vertex ON: vertex images embedded, loss-zero holds,
# and at least one vertex-* path appears (the report names the Vertex path).
# ==========================================================================
def test_render_report_html_off_vertex_on_paths():
    fake = _FakeVertexClient()

    def _mocks(_proj):
        return [
            patch.object(vim, "get_vertex_image_client", lambda **_k: fake),
            patch.object(srv, "_call_bridge", lambda *a, **k: None),
            patch.object(srv, "_find_local_chrome", lambda: ""),
            patch.object(srv, "_tool_generate_image", _img_gen_disabled),
        ]

    env = {
        "AE_ENABLE_HTML_SLIDES": "0",
        "AE_PREFER_VERTEX_IMAGE": "1",
        "AE_PREFER_EDITABLE_DIAGRAM": "0",
        "AE_DISABLE_NATIVE_DIAGRAM": "0",
        "AE_PPTX_TOC": "0",
        "AE_ENABLE_VERTEX_BG": "0",
    }
    result = _run(_deck(), env, mocks=_mocks)
    assert "absPath" in result, f"pptx generation failed: {result}"
    report = result["renderReport"]
    _assert_report_shape(report)

    assert report["htmlEnabled"] is False
    assert report["htmlDisabledReason"], "htmlDisabledReason must be set when HTML off"
    assert report["vertexEnabled"] is True
    assert report["vertexDisabledReason"] == ""
    assert report["vertexGenerated"] >= 2

    # Every generated Vertex image is accounted for as embedded (loss-zero).
    _assert_loss_zero(report)
    assert report["vertexEmbedded"] == report["vertexGenerated"]

    # At least one slide is named as a Vertex path (visual or backdrop hero).
    rendered = {s["path"] for s in report["slides"]}
    assert any(p.startswith("vertex-") or p == "native-backdrop" for p in rendered), (
        f"expected a vertex-* or native-backdrop path; got {rendered}")


# ==========================================================================
# Optional property-based check (hypothesis optional) — loss-zero across the
# enable/disable matrix. Skipped gracefully if hypothesis is unavailable.
# ==========================================================================
try:
    from hypothesis import given, settings, HealthCheck, strategies as st
    _HAS_HYPOTHESIS = True
except Exception:  # pragma: no cover
    _HAS_HYPOTHESIS = False


@pytest.mark.skipif(not _HAS_HYPOTHESIS, reason="hypothesis not installed")
@settings(max_examples=12, deadline=None,
          suppress_health_check=[HealthCheck.function_scoped_fixture] if _HAS_HYPOTHESIS else [])
@given(html_on=st.booleans(), vertex_on=st.booleans())
def test_render_report_loss_zero_property(html_on, vertex_on):
    """Property: regardless of the HTML/Vertex enable matrix, renderReport is
    always present with all keys and vertexUnused is always 0."""
    fake = _FakeVertexClient() if vertex_on else _DisabledVertexClient()

    def _mocks(proj):
        ms = [
            patch.object(vim, "get_vertex_image_client", lambda **_k: fake),
            patch.object(srv, "_tool_generate_image", _img_gen_disabled),
        ]
        if html_on:
            ms += [
                patch.object(srv, "_call_bridge", lambda *a, **k: {"ok": True}),
                patch.object(srv, "_get_gw", lambda *a, **k: object()),
                patch.object(srv, "_specialized_model_for_task", lambda *a, **k: "m"),
                patch.object(srv, "_render_html_slide_to_png", _render_html_png_fake),
                patch.object(srv, "_generate_html_slide_for_section",
                             _make_compositing_section_fake(proj)),
            ]
        else:
            ms += [
                patch.object(srv, "_call_bridge", lambda *a, **k: None),
                patch.object(srv, "_find_local_chrome", lambda: ""),
            ]
        return ms

    env = {
        "AE_ENABLE_HTML_SLIDES": "1" if html_on else "0",
        "AE_DISABLE_HTML_SLIDES": "0",
        "AE_PREFER_VERTEX_IMAGE": "1" if vertex_on else "0",
        "AE_PREFER_EDITABLE_DIAGRAM": "0",
        "AE_DISABLE_NATIVE_DIAGRAM": "0",
        "AE_PPTX_TOC": "0",
        "AE_ENABLE_VERTEX_BG": "0",
        "AE_DISABLE_NATIVE_COVER": "1",
    }
    result = _run(_deck(), env, mocks=_mocks)
    assert "renderReport" in result, f"renderReport missing: {result.get('error')}"
    report = result["renderReport"]
    _assert_report_shape(report)
    _assert_loss_zero(report)
    assert report["htmlEnabled"] is bool(html_on)


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-p", "no:cacheprovider", "-q"]))
