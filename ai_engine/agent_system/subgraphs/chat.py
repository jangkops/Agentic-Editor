"""chat 서브그래프 — 도구가 불필요한 일반 대화 도메인(경량).

Task 3.3 산출물. `_common.build_domain_subgraph` 를 재사용하되 **도구 없이**(tools=None)
그리고 **retrieve 스킵**(with_retrieve=False)으로 조립한다. design 서브그래프 분할 기준에서
chat 은 RAG 가 불필요하며 도구 집합도 없다.

구성:
    START → model → verify → END

- 도구가 없으므로 tools 노드/conditional edge 가 없고, model 은 tool_calls 를 낼 일이 없다.
- retrieve 를 스킵하므로 근거 조회 비용 없이 곧바로 model 을 호출한다.
- model_id 는 deps.model_coding(sonnet-4-5 기본). design 표의 "사용자 선택 모델 그대로" 는
  Phase 2+ 에서 상위 그래프가 model_id 를 주입하도록 확장할 여지가 있으나, 현재 deps 는
  단일 model_coding 만 보유하므로 이를 사용한다.
"""

from __future__ import annotations

from typing import Any

from ai_engine.agent_system.subgraphs._common import build_domain_subgraph


def build_chat_subgraph(deps: Any):
    """chat 서브그래프를 조립해 compiled Runnable 을 반환.

    도구 없음(tools=None) + retrieve 스킵(with_retrieve=False) → START→model→verify→END.
    도구가 없으므로 유한 종료가 자명하다(model 1회 호출 후 verify→END).

    Postcondition: sg.compile() 결과(CompiledStateGraph)를 반환한다. checkpointer 는
                   주입하지 않는다(부모 그래프가 주입).
    """
    return build_domain_subgraph(
        deps,
        tools=None,
        model_id=deps.model_coding,
        with_retrieve=False,
    )
