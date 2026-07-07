"""gw_text.converse_text — 스트리밍 우선/동기 폴백/타임아웃 검증.

실행: ./ai_engine/.venv/bin/python -m pytest scripts/test_gw_text_pbt.py -p no:cacheprovider -q
"""
import asyncio
import pytest
from ai_engine.rag.gw_text import converse_text


class StreamGW:
    """stream_sse_realtime 제공(빠른 경로). converse도 있지만 쓰이면 안 됨."""
    def __init__(self): self.stream_calls = 0; self.sync_calls = 0
    async def stream_sse_realtime(self, model_id, messages, system_prompt="", tool_config=None):
        self.stream_calls += 1
        for t in ["SCO", "RE: 0.9\n", "FEEDBACK: OK"]:
            yield {"type": "content_block_delta", "delta": {"text": t}}
        yield {"type": "message_stop"}
    async def converse(self, model_id, messages, system_prompt="", tool_config=None):
        self.sync_calls += 1
        return {"decision": "ALLOW", "output": {"message": {"content": [{"text": "SYNC"}]}}}


class SyncOnlyGW:
    """converse만 제공(스트리밍 없음) → 동기 폴백 경로."""
    def __init__(self): self.sync_calls = 0
    async def converse(self, model_id, messages, system_prompt="", tool_config=None):
        self.sync_calls += 1
        return {"decision": "ALLOW", "output": {"message": {"content": [{"text": "SYNCTEXT"}]}}}


class StreamErrGW:
    """스트리밍이 error 이벤트 → 동기 폴백으로 넘어가야 함."""
    async def stream_sse_realtime(self, model_id, messages, system_prompt="", tool_config=None):
        yield {"type": "error", "message": "boom"}
    async def converse(self, model_id, messages, system_prompt="", tool_config=None):
        return {"decision": "ALLOW", "output": {"message": {"content": [{"text": "FELLBACK"}]}}}


class HangStreamGW:
    """스트리밍이 무한 지연 → wait_for 타임아웃 발생해야 함(깔끔 취소)."""
    async def stream_sse_realtime(self, model_id, messages, system_prompt="", tool_config=None):
        await asyncio.sleep(10)
        yield {"type": "message_stop"}
    async def converse(self, model_id, messages, system_prompt="", tool_config=None):
        return {"decision": "ALLOW", "output": {"message": {"content": [{"text": "X"}]}}}


def test_prefers_stream():
    gw = StreamGW()
    txt = asyncio.run(converse_text(gw, "m", [{"role": "user", "content": [{"text": "q"}]}], timeout=5))
    assert txt == "SCORE: 0.9\nFEEDBACK: OK"
    assert gw.stream_calls == 1 and gw.sync_calls == 0


def test_sync_fallback_when_no_stream():
    gw = SyncOnlyGW()
    txt = asyncio.run(converse_text(gw, "m", [{"role": "user", "content": [{"text": "q"}]}], timeout=5))
    assert txt == "SYNCTEXT" and gw.sync_calls == 1


def test_stream_error_falls_back_to_sync():
    gw = StreamErrGW()
    txt = asyncio.run(converse_text(gw, "m", [{"role": "user", "content": [{"text": "q"}]}], timeout=5))
    assert txt == "FELLBACK"


def test_prefer_stream_false_uses_sync():
    gw = StreamGW()
    txt = asyncio.run(converse_text(gw, "m", [{"role": "user", "content": [{"text": "q"}]}],
                                    timeout=5, prefer_stream=False))
    assert txt == "SYNC" and gw.sync_calls == 1 and gw.stream_calls == 0


def test_timeout_raises_cleanly():
    gw = HangStreamGW()
    with pytest.raises(asyncio.TimeoutError):
        asyncio.run(converse_text(gw, "m", [{"role": "user", "content": [{"text": "q"}]}], timeout=0.2))
