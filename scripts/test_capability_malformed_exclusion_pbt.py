# Feature: gateway-models-effort-support, Property 2: Malformed 제외
# *For any* entry에 대해 필수 필드 삭제, 타입 변형, enum 이탈, Capability_Fingerprint 불일치,
# evidence 참조 무결성 실패 중 하나 이상을 임의로 주입하면, 해당 entry는 항상 Malformed_Entry로
# 판정되어 activation 입력에서 제외되고 Active_Model 집합과의 교집합은 공집합이다.
"""Property 2 (Malformed 제외) property test — task 5.5.

검증 대상 모듈:
  ``ai_engine/capability/contracts.py``
    - ``validate_entry``  Malformed 판정 이유 목록
    - ``is_malformed``    Malformed_Entry 여부
  ``ai_engine/capability/capability_map.py``
    - ``classify_entries`` / ``valid_entries``  유효 entry와 Malformed_Entry 분리
  ``ai_engine/capability/activation_gate.py``
    - ``is_active`` / ``active_models`` / ``activation_report``  활성 판정과 탈락 이유

단정 요약 (design.md Correctness Properties → Property 2):
  R1. **주입 전 원본은 유효** — mutation 이전 entry는 Malformed_Entry가 아니다. 따라서 이후
      판정 변화의 원인은 주입된 deviation 하나뿐이다.
  R2. **항상 Malformed 판정** — 주입 entry는 ``is_malformed``가 참이고 판정 이유가 1개 이상이며,
      모든 이유 코드는 닫힌 집합 ``contracts.MALFORMED_CODES`` 안에 있다. ``capability_map``의
      판정(canonicalizer 주입 경로)과 ``contracts``의 판정은 항상 같은 이유 목록을 낸다.
  R3. **주입 범주가 이유에 나타난다** — 단일 주입 케이스는 주입한 범주 코드가 이유 목록에 있다
      (5개 범주 각각이 실제로 그 범주로 판정된다).
  R4. **유효 entry 집합에서 분리** — ``classify_entries``/``valid_entries``가 주입 entry를
      Malformed 쪽으로만 분류하고 유효 쪽에는 남기지 않으며, 유효 쪽은 주입 전 map과 동일하다.
  R5. **Active_Model 교집합 공집합** — ``active_models`` 결과에 주입 entry가 하나도 없고,
      ``is_active``는 ``(False, MALFORMED_ENTRY | FINGERPRINT_MISMATCH)``를 돌려준다. ctx를
      주지 않아도(현재성 검사 생략) 결과는 같다 — 제외 근거가 ctx에 의존하지 않는다.
  R6. **activation 입력에서 제외** — 주입 entry를 제거한 map과 섞은 map의 Active_Model이 완전히
      동일하다. Malformed entry는 다른 entry의 활성 판정·duplicate 축약·`verifiedAt` tie 판정에
      영향을 주지 않는다.
  R7. **보고서 일관성** — ``activation_report``의 malformed count가 주입 entry 수와 정확히 같고,
      각 주입 entry가 Malformed 계열 탈락 record로 정확히 1회 기록된다.

mutation 5개 범주 전수 (``_capability_strategies.MUTATION_KINDS`` = ``contracts.MALFORMED_CODES``):
  ``MISSING_FIELD``(필수 필드 삭제), ``TYPE_MISMATCH``(타입 변형), ``ENUM_VIOLATION``(enum 이탈),
  ``FINGERPRINT_MISMATCH``(fingerprint 불일치), ``EVIDENCE_INTEGRITY``(evidence 참조 무결성 실패).
  **생성 case 하나에 5개 범주가 각각 1개씩 반드시 들어간다**(:func:`malformed_exclusion_cases`)
  — 범주 전수 도달이 실행 순서·seed·case 수에 의존하지 않는다. 여기에 복수 주입("하나 이상")
  케이스 1개를 더해 단일 주입과 중복 주입을 함께 exercise한다.

주입 전제에서 제외하는 한 가지 (:func:`_is_genuine_mutation`):
  ``displayName``처럼 계약이 nullable로 정의한 선택 필드(``contracts.OPTIONAL_ENTRY_FIELDS``)에
  ``None``을 기록하는 것은 **계약이 허용하는 값**이므로 "타입 변형" 주입이 아니다. 이 spec은
  Property 2의 전제(deviation 주입)를 만족하지 않으므로 입력에서 걸러낸다.

경계값(design.md "PBT 구성 규칙")은 입력 전략에서 명시적으로 포함한다: 유효 entry가 0개인 map
(주입 entry만 존재), 단일 유효 entry, 동일 modelId duplicate·동시각 tie·fingerprint 불일치·최신
`verifiedAt` 형제 entry, 빈 문자열 ID·`SEED` 출처·미완성 계약 entry. 상태 enum·계약 완전성·effort
domain(enum 단일값, range 상·하한 동일)은 공통 생성기가 전수로 섞는다.

model ID·provider·effort field path·effort 허용값은 확정 상수 없이 무작위 심볼로만 생성한다
(`scripts/_capability_strategies.py`). 이 테스트는 순수 로직만 구동하며 Gateway·네트워크·
파일시스템(실패 기록 제외)에 접근하지 않고, 결과는 Gateway 지원 근거가 아니다(Requirement 12.22).

실패 시 최소화된 counterexample과 재현 정보를
``.generated/pbt/capability_malformed_exclusion.json`` 에 기록한다(Validation_Runner 보고서
``pbt.counterexamples`` 입력, 작업 12.2). `print_blob=True` 로 Hypothesis 재현 blob도 함께 출력된다.

실행:
  ai_engine/.venv/bin/python -m pytest scripts/test_capability_malformed_exclusion_pbt.py -q

**Validates: Requirements 3.1, 3.2, 3.3, 3.4, 3.5, 3.6, 3.7, 3.8, 3.9, 3.10, 3.11, 3.17, 6.8, 12.10**
_Requirements: 3.17, 6.8, 12.10, 12.19, 12.21_
"""
from __future__ import annotations

import copy
import json
import os
import sys
from typing import Any, Mapping, Sequence

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
from ai_engine.capability import activation_gate, canonicalizer, capability_map, contracts  # noqa: E402

# ─────────────────────────────────────────────────────────────────
# property 식별 (보고서 기록용)
# ─────────────────────────────────────────────────────────────────
FEATURE = "gateway-models-effort-support"
PROPERTY_ID = 2
PROPERTY_LABEL = "Malformed 제외"

#: Malformed 계열 탈락 이유 코드(activation_gate가 Malformed entry에 붙이는 코드).
MALFORMED_EXCLUSION_REASONS: frozenset[str] = frozenset(
    {activation_gate.MALFORMED_ENTRY, activation_gate.FINGERPRINT_MISMATCH}
)


# ─────────────────────────────────────────────────────────────────
# counterexample 기록 (Requirement 12.21)
# ─────────────────────────────────────────────────────────────────

#: 기록 디렉터리. `.generated/` 는 gitignore 대상이며 env로 재지정할 수 있다.
COUNTEREXAMPLE_DIR = os.environ.get("AE_PBT_COUNTEREXAMPLE_DIR") or os.path.join(
    _ROOT, ".generated", "pbt"
)

#: 기록 파일 경로(property 1개당 1파일).
COUNTEREXAMPLE_PATH = os.path.join(COUNTEREXAMPLE_DIR, "capability_malformed_exclusion.json")

#: 재현 명령(보고서에 그대로 실을 수 있는 형태).
REPRODUCE_COMMAND = (
    f"AE_PBT_SEED={S.AE_PBT_SEED} ai_engine/.venv/bin/python -m pytest "
    "scripts/test_capability_malformed_exclusion_pbt.py -q"
)


def _json_safe(value: Any) -> Any:
    """counterexample 기록용 JSON 안전 표현(기록 실패가 판정을 가리지 않게 한다)."""
    try:
        return json.loads(canonicalizer.serialize(value))
    except Exception:  # pragma: no cover - 기록 경로 방어
        return repr(value)[:4000]


def record_counterexample(reason: str, counterexample: Mapping[str, Any]) -> None:
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
# 작은 유틸
# ─────────────────────────────────────────────────────────────────
def _text(value: Any) -> str:
    return value if isinstance(value, str) else ""


def _key(entry: Any) -> str:
    """entry의 canonical 신원 키.

    canonical serialization이 같으면 내용이 같고, 내용이 같으면 Malformed 판정도 같다.
    따라서 이 키로 만든 집합 비교는 "주입 entry가 활성 목록에 있는지"를 정확히 가린다.
    """
    try:
        return canonicalizer.serialize(entry)
    except Exception:  # pragma: no cover - 진단 경로 방어
        return "<unserializable>" + repr(entry)


def _keys(items: Sequence[Any]) -> list[str]:
    """신원 키 목록(정렬 — 저장 순서에 의존하지 않는 multiset 비교용)."""
    return sorted(_key(item) for item in items)


def _codes(reasons: Sequence[str]) -> set[str]:
    """판정 이유 문자열(``CODE:path(detail)``)에서 코드만 뽑는다."""
    return {str(reason).split(":", 1)[0] for reason in reasons}


def _short(value: Any, limit: int = 400) -> str:
    """실패 메시지용 축약 canonical 표현."""
    try:
        return canonicalizer.serialize(value)[:limit]
    except Exception:  # pragma: no cover - 메시지 경로 방어
        return repr(value)[:limit]


def _settle(entry: dict) -> dict:
    """파생 모드 상태를 routes에서 다시 유도하고 Capability_Fingerprint를 재계산한다.

    Complete_Record는 `syncSupport`/`asyncSupport`/`streamingSupport`가 routes에서 유도한
    값과 같기를 요구한다(Requirement 3.20~3.23). 파생 상태는 fingerprint 입력이 아니므로
    재계산 순서와 무관하게 저장 fingerprint는 항상 재계산값과 일치한다(Malformed 아님).
    """
    return S.refresh_fingerprint(capability_map.apply_mode_support(copy.deepcopy(entry)))


def _align(entry: dict, revision: str, catalog_fingerprint: str) -> dict:
    """entry의 현재성 필드를 공유 값으로 맞춘다(Activation_Gate 통과 가능 상태 확보).

    `catalogFingerprint`는 fingerprint 입력이므로 재계산이 필요하다.
    """
    aligned = copy.deepcopy(entry)
    aligned["revision"] = revision
    aligned["catalogFingerprint"] = catalog_fingerprint
    return _settle(aligned)


def _context_for(items: Sequence[dict], revision: str, catalog_fingerprint: str, now: str) -> dict:
    """Activation_Gate ctx. catalog 쪽 값은 entry identity에서 파생해 일관성을 유지한다.

    주입 entry의 원본 modelId까지 catalog에 포함시켜, "catalog에 있는 모델인데도 entry가
    Malformed라서 제외된다"는 상황을 만든다(제외 근거가 현재성 검사가 아님을 분리).
    """
    model_ids = sorted({_text(entry.get("modelId")) for entry in items if _text(entry.get("modelId"))})
    providers = {
        _text(entry.get("modelId")): _text(entry.get("provider"))
        for entry in items
        if _text(entry.get("modelId"))
    }
    return {
        "revision": revision,
        "catalogFingerprint": catalog_fingerprint,
        "catalogModelIds": model_ids,
        "catalogProviders": providers,
        "nowUtc": now,
    }


# ─────────────────────────────────────────────────────────────────
# mutation 전제 — 계약이 허용하는 값은 "주입"이 아니다
# ─────────────────────────────────────────────────────────────────
def _is_genuine_mutation(spec: Mapping[str, Any]) -> bool:
    """spec이 실제 deviation을 주입하는지.

    ``contracts.OPTIONAL_ENTRY_FIELDS``(nullable 선택 필드)에 ``None``을 기록하는 것은 계약이
    허용하는 값이므로 "타입 변형" 주입이 아니다 → Property 2의 전제에서 제외한다.
    """
    return not (
        spec.get("kind") == contracts.TYPE_MISMATCH
        and spec.get("field") in contracts.OPTIONAL_ENTRY_FIELDS
        and spec.get("value") is None
    )


def _is_genuine_case(case: Mapping[str, Any]) -> bool:
    """case의 모든 mutation spec이 실제 deviation인지."""
    return all(_is_genuine_mutation(spec) for spec in case.get("mutations") or ())


#: 복수 주입에 쓰는 범주. ``apply_mutation`` 체인에서 서로의 구조 가정을 깨지 않는 범주만 고른다
#: (``TYPE_MISMATCH``·``ENUM_VIOLATION``은 `routes`/`effort` 컨테이너 자체를 비-dict로 바꾸거나
#:  앞선 주입을 덮어써, 어떤 범주가 판정을 유발했는지 흐려진다).
SAFE_CHAIN_KINDS: tuple[str, ...] = (
    contracts.MISSING_FIELD,
    contracts.FINGERPRINT_MISMATCH,
    contracts.EVIDENCE_INTEGRITY,
)


def single_mutation_cases(kind: str, base: st.SearchStrategy[dict]) -> st.SearchStrategy[dict]:
    """범주 ``kind`` 하나만 주입한 case(공통 생성기 :func:`_capability_strategies.malformed_entries`)."""
    return (
        S.malformed_entries(kind=kind, base=base)
        .filter(_is_genuine_case)
        .map(lambda case: {**case, "chained": False})
    )


@st.composite
def chained_mutation_cases(draw: Any, base: st.SearchStrategy[dict]) -> dict:
    """서로 다른(또는 같은) 범주 2개를 연속 주입한 case — "하나 이상" 주입을 exercise한다.

    반환 형태는 :func:`_capability_strategies.malformed_entries`와 같다
    (``{"original", "mutations", "mutated", "expectedCodes", "chained"}``).
    체인은 앞선 주입 코드를 가릴 수 있으므로(예: 삭제된 `capabilityFingerprint`를 다음 주입이
    다시 채움) 이 case에는 범주별 이유 코드 단정(R3)을 적용하지 않는다.
    """
    original = draw(base)
    mutated = original
    specs: list[dict] = []
    for _ in range(2):
        spec = draw(
            S.malformed_mutations(mutated, kind=draw(st.sampled_from(SAFE_CHAIN_KINDS))).filter(
                _is_genuine_mutation
            )
        )
        mutated = S.apply_mutation(mutated, spec)
        specs.append(spec)
    return {
        "original": original,
        "mutations": specs,
        "mutated": mutated,
        "expectedCodes": [spec["kind"] for spec in specs],
        "chained": True,
    }


# ─────────────────────────────────────────────────────────────────
# 입력 전략 — 5개 mutation 범주 전수 + 유효 entry 혼합 map
# ─────────────────────────────────────────────────────────────────
@st.composite
def malformed_exclusion_cases(draw: Any) -> dict:
    """Property 2 입력.

    반환 형태::

        {"purposes", "ctx", "baseMap", "mixedMap", "cases"}

    ``baseMap``  주입 entry를 제외한 유효 entry만 담은 map(R6의 기준선)
    ``mixedMap`` 유효 entry와 주입 entry를 임의 위치로 섞은 map(판정 입력)
    ``cases``    주입 case 목록 — :data:`_capability_strategies.MUTATION_KINDS` 5개 범주 각
                 1개 + 복수 주입 1개

    Activation_Gate를 통과할 수 있는 유효 entry를 섞어 Active_Model이 비어 있지 않은 경로를
    exercise하고, 유효 entry가 0개인 경계(주입 entry만 존재하는 map)도 포함한다.
    """
    purposes = draw(S.purpose_lists(min_size=1, max_size=2))
    revision = draw(S.revisions(allow_empty=False))
    catalog_fingerprint = draw(S.catalog_fingerprints(allow_empty=False))
    now = draw(S.utc_timestamps())
    updated_at = draw(S.utc_timestamps(allow_undetermined=True))

    # ── 유효 entry — 활성 후보(현재성 정렬)와 임의 상태를 섞고, 0개 경계도 포함한다 ──
    base_items: list[dict] = [
        _align(draw(S.entries(active_ready=True, purposes=purposes)), revision, catalog_fingerprint)
        for _ in range(draw(st.integers(min_value=0, max_value=2)))
    ]
    for _ in range(draw(st.integers(min_value=0, max_value=2))):
        other = draw(S.entries(purposes=purposes))
        base_items.append(
            _align(other, revision, catalog_fingerprint) if draw(st.booleans()) else other
        )

    # 경계값: 동일 modelId duplicate·동시각 tie·fingerprint 불일치·최신 verifiedAt 형제.
    if base_items and draw(st.booleans()):
        _, sibling = draw(S.sibling_entries(draw(st.sampled_from(base_items))))
        base_items.append(sibling)

    # ── 주입 entry — 5개 범주 전수(각 1개) + 복수 주입 1개 ──
    ready_base = S.entries(active_ready=True, purposes=purposes).map(
        lambda entry: _align(entry, revision, catalog_fingerprint)
    )
    mixed_base = S.entries(purposes=purposes).map(
        lambda entry: _align(entry, revision, catalog_fingerprint)
    )
    # 절반은 활성 후보 원본(주입만이 제외 원인), 절반은 임의 상태 원본.
    cases: list[dict] = [
        draw(single_mutation_cases(kind, ready_base if draw(st.booleans()) else mixed_base))
        for kind in S.MUTATION_KINDS
    ]
    cases.append(draw(chained_mutation_cases(ready_base)))

    # ── map 구성 — 주입 entry를 임의 위치에 끼워 넣는다(위치 무관 판정) ──
    mixed_items = list(base_items)
    for case in cases:
        index = draw(st.integers(min_value=0, max_value=len(mixed_items)))
        mixed_items.insert(index, case["mutated"])

    ctx = _context_for(
        base_items + [case["original"] for case in cases], revision, catalog_fingerprint, now
    )
    return {
        "purposes": purposes,
        "ctx": ctx,
        "baseMap": S.new_capability_map(base_items, updated_at=updated_at),
        "mixedMap": S.new_capability_map(mixed_items, updated_at=updated_at),
        "cases": cases,
    }


# ─────────────────────────────────────────────────────────────────
# 단정 본문 (단일 property test에서만 호출한다)
# ─────────────────────────────────────────────────────────────────
def _assert_entry_level(case: Mapping[str, Any]) -> list[str]:
    """R1~R3 — entry 단위 Malformed 판정. 판정 이유 목록을 돌려준다."""
    original, mutated = case["original"], case["mutated"]
    specs = case["mutations"]

    # R1. 주입 전 원본은 유효하다 → 이후 판정 변화의 원인은 주입뿐이다.
    original_reasons = S.validate_entry_reasons(original)
    assert not original_reasons, (
        f"주입 전 원본이 이미 Malformed다 — Property {PROPERTY_ID}: {original_reasons}\n"
        f"  entry={_short(original)}"
    )

    # R2. 주입 entry는 항상 Malformed다.
    reasons = contracts.validate_entry(
        mutated, fingerprint_fn=canonicalizer.capability_fingerprint
    )
    assert reasons, (
        f"주입했는데 Malformed로 판정되지 않았다 — Property {PROPERTY_ID}\n"
        f"  mutations={_short(specs)}\n  entry={_short(mutated)}"
    )
    assert contracts.is_malformed(
        mutated, fingerprint_fn=canonicalizer.capability_fingerprint
    ), f"is_malformed가 False다 — Property {PROPERTY_ID}: mutations={_short(specs)}"

    codes = _codes(reasons)
    unknown = codes - set(contracts.MALFORMED_CODES)
    assert not unknown, (
        f"닫힌 집합 밖의 판정 코드가 나왔다 — Property {PROPERTY_ID}: {sorted(unknown)}"
    )

    # R2. capability_map 경로(canonicalizer 주입)와 contracts 경로의 판정이 같다.
    map_reasons = capability_map.entry_malformed_reasons(mutated)
    assert map_reasons == reasons, (
        f"capability_map과 contracts의 Malformed 판정이 다르다 — Property {PROPERTY_ID}\n"
        f"  capability_map={map_reasons}\n  contracts={reasons}"
    )

    # R3. 단일 주입은 주입한 범주 코드가 이유에 나타난다(체인은 앞선 코드를 가릴 수 있어 제외).
    if not case.get("chained"):
        for expected in case["expectedCodes"]:
            assert expected in codes, (
                f"주입 범주 {expected}가 판정 이유에 없다 — Property {PROPERTY_ID}\n"
                f"  mutations={_short(specs)}\n  reasons={reasons}"
            )
    return reasons


def _assert_malformed_exclusion(scenario: Mapping[str, Any]) -> None:
    """Property 2의 R1~R7 단정.

    단정 순서는 **진단이 좁은 것부터**다: entry 단위 판정(R1~R3) → 유효/Malformed 분리(R4) →
    entry 단위 활성 판정(R5) → map 단위 Active_Model 교집합·기준선 동일(R5, R6) → 보고서(R7).
    """
    ctx = scenario["ctx"]
    purposes = scenario["purposes"]
    base_map, mixed_map = scenario["baseMap"], scenario["mixedMap"]
    cases = scenario["cases"]
    injected = [case["mutated"] for case in cases]
    injected_keys = set(_keys(injected))

    # 전제: 5개 mutation 범주가 이 case 하나에 모두 들어 있다(전수 도달이 seed에 의존하지 않는다).
    covered = {code for case in cases if not case.get("chained") for code in case["expectedCodes"]}
    assert covered == set(S.MUTATION_KINDS), (
        f"mutation 범주 전수가 아니다 — Property {PROPERTY_ID}: "
        f"{sorted(covered)} != {sorted(S.MUTATION_KINDS)}"
    )

    # ── R1~R3. entry 단위 판정 ────────────────────────────────────
    reasons_by_key: dict[str, list[str]] = {}
    for case in cases:
        reasons_by_key[_key(case["mutated"])] = _assert_entry_level(case)

    # 전제: 유효 entry만으로 만든 기준선 map에는 Malformed_Entry가 없다.
    base_valid, base_malformed = capability_map.valid_entries(base_map)
    assert not base_malformed, (
        f"기준선 map에 Malformed_Entry가 섞였다 — Property {PROPERTY_ID}: "
        f"{[_short(entry, 200) for entry in base_malformed]}"
    )

    # ── R4. 유효 entry 집합에서 분리 (Requirement 3.17) ───────────
    valid, malformed_pairs = capability_map.classify_entries(mixed_map)
    valid_keys, malformed_keys = set(_keys(valid)), set(_keys([entry for entry, _ in malformed_pairs]))
    assert injected_keys & valid_keys == set(), (
        f"주입 entry가 유효 entry로 분류됐다 — Property {PROPERTY_ID}: "
        f"{sorted(injected_keys & valid_keys)[:1]}"
    )
    assert injected_keys <= malformed_keys, (
        f"주입 entry가 Malformed 집합에 없다 — Property {PROPERTY_ID}: "
        f"{sorted(injected_keys - malformed_keys)[:1]}"
    )
    assert _keys(valid) == _keys(base_valid), (
        f"Malformed 혼입이 유효 entry 집합을 바꿨다 — Property {PROPERTY_ID}\n"
        f"  mixed={len(valid)}개, base={len(base_valid)}개"
    )
    for entry, pair_reasons in malformed_pairs:
        key = _key(entry)
        if key in reasons_by_key:
            assert pair_reasons == reasons_by_key[key], (
                f"classify_entries의 판정 이유가 entry 단위 판정과 다르다 — "
                f"Property {PROPERTY_ID}\n  classify={pair_reasons}\n"
                f"  entry={reasons_by_key[key]}"
            )
    # `valid_entries`는 `classify_entries`와 같은 분리 결과를 내야 한다.
    mixed_valid, mixed_malformed = capability_map.valid_entries(mixed_map)
    assert _keys(mixed_valid) == _keys(valid) and _keys(mixed_malformed) == _keys(
        [entry for entry, _ in malformed_pairs]
    ), f"valid_entries와 classify_entries의 분리 결과가 다르다 — Property {PROPERTY_ID}"

    # ── R5. entry 단위 활성 판정 (Requirement 6.8) ────────────────
    for case in cases:
        for label, context in (("ctx 있음", ctx), ("ctx 없음", None)):
            active, reason = activation_gate.is_active(
                case["mutated"], context, purposes=purposes
            )
            assert not active, (
                f"주입 entry가 활성으로 판정됐다({label}) — Property {PROPERTY_ID}\n"
                f"  mutations={_short(case['mutations'])}"
            )
            assert reason in MALFORMED_EXCLUSION_REASONS, (
                f"주입 entry의 탈락 이유가 Malformed 계열이 아니다({label}) — "
                f"Property {PROPERTY_ID}: {reason}\n  mutations={_short(case['mutations'])}"
            )

    # ── R5. Active_Model 교집합 공집합 (Requirement 6.8, 12.10) ───
    active = activation_gate.active_models(mixed_map, ctx, purposes=purposes)
    active_keys = set(_keys(active))
    overlap = injected_keys & active_keys
    assert overlap == set(), (
        f"주입 entry가 Active_Model에 노출됐다 — Property {PROPERTY_ID}: "
        f"{sorted(overlap)[:1]}"
    )
    assert active_keys <= valid_keys, (
        f"Active_Model이 유효 entry 집합을 벗어났다 — Property {PROPERTY_ID}"
    )

    # ── R6. activation 입력에서 제외 (Requirement 3.17) ───────────
    base_active = activation_gate.active_models(base_map, ctx, purposes=purposes)
    assert _keys(active) == _keys(base_active), (
        f"Malformed 혼입이 Active_Model 집합을 바꿨다 — Property {PROPERTY_ID}\n"
        f"  mixed={len(active)}개, base={len(base_active)}개"
    )
    assert activation_gate.active_model_ids(
        mixed_map, ctx, purposes=purposes
    ) == activation_gate.active_model_ids(base_map, ctx, purposes=purposes), (
        f"Malformed 혼입이 Active_Model의 Exact_Model_ID 목록을 바꿨다 — Property {PROPERTY_ID}"
    )

    # ── R7. 보고서 일관성 ─────────────────────────────────────────
    report = activation_gate.activation_report(mixed_map, ctx, purposes=purposes)
    assert report["counts"]["malformed"] == len(injected), (
        f"보고서의 Malformed count가 주입 수와 다르다 — Property {PROPERTY_ID}: "
        f"{report['counts']['malformed']} != {len(injected)}"
    )
    assert _keys(report["active"]) == _keys(active), (
        f"activation_report와 active_models 결과가 다르다 — Property {PROPERTY_ID}"
    )
    malformed_records = [
        record
        for record in report["excluded"]
        if record.get("reason") in MALFORMED_EXCLUSION_REASONS
    ]
    assert len(malformed_records) == len(injected), (
        f"Malformed 탈락 record 수가 주입 수와 다르다 — Property {PROPERTY_ID}: "
        f"{len(malformed_records)} != {len(injected)}"
    )


# ─────────────────────────────────────────────────────────────────
# Property 2 — 정확히 하나의 property test
# ─────────────────────────────────────────────────────────────────
@seed(S.AE_PBT_SEED)
@S.PBT_SETTINGS
@given(malformed_exclusion_cases())
def test_property2_malformed_exclusion(scenario: dict) -> None:
    """Property 2: Malformed 제외.

    임의 entry에 필수 필드 삭제·타입 변형·enum 이탈·Capability_Fingerprint 불일치·evidence
    참조 무결성 실패 중 하나 이상을 주입하면, 그 entry는 항상 Malformed_Entry로 판정되어
    activation 입력에서 제외되고 Active_Model 집합과의 교집합은 공집합이다.

    **Validates: Requirements 3.1, 3.2, 3.3, 3.4, 3.5, 3.6, 3.7, 3.8, 3.9, 3.10, 3.11, 3.17,
    6.8, 12.10**
    """
    try:
        _assert_malformed_exclusion(scenario)
    except AssertionError as exc:
        record_counterexample(
            str(exc),
            {
                "mutations": [case["mutations"] for case in scenario["cases"]],
                "injectedEntries": [case["mutated"] for case in scenario["cases"]],
                "originalEntries": [case["original"] for case in scenario["cases"]],
                "baseMap": scenario["baseMap"],
                "mixedMap": scenario["mixedMap"],
                "ctx": scenario["ctx"],
                "purposes": scenario["purposes"],
            },
        )
        raise


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-p", "no:cacheprovider", "-q"]))
