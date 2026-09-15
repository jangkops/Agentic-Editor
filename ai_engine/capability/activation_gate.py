"""Activation_Gate — Active_Model 판정(순수 함수).

이 모듈은 Capability_Map entry가 Active_Model 조건을 충족하는지 판정하고, map 전체에서
노출 가능한 Active_Model 목록을 산출한다. 디스크·네트워크·시각·난수에 접근하지 않으며
같은 입력에는 항상 같은 출력을 만든다(순수 함수만).

**값 추론 금지** — 특정 model ID·provider·route 지원 여부·effort field path·effort
허용값은 이 모듈에 존재하지 않는다. 판정 입력은 오직 entry에 기록된 evidence 결과와
호출자가 준 현재성 컨텍스트(`ctx`)다. Candidate_Label 문자열에서 model identity·provider·
route·effort를 유도하지 않으며, Exact_Model_ID가 없는 Candidate_Label은 어떤 경우에도
모델 항목으로 산출되지 않는다(:data:`EMPTY_MODEL_ID` 탈락, Requirement 6.26).

책임 경계:
  - Malformed 판정·계약 완전성 규칙 → :mod:`.contracts`
  - canonical 직렬화·fingerprint 계산 → :mod:`.canonicalizer`
  - Complete_Record·승격 차단 코드·STALE 전이 → :mod:`.capability_map`
  - `/api/models` 병합·UI payload 구성 → :mod:`.capability_map`(작업 5.3, 이 모듈 아님)

활성 조건(전부 AND — Requirement 6.1~6.13):

1. `verificationStatus == "VERIFIED"` (`UNVERIFIED`·`DISCOVERED`·`REJECTED`·`STALE` 제외)
2. non-empty Exact_Model_ID
3. non-empty Provider_String
4. 저장된 `capabilityFingerprint` == 재계산값
5. Complete_Record
6. Eligible_Contract ≥ 1
7. Current_Evidence 보유(evidence reference·`verifiedAt`·revision·Catalog_Fingerprint 일치)
8. Malformed_Entry 아님, Seed_Entry 아님(`sourceKind != "SEED"`)

`Eligible_Contract` 판정(Requirement 6.6): route `status == SUPPORTED` AND Current_Evidence
reference 보유 AND 요청 목적(`purposes`) 충족 AND `allowlist == ALLOWED`. 계약 완전성도
함께 요구한다(불완전 계약으로는 request를 생성할 수 없다).

노출 규칙(Requirement 6.22~6.25) — :func:`active_models`:
  - canonical serialization이 동일한 duplicate는 1개로 축약한다(:data:`DUPLICATE_COLLAPSED`).
  - 동일 Exact_Model_ID의 유효 entry가 복수면 가장 늦은 `verifiedAt` 단일 entry만 노출한다
    (밀린 entry는 :data:`SUPERSEDED_BY_NEWER`).
  - 최신 entry들의 Capability_Fingerprint가 다르면 해당 Exact_Model_ID를 노출하지 않는다
    (:data:`FINGERPRINT_DIVERGENCE`).
  - 최신 entry들이 같은 `verifiedAt`으로 tie면 해당 Exact_Model_ID를 노출하지 않는다
    (:data:`VERIFIED_AT_TIE`).

`ctx` 키(모두 선택 — 주지 않은 항목은 검사를 건너뛴다. 단 entry의 `revision`·
`catalogFingerprint`가 비어 있으면 미확정으로 보아 탈락시킨다):
  ``revision``          Current_Revision
  ``catalogFingerprint`` 현재 Catalog_Fingerprint
  ``catalogModelIds``   현재 catalog의 Exact_Model_ID 집합
  ``catalogProviders``  `{modelId: Provider_String}`
  ``environment``       Same_Gateway_Environment identity(`record`와 함께 쓸 때만 비교)
  ``purposes``/``purpose`` 요청 목적(Eligible_Contract 판정 입력)
  ``nowUtc``            보고서용 기준 시각(판정에는 사용하지 않는다 — 시각 의존 없음)

참조: .kiro/specs/gateway-models-effort-support/design.md
  - "Components and Interfaces" 4절 (Activation_Gate)
  - Correctness Properties 1(activation subset)·2(malformed 제외)·7(ordering invariance)
Requirements: 6.1, 6.2, 6.3, 6.4, 6.5, 6.6, 6.7, 6.8, 6.9, 6.10, 6.11, 6.12, 6.13,
              6.22, 6.23, 6.24, 6.25, 6.26
"""
from __future__ import annotations

import copy
from datetime import datetime
from typing import Any, Iterable, Mapping

from . import canonicalizer, capability_map, contracts

# ─────────────────────────────────────────────────────────────────
# 탈락 이유 코드 (닫힌 집합)
#
# capability_map의 승격 차단 코드를 그대로 재사용해 보고서 어휘를 하나로 유지한다.
# ─────────────────────────────────────────────────────────────────

#: 탈락 이유가 없음(활성).
REASON_OK = ""

MALFORMED_ENTRY = capability_map.BLOCK_MALFORMED
INCOMPLETE_RECORD = capability_map.BLOCK_INCOMPLETE_RECORD
EMPTY_MODEL_ID = capability_map.BLOCK_EMPTY_MODEL_ID
EMPTY_PROVIDER = capability_map.BLOCK_EMPTY_PROVIDER
NO_SUPPORTED_ROUTE = capability_map.BLOCK_NO_SUPPORTED_ROUTE
REVISION_UNKNOWN = capability_map.BLOCK_REVISION_UNKNOWN
REVISION_MISMATCH = capability_map.BLOCK_REVISION_MISMATCH
CATALOG_FINGERPRINT_UNKNOWN = capability_map.BLOCK_CATALOG_FINGERPRINT_UNKNOWN
CATALOG_FINGERPRINT_MISMATCH = capability_map.BLOCK_CATALOG_FINGERPRINT_MISMATCH
CATALOG_MODEL_ABSENT = capability_map.BLOCK_CATALOG_MODEL_ABSENT
PROVIDER_MISMATCH = capability_map.BLOCK_PROVIDER_MISMATCH
ENVIRONMENT_UNKNOWN = capability_map.BLOCK_ENVIRONMENT_UNKNOWN
ENVIRONMENT_MISMATCH = capability_map.BLOCK_ENVIRONMENT_MISMATCH

#: 저장된 Capability_Fingerprint가 재계산값과 다르다(Requirement 6.4).
FINGERPRINT_MISMATCH = "FINGERPRINT_MISMATCH"

#: Seed_Entry(`sourceKind == "SEED"`)는 활성 후보가 아니다(Requirement 6.13).
SEED_ENTRY = "SEED_ENTRY"

# Verification_Status별 탈락 코드(Requirement 6.9~6.12).
STATUS_UNVERIFIED = "STATUS_UNVERIFIED"
STATUS_DISCOVERED = "STATUS_DISCOVERED"
STATUS_REJECTED = "STATUS_REJECTED"
STATUS_STALE = "STATUS_STALE"
STATUS_INVALID = "STATUS_INVALID"

#: Current_Evidence reference 또는 `verifiedAt`이 없다(Requirement 6.7).
NO_CURRENT_EVIDENCE = "NO_CURRENT_EVIDENCE"

#: Eligible_Contract가 0개다(Requirement 6.6).
NO_ELIGIBLE_CONTRACT = "NO_ELIGIBLE_CONTRACT"

# 노출 단계 탈락 코드(Requirement 6.22~6.25).
DUPLICATE_COLLAPSED = "DUPLICATE_COLLAPSED"
SUPERSEDED_BY_NEWER = "SUPERSEDED_BY_NEWER"
FINGERPRINT_DIVERGENCE = "FINGERPRINT_DIVERGENCE"
VERIFIED_AT_TIE = "VERIFIED_AT_TIE"

#: entry 판정 이유의 결정론적 우선순위(첫 항목이 :func:`is_active`의 대표 이유).
REASON_PRECEDENCE: tuple[str, ...] = (
    MALFORMED_ENTRY,
    FINGERPRINT_MISMATCH,
    SEED_ENTRY,
    STATUS_UNVERIFIED,
    STATUS_DISCOVERED,
    STATUS_REJECTED,
    STATUS_STALE,
    STATUS_INVALID,
    EMPTY_MODEL_ID,
    EMPTY_PROVIDER,
    INCOMPLETE_RECORD,
    NO_CURRENT_EVIDENCE,
    REVISION_UNKNOWN,
    REVISION_MISMATCH,
    CATALOG_FINGERPRINT_UNKNOWN,
    CATALOG_FINGERPRINT_MISMATCH,
    CATALOG_MODEL_ABSENT,
    PROVIDER_MISMATCH,
    ENVIRONMENT_UNKNOWN,
    ENVIRONMENT_MISMATCH,
    NO_SUPPORTED_ROUTE,
    NO_ELIGIBLE_CONTRACT,
)

#: 노출 단계 이유 코드(entry 판정 이후에 적용된다).
EXPOSURE_REASONS: tuple[str, ...] = (
    DUPLICATE_COLLAPSED,
    SUPERSEDED_BY_NEWER,
    FINGERPRINT_DIVERGENCE,
    VERIFIED_AT_TIE,
)

#: 이 모듈이 산출할 수 있는 모든 이유 코드.
DEACTIVATION_REASONS: tuple[str, ...] = REASON_PRECEDENCE + EXPOSURE_REASONS

# ── Eligible_Contract 탈락 코드(route 단위) ───────────────────────
ROUTE_NOT_SUPPORTED = "ROUTE_NOT_SUPPORTED"
ROUTE_CONTRACT_MISSING = "ROUTE_CONTRACT_MISSING"
ROUTE_CONTRACT_INCOMPLETE = "ROUTE_CONTRACT_INCOMPLETE"
ROUTE_CONTRACT_KEY_MISMATCH = "ROUTE_CONTRACT_KEY_MISMATCH"
ROUTE_EVIDENCE_MISSING = "ROUTE_EVIDENCE_MISSING"
ROUTE_PURPOSE_UNMET = "ROUTE_PURPOSE_UNMET"
ROUTE_ALLOWLIST_NOT_ALLOWED = "ROUTE_ALLOWLIST_NOT_ALLOWED"

CONTRACT_REASONS: tuple[str, ...] = (
    ROUTE_NOT_SUPPORTED,
    ROUTE_CONTRACT_MISSING,
    ROUTE_CONTRACT_INCOMPLETE,
    ROUTE_CONTRACT_KEY_MISMATCH,
    ROUTE_EVIDENCE_MISSING,
    ROUTE_PURPOSE_UNMET,
    ROUTE_ALLOWLIST_NOT_ALLOWED,
)

#: Verification_Status → 탈락 코드.
_STATUS_REASON: dict[str, str] = {
    str(contracts.Verification_Status.UNVERIFIED): STATUS_UNVERIFIED,
    str(contracts.Verification_Status.DISCOVERED): STATUS_DISCOVERED,
    str(contracts.Verification_Status.REJECTED): STATUS_REJECTED,
    str(contracts.Verification_Status.STALE): STATUS_STALE,
}

#: 보고서 record에 남기는 entry 식별 필드(비밀정보 없음).
REPORT_ENTRY_FIELDS: tuple[str, ...] = (
    "candidateLabel",
    "modelId",
    "provider",
    "capabilityFingerprint",
    "verifiedAt",
    "revision",
    "sourceKind",
    "verificationStatus",
)

_REASON_ORDER: dict[str, int] = {code: index for index, code in enumerate(DEACTIVATION_REASONS)}


# ─────────────────────────────────────────────────────────────────
# 작은 술어·유틸
# ─────────────────────────────────────────────────────────────────
def _is_dict(value: Any) -> bool:
    return isinstance(value, dict)


def _text(value: Any) -> str:
    """문자열 필드를 안전하게 읽는다(문자열이 아니면 미확정으로 취급)."""
    return value if isinstance(value, str) else contracts.UNDETERMINED


def _sort_bytes(text: str) -> bytes:
    """UTF-8 바이트 순 정렬 키(canonicalizer와 동일 규칙)."""
    return text.encode("utf-8", "surrogatepass")


def _instant(value: Any) -> float | None:
    """UTC ISO 8601 문자열 → epoch 초(형식 위반·미확정은 ``None``)."""
    if not contracts.is_utc_iso8601(value):
        return None
    try:
        return datetime.fromisoformat(value).timestamp()
    except ValueError:  # pragma: no cover - is_utc_iso8601이 선차단한다
        return None


def _recency_rank(verified_at: Any) -> tuple[int, float]:
    """`verifiedAt` 비교 키. 형식 위반·미확정은 항상 확정 시각보다 낮다."""
    moment = _instant(verified_at)
    return (1, moment) if moment is not None else (0, 0.0)


def _order_reasons(reasons: Iterable[str]) -> list[str]:
    """이유 코드를 :data:`REASON_PRECEDENCE` 순서로 정렬한다(미등록 코드는 뒤에 정렬)."""
    unique = {code for code in reasons if code}
    return sorted(unique, key=lambda code: (_REASON_ORDER.get(code, len(_REASON_ORDER)), code))


def _evidence_ids_of(entry: Any) -> list[str]:
    """entry가 보유한 비어 있지 않은 Evidence_Record_ID 목록."""
    if not _is_dict(entry):
        return []
    evidence = entry.get("evidence")
    if not isinstance(evidence, list):
        return []
    return [ref for ref in evidence if isinstance(ref, str) and ref]


def _serialize_or_none(value: Any) -> str | None:
    """canonical serialization(의미 손실이면 ``None``)."""
    try:
        return canonicalizer.serialize(value)
    except canonicalizer.MalformedEntryError:
        return None


# ─────────────────────────────────────────────────────────────────
# 요청 목적 (Eligible_Contract 판정 입력)
# ─────────────────────────────────────────────────────────────────
def requested_purposes(
    ctx: Mapping[str, Any] | None = None,
    purposes: Any = None,
) -> tuple[str, ...]:
    """요청 목적 집합을 정규화한다(정렬·중복 제거).

    우선순위: 명시 인자 ``purposes`` → ``ctx["purposes"]`` → ``ctx["purpose"]``.
    아무 것도 없으면 빈 tuple이며, 이때 목적 제약은 적용되지 않는다(계약이 열거한
    목적이 비어 있지 않으면 목적 조건을 충족한 것으로 본다).
    """
    source = purposes
    if source is None and isinstance(ctx, Mapping):
        source = ctx.get("purposes")
        if source is None:
            source = ctx.get("purpose")
    if source is None:
        return ()
    if isinstance(source, str):
        return (source,) if source else ()
    if isinstance(source, (list, tuple, set, frozenset)):
        return tuple(sorted({item for item in source if isinstance(item, str) and item}))
    return ()


# ─────────────────────────────────────────────────────────────────
# Eligible_Contract (Requirement 6.6)
# ─────────────────────────────────────────────────────────────────
def contract_ineligibility_reasons(
    entry: Any,
    route_key: str,
    *,
    ctx: Mapping[str, Any] | None = None,
    purposes: Any = None,
) -> list[str]:
    """route가 Eligible_Contract가 아닌 이유 목록(빈 목록이면 Eligible_Contract).

    조건: route `status == SUPPORTED` AND 완전한 Route_Contract AND Current_Evidence
    reference(entry의 evidence 목록에 존재) AND 요청 목적 충족 AND `allowlist == ALLOWED`.

    entry 단위 현재성(revision·Catalog_Fingerprint·Capability_Fingerprint)은
    :func:`deactivation_reasons`가 검사한다. 이 함수는 route 단위 조건만 본다.
    """
    if not _is_dict(entry) or not contracts.Known_Route.has(route_key):
        return [ROUTE_NOT_SUPPORTED]

    routes = entry.get("routes")
    route_entry = routes.get(route_key) if _is_dict(routes) else None
    if not _is_dict(route_entry):
        return [ROUTE_NOT_SUPPORTED]

    reasons: list[str] = []
    if route_entry.get("status") != contracts.Route_Support_Status.SUPPORTED:
        reasons.append(ROUTE_NOT_SUPPORTED)

    contract = route_entry.get("contract")
    if contract is None:
        reasons.append(ROUTE_CONTRACT_MISSING)
    elif not _is_dict(contract):
        reasons.append(ROUTE_CONTRACT_INCOMPLETE)
    else:
        if not contracts.route_contract_is_complete(contract):
            reasons.append(ROUTE_CONTRACT_INCOMPLETE)
        if _text(contract.get("routeKey")) != route_key:
            reasons.append(ROUTE_CONTRACT_KEY_MISMATCH)

        wanted = requested_purposes(ctx, purposes)
        available = contract.get("purposes")
        available_set = (
            {item for item in available if isinstance(item, str) and item}
            if isinstance(available, list)
            else set()
        )
        if not available_set or not set(wanted).issubset(available_set):
            reasons.append(ROUTE_PURPOSE_UNMET)

    # Current_Evidence reference — route와 계약 모두 entry evidence를 가리켜야 한다.
    known = set(_evidence_ids_of(entry))
    route_ref = _text(route_entry.get("evidenceRef"))
    if not route_ref or route_ref not in known:
        reasons.append(ROUTE_EVIDENCE_MISSING)
    if _is_dict(contract):
        contract_ref = contract.get("evidenceRef")
        if isinstance(contract_ref, str) and contract_ref and contract_ref not in known:
            reasons.append(ROUTE_EVIDENCE_MISSING)

    if route_entry.get("allowlist") != contracts.Allowlist_Result.ALLOWED:
        reasons.append(ROUTE_ALLOWLIST_NOT_ALLOWED)

    return sorted(set(reasons), key=lambda code: (CONTRACT_REASONS.index(code), code))


def is_eligible_contract(
    entry: Any,
    route_key: str,
    *,
    ctx: Mapping[str, Any] | None = None,
    purposes: Any = None,
) -> bool:
    """route가 Eligible_Contract인지."""
    return not contract_ineligibility_reasons(entry, route_key, ctx=ctx, purposes=purposes)


def eligible_contracts(
    entry: Any,
    *,
    ctx: Mapping[str, Any] | None = None,
    purposes: Any = None,
) -> list[dict]:
    """Eligible_Contract 목록을 Fallback_Order(`fallbackRank` 오름차순)로 반환한다.

    반환 항목: ``{"routeKey", "fallbackRank", "evidenceRef", "contract"}``. 계약은 깊은
    복사본이므로 호출자가 수정해도 entry에 영향을 주지 않는다. 같은 rank는 route key의
    UTF-8 바이트 순으로 정렬해 입력 순서와 무관한 결정론적 순서를 만든다.
    """
    found: list[dict] = []
    for route_key in contracts.KNOWN_ROUTES:
        if not is_eligible_contract(entry, route_key, ctx=ctx, purposes=purposes):
            continue
        route_entry = entry["routes"][route_key]
        contract = route_entry.get("contract") or {}
        rank = contract.get("fallbackRank")
        found.append(
            {
                "routeKey": route_key,
                "fallbackRank": rank if isinstance(rank, int) and not isinstance(rank, bool) else 0,
                "evidenceRef": _text(route_entry.get("evidenceRef")),
                "contract": copy.deepcopy(contract),
            }
        )
    found.sort(key=lambda item: (item["fallbackRank"], _sort_bytes(item["routeKey"])))
    return found


def eligible_route_keys(
    entry: Any,
    *,
    ctx: Mapping[str, Any] | None = None,
    purposes: Any = None,
) -> list[str]:
    """Eligible_Contract를 가진 route key 목록(Fallback_Order 순)."""
    return [item["routeKey"] for item in eligible_contracts(entry, ctx=ctx, purposes=purposes)]


# ─────────────────────────────────────────────────────────────────
# entry 단위 활성 판정 (Requirement 6.1~6.13)
# ─────────────────────────────────────────────────────────────────
def deactivation_reasons(
    entry: Any,
    ctx: Mapping[str, Any] | None = None,
    *,
    purposes: Any = None,
    mode_hints: Mapping[str, str] | None = None,
    known_evidence_ids: Iterable[str] | None = None,
) -> list[str]:
    """entry가 Active_Model이 아닌 이유 코드 목록(빈 목록이면 활성).

    이유는 :data:`REASON_PRECEDENCE` 순서로 정렬되므로 첫 항목이 대표 이유다.
    """
    if not _is_dict(entry):
        return [MALFORMED_ENTRY]

    reasons: list[str] = []

    # (8) Malformed_Entry 아님 — fingerprint 재계산 불일치는 별도 코드로 구분한다(6.4).
    malformed = capability_map.entry_malformed_reasons(entry, known_evidence_ids=known_evidence_ids)
    if malformed:
        if any(reason.startswith(contracts.FINGERPRINT_MISMATCH) for reason in malformed):
            reasons.append(FINGERPRINT_MISMATCH)
        if any(not reason.startswith(contracts.FINGERPRINT_MISMATCH) for reason in malformed):
            reasons.append(MALFORMED_ENTRY)

    # (8) Seed_Entry 아님
    if _text(entry.get("sourceKind")) == str(contracts.Source_Kind.SEED):
        reasons.append(SEED_ENTRY)

    # (1) VERIFIED만 후보
    status = _text(entry.get("verificationStatus"))
    if status != str(contracts.Verification_Status.VERIFIED):
        reasons.append(_STATUS_REASON.get(status, STATUS_INVALID))

    # (7) Current_Evidence — evidence reference와 검증 시각이 모두 있어야 한다.
    if not _evidence_ids_of(entry) or not contracts.is_utc_iso8601(entry.get("verifiedAt")):
        reasons.append(NO_CURRENT_EVIDENCE)

    # (2)(3)(5)(7) identity·Complete_Record·현재성 — capability_map 승격 차단 코드 재사용
    reasons.extend(
        capability_map.promotion_blockers(
            entry,
            ctx=ctx,
            known_evidence_ids=known_evidence_ids,
            mode_hints=mode_hints,
        )
    )

    # (6) Eligible_Contract ≥ 1
    if not eligible_contracts(entry, ctx=ctx, purposes=purposes):
        reasons.append(NO_ELIGIBLE_CONTRACT)

    return _order_reasons(reasons)


def is_active(
    entry: Any,
    ctx: Mapping[str, Any] | None = None,
    *,
    purposes: Any = None,
    mode_hints: Mapping[str, str] | None = None,
    known_evidence_ids: Iterable[str] | None = None,
) -> tuple[bool, str]:
    """entry가 Active_Model 후보인지 판정한다.

    Args:
        entry: Capability_Map entry.
        ctx: 현재성 컨텍스트(모듈 docstring의 `ctx` 키 참조).
        purposes: 요청 목적(문자열 또는 문자열 collection). 없으면 ctx에서 읽는다.
        mode_hints: `{routeKey: Execution_Mode}` — 계약에 mode가 없을 때만 사용.
        known_evidence_ids: 저장된 Verification_Record ID 집합(참조 무결성 검사용).

    Returns:
        ``(활성 여부, 탈락 이유 코드)``. 활성이면 이유는 :data:`REASON_OK`(빈 문자열)다.
    """
    reasons = deactivation_reasons(
        entry,
        ctx,
        purposes=purposes,
        mode_hints=mode_hints,
        known_evidence_ids=known_evidence_ids,
    )
    return (not reasons, reasons[0] if reasons else REASON_OK)


# ─────────────────────────────────────────────────────────────────
# map 단위 노출 (Requirement 6.22~6.26)
# ─────────────────────────────────────────────────────────────────
def _report_record(entry: Any, reason: str) -> dict:
    """탈락 record(비밀정보 없음 — 식별 필드와 이유 코드만)."""
    source = entry if _is_dict(entry) else {}
    record = {field: source.get(field) for field in REPORT_ENTRY_FIELDS}
    record["reason"] = reason
    return record


def _excluded_sort_key(record: Mapping[str, Any]) -> tuple:
    """탈락 record의 결정론적 정렬 키(입력 순서에 의존하지 않는다)."""
    serialized = _serialize_or_none(record) or ""
    return (
        _sort_bytes(_text(record.get("modelId"))),
        _sort_bytes(_text(record.get("reason"))),
        _sort_bytes(serialized),
    )


def activation_report(
    map_obj: Any,
    ctx: Mapping[str, Any] | None = None,
    *,
    purposes: Any = None,
    mode_hints: Mapping[str, str] | None = None,
    known_evidence_ids: Iterable[str] | None = None,
) -> dict:
    """Active_Model 목록과 탈락 이유 코드를 함께 산출한다(Validation_Runner 보고서용).

    Returns:
        ``{"active": [entry, ...], "excluded": [record, ...], "counts": {...}}``.
        ``active``는 Exact_Model_ID의 UTF-8 바이트 순으로 정렬된 깊은 복사본이며 각
        Exact_Model_ID당 최대 1개다. ``excluded``는 결정론적으로 정렬된 탈락 record
        목록(식별 필드 + 이유 코드)이다. 입력 entry 순서를 바꿔도 두 결과는 같다.
    """
    valid, malformed_pairs = capability_map.classify_entries(
        map_obj, known_evidence_ids=known_evidence_ids
    )

    excluded: list[dict] = []
    for entry, reasons in malformed_pairs:
        code = (
            FINGERPRINT_MISMATCH
            if all(reason.startswith(contracts.FINGERPRINT_MISMATCH) for reason in reasons)
            else MALFORMED_ENTRY
        )
        excluded.append(_report_record(entry, code))

    # 1) entry 단위 활성 판정
    candidates: list[dict] = []
    for entry in valid:
        active, reason = is_active(
            entry,
            ctx,
            purposes=purposes,
            mode_hints=mode_hints,
            known_evidence_ids=known_evidence_ids,
        )
        if active:
            candidates.append(entry)
        else:
            excluded.append(_report_record(entry, reason))

    # 2) canonical serialization 동일 duplicate 축약 (6.22)
    unique: dict[str, dict] = {}
    for entry in candidates:
        serialized = _serialize_or_none(entry)
        if serialized is None:  # 직렬화 의미 손실 → Malformed로 취급해 제외
            excluded.append(_report_record(entry, MALFORMED_ENTRY))
            continue
        if serialized in unique:
            excluded.append(_report_record(entry, DUPLICATE_COLLAPSED))
            continue
        unique[serialized] = entry

    # 3) 동일 Exact_Model_ID 축약 (6.23~6.25)
    groups: dict[str, list[dict]] = {}
    for entry in unique.values():
        groups.setdefault(_text(entry.get("modelId")), []).append(entry)

    active_entries: list[dict] = []
    for model_id in sorted(groups, key=_sort_bytes):
        group = groups[model_id]
        if len(group) == 1:
            active_entries.append(group[0])
            continue

        ranks = [_recency_rank(item.get("verifiedAt")) for item in group]
        top = max(ranks)
        latest_positions = [index for index, rank in enumerate(ranks) if rank == top]
        for index, item in enumerate(group):
            if index not in latest_positions:
                excluded.append(_report_record(item, SUPERSEDED_BY_NEWER))

        if len(latest_positions) == 1:
            active_entries.append(group[latest_positions[0]])
            continue

        latest = [group[index] for index in latest_positions]
        fingerprints = {_text(item.get("capabilityFingerprint")) for item in latest}
        code = FINGERPRINT_DIVERGENCE if len(fingerprints) > 1 else VERIFIED_AT_TIE
        for item in latest:
            excluded.append(_report_record(item, code))

    excluded.sort(key=_excluded_sort_key)
    return {
        "active": [copy.deepcopy(entry) for entry in active_entries],
        "excluded": excluded,
        "counts": {
            "entries": len(capability_map.entries_of(map_obj)),
            "malformed": len(malformed_pairs),
            "active": len(active_entries),
            "excluded": len(excluded),
        },
    }


def active_models(
    map_obj: Any,
    ctx: Mapping[str, Any] | None = None,
    *,
    purposes: Any = None,
    mode_hints: Mapping[str, str] | None = None,
    known_evidence_ids: Iterable[str] | None = None,
) -> list[dict]:
    """Active_Model entry 목록을 반환한다(Managed_Segment 노출 집합).

    유효 entry만 평가하고, canonical serialization이 같은 duplicate를 1개로 축약한 뒤,
    동일 Exact_Model_ID는 가장 늦은 `verifiedAt` 단일 entry만 남긴다. 최신 entry들의
    Capability_Fingerprint 불일치 또는 `verifiedAt` tie면 그 Exact_Model_ID는 노출하지
    않는다. Exact_Model_ID가 없는 Candidate_Label은 어떤 경우에도 산출되지 않는다.
    """
    return activation_report(
        map_obj,
        ctx,
        purposes=purposes,
        mode_hints=mode_hints,
        known_evidence_ids=known_evidence_ids,
    )["active"]


def active_model_ids(
    map_obj: Any,
    ctx: Mapping[str, Any] | None = None,
    *,
    purposes: Any = None,
    mode_hints: Mapping[str, str] | None = None,
    known_evidence_ids: Iterable[str] | None = None,
) -> list[str]:
    """Active_Model의 Exact_Model_ID 목록(UTF-8 바이트 순, 중복 없음)."""
    return [
        _text(entry.get("modelId"))
        for entry in active_models(
            map_obj,
            ctx,
            purposes=purposes,
            mode_hints=mode_hints,
            known_evidence_ids=known_evidence_ids,
        )
    ]


def exclusion_reasons(
    map_obj: Any,
    ctx: Mapping[str, Any] | None = None,
    *,
    purposes: Any = None,
    mode_hints: Mapping[str, str] | None = None,
    known_evidence_ids: Iterable[str] | None = None,
) -> list[dict]:
    """탈락 record 목록(식별 필드 + 이유 코드) — 보고서 기록용."""
    return activation_report(
        map_obj,
        ctx,
        purposes=purposes,
        mode_hints=mode_hints,
        known_evidence_ids=known_evidence_ids,
    )["excluded"]


__all__ = [
    "CATALOG_FINGERPRINT_MISMATCH",
    "CATALOG_FINGERPRINT_UNKNOWN",
    "CATALOG_MODEL_ABSENT",
    "CONTRACT_REASONS",
    "DEACTIVATION_REASONS",
    "DUPLICATE_COLLAPSED",
    "EMPTY_MODEL_ID",
    "EMPTY_PROVIDER",
    "ENVIRONMENT_MISMATCH",
    "ENVIRONMENT_UNKNOWN",
    "EXPOSURE_REASONS",
    "FINGERPRINT_DIVERGENCE",
    "FINGERPRINT_MISMATCH",
    "INCOMPLETE_RECORD",
    "MALFORMED_ENTRY",
    "NO_CURRENT_EVIDENCE",
    "NO_ELIGIBLE_CONTRACT",
    "NO_SUPPORTED_ROUTE",
    "PROVIDER_MISMATCH",
    "REASON_OK",
    "REASON_PRECEDENCE",
    "REPORT_ENTRY_FIELDS",
    "REVISION_MISMATCH",
    "REVISION_UNKNOWN",
    "ROUTE_ALLOWLIST_NOT_ALLOWED",
    "ROUTE_CONTRACT_INCOMPLETE",
    "ROUTE_CONTRACT_KEY_MISMATCH",
    "ROUTE_CONTRACT_MISSING",
    "ROUTE_EVIDENCE_MISSING",
    "ROUTE_NOT_SUPPORTED",
    "ROUTE_PURPOSE_UNMET",
    "SEED_ENTRY",
    "STATUS_DISCOVERED",
    "STATUS_INVALID",
    "STATUS_REJECTED",
    "STATUS_STALE",
    "STATUS_UNVERIFIED",
    "SUPERSEDED_BY_NEWER",
    "VERIFIED_AT_TIE",
    "activation_report",
    "active_model_ids",
    "active_models",
    "contract_ineligibility_reasons",
    "deactivation_reasons",
    "eligible_contracts",
    "eligible_route_keys",
    "exclusion_reasons",
    "is_active",
    "is_eligible_contract",
    "requested_purposes",
]
