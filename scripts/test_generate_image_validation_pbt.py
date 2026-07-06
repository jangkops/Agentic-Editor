"""Property-based test: generate_image 입력 유효성 검증.

Feature: media-generation-editing, Property 2: generate_image 입력 유효성 검증
**Validates: Requirements 1.4, 1.7**

For any input to `_tool_generate_image()`, the validation layer MUST behave as
follows BEFORE any model call is attempted:

  - prompt empty/whitespace-only/missing  → error == "invalid-parameter"
                                            (Req 1.6: prompt required)
  - prompt length > 2000 chars            → error == "invalid-parameter"
                                            (Req 1.7: max 2000 chars)
  - prompt length 1..=2000 + arbitrary size → validation PASSES; control flow
                                            reaches the model call. Hitting the
                                            tripped circuit breaker here yields
                                            error=="circuit-breaker", which is
                                            sufficient evidence that the
                                            validation layer did NOT reject the
                                            input.
  - size string parseable or not          → never produces "invalid-parameter"
                                            in the current implementation
                                            (per task: "error or coerced — per
                                            implementation"). Unparseable size
                                            is silently coerced to 1024x1024.

To keep the test hermetic and fast we *trip* the global image-gen circuit
breaker in setup. Valid prompts then short-circuit to the circuit-breaker
branch instead of reaching out to the Bedrock gateway. The breaker is
restored on teardown.

Stack: Python 3.11+, hypothesis, Pillow.

Run:
    ai_engine/.venv/bin/python scripts/test_generate_image_validation_pbt.py
"""
from __future__ import annotations

import asyncio
import json
import os
import sys
import tempfile
import time as _time
from pathlib import Path

# Make the ai_engine package importable when this script is run directly.
_REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_REPO_ROOT))

from hypothesis import HealthCheck, given, settings, strategies as st  # noqa: E402

from ai_engine import server as _server  # noqa: E402
from ai_engine.server import _tool_generate_image  # noqa: E402


# ---------------------------------------------------------------------------
# Hermetic setup: trip the image-gen circuit breaker so valid inputs never
# escape the process. The breaker is checked AFTER prompt validation, so
# invalid prompts still return "invalid-parameter" first.
# ---------------------------------------------------------------------------

_FIXTURE_DIR = Path(tempfile.mkdtemp(prefix="genimg_pbt_"))


def _trip_breaker() -> None:
    _server._IMAGE_GEN_CIRCUIT["disabled_at"] = _time.time()


def _restore_breaker() -> None:
    _server._IMAGE_GEN_CIRCUIT["disabled_at"] = 0


# Validation error codes that the implementation can return BEFORE the model
# call. If a "valid" input produces any of these, the property is violated.
_VALIDATION_ERRORS = {"invalid-parameter"}

# Errors that may legitimately appear AFTER the input passes validation. They
# are evidence that the validation layer accepted the input.
_POST_VALIDATION_ERRORS = {"circuit-breaker", "model-unavailable"}


def _run(coro):
    return asyncio.run(coro)


def _invoke(tool_input: dict) -> dict:
    raw = _run(_tool_generate_image(tool_input, str(_FIXTURE_DIR)))
    try:
        return json.loads(raw)
    except json.JSONDecodeError as e:  # pragma: no cover — implementation contract
        raise AssertionError(f"non-JSON response: {raw!r}") from e


# ---------------------------------------------------------------------------
# Strategies
# ---------------------------------------------------------------------------

# Whitespace-only or empty prompts. Server strips before checking, so any of
# these MUST be treated as missing.
_blank_prompt = st.text(alphabet=" \t\n\r\u00a0", min_size=0, max_size=20)

# Prompts strictly longer than 2000 characters. The implementation rejects
# strip(prompt) > 2000, so we generate trailing non-whitespace content.
_oversized_prompt = st.integers(min_value=1, max_value=4000).map(
    lambda extra: "x" * (2000 + extra)
)

# Valid prompts: 1..=2000 characters after strip. We sample from non-whitespace
# alphabets to ensure strip() doesn't reduce length below 1.
_valid_prompt_text = st.text(
    alphabet=st.characters(
        whitelist_categories=("Lu", "Ll", "Nd", "Po"),
        blacklist_characters="\x00",
    ),
    min_size=1,
    max_size=2000,
).filter(lambda s: 1 <= len(s.strip()) <= 2000)

# Size dimensions in [256, 2048]. The spec calls out 256..2048 as the typical
# valid range; the implementation accepts any parseable WxH, so this property
# captures the "valid inputs proceed to model call" expectation.
_valid_dim = st.integers(min_value=256, max_value=2048)

# Out-of-range dimensions: outside [256, 2048] OR unparseable. Per the task
# description these are "error or coerced — per implementation". The current
# implementation silently coerces unparseable input and accepts parseable
# out-of-range input; in NEITHER case must validation produce
# "invalid-parameter".
_unparseable_size = st.text(
    alphabet=st.characters(
        whitelist_categories=("Lu", "Ll", "Po"),
        blacklist_characters="\x00",
    ),
    min_size=0,
    max_size=20,
).filter(lambda s: "x" not in s.lower())

_out_of_range_dim = st.one_of(
    st.integers(min_value=-512, max_value=255),
    st.integers(min_value=2049, max_value=8192),
)


# ---------------------------------------------------------------------------
# Properties
# ---------------------------------------------------------------------------

# Req 1.6 — empty/whitespace/missing prompt → invalid-parameter.
@given(blank=_blank_prompt)
@settings(
    max_examples=40,
    deadline=None,
    suppress_health_check=[HealthCheck.function_scoped_fixture],
)
def prop_empty_prompt_returns_invalid_parameter(blank: str) -> None:
    res = _invoke({"prompt": blank})
    assert res.get("error") == "invalid-parameter", (
        f"expected invalid-parameter for blank prompt={blank!r}, got {res!r}"
    )
    detail = (res.get("detail") or "").lower()
    assert "required" in detail or "prompt" in detail, (
        f"expected detail to mention prompt requirement, got {res!r}"
    )


# Req 1.6 — `prompt` key missing entirely → invalid-parameter.
def case_missing_prompt_key_returns_invalid_parameter() -> None:
    res = _invoke({})  # no prompt key at all
    assert res.get("error") == "invalid-parameter", (
        f"expected invalid-parameter for missing prompt key, got {res!r}"
    )


# Req 1.6 — prompt of `None` (key present, value None) → invalid-parameter.
def case_none_prompt_returns_invalid_parameter() -> None:
    res = _invoke({"prompt": None})
    assert res.get("error") == "invalid-parameter", (
        f"expected invalid-parameter for prompt=None, got {res!r}"
    )


# Req 1.7 — prompt > 2000 chars → invalid-parameter.
@given(prompt=_oversized_prompt)
@settings(
    max_examples=30,
    deadline=None,
    suppress_health_check=[HealthCheck.function_scoped_fixture],
)
def prop_oversized_prompt_returns_invalid_parameter(prompt: str) -> None:
    assert len(prompt) > 2000  # sanity
    res = _invoke({"prompt": prompt})
    assert res.get("error") == "invalid-parameter", (
        f"expected invalid-parameter for len(prompt)={len(prompt)}, got {res!r}"
    )
    detail = (res.get("detail") or "").lower()
    assert "2000" in detail or "exceed" in detail, (
        f"expected detail to mention 2000-char limit, got {res!r}"
    )


# Req 1.7 boundary — len == 2000 passes validation; len == 2001 fails.
def case_prompt_length_boundary() -> None:
    at_limit = "a" * 2000
    over_limit = "a" * 2001

    res_ok = _invoke({"prompt": at_limit})
    assert res_ok.get("error") not in _VALIDATION_ERRORS, (
        f"prompt length 2000 must pass validation, got {res_ok!r}"
    )

    res_bad = _invoke({"prompt": over_limit})
    assert res_bad.get("error") == "invalid-parameter", (
        f"prompt length 2001 must be rejected, got {res_bad!r}"
    )


# Req 1.4 — for any prompt of valid length and any size value, validation
# does not return "invalid-parameter". (Implementation accepts any size or
# silently coerces; this property documents that "size" does not gate
# validation in the current implementation.)
@given(
    prompt=_valid_prompt_text,
    w=st.one_of(_valid_dim, _out_of_range_dim),
    h=st.one_of(_valid_dim, _out_of_range_dim),
)
@settings(
    max_examples=40,
    deadline=None,
    suppress_health_check=[HealthCheck.function_scoped_fixture],
)
def prop_valid_prompt_with_any_size_passes_validation(
    prompt: str, w: int, h: int
) -> None:
    res = _invoke({"prompt": prompt, "size": f"{w}x{h}"})
    assert res.get("error") not in _VALIDATION_ERRORS, (
        f"valid prompt + size={w}x{h} should pass validation, got {res!r}"
    )


# Req 1.4 — unparseable size strings must NOT trigger "invalid-parameter"
# (current implementation silently coerces to 1024x1024).
@given(prompt=_valid_prompt_text, size=_unparseable_size)
@settings(
    max_examples=30,
    deadline=None,
    suppress_health_check=[HealthCheck.function_scoped_fixture],
)
def prop_unparseable_size_does_not_produce_validation_error(
    prompt: str, size: str
) -> None:
    res = _invoke({"prompt": prompt, "size": size})
    assert res.get("error") not in _VALIDATION_ERRORS, (
        f"unparseable size={size!r} must not produce invalid-parameter, got {res!r}"
    )


# Req 1.6 — a valid prompt without a "size" field must pass validation
# (1024x1024 default).
@given(prompt=_valid_prompt_text)
@settings(
    max_examples=30,
    deadline=None,
    suppress_health_check=[HealthCheck.function_scoped_fixture],
)
def prop_valid_prompt_no_size_passes_validation(prompt: str) -> None:
    res = _invoke({"prompt": prompt})
    assert res.get("error") not in _VALIDATION_ERRORS, (
        f"valid prompt without size should pass validation, got {res!r}"
    )


# ---------------------------------------------------------------------------
# Smoke check: confirm the test environment is wired correctly. With the
# circuit breaker tripped, a valid input must produce error=="circuit-breaker"
# (or one of the post-validation errors) — never a validation error.
# ---------------------------------------------------------------------------

def smoke_baseline_valid_input_passes_validation() -> None:
    res = _invoke({"prompt": "a serene mountain at sunrise", "size": "1024x1024"})
    err = res.get("error")
    assert err not in _VALIDATION_ERRORS, (
        f"baseline valid input tripped validation (err={err!r}). Properties below "
        f"would pass vacuously. full response: {res!r}"
    )
    # In the hermetic test setup we expect to land on the breaker branch.
    assert err in _POST_VALIDATION_ERRORS, (
        f"expected post-validation error code (circuit-breaker / model-unavailable) "
        f"with breaker tripped, got {res!r}"
    )


# ---------------------------------------------------------------------------
# Driver
# ---------------------------------------------------------------------------

PROPERTIES = [
    ("invalid-parameter on blank/whitespace prompt", prop_empty_prompt_returns_invalid_parameter),
    ("invalid-parameter on prompt >2000 chars", prop_oversized_prompt_returns_invalid_parameter),
    ("valid prompt + any size passes validation", prop_valid_prompt_with_any_size_passes_validation),
    ("unparseable size does not produce invalid-parameter", prop_unparseable_size_does_not_produce_validation_error),
    ("valid prompt without size passes validation", prop_valid_prompt_no_size_passes_validation),
]

CASES = [
    ("missing prompt key → invalid-parameter", case_missing_prompt_key_returns_invalid_parameter),
    ("prompt=None → invalid-parameter", case_none_prompt_returns_invalid_parameter),
    ("prompt-length boundary at 2000/2001", case_prompt_length_boundary),
]


def main() -> int:
    print("=== Property 2: generate_image 입력 유효성 검증 ===")
    print(f"fixture dir: {_FIXTURE_DIR}")

    _trip_breaker()
    try:
        print("\n[smoke] baseline valid input must pass validation ...", end=" ", flush=True)
        smoke_baseline_valid_input_passes_validation()
        print("OK")

        failures: list[tuple[str, BaseException]] = []
        for label, fn in PROPERTIES:
            print(f"[prop] {label} ...", end=" ", flush=True)
            try:
                fn()
                print("OK")
            except BaseException as e:  # noqa: BLE001
                print("FAIL")
                failures.append((label, e))

        for label, fn in CASES:
            print(f"[case] {label} ...", end=" ", flush=True)
            try:
                fn()
                print("OK")
            except BaseException as e:  # noqa: BLE001
                print("FAIL")
                failures.append((label, e))

        print()
        if failures:
            print(f"FAILED: {len(failures)} of {len(PROPERTIES) + len(CASES)} checks")
            for label, e in failures:
                print(f"  - {label}: {type(e).__name__}: {e}")
            return 1
        print(f"PASSED: all {len(PROPERTIES) + len(CASES)} checks")
        return 0
    finally:
        _restore_breaker()


if __name__ == "__main__":
    sys.exit(main())
