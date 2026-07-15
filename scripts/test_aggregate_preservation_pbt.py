# Feature: langgraph-reasoning-upgrade, Property 4: 비차단 종합 및 verified_files 보존
"""Property 4: 비차단 종합 및 verified_files 보존 — Hypothesis 기반 PBT.

Validates: Requirements 3.4, 3.5, 3.6, 3.8, 7.3

design.md Correctness Property 4 발췌:
    For any 임의의 사전 상태와 임의의 verified_files 목록에 대해, aggregate/evaluator 의
    LLM 호출이 성공하든 실패/타임아웃하든, 그래프는 예외를 전파하지 않고 진행하며 결과
    상태의 verified_files 의 absPath 집합은 입력 verified_files 의 absPath 집합을 포함한다
    (삭제 없음).

대상 코드(실측):
- ai_engine/agent_system/supervisor.py 의 make_aggregate_node / make_evaluator_node.
  GatewayChatModel 을 mock 하여 성공/실패/타임아웃을 주입한다(네트워크 없음).

검증 방법:
- 노드는 부분 상태 dict 를 반환한다. verified_files 는 _merge_verified_files reducer 로
  병합되므로, "삭제 없음"은 (기존 verified_files) + (노드 반환 verified_files, 있으면)를
  reducer 로 병합한 결과 absPath 집합이 입력 집합을 포함함으로 검증한다.

실행:
  ai_engine/.venv/bin/python -m pytest scripts/test_aggregate_preservation_pbt.py -q
"""
from __future__ import annotations

import asyncio
import os
import sys
from types import SimpleNamespace

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from hypothesis import given, settings, strategies as st
from langchain_core.messages import AIMessage, HumanMessage

import ai_engine.agent_system.supervisor as sup
from ai_engine.agent_system.graph_state import _merge_verified_files


# ── Gateway mock (성공/예외/타임아웃 주입) ────────────────────────────────────
def _make_model_cls(mode: str, content: str = "종합 답변"):
    class _FakeModel:
        def __init__(self, *a, **k):
            pass

        def bind_tools(self, *a, **k):
            return self

        async def ainvoke(self, messages):
            if mode == "timeout":
                raise asyncio.TimeoutError()
            if mode == "error":
                raise RuntimeError("gateway boom")
            return AIMessage(content=content)

    return _FakeModel


def _apply_reducer(prev_vf, node_result):
    """노드 반환(부분 상태)을 reducer 로 병합해 최종 verified_files 를 계산."""
    returned = node_result.get("verified_files") if isinstance(node_result, dict) else None
    return _merge_verified_files(prev_vf, returned)


# ── 전략(생성기) ─────────────────────────────────────────────────────────────
@st.composite
def verified_files(draw):
    n = draw(st.integers(min_value=0, max_value=6))
    files = []
    for i in range(n):
        ap = draw(st.text(min_size=1, max_size=8).map(lambda s, i=i: f"/abs/{s}_{i}"))
        files.append(
            {"path": f"p{i}", "absPath": ap, "tool": draw(st.sampled_from(["edit", "pptx", "write"]))}
        )
    return files


@st.composite
def messages_list(draw):
    n = draw(st.integers(min_value=0, max_value=4))
    msgs = []
    for _ in range(n):
        txt = draw(st.text(max_size=15))
        msgs.append(draw(st.sampled_from([AIMessage(content=txt), HumanMessage(content=txt)])))
    return msgs


_modes = st.sampled_from(["success", "error", "timeout"])


def _absset(vf):
    return {f["absPath"] for f in vf}


# ── 속성 테스트: aggregate ────────────────────────────────────────────────────
@settings(max_examples=100, deadline=None)
@given(vf=verified_files(), msgs=messages_list(), mode=_modes, n_workers=st.integers(min_value=0, max_value=4))
def test_aggregate_preserves_verified_files(vf, msgs, mode, n_workers):
    """Property 4: aggregate 성공/실패 무관, verified_files absPath 집합 보존, 예외 미전파."""
    plan = [
        {"id": f"t{i}", "domain": "coding", "subtask": "x", "depends_on": []}
        for i in range(n_workers)
    ]
    state = {"prompt": "요청", "plan": plan, "messages": msgs, "verified_files": vf}

    orig = sup.GatewayChatModel
    sup.GatewayChatModel = _make_model_cls(mode)
    try:
        deps = SimpleNamespace(gateway=object(), model_generator="m")
        node = sup.make_aggregate_node(deps)
        result = asyncio.run(node(state))  # 예외 전파 없으면 통과(Req 3.8/7.3)
    finally:
        sup.GatewayChatModel = orig

    assert isinstance(result, dict)
    final_vf = _apply_reducer(vf, result)
    assert _absset(vf).issubset(_absset(final_vf))  # 삭제 없음(Req 3.4/3.6)


# ── 속성 테스트: evaluator ────────────────────────────────────────────────────
@settings(max_examples=100, deadline=None)
@given(vf=verified_files(), msgs=messages_list(), mode=_modes, refine=st.integers(min_value=0, max_value=5))
def test_evaluator_preserves_verified_files(vf, msgs, mode, refine):
    """Property 4: evaluator 성공/실패 무관, verified_files 보존, 예외 미전파."""
    state = {"prompt": "요청", "messages": msgs, "verified_files": vf, "refine_count": refine}

    orig = sup.GatewayChatModel
    sup.GatewayChatModel = _make_model_cls(mode)
    try:
        deps = SimpleNamespace(gateway=object(), model_evaluator="m")
        node = sup.make_evaluator_node(deps)
        result = asyncio.run(node(state))  # 예외 전파 없으면 통과
    finally:
        sup.GatewayChatModel = orig

    assert isinstance(result, dict)
    # evaluator 는 verified_files 를 반환하지 않음 → reducer 병합 후에도 입력 보존.
    final_vf = _apply_reducer(vf, result)
    assert _absset(vf).issubset(_absset(final_vf))


@settings(max_examples=100, deadline=None)
@given(vf=verified_files(), mode=_modes)
def test_gateway_none_is_nonblocking(vf, mode):
    """Property 4: gateway=None 이어도 aggregate/evaluator 비차단, verified_files 보존."""
    state = {
        "prompt": "요청",
        "plan": [
            {"id": "t0", "domain": "coding", "subtask": "a", "depends_on": []},
            {"id": "t1", "domain": "media", "subtask": "b", "depends_on": []},
        ],
        "messages": [AIMessage(content="A")],
        "verified_files": vf,
    }
    deps = SimpleNamespace(gateway=None, model_generator="m", model_evaluator="m")
    agg = asyncio.run(sup.make_aggregate_node(deps)(state))
    ev = asyncio.run(sup.make_evaluator_node(deps)(state))
    assert _absset(vf).issubset(_absset(_apply_reducer(vf, agg)))
    assert _absset(vf).issubset(_absset(_apply_reducer(vf, ev)))


if __name__ == "__main__":
    import pytest

    raise SystemExit(pytest.main([__file__, "-q"]))
