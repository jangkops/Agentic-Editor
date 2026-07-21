"""Depth_Router — 질의 복잡도 휴리스틱 (Phase 2a).

이 모듈은 사용자 질의를 `simple` / `complex` 로 분류하기 위한 **순수 휴리스틱**을 제공한다.
Fast_Path(단일 도메인 서브그래프) 대상은 `simple`, Full_Graph(planner→workers→aggregate→
evaluator) 대상은 `complex` 다.

불변 제약(요구사항 11):
- 이 모듈은 `boto3`/`anthropic`/`openai` 를 직접 import 하지 않는다. LLM 호출이 필요한 확인
  경로(`classify_complexity`, task 4.4)는 GatewayChatModel 경유 전용이며 이 파일의 순수
  휴리스틱과 분리된다.
- 재사용 대상(`server._is_code_related`, `server._infer_file_intent_from_prompt`)의 import·
  호출은 try/except 로 감싸고, 실패·불확실은 **보수적으로 complex 쪽**(신호 True)으로 처리한다
  (요구사항 4.3 fail-safe).

이 파일은 task 4.1 범위(complexity_signals + classify_heuristic)만 구현한다.
`classify_complexity`(async, task 4.4), `pick_fast_domain`/`build_fast_path_graph`(task 4.5)는
후속 태스크에서 추가한다.
"""

from __future__ import annotations

import asyncio
import os
from typing import Any

# 다도메인 판정을 도와주는 접속 표현. 여러 작업이 연쇄되면 복잡 질의일 가능성이 높다.
_CONJUNCTION_PATTERNS = (
    "그리고",
    "그 결과로",
    "그런 다음",
    "그다음",
    "그후",
    "그 후",
    "이어서",
    "그리고 나서",
    "그리고나서",
    "및 ",
    " and then ",
)

# 명시적 근거/조사/출처 요구 — needs_evidence 신호. 존재 시 복잡으로 간주한다.
_EVIDENCE_KEYWORDS = (
    "근거",
    "출처",
    "조사",
    "왜",
    "분석",
    "이유",
    "레퍼런스",
    "reference",
    "citation",
    "source",
    "evidence",
    "investigate",
    "research",
)

# 셸/검색 도구 사용을 강하게 시사하는 키워드 — needs_tool 신호 보완.
_TOOL_KEYWORDS = (
    "검색",
    "찾아",
    "실행",
    "명령어",
    "터미널",
    "쉘",
    "셸",
    "shell",
    "command",
    "terminal",
    "search",
    "run ",
    "grep",
    "설치",
    "install",
)

# 미디어/문서 도메인 키워드(파일 생성 의도 추론이 실패했을 때의 보수적 보완).
_MEDIA_KEYWORDS = (
    "pptx",
    "ppt",
    "파워포인트",
    "프레젠테이션",
    "발표자료",
    "슬라이드",
    "deck",
    "pdf",
    "보고서",
    "리포트",
    "report",
    "xlsx",
    "엑셀",
    "excel",
    "docx",
    "워드",
    "이미지",
    "image",
    "png",
    "그림",
)

# 길이 임계 — _is_code_related 의 200자 관례와 정합. 초과 시 장문(복잡 가능성) 신호.
_LONG_THRESHOLD = 200


def _safe_is_code_related(prompt: str) -> bool:
    """server._is_code_related 재사용(비차단). import·호출 실패 시 보수적으로 True.

    코드 관련 여부가 불확실하면(예외) complex 쪽으로 기울도록 True 를 반환한다.
    """
    try:
        from ai_engine.server import _is_code_related

        return bool(_is_code_related(prompt))
    except Exception:
        # 불확실 → 보수적(복잡 쪽): 도메인 신호로 취급될 수 있도록 True.
        return True


def _safe_file_intent_wanted(prompt: str) -> bool:
    """server._infer_file_intent_from_prompt 재사용(비차단). 실패 시 보수적으로 True.

    Returns:
        파일/미디어 생성 의도 감지 여부(wanted). import·호출 실패는 True(복잡 쪽)로 폴백.
    """
    try:
        from ai_engine.server import _infer_file_intent_from_prompt

        _primary_tool, wanted, _target = _infer_file_intent_from_prompt(prompt, "", "")
        return bool(wanted)
    except Exception:
        # 불확실 → 보수적(복잡 쪽).
        return True


def complexity_signals(prompt: str) -> dict:
    """휴리스틱 복잡도 신호를 집계한다(순수 함수).

    재사용: `server._is_code_related`, `server._infer_file_intent_from_prompt`(둘 다 try/except
    로 감싸며 실패는 보수적 complex 처리).

    Returns:
        {
          "multi_domain": bool,   # 여러 도메인 키워드 동시 등장 또는 접속 표현
          "needs_tool": bool,     # 파일/미디어 생성 의도 또는 셸/검색 요구
          "needs_evidence": bool, # 명시적 근거/조사/출처 요구
          "long": bool,           # 길이 임계 초과
        }
    """
    p = (prompt or "")
    p_lower = p.lower().strip()

    # ── 도메인 집합 판정(multi_domain) ──
    domains: set[str] = set()
    code_related = _safe_is_code_related(p)
    file_wanted = _safe_file_intent_wanted(p)
    if code_related:
        domains.add("code")
    if file_wanted:
        domains.add("media")
    # 파일 의도 추론이 놓친 미디어/문서 키워드를 보완(별도 도메인 근거로 취급).
    if any(kw in p_lower for kw in _MEDIA_KEYWORDS):
        domains.add("media")
    if any(kw in p_lower for kw in _EVIDENCE_KEYWORDS):
        domains.add("research")

    has_conjunction = any(c in p for c in _CONJUNCTION_PATTERNS)
    multi_domain = (len(domains) >= 2) or has_conjunction

    # ── 도구 필요(needs_tool) ──
    needs_tool = file_wanted or any(kw in p_lower for kw in _TOOL_KEYWORDS)

    # ── 근거 요구(needs_evidence) ──
    needs_evidence = any(kw in p_lower for kw in _EVIDENCE_KEYWORDS)

    # ── 장문(long) ──
    long = len(p_lower) > _LONG_THRESHOLD

    return {
        "multi_domain": bool(multi_domain),
        "needs_tool": bool(needs_tool),
        "needs_evidence": bool(needs_evidence),
        "long": bool(long),
    }


def classify_heuristic(prompt: str) -> str:
    """복잡도 신호 중 하나라도 complex 이면 'complex', 아니면 'simple'(요구사항 4.2).

    반환값은 항상 {'simple', 'complex'} 중 하나다(예외 전파 없음 — 신호 집계 자체가 비차단).
    """
    signals = complexity_signals(prompt)
    if any(signals.values()):
        return "complex"
    return "simple"


# ─────────────────────────────────────────────────────────────────────────────
# classify_complexity(async) — Gateway LLM 확인(옵션) + wait_for (task 4.4)
# ─────────────────────────────────────────────────────────────────────────────
# Reasoning 메타 노드 기본 모델(Sonnet 계열). Opus 는 스트리밍 미지원이므로 사용하지 않는다
# (요구사항 11.6). supervisor._DEFAULT_ROUTER_MODEL 와 동일 계열로 정합화.
_DEFAULT_SONNET_MODEL = "us.anthropic.claude-sonnet-4-5-20250929-v1:0"

# 분류 결과 유효 집합 — 반환값은 항상 이 둘 중 하나다(Property 2).
_VALID_DEPTH = ("simple", "complex")


# ── 플래그/타임아웃 판독 헬퍼(호출 시점 판독 — 테스트 토글 허용) ──
def _env_flag(name: str, default: bool) -> bool:
    """불리언 env 플래그 판독. 미설정 시 default. 값은 호출 시점에 읽는다.

    off 로 해석되는 값: "0", "false", "no", "off", "" (대소문자 무시). 그 외는 on.
    """
    raw = os.environ.get(name)
    if raw is None:
        return default
    return raw.strip().lower() not in ("0", "false", "no", "off", "")


def _env_float(name: str, default: float) -> float:
    """실수 env 판독. 파싱 실패 시 default(비차단)."""
    try:
        return float(os.environ.get(name, str(default)))
    except (TypeError, ValueError):
        return default


def adaptive_depth_enabled() -> bool:
    """적응형 깊이 라우팅 플래그(기본 off — 요구사항 10.1)."""
    return _env_flag("AE_ENABLE_ADAPTIVE_DEPTH", False)


def depth_router_llm_enabled() -> bool:
    """Depth_Router LLM 확인 사용 플래그(기본 off). classify_complexity 의 use_llm 기본과 별개로,
    server 배선에서 LLM 확인을 켤지 결정하는 호출-시점 플래그."""
    return _env_flag("AE_DEPTH_ROUTER_LLM", False)


def depth_router_timeout() -> float:
    """Depth_Router LLM 개별 ainvoke 상한(초, 기본 60 — 요구사항 4.5)."""
    return _env_float("AE_DEPTH_ROUTER_TIMEOUT", 60.0)


def _make_depth_tool() -> dict:
    """select_depth 도구 스키마(toolChoice 강제 — 단일 라벨 안정 확보).

    GatewayChatModel.bind_tools 입력으로 쓰이는 Bedrock toolSpec dict.
    """
    return {
        "name": "select_depth",
        "description": (
            "사용자 질의의 처리 복잡도를 정확히 하나 선택한다. "
            "simple: 단일 도메인·도구 불필요·근거 조사 불필요한 짧은 질의(빠른 단일 서브그래프로 처리 가능). "
            "complex: 다도메인 요구, 도구 사용 필요, 명시적 근거·조사 요구, 장문 등 "
            "풀 그래프(planner→workers→aggregate→evaluator)가 필요한 질의. "
            "불확실하면 complex 를 선택한다."
        ),
        "inputSchema": {
            "json": {
                "type": "object",
                "properties": {
                    "depth": {
                        "type": "string",
                        "enum": list(_VALID_DEPTH),
                        "description": "선택한 복잡도 라벨",
                    },
                },
                "required": ["depth"],
            }
        },
    }


def _extract_depth_from_ai(ai: Any) -> str | None:
    """LLM 응답에서 유효 depth 라벨을 추출한다. 없으면 None(불확실).

    1) toolChoice 강제 스키마(tool_calls) 우선, 2) 응답 텍스트에서 라벨 파싱.
    """
    # 1) toolChoice 강제 스키마 응답
    tool_calls = getattr(ai, "tool_calls", None) or []
    for tc in tool_calls:
        args = tc.get("args") if isinstance(tc, dict) else None
        if isinstance(args, dict):
            label = args.get("depth")
            if isinstance(label, str) and label.strip().lower() in _VALID_DEPTH:
                return label.strip().lower()

    # 2) 텍스트 라벨 파싱 폴백
    content = getattr(ai, "content", "")
    if isinstance(content, list):
        content = "".join(
            p.get("text", "") if isinstance(p, dict) else str(p) for p in content
        )
    if isinstance(content, str):
        low = content.lower()
        # 'complex' 를 'simple' 보다 우선 확인(보수적).
        if "complex" in low:
            return "complex"
        if "simple" in low:
            return "simple"
    return None


async def classify_complexity(prompt: str, deps: Any, *, use_llm: bool = False) -> str:
    """질의를 'simple' / 'complex' 로 분류한다(요구사항 4.1/4.3/4.4/4.5).

    절차:
      1. 휴리스틱 우선(`classify_heuristic`). 'complex' 면 즉시 'complex' 반환(LLM 미호출).
      2. 휴리스틱이 'simple' AND `use_llm=True` 이고 gateway 가 있으면, GatewayChatModel
         (Sonnet, prefer_streaming=True).bind_tools(select_depth, toolChoice) 로 1회 확인.
      3. 개별 `ainvoke` await **하나만** `asyncio.wait_for(AE_DEPTH_ROUTER_TIMEOUT)`(기본 60초)로
         감싼다. 스트림 소비 루프(async for)는 감싸지 않는다(API_NOTES CRITICAL 2).
      4. TimeoutError / GatewayModelError / 기타 예외 → 'complex' fail-safe(요구사항 4.3).
         LLM 응답이 불확실(유효 라벨 없음)해도 보수적으로 'complex'.

    Returns:
        항상 {'simple', 'complex'} 중 하나(예외 전파 없음 — Property 2).

    Invariant:
        - 신규 LLM 호출은 GatewayChatModel(gateway 경유)만(요구사항 11.1). boto3/anthropic/
          openai 직접 import 없음.
        - Reasoning 메타 노드는 Sonnet 계열 사용(Opus 스트리밍 미지원 — 요구사항 11.6).
    """
    # ── 1) 휴리스틱 우선 ──
    heuristic = classify_heuristic(prompt)
    if heuristic == "complex":
        return "complex"

    # ── 2) simple & use_llm → Gateway LLM 1회 확인 ──
    if not use_llm:
        return "simple"

    gateway = getattr(deps, "gateway", None)
    if gateway is None:
        # gateway 부재 → LLM 확인 불가. 휴리스틱 simple 결과를 그대로 신뢰(비차단).
        return "simple"

    model_id = getattr(deps, "model_coding", None) or _DEFAULT_SONNET_MODEL

    try:
        # 지연 import — 순수 휴리스틱 경로(use_llm=False)에서는 어댑터를 건드리지 않는다.
        # GatewayModelError 는 Exception 하위형이라 아래 except Exception 이 모두 포섭한다
        # (asyncio.TimeoutError 포함). 예외 미전파를 보장한다(Property 2).
        from ai_engine.agent_system.chat_model_adapter import GatewayChatModel
        from langchain_core.messages import HumanMessage, SystemMessage

        llm = GatewayChatModel(
            gateway=gateway, model_id=model_id, prefer_streaming=True
        ).bind_tools([_make_depth_tool()], tool_choice="select_depth")
        messages = [
            SystemMessage(
                content=(
                    "너는 질의 복잡도 분류기다. select_depth 도구로 정확히 하나의 라벨을 "
                    "선택한다. 불확실하면 complex 를 선택한다."
                )
            ),
            HumanMessage(content=(prompt or "")),
        ]
        # 개별 await 하나만 wait_for 로 감싼다(스트림 아님).
        ai = await asyncio.wait_for(
            llm.ainvoke(messages), timeout=depth_router_timeout()
        )
    except Exception:
        # 실패/타임아웃(TimeoutError/GatewayModelError 포함) → fail-safe(요구사항 4.3).
        return "complex"

    label = _extract_depth_from_ai(ai)
    if label in _VALID_DEPTH:
        return label
    # 불확실(유효 라벨 없음) → 보수적 complex(요구사항 4.3).
    return "complex"


# ─────────────────────────────────────────────────────────────────────────────
# Fast_Path — 단일 도메인 선택 + 얇은 top 그래프 조립 (task 4.5)
# ─────────────────────────────────────────────────────────────────────────────
# Fast_Path 는 단순(simple) 질의를 planner·병렬 워커 fan-out·aggregate·evaluator 메타 노드
# **없이** 단일 도메인 서브그래프 하나만으로 처리한다(요구사항 5.1/5.2). 이 도메인 서브그래프는
# Full_Graph(build_parallel_top_graph)의 워커와 **완전히 동일한 빌더**로 만든다 — 즉 도메인별
# tools / model_id / with_retrieve / domain 인자가 정상 워커와 바이트 동등하게 배선된다
# (요구사항 5.3/11.4). 서브그래프 내부 계약(retrieve→model→(tools)→verify, SUBGRAPH_RECURSION
# _LIMIT 이내 유한 종료)과 SSE 계약(on_chain_start/on_chain_end 이 도메인/서브그래프명을 실음)은
# Full_Graph 와 동일하게 유지된다.

# Fast_Path 가 얹을 수 있는 도메인 서브그래프 노드 라벨. supervisor._SUBGRAPH_ROUTES 와 동일
# 순서·집합으로, Full_Graph 워커 노드명과 정합한다(Property 3 구조 테스트가 노드 집합을
# introspect 할 때 planner/aggregate/evaluator/fan-out 과 교집합이 공집합이어야 함).
_FAST_PATH_DOMAINS = ("coding", "media", "research", "ops", "chat")

# 도메인 판정 불확실/실패 시의 보수적 폴백 도메인. chat 은 도구가 없어 유한 종료가 자명하다.
_FAST_PATH_DEFAULT_DOMAIN = "chat"


def pick_fast_domain(prompt: str, deps: Any = None) -> str:
    """단순 질의를 처리할 **단일 도메인** 라벨을 결정한다(요구사항 5.1).

    `supervisor._heuristic_route` 를 재사용해 파일/미디어 생성 의도 → 'media', 코드 관련 →
    'coding', 그 외 → 'chat' 로 라우팅한다(server 휴리스틱과 정합). import·호출 실패는 보수적
    으로 폴백 도메인('chat')으로 처리한다(비차단).

    Args:
        prompt: 사용자 질의 텍스트.
        deps:   시그니처 일관성을 위한 GraphDeps(현재 휴리스틱 라우팅에는 사용하지 않음).

    Returns:
        _FAST_PATH_DOMAINS 중 하나(항상 유효 도메인 라벨). 예외 전파 없음.
    """
    try:
        # 지연 import — 순환 import 회피. _heuristic_route 는 state dict 를 받으므로 최소 상태를
        # 구성해 전달한다(prompt 만 사용).
        from ai_engine.agent_system.supervisor import _heuristic_route

        domain = _heuristic_route({"prompt": prompt or ""})
        if domain in _FAST_PATH_DOMAINS:
            return domain
    except Exception:
        # 불확실/실패 → 보수적 폴백(비차단).
        pass
    return _FAST_PATH_DEFAULT_DOMAIN


def _build_fast_domain_subgraph(deps: Any, domain: str):
    """단일 도메인 서브그래프를 `build_domain_subgraph`(_common) 로 직접 조립한다.

    Full_Graph 워커 빌더(`build_coding_subgraph`/`build_media_subgraph`/…)가 `build_domain_
    subgraph` 에 넘기는 인자(tools / model_id / with_retrieve / domain)를 **동일하게** 재현한다
    (research/ops 는 deps.mcp_tools 병합, chat 은 tools=None+with_retrieve=True). 이로써 Fast_Path
    워커가 Full_Graph 워커와 바이트 동등하게 배선된다(요구사항 5.3). 도메인 서브그래프 프리미티브
    자체(retrieve→model→(tools)→verify, SUBGRAPH_RECURSION_LIMIT)는 수정하지 않고 **호출만** 한다.

    지연 import 로 순환 import 를 회피한다(depth_router 는 server 에서 import 되고, subgraphs
    모듈은 _common → grounding_gate 등을 import 한다).
    """
    from ai_engine.agent_system.subgraphs._common import build_domain_subgraph
    from ai_engine.agent_system.subgraphs.coding import CODING_TOOLS
    from ai_engine.agent_system.subgraphs.media import MEDIA_TOOLS
    from ai_engine.agent_system.subgraphs.ops import OPS_TOOLS
    from ai_engine.agent_system.subgraphs.research import RESEARCH_TOOLS

    model_id = deps.model_coding
    mcp = list(getattr(deps, "mcp_tools", None) or [])

    if domain == "coding":
        return build_domain_subgraph(
            deps, tools=CODING_TOOLS, model_id=model_id, domain="coding"
        )
    if domain == "media":
        return build_domain_subgraph(
            deps, tools=MEDIA_TOOLS, model_id=model_id, domain="media"
        )
    if domain == "research":
        # research 워커와 동일하게 MCP 도구를 병합.
        return build_domain_subgraph(
            deps, tools=RESEARCH_TOOLS + mcp, model_id=model_id, domain="research"
        )
    if domain == "ops":
        # ops 워커와 동일하게 MCP 도구를 병합.
        return build_domain_subgraph(
            deps, tools=OPS_TOOLS + mcp, model_id=model_id, domain="ops"
        )
    # chat(및 알 수 없는 라벨의 폴백): 도구 없음 + retrieve 유지(chat 워커와 동일).
    return build_domain_subgraph(
        deps, tools=None, model_id=model_id, with_retrieve=True, domain="chat"
    )


def build_fast_path_graph(deps: Any, domain: str):
    """단일 도메인 서브그래프를 얇은 top StateGraph 위에 얹어 compile 한다(요구사항 5.1/5.2/5.3).

    구성:
        START → <domain> → END

    - `<domain>` 노드는 `build_domain_subgraph`(subgraphs/_common.py)로 만든 컴파일된 단일 도메인
      서브그래프다. tools / model_id / with_retrieve / domain 인자가 Full_Graph(build_parallel_
      top_graph)의 정상 워커와 바이트 동등하게 배선된다(요구사항 5.3).
    - planner / Send fan-out / aggregate / evaluator 노드를 **일절 추가하지 않는다**(요구사항 5.2).
      노드 집합은 정확히 `{domain}` 하나다(Property 3).
    - 노드명은 도메인 라벨(supervisor._SUBGRAPH_ROUTES 와 정합)이라 SSE 계약(on_chain_start/
      on_chain_end 이 도메인/서브그래프명을 실음)이 Full_Graph 와 동일하게 유지된다(요구사항 5.4).
    - checkpointer / store 는 최상위 그래프 compile 에서만 주입한다(API_NOTES 항목 6). 서브그래프는
      이를 상속한다.

    Args:
        deps:   GraphDeps (gateway / model_* / checkpointer / store). 도메인 서브그래프에 전달.
        domain: 얹을 단일 도메인 라벨(coding/media/research/ops/chat). 유효하지 않으면 'chat'
                으로 폴백한다(비차단).

    Returns:
        CompiledStateGraph — START→domain→END 단일 도메인 그래프.

    Invariant:
        - 신규 노드는 정확히 도메인 서브그래프 하나뿐. {planner, aggregate, evaluator} 및 fan-out
          디스패치 노드와 교집합 공집합(Property 3).
        - 도메인 서브그래프 내부에서 model 왕복은 기존 계약(도구 없으면 1회, 도구 사용 시
          SUBGRAPH_RECURSION_LIMIT 이내)으로 유한 종료(요구사항 5.3/11.4, Property 4).
        - `build_parallel_top_graph`·`build_domain_subgraph` 내부는 수정하지 않고 호출만 한다.
    """
    # 지연 import — 순환 import 회피(depth_router 는 server 에서 import 된다).
    from ai_engine.agent_system.graph_state import GraphState
    from langgraph.graph import END, START, StateGraph

    dom = domain if domain in _FAST_PATH_DOMAINS else _FAST_PATH_DEFAULT_DOMAIN
    # Full_Graph 워커와 동일 인자로 build_domain_subgraph 를 직접 호출해 조립.
    domain_subgraph = _build_fast_domain_subgraph(deps, dom)

    g = StateGraph(GraphState)
    # 단일 도메인 서브그래프를 노드로 add (graph-of-graphs, API_NOTES 항목 6). 노드명은 도메인
    # 라벨이라 SSE on_chain_start/on_chain_end 가 Full_Graph 워커와 동일한 이름을 실는다.
    g.add_node(dom, domain_subgraph)
    g.add_edge(START, dom)
    g.add_edge(dom, END)

    # ── compile: checkpointer + store 는 최상위에서만 주입(API_NOTES 항목 6). ──
    compile_kwargs: dict = {}
    checkpointer = getattr(deps, "checkpointer", None)
    if checkpointer is not None:
        compile_kwargs["checkpointer"] = checkpointer
    store = getattr(deps, "store", None)
    if store is not None:
        compile_kwargs["store"] = store
    return g.compile(**compile_kwargs)
