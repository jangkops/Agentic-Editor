# Feature: langgraph-reasoning-upgrade, Property 10: fan-out 상한 및 Wave 선택
"""Property 10: fan-out 상한 및 Wave 선택 — Hypothesis 기반 PBT.

Validates: Requirements 4.3, 4.4, 4.6

design.md Correctness Property 10 발췌:
    For any 임의 크기의 plan 과 임의의 completed_waves 인덱스에 대해, `plan_dispatch` 가
    생성하는 Send 수는 AE_MAX_PARALLEL_TASKS 이하이며, 각 Send 의 도메인은 유효한
    서브그래프 라우트이고, DAG 활성 시 현재 Wave 에 속한 서브태스크만 dispatch 된다.

대상 코드(실측):
- ai_engine/agent_system/supervisor.py 의 plan_dispatch(state).
- ai_engine/agent_system/dag.py 의 sanitize_depends_on / topological_waves(기대값 계산).

실행:
  ai_engine/.venv/bin/python -m pytest scripts/test_plan_dispatch_pbt.py -q
"""
from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from hypothesis import given, settings, strategies as st
from langchain_core.messages import HumanMessage

from ai_engine.agent_system.dag import sanitize_depends_on, topological_waves
from ai_engine.agent_system.supervisor import (
    MAX_PARALLEL_TASKS,
    _SUBGRAPH_ROUTES,
    plan_dispatch,
)


def _dispatch(state, dag_on):
    """AE_ENABLE_DAG_PLANNER 를 지정해 plan_dispatch 실행 후 env 복원."""
    prev = os.environ.get("AE_ENABLE_DAG_PLANNER")
    os.environ["AE_ENABLE_DAG_PLANNER"] = "1" if dag_on else "0"
    try:
        return plan_dispatch(state)
    finally:
        if prev is None:
            os.environ.pop("AE_ENABLE_DAG_PLANNER", None)
        else:
            os.environ["AE_ENABLE_DAG_PLANNER"] = prev


# ── 생성기: 비순환 plan(고유 subtask 텍스트로 역추적 가능) ────────────────────
@st.composite
def acyclic_plan(draw):
    count = draw(st.integers(min_value=1, max_value=8))
    items = []
    for i in range(count):
        if i == 0:
            deps = []
        else:
            deps = draw(
                st.lists(
                    st.sampled_from([f"t{j}" for j in range(i)]),
                    max_size=i,
                    unique=True,
                )
            )
        items.append(
            {
                "id": f"t{i}",
                "domain": draw(st.sampled_from(list(_SUBGRAPH_ROUTES))),
                "subtask": f"task-{i}",  # 고유 텍스트
                "depends_on": deps,
            }
        )
    return items


def _dispatched_subtasks(sends):
    """각 Send 의 worker_state 마지막 메시지(HumanMessage) 내용 = subtask."""
    return [s.arg["messages"][-1].content for s in sends]


# ── 속성 테스트 ──────────────────────────────────────────────────────────────
@settings(max_examples=100, deadline=None)
@given(
    plan=acyclic_plan(),
    completed=st.integers(min_value=-1, max_value=10),
    dag_on=st.booleans(),
)
def test_send_count_bounded_and_domain_valid(plan, completed, dag_on):
    """Property 10: Send 수 ≤ MAX_PARALLEL_TASKS, 각 도메인은 유효 서브그래프 라우트."""
    state = {
        "plan": plan,
        "completed_waves": completed,
        "prompt": "p",
        "messages": [HumanMessage(content="ctx")],  # 후속 Wave 컨텍스트 제공
    }
    sends = _dispatch(state, dag_on)
    assert isinstance(sends, list)
    assert len(sends) <= MAX_PARALLEL_TASKS
    for s in sends:
        assert s.node in _SUBGRAPH_ROUTES


@settings(max_examples=100, deadline=None)
@given(plan=acyclic_plan())
def test_dag_dispatches_only_first_wave_when_completed_zero(plan):
    """Property 10: DAG 활성 + completed=0 → Wave 0 서브태스크만 dispatch(순서 보존)."""
    state = {"plan": plan, "completed_waves": 0, "prompt": "p"}
    sends = _dispatch(state, dag_on=True)
    waves = topological_waves(sanitize_depends_on(plan))
    expected = [it["subtask"] for it in waves[0][:MAX_PARALLEL_TASKS]]
    assert _dispatched_subtasks(sends) == expected


@settings(max_examples=100, deadline=None)
@given(plan=acyclic_plan(), data=st.data())
def test_dag_selects_wave_at_completed_index(plan, data):
    """Property 10: DAG 활성 시 completed_waves 인덱스의 Wave 서브태스크만 dispatch."""
    waves = topological_waves(sanitize_depends_on(plan))
    idx = data.draw(st.integers(min_value=0, max_value=len(waves) - 1))
    state = {
        "plan": plan,
        "completed_waves": idx,
        "prompt": "p",
        "messages": [HumanMessage(content="ctx")],
    }
    sends = _dispatch(state, dag_on=True)
    expected = [it["subtask"] for it in waves[idx][:MAX_PARALLEL_TASKS]]
    assert _dispatched_subtasks(sends) == expected


@settings(max_examples=100, deadline=None)
@given(plan=acyclic_plan())
def test_dag_off_dispatches_whole_plan_single_wave(plan):
    """Property 10(무회귀): DAG 비활성 시 전체 plan(cap 이내)을 단일 Wave 로 fan-out."""
    state = {"plan": plan, "completed_waves": 0, "prompt": "p"}
    sends = _dispatch(state, dag_on=False)
    expected = [it["subtask"] for it in plan[:MAX_PARALLEL_TASKS]]
    assert _dispatched_subtasks(sends) == expected


# ── 예시(단위) 테스트 ─────────────────────────────────────────────────────────
def test_out_of_range_completed_returns_empty():
    plan = [{"id": "t0", "domain": "coding", "subtask": "a", "depends_on": []}]
    state = {"plan": plan, "completed_waves": 5, "prompt": "p", "messages": [HumanMessage(content="c")]}
    assert _dispatch(state, dag_on=True) == []


def test_subsequent_wave_without_context_returns_empty():
    plan = [
        {"id": "t0", "domain": "coding", "subtask": "a", "depends_on": []},
        {"id": "t1", "domain": "media", "subtask": "b", "depends_on": ["t0"]},
    ]
    # completed=1 (후속 Wave)인데 messages 부재 → 빈 Send 종료(Req 4.5)
    state = {"plan": plan, "completed_waves": 1, "prompt": "p"}
    assert _dispatch(state, dag_on=True) == []


def test_linear_chain_waves_dispatch_sequentially():
    plan = [
        {"id": "t0", "domain": "coding", "subtask": "analyze", "depends_on": []},
        {"id": "t1", "domain": "media", "subtask": "make_ppt", "depends_on": ["t0"]},
    ]
    # Wave 0
    s0 = _dispatch({"plan": plan, "completed_waves": 0, "prompt": "p"}, dag_on=True)
    assert _dispatched_subtasks(s0) == ["analyze"]
    assert s0[0].node == "coding"
    # Wave 1 (컨텍스트 제공)
    s1 = _dispatch(
        {"plan": plan, "completed_waves": 1, "prompt": "p", "messages": [HumanMessage(content="ctx")]},
        dag_on=True,
    )
    assert _dispatched_subtasks(s1) == ["make_ppt"]
    assert s1[0].node == "media"


def test_empty_plan_falls_back_to_chat():
    sends = _dispatch({"plan": [], "completed_waves": 0, "prompt": "hi"}, dag_on=True)
    assert len(sends) == 1
    assert sends[0].node == "chat"
