"""Integration — 카탈로그 병합 → 라우팅 → 어댑터 변환 엔드투엔드.

Feature: gateway-openai-models
대상: openai_catalog.merge_openai_into_catalog + server.is_openai_model +
      server.route_openai_chat + openai_adapter.to_converse

검증(Req 1.1, 5.1, 6.1, 6.2): 게이트웨이 모킹으로 동기·비동기 폴백 양 경로에서
OpenAI 모델 채팅이 Chat_Stream 호환 Converse 출력으로 변환되는지 확인.

실행: pytest scripts/test_openai_integration.py -q
"""
from __future__ import annotations

import asyncio
import os
import sys

import pytest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from ai_engine.gateway_module import SyncTimeout  # noqa: E402
from ai_engine.openai_catalog import merge_openai_into_catalog  # noqa: E402

server = pytest.importorskip("ai_engine.server")


class _FakeGW:
    def __init__(self, sync_result=None, sync_exc=None, async_result=None):
        self._sync_result = sync_result
        self._sync_exc = sync_exc
        self._async_result = async_result

    async def openai_responses_sync(self, model_id, messages, system_prompt="", timeout=120):
        if self._sync_exc:
            raise self._sync_exc
        return self._sync_result

    async def openai_responses_job_submit_and_poll(self, model_id, messages, system_prompt="",
                                                   poll_interval=5, max_wait=300):
        return self._async_result


def _text(conv):
    return "".join(b["text"] for b in conv["output"]["message"]["content"] if "text" in b)


def test_merge_then_route_sync_path():
    bedrock = {"Anthropic": [{"id": "anthropic.claude-3-opus-20240229-v1:0", "name": "Opus"}]}
    merged = merge_openai_into_catalog(bedrock, [{"id": "openai.gpt-5.5", "name": "GPT 5.5"}])
    # 병합 결과에 OpenAI 멤버 존재
    openai_ids = {m["id"] for m in merged["OpenAI"]}
    assert "openai.gpt-5.5" in openai_ids
    # 라우팅: OpenAI로 판정
    assert server.is_openai_model("openai.gpt-5.5", openai_ids) is True
    # 동기 경로 변환
    gw = _FakeGW(sync_result={"output_text": "통합-동기"})
    conv = asyncio.run(server.route_openai_chat(gw, "openai.gpt-5.5", [{"role": "user", "content": "hi"}]))
    assert _text(conv) == "통합-동기"


def test_route_async_fallback_path():
    gw = _FakeGW(
        sync_exc=SyncTimeout("t"),
        async_result={"output": [{"content": [{"text": "통합-비동기"}]}]},
    )
    conv = asyncio.run(server.route_openai_chat(gw, "openai.gpt-5.4", [{"role": "user", "content": "hi"}]))
    assert _text(conv) == "통합-비동기"


def test_bedrock_not_routed_after_merge():
    merged = merge_openai_into_catalog(
        {"Anthropic": [{"id": "anthropic.claude-3-opus-20240229-v1:0", "name": "Opus"}]},
        [{"id": "openai.gpt-5.5", "name": "GPT 5.5"}],
    )
    openai_ids = {m["id"] for m in merged["OpenAI"]}
    assert server.is_openai_model("anthropic.claude-3-opus-20240229-v1:0", openai_ids) is False


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-q"]))
