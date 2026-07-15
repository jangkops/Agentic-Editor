# Feature: langgraph-reasoning-upgrade, Task 7.2: aggregate 비차단/단일 워커 스킵 단위 테스트
"""Aggregate 노드 단위 테스트 (gateway mock, 네트워크 없음).

Validates: Requirements 3.5, 3.7

대상 코드(실측):
- ai_engine/agent_system/supervisor.py 의 make_aggregate_node(deps).

검증 시나리오:
- 워커 1개(plan 길이 <=1) → LLM 미호출, {} 반환 (Req 3.7).
- 워커 여러 개 + LLM 예외/타임아웃 → {} 반환, verified_files 보존 (Req 3.5).
- 워커 여러 개 + LLM 성공 → {"final_text","messages"} 반환.

실행:
  ai_engine/.venv/bin/python -m pytest scripts/test_aggregate_node.py -q
"""
from __future__ import annotations

import asyncio
import os
import sys
from types import SimpleNamespace

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from langchain_core.messages import AIMessage

import ai_engine.agent_system.supervisor as sup


# ── Gateway mock ─────────────────────────────────────────────────────────────
class _CallCounter:
    def __init__(self):
        self.n = 0


def _make_model_cls(counter, *, raise_exc=None, timeout=False, content="종합된 최종 답변"):
    class _FakeModel:
        def __init__(self, *a, **k):
            pass

        def bind_tools(self, *a, **k):
            return self

        async def ainvoke(self, messages):
            counter.n += 1
            if timeout:
                raise asyncio.TimeoutError()
            if raise_exc is not None:
                raise raise_exc
            return AIMessage(content=content)

    return _FakeModel


def _run_aggregate(state, *, raise_exc=None, timeout=False, content="종합된 최종 답변"):
    counter = _CallCounter()
    orig = sup.GatewayChatModel
    sup.GatewayChatModel = _make_model_cls(
        counter, raise_exc=raise_exc, timeout=timeout, content=content
    )
    try:
        deps = SimpleNamespace(gateway=object(), model_generator="m")
        node = sup.make_aggregate_node(deps)
        result = asyncio.run(node(state))
        return result, counter.n
    finally:
        sup.GatewayChatModel = orig


# ── 단위 테스트 ──────────────────────────────────────────────────────────────
def test_single_worker_skips_llm():
    """Req 3.7: 워커 1개(plan 길이 1)면 LLM 미호출, {} 반환."""
    state = {
        "prompt": "코드 리팩터",
        "plan": [{"id": "t0", "domain": "coding", "subtask": "리팩터", "depends_on": []}],
        "messages": [AIMessage(content="완료")],
        "verified_files": [{"path": "a.py", "absPath": "/abs/a.py", "tool": "edit"}],
    }
    result, calls = _run_aggregate(state)
    assert result == {}
    assert calls == 0


def test_empty_plan_skips_llm():
    """plan 부재 → 종합 불필요, {} 반환, LLM 미호출."""
    state = {"prompt": "x", "messages": [AIMessage(content="완료")]}
    result, calls = _run_aggregate(state)
    assert result == {}
    assert calls == 0


def test_gateway_none_skips_llm():
    """gateway=None → LLM 스킵, {} 반환."""
    deps = SimpleNamespace(gateway=None, model_generator="m")
    node = sup.make_aggregate_node(deps)
    state = {
        "prompt": "x",
        "plan": [
            {"id": "t0", "domain": "coding", "subtask": "a", "depends_on": []},
            {"id": "t1", "domain": "media", "subtask": "b", "depends_on": []},
        ],
        "messages": [AIMessage(content="A"), AIMessage(content="B")],
    }
    result = asyncio.run(node(state))
    assert result == {}


def test_multi_worker_llm_exception_preserves_state():
    """Req 3.5: 여러 워커 + LLM 예외 → {} 반환(verified_files 보존, 비차단)."""
    vf = [{"path": "deck.pptx", "absPath": "/abs/deck.pptx", "tool": "pptx"}]
    state = {
        "prompt": "코드 분석 후 PPT",
        "plan": [
            {"id": "t0", "domain": "coding", "subtask": "분석", "depends_on": []},
            {"id": "t1", "domain": "media", "subtask": "PPT", "depends_on": ["t0"]},
        ],
        "messages": [AIMessage(content="분석 결과"), AIMessage(content="PPT 생성")],
        "verified_files": vf,
    }
    result, calls = _run_aggregate(state, raise_exc=RuntimeError("gateway boom"))
    assert result == {}  # 비차단 폴백 — verified_files 미삭제(빈 반환이므로 기존 유지)
    assert calls == 1  # LLM 은 호출되었으나 실패


def test_multi_worker_llm_timeout_preserves_state():
    """Req 3.5: 여러 워커 + LLM 타임아웃 → {} 반환(비차단)."""
    state = {
        "prompt": "멀티 도메인",
        "plan": [
            {"id": "t0", "domain": "coding", "subtask": "a", "depends_on": []},
            {"id": "t1", "domain": "research", "subtask": "b", "depends_on": []},
        ],
        "messages": [AIMessage(content="A"), AIMessage(content="B")],
        "verified_files": [{"path": "r.md", "absPath": "/abs/r.md", "tool": "write"}],
    }
    result, calls = _run_aggregate(state, timeout=True)
    assert result == {}
    assert calls == 1


def test_multi_worker_success_synthesizes():
    """여러 워커 + LLM 성공 → final_text + messages 반환."""
    state = {
        "prompt": "코드 분석 후 PPT",
        "plan": [
            {"id": "t0", "domain": "coding", "subtask": "분석", "depends_on": []},
            {"id": "t1", "domain": "media", "subtask": "PPT", "depends_on": ["t0"]},
        ],
        "messages": [AIMessage(content="분석 완료"), AIMessage(content="PPT 완료")],
        "verified_files": [{"path": "deck.pptx", "absPath": "/abs/deck.pptx", "tool": "pptx"}],
    }
    result, calls = _run_aggregate(state, content="통합 최종 답변")
    assert calls == 1
    assert result["final_text"] == "통합 최종 답변"
    assert len(result["messages"]) == 1
    assert isinstance(result["messages"][0], AIMessage)
    assert result["messages"][0].content == "통합 최종 답변"
    # aggregate 는 verified_files 를 반환하지 않음(reducer 가 기존 병합 유지 — 삭제 없음).
    assert "verified_files" not in result


def test_multi_worker_empty_llm_output_falls_back():
    """LLM 이 빈 텍스트를 반환하면 {} 반환(기존 결과 유지)."""
    state = {
        "prompt": "x",
        "plan": [
            {"id": "t0", "domain": "coding", "subtask": "a", "depends_on": []},
            {"id": "t1", "domain": "chat", "subtask": "b", "depends_on": []},
        ],
        "messages": [AIMessage(content="A"), AIMessage(content="B")],
    }
    result, calls = _run_aggregate(state, content="   ")
    assert result == {}
    assert calls == 1


if __name__ == "__main__":
    import pytest

    raise SystemExit(pytest.main([__file__, "-q"]))
