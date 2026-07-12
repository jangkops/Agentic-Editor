"""server.py 모델 ID 해석/카탈로그 숨김 테스트 (수정 B, C 검증).

대상: ai_engine/server.py
- _resolve_callable_model_id : inferenceTypes 캐시 기반 prefix 결정 (수정 B)
- _filter_uninvokable / _UNINVOKABLE_MODEL_IDS : /api/models HIDE 필터 (수정 C)

시나리오:
[B] _resolve_callable_model_id (_load_inference_types monkeypatch)
  - unknown(캐시 빈 리스트) → 원본 model_id 그대로 (bare면 bare 유지)
  - INFERENCE_PROFILE only → us. 강제
  - ON_DEMAND only → bare
  - 둘 다 → 기존 CRIS(us.) 유지
[C] _filter_uninvokable
  - HIDE 목록 모델 제외 (base id 비교 → prefix 변형도 제외)
  - 비-HIDE 모델은 유지, 빈 provider 제거

제약: 실제 네트워크/LLM 호출 없음. _load_inference_types를 monkeypatch.
실행: ai_engine/.venv/bin/python scripts/test_model_id_resolution_and_hide.py
"""
from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from ai_engine import server

_PASS = 0
_FAIL = 0


def _check(name, cond):
    global _PASS, _FAIL
    if cond:
        _PASS += 1
        print(f"  PASS: {name}")
    else:
        _FAIL += 1
        print(f"  FAIL: {name}")


def _patch_types(mapping):
    """_load_inference_types를 mapping 반환하도록 대체."""
    server._load_inference_types = lambda aws_profile, bedrock_user: dict(mapping)


def test_resolve_unknown_keeps_original():
    print("[B] unknown(캐시 빈) → 원본 유지")
    _patch_types({})  # 어떤 모델도 정보 없음
    _check("bare 3P 모델 그대로 bare 유지",
           server._resolve_callable_model_id("nvidia.nemotron-x", "p", "u") == "nvidia.nemotron-x")
    _check("bare qwen 그대로",
           server._resolve_callable_model_id("qwen.qwen3-32b", "p", "u") == "qwen.qwen3-32b")
    _check("이미 us. 붙은 unknown은 원본(us.) 유지",
           server._resolve_callable_model_id("us.deepseek.v3", "p", "u") == "us.deepseek.v3")


def test_resolve_inference_profile_only():
    print("[B] INFERENCE_PROFILE only → us. 강제")
    _patch_types({"anthropic.claude-x": ["INFERENCE_PROFILE"]})
    _check("bare → us. 부착",
           server._resolve_callable_model_id("anthropic.claude-x", "p", "u") == "us.anthropic.claude-x")
    _check("이미 us. → 유지",
           server._resolve_callable_model_id("us.anthropic.claude-x", "p", "u") == "us.anthropic.claude-x")


def test_resolve_on_demand_only():
    print("[B] ON_DEMAND only → bare")
    _patch_types({"meta.llama-x": ["ON_DEMAND"]})
    _check("bare 유지",
           server._resolve_callable_model_id("meta.llama-x", "p", "u") == "meta.llama-x")
    _check("us. 붙어와도 bare로 제거",
           server._resolve_callable_model_id("us.meta.llama-x", "p", "u") == "meta.llama-x")


def test_resolve_both():
    print("[B] 둘 다 지원 → 기본 CRIS(us.)")
    _patch_types({"anthropic.claude-y": ["ON_DEMAND", "INFERENCE_PROFILE"]})
    _check("bare → us.",
           server._resolve_callable_model_id("anthropic.claude-y", "p", "u") == "us.anthropic.claude-y")
    _check("us. → 유지",
           server._resolve_callable_model_id("us.anthropic.claude-y", "p", "u") == "us.anthropic.claude-y")


def test_filter_uninvokable():
    print("[C] _filter_uninvokable HIDE 필터")
    catalog = {
        "Anthropic": [
            {"id": "anthropic.claude-3-haiku-20240307-v1:0", "name": "hide-me"},         # HIDE
            {"id": "us.anthropic.claude-3-5-sonnet-20240620-v1:0", "name": "hide-prefix"},  # HIDE(변형)
            {"id": "anthropic.claude-sonnet-4-5-20250929-v1:0", "name": "keep"},          # 유지
        ],
        "Cohere": [
            {"id": "cohere.command-r-v1:0", "name": "hide-noaccess"},                     # HIDE → provider 비게 됨
        ],
        "Meta": [
            {"id": "meta.llama3-2-1b-instruct-v1:0", "name": "hide"},                     # HIDE
            {"id": "meta.llama4-x", "name": "keep"},                                      # 유지
        ],
    }
    out = server._filter_uninvokable(catalog)
    anthropic_ids = [m["id"] for m in out.get("Anthropic", [])]
    _check("HIDE base id 제외", "anthropic.claude-3-haiku-20240307-v1:0" not in anthropic_ids)
    _check("HIDE prefix 변형 제외", "us.anthropic.claude-3-5-sonnet-20240620-v1:0" not in anthropic_ids)
    _check("비-HIDE 유지", "anthropic.claude-sonnet-4-5-20250929-v1:0" in anthropic_ids)
    _check("전부 HIDE된 provider 제거(Cohere)", "Cohere" not in out)
    meta_ids = [m["id"] for m in out.get("Meta", [])]
    _check("Meta HIDE 제외", "meta.llama3-2-1b-instruct-v1:0" not in meta_ids)
    _check("Meta 비-HIDE 유지", "meta.llama4-x" in meta_ids)


def test_filter_embed_image_categories():
    print("[C] embed/image 카테고리 HIDE 반영")
    embed = {"Cohere": [{"id": "cohere.embed-english-v3:0:512", "name": "hide"},
                        {"id": "cohere.embed-multilingual-v3:0", "name": "keep"}]}
    image = {"Amazon": [{"id": "amazon.titan-image-generator-v2:0", "name": "hide"},
                        {"id": "amazon.nova-canvas-v1:0", "name": "keep"}]}
    eo = server._filter_uninvokable(embed)
    io = server._filter_uninvokable(image)
    _check("embed HIDE 제외", "cohere.embed-english-v3:0:512" not in [m["id"] for m in eo.get("Cohere", [])])
    _check("embed 비-HIDE 유지", "cohere.embed-multilingual-v3:0" in [m["id"] for m in eo.get("Cohere", [])])
    _check("image HIDE 제외", "amazon.titan-image-generator-v2:0" not in [m["id"] for m in io.get("Amazon", [])])
    _check("image 비-HIDE 유지", "amazon.nova-canvas-v1:0" in [m["id"] for m in io.get("Amazon", [])])


def main():
    test_resolve_unknown_keeps_original()
    test_resolve_inference_profile_only()
    test_resolve_on_demand_only()
    test_resolve_both()
    test_filter_uninvokable()
    test_filter_embed_image_categories()
    print(f"\n=== 결과: PASS={_PASS} FAIL={_FAIL} ===")
    return 0 if _FAIL == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
