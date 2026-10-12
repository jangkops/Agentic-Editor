"""게이트웨이 global 기준 모델(Claude 5.5) 라우팅·prefix 회귀 테스트.

  G1. _profile_prefixed / _module_with_prefix — Claude 5.5 만 global., 그 외는 기존대로 us.
  G2. _resolve_callable_model_id — 프로필 전용·둘 다·캐시 없음에서 5.5 는 global., 4.x·3P·OpenAI 는 기존 동작
  G3. 자동 라우팅 — code/file_generation/intent_classifier 가 Opus/Sonnet/Haiku 5.5 를 고른다
  G4. 5.5 가 런타임에 거부(model_denied)되면 다음 후보(4.x, us.)로 내려간다 — 최종 폴백 포함
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_ROOT))
sys.path.insert(0, str(_ROOT / "ai_engine"))

server = pytest.importorskip("ai_engine.server")

OPUS55, SONNET55, HAIKU55 = "anthropic.claude-opus-5-5", "anthropic.claude-sonnet-5-5", "anthropic.claude-haiku-5-5"
SONNET45 = "anthropic.claude-sonnet-4-5-20250929-v1:0"
OPUS47 = "anthropic.claude-opus-4-7-20251015-v1:0"


@pytest.fixture
def fresh_denylist(monkeypatch):
    monkeypatch.setattr(server, "_DENIED_MODEL_IDS", set(server._DENIED_MODEL_IDS))


def test_profile_prefixed_only_global_models():
    for mid in (OPUS55, SONNET55, HAIKU55):
        assert server._profile_prefixed(mid) == f"global.{mid}"
        assert server._module_with_prefix(mid) == f"global.{mid}"
    assert server._profile_prefixed(SONNET45) == f"us.{SONNET45}"
    assert server._module_with_prefix(SONNET45) == f"us.{SONNET45}"
    for already in (f"us.{OPUS55}", f"global.{SONNET45}", f"eu.{HAIKU55}"):
        assert server._module_with_prefix(already) == already


def test_resolve_callable_prefers_global_for_55(monkeypatch):
    cache = {OPUS55: ["INFERENCE_PROFILE"], HAIKU55: ["ON_DEMAND", "INFERENCE_PROFILE"],
             SONNET45: ["INFERENCE_PROFILE"], "meta.llama3-3-70b-instruct-v1:0": ["ON_DEMAND"]}
    monkeypatch.setattr(server, "_load_inference_types", lambda *a, **k: cache)
    r = lambda mid: server._resolve_callable_model_id(mid, "p", "u")  # noqa: E731
    assert r(OPUS55) == f"global.{OPUS55}"
    assert r(HAIKU55) == f"global.{HAIKU55}"
    assert r(SONNET45) == f"us.{SONNET45}"                       # 예전 모델은 그대로 us.
    assert r(f"us.{OPUS55}") == f"us.{OPUS55}"                   # 명시 prefix 는 존중
    assert r("meta.llama3-3-70b-instruct-v1:0") == "meta.llama3-3-70b-instruct-v1:0"
    assert r("openai.gpt-6-sol") == "openai.gpt-6-sol"           # OpenAI 라우트는 원본 id


def test_resolve_callable_unknown_cache(monkeypatch):
    monkeypatch.setattr(server, "_load_inference_types", lambda *a, **k: {})
    assert server._resolve_callable_model_id(SONNET55, "p", "u") == f"global.{SONNET55}"
    assert server._resolve_callable_model_id("qwen.qwen3-32b-v1:0", "p", "u") == "qwen.qwen3-32b-v1:0"


def test_auto_routing_picks_55(fresh_denylist):
    assert server._specialized_model_for_task("code", "") == f"global.{OPUS55}"
    assert server._specialized_model_for_task("file_generation", "") == f"global.{SONNET55}"
    assert server._specialized_model_for_task("intent_classifier", "") == f"global.{HAIKU55}"
    assert server._specialized_model_for_task("no_such_task", "") == f"global.{SONNET55}"


def test_denied_55_falls_back_to_4x(fresh_denylist):
    server._record_denied_model(OPUS55)
    server._record_denied_model(SONNET55)
    assert server._specialized_model_for_task("code", "") == f"us.{OPUS47}"
    assert server._specialized_model_for_task("file_generation", "") == f"us.{SONNET45}"
    assert server._specialized_model_for_task("no_such_task", "") == f"us.{SONNET45}"
