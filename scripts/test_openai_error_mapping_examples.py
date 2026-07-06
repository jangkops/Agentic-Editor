"""Example tests — route_openai_chat 오류 매핑·동기→비동기 폴백.

Feature: gateway-openai-models
대상: ai_engine.server.route_openai_chat

검증(Req 7.1, 7.2, 7.3, 7.5):
  - 동기 성공 → Converse 변환 결과 반환
  - SyncTimeout → 비동기 잡 폴백 후 변환
  - QuotaExceededError 등은 그대로 전파(부분 응답 미전달)

실행: pytest scripts/test_openai_error_mapping_examples.py -q
"""
from __future__ import annotations

import asyncio
import os
import sys

import pytest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

server = pytest.importorskip("ai_engine.server")
from ai_engine.gateway_module import QuotaExceededError, SyncTimeout  # noqa: E402

route_openai_chat = server.route_openai_chat


class _FakeGW:
    def __init__(self, *, sync_result=None, sync_exc=None, async_result=None, async_exc=None):
        self._sync_result = sync_result
        self._sync_exc = sync_exc
        self._async_result = async_result
        self._async_exc = async_exc
        self.async_called = False

    async def openai_responses_sync(self, model_id, messages, system_prompt="", timeout=120):
        if self._sync_exc:
            raise self._sync_exc
        return self._sync_result

    async def openai_responses_job_submit_and_poll(self, model_id, messages, system_prompt="",
                                                   poll_interval=5, max_wait=300):
        self.async_called = True
        if self._async_exc:
            raise self._async_exc
        return self._async_result


def _text(conv):
    return "".join(
        b["text"] for b in conv["output"]["message"]["content"] if "text" in b
    )


def test_sync_success_converts():
    gw = _FakeGW(sync_result={"output_text": "hello"})
    conv = asyncio.run(route_openai_chat(gw, "openai.gpt-5.5", [{"role": "user", "content": "hi"}]))
    assert _text(conv) == "hello"
    assert gw.async_called is False


def test_sync_timeout_falls_back_to_async():
    gw = _FakeGW(sync_exc=SyncTimeout("t"), async_result={"output_text": "async-ok"})
    conv = asyncio.run(route_openai_chat(gw, "openai.gpt-5.5", [{"role": "user", "content": "hi"}]))
    assert _text(conv) == "async-ok"
    assert gw.async_called is True


def test_quota_error_propagates():
    gw = _FakeGW(sync_exc=QuotaExceededError("403"))
    with pytest.raises(QuotaExceededError):
        asyncio.run(route_openai_chat(gw, "openai.gpt-5.5", [{"role": "user", "content": "hi"}]))
    # 동기에서 quota 거부 → 비동기 폴백 시도하지 않음(부분 응답·우회 없음)
    assert gw.async_called is False


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-q"]))
