"""Property 4: 그래프는 유한 시간에 종료 (무한대기 없음) — hypothesis 기반 PBT.

Validates: Requirements 6.4, 6.5, 6.6, 6.7 (design.md Correctness Property 4).

design.md 발췌:
    assert graph_run_duration <= AE_GRAPH_TOTAL_TIMEOUT
    assert route_hops <= AE_MAX_ROUTE_HOPS
    assert subgraph_model_tool_roundtrips <= AE_SUBGRAPH_RECURSION

검증 대상 코드(실측):
- `agent_system/supervisor.py`: `make_top_router_node` / `route_selector` / `MAX_ROUTE_HOPS`.
    · 라우터는 어떤 route 결정 시퀀스(적대적: 절대 done 을 내지 않는 경우 포함)를 반환해도
      `route_hops`(last-wins, echo 면역)가 `MAX_ROUTE_HOPS` 에 도달하면 LLM 호출 없이
      route="done" 을 강제 반환하여 재라우팅 순환을 유한 hop 안에 종료한다(요구사항 6.5 / 6.7).
- `agent_system/subgraphs/_common.py`: `tools_condition_or_verify` / `SUBGRAPH_RECURSION_LIMIT`.
    · model 이 매 턴 tool_calls 를 계속 내는 악성 ReAct 루프에서도 `iteration` 이
      `SUBGRAPH_RECURSION_LIMIT` 에 도달하면 강제로 "verify" 로 라우팅되어 model↔tool
      왕복이 유한하게 종료된다(요구사항 6.4 / 6.7).
- 실그래프 종료(요구사항 6.6): mock LLM + 짧은 recursion_limit + asyncio.wait_for 로
  build_top_graph 를 실제 실행해, 라우터가 절대 done 을 내지 않아도 hop cap 에 의해
  유한 시간(타임아웃 이내)에 END 로 종료함을 실측한다.

전략:
- Gateway/LLM 은 mock 으로 대체(직접 네트워크 호출 금지). 라우터 분류(`_classify_route`)를
  hypothesis 가 생성한 임의의 결정 시퀀스로 monkeypatch 해 적대적 시나리오를 포괄한다.
- reducer(visited_routes=operator.add, route=last-wins, messages=add_messages)를 실그래프와
  동일하게 적용해 라우터 루프를 시뮬레이션한다.
- hypothesis: max_examples 60, deadline=None(유한 예제 상한). 각 시뮬레이션 루프는 안전
  상한(HARD_CAP)으로 이중 방어 — 실패 시 즉시 assert 로 드러난다.

실행:
  ai_engine/.venv/bin/python -m pytest scripts/test_langgraph_termination_pbt.py -q
"""
from __future__ import annotations

import asyncio
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from hypothesis import given, settings, strategies as st
from langchain_core.messages import AIMessage, HumanMessage

from ai_engine.agent_system.deps import GraphDeps
from ai_engine.agent_system import supervisor as S
from ai_engine.agent_system.subgraphs import _common as C


# ─────────────────────────────────────────────────────────────────────────────
# 공통 유틸
# ─────────────────────────────────────────────────────────────────────────────
def _run(coro):
    return asyncio.run(coro)


# 라우터가 선택할 수 있는 도메인 라벨(적대적: done 을 절대 내지 않는 시퀀스 포함).
_DOMAINS = list(S._ROUTE_LABELS)                    # coding/media/research/ops/chat
_DOMAINS_WITH_DONE = list(S._ROUTE_LABELS_WITH_DONE)  # + done


# ─────────────────────────────────────────────────────────────────────────────
# Property 4-A: 라우터 hop cap — 임의 route 시퀀스에서도 유한 hop 안에 done 종료
#   (요구사항 6.5 / 6.7)
# ─────────────────────────────────────────────────────────────────────────────
async def _simulate_router_loop(decisions, max_hops):
    """실 top_router_node + route_selector + reducer 로 라우터 루프를 돌린다.

    `_classify_route` 를 decisions 시퀀스(소진 시 순환)를 반환하도록 monkeypatch 한다.
    적대적: decisions 가 전부 도메인(비-done)이면 라우터는 절대 스스로 done 을 못 내지만,
    hop cap 이 유한 종료를 강제해야 한다.

    Returns: 종료 시 state(dict). route 는 반드시 "done".
    """
    orig_hops = S.MAX_ROUTE_HOPS
    orig_classify = S._classify_route
    S.MAX_ROUTE_HOPS = max_hops

    seq = list(decisions) or ["chat"]
    idx = {"i": 0}

    async def fake_classify(state, deps, allow_done=False):
        label = seq[idx["i"] % len(seq)]
        idx["i"] += 1
        return (label, "다음 작업")

    S._classify_route = fake_classify
    try:
        node = S.make_top_router_node(GraphDeps(gateway=object()))
        state = {"prompt": "요청", "visited_routes": [], "route_hops": 0, "messages": []}
        # 안전 이중 방어: hop cap 이 동작하면 최대 max_hops+1 스텝(마지막 done 판정)에 끝난다.
        hard_cap = max_hops + 5
        steps = 0
        while True:
            out = await node(state)
            # ── 실그래프 reducer 를 그대로 반영 ──
            if out.get("route") is not None:
                state["route"] = out["route"]                       # _take_right(last-wins)
            if "visited_routes" in out:
                state["visited_routes"] = state["visited_routes"] + list(out["visited_routes"])  # operator.add
            if "route_hops" in out:
                state["route_hops"] = out["route_hops"]             # _take_right(last-wins) — echo 면역 hop 계수
            if "messages" in out:
                state["messages"] = state["messages"] + list(out["messages"])                     # add_messages(근사)
            if S.route_selector(state) == "done":
                break
            steps += 1
            assert steps <= hard_cap, (
                f"라우터 루프가 유한 종료하지 못함: steps={steps} > hard_cap={hard_cap} "
                f"(max_hops={max_hops})"
            )
        return state
    finally:
        S.MAX_ROUTE_HOPS = orig_hops
        S._classify_route = orig_classify


@settings(max_examples=60, deadline=None)
@given(
    decisions=st.lists(st.sampled_from(_DOMAINS_WITH_DONE), min_size=1, max_size=12),
    max_hops=st.integers(min_value=1, max_value=6),
)
def test_router_terminates_within_hop_cap(decisions, max_hops):
    """임의(적대적 포함) route 결정 시퀀스에서도 라우터는 hop cap 이내에 done 종료.

    Validates: Requirements 6.5, 6.7
    """
    state = _simulate_router_loop_sync(decisions, max_hops)
    # (1) 반드시 done 으로 종료.
    assert S.route_selector(state) == "done"
    # (2) route_hops(last-wins, echo 면역 hop 계수) <= MAX_ROUTE_HOPS.
    assert state.get("route_hops", 0) <= max_hops, (
        f"route_hops={state.get('route_hops', 0)} > MAX_ROUTE_HOPS={max_hops}"
    )


def _simulate_router_loop_sync(decisions, max_hops):
    return _run(_simulate_router_loop(decisions, max_hops))


@settings(max_examples=60, deadline=None)
@given(max_hops=st.integers(min_value=1, max_value=6))
def test_router_adversarial_never_done_still_terminates(max_hops):
    """적대적 최악: 라우터가 매번 도메인만 골라 절대 done 을 내지 않아도 hop cap 이 종료 강제.

    Validates: Requirements 6.5, 6.7
    """
    # 절대 done 을 포함하지 않는 시퀀스(오직 도메인).
    state = _simulate_router_loop_sync(_DOMAINS, max_hops)
    assert S.route_selector(state) == "done"
    assert state.get("route_hops", 0) <= max_hops


# ─────────────────────────────────────────────────────────────────────────────
# Property 4-B: 서브그래프 model↔tool 왕복 cap — 악성 ReAct 루프도 유한 종료
#   (요구사항 6.4 / 6.7)
# ─────────────────────────────────────────────────────────────────────────────
_TOOL_CALL = [{"name": "read_file", "args": {"path": "x"}, "id": "tc1"}]


def _simulate_react_loop(limit):
    """model 이 매 턴 tool_calls 를 계속 내는 악성 ReAct 루프를 시뮬레이션.

    실 `tools_condition_or_verify` + `SUBGRAPH_RECURSION_LIMIT`(monkeypatch)를 사용한다.
    model 노드는 iteration 을 +1 하므로(=_common.make_model_node 계약), 매 루프에서 iteration
    을 증가시킨 뒤 conditional edge 를 평가한다.

    Returns: (max_iteration, roundtrips). roundtrips = tools 로 되돌아간 횟수.
    """
    orig = C.SUBGRAPH_RECURSION_LIMIT
    C.SUBGRAPH_RECURSION_LIMIT = limit
    try:
        iteration = 0
        roundtrips = 0
        hard_cap = limit + 5
        while True:
            iteration += 1  # model 노드가 iteration+1 을 반환하는 계약 반영
            state = {
                "messages": [AIMessage(content="", tool_calls=list(_TOOL_CALL))],
                "iteration": iteration,
            }
            decision = C.tools_condition_or_verify(state)
            if decision == "verify":
                break
            assert decision == "tools"
            roundtrips += 1
            assert roundtrips <= hard_cap, (
                f"ReAct 루프가 유한 종료하지 못함: roundtrips={roundtrips} > "
                f"hard_cap={hard_cap} (limit={limit})"
            )
        return iteration, roundtrips
    finally:
        C.SUBGRAPH_RECURSION_LIMIT = orig


@settings(max_examples=60, deadline=None)
@given(limit=st.integers(min_value=1, max_value=30))
def test_subgraph_roundtrips_bounded_by_recursion_limit(limit):
    """tool_calls 를 계속 내도 model↔tool 왕복은 SUBGRAPH_RECURSION_LIMIT 이하로 유한 종료.

    Validates: Requirements 6.4, 6.7
    """
    max_iteration, roundtrips = _simulate_react_loop(limit)
    # 왕복 수(=tools 재진입)는 recursion limit 이하.
    assert roundtrips <= limit, f"roundtrips={roundtrips} > limit={limit}"
    # 최종 iteration 도 limit 이하(iteration==limit 도달 시 강제 verify).
    assert max_iteration <= limit, f"max_iteration={max_iteration} > limit={limit}"


@settings(max_examples=30, deadline=None)
@given(iteration=st.integers(min_value=0, max_value=100))
def test_no_tool_calls_always_routes_to_verify(iteration):
    """model 이 tool_calls 를 내지 않으면 iteration 과 무관하게 즉시 verify(유한 종료).

    Validates: Requirements 6.4
    """
    state = {"messages": [AIMessage(content="완료")], "iteration": iteration}
    assert C.tools_condition_or_verify(state) == "verify"


# ─────────────────────────────────────────────────────────────────────────────
# Property 4-C: 실그래프 유한 종료 (요구사항 6.6) — mock LLM + 짧은 recursion_limit + timeout
# ─────────────────────────────────────────────────────────────────────────────
class _MockGateway:
    """GatewayChatModel 이 기대하는 converse 계약만 흉내내는 mock.

    _agenerate 는 gateway.converse(model_id, messages, system_prompt, tool_config) 를
    await 하고 result["output"]["message"] 를 AIMessage 로 변환한다(chat_model_adapter 실측).
    tool_calls 없는 순수 텍스트만 반환 → chat 서브그래프는 model→verify→END 로 즉시 종료.
    네트워크 호출 없음.
    """

    async def converse(self, model_id=None, messages=None, system_prompt=None,
                       tool_config=None, **kwargs):
        return {
            "decision": "ALLOW",
            "output": {
                "message": {
                    "role": "assistant",
                    "content": [{"text": "네, 도와드리겠습니다."}],
                }
            },
        }


async def _run_top_graph_until_done(max_hops, timeout=60.0):
    """실 build_top_graph 를 mock LLM 으로 실행. 라우터는 절대 done 을 안 내도록 monkeypatch.

    hop cap 만이 종료를 보장하는 최악 시나리오. asyncio.wait_for(timeout) 로 감싸 무한대기를
    구조적으로 차단한다(테스트 자체가 유한 시간에 실패/성공).
    """
    # answer_quality off → verify 노드가 gateway/네트워크를 쓰지 않음(완전 무네트워크).
    prev_aq = os.environ.get("AE_ANSWER_QUALITY")
    os.environ["AE_ANSWER_QUALITY"] = "0"

    orig_hops = S.MAX_ROUTE_HOPS
    orig_classify = S._classify_route
    S.MAX_ROUTE_HOPS = max_hops

    # 라우터 LLM 분류 호출 횟수 = 실제 route_hops(design 의 route_hops <= MAX_ROUTE_HOPS).
    # node 는 route_hops < MAX_ROUTE_HOPS 일 때만 _classify_route 를 호출하고, hop cap 도달
    # 시에는 LLM 없이 done 을 반환하므로 이 카운터가 hop cap 의 직접 증거다.
    calls = {"n": 0}

    async def always_chat(state, deps, allow_done=False):
        # 적대적: 재진입에서도 절대 done 을 내지 않는다(오직 hop cap 이 종료를 강제).
        calls["n"] += 1
        return ("chat", "계속 진행")

    S._classify_route = always_chat
    try:
        deps = GraphDeps(gateway=_MockGateway())
        graph = S.build_top_graph(deps)
        state = {
            "prompt": "안녕, 이 프로젝트를 설명해줘",
            "session_id": "term-test",
            "messages": [HumanMessage(content="안녕, 이 프로젝트를 설명해줘")],
            "visited_routes": [],
        }
        config = {
            "configurable": {"thread_id": "term-test"},
            "recursion_limit": 50,  # 짧은 상한: 무한 루프면 GraphRecursionError 로 유한 실패.
        }
        result = await asyncio.wait_for(graph.ainvoke(state, config), timeout=timeout)
        return result, calls["n"]
    finally:
        S.MAX_ROUTE_HOPS = orig_hops
        S._classify_route = orig_classify
        if prev_aq is None:
            os.environ.pop("AE_ANSWER_QUALITY", None)
        else:
            os.environ["AE_ANSWER_QUALITY"] = prev_aq


def test_real_top_graph_terminates_finite_time():
    """실 build_top_graph 가 (라우터가 done 을 안 내도) hop cap 으로 유한 시간에 END 종료.

    Validates: Requirements 6.6, 6.7

    핵심: 라우터가 절대 done 을 내지 않아도 그래프가 asyncio.wait_for(timeout) 이내에
    완료되어야 한다(무한대기 없음 = 유한 종료). route_hops(라우터 LLM 분류 호출 횟수)는
    MAX_ROUTE_HOPS 이하로 제한된다(design: route_hops <= AE_MAX_ROUTE_HOPS).

    참고: state 의 `visited_routes` 는 LangGraph 서브그래프의 공유 채널(operator.add
    reducer) 병합 특성상 중복 누적될 수 있어 hop 수의 직접 지표로 쓰지 않는다. 대신 라우터가
    LLM 분류를 실제 수행한 횟수(node 가 hop cap 미도달일 때만 호출)를 route_hops 로 측정한다.
    """
    max_hops = 3
    # asyncio.wait_for 안에서 완료됐다는 것 자체가 유한 종료 증거(타임아웃이면 TimeoutError).
    result, route_hops = _run(_run_top_graph_until_done(max_hops, timeout=60.0))
    assert isinstance(result, dict)
    # 최종 route 는 done(hop cap 도달로 강제 종료).
    assert result.get("route") == "done"
    # route_hops(라우터 분류 호출) <= MAX_ROUTE_HOPS — 재라우팅이 유한하게 종료됨.
    assert route_hops <= max_hops, f"route_hops={route_hops} > MAX_ROUTE_HOPS={max_hops}"
    # 강화(echo 면역 증거): 노드는 route_hops(last-wins) 로 cap 을 판정하므로, 라우터가 절대
    # done 을 안 내면 정확히 max_hops 번 분류를 수행한 뒤 hop cap 으로 종료한다. 만약 hop cap
    # 을 visited_routes(operator.add) 로 판정했다면 서브그래프 echo 복리 폭증으로 예산이
    # 조기 소진되어 max_hops 보다 적게 호출됐을 것이다. 정확히 일치 = route_hops 의 echo 면역.
    assert route_hops == max_hops, (
        f"route_hops={route_hops} != MAX_ROUTE_HOPS={max_hops} — "
        f"echo 면역 실패(예산 조기 소진 의심)"
    )


if __name__ == "__main__":
    import pytest

    raise SystemExit(pytest.main([__file__, "-q"]))
