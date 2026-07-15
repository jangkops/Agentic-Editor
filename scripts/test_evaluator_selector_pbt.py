# Feature: langgraph-reasoning-upgrade, Property 11: Evaluator 라우팅 및 재계획 카운트 정확성
"""Property 11: Evaluator 라우팅 및 재계획 카운트 정확성 — Hypothesis 기반 PBT.

Validates: Requirements 1.3, 1.4, 2.2

design.md Correctness Property 11 발췌:
    For any 임의의 evaluation 결과와 refine_count 에 대해, evaluator_selector 는
    achieved=True 이거나 refine_count 가 cap 에 도달하면 "done"을, achieved=False 이고
    refine_count 가 cap 미만이면 "planner"를 반환한다. "planner"로 라우팅되는 경우
    evaluator_node 가 반환하는 refine_count 는 입력값 + 1 이다.

대상 코드(실측):
- ai_engine/agent_system/supervisor.py 의 evaluator_selector, make_evaluator_node.

실행:
  ai_engine/.venv/bin/python -m pytest scripts/test_evaluator_selector_pbt.py -q
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
from ai_engine.agent_system.supervisor import evaluator_selector


# ── 전략(생성기) ─────────────────────────────────────────────────────────────
_evaluation_values = st.one_of(
    st.none(),
    st.just({}),
    st.builds(
        lambda a, r, m: {"achieved": a, "reason": r, "missing_domains": m},
        st.booleans(),
        st.text(max_size=10),
        st.lists(st.sampled_from(list(sup._ROUTE_LABELS)), max_size=3),
    ),
    st.builds(lambda a: {"achieved": a}, st.booleans()),
)
_refine_values = st.integers(min_value=0, max_value=8)


# ── 속성 테스트: selector 라우팅 정확성 ───────────────────────────────────────
@settings(max_examples=100, deadline=None)
@given(evaluation=_evaluation_values, refine=_refine_values)
def test_selector_routing_matches_spec(evaluation, refine):
    """Property 11: achieved=True 또는 refine>=cap → done, 그 외 → planner."""
    cap = sup.AE_MAX_REFINE
    state = {"evaluation": evaluation, "refine_count": refine}
    result = evaluator_selector(state)
    assert result in ("done", "planner")

    ev = evaluation or {}
    achieved = bool(ev.get("achieved", True))  # 미설정/빈 dict → 안전 종료(True)
    if achieved or refine >= cap:
        assert result == "done"
    else:
        assert result == "planner"


@settings(max_examples=100, deadline=None)
@given(refine=_refine_values)
def test_missing_evaluation_defaults_done(refine):
    """evaluation 미설정 → 안전하게 done(비차단 종료)."""
    assert evaluator_selector({"refine_count": refine}) == "done"


@settings(max_examples=100, deadline=None)
@given(achieved=st.booleans())
def test_cap_reached_always_done(achieved):
    """refine_count >= cap 이면 achieved 무관 done (Req 2.2)."""
    cap = sup.AE_MAX_REFINE
    state = {"evaluation": {"achieved": achieved}, "refine_count": cap}
    assert evaluator_selector(state) == "done"


# ── Gateway mock (미달 응답 주입) ─────────────────────────────────────────────
def _make_not_achieved_model_cls(missing):
    class _FakeAI:
        tool_calls = [{"name": "submit_evaluation", "args": {"achieved": False, "missing_domains": missing}}]
        content = ""

    class _FakeBound:
        async def ainvoke(self, messages):
            return _FakeAI()

    class _FakeModel:
        def __init__(self, *a, **k):
            pass

        def bind_tools(self, *a, **k):
            return _FakeBound()

    return _FakeModel


@settings(max_examples=100, deadline=None)
@given(refine=st.integers(min_value=0, max_value=10), missing=st.lists(st.sampled_from(list(sup._ROUTE_LABELS)), max_size=3))
def test_planner_route_increments_refine_count(refine, missing):
    """Property 11: 미달 & cap 미만 시 evaluator_node 반환 refine_count = 입력 + 1."""
    orig = sup.GatewayChatModel
    orig_cap = sup.AE_MAX_REFINE
    # cap 을 충분히 크게 두어 미달 경로(refine_count+1 반환)를 항상 통과시킨다.
    sup.AE_MAX_REFINE = refine + 5
    sup.GatewayChatModel = _make_not_achieved_model_cls(missing)
    try:
        deps = SimpleNamespace(gateway=object(), model_evaluator="m")
        node = sup.make_evaluator_node(deps)
        state = {"prompt": "요청", "messages": [AIMessage(content="부분")], "refine_count": refine}
        result = asyncio.run(node(state))
        # 미달 → refine_count +1, planner 라우팅
        assert result["evaluation"]["achieved"] is False
        assert result["refine_count"] == refine + 1
        assert any(isinstance(m, HumanMessage) for m in result.get("messages", []))
        assert evaluator_selector({**state, **result}) == "planner"
    finally:
        sup.GatewayChatModel = orig
        sup.AE_MAX_REFINE = orig_cap


if __name__ == "__main__":
    import pytest

    raise SystemExit(pytest.main([__file__, "-q"]))
