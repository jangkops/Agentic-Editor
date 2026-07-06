"""Property test — API 토큰 마스킹.

Feature: gateway-openai-models
대상: ai_engine.gateway_module.mask_token

Property:
  - Property 11: 토큰 마스킹은 앞 4자만 남기고 원문을 노출하지 않는다 (Req 9.4)

실행: pytest scripts/test_token_mask_property.py -q
"""
from __future__ import annotations

import os
import sys

from hypothesis import given, settings, strategies as st

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from ai_engine.gateway_module import mask_token  # noqa: E402

_HSET = settings(max_examples=200, deadline=None)


# Feature: gateway-openai-models, Property 11: 토큰 마스킹은 앞 4자만 남기고 원문을 노출하지 않는다
@_HSET
@given(token=st.text(min_size=5, max_size=200))
def test_property11_long_token_not_exposed(token):
    masked = mask_token(token)
    # 앞 4자 + "****" 형태
    assert masked == token[:4] + "****"
    # 5자 이상 토큰의 5번째 글자 이후 원문이 마스킹 결과에 통째로 포함되면 안 됨
    tail = token[4:]
    if tail:
        assert tail not in masked
    # 전체 원문이 그대로 노출되지 않음
    assert token not in masked


@_HSET
@given(token=st.text(min_size=0, max_size=4))
def test_property11_short_token_fully_masked(token):
    # 4자 이하(빈값 포함)는 원문 노출 없이 "****"
    assert mask_token(token) == "****"


def test_non_string_inputs():
    assert mask_token(None) == "****"
    assert mask_token(123456) == "****"
    assert mask_token("") == "****"


if __name__ == "__main__":
    import pytest

    raise SystemExit(pytest.main([__file__, "-q"]))
