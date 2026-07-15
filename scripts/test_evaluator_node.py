# Feature: langgraph-reasoning-upgrade, Task 8.2: evaluator 타임아웃/실패 시 achieved 단위 테스트
"""Evaluator 노드 단위 테스트 (gateway mock, 네트워크 없음).

Validates: Requirements 1.6, 2.2

대상 코드(실측):
- ai_engine/agent_system/supervisor.py 의 make_evaluator_node(deps), evaluator_selector.

검증 시나리오:
- LLM 타임아웃/예외 → achieved=True, selector "done" 라우팅 (Req 1.6).
- refine_count >= cap → LLM 미호출, achieved=True (Req 2.2).
- 달성 판정 → END, refine_count 미증가.
- 미달 & cap 미만 → refine_count +1, 교정 지시 HumanMessage, selector "planner".

실행:
  ai_engine/.venv/bin/python -m pytest scripts/test_evaluator_node.py -q
"""
from __future__ import annotations

import asyncio
import os
import sys
from types import SimpleNamespace

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from langchain_core.messages import AIMessage, HumanMessage

import ai_engine.agent_system.supervisor as sup


# ── Gateway mock ─────────────────────────────────────────────────────────────
class _CallCounter:
    def __init__(self):
        self.n = 0


def _make_model_cls(counter, *, mode="success", tool_args=None):
    class _FakeAI:
        def __init__(self, args):
            self.tool_calls = [{"name": "submit_evaluation", "args": args}] if args is not None else []
            self.content = ""

    class _FakeBound:
        def __init__(self, args):
            self._args = args

        async def ainvoke(self, messages):
            counter.n += 1
            if mode == "timeout":
                raise asyncio.TimeoutError()
            if mode == "error":
                raise RuntimeError("gateway boom")
            return _FakeAI(self._args)

    class _FakeModel:
        def __init__(self, *a, **k):
            pass

        def bind_tools(self, *a, **k):
            return _FakeBound(tool_args)

    return _FakeModel


def _run_eval(state, *, mode="success", tool_args=None):
    counter = _CallCounter()
    orig = sup.GatewayChatModel
    sup.GatewayChatModel = _make_model_cls(counter, mode=mode, tool_args=tool_args)
    try:
        deps = SimpleNamespace(gateway=object(), model_evaluator="m")
        node = sup.make_evaluator_node(deps)
        result = asyncio.run(node(state))
        return result, counter.n
    finally:
        sup.GatewayChatModel = orig


# ── 단위 테스트 ──────────────────────────────────────────────────────────────
def test_timeout_returns_achieved_and_done():
    """Req 1.6: LLM 타임아웃 → achieved=True, selector done."""
    state = {"prompt": "요청", "messages": [AIMessage(content="A")], "refine_count": 0}
    result, calls = _run_eval(state, mode="timeout")
    assert calls == 1
    assert result["evaluation"]["achieved"] is True
    assert "refine_count" not in result  # 재계획 없음
    # selector 는 반환 상태를 병합한 뒤 평가 — achieved True → done
    assert sup.evaluator_selector({**state, **result}) == "done"


def test_exception_returns_achieved_and_done():
    """Req 1.6: LLM 예외 → achieved=True, selector done."""
    state = {"prompt": "요청", "messages": [AIMessage(content="A")], "refine_count": 1}
    result, calls = _run_eval(state, mode="error")
    assert calls == 1
    assert result["evaluation"]["achieved"] is True
    assert sup.evaluator_selector({**state, **result}) == "done"


def test_cap_reached_skips_llm():
    """Req 2.2: refine_count >= cap → LLM 미호출, achieved=True."""
    state = {"prompt": "요청", "messages": [AIMessage(content="A")], "refine_count": sup.AE_MAX_REFINE}
    result, calls = _run_eval(state, mode="success", tool_args={"achieved": False, "missing_domains": ["media"]})
    assert calls == 0  # LLM 미호출
    assert result["evaluation"]["achieved"] is True
    assert "refine_count" not in result
    assert sup.evaluator_selector({**state, **result}) == "done"


def test_gateway_none_skips_llm():
    """gateway=None → achieved=True(비차단)."""
    deps = SimpleNamespace(gateway=None, model_evaluator="m")
    node = sup.make_evaluator_node(deps)
    result = asyncio.run(node({"prompt": "x", "refine_count": 0}))
    assert result["evaluation"]["achieved"] is True


def test_achieved_true_ends_without_refine():
    """달성 판정 → END, refine_count 미증가."""
    state = {"prompt": "요청", "messages": [AIMessage(content="완료")], "refine_count": 0}
    result, calls = _run_eval(state, mode="success", tool_args={"achieved": True, "reason": "완료"})
    assert calls == 1
    assert result["evaluation"]["achieved"] is True
    assert "refine_count" not in result
    assert sup.evaluator_selector({**state, **result}) == "done"


def test_not_achieved_below_cap_replans():
    """미달 & cap 미만 → refine_count +1, 교정 지시, selector planner."""
    state = {"prompt": "코드 분석 후 PPT", "messages": [AIMessage(content="분석만 완료")], "refine_count": 0}
    result, calls = _run_eval(
        state,
        mode="success",
        tool_args={"achieved": False, "reason": "PPT 미생성", "missing_domains": ["media"]},
    )
    assert calls == 1
    assert result["evaluation"]["achieved"] is False
    assert result["refine_count"] == 1  # 입력 0 + 1
    assert len(result["messages"]) == 1
    assert isinstance(result["messages"][0], HumanMessage)
    assert "media" in result["messages"][0].content
    assert sup.evaluator_selector({**state, **result}) == "planner"


def test_refine_count_increments_from_input():
    """미달 시 반환 refine_count = 입력 + 1 (입력 1 → 2, 단 cap 이상이면 selector done)."""
    # cap 을 3 으로 올려 planner 라우팅 확인
    orig_cap = sup.AE_MAX_REFINE
    sup.AE_MAX_REFINE = 3
    try:
        state = {"prompt": "요청", "messages": [AIMessage(content="부분")], "refine_count": 1}
        result, _ = _run_eval(
            state, mode="success", tool_args={"achieved": False, "missing_domains": ["ops"]}
        )
        assert result["refine_count"] == 2
        assert sup.evaluator_selector({**state, **result}) == "planner"
    finally:
        sup.AE_MAX_REFINE = orig_cap


if __name__ == "__main__":
    import pytest

    raise SystemExit(pytest.main([__file__, "-q"]))
