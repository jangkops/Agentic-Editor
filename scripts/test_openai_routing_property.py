"""Property test — OpenAI/Bedrock 라우팅 분기.

Feature: gateway-openai-models
대상: ai_engine.server.is_openai_model

Property:
  - Property 9: provider에 따른 라우팅 분기 정확성 (Req 5.1, 5.2, 8.2)
    · OpenAI 식별자(카탈로그 멤버 또는 'openai.' prefix) → OpenAI 라우트로 판정
    · 그 외(Bedrock 등) → 비-OpenAI(기존 /converse 경로)로 판정

실행: pytest scripts/test_openai_routing_property.py -q
"""
from __future__ import annotations

import os
import sys

import pytest
from hypothesis import given, settings, strategies as st

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

# server.py는 무거운 의존성(fastapi/boto3 등)을 가질 수 있어 importorskip로 가드.
server = pytest.importorskip("ai_engine.server")
is_openai_model = server.is_openai_model

_HSET = settings(max_examples=200, deadline=None)

_suffix = st.text(alphabet=st.characters(min_codepoint=97, max_codepoint=122), min_size=1, max_size=20)
# Bedrock 스타일 식별자(openai. prefix를 포함하지 않도록 구성)
_bedrock_id = st.sampled_from(
    [
        "anthropic.claude-3-opus-20240229-v1:0",
        "anthropic.claude-3-5-sonnet-20241022-v2:0",
        "amazon.titan-text-express-v1",
        "meta.llama3-70b-instruct-v1:0",
        "cohere.command-r-plus-v1:0",
    ]
)


# Feature: gateway-openai-models, Property 9: provider에 따른 라우팅 분기 정확성
@_HSET
@given(suffix=_suffix)
def test_property9_openai_prefix_routes_to_openai(suffix):
    model_id = "openai." + suffix
    assert is_openai_model(model_id, set()) is True


@_HSET
@given(bid=_bedrock_id)
def test_property9_bedrock_routes_to_converse(bid):
    # 카탈로그에 없고 openai. prefix도 아니면 비-OpenAI
    assert is_openai_model(bid, set()) is False


@_HSET
@given(member=_suffix)
def test_property9_catalog_membership_routes_to_openai(member):
    # prefix가 없어도 OpenAI 카탈로그 멤버면 OpenAI로 판정
    openai_ids = {member}
    assert is_openai_model(member, openai_ids) is True


def test_property9_empty_model_is_not_openai():
    assert is_openai_model("", set()) is False
    assert is_openai_model(None, set()) is False


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-q"]))
