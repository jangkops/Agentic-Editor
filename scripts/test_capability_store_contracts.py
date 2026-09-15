"""capability 계약·영속 계층 단위 테스트 (task 1.3).

검증 대상 모듈:
    ai_engine/capability/contracts.py   닫힌 enum·계약 스키마·Malformed_Entry 판정기
    ai_engine/capability/store.py       userData 한정 경로 해석·원자적 쓰기·정제

검증 항목 (_Requirements: 3.8, 3.9, 3.10, 3.11, 10.15_):
    1. enum 이탈·필수 필드 누락·타입 불일치·fingerprint 불일치·evidence 참조 무결성
       실패 입력이 Malformed_Entry로 판정된다(3.8, 3.9, 3.10 — 닫힌 값 집합 강제).
    2. 모든 시각 필드는 UTC ISO 8601만 허용한다(3.11).
    3. userData 루트 밖 경로(상위참조·절대경로·홈 확장·symlink 이스케이프)는
       읽기·쓰기 모두 거부된다(10.15).
    4. 원자적 쓰기 후 파일 무결성: 임시 파일 잔존 없음, 교체 실패 시 이전 완전본 보존.
    5. sanitizer가 credential·authorization·cookie·signature를 중첩 위치까지 제거하고
       raw prompt → Probe_ID, raw body → Sanitized_Schema로 대체한다.

이 파일은 **단위 테스트**다. Correctness Properties 1~10은 design.md의 property ↔
테스트 파일 매핑 표에 따라 각자 전용 파일에서 Hypothesis로 구현하므로, 여기서는
새 property를 추가하지 않고 `parametrize`로 닫힌 집합을 전수 열거한다.

모델 ID·provider·effort field path·effort 허용값은 확정 상수로 두지 않는다. 아래
fixture는 구조만 채우는 무작위 심볼이며 Gateway 지원 근거가 아니다(활성화 강제 조건).

네트워크·Gateway 호출 없음. 파일시스템은 `tmp_path`로 격리하며 실제 userData를
건드리지 않는다(환경변수는 주입 dict로만 전달한다).

실행: ai_engine/.venv/bin/python -m pytest scripts/test_capability_store_contracts.py -q
"""
import json
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import pytest

from ai_engine.capability import contracts as C
from ai_engine.capability import store as S


# ─────────────────────────────────────────────────────────────────
# fixture 심볼 — 구조를 채우기 위한 무작위 심볼(실제 model/provider/effort 아님)
# ─────────────────────────────────────────────────────────────────
_LABEL_SYMBOL = "label-symbol-c2e8"
_MODEL_SYMBOL = "model-symbol-9f3a"
_PROVIDER_SYMBOL = "provider-symbol-71bd"
_REVISION_SYMBOL = "rev-symbol-4d10"
_EVIDENCE_ID = "evr1:sha256:" + "a" * 64
_CATALOG_FP = "cat1:sha256:" + "b" * 64
_CAPABILITY_FP = "cfp1:sha256:" + "c" * 64

#: route 키는 Known_Route 닫힌 집합의 구조적 키일 뿐 지원 주장이 아니다.
_ROUTE_A = C.Known_Route.values()[0]
_ROUTE_B = C.Known_Route.values()[1]

_EFFORT_PATH_SYMBOL = ["pathSymbolA", "pathSymbolB"]
_EFFORT_VALUE_SYMBOLS = ["valueSymbolA", "valueSymbolB"]


def _route_contract(route_key=_ROUTE_A):
    """완전한 Route_Contract(모든 자리가 채워진 상태)."""
    return C.new_route_contract(
        route_key,
        endpoint_ref="endpointRefSymbolA",
        http_method="methodSymbolA",
        execution_mode=C.Execution_Mode.SYNC,
        signing_service=C.Signing_Service.EXECUTE_API,
        model_id_required=True,
        model_id_field_path=["modelIdPathSymbolA"],
        message_field_path=["messagePathSymbolA"],
        inference_config_field_path=["inferencePathSymbolA"],
        optional_fields=["optionalSymbolA"],
        output_validator_ref="validatorRefSymbolA",
        terminal_condition_ref="terminalRefSymbolA",
        retry_policy_ref="retryRefSymbolA",
        fallback_rank=0,
        purposes=["purposeSymbolA"],
        min_output_bound={"boundSymbolA": 1},
        evidence_ref=_EVIDENCE_ID,
    )


def _effort_contract(route_key=_ROUTE_A, model_id=_MODEL_SYMBOL):
    """완전한 Effort_Contract(enum domain)."""
    return C.new_effort_contract(
        model_id,
        route_key,
        field_path=list(_EFFORT_PATH_SYMBOL),
        value_type=C.Value_Type.STRING,
        domain_kind=C.Domain_Kind.ENUM,
        enum_values=list(_EFFORT_VALUE_SYMBOLS),
        verified_values=list(_EFFORT_VALUE_SYMBOLS),
        evidence_ref=_EVIDENCE_ID,
    )


def _valid_entry(route_key=_ROUTE_A):
    """Malformed가 아닌 완전한 entry(하나의 route·effort가 `SUPPORTED`)."""
    entry = C.new_entry(_LABEL_SYMBOL, revision=_REVISION_SYMBOL)
    entry["modelId"] = _MODEL_SYMBOL
    entry["invocationModelIds"] = [_MODEL_SYMBOL]
    entry["provider"] = _PROVIDER_SYMBOL
    entry["catalogFingerprint"] = _CATALOG_FP
    entry["capabilityFingerprint"] = _CAPABILITY_FP
    entry["evidence"] = [_EVIDENCE_ID]
    entry["verifiedAt"] = C.utc_now_iso()
    entry["verificationStatus"] = str(C.Verification_Status.VERIFIED)
    entry["routes"][route_key] = {
        "status": str(C.Route_Support_Status.SUPPORTED),
        "allowlist": str(C.Allowlist_Result.ALLOWED),
        "contract": _route_contract(route_key),
        "evidenceRef": _EVIDENCE_ID,
    }
    entry["effort"][route_key] = {
        "status": str(C.Effort_Support_Status.SUPPORTED),
        "contract": _effort_contract(route_key),
        "evidenceRef": _EVIDENCE_ID,
    }
    entry["syncSupport"] = str(C.Route_Support_Status.SUPPORTED)
    return entry


def _valid_record():
    """Malformed가 아닌 Verification_Record."""
    return C.new_verification_record(
        run_id="runSymbolA",
        revision=_REVISION_SYMBOL,
        interpreter_path="/interpreter/path/symbol/python",
        environment={
            "gatewayEnvironmentId": "envSymbolA",
            "endpointIdentity": "endpointSymbolA",
            "region": "regionSymbolA",
        },
        candidate_label=_LABEL_SYMBOL,
        model_id=_MODEL_SYMBOL,
        provider=_PROVIDER_SYMBOL,
        invocation_model_ids=[_MODEL_SYMBOL],
        catalog_fingerprint=_CATALOG_FP,
        capability_fingerprint=_CAPABILITY_FP,
        route_results=[{
            "routeKey": _ROUTE_A,
            "http": True,
            "validOutput": True,
            "terminalSuccess": True,
            "allowlist": str(C.Allowlist_Result.ALLOWED),
            "status": str(C.Route_Support_Status.SUPPORTED),
            "probeId": "probe:routeSymbolA",
            "sanitizedSchema": {"type": "object", "fieldCount": 0, "fields": {}},
            "correctionUsed": False,
        }],
        effort_results=[{
            "routeKey": _ROUTE_A,
            "fieldPath": list(_EFFORT_PATH_SYMBOL),
            "value": _EFFORT_VALUE_SYMBOLS[0],
            "status": str(C.Effort_Support_Status.SUPPORTED),
            "probeId": "probe:effortSymbolA",
            "sanitizedSchema": {"type": "object", "fieldCount": 0, "fields": {}},
            "baselineSucceeded": True,
        }],
        route_completeness=True,
        evidence_record_id=_EVIDENCE_ID,
    )


def _codes(reasons):
    """판정 이유 목록에서 Malformed 코드 집합만 추출한다."""
    return {reason.split(":", 1)[0] for reason in reasons}


def _store(tmp_path):
    """실제 userData를 건드리지 않는 격리 store(환경변수는 주입 dict로만 전달)."""
    return S.CapabilityStore(user_data_root=str(tmp_path / "userData"), env={})


# ═════════════════════════════════════════════════════════════════
# 1. 기준선 — 유효 입력은 Malformed가 아니다
# ═════════════════════════════════════════════════════════════════
@pytest.mark.parametrize("route_key", C.Known_Route.values())
def test_valid_entry_is_not_malformed(route_key):
    """완전한 entry는 어떤 Known_Route에서도 판정 이유가 없다."""
    entry = _valid_entry(route_key)
    assert C.validate_entry(entry) == []
    assert C.is_malformed(entry) is False


def test_initial_unverified_entry_is_not_malformed():
    """라벨만 아는 초기 entry는 미확정이지만 Malformed는 아니다(값 추론 금지)."""
    entry = C.new_entry(_LABEL_SYMBOL)
    assert C.validate_entry(entry) == []
    assert entry["verificationStatus"] == C.Verification_Status.UNVERIFIED
    assert entry["modelId"] == C.UNDETERMINED
    assert entry["provider"] == C.UNDETERMINED
    # 계약 자리는 삭제되지 않고 None으로 남아 "미존재"와 "미확정"을 구분한다.
    assert all(entry["routes"][r]["contract"] is None for r in C.KNOWN_ROUTES)
    assert all(entry["effort"][r]["contract"] is None for r in C.KNOWN_ROUTES)


def test_valid_verification_record_is_not_malformed():
    assert C.validate_verification_record(_valid_record()) == []


# ═════════════════════════════════════════════════════════════════
# 2. enum 이탈 → Malformed (요구사항 3.8, 3.9, 3.10)
# ═════════════════════════════════════════════════════════════════
@pytest.mark.parametrize(
    "enum_cls,expected",
    [
        (C.Verification_Status, ("UNVERIFIED", "DISCOVERED", "VERIFIED", "REJECTED", "STALE")),
        (C.Route_Support_Status, ("SUPPORTED", "UNSUPPORTED", "UNVERIFIED", "NOT_ADVERTISED")),
        (C.Effort_Support_Status, ("SUPPORTED", "UNSUPPORTED", "UNVERIFIED", "STALE")),
    ],
)
def test_status_enums_are_closed_sets(enum_cls, expected):
    """3.8/3.9/3.10 — 상태 값 집합이 요구사항과 정확히 일치하는 닫힌 집합이다."""
    assert set(enum_cls.values()) == set(expected)
    assert len(enum_cls.values()) == len(expected)


@pytest.mark.parametrize(
    "enum_cls",
    [
        C.Verification_Status,
        C.Route_Support_Status,
        C.Effort_Support_Status,
        C.Allowlist_Result,
        C.Known_Route,
        C.Execution_Mode,
        C.Signing_Service,
        C.Domain_Kind,
        C.Value_Type,
        C.Source_Kind,
        C.Failure_Category,
    ],
)
def test_enum_membership_rejects_variants(enum_cls):
    """멤버 값만 통과하고 대소문자·공백·유사 문자열 변형은 이탈로 판정된다."""
    for value in enum_cls.values():
        assert enum_cls.has(value) is True
    for value in enum_cls.values():
        assert enum_cls.has(value.lower() + "x") is False
        assert enum_cls.has(" " + value) is False
    assert enum_cls.has(None) is False
    assert enum_cls.has(0) is False
    assert enum_cls.has("") is False


@pytest.mark.parametrize(
    "field,bad_value",
    [
        ("verificationStatus", "VERIFED"),
        ("verificationStatus", "verified"),
        ("sourceKind", "CATALOGUE"),
        ("syncSupport", "SUPPORT"),
        ("asyncSupport", "NOTADVERTISED"),
        ("streamingSupport", ""),
        ("schemaVersion", C.SCHEMA_VERSION + 1),
    ],
)
def test_entry_enum_violation_is_malformed(field, bad_value):
    entry = _valid_entry()
    entry[field] = bad_value
    reasons = C.validate_entry(entry)
    assert C.ENUM_VIOLATION in _codes(reasons), reasons
    assert C.is_malformed(entry) is True


@pytest.mark.parametrize(
    "container,inner_field,bad_value",
    [
        ("routes", "status", "OK"),
        ("routes", "status", "supported"),
        ("routes", "allowlist", "ALLOW"),
        ("effort", "status", "ENABLED"),
        ("effort", "status", "NOT_ADVERTISED"),  # Effort 상태 집합에는 없다
    ],
)
def test_nested_enum_violation_is_malformed(container, inner_field, bad_value):
    entry = _valid_entry()
    entry[container][_ROUTE_A][inner_field] = bad_value
    reasons = C.validate_entry(entry)
    assert C.ENUM_VIOLATION in _codes(reasons), reasons
    assert any(f"{container}.{_ROUTE_A}.{inner_field}" in r for r in reasons), reasons


def test_unknown_route_key_is_malformed():
    """routes·effort의 키 집합은 Known_Route 닫힌 집합이다."""
    for container in ("routes", "effort"):
        entry = _valid_entry()
        entry[container]["CONVERSE_V2"] = entry[container][_ROUTE_A]
        reasons = C.validate_entry(entry)
        assert C.ENUM_VIOLATION in _codes(reasons), reasons
        assert any("unknown-route-key" in r for r in reasons), reasons


@pytest.mark.parametrize(
    "field,bad_value",
    [
        ("executionMode", "SYNCHRONOUS"),
        ("signingService", "apigateway"),
        ("routeKey", "NOT_A_ROUTE"),
    ],
)
def test_route_contract_enum_violation(field, bad_value):
    entry = _valid_entry()
    entry["routes"][_ROUTE_A]["contract"][field] = bad_value
    reasons = C.validate_entry(entry)
    assert C.ENUM_VIOLATION in _codes(reasons), reasons


@pytest.mark.parametrize(
    "field,bad_value",
    [
        ("valueType", "TEXT"),
        ("domainKind", "SET"),
        ("routeKey", "NOT_A_ROUTE"),
    ],
)
def test_effort_contract_enum_violation(field, bad_value):
    entry = _valid_entry()
    entry["effort"][_ROUTE_A]["contract"][field] = bad_value
    reasons = C.validate_entry(entry)
    assert C.ENUM_VIOLATION in _codes(reasons), reasons


def test_contract_binding_key_mismatch_is_enum_violation():
    """계약의 routeKey·modelId가 결속 위치와 다르면 이탈로 판정된다."""
    entry = _valid_entry()
    entry["routes"][_ROUTE_A]["contract"]["routeKey"] = _ROUTE_B
    entry["effort"][_ROUTE_A]["contract"]["modelId"] = _MODEL_SYMBOL + "-other"
    reasons = C.validate_entry(entry)
    assert any("route-key-mismatch" in r for r in reasons), reasons
    assert any("model-id-mismatch" in r for r in reasons), reasons


# ═════════════════════════════════════════════════════════════════
# 3. 필수 필드 누락 → Malformed
# ═════════════════════════════════════════════════════════════════
@pytest.mark.parametrize("field", C.REQUIRED_ENTRY_FIELDS)
def test_missing_required_entry_field_is_malformed(field):
    entry = _valid_entry()
    del entry[field]
    reasons = C.validate_entry(entry)
    assert f"{C.MISSING_FIELD}:{field}" in reasons, reasons
    assert C.is_malformed(entry) is True


def test_optional_entry_field_absence_is_not_malformed():
    """`displayName`은 선택 필드이므로 없어도 Malformed가 아니다."""
    entry = _valid_entry()
    for field in C.OPTIONAL_ENTRY_FIELDS:
        entry.pop(field, None)
    assert C.validate_entry(entry) == []


@pytest.mark.parametrize("field", C.REQUIRED_ROUTE_ENTRY_FIELDS)
def test_missing_route_entry_field_is_malformed(field):
    entry = _valid_entry()
    del entry["routes"][_ROUTE_A][field]
    reasons = C.validate_entry(entry)
    assert f"{C.MISSING_FIELD}:routes.{_ROUTE_A}.{field}" in reasons, reasons


@pytest.mark.parametrize("field", C.REQUIRED_EFFORT_ENTRY_FIELDS)
def test_missing_effort_entry_field_is_malformed(field):
    entry = _valid_entry()
    del entry["effort"][_ROUTE_A][field]
    reasons = C.validate_entry(entry)
    assert f"{C.MISSING_FIELD}:effort.{_ROUTE_A}.{field}" in reasons, reasons


@pytest.mark.parametrize("field", C.REQUIRED_ROUTE_CONTRACT_FIELDS)
def test_missing_route_contract_field_is_malformed(field):
    entry = _valid_entry()
    del entry["routes"][_ROUTE_A]["contract"][field]
    reasons = C.validate_entry(entry)
    assert C.MISSING_FIELD in _codes(reasons), reasons
    assert any(f"routes.{_ROUTE_A}.contract.{field}" in r for r in reasons), reasons


@pytest.mark.parametrize("field", C.REQUIRED_EFFORT_CONTRACT_FIELDS)
def test_missing_effort_contract_field_is_malformed(field):
    entry = _valid_entry()
    del entry["effort"][_ROUTE_A]["contract"][field]
    reasons = C.validate_entry(entry)
    assert C.MISSING_FIELD in _codes(reasons), reasons
    assert any(f"effort.{_ROUTE_A}.contract.{field}" in r for r in reasons), reasons


@pytest.mark.parametrize(
    "field,undetermined_value",
    [
        ("endpointRef", C.UNDETERMINED),
        ("httpMethod", C.UNDETERMINED),
        ("executionMode", None),
        ("signingService", None),
        ("modelIdRequired", None),
        ("messageFieldPath", None),
        ("purposes", []),
        ("minOutputBound", {}),
    ],
)
def test_supported_route_requires_complete_contract(field, undetermined_value):
    """`SUPPORTED` route는 완전한 Route_Contract를 요구한다(미확정 자리 = Malformed)."""
    entry = _valid_entry()
    entry["routes"][_ROUTE_A]["contract"][field] = undetermined_value
    reasons = C.validate_entry(entry)
    assert any("supported-route" in r for r in reasons), reasons
    assert C.is_malformed(entry) is True


@pytest.mark.parametrize(
    "field,undetermined_value",
    [
        ("fieldPath", None),
        ("fieldPath", []),
        ("valueType", None),
        ("domainKind", None),
        ("enumValues", []),
    ],
)
def test_supported_effort_requires_complete_contract(field, undetermined_value):
    """`SUPPORTED` effort는 완전한 Effort_Contract를 요구한다."""
    entry = _valid_entry()
    entry["effort"][_ROUTE_A]["contract"][field] = undetermined_value
    reasons = C.validate_entry(entry)
    assert any("supported-effort" in r for r in reasons), reasons


def test_range_domain_requires_both_inclusive_bounds():
    """RANGE domain은 두 inclusive 경계가 모두 확정되어야 완전하다."""
    contract = C.new_effort_contract(
        _MODEL_SYMBOL,
        _ROUTE_A,
        field_path=list(_EFFORT_PATH_SYMBOL),
        value_type=C.Value_Type.INTEGER,
        domain_kind=C.Domain_Kind.RANGE,
        range_lower_inclusive=1,
    )
    assert "rangeUpperInclusive" in C.effort_contract_missing(contract)
    contract["rangeUpperInclusive"] = 1  # 상·하한 동일(경계값)도 완전한 domain이다
    assert C.effort_contract_is_complete(contract) is True


def test_model_id_required_contract_needs_exact_field_path():
    """model ID를 요구하는 계약은 exact field path 없이 완전해질 수 없다."""
    contract = _route_contract()
    contract["modelIdFieldPath"] = None
    assert "modelIdFieldPath" in C.route_contract_missing(contract)
    contract["modelIdRequired"] = False
    assert C.route_contract_is_complete(contract) is True


# ═════════════════════════════════════════════════════════════════
# 4. 타입 불일치 → Malformed
# ═════════════════════════════════════════════════════════════════
@pytest.mark.parametrize(
    "field,bad_value",
    [
        ("modelId", 123),
        ("modelId", None),
        ("provider", ["p"]),
        ("candidateLabel", 0),
        ("catalogFingerprint", 1.5),
        ("revision", None),
        ("capabilityFingerprint", 7),
        ("displayName", 42),
        ("invocationModelIds", _MODEL_SYMBOL),
        ("invocationModelIds", [1, 2]),
        ("evidence", _EVIDENCE_ID),
        ("evidence", [None]),
        ("routes", []),
        ("effort", "none"),
        ("schemaVersion", "1"),
        ("schemaVersion", True),
    ],
)
def test_entry_type_mismatch_is_malformed(field, bad_value):
    entry = _valid_entry()
    entry[field] = bad_value
    reasons = C.validate_entry(entry)
    assert C.TYPE_MISMATCH in _codes(reasons), reasons
    assert C.is_malformed(entry) is True


@pytest.mark.parametrize("container", ["routes", "effort"])
def test_non_object_route_or_effort_entry_is_type_mismatch(container):
    entry = _valid_entry()
    entry[container][_ROUTE_A] = ["not", "an", "object"]
    reasons = C.validate_entry(entry)
    assert C.TYPE_MISMATCH in _codes(reasons), reasons


@pytest.mark.parametrize(
    "field,bad_value",
    [
        ("fallbackRank", "0"),
        ("fallbackRank", 1.5),
        ("minOutputBound", []),
        ("optionalFields", "optionalSymbolA"),
        ("purposes", [1]),
        ("messageFieldPath", "messagePathSymbolA"),
        ("evidenceRef", 5),
    ],
)
def test_route_contract_type_mismatch(field, bad_value):
    entry = _valid_entry()
    entry["routes"][_ROUTE_A]["contract"][field] = bad_value
    reasons = C.validate_entry(entry)
    assert C.TYPE_MISMATCH in _codes(reasons), reasons


def test_effort_domain_value_type_mismatch():
    """valueType이 확정되면 저장된 domain 값도 그 타입을 따라야 한다."""
    entry = _valid_entry()
    entry["effort"][_ROUTE_A]["contract"]["enumValues"] = [_EFFORT_VALUE_SYMBOLS[0], 3]
    reasons = C.validate_entry(entry)
    assert any("value-type" in r for r in reasons), reasons


def test_entry_not_object_is_type_mismatch():
    assert C.validate_entry(["not", "an", "entry"]) == [f"{C.TYPE_MISMATCH}:entry(not-object)"]
    assert C.is_malformed(None) is True


def test_malformed_reason_codes_are_closed_set():
    """모든 판정 이유는 MALFORMED_CODES 닫힌 집합의 코드만 사용한다."""
    entry = _valid_entry()
    entry["verificationStatus"] = "VERIFED"          # enum 이탈
    entry["modelId"] = 5                              # 타입 불일치
    del entry["provider"]                             # 필수 필드 누락
    entry["routes"][_ROUTE_A]["evidenceRef"] = "unknown-ref"  # 참조 무결성
    reasons = C.validate_entry(entry, fingerprint_fn=lambda _e: "cfp1:sha256:" + "d" * 64)
    assert reasons, "판정 이유가 비어 있으면 Malformed 판정이 동작하지 않는다"
    assert _codes(reasons) <= set(C.MALFORMED_CODES), reasons
    # 반환값은 결정론적으로 정렬·중복 제거된다.
    assert reasons == sorted(set(reasons))


# ═════════════════════════════════════════════════════════════════
# 5. fingerprint 불일치 · evidence 참조 무결성 → Malformed
# ═════════════════════════════════════════════════════════════════
def test_fingerprint_mismatch_is_malformed():
    """저장된 Capability_Fingerprint와 재계산 결과가 다르면 Malformed다."""
    entry = _valid_entry()
    assert C.validate_entry(entry, fingerprint_fn=lambda e: e["capabilityFingerprint"]) == []
    reasons = C.validate_entry(entry, fingerprint_fn=lambda _e: "cfp1:sha256:" + "e" * 64)
    assert f"{C.FINGERPRINT_MISMATCH}:capabilityFingerprint" in reasons, reasons


def test_fingerprint_compute_failure_is_malformed():
    """fingerprint를 계산할 수 없으면 그 값을 신뢰하지 않는다."""
    def _boom(_entry):
        raise RuntimeError("계산 불가")

    reasons = C.validate_entry(_valid_entry(), fingerprint_fn=_boom)
    assert C.FINGERPRINT_MISMATCH in _codes(reasons), reasons
    assert any("compute-failed:RuntimeError" in r for r in reasons), reasons


@pytest.mark.parametrize("container", ["routes", "effort"])
def test_dangling_evidence_ref_is_integrity_failure(container):
    """entry evidence 목록에 없는 evidence reference는 참조 무결성 실패다."""
    entry = _valid_entry()
    entry[container][_ROUTE_A]["evidenceRef"] = "evr1:sha256:" + "f" * 64
    reasons = C.validate_entry(entry)
    assert C.EVIDENCE_INTEGRITY in _codes(reasons), reasons
    assert any("unknown-ref" in r for r in reasons), reasons


def test_empty_evidence_ref_is_integrity_failure():
    entry = _valid_entry()
    entry["evidence"] = [C.UNDETERMINED]
    entry["routes"][_ROUTE_A]["evidenceRef"] = C.UNDETERMINED
    reasons = C.validate_entry(entry)
    assert C.EVIDENCE_INTEGRITY in _codes(reasons), reasons
    assert any("empty-ref" in r for r in reasons), reasons


def test_evidence_id_unknown_to_store_is_integrity_failure():
    """저장소에 없는 Evidence_Record_ID를 참조하면 무결성 실패다."""
    entry = _valid_entry()
    reasons = C.validate_entry(entry, known_evidence_ids=[])
    assert C.EVIDENCE_INTEGRITY in _codes(reasons), reasons
    assert C.validate_entry(entry, known_evidence_ids=[_EVIDENCE_ID]) == []


@pytest.mark.parametrize("container,marker", [("routes", "supported-route"), ("effort", "supported-effort")])
def test_supported_status_requires_current_evidence(container, marker):
    entry = _valid_entry()
    entry[container][_ROUTE_A]["evidenceRef"] = None
    reasons = C.validate_entry(entry)
    assert any(marker in r and C.EVIDENCE_INTEGRITY in r for r in reasons), reasons


# ═════════════════════════════════════════════════════════════════
# 6. 시각 필드는 UTC ISO 8601만 (요구사항 3.11)
# ═════════════════════════════════════════════════════════════════
@pytest.mark.parametrize(
    "value",
    [
        "2026-08-03T12:34:56.000000Z",
        "2026-08-03T12:34:56Z",
        "2026-08-03T12:34:56+00:00",
        "2026-08-03T12:34:56.123456+00:00",
    ],
)
def test_utc_iso8601_accepted(value):
    assert C.is_utc_iso8601(value) is True


@pytest.mark.parametrize(
    "value",
    [
        "2026-08-03T12:34:56+09:00",   # offset 0이 아님
        "2026-08-03T12:34:56",         # tz 없음(naive)
        "2026-08-03 12:34:56Z",        # `T` 구분자 없음
        "2026-08-03",                  # 날짜만
        "1785000000",                  # epoch
        "not-a-time",
        "",
        None,
        0,
    ],
)
def test_non_utc_iso8601_rejected(value):
    assert C.is_utc_iso8601(value) is False


def test_utc_now_iso_is_utc_iso8601():
    now = C.utc_now_iso()
    assert C.is_utc_iso8601(now) is True
    assert now.endswith("Z")


def test_to_utc_iso_normalizes_offset_and_rejects_naive():
    from datetime import datetime, timedelta, timezone

    aware = datetime(2026, 8, 3, 21, 34, 56, tzinfo=timezone(timedelta(hours=9)))
    converted = C.to_utc_iso(aware)
    assert converted == "2026-08-03T12:34:56.000000Z"
    assert C.is_utc_iso8601(converted) is True
    with pytest.raises(ValueError):
        C.to_utc_iso(datetime(2026, 8, 3, 12, 34, 56))
    with pytest.raises(TypeError):
        C.to_utc_iso("2026-08-03T12:34:56Z")


@pytest.mark.parametrize(
    "bad_time",
    ["2026-08-03T12:34:56+09:00", "2026-08-03T12:34:56", "2026-08-03", "yesterday", 0],
)
def test_entry_time_field_must_be_utc_iso8601(bad_time):
    entry = _valid_entry()
    entry["verifiedAt"] = bad_time
    reasons = C.validate_entry(entry)
    assert C.TYPE_MISMATCH in _codes(reasons), reasons
    assert any("verifiedAt" in r for r in reasons), reasons


def test_entry_time_field_may_be_undetermined_but_record_time_may_not():
    """entry의 빈 시각은 미확정 표현이지만, Verification_Record는 실제 시각을 요구한다."""
    entry = _valid_entry()
    entry["verifiedAt"] = C.UNDETERMINED
    assert C.validate_entry(entry) == []

    record = _valid_record()
    record["verifiedAt"] = C.UNDETERMINED
    reasons = C.validate_verification_record(record)
    assert any("verifiedAt" in r and "not-utc-iso8601" in r for r in reasons), reasons


@pytest.mark.parametrize("field", C.REQUIRED_VERIFICATION_RECORD_FIELDS)
def test_missing_verification_record_field_is_malformed(field):
    record = _valid_record()
    del record[field]
    reasons = C.validate_verification_record(record)
    assert f"{C.MISSING_FIELD}:record.{field}" in reasons, reasons


@pytest.mark.parametrize("value,ok", [({"inputTokens": 1}, True), (C.NOT_PROVIDED, True), (0, False), ("free", False)])
def test_verification_record_usage_cost_types(value, ok):
    """usage·cost는 Gateway 제공 dict 또는 `notProvided`만 허용한다."""
    for field in ("usage", "cost"):
        record = _valid_record()
        record[field] = value
        reasons = C.validate_verification_record(record)
        assert (reasons == []) is ok, reasons


# ═════════════════════════════════════════════════════════════════
# 7. userData 루트 해석과 루트 밖 경로 거부 (요구사항 10.15)
# ═════════════════════════════════════════════════════════════════
def test_explicit_root_wins_over_environment(tmp_path):
    explicit = tmp_path / "explicit"
    env = {"AE_USERDATA_PATH": str(tmp_path / "env"), "AE_GENERATED_ROOT": str(tmp_path / "gen")}
    assert S.resolve_user_data_root(str(explicit), env) == explicit


def test_userdata_env_is_used_when_no_explicit_root(tmp_path):
    env = {"AE_USERDATA_PATH": str(tmp_path / "env")}
    assert S.resolve_user_data_root(None, env) == tmp_path / "env"


def test_generated_root_env_resolves_parent_userdata(tmp_path):
    """Electron은 `{userData}/generated`를 주입하므로 부모가 userData 루트다."""
    env = {"AE_GENERATED_ROOT": str(tmp_path / "userData" / "generated")}
    assert S.resolve_user_data_root(None, env) == tmp_path / "userData"
    # 폴백 레이아웃(basename이 generated가 아님)은 값 자체가 루트.
    env = {"AE_GENERATED_ROOT": str(tmp_path / "custom")}
    assert S.resolve_user_data_root(None, env) == tmp_path / "custom"


def test_default_root_is_user_scoped_and_absolute():
    root = S.resolve_user_data_root(None, {})
    assert root == S.Path(os.path.abspath(os.path.expanduser("~/.agentic-editor")))
    assert root.is_absolute()


def test_path_builders_stay_under_capability_root(tmp_path):
    store = _store(tmp_path)
    paths = {
        "map": store.capability_map_path(),
        "effort": store.effort_settings_path(),
        "baseline": store.baseline_path(_REVISION_SYMBOL),
        "evidence": store.evidence_path(_EVIDENCE_ID),
        "catalog": store.catalog_path(_CATALOG_FP),
        "run": store.run_path("runSymbolA"),
    }
    for name, path in paths.items():
        assert S.is_within(path, store.user_data_root), name
        assert str(path).startswith(str(store.capability_root) + os.sep), name
    assert paths["map"].name == S.CAPABILITY_MAP_FILENAME
    assert paths["effort"].name == S.EFFORT_SETTINGS_FILENAME
    assert paths["baseline"].parent.name == S.BASELINE_DIRNAME
    assert paths["evidence"].parent.name == S.EVIDENCE_DIRNAME
    assert paths["catalog"].parent.name == S.CATALOG_DIRNAME
    assert paths["run"].parent.name == S.RUNS_DIRNAME
    # `evr1:sha256:…`의 `:`는 파일명 안전 문자가 아니므로 치환된다.
    assert ":" not in paths["evidence"].name
    assert paths["evidence"].name.startswith("evr1_sha256_")


@pytest.mark.parametrize(
    "escape",
    [
        "../outside.json",
        "capability/../../outside.json",
        "capability/evidence/../../../outside.json",
        "..",
    ],
)
def test_relative_escape_paths_are_rejected(tmp_path, escape):
    store = _store(tmp_path)
    with pytest.raises(S.PathOutsideRootError):
        store.resolve(escape)
    with pytest.raises(S.PathOutsideRootError):
        store.write_json(escape, {"k": "v"})
    with pytest.raises(S.PathOutsideRootError):
        store.read_json(escape)


def test_home_expanded_path_is_rejected(tmp_path):
    """`~` 확장이 홈 디렉터리로 나가면 거부한다(실제 userData 오염 방지)."""
    store = _store(tmp_path)
    home_target = "~/outside-capability.json"
    with pytest.raises(S.PathOutsideRootError):
        store.write_json(home_target, {"k": "v"})
    with pytest.raises(S.PathOutsideRootError):
        store.read_json(home_target)
    assert not os.path.exists(os.path.expanduser(home_target))
    # 루트 기준 경로 성분으로서의 `~`는 루트를 벗어나지 않으므로 루트 하위로 남는다.
    assert S.is_within(store.resolve("~", "inside.json"), store.user_data_root)


def test_absolute_path_outside_root_is_rejected(tmp_path):
    store = _store(tmp_path)
    outside = tmp_path / "outside" / "leak.json"
    with pytest.raises(S.PathOutsideRootError):
        store.write_json(str(outside), {"k": "v"})
    with pytest.raises(S.PathOutsideRootError):
        store.read_json(str(outside))
    assert not outside.exists()


def test_absolute_path_inside_root_is_accepted(tmp_path):
    store = _store(tmp_path)
    target = store.capability_map_path()
    store.write_json(str(target), {"schemaVersion": C.SCHEMA_VERSION, "entries": {}})
    assert store.read_json(str(target))["schemaVersion"] == C.SCHEMA_VERSION


def test_symlink_escape_is_rejected(tmp_path):
    """루트 하위 symlink가 밖을 가리켜도 realpath 정규화 후 거부된다."""
    store = _store(tmp_path)
    outside = tmp_path / "outside"
    outside.mkdir()
    store.capability_root.mkdir(parents=True, exist_ok=True)
    link = store.capability_root / "escape"
    os.symlink(str(outside), str(link))

    with pytest.raises(S.PathOutsideRootError):
        store.write_json("capability/escape/leak.json", {"k": "v"})
    with pytest.raises(S.PathOutsideRootError):
        store.read_json("capability/escape/leak.json")
    assert list(outside.iterdir()) == []


def test_read_json_safe_swallows_outside_root(tmp_path):
    store = _store(tmp_path)
    assert store.read_json_safe("../outside.json", default={"fallback": True}) == {"fallback": True}


@pytest.mark.parametrize(
    "raw",
    ["../../etc/passwd", "/etc/passwd", "a/b/../../../c", "..", ".", "", None, "  ", "evr1:sha256:" + "a" * 64],
)
def test_safe_component_never_escapes(raw):
    name = S.safe_component(raw)
    assert name, "빈 성분은 경로를 모호하게 만든다"
    assert os.sep not in name and "/" not in name and "\\" not in name
    assert name not in (".", "..")
    assert ":" not in name
    assert len(name) <= 100
    # 동일 입력은 항상 동일 성분(저장·조회 왕복 보존)
    assert S.safe_component(raw) == name


def test_safe_component_truncates_long_input_deterministically():
    long_raw = "z" * 400
    name = S.safe_component(long_raw)
    assert len(name) <= 100
    assert name == S.safe_component(long_raw)
    assert S.safe_component("z" * 401) != name


def test_traversal_in_dynamic_path_component_stays_inside(tmp_path):
    """revision·evidence ID 같은 동적 성분에 상위참조를 넣어도 루트를 벗어나지 않는다."""
    store = _store(tmp_path)
    for path in (
        store.baseline_path("../../../etc/passwd"),
        store.evidence_path("../../outside"),
        store.catalog_path("/absolute/catalog"),
        store.run_path(".."),
    ):
        assert S.is_within(path, store.user_data_root), path


# ═════════════════════════════════════════════════════════════════
# 8. 원자적 쓰기와 파일 무결성 (요구사항 10.15)
# ═════════════════════════════════════════════════════════════════
def _tmp_leftovers(root):
    return [str(p) for p in S.Path(root).rglob("*.tmp")]


def test_write_json_roundtrip_leaves_no_temp_file(tmp_path):
    store = _store(tmp_path)
    entry = _valid_entry()
    payload = {"schemaVersion": C.SCHEMA_VERSION, "updatedAt": C.utc_now_iso(), "entries": {_MODEL_SYMBOL: entry}}

    written = store.write_capability_map(payload)
    assert written.is_file()
    assert _tmp_leftovers(store.user_data_root) == []

    loaded = store.read_capability_map()
    assert loaded == json.loads(written.read_text(encoding="utf-8"))
    # 정제는 capability 계약 필드를 손상시키지 않는다 → 읽은 entry가 여전히 유효하다.
    assert C.validate_entry(loaded["entries"][_MODEL_SYMBOL]) == []
    assert C.is_utc_iso8601(loaded["updatedAt"]) is True


def test_overwrite_replaces_previous_content_entirely(tmp_path):
    store = _store(tmp_path)
    store.write_run_report("runSymbolA", {"long": "x" * 5000})
    store.write_run_report("runSymbolA", {"short": 1})
    text = store.run_path("runSymbolA").read_text(encoding="utf-8")
    assert json.loads(text) == {"short": 1}
    assert "x" * 100 not in text  # 이전 내용의 잔여 바이트가 남지 않는다
    assert _tmp_leftovers(store.user_data_root) == []


def test_replace_failure_preserves_previous_complete_file(tmp_path, monkeypatch):
    """교체가 실패하면 독자는 이전 완전본만 본다(부분 기록 파일 없음)."""
    store = _store(tmp_path)
    store.write_effort_settings({"generation": 1})

    def _boom(_src, _dst):
        raise OSError("replace 실패")

    monkeypatch.setattr(S.os, "replace", _boom)
    with pytest.raises(S.StoreError):
        store.write_effort_settings({"generation": 2})
    monkeypatch.undo()

    assert store.read_effort_settings() == {"generation": 1}
    assert _tmp_leftovers(store.user_data_root) == []


def test_atomic_write_bytes_creates_parents_and_exact_bytes(tmp_path):
    store = _store(tmp_path)
    target = store.resolve(S.CAPABILITY_DIRNAME, "deep", "nested", "blob.json")
    data = json.dumps({"한글": "값", "n": 1}, ensure_ascii=False).encode("utf-8")
    S.atomic_write_bytes(target, data)
    assert target.read_bytes() == data
    assert _tmp_leftovers(store.user_data_root) == []


def test_read_json_missing_file_returns_default(tmp_path):
    store = _store(tmp_path)
    sentinel = {"missing": True}
    assert store.read_capability_map(default=sentinel) is sentinel
    assert store.read_effort_settings() is None
    assert store.list_evidence_ids() == []
    assert store.list_run_ids() == []


def test_corrupted_json_raises_store_error_and_safe_read_falls_back(tmp_path):
    store = _store(tmp_path)
    S.atomic_write_bytes(store.capability_map_path(), b"{not json")
    with pytest.raises(S.StoreError):
        store.read_capability_map()
    assert store.read_json_safe(store.capability_map_path(), default={}) == {}


def test_unserializable_payload_raises_store_error_without_writing(tmp_path):
    store = _store(tmp_path)
    with pytest.raises(S.StoreError):
        store.write_run_report("runSymbolB", {"obj": object()})
    assert not store.run_path("runSymbolB").exists()
    assert _tmp_leftovers(store.user_data_root) == []


def test_evidence_and_run_listing_roundtrip(tmp_path):
    store = _store(tmp_path)
    store.write_evidence(_EVIDENCE_ID, _valid_record())
    store.write_run_report("runSymbolA", {"revision": _REVISION_SYMBOL})
    assert store.evidence_path(_EVIDENCE_ID).stem in store.list_evidence_ids()
    assert "runSymbolA" in store.list_run_ids()
    stored = store.read_evidence(_EVIDENCE_ID)
    assert stored["evidenceRecordId"] == _EVIDENCE_ID
    assert C.is_utc_iso8601(stored["verifiedAt"]) is True
    # 정제를 거친 record도 계약 검증을 통과한다.
    assert C.validate_verification_record(stored) == []


# ═════════════════════════════════════════════════════════════════
# 9. sanitizer — credential·authorization·cookie·signature 제거
# ═════════════════════════════════════════════════════════════════
_SECRET_VALUE = "AKIAIOSFODNN7EXAMPLESECRET"

_CREDENTIAL_KEYS = [
    "Authorization",
    "authorization",
    "Cookie",
    "set-cookie",
    "signature",
    "X-Amz-Signature",
    "credentials",
    "credential",
    "accessKeyId",
    "secretAccessKey",
    "sessionToken",
    "X-Amz-Security-Token",
    "apitoken",
    "apiKey",
    "password",
    "passphrase",
    "clientSecret",
    "privateKey",
    "bearer",
    "token",
    "secret",
]


@pytest.mark.parametrize("key", _CREDENTIAL_KEYS)
def test_sanitizer_drops_credential_keys(key):
    cleaned = S.sanitize_for_persist({key: _SECRET_VALUE, "keepSymbol": "keepValue"})
    assert key not in cleaned
    assert cleaned == {"keepSymbol": "keepValue"}
    assert S.classify_key(key) == "DROP"


def test_sanitizer_drops_credential_keys_at_any_depth():
    payload = {
        "routeResults": [
            {
                "routeKey": _ROUTE_A,
                "request": {
                    "headers": {"Authorization": _SECRET_VALUE, "Cookie": "session=" + _SECRET_VALUE},
                    "nested": [{"X-Amz-Signature": _SECRET_VALUE, "keepSymbol": 1}],
                },
                "credentials": {"accessKeyId": _SECRET_VALUE, "secretAccessKey": _SECRET_VALUE},
            }
        ]
    }
    cleaned = S.sanitize_for_persist(payload)
    text = json.dumps(cleaned, ensure_ascii=False)
    assert _SECRET_VALUE not in text
    for needle in ("Authorization", "Cookie", "Signature", "credentials", "accessKeyId"):
        assert needle not in text
    assert cleaned["routeResults"][0]["request"]["nested"][0] == {"keepSymbol": 1}
    # 입력은 변경되지 않는다(새 구조 반환).
    assert payload["routeResults"][0]["credentials"]["accessKeyId"] == _SECRET_VALUE


def test_sanitizer_preserves_legitimate_capability_fields():
    """`maxTokens`·`inputTokens`·`minOutputBound` 같은 정상 field는 지우지 않는다."""
    entry = _valid_entry()
    assert S.sanitize_for_persist(entry) == entry

    usage = {"maxTokens": 4096, "inputTokens": 12, "outputTokens": 3, "minOutputBound": {"boundSymbolA": 1}}
    assert S.sanitize_for_persist(usage) == usage


def test_sanitizer_replaces_raw_prompt_with_probe_id():
    payload = {
        "probeId": "probe:symbolA",
        "prompt": "사용자 원문 프롬프트",
        "messages": [{"text": "원문"}],
        "nested": {"systemPrompt": "원문 시스템 지시문"},
    }
    cleaned = S.sanitize_for_persist(payload)
    assert cleaned["prompt"] == "probe:symbolA"
    assert cleaned["messages"] == "probe:symbolA"
    assert cleaned["nested"]["systemPrompt"] == "probe:symbolA"
    assert "원문" not in json.dumps(cleaned, ensure_ascii=False)


def test_sanitizer_uses_unknown_probe_sentinel_when_absent():
    cleaned = S.sanitize_for_persist({"prompt": "원문"})
    assert cleaned["prompt"] == S.PROBE_ID_UNKNOWN


def test_sanitizer_replaces_raw_body_with_sanitized_schema():
    body = {
        "modelId": _MODEL_SYMBOL,
        "messages": [{"role": "user", "content": [{"text": "원문 프롬프트"}]}],
        "status": "COMPLETED",
        "Authorization": _SECRET_VALUE,
    }
    cleaned = S.sanitize_for_persist({"probeId": "probe:symbolB", "requestBody": body})
    schema = cleaned["requestBody"]
    assert schema["type"] == "object"
    assert "Authorization" not in schema["fields"]           # schema 파생에서도 제거
    assert schema["fields"]["modelId"] == {"type": "string", "length": len(_MODEL_SYMBOL)}
    assert schema["statusFields"] == {"status": "COMPLETED"}  # 필요한 status만 값 보존
    text = json.dumps(cleaned, ensure_ascii=False)
    assert "원문 프롬프트" not in text and _SECRET_VALUE not in text


def test_sanitized_schema_keeps_only_shape_information():
    schema = S.sanitized_schema({"a": "값", "b": [1, 2, 3], "c": {"d": True}, "n": None})
    assert schema["fieldCount"] == 4
    assert schema["fields"]["a"] == {"type": "string", "length": 1}
    assert schema["fields"]["b"]["type"] == "array" and schema["fields"]["b"]["count"] == 3
    assert schema["fields"]["c"]["type"] == "object"
    assert schema["fields"]["n"] == {"type": "null"}
    assert "값" not in json.dumps(schema, ensure_ascii=False)


def test_write_json_enforces_sanitization_on_disk(tmp_path):
    """저장 직전 정제는 기본 동작이므로 비밀정보가 디스크에 남지 않는다."""
    store = _store(tmp_path)
    record = _valid_record()
    record["routeResults"][0].update({
        "Authorization": _SECRET_VALUE,
        "cookie": "session=" + _SECRET_VALUE,
        "signature": _SECRET_VALUE,
        "credentials": {"accessKeyId": _SECRET_VALUE, "secretAccessKey": _SECRET_VALUE},
        "prompt": "원문 프롬프트",
        "requestBody": {"messages": [{"text": "원문 프롬프트"}], "status": 200},
    })

    path = store.write_evidence(_EVIDENCE_ID, record)
    text = path.read_text(encoding="utf-8")
    assert _SECRET_VALUE not in text
    assert "원문 프롬프트" not in text
    for needle in ("Authorization", "cookie", "signature", "credentials", "accessKeyId"):
        assert needle not in text
    stored = json.loads(text)
    assert stored["routeResults"][0]["prompt"] == "probe:routeSymbolA"
    assert stored["routeResults"][0]["requestBody"]["type"] == "object"


# ═════════════════════════════════════════════════════════════════
# 10. 로그 — Probe_ID와 Sanitized_Schema만
# ═════════════════════════════════════════════════════════════════
def test_log_probe_emits_only_probe_id_and_schema():
    line = S.log_probe(
        "probe:symbolC",
        body={"messages": [{"text": "원문"}], "Authorization": _SECRET_VALUE, "status": 200},
        extra={"modelId": _MODEL_SYMBOL, "route": _ROUTE_A, "authorization": _SECRET_VALUE},
        emit=False,
    )
    assert _SECRET_VALUE not in line
    assert "원문" not in line
    payload = json.loads(line.split(" ", 1)[1])
    assert payload["probeId"] == "probe:symbolC"
    assert payload["sanitizedSchema"]["type"] == "object"
    assert payload["modelId"] == _MODEL_SYMBOL
    assert "authorization" not in payload  # 화이트리스트 밖 field는 버린다


def test_log_fields_truncate_long_values():
    fields = S.probe_log_fields("probe:symbolD", extra={"modelId": "y" * 500})
    assert len(fields["modelId"]) == S._LOG_VALUE_MAX


def test_log_fields_fall_back_to_unknown_probe():
    assert S.probe_log_fields("")["probeId"] == S.PROBE_ID_UNKNOWN
    assert S.probe_log_fields(None)["probeId"] == S.PROBE_ID_UNKNOWN


def test_mask_token_for_log_delegates_to_existing_masker():
    """토큰 마스킹은 기존 `gateway_module.mask_token`에 위임한다(신규 규칙 없음)."""
    from ai_engine.gateway_module import mask_token

    token = "abcdefghijklmnop"
    masked = S.mask_token_for_log(token)
    assert masked == mask_token(token)
    assert token not in masked
    assert S.mask_token_for_log("") == "****"
    assert S.mask_token_for_log(None) == "****"


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-q"]))
