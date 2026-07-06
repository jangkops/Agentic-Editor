"""Example tests — OpenAI 어댑터 스키마 불일치/도구/사용량 변환.

Feature: gateway-openai-models
대상: ai_engine.openai_adapter (to_converse, extract_tool_calls, extract_usage,
      extract_job_id, extract_status, InvalidOpenAIResponse)

검증(Req 6.3, 6.4, 6.5):
  - 출력 텍스트 필드 부재 → InvalidOpenAIResponse, 부분 텍스트 미전달
  - tool call / usage 변환 대표 예제

실행: pytest scripts/test_openai_adapter_schema_examples.py -q
"""
from __future__ import annotations

import os
import sys

import pytest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from ai_engine.openai_adapter import (  # noqa: E402
    InvalidOpenAIResponse,
    extract_job_id,
    extract_status,
    extract_tool_calls,
    extract_usage,
    to_converse,
)


def test_missing_text_and_tools_raises():
    # 텍스트/도구 모두 없음 → 예외, 부분 텍스트 미전달
    with pytest.raises(InvalidOpenAIResponse):
        to_converse({"foo": "bar"})


def test_empty_output_raises():
    with pytest.raises(InvalidOpenAIResponse):
        to_converse({"output": []})


def test_tool_call_conversion_function_form():
    raw = {
        "output": [
            {
                "type": "function_call",
                "name": "get_weather",
                "call_id": "call_123",
                "arguments": '{"city": "Seoul"}',
            }
        ]
    }
    conv = to_converse(raw)
    blocks = conv["output"]["message"]["content"]
    tool_blocks = [b for b in blocks if "toolUse" in b]
    assert len(tool_blocks) == 1
    tu = tool_blocks[0]["toolUse"]
    assert tu["name"] == "get_weather"
    assert tu["toolUseId"] == "call_123"
    assert tu["input"] == {"city": "Seoul"}


def test_tool_calls_top_level():
    raw = {
        "output_text": "ok",
        "tool_calls": [
            {"id": "t1", "function": {"name": "f", "arguments": '{"a":1}'}}
        ],
    }
    blocks = extract_tool_calls(raw)
    assert blocks and blocks[0]["toolUse"]["name"] == "f"
    assert blocks[0]["toolUse"]["input"] == {"a": 1}


def test_tool_call_invalid_json_arguments_safe():
    raw = {"output": [{"type": "tool_call", "name": "f", "arguments": "not-json"}]}
    blocks = extract_tool_calls(raw)
    assert blocks
    # JSON 파싱 실패 시 _raw로 안전 보존
    assert blocks[0]["toolUse"]["input"].get("_raw") == "not-json"


def test_usage_extraction_variants():
    assert extract_usage({"usage": {"input_tokens": 10, "output_tokens": 5}}) == {
        "inputTokens": 10,
        "outputTokens": 5,
    }
    assert extract_usage({"token_usage": {"prompt_tokens": 7, "completion_tokens": 3}}) == {
        "inputTokens": 7,
        "outputTokens": 3,
    }
    # 부분 누락 → 0
    assert extract_usage({"usage": {"input_tokens": 4}}) == {
        "inputTokens": 4,
        "outputTokens": 0,
    }
    # usage 부재 → 0/0
    assert extract_usage({}) == {"inputTokens": 0, "outputTokens": 0}


def test_job_id_defensive_extraction():
    assert extract_job_id({"job_id": "j1"}) == "j1"
    assert extract_job_id({"jobId": "j2"}) == "j2"
    assert extract_job_id({"id": 123}) == "123"
    assert extract_job_id({"task_id": "t9"}) == "t9"
    assert extract_job_id({}) == ""


def test_status_defensive_extraction():
    assert extract_status({"status": "COMPLETED"}) == "completed"
    assert extract_status({"state": "Failed"}) == "failed"
    assert extract_status({}) == ""


def test_gateway_envelope_live_shape():
    # 라이브 게이트웨이 응답 형태(2026-06 확인): output 키로 OpenAI 응답을 래핑,
    # 내부 output 배열에 reasoning 항목 + message 항목이 섞여 있다.
    raw = {
        "decision": "ALLOW",
        "output": {
            "object": "response",
            "model": "openai.gpt-5.5",
            "output": [
                {"id": "rs_1", "summary": [], "type": "reasoning"},
                {
                    "type": "message",
                    "role": "assistant",
                    "status": "completed",
                    "content": [
                        {"annotations": [], "logprobs": [], "text": "pong", "type": "output_text"}
                    ],
                },
            ],
            "usage": {"input_tokens": 14, "output_tokens": 18},
        },
        "usage": {"input_tokens": 14, "output_tokens": 18},
    }
    conv = to_converse(raw)
    text = "".join(
        b["text"] for b in conv["output"]["message"]["content"] if "text" in b
    )
    assert text == "pong", f"게이트웨이 래퍼에서 텍스트 추출 실패: {text!r}"
    assert conv["usage"]["inputTokens"] == 14
    assert conv["usage"]["outputTokens"] == 18


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-q"]))
