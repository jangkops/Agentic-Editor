"""Top Supervisor 라우터 노드 — 사용자 의도를 도메인 Route 로 분류.

Task 3.1 산출물. design.md 섹션 3(Top Supervisor + StateGraph 조립) + API_NOTES.md
(항목 1·5) + 요구사항 1.2 / 6.5 를 근거로 한다.

핵심 사항:
- **팩토리 패턴 (`make_top_router_node(deps)`):** design 은 라우터가 GatewayChatModel
  을 통해 LLM 분류를 수행해야 하므로 `deps`(GraphDeps: gateway / model_coding) 주입이
  필요하다. `build_top_graph` 에서 `g.add_node("router", make_top_router_node(deps))`
  형태로 배선하기 쉽도록 노드 콜러블을 반환하는 팩토리로 구현한다.
- **hop cap (요구사항 6.5 / Property 4):** `visited_routes` 길이가 `MAX_ROUTE_HOPS`
  (기본 4, `AE_MAX_ROUTE_HOPS`)에 도달하면 LLM 호출 없이 즉시 `{"route": "done"}` 를
  반환하여 재라우팅 순환을 종료한다(무한 순환 차단 — 과거 hang 이력 대응).
- **분류 안정화:** GatewayChatModel(sonnet-4-5)에 단일 라벨을 강제로 얻기 위해
  toolChoice(강제 스키마) 를 우선 시도하고, tool_calls 가 없으면 응답 텍스트에서 라벨을
  파싱한다. 어느 쪽도 유효 라벨을 못 얻으면 휴리스틱 폴백(`_is_code_related` /
  `_infer_file_intent_from_prompt`) 또는 기본 `"chat"` 으로 폴백한다.
- **타임아웃 (요구사항 6.x / API_NOTES CRITICAL 2):** LLM 개별 await 하나만
  `asyncio.wait_for(..., ROUTER_TIMEOUT)` 로 감싼다(스트림 아님 — ainvoke). 분류 실패는
  **비차단**이며 폴백 라벨로 진행한다(요구사항 2.2 유지: LLM 호출은 Gateway 경유만).

기존 자산 재사용(재구현 금지 — 요구사항 7.5):
- GatewayChatModel (`agent_system/chat_model_adapter.py`)
- GraphState / RouteName (`agent_system/graph_state.py`)
- GraphDeps (`agent_system/deps.py`)
- server.py `_is_code_related` / `_infer_file_intent_from_prompt` (휴리스틱 폴백)
"""

from __future__ import annotations

import asyncio
import os
from typing import Any, List, Optional

from langchain_core.messages import HumanMessage, SystemMessage
from langgraph.graph import END, START, StateGraph

from ai_engine.agent_system.chat_model_adapter import (
    GatewayChatModel,
    GatewayModelError,
)
from ai_engine.agent_system.graph_state import GraphState
from ai_engine.agent_system.subgraphs import (
    build_chat_subgraph,
    build_coding_subgraph,
    build_media_subgraph,
    build_ops_subgraph,
    build_research_subgraph,
)

# 라우터 분류가 선택 가능한 도메인 라벨(종료 라벨 done 은 hop cap 도달 시에만 부여).
_ROUTE_LABELS = ("coding", "media", "research", "ops", "chat")

# design 서브그래프 분할에 사용하는 라우터 기본 모델(sonnet-4-5).
_DEFAULT_ROUTER_MODEL = "us.anthropic.claude-sonnet-4-5-20250929-v1:0"


def _env_int(name: str, default: int) -> int:
    try:
        return int(os.environ.get(name, str(default)))
    except (TypeError, ValueError):
        return default


def _env_float(name: str, default: float) -> float:
    try:
        return float(os.environ.get(name, str(default)))
    except (TypeError, ValueError):
        return default


# 재라우팅 hop 상한(요구사항 6.5). visited_routes 길이가 이 값에 도달하면 route="done".
MAX_ROUTE_HOPS: int = _env_int("AE_MAX_ROUTE_HOPS", 4)
# 라우터 LLM 개별 호출 상한(초). 스트림 아님(ainvoke). 초과/실패는 비차단 폴백.
ROUTER_TIMEOUT: float = _env_float("AE_ROUTER_TIMEOUT", 60.0)


# ─────────────────────────────────────────────────────────────────────────────
# 라우터 분류용 도구 스키마 (toolChoice 강제 — 단일 라벨 안정 확보)
# ─────────────────────────────────────────────────────────────────────────────
_ROUTE_TOOL: dict = {
    "name": "select_route",
    "description": (
        "사용자 요청을 처리할 도메인 서브그래프를 하나 선택한다. "
        "coding: 코드 이해/수정/리팩터/디버그·파일 검색·명령. "
        "media: pptx/pdf/이미지/docx/xlsx/슬라이드/다이어그램 생성. "
        "research: 웹 검색/문서 조사/요약. "
        "ops: 셸 명령/git/원격 SSH 운영 작업. "
        "chat: 도구가 필요 없는 일반 대화."
    ),
    "inputSchema": {
        "json": {
            "type": "object",
            "properties": {
                "route": {
                    "type": "string",
                    "enum": list(_ROUTE_LABELS),
                    "description": "선택한 도메인 라벨",
                }
            },
            "required": ["route"],
        }
    },
}

_ROUTER_SYSTEM_PROMPT = (
    "너는 요청 라우터다. 사용자의 요청과 컨텍스트(열린 파일 등)를 보고 "
    "가장 적합한 도메인 하나를 골라 select_route 도구를 호출한다. "
    "가능한 라벨: coding, media, research, ops, chat. "
    "도구를 사용할 수 없으면 라벨 단어 하나만 출력한다."
)


# ─────────────────────────────────────────────────────────────────────────────
# 분류 컨텍스트 구성
# ─────────────────────────────────────────────────────────────────────────────
def _build_router_prompt(state: GraphState) -> str:
    """프롬프트 + 간단한 컨텍스트(open_file 등)를 라우터 입력 텍스트로 구성한다."""
    parts: List[str] = []
    prompt = state.get("prompt") or ""
    parts.append(f"[요청]\n{prompt}")

    open_file = state.get("open_file")
    if isinstance(open_file, str) and open_file.strip():
        parts.append(f"[열린 파일]\n{open_file.strip()}")

    template_id = state.get("template_id")
    if isinstance(template_id, str) and template_id.strip():
        parts.append(f"[템플릿]\n{template_id.strip()}")

    return "\n\n".join(parts)


def _extract_label_from_text(text: Any) -> Optional[str]:
    """응답 텍스트에서 유효한 route 라벨 하나를 파싱한다(없으면 None)."""
    if not isinstance(text, str):
        # AIMessage.content 가 list(멀티모달)인 경우 텍스트만 이어붙임
        if isinstance(text, list):
            joined = "".join(
                p.get("text", "") if isinstance(p, dict) else str(p) for p in text
            )
            text = joined
        else:
            return None
    low = text.lower()
    # 라벨 단어가 등장하면 첫 등장 라벨을 채택.
    best: Optional[str] = None
    best_pos = len(low) + 1
    for label in _ROUTE_LABELS:
        pos = low.find(label)
        if pos != -1 and pos < best_pos:
            best = label
            best_pos = pos
    return best


def _heuristic_route(state: GraphState) -> str:
    """LLM 분류 실패 시 휴리스틱 폴백 라벨(비차단).

    server.py 의 `_infer_file_intent_from_prompt`(파일 생성 의도 → media) 및
    `_is_code_related`(코드 관련 → coding)를 재사용한다. 어느 것도 아니면 chat.
    """
    prompt = state.get("prompt") or ""

    # 1) 파일/미디어 생성 의도 → media
    try:
        from ai_engine.server import _infer_file_intent_from_prompt

        _pt, wanted, _ = _infer_file_intent_from_prompt(prompt, "", "")
        if wanted:
            return "media"
    except Exception:
        pass

    # 2) 코드 관련 → coding
    try:
        from ai_engine.server import _is_code_related

        if _is_code_related(prompt):
            return "coding"
    except Exception:
        pass

    # 3) 기본 대화
    return "chat"


# ─────────────────────────────────────────────────────────────────────────────
# 라우터 LLM 분류
# ─────────────────────────────────────────────────────────────────────────────
async def _classify_route(state: GraphState, deps: Any) -> str:
    """GatewayChatModel(sonnet-4-5)로 route 를 분류해 단일 라벨을 반환한다.

    Precondition:  deps.gateway 는 converse 를 제공한다. state["prompt"] 존재.
    Postcondition: _ROUTE_LABELS 중 하나를 반환. LLM 실패/애매/타임아웃이면 휴리스틱
                   폴백(_heuristic_route) 라벨을 반환(비차단).
    Invariant:     LLM 호출은 GatewayChatModel(gateway 경유)만 사용. 개별 await 하나만
                   asyncio.wait_for(ROUTER_TIMEOUT)로 감싼다(스트림 아님).
    """
    gateway = getattr(deps, "gateway", None)
    if gateway is None:
        return _heuristic_route(state)

    model_id = getattr(deps, "model_coding", None) or _DEFAULT_ROUTER_MODEL

    try:
        llm = GatewayChatModel(gateway=gateway, model_id=model_id).bind_tools(
            [_ROUTE_TOOL], tool_choice="select_route"
        )
        messages = [
            SystemMessage(content=_ROUTER_SYSTEM_PROMPT),
            HumanMessage(content=_build_router_prompt(state)),
        ]
        ai = await asyncio.wait_for(llm.ainvoke(messages), timeout=ROUTER_TIMEOUT)
    except (asyncio.TimeoutError, GatewayModelError, Exception):
        # LLM 실패는 비차단 — 휴리스틱 폴백.
        return _heuristic_route(state)

    # 1) toolChoice 강제 스키마 응답 우선
    tool_calls = getattr(ai, "tool_calls", None) or []
    for tc in tool_calls:
        args = tc.get("args") if isinstance(tc, dict) else None
        if isinstance(args, dict):
            label = args.get("route")
            if isinstance(label, str) and label.lower() in _ROUTE_LABELS:
                return label.lower()

    # 2) 텍스트 라벨 파싱 폴백
    label = _extract_label_from_text(getattr(ai, "content", ""))
    if label in _ROUTE_LABELS:
        return label

    # 3) 최종 휴리스틱 폴백
    return _heuristic_route(state)


# ─────────────────────────────────────────────────────────────────────────────
# 노드 팩토리 / conditional edge
# ─────────────────────────────────────────────────────────────────────────────
def make_top_router_node(deps: Any):
    """Top Supervisor 라우터 노드 팩토리 — `top_router_node(state)` 콜러블을 반환.

    `build_top_graph` 에서 `g.add_node("router", make_top_router_node(deps))` 로 배선한다.

    반환 노드의 계약:
      Precondition:  state["prompt"] 는 비어있지 않다.
      Postcondition: hop cap 도달 시 {"route": "done"}(visited_routes 미증가) 반환.
                     그 외에는 {"route": <label>, "visited_routes": [<label>]} 반환
                     (visited_routes reducer 가 operator.add 로 누적).
      Invariant:     LLM 호출은 GatewayChatModel(gateway 경유)만. 분류 실패는 비차단.
    """

    async def top_router_node(state: GraphState) -> dict:
        # 요구사항 6.5 / Property 4: hop cap 도달 시 LLM 없이 즉시 종료.
        if len(state.get("visited_routes", []) or []) >= MAX_ROUTE_HOPS:
            return {"route": "done"}

        route = await _classify_route(state, deps)
        return {"route": route, "visited_routes": [route]}

    return top_router_node


def route_selector(state: GraphState) -> str:
    """conditional edge 함수 — state["route"] 를 그대로 반환한다.

    반환 라벨(coding/media/research/ops/chat/done)은 build_top_graph 의
    add_conditional_edges path_map 키와 일치해야 한다. route 미설정 시 안전하게 "done".
    """
    return state.get("route") or "done"


# ─────────────────────────────────────────────────────────────────────────────
# Top 그래프 조립 (graph-of-graphs — Task 3.2)
# ─────────────────────────────────────────────────────────────────────────────
# 재라우팅 대상 도메인 라벨(각각 컴파일된 서브그래프 노드로 add 되고, 종료 후 router 로 복귀).
_SUBGRAPH_ROUTES = ("coding", "media", "research", "ops", "chat")


def build_top_graph(deps: Any):
    """Top Supervisor + 도메인 서브그래프들을 조립해 compiled Runnable 을 반환.

    design.md 섹션 3(Top Supervisor + StateGraph 조립) + API_NOTES.md 항목 6
    (`add_node(name, compiled_subgraph)` 유효 확인)을 근거로 한 **graph-of-graphs** 다.

    구성:
      START → router
      conditional(router, route_selector) → {coding, media, research, ops, chat, done→END}
      각 서브그래프(coding/media/research/ops/chat) → router  (멀티 도메인 재라우팅)

    무한 순환 차단(요구사항 1.5 / Property 4): 재라우팅은 router 의 hop cap
    (make_top_router_node 내부 `MAX_ROUTE_HOPS`)에 의해 유한하게 종료된다. router 는
    visited_routes 길이가 상한에 도달하면 LLM 호출 없이 route="done" 을 반환하고,
    route_selector→END 로 유한 종료한다.

    Args:
        deps: GraphDeps (gateway / model_coding / checkpointer). checkpointer 는 이
              최상위 그래프 compile 에서만 주입되며, 서브그래프는 이를 상속한다
              (API_NOTES 항목 6 — 서브그래프 sg.compile() 에는 checkpointer 미주입).

    Postcondition:
      - 컴파일된 서브그래프 5개가 노드로 add 되어 graph-of-graphs 를 이룬다(요구사항 1.1).
      - route 라벨에 따라 해당 서브그래프로 진입하고(요구사항 1.3), route="done" 이면
        즉시 END 로 종료한다(요구사항 1.4).
      - deps.checkpointer 가 있으면 compile(checkpointer=...) 로 thread_id 기반 영속을
        바인딩하고(요구사항 4.6), None 이면 checkpointer 없이 compile 한다.
    """
    g = StateGraph(GraphState)

    # ── 라우터 노드 ──
    g.add_node("router", make_top_router_node(deps))

    # ── 컴파일된 서브그래프를 노드로 add (graph-of-graphs 핵심, API_NOTES 항목 6) ──
    g.add_node("coding", build_coding_subgraph(deps))
    g.add_node("media", build_media_subgraph(deps))
    g.add_node("research", build_research_subgraph(deps))
    g.add_node("ops", build_ops_subgraph(deps))
    g.add_node("chat", build_chat_subgraph(deps))

    # ── 진입 + 라우팅 edge ──
    g.add_edge(START, "router")
    g.add_conditional_edges(
        "router",
        route_selector,
        {
            "coding": "coding",
            "media": "media",
            "research": "research",
            "ops": "ops",
            "chat": "chat",
            "done": END,
        },
    )

    # ── 서브그래프 종료 후 재라우팅(멀티 도메인) — 다시 router 로. hop cap 이 무한 순환 차단. ──
    for name in _SUBGRAPH_ROUTES:
        g.add_edge(name, "router")

    # ── compile: checkpointer 는 최상위에서만 주입(API_NOTES 항목 6 / 요구사항 4.6) ──
    checkpointer = getattr(deps, "checkpointer", None)
    if checkpointer is not None:
        return g.compile(checkpointer=checkpointer)
    return g.compile()
