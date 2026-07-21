# Feature: reasoning-perf-reliability, Property 7: Grounding_Gate refine 는 유한하고 단조적이다
"""Property-based test: Grounding_Gate refine 의 유한·단조 종료.

Feature: reasoning-perf-reliability, Property 7: Grounding_Gate refine 는 유한하고 단조적이다
**Validates: Requirements 8.1, 8.2, 8.3, 11.5**

For any 지속적으로 근거 미달인 응답(faithfulness 점수가 임계값 미만, not degraded)에 대해:
    - `grounding_refine_count` 는 실행 전 구간에서 단조 비감소(monotonic non-decreasing).
    - 카운터는 `AE_MAX_REFINE` 를 절대 초과하지 않는다.
    - 게이트로 인한 model 재호출(refine 유도) 총 횟수는 `AE_MAX_REFINE` 이하로 유한 종료한다.
    - 루프는 유한 반복(guard = N+2) 안에서 종료한다.

대상 코드(실측 — 수정하지 않음):
- ai_engine/agent_system/nodes/verify.py 의 `_apply_grounding_gate(state, final_text,
  answer_quality, base_out)` 를 verify-gate 루프의 순수 step 함수로 사용한다.
    · grounding_below True & g_rc < AE_MAX_REFINE
        → base_out 에 refine 지시 HumanMessage + grounding_refine_count = g_rc+1 (refine 유도)
    · grounding_below True & 상한 소진(g_rc >= AE_MAX_REFINE)
        → final_text 를 경고/거절로 대체하되 grounding_refine_count 는 bump 하지 않음(종료)
    · grounding_below False → None(통과, 종료)
- ai_engine/agent_system/graph_state.py 의 `_take_max_int`(monotonic MAX reducer)로 카운터
  갱신을 병합해 Send fan-out echo/reset(워커의 0-emit) 면역을 함께 실증한다.

시뮬레이션(결정론적): state={"grounding_refine_count":0} 에서 시작해 반복적으로
`_apply_grounding_gate` 를 호출한다. refine 이 유도되면(반환 dict 에 grounding_refine_count
존재) 카운터를 `_take_max_int` 로 갱신하고(추가로 워커의 0-echo 를 섞어 리셋 면역 확인)
model 재호출 1회로 계수, 상한 소진/통과면(카운터 bump 없음 또는 None) 정지한다.

생성기(hypothesis strategies):
    - AE_MAX_REFINE ∈ {0,1,2,3}
    - faithfulness 점수: 임계값(0.7) 미만 [0.0, 0.699...] (지속 미달 보장)

실행: ai_engine/.venv/bin/python -m pytest scripts/test_grounding_refine_finite_pbt.py -q
Stack: Python 3.11+, hypothesis library.
"""
from __future__ import annotations

import os
import sys

# repo 루트를 import 경로에 추가해 ai_engine 패키지를 로드한다
# (test_grounding_below_pbt.py 와 동일 패턴).
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from hypothesis import given, settings, strategies as st  # noqa: E402

from ai_engine.agent_system.graph_state import _take_max_int  # noqa: E402
from ai_engine.agent_system.nodes.verify import _apply_grounding_gate  # noqa: E402

# AE_MAX_REFINE 후보: 0(비활성)·1(기본)·2·3.
_MAX_REFINE = st.sampled_from([0, 1, 2, 3])

# faithfulness 점수: 임계값 0.7 미만(경계 0.0 포함) — 지속적 근거 미달을 보장한다.
_BELOW_SCORES = st.one_of(
    st.sampled_from([0.0, 0.1, 0.5, 0.69, 0.699999]),
    st.floats(min_value=0.0, max_value=0.6999, allow_nan=False, allow_infinity=False),
)


@settings(max_examples=200)
@given(max_refine=_MAX_REFINE, f_score=_BELOW_SCORES)
def test_grounding_refine_finite_and_monotonic(max_refine, f_score):
    """지속 미달 응답에서 grounding_refine_count 는 단조·유한하며 model 재호출 <= AE_MAX_REFINE."""
    # 지속적으로 근거 미달인 answer_quality(faithfulness 유효 & score < threshold(0.7)).
    answer_quality = {"faithfulness": {"score": f_score, "degraded": False}}
    final_text = "원본 응답 본문(근거 미달 시나리오)."

    # 게이트 on / reject off / AE_MAX_REFINE=N 를 env 에 주입(테스트 후 원복).
    saved = {
        k: os.environ.get(k)
        for k in ("AE_ENABLE_GROUNDING_GATE", "AE_GROUNDING_REJECT", "AE_MAX_REFINE")
    }
    os.environ["AE_ENABLE_GROUNDING_GATE"] = "1"
    os.environ["AE_GROUNDING_REJECT"] = "0"
    os.environ["AE_MAX_REFINE"] = str(max_refine)
    # AE_VERIFY_THRESHOLD 는 기본 0.7 을 사용(점수를 그 미만으로 생성했으므로 지속 미달).
    try:
        state = {"grounding_refine_count": 0}
        counter_history = [state["grounding_refine_count"]]
        reinvocations = 0  # 게이트로 인한 model 재호출(refine 유도) 총 횟수.
        guard = max_refine + 2  # 유한 종료 보장을 위한 반복 상한.
        iterations = 0
        terminated = False

        while iterations <= guard:
            iterations += 1
            result = _apply_grounding_gate(state, final_text, answer_quality, {})

            if result is None:
                # 근거 통과 → 종료(지속 미달 시나리오에선 발생하지 않아야 함).
                terminated = True
                break

            if "grounding_refine_count" in result:
                # refine 유도 — model 1회 재호출. 카운터를 _take_max_int 로 병합한다.
                emitted = result["grounding_refine_count"]
                # Send fan-out echo/reset 면역 실증: 워커가 0 을 echo 해도 running max 유지.
                merged = _take_max_int(state["grounding_refine_count"], emitted)
                merged = _take_max_int(merged, 0)  # 워커 0-echo 를 섞어도 감소하지 않음.
                state = {**state, "grounding_refine_count": merged}
                counter_history.append(merged)
                reinvocations += 1
                # refine 유도 시엔 반드시 HumanMessage 지시가 함께 있어야 한다.
                assert result.get("messages"), "refine 유도인데 지시 메시지가 없음"
            else:
                # 상한 소진(경고/거절) — 카운터 bump 없이 종료.
                terminated = True
                break

        # (유한 종료) guard 내에서 반드시 종료해야 한다.
        assert terminated, (
            f"refine 루프가 guard({guard}) 안에서 종료하지 않음: "
            f"max_refine={max_refine}, history={counter_history}"
        )

        # (단조 비감소) 카운터는 절대 줄지 않는다.
        for prev, cur in zip(counter_history, counter_history[1:]):
            assert cur >= prev, (
                f"grounding_refine_count 단조성 위반: {prev} → {cur} "
                f"(history={counter_history})"
            )

        # (상한 준수) 카운터는 AE_MAX_REFINE 를 초과하지 않는다.
        assert max(counter_history) <= max_refine, (
            f"grounding_refine_count 가 AE_MAX_REFINE 초과: "
            f"max={max(counter_history)} > {max_refine} (history={counter_history})"
        )

        # (유한·최소 왕복) 게이트로 인한 model 재호출 총 횟수 <= AE_MAX_REFINE.
        #  지속 미달 시나리오에서는 정확히 N 회 유도된다.
        assert reinvocations <= max_refine, (
            f"model 재호출 횟수가 AE_MAX_REFINE 초과: {reinvocations} > {max_refine}"
        )
        assert reinvocations == max_refine, (
            f"지속 미달인데 refine 유도 횟수 불일치: {reinvocations} != {max_refine}"
        )
        # 최종 카운터는 정확히 N(모든 refine 소진).
        assert state["grounding_refine_count"] == max_refine, (
            f"최종 grounding_refine_count 불일치: "
            f"{state['grounding_refine_count']} != {max_refine}"
        )
    finally:
        for k, v in saved.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v
