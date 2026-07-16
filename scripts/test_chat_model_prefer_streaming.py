"""GatewayChatModel.prefer_streaming 회귀 테스트 (지연 최적화 — reasoning 메타 호출).

배경: /converse 는 toolConfig 동반 호출을 비동기 S3 잡 폴링으로 처리해 느리다(라이브 실측
converse 35s vs converse_stream_live 7.6s). reasoning 메타 노드(router/planner/evaluator/
aggregate)는 prefer_streaming=True 로 스트리밍 경로를 우선 사용한다.

검증:
- prefer_streaming=True → converse_stream_live 사용(정상 ALLOW 시). toolUse 파싱 동일.
- 스트리밍이 ERROR/빈 결과 → converse 로 폴백(무회귀).
- 스트리밍이 예외 → converse 로 폴백(무회귀).
- prefer_streaming=False(기본) → converse 만 사용(기존 경로 불변).

네트워크 불필요 — gateway 를 스텁으로 대체.
실행: ai_engine/.venv/bin/python -m pytest scripts/test_chat_model_prefer_streaming.py -q
"""
from __future__ import annotations

import asyncio
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from ai_engine.agent_system.chat_model_adapter import GatewayChatModel
from langchain_core.messages import HumanMessage

_TOOLUSE_OUT = {
    "decision": "ALLOW",
    "output": {"message": {"content": [
        {"toolUse": {"toolUseId": "t1", "name": "select_plan",
                     "input": {"subtasks": [{"id": "t1", "domain": "coding", "subtask": "a"}]}}}
    ]}},
}
_TEXT_OUT = {"decision": "ALLOW", "output": {"message": {"content": [{"text": "hi"}]}}}


class _StubGateway:
    """converse / converse_stream_live 호출을 기록하는 스텁."""

    def __init__(self, converse_ret=None, stream_ret=None, stream_raises=False):
        self.calls = []
        self._converse_ret = converse_ret or _TEXT_OUT
        self._stream_ret = stream_ret
        self._stream_raises = stream_raises

    async def converse(self, model_id, messages, system_prompt="", tool_config=None):
        self.calls.append("converse")
        return self._converse_ret

    async def converse_stream_live(self, model_id, messages, system_prompt="", tool_config=None):
        self.calls.append("stream")
        if self._stream_raises:
            raise RuntimeError("stream boom")
        return self._stream_ret


def _run(coro):
    return asyncio.run(coro)


def test_prefer_streaming_uses_stream_and_parses_tooluse():
    gw = _StubGateway(stream_ret=_TOOLUSE_OUT)
    llm = GatewayChatModel(gateway=gw, model_id="m", prefer_streaming=True)
    ai = _run(llm.ainvoke([HumanMessage(content="x")]))
    assert gw.calls == ["stream"]  # converse 미호출
    assert ai.tool_calls and ai.tool_calls[0]["name"] == "select_plan"


def test_streaming_error_falls_back_to_converse():
    gw = _StubGateway(converse_ret=_TOOLUSE_OUT, stream_ret={"decision": "ERROR", "error": "x"})
    llm = GatewayChatModel(gateway=gw, model_id="m", prefer_streaming=True)
    ai = _run(llm.ainvoke([HumanMessage(content="x")]))
    assert gw.calls == ["stream", "converse"]  # 스트리밍 실패 → converse 폴백
    assert ai.tool_calls and ai.tool_calls[0]["name"] == "select_plan"


def test_streaming_exception_falls_back_to_converse():
    gw = _StubGateway(converse_ret=_TEXT_OUT, stream_raises=True)
    llm = GatewayChatModel(gateway=gw, model_id="m", prefer_streaming=True)
    ai = _run(llm.ainvoke([HumanMessage(content="x")]))
    assert gw.calls == ["stream", "converse"]
    assert "hi" in ai.content


def test_default_uses_converse_only():
    gw = _StubGateway(converse_ret=_TEXT_OUT)
    llm = GatewayChatModel(gateway=gw, model_id="m")  # prefer_streaming 기본 False
    ai = _run(llm.ainvoke([HumanMessage(content="x")]))
    assert gw.calls == ["converse"]  # 스트리밍 미사용(기존 경로 불변)
    assert "hi" in ai.content


def test_streaming_empty_output_falls_back():
    gw = _StubGateway(converse_ret=_TOOLUSE_OUT, stream_ret={"decision": "ALLOW", "output": {}})
    llm = GatewayChatModel(gateway=gw, model_id="m", prefer_streaming=True)
    ai = _run(llm.ainvoke([HumanMessage(content="x")]))
    assert gw.calls == ["stream", "converse"]  # 빈 output → 폴백
    assert ai.tool_calls


if __name__ == "__main__":
    import pytest

    raise SystemExit(pytest.main([__file__, "-q"]))
