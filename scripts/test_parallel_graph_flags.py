# Feature: langgraph-reasoning-upgrade, Property 2: 무회귀 구성 (플래그 off)
"""Property 2: 무회귀 구성 — build_parallel_top_graph 플래그 on/off 노드·엣지 스냅샷.

Validates: Requirements 6.2, 6.3, 4.7

design.md Correctness Property 2 발췌:
    For any 플래그 조합에서 AE_ENABLE_EVALUATOR 와 AE_ENABLE_DAG_PLANNER 가 모두 off 이면,
    build_parallel_top_graph 가 조립한 그래프의 노드/엣지 구성은 evaluator 노드 없이
    aggregate → END 로 끝나고, plan_dispatch 는 depends_on 을 무시한 단일 Wave fan-out 으로
    동작하여 기존 그래프와 동일하다.

design.md 검증 방식 노트:
    이 속성은 조립 구성(노드/엣지 집합)의 동치성을 검사하므로 무작위 입력보다 플래그
    조합에 대한 스냅샷 비교(example-like)로 구현한다.

대상 코드(실측):
- ai_engine/agent_system/supervisor.py 의 build_parallel_top_graph, plan_dispatch.

실행:
  ai_engine/.venv/bin/python -m pytest scripts/test_parallel_graph_flags.py -q
"""
from __future__ import annotations

import os
import sys
from contextlib import contextmanager
from types import SimpleNamespace

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from langchain_core.messages import HumanMessage
from langgraph.types import Send

import ai_engine.agent_system.supervisor as sup


# ── 헬퍼 ─────────────────────────────────────────────────────────────────────
def _deps():
    """네트워크 없는 조립 전용 deps(gateway=None 이면 compile 은 정상 동작)."""
    return SimpleNamespace(
        gateway=None,
        model_coding="m",
        model_planner="p",
        model_generator="g",
        model_evaluator="e",
        checkpointer=None,
        store=None,
    )


@contextmanager
def _flags(evaluator: str, dag: str):
    """AE_ENABLE_EVALUATOR / AE_ENABLE_DAG_PLANNER env 를 임시 설정 후 복원."""
    prev = {
        "AE_ENABLE_EVALUATOR": os.environ.get("AE_ENABLE_EVALUATOR"),
        "AE_ENABLE_DAG_PLANNER": os.environ.get("AE_ENABLE_DAG_PLANNER"),
    }
    os.environ["AE_ENABLE_EVALUATOR"] = evaluator
    os.environ["AE_ENABLE_DAG_PLANNER"] = dag
    try:
        yield
    finally:
        for k, v in prev.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v


def _graph_snapshot():
    """현재 env 플래그로 build_parallel_top_graph 조립 후 (nodes, edges) 스냅샷 반환.

    edges 는 (source, target, conditional) 튜플 집합으로 반환한다.
    """
    cg = sup.build_parallel_top_graph(_deps())
    gg = cg.get_graph()
    nodes = set(gg.nodes.keys())
    edges = {
        (e.source, e.target, bool(getattr(e, "conditional", False))) for e in gg.edges
    }
    return nodes, edges


_END = "__end__"
_START = "__start__"
_WORKERS = ("coding", "media", "research", "ops", "chat")


# ── 스냅샷 테스트: 두 플래그 모두 off (무회귀 기준선) ─────────────────────────
def test_both_flags_off_matches_legacy_aggregate_to_end():
    """Req 6.2/6.3: 둘 다 off → evaluator 노드 없음, aggregate → END(직접 엣지)."""
    with _flags("0", "0"):
        nodes, edges = _graph_snapshot()

    # evaluator 노드가 존재하지 않는다(무회귀).
    assert "evaluator" not in nodes
    # 기대 노드 집합: start/end + planner + 5 워커 + aggregate.
    assert nodes == {_START, _END, "planner", "aggregate", *_WORKERS}

    # aggregate → END 는 직접(비조건) 엣지여야 한다(기존 동작 보존).
    assert ("aggregate", _END, False) in edges
    # aggregate 에서 나가는 다른 엣지(planner/evaluator)는 없어야 한다.
    agg_targets = {t for (s, t, _c) in edges if s == "aggregate"}
    assert agg_targets == {_END}
    # planner 로 '되돌아오는' 순환 엣지가 없다(START→planner 진입 엣지는 제외).
    return_edges_to_planner = [
        (s, t) for (s, t, _c) in edges if t == "planner" and s != _START
    ]
    assert not return_edges_to_planner, f"예상치 못한 planner 복귀 엣지: {return_edges_to_planner}"


def test_both_flags_off_workers_go_to_aggregate():
    """둘 다 off: START→planner, 각 워커→aggregate 결선 유지."""
    with _flags("0", "0"):
        _nodes, edges = _graph_snapshot()
    assert (_START, "planner", False) in edges
    for w in _WORKERS:
        assert (w, "aggregate", False) in edges


# ── 스냅샷 테스트: 두 플래그 모두 on (고도화 구성) ───────────────────────────
def test_both_flags_on_adds_evaluator_and_conditionals():
    """Req 1.1/6.1: 둘 다 on → evaluator 노드 추가 + evaluator conditional{planner, END}."""
    with _flags("1", "1"):
        nodes, edges = _graph_snapshot()

    assert "evaluator" in nodes
    # evaluator conditional 은 planner(재계획) 과 END(종료) 두 대상만 가진다.
    eval_targets = {t for (s, t, _c) in edges if s == "evaluator"}
    assert eval_targets == {"planner", _END}
    for (s, t, c) in edges:
        if s == "evaluator":
            assert c is True  # conditional edge

    # DAG on: aggregate 는 conditional 로 planner(다음 Wave)/evaluator/END 를 가진다.
    agg_targets = {t for (s, t, _c) in edges if s == "aggregate"}
    assert agg_targets == {"planner", "evaluator", _END}


def test_evaluator_off_dag_on_aggregate_to_end_no_evaluator():
    """Req 6.2: evaluator off + DAG on → evaluator 노드 없음, aggregate→{planner, END}."""
    with _flags("0", "1"):
        nodes, edges = _graph_snapshot()
    assert "evaluator" not in nodes
    agg_targets = {t for (s, t, _c) in edges if s == "aggregate"}
    # DAG on 이므로 다중 Wave 진행용 planner 복귀 + 완료 시 END.
    assert agg_targets == {"planner", _END}


def test_evaluator_on_dag_off_direct_aggregate_to_evaluator():
    """Req 6.3: evaluator on + DAG off → 단일 Wave, aggregate→evaluator 직접 엣지."""
    with _flags("1", "0"):
        nodes, edges = _graph_snapshot()
    assert "evaluator" in nodes
    # DAG off → aggregate 이후 곧바로 evaluator(직접 엣지).
    assert ("aggregate", "evaluator", False) in edges
    agg_targets = {t for (s, t, _c) in edges if s == "aggregate"}
    assert agg_targets == {"evaluator"}


# ── plan_dispatch: DAG off 단일 Wave(depends_on 무시) 동작 ───────────────────
def _chain_plan():
    """depends_on 체인(t0 → t1 → t2)을 가진 3-서브태스크 plan."""
    return [
        {"id": "t0", "domain": "coding", "subtask": "분석", "depends_on": []},
        {"id": "t1", "domain": "research", "subtask": "조사", "depends_on": ["t0"]},
        {"id": "t2", "domain": "media", "subtask": "PPT", "depends_on": ["t1"]},
    ]


def test_plan_dispatch_dag_off_single_wave_ignores_depends_on():
    """Req 4.7: DAG off → depends_on 무시, 전체 plan 을 단일 Wave 로 fan-out."""
    state = {"plan": _chain_plan(), "prompt": "요청", "messages": []}
    with _flags("0", "0"):
        sends = sup.plan_dispatch(state)
    assert all(isinstance(s, Send) for s in sends)
    # 단일 Wave: depends_on 무시하고 3개 모두 dispatch(상한 이하).
    assert len(sends) == 3
    domains = {s.node for s in sends}
    assert domains == {"coding", "research", "media"}


def test_plan_dispatch_dag_on_first_wave_only():
    """DAG on → topological_waves 로 Wave 0(선행 없는 t0)만 dispatch."""
    state = {"plan": _chain_plan(), "prompt": "요청", "messages": [], "completed_waves": 0}
    with _flags("1", "1"):
        sends = sup.plan_dispatch(state)
    # 체인이므로 Wave 0 = [t0] 하나만.
    assert len(sends) == 1
    assert sends[0].node == "coding"


def test_plan_dispatch_respects_max_parallel_cap():
    """Req 4.6: 어느 플래그든 동시 Send 수 ≤ MAX_PARALLEL_TASKS."""
    big_plan = [
        {"id": f"t{i}", "domain": "chat", "subtask": f"작업{i}", "depends_on": []}
        for i in range(sup.MAX_PARALLEL_TASKS + 5)
    ]
    state = {"plan": big_plan, "prompt": "요청", "messages": []}
    with _flags("0", "0"):
        sends_off = sup.plan_dispatch(state)
    with _flags("1", "1"):
        sends_on = sup.plan_dispatch({**state, "completed_waves": 0})
    assert len(sends_off) <= sup.MAX_PARALLEL_TASKS
    assert len(sends_on) <= sup.MAX_PARALLEL_TASKS


# ── recursion 안전: 두 플래그 조합 모두 컴파일 가능(순환 유한 종료 구조) ──────
def test_all_flag_combinations_compile():
    """AE_ENABLE_EVALUATOR on/off × DAG on/off 4조합 모두 정상 컴파일(recursion 안전).

    evaluator conditional 은 refine_count>=cap 시 done 을 반환(Task8)하고, aggregate
    conditional 의 다중 Wave 복귀는 Wave 수(≤서브태스크 수) 이하로 유한 종료되므로
    planner↔aggregate↔evaluator 순환은 유한하다. 여기서는 4조합 컴파일 성공을 확인한다.
    """
    for ev in ("0", "1"):
        for dag in ("0", "1"):
            with _flags(ev, dag):
                cg = sup.build_parallel_top_graph(_deps())
                assert cg is not None
                # evaluator 노드 존재 여부가 플래그와 일치.
                nodes = set(cg.get_graph().nodes.keys())
                assert ("evaluator" in nodes) == (ev == "1")


if __name__ == "__main__":
    import pytest

    raise SystemExit(pytest.main([__file__, "-q"]))
