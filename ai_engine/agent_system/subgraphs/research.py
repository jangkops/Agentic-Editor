"""research 서브그래프 — 웹/문서 리서치·요약 도메인.

`_common.build_domain_subgraph` 를 재사용하고 research 도메인의 도구 집합만
바인딩한다(요구사항 1.6). 서브그래프 구성(retrieve → model → tools → verify)은
coding 과 동일하며, RESEARCH_TOOLS(읽기/검색 전용) 도구 집합만 다르다.

RESEARCH_TOOLS 구성:
- `read_file` / `search_files` — 로컬 프로젝트 파일 조회(기존).
- `web_search` / `search_papers` / `fetch_content` — 외부 리서치 조회 도구(축 A)
  (Task 10.1, design.md "8) 도구 통합"). 각 도구의 실행(조립)은 이 파일의 조립
  함수(`run_web_search` / `run_search_papers` / `run_fetch_content`)가 research
  백엔드(단일 외부 egress)+normalize(파서/프린터)+rank(정렬/융합)로만 수행한다.
  옵트인/동의가 off 면 외부 호출 없이 빈 결과를 비차단 반환한다(무회귀 — P15).
- `deep_research` — 멀티스텝 딥리서치 도구(축 B) (Task 14.1, design.md "8) 도구 통합").
  조립 함수(`run_deep_research_tool`)가 축 B 파이프라인 동기 seam
  (`deep_research.run_deep_research_sync`)을 호출해 인용 포함 Research_Report 를 종합한다.
  리포트 파일(userData 하위 report.json)을 생성하므로 결과 dict 최상위 `path` 로 그 경로를
  실어 `GatewayToolNode` 가 디스크 실측 후 verified_files 에 포함하게 한다(요구사항 17.5).

⚠️ 도구 name 정합성(요구사항 17.4):
- 모든 도구 name 은 server.py `_execute_tool` 디스패치와 동일 문자열로 일치해야 한다.
- read_file / search_files 는 server.py `_execute_tool` 이 이미 이 이름으로 디스패치한다.
- web_search / search_papers / fetch_content(Task 10.2)와 deep_research(Task 14.1)의
  `_execute_tool` 디스패치는 `RESEARCH_TOOL_EXECUTORS` 매핑으로 라우팅되며, 본 파일이 도구
  스키마 + 조립 함수 + 실행기 매핑을 제공한다(name 을 이 매핑 키로 단일 소스화).
"""

from __future__ import annotations

from typing import Any, List

from ai_engine.agent_system.subgraphs._common import build_domain_subgraph


# ─────────────────────────────────────────────────────────────────────────────
# RESEARCH_TOOLS — 읽기/검색 중심(부작용 없는 조회). name 은 server.py 실측과 일치.
# ─────────────────────────────────────────────────────────────────────────────
RESEARCH_TOOLS: List[dict] = [
    {
        "name": "read_file",
        "description": "프로젝트 내 파일의 내용을 읽어 반환한다.",
        "inputSchema": {
            "json": {
                "type": "object",
                "properties": {
                    "path": {
                        "type": "string",
                        "description": "읽을 파일의 프로젝트 상대 경로",
                    }
                },
                "required": ["path"],
            }
        },
    },
    {
        "name": "search_files",
        "description": "프로젝트 내에서 텍스트/패턴에 일치하는 위치를 검색한다.",
        "inputSchema": {
            "json": {
                "type": "object",
                "properties": {
                    "query": {
                        "type": "string",
                        "description": "검색할 텍스트 또는 정규식 패턴",
                    },
                    "path": {
                        "type": "string",
                        "description": "검색 범위를 한정할 하위 경로(선택)",
                    },
                    "file_pattern": {
                        "type": "string",
                        "description": "파일 패턴(예: *.py, *.md) — 선택",
                    },
                },
                "required": ["query", "path"],
            }
        },
    },
    # ── 외부 조회 도구 3종 (Task 10.1) — design.md "8) 도구 통합" 정합 ──
    # name 은 server._execute_tool 디스패치(Task 10.2)·RESEARCH_TOOL_EXECUTORS 키와
    # 동일 문자열로 일치한다(요구사항 17.4). 실제 egress·조립은 아래 조립 함수가
    # backend(단일 egress)+normalize+rank 로만 수행하며, 옵트인/동의 off 시 외부
    # 호출이 없다(무회귀 — P15). 파일 산출물이 없어 verified_files 대상이 아니다.
    {
        "name": "web_search",
        "description": (
            "외부 웹을 검색해 최신·외부 지식을 가져온다. 로컬 프로젝트 파일에 없는 "
            "정보 조사에 사용한다. 결과는 제목·URL·발췌문·발행일·출처도메인·관련성 "
            "점수를 갖는 항목 목록이다. (외부 리서치 옵트인·동의가 활성일 때만 외부 "
            "호출하며, 비활성이면 빈 결과를 비차단 반환한다.)"
        ),
        "inputSchema": {
            "json": {
                "type": "object",
                "properties": {
                    "query": {
                        "type": "string",
                        "description": "웹 검색 질의(앞뒤 공백 제거 후 비어있지 않아야 함)",
                    },
                    "top_k": {
                        "type": "integer",
                        "description": "반환 결과 상한(선택, 미지정 시 서버 설정 기본값)",
                    },
                    "recency_days": {
                        "type": "integer",
                        "description": (
                            "최신성 창(일). 지정하면 해당 일수 이내 발행 결과만 남겨 "
                            "발행일 내림차순으로 반환한다(선택)."
                        ),
                    },
                },
                "required": ["query"],
            }
        },
    },
    {
        "name": "search_papers",
        "description": (
            "학술·논문 데이터베이스를 검색해 논문 결과를 가져온다. 각 결과는 제목·"
            "저자 목록·발행연도·게재처·초록·DOI 또는 URL·피인용수를 갖는다. 근거 "
            "기반의 기술적·학술적 조사에 사용한다. (외부 리서치 옵트인·동의가 활성일 "
            "때만 외부 호출하며, 비활성이면 빈 결과를 비차단 반환한다.)"
        ),
        "inputSchema": {
            "json": {
                "type": "object",
                "properties": {
                    "query": {
                        "type": "string",
                        "description": "논문 검색 질의(앞뒤 공백 제거 후 비어있지 않아야 함)",
                    },
                    "top_k": {
                        "type": "integer",
                        "description": "반환 결과 상한(선택, 미지정 시 서버 설정 기본값)",
                    },
                },
                "required": ["query"],
            }
        },
    },
    {
        "name": "fetch_content",
        "description": (
            "http/https URL의 본문 텍스트를 Python 백엔드에서 조회·추출한다(개별 "
            "타임아웃·본문 크기 상한 적용). 검색 결과의 원문 근거를 발췌문이 아닌 "
            "본문으로 확보할 때 사용한다. 조회 실패·타임아웃은 예외 없이 ok=false 로 "
            "반환한다. (외부 리서치 옵트인·동의가 활성일 때만 외부 호출한다.)"
        ),
        "inputSchema": {
            "json": {
                "type": "object",
                "properties": {
                    "url": {
                        "type": "string",
                        "description": "조회할 소스 URL(http 또는 https 스킴만 허용)",
                    }
                },
                "required": ["url"],
            }
        },
    },
    # ── 딥리서치 도구 (Task 14.1, 축 B) — design.md "8) 도구 통합" 정합 ──
    # name "deep_research" 는 server._execute_tool 디스패치(Task 14.1)·RESEARCH_TOOL_EXECUTORS
    # 키와 동일 문자열로 일치한다(요구사항 17.4). 조립 함수(run_deep_research_tool)가 축 B
    # 파이프라인 동기 seam(deep_research.run_deep_research_sync)을 호출해 인용 포함
    # Research_Report 를 종합하고, 그 산출물을 JSON 호환 dict 로 환원한다. web/academic/fetch
    # 3종과 달리 리포트 파일(userData 하위 report.json)을 생성하므로, 결과 dict 최상위 `path`
    # 키로 그 경로를 실어 GatewayToolNode 가 디스크 실측 후 verified_files 에 포함하게 한다
    # (요구사항 17.5). 옵트인/동의 off 여도 파이프라인은 "외부 근거 미확보" 리포트로 비차단
    # 종료하며 외부 호출을 하지 않는다(무회귀 — P15).
    {
        "name": "deep_research",
        "description": (
            "복잡한 질문을 여러 하위 질의로 분해하고 다중 소스(웹·논문)를 조사·종합해 "
            "인용 포함 리서치 리포트를 생성하는 멀티스텝 딥리서치를 수행한다. 단일 검색으로 "
            "얻기 어려운 깊이 있는 조사가 필요할 때 사용한다. 결과에는 종합 본문"
            "(report_markdown), 검증/미검증 인용 분류, 품질 지표(metrics), 저장된 리포트 "
            "파일 경로(path)가 포함된다. (외부 리서치 옵트인·동의가 활성일 때만 외부 호출하며, "
            "비활성이면 '외부 근거 미확보' 리포트로 비차단 종료한다.)"
        ),
        "inputSchema": {
            "json": {
                "type": "object",
                "properties": {
                    "query": {
                        "type": "string",
                        "description": "딥리서치 조사 질의(앞뒤 공백 제거 후 비어있지 않아야 함)",
                    },
                    "session_id": {
                        "type": "string",
                        "description": (
                            "리서치 세션 식별자(선택). 산출물 폴더명·리포트 경로 구성에 "
                            "사용하며, 미지정 시 무작위로 생성한다."
                        ),
                    },
                },
                "required": ["query"],
            }
        },
    },
]


# ─────────────────────────────────────────────────────────────────────────────
# 읽기 전용 외부 조회 도구 3종 — 다른 도메인 워커에도 병합하기 위한 부분집합.
#
# ⚠️ 실측 사고: planner LLM 이 "GLP-1 간독성 논문을 검색해서 DOI와 함께 제시해줘"를
# coding/ops 로 라우팅했고, 그 워커에는 web_search/search_papers 가 없었다. 모델은
# 도구가 없다고 정확히 인지한 뒤 `run_command` 로 curl 을 실행해 우회했다. 결과는
# 나왔지만 (a) 옵트인·동의 게이트를 완전히 우회하고 (b) 캐시·레이트리밋·인용 검증
# 파이프라인을 전부 건너뛴다.
#
# 라우팅 정확도를 LLM 판단에 의존하는 대신, **게이트를 통과하는 조회 도구를 coding
# 워커에도 제공**해 "라우팅이 어긋나면 외부 조회가 아예 불가능"한 실패 모드를 없앤다.
# 셋 다 부작용이 없는 읽기 전용이며 옵트인·동의 off 면 빈 결과를 비차단 반환한다.
# deep_research 는 무겁고 자체 파이프라인을 가지므로 research 워커 전용으로 남긴다.
_LOOKUP_TOOL_NAMES = ("web_search", "search_papers", "fetch_content")

RESEARCH_LOOKUP_TOOLS: List[dict] = [
    t for t in RESEARCH_TOOLS if t.get("name") in _LOOKUP_TOOL_NAMES
]


def build_research_subgraph(deps: Any):
    """research 서브그래프를 조립해 compiled Runnable 을 반환.

    구성은 coding 과 동일한 ReAct 루프(retrieve → model → tools → verify)이며 도구 집합만
    RESEARCH_TOOLS(읽기/검색 전용)로 다르다. model_id 는 deps.model_coding(sonnet-4-5).
    """
    # MCP 도구(있으면)를 research 도구에 병합 — 웹/문서 조회(aws-documentation 등)에 적합.
    mcp = list(getattr(deps, "mcp_tools", None) or [])
    return build_domain_subgraph(
        deps,
        tools=RESEARCH_TOOLS + mcp,
        model_id=deps.model_coding,
        domain="research",
    )


# ═════════════════════════════════════════════════════════════════════════════
# 조회 도구 조립 로직 (Task 10.1) — web_search / search_papers / fetch_content
# ═════════════════════════════════════════════════════════════════════════════
# design.md "8) 도구 통합" 정합. 위 RESEARCH_TOOLS 에 추가한 3개 조회 도구의 실제
# 실행(조립)을 제공한다. 각 도구는 research 백엔드(단일 외부 egress)+normalize(파서/
# 프린터)+rank(정렬/융합)만 조립해 JSON 직렬화 가능한 결과 dict 를 만든다. egress·LLM
# 은 이 파일에서 수행하지 않으며(요구사항 10.4), backend 가 옵트인/동의 게이트·개별
# 타임아웃·비차단 폴백을 모두 책임진다(P8/P15). 무거운·네트워크 의존(httpx 등)은 함수
# 내부 지연 import 로 로딩해 `import ai_engine.agent_system.subgraphs.research` 가
# 가볍고 부작용 없이 유지되도록 한다(deep_research.py 의 지연 import 철학 계승).
#
# 반환 계약: 조립 함수는 dict 를 반환한다. 도구 결과 문자열화(json.dumps)와
# server._execute_tool 디스패치 연결은 Task 10.2 가 담당한다. 도구 name 은
# RESEARCH_TOOLS 스키마와 아래 RESEARCH_TOOL_EXECUTORS 키로 단일 소스화되어
# _execute_tool 디스패치와 항상 동일 문자열로 일치한다(요구사항 17.4).

# 원시 제공자 응답에서 결과 항목 배열이 실리는 컨테이너 경로(first-match).
# normalize._web_envelope / _academic_envelope · providers.py 어댑터의 컨테이너
# 규약을 그대로 반영한다(제공자 응답은 이 중 정확히 하나만 채우므로 first-match 가
# 곧 정확 추출이며, 구조화 오류 dict 는 어느 것도 채우지 않아 빈 목록이 된다 — P8).
_WEB_ITEM_PATHS = (("results",), ("web", "results"))
_ACADEMIC_ITEM_PATHS = (
    ("data",),                                            # Semantic Scholar
    ("resultList", "result"),                             # Europe PMC (keyless)
    ("results",),                                         # OpenAlex
    ("entries",), ("feed", "entry"),                      # arXiv
    ("articles",), ("PubmedArticleSet", "PubmedArticle"),  # PubMed
)


def _dig(node: Any, path) -> Any:
    """중첩 dict 경로를 안전하게 탐색한다(각 단계가 dict 가 아니면 ``None``)."""
    for key in path:
        if not isinstance(node, dict):
            return None
        node = node.get(key)
    return node


def _collect_raw_items(raw: Any, paths) -> List[dict]:
    """원시 제공자 응답에서 결과 항목 리스트를 first-match 로 추출한다(순수·방어적).

    ``providers._result_items`` 규약을 계승한다: 후보 경로를 순서대로 시도해 처음
    발견되는 list 를 반환하고, 단일 dict 는 ``[dict]`` 로 감싼다. 컨테이너가 없거나
    (구조화 오류 dict 등) ``raw`` 가 dict 가 아니면 빈 리스트를 반환한다(비차단 — P8).
    """
    if not isinstance(raw, dict):
        return []
    for path in paths:
        node = _dig(raw, path)
        if isinstance(node, list):
            return node
        if isinstance(node, dict):
            return [node]
    return []


# ─────────────────────────────────────────────────────────────────────────────
# 제공자 실패 사유 집계 — "왜 결과가 0건인지"를 도구 결과에 실어 보낸다.
#
# backend 는 실패를 예외 없이 구조화 오류 dict 로 돌려주지만(P8), 조립 단계가
# `_collect_raw_items` 로 결과 컨테이너만 뽑아내면 그 사유가 사라진다. 그러면 도구
# 결과가 `{results: [], count: 0}` 이 되어 **키 미설정과 "검색해봤지만 없음"이
# 구별되지 않는다.** 모델은 원인을 모른 채 "웹 검색을 할 수 없다"고 답하고, UI
# 인디케이터도 정상 완료로 보인다(tool_node 는 예외가 없으면 status="ok").
#
# 그래서 제공자별 시도 결과를 모아, 결과가 0건이고 모든 시도가 실패했을 때만
# `error`/`detail`/`provider_errors` 를 덧붙인다. 자격증명 값은 담지 않는다(P9).
# 게이트 off 는 기존대로 `disabled: True` 로 표현하며 이 경로를 타지 않는다(무회귀).
# ─────────────────────────────────────────────────────────────────────────────

# 사용자가 조치할 수 있는 형태의 한국어 사유. backend 의 error code → 설명.
_PROVIDER_ERROR_HINTS = {
    "missing_credential": "API 키가 설정되지 않아 호출하지 못했습니다",
    "timeout": "응답이 제한 시간 내에 오지 않았습니다",
    "http_error": "제공자가 오류 상태를 반환했습니다(레이트리밋·권한 등)",
    "request_error": "제공자에 연결하지 못했습니다(네트워크·DNS)",
    "unknown_provider": "지원하지 않는 제공자 이름입니다",
    "web_research_disabled": "외부 리서치가 비활성 상태입니다",
    "invalid_query": "질의가 비어 있거나 너무 깁니다",
}


def _provider_error(raw: Any):
    """제공자 원시 응답이 구조화 오류면 그 code, 정상이면 ``None``(순수)."""
    if not isinstance(raw, dict):
        return "request_error"
    code = raw.get("error")
    return code if isinstance(code, str) and code else None


def _attach_provider_errors(out: dict, attempts: List[dict]) -> dict:
    """결과 0건 + 전 제공자 실패일 때만 사유를 ``out`` 에 덧붙인다(그 외 무변경).

    ``attempts`` 는 ``{"provider": name, "error": code|None}`` 목록이다. 한 곳이라도
    성공했거나 결과가 있으면 아무것도 붙이지 않는다 — "검색은 됐지만 결과 없음"과
    "검색 자체가 불가"를 구분하기 위한 것이므로, 전자에 오류를 달면 안 된다.

    집계 규칙:
      - 모든 실패가 같은 code 면 그 code 를 대표값으로 쓴다(예: 전부 키 미설정).
      - 섞여 있으면 ``no_external_evidence``.
    """
    if out.get("count"):
        return out
    failed = [a for a in attempts if a.get("error")]
    if not attempts or len(failed) != len(attempts):
        return out  # 시도 없음 또는 일부 성공 → 사유 미부착

    codes = {a["error"] for a in failed}
    out["error"] = codes.pop() if len(codes) == 1 else "no_external_evidence"
    out["provider_errors"] = [
        {"provider": a.get("provider", ""), "error": a.get("error", "")}
        for a in failed
    ]
    hint = _PROVIDER_ERROR_HINTS.get(out["error"], "제공자 호출이 모두 실패했습니다")
    names = ", ".join(a.get("provider", "") for a in failed)
    out["detail"] = f"{names}: {hint}"
    return out


def _positive_int_or_none(value: Any):
    """양의 정수면 그 값, 아니면 ``None``(형식 오류·0·음수 포함)."""
    try:
        v = int(value)
    except (TypeError, ValueError):
        return None
    return v if v > 0 else None


def _resolve_top_k(value: Any, default: int) -> int:
    """top_k 를 양의 정수로 정규화한다(형식 오류·0·음수 → 기본값)."""
    v = _positive_int_or_none(value)
    return v if v is not None else default


def _run_coro_sync(coro):
    """async 조립 코루틴을 동기 맥락에서 안전하게 실행한다.

    ``rag.retrieval_pipeline.retrieve_evidence_sync`` 와 동일한 seam 패턴: 실행 중인
    이벤트 루프가 있으면 별도 스레드의 독립 루프에서 돌려 "실행 중 루프 내 asyncio.run"
    안티패턴을 피한다. ``_execute_tool`` 은 ``GatewayToolNode`` 가 ``asyncio.to_thread``
    로 분리 실행하므로 대개 실행 중 루프가 없지만(그 경우 ``asyncio.run`` 직행), 양쪽을
    모두 방어한다(비차단).
    """
    import asyncio

    try:
        asyncio.get_running_loop()
        running = True
    except RuntimeError:
        running = False

    if not running:
        return asyncio.run(coro)

    import concurrent.futures

    with concurrent.futures.ThreadPoolExecutor(max_workers=1) as ex:
        return ex.submit(lambda: asyncio.run(coro)).result()


def _load_config(config, env):
    """``DeepResearchConfig`` 를 해석한다(주입 우선, 없으면 env 로딩 — 지연 import)."""
    if config is not None:
        return config
    from ai_engine.research.config import DeepResearchConfig

    return DeepResearchConfig.from_env(env)


async def _assemble_web_search(tool_input: dict, config, env) -> dict:
    """web_search 조립: backend.web_search_raw → parse_search_result → rank (요구사항 1).

    config 웹 제공자별로 원시 검색(단일 egress) → 정규 파싱 → 제공자 내 관련성 정렬
    한다. 제공자가 둘 이상이면 ``merge_and_rerank`` 로 RRF 융합·중복제거하고(옵션),
    하나면 ``sort_by_relevance_web`` 로 관련성 내림차순 정렬한다(P7 / Req 1.4·1.5).
    ``recency_days`` 가 주어지면 ``apply_recency`` 로 최신성 창 필터 + 발행일 내림차순
    정렬한다(P12 / Req 9.2). 옵트인/동의 off 면 외부 호출 없이 빈 결과를 반환한다(P15).
    """
    from ai_engine.research import backend, normalize, rank

    raw_q = (tool_input or {}).get("query", "")
    query = raw_q.strip() if isinstance(raw_q, str) else ""
    providers_list = list(config.web_providers)
    out = {"kind": "web", "query": query, "providers": providers_list,
           "results": [], "count": 0}

    if not query:
        out["error"] = "invalid_query"
        return out
    if not backend.web_research_enabled(env):
        # 옵트인/동의 off → 외부 호출 없이 빈 결과(무회귀 — P15 / Req 10.3·14.2).
        out["disabled"] = True
        return out

    top_k = _resolve_top_k((tool_input or {}).get("top_k"), config.top_k)
    recency_days = _positive_int_or_none((tool_input or {}).get("recency_days"))
    recency_hint = str(recency_days) if recency_days else None

    per_provider: List[list] = []
    union: list = []
    attempts: List[dict] = []
    for provider in providers_list:
        raw = await backend.web_search_raw(
            provider, query, top_k=top_k,
            timeout=config.search_timeout, recency=recency_hint,
        )
        # 실패 사유를 파싱 전에 기록한다 — `_collect_raw_items` 는 오류 dict 를 빈
        # 목록으로 흡수하므로 여기서 잡지 않으면 사유가 사라진다.
        attempts.append({"provider": provider, "error": _provider_error(raw)})
        parsed = [
            normalize.parse_search_result(provider, item)
            for item in _collect_raw_items(raw, _WEB_ITEM_PATHS)
        ]
        parsed = rank.sort_by_relevance_web(parsed)
        if parsed:
            per_provider.append(parsed)
            union.extend(parsed)

    if len(per_provider) > 1:
        # 다중 제공자: RRF 융합 + 중복제거(옵션 merge_and_rerank). 점수 스케일이 다른
        # 제공자 결과를 순위 기반으로 견고히 융합한다(설계 근거). gw 미주입 → 결정적.
        ranked = rank.merge_and_rerank(query, per_provider, union)
    else:
        ranked = rank.sort_by_relevance_web(union)

    if recency_days:
        ranked = rank.apply_recency(
            ranked, window=recency_days, unknown_rule=config.recency_unknown
        )

    ranked = ranked[:top_k]
    out["results"] = [normalize.serialize_result(r) for r in ranked]
    out["count"] = len(out["results"])
    return _attach_provider_errors(out, attempts)


async def _assemble_search_papers(tool_input: dict, config, env) -> dict:
    """search_papers 조립: backend.academic_search_raw → parse_paper_result → rank (요구사항 2).

    config 학술 제공자별로 원시 검색(단일 egress) → 정규 파싱 → 제공자 내 정렬한다.
    제공자가 둘 이상이면 ``merge_and_rerank`` 로 융합·중복제거하고(제공자 간 점수는
    비교 불가하므로 RRF 순위 융합이 정합), 하나면 ``sort_by_relevance_papers`` 로 관련성
    내림차순 → 피인용수 내림차순 정렬한다(P7 / Req 2.3·2.4). 옵트인/동의 off 면 외부
    호출 없이 빈 결과를 반환한다(P15).
    """
    from ai_engine.research import backend, normalize, rank

    raw_q = (tool_input or {}).get("query", "")
    query = raw_q.strip() if isinstance(raw_q, str) else ""
    providers_list = list(config.academic_providers)
    out = {"kind": "academic", "query": query, "providers": providers_list,
           "results": [], "count": 0}

    if not query:
        out["error"] = "invalid_query"
        return out
    if not backend.web_research_enabled(env):
        out["disabled"] = True
        return out

    top_k = _resolve_top_k((tool_input or {}).get("top_k"), config.top_k)

    per_provider: List[list] = []
    union: list = []
    attempts: List[dict] = []
    for provider in providers_list:
        raw = await backend.academic_search_raw(
            provider, query, top_k=top_k, timeout=config.search_timeout,
        )
        attempts.append({"provider": provider, "error": _provider_error(raw)})
        parsed = [
            normalize.parse_paper_result(provider, item)
            for item in _collect_raw_items(raw, _ACADEMIC_ITEM_PATHS)
        ]
        parsed = rank.sort_by_relevance_papers(parsed)
        if parsed:
            per_provider.append(parsed)
            union.extend(parsed)

    if len(per_provider) > 1:
        ranked = rank.merge_and_rerank(query, per_provider, union)
    else:
        ranked = rank.sort_by_relevance_papers(union)

    ranked = ranked[:top_k]
    out["results"] = [normalize.serialize_result(r) for r in ranked]
    out["count"] = len(out["results"])
    return _attach_provider_errors(out, attempts)


async def _assemble_fetch_content(tool_input: dict, config, env) -> dict:
    """fetch_content 조립: backend.fetch_url_raw → 본문 dict (요구사항 3).

    http/https URL 본문을 단일 egress(``fetch_url_raw``)로 조회·추출한다. 옵트인/동의
    게이트·스킴 검증·개별 타임아웃·본문 크기 상한·비차단 폴백은 모두 backend 가
    책임진다(P8/P15). 조회 실패·타임아웃·옵트인 off 는 예외 없이 ``ok=False`` 로
    표현되므로 이 조립은 그 결과를 JSON 호환 dict 로 환원만 한다.
    """
    from ai_engine.research import backend

    raw_url = (tool_input or {}).get("url", "")
    url = raw_url.strip() if isinstance(raw_url, str) else ""
    if not url:
        return {"url": "", "text": "", "chars": 0, "truncated": False,
                "ok": False, "error": "invalid_url"}

    fr = await backend.fetch_url_raw(
        url, timeout=config.fetch_timeout, max_chars=config.fetch_max_chars
    )
    return {
        "url": fr.url,
        "text": fr.text,
        "chars": fr.chars,
        "truncated": fr.truncated,
        "ok": fr.ok,
        "error": fr.error,
    }


# ── 도구 실행기 공통 시그니처 (Task 18.1 — 게이트웨이 배선) ──────────────────────
# 모든 실행기는 `(tool_input, *, deps=None, config=None, env=None)` 로 통일한다. server.
# _execute_tool 디스패치가 GatewayToolNode 의 deps(GraphDeps: gateway/model_*/checkpointer/
# store 보유)를 `_executor(tool_input, deps=deps)` 로 균일 전달하기 위함이다(Task 14.1 이
# 지적한 배선 갭 해소). deps 를 실제로 소비하는 것은 축 B(딥리서치)뿐이다:
#   - run_deep_research_tool: deps → run_deep_research_sync(query, deps, ...) 로 전달해
#     Planner/Generator 가 Bedrock Gateway 를 실제로 사용하게 한다(요구사항 6/10.1).
#   - 축 A 경량 조회 도구(web_search/search_papers/fetch_content)는 deps 를 받되 **결정적**
#     조립(RRF+신뢰도 융합, gw=None)을 유지한다 — 도구 호출마다 LLM 재랭킹을 강제하지 않아
#     "가벼운 외부 조사"(design 축 A) 성격과 저지연을 보존한다. deps 는 균일 디스패치를 위해
#     받으며, 향후 LLM 재랭킹 옵트인 배선 지점이다(현재 동작 불변 — 무회귀).


def run_web_search(tool_input: dict, *, deps=None, config=None, env=None) -> dict:
    """web_search 도구 동기 진입점 — server._execute_tool 디스패치 대상.

    ``deps`` 는 균일 디스패치용으로 받되 사용하지 않는다(축 A 경량 조회는 결정적 융합
    유지 — 위 배선 주석 참조). 실제 게이트웨이 소비는 딥리서치(축 B)뿐이다.
    """
    return _run_coro_sync(_assemble_web_search(tool_input, _load_config(config, env), env))


def run_search_papers(tool_input: dict, *, deps=None, config=None, env=None) -> dict:
    """search_papers 도구 동기 진입점 — server._execute_tool 디스패치 대상.

    ``deps`` 는 균일 디스패치용으로 받되 사용하지 않는다(축 A 경량 조회는 결정적 융합 유지).
    """
    return _run_coro_sync(_assemble_search_papers(tool_input, _load_config(config, env), env))


def run_fetch_content(tool_input: dict, *, deps=None, config=None, env=None) -> dict:
    """fetch_content 도구 동기 진입점 — server._execute_tool 디스패치 대상.

    ``deps`` 는 균일 디스패치용으로 받되 사용하지 않는다(본문 수집은 순수 egress).
    """
    return _run_coro_sync(_assemble_fetch_content(tool_input, _load_config(config, env), env))


def run_deep_research_tool(tool_input: dict, *, deps=None, config=None, env=None) -> dict:
    """deep_research 도구 동기 진입점 — server._execute_tool(Task 14.1) 디스패치 대상 (요구사항 5/17).

    축 B(딥리서치) 파이프라인을 동기 실행 seam(``deep_research.run_deep_research_sync`` —
    "실행 중 루프면 별도 스레드+독립 루프")으로 호출해 인용 포함 ``ResearchReport`` 를 종합
    하고, 그 산출물을 JSON 직렬화 가능한 dict 로 환원한다. 다른 조회 도구(web/academic/fetch)
    와 시그니처를 일치시킨다(``tool_input`` + 선택 ``config``/``env``).

    verified_files 배선(요구사항 17.5): 파이프라인은 리포트를 ``userData/research/{session}/
    report.json`` 에 저장하므로, 그 경로를 ``deep_research_report_path(session_id)`` 로 재구성해
    (동일 session_id 사용) 결과 dict 최상위 ``path`` 키로 싣는다. 파일이 디스크에 실제 존재할
    때만 ``path`` 를 노출하며, ``GatewayToolNode._verify_files`` 가 이 ``path`` 를 다시 디스크
    실측(isfile+size>0)해 verified_files 에 포함한다. 파일 미존재/저장 실패면 ``path`` 는 ""로
    두어 추출·실측에서 자연히 제외된다(비차단).

    다운스트림 활용(프론트 인용 표기): 결과 dict 에 종합 본문(``report_markdown``), 인용 분류
    (``citations`` = {verified, unverified})와 그 개수(``verified_count``/``unverified_count``),
    미검증 인용 비율(``unverified_ratio``), 품질 지표(``metrics``)를 함께 싣는다. 또한 Task 13.2 가
    병합한 근거성 메타데이터(``answer_quality``)를 프론트 ``answerQuality`` SSE 규약과 동일 형태
    ({citation:{citations_total,verified,unverified}, grounding?:{score}, faithfulness?, ...})로
    노출해 research-panel 인용/미검증/근거성 표기가 가능하게 한다. graph-stream 경로에서 이 dict 를
    실제 ``answerQuality`` SSE 이벤트로 방출하는 배선은 Task 18.1(최종 통합 배선) 범위이며(신규 SSE
    채널·CSP 변경 없음), 여기서는 도구 결과 JSON 에 그 데이터를 담는 것까지를 책임진다.

    Invariant:
        - 예외를 전파하지 않는다(파이프라인 자체가 비차단이나, 방어적으로 감싸 구조화 오류
          dict 로 환원 — P8). 옵트인/동의 off 여도 외부 호출 없이 리포트로 비차단 종료(P15).
        - 무거운·LLM 의존(deep_research 파이프라인)은 함수 내부 지연 import 로 로딩해
          모듈 import 를 가볍게 유지한다(다른 조립 함수의 지연 import 철학 계승).
    """
    import os
    import uuid
    from dataclasses import asdict, is_dataclass

    from ai_engine.research.deep_research import (
        deep_research_report_path,
        run_deep_research_sync,
    )

    cfg = _load_config(config, env)

    raw_q = (tool_input or {}).get("query", "")
    query = raw_q.strip() if isinstance(raw_q, str) else ""

    out: dict = {"kind": "deep", "query": query, "path": ""}
    if not query:
        # 빈/공백 질의 → 제공자·파이프라인 미실행, 구조화 오류(비차단 — 요구사항 1.8/P8).
        out["error"] = "invalid_query"
        return out

    # 세션 식별자: 명시 주입 우선(경로 재구성 정합), 없으면 무작위 생성. 산출물 경로를
    # 재구성하려면 run_deep_research_sync 에 넘긴 것과 동일 session_id 를 써야 한다
    # (run_deep_research_sync 는 빈 session_id 면 내부에서 무작위 생성하므로 반드시 주입).
    raw_sid = (tool_input or {}).get("session_id", "")
    session_id = (
        raw_sid.strip()
        if isinstance(raw_sid, str) and raw_sid.strip()
        else uuid.uuid4().hex
    )

    # deps 배선(Task 18.1): server._execute_tool 이 GatewayToolNode 의 deps(GraphDeps —
    # gateway/model_planner/model_generator/checkpointer/store 보유)를 여기로 전달한다. 이를
    # run_deep_research_sync 에 넘겨 Planner/Generator 가 Bedrock Gateway 를 실제로 사용하게
    # 한다(요구사항 6.1~6.3 / 10.1). deps 가 None 이면(예: 게이트웨이 미배선 진입점) 파이프
    # 라인은 결정적 폴백으로 비차단 종합한다(요구사항 13.3 — 무회귀 안전). deps 는 GraphDeps
    # 유사 객체 또는 게이트웨이 클라이언트를 모두 수용한다(deep_research._resolve_gateway_*).
    try:
        report = run_deep_research_sync(
            query, deps, session_id=session_id, config=cfg
        )
    except Exception as exc:  # noqa: BLE001 — 비차단: 구조화 오류로 환원(P8).
        out["error"] = "deep_research_failed"
        out["detail"] = str(exc)[:300]
        return out

    # 리포트 파일 경로 재구성(동일 session_id) — 디스크 실존 시에만 path 노출(요구사항 17.5/P10).
    try:
        report_path = deep_research_report_path(session_id)
        if report_path and os.path.isfile(report_path):
            out["path"] = report_path
    except Exception:  # noqa: BLE001 — 경로 재구성 실패는 비차단(path="" 유지).
        pass

    # 인용 요약 + 품질 지표를 결과 dict 로 환원(다운스트림 인용 표기·품질 게이트용).
    citations = getattr(report, "citations", None) or {}
    verified = list(citations.get("verified") or [])
    unverified = list(citations.get("unverified") or [])
    out["report_markdown"] = getattr(report, "report_markdown", "") or ""
    out["citations"] = {"verified": verified, "unverified": unverified}
    out["verified_count"] = len(verified)
    out["unverified_count"] = len(unverified)
    try:
        out["unverified_ratio"] = float(getattr(report, "unverified_ratio", 0.0) or 0.0)
    except (TypeError, ValueError):
        out["unverified_ratio"] = 0.0
    out["deepening_count"] = getattr(report, "deepening_count", 0)
    out["created_at"] = getattr(report, "created_at", "") or ""

    metrics = getattr(report, "metrics", None)
    out["metrics"] = asdict(metrics) if is_dataclass(metrics) else {}

    # 근거성 메타데이터(Task 13.2) — 프론트 answerQuality SSE 규약과 동일 형태로 노출한다:
    # {citation:{citations_total,verified,unverified}, unverified_ratio, grounding?:{score},
    #  faithfulness?:{...}, grounding_gate?:{...}}. graph-stream 경로에서 이 dict 를 answerQuality
    # 이벤트로 방출하는 SSE 배선은 Task 18.1 범위이며(신규 채널 없음), 여기서는 데이터만 싣는다.
    aq = getattr(report, "answer_quality", None)
    out["answer_quality"] = aq if isinstance(aq, dict) else {}
    return out


# 도구 name → 동기 실행기 매핑. 키는 RESEARCH_TOOLS 스키마 name 과 단일 소스로 일치하며,
# server._execute_tool 디스패치가 이 매핑으로 도구를 라우팅한다(name 정합 — 요구사항 17.4).
# web_search/search_papers/fetch_content 는 Task 10.2, deep_research 는 Task 14.1 에서 배선한다.
# 각 실행기는 dict 를 반환하고, 결과 문자열화는 _execute_tool 이 수행한다(호출당 ToolMessage
# 1개 — 요구사항 17.3). deep_research 는 리포트 파일 경로를 `path` 로 실어 GatewayToolNode 가
# verified_files 로 디스크 실측하게 한다(요구사항 17.5).
RESEARCH_TOOL_EXECUTORS = {
    "web_search": run_web_search,
    "search_papers": run_search_papers,
    "fetch_content": run_fetch_content,
    "deep_research": run_deep_research_tool,
}
