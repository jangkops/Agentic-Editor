# Feature: deep-research-engine, Property 14: 인디케이터 라이프사이클 무결성
"""Property 14 — 인디케이터 라이프사이클 무결성 property 테스트.

Feature: deep-research-engine, Property 14: 인디케이터 라이프사이클 무결성
**Validates: Requirements 18.1, 18.3, 18.6**

*For any* 검색 도구 실행(``web_search`` / ``search_papers`` / ``deep_research``)에
대해, 시작 ``search_status``(phase=start)가 **정확히 1회** 방출된 뒤 종료
``search_status``(phase=end)가 **정확히 1회** 방출된다(고아 시작 없음, 미종료 없음).
도구 실행이 성공·타임아웃·예외 중 무엇으로 끝나든 종료 이벤트는 ``try/finally`` 로
보장되므로, ``Search_Indicator`` 는 검색 종료 후 활성 상태로 잔류하지 않는다.

검증 대상 컴포넌트는 ``ai_engine/agent_system/nodes/tool_node.py`` 의
``GatewayToolNode`` 실행 경계 방출(task 20.1, design.md "9-1) 방출")이다. 임의의
도구 호출 시퀀스(검색 도구 + 비검색 도구 혼합)와 임의의 종료 유형(성공/타임아웃/
예외)을 생성해, ``adispatch_custom_event`` 를 비동기 레코더로 monkeypatch 한 뒤
방출 이벤트 로그를 수집하여 다음 불변식을 검증한다:

    1. (start,end)=(1,1)  : 각 검색 도구 실행마다 start 1회 → end 1회.
    2. 순서               : 방출 순서는 항상 start → end (교대, 미종료/고아 없음).
    3. 비검색 방출 0      : 검색 도구가 아닌 도구는 search_status 를 방출하지 않는다.
    4. 종료 상태          : 성공 → status="ok", 타임아웃/예외 → status="error".
    5. P9(비노출)         : payload 에 자격증명 없이 제공자 "이름" 목록만 존재.
    6. ToolMessage 1/호출 : 방출과 무관하게 호출당 ToolMessage 1개가 유지된다.

노드 구동 패턴(``asyncio.run`` 로 ``__call__`` 실행)은 기존
``scripts/test_langgraph_tool_node.py`` 를 계승한다. 종료 유형 중 "타임아웃"은
``GatewayToolNode`` 의 ``except asyncio.TimeoutError`` 분기를 결정적·즉시 트리거하기
위해 워커에서 ``asyncio.TimeoutError`` 를 발생시켜 시뮬레이션한다(200회 반복에서
실제 sleep 기반 타임아웃은 스레드풀 포화·지연으로 불안정). ``asyncio.wait_for`` 의
실제 시계 기반 타임아웃 경로는 하단 스모크 ``test_smoke_real_timeout_*`` 에서 실제
sleep 으로 한 번 더 확인한다.

Stack: Python 3.11+, hypothesis 라이브러리(기존 ``scripts/test_*_pbt.py`` 관례).
"""
from __future__ import annotations

import asyncio
import json
import sys
import time
from pathlib import Path

# 스크립트를 직접 실행할 때 ai_engine 패키지를 import 가능하게 한다.
_REPO_ROOT = Path(__file__).resolve().parent.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

import pytest  # noqa: E402
from hypothesis import example, given, settings  # noqa: E402
from hypothesis import strategies as st  # noqa: E402

from ai_engine.agent_system.nodes import tool_node as TN  # noqa: E402

# 최소 반복 200회(task 20.5 지정 — @settings(max_examples=200)).
_MAX_EXAMPLES = 200

# 검색 도구군(방출 대상) / 비검색·비미디어 도구군(방출 0). 미디어 도구는 전역 세마포어
# (이벤트루프 간 재사용 위험)를 타므로 제외해 밀폐성을 유지한다.
_SEARCH_POOL = sorted(TN._SEARCH_TOOLS)  # deep_research, search_papers, web_search
_NON_SEARCH_POOL = ["read_file", "list_directory", "search_files", "run_command", "write_file"]
_ALL_TOOLS = _SEARCH_POOL + _NON_SEARCH_POOL
_OUTCOMES = ["success", "exception", "timeout"]

# start(status 없음) / end(status 있음) payload 의 허용 키 집합(P9: 크리덴셜 키 부재).
#
# end 페이로드는 도구 결과에서 추출한 **실제 산출**을 선택적으로 더 싣는다
# (`tool_node._search_outcome_from_result`): count / error / provider_errors.
# 근거: `status` 는 실행 성공(예외·타임아웃 없음)만 뜻해서, "제공자 키가 없어 0건" 같은
# 조용한 실패도 status="ok" 로 방출됐다(실측 사고). 이 키들이 있어야 검색 인디케이터가
# "완료" 대신 사유를 보여주고 effect_ledger 가 선언 vs 실제 불일치를 판정할 수 있다.
# 필수(_END_KEYS)와 선택(_END_OPTIONAL_KEYS)을 구분해 P9 검사는 그대로 유지한다.
_START_KEYS = {"phase", "kind", "providers", "query_summary"}
_END_KEYS = {"phase", "kind", "providers", "query_summary", "status"}
_END_OPTIONAL_KEYS = {"count", "error", "provider_errors"}

# providers 이름에 절대 나타나면 안 되는 자격증명 흔적(P9).
_CRED_MARKERS = ("secret", "token", "apikey", "api_key")


# --------------------------------------------------------------------------- #
# 노드 구동 하네스 (test_langgraph_tool_node.py 패턴 계승)
# --------------------------------------------------------------------------- #
class _FakeAI:
    """tool_calls 만 갖는 최소 AIMessage 대역."""

    def __init__(self, tool_calls):
        self.tool_calls = tool_calls


def _tc(name, args=None, tid="tc1"):
    return {"name": name, "args": args or {}, "id": tid}


def _fake_run_local(name, args, state):
    """도구 인자에 인코딩된 종료 유형을 재생하는 로컬 디스패처 대역.

    - success  : 정상 JSON 문자열 반환.
    - exception: ``RuntimeError`` 발생 → 노드의 ``except Exception`` 분기(status=error).
    - timeout  : ``asyncio.TimeoutError`` 발생 → 노드의 ``except asyncio.TimeoutError``
                 분기(status=error). ``asyncio.TimeoutError`` 는 3.11+ 에서 builtin
                 ``TimeoutError`` 의 별칭이라 노드의 타임아웃 핸들러가 그대로 포착한다.
    """
    outcome = args.get("_outcome", "success") if isinstance(args, dict) else "success"
    if outcome == "exception":
        raise RuntimeError(f"simulated provider failure: {name}")
    if outcome == "timeout":
        raise asyncio.TimeoutError()
    return json.dumps({"results": [], "tool": name})


def _run_node_capture(tool_calls, *, run_local=_fake_run_local, timeout=5.0):
    """``GatewayToolNode`` 를 구동하고 방출된 ``search_status`` 이벤트를 수집한다.

    ``adispatch_custom_event`` 를 모듈 속성 수준에서 비동기 레코더로 교체한다
    (``_emit_search_status`` 가 모듈 전역으로 조회하므로 유효). hypothesis 의
    function-scoped fixture 헬스체크를 피하기 위해 pytest fixture 대신 저장/복원한다.
    """
    node = TN.GatewayToolNode(tools=[], deps=None, timeout=timeout)
    node._run_local = run_local  # 인스턴스 속성이 메서드를 가림(self 미바인딩) — 참고 테스트 동형
    state = {"messages": [_FakeAI(tool_calls)]}

    events: list = []

    async def _rec(name, data, *, config=None):
        events.append((name, dict(data)))  # 방어적 복사

    orig = TN.adispatch_custom_event
    TN.adispatch_custom_event = _rec
    try:
        out = asyncio.run(node(state))
    finally:
        TN.adispatch_custom_event = orig
    return out, events


def _tool_calls_from_specs(specs):
    """스펙 리스트 → tool_calls(고유 id, args 에 종료 유형 인코딩)."""
    return [
        {
            "name": s["name"],
            "args": {"query": s["query"], "_outcome": s["outcome"]},
            "id": f"tc{i}",
        }
        for i, s in enumerate(specs)
    ]


# --------------------------------------------------------------------------- #
# 생성기 — 검색/비검색 혼합 시퀀스 + 성공/예외/타임아웃 종료 유형 다양화
# --------------------------------------------------------------------------- #
_query = st.text(
    alphabet=st.characters(
        min_codepoint=32, max_codepoint=0x2FFF, blacklist_categories=("Cs",)
    ),
    max_size=40,
)


@st.composite
def _tool_spec(draw) -> dict:
    return {
        "name": draw(st.sampled_from(_ALL_TOOLS)),
        "outcome": draw(st.sampled_from(_OUTCOMES)),
        "query": draw(_query),
    }


# 최대 6개 호출(검색/비검색 혼합). 빈 리스트(도구 없음)도 유효 케이스로 포함한다.
_specs = st.lists(_tool_spec(), max_size=6)


def _assert_lifecycle(specs, out, events) -> None:
    """P14 라이프사이클 무결성 불변식 일괄 검증(속성/스모크 공용 오라클)."""
    # 방출된 이벤트는 전부 search_status 여야 한다(다른 커스텀 이벤트 없음).
    assert all(n == "search_status" for (n, _d) in events), (
        f"unexpected non-search_status event emitted: {events}"
    )
    payloads = [d for (_n, d) in events]

    search_specs = [s for s in specs if s["name"] in TN._SEARCH_TOOLS]
    num_search = len(search_specs)

    # facet 1+3 — 총 방출 = 2 × 검색 실행 수. 비검색 도구가 방출 0 임을 함의한다.
    assert len(payloads) == 2 * num_search, (
        f"emit count != 2*num_search: emits={len(payloads)}, num_search={num_search}"
    )

    # facet 2 — 순서: start,end 가 정확히 교대(고아 start 없음, 미종료 없음).
    phases = [p["phase"] for p in payloads]
    assert phases == ["start", "end"] * num_search, (
        f"lifecycle order violated (expected alternating start→end): {phases}"
    )

    # facet 4+5 — 각 검색 실행의 (start,end) 쌍이 kind/status/키집합 정합.
    for k, s in enumerate(search_specs):
        start = payloads[2 * k]
        end = payloads[2 * k + 1]
        expected_kind = TN._SEARCH_KIND[s["name"]]
        expected_status = "ok" if s["outcome"] == "success" else "error"

        assert start["phase"] == "start" and end["phase"] == "end"
        assert start["kind"] == expected_kind, (start, s)
        assert end["kind"] == expected_kind, (end, s)
        assert "status" not in start, "start 에는 status 가 없어야 한다"
        assert end["status"] == expected_status, (end, s)

        # P9 — 허용 키만 존재(크리덴셜 키가 끼어들 여지 없음) + providers 는 이름 목록.
        assert set(start) == _START_KEYS, f"start keys={set(start)}"
        # end 는 필수 키를 모두 갖고, 그 외에는 선택 키(실제 산출)만 허용한다.
        assert _END_KEYS <= set(end), f"end 필수 키 누락: {_END_KEYS - set(end)}"
        _extra = set(end) - _END_KEYS
        assert _extra <= _END_OPTIONAL_KEYS, f"end 에 미허용 키: {_extra}"
        for p in (start, end):
            provs = p["providers"]
            assert isinstance(provs, list) and all(isinstance(x, str) for x in provs)
            for name in provs:
                low = name.lower()
                assert not any(m in low for m in _CRED_MARKERS), (
                    f"provider name looks like a credential: {name!r}"
                )

    # facet 6 — ToolMessage 1/호출 불변식 보존(요구사항 17.3): 방출과 무관하게 유지.
    assert len(out["messages"]) == num_search + (len(specs) - num_search)
    assert len(out["messages"]) == len(specs)


# --------------------------------------------------------------------------- #
# Property 14 — 인디케이터 라이프사이클 무결성 (단일 속성, 6 facet)
# --------------------------------------------------------------------------- #
@settings(max_examples=_MAX_EXAMPLES, deadline=None)
@given(specs=_specs)
# 결정적으로 핵심 시나리오를 매 실행 포함한다.
@example(specs=[])  # 도구 없음 → 방출 0, 미종료 없음
@example(specs=[{"name": "read_file", "outcome": "success", "query": ""}])  # 비검색만 → 0
@example(
    specs=[
        {"name": "web_search", "outcome": "success", "query": "q1"},
        {"name": "read_file", "outcome": "success", "query": ""},
        {"name": "search_papers", "outcome": "exception", "query": "q2"},
        {"name": "run_command", "outcome": "success", "query": ""},
        {"name": "deep_research", "outcome": "timeout", "query": "q3"},
    ]
)  # 혼합 + 성공/예외/타임아웃 종료 유형 각각
@example(
    specs=[
        {"name": "web_search", "outcome": "timeout", "query": "a"},
        {"name": "search_papers", "outcome": "timeout", "query": "b"},
        {"name": "deep_research", "outcome": "exception", "query": "c"},
    ]
)  # 전부 실패 종료라도 (start,end)=(1,1) 유지
def test_search_indicator_lifecycle_integrity(specs) -> None:
    """임의 시퀀스·임의 종료 유형에서 실행마다 (start,end)=(1,1), 순서 start→end (P14)."""
    tool_calls = _tool_calls_from_specs(specs)
    out, events = _run_node_capture(tool_calls)
    _assert_lifecycle(specs, out, events)


# --------------------------------------------------------------------------- #
# 스모크 — 속성 테스트가 공허하게 통과하지 않도록 구체 예시로 라이프사이클을 고정
# --------------------------------------------------------------------------- #
def test_smoke_mixed_sequence_exact_events() -> None:
    """혼합 시퀀스의 방출 이벤트를 구체적으로 고정한다."""
    specs = [
        {"name": "web_search", "outcome": "success", "query": "hello"},
        {"name": "list_directory", "outcome": "success", "query": ""},
        {"name": "deep_research", "outcome": "exception", "query": "deep"},
    ]
    out, events = _run_node_capture(_tool_calls_from_specs(specs))
    kinds_phases = [(d["kind"], d["phase"], d.get("status")) for (_n, d) in events]
    assert kinds_phases == [
        ("web", "start", None),
        ("web", "end", "ok"),
        ("deep", "start", None),
        ("deep", "end", "error"),
    ]
    assert len(out["messages"]) == 3  # ToolMessage 1/호출
    _assert_lifecycle(specs, out, events)


def test_smoke_non_search_only_emits_nothing() -> None:
    """비검색 도구만 있으면 search_status 방출이 0 이다(기존 동작 불변, 무회귀)."""
    specs = [
        {"name": "read_file", "outcome": "success", "query": ""},
        {"name": "write_file", "outcome": "exception", "query": ""},
        {"name": "run_command", "outcome": "timeout", "query": ""},
    ]
    out, events = _run_node_capture(_tool_calls_from_specs(specs))
    assert events == []
    assert len(out["messages"]) == 3


def test_smoke_real_timeout_emits_end_error() -> None:
    """실제 ``asyncio.wait_for`` 시계 기반 타임아웃 경로에서도 end(status=error) 1회.

    PBT 는 속도·안정성을 위해 TimeoutError 발생으로 타임아웃 분기를 시뮬레이션하므로,
    여기서는 실제 sleep 으로 wait_for 자체의 타임아웃 메커니즘을 1회 검증한다.
    """
    slow = lambda name, args, state: (time.sleep(0.5), "{}")[1]  # noqa: E731
    out, events = _run_node_capture(
        [_tc("web_search", {"query": "q"})], run_local=slow, timeout=0.05
    )
    assert [(d["phase"], d.get("status")) for (_n, d) in events] == [
        ("start", None),
        ("end", "error"),
    ]
    assert "시간 초과" in out["messages"][0].content


def test_smoke_emit_failure_is_non_blocking() -> None:
    """방출(adispatch)이 항상 예외를 던져도 도구 실행·ToolMessage 생성은 진행된다(P8).

    방출 실패로 이벤트가 유실돼도 노드는 예외 없이 완료돼야 한다(라이프사이클 잔류 아님 —
    인디케이터는 애초에 표시되지 않으며 그래프 진행이 막히지 않는다).
    """
    async def _boom(name, data, *, config=None):
        raise RuntimeError("dispatch failed")

    node = TN.GatewayToolNode(tools=[], deps=None, timeout=5.0)
    node._run_local = lambda name, args, state: json.dumps({"results": [1]})
    orig = TN.adispatch_custom_event
    TN.adispatch_custom_event = _boom
    try:
        out = asyncio.run(
            node({"messages": [_FakeAI([_tc("web_search", {"query": "q"})])]})
        )
    finally:
        TN.adispatch_custom_event = orig
    assert len(out["messages"]) == 1
    assert out["messages"][0].tool_call_id == "tc1"


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-q"]))
