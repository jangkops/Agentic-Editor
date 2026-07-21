"""메시지 변환 헬퍼 엣지 케이스 단위 테스트 (Task 1.6 / 요구사항 2.6).

대상: ai_engine/agent_system/chat_model_adapter.py 의 순수 변환 헬퍼
- _lc_messages_to_bedrock: LangChain 메시지 → Bedrock converse messages + system_text
- _image_block_from_lc / _lc_content_to_blocks: 멀티모달 이미지 첨부 변환
- _enforce_alternation: user/assistant 교대 규칙(연속 동일 role 병합, 선두 assistant 방지)
- _bedrock_output_to_ai_message: Bedrock 출력 → AIMessage(+tool_calls)

커버 엣지 케이스(요구사항 2.6):
1. 이미지 첨부가 포함된 멀티모달 메시지 변환
2. toolResult(ToolMessage) 블록 변환
3. user/assistant 교대(alternation) 규칙 — 연속 동일 role 병합/정규화

Gateway·네트워크 불필요(순수 변환 함수), 유한 시간.
실행: ai_engine/.venv/bin/python -m pytest scripts/test_langgraph_message_conversion.py -q
"""
import base64
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from langchain_core.messages import (
    AIMessage,
    HumanMessage,
    SystemMessage,
    ToolMessage,
)

from ai_engine.agent_system.chat_model_adapter import (
    _bedrock_output_to_ai_message,
    _enforce_alternation,
    _image_block_from_lc,
    _lc_content_to_blocks,
    _lc_messages_to_bedrock,
)

# 작은 더미 이미지 바이트(내용 무의미 — base64 왕복만 검증)
_RAW = b"\x89PNG\r\n\x1a\n dummy image bytes"
_B64 = base64.b64encode(_RAW).decode()


# ─────────────────────────────────────────────────────────────────────────────
# 1) 이미지 첨부(멀티모달) 변환
# ─────────────────────────────────────────────────────────────────────────────
def test_image_url_data_uri_to_bedrock_block():
    part = {"type": "image_url", "image_url": {"url": f"data:image/png;base64,{_B64}"}}
    block = _image_block_from_lc(part)
    assert block is not None
    assert block["image"]["format"] == "png"
    assert block["image"]["source"]["bytes"] == _RAW


def test_image_jpg_format_normalized_to_jpeg():
    part = {"type": "image_url", "image_url": {"url": f"data:image/jpg;base64,{_B64}"}}
    block = _image_block_from_lc(part)
    assert block is not None
    # jpg → jpeg 정규화(Bedrock 허용 포맷)
    assert block["image"]["format"] == "jpeg"


def test_image_source_bytes_form():
    part = {"type": "image", "format": "png", "source": {"bytes": _RAW}}
    block = _image_block_from_lc(part)
    assert block is not None
    assert block["image"]["source"]["bytes"] == _RAW


def test_image_invalid_data_uri_returns_none():
    part = {"type": "image_url", "image_url": {"url": "data:image/png;base64,%%%not-b64%%%"}}
    assert _image_block_from_lc(part) is None


def test_image_non_image_part_returns_none():
    assert _image_block_from_lc({"type": "text", "text": "hi"}) is None


def test_multimodal_human_message_conversion():
    """텍스트 + 이미지 첨부 HumanMessage → user role, text 블록 + image 블록."""
    msg = HumanMessage(
        content=[
            {"type": "text", "text": "이 이미지를 설명해줘"},
            {"type": "image_url", "image_url": {"url": f"data:image/png;base64,{_B64}"}},
        ]
    )
    bedrock, system = _lc_messages_to_bedrock([msg])
    assert system == ""
    assert len(bedrock) == 1
    assert bedrock[0]["role"] == "user"
    content = bedrock[0]["content"]
    assert {"text": "이 이미지를 설명해줘"} in content
    image_blocks = [b for b in content if "image" in b]
    assert len(image_blocks) == 1
    assert image_blocks[0]["image"]["source"]["bytes"] == _RAW


def test_lc_content_to_blocks_skips_empty_text():
    assert _lc_content_to_blocks("") == []
    assert _lc_content_to_blocks([{"type": "text", "text": ""}]) == []


# ─────────────────────────────────────────────────────────────────────────────
# 2) toolResult(ToolMessage) 블록 변환
# ─────────────────────────────────────────────────────────────────────────────
def test_tool_message_string_to_toolresult():
    msg = ToolMessage(content="파일 생성 완료", tool_call_id="tool-123")
    bedrock, _ = _lc_messages_to_bedrock([HumanMessage(content="해줘"), msg])
    # HumanMessage(user) → ToolMessage(user) 는 연속 user 이므로 병합됨
    assert len(bedrock) == 1
    tool_blocks = [b for b in bedrock[0]["content"] if "toolResult" in b]
    assert len(tool_blocks) == 1
    tr = tool_blocks[0]["toolResult"]
    assert tr["toolUseId"] == "tool-123"
    assert tr["content"] == [{"text": "파일 생성 완료"}]


def test_tool_message_empty_content_defaults():
    msg = ToolMessage(content="", tool_call_id="t1")
    bedrock, _ = _lc_messages_to_bedrock([msg])
    tr = bedrock[0]["content"][0]["toolResult"]
    assert tr["content"] == [{"text": ""}]


def test_tool_message_missing_id_defaults_empty():
    # tool_call_id 는 필수 필드지만 빈 문자열이면 "" 로 전달돼야 한다
    msg = ToolMessage(content="ok", tool_call_id="")
    bedrock, _ = _lc_messages_to_bedrock([msg])
    assert bedrock[0]["content"][0]["toolResult"]["toolUseId"] == ""


def test_ai_tool_call_then_tool_result_roundtrip():
    """assistant toolUse → user toolResult 교대(정상 도구 루프 형태)."""
    ai = AIMessage(
        content="도구를 호출합니다",
        tool_calls=[{"id": "call-1", "name": "write_file", "args": {"path": "a.txt"}, "type": "tool_call"}],
    )
    tool = ToolMessage(content="작성됨", tool_call_id="call-1")
    bedrock, _ = _lc_messages_to_bedrock([HumanMessage(content="파일 만들어"), ai, tool])
    assert [m["role"] for m in bedrock] == ["user", "assistant", "user"]
    # assistant 에 toolUse 블록
    tu = [b for b in bedrock[1]["content"] if "toolUse" in b][0]["toolUse"]
    assert tu["toolUseId"] == "call-1"
    assert tu["name"] == "write_file"
    assert tu["input"] == {"path": "a.txt"}
    # 마지막 user 에 toolResult 블록
    tr = [b for b in bedrock[2]["content"] if "toolResult" in b][0]["toolResult"]
    assert tr["toolUseId"] == "call-1"


# ─────────────────────────────────────────────────────────────────────────────
# 3) user/assistant 교대(alternation) 규칙
# ─────────────────────────────────────────────────────────────────────────────
def test_system_message_merged_into_system_text():
    msgs = [
        SystemMessage(content="너는 도우미"),
        SystemMessage(content="한국어로 답해"),
        HumanMessage(content="안녕"),
    ]
    bedrock, system = _lc_messages_to_bedrock(msgs)
    assert "너는 도우미" in system and "한국어로 답해" in system
    # system 은 messages 에 포함되지 않음, user 하나만 남음
    assert len(bedrock) == 1 and bedrock[0]["role"] == "user"


def test_consecutive_users_merged():
    msgs = [HumanMessage(content="첫째"), HumanMessage(content="둘째")]
    bedrock, _ = _lc_messages_to_bedrock(msgs)
    assert len(bedrock) == 1
    assert bedrock[0]["role"] == "user"
    assert {"text": "첫째"} in bedrock[0]["content"]
    assert {"text": "둘째"} in bedrock[0]["content"]


def test_consecutive_assistants_merged_with_leading_user_prefix():
    msgs = [AIMessage(content="A1"), AIMessage(content="A2")]
    bedrock, _ = _lc_messages_to_bedrock(msgs)
    # 선두 assistant 방지용 빈 user 프리픽스 + 병합된 assistant
    assert bedrock[0]["role"] == "user"
    assert bedrock[1]["role"] == "assistant"
    assert {"text": "A1"} in bedrock[1]["content"]
    assert {"text": "A2"} in bedrock[1]["content"]


def test_alternation_no_two_consecutive_same_role():
    msgs = [
        HumanMessage(content="u1"),
        HumanMessage(content="u2"),
        AIMessage(content="a1"),
        HumanMessage(content="u3"),
        AIMessage(content="a2"),
        AIMessage(content="a3"),
    ]
    bedrock, _ = _lc_messages_to_bedrock(msgs)
    roles = [m["role"] for m in bedrock]
    # 연속 동일 role 이 없어야 한다
    for prev, cur in zip(roles, roles[1:]):
        assert prev != cur, roles


def test_enforce_alternation_leading_assistant_gets_user_prefix():
    raw = [{"role": "assistant", "content": [{"text": "hi"}]}]
    out = _enforce_alternation(raw)
    assert out[0]["role"] == "user"
    assert out[1]["role"] == "assistant"


def test_enforce_alternation_does_not_mutate_first_role_when_user():
    raw = [
        {"role": "user", "content": [{"text": "a"}]},
        {"role": "assistant", "content": [{"text": "b"}]},
    ]
    out = _enforce_alternation(raw)
    assert [m["role"] for m in out] == ["user", "assistant"]


# ─────────────────────────────────────────────────────────────────────────────
# 4) _bedrock_output_to_ai_message (출력 역변환)
# ─────────────────────────────────────────────────────────────────────────────
def test_output_text_only():
    ai = _bedrock_output_to_ai_message({"content": [{"text": "안녕"}, {"text": "하세요"}]})
    assert isinstance(ai, AIMessage)
    assert ai.content == "안녕하세요"
    assert not ai.tool_calls


def test_output_tooluse_populates_tool_calls():
    msg = {
        "content": [
            {"text": "호출"},
            {"toolUse": {"toolUseId": "id-9", "name": "generate_pptx", "input": {"topic": "x"}}},
        ]
    }
    ai = _bedrock_output_to_ai_message(msg)
    assert ai.content == "호출"
    assert len(ai.tool_calls) == 1
    tc = ai.tool_calls[0]
    assert tc["id"] == "id-9"
    assert tc["name"] == "generate_pptx"
    assert tc["args"] == {"topic": "x"}


def test_output_tooluse_defaults_never_none():
    """toolUse 필드가 비어도 id/name/args 는 None 이 아닌 기본값(요구사항 2.4)."""
    ai = _bedrock_output_to_ai_message({"content": [{"toolUse": {}}]})
    tc = ai.tool_calls[0]
    assert tc["id"] == "" and tc["name"] == "" and tc["args"] == {}


def test_output_empty_message():
    ai = _bedrock_output_to_ai_message({})
    assert isinstance(ai, AIMessage)
    assert ai.content == ""
    assert not ai.tool_calls


if __name__ == "__main__":
    import pytest

    raise SystemExit(pytest.main([__file__, "-q"]))
