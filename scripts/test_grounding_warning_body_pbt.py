# Feature: reasoning-perf-reliability, Property 8: 상한 소진 후 미달이면 경고를 부가하되 본문을 보존한다 (가용성)
"""Property-based test: Grounding_Gate 상한 소진 후 미달 시 경고 부가·본문 보존.

Feature: reasoning-perf-reliability, Property 8: 상한 소진 후 미달이면 경고를 부가하되 본문을 보존한다 (가용성)
**Validates: Requirements 9.1, 9.2**

For any AE_MAX_REFINE 소진 후에도 근거 미달인 응답에 대해(reject 모드 off),
최종 final_text 는 원본 응답 본문을 부분 문자열로 보존하면서 근거 부족 경고 마커를
포함한다.

대상 코드(실측):
- ai_engine/agent_system/nodes/verify.py 의
  _apply_grounding_gate(state, final_text, answer_quality, base_out):
    · grounding_below 이 True 이고 grounding_refine_count >= AE_MAX_REFINE(상한 소진)
      이며 AE_GROUNDING_REJECT off 이면 → base_out.final_text 를
      "원문 본문 + ⚠️ 근거 부족 경고 마커" 로 확정한다(요구사항 9.1/9.2).

전제(플래그):
- AE_ENABLE_GROUNDING_GATE=1 (게이트 on — 호출자가 이미 확인했다는 precondition 충족)
- AE_GROUNDING_REJECT=0     (reject 모드 off — Property 8 범위)
- AE_MAX_REFINE=1           (상한 1)

근거 미달을 강제하기 위해 answer_quality.faithfulness.score 를 임계값(0.7) 미만으로,
degraded=False 로 구성한다(grounding_below → faithfulness_below_threshold → score < t).
state.grounding_refine_count 를 AE_MAX_REFINE 로 설정해 상한을 소진시킨다.

실행: ai_engine/.venv/bin/python -m pytest scripts/test_grounding_warning_body_pbt.py -q
Stack: Python 3.11+, hypothesis library.
"""
from __future__ import annotations

import os
import sys

# repo 루트를 import 경로에 추가해 ai_engine 패키지를 로드한다
# (test_grounding_below_pbt.py 의 sys.path 패턴을 미러링).
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from hypothesis import given, settings, strategies as st  # noqa: E402

from ai_engine.agent_system.nodes.verify import _apply_grounding_gate  # noqa: E402

# 근거 부족 경고 마커(구현 실측 문자열의 안정적 부분).
_WARNING_MARKER = "근거 부족"
_WARNING_ICON = "⚠️"

_THRESHOLD = 0.7  # AE_VERIFY_THRESHOLD 기본값.

# 원본 본문: 비어있지 않은 임의 텍스트(한/영/공백/특수문자 포함 가능).
_BODY = st.text(min_size=1, max_size=200)

# faithfulness 점수: 임계값(0.7) 미만을 강제 — 0.0 경계·임계 근방·일반 구간 포섭.
_BELOW_SCORES = st.one_of(
    st.sampled_from([0.0, 0.5, 0.69, 0.699999, 0.1, 0.6999]),
    st.floats(min_value=0.0, max_value=_THRESHOLD, exclude_max=True,
              allow_nan=False, allow_infinity=False),
)


def _set_gate_env():
    """Property 8 전제 플래그를 환경변수에 주입하고, 복원용 원본 값을 반환한다."""
    keys = ("AE_ENABLE_GROUNDING_GATE", "AE_GROUNDING_REJECT", "AE_MAX_REFINE")
    original = {k: os.environ.get(k) for k in keys}
    os.environ["AE_ENABLE_GROUNDING_GATE"] = "1"
    os.environ["AE_GROUNDING_REJECT"] = "0"
    os.environ["AE_MAX_REFINE"] = "1"
    return original


def _restore_env(original: dict):
    for k, v in original.items():
        if v is None:
            os.environ.pop(k, None)
        else:
            os.environ[k] = v


@settings(max_examples=100)
@given(body=_BODY, f_score=_BELOW_SCORES)
def test_warning_appended_body_preserved(body, f_score):
    """상한 소진·미달·reject off → final_text 가 원본 본문 보존 + 근거 부족 경고 포함."""
    original = _set_gate_env()
    try:
        max_refine = int(os.environ["AE_MAX_REFINE"])
        # 상한 소진: grounding_refine_count == AE_MAX_REFINE (g_rc >= max_refine).
        state = {"grounding_refine_count": max_refine}
        # 근거 미달 강제: faithfulness.score < 0.7, not degraded.
        answer_quality = {"faithfulness": {"score": f_score, "degraded": False}}
        base_out = {"final_text": body, "citations": {"verified": [], "unverified": []}}

        result = _apply_grounding_gate(state, body, answer_quality, base_out)
    finally:
        _restore_env(original)

    # 근거 미달이므로 게이트가 개입해 dict 를 반환해야 한다(None 아님).
    assert result is not None, (
        f"근거 미달인데 게이트가 통과(None) 반환: score={f_score}"
    )
    final_text = result.get("final_text", "")

    # (가용성) 원본 본문이 부분 문자열로 보존된다.
    assert body in final_text, (
        f"원본 본문이 보존되지 않음: body={body!r} not in final_text={final_text!r}"
    )
    # 근거 부족 경고 마커가 포함된다.
    assert _WARNING_MARKER in final_text, (
        f"근거 부족 경고 마커 누락: {final_text!r}"
    )
    assert _WARNING_ICON in final_text, (
        f"경고 아이콘(⚠️) 누락: {final_text!r}"
    )
    # refine 유도가 아니라 최종 확정이어야 한다(상한 소진 → refine 카운터 증가 없음).
    assert "grounding_refine_count" not in result, (
        f"상한 소진인데 refine 카운터가 증가됨: {result!r}"
    )
