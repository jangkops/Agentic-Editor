"""capability 계약 스키마·닫힌 enum·Malformed_Entry 판정기.

이 모듈은 Capability_Map entry, Route_Entry, Effort_Entry, Route_Contract,
Effort_Contract, Verification_Record의 **구조**만 정의한다.

특정 model ID, provider, route 지원 여부, effort field path, effort 허용값은
이 모듈에 존재하지 않는다. 전부 Authoritative_Evidence가 채우는 자리이며,
Candidate_Label·Provider_String 문자열에서 값을 유도하지 않는다(값 추론 금지).

예외적으로 Known_Route의 **execution mode**는 이 모듈이 정의한다
(:data:`DEFAULT_ROUTE_EXECUTION_MODES`). mode는 route를 열거한 순간 결정되는 route의
정의이지 evidence가 관측하는 값이 아니며, write·read 양쪽이 같은 값으로 파생 모드
상태를 유도하려면 최하위 계층에 하나만 있어야 한다.

미확정(undetermined) 표현 규칙:
  - 계약 자리와 nullable 필드 → ``None``
  - 문자열 식별자·fingerprint·revision·시각 필드 → 빈 문자열 ``""``
  - ``verificationStatus`` 초기값 → ``UNVERIFIED``
  - route/effort 상태 초기값 → ``UNVERIFIED``, allowlist 초기값 → ``UNVERIFIED``
"미존재"와 "미확정"을 구분하기 위해 nullable 필드는 삭제하지 않고 ``None``으로 남긴다.

모든 시각 필드는 UTC ISO 8601 문자열로만 저장한다(`utc_now_iso`, `to_utc_iso`).
빈 문자열은 "아직 시각이 없음"을 뜻하고, 비어 있지 않으면 반드시 UTC ISO 8601이어야 한다.

Malformed_Entry 판정 범주(닫힌 집합, `MALFORMED_CODES`):
  MISSING_FIELD          필수 필드 누락
  TYPE_MISMATCH          타입 불일치(시각 형식 위반 포함)
  ENUM_VIOLATION         닫힌 enum 이탈·계약 결속 키 불일치
  FINGERPRINT_MISMATCH   저장된 Capability_Fingerprint와 재계산값 불일치
  EVIDENCE_INTEGRITY     evidence 참조 무결성 실패

참조: design.md "Data Models" 절, Requirements 1.15, 3.1, 3.2, 3.3, 3.5, 3.6,
      3.8, 3.9, 3.10, 3.11, 3.17
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from enum import StrEnum
from typing import Any, Callable, Iterable, Sequence

SCHEMA_VERSION = 1
SUPPORTED_SCHEMA_VERSIONS: tuple[int, ...] = (1,)

#: 문자열 필드의 미확정 표현. 값 추론 금지 원칙에 따라 추측값을 넣지 않는다.
UNDETERMINED = ""

#: Gateway가 usage/cost를 제공하지 않은 경우의 저장 값(Requirement 11.19).
NOT_PROVIDED = "notProvided"


# ---------------------------------------------------------------------------
# 닫힌 enum
# ---------------------------------------------------------------------------
class _ClosedEnum(StrEnum):
    """닫힌 값 집합 enum 공통 기반. 멤버 값 외의 문자열은 enum 이탈로 판정한다."""

    @classmethod
    def values(cls) -> tuple[str, ...]:
        return tuple(member.value for member in cls)

    @classmethod
    def has(cls, value: Any) -> bool:
        return isinstance(value, str) and value in cls.values()


class Verification_Status(_ClosedEnum):
    UNVERIFIED = "UNVERIFIED"
    DISCOVERED = "DISCOVERED"
    VERIFIED = "VERIFIED"
    REJECTED = "REJECTED"
    STALE = "STALE"


class Route_Support_Status(_ClosedEnum):
    SUPPORTED = "SUPPORTED"
    UNSUPPORTED = "UNSUPPORTED"
    UNVERIFIED = "UNVERIFIED"
    NOT_ADVERTISED = "NOT_ADVERTISED"


class Effort_Support_Status(_ClosedEnum):
    SUPPORTED = "SUPPORTED"
    UNSUPPORTED = "UNSUPPORTED"
    UNVERIFIED = "UNVERIFIED"
    STALE = "STALE"


class Allowlist_Result(_ClosedEnum):
    ALLOWED = "ALLOWED"
    REJECTED = "REJECTED"
    UNVERIFIED = "UNVERIFIED"


class Known_Route(_ClosedEnum):
    CONVERSE = "CONVERSE"
    INVOKE = "INVOKE"
    OPENAI_RESPONSES = "OPENAI_RESPONSES"
    OPENAI_RESPONSES_JOBS = "OPENAI_RESPONSES_JOBS"
    SSE_STREAM = "SSE_STREAM"


class Execution_Mode(_ClosedEnum):
    SYNC = "SYNC"
    ASYNC = "ASYNC"
    STREAMING = "STREAMING"


class Signing_Service(_ClosedEnum):
    EXECUTE_API = "execute-api"
    LAMBDA = "lambda"


class Domain_Kind(_ClosedEnum):
    ENUM = "ENUM"
    RANGE = "RANGE"


class Value_Type(_ClosedEnum):
    """저장 표현의 닫힌 집합. 특정 모델·route의 실제 value type은 evidence가 지정한다."""

    STRING = "STRING"
    INTEGER = "INTEGER"
    NUMBER = "NUMBER"
    BOOLEAN = "BOOLEAN"


class Source_Kind(_ClosedEnum):
    CATALOG = "CATALOG"
    OPERATOR_EXPORT = "OPERATOR_EXPORT"
    SEED = "SEED"


class Failure_Category(_ClosedEnum):
    AUTHENTICATION = "authentication"
    ALLOWLIST = "allowlist"
    EFFORT_MISMATCH = "effort-mismatch"
    ROUTE_CAPABILITY_MISMATCH = "route-capability-mismatch"
    QUOTA = "quota"
    TRANSIENT = "transient"
    REQUEST_VALIDATION = "request-validation"
    UNKNOWN = "unknown"


#: 모든 Known_Route 값(entry의 routes·effort는 이 키 집합만 사용한다).
KNOWN_ROUTES: tuple[str, ...] = Known_Route.values()


#: Known_Route → Execution_Mode 기본 매핑.
#:
#: route의 execution mode는 **evidence가 아니라 route의 정의**다. 어떤 transport 형태를
#: 쓰는지(단발 응답 / 잡 제출 후 폴링 / 스트리밍)는 Known_Route를 열거한 순간 결정되며
#: 모델·provider·probe 결과에 따라 달라지지 않는다. 따라서 이 매핑은 evidence 계층
#: (`evidence_collector.ROUTE_PROFILES`)이 아니라 최하위 계약 계층에 둔다 — write 시점과
#: read 시점이 **동일한 값으로** 파생 모드 상태를 유도해야 하기 때문이다.
#:
#: 값의 출처는 기존 구현(Baseline_Record)이며 `evidence_collector.ROUTE_PROFILES`가 이
#: 상수를 참조한다(단일 출처). route 지원 **여부**는 여기에 없다 — 그것만이 evidence의
#: 몫이다(값 추론 금지 원칙 유지).
DEFAULT_ROUTE_EXECUTION_MODES: dict[str, str] = {
    str(Known_Route.CONVERSE): str(Execution_Mode.SYNC),
    str(Known_Route.INVOKE): str(Execution_Mode.SYNC),
    str(Known_Route.OPENAI_RESPONSES): str(Execution_Mode.SYNC),
    str(Known_Route.OPENAI_RESPONSES_JOBS): str(Execution_Mode.ASYNC),
    str(Known_Route.SSE_STREAM): str(Execution_Mode.STREAMING),
}


def default_execution_mode(route_key: Any) -> str | None:
    """Known_Route의 기본 Execution_Mode(모르는 route는 ``None``).

    :data:`DEFAULT_ROUTE_EXECUTION_MODES`의 유일한 읽기 경로다. Route_Contract에
    `executionMode`가 없는 route(상태만 기록된 `NOT_ADVERTISED`/`UNVERIFIED` 등)도
    write·read 양쪽에서 같은 mode로 유도되게 한다.
    """
    if not Known_Route.has(route_key):
        return None
    return DEFAULT_ROUTE_EXECUTION_MODES[str(route_key)]


# ---------------------------------------------------------------------------
# UTC ISO 8601 시각
# ---------------------------------------------------------------------------
def to_utc_iso(value: datetime) -> str:
    """tz-aware datetime → UTC ISO 8601 문자열(`...Z`). naive datetime은 거부한다."""
    if not isinstance(value, datetime):
        raise TypeError("datetime이 필요하다")
    if value.tzinfo is None:
        raise ValueError("naive datetime은 UTC ISO 8601로 저장할 수 없다")
    return value.astimezone(timezone.utc).isoformat(timespec="microseconds").replace("+00:00", "Z")


def utc_now_iso() -> str:
    """현재 시각의 UTC ISO 8601 문자열."""
    return to_utc_iso(datetime.now(timezone.utc))


def is_utc_iso8601(value: Any) -> bool:
    """UTC ISO 8601 문자열인지 판정한다(offset 0, 날짜·시각 구분자 `T` 필수)."""
    if not isinstance(value, str) or not value or "T" not in value:
        return False
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError:
        return False
    offset = parsed.utcoffset()
    return offset is not None and offset == timedelta(0)


def _time_field_ok(value: Any, *, allow_undetermined: bool = True) -> bool:
    if not isinstance(value, str):
        return False
    if value == UNDETERMINED:
        return allow_undetermined
    return is_utc_iso8601(value)


# ---------------------------------------------------------------------------
# Malformed_Entry 판정 코드
# ---------------------------------------------------------------------------
MISSING_FIELD = "MISSING_FIELD"
TYPE_MISMATCH = "TYPE_MISMATCH"
ENUM_VIOLATION = "ENUM_VIOLATION"
FINGERPRINT_MISMATCH = "FINGERPRINT_MISMATCH"
EVIDENCE_INTEGRITY = "EVIDENCE_INTEGRITY"

MALFORMED_CODES: tuple[str, ...] = (
    MISSING_FIELD,
    TYPE_MISMATCH,
    ENUM_VIOLATION,
    FINGERPRINT_MISMATCH,
    EVIDENCE_INTEGRITY,
)


def _reason(code: str, path: str, detail: str = "") -> str:
    """결정론적 판정 이유 문자열. 형식: ``CODE:path`` 또는 ``CODE:path(detail)``."""
    return f"{code}:{path}({detail})" if detail else f"{code}:{path}"


# ---------------------------------------------------------------------------
# 타입 술어
# ---------------------------------------------------------------------------
def _is_str(value: Any) -> bool:
    return isinstance(value, str)


def _is_int(value: Any) -> bool:
    return isinstance(value, int) and not isinstance(value, bool)


def _is_number(value: Any) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool)


def _is_bool(value: Any) -> bool:
    return isinstance(value, bool)


def _is_dict(value: Any) -> bool:
    return isinstance(value, dict)


def _is_list(value: Any) -> bool:
    return isinstance(value, list)


def _is_str_list(value: Any) -> bool:
    return isinstance(value, list) and all(isinstance(item, str) for item in value)


def value_matches_type(value: Any, value_type: Any) -> bool:
    """value가 Value_Type 표현과 일치하는지 판정한다(effort value 저장 타입 검사)."""
    if value_type == Value_Type.STRING:
        return _is_str(value)
    if value_type == Value_Type.INTEGER:
        return _is_int(value)
    if value_type == Value_Type.NUMBER:
        return _is_number(value)
    if value_type == Value_Type.BOOLEAN:
        return _is_bool(value)
    return False


# ---------------------------------------------------------------------------
# 스키마 필드 목록
# ---------------------------------------------------------------------------
REQUIRED_ENTRY_FIELDS: tuple[str, ...] = (
    "schemaVersion",
    "candidateLabel",
    "modelId",
    "invocationModelIds",
    "provider",
    "sourceKind",
    "catalogFingerprint",
    "routes",
    "syncSupport",
    "asyncSupport",
    "streamingSupport",
    "effort",
    "verifiedAt",
    "revision",
    "evidence",
    "verificationStatus",
    "capabilityFingerprint",
)
OPTIONAL_ENTRY_FIELDS: tuple[str, ...] = ("displayName",)

REQUIRED_ROUTE_ENTRY_FIELDS: tuple[str, ...] = ("status", "allowlist", "contract", "evidenceRef")
REQUIRED_EFFORT_ENTRY_FIELDS: tuple[str, ...] = ("status", "contract", "evidenceRef")

REQUIRED_ROUTE_CONTRACT_FIELDS: tuple[str, ...] = (
    "routeKey",
    "endpointRef",
    "httpMethod",
    "executionMode",
    "signingService",
    "modelIdRequired",
    "modelIdFieldPath",
    "messageFieldPath",
    "inferenceConfigFieldPath",
    "optionalFields",
    "outputValidatorRef",
    "terminalConditionRef",
    "retryPolicyRef",
    "fallbackRank",
    "purposes",
    "minOutputBound",
    "evidenceRef",
)
REQUIRED_EFFORT_CONTRACT_FIELDS: tuple[str, ...] = (
    "modelId",
    "routeKey",
    "fieldPath",
    "valueType",
    "domainKind",
    "enumValues",
    "rangeLowerInclusive",
    "rangeUpperInclusive",
    "verifiedValues",
    "evidenceRef",
)
REQUIRED_VERIFICATION_RECORD_FIELDS: tuple[str, ...] = (
    "evidenceRecordId",
    "runId",
    "verifiedAt",
    "revision",
    "interpreterPath",
    "environment",
    "candidateLabel",
    "modelId",
    "provider",
    "invocationModelIds",
    "catalogFingerprint",
    "capabilityFingerprint",
    "routeResults",
    "effortResults",
    "usage",
    "cost",
    "routeCompleteness",
)
REQUIRED_ENVIRONMENT_FIELDS: tuple[str, ...] = ("gatewayEnvironmentId", "endpointIdentity", "region")
REQUIRED_ROUTE_RESULT_FIELDS: tuple[str, ...] = (
    "routeKey",
    "http",
    "validOutput",
    "terminalSuccess",
    "allowlist",
    "status",
    "probeId",
    "sanitizedSchema",
    "correctionUsed",
)
REQUIRED_EFFORT_RESULT_FIELDS: tuple[str, ...] = (
    "routeKey",
    "fieldPath",
    "value",
    "status",
    "probeId",
    "sanitizedSchema",
    "baselineSucceeded",
)


# ---------------------------------------------------------------------------
# 팩토리 — 미확정 상태를 명시적으로 만든다(값 추론 금지)
# ---------------------------------------------------------------------------
def new_route_entry(
    status: str = Route_Support_Status.UNVERIFIED,
    allowlist: str = Allowlist_Result.UNVERIFIED,
) -> dict:
    """Route_Entry 초기값. 계약과 evidence 참조는 미확정(`None`)."""
    return {"status": str(status), "allowlist": str(allowlist), "contract": None, "evidenceRef": None}


def new_effort_entry(status: str = Effort_Support_Status.UNVERIFIED) -> dict:
    """Effort_Entry 초기값. 계약과 evidence 참조는 미확정(`None`)."""
    return {"status": str(status), "contract": None, "evidenceRef": None}


def new_entry(
    candidate_label: str,
    *,
    source_kind: str = Source_Kind.CATALOG,
    revision: str = UNDETERMINED,
    fingerprint_fn: Callable[[dict], str] | None = None,
) -> dict:
    """Candidate_Label만 아는 상태의 초기 `UNVERIFIED` entry를 만든다.

    model ID·provider·catalog fingerprint·시각은 빈 문자열, 모든 route·effort
    계약은 `None`으로 남긴다(Requirement 1.15 — 라벨에서 값을 유도하지 않는다).
    `fingerprint_fn`을 주면 그 결과를 `capabilityFingerprint`에 기록하고,
    주지 않으면 빈 문자열(미확정)로 둔다.
    """
    entry = {
        "schemaVersion": SCHEMA_VERSION,
        "candidateLabel": candidate_label,
        "modelId": UNDETERMINED,
        "invocationModelIds": [],
        "provider": UNDETERMINED,
        "displayName": None,
        "sourceKind": str(source_kind),
        "catalogFingerprint": UNDETERMINED,
        "routes": {route: new_route_entry() for route in KNOWN_ROUTES},
        "syncSupport": str(Route_Support_Status.UNVERIFIED),
        "asyncSupport": str(Route_Support_Status.UNVERIFIED),
        "streamingSupport": str(Route_Support_Status.UNVERIFIED),
        "effort": {route: new_effort_entry() for route in KNOWN_ROUTES},
        "verifiedAt": UNDETERMINED,
        "revision": revision,
        "evidence": [],
        "verificationStatus": str(Verification_Status.UNVERIFIED),
        "capabilityFingerprint": UNDETERMINED,
    }
    if fingerprint_fn is not None:
        entry["capabilityFingerprint"] = fingerprint_fn(entry)
    return entry


def new_route_contract(
    route_key: str,
    *,
    endpoint_ref: str = UNDETERMINED,
    http_method: str = UNDETERMINED,
    execution_mode: str | None = None,
    signing_service: str | None = None,
    model_id_required: bool | None = None,
    model_id_field_path: Sequence[str] | None = None,
    message_field_path: Sequence[str] | None = None,
    inference_config_field_path: Sequence[str] | None = None,
    optional_fields: Iterable[str] = (),
    output_validator_ref: str = UNDETERMINED,
    terminal_condition_ref: str = UNDETERMINED,
    retry_policy_ref: str = UNDETERMINED,
    fallback_rank: int = 0,
    purposes: Iterable[str] = (),
    min_output_bound: dict | None = None,
    evidence_ref: str | None = None,
) -> dict:
    """Route_Contract를 만든다. 채워지지 않은 자리는 미확정으로 남는다.

    endpoint·method·mode·signing service는 기존 구현(Baseline_Record)이 알려주는
    값이고, model ID·field path는 production probe evidence가 채운다.
    """
    return {
        "routeKey": str(route_key),
        "endpointRef": endpoint_ref,
        "httpMethod": http_method,
        "executionMode": None if execution_mode is None else str(execution_mode),
        "signingService": None if signing_service is None else str(signing_service),
        "modelIdRequired": model_id_required,
        "modelIdFieldPath": None if model_id_field_path is None else list(model_id_field_path),
        "messageFieldPath": None if message_field_path is None else list(message_field_path),
        "inferenceConfigFieldPath": (
            None if inference_config_field_path is None else list(inference_config_field_path)
        ),
        "optionalFields": list(optional_fields),
        "outputValidatorRef": output_validator_ref,
        "terminalConditionRef": terminal_condition_ref,
        "retryPolicyRef": retry_policy_ref,
        "fallbackRank": fallback_rank,
        "purposes": list(purposes),
        "minOutputBound": {} if min_output_bound is None else dict(min_output_bound),
        "evidenceRef": evidence_ref,
    }


def new_effort_contract(
    model_id: str,
    route_key: str,
    *,
    field_path: Sequence[str] | None = None,
    value_type: str | None = None,
    domain_kind: str | None = None,
    enum_values: Sequence[Any] | None = None,
    range_lower_inclusive: float | int | None = None,
    range_upper_inclusive: float | int | None = None,
    verified_values: Iterable[Any] = (),
    evidence_ref: str | None = None,
) -> dict:
    """Effort_Contract를 만든다. field path·value type·domain은 evidence만 채운다."""
    return {
        "modelId": model_id,
        "routeKey": str(route_key),
        "fieldPath": None if field_path is None else list(field_path),
        "valueType": None if value_type is None else str(value_type),
        "domainKind": None if domain_kind is None else str(domain_kind),
        "enumValues": None if enum_values is None else list(enum_values),
        "rangeLowerInclusive": range_lower_inclusive,
        "rangeUpperInclusive": range_upper_inclusive,
        "verifiedValues": list(verified_values),
        "evidenceRef": evidence_ref,
    }


def new_verification_record(
    *,
    run_id: str,
    revision: str = UNDETERMINED,
    interpreter_path: str = UNDETERMINED,
    environment: dict | None = None,
    candidate_label: str = UNDETERMINED,
    model_id: str = UNDETERMINED,
    provider: str = UNDETERMINED,
    invocation_model_ids: Iterable[str] = (),
    catalog_fingerprint: str = UNDETERMINED,
    capability_fingerprint: str = UNDETERMINED,
    route_results: Iterable[dict] = (),
    effort_results: Iterable[dict] = (),
    usage: dict | str = NOT_PROVIDED,
    cost: dict | str = NOT_PROVIDED,
    route_completeness: bool = False,
    verified_at: str | None = None,
    evidence_record_id: str = UNDETERMINED,
) -> dict:
    """정제 전 Verification_Record 골격. Evidence_Record_ID는 canonicalizer가 채운다."""
    env = dict(environment or {})
    for key in REQUIRED_ENVIRONMENT_FIELDS:
        env.setdefault(key, UNDETERMINED)
    return {
        "evidenceRecordId": evidence_record_id,
        "runId": run_id,
        "verifiedAt": utc_now_iso() if verified_at is None else verified_at,
        "revision": revision,
        "interpreterPath": interpreter_path,
        "environment": env,
        "candidateLabel": candidate_label,
        "modelId": model_id,
        "provider": provider,
        "invocationModelIds": list(invocation_model_ids),
        "catalogFingerprint": catalog_fingerprint,
        "capabilityFingerprint": capability_fingerprint,
        "routeResults": [dict(item) for item in route_results],
        "effortResults": [dict(item) for item in effort_results],
        "usage": usage,
        "cost": cost,
        "routeCompleteness": route_completeness,
    }


# ---------------------------------------------------------------------------
# 계약 완전성 — `SUPPORTED` 상태가 요구하는 필드가 모두 채워졌는지
# ---------------------------------------------------------------------------
def route_contract_missing(contract: Any) -> list[str]:
    """완전한 Route_Contract가 되기 위해 아직 미확정인 필드 이름 목록(Requirement 3.3)."""
    if not _is_dict(contract):
        return list(REQUIRED_ROUTE_CONTRACT_FIELDS)
    missing: list[str] = []
    for field in ("endpointRef", "httpMethod", "outputValidatorRef", "terminalConditionRef", "retryPolicyRef"):
        if not (_is_str(contract.get(field)) and contract.get(field) != UNDETERMINED):
            missing.append(field)
    if not Execution_Mode.has(contract.get("executionMode")):
        missing.append("executionMode")
    if not Signing_Service.has(contract.get("signingService")):
        missing.append("signingService")
    if not _is_bool(contract.get("modelIdRequired")):
        missing.append("modelIdRequired")
    elif contract.get("modelIdRequired") and not (_is_str_list(contract.get("modelIdFieldPath")) and contract["modelIdFieldPath"]):
        # model ID를 요구하는 계약은 exact field path가 있어야 1회 기록을 보장할 수 있다.
        missing.append("modelIdFieldPath")
    if not (_is_str_list(contract.get("messageFieldPath")) and contract["messageFieldPath"]):
        missing.append("messageFieldPath")
    if not _is_int(contract.get("fallbackRank")):
        missing.append("fallbackRank")
    if not (_is_str_list(contract.get("purposes")) and contract["purposes"]):
        missing.append("purposes")
    if not (_is_dict(contract.get("minOutputBound")) and contract["minOutputBound"]):
        missing.append("minOutputBound")
    return missing


def route_contract_is_complete(contract: Any) -> bool:
    return not route_contract_missing(contract)


def effort_contract_missing(contract: Any) -> list[str]:
    """완전한 Effort_Contract가 되기 위해 아직 미확정인 필드 이름 목록(Requirement 3.6).

    domain별 요구: `ENUM`은 비어 있지 않은 `enumValues`, `RANGE`는 두 inclusive 경계.
    하나라도 비면 Effort_Support_Status는 `UNVERIFIED`를 유지해야 한다.
    """
    if not _is_dict(contract):
        return list(REQUIRED_EFFORT_CONTRACT_FIELDS)
    missing: list[str] = []
    if not (_is_str(contract.get("modelId")) and contract.get("modelId") != UNDETERMINED):
        missing.append("modelId")
    if not Known_Route.has(contract.get("routeKey")):
        missing.append("routeKey")
    if not (_is_str_list(contract.get("fieldPath")) and contract["fieldPath"]):
        missing.append("fieldPath")
    if not Value_Type.has(contract.get("valueType")):
        missing.append("valueType")
    domain_kind = contract.get("domainKind")
    if not Domain_Kind.has(domain_kind):
        missing.append("domainKind")
    elif domain_kind == Domain_Kind.ENUM:
        if not (_is_list(contract.get("enumValues")) and contract["enumValues"]):
            missing.append("enumValues")
    else:  # RANGE
        if not _is_number(contract.get("rangeLowerInclusive")):
            missing.append("rangeLowerInclusive")
        if not _is_number(contract.get("rangeUpperInclusive")):
            missing.append("rangeUpperInclusive")
    return missing


def effort_contract_is_complete(contract: Any) -> bool:
    return not effort_contract_missing(contract)


# ---------------------------------------------------------------------------
# 검증기 — 반환값은 결정론적으로 정렬된 판정 이유 목록
# ---------------------------------------------------------------------------
def validate_route_contract(contract: Any, *, path: str = "contract", route_key: str | None = None) -> list[str]:
    """Route_Contract의 필수 필드·타입·enum을 검사한다(미확정 `None`은 허용)."""
    if not _is_dict(contract):
        return [_reason(TYPE_MISMATCH, path, "not-object")]
    reasons: list[str] = []
    for field in REQUIRED_ROUTE_CONTRACT_FIELDS:
        if field not in contract:
            reasons.append(_reason(MISSING_FIELD, f"{path}.{field}"))

    if "routeKey" in contract and not Known_Route.has(contract["routeKey"]):
        reasons.append(_reason(ENUM_VIOLATION, f"{path}.routeKey"))
    elif route_key is not None and contract.get("routeKey") != route_key:
        reasons.append(_reason(ENUM_VIOLATION, f"{path}.routeKey", "route-key-mismatch"))

    for field in ("endpointRef", "httpMethod", "outputValidatorRef", "terminalConditionRef", "retryPolicyRef"):
        if field in contract and not _is_str(contract[field]):
            reasons.append(_reason(TYPE_MISMATCH, f"{path}.{field}"))
    if "executionMode" in contract and contract["executionMode"] is not None and not Execution_Mode.has(contract["executionMode"]):
        reasons.append(_reason(ENUM_VIOLATION, f"{path}.executionMode"))
    if "signingService" in contract and contract["signingService"] is not None and not Signing_Service.has(contract["signingService"]):
        reasons.append(_reason(ENUM_VIOLATION, f"{path}.signingService"))
    if "modelIdRequired" in contract and contract["modelIdRequired"] is not None and not _is_bool(contract["modelIdRequired"]):
        reasons.append(_reason(TYPE_MISMATCH, f"{path}.modelIdRequired"))
    for field in ("modelIdFieldPath", "messageFieldPath", "inferenceConfigFieldPath"):
        if field in contract and contract[field] is not None and not _is_str_list(contract[field]):
            reasons.append(_reason(TYPE_MISMATCH, f"{path}.{field}"))
    for field in ("optionalFields", "purposes"):
        if field in contract and not _is_str_list(contract[field]):
            reasons.append(_reason(TYPE_MISMATCH, f"{path}.{field}"))
    if "fallbackRank" in contract and not _is_int(contract["fallbackRank"]):
        reasons.append(_reason(TYPE_MISMATCH, f"{path}.fallbackRank"))
    if "minOutputBound" in contract and not _is_dict(contract["minOutputBound"]):
        reasons.append(_reason(TYPE_MISMATCH, f"{path}.minOutputBound"))
    if "evidenceRef" in contract and contract["evidenceRef"] is not None and not _is_str(contract["evidenceRef"]):
        reasons.append(_reason(TYPE_MISMATCH, f"{path}.evidenceRef"))
    return reasons


def validate_effort_contract(
    contract: Any,
    *,
    path: str = "contract",
    route_key: str | None = None,
    model_id: str | None = None,
) -> list[str]:
    """Effort_Contract의 필수 필드·타입·enum·결속 키를 검사한다(미확정 `None` 허용)."""
    if not _is_dict(contract):
        return [_reason(TYPE_MISMATCH, path, "not-object")]
    reasons: list[str] = []
    for field in REQUIRED_EFFORT_CONTRACT_FIELDS:
        if field not in contract:
            reasons.append(_reason(MISSING_FIELD, f"{path}.{field}"))

    if "modelId" in contract and not _is_str(contract["modelId"]):
        reasons.append(_reason(TYPE_MISMATCH, f"{path}.modelId"))
    elif model_id is not None and _is_str(contract.get("modelId")) and contract["modelId"] != model_id:
        reasons.append(_reason(ENUM_VIOLATION, f"{path}.modelId", "model-id-mismatch"))

    if "routeKey" in contract and not Known_Route.has(contract["routeKey"]):
        reasons.append(_reason(ENUM_VIOLATION, f"{path}.routeKey"))
    elif route_key is not None and contract.get("routeKey") != route_key:
        reasons.append(_reason(ENUM_VIOLATION, f"{path}.routeKey", "route-key-mismatch"))

    if "fieldPath" in contract and contract["fieldPath"] is not None and not _is_str_list(contract["fieldPath"]):
        reasons.append(_reason(TYPE_MISMATCH, f"{path}.fieldPath"))
    value_type = contract.get("valueType")
    if "valueType" in contract and value_type is not None and not Value_Type.has(value_type):
        reasons.append(_reason(ENUM_VIOLATION, f"{path}.valueType"))
    if "domainKind" in contract and contract["domainKind"] is not None and not Domain_Kind.has(contract["domainKind"]):
        reasons.append(_reason(ENUM_VIOLATION, f"{path}.domainKind"))
    if "enumValues" in contract and contract["enumValues"] is not None and not _is_list(contract["enumValues"]):
        reasons.append(_reason(TYPE_MISMATCH, f"{path}.enumValues"))
    for field in ("rangeLowerInclusive", "rangeUpperInclusive"):
        if field in contract and contract[field] is not None and not _is_number(contract[field]):
            reasons.append(_reason(TYPE_MISMATCH, f"{path}.{field}"))
    if "verifiedValues" in contract and not _is_list(contract["verifiedValues"]):
        reasons.append(_reason(TYPE_MISMATCH, f"{path}.verifiedValues"))
    if "evidenceRef" in contract and contract["evidenceRef"] is not None and not _is_str(contract["evidenceRef"]):
        reasons.append(_reason(TYPE_MISMATCH, f"{path}.evidenceRef"))

    # value type이 확정되면 저장된 domain 값도 그 타입을 따라야 한다.
    if Value_Type.has(value_type):
        for field in ("enumValues", "verifiedValues"):
            values = contract.get(field)
            if _is_list(values):
                for index, item in enumerate(values):
                    if not value_matches_type(item, value_type):
                        reasons.append(_reason(TYPE_MISMATCH, f"{path}.{field}[{index}]", "value-type"))
    return reasons


def validate_route_entry(
    route_entry: Any,
    *,
    route_key: str,
    evidence_ids: Iterable[str] | None = None,
    path: str | None = None,
) -> list[str]:
    """Route_Entry의 필수 필드·enum·계약·evidence 참조 무결성을 검사한다."""
    path = path or f"routes.{route_key}"
    if not _is_dict(route_entry):
        return [_reason(TYPE_MISMATCH, path, "not-object")]
    reasons: list[str] = []
    for field in REQUIRED_ROUTE_ENTRY_FIELDS:
        if field not in route_entry:
            reasons.append(_reason(MISSING_FIELD, f"{path}.{field}"))

    status = route_entry.get("status")
    if "status" in route_entry and not Route_Support_Status.has(status):
        reasons.append(_reason(ENUM_VIOLATION, f"{path}.status"))
    if "allowlist" in route_entry and not Allowlist_Result.has(route_entry.get("allowlist")):
        reasons.append(_reason(ENUM_VIOLATION, f"{path}.allowlist"))

    contract = route_entry.get("contract")
    if contract is not None:
        reasons.extend(validate_route_contract(contract, path=f"{path}.contract", route_key=route_key))

    reasons.extend(
        _validate_evidence_ref(route_entry.get("evidenceRef"), path=f"{path}.evidenceRef", evidence_ids=evidence_ids)
    )
    if contract is not None and _is_dict(contract):
        reasons.extend(
            _validate_evidence_ref(
                contract.get("evidenceRef"), path=f"{path}.contract.evidenceRef", evidence_ids=evidence_ids
            )
        )

    # `SUPPORTED` route는 완전한 계약과 Current_Evidence reference를 요구한다(3.3, 3.4).
    if status == Route_Support_Status.SUPPORTED:
        if contract is None:
            reasons.append(_reason(MISSING_FIELD, f"{path}.contract", "supported-route"))
        else:
            for field in route_contract_missing(contract):
                reasons.append(_reason(MISSING_FIELD, f"{path}.contract.{field}", "supported-route"))
        if not (_is_str(route_entry.get("evidenceRef")) and route_entry.get("evidenceRef")):
            reasons.append(_reason(EVIDENCE_INTEGRITY, f"{path}.evidenceRef", "supported-route"))
    return reasons


def validate_effort_entry(
    effort_entry: Any,
    *,
    route_key: str,
    model_id: str | None = None,
    evidence_ids: Iterable[str] | None = None,
    path: str | None = None,
) -> list[str]:
    """Effort_Entry의 필수 필드·enum·계약·evidence 참조 무결성을 검사한다."""
    path = path or f"effort.{route_key}"
    if not _is_dict(effort_entry):
        return [_reason(TYPE_MISMATCH, path, "not-object")]
    reasons: list[str] = []
    for field in REQUIRED_EFFORT_ENTRY_FIELDS:
        if field not in effort_entry:
            reasons.append(_reason(MISSING_FIELD, f"{path}.{field}"))

    status = effort_entry.get("status")
    if "status" in effort_entry and not Effort_Support_Status.has(status):
        reasons.append(_reason(ENUM_VIOLATION, f"{path}.status"))

    contract = effort_entry.get("contract")
    if contract is not None:
        reasons.extend(
            validate_effort_contract(contract, path=f"{path}.contract", route_key=route_key, model_id=model_id)
        )

    reasons.extend(
        _validate_evidence_ref(effort_entry.get("evidenceRef"), path=f"{path}.evidenceRef", evidence_ids=evidence_ids)
    )
    if contract is not None and _is_dict(contract):
        reasons.extend(
            _validate_evidence_ref(
                contract.get("evidenceRef"), path=f"{path}.contract.evidenceRef", evidence_ids=evidence_ids
            )
        )

    # `SUPPORTED` effort는 완전한 계약과 Current_Evidence reference를 요구한다(3.6, 3.7).
    if status == Effort_Support_Status.SUPPORTED:
        if contract is None:
            reasons.append(_reason(MISSING_FIELD, f"{path}.contract", "supported-effort"))
        else:
            for field in effort_contract_missing(contract):
                reasons.append(_reason(MISSING_FIELD, f"{path}.contract.{field}", "supported-effort"))
        if not (_is_str(effort_entry.get("evidenceRef")) and effort_entry.get("evidenceRef")):
            reasons.append(_reason(EVIDENCE_INTEGRITY, f"{path}.evidenceRef", "supported-effort"))
    return reasons


def _validate_evidence_ref(ref: Any, *, path: str, evidence_ids: Iterable[str] | None) -> list[str]:
    """evidence reference 타입과 참조 무결성(entry evidence 목록 포함 여부)을 검사한다."""
    if ref is None:
        return []
    if not _is_str(ref):
        return [_reason(TYPE_MISMATCH, path)]
    if ref == UNDETERMINED:
        return [_reason(EVIDENCE_INTEGRITY, path, "empty-ref")]
    if evidence_ids is not None and ref not in set(evidence_ids):
        return [_reason(EVIDENCE_INTEGRITY, path, "unknown-ref")]
    return []


def validate_entry(
    entry: Any,
    *,
    fingerprint_fn: Callable[[dict], str] | None = None,
    known_evidence_ids: Iterable[str] | None = None,
) -> list[str]:
    """Capability_Map entry의 Malformed 판정 이유를 모두 모아 정렬해 반환한다.

    `fingerprint_fn`을 주면 재계산 결과와 저장된 `capabilityFingerprint`를 비교한다
    (canonicalizer 주입 — 이 모듈은 fingerprint 계산 방식을 알지 않는다).
    `known_evidence_ids`를 주면 entry의 `evidence` 항목이 그 집합에 존재하는지도 확인한다.
    빈 목록이면 Malformed_Entry가 아니다.
    """
    if not _is_dict(entry):
        return [_reason(TYPE_MISMATCH, "entry", "not-object")]

    reasons: list[str] = []
    for field in REQUIRED_ENTRY_FIELDS:
        if field not in entry:
            reasons.append(_reason(MISSING_FIELD, field))

    # schemaVersion — 지원 버전 닫힌 집합
    if "schemaVersion" in entry:
        if not _is_int(entry["schemaVersion"]):
            reasons.append(_reason(TYPE_MISMATCH, "schemaVersion"))
        elif entry["schemaVersion"] not in SUPPORTED_SCHEMA_VERSIONS:
            reasons.append(_reason(ENUM_VIOLATION, "schemaVersion", "unsupported-version"))

    # 문자열 식별자 필드(빈 문자열 = 미확정, 타입은 반드시 str)
    for field in ("candidateLabel", "modelId", "provider", "catalogFingerprint", "revision", "capabilityFingerprint"):
        if field in entry and not _is_str(entry[field]):
            reasons.append(_reason(TYPE_MISMATCH, field))
    if "displayName" in entry and entry["displayName"] is not None and not _is_str(entry["displayName"]):
        reasons.append(_reason(TYPE_MISMATCH, "displayName"))
    if "invocationModelIds" in entry and not _is_str_list(entry["invocationModelIds"]):
        reasons.append(_reason(TYPE_MISMATCH, "invocationModelIds"))

    # 시각 필드는 UTC ISO 8601만 허용한다(빈 문자열은 미확정).
    if "verifiedAt" in entry and not _time_field_ok(entry["verifiedAt"]):
        reasons.append(_reason(TYPE_MISMATCH, "verifiedAt", "not-utc-iso8601"))

    # 닫힌 enum
    if "sourceKind" in entry and not Source_Kind.has(entry["sourceKind"]):
        reasons.append(_reason(ENUM_VIOLATION, "sourceKind"))
    if "verificationStatus" in entry and not Verification_Status.has(entry["verificationStatus"]):
        reasons.append(_reason(ENUM_VIOLATION, "verificationStatus"))
    for field in ("syncSupport", "asyncSupport", "streamingSupport"):
        if field in entry and not Route_Support_Status.has(entry[field]):
            reasons.append(_reason(ENUM_VIOLATION, field))

    # evidence 목록
    evidence_ids: list[str] | None = None
    if "evidence" in entry:
        if not _is_str_list(entry["evidence"]):
            reasons.append(_reason(TYPE_MISMATCH, "evidence"))
        else:
            evidence_ids = list(entry["evidence"])
            for index, ref in enumerate(evidence_ids):
                if ref == UNDETERMINED:
                    reasons.append(_reason(EVIDENCE_INTEGRITY, f"evidence[{index}]", "empty-ref"))
            if known_evidence_ids is not None:
                known = set(known_evidence_ids)
                for index, ref in enumerate(evidence_ids):
                    if ref not in known:
                        reasons.append(_reason(EVIDENCE_INTEGRITY, f"evidence[{index}]", "unknown-ref"))

    model_id = entry.get("modelId") if _is_str(entry.get("modelId")) else None

    # routes / effort — 키 집합은 Known_Route 닫힌 집합
    for field, validator in (("routes", validate_route_entry), ("effort", validate_effort_entry)):
        if field not in entry:
            continue
        container = entry[field]
        if not _is_dict(container):
            reasons.append(_reason(TYPE_MISMATCH, field))
            continue
        for key in container:
            if not Known_Route.has(key):
                reasons.append(_reason(ENUM_VIOLATION, f"{field}.{key}", "unknown-route-key"))
        for key, value in container.items():
            if not Known_Route.has(key):
                continue
            if field == "routes":
                reasons.extend(validator(value, route_key=key, evidence_ids=evidence_ids))
            else:
                reasons.extend(validator(value, route_key=key, model_id=model_id, evidence_ids=evidence_ids))

    # Capability_Fingerprint 재계산 일치
    if fingerprint_fn is not None and _is_str(entry.get("capabilityFingerprint")):
        try:
            recomputed = fingerprint_fn(entry)
        except Exception as exc:  # 계산 자체가 실패하면 fingerprint를 신뢰할 수 없다.
            reasons.append(_reason(FINGERPRINT_MISMATCH, "capabilityFingerprint", f"compute-failed:{type(exc).__name__}"))
        else:
            if recomputed != entry["capabilityFingerprint"]:
                reasons.append(_reason(FINGERPRINT_MISMATCH, "capabilityFingerprint"))

    return sorted(set(reasons))


def is_malformed(
    entry: Any,
    *,
    fingerprint_fn: Callable[[dict], str] | None = None,
    known_evidence_ids: Iterable[str] | None = None,
) -> bool:
    """Malformed_Entry 여부. `True`면 activation 입력에서 제외한다(Requirement 3.17)."""
    return bool(validate_entry(entry, fingerprint_fn=fingerprint_fn, known_evidence_ids=known_evidence_ids))


def validate_verification_record(record: Any, *, path: str = "record") -> list[str]:
    """Verification_Record의 필수 필드·타입·enum·시각 형식을 검사한다."""
    if not _is_dict(record):
        return [_reason(TYPE_MISMATCH, path, "not-object")]
    reasons: list[str] = []
    for field in REQUIRED_VERIFICATION_RECORD_FIELDS:
        if field not in record:
            reasons.append(_reason(MISSING_FIELD, f"{path}.{field}"))

    for field in (
        "evidenceRecordId",
        "runId",
        "revision",
        "interpreterPath",
        "candidateLabel",
        "modelId",
        "provider",
        "catalogFingerprint",
        "capabilityFingerprint",
    ):
        if field in record and not _is_str(record[field]):
            reasons.append(_reason(TYPE_MISMATCH, f"{path}.{field}"))
    if "invocationModelIds" in record and not _is_str_list(record["invocationModelIds"]):
        reasons.append(_reason(TYPE_MISMATCH, f"{path}.invocationModelIds"))
    if "verifiedAt" in record and not _time_field_ok(record["verifiedAt"], allow_undetermined=False):
        reasons.append(_reason(TYPE_MISMATCH, f"{path}.verifiedAt", "not-utc-iso8601"))
    if "routeCompleteness" in record and not _is_bool(record["routeCompleteness"]):
        reasons.append(_reason(TYPE_MISMATCH, f"{path}.routeCompleteness"))

    environment = record.get("environment")
    if "environment" in record:
        if not _is_dict(environment):
            reasons.append(_reason(TYPE_MISMATCH, f"{path}.environment"))
        else:
            for field in REQUIRED_ENVIRONMENT_FIELDS:
                if field not in environment:
                    reasons.append(_reason(MISSING_FIELD, f"{path}.environment.{field}"))
                elif not _is_str(environment[field]):
                    reasons.append(_reason(TYPE_MISMATCH, f"{path}.environment.{field}"))

    # usage / cost는 Gateway 제공 dict 또는 `notProvided` 문자열만 허용한다.
    for field in ("usage", "cost"):
        if field in record and not (_is_dict(record[field]) or record[field] == NOT_PROVIDED):
            reasons.append(_reason(TYPE_MISMATCH, f"{path}.{field}", "dict-or-notProvided"))

    if "routeResults" in record:
        reasons.extend(_validate_result_list(record["routeResults"], path=f"{path}.routeResults", kind="route"))
    if "effortResults" in record:
        reasons.extend(_validate_result_list(record["effortResults"], path=f"{path}.effortResults", kind="effort"))
    return sorted(set(reasons))


def _validate_result_list(items: Any, *, path: str, kind: str) -> list[str]:
    if not _is_list(items):
        return [_reason(TYPE_MISMATCH, path)]
    required = REQUIRED_ROUTE_RESULT_FIELDS if kind == "route" else REQUIRED_EFFORT_RESULT_FIELDS
    bool_fields = (
        ("http", "validOutput", "terminalSuccess", "correctionUsed")
        if kind == "route"
        else ("baselineSucceeded",)
    )
    status_enum = Route_Support_Status if kind == "route" else Effort_Support_Status
    reasons: list[str] = []
    for index, item in enumerate(items):
        item_path = f"{path}[{index}]"
        if not _is_dict(item):
            reasons.append(_reason(TYPE_MISMATCH, item_path, "not-object"))
            continue
        for field in required:
            if field not in item:
                reasons.append(_reason(MISSING_FIELD, f"{item_path}.{field}"))
        if "routeKey" in item and not Known_Route.has(item["routeKey"]):
            reasons.append(_reason(ENUM_VIOLATION, f"{item_path}.routeKey"))
        if "status" in item and not status_enum.has(item["status"]):
            reasons.append(_reason(ENUM_VIOLATION, f"{item_path}.status"))
        if kind == "route" and "allowlist" in item and not Allowlist_Result.has(item["allowlist"]):
            reasons.append(_reason(ENUM_VIOLATION, f"{item_path}.allowlist"))
        for field in bool_fields:
            if field in item and not _is_bool(item[field]):
                reasons.append(_reason(TYPE_MISMATCH, f"{item_path}.{field}"))
        if "probeId" in item and not _is_str(item["probeId"]):
            reasons.append(_reason(TYPE_MISMATCH, f"{item_path}.probeId"))
        if "sanitizedSchema" in item and not _is_dict(item["sanitizedSchema"]):
            reasons.append(_reason(TYPE_MISMATCH, f"{item_path}.sanitizedSchema"))
        if kind == "effort" and "fieldPath" in item and item["fieldPath"] is not None and not _is_str_list(item["fieldPath"]):
            reasons.append(_reason(TYPE_MISMATCH, f"{item_path}.fieldPath"))
    return reasons
