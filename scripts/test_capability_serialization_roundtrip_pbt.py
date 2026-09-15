# Feature: gateway-models-effort-support, Property 6: serialization round-trip
# *For any* Capability_Map에 대해, serialize한 뒤 deserialize하면 model identity, provider,
# 모든 Known_Route의 계약·상태, effort 계약·상태, evidence reference의 의미가 보존되고,
# 다시 serialize한 바이트열은 최초 serialize 결과와 동일하다.
"""Property 6 (serialization round-trip) property test — task 2.4.

검증 대상 모듈: ``ai_engine/capability/canonicalizer.py``
  - ``serialize``   canonical form → 결정론적 JSON 문자열
  - ``deserialize`` canonical JSON 문자열 → 의미 보존 역직렬화

단정 요약 (design.md Correctness Properties → Property 6):
  R1. **의미 보존** — ``deserialize(serialize(map))`` 이후에도
      model identity(``modelId``·``invocationModelIds``), ``provider``,
      **모든 Known_Route** 의 ``status``·``allowlist``·``contract``,
      effort 의 ``status``·``contract``, 그리고 evidence reference
      (entry ``evidence`` 목록, route·effort ``evidenceRef``, 계약 하위 ``evidenceRef``)의
      의미가 canonical 표현 기준으로 동일하다.
  R2. **재직렬화 바이트 동일** — ``serialize(deserialize(serialize(map)))`` 의 UTF-8
      바이트열이 최초 ``serialize(map)`` 바이트열과 동일하다(2회 반복해도 동일).
  R3. **파생 의미 보존** — 복원 entry의 Capability_Fingerprint 재계산값과 저장값,
      Malformed_Entry 판정 결과가 원본과 같다(round-trip이 활성화 판정을 바꾸지 않는다).
  R4. **비-canonical 저장 텍스트도 의미 보존** — 정렬되지 않은 JSON 텍스트를
      ``deserialize`` 해도 같은 canonical 구조·같은 직렬화 바이트를 만든다.

`null`(미확정)과 key 부재(미존재)는 서로 다른 의미이므로, 비교 view는 부재를 전용
sentinel(:data:`_ABSENT`)로 표시해 둘을 구분한다.

경계값(design.md "PBT 구성 규칙")은 입력 전략에서 명시적으로 포함한다: 빈 map, 단일
entry, 동일 modelId duplicate·동시각 tie·fingerprint 불일치 형제 entry. 상태 enum·계약
완전성·effort domain(enum 단일값, range 상·하한 동일)은 공통 생성기가 전수로 섞는다.

model ID·provider·effort field path·effort 허용값은 확정 상수 없이 무작위 심볼로만
생성한다(`scripts/_capability_strategies.py`). 이 테스트는 순수 로직만 구동하며
Gateway·네트워크·파일시스템(실패 기록 제외)에 접근하지 않고, 결과는 Gateway 지원
근거가 아니다(Requirement 12.22).

실패 시 최소화된 counterexample과 재현 정보를
``.generated/pbt/capability_serialization_roundtrip.json`` 에 기록한다(Validation_Runner의
보고서 ``pbt.counterexamples`` 입력, 작업 12.2). `print_blob=True` 로 Hypothesis 재현
blob도 함께 출력된다.

실행:
  ai_engine/.venv/bin/python -m pytest scripts/test_capability_serialization_roundtrip_pbt.py -q

**Validates: Requirements 3.15, 12.14**
_Requirements: 3.15, 12.14, 12.19, 12.21_
"""
from __future__ import annotations

import json
import os
import sys
from typing import Any

from hypothesis import given, seed
from hypothesis import strategies as st

_HERE = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.dirname(_HERE)
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)
if _HERE not in sys.path:
    sys.path.insert(0, _HERE)

import pytest  # noqa: E402

import _capability_strategies as S  # noqa: E402
from ai_engine.capability import canonicalizer, contracts  # noqa: E402

# ─────────────────────────────────────────────────────────────────
# property 식별 (보고서 기록용)
# ─────────────────────────────────────────────────────────────────
FEATURE = "gateway-models-effort-support"
PROPERTY_ID = 6
PROPERTY_LABEL = "serialization round-trip"


# ─────────────────────────────────────────────────────────────────
# counterexample 기록 (Requirement 12.21)
# ─────────────────────────────────────────────────────────────────

#: 기록 디렉터리. `.generated/` 는 gitignore 대상이며 env로 재지정할 수 있다.
COUNTEREXAMPLE_DIR = os.environ.get("AE_PBT_COUNTEREXAMPLE_DIR") or os.path.join(
    _ROOT, ".generated", "pbt"
)

#: 기록 파일 경로(property 1개당 1파일).
COUNTEREXAMPLE_PATH = os.path.join(
    COUNTEREXAMPLE_DIR, "capability_serialization_roundtrip.json"
)

#: 재현 명령(보고서에 그대로 실을 수 있는 형태).
REPRODUCE_COMMAND = (
    f"AE_PBT_SEED={S.AE_PBT_SEED} ai_engine/.venv/bin/python -m pytest "
    "scripts/test_capability_serialization_roundtrip_pbt.py -q"
)


def _json_safe(value: Any) -> Any:
    """counterexample 기록용 JSON 안전 표현(기록 실패가 판정을 가리지 않게 한다)."""
    try:
        return json.loads(canonicalizer.serialize(value))
    except Exception:  # pragma: no cover - 기록 경로 방어
        return repr(value)[:4000]


def record_counterexample(reason: str, counterexample: dict) -> None:
    """최소화된 counterexample을 JSON으로 기록한다(덮어쓰기).

    Hypothesis는 shrink 과정에서 실패 case를 반복 실행하고 **최소화된 case를 마지막에**
    보고하므로, 덮어쓰기 기록의 최종 내용이 최소화 counterexample이다. 기록 실패
    (권한·디스크)는 무시한다 — property 판정을 가리지 않는 것이 우선이다.
    """
    record = {
        "feature": FEATURE,
        "property": f"Property {PROPERTY_ID}: {PROPERTY_LABEL}",
        "testFile": os.path.relpath(os.path.abspath(__file__), _ROOT),
        "seed": S.AE_PBT_SEED,
        "maxExamples": S.MAX_EXAMPLES,
        "recordedAt": contracts.utc_now_iso(),
        "reason": str(reason)[:2000],
        "counterexample": {key: _json_safe(value) for key, value in counterexample.items()},
        "reproduce": REPRODUCE_COMMAND,
    }
    try:
        os.makedirs(COUNTEREXAMPLE_DIR, exist_ok=True)
        with open(COUNTEREXAMPLE_PATH, "w", encoding="utf-8") as handle:
            json.dump(record, handle, ensure_ascii=False, indent=2, default=str)
    except OSError:  # pragma: no cover - 기록 경로 방어
        pass


# ─────────────────────────────────────────────────────────────────
# 의미 비교 view — 부재(_ABSENT)와 null을 구분한다
# ─────────────────────────────────────────────────────────────────

#: key 부재 표시. 심볼 알파벳에 `<`·`>`가 없으므로 생성값과 충돌하지 않는다.
_ABSENT = "<absent>"

#: model identity·provider 등 entry 최상위 의미 필드.
_ENTRY_VIEW_FIELDS: tuple[str, ...] = (
    "schemaVersion",
    "modelId",
    "invocationModelIds",
    "provider",
    "sourceKind",
    "catalogFingerprint",
    "syncSupport",
    "asyncSupport",
    "streamingSupport",
    "evidence",
    "verificationStatus",
    "capabilityFingerprint",
)

#: Route_Entry 의미 필드.
_ROUTE_VIEW_FIELDS: tuple[str, ...] = contracts.REQUIRED_ROUTE_ENTRY_FIELDS

#: Effort_Entry 의미 필드.
_EFFORT_VIEW_FIELDS: tuple[str, ...] = contracts.REQUIRED_EFFORT_ENTRY_FIELDS


def _get(source: Any, key: str) -> Any:
    """``source[key]``. key가 없으면 :data:`_ABSENT` (null과 구분하기 위해)."""
    if isinstance(source, dict) and key in source:
        return source[key]
    return _ABSENT


def _container_keys(container: Any) -> list[str]:
    """route/effort 컨테이너에서 훑을 키 목록(모든 Known_Route + 실제 존재 키)."""
    keys = set(contracts.KNOWN_ROUTES)
    if isinstance(container, dict):
        keys |= {str(key) for key in container}
    return sorted(keys)


def _sub_entry_view(container: Any, route: str, fields: tuple[str, ...]) -> Any:
    """Route_Entry/Effort_Entry의 의미 view(비-dict면 원값을 그대로 비교 대상으로 둔다)."""
    sub = _get(container, route)
    if not isinstance(sub, dict):
        return sub
    return {field: _get(sub, field) for field in fields}


def _entry_view(entry: Any) -> Any:
    """entry의 의미 view.

    key 이름을 원 구조와 동일하게 유지하므로 canonicalizer의 집합 의미
    (``routes.*.contract.optionalFields`` 등)·순서 의미(``effort.*.contract.fieldPath`` 등)
    경로 규칙이 view에도 그대로 적용된다.
    """
    if not isinstance(entry, dict):
        return entry
    routes = _get(entry, "routes")
    effort = _get(entry, "effort")
    view: dict[str, Any] = {field: _get(entry, field) for field in _ENTRY_VIEW_FIELDS}
    view["routes"] = {
        route: _sub_entry_view(routes, route, _ROUTE_VIEW_FIELDS)
        for route in _container_keys(routes)
    }
    view["effort"] = {
        route: _sub_entry_view(effort, route, _EFFORT_VIEW_FIELDS)
        for route in _container_keys(effort)
    }
    return view


def _map_view(map_obj: Any) -> Any:
    """Capability_Map 전체의 의미 view(`entries`는 저장 순서를 유지한다)."""
    if not isinstance(map_obj, dict):
        return map_obj
    entry_list = _get(map_obj, "entries")
    return {
        "schemaVersion": _get(map_obj, "schemaVersion"),
        "entries": (
            [_entry_view(entry) for entry in entry_list]
            if isinstance(entry_list, list)
            else entry_list
        ),
    }


def _evidence_reference_view(entry: Any) -> dict:
    """entry의 모든 evidence reference 자리를 한 dict로 모은다.

    포함 자리: entry ``evidence`` 목록, ``routes.*.evidenceRef``,
    ``routes.*.contract.evidenceRef``, ``effort.*.evidenceRef``,
    ``effort.*.contract.evidenceRef``.
    """
    refs: dict[str, Any] = {"evidence": _get(entry, "evidence")}
    for container_key in ("routes", "effort"):
        container = _get(entry, container_key)
        for route in _container_keys(container):
            sub = _get(container, route)
            refs[f"{container_key}.{route}.evidenceRef"] = (
                _get(sub, "evidenceRef") if isinstance(sub, dict) else sub
            )
            contract = _get(sub, "contract") if isinstance(sub, dict) else _ABSENT
            refs[f"{container_key}.{route}.contract.evidenceRef"] = (
                _get(contract, "evidenceRef") if isinstance(contract, dict) else contract
            )
    return refs


def _same(key: str, left: Any, right: Any) -> bool:
    """canonical 표현 기준 의미 동일성.

    ``key``로 감싸 비교하므로 집합 의미(정렬·중복 제거)와 순서 의미(원 순서 보존) 경로
    규칙이 유지된다(bare list를 root에서 비교하면 집합 의미가 소실된다).
    """
    return canonicalizer.canonical_equal({key: left}, {key: right})


def _text(value: Any) -> str:
    """실패 메시지용 축약 canonical 표현."""
    try:
        return canonicalizer.serialize(value)[:400]
    except Exception:  # pragma: no cover - 메시지 경로 방어
        return repr(value)[:400]


# ─────────────────────────────────────────────────────────────────
# 입력 전략 — 경계값을 명시적으로 포함한다
# ─────────────────────────────────────────────────────────────────
def round_trip_maps() -> st.SearchStrategy[dict]:
    """Property 6 입력: 임의 Capability_Map + 경계값.

    - 빈 map (``min_entries=0, max_entries=0``)
    - 단일 entry
    - 일반 map(상태 enum 전수·계약 완전성·effort domain 혼합)
    - 동일 modelId 형제 entry 전수(:data:`_capability_strategies.SIBLING_KINDS` —
      duplicate, 동시각 tie, fingerprint 불일치, 최신 verifiedAt)
    """
    return st.one_of(
        S.capability_maps(min_entries=0, max_entries=0, siblings=False),
        S.capability_maps(min_entries=1, max_entries=1, siblings=False),
        S.capability_maps(min_entries=0, max_entries=4),
        *[S.tie_capability_maps(kind=kind) for kind in S.SIBLING_KINDS],
    )


# ─────────────────────────────────────────────────────────────────
# 단정 본문 (단일 property test에서만 호출한다)
# ─────────────────────────────────────────────────────────────────
def _assert_round_trip(map_obj: dict) -> None:
    """Property 6의 R1~R4 단정.

    단정 순서는 **진단이 좁은 것부터**다: entry 필드 단위 의미 보존(R1) → map 전체 의미
    view(R1) → 재직렬화 바이트 동일(R2) → 비-canonical 텍스트(R4). 바이트 비교가 먼저
    깨지면 어떤 필드가 손실됐는지 알 수 없으므로 뒤에 둔다.
    """
    text0 = canonicalizer.serialize(map_obj)
    restored = canonicalizer.deserialize(text0)

    # ── R1. 의미 보존 — entry 필드 단위 (Requirement 3.15) ───────────
    entries_before = map_obj.get("entries") if isinstance(map_obj, dict) else None
    entries_after = restored.get("entries") if isinstance(restored, dict) else None
    assert isinstance(entries_before, list) and isinstance(entries_after, list), (
        f"entries 타입이 보존되지 않았다 — Property {PROPERTY_ID}: "
        f"{type(entries_before).__name__} → {type(entries_after).__name__}"
    )
    assert len(entries_before) == len(entries_after), (
        f"entry 개수 불일치 — Property {PROPERTY_ID}: "
        f"{len(entries_before)} → {len(entries_after)}"
    )

    for index, (before, after) in enumerate(zip(entries_before, entries_after)):
        where = f"entries[{index}]"

        # model identity — Exact_Model_ID와 Invocation_Model_ID 집합
        assert _same("modelId", _get(before, "modelId"), _get(after, "modelId")), (
            f"{where}: modelId 의미 손실 — Property {PROPERTY_ID}: "
            f"{_get(before, 'modelId')!r} → {_get(after, 'modelId')!r}"
        )
        assert _same(
            "invocationModelIds",
            _get(before, "invocationModelIds"),
            _get(after, "invocationModelIds"),
        ), f"{where}: invocationModelIds 의미 손실 — Property {PROPERTY_ID}"

        # provider
        assert _same("provider", _get(before, "provider"), _get(after, "provider")), (
            f"{where}: provider 의미 손실 — Property {PROPERTY_ID}: "
            f"{_get(before, 'provider')!r} → {_get(after, 'provider')!r}"
        )

        # 모든 Known_Route의 상태·allowlist·계약
        routes_before, routes_after = _get(before, "routes"), _get(after, "routes")
        assert _container_keys(routes_before) == _container_keys(routes_after), (
            f"{where}: routes 키 집합 손실 — Property {PROPERTY_ID}"
        )
        for route in _container_keys(routes_before):
            left = _sub_entry_view(routes_before, route, _ROUTE_VIEW_FIELDS)
            right = _sub_entry_view(routes_after, route, _ROUTE_VIEW_FIELDS)
            assert _same("routes", {route: left}, {route: right}), (
                f"{where}.routes[{route}]: 상태·계약 의미 손실 — Property {PROPERTY_ID}\n"
                f"  before={_text(left)}\n  after={_text(right)}"
            )

        # 모든 Known_Route의 effort 상태·계약
        effort_before, effort_after = _get(before, "effort"), _get(after, "effort")
        assert _container_keys(effort_before) == _container_keys(effort_after), (
            f"{where}: effort 키 집합 손실 — Property {PROPERTY_ID}"
        )
        for route in _container_keys(effort_before):
            left = _sub_entry_view(effort_before, route, _EFFORT_VIEW_FIELDS)
            right = _sub_entry_view(effort_after, route, _EFFORT_VIEW_FIELDS)
            assert _same("effort", {route: left}, {route: right}), (
                f"{where}.effort[{route}]: effort 상태·계약 의미 손실 — Property {PROPERTY_ID}\n"
                f"  before={_text(left)}\n  after={_text(right)}"
            )

        # evidence reference — entry 목록·route·effort·계약 하위 전부
        refs_before = _evidence_reference_view(before)
        refs_after = _evidence_reference_view(after)
        assert _same("evidenceReferences", refs_before, refs_after), (
            f"{where}: evidence reference 의미 손실 — Property {PROPERTY_ID}\n"
            f"  before={_text(refs_before)}\n  after={_text(refs_after)}"
        )

        # R3. 파생 의미 보존 — fingerprint 재계산값·저장값·Malformed 판정
        assert canonicalizer.capability_fingerprint(
            after
        ) == canonicalizer.capability_fingerprint(before), (
            f"{where}: Capability_Fingerprint 재계산값 불일치 — Property {PROPERTY_ID}"
        )
        assert _get(after, "capabilityFingerprint") == _get(before, "capabilityFingerprint"), (
            f"{where}: 저장된 capabilityFingerprint 불일치 — Property {PROPERTY_ID}"
        )
        assert S.validate_entry_reasons(after) == S.validate_entry_reasons(before), (
            f"{where}: Malformed_Entry 판정이 round-trip으로 변했다 — Property {PROPERTY_ID}: "
            f"{S.validate_entry_reasons(before)} → {S.validate_entry_reasons(after)}"
        )

    # ── R1. 의미 보존 — map 전체 view (필드 화이트리스트 밖 손실까지 포착) ──
    view_before, view_after = _map_view(map_obj), _map_view(restored)
    assert canonicalizer.serialize(view_before) == canonicalizer.serialize(view_after), (
        f"Capability_Map 의미 view 불일치 — Property {PROPERTY_ID}\n"
        f"  before={_text(view_before)}\n  after={_text(view_after)}"
    )

    # ── R2. 재직렬화 바이트 동일 (Requirement 3.15, 12.14) ──────────
    text1 = canonicalizer.serialize(restored)
    assert text1 == text0, (
        f"재직렬화 문자열 불일치 — Property {PROPERTY_ID}\n"
        f"  first={text0[:400]}\n  again={text1[:400]}"
    )
    assert text1.encode("utf-8") == text0.encode("utf-8"), (
        f"재직렬화 UTF-8 바이트 불일치 — Property {PROPERTY_ID}"
    )
    assert canonicalizer.canonical_bytes(restored) == canonicalizer.canonical_bytes(map_obj), (
        f"canonical 바이트열 불일치 — Property {PROPERTY_ID}"
    )
    # 2회 round-trip도 같은 바이트열·같은 구조를 만든다(반복 안정성).
    twice = canonicalizer.deserialize(text1)
    assert canonicalizer.serialize(twice) == text0, (
        f"2회 round-trip 후 바이트열 불일치 — Property {PROPERTY_ID}"
    )
    assert twice == restored, f"2회 round-trip 후 구조 불일치 — Property {PROPERTY_ID}"

    # ── R4. 비-canonical 저장 텍스트도 의미 보존 ─────────────────────
    # key 순서·집합 의미 원소 순서가 정렬되지 않은 JSON 텍스트를 역직렬화해도 같은
    # canonical 구조·같은 직렬화 바이트를 만든다.
    raw_text = json.dumps(map_obj, ensure_ascii=True)
    from_raw = canonicalizer.deserialize(raw_text)
    assert from_raw == restored, (
        f"비-canonical 텍스트 역직렬화 구조 불일치 — Property {PROPERTY_ID}"
    )
    assert canonicalizer.serialize(from_raw) == text0, (
        f"비-canonical 텍스트 역직렬화 후 바이트열 불일치 — Property {PROPERTY_ID}"
    )


# ─────────────────────────────────────────────────────────────────
# Property 6 — 정확히 하나의 property test
# ─────────────────────────────────────────────────────────────────
@seed(S.AE_PBT_SEED)
@S.PBT_SETTINGS
@given(round_trip_maps())
def test_property6_serialization_round_trip(map_obj: dict) -> None:
    """Property 6: serialization round-trip.

    임의 Capability_Map을 ``serialize`` → ``deserialize`` 하면 model identity, provider,
    모든 Known_Route의 계약·상태, effort 계약·상태, evidence reference의 의미가 보존되고,
    다시 ``serialize`` 한 바이트열은 최초 결과와 동일하다.

    **Validates: Requirements 3.15, 12.14**
    """
    try:
        _assert_round_trip(map_obj)
    except AssertionError as exc:
        record_counterexample(str(exc), {"capabilityMap": map_obj})
        raise


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-p", "no:cacheprovider", "-q"]))
