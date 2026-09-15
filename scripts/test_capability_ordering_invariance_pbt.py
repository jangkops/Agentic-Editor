# Feature: gateway-models-effort-support, Property 7: ordering invariance
# *For any* catalog 입력 순서, entry 순서, object key 순서, 집합 의미 collection의 원소 순서
# 치환에 대해, 산출된 Active_Model 집합과 Capability_Fingerprint는 변하지 않는다. 또한
# fingerprint 제외 입력(Candidate_Label, display name, UTC 시각, revision, evidence 저장
# 위치, Evidence_Record_ID, 로그, Sanitized_Schema)만 변경해도 fingerprint는 불변이며,
# 동일 Exact_Model_ID의 중복·다중 entry 축약 결과(최신 `verifiedAt` 단일 노출, fingerprint
# 불일치·시각 tie 미노출, 동시각 tie는 Evidence_Record_ID 오름차순 첫 record 선택)도 입력
# 순서와 무관하다.
"""Property 7 (ordering invariance) property test — task 5.6.

검증 대상 모듈:
  ``ai_engine/capability/canonicalizer.py``
    - ``canonical`` / ``serialize``          object key·집합 의미 원소 순서 정규화
    - ``capability_fingerprint``             포함·제외 입력 화이트리스트
    - ``catalog_fingerprint``                catalog 입력 순서 무관 변경 감지값
  ``ai_engine/capability/activation_gate.py``
    - ``activation_report``                  Active_Model 집합(``active_models``의 근원)과
                                             탈락 이유 코드
  ``ai_engine/capability/capability_map.py``
    - ``select_active_evidence``             활성 evidence 선택(최신 → 동시각은 ID 오름차순)

단정 요약 (design.md Correctness Properties → Property 7)
  R1. **entry 단위 순서 불변** — object key 순서와 집합 의미 collection(`invocationModelIds`,
      `evidence`, `purposes`, `optionalFields`, `enumValues`, `verifiedValues`)의 원소 순서를
      치환해도 ``canonical``·``serialize`` 결과, Capability_Fingerprint, Malformed 판정이
      모두 동일하다. 순서 의미 경로(field path)는 치환하지 않는다(3.12, 3.14).
  R2. **map 단위 entry 순서 불변** — `entries` 순서를 치환해도 entry canonical 직렬화
      다중집합과 Capability_Fingerprint 다중집합이 같다.
  R3. **Active_Model 집합·탈락 이유 불변** — entry 순서·object key 순서·집합 의미 원소 순서·
      ctx의 catalog model ID 순서를 치환해도 ``activation_report``의 ``active``·``excluded``·
      ``counts``가 바이트 수준에서 동일하다(6.22~6.25, 12.15).
  R4. **duplicate 축약·tie 규칙의 순서 무관성** — 활성 후보에서 독립적으로 계산한 기대 노출
      (canonical 동일 duplicate 1개 축약 → 동일 Exact_Model_ID는 최신 `verifiedAt` 단일 노출 →
      최신끼리 fingerprint 불일치 또는 시각 tie면 미노출)과 구현 결과가 **모든 입력 순서에서**
      일치하고, 탈락 이유 코드도 기대 코드를 포함한다(6.22, 6.23, 6.24, 6.25).
  R5. **fingerprint 제외 입력 변경 → fingerprint 불변** — `candidateLabel`, `displayName`,
      `verifiedAt`, `revision`, evidence ID(저장 위치)만 바꿔도 재계산 fingerprint와 저장
      fingerprint가 그대로다(3.13). 반대로 fingerprint **포함** 입력(route 상태·allowlist)을
      바꾸면 fingerprint는 반드시 달라진다(3.12).
  R6. **catalog 입력 순서 불변** — catalog snapshot의 model 나열 순서·object key 순서를
      치환하고 수집 메타데이터(수집 시각·수집자)를 바꿔도 Catalog_Fingerprint는 불변이다.
  R7. **활성 evidence 선택의 순서 무관성** — 동일 Exact_Model_ID·동일 Capability_Fingerprint
      record 목록을 임의로 치환해도 ``select_active_evidence``는 같은 record를 고르며, 그
      record는 최신 `verifiedAt`이고 동시각이면 Evidence_Record_ID 오름차순 첫 record다
      (3.18, 3.19).

기대 노출(R4)과 활성 evidence 선택(R7)은 production 함수를 쓰지 않고 requirements.md의
규칙을 그대로 재구현한 **독립 oracle**로 계산한다. entry 단위 활성 조건 자체는 Property 1의
책임이므로 ``activation_gate.is_active``를 후보 판정 입력으로만 사용한다.

경계값(design.md "PBT 구성 규칙")은 입력 전략에서 명시적으로 포함한다: 빈 map, 단일 entry,
동일 modelId duplicate·동시각 tie·fingerprint 불일치·최신 `verifiedAt` 형제 entry
(:data:`_capability_strategies.SIBLING_KINDS` **전수**), enum 단일값·range 상·하한 동일
domain, 빈 문자열 ID, 상태 enum 전수, 미확정 `verifiedAt` evidence record.

model ID·provider·route 지원 여부·effort field path·effort 허용값은 확정 상수 없이 무작위
심볼로만 생성한다(`scripts/_capability_strategies.py`). 이 테스트는 순수 로직만 구동하며
Gateway·네트워크에 접근하지 않고, 결과는 Gateway 지원 근거가 아니다(Requirement 12.22).

실패 시 최소화된 counterexample과 재현 정보를
``.generated/pbt/capability_ordering_invariance.json`` 에 기록한다(Validation_Runner 보고서
``pbt.counterexamples`` 입력, 작업 12.2). `print_blob=True` 로 재현 blob도 함께 출력된다.

실행:
  ai_engine/.venv/bin/python -m pytest scripts/test_capability_ordering_invariance_pbt.py -q

**Validates: Requirements 3.12, 3.13, 3.18, 3.19, 6.22, 6.23, 6.24, 6.25, 12.15**

_Requirements: 3.12, 3.13, 6.22, 6.23, 12.15, 12.19, 12.21_
"""
from __future__ import annotations

import copy
import json
import os
import sys
from datetime import datetime
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
PROPERTY_ID = 7
PROPERTY_LABEL = "ordering invariance"


# ─────────────────────────────────────────────────────────────────
# counterexample 기록 (Requirement 12.21)
# ─────────────────────────────────────────────────────────────────

#: 기록 디렉터리. `.generated/` 는 gitignore 대상이며 env로 재지정할 수 있다.
COUNTEREXAMPLE_DIR = os.environ.get("AE_PBT_COUNTEREXAMPLE_DIR") or os.path.join(
    _ROOT, ".generated", "pbt"
)

#: 기록 파일 경로(property 1개당 1파일).
COUNTEREXAMPLE_PATH = os.path.join(
    COUNTEREXAMPLE_DIR, "capability_ordering_invariance.json"
)

#: 재현 명령(보고서에 그대로 실을 수 있는 형태).
REPRODUCE_COMMAND = (
    f"AE_PBT_SEED={S.AE_PBT_SEED} ai_engine/.venv/bin/python -m pytest "
    "scripts/test_capability_ordering_invariance_pbt.py -q"
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
# 작은 유틸
# ─────────────────────────────────────────────────────────────────
def _text(value: Any) -> str:
    return value if isinstance(value, str) else contracts.UNDETERMINED


def _sort_bytes(text: str) -> bytes:
    """UTF-8 바이트 순 정렬 키(canonicalizer·activation_gate와 동일 규칙)."""
    return text.encode("utf-8", "surrogatepass")


def _ser(value: Any) -> str:
    """canonical 직렬화(비교 기준 바이트열)."""
    return canonicalizer.serialize(value)


def _brief(value: Any) -> str:
    """실패 메시지용 축약 canonical 표현."""
    try:
        return _ser(value)[:400]
    except Exception:  # pragma: no cover - 메시지 경로 방어
        return repr(value)[:400]


def _rank(verified_at: Any) -> tuple[int, float]:
    """`verifiedAt` 비교 키(oracle). 형식 위반·미확정은 항상 확정 시각보다 낮다."""
    if not contracts.is_utc_iso8601(verified_at):
        return (0, 0.0)
    try:
        return (1, datetime.fromisoformat(verified_at).timestamp())
    except ValueError:  # pragma: no cover - is_utc_iso8601이 선차단한다
        return (0, 0.0)


def _settle(entry: dict) -> dict:
    """파생 모드 상태를 routes에서 다시 유도하고 Capability_Fingerprint를 재계산한다.

    Complete_Record는 `syncSupport`/`asyncSupport`/`streamingSupport`가 routes에서 유도한
    값과 같기를 요구한다(Requirement 3.20~3.23). 파생 상태는 fingerprint 입력이 아니므로
    재계산 순서와 무관하게 저장 fingerprint는 항상 재계산값과 일치한다(Malformed 아님).
    """
    settled = capability_map.apply_mode_support(copy.deepcopy(entry))
    return S.refresh_fingerprint(settled)


def _align(entry: dict, revision: str, catalog_fingerprint: str) -> dict:
    """entry의 현재성 필드를 공유 값으로 맞춘다(Activation_Gate 통과 가능 상태 확보).

    `catalogFingerprint`는 fingerprint 입력이므로 :func:`_settle`이 재계산한다.
    """
    aligned = copy.deepcopy(entry)
    aligned["revision"] = revision
    aligned["catalogFingerprint"] = catalog_fingerprint
    return _settle(aligned)


def _context_for(items: Sequence[dict], revision: str, catalog_fingerprint: str, now: str) -> dict:
    """Activation_Gate ctx. catalog 쪽 값은 map identity에서 파생해 일관성을 유지한다."""
    model_ids = sorted(
        {_text(entry.get("modelId")) for entry in items if _text(entry.get("modelId"))}
    )
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


def _report_view(report: Mapping[str, Any]) -> dict:
    """``activation_report`` 결과의 순서 비교용 view(모든 값을 canonical 바이트로 환원)."""
    return {
        "active": [_ser(entry) for entry in report.get("active") or []],
        "excluded": [_ser(record) for record in report.get("excluded") or []],
        "counts": dict(report.get("counts") or {}),
    }


# ─────────────────────────────────────────────────────────────────
# 독립 oracle — duplicate 축약·tie 규칙 (Requirement 6.22~6.25)
# ─────────────────────────────────────────────────────────────────
def _expected_exposure(
    map_obj: Mapping[str, Any],
    ctx: Mapping[str, Any],
    purposes: Sequence[str],
) -> tuple[dict[str, str], dict[str, set[str]]]:
    """기대 노출과 기대 탈락 이유를 순서 의존 없이 계산한다.

    규칙(requirements.md 6.22~6.25):
      1. canonical serialization이 같은 duplicate는 하나로 축약한다.
      2. 동일 Exact_Model_ID의 유효 entry가 복수면 가장 늦은 `verifiedAt` 단일 entry만 노출한다.
      3. 최신 entry들의 Capability_Fingerprint가 다르면 그 Exact_Model_ID를 노출하지 않는다.
      4. 최신 entry들이 같은 `verifiedAt`으로 tie면 그 Exact_Model_ID를 노출하지 않는다.

    Returns:
        ``({modelId: 노출 entry의 canonical 직렬화}, {modelId: 기대 탈락 이유 코드 집합})``.
    """
    candidates = [
        entry
        for entry in capability_map.entries_of(map_obj)
        if activation_gate.is_active(entry, ctx, purposes=purposes)[0]
    ]

    # (1) duplicate 축약 — 내용이 같으므로 어느 것을 남겨도 직렬화는 같다.
    unique: dict[str, dict] = {}
    reasons: dict[str, set[str]] = {}
    for entry in candidates:
        text = _ser(entry)
        if text in unique:
            reasons.setdefault(_text(entry.get("modelId")), set()).add(
                activation_gate.DUPLICATE_COLLAPSED
            )
            continue
        unique[text] = entry

    groups: dict[str, list[dict]] = {}
    for entry in unique.values():
        groups.setdefault(_text(entry.get("modelId")), []).append(entry)

    exposed: dict[str, str] = {}
    for model_id, group in groups.items():
        if len(group) == 1:
            exposed[model_id] = _ser(group[0])
            continue

        ranks = [_rank(item.get("verifiedAt")) for item in group]
        top = max(ranks)
        latest = [item for item, rank in zip(group, ranks) if rank == top]
        if len(latest) < len(group):  # (2) 밀린 entry
            reasons.setdefault(model_id, set()).add(activation_gate.SUPERSEDED_BY_NEWER)
        if len(latest) == 1:
            exposed[model_id] = _ser(latest[0])
            continue

        fingerprints = {_text(item.get("capabilityFingerprint")) for item in latest}
        reasons.setdefault(model_id, set()).add(
            activation_gate.FINGERPRINT_DIVERGENCE  # (3)
            if len(fingerprints) > 1
            else activation_gate.VERIFIED_AT_TIE  # (4)
        )
    return exposed, reasons


def _expected_active_evidence(
    records: Sequence[Any],
    *,
    model_id: str,
    capability_fingerprint: str,
) -> dict | None:
    """활성 evidence oracle — 최신 `verifiedAt`, 동시각은 Evidence_Record_ID 오름차순 첫 record."""
    candidates = [
        record
        for record in records
        if isinstance(record, dict)
        and _text(record.get("modelId")) == model_id
        and _text(record.get("capabilityFingerprint")) == capability_fingerprint
    ]
    if not candidates:
        return None
    top = max(_rank(record.get("verifiedAt")) for record in candidates)
    latest = [record for record in candidates if _rank(record.get("verifiedAt")) == top]
    return min(latest, key=lambda record: _text(record.get("evidenceRecordId")))


# ─────────────────────────────────────────────────────────────────
# 입력 전략
# ─────────────────────────────────────────────────────────────────
@st.composite
def catalog_records(draw: Any) -> list[dict]:
    """catalog snapshot의 모델 record 목록(identity·provider·route·capability + 수집 메타데이터).

    model ID·provider·capability 값은 전부 무작위 심볼이다. `routes`는 순서 의미 자리가
    아니지만 canonicalizer의 집합 의미 경로도 아니므로 **원 순서를 보존**해야 하는 목록이며,
    이 테스트는 그 목록을 치환하지 않는다(치환 대상은 model 나열 순서와 object key 순서다).
    """
    model_ids = draw(st.lists(S.model_ids(allow_empty=False), min_size=1, max_size=3, unique=True))
    records: list[dict] = []
    for model_id in model_ids:
        records.append(
            {
                "modelId": model_id,
                "provider": draw(S.providers(allow_empty=False)),
                "routes": draw(st.lists(S.known_routes(), min_size=0, max_size=3, unique=True)),
                "capabilities": draw(st.dictionaries(S.symbols(), S.json_scalars(), max_size=2)),
                "collectedAt": draw(S.utc_timestamps()),
                "collector": draw(S.symbols()),
            }
        )
    return records


@st.composite
def evidence_pools(draw: Any, model_id: str, capability_fingerprint: str) -> list[dict]:
    """Verification_Record 목록(동시각 tie·미확정 시각·다른 identity 잡음 포함).

    시각 pool을 좁게 잡아 동시각 tie(Requirement 3.19)를 자주 만들고, 잡음 record로
    modelId·Capability_Fingerprint 필터(3.18) 경계를 함께 exercise한다.
    """
    stamps = draw(st.lists(S.utc_timestamps(), min_size=1, max_size=2, unique=True))
    record_ids = draw(st.lists(S.evidence_ids(), min_size=2, max_size=4, unique=True))

    records: list[dict] = [
        {
            "evidenceRecordId": record_id,
            "modelId": model_id,
            "capabilityFingerprint": capability_fingerprint,
            "verifiedAt": draw(st.sampled_from(stamps)),
        }
        for record_id in record_ids
    ]
    # 시각 미확정 경계값 — 확정 시각보다 항상 낮은 순위여야 한다.
    if draw(st.booleans()):
        records.append(
            {
                "evidenceRecordId": draw(S.evidence_ids().filter(lambda ref: ref not in record_ids)),
                "modelId": model_id,
                "capabilityFingerprint": capability_fingerprint,
                "verifiedAt": contracts.UNDETERMINED,
            }
        )
    # 잡음 — 다른 modelId 또는 다른 Capability_Fingerprint.
    for _ in range(draw(st.integers(min_value=0, max_value=2))):
        records.append(
            {
                "evidenceRecordId": draw(S.evidence_ids()),
                "modelId": draw(
                    st.one_of(st.just(model_id), S.model_ids(allow_empty=False))
                ),
                "capabilityFingerprint": draw(
                    st.one_of(
                        st.just(capability_fingerprint), S.capability_fingerprint_strings()
                    )
                ),
                "verifiedAt": draw(S.utc_timestamps()),
            }
        )
    return records


@st.composite
def ordering_cases(draw: Any) -> dict:
    """Property 7 입력.

    반환 형태::

        {"purposes", "ctx", "updatedAt", "baseEntry", "noiseEntries", "siblings",
         "reorderSeeds", "perturbation", "catalog", "catalogPermutation",
         "evidenceRecords", "evidencePermutation", "evidenceFilter"}

    Activation_Gate를 통과할 수 있는 base entry를 만들고 :data:`SIBLING_KINDS` **전 종류**의
    형제 entry를 함께 draw한다(:func:`_scenario_maps`가 kind별 map으로 조립하므로 한
    example에서 duplicate·동시각 tie·fingerprint 불일치·최신 `verifiedAt` 규칙에 전수
    도달한다). 파생 구조(scenario map)는 반환하지 않는다 — 실패 시 counterexample repr가
    불필요하게 커지지 않도록 draw한 입력만 돌려준다.
    """
    purposes = draw(S.purpose_lists(min_size=1, max_size=2))
    revision = draw(S.revisions(allow_empty=False))
    catalog_fingerprint = draw(S.catalog_fingerprints(allow_empty=False))
    now = draw(S.utc_timestamps())

    base = _align(draw(S.entries(active_ready=True, purposes=purposes)), revision, catalog_fingerprint)

    # 임의 상태 noise entry(상태 enum 전수·빈 문자열 ID·미완성 계약·`SEED` 출처 포함).
    noise: list[dict] = []
    for _ in range(draw(st.integers(min_value=0, max_value=2))):
        other = draw(S.entries(purposes=purposes))
        noise.append(
            _align(other, revision, catalog_fingerprint) if draw(st.booleans()) else other
        )

    # 형제 entry 전수 — duplicate는 바이트 동일을 유지해야 하므로 settle하지 않는다.
    siblings: dict[str, dict] = {}
    for kind in S.SIBLING_KINDS:
        _, sibling = draw(S.sibling_entries(base, kind=kind))
        siblings[kind] = sibling if kind == "duplicate" else _settle(sibling)

    ctx = _context_for([base, *noise, *siblings.values()], revision, catalog_fingerprint, now)
    catalog = draw(catalog_records())
    evidence_filter = {
        "modelId": _text(base.get("modelId")),
        "capabilityFingerprint": _text(base.get("capabilityFingerprint")),
    }
    records = draw(
        evidence_pools(evidence_filter["modelId"], evidence_filter["capabilityFingerprint"])
    )

    return {
        "purposes": purposes,
        "ctx": ctx,
        "updatedAt": now,
        "baseEntry": base,
        "noiseEntries": noise,
        "siblings": siblings,
        "reorderSeeds": draw(st.lists(S.reorder_seeds(), min_size=2, max_size=2)),
        "perturbation": draw(S.excluded_perturbations()),
        "catalog": catalog,
        "catalogPermutation": draw(st.permutations(catalog)),
        "evidenceRecords": records,
        "evidencePermutation": draw(st.permutations(records)),
        "evidenceFilter": evidence_filter,
    }


def _scenario_maps(case: Mapping[str, Any]) -> dict[str, dict]:
    """draw한 entry로 scenario map을 조립한다(경계값: 빈 map·단일 entry·형제 kind 전수).

    - ``empty``            빈 map
    - ``single``           단일 entry(base)
    - ``general``          base + 임의 상태 noise entry
    - ``sibling:{kind}``   base + :data:`SIBLING_KINDS` 각 형제(duplicate·tie·
                           divergentFingerprint·newer)
    """
    base = case["baseEntry"]
    now = case["updatedAt"]
    scenarios: dict[str, dict] = {
        "empty": S.new_capability_map([], updated_at=now),
        "single": S.new_capability_map([base], updated_at=now),
        "general": S.new_capability_map([base, *case["noiseEntries"]], updated_at=now),
    }
    for kind, sibling in case["siblings"].items():
        scenarios[f"sibling:{kind}"] = S.new_capability_map([base, sibling], updated_at=now)
    return scenarios


# ─────────────────────────────────────────────────────────────────
# 단정 본문 (단일 property test에서만 호출한다)
# ─────────────────────────────────────────────────────────────────
def _assert_entry_order_invariance(where: str, entry: dict, seeds: Sequence[int]) -> None:
    """R1: entry의 object key·집합 의미 원소 순서 치환은 canonical·fingerprint·판정을 바꾸지 않는다."""
    text = _ser(entry)
    fingerprint = canonicalizer.capability_fingerprint(entry)
    canonical_form = canonicalizer.canonical(entry)
    reasons = S.validate_entry_reasons(entry)

    for order_seed in seeds:
        shuffled = S.reorder(entry, order_seed)
        assert _ser(shuffled) == text, (
            f"{where}: 순서 치환이 canonical 직렬화를 바꿨다 — Property {PROPERTY_ID}"
            f"(seed={order_seed})\n  before={text[:400]}\n  after={_ser(shuffled)[:400]}"
        )
        assert canonicalizer.canonical(shuffled) == canonical_form, (
            f"{where}: 순서 치환이 canonical form을 바꿨다 — Property {PROPERTY_ID}"
            f"(seed={order_seed})"
        )
        assert canonicalizer.capability_fingerprint(shuffled) == fingerprint, (
            f"{where}: 순서 치환이 Capability_Fingerprint를 바꿨다 — Property {PROPERTY_ID}"
            f"(seed={order_seed})"
        )
        assert _text(shuffled.get("capabilityFingerprint")) == _text(
            entry.get("capabilityFingerprint")
        ), f"{where}: 순서 치환이 저장 fingerprint를 바꿨다 — Property {PROPERTY_ID}"
        assert S.validate_entry_reasons(shuffled) == reasons, (
            f"{where}: 순서 치환이 Malformed 판정을 바꿨다 — Property {PROPERTY_ID}: "
            f"{reasons} → {S.validate_entry_reasons(shuffled)}"
        )


def _assert_map_order_invariance(where: str, map_obj: dict, shuffled: dict) -> None:
    """R2: `entries` 순서 치환은 entry 직렬화·fingerprint 다중집합을 바꾸지 않는다."""
    before = sorted(_ser(entry) for entry in capability_map.entries_of(map_obj))
    after = sorted(_ser(entry) for entry in capability_map.entries_of(shuffled))
    assert before == after, (
        f"{where}: entry 순서 치환이 entry 직렬화 다중집합을 바꿨다 — Property {PROPERTY_ID}"
    )

    before_fp = sorted(
        canonicalizer.capability_fingerprint(entry)
        for entry in capability_map.entries_of(map_obj)
    )
    after_fp = sorted(
        canonicalizer.capability_fingerprint(entry)
        for entry in capability_map.entries_of(shuffled)
    )
    assert before_fp == after_fp, (
        f"{where}: entry 순서 치환이 Capability_Fingerprint 다중집합을 바꿨다 — "
        f"Property {PROPERTY_ID}"
    )


def _assert_collapse_rule(
    where: str,
    map_obj: dict,
    ctx: Mapping[str, Any],
    purposes: Sequence[str],
    report: Mapping[str, Any],
) -> None:
    """R4: duplicate 축약·최신 단일 노출·fingerprint 불일치·시각 tie 규칙(순서 무관)."""
    expected_active, expected_reasons = _expected_exposure(map_obj, ctx, purposes)

    observed_active = {
        _text(entry.get("modelId")): _ser(entry) for entry in report["active"]
    }
    assert observed_active == expected_active, (
        f"{where}: duplicate 축약·tie 규칙 결과가 기대와 다르다 — Property {PROPERTY_ID}\n"
        f"  expected={sorted(expected_active)}\n  observed={sorted(observed_active)}"
    )
    assert len(report["active"]) == len(observed_active), (
        f"{where}: 같은 Exact_Model_ID가 중복 노출됐다 — Property {PROPERTY_ID}: "
        f"{[_text(entry.get('modelId')) for entry in report['active']]!r}"
    )

    observed_reasons: dict[str, set[str]] = {}
    for record in report["excluded"]:
        observed_reasons.setdefault(_text(record.get("modelId")), set()).add(
            _text(record.get("reason"))
        )
    for model_id, codes in expected_reasons.items():
        assert codes <= observed_reasons.get(model_id, set()), (
            f"{where}: 기대 탈락 이유가 보고되지 않았다 — Property {PROPERTY_ID}: "
            f"modelId={model_id!r} expected⊆{sorted(codes)} "
            f"observed={sorted(observed_reasons.get(model_id, set()))}"
        )


def _assert_activation_order_invariance(
    name: str,
    map_obj: dict,
    ctx: Mapping[str, Any],
    purposes: Sequence[str],
    seeds: Sequence[int],
) -> None:
    """R2·R3·R4: entry·object key·집합 원소·ctx catalog 순서 치환의 activation 불변성.

    치환 변형은 두 종류다: (1) map+ctx 동시 치환(seed별), (2) ctx만 치환(catalog model ID
    순서 무관성). 각 변형의 ``activation_report``는 원본과 **바이트 수준으로 동일**해야 하며,
    축약·tie 규칙 oracle 검사는 원본과 첫 변형에서 수행한다(나머지는 보고서 동일성으로 귀결).
    """
    base_report = activation_gate.activation_report(map_obj, ctx, purposes=purposes)
    base_view = _report_view(base_report)
    _assert_collapse_rule(name, map_obj, ctx, purposes, base_report)

    variants: list[tuple[str, dict, Mapping[str, Any], bool]] = []
    for index, order_seed in enumerate(seeds):
        shuffled_map = S.reorder(map_obj, order_seed)
        shuffled_ctx = S.reorder(ctx, order_seed, extra_set_like=("catalogModelIds",))
        _assert_map_order_invariance(name, map_obj, shuffled_map)
        variants.append((f"seed={order_seed}", shuffled_map, shuffled_ctx, index == 0))
        if index == 0:  # ctx만 치환한 변형(catalog model ID 순서·object key 순서)
            variants.append((f"ctx-only:{order_seed}", map_obj, shuffled_ctx, False))

    for label, shuffled_map, shuffled_ctx, check_rule in variants:
        report = activation_gate.activation_report(shuffled_map, shuffled_ctx, purposes=purposes)
        view = _report_view(report)
        assert view == base_view, (
            f"{name}[{label}]: 순서 치환이 activation 결과를 바꿨다 — Property {PROPERTY_ID}\n"
            f"  active(before)={base_view['active']}\n  active(after)={view['active']}\n"
            f"  excluded(before)={base_view['excluded']}\n"
            f"  excluded(after)={view['excluded']}"
        )
        if check_rule:
            _assert_collapse_rule(
                f"{name}[{label}]", shuffled_map, shuffled_ctx, purposes, report
            )


def _assert_excluded_input_invariance(base: dict, spec: Mapping[str, Any]) -> dict:
    """R5: fingerprint 제외 입력만 바꿔도 fingerprint는 불변이다(Requirement 3.13)."""
    perturbed = S.apply_excluded_perturbation(base, dict(spec))
    fingerprint = canonicalizer.capability_fingerprint(base)

    assert canonicalizer.capability_fingerprint(perturbed) == fingerprint, (
        f"fingerprint 제외 입력 변경이 Capability_Fingerprint를 바꿨다 — Property {PROPERTY_ID}\n"
        f"  spec={_brief(spec)}"
    )
    assert _ser(canonicalizer.capability_fingerprint_input(perturbed)) == _ser(
        canonicalizer.capability_fingerprint_input(base)
    ), f"fingerprint 입력 view가 제외 입력 변경으로 달라졌다 — Property {PROPERTY_ID}"
    assert _text(perturbed.get("capabilityFingerprint")) == fingerprint, (
        f"제외 입력 변경 후 저장 fingerprint가 재계산값과 어긋났다 — Property {PROPERTY_ID}"
    )
    assert S.validate_entry_reasons(perturbed) == S.validate_entry_reasons(base), (
        f"제외 입력 변경이 Malformed 판정을 바꿨다 — Property {PROPERTY_ID}: "
        f"{S.validate_entry_reasons(base)} → {S.validate_entry_reasons(perturbed)}"
    )
    return perturbed


def _assert_included_input_sensitivity(base: dict, divergent: dict) -> None:
    """R5(뒤집기): fingerprint **포함** 입력(route 상태·allowlist)이 바뀌면 값이 달라진다(3.12)."""
    assert canonicalizer.capability_fingerprint(divergent) != canonicalizer.capability_fingerprint(
        base
    ), (
        f"route 상태·allowlist 변경이 Capability_Fingerprint에 반영되지 않았다 — "
        f"Property {PROPERTY_ID}"
    )


def _assert_catalog_order_invariance(
    records: Sequence[dict],
    permuted: Sequence[dict],
    seeds: Sequence[int],
) -> None:
    """R6: catalog 입력 순서·object key 순서·수집 메타데이터는 Catalog_Fingerprint를 바꾸지 않는다."""
    baseline = canonicalizer.catalog_fingerprint(list(records))
    assert baseline.startswith(canonicalizer.CATALOG_FINGERPRINT_PREFIX), (
        f"Catalog_Fingerprint 형식 위반 — Property {PROPERTY_ID}: {baseline[:64]}"
    )
    assert canonicalizer.catalog_fingerprint(list(permuted)) == baseline, (
        f"catalog model 나열 순서가 Catalog_Fingerprint를 바꿨다 — Property {PROPERTY_ID}"
    )

    # `{"models": [...]}` 형태 + 수집 메타데이터 변경 → 불변.
    wrapped = {"models": list(records), "collectedAt": "2020-01-01T00:00:00+00:00"}
    wrapped_permuted = {
        "collector": "someone-else",
        "collectedAt": "2035-01-01T00:00:00+00:00",
        "models": list(permuted),
    }
    assert canonicalizer.catalog_fingerprint(wrapped) == baseline, (
        f"`models` wrapper가 Catalog_Fingerprint를 바꿨다 — Property {PROPERTY_ID}"
    )
    assert canonicalizer.catalog_fingerprint(wrapped_permuted) == baseline, (
        f"catalog 순서·수집 메타데이터 변경이 Catalog_Fingerprint를 바꿨다 — "
        f"Property {PROPERTY_ID}"
    )

    for order_seed in seeds:
        shuffled = S.reorder(wrapped, order_seed, extra_set_like=("models",))
        assert canonicalizer.catalog_fingerprint(shuffled) == baseline, (
            f"catalog object key·model 순서 치환이 Catalog_Fingerprint를 바꿨다 — "
            f"Property {PROPERTY_ID}(seed={order_seed})"
        )


def _assert_evidence_selection_order_invariance(
    records: Sequence[dict],
    permuted: Sequence[dict],
    selector: Mapping[str, str],
    seeds: Sequence[int],
) -> None:
    """R7: 활성 evidence 선택이 record 순서와 무관하다(Requirement 3.18, 3.19)."""
    model_id = selector["modelId"]
    fingerprint = selector["capabilityFingerprint"]
    expected = _expected_active_evidence(
        records, model_id=model_id, capability_fingerprint=fingerprint
    )
    chosen = capability_map.select_active_evidence(
        list(records), model_id=model_id, capability_fingerprint=fingerprint
    )
    assert (chosen is None) == (expected is None), (
        f"활성 evidence 존재 판정이 기대와 다르다 — Property {PROPERTY_ID}: "
        f"chosen={_brief(chosen)} expected={_brief(expected)}"
    )
    if expected is not None:
        assert _ser(chosen) == _ser(expected), (
            f"활성 evidence 선택이 규칙(최신 verifiedAt → 동시각은 ID 오름차순)과 다르다 — "
            f"Property {PROPERTY_ID}\n  chosen={_brief(chosen)}\n  expected={_brief(expected)}"
        )

    # 순서 변형: Hypothesis permutation, 역순, 회전, record 내부 object key 치환.
    orders: list[tuple[str, list]] = [
        ("permutation", list(permuted)),
        ("reversed", list(records)[::-1]),
    ]
    for order_seed in seeds:
        offset = order_seed % max(1, len(records))
        rotated = list(records)[offset:] + list(records)[:offset]
        orders.append((f"rotated:{offset}", rotated))
        orders.append(
            (f"keyShuffle:{order_seed}", S.reorder(rotated, order_seed, extra_set_like=()))
        )
    for label, ordered in orders:
        other = capability_map.select_active_evidence(
            ordered, model_id=model_id, capability_fingerprint=fingerprint
        )
        assert (other is None) == (chosen is None), (
            f"[{label}] record 순서 치환이 활성 evidence 존재 판정을 바꿨다 — "
            f"Property {PROPERTY_ID}"
        )
        if chosen is not None:
            assert _ser(other) == _ser(chosen), (
                f"[{label}] record 순서 치환이 활성 evidence 선택을 바꿨다 — "
                f"Property {PROPERTY_ID}\n  before={_brief(chosen)}\n  after={_brief(other)}"
            )

    # 후보가 없는 필터는 순서와 무관하게 항상 미선택이다.
    absent = canonicalizer.CAPABILITY_FINGERPRINT_PREFIX + "0" * 8
    for ordered in (list(records), list(permuted)):
        assert (
            capability_map.select_active_evidence(
                ordered, model_id=model_id, capability_fingerprint=absent
            )
            is None
        ), f"후보 없는 필터가 evidence를 선택했다 — Property {PROPERTY_ID}"


def _assert_ordering_invariance(case: Mapping[str, Any]) -> None:
    """Property 7의 R1~R7 단정.

    단정 순서는 **진단이 좁은 것부터**다: entry 단위 canonical·fingerprint(R1) → map 단위
    다중집합(R2) → activation 결과(R3) → 축약·tie 규칙(R4) → 제외/포함 입력 민감도(R5) →
    catalog(R6) → 활성 evidence 선택(R7).
    """
    purposes = list(case["purposes"])
    ctx = case["ctx"]
    seeds = list(case["reorderSeeds"])

    # ── R1. entry 단위 순서 불변 ─────────────────────────────────
    _assert_entry_order_invariance("baseEntry", case["baseEntry"], seeds)
    for kind, sibling in case["siblings"].items():
        _assert_entry_order_invariance(f"sibling:{kind}", sibling, seeds)

    # ── R2·R3·R4. map 순서 치환 → entry 다중집합·activation 결과·축약 규칙 불변 ──
    for name, map_obj in _scenario_maps(case).items():
        _assert_activation_order_invariance(name, map_obj, ctx, purposes, seeds)

    # ── R5. fingerprint 제외/포함 입력 민감도 ────────────────────
    _assert_excluded_input_invariance(case["baseEntry"], case["perturbation"])
    _assert_included_input_sensitivity(
        case["baseEntry"], case["siblings"]["divergentFingerprint"]
    )

    # ── R6. catalog 입력 순서 불변 ───────────────────────────────
    _assert_catalog_order_invariance(case["catalog"], case["catalogPermutation"], seeds)

    # ── R7. 활성 evidence 선택 순서 무관 ─────────────────────────
    _assert_evidence_selection_order_invariance(
        case["evidenceRecords"],
        case["evidencePermutation"],
        case["evidenceFilter"],
        seeds,
    )


# ─────────────────────────────────────────────────────────────────
# Property 7 — 정확히 하나의 property test
# ─────────────────────────────────────────────────────────────────
@seed(S.AE_PBT_SEED)
@S.PBT_SETTINGS
@given(ordering_cases())
def test_property7_ordering_invariance(case: dict) -> None:
    """Property 7: ordering invariance.

    catalog 입력 순서, entry 순서, object key 순서, 집합 의미 collection의 원소 순서를
    치환해도 Active_Model 집합과 Capability_Fingerprint는 변하지 않는다. fingerprint 제외
    입력만 변경해도 fingerprint는 불변이며, duplicate 축약·tie 규칙(최신 `verifiedAt` 단일
    노출, fingerprint 불일치·시각 tie 미노출, 동시각 tie는 Evidence_Record_ID 오름차순 첫
    record 선택)도 입력 순서와 무관하다.

    **Validates: Requirements 3.12, 3.13, 3.18, 3.19, 6.22, 6.23, 6.24, 6.25, 12.15**
    """
    try:
        _assert_ordering_invariance(case)
    except AssertionError as exc:
        record_counterexample(
            str(exc),
            {
                "purposes": case["purposes"],
                "ctx": case["ctx"],
                "updatedAt": case["updatedAt"],
                "baseEntry": case["baseEntry"],
                "noiseEntries": case["noiseEntries"],
                "siblings": case["siblings"],
                "reorderSeeds": case["reorderSeeds"],
                "perturbation": case["perturbation"],
                "catalog": case["catalog"],
                "catalogPermutation": list(case["catalogPermutation"]),
                "evidenceRecords": case["evidenceRecords"],
                "evidencePermutation": list(case["evidencePermutation"]),
                "evidenceFilter": case["evidenceFilter"],
            },
        )
        raise


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-p", "no:cacheprovider", "-q"]))
