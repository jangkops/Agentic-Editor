"""Property-based test: edit_image outpaint 입력 유효성 검증.

Feature: media-generation-editing, Property 5: edit_image outpaint 입력 유효성 검증
**Validates: Requirements 3.2, 3.5, 3.7**

For any input where mode='outpaint', validation MUST reject the request
with the appropriate error code when:
  - image_path missing/non-existent → "file-not-found"
  - image format not PNG/JPEG/WEBP → "invalid-input"
  - any side > 4096px           → "invalid-input"
  - direction values not in {left,right,top,bottom,up,down} or count not 1..4
                                 → "invalid-parameter"
  - extend_pixels not in [1, 1024] → "invalid-parameter"
  - prompt empty or > 512 chars → "invalid-parameter"

The test calls `_tool_edit_image()` directly with carefully constructed
input dimensions so that validation always fails BEFORE any gateway call,
and asserts the appropriate error code is returned.

Stack: Python 3.11+, hypothesis library.
"""
from __future__ import annotations

import asyncio
import json
import os
import sys
import tempfile
from pathlib import Path

# Make the ai_engine package importable when the script is run directly.
_REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_REPO_ROOT))

from hypothesis import HealthCheck, Verbosity, given, settings, strategies as st  # noqa: E402
from PIL import Image as PILImage  # noqa: E402

from ai_engine.server import _tool_edit_image  # noqa: E402


# ---------------------------------------------------------------------------
# Fixtures — generated once and reused across all hypothesis examples.
# ---------------------------------------------------------------------------

_FIXTURE_DIR = Path(tempfile.mkdtemp(prefix="outpaint_pbt_"))

ALLOWED_DIRECTIONS = ("left", "right", "top", "bottom", "up", "down")
# AGENT_TOOLS schema also accepts up/down aliases; the spec uses top/bottom.
# `_tool_edit_image` accepts both and normalizes internally, so all six are valid.

DEFAULT_PROMPT = "extend the scene with matching surroundings"


def _save_png(size: tuple[int, int], name: str) -> str:
    p = _FIXTURE_DIR / name
    PILImage.new("RGB", size, color=(123, 45, 67)).save(str(p), "PNG")
    return str(p)


def _save_jpeg(size: tuple[int, int], name: str) -> str:
    p = _FIXTURE_DIR / name
    PILImage.new("RGB", size, color=(10, 20, 30)).save(str(p), "JPEG")
    return str(p)


def _save_webp(size: tuple[int, int], name: str) -> str:
    p = _FIXTURE_DIR / name
    PILImage.new("RGB", size, color=(200, 100, 50)).save(str(p), "WEBP")
    return str(p)


def _save_raw(name: str, body: bytes) -> str:
    p = _FIXTURE_DIR / name
    p.write_bytes(body)
    return str(p)


# Valid format fixtures (small dimensions, well under 5MB)
VALID_PNG = _save_png((256, 256), "valid.png")
VALID_JPEG = _save_jpeg((256, 256), "valid.jpg")
VALID_WEBP = _save_webp((256, 256), "valid.webp")

# Files whose first bytes do NOT match PNG (\\x89PNG...), JPEG (\\xff\\xd8\\xff)
# or WEBP (RIFF) signatures.
INVALID_FMT_TEXT = _save_raw("invalid_text.png", b"this is plain text, not an image\n")
INVALID_FMT_GIF89 = _save_raw("invalid_gif.png", b"GIF89a" + b"\x00" * 32)
INVALID_FMT_BMP = _save_raw("invalid_bmp.png", b"BM" + b"\x00" * 50)

INVALID_FORMAT_FILES = [INVALID_FMT_TEXT, INVALID_FMT_GIF89, INVALID_FMT_BMP]

# Oversize images (one side > 4096). Stay solid-color to keep file size tiny.
OVERSIZE_W = _save_png((4097, 64), "oversize_w.png")
OVERSIZE_H = _save_png((64, 4097), "oversize_h.png")
OVERSIZE_BOTH = _save_png((5000, 5000), "oversize_both.png")
OVERSIZE_FILES = [OVERSIZE_W, OVERSIZE_H, OVERSIZE_BOTH]


def _run(coro):
    """Run an async coroutine to completion in a fresh event loop."""
    return asyncio.run(coro)


def _valid_args(**overrides) -> dict:
    """Return a baseline outpaint payload that would otherwise pass validation."""
    base = {
        "mode": "outpaint",
        "image_path": VALID_PNG,
        "prompt": DEFAULT_PROMPT,
        "direction": ["right"],
        "extend_pixels": 256,
    }
    base.update(overrides)
    return base


def _invoke(args: dict) -> dict:
    """Invoke `_tool_edit_image` and decode the returned JSON string."""
    raw = _run(_tool_edit_image(args, str(_FIXTURE_DIR)))
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        raise AssertionError(f"non-JSON response: {raw!r}")


# ---------------------------------------------------------------------------
# Strategies
# ---------------------------------------------------------------------------

# Strategy that produces strings that look like file paths but cannot exist
# inside the fixture dir.
_safe_path_chars = st.text(
    alphabet=st.characters(
        whitelist_categories=("Lu", "Ll", "Nd"),
        whitelist_characters="_-.",
    ),
    min_size=1,
    max_size=40,
)

# Bad direction strings: anything not in the allowed set.
_bad_direction_value = st.text(
    alphabet=st.characters(whitelist_categories=("Lu", "Ll", "Nd")),
    min_size=1,
    max_size=20,
).filter(lambda s: s not in ALLOWED_DIRECTIONS)


# ---------------------------------------------------------------------------
# Property assertions
# ---------------------------------------------------------------------------

# 3.5 — image_path missing / non-existent → "file-not-found"
@given(stem=_safe_path_chars, suffix=st.sampled_from([".png", ".jpg", ".jpeg", ".webp", ".bin"]))
@settings(
    max_examples=40,
    deadline=None,
    suppress_health_check=[HealthCheck.function_scoped_fixture],
)
def prop_nonexistent_image_path_returns_file_not_found(stem: str, suffix: str) -> None:
    bogus = _FIXTURE_DIR / f"missing_{stem}_{abs(hash(stem)) % 10**8}{suffix}"
    if bogus.exists():  # pragma: no cover — extraordinarily unlikely
        return
    res = _invoke(_valid_args(image_path=str(bogus)))
    assert res.get("error") == "file-not-found", (
        f"expected file-not-found for non-existent image_path, got {res!r}"
    )


# 3.5 — unsupported image format (not PNG/JPEG/WEBP) → "invalid-input"
@given(idx=st.integers(min_value=0, max_value=len(INVALID_FORMAT_FILES) - 1))
@settings(
    max_examples=20,
    deadline=None,
    suppress_health_check=[HealthCheck.function_scoped_fixture],
)
def prop_unsupported_format_returns_invalid_input(idx: int) -> None:
    bad_path = INVALID_FORMAT_FILES[idx]
    res = _invoke(_valid_args(image_path=bad_path))
    assert res.get("error") == "invalid-input", (
        f"expected invalid-input for unsupported format, got {res!r}"
    )


# 3.5 — any side > 4096px → "invalid-input"
@given(idx=st.integers(min_value=0, max_value=len(OVERSIZE_FILES) - 1))
@settings(
    max_examples=20,
    deadline=None,
    suppress_health_check=[HealthCheck.function_scoped_fixture],
)
def prop_oversize_dimensions_returns_invalid_input(idx: int) -> None:
    res = _invoke(_valid_args(image_path=OVERSIZE_FILES[idx]))
    assert res.get("error") == "invalid-input", (
        f"expected invalid-input for >4096px image, got {res!r}"
    )
    detail = (res.get("detail") or "").lower()
    assert "4096" in detail or "dimension" in detail, (
        f"expected detail to mention dimension/4096, got {res!r}"
    )


# 3.7 — direction value outside the allowed set → "invalid-parameter"
@given(bad=_bad_direction_value)
@settings(
    max_examples=40,
    deadline=None,
    suppress_health_check=[HealthCheck.function_scoped_fixture],
)
def prop_invalid_direction_value_returns_invalid_parameter(bad: str) -> None:
    res = _invoke(_valid_args(direction=[bad]))
    assert res.get("error") == "invalid-parameter", (
        f"expected invalid-parameter for direction={bad!r}, got {res!r}"
    )


# 3.7 — direction list with count outside 1..4 → "invalid-parameter"
@given(
    direction_list=st.one_of(
        st.just([]),  # zero entries
        st.lists(st.sampled_from(ALLOWED_DIRECTIONS), min_size=5, max_size=10),  # too many
    )
)
@settings(
    max_examples=30,
    deadline=None,
    suppress_health_check=[HealthCheck.function_scoped_fixture],
)
def prop_invalid_direction_count_returns_invalid_parameter(direction_list: list) -> None:
    res = _invoke(_valid_args(direction=direction_list))
    assert res.get("error") == "invalid-parameter", (
        f"expected invalid-parameter for direction count={len(direction_list)}, got {res!r}"
    )


# 3.7 — direction not provided as a list at all → "invalid-parameter"
@given(non_list=st.one_of(st.text(min_size=1, max_size=10), st.integers(), st.floats(allow_nan=False)))
@settings(
    max_examples=30,
    deadline=None,
    suppress_health_check=[HealthCheck.function_scoped_fixture],
)
def prop_non_list_direction_returns_invalid_parameter(non_list) -> None:
    res = _invoke(_valid_args(direction=non_list))
    assert res.get("error") == "invalid-parameter", (
        f"expected invalid-parameter for non-list direction={non_list!r}, got {res!r}"
    )


# 3.7 — extend_pixels not in [1, 1024] → "invalid-parameter"
@given(
    extend=st.one_of(
        st.integers(max_value=0),
        st.integers(min_value=1025, max_value=1_000_000),
    )
)
@settings(
    max_examples=40,
    deadline=None,
    suppress_health_check=[HealthCheck.function_scoped_fixture],
)
def prop_extend_pixels_out_of_range_returns_invalid_parameter(extend: int) -> None:
    res = _invoke(_valid_args(extend_pixels=extend))
    assert res.get("error") == "invalid-parameter", (
        f"expected invalid-parameter for extend_pixels={extend}, got {res!r}"
    )


# 3.7 — extend_pixels of wrong type (non-int / bool) → "invalid-parameter"
@given(
    bogus=st.one_of(
        st.booleans(),  # bool is an int subclass; impl rejects it
        st.text(min_size=1, max_size=8),
        st.floats(allow_nan=False, allow_infinity=False, min_value=-1000, max_value=1000),
    )
)
@settings(
    max_examples=30,
    deadline=None,
    suppress_health_check=[HealthCheck.function_scoped_fixture],
)
def prop_extend_pixels_wrong_type_returns_invalid_parameter(bogus) -> None:
    res = _invoke(_valid_args(extend_pixels=bogus))
    assert res.get("error") == "invalid-parameter", (
        f"expected invalid-parameter for extend_pixels of type {type(bogus).__name__}, got {res!r}"
    )


# 3.2 — prompt empty (after strip) → "invalid-parameter"
@given(blank=st.text(alphabet=" \t\n\r", min_size=0, max_size=12))
@settings(
    max_examples=20,
    deadline=None,
    suppress_health_check=[HealthCheck.function_scoped_fixture],
)
def prop_empty_prompt_returns_invalid_parameter(blank: str) -> None:
    res = _invoke(_valid_args(prompt=blank))
    assert res.get("error") == "invalid-parameter", (
        f"expected invalid-parameter for empty/whitespace prompt={blank!r}, got {res!r}"
    )


# 3.2 — prompt > 512 chars → "invalid-parameter"
@given(extra=st.integers(min_value=1, max_value=2000))
@settings(
    max_examples=30,
    deadline=None,
    suppress_health_check=[HealthCheck.function_scoped_fixture],
)
def prop_oversized_prompt_returns_invalid_parameter(extra: int) -> None:
    big_prompt = "x" * (512 + extra)
    res = _invoke(_valid_args(prompt=big_prompt))
    assert res.get("error") == "invalid-parameter", (
        f"expected invalid-parameter for prompt length={len(big_prompt)}, got {res!r}"
    )


# ---------------------------------------------------------------------------
# Smoke check: a sanity test that confirms our valid baseline is in fact valid
# at the validation layer (any subsequent error must originate from the gateway,
# not from input validation). This protects against the property tests passing
# trivially because the baseline itself is broken.
# ---------------------------------------------------------------------------

def smoke_valid_baseline_passes_validation() -> None:
    res = _invoke(_valid_args())
    err = res.get("error")
    # If validation accepts the input we would call invoke_model, which is
    # expected to fail in the test environment with a network/auth error. Any
    # of those downstream errors are acceptable; the only failure modes that
    # would invalidate our property tests are the validation error codes
    # themselves.
    forbidden = {"file-not-found", "invalid-input", "invalid-parameter", "invalid-mode", "invalid-image"}
    assert err not in forbidden, (
        f"baseline payload tripped validation (err={err!r}); property tests would be vacuously true. "
        f"full response: {res!r}"
    )


# ---------------------------------------------------------------------------
# Driver
# ---------------------------------------------------------------------------

PROPERTIES = [
    ("file-not-found on non-existent image_path", prop_nonexistent_image_path_returns_file_not_found),
    ("invalid-input on unsupported format", prop_unsupported_format_returns_invalid_input),
    ("invalid-input on >4096px side", prop_oversize_dimensions_returns_invalid_input),
    ("invalid-parameter on bad direction value", prop_invalid_direction_value_returns_invalid_parameter),
    ("invalid-parameter on bad direction count", prop_invalid_direction_count_returns_invalid_parameter),
    ("invalid-parameter on non-list direction", prop_non_list_direction_returns_invalid_parameter),
    ("invalid-parameter on extend_pixels out of [1,1024]", prop_extend_pixels_out_of_range_returns_invalid_parameter),
    ("invalid-parameter on extend_pixels wrong type", prop_extend_pixels_wrong_type_returns_invalid_parameter),
    ("invalid-parameter on empty prompt", prop_empty_prompt_returns_invalid_parameter),
    ("invalid-parameter on prompt >512 chars", prop_oversized_prompt_returns_invalid_parameter),
]


def main() -> int:
    print(f"Property test: edit_image outpaint validation")
    print(f"Fixture dir: {_FIXTURE_DIR}")
    print()

    print("[smoke] valid baseline must pass validation layer ...", end=" ", flush=True)
    smoke_valid_baseline_passes_validation()
    print("OK")

    failures = []
    for label, fn in PROPERTIES:
        print(f"[prop] {label} ...", end=" ", flush=True)
        try:
            fn()
            print("OK")
        except Exception as e:  # noqa: BLE001
            print("FAIL")
            failures.append((label, e))

    print()
    if failures:
        print(f"FAILED: {len(failures)} of {len(PROPERTIES)} properties")
        for label, e in failures:
            print(f"  - {label}: {e}")
        return 1
    print(f"PASSED: all {len(PROPERTIES)} properties")
    return 0


if __name__ == "__main__":
    sys.exit(main())
