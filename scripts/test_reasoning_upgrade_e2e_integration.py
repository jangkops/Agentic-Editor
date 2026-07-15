# Feature: langgraph-reasoning-upgrade, Property 1 (runtime integration): Refine_Loop 유한 종료
"""build_parallel_top_graph 런타임 통합 e2e 검증 (mock GatewayChatModel, 네트워크 없음).

이 파일은 Property 1(유한 종료)의 **런타임 통합** 검증이다. 순수 함수/reducer 단위
테스트(test_*_pbt.py)와 달리, planner → plan_dispatch(Send fan-out) → 워커 서브그래프
→ aggregate → evaluator 전체 경로를 실제 `ainvoke` 로 끝까지 돌려, refine_count 리셋
무한루프(과거 GraphRecursionError, evaluator 20회 호출) 회귀를 영구히 가드한다.

배경(치명적 버그):
- refine_count 채널이 last-wins(_take_right)였을 때, Send 로 fan-out 된 워커 서브그래프
  (동일 GraphState 스키마 공유)가 병합 시 refine_count=0 을 emit → `_take_right(1,0)=0`
  으로 리셋 → `refine_count >= AE_MAX_REFINE` 가 절대 성립하지 않아 Refine_Loop 무한 반복.
- 수정: refine_count 를 monotonic MAX reducer(_take_max_int)로 교체(워커 0-echo 면역).
- 다중 Wave 완료 커서(completed_waves)도 동일 Send 워커 리셋에 견고해야 한다.

검증 시나리오:
  (a) evaluator 1회 미달 후 달성 → 정상 종료, final_text 존재, refine_count 정확.
  (b) 최악: evaluator 항상 미달 → recursion_limit(80) 안에서 graceful 종료(예외 없음),
      evaluator LLM 호출 수 ≤ AE_MAX_REFINE+1 (cap 유한 종료 증명). ← 핵심 회귀 가드
  (c) 다중 Wave(2 Wave DAG: t0 독립 research, t1 이 t0 의존 coding)가 순서대로 dispatch.
  (d) 최종 state 에 자격증명(AKIA/secretAccessKey/sessionToken) 부재.

전제: AE_ANSWER_QUALITY=0(품질 LLM off), project_path 미설정(RAG 스킵), 네트워크 없음.
실행: ai_engine/.venv/bin/python -m pytest scripts/test_reasoning_upgrade_e2e_integration.py -q
"""
from __future__ import annotations

import asyncio
import json
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import pytest
from langchain_core.messages import AIMessage, HumanMessage, SystemMessage

from ai_engine.agent_system import supervisor as S
from ai_engine.agent_system.subgraphs import _common as C
from ai_engine.agent_system.deps import GraphDeps


# ─────────────────────────────────────────────────────────────────────────────
# Mock GatewayChatModel — supervisor + subgraphs._common 두 모듈 모두 패치.
# 워커 content 응답은 실제 langchain AIMessage 로 반환한다(네트워크 없음).
# ─────────────────────────────────────────────────────────────────────────────
class _Recorder:
    """노드별 LLM 호출을 기록해 시나리오 검증에 사용한다."""

    def __init__(self):
        self.calls = []          # [{"kind": ..., ...}, ...] 호출 순서 보존
        self._eval_index = 0     # evaluator 호출 순번

    @property
    def evaluator_calls(self):
        return [c for c in self.calls if c["kind"] == "evaluator"]

    @property
    def worker_calls(self):
        return [c for c in self.calls if c["kind"] == "worker"]


class FakeChatModel:
    """GatewayChatModel 대체 — tool_choice / system prompt 로 호출 주체를 판별해 응답한다.

    - tool_choice="select_plan"        → planner: 미리 설정한 subtasks 반환.
    - tool_choice="submit_evaluation"  → evaluator: eval_script[i] 의 achieved 반환.
    - tool_choice None + system"편집자" → aggregate: 종합 텍스트 AIMessage.
    - tool_choice None + 그 외          → worker model: subtask 를 담은 AIMessage(도구호출 없음).
    """

    # 테스트가 시나리오별로 주입하는 클래스 레벨 제어값.
    rec: _Recorder | None = None
    plan_subtasks: list | None = None
    eval_script: list | None = None  # list[bool] — 각 evaluator 호출의 achieved

    def __init__(self, gateway=None, model_id="", **kwargs):
        self.model_id = model_id
        self._tool_choice = None

    def bind_tools(self, tools, tool_choice=None, **kwargs):
        self._tool_choice = tool_choice
        return self

    async def ainvoke(self, messages, **kwargs):
        # 이벤트 루프 양보(실제 async 경로와 유사한 스케줄링).
        await asyncio.sleep(0)
        rec = FakeChatModel.rec
        tc = self._tool_choice

        if tc == "select_plan":
            subs = FakeChatModel.plan_subtasks or [
                {"id": "t0", "domain": "chat", "subtask": "일반 응답", "depends_on": []}
            ]
            if rec is not None:
                rec.calls.append({"kind": "planner"})
            return AIMessage(
                content="",
                tool_calls=[{
                    "name": "select_plan",
                    "args": {"subtasks": subs},
                    "id": "plan-1",
                    "type": "tool_call",
                }],
            )

        if tc == "submit_evaluation":
            script = FakeChatModel.eval_script or []
            i = FakeChatModel.rec._eval_index if rec is not None else 0
            achieved = script[i] if i < len(script) else True
            if rec is not None:
                rec._eval_index += 1
                rec.calls.append({"kind": "evaluator", "achieved": achieved})
            args = {
                "achieved": achieved,
                "reason": "테스트 평가",
                "missing_domains": [] if achieved else ["coding"],
            }
            return AIMessage(
                content="",
                tool_calls=[{
                    "name": "submit_evaluation",
                    "args": args,
                    "id": "eval-1",
                    "type": "tool_call",
                }],
            )

        # tool_choice 없음 → aggregate 또는 worker 판별(system prompt 기준).
        sys_txt = "".join(
            str(m.content) for m in messages if isinstance(m, SystemMessage)
        )
        if "편집자" in sys_txt or "종합" in sys_txt:
            if rec is not None:
                rec.calls.append({"kind": "aggregate"})
            return AIMessage(content="여러 워커 산출물을 종합한 최종 답변입니다.")

        # worker model — 마지막 HumanMessage(subtask)를 추출해 기록.
        sub = ""
        for m in reversed(messages):
            if isinstance(m, HumanMessage):
                sub = str(m.content)
                break
        if rec is not None:
            rec.calls.append({"kind": "worker", "subtask": sub, "model": self.model_id})
        return AIMessage(content=f"[워커 산출물] {sub[:60]}")


# ─────────────────────────────────────────────────────────────────────────────
# 픽스처 / 헬퍼
# ─────────────────────────────────────────────────────────────────────────────
@pytest.fixture(autouse=True)
def _env_and_patch(monkeypatch):
    """플래그/품질 env 설정 + 두 모듈의 GatewayChatModel 을 FakeChatModel 로 패치."""
    monkeypatch.setenv("AE_ENABLE_EVALUATOR", "1")
    monkeypatch.setenv("AE_ENABLE_DAG_PLANNER", "1")
    monkeypatch.setenv("AE_MAX_REFINE", "2")
    monkeypatch.setenv("AE_ANSWER_QUALITY", "0")   # verify 품질 LLM off
    # 두 모듈 모두 패치(supervisor: planner/aggregate/evaluator, _common: worker model).
    monkeypatch.setattr(S, "GatewayChatModel", FakeChatModel)
    monkeypatch.setattr(C, "GatewayChatModel", FakeChatModel)
    # 각 테스트마다 recorder 초기화.
    FakeChatModel.rec = _Recorder()
    FakeChatModel.plan_subtasks = None
    FakeChatModel.eval_script = None
    yield
    FakeChatModel.rec = None


def _deps():
    # gateway 는 non-None 이어야 aggregate/evaluator 가 LLM 경로를 탄다(FakeChatModel 이 대체).
    return GraphDeps(gateway=object())


def _initial_state(prompt: str) -> dict:
    # project_path 미설정 → retrieve RAG 스킵. 자격증명 필드 없음.
    return {
        "prompt": prompt,
        "session_id": "sess-e2e",
        "messages": [HumanMessage(content=prompt)],
    }


async def _run(prompt: str, recursion_limit: int = 80) -> dict:
    compiled = S.build_parallel_top_graph(_deps())
    return await compiled.ainvoke(
        _initial_state(prompt), config={"recursion_limit": recursion_limit}
    )


def _no_credentials(obj) -> bool:
    """직렬화 문자열에 자격증명 지표가 전혀 없으면 True(비저장 확인)."""
    blob = json.dumps(obj, default=str, ensure_ascii=False)
    banned = ("AKIA", "secretAccessKey", "sessionToken", "aws_secret_access_key")
    return not any(tok in blob for tok in banned)


# ─────────────────────────────────────────────────────────────────────────────
# (a) evaluator 1회 미달 후 달성 → 정상 종료
# ─────────────────────────────────────────────────────────────────────────────
def test_refine_once_then_achieved_terminates_cleanly():
    FakeChatModel.plan_subtasks = [
        {"id": "t0", "domain": "coding", "subtask": "코드 리팩터", "depends_on": []},
        {"id": "t1", "domain": "research", "subtask": "관련 문서 조사", "depends_on": []},
    ]
    # 첫 평가 미달 → refine, 두 번째 평가 달성 → 종료.
    FakeChatModel.eval_script = [False, True]

    result = asyncio.run(_run("코드를 리팩터하고 문서를 조사하라"))

    rec = FakeChatModel.rec
    # evaluator 는 정확히 2회(미달 1 + 달성 1) 호출.
    assert len(rec.evaluator_calls) == 2, rec.calls
    # 최종 refine_count == 1 (1회 재계획).
    assert result.get("refine_count") == 1, result.get("refine_count")
    # 종합 최종 답변 존재.
    assert isinstance(result.get("final_text"), str) and result["final_text"].strip()


# ─────────────────────────────────────────────────────────────────────────────
# (b) 최악: evaluator 항상 미달 → graceful 유한 종료 (핵심 회귀 가드)
# ─────────────────────────────────────────────────────────────────────────────
def test_worst_case_evaluator_always_miss_terminates_within_cap():
    FakeChatModel.plan_subtasks = [
        {"id": "t0", "domain": "coding", "subtask": "코드 작성", "depends_on": []},
        {"id": "t1", "domain": "chat", "subtask": "설명", "depends_on": []},
    ]
    # 항상 미달 — 수정 전이라면 refine_count 리셋으로 무한루프(GraphRecursionError).
    FakeChatModel.eval_script = [False] * 50

    # recursion_limit=80 안에서 예외 없이 graceful 종료해야 한다.
    result = asyncio.run(_run("절대 만족 못하는 요청", recursion_limit=80))

    rec = FakeChatModel.rec
    max_refine = int(os.environ["AE_MAX_REFINE"])
    # cap 유한 종료 증명: evaluator LLM 호출 수 ≤ AE_MAX_REFINE + 1.
    assert len(rec.evaluator_calls) <= max_refine + 1, (
        f"evaluator {len(rec.evaluator_calls)}회 호출 — cap({max_refine}) 유한 종료 위반",
        rec.calls,
    )
    # refine_count 는 cap 을 초과하지 않는다.
    assert (result.get("refine_count") or 0) <= max_refine, result.get("refine_count")


def test_worst_case_does_not_raise_recursion_error():
    """무한루프면 GraphRecursionError 가 발생한다 — 발생하지 않아야 한다."""
    FakeChatModel.plan_subtasks = [
        {"id": "t0", "domain": "chat", "subtask": "응답", "depends_on": []},
    ]
    FakeChatModel.eval_script = [False] * 50
    # 예외가 나면 이 테스트가 실패한다(pytest 가 예외를 실패로 처리).
    result = asyncio.run(_run("항상 미달 단일 워커", recursion_limit=80))
    assert result is not None


# ─────────────────────────────────────────────────────────────────────────────
# (c) 다중 Wave: t0(research) → t1(coding) 순서대로 dispatch
# ─────────────────────────────────────────────────────────────────────────────
def test_multi_wave_dispatches_in_dependency_order():
    # 2 Wave DAG: t0 독립(research) 먼저, t1 이 t0 의존(coding) 나중.
    FakeChatModel.plan_subtasks = [
        {"id": "t0", "domain": "research", "subtask": "선행 리서치 작업", "depends_on": []},
        {"id": "t1", "domain": "coding", "subtask": "후행 코딩 작업", "depends_on": ["t0"]},
    ]
    FakeChatModel.eval_script = [True]  # 모든 Wave 완료 후 즉시 달성 → 종료.

    result = asyncio.run(_run("리서치 결과로 코드를 작성하라"))

    rec = FakeChatModel.rec
    workers = rec.worker_calls
    # 두 워커가 모두 실행되어야 한다.
    assert len(workers) == 2, rec.calls
    # research(t0)가 coding(t1)보다 먼저 dispatch(실행)되어야 한다(Wave 순서).
    order = [w["subtask"] for w in workers]
    assert "리서치" in order[0], f"첫 워커가 research 가 아님: {order}"
    assert "코딩" in order[1], f"둘째 워커가 coding 이 아님: {order}"
    # 다중 Wave 완료 후 종합/종료 정상.
    assert isinstance(result.get("final_text"), str)


# ─────────────────────────────────────────────────────────────────────────────
# (d) 최종 state 자격증명 부재
# ─────────────────────────────────────────────────────────────────────────────
def test_final_state_has_no_credentials():
    FakeChatModel.plan_subtasks = [
        {"id": "t0", "domain": "coding", "subtask": "작업", "depends_on": []},
        {"id": "t1", "domain": "research", "subtask": "조사", "depends_on": []},
    ]
    FakeChatModel.eval_script = [True]
    result = asyncio.run(_run("자격증명 미저장 확인"))
    assert _no_credentials(result), "최종 state 직렬화에 자격증명 지표가 존재함"


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-q"]))
