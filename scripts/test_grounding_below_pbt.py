# Feature: reasoning-perf-reliability, Property 6: Grounding_Gate 판정은 임계 비교와 degraded 통과를 정확히 만족한다
"""Property-based test: Grounding_Gate 근거 미달 판정(`grounding_below`)의 정확성.

Feature: reasoning-perf-reliability, Property 6: Grounding_Gate 판정은 임계 비교와 degraded 통과를 정확히 만족한다
**Validates: Requirements 7.2, 7.4**

For any answer_quality 메타데이터와 임계값 t 에 대해:
    - faithfulness 점수 s 가 존재하고 degraded 가 아니면 → grounding_below == (s < t)
    - 근거성 산출이 degraded(점수 부재)이면(faithfulness·grounding 모두 없음/degraded)
      → 근거 컨텍스트 유무와 무관하게 grounding_below == False (통과)

대상 코드(실측):
- ai_engine/agent_system/grounding_gate.py 의 grounding_below(answer_quality, env=None):
    · faithfulness.score 존재 & not degraded → faithfulness_below_threshold() (score < t)
    · faithfulness degraded/부재 & grounding.score 존재 → grounding.score < t
    · 둘 다 산출 불가 → False (요구사항 7.4)
- 임계값은 env["AE_VERIFY_THRESHOLD"](기본 0.7)로 호출 시점 판독.

생성기(hypothesis strategies)는 다음 edge case 를 포함한다:
    - faithfulness 점수 0.0 / 1.0 경계, 임계 근방, None(degraded)
    - degraded 플래그 on/off
    - grounding.score 존재/부재
    - 근거 컨텍스트(evidence) 유무 — degraded 통과가 컨텍스트와 무관함을 검증

실행: ai_engine/.venv/bin/python -m pytest scripts/test_grounding_below_pbt.py -q
Stack: Python 3.11+, hypothesis library.
"""
from __future__ import annotations

import os
import sys

# repo 루트를 import 경로에 추가해 ai_engine 패키지를 로드한다
# (grounding_gate 가 ai_engine.rag.answer_quality 를 import 하므로 루트가 필요).
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from hypothesis import given, settings, strategies as st  # noqa: E402

from ai_engine.agent_system.grounding_gate import grounding_below  # noqa: E402

# 점수: 0.0/1.0 경계·임계(0.7) 근방·일반 [0,1] 구간·None(degraded) 을 모두 포섭.
_SCORES = st.one_of(
    st.none(),
    st.sampled_from([0.0, 1.0, 0.5, 0.7, 0.69, 0.71, 0.699999, 0.700001]),
    st.floats(min_value=0.0, max_value=1.0, allow_nan=False, allow_infinity=False),
)

# 임계값: 경계·기본(0.7)·일반 구간 — 점수와 독립 추출해 임계 근방 비교를 폭넓게 검증.
_THRESHOLDS = st.one_of(
    st.sampled_from([0.0, 0.5, 0.7, 1.0]),
    st.floats(min_value=0.0, max_value=1.0, allow_nan=False, allow_infinity=False),
)

# 근거 컨텍스트: degraded 통과가 컨텍스트 유무와 무관함을 입증하기 위한 임의 텍스트 목록.
_EVIDENCE = st.lists(st.text(max_size=10), max_size=3)


@settings(max_examples=300)
@given(
    f_score=_SCORES,
    degraded=st.booleans(),
    g_score=_SCORES,
    evidence=_EVIDENCE,
    t=_THRESHOLDS,
)
def test_grounding_below_threshold_and_degraded(f_score, degraded, g_score, evidence, t):
    """grounding_below 가 임계 비교와 degraded 통과 규약을 정확히 만족한다."""
    # answer_quality 조립: faithfulness(score/degraded) + optional grounding + evidence context.
    faithfulness = {"degraded": degraded}
    if f_score is not None:
        faithfulness["score"] = f_score
    aq = {"faithfulness": faithfulness}
    if g_score is not None:
        aq["grounding"] = {"score": g_score, "method": "local-embedding-cosine"}
    # 근거 컨텍스트를 부착(게이트 판정에는 영향이 없어야 함 — 요구사항 7.4).
    if evidence:
        aq["_evidence_context"] = evidence

    # 임계값을 env 에 주입해 임계 비교를 실측(테스트 후 원복).
    original = os.environ.get("AE_VERIFY_THRESHOLD")
    os.environ["AE_VERIFY_THRESHOLD"] = repr(t)
    try:
        result = grounding_below(aq)
    finally:
        if original is None:
            os.environ.pop("AE_VERIFY_THRESHOLD", None)
        else:
            os.environ["AE_VERIFY_THRESHOLD"] = original

    faithfulness_valid = f_score is not None and not degraded
    if faithfulness_valid:
        # faithfulness 유효 → 정확히 (s < t).
        assert result == (f_score < t), (
            f"faithfulness 임계 비교 불일치: score={f_score}, t={t}, "
            f"result={result}, expected={f_score < t}"
        )
    elif g_score is not None:
        # faithfulness degraded/부재 이지만 로컬 grounding.score 존재 → (g_score < t).
        assert result == (g_score < t), (
            f"grounding 임계 비교 불일치: g_score={g_score}, t={t}, "
            f"result={result}, expected={g_score < t}"
        )
    else:
        # 어떤 근거 점수도 산출 불가(degraded) → 컨텍스트 유무와 무관하게 통과(False).
        assert result is False, (
            f"degraded 통과 위반: 근거 점수 부재인데 result={result} "
            f"(evidence={evidence!r})"
        )
