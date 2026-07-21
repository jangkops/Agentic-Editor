# Feature: reasoning-perf-reliability, Property 4: Fast_Path 실행은 유한하며 model 왕복이 최소다
"""Property-based test: Fast_Path 실행의 유한성과 최소 model 왕복.

Feature: reasoning-perf-reliability, Property 4: Fast_Path 실행은 유한하며 model 왕복이 최소다
**Validates: Requirements 5.3, 11.4**

For any simple query 에 대해:
    - 도구 호출이 없으면(chat 도메인) Fast_Path 의 model 노드는 **정확히 1회** 호출된다.
    - model↔tool 왕복이 있어도 SUBGRAPH_RECURSION_LIMIT 이내로 유한 종료한다.
      (이 테스트는 도구 없는 chat 도메인으로 최소 왕복=1 과 유한 종료를 함께 실증한다.)

대상 코드(실측):
- ai_engine/agent_system/depth_router.build_fast_path_graph(deps, domain):
    domain='chat' 이면 START → chat(단일 도메인 서브그래프) → END.
    chat 서브그래프는 도구 없음(with_retrieve=True) → retrieve(RAG 스킵) → model → verify → END.
- ai_engine/agent_system/subgraphs/_common.build_domain_subgraph:
    · make_model_node 는 tools 가 없으면 순수 GatewayChatModel.ainvoke → gateway.converse 1회.
    · SUBGRAPH_RECURSION_LIMIT(기본 25) 이내 유한 종료(tools_condition_or_verify).

계측 방식:
- scripts/eval_reasoning_perf.MockGateway 를 상속한 CountingMockGateway 로 converse /
  converse_stream_live 호출 횟수를 센다(네트워크·비용·자격증명 없음 — 요구사항 11.1).
- chat 도메인은 도구가 없고 prefer_streaming=False 이므로 model 노드가 converse 를 정확히
  1회 호출한다. GraphRecursionError 없이 compiled.ainvoke 가 최종 상태를 반환하면 유한 종료.

실행: ai_engine/.venv/bin/python -m pytest scripts/test_fast_path_finite_pbt.py -q
Stack: Python 3.11+, hypothesis library.
"""
from __future__ import annotations

import asyncio
import os
import sys

# repo 루트 + scripts 를 import 경로에 추가한다(test_grounding_below_pbt.py 패턴 미러).
# repo 루트: ai_engine 패키지 로드용. scripts: eval_reasoning_perf.MockGateway 로드용.
_HERE = os.path.dirname(__file__)
sys.path.insert(0, os.path.join(_HERE, ".."))
sys.path.insert(0, _HERE)

# 결정론·무회귀: 신규 게이트 플래그와 answer_quality 를 명시적으로 off 로 고정한다.
# (grounding gate on 이면 verify→model refine 루프가 생겨 model 왕복이 달라질 수 있고,
#  answer_quality on 이면 verify 가 gateway 를 추가 호출할 수 있어 카운트가 오염된다.)
os.environ["AE_ENABLE_GROUNDING_GATE"] = "0"
os.environ.pop("AE_ANSWER_QUALITY", None)

from hypothesis import given, settings, strategies as st  # noqa: E402
from langchain_core.messages import HumanMessage  # noqa: E402

from eval_reasoning_perf import MockGateway  # noqa: E402
from ai_engine.agent_system.deps import GraphDeps  # noqa: E402
from ai_engine.agent_system.depth_router import build_fast_path_graph  # noqa: E402
from ai_engine.agent_system.subgraphs._common import (  # noqa: E402
    SUBGRAPH_RECURSION_LIMIT,
)


class CountingMockGateway(MockGateway):
    """결정론적 MockGateway 에 model(=LLM) 호출 카운터를 얹은 스텁.

    make_model_node 는 tools 가 없으면 GatewayChatModel(prefer_streaming=False).ainvoke →
    _agenerate → _invoke_gateway → gateway.converse 를 호출한다. 따라서 converse 및
    converse_stream_live 호출 총합이 곧 model 노드 왕복 횟수다.
    """

    def __init__(self, *args, **kwargs) -> None:
        super().__init__(*args, **kwargs)
        self.model_calls = 0

    async def converse(self, model_id, messages, system_prompt="", tool_config=None):
        self.model_calls += 1
        return await super().converse(
            model_id, messages, system_prompt=system_prompt, tool_config=tool_config
        )

    async def converse_stream_live(
        self, model_id, messages, system_prompt="", tool_config=None
    ):
        self.model_calls += 1
        return await super().converse_stream_live(
            model_id, messages, system_prompt=system_prompt, tool_config=tool_config
        )


def _run_chat_fast_path(prompt: str) -> tuple[int, dict]:
    """chat 도메인 Fast_Path 를 1회 실행하고 (model 호출 수, 최종 상태) 를 반환한다.

    deps.checkpointer 는 None(미주입)이라 서브그래프는 별도 thread 상태를 요구하지 않지만,
    계약 일관성을 위해 recursion_limit 이 있는 config 를 전달한다.
    """
    gw = CountingMockGateway()
    deps = GraphDeps(gateway=gw)
    compiled = build_fast_path_graph(deps, "chat")

    initial_state = {
        "prompt": prompt,
        "session_id": "fast-path-finite",
        "project_path": "",
        "open_file": "",
        "open_file_content": "",
        "aws_profile": "",
        "bedrock_user": "",
        "template_id": "",
        "system_prompt": "",
        "messages": [HumanMessage(content=prompt)],
        "visited_routes": [],
    }
    # recursion_limit 은 SUBGRAPH_RECURSION_LIMIT 여유분보다 크게 둬 유한 종료를 실측한다.
    config = {"recursion_limit": SUBGRAPH_RECURSION_LIMIT + 10}

    # 개별 ainvoke await 하나만 실행(스트림 루프 아님 — 요구사항 11.3 준수).
    state = asyncio.run(compiled.ainvoke(initial_state, config))
    return gw.model_calls, (state or {})


# 프롬프트 생성기: 빈 문자열·공백·한/영 혼합·유니코드 등 다양한 simple 질의를 포섭한다.
_PROMPTS = st.text(max_size=200)


@settings(max_examples=100, deadline=None)
@given(prompt=_PROMPTS)
def test_fast_path_chat_finite_single_model_call(prompt):
    """chat Fast_Path 는 model 을 정확히 1회 호출하고 GraphRecursionError 없이 유한 종료한다."""
    # ainvoke 가 예외 없이 반환하면 유한 종료(GraphRecursionError 미발생 — 요구사항 11.4).
    model_calls, state = _run_chat_fast_path(prompt)

    # (1) 유한 종료: 최종 상태(dict)가 반환되어야 한다.
    assert isinstance(state, dict), f"Fast_Path 가 최종 상태를 반환하지 않음: {state!r}"

    # (2) 최소 model 왕복: 도구 없는 chat 도메인은 model 노드를 정확히 1회만 호출한다
    #     (요구사항 5.3 — 도구 루프 제외 model LLM 왕복 1회).
    assert model_calls == 1, (
        f"chat Fast_Path model 왕복 수 불일치: expected=1, actual={model_calls} "
        f"(prompt={prompt!r})"
    )

    # (3) verify 단계까지 완주해 final_text 채널이 존재한다(유한 종료 부수 실증).
    assert "final_text" in state, (
        f"Fast_Path 가 verify 를 완주하지 않음(final_text 부재): keys={sorted(state.keys())!r}"
    )
