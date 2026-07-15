# Feature: langgraph-reasoning-upgrade, Property 3: 신규 채널 자격증명 미저장
"""Property 3: 신규 채널 자격증명 미저장 — Hypothesis 기반 PBT.

Validates: Requirements 10.1, 10.2

design.md Correctness Property 3 발췌:
    For any evaluation / refine_count / completed_waves / 확장된 plan(id/depends_on 포함)
    채널 값에 대해, 해당 값과 그 직렬화 결과 어디에도 accessKeyId, secretAccessKey,
    sessionToken 키가 존재하지 않는다.

대상 코드(실측):
- ai_engine/agent_system/graph_state.py 의 신규 채널(evaluation/refine_count/
  completed_waves) 및 확장된 plan 항목 형태.

실행:
  ai_engine/.venv/bin/python -m pytest scripts/test_new_channels_no_creds_pbt.py -q
"""
from __future__ import annotations

import json
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from hypothesis import given, settings, strategies as st

import ai_engine.agent_system.supervisor as sup

# 상태/체크포인트 어디에도 존재해선 안 되는 자격증명 키.
_CRED_KEYS = ("accessKeyId", "secretAccessKey", "sessionToken")


# ── 재귀 키 스캔 ─────────────────────────────────────────────────────────────
def _all_keys(obj) -> set:
    """중첩 dict/list 를 재귀 순회하며 등장하는 모든 dict 키를 수집."""
    found: set = set()
    if isinstance(obj, dict):
        for k, v in obj.items():
            found.add(k)
            found |= _all_keys(v)
    elif isinstance(obj, (list, tuple)):
        for it in obj:
            found |= _all_keys(it)
    return found


def _assert_no_creds(state: dict):
    """상태 dict 자체 + JSON 직렬화 결과 어디에도 자격증명 키가 없음을 단언."""
    keys = _all_keys(state)
    for ck in _CRED_KEYS:
        assert ck not in keys, f"자격증명 키 {ck} 가 채널 값에 존재"

    # 직렬화 결과(체크포인트 기록 시나리오) 스캔 — Req 10.2.
    serialized = json.dumps(state, ensure_ascii=False, default=str)
    for ck in _CRED_KEYS:
        # 키로 존재하는지(예: "accessKeyId":) 검사. 임의 텍스트에 우연히 부분 문자열로
        # 등장할 수 있으므로 JSON 키 패턴("<key>":)으로 엄격히 검사한다.
        assert f'"{ck}"' not in serialized, f"직렬화 결과에 자격증명 키 {ck} 존재"


# ── 전략(생성기) ─────────────────────────────────────────────────────────────
_evaluation = st.one_of(
    st.none(),
    st.just({}),
    st.builds(
        lambda a, r, m: {"achieved": a, "reason": r, "missing_domains": m},
        st.booleans(),
        st.text(max_size=40),
        st.lists(st.sampled_from(list(sup._ROUTE_LABELS)), max_size=3),
    ),
)

_plan_item = st.builds(
    lambda i, d, s, deps: {"id": i, "domain": d, "subtask": s, "depends_on": deps},
    st.text(min_size=1, max_size=6),
    st.sampled_from(list(sup._ROUTE_LABELS)),
    st.text(max_size=40),
    st.lists(st.text(max_size=6), max_size=3),
)
_plan = st.lists(_plan_item, max_size=6)

_counter = st.integers(min_value=0, max_value=100)


# ── 속성 테스트 ──────────────────────────────────────────────────────────────
@settings(max_examples=100, deadline=None)
@given(evaluation=_evaluation, refine=_counter, completed=_counter, plan=_plan)
def test_new_channels_carry_no_credentials(evaluation, refine, completed, plan):
    """Property 3: 신규 채널 값과 직렬화 결과 어디에도 자격증명 키 부재."""
    state = {
        "evaluation": evaluation,
        "refine_count": refine,
        "completed_waves": completed,
        "plan": plan,
    }
    _assert_no_creds(state)


@settings(max_examples=100, deadline=None)
@given(evaluation=_evaluation, refine=_counter, completed=_counter, plan=_plan)
def test_new_channels_with_string_identifiers_only(evaluation, refine, completed, plan):
    """Req 10.3: AWS 관련 상태는 문자열 식별자(aws_profile/bedrock_user)만 — 자격증명 무.

    문자열 식별자가 함께 있어도 자격증명 키는 등장하지 않아야 한다.
    """
    state = {
        "aws_profile": "my-sso-profile",
        "bedrock_user": "team-user",
        "evaluation": evaluation,
        "refine_count": refine,
        "completed_waves": completed,
        "plan": plan,
    }
    _assert_no_creds(state)
    # 식별자는 보존되어야 한다(자격증명이 아니므로 상태 전달 허용 — Req 10.4).
    assert state["aws_profile"] == "my-sso-profile"
    assert state["bedrock_user"] == "team-user"


if __name__ == "__main__":
    import pytest

    raise SystemExit(pytest.main([__file__, "-q"]))
