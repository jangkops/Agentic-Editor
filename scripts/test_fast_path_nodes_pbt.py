# Feature: reasoning-perf-reliability, Property 3: Fast_Path 는 planner·aggregate·evaluator 를 포함하지 않는다
"""Property-based test: Fast_Path 컴파일 그래프의 노드 집합 불변식 검증.

Feature: reasoning-perf-reliability, Property 3: Fast_Path 는 planner·aggregate·evaluator 를 포함하지 않는다
**Validates: Requirements 5.1, 5.2**

For any 도메인 라벨(coding/media/research/ops/chat)에 대해:
    - build_fast_path_graph(deps, domain) 가 만든 컴파일 그래프의 노드 이름 집합(빌트인
      '__start__'/'__end__' 제외)은
        · {'planner', 'aggregate', 'evaluator'} 및 병렬 fan-out 디스패치
          ('router', 'plan_dispatch') 와 교집합이 공집합이고,
        · 정확히 하나의 도메인 서브그래프 노드만 포함한다(정확히 {domain}).

대상 코드(실측):
- ai_engine/agent_system/depth_router.py 의 build_fast_path_graph(deps, domain):
    START → <domain> → END 단일 도메인 서브그래프만 얹어 compile.
- 노드 introspection: LangGraph 의 CompiledStateGraph.get_graph().nodes 키 집합에서
  빌트인('__start__'/'__end__')을 제외해 확인한다.

deps 는 라이브 게이트웨이 없이 최소 GraphDeps + MockGateway 스텁으로 구성한다
(네트워크/자격증명/비용 없음).

실행: ai_engine/.venv/bin/python -m pytest scripts/test_fast_path_nodes_pbt.py -q
Stack: Python 3.11+, hypothesis library.
"""
from __future__ import annotations

import os
import sys

# repo 루트를 import 경로에 추가해 ai_engine 패키지를 로드한다
# (depth_router 가 지연 import 로 subgraphs/graph_state 등을 로드하므로 루트가 필요).
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from hypothesis import given, settings, strategies as st  # noqa: E402

from ai_engine.agent_system.deps import GraphDeps  # noqa: E402
from ai_engine.agent_system.depth_router import (  # noqa: E402
    _FAST_PATH_DOMAINS,
    build_fast_path_graph,
    pick_fast_domain,
)

# Fast_Path 에 절대 등장해선 안 되는 메타 노드 + 병렬 fan-out 디스패치 노드 집합.
_FORBIDDEN_NODES = {"planner", "aggregate", "evaluator", "router", "plan_dispatch"}

# LangGraph 컴파일 그래프가 자동 삽입하는 빌트인 노드(도메인 노드 판정에서 제외).
_BUILTIN_NODES = {"__start__", "__end__"}


class _StubGateway:
    """라이브 게이트웨이 없이 GraphDeps 를 채우기 위한 최소 스텁.

    build_fast_path_graph 는 그래프를 *조립*만 하고 실행하지 않으므로 게이트웨이
    메서드는 호출되지 않지만, deps.gateway 참조가 존재하도록 최소 표면만 제공한다.
    네트워크/자격증명/비용 없음.
    """

    async def converse(self, *args, **kwargs):  # pragma: no cover - 조립만 함
        return {}

    async def stream_sse_realtime(self, *args, **kwargs):  # pragma: no cover
        if False:
            yield {}


def _make_deps() -> GraphDeps:
    """최소 GraphDeps 구성(MockGateway 스텁, checkpointer/store 없음)."""
    return GraphDeps(gateway=_StubGateway())


def _domain_node_names(compiled) -> set[str]:
    """컴파일 그래프에서 빌트인을 제외한 노드 이름 집합을 반환한다.

    LangGraph 는 서브그래프를 노드로 얹으면(add_node(name, compiled_subgraph)) 기본
    get_graph() 에서 서브그래프 내부를 펼치지 않으므로, 노드 키는 도메인 라벨 하나 +
    빌트인('__start__'/'__end__')이다.
    """
    node_keys = set(compiled.get_graph().nodes.keys())
    return node_keys - _BUILTIN_NODES


# 도메인 라벨(coding/media/research/ops/chat) — hypothesis 로 샘플링(직접 생성기 미구현).
_DOMAIN_LABELS = st.sampled_from(list(_FAST_PATH_DOMAINS))


@settings(max_examples=100, deadline=None)
@given(domain=_DOMAIN_LABELS)
def test_fast_path_excludes_meta_nodes(domain):
    """Fast_Path 그래프는 정확히 {domain} 노드만 가지며 메타/fan-out 노드를 포함하지 않는다."""
    deps = _make_deps()
    compiled = build_fast_path_graph(deps, domain)
    domain_nodes = _domain_node_names(compiled)

    # (1) planner/aggregate/evaluator + fan-out 디스패치와 교집합 공집합.
    assert domain_nodes.isdisjoint(_FORBIDDEN_NODES), (
        f"Fast_Path({domain}) 가 금지 노드를 포함: "
        f"{domain_nodes & _FORBIDDEN_NODES} (전체 노드={domain_nodes})"
    )

    # (2) 정확히 하나의 도메인 서브그래프 노드만 포함하며 그 이름이 domain 이다.
    assert domain_nodes == {domain}, (
        f"Fast_Path 노드 집합이 정확히 {{{domain}}} 이 아님: {domain_nodes}"
    )


# 알 수 없는 라벨은 보수적으로 'chat' 폴백 도메인으로 조립되어야 한다(비차단).
_BOGUS_LABELS = st.text(max_size=12).filter(lambda s: s not in _FAST_PATH_DOMAINS)


@settings(max_examples=100, deadline=None)
@given(bogus=_BOGUS_LABELS)
def test_fast_path_bogus_label_falls_back_to_chat(bogus):
    """유효하지 않은 도메인 라벨은 'chat' 단일 노드로 폴백 조립된다(메타 노드 없음)."""
    deps = _make_deps()
    compiled = build_fast_path_graph(deps, bogus)
    domain_nodes = _domain_node_names(compiled)

    assert domain_nodes.isdisjoint(_FORBIDDEN_NODES), (
        f"폴백 그래프가 금지 노드를 포함: {domain_nodes & _FORBIDDEN_NODES}"
    )
    assert domain_nodes == {"chat"}, (
        f"폴백 노드 집합이 {{'chat'}} 이 아님: {domain_nodes}"
    )


@settings(max_examples=100, deadline=None)
@given(prompt=st.text(max_size=200))
def test_pick_fast_domain_returns_valid_label(prompt):
    """pick_fast_domain 은 항상 유효 도메인 라벨을 반환하고, 그 라벨로 조립한 그래프도 단일 노드다."""
    deps = _make_deps()
    domain = pick_fast_domain(prompt, deps)
    assert domain in _FAST_PATH_DOMAINS, f"유효하지 않은 도메인 라벨: {domain!r}"

    compiled = build_fast_path_graph(deps, domain)
    domain_nodes = _domain_node_names(compiled)
    assert domain_nodes.isdisjoint(_FORBIDDEN_NODES)
    assert domain_nodes == {domain}
