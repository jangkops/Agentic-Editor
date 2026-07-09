"""서브그래프 공통 빌더 — `retrieve → model → (tools | verify)` 조립 프리미티브.

Task 3.3 산출물. coding.py(task 1.12)에서 검증된 공통 패턴을 추출해 media / research /
ops / chat 서브그래프가 **도구 집합만 다르게** 재사용하도록 한다(중복 최소화, 요구사항 1.6).

design.md 섹션 4(서브그래프 공통 패턴) + API_NOTES.md(항목 5·6, CRITICAL 2)를 근거로 한다.

핵심 사항(coding.py 주석과 동일 — 값이 여기서 단일 정의된다):
- **model 노드 타임아웃 (요구사항 6.1 / API_NOTES CRITICAL 2):** `llm.ainvoke(...)` 라는
  **개별 await 하나만** `asyncio.wait_for(..., MODEL_NODE_TIMEOUT)` 로 감싼다. 스트림 소비
  루프(async for)를 통째로 감싸면 Python 3.14 에서 취소 시 hang 이 발생하므로 절대 금지.
- **iteration cap (요구사항 6.4 / Property 4):** `tools_condition_or_verify` 는 마지막
  메시지에 tool_calls 가 있어도 `iteration >= SUBGRAPH_RECURSION_LIMIT` 이면 강제로 verify
  로 라우팅하여 model↔tool 무한 루프를 차단한다(과거 hang 이력 대응).
- **checkpointer (API_NOTES 항목 6):** `build_domain_subgraph` 는 `sg.compile()` 만 호출
  한다. 서브그래프는 부모(build_top_graph)가 주입하는 checkpointer 를 상속하므로 여기서
  checkpointer 를 넘기지 않는다.
- **Phase 1 스텁:** retrieve / verify 는 최소 구현이다. retrieve 는 `{}`(evidence 미적재),
  verify 는 마지막 AIMessage 텍스트를 final_text 로 확정한다. Phase 3(task 5.1/5.2)에서
  실제 RAG / citation / 강제 생성 폴백 노드로 교체된다.

기존 자산 재사용(재구현 금지 — 요구사항 7.5):
- GraphState (`agent_system/graph_state.py`)
- GatewayChatModel (`agent_system/chat_model_adapter.py`)
- GatewayToolNode (`agent_system/nodes/tool_node.py`)
"""

from __future__ import annotations

import asyncio
import os
from typing import Any, List, Optional

from langchain_core.messages import AIMessage, BaseMessage, SystemMessage
from langgraph.graph import END, START, StateGraph

from ai_engine.agent_system.chat_model_adapter import GatewayChatModel
from ai_engine.agent_system.graph_state import GraphState
from ai_engine.agent_system.nodes.tool_node import GatewayToolNode


# ─────────────────────────────────────────────────────────────────────────────
# 타임아웃 / recursion 상수 (env override — 요구사항 6.1 / 6.4)
# ─────────────────────────────────────────────────────────────────────────────
def _env_float(name: str, default: float) -> float:
    try:
        return float(os.environ.get(name, str(default)))
    except (TypeError, ValueError):
        return default


def _env_int(name: str, default: int) -> int:
    try:
        return int(os.environ.get(name, str(default)))
    except (TypeError, ValueError):
        return default


# model 노드 개별 ainvoke 상한(초). 기본 300 — gateway converse read timeout 과 정합.
MODEL_NODE_TIMEOUT: float = _env_float("AE_MODEL_NODE_TIMEOUT", 300.0)
# 서브그래프 model↔tool 왕복 상한. 기본 25 — 무한 도구 루프 차단.
SUBGRAPH_RECURSION_LIMIT: int = _env_int("AE_SUBGRAPH_RECURSION", 25)


# ─────────────────────────────────────────────────────────────────────────────
# 메시지 구성 헬퍼
# ─────────────────────────────────────────────────────────────────────────────
def _compose_messages(state: GraphState) -> List[BaseMessage]:
    """system_prompt + evidence + 대화 messages 를 조합해 LLM 입력 메시지를 만든다.

    - state["system_prompt"] 와 evidence.context 가 있으면 SystemMessage 로 선두에 둔다.
    - messages 에 이미 SystemMessage 가 있으면 중복 주입하지 않는다(retrieve 노드가 이미
      system_prompt 를 갱신했을 수 있음).
    """
    messages: List[BaseMessage] = list(state.get("messages") or [])

    system_parts: List[str] = []
    sys_prompt = state.get("system_prompt")
    if isinstance(sys_prompt, str) and sys_prompt.strip():
        system_parts.append(sys_prompt.strip())

    evidence = state.get("evidence")
    if isinstance(evidence, dict):
        ctx = evidence.get("context")
        if isinstance(ctx, str) and ctx.strip():
            system_parts.append("[근거]\n" + ctx.strip())

    has_system = any(isinstance(m, SystemMessage) for m in messages)
    if system_parts and not has_system:
        combined = "\n\n".join(system_parts)
        return [SystemMessage(content=combined), *messages]
    return messages


# ─────────────────────────────────────────────────────────────────────────────
# 노드 팩토리
# ─────────────────────────────────────────────────────────────────────────────
def make_model_node(deps: Any, tools: Optional[List[dict]], model_id: str):
    """model 노드 팩토리 — GatewayChatModel 을 1턴 호출.

    Precondition:  state["messages"] 는 비어있지 않다(retrieve 가 시스템/근거를 주입).
    Postcondition: 정상 시 {"messages":[AIMessage], "iteration": iteration+1} 반환.
                   타임아웃 시 {"messages":[AIMessage("[모델 응답 시간 초과]")],
                   "error":"model_timeout"} 반환(→ tool_calls 없으므로 verify 로 진행).
    Invariant:     ainvoke 라는 개별 await 하나만 asyncio.wait_for(MODEL_NODE_TIMEOUT)로
                   감싼다(API_NOTES CRITICAL 2 — 스트림 루프는 감싸지 않음).

    tools 가 비어있거나 None 이면 도구를 바인딩하지 않는다(chat 서브그래프 — 도구 없음).
    빈 도구 리스트를 bind_tools 로 넘기면 Bedrock toolConfig 가 빈 tools 로 전달되어 거부될
    수 있으므로, 도구가 없을 때는 순수 model 로 호출한다.
    """
    base = GatewayChatModel(gateway=deps.gateway, model_id=model_id)
    llm = base.bind_tools(tools) if tools else base

    async def model_node(state: GraphState) -> dict:
        msgs = _compose_messages(state)
        try:
            ai = await asyncio.wait_for(llm.ainvoke(msgs), timeout=MODEL_NODE_TIMEOUT)
        except asyncio.TimeoutError:
            return {
                "messages": [AIMessage(content="[모델 응답 시간 초과]")],
                "error": "model_timeout",
            }
        return {"messages": [ai], "iteration": state.get("iteration", 0) + 1}

    return model_node


def tools_condition_or_verify(state: GraphState) -> str:
    """conditional edge 함수 — 다음 노드 라벨을 반환.

    마지막 메시지에 tool_calls 가 있고 iteration < SUBGRAPH_RECURSION_LIMIT 이면 "tools",
    아니면 "verify"(요구사항 6.4 / Property 4 — 무한 도구 루프 차단).
    """
    messages = state.get("messages") or []
    if not messages:
        return "verify"
    last = messages[-1]
    tool_calls = getattr(last, "tool_calls", None)
    if tool_calls and state.get("iteration", 0) < SUBGRAPH_RECURSION_LIMIT:
        return "tools"
    return "verify"


def make_retrieve_node(deps: Any):
    """retrieve 노드 팩토리 (Phase 1 스텁).

    Phase 1 에서는 RAG 를 수행하지 않고 evidence 를 적재하지 않는다({} 반환 → 상태 무변경).
    Phase 3(task 5.1)에서 context_builder / indexer / embedder 를 재사용하는 실제 노드로
    교체된다.
    """

    async def retrieve_node(state: GraphState) -> dict:
        return {}

    return retrieve_node


def _last_ai_text(messages: List[BaseMessage]) -> str:
    """마지막 AIMessage 의 텍스트를 추출(없으면 빈 문자열)."""
    for msg in reversed(messages or []):
        if isinstance(msg, AIMessage):
            content = msg.content
            if isinstance(content, str):
                return content
            if isinstance(content, list):
                parts = [
                    p.get("text", "") if isinstance(p, dict) else str(p)
                    for p in content
                ]
                return "".join(parts)
    return ""


def make_verify_node(deps: Any):
    """verify 노드 팩토리 (Phase 1 스텁).

    Phase 1 에서는 마지막 AIMessage 텍스트를 final_text 로 확정하는 최소 구현이다
    (요구사항 3.3). citation 검증 / answer_quality / 강제 생성 폴백은 Phase 3(task 5.2)에서
    추가된다.
    """

    async def verify_node(state: GraphState) -> dict:
        final = _last_ai_text(state.get("messages") or [])
        return {"final_text": final}

    return verify_node


# ─────────────────────────────────────────────────────────────────────────────
# 공통 서브그래프 빌더
# ─────────────────────────────────────────────────────────────────────────────
def build_domain_subgraph(
    deps: Any,
    tools: Optional[List[dict]],
    model_id: str,
    *,
    with_retrieve: bool = True,
):
    """도메인 서브그래프를 조립해 compiled Runnable 을 반환.

    coding / media / research / ops / chat 이 이 빌더를 재사용하며, **도구 집합만 다르게**
    바인딩한다(요구사항 1.6). 구성:

      - tools 가 있으면 (ReAct 루프):
          START → [retrieve →] model
          conditional(model, tools_condition_or_verify) → {tools, verify}
          tools → model
          verify → END
      - tools 가 없으면 (chat — 도구 불필요):
          START → [retrieve →] model → verify → END

    Args:
        deps:          GraphDeps (gateway / model_coding / checkpointer).
        tools:         Bedrock toolSpec dict 리스트. 빈 값이면 도구 노드 없이 model→verify.
        model_id:      model 노드가 사용할 Bedrock model_id.
        with_retrieve: False 면 retrieve 노드를 두지 않고 START→model 로 진입(chat 은 RAG
                       불필요 — design 서브그래프 분할 기준).

    Postcondition: sg.compile() 결과(CompiledStateGraph)를 반환한다. checkpointer 는
                   주입하지 않는다(부모 그래프가 주입 — API_NOTES 항목 6).
    """
    sg = StateGraph(GraphState)

    has_tools = bool(tools)

    if with_retrieve:
        sg.add_node("retrieve", make_retrieve_node(deps))
    sg.add_node("model", make_model_node(deps, tools=tools, model_id=model_id))
    if has_tools:
        sg.add_node("tools", GatewayToolNode(tools, deps=deps))
    sg.add_node("verify", make_verify_node(deps))

    if with_retrieve:
        sg.add_edge(START, "retrieve")
        sg.add_edge("retrieve", "model")
    else:
        sg.add_edge(START, "model")

    if has_tools:
        sg.add_conditional_edges(
            "model",
            tools_condition_or_verify,
            {"tools": "tools", "verify": "verify"},
        )
        sg.add_edge("tools", "model")
    else:
        # 도구가 없으면 model 은 tool_calls 를 낼 일이 없으므로 바로 verify 로.
        sg.add_edge("model", "verify")

    sg.add_edge("verify", END)

    return sg.compile()
