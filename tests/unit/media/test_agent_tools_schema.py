"""
Test: AGENT_TOOLS edit_image schema and mode validation.

Feature: media-generation-editing
Property 11: edit_image mode 유효성 검증
Validates: Requirements 8.5

For any string `mode` value:
  - If `mode` is NOT in {"inpaint", "outpaint"} → _tool_edit_image SHALL return
    a JSON object whose `error` field equals "invalid-mode".
  - If `mode` IS "inpaint" or "outpaint" → _tool_edit_image SHALL NOT return
    "invalid-mode"; it must proceed past the mode check to the next validation
    step (e.g., invalid-parameter / file-not-found / invalid-image / ...).
"""

import asyncio
import json
import sys
from pathlib import Path

import pytest
from hypothesis import HealthCheck, given, settings, strategies as st

# Ensure ai_engine is importable
_REPO_ROOT = Path(__file__).resolve().parents[3]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from ai_engine.server import AGENT_TOOLS, _tool_edit_image  # noqa: E402


VALID_MODES = ("inpaint", "outpaint")


def _edit_image_tool_spec() -> dict:
    """Locate the edit_image toolSpec entry inside AGENT_TOOLS."""
    for entry in AGENT_TOOLS["tools"]:
        spec = entry.get("toolSpec", {})
        if spec.get("name") == "edit_image":
            return spec
    raise AssertionError("edit_image toolSpec not registered in AGENT_TOOLS")


# ---------------------------------------------------------------------------
# Schema sanity (example-based) — ensures the schema declares the mode enum
# that Property 11 relies on at runtime.
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_edit_image_schema_declares_mode_enum():
    """edit_image toolSpec must declare mode as an enum of {inpaint, outpaint}."""
    spec = _edit_image_tool_spec()
    schema = spec["inputSchema"]["json"]
    properties = schema["properties"]

    assert "mode" in properties, "mode property missing from edit_image schema"
    mode_prop = properties["mode"]
    assert mode_prop.get("type") == "string"
    assert sorted(mode_prop.get("enum", [])) == sorted(VALID_MODES)

    required = schema.get("required", [])
    for field in ("mode", "image_path", "prompt"):
        assert field in required, f"{field} must be required (Req 8.2)"


# ---------------------------------------------------------------------------
# Property 11 — runtime mode validity
# ---------------------------------------------------------------------------


def _run_edit_image(tool_input: dict) -> dict:
    """Invoke _tool_edit_image synchronously and parse its JSON response."""
    raw = asyncio.run(_tool_edit_image(tool_input, project_path=""))
    return json.loads(raw)


# Generator: any text that is NOT one of the two valid modes.
_invalid_mode_strategy = st.text(max_size=64).filter(lambda s: s not in VALID_MODES)


@pytest.mark.unit
@given(mode=_invalid_mode_strategy)
@settings(max_examples=200, deadline=None,
          suppress_health_check=[HealthCheck.function_scoped_fixture])
def test_property11_invalid_mode_returns_invalid_mode_error(mode):
    """
    Feature: media-generation-editing, Property 11: edit_image mode 유효성 검증

    Validates: Requirements 8.5
    Any string mode that is not "inpaint" or "outpaint" must yield an
    "invalid-mode" error, regardless of the other tool inputs.
    """
    tool_input = {
        "mode": mode,
        # supply otherwise-valid placeholders so we can be confident the
        # mode check is what rejects the call.
        "image_path": "some/file.png",
        "prompt": "edit the sky",
    }
    result = _run_edit_image(tool_input)
    assert result.get("error") == "invalid-mode", (
        f"Expected invalid-mode for mode={mode!r}, got: {result}"
    )


@pytest.mark.unit
@given(mode=st.sampled_from(VALID_MODES))
@settings(max_examples=20, deadline=None,
          suppress_health_check=[HealthCheck.function_scoped_fixture])
def test_property11_valid_mode_passes_mode_check(mode):
    """
    Feature: media-generation-editing, Property 11: edit_image mode 유효성 검증

    Validates: Requirements 8.5
    Valid modes ("inpaint", "outpaint") must NOT trigger the "invalid-mode"
    error. The call should proceed to the next validation step (which here
    will fail with a different error such as invalid-parameter / file-not-found).
    """
    tool_input = {
        "mode": mode,
        "image_path": "definitely/missing/path-for-test.png",
        "prompt": "edit the sky",
    }
    result = _run_edit_image(tool_input)
    err = result.get("error")
    assert err != "invalid-mode", (
        f"Mode {mode!r} should pass the mode check, but invalid-mode was returned: {result}"
    )
