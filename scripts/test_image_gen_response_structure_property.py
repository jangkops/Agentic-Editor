"""Property 3: 이미지 생성 성공 시 응답 구조 완전성 (Validates: Requirements 1.3).

For any successful _tool_generate_image call (any model in the chain wins),
the returned JSON response MUST satisfy these invariants:

  - "path"          : non-empty string, file exists on disk under the local
                      ".generated/" directory.
  - "model"         : non-empty string identifying which model produced output;
                      must be one of the configured IMAGE_MODELS.
  - size info       : at least one of:
                        * "size"            (e.g. "1024x1024"), OR
                        * "width" + "height" (positive integers)
  - "qualityScore"  : optional, but if present must be a number in 0..100.

The property is exercised across:
  * 5 allowed sizes (Req 1.4): 512x512, 1024x1024, 1024x1536, 1536x1024, 2048x2048
  * Each model in IMAGE_MODELS as the parallel "winner"
  * Varied prompts (short ASCII, multi-byte Korean)

We patch ``gw.invoke_model`` with a simulated success that returns a valid
base64 PNG of the requested dimensions, so the response truly originates from
the success path of ``_tool_generate_image``.

Run:
  ai_engine/.venv/bin/python scripts/test_image_gen_response_structure_property.py
"""
from __future__ import annotations

import os
import sys
import json
import asyncio
import base64
import tempfile
from io import BytesIO
from unittest.mock import patch, MagicMock

# Make ai_engine importable from repo root.
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from hypothesis import given, settings, strategies as st  # noqa: E402
from PIL import Image  # noqa: E402

from ai_engine.server import (  # noqa: E402
    _tool_generate_image,
    IMAGE_MODELS,
)


# ---------- helpers ----------

ALLOWED_SIZES = [
    (512, 512),
    (1024, 1024),
    (1024, 1536),
    (1536, 1024),
    (2048, 2048),
]


def _png_b64(w: int, h: int) -> str:
    """Build a real PNG of size (w, h) and return its base64 string.

    Uses noisy pixel content so the image survives the >=5KB / non-trivial
    quality gate inside ``_save_and_score`` (otherwise the result would be
    rejected as 'PNG 너무 작음').
    """
    buf = BytesIO()
    img = Image.new("RGB", (w, h))
    px = img.load()
    # cheap deterministic noise — enough entropy to satisfy size threshold
    for y in range(h):
        for x in range(w):
            px[x, y] = ((x * 13 + y * 7) & 0xFF, (x * 5 + y * 11) & 0xFF, (x + y) & 0xFF)
    img.save(buf, format="PNG", optimize=False)
    return base64.b64encode(buf.getvalue()).decode("ascii")


def _strip_region_prefix(model_id: str) -> str:
    for p in ("us.", "eu.", "global."):
        if model_id.startswith(p):
            return model_id[len(p):]
    return model_id


def _make_invoke_factory(winner_model: str, w: int, h: int):
    """Return an async ``invoke_model`` that succeeds only for ``winner_model``
    and errors for every other model in the chain.

    Whichever model the parallel scheduler picked first will, by hypothesis
    selection, eventually win because at least one call returns ``ok``.
    """
    img_b64 = _png_b64(w, h)

    async def invoke_model(model_id, body, timeout=30):
        if _strip_region_prefix(model_id) == winner_model:
            return {"images": [img_b64]}
        return {"error": "simulated non-winner failure"}

    return invoke_model


def _run(prompt: str, size: str, winner_model: str, parallel_n: int = 5) -> dict:
    """Execute _tool_generate_image with a patched gateway and return parsed JSON.

    File-on-disk verification happens *inside* this function (before the temp
    directory is torn down) and is encoded into the returned dict as
    ``_diskExists`` / ``_diskBytes`` so :func:`_check_invariants` can assert
    on the saved artifact even after cleanup.
    """
    w, h = (int(x) for x in size.lower().split("x"))
    mock_gw = MagicMock()
    mock_gw.invoke_model = _make_invoke_factory(winner_model, w, h)

    with tempfile.TemporaryDirectory() as tmp:
        tool_input = {"prompt": prompt, "size": size}
        env_overrides = {
            "AE_IMAGE_PARALLEL_N": str(parallel_n),
            # Disable the auto-retry path (it would call _tool_generate_image
            # recursively); we want the deterministic single-success result.
            "AE_IMAGE_QUALITY_THRESHOLD": "0",
        }
        with patch.dict(os.environ, env_overrides, clear=False), \
             patch("ai_engine.server._get_gw", return_value=mock_gw), \
             patch("ai_engine.server._resolve_callable_model_id",
                   side_effect=lambda mid, *a, **k: mid), \
             patch("ai_engine.server._select_image_models",
                   side_effect=lambda *a, **k: list(IMAGE_MODELS)):
            raw = asyncio.run(_tool_generate_image(tool_input, project_path=tmp))
        parsed = json.loads(raw)
        if "error" in parsed:
            return {"_parsed": parsed, "_failed": True}

        # Verify the saved file exists on disk *while* tmp is still alive.
        # _resolve_local_root may pick tmp, AE_GENERATED_ROOT, ~/.agentic-editor,
        # or tempfile.gettempdir(); search every plausible root.
        rel = parsed["path"]  # ".generated/<filename>"
        disk_exists = False
        disk_bytes = 0
        for root in (
            tmp,
            os.environ.get("AE_GENERATED_ROOT", ""),
            os.path.expanduser("~/.agentic-editor"),
            tempfile.gettempdir(),
        ):
            if not root:
                continue
            candidate = os.path.join(root, rel)
            if os.path.isfile(candidate):
                disk_exists = True
                disk_bytes = os.path.getsize(candidate)
                # Clean up artifacts created outside the tmp dir to avoid
                # accumulation across hypothesis examples.
                if not candidate.startswith(tmp):
                    try:
                        os.remove(candidate)
                    except OSError:
                        pass
                break

        parsed["_diskExists"] = disk_exists
        parsed["_diskBytes"] = disk_bytes
        return {"_parsed": parsed, "_failed": False}


def _check_invariants(parsed: dict, requested_size: str, expected_model: str):
    """Assert Property 3 invariants on a success response."""
    assert "error" not in parsed, f"unexpected error response: {parsed}"

    # path: non-empty string, exists on disk, lives under .generated/
    path = parsed.get("path")
    assert isinstance(path, str) and path, f"missing/invalid path: {parsed}"
    assert path.startswith(".generated/"), f"path not under .generated/: {path!r}"
    assert parsed.get("_diskExists"), (
        f"saved file does not exist on disk: rel={path!r} "
        f"(checked in tmp / AE_GENERATED_ROOT / ~/.agentic-editor / tempdir)"
    )
    assert parsed.get("_diskBytes", 0) > 0, (
        f"saved file is empty: rel={path!r}, size={parsed.get('_diskBytes')}"
    )

    # model: non-empty string, must be one of IMAGE_MODELS
    model = parsed.get("model")
    assert isinstance(model, str) and model, f"missing/invalid model: {parsed}"
    assert model in IMAGE_MODELS, f"unknown model id: {model!r}"
    assert model == expected_model, f"expected winner {expected_model!r}, got {model!r}"

    # size info: either "size" or "width"+"height" must be present and valid
    has_size_str = "size" in parsed and isinstance(parsed["size"], str) and parsed["size"]
    has_wh = (
        isinstance(parsed.get("width"), int) and parsed["width"] > 0
        and isinstance(parsed.get("height"), int) and parsed["height"] > 0
    )
    assert has_size_str or has_wh, f"response lacks size info: {parsed}"
    if has_size_str:
        # size string must parse as <int>x<int>
        sw, sh = parsed["size"].lower().split("x")
        assert int(sw) > 0 and int(sh) > 0, f"invalid size string: {parsed['size']!r}"
    if has_wh:
        # width/height must be positive integers
        assert parsed["width"] > 0 and parsed["height"] > 0
    # the requested size should be reflected somewhere
    rw, rh = (int(x) for x in requested_size.lower().split("x"))
    if has_size_str:
        assert parsed["size"].lower() == f"{rw}x{rh}", (
            f"size string mismatch: requested={requested_size}, got={parsed['size']}"
        )
    if has_wh:
        # width/height come from PIL inspection of the saved PNG; for our
        # simulated success it must equal the requested dimensions.
        assert (parsed["width"], parsed["height"]) == (rw, rh), (
            f"width/height mismatch: requested={(rw, rh)}, got={(parsed['width'], parsed['height'])}"
        )

    # qualityScore: optional, but if present must be a number 0..100
    if "qualityScore" in parsed:
        q = parsed["qualityScore"]
        assert isinstance(q, (int, float)), f"qualityScore not numeric: {q!r}"
        assert 0 <= float(q) <= 100, f"qualityScore out of [0,100]: {q!r}"


# ---------- Property 3 ----------

# Prompts: cover ASCII and multi-byte Korean to make sure response structure
# is independent of prompt content.
_PROMPT_STRATEGY = st.one_of(
    st.text(
        alphabet=st.characters(min_codepoint=0x20, max_codepoint=0x7E),
        min_size=1, max_size=64,
    ),
    st.text(
        alphabet=st.characters(min_codepoint=0xAC00, max_codepoint=0xD7A3),
        min_size=1, max_size=16,
    ),
)


@settings(max_examples=25, deadline=None)
@given(
    prompt=_PROMPT_STRATEGY,
    size_idx=st.integers(min_value=0, max_value=len(ALLOWED_SIZES) - 1),
    winner_idx=st.integers(min_value=0, max_value=len(IMAGE_MODELS) - 1),
)
def test_success_response_structure(prompt, size_idx, winner_idx):
    w, h = ALLOWED_SIZES[size_idx]
    size = f"{w}x{h}"
    winner_model = IMAGE_MODELS[winner_idx]

    out = _run(prompt, size, winner_model)
    assert not out["_failed"], (
        f"expected success but got error: {out['_parsed']} "
        f"(prompt={prompt!r}, size={size}, winner={winner_model})"
    )
    _check_invariants(out["_parsed"], size, winner_model)


def main():
    print("=== Property 3: 이미지 생성 성공 응답 구조 완전성 ===")
    print(f"  IMAGE_MODELS chain ({len(IMAGE_MODELS)}): "
          f"{[m.split('.', 1)[1] for m in IMAGE_MODELS]}")
    print(f"  Allowed sizes (Req 1.4): {[f'{w}x{h}' for w, h in ALLOWED_SIZES]}")
    test_success_response_structure()
    print("  success-path response invariants                OK")
    print("All Property 3 cases passed.")


if __name__ == "__main__":
    main()
