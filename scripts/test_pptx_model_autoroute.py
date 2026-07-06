"""Regression — 도구 미지원 모델 선택 시 파일/도구 생성 요청을 도구 가능 모델로 자동 라우팅.

사용자 보고: NVIDIA Nemotron Nano 12B VL(도구 미지원) 단일 모드로 "템플릿 사용해 이미지
슬라이드로 정리" 요청 → {"text": ""} 빈 응답(모델 사용 불가).

근본 원인: run-agent에서 needs_tools=True면 task_for_routing이 "general_chat"(경량 비도구
풀)로 잘못 떨어져, 도구가 필요한 작업이 도구 미지원 모델로 라우팅됨.

수정: needs_tools 또는 프롬프트의 파일 생성 의도가 감지되면 task_for_routing="file_generation"
(도구 호출 가능 Sonnet)로 라우팅 → 어떤 모델을 골라도 생성이 동작한다.

Correctness properties:
  P1. _specialized_model_for_task("file_generation", <non-tool model>) → 도구 가능 Claude.
  P2. run-agent에서 도구 미지원 모델 + 파일 생성 요청 → 게이트웨이 호출이 Claude로 라우팅된다.
  P3. 일반 채팅(도구 불필요) 요청은 사용자 모델/경량 라우팅을 유지한다(과도 라우팅 방지).

실행: pytest scripts/test_pptx_model_autoroute.py -q
"""
from __future__ import annotations

import os
import sys
import asyncio
from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_ROOT))
sys.path.insert(0, str(_ROOT / "ai_engine"))

server = pytest.importorskip("ai_engine.server")
pytest.importorskip("starlette.testclient")


def test_file_generation_routes_non_tool_model_to_claude():
    """P1 — 도구 미지원 모델도 file_generation에서 도구 가능 Claude로 대체."""
    for non_tool in ("nvidia.nemotron-nano-12b-v2-vl", "google.gemma-3-12b-it-v1:0",
                     "meta.llama3-3-70b-instruct-v1:0"):
        picked = server._specialized_model_for_task("file_generation", non_tool)
        assert server._module_is_tool_capable(picked), f"{non_tool} → {picked} (도구 미지원)"
        assert "claude" in picked.lower()


def test_run_agent_autoroutes_to_tool_capable(monkeypatch, tmp_path):
    """P2 — 도구 미지원 모델 + 파일 생성 요청 → 게이트웨이가 Claude로 호출된다."""
    from starlette.testclient import TestClient

    os.environ["AE_GENERATED_ROOT"] = str(tmp_path)
    seen = {"models": []}

    class _FakeGW:
        def force_refresh_creds(self):
            pass

        async def stream_sse_realtime(self, model_id=None, messages=None, system_prompt="", tool_config=None):
            seen["models"].append(model_id or "")
            yield {"type": "content_block_delta", "delta": {"text": "ok"}}
            yield {"type": "message_stop", "stopReason": "end_turn"}

    monkeypatch.setattr(server, "_get_gw", lambda *a, **k: _FakeGW())
    monkeypatch.setattr(server, "_build_messages", lambda *a, **k: [{"role": "user", "content": [{"text": "hi"}]}])
    monkeypatch.setattr(server, "_is_code_related", lambda *a, **k: False)
    monkeypatch.setattr(server, "_call_bridge", lambda *a, **k: None)  # HTML off

    client = TestClient(server.app)
    resp = client.post("/api/agents/run-agent", json={
        "prompt": "현재 템플릿을 사용해 프로젝트 흐름에대해 PPTX 이미지 슬라이드로 정리해줘",
        "model": "nvidia.nemotron-nano-12b-v2-vl",
        "needs_tools": True,
        "awsProfile": "p", "bedrockUser": "u",
    })
    assert resp.status_code == 200
    # 게이트웨이가 적어도 한 번은 도구 가능한 Claude로 호출되어야 한다.
    assert seen["models"], "게이트웨이 호출이 없음"
    assert any("claude" in m.lower() for m in seen["models"]), \
        f"도구 가능 모델로 라우팅되지 않음: {seen['models']}"
    assert not any("nvidia" in m.lower() for m in seen["models"]), \
        f"도구 미지원 NVIDIA 모델이 그대로 호출됨: {seen['models']}"


def test_general_chat_not_overrouted():
    """P3 — 도구 불필요 일반 채팅은 file_generation으로 과도 라우팅되지 않는다."""
    # 도구가 필요없는 단순 질의 — 프롬프트 의도 추론이 파일 생성을 요구하지 않아야 함
    pt, wanted, _ = server._infer_file_intent_from_prompt("오늘 날씨 어때?", "", "")
    assert wanted is False, f"일반 질의가 파일 생성으로 오분류: pt={pt}"


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-q"]))
