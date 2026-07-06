"""Property tests — OpenAI 카탈로그 병합/정규화.

Feature: gateway-openai-models
대상: ai_engine.openai_catalog.merge_openai_into_catalog

Properties:
  - Property 1: 병합은 중복이 아닌 모든 OpenAI 항목을 OpenAI provider로 포함한다 (Req 1.1, 1.2)
  - Property 2: 정규화된 모든 OpenAI 항목은 chat capability를 가진다 (Req 1.3)
  - Property 3: 빈 OpenAI 목록 병합은 Bedrock baseline을 보존한다 (Req 1.4, 8.1)
  - Property 4: 중복 식별자는 Bedrock 항목을 보존하고 OpenAI를 추가하지 않는다 (Req 1.5)

실행: pytest scripts/test_openai_catalog_merge_property.py -q
"""
from __future__ import annotations

import copy
import os
import sys

from hypothesis import given, settings, strategies as st

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from ai_engine.openai_catalog import merge_openai_into_catalog  # noqa: E402

_HSET = settings(max_examples=150, deadline=None)

# ── 전략 ──────────────────────────────────────────────────────────
_id_text = st.text(
    alphabet=st.characters(min_codepoint=33, max_codepoint=122), min_size=1, max_size=40
)
_name_text = st.text(min_size=1, max_size=40)


def _model(id_prefix=""):
    return st.builds(
        lambda i, n: {"id": id_prefix + i, "name": n},
        _id_text,
        _name_text,
    )


# Bedrock 카탈로그: provider -> [ {id, name}, ... ]
_bedrock_catalog = st.dictionaries(
    keys=st.sampled_from(["Anthropic", "Amazon", "Meta", "Cohere", "Mistral"]),
    values=st.lists(_model("bedrock-"), max_size=5),
    max_size=5,
)

# OpenAI 항목(아직 정규화 전; id/name 필수)
_openai_entry = st.builds(
    lambda i, n: {"id": "openai." + i, "name": n},
    _id_text,
    _name_text,
)
_openai_entries = st.lists(_openai_entry, max_size=6)


def _all_ids(catalog):
    ids = set()
    for ms in catalog.values():
        if isinstance(ms, list):
            for m in ms:
                if isinstance(m, dict) and m.get("id"):
                    ids.add(m["id"])
    return ids


# Feature: gateway-openai-models, Property 1: 병합은 중복이 아닌 모든 OpenAI 항목을 OpenAI provider로 포함한다
@_HSET
@given(bedrock=_bedrock_catalog, entries=_openai_entries)
def test_property1_merge_includes_all_non_duplicate_openai(bedrock, entries):
    base = copy.deepcopy(bedrock)
    base_ids = _all_ids(base)
    merged = merge_openai_into_catalog(base, entries)
    merged_openai_ids = {m["id"] for m in merged.get("OpenAI", []) if isinstance(m, dict)}
    # 중복(이미 base에 존재)이 아닌 모든 OpenAI 항목 id가 OpenAI 그룹에 존재해야 한다
    for e in entries:
        if e["id"] not in base_ids:
            assert e["id"] in merged_openai_ids, f"누락된 OpenAI 항목: {e['id']}"


# Feature: gateway-openai-models, Property 2: 정규화된 모든 OpenAI 항목은 chat capability를 가진다
@_HSET
@given(bedrock=_bedrock_catalog, entries=_openai_entries)
def test_property2_all_openai_have_chat_capability(bedrock, entries):
    merged = merge_openai_into_catalog(copy.deepcopy(bedrock), entries)
    for m in merged.get("OpenAI", []):
        assert isinstance(m, dict)
        assert m.get("capabilities", {}).get("chat") is True
        assert m.get("provider") == "OpenAI"


# Feature: gateway-openai-models, Property 3: 빈 OpenAI 목록 병합은 Bedrock baseline을 보존한다
@_HSET
@given(bedrock=_bedrock_catalog)
def test_property3_empty_openai_preserves_baseline(bedrock):
    base = copy.deepcopy(bedrock)
    merged = merge_openai_into_catalog(base, [])
    # 변경 없이 동일 객체 반환(baseline 보존)
    assert merged is base
    assert merged == bedrock


# Feature: gateway-openai-models, Property 4: 중복 식별자는 Bedrock 항목을 보존하고 OpenAI를 추가하지 않는다
@_HSET
@given(bedrock=_bedrock_catalog, name=_name_text)
def test_property4_duplicate_id_preserves_bedrock(bedrock, name):
    base = copy.deepcopy(bedrock)
    base_ids = list(_all_ids(base))
    if not base_ids:
        return  # 중복시킬 기존 id가 없으면 스킵
    dup_id = base_ids[0]
    # 기존 id와 동일한 OpenAI 항목을 병합 시도
    merged = merge_openai_into_catalog(base, [{"id": dup_id, "name": name}])
    # 중복 id는 OpenAI 그룹에 추가되지 않아야 한다
    openai_ids = {m["id"] for m in merged.get("OpenAI", []) if isinstance(m, dict)}
    assert dup_id not in openai_ids
    # 기존 Bedrock 항목은 그대로 보존
    assert dup_id in _all_ids({k: v for k, v in merged.items() if k != "OpenAI"})


if __name__ == "__main__":
    import pytest

    raise SystemExit(pytest.main([__file__, "-q"]))
