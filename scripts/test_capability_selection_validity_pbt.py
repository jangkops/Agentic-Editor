# Feature: gateway-models-effort-support, Property 8: selection validity
# *For any* catalog 또는 capability 변경 시퀀스에 대해, 변경 후 model 선택 결과는
# Active_Model 중 하나이거나 명시적 미선택 상태이며, 선택된 tuple과 정확히 일치하지 않는
# Effort_Settings(modelId 불일치, route 불일치, Capability_Fingerprint 불일치, verified
# domain 이탈, entry 삭제, entry `STALE`, route의 `SUPPORTED` 상실)는 request 생성 전에
# 제거된다. 복원은 새 tuple의 세 요소가 모두 일치할 때만 일어난다.
"""Property 8 (selection validity) property test — task 8.2.

검증 대상 모듈:
  ``ai_engine/capability/effort_settings.py``
    - ``prune``            request 생성 전 어긋난 저장 value 제거
    - ``restore``          tuple 3요소 전부 일치 시에만 복원
    - ``active_selection`` request에 실을 effort selection(없으면 명시적 미선택)
  ``ai_engine/capability/activation_gate.py``
    - ``active_models``    선택 가능한 Active_Model 집합
    - ``eligible_route_keys`` 선택 tuple의 route 성분(Fallback_Order 첫 Eligible_Contract)

이 테스트는 **변경 시퀀스**를 구동한다. 초기 Capability_Map·Effort_Settings에서 시작해
catalog·capability 변경(catalog에서 model 제거, provider 변경, Route_Contract 변경,
Effort_Contract 변경, revision bump, `STALE` 전이, entry 삭제, route의 `SUPPORTED` 상실,
effort `SUPPORTED` 상실, Capability_Fingerprint 회전)을 순서대로 적용하고, **모든 중간
단계**에서 아래 단정을 확인한다.

단정 요약 (design.md Correctness Properties → Property 8):
  R1. **선택 유효성** — 각 단계의 선택 결과는 `active_models`가 산출한 Active_Model 중
      하나이거나 ``None``(명시적 미선택)이다. 노출된 Active_Model은 비어 있지 않은
      Exact_Model_ID를 갖고 Exact_Model_ID당 1개이며, 선택 tuple의 route는 그 entry에서
      실제로 `SUPPORTED` + allowlist `ALLOWED`이고 fingerprint는 재계산값과 같다.
      원하는 모델이 Active_Model이 아니면 선택은 반드시 ``None``이다(7.11~7.13).
  R2. **불일치 Effort_Settings 제거** — ``prune``이 남긴 항목의 tuple 집합은 독립적으로
      계산한 "살아남아야 하는" tuple 집합과 정확히 같다. 제거 판정 근거는 modelId·route·
      Capability_Fingerprint 불일치, verified domain 이탈, entry 삭제, entry `STALE`,
      route의 `SUPPORTED` 상실이며(7.7~7.13) 같은 tuple 중복은 1개로 축약된다(최신
      `updatedAt`). ``prune``은 입력을 변경하지 않고 재적용에도 결과가 같다.
  R3. **복원은 tuple 3요소 전부 일치 시에만** — ``restore``의 반환 유무는 "요청 tuple과
      `(modelId, route, capabilityFingerprint)`가 완전히 같은 저장 항목이 있는지"와 정확히
      일치하고, 돌려준 항목의 tuple은 요청 tuple과 같다. 성분이 하나라도 다른 요청에는
      선택 tuple의 항목이 복원되지 않으며, 비어 있는 성분이 있는 tuple로는 어떤 값도
      복원되지 않는다(7.14).
  R4. **request 진입 지점** — ``active_selection``은 선택 tuple과 정확히 일치하고 모든
      상태 조건을 만족하는 항목이 있을 때만 값을 돌려주고(그 값은 verified domain 안),
      선택이 미선택이면 항상 ``None``이다. 즉 어긋난 effort는 request 생성 전에 사라진다.

"살아남아야 하는" 집합은 production ``prune``과 **독립적으로** 계산한다(entry 상태·route
상태·effort 상태를 map 원본에서 직접 읽고, domain 멤버십은 생성기 쪽 구현
:func:`_capability_strategies.in_domain`을 쓴다). 다만 같은 modelId entry가 복수일 때의
활성 evidence 선택 규칙(최신 `verifiedAt`)은 Property 7·Capability_Map의 책임이므로
``effort_settings.capability_index``를 그대로 재사용한다.

경계값(design.md "PBT 구성 규칙")은 입력 전략에서 명시적으로 포함한다: 빈 Effort_Settings,
단일 entry map, 동일 modelId duplicate·동시각 tie·fingerprint 불일치 형제 entry, enum
단일값·range 상·하한 동일 domain, 상태 enum 전수, 비어 있는 tuple 성분.

model ID·provider·route 지원 여부·effort field path·effort 허용값은 확정 상수 없이 무작위
심볼로만 생성한다(`scripts/_capability_strategies.py`). 이 테스트는 순수 로직만 구동하며
Gateway·네트워크에 접근하지 않고, 결과는 Gateway 지원 근거가 아니다(Requirement 12.22).

실패 시 최소화된 counterexample과 재현 정보를
``.generated/pbt/capability_selection_validity.json`` 에 기록한다(Validation_Runner 보고서
``pbt.counterexamples`` 입력, 작업 12.2). `print_blob=True` 로 재현 blob도 함께 출력된다.

실행:
  ai_engine/.venv/bin/python -m pytest scripts/test_capability_selection_validity_pbt.py -q

**Validates: Requirements 7.7, 7.8, 7.9, 7.10, 7.11, 7.12, 7.13, 7.14, 12.16**
_Requirements: 7.11, 7.12, 7.13, 7.14, 12.16, 12.19, 12.21_
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
from ai_engine.capability import (  # noqa: E402
    activation_gate,
    canonicalizer,
    capability_map,
    contracts,
    effort_settings,
)

# ─────────────────────────────────────────────────────────────────
# property 식별 (보고서 기록용)
# ─────────────────────────────────────────────────────────────────
FEATURE = "gateway-models-effort-support"
PROPERTY_ID = 8
PROPERTY_LABEL = "selection validity"

_SUPPORTED_ROUTE = str(contracts.Route_Support_Status.SUPPORTED)
_SUPPORTED_EFFORT = str(contracts.Effort_Support_Status.SUPPORTED)
_ALLOWED = str(contracts.Allowlist_Result.ALLOWED)
_VERIFIED = str(contracts.Verification_Status.VERIFIED)
_STALE = str(contracts.Verification_Status.STALE)


# ─────────────────────────────────────────────────────────────────
# counterexample 기록 (Requirement 12.21)
# ─────────────────────────────────────────────────────────────────

#: 기록 디렉터리. `.generated/` 는 gitignore 대상이며 env로 재지정할 수 있다.
COUNTEREXAMPLE_DIR = os.environ.get("AE_PBT_COUNTEREXAMPLE_DIR") or os.path.join(
    _ROOT, ".generated", "pbt"
)

#: 기록 파일 경로(property 1개당 1파일).
COUNTEREXAMPLE_PATH = os.path.join(COUNTEREXAMPLE_DIR, "capability_selection_validity.json")

#: 재현 명령(보고서에 그대로 실을 수 있는 형태).
REPRODUCE_COMMAND = (
    f"AE_PBT_SEED={S.AE_PBT_SEED} ai_engine/.venv/bin/python -m pytest "
    "scripts/test_capability_selection_validity_pbt.py -q"
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


def _moment(value: Any) -> float:
    """`updatedAt` 비교용 epoch 초(형식 위반은 음의 무한대에 가까운 값)."""
    if not contracts.is_utc_iso8601(value):
        return float("-inf")
    try:
        return datetime.fromisoformat(value).timestamp()
    except ValueError:  # pragma: no cover - is_utc_iso8601이 선차단한다
        return float("-inf")


def _settle(entry: dict) -> dict:
    """파생 모드 상태를 routes에서 다시 유도하고 Capability_Fingerprint를 재계산한다.

    Complete_Record는 `syncSupport`/`asyncSupport`/`streamingSupport`가 routes에서 유도한
    값과 같기를 요구한다(Requirement 3.20~3.23). 파생 상태는 fingerprint 입력이 아니므로
    재계산 순서와 무관하게 저장 fingerprint는 항상 재계산값과 일치한다(Malformed 아님).
    """
    settled = capability_map.apply_mode_support(copy.deepcopy(entry))
    return S.refresh_fingerprint(settled)


def _tuple_of(selection: Mapping[str, Any] | None) -> tuple[str, str, str] | None:
    """선택 tuple을 `(modelId, route, capabilityFingerprint)` 3-tuple로 바꾼다."""
    if not isinstance(selection, Mapping):
        return None
    return (
        _text(selection.get("modelId")),
        _text(selection.get("route")),
        _text(selection.get("capabilityFingerprint")),
    )


# ─────────────────────────────────────────────────────────────────
# 독립 판정 — production prune을 쓰지 않고 조건을 직접 계산한다
# ─────────────────────────────────────────────────────────────────
def _effort_contract_complete(contract: Any) -> bool:
    """Effort_Contract 완전성(field path·value type·완전 domain)을 독립적으로 판정한다.

    design.md Data Models의 요구와 동일하다: `modelId`·`routeKey`가 채워지고 `fieldPath`가
    비어 있지 않은 문자열 목록이며, `valueType`·`domainKind`가 닫힌 집합이고, `ENUM`은
    비어 있지 않은 `enumValues`, `RANGE`는 두 inclusive 경계가 수치여야 한다.
    """
    if not isinstance(contract, dict):
        return False
    if not _text(contract.get("modelId")):
        return False
    if contract.get("routeKey") not in S.KNOWN_ROUTES:
        return False
    path = contract.get("fieldPath")
    if not isinstance(path, list) or not path:
        return False
    if not all(isinstance(part, str) for part in path):
        return False
    if contract.get("valueType") not in S.VALUE_TYPES:
        return False
    kind = contract.get("domainKind")
    if kind == contracts.Domain_Kind.ENUM:
        values = contract.get("enumValues")
        return isinstance(values, list) and bool(values)
    if kind == contracts.Domain_Kind.RANGE:
        return all(
            isinstance(bound, (int, float)) and not isinstance(bound, bool)
            for bound in (contract.get("rangeLowerInclusive"), contract.get("rangeUpperInclusive"))
        )
    return False


def _keep_conditions(
    setting: Mapping[str, Any],
    entry: Any,
) -> list[str]:
    """저장 항목이 capability 상태와 어긋난 이유(빈 목록이면 상태 조건을 모두 만족).

    이유 문자열은 requirements.md의 granular criteria와 1:1로 대응한다. production
    ``effort_settings``의 판정 함수를 쓰지 않고 map 원본에서 직접 읽는다.
    """
    route = _text(setting.get("route"))
    reasons: list[str] = []

    if entry is None:
        return ["entry-absent(7.11)"]  # 선택 model entry 삭제

    status = _text(entry.get("verificationStatus"))
    if status == _STALE:
        reasons.append("entry-stale(7.12)")
    elif status != _VERIFIED:
        reasons.append("entry-not-verified(7.12)")

    entry_fingerprint = _text(entry.get("capabilityFingerprint"))
    if entry_fingerprint and entry_fingerprint != _text(setting.get("capabilityFingerprint")):
        reasons.append("fingerprint-mismatch(7.9)")

    routes = entry.get("routes")
    route_entry = routes.get(route) if isinstance(routes, dict) else None
    if not (isinstance(route_entry, dict) and route_entry.get("status") == _SUPPORTED_ROUTE):
        reasons.append("route-not-supported(7.13)")

    effort_container = entry.get("effort")
    effort_entry = effort_container.get(route) if isinstance(effort_container, dict) else None
    if not (isinstance(effort_entry, dict) and effort_entry.get("status") == _SUPPORTED_EFFORT):
        reasons.append("effort-not-supported(7.10)")
        return reasons

    contract = effort_entry.get("contract")
    if not _effort_contract_complete(contract):
        reasons.append("effort-contract-incomplete(7.10)")
        return reasons
    if _text(contract.get("modelId")) != _text(setting.get("modelId")) or _text(
        contract.get("routeKey")
    ) != route:
        reasons.append("effort-contract-misbound(7.10)")
        return reasons
    if _text(setting.get("valueType")) != _text(contract.get("valueType")):
        reasons.append("value-type-mismatch(7.10)")
    if not S.in_domain(contract, setting.get("value")):
        reasons.append("domain-violation(7.10)")
    return reasons


def _expected_surviving(
    settings: Any,
    index: Mapping[str, Any],
    selection: Mapping[str, Any] | None,
) -> dict[tuple[str, str, str], list[dict]]:
    """살아남아야 하는 항목을 tuple별로 모은다(production ``prune`` 미사용).

    선택 tuple이 주어지면 세 성분이 모두 일치하는 항목만 남는다(7.7~7.9). 선택이
    미제공이면(명시적 미선택) tuple 비교를 생략하고 상태 조건만 적용한다 —
    "모른다"를 이유로 값을 지우지 않는다는 모듈 계약과 같다.
    """
    wanted = _tuple_of(selection)
    surviving: dict[tuple[str, str, str], list[dict]] = {}
    for setting in effort_settings.entries_of(settings):
        if effort_settings.is_malformed_entry(setting):
            continue  # 스키마 위반은 언제나 제거 대상(복원·전송 금지)
        key = effort_settings.key_of(setting)
        if wanted is not None and key != wanted:
            continue
        if _keep_conditions(setting, index.get(key[0])):
            continue
        surviving.setdefault(key, []).append(setting)
    return surviving


def _prune_context(map_obj: dict, selection: Mapping[str, Any] | None) -> dict:
    """``prune``/``active_selection``에 넘기는 판정 컨텍스트.

    `currentFingerprints`는 capability 색인에서 파생하므로 색인 경로와 항상 일관된다.
    """
    index = effort_settings.capability_index({"capabilityMap": map_obj}) or {}
    ctx: dict[str, Any] = {
        "capabilityMap": map_obj,
        "currentFingerprints": {
            model_id: _text(entry.get("capabilityFingerprint"))
            for model_id, entry in index.items()
        },
    }
    if selection is not None:
        ctx["selection"] = dict(selection)
    return ctx


# ─────────────────────────────────────────────────────────────────
# 선택 해석 — production Activation_Gate만 입력으로 쓴다
# ─────────────────────────────────────────────────────────────────
def _resolve_selection(
    map_obj: dict,
    ctx: Mapping[str, Any],
    purposes: Sequence[str],
    desired_model_id: str,
) -> tuple[list[dict], dict | None]:
    """(Active_Model 목록, 선택 tuple 또는 ``None``)을 반환한다.

    Model_Selection_Manager 규칙: 원하는 모델이 Active_Model이면 유지하고, 아니면 명시적
    미선택(``None``)이다. route 성분은 Fallback_Order 첫 Eligible_Contract에서 얻는다.
    """
    active = activation_gate.active_models(map_obj, ctx, purposes=purposes)
    for entry in active:
        if _text(entry.get("modelId")) != desired_model_id or not desired_model_id:
            continue
        routes = activation_gate.eligible_route_keys(entry, ctx=ctx, purposes=purposes)
        if not routes:
            return active, None
        return active, {
            "modelId": _text(entry.get("modelId")),
            "route": routes[0],
            "capabilityFingerprint": _text(entry.get("capabilityFingerprint")),
        }
    return active, None


# ─────────────────────────────────────────────────────────────────
# catalog·capability 변경 시퀀스
# ─────────────────────────────────────────────────────────────────

#: 변경 종류. 각 항목은 Requirement 6.15~6.19(STALE 전이)와 7.11~7.13(설정 정리)의 트리거다.
CHANGE_KINDS: tuple[str, ...] = (
    "noop",
    "catalogRemoveModel",   # 6.15 → 7.12
    "providerChange",       # 6.16 → 7.12
    "routeContractChange",  # 6.17 → 7.12
    "effortContractChange", # 6.18 → 7.12
    "revisionBump",         # 6.19 → 7.12
    "markStale",            # 7.12
    "dropEntry",            # 7.11
    "routeUnsupported",     # 7.13
    "effortUnsupported",    # 7.10
    "fingerprintRotate",    # 7.9
)


@st.composite
def change_specs(draw: Any, model_pool: Sequence[str]) -> dict:
    """변경 명세(순수 데이터 — 보고서에 그대로 실을 수 있다)."""
    kind = draw(st.sampled_from(CHANGE_KINDS))
    if kind == "noop" or (not model_pool and kind != "revisionBump"):
        return {"kind": "noop"}
    if kind == "revisionBump":
        return {"kind": kind, "revision": draw(S.revisions(allow_empty=False))}

    model_id = draw(st.sampled_from(list(model_pool)))
    if kind == "providerChange":
        return {"kind": kind, "modelId": model_id, "provider": draw(S.providers(allow_empty=False))}
    if kind == "routeContractChange":
        # catalog·baseline이 알려주는 현재 Route_Contract가 달라진 경우(6.17).
        route = draw(S.known_routes())
        return {
            "kind": kind,
            "modelId": model_id,
            "route": route,
            "contract": draw(S.route_contracts(route_key=route)),
        }
    if kind == "effortContractChange":
        # catalog가 선언한 현재 Effort_Contract가 달라진 경우(6.18).
        route = draw(S.known_routes())
        return {
            "kind": kind,
            "modelId": model_id,
            "route": route,
            "contract": draw(S.effort_contracts(model_id=model_id, route_key=route)),
        }
    if kind == "routeUnsupported":
        return {
            "kind": kind,
            "modelId": model_id,
            "route": draw(S.known_routes()),
            "status": draw(S.route_support_statuses(exclude=(_SUPPORTED_ROUTE,))),
        }
    if kind == "effortUnsupported":
        return {
            "kind": kind,
            "modelId": model_id,
            "route": draw(S.known_routes()),
            "status": draw(S.effort_support_statuses(exclude=(_SUPPORTED_EFFORT,))),
        }
    if kind == "fingerprintRotate":
        return {
            "kind": kind,
            "modelId": model_id,
            "route": draw(S.known_routes()),
            "allowlist": draw(S.allowlist_results()),
        }
    return {"kind": kind, "modelId": model_id}


def apply_change(map_obj: dict, ctx: Mapping[str, Any], spec: Mapping[str, Any]) -> tuple[dict, dict]:
    """변경 명세를 적용한 `(새 map, 새 ctx)`를 반환한다(입력은 변경하지 않는다).

    catalog 쪽 변경(model 제거·provider 변경·Route_Contract 변경·Effort_Contract 변경·
    revision bump)은 ctx를 바꾼 뒤 production ``capability_map.apply_stale_triggers``로
    STALE 전이를 유도한다. capability 쪽 변경은 entry를 직접 수정하고
    Capability_Fingerprint를 재계산한다(저장 fingerprint는 항상 재계산값과 일치하므로
    entry는 Malformed가 되지 않는다).
    """
    kind = spec.get("kind")
    new_ctx = copy.deepcopy(dict(ctx))
    entries = [copy.deepcopy(entry) for entry in capability_map.entries_of(map_obj)]
    updated_at = _text(map_obj.get("updatedAt"))
    model_id = _text(spec.get("modelId"))

    if kind == "catalogRemoveModel":
        new_ctx["catalogModelIds"] = [
            item for item in new_ctx.get("catalogModelIds") or [] if item != model_id
        ]
        providers = dict(new_ctx.get("catalogProviders") or {})
        providers.pop(model_id, None)
        new_ctx["catalogProviders"] = providers
        staged = S.new_capability_map(entries, updated_at=updated_at)
        return capability_map.apply_stale_triggers(staged, new_ctx), new_ctx

    if kind == "providerChange":
        providers = dict(new_ctx.get("catalogProviders") or {})
        providers[model_id] = _text(spec.get("provider"))
        new_ctx["catalogProviders"] = providers
        staged = S.new_capability_map(entries, updated_at=updated_at)
        return capability_map.apply_stale_triggers(staged, new_ctx), new_ctx

    if kind in ("routeContractChange", "effortContractChange"):
        # 현재 계약을 ctx에 싣고 production `apply_stale_triggers`로 전이를 유도한다
        # (`{modelId: {routeKey: 계약}}` — Requirement 6.17, 6.18).
        ctx_key = "routeContracts" if kind == "routeContractChange" else "effortContracts"
        by_model = {
            key: dict(value)
            for key, value in (new_ctx.get(ctx_key) or {}).items()
            if isinstance(value, Mapping)
        }
        route_map = by_model.setdefault(model_id, {})
        route_map[_text(spec.get("route"))] = copy.deepcopy(spec.get("contract"))
        new_ctx[ctx_key] = by_model
        staged = S.new_capability_map(entries, updated_at=updated_at)
        return capability_map.apply_stale_triggers(staged, new_ctx), new_ctx

    if kind == "revisionBump":
        new_ctx["revision"] = _text(spec.get("revision"))
        staged = S.new_capability_map(entries, updated_at=updated_at)
        return capability_map.apply_stale_triggers(staged, new_ctx), new_ctx

    if kind == "markStale":
        staged = S.new_capability_map(entries, updated_at=updated_at)
        return capability_map.mark_stale(staged, "test-change", {"modelId": model_id}), new_ctx

    if kind == "dropEntry":
        kept = [entry for entry in entries if _text(entry.get("modelId")) != model_id]
        return S.new_capability_map(kept, updated_at=updated_at), new_ctx

    if kind in ("routeUnsupported", "effortUnsupported", "fingerprintRotate"):
        route = _text(spec.get("route"))
        container_key = "effort" if kind == "effortUnsupported" else "routes"
        field = "allowlist" if kind == "fingerprintRotate" else "status"
        mutated: list[dict] = []
        for entry in entries:
            if _text(entry.get("modelId")) != model_id:
                mutated.append(entry)
                continue
            container = entry.get(container_key)
            sub_entry = container.get(route) if isinstance(container, dict) else None
            if not isinstance(sub_entry, dict):
                mutated.append(entry)
                continue
            sub_entry[field] = spec.get(field if field == "allowlist" else "status")
            mutated.append(_settle(entry))
        return S.new_capability_map(mutated, updated_at=updated_at), new_ctx

    return S.new_capability_map(entries, updated_at=updated_at), new_ctx


# ─────────────────────────────────────────────────────────────────
# 입력 전략 — 변경 시퀀스 시나리오
# ─────────────────────────────────────────────────────────────────
def _align(entry: dict, revision: str, catalog_fingerprint: str) -> dict:
    """entry의 현재성 필드를 공유 값으로 맞춘다(Activation_Gate 통과 가능 상태 확보).

    `catalogFingerprint`는 fingerprint 입력이므로 재계산이 필요하다.
    """
    aligned = copy.deepcopy(entry)
    aligned["revision"] = revision
    aligned["catalogFingerprint"] = catalog_fingerprint
    return _settle(aligned)


def _context_for(items: Sequence[dict], revision: str, catalog_fingerprint: str, now: str) -> dict:
    """Activation_Gate ctx. catalog 쪽 값은 map의 identity에서 파생해 일관성을 유지한다."""
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


def _locate(items: Sequence[dict], entry: Mapping[str, Any]) -> int | None:
    """`active_models`가 돌려준 복사본에 해당하는 items 위치를 찾는다."""
    label = _text(entry.get("candidateLabel"))
    model_id = _text(entry.get("modelId"))
    for index, item in enumerate(items):
        if _text(item.get("candidateLabel")) == label and _text(item.get("modelId")) == model_id:
            return index
    return None


@st.composite
def selection_scenarios(draw: Any) -> dict:
    """Property 8 입력: 초기 map·Effort_Settings + catalog·capability 변경 시퀀스.

    반환 형태::

        {"purposes", "map", "ctx", "settings", "desiredModelId", "changes",
         "otherModelId", "otherFingerprint"}

    Activation_Gate를 통과할 수 있는 entry를 1개 이상 포함시켜 "선택이 유지되는" 경로와
    "미선택으로 복구되는" 경로를 모두 exercise한다. 선택 route에 `SUPPORTED` effort를
    붙이고 그 tuple에 정확히 일치하는 저장 항목을 섞어 복원 경로도 함께 exercise한다.
    """
    purposes = draw(S.purpose_lists(min_size=1, max_size=2))
    revision = draw(S.revisions(allow_empty=False))
    catalog_fingerprint = draw(S.catalog_fingerprints(allow_empty=False))
    now = draw(S.utc_timestamps())

    ready_count = draw(st.integers(min_value=1, max_value=2))
    items: list[dict] = [
        _align(draw(S.entries(active_ready=True, purposes=purposes)), revision, catalog_fingerprint)
        for _ in range(ready_count)
    ]
    for _ in range(draw(st.integers(min_value=0, max_value=2))):
        other = draw(S.entries(purposes=purposes))
        items.append(
            _align(other, revision, catalog_fingerprint) if draw(st.booleans()) else other
        )

    # 경계값: 동일 modelId duplicate·동시각 tie·fingerprint 불일치·최신 verifiedAt 형제.
    if draw(st.booleans()):
        base = draw(st.sampled_from(items))
        kind, sibling = draw(S.sibling_entries(base))
        items.append(sibling if kind == "duplicate" else _settle(sibling))

    ctx = _context_for(items, revision, catalog_fingerprint, now)

    # 선택 route에 `SUPPORTED` effort를 붙여 복원 경로를 exercise한다.
    exact_setting: dict | None = None
    active = activation_gate.active_models(
        S.new_capability_map(items, updated_at=now), ctx, purposes=purposes
    )
    if active and draw(st.booleans()):
        chosen = draw(st.sampled_from(active))
        position = _locate(items, chosen)
        routes = activation_gate.eligible_route_keys(chosen, ctx=ctx, purposes=purposes)
        if position is not None and routes:
            route = routes[0]
            entry = copy.deepcopy(items[position])
            entry.setdefault("effort", {})[route] = draw(
                S.effort_entries(
                    route_key=route,
                    model_id=_text(entry.get("modelId")),
                    evidence=list(entry.get("evidence") or []),
                    status=_SUPPORTED_EFFORT,
                )
            )
            entry = _settle(entry)
            items[position] = entry
            exact_setting = draw(
                S.effort_settings_entries(
                    model_id=_text(entry.get("modelId")),
                    route=route,
                    capability_fingerprint=_text(entry.get("capabilityFingerprint")),
                    contract=entry["effort"][route]["contract"],
                    in_domain_value=draw(st.booleans()),
                )
            )

    map_obj = S.new_capability_map(items, updated_at=now)

    # 저장 설정: map에서 파생된 일치·불일치 항목 + 선택 tuple 정확 일치 항목(있을 때).
    settings = effort_settings.entries_of(draw(S.effort_settings_from_map(map_obj, max_entries=3)))
    if exact_setting is not None:
        settings.append(exact_setting)
        if draw(st.booleans()):  # 같은 tuple 중복(최신 updatedAt만 유지) 경계값
            settings.append({**copy.deepcopy(exact_setting), "updatedAt": draw(S.utc_timestamps())})

    model_pool = sorted({_text(entry.get("modelId")) for entry in items if _text(entry.get("modelId"))})
    active_ids = sorted({_text(entry.get("modelId")) for entry in active if _text(entry.get("modelId"))})
    desired_pool = active_ids or model_pool
    desired = draw(
        st.sampled_from(desired_pool) if desired_pool else S.model_ids(allow_empty=False)
    )

    return {
        "purposes": purposes,
        "map": map_obj,
        "ctx": ctx,
        "settings": S.new_effort_settings(settings),
        "desiredModelId": desired,
        "changes": draw(st.lists(change_specs(model_pool), min_size=1, max_size=3)),
        "otherModelId": draw(S.model_ids(allow_empty=False)),
        "otherFingerprint": draw(S.capability_fingerprint_strings()),
    }


# ─────────────────────────────────────────────────────────────────
# 단정 본문 (단일 property test에서만 호출한다)
# ─────────────────────────────────────────────────────────────────
def _assert_active_set(where: str, active: Sequence[dict], ctx: Mapping[str, Any], purposes: Sequence[str]) -> None:
    """R1 전반: 노출된 Active_Model 집합 자체가 선택 후보로 유효한지 확인한다."""
    model_ids = [_text(entry.get("modelId")) for entry in active]
    assert all(model_ids), (
        f"{where}: Active_Model에 비어 있는 Exact_Model_ID가 있다 — Property {PROPERTY_ID}: "
        f"{model_ids!r}"
    )
    assert len(model_ids) == len(set(model_ids)), (
        f"{where}: 같은 Exact_Model_ID가 중복 노출됐다 — Property {PROPERTY_ID}: {model_ids!r}"
    )
    for entry in active:
        ok, reason = activation_gate.is_active(entry, ctx, purposes=purposes)
        assert ok, (
            f"{where}: 노출된 entry가 is_active를 통과하지 못한다 — Property {PROPERTY_ID}: "
            f"reason={reason} entry={_brief(entry)}"
        )


def _assert_resolved_selection(
    where: str,
    selection: Mapping[str, Any] | None,
    active: Sequence[dict],
    desired_model_id: str,
    purposes: Sequence[str],
) -> None:
    """R1: 선택은 Active_Model 중 하나이거나 명시적 미선택이다."""
    active_ids = {_text(entry.get("modelId")) for entry in active}

    if selection is None:
        return  # 명시적 미선택 — 허용 상태

    model_id = _text(selection.get("modelId"))
    assert model_id in active_ids, (
        f"{where}: 선택이 Active_Model이 아니다 — Property {PROPERTY_ID}: "
        f"selected={model_id!r} active={sorted(active_ids)!r}"
    )
    assert model_id == desired_model_id, (
        f"{where}: 원하지 않은 모델이 선택됐다 — Property {PROPERTY_ID}: "
        f"selected={model_id!r} desired={desired_model_id!r}"
    )

    entry = next(item for item in active if _text(item.get("modelId")) == model_id)
    route = _text(selection.get("route"))

    # 선택 tuple의 route는 그 entry에서 실제로 `SUPPORTED` + allowlist `ALLOWED`여야 한다.
    route_entry = (entry.get("routes") or {}).get(route)
    assert isinstance(route_entry, dict), (
        f"{where}: 선택 route가 entry에 없다 — Property {PROPERTY_ID}: route={route!r}"
    )
    assert route_entry.get("status") == _SUPPORTED_ROUTE, (
        f"{where}: 선택 route가 SUPPORTED가 아니다 — Property {PROPERTY_ID}: "
        f"route={route!r} status={route_entry.get('status')!r}"
    )
    assert route_entry.get("allowlist") == _ALLOWED, (
        f"{where}: 선택 route의 allowlist가 ALLOWED가 아니다 — Property {PROPERTY_ID}: "
        f"route={route!r} allowlist={route_entry.get('allowlist')!r}"
    )

    # fingerprint 성분은 저장값이 아니라 재계산값과 같아야 한다(변경 후 회전 감지).
    recomputed = canonicalizer.capability_fingerprint(entry)
    assert _text(selection.get("capabilityFingerprint")) == recomputed, (
        f"{where}: 선택 tuple의 Capability_Fingerprint가 재계산값과 다르다 — "
        f"Property {PROPERTY_ID}"
    )


def _assert_prune(
    where: str,
    settings: dict,
    map_obj: dict,
    selection: Mapping[str, Any] | None,
    ctx_prune: Mapping[str, Any],
    index: Mapping[str, Any],
) -> dict:
    """R2: 불일치 Effort_Settings가 request 생성 전에 제거된다. 반환값은 정리된 settings."""
    before = canonicalizer.serialize(settings)
    pruned = effort_settings.prune(settings, ctx_prune)
    assert canonicalizer.serialize(settings) == before, (
        f"{where}: prune이 입력 settings를 변경했다 — Property {PROPERTY_ID}"
    )

    expected = _expected_surviving(settings, index, selection)
    kept = effort_settings.entries_of(pruned)
    kept_keys = [effort_settings.key_of(item) for item in kept]

    assert len(kept_keys) == len(set(kept_keys)), (
        f"{where}: 같은 tuple 항목이 중복으로 남았다 — Property {PROPERTY_ID}: {kept_keys!r}"
    )
    assert set(kept_keys) == set(expected), (
        f"{where}: 살아남은 tuple 집합이 기대와 다르다 — Property {PROPERTY_ID}\n"
        f"  kept={sorted(set(kept_keys))!r}\n  expected={sorted(set(expected))!r}\n"
        f"  selection={_brief(selection)}"
    )

    for item in kept:
        key = effort_settings.key_of(item)
        candidates = expected[key]
        assert any(canonicalizer.canonical_equal(item, candidate) for candidate in candidates), (
            f"{where}: 남은 항목이 입력에 없던 값이다 — Property {PROPERTY_ID}: {_brief(item)}"
        )
        newest = max(_moment(candidate.get("updatedAt")) for candidate in candidates)
        assert _moment(item.get("updatedAt")) == newest, (
            f"{where}: 중복 tuple에서 최신 updatedAt이 유지되지 않았다 — "
            f"Property {PROPERTY_ID}: {_brief(item)}"
        )

        # 남은 항목은 상태 조건을 모두 만족해야 하며(독립 판정), 선택 tuple과도 일치한다.
        assert not _keep_conditions(item, index.get(key[0])), (
            f"{where}: 상태 조건을 어긴 항목이 남았다 — Property {PROPERTY_ID}: "
            f"{_keep_conditions(item, index.get(key[0]))!r} item={_brief(item)}"
        )
        wanted = _tuple_of(selection)
        if wanted is not None:
            assert key == wanted, (
                f"{where}: 선택 tuple과 다른 항목이 남았다 — Property {PROPERTY_ID}: "
                f"kept={key!r} selection={wanted!r}"
            )

    # 제거된 항목에는 반드시 닫힌 집합의 제거 이유가 있다.
    for item in effort_settings.entries_of(settings):
        key = effort_settings.key_of(item)
        if key in expected and any(
            canonicalizer.canonical_equal(item, candidate) for candidate in expected[key]
        ):
            continue
        reasons = effort_settings.drop_reasons(item, ctx_prune)
        assert reasons, (
            f"{where}: 제거돼야 하는 항목에 제거 이유가 없다 — Property {PROPERTY_ID}: "
            f"{_brief(item)}"
        )
        unknown = [code for code in reasons if code not in effort_settings.DROP_REASONS]
        assert not unknown, (
            f"{where}: 알 수 없는 제거 이유 코드 — Property {PROPERTY_ID}: {unknown!r}"
        )

    # 재적용 안정성 — 정리된 settings를 다시 정리해도 결과가 같다.
    again = effort_settings.prune(pruned, ctx_prune)
    assert canonicalizer.serialize(again) == canonicalizer.serialize(pruned), (
        f"{where}: prune 재적용 결과가 다르다 — Property {PROPERTY_ID}"
    )
    return pruned


def _exact_tuple_matches(settings: Any, key: tuple[str, str, str] | None) -> list[dict]:
    """tuple 키가 ``key``와 **완전히 같은** 스키마 유효 저장 항목(독립 계산).

    세 성분 중 하나라도 비어 있거나 route가 Known_Route가 아니면 일치 항목이 없다고 본다
    (미확정 tuple로는 어떤 값도 복원하지 않는다 — Requirement 7.14).
    """
    if key is None or not all(key) or key[1] not in S.KNOWN_ROUTES:
        return []
    return [
        item
        for item in effort_settings.entries_of(settings)
        if not effort_settings.is_malformed_entry(item) and effort_settings.key_of(item) == key
    ]


def _assert_restore(
    where: str,
    settings: dict,
    pruned: dict,
    selection: Mapping[str, Any] | None,
    scenario: Mapping[str, Any],
) -> None:
    """R3: 복원은 요청 tuple 세 요소가 모두 일치하는 항목에만 일어난다.

    probe는 선택 tuple 자체(``exact``)와 **한 성분만** 바꾼 변형(다른 modelId·다른 route·
    다른 Capability_Fingerprint), 그리고 한 성분을 비운 변형이다. 각 probe에 대해 "요청
    tuple과 완전히 같은 저장 항목이 있는지"를 :func:`_exact_tuple_matches`로 독립 계산하고,
    ``restore``의 반환 유무가 그 계산과 정확히 일치하는지 확인한다.

    변형 probe가 값을 돌려주는 경우도 정상일 수 있다 — 저장 항목 중 **그 변형 tuple과
    완전히 일치하는** 다른 항목이 있을 수 있기 때문이다. 금지되는 것은 성분이 하나라도
    다른 요청에 선택 tuple의 항목이 복원되는 일이며, 아래에서 반환 항목의 tuple이 요청
    tuple과 같은지, 그리고 변형 요청이 선택 tuple 항목을 돌려주지 않는지를 단정한다.
    """
    probes: list[tuple[str, dict]] = []
    if selection is not None:
        probes.append(("exact", dict(selection)))
        other_model_id = _text(scenario.get("otherModelId"))
        other_fingerprint = _text(scenario.get("otherFingerprint"))
        alt_route = next(
            (route for route in S.KNOWN_ROUTES if route != _text(selection.get("route"))), None
        )
        if other_model_id and other_model_id != _text(selection.get("modelId")):
            probes.append(("modelId", {**selection, "modelId": other_model_id}))
        if alt_route is not None:
            probes.append(("route", {**selection, "route": alt_route}))
        if other_fingerprint != _text(selection.get("capabilityFingerprint")):
            probes.append(
                ("capabilityFingerprint", {**selection, "capabilityFingerprint": other_fingerprint})
            )
        for field in effort_settings.TUPLE_KEY_FIELDS:
            probes.append((f"{field}-empty", {**selection, field: contracts.UNDETERMINED}))

    selected_key = _tuple_of(selection)
    for label, probe in probes:
        wanted = _tuple_of(probe)
        for source_name, source in (("settings", settings), ("pruned", pruned)):
            candidates = _exact_tuple_matches(source, wanted)
            restored = effort_settings.restore(source, probe)

            assert (restored is not None) == bool(candidates), (
                f"{where}: {source_name}의 복원 결과가 tuple 완전 일치 여부와 어긋난다 — "
                f"Property {PROPERTY_ID}: probe={label} restored={restored is not None} "
                f"matches={len(candidates)} wanted={wanted!r}"
            )
            if restored is None:
                continue

            assert all(wanted), (
                f"{where}: 미확정 성분이 있는 tuple로 복원됐다 — Property {PROPERTY_ID}: "
                f"probe={label} wanted={wanted!r}"
            )
            assert effort_settings.key_of(restored) == wanted, (
                f"{where}: {source_name}에서 tuple이 다른 항목이 복원됐다 — "
                f"Property {PROPERTY_ID}: probe={label} "
                f"restored={effort_settings.key_of(restored)!r} wanted={wanted!r}"
            )
            assert any(
                canonicalizer.canonical_equal(restored, candidate) for candidate in candidates
            ), (
                f"{where}: 저장에 없던 항목이 복원됐다 — Property {PROPERTY_ID}: "
                f"probe={label} restored={_brief(restored)}"
            )
            if label != "exact":
                assert wanted != selected_key, (
                    f"{where}: 성분이 다른 요청에 선택 tuple 항목이 복원됐다 — "
                    f"Property {PROPERTY_ID}: probe={label} restored={_brief(restored)}"
                )

    if selection is None:
        return

    # 정리된 settings에서의 복원 가능성은 "살아남은 tuple 보유"와 동치다.
    wanted = _tuple_of(selection)
    restored = effort_settings.restore(pruned, selection)
    kept_keys = {effort_settings.key_of(item) for item in effort_settings.entries_of(pruned)}
    assert (restored is not None) == (wanted in kept_keys), (
        f"{where}: 복원 가능성과 정리 결과가 어긋난다 — Property {PROPERTY_ID}: "
        f"restored={restored is not None} keptKeys={sorted(kept_keys)!r}"
    )


def _assert_active_selection(
    where: str,
    settings: dict,
    pruned: dict,
    selection: Mapping[str, Any] | None,
    ctx_prune: Mapping[str, Any],
    index: Mapping[str, Any],
) -> None:
    """R4: request에 실리는 effort selection은 tuple 일치 + 상태 조건 충족일 때만 존재한다."""
    picked = effort_settings.active_selection(settings, ctx_prune)

    if selection is None:
        assert picked is None, (
            f"{where}: 미선택 상태인데 effort selection이 생성됐다 — Property {PROPERTY_ID}: "
            f"{_brief(picked)}"
        )
        return

    wanted = _tuple_of(selection)
    survivors = {
        effort_settings.key_of(item): item for item in effort_settings.entries_of(pruned)
    }
    assert (picked is not None) == (wanted in survivors), (
        f"{where}: effort selection 존재 여부가 정리 결과와 다르다 — Property {PROPERTY_ID}: "
        f"picked={_brief(picked)} keptKeys={sorted(survivors)!r}"
    )
    if picked is None:
        return

    assert _tuple_of(picked) == wanted, (
        f"{where}: effort selection tuple이 선택과 다르다 — Property {PROPERTY_ID}: "
        f"{_tuple_of(picked)!r} != {wanted!r}"
    )

    entry = index.get(wanted[0]) or {}
    contract = ((entry.get("effort") or {}).get(wanted[1]) or {}).get("contract")
    assert S.in_domain(contract, picked.get("value")), (
        f"{where}: verified domain을 벗어난 effort value가 request로 향한다 — "
        f"Property {PROPERTY_ID}: value={picked.get('value')!r} contract={_brief(contract)}"
    )
    assert _text(picked.get("valueType")) == _text((contract or {}).get("valueType")), (
        f"{where}: effort selection의 valueType이 계약과 다르다 — Property {PROPERTY_ID}"
    )
    assert canonicalizer.canonical_equal(picked.get("value"), survivors[wanted].get("value")), (
        f"{where}: 저장 값과 다른 값이 request로 향한다 — Property {PROPERTY_ID}"
    )


def _assert_step(
    where: str,
    map_obj: dict,
    ctx: Mapping[str, Any],
    scenario: Mapping[str, Any],
) -> None:
    """한 단계(초기 또는 변경 적용 직후)의 R1~R4 단정."""
    purposes = scenario["purposes"]
    settings = scenario["settings"]
    desired = _text(scenario["desiredModelId"])

    active, selection = _resolve_selection(map_obj, ctx, purposes, desired)
    _assert_active_set(where, active, ctx, purposes)
    _assert_resolved_selection(where, selection, active, desired, purposes)

    ctx_prune = _prune_context(map_obj, selection)
    index = effort_settings.capability_index({"capabilityMap": map_obj}) or {}

    pruned = _assert_prune(where, settings, map_obj, selection, ctx_prune, index)
    _assert_restore(where, settings, pruned, selection, scenario)
    _assert_active_selection(where, settings, pruned, selection, ctx_prune, index)


def _assert_selection_validity(scenario: Mapping[str, Any]) -> None:
    """변경 시퀀스 전 구간의 Property 8 단정.

    단정 순서는 **진단이 좁은 것부터**다: Active_Model 집합 → 선택 해석 → prune →
    restore → active_selection. 앞 단계가 깨지면 뒤 단계의 실패 원인을 알 수 없다.
    """
    map_obj = scenario["map"]
    ctx: Mapping[str, Any] = scenario["ctx"]
    desired = _text(scenario["desiredModelId"])

    _assert_step("step[0](initial)", map_obj, ctx, scenario)

    for index, spec in enumerate(scenario["changes"], start=1):
        map_obj, ctx = apply_change(map_obj, ctx, spec)
        where = f"step[{index}]({spec.get('kind')})"
        _assert_step(where, map_obj, ctx, scenario)

        # 변경이 원하는 모델의 entry를 없애거나 `STALE`로 만들었다면 선택은 미선택이어야 한다.
        entries = [
            entry
            for entry in capability_map.entries_of(map_obj)
            if _text(entry.get("modelId")) == desired
        ]
        dropped = not entries
        all_stale = bool(entries) and all(
            _text(entry.get("verificationStatus")) == _STALE for entry in entries
        )
        if dropped or all_stale:
            _, selection = _resolve_selection(map_obj, ctx, scenario["purposes"], desired)
            assert selection is None, (
                f"{where}: 삭제·STALE 전이된 모델이 선택으로 남았다 — Property {PROPERTY_ID}: "
                f"desired={desired!r} selection={_brief(selection)}"
            )


# ─────────────────────────────────────────────────────────────────
# Property 8 — 정확히 하나의 property test
# ─────────────────────────────────────────────────────────────────
@seed(S.AE_PBT_SEED)
@S.PBT_SETTINGS
@given(selection_scenarios())
def test_property8_selection_validity(scenario: dict) -> None:
    """Property 8: selection validity.

    임의 catalog·capability 변경 시퀀스 후 model 선택 결과는 Active_Model 중 하나이거나
    명시적 미선택이며, 선택 tuple과 정확히 일치하지 않는 Effort_Settings는 request 생성
    전에 제거된다. 복원은 `(modelId, route, capabilityFingerprint)` 세 요소가 모두 일치할
    때만 일어난다.

    **Validates: Requirements 7.7, 7.8, 7.9, 7.10, 7.11, 7.12, 7.13, 7.14, 12.16**
    """
    try:
        _assert_selection_validity(scenario)
    except AssertionError as exc:
        record_counterexample(
            str(exc),
            {
                "capabilityMap": scenario["map"],
                "context": scenario["ctx"],
                "effortSettings": scenario["settings"],
                "desiredModelId": scenario["desiredModelId"],
                "purposes": scenario["purposes"],
                "changes": scenario["changes"],
            },
        )
        raise


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-p", "no:cacheprovider", "-q"]))
