"""Top Supervisor graph-of-graphs 재라우팅 통합 테스트 (Gateway mock).

검증 대상 (요구사항 1.3 / 1.4 / 1.5):
- 1.3: Top_Supervisor 가 결정한 Route 에 대응하는 Domain_Subgraph 로 실행이 전달된다.
- 1.5: 하나의 Domain_Subgraph 실행이 끝나면 router 로 복귀해 재라우팅한다(멀티도메인 체이닝).
- 1.4: Route 가 done 이면 그래프가 END 로 종료된다.

접근(제약 준수):
- 네트워크 없음: GatewayChatModel 이 호출하는 gateway.converse 를 mock(ScriptedGateway)으로 대체.
- LLM 직접 SDK 없음: 전부 mock Gateway 경유.
- 서브그래프 model 노드는 tool_calls 없는 평문 응답으로 mock → 각 서브그래프가
  model→verify→END 로 즉시 종료(도구 루프 없음).
- verify 노드의 강제 생성 폴백(_force_generate_from_text)은 async 스텁으로 대체(실파일
  생성/네트워크 차단 — 라우팅 검증과 무관한 실산출물 경로 제거).
- checkpointer: tmp 디렉토리 기반 JsonFileCheckpointSaver.
- 유한 시간: 그래프 실행을 asyncio.wait_for(GRAPH_ASSERT_TIMEOUT) + 짧은 recursion_limit
  로 감싸 무한대기 원천 차단.

⚠️ 방문 순서 확인 방식 (중요):
  build_top_graph 는 각 도메인 서브그래프를 노드로 add 하며, 서브그래프는 부모와 동일한
  GraphState 채널을 공유한다. `visited_routes` 는 operator.add 리듀서를 쓰므로, 서브그래프가
  종료되며 부모로 돌아올 때 자신이 받은 visited_routes 값이 부모 리듀서에 재합산되어 값이
  **중복 누적**된다(graph-of-graphs 에서 공유 리듀서 채널의 알려진 특성). 따라서 visited_routes
  의 '정확한 나열'에 의존하지 않고, 각 서브그래프 model 노드가 GatewayChatModel.bind_tools 로
  넘긴 **도메인 고유 도구 집합**으로 실제 방문한 서브그래프를 식별한다(중복 리듀서와 무관한
  신뢰 신호). 라우터 분류 호출(select_route)과 서브그래프 model 호출은 tool_config 로 구분한다.

실행: ai_engine/.venv/bin/python -m pytest scripts/test_langgraph_reroute_integration.py -q
"""
from __future__ import annotations

import asyncio
import os
import sys
import tempfile

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from langchain_core.messages import HumanMessage

import ai_engine.server as server_mod
from ai_engine.agent_system import supervisor as S
from ai_engine.agent_system.checkpoint_store import JsonFileCheckpointSaver
from ai_engine.agent_system.deps import GraphDeps
from ai_engine.agent_system.subgraphs import (
    CODING_TOOLS,
    MEDIA_TOOLS,
    OPS_TOOLS,
    RESEARCH_TOOLS,
)


# 그래프 실행 전체 상한(초) — 무한대기 방지. mock 이라 실제로는 즉시 끝난다.
GRAPH_ASSERT_TIMEOUT = 30.0
# recursion_limit — router↔subgraph 왕복이 얕으므로 짧게 잡아 폭주를 조기 차단.
GRAPH_RECURSION_LIMIT = 25

# 도메인 고유 도구 집합(정확 매칭) — model 노드의 bind_tools 에 넘긴 tool 이름 집합으로
# 실제 방문한 서브그래프를 식별한다. 각 도메인의 toolSpec name 집합은 서로 다르다.
_DOMAIN_BY_TOOLSET = {
    frozenset(t["name"] for t in CODING_TOOLS): "coding",
    frozenset(t["name"] for t in MEDIA_TOOLS): "media",
    frozenset(t["name"] for t in RESEARCH_TOOLS): "research",
    frozenset(t["name"] for t in OPS_TOOLS): "ops",
}


# ─────────────────────────────────────────────────────────────────────────────
# mock Gateway
# ─────────────────────────────────────────────────────────────────────────────
class ScriptedGateway:
    """GatewayChatModel 이 호출하는 async converse 만 제공하는 최소 mock.

    - tool_config 에 select_route toolSpec 이 있으면 '라우터 분류 호출'로 보고
      route_sequence 를 순서대로 소비해 select_route toolUse 를 반환한다.
    - 그 외(서브그래프 model 노드)면 tool_calls 없는 평문 텍스트를 반환하고, bind_tools 로
      넘어온 도구 집합으로 방문 도메인을 기록한다(visited_domains).
    """

    def __init__(self, route_sequence):
        self.route_sequence = list(route_sequence)
        self._route_idx = 0
        self.router_routes = []     # 라우터가 반환한 route 순서(gateway 실제 분류 호출)
        self.visited_domains = []   # 서브그래프 model 노드 방문 도메인 순서(신뢰 신호)

    @staticmethod
    def _tool_names(tool_config):
        if not isinstance(tool_config, dict):
            return frozenset()
        names = set()
        for t in tool_config.get("tools", []) or []:
            spec = t.get("toolSpec", {}) if isinstance(t, dict) else {}
            n = spec.get("name")
            if n:
                names.add(n)
        return frozenset(names)

    async def converse(
        self,
        model_id=None,
        messages=None,
        system_prompt=None,
        tool_config=None,
        **kwargs,
    ):
        await asyncio.sleep(0)  # 진짜 코루틴처럼 동작(네트워크 없음).
        names = self._tool_names(tool_config)

        if "select_route" in names:
            # 라우터 분류 호출.
            if self._route_idx < len(self.route_sequence):
                route = self.route_sequence[self._route_idx]
            else:
                route = "done"
            self._route_idx += 1
            self.router_routes.append(route)
            subtask = "" if route == "done" else f"{route} 도메인 작업을 수행한다"
            return _tool_use_result("route-%d" % self._route_idx, "select_route",
                                    {"route": route, "subtask": subtask})

        # 서브그래프 model 노드 — 도구 집합으로 도메인 식별 후 평문 응답.
        domain = _DOMAIN_BY_TOOLSET.get(names, "chat" if not names else "unknown")
        self.visited_domains.append(domain)
        return {
            "output": {
                "message": {
                    "content": [{"text": f"[mock:{domain}] 요청한 작업을 처리했습니다."}]
                }
            }
        }


def _tool_use_result(tool_use_id, name, args):
    return {
        "output": {
            "message": {
                "content": [
                    {"toolUse": {"toolUseId": tool_use_id, "name": name, "input": args}}
                ]
            }
        }
    }


# ─────────────────────────────────────────────────────────────────────────────
# 강제 생성 폴백 스텁 — verify 노드가 실파일 생성/네트워크로 새지 않도록 차단.
# ─────────────────────────────────────────────────────────────────────────────
_orig_force_generate = getattr(server_mod, "_force_generate_from_text", None)


async def _stub_force_generate(*args, **kwargs):
    return []


def _install_force_generate_stub():
    server_mod._force_generate_from_text = _stub_force_generate


def _restore_force_generate():
    if _orig_force_generate is not None:
        server_mod._force_generate_from_text = _orig_force_generate


# ─────────────────────────────────────────────────────────────────────────────
# 헬퍼: 그래프 조립 + 유한 시간 실행
# ─────────────────────────────────────────────────────────────────────────────
def _build_graph(gateway, checkpoint_dir):
    saver = JsonFileCheckpointSaver(base_dir=checkpoint_dir)
    deps = GraphDeps(gateway=gateway, checkpointer=saver)
    return S.build_top_graph(deps)


async def _run_graph(graph, prompt, thread_id):
    initial = {
        "prompt": prompt,
        "messages": [HumanMessage(content=prompt)],
        "visited_routes": [],
        "iteration": 0,
        # project_path 미설정 → retrieve 는 RAG 스킵(비차단). 자격증명 미포함(문자열만).
    }
    config = {
        "configurable": {"thread_id": thread_id},
        "recursion_limit": GRAPH_RECURSION_LIMIT,
    }
    return await asyncio.wait_for(
        graph.ainvoke(initial, config=config), timeout=GRAPH_ASSERT_TIMEOUT
    )


def _run(coro):
    return asyncio.run(coro)


# ─────────────────────────────────────────────────────────────────────────────
# 테스트
# ─────────────────────────────────────────────────────────────────────────────
def test_reroute_coding_then_media():
    """coding 서브그래프 → 재라우팅 → media 서브그래프 순서 방문 (요구사항 1.3 / 1.5).

    라우터가 첫 hop 에서 coding, 둘째 hop 에서 media 로 분류한다. 각 서브그래프가 실제로
    그 순서대로 실행되었는지를 서브그래프 model 노드의 도구 집합(도메인 식별)으로 검증한다.
    그래프는 유한 시간에 종료되며 최종 route 는 done 이다(요구사항 1.4).
    """
    _install_force_generate_stub()
    try:
        # coding, media 이후는 done(시퀀스 소진 시 자동 done). hop cap 도 종료를 보장.
        gateway = ScriptedGateway(["coding", "media", "done"])
        with tempfile.TemporaryDirectory(prefix="lg_ckpt_") as ckpt_dir:
            graph = _build_graph(gateway, ckpt_dir)
            final = _run(
                _run_graph(
                    graph,
                    "이 코드를 분석하고 그 결과로 발표자료(PPT)를 만들어줘",
                    thread_id="t-reroute-1",
                )
            )

        # (요구사항 1.3 / 1.5) 서브그래프가 coding→media 순서로 실제 방문됨(도구 집합 신뢰 신호).
        assert gateway.visited_domains == ["coding", "media"], (
            f"서브그래프 방문 순서 불일치: {gateway.visited_domains}"
        )
        # (요구사항 1.5) 라우터가 coding, media 를 순서대로 분류(재라우팅 발생).
        assert gateway.router_routes[:2] == ["coding", "media"], (
            f"라우터 분류 순서 불일치: {gateway.router_routes}"
        )
        # (요구사항 1.4) done 으로 유한 종료.
        assert final.get("route") == "done", f"최종 route 불일치: {final.get('route')}"
    finally:
        _restore_force_generate()


def test_scripted_done_terminates_at_end():
    """서브그래프 1회 실행 후 라우터가 done 을 결정하면 END 로 종료 (요구사항 1.4 / 1.5).

    첫 진입은 done 불가(도메인 강제)이므로 coding 을 실행하고 router 로 복귀한 뒤(1.5),
    재진입에서 라우터 LLM(mock)이 done 을 반환해 그래프가 END 로 종료된다(1.4).
    """
    _install_force_generate_stub()
    try:
        gateway = ScriptedGateway(["coding", "done"])
        with tempfile.TemporaryDirectory(prefix="lg_ckpt_") as ckpt_dir:
            graph = _build_graph(gateway, ckpt_dir)
            final = _run(
                _run_graph(graph, "이 함수를 리팩터링해줘", thread_id="t-done-1")
            )

        # coding 서브그래프 1회만 방문.
        assert gateway.visited_domains == ["coding"], (
            f"서브그래프 방문 불일치: {gateway.visited_domains}"
        )
        # 라우터가 coding 후 done 을 결정(재진입 완료 판정).
        assert gateway.router_routes == ["coding", "done"], (
            f"라우터 분류 순서 불일치: {gateway.router_routes}"
        )
        assert final.get("route") == "done"
    finally:
        _restore_force_generate()


def test_hop_cap_forces_finite_termination():
    """라우터가 계속 도메인을 반환해도 hop cap 으로 유한 종료 (요구사항 1.4 / 6.5).

    done 을 절대 스크립트하지 않아도, MAX_ROUTE_HOPS 가 재라우팅 순환을 유한하게 끊어
    route=done 으로 종료해야 한다(무한대기 방지 — 과거 hang 이력 대응).
    """
    _install_force_generate_stub()
    try:
        gateway = ScriptedGateway(["coding"] * 50)  # done 없음.
        with tempfile.TemporaryDirectory(prefix="lg_ckpt_") as ckpt_dir:
            graph = _build_graph(gateway, ckpt_dir)
            final = _run(
                _run_graph(graph, "코드를 계속 고쳐줘", thread_id="t-cap-1")
            )

        # 유한 종료: route=done.
        assert final.get("route") == "done", f"유한 종료 실패: route={final.get('route')}"
        # 라우터 분류 호출이 무한하지 않고 유계(hop cap 이내). recursion_limit 미만이어야 한다.
        assert 0 < len(gateway.router_routes) < GRAPH_RECURSION_LIMIT, (
            f"라우터 호출 수 유계 위반: {len(gateway.router_routes)}"
        )
        # 방문한 서브그래프는 모두 coding(다른 도메인으로 새지 않음).
        assert set(gateway.visited_domains) <= {"coding"}, (
            f"예상 밖 도메인 방문: {gateway.visited_domains}"
        )
    finally:
        _restore_force_generate()


if __name__ == "__main__":
    import pytest

    raise SystemExit(pytest.main([__file__, "-q"]))
