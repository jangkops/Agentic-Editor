# Feature: langgraph-reasoning-upgrade, Property 12: SSE 이벤트 키 부분집합
"""Property 12: SSE 이벤트 키 부분집합 — Hypothesis 기반 PBT.

Validates: Requirements 6.5

design.md Correctness Property 12 발췌:
    For any 신규 노드(evaluator/aggregate/planner)가 반환하는 상태 dict 에 대해, 그 키
    집합은 SSE_Bridge 가 이벤트로 변환할 수 있는 기존 GraphState 채널 집합의 부분집합이며,
    결과적으로 emit 되는 SSE 이벤트 키는 기존 집합
    {text, thinking, tool, status, verifiedFiles, type, taskId, heartbeat,
     answerQuality, qualityPending, error} 의 부분집합이다.

대상 코드(실측):
- ai_engine/agent_system/supervisor.py 의 make_planner_node/make_aggregate_node/
  make_evaluator_node 반환 dict.
- ai_engine/agent_system/graph_state.py 의 GraphState 채널.
- ai_engine/agent_system/sse_bridge.py 의 ALLOWED_EVENT_KEYS + emit 매핑.

실행:
  ai_engine/.venv/bin/python -m pytest scripts/test_sse_key_subset_pbt.py -q
"""
from __future__ import annotations

import asyncio
import os
import sys
from types import SimpleNamespace

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from hypothesis import given, settings, strategies as st
from langchain_core.messages import AIMessage, HumanMessage

import ai_engine.agent_system.supervisor as sup
from ai_engine.agent_system.graph_state import GraphState
from ai_engine.agent_system.sse_bridge import ALLOWED_EVENT_KEYS

# 기존 GraphState 채널 집합(신규 노드 반환 키는 이 집합의 부분집합이어야 함).
_GRAPH_STATE_CHANNELS = set(GraphState.__annotations__.keys())

# sse_bridge.graph_events_to_sse 가 정적으로 emit 하는 이벤트 키(실측). 모두 ALLOWED 부분집합.
_SSE_EMITTED_KEYS = {
    "text",
    "tool",
    "status",
    "type",
    "taskId",
    "verifiedFiles",
    "heartbeat",
    "error",
}


# ── Gateway mock (구성 가능한 content / tool_calls 반환) ──────────────────────
def _make_model_cls(*, content="", tool_calls=None):
    tcs = tool_calls or []

    class _FakeAI:
        def __init__(self):
            self.content = content
            self.tool_calls = tcs

    class _FakeBound:
        async def ainvoke(self, messages):
            return _FakeAI()

    class _FakeModel:
        def __init__(self, *a, **k):
            pass

        def bind_tools(self, *a, **k):
            return _FakeBound()

        async def ainvoke(self, messages):
            return _FakeAI()

    return _FakeModel


def _run_node(node_coro):
    return asyncio.run(node_coro)


def _assert_subset_of_channels(keys, label):
    extra = set(keys) - _GRAPH_STATE_CHANNELS
    assert not extra, f"{label} 반환 키가 GraphState 채널 밖: {extra}"


# ── 전략 ─────────────────────────────────────────────────────────────────────
_verified_files = st.lists(
    st.builds(
        lambda p: {"path": p, "absPath": "/abs/" + p, "tool": "gen"},
        st.text(min_size=1, max_size=8),
    ),
    max_size=4,
)
_refine = st.integers(min_value=0, max_value=1)  # cap 미만 유지(재계획 경로 포함)


# ── 속성 테스트: planner 반환 키 ─────────────────────────────────────────────
@settings(max_examples=100, deadline=None)
@given(prompt=st.text(min_size=1, max_size=40), vfs=_verified_files)
def test_planner_return_keys_subset(prompt, vfs):
    """planner 반환 키 ⊆ GraphState 채널."""
    subtasks = [{"id": "t0", "domain": "chat", "subtask": prompt, "depends_on": []}]
    model_cls = _make_model_cls(
        tool_calls=[{"name": "select_plan", "args": {"subtasks": subtasks}}]
    )
    orig = sup.GatewayChatModel
    sup.GatewayChatModel = model_cls
    try:
        deps = SimpleNamespace(gateway=object(), model_coding="m", model_planner="p")
        node = sup.make_planner_node(deps)
        # 최초 진입(plan 부재) → 새 plan 생성 경로.
        out = _run_node(node({"prompt": prompt, "verified_files": vfs}))
    finally:
        sup.GatewayChatModel = orig
    _assert_subset_of_channels(out.keys(), "planner")


def test_planner_wave_progress_return_keys_subset():
    """다중 Wave 진행(재진입) 경로 반환 키 ⊆ GraphState 채널."""
    plan = [
        {"id": "t0", "domain": "coding", "subtask": "a", "depends_on": []},
        {"id": "t1", "domain": "media", "subtask": "b", "depends_on": ["t0"]},
    ]
    prev = os.environ.get("AE_ENABLE_DAG_PLANNER")
    os.environ["AE_ENABLE_DAG_PLANNER"] = "1"
    try:
        deps = SimpleNamespace(gateway=None, model_planner="p")
        node = sup.make_planner_node(deps)
        out = _run_node(node({"prompt": "x", "plan": plan, "completed_waves": 0}))
    finally:
        if prev is None:
            os.environ.pop("AE_ENABLE_DAG_PLANNER", None)
        else:
            os.environ["AE_ENABLE_DAG_PLANNER"] = prev
    _assert_subset_of_channels(out.keys(), "planner(wave)")
    assert out == {"completed_waves": 1}


# ── 속성 테스트: aggregate 반환 키 ───────────────────────────────────────────
@settings(max_examples=100, deadline=None)
@given(text=st.text(min_size=1, max_size=40), vfs=_verified_files)
def test_aggregate_multi_worker_return_keys_subset(text, vfs):
    """aggregate(다중 워커) 반환 키 ⊆ GraphState 채널."""
    plan = [
        {"id": "t0", "domain": "coding", "subtask": "a", "depends_on": []},
        {"id": "t1", "domain": "media", "subtask": "b", "depends_on": []},
    ]
    model_cls = _make_model_cls(content=text)
    orig = sup.GatewayChatModel
    sup.GatewayChatModel = model_cls
    try:
        deps = SimpleNamespace(gateway=object(), model_generator="g")
        node = sup.make_aggregate_node(deps)
        state = {
            "prompt": "요청",
            "plan": plan,
            "messages": [AIMessage(content="w1"), AIMessage(content="w2")],
            "verified_files": vfs,
        }
        out = _run_node(node(state))
    finally:
        sup.GatewayChatModel = orig
    _assert_subset_of_channels(out.keys(), "aggregate")


def test_aggregate_single_worker_return_keys_subset():
    """aggregate(단일 워커) → {} 반환, 키 집합 공집합(부분집합 자명)."""
    deps = SimpleNamespace(gateway=object(), model_generator="g")
    node = sup.make_aggregate_node(deps)
    out = _run_node(node({"prompt": "x", "plan": [{"id": "t0", "domain": "chat", "subtask": "a"}]}))
    assert out == {}
    _assert_subset_of_channels(out.keys(), "aggregate(single)")


# ── 속성 테스트: evaluator 반환 키 ───────────────────────────────────────────
@settings(max_examples=100, deadline=None)
@given(refine=_refine, missing=st.lists(st.sampled_from(list(sup._ROUTE_LABELS)), max_size=3))
def test_evaluator_not_achieved_return_keys_subset(refine, missing):
    """evaluator(미달 재계획) 반환 키 ⊆ GraphState 채널."""
    model_cls = _make_model_cls(
        tool_calls=[{"name": "submit_evaluation", "args": {"achieved": False, "missing_domains": missing}}]
    )
    orig = sup.GatewayChatModel
    orig_cap = sup.AE_MAX_REFINE
    sup.AE_MAX_REFINE = 5  # cap 충분히 크게 → 재계획 경로(refine_count/messages 반환) 보장
    sup.GatewayChatModel = model_cls
    try:
        deps = SimpleNamespace(gateway=object(), model_evaluator="e")
        node = sup.make_evaluator_node(deps)
        state = {"prompt": "요청", "messages": [AIMessage(content="부분")], "refine_count": refine}
        out = _run_node(node(state))
    finally:
        sup.GatewayChatModel = orig
        sup.AE_MAX_REFINE = orig_cap
    _assert_subset_of_channels(out.keys(), "evaluator(refine)")


@settings(max_examples=100, deadline=None)
@given(refine=st.integers(min_value=0, max_value=1))
def test_evaluator_achieved_return_keys_subset(refine):
    """evaluator(달성) 반환 키 ⊆ GraphState 채널."""
    model_cls = _make_model_cls(
        tool_calls=[{"name": "submit_evaluation", "args": {"achieved": True, "reason": "완료"}}]
    )
    orig = sup.GatewayChatModel
    sup.GatewayChatModel = model_cls
    try:
        deps = SimpleNamespace(gateway=object(), model_evaluator="e")
        node = sup.make_evaluator_node(deps)
        out = _run_node(node({"prompt": "x", "messages": [AIMessage(content="완료")], "refine_count": refine}))
    finally:
        sup.GatewayChatModel = orig
    _assert_subset_of_channels(out.keys(), "evaluator(achieved)")


# ── 정적 검증: SSE emit 키가 허용 집합의 부분집합 ────────────────────────────
def test_sse_emitted_keys_subset_of_allowed():
    """graph_events_to_sse 가 emit 하는 이벤트 키가 ALLOWED_EVENT_KEYS 부분집합(Req 6.5)."""
    assert _SSE_EMITTED_KEYS.issubset(ALLOWED_EVENT_KEYS)


def test_allowed_event_keys_frozen_contract():
    """허용 이벤트 키 집합이 요구사항 6.5 계약과 정확히 일치(회귀 방지)."""
    expected = {
        "text", "thinking", "tool", "status", "verifiedFiles", "type",
        "taskId", "heartbeat", "answerQuality", "qualityPending", "error",
    }
    assert set(ALLOWED_EVENT_KEYS) == expected


if __name__ == "__main__":
    import pytest

    raise SystemExit(pytest.main([__file__, "-q"]))
