"""Request_Router 단위 테스트.

Feature: gateway-models-effort-support (task 7.2)
대상: ai_engine.capability.request_builder (Request_Router 부분)

검증 범위
  - Fallback_Order(`fallbackRank` 오름차순) 첫 Eligible_Contract 선택 (Requirement 8.1, 8.21)
  - Eligible_Contract가 없으면 `None` + 전송 건수 0 (Requirement 8.22)
  - Candidate_Label·Provider_String 문자열이 route 결정에 개입하지 않음 (Requirement 8.2, 8.3)
  - route가 `SUPPORTED`가 아니면 전송 계획이 0건 (Requirement 8.20)
  - fallback은 첫 Eligible_Contract만 사용하고 연쇄 이동하지 않음 (Requirement 8.21)

이 테스트는 Gateway를 호출하지 않는다. 통과 사실은 Gateway 지원 근거가 아니다
(Gateway 지원 주장은 작업 18의 production path probe로만 확정한다). 어떤 model ID·
provider·route 지원 여부도 상수로 두지 않고 무작위 심볼 자리표시자만 사용한다.

실행: ai_engine/.venv/bin/python -m pytest scripts/test_capability_request_router.py -q
"""
from __future__ import annotations

import copy
import os
import sys

import pytest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from ai_engine.capability import activation_gate, contracts  # noqa: E402
from ai_engine.capability import request_builder as rb  # noqa: E402

# 자리표시자 심볼 — 실제 model identity가 아니다(evidence만 실제 값을 채운다).
MODEL_ID = "sym-model-a"
PROVIDER = "sym-provider-a"
LABEL = "sym-label-a"
EVIDENCE_ID = "evr1:sha256:" + "a" * 64
PURPOSE = "sym-purpose"


# ─────────────────────────────────────────────────────────────────
# 헬퍼 — 완전한 Route_Contract를 가진 entry를 만든다
# ─────────────────────────────────────────────────────────────────
def _complete_contract(route_key: str, *, rank: int, purposes: tuple[str, ...] = (PURPOSE,)) -> dict:
    return contracts.new_route_contract(
        route_key,
        endpoint_ref=f"sym-endpoint-{route_key}",
        http_method="POST",
        execution_mode=contracts.Execution_Mode.SYNC,
        signing_service=contracts.Signing_Service.EXECUTE_API,
        model_id_required=False,
        message_field_path=["sym-messages"],
        optional_fields=[],
        output_validator_ref="sym-validator",
        terminal_condition_ref="sym-terminal",
        retry_policy_ref="sym-retry",
        fallback_rank=rank,
        purposes=list(purposes),
        min_output_bound={"sym-bound": 1},
        evidence_ref=EVIDENCE_ID,
    )


def _entry(
    supported: dict[str, int],
    *,
    allowlist: str = contracts.Allowlist_Result.ALLOWED,
    purposes: tuple[str, ...] = (PURPOSE,),
) -> dict:
    """`supported`의 `{routeKey: fallbackRank}`만 Eligible_Contract로 만든 entry."""
    entry = contracts.new_entry(LABEL)
    entry["modelId"] = MODEL_ID
    entry["provider"] = PROVIDER
    entry["evidence"] = [EVIDENCE_ID]
    entry["verifiedAt"] = "2026-08-03T00:00:00.000000Z"
    entry["verificationStatus"] = str(contracts.Verification_Status.VERIFIED)
    for route_key, rank in supported.items():
        entry["routes"][route_key] = {
            "status": str(contracts.Route_Support_Status.SUPPORTED),
            "allowlist": str(allowlist),
            "contract": _complete_contract(route_key, rank=rank, purposes=purposes),
            "evidenceRef": EVIDENCE_ID,
        }
    return entry


# ─────────────────────────────────────────────────────────────────
# Requirement 8.1, 8.21 — Fallback_Order 첫 Eligible_Contract
# ─────────────────────────────────────────────────────────────────
def test_select_contract_returns_lowest_fallback_rank():
    """`fallbackRank` 오름차순 첫 Eligible_Contract를 반환한다."""
    entry = _entry(
        {
            contracts.Known_Route.SSE_STREAM: 1,
            contracts.Known_Route.CONVERSE: 5,
            contracts.Known_Route.OPENAI_RESPONSES: 3,
        }
    )
    contract = rb.select_contract(entry, PURPOSE)
    assert contract is not None
    assert contract["routeKey"] == contracts.Known_Route.SSE_STREAM
    assert contract["fallbackRank"] == 1
    assert rb.select_route(entry, PURPOSE) == contracts.Known_Route.SSE_STREAM
    # 전체 Fallback_Order도 rank 오름차순이다.
    assert rb.fallback_route_keys(entry, PURPOSE) == [
        contracts.Known_Route.SSE_STREAM,
        contracts.Known_Route.OPENAI_RESPONSES,
        contracts.Known_Route.CONVERSE,
    ]


def test_fallback_order_is_independent_of_entry_route_insertion_order():
    """entry의 route 삽입 순서를 바꿔도 선택과 순서가 같다(결정론)."""
    ranks = {
        contracts.Known_Route.CONVERSE: 2,
        contracts.Known_Route.INVOKE: 0,
        contracts.Known_Route.OPENAI_RESPONSES_JOBS: 4,
    }
    forward = _entry(dict(ranks))
    reversed_entry = _entry({key: ranks[key] for key in reversed(list(ranks))})
    assert rb.fallback_route_keys(forward, PURPOSE) == rb.fallback_route_keys(reversed_entry, PURPOSE)
    assert rb.select_route(forward, PURPOSE) == contracts.Known_Route.INVOKE


def test_equal_rank_tie_is_broken_deterministically():
    """동일 rank는 route key 순으로 안정 정렬된다(입력 순서 무관)."""
    tie = {contracts.Known_Route.SSE_STREAM: 7, contracts.Known_Route.CONVERSE: 7}
    first = rb.fallback_route_keys(_entry(dict(tie)), PURPOSE)
    second = rb.fallback_route_keys(
        _entry({key: tie[key] for key in reversed(list(tie))}), PURPOSE
    )
    assert first == second == [contracts.Known_Route.CONVERSE, contracts.Known_Route.SSE_STREAM]


def test_selected_contract_is_a_deep_copy():
    """반환 계약을 수정해도 entry는 변하지 않는다."""
    entry = _entry({contracts.Known_Route.CONVERSE: 0})
    contract = rb.select_contract(entry, PURPOSE)
    contract["optionalFields"].append("sym-injected")
    assert entry["routes"][contracts.Known_Route.CONVERSE]["contract"]["optionalFields"] == []


# ─────────────────────────────────────────────────────────────────
# Requirement 8.22 — 후보 없음
# ─────────────────────────────────────────────────────────────────
@pytest.mark.parametrize(
    "mutate,expected_reason",
    [
        # route 상태가 SUPPORTED가 아니다
        (
            lambda e: e["routes"][contracts.Known_Route.CONVERSE].update(
                {"status": str(contracts.Route_Support_Status.UNVERIFIED)}
            ),
            rb.ROUTE_NOT_SUPPORTED,
        ),
        # allowlist가 ALLOWED가 아니다
        (
            lambda e: e["routes"][contracts.Known_Route.CONVERSE].update(
                {"allowlist": str(contracts.Allowlist_Result.REJECTED)}
            ),
            rb.ROUTE_ALLOWLIST_NOT_ALLOWED,
        ),
        # 계약이 없다
        (
            lambda e: e["routes"][contracts.Known_Route.CONVERSE].update({"contract": None}),
            rb.ROUTE_CONTRACT_MISSING,
        ),
        # 계약이 불완전하다
        (
            lambda e: e["routes"][contracts.Known_Route.CONVERSE]["contract"].update(
                {"messageFieldPath": None}
            ),
            rb.ROUTE_CONTRACT_INCOMPLETE,
        ),
        # Current_Evidence reference가 없다
        (
            lambda e: e["routes"][contracts.Known_Route.CONVERSE].update({"evidenceRef": None}),
            rb.ROUTE_EVIDENCE_MISSING,
        ),
        # 요청 목적을 충족하지 못한다
        (
            lambda e: e["routes"][contracts.Known_Route.CONVERSE]["contract"].update(
                {"purposes": ["sym-other-purpose"]}
            ),
            rb.ROUTE_PURPOSE_UNMET,
        ),
    ],
)
def test_no_eligible_contract_blocks_transmission(mutate, expected_reason):
    """Eligible_Contract가 없으면 `None`이고 전송 계획은 0건이다."""
    entry = _entry({contracts.Known_Route.CONVERSE: 0})
    mutate(entry)

    assert rb.select_contract(entry, PURPOSE) is None
    assert rb.select_route(entry, PURPOSE) is None
    assert rb.fallback_order(entry, PURPOSE) == []
    assert rb.may_transmit(entry, contracts.Known_Route.CONVERSE, PURPOSE) is False
    assert rb.route_block_reason(entry, contracts.Known_Route.CONVERSE, PURPOSE) == expected_reason

    plan = rb.transmission_plan(entry, PURPOSE)
    assert plan["transmit"] is False
    assert plan["maxTransmissions"] == 0
    assert plan["routeKey"] is None
    assert plan["contract"] is None
    assert plan["reason"] == rb.NO_ELIGIBLE_CONTRACT
    assert plan["fallbackOrder"] == []


def test_empty_and_malformed_entries_yield_no_contract():
    """빈 entry·비-dict 입력도 예외 없이 후보 0개로 판정된다."""
    for candidate in (None, {}, [], "sym-not-an-entry", {"routes": None}, {"routes": {}}):
        assert rb.select_contract(candidate, PURPOSE) is None
        assert rb.fallback_order(candidate, PURPOSE) == []
        plan = rb.transmission_plan(candidate, PURPOSE)
        assert plan["transmit"] is False and plan["maxTransmissions"] == 0


# ─────────────────────────────────────────────────────────────────
# Requirement 8.20 — 미지원 route 전송 0건
# ─────────────────────────────────────────────────────────────────
def test_requested_unsupported_route_is_never_transmitted():
    """`SUPPORTED`가 아닌 route를 명시 요청하면 전송 계획이 0건이다."""
    entry = _entry({contracts.Known_Route.CONVERSE: 0})
    plan = rb.transmission_plan(entry, PURPOSE, route_key=contracts.Known_Route.INVOKE)
    assert plan["transmit"] is False
    assert plan["maxTransmissions"] == 0
    assert plan["routeKey"] is None
    assert plan["requestedRoute"] == contracts.Known_Route.INVOKE
    assert plan["reason"] == rb.ROUTE_NOT_SUPPORTED
    # 그러나 Eligible_Contract는 여전히 존재한다(요청 route만 차단된다).
    assert plan["fallbackOrder"] == [contracts.Known_Route.CONVERSE]
    assert rb.contract_for_route(entry, contracts.Known_Route.INVOKE, PURPOSE) is None


def test_unknown_route_key_is_rejected():
    """Known_Route가 아닌 route key는 전송되지 않는다."""
    entry = _entry({contracts.Known_Route.CONVERSE: 0})
    plan = rb.transmission_plan(entry, PURPOSE, route_key="sym-not-a-route")
    assert plan["transmit"] is False and plan["reason"] == rb.ROUTE_UNKNOWN


def test_supported_route_request_transmits_once():
    """Eligible_Contract인 route를 요청하면 전송 1건이 허용된다."""
    entry = _entry({contracts.Known_Route.CONVERSE: 0, contracts.Known_Route.INVOKE: 9})
    plan = rb.transmission_plan(entry, PURPOSE, route_key=contracts.Known_Route.INVOKE)
    assert plan["transmit"] is True
    assert plan["maxTransmissions"] == 1
    assert plan["routeKey"] == contracts.Known_Route.INVOKE
    assert plan["contract"]["routeKey"] == contracts.Known_Route.INVOKE
    assert plan["fallbackRank"] == 9
    assert plan["evidenceRef"] == EVIDENCE_ID
    assert plan["reason"] == rb.REASON_OK
    assert plan["modelId"] == MODEL_ID


# ─────────────────────────────────────────────────────────────────
# Requirement 8.2, 8.3 — 라벨·provider 문자열 패턴 미사용
# ─────────────────────────────────────────────────────────────────
def test_label_and_provider_strings_do_not_change_routing():
    """Candidate_Label·displayName·Provider_String을 바꿔도 결정이 동일하다."""
    entry = _entry({contracts.Known_Route.CONVERSE: 1, contracts.Known_Route.INVOKE: 0})
    baseline = rb.transmission_plan(entry, PURPOSE)

    for label, provider, display in (
        ("opus 5", "anthropic", "표시명"),
        ("gpt 5.6", "openai", None),
        ("", "", ""),
        ("sym-zzz", "sym-yyy", "sym-xxx"),
    ):
        variant = copy.deepcopy(entry)
        variant["candidateLabel"] = label
        variant["provider"] = provider
        variant["displayName"] = display
        assert rb.transmission_plan(variant, PURPOSE) == baseline
        assert rb.select_route(variant, PURPOSE) == baseline["routeKey"]


def test_decision_view_excludes_label_and_provider_fields():
    """route 결정 입력 view에는 라벨·표시명·provider가 존재하지 않는다."""
    entry = _entry({contracts.Known_Route.CONVERSE: 0})
    view = rb.decision_view(entry)
    assert set(view) == set(rb.DECISION_INPUT_FIELDS)
    for field in rb.NON_INPUT_IDENTITY_FIELDS:
        assert field not in view


# ─────────────────────────────────────────────────────────────────
# Requirement 8.21 — fallback은 첫 후보만, 연쇄 이동 없음
# ─────────────────────────────────────────────────────────────────
def test_fallback_uses_first_candidate_only_and_does_not_chain():
    """실패 route를 제외한 첫 후보만 사용하고, 그 뒤로 연쇄 이동하지 않는다."""
    entry = _entry(
        {
            contracts.Known_Route.CONVERSE: 0,
            contracts.Known_Route.INVOKE: 1,
            contracts.Known_Route.SSE_STREAM: 2,
        }
    )
    primary = rb.select_route(entry, PURPOSE)
    assert primary == contracts.Known_Route.CONVERSE

    first_fallback = rb.select_fallback_contract(entry, PURPOSE, exclude_routes=(primary,))
    assert first_fallback["routeKey"] == contracts.Known_Route.INVOKE

    # 한 단계만 수행한다 — 같은 호출을 반복해도 다음 후보로 자동 이동하지 않는다.
    assert rb.FALLBACK_MAX_STEPS == 1
    assert (
        rb.select_fallback_contract(entry, PURPOSE, exclude_routes=(primary,))["routeKey"]
        == contracts.Known_Route.INVOKE
    )

    # 남은 후보가 없으면 fallback 전송도 생성되지 않는다(Requirement 8.22).
    exhausted = rb.select_fallback_contract(
        entry,
        PURPOSE,
        exclude_routes=(
            contracts.Known_Route.CONVERSE,
            contracts.Known_Route.INVOKE,
            contracts.Known_Route.SSE_STREAM,
        ),
    )
    assert exhausted is None
    plan = rb.transmission_plan(
        entry,
        PURPOSE,
        exclude_routes=(
            contracts.Known_Route.CONVERSE,
            contracts.Known_Route.INVOKE,
            contracts.Known_Route.SSE_STREAM,
        ),
    )
    assert plan["transmit"] is False and plan["maxTransmissions"] == 0


def test_excluded_route_request_is_blocked():
    """제외된 route를 명시 요청해도 전송하지 않는다."""
    entry = _entry({contracts.Known_Route.CONVERSE: 0})
    plan = rb.transmission_plan(
        entry,
        PURPOSE,
        route_key=contracts.Known_Route.CONVERSE,
        exclude_routes=(contracts.Known_Route.CONVERSE,),
    )
    assert plan["transmit"] is False and plan["reason"] == rb.ROUTE_EXCLUDED


# ─────────────────────────────────────────────────────────────────
# Activation_Gate 재사용 일관성
# ─────────────────────────────────────────────────────────────────
def test_router_matches_activation_gate_eligibility():
    """Eligible_Contract 판정은 Activation_Gate 결과와 정확히 일치한다."""
    entry = _entry({contracts.Known_Route.CONVERSE: 3, contracts.Known_Route.INVOKE: 1})
    assert rb.fallback_route_keys(entry, PURPOSE) == activation_gate.eligible_route_keys(
        entry, purposes=(PURPOSE,)
    )


def test_require_active_blocks_non_verified_entry():
    """`require_active`는 Activation_Gate entry 판정을 추가로 적용한다."""
    entry = _entry({contracts.Known_Route.CONVERSE: 0})
    entry["verificationStatus"] = str(contracts.Verification_Status.STALE)

    # route 단위 자격은 남아 있지만, entry 단위 판정에서 차단된다.
    assert rb.select_contract(entry, PURPOSE) is not None
    plan = rb.transmission_plan(entry, PURPOSE, require_active=True)
    assert plan["transmit"] is False
    assert plan["maxTransmissions"] == 0
    assert plan["reason"] in rb.DECISION_REASONS


def test_purpose_can_come_from_ctx():
    """목적은 ctx(`purposes`/`purpose`)에서도 읽는다."""
    entry = _entry({contracts.Known_Route.CONVERSE: 0})
    assert rb.select_route(entry, None, {"purpose": PURPOSE}) == contracts.Known_Route.CONVERSE
    assert rb.select_route(entry, None, {"purposes": [PURPOSE]}) == contracts.Known_Route.CONVERSE
    assert rb.select_route(entry, None, {"purpose": "sym-other"}) is None


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-q"]))
