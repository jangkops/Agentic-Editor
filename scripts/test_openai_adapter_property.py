"""Property tests — OpenAI Responses → Converse 어댑터.

Feature: gateway-openai-models
대상: ai_engine.openai_adapter.to_converse / extract_text

Property:
  - Property 10: 어댑터는 출력 텍스트를 정확히 추출해 Converse 구조로 변환한다 (Req 6.1, 6.2)

실행: pytest scripts/test_openai_adapter_property.py -q
"""
from __future__ import annotations

import os
import sys

from hypothesis import given, settings, strategies as st

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from ai_engine.openai_adapter import to_converse  # noqa: E402

_HSET = settings(max_examples=150, deadline=None)

# 비어있지 않은 임의 텍스트(추출 결과가 식별 가능하도록 non-empty)
_text = st.text(min_size=1, max_size=200).filter(lambda s: s.strip() != "")


def _converse_text(conv):
    parts = []
    for block in conv.get("output", {}).get("message", {}).get("content", []):
        if isinstance(block, dict) and isinstance(block.get("text"), str):
            parts.append(block["text"])
    return "".join(parts)


# Feature: gateway-openai-models, Property 10: 어댑터는 출력 텍스트를 정확히 추출해 Converse 구조로 변환한다
@_HSET
@given(t=_text)
def test_property10_output_text_field(t):
    # 형태 A: output_text 단일 문자열
    conv = to_converse({"output_text": t})
    assert _converse_text(conv) == t
    assert conv["output"]["message"]["role"] == "assistant"


@_HSET
@given(t=_text)
def test_property10_nested_output_content(t):
    # 형태 B: output[].content[].text (OpenAI Responses 표준 형태)
    raw = {
        "output": [
            {
                "type": "message",
                "content": [{"type": "output_text", "text": t}],
            }
        ]
    }
    conv = to_converse(raw)
    assert _converse_text(conv) == t


@_HSET
@given(parts=st.lists(_text, min_size=1, max_size=5))
def test_property10_multi_segment_text_concatenation(parts):
    # 여러 텍스트 세그먼트는 순서대로 연결되어야 한다
    raw = {"output": [{"content": [{"text": p} for p in parts]}]}
    conv = to_converse(raw)
    assert _converse_text(conv) == "".join(parts)


if __name__ == "__main__":
    import pytest

    raise SystemExit(pytest.main([__file__, "-q"]))
