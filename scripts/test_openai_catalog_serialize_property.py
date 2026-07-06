"""Property tests — OpenAI 카탈로그 직렬화/역직렬화.

Feature: gateway-openai-models
대상: ai_engine.openai_catalog.serialize / deserialize / CatalogError

Properties:
  - Property 5: 카탈로그 직렬화 왕복 보존 — serialize(deserialize(serialize(x))) == serialize(x) (Req 3.1, 3.3)
  - Property 6: 직렬화는 결정론적이다 — 의미 동등 입력 → 동일 UTF-8 바이트 (Req 3.2)
  - Property 7: 유효하지 않은 항목은 부분 목록 없이 거부된다 (Req 3.5)

실행: pytest scripts/test_openai_catalog_serialize_property.py -q
"""
from __future__ import annotations

import os
import sys

import pytest
from hypothesis import given, settings, strategies as st

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from ai_engine.openai_catalog import (  # noqa: E402
    CatalogError,
    deserialize,
    serialize,
)

_HSET = settings(max_examples=150, deadline=None)

_id_text = st.text(
    alphabet=st.characters(min_codepoint=33, max_codepoint=122), min_size=1, max_size=60
)
_name_text = st.text(min_size=1, max_size=40)
_mode = st.sampled_from(["sync", "async", "auto", "weird", ""])

_entry = st.builds(
    lambda i, n, md: {"id": i, "name": n, "mode": md},
    _id_text,
    _name_text,
    _mode,
)


def _unique_by_id(entries):
    seen = {}
    for e in entries:
        seen[e["id"]] = e
    return list(seen.values())


_entries = st.lists(_entry, max_size=8).map(_unique_by_id)


# Feature: gateway-openai-models, Property 5: 카탈로그 직렬화 왕복 보존
@_HSET
@given(entries=_entries)
def test_property5_roundtrip_preservation(entries):
    once = serialize(entries)
    twice = serialize(deserialize(once))
    assert once == twice, "왕복 직렬화가 바이트 동일하지 않음"


# Feature: gateway-openai-models, Property 6: 직렬화는 결정론적이다
@_HSET
@given(entries=_entries)
def test_property6_deterministic_serialization(entries):
    # 입력 순서를 뒤집어도(의미 동등) 동일 바이트열을 내야 한다(id 정렬로 정규화).
    a = serialize(entries)
    b = serialize(list(reversed(entries)))
    assert a == b
    # UTF-8 인코딩 바이트도 동일
    assert a.encode("utf-8") == b.encode("utf-8")


# Feature: gateway-openai-models, Property 7: 유효하지 않은 항목은 부분 목록 없이 거부된다
@_HSET
@given(
    valid=st.lists(_entry, min_size=1, max_size=4).map(_unique_by_id),
    bad_name=st.just(""),
)
def test_property7_invalid_entry_rejected_no_partial(valid, bad_name):
    import json

    # 유효 항목들 + 마지막에 name이 빈 무효 항목 1개를 섞는다.
    payload = {"version": 1, "models": list(valid) + [{"id": "openai.bad", "name": bad_name}]}
    text = json.dumps(payload, ensure_ascii=False)
    with pytest.raises(CatalogError) as ei:
        deserialize(text)
    assert ei.value.code == "invalid-model-entry"


def test_invalid_json_raises():
    with pytest.raises(CatalogError) as ei:
        deserialize("{not valid json")
    assert ei.value.code == "invalid-json"


def test_id_too_long_rejected():
    import json

    long_id = "openai." + ("x" * 300)
    text = json.dumps({"models": [{"id": long_id, "name": "X"}]}, ensure_ascii=False)
    with pytest.raises(CatalogError) as ei:
        deserialize(text)
    assert ei.value.code == "invalid-model-entry"


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-q"]))
