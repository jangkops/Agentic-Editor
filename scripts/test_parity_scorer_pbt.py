"""Parity_Scorer tests for scripts/parity_scorer.py.

Covers the pptx-design-density-parity machine-judge (요구사항 5). Includes:

  Property 17 (PBT) — score range and pass decision (요구사항 5.2, 5.4, 5.5, 5.6,
                5.8): for HTML built from a random subset of the category's
                Parity_Checklist markers, the Density_Score is an integer in
                0..total, `passed` matches `density_score >= reference_score`
                exactly, `len(items) == total`, and `missing` equals the set of
                checklist names whose marker is not present.

  Example unit tests (8.3) — cover/body pass case (Density >= Reference using a
                fully-marked HTML), fail case (few markers → passed False and
                missing reported), None/empty input → ValueError, bad category
                → ValueError.

Everything is hermetic — pure Python, NO network, NO external deps.

Run (hermetic):
  ./venv/bin/python -m pytest scripts/test_parity_scorer_pbt.py -p no:cacheprovider -q
"""
from __future__ import annotations

import os
import sys

# Make scripts/parity_scorer.py importable regardless of the invocation cwd.
sys.path.insert(0, os.path.dirname(__file__))

import pytest  # noqa: E402
from hypothesis import given, settings, strategies as st  # noqa: E402

from parity_scorer import (  # noqa: E402
    score,
    COVER_CHECKLIST,
    BODY_CHECKLIST,
    COVER_REFERENCE_SCORE,
    BODY_REFERENCE_SCORE,
)

_CHECKLISTS = {"cover": COVER_CHECKLIST, "body": BODY_CHECKLIST}
_REFERENCE = {"cover": COVER_REFERENCE_SCORE, "body": BODY_REFERENCE_SCORE}


def _html_with(markers) -> str:
    """Wrap each checklist marker substring in a div inside a minimal document."""
    body = "".join(f"<div {mk}>x</div>" for mk in markers)
    return "<html><body>" + body + "</body></html>"


# ===========================================================================
# Feature: pptx-design-density-parity, Property 17: Parity_Scorer 점수 범위와
# 합격 판정 — Density_Score 는 0..total 정수이고, passed 는
# (density_score >= reference_score) 와 정확히 일치하며, items 길이 == total,
# missing 은 미충족 항목 집합과 일치한다.
# Validates: Requirements 5.2, 5.4, 5.5, 5.6, 5.8
# ===========================================================================
@st.composite
def _category_html_and_selected(draw):
    category = draw(st.sampled_from(["cover", "body"]))
    checklist = _CHECKLISTS[category]
    # One independent on/off flag per checklist item → a random subset.
    flags = draw(st.lists(st.booleans(),
                          min_size=len(checklist), max_size=len(checklist)))
    selected = {name for (name, _mk), on in zip(checklist, flags) if on}
    markers = [mk for (_name, mk), on in zip(checklist, flags) if on]
    return category, _html_with(markers), selected


@settings(max_examples=200, deadline=None)
@given(case=_category_html_and_selected())
def test_property17_score_range_and_pass_decision(case):
    category, html, selected = case
    checklist = _CHECKLISTS[category]
    total = len(checklist)
    all_names = {name for name, _mk in checklist}

    result = score(html, category)

    # Density_Score is an integer within 0..total and equals the selected count.
    assert isinstance(result["density_score"], int)
    assert 0 <= result["density_score"] <= total
    assert result["density_score"] == len(selected)

    # Reference is the fixed category constant; total matches the checklist size.
    assert result["reference_score"] == _REFERENCE[category]
    assert result["total"] == total

    # passed must match the >= comparison exactly.
    assert result["passed"] == (result["density_score"] >= result["reference_score"])

    # items reports every checklist entry; missing == names not present.
    assert len(result["items"]) == total
    assert {it["name"] for it in result["items"]} == all_names
    assert set(result["missing"]) == (all_names - selected)
    # present flags are internally consistent with selection.
    assert {it["name"] for it in result["items"] if it["present"]} == selected


# ===========================================================================
# 8.3 Example unit tests — pass / fail / missing-input paths
# ===========================================================================
def test_cover_pass_when_fully_marked():
    markers = [mk for _name, mk in COVER_CHECKLIST]
    res = score(_html_with(markers), "cover")
    assert res["density_score"] == len(COVER_CHECKLIST) == 7
    assert res["density_score"] >= res["reference_score"]
    assert res["passed"] is True
    assert res["missing"] == []


def test_body_pass_when_fully_marked():
    markers = [mk for _name, mk in BODY_CHECKLIST]
    res = score(_html_with(markers), "body")
    assert res["density_score"] == len(BODY_CHECKLIST) == 8
    assert res["density_score"] >= res["reference_score"]
    assert res["passed"] is True
    assert res["missing"] == []


def test_cover_fail_with_few_markers_reports_missing():
    # Only the first checklist marker present → below the reference score.
    first_name, first_marker = COVER_CHECKLIST[0]
    res = score(_html_with([first_marker]), "cover")
    assert res["density_score"] == 1
    assert res["passed"] is False
    # Every other checklist name is reported missing.
    expected_missing = {name for name, _mk in COVER_CHECKLIST} - {first_name}
    assert set(res["missing"]) == expected_missing
    assert first_name not in res["missing"]


def test_body_fail_with_few_markers_reports_missing():
    first_name, first_marker = BODY_CHECKLIST[0]
    res = score(_html_with([first_marker]), "body")
    assert res["density_score"] == 1
    assert res["passed"] is False
    expected_missing = {name for name, _mk in BODY_CHECKLIST} - {first_name}
    assert set(res["missing"]) == expected_missing


def test_none_input_raises_value_error():
    with pytest.raises(ValueError):
        score(None, "cover")


def test_empty_input_raises_value_error():
    with pytest.raises(ValueError):
        score("", "body")


def test_bad_category_raises_value_error():
    with pytest.raises(ValueError):
        score("<html><body></body></html>", "banner")


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(pytest.main([__file__, "-q", "-p", "no:cacheprovider"]))
