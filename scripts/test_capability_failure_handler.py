"""Unit — Failure_Handler의 분류 precedence·상태 보존·복구 계획·User_Notification.

Feature: gateway-models-effort-support (task 9.2)
대상: ai_engine.capability.failure_handler

검증 범위
  - 신호가 중첩된 모든 조합에서 Failure_Precedence 첫 일치 범주를 선택하는지
    (범주 전수 — 8개 Failure_Category 모두가 승자로 관찰된다) — Requirement 9.1
  - 상태 보존 불변식: `transient`·`unknown`·인증 실패(+`quota`·`request-validation`)에서
    Verification_Status·Route_Support_Status·Effort_Support_Status·파생 모드 상태·
    Editor_Model_Catalog 목록이 모두 불변 — Requirements 9.8, 9.9, 9.20, 10.16~10.18
  - effort-only mismatch: 해당 Effort_Contract만 `STALE`, base route·Verification 유지,
    무-effort Baseline_Request_Body 재시도가 정확히 1회 — Requirements 9.4, 9.5, 9.6, 9.7
  - fallback: Eligible_Contract만 후보, Fallback_Order 첫 항목만 사용, 후보 없으면
    오류 종료, transient는 기존 retry 한도 소진 후에만 fallback
    — Requirements 9.10, 9.11, 9.12, 9.13
  - User_Notification: 필드 화이트리스트(`modelId`·`route`·`category`·`retryCount`·
    `fallback`)와 credential·authorization·cookie·signature·raw body 부재
    — Requirement 9.19

이 테스트는 Gateway를 호출하지 않는다. 판정·기록 연결은 `FailureHooks` 주입으로만
대체하며, 통과 사실은 Gateway 지원 근거가 아니다(지원 주장은 작업 18의 production
path probe만이 확정한다).

fixture의 model ID·provider·effort field path·effort 값은 **의미 없는 합성 심볼**이다.
실제 값은 evidence만 채우므로 여기에 확정 상수를 두지 않는다.

실행: ai_engine/.venv/bin/python -m pytest scripts/test_capability_failure_handler.py -q
"""
from __future__ import annotations

import copy
import itertools
import json
import os
import sys

import pytest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from ai_engine.capability import canonicalizer  # noqa: E402
from ai_engine.capability import capability_map  # noqa: E402
from ai_engine.capability import contracts  # noqa: E402
from ai_engine.capability import failure_handler as fh  # noqa: E402
from ai_engine.capability import store  # noqa: E402

# ─────────────────────────────────────────────────────────────────
# 합성 심볼 — capability 주장이 아니라 자리표시자다
# ─────────────────────────────────────────────────────────────────
MODEL_ID = "model-sym-a1"
OTHER_MODEL_ID = "model-sym-b2"
PROVIDER = "provider-sym-p1"
CATALOG_FP = "catfp-sym-1"
REVISION = "rev-sym-1"
EVIDENCE_ID = "evidence-sym-1"
PURPOSE = "purpose-sym-1"
OTHER_PURPOSE = "purpose-sym-2"
VERIFIED_AT = "2026-08-03T00:00:00.000000Z"
EFFORT_FIELD_PATH = ("fieldseg-sym-1", "fieldseg-sym-2")
EFFORT_ENUM_VALUES = ("effortvalue-sym-1", "effortvalue-sym-2")

ROUTES = contracts.KNOWN_ROUTES
SUPPORTED = str(contracts.Route_Support_Status.SUPPORTED)
UNSUPPORTED = str(contracts.Route_Support_Status.UNSUPPORTED)
ROUTE_UNVERIFIED = str(contracts.Route_Support_Status.UNVERIFIED)
ALLOWED = str(contracts.Allowlist_Result.ALLOWED)
ALLOWLIST_REJECTED = str(contracts.Allowlist_Result.REJECTED)
EFFORT_SUPPORTED = str(contracts.Effort_Support_Status.SUPPORTED)
EFFORT_STALE = str(contracts.Effort_Support_Status.STALE)
EFFORT_UNVERIFIED = str(contracts.Effort_Support_Status.UNVERIFIED)


# ─────────────────────────────────────────────────────────────────
# 훅 — 기존 구현 대신 결정론적 stub을 주입한다(Gateway·server 의존 없음)
# ─────────────────────────────────────────────────────────────────
class DeniedRecorder:
    """`_record_denied_model` 대체 — 호출 여부와 인수를 기록한다."""

    def __init__(self) -> None:
        self.calls: list[str] = []

    def __call__(self, model_id: str) -> None:
        self.calls.append(model_id)


def silent_hooks(recorder: DeniedRecorder | None = None) -> fh.FailureHooks:
    """문자열 추론을 모두 끈 훅 — 명시 boolean 신호만 판정에 쓰이게 한다."""
    return fh.FailureHooks(
        is_expired_error=lambda _text: False,
        looks_unsupported_model=lambda _status, _text: False,
        is_prefix_form_error=lambda _text: False,
        record_denied_model=recorder or DeniedRecorder(),
        extract_denied_model=lambda _text: "",
        normalize_model_key=lambda model_id: model_id.lower(),
    )


# design.md "Failure_Precedence 기반 분류표"의 순위 — 구현이 아니라 설계가 기준이다.
# 판정 oracle을 fh.PRECEDENCE로 두면 순서를 바꿔도 테스트가 통과하므로 여기에 못박는다.
DESIGN_PRECEDENCE: tuple[str, ...] = (
    "authentication",
    "allowlist",
    "effort-mismatch",
    "route-capability-mismatch",
    "quota",
    "transient",
    "request-validation",
    "unknown",
)

# 범주별 최소 신호 — 각 신호는 자기 범주만 활성화한다(명시 boolean 신호).
CATEGORY_SIGNALS: dict[str, dict] = {
    fh.AUTHENTICATION: {"credentialFailure": True},
    fh.ALLOWLIST: {"allowlistDenied": True},
    fh.EFFORT_MISMATCH: {"effortInjected": True, "effortFieldRejected": True},
    fh.ROUTE_CAPABILITY_MISMATCH: {"routeRejected": True},
    fh.QUOTA: {"quotaExceeded": True},
    fh.TRANSIENT: {"timeout": True},
    fh.REQUEST_VALIDATION: {"requestValidation": True},
}
SIGNAL_CATEGORIES: tuple[str, ...] = tuple(
    category for category in DESIGN_PRECEDENCE if category in CATEGORY_SIGNALS
)


# ─────────────────────────────────────────────────────────────────
# entry fixture 빌더
# ─────────────────────────────────────────────────────────────────
def _execution_mode(route_key: str) -> str:
    """route에 execution mode를 결정론적으로 배정한다(실제 매핑은 evidence가 정한다)."""
    modes = contracts.Execution_Mode.values()
    return modes[ROUTES.index(route_key) % len(modes)]


def _signing_service(route_key: str) -> str:
    """route에 signing service를 결정론적으로 배정한다(실제 값은 baseline이 정한다)."""
    services = contracts.Signing_Service.values()
    return services[ROUTES.index(route_key) % len(services)]


def route_contract(
    route_key: str,
    *,
    rank: int = 0,
    purposes: tuple[str, ...] = (PURPOSE,),
    complete: bool = True,
) -> dict:
    """완전한 Route_Contract(또는 `complete=False`면 결손 계약)를 만든다."""
    contract = contracts.new_route_contract(
        route_key,
        endpoint_ref=f"endpointref-sym-{route_key.lower()}",
        http_method="POST",
        execution_mode=_execution_mode(route_key),
        signing_service=_signing_service(route_key),
        model_id_required=False,
        message_field_path=["msgseg-sym-1"],
        inference_config_field_path=["cfgseg-sym-1"],
        output_validator_ref=f"validatorref-sym-{route_key.lower()}",
        terminal_condition_ref=f"terminalref-sym-{route_key.lower()}",
        retry_policy_ref=f"retryref-sym-{route_key.lower()}",
        fallback_rank=rank,
        purposes=purposes,
        min_output_bound={"boundkey-sym-1": 1},
        evidence_ref=EVIDENCE_ID,
    )
    if not complete:
        contract["minOutputBound"] = {}  # 계약 결손 → Eligible_Contract 실격
    return contract


def effort_contract(route_key: str, *, model_id: str = MODEL_ID) -> dict:
    """완전한 Effort_Contract(ENUM domain)를 만든다."""
    return contracts.new_effort_contract(
        model_id,
        route_key,
        field_path=list(EFFORT_FIELD_PATH),
        value_type=str(contracts.Value_Type.STRING),
        domain_kind=str(contracts.Domain_Kind.ENUM),
        enum_values=list(EFFORT_ENUM_VALUES),
        verified_values=[EFFORT_ENUM_VALUES[0]],
        evidence_ref=EVIDENCE_ID,
    )


def build_entry(
    spec: dict[str, dict] | None = None,
    *,
    model_id: str = MODEL_ID,
    verification: str = str(contracts.Verification_Status.VERIFIED),
    revision: str = REVISION,
    catalog_fingerprint: str = CATALOG_FP,
) -> dict:
    """spec에 기술된 route만 채운 Capability_Map entry를 만든다.

    spec의 각 값은 ``status``·``allowlist``·``rank``·``purposes``·``effort``·
    ``complete``·``evidence`` 키를 받는다. spec에 없는 route는 미확정
    (`UNVERIFIED` + 계약 `None`)으로 남는다 — 값 추론 금지.
    """
    entry = contracts.new_entry("candidatelabel-sym-1", revision=revision)
    entry["modelId"] = model_id
    entry["provider"] = PROVIDER
    entry["catalogFingerprint"] = catalog_fingerprint
    entry["verifiedAt"] = VERIFIED_AT
    entry["evidence"] = [EVIDENCE_ID]
    entry["verificationStatus"] = verification

    for route_key, options in (spec or {}).items():
        has_evidence = options.get("evidence", True)
        contract = route_contract(
            route_key,
            rank=options.get("rank", 0),
            purposes=tuple(options.get("purposes", (PURPOSE,))),
            complete=options.get("complete", True),
        )
        if not has_evidence:
            # Current_Evidence reference 결손 — route·계약 양쪽 모두 비운다.
            contract["evidenceRef"] = None
        entry["routes"][route_key] = {
            "status": options.get("status", SUPPORTED),
            "allowlist": options.get("allowlist", ALLOWED),
            "contract": contract,
            "evidenceRef": EVIDENCE_ID if has_evidence else None,
        }
        effort_status = options.get("effort", EFFORT_UNVERIFIED)
        entry["effort"][route_key] = {
            "status": effort_status,
            "contract": effort_contract(route_key, model_id=model_id),
            "evidenceRef": EVIDENCE_ID,
        }

    capability_map.apply_mode_support(entry)
    capability_map.recompute_fingerprint(entry)
    return entry


def state_view(entry: dict) -> dict:
    """상태 보존 불변식 비교용 view — 상태 필드만 뽑는다."""
    return {
        "verificationStatus": entry["verificationStatus"],
        "routes": {
            route: (entry["routes"][route]["status"], entry["routes"][route]["allowlist"])
            for route in ROUTES
        },
        "effort": {route: entry["effort"][route]["status"] for route in ROUTES},
        "modes": (
            entry["syncSupport"],
            entry["asyncSupport"],
            entry["streamingSupport"],
        ),
        "capabilityFingerprint": entry["capabilityFingerprint"],
    }


def rich_entry() -> dict:
    """여러 route가 `SUPPORTED`/effort `SUPPORTED`인 검증된 entry."""
    return build_entry(
        {
            ROUTES[0]: {"rank": 2, "effort": EFFORT_SUPPORTED},
            ROUTES[1]: {"rank": 1, "effort": EFFORT_SUPPORTED},
            ROUTES[2]: {"rank": 5, "effort": EFFORT_UNVERIFIED},
        }
    )


def base_ctx(entry: dict, route_key: str, **extra) -> dict:
    """전이·복구·알림 공통 ctx."""
    ctx = {
        "modelId": entry["modelId"],
        "route": route_key,
        "purpose": PURPOSE,
        "revision": entry["revision"],
        "catalogFingerprint": entry["catalogFingerprint"],
        "catalogModelIds": [entry["modelId"], OTHER_MODEL_ID],
    }
    ctx.update(extra)
    return ctx


# ─────────────────────────────────────────────────────────────────
# fixture 자체가 Malformed가 아님을 먼저 확인한다
# ─────────────────────────────────────────────────────────────────
def test_fixture_entry_is_well_formed_and_complete():
    entry = rich_entry()
    reasons = contracts.validate_entry(
        entry,
        fingerprint_fn=canonicalizer.capability_fingerprint,
        known_evidence_ids=[EVIDENCE_ID],
    )
    assert reasons == [], reasons
    assert capability_map.is_complete_record(entry, known_evidence_ids=[EVIDENCE_ID])
    assert capability_map.supported_routes(entry) == [ROUTES[0], ROUTES[1], ROUTES[2]]


# ─────────────────────────────────────────────────────────────────
# 1) Failure_Precedence — 첫 일치 범주 (Requirement 9.1)
# ─────────────────────────────────────────────────────────────────
def test_precedence_matches_design_table_and_covers_enum():
    assert fh.PRECEDENCE == DESIGN_PRECEDENCE
    assert set(DESIGN_PRECEDENCE) == set(contracts.Failure_Category.values())
    assert fh.PRECEDENCE_COVERS_ENUM is True


@pytest.mark.parametrize("category", SIGNAL_CATEGORIES)
def test_single_signal_selects_its_own_category(category):
    assert fh.classify(CATEGORY_SIGNALS[category], hooks=silent_hooks()) == category


def test_empty_signals_classify_as_unknown():
    assert fh.classify({}, hooks=silent_hooks()) == fh.UNKNOWN
    assert fh.classify(None, hooks=silent_hooks()) == fh.UNKNOWN


def test_overlapping_signals_select_first_precedence_match():
    """신호 조합 전수(2^7 = 128)에서 항상 PRECEDENCE 첫 일치 범주를 고른다."""
    hooks = silent_hooks()
    winners: set[str] = set()
    for size in range(len(SIGNAL_CATEGORIES) + 1):
        for combination in itertools.combinations(SIGNAL_CATEGORIES, size):
            signals: dict = {}
            for category in combination:
                signals.update(CATEGORY_SIGNALS[category])
            expected = next(
                (item for item in DESIGN_PRECEDENCE if item in combination), fh.UNKNOWN
            )
            assert fh.classify(signals, hooks=hooks) == expected, (combination, signals)
            winners.add(expected)
    # 범주 전수: 8개 Failure_Category가 모두 한 번 이상 승자로 관찰된다.
    assert winners == set(DESIGN_PRECEDENCE)


@pytest.mark.parametrize(
    "signals, expected",
    [
        # 기존 `_is_expired_error` 문자열 신호 → authentication(1순위)
        ({"errorText": "The security token included in the request is expired"}, fh.AUTHENTICATION),
        ({"httpStatus": 401, "errorText": "denied"}, fh.AUTHENTICATION),
        # `not in allowed list`는 allowlist·route mismatch·prefix 교정 신호에 동시에
        # 걸리지만 allowlist(2순위)가 이긴다.
        ({"errorText": "model_denied: xyz not in allowed list", "httpStatus": 422}, fh.ALLOWLIST),
        # effort field 지목 + unknown parameter → effort-mismatch(3순위)
        (
            {
                "effortInjected": True,
                "effortFieldPath": list(EFFORT_FIELD_PATH),
                "errorText": f"unknown parameter: {EFFORT_FIELD_PATH[0]}",
                "httpStatus": 400,
            },
            fh.EFFORT_MISMATCH,
        ),
        # effort 주입이 없으면 같은 문구도 effort 범주가 아니다(교정 가능 validation).
        (
            {"errorText": f"unknown parameter: {EFFORT_FIELD_PATH[0]}", "httpStatus": 400},
            fh.REQUEST_VALIDATION,
        ),
        ({"errorText": "unknown model for this route"}, fh.ROUTE_CAPABILITY_MISMATCH),
        ({"errorType": "OpenAIModelUnsupported"}, fh.ROUTE_CAPABILITY_MISMATCH),
        ({"errorType": "QuotaExceededError"}, fh.QUOTA),
        ({"httpStatus": 403}, fh.QUOTA),
        ({"errorType": "SyncTimeout"}, fh.TRANSIENT),
        ({"errorType": "JobTimeout"}, fh.TRANSIENT),
        ({"httpStatus": 429}, fh.TRANSIENT),
        ({"httpStatus": 503}, fh.TRANSIENT),
        ({"httpStatus": -1, "errorText": "connection reset by peer"}, fh.TRANSIENT),
        ({"errorText": "partial output received"}, fh.TRANSIENT),
        ({"errorType": "OpenAISurfaceError"}, fh.REQUEST_VALIDATION),
        ({"errorText": "ValidationException: field is required"}, fh.REQUEST_VALIDATION),
        ({"errorText": "no idea what happened here"}, fh.UNKNOWN),
    ],
)
def test_existing_symbol_signals_classify_with_default_hooks(signals, expected):
    """기존 심볼(문자열·예외 타입) 신호가 기본 훅으로 같은 범주를 만든다."""
    assert fh.classify(signals) == expected


# ─────────────────────────────────────────────────────────────────
# 2) 상태 보존 불변식 (Requirements 9.8, 9.9, 9.20, 10.16~10.18)
# ─────────────────────────────────────────────────────────────────
@pytest.mark.parametrize("category", fh.STATE_PRESERVING_CATEGORIES)
def test_state_preserving_categories_change_nothing(category):
    entry = rich_entry()
    before = copy.deepcopy(entry)
    recorder = DeniedRecorder()
    ctx = base_ctx(
        entry,
        ROUTES[0],
        errorText="apitoken=secretvalue-sym-1 cause text",
        retryCount=2,
    )

    result = fh.apply(entry, category, ctx, hooks=silent_hooks(recorder))

    assert result["category"] == category
    assert result["transitions"] == []
    assert result["changed"] is False
    assert result["removeFromCatalog"] is False
    assert result["catalogModelIds"] is None  # 목록 필터를 만들지 않는다
    assert result["deniedModelIds"] == []
    assert result["deniedRecorded"] is False
    assert result["fingerprintRecomputed"] is False
    assert recorder.calls == []  # denylist 등록 부작용 없음

    # Verification / Route / Effort / 파생 모드 / fingerprint 전부 불변
    assert state_view(result["entry"]) == state_view(before)
    assert canonicalizer.canonical_equal(before, result["entry"])
    assert canonicalizer.serialize(result["entry"]) == canonicalizer.serialize(before)
    # 입력 entry도 변형되지 않는다(순수 함수).
    assert entry == before


@pytest.mark.parametrize("category", [fh.TRANSIENT, fh.UNKNOWN, fh.AUTHENTICATION])
def test_state_preserving_categories_keep_unverified_states(category):
    """`UNVERIFIED`는 `UNVERIFIED`로 유지된다(강등도 승격도 없음)."""
    entry = build_entry(
        {
            ROUTES[0]: {"status": ROUTE_UNVERIFIED, "effort": EFFORT_UNVERIFIED},
            ROUTES[1]: {"status": SUPPORTED, "effort": EFFORT_SUPPORTED, "rank": 1},
        },
        verification=str(contracts.Verification_Status.UNVERIFIED),
    )
    before = copy.deepcopy(entry)
    result = fh.apply(entry, category, base_ctx(entry, ROUTES[0]), hooks=silent_hooks())
    assert state_view(result["entry"]) == state_view(before)
    assert result["entry"]["routes"][ROUTES[0]]["status"] == ROUTE_UNVERIFIED
    assert result["entry"]["effort"][ROUTES[1]]["status"] == EFFORT_SUPPORTED


def test_out_of_enum_category_is_reduced_to_unknown_without_transition():
    entry = rich_entry()
    before = copy.deepcopy(entry)
    result = fh.apply(entry, "not-a-real-category", base_ctx(entry, ROUTES[0]), hooks=silent_hooks())
    assert result["category"] == fh.UNKNOWN
    assert result["transitions"] == []
    assert canonicalizer.canonical_equal(before, result["entry"])


@pytest.mark.parametrize("category", [fh.QUOTA, fh.UNKNOWN])
def test_quota_and_unknown_have_no_retry_and_no_fallback(category):
    entry = rich_entry()
    plan = fh.plan_recovery(entry, category, base_ctx(entry, ROUTES[0]), hooks=silent_hooks())
    assert plan["retryWithoutEffort"] is False
    assert plan["retryTransient"] is False
    assert plan["retryWithCorrection"] is False
    assert plan["fallbackContract"] is None
    assert plan["terminate"] is True
    assert plan["reasonCode"] == fh.RECOVERY_NO_RECOVERY


@pytest.mark.parametrize("category", [category for category in fh.PRECEDENCE if category != fh.EFFORT_MISMATCH])
def test_only_effort_mismatch_plans_a_no_effort_retry(category):
    """무-effort 재시도는 effort-only mismatch에서만 계획된다(9.7)."""
    entry = rich_entry()
    plan = fh.plan_recovery(entry, category, base_ctx(entry, ROUTES[0]), hooks=silent_hooks())
    assert plan["retryWithoutEffort"] is False


def test_authentication_delegates_to_existing_refresh_policy():
    """인증 실패는 기존 강제 갱신 정책에 위임하고 자체 retry·fallback을 만들지 않는다."""
    entry = rich_entry()
    plan = fh.plan_recovery(
        entry, fh.AUTHENTICATION, base_ctx(entry, ROUTES[0]), hooks=silent_hooks()
    )
    assert plan["retryTransient"] is False
    assert plan["retryWithCorrection"] is False
    assert plan["fallbackContract"] is None
    assert plan["terminate"] is False
    assert plan["retryLimit"] == fh.AUTH_MAX_REFRESH_ATTEMPTS == 3
    assert plan["reasonCode"] == fh.RECOVERY_AUTH_REFRESH_DELEGATED


def test_authentication_terminates_after_existing_refresh_budget():
    entry = rich_entry()
    ctx = base_ctx(entry, ROUTES[0], authRetryCount=fh.AUTH_MAX_REFRESH_ATTEMPTS)
    plan = fh.plan_recovery(entry, fh.AUTHENTICATION, ctx, hooks=silent_hooks())
    assert plan["terminate"] is True
    assert plan["reasonCode"] == fh.RECOVERY_AUTH_REFRESH_EXHAUSTED
    assert plan["fallbackContract"] is None


# ─────────────────────────────────────────────────────────────────
# 3) effort-only mismatch (Requirements 9.4, 9.5, 9.6, 9.7)
# ─────────────────────────────────────────────────────────────────
def test_effort_mismatch_marks_only_that_effort_contract_stale():
    entry = rich_entry()
    before = copy.deepcopy(entry)
    failed = ROUTES[0]
    recorder = DeniedRecorder()

    result = fh.apply(
        entry, fh.EFFORT_MISMATCH, base_ctx(entry, failed), hooks=silent_hooks(recorder)
    )
    updated = result["entry"]

    assert result["transitions"] == [fh.TRANSITION_EFFORT_CONTRACT_STALE]
    assert updated["effort"][failed]["status"] == EFFORT_STALE
    # 나머지 effort·route·Verification·목록은 불변 (9.6)
    assert updated["effort"][ROUTES[1]]["status"] == before["effort"][ROUTES[1]]["status"]
    assert updated["effort"][ROUTES[2]]["status"] == before["effort"][ROUTES[2]]["status"]
    for route in ROUTES:
        assert updated["routes"][route]["status"] == before["routes"][route]["status"]
        assert updated["routes"][route]["allowlist"] == before["routes"][route]["allowlist"]
    assert updated["verificationStatus"] == before["verificationStatus"]
    assert result["removeFromCatalog"] is False
    assert result["catalogModelIds"] is None
    assert recorder.calls == []
    # 계약 자체(field path·domain)는 보존된다 — STALE은 재검증 필요 표시일 뿐이다.
    assert updated["effort"][failed]["contract"] == before["effort"][failed]["contract"]
    # 전이가 있었으므로 fingerprint를 재계산해 Complete_Record를 유지한다.
    assert result["fingerprintRecomputed"] is True
    assert updated["capabilityFingerprint"] == canonicalizer.capability_fingerprint(updated)
    assert capability_map.is_complete_record(updated, known_evidence_ids=[EVIDENCE_ID])


def test_effort_mismatch_retry_without_effort_happens_exactly_once():
    entry = rich_entry()
    failed = ROUTES[0]
    applied = fh.apply(entry, fh.EFFORT_MISMATCH, base_ctx(entry, failed), hooks=silent_hooks())
    updated = applied["entry"]

    first = fh.plan_recovery(
        updated, fh.EFFORT_MISMATCH, base_ctx(entry, failed), hooks=silent_hooks()
    )
    assert first["retryWithoutEffort"] is True
    assert first["retryLimit"] == fh.EFFORT_BASELINE_MAX_RETRIES == 1
    assert first["retryCount"] == 0
    assert first["terminate"] is False
    assert first["fallbackContract"] is None  # effort 재시도는 fallback을 쓰지 않는다
    assert first["reasonCode"] == fh.RECOVERY_EFFORT_BASELINE_RETRY

    for used_ctx in (
        base_ctx(entry, failed, retryWithoutEffortUsed=True),
        base_ctx(entry, failed, effortRetryCount=1),
    ):
        second = fh.plan_recovery(updated, fh.EFFORT_MISMATCH, used_ctx, hooks=silent_hooks())
        assert second["retryWithoutEffort"] is False  # 두 번째 재시도는 없다
        assert second["terminate"] is True
        assert second["fallbackContract"] is None
        assert second["retryCount"] == fh.EFFORT_BASELINE_MAX_RETRIES
        assert second["reasonCode"] == fh.RECOVERY_EFFORT_BASELINE_USED


def test_effort_mismatch_terminates_when_base_route_is_not_supported():
    entry = build_entry({ROUTES[0]: {"status": UNSUPPORTED, "effort": EFFORT_SUPPORTED}})
    plan = fh.plan_recovery(
        entry, fh.EFFORT_MISMATCH, base_ctx(entry, ROUTES[0]), hooks=silent_hooks()
    )
    assert plan["retryWithoutEffort"] is False
    assert plan["terminate"] is True
    assert plan["reasonCode"] == fh.RECOVERY_EFFORT_BASE_ROUTE_NOT_SUPPORTED


def test_route_capability_mismatch_marks_route_unsupported_and_its_effort_stale():
    entry = rich_entry()
    before = copy.deepcopy(entry)
    failed = ROUTES[0]
    result = fh.apply(
        entry, fh.ROUTE_CAPABILITY_MISMATCH, base_ctx(entry, failed), hooks=silent_hooks()
    )
    updated = result["entry"]

    assert result["transitions"] == [
        fh.TRANSITION_ROUTE_UNSUPPORTED,
        fh.TRANSITION_EFFORT_CONTRACT_STALE,
    ]
    assert updated["routes"][failed]["status"] == UNSUPPORTED
    assert updated["effort"][failed]["status"] == EFFORT_STALE
    # 다른 `SUPPORTED` route가 남아 있으므로 entry와 목록은 유지된다.
    assert updated["verificationStatus"] == before["verificationStatus"]
    assert updated["routes"][ROUTES[1]]["status"] == SUPPORTED
    assert result["removeFromCatalog"] is False
    assert result["catalogModelIds"] is None


def test_route_capability_mismatch_removes_model_when_no_eligible_contract_remains():
    entry = build_entry({ROUTES[0]: {"effort": EFFORT_SUPPORTED}})
    ctx = base_ctx(entry, ROUTES[0])
    result = fh.apply(entry, fh.ROUTE_CAPABILITY_MISMATCH, ctx, hooks=silent_hooks())

    assert fh.TRANSITION_CATALOG_MODEL_REMOVED in result["transitions"]
    assert result["removeFromCatalog"] is True
    assert result["catalogModelIds"] == [OTHER_MODEL_ID]


# ─────────────────────────────────────────────────────────────────
# 4) fallback — Eligible_Contract 첫 항목만 (Requirements 9.11, 9.12, 9.13)
# ─────────────────────────────────────────────────────────────────
def test_eligible_contracts_are_ordered_by_fallback_rank():
    entry = build_entry(
        {ROUTES[0]: {"rank": 7}, ROUTES[1]: {"rank": 2}, ROUTES[2]: {"rank": 4}}
    )
    ranks = [
        contract["fallbackRank"]
        for contract in fh.eligible_contracts(entry, purpose=PURPOSE, ctx={})
    ]
    assert ranks == sorted(ranks) == [2, 4, 7]


@pytest.mark.parametrize(
    "category, extra_ctx",
    [
        (fh.ALLOWLIST, {}),
        (fh.ROUTE_CAPABILITY_MISMATCH, {}),
        (fh.TRANSIENT, {"transientRetryCount": fh.TRANSIENT_MAX_RETRIES}),
    ],
)
def test_fallback_uses_only_first_eligible_contract(category, extra_ctx):
    entry = build_entry(
        {ROUTES[0]: {"rank": 1}, ROUTES[1]: {"rank": 3}, ROUTES[2]: {"rank": 2}}
    )
    failed = ROUTES[0]
    ctx = base_ctx(entry, failed, **extra_ctx)
    applied = fh.apply(entry, category, ctx, hooks=silent_hooks())
    plan = fh.plan_recovery(applied["entry"], category, ctx, hooks=silent_hooks())

    remaining = fh.eligible_contracts(
        applied["entry"], purpose=PURPOSE, ctx=ctx, exclude_routes=(failed,)
    )
    assert plan["fallbackContract"] is not None
    assert plan["terminate"] is False
    assert plan["reasonCode"] == fh.RECOVERY_FALLBACK_SELECTED
    # 첫 항목만 사용한다(연쇄 이동 금지) — 후보 목록의 두 번째는 쓰이지 않는다.
    assert plan["fallbackContract"] == remaining[0]
    assert plan["fallbackContract"]["routeKey"] == ROUTES[2]  # rank 2 < rank 3
    assert plan["fallbackContract"]["routeKey"] != failed
    assert isinstance(plan["fallbackContract"], dict)


@pytest.mark.parametrize(
    "disqualifier",
    ["status", "allowlist", "evidence", "contract", "purpose", "revision"],
)
def test_fallback_candidates_exclude_non_eligible_routes(disqualifier):
    """미지원·거부·근거 결손·계약 결손·목적 불일치·구식 evidence는 후보가 아니다."""
    alternate: dict = {"rank": 1}
    ctx_overrides: dict = {}
    if disqualifier == "status":
        alternate["status"] = UNSUPPORTED
    elif disqualifier == "allowlist":
        alternate["allowlist"] = ALLOWLIST_REJECTED
    elif disqualifier == "evidence":
        alternate["evidence"] = False
    elif disqualifier == "contract":
        alternate["complete"] = False
    elif disqualifier == "purpose":
        alternate["purposes"] = (OTHER_PURPOSE,)
    elif disqualifier == "revision":
        ctx_overrides["revision"] = "rev-sym-other"

    entry = build_entry({ROUTES[0]: {"rank": 9}, ROUTES[1]: alternate})
    failed = ROUTES[0]
    ctx = base_ctx(entry, failed, **ctx_overrides)

    assert fh.is_eligible_contract(entry, ROUTES[1], purpose=PURPOSE, ctx=ctx) is False
    assert (
        fh.first_eligible_contract(entry, purpose=PURPOSE, ctx=ctx, exclude_routes=(failed,))
        is None
    )

    plan = fh.plan_recovery(entry, fh.ROUTE_CAPABILITY_MISMATCH, ctx, hooks=silent_hooks())
    assert plan["fallbackContract"] is None
    assert plan["terminate"] is True  # 후보 없으면 오류 상태로 종료 (9.13)
    assert plan["reasonCode"] == fh.RECOVERY_NO_ELIGIBLE_CONTRACT


def test_transient_retries_within_existing_budget_then_falls_back():
    entry = build_entry({ROUTES[0]: {"rank": 1}, ROUTES[1]: {"rank": 2}})
    failed = ROUTES[0]

    for used in range(fh.TRANSIENT_MAX_RETRIES):
        ctx = base_ctx(entry, failed, transientRetryCount=used)
        plan = fh.plan_recovery(entry, fh.TRANSIENT, ctx, hooks=silent_hooks())
        assert plan["retryTransient"] is True
        assert plan["fallbackContract"] is None  # 한도 소진 전에는 fallback 없음
        assert plan["terminate"] is False
        assert plan["retryLimit"] == fh.TRANSIENT_MAX_RETRIES
        assert plan["retryDelaySeconds"] == fh.TRANSIENT_BACKOFF_SECONDS[used]
        assert plan["reasonCode"] == fh.RECOVERY_TRANSIENT_RETRY

    exhausted = base_ctx(entry, failed, transientRetryCount=fh.TRANSIENT_MAX_RETRIES)
    plan = fh.plan_recovery(entry, fh.TRANSIENT, exhausted, hooks=silent_hooks())
    assert plan["retryTransient"] is False
    assert plan["fallbackContract"] is not None
    assert plan["fallbackContract"]["routeKey"] == ROUTES[1]
    assert plan["terminate"] is False


def test_transient_terminates_when_no_fallback_candidate_exists():
    entry = build_entry({ROUTES[0]: {"rank": 1}})
    ctx = base_ctx(entry, ROUTES[0], transientRetryCount=fh.TRANSIENT_MAX_RETRIES)
    plan = fh.plan_recovery(entry, fh.TRANSIENT, ctx, hooks=silent_hooks())
    assert plan["fallbackContract"] is None
    assert plan["terminate"] is True
    assert plan["reasonCode"] == fh.RECOVERY_NO_ELIGIBLE_CONTRACT


def test_request_validation_correction_retry_is_capped_at_one():
    entry = rich_entry()
    first = fh.plan_recovery(
        entry, fh.REQUEST_VALIDATION, base_ctx(entry, ROUTES[0]), hooks=silent_hooks()
    )
    assert first["retryWithCorrection"] is True
    assert first["retryLimit"] == fh.CORRECTION_MAX_RETRIES == 1
    assert first["terminate"] is False

    used = base_ctx(entry, ROUTES[0], correctionRetryCount=1)
    second = fh.plan_recovery(entry, fh.REQUEST_VALIDATION, used, hooks=silent_hooks())
    assert second["retryWithCorrection"] is False
    assert second["terminate"] is True
    assert second["fallbackContract"] is None
    assert second["reasonCode"] == fh.RECOVERY_CORRECTION_USED


# ─────────────────────────────────────────────────────────────────
# 5) User_Notification (Requirements 9.14~9.19)
# ─────────────────────────────────────────────────────────────────
SECRET_VALUES = {
    "authorization": "Bearer-secretsym-1",
    "cookie": "session-secretsym-2",
    "signature": "sigv4-secretsym-3",
    "credential": "cred-secretsym-4",
    "apitoken": "apitoken-secretsym-5",
    "awsSecretAccessKey": "awssecret-secretsym-6",
}


def notification_ctx(**extra) -> dict:
    ctx = {
        "modelId": MODEL_ID,
        "route": ROUTES[0],
        "category": fh.TRANSIENT,
        "retryCount": 2,
        # 아래 값들은 화이트리스트 밖이므로 알림에 실릴 수 없어야 한다.
        "errorText": " ".join(f"{key}={value}" for key, value in SECRET_VALUES.items()),
        "requestBody": {"prompt": "raw prompt text", "messages": ["raw message"]},
        "responseBody": {"raw": "raw response body"},
    }
    ctx.update(SECRET_VALUES)
    ctx.update(extra)
    return ctx


def test_notification_contains_only_whitelisted_fields():
    payload = fh.notification(notification_ctx())
    assert set(payload) == set(fh.NOTIFICATION_FIELDS)
    assert payload["modelId"] == MODEL_ID  # 라벨이 아니라 원 Exact_Model_ID (9.14)
    assert payload["route"] == ROUTES[0]  # 원 Known_Route (9.15)
    assert payload["category"] == fh.TRANSIENT  # Failure_Category (9.16)
    assert payload["retryCount"] == 2  # 수행한 retry 횟수 (9.17)
    assert payload["fallback"] == fh.NO_FALLBACK  # fallback 미수행 표시 (9.18)


def test_notification_excludes_credential_authorization_cookie_signature_and_raw_body():
    payload = fh.notification(notification_ctx())
    rendered = json.dumps(payload, ensure_ascii=False)
    for key, value in SECRET_VALUES.items():
        assert key not in payload
        assert value not in rendered
    assert "raw prompt text" not in rendered
    assert "raw message" not in rendered
    assert "raw response body" not in rendered
    # 화이트리스트 키 중 비밀 분류로 판정되는 것이 없다.
    assert all(store.classify_key(key) == "KEEP" for key in payload)


def test_notification_reports_fallback_model_and_route_when_used():
    contract = route_contract(ROUTES[1], rank=1)
    payload = fh.notification(notification_ctx(fallbackContract=contract))
    assert payload["fallback"] == {"modelId": MODEL_ID, "route": ROUTES[1]}

    explicit = fh.notification(
        notification_ctx(fallback={"modelId": OTHER_MODEL_ID, "route": ROUTES[2]})
    )
    assert explicit["fallback"] == {"modelId": OTHER_MODEL_ID, "route": ROUTES[2]}


def test_notification_retry_count_includes_the_no_effort_retry():
    payload = fh.notification(
        {
            "modelId": MODEL_ID,
            "route": ROUTES[0],
            "category": fh.EFFORT_MISMATCH,
            "retryWithoutEffortUsed": True,
        }
    )
    assert payload["retryCount"] == 1
    assert payload["fallback"] == fh.NO_FALLBACK

    summed = fh.notification(
        {
            "modelId": MODEL_ID,
            "route": ROUTES[0],
            "category": fh.TRANSIENT,
            "transientRetryCount": 2,
            "correctionRetryCount": 1,
        }
    )
    assert summed["retryCount"] == 3


def test_notification_out_of_enum_inputs_degrade_safely():
    payload = fh.notification({"category": "made-up", "route": "MADE_UP_ROUTE"})
    assert payload["category"] == fh.UNKNOWN
    assert payload["route"] == contracts.UNDETERMINED
    assert payload["modelId"] == ""
    assert payload["retryCount"] == 0


def test_notification_cause_is_masked_and_truncated_when_requested():
    secret = "apitoken-secretsym-5"
    long_text = f"apitoken={secret} " + ("x" * 400)
    payload = fh.notification(
        notification_ctx(errorText=long_text), include_cause=True
    )
    assert set(payload) == set(fh.NOTIFICATION_FIELDS) | {"reason"}
    assert len(payload["reason"]) == fh.CAUSE_MAX_LENGTH == 200
    assert secret not in payload["reason"]
    assert store.mask_token_for_log(secret) in payload["reason"]


def test_safe_cause_keeps_non_secret_assignments_readable():
    cause = fh.safe_cause(f"modelId={MODEL_ID} route={ROUTES[0]} apitoken=secretsym-9")
    assert MODEL_ID in cause
    assert ROUTES[0] in cause
    assert "secretsym-9" not in cause
