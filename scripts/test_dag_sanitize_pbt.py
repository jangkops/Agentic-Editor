# Feature: langgraph-reasoning-upgrade, Property 7: 무효 의존 참조 제거
"""Property 7: 무효 의존 참조 제거 — Hypothesis 기반 PBT.

Validates: Requirements 5.3

design.md Correctness Property 7 발췌:
    For any 존재하지 않는 id 를 참조하는 depends_on 을 포함한 서브태스크 목록에 대해,
    `sanitize_depends_on` 이후 모든 depends_on 원소는 목록 내 실재하는 서브태스크 id 만
    포함하며, 서브태스크 총 개수는 보존된다.

대상 코드(실측):
- `ai_engine/agent_system/dag.py` 의 `sanitize_depends_on(subtasks)`:
    · id 누락/비어있음/비문자열 항목을 인덱스 기반 "t{i}" 로 보정
    · depends_on 의 미실재 id 참조 제거
    · 입력 불변(새 리스트/새 dict 반환), 서브태스크 개수 보존

전략:
- 실재 id 와 랜덤 무효 id 를 섞은 depends_on 을 생성해 정제 후 실재만 남는지 검증.
- Gateway/네트워크 불필요. hypothesis max_examples=100, deadline=None.

실행:
  ai_engine/.venv/bin/python -m pytest scripts/test_dag_sanitize_pbt.py -q
"""
from __future__ import annotations

import copy
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from hypothesis import given, settings, strategies as st

from ai_engine.agent_system.dag import sanitize_depends_on

_DOMAINS = ["coding", "media", "research", "ops", "chat"]


def _subtasks_strategy():
    """id/domain/subtask/depends_on 을 가진 서브태스크 목록 생성기.

    depends_on 에는 실재 후보 id("t0".."tN")와 무효 랜덤 문자열을 섞는다.
    일부 항목은 id 를 아예 누락시켜 보정 경로도 커버한다.
    """
    n = st.integers(min_value=0, max_value=8)

    @st.composite
    def build(draw):
        count = draw(n)
        # 실재하게 될 후보 id 풀(인덱스 기반). 실제 id 는 sanitize 가 보정할 수 있으므로
        # depends_on 참조는 "t{i}" 형태의 실재 후보 + 무효 문자열을 섞는다.
        valid_pool = [f"t{i}" for i in range(count)]
        invalid_pool = st.text(
            alphabet="xyzXYZ_-0123456789", min_size=1, max_size=6
        ).map(lambda s: f"z_{s}")  # "t{i}" 와 절대 겹치지 않는 접두사

        items = []
        for i in range(count):
            has_id = draw(st.booleans())
            item = {
                "domain": draw(st.sampled_from(_DOMAINS)),
                "subtask": draw(st.text(max_size=10)),
            }
            if has_id:
                item["id"] = f"t{i}"
            deps = draw(
                st.lists(
                    st.one_of(
                        st.sampled_from(valid_pool) if valid_pool else st.just("t0"),
                        invalid_pool,
                    ),
                    max_size=5,
                )
            )
            item["depends_on"] = deps
            items.append(item)
        return items

    return build()


@settings(max_examples=100, deadline=None)
@given(subtasks=_subtasks_strategy())
def test_sanitized_deps_reference_existing_ids_only(subtasks):
    """Property 7: 정제 후 모든 depends_on 원소는 실재 id 만 포함."""
    result = sanitize_depends_on(subtasks)
    real_ids = {item["id"] for item in result}
    for item in result:
        for dep in item["depends_on"]:
            assert dep in real_ids


@settings(max_examples=100, deadline=None)
@given(subtasks=_subtasks_strategy())
def test_count_preserved(subtasks):
    """Property 7: 서브태스크 총 개수는 보존된다."""
    result = sanitize_depends_on(subtasks)
    assert len(result) == len(subtasks)


@settings(max_examples=100, deadline=None)
@given(subtasks=_subtasks_strategy())
def test_input_not_mutated(subtasks):
    """Property 7(불변식): 입력을 변경하지 않는다(새 리스트/새 dict 반환)."""
    snapshot = copy.deepcopy(subtasks)
    _ = sanitize_depends_on(subtasks)
    assert subtasks == snapshot


@settings(max_examples=100, deadline=None)
@given(subtasks=_subtasks_strategy())
def test_every_item_has_nonempty_id(subtasks):
    """Property 7: id 누락 항목은 "t{i}" 로 보정되어 항상 비어있지 않은 id 보유."""
    result = sanitize_depends_on(subtasks)
    for item in result:
        assert isinstance(item["id"], str) and item["id"] != ""
        assert isinstance(item["depends_on"], list)


# ── 예시(단위) 테스트 ──────────────────────────────────────────────────────
def test_removes_nonexistent_reference():
    subs = [
        {"id": "t0", "domain": "coding", "subtask": "a", "depends_on": []},
        {"id": "t1", "domain": "media", "subtask": "b", "depends_on": ["t0", "ghost"]},
    ]
    result = sanitize_depends_on(subs)
    assert result[1]["depends_on"] == ["t0"]


def test_fills_missing_id():
    subs = [
        {"domain": "coding", "subtask": "a"},
        {"domain": "media", "subtask": "b", "depends_on": ["t0"]},
    ]
    result = sanitize_depends_on(subs)
    assert result[0]["id"] == "t0"
    assert result[1]["id"] == "t1"
    assert result[1]["depends_on"] == ["t0"]


def test_empty_list():
    assert sanitize_depends_on([]) == []
