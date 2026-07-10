"""도구 미지원 모델 graceful fallback 회귀 테스트 (chat_model_adapter._agenerate).

배경(회귀):
- graph-stream 서브그래프의 model 노드는 bind_tools 로 toolConfig 를 항상 바인딩한다.
- Nemotron 등 도구 미지원 모델은 게이트웨이가 toolConfig 를 거부한다.
- 수정: `GatewayChatModel._agenerate` 가 '도구 거부' 오류를 감지하면 tool_config=None 으로
  1회 재시도(graceful degradation)한다. 도구와 무관한 오류(토큰 만료/allowlist)는 그대로 전파.

검증 대상(ai_engine/agent_system/chat_model_adapter.py):
- `_is_tool_rejection(msg)` : 도구 거부 메시지 판정 헬퍼(도구 언급 전제).
- `_agenerate`             : 첫 converse(tool_config 있음) 도구거부 → 두번째 converse(None) 성공.
                             도구 무관 오류는 재시도 없이 raise.

제약 준수: 네트워크/LLM SDK 없음(mock Gateway), 무한대기 없음(asyncio.run).

실행: ai_engine/.venv/bin/python -m pytest scripts/test_langgraph_tool_rejection_fallback.py -q
"""
from __future__ import annotations

import asyncio
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from langchain_core.messages import AIMessage, HumanMessage

from ai_engine.agent_system.chat_model_adapter import (
    GatewayChatModel,
    GatewayModelError,
    _is_tool_rejection,
)


# ─────────────────────────────────────────────────────────────────────────────
# 1) _is_tool_rejection 단위 검증
# ─────────────────────────────────────────────────────────────────────────────
def test_is_tool_rejection_true_for_tool_errors():
    """도구 거부/미지원 메시지는 True 로 판정."""
    positives = [
        "This model does not support toolConfig",
        "Model doesn't support tool use",
        "tool use is not supported for this model",
        "toolConfig is invalid for provider nemotron",
        "The model cannot use tools",
        "tools are not allowed for this modelId",
        "unsupported parameter: toolConfig",
        "toolUse blocks are not supported",
    ]
    for msg in positives:
        assert _is_tool_rejection(msg) is True, f"도구 거부로 판정돼야 함: {msg!r}"


def test_is_tool_rejection_false_for_unrelated_errors():
    """도구와 무관한 오류(토큰 만료/allowlist/일반 검증)는 False."""
    negatives = [
        "The security token included in the request is expired",
        "expired token, please refresh credentials",
        "modelId is not in allowed list",
        "ValidationException: Unknown parameter modelId",
        "throttling: too many requests",
        "",
        None,  # 방어적: None 도 안전하게 False.
    ]
    for msg in negatives:
        assert _is_tool_rejection(msg) is False, f"도구 무관 오류인데 True: {msg!r}"


def test_is_tool_rejection_requires_tool_mention():
    """'tool' 언급이 없으면 'not support' 등이 있어도 False(오탐 방지)."""
    assert _is_tool_rejection("this feature is not supported") is False
    assert _is_tool_rejection("invalid request body") is False


# ─────────────────────────────────────────────────────────────────────────────
# 2) _agenerate 재시도 동작 — 스크립트된 mock Gateway
# ─────────────────────────────────────────────────────────────────────────────
def _text_output_message(text: str) -> dict:
    return {"role": "assistant", "content": [{"text": text}]}


class _ScriptedGateway:
    """converse 호출 순서에 따라 다른 결과를 돌려주는 mock.

    - 1번째 호출(tool_config 있음): 도구 거부 오류(decision=ERROR, error=<tool 메시지>).
    - 2번째 호출(tool_config=None): 성공(ALLOW + 텍스트 output).
    각 호출의 tool_config 를 calls 에 기록해 '도구 없이 재시도' 를 검증한다.
    """

    def __init__(self, *, error_message: str, error_first: bool = True):
        self.error_message = error_message
        self.error_first = error_first
        self.calls = []

    async def converse(self, *, model_id, messages, system_prompt, tool_config=None):
        await asyncio.sleep(0)
        self.calls.append({"tool_config": tool_config})
        # 첫 호출이 도구 포함이면 도구 거부 오류를 낸다(에러 시나리오).
        if self.error_first and len(self.calls) == 1:
            return {"decision": "ERROR", "error": self.error_message}
        return {"decision": "ALLOW", "output": {"message": _text_output_message("완료")}}


class _AlwaysErrorGateway:
    """항상 동일 오류를 돌려주는 mock — 재시도 없이 전파돼야 하는 케이스 검증용."""

    def __init__(self, *, error_message: str):
        self.error_message = error_message
        self.calls = []

    async def converse(self, *, model_id, messages, system_prompt, tool_config=None):
        await asyncio.sleep(0)
        self.calls.append({"tool_config": tool_config})
        return {"decision": "ERROR", "error": self.error_message}


def _fake_tool_config():
    """bind_tools 없이 _agenerate 에 직접 tool_config 를 주입하기 위한 최소 toolConfig."""
    return {"tools": [{"toolSpec": {"name": "dummy", "inputSchema": {"json": {"type": "object"}}}}]}


def test_agenerate_retries_without_tools_on_tool_rejection():
    """도구 거부 오류 → tool_config=None 으로 1회 재시도해 정상 AIMessage 반환."""
    gw = _ScriptedGateway(error_message="This model does not support toolConfig")
    model = GatewayChatModel(gateway=gw, model_id="nvidia.nemotron-test")

    result = asyncio.run(
        model._agenerate(
            [HumanMessage(content="작업 해줘")],
            _bedrock_tool_config=_fake_tool_config(),
        )
    )
    ai = result.generations[0].message
    assert isinstance(ai, AIMessage)
    assert ai.content == "완료"
    # 정확히 2회 호출: 1번째 도구 포함, 2번째 도구 없이(None).
    assert len(gw.calls) == 2, f"재시도 포함 2회여야 함: {len(gw.calls)}"
    assert gw.calls[0]["tool_config"] is not None, "첫 호출은 도구 포함이어야 함"
    assert gw.calls[1]["tool_config"] is None, "재시도는 도구 없이(None) 여야 함"


def test_agenerate_does_not_retry_on_unrelated_error():
    """도구 무관 오류(토큰 만료 등) → 재시도 없이 그대로 raise."""
    gw = _AlwaysErrorGateway(
        error_message="The security token included in the request is expired"
    )
    model = GatewayChatModel(gateway=gw, model_id="anthropic.claude-test")

    raised = False
    try:
        asyncio.run(
            model._agenerate(
                [HumanMessage(content="작업 해줘")],
                _bedrock_tool_config=_fake_tool_config(),
            )
        )
    except GatewayModelError as e:
        raised = True
        assert "expired" in str(e).lower()
    assert raised, "도구 무관 오류는 GatewayModelError 로 전파돼야 함"
    # 재시도 없음 → 호출 1회.
    assert len(gw.calls) == 1, f"재시도 없이 1회여야 함: {len(gw.calls)}"


def test_agenerate_no_retry_when_no_tool_config():
    """tool_config 가 애초에 없으면(도구 미바인딩) 도구 거부 메시지라도 재시도하지 않는다."""
    gw = _AlwaysErrorGateway(error_message="tool use is not supported")
    model = GatewayChatModel(gateway=gw, model_id="nvidia.nemotron-test")

    raised = False
    try:
        # _bedrock_tool_config 미전달 + bind_tools 미사용 → tool_config=None.
        asyncio.run(model._agenerate([HumanMessage(content="작업 해줘")]))
    except GatewayModelError:
        raised = True
    assert raised
    # tool_config 가 없으므로 재시도 분기에 들어가지 않아야 함(호출 1회).
    assert len(gw.calls) == 1, f"tool_config 없으면 재시도 없이 1회: {len(gw.calls)}"


if __name__ == "__main__":
    import pytest

    raise SystemExit(pytest.main([__file__, "-q"]))
