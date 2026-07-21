# Feature: reasoning-perf-reliability, Property 5: 신규 플래그가 모두 off 면 그래프 구조·경로가 기존과 동등하다 (무회귀)
"""Property-based test: 신규 플래그가 모두 off 일 때 그래프 구조·경로 무회귀 검증.

Feature: reasoning-perf-reliability, Property 5: 신규 플래그가 모두 off 면 그래프 구조·경로가 기존과 동등하다 (무회귀)
**Validates: Requirements 6.2, 6.3, 10.1, 10.3**

For any 프롬프트와 도메인/도구 조합에 대해, `AE_ENABLE_ADAPTIVE_DEPTH` 와
`AE_ENABLE_GROUNDING_GATE` 가 모두 off 이면:
    (a) adaptive_depth_enabled() 는 False 다 — server 는 항상 Full_Graph 를 선택하고
        Fast_Path 는 절대 선택되지 않는다(요구사항 6.3, 10.1).
    (b) build_domain_subgraph 가 만든 컴파일 그래프의 노드·엣지 집합이 게이트 미적용
        기존 구조와 동일하다: `verify → END` 엣지가 존재하고 `verify → model`(게이트 ON
        전용 조건부 엣지)은 존재하지 않는다(요구사항 10.3). 또한 동일 인자로 두 번 조립하면
        구조가 결정론적으로 동일하다(무회귀 안정성).

강화(sanity): AE_ENABLE_GROUNDING_GATE=1 로 조립하면 `verify → model` 조건부 엣지가
등장함을 확인해, flag-off 무회귀가 "게이트 배선이 실제로 다르다"는 사실 위에서 성립함을
실증한다(env 는 테스트 종료 시 복원).

대상 코드(실측):
- ai_engine/agent_system/subgraphs/_common.py build_domain_subgraph:
    조립 시점 AE_ENABLE_GROUNDING_GATE 1회 판독 —
      off → sg.add_edge("verify", END)                      (verify → __end__ 무조건)
      on  → add_conditional_edges("verify", ..., {"model":"model","done":END})
            (verify → model / verify → __end__ 조건부)
- ai_engine/agent_system/depth_router.py adaptive_depth_enabled():
    AE_ENABLE_ADAPTIVE_DEPTH(기본 off) 판독.
- 엣지 introspection: LangGraph CompiledStateGraph.get_graph().edges 의 각 Edge 는
  .source/.target/.conditional 속성을 가진다(실측 확인).

deps 는 라이브 게이트웨이 없이 최소 GraphDeps + 스텁 게이트웨이로 구성한다
(네트워크/자격증명/비용 없음 — 그래프는 조립만 하고 실행하지 않는다).

실행: ai_engine/.venv/bin/python -m pytest scripts/test_no_regression_structure_pbt.py -q
Stack: Python 3.11+, hypothesis library.
"""
from __future__ import annotations

import os
import sys

# repo 루트를 import 경로에 추가해 ai_engine 패키지를 로드한다
# (build_domain_subgraph 가 grounding_gate/graph_state 등을 import 하므로 루트가 필요).
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from hypothesis import given, settings, strategies as st  # noqa: E402

from ai_engine.agent_system.deps import GraphDeps  # noqa: E402
from ai_engine.agent_system.depth_router import adaptive_depth_enabled  # noqa: E402
from ai_engine.agent_system.subgraphs._common import build_domain_subgraph  # noqa: E402

_ADAPTIVE_FLAG = "AE_ENABLE_ADAPTIVE_DEPTH"
_GATE_FLAG = "AE_ENABLE_GROUNDING_GATE"

# LangGraph 컴파일 그래프가 자동 삽입하는 빌트인 노드(구조 비교에서 END 는 __end__ 로 나타남).
_END = "__end__"

# build_domain_subgraph 가 재사용하는 도메인 라벨(retrieve 실노드 스킵 판정용).
_DOMAINS = ("coding", "media", "research", "ops", "chat")

# has_tools 변형을 위한 최소 Bedrock toolSpec dict(도구 노드 배선을 활성화하되 verify 배선에는
# 영향 없음 — 게이트 off 무회귀 검증 대상은 verify→END 엣지다).
_STUB_TOOL = {
    "name": "noop_tool",
    "description": "테스트용 no-op 도구(조립만; 실행되지 않음).",
    "inputSchema": {"json": {"type": "object", "properties": {}}},
}


class _StubGateway:
    """라이브 게이트웨이 없이 GraphDeps 를 채우기 위한 최소 스텁(조립 전용).

    build_domain_subgraph 는 그래프를 *조립*만 하고 실행하지 않으므로 게이트웨이
    메서드는 호출되지 않지만, deps.gateway 참조가 존재하도록 최소 표면만 제공한다.
    네트워크/자격증명/비용 없음(scripts/test_fast_path_nodes_pbt.py 패턴 미러).
    """

    async def converse(self, *args, **kwargs):  # pragma: no cover - 조립만 함
        return {}

    async def stream_sse_realtime(self, *args, **kwargs):  # pragma: no cover
        if False:
            yield {}


def _make_deps() -> GraphDeps:
    """최소 GraphDeps 구성(스텁 게이트웨이, checkpointer/store 없음)."""
    return GraphDeps(gateway=_StubGateway())


def _edge_pairs(compiled) -> set:
    """컴파일 그래프의 (source, target) 엣지 집합을 반환한다."""
    return {
        (getattr(e, "source", None), getattr(e, "target", None))
        for e in compiled.get_graph().edges
    }


def _verify_targets(compiled) -> set:
    """verify 노드에서 나가는 엣지의 target 집합을 반환한다."""
    return {t for (s, t) in _edge_pairs(compiled) if s == "verify"}


def _clear_new_flags() -> None:
    """두 신규 플래그를 환경에서 제거(= 기본 off 상태)한다."""
    os.environ.pop(_ADAPTIVE_FLAG, None)
    os.environ.pop(_GATE_FLAG, None)


# 도메인 라벨 + has_tools 여부를 hypothesis 로 샘플링(직접 생성기 미구현).
_DOMAIN_LABELS = st.sampled_from(list(_DOMAINS))


@settings(max_examples=100, deadline=None)
@given(
    domain=_DOMAIN_LABELS,
    with_tools=st.booleans(),
    with_retrieve=st.booleans(),
)
def test_flags_off_structure_no_regression(domain, with_tools, with_retrieve):
    """두 플래그 off → adaptive off + verify→END(게이트 미적용 기존 구조) + 결정론적 안정."""
    _clear_new_flags()

    # (a) 적응형 깊이 비활성 → server 는 항상 Full_Graph 선택(Fast_Path 미선택).
    assert adaptive_depth_enabled() is False, (
        f"{_ADAPTIVE_FLAG} 미설정인데 adaptive_depth_enabled() 가 True"
    )

    tools = [_STUB_TOOL] if with_tools else None
    compiled = build_domain_subgraph(
        _make_deps(),
        tools=tools,
        model_id="m",
        with_retrieve=with_retrieve,
        domain=domain,
    )
    verify_targets = _verify_targets(compiled)

    # (b) 게이트 off 무회귀: verify → END 엣지가 존재하고 verify → model 조건부 엣지는 없다.
    assert _END in verify_targets, (
        f"게이트 off 인데 verify→END 엣지가 없음: verify_targets={verify_targets}"
    )
    assert "model" not in verify_targets, (
        f"게이트 off 인데 게이트 ON 전용 verify→model 엣지가 존재: "
        f"verify_targets={verify_targets}"
    )
    # verify 는 정확히 END 하나로만 나간다(기존 구조와 동일).
    assert verify_targets == {_END}, (
        f"게이트 off verify 엣지 집합이 {{'{_END}'}} 이 아님: {verify_targets}"
    )

    # 동일 인자로 재조립 → 노드·엣지 집합 결정론적으로 동일(무회귀 안정성).
    compiled2 = build_domain_subgraph(
        _make_deps(),
        tools=tools,
        model_id="m",
        with_retrieve=with_retrieve,
        domain=domain,
    )
    g1, g2 = compiled.get_graph(), compiled2.get_graph()
    assert set(g1.nodes.keys()) == set(g2.nodes.keys()), "재조립 시 노드 집합이 달라짐"
    assert _edge_pairs(compiled) == _edge_pairs(compiled2), "재조립 시 엣지 집합이 달라짐"


@settings(max_examples=100, deadline=None)
@given(domain=_DOMAIN_LABELS, with_tools=st.booleans())
def test_gate_on_adds_verify_to_model_edge(domain, with_tools):
    """강화(sanity): 게이트 ON 이면 verify→model 조건부 엣지가 등장한다(배선이 실제로 다름).

    이로써 flag-off 무회귀(verify→model 부재)가 "게이트가 실제 구조를 바꾼다"는 대비 위에서
    성립함을 실증한다. env 는 종료 시 복원한다.
    """
    _saved_gate = os.environ.get(_GATE_FLAG)
    _saved_adaptive = os.environ.get(_ADAPTIVE_FLAG)
    try:
        os.environ.pop(_ADAPTIVE_FLAG, None)  # adaptive 는 여전히 off(그래프 구조와 무관)
        os.environ[_GATE_FLAG] = "1"

        tools = [_STUB_TOOL] if with_tools else None
        compiled = build_domain_subgraph(
            _make_deps(), tools=tools, model_id="m", domain=domain
        )
        verify_targets = _verify_targets(compiled)

        assert "model" in verify_targets, (
            f"게이트 ON 인데 verify→model 재생성 엣지가 없음: {verify_targets}"
        )
        assert _END in verify_targets, (
            f"게이트 ON 인데 verify→END(done) 엣지가 없음: {verify_targets}"
        )
        assert verify_targets == {"model", _END}, (
            f"게이트 ON verify 엣지 집합이 {{'model','{_END}'}} 이 아님: {verify_targets}"
        )
    finally:
        # env 복원(다른 테스트/예제에 누수 방지).
        if _saved_gate is None:
            os.environ.pop(_GATE_FLAG, None)
        else:
            os.environ[_GATE_FLAG] = _saved_gate
        if _saved_adaptive is None:
            os.environ.pop(_ADAPTIVE_FLAG, None)
        else:
            os.environ[_ADAPTIVE_FLAG] = _saved_adaptive
