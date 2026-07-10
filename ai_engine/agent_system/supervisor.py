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

from langchain_core.messages import AIMessage, HumanMessage, SystemMessage
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

# 라우터 분류가 선택 가능한 도메인 라벨.
_ROUTE_LABELS = ("coding", "media", "research", "ops", "chat")
# 재진입(서브그래프 1회 이상 방문 후) 시에는 done 도 선택 가능하다 — 멀티도메인 체이닝의
# 완료 판정을 LLM 이 직접 내린다(supervisor-of-supervisors). 첫 진입에는 done 을 제외한다.
_ROUTE_LABELS_WITH_DONE = _ROUTE_LABELS + ("done",)

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
def _make_route_tool(allow_done: bool) -> dict:
    """select_route 도구 스키마 생성. allow_done=True 면 enum 에 done 을 포함한다.

    subtask: route 가 도메인(비-done)일 때, 그 도메인이 이번 단계에서 수행할 구체적 작업을
    한국어 한두 문장으로 기술한다. 멀티도메인 체이닝에서 다음 서브그래프에 명확한 지시를
    전달하는 데 쓰인다(supervisor→worker 작업 지시). done 이면 비워둔다.
    """
    labels = _ROUTE_LABELS_WITH_DONE if allow_done else _ROUTE_LABELS
    desc = (
        "사용자 요청을 처리할 다음 단계를 하나 선택하고, 그 단계가 수행할 작업을 subtask 에 "
        "기술한다. "
        "coding: 코드 이해/수정/리팩터/디버그·파일 검색·명령. "
        "media: pptx/pdf/이미지/docx/xlsx/슬라이드/다이어그램 생성. "
        "research: 웹 검색/문서 조사/요약. "
        "ops: 셸 명령/git/원격 SSH 운영 작업. "
        "chat: 도구가 필요 없는 일반 대화."
    )
    if allow_done:
        desc += (
            " done: 사용자의 원래 요청이 이미 모두 충족되어 더 이상 다른 도메인 작업이 "
            "필요 없을 때 선택한다(작업 종료, subtask 불필요)."
        )
    return {
        "name": "select_route",
        "description": desc,
        "inputSchema": {
            "json": {
                "type": "object",
                "properties": {
                    "route": {
                        "type": "string",
                        "enum": list(labels),
                        "description": "선택한 다음 단계 라벨",
                    },
                    "subtask": {
                        "type": "string",
                        "description": (
                            "선택한 도메인이 이번 단계에서 수행할 구체적 작업(한국어 1~2문장). "
                            "done 이면 빈 문자열."
                        ),
                    },
                },
                "required": ["route"],
            }
        },
    }


_ROUTER_SYSTEM_PROMPT = (
    "너는 계층적 오케스트레이터의 최상위 라우터다. 사용자의 원래 요청과 지금까지 수행한 "
    "작업(이미 방문한 도메인, 직전 응답)을 보고, 요청을 완수하기 위한 다음 단계 도메인 "
    "하나를 골라 select_route 도구를 호출한다. 가능한 라벨: coding, media, research, ops, chat, done.\n"
    "판정 규칙:\n"
    "- 사용자의 요청에 여러 도메인 작업이 포함되면(예: '코드를 분석하고 그 결과로 PPT를 "
    "만들어줘'), 한 번에 하나씩 순서대로 처리한다.\n"
    "- 이미 완료한 도메인 작업을 같은 목적으로 다시 선택하지 마라(반복 금지).\n"
    "- 원래 요청이 모두 충족되었으면 반드시 done 을 선택한다.\n"
    "- 도구를 사용할 수 없으면 라벨 단어 하나만 출력한다."
)


# ─────────────────────────────────────────────────────────────────────────────
# 분류 컨텍스트 구성
# ─────────────────────────────────────────────────────────────────────────────
def _last_ai_text(messages: list, limit: int = 800) -> str:
    """messages 에서 마지막 AIMessage 의 텍스트를 추출(멀티도메인 완료 판정 컨텍스트용)."""
    for m in reversed(messages or []):
        if isinstance(m, AIMessage):
            c = getattr(m, "content", "")
            if isinstance(c, list):
                c = "".join(
                    p.get("text", "") if isinstance(p, dict) else str(p) for p in c
                )
            c = str(c).strip()
            if c:
                return c[:limit]
    return ""


def _build_router_prompt(state: GraphState) -> str:
    """프롬프트 + 컨텍스트(open_file) + 진행 상황(visited/직전 응답)을 라우터 입력으로 구성.

    재진입 시 라우터가 '원래 요청이 충족됐는지'를 판정할 수 있도록, 이미 방문한 도메인과
    직전 assistant 응답 요약을 함께 제공한다(멀티도메인 체이닝의 완료 판정 근거).
    """
    parts: List[str] = []
    prompt = state.get("prompt") or ""
    parts.append(f"[원래 요청]\n{prompt}")

    open_file = state.get("open_file")
    if isinstance(open_file, str) and open_file.strip():
        parts.append(f"[열린 파일]\n{open_file.strip()}")

    template_id = state.get("template_id")
    if isinstance(template_id, str) and template_id.strip():
        parts.append(f"[템플릿]\n{template_id.strip()}")

    visited = state.get("visited_routes") or []
    if visited:
        parts.append(f"[이미 수행한 도메인(순서대로)]\n{', '.join(visited)}")
        last_text = _last_ai_text(state.get("messages") or [])
        if last_text:
            parts.append(f"[직전 응답 요약]\n{last_text}")
        parts.append(
            "위 진행 상황을 근거로: 원래 요청이 모두 충족되었으면 done 을, 아직 남은 "
            "다른 도메인 작업이 있으면 그 도메인을 선택하라."
        )

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
async def _classify_route(
    state: GraphState, deps: Any, allow_done: bool = False
) -> tuple:
    """GatewayChatModel(sonnet-4-5)로 다음 단계 (route, subtask) 를 분류해 반환한다.

    Args:
        allow_done: True 면 재진입 상황으로 간주하고 done 을 유효 라벨로 허용한다
                    (멀티도메인 완료 판정). False(첫 진입)면 done 을 제외한다.

    Returns:
        (route, subtask) 튜플. route 는 유효 라벨(allow_done 이면 done 포함). subtask 는
        해당 도메인이 수행할 작업 지시(없으면 ""). LLM 실패/애매/타임아웃이면 폴백
        (재진입=done, 첫 진입=휴리스틱)을 반환(비차단).

    Invariant: LLM 호출은 GatewayChatModel(gateway 경유)만. 개별 await 하나만
               asyncio.wait_for(ROUTER_TIMEOUT)로 감싼다(스트림 아님).
    """
    valid = _ROUTE_LABELS_WITH_DONE if allow_done else _ROUTE_LABELS
    gateway = getattr(deps, "gateway", None)
    if gateway is None:
        return ("done", "") if allow_done else (_heuristic_route(state), "")

    model_id = getattr(deps, "model_coding", None) or _DEFAULT_ROUTER_MODEL

    try:
        llm = GatewayChatModel(gateway=gateway, model_id=model_id).bind_tools(
            [_make_route_tool(allow_done)], tool_choice="select_route"
        )
        messages = [
            SystemMessage(content=_ROUTER_SYSTEM_PROMPT),
            HumanMessage(content=_build_router_prompt(state)),
        ]
        ai = await asyncio.wait_for(llm.ainvoke(messages), timeout=ROUTER_TIMEOUT)
    except (asyncio.TimeoutError, GatewayModelError, Exception):
        return ("done", "") if allow_done else (_heuristic_route(state), "")

    # 1) toolChoice 강제 스키마 응답 우선
    tool_calls = getattr(ai, "tool_calls", None) or []
    for tc in tool_calls:
        args = tc.get("args") if isinstance(tc, dict) else None
        if isinstance(args, dict):
            label = args.get("route")
            if isinstance(label, str) and label.lower() in valid:
                subtask = args.get("subtask")
                return (label.lower(), subtask.strip() if isinstance(subtask, str) else "")

    # 2) 텍스트 라벨 파싱 폴백
    label = _extract_label_from_text(getattr(ai, "content", ""))
    if label in valid:
        return (label, "")
    if allow_done and isinstance(getattr(ai, "content", ""), str) and "done" in ai.content.lower():
        return ("done", "")

    # 3) 최종 폴백
    return ("done", "") if allow_done else (_heuristic_route(state), "")


# ─────────────────────────────────────────────────────────────────────────────
# 노드 팩토리 / conditional edge
# ─────────────────────────────────────────────────────────────────────────────
def make_top_router_node(deps: Any):
    """Top Supervisor 라우터 노드 팩토리 — `top_router_node(state)` 콜러블을 반환.

    `build_top_graph` 에서 `g.add_node("router", make_top_router_node(deps))` 로 배선한다.

    반환 노드의 계약:
      Precondition:  state["prompt"] 는 비어있지 않다.
      Postcondition: hop cap 도달 시 {"route": "done"}(visited_routes 미증가) 반환.
                     첫 진입(visited 비었음): done 불가 분류 → {"route": <label>, "visited_routes": [<label>]}.
                     재진입(visited 있음): 라우터가 원래 요청 충족 여부를 판정 —
                       · done 이면 {"route": "done"}(visited 미증가)로 종료.
                       · 다른 도메인이 남았으면 {"route": <label>, "visited_routes": [<label>]}로
                         멀티도메인 체이닝 계속(supervisor-of-supervisors).
      Invariant:     LLM 호출은 GatewayChatModel(gateway 경유)만. 분류 실패는 비차단
                     (재진입 실패 시 done 으로 안전 종료). hop cap 이 순환을 유한 종료.
    """

    async def top_router_node(state: GraphState) -> dict:
        visited = state.get("visited_routes", []) or []

        # 요구사항 6.5 / Property 4: hop cap 도달 시 LLM 없이 즉시 종료(무한 순환 차단).
        if len(visited) >= MAX_ROUTE_HOPS:
            return {"route": "done"}

        # 방어: 마지막 메시지가 도구호출 대기(tool_calls 있는 AIMessage)면 서브그래프 내부에서
        # 처리돼야 하며 여기 도달하면 안 되지만, 안전하게 done 으로 종료한다.
        messages = state.get("messages") or []
        if visited and messages:
            last = messages[-1]
            if isinstance(last, AIMessage) and getattr(last, "tool_calls", None):
                return {"route": "done"}

        # 첫 진입엔 done 불가(반드시 도메인 하나 실행). 재진입엔 done 허용(완료 판정).
        allow_done = bool(visited)
        route, subtask = await _classify_route(state, deps, allow_done=allow_done)

        if route == "done":
            return {"route": "done"}

        out: dict = {"route": route, "visited_routes": [route]}

        # 재진입(멀티도메인 체이닝)에서 다음 도메인에 명확한 지시를 전달한다. 직전 도메인의
        # 최종 AIMessage 로 messages 가 끝나 있으면, 새 도메인의 model 호출이 "생성할 것 없음"
        # (No generations found in stream)을 반환하므로, subtask 를 HumanMessage 로 추가해
        # messages 가 사용자 턴으로 끝나게 하고 다음 워커에게 작업을 지시한다(supervisor→worker).
        if visited:
            instruction = subtask or (state.get("prompt") or "")
            if instruction:
                out["messages"] = [HumanMessage(content=instruction)]

        return out

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

    # ── compile: checkpointer + store 는 최상위에서만 주입(API_NOTES 항목 6 / 요구사항 4.6) ──
    # store(BaseStore)는 세션 간 장기 메모리. 부모 그래프에 주입하면 서브그래프 노드로 전파된다.
    compile_kwargs: dict = {}
    checkpointer = getattr(deps, "checkpointer", None)
    if checkpointer is not None:
        compile_kwargs["checkpointer"] = checkpointer
    store = getattr(deps, "store", None)
    if store is not None:
        compile_kwargs["store"] = store
    return g.compile(**compile_kwargs)
