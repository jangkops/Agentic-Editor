"""Effort_Settings — 저장된 effort 선택의 정규화·무효화·복원(순수 로직).

이 모듈은 사용자가 고른 effort value를 `(Exact_Model_ID, Known_Route,
Capability_Fingerprint)` tuple에 결속해 보관하고, tuple이나 capability 상태가
어긋난 값이 **request 생성 전에** 제거되도록 만든다.

**순수 로직** — DOM·디스크·네트워크에 접근하지 않는다. 영속화는
`ai_engine/capability/store.py`(백엔드)와 `electron/src/ipc-capability-handlers.js`
(프론트)가 담당하고, 이 모듈은 값만 다룬다. 시각은 `now` 인자로 주입할 수 있으며,
주지 않으면 `contracts.utc_now_iso()`를 쓴다.

**값 추론 금지** — 특정 model ID·route 지원 여부·effort field path·effort 허용값은
이 모듈에 존재하지 않는다. 허용값 판정은 항상 인자로 받은 Effort_Contract의
`valueType`·`domainKind`·`enumValues`·`rangeLowerInclusive`·`rangeUpperInclusive`만
읽는다(:func:`value_in_domain`).

스키마(design.md "Data Models" → Effort_Settings)::

    {"schemaVersion": 1,
     "entries": [{"modelId", "route", "capabilityFingerprint",
                  "value", "valueType", "updatedAt"}]}

tuple 키는 `(modelId, route, capabilityFingerprint)`이며 **세 값이 모두 일치할
때만** 복원한다(:func:`restore`). 하나라도 다르면 값을 쓰지 않는다.

제거 규칙(:func:`prune`) — 각 항목은 requirements.md의 granular criteria와 1:1이다:

===============================  =========================================  ======
조건                             제거 이유 코드                             근거
===============================  =========================================  ======
스키마 위반                      :data:`DROP_MALFORMED`                     7.4~7.6
현재 선택 modelId 불일치         :data:`DROP_MODEL_ID_MISMATCH`             7.7
현재 선택 route 불일치           :data:`DROP_ROUTE_MISMATCH`                7.8
Capability_Fingerprint 불일치    :data:`DROP_FINGERPRINT_MISMATCH`          7.9
verified domain 이탈             :data:`DROP_DOMAIN_VIOLATION`              7.10
value type 불일치                :data:`DROP_VALUE_TYPE_MISMATCH`           7.10
entry 삭제                       :data:`DROP_ENTRY_ABSENT`                  7.11
entry `STALE`                    :data:`DROP_ENTRY_STALE`                   7.12
entry가 `VERIFIED`가 아님        :data:`DROP_ENTRY_NOT_VERIFIED`            7.12
route의 `SUPPORTED` 상실         :data:`DROP_ROUTE_NOT_SUPPORTED`           7.13
effort가 `SUPPORTED`가 아님      :data:`DROP_EFFORT_NOT_SUPPORTED`          7.10
effort 계약 불완전·결속 위반     :data:`DROP_CONTRACT_INCOMPLETE`           7.10
                                 :data:`DROP_CONTRACT_MISBOUND`
같은 tuple 중복 저장             :data:`DROP_DUPLICATE_TUPLE`               7.14
===============================  =========================================  ======

미제공(unknown)과 불일치(mismatch)를 구분한다. ctx가 capability 정보를 주지 않으면
상태 기반 검사(7.11~7.13)를 **생략**하고, `selection`을 주지 않으면 선택 기반
검사(7.7~7.9)를 생략한다. "모른다"를 이유로 값을 지우지도, 남기지도 않는다.

출력은 결정론적이다. 살아남은 항목은 tuple 키의 UTF-8 바이트 순으로 정렬하므로
입력 순서가 저장 바이트열을 흔들지 않는다(Property 7·8과 같은 불변식).

참조: .kiro/specs/gateway-models-effort-support/design.md
  - "Components and Interfaces" 6절 (Effort_Control / Effort_Settings_Manager)
  - "Data Models" → Effort_Settings
  - Correctness Property 8 (selection validity)
Requirements: 7.4, 7.5, 7.6, 7.7, 7.8, 7.9, 7.10, 7.11, 7.12, 7.13, 7.14
"""
from __future__ import annotations

import copy
from datetime import datetime
from typing import Any, Iterable, Mapping

from . import canonicalizer, contracts

#: Effort_Settings 파일 스키마 버전(entry 스키마와 동일 버전을 사용한다).
SCHEMA_VERSION = contracts.SCHEMA_VERSION

#: Effort_Settings 최상위 키(design.md Data Models).
SETTINGS_KEYS: tuple[str, ...] = ("schemaVersion", "entries")

#: 저장 항목의 필수 필드.
REQUIRED_SETTINGS_ENTRY_FIELDS: tuple[str, ...] = (
    "modelId",
    "route",
    "capabilityFingerprint",
    "value",
    "valueType",
    "updatedAt",
)

#: tuple 키를 구성하는 필드(세 값 모두 일치할 때만 복원한다).
TUPLE_KEY_FIELDS: tuple[str, ...] = ("modelId", "route", "capabilityFingerprint")

#: ctx에서 capability entry 색인을 만들 때 보는 키(앞선 키가 우선).
CAPABILITY_CTX_KEYS: tuple[str, ...] = ("entriesByModelId", "capabilityEntries", "capabilityMap")

#: ctx에서 현재 Capability_Fingerprint 매핑(`{modelId: fingerprint}`)을 읽는 키.
FINGERPRINT_CTX_KEY = "currentFingerprints"

#: ctx에서 Effort_Contract 매핑(`{modelId: {routeKey: Effort_Contract}}`)을 읽는 키.
EFFORT_CONTRACT_CTX_KEY = "effortContracts"

#: ctx에서 현재 선택 tuple을 읽는 키.
SELECTION_CTX_KEY = "selection"


# ─────────────────────────────────────────────────────────────────
# 제거 이유 코드 (닫힌 집합)
# ─────────────────────────────────────────────────────────────────
DROP_MALFORMED = "MALFORMED_SETTINGS_ENTRY"
DROP_MODEL_ID_MISMATCH = "MODEL_ID_MISMATCH"
DROP_ROUTE_MISMATCH = "ROUTE_MISMATCH"
DROP_FINGERPRINT_MISMATCH = "FINGERPRINT_MISMATCH"
DROP_DOMAIN_VIOLATION = "DOMAIN_VIOLATION"
DROP_VALUE_TYPE_MISMATCH = "VALUE_TYPE_MISMATCH"
DROP_ENTRY_ABSENT = "ENTRY_ABSENT"
DROP_ENTRY_STALE = "ENTRY_STALE"
DROP_ENTRY_NOT_VERIFIED = "ENTRY_NOT_VERIFIED"
DROP_ROUTE_NOT_SUPPORTED = "ROUTE_NOT_SUPPORTED"
DROP_EFFORT_NOT_SUPPORTED = "EFFORT_NOT_SUPPORTED"
DROP_CONTRACT_INCOMPLETE = "EFFORT_CONTRACT_INCOMPLETE"
DROP_CONTRACT_MISBOUND = "EFFORT_CONTRACT_MISBOUND"
DROP_DUPLICATE_TUPLE = "DUPLICATE_TUPLE"

DROP_REASONS: tuple[str, ...] = (
    DROP_MALFORMED,
    DROP_MODEL_ID_MISMATCH,
    DROP_ROUTE_MISMATCH,
    DROP_FINGERPRINT_MISMATCH,
    DROP_DOMAIN_VIOLATION,
    DROP_VALUE_TYPE_MISMATCH,
    DROP_ENTRY_ABSENT,
    DROP_ENTRY_STALE,
    DROP_ENTRY_NOT_VERIFIED,
    DROP_ROUTE_NOT_SUPPORTED,
    DROP_EFFORT_NOT_SUPPORTED,
    DROP_CONTRACT_INCOMPLETE,
    DROP_CONTRACT_MISBOUND,
    DROP_DUPLICATE_TUPLE,
)


class EffortSettingsError(ValueError):
    """Effort_Settings 저장 계약 위반(허용되지 않은 tuple·value를 저장하려 했다)."""


# ─────────────────────────────────────────────────────────────────
# 작은 술어·유틸
# ─────────────────────────────────────────────────────────────────
def _is_dict(value: Any) -> bool:
    return isinstance(value, dict)


def _text(value: Any) -> str:
    """문자열 필드를 안전하게 읽는다(문자열이 아니면 미확정으로 취급)."""
    return value if isinstance(value, str) else contracts.UNDETERMINED


def _is_number(value: Any) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool)


def _sort_bytes(text: str) -> bytes:
    return text.encode("utf-8", "surrogatepass")


def _instant(value: Any) -> float | None:
    """UTC ISO 8601 문자열을 epoch 초로 바꾼다(형식 위반·빈 문자열은 ``None``)."""
    if not contracts.is_utc_iso8601(value):
        return None
    try:
        return datetime.fromisoformat(value).timestamp()
    except ValueError:  # pragma: no cover - is_utc_iso8601이 선차단한다
        return None


def _recency_rank(updated_at: Any) -> tuple[int, float]:
    """`updatedAt` 비교 키. 형식 위반·미확정은 항상 확정 시각보다 낮다."""
    moment = _instant(updated_at)
    return (1, moment) if moment is not None else (0, 0.0)


def _canonical_text(value: Any) -> str:
    """canonical 직렬화 문자열(정규화 불가 값은 결정론적 대체 문자열)."""
    try:
        return canonicalizer.serialize(value)
    except canonicalizer.MalformedEntryError:
        return f"<unserializable:{type(value).__name__}>"


# ─────────────────────────────────────────────────────────────────
# tuple 키
# ─────────────────────────────────────────────────────────────────
def tuple_key(model_id: str, route: str, capability_fingerprint: str) -> dict:
    """`(modelId, route, capabilityFingerprint)` tuple 키 dict를 만든다."""
    return {
        "modelId": _text(model_id),
        "route": _text(route),
        "capabilityFingerprint": _text(capability_fingerprint),
    }


def key_of(setting: Any) -> tuple[str, str, str]:
    """저장 항목의 tuple 키를 반환한다(문자열 3-tuple)."""
    source = setting if _is_dict(setting) else {}
    return tuple(_text(source.get(field)) for field in TUPLE_KEY_FIELDS)  # type: ignore[return-value]


def _complete_key(raw: Any) -> tuple[str, str, str] | None:
    """세 값이 모두 채워진 tuple 키만 반환한다(하나라도 비면 ``None``).

    `route`는 Known_Route 닫힌 집합이어야 한다(임의 문자열 키를 만들지 않는다).
    """
    if not _is_dict(raw):
        return None
    key = key_of(raw)
    if not all(key):
        return None
    if not contracts.Known_Route.has(key[1]):
        return None
    return key


# ─────────────────────────────────────────────────────────────────
# 스키마 정규화·검증
# ─────────────────────────────────────────────────────────────────
def new_settings(*, entries: Iterable[Mapping[str, Any]] | None = None) -> dict:
    """빈 Effort_Settings(`{schemaVersion, entries}`)를 만든다."""
    return {
        "schemaVersion": SCHEMA_VERSION,
        "entries": [copy.deepcopy(dict(entry)) for entry in (entries or [])],
    }


def normalize(raw: Any) -> dict:
    """임의 입력을 Effort_Settings 형식으로 정규화한다(항목 내용은 보존).

    - 최상위가 dict가 아니거나 `schemaVersion`이 미지원이면 **빈 settings**를 반환한다
      (알 수 없는 형식은 effort 미선택과 같게 취급 — 값이 새어나가지 않는다).
    - `entries`가 목록이 아니면 빈 목록으로 둔다.
    - 항목은 검증하거나 수정하지 않는다. 스키마 위반 판정은 :func:`entry_reasons`,
      제거는 :func:`prune`이 담당한다.
    """
    if not _is_dict(raw):
        return new_settings()

    version = raw.get("schemaVersion")
    if isinstance(version, bool) or not isinstance(version, int):
        version = SCHEMA_VERSION
    elif version not in contracts.SUPPORTED_SCHEMA_VERSIONS:
        return new_settings()

    entries = raw.get("entries")
    return {
        "schemaVersion": version,
        "entries": copy.deepcopy(entries) if isinstance(entries, list) else [],
    }


def entry_reasons(setting: Any) -> list[str]:
    """저장 항목의 스키마 위반 이유 목록(빈 목록이면 스키마를 만족한다).

    이유 문자열은 `contracts`와 같은 ``CODE:path(detail)`` 형식이다.
    """
    if not _is_dict(setting):
        return [f"{contracts.TYPE_MISMATCH}:entry(not-object)"]

    reasons: list[str] = []
    for field in REQUIRED_SETTINGS_ENTRY_FIELDS:
        if field not in setting:
            reasons.append(f"{contracts.MISSING_FIELD}:{field}")

    for field in ("modelId", "capabilityFingerprint"):
        if field in setting:
            if not isinstance(setting[field], str):
                reasons.append(f"{contracts.TYPE_MISMATCH}:{field}")
            elif not setting[field]:
                reasons.append(f"{contracts.MISSING_FIELD}:{field}(empty)")

    if "route" in setting and not contracts.Known_Route.has(setting["route"]):
        reasons.append(f"{contracts.ENUM_VIOLATION}:route")

    value_type = setting.get("valueType")
    if "valueType" in setting and not contracts.Value_Type.has(value_type):
        reasons.append(f"{contracts.ENUM_VIOLATION}:valueType")
    elif "value" in setting and not contracts.value_matches_type(setting["value"], value_type):
        # 저장된 value는 자기 valueType과 일치해야 한다(타입이 어긋난 값은 복원하지 않는다).
        reasons.append(f"{contracts.TYPE_MISMATCH}:value(value-type)")

    if "updatedAt" in setting and not contracts.is_utc_iso8601(setting.get("updatedAt")):
        reasons.append(f"{contracts.TYPE_MISMATCH}:updatedAt(not-utc-iso8601)")

    return sorted(set(reasons))


def is_malformed_entry(setting: Any) -> bool:
    """저장 항목이 스키마를 위반했는지(위반이면 복원·전송 대상에서 제외한다)."""
    return bool(entry_reasons(setting))


# ─────────────────────────────────────────────────────────────────
# verified domain 판정 (Requirement 7.10)
# ─────────────────────────────────────────────────────────────────
def value_in_domain(contract: Any, value: Any) -> bool:
    """value가 Effort_Contract의 verified domain에 속하는지 판정한다.

    판정 입력은 계약이 evidence로 채운 `valueType`·`domainKind`와 domain 경계뿐이다.
    `ENUM`은 canonical 표현 기준 멤버십, `RANGE`는 inclusive 경계 비교를 쓴다.
    계약이 불완전하면(값이 미확정) 항상 ``False``다 — 모르는 값은 허용하지 않는다.
    """
    if not _is_dict(contract):
        return False
    value_type = contract.get("valueType")
    if not contracts.Value_Type.has(value_type):
        return False
    if not contracts.value_matches_type(value, value_type):
        return False

    domain_kind = contract.get("domainKind")
    if domain_kind == contracts.Domain_Kind.ENUM:
        allowed = contract.get("enumValues")
        if not isinstance(allowed, list) or not allowed:
            return False
        return any(canonicalizer.canonical_equal(value, item) for item in allowed)

    if domain_kind == contracts.Domain_Kind.RANGE:
        lower = contract.get("rangeLowerInclusive")
        upper = contract.get("rangeUpperInclusive")
        if not (_is_number(value) and _is_number(lower) and _is_number(upper)):
            return False
        return lower <= value <= upper

    return False


# ─────────────────────────────────────────────────────────────────
# ctx 해석 — 미제공(unknown)과 불일치(mismatch)를 구분한다
# ─────────────────────────────────────────────────────────────────
def _pick_capability_entry(current: Any, candidate: Any) -> Any:
    """같은 modelId의 capability entry가 둘이면 최신 `verifiedAt`을 고른다.

    동시각이면 canonical 직렬화 오름차순 첫 entry를 고른다(입력 순서 무관).
    """
    if current is None:
        return candidate
    current_rank = _recency_rank(current.get("verifiedAt") if _is_dict(current) else None)
    candidate_rank = _recency_rank(candidate.get("verifiedAt") if _is_dict(candidate) else None)
    if candidate_rank != current_rank:
        return candidate if candidate_rank > current_rank else current
    return candidate if _canonical_text(candidate) < _canonical_text(current) else current


def capability_index(ctx: Mapping[str, Any] | None) -> dict | None:
    """ctx에서 `{modelId: capability entry}` 색인을 만든다(미제공이면 ``None``).

    지원 형태(앞선 키가 우선):
      ``entriesByModelId``  `{modelId: entry}` 매핑
      ``capabilityEntries`` entry 목록
      ``capabilityMap``     Capability_Map(`{schemaVersion, updatedAt, entries}`)

    키가 하나도 없으면 ``None``(정보 미제공 → 상태 기반 검사 생략)을 반환하고,
    키가 있으면 빈 색인이라도 dict를 반환한다(모든 entry 삭제 → 7.11 적용).
    """
    if not isinstance(ctx, Mapping):
        return None

    raw: Any = None
    for key in CAPABILITY_CTX_KEYS:
        if key in ctx:
            raw = ctx[key]
            break
    else:
        return None

    index: dict[str, Any] = {}
    records: list[Any] = []
    if _is_dict(raw):
        entries = raw.get("entries")
        if isinstance(entries, list):  # Capability_Map 형태
            records = list(entries)
        else:  # {modelId: entry} 매핑 — 키는 참고만 하고 entry의 modelId를 신뢰한다
            for model_id, entry in raw.items():
                if _is_dict(entry):
                    records.append(entry if _text(entry.get("modelId")) else {**entry, "modelId": _text(model_id)})
    elif isinstance(raw, (list, tuple)):
        records = list(raw)

    for entry in records:
        if not _is_dict(entry):
            continue
        model_id = _text(entry.get("modelId"))
        if not model_id:
            continue  # modelId가 없는 entry는 어떤 tuple도 만족시키지 못한다
        index[model_id] = _pick_capability_entry(index.get(model_id), entry)
    return index


def _mapping_of(ctx: Mapping[str, Any] | None, key: str) -> dict | None:
    """ctx의 중첩 매핑을 읽는다(미제공·형식 위반은 ``None``)."""
    if not isinstance(ctx, Mapping) or key not in ctx:
        return None
    value = ctx[key]
    return value if _is_dict(value) else None


def _selection_of(ctx: Mapping[str, Any] | None) -> dict | None:
    """ctx의 현재 선택 tuple을 읽는다(미제공·`None`이면 선택 기반 검사 생략)."""
    if not isinstance(ctx, Mapping):
        return None
    selection = ctx.get(SELECTION_CTX_KEY)
    if not _is_dict(selection):
        return None
    view = {field: _text(selection.get(field)) for field in TUPLE_KEY_FIELDS}
    return view if any(view.values()) else None


def _contract_for(
    setting: Mapping[str, Any],
    cap_entry: Any,
    ctx_contracts: Mapping[str, Any] | None,
) -> tuple[Any, bool]:
    """(Effort_Contract, 계약 정보 제공 여부)를 반환한다.

    capability entry의 `effort[route].contract`를 우선 보고, 없으면 ctx의
    `effortContracts[modelId][routeKey]`를 본다. 둘 다 없으면 (None, False)로
    "모른다"를 표현한다.
    """
    route = _text(setting.get("route"))
    if _is_dict(cap_entry):
        effort = cap_entry.get("effort")
        effort_entry = effort.get(route) if _is_dict(effort) else None
        if _is_dict(effort_entry) and "contract" in effort_entry:
            return effort_entry.get("contract"), True

    if _is_dict(ctx_contracts):
        by_route = ctx_contracts.get(_text(setting.get("modelId")))
        if _is_dict(by_route) and route in by_route:
            return by_route.get(route), True
    return None, False


# ─────────────────────────────────────────────────────────────────
# 제거 판정
# ─────────────────────────────────────────────────────────────────
def _selection_reasons(setting: Mapping[str, Any], selection: Mapping[str, Any] | None) -> list[str]:
    """현재 선택 tuple과 어긋난 항목의 제거 이유(Requirement 7.7~7.9).

    선택에서 비어 있는 성분은 비교하지 않는다(미확정을 불일치로 취급하지 않는다).
    """
    if not selection:
        return []
    reasons: list[str] = []
    for field, code in (
        ("modelId", DROP_MODEL_ID_MISMATCH),
        ("route", DROP_ROUTE_MISMATCH),
        ("capabilityFingerprint", DROP_FINGERPRINT_MISMATCH),
    ):
        wanted = _text(selection.get(field))
        if wanted and _text(setting.get(field)) != wanted:
            reasons.append(code)
    return reasons


def _state_reasons(
    setting: Mapping[str, Any],
    *,
    index: Mapping[str, Any] | None,
    fingerprints: Mapping[str, Any] | None,
    ctx_contracts: Mapping[str, Any] | None,
) -> list[str]:
    """capability 상태와 어긋난 항목의 제거 이유(Requirement 7.9~7.13)."""
    model_id = _text(setting.get("modelId"))
    route = _text(setting.get("route"))
    reasons: list[str] = []
    cap_entry: Any = None

    if index is not None:
        cap_entry = index.get(model_id)
        if cap_entry is None:
            return [DROP_ENTRY_ABSENT]  # 7.11 — 선택 model entry 삭제

        status = _text(cap_entry.get("verificationStatus"))
        if status == contracts.Verification_Status.STALE:
            reasons.append(DROP_ENTRY_STALE)  # 7.12
        elif status != contracts.Verification_Status.VERIFIED:
            # `UNVERIFIED`/`DISCOVERED`/`REJECTED`/enum 이탈은 Active_Model이 아니다.
            reasons.append(DROP_ENTRY_NOT_VERIFIED)

        current_fp = _text(cap_entry.get("capabilityFingerprint"))
        if current_fp and current_fp != _text(setting.get("capabilityFingerprint")):
            reasons.append(DROP_FINGERPRINT_MISMATCH)  # 7.9

        routes = cap_entry.get("routes")
        route_entry = routes.get(route) if _is_dict(routes) else None
        if not (_is_dict(route_entry) and route_entry.get("status") == contracts.Route_Support_Status.SUPPORTED):
            reasons.append(DROP_ROUTE_NOT_SUPPORTED)  # 7.13

        effort = cap_entry.get("effort")
        effort_entry = effort.get(route) if _is_dict(effort) else None
        if not (_is_dict(effort_entry) and effort_entry.get("status") == contracts.Effort_Support_Status.SUPPORTED):
            reasons.append(DROP_EFFORT_NOT_SUPPORTED)

    if fingerprints is not None and model_id in fingerprints:
        wanted_fp = _text(fingerprints.get(model_id))
        if wanted_fp and wanted_fp != _text(setting.get("capabilityFingerprint")):
            reasons.append(DROP_FINGERPRINT_MISMATCH)  # 7.9 — 색인 없이 fingerprint만 알 때

    contract, provided = _contract_for(setting, cap_entry, ctx_contracts)
    if provided:
        if not contracts.effort_contract_is_complete(contract):
            reasons.append(DROP_CONTRACT_INCOMPLETE)  # 계약이 불완전하면 domain을 모른다
        elif _text(contract.get("modelId")) != model_id or _text(contract.get("routeKey")) != route:
            reasons.append(DROP_CONTRACT_MISBOUND)  # 다른 model·route 계약으로는 판정하지 않는다
        else:
            if _text(setting.get("valueType")) != _text(contract.get("valueType")):
                reasons.append(DROP_VALUE_TYPE_MISMATCH)  # 7.10
            if not value_in_domain(contract, setting.get("value")):
                reasons.append(DROP_DOMAIN_VIOLATION)  # 7.10

    return reasons


def drop_reasons(setting: Any, ctx: Mapping[str, Any] | None = None) -> list[str]:
    """항목을 제거해야 하는 이유 코드 목록(빈 목록이면 유지한다).

    ctx 키(모두 선택):
      ``selection``           `{modelId, route, capabilityFingerprint}` 현재 선택 tuple
      ``entriesByModelId`` / ``capabilityEntries`` / ``capabilityMap``  capability entry 출처
      ``currentFingerprints`` `{modelId: Capability_Fingerprint}`
      ``effortContracts``     `{modelId: {routeKey: Effort_Contract}}`
    """
    if is_malformed_entry(setting):
        return [DROP_MALFORMED]
    reasons = _selection_reasons(setting, _selection_of(ctx))
    reasons.extend(
        _state_reasons(
            setting,
            index=capability_index(ctx),
            fingerprints=_mapping_of(ctx, FINGERPRINT_CTX_KEY),
            ctx_contracts=_mapping_of(ctx, EFFORT_CONTRACT_CTX_KEY),
        )
    )
    return sorted(set(reasons))


# ─────────────────────────────────────────────────────────────────
# prune / restore
# ─────────────────────────────────────────────────────────────────
def prune_with_reasons(
    settings: Any,
    ctx: Mapping[str, Any] | None = None,
) -> tuple[dict, list[dict]]:
    """(정리된 settings, 제거 기록)을 반환한다.

    제거 기록 항목은 `{"entry": 원본 항목, "reasons": [이유 코드], "detail": [스키마 이유]}`
    형식이며, 보고서·로그에 남길 수 있도록 결정론적으로 정렬된다.
    """
    normalized = normalize(settings)
    selection = _selection_of(ctx)
    index = capability_index(ctx)
    fingerprints = _mapping_of(ctx, FINGERPRINT_CTX_KEY)
    ctx_contracts = _mapping_of(ctx, EFFORT_CONTRACT_CTX_KEY)

    kept: dict[tuple[str, str, str], dict] = {}
    removed: list[dict] = []

    for setting in normalized["entries"]:
        schema_reasons = entry_reasons(setting)
        if schema_reasons:
            removed.append({"entry": setting, "reasons": [DROP_MALFORMED], "detail": schema_reasons})
            continue

        reasons = _selection_reasons(setting, selection)
        reasons.extend(
            _state_reasons(
                setting, index=index, fingerprints=fingerprints, ctx_contracts=ctx_contracts
            )
        )
        if reasons:
            removed.append({"entry": setting, "reasons": sorted(set(reasons)), "detail": []})
            continue

        key = key_of(setting)
        prior = kept.get(key)
        if prior is None:
            kept[key] = setting
            continue
        winner, loser = _pick_newer(prior, setting)
        kept[key] = winner
        removed.append({"entry": loser, "reasons": [DROP_DUPLICATE_TUPLE], "detail": []})

    entries = [copy.deepcopy(kept[key]) for key in sorted(kept, key=lambda k: tuple(_sort_bytes(part) for part in k))]
    removed.sort(key=lambda item: (_canonical_text(item["entry"]), ",".join(item["reasons"])))
    return {"schemaVersion": SCHEMA_VERSION, "entries": entries}, removed


def _pick_newer(current: dict, candidate: dict) -> tuple[dict, dict]:
    """같은 tuple 중복 항목에서 (유지, 제거)를 결정론적으로 고른다.

    최신 `updatedAt`을 유지하고, 동시각이면 canonical 직렬화 오름차순 첫 항목을
    유지한다(입력 순서와 무관하다).
    """
    current_rank = _recency_rank(current.get("updatedAt"))
    candidate_rank = _recency_rank(candidate.get("updatedAt"))
    if candidate_rank != current_rank:
        return (candidate, current) if candidate_rank > current_rank else (current, candidate)
    if _canonical_text(candidate) < _canonical_text(current):
        return candidate, current
    return current, candidate


def prune(settings: Any, ctx: Mapping[str, Any] | None = None) -> dict:
    """어긋난 저장 value를 제거한 새 Effort_Settings를 반환한다(입력은 변경하지 않는다).

    제거 조건은 모듈 docstring의 표와 같다. 스키마 위반, 현재 선택 tuple 불일치
    (modelId·route·Capability_Fingerprint), verified domain 이탈, 선택 model entry
    삭제·`STALE`, route의 `SUPPORTED` 상실, effort 미지원, 같은 tuple 중복이 대상이다.

    **request 생성 전에 호출한다.** 반환 settings에 남은 항목은 현재 선택 tuple과
    capability 상태를 모두 만족하므로, 그 값만 request 생성 입력이 될 수 있다.

    Args:
        settings: 저장된 Effort_Settings(임의 형식 허용 — 내부에서 정규화한다).
        ctx: 판정 컨텍스트(:func:`drop_reasons` 참조). 항목을 주지 않으면 해당 검사를
            생략한다("모른다"를 이유로 지우지 않는다).

    Returns:
        `{schemaVersion, entries}` — entries는 tuple 키 순으로 정렬된다.
    """
    pruned, _ = prune_with_reasons(settings, ctx)
    return pruned


def restore(settings: Any, tuple_key: Any) -> dict | None:
    """새 tuple과 정확히 일치하는 항목만 복원한다(Requirement 7.14).

    `(modelId, route, capabilityFingerprint)` 세 값이 모두 일치하고 스키마를 만족하는
    항목이 있을 때만 그 항목의 복사본을 반환한다. 그 외에는 ``None``(명시적 미선택)이며,
    호출자는 effort를 선택하지 않은 것으로 취급해야 한다.

    이 함수는 capability 상태를 다시 확인하지 않는다. 상태 기반 정리는 :func:`prune`이
    담당하므로, 요청 경로에서는 `prune` 결과에 대해 호출한다(:func:`active_selection`).
    """
    key = _complete_key(tuple_key)
    if key is None:
        return None
    for setting in normalize(settings)["entries"]:
        if is_malformed_entry(setting):
            continue
        if key_of(setting) == key:
            return copy.deepcopy(setting)
    return None


def to_selection(setting: Any) -> dict | None:
    """저장 항목을 Request_Builder가 쓰는 effort selection dict로 바꾼다.

    반환: `{modelId, route, capabilityFingerprint, value, valueType}` 또는 ``None``.
    """
    if not _is_dict(setting) or is_malformed_entry(setting):
        return None
    return {
        "modelId": _text(setting.get("modelId")),
        "route": _text(setting.get("route")),
        "capabilityFingerprint": _text(setting.get("capabilityFingerprint")),
        "value": setting.get("value"),
        "valueType": _text(setting.get("valueType")),
    }


def active_selection(settings: Any, ctx: Mapping[str, Any] | None = None) -> dict | None:
    """request에 실을 effort selection을 반환한다(없으면 ``None`` — 명시적 미선택).

    :func:`prune`으로 어긋난 값을 먼저 제거하고, ctx의 현재 선택 tuple과 세 값이 모두
    일치하는 항목만 :func:`to_selection` 형태로 돌려준다. ctx에 선택 tuple이 없거나
    tuple이 불완전하면 ``None``이다.
    """
    pruned = prune(settings, ctx)
    selection = _selection_of(ctx)
    if not selection:
        return None
    restored = restore(pruned, selection)
    return None if restored is None else to_selection(restored)


# ─────────────────────────────────────────────────────────────────
# 저장·제거 (Requirement 7.4~7.6)
# ─────────────────────────────────────────────────────────────────
def put(
    settings: Any,
    *,
    model_id: str,
    route: str,
    capability_fingerprint: str,
    value: Any,
    value_type: str,
    now: str | None = None,
    contract: Any = None,
) -> dict:
    """선택한 effort value를 tuple과 함께 저장한 새 settings를 반환한다.

    modelId(7.4)·route(7.5)·Capability_Fingerprint(7.6)를 value와 함께 기록하며,
    같은 tuple의 기존 항목은 교체한다. `contract`를 주면 verified domain 소속까지
    확인하고, 벗어난 값은 저장하지 않는다.

    Raises:
        EffortSettingsError: tuple 성분이 비었거나 route·valueType이 닫힌 집합을
            벗어났거나 value가 valueType 또는 verified domain과 어긋난다.
    """
    key = tuple_key(model_id, route, capability_fingerprint)
    if not all(key.values()):
        raise EffortSettingsError("Effort_Settings에는 modelId·route·capabilityFingerprint가 모두 필요하다")
    if not contracts.Known_Route.has(key["route"]):
        raise EffortSettingsError(f"Known_Route가 아니다: {key['route']!r}")
    if not contracts.Value_Type.has(value_type):
        raise EffortSettingsError(f"Value_Type이 아니다: {value_type!r}")
    if not contracts.value_matches_type(value, value_type):
        raise EffortSettingsError(f"value가 {value_type} 타입과 일치하지 않는다")
    if contract is not None and not value_in_domain(contract, value):
        raise EffortSettingsError("value가 verified domain에 속하지 않는다")

    timestamp = now if isinstance(now, str) and now else contracts.utc_now_iso()
    if not contracts.is_utc_iso8601(timestamp):
        raise EffortSettingsError("updatedAt은 UTC ISO 8601이어야 한다")

    new_entry = {
        "modelId": key["modelId"],
        "route": key["route"],
        "capabilityFingerprint": key["capabilityFingerprint"],
        "value": value,
        "valueType": str(value_type),
        "updatedAt": timestamp,
    }
    kept = [
        copy.deepcopy(setting)
        for setting in normalize(settings)["entries"]
        if key_of(setting) != key_of(new_entry)
    ]
    kept.append(new_entry)
    kept.sort(key=lambda setting: tuple(_sort_bytes(part) for part in key_of(setting)))
    return {"schemaVersion": SCHEMA_VERSION, "entries": kept}


def remove(settings: Any, tuple_key: Any) -> dict:
    """tuple과 일치하는 항목을 제거한 새 settings를 반환한다.

    tuple의 비어 있는 성분은 비교하지 않으므로, `{"modelId": ...}` 하나만 주면 해당
    모델의 모든 항목을 제거한다(entry 삭제·`STALE` 전이 대응 — Requirement 7.11, 7.12).
    """
    if not _is_dict(tuple_key):
        return normalize(settings)
    wanted = {field: _text(tuple_key.get(field)) for field in TUPLE_KEY_FIELDS}
    if not any(wanted.values()):
        return normalize(settings)

    kept = []
    for setting in normalize(settings)["entries"]:
        matches = all(
            not value or _text(setting.get(field)) == value for field, value in wanted.items()
        )
        if not matches:
            kept.append(copy.deepcopy(setting))
    kept.sort(key=lambda item: tuple(_sort_bytes(part) for part in key_of(item)))
    return {"schemaVersion": SCHEMA_VERSION, "entries": kept}


def entries_of(settings: Any) -> list:
    """settings의 항목 목록을 반환한다(형식 위반은 빈 목록)."""
    return normalize(settings)["entries"]


__all__ = [
    "CAPABILITY_CTX_KEYS",
    "DROP_CONTRACT_INCOMPLETE",
    "DROP_CONTRACT_MISBOUND",
    "DROP_DOMAIN_VIOLATION",
    "DROP_DUPLICATE_TUPLE",
    "DROP_EFFORT_NOT_SUPPORTED",
    "DROP_ENTRY_ABSENT",
    "DROP_ENTRY_NOT_VERIFIED",
    "DROP_ENTRY_STALE",
    "DROP_FINGERPRINT_MISMATCH",
    "DROP_MALFORMED",
    "DROP_MODEL_ID_MISMATCH",
    "DROP_REASONS",
    "DROP_ROUTE_MISMATCH",
    "DROP_ROUTE_NOT_SUPPORTED",
    "DROP_VALUE_TYPE_MISMATCH",
    "EFFORT_CONTRACT_CTX_KEY",
    "EffortSettingsError",
    "FINGERPRINT_CTX_KEY",
    "REQUIRED_SETTINGS_ENTRY_FIELDS",
    "SCHEMA_VERSION",
    "SELECTION_CTX_KEY",
    "SETTINGS_KEYS",
    "TUPLE_KEY_FIELDS",
    "active_selection",
    "capability_index",
    "drop_reasons",
    "entries_of",
    "entry_reasons",
    "is_malformed_entry",
    "key_of",
    "new_settings",
    "normalize",
    "prune",
    "prune_with_reasons",
    "put",
    "remove",
    "restore",
    "to_selection",
    "tuple_key",
    "value_in_domain",
]
