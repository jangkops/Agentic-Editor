"""Send fan-out 병렬 + last-wins reducer 회귀 테스트.

검증 (요구사항: 병렬 fan-out 정확성):
- _take_right reducer: last-wins, None 이면 좌측 보존.
- plan_dispatch: plan 개수만큼 Send fan-out, MAX_PARALLEL_TASKS cap, 빈 plan→chat 폴백,
  각 Send arg 에 messages(base+subtask HumanMessage) / visited_routes 포함.
- build_parallel_top_graph: fake gateway 로 compile.
gateway·네트워크 불필요, 유한 시간.
실행: ai_engine/.venv/bin/python -m pytest scripts/test_langgraph_parallel.py -q
"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from langchain_core.messages import HumanMessage

from ai_engine.agent_system.graph_state import _take_right
from ai_engine.agent_system.deps import GraphDeps
from ai_engine.agent_system import supervisor as S


# ── _take_right reducer ──
def test_take_right_last_wins():
    assert _take_right("a", "b") == "b"
    assert _take_right(1, 2) == 2


def test_take_right_none_preserves_left():
    assert _take_right("a", None) == "a"
    assert _take_right(5, None) == 5
    assert _take_right(None, "b") == "b"


# ── plan_dispatch ──
def _base_state(plan):
    return {
        "prompt": "요청",
        "messages": [HumanMessage(content="이전")],
        "plan": plan,
    }


def test_dispatch_fans_out_per_plan():
    st = _base_state([
        {"domain": "coding", "subtask": "리팩터"},
        {"domain": "media", "subtask": "PPT"},
    ])
    sends = S.plan_dispatch(st)
    assert len(sends) == 2
    assert {s.node for s in sends} == {"coding", "media"}
    # 각 워커 state 에 base messages + subtask HumanMessage 가 포함
    for s in sends:
        msgs = s.arg.get("messages")
        assert msgs and isinstance(msgs[-1], HumanMessage)
        assert s.arg.get("visited_routes") == [s.node]


def test_dispatch_empty_plan_falls_back_to_chat():
    sends = S.plan_dispatch({"prompt": "hi", "messages": []})
    assert len(sends) == 1 and sends[0].node == "chat"


def test_dispatch_caps_fanout():
    big = _base_state([{"domain": "chat", "subtask": str(i)} for i in range(20)])
    sends = S.plan_dispatch(big)
    assert len(sends) <= S.MAX_PARALLEL_TASKS


def test_dispatch_unknown_domain_falls_back_chat():
    st = _base_state([{"domain": "not_a_domain", "subtask": "x"}])
    sends = S.plan_dispatch(st)
    assert sends[0].node == "chat"


# ── build_parallel_top_graph compile ──
def test_parallel_graph_compiles():
    g = S.build_parallel_top_graph(GraphDeps(gateway=None))
    assert type(g).__name__ == "CompiledStateGraph"


if __name__ == "__main__":
    import pytest
    raise SystemExit(pytest.main([__file__, "-q"]))
