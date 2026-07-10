"""멀티턴 맥락 역변환(bedrock_messages_to_lc) 회귀 테스트.

검증 (요구사항: graph-stream 멀티턴 맥락 복원):
- Bedrock messages(dict) → LangChain 메시지 역변환. text 이어붙임, image→placeholder,
  role=assistant→AIMessage / 그 외→HumanMessage, 빈 입력→[].
gateway·네트워크 불필요, 유한 시간.
실행: ai_engine/.venv/bin/python -m pytest scripts/test_langgraph_multiturn.py -q
"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from langchain_core.messages import AIMessage, HumanMessage

from ai_engine.agent_system.chat_model_adapter import bedrock_messages_to_lc


def test_roles_mapped():
    bedrock = [
        {"role": "user", "content": [{"text": "안녕"}]},
        {"role": "assistant", "content": [{"text": "네 반갑습니다"}]},
    ]
    lc = bedrock_messages_to_lc(bedrock)
    assert isinstance(lc[0], HumanMessage) and lc[0].content == "안녕"
    assert isinstance(lc[1], AIMessage) and "반갑" in lc[1].content


def test_multiple_text_blocks_joined():
    lc = bedrock_messages_to_lc([{"role": "user", "content": [{"text": "a"}, {"text": "b"}]}])
    assert lc[0].content == "a\nb"


def test_image_block_placeholder():
    bedrock = [{"role": "user", "content": [{"text": "이거 봐"}, {"image": {"format": "png"}}]}]
    lc = bedrock_messages_to_lc(bedrock)
    assert "[이미지 첨부됨]" in lc[0].content and "이거 봐" in lc[0].content


def test_string_content():
    lc = bedrock_messages_to_lc([{"role": "user", "content": "문자열"}])
    assert lc[0].content == "문자열"


def test_empty_and_none():
    assert bedrock_messages_to_lc([]) == []
    assert bedrock_messages_to_lc(None) == []


def test_summary_checkpoint_shape():
    # ConversationMemory 요약 주입 형태(요약 user + ack assistant + 현재 질문)
    bedrock = [
        {"role": "user", "content": [{"text": "[이전 대화 요약]\n커피 논의"}]},
        {"role": "assistant", "content": [{"text": "이해했습니다"}]},
        {"role": "user", "content": [{"text": "그럼 홍차는?"}]},
    ]
    lc = bedrock_messages_to_lc(bedrock)
    assert len(lc) == 3
    assert isinstance(lc[-1], HumanMessage) and "홍차" in lc[-1].content


if __name__ == "__main__":
    import pytest
    raise SystemExit(pytest.main([__file__, "-q"]))
