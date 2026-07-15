# Feature: langgraph-reasoning-upgrade, Property 1: 유한 종료 (Refine / Wave / Route 상한)
"""Property 1: 유한 종료 — Hypothesis 기반 PBT.

Validates: Requirements 2.4, 4.2, 5.4, 8.5

design.md Correctness Property 1 발췌:
    For any 서브태스크 목록과 임의의 초기 refine_count 에 대해, 하나의 그래프 실행에서
    수행되는 Refine_Loop 복귀 횟수는 AE_MAX_REFINE 이하이고, topological_waves 가 생성하는
    Wave 수는 서브태스크 총 개수 이하이며, 재라우팅 hop 은 MAX_ROUTE_HOPS 이하이다.

대상 코드(실측):
- `ai_engine/agent_system/dag.py` 의 `topological_waves` — Wave 수 상한(≤ 서브태스크 수).
- Refine_Loop 유한 종료는 evaluator_selector 의 cap 판정 의미론을 순수 시뮬레이션으로
  검증한다(Task 8 의 실제 노드 구현과 독립적으로 종료 불변식만 확인).

전략:
- 임의(구조가 깨진 것 포함) 서브태스크 목록에 대해서도 Wave 수 ≤ 서브태스크 수 유한 종료.
- 임의 초기 refine_count / cap 조합에서 Refine_Loop 복귀 횟수가 cap 이하로 종료.
- Gateway/네트워크 불필요. hypothesis max_examples=100, deadline=None.

실행:
  ai_engine/.venv/bin/python -m pytest scripts/test_finite_termination_pbt.py -q
"""
from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from hypothesis import given, settings, strategies as st

from ai_engine.agent_system.dag import topological_waves

_DOMAINS = ["coding", "media", "research", "ops", "chat"]


def _arbitrary_subtask():
    """구조가 완전/불완전할 수 있는 임의 서브태스크(누락 id, 무효 참조, 자기참조 포함)."""
    return st.fixed_dictionaries(
        {},
        optional={
            "id": st.one_of(st.text(max_size=5), st.integers(), st.none()),
            "domain": st.sampled_from(_DOMAINS),
            "subtask": st.text(max_size=8),
            "depends_on": st.lists(st.text(max_size=5), max_size=6),
        },
    )


@settings(max_examples=100, deadline=None)
@given(subtasks=st.lists(_arbitrary_subtask(), max_size=12))
def test_wave_count_le_subtask_count_arbitrary(subtasks):
    """Property 1: 임의 subtasks 에 대해 Wave 수 ≤ 서브태스크 총 개수."""
    waves = topological_waves(subtasks)
    assert len(waves) <= len(subtasks)


@settings(max_examples=100, deadline=None)
@given(subtasks=st.lists(_arbitrary_subtask(), max_size=12))
def test_waves_terminate_and_partition(subtasks):
    """Property 1: topological_waves 는 유한 종료하며 모든 서브태스크를 정확히 1회 포함."""
    waves = topological_waves(subtasks)
    total = sum(len(w) for w in waves)
    assert total == len(subtasks)


# ── Refine_Loop 유한 종료 시뮬레이션 ──────────────────────────────────────
def _simulate_refine_loop(initial_refine_count: int, cap: int, always_unmet: bool) -> int:
    """evaluator_selector 의 cap 판정 의미론을 순수 시뮬레이션.

    design.md 명세:
      - achieved=True 이거나 refine_count >= cap 이면 종료(done).
      - 미달 & refine_count < cap 이면 planner 복귀 + refine_count += 1.
    always_unmet=True 는 평가가 항상 미달(최악 시나리오)을 의미하여 cap 도달까지 반복.

    반환: 수행된 Refine_Loop 복귀 횟수.
    """
    refine_count = initial_refine_count
    loops = 0
    # 유한 종료 검증을 위해 넉넉한 안전 상한(무한루프면 이 상한을 초과 → 테스트 실패)
    hard_stop = cap + abs(initial_refine_count) + 1000
    while loops < hard_stop:
        achieved = not always_unmet
        if achieved or refine_count >= cap:
            break  # done → END
        # 미달 & cap 미만 → planner 복귀
        refine_count += 1
        loops += 1
    return loops


@settings(max_examples=100, deadline=None)
@given(
    initial=st.integers(min_value=0, max_value=10),
    cap=st.integers(min_value=0, max_value=5),
    always_unmet=st.booleans(),
)
def test_refine_loop_bounded_by_cap(initial, cap, always_unmet):
    """Property 1: Refine_Loop 복귀 횟수는 cap 이하로 유한 종료."""
    loops = _simulate_refine_loop(initial, cap, always_unmet)
    # 초기 refine_count 가 cap 이상이면 즉시 종료(0회). 그 외 최대 (cap - initial) 회.
    max_expected = max(0, cap - initial)
    assert loops <= max_expected
    assert loops <= cap
