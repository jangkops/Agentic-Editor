"""Regression — 비-OpenAI 채팅 경로 보존 (요구사항 8.2).

Feature: gateway-openai-models
대상: ai_engine.server.is_openai_model

비-OpenAI(Bedrock) 모델은 라우팅 분기 판정이 항상 False여서 기존 gw.converse/
stream_sse_realtime 경로를 그대로 탄다(OpenAI 우회 분기 미진입). 이 회귀 가드는
OpenAI 통합이 기존 채팅 경로를 변경하지 않음을 보장한다.

실행: pytest scripts/test_nonopenai_chat_preserved.py -q
"""
from __future__ import annotations

import os
import sys

import pytest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

server = pytest.importorskip("ai_engine.server")
is_openai_model = server.is_openai_model

_BEDROCK_IDS = [
    "anthropic.claude-3-opus-20240229-v1:0",
    "anthropic.claude-3-5-sonnet-20241022-v2:0",
    "us.anthropic.claude-3-5-sonnet-20241022-v2:0",
    "amazon.titan-text-express-v1",
    "amazon.nova-pro-v1:0",
    "meta.llama3-70b-instruct-v1:0",
    "cohere.command-r-plus-v1:0",
    "deepseek.r1-v1:0",
]


@pytest.mark.parametrize("mid", _BEDROCK_IDS)
def test_bedrock_models_not_routed_to_openai(mid):
    # 카탈로그에 OpenAI 항목이 있어도 Bedrock id는 비-OpenAI로 판정되어야 한다
    assert is_openai_model(mid, {"openai.gpt-5.5", "openai.gpt-5.4"}) is False


def test_converse_signature_unchanged():
    import inspect

    from ai_engine.gateway_module import GatewayClient

    sig = inspect.signature(GatewayClient.converse)
    assert list(sig.parameters.keys()) == [
        "self", "model_id", "messages", "system_prompt", "tool_config",
    ]


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-q"]))
