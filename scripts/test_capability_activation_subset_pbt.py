# Feature: gateway-models-effort-support, Property 1: Activation subset
# *For any* Capability_Map(모든 Verification_Status, Malformed_Entry, Seed_Entry, mock 출처
# evidence, 미발견 Candidate_Label을 섞어 생성한 임의 입력)에 대해, Activation_Gate가 산출한
# Active_Model 집합은 항상 `{유효 entry ∧ verificationStatus == VERIFIED ∧ non-empty modelId ∧
# non-empty provider ∧ 현재 Capability_Fingerprint 일치 ∧ Complete_Record ∧ Eligible_Contract
# ≥ 1 ∧ Current_Evidence 보유 ∧ sourceKind ≠ SEED}`의 부분집합이며, Managed_Segment 노출
# 집합은 Active_Model 집합과 정확히 같다. Candidate_Label 문자열은 어떤 경우에도 모델 항목으로
# 노출되지 않는다.
"""Property 1 (Activation subset) property test — task 5.4.

검증 대상 모듈:
  ``ai_engine/capability/activation_gate.py``
    - ``active_models``      Active_Model 노출 집합(duplicate 축약·tie 규칙 적용 후)
    - ``is_active``          entry 단위 활성 판정과 대표 탈락 이유 코드
    - ``activation_report``  Active_Model + 탈락 이유 record(보고서 입력)
  ``ai_engine/capability/capability_map.py``
    - ``merge_active_into_catalog``  Managed_Segment를 Baseline_Catalog_Segment에 병합
    - ``to_ui_payload``             `/api/models` 신규 `capabilities` 값 투영

단정 요약 (design.md Correctness Properties → Property 1):
  R1. **부분집합** — `active_models`가 산출한 모든 entry는 Property 1의 조건 집합을 만족한다.
      조건은 :func:`_condition_failures`가 production 판정기를 쓰지 않고 **독립적으로**
      계산한다(유효 entry, `VERIFIED`, non-empty modelId·provider, fingerprint 재계산 일치,
      Complete_Record, Eligible_Contract ≥ 1, Current_Evidence, 비-Seed). 노출 entry는 입력
      map에 있던 entry와 canonical 동일해야 하며(값 발명 금지), Exact_Model_ID는 비어 있지
      않고 중복이 없다(6.1~6.7, 6.9~6.13, 6.26).
  R2. **적대적 클래스 0건** — 라벨만 아는 초기 entry, `VERIFIED`를 자칭하는 라벨 전용 entry,
      상태를 비튼 entry, Seed_Entry, 빈 provider, allowlist 거부, 요청 목적 불충족,
      Malformed mutation 주입 entry는 어떤 경우에도 Active_Model에 나타나지 않는다
      (1.15, 1.17, 6.8~6.13).
  R3. **Managed_Segment 노출 == Active_Model** — `to_ui_payload`의 `modelIds`는 Active_Model
      Exact_Model_ID 목록과 순서까지 같고 `models` 키 집합도 같다.
      `merge_active_into_catalog`가 baseline에 **추가한** 항목의 id 집합은
      `Active_Model 집합 − baseline 집합`과 정확히 같으며, 각 추가 항목은 그 entry의
      Provider_String 그룹에 `{"id","name"}` 형태로만 들어간다. baseline 항목은 순서까지
      보존되고, Managed_Segment가 비면 카탈로그는 **동일 객체로 그대로** 반환된다
      (6.14, 12.1, 12.2).
  R4. **Candidate_Label 미노출** — 노출 항목과 UI payload에는 `candidateLabel`·`displayName`
      키가 존재하지 않고, 라벨 문자열이 노출 문자열 집합에 나타나지 않는다. Exact_Model_ID가
      비어 있는 entry(미발견 라벨)는 어떤 노출 항목도 만들지 않는다(6.26).
  R5. **탈락 이유의 완전성** — 노출되지 않은 모든 entry는 닫힌 집합
      (``activation_gate.DEACTIVATION_REASONS``)의 이유 코드 record를 정확히 하나 갖고,
      record에는 비밀정보가 없는 식별 필드만 실린다. 판정은 입력 map을 변경하지 않는다.

"mock 출처 evidence"는 구조적으로 `sourceKind == SEED`(실제 Gateway 근거 없이 파일·상수·
fallback으로 제공된 entry)와 evidence 미보유 entry로 표현한다. "미발견 Candidate_Label"은
`contracts.new_entry`가 만드는 전 필드 미확정 entry(Exact_Model_ID 빈 문자열)로 표현한다.

경계값(design.md "PBT 구성 규칙")은 입력 전략에서 명시적으로 포함한다: 빈 map, 단일 entry,
동일 modelId duplicate·동시각 tie·fingerprint 불일치 형제, 빈 문자열 ID, 빈 baseline 카탈로그,
baseline과 id가 겹치는 Managed_Segment 후보, ctx 미제공(`{}`)과 ctx 불일치 전수.

model ID·provider·route 지원 여부·effort field path·effort 허용값은 확정 상수 없이 무작위
심볼로만 생성한다(`scripts/_capability_strategies.py`). 이 테스트는 순수 로직만 구동하며
Gateway·네트워크에 접근하지 않고, 결과는 Gateway 지원 근거가 아니다(Requirement 12.22).

실패 시 최소화된 counterexample과 재현 정보를
``.generated/pbt/capability_activation_subset.json`` 에 기록한다(Validation_Runner 보고서
``pbt.counterexamples`` 입력, 작업 12.2). `print_blob=True` 로 재현 blob도 함께 출력된다.

실행:
  ai_engine/.venv/bin/python -m pytest scripts/test_capability_activation_subset_pbt.py -q

**Validates: Requirements 1.15, 1.17, 6.1, 6.2, 6.3, 6.4, 6.5, 6.6, 6.7, 6.9, 6.10, 6.11,
6.12, 6.13, 6.14, 6.26, 12.9, 12.22**
_Requirements: 6.1, 6.14, 12.9, 12.19, 12.21, 12.22_
"""
from __future__ import annotations

import copy
import json
import os
import sys
from typing import Any, Iterable, Mapping, Sequence

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
from ai_engine.capability import (  # noqa: E402
    activation_gate,
    canonicalizer,
    capability_map,
    contracts,
)

# ─────────────────────────────────────────────────────────────────
# property 식별 (보고서 기록용)
# ─────────────────────────────────────────────────────────────────
FEATURE = "gateway-models-effort-support"
PROPERTY_ID = 1
PROPERTY_LABEL = "Activation subset"

_VERIFIED = str(contracts.Verification_Status.VERIFIED)
_SEED = str(contracts.Source_Kind.SEED)
_SUPPORTED_ROUTE = str(contracts.Route_Support_Status.SUPPORTED)
_ALLOWED = str(contracts.Allowlist_Result.ALLOWED)

#: 파생 모드 상태 필드 ← execution mode (design.md Data Models, Requirement 3.20~3.22).
_MODE_FIELD_BY_EXECUTION_MODE: dict[str, str] = {
    str(contracts.Execution_Mode.SYNC): "syncSupport",
    str(contracts.Execution_Mode.ASYNC): "asyncSupport",
    str(contracts.Execution_Mode.STREAMING): "streamingSupport",
}

#: 파생 모드 유도 우선순위 — design.md "SUPPORTED > UNVERIFIED > UNSUPPORTED > NOT_ADVERTISED".
_MODE_PRIORITY: tuple[str, ...] = (
    str(contracts.Route_Support_Status.SUPPORTED),
    str(contracts.Route_Support_Status.UNVERIFIED),
    str(contracts.Route_Support_Status.UNSUPPORTED),
    str(contracts.Route_Support_Status.NOT_ADVERTISED),
)

#: 어떤 route도 그 mode에 귀속되지 않을 때의 파생 상태(미확정).
_DEFAULT_MODE_SUPPORT = str(contracts.Route_Support_Status.UNVERIFIED)

#: Managed_Segment 노출 항목에 절대 나타나면 안 되는 키(라벨·표시명).
LABEL_FIELDS: tuple[str, ...] = ("candidateLabel", "displayName")


# ─────────────────────────────────────────────────────────────────
# Property 1 조건 코드(닫힌 집합) — 조건 집합과 1:1
# ─────────────────────────────────────────────────────────────────
COND_NOT_OBJECT = "not-object"
COND_MALFORMED = "malformed-entry(6.8)"
COND_FINGERPRINT = "fingerprint-mismatch(6.4)"
COND_STATUS = "status-not-verified(6.1,6.9-6.12)"
COND_MODEL_ID = "empty-model-id(6.2,6.26)"
COND_PROVIDER = "empty-provider(6.3)"
COND_INCOMPLETE = "incomplete-record(6.5)"
COND_CONTRACT = "no-eligible-contract(6.6)"
COND_EVIDENCE = "no-current-evidence(6.7)"
COND_SEED = "seed-entry(6.13)"

CONDITION_CODES: tuple[str, ...] = (
    COND_NOT_OBJECT,
    COND_MALFORMED,
    COND_FINGERPRINT,
    COND_STATUS,
    COND_MODEL_ID,
    COND_PROVIDER,
    COND_INCOMPLETE,
    COND_CONTRACT,
    COND_EVIDENCE,
    COND_SEED,
)


# ─────────────────────────────────────────────────────────────────
# counterexample 기록 (Requirement 12.21)
# ─────────────────────────────────────────────────────────────────

#: 기록 디렉터리. `.generated/` 는 gitignore 대상이며 env로 재지정할 수 있다.
COUNTEREXAMPLE_DIR = os.environ.get("AE_PBT_COUNTEREXAMPLE_DIR") or os.path.join(
    _ROOT, ".generated", "pbt"
)

#: 기록 파일 경로(property 1개당 1파일).
COUNTEREXAMPLE_PATH = os.path.join(COUNTEREXAMPLE_DIR, "capability_activation_subset.json")

#: 재현 명령(보고서에 그대로 실을 수 있는 형태).
REPRODUCE_COMMAND = (
    f"AE_PBT_SEED={S.AE_PBT_SEED} ai_engine/.venv/bin/python -m pytest "
    "scripts/test_capability_activation_subset_pbt.py -q"
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


def _brief(value: Any) -> str:
    """실패 메시지용 축약 canonical 표현."""
    try:
        return canonicalizer.serialize(value)[:400]
    except Exception:  # pragma: no cover - 메시지 경로 방어
        return repr(value)[:400]


def _serialized(value: Any) -> str | None:
    """canonical serialization(의미 손실이면 ``None``)."""
    try:
        return canonicalizer.serialize(value)
    except Exception:
        return None


def _is_str_list(value: Any) -> bool:
    return isinstance(value, list) and all(isinstance(item, str) for item in value)


def _non_empty_str_list(value: Any) -> bool:
    return _is_str_list(value) and bool(value)


def _is_int(value: Any) -> bool:
    return isinstance(value, int) and not isinstance(value, bool)


def _is_number(value: Any) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool)


def _dict_keys_in(value: Any) -> set[str]:
    """중첩 구조의 모든 dict key를 모은다(라벨 필드 부재 확인용)."""
    keys: set[str] = set()
    if isinstance(value, dict):
        for key, item in value.items():
            keys.add(str(key))
            keys |= _dict_keys_in(item)
    elif isinstance(value, (list, tuple)):
        for item in value:
            keys |= _dict_keys_in(item)
    return keys


def _strings_in(value: Any) -> set[str]:
    """중첩 구조의 모든 문자열(키와 값)을 모은다(라벨 유출 확인용)."""
    found: set[str] = set()
    if isinstance(value, dict):
        for key, item in value.items():
            if isinstance(key, str):
                found.add(key)
            found |= _strings_in(item)
    elif isinstance(value, (list, tuple)):
        for item in value:
            found |= _strings_in(item)
    elif isinstance(value, str):
        found.add(value)
    return found


def _settle(entry: dict) -> dict:
    """파생 모드 상태를 routes에서 다시 유도하고 Capability_Fingerprint를 재계산한다.

    Complete_Record는 `syncSupport`/`asyncSupport`/`streamingSupport`가 routes에서 유도한
    값과 같기를 요구한다(Requirement 3.20~3.23). 파생 상태는 fingerprint 입력이 아니므로
    재계산 순서와 무관하게 저장 fingerprint는 항상 재계산값과 일치한다(Malformed 아님).
    """
    settled = capability_map.apply_mode_support(copy.deepcopy(entry))
    return S.refresh_fingerprint(settled)


# ─────────────────────────────────────────────────────────────────
# 독립 판정 — production 판정기를 쓰지 않고 조건 집합을 직접 계산한다
# ─────────────────────────────────────────────────────────────────
def _route_contract_complete(contract: Any) -> bool:
    """완전한 Route_Contract인지 독립 판정한다(design.md Data Models, Requirement 3.3)."""
    if not isinstance(contract, dict):
        return False
    for field in (
        "endpointRef",
        "httpMethod",
        "outputValidatorRef",
        "terminalConditionRef",
        "retryPolicyRef",
    ):
        if not (isinstance(contract.get(field), str) and contract.get(field)):
            return False
    if not contracts.Execution_Mode.has(contract.get("executionMode")):
        return False
    if not contracts.Signing_Service.has(contract.get("signingService")):
        return False
    requires_model_id = contract.get("modelIdRequired")
    if not isinstance(requires_model_id, bool):
        return False
    if requires_model_id and not _non_empty_str_list(contract.get("modelIdFieldPath")):
        return False
    if not _non_empty_str_list(contract.get("messageFieldPath")):
        return False
    if not _is_int(contract.get("fallbackRank")):
        return False
    if not _non_empty_str_list(contract.get("purposes")):
        return False
    bound = contract.get("minOutputBound")
    return isinstance(bound, dict) and bool(bound)


def _effort_contract_complete(contract: Any) -> bool:
    """완전한 Effort_Contract인지 독립 판정한다(Requirement 3.6)."""
    if not isinstance(contract, dict):
        return False
    if not _text(contract.get("modelId")):
        return False
    if contract.get("routeKey") not in S.KNOWN_ROUTES:
        return False
    if not _non_empty_str_list(contract.get("fieldPath")):
        return False
    if not contracts.Value_Type.has(contract.get("valueType")):
        return False
    kind = contract.get("domainKind")
    if kind == contracts.Domain_Kind.ENUM:
        values = contract.get("enumValues")
        return isinstance(values, list) and bool(values)
    if kind == contracts.Domain_Kind.RANGE:
        return _is_number(contract.get("rangeLowerInclusive")) and _is_number(
            contract.get("rangeUpperInclusive")
        )
    return False


def _evidence_ref_findings(ref: Any, path: str, refs: set[str]) -> list[str]:
    """evidence reference 타입·참조 무결성을 독립 판정한다(미확정 ``None``은 허용)."""
    if ref is None:
        return []
    if not isinstance(ref, str):
        return [f"type:{path}"]
    if not ref:
        return [f"evidence:{path}(empty-ref)"]
    if ref not in refs:
        return [f"evidence:{path}(unknown-ref)"]
    return []


def _route_entry_findings(route_entry: Any, route_key: str, refs: set[str]) -> list[str]:
    """Route_Entry 구조 무결성을 독립 판정한다(Requirement 3.2, 3.3, 3.4)."""
    path = f"routes.{route_key}"
    if not isinstance(route_entry, dict):
        return [f"type:{path}"]
    findings: list[str] = []
    for field in contracts.REQUIRED_ROUTE_ENTRY_FIELDS:
        if field not in route_entry:
            findings.append(f"missing:{path}.{field}")

    status = route_entry.get("status")
    if "status" in route_entry and not contracts.Route_Support_Status.has(status):
        findings.append(f"enum:{path}.status")
    if "allowlist" in route_entry and not contracts.Allowlist_Result.has(route_entry.get("allowlist")):
        findings.append(f"enum:{path}.allowlist")

    contract = route_entry.get("contract")
    if contract is not None:
        if not isinstance(contract, dict):
            findings.append(f"type:{path}.contract")
        else:
            for field in contracts.REQUIRED_ROUTE_CONTRACT_FIELDS:
                if field not in contract:
                    findings.append(f"missing:{path}.contract.{field}")
            if contract.get("routeKey") != route_key:
                findings.append(f"enum:{path}.contract.routeKey(route-key-mismatch)")
            findings.extend(
                _evidence_ref_findings(contract.get("evidenceRef"), f"{path}.contract.evidenceRef", refs)
            )

    findings.extend(_evidence_ref_findings(route_entry.get("evidenceRef"), f"{path}.evidenceRef", refs))

    # `SUPPORTED` route는 완전한 계약과 evidence reference를 요구한다(3.3, 3.4).
    if status == _SUPPORTED_ROUTE:
        if not _route_contract_complete(contract):
            findings.append(f"missing:{path}.contract(supported-route)")
        if not _text(route_entry.get("evidenceRef")):
            findings.append(f"evidence:{path}.evidenceRef(supported-route)")
    return findings


def _effort_entry_findings(
    effort_entry: Any, route_key: str, refs: set[str], model_id: Any
) -> list[str]:
    """Effort_Entry 구조 무결성을 독립 판정한다(Requirement 3.5, 3.6, 3.7)."""
    path = f"effort.{route_key}"
    if not isinstance(effort_entry, dict):
        return [f"type:{path}"]
    findings: list[str] = []
    for field in contracts.REQUIRED_EFFORT_ENTRY_FIELDS:
        if field not in effort_entry:
            findings.append(f"missing:{path}.{field}")

    status = effort_entry.get("status")
    if "status" in effort_entry and not contracts.Effort_Support_Status.has(status):
        findings.append(f"enum:{path}.status")

    contract = effort_entry.get("contract")
    if contract is not None:
        if not isinstance(contract, dict):
            findings.append(f"type:{path}.contract")
        else:
            for field in contracts.REQUIRED_EFFORT_CONTRACT_FIELDS:
                if field not in contract:
                    findings.append(f"missing:{path}.contract.{field}")
            if contract.get("routeKey") != route_key:
                findings.append(f"enum:{path}.contract.routeKey(route-key-mismatch)")
            if isinstance(model_id, str) and isinstance(contract.get("modelId"), str):
                if contract["modelId"] != model_id:
                    findings.append(f"enum:{path}.contract.modelId(model-id-mismatch)")
            findings.extend(
                _evidence_ref_findings(contract.get("evidenceRef"), f"{path}.contract.evidenceRef", refs)
            )

    findings.extend(_evidence_ref_findings(effort_entry.get("evidenceRef"), f"{path}.evidenceRef", refs))

    if status == contracts.Effort_Support_Status.SUPPORTED:
        if not _effort_contract_complete(contract):
            findings.append(f"missing:{path}.contract(supported-effort)")
        if not _text(effort_entry.get("evidenceRef")):
            findings.append(f"evidence:{path}.evidenceRef(supported-effort)")
    return findings


def _malformed_findings(entry: Mapping[str, Any]) -> list[str]:
    """Malformed_Entry 판정을 독립적으로 수행한다(fingerprint 비교는 별도 조건 코드).

    검사 범주는 requirements.md의 Malformed_Entry 정의와 같다: 필수 필드 누락, 타입 불일치
    (시각 형식 위반 포함), 닫힌 enum 이탈·계약 결속 키 불일치, evidence 참조 무결성 실패.
    """
    findings: list[str] = []
    for field in contracts.REQUIRED_ENTRY_FIELDS:
        if field not in entry:
            findings.append(f"missing:{field}")

    version = entry.get("schemaVersion")
    if not _is_int(version) or version not in contracts.SUPPORTED_SCHEMA_VERSIONS:
        findings.append("schemaVersion")

    for field in (
        "candidateLabel",
        "modelId",
        "provider",
        "catalogFingerprint",
        "revision",
        "capabilityFingerprint",
    ):
        if field in entry and not isinstance(entry[field], str):
            findings.append(f"type:{field}")
    display_name = entry.get("displayName")
    if display_name is not None and not isinstance(display_name, str):
        findings.append("type:displayName")
    if "invocationModelIds" in entry and not _is_str_list(entry["invocationModelIds"]):
        findings.append("type:invocationModelIds")

    verified_at = entry.get("verifiedAt")
    if not isinstance(verified_at, str) or (verified_at and not contracts.is_utc_iso8601(verified_at)):
        findings.append("type:verifiedAt(not-utc-iso8601)")

    if "sourceKind" in entry and not contracts.Source_Kind.has(entry["sourceKind"]):
        findings.append("enum:sourceKind")
    if "verificationStatus" in entry and not contracts.Verification_Status.has(
        entry["verificationStatus"]
    ):
        findings.append("enum:verificationStatus")
    for field in _MODE_FIELD_BY_EXECUTION_MODE.values():
        if field in entry and not contracts.Route_Support_Status.has(entry[field]):
            findings.append(f"enum:{field}")

    evidence = entry.get("evidence")
    refs: set[str] = set()
    if "evidence" in entry:
        if not _is_str_list(evidence):
            findings.append("type:evidence")
        else:
            refs = {ref for ref in evidence if ref}
            if any(not ref for ref in evidence):
                findings.append("evidence:empty-ref")

    for field in ("routes", "effort"):
        if field not in entry:
            continue
        container = entry[field]
        if not isinstance(container, dict):
            findings.append(f"type:{field}")
            continue
        for key in container:
            if key not in S.KNOWN_ROUTES:
                findings.append(f"enum:{field}.{key}(unknown-route-key)")

    routes = entry.get("routes")
    if isinstance(routes, dict):
        for key, route_entry in routes.items():
            if key in S.KNOWN_ROUTES:
                findings.extend(_route_entry_findings(route_entry, key, refs))
    effort = entry.get("effort")
    if isinstance(effort, dict):
        for key, effort_entry in effort.items():
            if key in S.KNOWN_ROUTES:
                findings.extend(_effort_entry_findings(effort_entry, key, refs, entry.get("modelId")))
    return findings


def _derived_mode_support(routes: Any) -> dict[str, str]:
    """파생 모드 상태를 Known_Route의 execution mode에서 독립적으로 유도한다(3.20~3.23).

    Requirement 3.20~3.22는 "execution mode가 sync/async/streaming인 **Known_Route**의
    상태"에서 유도하라고 한다 — mode는 route의 정의이므로 계약이 기록된 route에만 있는
    값이 아니다. 따라서 계약의 `executionMode`가 있으면 그것을, 없으면 Known_Route의
    정의값(:data:`contracts.DEFAULT_ROUTE_EXECUTION_MODES`)을 쓴다. 유도 **알고리즘**은
    여기서 직접 계산하므로 production 판정기(capability_map)와 독립적이다.
    """
    container = routes if isinstance(routes, dict) else {}
    buckets: dict[str, list[str]] = {mode: [] for mode in _MODE_FIELD_BY_EXECUTION_MODE}
    for route_key in S.KNOWN_ROUTES:
        route_entry = container.get(route_key)
        if not isinstance(route_entry, dict):
            continue
        status = route_entry.get("status")
        if not contracts.Route_Support_Status.has(status):
            continue
        contract = route_entry.get("contract")
        mode = contract.get("executionMode") if isinstance(contract, dict) else None
        if not contracts.Execution_Mode.has(mode):
            mode = contracts.DEFAULT_ROUTE_EXECUTION_MODES.get(route_key)
        if not contracts.Execution_Mode.has(mode):
            continue
        buckets[str(mode)].append(str(status))

    derived: dict[str, str] = {}
    for mode, field in _MODE_FIELD_BY_EXECUTION_MODE.items():
        statuses = buckets[mode]
        derived[field] = next(
            (candidate for candidate in _MODE_PRIORITY if candidate in statuses),
            _DEFAULT_MODE_SUPPORT,
        )
    return derived


def _incomplete_record_findings(entry: Mapping[str, Any]) -> list[str]:
    """Complete_Record가 아닌 이유를 독립 판정한다(모든 Known_Route 키 + 파생 상태 일치)."""
    findings: list[str] = []
    for field in ("routes", "effort"):
        container = entry.get(field)
        if not isinstance(container, dict):
            findings.append(f"type:{field}")
            continue
        for route_key in S.KNOWN_ROUTES:
            if route_key not in container:
                findings.append(f"missing:{field}.{route_key}")
    for field, expected in _derived_mode_support(entry.get("routes")).items():
        if _text(entry.get(field)) != expected:
            findings.append(f"derived-mismatch:{field}")
    return findings


def _eligible_route_keys(entry: Mapping[str, Any], purposes: Sequence[str]) -> list[str]:
    """Eligible_Contract를 가진 route key를 독립 판정한다(Requirement 6.6).

    조건: route `status == SUPPORTED` AND 완전한 Route_Contract(계약 route key 결속) AND
    Current_Evidence reference(entry evidence 목록 소속) AND 요청 목적 충족 AND
    allowlist `ALLOWED`.
    """
    routes = entry.get("routes")
    if not isinstance(routes, dict):
        return []
    evidence = entry.get("evidence")
    refs = {ref for ref in evidence if isinstance(ref, str) and ref} if _is_str_list(evidence) else set()
    wanted = {item for item in purposes if isinstance(item, str) and item}

    eligible: list[str] = []
    for route_key in S.KNOWN_ROUTES:
        route_entry = routes.get(route_key)
        if not isinstance(route_entry, dict):
            continue
        if route_entry.get("status") != _SUPPORTED_ROUTE:
            continue
        if route_entry.get("allowlist") != _ALLOWED:
            continue
        contract = route_entry.get("contract")
        if not _route_contract_complete(contract) or contract.get("routeKey") != route_key:
            continue
        available = {item for item in contract.get("purposes") or [] if isinstance(item, str) and item}
        if not available or not wanted.issubset(available):
            continue
        ref = route_entry.get("evidenceRef")
        if not (isinstance(ref, str) and ref and ref in refs):
            continue
        contract_ref = contract.get("evidenceRef")
        if isinstance(contract_ref, str) and contract_ref and contract_ref not in refs:
            continue
        eligible.append(route_key)
    return eligible


def _current_evidence_findings(entry: Mapping[str, Any], ctx: Mapping[str, Any]) -> list[str]:
    """Current_Evidence를 보유하지 않은 이유를 독립 판정한다(Requirement 6.7).

    Current_Evidence는 "Same_Gateway_Environment · Current_Revision · 현재
    Catalog_Fingerprint · 현재 Capability_Fingerprint와 일치하는 Verification_Record"다.
    entry 쪽 값이 비어 있으면 미확정이므로 현재로 간주하지 않는다. ctx가 주지 않은 항목은
    비교하지 않는다(모른다는 이유로 강등하지 않는다 — Activation_Gate 계약과 동일).
    """
    findings: list[str] = []
    evidence = entry.get("evidence")
    if not (_is_str_list(evidence) and any(ref for ref in evidence)):
        findings.append("no-evidence-ref")
    if not contracts.is_utc_iso8601(entry.get("verifiedAt")):
        findings.append("no-verified-at")

    model_id = _text(entry.get("modelId"))
    revision = _text(entry.get("revision"))
    expected_revision = _text(ctx.get("revision"))
    if not revision:
        findings.append("revision-unknown")
    elif expected_revision and revision != expected_revision:
        findings.append("revision-mismatch")

    catalog_fingerprint = _text(entry.get("catalogFingerprint"))
    expected_catalog = _text(ctx.get("catalogFingerprint"))
    if not catalog_fingerprint:
        findings.append("catalog-fingerprint-unknown")
    elif expected_catalog and catalog_fingerprint != expected_catalog:
        findings.append("catalog-fingerprint-mismatch")

    catalog_model_ids = ctx.get("catalogModelIds")
    if catalog_model_ids is not None and model_id and model_id not in set(catalog_model_ids):
        findings.append("catalog-model-absent")

    catalog_providers = ctx.get("catalogProviders")
    if isinstance(catalog_providers, dict) and model_id in catalog_providers:
        if _text(catalog_providers.get(model_id)) != _text(entry.get("provider")):
            findings.append("provider-mismatch")
    return findings


def _condition_failures(
    entry: Any, ctx: Mapping[str, Any], purposes: Sequence[str]
) -> list[str]:
    """Property 1의 조건 집합을 어긴 이유 코드 목록(빈 목록이면 조건 집합의 원소).

    production ``activation_gate``·``capability_map``·``contracts`` 판정기를 쓰지 않고
    requirements.md의 조건을 직접 다시 계산한다. 값 추론이 없으므로 model ID·provider·
    route 지원·effort 값 상수는 등장하지 않는다.
    """
    if not isinstance(entry, dict):
        return [COND_NOT_OBJECT]

    failures: list[str] = []

    stored = entry.get("capabilityFingerprint")
    try:
        recomputed = canonicalizer.capability_fingerprint(entry)
    except Exception:
        recomputed = None
    if not isinstance(stored, str) or recomputed is None or stored != recomputed:
        failures.append(COND_FINGERPRINT)

    if _malformed_findings(entry):
        failures.append(COND_MALFORMED)
    if _text(entry.get("verificationStatus")) != _VERIFIED:
        failures.append(COND_STATUS)
    if not _text(entry.get("modelId")):
        failures.append(COND_MODEL_ID)
    if not _text(entry.get("provider")):
        failures.append(COND_PROVIDER)
    if _incomplete_record_findings(entry):
        failures.append(COND_INCOMPLETE)
    if not _eligible_route_keys(entry, purposes):
        failures.append(COND_CONTRACT)
    if _current_evidence_findings(entry, ctx):
        failures.append(COND_EVIDENCE)
    if _text(entry.get("sourceKind")) == _SEED:
        failures.append(COND_SEED)
    return failures


def _condition_detail(entry: Any, ctx: Mapping[str, Any], purposes: Sequence[str]) -> dict:
    """조건 위반 진단 상세(실패 메시지용)."""
    if not isinstance(entry, dict):
        return {"entry": "not-object"}
    return {
        "malformed": _malformed_findings(entry)[:6],
        "incompleteRecord": _incomplete_record_findings(entry)[:6],
        "eligibleRoutes": _eligible_route_keys(entry, purposes),
        "currentEvidence": _current_evidence_findings(entry, ctx),
    }


# ─────────────────────────────────────────────────────────────────
# 입력 전략 — 상태·출처·구조를 섞은 임의 Capability_Map
# ─────────────────────────────────────────────────────────────────

#: 활성 목록에 절대 나타나면 안 되는 적대적 entry 종류.
#:   ``labelOnly``               라벨만 아는 초기 entry(전 필드 미확정 — 1.15, 6.26)
#:   ``labelOnlyClaimsVerified`` 라벨만 아는데 `VERIFIED`를 자칭(1.17, 6.2)
#:   ``statusFlip``              활성 가능 entry의 상태만 비틈(6.9~6.12)
#:   ``seedSource``              활성 가능 entry의 출처만 Seed로 바꿈(6.13)
#:   ``emptyProvider``           Provider_String 미확정(6.3)
#:   ``allowlistRejected``       `SUPPORTED` route의 allowlist가 `ALLOWED`가 아님(6.6)
#:   ``purposeMismatch``         계약이 요청 목적을 열거하지 않음(6.6)
#:   ``malformed``               Malformed mutation 주입(6.8)
FORBIDDEN_KINDS: tuple[str, ...] = (
    "labelOnly",
    "labelOnlyClaimsVerified",
    "statusFlip",
    "seedSource",
    "emptyProvider",
    "allowlistRejected",
    "purposeMismatch",
    "malformed",
)

#: ctx 종류. ``minimal``은 server seam과 동일한 `{}`(현재성 비교 생략), 나머지는 불일치 전수.
CONTEXT_MODES: tuple[str, ...] = (
    "aligned",
    "aligned",
    "minimal",
    "revisionMismatch",
    "catalogFingerprintMismatch",
    "catalogModelSubset",
)


def _align(entry: dict, revision: str, catalog_fingerprint: str) -> dict:
    """entry의 현재성 필드를 공유 값으로 맞춘다(Activation_Gate 통과 가능 상태 확보)."""
    aligned = copy.deepcopy(entry)
    aligned["revision"] = revision
    aligned["catalogFingerprint"] = catalog_fingerprint
    return _settle(aligned)


@st.composite
def label_only_entries(draw: Any, *, revision: str, claims_verified: bool) -> dict:
    """Exact_Model_ID가 발견되지 않은 Candidate_Label entry(미발견 라벨).

    `contracts.new_entry`가 만드는 전 필드 미확정 entry다. ``claims_verified``면
    `verificationStatus`만 `VERIFIED`로 비틀어(적대적) 라벨 문자열이 모델 항목으로
    노출되지 않는지 확인한다. `verificationStatus`는 fingerprint 입력이 아니므로
    저장된 fingerprint는 그대로 유효하다.
    """
    entry = contracts.new_entry(
        draw(S.candidate_labels()),
        source_kind=draw(S.source_kinds()),
        revision=revision,
        fingerprint_fn=canonicalizer.capability_fingerprint,
    )
    entry["displayName"] = draw(S.display_names())
    if claims_verified:
        entry["verificationStatus"] = _VERIFIED
        entry["verifiedAt"] = draw(S.utc_timestamps())
    return entry


@st.composite
def forbidden_variants(draw: Any, base: dict, *, kind: str, purposes: Sequence[str]) -> dict:
    """활성 가능 entry에서 파생한 적대적 변형(항상 유효 entry — 게이트 판정을 겨냥한다)."""
    entry = copy.deepcopy(base)

    if kind == "statusFlip":
        entry["verificationStatus"] = draw(S.verification_statuses(exclude=(_VERIFIED,)))
        return entry  # 상태는 fingerprint 입력이 아니다 → 재계산 불필요

    if kind == "seedSource":
        entry["sourceKind"] = _SEED
        return entry  # 출처도 fingerprint 입력이 아니다

    if kind == "emptyProvider":
        entry["provider"] = contracts.UNDETERMINED
        return _settle(entry)

    if kind == "allowlistRejected":
        for route_entry in (entry.get("routes") or {}).values():
            if isinstance(route_entry, dict) and route_entry.get("status") == _SUPPORTED_ROUTE:
                route_entry["allowlist"] = draw(S.allowlist_results(exclude=(_ALLOWED,)))
        return _settle(entry)

    if kind == "purposeMismatch":
        replacement = draw(
            S.purpose_lists(min_size=1, max_size=2).filter(
                lambda items: not set(purposes).issubset(set(items))
            )
        )
        for route_entry in (entry.get("routes") or {}).values():
            contract = route_entry.get("contract") if isinstance(route_entry, dict) else None
            if isinstance(contract, dict):
                contract["purposes"] = list(replacement)
        return _settle(entry)

    raise ValueError(f"알 수 없는 forbidden kind: {kind!r}")  # pragma: no cover


def _context_for(
    items: Sequence[dict],
    ready: Sequence[dict],
    *,
    mode: str,
    revision: str,
    catalog_fingerprint: str,
    other_revision: str,
    other_catalog_fingerprint: str,
    dropped_model_id: str,
    now: str,
) -> dict:
    """Activation_Gate ctx. catalog 쪽 값은 map identity에서 파생해 일관성을 유지한다.

    ``minimal``은 production server seam과 같은 `{}`이며, 나머지 mode는 현재성 불일치를
    하나씩 주입한다. `catalogProviders`는 활성 가능 entry를 먼저 등록해(그 다음 나머지)
    적대적 변형이 catalog 쪽 provider를 덮어쓰지 못하게 한다.
    """
    if mode == "minimal":
        return {}

    model_ids = sorted({_text(entry.get("modelId")) for entry in items if _text(entry.get("modelId"))})
    providers: dict[str, str] = {}
    for entry in list(ready) + list(items):
        model_id = _text(entry.get("modelId"))
        if model_id:
            providers.setdefault(model_id, _text(entry.get("provider")))

    if mode == "catalogModelSubset" and dropped_model_id:
        model_ids = [model_id for model_id in model_ids if model_id != dropped_model_id]

    return {
        "revision": other_revision if mode == "revisionMismatch" else revision,
        "catalogFingerprint": (
            other_catalog_fingerprint
            if mode == "catalogFingerprintMismatch"
            else catalog_fingerprint
        ),
        "catalogModelIds": model_ids,
        "catalogProviders": providers,
        "nowUtc": now,
    }


@st.composite
def baseline_catalogs(draw: Any, model_ids: Sequence[str]) -> dict:
    """Baseline_Catalog_Segment 자리(`{provider: [{id, name}, ...]}`).

    빈 카탈로그와 "Managed_Segment 후보 id가 이미 baseline에 있는" 경계값을 포함한다.
    provider·id는 전부 무작위 심볼이다.
    """
    providers = draw(st.lists(S.providers(allow_empty=False), max_size=2, unique=True))
    catalog: dict[str, list[dict]] = {}
    for provider in providers:
        ids = draw(st.lists(S.model_ids(allow_empty=False), max_size=2, unique=True))
        catalog[provider] = [{"id": model_id, "name": model_id} for model_id in ids]

    if model_ids and draw(st.booleans()):
        model_id = draw(st.sampled_from(sorted(set(model_ids))))
        provider = (
            draw(st.sampled_from(providers)) if providers else draw(S.providers(allow_empty=False))
        )
        catalog.setdefault(provider, []).append({"id": model_id, "name": model_id})
    return catalog


@st.composite
def activation_scenarios(draw: Any) -> dict:
    """Property 1 입력: 상태·출처·구조를 섞은 Capability_Map + ctx + baseline 카탈로그.

    반환 형태::

        {"purposes", "map", "ctx", "contextMode", "catalog", "forbidden"}

    ``forbidden``은 ``[{"kind", "entry"}]`` — 어떤 경우에도 Active_Model에 나타나면 안 되는
    entry 목록이다(R2 단정 입력). 빈 map 경계값에서는 비어 있다.
    """
    purposes = draw(S.purpose_lists(min_size=1, max_size=2))
    revision = draw(S.revisions(allow_empty=False))
    other_revision = draw(S.revisions(allow_empty=False).filter(lambda text: text != revision))
    catalog_fingerprint = draw(S.catalog_fingerprints(allow_empty=False))
    other_catalog_fingerprint = draw(
        S.catalog_fingerprints(allow_empty=False).filter(lambda text: text != catalog_fingerprint)
    )
    now = draw(S.utc_timestamps())

    items: list[dict] = []
    ready: list[dict] = []
    forbidden: list[dict] = []

    # 경계값: 빈 map(Managed_Segment 공집합 → 카탈로그 동일 객체 반환 경로).
    empty_map = draw(st.integers(min_value=0, max_value=7)) == 0
    if not empty_map:
        # (a) 활성 가능 후보 — 경계값 "단일 entry"를 포함한다.
        for _ in range(draw(st.integers(min_value=1, max_value=2))):
            ready.append(
                _align(
                    draw(S.entries(active_ready=True, purposes=purposes)),
                    revision,
                    catalog_fingerprint,
                )
            )
        items.extend(ready)

        # (b) 임의 상태·출처 entry(상태 enum 전수, 빈 문자열 ID, Seed, 미완성 계약 등).
        for _ in range(draw(st.integers(min_value=0, max_value=2))):
            other = draw(S.entries(purposes=purposes))
            items.append(
                _align(other, revision, catalog_fingerprint) if draw(st.booleans()) else other
            )

        # (c) 적대적 변형 — 활성 가능 entry에서 조건을 하나씩 깬다.
        base = draw(st.sampled_from(ready))
        for kind in draw(
            st.lists(st.sampled_from(FORBIDDEN_KINDS), min_size=1, max_size=3, unique=True)
        ):
            if kind == "labelOnly" or kind == "labelOnlyClaimsVerified":
                entry = draw(
                    label_only_entries(
                        revision=revision,
                        claims_verified=(kind == "labelOnlyClaimsVerified"),
                    )
                )
            elif kind == "malformed":
                entry = draw(S.malformed_entries(base=st.just(base)))["mutated"]
            else:
                entry = draw(forbidden_variants(base, kind=kind, purposes=purposes))
            items.append(entry)
            forbidden.append({"kind": kind, "entry": entry})

        # (d) 경계값: 동일 modelId duplicate·동시각 tie·fingerprint 불일치·최신 verifiedAt 형제.
        #     활성 가능 entry를 형제 base로 더 자주 뽑아 노출 축약 규칙까지 exercise한다.
        if draw(st.booleans()):
            sibling_base = draw(st.sampled_from(list(ready) + items))
            kind, sibling = draw(S.sibling_entries(sibling_base))
            items.append(sibling if kind == "duplicate" else _settle(sibling))

    map_obj = S.new_capability_map(items, updated_at=now)
    model_ids = sorted({_text(entry.get("modelId")) for entry in items if _text(entry.get("modelId"))})
    mode = draw(st.sampled_from(CONTEXT_MODES))
    ctx = _context_for(
        items,
        ready,
        mode=mode,
        revision=revision,
        catalog_fingerprint=catalog_fingerprint,
        other_revision=other_revision,
        other_catalog_fingerprint=other_catalog_fingerprint,
        dropped_model_id=draw(st.sampled_from(model_ids)) if model_ids else contracts.UNDETERMINED,
        now=now,
    )

    return {
        "purposes": purposes,
        "map": map_obj,
        "ctx": ctx,
        "contextMode": mode,
        "catalog": draw(baseline_catalogs(model_ids)),
        "forbidden": forbidden,
    }


# ─────────────────────────────────────────────────────────────────
# 단정 본문 (단일 property test에서만 호출한다)
# ─────────────────────────────────────────────────────────────────
def _assert_subset(
    active: Sequence[dict],
    map_obj: Mapping[str, Any],
    ctx: Mapping[str, Any],
    purposes: Sequence[str],
) -> None:
    """R1: Active_Model은 조건 집합의 부분집합이고 입력 entry에서만 나온다."""
    known = {
        serialized
        for serialized in (_serialized(entry) for entry in capability_map.entries_of(map_obj))
        if serialized is not None
    }

    model_ids = [_text(entry.get("modelId")) for entry in active]
    assert all(model_ids), (
        f"Active_Model에 비어 있는 Exact_Model_ID가 있다 — Property {PROPERTY_ID}: {model_ids!r}"
    )
    assert len(model_ids) == len(set(model_ids)), (
        f"같은 Exact_Model_ID가 중복 노출됐다 — Property {PROPERTY_ID}: {model_ids!r}"
    )

    for entry in active:
        failures = _condition_failures(entry, ctx, purposes)
        assert not failures, (
            f"Active_Model이 조건 집합을 벗어났다 — Property {PROPERTY_ID}: "
            f"failures={failures!r} detail={_condition_detail(entry, ctx, purposes)!r} "
            f"entry={_brief(entry)}"
        )
        assert all(code in CONDITION_CODES for code in failures)  # 닫힌 조건 코드 유지

        serialized = _serialized(entry)
        assert serialized is not None and serialized in known, (
            f"입력 map에 없던 entry가 노출됐다 — Property {PROPERTY_ID}: {_brief(entry)}"
        )

        ok, reason = activation_gate.is_active(entry, ctx, purposes=purposes)
        assert ok and reason == activation_gate.REASON_OK, (
            f"노출된 entry가 is_active를 통과하지 못한다 — Property {PROPERTY_ID}: "
            f"reason={reason} entry={_brief(entry)}"
        )


def _assert_forbidden_excluded(
    active: Sequence[dict], forbidden: Iterable[Mapping[str, Any]]
) -> None:
    """R2: 검증되지 않은 entry는 어떤 경우에도 Active_Model에 나타나지 않는다."""
    exposed = {
        serialized
        for serialized in (_serialized(entry) for entry in active)
        if serialized is not None
    }
    for case in forbidden:
        serialized = _serialized(case.get("entry"))
        assert serialized is None or serialized not in exposed, (
            f"활성 후보가 아닌 entry가 노출됐다 — Property {PROPERTY_ID}: "
            f"kind={case.get('kind')!r} entry={_brief(case.get('entry'))}"
        )

    for entry in active:
        assert _text(entry.get("verificationStatus")) == _VERIFIED, (
            f"`VERIFIED`가 아닌 entry가 노출됐다 — Property {PROPERTY_ID}: {_brief(entry)}"
        )
        assert _text(entry.get("sourceKind")) != _SEED, (
            f"Seed_Entry가 노출됐다 — Property {PROPERTY_ID}: {_brief(entry)}"
        )


def _assert_ui_payload(active: Sequence[dict], model_ids: Sequence[str]) -> dict:
    """R3 전반: UI payload 노출 집합이 Active_Model 집합과 정확히 같다."""
    payload = capability_map.to_ui_payload(active)

    assert sorted(payload) == sorted(capability_map.UI_PAYLOAD_KEYS), (
        f"UI payload 최상위 키가 다르다 — Property {PROPERTY_ID}: {sorted(payload)!r}"
    )
    assert payload["modelIds"] == list(model_ids), (
        f"UI payload 노출 집합이 Active_Model과 다르다 — Property {PROPERTY_ID}: "
        f"payload={payload['modelIds']!r} active={list(model_ids)!r}"
    )
    assert set(payload["models"]) == set(model_ids), (
        f"UI payload models 키가 Active_Model과 다르다 — Property {PROPERTY_ID}: "
        f"{sorted(payload['models'])!r} != {sorted(set(model_ids))!r}"
    )

    for entry in active:
        model_id = _text(entry.get("modelId"))
        view = payload["models"][model_id]
        assert view["modelId"] == model_id and view["provider"] == _text(entry.get("provider")), (
            f"UI payload identity가 entry와 다르다 — Property {PROPERTY_ID}: {_brief(view)}"
        )
        assert view["capabilityFingerprint"] == _text(entry.get("capabilityFingerprint")), (
            f"UI payload fingerprint가 entry와 다르다 — Property {PROPERTY_ID}: {_brief(view)}"
        )
        assert sorted(view["routes"]) == sorted(S.KNOWN_ROUTES), (
            f"UI payload route 키가 Known_Route 전수가 아니다 — Property {PROPERTY_ID}"
        )
        assert sorted(view["effort"]) == sorted(S.KNOWN_ROUTES), (
            f"UI payload effort 키가 Known_Route 전수가 아니다 — Property {PROPERTY_ID}"
        )
    return payload


def _assert_catalog_merge(
    catalog: Mapping[str, Any], active: Sequence[dict], model_ids: Sequence[str]
) -> dict[str, list[dict]]:
    """R3 후반: 카탈로그 병합 결과가 baseline + Active_Model뿐임을 확인한다.

    반환값은 provider별 **추가된** 항목 목록(라벨 유출 검사 입력).
    """
    before = canonicalizer.serialize(catalog)
    merged = capability_map.merge_active_into_catalog(catalog, list(active))
    assert canonicalizer.serialize(catalog) == before, (
        f"병합이 baseline 카탈로그를 변경했다 — Property {PROPERTY_ID}"
    )

    baseline_ids = {
        item["id"]
        for items in catalog.values()
        for item in items
        if isinstance(item, dict) and isinstance(item.get("id"), str)
    }

    if not active:
        assert merged is catalog, (
            f"Managed_Segment가 비었는데 카탈로그가 새 객체로 바뀌었다 — Property {PROPERTY_ID}"
        )
        return {}

    assert set(catalog).issubset(set(merged)), (
        f"병합이 baseline provider 그룹을 제거했다 — Property {PROPERTY_ID}: "
        f"{sorted(set(catalog) - set(merged))!r}"
    )

    additions: dict[str, list[dict]] = {}
    for provider, merged_items in merged.items():
        base_items = catalog.get(provider) or []
        assert isinstance(merged_items, list), (
            f"병합 결과 provider 그룹이 목록이 아니다 — Property {PROPERTY_ID}: {provider!r}"
        )
        assert merged_items[: len(base_items)] == list(base_items), (
            f"baseline 항목이 보존되지 않았다 — Property {PROPERTY_ID}: provider={provider!r}"
        )
        extra = merged_items[len(base_items) :]
        if extra:
            additions[provider] = extra

    provider_of = {
        _text(entry.get("modelId")): _text(entry.get("provider"))
        for entry in active
        if _text(entry.get("modelId"))
    }
    added_ids: list[str] = []
    for provider, extra in additions.items():
        for item in extra:
            assert isinstance(item, dict) and sorted(item) == sorted(capability_map.CATALOG_ITEM_FIELDS), (
                f"추가 항목의 필드가 baseline 형태와 다르다 — Property {PROPERTY_ID}: {item!r}"
            )
            model_id = item["id"]
            assert item["name"] == model_id, (
                f"추가 항목의 name이 Exact_Model_ID가 아니다 — Property {PROPERTY_ID}: {item!r}"
            )
            assert model_id in set(model_ids), (
                f"Active_Model이 아닌 항목이 카탈로그에 추가됐다 — Property {PROPERTY_ID}: "
                f"{item!r}"
            )
            assert provider_of[model_id] == provider, (
                f"추가 항목이 다른 provider 그룹에 들어갔다 — Property {PROPERTY_ID}: "
                f"id={model_id!r} provider={provider!r} expected={provider_of[model_id]!r}"
            )
            added_ids.append(model_id)

    assert len(added_ids) == len(set(added_ids)), (
        f"같은 Exact_Model_ID가 중복 추가됐다 — Property {PROPERTY_ID}: {added_ids!r}"
    )
    assert set(added_ids) == set(model_ids) - baseline_ids, (
        f"노출 집합이 Active_Model 집합과 다르다 — Property {PROPERTY_ID}: "
        f"added={sorted(set(added_ids))!r} expected={sorted(set(model_ids) - baseline_ids)!r}"
    )
    return additions


def _assert_no_label_exposure(
    map_obj: Mapping[str, Any],
    additions: Mapping[str, list[dict]],
    payload: Mapping[str, Any],
) -> None:
    """R4: Candidate_Label 문자열과 표시명은 어떤 경우에도 모델 항목으로 노출되지 않는다.

    구조 단정(라벨·표시명 키 부재, 추가 항목의 `id`/`name`이 Exact_Model_ID)과 문자열 단정을
    함께 적용한다. 문자열 단정 대상은 map의 다른 필드에 나타나지 않는 라벨로 한정한다 —
    무작위 심볼 라벨이 우연히 다른 노출 가능 문자열(요청 목적 등)과 같아지는 경우는 노출
    여부를 구분할 수 없기 때문이다.
    """
    entries = capability_map.entries_of(map_obj)

    exposed_keys = _dict_keys_in(payload) | _dict_keys_in(dict(additions))
    for field in LABEL_FIELDS:
        assert field not in exposed_keys, (
            f"노출 구조에 {field} 키가 있다 — Property {PROPERTY_ID}: {sorted(exposed_keys)!r}"
        )

    labels: set[str] = set()
    undiscovered: set[str] = set()
    others: set[str] = set()
    for entry in entries:
        if not isinstance(entry, dict):
            continue
        label = entry.get("candidateLabel")
        if isinstance(label, str) and label:
            labels.add(label)
            if not _text(entry.get("modelId")):
                undiscovered.add(label)  # Exact_Model_ID 미발견 라벨
        display_name = entry.get("displayName")
        if isinstance(display_name, str) and display_name:
            labels.add(display_name)
        rest = {key: value for key, value in entry.items() if key not in LABEL_FIELDS}
        others |= _strings_in(rest)

    exposed_strings = _strings_in(dict(additions)) | _strings_in(payload)
    leaked = (labels - others) & exposed_strings
    assert not leaked, (
        f"Candidate_Label 문자열이 노출됐다 — Property {PROPERTY_ID}: {sorted(leaked)!r}"
    )

    # Exact_Model_ID가 없는 entry(미발견 라벨)는 어떤 노출 항목도 만들지 않는다(6.26).
    added_ids = {
        item["id"]
        for items in additions.values()
        for item in items
        if isinstance(item, dict) and isinstance(item.get("id"), str)
    }
    exposed_model_ids = set(payload.get("models") or {}) | added_ids
    surfaced = (undiscovered - others) & exposed_model_ids
    assert not surfaced, (
        f"미발견 라벨이 모델 항목으로 노출됐다 — Property {PROPERTY_ID}: {sorted(surfaced)!r}"
    )


def _assert_exclusion_report(
    report: Mapping[str, Any], map_obj: Mapping[str, Any], active: Sequence[dict]
) -> None:
    """R5: 노출되지 않은 모든 entry에 닫힌 집합의 이유 코드 record가 정확히 하나 있다."""
    entries = capability_map.entries_of(map_obj)
    excluded = report["excluded"]

    assert report["counts"]["entries"] == len(entries)
    assert report["counts"]["active"] == len(active)
    assert len(entries) == len(active) + len(excluded), (
        f"노출/탈락 record 수가 entry 수와 다르다 — Property {PROPERTY_ID}: "
        f"entries={len(entries)} active={len(active)} excluded={len(excluded)}"
    )

    allowed_fields = set(activation_gate.REPORT_ENTRY_FIELDS) | {"reason"}
    for record in excluded:
        assert set(record) == allowed_fields, (
            f"탈락 record 필드가 화이트리스트와 다르다 — Property {PROPERTY_ID}: "
            f"{sorted(record)!r}"
        )
        assert record["reason"] in activation_gate.DEACTIVATION_REASONS, (
            f"닫힌 집합 밖의 탈락 이유 코드 — Property {PROPERTY_ID}: {record['reason']!r}"
        )


def _assert_activation_subset(scenario: Mapping[str, Any]) -> None:
    """Property 1 단정 전체.

    단정 순서는 **진단이 좁은 것부터**다: 부분집합 → 적대적 클래스 → UI payload → 카탈로그
    병합 → 라벨 미노출 → 탈락 이유. 앞 단계가 깨지면 뒤 단계의 실패 원인을 알 수 없다.
    """
    map_obj = scenario["map"]
    ctx: Mapping[str, Any] = scenario["ctx"]
    purposes: Sequence[str] = scenario["purposes"]

    before = canonicalizer.serialize(map_obj)
    report = activation_gate.activation_report(map_obj, ctx, purposes=purposes)
    active = activation_gate.active_models(map_obj, ctx, purposes=purposes)
    model_ids = activation_gate.active_model_ids(map_obj, ctx, purposes=purposes)

    assert canonicalizer.serialize(map_obj) == before, (
        f"판정이 입력 Capability_Map을 변경했다 — Property {PROPERTY_ID}"
    )
    assert canonicalizer.serialize(report["active"]) == canonicalizer.serialize(active), (
        f"activation_report와 active_models 결과가 다르다 — Property {PROPERTY_ID}"
    )
    assert model_ids == [_text(entry.get("modelId")) for entry in active], (
        f"active_model_ids가 active_models와 다르다 — Property {PROPERTY_ID}: {model_ids!r}"
    )

    _assert_subset(active, map_obj, ctx, purposes)
    _assert_forbidden_excluded(active, scenario["forbidden"])
    payload = _assert_ui_payload(active, model_ids)
    additions = _assert_catalog_merge(scenario["catalog"], active, model_ids)
    _assert_no_label_exposure(map_obj, additions, payload)
    _assert_exclusion_report(report, map_obj, active)


# ─────────────────────────────────────────────────────────────────
# Property 1 — 정확히 하나의 property test
# ─────────────────────────────────────────────────────────────────
@seed(S.AE_PBT_SEED)
@S.PBT_SETTINGS
@given(activation_scenarios())
def test_property1_activation_subset(scenario: dict) -> None:
    """Property 1: Activation subset.

    모든 Verification_Status·Malformed_Entry·Seed_Entry·mock 출처 evidence·미발견
    Candidate_Label을 섞은 임의 Capability_Map에서 Active_Model 집합은 조건 집합의
    부분집합이고, Managed_Segment 노출 집합은 Active_Model 집합과 정확히 같으며,
    Candidate_Label 문자열은 어떤 경우에도 모델 항목으로 노출되지 않는다.

    **Validates: Requirements 1.15, 1.17, 6.1, 6.2, 6.3, 6.4, 6.5, 6.6, 6.7, 6.9, 6.10,
    6.11, 6.12, 6.13, 6.14, 6.26, 12.9, 12.22**
    """
    try:
        _assert_activation_subset(scenario)
    except AssertionError as exc:
        record_counterexample(
            str(exc),
            {
                "capabilityMap": scenario["map"],
                "context": scenario["ctx"],
                "contextMode": scenario["contextMode"],
                "purposes": scenario["purposes"],
                "baselineCatalog": scenario["catalog"],
                "forbidden": scenario["forbidden"],
            },
        )
        raise


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-p", "no:cacheprovider", "-q"]))
