"""Regression — OpenAI 모델 id는 _resolve_callable_model_id에서 변형되지 않는다.

Feature: gateway-openai-models (런타임 버그 수정)

버그: _resolve_callable_model_id("openai.gpt-5.5", ...)가 Bedrock 추론 타입
조회에서 빈 결과를 받아 최종 분기에서 "us.openai.gpt-5.5"로 변형 →
  (1) is_openai_model 판정이 깨져 Bedrock 경로로 잘못 라우팅
  (2) 게이트웨이가 "us.openai.*"를 미지원으로 거부
수정: OpenAI id(openai.* 또는 카탈로그 멤버)는 early-guard로 원본 그대로 반환.

이 테스트는 수정의 의미성을 증명한다(guard 제거 시 실패).

실행: pytest scripts/test_openai_model_id_passthrough.py -q
"""
from __future__ import annotations

import os
import sys

import pytest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

server = pytest.importorskip("ai_engine.server")

_resolve = server._resolve_callable_model_id
is_openai_model = server.is_openai_model


@pytest.mark.parametrize("mid", ["openai.gpt-5.5", "openai.gpt-5.4", "openai.gpt-oss-120b"])
def test_openai_id_unchanged(mid):
    # OpenAI id는 us./eu. prefix 부착 없이 원본 그대로 반환되어야 한다
    out = _resolve(mid, "default", "")
    assert out == mid, f"OpenAI id가 변형됨: {mid} → {out}"
    # 변형되지 않았으므로 라우팅 판정도 유지
    assert is_openai_model(out) is True


def test_openai_id_not_prefixed_us():
    out = _resolve("openai.gpt-5.5", "default", "")
    assert not out.startswith("us."), "OpenAI id에 us. prefix가 잘못 부착됨"


def test_openai_is_tool_and_vision_capable():
    # OpenAI 도구 루프 지원 → 사용자 선택이 run-agent에서 Claude로 대체되지 않아야 함
    assert server._module_is_tool_capable("openai.gpt-5.5") is True
    assert server._module_is_tool_capable("openai.gpt-5.4") is True
    assert server._module_is_vision_capable("openai.gpt-5.5") is True
    # 비-도구 모델은 여전히 False
    assert server._module_is_tool_capable("meta.llama3-70b-instruct-v1:0") is False


def test_specialized_routing_preserves_openai():
    # 사용자가 openai.gpt-5.5를 고르면 file_generation에서도 그대로 보존
    picked = server._specialized_model_for_task("file_generation", "openai.gpt-5.5")
    assert "openai.gpt-5.5" in picked, f"OpenAI 선택이 보존되지 않음: {picked}"


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-q"]))
