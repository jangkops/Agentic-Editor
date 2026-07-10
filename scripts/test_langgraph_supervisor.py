"""Top Supervisor 라우팅 로직 회귀/속성 테스트.

검증 대상 (요구사항 1.2/6.5, Property 4 — 유한 종료):
- route_selector: state["route"] 반환, 미설정 시 done.
- _make_route_tool: 첫 진입 enum 에 done 없음 / 재진입 enum 에 done 있음.
- top_router_node (gateway=None 폴백으로 결정적 검증):
    · hop cap 도달 → done.
    · 첫 진입(visited=[]) → 도메인 라벨 + visited 누적(done 아님).
    · 재진입(visited 有, gateway=None) → done (완료 판정 폴백, 안전 종료).
    · 마지막 메시지가 tool_calls 있는 AIMessage → done (방어).

gateway=None 이면 _classify_route 가 LLM 없이 폴백하므로 네트워크 불필요·유한 시간.
실행: ai_engine/.venv/bin/python -m pytest scripts/test_langgraph_supervisor.py -q
"""
import os
import sys
import asyncio

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from langchain_core.messages import AIMessage, HumanMessage

from ai_engine.agent_system.deps import GraphDeps
from ai_engine.agent_system import supervisor as S


def _run(coro):
    return asyncio.run(coro)


# ── route_selector ──
def test_route_selector_returns_route():
    assert S.route_selector({"route": "coding"}) == "coding"
    assert S.route_selector({"route": "done"}) == "done"


def test_route_selector_defaults_done_when_unset():
    assert S.route_selector({}) == "done"
    assert S.route_selector({"route": None}) == "done"


# ── _make_route_tool ──
def test_route_tool_enum_done_gating():
    enum_first = S._make_route_tool(False)["inputSchema"]["json"]["properties"]["route"]["enum"]
    enum_reentry = S._make_route_tool(True)["inputSchema"]["json"]["properties"]["route"]["enum"]
    assert "done" not in enum_first
    assert "done" in enum_reentry
    # subtask 필드 존재
    props = S._make_route_tool(True)["inputSchema"]["json"]["properties"]
    assert "subtask" in props


# ── top_router_node (gateway=None 결정적 폴백) ──
def _node(deps=None):
    return S.make_top_router_node(deps or GraphDeps(gateway=None))


def test_hop_cap_forces_done():
    node = _node()
    visited = ["chat"] * S.MAX_ROUTE_HOPS
    out = _run(node({"prompt": "x", "visited_routes": visited, "messages": []}))
    assert out["route"] == "done"


def test_first_entry_picks_domain_and_accumulates():
    node = _node()
    out = _run(node({"prompt": "이 코드를 리팩터링해줘", "visited_routes": [], "messages": []}))
    assert out["route"] in ("coding", "media", "research", "ops", "chat")
    assert out["route"] != "done"
    assert out["visited_routes"] == [out["route"]]


def test_reentry_without_gateway_terminates_done():
    """재진입 + gateway 없음 → 완료 판정 폴백으로 done(무한 순환/불필요 재호출 방지)."""
    node = _node()
    out = _run(node({
        "prompt": "설명해줘",
        "visited_routes": ["chat"],
        "messages": [HumanMessage(content="설명해줘"), AIMessage(content="설명입니다.")],
    }))
    assert out["route"] == "done"


def test_pending_tool_calls_defends_done():
    node = _node()
    ai = AIMessage(content="", tool_calls=[{"name": "read_file", "args": {"path": "x"}, "id": "t1"}])
    out = _run(node({
        "prompt": "x",
        "visited_routes": ["coding"],
        "messages": [HumanMessage(content="x"), ai],
    }))
    assert out["route"] == "done"


if __name__ == "__main__":
    import pytest
    raise SystemExit(pytest.main([__file__, "-q"]))
