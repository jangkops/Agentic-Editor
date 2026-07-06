"""Property-based test: smoke verdict aggregation invariant.

Feature: app-deployment-readiness, Property 7: 스모크 판정 집계 불변
**Validates: Requirements 1.7**

For any list of per-path smoke results
``[{"path": str, "ok": bool, "error": str | None, "skipped"?: bool}]``, the PURE
function ``evaluate_smoke`` (in ``scripts/smoke_frozen_backend.py``) MUST satisfy:

  - ``passed`` is ``True`` iff there are NO non-skipped failures — i.e. every
    entry is ``ok`` OR ``skipped`` (vacuously ``True`` for the empty list).
  - ``failed_paths`` is exactly the set of non-skipped entries whose ``ok`` is
    falsy, capturing both ``path`` and ``error``.
  - ``skipped`` entries never appear in ``failed_paths`` and never flip
    ``passed`` to ``False``.
  - ``evaluate_smoke`` is deterministic: the same input yields the same output.

Stack: Python 3.11+, hypothesis library.

Run:
    ./venv/bin/python -m pytest scripts/test_deployment_smoke_evaluate_pbt.py \
        -p no:cacheprovider -q
"""
from __future__ import annotations

import os
import sys

# ``evaluate_smoke`` lives in scripts/smoke_frozen_backend.py which is import-safe
# (its CLI entry point is guarded under ``if __name__ == "__main__"``). Add the
# scripts dir to sys.path so we can import it by module name.
_SCRIPTS_DIR = os.path.dirname(os.path.abspath(__file__))
if _SCRIPTS_DIR not in sys.path:
    sys.path.insert(0, _SCRIPTS_DIR)

from hypothesis import given, settings  # noqa: E402
from hypothesis import strategies as st  # noqa: E402

from smoke_frozen_backend import evaluate_smoke  # noqa: E402


# ---------------------------------------------------------------------------
# Strategies
# ---------------------------------------------------------------------------

# error is str | None. Use a modest text range so shrinking stays cheap.
_error_strategy = st.one_of(st.none(), st.text(max_size=40))

# path is a str.
_path_strategy = st.text(max_size=40)


def _result_entry(with_skipped_key: bool) -> st.SearchStrategy[dict]:
    """A single SmokePathResult dict.

    ``with_skipped_key`` controls whether the optional ``skipped`` key is
    present. When present it may be True/False (and occasionally a truthy /
    falsy non-bool to mirror the ``r.get("skipped")`` truthiness contract).
    """
    base = st.fixed_dictionaries(
        {
            "path": _path_strategy,
            "ok": st.booleans(),
            "error": _error_strategy,
        }
    )
    if not with_skipped_key:
        return base

    def _add_skipped(d: dict, skipped_val) -> dict:
        out = dict(d)
        out["skipped"] = skipped_val
        return out

    skipped_val_strategy = st.one_of(
        st.booleans(),
        # truthy / falsy non-bool values to exercise the .get("skipped")
        # truthiness contract (skipped is treated as a boolean predicate).
        st.sampled_from([1, 0, "", "x", None]),
    )
    return st.builds(_add_skipped, base, skipped_val_strategy)


# Mixed lists: some entries carry the optional skipped key, some don't.
_results_strategy = st.lists(
    st.one_of(_result_entry(with_skipped_key=False), _result_entry(with_skipped_key=True)),
    max_size=12,
)


# ---------------------------------------------------------------------------
# Helpers mirroring the specification (independent of the implementation)
# ---------------------------------------------------------------------------


def _is_skipped(r: dict) -> bool:
    return bool(r.get("skipped"))


def _is_failure(r: dict) -> bool:
    """A non-skipped entry whose ok is falsy."""
    return (not _is_skipped(r)) and (not bool(r.get("ok")))


# ---------------------------------------------------------------------------
# Properties
# ---------------------------------------------------------------------------


@settings(max_examples=200, deadline=None)
@given(results=_results_strategy)
def test_property7_smoke_verdict_aggregation_invariant(results: list[dict]) -> None:
    """Property 7: 스모크 판정 집계 불변 (Validates: Requirements 1.7)."""
    verdict = evaluate_smoke(results)

    # --- shape ---
    assert set(verdict.keys()) == {"passed", "failed_paths", "skipped_paths"}
    assert isinstance(verdict["passed"], bool)
    assert isinstance(verdict["failed_paths"], list)
    assert isinstance(verdict["skipped_paths"], list)

    expected_failures = [r for r in results if _is_failure(r)]
    expected_skips = [r for r in results if _is_skipped(r)]

    # --- passed iff no non-skipped failures (vacuously True for empty list) ---
    assert verdict["passed"] == (len(expected_failures) == 0)

    # --- failed_paths captures exactly the non-skipped failures (path+error) ---
    assert verdict["failed_paths"] == [
        {"path": r.get("path"), "error": r.get("error")} for r in expected_failures
    ]

    # --- skipped_paths captures exactly the skipped entries (path+error) ---
    assert verdict["skipped_paths"] == [
        {"path": r.get("path"), "error": r.get("error")} for r in expected_skips
    ]

    # --- skipped entries never appear in failed_paths ---
    # Every skipped entry is, by construction, never a failure record.
    for r in results:
        if _is_skipped(r):
            assert not _is_failure(r)
    if not expected_skips:
        assert verdict["skipped_paths"] == []


@settings(max_examples=200, deadline=None)
@given(results=_results_strategy)
def test_property7_skipped_never_flips_passed(results: list[dict]) -> None:
    """Skipped entries must never flip passed to False (Validates: Requirements 1.7).

    Removing all skipped entries from the input must not change ``passed``,
    proving skips have no bearing on the pass/fail verdict.
    """
    verdict_full = evaluate_smoke(results)
    non_skipped = [r for r in results if not _is_skipped(r)]
    verdict_without_skips = evaluate_smoke(non_skipped)
    assert verdict_full["passed"] == verdict_without_skips["passed"]


@settings(max_examples=200, deadline=None)
@given(results=_results_strategy)
def test_property7_deterministic(results: list[dict]) -> None:
    """Same input yields the same output (Validates: Requirements 1.7)."""
    first = evaluate_smoke(results)
    second = evaluate_smoke([dict(r) for r in results])
    assert first == second


def test_property7_empty_list_passes_vacuously() -> None:
    """Empty list -> passed True, no failures, no skips (Validates: Requirements 1.7)."""
    verdict = evaluate_smoke([])
    assert verdict == {"passed": True, "failed_paths": [], "skipped_paths": []}
