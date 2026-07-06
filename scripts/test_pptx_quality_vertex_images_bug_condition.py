"""Bug-condition exploration test — spec: pptx-quality-vertex-images (bugfix), Task 1.

PROPERTY 1 — Bug Condition (생성된 Vertex 이미지의 폐기/미생성 재현).

This is an EXPLORATORY bug-condition test. It encodes the EXPECTED (post-fix)
behaviour — *every Vertex image that is generated (or that the slide is eligible
to receive) must end up embedded in the final PPTX, with zero "generated but
unused" images* (design Property 1 / Fix Checking). On the UNFIXED code the
production decision seam in ``ai_engine/server.py:_tool_generate_pptx`` violates
this in two ways, so these assertions FAIL — and that failure is the proof that
the bug exists. After the fix (tasks 3.x) the SAME test is re-run and must PASS.

DO NOT "fix" this test when it fails on unfixed code — the failure is intended.

The two scoped bug-condition families (design Bug Condition formal spec):

  (B) embedDiscarded
      hasVertexImage ∧ hasNativeDiagram ∧ ¬hasImageFile ∧ ¬hasSlideBg
      Reproduction: a content slide whose title+bullets classify to *no*
      structural kind (so the Vertex pre-gen loop DOES generate an image and
      fills ``_vertex_pre[i]``), but whose ``imagePrompt`` carries a structural
      keyword. In the embed loop the second classify pass
      (``_classify_section_diagram(heading, imagePrompt, ...)``) assigns a
      ``nativeDiagram``, so the guard ``if (not native_diag and ...)`` discards
      the already-generated Vertex image. → generatedButUnused == 1.

  (A) gateSuppressed
      htmlEnabled ∧ vertexEnabled ∧ role∈{cover,content,visual} ∧ ¬hasVertexImage
      Reproduction: HTML full-bleed enabled. The Vertex pre-gen block is gated
      by ``not _html_enabled``, so when HTML is on the Vertex image is never
      generated at all (``_vertex_pre`` stays empty) — even for a cover/content
      slide that carries an ``imagePrompt``. → no high-quality image embedded.

Everything is hermetic — no network. The Bedrock gateway, the Vertex client
(``get_vertex_image_client`` / ``generate``), the HTML→PNG renderer and
``_tool_generate_image`` are all mocked. We then open the produced ``.pptx`` as
a zip and compare the embedded ``ppt/media/*`` bytes against the exact bytes the
mocked Vertex client produced, to decide whether the generated image was used.

Run (hermetic):
  ./venv/bin/python -m pytest scripts/test_pptx_quality_vertex_images_bug_condition.py -p no:cacheprovider -q

_Requirements: 1.1, 1.2, 1.3, 1.4 (encodes Expected Behaviour 2.1, 2.2, 2.3)_
"""
from __future__ import annotations

import io
import os
import sys
import json
import base64
import asyncio
import zipfile
import tempfile
from unittest.mock import patch

# Make ai_engine importable from repo root.
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import pytest  # noqa: E402
from PIL import Image  # noqa: E402
from hypothesis import given, settings, strategies as st, HealthCheck  # noqa: E402

import ai_engine.server as srv  # noqa: E402
import ai_engine.vertex_image_module as vim  # noqa: E402


# --------------------------------------------------------------------------
# Formal spec mirror — isBugCondition (design "Formal Specification").
# Used only to assert that the inputs we drive really are bug-condition inputs
# (a precondition), keeping the exploration honest.
# --------------------------------------------------------------------------
_VISUAL_ROLES = {"cover", "content", "visual"}


def is_bug_condition(state: dict) -> bool:
    gate_suppressed = (
        state["htmlEnabled"]
        and state["vertexEnabled"]
        and state["role"] in _VISUAL_ROLES
        and not state["hasVertexImage"]
    )
    embed_discarded = (
        state["hasVertexImage"]
        and state["hasNativeDiagram"]
        and not state["hasImageFile"]
        and not state["hasSlideBg"]
    )
    return gate_suppressed or embed_discarded


# --------------------------------------------------------------------------
# Hermetic fakes
# --------------------------------------------------------------------------
def _make_png(tag: int) -> bytes:
    """Produce a unique, valid PNG so each generated image is byte-distinct."""
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
        self.generated_raw: list[bytes] = []

    async def generate(self, prompt=None, model_class=None, aspect_ratio=None,
                       negative_prompt=None, timeout=None, **_kw):
        self.calls += 1
        raw = _make_png(self.calls)
        self.generated_raw.append(raw)
        return {"images": [base64.b64encode(raw).decode("ascii")]}


async def _img_gen_disabled(*_a, **_k):
    """Stand-in for _tool_generate_image — never returns a path (no network)."""
    return json.dumps({"error": "test-disabled"})


async def _html_to_png_fake(html, output_path, width=1920, height=1080, timeout=30, **_k):
    """Stand-in for _render_html_slide_to_png — writes a small valid PNG."""
    os.makedirs(os.path.dirname(output_path), exist_ok=True)
    with open(output_path, "wb") as f:
        f.write(_make_png(900_000))  # large tag → not collide with vertex tags
    return {"ok": True}


def _make_section_html_fake(project_path: str):
    """Build a stand-in for _generate_html_slide_for_section bound to a project."""
    state = {"n": 0}

    async def _section_fake(gw, model_id, heading, body, doc_context,
                            proj_path, style_profile=None):
        state["n"] += 1
        gen = os.path.join(project_path, ".generated")
        os.makedirs(gen, exist_ok=True)
        name = f"slide-html-{state['n']}.png"
        with open(os.path.join(gen, name), "wb") as f:
            f.write(_make_png(800_000 + state["n"]))
        return f".generated/{name}"

    return _section_fake


# --------------------------------------------------------------------------
# PPTX media inspection
# --------------------------------------------------------------------------
def _pptx_media_bytes(pptx_abs: str) -> list[bytes]:
    with zipfile.ZipFile(pptx_abs) as z:
        return [z.read(n) for n in z.namelist() if n.startswith("ppt/media/")]


def _classify_vertex_usage(fake: _FakeVertexClient, pptx_abs: str):
    """Return (generated, embedded, unused) counts for the Vertex images."""
    media = _pptx_media_bytes(pptx_abs)
    embedded = sum(1 for raw in fake.generated_raw if raw in media)
    generated = len(fake.generated_raw)
    return generated, embedded, generated - embedded


# --------------------------------------------------------------------------
# Drivers — exercise the real _tool_generate_pptx decision seam
# --------------------------------------------------------------------------
def _run_embed_discard(title: str, bullets: list[str], image_prompt: str):
    """(B) embedDiscarded driver.

    HTML off + structuring/card-fallback off so the Vertex pre-gen loop runs and
    fills _vertex_pre for the content slide; the slide's imagePrompt then makes
    the embed loop assign a nativeDiagram, exercising the discard guard.
    Returns (generated, embedded, unused).
    """
    fake = _FakeVertexClient()
    with tempfile.TemporaryDirectory() as proj:
        env = {
            "AE_ENABLE_HTML_SLIDES": "0",        # force HTML off
            "AE_PREFER_VERTEX_IMAGE": "1",       # Vertex pre-gen on
            "AE_PREFER_EDITABLE_DIAGRAM": "0",   # skip LLM structuring + card fallback
            "AE_DISABLE_NATIVE_DIAGRAM": "0",
            "AE_PPTX_TOC": "0",
            "AE_ENABLE_VERTEX_BG": "0",
            "AE_GENERATED_ROOT": proj,
        }
        slides = [{"title": title, "bullets": list(bullets), "imagePrompt": image_prompt}]
        with patch.dict(os.environ, env, clear=False), \
                patch.object(vim, "get_vertex_image_client", lambda **_k: fake), \
                patch.object(srv, "_call_bridge", lambda *a, **k: None), \
                patch.object(srv, "_find_local_chrome", lambda: ""), \
                patch.object(srv, "_tool_generate_image", _img_gen_disabled):
            raw = asyncio.run(srv._tool_generate_pptx(
                {"title": title, "slides": slides}, project_path=proj))
        result = json.loads(raw)
        assert "absPath" in result, f"pptx generation failed: {result}"
        return _classify_vertex_usage(fake, result["absPath"])


def _run_gate_suppressed(title: str, image_prompt: str):
    """(A) gateSuppressed driver.

    HTML full-bleed enabled. Vertex is 'enabled' but the pre-gen block is gated
    by `not _html_enabled`, so it never runs. Returns (generated, embedded,
    unused) — generated should be >= 1 under expected behaviour.
    """
    fake = _FakeVertexClient()
    with tempfile.TemporaryDirectory() as proj:
        env = {
            "AE_ENABLE_HTML_SLIDES": "1",        # force HTML on
            "AE_DISABLE_HTML_SLIDES": "0",
            "AE_PREFER_VERTEX_IMAGE": "1",
            "AE_PPTX_TOC": "0",
            "AE_ENABLE_VERTEX_BG": "0",
            "AE_DISABLE_NATIVE_COVER": "1",      # keep cover simple
            "AE_GENERATED_ROOT": proj,
        }
        slides = [{"title": "본문 비주얼 슬라이드", "bullets": ["핵심 메시지"],
                   "imagePrompt": image_prompt}]
        with patch.dict(os.environ, env, clear=False), \
                patch.object(vim, "get_vertex_image_client", lambda **_k: fake), \
                patch.object(srv, "_call_bridge", lambda *a, **k: {"ok": True}), \
                patch.object(srv, "_get_gw", lambda *a, **k: object()), \
                patch.object(srv, "_specialized_model_for_task", lambda *a, **k: "dummy-model"), \
                patch.object(srv, "_render_html_slide_to_png", _html_to_png_fake), \
                patch.object(srv, "_generate_html_slide_for_section", _make_section_html_fake(proj)), \
                patch.object(srv, "_tool_generate_image", _img_gen_disabled):
            raw = asyncio.run(srv._tool_generate_pptx(
                {"title": title, "slides": slides}, project_path=proj))
        result = json.loads(raw)
        assert "absPath" in result, f"pptx generation failed: {result}"
        return fake.calls, _classify_vertex_usage(fake, result["absPath"])


# --------------------------------------------------------------------------
# Case B — embedDiscarded (deterministic)
# --------------------------------------------------------------------------
def test_bug_embed_discarded_vertex_image_is_used():
    """A generated Vertex image must NOT be discarded by the nativeDiagram guard.

    EXPECTED (post-fix): the pre-generated Vertex image is embedded → unused == 0.
    UNFIXED: the imagePrompt-driven nativeDiagram trips `if (not native_diag...)`,
    discarding the already-generated image → unused == 1 (this assertion FAILS).
    """
    title = "분기 요약"
    bullets = ["올해 매출이 늘었다", "내년 방향을 공유한다"]
    image_prompt = "프로세스 흐름도"

    # Precondition: this really is an embedDiscarded bug-condition input.
    assert is_bug_condition({
        "htmlEnabled": False, "vertexEnabled": True, "role": "content",
        "hasVertexImage": True, "hasNativeDiagram": True,
        "hasImageFile": False, "hasSlideBg": False,
    })

    generated, embedded, unused = _run_embed_discard(title, bullets, image_prompt)

    assert generated >= 1, (
        "선행조건 실패: 콘텐츠 슬라이드에 대해 Vertex 이미지가 생성되어야 한다 "
        f"(_vertex_pre 채워짐). generated={generated}"
    )
    # Expected-behaviour assertions (design Property 1). FAIL on unfixed code.
    assert unused == 0, (
        "embedDiscarded 버그: 생성된 Vertex 이미지가 nativeDiagram 가드에 걸려 폐기됨 "
        f"(generatedButUnused={unused}). 기대: 0 (이미지가 슬라이드에 임베드되어야 함)."
    )
    assert embedded >= 1, (
        "embedDiscarded 버그: 최종 PPTX에 Vertex 이미지가 한 장도 임베드되지 않음 "
        f"(embedded={embedded}). 기대: >= 1."
    )


# --------------------------------------------------------------------------
# Case A — gateSuppressed (deterministic)
# --------------------------------------------------------------------------
def test_bug_gate_suppressed_html_excludes_vertex():
    """With HTML on, a cover/content visual slide must still get a Vertex image.

    EXPECTED (post-fix): HTML and Vertex coexist → at least one Vertex image is
    generated and embedded (hero / image slot / on-slide layer).
    UNFIXED: the `not _html_enabled` pre-gen gate suppresses Vertex entirely →
    generated == 0 (this assertion FAILS).
    """
    image_prompt = "회사 비전을 담은 추상 히어로 일러스트, 사람들, 미래 도시"

    # Precondition: gateSuppressed bug-condition input.
    assert is_bug_condition({
        "htmlEnabled": True, "vertexEnabled": True, "role": "content",
        "hasVertexImage": False, "hasNativeDiagram": False,
        "hasImageFile": False, "hasSlideBg": False,
    })

    calls, (generated, embedded, unused) = _run_gate_suppressed("회사 비전 2026", image_prompt)

    # Expected-behaviour assertion (design Property 4 / 2.1). FAIL on unfixed code.
    assert calls >= 1, (
        "gateSuppressed 버그: HTML 활성 시 Vertex 사전생성이 `not _html_enabled` 게이트로 "
        f"완전히 스킵됨 (vertex.generate 호출 {calls}회). 기대: HTML과 Vertex 공존(>= 1회)."
    )
    assert embedded >= 1 and unused == 0, (
        "gateSuppressed 버그: 생성된 Vertex 이미지가 최종 PPTX에 임베드되지 않음 "
        f"(embedded={embedded}, unused={unused}). 기대: embedded>=1, unused==0."
    )


# --------------------------------------------------------------------------
# PROPERTY 1 (scoped PBT) — embedDiscarded across structural imagePrompts.
# Every generated example is an embedDiscarded bug-condition input; on unfixed
# code each FAILS (image discarded), surfacing a shrunk counterexample.
# --------------------------------------------------------------------------
_STRUCTURAL_PROMPTS = [
    "프로세스 흐름도",
    "업무 처리 단계",
    "시스템 아키텍처 구성도",
    "전체 시스템 토폴로지",
    "조직 구조 트리",
    "데이터 파이프라인 flow",
    "서비스 계층 architecture",
    "배포 순서 단계별 process",
]

_NEUTRAL_CONTENT = [
    ("분기 요약", ["올해 매출이 늘었다", "내년 방향을 공유한다"]),
    ("팀 인사", ["반갑습니다", "함께 잘 부탁드립니다"]),
    ("오늘의 메시지", ["감사합니다", "끝까지 함께 가요"]),
    ("환영합니다", ["좋은 하루입니다", "즐겁게 시작해요"]),
]


@settings(max_examples=8, deadline=None,
          suppress_health_check=[HealthCheck.function_scoped_fixture, HealthCheck.too_slow])
@given(prompt=st.sampled_from(_STRUCTURAL_PROMPTS),
       content=st.sampled_from(_NEUTRAL_CONTENT))
def test_property1_embed_discarded_pbt(prompt, content):
    """Property 1 (Bug Condition): for any content slide whose Vertex image is
    generated but whose imagePrompt carries a structural signal, the generated
    image must be embedded (unused == 0). FAILS on unfixed code."""
    title, bullets = content
    generated, embedded, unused = _run_embed_discard(title, bullets, prompt)
    assert generated >= 1, f"precondition: Vertex must generate (generated={generated})"
    assert unused == 0 and embedded >= 1, (
        f"embedDiscarded: 생성된 Vertex 이미지 폐기됨 — prompt={prompt!r}, "
        f"generated={generated}, embedded={embedded}, unused={unused}"
    )


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-p", "no:cacheprovider", "-q"]))
