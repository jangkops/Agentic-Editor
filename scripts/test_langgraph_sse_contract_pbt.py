"""Property test — Property 6: SSE 이벤트 계약 호환.

Validates: Requirements 5.5, 5.6

대상 코드:
    ai_engine/agent_system/sse_bridge.py
    - graph_events_to_sse(compiled_graph, state, config, *, heartbeat_interval,
      total_timeout) -> async generator[str]
    - astream_events(v2) 이벤트를 기존 프론트 SSE 계약으로 매핑:
        * on_chat_model_stream        → {text}
        * on_tool_start / on_tool_end → {tool, status}
        * on_chain_start(서브그래프)  → {type: agent_start, taskId}
        * on_chain_end(서브그래프)    → {verifiedFiles?} → {type: agent_done, taskId}
      노드 예외 → {error}, 스트림 종료 시 항상 마지막에 `data: [DONE]`.

검증 속성 (Property 6 / 요구사항 5.5, 5.6):
    (1) 방출되는 각 SSE 이벤트(JSON payload)의 키는 기존 계약에서 허용된 키 집합
        ALLOWED_EVENT_KEYS = {text, thinking, tool, status, verifiedFiles, type,
        taskId, heartbeat, answerQuality, qualityPending, error} 의 **부분집합**이어야
        한다(계약 외 키 누출 없음). 특히 astream_events 원본의 input/output/chunk 등
        내부 키가 최상위로 새어 나가면 안 된다.
    (2) 스트림은 정상/에러/조기종료와 무관하게 반드시 `data: [DONE]` 센티널로 종료된다.

접근:
    - fake astream_events 제너레이터(다양한 이벤트 종류/순서를 hypothesis 로 생성)를
      sse_bridge 에 흘려보내 출력 SSE 라인을 수집한다.
    - 허용 키 집합(ALLOWED_EVENT_KEYS)은 sse_bridge 모듈에서 직접 import 하여
      실제 계약과 일치시킨다(하드코딩 추측 금지).
    - 네트워크/게이트웨이/실제 LangGraph 없음. fake 제너레이터는 유한 이벤트만 방출하고,
      heartbeat_interval / total_timeout 을 크게 잡아 heartbeat 로 인한 지연을 배제한다.
    - 전체 소비는 asyncio.wait_for 로 감싸 무한대기를 원천 차단한다.

실행:
    ai_engine/.venv/bin/python -m pytest scripts/test_langgraph_sse_contract_pbt.py -q
"""
import asyncio
import json
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from hypothesis import given, settings, strategies as st, HealthCheck  # noqa: E402
from langchain_core.messages import AIMessageChunk  # noqa: E402

from ai_engine.agent_system.sse_bridge import (  # noqa: E402
    graph_events_to_sse,
    ALLOWED_EVENT_KEYS,
    SUBGRAPH_NAMES,
)

_SUBGRAPHS = sorted(SUBGRAPH_NAMES)

# 소비 전체 상한(초) — fake 제너레이터는 즉시 yield 하므로 여유롭게. 무한대기 차단용.
_CONSUME_TIMEOUT = 15.0


# ─────────────────────────────────────────────────────────────────────────────
# fake compiled graph — astream_events(v2) 를 흉내내는 유한 async generator
# ─────────────────────────────────────────────────────────────────────────────
class _FakeGraph:
    """`astream_events(state, config=..., version="v2")` 를 제공하는 최소 fake.

    events: astream_events v2 이벤트 dict 리스트.
    raise_at: 해당 인덱스 이벤트 방출 직전에 RuntimeError 를 발생(노드 예외 시뮬레이션).
    """

    def __init__(self, events, raise_at=None):
        self._events = events
        self._raise_at = raise_at

    def astream_events(self, state, config=None, version="v2"):
        return self._agen()

    async def _agen(self):
        for i, evt in enumerate(self._events):
            if self._raise_at is not None and i == self._raise_at:
                raise RuntimeError("노드 실행 예외(시뮬레이션)")
            yield evt


# ─────────────────────────────────────────────────────────────────────────────
# 이벤트 생성기 — astream_events v2 스키마 (design.md 섹션 6 매핑 표 기준)
# ─────────────────────────────────────────────────────────────────────────────
# content: str 또는 멀티모달 list(dict 블록) — _extract_text 방어 경로까지 커버.
_content = st.one_of(
    st.text(max_size=20),
    st.lists(
        st.one_of(
            st.fixed_dictionaries({"type": st.just("text"), "text": st.text(max_size=10)}),
            st.text(max_size=8),
        ),
        max_size=3,
    ),
)

# 임의 이름(도구/체인 이름) — 서브그래프 이름과 겹칠 수도, 아닐 수도 있게.
_name = st.one_of(st.sampled_from(_SUBGRAPHS), st.text(max_size=12))

# verified_files output — path 있는 dict / path 없는 dict / 비-dict 혼재.
_vf_item = st.one_of(
    st.fixed_dictionaries({"path": st.text(min_size=1, max_size=15), "absPath": st.text(max_size=15)}),
    st.fixed_dictionaries({"tool": st.text(max_size=8)}),  # path 없음
    st.text(max_size=6),  # 비-dict
)
_chain_output = st.one_of(
    st.fixed_dictionaries({"verified_files": st.lists(_vf_item, max_size=4)}),
    st.dictionaries(st.text(max_size=6), st.text(max_size=6), max_size=3),  # verified_files 없음
    st.none(),
)


@st.composite
def _event(draw):
    """단일 astream_events v2 이벤트 dict 생성."""
    kind = draw(
        st.sampled_from(
            [
                "on_chat_model_stream",
                "on_tool_start",
                "on_tool_end",
                "on_chain_start",
                "on_chain_end",
                "on_chain_stream",       # 무시 대상
                "on_chat_model_start",   # 무시 대상
                "on_retriever_end",      # 무시 대상
            ]
        )
    )
    if kind == "on_chat_model_stream":
        chunk = AIMessageChunk(content=draw(_content))
        return {"event": kind, "name": draw(_name), "data": {"chunk": chunk}}
    if kind in ("on_tool_start", "on_tool_end"):
        # 원본에는 input/output 내부 키가 있으나 브리지는 무시해야 한다.
        return {"event": kind, "name": draw(_name),
                "data": {"input": {"x": 1}, "output": "raw-output"}}
    if kind == "on_chain_start":
        return {"event": kind, "name": draw(_name), "data": {"input": {"prompt": "q"}}}
    if kind == "on_chain_end":
        return {"event": kind, "name": draw(_name), "data": {"output": draw(_chain_output)}}
    # 무시 대상 이벤트 — data 에 임의 내부 키를 넣어 누출 여부를 검증.
    return {"event": kind, "name": draw(_name),
            "data": {"chunk": "ignored", "input": 1, "output": 2}}


_events = st.lists(_event(), max_size=25)


# ─────────────────────────────────────────────────────────────────────────────
# 소비 헬퍼
# ─────────────────────────────────────────────────────────────────────────────
def _consume(graph) -> list:
    """graph_events_to_sse 를 유한 시간 내에 소비해 SSE 라인 리스트 반환."""

    async def _run():
        outs = []
        agen = graph_events_to_sse(
            graph, {"prompt": "q"}, {"configurable": {"thread_id": "t"}},
            heartbeat_interval=1000.0,   # heartbeat 로 인한 간섭 배제
            total_timeout=1000.0,
        )
        async for line in agen:
            outs.append(line)
        return outs

    return asyncio.run(asyncio.wait_for(_run(), timeout=_CONSUME_TIMEOUT))


def _parse_payloads(lines: list):
    """SSE 라인 리스트 → (payload dict 리스트, done_count).

    각 라인은 `data: {json}\\n\\n` 또는 `data: [DONE]\\n\\n`.
    """
    payloads = []
    done_count = 0
    for line in lines:
        assert line.startswith("data: "), f"SSE 접두사 위반: {line!r}"
        assert line.endswith("\n\n"), f"SSE 종결 위반: {line!r}"
        body = line[len("data: "):].rstrip("\n")
        if body == "[DONE]":
            done_count += 1
            continue
        payloads.append(json.loads(body))
    return payloads, done_count


def _assert_contract(lines: list):
    """공통 계약 검증 — (1) 키 부분집합, (2) [DONE] 로 종료."""
    payloads, done_count = _parse_payloads(lines)

    # (1) 모든 payload 의 키가 허용 집합의 부분집합.
    for p in payloads:
        keys = set(p.keys())
        assert keys, f"빈 payload 방출: {p!r}"
        assert keys <= ALLOWED_EVENT_KEYS, (
            f"계약 외 키 누출: {keys - ALLOWED_EVENT_KEYS} in {p!r}"
        )

    # (2) 스트림은 정확히 마지막 한 번 [DONE] 로 종료.
    assert done_count == 1, f"[DONE] 센티널 개수 이상: {done_count}"
    assert lines[-1] == "data: [DONE]\n\n", f"마지막 이벤트가 [DONE] 아님: {lines[-1]!r}"


# ─────────────────────────────────────────────────────────────────────────────
# Property 6 — (1) 키 부분집합 + (2) [DONE] 종료 (정상 스트림)
# ─────────────────────────────────────────────────────────────────────────────
@settings(max_examples=80, deadline=None,
          suppress_health_check=[HealthCheck.function_scoped_fixture])
@given(events=_events)
def test_emitted_keys_subset_and_done_terminated(events):
    """임의 이벤트 시퀀스에 대해 키는 허용집합 부분집합, 스트림은 [DONE] 로 종료."""
    lines = _consume(_FakeGraph(events))
    _assert_contract(lines)


# ─────────────────────────────────────────────────────────────────────────────
# Property 6 — 노드 예외 발생 시에도 {error} 키 부분집합 + [DONE] 종료 (요구사항 5.7)
# ─────────────────────────────────────────────────────────────────────────────
@settings(max_examples=60, deadline=None,
          suppress_health_check=[HealthCheck.function_scoped_fixture])
@given(events=st.lists(_event(), min_size=1, max_size=25), raise_idx=st.integers(min_value=0, max_value=24))
def test_node_exception_still_terminates_with_done(events, raise_idx):
    """스트림 중 노드 예외가 나도 {error} emit 후 반드시 [DONE] 로 종료(키 부분집합 유지)."""
    raise_at = raise_idx % len(events)
    lines = _consume(_FakeGraph(events, raise_at=raise_at))
    _assert_contract(lines)
    # 예외 경로에서는 error payload 가 최소 1개 존재해야 한다.
    payloads, _ = _parse_payloads(lines)
    assert any("error" in p for p in payloads), "노드 예외인데 {error} 이벤트가 없음"


# ─────────────────────────────────────────────────────────────────────────────
# 예시 기반 — 각 이벤트 종류별 정확 매핑 확인(회귀 가드)
# ─────────────────────────────────────────────────────────────────────────────
def test_chat_model_stream_maps_to_text():
    ev = [{"event": "on_chat_model_stream", "name": "model",
           "data": {"chunk": AIMessageChunk(content="안녕")}}]
    payloads, done = _parse_payloads(_consume(_FakeGraph(ev)))
    assert done == 1
    assert payloads == [{"text": "안녕"}]


def test_empty_text_chunk_skipped():
    ev = [{"event": "on_chat_model_stream", "name": "model",
           "data": {"chunk": AIMessageChunk(content="")}}]
    payloads, done = _parse_payloads(_consume(_FakeGraph(ev)))
    assert done == 1
    assert payloads == []  # 빈 content 는 스킵(요구사항 5.1)


def test_tool_start_end_maps_to_tool_status():
    ev = [
        {"event": "on_tool_start", "name": "generate_pptx", "data": {"input": {"a": 1}}},
        {"event": "on_tool_end", "name": "generate_pptx", "data": {"output": "raw"}},
    ]
    payloads, done = _parse_payloads(_consume(_FakeGraph(ev)))
    assert done == 1
    assert payloads == [
        {"tool": "generate_pptx", "status": "running"},
        {"tool": "generate_pptx", "status": "done"},
    ]
    # input/output 내부 키가 최상위로 새지 않았는지 확인.
    for p in payloads:
        assert "input" not in p and "output" not in p


def test_subgraph_enter_exit_maps_to_agent_events_with_verified_files():
    ev = [
        {"event": "on_chain_start", "name": "coding", "data": {"input": {}}},
        {"event": "on_chain_end", "name": "coding",
         "data": {"output": {"verified_files": [
             {"path": ".generated/a.pptx", "absPath": "/abs/a.pptx"},
             {"tool": "x"},           # path 없음 → 제외
             "not-a-dict",            # 비-dict → 제외
         ]}}},
    ]
    payloads, done = _parse_payloads(_consume(_FakeGraph(ev)))
    assert done == 1
    assert payloads == [
        {"type": "agent_start", "taskId": "coding"},
        {"verifiedFiles": [".generated/a.pptx"]},
        {"type": "agent_done", "taskId": "coding"},
    ]


def test_non_subgraph_chain_events_ignored():
    # 서브그래프 이름이 아닌 chain 이벤트는 agent_* 로 변환되지 않는다.
    ev = [
        {"event": "on_chain_start", "name": "RunnableSequence", "data": {"input": {}}},
        {"event": "on_chain_end", "name": "RunnableSequence",
         "data": {"output": {"verified_files": [{"path": "z", "absPath": "/z"}]}}},
    ]
    payloads, done = _parse_payloads(_consume(_FakeGraph(ev)))
    assert done == 1
    assert payloads == []  # 서브그래프 아님 → 전부 무시


def test_empty_stream_still_done():
    payloads, done = _parse_payloads(_consume(_FakeGraph([])))
    assert done == 1
    assert payloads == []


if __name__ == "__main__":
    import pytest

    raise SystemExit(pytest.main([__file__, "-q"]))
