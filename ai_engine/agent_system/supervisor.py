"""Top Supervisor 라우터 노드 — 사용자 의도를 도메인 Route 로 분류.

Task 3.1 산출물. design.md 섹션 3(Top Supervisor + StateGraph 조립) + API_NOTES.md
(항목 1·5) + 요구사항 1.2 / 6.5 를 근거로 한다.

핵심 사항:
- **팩토리 패턴 (`make_top_router_node(deps)`):** design 은 라우터가 GatewayChatModel
  을 통해 LLM 분류를 수행해야 하므로 `deps`(GraphDeps: gateway / model_coding) 주입이
  필요하다. `build_top_graph` 에서 `g.add_node("router", make_top_router_node(deps))`
  형태로 배선하기 쉽도록 노드 콜러블을 반환하는 팩토리로 구현한다.
- **hop cap (요구사항 6.5 / Property 4):** `route_hops`(last-wins reducer)가
  `MAX_ROUTE_HOPS`(기본 4, `AE_MAX_ROUTE_HOPS`)에 도달하면 LLM 호출 없이 즉시
  `{"route": "done"}` 를 반환하여 재라우팅 순환을 종료한다(무한 순환 차단 — 과거 hang
  이력 대응). **주의:** `visited_routes`(operator.add)는 서브그래프 공유 채널이라 부모
  리듀서에 재합산돼 hop마다 복리로 폭증(echo)하므로 hop cap 판정에는 쓰지 않는다 —
  echo에 면역인 last-wins 정수 카운터 `route_hops` 를 신뢰 지표로 사용한다.
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
from langgraph.types import Send

from ai_engine.agent_system.chat_model_adapter import (
    GatewayChatModel,
    GatewayModelError,
)
from ai_engine.agent_system.dag import sanitize_depends_on, topological_waves
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


def _env_flag(name: str, default: bool) -> bool:
    """불리언 env 플래그 판독. 미설정 시 default. 값은 호출 시점에 읽어 테스트 토글을 허용한다.

    off 로 해석되는 값: "0", "false", "no", "off", "" (대소문자 무시). 그 외는 on.
    """
    raw = os.environ.get(name)
    if raw is None:
        return default
    return raw.strip().lower() not in ("0", "false", "no", "off", "")


def _obs_log(prefix: str, message: str) -> None:
    """구조화 관측 로그(프로젝트 관례 print 기반, verify.py 의 `[verify] ...` 스타일).

    프로덕션 운영 시 evaluator 재계획 루프·DAG Wave 진행·aggregate 종합의 진행 상황을
    추적하기 위한 비차단 로깅. 노드 로직/반환값/성능에 영향을 주지 않도록 방어적으로 감싼다
    (로깅 자체가 예외를 던져도 노드 실행을 막지 않는다).

    보안(자격증명·PII 미로깅): 호출자는 refine_count/wave 인덱스/도메인 라벨/subtask 개수 같은
    **메타데이터만** 전달한다. subtask 내용 전문·messages 원문·aws_profile/bedrock_user·
    프롬프트 전문은 절대 전달하지 않는다(길이/개수만 허용).
    """
    try:
        print(f"[{prefix}] {message}")
    except Exception:  # noqa: BLE001 — 로깅은 비차단(가용성 우선).
        pass


# 재라우팅 hop 상한(요구사항 6.5). route_hops(last-wins) 가 이 값에 도달하면 route="done".
# visited_routes(operator.add)는 서브그래프 echo로 복리 폭증하므로 판정에 쓰지 않는다.
MAX_ROUTE_HOPS: int = _env_int("AE_MAX_ROUTE_HOPS", 4)
# 라우터 LLM 개별 호출 상한(초). 스트림 아님(ainvoke). 초과/실패는 비차단 폴백.
ROUTER_TIMEOUT: float = _env_float("AE_ROUTER_TIMEOUT", 60.0)
# 플래너 LLM 개별 호출 상한(초). 스트림 아님(ainvoke). 초과/실패는 단일 서브태스크 폴백.
# ⚠️ 게이트웨이 /converse 는 toolConfig 동반 호출을 비동기 S3 잡 폴링(최대 300s)으로 처리하므로,
# planner 타임아웃이 폴링 상한보다 짧으면(과거 60s) 게이트웨이가 응답하기 전에 조기 폴백되어
# DAG 분해가 통째로 무력화된다(단일 서브태스크로 축약). 폴링 상한과 정렬해 게이트웨이가
# 스스로 종결할 때까지 기다린다(정상 시엔 모델 응답 즉시 반환되어 지연 없음).
PLANNER_TIMEOUT: float = _env_float("AE_PLANNER_TIMEOUT", 300.0)


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
        # visited_routes 는 operator.add 채널이라 서브그래프 echo로 같은 도메인이 중복
        # 누적될 수 있다. 프롬프트 품질을 위해 순서를 보존하며 중복만 제거해 표시한다.
        visited_display = list(dict.fromkeys(visited))
        parts.append(f"[이미 수행한 도메인(순서대로)]\n{', '.join(visited_display)}")
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
      Postcondition: hop cap 도달(route_hops >= MAX_ROUTE_HOPS) 시 {"route": "done"} 반환.
                     첫 진입(route_hops==0): done 불가 분류 →
                       {"route": <label>, "visited_routes": [<label>], "route_hops": 1}.
                     재진입(route_hops>0): 라우터가 원래 요청 충족 여부를 판정 —
                       · done 이면 {"route": "done"}(route_hops 미증가)로 종료.
                       · 다른 도메인이 남았으면 {"route": <label>, "visited_routes": [<label>],
                         "route_hops": route_hops+1}로 멀티도메인 체이닝 계속.
      Invariant:     LLM 호출은 GatewayChatModel(gateway 경유)만. 분류 실패는 비차단
                     (재진입 실패 시 done 으로 안전 종료). hop cap 은 route_hops(last-wins,
                     echo 면역)로 판정하여 순환을 유한 종료한다. visited_routes(operator.add)는
                     서브그래프 echo로 복리 폭증하므로 판정에 쓰지 않고 관측/프롬프트용으로만
                     유지한다.
    """

    async def top_router_node(state: GraphState) -> dict:
        visited = state.get("visited_routes", []) or []
        # hop cap 판정 지표: route_hops(last-wins, 서브그래프 echo에 면역인 정확한 hop 계수).
        hops = state.get("route_hops", 0) or 0

        # 요구사항 6.5 / Property 4: hop cap 도달 시 LLM 없이 즉시 종료(무한 순환 차단).
        if hops >= MAX_ROUTE_HOPS:
            return {"route": "done"}

        # 방어: 마지막 메시지가 도구호출 대기(tool_calls 있는 AIMessage)면 서브그래프 내부에서
        # 처리돼야 하며 여기 도달하면 안 되지만, 안전하게 done 으로 종료한다.
        messages = state.get("messages") or []
        if hops > 0 and messages:
            last = messages[-1]
            if isinstance(last, AIMessage) and getattr(last, "tool_calls", None):
                return {"route": "done"}

        # 첫 진입엔 done 불가(반드시 도메인 하나 실행). 재진입엔 done 허용(완료 판정).
        # 재진입 판정은 route_hops>0 로 한다(visited echo와 무관하게 정확).
        allow_done = hops > 0
        route, subtask = await _classify_route(state, deps, allow_done=allow_done)

        if route == "done":
            return {"route": "done"}

        # route_hops 를 함께 증가시켜 다음 hop 판정을 정확하게 유지한다. visited_routes 는
        # 관측/프롬프트용으로 계속 반환(operator.add 로 누적되지만 hop cap 판정에는 미사용).
        out: dict = {"route": route, "visited_routes": [route], "route_hops": hops + 1}

        # 재진입(멀티도메인 체이닝)에서 다음 도메인에 명확한 지시를 전달한다. 직전 도메인의
        # 최종 AIMessage 로 messages 가 끝나 있으면, 새 도메인의 model 호출이 "생성할 것 없음"
        # (No generations found in stream)을 반환하므로, subtask 를 HumanMessage 로 추가해
        # messages 가 사용자 턴으로 끝나게 하고 다음 워커에게 작업을 지시한다(supervisor→worker).
        if hops > 0:
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
    route_hops(last-wins reducer)가 상한에 도달하면 LLM 호출 없이 route="done" 을 반환하고,
    route_selector→END 로 유한 종료한다. route_hops 는 서브그래프 공유 채널
    (visited_routes=operator.add)의 echo 복리 폭증에 면역이라 hop 예산을 정확히 집행한다.

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


# ─────────────────────────────────────────────────────────────────────────────
# 평가 결과 파싱 순수 함수 (Task 4 / Property 8)
# ─────────────────────────────────────────────────────────────────────────────
def _attr_or_key(obj: Any, key: str) -> Any:
    """obj 가 dict 면 key 로, 아니면 attribute 로 값을 안전 조회(없으면 None)."""
    if isinstance(obj, dict):
        return obj.get(key)
    return getattr(obj, key, None)


def _content_to_text(content: Any) -> str:
    """AIMessage.content(문자열 또는 멀티모달 list)를 평문 텍스트로 평탄화."""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return "".join(
            p.get("text", "") if isinstance(p, dict) else str(p) for p in content
        )
    if content is None:
        return ""
    return str(content)


def _try_parse_json_obj(text: str) -> Optional[dict]:
    """텍스트에서 JSON 오브젝트를 관대하게 추출(첫 '{' ~ 마지막 '}'). 실패 시 None."""
    if not isinstance(text, str) or "{" not in text:
        return None
    import json

    # 1) 전체 파싱 시도
    try:
        obj = json.loads(text)
        return obj if isinstance(obj, dict) else None
    except Exception:
        pass
    # 2) 첫 '{' ~ 마지막 '}' 구간 파싱 시도
    try:
        start = text.index("{")
        end = text.rindex("}")
        if end > start:
            obj = json.loads(text[start : end + 1])
            return obj if isinstance(obj, dict) else None
    except Exception:
        return None
    return None


def _coerce_evaluation(data: dict, valid_set: set) -> dict:
    """임의 dict 를 Evaluation 계약 형태로 강제 변환(방어적).

    achieved 누락/무효 시 True(비차단). missing_domains 는 valid_set 부분집합으로 필터.
    """
    raw_ach = data.get("achieved")
    if isinstance(raw_ach, bool):
        achieved = raw_ach
    elif isinstance(raw_ach, str):
        achieved = raw_ach.strip().lower() in ("true", "1", "yes", "y", "달성", "achieved")
    elif raw_ach is None:
        achieved = True  # 누락 → 비차단 종료 지향
    else:
        achieved = bool(raw_ach)

    raw_reason = data.get("reason")
    if isinstance(raw_reason, str):
        reason = raw_reason
    elif raw_reason is None:
        reason = ""
    else:
        reason = str(raw_reason)

    missing: List[str] = []
    raw_md = data.get("missing_domains")
    if isinstance(raw_md, (list, tuple)):
        for d in raw_md:
            if isinstance(d, str) and d in valid_set and d not in missing:
                missing.append(d)

    return {"achieved": achieved, "reason": reason, "missing_domains": missing}


def parse_evaluation(ai_message: Any, valid_domains: tuple) -> dict:
    """Evaluator LLM 응답(tool_calls 우선, 텍스트 폴백)을 Evaluation dict 로 파싱.

    Postcondition: 항상 {"achieved": bool, "reason": str, "missing_domains": list[str]} 반환.
                   missing_domains 는 valid_domains 에 속한 라벨만 포함(무효 라벨 제거).
                   파싱 불가/무효 시 achieved=True(비차단 종료 지향 — Req 1.6/6.6).
    Invariant:     어떤 예외도 전파하지 않는다(비차단).
    """
    valid_set = set(valid_domains or ())
    default = {"achieved": True, "reason": "", "missing_domains": []}
    try:
        # 1) tool_calls 우선 — 평가 관련 필드를 가진 첫 호출을 채택.
        tool_calls = _attr_or_key(ai_message, "tool_calls") or []
        if isinstance(tool_calls, (list, tuple)):
            for tc in tool_calls:
                args = tc.get("args") if isinstance(tc, dict) else _attr_or_key(tc, "args")
                if isinstance(args, dict) and (
                    "achieved" in args or "missing_domains" in args or "reason" in args
                ):
                    return _coerce_evaluation(args, valid_set)

        # 2) 텍스트 폴백 — content 내 JSON 오브젝트 추출 시도.
        text = _content_to_text(_attr_or_key(ai_message, "content"))
        parsed = _try_parse_json_obj(text)
        if isinstance(parsed, dict):
            return _coerce_evaluation(parsed, valid_set)

        # 3) 파싱 불가 → 안전 종료(achieved=True).
        return dict(default)
    except Exception:
        return dict(default)


# ─────────────────────────────────────────────────────────────────────────────
# 병렬 fan-out (Send API) — 독립 다중 도메인 요청을 동시에 실행 (map-reduce)
# ─────────────────────────────────────────────────────────────────────────────
# 순차 멀티홉(build_top_graph)은 "A 완료 → 재라우팅 → B" 로 도메인을 하나씩 처리한다.
# 요청에 서로 독립적인 여러 산출물이 있을 때(예: "PPT 도 만들고 코드도 리팩터"), 이를
# 동시에 실행하면 지연이 크게 준다. LangGraph 의 Send 로 planner 가 분해한 서브태스크들을
# 병렬 워커(도메인 서브그래프)로 fan-out 하고, aggregate 노드에서 fan-in 한다.
#
# 무한/과다 fan-out 방지: 최대 MAX_PARALLEL_TASKS(기본 4) 개로 제한. planner 실패/단일이면
# 워커 1개(사실상 순차와 동일). recursion_limit 는 planner→workers→aggregate 로 얕다.
MAX_PARALLEL_TASKS: int = _env_int("AE_MAX_PARALLEL_TASKS", 4)

_PLAN_TOOL: dict = {
    "name": "select_plan",
    "description": (
        "사용자 요청을 서브태스크들로 분해한다. 각 서브태스크는 고유 id, 도메인"
        "(coding/media/research/ops/chat), 그 도메인이 수행할 구체적 작업(subtask), 그리고 "
        "선행 완료가 필요한 서브태스크 id 목록(depends_on)을 가진다. 서로 독립적인 작업은 "
        "depends_on 을 비워 동시 실행되게 하고, 뒤 작업이 앞 작업 결과에 의존하면 앞 작업의 "
        "id 를 depends_on 에 넣는다(예: 코드 분석 결과로 PPT 생성). 요청이 단일 작업이면 "
        "subtasks 를 1개만 만든다."
    ),
    "inputSchema": {
        "json": {
            "type": "object",
            "properties": {
                "subtasks": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {
                            "id": {"type": "string", "description": "서브태스크 고유 식별자(예: t1)"},
                            "domain": {"type": "string", "enum": list(_ROUTE_LABELS)},
                            "subtask": {"type": "string", "description": "해당 도메인이 수행할 구체적 작업(한국어)"},
                            "depends_on": {
                                "type": "array",
                                "items": {"type": "string"},
                                "description": "이 작업 시작 전 완료되어야 할 선행 서브태스크 id 목록",
                            },
                        },
                        "required": ["id", "domain", "subtask"],
                    },
                }
            },
            "required": ["subtasks"],
        }
    },
}

_PLANNER_SYSTEM_PROMPT = (
    "너는 의존성 인식 작업 분해 플래너다. 사용자 요청을 서브태스크로 나눠 select_plan 을 "
    "호출한다. 각 서브태스크에는 고유 id(t1, t2 …), 도메인, 구체적 작업(subtask), 그리고 "
    "선행 서브태스크 id 목록(depends_on)을 부여한다. 서로 독립적인 작업은 depends_on 을 "
    "비워 병렬 실행되게 하고, 앞 작업 결과에 의존하는 작업은 그 앞 작업 id 를 depends_on 에 "
    "넣는다. 단일 작업이면 subtasks 를 1개로 둔다. 도메인은 coding/media/research/ops/chat 중 선택."
)


async def _make_plan(state: GraphState, deps: Any) -> List[dict]:
    """요청을 [{"domain","subtask"}] 로 분해. LLM 실패/애매 시 단일 휴리스틱 폴백(비차단)."""
    prompt = state.get("prompt") or ""
    # 폴백도 확장 스키마(id/depends_on)를 갖춰 plan 항목 형태를 항상 일관되게 유지한다.
    fallback = [{"id": "t0", "domain": _heuristic_route(state), "subtask": prompt, "depends_on": []}]
    gateway = getattr(deps, "gateway", None)
    if gateway is None:
        return fallback
    # 역할 모델 배분(요구사항 9.2): planner 는 전용 model_planner 를 우선 사용하고,
    # 미주입 시 model_coding → 기본값으로 폴백한다.
    model_id = (
        getattr(deps, "model_planner", None)
        or getattr(deps, "model_coding", None)
        or _DEFAULT_ROUTER_MODEL
    )
    try:
        llm = GatewayChatModel(gateway=gateway, model_id=model_id).bind_tools(
            [_PLAN_TOOL], tool_choice="select_plan"
        )
        messages = [
            SystemMessage(content=_PLANNER_SYSTEM_PROMPT),
            HumanMessage(content=_build_router_prompt(state)),
        ]
        # PLANNER_TIMEOUT(폴링 상한 정렬) — 게이트웨이 비동기 잡을 조기 폴백하지 않는다.
        ai = await asyncio.wait_for(llm.ainvoke(messages), timeout=PLANNER_TIMEOUT)
    except (asyncio.TimeoutError, GatewayModelError, Exception):
        return fallback

    for tc in (getattr(ai, "tool_calls", None) or []):
        args = tc.get("args") if isinstance(tc, dict) else None
        if isinstance(args, dict) and isinstance(args.get("subtasks"), list):
            out: List[dict] = []
            for i, it in enumerate(args["subtasks"]):
                if not isinstance(it, dict):
                    continue
                dom = it.get("domain")
                sub = it.get("subtask")
                if dom not in _ROUTE_LABELS or not isinstance(sub, str) or not sub.strip():
                    continue
                # id: 제공된 비어있지 않은 문자열 우선, 누락/무효 시 인덱스 기반 "t{i}" 보정.
                raw_id = it.get("id")
                item_id = raw_id.strip() if isinstance(raw_id, str) and raw_id.strip() else f"t{i}"
                # depends_on: 문자열 원소만 남긴 리스트, 누락/무효 시 기본 [].
                raw_deps = it.get("depends_on")
                deps_list = (
                    [d for d in raw_deps if isinstance(d, str)]
                    if isinstance(raw_deps, (list, tuple))
                    else []
                )
                out.append(
                    {"id": item_id, "domain": dom, "subtask": sub.strip(), "depends_on": deps_list}
                )
            if out:
                return out[:MAX_PARALLEL_TASKS]
    return fallback


def make_planner_node(deps: Any):
    """planner 노드 팩토리 → async def planner_node(state) -> dict.

    planner 는 두 가지 이유로 진입한다(설계 결정 A — planner↔aggregate 순환):
      1. 최초 진입(START) 또는 evaluator 재계획(Refine_Loop, aggregate 이후 evaluator →
         planner) → 새 plan 을 생성하고 completed_waves 를 0 으로 리셋한다.
      2. 다중 Wave 진행(aggregate → planner) → 기존 plan 을 유지한 채 completed_waves 를
         1 증가시켜 다음 Wave 를 dispatch 한다(LLM 재호출 없음).

    두 진입의 구분 기준은 "현재 plan 에 아직 dispatch 하지 않은 Wave 가 남았는가"이다:
    completed_waves+1 < 전체 Wave 수 이면(=다음 Wave 존재) Wave 진행으로 간주해 카운터만
    증가시킨다. 그 외(최초 진입=plan 부재, 또는 plan 소진 후 evaluator 재계획)는 새 plan 을
    만든다. after_aggregate_selector 는 "남은 Wave 있음"일 때만 planner 로 보내고, evaluator
    는 모든 Wave 소진(=plan 마지막 Wave 완료) 후에만 planner 로 refine 을 보내므로, 이
    completed_waves+1 < len(waves) 판정으로 두 경우가 정확히 갈린다(evaluator 상태를 읽지
    않아도 애매성이 없다).

    유한 종료: Wave 진행 복귀 횟수는 전체 Wave 수(≤ 서브태스크 수) 이하이고, refine 복귀는
    refine_count cap 으로 제한된다(요구사항 2.4 / 4.2 / 5.4 / 8.5). 무회귀: DAG 비활성이면
    항상 새 plan 을 만들어 기존 단일 Wave 동작과 동일하다(요구사항 4.7 / 6.3).
    """

    async def planner_node(state: GraphState) -> dict:
        # 다중 Wave 진행 재진입 판정: DAG 활성 + 기존 plan 에 남은 Wave 가 있으면 새 계획
        # 없이 completed_waves 만 증가시켜 다음 Wave 를 dispatch 한다(evaluator refine 루프와
        # 구분 — 설계 결정 A). completed_waves 는 last-wins 이라 echo 증폭 없이 정확히 집계된다.
        if _env_flag("AE_ENABLE_DAG_PLANNER", default=True):
            plan = state.get("plan") or []
            if plan:
                waves = topological_waves(sanitize_depends_on(plan))
                completed = state.get("completed_waves", 0) or 0
                if completed + 1 < len(waves):
                    # Wave 진행: 완료된 Wave 인덱스만 로깅(메타데이터, subtask 내용 없음).
                    _obs_log(
                        "reasoning:planner",
                        f"wave advance: {completed}->{completed + 1} of {len(waves)}",
                    )
                    return {"completed_waves": completed + 1}

        # 최초 진입 또는 evaluator 재계획 → 새 plan 생성 + Wave 커서 리셋(completed_waves=0).
        new_plan = await _make_plan(state, deps)
        # 새 plan 개수/Wave 수만 로깅(subtask 내용·프롬프트 전문 미로깅).
        try:
            _wave_count = len(topological_waves(sanitize_depends_on(new_plan)))
        except Exception:  # noqa: BLE001 — 로깅용 계산 실패는 비차단.
            _wave_count = 0
        _obs_log(
            "reasoning:planner",
            f"new plan: {len(new_plan)} subtasks, {_wave_count} waves",
        )
        return {"plan": new_plan, "completed_waves": 0}

    return planner_node


# 병렬 워커에 전달할 state 필수 필드(자격증명 제외 — 문자열 식별자만, 요구사항 8.1).
_WORKER_STATE_KEYS = (
    "prompt", "session_id", "project_path", "open_file", "open_file_content",
    "aws_profile", "bedrock_user", "template_id", "system_prompt", "is_remote",
)


def plan_dispatch(state: GraphState):
    """conditional edge — 현재 Wave 의 서브태스크만 Send 로 도메인 워커에 병렬 fan-out.

    각 워커는 공유 messages(대화 맥락) + 자신의 subtask HumanMessage 를 받아 독립 실행한다.
    반환값(messages/verified_files)은 GraphState reducer 로 병합된다(add_messages dedup /
    verified_files dedup). plan 이 비면 chat 단일로 폴백.

    - AE_ENABLE_DAG_PLANNER off(무회귀): 전체 plan 을 단일 Wave 로 fan-out(depends_on 무시).
      기존 동작과 바이트 동등(Req 4.7).
    - on: sanitize_depends_on → topological_waves 로 Wave 를 계산하고 state["completed_waves"]
      (기본 0) 인덱스의 Wave 만 Send. 순환 감지 시 topological_waves 가 단일 Wave 로 폴백(Req 5.2).
      완료 인덱스가 Wave 범위를 벗어나거나, 후속 Wave(>0)인데 선행 컨텍스트(messages)가 부재하면
      빈 Send 로 종료한다(Req 4.5).
    - 어느 경우든 동시 Send 수 ≤ MAX_PARALLEL_TASKS, 각 Send 도메인은 유효 서브그래프 라우트(Req 4.6).
    """
    plan = state.get("plan") or []
    if not plan:
        plan = [{"domain": "chat", "subtask": state.get("prompt", "")}]

    if _env_flag("AE_ENABLE_DAG_PLANNER", default=True):
        waves = topological_waves(sanitize_depends_on(plan))
        completed = state.get("completed_waves", 0) or 0
        # 실행할 Wave 없음(완료 인덱스가 범위 밖) → 빈 Send 로 종료(Req 4.5).
        if completed < 0 or completed >= len(waves):
            return []
        # 후속 Wave 인데 선행 Wave 컨텍스트(messages)가 전달되지 않았으면 종료(Req 4.5).
        if completed > 0 and not (state.get("messages") or []):
            return []
        current = waves[completed]
    else:
        # 무회귀: 전체 plan 을 단일 Wave 로 취급(depends_on 무시).
        current = list(plan)

    base = {k: state.get(k) for k in _WORKER_STATE_KEYS if state.get(k) is not None}

    # ── Send fan-out echo/reset 면역 (다중 Wave 견고성) ──
    # 워커 서브그래프는 동일 GraphState 스키마를 공유하므로, 워커 substate 에 없는 스칼라/리스트
    # 채널은 병합(fan-in) 시 기본값(plan→[], completed_waves→0)으로 echo 되어 부모의 last-wins
    # reducer(_take_right)를 리셋한다. plan 이 [] 로 지워지면 다중 Wave 스케줄이 통째로 소실되고
    # (aggregate 가 len(plan)<=1 로 종합을 스킵, after_aggregate_selector 가 남은 Wave 를 못 봐
    # 조기 종료), completed_waves 가 0 으로 리셋되면 Wave 커서가 되감긴다. 현재 plan 과
    # completed_waves 를 워커 substate 로 함께 전달하면 워커가 같은 값을 그대로 echo 하므로
    # `_take_right(x, x)=x` 로 값이 보존된다(워커 리셋 차단). 이 채널들은 자격증명이 아니라
    # 스케줄 메타데이터이므로 substate 전달이 보안 정책(자격증명 미저장)에 위배되지 않는다.
    # (refine_count 는 monotonic MAX reducer(_take_max_int)로 별도 면역 — graph_state.py.)
    _plan_carry = state.get("plan")
    if _plan_carry is not None:
        base["plan"] = _plan_carry
    base["completed_waves"] = state.get("completed_waves", 0) or 0

    base_msgs = list(state.get("messages") or [])
    sends = []
    for item in current[:MAX_PARALLEL_TASKS]:
        domain = item.get("domain") if item.get("domain") in _SUBGRAPH_ROUTES else "chat"
        sub = item.get("subtask") or state.get("prompt", "")
        worker_state = {
            **base,
            "messages": base_msgs + [HumanMessage(content=sub)],
            "iteration": 0,
            "visited_routes": [domain],
        }
        sends.append(Send(domain, worker_state))
    return sends


# ─────────────────────────────────────────────────────────────────────────────
# Aggregate 종합(synthesis) 노드 (Task 7 / Req 3)
# ─────────────────────────────────────────────────────────────────────────────
# aggregate LLM 개별 호출 상한(초). 스트림 아님(ainvoke). 초과/실패는 비차단({} 반환).
AE_AGGREGATE_TIMEOUT: float = _env_float("AE_AGGREGATE_TIMEOUT", 300.0)

_AGGREGATE_SYSTEM_PROMPT = (
    "너는 여러 도메인 워커의 산출물을 하나의 일관된 최종 답변으로 종합하는 편집자다. "
    "각 워커 결과를 통합해 사용자 원래 요청에 대한 완결된 한국어 답변을 작성한다. "
    "생성된 파일 목록은 보존하되 답변 본문에 자연스럽게 언급한다."
)


def _collect_worker_texts(messages: list, limit: int = 4000) -> str:
    """messages 에서 워커들이 남긴 AIMessage 텍스트를 순서대로 모아 종합 입력으로 구성."""
    parts: List[str] = []
    for m in messages or []:
        if isinstance(m, AIMessage):
            txt = _content_to_text(getattr(m, "content", "")).strip()
            if txt:
                parts.append(txt)
    joined = "\n\n---\n\n".join(parts)
    return joined[:limit]


def _summarize_verified_files(verified_files: list, limit: int = 40) -> str:
    """verified_files 목록을 종합 프롬프트용 텍스트로 요약(path/tool)."""
    lines: List[str] = []
    for vf in (verified_files or [])[:limit]:
        if isinstance(vf, dict):
            path = vf.get("path") or vf.get("absPath") or ""
            tool = vf.get("tool") or ""
            if path:
                lines.append(f"- {path}" + (f" ({tool})" if tool else ""))
    return "\n".join(lines)


def _build_aggregate_prompt(state: GraphState) -> str:
    """원래 요청 + 워커 산출 텍스트 + 생성 파일 목록을 종합 입력으로 구성."""
    parts: List[str] = []
    prompt = state.get("prompt") or ""
    parts.append(f"[원래 요청]\n{prompt}")

    worker_texts = _collect_worker_texts(state.get("messages") or [])
    if worker_texts:
        parts.append(f"[워커 산출물]\n{worker_texts}")

    files_summary = _summarize_verified_files(state.get("verified_files") or [])
    if files_summary:
        parts.append(f"[생성된 파일]\n{files_summary}")

    parts.append("위 산출물을 하나의 일관된 최종 답변으로 종합하라.")
    return "\n\n".join(parts)


def make_aggregate_node(deps: Any):
    """aggregate(fan-in) 노드 — 병렬 워커 산출물을 하나의 일관된 최종 답변으로 종합(Req 3).

    Postcondition:
      - 병렬 워커가 1개면(plan 길이 <=1) LLM 스킵, 기존 결과 통과 {} 반환 (Req 3.7).
      - gateway=None 이면 LLM 스킵, 기존 결과 통과 {} 반환.
      - 워커가 여러 개면 GatewayChatModel(deps.model_generator, 기본 Sonnet)로
        messages+verified_files 요약을 입력해 일관된 final_text 생성 →
        {"final_text":..., "messages":[AIMessage(...)]} 반환 (Req 3.1/3.2/3.3).
      - verified_files 는 입력을 그대로 보존(reducer dedup 병합, 삭제 없음) (Req 3.4/3.6).
      - LLM 실패/타임아웃이면 {} 반환(기존 병합 유지, 비차단) (Req 3.5/3.8).
    Invariant:     ainvoke 개별 await 하나만 asyncio.wait_for(AE_AGGREGATE_TIMEOUT).
                   LLM 호출은 GatewayChatModel(gateway 경유)만. 실패 시 예외 미전파.
    """

    async def aggregate_node(state: GraphState) -> dict:
        # 워커 1개(또는 plan 부재) → 종합 불필요, 기존 결과 통과(Req 3.7).
        plan = state.get("plan") or []
        if len(plan) <= 1:
            _obs_log("reasoning:aggregate", "single worker, skip synthesis")
            return {}

        gateway = getattr(deps, "gateway", None)
        if gateway is None:
            _obs_log("reasoning:aggregate", "no gateway, skip synthesis")
            return {}

        model_id = getattr(deps, "model_generator", None) or _DEFAULT_ROUTER_MODEL
        try:
            llm = GatewayChatModel(gateway=gateway, model_id=model_id)
            messages = [
                SystemMessage(content=_AGGREGATE_SYSTEM_PROMPT),
                HumanMessage(content=_build_aggregate_prompt(state)),
            ]
            ai = await asyncio.wait_for(
                llm.ainvoke(messages), timeout=AE_AGGREGATE_TIMEOUT
            )
        except (asyncio.TimeoutError, GatewayModelError, Exception):
            # 비차단: 기존 messages/verified_files 유지(삭제·변경 없음) (Req 3.5/3.6/3.8).
            # 워커 개수만 로깅(산출물 내용·파일 경로 미로깅).
            _obs_log(
                "reasoning:aggregate",
                f"synthesis failed (non-blocking), preserving {len(plan)} worker outputs",
            )
            return {}

        final_text = _content_to_text(getattr(ai, "content", "")).strip()
        if not final_text:
            _obs_log("reasoning:aggregate", "synthesis empty, preserving worker outputs")
            return {}
        # 종합 성공: 결과 텍스트 길이만 로깅(본문·파일 경로 미로깅).
        _obs_log(
            "reasoning:aggregate",
            f"synthesis ok: {len(plan)} workers merged, final_text len={len(final_text)}",
        )
        return {"final_text": final_text, "messages": [AIMessage(content=final_text)]}

    return aggregate_node


# ─────────────────────────────────────────────────────────────────────────────
# Evaluator 재계획 루프 (Task 8 / Req 1, 2)
# ─────────────────────────────────────────────────────────────────────────────
# Refine_Loop 상한. refine_count 가 이 값에 도달하면 재계획 없이 END(요구사항 2.1/2.2).
AE_MAX_REFINE: int = _env_int("AE_MAX_REFINE", 2)
# evaluator LLM 개별 호출 상한(초). 스트림 아님(ainvoke). 초과/실패는 achieved=True(비차단).
AE_EVALUATOR_TIMEOUT: float = _env_float("AE_EVALUATOR_TIMEOUT", 300.0)

# 평가 강제 스키마 — GatewayChatModel toolChoice 로 단일 구조 강제(Req 1.2).
_EVAL_TOOL: dict = {
    "name": "submit_evaluation",
    "description": "원래 요청 대비 현재 산출물의 달성 여부를 평가한다.",
    "inputSchema": {
        "json": {
            "type": "object",
            "properties": {
                "achieved": {
                    "type": "boolean",
                    "description": "원래 요청이 모두 충족되었는가",
                },
                "reason": {"type": "string", "description": "판정 사유(한국어)"},
                "missing_domains": {
                    "type": "array",
                    "items": {"type": "string", "enum": list(_ROUTE_LABELS)},
                    "description": "미달 시 보완이 필요한 도메인 목록",
                },
            },
            "required": ["achieved"],
        }
    },
}

_EVAL_SYSTEM_PROMPT = (
    "너는 계층적 오케스트레이터의 평가자다. 사용자의 원래 요청과 지금까지 생성된 산출물"
    "(워커 응답, 생성 파일)을 보고 원래 요청이 모두 충족되었는지 판정해 submit_evaluation "
    "도구를 호출한다. 충족되었으면 achieved=true 로, 부족하면 achieved=false 와 함께 보완이 "
    "필요한 도메인(coding/media/research/ops/chat)을 missing_domains 에, 사유를 reason 에 담는다."
)


def _build_eval_prompt(state: GraphState) -> str:
    """원래 요청 + 현재 산출 텍스트 + 생성 파일 목록을 평가 입력으로 구성."""
    parts: List[str] = []
    prompt = state.get("prompt") or ""
    parts.append(f"[원래 요청]\n{prompt}")

    worker_texts = _collect_worker_texts(state.get("messages") or [])
    if worker_texts:
        parts.append(f"[현재 산출물]\n{worker_texts}")

    files_summary = _summarize_verified_files(state.get("verified_files") or [])
    if files_summary:
        parts.append(f"[생성된 파일]\n{files_summary}")

    parts.append(
        "원래 요청이 모두 충족되었으면 achieved=true, 부족하면 achieved=false 와 "
        "missing_domains/reason 을 담아 submit_evaluation 을 호출하라."
    )
    return "\n\n".join(parts)


def _build_refine_instruction(evaluation: dict) -> str:
    """미달 평가 결과를 다음 planner 턴의 교정 지시(HumanMessage)로 변환."""
    missing = evaluation.get("missing_domains") or []
    reason = evaluation.get("reason") or ""
    parts = ["이전 산출물이 원래 요청을 완전히 충족하지 못했다. 부족한 부분을 보완하라."]
    if missing:
        parts.append(f"보완이 필요한 도메인: {', '.join(missing)}")
    if reason:
        parts.append(f"사유: {reason}")
    return "\n".join(parts)


def make_evaluator_node(deps: Any):
    """evaluator 노드 팩토리 → async evaluator_node(state) -> dict (Req 1).

    Postcondition:
      - refine_count >= AE_MAX_REFINE 이면 LLM 호출 없이 achieved=True 반환 → END (Req 2.2).
      - gateway=None 이면 achieved=True 반환 → END(비차단).
      - Evaluator_Model(deps.model_evaluator, 기본 Opus)로 평가. 달성이면
        {"evaluation": {...achieved:True}} → END.
      - 미달 & refine_count < cap 이면 {"evaluation":..., "refine_count": +1,
        "messages":[HumanMessage(교정지시)]} → planner (Req 1.4).
      - 호출 실패/타임아웃이면 achieved=True 간주 → END (Req 1.6/6.6, 비차단).
    Invariant:     LLM 호출은 GatewayChatModel(gateway 경유)만. ainvoke 개별 await 하나만
                   asyncio.wait_for(AE_EVALUATOR_TIMEOUT). refine_count 는 last-wins reducer로
                   echo 증폭 없이 정확히 집계(Req 2.3).
    """

    async def evaluator_node(state: GraphState) -> dict:
        refine_count = state.get("refine_count", 0) or 0
        # 진입 로깅: refine_count/cap 만(요청 내용·messages 원문 미로깅).
        _obs_log(
            "reasoning:evaluator",
            f"enter: refine_count={refine_count}, cap={AE_MAX_REFINE}",
        )

        # Req 2.2: cap 도달 시 LLM 호출 없이 즉시 종료(achieved=True).
        if refine_count >= AE_MAX_REFINE:
            _obs_log("reasoning:evaluator", "cap reached, terminating")
            return {
                "evaluation": {
                    "achieved": True,
                    "reason": "재계획 상한(AE_MAX_REFINE) 도달",
                    "missing_domains": [],
                }
            }

        gateway = getattr(deps, "gateway", None)
        if gateway is None:
            _obs_log("reasoning:evaluator", "no gateway, treating as achieved")
            return {
                "evaluation": {"achieved": True, "reason": "", "missing_domains": []}
            }

        model_id = getattr(deps, "model_evaluator", None) or _DEFAULT_ROUTER_MODEL
        try:
            llm = GatewayChatModel(gateway=gateway, model_id=model_id).bind_tools(
                [_EVAL_TOOL], tool_choice="submit_evaluation"
            )
            messages = [
                SystemMessage(content=_EVAL_SYSTEM_PROMPT),
                HumanMessage(content=_build_eval_prompt(state)),
            ]
            ai = await asyncio.wait_for(
                llm.ainvoke(messages), timeout=AE_EVALUATOR_TIMEOUT
            )
        except (asyncio.TimeoutError, GatewayModelError, Exception):
            # Req 1.6/6.6: 실패/타임아웃 → achieved=True 간주(비차단 종료).
            _obs_log(
                "reasoning:evaluator",
                "eval failed (non-blocking), treating as achieved",
            )
            return {
                "evaluation": {
                    "achieved": True,
                    "reason": "평가 호출 실패 - 비차단 종료",
                    "missing_domains": [],
                }
            }

        evaluation = parse_evaluation(ai, _ROUTE_LABELS)
        if evaluation.get("achieved"):
            # 판정 결과: achieved 값만(사유 전문 미로깅).
            _obs_log("reasoning:evaluator", "verdict: achieved=True")
            return {"evaluation": evaluation}

        # 미달 판정: achieved + missing_domains(도메인 라벨 = 메타데이터, 내용 아님).
        missing = evaluation.get("missing_domains") or []
        _obs_log(
            "reasoning:evaluator",
            f"verdict: achieved=False, missing={missing}",
        )

        # 미달 & refine_count < cap → 교정 지시로 planner 재디스패치, refine_count +1 (Req 1.4).
        _obs_log(
            "reasoning:evaluator",
            f"replanning: refine_count {refine_count}->{refine_count + 1}, missing={missing}",
        )
        return {
            "evaluation": evaluation,
            "refine_count": refine_count + 1,
            "messages": [HumanMessage(content=_build_refine_instruction(evaluation))],
        }

    return evaluator_node


def evaluator_selector(state: GraphState) -> str:
    """conditional edge — evaluation.achieved / refine_count 기준 'planner' | 'done' 반환.

    - achieved=True 또는 refine_count >= AE_MAX_REFINE → "done"(END).
    - achieved=False 이고 refine_count < AE_MAX_REFINE → "planner"(재계획).
    evaluation 미설정 시 안전하게 "done"(비차단 종료).
    """
    evaluation = state.get("evaluation") or {}
    achieved = bool(evaluation.get("achieved", True))
    refine_count = state.get("refine_count", 0) or 0
    if achieved or refine_count >= AE_MAX_REFINE:
        return "done"
    return "planner"


def _make_after_aggregate_selector(evaluator_on: bool):
    """aggregate 이후 라우팅 결정 함수 팩토리(다중 Wave 진행 vs 종료/평가).

    반환 함수 계약:
      - DAG 활성이고 현재 plan 에 아직 dispatch 하지 않은 Wave 가 남았으면
        (completed_waves+1 < 전체 Wave 수) "planner" 를 반환한다 → planner 가
        completed_waves 를 1 증가시켜 다음 Wave 를 dispatch 한다(다중 Wave 순차 실행).
        이 경로는 evaluator refine 루프와 구분된다(설계 결정 A).
      - 남은 Wave 가 없으면(모든 Wave 완료) evaluator_on 이면 "evaluator", 아니면 "done"(END).

    evaluator_on(및 그에 따른 terminal 라벨)은 조립 시점에 캡처하여 반환 라벨이 항상
    add_conditional_edges 의 path_map 부분집합이 되도록 보장한다(무효 라우팅 방지).

    유한 종료: Wave 수 ≤ 서브태스크 수 이므로 planner 복귀는 유한하다(요구사항 4.2/5.4/8.5).
    """
    terminal = "evaluator" if evaluator_on else "done"

    def after_aggregate_selector(state: GraphState) -> str:
        if _env_flag("AE_ENABLE_DAG_PLANNER", default=True):
            plan = state.get("plan") or []
            if plan:
                waves = topological_waves(sanitize_depends_on(plan))
                completed = state.get("completed_waves", 0) or 0
                if completed + 1 < len(waves):
                    return "planner"
        return terminal

    return after_aggregate_selector


def build_parallel_top_graph(deps: Any):
    """planner → (Send fan-out) → 도메인 워커 병렬 → aggregate(fan-in) → [evaluator] → END.

    독립 다중 도메인 요청을 동시에 처리한다(LangGraph Send map-reduce). planner 가 단일
    서브태스크만 만들면 워커 1개로 순차와 동일하게 동작한다. 무회귀를 위해 기존
    build_top_graph(순차 멀티홉)는 그대로 유지되며, 이 그래프는 graph-stream 에서 선택적으로
    사용된다.

    구성(플래그 기본 on):
        START → planner
        conditional(planner, plan_dispatch) → [Send(domain, substate), ...]  (현재 Wave 병렬)
        각 도메인 워커(컴파일된 서브그래프) → aggregate
        conditional(aggregate, after_aggregate_selector):
          · 남은 Wave 있음(DAG on) → planner (다음 Wave dispatch — 다중 Wave 순차 실행)
          · 모든 Wave 완료 + evaluator on → evaluator
          · 모든 Wave 완료 + evaluator off → END
        conditional(evaluator, evaluator_selector) → {planner(재계획), END}

    플래그 조합별 결선(요구사항 6.1~6.4 무회귀):
      - AE_ENABLE_EVALUATOR + AE_ENABLE_DAG_PLANNER 모두 off → aggregate → END(기존 동작 보존).
      - DAG off + evaluator on → 단일 Wave 이므로 aggregate → evaluator 직접 edge.
      - DAG on → aggregate conditional 로 다중 Wave 진행 루프 결선(evaluator 는 완료 후 실행).

    유한 종료: Wave 진행 복귀는 Wave 수(≤ 서브태스크 수) 이하, refine 복귀는 refine_count
    cap(AE_MAX_REFINE) 이하로 각각 제한된다. fan-out 은 MAX_PARALLEL_TASKS 로, 각 워커
    서브그래프는 자체 iteration cap 으로 유한 종료한다(요구사항 2.4/4.2/5.4/8.5).
    """
    g = StateGraph(GraphState)

    g.add_node("planner", make_planner_node(deps))
    g.add_node("coding", build_coding_subgraph(deps))
    g.add_node("media", build_media_subgraph(deps))
    g.add_node("research", build_research_subgraph(deps))
    g.add_node("ops", build_ops_subgraph(deps))
    g.add_node("chat", build_chat_subgraph(deps))
    g.add_node("aggregate", make_aggregate_node(deps))

    g.add_edge(START, "planner")
    g.add_conditional_edges("planner", plan_dispatch, list(_SUBGRAPH_ROUTES))
    for name in _SUBGRAPH_ROUTES:
        g.add_edge(name, "aggregate")

    # ── Evaluator 재계획 루프 + 다중 Wave 진행 루프 결선(요구사항 1.1 / 6.1~6.4) ──
    # 플래그는 조립 시점에 1회 읽어 그래프 '구조'를 결정한다(무회귀: 둘 다 off 면 기존
    # aggregate→END 와 노드/엣지 집합이 동일). server.py 조립 분기 및 AE_LANGGRAPH /
    # AE_LANGGRAPH_PARALLEL 계약은 변경하지 않는다.
    evaluator_on = _env_flag("AE_ENABLE_EVALUATOR", default=True)
    dag_on = _env_flag("AE_ENABLE_DAG_PLANNER", default=True)

    if evaluator_on:
        g.add_node("evaluator", make_evaluator_node(deps))

    if dag_on:
        # DAG 활성: aggregate 이후 남은 Wave 가 있으면 planner 로 복귀(다중 Wave 순차 실행),
        # 모든 Wave 완료 시 evaluator(활성) 또는 END 로 진행.
        agg_path: dict = {"planner": "planner", "done": END}
        if evaluator_on:
            agg_path["evaluator"] = "evaluator"
        g.add_conditional_edges(
            "aggregate", _make_after_aggregate_selector(evaluator_on), agg_path
        )
    elif evaluator_on:
        # DAG 비활성 + evaluator 활성: 단일 Wave → aggregate 이후 곧바로 evaluator.
        g.add_edge("aggregate", "evaluator")
    else:
        # 둘 다 비활성: 기존 동작 보존(무회귀) — aggregate → END.
        g.add_edge("aggregate", END)

    if evaluator_on:
        # evaluator conditional: 미달 & refine_count < cap 이면 planner 재계획, 아니면 END.
        g.add_conditional_edges(
            "evaluator", evaluator_selector, {"planner": "planner", "done": END}
        )

    compile_kwargs: dict = {}
    checkpointer = getattr(deps, "checkpointer", None)
    if checkpointer is not None:
        compile_kwargs["checkpointer"] = checkpointer
    store = getattr(deps, "store", None)
    if store is not None:
        compile_kwargs["store"] = store
    return g.compile(**compile_kwargs)
