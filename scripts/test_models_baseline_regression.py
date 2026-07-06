"""Regression — OpenAI 0개 시 카탈로그 baseline 보존 (요구사항 8.1).

Feature: gateway-openai-models
대상: ai_engine.openai_catalog.merge_openai_into_catalog

OpenAI 항목이 0개일 때 병합 결과가 Bedrock baseline과 구조·순서·내용·직렬화
바이트까지 동일함을 검증한다(순수 add 원칙의 핵심 회귀 가드).

실행: pytest scripts/test_models_baseline_regression.py -q
"""
from __future__ import annotations

import copy
import json
import os
import sys

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from ai_engine.openai_catalog import merge_openai_into_catalog  # noqa: E402

_BASELINE = {
    "Anthropic": [
        {"id": "anthropic.claude-3-opus-20240229-v1:0", "name": "Claude 3 Opus",
         "capabilities": {"chat": True}},
        {"id": "anthropic.claude-3-5-sonnet-20241022-v2:0", "name": "Claude 3.5 Sonnet",
         "capabilities": {"chat": True}},
    ],
    "Amazon": [
        {"id": "amazon.titan-text-express-v1", "name": "Titan Text Express",
         "capabilities": {"chat": True}},
    ],
}


def test_empty_openai_returns_same_object():
    base = copy.deepcopy(_BASELINE)
    merged = merge_openai_into_catalog(base, [])
    assert merged is base  # 동일 객체(복사·변형 없음)


def test_empty_openai_byte_identical():
    base = copy.deepcopy(_BASELINE)
    before = json.dumps(base, sort_keys=True, ensure_ascii=False)
    merged = merge_openai_into_catalog(base, [])
    after = json.dumps(merged, sort_keys=True, ensure_ascii=False)
    assert before == after


def test_empty_openai_no_openai_provider_key():
    merged = merge_openai_into_catalog(copy.deepcopy(_BASELINE), [])
    assert "OpenAI" not in merged  # baseline에 없던 키를 추가하지 않음


def test_provider_count_and_membership_unchanged():
    merged = merge_openai_into_catalog(copy.deepcopy(_BASELINE), [])
    assert set(merged.keys()) == set(_BASELINE.keys())
    for prov, models in _BASELINE.items():
        assert [m["id"] for m in merged[prov]] == [m["id"] for m in models]


if __name__ == "__main__":
    import pytest

    raise SystemExit(pytest.main([__file__, "-q"]))
