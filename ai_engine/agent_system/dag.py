"""순수 함수 DAG 스케줄링 모듈 (langgraph-reasoning-upgrade Task 2).

design.md "Components and Interfaces / 1. 순수 함수 — DAG 스케줄링" 명세 구현.

이 모듈은 **부작용·네트워크 의존이 전혀 없는 순수 함수**만 제공한다. LLM/Gateway/디스크
I/O 를 일절 사용하지 않으므로 Hypothesis 기반 100+ 반복 속성 테스트에 이상적이다.

서브태스크 정규화 타입(dict 기반, GraphState.plan 항목과 동일 형태):
    {"id": str, "domain": RouteName, "subtask": str, "depends_on": list[str]}

제공 함수:
- sanitize_depends_on(subtasks) -> list[dict]   (Req 5.3)
- detect_cycle(subtasks) -> bool                (Req 5.1)
- topological_waves(subtasks) -> list[list[dict]] (Req 4.2 / 5.2 / 5.4)

설계 원칙:
- 입력을 절대 변경하지 않는다(sanitize 는 새 리스트/새 dict 반환).
- 어떤 예외도 던지지 않고 방어적으로 동작한다(임의 입력에 대해서도 유한 종료).
- detect_cycle 과 topological_waves 는 동일한 "준비(readiness) 시뮬레이션" 규칙을
  공유하여 판정이 항상 일관된다.
"""

from __future__ import annotations

from typing import Any, Dict, List, Set


def _node_ids(subtasks: List[dict]) -> List[str]:
    """각 서브태스크의 정규화된 id 목록을 반환.

    id 가 없거나 비어있거나 문자열이 아니면 인덱스 기반 "t{i}" 로 보정한다.
    입력을 변경하지 않는다(값만 계산).
    """
    ids: List[str] = []
    for i, item in enumerate(subtasks):
        raw = item.get("id") if isinstance(item, dict) else None
        if isinstance(raw, str) and raw != "":
            ids.append(raw)
        else:
            ids.append(f"t{i}")
    return ids


def _deps_of(item: Any) -> List[str]:
    """서브태스크의 depends_on 을 문자열 리스트로 안전 추출(없으면 빈 리스트)."""
    if not isinstance(item, dict):
        return []
    raw = item.get("depends_on")
    if not isinstance(raw, (list, tuple)):
        return []
    return [d for d in raw if isinstance(d, str)]


def _existing_deps(subtasks: List[dict], ids: List[str]) -> List[Set[str]]:
    """각 노드의 depends_on 중 실재하는 id 만 담은 집합 리스트를 반환.

    존재하지 않는 id 참조는 무시(방어적). 중복은 집합으로 dedup 된다.
    """
    id_set: Set[str] = set(ids)
    result: List[Set[str]] = []
    for item in subtasks:
        deps = {d for d in _deps_of(item) if d in id_set}
        result.append(deps)
    return result


def sanitize_depends_on(subtasks: List[dict]) -> List[dict]:
    """존재하지 않는 id 를 참조하는 depends_on 항목을 제거(Req 5.3).

    Precondition:  subtasks 는 dict 리스트(각 항목 id/domain/subtask 보유, depends_on 선택).
    Postcondition: 반환 리스트의 모든 depends_on 원소는 subtasks 내 실재 id 만 포함.
                   id 누락(또는 비어있음/비문자열) 항목은 인덱스 기반 id("t{i}")로 보정.
    Invariant:     입력을 변경하지 않는다(새 리스트 + 새 dict 반환). 서브태스크 개수 보존.
    """
    if not isinstance(subtasks, (list, tuple)):
        return []

    subs = list(subtasks)
    ids = _node_ids(subs)
    id_set: Set[str] = set(ids)

    sanitized: List[dict] = []
    for i, item in enumerate(subs):
        # 원본 dict 를 복사해 불변성 보장(원본 미변경).
        new_item: Dict[str, Any] = dict(item) if isinstance(item, dict) else {}
        new_item["id"] = ids[i]
        # depends_on 을 실재 id 만 남기도록 정제(순서 보존, 중복은 그대로 두되 실재만).
        new_item["depends_on"] = [d for d in _deps_of(item) if d in id_set]
        sanitized.append(new_item)

    return sanitized


def detect_cycle(subtasks: List[dict]) -> bool:
    """depends_on 그래프에 순환이 존재하면 True(Req 5.1).

    Kahn 스타일 준비(readiness) 시뮬레이션으로 판정한다. 준비 가능한 노드가 하나도
    없는데 미처리 노드가 남으면 순환으로 판정한다. 자기 자신을 depends_on 하는 경우도
    순환으로 감지된다. `sanitize_depends_on` 이후 호출을 가정하지만, 존재하지 않는
    참조는 방어적으로 무시하므로 미정제 입력에도 안전하다.
    """
    if not isinstance(subtasks, (list, tuple)) or not subtasks:
        return False

    subs = list(subtasks)
    ids = _node_ids(subs)
    deps = _existing_deps(subs, ids)

    completed: Set[str] = set()
    remaining: Set[int] = set(range(len(subs)))

    while remaining:
        ready = [i for i in remaining if deps[i] <= completed]
        if not ready:
            return True  # 순환(또는 자기참조)로 인해 더 이상 진행 불가
        for i in ready:
            completed.add(ids[i])
            remaining.discard(i)
    return False


def topological_waves(subtasks: List[dict]) -> List[List[dict]]:
    """depends_on 을 위상정렬하여 Wave 목록으로 분할(Req 4.2).

    Precondition:  subtasks 는 sanitize_depends_on 을 통과한 상태를 가정(미정제도 안전).
    Postcondition: 각 Wave 의 서브태스크는 선행 depends_on 이 모두 이전 Wave 에 존재.
                   순환이 있으면(detect_cycle True) 전체를 단일 Wave 로 반환(Req 5.2).
                   반환 Wave 수는 서브태스크 총 개수 이하(Req 5.4 / 4.2).
    Invariant:     모든 서브태스크는 정확히 하나의 Wave 에 속한다(분할 = partition).
                   입력을 변경하지 않는다(dict 참조를 그대로 그룹핑, 미변경).
    """
    if not isinstance(subtasks, (list, tuple)) or not subtasks:
        return []

    subs = list(subtasks)

    # 순환 감지 시 단일 Wave 폴백(Req 5.2).
    if detect_cycle(subs):
        return [list(subs)]

    ids = _node_ids(subs)
    deps = _existing_deps(subs, ids)

    completed: Set[str] = set()
    remaining: Set[int] = set(range(len(subs)))
    waves: List[List[dict]] = []

    while remaining:
        ready = [i for i in remaining if deps[i] <= completed]
        if not ready:
            # 비순환 경로에서는 도달 불가하나, 방어적으로 남은 전체를 단일 Wave 로
            # 묶어 유한 종료를 보장한다(Wave 수 ≤ 서브태스크 수 불변식 유지).
            waves.append([subs[i] for i in sorted(remaining)])
            break
        # 인덱스 순서를 보존하여 결정론적 Wave 구성.
        ready_sorted = sorted(ready)
        waves.append([subs[i] for i in ready_sorted])
        # Wave 확정 후에 완료 표시(같은 Wave 내부 의존이 조기 충족되지 않도록).
        for i in ready_sorted:
            completed.add(ids[i])
            remaining.discard(i)

    return waves
