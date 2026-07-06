"""Property-based test: evaluate_smoke 스모크 판정 집계.

Feature: app-deployment-readiness, Property 7: 스모크 판정 집계 불변
**Validates: Requirements 1.7**

For any list of per-path results
    [{"path": str, "ok": bool, "error": str | None, "skipped"?: bool}]
the PURE function ``evaluate_smoke`` returns
    {"passed": bool, "failed_paths": [...], "skipped_paths": [...]}
where:
  - ``passed`` is True iff there is NO non-skipped failure
    (vacuously True for an empty list).
  - ``failed_paths`` contains EXACTLY the non-skipped ``ok == False`` entries,
    each as ``{"path", "error"}``.
  - skipped entries NEVER affect ``passed`` and appear in ``skipped_paths``.

Stack: Python 3.11+, hypothesis library.
"""
from __future__ import annotations

import sys
from pathlib import Path

# Make ``scripts/`` importable so ``smoke_frozen_backend`` resolves when this
# test is run from any working directory. The module is import-safe (its CLI
# entry point is guarded under ``if __name__ == "__main__"``).
_SCRIPTS_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(_SCRIPTS_DIR))

from hypothesis import given, settings, strategies as st  # noqa: E402

from smoke_frozen_backend import evaluate_smoke  # noqa: E402


# ─────────────────────────────────────────────────────────────────────────────
# Strategies
# ─────────────────────────────────────────────────────────────────────────────

# A path name — arbitrary text (paths need not be unique across entries).
_paths = st.text(min_size=0, max_size=20)

# An error / skip reason — either a string or None.
_errors = st.one_of(st.none(), st.text(min_size=0, max_size=20))


@st.composite
def _result_entry(draw) -> dict:
    """Build one SmokePathResult-like dict.

    ``skipped`` is present as a key only ~half the time (mirroring the real
    producer, which omits the key for non-skipped entries) so the test also
    exercises the "key absent" branch.
    """
    entry: dict = {
        "path": draw(_paths),
        "ok": draw(st.booleans()),
        "error": draw(_errors),
    }
    if draw(st.booleans()):
        entry["skipped"] = draw(st.booleans())
    return entry


_results = st.lists(_result_entry(), min_size=0, max_size=12)


# ─────────────────────────────────────────────────────────────────────────────
# Property 7: 스모크 판정 집계 불변
# ─────────────────────────────────────────────────────────────────────────────


@settings(max_examples=200)
@given(results=_results)
def test_evaluate_smoke_aggregation_invariant(results: list[dict]) -> None:
    verdict = evaluate_smoke(results)

    # Verdict shape.
    assert set(verdict.keys()) == {"passed", "failed_paths", "skipped_paths"}
    assert isinstance(verdict["passed"], bool)

    # Reference partition computed independently of the implementation.
    expected_failed = [
        {"path": r.get("path"), "error": r.get("error")}
        for r in results
        if not r.get("skipped") and not bool(r.get("ok"))
    ]
    expected_skipped = [
        {"path": r.get("path"), "error": r.get("error")}
        for r in results
        if r.get("skipped")
    ]

    # failed_paths == exactly the non-skipped ok==False entries (path+error).
    assert verdict["failed_paths"] == expected_failed
    # skipped entries appear in skipped_paths.
    assert verdict["skipped_paths"] == expected_skipped

    # passed iff no non-skipped failure.
    assert verdict["passed"] == (len(expected_failed) == 0)

    # Skipped entries NEVER affect passed: dropping every skipped entry from the
    # input yields the identical verdict.
    non_skipped = [r for r in results if not r.get("skipped")]
    verdict_without_skips = evaluate_smoke(non_skipped)
    assert verdict_without_skips["passed"] == verdict["passed"]
    assert verdict_without_skips["failed_paths"] == verdict["failed_paths"]


def test_empty_list_passes_vacuously() -> None:
    """Empty input → passed True with no failures/skips."""
    verdict = evaluate_smoke([])
    assert verdict == {"passed": True, "failed_paths": [], "skipped_paths": []}


def test_skipped_failure_does_not_fail_verdict() -> None:
    """A skipped entry with ok=False must NOT flip passed to False."""
    verdict = evaluate_smoke(
        [{"path": "LLM 채팅", "ok": False, "error": "creds 부재", "skipped": True}]
    )
    assert verdict["passed"] is True
    assert verdict["failed_paths"] == []
    assert verdict["skipped_paths"] == [{"path": "LLM 채팅", "error": "creds 부재"}]


def test_non_skipped_failure_fails_verdict() -> None:
    """A non-skipped ok=False entry fails the verdict and is collected."""
    verdict = evaluate_smoke(
        [
            {"path": "부팅/모듈", "ok": True, "error": None},
            {"path": "PPTX", "ok": False, "error": "HTTP 500"},
        ]
    )
    assert verdict["passed"] is False
    assert verdict["failed_paths"] == [{"path": "PPTX", "error": "HTTP 500"}]
    assert verdict["skipped_paths"] == []
