"""GatewayChatModel._astream 의 스트리밍 실패 → converse 폴백 회귀 테스트.

배경: 일부 모델(예: Opus)은 게이트웨이 스트리밍 엔드포인트 미지원 → 스트림이 에러/무응답으로
끝난다. 이때 _astream 이 converse(비스트리밍, async 잡 경로 포함)로 폴백해 산출물을 확보한다.
정상 스트리밍(콘텐츠 방출)에서는 폴백이 트리거되지 않아 기존 동작 불변(무손상).

검증:
- 스트림 error 이벤트(콘텐츠 방출 전) → converse 폴백, tool_calls/텍스트 복구.
- 스트림 콘텐츠 없이 종료(무응답) → converse 폴백.
- 정상 스트리밍(text 방출) → converse 미호출(폴백 없음, 무손상).
- 스트림이 콘텐츠 방출 후 error → 예외 전파(부분 스트림 복구 불가).

네트워크 불필요 — gateway 스텁.
실행: ai_engine/.venv/bin/python -m pytest scripts/test_astream_converse_fallback.py -q
"""
from __future__ import annotations

import asyncio
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from ai_engine.agent_system.chat_model_adapter import GatewayChatModel, GatewayModelError
from langchain_core.messages import HumanMessage


class _StubGateway:
    def __init__(self, events, converse_ret=None):
        self._events = events
        self.calls = []
        self._converse_ret = converse_ret or {
            "decision": "ALLOW",
            "output": {"message": {"content": [{"text": "폴백텍스트"}]}},
        }

    async def stream_sse_realtime(self, model_id, messages, system_prompt="", tool_config=None):
        self.calls.append("stream")
        for e in self._events:
            yield e

    async def converse(self, model_id, messages, system_prompt="", tool_config=None):
        self.calls.append("converse")
        return self._converse_ret


async def _collect(llm, msgs):
    chunks = []
    async for c in llm.astream(msgs):
        chunks.append(c)
    return chunks


def _accumulate(chunks):
    if not chunks:
        return None
    final = chunks[0]
    for c in chunks[1:]:
        final = final + c
    return final


_MSG = [HumanMessage(content="x")]
_TOOLUSE_CONVERSE = {
    "decision": "ALLOW",
    "output": {"message": {"content": [
        {"toolUse": {"toolUseId": "t1", "name": "select_plan",
                     "input": {"subtasks": [{"id": "t1", "domain": "coding", "subtask": "a"}]}}}
    ]}},
}


def test_stream_error_before_content_falls_back_to_converse():
    gw = _StubGateway(events=[{"type": "error", "message": "unsupported"}], converse_ret=_TOOLUSE_CONVERSE)
    llm = GatewayChatModel(gateway=gw, model_id="opus")
    chunks = asyncio.run(_collect(llm, _MSG))
    assert gw.calls == ["stream", "converse"]
    final = _accumulate(chunks)
    assert final is not None and final.tool_calls and final.tool_calls[0]["name"] == "select_plan"


def test_stream_empty_falls_back_to_converse():
    gw = _StubGateway(events=[], converse_ret={"decision": "ALLOW", "output": {"message": {"content": [{"text": "hi"}]}}})
    llm = GatewayChatModel(gateway=gw, model_id="opus")
    chunks = asyncio.run(_collect(llm, _MSG))
    assert gw.calls == ["stream", "converse"]
    final = _accumulate(chunks)
    assert final is not None and "hi" in final.content


def test_normal_stream_no_fallback():
    gw = _StubGateway(events=[
        {"type": "content_block_delta", "delta": {"text": "안녕"}},
        {"type": "content_block_delta", "delta": {"text": "하세요"}},
        {"type": "message_stop"},
    ])
    llm = GatewayChatModel(gateway=gw, model_id="sonnet")
    chunks = asyncio.run(_collect(llm, _MSG))
    assert gw.calls == ["stream"]  # converse 미호출 — 무손상
    final = _accumulate(chunks)
    assert "안녕하세요" in final.content


def test_error_after_content_raises():
    gw = _StubGateway(events=[
        {"type": "content_block_delta", "delta": {"text": "부분"}},
        {"type": "error", "message": "mid-stream boom"},
    ])
    llm = GatewayChatModel(gateway=gw, model_id="sonnet")
    try:
        asyncio.run(_collect(llm, _MSG))
        assert False, "부분 콘텐츠 방출 후 error 는 예외를 전파해야 함"
    except GatewayModelError:
        pass
    assert gw.calls == ["stream"]  # 폴백 없음(부분 스트림 오염 방지)


def test_fallback_converse_error_propagates():
    gw = _StubGateway(events=[{"type": "error", "message": "no stream"}],
                      converse_ret={"decision": "ERROR", "error": "converse도 실패"})
    llm = GatewayChatModel(gateway=gw, model_id="opus")
    try:
        asyncio.run(_collect(llm, _MSG))
        assert False, "converse 폴백도 실패하면 예외 전파"
    except GatewayModelError:
        pass
    assert gw.calls == ["stream", "converse"]


if __name__ == "__main__":
    import pytest

    raise SystemExit(pytest.main([__file__, "-q"]))
