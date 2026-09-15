# Feature: gateway-models-effort-support, Property 5: canonicalization 멱등성 —
# 임의 Capability_Map entry에 대해 Capability_Canonicalizer를 반복 적용한 결과는 첫 적용
# 결과와 동일하다. 즉 canonical(canonical(x)) == canonical(x)이고
# serialize(canonical(x)) == serialize(x)이며, 반복 적용은 Capability_Fingerprint를
# 변화시키지 않는다.
"""Property 5 (canonicalization 멱등성) property-based test — task 2.3.

검증 대상 모듈: ``ai_engine/capability/canonicalizer.py``
  (design.md "canonical serialization과 fingerprint 계산 규칙" 8단계 중 8단계 멱등성)

**Validates: Requirements 3.14, 3.16, 12.13**

단정 내용
  1. ``canonical`` 은 **고정점 함수**다 — ``canonical(canonical(x)) == canonical(x)``
     이며 3회 적용도 1회 적용과 같다(Requirement 3.16).
  2. ``serialize(canonical(x)) == serialize(x)`` 이고 ``canonical_bytes`` 도 동일하다.
     즉 반복 적용은 UTF-8 바이트열을 바꾸지 않는다(Requirement 3.14 — object key와
     집합형 collection을 정렬한 canonical serialization).
  3. 반복 적용은 Capability_Fingerprint를 변화시키지 않는다. entry 단위 fingerprint와
     fingerprint 입력 view(``capability_fingerprint_input``) 모두 불변이며, view 자체도
     canonical 고정점이다.
  4. ``canonical`` 은 입력을 변형하지 않는다(순수성 — 반복 적용이 잘 정의되기 위한 전제).

입력 생성기
  공통 생성기 ``scripts/_capability_strategies.py`` 만 사용한다. 경계값은 생성기가 명시적으로
  포함한다: 빈 map, 단일 entry, 동시각 tie, 빈 문자열 ID, enum 단일값, range 상·하한 동일,
  최대 중첩 body. Malformed_Entry도 입력에 포함한다 — 정규화는 유효성과 무관하게 결정론적이어야
  한다(Malformed 제외 규칙 자체는 Property 2가 검증한다).

절대 원칙
  model ID·provider·route 지원 여부·effort field path·effort 허용값을 확정 상수로 두지 않는다.
  모든 식별자는 생성기의 무작위 심볼이며, 이 테스트의 성공은 **Gateway 지원 근거가 아니다**
  (Requirement 12.22). 외부 Gateway를 호출하지 않는다(네트워크 0, 파일 쓰기 0).

PBT 규약 (design.md "PBT 구성 규칙")
  ``@seed(AE_PBT_SEED)`` 고정 seed, ``max_examples`` 최소 100, ``deadline=None``,
  ``database=None``, ``print_blob=True``. 실패 시 최소화된 counterexample과 재현 blob을
  ``pbt.counterexamples`` JSON 레코드로 표준 출력에 남기고, ``AE_PBT_REPORT`` 환경변수가
  가리키는 파일이 있으면 같은 레코드를 JSON Lines로 덧붙인다(Validation_Runner 보고서 입력).

실행:
  ai_engine/.venv/bin/python -m pytest scripts/test_capability_canonicalizer_idempotence_pbt.py -q
  ai_engine/.venv/bin/python scripts/test_capability_canonicalizer_idempotence_pbt.py

_Requirements: 3.14, 3.16, 12.13, 12.19, 12.21_
"""
from __future__ import annotations

import copy
import json
import os
import re
import sys
from typing import Any

from hypothesis import given, seed
from hypothesis import strategies as st

# repo 루트와 scripts/ 를 import 경로에 추가한다(_capability_strategies 관행 재사용).
_HERE = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.dirname(_HERE)
for _path in (_ROOT, _HERE):
    if _path not in sys.path:
        sys.path.insert(0, _path)

import _capability_strategies as S  # noqa: E402

from ai_engine.capability import canonicalizer  # noqa: E402

FEATURE = "gateway-models-effort-support"
PROPERTY_ID = 5
PROPERTY_NAME = "canonicalization 멱등성"

#: 재현 blob(`@reproduce_failure(...)`) 추출 패턴.
_BLOB_PATTERN = re.compile(r"@reproduce_failure\([^)]*\)")


# ─────────────────────────────────────────────────────────────────
# 입력 생성기 — 유효 entry·Malformed entry·map·최대 중첩 구조 전부
# ─────────────────────────────────────────────────────────────────
def canonicalizer_inputs() -> st.SearchStrategy[Any]:
    """Property 5 입력. 모든 경계값을 생성기 수준에서 포함한다.

    - :func:`_capability_strategies.entries` — 임의 상태 entry(빈 문자열 ID, 미완성 계약,
      `SEED` 출처, enum 단일값, range 상·하한 동일 domain 포함)
    - ``entries(active_ready=True)`` — `VERIFIED` + `SUPPORTED` route를 가진 entry
    - :func:`_capability_strategies.malformed_entries` — mutation 주입 entry(정규화 결정론)
    - :func:`_capability_strategies.capability_maps` — 빈 map·단일 entry·형제 entry
    - :func:`_capability_strategies.tie_capability_maps` — 동시각 tie 경계값
    - :func:`_capability_strategies.baseline_bodies` — 최대 중첩 구조 경계값
    """
    return st.one_of(
        S.entries(),
        S.entries(active_ready=True),
        S.malformed_entries().map(lambda case: case["mutated"]),
        S.capability_maps(),
        S.tie_capability_maps(),
        S.baseline_bodies(),
    )


def _entry_list(value: Any) -> list:
    """Capability_Map이면 entry 목록, 그 밖이면 빈 목록(canonical은 `entries` 순서를 보존한다)."""
    if isinstance(value, dict) and isinstance(value.get("entries"), list):
        return list(value["entries"])
    return []


# ─────────────────────────────────────────────────────────────────
# counterexample 기록 (Requirement 12.21)
# ─────────────────────────────────────────────────────────────────
def _counterexample_record(test_name: str, exc: BaseException) -> dict:
    """Hypothesis가 최소화한 counterexample과 재현 blob을 보고서 레코드로 만든다."""
    notes = [str(note) for note in getattr(exc, "__notes__", []) or []]
    falsifying = [note for note in notes if note.lstrip().startswith("Falsifying example")]
    blobs = [match.group(0) for note in notes for match in _BLOB_PATTERN.finditer(note)]
    return {
        "feature": FEATURE,
        "property": PROPERTY_ID,
        "propertyName": PROPERTY_NAME,
        "test": test_name,
        "seed": S.AE_PBT_SEED,
        "maxExamples": S.MAX_EXAMPLES,
        "error": f"{type(exc).__name__}: {str(exc)[:200]}",
        # 최소화된 counterexample(마지막 note가 최종 축소 결과다).
        "counterexample": falsifying[-1] if falsifying else "",
        "reproduceBlob": blobs[-1] if blobs else "",
    }


def _report_counterexample(test_name: str, exc: BaseException) -> None:
    """counterexample 레코드를 표준 출력에 남기고 `AE_PBT_REPORT`가 있으면 파일에 덧붙인다."""
    record = _counterexample_record(test_name, exc)
    line = json.dumps(record, ensure_ascii=False, sort_keys=True)
    print(f"pbt.counterexamples: {line}")

    report_path = (os.environ.get("AE_PBT_REPORT") or "").strip()
    if not report_path:
        return
    try:  # 보고서 기록 실패가 원래 실패를 가려서는 안 된다.
        with open(report_path, "a", encoding="utf-8") as handle:
            handle.write(line + "\n")
    except OSError as write_error:
        print(f"pbt.counterexamples 기록 실패: {str(write_error)[:200]}")


# ─────────────────────────────────────────────────────────────────
# Property 5
# ─────────────────────────────────────────────────────────────────
# Feature: gateway-models-effort-support, Property 5: canonicalization 멱등성
@seed(S.AE_PBT_SEED)
@S.PBT_SETTINGS
@given(canonicalizer_inputs())
def _property_canonicalization_idempotence(value: Any) -> None:
    """canonical·serialize·fingerprint의 반복 적용 불변성(Requirements 3.14, 3.16, 12.13)."""
    before = copy.deepcopy(value)

    first = canonicalizer.canonical(value)
    second = canonicalizer.canonical(first)
    third = canonicalizer.canonical(second)

    # (4) 순수성 — 입력을 변형하지 않는다.
    assert value == before, "canonical이 입력을 변형했다"

    # (1) canonical(canonical(x)) == canonical(x) — 고정점.
    assert second == first, "canonical(canonical(x)) != canonical(x)"
    assert third == first, "canonical 3회 적용 결과가 1회 적용과 다르다"

    # (2) serialize(canonical(x)) == serialize(x) — 동일 UTF-8 바이트열.
    first_text = canonicalizer.serialize(value)
    assert canonicalizer.serialize(first) == first_text, "serialize(canonical(x)) != serialize(x)"
    assert canonicalizer.serialize(second) == first_text, "반복 적용이 직렬화 바이트를 바꿨다"
    assert canonicalizer.canonical_bytes(first) == canonicalizer.canonical_bytes(value)
    assert canonicalizer.canonical_equal(first, value)

    # (3) 반복 적용은 Capability_Fingerprint를 바꾸지 않는다.
    fingerprint = canonicalizer.capability_fingerprint(value)
    assert fingerprint.startswith(canonicalizer.CAPABILITY_FINGERPRINT_PREFIX)
    assert canonicalizer.capability_fingerprint(first) == fingerprint, "1회 적용 후 fingerprint 변화"
    assert canonicalizer.capability_fingerprint(second) == fingerprint, "반복 적용 후 fingerprint 변화"
    assert canonicalizer.capability_fingerprint(third) == fingerprint

    # (3) fingerprint 입력 view 자체도 canonical 고정점이며 반복 적용에 불변이다.
    view = canonicalizer.canonical(canonicalizer.capability_fingerprint_input(value))
    assert canonicalizer.canonical(view) == view, "fingerprint 입력 view가 고정점이 아니다"
    assert (
        canonicalizer.serialize(canonicalizer.capability_fingerprint_input(first))
        == canonicalizer.serialize(view)
    ), "1회 적용 후 fingerprint 입력이 달라졌다"

    # (3) Capability_Map 입력이면 entry 단위 fingerprint도 불변이다(`entries` 순서 보존).
    original_entries = _entry_list(value)
    canonical_entries = _entry_list(first)
    assert len(canonical_entries) == len(original_entries)
    for original_entry, canonical_entry in zip(original_entries, canonical_entries):
        assert canonicalizer.capability_fingerprint(
            canonical_entry
        ) == canonicalizer.capability_fingerprint(original_entry), "entry fingerprint가 변화했다"
        assert canonicalizer.serialize(canonical_entry) == canonicalizer.serialize(original_entry)


def test_canonicalization_idempotence() -> None:
    """Property 5 pytest 진입점 — 실패 시 최소화 counterexample과 재현 blob을 기록한다."""
    try:
        _property_canonicalization_idempotence()
    except BaseException as exc:
        _report_counterexample("test_canonicalization_idempotence", exc)
        raise


if __name__ == "__main__":
    # 단발 실행 드라이버(워치 모드 금지).
    test_canonicalization_idempotence()
    print(
        f"PASSED: Property {PROPERTY_ID} — {PROPERTY_NAME} "
        f"(seed={S.AE_PBT_SEED}, max_examples={S.MAX_EXAMPLES})"
    )
