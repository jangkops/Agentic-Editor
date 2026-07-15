# Feature: langgraph-reasoning-upgrade, Property 9: 계획 파싱 스키마 견고성
"""Property 9: 계획 파싱 스키마 견고성 — Hypothesis 기반 PBT.

Validates: Requirements 4.1

design.md Correctness Property 9 발췌:
    For any DAG_Planner LLM 응답의 subtasks 배열에 대해, `_make_plan` 파싱 결과의 각
    항목은 비어있지 않은 id(누락 시 인덱스 기반 보정), 유효 domain, 문자열 subtask,
    리스트 depends_on 을 항상 보유한다.

대상 코드(실측):
- ai_engine/agent_system/supervisor.py 의 _make_plan(state, deps).
  GatewayChatModel 을 mock 하여 네트워크 없이 결정론적으로 subtasks 응답을 주입한다.

실행:
  ai_engine/.venv/bin/python -m pytest scripts/test_make_plan_schema_pbt.py -q
"""
from __future__ import annotations

import asyncio
import os
import sys
from types import SimpleNamespace

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from hypothesis import given, settings, strategies as st

import ai_engine.agent_system.supervisor as sup
from ai_engine.agent_system.supervisor import MAX_PARALLEL_TASKS, _ROUTE_LABELS


# ── Gateway mock ─────────────────────────────────────────────────────────────
class _FakeAI:
    def __init__(self, tool_calls):
        self.tool_calls = tool_calls
        self.content = ""


class _FakeBound:
    def __init__(self, ai):
        self._ai = ai

    async def ainvoke(self, messages):
        return self._ai


def _make_fake_model_cls(ai):
    class _FakeModel:
        def __init__(self, *a, **k):
            pass

        def bind_tools(self, *a, **k):
            return _FakeBound(ai)

    return _FakeModel


def _run_make_plan(subtasks_arg):
    """subtasks_arg 를 tool_calls 로 주입한 mock LLM 으로 _make_plan 실행."""
    ai = _FakeAI(tool_calls=[{"name": "select_plan", "args": {"subtasks": subtasks_arg}}])
    orig = sup.GatewayChatModel
    sup.GatewayChatModel = _make_fake_model_cls(ai)
    try:
        deps = SimpleNamespace(gateway=object(), model_coding="m")
        state = {"prompt": "코드 분석하고 PPT 만들기"}
        return asyncio.run(sup._make_plan(state, deps))
    finally:
        sup.GatewayChatModel = orig


# ── 전략(생성기) ─────────────────────────────────────────────────────────────
_domain_tokens = list(_ROUTE_LABELS) + ["bogus", "", "done", "planner"]
_id_values = st.one_of(st.text(max_size=6), st.none(), st.just(""), st.integers())
_subtask_values = st.one_of(st.text(max_size=12), st.none(), st.just("   "), st.integers())
_depends_values = st.one_of(
    st.lists(st.text(max_size=4), max_size=4),
    st.none(),
    st.text(max_size=6),           # 무효 타입
    st.lists(st.integers(), max_size=3),  # 원소 무효 타입
)


@st.composite
def subtask_item(draw):
    d = {}
    if draw(st.booleans()):
        d["id"] = draw(_id_values)
    d["domain"] = draw(st.sampled_from(_domain_tokens))
    if draw(st.booleans()):
        d["subtask"] = draw(_subtask_values)
    if draw(st.booleans()):
        d["depends_on"] = draw(_depends_values)
    return d


@st.composite
def subtasks_array(draw):
    items = draw(st.lists(subtask_item(), max_size=8))
    # 가끔 비-dict 원소를 섞어 방어성 검증
    if draw(st.booleans()) and items:
        items.append(draw(st.one_of(st.text(max_size=3), st.integers(), st.none())))
    return items


# ── 속성 테스트 ──────────────────────────────────────────────────────────────
@settings(max_examples=100, deadline=None)
@given(subtasks=subtasks_array())
def test_every_plan_item_has_valid_schema(subtasks):
    """Property 9: 파싱 결과 각 항목이 id/domain/subtask/depends_on 계약을 보유."""
    plan = _run_make_plan(subtasks)
    assert isinstance(plan, list)
    assert len(plan) >= 1  # 유효 항목 없으면 폴백 단일 항목
    assert len(plan) <= MAX_PARALLEL_TASKS
    for item in plan:
        assert isinstance(item, dict)
        # 비어있지 않은 id
        assert isinstance(item["id"], str) and item["id"] != ""
        # 유효 domain
        assert item["domain"] in _ROUTE_LABELS
        # 문자열 subtask
        assert isinstance(item["subtask"], str)
        # 리스트 depends_on
        assert isinstance(item["depends_on"], list)
        assert all(isinstance(d, str) for d in item["depends_on"])


@settings(max_examples=100, deadline=None)
@given(subtasks=subtasks_array())
def test_plan_ids_are_nonempty_strings(subtasks):
    """Property 9: id 누락/무효 시 인덱스 기반 보정으로 항상 비어있지 않음."""
    plan = _run_make_plan(subtasks)
    ids = [item["id"] for item in plan]
    assert all(isinstance(i, str) and i for i in ids)


# ── 예시(단위) 테스트 ─────────────────────────────────────────────────────────
def test_missing_id_is_backfilled():
    plan = _run_make_plan(
        [
            {"domain": "coding", "subtask": "분석"},
            {"domain": "media", "subtask": "PPT", "depends_on": ["t0"]},
        ]
    )
    assert plan[0]["id"] == "t0"
    assert plan[1]["id"] == "t1"
    assert plan[1]["depends_on"] == ["t0"]


def test_invalid_items_filtered_then_fallback():
    # 유효 항목이 하나도 없으면 폴백 단일 항목 반환
    plan = _run_make_plan([{"domain": "bogus", "subtask": "x"}, {"domain": "coding"}])
    assert len(plan) == 1
    assert plan[0]["id"] == "t0"
    assert plan[0]["domain"] in _ROUTE_LABELS
    assert plan[0]["depends_on"] == []


def test_provided_id_preserved():
    plan = _run_make_plan([{"id": "analyze", "domain": "coding", "subtask": "분석"}])
    assert plan[0]["id"] == "analyze"
