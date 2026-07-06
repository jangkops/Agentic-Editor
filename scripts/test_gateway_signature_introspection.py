"""Regression — 기존 GatewayClient 메서드 시그니처 불변 (요구사항 8.3, 8.4).

Feature: gateway-openai-models
대상: ai_engine.gateway_module.GatewayClient

순수 add 원칙: OpenAI 메서드 추가가 기존 converse/invoke/스트리밍 메서드의
시그니처를 변경하지 않음을 inspect.signature로 못박는다.

실행: pytest scripts/test_gateway_signature_introspection.py -q
"""
from __future__ import annotations

import inspect
import os
import sys

import pytest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from ai_engine.gateway_module import GatewayClient  # noqa: E402

# {메서드명: 기대 파라미터 목록(순서 포함)}
_EXPECTED = {
    "converse": ["self", "model_id", "messages", "system_prompt", "tool_config"],
    "converse_quota_only": ["self", "model_id", "messages", "system_prompt"],
    "converse_stream_live": ["self", "model_id", "messages", "system_prompt", "tool_config"],
    "stream_sse_realtime": ["self", "model_id", "messages", "system_prompt", "tool_config"],
    "stream_converse": ["self", "model_id", "messages", "system_prompt"],
    "invoke_model": ["self", "model_id", "body", "timeout"],
}


@pytest.mark.parametrize("name,expected", list(_EXPECTED.items()))
def test_existing_method_signature_unchanged(name, expected):
    assert hasattr(GatewayClient, name), f"기존 메서드 {name} 사라짐"
    sig = inspect.signature(getattr(GatewayClient, name))
    assert list(sig.parameters.keys()) == expected, f"{name} 시그니처 변경됨"


def test_openai_methods_added():
    # 신규 OpenAI 메서드가 추가되었는지(순수 add 확인)
    for m in (
        "openai_responses_sync",
        "openai_responses_job_submit",
        "openai_responses_job_submit_and_poll",
        "_openai_poll_job",
        "_build_openai_payload",
    ):
        assert hasattr(GatewayClient, m), f"OpenAI 메서드 {m} 누락"


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-q"]))
