# Feature: langgraph-reasoning-upgrade, Property 13: last-wins reducer 정확성 (echo 면역)
"""Property 13: last-wins reducer 정확성 (echo 면역) — Hypothesis 기반 PBT.

Validates: Requirements 2.3

design.md Correctness Property 13 발췌:
    For any 정수/값 시퀀스에 대해, `_take_right` reducer 를 순차 적용한 결과는 마지막
    non-None 값이며(모두 None 이면 초기값 유지), 병렬 fan-out 에서 동일 값이 여러 번
    echo 되어도 값이 증폭되지 않는다. 따라서 refine_count/completed_waves 는 정확한
    카운트를 유지한다.

대상 코드(실측):
- `ai_engine/agent_system/graph_state.py` 의 `_take_right(left, right)`:
    · right 가 None 이 아니면 right, 아니면 left 를 반환(last-wins, None 보존).

전략:
- Gateway/네트워크 불필요. 순수 함수만 검증. hypothesis max_examples=100, deadline=None.

실행:
  ai_engine/.venv/bin/python -m pytest scripts/test_take_right_reducer_pbt.py -q
"""
from __future__ import annotations

import functools
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from hypothesis import given, settings, strategies as st

from ai_engine.agent_system.graph_state import _take_right


def _reduce_sequence(initial, values):
    """초기값에서 시작해 값 시퀀스를 _take_right 로 순차 병합한 결과."""
    acc = initial
    for v in values:
        acc = _take_right(acc, v)
    return acc


def _expected_last_wins(initial, values):
    """기대값: 마지막 non-None 값, 모두 None 이면 initial 유지."""
    acc = initial
    for v in values:
        if v is not None:
            acc = v
    return acc


# ── 예시(단위) 테스트 ──────────────────────────────────────────────────────
def test_take_right_prefers_right():
    assert _take_right(1, 2) == 2
    assert _take_right("a", "b") == "b"


def test_take_right_none_preserves_left():
    assert _take_right(5, None) == 5
    assert _take_right(None, None) is None
    assert _take_right(None, 7) == 7


def test_take_right_zero_is_not_none():
    # 0 은 falsy 지만 None 이 아니므로 반드시 채택되어야 한다(카운터 정확성 핵심).
    assert _take_right(3, 0) == 0
    assert _take_right(0, None) == 0


def test_echo_does_not_amplify_counter():
    """동일 카운터 값이 여러 번 echo 되어도 값이 증폭되지 않는다(요구사항 2.3)."""
    # refine_count=1 이 병렬 fan-out 에서 4번 echo 되는 상황 시뮬레이션.
    result = _reduce_sequence(0, [1, 1, 1, 1])
    assert result == 1  # 4 로 증폭되지 않음


# ── 속성(PBT) 테스트 ───────────────────────────────────────────────────────
@settings(max_examples=100, deadline=None)
@given(
    initial=st.one_of(st.none(), st.integers()),
    values=st.lists(st.one_of(st.none(), st.integers()), max_size=20),
)
def test_last_wins_equals_last_non_none(initial, values):
    """Property 13: 순차 적용 결과는 마지막 non-None 값(모두 None 이면 초기값)."""
    assert _reduce_sequence(initial, values) == _expected_last_wins(initial, values)


@settings(max_examples=100, deadline=None)
@given(
    value=st.integers(),
    echo_count=st.integers(min_value=1, max_value=32),
)
def test_echo_immunity_no_amplification(value, echo_count):
    """Property 13: 동일 값 N회 echo 시 결과는 그 값 그대로(증폭 없음)."""
    result = _reduce_sequence(None, [value] * echo_count)
    assert result == value  # echo_count 배로 증폭되지 않음


@settings(max_examples=100, deadline=None)
@given(
    initial=st.integers(),
    trailing_nones=st.integers(min_value=0, max_value=10),
    last_value=st.integers(),
)
def test_trailing_nones_preserve_last_value(initial, trailing_nones, last_value):
    """Property 13: 마지막 실제 값 뒤에 None 이 이어져도 그 값이 유지된다."""
    seq = [last_value] + [None] * trailing_nones
    assert _reduce_sequence(initial, seq) == last_value


@settings(max_examples=100, deadline=None)
@given(values=st.lists(st.one_of(st.none(), st.integers()), max_size=20))
def test_associative_fold_matches_reduce(values):
    """Property 13: functools.reduce 로 좌결합 폴딩한 결과와 일치(결정론)."""
    if not values:
        return
    folded = functools.reduce(_take_right, values)
    assert folded == _reduce_sequence(values[0], values[1:])
