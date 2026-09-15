#!/usr/bin/env python3
"""외부 조사 요청이 도구 있는 워커로 가고, 도구 사용 지침을 받는지 회귀 테스트.

── 배경(실측 증상) ────────────────────────────────────────────────────────
단일 모드로 "GLP-1 수용체 작용제의 간독성 관련 최신 보고를 찾아서, 각 근거의 DOI 또는
URL을 반드시 함께 제시해줘"라고 물으면, 모델이 **"…대조하겠습니다. …붙이겠습니다"라는
조사 계획만 출력하고 종료**했다. web_search/search_papers 가 한 번도 호출되지 않았다.

원인은 두 겹이었다.
  (1) **라우팅**: planner 스키마·시스템 프롬프트에 도메인 의미 설명이 없어(라벨 5개만
      노출) 조사 요청이 `chat` 으로 갈 수 있었고, LLM 실패 시 폴백(`_heuristic_route`)은
      무조건 `chat` 이었다. `chat` 서브그래프는 ``tools=None`` 이라 도구 노드 자체가
      생성되지 않아(`_common.build_domain_subgraph`) 검색이 **물리적으로 불가능**하다.
  (2) **지침**: 모델에 들어가는 유일한 시스템 프롬프트(`rag.context_builder`)는 "AI 코딩
      어시스턴트" 페르소나로 로컬/파일 도구만 열거하고 web_search 를 한 번도 언급하지
      않았다. 게다가 `project_path` 가 없으면 SystemMessage 자체가 없었다(완전 무지시).

── 이 테스트가 고정하는 성질 ──────────────────────────────────────────────
  A. 조사형 프롬프트의 폴백 라우팅이 `chat` 이 아니다(= 도구 있는 워커)
  B. 기존 라우팅(코드/미디어/일반대화)은 무회귀
  C. planner 스키마·시스템 프롬프트에 도메인 의미 설명이 실려 있다
  D. 시스템 프롬프트가 리서치 도구를 실제로 언급한다
  E. project_path 가 없어도 research 워커는 도구 사용 지침을 받는다

네트워크·게이트웨이를 쓰지 않는다(순수 함수/문자열 검증 + 가짜 deps).
"""
import asyncio
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from ai_engine.agent_system import supervisor  # noqa: E402
from ai_engine.agent_system.nodes.retrieve import make_retrieve_node  # noqa: E402

# 사용자가 실제로 보고한 프롬프트.
_REPORTED_PROMPT = (
    "GLP-1 수용체 작용제의 간독성 관련 최신 보고를 찾아서, 각 근거의 DOI 또는 URL을 "
    "반드시 함께 제시해줘. 논문과 웹 자료를 모두 확인하고, 출처 없이 서술하지 마."
)

# 도구가 없는 도메인 — 여기로 가면 검색이 물리적으로 불가능하다.
_TOOLLESS = "chat"

_RESEARCH_TOOL_NAMES = ("web_search", "search_papers", "fetch_content", "deep_research")


# ─────────────────────────────────────────────────────────────────────────────
# A. 조사형 프롬프트는 도구 없는 chat 으로 떨어지지 않는다
# ─────────────────────────────────────────────────────────────────────────────
def test_reported_prompt_does_not_route_to_toolless_chat() -> None:
    """보고된 프롬프트가 research 로 라우팅된다(chat 이면 검색 불가)."""
    got = supervisor._heuristic_route({"prompt": _REPORTED_PROMPT})
    assert got != _TOOLLESS, (
        f"조사 요청이 도구 없는 {_TOOLLESS!r} 로 라우팅됨 — web_search 호출 불가"
    )
    assert got == "research", f"research 기대, 실제 {got!r}"


def test_research_intent_variants_route_to_research() -> None:
    """검색·출처·논문·최신 신호가 있는 프롬프트는 research 로 간다."""
    prompts = [
        "최신 LangGraph 버전 알아봐줘",
        "PubMed에서 semaglutide 논문 검색해줘",
        "이 주장의 출처를 찾아줘",
        "관련 문헌 조사해서 인용과 함께 정리해줘",
        "DOI 붙여서 근거 제시해줘",
        "search recent papers about GLP-1 hepatotoxicity",
    ]
    for p in prompts:
        got = supervisor._heuristic_route({"prompt": p})
        assert got == "research", f"{p!r} → {got!r} (research 기대)"


def test_research_hint_detector_is_pure_and_case_insensitive() -> None:
    """`_looks_like_research` 는 대소문자 무시 부분일치이며 예외를 던지지 않는다."""
    assert supervisor._looks_like_research("DOI 알려줘") is True
    assert supervisor._looks_like_research("doi 알려줘") is True
    assert supervisor._looks_like_research("SEARCH the web") is True
    assert supervisor._looks_like_research("안녕") is False
    # 방어적 입력
    assert supervisor._looks_like_research("") is False
    assert supervisor._looks_like_research(None) is False


# ─────────────────────────────────────────────────────────────────────────────
# B. 기존 라우팅 무회귀
# ─────────────────────────────────────────────────────────────────────────────
def test_existing_routing_unchanged() -> None:
    """코드/미디어/일반대화 라우팅은 그대로다."""
    cases = [
        ("이 파일의 버그를 고쳐줘", "coding"),
        ("main.js의 renderMessages 함수 설명해줘", "coding"),
        ("발표자료 슬라이드 만들어줘", "media"),
        ("pptx로 정리해줘", "media"),
        ("오늘 기분이 어때?", "chat"),
    ]
    for prompt, expect in cases:
        got = supervisor._heuristic_route({"prompt": prompt})
        assert got == expect, f"{prompt!r} → {got!r} ({expect!r} 기대)"


def test_media_intent_wins_over_research_hint() -> None:
    """파일 생성 의도가 조사 신호보다 우선한다(순서 계약)."""
    # "보고서"(strong_map) + "조사"(research hint) 동시 — media 가 먼저다.
    got = supervisor._heuristic_route({"prompt": "조사 내용을 보고서 pdf로 만들어줘"})
    assert got == "media", f"media 기대, 실제 {got!r}"


# ─────────────────────────────────────────────────────────────────────────────
# C. planner 가 도메인 의미를 본다
# ─────────────────────────────────────────────────────────────────────────────
def test_planner_schema_carries_domain_guide() -> None:
    """select_plan 스키마 description 에 도메인 의미 설명이 실려 있다."""
    desc = supervisor._PLAN_TOOL["description"]
    assert "research:" in desc, "planner 스키마에 research 도메인 설명이 없음"
    assert "웹 검색" in desc or "논문" in desc
    for label in ("coding:", "media:", "ops:", "chat:"):
        assert label in desc, f"planner 스키마에 {label} 설명이 없음"


def test_planner_system_prompt_carries_domain_guide() -> None:
    """planner 시스템 프롬프트도 같은 설명을 담고, chat 의 한계를 명시한다."""
    sp = supervisor._PLANNER_SYSTEM_PROMPT
    assert "research:" in sp, "planner 프롬프트에 research 설명이 없음"
    # 애매할 때 도구 없는 chat 으로 도피하지 않도록 하는 지시.
    assert "chat" in sp and "도구가 없어" in sp


def test_domain_guide_is_single_source() -> None:
    """스키마와 시스템 프롬프트가 같은 _DOMAIN_GUIDE 를 공유한다(문구 분기 방지)."""
    guide = supervisor._DOMAIN_GUIDE
    assert guide in supervisor._PLAN_TOOL["description"]
    assert guide in supervisor._PLANNER_SYSTEM_PROMPT


# ─────────────────────────────────────────────────────────────────────────────
# D. 시스템 프롬프트가 리서치 도구를 언급한다
# ─────────────────────────────────────────────────────────────────────────────
def test_project_system_prompt_mentions_research_tools() -> None:
    """`build_system_prompt` 산출물이 리서치 도구 4종을 언급한다."""
    import inspect

    from ai_engine.rag import context_builder

    src = inspect.getsource(context_builder.build_system_prompt)
    for name in _RESEARCH_TOOL_NAMES:
        assert name in src, f"시스템 프롬프트에 {name} 언급이 없음"
    # 출처 조작 금지 지시가 함께 있어야 한다(환각 DOI 방지).
    assert "지어내지 마세요" in src


# ─────────────────────────────────────────────────────────────────────────────
# E. project_path 가 없어도 research 워커는 지침을 받는다
# ─────────────────────────────────────────────────────────────────────────────
class _Deps:
    """retrieve 노드가 참조하는 최소 deps(게이트웨이·스토어 없음)."""

    gateway = None
    store = None


def _run_retrieve(domain: str, state: dict) -> dict:
    node = make_retrieve_node(_Deps(), domain=domain)
    return asyncio.run(node(state))


def test_research_worker_gets_tool_guidance_without_project() -> None:
    """project_path 가 없어도 research 워커의 system_prompt 에 도구 지침이 실린다."""
    out = _run_retrieve("research", {"prompt": _REPORTED_PROMPT, "project_path": ""})
    sp = out.get("system_prompt") or ""
    assert sp, "research 워커가 무지시 상태(system_prompt 없음)"
    for name in ("web_search", "search_papers"):
        assert name in sp, f"지침에 {name} 언급이 없음"
    assert "예고 없이" in sp, "중간 보고 금지 지시가 없음"
    assert out.get("evidence") is None, "RAG 는 여전히 스킵되어야 한다(무회귀)"


def test_chat_worker_gets_no_baseline_guidance() -> None:
    """chat 은 도구가 없으므로 지침을 주지 않는다(무회귀)."""
    out = _run_retrieve("chat", {"prompt": "안녕", "project_path": ""})
    assert out.get("evidence") is None
    # 장기 기억도 없으므로 system_prompt 키가 아예 없어야 한다(기존 동작).
    assert "system_prompt" not in out, f"chat 에 불필요한 지침이 주입됨: {out}"


def test_existing_system_prompt_is_preserved() -> None:
    """호출자가 준 system_prompt 는 지침 앞에 보존된다."""
    out = _run_retrieve(
        "research", {"prompt": "q", "project_path": "", "system_prompt": "기존지침XYZ"}
    )
    sp = out.get("system_prompt") or ""
    assert sp.startswith("기존지침XYZ"), f"기존 system_prompt 유실: {sp[:60]}"
    assert "web_search" in sp


if __name__ == "__main__":
    import traceback

    fails = 0
    for name, fn in sorted(globals().items()):
        if not name.startswith("test_") or not callable(fn):
            continue
        try:
            fn()
            print(f"  PASS {name}")
        except Exception:
            fails += 1
            print(f"  FAIL {name}")
            traceback.print_exc()
    print(f"\n{'실패 ' + str(fails) if fails else '전부 통과'}")
    raise SystemExit(1 if fails else 0)


# ─────────────────────────────────────────────────────────────────────────────
# F. 라우팅이 어긋나도 외부 조회가 가능하다 (curl 우회 동기 제거)
# ─────────────────────────────────────────────────────────────────────────────
# 실측 사고: planner LLM 이 "GLP-1 간독성 논문을 검색해서 DOI와 함께 제시해줘"를
# coding/ops 로 라우팅했고, 그 워커에 web_search/search_papers 가 없었다. 모델은 도구
# 부재를 정확히 인지한 뒤 run_command 로 curl 을 실행해 우회했다 — 결과는 나왔지만
# 옵트인·동의 게이트와 캐시·인용 검증을 전부 건너뛴다.
def test_coding_worker_has_readonly_lookup_tools() -> None:
    """coding 워커가 읽기 전용 외부 조회 도구 3종을 갖는다(게이트 경유 경로 확보)."""
    from ai_engine.agent_system.subgraphs.coding import CODING_TOOLS
    from ai_engine.agent_system.subgraphs.research import RESEARCH_LOOKUP_TOOLS

    merged = [t["name"] for t in CODING_TOOLS + RESEARCH_LOOKUP_TOOLS]
    for name in ("web_search", "search_papers", "fetch_content"):
        assert name in merged, f"coding 워커에 {name} 이 없음 — curl 우회 유발"
    # 도구 이름 중복은 Bedrock toolConfig 검증에서 거부될 수 있다.
    assert len(merged) == len(set(merged)), f"도구 이름 중복: {merged}"


def test_deep_research_stays_research_only() -> None:
    """deep_research 는 무겁고 자체 파이프라인이라 research 워커 전용으로 남는다."""
    from ai_engine.agent_system.subgraphs.research import (
        RESEARCH_LOOKUP_TOOLS,
        RESEARCH_TOOLS,
    )

    lookup = {t["name"] for t in RESEARCH_LOOKUP_TOOLS}
    assert "deep_research" not in lookup
    assert "deep_research" in {t["name"] for t in RESEARCH_TOOLS}


def test_lookup_tools_are_subset_of_research_tools() -> None:
    """조회 도구는 RESEARCH_TOOLS 의 부분집합이다(스키마 단일 소스 — 복제 금지)."""
    from ai_engine.agent_system.subgraphs.research import (
        RESEARCH_LOOKUP_TOOLS,
        RESEARCH_TOOLS,
    )

    for t in RESEARCH_LOOKUP_TOOLS:
        assert t in RESEARCH_TOOLS, f"{t.get('name')} 스키마가 RESEARCH_TOOLS 와 다름"


def test_lookup_tools_are_dispatchable() -> None:
    """병합한 조회 도구가 server._execute_tool 디스패치 대상에 모두 있다."""
    from ai_engine.agent_system.subgraphs.research import (
        RESEARCH_LOOKUP_TOOLS,
        RESEARCH_TOOL_EXECUTORS,
    )

    for t in RESEARCH_LOOKUP_TOOLS:
        name = t["name"]
        assert name in RESEARCH_TOOL_EXECUTORS, f"{name} 실행기 미등록 — 호출 시 실패"
        assert callable(RESEARCH_TOOL_EXECUTORS[name])


def test_ops_worker_still_minimal() -> None:
    """ops 는 운영 전용이므로 조회 도구를 병합하지 않는다(무회귀)."""
    from ai_engine.agent_system.subgraphs.ops import OPS_TOOLS

    names = {t["name"] for t in OPS_TOOLS}
    assert names == {"run_command"}, f"ops 도구 집합이 변경됨: {names}"
