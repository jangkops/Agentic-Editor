"""planner 모델 역할 배분(요구사항 9.2) + 타임아웃 정렬 회귀 테스트.

검증:
- _make_plan 이 model_planner 를 우선 사용(주입 시), 미주입이면 model_coding → 기본값 폴백.
- planner 는 PLANNER_TIMEOUT(폴링 상한 정렬) 로 wait_for — ROUTER_TIMEOUT(60s)로 조기
  폴백하지 않는다(게이트웨이 비동기 잡이 DAG 를 무력화하던 결함 방지).

네트워크/게이트웨이 불필요 — GatewayChatModel 을 캡처용 스텁으로 대체.
실행: ai_engine/.venv/bin/python -m pytest scripts/test_planner_model_role_and_timeout.py -q
"""
from __future__ import annotations

import asyncio
import os
import sys
from types import SimpleNamespace

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import ai_engine.agent_system.supervisor as sup


class _CaptureModel:
    """GatewayChatModel 대체 — 생성 시 model_id 를 캡처하고 select_plan tool_call 을 반환."""

    captured_model_id = None

    def __init__(self, gateway=None, model_id="", **kw):
        type(self).captured_model_id = model_id

    def bind_tools(self, tools, tool_choice=None, **kw):
        return self

    async def ainvoke(self, messages, **kw):
        return SimpleNamespace(
            tool_calls=[{
                "name": "select_plan",
                "args": {"subtasks": [
                    {"id": "t1", "domain": "coding", "subtask": "a", "depends_on": []},
                    {"id": "t2", "domain": "research", "subtask": "b", "depends_on": []},
                ]},
            }],
            content="",
        )


def _run(coro):
    return asyncio.run(coro)


def test_planner_prefers_model_planner():
    """model_planner 주입 시 planner LLM 이 그 값을 사용(요구사항 9.2)."""
    _orig = sup.GatewayChatModel
    sup.GatewayChatModel = _CaptureModel
    try:
        deps = SimpleNamespace(gateway=object(), model_planner="PLANNER-MODEL", model_coding="CODING-MODEL")
        plan = _run(sup._make_plan({"prompt": "x"}, deps))
        assert _CaptureModel.captured_model_id == "PLANNER-MODEL"
        assert len(plan) == 2  # 정상 분해(폴백 아님)
    finally:
        sup.GatewayChatModel = _orig


def test_planner_falls_back_to_model_coding_when_no_planner():
    """model_planner 미주입 시 model_coding 사용(하위 호환)."""
    _orig = sup.GatewayChatModel
    sup.GatewayChatModel = _CaptureModel
    try:
        deps = SimpleNamespace(gateway=object(), model_coding="CODING-MODEL")
        _run(sup._make_plan({"prompt": "x"}, deps))
        assert _CaptureModel.captured_model_id == "CODING-MODEL"
    finally:
        sup.GatewayChatModel = _orig


def test_planner_timeout_aligned_to_polling_ceiling():
    """PLANNER_TIMEOUT 이 존재하고 ROUTER_TIMEOUT 보다 크다(비동기 잡 조기 폴백 방지)."""
    assert hasattr(sup, "PLANNER_TIMEOUT")
    assert sup.PLANNER_TIMEOUT >= sup.ROUTER_TIMEOUT
    # 폴링 상한(_poll_job_data max_wait=300)과 정렬 — 기본값 300s.
    assert sup.PLANNER_TIMEOUT >= 300.0


def test_planner_uses_planner_timeout_not_router_timeout(monkeypatch):
    """_make_plan 이 wait_for 에 PLANNER_TIMEOUT 을 전달하는지(ROUTER_TIMEOUT 아님) 확인."""
    _orig_model = sup.GatewayChatModel
    _orig_wait = asyncio.wait_for
    captured = {}

    async def _capture_wait_for(coro, timeout=None):
        captured["timeout"] = timeout
        return await coro

    sup.GatewayChatModel = _CaptureModel
    monkeypatch.setattr(sup.asyncio, "wait_for", _capture_wait_for)
    try:
        deps = SimpleNamespace(gateway=object(), model_planner="P")
        _run(sup._make_plan({"prompt": "x"}, deps))
        assert captured.get("timeout") == sup.PLANNER_TIMEOUT
        assert captured.get("timeout") != sup.ROUTER_TIMEOUT
    finally:
        sup.GatewayChatModel = _orig_model


if __name__ == "__main__":
    import pytest

    raise SystemExit(pytest.main([__file__, "-q"]))
