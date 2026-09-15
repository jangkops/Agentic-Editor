# Feature: deep-research-engine
"""도구 등록·name 정합·디스패치 단위 테스트 (Task 10.3).

Feature: deep-research-engine
**Validates: Requirements 1.6, 1.7, 2.5, 17.1, 17.4**

축 A(조회 도구 통합)의 두 배선 지점이 서로 정합함을 단위 수준에서 확인한다.

- ``subgraphs/research.py`` 의 ``RESEARCH_TOOLS`` (도구 스키마 목록, name 단일 소스)
- ``subgraphs/research.py`` 의 ``RESEARCH_TOOL_EXECUTORS`` (name → 동기 실행기 매핑)
- ``server.py`` 의 ``_execute_tool`` (name 기반 로컬 통합 디스패처)
- ``nodes/tool_node.py`` 의 ``GatewayToolNode`` (tool_call → ToolMessage 변환)

검증 축:

  (1) **name 유일성** — ``RESEARCH_TOOLS`` 의 모든 도구 name 이 유일하다(중복 없음).
      중복 name 은 디스패치 모호성을 유발하므로 금지된다(요구사항 1.6/2.5/17.1).
  (2) **name 정합(단일 소스)** — 리서치 실행기 4종
      (``web_search``/``search_papers``/``fetch_content`` + ``deep_research``)의 name 이
      ``RESEARCH_TOOLS`` 스키마와 ``RESEARCH_TOOL_EXECUTORS`` 키에서 **정확히 동일 문자열**로
      일치한다(요구사항 17.4). 조회 3종(축 A)에 더해 딥리서치(축 B, Task 14.1)도 실행기
      매핑을 가지며, 실행기 키 집합은 이 4종과 **정확히 상등**한다. read_file/search_files 는
      자체 분기로 디스패치되므로 실행기 매핑 대상이 아니다.
  (3) **디스패치 인지 + 호출당 결과 1개** — ``server._execute_tool`` 이 4개 name 을
      각각 인지해 실행기로 라우팅하고, **호출당 정확히 하나의 JSON 문자열**을 반환하며
      예외를 전파하지 않는다(요구사항 17.4/1.7). deep_research 도 동일하게 디스패치됨을
      확인한다. 미지의 name 은 디스패치되지 않음을 음성 대조로 함께 확인한다.
  (4) **옵트인 OFF 비차단** — 실행기를 옵트인/동의 off(env={}) 또는 빈 입력으로 직접
      호출하면 외부 호출 없이 구조화된 결과 dict 를 비차단 반환한다(무회귀 — P15 인접).
      조회 3종은 disabled/invalid_url 로, 딥리서치는 파이프라인 실행 이전의 kind="deep"
      구조화 결과(예외 없음)로 비차단 반환한다. 각 결과는 JSON 문자열 1개로 직렬화 가능하다.
  (5) **호출당 ToolMessage 1개** — ``GatewayToolNode`` 에 4개 tool_call 을 담은
      AIMessage 를 넣으면 **tool_call 당 ToolMessage 정확히 1개**(총 4개, tool_call_id
      매칭)를 반환한다(요구사항 1.7/17.4). ``_execute_tool`` 의 "결과 1개"가
      GatewayToolNode 를 통해 "ToolMessage 1개"로 매핑됨을 직접 확인한다.

밀폐(hermetic) 원칙: 모든 실행 경로는 **빈 입력**(invalid_query/invalid_url) 또는
**옵트인 off(env={})** 만 사용한다. 조회 3종은 두 경로 모두 외부 egress(``web_search_raw`` /
``fetch_url_raw`` 등) 이전에 구조화 결과로 반환된다. 딥리서치(축 B)는 옵트인 off 여도
파이프라인을 실행하므로(조회 3종의 조기 disabled 폴백과 다름), 본 테스트는 딥리서치를
**빈 질의**(invalid_query)로만 호출해 파이프라인·egress·디스크 기록 이전에 비차단
종료시킨다. 따라서 주변 환경변수(``AE_ENABLE_WEB_RESEARCH`` 등)와 무관하게 네트워크
호출이 발생하지 않는다(결정적·오프라인).

Stack: Python 3.11+ + pytest 수집. 단발 실행(워치 모드 금지):
    ai_engine/.venv/bin/python -m pytest scripts/test_research_tool_dispatch.py -q
    ai_engine/.venv/bin/python scripts/test_research_tool_dispatch.py

_Requirements: 1.6, 1.7, 2.5, 17.1, 17.4_
"""
from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path

# 스크립트를 직접 실행할 때도 ai_engine 패키지를 import 할 수 있게 repo 루트를 경로에 추가.
_REPO_ROOT = Path(__file__).resolve().parent.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from ai_engine.agent_system.subgraphs.research import (  # noqa: E402
    RESEARCH_TOOLS,
    RESEARCH_TOOL_EXECUTORS,
)

# 외부 "조회" 도구 3종(축 A)의 정식 name(요구사항 1.6/2.5/17.1). server._execute_tool
# 디스패치 분기·RESEARCH_TOOL_EXECUTORS 키에 동일 문자열로 존재해야 한다(요구사항 17.4).
# 이 상수는 "조회/fetch 3종에 한정된" 단언(스키마 노출·구조 계약)에서만 사용한다.
_RESEARCH_TOOL_NAMES = ("web_search", "search_papers", "fetch_content")

# RESEARCH_TOOL_EXECUTORS 가 보유해야 하는 리서치 실행기 name 전체(요구사항 17.4).
# 조회 3종(축 A) + 딥리서치(축 B, Task 14.1) = 4종. web/academic/fetch 는 backend(단일
# egress)+normalize+rank 로 조립되는 경량 조회 도구이고, deep_research 는 축 B 파이프라인
# 동기 seam(run_deep_research_sync)을 호출하는 멀티스텝 도구다. read_file/search_files 는
# 자체 분기로 디스패치되므로 실행기 매핑 대상이 아니다. 실행기-키 "정확히 일치"(집합 상등)
# 단언은 이 상수를 기준으로 한다(과거 3종 계약 → 현재 4종 계약으로 정합화).
_RESEARCH_EXECUTOR_NAMES = (
    "web_search",
    "search_papers",
    "fetch_content",
    "deep_research",
)


def _tool_names() -> list[str]:
    """RESEARCH_TOOLS 스키마에서 도구 name 목록을 추출한다."""
    return [t["name"] for t in RESEARCH_TOOLS]


# =========================================================================== #
# (1) name 유일성 — 요구사항 1.6/2.5/17.1
# =========================================================================== #
def test_research_tools_names_are_unique() -> None:
    """RESEARCH_TOOLS 의 모든 도구 name 이 유일하다(중복 없음)."""
    names = _tool_names()
    assert len(names) == len(set(names)), (
        f"RESEARCH_TOOLS 에 중복 name 존재: {names}"
    )
    # 모든 name 은 비어있지 않은 문자열이어야 한다(디스패치 키로 사용됨).
    for n in names:
        assert isinstance(n, str) and n.strip(), f"유효하지 않은 도구 name: {n!r}"


def test_research_tools_expose_research_tool_names() -> None:
    """외부 조회 도구 3종이 RESEARCH_TOOLS 에 노출되고, 기존 로컬 도구는 유지된다."""
    names = set(_tool_names())
    # 3종 조회 도구 노출(요구사항 1.6/2.5/17.1).
    for expected in _RESEARCH_TOOL_NAMES:
        assert expected in names, f"RESEARCH_TOOLS 에 {expected!r} 도구가 없음"
    # 기존 로컬 파일 도구 유지(무회귀).
    assert {"read_file", "search_files"} <= names, "기존 로컬 도구가 누락됨"


# =========================================================================== #
# (2) name 정합(단일 소스) — 요구사항 17.4
# =========================================================================== #
def test_research_tool_names_match_executor_keys() -> None:
    """리서치 실행기 4종 name 이 RESEARCH_TOOLS·RESEARCH_TOOL_EXECUTORS 에서 정확히 일치한다.

    조회 3종(축 A)에 더해 딥리서치(축 B, Task 14.1)까지 총 4종이 실행기 매핑을 가진다.
    실행기 키 집합은 이 4종과 **정확히 상등**하며, 모든 키는 RESEARCH_TOOLS 스키마 name 에도
    동일 문자열로 존재한다(단일 소스 — 요구사항 17.4). 특히 deep_research 가 스키마에
    present + 실행기 has-executor 임을 명시적으로 확인한다.
    """
    schema_names = set(_tool_names())
    executor_keys = set(RESEARCH_TOOL_EXECUTORS.keys())

    # 실행기 매핑 키 집합 == 리서치 실행기 4종(정확히 이 네 개만 실행기를 가진다 — 조회 3종
    # + 딥리서치). 과거 "조회 3종" 계약을 4종 계약으로 정합화한다(Task 14.1 이 4번째 실행기
    # deep_research 를 정당하게 추가함 — 제품이 정답, 테스트가 stale 이었음).
    assert executor_keys == set(_RESEARCH_EXECUTOR_NAMES), (
        f"RESEARCH_TOOL_EXECUTORS 키가 리서치 실행기 4종과 불일치: {sorted(executor_keys)}"
    )
    # 각 실행기 키는 RESEARCH_TOOLS 스키마 name 에도 동일 문자열로 존재한다(단일 소스).
    assert executor_keys <= schema_names, (
        f"실행기 키가 RESEARCH_TOOLS name 에 없음: {sorted(executor_keys - schema_names)}"
    )
    # 딥리서치(4번째 실행기)를 명시적으로 확인: 스키마 present-in-schema + 실행기 has-executor.
    assert "deep_research" in schema_names, "deep_research 도구가 RESEARCH_TOOLS 스키마에 없음"
    assert "deep_research" in executor_keys, (
        "deep_research 실행기가 RESEARCH_TOOL_EXECUTORS 에 없음"
    )
    # 각 실행기는 호출 가능해야 한다.
    for name, fn in RESEARCH_TOOL_EXECUTORS.items():
        assert callable(fn), f"{name!r} 실행기가 호출 가능하지 않음"


# =========================================================================== #
# (3) server._execute_tool 디스패치 인지 + 호출당 JSON 문자열 1개 — 요구사항 17.4/1.7
# =========================================================================== #
def test_execute_tool_dispatches_each_research_tool_one_json_string() -> None:
    """_execute_tool 이 4개 name 을 각각 인지해 JSON 문자열 1개를 예외 없이 반환한다.

    빈 입력(``{}``)을 사용하므로 web/academic 은 invalid_query, fetch 는 invalid_url,
    deep_research 는 파이프라인 실행 이전의 invalid_query(kind="deep") 구조화 결과로 외부
    egress·디스크 기록 이전에 반환된다(밀폐·오프라인). 각 반환값은 단일 JSON 문서
    (``json.loads`` 성공 → 정확히 결과 1개)이며, 이는 GatewayToolNode 가 호출당 ToolMessage
    1개로 매핑하는 계약과 정합한다(요구사항 1.7). 딥리서치(Task 14.1)도 동일하게 디스패치됨을
    포함해 확인한다(요구사항 17.4).
    """
    import ai_engine.server as server

    for name in _RESEARCH_EXECUTOR_NAMES:
        out = server._execute_tool(name, {})
        # 호출당 결과 1개 = 단일 문자열.
        assert isinstance(out, str), f"{name}: _execute_tool 이 str 을 반환하지 않음: {type(out)}"
        # 단일 JSON 문서로 파싱됨(연결된 JSON 이면 json.loads 가 실패 → "정확히 1개" 보장).
        parsed = json.loads(out)
        assert isinstance(parsed, dict), f"{name}: JSON dict 가 아님: {type(parsed)}"
        # 디스패치가 실행기로 라우팅됨을 구조로 확인(미지 도구 문자열이 아님).
        assert not out.startswith("알 수 없는 도구"), f"{name}: 디스패치되지 않음"

    # 도구별 구조 계약(실행기가 실제로 호출됐음을 확인).
    web = json.loads(server._execute_tool("web_search", {}))
    assert web.get("kind") == "web" and web.get("error") == "invalid_query"
    aca = json.loads(server._execute_tool("search_papers", {}))
    assert aca.get("kind") == "academic" and aca.get("error") == "invalid_query"
    fc = json.loads(server._execute_tool("fetch_content", {}))
    assert fc.get("ok") is False and fc.get("error") == "invalid_url"
    # deep_research: 빈 질의 → 파이프라인 미실행, invalid_query(kind="deep") 구조화 결과.
    deep = json.loads(server._execute_tool("deep_research", {}))
    assert deep.get("kind") == "deep" and deep.get("error") == "invalid_query"


def test_execute_tool_unknown_name_not_dispatched() -> None:
    """미지의 도구 name 은 조회 도구로 디스패치되지 않는다(음성 대조)."""
    import ai_engine.server as server

    out = server._execute_tool("definitely_not_a_research_tool", {})
    assert isinstance(out, str)
    assert out.startswith("알 수 없는 도구"), (
        f"미지 도구가 예상 밖 경로로 처리됨: {out[:80]!r}"
    )


# =========================================================================== #
# (4) 옵트인 OFF 비차단 — 실행기 직접 호출(env={}) — 요구사항 1.7 / 무회귀
# =========================================================================== #
def test_executors_optin_off_return_structured_nonblocking() -> None:
    """옵트인/동의 off(env={}) 로 실행기를 직접 호출하면 비차단 구조화 결과를 반환한다.

    web_search/search_papers 는 게이트가 off 이므로 외부 호출 없이 ``disabled=True`` 로
    폴백한다(무회귀). fetch_content 는 빈 url 로 invalid_url 을 반환한다. deep_research 는
    옵트인 off 여도 조기 disabled 폴백을 하지 않고 파이프라인을 실행하는 실행기이므로,
    밀폐 유지를 위해 빈 질의로 호출해 **파이프라인 실행 이전의** 비차단 구조화 결과
    (``kind="deep"``, invalid_query — web/academic 의 ``disabled`` 형태와 다른 실제
    non-blocking shape, 예외 없음)를 확인한다. 네 결과 모두 dict 이며 JSON 문자열 1개로
    직렬화 가능하다(호출당 결과 1개 — 요구사항 1.7).
    """
    # web_search: 옵트인 off + 실제 질의 → disabled(외부 호출 없음).
    web = RESEARCH_TOOL_EXECUTORS["web_search"]({"query": "transformer models"}, env={})
    assert isinstance(web, dict) and web.get("disabled") is True
    assert web.get("count") == 0 and web.get("results") == []

    # search_papers: 옵트인 off + 실제 질의 → disabled.
    aca = RESEARCH_TOOL_EXECUTORS["search_papers"]({"query": "deep learning"}, env={})
    assert isinstance(aca, dict) and aca.get("disabled") is True
    assert aca.get("count") == 0 and aca.get("results") == []

    # fetch_content: 빈 url → invalid_url(외부 호출 없음).
    fc = RESEARCH_TOOL_EXECUTORS["fetch_content"]({"url": ""}, env={})
    assert isinstance(fc, dict) and fc.get("ok") is False and fc.get("error") == "invalid_url"

    # deep_research: 빈 질의 → 파이프라인 실행 이전의 비차단 구조화 결과(kind="deep").
    # 축 B 파이프라인은 옵트인 off 로도 disabled 폴백하지 않고 실행되므로(그러면 디스크 기록·
    # 소요 발생), 밀폐·결정성 유지를 위해 빈 질의(invalid_query)로 조기 반환을 확인한다.
    # web/academic 의 disabled 형태가 아니라 deep_research 고유의 non-blocking shape 이다.
    deep = RESEARCH_TOOL_EXECUTORS["deep_research"]({}, env={})
    assert isinstance(deep, dict) and deep.get("kind") == "deep"
    assert deep.get("error") == "invalid_query" and "disabled" not in deep

    # 각 결과는 JSON 문자열 1개로 직렬화 가능(_execute_tool 의 json.dumps 계약 정합).
    for res in (web, aca, fc, deep):
        s = json.dumps(res, ensure_ascii=False)
        assert isinstance(json.loads(s), dict)


# =========================================================================== #
# (5) GatewayToolNode — tool_call 당 ToolMessage 1개 — 요구사항 1.7/17.4
# =========================================================================== #
class _FakeAIMessage:
    """마지막 AIMessage 스텁 — GatewayToolNode 가 읽는 ``tool_calls`` 만 제공한다."""

    def __init__(self, tool_calls: list[dict]) -> None:
        self.tool_calls = tool_calls


def test_gateway_tool_node_one_toolmessage_per_call() -> None:
    """4개 리서치 도구 tool_call → ToolMessage 정확히 4개(호출당 1개, id 매칭).

    GatewayToolNode 는 각 tool_call 을 ``_execute_tool`` 로 실행하고 결과 문자열을
    ToolMessage 1개로 감싼다. 빈 args 를 사용해 외부 호출 없이(밀폐) "호출당 정확히
    1개"(요구사항 1.7)와 name→ToolMessage 매핑(요구사항 17.4)을 확인한다. 조회 3종에 더해
    딥리서치(Task 14.1)도 프로덕션 노드 경로로 디스패치돼 ToolMessage 1개로 매핑됨을 포함해
    확인한다(빈 질의 → invalid_query 로 파이프라인·디스크 기록 미발생 — 밀폐). 검색 도구
    (web_search/search_papers/deep_research)는 실행 경계에서 search_status 이벤트를 방출하지만
    방출 실패는 비차단이라(런 컨텍스트 부재에도) 도구 결과 처리를 막지 않는다(P8).
    """
    from ai_engine.agent_system.nodes.tool_node import GatewayToolNode

    tool_calls = [
        {"name": "web_search", "args": {}, "id": "call_web"},
        {"name": "search_papers", "args": {}, "id": "call_aca"},
        {"name": "fetch_content", "args": {}, "id": "call_fetch"},
        {"name": "deep_research", "args": {}, "id": "call_deep"},
    ]
    state = {
        "messages": [_FakeAIMessage(tool_calls)],
        "project_path": "",
    }
    node = GatewayToolNode(tools=RESEARCH_TOOLS, deps=None, timeout=30)
    result = asyncio.run(node(state))

    messages = result["messages"]
    # 호출당 ToolMessage 정확히 1개 → 총 4개.
    assert len(messages) == len(tool_calls), (
        f"ToolMessage 개수 불일치: 기대 {len(tool_calls)}, 실제 {len(messages)}"
    )
    # tool_call_id 가 입력 tool_call 과 1:1 매칭.
    assert [m.tool_call_id for m in messages] == [tc["id"] for tc in tool_calls]
    # 각 ToolMessage content 는 _execute_tool 이 반환한 단일 JSON 문서.
    for m in messages:
        assert isinstance(json.loads(m.content), dict)
    # 리서치 도구(빈 입력)는 파일 산출물이 없으므로 verified_files 는 비어 있다(요구사항 17.5
    # 정합). deep_research 도 빈 질의 → error 결과라 path 미노출 → verified_files 미포함.
    assert result["verified_files"] == []


if __name__ == "__main__":  # 편의 실행 경로(단발 — 워치 모드 금지)
    import pytest

    raise SystemExit(pytest.main([__file__, "-q"]))
