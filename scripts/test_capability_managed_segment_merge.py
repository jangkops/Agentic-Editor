"""Unit — Managed_Segment 병합 seam과 UI payload 투영.

Feature: gateway-models-effort-support (task 5.3)
대상: ai_engine.capability.capability_map.merge_active_into_catalog / to_ui_payload
      + ai_engine.server._merge_managed_segment (`/api/models` seam)

검증 범위
  - Managed_Segment가 비면 `catalog`가 **동일 객체**로 반환되고 `capabilities` 키가
    부재해 `/api/models` 응답 바이트가 기준선과 동일하다 (Requirement 12.1, 12.2)
  - Active_Model이 있으면 Provider_String 그룹에만 추가되고 Baseline_Catalog_Segment의
    provider 분류·항목은 변하지 않으며 동일 `id`는 중복 추가되지 않는다 (Requirement 6.14)
  - Candidate_Label·displayName은 카탈로그 항목·UI payload 어디에도 노출되지 않는다
    (Requirement 6.26)
  - UI payload는 model·route별 effort 지원 여부와 **검증된 허용값**(enum 목록 또는
    inclusive range 경계) + value type을 계약에서 그대로 싣는다 (Requirement 7.1, 7.2, 7.3)
  - 병합 중 예외가 발생하면 원인을 200자로 절단해 로그하고 Baseline_Catalog_Segment만
    반환한다 (Requirement 1.13)
  - 영속 경로는 `userData/capability/` 하위만 사용한다 (Requirement 10.15)

이 테스트의 fixture 계약값(route·effort field path·허용값)은 **합성 값**이며 Gateway
지원 근거가 아니다. 이 파일은 Gateway를 호출하지 않으므로 통과 사실이 어떤 모델·route·
effort의 지원 근거가 되지 않는다(Requirement 12.22).

실행: ai_engine/.venv/bin/python -m pytest scripts/test_capability_managed_segment_merge.py -q
"""
from __future__ import annotations

import json
import os
import sys

import pytest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from ai_engine import server  # noqa: E402  (import 시 배너 출력 — 수집 단계에서 1회)
from ai_engine.capability import activation_gate as ag  # noqa: E402
from ai_engine.capability import capability_map as cm  # noqa: E402
from ai_engine.capability import contracts  # noqa: E402

CONVERSE = str(contracts.Known_Route.CONVERSE)
OPENAI_RESPONSES = str(contracts.Known_Route.OPENAI_RESPONSES)

#: 기준선 카탈로그(control-plane 결과 + 기존 필터 통과 형태)를 모사한 fixture.
BASELINE_CATALOG = {
    "Anthropic": [{"id": "baseline.model.a", "name": "Baseline A"}],
    "Meta": [{"id": "baseline.model.b", "name": "Baseline B"}],
}

# 진단 문자열 절단 길이(모듈 관례: 200자)
REASON_MAX = 200


# ─────────────────────────────────────────────────────────────────
# fixture 빌더 — 합성 계약값으로 Activation_Gate 통과 entry를 만든다
# ─────────────────────────────────────────────────────────────────
def _route_contract(
    route_key: str,
    evidence_ref: str,
    *,
    execution_mode: str = str(contracts.Execution_Mode.SYNC),
    purposes: tuple[str, ...] = ("chat",),
) -> dict:
    """완전한 Route_Contract(합성값). 실제 route 지원 근거가 아니다."""
    return contracts.new_route_contract(
        route_key,
        endpoint_ref="endpoint-ref",
        http_method="POST",
        execution_mode=execution_mode,
        signing_service=contracts.Signing_Service.EXECUTE_API,
        model_id_required=True,
        model_id_field_path=["modelId"],
        message_field_path=["messages"],
        inference_config_field_path=["inferenceConfig"],
        optional_fields=["system"],
        output_validator_ref="validator-ref",
        terminal_condition_ref="terminal-ref",
        retry_policy_ref="retry-ref",
        fallback_rank=0,
        purposes=list(purposes),
        min_output_bound={"maxTokens": 1},
        evidence_ref=evidence_ref,
    )


def _enum_effort_contract(model_id: str, route_key: str, evidence_ref: str) -> dict:
    """완전한 ENUM Effort_Contract(합성값)."""
    return contracts.new_effort_contract(
        model_id,
        route_key,
        field_path=["fieldAlpha", "fieldBeta"],
        value_type=contracts.Value_Type.STRING,
        domain_kind=contracts.Domain_Kind.ENUM,
        enum_values=["sym-a", "sym-b", "sym-c"],
        verified_values=["sym-a", "sym-b", "sym-c"],
        evidence_ref=evidence_ref,
    )


def _range_effort_contract(model_id: str, route_key: str, evidence_ref: str) -> dict:
    """완전한 RANGE Effort_Contract(합성값)."""
    return contracts.new_effort_contract(
        model_id,
        route_key,
        field_path=["fieldGamma"],
        value_type=contracts.Value_Type.INTEGER,
        domain_kind=contracts.Domain_Kind.RANGE,
        range_lower_inclusive=2,
        range_upper_inclusive=9,
        verified_values=[2, 9],
        evidence_ref=evidence_ref,
    )


def make_active_entry(
    *,
    model_id: str,
    provider: str,
    candidate_label: str = "label-x",
    display_name: str | None = None,
    route_key: str = CONVERSE,
    effort_contract: dict | None = None,
    effort_route_key: str | None = None,
    verified_at: str = "2026-08-03T01:00:00Z",
    revision: str = "rev-fixture",
    evidence_ref: str = "evr1:sha256:aaaa1111",
) -> dict:
    """Activation_Gate를 통과하는 유효 entry를 만든다(합성 계약값)."""
    entry = contracts.new_entry(candidate_label, source_kind=contracts.Source_Kind.CATALOG)
    entry["modelId"] = model_id
    entry["provider"] = provider
    entry["displayName"] = display_name
    entry["catalogFingerprint"] = "cat1:sha256:beef1234"
    entry["revision"] = revision
    entry["verifiedAt"] = verified_at
    entry["evidence"] = [evidence_ref]
    entry["activeEvidenceRef"] = evidence_ref

    entry["routes"][route_key] = {
        "status": str(contracts.Route_Support_Status.SUPPORTED),
        "allowlist": str(contracts.Allowlist_Result.ALLOWED),
        "contract": _route_contract(route_key, evidence_ref),
        "evidenceRef": evidence_ref,
    }
    if effort_contract is not None:
        target = effort_route_key or route_key
        entry["effort"][target] = {
            "status": str(contracts.Effort_Support_Status.SUPPORTED),
            "contract": effort_contract,
            "evidenceRef": evidence_ref,
        }

    entry["verificationStatus"] = str(contracts.Verification_Status.VERIFIED)
    cm.apply_mode_support(entry)
    cm.recompute_fingerprint(entry)
    return entry


#: 실제 Capability_Map entry의 route 상태 배치 — `SUPPORTED` 1개(계약 있음) +
#: 나머지 4개는 **상태만 있고 계약은 없다**. 계약이 없는 route는 mode를 Known_Route
#: 정의값에서만 알 수 있으므로, write 시점(hints 있음)과 read 시점(hints 없음)의 파생
#: 모드 유도가 갈리면 이 배치에서만 Complete_Record 판정이 깨진다.
STATUS_ONLY_ROUTE_STATUSES: dict[str, str] = {
    str(contracts.Known_Route.CONVERSE): str(contracts.Route_Support_Status.UNVERIFIED),
    str(contracts.Known_Route.INVOKE): str(contracts.Route_Support_Status.NOT_ADVERTISED),
    OPENAI_RESPONSES: str(contracts.Route_Support_Status.NOT_ADVERTISED),
    str(contracts.Known_Route.OPENAI_RESPONSES_JOBS): str(
        contracts.Route_Support_Status.NOT_ADVERTISED
    ),
}


def make_status_only_entry(
    *,
    model_id: str = "managed.model.streaming",
    provider: str = "ManagedProvider",
    candidate_label: str = "candidate-delta",
    supported_route: str = str(contracts.Known_Route.SSE_STREAM),
    evidence_ref: str = "evr3:sha256:cccc3333",
    verified_at: str = "2026-08-03T03:00:00Z",
    mode_hints: dict | None = None,
) -> dict:
    """실제 map 형태의 entry — `SUPPORTED` route 1개 + 상태만 있는 route 4개.

    파생 모드 상태는 write 시점처럼 `mode_hints`를 넘겨 계산한다(collector 경로 재현).
    """
    entry = contracts.new_entry(candidate_label, source_kind=contracts.Source_Kind.OPERATOR_EXPORT)
    entry["modelId"] = model_id
    entry["provider"] = provider
    entry["catalogFingerprint"] = "cat1:sha256:beef1234"
    entry["revision"] = "rev-fixture"
    entry["verifiedAt"] = verified_at
    entry["invocationModelIds"] = [model_id]
    entry["evidence"] = [evidence_ref]
    entry["activeEvidenceRef"] = evidence_ref

    for route_key, status in STATUS_ONLY_ROUTE_STATUSES.items():
        entry["routes"][route_key] = {
            "status": status,
            "allowlist": str(contracts.Allowlist_Result.UNVERIFIED),
            "contract": None,  # 계약 없음 — mode는 Known_Route 정의값으로만 알 수 있다
            "evidenceRef": None,
        }
    entry["routes"][supported_route] = {
        "status": str(contracts.Route_Support_Status.SUPPORTED),
        "allowlist": str(contracts.Allowlist_Result.ALLOWED),
        "contract": _route_contract(
            supported_route,
            evidence_ref,
            execution_mode=str(contracts.Execution_Mode.STREAMING),
            purposes=("stream",),
        ),
        "evidenceRef": evidence_ref,
    }

    entry["verificationStatus"] = str(contracts.Verification_Status.VERIFIED)
    cm.apply_mode_support(entry, mode_hints=mode_hints or _write_time_mode_hints())
    cm.recompute_fingerprint(entry)
    return entry


def _write_time_mode_hints() -> dict:
    """collector가 write 시점에 넘기는 route→mode 힌트(단일 출처 확인 포함)."""
    from ai_engine.capability import evidence_collector as ec

    hints = ec.route_mode_hints()
    assert hints == dict(contracts.DEFAULT_ROUTE_EXECUTION_MODES)  # 값이 갈리면 여기서 잡힌다
    return hints


@pytest.fixture()
def status_only_entry() -> dict:
    return make_status_only_entry()


@pytest.fixture()
def enum_entry() -> dict:
    return make_active_entry(
        model_id="managed.model.enum",
        provider="ManagedProvider",
        candidate_label="candidate-alpha",
        display_name="표시명 알파",
        effort_contract=_enum_effort_contract(
            "managed.model.enum", CONVERSE, "evr1:sha256:aaaa1111"
        ),
    )


@pytest.fixture()
def range_entry() -> dict:
    return make_active_entry(
        model_id="managed.model.range",
        provider="Anthropic",  # 기존 provider 그룹에 합류하는 경우
        candidate_label="candidate-beta",
        route_key=OPENAI_RESPONSES,
        effort_contract=_range_effort_contract(
            "managed.model.range", OPENAI_RESPONSES, "evr2:sha256:bbbb2222"
        ),
        verified_at="2026-08-03T02:00:00Z",
        evidence_ref="evr2:sha256:bbbb2222",
    )


# ─────────────────────────────────────────────────────────────────
# fixture 자체가 Activation_Gate를 통과하는지 먼저 확인한다
# ─────────────────────────────────────────────────────────────────
def test_fixture_entries_pass_activation_gate(enum_entry, range_entry):
    """병합 입력은 Activation_Gate 산출물이어야 한다 — fixture 전제 검증."""
    for entry in (enum_entry, range_entry):
        assert cm.entry_malformed_reasons(entry) == []
        active, reason = ag.is_active(entry, {})
        assert active is True, reason

    map_obj = cm.new_map(entries=[enum_entry, range_entry])
    active_ids = ag.active_model_ids(map_obj, {})
    assert active_ids == ["managed.model.enum", "managed.model.range"]


# ─────────────────────────────────────────────────────────────────
# 빈 Managed_Segment → 기준선 동일성 (Requirement 12.1, 12.2)
# ─────────────────────────────────────────────────────────────────
def test_empty_managed_segment_returns_same_catalog_object():
    """비면 병합은 입력 catalog를 동일 객체로 반환한다(응답 바이트 보존)."""
    for empty in ([], (), None):
        merged = cm.merge_active_into_catalog(BASELINE_CATALOG, empty)
        assert merged is BASELINE_CATALOG


def test_duplicate_only_managed_segment_returns_same_catalog_object(enum_entry):
    """추가할 항목이 하나도 없으면(전부 baseline 중복) 동일 객체를 반환한다."""
    entry = make_active_entry(
        model_id="baseline.model.a",  # 이미 baseline에 있는 id
        provider="ManagedProvider",
    )
    merged = cm.merge_active_into_catalog(BASELINE_CATALOG, [entry])
    assert merged is BASELINE_CATALOG


def test_seam_without_map_keeps_baseline_bytes(tmp_path, monkeypatch, capsys):
    """Capability_Map 파일이 없으면 seam이 catalog를 그대로 두고 capabilities도 없다."""
    monkeypatch.setenv("AE_USERDATA_PATH", str(tmp_path))
    monkeypatch.delenv("AE_GENERATED_ROOT", raising=False)

    baseline_bytes = json.dumps(BASELINE_CATALOG).encode("utf-8")
    catalog, capabilities = server._merge_managed_segment(BASELINE_CATALOG)

    assert catalog is BASELINE_CATALOG
    assert capabilities is None
    assert json.dumps(catalog).encode("utf-8") == baseline_bytes
    assert capsys.readouterr().out == ""  # 정상 경로는 로그를 만들지 않는다


def test_seam_with_unverified_map_keeps_baseline(tmp_path, monkeypatch):
    """`UNVERIFIED` entry만 있는 map은 Managed_Segment를 비워 기준선을 유지한다."""
    monkeypatch.setenv("AE_USERDATA_PATH", str(tmp_path))
    monkeypatch.delenv("AE_GENERATED_ROOT", raising=False)

    unverified = contracts.new_entry("candidate-gamma")
    cm.save(cm.new_map(entries=[unverified]), str(tmp_path))

    catalog, capabilities = server._merge_managed_segment(BASELINE_CATALOG)
    assert catalog is BASELINE_CATALOG
    assert capabilities is None


# ─────────────────────────────────────────────────────────────────
# Active_Model 병합 (Requirement 6.14, 6.26)
# ─────────────────────────────────────────────────────────────────
def test_merge_adds_active_models_to_provider_groups(enum_entry, range_entry):
    """Active_Model은 자신의 Provider_String 그룹에만 추가된다."""
    merged = cm.merge_active_into_catalog(BASELINE_CATALOG, [enum_entry, range_entry])

    assert merged is not BASELINE_CATALOG
    # 새 provider 그룹 생성
    assert merged["ManagedProvider"] == [
        {"id": "managed.model.enum", "name": "managed.model.enum"}
    ]
    # 기존 provider 그룹에는 **뒤에** 덧붙는다(기존 항목 순서·내용 불변)
    assert merged["Anthropic"] == [
        {"id": "baseline.model.a", "name": "Baseline A"},
        {"id": "managed.model.range", "name": "managed.model.range"},
    ]
    assert merged["Meta"] == BASELINE_CATALOG["Meta"]
    # 입력 카탈로그는 변경되지 않는다
    assert BASELINE_CATALOG == {
        "Anthropic": [{"id": "baseline.model.a", "name": "Baseline A"}],
        "Meta": [{"id": "baseline.model.b", "name": "Baseline B"}],
    }


def test_merge_skips_ids_already_present_in_any_provider(enum_entry):
    """어느 provider에든 동일 id가 있으면 건너뛴다(baseline 보존·중복 금지)."""
    dup = make_active_entry(model_id="baseline.model.b", provider="ManagedProvider")
    merged = cm.merge_active_into_catalog(BASELINE_CATALOG, [dup, enum_entry])

    assert merged["Meta"] == BASELINE_CATALOG["Meta"]
    assert [item["id"] for item in merged["ManagedProvider"]] == ["managed.model.enum"]


def test_merge_skips_entries_without_identity():
    """modelId·provider가 비면 노출하지 않는다(방어적)."""
    no_model = make_active_entry(model_id="", provider="ManagedProvider")
    no_provider = make_active_entry(model_id="managed.model.x", provider="")
    assert cm.merge_active_into_catalog(BASELINE_CATALOG, [no_model, no_provider]) is BASELINE_CATALOG


def test_merged_catalog_never_exposes_candidate_label_or_display_name(enum_entry, range_entry):
    """Requirement 6.26 — 검색 라벨·표시명은 모델 항목이 되지 않는다."""
    merged = cm.merge_active_into_catalog(BASELINE_CATALOG, [enum_entry, range_entry])
    text = json.dumps(merged, ensure_ascii=False)
    assert "candidate-alpha" not in text
    assert "candidate-beta" not in text
    assert "표시명 알파" not in text
    # Managed 항목이 노출하는 표시 문자열은 Exact_Model_ID뿐이다(baseline 항목은 불변).
    baseline_ids = {
        item["id"] for items in BASELINE_CATALOG.values() for item in items
    }
    for items in merged.values():
        for item in items:
            assert set(item) == set(cm.CATALOG_ITEM_FIELDS)
            if item["id"] not in baseline_ids:
                assert item["name"] == item["id"]


# ─────────────────────────────────────────────────────────────────
# UI payload — effort 지원 정보 (Requirement 7.1, 7.2, 7.3)
# ─────────────────────────────────────────────────────────────────
def test_ui_payload_structure_and_enum_domain(enum_entry):
    """ENUM effort는 검증된 enum 목록과 value type을 그대로 싣는다."""
    payload = cm.to_ui_payload([enum_entry])

    assert set(payload) == set(cm.UI_PAYLOAD_KEYS)
    assert payload["schemaVersion"] == cm.UI_PAYLOAD_SCHEMA_VERSION
    assert payload["modelIds"] == ["managed.model.enum"]

    view = payload["models"]["managed.model.enum"]
    assert view["modelId"] == "managed.model.enum"
    assert view["provider"] == "ManagedProvider"
    assert view["capabilityFingerprint"] == enum_entry["capabilityFingerprint"]
    assert view["verificationStatus"] == str(contracts.Verification_Status.VERIFIED)

    # 모든 Known_Route 키가 존재해야 프론트가 키 존재를 가정할 수 있다.
    assert set(view["routes"]) == set(contracts.KNOWN_ROUTES)
    assert set(view["effort"]) == set(contracts.KNOWN_ROUTES)

    route_view = view["routes"][CONVERSE]
    assert set(route_view) == set(cm.UI_ROUTE_FIELDS)
    assert route_view["status"] == str(contracts.Route_Support_Status.SUPPORTED)
    assert route_view["allowlist"] == str(contracts.Allowlist_Result.ALLOWED)
    assert route_view["executionMode"] == str(contracts.Execution_Mode.SYNC)
    assert route_view["purposes"] == ["chat"]
    assert route_view["fallbackRank"] == 0

    effort_view = view["effort"][CONVERSE]
    assert effort_view["supported"] is True
    assert effort_view["status"] == str(contracts.Effort_Support_Status.SUPPORTED)
    assert effort_view["valueType"] == str(contracts.Value_Type.STRING)
    assert effort_view["domainKind"] == str(contracts.Domain_Kind.ENUM)
    assert effort_view["enumValues"] == ["sym-a", "sym-b", "sym-c"]
    assert effort_view["verifiedValues"] == ["sym-a", "sym-b", "sym-c"]
    assert "rangeLowerInclusive" not in effort_view
    assert view["effortRoutes"] == [CONVERSE]


def test_ui_payload_range_domain_boundaries(range_entry):
    """RANGE effort는 inclusive 경계와 value type만 싣는다(enum 목록 없음)."""
    payload = cm.to_ui_payload([range_entry])
    effort_view = payload["models"]["managed.model.range"]["effort"][OPENAI_RESPONSES]

    assert effort_view["supported"] is True
    assert effort_view["valueType"] == str(contracts.Value_Type.INTEGER)
    assert effort_view["domainKind"] == str(contracts.Domain_Kind.RANGE)
    assert effort_view["rangeLowerInclusive"] == 2
    assert effort_view["rangeUpperInclusive"] == 9
    assert "enumValues" not in effort_view
    assert effort_view["verifiedValues"] == [2, 9]
    assert payload["models"]["managed.model.range"]["effortRoutes"] == [OPENAI_RESPONSES]


def test_ui_payload_hides_unsupported_effort_domains(enum_entry):
    """`SUPPORTED`가 아닌 effort route는 상태만 남기고 허용값을 싣지 않는다."""
    payload = cm.to_ui_payload([enum_entry])
    view = payload["models"]["managed.model.enum"]

    for route in contracts.KNOWN_ROUTES:
        if route == CONVERSE:
            continue
        effort_view = view["effort"][route]
        assert effort_view["supported"] is False
        assert set(effort_view) == set(cm.UI_EFFORT_BASE_FIELDS)
        assert effort_view["status"] == str(contracts.Effort_Support_Status.UNVERIFIED)


def test_ui_payload_rejects_misbound_effort_contract():
    """다른 model·route에 결속된 계약으로는 선택 후보를 만들지 않는다(방어적)."""
    entry = make_active_entry(
        model_id="managed.model.enum",
        provider="ManagedProvider",
        effort_contract=_enum_effort_contract(
            "other.model.id", CONVERSE, "evr1:sha256:aaaa1111"
        ),
    )
    effort_view = cm.to_ui_payload([entry])["models"]["managed.model.enum"]["effort"][CONVERSE]
    assert effort_view["supported"] is False
    assert set(effort_view) == set(cm.UI_EFFORT_BASE_FIELDS)


def test_ui_payload_never_exposes_candidate_label_or_display_name(enum_entry, range_entry):
    """Requirement 6.26 — payload에도 검색 라벨·표시명이 실리지 않는다."""
    text = json.dumps(cm.to_ui_payload([enum_entry, range_entry]), ensure_ascii=False)
    for leaked in ("candidateLabel", "candidate-alpha", "candidate-beta", "displayName", "표시명 알파"):
        assert leaked not in text


def test_ui_payload_empty_and_duplicate_inputs(enum_entry):
    """빈 입력은 빈 payload, 동일 modelId 중복은 1개만 노출한다."""
    empty = cm.to_ui_payload([])
    assert empty == {
        "schemaVersion": cm.UI_PAYLOAD_SCHEMA_VERSION,
        "modelIds": [],
        "models": {},
    }

    payload = cm.to_ui_payload([enum_entry, enum_entry, {"modelId": ""}, "not-a-dict"])
    assert payload["modelIds"] == ["managed.model.enum"]
    assert len(payload["models"]) == 1


def test_ui_payload_does_not_alias_contract_values(enum_entry):
    """payload 변형이 entry 계약을 오염시키지 않는다(깊은 복사)."""
    payload = cm.to_ui_payload([enum_entry])
    payload["models"]["managed.model.enum"]["effort"][CONVERSE]["enumValues"].append("mutated")
    assert enum_entry["effort"][CONVERSE]["contract"]["enumValues"] == ["sym-a", "sym-b", "sym-c"]


# ─────────────────────────────────────────────────────────────────
# server seam — 실제 userData 경로 왕복과 실패 폴백
# ─────────────────────────────────────────────────────────────────
def test_seam_merges_active_models_from_user_data(tmp_path, monkeypatch, enum_entry, range_entry):
    """`userData/capability/capability_map.json`에서 읽어 병합·payload를 구성한다."""
    monkeypatch.setenv("AE_USERDATA_PATH", str(tmp_path))
    monkeypatch.delenv("AE_GENERATED_ROOT", raising=False)

    written = cm.save(cm.new_map(entries=[enum_entry, range_entry]), str(tmp_path))
    assert written == tmp_path / "capability" / "capability_map.json"  # userData 하위만

    catalog, capabilities = server._merge_managed_segment(BASELINE_CATALOG)

    assert catalog is not BASELINE_CATALOG
    assert [item["id"] for item in catalog["ManagedProvider"]] == ["managed.model.enum"]
    assert [item["id"] for item in catalog["Anthropic"]] == [
        "baseline.model.a",
        "managed.model.range",
    ]
    assert capabilities["modelIds"] == ["managed.model.enum", "managed.model.range"]
    assert capabilities["models"]["managed.model.enum"]["effort"][CONVERSE]["enumValues"] == [
        "sym-a",
        "sym-b",
        "sym-c",
    ]
    assert capabilities["models"]["managed.model.range"]["effort"][OPENAI_RESPONSES][
        "rangeUpperInclusive"
    ] == 9


def test_seam_falls_back_to_baseline_on_exception(monkeypatch, capsys):
    """병합 실패 시 원인을 200자로 절단해 로그하고 Baseline_Catalog_Segment만 반환한다."""

    def _boom(*_args, **_kwargs):
        raise RuntimeError("병합 실패 " + "x" * 400)

    monkeypatch.setattr(ag, "active_models", _boom)

    catalog, capabilities = server._merge_managed_segment(BASELINE_CATALOG)
    assert catalog is BASELINE_CATALOG
    assert capabilities is None

    line = capsys.readouterr().out.strip()
    assert line.startswith("[Capability] Managed_Segment 병합 생략: ")
    assert len(line.split(": ", 1)[1]) <= REASON_MAX


# ─────────────────────────────────────────────────────────────────
# read-time 유도 일치 — "상태는 있고 계약은 없는 Known_Route"를 포함한 entry
#
# write 시점은 collector가 `mode_hints`를 넘기고 read 시점(server seam)은 넘기지 않는다.
# 계약 없는 route의 execution mode가 hints에만 있으면 두 유도 결과가 갈려
# `complete_record_reasons`가 `derived-mismatch`를 내고 `VERIFIED` entry가 탈락한다.
# (Requirement 3.20~3.23, 6.5, 6.14)
# ─────────────────────────────────────────────────────────────────
def test_status_only_routes_derive_the_same_mode_support_without_hints(status_only_entry):
    """hints 없이 유도해도 저장된 파생 모드 상태와 같다."""
    derived = cm.derive_mode_support(status_only_entry["routes"])
    with_hints = cm.derive_mode_support(
        status_only_entry["routes"], mode_hints=_write_time_mode_hints()
    )

    assert derived == with_hints
    for field, expected in derived.items():
        assert status_only_entry[field] == expected, field
    # 실제 map 형태의 기대값 — streaming만 SUPPORTED, async는 광고되지 않았다.
    assert derived["streamingSupport"] == str(contracts.Route_Support_Status.SUPPORTED)
    assert derived["asyncSupport"] == str(contracts.Route_Support_Status.NOT_ADVERTISED)
    assert derived["syncSupport"] == str(contracts.Route_Support_Status.UNVERIFIED)


def test_status_only_entry_is_complete_record_and_active_without_hints(status_only_entry):
    """read 시점 게이트(`active_models(map, {})`)가 hints 없이 통과시킨다."""
    assert cm.entry_malformed_reasons(status_only_entry) == []
    assert cm.complete_record_reasons(status_only_entry) == []
    assert cm.promotion_blockers(status_only_entry, ctx={}) == []
    assert ag.is_active(status_only_entry, {}) == (True, ag.REASON_OK)

    map_obj = cm.new_map(entries=[status_only_entry])
    assert ag.active_model_ids(map_obj, {}) == ["managed.model.streaming"]
    assert ag.eligible_route_keys(status_only_entry, ctx={}) == [
        str(contracts.Known_Route.SSE_STREAM)
    ]


def test_seam_exposes_status_only_entry_from_user_data(tmp_path, monkeypatch, status_only_entry):
    """`/api/models` seam이 이 entry를 Managed_Segment로 노출한다(capabilities 포함)."""
    monkeypatch.setenv("AE_USERDATA_PATH", str(tmp_path))
    monkeypatch.delenv("AE_GENERATED_ROOT", raising=False)
    cm.save(cm.new_map(entries=[status_only_entry]), str(tmp_path))

    catalog, capabilities = server._merge_managed_segment(BASELINE_CATALOG)

    assert catalog is not BASELINE_CATALOG
    assert [item["id"] for item in catalog["ManagedProvider"]] == ["managed.model.streaming"]
    assert capabilities is not None
    assert capabilities["modelIds"] == ["managed.model.streaming"]

    view = capabilities["models"]["managed.model.streaming"]
    assert view["streamingSupport"] == str(contracts.Route_Support_Status.SUPPORTED)
    assert view["routes"][str(contracts.Known_Route.SSE_STREAM)]["executionMode"] == str(
        contracts.Execution_Mode.STREAMING
    )
    # 계약이 없는 route는 mode를 payload에 만들어 넣지 않는다(투영은 계약만 읽는다).
    assert view["routes"][CONVERSE]["executionMode"] is None
    # effort가 검증되지 않았으므로 선택 후보는 0건이다(프론트 셀렉트 박스는 숨는다).
    assert view["effortRoutes"] == []


def test_seam_falls_back_when_ui_payload_fails(monkeypatch, tmp_path, enum_entry):
    """payload 구성이 실패해도 병합 결과를 흘리지 않고 baseline을 반환한다."""
    monkeypatch.setenv("AE_USERDATA_PATH", str(tmp_path))
    monkeypatch.delenv("AE_GENERATED_ROOT", raising=False)
    cm.save(cm.new_map(entries=[enum_entry]), str(tmp_path))

    def _boom(*_args, **_kwargs):
        raise ValueError("payload 실패")

    monkeypatch.setattr(cm, "to_ui_payload", _boom)

    catalog, capabilities = server._merge_managed_segment(BASELINE_CATALOG)
    assert catalog is BASELINE_CATALOG
    assert capabilities is None


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-q"]))
