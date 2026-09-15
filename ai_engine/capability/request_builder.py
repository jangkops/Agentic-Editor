"""Request_Router + Request_Builder — verified 계약만으로 route와 body를 만든다.

이 모듈은 두 책임을 담는다.

**Request_Router**(순수 함수) — Exact_Model_ID로 식별된 Capability_Map entry에서
`Fallback_Order`(`fallbackRank` 오름차순)의 첫 `Eligible_Contract`를 선택하고, 후보가
없으면 `None`을 돌려 호출자가 Gateway 전송을 만들지 못하게 한다. 디스크·네트워크·시각·
난수에 접근하지 않으며 같은 입력에는 항상 같은 출력을 만든다.

**Request_Builder**(`EffortBoundClient` + :func:`_inject_effort`) — 기존
`GatewayClient`를 상속해 **builder seam만** 오버라이드하는 요청 단위 클라이언트다.
baseline body는 항상 기존 구현이 만들고, 계약·tuple·domain이 전부 일치할 때만 그
복사본에 effort 값을 정확히 1회 기록한다.

    baseline = super()._build_payload(...)        # 기존 구현이 만든 Baseline_Request_Body
    body = _inject_effort(baseline, contract, selection)
    #  불일치 → baseline과 **동일 객체**(구조·바이트 동일)
    #  일치   → baseline의 deep copy + 계약 field path 1회 기록

비침습 확장 (Requirement 1.13, 10.1~10.6, 10.19)
------------------------------------------------
- **신규 서명 코드 없음** — `execute-api`/`lambda` SigV4는 상속받은
  `GatewayClient._sign`·`stream_sse_realtime` 구현을 그대로 쓴다.
- **신규 credential 캐시 없음** — `_get_creds`·`force_refresh_creds`·
  `inject_credentials`를 base 인스턴스에 위임해 5분 캐시와 만료 강제 갱신을 하나로
  유지한다(:data:`CREDENTIAL_DELEGATED_METHODS`).
- **신규 URL 없음** — `gateway_url`·`STREAM_URL`은 base 설정을 그대로 복사·상속한다
  (:data:`DELEGATED_BASE_ATTRS`).
- retry·prefix 교정·job polling·응답 변환은 오버라이드하지 않는다
  (:data:`EFFORT_BOUND_OVERRIDES`가 허용 오버라이드의 닫힌 목록이다).

문자열 패턴 금지 (Requirement 8.2, 8.3)
---------------------------------------
route 결정 입력은 :data:`DECISION_INPUT_FIELDS`(``modelId``·``routes``·``evidence``)로
한정한다. :func:`decision_view`가 entry에서 이 세 필드만 남긴 view를 만들고, 모든 라우팅
판정은 그 view로만 수행한다. 따라서 ``candidateLabel``·``displayName``·``provider``
문자열은 구조적으로 결정에 개입할 수 없다(:data:`NON_INPUT_IDENTITY_FIELDS`). model ID는
어떤 entry의 계약인지 식별하는 값으로만 쓰고 패턴 매칭 대상으로 쓰지 않는다.

호출자 전송 계약 (Requirement 8.20, 8.22)
------------------------------------------
:func:`transmission_plan`이 라우팅 결정과 함께 전송 허용 여부를 반환한다.

    plan = transmission_plan(entry, purpose="chat", ctx=ctx)
    if not plan["transmit"]:
        # Gateway 전송을 **생성하지 않는다**. plan["maxTransmissions"] == 0.
        return failure_handler_path(plan["reason"])
    send(plan["routeKey"], plan["contract"])   # 최대 1건

``transmit``이 거짓이면 ``contract``·``routeKey``는 ``None``이고
``maxTransmissions``는 0이다. route가 `SUPPORTED`가 아니거나 Eligible_Contract가 없으면
언제나 이 경로이므로 미지원 route의 Gateway 전송 건수는 0으로 유지된다.

fallback 규칙 (Requirement 8.21, 8.22)
--------------------------------------
:func:`select_fallback_contract`는 `Fallback_Order`의 **첫** Eligible_Contract만
반환한다(:data:`FALLBACK_MAX_STEPS` = 1). 두 번째 후보로 연쇄 이동하지 않으며, 후보가
없으면 ``None``을 반환해 fallback 전송도 생성되지 않는다.

effort 주입 규칙 (Requirement 7.15~7.17, 8.16~8.19)
---------------------------------------------------
:func:`_inject_effort`는 아래 중 **하나라도** 성립하면 baseline body를 동일 객체로
그대로 반환한다(구조·바이트 동일, effort field 발생 0회).

  - selection 없음 · modelId 불일치 · route 불일치 · Capability_Fingerprint 불일치
  - Effort_Support_Status가 `SUPPORTED`가 아님 · 계약 불완전 · 계약이 다른 model·route에 결속
  - value type 불일치 · verified domain 이탈 · field path로 쓸 수 없는 body 구조

전부 일치하면 baseline의 deep copy에 `Effort_Contract.fieldPath`로 값을 **정확히 1회**
기록하고, 그 경로 외에는 어떤 key도 추가·삭제·변형하지 않는다.

jobs `modelId` 계약 (Requirement 8.11, 8.12)
--------------------------------------------
:func:`apply_jobs_model_id`는 verified jobs Route_Contract만 따른다.
`modelIdRequired`가 참이면 계약 path에 Invocation_Model_ID를 1회 기록하고, 거짓이면
body 전체에서 ``modelId`` key를 0회로 만든다. 계약이 미확정이면 ``None``을 반환해
호출자가 상속받은 기본 구현(기존 동작과 바이트 동일)에 위임하도록 한다.

책임 경계:
  - Eligible_Contract 판정·Active_Model 노출 → :mod:`.activation_gate`
  - Malformed 판정·계약 완전성 → :mod:`.contracts`
  - 저장된 effort 선택의 정리·복원·domain 판정 → :mod:`.effort_settings`
    (:func:`~.effort_settings.value_in_domain`·:func:`~.effort_settings.to_selection`를
    재사용한다 — 허용값 판정 로직을 여기에 복제하지 않는다)
  - 실패 분류·복구 계획(retry·fallback 수행) → :mod:`.failure_handler`
    (:class:`~.failure_handler.FailureHooks`의 ``select_contract`` 훅에
    :func:`select_contract`를 그대로 주입할 수 있다 — 시그니처가 동일하다)

참조: .kiro/specs/gateway-models-effort-support/design.md
  - "Components and Interfaces" 5절 (Request_Router / Request_Builder)
  - "verified fallback 규칙", "route별 body 생성 규칙"
  - Correctness Property 3·4·9·10
Requirements: 1.13, 7.15, 7.16, 7.17, 8.1, 8.2, 8.3, 8.13, 8.14, 8.15, 8.16, 8.17,
8.18, 8.19, 8.20, 8.21, 8.22, 10.1, 10.2, 10.3, 10.4, 10.5, 10.6, 10.19
"""
from __future__ import annotations

import copy
from typing import Any, Iterable, Mapping

from . import activation_gate, contracts, effort_settings

# ─────────────────────────────────────────────────────────────────
# route 결정 입력 (Requirement 8.1~8.3)
# ─────────────────────────────────────────────────────────────────

#: 라우팅 판정이 읽는 entry 필드. 이 밖의 필드는 결정에 개입하지 않는다.
DECISION_INPUT_FIELDS: tuple[str, ...] = ("modelId", "routes", "evidence")

#: 라우팅 판정이 **절대** 읽지 않는 식별 문자열 필드(Requirement 8.2, 8.3).
NON_INPUT_IDENTITY_FIELDS: tuple[str, ...] = ("candidateLabel", "displayName", "provider")

#: fallback은 첫 Eligible_Contract 한 단계만 사용한다(연쇄 이동 금지).
FALLBACK_MAX_STEPS = 1

# ─────────────────────────────────────────────────────────────────
# 이유 코드 (Activation_Gate 어휘 재사용 — 보고서 어휘를 하나로 유지)
# ─────────────────────────────────────────────────────────────────

#: 차단 이유가 없음(전송 허용).
REASON_OK = activation_gate.REASON_OK

NO_ELIGIBLE_CONTRACT = activation_gate.NO_ELIGIBLE_CONTRACT
ROUTE_NOT_SUPPORTED = activation_gate.ROUTE_NOT_SUPPORTED
ROUTE_CONTRACT_MISSING = activation_gate.ROUTE_CONTRACT_MISSING
ROUTE_CONTRACT_INCOMPLETE = activation_gate.ROUTE_CONTRACT_INCOMPLETE
ROUTE_CONTRACT_KEY_MISMATCH = activation_gate.ROUTE_CONTRACT_KEY_MISMATCH
ROUTE_EVIDENCE_MISSING = activation_gate.ROUTE_EVIDENCE_MISSING
ROUTE_PURPOSE_UNMET = activation_gate.ROUTE_PURPOSE_UNMET
ROUTE_ALLOWLIST_NOT_ALLOWED = activation_gate.ROUTE_ALLOWLIST_NOT_ALLOWED

#: 요청된 route key가 Known_Route가 아니다.
ROUTE_UNKNOWN = "ROUTE_UNKNOWN"

#: 요청된 route가 호출자 지정 제외 목록(이미 실패한 route 등)에 있다.
ROUTE_EXCLUDED = "ROUTE_EXCLUDED"


def _dedup(codes: Iterable[str]) -> tuple[str, ...]:
    """선언 순서를 보존하며 중복만 제거한다."""
    seen: dict[str, None] = {}
    for code in codes:
        seen.setdefault(code, None)
    return tuple(seen)


#: 이 모듈이 산출할 수 있는 모든 차단 이유 코드.
DECISION_REASONS: tuple[str, ...] = _dedup(
    (ROUTE_UNKNOWN, ROUTE_EXCLUDED, NO_ELIGIBLE_CONTRACT)
    + activation_gate.CONTRACT_REASONS
    + activation_gate.DEACTIVATION_REASONS
)


# ─────────────────────────────────────────────────────────────────
# 작은 술어·유틸
# ─────────────────────────────────────────────────────────────────
def _is_dict(value: Any) -> bool:
    return isinstance(value, dict)


def _text(value: Any) -> str:
    """문자열 필드를 안전하게 읽는다(문자열이 아니면 미확정으로 취급)."""
    return value if isinstance(value, str) else contracts.UNDETERMINED


def _excluded(exclude_routes: Iterable[str]) -> frozenset[str]:
    if isinstance(exclude_routes, str):
        return frozenset({exclude_routes})
    return frozenset(route for route in exclude_routes if isinstance(route, str) and route)


def normalized_purposes(
    purpose: Any = None,
    ctx: Mapping[str, Any] | None = None,
) -> tuple[str, ...]:
    """요청 목적을 정규화한다(Activation_Gate와 동일 규칙).

    ``purpose``는 문자열 또는 문자열 collection이며, 주지 않으면 ``ctx["purposes"]`` →
    ``ctx["purpose"]`` 순으로 읽는다. 아무 것도 없으면 빈 tuple(목적 제약 없음)이다.
    """
    return activation_gate.requested_purposes(ctx, purpose)


def decision_view(entry: Any) -> dict:
    """route 결정 입력만 남긴 entry view를 만든다(Requirement 8.2, 8.3).

    ``candidateLabel``·``displayName``·``provider``는 view에 존재하지 않으므로 어떤
    라우팅 판정도 그 문자열에 의존할 수 없다. 계약 dict은 참조로 담기지만, 반환되는
    계약은 항상 깊은 복사본이므로 호출자 수정이 entry로 새지 않는다.
    """
    source = entry if _is_dict(entry) else {}
    return {field: source.get(field) for field in DECISION_INPUT_FIELDS}


# ─────────────────────────────────────────────────────────────────
# Fallback_Order (Requirement 8.1, 8.21)
# ─────────────────────────────────────────────────────────────────
def _eligible_items(
    entry: Any,
    purpose: Any = None,
    ctx: Mapping[str, Any] | None = None,
    *,
    exclude_routes: Iterable[str] = (),
) -> list[dict]:
    """Eligible_Contract 항목을 Fallback_Order로 반환한다(내부용).

    항목: ``{"routeKey", "fallbackRank", "evidenceRef", "contract"}``. 정렬은
    Activation_Gate와 동일하게 `fallbackRank` 오름차순, 동일 rank는 route key의 UTF-8
    바이트 순이므로 입력 순서와 무관하게 결정론적이다.
    """
    items = activation_gate.eligible_contracts(
        decision_view(entry),
        ctx=ctx,
        purposes=normalized_purposes(purpose, ctx),
    )
    excluded = _excluded(exclude_routes)
    return [item for item in items if item["routeKey"] not in excluded]


def fallback_order(
    entry: Any,
    purpose: Any = None,
    ctx: Mapping[str, Any] | None = None,
    *,
    exclude_routes: Iterable[str] = (),
) -> list[dict]:
    """Eligible_Contract 계약 목록을 `Fallback_Order`로 반환한다(깊은 복사본).

    첫 항목이 :func:`select_contract`의 선택 결과다. 목록이 비면 Gateway 전송을
    생성할 수 없다(Requirement 8.22).
    """
    return [item["contract"] for item in _eligible_items(entry, purpose, ctx, exclude_routes=exclude_routes)]


def fallback_route_keys(
    entry: Any,
    purpose: Any = None,
    ctx: Mapping[str, Any] | None = None,
    *,
    exclude_routes: Iterable[str] = (),
) -> list[str]:
    """`Fallback_Order`의 route key 목록(보고서·로그용 — 비밀정보 없음)."""
    return [
        item["routeKey"]
        for item in _eligible_items(entry, purpose, ctx, exclude_routes=exclude_routes)
    ]


def select_contract(
    entry: Any,
    purpose: Any = None,
    ctx: Mapping[str, Any] | None = None,
) -> dict | None:
    """`Fallback_Order`의 첫 Eligible_Contract를 반환한다(없으면 ``None``).

    Candidate_Label 문자열 패턴과 Provider_String 문자열 패턴은 입력으로 사용하지
    않는다(:func:`decision_view`). ``None``이면 호출자는 Gateway 전송을 생성하지
    않는다(Requirement 8.20, 8.22).

    Args:
        entry: Capability_Map entry(Exact_Model_ID로 식별된 관리 대상 entry).
        purpose: 요청 목적. 문자열 또는 문자열 collection. 없으면 ctx에서 읽는다.
        ctx: 현재성 컨텍스트(:mod:`.activation_gate` 참조).

    Returns:
        Route_Contract의 깊은 복사본(``routeKey`` 포함) 또는 ``None``.
    """
    items = _eligible_items(entry, purpose, ctx)
    return items[0]["contract"] if items else None


def select_route(
    entry: Any,
    purpose: Any = None,
    ctx: Mapping[str, Any] | None = None,
) -> str | None:
    """선택된 Known_Route key(없으면 ``None``)."""
    items = _eligible_items(entry, purpose, ctx)
    return items[0]["routeKey"] if items else None


def select_fallback_contract(
    entry: Any,
    purpose: Any = None,
    ctx: Mapping[str, Any] | None = None,
    *,
    exclude_routes: Iterable[str] = (),
) -> dict | None:
    """fallback 계약을 선택한다 — `Fallback_Order`의 **첫** 후보만(연쇄 이동 금지).

    ``exclude_routes``에는 이미 실패한 route를 넣는다(실패한 route로의 재전송은
    fallback이 아니다). 남은 후보가 없으면 ``None``이며 fallback 전송도 생성되지
    않는다(Requirement 8.22). 이 함수는 한 단계만 수행한다
    (:data:`FALLBACK_MAX_STEPS` = 1) — 반환된 후보가 다시 실패하더라도 다음 후보로
    이동하지 않는다.
    """
    items = _eligible_items(entry, purpose, ctx, exclude_routes=exclude_routes)
    return items[0]["contract"] if items else None


# ─────────────────────────────────────────────────────────────────
# route 단위 판정 (Requirement 8.20)
# ─────────────────────────────────────────────────────────────────
def route_block_reason(
    entry: Any,
    route_key: Any,
    purpose: Any = None,
    ctx: Mapping[str, Any] | None = None,
    *,
    exclude_routes: Iterable[str] = (),
) -> str:
    """특정 route로 전송할 수 없는 대표 이유(전송 가능하면 :data:`REASON_OK`)."""
    if not isinstance(route_key, str) or not contracts.Known_Route.has(route_key):
        return ROUTE_UNKNOWN
    if route_key in _excluded(exclude_routes):
        return ROUTE_EXCLUDED
    reasons = activation_gate.contract_ineligibility_reasons(
        decision_view(entry),
        route_key,
        ctx=ctx,
        purposes=normalized_purposes(purpose, ctx),
    )
    return reasons[0] if reasons else REASON_OK


def may_transmit(
    entry: Any,
    route_key: Any,
    purpose: Any = None,
    ctx: Mapping[str, Any] | None = None,
    *,
    exclude_routes: Iterable[str] = (),
) -> bool:
    """해당 route로 Gateway 전송을 생성해도 되는지.

    route 상태가 `SUPPORTED`가 아니거나 계약·evidence·목적·allowlist 조건 중 하나라도
    어긋나면 거짓이며, 그때 전송 건수는 0이어야 한다(Requirement 8.20).
    """
    return route_block_reason(entry, route_key, purpose, ctx, exclude_routes=exclude_routes) == REASON_OK


def contract_for_route(
    entry: Any,
    route_key: Any,
    purpose: Any = None,
    ctx: Mapping[str, Any] | None = None,
    *,
    exclude_routes: Iterable[str] = (),
) -> dict | None:
    """지정 route의 Eligible_Contract 깊은 복사본(자격이 없으면 ``None``)."""
    if not may_transmit(entry, route_key, purpose, ctx, exclude_routes=exclude_routes):
        return None
    contract = entry["routes"][route_key].get("contract")
    return copy.deepcopy(contract) if _is_dict(contract) else None


# ─────────────────────────────────────────────────────────────────
# 호출자 전송 계약 (Requirement 8.20, 8.22)
# ─────────────────────────────────────────────────────────────────
def _plan(
    *,
    model_id: str,
    reason: str,
    route_key: str | None = None,
    contract: dict | None = None,
    fallback_rank: int | None = None,
    evidence_ref: str = contracts.UNDETERMINED,
    requested_route: str | None = None,
    order: list[str] | None = None,
) -> dict:
    """전송 계획 record. ``transmit``이 거짓이면 전송 건수는 0이다."""
    transmit = contract is not None and route_key is not None
    return {
        "transmit": transmit,
        "maxTransmissions": 1 if transmit else 0,
        "modelId": model_id,
        "routeKey": route_key if transmit else None,
        "requestedRoute": requested_route,
        "contract": contract if transmit else None,
        "fallbackRank": fallback_rank if transmit else None,
        "evidenceRef": evidence_ref if transmit else contracts.UNDETERMINED,
        "fallbackOrder": list(order or []),
        "reason": reason,
    }


def transmission_plan(
    entry: Any,
    purpose: Any = None,
    ctx: Mapping[str, Any] | None = None,
    *,
    route_key: Any = None,
    exclude_routes: Iterable[str] = (),
    require_active: bool = False,
) -> dict:
    """라우팅 결정과 전송 허용 여부를 함께 반환한다(호출자 계약).

    ``transmit``이 거짓이면 호출자는 **Gateway 전송을 생성하지 않는다**
    (``maxTransmissions == 0``) — 미지원 route의 전송 건수 0을 이 계약으로 보장한다
    (Requirement 8.20). Eligible_Contract가 없을 때도 같은 경로이므로 fallback 전송도
    생성되지 않는다(Requirement 8.22).

    Args:
        entry: Capability_Map entry.
        purpose: 요청 목적(문자열 또는 collection). 없으면 ctx에서 읽는다.
        ctx: 현재성 컨텍스트.
        route_key: 특정 route를 요청할 때 지정. ``None``이면 `Fallback_Order` 첫
            Eligible_Contract를 선택한다(Requirement 8.21).
        exclude_routes: 후보에서 제외할 route(이미 실패한 route 등).
        require_active: 참이면 Activation_Gate의 entry 단위 판정(`VERIFIED`·
            Current_Evidence·fingerprint 일치 등)을 추가로 요구한다. 이 검사는 식별
            필드의 **존재·일치**만 보며 라벨·provider 문자열 패턴을 쓰지 않는다.

    Returns:
        ``{"transmit", "maxTransmissions", "modelId", "routeKey", "requestedRoute",
        "contract", "fallbackRank", "evidenceRef", "fallbackOrder", "reason"}``.
        ``contract``는 깊은 복사본이고 ``reason``은 :data:`DECISION_REASONS`의 한 값
        (허용 시 :data:`REASON_OK`)이다.
    """
    view = decision_view(entry)
    model_id = _text(view.get("modelId"))
    purposes = normalized_purposes(purpose, ctx)
    requested = route_key if isinstance(route_key, str) else None

    if require_active:
        active, reason = activation_gate.is_active(entry, ctx, purposes=purposes)
        if not active:
            return _plan(model_id=model_id, reason=reason, requested_route=requested)

    items = _eligible_items(entry, purposes, ctx, exclude_routes=exclude_routes)
    order = [item["routeKey"] for item in items]

    if route_key is not None:
        reason = route_block_reason(entry, route_key, purposes, ctx, exclude_routes=exclude_routes)
        if reason != REASON_OK:
            return _plan(model_id=model_id, reason=reason, requested_route=requested, order=order)
        chosen = next(item for item in items if item["routeKey"] == route_key)
    else:
        if not items:
            return _plan(
                model_id=model_id,
                reason=NO_ELIGIBLE_CONTRACT,
                requested_route=requested,
                order=order,
            )
        chosen = items[0]

    return _plan(
        model_id=model_id,
        reason=REASON_OK,
        route_key=chosen["routeKey"],
        contract=chosen["contract"],
        fallback_rank=chosen["fallbackRank"],
        evidence_ref=chosen["evidenceRef"],
        requested_route=requested,
        order=order,
    )


# ═════════════════════════════════════════════════════════════════
# Request_Binding — entry 식별값을 선택된 Route_Contract에 결속한다
# ═════════════════════════════════════════════════════════════════

#: Request_Binding이 effort 주입 판정에 쓰는 식별 필드(셋 다 채워져야 한다).
BINDING_IDENTITY_FIELDS: tuple[str, ...] = ("modelId", "routeKey", "capabilityFingerprint")

#: 게이트웨이 전용 model ID key 이름(동기 Responses body에는 0개여야 한다).
MODEL_ID_KEY = "modelId"

#: 값이 없음을 나타내는 sentinel(``None``과 구분한다).
MISSING: Any = object()


def bind_contract(
    entry: Any,
    contract: Any,
    *,
    invocation_model_id: Any = None,
) -> dict:
    """선택된 Route_Contract에 entry 식별값과 route별 Effort_Entry를 결속한다.

    반환 dict(=Request_Binding)는 Route_Contract의 모든 필드에 다음을 더한 것이다.

    ==========================  ================================================
    ``modelId``                 Exact_Model_ID(entry 값 그대로)
    ``capabilityFingerprint``   entry의 현재 Capability_Fingerprint
    ``invocationModelIds``      관측된 Invocation_Model_ID 목록(집합 의미)
    ``invocationModelId``       이번 요청에 쓸 Invocation_Model_ID(없으면 미확정)
    ``effort``                  해당 route의 Effort_Entry(`{status, contract, …}`)
    ==========================  ================================================

    entry나 contract가 dict가 아니면 빈 값으로 채운 binding을 만든다. 값은 모두 깊은
    복사본이므로 호출자가 수정해도 Capability_Map에 영향을 주지 않는다. 라벨·provider
    문자열은 binding에 담지 않는다(Requirement 8.2, 8.3).
    """
    source = entry if _is_dict(entry) else {}
    binding = copy.deepcopy(contract) if _is_dict(contract) else {}

    route_key = _text(binding.get("routeKey"))
    effort_map = source.get("effort")
    effort_entry = effort_map.get(route_key) if _is_dict(effort_map) and route_key else None

    observed = source.get("invocationModelIds")
    binding["modelId"] = _text(source.get("modelId"))
    binding["capabilityFingerprint"] = _text(source.get("capabilityFingerprint"))
    binding["invocationModelIds"] = (
        [item for item in observed if isinstance(item, str) and item] if isinstance(observed, list) else []
    )
    binding["invocationModelId"] = _text(invocation_model_id)
    binding["effort"] = copy.deepcopy(effort_entry) if _is_dict(effort_entry) else None
    return binding


def binding_for(
    entry: Any,
    purpose: Any = None,
    ctx: Mapping[str, Any] | None = None,
    *,
    route_key: Any = None,
    exclude_routes: Iterable[str] = (),
    require_active: bool = False,
    invocation_model_id: Any = None,
) -> dict | None:
    """라우팅 결정까지 수행해 Request_Binding을 만든다(전송 불가면 ``None``).

    :func:`transmission_plan`으로 전송 허용 여부를 먼저 판정하므로, ``None``이면
    호출자는 Gateway 전송을 생성하지 않는다(Requirement 8.20, 8.22). 차단 이유가
    필요하면 :func:`transmission_plan`을 직접 호출한다.
    """
    plan = transmission_plan(
        entry,
        purpose,
        ctx,
        route_key=route_key,
        exclude_routes=exclude_routes,
        require_active=require_active,
    )
    if not plan["transmit"]:
        return None
    return bind_contract(entry, plan["contract"], invocation_model_id=invocation_model_id)


def effort_view(binding: Any) -> tuple[str, Any]:
    """binding에서 `(Effort_Support_Status, Effort_Contract)`를 읽는다.

    두 형태를 모두 받는다.
      - Capability_Map의 Effort_Entry 형태: ``{"status": …, "contract": {…}}``
      - design 표기의 평탄 형태: ``{"status": …, "fieldPath": […], …}``
        (이 형태를 쓸 때는 `modelId`·`routeKey`도 함께 담아야 계약 완전성 검사를
        통과한다 — 담기지 않으면 주입하지 않는다)

    effort 정보가 없으면 ``("", None)``을 반환한다.
    """
    if not _is_dict(binding):
        return contracts.UNDETERMINED, None
    raw = binding.get("effort")
    if not _is_dict(raw):
        return contracts.UNDETERMINED, None
    status = _text(raw.get("status"))
    if "contract" in raw:
        return status, raw.get("contract")
    if any(field in raw for field in ("fieldPath", "valueType", "domainKind")):
        return status, {key: value for key, value in raw.items() if key != "status"}
    return status, None


def invocation_model_id(binding: Any, fallback: Any = contracts.UNDETERMINED) -> str:
    """이번 요청에 기록할 Invocation_Model_ID를 고른다(추론하지 않는다).

    우선순위는 ``binding["invocationModelId"]`` → 관측된 ID가 정확히 하나면 그 값 →
    ``fallback``(기존 transport가 넘긴 model ID, prefix 교정 결과 포함)이다. 라벨·
    provider·모델 계열에서 ID를 만들어내지 않는다(Requirement 2.18).
    """
    default = fallback if isinstance(fallback, str) else contracts.UNDETERMINED
    if not _is_dict(binding):
        return default
    explicit = _text(binding.get("invocationModelId"))
    if explicit:
        return explicit
    observed = binding.get("invocationModelIds")
    if isinstance(observed, list):
        usable = [item for item in observed if isinstance(item, str) and item]
        if len(usable) == 1:
            return usable[0]
        if default in usable:
            return default
    return default


# ═════════════════════════════════════════════════════════════════
# body 경로 유틸 — 입력 body는 절대 변형하지 않는다
# ═════════════════════════════════════════════════════════════════
def is_field_path(path: Any) -> bool:
    """계약 field path 형식인지(비어 있지 않은 문자열 목록)."""
    return (
        isinstance(path, (list, tuple))
        and bool(path)
        and all(isinstance(part, str) and part for part in path)
    )


def count_key_occurrences(value: Any, key: str) -> int:
    """body 전체(모든 중첩 경로)에서 key가 나타난 횟수(Property 9·10 판정용)."""
    if isinstance(value, dict):
        total = 1 if key in value else 0
        return total + sum(count_key_occurrences(item, key) for item in value.values())
    if isinstance(value, (list, tuple)):
        return sum(count_key_occurrences(item, key) for item in value)
    return 0


def value_at_path(body: Any, path: Any, default: Any = MISSING) -> Any:
    """path가 가리키는 값(없으면 ``default``, 기본은 :data:`MISSING`)."""
    if not is_field_path(path):
        return default
    current: Any = body
    for part in path:
        if not isinstance(current, dict) or part not in current:
            return default
        current = current[part]
    return current


def path_exists(body: Any, path: Any) -> bool:
    """path가 body에 실제로 존재하는지."""
    return value_at_path(body, path) is not MISSING


def path_writable(body: Any, path: Any) -> bool:
    """path에 값을 기록할 수 있는지(중간 노드가 dict가 아니면 거짓).

    중간 노드가 없으면 새로 만들 수 있으므로 참이다. 이미 dict가 아닌 값이 놓여
    있으면 그 값을 덮어써야 하므로 거짓이다 — baseline을 파괴하지 않는다.
    """
    if not isinstance(body, dict) or not is_field_path(path):
        return False
    current: Any = body
    for part in path[:-1]:
        if not isinstance(current, dict):
            return False
        if part not in current:
            return True  # 이후 경로는 새로 만든다
        current = current[part]
    return isinstance(current, dict)


def write_once(body: Any, path: Any, value: Any) -> dict | None:
    """body의 deep copy에 path로 값을 **정확히 1회** 기록한 새 body를 반환한다.

    입력 body는 변형하지 않는다. 기록할 수 없는 구조(:func:`path_writable`가 거짓)면
    ``None``을 반환한다. 중간에 없는 dict만 새로 만들고, 그 경로 밖의 어떤 key도
    추가·삭제·변형하지 않는다.
    """
    if not path_writable(body, path):
        return None
    result = copy.deepcopy(body)
    current = result
    for part in path[:-1]:
        child = current.get(part)
        if not isinstance(child, dict):
            child = {}
            current[part] = child
        current = child
    current[path[-1]] = copy.deepcopy(value)
    return result


def without_key(value: Any, key: str) -> Any:
    """모든 중첩 경로에서 key를 제거한 deep copy(입력은 변형하지 않는다)."""
    if isinstance(value, dict):
        return {k: without_key(v, key) for k, v in value.items() if k != key}
    if isinstance(value, list):
        return [without_key(item, key) for item in value]
    if isinstance(value, tuple):
        return tuple(without_key(item, key) for item in value)
    return copy.deepcopy(value)


# ═════════════════════════════════════════════════════════════════
# effort 주입 (Requirement 7.15~7.17, 8.16~8.19)
# ═════════════════════════════════════════════════════════════════

#: selection이 없다(effort 미선택 또는 UI 숨김) — Requirement 7.15, 7.16.
EFFORT_NO_SELECTION = "EFFORT_NO_SELECTION"

#: binding의 modelId·routeKey·Capability_Fingerprint 중 하나가 미확정이다.
EFFORT_BINDING_INCOMPLETE = "EFFORT_BINDING_INCOMPLETE"

# 어휘는 Effort_Settings의 제거 이유 코드와 하나로 유지한다(보고서 일관성).
EFFORT_MODEL_ID_MISMATCH = effort_settings.DROP_MODEL_ID_MISMATCH
EFFORT_ROUTE_MISMATCH = effort_settings.DROP_ROUTE_MISMATCH
EFFORT_FINGERPRINT_MISMATCH = effort_settings.DROP_FINGERPRINT_MISMATCH
EFFORT_NOT_SUPPORTED = effort_settings.DROP_EFFORT_NOT_SUPPORTED
EFFORT_CONTRACT_INCOMPLETE = effort_settings.DROP_CONTRACT_INCOMPLETE
EFFORT_CONTRACT_MISBOUND = effort_settings.DROP_CONTRACT_MISBOUND
EFFORT_VALUE_TYPE_MISMATCH = effort_settings.DROP_VALUE_TYPE_MISMATCH
EFFORT_DOMAIN_VIOLATION = effort_settings.DROP_DOMAIN_VIOLATION

#: 계약 field path가 형식을 벗어났다(완전성 검사에서 걸러지지만 방어적으로 유지).
EFFORT_FIELD_PATH_INVALID = "EFFORT_FIELD_PATH_INVALID"

#: baseline body 구조가 계약 field path와 충돌한다(중간 노드가 dict가 아니다).
EFFORT_PATH_CONFLICT = "EFFORT_PATH_CONFLICT"

#: 주입 차단 이유 코드 — **판정 순서**대로 나열한다(첫 일치 코드를 보고한다).
INJECTION_REASONS: tuple[str, ...] = (
    EFFORT_NO_SELECTION,
    EFFORT_BINDING_INCOMPLETE,
    EFFORT_MODEL_ID_MISMATCH,
    EFFORT_ROUTE_MISMATCH,
    EFFORT_FINGERPRINT_MISMATCH,
    EFFORT_NOT_SUPPORTED,
    EFFORT_CONTRACT_INCOMPLETE,
    EFFORT_CONTRACT_MISBOUND,
    EFFORT_VALUE_TYPE_MISMATCH,
    EFFORT_DOMAIN_VIOLATION,
    EFFORT_FIELD_PATH_INVALID,
    EFFORT_PATH_CONFLICT,
)


def _selection_route(selection: Mapping[str, Any]) -> str:
    """selection의 route를 읽는다(`route` 우선, `routeKey`도 허용)."""
    route = _text(selection.get("route"))
    return route if route else _text(selection.get("routeKey"))


def injection_reason(
    contract: Any,
    selection: Any,
    *,
    baseline_body: Any = None,
) -> str:
    """effort 주입을 막는 첫 이유 코드(주입 가능하면 :data:`REASON_OK`).

    판정 순서는 :data:`INJECTION_REASONS`와 같다. ``baseline_body``를 주면 계약 field
    path로 실제로 기록할 수 있는지까지 확인한다(:data:`EFFORT_PATH_CONFLICT`).
    """
    if not _is_dict(selection) or not selection:
        return EFFORT_NO_SELECTION

    identity = {field: _text(contract.get(field)) if _is_dict(contract) else "" for field in BINDING_IDENTITY_FIELDS}
    if not all(identity.values()):
        return EFFORT_BINDING_INCOMPLETE

    if _text(selection.get("modelId")) != identity["modelId"]:
        return EFFORT_MODEL_ID_MISMATCH
    if _selection_route(selection) != identity["routeKey"]:
        return EFFORT_ROUTE_MISMATCH
    if _text(selection.get("capabilityFingerprint")) != identity["capabilityFingerprint"]:
        return EFFORT_FINGERPRINT_MISMATCH

    status, effort_contract = effort_view(contract)
    if status != contracts.Effort_Support_Status.SUPPORTED:
        return EFFORT_NOT_SUPPORTED
    if not contracts.effort_contract_is_complete(effort_contract):
        return EFFORT_CONTRACT_INCOMPLETE
    if (
        _text(effort_contract.get("modelId")) != identity["modelId"]
        or _text(effort_contract.get("routeKey")) != identity["routeKey"]
    ):
        return EFFORT_CONTRACT_MISBOUND

    selected_type = _text(selection.get("valueType"))
    if selected_type and selected_type != _text(effort_contract.get("valueType")):
        return EFFORT_VALUE_TYPE_MISMATCH
    if not effort_settings.value_in_domain(effort_contract, selection.get("value")):
        return EFFORT_DOMAIN_VIOLATION

    field_path = effort_contract.get("fieldPath")
    if not is_field_path(field_path):
        return EFFORT_FIELD_PATH_INVALID
    if baseline_body is not None and not path_writable(baseline_body, field_path):
        return EFFORT_PATH_CONFLICT
    return REASON_OK


def effort_injection_plan(
    contract: Any,
    selection: Any,
    *,
    baseline_body: Any = None,
) -> dict:
    """주입 계획 record(감사·보고서용 — 비밀정보 없음).

    반환: ``{"inject", "reason", "routeKey", "fieldPath", "value", "valueType"}``.
    ``inject``가 거짓이면 ``fieldPath``는 ``None``이고 body는 baseline 그대로 쓴다.
    """
    reason = injection_reason(contract, selection, baseline_body=baseline_body)
    route_key = _text(contract.get("routeKey")) if _is_dict(contract) else contracts.UNDETERMINED
    if reason != REASON_OK:
        return {
            "inject": False,
            "reason": reason,
            "routeKey": route_key,
            "fieldPath": None,
            "value": None,
            "valueType": contracts.UNDETERMINED,
        }
    _, effort_contract = effort_view(contract)
    return {
        "inject": True,
        "reason": REASON_OK,
        "routeKey": route_key,
        "fieldPath": list(effort_contract["fieldPath"]),
        "value": copy.deepcopy(selection.get("value")),
        "valueType": _text(effort_contract.get("valueType")),
    }


def _inject_effort(baseline_body: Any, contract: Any, selection: Any) -> Any:
    """baseline body에 effort 값을 조건부로 1회 주입한다.

    다음 중 하나라도 성립하면 ``baseline_body``를 **동일 객체로 그대로** 반환한다
    (구조·바이트 동일 — Requirement 7.15~7.17, 8.17~8.19):

      - selection 없음(effort 미선택 · effort UI 숨김)
      - modelId·route·Capability_Fingerprint 불일치
      - Effort_Support_Status가 `SUPPORTED`가 아님 · 계약 불완전 · 계약 결속 위반
      - value type 불일치 · verified domain 이탈
      - 계약 field path로 기록할 수 없는 body 구조

    전부 일치하면 baseline의 deep copy에 `Effort_Contract.fieldPath`로 값을 **정확히
    1회** 기록해 반환한다. 그 경로를 제외한 나머지 body는 baseline과 동일하다
    (Requirement 8.16).

    Args:
        baseline_body: 기존 builder(`_build_payload`·`_build_openai_payload`)가 만든
            Baseline_Request_Body.
        contract: Request_Binding(:func:`bind_contract` 결과).
        selection: `{modelId, route, capabilityFingerprint, value, valueType}`
            (:func:`~.effort_settings.to_selection` 형식) 또는 ``None``.

    Returns:
        baseline과 동일한 객체 또는 값이 1회 기록된 새 dict.
    """
    plan = effort_injection_plan(contract, selection, baseline_body=baseline_body)
    if not plan["inject"]:
        return baseline_body
    written = write_once(baseline_body, plan["fieldPath"], plan["value"])
    return baseline_body if written is None else written


#: 공개 별칭 — 테스트·seam은 이 이름으로 호출한다(동작은 :func:`_inject_effort`와 같다).
inject_effort = _inject_effort


# ═════════════════════════════════════════════════════════════════
# jobs route의 model ID 부착 (Requirement 8.11, 8.12)
# ═════════════════════════════════════════════════════════════════
def jobs_contract(binding: Any) -> dict | None:
    """binding이 `/openai/responses-jobs` 계약일 때만 그 계약을 반환한다.

    다른 route에 결속된 binding이면 ``None``이다 — jobs 계약이 아닌 근거로 jobs body를
    바꾸지 않는다.
    """
    if not _is_dict(binding):
        return None
    if _text(binding.get("routeKey")) != contracts.Known_Route.OPENAI_RESPONSES_JOBS:
        return None
    return binding


def apply_jobs_model_id(body: Any, model_id: Any, binding: Any) -> dict | None:
    """verified jobs 계약대로 model ID를 부착한 새 body(계약 미확정이면 ``None``).

    - ``modelIdRequired``가 참: 계약 ``modelIdFieldPath``에 Invocation_Model_ID를
      **정확히 1회** 기록하고, 그 밖의 경로에는 ``modelId`` key를 남기지 않는다.
    - ``modelIdRequired``가 거짓: body 전체에서 ``modelId`` key를 **0회**로 만든다.
    - 계약이 없거나 ``modelIdRequired``가 미확정(`None`)이거나 path가 쓸 수 없는
      구조면 ``None``을 반환한다 → 호출자는 상속받은 기본 구현(기존 동작과 바이트
      동일)에 위임한다.

    ``modelId``는 이 route의 **게이트웨이 전용 필드**이므로(기존 구현이 top-level에
    부착하고 게이트웨이가 소비한다) 중복 부착을 제거해도 백엔드 payload 의미가
    변하지 않는다.
    """
    if not isinstance(body, dict):
        return None
    contract = jobs_contract(binding)
    if contract is None:
        return None
    required = contract.get("modelIdRequired")
    if not isinstance(required, bool):
        return None  # 미확정 → baseline 위임(jobs route는 `UNVERIFIED`로 남는다)

    stripped = without_key(body, MODEL_ID_KEY)
    if not required:
        return stripped  # Requirement 8.12 — body 전체 0회

    field_path = contract.get("modelIdFieldPath")
    if not is_field_path(field_path):
        return None
    return write_once(stripped, field_path, invocation_model_id(binding, model_id))


# ═════════════════════════════════════════════════════════════════
# EffortBoundClient — 기존 GatewayClient의 builder seam만 오버라이드
# ═════════════════════════════════════════════════════════════════

#: base 인스턴스에서 그대로 복사하는 transport 설정(신규 URL·region을 만들지 않는다).
DELEGATED_BASE_ATTRS: tuple[str, ...] = ("gateway_url", "region", "aws_profile", "bedrock_user")

#: base 인스턴스에 위임하는 credential 메서드(5분 캐시·강제 갱신 단일화).
CREDENTIAL_DELEGATED_METHODS: tuple[str, ...] = (
    "_get_creds",
    "force_refresh_creds",
    "inject_credentials",
)

#: builder seam 오버라이드(여기서만 effort 주입·model ID 부착이 일어난다).
BUILDER_SEAM_METHODS: tuple[str, ...] = (
    "_build_payload",
    "_build_openai_payload",
    "_apply_jobs_model_id",
)

#: 허용 오버라이드의 **닫힌 목록**. 서명·retry·prefix 교정·job polling·응답 변환은
#: 상속 구현을 그대로 쓴다(:func:`unexpected_effort_bound_overrides`로 검증한다).
EFFORT_BOUND_OVERRIDES: tuple[str, ...] = tuple(
    sorted(("__init__",) + CREDENTIAL_DELEGATED_METHODS + BUILDER_SEAM_METHODS)
)

#: 클래스 dict의 메타데이터 이름(오버라이드 계산에서 제외).
_CLASS_METADATA_NAMES: frozenset[str] = frozenset(
    {"__doc__", "__module__", "__qualname__", "__dict__", "__weakref__", "__slots__"}
)

_UNSET: Any = object()
_EFFORT_BOUND_CLIENT: Any = _UNSET


def _build_effort_bound_client(base_cls: type) -> type:
    """``base_cls``(=`GatewayClient`)를 상속한 `EffortBoundClient`를 만든다.

    클래스를 지연 생성하는 이유는 capability 패키지가 **import 시점에 transport
    의존(boto3·httpx)을 만들지 않는다**는 규약 때문이다(:mod:`.failure_handler`와 동일).
    Request_Router만 쓰는 호출자는 gateway_module을 import하지 않는다.
    """

    class EffortBoundClient(base_cls):  # type: ignore[misc, valid-type]
        """요청 단위 Request_Builder — builder seam만 오버라이드한다.

        - credential 상태는 base 인스턴스에 위임한다 → 5분 캐시·강제 갱신 단일화
          (Requirement 10.4, 10.5, 10.6). 신규 credential 캐시를 만들지 않는다.
        - 서명(`execute-api`/`lambda`), retry, prefix 교정, job polling, 응답 변환은
          상속 구현 그대로다(Requirement 1.13, 10.1~10.3, 10.19).
        - effort 주입은 :meth:`_build_payload`·:meth:`_build_openai_payload`
          오버라이드에서만 일어난다.

        사용:

            client = effort_bound_client(gw, binding, selection)
            await client.converse(model_id, messages)   # 상속 구현 그대로

        ``close()``는 상속 구현을 그대로 쓴다. base의 credential 캐시는 건드리지
        않는다(요청 단위 wrapper가 공유 캐시를 무효화하지 않는다).
        """

        def __init__(self, base: Any, contract: Any = None, effort_selection: Any = None):
            """base 인스턴스의 transport 설정만 복사하고 계약·선택을 보관한다.

            Args:
                base: 기존 `GatewayClient` 인스턴스(credential 상태의 유일한 소유자).
                contract: Request_Binding(:func:`bind_contract` 결과) 또는 ``None``.
                effort_selection: effort selection dict 또는 ``None``(미선택).
            """
            for name in DELEGATED_BASE_ATTRS:
                setattr(self, name, getattr(base, name))
            self._base = base
            self._contract = contract
            self._effort = effort_selection

        # ── credential 위임 (신규 캐시 없음) ─────────────────────────
        def _get_creds(self):
            """base의 5분 캐시를 그대로 사용한다(Requirement 10.4, 10.5)."""
            return self._base._get_creds()

        def force_refresh_creds(self):
            """base의 강제 갱신 정책을 그대로 사용한다(Requirement 10.6)."""
            return self._base.force_refresh_creds()

        def inject_credentials(self, access_key: str, secret_key: str, session_token: str = ""):
            """runtime 주입도 base 인스턴스 하나에만 반영한다(Requirement 10.1)."""
            return self._base.inject_credentials(access_key, secret_key, session_token)

        # ── builder seam (여기서만 주입) ────────────────────────────
        def _build_payload(self, *args: Any, **kwargs: Any):
            """Converse·SSE baseline body + 조건부 effort 1회 주입."""
            return _inject_effort(super()._build_payload(*args, **kwargs), self._contract, self._effort)

        def _build_openai_payload(self, *args: Any, **kwargs: Any):
            """OpenAI Responses baseline body + 조건부 effort 1회 주입."""
            return _inject_effort(super()._build_openai_payload(*args, **kwargs), self._contract, self._effort)

        def _apply_jobs_model_id(self, body: Any, model_id: Any):
            """verified jobs 계약을 따른다. 계약 미확정이면 기본 구현에 위임한다."""
            applied = apply_jobs_model_id(body, model_id, self._contract)
            if applied is None:
                return super()._apply_jobs_model_id(body, model_id)
            return applied

    EffortBoundClient.__name__ = "EffortBoundClient"
    EffortBoundClient.__qualname__ = "EffortBoundClient"
    return EffortBoundClient


def effort_bound_client_class() -> type:
    """`EffortBoundClient` 클래스를 반환한다(첫 호출에서 생성·캐시).

    이 호출 시점에 ``ai_engine.gateway_module``을 import한다. 모듈 속성으로도
    접근할 수 있다(``request_builder.EffortBoundClient`` — PEP 562).
    """
    global _EFFORT_BOUND_CLIENT
    if _EFFORT_BOUND_CLIENT is _UNSET:
        try:
            from ..gateway_module import GatewayClient
        except ImportError:  # pragma: no cover - PyInstaller 평면 레이아웃 폴백
            from gateway_module import GatewayClient  # type: ignore[no-redef]
        _EFFORT_BOUND_CLIENT = _build_effort_bound_client(GatewayClient)
    return _EFFORT_BOUND_CLIENT


def effort_bound_client(base: Any, contract: Any = None, effort_selection: Any = None) -> Any:
    """요청 단위 `EffortBoundClient`를 만든다.

    ``contract``가 ``None``이거나 effort selection이 tuple과 어긋나면 생성 body는
    baseline과 동일하므로, 기존 경로와 바이트 수준으로 같은 요청이 나간다.
    """
    return effort_bound_client_class()(base, contract, effort_selection)


def effort_bound_overrides() -> tuple[str, ...]:
    """`EffortBoundClient`가 실제로 오버라이드한 이름(정렬)."""
    cls = effort_bound_client_class()
    base = cls.__mro__[1]
    names = []
    for name, value in vars(cls).items():
        if name in _CLASS_METADATA_NAMES:
            continue
        if not (callable(value) or isinstance(value, (property, staticmethod, classmethod))):
            continue  # 인터프리터가 넣는 메타데이터(문자열·tuple 등)는 제외
        if hasattr(base, name):
            names.append(name)
    return tuple(sorted(names))


def unexpected_effort_bound_overrides() -> tuple[str, ...]:
    """허용 목록(:data:`EFFORT_BOUND_OVERRIDES`) 밖의 오버라이드(없으면 빈 tuple)."""
    allowed = frozenset(EFFORT_BOUND_OVERRIDES)
    return tuple(name for name in effort_bound_overrides() if name not in allowed)


def __getattr__(name: str) -> Any:
    """``EffortBoundClient``를 지연 해석한다(PEP 562)."""
    if name == "EffortBoundClient":
        return effort_bound_client_class()
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


__all__ = [
    "BINDING_IDENTITY_FIELDS",
    "BUILDER_SEAM_METHODS",
    "CREDENTIAL_DELEGATED_METHODS",
    "DECISION_INPUT_FIELDS",
    "DECISION_REASONS",
    "DELEGATED_BASE_ATTRS",
    "EFFORT_BINDING_INCOMPLETE",
    "EFFORT_BOUND_OVERRIDES",
    "EFFORT_CONTRACT_INCOMPLETE",
    "EFFORT_CONTRACT_MISBOUND",
    "EFFORT_DOMAIN_VIOLATION",
    "EFFORT_FIELD_PATH_INVALID",
    "EFFORT_FINGERPRINT_MISMATCH",
    "EFFORT_MODEL_ID_MISMATCH",
    "EFFORT_NOT_SUPPORTED",
    "EFFORT_NO_SELECTION",
    "EFFORT_PATH_CONFLICT",
    "EFFORT_ROUTE_MISMATCH",
    "EFFORT_VALUE_TYPE_MISMATCH",
    "EffortBoundClient",
    "FALLBACK_MAX_STEPS",
    "INJECTION_REASONS",
    "MISSING",
    "MODEL_ID_KEY",
    "NON_INPUT_IDENTITY_FIELDS",
    "NO_ELIGIBLE_CONTRACT",
    "REASON_OK",
    "ROUTE_ALLOWLIST_NOT_ALLOWED",
    "ROUTE_CONTRACT_INCOMPLETE",
    "ROUTE_CONTRACT_KEY_MISMATCH",
    "ROUTE_CONTRACT_MISSING",
    "ROUTE_EVIDENCE_MISSING",
    "ROUTE_EXCLUDED",
    "ROUTE_NOT_SUPPORTED",
    "ROUTE_PURPOSE_UNMET",
    "ROUTE_UNKNOWN",
    "apply_jobs_model_id",
    "bind_contract",
    "binding_for",
    "contract_for_route",
    "count_key_occurrences",
    "decision_view",
    "effort_bound_client",
    "effort_bound_client_class",
    "effort_bound_overrides",
    "effort_injection_plan",
    "effort_view",
    "fallback_order",
    "fallback_route_keys",
    "inject_effort",
    "injection_reason",
    "invocation_model_id",
    "is_field_path",
    "jobs_contract",
    "may_transmit",
    "normalized_purposes",
    "path_exists",
    "path_writable",
    "route_block_reason",
    "select_contract",
    "select_fallback_contract",
    "select_route",
    "transmission_plan",
    "unexpected_effort_bound_overrides",
    "value_at_path",
    "without_key",
    "write_once",
]
