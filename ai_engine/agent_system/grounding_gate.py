"""Grounding_Gate — 근거 강제 게이트의 순수 판정 + conditional edge selector.

Phase 2b 산출물(Task 8.1). design.md "Phase 2b — Grounding_Gate" 섹션을 반영한다.

이 모듈은 판정 로직(`grounding_below`)과 그래프 라우팅(`grounding_gate_selector`)만
제공한다. verify 노드 확장(`make_verify_node`)·서브그래프 배선(`build_domain_subgraph`)은
각각 Task 8.3 / 8.5 에서 다룬다(여기서 수정하지 않음).

불변 제약(요구사항 11):
- `boto3`/`anthropic`/`openai` 를 import 하지 않는다(신규 LLM 호출 없음 — 순수/라우팅만).
- 기존 자산 재사용(재구현 금지): `faithfulness_below_threshold`(`rag/answer_quality.py`),
  `local_grounding_score`(`rag/verifier.py`)의 '로컬 임베딩 grounding.score' 규약.
- 플래그·임계값은 호출 시점에 판독한다(테스트 토글 허용).

플래그(env):
  AE_ENABLE_GROUNDING_GATE : 게이트 마스터 스위치. 기본 off.
  AE_VERIFY_THRESHOLD      : 근거성 임계값(answer_quality 와 공유). 기본 0.7.
  AE_MAX_REFINE            : grounding refine 상한. 기본 1.
  AE_GROUNDING_REJECT      : reject 모드 플래그. 기본 off.

Requirements: 7.1, 7.2, 7.3, 7.4, 7.5, 10.2
"""
from __future__ import annotations

import os
from typing import Any, Optional

from langchain_core.messages import HumanMessage

from ai_engine.rag.answer_quality import faithfulness_below_threshold


def _truthy(v) -> bool:
    """env 값의 boolean 해석(answer_quality 관례와 정합)."""
    return str(v or "").strip().lower() in ("1", "true", "yes", "on")


def _verify_threshold(env: Optional[dict] = None) -> float:
    """근거성 임계값 판독(기본 0.7, answer_quality 와 공유)."""
    env = env if env is not None else os.environ
    try:
        return float(env.get("AE_VERIFY_THRESHOLD", "0.7"))
    except (TypeError, ValueError):
        return 0.7


def grounding_gate_enabled(env: Optional[dict] = None) -> bool:
    """게이트 마스터 스위치. 기본 off(미설정/빈값 → off, 무회귀)."""
    env = env if env is not None else os.environ
    return _truthy(env.get("AE_ENABLE_GROUNDING_GATE"))


def grounding_below(answer_quality: dict, env: Optional[dict] = None) -> bool:
    """근거 미달 판정(순수). 근거성이 임계값 미만이면 True(재생성 유도), 아니면 False(통과).

    판정 우선순위(design.md Phase 2b):
      1. faithfulness.score 가 있고 not degraded → 기존 `faithfulness_below_threshold()`.
      2. faithfulness 가 degraded/부재이고 grounding.score(로컬 임베딩)가 있으면
         `grounding.score < threshold`.
      3. 둘 다 산출 불가(degraded) → False (요구사항 7.4 — 근거 컨텍스트 유무와 무관히 통과).

    Invariant: 예외를 전파하지 않는다(비차단, 가용성 우선). 임계값은 호출 시점 판독.
    """
    env = env if env is not None else os.environ
    aq = answer_quality or {}

    # 1) faithfulness 가 유효(점수 존재 & not degraded)하면 기존 판정 로직 사용.
    f = aq.get("faithfulness") or {}
    f_score = f.get("score")
    f_degraded = bool(f.get("degraded"))
    if f_score is not None and not f_degraded:
        return faithfulness_below_threshold(aq, env)

    # 2) faithfulness 가 degraded/부재이고 로컬 grounding.score 가 있으면 임계 비교.
    g = aq.get("grounding") or {}
    g_score = g.get("score")
    if g_score is not None:
        try:
            return float(g_score) < _verify_threshold(env)
        except (TypeError, ValueError):
            return False

    # 3) 어느 신호도 산출 불가 → 통과(요구사항 7.4).
    return False


def grounding_gate_selector(state: Any, env: Optional[dict] = None) -> str:
    """conditional edge 라우터 → 'model' | 'done'.

    - 게이트 off → 'done'(무회귀, verify → END 동등).
    - messages 마지막이 refine 지시(HumanMessage)면 'model'(재생성), 아니면 'done'.

    verify 노드는 refine 을 유도할 때만 HumanMessage 를 append 하므로, 여기서
    마지막 메시지 타입만 보면 재생성 필요 여부가 결정된다(무한 루프 없음 —
    카운터는 verify 노드가 상한으로 관리).
    """
    env = env if env is not None else os.environ
    if not grounding_gate_enabled(env):
        return "done"

    messages = None
    try:
        messages = state.get("messages") if hasattr(state, "get") else None
    except Exception:  # noqa: BLE001 — state 접근 실패는 비차단.
        messages = None
    if not messages:
        return "done"

    last = messages[-1]
    return "model" if isinstance(last, HumanMessage) else "done"
