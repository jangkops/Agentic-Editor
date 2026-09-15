#!/usr/bin/env python3
"""실행 계약 검사(effect_ledger) 회귀 테스트 — "조용한 실패" 검출 보장.

── 배경 ──────────────────────────────────────────────────────────────────
이 리포에서 반복된 결함은 모두 **"기능이 켜져 있는데 조용히 아무것도 안 한다"** 형태였다.
개별 버그를 고쳐도 같은 형태가 재발했으므로, 요청이 끝날 때 **선언(설정·의도)** 과
**관측(도구 호출·제공자·결과 수)** 을 대조해 불일치를 사용자에게 올리도록 만들었다.

── 이 테스트가 고정하는 성질 ──────────────────────────────────────────────
  A. 실측 사고 재현 — 오늘 발견한 실패 형태를 실제로 검출한다
       1) 조사 요청이 도구 없는 워커로 라우팅돼 검색 0회
       2) 게이트가 조용히 off 인 상태로 조사 요청
       3) 제공자 키가 없어 결과 0건인데 도구가 정상 반환
  B. 무노이즈 — 조사 의도가 없으면 리서치 불일치를 판정하지 않는다
       (항상 뜨는 경고는 무시되므로, 이 성질이 깨지면 장치가 무력화된다)
  C. 비차단 — 어떤 입력에도 예외를 전파하지 않는다
  D. 자격증명 미포함(P9) — 산출물에 키·토큰 흔적이 없다
  E. SSE 배선 — effectSummary 가 [DONE] 직전 정확히 1회, 허용 키로 방출된다

네트워크·게이트웨이·LLM 을 쓰지 않는다(순수 함수 + 가짜 이벤트 스트림).
"""
import asyncio
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from ai_engine.agent_system.effect_ledger import (  # noqa: E402
    MISMATCH_MESSAGES,
    EffectCollector,
    build_effect_summary,
    declared_from_env,
    detect_mismatches,
    should_emit_summary,
)
from ai_engine.agent_system.sse_bridge import (  # noqa: E402
    ALLOWED_EVENT_KEYS,
    graph_events_to_sse,
)

# 게이트가 열린 상태의 "선언". 조사 의도는 각 테스트가 지정한다.
_GATE_OPEN = {
    "researchEnabled": True,
    "researchConsent": True,
    "webProviders": ["tavily", "exa", "brave"],
    "academicProviders": ["openalex", "europepmc"],
}


def _declared(intent: bool, **over) -> dict:
    return {**_GATE_OPEN, "researchIntent": intent, **over}


def _codes(mismatches) -> list:
    return [m["code"] for m in mismatches]


# =========================================================================== #
# A. 실측 사고 재현
# =========================================================================== #
def test_detects_research_intent_routed_to_toolless_worker() -> None:
    """조사 요청이 도구 없는 워커로 가서 검색 0회 → 검출한다.

    실측: planner LLM 이 "GLP-1 간독성 논문을 DOI와 함께" 요청을 chat/coding 으로
    라우팅했고, 그 워커에 web_search 가 없어 모델이 "조사하겠습니다"만 남기고 종료했다.
    """
    c = EffectCollector()
    c.on_agent_start("chat")

    codes = _codes(detect_mismatches(_declared(True), c.observed()))
    assert "research_intent_no_search" in codes, codes


def test_detects_gate_silently_off() -> None:
    """게이트가 조용히 off 인데 조사 요청 → 검출하고, 중복 경고는 내지 않는다.

    실측: settings.json 경로를 못 찾아 UI 를 다 켜도 게이트가 off 로 유지됐다.
    """
    c = EffectCollector()
    c.on_agent_start("research")

    m = detect_mismatches(
        _declared(True, researchEnabled=False, researchConsent=False), c.observed()
    )
    assert _codes(m) == ["research_intent_gate_off"], _codes(m)


def test_detects_zero_results_with_provider_reason() -> None:
    """키가 없어 0건인데 도구가 정상 반환 → 검출하고 사유를 조치 가능한 문구로 준다.

    실측: web_search 가 `{results: [], count: 0}` 만 돌려줘 모델·UI 가 원인을 몰랐다.
    """
    c = EffectCollector()
    c.on_tool_start("web_search")
    c.on_search_status(
        {
            "phase": "end",
            "kind": "web",
            "providers": ["tavily", "exa"],
            "status": "ok",
            "count": 0,
            "provider_errors": [
                {"provider": "tavily", "error": "missing_credential"},
                {"provider": "exa", "error": "missing_credential"},
            ],
        }
    )
    m = detect_mismatches(_declared(True), c.observed())
    codes = _codes(m)
    assert "search_ran_but_no_results" in codes, codes
    assert "search_provider_failed" in codes, codes
    detail = next(x["detail"] for x in m if x["code"] == "search_provider_failed")
    assert "tavily" in detail and "API 키 미설정" in detail, detail


def test_aggregate_error_not_double_reported() -> None:
    """최상위 error 는 제공자별 사유의 집계값 — 둘 다 있으면 한 번만 보고한다."""
    c = EffectCollector()
    c.on_tool_start("web_search")
    c.on_search_status(
        {
            "phase": "end",
            "providers": ["exa"],
            "status": "ok",
            "count": 0,
            "error": "missing_credential",          # 집계값
            "provider_errors": [{"provider": "exa", "error": "missing_credential"}],
        }
    )
    errs = c.observed()["providerErrors"]
    assert errs == [{"provider": "exa", "error": "missing_credential"}], errs


def test_aggregate_error_absorbed_when_no_per_provider() -> None:
    """제공자별 정보가 없으면 최상위 error 라도 사유를 잃지 않는다."""
    c = EffectCollector()
    c.on_tool_start("web_search")
    c.on_search_status(
        {"phase": "end", "providers": ["tavily"], "status": "ok", "count": 0,
         "error": "timeout"}
    )
    errs = c.observed()["providerErrors"]
    assert errs == [{"provider": "", "error": "timeout"}], errs


def test_detects_declared_providers_never_called() -> None:
    """설정에서 고른 제공자가 하나도 동원되지 않으면 검출한다."""
    c = EffectCollector()
    c.on_tool_start("web_search")
    c.on_search_status(
        {"phase": "end", "providers": ["some_other_provider"], "status": "ok", "count": 2}
    )
    codes = _codes(detect_mismatches(_declared(True), c.observed()))
    assert "declared_providers_never_called" in codes, codes


# =========================================================================== #
# B. 무노이즈 — 이 성질이 깨지면 경고가 무시되어 장치가 죽는다
# =========================================================================== #
def test_no_warning_without_research_intent() -> None:
    """조사 의도가 없으면(코딩 질문 등) 리서치 불일치를 판정하지 않는다."""
    c = EffectCollector()
    c.on_agent_start("coding")
    c.on_tool_start("read_file")
    c.on_tool_start("write_file")

    assert detect_mismatches(_declared(False), c.observed()) == []


def test_no_warning_on_successful_search() -> None:
    """검색이 정상적으로 결과를 낸 경우 불일치가 없다."""
    c = EffectCollector()
    c.on_agent_start("research")
    c.on_tool_start("search_papers")
    c.on_search_status(
        {"phase": "end", "providers": ["openalex"], "status": "ok", "count": 5}
    )
    s = build_effect_summary(_declared(True), c.observed())
    assert s["ok"] is True and s["mismatches"] == []


def test_result_count_unknown_suppresses_zero_result_warning() -> None:
    """count 를 관측하지 못했으면 "0건" 판정을 보류한다(과거 payload 호환)."""
    c = EffectCollector()
    c.on_tool_start("web_search")
    c.on_search_status({"phase": "end", "providers": ["tavily"], "status": "ok"})
    assert c.observed()["searchResults"] is None
    assert "search_ran_but_no_results" not in _codes(
        detect_mismatches(_declared(True), c.observed())
    )


def test_fetch_content_counts_as_external_lookup() -> None:
    """fetch_content 도 외부 조회로 계수한다(검색 0회 오탐 방지)."""
    c = EffectCollector()
    c.on_tool_start("fetch_content")
    assert c.search_tool_calls() == 1
    assert "research_intent_no_search" not in _codes(
        detect_mismatches(_declared(True), c.observed())
    )


# =========================================================================== #
# C. 비차단 — 감시 장치가 본체를 막지 않는다
# =========================================================================== #
def test_never_raises_on_malformed_input() -> None:
    """비정상 입력에도 예외를 전파하지 않는다."""
    c = EffectCollector()
    for bad in (None, 123, "str", [], {}, {"phase": None}, {"providers": "notalist"}):
        c.on_search_status(bad)
        c.on_tool_start(bad)
        c.on_agent_start(bad)
        c.on_verified_files(bad)
    assert isinstance(c.observed(), dict)

    for d in (None, 1, "x", [], {}):
        for o in (None, 1, "x", [], {}):
            assert isinstance(detect_mismatches(d, o), list)
            assert isinstance(build_effect_summary(d, o), dict)


def test_declared_from_env_never_raises() -> None:
    """설정 로드·의도 판정이 실패해도 보수적 기본값을 돌려준다."""
    for p in (None, "", 123, "GLP-1 논문 찾아줘"):
        d = declared_from_env(p)
        assert set(d) >= {
            "researchEnabled", "researchConsent", "webProviders",
            "academicProviders", "researchIntent",
        }
        assert isinstance(d["researchIntent"], bool)


def test_summary_is_json_serializable() -> None:
    """산출물은 SSE 로 내려보낼 수 있어야 한다(JSON 직렬화 가능)."""
    c = EffectCollector()
    c.on_tool_start("web_search")
    c.on_search_status({"phase": "end", "providers": ["tavily"], "count": 0})
    json.dumps(build_effect_summary(_declared(True), c.observed()), ensure_ascii=False)


def test_every_code_has_message() -> None:
    """모든 불일치 코드에 사용자 문구가 있다(코드만 노출되는 일 방지)."""
    c = EffectCollector()
    c.on_tool_start("web_search")
    c.on_search_status(
        {"phase": "end", "providers": ["zzz"], "count": 0, "error": "timeout"}
    )
    for m in detect_mismatches(_declared(True), c.observed()):
        assert m["code"] in MISMATCH_MESSAGES, m["code"]
        assert m["message"] and m["message"] != m["code"], m


# =========================================================================== #
# D. 자격증명 미포함 (P9)
# =========================================================================== #
def test_no_credential_leak_in_summary() -> None:
    """수집·판정 산출물에 키 흔적이 없다."""
    secret = "tvly-SECRET-abcdef0123456789"
    c = EffectCollector()
    c.on_tool_start("web_search")
    # 방어적으로 오염된 payload 를 흘려보내도 산출물에 값이 실리지 않아야 한다.
    c.on_search_status(
        {
            "phase": "end",
            "providers": ["tavily"],
            "status": "ok",
            "count": 0,
            "provider_errors": [{"provider": "tavily", "error": "missing_credential"}],
        }
    )
    blob = json.dumps(
        build_effect_summary(_declared(True), c.observed()), ensure_ascii=False
    )
    assert secret not in blob
    for marker in ("secret", "apikey", "api_key", "token"):
        assert marker not in blob.lower(), marker


# =========================================================================== #
# E. SSE 배선
# =========================================================================== #
class _FakeGraph:
    """astream_events 를 흉내내는 최소 그래프."""

    def __init__(self, events):
        self._events = events

    def astream_events(self, state, config=None, version=None):
        async def _gen():
            for e in self._events:
                yield e

        return _gen()


def _run_sse(events, prompt: str) -> list:
    async def _main():
        return [
            line
            async for line in graph_events_to_sse(_FakeGraph(events), {"prompt": prompt}, {})
        ]

    return asyncio.run(_main())


def _payloads(lines) -> list:
    out = []
    for line in lines:
        body = line[len("data: "):].strip()
        if body and body != "[DONE]":
            out.append(json.loads(body))
    return out


def test_sse_emits_effect_summary_exactly_once_before_done() -> None:
    """방출될 때는 [DONE] 직전 정확히 1회다(중복 방출 금지).

    방출 조건은 should_emit_summary 가 정한다 — 불일치가 있거나 외부 조회 활동이
    관측됐을 때만이다. 그래서 이 테스트는 **방출되는 입력**을 쓴다(조사 의도 + 검색 0회).
    무조건 방출로 되돌리면 test_sse_silent_when_nothing_to_report 가 실패한다.
    """
    os.environ["AE_ENABLE_WEB_RESEARCH"] = "1"
    os.environ["AE_RESEARCH_CONSENT"] = "1"
    try:
        lines = _run_sse(
            [{"event": "on_chain_start", "name": "chat", "data": {}}],
            "GLP-1 간독성 논문 DOI 찾아줘",
        )
    finally:
        os.environ.pop("AE_ENABLE_WEB_RESEARCH", None)
        os.environ.pop("AE_RESEARCH_CONSENT", None)
    assert lines[-1] == "data: [DONE]\n\n", lines[-1]
    summaries = [p for p in _payloads(lines) if "effectSummary" in p]
    assert len(summaries) == 1, f"effectSummary {len(summaries)}회 방출"
    assert "effectSummary" in lines[-2], lines[-2]


def test_sse_silent_when_nothing_to_report() -> None:
    """보고할 것이 없으면 **아무 것도 방출하지 않는다**(프로토콜 수준 무노이즈).

    처음 구현은 모든 스트림에 ok=True 요약을 실었다. 프론트는 불일치가 없으면 라이브
    로그만 찍고 끝내므로 사용자에게 보여줄 것이 없는데 바이트만 늘었고, 페이로드 목록을
    정확 동일로 단정하던 SSE 계약 테스트 6건이 깨졌다. 계약을 느슨하게 푸는 대신
    방출을 좁혔다 — 이 테스트가 그 결정을 고정한다.
    """
    lines = _run_sse([{"event": "on_chain_start", "name": "chat", "data": {}}], "안녕")
    assert lines[-1] == "data: [DONE]\n\n", lines[-1]
    summaries = [p for p in _payloads(lines) if "effectSummary" in p]
    assert summaries == [], f"보고할 것이 없는데 방출됨: {summaries}"


def test_sse_emits_when_search_ran_even_without_mismatch() -> None:
    """불일치가 없어도 외부 조회가 돌았으면 방출한다 — 성공의 근거도 남긴다."""
    os.environ["AE_ENABLE_WEB_RESEARCH"] = "1"
    os.environ["AE_RESEARCH_CONSENT"] = "1"
    try:
        lines = _run_sse(
            [
                {"event": "on_chain_start", "name": "research", "data": {}},
                {"event": "on_tool_start", "name": "web_search", "data": {}},
                {
                    "event": "on_custom_event",
                    "name": "search_status",
                    # 실제 계약 형태 — providers 는 **복수 리스트**다
                    # (test_research_search_indicator_lifecycle_pbt.py::_END_KEYS).
                    # 단수 "provider" 로 쓰면 수집기가 제공자를 못 읽어 통과해도 의미가 없다.
                    "data": {"phase": "end", "kind": "web", "providers": ["tavily"],
                             "query_summary": "q", "status": "ok", "count": 5},
                },
                {"event": "on_chain_end", "name": "research", "data": {"output": {}}},
            ],
            "transformer attention 최신 논문 찾아줘",
        )
    finally:
        os.environ.pop("AE_ENABLE_WEB_RESEARCH", None)
        os.environ.pop("AE_RESEARCH_CONSENT", None)
    s = next((p["effectSummary"] for p in _payloads(lines) if "effectSummary" in p), None)
    assert s is not None, "검색이 돌았는데 요약이 방출되지 않았다"
    assert s["observed"]["searchToolCalls"] == 1
    assert s["observed"]["searchResults"] == 5
    # 제공자가 실제로 읽혔는지 — 이게 비면 declared_providers_never_called 가 오탐한다
    assert s["observed"]["searchProviders"] == ["tavily"], s["observed"]
    assert s["mismatches"] == [], s["mismatches"]


def test_sse_effect_summary_key_is_allowed() -> None:
    """방출 키가 SSE 허용 키 집합에 등재돼 있다(Property 6 계약)."""
    assert "effectSummary" in ALLOWED_EVENT_KEYS
    lines = _run_sse([{"event": "on_chain_start", "name": "chat", "data": {}}], "q")
    for p in _payloads(lines):
        assert set(p) <= ALLOWED_EVENT_KEYS, f"미허용 키: {set(p) - ALLOWED_EVENT_KEYS}"


def test_sse_collects_routing_mismatch_end_to_end() -> None:
    """조사 요청 + 도구 없는 워커 → effectSummary 에 불일치가 실린다."""
    os.environ["AE_ENABLE_WEB_RESEARCH"] = "1"
    os.environ["AE_RESEARCH_CONSENT"] = "1"
    try:
        lines = _run_sse(
            [
                {"event": "on_chain_start", "name": "chat", "data": {}},
                {"event": "on_chain_end", "name": "chat", "data": {"output": {}}},
            ],
            "GLP-1 간독성 논문 DOI 찾아줘",
        )
        s = next(p["effectSummary"] for p in _payloads(lines) if "effectSummary" in p)
        assert s["ok"] is False
        assert "research_intent_no_search" in _codes(s["mismatches"])
        assert s["observed"]["domains"] == ["chat"]
        assert s["observed"]["searchToolCalls"] == 0
    finally:
        os.environ.pop("AE_ENABLE_WEB_RESEARCH", None)
        os.environ.pop("AE_RESEARCH_CONSENT", None)


def test_sse_emits_summary_even_on_stream_error() -> None:
    """스트림이 예외로 끝나도 요약은 방출된다(진단 근거 보존)."""

    class _BoomGraph:
        def astream_events(self, state, config=None, version=None):
            async def _gen():
                yield {"event": "on_chain_start", "name": "research", "data": {}}
                raise RuntimeError("boom")

            return _gen()

    async def _main():
        return [
            line
            async for line in graph_events_to_sse(_BoomGraph(), {"prompt": "논문 찾아줘"}, {})
        ]

    lines = asyncio.run(_main())
    payloads = _payloads(lines)
    assert any("error" in p for p in payloads), "에러 이벤트가 없다"
    assert any("effectSummary" in p for p in payloads), "에러 시 요약이 누락됐다"
    assert lines[-1] == "data: [DONE]\n\n"


def test_sse_unchanged_for_non_research_prompt() -> None:
    """순수 코딩 요청의 스트림은 **변하지 않는다**(무회귀·무노이즈).

    검색 도구가 아닌 도구만 쓰였고 불일치도 없으므로 effectSummary 는 실리지 않고,
    기존 이벤트 목록도 그대로다. 이 성질이 깨지면 코딩 작업마다 잡음이 붙는다.
    """
    lines = _run_sse(
        [
            {"event": "on_chain_start", "name": "coding", "data": {}},
            {"event": "on_tool_start", "name": "read_file", "data": {}},
            {"event": "on_chain_end", "name": "coding", "data": {"output": {}}},
        ],
        "이 파일 버그 고쳐줘",
    )
    payloads = _payloads(lines)
    assert not any("effectSummary" in p for p in payloads), \
        f"코딩 요청에 잡음이 붙었다: {[p for p in payloads if 'effectSummary' in p]}"
    # 기존 계약 이벤트는 그대로 흐른다
    assert {"type": "agent_start", "taskId": "coding"} in payloads
    assert {"tool": "read_file", "status": "running"} in payloads
    assert lines[-1] == "data: [DONE]\n\n"


def test_should_emit_summary_is_pure_and_total() -> None:
    """방출 판정은 어떤 입력에도 예외를 던지지 않고 bool 을 돌려준다."""
    for bad in (None, 0, "", [], {}, {"observed": None}, {"observed": []},
                {"mismatches": None}, {"mismatches": []}, object()):
        assert isinstance(should_emit_summary(bad), bool)
    # 불일치가 있으면 활동이 없어도 방출한다 — 이 장치의 존재 이유
    assert should_emit_summary({"mismatches": [{"code": "x"}], "observed": {}}) is True
    # 활동이 있으면 불일치가 없어도 방출한다
    assert should_emit_summary({"mismatches": [], "observed": {"searchToolCalls": 1}}) is True
    assert should_emit_summary({"mismatches": [], "observed": {"searchRuns": 1}}) is True
    assert should_emit_summary({"mismatches": [], "observed": {"searchProviders": ["tavily"]}}) is True
    assert should_emit_summary(
        {"mismatches": [], "observed": {"providerErrors": [{"provider": "exa"}]}}) is True
    assert should_emit_summary({"mismatches": [], "observed": {"searchResults": 0}}) is True
    # 활동도 불일치도 없으면 침묵
    assert should_emit_summary(
        {"mismatches": [], "observed": {"toolCalls": {"read_file": 3}, "searchToolCalls": 0,
                                        "searchRuns": 0, "searchProviders": [],
                                        "providerErrors": [], "searchResults": None}}) is False


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
