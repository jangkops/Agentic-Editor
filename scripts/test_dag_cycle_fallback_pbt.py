# Feature: langgraph-reasoning-upgrade, Property 6: 순환 감지 및 단일 Wave 폴백
"""Property 6: 순환 감지 및 단일 Wave 폴백 — Hypothesis 기반 PBT.

Validates: Requirements 5.1, 5.2

design.md Correctness Property 6 발췌:
    For any 순환 의존을 포함하는 서브태스크 목록에 대해, `detect_cycle` 은 True 를
    반환하고 `topological_waves` 는 모든 서브태스크를 담은 길이 1 의 Wave 목록
    (단일 Wave 폴백)을 반환한다.

대상 코드(실측):
- `ai_engine/agent_system/dag.py` 의 `detect_cycle`, `topological_waves`.

생성기 설계(design.md "생성기 설계 노트"):
- 순환 그래프 생성: 비순환 DAG 에 역방향 엣지(더 큰 순서 → 더 작은 순서 노드로의
  depends_on)를 하나 이상 강제 삽입하여 순환을 만든다.

전략:
- Gateway/네트워크 불필요. hypothesis max_examples=100, deadline=None.

실행:
  ai_engine/.venv/bin/python -m pytest scripts/test_dag_cycle_fallback_pbt.py -q
"""
from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from hypothesis import given, settings, strategies as st

from ai_engine.agent_system.dag import detect_cycle, topological_waves

_DOMAINS = ["coding", "media", "research", "ops", "chat"]


@st.composite
def cyclic_dag(draw):
    """순환 그래프 생성: 비순환 사슬에 최소 하나의 역방향 엣지를 강제 삽입.

    노드 수 >= 2 를 보장하고, 인덱스 순서대로 사슬을 만든 뒤 하나 이상의 노드가
    자신보다 큰 인덱스 노드를 depends_on 하도록 하여 순환을 생성한다.
    """
    count = draw(st.integers(min_value=2, max_value=8))
    items = []
    for i in range(count):
        deps = []
        if i > 0:
            # 앞선 노드에 대한 정상(전방) 의존 일부
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
                "depends_on": list(deps),
            }
        )

    # 역방향 엣지 강제 삽입: 노드 a 가 노드 b(b > a) 를 depends_on 하게 만들어
    # 최소 하나의 순환을 보장한다. (a<b) 이고 b 는 이미 t{<=b-1} 을 참조할 수 있으므로
    # a->b, b->...->a 형태의 순환이 형성될 여지를 만든다.
    a = draw(st.integers(min_value=0, max_value=count - 2))
    b = draw(st.integers(min_value=a + 1, max_value=count - 1))
    # b 가 a 에 도달 가능하도록: b 의 depends_on 에 a 를 보장(전방 엣지 b->a)
    if f"t{a}" not in items[b]["depends_on"]:
        items[b]["depends_on"].append(f"t{a}")
    # a 가 b 를 depends_on (역방향 엣지 a->b) → a->b->a 순환 확정
    items[a]["depends_on"].append(f"t{b}")
    return items


@settings(max_examples=100, deadline=None)
@given(subtasks=cyclic_dag())
def test_detect_cycle_true(subtasks):
    """Property 6: 순환 포함 그래프에서 detect_cycle 은 True."""
    assert detect_cycle(subtasks) is True


@settings(max_examples=100, deadline=None)
@given(subtasks=cyclic_dag())
def test_topological_waves_single_wave_fallback(subtasks):
    """Property 6: 순환 시 topological_waves 는 길이 1 의 단일 Wave 반환."""
    waves = topological_waves(subtasks)
    assert len(waves) == 1
    # 단일 Wave 는 모든 서브태스크를 담는다.
    assert len(waves[0]) == len(subtasks)
    flat_ids = sorted(item["id"] for item in waves[0])
    input_ids = sorted(item["id"] for item in subtasks)
    assert flat_ids == input_ids


# ── 예시(단위) 테스트 ──────────────────────────────────────────────────────
def test_two_node_cycle():
    subs = [
        {"id": "t0", "domain": "coding", "subtask": "a", "depends_on": ["t1"]},
        {"id": "t1", "domain": "media", "subtask": "b", "depends_on": ["t0"]},
    ]
    assert detect_cycle(subs) is True
    waves = topological_waves(subs)
    assert len(waves) == 1
    assert len(waves[0]) == 2


def test_self_dependency_is_cycle():
    subs = [
        {"id": "t0", "domain": "coding", "subtask": "a", "depends_on": ["t0"]},
    ]
    assert detect_cycle(subs) is True
    waves = topological_waves(subs)
    assert len(waves) == 1
    assert len(waves[0]) == 1
