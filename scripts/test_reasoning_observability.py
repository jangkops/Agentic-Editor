# Feature: langgraph-reasoning-upgrade, Observability: 구조화 로깅 검증
"""추론 노드(evaluator/planner/aggregate) 관측성 로깅 검증 (capsys, gateway mock).

목적:
- 프로덕션 운영 시 evaluator 재계획 루프·DAG Wave 진행·aggregate 종합의 진행 상황을
  추적할 수 있도록, 각 노드가 프로젝트 관례(print 기반, verify.py 의 `[verify] ...` 스타일)에
  맞춰 일관된 prefix 로 구조화 로그를 emit 하는지 검증한다.
- **보안 검증(자격증명·PII 미로깅)**: 로그 출력에 자격증명 지표(AKIA / secretAccessKey /
  sessionToken)와 원문 프롬프트가 포함되지 않는지 확인한다. 로깅은 refine_count / wave
  인덱스 / 도메인 라벨 / subtask 개수 같은 메타데이터만 남겨야 한다.

로그 prefix 계약:
- evaluator 노드: `[reasoning:evaluator]`
- planner 노드:   `[reasoning:planner]`
- aggregate 노드: `[reasoning:aggregate]`

실행:
  ai_engine/.venv/bin/python -m pytest scripts/test_reasoning_observability.py -q
"""
from __future__ import annotations

import asyncio
import os
import sys
from types import SimpleNamespace

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from langchain_core.messages import AIMessage, HumanMessage

import ai_engine.agent_system.supervisor as sup


# 로그에 절대 노출되면 안 되는 자격증명 지표 및 원문 프롬프트 마커.
_CRED_MARKERS = ("AKIA", "secretAccessKey", "sessionToken", "aws_secret", "aws_session")
# 프롬프트/subtask 전문에 심어두는 고유 마커 — 로그에 등장하면 원문 유출로 간주한다.
_SECRET_PROMPT_MARKER = "SENSITIVE_PROMPT_BODY_9F3A2"
_SECRET_SUBTASK_MARKER = "SENSITIVE_SUBTASK_BODY_7C1B4"


def _assert_no_leak(captured_text: str):
    """캡처된 로그 출력에 자격증명 지표·원문 프롬프트/subtask 가 없음을 단언."""
    for marker in _CRED_MARKERS:
        assert marker not in captured_text, f"자격증명 지표 유출: {marker}"
    assert _SECRET_PROMPT_MARKER not in captured_text, "원문 프롬프트 유출"
    assert _SECRET_SUBTASK_MARKER not in captured_text, "원문 subtask 유출"


# ── Gateway mock ─────────────────────────────────────────────────────────────
def _make_eval_model_cls(*, tool_args):
    class _FakeAI:
        def __init__(self, args):
            self.tool_calls = [{"name": "submit_evaluation", "args": args}]
            self.content = ""

    class _FakeBound:
        def __init__(self, args):
            self._args = args

        async def ainvoke(self, messages):
            return _FakeAI(self._args)

    class _FakeModel:
        def __init__(self, *a, **k):
            pass

        def bind_tools(self, *a, **k):
            return _FakeBound(tool_args)

    return _FakeModel


def _make_synth_model_cls(*, content):
    class _FakeModel:
        def __init__(self, *a, **k):
            pass

        def bind_tools(self, *a, **k):
            return self

        async def ainvoke(self, messages):
            return AIMessage(content=content)

    return _FakeModel


# ── evaluator 로깅 ───────────────────────────────────────────────────────────
def test_evaluator_emits_prefix_and_no_creds(capsys):
    """evaluator 실행 시 [reasoning:evaluator] 로그 emit + 자격증명/프롬프트 미유출."""
    orig = sup.GatewayChatModel
    sup.GatewayChatModel = _make_eval_model_cls(
        tool_args={"achieved": False, "reason": "PPT 미생성", "missing_domains": ["media"]}
    )
    try:
        deps = SimpleNamespace(
            gateway=object(),
            model_evaluator="m",
            aws_profile="AKIA-should-not-log",
        )
        node = sup.make_evaluator_node(deps)
        state = {
            "prompt": _SECRET_PROMPT_MARKER + " 코드 분석 후 PPT 생성",
            "messages": [AIMessage(content=_SECRET_SUBTASK_MARKER + " 분석 결과")],
            "refine_count": 0,
            "aws_profile": "AKIA-should-not-log",
        }
        asyncio.run(node(state))
    finally:
        sup.GatewayChatModel = orig

    out = capsys.readouterr().out
    assert "[reasoning:evaluator]" in out
    # 진입 로그(refine_count/cap) 존재
    assert "enter: refine_count=0" in out
    # 재계획 트리거 로그 존재(도메인 라벨 = 메타데이터)
    assert "replanning: refine_count 0->1" in out
    assert "media" in out  # 도메인 라벨은 허용된 메타데이터
    _assert_no_leak(out)


def test_evaluator_cap_reached_logs_terminating(capsys):
    """refine_count >= cap → 'cap reached, terminating' 로그, LLM 미호출."""
    deps = SimpleNamespace(gateway=object(), model_evaluator="m")
    node = sup.make_evaluator_node(deps)
    state = {"prompt": _SECRET_PROMPT_MARKER, "refine_count": sup.AE_MAX_REFINE}
    asyncio.run(node(state))
    out = capsys.readouterr().out
    assert "[reasoning:evaluator]" in out
    assert "cap reached, terminating" in out
    _assert_no_leak(out)


def test_evaluator_failure_logs_non_blocking(capsys):
    """LLM 실패 → 'eval failed (non-blocking), treating as achieved' 로그."""
    class _BoomModel:
        def __init__(self, *a, **k):
            pass

        def bind_tools(self, *a, **k):
            return self

        async def ainvoke(self, messages):
            raise RuntimeError("gateway boom")

    orig = sup.GatewayChatModel
    sup.GatewayChatModel = _BoomModel
    try:
        deps = SimpleNamespace(gateway=object(), model_evaluator="m")
        node = sup.make_evaluator_node(deps)
        asyncio.run(node({"prompt": _SECRET_PROMPT_MARKER, "refine_count": 0}))
    finally:
        sup.GatewayChatModel = orig

    out = capsys.readouterr().out
    assert "eval failed (non-blocking), treating as achieved" in out
    _assert_no_leak(out)


def test_evaluator_achieved_logs_verdict(capsys):
    """달성 판정 → 'verdict: achieved=True' 로그."""
    orig = sup.GatewayChatModel
    sup.GatewayChatModel = _make_eval_model_cls(
        tool_args={"achieved": True, "reason": "완료"}
    )
    try:
        deps = SimpleNamespace(gateway=object(), model_evaluator="m")
        node = sup.make_evaluator_node(deps)
        asyncio.run(node({"prompt": _SECRET_PROMPT_MARKER, "refine_count": 0}))
    finally:
        sup.GatewayChatModel = orig

    out = capsys.readouterr().out
    assert "verdict: achieved=True" in out
    _assert_no_leak(out)


# ── planner 로깅 ─────────────────────────────────────────────────────────────
def test_planner_new_plan_logs(capsys):
    """새 plan 생성 시 '[reasoning:planner] new plan: N subtasks, M waves' 로그."""
    # gateway=None → _make_plan 이 휴리스틱 폴백(단일 subtask) 반환, LLM 미호출.
    deps = SimpleNamespace(gateway=None, model_coding="m")
    node = sup.make_planner_node(deps)
    state = {"prompt": _SECRET_PROMPT_MARKER + " 일반 요청"}
    result = asyncio.run(node(state))
    assert "plan" in result  # 새 plan 생성 경로
    out = capsys.readouterr().out
    assert "[reasoning:planner]" in out
    assert "new plan:" in out
    assert "subtasks" in out and "waves" in out
    _assert_no_leak(out)


def test_planner_wave_advance_logs(capsys):
    """다중 Wave 진행 시 '[reasoning:planner] wave advance: c->c+1 of total' 로그."""
    os.environ["AE_ENABLE_DAG_PLANNER"] = "1"
    try:
        deps = SimpleNamespace(gateway=object(), model_coding="m")
        node = sup.make_planner_node(deps)
        # t1 이 t0 에 의존 → 2 Wave. completed_waves=0 이면 다음 Wave(1) 존재 → wave advance.
        state = {
            "prompt": _SECRET_PROMPT_MARKER,
            "plan": [
                {"id": "t0", "domain": "coding", "subtask": _SECRET_SUBTASK_MARKER, "depends_on": []},
                {"id": "t1", "domain": "media", "subtask": _SECRET_SUBTASK_MARKER, "depends_on": ["t0"]},
            ],
            "completed_waves": 0,
        }
        result = asyncio.run(node(state))
        assert result == {"completed_waves": 1}  # LLM 재호출 없이 Wave 진행
    finally:
        os.environ.pop("AE_ENABLE_DAG_PLANNER", None)

    out = capsys.readouterr().out
    assert "[reasoning:planner]" in out
    assert "wave advance: 0->1 of 2" in out
    _assert_no_leak(out)


# ── aggregate 로깅 ───────────────────────────────────────────────────────────
def test_aggregate_single_worker_logs_skip(capsys):
    """워커 1개 → '[reasoning:aggregate] single worker, skip synthesis' 로그."""
    deps = SimpleNamespace(gateway=object(), model_generator="m")
    node = sup.make_aggregate_node(deps)
    state = {
        "prompt": _SECRET_PROMPT_MARKER,
        "plan": [{"id": "t0", "domain": "coding", "subtask": _SECRET_SUBTASK_MARKER, "depends_on": []}],
        "messages": [AIMessage(content=_SECRET_SUBTASK_MARKER)],
    }
    result = asyncio.run(node(state))
    assert result == {}
    out = capsys.readouterr().out
    assert "[reasoning:aggregate]" in out
    assert "single worker, skip synthesis" in out
    _assert_no_leak(out)


def test_aggregate_synthesis_success_logs(capsys):
    """여러 워커 + LLM 성공 → '[reasoning:aggregate] synthesis ok' 로그."""
    orig = sup.GatewayChatModel
    sup.GatewayChatModel = _make_synth_model_cls(content="통합 최종 답변")
    try:
        deps = SimpleNamespace(gateway=object(), model_generator="m")
        node = sup.make_aggregate_node(deps)
        state = {
            "prompt": _SECRET_PROMPT_MARKER,
            "plan": [
                {"id": "t0", "domain": "coding", "subtask": _SECRET_SUBTASK_MARKER, "depends_on": []},
                {"id": "t1", "domain": "media", "subtask": _SECRET_SUBTASK_MARKER, "depends_on": []},
            ],
            "messages": [AIMessage(content=_SECRET_SUBTASK_MARKER), AIMessage(content="B")],
            "verified_files": [{"path": "deck.pptx", "absPath": "/abs/deck.pptx", "tool": "pptx"}],
        }
        result = asyncio.run(node(state))
        assert result.get("final_text") == "통합 최종 답변"
    finally:
        sup.GatewayChatModel = orig

    out = capsys.readouterr().out
    assert "[reasoning:aggregate]" in out
    assert "synthesis ok" in out
    _assert_no_leak(out)


def test_aggregate_synthesis_failure_logs(capsys):
    """여러 워커 + LLM 실패 → '[reasoning:aggregate] synthesis failed (non-blocking)' 로그."""
    class _BoomModel:
        def __init__(self, *a, **k):
            pass

        def bind_tools(self, *a, **k):
            return self

        async def ainvoke(self, messages):
            raise RuntimeError("gateway boom")

    orig = sup.GatewayChatModel
    sup.GatewayChatModel = _BoomModel
    try:
        deps = SimpleNamespace(gateway=object(), model_generator="m")
        node = sup.make_aggregate_node(deps)
        state = {
            "prompt": _SECRET_PROMPT_MARKER,
            "plan": [
                {"id": "t0", "domain": "coding", "subtask": _SECRET_SUBTASK_MARKER, "depends_on": []},
                {"id": "t1", "domain": "research", "subtask": _SECRET_SUBTASK_MARKER, "depends_on": []},
            ],
            "messages": [AIMessage(content=_SECRET_SUBTASK_MARKER), AIMessage(content="B")],
            "verified_files": [{"path": "r.md", "absPath": "/abs/r.md", "tool": "write"}],
        }
        result = asyncio.run(node(state))
        assert result == {}  # 비차단 폴백
    finally:
        sup.GatewayChatModel = orig

    out = capsys.readouterr().out
    assert "[reasoning:aggregate]" in out
    assert "synthesis failed (non-blocking)" in out
    _assert_no_leak(out)


if __name__ == "__main__":
    import pytest

    raise SystemExit(pytest.main([__file__, "-q"]))
