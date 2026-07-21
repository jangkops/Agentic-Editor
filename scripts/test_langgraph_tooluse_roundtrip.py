"""Property 2: toolUse ↔ ToolCall 왕복 보존 (hypothesis, Gateway mock).

design.md **Property 2: toolUse ↔ ToolCall 왕복 보존** 검증.
**Validates: Requirements 2.3, 2.4**

대상 변환 헬퍼(`ai_engine/agent_system/chat_model_adapter.py`):
- `_bedrock_output_to_ai_message`  : Bedrock converse output → LangChain AIMessage(+tool_calls)
- `_lc_messages_to_bedrock`        : LangChain 메시지 → Bedrock converse messages
- `_lc_tool_to_bedrock_toolspec`   : LangChain 도구 → Bedrock toolSpec

검증 속성:
- Req 2.3: Bedrock 출력의 각 `toolUse` 블록이 대응하는 LangChain `ToolCall`(id, name, args)로
  변환되어 AIMessage.tool_calls 에 채워진다.
- Req 2.4: 반환된 AIMessage 의 각 ToolCall 은 비어있지 않은 id 와 name 을 갖는다.
- 왕복: Bedrock toolUse → ToolCall → 다시 Bedrock toolUse 시 name / args / id 가 보존된다.

Gateway 는 mock 으로 대체(직접 네트워크 호출 금지). hypothesis 로 다양한 tool call 입력 생성.
유한 시간 종료: max_examples 상한 + deadline=None.

실행: ai_engine/.venv/bin/python -m pytest scripts/test_langgraph_tooluse_roundtrip.py -q
"""
import asyncio
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from hypothesis import given, settings, strategies as st
from langchain_core.messages import AIMessage

from ai_engine.agent_system.chat_model_adapter import (
    GatewayChatModel,
    _bedrock_output_to_ai_message,
    _lc_messages_to_bedrock,
    _lc_tool_to_bedrock_toolspec,
)


# ─────────────────────────────────────────────────────────────────────────────
# hypothesis strategies — 다양한 tool call 입력 생성
# ─────────────────────────────────────────────────────────────────────────────
# 도구 이름/ID: 비어있지 않은 문자열 (Req 2.4 보장 대상)
_tool_names = st.text(
    alphabet=st.characters(min_codepoint=97, max_codepoint=122),  # a-z
    min_size=1,
    max_size=20,
)
_tool_ids = st.text(
    alphabet="abcdefghijklmnopqrstuvwxyz0123456789_-",
    min_size=1,
    max_size=24,
)

# tool args: JSON 직렬화 가능한 단순 dict (문자열 키 + 스칼라/리스트 값)
_json_scalars = st.one_of(
    st.text(max_size=20),
    st.integers(min_value=-1000, max_value=1000),
    st.booleans(),
    st.floats(allow_nan=False, allow_infinity=False, width=32),
)
_tool_args = st.dictionaries(
    keys=st.text(
        alphabet=st.characters(min_codepoint=97, max_codepoint=122),
        min_size=1,
        max_size=10,
    ),
    values=st.one_of(_json_scalars, st.lists(_json_scalars, max_size=3)),
    max_size=4,
)


@st.composite
def _tooluse_blocks(draw, min_size=1, max_size=4):
    """Bedrock output content 의 toolUse 블록 리스트를 생성."""
    n = draw(st.integers(min_value=min_size, max_value=max_size))
    blocks = []
    for _ in range(n):
        blocks.append(
            {
                "toolUse": {
                    "toolUseId": draw(_tool_ids),
                    "name": draw(_tool_names),
                    "input": draw(_tool_args),
                }
            }
        )
    return blocks


def _bedrock_output_message(text, tooluse_blocks):
    """Bedrock converse output.message 형식 조립."""
    content = []
    if text:
        content.append({"text": text})
    content.extend(tooluse_blocks)
    return {"role": "assistant", "content": content}


def _count_tooluse(message):
    return sum(
        1
        for b in message.get("content", [])
        if isinstance(b, dict) and "toolUse" in b
    )


# ─────────────────────────────────────────────────────────────────────────────
# Req 2.3 / 2.4: Bedrock toolUse → AIMessage.tool_calls
# ─────────────────────────────────────────────────────────────────────────────
@settings(max_examples=80, deadline=None)
@given(text=st.text(max_size=30), blocks=_tooluse_blocks())
def test_tooluse_count_matches_and_nonempty(text, blocks):
    """Property 2: toolUse 블록 개수 == tool_calls 개수, 각 tool_call 은 id/name 비어있지 않음."""
    message = _bedrock_output_message(text, blocks)
    ai = _bedrock_output_to_ai_message(message)

    # Req 2.3: toolUse 블록 수만큼 tool_calls 생성
    assert len(ai.tool_calls) == _count_tooluse(message)
    # Req 2.4: 각 ToolCall 은 비어있지 않은 id 와 name 을 가진다
    assert all(tc["id"] and tc["name"] for tc in ai.tool_calls)


@settings(max_examples=80, deadline=None)
@given(blocks=_tooluse_blocks())
def test_tooluse_values_preserved_to_toolcall(blocks):
    """Property 2: 각 toolUse 의 id/name/input 이 대응 ToolCall 로 정확히 보존된다."""
    message = _bedrock_output_message("", blocks)
    ai = _bedrock_output_to_ai_message(message)

    assert len(ai.tool_calls) == len(blocks)
    for src, tc in zip(blocks, ai.tool_calls):
        tu = src["toolUse"]
        assert tc["id"] == tu["toolUseId"]
        assert tc["name"] == tu["name"]
        assert tc["args"] == tu["input"]


def test_no_tooluse_produces_empty_tool_calls():
    """toolUse 가 없으면 tool_calls 는 비어있고 텍스트만 보존된다."""
    message = {"role": "assistant", "content": [{"text": "그냥 답변"}]}
    ai = _bedrock_output_to_ai_message(message)
    assert ai.tool_calls == []
    assert ai.content == "그냥 답변"


# ─────────────────────────────────────────────────────────────────────────────
# 왕복(round-trip): Bedrock toolUse → ToolCall → 다시 Bedrock toolUse
# ─────────────────────────────────────────────────────────────────────────────
@settings(max_examples=80, deadline=None)
@given(blocks=_tooluse_blocks())
def test_roundtrip_bedrock_tooluse_preserved(blocks):
    """Property 2: Bedrock toolUse → AIMessage → Bedrock 변환 왕복 시 name/args/id 보존."""
    src_message = _bedrock_output_message("", blocks)

    # forward: Bedrock output → AIMessage(+tool_calls)
    ai = _bedrock_output_to_ai_message(src_message)

    # backward: AIMessage → Bedrock converse messages
    bedrock_msgs, _system = _lc_messages_to_bedrock([ai])

    # assistant 메시지의 toolUse 블록만 추출
    round_tripped = []
    for m in bedrock_msgs:
        if m["role"] == "assistant":
            for b in m["content"]:
                if isinstance(b, dict) and "toolUse" in b:
                    round_tripped.append(b["toolUse"])

    assert len(round_tripped) == len(blocks)
    for src, out in zip(blocks, round_tripped):
        tu = src["toolUse"]
        assert out["toolUseId"] == tu["toolUseId"]
        assert out["name"] == tu["name"]
        assert out["input"] == tu["input"]


# ─────────────────────────────────────────────────────────────────────────────
# Gateway mock 을 통한 end-to-end 왕복: _agenerate 경유 (직접 네트워크 호출 없음)
# ─────────────────────────────────────────────────────────────────────────────
class _MockGateway:
    """converse 호출을 가로채 미리 준비한 Bedrock output 을 그대로 돌려주는 mock.

    네트워크 호출 없음. 마지막 요청 인자를 기록해 toolConfig 전달 검증에도 사용 가능.
    """

    def __init__(self, output_message):
        self._output_message = output_message
        self.last_call = None

    async def converse(self, *, model_id, messages, system_prompt, tool_config=None):
        self.last_call = {
            "model_id": model_id,
            "messages": messages,
            "system_prompt": system_prompt,
            "tool_config": tool_config,
        }
        return {
            "decision": "ALLOW",
            "output": {"message": self._output_message},
        }


@settings(max_examples=60, deadline=None)
@given(blocks=_tooluse_blocks())
def test_agenerate_via_mock_gateway_preserves_tool_calls(blocks):
    """Property 2: mock Gateway 를 경유한 _agenerate 결과 AIMessage 가 toolUse 를 보존한다."""
    output_message = _bedrock_output_message("호출합니다", blocks)
    gateway = _MockGateway(output_message)
    model = GatewayChatModel(gateway=gateway, model_id="anthropic.claude-test")

    from langchain_core.messages import HumanMessage

    result = asyncio.run(model._agenerate([HumanMessage(content="작업 해줘")]))
    ai = result.generations[0].message

    assert isinstance(ai, AIMessage)
    assert len(ai.tool_calls) == len(blocks)
    assert all(tc["id"] and tc["name"] for tc in ai.tool_calls)
    for src, tc in zip(blocks, ai.tool_calls):
        tu = src["toolUse"]
        assert tc["id"] == tu["toolUseId"]
        assert tc["name"] == tu["name"]
        assert tc["args"] == tu["input"]


# ─────────────────────────────────────────────────────────────────────────────
# bind_tools: LangChain 도구 → Bedrock toolSpec name 보존 (Req 2.3 지원 경로)
# ─────────────────────────────────────────────────────────────────────────────
@settings(max_examples=60, deadline=None)
@given(name=_tool_names, description=st.text(max_size=40))
def test_tool_to_toolspec_name_preserved(name, description):
    """도구 정의 → Bedrock toolSpec 변환 시 name 이 보존되고 inputSchema.json 이 존재한다."""
    tool_def = {
        "name": name,
        "description": description,
        "inputSchema": {"json": {"type": "object", "properties": {}}},
    }
    spec = _lc_tool_to_bedrock_toolspec(tool_def)
    assert spec["toolSpec"]["name"] == name
    assert "json" in spec["toolSpec"]["inputSchema"]


if __name__ == "__main__":
    import pytest

    raise SystemExit(pytest.main([__file__, "-q"]))
