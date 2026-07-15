# Feature: langgraph-reasoning-upgrade, Task 11.1: 아키텍처 정적 제약 smoke 테스트
"""신규 코드의 아키텍처 정적 제약 smoke 테스트(1회 검증, 네트워크 없음).

Validates: Requirements 6.5, 7.2, 8.4, 9.1

검증 항목:
- 신규 소스(supervisor.py/dag.py/graph_state.py/deps.py)에 boto3/anthropic/openai 직접
  import 부재(요구사항 7.2 — 모든 LLM 호출은 GatewayChatModel 경유).
- 신규 LLM 호출 노드에 `wait_for(...ainvoke...)` 패턴 존재, 스트림 소비 루프(`async for`)를
  wait_for 로 감싸지 않음(요구사항 8.1~8.4 — 무한대기 차단).
- GraphDeps 에 model_planner/model_generator/model_evaluator 필드 존재 + 기본값 Opus/
  Sonnet/Opus(요구사항 9.1~9.4).
- 플래그 on/off 조립 스냅샷(요구사항 6.2/6.3 무회귀).

실행:
  ai_engine/.venv/bin/python -m pytest scripts/test_reasoning_upgrade_smoke.py -q
"""
from __future__ import annotations

import os
import re
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

_ROOT = os.path.join(os.path.dirname(__file__), "..")
_AGENT = os.path.join(_ROOT, "ai_engine", "agent_system")
_NEW_SOURCES = [
    os.path.join(_AGENT, "supervisor.py"),
    os.path.join(_AGENT, "dag.py"),
    os.path.join(_AGENT, "graph_state.py"),
    os.path.join(_AGENT, "deps.py"),
]


def _read(path: str) -> str:
    with open(path, "r", encoding="utf-8") as f:
        return f.read()


# ── 직접 SDK import 부재 (요구사항 7.2) ───────────────────────────────────────
def test_no_direct_sdk_imports():
    """신규 소스에 boto3/anthropic/openai 직접 import 부재(모든 LLM 은 Gateway 경유)."""
    forbidden = [
        re.compile(r"^\s*import\s+boto3", re.M),
        re.compile(r"^\s*from\s+boto3", re.M),
        re.compile(r"^\s*import\s+anthropic", re.M),
        re.compile(r"^\s*from\s+anthropic", re.M),
        re.compile(r"^\s*import\s+openai", re.M),
        re.compile(r"^\s*from\s+openai", re.M),
    ]
    for path in _NEW_SOURCES:
        src = _read(path)
        for pat in forbidden:
            assert not pat.search(src), f"{os.path.basename(path)} 에 금지된 SDK import: {pat.pattern}"


# ── wait_for(ainvoke) 패턴 존재 + 스트림 루프 미포장 (요구사항 8.1~8.4) ───────
def test_llm_calls_use_wait_for_ainvoke():
    """신규 LLM 노드(evaluator/aggregate)에 asyncio.wait_for(llm.ainvoke(...)) 패턴 존재."""
    src = _read(os.path.join(_AGENT, "supervisor.py"))
    # wait_for 로 감싼 ainvoke 호출이 최소 1회 이상 존재.
    pat = re.compile(r"asyncio\.wait_for\(\s*llm\.ainvoke\(", re.S)
    matches = pat.findall(src)
    assert matches, "wait_for(llm.ainvoke(...)) 패턴이 supervisor.py 에 없음"
    # aggregate/evaluator 두 신규 노드 각각에 개별 await 타임아웃 래핑이 존재하므로
    # 최소 2회 이상 등장해야 한다(요구사항 8.1/8.2).
    assert len(matches) >= 2, (
        f"aggregate/evaluator 양쪽 wait_for(ainvoke) 패턴 부족(발견 {len(matches)}회)"
    )
    # 개별 await 하나만 감싸는 패턴이므로, 스트림 소비 루프(async for)를 wait_for 로
    # 감싸지 않는다 — async for 를 인자로 받는 wait_for 가 없어야 한다.
    bad = re.compile(r"wait_for\([^)]*async\s+for", re.S)
    assert not bad.search(src), "async for 루프를 wait_for 로 감쌈(무한대기 위험)"


def test_aggregate_and_evaluator_each_wrap_ainvoke_with_timeout():
    """요구사항 8.1/8.2: aggregate 는 AE_AGGREGATE_TIMEOUT, evaluator 는 AE_EVALUATOR_TIMEOUT
    으로 각각 llm.ainvoke 개별 await 를 wait_for 로 감싼다."""
    src = _read(os.path.join(_AGENT, "supervisor.py"))
    # aggregate 노드: AE_AGGREGATE_TIMEOUT 을 인자로 하는 wait_for(llm.ainvoke(...)).
    agg_pat = re.compile(
        r"asyncio\.wait_for\(\s*llm\.ainvoke\([^)]*\)\s*,\s*timeout=AE_AGGREGATE_TIMEOUT",
        re.S,
    )
    assert agg_pat.search(src), "aggregate 의 wait_for(llm.ainvoke, AE_AGGREGATE_TIMEOUT) 패턴 부재"
    # evaluator 노드: AE_EVALUATOR_TIMEOUT 을 인자로 하는 wait_for(llm.ainvoke(...)).
    eval_pat = re.compile(
        r"asyncio\.wait_for\(\s*llm\.ainvoke\([^)]*\)\s*,\s*timeout=AE_EVALUATOR_TIMEOUT",
        re.S,
    )
    assert eval_pat.search(src), "evaluator 의 wait_for(llm.ainvoke, AE_EVALUATOR_TIMEOUT) 패턴 부재"


# ── GraphDeps 모델 역할 필드 (요구사항 9.1~9.4) ───────────────────────────────
def test_graphdeps_has_model_role_fields():
    """GraphDeps 에 model_planner/model_generator/model_evaluator 필드 존재 + 기본값 역할 정합."""
    from ai_engine.agent_system.deps import GraphDeps

    deps = GraphDeps()
    for field in ("model_planner", "model_generator", "model_evaluator"):
        assert hasattr(deps, field), f"GraphDeps 에 {field} 필드 없음"
    # 역할 배분: Planner=Opus, Generator=Sonnet, Evaluator=Opus.
    assert "opus" in deps.model_planner.lower()
    assert "sonnet" in deps.model_generator.lower()
    assert "opus" in deps.model_evaluator.lower()


def test_graphdeps_model_roles_injectable():
    """요구사항 9.5: 주입된 model_id 를 그대로 사용."""
    from ai_engine.agent_system.deps import GraphDeps

    deps = GraphDeps(model_planner="P", model_generator="G", model_evaluator="E")
    assert deps.model_planner == "P"
    assert deps.model_generator == "G"
    assert deps.model_evaluator == "E"


# ── 플래그 on/off 조립 스냅샷 (요구사항 6.2/6.3) ──────────────────────────────
def _snapshot(evaluator: str, dag: str):
    import ai_engine.agent_system.supervisor as sup
    from ai_engine.agent_system.deps import GraphDeps

    prev_e = os.environ.get("AE_ENABLE_EVALUATOR")
    prev_d = os.environ.get("AE_ENABLE_DAG_PLANNER")
    os.environ["AE_ENABLE_EVALUATOR"] = evaluator
    os.environ["AE_ENABLE_DAG_PLANNER"] = dag
    try:
        gg = sup.build_parallel_top_graph(GraphDeps(gateway=None)).get_graph()
        return set(gg.nodes.keys()), {(e.source, e.target) for e in gg.edges}
    finally:
        for key, prev in (("AE_ENABLE_EVALUATOR", prev_e), ("AE_ENABLE_DAG_PLANNER", prev_d)):
            if prev is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = prev


def test_assembly_snapshot_flag_off_vs_on():
    """무회귀: 둘 다 off → evaluator 없음/aggregate→END. on → evaluator + 다중 Wave/refine 루프."""
    nodes_off, edges_off = _snapshot("0", "0")
    assert "evaluator" not in nodes_off
    assert ("aggregate", "__end__") in edges_off
    assert ("aggregate", "planner") not in edges_off

    nodes_on, edges_on = _snapshot("1", "1")
    assert "evaluator" in nodes_on
    assert ("aggregate", "planner") in edges_on      # 다중 Wave 진행
    assert ("aggregate", "evaluator") in edges_on    # 모든 Wave 완료 후 평가
    assert ("evaluator", "planner") in edges_on      # refine 재계획
    assert ("evaluator", "__end__") in edges_on


def test_build_top_graph_unchanged_sequential():
    """무회귀: 순차 그래프(build_top_graph)는 evaluator 결선 없이 기존 router 구조 유지."""
    import ai_engine.agent_system.supervisor as sup
    from ai_engine.agent_system.deps import GraphDeps

    gg = sup.build_top_graph(GraphDeps(gateway=None)).get_graph()
    nodes = set(gg.nodes.keys())
    assert "router" in nodes
    assert "evaluator" not in nodes  # 순차 그래프는 evaluator 미포함(병렬 그래프에만 추가)
    assert "aggregate" not in nodes


if __name__ == "__main__":
    import pytest

    raise SystemExit(pytest.main([__file__, "-q"]))
