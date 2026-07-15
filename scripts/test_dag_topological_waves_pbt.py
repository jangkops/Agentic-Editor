# Feature: langgraph-reasoning-upgrade, Property 5: 위상정렬 정확성
"""Property 5: 위상정렬 정확성 — Hypothesis 기반 PBT.

Validates: Requirements 4.2

design.md Correctness Property 5 발췌:
    For any 순환이 없는 서브태스크 목록에 대해, `topological_waves` 가 생성한 Wave
    분할에서 각 서브태스크의 모든 depends_on 선행 항목은 그 서브태스크보다 앞선
    Wave 에 속하며, 모든 서브태스크는 정확히 하나의 Wave 에 속한다(분할 = partition).

대상 코드(실측):
- `ai_engine/agent_system/dag.py` 의 `topological_waves(subtasks)`.

생성기 설계(design.md "생성기 설계 노트"):
- 비순환 DAG 생성: 노드에 정수 순서를 부여하고 depends_on 을 "더 작은 순서 노드만
  참조" 하도록 제한하여 순환 없는 그래프를 생성한다.

전략:
- Gateway/네트워크 불필요. hypothesis max_examples=100, deadline=None.

실행:
  ai_engine/.venv/bin/python -m pytest scripts/test_dag_topological_waves_pbt.py -q
"""
from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from hypothesis import given, settings, strategies as st

from ai_engine.agent_system.dag import detect_cycle, topological_waves

_DOMAINS = ["coding", "media", "research", "ops", "chat"]


@st.composite
def acyclic_dag(draw):
    """비순환 DAG 생성: 노드 i 의 depends_on 은 인덱스 < i 인 노드(t{j}) 만 참조."""
    count = draw(st.integers(min_value=0, max_value=10))
    items = []
    for i in range(count):
        if i == 0:
            deps = []
        else:
            deps = draw(
                st.lists(
                    st.sampled_from([f"t{j}" for j in range(i)]),
                    max_size=i,
                    unique=True,
                )
            )
        items.append(
            {
                "id": f"t{i}",
                "domain": draw(st.sampled_from(_DOMAINS)),
                "subtask": draw(st.text(max_size=8)),
                "depends_on": deps,
            }
        )
    return items


@settings(max_examples=100, deadline=None)
@given(subtasks=acyclic_dag())
def test_deps_precede_in_earlier_waves(subtasks):
    """Property 5: 각 서브태스크의 depends_on 은 앞선 Wave 에 위치."""
    waves = topological_waves(subtasks)
    # id -> wave 인덱스
    wave_of = {}
    for w_idx, wave in enumerate(waves):
        for item in wave:
            wave_of[item["id"]] = w_idx
    for item in subtasks:
        for dep in item["depends_on"]:
            assert wave_of[dep] < wave_of[item["id"]]


@settings(max_examples=100, deadline=None)
@given(subtasks=acyclic_dag())
def test_partition_every_subtask_exactly_once(subtasks):
    """Property 5: 모든 서브태스크는 정확히 하나의 Wave 에 속한다(partition)."""
    waves = topological_waves(subtasks)
    flat_ids = [item["id"] for wave in waves for item in wave]
    input_ids = [item["id"] for item in subtasks]
    # 개수 동일 + 중복 없음 → 정확히 하나의 Wave 소속
    assert sorted(flat_ids) == sorted(input_ids)
    assert len(flat_ids) == len(set(flat_ids))


@settings(max_examples=100, deadline=None)
@given(subtasks=acyclic_dag())
def test_no_cycle_detected_for_acyclic(subtasks):
    """Property 5(전제): 비순환 생성기 산출물은 detect_cycle False."""
    assert detect_cycle(subtasks) is False


@settings(max_examples=100, deadline=None)
@given(subtasks=acyclic_dag())
def test_wave_count_le_subtask_count(subtasks):
    """Property 5/1: Wave 수 ≤ 서브태스크 총 개수."""
    waves = topological_waves(subtasks)
    assert len(waves) <= len(subtasks)


# ── 예시(단위) 테스트 ──────────────────────────────────────────────────────
def test_linear_chain_makes_sequential_waves():
    subs = [
        {"id": "t0", "domain": "coding", "subtask": "a", "depends_on": []},
        {"id": "t1", "domain": "media", "subtask": "b", "depends_on": ["t0"]},
        {"id": "t2", "domain": "ops", "subtask": "c", "depends_on": ["t1"]},
    ]
    waves = topological_waves(subs)
    assert [[i["id"] for i in w] for w in waves] == [["t0"], ["t1"], ["t2"]]


def test_independent_tasks_single_wave():
    subs = [
        {"id": "t0", "domain": "coding", "subtask": "a", "depends_on": []},
        {"id": "t1", "domain": "media", "subtask": "b", "depends_on": []},
    ]
    waves = topological_waves(subs)
    assert len(waves) == 1
    assert {i["id"] for i in waves[0]} == {"t0", "t1"}
