"""Fix-checking property tests (F1–F4) — spec: media-output-quality (bugfix), Task 7.

These property-based tests validate that the fix (tasks 2–6, already present in
``ai_engine/server.py``) satisfies the expected behaviour across the input domain.
They MUST PASS on the current (fixed) code.

  F1 (Property 1, Req 2.1): visual-intent PPTX/PDF + HEALTHY mocked gateway
     returning a valid PNG for a Bedrock image model id → at least one embedded
     image originates from a Bedrock model id
     (``stability.*`` / ``amazon.titan-image-generator-v2:0`` /
     ``amazon.nova-canvas-v1:0``).
  F2 (Property 3, Req 2.2): text with ZERO path tokens / arrow chains / markdown
     table rows (generic keywords allowed) → ``_looks_structural`` returns False.
  F3 (Property 5, Req 3.1): text with at least one structural signal →
     ``_looks_structural`` returns True (regression sentinel pairing F2).
  F4 (Property 4, Req 2.4): GET /api/debug/image-gen-status in healthy AND broken
     circuit states → HTTP 200 with the 5-key JSON schema.

SEAM NOTE for F1 (justification — see task 3 fallback clause):
``_force_generate_from_text`` does NOT surface the embedded image's ``model``
meta in its return value (the returned tuples carry the *document* builder's
model, e.g. "python-pptx"; the per-image ``.meta.json`` sidecars are written by
the agent loop, NOT by force-generate). The image that gets embedded for a
visual section comes from ``_tool_generate_image``, whose returned payload
carries ``model`` = the actual Bedrock model id that produced the PNG.

So F1 drives the REAL ``_force_generate_from_text`` routing (with the Bedrock
slide-image tier enabled and Vertex/HTML/mermaid disabled so the deterministic
Bedrock path is exercised), and captures the ``model`` of every image
``_force_generate_from_text`` actually embeds by wrapping ``_tool_generate_image``
— the exact seam force-generate delegates to. The assertion is NOT a tautology:
if routing fell through to the native/matplotlib path, ``_tool_generate_image``
would never be invoked and nothing would be captured. This validates Req 2.1's
intent (healthy gateway → a real Bedrock image model is invoked and its id is the
source of the embedded image) at the correct, observable seam.

Run (hermetic — no network, gateway mocked):
  ./venv/bin/python -m pytest scripts/test_media_output_quality_fix_pbt.py -p no:cacheprovider -q

_Requirements: 2.1, 2.2, 2.4, 3.1_
"""
from __future__ import annotations

import os
import io
import re
import sys
import json
import time
import base64
import asyncio
import tempfile
import random
from unittest.mock import patch

# Make ai_engine importable from repo root.
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import pytest  # noqa: E402
from hypothesis import given, settings, strategies as st, assume, HealthCheck  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

import ai_engine.server as srv  # noqa: E402
from ai_engine.server import (  # noqa: E402
    app,
    _looks_structural,
    _IMAGE_GEN_CIRCUIT,
    IMAGE_MODELS,
)

# Bedrock image model ids the fix must route to when the gateway is healthy.
_BEDROCK_IMAGE_IDS = set(IMAGE_MODELS)


def _is_bedrock_image_model(model_id: str) -> bool:
    if not model_id:
        return False
    return (
        model_id.startswith("stability.")
        or model_id == "amazon.titan-image-generator-v2:0"
        or model_id == "amazon.nova-canvas-v1:0"
        or model_id in _BEDROCK_IMAGE_IDS
    )


# --------------------------------------------------------------------------
# Shared fixtures for F1 — a valid, high-entropy PNG and a healthy fake gateway
# --------------------------------------------------------------------------
def _make_valid_png_b64() -> str:
    """Return base64 PNG > 5KB with real entropy so _save_and_score accepts it."""
    from PIL import Image
    rng = random.Random(1234)
    img = Image.new("RGB", (256, 256))
    img.putdata([
        (rng.randint(0, 255), rng.randint(0, 255), rng.randint(0, 255))
        for _ in range(256 * 256)
    ])
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return base64.b64encode(buf.getvalue()).decode("ascii")


_VALID_PNG_B64 = _make_valid_png_b64()


class _HealthyFakeGW:
    """Gateway stub that always returns a valid PNG for any model id."""

    def __init__(self, png_b64: str):
        self._png = png_b64
        self.calls: list[str] = []

    async def invoke_model(self, model_id, body, timeout=60):
        self.calls.append(model_id)
        return {"images": [self._png]}


class _DisabledVertexClient:
    enabled = False
    _project_id = None


async def _async_none(*args, **kwargs):
    return None


# Environment that forces the deterministic Bedrock slide-image tier and
# disables every higher tier (HTML bridge, Vertex, mermaid) so the Bedrock
# image path is exercised hermetically.
_FORCE_BEDROCK_ENV = {
    "AE_ENABLE_BEDROCK_SLIDE_IMAGES": "1",   # turn ON Bedrock tier 0.5
    "AE_PREFER_EDITABLE_DIAGRAM": "0",       # let PPTX/PDF embed the image file
    "AE_DISABLE_MERMAID": "1",               # no mermaid (would call LLM)
    "AE_ENABLE_HTML_SLIDES": "0",            # no HTML full-bleed tier
    "AE_DISABLE_HTML_SLIDES": "1",           # legacy disable too
    "AE_PREFER_VERTEX_IMAGE": "0",           # _tool_generate_image: skip vertex-first
    "AE_DISABLE_VERTEX_IMAGE": "1",
    "AE_IMAGE_QUALITY_THRESHOLD": "0",       # no quality-retry recursion
    "AE_BEDROCK_HERO_IMAGE": "",             # hero tier off
}

# Visual-intent keywords that trigger _detect_visual_intent but contain NO
# structural signal (no '/', no '->'/'→'/'⇒', no '|...|').
_VISUAL_KEYWORDS = [
    "다이어그램", "아키텍처", "시스템", "구성도", "개요", "발표 슬라이드",
    "diagram", "architecture", "system overview", "presentation",
]
_SAFE_FILLERS = [
    "이번 분기 보고", "프로젝트 구조 정리", "핵심 내용 요약",
    "quarterly summary", "design highlights", "key points",
]


@settings(
    max_examples=12,
    deadline=None,
    suppress_health_check=[HealthCheck.too_slow, HealthCheck.function_scoped_fixture],
)
@given(
    keyword=st.sampled_from(_VISUAL_KEYWORDS),
    filler=st.sampled_from(_SAFE_FILLERS),
    ext=st.sampled_from(["pptx", "pdf"]),
)
def test_f1_visual_intent_routes_to_bedrock_image_model(keyword, filler, ext):
    title = f"{keyword} 자료"
    description = f"{keyword} 를 만들어줘"
    body = f"{filler}. {keyword} 내용 설명. 추가 설명 문장."

    # Sanity: the input must be visual-intent yet NON-structural so the Bedrock
    # path (not native/matplotlib) is the one under test.
    assume(not _looks_structural(description, title, body))

    gw = _HealthyFakeGW(_VALID_PNG_B64)
    captured_models: list[str] = []

    real_tgi = srv._tool_generate_image

    async def _capturing_tgi(*a, **k):
        out = await real_tgi(*a, **k)
        try:
            parsed = json.loads(out)
            if isinstance(parsed, dict) and parsed.get("model"):
                captured_models.append(parsed["model"])
        except (json.JSONDecodeError, TypeError):
            pass
        return out

    _IMAGE_GEN_CIRCUIT["disabled_at"] = 0  # healthy
    saved_env = {k: os.environ.get(k) for k in _FORCE_BEDROCK_ENV}
    os.environ.update(_FORCE_BEDROCK_ENV)
    try:
        with tempfile.TemporaryDirectory() as tmp, \
                patch.object(srv, "_get_gw", return_value=gw), \
                patch.object(srv, "_resolve_callable_model_id", side_effect=lambda m, *a, **k: m), \
                patch.object(srv, "_tool_generate_image", new=_capturing_tgi), \
                patch.object(srv, "_try_vertex_image_single", new=_async_none), \
                patch("ai_engine.vertex_image_module.get_vertex_image_client",
                      return_value=_DisabledVertexClient()):
            out_files = asyncio.run(srv._force_generate_from_text(
                primary_tool=f"generate_{ext}",
                target_files=[f"out.{ext}"],
                title=title,
                description=description,
                final_text=body,
                project_path=tmp,
                aws_profile="test",
                bedrock_user="",
            ))
    finally:
        for k, v in saved_env.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v
        _IMAGE_GEN_CIRCUIT["disabled_at"] = 0

    # A document was produced...
    assert out_files, f"force-generate produced no files for ext={ext}"
    # ...and at least one embedded image came from a real Bedrock image model.
    bedrock_hits = [m for m in captured_models if _is_bedrock_image_model(m)]
    assert bedrock_hits, (
        "expected an embedded image sourced from a Bedrock image model id "
        f"({sorted(_BEDROCK_IMAGE_IDS)[:3]}...), but captured models were: "
        f"{captured_models!r}"
    )


# --------------------------------------------------------------------------
# F2 (Property 3, Req 2.2) — no structural signal → _looks_structural False
# --------------------------------------------------------------------------
# Safe alphabet: Korean syllables + ASCII letters + digits + spaces + a few
# benign punctuation marks — but NEVER '/', '|', '>', '→', '⇒' so no path
# token / arrow chain / markdown table row can possibly form. A bare '-' is
# allowed (an arrow needs the two-char '->' which '>' exclusion prevents).
_SAFE_TEXT_ALPHABET = (
    "가나다라마바사아자차카타파하"
    "abcdefghijklmnopqrstuvwxyz"
    "ABCDEFGHIJKLMNOPQRSTUVWXYZ"
    "0123456789"
    "     .,!?:;()-_"
)
_GENERIC_KEYWORDS = ["프로젝트", "구조", "흐름도", "트리", "다이어그램",
                     "diagram", "architecture", "flowchart", "structure"]


@settings(max_examples=60, deadline=None)
@given(
    base=st.text(alphabet=_SAFE_TEXT_ALPHABET, max_size=120),
    keywords=st.lists(st.sampled_from(_GENERIC_KEYWORDS), max_size=4),
)
def test_f2_generic_keywords_without_signals_are_not_structural(base, keywords):
    # Inject generic visual keywords — they MUST NOT trigger structural routing.
    parts = [base] + keywords
    random.Random(len(base)).shuffle(parts)
    text = " ".join(p for p in parts if p)

    # Guard: ensure no structural signal slipped in via the random text.
    assume("/" not in text)
    assume("->" not in text and "→" not in text and "⇒" not in text)
    assume(not any(
        re.match(r"^\s*\|.+\|\s*$", ln) and ln.count("|") >= 3
        for ln in text.splitlines()
    ))

    # Split across the three arguments arbitrarily to exercise the join.
    third = max(1, len(text) // 3)
    description, title, body = text[:third], text[third:2 * third], text[2 * third:]
    assert _looks_structural(description, title, body) is False, (
        f"generic-keyword text with no structural signal must be non-structural: "
        f"{text!r}"
    )


# --------------------------------------------------------------------------
# F3 (Property 5, Req 3.1) — at least one signal → _looks_structural True
# --------------------------------------------------------------------------
_IDENT = st.text(
    alphabet="abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789_.-",
    min_size=1, max_size=12,
)


@st.composite
def _signal_text(draw):
    kind = draw(st.sampled_from(["path", "arrow", "table"]))
    a = draw(_IDENT)
    b = draw(_IDENT)
    if kind == "path":
        return f"설명 {a}/{b} 참고"
    if kind == "arrow":
        arrow = draw(st.sampled_from(["->", "→", "⇒"]))
        return f"흐름 {a} {arrow} {b} 단계"
    # markdown table row — wrapped in '|' with >= 3 pipes
    c = draw(_IDENT)
    return f"| {a} | {b} | {c} |"


@settings(max_examples=60, deadline=None)
@given(text=_signal_text())
def test_f3_structural_signals_are_structural(text):
    assert _looks_structural("", "", text) is True, (
        f"text containing a structural signal must be structural: {text!r}"
    )


# --------------------------------------------------------------------------
# F4 (Property 4, Req 2.4) — diagnostic endpoint schema in both circuit states
# --------------------------------------------------------------------------
_EXPECTED_TOP_KEYS = ("circuit", "models", "selectPreview", "env", "recentAttempts")
_EXPECTED_ENV_KEYS = (
    "AE_IMAGE_PARALLEL_N",
    "AE_IMAGE_QUALITY_THRESHOLD",
    "AE_FORCE_NATIVE_DIAGRAM",
    "AE_DISABLE_HTML_SLIDES",
)


def _assert_diagnostic_schema(body: dict):
    for key in _EXPECTED_TOP_KEYS:
        assert key in body, f"missing top-level key {key!r} in {sorted(body)}"
    circuit = body["circuit"]
    assert isinstance(circuit.get("isBroken"), bool), \
        f"circuit.isBroken must be bool: {circuit!r}"
    assert isinstance(circuit.get("disabled_at"), (int, float)) \
        and not isinstance(circuit.get("disabled_at"), bool), \
        f"circuit.disabled_at must be a number: {circuit!r}"
    assert isinstance(circuit.get("ttl"), (int, float)) \
        and not isinstance(circuit.get("ttl"), bool), \
        f"circuit.ttl must be a number: {circuit!r}"
    env = body["env"]
    for ev in _EXPECTED_ENV_KEYS:
        assert ev in env, f"missing env.{ev} in {sorted(env)}"


@settings(max_examples=10, deadline=None,
          suppress_health_check=[HealthCheck.function_scoped_fixture])
@given(broken=st.booleans())
def test_f4_diagnostic_endpoint_schema_in_both_states(broken):
    client = TestClient(app)
    if broken:
        _IMAGE_GEN_CIRCUIT["disabled_at"] = time.time()
    else:
        _IMAGE_GEN_CIRCUIT["disabled_at"] = 0
    try:
        resp = client.get("/api/debug/image-gen-status")
        assert resp.status_code == 200, \
            f"expected 200 (broken={broken}), got {resp.status_code}: {resp.text[:300]}"
        body = resp.json()
        _assert_diagnostic_schema(body)
        # isBroken must agree with the forced state.
        assert body["circuit"]["isBroken"] is broken, (
            f"circuit.isBroken should be {broken}, got {body['circuit']['isBroken']}"
        )
    finally:
        _IMAGE_GEN_CIRCUIT["disabled_at"] = 0


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-p", "no:cacheprovider", "-q"]))
