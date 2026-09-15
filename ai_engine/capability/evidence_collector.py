"""Evidence_Collector — discovery · route/effort probe 오케스트레이션 · Verification_Record.

이 모듈은 Candidate_Label을 **검색 라벨로만** 취급해 다음 세 가지를 수행한다.

1. **discovery** — Same_Gateway_Environment의 Gateway_Catalog(또는 catalog endpoint
   부재 시 엄격히 검증된 Operator_Catalog_Export)가 Candidate_Label과 model record의
   관계를 **명시**한 경우에만 Exact_Model_ID·Provider_String을 문자 변경 없이 기록한다.
2. **route probe** — production Request_Builder로 Minimal_Request를 만들고 기존
   transport로 전송해, production adapter·job polling으로 HTTP 성공·Valid_Output·
   Terminal_Success를 **각각 독립 결과로** 기록한다. 세 조건을 모두 충족한 route만
   `SUPPORTED`가 된다(:meth:`EvidenceCollector.probe_route`).
3. **effort probe** — catalog가 **명시한** effort 선언으로만 후보 Effort_Contract를
   만들고(field path·value type·complete domain), 동일 model·route의 무-effort
   Baseline_Request_Body 성공을 먼저 확인한 뒤, domain이 요구하는 value만 production
   Request_Builder의 주입 seam으로 검증한다(:meth:`EvidenceCollector.probe_effort`).
   요구된 모든 검증이 성공한 경우에만 `SUPPORTED`이며, effort 결과는 route 상태를 담지
   않으므로 base Route_Support_Status를 강등할 경로가 없다.
4. **Verification_Record 생성** — 정제(credential 제거 · raw prompt → Probe_ID ·
   raw body → Sanitized_Schema)된 record를 만들고 Evidence_Record_ID를 계산한다.

route probe의 전송은 언제나 effort selection ``None``으로 수행되므로 생성 body는
Baseline_Request_Body와 동일하다(계약이 지정한 최소 output/token bound만 1회 기록한다 —
Requirement 4.26). effort probe만 production 주입 seam으로 값을 정확히 1회 주입하며, 주입이
관측되지 않은 전송은 애초에 만들지 않는다.

effort probe의 전송 규칙(요약):
  - 계약 결손(field path·value type·complete domain 중 하나라도) → **전송 0건**,
    `UNVERIFIED` 유지(Requirement 5.6).
  - 무-effort baseline 성공 미확인 → **전송 0건**, effort 상태 변경 없음(5.7, 5.8).
  - domain 예산: enum은 광고된 각 value 1회, range는 상·하한이 다르면 두 경계 각 1회,
    같으면 그 경계 1회(5.9~5.11).
  - unknown field → `UNSUPPORTED`(5.14). invalid value → `UNVERIFIED`(5.15).
    transient·부분 검증 → `UNVERIFIED` 유지(5.16, 5.17) — 그 결과는 record에 담지 않아
    이전 상태를 강등하지 않는다.
  - `/invoke`는 production 주입 seam이 없어(body가 모델별 형식·운영자 제공) effort probe를
    전송하지 않는다. 수동 body 조립은 activation evidence가 아니다.

route probe의 전송 규칙(요약 — 자세한 근거는 각 함수 docstring):
  - 광고되지 않은 Known_Route는 `NOT_ADVERTISED`로 기록하고 **전송 0건**이다.
  - 요청 body는 production Request_Builder(`bind_contract` → `EffortBoundClient` builder
    seam)가 만들고, 전송은 기존 transport 메서드가, 판정은 production adapter·job
    polling이 한다. 주입된 transport(mock·진단 코드)로 얻은 결과는
    `evidenceEligible: False`로 표시되어 activation evidence에서 제외된다(4.4).
  - 예산은 :class:`ProbeBudget`이 강제한다 — 조합당 성공 generation 1회, 명시적·교정
    가능 validation error에만 동일 조합 교정 1회, prefix 형태 교정은 동일 route 1회.
  - 실패 분류는 :func:`failure_handler.classify`(Failure_Precedence)만 사용한다.
    transient·empty·partial·timeout은 route 상태를 강등하지 않고 `UNVERIFIED`로 둔다.

값 추론 금지(핵심 불변식):
  - model ID·provider·route 지원 여부·effort field path·effort 허용값을 이 모듈에
    상수로 두지 않는다. 라벨 문자열에서 유도하지도 않는다.
  - 라벨 매칭은 **catalog가 명시한 라벨 field/매핑**만 본다. 표시명(`name`,
    `modelName`, `displayName`)·주석·부분 문자열 유사도는 매칭 근거가 아니다
    (Requirement 1.17).
  - 라벨 간 전파 차단: 라벨마다 독립적으로 catalog를 평가하고, map 반영도
    `candidateLabel` 단위 upsert(:func:`capability_map.upsert`)로만 한다. 한 라벨의
    provider·model family·route·effort가 다른 라벨 entry에 흘러갈 경로가 없다
    (Requirement 2.18).

Authoritative_Evidence 판정:
  - `CATALOG`      Same_Gateway_Environment의 Gateway_Catalog 조회 결과
  - `OPERATOR_EXPORT` 4개 검증(환경 identity·region 일치, UTC 시각 순서,
    Catalog_Fingerprint 재계산 일치, sanitization manifest가 credential 관련 field만
    제거)을 **모두** 통과한 export. 하나라도 실패하면 근거에서 제외한다.
  - `SEED`·mock·과거 사양·표시명·주석은 어떤 경우에도 근거가 아니다.
  - `evidenceEligible: False` baseline(:mod:`.baseline_inspector`)도 근거 집합에서
    제외한다(Requirement 1.16). 다만 baseline 결손은 catalog discovery 자체를 막지
    않으므로 결과의 `notes`에만 기록한다.

Gateway 호출 경로: catalog 조회는 기존 구현을 재사용한다
(`ai_engine.openai_catalog.GatewayListSource(gw, endpoint).list_models()` — 서명·
credential·retry는 전부 :class:`ai_engine.gateway_module.GatewayClient`에 위임).
현재 저장소의 `GatewayListSource`는 미구현 스텁이라 빈 목록을 돌려주고, catalog
endpoint가 구성되지 않으면 조회 자체를 시도하지 않는다. 두 경우 모두 catalog endpoint
부재로 기록되고 Operator_Catalog_Export만 대체 근거가 된다. 신규 URL·신규 서명 코드·
신규 credential 캐시를 만들지 않는다.

참조: .kiro/specs/gateway-models-effort-support/design.md
  - "Components and Interfaces" 2절 (Evidence_Collector)
  - "route별 판정 근거" 표 · "검증 파이프라인" 예산 규칙 · "effort probe 예산" 표
  - "Data Models" → Capability_Map entry · Route_Contract · Effort_Contract ·
    Verification_Record · canonical fingerprint 규칙
Requirements: 1.14, 1.15, 1.16, 1.17, 2.1~2.18, 4.1~4.8, 4.16~4.28, 5.1~5.20,
              8.8, 8.9, 8.10, 10.12, 10.13, 10.14
"""
from __future__ import annotations

import copy
import hashlib
import os
from datetime import datetime
from typing import Any, Callable, Iterable, Mapping, Sequence

from . import (
    canonicalizer,
    capability_map,
    contracts,
    failure_handler,
    request_builder,
    store,
)

# ─────────────────────────────────────────────────────────────────
# 상수
# ─────────────────────────────────────────────────────────────────

#: 이번 기능의 여섯 Candidate_Label(검색 라벨 — model identity가 아니다).
#: 이 문자열에서 model ID·provider·모델 계열·route·effort를 유도하는 코드는 없다.
CANDIDATE_LABELS: tuple[str, ...] = ("opus 5", "sonnet 5", "gpt 5.6", "sol", "terra", "luna")

#: Probe_ID 접두사(비민감 고정 식별자 — raw prompt 자리를 대신한다).
PROBE_ID_PREFIX = "prb1:sha256:"

#: Probe_ID 해시 표현 길이(짧고 결정론적인 식별자).
_PROBE_ID_HEX_LEN = 32

#: 진단 문자열 최대 길이(프로젝트 로깅 관례와 동일하게 200자 절단).
_REASON_MAX = 200

#: Operator_Catalog_Export 스키마 버전(현재 1만 지원).
OPERATOR_EXPORT_SCHEMA_VERSIONS: tuple[int, ...] = (1,)

#: Operator_Catalog_Export 필수 필드.
OPERATOR_EXPORT_REQUIRED_FIELDS: tuple[str, ...] = (
    "schemaVersion",
    "environment",
    "generatedAt",
    "collectedAt",
    "catalogFingerprint",
    "sanitizationManifest",
)

#: Operator_Catalog_Export에서 catalog 본문을 담을 수 있는 키(우선순위 순).
OPERATOR_EXPORT_CATALOG_KEYS: tuple[str, ...] = ("catalog", "models")

#: sanitization manifest에서 제거·변경 field 목록을 담을 수 있는 키(정규화 형태).
_MANIFEST_REMOVED_KEYS: frozenset[str] = frozenset({"removedfields", "removed", "dropped", "droppedfields"})
_MANIFEST_MODIFIED_KEYS: frozenset[str] = frozenset({"modifiedfields", "modified", "changedfields", "changed", "maskedfields", "masked"})

#: catalog snapshot에서 모델 record 목록을 담을 수 있는 키(정규화 형태).
_MODEL_LIST_KEYS: frozenset[str] = frozenset({"models", "entries", "modelsummaries", "data", "items", "catalog"})

#: Exact_Model_ID를 읽는 키(정규화 형태, 우선순위 순).
#: 표시명 키(`name`, `modelName`, `displayName`)는 **포함하지 않는다**(Requirement 1.17).
MODEL_ID_KEYS: tuple[str, ...] = ("modelid", "exactmodelid", "modelidentifier", "id", "model")

#: Invocation_Model_ID를 읽는 키(정규화 형태, 우선순위 순).
#: catalog record가 **명시**한 전송 ID만 읽는다. 값은 문자 그대로 쓰며 접두사를 붙이거나
#: 떼지 않는다(Requirement 2.17 — Exact_Model_ID와 별도 자리).
INVOCATION_MODEL_ID_KEYS: tuple[str, ...] = (
    "invocationmodelid",
    "invocationmodelids",
    "inferenceprofileid",
)

#: Provider_String을 읽는 키(정규화 형태, 우선순위 순).
PROVIDER_KEYS: tuple[str, ...] = ("provider", "providername", "providerid", "vendor", "owner", "ownedby")

#: Candidate_Label 관계를 **명시**하는 record 키(정규화 형태).
#: 표시명·설명·주석 키는 의도적으로 제외한다.
LABEL_FIELD_KEYS: tuple[str, ...] = (
    "candidatelabel",
    "candidatelabels",
    "searchlabel",
    "searchlabels",
    "label",
    "labels",
    "alias",
    "aliases",
)

#: snapshot 최상위에서 `{label: modelId}` 매핑을 담을 수 있는 키(정규화 형태).
LABEL_MAP_KEYS: tuple[str, ...] = ("candidatelabels", "candidatelabelmap", "labelmap", "labels")

#: advertised route를 열거하는 record 키(정규화 형태).
#: 값에서 Known_Route enum 문자열과 **정확히** 일치하는 토큰만 채택한다(추론 없음).
ROUTE_FIELD_KEYS: tuple[str, ...] = (
    "route",
    "routes",
    "routekey",
    "routekeys",
    "advertisedroutes",
    "supportedroutes",
    "knownroutes",
)

#: 라벨 매칭 방식(감사용).
MATCH_EXACT = "EXACT"
MATCH_NORMALIZED_LABEL = "NORMALIZED_LABEL"

# ── discovery 판정 이유 코드(닫힌 집합) ────────────────────────────────────
# catalog 획득 단계
CATALOG_ENDPOINT_ABSENT = "CATALOG_ENDPOINT_ABSENT"
CATALOG_FETCH_FAILED = "CATALOG_FETCH_FAILED"
CATALOG_EMPTY = "CATALOG_EMPTY"
CATALOG_ENVIRONMENT_UNKNOWN = "CATALOG_ENVIRONMENT_UNKNOWN"
CATALOG_ENVIRONMENT_MISMATCH = "CATALOG_ENVIRONMENT_MISMATCH"
CATALOG_SOURCE_NOT_AUTHORITATIVE = "CATALOG_SOURCE_NOT_AUTHORITATIVE"
# Operator_Catalog_Export 검증 단계
EXPORT_SCHEMA_INVALID = "EXPORT_SCHEMA_INVALID"
EXPORT_ENVIRONMENT_UNKNOWN = "EXPORT_ENVIRONMENT_UNKNOWN"
EXPORT_ENVIRONMENT_MISMATCH = "EXPORT_ENVIRONMENT_MISMATCH"
EXPORT_TIME_FORMAT_INVALID = "EXPORT_TIME_FORMAT_INVALID"
EXPORT_TIME_ORDER_INVALID = "EXPORT_TIME_ORDER_INVALID"
EXPORT_FINGERPRINT_MISMATCH = "EXPORT_FINGERPRINT_MISMATCH"
EXPORT_SANITIZATION_NOT_CREDENTIAL_ONLY = "EXPORT_SANITIZATION_NOT_CREDENTIAL_ONLY"
EXPORT_SIGNIFICANT_FIELD_CHANGED = "EXPORT_SIGNIFICANT_FIELD_CHANGED"
# 라벨 매칭 단계
LABEL_ASSOCIATION_ABSENT = "LABEL_ASSOCIATION_ABSENT"
LABEL_ASSOCIATION_AMBIGUOUS = "LABEL_ASSOCIATION_AMBIGUOUS"
MODEL_ID_ABSENT = "MODEL_ID_ABSENT"
PROVIDER_ABSENT = "PROVIDER_ABSENT"

DISCOVERY_REASONS: tuple[str, ...] = (
    CATALOG_ENDPOINT_ABSENT,
    CATALOG_FETCH_FAILED,
    CATALOG_EMPTY,
    CATALOG_ENVIRONMENT_UNKNOWN,
    CATALOG_ENVIRONMENT_MISMATCH,
    CATALOG_SOURCE_NOT_AUTHORITATIVE,
    EXPORT_SCHEMA_INVALID,
    EXPORT_ENVIRONMENT_UNKNOWN,
    EXPORT_ENVIRONMENT_MISMATCH,
    EXPORT_TIME_FORMAT_INVALID,
    EXPORT_TIME_ORDER_INVALID,
    EXPORT_FINGERPRINT_MISMATCH,
    EXPORT_SANITIZATION_NOT_CREDENTIAL_ONLY,
    EXPORT_SIGNIFICANT_FIELD_CHANGED,
    LABEL_ASSOCIATION_ABSENT,
    LABEL_ASSOCIATION_AMBIGUOUS,
    MODEL_ID_ABSENT,
    PROVIDER_ABSENT,
)

#: 비차단 note 코드(근거 판정에는 영향을 주지 않지만 보고서에 남긴다).
NOTE_BASELINE_EXCLUDED = "BASELINE_EXCLUDED"
NOTE_CATALOG_EMPTY_TREATED_AS_ABSENT = "CATALOG_EMPTY_TREATED_AS_ABSENT"
NOTE_OPERATOR_EXPORT_USED = "OPERATOR_EXPORT_USED"

#: Sanitized_Schema 하위 트리로 취급하는 키(정규화 형태).
#: 이 하위에서는 credential 키 제거만 하고 prompt/body 키 재작성을 하지 않는다
#: (schema는 이미 field name·type·cardinality만 담고 있어 원문이 없다).
_SCHEMA_SUBTREE_KEYS: frozenset[str] = frozenset({"sanitizedschema", "recordschema"})

#: :func:`is_catalog_significant_field` 판정에 쓰는 anchor field
#: (canonicalizer의 "모르는 catalog 형태" 폴백 경로를 타지 않게 한다).
_SIGNIFICANT_PROBE_ANCHOR = "modelId"

#: route/effort 결과의 기본값 — 판정되지 않은 자리는 보수적으로 미확정·실패로 둔다.
_ROUTE_RESULT_DEFAULTS: dict[str, Any] = {
    "http": False,
    "validOutput": False,
    "terminalSuccess": False,
    "allowlist": str(contracts.Allowlist_Result.UNVERIFIED),
    "status": str(contracts.Route_Support_Status.UNVERIFIED),
    "correctionUsed": False,
}
_EFFORT_RESULT_DEFAULTS: dict[str, Any] = {
    "fieldPath": None,
    "value": None,
    "status": str(contracts.Effort_Support_Status.UNVERIFIED),
    "baselineSucceeded": False,
}


# ─────────────────────────────────────────────────────────────────
# 예외
# ─────────────────────────────────────────────────────────────────
class EvidenceCollectorError(Exception):
    """Evidence_Collector 계약 위반."""


class InvalidVerificationRecordError(EvidenceCollectorError):
    """생성한 Verification_Record가 스키마를 만족하지 않는다.

    ``reasons``는 ``contracts`` 판정 이유(``CODE:path(detail)``) 목록이다.
    """

    def __init__(self, reasons: Sequence[str]) -> None:
        self.reasons = list(reasons)
        super().__init__("Verification_Record 스키마 위반: " + ", ".join(self.reasons)[:_REASON_MAX])


class SanitizationError(EvidenceCollectorError):
    """정제되지 않은 값을 영속화하려 했다(credential·raw prompt·raw body 잔존)."""

    def __init__(self, violations: Sequence[str]) -> None:
        self.violations = list(violations)
        super().__init__("정제 위반: " + ", ".join(self.violations)[:_REASON_MAX])


# ─────────────────────────────────────────────────────────────────
# 작은 유틸
# ─────────────────────────────────────────────────────────────────
def _is_dict(value: Any) -> bool:
    return isinstance(value, dict)


def _text(value: Any) -> str:
    """문자열 필드를 안전하게 읽는다(문자열이 아니면 미확정으로 취급)."""
    return value if isinstance(value, str) else contracts.UNDETERMINED


def _truncate(value: Any) -> str:
    text = value if isinstance(value, str) else str(value)
    return text[:_REASON_MAX]


def _normalize_key(key: Any) -> str:
    """키를 소문자 영숫자만 남긴 형태로 정규화한다(``Model-Id`` → ``modelid``)."""
    text = key if isinstance(key, str) else str(key)
    return "".join(char for char in text.lower() if char.isalnum())


def normalize_label(label: Any) -> str:
    """라벨 비교용 정규화 — casefold + 내부 공백 축약.

    **라벨끼리 비교할 때만** 쓴다. 이 함수는 라벨에서 model identity·provider·route·
    effort를 만들지 않으며, 정규화 결과가 저장되는 곳도 없다(Exact_Model_ID와
    Provider_String은 언제나 catalog 원문 그대로 기록한다).
    """
    text = label if isinstance(label, str) else ("" if label is None else str(label))
    return " ".join(text.split()).casefold()


def _instant(value: Any) -> float | None:
    """UTC ISO 8601 문자열을 epoch 초로 바꾼다(형식 위반·빈 문자열은 ``None``)."""
    if not contracts.is_utc_iso8601(value):
        return None
    try:
        return datetime.fromisoformat(value).timestamp()
    except ValueError:  # pragma: no cover - is_utc_iso8601이 선차단한다
        return None


def _pick(record: Mapping[str, Any], normalized_keys: Sequence[str]) -> tuple[str, Any] | None:
    """정규화 키 우선순위로 record에서 (원본 키, 값)을 찾는다(없으면 ``None``)."""
    index: dict[str, tuple[str, Any]] = {}
    for key, value in record.items():
        norm = _normalize_key(key)
        index.setdefault(norm, (key if isinstance(key, str) else str(key), value))
    for wanted in normalized_keys:
        if wanted in index:
            return index[wanted]
    return None


def probe_id(*parts: Any) -> str:
    """비민감 고정 Probe_ID(``prb1:sha256:<hex>``)를 만든다.

    입력은 **비민감 식별자만** 넘긴다(예: model ID, route key, effort value, 순번).
    prompt 원문·request body·credential을 넘기면 안 된다 — Probe_ID는 그 원문을
    기록하지 않기 위한 대체 식별자다(Requirement 10.13).
    """
    digest = hashlib.sha256(canonicalizer.canonical_bytes(list(parts))).hexdigest()
    return PROBE_ID_PREFIX + digest[:_PROBE_ID_HEX_LEN]


# ─────────────────────────────────────────────────────────────────
# 정제 보조 — credential 키만 제거(prompt/body 키는 건드리지 않는다)
# ─────────────────────────────────────────────────────────────────
def strip_credential_fields(value: Any, *, _depth: int = 0) -> Any:
    """중첩 위치를 포함해 credential·authorization·cookie·signature 키를 제거한다.

    :func:`store.classify_key`가 ``DROP``으로 분류한 키만 제거하므로, 이미 파생된
    Sanitized_Schema의 field 이름(``input``·``messages`` 등)을 재작성하지 않는다.
    """
    if _depth > 16:
        return store.sanitized_schema(value)
    if isinstance(value, dict):
        return {
            key: strip_credential_fields(item, _depth=_depth + 1)
            for key, item in value.items()
            if store.classify_key(key) != "DROP"
        }
    if isinstance(value, (list, tuple)):
        return [strip_credential_fields(item, _depth=_depth + 1) for item in value]
    return value


def sanitization_violations(value: Any, *, path: str = "", _in_schema: bool = False) -> list[str]:
    """정제 위반 경로 목록을 반환한다(빈 목록이면 영속화해도 안전하다).

    검사 항목:
      - credential·authorization·cookie·signature 키 잔존 → ``credential-field:<path>``
      - raw prompt 잔존(Probe_ID가 아닌 값) → ``raw-prompt:<path>``
      - raw body 잔존(Sanitized_Schema가 아닌 값) → ``raw-body:<path>``

    Sanitized_Schema 하위 트리(:data:`_SCHEMA_SUBTREE_KEYS`)에서는 credential 키만
    검사한다. schema는 field name·type·cardinality만 담으므로 그 안의 ``input``·
    ``messages``는 원문이 아니라 field 이름이다.
    """
    violations: list[str] = []
    if isinstance(value, dict):
        for key, item in value.items():
            child = f"{path}.{key}" if path else str(key)
            kind = store.classify_key(key)
            if kind == "DROP":
                violations.append(f"credential-field:{child}")
                continue
            in_schema = _in_schema or _normalize_key(key) in _SCHEMA_SUBTREE_KEYS
            if not _in_schema and kind == "PROMPT" and not _is_probe_reference(item):
                violations.append(f"raw-prompt:{child}")
                continue
            if not _in_schema and kind == "BODY" and not _is_schema_value(item):
                violations.append(f"raw-body:{child}")
                continue
            violations.extend(sanitization_violations(item, path=child, _in_schema=in_schema))
        return violations
    if isinstance(value, (list, tuple)):
        for index, item in enumerate(value):
            violations.extend(
                sanitization_violations(item, path=f"{path}[{index}]", _in_schema=_in_schema)
            )
    return violations


def _is_probe_reference(value: Any) -> bool:
    """raw prompt 자리에 Probe_ID(또는 store sentinel)만 남았는지."""
    return isinstance(value, str) and (
        value.startswith(PROBE_ID_PREFIX) or value == store.PROBE_ID_UNKNOWN
    )


def _is_schema_value(value: Any) -> bool:
    """raw body 자리에 Sanitized_Schema(``{"type": ...}``)만 남았는지."""
    return _is_dict(value) and isinstance(value.get("type"), str)


# ─────────────────────────────────────────────────────────────────
# catalog snapshot 읽기 — 라벨 관계는 catalog가 "명시"해야 한다
# ─────────────────────────────────────────────────────────────────
def catalog_records(snapshot: Any) -> list[dict]:
    """catalog snapshot에서 모델 record 목록을 뽑는다(형식 판별만 — 값 변경 없음).

    지원 형태: record 목록, ``{"models": [...]}`` 계열 dict, ``{modelId: record}``
    매핑, 단일 record dict. ``{modelId: record}`` 매핑은 키가 identity이므로 record에
    없을 때만 ``modelId``로 채워 넣는다(문자 변경 없이 그대로).
    """
    if snapshot is None:
        return []
    if isinstance(snapshot, (list, tuple)):
        return [dict(item) for item in snapshot if _is_dict(item)]
    if not _is_dict(snapshot):
        return []

    for key, value in snapshot.items():
        if _normalize_key(key) in _MODEL_LIST_KEYS and isinstance(value, (list, tuple)):
            return [dict(item) for item in value if _is_dict(item)]

    values = list(snapshot.values())
    if values and all(_is_dict(item) for item in values):
        records: list[dict] = []
        for key, record in snapshot.items():
            item = dict(record)
            if _pick(item, MODEL_ID_KEYS) is None and isinstance(key, str):
                item = {"modelId": key, **item}
            records.append(item)
        return records
    return [dict(snapshot)]


def record_model_id(record: Mapping[str, Any]) -> str:
    """record의 Exact_Model_ID를 **문자 변경 없이** 읽는다(없으면 미확정)."""
    found = _pick(record, MODEL_ID_KEYS)
    if found is None:
        return contracts.UNDETERMINED
    _, value = found
    return value if isinstance(value, str) and value else contracts.UNDETERMINED


def record_invocation_model_id(record: Mapping[str, Any]) -> str:
    """record가 **명시한** Invocation_Model_ID를 문자 변경 없이 읽는다(없으면 미확정).

    이 값은 production 요청 경로가 실제 전송에 쓰는 ID(예: control-plane이 반환한
    inference profile ID)를 catalog가 알려주는 자리다. probe는 이 값을 전송 ID로 쓰고
    Exact_Model_ID는 identity로 그대로 유지한다(Requirement 2.17).

    목록 형태(`invocationModelIds`)는 **서로 다른 값이 둘 이상이면 미확정**으로 둔다 —
    어느 것을 보낼지 추측하지 않는다(값 추론 금지).
    """
    found = _pick(record, INVOCATION_MODEL_ID_KEYS)
    if found is None:
        return contracts.UNDETERMINED
    _, value = found
    if isinstance(value, str):
        return value if value else contracts.UNDETERMINED
    if isinstance(value, (list, tuple)):
        unique = {item for item in value if isinstance(item, str) and item}
        if len(unique) == 1:
            return next(iter(unique))
    return contracts.UNDETERMINED


def record_provider(record: Mapping[str, Any]) -> str:
    """record의 Provider_String을 **문자 변경 없이** 읽는다(없으면 미확정)."""
    found = _pick(record, PROVIDER_KEYS)
    if found is None:
        return contracts.UNDETERMINED
    _, value = found
    return value if isinstance(value, str) and value else contracts.UNDETERMINED


def record_labels(record: Mapping[str, Any]) -> list[str]:
    """record가 **명시한** Candidate_Label 문자열 목록.

    :data:`LABEL_FIELD_KEYS`에 해당하는 키의 문자열 값만 읽는다. 표시명·설명 필드는
    읽지 않으므로 표시명만으로는 라벨 관계가 성립하지 않는다(Requirement 1.17).
    """
    labels: list[str] = []
    for key, value in record.items():
        if _normalize_key(key) not in LABEL_FIELD_KEYS:
            continue
        if isinstance(value, str):
            if value:
                labels.append(value)
        elif isinstance(value, (list, tuple)):
            labels.extend(item for item in value if isinstance(item, str) and item)
    return labels


def record_advertised_routes(record: Mapping[str, Any]) -> list[str]:
    """record가 광고한 Known_Route 목록(Known_Route 순서).

    :data:`ROUTE_FIELD_KEYS` 키의 값에서 Known_Route enum 문자열과 **정확히** 같은
    토큰만 채택한다. endpoint 문자열·provider·라벨에서 route를 추론하지 않으므로,
    광고되지 않은 route는 후속 orchestration이 `NOT_ADVERTISED`로 다룰 수 있다.
    """
    tokens: set[str] = set()
    for key, value in record.items():
        if _normalize_key(key) not in ROUTE_FIELD_KEYS:
            continue
        candidates: list[Any] = []
        if isinstance(value, str):
            candidates = [value]
        elif isinstance(value, (list, tuple)):
            candidates = list(value)
        elif _is_dict(value):
            candidates = list(value.keys())
        for item in candidates:
            if contracts.Known_Route.has(item):
                tokens.add(str(item))
    return [route for route in contracts.KNOWN_ROUTES if route in tokens]


def label_map_entries(snapshot: Any) -> dict[str, list[str]]:
    """snapshot 최상위의 ``{label: modelId}`` 매핑을 `{label: [modelId, ...]}`로 읽는다.

    catalog가 라벨 관계를 record 밖 매핑으로 제공하는 형태를 지원한다. 값은 문자열
    또는 문자열 목록만 인정하며, 어떤 문자도 변경하지 않는다.
    """
    if not _is_dict(snapshot):
        return {}
    out: dict[str, list[str]] = {}
    for key, value in snapshot.items():
        if _normalize_key(key) not in LABEL_MAP_KEYS or not _is_dict(value):
            continue
        for label, target in value.items():
            if not isinstance(label, str) or not label:
                continue
            if isinstance(target, str) and target:
                out.setdefault(label, []).append(target)
            elif isinstance(target, (list, tuple)):
                out.setdefault(label, []).extend(
                    item for item in target if isinstance(item, str) and item
                )
    return out


def find_label_records(snapshot: Any, label: str) -> tuple[list[dict], str | None]:
    """catalog가 해당 Candidate_Label과 명시적으로 연결한 record 목록을 찾는다.

    Returns:
        ``(records, matchKind)``. ``records``가 비면 관계가 명시되지 않은 것이며
        ``matchKind``는 ``None``이다. ``matchKind``는 원문 일치(:data:`MATCH_EXACT`)
        또는 라벨 정규화 일치(:data:`MATCH_NORMALIZED_LABEL`)를 뜻한다.

    부분 문자열·유사도 매칭은 하지 않는다. 라벨 정규화는 대소문자와 공백 축약만
    적용하는 **라벨 대 라벨** 비교이며, 라벨에서 model identity를 만들지 않는다.
    """
    records = catalog_records(snapshot)
    if not records or not isinstance(label, str) or not label:
        return [], None

    wanted_norm = normalize_label(label)
    mapping = label_map_entries(snapshot)

    exact: list[dict] = []
    normalized: list[dict] = []

    # 1) record가 직접 명시한 라벨
    for record in records:
        labels = record_labels(record)
        if any(item == label for item in labels):
            exact.append(record)
        elif any(normalize_label(item) == wanted_norm for item in labels):
            normalized.append(record)

    # 2) snapshot 최상위 `{label: modelId}` 매핑
    exact_ids: set[str] = set()
    normalized_ids: set[str] = set()
    for map_label, model_ids in mapping.items():
        if map_label == label:
            exact_ids.update(model_ids)
        elif normalize_label(map_label) == wanted_norm:
            normalized_ids.update(model_ids)
    if exact_ids or normalized_ids:
        for record in records:
            model_id = record_model_id(record)
            if not model_id:
                continue
            if model_id in exact_ids and record not in exact:
                exact.append(record)
            elif model_id in normalized_ids and record not in normalized:
                normalized.append(record)

    if exact:
        return exact, MATCH_EXACT
    if normalized:
        return normalized, MATCH_NORMALIZED_LABEL
    return [], None


# ─────────────────────────────────────────────────────────────────
# Same_Gateway_Environment identity
# ─────────────────────────────────────────────────────────────────
def environment_identity(
    gw: Any = None,
    *,
    gateway_environment_id: str = contracts.UNDETERMINED,
    endpoint_identity: str = contracts.UNDETERMINED,
    region: str = contracts.UNDETERMINED,
    env: Mapping[str, str] | None = None,
) -> dict:
    """Same_Gateway_Environment identity(`{gatewayEnvironmentId, endpointIdentity, region}`).

    endpoint identity와 region은 기존 client 인스턴스(`gw.gateway_url`, `gw.region`)에서
    읽는다. 신규 URL을 하드코딩하지 않는다. `gatewayEnvironmentId`는 운영자가 지정하는
    값이며 인자 또는 환경변수 ``AE_GATEWAY_ENV_ID``로만 들어온다(추측하지 않는다).
    """
    env = env if env is not None else os.environ
    endpoint = _text(endpoint_identity)
    if not endpoint and gw is not None:
        endpoint = _text(getattr(gw, "gateway_url", contracts.UNDETERMINED))
    region_value = _text(region)
    if not region_value and gw is not None:
        region_value = _text(getattr(gw, "region", contracts.UNDETERMINED))
    env_id = _text(gateway_environment_id) or _text(env.get("AE_GATEWAY_ENV_ID"))
    return {
        "gatewayEnvironmentId": env_id,
        "endpointIdentity": endpoint,
        "region": region_value,
    }


def environment_complete(identity: Any) -> bool:
    """identity의 3개 필수 필드가 모두 채워졌는지."""
    if not _is_dict(identity):
        return False
    return all(_text(identity.get(field)) for field in contracts.REQUIRED_ENVIRONMENT_FIELDS)


def environment_matches(left: Any, right: Any) -> bool:
    """두 environment identity가 3개 필드 모두 정확히 일치하는지."""
    if not environment_complete(left) or not environment_complete(right):
        return False
    return all(
        _text(left.get(field)) == _text(right.get(field))
        for field in contracts.REQUIRED_ENVIRONMENT_FIELDS
    )


# ─────────────────────────────────────────────────────────────────
# Operator_Catalog_Export 검증 (Requirement 2.5~2.11)
# ─────────────────────────────────────────────────────────────────
def is_catalog_significant_field(name: Any) -> bool:
    """이름이 catalog identity·provider·advertised route·capability field인지 판정한다.

    판정은 :func:`canonicalizer.catalog_model_views`의 Catalog_Fingerprint 입력 분류를
    그대로 재사용한다(동일 분류 목록을 이 모듈에 복제하지 않는다). anchor field를 함께
    넣어 canonicalizer의 "모르는 catalog 형태" 폴백 경로를 타지 않게 한다.
    """
    key = name if isinstance(name, str) else ("" if name is None else str(name))
    if not key:
        return False
    if _normalize_key(key) == _normalize_key(_SIGNIFICANT_PROBE_ANCHOR):
        return True
    views = canonicalizer.catalog_model_views([{_SIGNIFICANT_PROBE_ANCHOR: None, key: None}])
    view = views[0] if views else {}
    return _is_dict(view) and key in view


def manifest_field_names(manifest: Any) -> tuple[list[str], list[str]]:
    """sanitization manifest에서 (제거 field, 변경 field) 이름 목록을 읽는다."""
    removed: list[str] = []
    modified: list[str] = []
    if not _is_dict(manifest):
        return removed, modified
    for key, value in manifest.items():
        norm = _normalize_key(key)
        names: list[str] = []
        if isinstance(value, str):
            names = [value]
        elif isinstance(value, (list, tuple)):
            names = [item for item in value if isinstance(item, str)]
        elif _is_dict(value):
            names = [item for item in value.keys() if isinstance(item, str)]
        if norm in _MANIFEST_REMOVED_KEYS:
            removed.extend(names)
        elif norm in _MANIFEST_MODIFIED_KEYS:
            modified.extend(names)
    return removed, modified


def validate_operator_catalog_export(export: Any, environment: Any) -> dict:
    """Operator_Catalog_Export를 4개 조건으로 검증한다(Requirement 2.6~2.10).

    Args:
        export: 운영자 export 구조(모듈 docstring의 스키마).
        environment: 검증 대상 Same_Gateway_Environment identity.

    Returns:
        ``{"valid": bool, "reasons": [코드...], "snapshot": Any|None,
        "catalogFingerprint": str, "environment": dict|None,
        "generatedAt": str, "collectedAt": str}``

    검증 조건(하나라도 실패하면 ``valid == False`` — 근거에서 제외한다):
      1. export의 environment identity·region이 Same_Gateway_Environment와 정확히 일치
      2. 생성·수집 시각이 UTC ISO 8601이고 생성 시각이 수집 시각보다 늦지 않음
      3. export 내용에서 재계산한 Catalog_Fingerprint가 제공된 값과 일치
      4. sanitization manifest가 credential 관련 field만 제거·변경했고, model identity·
         provider·advertised route·capability field는 건드리지 않음
    """
    reasons: list[str] = []
    result: dict[str, Any] = {
        "valid": False,
        "reasons": reasons,
        "snapshot": None,
        "catalogFingerprint": contracts.UNDETERMINED,
        "environment": None,
        "generatedAt": contracts.UNDETERMINED,
        "collectedAt": contracts.UNDETERMINED,
    }
    if not _is_dict(export):
        reasons.append(EXPORT_SCHEMA_INVALID)
        return result

    # 스키마 — 필수 필드와 catalog 본문 존재
    for field in OPERATOR_EXPORT_REQUIRED_FIELDS:
        if field not in export:
            reasons.append(EXPORT_SCHEMA_INVALID)
            break
    version = export.get("schemaVersion")
    if isinstance(version, bool) or not isinstance(version, int) or version not in OPERATOR_EXPORT_SCHEMA_VERSIONS:
        reasons.append(EXPORT_SCHEMA_INVALID)

    payload = None
    payload_key = None
    for key in OPERATOR_EXPORT_CATALOG_KEYS:
        if key in export:
            payload, payload_key = export[key], key
            break
    if payload_key is None:
        reasons.append(EXPORT_SCHEMA_INVALID)
    result["snapshot"] = payload

    # 1) environment identity·region 정확 일치
    export_env = export.get("environment")
    result["environment"] = dict(export_env) if _is_dict(export_env) else None
    if not environment_complete(export_env):
        reasons.append(EXPORT_ENVIRONMENT_UNKNOWN)
    elif not environment_complete(environment):
        reasons.append(EXPORT_ENVIRONMENT_UNKNOWN)
    elif not environment_matches(export_env, environment):
        reasons.append(EXPORT_ENVIRONMENT_MISMATCH)

    # 2) UTC ISO 8601 시각과 순서(생성 ≤ 수집)
    generated_at = export.get("generatedAt")
    collected_at = export.get("collectedAt")
    result["generatedAt"] = _text(generated_at)
    result["collectedAt"] = _text(collected_at)
    generated = _instant(generated_at)
    collected = _instant(collected_at)
    if generated is None or collected is None:
        reasons.append(EXPORT_TIME_FORMAT_INVALID)
    elif generated > collected:
        reasons.append(EXPORT_TIME_ORDER_INVALID)

    # 3) Catalog_Fingerprint 재계산 일치
    declared = _text(export.get("catalogFingerprint"))
    result["catalogFingerprint"] = declared
    if payload_key is not None:
        try:
            recomputed = canonicalizer.catalog_fingerprint(payload)
        except canonicalizer.MalformedEntryError:
            reasons.append(EXPORT_SCHEMA_INVALID)
        else:
            if not declared or declared != recomputed:
                reasons.append(EXPORT_FINGERPRINT_MISMATCH)

    # 4) sanitization manifest — credential 관련 field만 제거·변경
    manifest = export.get("sanitizationManifest")
    if not _is_dict(manifest):
        reasons.append(EXPORT_SCHEMA_INVALID)
    else:
        removed, modified = manifest_field_names(manifest)
        for name in list(removed) + list(modified):
            if store.classify_key(name) != "DROP":
                reasons.append(EXPORT_SANITIZATION_NOT_CREDENTIAL_ONLY)
                break
        for name in list(removed) + list(modified):
            if is_catalog_significant_field(name):
                reasons.append(EXPORT_SIGNIFICANT_FIELD_CHANGED)
                break

    result["reasons"] = sorted(set(reasons))
    result["valid"] = not result["reasons"]
    return result


# ═════════════════════════════════════════════════════════════════
# route probe — production 경로 결속 · 예산 · 판정
#
# 이 절은 Known_Route별로 "무엇이 요청을 만들고, 무엇이 Valid_Output을 판정하고, 무엇이
# Terminal_Success를 판정하는가"를 design.md "route별 판정 근거" 표의 **기존 심볼로만**
# 연결한다. 어떤 route가 지원되는지는 이 표가 정하지 않는다 — HTTP 성공·Valid_Output·
# Terminal_Success **세 조건을 모두** 충족한 production probe만 route를 `SUPPORTED`로
# 만든다(Requirement 4.5~4.8).
# ═════════════════════════════════════════════════════════════════

#: Minimal_Request의 고정 짧은 비민감 입력. 원문은 어디에도 기록하지 않고 Probe_ID로만 남긴다.
PROBE_INPUT_TEXT = "ping"

#: Minimal_Request의 system 지시문 — 쓰지 않는다(baseline body를 최소 형태로 유지).
PROBE_SYSTEM_PROMPT = ""

#: route probe의 effort selection — 언제나 미선택이다(effort probe는 작업 11.3).
#: 따라서 생성 body는 Baseline_Request_Body + 계약 최소 bound뿐이다.
PROBE_EFFORT_SELECTION = None

#: 조합 키의 effort 자리(∅). 조합 단위는 `(Exact_Model_ID, Known_Route, effort value)`이며
#: effort 미주입 baseline은 하나의 조합이다(design "성공 generation은 조합당 1회").
EFFORT_VALUE_NONE = None

#: prefix 형태 교정의 수행 주체. 기존 transport에 내부 폴백이 있으면 `TRANSPORT`이며
#: 그 route에서는 collector가 추가 교정을 전송하지 않는다(동일 route 최대 1회 — 2.16).
PREFIX_OWNER_TRANSPORT = "TRANSPORT"
PREFIX_OWNER_COLLECTOR = "COLLECTOR"

#: 차단 이유 없음(전송 허용). Activation_Gate·Request_Router와 같은 어휘를 쓴다.
PROBE_OK = request_builder.REASON_OK

#: 상태를 바꾸지 않는 실패 범주(design "상태 보존 불변식" 표를 그대로 재사용한다).
#: 이 범주로 끝난 probe 결과는 Verification_Record에 담지 않는다 — 담으면 일시 오류가
#: 이전에 `SUPPORTED`였던 route를 강등시킨다(Requirement 4.20~4.23).
STATE_PRESERVING_CATEGORIES: tuple[str, ...] = failure_handler.STATE_PRESERVING_CATEGORIES

#: SSE probe가 수집하는 최대 이벤트 수(terminal event를 만나면 그 전에 멈춘다).
SSE_MAX_EVENTS = 64

# ── probe 차단·판정 이유 코드(닫힌 집합) ──────────────────────────────────
PROBE_ROUTE_UNKNOWN = "PROBE_ROUTE_UNKNOWN"
PROBE_NOT_ADVERTISED = "PROBE_NOT_ADVERTISED"
PROBE_MODEL_ID_ABSENT = "PROBE_MODEL_ID_ABSENT"
PROBE_CONTRACT_INCOMPLETE = "PROBE_CONTRACT_INCOMPLETE"
PROBE_INPUT_UNAVAILABLE = "PROBE_INPUT_UNAVAILABLE"
PROBE_BUDGET_SUCCESS_USED = "PROBE_BUDGET_SUCCESS_USED"
PROBE_BUDGET_TRANSMISSION_LIMIT = "PROBE_BUDGET_TRANSMISSION_LIMIT"
PROBE_TRANSPORT_UNAVAILABLE = "PROBE_TRANSPORT_UNAVAILABLE"
PROBE_TRANSPORT_METHOD_ABSENT = "PROBE_TRANSPORT_METHOD_ABSENT"

PROBE_BLOCK_REASONS: tuple[str, ...] = (
    PROBE_ROUTE_UNKNOWN,
    PROBE_NOT_ADVERTISED,
    PROBE_MODEL_ID_ABSENT,
    PROBE_CONTRACT_INCOMPLETE,
    PROBE_INPUT_UNAVAILABLE,
    PROBE_BUDGET_SUCCESS_USED,
    PROBE_BUDGET_TRANSMISSION_LIMIT,
    PROBE_TRANSPORT_UNAVAILABLE,
    PROBE_TRANSPORT_METHOD_ABSENT,
)

# ── 비차단 note 코드(판정에는 영향 없음 — 보고서 기록용) ───────────────────
NOTE_NON_PRODUCTION_TRANSPORT = "NON_PRODUCTION_TRANSPORT"
NOTE_PREFIX_CORRECTION_DELEGATED = "PREFIX_CORRECTION_DELEGATED"
NOTE_PREFIX_CORRECTION_APPLIED = "PREFIX_CORRECTION_APPLIED"
NOTE_MIN_OUTPUT_BOUND_DROPPED = "MIN_OUTPUT_BOUND_DROPPED"
NOTE_REQUEST_PREVIEW_UNAVAILABLE = "REQUEST_PREVIEW_UNAVAILABLE"
NOTE_MODEL_ID_PATH_UNVERIFIED = "MODEL_ID_PATH_UNVERIFIED"
NOTE_MESSAGE_PATH_UNVERIFIED = "MESSAGE_PATH_UNVERIFIED"
NOTE_MODEL_ID_BINDING_OBSERVED = "MODEL_ID_BINDING_OBSERVED"
NOTE_INVOKE_EXTRACTOR_UNAVAILABLE = "INVOKE_EXTRACTOR_UNAVAILABLE"
NOTE_ASYNC_STAGE_FAILURE = "ASYNC_STAGE_FAILURE"
NOTE_CORRECTION_UNAVAILABLE = "CORRECTION_UNAVAILABLE"

#: `/converse` 응답에서 Gateway가 요청을 처리했음을 뜻하는 decision(기존 `converse` 계약).
#: `ERROR`는 전송 계층·HTTP 오류이므로 HTTP 성공이 아니다.
CONVERSE_HTTP_DECISIONS: tuple[str, ...] = ("ALLOW", "ACCEPTED", "DENY")

#: `invoke_model`의 오류 문자열 중 **HTTP 성공 이후 단계**(async 잡)에서 나온 것을 구분하는
#: 신호. 기존 `_poll_invoke_job`이 만드는 문자열이다.
INVOKE_ASYNC_STAGE_SIGNALS: tuple[str, ...] = ("async 잡",)

#: SSE content event 타입(기존 `_converse_stream_live_once`가 파싱하는 이름 그대로).
SSE_CONTENT_EVENT_TYPES: tuple[str, ...] = (
    "content_block_delta",
    "content_block_start",
    "content_block_stop",
)

#: SSE terminal event 타입(동 구현이 종료 신호로 파싱하는 이름 그대로).
SSE_TERMINAL_EVENT_TYPES: tuple[str, ...] = ("message_stop", "settlement")

#: SSE 오류 event 타입.
SSE_ERROR_EVENT_TYPE = "error"

#: SSE event가 실제 전송에 쓰인 model ID를 알려주는 필드 이름.
#:
#: SSE_Stream_Route의 prefix 교정은 transport 내부(`stream_sse_realtime`)에서 일어나므로
#: collector는 자신이 넘긴 ID만 알고 실제 성공 전송 ID는 모른다. 실측 SSE 응답이
#: `{"type": ..., "model_id": ..., "request_id": ...}` 형태로 전송 ID를 되돌려주므로,
#: 그 관측값을 Invocation_Model_ID로 기록한다(Requirement 2.17). 필드 이름은 실측 응답에
#: 있는 이름 그대로이며, 이벤트에 없으면 값을 만들지 않는다.
SSE_MODEL_ID_FIELD = "model_id"

#: `stream_sse_realtime`이 비-200 응답에 담는 접두사(HTTP 실패 판정에만 쓴다).
SSE_HTTP_ERROR_PREFIX = "Lambda HTTP"

#: Known_Route별 production 경로 profile.
#:
#: 값의 출처는 **기존 구현**이다(Baseline_Record). endpoint는 기존 client의
#: `gateway_url` 상대 경로 또는 stream route 식별자이며 신규 URL을 하드코딩하지 않는다.
#: `purposes`·`fallbackRank`는 기존 구현이 그 transport를 쓰는 요청 목적과 기본 우선순위이며
#: 운영자·runner가 `overrides`로 대체할 수 있다. model ID 결속이 미확정인 route
#: (`modelIdRequired: None`)는 성공 probe의 실제 전송 body 관측으로만 확정된다.
#: Known_Route → Execution_Mode 단일 출처(:mod:`.contracts`). profile은 이 매핑을
#: 조회만 하며 mode 값을 따로 들고 있지 않다 — 두 곳에 복제되면 write·read 유도가
#: 다시 갈린다(Capability_Map의 read-time 유도도 같은 매핑을 본다).
_ROUTE_MODE: dict[str, str] = contracts.DEFAULT_ROUTE_EXECUTION_MODES

ROUTE_PROFILES: dict[str, dict[str, Any]] = {
    str(contracts.Known_Route.CONVERSE): {
        "endpointRef": "/converse",
        "httpMethod": "POST",
        "executionMode": _ROUTE_MODE[str(contracts.Known_Route.CONVERSE)],
        "signingService": str(contracts.Signing_Service.EXECUTE_API),
        "modelIdRequired": True,
        "modelIdFieldPath": ["modelId"],
        "observedModelIdFieldPath": ["modelId"],
        "messageFieldPath": ["messages"],
        "inferenceConfigFieldPath": ["inferenceConfig"],
        "optionalFields": ["system", "toolConfig"],
        "requestBuilderRef": "GatewayClient._build_payload",
        "transportRef": "GatewayClient.converse",
        "outputValidatorRef": "GatewayClient.converse:output.message.content",
        "terminalConditionRef": "GatewayClient._poll_job_data",
        "retryPolicyRef": "GatewayClient.converse",
        "prefixCorrectionOwner": PREFIX_OWNER_TRANSPORT,
        "fallbackRank": 0,
        "purposes": ["chat"],
        "minOutputBound": {
            "sourceRef": "GatewayClient.converse_quota_only",
            "fieldPath": ["inferenceConfig"],
            "fields": {"maxTokens": 1},
        },
    },
    str(contracts.Known_Route.INVOKE): {
        "endpointRef": "/invoke",
        "httpMethod": "POST",
        "executionMode": _ROUTE_MODE[str(contracts.Known_Route.INVOKE)],
        "signingService": str(contracts.Signing_Service.EXECUTE_API),
        "modelIdRequired": True,
        "modelIdFieldPath": ["modelId"],
        "observedModelIdFieldPath": ["modelId"],
        "messageFieldPath": ["body"],
        "inferenceConfigFieldPath": None,
        "optionalFields": [],
        "requestBuilderRef": "GatewayClient.invoke_model",
        "transportRef": "GatewayClient.invoke_model",
        "outputValidatorRef": "GatewayClient._extract_invoke_result",
        "terminalConditionRef": "GatewayClient._poll_invoke_job",
        "retryPolicyRef": "GatewayClient.invoke_model",
        "prefixCorrectionOwner": PREFIX_OWNER_COLLECTOR,
        "fallbackRank": 4,
        "purposes": ["image"],
        # 내부 body는 모델별 형식이라 라벨·provider에서 만들 수 없다. 운영자가
        # `probe_inputs`로 최소 body를 주지 않으면 전송하지 않는다(Requirement 4.26 준수).
        "requiresProbeInput": True,
        "minOutputBound": {
            "sourceRef": "GatewayClient.invoke_model",
            "fieldPath": [],
            "fields": {},
        },
    },
    str(contracts.Known_Route.OPENAI_RESPONSES): {
        "endpointRef": "/openai/responses",
        "httpMethod": "POST",
        "executionMode": _ROUTE_MODE[str(contracts.Known_Route.OPENAI_RESPONSES)],
        "signingService": str(contracts.Signing_Service.EXECUTE_API),
        "modelIdRequired": True,
        "modelIdFieldPath": ["model"],
        "observedModelIdFieldPath": ["model"],
        "messageFieldPath": ["input"],
        "inferenceConfigFieldPath": None,
        "optionalFields": ["instructions"],
        "requestBuilderRef": "GatewayClient._build_openai_payload",
        "transportRef": "GatewayClient.openai_responses_call",
        "outputValidatorRef": "openai_adapter.to_converse",
        "terminalConditionRef": "openai_adapter.status_is_completed",
        "retryPolicyRef": "GatewayClient._openai_post_with_retry",
        "prefixCorrectionOwner": PREFIX_OWNER_COLLECTOR,
        "fallbackRank": 1,
        "purposes": ["chat"],
        # 기존 `_build_openai_payload`는 output bound field를 만들지 않는다. 계약이 허용하는
        # 최소값은 "bound field 없음"이며, 실제 bound field는 evidence만 채울 수 있다.
        "minOutputBound": {
            "sourceRef": "GatewayClient._build_openai_payload",
            "fieldPath": [],
            "fields": {},
        },
    },
    str(contracts.Known_Route.OPENAI_RESPONSES_JOBS): {
        "endpointRef": "/openai/responses-jobs",
        "httpMethod": "POST",
        "executionMode": _ROUTE_MODE[str(contracts.Known_Route.OPENAI_RESPONSES_JOBS)],
        "signingService": str(contracts.Signing_Service.EXECUTE_API),
        # 미확정 — jobs route의 model ID 필수 여부와 exact path는 성공 probe로만 확정한다.
        "modelIdRequired": None,
        "modelIdFieldPath": None,
        "observedModelIdFieldPath": ["modelId"],
        "messageFieldPath": ["input"],
        "inferenceConfigFieldPath": None,
        "optionalFields": ["instructions"],
        "requestBuilderRef": "GatewayClient.openai_responses_job_submit",
        "transportRef": "GatewayClient.openai_responses_job_submit",
        "outputValidatorRef": "openai_adapter.to_converse",
        "terminalConditionRef": "GatewayClient._openai_poll_job",
        "retryPolicyRef": "GatewayClient._openai_post_with_retry",
        "prefixCorrectionOwner": PREFIX_OWNER_COLLECTOR,
        "fallbackRank": 2,
        "purposes": ["chat"],
        "minOutputBound": {
            "sourceRef": "GatewayClient._build_openai_payload",
            "fieldPath": [],
            "fields": {},
        },
    },
    str(contracts.Known_Route.SSE_STREAM): {
        "endpointRef": "GatewayClient.STREAM_URL",
        "httpMethod": "POST",
        "executionMode": _ROUTE_MODE[str(contracts.Known_Route.SSE_STREAM)],
        "signingService": str(contracts.Signing_Service.LAMBDA),
        "modelIdRequired": True,
        "modelIdFieldPath": ["modelId"],
        "observedModelIdFieldPath": ["modelId"],
        "messageFieldPath": ["messages"],
        "inferenceConfigFieldPath": ["inferenceConfig"],
        "optionalFields": ["system", "toolConfig"],
        "requestBuilderRef": "GatewayClient._build_payload",
        "transportRef": "GatewayClient.stream_sse_realtime",
        "outputValidatorRef": "GatewayClient.stream_sse_realtime:content_block_delta",
        "terminalConditionRef": "GatewayClient.stream_sse_realtime:message_stop",
        "retryPolicyRef": "GatewayClient.stream_sse_realtime",
        "prefixCorrectionOwner": PREFIX_OWNER_TRANSPORT,
        "fallbackRank": 3,
        "purposes": ["stream"],
        "minOutputBound": {
            "sourceRef": "GatewayClient.converse_quota_only",
            "fieldPath": ["inferenceConfig"],
            "fields": {"maxTokens": 1},
        },
    },
}

_ADAPTER: Any = None


def _openai_adapter():
    """production 응답 adapter를 지연 import한다(Valid_Output 판정의 유일한 근거)."""
    global _ADAPTER
    if _ADAPTER is None:
        from ai_engine import openai_adapter

        _ADAPTER = openai_adapter
    return _ADAPTER


def route_profile(route_key: Any) -> dict | None:
    """Known_Route의 production 경로 profile 복사본(모르는 route는 ``None``)."""
    if not contracts.Known_Route.has(route_key):
        return None
    return copy.deepcopy(ROUTE_PROFILES[str(route_key)])


def route_mode_hints() -> dict:
    """`{routeKey: Execution_Mode}` — 계약에 mode가 없을 때 쓰는 Baseline 힌트.

    값은 :data:`contracts.DEFAULT_ROUTE_EXECUTION_MODES`를 참조하는 profile에서 읽으므로
    Capability_Map의 read-time 유도(hints 없음)와 항상 일치한다. hints를 넘기지 않아도
    같은 결과가 나오지만, 기존 호출자 시그니처를 유지하기 위해 그대로 둔다.
    """
    return {route: str(profile["executionMode"]) for route, profile in ROUTE_PROFILES.items()}


def advertised_routes(model: Any) -> list[str]:
    """discovery 결과가 광고한 Known_Route 목록(Known_Route 순서).

    `advertisedRoutes`가 없으면 빈 목록이다 — 광고되지 않은 route는 `NOT_ADVERTISED`로
    기록되고 전송되지 않는다(Requirement 4.16, 4.17). 라벨·provider·endpoint 문자열에서
    route를 추론하지 않는다.
    """
    if not _is_dict(model):
        return []
    raw = model.get("advertisedRoutes")
    if isinstance(raw, str):
        raw = [raw]
    if not isinstance(raw, (list, tuple, set, frozenset)):
        return []
    tokens = {str(item) for item in raw if contracts.Known_Route.has(item)}
    return [route for route in contracts.KNOWN_ROUTES if route in tokens]


def candidate_route_contract(
    route_key: Any,
    *,
    evidence_ref: str | None = None,
    overrides: Mapping[str, Any] | None = None,
) -> dict | None:
    """probe에 쓸 후보 Route_Contract를 만든다(기존 구현이 아는 값만 채운다).

    endpoint·method·execution mode·signing service·field path·optional field는 기존
    builder·transport 구현이 결정하는 값이다. route 지원 여부는 채우지 않는다(그것은
    probe 결과가 정한다). Exact_Model_ID 결속은 계약이 아니라
    :func:`request_builder.bind_contract`가 담당한다. ``overrides``로 운영자·runner가
    계약 값을 대체할 수 있다.
    """
    profile = route_profile(route_key)
    if profile is None:
        return None
    contract = contracts.new_route_contract(
        str(route_key),
        endpoint_ref=profile["endpointRef"],
        http_method=profile["httpMethod"],
        execution_mode=profile["executionMode"],
        signing_service=profile["signingService"],
        model_id_required=profile["modelIdRequired"],
        model_id_field_path=profile["modelIdFieldPath"],
        message_field_path=profile["messageFieldPath"],
        inference_config_field_path=profile["inferenceConfigFieldPath"],
        optional_fields=profile["optionalFields"],
        output_validator_ref=profile["outputValidatorRef"],
        terminal_condition_ref=profile["terminalConditionRef"],
        retry_policy_ref=profile["retryPolicyRef"],
        fallback_rank=profile["fallbackRank"],
        purposes=profile["purposes"],
        min_output_bound=profile["minOutputBound"],
        evidence_ref=evidence_ref,
    )
    if _is_dict(overrides):
        for key, value in overrides.items():
            if key in contract and key != "routeKey":
                contract[key] = copy.deepcopy(value)
    return contract


def min_output_bound_fields(contract: Any) -> list[tuple[list[str], Any]]:
    """계약이 지정한 최소 output/token bound를 `(경로, 값)` 목록으로 읽는다.

    형식: ``minOutputBound = {"sourceRef": str, "fieldPath": [...], "fields": {...}}``.
    ``fields``가 비면 "이 route의 production builder는 output bound field를 만들지
    않는다"는 뜻이고 body는 baseline 그대로 유지된다(Requirement 4.26).
    """
    bound = contract.get("minOutputBound") if _is_dict(contract) else None
    if not _is_dict(bound):
        return []
    parent = bound.get("fieldPath")
    parent_path = (
        [part for part in parent if isinstance(part, str) and part]
        if isinstance(parent, (list, tuple))
        else []
    )
    fields = bound.get("fields")
    if not _is_dict(fields):
        return []
    return [
        (parent_path + [str(name)], fields[name])
        for name in sorted(fields, key=lambda item: str(item))
    ]


def apply_min_output_bound(body: Any, contract: Any) -> Any:
    """production builder가 만든 body에 계약 최소 bound만 기록한 새 body를 반환한다.

    production 선례: `GatewayClient.converse_quota_only`도 `_build_payload` 결과의
    `inferenceConfig.maxTokens`를 최소값으로 덮어써 최소 비용 요청을 만든다. 기록은
    :func:`request_builder.write_once`로 경로당 정확히 1회이며 그 경로 밖은 건드리지
    않는다. bound가 없으면 입력 body를 **동일 객체로** 반환한다.

    부모 경로가 body에 없으면 기록하지 않는다 — production builder가 만들지 않은 구조를
    probe가 새로 만들지 않기 위한 보호막이다(client는 route마다 자기 계약으로 결속된다).
    """
    result = body
    for path, value in min_output_bound_fields(contract):
        parent = path[:-1]
        if parent and not request_builder.path_exists(result, parent):
            continue
        written = request_builder.write_once(result, path, value)
        if written is not None:
            result = written
    return result


def without_min_output_bound(contract: Any) -> dict:
    """최소 bound를 제거한 계약 복사본(교정 재시도에서 baseline body로 되돌릴 때 쓴다)."""
    out = copy.deepcopy(contract) if _is_dict(contract) else {}
    bound = out.get("minOutputBound")
    if _is_dict(bound):
        bound["fields"] = {}
    return out


# ─────────────────────────────────────────────────────────────────
# ProbeBudget — 조합당 성공 1회 · 교정 1회 · route당 prefix 교정 1회
# ─────────────────────────────────────────────────────────────────
class ProbeBudget:
    """Gateway_Probe 예산을 강제한다(Requirement 2.16, 4.24, 4.25).

    조합(combination)의 단위는 `(Exact_Model_ID, Known_Route, effort value)`이며 effort
    미주입 baseline은 `effort value = ∅`인 하나의 조합이다. 예산은 세 축으로 나뉜다.

      - **성공 generation** 조합당 최대 1회. 이미 성공한 조합은 다시 전송하지 않는다.
      - **교정 요청** 조합당 최대 1회(명시적·교정 가능 validation error에만).
      - **prefix 형태 교정** 동일 route 최대 1회. 기존 transport가 내부에서 수행하는
        route는 collector가 추가 전송을 하지 않는다(:data:`PREFIX_OWNER_TRANSPORT`).

    전송 총량은 조합당 `성공 1 + 교정 1 + prefix 교정 1`을 넘지 않는다.
    """

    def __init__(
        self,
        *,
        max_successes: int = 1,
        max_corrections: int = 1,
        max_prefix_corrections: int = 1,
    ) -> None:
        self.max_successes = max(0, int(max_successes))
        self.max_corrections = max(0, int(max_corrections))
        self.max_prefix_corrections = max(0, int(max_prefix_corrections))
        self._transmissions: dict[str, int] = {}
        self._successes: dict[str, int] = {}
        self._corrections: dict[str, int] = {}
        self._prefix: dict[str, int] = {}

    # -- 키 --------------------------------------------------------------
    @staticmethod
    def key(model_id: Any, route_key: Any, effort_value: Any = EFFORT_VALUE_NONE) -> str:
        """조합 키(결정론적 문자열). effort 미주입은 ``null`` 자리로 표현된다."""
        return canonicalizer.serialize([_text(model_id), _text(route_key), effort_value])

    @staticmethod
    def route_key_of(model_id: Any, route_key: Any) -> str:
        """prefix 교정 예산 키(`(Exact_Model_ID, Known_Route)` 단위)."""
        return canonicalizer.serialize([_text(model_id), _text(route_key)])

    # -- 조회 ------------------------------------------------------------
    def transmissions(self, key: str) -> int:
        return self._transmissions.get(key, 0)

    def successes(self, key: str) -> int:
        return self._successes.get(key, 0)

    def corrections(self, key: str) -> int:
        return self._corrections.get(key, 0)

    def prefix_corrections(self, model_id: Any, route_key: Any) -> int:
        return self._prefix.get(self.route_key_of(model_id, route_key), 0)

    def transmission_limit(self) -> int:
        """조합당 허용 전송 총량."""
        return self.max_successes + self.max_corrections + self.max_prefix_corrections

    def may_transmit(self, key: str) -> bool:
        """이 조합에 Gateway 전송을 더 만들어도 되는지."""
        return (
            self.successes(key) < self.max_successes
            and self.transmissions(key) < self.transmission_limit()
        )

    def block_reason(self, key: str) -> str:
        """전송을 막는 예산 이유(허용되면 :data:`PROBE_OK`)."""
        if self.successes(key) >= self.max_successes:
            return PROBE_BUDGET_SUCCESS_USED
        if self.transmissions(key) >= self.transmission_limit():
            return PROBE_BUDGET_TRANSMISSION_LIMIT
        return PROBE_OK

    def may_correct(self, key: str) -> bool:
        return self.corrections(key) < self.max_corrections and self.may_transmit(key)

    def may_prefix_correct(self, model_id: Any, route_key: Any) -> bool:
        return self.prefix_corrections(model_id, route_key) < self.max_prefix_corrections

    # -- 기록 ------------------------------------------------------------
    def record_transmission(self, key: str) -> int:
        self._transmissions[key] = self.transmissions(key) + 1
        return self._transmissions[key]

    def record_success(self, key: str) -> int:
        self._successes[key] = self.successes(key) + 1
        return self._successes[key]

    def record_correction(self, key: str) -> int:
        self._corrections[key] = self.corrections(key) + 1
        return self._corrections[key]

    def record_prefix_correction(self, model_id: Any, route_key: Any) -> int:
        route = self.route_key_of(model_id, route_key)
        self._prefix[route] = self._prefix.get(route, 0) + 1
        return self._prefix[route]

    def baseline_succeeded(self, model_id: Any, route_key: Any) -> bool:
        """이 실행에서 effort 미주입(∅) 조합의 성공 generation이 기록됐는지.

        effort 검증의 전제조건(동일 Exact_Model_ID·Known_Route의 무-effort
        Baseline_Request_Body 성공 — Requirement 5.7)을 **추가 전송 없이** 확인하는
        경로다. 성공 generation은 조합당 1회이므로 이 카운터가 1이면 그 조합의 baseline
        전송이 이미 성공했다는 뜻이다.
        """
        return self.successes(self.key(model_id, route_key, EFFORT_VALUE_NONE)) >= 1

    # -- 보고 ------------------------------------------------------------
    def snapshot(self) -> dict:
        """보고서용 예산 사용 현황(비민감 — 값은 전부 카운터다)."""
        return {
            "maxSuccesses": self.max_successes,
            "maxCorrections": self.max_corrections,
            "maxPrefixCorrections": self.max_prefix_corrections,
            "transmissionLimit": self.transmission_limit(),
            "transmissions": dict(sorted(self._transmissions.items())),
            "successes": dict(sorted(self._successes.items())),
            "corrections": dict(sorted(self._corrections.items())),
            "prefixCorrections": dict(sorted(self._prefix.items())),
            "totalTransmissions": sum(self._transmissions.values()),
        }


# ─────────────────────────────────────────────────────────────────
# Valid_Output · Terminal_Success 판정 (production 심볼만 사용)
# ─────────────────────────────────────────────────────────────────
def converse_content_blocks(result: Any) -> list:
    """`/converse` 응답의 content 블록 중 **비어 있지 않은** 블록만 반환한다.

    기존 `GatewayClient.converse`가 job 결과를 채택할 때 쓰는 조건
    (`output.message.content`가 비어 있지 않음)을 그대로 쓰고, empty·partial output이
    `SUPPORTED`가 되지 않도록 빈 text 블록은 제외한다(Requirement 4.21, 4.22).
    """
    if not _is_dict(result):
        return []
    message = (result.get("output") or {}) if _is_dict(result.get("output")) else {}
    message = message.get("message") if _is_dict(message.get("message")) else None
    content = message.get("content") if _is_dict(message) else None
    if not isinstance(content, (list, tuple)):
        return []
    blocks = []
    for block in content:
        if not _is_dict(block):
            continue
        if isinstance(block.get("text"), str) and block["text"]:
            blocks.append(block)
        elif _is_dict(block.get("toolUse")):
            blocks.append(block)
    return blocks


def invoke_result_view(result: Any, extractor: Any = None) -> dict:
    """`/invoke` 응답을 production 추출기로 본 결과(`_extract_invoke_result`).

    추출기를 구할 수 없으면 응답 dict을 그대로 본다(그 사실은 note로 남긴다). 추출기
    출력이 비어 있거나 `error`를 담고 있으면 소비 가능한 output이 아니다.
    """
    if not _is_dict(result):
        return {}
    view = result
    if callable(extractor):
        try:
            view = extractor(result)
        except Exception:  # 추출 실패는 미확정으로만 취급한다(값을 만들지 않는다)
            view = {}
    return view if _is_dict(view) else {}


def _signals(**kwargs: Any) -> dict:
    """`failure_handler.classify` 입력 신호(None 값은 넣지 않는다)."""
    return {key: value for key, value in kwargs.items() if value is not None}


def judge_converse(result: Any) -> dict:
    """`/converse` 결과를 HTTP·Valid_Output·Terminal_Success로 판정한다.

    - HTTP 성공: Gateway가 decision을 돌려줬다(:data:`CONVERSE_HTTP_DECISIONS`).
      `ERROR`는 전송 계층·HTTP 오류다.
    - Valid_Output: content 블록이 비어 있지 않다.
    - Terminal_Success: `decision == ALLOW` + Valid_Output. `ACCEPTED`는 기존
      `converse`가 내부에서 `_poll_job_data`로 종결하므로 여기서 `ALLOW`로 관측된다.
    """
    data = result if _is_dict(result) else {}
    decision = _text(data.get("decision")).upper()
    error_text = _text(data.get("error")) or _text(data.get("denial_reason"))
    blocks = converse_content_blocks(data)
    http = decision in CONVERSE_HTTP_DECISIONS
    valid = bool(blocks)
    terminal = decision == "ALLOW" and valid
    signals = _signals(
        errorText=error_text or None,
        allowlistDenied=True if decision == "DENY" else None,
        emptyOutput=True if (decision == "ALLOW" and not valid) else None,
        partialOutput=True if decision == "ACCEPTED" else None,
    )
    return {
        "http": http,
        "validOutput": valid,
        "terminalSuccess": terminal,
        "signals": signals,
        "observations": {"decision": decision, "contentBlocks": len(blocks)},
        "notes": [],
        "usage": gateway_usage(data),
        "cost": gateway_cost(data),
    }


def judge_invoke(result: Any, *, extractor: Any = None) -> dict:
    """`/invoke` 결과를 판정한다(production `_extract_invoke_result` 기준).

    기존 `invoke_model`은 동기 결과 또는 `_poll_invoke_job` 종결 후에만 반환하므로,
    소비 가능한 output이 있으면 Terminal_Success가 성립한다(Requirement 4.11, 4.12).
    """
    data = result if _is_dict(result) else {}
    error_text = _text(data.get("error"))
    async_stage = any(signal in error_text for signal in INVOKE_ASYNC_STAGE_SIGNALS)
    view = invoke_result_view(data, extractor) if not error_text else {}
    valid = bool(view) and "error" not in view
    http = (not error_text) or async_stage
    terminal = http and valid
    notes: list[str] = []
    if not callable(extractor):
        notes.append(NOTE_INVOKE_EXTRACTOR_UNAVAILABLE)
    if async_stage:
        notes.append(NOTE_ASYNC_STAGE_FAILURE)
    return {
        "http": http,
        "validOutput": valid,
        "terminalSuccess": terminal,
        "signals": _signals(errorText=error_text or None),
        "observations": {"asyncStage": async_stage, "outputFields": len(view)},
        "notes": notes,
        "usage": gateway_usage(data),
        "cost": gateway_cost(data),
    }


def _openai_valid_output(raw: Any) -> tuple[bool, str]:
    """production adapter(`to_converse`)로 Valid_Output을 판정한다."""
    adapter = _openai_adapter()
    try:
        converted = adapter.to_converse(raw)
    except adapter.InvalidOpenAIResponse as exc:
        return False, _truncate(str(exc))
    except Exception as exc:  # adapter가 소비할 수 없는 형태
        return False, _truncate(f"{type(exc).__name__}: {exc}")
    content = ((converted.get("output") or {}).get("message") or {}).get("content")
    return bool(content), ""


def _openai_status(raw: Any) -> str:
    """응답에서 상태 문자열을 읽는다(게이트웨이 래퍼도 확인)."""
    adapter = _openai_adapter()
    status = adapter.extract_status(raw)
    if status:
        return status
    return adapter.extract_status(adapter._unwrap_gateway_envelope(raw))


def judge_openai_sync(raw: Any) -> dict:
    """`/openai/responses` 동기 결과를 판정한다(Requirement 4.13).

    기존 `_openai_post_with_retry`는 오류를 예외로 올리므로 dict가 돌아왔다는 것은
    HTTP 성공이다. Valid_Output은 `openai_adapter.to_converse`가, Terminal_Success는
    완료 상태(`status_is_completed`)와 Valid_Output이 함께 판정한다.
    """
    adapter = _openai_adapter()
    valid, detail = _openai_valid_output(raw)
    status = _openai_status(raw)
    failed = bool(status) and adapter.status_is_failed(status)
    completed = (not status) or adapter.status_is_completed(status)
    terminal = valid and completed and not failed
    return {
        "http": _is_dict(raw),
        "validOutput": valid,
        "terminalSuccess": terminal,
        "signals": _signals(
            errorText=detail or None,
            partialOutput=True if (valid and not completed) else None,
            emptyOutput=True if (_is_dict(raw) and not valid and not detail) else None,
        ),
        "observations": {"status": status, "completed": completed},
        "notes": [],
        "usage": adapter_usage(raw),
        "cost": gateway_cost(raw),
    }


def judge_openai_jobs(job_id: Any, poll: Any) -> dict:
    """`/openai/responses-jobs` 결과를 판정한다(Requirement 4.14).

    제출 성공·job ID·Terminal_Success·Valid_Output을 각각 관측한다. 기존
    `_openai_poll_job`은 completed/succeeded에서만 결과를 돌려주므로 poll 결과가 있으면
    terminal 상태이고, Valid_Output은 `to_converse`가 판정한다.
    """
    submitted = isinstance(job_id, str) and bool(job_id)
    valid, detail = _openai_valid_output(poll) if poll is not None else (False, "")
    http = submitted
    terminal = submitted and poll is not None and valid
    return {
        "http": http,
        "validOutput": valid,
        "terminalSuccess": terminal,
        "signals": _signals(
            errorText=detail or None,
            partialOutput=True if (submitted and poll is None) else None,
            emptyOutput=True if (poll is not None and not valid and not detail) else None,
        ),
        "observations": {
            "submitted": submitted,
            "jobIdPresent": submitted,
            "polled": poll is not None,
        },
        "notes": [],
        "usage": adapter_usage(poll),
        "cost": gateway_cost(poll),
    }


def judge_sse(events: Any) -> dict:
    """SSE_Stream_Route 결과를 판정한다(Requirement 4.15).

    content event와 terminal event를 각각 센다. 이벤트 타입 이름은 기존
    `_converse_stream_live_once`가 파싱하는 이름 그대로다.
    """
    items = [event for event in (events or []) if _is_dict(event)]
    content = 0
    terminal = 0
    error_text = ""
    http_error = False
    for event in items:
        kind = _text(event.get("type"))
        if kind == SSE_ERROR_EVENT_TYPE:
            message = _text(event.get("message")) or _text(event.get("error"))
            error_text = error_text or message
            if message.startswith(SSE_HTTP_ERROR_PREFIX):
                http_error = True
            continue
        if kind in SSE_TERMINAL_EVENT_TYPES:
            terminal += 1
            continue
        if kind in SSE_CONTENT_EVENT_TYPES and _sse_event_has_content(event):
            content += 1
    http = bool(items) and not http_error
    valid = content >= 1
    observations: dict[str, Any] = {"contentEvents": content, "terminalEvents": terminal}
    observed_model_id = observe_sse_model_id(items)
    if observed_model_id:
        observations["invocationModelId"] = observed_model_id
    return {
        "http": http,
        "validOutput": valid,
        "terminalSuccess": terminal >= 1 and valid,
        "signals": _signals(
            errorText=error_text or None,
            emptyOutput=True if (http and not valid) else None,
            partialOutput=True if (valid and terminal == 0) else None,
        ),
        "observations": observations,
        "notes": [],
        "usage": contracts.NOT_PROVIDED,
        "cost": _sse_cost(items),
    }


def observe_sse_model_id(events: Any) -> str:
    """SSE 이벤트가 알려준 실제 전송 model ID(관측되지 않으면 빈 문자열).

    transport가 내부에서 prefix를 교정하면 재연결마다 전송 ID를 알려주는 event가 다시
    나오므로, **마지막으로 관측된** 값이 마지막 실제 전송에 쓰인 ID다. 값은 응답에서
    읽은 문자 그대로이며 접두사를 붙이거나 떼지 않는다(값 추론 금지).
    """
    observed = contracts.UNDETERMINED
    for event in events or []:
        if not _is_dict(event):
            continue
        value = _text(event.get(SSE_MODEL_ID_FIELD))
        if value:
            observed = value
    return observed


def _sse_cost(events: Sequence[Mapping[str, Any]]) -> Any:
    """settlement event가 알려준 비용만 Gateway key 이름 그대로 읽는다."""
    for event in events:
        if _text(event.get("type")) != "settlement":
            continue
        out = {
            key: event[key]
            for key in ("estimated_cost_krw", "remaining_quota_krw")
            if isinstance(event.get(key), (int, float)) and not isinstance(event.get(key), bool)
        }
        if out:
            return out
    return contracts.NOT_PROVIDED


def _sse_event_has_content(event: Mapping[str, Any]) -> bool:
    """content event가 실제 content를 담고 있는지(빈 delta는 제외)."""
    kind = _text(event.get("type"))
    if kind != "content_block_delta":
        block = event.get("content_block") or event.get("contentBlock")
        return _is_dict(block) and bool(block)
    delta = event.get("delta")
    if not _is_dict(delta):
        return False
    if isinstance(delta.get("text"), str) and delta["text"]:
        return True
    return bool(delta.get("toolUse"))


def gateway_usage(raw: Any) -> Any:
    """Gateway가 제공한 usage만 읽는다(없으면 ``notProvided`` — Requirement 11.19)."""
    if not _is_dict(raw):
        return contracts.NOT_PROVIDED
    usage = raw.get("usage")
    return dict(usage) if _is_dict(usage) and usage else contracts.NOT_PROVIDED


def gateway_cost(raw: Any) -> Any:
    """Gateway가 제공한 비용 값만 Gateway의 key 이름 그대로 읽는다(없으면 ``notProvided``).

    key 이름은 기존 응답 계약(`converse`·settlement event)의 이름을 바꾸지 않고 쓴다.
    """
    if not _is_dict(raw):
        return contracts.NOT_PROVIDED
    out: dict[str, Any] = {}
    for key in ("estimated_cost_krw", "remaining_quota"):
        value = raw.get(key)
        if isinstance(value, (int, float)) and not isinstance(value, bool):
            out[key] = value
        elif _is_dict(value) and value:
            out[key] = dict(value)
    return out or contracts.NOT_PROVIDED


def adapter_usage(raw: Any) -> Any:
    """production adapter로 usage를 읽는다(전부 0이면 미제공으로 본다).

    `openai_adapter.extract_usage`는 필드가 없으면 0을 돌려주므로, 0만 있는 결과를
    "Gateway가 제공한 값"으로 기록하지 않는다(추정 금지).
    """
    if not _is_dict(raw):
        return contracts.NOT_PROVIDED
    usage = _openai_adapter().extract_usage(raw)
    return dict(usage) if any(usage.values()) else contracts.NOT_PROVIDED


def observe_model_id_binding(request_body: Any, profile: Mapping[str, Any]) -> dict:
    """성공 probe가 전송한 body에서 model ID 결속을 관측한다(Requirement 8.11, 8.12 근거).

    profile의 `observedModelIdFieldPath`(기존 builder·seam이 model ID를 두는 위치)에
    값이 실제로 존재하는지만 본다. 라벨·provider·모델 계열에서 경로를 만들지 않는다.

    Returns:
        ``{"observed": bool, "modelIdRequired": bool|None, "modelIdFieldPath": [...]|None}``
    """
    path = profile.get("observedModelIdFieldPath") if _is_dict(profile) else None
    if not request_builder.is_field_path(path) or not _is_dict(request_body):
        return {"observed": False, "modelIdRequired": None, "modelIdFieldPath": None}
    if request_builder.path_exists(request_body, path):
        return {"observed": True, "modelIdRequired": True, "modelIdFieldPath": list(path)}
    return {"observed": True, "modelIdRequired": False, "modelIdFieldPath": None}


def route_support_status(
    *,
    advertised: bool,
    http: bool,
    valid_output: bool,
    terminal_success: bool,
    category: str | None = None,
) -> str:
    """세 독립 결과와 실패 범주에서 Route_Support_Status를 정한다.

    - 광고되지 않음 → `NOT_ADVERTISED`(전송 0건 — Requirement 4.16, 4.17)
    - 세 조건 모두 충족 → `SUPPORTED`(Requirement 4.8)
    - unknown model·unsupported route 명시적 거부 → `UNSUPPORTED`(Requirement 4.18)
    - 그 외(transient·empty·partial·timeout·allowlist 거부·quota·인증·validation)
      → `UNVERIFIED` 유지(Requirement 4.20~4.23)
    """
    if not advertised:
        return str(contracts.Route_Support_Status.NOT_ADVERTISED)
    if http and valid_output and terminal_success:
        return str(contracts.Route_Support_Status.SUPPORTED)
    if category == failure_handler.ROUTE_CAPABILITY_MISMATCH:
        return str(contracts.Route_Support_Status.UNSUPPORTED)
    return str(contracts.Route_Support_Status.UNVERIFIED)


def allowlist_result(*, valid_output: bool, category: str | None = None) -> str:
    """Allowlist_Result를 정한다(Requirement 2.13, 2.14, 2.15).

    production path가 Valid_Output을 돌려주면 `ALLOWED`, Gateway가 명시적으로 allowlist
    거부를 반환하면 `REJECTED`, 그 외에는 `UNVERIFIED`를 유지한다.
    """
    if category == failure_handler.ALLOWLIST:
        return str(contracts.Allowlist_Result.REJECTED)
    if valid_output:
        return str(contracts.Allowlist_Result.ALLOWED)
    return str(contracts.Allowlist_Result.UNVERIFIED)


# ─────────────────────────────────────────────────────────────────
# Minimal_Request 전용 client — production builder + 계약 최소 bound
# ─────────────────────────────────────────────────────────────────
_PROBE_CLIENT: Any = None


def probe_client_class() -> type:
    """`EffortBoundClient`를 상속한 Minimal_Request 전용 client 클래스.

    production Request_Builder(`_build_payload`·`_build_openai_payload`)가 body를
    만들고, 그 결과에 계약이 지정한 **최소 output/token bound만** 1회 기록한다
    (Requirement 4.2, 4.26). 서명·credential·retry·prefix 교정·job polling·응답 변환은
    모두 상속 구현 그대로이며 새로 만들지 않는다.

    첫 호출에서 `ai_engine.gateway_module`을 import한다(패키지 import 시점에 transport
    의존을 만들지 않는다는 규약 — :func:`request_builder.effort_bound_client_class`와 동일).
    """
    global _PROBE_CLIENT
    if _PROBE_CLIENT is None:
        base_cls = request_builder.effort_bound_client_class()

        class MinimalRequestClient(base_cls):  # type: ignore[misc, valid-type]
            """Minimal_Request 전용 probe client(요청 경로에는 쓰지 않는다)."""

            def _build_payload(self, *args: Any, **kwargs: Any):
                return apply_min_output_bound(super()._build_payload(*args, **kwargs), self._contract)

            def _build_openai_payload(self, *args: Any, **kwargs: Any):
                return apply_min_output_bound(
                    super()._build_openai_payload(*args, **kwargs), self._contract
                )

        MinimalRequestClient.__name__ = "MinimalRequestClient"
        MinimalRequestClient.__qualname__ = "MinimalRequestClient"
        _PROBE_CLIENT = MinimalRequestClient
    return _PROBE_CLIENT


def probe_client(base: Any, binding: Any) -> Any:
    """Minimal_Request 전용 probe client를 만든다(effort selection은 언제나 미선택)."""
    return probe_client_class()(base, binding, PROBE_EFFORT_SELECTION)


def probe_messages(text: str = PROBE_INPUT_TEXT) -> list:
    """Converse 형식 Minimal_Request 입력(고정 짧은 비민감 입력)."""
    return [{"role": "user", "content": [{"text": text}]}]


#: 요청 body를 collector가 직접 전송하는 route(builder seam이 없으면 전송할 수 없다).
_PREVIEW_REQUIRED_ROUTES: frozenset[str] = frozenset({
    str(contracts.Known_Route.OPENAI_RESPONSES),
})

#: 교정 수단(닫힌 집합).
_CORRECTION_PREFIX = "PREFIX"
_CORRECTION_PREFIX_DELEGATED = "PREFIX_DELEGATED"
_CORRECTION_DROP_BOUND = "DROP_BOUND"

_MODULE_UNSET: Any = object()
_GATEWAY_MODULE: Any = _MODULE_UNSET


def _gateway_module():
    """``ai_engine.gateway_module``을 지연 import한다(불가하면 ``None``).

    capability 패키지는 import 시점에 transport 의존을 만들지 않는다
    (:mod:`.failure_handler`와 동일 규약).
    """
    global _GATEWAY_MODULE
    if _GATEWAY_MODULE is _MODULE_UNSET:
        try:
            from ai_engine import gateway_module as module
        except Exception:  # pragma: no cover - transport 의존 부재 환경
            module = None
        _GATEWAY_MODULE = module
    return _GATEWAY_MODULE


def corrected_model_id(model_id: Any) -> str:
    """기존 production prefix 규칙으로 반대 형태 model ID를 만든다(불가하면 원본).

    판정·변환은 기존 `_has_region_prefix`·`_strip_region_prefix`와 `converse`의 폴백
    규칙(`us.` 접두)을 그대로 쓴다. 새 prefix 규칙을 만들지 않으며, 결과는
    Invocation_Model_ID로만 기록된다(Exact_Model_ID는 바뀌지 않는다 — Requirement 2.17).
    """
    text = _text(model_id)
    module = _gateway_module()
    if not text or module is None:
        return text
    if module._has_region_prefix(text):
        return module._strip_region_prefix(text)
    return f"us.{text}"


# ─────────────────────────────────────────────────────────────────
# route 결과 → Verification_Record 투영
# ─────────────────────────────────────────────────────────────────
def evidence_eligible_results(results: Iterable[Mapping[str, Any]]) -> list[dict]:
    """activation evidence로 인정되는 route 결과만 고른다(Requirement 4.4).

    남는 것은 두 종류다.
      - production Request_Builder·production transport·production adapter로 얻은 결과
      - catalog가 알려준 `NOT_ADVERTISED`(전송이 필요 없는 근거)

    주입 transport(mock·진단 코드)로 전송한 결과와, 예산·입력·transport 부재로 전송하지
    못해 아무것도 주장하지 않는 결과는 제외된다(기존 route 상태를 강등시키지 않는다).
    """
    return [
        dict(item)
        for item in results
        if _is_dict(item) and item.get("evidenceEligible") and contracts.Known_Route.has(item.get("routeKey"))
    ]


def recordable_results(results: Iterable[Mapping[str, Any]]) -> list[dict]:
    """Verification_Record에 담을 route 결과만 고른다.

    :func:`evidence_eligible_results`(Requirement 4.4)에 **상태 보존 불변식**을 더한
    것이다. `transient`·`quota`·`authentication`·`request-validation`·`unknown`은
    route·allowlist 상태를 바꾸지 않으므로(design "상태 보존 불변식" 표) record에 담지
    않는다. 담으면 이전에 `SUPPORTED`였던 route가 일시 오류만으로 강등된다
    (Requirement 4.20~4.23, 9.8~9.10).
    """
    return [item for item in evidence_eligible_results(results) if item.get("stateBearing")]


def route_result_for_record(result: Mapping[str, Any]) -> dict:
    """route 결과를 Verification_Record `routeResults` 항목으로 투영한다.

    스키마 필드(:data:`contracts.REQUIRED_ROUTE_RESULT_FIELDS`)와 비민감 관측치
    (`observations`)만 남긴다. Invocation_Model_ID는 record 최상위
    `invocationModelIds`에 별도로 기록된다(Requirement 2.17).
    """
    projected = {field: result.get(field) for field in contracts.REQUIRED_ROUTE_RESULT_FIELDS}
    observations = result.get("observations")
    if _is_dict(observations) and observations:
        projected["observations"] = copy.deepcopy(observations)
    return projected


def route_contracts_for_record(results: Iterable[Mapping[str, Any]]) -> dict:
    """`{routeKey: Route_Contract}` — `upsert`에 넘길 **확정 계약**만 담는다.

    성공 probe가 확정한 계약만 넣는다. 확정하지 못한 route는 키 자체를 넣지 않으므로
    `upsert`가 기존 계약을 그대로 유지한다(실패한 재검증이 이전 계약을 지우지 않는다).
    evidence로 인정되지 않는 결과의 계약도 담지 않는다(Requirement 4.4).
    """
    out: dict[str, Any] = {}
    for item in recordable_results(results):
        contract = item.get("contract")
        if _is_dict(contract):
            out[str(item.get("routeKey"))] = copy.deepcopy(contract)
    return out


def invocation_model_ids_from(results: Iterable[Mapping[str, Any]]) -> list[str]:
    """실제 전송에 쓴 Invocation_Model_ID 목록(집합 의미, 정렬)."""
    ids = {
        _text(item.get("invocationModelId"))
        for item in results
        if _is_dict(item) and _text(item.get("invocationModelId"))
    }
    return sorted(ids)


def route_completeness(results: Iterable[Mapping[str, Any]]) -> bool:
    """모든 Known_Route 상태가 채워졌는지(Requirement 4.27)."""
    covered = {
        str(item.get("routeKey"))
        for item in results
        if _is_dict(item)
        and contracts.Known_Route.has(item.get("routeKey"))
        and contracts.Route_Support_Status.has(item.get("status"))
    }
    return covered >= set(contracts.KNOWN_ROUTES)


def usage_from(results: Iterable[Mapping[str, Any]]) -> Any:
    """Gateway가 제공한 usage 중 첫 값(없으면 ``notProvided``) — 추정하지 않는다."""
    for item in results:
        usage = item.get("usage") if _is_dict(item) else None
        if _is_dict(usage) and usage:
            return dict(usage)
    return contracts.NOT_PROVIDED


def cost_from(results: Iterable[Mapping[str, Any]]) -> Any:
    """Gateway가 제공한 cost 중 첫 값(없으면 ``notProvided``) — 추정하지 않는다."""
    for item in results:
        cost = item.get("cost") if _is_dict(item) else None
        if _is_dict(cost) and cost:
            return dict(cost)
    return contracts.NOT_PROVIDED


# ═════════════════════════════════════════════════════════════════
# effort 계약 읽기 — Authoritative_Evidence가 "명시"한 자리만 읽는다
#
# effort의 field path·value type·domain은 **evidence만** 채우는 자리다. 이 절의 모든
# 함수는 catalog record가 명시한 effort 선언과 discovery가 확정한 Exact_Model_ID·
# Known_Route만 입력으로 받는다. Candidate_Label·Provider_String·다른 Candidate_Model의
# evidence는 어떤 함수의 입력에도 들어가지 않으므로, 라벨·provider에서 field path나
# domain이 생성될 경로가 구조적으로 없다(Requirement 5.18, 5.19).
# ═════════════════════════════════════════════════════════════════

#: record에서 effort capability 선언을 담을 수 있는 키(정규화 형태).
#: 표시명·설명·주석 키는 포함하지 않는다(Requirement 1.17과 동일 원칙).
EFFORT_CAPABILITY_KEYS: tuple[str, ...] = (
    "effort",
    "efforts",
    "effortcapability",
    "effortcapabilities",
    "effortsupport",
    "effortcontract",
    "effortcontracts",
)

#: 선언 한 단계 하위에서 계속 조회할 dict 키(중첩 domain 선언 형태 지원).
_EFFORT_NESTED_KEYS: frozenset[str] = frozenset(
    {"domain", "valuedomain", "effort", "contract", "schema", "spec", "parameter", "field"}
)

#: exact field path를 담는 키(정규화 형태, 우선순위 순).
EFFORT_FIELD_PATH_KEYS: tuple[str, ...] = (
    "fieldpath",
    "path",
    "fieldname",
    "field",
    "parameterpath",
    "parametername",
)

#: exact value type을 담는 키(정규화 형태).
EFFORT_VALUE_TYPE_KEYS: tuple[str, ...] = ("valuetype", "type", "valuekind")

#: domain 종류를 담는 키(정규화 형태).
EFFORT_DOMAIN_KIND_KEYS: tuple[str, ...] = ("domainkind", "domaintype", "domain", "kind")

#: complete enum을 담는 키(정규화 형태).
EFFORT_ENUM_KEYS: tuple[str, ...] = (
    "enumvalues",
    "enum",
    "allowedvalues",
    "supportedvalues",
    "values",
)

#: inclusive 하한·상한을 담는 키(정규화 형태).
EFFORT_RANGE_LOWER_KEYS: tuple[str, ...] = ("rangelowerinclusive", "lowerinclusive", "minimum", "min", "lower")
EFFORT_RANGE_UPPER_KEYS: tuple[str, ...] = ("rangeupperinclusive", "upperinclusive", "maximum", "max", "upper")

#: production Request_Builder seam으로 effort를 주입할 수 있는 route.
#: `/invoke`는 body가 모델별 형식이고 운영자가 제공하므로 production 주입 seam이 없다 →
#: effort probe를 전송하지 않는다(수동 body 조립은 activation evidence가 아니다).
EFFORT_INJECTABLE_ROUTES: frozenset[str] = frozenset(
    {
        str(contracts.Known_Route.CONVERSE),
        str(contracts.Known_Route.OPENAI_RESPONSES),
        str(contracts.Known_Route.OPENAI_RESPONSES_JOBS),
        str(contracts.Known_Route.SSE_STREAM),
    }
)

#: Gateway의 effort 거부 종류(닫힌 집합).
EFFORT_REJECTION_UNKNOWN_FIELD = "UNKNOWN_FIELD"
EFFORT_REJECTION_INVALID_VALUE = "INVALID_VALUE"

#: :func:`effort_rejection_kind`가 읽는 오류 문자열 신호 키
#: (:data:`failure_handler.SIGNAL_KEYS`의 문자열 신호와 같은 이름을 쓴다).
_EFFORT_TEXT_SIGNAL_KEYS: tuple[str, ...] = ("errorText", "message", "detail", "bodyText")

# ── effort probe 차단·판정 이유 코드(닫힌 집합) ────────────────────────────
EFFORT_PROBE_ROUTE_UNKNOWN = "EFFORT_PROBE_ROUTE_UNKNOWN"
EFFORT_PROBE_MODEL_ID_ABSENT = "EFFORT_PROBE_MODEL_ID_ABSENT"
EFFORT_PROBE_NOT_ADVERTISED = "EFFORT_PROBE_NOT_ADVERTISED"
EFFORT_PROBE_ROUTE_NOT_INJECTABLE = "EFFORT_PROBE_ROUTE_NOT_INJECTABLE"
EFFORT_PROBE_CONTRACT_ABSENT = "EFFORT_PROBE_CONTRACT_ABSENT"
EFFORT_PROBE_CONTRACT_INCOMPLETE = "EFFORT_PROBE_CONTRACT_INCOMPLETE"
EFFORT_PROBE_CONTRACT_MISBOUND = "EFFORT_PROBE_CONTRACT_MISBOUND"
EFFORT_PROBE_DOMAIN_INCOMPLETE = "EFFORT_PROBE_DOMAIN_INCOMPLETE"
EFFORT_PROBE_JOBS_MODEL_ID_UNDETERMINED = "EFFORT_PROBE_JOBS_MODEL_ID_UNDETERMINED"
EFFORT_PROBE_BASELINE_UNVERIFIED = "EFFORT_PROBE_BASELINE_UNVERIFIED"
EFFORT_PROBE_TRANSPORT_UNAVAILABLE = "EFFORT_PROBE_TRANSPORT_UNAVAILABLE"
EFFORT_PROBE_TRANSPORT_METHOD_ABSENT = "EFFORT_PROBE_TRANSPORT_METHOD_ABSENT"
EFFORT_PROBE_BUDGET_EXHAUSTED = "EFFORT_PROBE_BUDGET_EXHAUSTED"
EFFORT_PROBE_INJECTION_BLOCKED = "EFFORT_PROBE_INJECTION_BLOCKED"
EFFORT_PROBE_INJECTION_UNOBSERVED = "EFFORT_PROBE_INJECTION_UNOBSERVED"
EFFORT_PROBE_NOT_PROBED = "EFFORT_PROBE_NOT_PROBED"

EFFORT_PROBE_BLOCK_REASONS: tuple[str, ...] = (
    EFFORT_PROBE_ROUTE_UNKNOWN,
    EFFORT_PROBE_MODEL_ID_ABSENT,
    EFFORT_PROBE_NOT_ADVERTISED,
    EFFORT_PROBE_ROUTE_NOT_INJECTABLE,
    EFFORT_PROBE_CONTRACT_ABSENT,
    EFFORT_PROBE_CONTRACT_INCOMPLETE,
    EFFORT_PROBE_CONTRACT_MISBOUND,
    EFFORT_PROBE_DOMAIN_INCOMPLETE,
    EFFORT_PROBE_JOBS_MODEL_ID_UNDETERMINED,
    EFFORT_PROBE_BASELINE_UNVERIFIED,
    EFFORT_PROBE_TRANSPORT_UNAVAILABLE,
    EFFORT_PROBE_TRANSPORT_METHOD_ABSENT,
    EFFORT_PROBE_BUDGET_EXHAUSTED,
    EFFORT_PROBE_INJECTION_BLOCKED,
    EFFORT_PROBE_INJECTION_UNOBSERVED,
    EFFORT_PROBE_NOT_PROBED,
)

#: 무-effort baseline 성공을 확인한 출처(닫힌 집합).
BASELINE_FROM_ROUTE_RESULT = "ROUTE_RESULT"
BASELINE_FROM_BUDGET = "BUDGET"
BASELINE_FROM_PROBE = "PROBE"

# ── 비차단 note 코드 ──────────────────────────────────────────────────────
NOTE_EFFORT_DOMAIN_PARTIAL = "EFFORT_DOMAIN_PARTIAL"
NOTE_EFFORT_PROBE_STOPPED = "EFFORT_PROBE_STOPPED"
NOTE_EFFORT_INJECTION_UNOBSERVED = "EFFORT_INJECTION_UNOBSERVED"
NOTE_JOBS_MODEL_ID_UNDETERMINED = "JOBS_MODEL_ID_UNDETERMINED"
NOTE_BASE_ROUTE_STATUS_PRESERVED = "BASE_ROUTE_STATUS_PRESERVED"


def _effort_views(declaration: Mapping[str, Any]) -> list[Mapping[str, Any]]:
    """선언과 그 한 단계 하위 dict를 조회 대상으로 만든다(중첩 domain 선언 지원)."""
    views: list[Mapping[str, Any]] = [declaration]
    for key, value in declaration.items():
        if _is_dict(value) and _normalize_key(key) in _EFFORT_NESTED_KEYS:
            views.append(value)
    return views


def _effort_pick(declaration: Any, normalized_keys: Sequence[str]) -> tuple[str, Any] | None:
    """선언(또는 한 단계 하위 dict)에서 (원본 키, 값)을 찾는다(없으면 ``None``)."""
    if not _is_dict(declaration):
        return None
    for view in _effort_views(declaration):
        found = _pick(view, normalized_keys)
        if found is not None:
            return found
    return None


def effort_declaration_routes(declaration: Any) -> list[str]:
    """선언이 **명시한** Known_Route 목록(명시하지 않으면 빈 목록).

    route 판별은 :func:`record_advertised_routes`(Known_Route 문자열 정확 일치)를 그대로
    재사용한다 — endpoint 문자열·provider·라벨에서 route를 추론하지 않는다.
    """
    return record_advertised_routes(declaration) if _is_dict(declaration) else []


def record_advertised_effort(record: Any) -> dict:
    """record가 명시한 effort 선언을 `{routeKey: 선언}`으로 읽는다.

    지원 형태:
      - `{"effort": {"CONVERSE": {...}}}`            route key로 색인된 매핑
      - `{"effort": {"route": "CONVERSE", ...}}`     선언이 자기 route를 명시
      - `{"effort": [{...}, {...}]}`                 선언 목록(각자 route 명시)

    route를 **명시하지 않은** 선언은 무시한다. effort 계약은 Exact_Model_ID와 Known_Route
    조합에 결속되어야 하므로(Requirement 5.1), route가 불명한 선언으로는 계약을 만들 수
    없다. 값은 어떤 문자도 변경하지 않고 그대로 담는다.
    """
    out: dict[str, dict] = {}
    if not _is_dict(record):
        return out
    for key, value in record.items():
        if _normalize_key(key) not in EFFORT_CAPABILITY_KEYS:
            continue
        candidates = list(value) if isinstance(value, (list, tuple)) else [value]
        for declaration in candidates:
            if not _is_dict(declaration):
                continue
            indexed = {
                str(route): dict(item)
                for route, item in declaration.items()
                if contracts.Known_Route.has(route) and _is_dict(item)
            }
            if indexed:
                for route, item in indexed.items():
                    out.setdefault(route, item)
                continue
            for route in effort_declaration_routes(declaration):
                out.setdefault(route, dict(declaration))
    return {route: out[route] for route in contracts.KNOWN_ROUTES if route in out}


def advertised_effort(model: Any) -> dict:
    """discovery 결과가 광고한 effort 선언 `{routeKey: 선언}`(없으면 빈 dict)."""
    raw = model.get("advertisedEffort") if _is_dict(model) else None
    if not _is_dict(raw):
        return {}
    return {
        str(route): dict(item)
        for route, item in raw.items()
        if contracts.Known_Route.has(route) and _is_dict(item)
    }


def effort_declaration_field_path(declaration: Any) -> list[str] | None:
    """선언이 명시한 exact field path(없거나 형식 위반이면 ``None``).

    문자열 목록은 그대로 쓰고, 단일 문자열은 한 segment로 본다. 점 표기를 분해하지
    않는다 — 문자 변경 없이 evidence가 준 형태만 기록한다.
    """
    found = _effort_pick(declaration, EFFORT_FIELD_PATH_KEYS)
    if found is None:
        return None
    _, value = found
    if isinstance(value, str):
        return [value] if value else None
    if isinstance(value, (list, tuple)):
        parts = [item for item in value if isinstance(item, str) and item]
        return parts if parts and len(parts) == len(list(value)) else None
    return None


def effort_declaration_value_type(declaration: Any) -> str | None:
    """선언이 명시한 exact value type(:class:`contracts.Value_Type`)이거나 ``None``.

    닫힌 집합 토큰의 대소문자만 정규화한다(값을 만들어내지 않는다).
    """
    found = _effort_pick(declaration, EFFORT_VALUE_TYPE_KEYS)
    if found is None:
        return None
    _, value = found
    if not isinstance(value, str) or not value.strip():
        return None
    token = value.strip().upper()
    return token if contracts.Value_Type.has(token) else None


def effort_declaration_domain(declaration: Any) -> dict:
    """선언이 제공한 domain을 읽는다(Requirement 5.4, 5.5).

    Returns:
        ``{"domainKind": "ENUM"|"RANGE"|None, "enumValues": list|None,
        "rangeLowerInclusive": number|None, "rangeUpperInclusive": number|None}``

    `domainKind`가 명시되면 그 종류의 값만 채운다. 명시되지 않으면 **완전한 domain이
    정확히 하나** 제공된 경우에만 그 종류로 확정한다(둘 다 또는 둘 다 아님 → 미확정).
    미확정 domain은 계약 불완전으로 남아 Effort_Support_Status가 `UNVERIFIED`를
    유지한다(Requirement 5.6).
    """
    result: dict[str, Any] = {
        "domainKind": None,
        "enumValues": None,
        "rangeLowerInclusive": None,
        "rangeUpperInclusive": None,
    }
    if not _is_dict(declaration):
        return result

    enum_found = _effort_pick(declaration, EFFORT_ENUM_KEYS)
    enum_values = None
    if enum_found is not None and isinstance(enum_found[1], (list, tuple)) and enum_found[1]:
        enum_values = list(enum_found[1])

    bounds: list[Any] = []
    for keys in (EFFORT_RANGE_LOWER_KEYS, EFFORT_RANGE_UPPER_KEYS):
        found = _effort_pick(declaration, keys)
        value = found[1] if found is not None else None
        bounds.append(value if isinstance(value, (int, float)) and not isinstance(value, bool) else None)
    lower, upper = bounds

    declared = None
    kind_found = _effort_pick(declaration, EFFORT_DOMAIN_KIND_KEYS)
    if kind_found is not None and isinstance(kind_found[1], str) and kind_found[1].strip():
        token = kind_found[1].strip().upper()
        declared = token if contracts.Domain_Kind.has(token) else None

    has_enum = enum_values is not None
    has_range = lower is not None and upper is not None
    if declared is None:
        if has_enum and not has_range:
            declared = str(contracts.Domain_Kind.ENUM)
        elif has_range and not has_enum:
            declared = str(contracts.Domain_Kind.RANGE)

    result["domainKind"] = declared
    if declared == contracts.Domain_Kind.ENUM:
        result["enumValues"] = enum_values
    elif declared == contracts.Domain_Kind.RANGE:
        result["rangeLowerInclusive"] = lower
        result["rangeUpperInclusive"] = upper
    return result


def candidate_effort_contract(
    declaration: Any,
    *,
    model_id: Any,
    route_key: Any,
    evidence_ref: str | None = None,
) -> dict | None:
    """선언에서 후보 Effort_Contract를 만든다(Requirement 5.1~5.5).

    Exact_Model_ID와 Known_Route는 discovery가 확정한 값을 그대로 결속하고(5.1), field
    path(5.2)·value type(5.3)·domain(5.4, 5.5)은 선언이 명시한 값만 채운다. 채우지 못한
    자리는 ``None``으로 남으며 그 계약은 불완전하므로 전송 없이 `UNVERIFIED`를
    유지한다(5.6). Exact_Model_ID나 route가 없으면 결속할 계약이 없으므로 ``None``이다.
    """
    if not _is_dict(declaration) or not contracts.Known_Route.has(route_key):
        return None
    exact_model_id = _text(model_id)
    if not exact_model_id:
        return None
    domain = effort_declaration_domain(declaration)
    return contracts.new_effort_contract(
        exact_model_id,
        str(route_key),
        field_path=effort_declaration_field_path(declaration),
        value_type=effort_declaration_value_type(declaration),
        domain_kind=domain["domainKind"],
        enum_values=domain["enumValues"],
        range_lower_inclusive=domain["rangeLowerInclusive"],
        range_upper_inclusive=domain["rangeUpperInclusive"],
        evidence_ref=evidence_ref,
    )


def effort_contracts_from_record(
    record: Any,
    *,
    model_id: Any,
    evidence_ref: str | None = None,
) -> dict:
    """record의 effort 선언에서 `{routeKey: 후보 Effort_Contract}`를 만든다."""
    out: dict[str, Any] = {}
    for route, declaration in record_advertised_effort(record).items():
        contract = candidate_effort_contract(
            declaration, model_id=model_id, route_key=route, evidence_ref=evidence_ref
        )
        if contract is not None:
            out[route] = contract
    return out


def candidate_effort_contract_for(
    model: Any,
    route_key: Any,
    *,
    override: Any = None,
    evidence_ref: str | None = None,
) -> dict | None:
    """probe에 쓸 후보 Effort_Contract(운영자 override 우선, 없으면 discovery 선언).

    입력은 discovery 결과의 `modelId`와 `advertisedEffort`뿐이다. Candidate_Label·
    Provider_String·다른 Candidate_Model의 evidence는 입력에 없다(Requirement 5.18, 5.19).
    """
    if _is_dict(override):
        return copy.deepcopy(override)
    declaration = advertised_effort(model).get(str(route_key))
    if declaration is None:
        return None
    return candidate_effort_contract(
        declaration,
        model_id=model.get("modelId") if _is_dict(model) else contracts.UNDETERMINED,
        route_key=route_key,
        evidence_ref=evidence_ref,
    )


def effort_contract_binding_reason(contract: Any, *, model_id: Any, route_key: Any) -> str:
    """후보 Effort_Contract의 결속·완전성을 판정한다(문제 없으면 :data:`PROBE_OK`)."""
    if not _is_dict(contract):
        return EFFORT_PROBE_CONTRACT_ABSENT
    reasons = contracts.validate_effort_contract(
        contract, route_key=str(route_key), model_id=_text(model_id) or None
    )
    if reasons:
        return EFFORT_PROBE_CONTRACT_MISBOUND
    if contracts.effort_contract_missing(contract):
        return EFFORT_PROBE_CONTRACT_INCOMPLETE
    return PROBE_OK


def effort_probe_values(contract: Any) -> list:
    """domain이 요구하는 검증 value 목록(Requirement 5.9, 5.10, 5.11).

    - `ENUM`: 광고된 각 value 1회(광고 순서 보존, canonical 중복 제거)
    - `RANGE`: 하한 ≠ 상한이면 두 경계 각 1회, 같으면 그 경계 1회

    계약이 불완전하거나 domain 값이 계약의 value type과 어긋나면 빈 목록이다 →
    호출자는 Gateway 전송을 만들지 않고 `UNVERIFIED`를 유지한다(Requirement 5.6).
    """
    if not contracts.effort_contract_is_complete(contract):
        return []
    value_type = contract.get("valueType")
    if contract.get("domainKind") == contracts.Domain_Kind.ENUM:
        values: list[Any] = []
        for item in contract.get("enumValues") or []:
            if not contracts.value_matches_type(item, value_type):
                return []  # 광고 domain이 value type과 어긋나면 확정할 수 없다
            if not any(canonicalizer.canonical_equal(item, seen) for seen in values):
                values.append(item)
        return values
    lower = contract.get("rangeLowerInclusive")
    upper = contract.get("rangeUpperInclusive")
    if not (
        contracts.value_matches_type(lower, value_type)
        and contracts.value_matches_type(upper, value_type)
    ):
        return []
    if canonicalizer.canonical_equal(lower, upper):
        return [lower]
    return [lower, upper]


def effort_rejection_kind(signals: Any) -> str | None:
    """Gateway의 effort 거부 종류(:data:`EFFORT_REJECTION_UNKNOWN_FIELD` /
    :data:`EFFORT_REJECTION_INVALID_VALUE`)이거나 ``None``.

    판정 신호는 :data:`failure_handler.EFFORT_UNKNOWN_FIELD_SIGNALS`와
    :data:`failure_handler.EFFORT_INVALID_VALUE_SIGNALS`를 그대로 재사용한다(신규 판정
    규칙·신규 신호 목록을 이 모듈에 만들지 않는다). 명시 boolean 신호가 문자열보다
    우선한다.
    """
    sig = signals if _is_dict(signals) else {}
    if sig.get("effortFieldRejected") is True:
        return EFFORT_REJECTION_UNKNOWN_FIELD
    if sig.get("effortValueRejected") is True:
        return EFFORT_REJECTION_INVALID_VALUE
    text = " ".join(
        _text(sig.get(key)) for key in _EFFORT_TEXT_SIGNAL_KEYS if isinstance(sig.get(key), str)
    ).lower()
    if not text:
        return None
    if any(signal in text for signal in failure_handler.EFFORT_UNKNOWN_FIELD_SIGNALS):
        return EFFORT_REJECTION_UNKNOWN_FIELD
    if any(signal in text for signal in failure_handler.EFFORT_INVALID_VALUE_SIGNALS):
        return EFFORT_REJECTION_INVALID_VALUE
    return None


def effort_value_status(
    *,
    success: bool,
    category: str | None = None,
    rejection: str | None = None,
) -> str:
    """단일 effort value probe의 Effort_Support_Status를 정한다.

    - 성공(HTTP·Valid_Output·Terminal_Success 모두 충족) → `SUPPORTED`
    - Gateway가 effort field를 unknown field로 거부 → `UNSUPPORTED`(Requirement 5.14)
    - invalid value·out-of-range → `UNVERIFIED`(Requirement 5.15)
    - Transient_Error·그 외 → `UNVERIFIED`(Requirement 5.16)
    """
    if success:
        return str(contracts.Effort_Support_Status.SUPPORTED)
    if rejection == EFFORT_REJECTION_UNKNOWN_FIELD and category == failure_handler.EFFORT_MISMATCH:
        return str(contracts.Effort_Support_Status.UNSUPPORTED)
    return str(contracts.Effort_Support_Status.UNVERIFIED)


def aggregate_effort_status(required_values: Sequence[Any], results: Iterable[Mapping[str, Any]]) -> str:
    """value별 결과를 Effort_Support_Status 하나로 집계한다.

    - unknown field 거부가 하나라도 있으면 `UNSUPPORTED`(Requirement 5.14)
    - 요구된 **모든** value가 성공 probe로 검증되면 `SUPPORTED`(Requirement 5.13)
    - 그 외(부분 검증·transient·invalid value)는 `UNVERIFIED`(Requirement 5.15~5.17)
    """
    items = [item for item in results if _is_dict(item)]
    statuses = {_text(item.get("status")) for item in items}
    if str(contracts.Effort_Support_Status.UNSUPPORTED) in statuses:
        return str(contracts.Effort_Support_Status.UNSUPPORTED)
    verified = [
        item.get("value")
        for item in items
        if _text(item.get("status")) == str(contracts.Effort_Support_Status.SUPPORTED)
    ]
    required = list(required_values or [])
    if required and all(
        any(canonicalizer.canonical_equal(value, item) for item in verified) for value in required
    ):
        return str(contracts.Effort_Support_Status.SUPPORTED)
    return str(contracts.Effort_Support_Status.UNVERIFIED)


def jobs_model_id_determined(contract: Any) -> bool:
    """jobs Route_Contract의 model ID 결속이 확정됐는지(Requirement 8.8, 8.9).

    `modelIdRequired`가 boolean으로 확정되고, 필수인 경우 exact field path까지 있어야
    확정이다. 확정되지 않았으면 호출자는 jobs route를 `UNVERIFIED`로 유지한다(8.10).
    """
    if not _is_dict(contract):
        return False
    required = contract.get("modelIdRequired")
    if not isinstance(required, bool):
        return False
    if required and not request_builder.is_field_path(contract.get("modelIdFieldPath")):
        return False
    return True


def effort_injection_observed(body: Any, contract: Any, value: Any) -> dict:
    """전송 body에서 effort 주입을 관측한다(Requirement 8.16 — 정확히 1회).

    Returns:
        ``{"observed": bool, "fieldPath": [...]|None, "value": Any, "occurrences": int}``

    ``observed``가 참이려면 계약 field path에 그 value가 있고, 마지막 segment key가 body
    전체에서 **정확히 1회**만 나타나야 한다. 거짓이면 그 전송은 effort를 검증하지
    못하므로 호출자는 전송을 만들지 않는다(주입되지 않은 body의 성공은 effort 근거가
    아니다).
    """
    path = contract.get("fieldPath") if _is_dict(contract) else None
    if not request_builder.is_field_path(path) or not _is_dict(body):
        return {"observed": False, "fieldPath": None, "value": None, "occurrences": 0}
    found = request_builder.value_at_path(body, path)
    occurrences = request_builder.count_key_occurrences(body, path[-1])
    observed = (
        found is not request_builder.MISSING
        and canonicalizer.canonical_equal(found, value)
        and occurrences == 1
    )
    return {
        "observed": observed,
        "fieldPath": list(path),
        "value": None if found is request_builder.MISSING else found,
        "occurrences": occurrences,
    }


def effort_probe_selection(
    contract: Any,
    value: Any,
    *,
    capability_fingerprint: str,
) -> dict:
    """production 주입 seam이 받는 effort selection을 만든다.

    형식은 :func:`effort_settings.to_selection` 결과와 같다
    (`{modelId, route, capabilityFingerprint, value, valueType}`). 값은 계약이 evidence로
    채운 domain에서만 나온다.
    """
    source = contract if _is_dict(contract) else {}
    return {
        "modelId": _text(source.get("modelId")),
        "route": _text(source.get("routeKey")),
        "capabilityFingerprint": _text(capability_fingerprint),
        "value": copy.deepcopy(value),
        "valueType": _text(source.get("valueType")),
    }


def effort_probe_client(base: Any, binding: Any, selection: Any) -> Any:
    """Minimal_Request 전용 probe client + effort selection.

    body는 production Request_Builder(`_build_payload`·`_build_openai_payload`)가 만들고
    effort 주입도 production seam(:func:`request_builder._inject_effort`)이 한다. 수동
    body 조립 경로는 없다(Requirement 4.2, 4.3).
    """
    return probe_client_class()(base, binding, selection)


# ─────────────────────────────────────────────────────────────────
# effort 결과 → Verification_Record 투영
# ─────────────────────────────────────────────────────────────────
def effort_probe_results(items: Iterable[Mapping[str, Any]]) -> list[dict]:
    """summary 목록 또는 평탄한 결과 목록에서 value별 effort 결과만 뽑는다."""
    out: list[dict] = []
    for item in items:
        if not _is_dict(item):
            continue
        nested = item.get("results")
        if isinstance(nested, (list, tuple)):
            out.extend(dict(child) for child in nested if _is_dict(child))
        else:
            out.append(dict(item))
    return out


def recordable_effort_results(items: Iterable[Mapping[str, Any]]) -> list[dict]:
    """Verification_Record에 담을 effort 결과만 고른다.

    두 겹의 필터를 적용한다.

    1. **activation evidence 자격**(Requirement 4.4) — production Request_Builder·
       production transport·production adapter로 얻은 결과만 남긴다. 주입 transport
       (mock·진단 코드) 결과는 제외한다.
    2. **상태 보존 불변식**(design "상태 보존 불변식" 표 + Requirement 5.16, 5.17) —
       결론이 확정된 결과만 남긴다.
       - summary 상태가 `SUPPORTED`(요구 domain 전체 검증 성공)면 그 성공 결과 전부
       - 그 외에는 확정된 부정 결과(unknown field → `UNSUPPORTED`, invalid value →
         `UNVERIFIED`)만
       - 부분 검증·transient·미전송 결과는 담지 않는다 → 이전 Effort_Support_Status가
         일시 오류나 부분 검증만으로 강등되지 않는다

    부분 검증 상태에서 성공 결과만 담으면 map 집계가 `SUPPORTED`로 승격되므로, 그
    경우 성공 결과도 담지 않는다.
    """
    out: list[dict] = []
    for item in items:
        if not _is_dict(item):
            continue
        nested = item.get("results")
        summary_supported = _text(item.get("status")) == str(
            contracts.Effort_Support_Status.SUPPORTED
        )
        if not isinstance(nested, (list, tuple)):
            nested = [item]
            summary_supported = _text(item.get("status")) == str(
                contracts.Effort_Support_Status.SUPPORTED
            )
        for child in nested:
            if not _is_dict(child):
                continue
            if not child.get("evidenceEligible") or not contracts.Known_Route.has(child.get("routeKey")):
                continue
            if not child.get("stateBearing"):
                continue
            supported = _text(child.get("status")) == str(contracts.Effort_Support_Status.SUPPORTED)
            if supported and not summary_supported:
                continue  # 부분 검증은 승격 근거가 아니다(Requirement 5.17)
            out.append(dict(child))
    return out


def effort_result_for_record(result: Mapping[str, Any]) -> dict:
    """effort 결과를 Verification_Record `effortResults` 항목으로 투영한다.

    스키마 필드(:data:`contracts.REQUIRED_EFFORT_RESULT_FIELDS`)와 비민감 관측치만
    남긴다. 성공 결과의 `fieldPath`·`value`는 **실제 전송 body에서 관측한 값**이다
    (Requirement 5.12).
    """
    projected = {field: result.get(field) for field in contracts.REQUIRED_EFFORT_RESULT_FIELDS}
    observations = result.get("observations")
    if _is_dict(observations) and observations:
        projected["observations"] = copy.deepcopy(observations)
    return projected


def effort_contracts_for_record(items: Iterable[Mapping[str, Any]]) -> dict:
    """`{routeKey: Effort_Contract}` — 모든 필수 검증이 성공한 계약만 담는다.

    `SUPPORTED`가 아닌 summary의 계약은 키 자체를 넣지 않으므로
    :func:`capability_map.upsert`가 기존 계약을 그대로 유지한다(실패한 재검증이 이전
    계약을 지우지 않는다). `verifiedValues`에는 성공 probe가 실제로 확인한 value만
    담긴다(Requirement 5.12, 5.13).
    """
    out: dict[str, Any] = {}
    for item in items:
        if not _is_dict(item):
            continue
        if _text(item.get("status")) != str(contracts.Effort_Support_Status.SUPPORTED):
            continue
        if not recordable_effort_results([item]):
            continue  # activation evidence가 아니면 계약도 기록하지 않는다(4.4)
        contract = item.get("contract")
        if _is_dict(contract):
            out[str(item.get("routeKey"))] = copy.deepcopy(contract)
    return out


# ─────────────────────────────────────────────────────────────────
# EvidenceCollector
# ─────────────────────────────────────────────────────────────────
class EvidenceCollector:
    """Candidate_Label discovery · route probe 오케스트레이션 · 정제 Verification_Record.

    catalog 조회 경로를 구성해 Exact_Model_ID·Provider_String·advertised route·advertised
    effort 선언을 발견하고(:meth:`discover`), 발견된 모델의 advertised Known_Route에만
    production 경로로 Minimal_Request를 전송해 route 상태를 판정한다(:meth:`probe_route`,
    :meth:`probe_routes`). route가 성공한 뒤에는 catalog가 명시한 effort 계약만
    production 주입 seam으로 검증한다(:meth:`probe_effort`, :meth:`probe_efforts`).

    Args:
        gw: 기존 :class:`ai_engine.gateway_module.GatewayClient`(또는 동일 인터페이스).
            catalog 조회와 environment identity 확인에만 사용한다. ``None`` 가능.
        env_identity: Same_Gateway_Environment identity. 주지 않으면 ``gw``에서 유도한다.
        revision: Current_Revision(Verification_Record에 기록).
        run_id: Validation_Run ID.
        budget: :class:`ProbeBudget`(또는 ``None``). route probe가 이 객체로 조합당
            성공 1회·교정 1회·route당 prefix 교정 1회를 강제한다. ProbeBudget이 아니면
            :meth:`probe_budget`이 이 실행 전용 ProbeBudget을 새로 만든다.
        catalog_fetcher: catalog를 돌려주는 무인자 callable. 주지 않으면
            ``gw`` + ``catalog_endpoint``로 기본 경로를 구성한다.
        catalog_endpoint: Gateway catalog(목록) endpoint. 없으면 catalog endpoint 부재.
        catalog_environment: 주입한 ``catalog_fetcher``가 조회하는 환경 identity.
            ``gw``가 없을 때 Same_Gateway_Environment 확인에 사용한다.
        operator_export: catalog endpoint 부재 시 사용할 Operator_Catalog_Export.
        baseline: :func:`baseline_inspector.inspect` 결과. ``evidenceEligible``이
            거짓이면 근거 집합에서 제외한다(Requirement 1.16).
        interpreter_path: Verification_Record에 기록할 interpreter 절대 경로.
        store_obj: 영속화에 사용할 :class:`store.CapabilityStore`(선택).
        now_fn: 시각 주입(테스트용). 기본은 :func:`contracts.utc_now_iso`.
    """

    def __init__(
        self,
        gw: Any = None,
        env_identity: Mapping[str, Any] | None = None,
        revision: str = contracts.UNDETERMINED,
        run_id: str = contracts.UNDETERMINED,
        budget: Any = None,
        *,
        catalog_fetcher: Callable[[], Any] | None = None,
        catalog_endpoint: str = contracts.UNDETERMINED,
        catalog_environment: Mapping[str, Any] | None = None,
        operator_export: Any = None,
        baseline: Mapping[str, Any] | None = None,
        interpreter_path: str = contracts.UNDETERMINED,
        store_obj: store.CapabilityStore | None = None,
        now_fn: Callable[[], str] | None = None,
    ) -> None:
        self._gw = gw
        self.environment = (
            dict(env_identity) if _is_dict(env_identity) else environment_identity(gw)
        )
        self.revision = _text(revision)
        self.run_id = _text(run_id)
        self.budget = budget
        self.interpreter_path = _text(interpreter_path)
        self._catalog_fetcher = catalog_fetcher
        self._catalog_endpoint = _text(catalog_endpoint)
        self._catalog_environment = dict(catalog_environment) if _is_dict(catalog_environment) else None
        self._operator_export = operator_export
        self._baseline = dict(baseline) if _is_dict(baseline) else None
        self._store = store_obj
        self._now_fn = now_fn or contracts.utc_now_iso
        self._catalog_result: dict | None = None
        self._probe_budget: ProbeBudget | None = None

    # -- 시각 -------------------------------------------------------------
    def now(self) -> str:
        """현재 UTC ISO 8601 시각."""
        return self._now_fn()

    # -- baseline 적격성 (Requirement 1.16) -------------------------------
    @property
    def baseline_eligible(self) -> bool:
        """baseline이 근거 집합에 포함될 수 있는지(`evidenceEligible`)."""
        return bool(self._baseline) and bool(self._baseline.get("evidenceEligible"))

    def authoritative_baseline(self) -> dict | None:
        """근거로 쓸 수 있는 baseline(부적격이면 ``None``).

        `evidenceEligible: False`인 baseline은 어떤 계약 자리도 채우지 못한다. 과거
        사양·표시명·주석·Seed_Entry·mock 근거도 마찬가지로 근거 집합에 넣지 않는다.
        """
        return copy.deepcopy(self._baseline) if self.baseline_eligible else None

    # -- 초기 entry (Requirement 1.14, 1.15) -----------------------------
    def _labels(self, labels: Iterable[str] | None) -> tuple[str, ...]:
        """라벨 목록을 정규화한다(빈 값 제거, 순서 보존 중복 제거)."""
        source = CANDIDATE_LABELS if labels is None else labels
        out: list[str] = []
        for label in source:
            if not isinstance(label, str) or not label.strip():
                continue
            if label not in out:
                out.append(label)
        if not out:
            raise EvidenceCollectorError("Candidate_Label이 비어 있다")
        return tuple(out)

    def initial_entries(
        self,
        labels: Iterable[str] | None = None,
        *,
        revision: str | None = None,
    ) -> list[dict]:
        """Candidate_Label마다 정확히 하나의 초기 `UNVERIFIED` entry를 만든다.

        model ID·provider·catalog fingerprint·시각은 미확정(빈 문자열), 모든 route·
        effort 계약은 `null`로 남는다. `revision`은 기본적으로 미확정이다 — 아직
        어떤 Verification_Record도 이 entry를 뒷받침하지 않기 때문이다.
        """
        target_revision = contracts.UNDETERMINED if revision is None else _text(revision)
        return [
            contracts.new_entry(
                label,
                revision=target_revision,
                fingerprint_fn=canonicalizer.capability_fingerprint,
            )
            for label in self._labels(labels)
        ]

    def initial_map(
        self,
        labels: Iterable[str] | None = None,
        *,
        revision: str | None = None,
        now: str | None = None,
    ) -> dict:
        """초기 entry만 담은 Capability_Map을 만든다."""
        return capability_map.new_map(
            now=now or self.now(),
            entries=self.initial_entries(labels, revision=revision),
        )

    # -- catalog 획득 (Requirement 2.1, 2.5, 2.11) ------------------------
    def _build_catalog_fetcher(self) -> Callable[[], Any] | None:
        """Same_Gateway_Environment catalog 조회 경로를 구성한다(여기서 호출하지 않는다).

        기존 구현 재사용: ``openai_catalog.GatewayListSource(gw, endpoint).list_models()``.
        서명·credential·retry는 전부 기존 GatewayClient에 위임되며 신규 서명 코드·신규
        URL·신규 credential 캐시를 만들지 않는다. endpoint가 구성되지 않았으면
        ``None``을 돌려주고 discovery는 catalog endpoint 부재로 기록한다.
        """
        if self._catalog_fetcher is not None:
            return self._catalog_fetcher
        if self._gw is None or not self._catalog_endpoint:
            return None

        gw, endpoint = self._gw, self._catalog_endpoint

        def _fetch() -> Any:
            # 지연 import — 무거운 transport 의존을 모듈 import 시점에 만들지 않는다.
            from ai_engine import openai_catalog

            return openai_catalog.GatewayListSource(gw, endpoint).list_models()

        return _fetch

    def fetch_catalog(self, *, refresh: bool = False) -> dict:
        """Authoritative_Evidence가 될 catalog를 획득한다(결과를 캐시한다).

        Returns:
            ``{"available": bool, "sourceKind": str|None, "snapshot": Any|None,
            "catalogFingerprint": str, "records": int, "reasons": [코드...],
            "notes": [코드...], "environment": dict|None}``

        순서:
          1. Gateway_Catalog 조회 경로가 있으면 조회하고, 조회 환경이
             Same_Gateway_Environment와 일치해야 근거로 인정한다.
          2. catalog endpoint가 없거나 결과가 비면(현재 `GatewayListSource`는 미구현
             스텁이라 빈 목록을 돌려준다) **Operator_Catalog_Export만** 대체 근거로
             허용하고 4개 검증을 모두 적용한다.
          3. 어느 쪽도 성립하지 않으면 `available: False` — 모든 라벨은 `UNVERIFIED`로
             남고 probe도 생성되지 않는다.
        """
        if self._catalog_result is not None and not refresh:
            return self._catalog_result

        reasons: list[str] = []
        notes: list[str] = []
        result: dict[str, Any] = {
            "available": False,
            "sourceKind": None,
            "snapshot": None,
            "catalogFingerprint": contracts.UNDETERMINED,
            "records": 0,
            "reasons": reasons,
            "notes": notes,
            "environment": None,
        }
        if not self.baseline_eligible:
            notes.append(NOTE_BASELINE_EXCLUDED)

        fetcher = self._build_catalog_fetcher()
        snapshot: Any = None
        if fetcher is None:
            reasons.append(CATALOG_ENDPOINT_ABSENT)
        else:
            source_env = self._catalog_environment or (
                environment_identity(self._gw) if self._gw is not None else None
            )
            if not environment_complete(source_env) or not environment_complete(self.environment):
                reasons.append(CATALOG_ENVIRONMENT_UNKNOWN)
            elif not environment_matches(source_env, self.environment):
                reasons.append(CATALOG_ENVIRONMENT_MISMATCH)
            else:
                try:
                    snapshot = fetcher()
                except Exception as exc:  # 조회 실패는 근거 부재로만 기록한다.
                    reasons.append(CATALOG_FETCH_FAILED)
                    notes.append(_truncate(f"catalog-fetch-error:{type(exc).__name__}:{exc}"))
                else:
                    records = catalog_records(snapshot)
                    if records:
                        result["environment"] = dict(source_env)
                        return self._finish_catalog(
                            result,
                            snapshot,
                            len(records),
                            str(contracts.Source_Kind.CATALOG),
                        )
                    # 빈 결과는 endpoint 미구현과 구분할 수 없으므로 부재로 취급한다.
                    reasons.append(CATALOG_EMPTY)
                    notes.append(NOTE_CATALOG_EMPTY_TREATED_AS_ABSENT)

        # catalog endpoint 부재 → Operator_Catalog_Export만 대체 근거로 허용한다.
        if self._operator_export is None:
            self._catalog_result = result
            return result

        verdict = validate_operator_catalog_export(self._operator_export, self.environment)
        result["environment"] = verdict.get("environment")
        if not verdict["valid"]:
            reasons.extend(verdict["reasons"])
            result["reasons"] = sorted(set(reasons))
            self._catalog_result = result
            return result

        notes.append(NOTE_OPERATOR_EXPORT_USED)
        return self._finish_catalog(
            result,
            verdict["snapshot"],
            len(catalog_records(verdict["snapshot"])),
            str(contracts.Source_Kind.OPERATOR_EXPORT),
            catalog_fingerprint=verdict["catalogFingerprint"],
        )

    def _finish_catalog(
        self,
        result: dict,
        snapshot: Any,
        record_count: int,
        source_kind: str,
        *,
        catalog_fingerprint: str | None = None,
    ) -> dict:
        """catalog 획득 성공 결과를 확정한다(Catalog_Fingerprint 계산 포함)."""
        result["available"] = True
        result["sourceKind"] = source_kind
        result["snapshot"] = snapshot
        result["records"] = record_count
        result["catalogFingerprint"] = (
            catalog_fingerprint
            if catalog_fingerprint
            else canonicalizer.catalog_fingerprint(snapshot)
        )
        result["reasons"] = sorted(set(result["reasons"]))
        self._catalog_result = result
        return result

    # -- discovery (Requirement 2.2, 2.3, 2.4, 2.12, 2.18) ---------------
    def discover(self, candidate_labels: Iterable[str] | None = None) -> list[dict]:
        """Candidate_Label별 discovery 결과를 만든다(라벨마다 독립 평가).

        Returns:
            라벨 순서대로의 결과 목록. 각 항목::

                {"candidateLabel": str,
                 "found": bool,
                 "modelId": str,              # catalog 문자 그대로(미발견은 "")
                 "invocationModelId": str,    # catalog가 명시한 전송 ID(없으면 "")
                 "provider": str,             # catalog 문자 그대로(미발견은 "")
                 "sourceKind": str|None,      # CATALOG | OPERATOR_EXPORT
                 "catalogFingerprint": str,
                 "advertisedRoutes": [Known_Route...],
                 "verificationStatus": "UNVERIFIED"|"DISCOVERED",
                 "probeEligible": bool,       # Exact_Model_ID 없으면 False(2.12)
                 "matchKind": "EXACT"|"NORMALIZED_LABEL"|None,
                 "environment": dict,
                 "recordSchema": dict,        # 매칭 record의 Sanitized_Schema
                 "reasons": [코드...],        # 차단 이유
                 "notes": [코드...]}          # 비차단 기록

        미발견·근거 실패 라벨은 `UNVERIFIED`를 유지하고 `probeEligible: False`이므로
        어떤 Gateway_Probe도 생성되지 않는다. 한 라벨의 결과가 다른 라벨의 결과에
        영향을 주는 경로는 없다(라벨별 독립 평가 — Requirement 2.18).
        """
        labels = self._labels(candidate_labels)
        catalog = self.fetch_catalog()
        return [self._discover_one(label, catalog) for label in labels]

    def _discover_one(self, label: str, catalog: Mapping[str, Any]) -> dict:
        """단일 Candidate_Label의 discovery 결과(다른 라벨 정보를 쓰지 않는다)."""
        result: dict[str, Any] = {
            "candidateLabel": label,
            "found": False,
            "modelId": contracts.UNDETERMINED,
            # catalog가 명시한 전송 ID(없으면 미확정 → probe는 Exact_Model_ID를 보낸다).
            # Exact_Model_ID는 identity로 그대로 유지된다(Requirement 2.17).
            "invocationModelId": contracts.UNDETERMINED,
            "provider": contracts.UNDETERMINED,
            "sourceKind": None,
            "catalogFingerprint": contracts.UNDETERMINED,
            "advertisedRoutes": [],
            # catalog가 명시한 effort 선언 `{routeKey: 선언}`(없으면 빈 dict).
            # 이 선언만이 Effort_Contract의 field path·value type·domain 입력이다.
            "advertisedEffort": {},
            "verificationStatus": str(contracts.Verification_Status.UNVERIFIED),
            "probeEligible": False,
            "matchKind": None,
            "environment": dict(self.environment),
            "recordSchema": {},
            "reasons": list(catalog.get("reasons") or []),
            "notes": list(catalog.get("notes") or []),
        }
        if not catalog.get("available"):
            return result

        records, match_kind = find_label_records(catalog.get("snapshot"), label)
        if not records:
            result["reasons"].append(LABEL_ASSOCIATION_ABSENT)
            return result

        model_ids = {record_model_id(record) for record in records}
        model_ids.discard(contracts.UNDETERMINED)
        if len(model_ids) > 1:
            # 하나의 라벨이 서로 다른 model record를 가리키면 관계가 확정되지 않았다.
            result["reasons"].append(LABEL_ASSOCIATION_AMBIGUOUS)
            return result

        record = records[0]
        model_id = record_model_id(record)
        provider = record_provider(record)
        result["matchKind"] = match_kind
        result["recordSchema"] = store.sanitized_schema(record)
        if not model_id:
            result["reasons"].append(MODEL_ID_ABSENT)
            return result
        if not provider:
            # provider가 없으면 Activation_Gate를 통과할 수 없고 승격도 불가하다.
            result["reasons"].append(PROVIDER_ABSENT)

        result["modelId"] = model_id
        result["invocationModelId"] = record_invocation_model_id(record)
        result["provider"] = provider
        result["sourceKind"] = catalog.get("sourceKind")
        result["catalogFingerprint"] = _text(catalog.get("catalogFingerprint"))
        result["advertisedRoutes"] = record_advertised_routes(record)
        # effort 선언은 **이 record**만 근거다(다른 라벨·다른 model record와 섞이지 않는다).
        result["advertisedEffort"] = record_advertised_effort(record)
        result["found"] = True
        result["probeEligible"] = bool(model_id)
        if provider:
            result["verificationStatus"] = str(contracts.Verification_Status.DISCOVERED)
        return result

    # -- Verification_Record 생성 (Requirement 10.12~10.14) ---------------
    def to_verification_record(
        self,
        *,
        candidate_label: str,
        model_id: str = contracts.UNDETERMINED,
        provider: str = contracts.UNDETERMINED,
        catalog_fingerprint: str = contracts.UNDETERMINED,
        capability_fingerprint: str = contracts.UNDETERMINED,
        invocation_model_ids: Iterable[str] = (),
        route_results: Iterable[Mapping[str, Any]] = (),
        effort_results: Iterable[Mapping[str, Any]] = (),
        usage: Any = contracts.NOT_PROVIDED,
        cost: Any = contracts.NOT_PROVIDED,
        route_completeness: bool | None = None,
        verified_at: str | None = None,
    ) -> dict:
        """정제된 Verification_Record를 만들고 Evidence_Record_ID를 계산한다.

        정제 규칙(저장 전 강제):
          - credential·authorization·cookie·signature field는 중첩 위치까지 제거
          - raw prompt는 **Probe_ID로 대체**
          - raw request/response body는 **Sanitized_Schema로 대체**

        결과 record는 이미 정제되어 있으므로 :meth:`persist_record`가
        ``sanitize=False``로 기록한다(재정제하면 schema의 field 이름이 재작성된다).

        Raises:
            InvalidVerificationRecordError: 생성 record가 스키마를 만족하지 않는다.
        """
        sanitized_routes = [self._normalize_result(item, kind="route") for item in route_results]
        sanitized_efforts = [self._normalize_result(item, kind="effort") for item in effort_results]

        completeness = (
            bool(route_completeness)
            if route_completeness is not None
            else {item.get("routeKey") for item in sanitized_routes} >= set(contracts.KNOWN_ROUTES)
        )

        record = contracts.new_verification_record(
            run_id=self.run_id,
            revision=self.revision,
            interpreter_path=self.interpreter_path,
            environment=dict(self.environment),
            candidate_label=candidate_label,
            model_id=_text(model_id),
            provider=_text(provider),
            invocation_model_ids=[item for item in invocation_model_ids if isinstance(item, str) and item],
            catalog_fingerprint=_text(catalog_fingerprint),
            capability_fingerprint=_text(capability_fingerprint),
            route_results=(),
            effort_results=(),
            usage=usage,
            cost=cost,
            route_completeness=completeness,
            verified_at=verified_at if verified_at is not None else self.now(),
        )
        # 결과 목록이 비어 있는 상태에서 정제하므로 Sanitized_Schema가 손상되지 않는다.
        record = store.sanitize_for_persist(record)
        record["routeResults"] = sanitized_routes
        record["effortResults"] = sanitized_efforts
        record["evidenceRecordId"] = canonicalizer.evidence_record_id(record)

        reasons = contracts.validate_verification_record(record)
        if reasons:
            raise InvalidVerificationRecordError(reasons)
        return record

    def discovery_record(self, discovery: Mapping[str, Any]) -> dict:
        """discovery 결과 하나를 Verification_Record로 만든다(route·effort 결과 없음).

        catalog 근거만 있는 상태이므로 route·effort 결과는 비어 있고
        `routeCompleteness`는 거짓이다. 승격 판정은 :func:`capability_map.upsert`가
        하며, 이 record만으로는 `DISCOVERED`를 넘어설 수 없다.
        """
        return self.to_verification_record(
            candidate_label=_text(discovery.get("candidateLabel")),
            model_id=_text(discovery.get("modelId")),
            provider=_text(discovery.get("provider")),
            catalog_fingerprint=_text(discovery.get("catalogFingerprint")),
            route_results=(),
            effort_results=(),
            route_completeness=False,
        )

    def _normalize_result(self, item: Any, *, kind: str) -> dict:
        """route/effort 결과 하나를 정제·기본값 보정해 record 항목으로 만든다.

        raw body key(``body``·``requestBody``·``rawResponse`` 등)는 제거하고 그
        Sanitized_Schema를 `sanitizedSchema` 하위에 원래 key 이름으로 남긴다. 호출자가
        이미 `sanitizedSchema`를 넘겼다면 그 값이 우선한다(schema는 재작성하지 않고
        credential 키만 제거한다).
        """
        if not _is_dict(item):
            raise EvidenceCollectorError(f"{kind} 결과는 dict여야 한다: {type(item).__name__}")
        raw = dict(item)

        given_schema = raw.pop("sanitizedSchema", None)
        derived: dict[str, Any] = {}
        for key in [k for k in list(raw) if store.classify_key(k) == "BODY"]:
            derived[key] = store.sanitized_schema(raw.pop(key))

        probe = _text(raw.get("probeId"))
        cleaned = store.sanitize_for_persist(raw, probe_id=probe or store.PROBE_ID_UNKNOWN)
        cleaned["probeId"] = probe or store.PROBE_ID_UNKNOWN

        schema: dict[str, Any] = dict(derived)
        if _is_dict(given_schema):
            schema.update(strip_credential_fields(given_schema))
        cleaned["sanitizedSchema"] = schema

        defaults = _ROUTE_RESULT_DEFAULTS if kind == "route" else _EFFORT_RESULT_DEFAULTS
        for field, value in defaults.items():
            cleaned.setdefault(field, copy.deepcopy(value))
        cleaned.setdefault("routeKey", None)
        return cleaned

    # ═════════════════════════════════════════════════════════════════
    # route probe (Requirement 2.13~2.17, 4.1~4.8, 4.16~4.28)
    # ═════════════════════════════════════════════════════════════════
    def probe_budget(self, budget: Any = None) -> ProbeBudget:
        """이 실행에 쓸 :class:`ProbeBudget`을 고른다(인자 → `self.budget` → 새로 생성).

        주입된 `budget`이 ProbeBudget이 아니면 이 collector 전용 ProbeBudget을 만들어
        재사용한다. 예산 상태가 collector 밖에서 초기화되는 경로를 만들지 않는다.
        """
        for candidate in (budget, self.budget, self._probe_budget):
            if isinstance(candidate, ProbeBudget):
                return candidate
        self._probe_budget = ProbeBudget()
        return self._probe_budget

    def probe_transmission_plan(
        self,
        model: Mapping[str, Any],
        route_key: Any,
        *,
        contract: Mapping[str, Any] | None = None,
        entry: Mapping[str, Any] | None = None,
        budget: Any = None,
        probe_input: Any = None,
        transport: Any = None,
    ) -> dict:
        """route probe 전송 허용 여부와 후보 계약을 함께 반환한다(호출자 계약).

        runtime 요청 경로의 게이트는 :func:`request_builder.transmission_plan`이지만
        probe 시점의 route 상태는 **정의상 아직 `SUPPORTED`가 아니므로** 그 게이트로는
        검증 자체가 불가능하다. 그래서 probe 게이트는 catalog 광고 · Exact_Model_ID ·
        후보 계약 구성 가능 여부 · 예산만 본다. 이미 `SUPPORTED`인 route를 재검증할 때는
        runtime 게이트 결과를 `runtimeTransmit`에 함께 기록한다(감사용).

        Returns:
            ``{"transmit", "maxTransmissions", "modelId", "invocationModelId", "routeKey",
            "advertised", "contract", "budgetKey", "reason", "runtimeTransmit",
            "productionPath"}``. ``modelId``는 identity(Exact_Model_ID)이고
            ``invocationModelId``는 **실제 전송에 쓸 ID**다(catalog가 명시하지 않으면
            Exact_Model_ID와 같다).
            ``transmit``이 거짓이면 호출자는 Gateway 전송을 **생성하지 않는다**
            (``maxTransmissions == 0``) — 광고되지 않은 route의 전송 건수 0을 이 계약으로
            보장한다(Requirement 4.17).
        """
        model_map = model if _is_dict(model) else {}
        model_id = _text(model_map.get("modelId"))
        # 전송 ID는 catalog가 명시한 Invocation_Model_ID를 그대로 쓴다(없으면 Exact_Model_ID).
        # production 요청 경로가 이미 해석해 보내는 ID와 같은 값을 보내기 위한 자리이며,
        # 접두사를 만들지 않는다 — 값의 출처는 catalog record뿐이다(Requirement 2.17).
        invocation_id = record_invocation_model_id(model_map) or model_id
        profile = route_profile(route_key)
        budget_obj = self.probe_budget(budget)
        route = str(route_key) if contracts.Known_Route.has(route_key) else None
        advertised = route is not None and route in advertised_routes(model_map)
        combo = ProbeBudget.key(model_id, route or contracts.UNDETERMINED)
        candidate = (
            candidate_route_contract(route, overrides=contract) if route is not None else None
        )

        runtime_transmit: bool | None = None
        if _is_dict(entry) and route is not None:
            runtime_transmit = bool(
                request_builder.transmission_plan(entry, ctx=None, route_key=route)["transmit"]
            )

        def _plan(reason: str) -> dict:
            allowed = reason == PROBE_OK
            remaining = max(0, budget_obj.transmission_limit() - budget_obj.transmissions(combo))
            return {
                "transmit": allowed,
                "maxTransmissions": remaining if allowed else 0,
                "modelId": model_id,
                "invocationModelId": invocation_id,
                "routeKey": route,
                "advertised": advertised,
                "contract": copy.deepcopy(candidate) if allowed else candidate,
                "budgetKey": combo,
                "reason": reason,
                "runtimeTransmit": runtime_transmit,
                "productionPath": transport is None and self._gw is not None,
            }

        if profile is None or route is None:
            return _plan(PROBE_ROUTE_UNKNOWN)
        if not model_id:
            # Exact_Model_ID가 없으면 어떤 Gateway_Probe도 만들지 않는다(Requirement 2.12).
            return _plan(PROBE_MODEL_ID_ABSENT)
        if not advertised:
            return _plan(PROBE_NOT_ADVERTISED)
        if candidate is None:
            return _plan(PROBE_CONTRACT_INCOMPLETE)
        if profile.get("requiresProbeInput") and probe_input is None:
            # 모델별 body는 라벨·provider에서 만들 수 없다 → 전송하지 않는다.
            return _plan(PROBE_INPUT_UNAVAILABLE)
        budget_reason = budget_obj.block_reason(combo)
        if budget_reason != PROBE_OK:
            return _plan(budget_reason)
        if transport is None and self._gw is None:
            return _plan(PROBE_TRANSPORT_UNAVAILABLE)
        return _plan(PROBE_OK)

    def _new_route_result(self, plan: Mapping[str, Any], *, sequence: int = 0) -> dict:
        """route 결과 골격 — 판정되지 않은 자리는 보수적으로 미확정·실패로 둔다."""
        route = plan.get("routeKey")
        reason = _text(plan.get("reason"))
        status = (
            str(contracts.Route_Support_Status.NOT_ADVERTISED)
            if reason == PROBE_NOT_ADVERTISED
            else str(contracts.Route_Support_Status.UNVERIFIED)
        )
        result = {
            "routeKey": route,
            "http": False,
            "validOutput": False,
            "terminalSuccess": False,
            "allowlist": str(contracts.Allowlist_Result.UNVERIFIED),
            "status": status,
            "probeId": probe_id("route", plan.get("modelId"), route, sequence),
            "sanitizedSchema": {},
            "correctionUsed": False,
            # -- 보고서·오케스트레이션 전용 필드(Verification_Record 투영에서 제외) --
            "modelId": _text(plan.get("modelId")),
            "invocationModelId": contracts.UNDETERMINED,
            "advertised": bool(plan.get("advertised")),
            "transmissions": 0,
            "productionPath": bool(plan.get("productionPath")),
            # 전송 0건 결과 중 근거가 되는 것은 catalog가 말해준 `NOT_ADVERTISED`뿐이다.
            # 예산·입력·transport 부재로 전송하지 못한 결과는 아무것도 주장하지 않으므로
            # 기록하지 않는다(기존 route 상태를 강등시키지 않는다).
            "evidenceEligible": reason == PROBE_NOT_ADVERTISED,
            "entryStatus": None,
            "category": None,
            # 상태 근거 여부 — 상태를 바꾸지 않는 실패 범주는 record에 담지 않는다.
            "stateBearing": True,
            "reason": reason,
            "notes": [],
            "observations": {},
            "usage": contracts.NOT_PROVIDED,
            "cost": contracts.NOT_PROVIDED,
            # 확정 계약은 성공 probe만 만든다. 후보 계약은 감사용으로만 남긴다.
            "contract": None,
            "candidateContract": copy.deepcopy(plan.get("contract")),
            "budgetKey": _text(plan.get("budgetKey")),
            "runtimeTransmit": plan.get("runtimeTransmit"),
        }
        return result

    def _probe_binding(
        self,
        model: Mapping[str, Any],
        entry: Mapping[str, Any] | None,
        contract: Mapping[str, Any] | None,
        invocation_model_id: str,
    ) -> dict:
        """production Request_Builder 결속을 만든다(:func:`request_builder.bind_contract`).

        entry가 없으면 discovery 결과의 identity만 담은 최소 view로 결속한다. 라벨·
        provider 문자열은 binding에 담기지 않는다(Requirement 8.2, 8.3).
        """
        source = entry if _is_dict(entry) else {
            "modelId": _text(model.get("modelId")) if _is_dict(model) else contracts.UNDETERMINED,
            "capabilityFingerprint": contracts.UNDETERMINED,
            "invocationModelIds": [],
            "effort": {},
        }
        return request_builder.bind_contract(
            source, contract, invocation_model_id=invocation_model_id
        )

    async def probe_route(
        self,
        model: Mapping[str, Any],
        route_key: Any,
        *,
        contract: Mapping[str, Any] | None = None,
        entry: Mapping[str, Any] | None = None,
        transport: Any = None,
        budget: Any = None,
        probe_input: Any = None,
        sequence: int = 0,
    ) -> dict:
        """단일 Known_Route를 production 경로로 검증한다(Requirement 4.1~4.8).

        절차:
          1. :meth:`probe_transmission_plan`으로 전송 허용 여부를 판정한다. 광고되지
             않은 route는 `NOT_ADVERTISED`를 기록하고 **전송 0건**으로 끝난다.
          2. production Request_Builder로 Minimal_Request를 만든다
             (`bind_contract` → `EffortBoundClient` builder seam + 계약 최소 bound).
          3. 기존 transport 메서드로 전송하고, production adapter·job polling으로
             HTTP 성공·Valid_Output·Terminal_Success를 **각각 독립 결과로** 기록한다.
          4. 실패면 :func:`failure_handler.classify`로 범주를 정하고, 명시적·교정 가능
             validation error에만 동일 조합 교정을 **최대 1회** 전송한다.

        Args:
            model: :meth:`discover` 결과(또는 `modelId`·`advertisedRoutes`를 담은 dict).
            route_key: 검증할 Known_Route.
            contract: 후보 Route_Contract override(운영자·runner 제공).
            entry: 현재 Capability_Map entry(있으면 결속·runtime 게이트 감사에 사용).
            transport: 전송에 쓸 client. ``None``이면 production probe client를 만든다.
                주입하면 결과는 `evidenceEligible: False`로 표시되어 activation
                evidence에서 제외된다(Requirement 4.4 — 진단 코드·mock은 근거가 아니다).
            budget: :class:`ProbeBudget`.
            probe_input: route가 모델별 body를 요구할 때의 최소 body(예: `/invoke`).
            sequence: Probe_ID 계산에 쓰는 순번(비민감).

        Returns:
            route 결과 dict. Verification_Record용 필드
            (`routeKey`·`http`·`validOutput`·`terminalSuccess`·`allowlist`·`status`·
            `probeId`·`sanitizedSchema`·`correctionUsed`)와 보고서용 부가 필드
            (`transmissions`·`productionPath`·`evidenceEligible`·`entryStatus`·
            `category`·`reason`·`notes`·`observations`·`contract`·`usage`·`cost`)를 담는다.
        """
        model_map = model if _is_dict(model) else {}
        plan = self.probe_transmission_plan(
            model_map,
            route_key,
            contract=contract,
            entry=entry,
            budget=budget,
            probe_input=probe_input,
            transport=transport,
        )
        result = self._new_route_result(plan, sequence=sequence)
        if not plan["transmit"]:
            return result

        route = str(plan["routeKey"])
        profile = route_profile(route) or {}
        model_id = _text(plan["modelId"])
        budget_obj = self.probe_budget(budget)
        combo = _text(plan["budgetKey"])
        contract_used = copy.deepcopy(plan["contract"])
        notes: list[str] = result["notes"]
        if transport is not None:
            result["productionPath"] = False
            notes.append(NOTE_NON_PRODUCTION_TRANSPORT)

        method_name = _text(profile.get("transportRef")).split(".")[-1]
        # 첫 전송 ID는 계획이 정한 Invocation_Model_ID다(catalog가 명시하지 않았으면
        # Exact_Model_ID와 같다). 이렇게 하면 probe가 production 요청 경로와 **같은 ID**를
        # 보내므로, transport 내부 prefix 폴백에 우연히 의존하지 않는다.
        current_id = _text(plan.get("invocationModelId")) or model_id
        client = self._bind_probe_client(
            transport, transport, model_map, entry, contract_used, current_id
        )
        if not callable(getattr(client, method_name, None)):
            result["reason"] = PROBE_TRANSPORT_METHOD_ABSENT
            return result

        preview: Any = None
        while True:
            preview = self._request_preview(client, route, current_id, probe_input)
            if preview is None and route in _PREVIEW_REQUIRED_ROUTES:
                result["reason"] = PROBE_TRANSPORT_METHOD_ABSENT
                return result
            if preview is None and NOTE_REQUEST_PREVIEW_UNAVAILABLE not in notes:
                notes.append(NOTE_REQUEST_PREVIEW_UNAVAILABLE)

            outcome = await self._send_route_probe(
                client,
                route,
                model_id=current_id,
                request_body=preview,
                probe_input=probe_input,
            )
            result["transmissions"] += 1
            budget_obj.record_transmission(combo)

            judgment = self._judge_route(route, outcome, client=client)
            signals = self._probe_signals(outcome, judgment)
            success = bool(
                judgment["http"] and judgment["validOutput"] and judgment["terminalSuccess"]
            )
            category = None if success else failure_handler.classify(signals)
            error_text = _text(signals.get("errorText"))

            result["http"] = judgment["http"]
            result["validOutput"] = judgment["validOutput"]
            result["terminalSuccess"] = judgment["terminalSuccess"]
            result["category"] = category
            result["observations"] = dict(judgment.get("observations") or {})
            result["usage"] = judgment.get("usage", contracts.NOT_PROVIDED)
            result["cost"] = judgment.get("cost", contracts.NOT_PROVIDED)
            # 응답이 실제 전송 ID를 알려주면 그것을 기록한다(transport 내부 prefix 교정은
            # collector가 넘긴 `current_id`와 다를 수 있다 — Requirement 2.17). 관측이
            # 없으면 기존 동작대로 전송에 쓴 ID를 남긴다(값을 만들지 않는다).
            result["invocationModelId"] = (
                _text(result["observations"].get("invocationModelId")) or current_id
            )
            result["sanitizedSchema"] = {
                "request": store.sanitized_schema(preview),
                "response": store.sanitized_schema(judgment.get("responseView")),
            }
            for note in judgment.get("notes") or []:
                if note not in notes:
                    notes.append(note)
            if error_text:
                result["reason"] = failure_handler.safe_cause(error_text)

            if success:
                budget_obj.record_success(combo)
                break

            correction = self._correction_plan(
                route_key=route,
                profile=profile,
                contract=contract_used,
                model_id=model_id,
                current_id=current_id,
                category=category,
                error_text=error_text,
                budget=budget_obj,
                combo=combo,
                correction_used=result["correctionUsed"],
            )
            if correction is None:
                if (
                    category == failure_handler.REQUEST_VALIDATION
                    and not result["correctionUsed"]
                    and NOTE_CORRECTION_UNAVAILABLE not in notes
                ):
                    # 교정 가능한 수단이 없다 → 추가 전송을 만들지 않는다.
                    notes.append(NOTE_CORRECTION_UNAVAILABLE)
                break
            if correction["kind"] == _CORRECTION_PREFIX_DELEGATED:
                # 기존 transport가 내부에서 prefix 교정을 이미 1회 수행한다 — 추가 전송 없음.
                if NOTE_PREFIX_CORRECTION_DELEGATED not in notes:
                    notes.append(NOTE_PREFIX_CORRECTION_DELEGATED)
                break
            if correction["kind"] == _CORRECTION_PREFIX:
                current_id = correction["modelId"]
                budget_obj.record_prefix_correction(model_id, route)
                notes.append(NOTE_PREFIX_CORRECTION_APPLIED)
            else:  # _CORRECTION_DROP_BOUND
                contract_used = without_min_output_bound(contract_used)
                notes.append(NOTE_MIN_OUTPUT_BOUND_DROPPED)
            budget_obj.record_correction(combo)
            result["correctionUsed"] = True
            client = self._bind_probe_client(
                client, transport, model_map, entry, contract_used, current_id
            )

        # 상태 판정 — 세 독립 결과와 실패 범주만으로 결정한다.
        result["status"] = route_support_status(
            advertised=True,
            http=result["http"],
            valid_output=result["validOutput"],
            terminal_success=result["terminalSuccess"],
            category=result["category"],
        )
        result["allowlist"] = allowlist_result(
            valid_output=result["validOutput"], category=result["category"]
        )
        if result["category"] == failure_handler.ALLOWLIST:
            # 명시적 allowlist 거부 → Candidate_Model을 REJECTED로 기록한다(4.19).
            result["entryStatus"] = str(contracts.Verification_Status.REJECTED)

        success_status = result["status"] == str(contracts.Route_Support_Status.SUPPORTED)
        if success_status:
            self._finalize_contract(contract_used, profile, preview, notes)
            if route == str(contracts.Known_Route.OPENAI_RESPONSES_JOBS) and not (
                jobs_model_id_determined(contract_used)
            ):
                # jobs route의 model ID 필수 여부·exact path는 성공 probe로만 확정한다.
                # 확정되지 않았으면 route를 `UNVERIFIED`로 유지한다(Requirement 8.8~8.10).
                result["status"] = str(contracts.Route_Support_Status.UNVERIFIED)
                success_status = False
                if NOTE_JOBS_MODEL_ID_UNDETERMINED not in notes:
                    notes.append(NOTE_JOBS_MODEL_ID_UNDETERMINED)
        result["contract"] = contract_used if success_status else None
        # 전송이 일어난 결과는 production 경로로 얻은 것만 activation evidence다(4.4).
        result["evidenceEligible"] = bool(result["productionPath"])
        # transient·quota·인증·validation·unknown은 상태를 바꾸지 않는다(design "상태 보존
        # 불변식" 표). 그런 결과는 record에 담지 않아 이전 route 상태를 강등시키지 않는다.
        result["stateBearing"] = result["category"] not in STATE_PRESERVING_CATEGORIES
        return result

    def _bind_probe_client(
        self,
        client: Any,
        transport: Any,
        model: Mapping[str, Any],
        entry: Mapping[str, Any] | None,
        contract: Mapping[str, Any] | None,
        invocation_model_id: str,
    ) -> Any:
        """계약을 결속한 probe client를 만든다(주입 transport는 결속만 갱신한다).

        production 경로에서는 :func:`probe_client`가 매번 새 client를 만들고, 주입된
        transport에서는 builder seam이 같은 계약을 보도록 `_contract`만 갱신한다.
        """
        binding = self._probe_binding(model, entry, contract, invocation_model_id)
        if transport is None:
            return probe_client(self._gw, binding)
        if hasattr(client, "_contract"):
            client._contract = binding
        return client

    def _request_preview(
        self, client: Any, route_key: str, model_id: str, probe_input: Any
    ) -> Any:
        """production builder seam으로 Minimal_Request body를 만든다(전송하지 않는다).

        이 body는 그대로 전송되거나(동기 Responses), 전송 메서드가 내부에서 같은 seam으로
        동일하게 재생성한다. Sanitized_Schema와 model ID 결속 관측의 근거로 쓴다.
        """
        messages = probe_messages()
        if route_key in (
            str(contracts.Known_Route.CONVERSE),
            str(contracts.Known_Route.SSE_STREAM),
        ):
            builder = getattr(client, "_build_payload", None)
            return builder(model_id, messages, PROBE_SYSTEM_PROMPT) if callable(builder) else None
        if route_key == str(contracts.Known_Route.OPENAI_RESPONSES):
            builder = getattr(client, "_build_openai_payload", None)
            return builder(model_id, messages, PROBE_SYSTEM_PROMPT) if callable(builder) else None
        if route_key == str(contracts.Known_Route.OPENAI_RESPONSES_JOBS):
            builder = getattr(client, "_build_openai_payload", None)
            if not callable(builder):
                return None
            body = builder(model_id, messages, PROBE_SYSTEM_PROMPT)
            apply_model_id = getattr(client, "_apply_jobs_model_id", None)
            return apply_model_id(body, model_id) if callable(apply_model_id) else body
        return probe_input  # `/invoke`는 운영자가 준 최소 body가 곧 요청 body다

    async def _send_route_probe(
        self,
        client: Any,
        route_key: str,
        *,
        model_id: str,
        request_body: Any,
        probe_input: Any,
    ) -> dict:
        """기존 transport 메서드로 Minimal_Request 1건을 전송한다(신규 전송 코드 없음).

        예외는 잡아 outcome에 담는다(분류는 :func:`failure_handler.classify`가 한다).
        """
        outcome: dict[str, Any] = {"raw": None, "events": None, "jobId": None, "poll": None, "error": None}
        messages = probe_messages()
        try:
            if route_key == str(contracts.Known_Route.CONVERSE):
                outcome["raw"] = await client.converse(model_id, messages, PROBE_SYSTEM_PROMPT)
            elif route_key == str(contracts.Known_Route.INVOKE):
                outcome["raw"] = await client.invoke_model(model_id, probe_input)
            elif route_key == str(contracts.Known_Route.OPENAI_RESPONSES):
                outcome["raw"] = await client.openai_responses_call(request_body)
            elif route_key == str(contracts.Known_Route.OPENAI_RESPONSES_JOBS):
                job_id = await client.openai_responses_job_submit(
                    model_id, messages, PROBE_SYSTEM_PROMPT
                )
                outcome["jobId"] = job_id
                if isinstance(job_id, str) and job_id:
                    outcome["poll"] = await client._openai_poll_job(job_id)
            elif route_key == str(contracts.Known_Route.SSE_STREAM):
                outcome["events"] = await self._collect_sse_events(
                    client, model_id, messages
                )
        except Exception as exc:  # 전송·폴링 실패는 판정 입력으로만 쓴다.
            outcome["error"] = exc
        return outcome

    async def _collect_sse_events(self, client: Any, model_id: str, messages: list) -> list:
        """SSE 이벤트를 terminal event까지(또는 상한까지) 수집한다."""
        events: list = []
        stream = client.stream_sse_realtime(model_id, messages, PROBE_SYSTEM_PROMPT)
        try:
            async for event in stream:
                if _is_dict(event):
                    events.append(event)
                if _is_dict(event) and _text(event.get("type")) in SSE_TERMINAL_EVENT_TYPES:
                    break
                if len(events) >= SSE_MAX_EVENTS:
                    break
        finally:
            closer = getattr(stream, "aclose", None)
            if callable(closer):
                await closer()
        return events

    def _judge_route(self, route_key: str, outcome: Mapping[str, Any], *, client: Any = None) -> dict:
        """route별 판정기를 호출한다(design "route별 판정 근거" 표의 기존 심볼만 사용)."""
        error = outcome.get("error")
        if route_key == str(contracts.Known_Route.CONVERSE):
            judgment = judge_converse(outcome.get("raw"))
            judgment["responseView"] = outcome.get("raw")
        elif route_key == str(contracts.Known_Route.INVOKE):
            extractor = getattr(client, "_extract_invoke_result", None)
            judgment = judge_invoke(outcome.get("raw"), extractor=extractor)
            judgment["responseView"] = outcome.get("raw")
        elif route_key == str(contracts.Known_Route.OPENAI_RESPONSES):
            judgment = judge_openai_sync(outcome.get("raw"))
            judgment["responseView"] = outcome.get("raw")
        elif route_key == str(contracts.Known_Route.OPENAI_RESPONSES_JOBS):
            judgment = judge_openai_jobs(outcome.get("jobId"), outcome.get("poll"))
            judgment["responseView"] = outcome.get("poll")
        else:
            judgment = judge_sse(outcome.get("events"))
            judgment["responseView"] = {"events": outcome.get("events") or []}

        if error is not None:
            # 예외로 끝난 전송은 어떤 조건도 충족하지 않는다(보수적 판정). 응답이 없는
            # 상태에서 파생된 신호(empty/partial output 등)는 버리고 예외만 신호로 쓴다.
            judgment["http"] = False
            judgment["validOutput"] = False
            judgment["terminalSuccess"] = False
            judgment["signals"] = {}
        judgment.setdefault("usage", contracts.NOT_PROVIDED)
        judgment.setdefault("cost", contracts.NOT_PROVIDED)
        return judgment

    def _probe_signals(self, outcome: Mapping[str, Any], judgment: Mapping[str, Any]) -> dict:
        """`failure_handler.classify` 입력 신호를 만든다(신규 판정 규칙 없음).

        예외가 있으면 예외 타입·메시지가 신호의 유일한 원문이다(응답 부재에서 파생된
        문자열이 Gateway가 준 원인을 가리지 않게 한다).
        """
        signals = dict(judgment.get("signals") or {})
        error = outcome.get("error")
        if error is not None:
            signals["error"] = error
            signals["errorText"] = _truncate(f"{type(error).__name__}: {error}")
        return signals

    def _correction_plan(
        self,
        *,
        route_key: str,
        profile: Mapping[str, Any],
        contract: Mapping[str, Any] | None,
        model_id: str,
        current_id: str,
        category: str | None,
        error_text: str,
        budget: ProbeBudget,
        combo: str,
        correction_used: bool,
    ) -> dict | None:
        """동일 조합 교정 재시도를 계획한다(불가하면 ``None`` — Requirement 2.16, 4.25).

        한도 판정은 기존 :func:`failure_handler.plan_recovery`(`request-validation` →
        교정 최대 1회)와 :class:`ProbeBudget`이 함께 강제한다. 교정 수단은 두 가지뿐이다.

          1. **prefix 형태 교정** — 기존 `_is_prefix_form_error` 신호에만, 기존
             `_has_region_prefix`·`_strip_region_prefix` 규칙으로 반대 형태 1회. 내부
             폴백이 있는 route는 전송하지 않고 그 사실만 기록한다.
          2. **최소 bound 제거** — 우리가 덧붙인 최소 output bound를 걷어내고 production
             baseline body로 1회.
        """
        if correction_used or category != failure_handler.REQUEST_VALIDATION:
            return None
        recovery = failure_handler.plan_recovery(
            None,
            category,
            {"correctionRetryCount": 1 if correction_used else 0, "route": route_key},
        )
        if not recovery["retryWithCorrection"] or not budget.may_correct(combo):
            return None

        if failure_handler.default_hooks().is_prefix_form_error(error_text):
            if profile.get("prefixCorrectionOwner") == PREFIX_OWNER_TRANSPORT:
                return {"kind": _CORRECTION_PREFIX_DELEGATED}
            if budget.may_prefix_correct(model_id, route_key):
                corrected = corrected_model_id(current_id)
                if corrected and corrected != current_id:
                    return {"kind": _CORRECTION_PREFIX, "modelId": corrected}
        if min_output_bound_fields(contract):
            return {"kind": _CORRECTION_DROP_BOUND}
        return None

    def _finalize_contract(
        self,
        contract: dict,
        profile: Mapping[str, Any],
        request_body: Any,
        notes: list[str],
    ) -> None:
        """성공 probe가 **실제 전송한 body**로 계약의 결속을 확정한다(추론 없음).

        - `modelIdRequired`가 미확정(`None`)인 route(예: jobs)는 body에서 관측한 값으로만
          채운다. 이미 확정된 값은 덮어쓰지 않는다.
        - 선언된 model ID·message field path가 실제 body에 없으면 note만 남긴다
          (route 상태 판정은 세 독립 결과만으로 한다).
        """
        if not _is_dict(contract) or not _is_dict(request_body):
            return
        if contract.get("modelIdRequired") is None:
            observed = observe_model_id_binding(request_body, profile)
            if observed["observed"]:
                contract["modelIdRequired"] = observed["modelIdRequired"]
                contract["modelIdFieldPath"] = observed["modelIdFieldPath"]
                if NOTE_MODEL_ID_BINDING_OBSERVED not in notes:
                    notes.append(NOTE_MODEL_ID_BINDING_OBSERVED)
        model_path = contract.get("modelIdFieldPath")
        if contract.get("modelIdRequired") and request_builder.is_field_path(model_path):
            if not request_builder.path_exists(request_body, model_path):
                if NOTE_MODEL_ID_PATH_UNVERIFIED not in notes:
                    notes.append(NOTE_MODEL_ID_PATH_UNVERIFIED)
        message_path = contract.get("messageFieldPath")
        if request_builder.is_field_path(message_path):
            if not request_builder.path_exists(request_body, message_path):
                if NOTE_MESSAGE_PATH_UNVERIFIED not in notes:
                    notes.append(NOTE_MESSAGE_PATH_UNVERIFIED)

    async def probe_routes(
        self,
        model: Mapping[str, Any],
        *,
        route_keys: Iterable[str] | None = None,
        contracts_by_route: Mapping[str, Any] | None = None,
        entry: Mapping[str, Any] | None = None,
        transport: Any = None,
        budget: Any = None,
        probe_inputs: Mapping[str, Any] | None = None,
    ) -> list[dict]:
        """모든 Known_Route(또는 지정 route)를 순서대로 검증한다.

        광고되지 않은 route도 `NOT_ADVERTISED`로 **기록**하므로 route 상태 완전성
        (Requirement 4.27)을 이 결과 목록만으로 판정할 수 있다. 광고되지 않은 route의
        전송 건수는 0이다.
        """
        targets = [
            route
            for route in (contracts.KNOWN_ROUTES if route_keys is None else route_keys)
            if contracts.Known_Route.has(route)
        ]
        overrides = contracts_by_route if _is_dict(contracts_by_route) else {}
        inputs = probe_inputs if _is_dict(probe_inputs) else {}
        results: list[dict] = []
        for index, route in enumerate(targets):
            results.append(
                await self.probe_route(
                    model,
                    route,
                    contract=overrides.get(route),
                    entry=entry,
                    transport=transport,
                    budget=budget,
                    probe_input=inputs.get(route),
                    sequence=index,
                )
            )
        return results

    # ═════════════════════════════════════════════════════════════════
    # effort probe (Requirement 5.1~5.20, 8.8, 8.9, 8.10)
    # ═════════════════════════════════════════════════════════════════
    def effort_probe_fingerprint(self, model: Any = None, entry: Any = None) -> str:
        """effort probe binding에 쓸 Capability_Fingerprint 자리 값.

        entry나 discovery 결과가 현재 fingerprint를 알고 있으면 그 값을 쓴다. 첫 검증처럼
        아직 fingerprint가 없는 후보에서는 **binding 안에서만 쓰는** 결정론적 tuple
        token을 만든다. production Request_Builder가 tuple(model·route·fingerprint) 일치를
        요구하기 때문에 필요한 임시 결속값이며, Capability_Map·Verification_Record에는
        기록되지 않는다(entry의 fingerprint는 언제나 canonicalizer가 계산한다).
        """
        for source in (entry, model):
            if _is_dict(source):
                value = _text(source.get("capabilityFingerprint"))
                if value:
                    return value
        model_id = _text(model.get("modelId")) if _is_dict(model) else contracts.UNDETERMINED
        return probe_id("effort-binding", model_id)

    def effort_transmission_plan(
        self,
        model: Mapping[str, Any],
        route_key: Any,
        contract: Mapping[str, Any] | None = None,
        *,
        route_result: Mapping[str, Any] | None = None,
        route_contract: Mapping[str, Any] | None = None,
        entry: Mapping[str, Any] | None = None,
        budget: Any = None,
        transport: Any = None,
    ) -> dict:
        """effort probe 전송 허용 여부·검증 value 목록·후보 계약을 반환한다.

        baseline 성공 확인(Requirement 5.7, 5.8)은 전송이 필요할 수 있어 비동기
        :meth:`probe_effort`에서 수행한다. 이 계획은 전송 없이 판정할 수 있는 조건만 본다.

        Returns:
            ``{"transmit", "maxTransmissions", "modelId", "routeKey", "advertised",
            "contract", "routeContract", "values", "capabilityFingerprint",
            "contractMissing", "budgetKeys", "productionPath", "reason"}``.
            ``transmit``이 거짓이면 호출자는 Gateway 전송을 **생성하지 않는다**
            (``maxTransmissions == 0``) — 계약 결손 시 전송 0건을 이 계약으로 보장한다
            (Requirement 5.6).
        """
        model_map = model if _is_dict(model) else {}
        model_id = _text(model_map.get("modelId"))
        route = str(route_key) if contracts.Known_Route.has(route_key) else None
        budget_obj = self.probe_budget(budget)
        advertised = route is not None and route in advertised_routes(model_map)
        candidate = (
            candidate_effort_contract_for(model_map, route, override=contract)
            if route is not None
            else None
        )
        values = effort_probe_values(candidate)
        fingerprint = self.effort_probe_fingerprint(model_map, entry)

        if _is_dict(route_contract):
            route_contract_used: Any = copy.deepcopy(route_contract)
        elif _is_dict(route_result) and _is_dict(route_result.get("contract")):
            # 성공한 baseline probe가 확정한 계약을 그대로 쓴다(추론 없음).
            route_contract_used = copy.deepcopy(route_result["contract"])
        else:
            route_contract_used = candidate_route_contract(route) if route is not None else None

        missing = (
            contracts.effort_contract_missing(candidate)
            if candidate is not None
            else list(contracts.REQUIRED_EFFORT_CONTRACT_FIELDS)
        )

        def _plan(reason: str) -> dict:
            allowed = reason == PROBE_OK
            return {
                "transmit": allowed,
                "maxTransmissions": len(values) if allowed else 0,
                "modelId": model_id,
                "routeKey": route,
                "advertised": advertised,
                "contract": copy.deepcopy(candidate),
                "routeContract": copy.deepcopy(route_contract_used),
                # domain이 요구하는 value 목록(보고용). 전송은 `transmit`이 참일 때만 한다.
                "values": list(values),
                "capabilityFingerprint": fingerprint,
                "contractMissing": list(missing),
                "budgetKeys": [ProbeBudget.key(model_id, route, value) for value in values]
                if allowed
                else [],
                "productionPath": transport is None and self._gw is not None,
                "reason": reason,
            }

        if route is None:
            return _plan(EFFORT_PROBE_ROUTE_UNKNOWN)
        if not model_id:
            # Exact_Model_ID가 없으면 계약을 결속할 수 없다(Requirement 2.12, 5.1).
            return _plan(EFFORT_PROBE_MODEL_ID_ABSENT)
        if not advertised:
            return _plan(EFFORT_PROBE_NOT_ADVERTISED)
        if route not in EFFORT_INJECTABLE_ROUTES:
            # production 주입 seam이 없는 route는 effort를 검증할 수 없다(수동 body 금지).
            return _plan(EFFORT_PROBE_ROUTE_NOT_INJECTABLE)
        binding_reason = effort_contract_binding_reason(
            candidate, model_id=model_id, route_key=route
        )
        if binding_reason != PROBE_OK:
            # field path·value type·complete domain 중 하나라도 결손 → 전송 0건, UNVERIFIED.
            return _plan(binding_reason)
        if not values:
            return _plan(EFFORT_PROBE_DOMAIN_INCOMPLETE)
        if route == str(contracts.Known_Route.OPENAI_RESPONSES_JOBS) and not (
            jobs_model_id_determined(route_contract_used)
        ):
            # jobs route는 model ID 결속이 성공 probe로 확정된 뒤에만 effort를 검증한다.
            return _plan(EFFORT_PROBE_JOBS_MODEL_ID_UNDETERMINED)
        if transport is None and self._gw is None:
            return _plan(EFFORT_PROBE_TRANSPORT_UNAVAILABLE)
        if all(
            not budget_obj.may_transmit(ProbeBudget.key(model_id, route, value)) for value in values
        ):
            return _plan(EFFORT_PROBE_BUDGET_EXHAUSTED)
        return _plan(PROBE_OK)

    def _new_effort_summary(
        self,
        plan: Mapping[str, Any],
        *,
        route_result: Mapping[str, Any] | None = None,
        sequence: int = 0,
    ) -> dict:
        """effort 결과 골격 — 판정되지 않은 자리는 보수적으로 미확정으로 둔다."""
        contract = plan.get("contract")
        route = plan.get("routeKey")
        field_path = contract.get("fieldPath") if _is_dict(contract) else None
        return {
            "routeKey": route,
            "modelId": _text(plan.get("modelId")),
            "invocationModelId": contracts.UNDETERMINED,
            "status": str(contracts.Effort_Support_Status.UNVERIFIED),
            "fieldPath": list(field_path) if request_builder.is_field_path(field_path) else None,
            "valueType": _text(contract.get("valueType")) if _is_dict(contract) else contracts.UNDETERMINED,
            "domainKind": _text(contract.get("domainKind")) if _is_dict(contract) else contracts.UNDETERMINED,
            "requiredValues": list(plan.get("values") or []),
            "verifiedValues": [],
            "baselineSucceeded": False,
            "baselineSource": None,
            # baseline 확인을 위해 수행한 route probe 결과(route 경로로만 기록한다).
            "baselineResult": None,
            "results": [],
            "transmissions": 0,
            "productionPath": bool(plan.get("productionPath")),
            "evidenceEligible": False,
            "stateBearing": False,
            # 확정 계약은 모든 필수 검증이 성공했을 때만 채운다.
            "contract": None,
            "candidateContract": copy.deepcopy(contract),
            "contractMissing": list(plan.get("contractMissing") or []),
            # 입력 route 상태(effort 결과가 이 값을 바꾸지 않음을 감사할 수 있게 남긴다).
            "baseRouteStatus": _text(route_result.get("status")) if _is_dict(route_result) else contracts.UNDETERMINED,
            "probeId": probe_id("effort", plan.get("modelId"), route, sequence),
            "reason": _text(plan.get("reason")),
            "notes": [],
            "usage": contracts.NOT_PROVIDED,
            "cost": contracts.NOT_PROVIDED,
        }

    def _new_effort_value_result(
        self,
        plan: Mapping[str, Any],
        value: Any,
        *,
        index: int,
        sequence: int,
        baseline_succeeded: bool,
        invocation_model_id: str,
    ) -> dict:
        """value 하나의 effort 결과 골격(전송 전 상태 — 아무것도 주장하지 않는다)."""
        contract = plan.get("contract")
        route = plan.get("routeKey")
        field_path = contract.get("fieldPath") if _is_dict(contract) else None
        return {
            # -- Verification_Record 스키마 필드 --
            "routeKey": route,
            "fieldPath": list(field_path) if request_builder.is_field_path(field_path) else None,
            "value": copy.deepcopy(value),
            "status": str(contracts.Effort_Support_Status.UNVERIFIED),
            "probeId": probe_id("effort", plan.get("modelId"), route, value, sequence, index),
            "sanitizedSchema": {},
            "baselineSucceeded": bool(baseline_succeeded),
            # -- 보고서·오케스트레이션 전용 필드(record 투영에서 제외) --
            "modelId": _text(plan.get("modelId")),
            "invocationModelId": _text(invocation_model_id),
            "http": False,
            "validOutput": False,
            "terminalSuccess": False,
            "category": None,
            "rejection": None,
            "transmitted": False,
            "productionPath": bool(plan.get("productionPath")),
            "evidenceEligible": False,
            "stateBearing": False,
            "reason": EFFORT_PROBE_NOT_PROBED,
            "notes": [],
            "observations": {},
            "usage": contracts.NOT_PROVIDED,
            "cost": contracts.NOT_PROVIDED,
        }

    def _effort_binding(
        self,
        model: Mapping[str, Any],
        route_contract: Any,
        effort_contract: Any,
        invocation_model_id: str,
        *,
        capability_fingerprint: str,
    ) -> dict:
        """production 주입 seam이 요구하는 Request_Binding을 만든다.

        binding의 effort entry 상태를 `SUPPORTED`로 두는 이유: production
        Request_Builder는 tuple·domain이 일치하는 `SUPPORTED` 계약에만 값을 정확히 1회
        주입한다(Requirement 8.16). probe는 **그 주입 경로 자체를 검증**하므로 후보 계약을
        그 자리에 넣어 production 경로를 그대로 태운다. 이 binding은 요청 1건 동안만
        존재하고 Capability_Map·Verification_Record에 기록되지 않는다 — 실제
        Effort_Support_Status는 probe 결과만이 정한다.

        라벨·provider 문자열은 binding에 담기지 않는다(Requirement 8.2, 8.3).
        """
        route_key = _text(route_contract.get("routeKey")) if _is_dict(route_contract) else contracts.UNDETERMINED
        source = {
            "modelId": _text(model.get("modelId")) if _is_dict(model) else contracts.UNDETERMINED,
            "capabilityFingerprint": _text(capability_fingerprint),
            "invocationModelIds": [],
            "effort": {
                route_key: {
                    "status": str(contracts.Effort_Support_Status.SUPPORTED),
                    "contract": copy.deepcopy(effort_contract),
                    "evidenceRef": None,
                }
            },
        }
        return request_builder.bind_contract(
            source, route_contract, invocation_model_id=invocation_model_id
        )

    def _bind_effort_client(
        self, client: Any, transport: Any, binding: Any, selection: Any
    ) -> Any:
        """effort selection을 결속한 probe client를 만든다.

        production 경로에서는 :func:`effort_probe_client`가 새 client를 만들고, 주입된
        transport에서는 builder seam이 같은 계약·선택을 보도록 결속만 갱신한다.
        """
        if transport is None:
            return effort_probe_client(self._gw, binding, selection)
        if hasattr(client, "_contract"):
            client._contract = binding
        if hasattr(client, "_effort"):
            client._effort = selection
        return client

    async def _confirm_effort_baseline(
        self,
        model: Mapping[str, Any],
        route_key: str,
        *,
        route_result: Mapping[str, Any] | None = None,
        route_contract: Any = None,
        entry: Mapping[str, Any] | None = None,
        transport: Any = None,
        budget: Any = None,
        probe_input: Any = None,
        sequence: int = 0,
    ) -> dict:
        """무-effort Baseline_Request_Body 성공을 확인한다(Requirement 5.7, 5.8).

        확인 순서(전송을 최소화한다):
          1. 같은 Exact_Model_ID·Known_Route의 route probe 결과가 주어졌으면 그 결과를
             그대로 채택한다(추가 전송 0건). 그 결과가 실패면 effort probe를 전송하지
             않는다.
          2. 이 실행의 예산에 ∅ 조합(effort 미주입) 성공이 이미 기록돼 있으면 baseline은
             성공한 것이다(:meth:`ProbeBudget.baseline_succeeded`).
          3. 둘 다 없으면 :meth:`probe_route`로 무-effort probe를 수행한다(예산·판정·
             production 경로를 전부 재사용한다).

        Returns:
            ``{"succeeded", "source", "invocationModelId", "routeContract", "result"}``.
            ``result``는 3번 경로에서 수행한 route 결과이며 route 기록 경로로만 담는다.
        """
        model_map = model if _is_dict(model) else {}
        model_id = _text(model_map.get("modelId"))
        budget_obj = self.probe_budget(budget)

        if _is_dict(route_result) and _text(route_result.get("routeKey")) == route_key:
            same_model = _text(route_result.get("modelId")) in ("", model_id)
            succeeded = bool(
                same_model
                and route_result.get("http")
                and route_result.get("validOutput")
                and route_result.get("terminalSuccess")
            )
            return {
                "succeeded": succeeded,
                "source": BASELINE_FROM_ROUTE_RESULT,
                "invocationModelId": _text(route_result.get("invocationModelId")) or model_id,
                "routeContract": route_result.get("contract"),
                "result": None,
            }

        if budget_obj.baseline_succeeded(model_id, route_key):
            return {
                "succeeded": True,
                "source": BASELINE_FROM_BUDGET,
                "invocationModelId": model_id,
                "routeContract": None,
                "result": None,
            }

        probed = await self.probe_route(
            model_map,
            route_key,
            contract=route_contract if _is_dict(route_contract) else None,
            entry=entry,
            transport=transport,
            budget=budget_obj,
            probe_input=probe_input,
            sequence=sequence,
        )
        succeeded = bool(
            probed.get("http") and probed.get("validOutput") and probed.get("terminalSuccess")
        )
        return {
            "succeeded": succeeded,
            "source": BASELINE_FROM_PROBE,
            "invocationModelId": _text(probed.get("invocationModelId")) or model_id,
            "routeContract": probed.get("contract"),
            "result": probed,
        }

    async def probe_effort(
        self,
        model: Mapping[str, Any],
        route_key: Any,
        contract: Mapping[str, Any] | None = None,
        *,
        route_result: Mapping[str, Any] | None = None,
        route_contract: Mapping[str, Any] | None = None,
        entry: Mapping[str, Any] | None = None,
        transport: Any = None,
        budget: Any = None,
        probe_input: Any = None,
        sequence: int = 0,
    ) -> dict:
        """단일 Known_Route의 effort 계약을 production 경로로 검증한다.

        절차:
          1. :meth:`effort_transmission_plan` — field path·value type·complete domain 중
             하나라도 결손이면 **전송 없이** `UNVERIFIED`를 유지한다(Requirement 5.6).
          2. :meth:`_confirm_effort_baseline` — 동일 model·route의 무-effort
             Baseline_Request_Body 성공을 먼저 확인하고, 실패하면 effort Gateway_Probe를
             전송하지 않는다(Requirement 5.7, 5.8).
          3. domain이 요구하는 value만 각각 하나의 Minimal_Request로 검증한다
             (enum 각 1회 / range 경계 각 1회 / 상·하한 동일 시 1회 — 5.9~5.11).
             body는 production Request_Builder가 만들고 effort 주입도 production seam이
             한다. 주입이 관측되지 않으면 그 전송은 근거가 아니므로 만들지 않는다.
          4. 성공한 value의 **실제 field path와 실제 value**를 결과에 기록하고(5.12),
             요구된 모든 검증이 성공했을 때만 `SUPPORTED`로 집계한다(5.13).
             unknown field → `UNSUPPORTED`(5.14), invalid value·transient·부분 검증 →
             `UNVERIFIED`(5.15~5.17).

        effort 결과는 route 상태를 담지 않는다. 반환 dict에는 route 결과 필드가 없고
        :func:`capability_map.upsert`의 effort 반영 경로도 route 상태를 건드리지 않으므로,
        effort 실패가 base Route_Support_Status를 강등할 경로가 구조적으로 없다
        (Requirement 5.20).

        Args:
            model: :meth:`discover` 결과(`modelId`·`advertisedRoutes`·`advertisedEffort`).
            route_key: 검증할 Known_Route.
            contract: 후보 Effort_Contract override(운영자·runner 제공). 주지 않으면
                discovery가 읽은 catalog effort 선언에서 만든다.
            route_result: 같은 model·route의 route probe 결과(baseline 확인·계약 재사용).
            route_contract: 확정 Route_Contract override.
            entry: 현재 Capability_Map entry(fingerprint 결속·runtime 게이트 감사).
            transport: 전송 client. ``None``이면 production probe client를 만든다.
                주입하면 결과는 `evidenceEligible: False`가 되어 activation evidence에서
                제외된다(Requirement 4.4).
            budget: :class:`ProbeBudget`.
            probe_input: baseline 확인에서 route가 모델별 body를 요구할 때의 최소 body.
            sequence: Probe_ID 계산에 쓰는 순번(비민감).

        Returns:
            effort 결과 summary dict. `results`에 value별 결과(Verification_Record용 필드
            포함)가 담긴다.
        """
        model_map = model if _is_dict(model) else {}
        plan = self.effort_transmission_plan(
            model_map,
            route_key,
            contract,
            route_result=route_result,
            route_contract=route_contract,
            entry=entry,
            budget=budget,
            transport=transport,
        )
        summary = self._new_effort_summary(plan, route_result=route_result, sequence=sequence)
        notes: list[str] = summary["notes"]
        if transport is not None:
            notes.append(NOTE_NON_PRODUCTION_TRANSPORT)
        if not plan["transmit"]:
            return self._finish_effort_summary(summary, route_result)

        route = str(plan["routeKey"])
        model_id = _text(plan["modelId"])
        budget_obj = self.probe_budget(budget)
        effort_contract = copy.deepcopy(plan["contract"])
        route_contract_used = copy.deepcopy(plan["routeContract"])
        values = list(plan["values"])
        profile = route_profile(route) or {}
        method_name = _text(profile.get("transportRef")).split(".")[-1]

        # 1) 무-effort Baseline_Request_Body 성공 확인 (Requirement 5.7, 5.8)
        baseline = await self._confirm_effort_baseline(
            model_map,
            route,
            route_result=route_result,
            route_contract=route_contract_used,
            entry=entry,
            transport=transport,
            budget=budget_obj,
            probe_input=probe_input,
            sequence=sequence,
        )
        summary["baselineSucceeded"] = bool(baseline["succeeded"])
        summary["baselineSource"] = baseline["source"]
        summary["baselineResult"] = baseline["result"]
        if _is_dict(baseline.get("routeContract")):
            route_contract_used = copy.deepcopy(baseline["routeContract"])
        invocation_id = _text(baseline.get("invocationModelId")) or model_id
        summary["invocationModelId"] = invocation_id
        if not summary["baselineSucceeded"]:
            # base 요청이 성공하지 않은 상태에서 effort를 전송하면 실패 원인을 분리할 수
            # 없다 → 전송 0건, effort 상태 변경 없음.
            summary["reason"] = EFFORT_PROBE_BASELINE_UNVERIFIED
            return self._finish_effort_summary(summary, route_result)

        # baseline 성공 계약으로 jobs model ID 결속을 재확인한다(Requirement 8.8~8.10).
        if route == str(contracts.Known_Route.OPENAI_RESPONSES_JOBS) and not (
            jobs_model_id_determined(route_contract_used)
        ):
            summary["reason"] = EFFORT_PROBE_JOBS_MODEL_ID_UNDETERMINED
            if NOTE_JOBS_MODEL_ID_UNDETERMINED not in notes:
                notes.append(NOTE_JOBS_MODEL_ID_UNDETERMINED)
            return self._finish_effort_summary(summary, route_result)

        # 2) domain이 요구하는 value만 각각 1회 검증 (Requirement 5.9~5.11)
        results: list[dict] = summary["results"]
        stopped_at: int | None = None
        for index, value in enumerate(values):
            combo = ProbeBudget.key(model_id, route, value)
            result = self._new_effort_value_result(
                plan,
                value,
                index=index,
                sequence=sequence,
                baseline_succeeded=True,
                invocation_model_id=invocation_id,
            )
            results.append(result)

            budget_reason = budget_obj.block_reason(combo)
            if budget_reason != PROBE_OK:
                result["reason"] = budget_reason
                stopped_at = index
                break

            selection = effort_probe_selection(
                effort_contract, value, capability_fingerprint=plan["capabilityFingerprint"]
            )
            binding = self._effort_binding(
                model_map,
                route_contract_used,
                effort_contract,
                invocation_id,
                capability_fingerprint=plan["capabilityFingerprint"],
            )
            injection = request_builder.effort_injection_plan(binding, selection)
            if not injection["inject"]:
                # production 주입 경로가 거부한 조합 → 전송하지 않는다(근거가 되지 못한다).
                result["reason"] = EFFORT_PROBE_INJECTION_BLOCKED
                result["observations"] = {"injectionReason": _text(injection.get("reason"))}
                stopped_at = index
                break

            client = self._bind_effort_client(transport, transport, binding, selection)
            if not callable(getattr(client, method_name, None)):
                result["reason"] = EFFORT_PROBE_TRANSPORT_METHOD_ABSENT
                stopped_at = index
                break

            preview = self._request_preview(client, route, invocation_id, probe_input)
            observed = effort_injection_observed(preview, effort_contract, value)
            if not observed["observed"]:
                # 주입이 관측되지 않은 body의 성공은 effort 근거가 아니다 → 전송 0건.
                result["reason"] = EFFORT_PROBE_INJECTION_UNOBSERVED
                result["observations"] = {"occurrences": observed["occurrences"]}
                if NOTE_EFFORT_INJECTION_UNOBSERVED not in notes:
                    notes.append(NOTE_EFFORT_INJECTION_UNOBSERVED)
                stopped_at = index
                break

            outcome = await self._send_route_probe(
                client,
                route,
                model_id=invocation_id,
                request_body=preview,
                probe_input=probe_input,
            )
            summary["transmissions"] += 1
            result["transmitted"] = True
            budget_obj.record_transmission(combo)

            judgment = self._judge_route(route, outcome, client=client)
            signals = self._probe_signals(outcome, judgment)
            # effort 범주 판정의 전제조건 — 이 요청에 무엇을 주입했는지 알려준다.
            signals["effortInjected"] = True
            signals["effortFieldPath"] = list(observed["fieldPath"] or [])
            signals["effortValue"] = value
            success = bool(
                judgment["http"] and judgment["validOutput"] and judgment["terminalSuccess"]
            )
            category = None if success else failure_handler.classify(signals)
            rejection = (
                effort_rejection_kind(signals)
                if category == failure_handler.EFFORT_MISMATCH
                else None
            )
            error_text = _text(signals.get("errorText"))

            result["http"] = judgment["http"]
            result["validOutput"] = judgment["validOutput"]
            result["terminalSuccess"] = judgment["terminalSuccess"]
            result["category"] = category
            result["rejection"] = rejection
            result["status"] = effort_value_status(
                success=success, category=category, rejection=rejection
            )
            # 성공 시 실제 전송 body에서 관측한 field path와 value만 기록한다(5.12).
            result["fieldPath"] = list(observed["fieldPath"] or []) or result["fieldPath"]
            if success:
                result["value"] = copy.deepcopy(observed["value"])
            result["observations"] = dict(judgment.get("observations") or {})
            result["observations"]["effortOccurrences"] = observed["occurrences"]
            # route probe와 동일 규칙 — 응답이 실제 전송 ID를 알려주면 그것을 남긴다.
            result["invocationModelId"] = (
                _text(result["observations"].get("invocationModelId")) or invocation_id
            )
            result["usage"] = judgment.get("usage", contracts.NOT_PROVIDED)
            result["cost"] = judgment.get("cost", contracts.NOT_PROVIDED)
            result["sanitizedSchema"] = {
                "request": store.sanitized_schema(preview),
                "response": store.sanitized_schema(judgment.get("responseView")),
            }
            for note in judgment.get("notes") or []:
                if note not in result["notes"]:
                    result["notes"].append(note)
            result["reason"] = (
                failure_handler.safe_cause(error_text) if error_text else PROBE_OK
            )
            # 전송이 일어난 결과는 production 경로로 얻은 것만 activation evidence다(4.4).
            result["evidenceEligible"] = bool(result["productionPath"])
            # 상태를 바꾸는 결과: 성공(5.13) · unknown field(5.14) · invalid value(5.15).
            # transient와 부분 검증은 이전 상태를 유지한다(5.16, 5.17).
            result["stateBearing"] = bool(
                success
                or result["status"] == str(contracts.Effort_Support_Status.UNSUPPORTED)
                or (
                    category == failure_handler.EFFORT_MISMATCH
                    and rejection == EFFORT_REJECTION_INVALID_VALUE
                )
            )

            if success:
                budget_obj.record_success(combo)
                continue
            stopped_at = index
            break

        # 검증하지 못한 나머지 value는 "전송하지 않음"으로 남긴다(부분 검증 → UNVERIFIED).
        if stopped_at is not None:
            if NOTE_EFFORT_PROBE_STOPPED not in notes:
                notes.append(NOTE_EFFORT_PROBE_STOPPED)
            for index in range(stopped_at + 1, len(values)):
                results.append(
                    self._new_effort_value_result(
                        plan,
                        values[index],
                        index=index,
                        sequence=sequence,
                        baseline_succeeded=True,
                        invocation_model_id=invocation_id,
                    )
                )
        return self._finish_effort_summary(summary, route_result)

    def _finish_effort_summary(
        self, summary: dict, route_result: Mapping[str, Any] | None = None
    ) -> dict:
        """value별 결과를 집계해 summary를 확정한다(계약 확정은 전체 성공에만).

        `SUPPORTED`는 요구된 모든 검증이 성공했을 때만 기록하고, 그때만 확정 계약에
        실제 검증된 value(`verifiedValues`)를 담는다(Requirement 5.12, 5.13). 그 밖의
        경우 계약은 ``None``이므로 :func:`capability_map.upsert`가 기존 계약을 그대로
        유지한다(실패한 재검증이 이전 계약을 지우지 않는다).
        """
        results = summary["results"]
        required = list(summary["requiredValues"])
        summary["status"] = aggregate_effort_status(required, results)
        summary["verifiedValues"] = [
            result.get("value")
            for result in results
            if _text(result.get("status")) == str(contracts.Effort_Support_Status.SUPPORTED)
        ]
        summary["usage"] = usage_from(results)
        summary["cost"] = cost_from(results)

        if summary["status"] == str(contracts.Effort_Support_Status.SUPPORTED):
            contract = copy.deepcopy(summary["candidateContract"])
            if _is_dict(contract):
                contract["verifiedValues"] = copy.deepcopy(summary["verifiedValues"])
                summary["contract"] = contract
        else:
            if required and len(summary["verifiedValues"]) < len(required):
                if NOTE_EFFORT_DOMAIN_PARTIAL not in summary["notes"]:
                    summary["notes"].append(NOTE_EFFORT_DOMAIN_PARTIAL)
            # 실패 원인은 첫 비성공 결과의 이유를 summary에도 남긴다(보고서 가독성).
            for result in results:
                reason = _text(result.get("reason"))
                if (
                    _text(result.get("status")) != str(contracts.Effort_Support_Status.SUPPORTED)
                    and reason
                    and reason != PROBE_OK
                ):
                    summary["reason"] = reason
                    break

        summary["evidenceEligible"] = bool(summary["productionPath"])
        summary["stateBearing"] = bool(recordable_effort_results([summary]))

        # effort 결과는 base route 상태를 담지 않는다(강등 경로 부재 — Requirement 5.20).
        base_status = _text(route_result.get("status")) if _is_dict(route_result) else ""
        if base_status:
            summary["baseRouteStatus"] = base_status
            if (
                base_status == str(contracts.Route_Support_Status.SUPPORTED)
                and summary["status"] != str(contracts.Effort_Support_Status.SUPPORTED)
                and NOTE_BASE_ROUTE_STATUS_PRESERVED not in summary["notes"]
            ):
                summary["notes"].append(NOTE_BASE_ROUTE_STATUS_PRESERVED)
        return summary

    async def probe_efforts(
        self,
        model: Mapping[str, Any],
        *,
        route_keys: Iterable[str] | None = None,
        route_results: Iterable[Mapping[str, Any]] | None = None,
        contracts_by_route: Mapping[str, Any] | None = None,
        route_contracts: Mapping[str, Any] | None = None,
        entry: Mapping[str, Any] | None = None,
        transport: Any = None,
        budget: Any = None,
        probe_inputs: Mapping[str, Any] | None = None,
    ) -> list[dict]:
        """모든 Known_Route(또는 지정 route)의 effort 계약을 순서대로 검증한다.

        `route_results`(:meth:`probe_routes` 결과)를 주면 각 route의 baseline 성공 여부와
        확정 Route_Contract를 그대로 재사용하므로 baseline 재전송이 발생하지 않는다.
        광고되지 않은 route·계약 결손 route는 전송 없이 `UNVERIFIED`로 기록된다.
        """
        targets = [
            route
            for route in (contracts.KNOWN_ROUTES if route_keys is None else route_keys)
            if contracts.Known_Route.has(route)
        ]
        by_route = {
            _text(item.get("routeKey")): item
            for item in (route_results or [])
            if _is_dict(item) and contracts.Known_Route.has(item.get("routeKey"))
        }
        effort_overrides = contracts_by_route if _is_dict(contracts_by_route) else {}
        route_overrides = route_contracts if _is_dict(route_contracts) else {}
        inputs = probe_inputs if _is_dict(probe_inputs) else {}
        results: list[dict] = []
        for index, route in enumerate(targets):
            results.append(
                await self.probe_effort(
                    model,
                    route,
                    effort_overrides.get(route),
                    route_result=by_route.get(route),
                    route_contract=route_overrides.get(route),
                    entry=entry,
                    transport=transport,
                    budget=budget,
                    probe_input=inputs.get(route),
                    sequence=index,
                )
            )
        return results

    # -- probe 결과 → Verification_Record · Capability_Map ----------------
    def probe_verification_record(
        self,
        discovery: Mapping[str, Any],
        route_results: Iterable[Mapping[str, Any]] = (),
        effort_results: Iterable[Mapping[str, Any]] = (),
        *,
        capability_fingerprint: str = contracts.UNDETERMINED,
        usage: Any = None,
        cost: Any = None,
        verified_at: str | None = None,
    ) -> dict:
        """route·effort probe 결과로 정제 Verification_Record를 만든다.

        담는 결과는 :func:`recordable_results`(route)와
        :func:`recordable_effort_results`(effort)가 고른다 — activation evidence가 아닌
        결과(주입 transport·진단 코드 산출물)와 상태를 바꾸지 않는 실패 범주
        (transient·quota·인증·validation·unknown·부분 검증)는 제외한다.

        `routeCompleteness`는 **route 결과만으로** 판정하므로 effort 결과가 route 상태
        완전성에 영향을 주지 않는다. usage·cost는 Gateway가 제공한 값만 담고 미제공은
        ``notProvided``다(추정 금지).
        """
        route_eligible = recordable_results(route_results)
        effort_eligible = recordable_effort_results(effort_results)
        combined = route_eligible + effort_eligible
        return self.to_verification_record(
            candidate_label=_text(discovery.get("candidateLabel")),
            model_id=_text(discovery.get("modelId")),
            provider=_text(discovery.get("provider")),
            catalog_fingerprint=_text(discovery.get("catalogFingerprint")),
            capability_fingerprint=_text(capability_fingerprint),
            invocation_model_ids=invocation_model_ids_from(combined),
            route_results=[route_result_for_record(item) for item in route_eligible],
            effort_results=[effort_result_for_record(item) for item in effort_eligible],
            usage=usage_from(combined) if usage is None else usage,
            cost=cost_from(combined) if cost is None else cost,
            route_completeness=route_completeness(route_eligible),
            verified_at=verified_at,
        )

    def route_verification_record(
        self,
        discovery: Mapping[str, Any],
        results: Iterable[Mapping[str, Any]],
        *,
        capability_fingerprint: str = contracts.UNDETERMINED,
        usage: Any = None,
        cost: Any = None,
        verified_at: str | None = None,
    ) -> dict:
        """route probe 결과만으로 정제 Verification_Record를 만든다(Requirement 4.27, 4.28).

        :meth:`probe_verification_record`의 effort 없는 형태다(effort 결과 0건).
        """
        return self.probe_verification_record(
            discovery,
            results,
            (),
            capability_fingerprint=capability_fingerprint,
            usage=usage,
            cost=cost,
            verified_at=verified_at,
        )

    def effort_verification_record(
        self,
        discovery: Mapping[str, Any],
        effort_results: Iterable[Mapping[str, Any]],
        *,
        route_results: Iterable[Mapping[str, Any]] = (),
        capability_fingerprint: str = contracts.UNDETERMINED,
        usage: Any = None,
        cost: Any = None,
        verified_at: str | None = None,
    ) -> dict:
        """effort probe 결과로 정제 Verification_Record를 만든다(Requirement 5.12, 5.13).

        성공한 effort probe의 **실제 field path와 실제 value**만 기록되고, 부분 검증·
        transient 결과는 제외된다. route 결과를 함께 주면 같은 record에 담긴다(route 상태는
        route 결과만으로 정해지므로 effort 실패가 base 상태를 강등하지 않는다 — 5.20).
        """
        return self.probe_verification_record(
            discovery,
            route_results,
            effort_results,
            capability_fingerprint=capability_fingerprint,
            usage=usage,
            cost=cost,
            verified_at=verified_at,
        )

    def apply_probes(
        self,
        map_obj: Any,
        discovery: Mapping[str, Any],
        route_results: Iterable[Mapping[str, Any]] = (),
        effort_results: Iterable[Mapping[str, Any]] = (),
        *,
        ctx: Mapping[str, Any] | None = None,
        mode_hints: Mapping[str, str] | None = None,
        now: str | None = None,
    ) -> tuple[dict, list[dict]]:
        """route·effort probe 결과를 Capability_Map에 반영한다(승격 판정은 capability_map).

        `VERIFIED`는 :func:`capability_map.upsert`가 승격 조건(Exact_Model_ID ·
        Provider_String · `SUPPORTED` route ≥ 1 · Complete_Record · Current_Evidence)을
        모두 만족할 때만 기록한다(Requirement 4.28). 명시적 allowlist 거부가 있으면
        기존 :func:`failure_handler.apply`로 entry를 `REJECTED`로 전이한다(4.19).

        effort 결과는 `effort` entry의 상태·계약·`verifiedValues`만 갱신하고 route 상태에는
        접근하지 않는다(:func:`capability_map._apply_effort_results`) — effort 실패가 base
        Route_Support_Status를 강등할 경로가 없다(Requirement 5.20).

        Returns:
            ``(새 map, 생성한 Verification_Record 목록)``. record는 호출자가
            :meth:`persist_record`로 저장해 evidence 참조 무결성을 유지한다.
        """
        items = [item for item in route_results if _is_dict(item)]
        effort_items = [item for item in effort_results if _is_dict(item)]
        label = _text(discovery.get("candidateLabel"))
        if not label and not _text(discovery.get("modelId")):
            raise EvidenceCollectorError("discovery 결과에 candidateLabel과 modelId가 모두 없다")

        hints = dict(mode_hints) if _is_dict(mode_hints) else route_mode_hints()
        route_contracts = route_contracts_for_record(items)
        effort_contracts = effort_contracts_for_record(effort_items)
        verified_at = self.now()
        source_kind = discovery.get("sourceKind")

        def _upsert(fingerprint: str) -> tuple[dict, dict]:
            record = self.probe_verification_record(
                discovery,
                items,
                effort_items,
                capability_fingerprint=fingerprint,
                verified_at=verified_at,
            )
            updated = capability_map.upsert(
                map_obj,
                record,
                route_contracts=route_contracts,
                effort_contracts=effort_contracts,
                ctx=ctx,
                source_kind=source_kind,
                mode_hints=hints,
                now=now,
            )
            return updated, record

        # 1차 upsert로 반영 후 Capability_Fingerprint를 얻고, 그 값을 record에 담아 다시
        # 반영한다(record의 fingerprint와 entry의 fingerprint를 일치시킨다).
        probed, _ = _upsert(contracts.UNDETERMINED)
        entry = capability_map.find_entry(
            probed, candidate_label=label, model_id=_text(discovery.get("modelId"))
        )
        fingerprint = (
            _text(entry.get("capabilityFingerprint")) if _is_dict(entry) else contracts.UNDETERMINED
        )
        out, record = _upsert(fingerprint)

        denials = [
            item
            for item in recordable_results(items)
            if item.get("category") == failure_handler.ALLOWLIST
        ]
        for denial in denials:
            target = capability_map.find_entry(
                out, candidate_label=label, model_id=_text(discovery.get("modelId"))
            )
            if not _is_dict(target):
                continue
            transition = failure_handler.apply(
                target,
                failure_handler.ALLOWLIST,
                {
                    "modelId": _text(discovery.get("modelId")),
                    "invocationModelId": _text(denial.get("invocationModelId")),
                    "route": denial.get("routeKey"),
                    "errorText": _text(denial.get("reason")),
                    "modeHints": hints,
                },
            )
            model_id = _text(discovery.get("modelId"))
            for index, item in enumerate(out["entries"]):
                if not _is_dict(item):
                    continue
                matched = (
                    _text(item.get("candidateLabel")) == label
                    if label
                    else _text(item.get("modelId")) == model_id
                )
                if matched:
                    out["entries"][index] = transition["entry"]
                    break
        return out, [record]

    def apply_route_probes(
        self,
        map_obj: Any,
        discovery: Mapping[str, Any],
        results: Iterable[Mapping[str, Any]],
        *,
        ctx: Mapping[str, Any] | None = None,
        mode_hints: Mapping[str, str] | None = None,
        now: str | None = None,
    ) -> tuple[dict, list[dict]]:
        """route probe 결과만 Capability_Map에 반영한다(:meth:`apply_probes`의 얇은 형태)."""
        return self.apply_probes(
            map_obj, discovery, results, (), ctx=ctx, mode_hints=mode_hints, now=now
        )

    def apply_effort_probes(
        self,
        map_obj: Any,
        discovery: Mapping[str, Any],
        effort_results: Iterable[Mapping[str, Any]],
        *,
        route_results: Iterable[Mapping[str, Any]] = (),
        ctx: Mapping[str, Any] | None = None,
        mode_hints: Mapping[str, str] | None = None,
        now: str | None = None,
    ) -> tuple[dict, list[dict]]:
        """effort probe 결과를 Capability_Map에 반영한다(base route 상태 불변 — 5.20).

        route 결과를 함께 주지 않으면 route 상태·계약은 **전혀 건드리지 않는다**
        (:func:`capability_map.upsert`가 route 결과·계약이 없는 route를 그대로 유지한다).
        """
        return self.apply_probes(
            map_obj,
            discovery,
            route_results,
            effort_results,
            ctx=ctx,
            mode_hints=mode_hints,
            now=now,
        )

    # -- map 반영 (Requirement 2.18) --------------------------------------
    def apply_discovery(
        self,
        map_obj: Any,
        results: Iterable[Mapping[str, Any]],
        *,
        ctx: Mapping[str, Any] | None = None,
        mode_hints: Mapping[str, str] | None = None,
        now: str | None = None,
    ) -> tuple[dict, list[dict]]:
        """discovery 결과를 `candidateLabel` 단위로 map에 반영한다.

        Args:
            map_obj: 현재 Capability_Map(변경하지 않는다).
            results: :meth:`discover` 결과.
            ctx: 현재성 판정 컨텍스트(:func:`capability_map.promotion_blockers`).
            mode_hints: `{routeKey: Execution_Mode}` — 계약에 mode가 없을 때만 사용.
            now: map `updatedAt`으로 쓸 UTC ISO 8601 시각.

        Returns:
            ``(새 map, 생성한 Verification_Record 목록)``. 호출자는 record를
            :meth:`persist_record`로 저장해 evidence 참조 무결성을 유지한다.

        규칙:
            - 라벨마다 정확히 하나의 entry가 존재하도록 보장한다(없으면 초기
              `UNVERIFIED` entry를 만든다).
            - 미발견·근거 실패 라벨은 record를 만들지 않는다 → `UNVERIFIED` 유지,
              evidence 참조도 생기지 않는다.
            - 발견 라벨은 `candidateLabel` 키로만 upsert하므로 provider·model family·
              route·effort가 다른 라벨 entry로 전파될 경로가 없다.
        """
        out = capability_map.normalize_map(map_obj, now=now)
        records: list[dict] = []

        for result in results:
            label = _text(result.get("candidateLabel"))
            if not label:
                raise EvidenceCollectorError("discovery 결과에 candidateLabel이 없다")

            if capability_map.find_entry(out, candidate_label=label) is None:
                out["entries"].append(
                    contracts.new_entry(
                        label,
                        source_kind=result.get("sourceKind") or contracts.Source_Kind.CATALOG,
                        fingerprint_fn=canonicalizer.capability_fingerprint,
                    )
                )

            if not result.get("found") or not _text(result.get("modelId")):
                continue  # 미발견 → UNVERIFIED 유지 · probe 미생성(Requirement 2.4, 2.12)

            record = self.discovery_record(result)
            records.append(record)
            out = capability_map.upsert(
                out,
                record,
                ctx=ctx,
                source_kind=result.get("sourceKind"),
                mode_hints=mode_hints,
                now=now,
            )
        return out, records

    # -- 영속화 (Requirement 10.12~10.15) --------------------------------
    def _require_store(self, store_obj: store.CapabilityStore | None) -> store.CapabilityStore:
        target = store_obj or self._store
        if target is None:
            raise EvidenceCollectorError("CapabilityStore가 필요하다")
        return target

    def persist_record(
        self,
        record: Mapping[str, Any],
        *,
        store_obj: store.CapabilityStore | None = None,
    ):
        """정제 Verification_Record를 `userData/capability/evidence/`에 기록한다.

        record는 :meth:`to_verification_record`가 이미 정제했으므로 ``sanitize=False``로
        기록한다(재정제하면 Sanitized_Schema의 field 이름이 Probe_ID로 재작성된다).
        기록 전에 :func:`sanitization_violations`로 credential·raw prompt·raw body
        잔존을 확인하고, 하나라도 있으면 기록하지 않고 예외를 던진다.

        Raises:
            SanitizationError: 정제되지 않은 값이 남아 있다.
            EvidenceCollectorError: store가 없거나 Evidence_Record_ID가 비어 있다.
        """
        evidence_id = _text(record.get("evidenceRecordId"))
        if not evidence_id:
            raise EvidenceCollectorError("Evidence_Record_ID가 비어 있다")
        violations = sanitization_violations(record)
        if violations:
            raise SanitizationError(violations)
        return self._require_store(store_obj).write_evidence(
            evidence_id, dict(record), sanitize=False
        )

    def persist_catalog_snapshot(
        self,
        catalog: Mapping[str, Any] | None = None,
        *,
        store_obj: store.CapabilityStore | None = None,
    ):
        """정제 catalog snapshot을 `userData/capability/catalog/`에 기록한다.

        Args:
            catalog: :meth:`fetch_catalog` 결과. 생략하면 캐시된 결과를 쓴다.

        Returns:
            기록된 절대 경로. 근거로 인정된 catalog가 없으면 ``None``.
        """
        source = catalog if catalog is not None else self._catalog_result
        if not _is_dict(source) or not source.get("available"):
            return None
        fingerprint = _text(source.get("catalogFingerprint"))
        if not fingerprint:
            return None
        payload = {
            "schemaVersion": contracts.SCHEMA_VERSION,
            "catalogFingerprint": fingerprint,
            "sourceKind": source.get("sourceKind"),
            "environment": source.get("environment"),
            "collectedAt": self.now(),
            "records": source.get("records", 0),
            "snapshot": source.get("snapshot"),
        }
        return self._require_store(store_obj).write_catalog_snapshot(
            fingerprint, payload, sanitize=True
        )


__all__ = [
    "BASELINE_FROM_BUDGET",
    "BASELINE_FROM_PROBE",
    "BASELINE_FROM_ROUTE_RESULT",
    "CANDIDATE_LABELS",
    "CATALOG_EMPTY",
    "EFFORT_CAPABILITY_KEYS",
    "EFFORT_DOMAIN_KIND_KEYS",
    "EFFORT_ENUM_KEYS",
    "EFFORT_FIELD_PATH_KEYS",
    "EFFORT_INJECTABLE_ROUTES",
    "EFFORT_PROBE_BASELINE_UNVERIFIED",
    "EFFORT_PROBE_BLOCK_REASONS",
    "EFFORT_PROBE_BUDGET_EXHAUSTED",
    "EFFORT_PROBE_CONTRACT_ABSENT",
    "EFFORT_PROBE_CONTRACT_INCOMPLETE",
    "EFFORT_PROBE_CONTRACT_MISBOUND",
    "EFFORT_PROBE_DOMAIN_INCOMPLETE",
    "EFFORT_PROBE_INJECTION_BLOCKED",
    "EFFORT_PROBE_INJECTION_UNOBSERVED",
    "EFFORT_PROBE_JOBS_MODEL_ID_UNDETERMINED",
    "EFFORT_PROBE_MODEL_ID_ABSENT",
    "EFFORT_PROBE_NOT_ADVERTISED",
    "EFFORT_PROBE_NOT_PROBED",
    "EFFORT_PROBE_ROUTE_NOT_INJECTABLE",
    "EFFORT_PROBE_ROUTE_UNKNOWN",
    "EFFORT_PROBE_TRANSPORT_METHOD_ABSENT",
    "EFFORT_PROBE_TRANSPORT_UNAVAILABLE",
    "EFFORT_RANGE_LOWER_KEYS",
    "EFFORT_RANGE_UPPER_KEYS",
    "EFFORT_REJECTION_INVALID_VALUE",
    "EFFORT_REJECTION_UNKNOWN_FIELD",
    "EFFORT_VALUE_NONE",
    "EFFORT_VALUE_TYPE_KEYS",
    "NOTE_BASE_ROUTE_STATUS_PRESERVED",
    "NOTE_EFFORT_DOMAIN_PARTIAL",
    "NOTE_EFFORT_INJECTION_UNOBSERVED",
    "NOTE_EFFORT_PROBE_STOPPED",
    "NOTE_JOBS_MODEL_ID_UNDETERMINED",
    "advertised_effort",
    "aggregate_effort_status",
    "candidate_effort_contract",
    "candidate_effort_contract_for",
    "effort_contract_binding_reason",
    "effort_contracts_for_record",
    "effort_contracts_from_record",
    "effort_declaration_domain",
    "effort_declaration_field_path",
    "effort_declaration_routes",
    "effort_declaration_value_type",
    "effort_injection_observed",
    "effort_probe_client",
    "effort_probe_results",
    "effort_probe_selection",
    "effort_probe_values",
    "effort_rejection_kind",
    "effort_result_for_record",
    "effort_value_status",
    "jobs_model_id_determined",
    "record_advertised_effort",
    "recordable_effort_results",
    "NOTE_ASYNC_STAGE_FAILURE",
    "NOTE_CORRECTION_UNAVAILABLE",
    "NOTE_INVOKE_EXTRACTOR_UNAVAILABLE",
    "NOTE_MESSAGE_PATH_UNVERIFIED",
    "NOTE_MIN_OUTPUT_BOUND_DROPPED",
    "NOTE_MODEL_ID_BINDING_OBSERVED",
    "NOTE_MODEL_ID_PATH_UNVERIFIED",
    "NOTE_NON_PRODUCTION_TRANSPORT",
    "NOTE_PREFIX_CORRECTION_APPLIED",
    "NOTE_PREFIX_CORRECTION_DELEGATED",
    "NOTE_REQUEST_PREVIEW_UNAVAILABLE",
    "PREFIX_OWNER_COLLECTOR",
    "PREFIX_OWNER_TRANSPORT",
    "PROBE_BLOCK_REASONS",
    "PROBE_BUDGET_SUCCESS_USED",
    "PROBE_BUDGET_TRANSMISSION_LIMIT",
    "PROBE_CONTRACT_INCOMPLETE",
    "PROBE_EFFORT_SELECTION",
    "PROBE_INPUT_TEXT",
    "PROBE_INPUT_UNAVAILABLE",
    "PROBE_MODEL_ID_ABSENT",
    "PROBE_NOT_ADVERTISED",
    "PROBE_OK",
    "PROBE_ROUTE_UNKNOWN",
    "PROBE_SYSTEM_PROMPT",
    "PROBE_TRANSPORT_METHOD_ABSENT",
    "PROBE_TRANSPORT_UNAVAILABLE",
    "ProbeBudget",
    "ROUTE_PROFILES",
    "SSE_CONTENT_EVENT_TYPES",
    "SSE_MODEL_ID_FIELD",
    "SSE_TERMINAL_EVENT_TYPES",
    "STATE_PRESERVING_CATEGORIES",
    "adapter_usage",
    "advertised_routes",
    "allowlist_result",
    "apply_min_output_bound",
    "candidate_route_contract",
    "converse_content_blocks",
    "corrected_model_id",
    "cost_from",
    "evidence_eligible_results",
    "gateway_cost",
    "gateway_usage",
    "invocation_model_ids_from",
    "invoke_result_view",
    "judge_converse",
    "judge_invoke",
    "judge_openai_jobs",
    "judge_openai_sync",
    "judge_sse",
    "min_output_bound_fields",
    "observe_model_id_binding",
    "observe_sse_model_id",
    "probe_client",
    "probe_client_class",
    "probe_messages",
    "recordable_results",
    "route_completeness",
    "route_contracts_for_record",
    "route_mode_hints",
    "route_profile",
    "route_result_for_record",
    "route_support_status",
    "usage_from",
    "without_min_output_bound",
    "CATALOG_ENDPOINT_ABSENT",
    "CATALOG_ENVIRONMENT_MISMATCH",
    "CATALOG_ENVIRONMENT_UNKNOWN",
    "CATALOG_FETCH_FAILED",
    "CATALOG_SOURCE_NOT_AUTHORITATIVE",
    "DISCOVERY_REASONS",
    "EXPORT_ENVIRONMENT_MISMATCH",
    "EXPORT_ENVIRONMENT_UNKNOWN",
    "EXPORT_FINGERPRINT_MISMATCH",
    "EXPORT_SANITIZATION_NOT_CREDENTIAL_ONLY",
    "EXPORT_SCHEMA_INVALID",
    "EXPORT_SIGNIFICANT_FIELD_CHANGED",
    "EXPORT_TIME_FORMAT_INVALID",
    "EXPORT_TIME_ORDER_INVALID",
    "EvidenceCollector",
    "EvidenceCollectorError",
    "InvalidVerificationRecordError",
    "LABEL_ASSOCIATION_ABSENT",
    "LABEL_ASSOCIATION_AMBIGUOUS",
    "LABEL_FIELD_KEYS",
    "LABEL_MAP_KEYS",
    "MATCH_EXACT",
    "MATCH_NORMALIZED_LABEL",
    "MODEL_ID_ABSENT",
    "MODEL_ID_KEYS",
    "NOTE_BASELINE_EXCLUDED",
    "NOTE_CATALOG_EMPTY_TREATED_AS_ABSENT",
    "NOTE_OPERATOR_EXPORT_USED",
    "OPERATOR_EXPORT_CATALOG_KEYS",
    "OPERATOR_EXPORT_REQUIRED_FIELDS",
    "OPERATOR_EXPORT_SCHEMA_VERSIONS",
    "PROBE_ID_PREFIX",
    "PROVIDER_ABSENT",
    "PROVIDER_KEYS",
    "ROUTE_FIELD_KEYS",
    "SanitizationError",
    "catalog_records",
    "environment_complete",
    "environment_identity",
    "environment_matches",
    "find_label_records",
    "is_catalog_significant_field",
    "label_map_entries",
    "manifest_field_names",
    "normalize_label",
    "probe_id",
    "record_advertised_routes",
    "record_labels",
    "record_model_id",
    "record_provider",
    "sanitization_violations",
    "strip_credential_fields",
    "validate_operator_catalog_export",
]
