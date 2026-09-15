"""Validation_Runner CLI 골격·stage 오케스트레이션·보고서 단위 테스트.

Feature: gateway-models-effort-support (task 12.1 + 12.2)
대상: scripts/validate_gateway_model_capabilities.py

검증 범위 (task 12.1 — CLI 골격·interpreter guard·dry-run·보고서 헤더)
  - `--labels`, `--report`, `--dry-run`, `--stage` 인자 처리와 `--stage` 닫힌 집합 거부
  - interpreter guard 판정 축(부재·실행 권한 없음·비활성·capability import 실패)과
    guard 실패 시 Gateway_Probe 전송 0건 + interpreter 환경 오류만 보고
    (Requirement 11.1, 11.2, 11.3)
  - `--dry-run`이 전송 0건으로 예산 계획만 산출(Requirement 11.13~11.16)
  - 보고서 헤더: Current_Revision, interpreter absolute path, 시작 UTC ISO 8601,
    Same_Gateway_Environment identity, PBT seed(Requirement 11.4, 11.5, 11.6)
  - Gateway_Probe 입력이 고정된 짧은 비민감 입력이고 route별 최소 output/token bound만
    계획에 남으며 raw prompt는 계획에도 기록되지 않음(Requirement 11.13, 11.14, 10.13)

검증 범위 (task 12.2 — stage 오케스트레이션과 보고서 본문)
  - stage 순서: interpreter → baseline → discover → route → effort → map → activate
  - `--dry-run`과 interpreter guard 실패에서 Gateway_Probe 전송 0건 유지
    (Requirement 11.2, 11.3, 11.13)
  - baseline stage가 10개 Baseline_Category record를
    `userData/capability/baseline/{revision}.json`에 기록하고 `evidenceEligible`을 보고
    (Requirement 1.11, 1.12, 1.16, 11.4~11.6)
  - catalog 부재 환경에서 전 라벨 `UNVERIFIED` 유지 · probe 미생성(Requirement 2.4, 2.12)
  - 검증된 Operator_Catalog_Export로 발견한 Exact_Model_ID·Provider_String을 문자 변경
    없이 보고하고, integration probe 미완료 조합을 `UNVERIFIED`로 보고
    (Requirement 11.7, 11.8, 11.24, 12.24)
  - 조합당 성공 1회·교정 1회·route당 prefix 교정 1회를 runner 수준에서 재확인
    (Requirement 11.15, 11.16)
  - 신규·변경 Active_Model의 Current_Evidence·revision·Capability_Fingerprint 일치 검사
    실패 시 activation 결과에서 제외(Requirement 11.21~11.24)
  - 보고서 본문 키가 `reportBody`이며 usage·cost 미제공은 `notProvided`
    (Requirement 11.17~11.20)

이 테스트는 Gateway를 호출하지 않는다. 통과 사실은 Gateway 지원 근거가 아니다
(Requirement 12.22) — 실제 지원 주장은 작업 18의 production path probe만이 만든다.

실행: ai_engine/.venv/bin/python -m pytest scripts/test_capability_validation_runner.py -q
"""
from __future__ import annotations

import copy
import json
import os
import sys

import pytest

_HERE = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.dirname(_HERE)
for _path in (_ROOT, _HERE):
    if _path not in sys.path:
        sys.path.insert(0, _path)

import validate_gateway_model_capabilities as runner  # noqa: E402

from ai_engine.capability import baseline_inspector, canonicalizer, capability_map, contracts  # noqa: E402
from ai_engine.capability import evidence_collector as ec  # noqa: E402

from _capability_strategies import AE_PBT_SEED  # noqa: E402  (보고서 seed의 유일한 정의 위치)

# 무작위 심볼 — model ID·provider·route 지원·effort 값을 상수로 두지 않는다.
SYM_MODEL_ID = "sym-model-7f31"
SYM_PROVIDER = "sym-provider-a2"
SYM_ENV_ID = "sym-env-id"
SYM_EVIDENCE_ID = "evr1:sha256:" + "d" * 8
SYM_EFFORT_FIELD = "sym-effort-field"
SYM_EFFORT_FIELD_OLD = "sym-effort-field-old"
SYM_EFFORT_VALUES = ("sym-effort-lo", "sym-effort-hi")
SYM_ENDPOINT_REF_OLD = "sym-endpoint-ref-old"
LABELS = ("opus 5", "sonnet 5", "gpt 5.6", "sol", "terra", "luna")


@pytest.fixture(autouse=True)
def _capability_loaded():
    """runner의 지연 import를 테스트 시작 시 결속한다."""
    loaded, error = runner.load_capability()
    assert loaded, error
    yield


@pytest.fixture()
def user_data(tmp_path, monkeypatch):
    """userData 루트를 임시 디렉터리로 고정한다(영속 데이터는 이 하위만)."""
    monkeypatch.setenv("AE_USERDATA_PATH", str(tmp_path))
    monkeypatch.delenv(runner.ENV_CATALOG_ENDPOINT, raising=False)
    monkeypatch.delenv(runner.ENV_OPERATOR_EXPORT, raising=False)
    monkeypatch.delenv(runner.ENV_PBT_RUN, raising=False)
    return tmp_path


def _run(stage="all", *, dry_run=False, labels=LABELS):
    """runner를 in-process로 실행하고 보고서를 반환한다(전송 여부는 보고서로 판정)."""
    return runner.build_report(
        labels=list(labels), stage=stage, dry_run=dry_run, root=runner.repo_root()
    )


def _stage(report, name):
    for item in report.get("stages") or []:
        if item.get("stage") == name:
            return item
    raise AssertionError(f"stage 없음: {name}")


def _operator_export(monkeypatch, user_data, *, routes, effort=None):
    """검증을 통과하는 Operator_Catalog_Export를 userData 하위에 만든다."""
    record = {
        "modelId": SYM_MODEL_ID,
        "provider": SYM_PROVIDER,
        "candidateLabel": LABELS[0],
        "routes": list(routes),
    }
    if effort is not None:
        record["effort"] = effort
    snapshot = {"models": [record]}
    export = {
        "schemaVersion": 1,
        "environment": {
            "gatewayEnvironmentId": SYM_ENV_ID,
            "endpointIdentity": _endpoint_identity(),
            "region": _region(),
        },
        "generatedAt": "2026-08-01T00:00:00Z",
        "collectedAt": "2026-08-01T00:00:01Z",
        "catalogFingerprint": canonicalizer.catalog_fingerprint(snapshot),
        "sanitizationManifest": {"removedFields": ["authorization"]},
        "models": snapshot["models"],
    }
    target = user_data / "capability" / "operator-export.json"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(export), encoding="utf-8")
    monkeypatch.setenv("AE_GATEWAY_ENV_ID", SYM_ENV_ID)
    monkeypatch.setenv(runner.ENV_OPERATOR_EXPORT, "capability/operator-export.json")
    return export


def _endpoint_identity():
    gw, _ = runner.gateway_client(os.environ)
    return getattr(gw, "gateway_url", "")


def _region():
    gw, _ = runner.gateway_client(os.environ)
    return getattr(gw, "region", "")


# ─────────────────────────────────────────────────────────────────
# stage 순서와 닫힌 집합
# ─────────────────────────────────────────────────────────────────
def test_stage_order_is_pipeline_order():
    assert runner.STAGE_ORDER == (
        "interpreter",
        "baseline",
        "discover",
        "route",
        "effort",
        "map",
        "activate",
    )
    assert runner.resolve_stages("all") == runner.STAGE_ORDER
    assert runner.resolve_stages("route") == ("interpreter", "route")
    assert set(runner.STAGE_HANDLERS) == set(runner.STAGE_ORDER) - {"interpreter"}
    with pytest.raises(ValueError):
        runner.resolve_stages("bogus")


# ─────────────────────────────────────────────────────────────────
# dry-run · interpreter guard — 전송 0건
# ─────────────────────────────────────────────────────────────────
def test_dry_run_keeps_zero_transmissions(user_data):
    report = _run("all", dry_run=True)
    assert report["gatewayProbeTransmissions"] == 0
    assert all(item["transmissions"] == 0 for item in report["stages"])
    assert [item["status"] for item in report["stages"][1:]] == ["skipped"] * 6
    body = report["reportBody"]
    assert body["status"] == "skipped"
    assert body["transmissions"] == 0
    assert body["budget"]["reverified"] is True
    assert body["budget"]["totalTransmissions"] == 0
    # 예산 계획만 산출하고 어떤 지원 주장도 하지 않는다.
    assert body["discovery"] == [] and body["routes"] == [] and body["effort"] == []


def test_interpreter_failure_blocks_all_stages(user_data):
    verdict = runner.interpreter_verdict(runner.repo_root())
    verdict = dict(verdict, ok=False, reasons=[runner.INTERPRETER_NOT_ACTIVE])
    report = runner.build_report(
        labels=list(LABELS), stage="all", dry_run=False, verdict=verdict
    )
    assert report["gatewayProbeTransmissions"] == 0
    assert "plan" not in report
    assert report["error"]["category"] == runner.REASON_INTERPRETER_ENVIRONMENT_ERROR
    statuses = {item["status"] for item in report["stages"][1:]}
    assert statuses == {"blocked"}
    assert all(item["transmissions"] == 0 for item in report["stages"])


# ─────────────────────────────────────────────────────────────────
# baseline stage
# ─────────────────────────────────────────────────────────────────
def test_baseline_stage_writes_ten_category_records(user_data):
    report = _run("baseline")
    stage = _stage(report, "baseline")
    assert stage["status"] == "completed"
    assert stage["transmissions"] == 0

    section = report["reportBody"]["baseline"]
    assert section["categoryCount"] == 10
    assert len(section["categories"]) == 10
    assert isinstance(section["evidenceEligible"], bool)
    assert section["inspectedAt"]

    revision = section["revision"]
    stored = json.loads(
        (user_data / "capability" / "baseline" / f"{revision}.json").read_text(encoding="utf-8")
    )
    assert len(stored["categories"]) == 10
    assert {record["category"] for record in stored["records"]} == set(stored["categories"])
    assert all(record["revision"] == revision for record in stored["records"])
    assert stored["evidenceEligible"] == section["evidenceEligible"]


# ─────────────────────────────────────────────────────────────────
# discover stage — catalog 부재 / 검증된 Operator_Catalog_Export
# ─────────────────────────────────────────────────────────────────
def test_discover_without_catalog_keeps_all_labels_unverified(user_data):
    report = _run("discover")
    stage = _stage(report, "discover")
    assert stage["status"] == "completed"
    assert stage["reason"] == runner.REASON_CATALOG_UNAVAILABLE
    assert stage["transmissions"] == 0

    body = report["reportBody"]
    assert body["catalog"]["available"] is False
    assert len(body["discovery"]) == 6
    for item in body["discovery"]:
        assert item["verificationStatus"] == str(contracts.Verification_Status.UNVERIFIED)
        assert item["found"] is False
        assert item["probeEligible"] is False
        assert item["modelId"] == contracts.UNDETERMINED
        assert item["advertisedRoutes"] == []
    assert body["probes"] == []
    assert body["transmissions"] == 0

    stored = json.loads(
        (user_data / "capability" / "capability_map.json").read_text(encoding="utf-8")
    )
    assert len(stored["entries"]) == 6
    assert {entry["verificationStatus"] for entry in stored["entries"]} == {"UNVERIFIED"}


def test_discover_with_operator_export_records_identity_and_unverified_claims(
    user_data, monkeypatch
):
    routes = (str(contracts.Known_Route.CONVERSE), str(contracts.Known_Route.OPENAI_RESPONSES))
    _operator_export(monkeypatch, user_data, routes=routes)

    report = _run("discover")
    stage = _stage(report, "discover")
    assert stage["status"] == "completed"
    assert stage["transmissions"] == 0  # discovery는 catalog 조회만 한다.

    body = report["reportBody"]
    assert body["catalog"]["available"] is True
    assert body["catalog"]["sourceKind"] == str(contracts.Source_Kind.OPERATOR_EXPORT)

    found = [item for item in body["discovery"] if item["found"]]
    assert len(found) == 1
    entry = found[0]
    # catalog 문자 그대로 기록한다(Requirement 2.3).
    assert entry["modelId"] == SYM_MODEL_ID
    assert entry["provider"] == SYM_PROVIDER
    assert entry["verificationStatus"] == str(contracts.Verification_Status.DISCOVERED)
    assert entry["advertisedRoutes"] == [route for route in contracts.KNOWN_ROUTES if route in routes]
    assert entry["evidenceRef"].startswith("evr1:sha256:")

    # 발견되지 않은 라벨은 evidence도 생기지 않는다(라벨 간 전파 없음).
    for item in body["discovery"]:
        if item["found"]:
            continue
        assert item["modelId"] == contracts.UNDETERMINED
        assert item["evidenceRef"] == contracts.UNDETERMINED

    # integration probe 미완료 → 라벨·route 조합은 모두 `UNVERIFIED` 주장이다.
    claims = report["reportBody"]["activation"]["unverified"]
    route_claims = {
        item["routeKey"] for item in claims if item["kind"] == runner.CLAIM_ROUTE
    }
    assert route_claims == set(entry["advertisedRoutes"])
    assert all(item["status"] == "UNVERIFIED" for item in claims)


def test_activation_excludes_discovered_entries(user_data, monkeypatch):
    _operator_export(
        monkeypatch, user_data, routes=(str(contracts.Known_Route.CONVERSE),)
    )
    report = _run("activate")
    section = report["reportBody"]["activation"]
    assert section["active"] == []
    assert section["managedSegment"]["count"] == 0
    reasons = {item["reason"] for item in section["excluded"]}
    # `DISCOVERED`·`UNVERIFIED`는 활성 후보가 아니다(Requirement 6.9, 6.10).
    assert reasons <= {"STATUS_DISCOVERED", "STATUS_UNVERIFIED"}
    assert _stage(report, "activate")["transmissions"] == 0


# ─────────────────────────────────────────────────────────────────
# 보고서 본문 키 트리 · usage/cost
# ─────────────────────────────────────────────────────────────────
def test_report_body_key_tree_and_not_provided(user_data):
    report = _run("all")
    assert "body" not in report  # store 정제기가 raw body로 오인하는 키는 쓰지 않는다.
    body = report["reportBody"]
    assert {
        "status",
        "stagesExecuted",
        "baseline",
        "catalog",
        "discovery",
        "routes",
        "effort",
        "models",
        "evidence",
        "activation",
        "probes",
        "usage",
        "cost",
        "budget",
        "pbt",
        "transmissions",
    } <= set(body)
    assert "body" not in body
    assert body["usage"] == contracts.NOT_PROVIDED
    assert body["cost"] == contracts.NOT_PROVIDED
    assert body["pbt"]["seed"] == report["header"]["pbtSeed"]
    assert body["pbt"]["counterexamples"] == []
    models = body["models"]
    assert models["entryCount"] == 6
    assert all(item["fingerprintMatches"] for item in models["entries"])


# ─────────────────────────────────────────────────────────────────
# 예산 재확인 (runner 수준)
# ─────────────────────────────────────────────────────────────────
def _context(user_data):
    return runner.new_run_context(
        labels=list(LABELS),
        stage="all",
        stages=runner.STAGE_ORDER,
        dry_run=False,
        root=runner.repo_root(),
    )


def test_budget_reverification_reads_probe_budget_limits(user_data):
    context = _context(user_data)
    budget = context["budget"]
    key = ec.ProbeBudget.key(SYM_MODEL_ID, str(contracts.Known_Route.CONVERSE))
    budget.record_transmission(key)
    budget.record_success(key)
    assert runner.budget_reverification(context)["reverified"] is True

    # 조합당 성공 generation 한도(1회)를 넘기면 위반으로 보고한다.
    budget.record_transmission(key)
    budget.record_success(key)
    verdict = runner.budget_reverification(context)
    assert verdict["reverified"] is False
    assert [item["scope"] for item in verdict["violations"]] == ["successes"]
    assert verdict["violations"][0]["count"] == 2
    assert verdict["violations"][0]["limit"] == 1
    assert verdict["totalTransmissions"] == 2


def test_budget_reverification_flags_correction_and_prefix_limits(user_data):
    context = _context(user_data)
    budget = context["budget"]
    route = str(contracts.Known_Route.OPENAI_RESPONSES)
    key = ec.ProbeBudget.key(SYM_MODEL_ID, route)
    budget.record_correction(key)
    budget.record_correction(key)
    budget.record_prefix_correction(SYM_MODEL_ID, route)
    budget.record_prefix_correction(SYM_MODEL_ID, route)
    verdict = runner.budget_reverification(context)
    assert verdict["reverified"] is False
    assert {item["scope"] for item in verdict["violations"]} == {
        "corrections",
        "prefixCorrections",
    }


# ─────────────────────────────────────────────────────────────────
# activation 일치 검사 (Requirement 11.21~11.24)
# ─────────────────────────────────────────────────────────────────
def _touched_entry(context, *, evidence_id, revision):
    """이 실행이 record를 만든 것처럼 상태를 채운 entry를 만든다."""
    entry = contracts.new_entry(
        LABELS[0], fingerprint_fn=canonicalizer.capability_fingerprint
    )
    entry["modelId"] = SYM_MODEL_ID
    entry["provider"] = SYM_PROVIDER
    entry["revision"] = revision
    entry["evidence"] = [evidence_id]
    entry["activeEvidenceRef"] = evidence_id
    entry["capabilityFingerprint"] = canonicalizer.capability_fingerprint(entry)
    context["state"]["evidenceIds"].append("evr1:sha256:" + "a" * 8)
    context["state"]["touchedLabels"].append(LABELS[0])
    context["state"]["records"].append(
        {"candidateLabel": LABELS[0], "modelId": SYM_MODEL_ID, "evidenceRecordId": "evr1:sha256:" + "a" * 8}
    )
    return entry


def test_run_evidence_reasons_detects_all_three_mismatches(user_data):
    context = _context(user_data)
    run_id = "evr1:sha256:" + "a" * 8
    entry = _touched_entry(context, evidence_id=run_id, revision=context["revision"])
    assert runner.touched_by_run(entry, context) is True
    assert runner.run_evidence_reasons(entry, context) == []

    # 1) Current_Evidence가 이 실행의 record를 가리키지 않는다.
    stale_evidence = dict(entry, evidence=["evr1:sha256:" + "b" * 8], activeEvidenceRef="")
    assert runner.RUN_EVIDENCE_MISMATCH in runner.run_evidence_reasons(stale_evidence, context)

    # 2) revision이 Current_Revision과 다르다.
    other_revision = dict(entry, revision="sym-other-revision")
    assert runner.RUN_REVISION_MISMATCH in runner.run_evidence_reasons(other_revision, context)

    # 3) 저장된 Capability_Fingerprint가 재계산값과 다르다.
    bad_fingerprint = dict(entry, capabilityFingerprint="cfp1:sha256:" + "c" * 8)
    assert runner.RUN_FINGERPRINT_MISMATCH in runner.run_evidence_reasons(
        bad_fingerprint, context
    )


def test_activation_section_excludes_run_mismatch(user_data, monkeypatch):
    context = _context(user_data)
    run_id = "evr1:sha256:" + "a" * 8
    entry = _touched_entry(context, evidence_id=run_id, revision="sym-other-revision")
    context["state"]["map"] = {"schemaVersion": 1, "updatedAt": "", "entries": [entry]}

    # activation_gate가 활성으로 판정하더라도 runner의 일치 검사가 제외한다.
    monkeypatch.setattr(
        runner.activation_gate,
        "activation_report",
        lambda *args, **kwargs: {
            "active": [entry],
            "excluded": [],
            "counts": {"entries": 1, "malformed": 0, "active": 1, "excluded": 0},
        },
    )
    section = runner.activation_section(context)
    assert section["active"] == []
    assert section["counts"]["active"] == 0
    assert [item["reason"] for item in section["excluded"]] == [runner.RUN_REVISION_MISMATCH]
    assert section["evidenceChecks"][0]["ok"] is False
    assert section["managedSegment"]["modelIds"] == []


# ─────────────────────────────────────────────────────────────────
# 미완료 조합 주장 (Requirement 12.23, 12.24)
# ─────────────────────────────────────────────────────────────────
def test_unverified_claims_cover_route_and_effort_combinations(user_data):
    context = _context(user_data)
    route = str(contracts.Known_Route.CONVERSE)
    effort_route = str(contracts.Known_Route.OPENAI_RESPONSES)
    context["state"]["discovery"] = [
        {
            "candidateLabel": LABELS[0],
            "found": True,
            "modelId": SYM_MODEL_ID,
            "provider": SYM_PROVIDER,
            "advertisedRoutes": [route, effort_route],
            "advertisedEffort": {effort_route: {"fieldPath": ["sym-effort"]}},
            "probeEligible": True,
            "reasons": [],
        },
        {
            "candidateLabel": LABELS[1],
            "found": False,
            "modelId": "",
            "advertisedRoutes": [],
            "reasons": [ec.LABEL_ASSOCIATION_ABSENT],
        },
    ]
    # route는 production probe로 SUPPORTED가 확정됐고, effort는 미완료다.
    context["state"]["routeResults"][LABELS[0]] = [
        {
            "routeKey": route,
            "modelId": SYM_MODEL_ID,
            "status": str(contracts.Route_Support_Status.SUPPORTED),
            "productionPath": True,
            "evidenceEligible": True,
        }
    ]
    claims = runner.unverified_claims(context)
    kinds = {(item["kind"], item["routeKey"]) for item in claims}
    assert (runner.CLAIM_MODEL, None) in kinds          # 미발견 라벨
    assert (runner.CLAIM_ROUTE, route) not in kinds      # 확정된 route는 주장에서 빠진다
    assert (runner.CLAIM_ROUTE, effort_route) in kinds   # probe 미수행 route
    assert (runner.CLAIM_EFFORT, effort_route) in kinds  # effort 미확정
    assert all(item["status"] == "UNVERIFIED" for item in claims)


# ─────────────────────────────────────────────────────────────────
# PBT seed와 counterexample 기록 (Requirement 12.19~12.21)
# ─────────────────────────────────────────────────────────────────
def test_property_test_files_cover_this_feature_only():
    files = runner.property_test_files(runner.repo_root())
    # Correctness Properties 1~10 — 공통 생성기 모듈을 쓰는 파일만 골라진다.
    assert len(files) == 10
    assert all(name.endswith(runner.PBT_FILE_SUFFIX) for name in files)


def test_pbt_section_records_seed_without_running(user_data):
    context = _context(user_data)
    section = runner.pbt_section(context)
    assert section["seed"] == context["pbtSeed"]
    assert section["executed"] is False
    assert section["reason"] == "NOT_REQUESTED"
    assert len(section["files"]) == 10

    context["dryRun"] = True
    assert runner.pbt_section(context)["reason"] == runner.REASON_DRY_RUN


def test_parse_counterexamples_captures_minimized_example_and_blob():
    output = "\n".join(
        [
            "E       Falsifying example: test_activation_subset(",
            "E           map_obj={'schemaVersion': 1, 'entries': []},",
            "E       )",
            "E       You can reproduce this example by temporarily adding "
            "@reproduce_failure('6.99.1', b'AXicY2BkZGQAAA==') as a decorator on your test case",
            "",
            "Falsifying example: test_other(value=0)",
        ]
    )
    found = runner.parse_counterexamples(output)
    assert len(found) == 2
    assert "map_obj={'schemaVersion': 1, 'entries': []}," in found[0]["minimizedExample"]
    assert found[0]["reproduceBlob"].startswith("@reproduce_failure(")
    assert found[1]["minimizedExample"] == "test_other(value=0)"
    assert found[1]["reproduceBlob"] is None
    assert all(len(item["minimizedExample"]) <= 200 for item in found)


# ─────────────────────────────────────────────────────────────────
# stage 실패 격리 — 이후 stage에 Gateway 예산을 쓰지 않는다
# ─────────────────────────────────────────────────────────────────
def test_failed_stage_blocks_later_stages_with_zero_transmissions(user_data, monkeypatch):
    context = _context(user_data)
    stages = runner.stage_results(runner.STAGE_ORDER, interpreter_ok=True, dry_run=False)

    def _boom(_context, _body):
        raise RuntimeError("sym-stage-failure")

    monkeypatch.setitem(runner.STAGE_HANDLERS, "discover", _boom)
    body = runner.execute_stages(context, stages, run_id="sym-run")

    by_stage = {item["stage"]: item for item in stages}
    assert by_stage["baseline"]["status"] == "completed"
    assert by_stage["discover"]["status"] == "failed"
    assert by_stage["discover"]["reason"] == runner.REASON_STAGE_ERROR
    for name in ("route", "effort", "map", "activate"):
        assert by_stage[name]["status"] == "blocked"
        assert by_stage[name]["transmissions"] == 0
    assert body["status"] == "failed"
    assert body["transmissions"] == 0
    assert any("sym-stage-failure" in note for note in body["notes"])


# ─────────────────────────────────────────────────────────────────
# STALE 전이 트리거 입력 — routeContracts · effortContracts
# (Requirement 6.17, 6.18, 6.21)
#
# `capability_map.stale_trigger`는 `{modelId: {routeKey: 계약}}` 두 매핑으로 Route_Contract
# 변경·Effort_Contract 변경을 판정한다. 그 매핑을 채우는 곳은 `currency_ctx`뿐이므로,
# 아래 테스트는 (1) catalog 근거가 있을 때만 채우고, (2) 값이 catalog 선언·evidence에서만
# 오고, (3) 두 트리거가 실제로 `STALE` 전이를 만들고 재검증 미완료 시 유지되는지 확인한다.
# ─────────────────────────────────────────────────────────────────
def _effort_declaration(field_path=(SYM_EFFORT_FIELD,)):
    """catalog가 선언한 effort 계약(field path·허용값은 전부 무작위 심볼)."""
    return {
        "fieldPath": list(field_path),
        "valueType": str(contracts.Value_Type.STRING),
        "domainKind": str(contracts.Domain_Kind.ENUM),
        "enumValues": list(SYM_EFFORT_VALUES),
    }


def _discovered_context(user_data, monkeypatch, *, route, effort_route=None):
    """catalog 근거(Operator_Catalog_Export)를 갖고 discovery까지 끝낸 실행 컨텍스트."""
    effort = {effort_route: _effort_declaration()} if effort_route else None
    _operator_export(monkeypatch, user_data, routes=(route,), effort=effort)
    context = _context(user_data)
    runner.ensure_discovery(context)
    return context


def _found_discovery(context):
    return next(item for item in context["state"]["discovery"] if item.get("found"))


def _recorded_entry(context, *, route, effort_route=None, effort_field_path=(SYM_EFFORT_FIELD,)):
    """성공 run이 기록한 것과 같은 형태의 `VERIFIED` entry.

    계약 값은 전부 기존 구현·catalog 선언이 준 것이고, evidence 자리(`evidenceRef`·
    `verifiedValues`)는 성공 probe가 채운 형태를 그대로 재현한다.
    """
    entry = contracts.new_entry(LABELS[0])
    entry["modelId"] = SYM_MODEL_ID
    entry["provider"] = SYM_PROVIDER
    entry["revision"] = context["revision"]
    entry["catalogFingerprint"] = context["state"]["catalog"]["catalogFingerprint"]
    entry["verifiedAt"] = "2026-08-01T00:00:02Z"
    entry["evidence"] = [SYM_EVIDENCE_ID]
    entry["activeEvidenceRef"] = SYM_EVIDENCE_ID
    entry["verificationStatus"] = str(contracts.Verification_Status.VERIFIED)
    entry["routes"][route]["status"] = str(contracts.Route_Support_Status.SUPPORTED)
    entry["routes"][route]["allowlist"] = str(contracts.Allowlist_Result.ALLOWED)
    entry["routes"][route]["evidenceRef"] = SYM_EVIDENCE_ID
    entry["routes"][route]["contract"] = ec.candidate_route_contract(
        route, evidence_ref=SYM_EVIDENCE_ID
    )
    if effort_route is not None:
        declaration = {"modelId": SYM_MODEL_ID, "advertisedEffort": {
            effort_route: _effort_declaration(effort_field_path)
        }}
        recorded = ec.candidate_effort_contract_for(
            declaration, effort_route, evidence_ref=SYM_EVIDENCE_ID
        )
        # 성공 effort probe가 확인한 value만 `verifiedValues`에 남는다(5.12, 5.13).
        recorded["verifiedValues"] = [SYM_EFFORT_VALUES[0]]
        entry["effort"][effort_route]["status"] = str(contracts.Effort_Support_Status.SUPPORTED)
        entry["effort"][effort_route]["evidenceRef"] = SYM_EVIDENCE_ID
        entry["effort"][effort_route]["contract"] = recorded
    capability_map.apply_mode_support(entry)
    entry["capabilityFingerprint"] = canonicalizer.capability_fingerprint(entry)
    return entry


def _map_with(entry):
    return {"schemaVersion": 1, "updatedAt": "", "entries": [copy.deepcopy(entry)]}


def test_currency_ctx_omits_contract_keys_without_catalog_evidence(user_data):
    """catalog가 근거로 인정되지 않으면 두 키를 넣지 않는다(모른다 ≠ 변경됐다)."""
    context = _context(user_data)
    runner.ensure_discovery(context)
    assert context["state"]["catalog"]["available"] is False

    ctx = runner.currency_ctx(context)
    assert "routeContracts" not in ctx
    assert "effortContracts" not in ctx
    # 기존 catalog 키 처리와 같은 원칙 — 근거가 없으면 어떤 catalog 키도 넣지 않는다.
    assert "catalogModelIds" not in ctx
    assert "catalogProviders" not in ctx

    # 근거가 없으므로 STALE 전이도 일어나지 않는다.
    entry = contracts.new_entry(LABELS[0], fingerprint_fn=canonicalizer.capability_fingerprint)
    assert capability_map.stale_trigger(entry, ctx) is None


def test_currency_ctx_fills_contract_keys_from_catalog_declaration(user_data, monkeypatch):
    """catalog 근거가 있으면 광고 route·effort 선언만 두 키에 담는다(값은 선언 그대로)."""
    route = str(contracts.Known_Route.CONVERSE)
    other_route = str(contracts.Known_Route.OPENAI_RESPONSES)
    context = _discovered_context(user_data, monkeypatch, route=route, effort_route=route)
    discovery = _found_discovery(context)

    ctx = runner.currency_ctx(context)
    assert set(ctx["routeContracts"]) == {SYM_MODEL_ID}
    assert set(ctx["effortContracts"]) == {SYM_MODEL_ID}
    # 광고되지 않은 route는 비교 대상이 아니다(catalog가 말하지 않은 것은 넣지 않는다).
    assert set(ctx["routeContracts"][SYM_MODEL_ID]) == {route}
    assert set(ctx["effortContracts"][SYM_MODEL_ID]) == {route}
    assert other_route not in ctx["routeContracts"][SYM_MODEL_ID]

    # 계약 값은 기존 구현(route profile)과 catalog 선언이 준 것뿐이다.
    assert ctx["routeContracts"][SYM_MODEL_ID][route] == ec.candidate_route_contract(route)
    assert ctx["effortContracts"][SYM_MODEL_ID][route] == ec.candidate_effort_contract_for(
        discovery, route
    )
    effort_contract = ctx["effortContracts"][SYM_MODEL_ID][route]
    assert effort_contract["modelId"] == SYM_MODEL_ID
    assert effort_contract["fieldPath"] == [SYM_EFFORT_FIELD]
    assert effort_contract["enumValues"] == list(SYM_EFFORT_VALUES)


def test_currency_ctx_keeps_unchanged_contracts_out_of_stale_transition(user_data, monkeypatch):
    """선언이 그대로면 evidence 기록 차이(`evidenceRef`·`verifiedValues`)만으로 강등하지 않는다."""
    route = str(contracts.Known_Route.CONVERSE)
    context = _discovered_context(user_data, monkeypatch, route=route, effort_route=route)
    entry = _recorded_entry(context, route=route, effort_route=route)
    context["state"]["map"] = _map_with(entry)

    ctx = runner.currency_ctx(context)
    assert capability_map.stale_trigger(entry, ctx) is None

    out = capability_map.apply_stale_triggers(context["state"]["map"], ctx)
    assert out["entries"][0]["verificationStatus"] == str(contracts.Verification_Status.VERIFIED)
    assert "staleReason" not in out["entries"][0]


def test_route_contract_change_transitions_entry_to_stale(user_data, monkeypatch):
    """기록된 Route_Contract가 현재 계약과 달라지면 `STALE`로 전이하고 유지한다(6.17, 6.21)."""
    route = str(contracts.Known_Route.CONVERSE)
    context = _discovered_context(user_data, monkeypatch, route=route)
    entry = _recorded_entry(context, route=route)
    # 기록된 계약의 endpoint 참조자가 현재 구현이 알려주는 값과 다르다.
    entry["routes"][route]["contract"]["endpointRef"] = SYM_ENDPOINT_REF_OLD
    entry["capabilityFingerprint"] = canonicalizer.capability_fingerprint(entry)
    context["state"]["map"] = _map_with(entry)

    ctx = runner.currency_ctx(context)
    assert capability_map.stale_trigger(entry, ctx) == capability_map.STALE_ROUTE_CONTRACT_CHANGED

    out = capability_map.apply_stale_triggers(context["state"]["map"], ctx)
    stale = out["entries"][0]
    assert stale["verificationStatus"] == str(contracts.Verification_Status.STALE)
    assert stale["staleReason"] == capability_map.STALE_ROUTE_CONTRACT_CHANGED
    # 재검증이 완료되지 않았으므로 다시 적용해도 `STALE`을 유지한다(Requirement 6.21).
    again = capability_map.apply_stale_triggers(out, ctx)
    assert again["entries"][0]["verificationStatus"] == str(contracts.Verification_Status.STALE)
    assert again["entries"][0]["staleReason"] == capability_map.STALE_ROUTE_CONTRACT_CHANGED
    # 계약·route 상태는 그대로 남는다(전이는 상태와 이유만 바꾼다).
    assert again["entries"][0]["routes"][route]["status"] == str(
        contracts.Route_Support_Status.SUPPORTED
    )


def test_effort_contract_change_transitions_entry_to_stale(user_data, monkeypatch):
    """catalog effort 선언이 바뀌면 기존 entry를 `STALE`로 전이하고 유지한다(6.18, 6.21)."""
    route = str(contracts.Known_Route.CONVERSE)
    context = _discovered_context(user_data, monkeypatch, route=route, effort_route=route)
    # entry는 이전 선언(다른 field path)으로 검증된 계약을 보유한다.
    entry = _recorded_entry(
        context, route=route, effort_route=route, effort_field_path=(SYM_EFFORT_FIELD_OLD,)
    )
    context["state"]["map"] = _map_with(entry)

    ctx = runner.currency_ctx(context)
    assert capability_map.stale_trigger(entry, ctx) == capability_map.STALE_EFFORT_CONTRACT_CHANGED

    out = capability_map.apply_stale_triggers(context["state"]["map"], ctx)
    stale = out["entries"][0]
    assert stale["verificationStatus"] == str(contracts.Verification_Status.STALE)
    assert stale["staleReason"] == capability_map.STALE_EFFORT_CONTRACT_CHANGED
    again = capability_map.apply_stale_triggers(out, ctx)
    assert again["entries"][0]["verificationStatus"] == str(contracts.Verification_Status.STALE)
    assert again["entries"][0]["effort"][route]["contract"]["fieldPath"] == [SYM_EFFORT_FIELD_OLD]


# ─────────────────────────────────────────────────────────────────
# task 12.1 — CLI 인자 처리 (`--labels`, `--report`, `--dry-run`, `--stage`)
# ─────────────────────────────────────────────────────────────────
def test_parser_defaults_and_explicit_arguments():
    parser = runner.build_parser()

    default = parser.parse_args([])
    assert default.labels == list(ec.CANDIDATE_LABELS)  # 검색 라벨의 유일한 정의 위치
    assert default.report is None
    assert default.dry_run is False
    assert default.stage == runner.STAGE_ALL

    args = parser.parse_args(
        [
            "--labels",
            "sym-label-a",
            "sym-label-b",
            "--report",
            "capability/runs/sym-report.json",
            "--dry-run",
            "--stage",
            "baseline",
        ]
    )
    assert args.labels == ["sym-label-a", "sym-label-b"]
    assert args.report == "capability/runs/sym-report.json"
    assert args.dry_run is True
    assert args.stage == "baseline"

    # 값 없는 `--labels`는 빈 목록이고, main이 기본 집합으로 되돌린다(라벨을 만들지 않는다).
    assert parser.parse_args(["--labels"]).labels == []


def test_parser_rejects_stage_outside_closed_set():
    with pytest.raises(SystemExit) as exc:
        runner.build_parser().parse_args(["--stage", "sym-unknown-stage"])
    assert exc.value.code == 2  # 인자 오류 종료 코드


def test_normalize_labels_drops_blank_and_duplicates():
    assert runner.normalize_labels(["sym-a", " ", "sym-a", "", None, "sym-b"]) == [
        "sym-a",
        "sym-b",
    ]
    assert runner.normalize_labels([]) == []


def test_main_dry_run_writes_report_at_requested_path(user_data, capsys):
    code = runner.main(["--labels", "--dry-run", "--report", "capability/runs/sym-dry-run.json"])
    assert code == runner.EXIT_OK

    written = json.loads(
        (user_data / "capability" / "runs" / "sym-dry-run.json").read_text(encoding="utf-8")
    )
    # `--dry-run`은 전송 0건으로 예산 계획만 산출한다(Requirement 11.13~11.16).
    assert written["gatewayProbeTransmissions"] == 0
    assert written["plan"]["plannedTransmissions"] == 0
    assert all(item["transmissions"] == 0 for item in written["stages"])
    assert written["reportBody"]["transmissions"] == 0
    assert written["header"]["dryRun"] is True
    # 값 없는 `--labels`는 기본 Candidate_Label 집합으로 되돌리고 note를 남긴다.
    assert written["header"]["candidateLabels"] == list(ec.CANDIDATE_LABELS)
    assert runner.NOTE_LABELS_DEFAULTED in written["header"]["notes"]
    assert "Gateway_Probe 전송: 0건" in capsys.readouterr().out


def test_main_rejects_report_path_outside_user_data(user_data, capsys):
    outside = str(user_data.parent / "sym-outside-report.json")
    code = runner.main(["--dry-run", "--report", outside])
    assert code == runner.EXIT_REPORT_ERROR
    assert not os.path.exists(outside)  # userData 루트 밖에는 아무 것도 쓰지 않는다.
    assert "보고서 기록 실패" in capsys.readouterr().err


# ─────────────────────────────────────────────────────────────────
# task 12.1 — interpreter guard (Requirement 11.1, 11.2, 11.3)
# ─────────────────────────────────────────────────────────────────
def test_interpreter_verdict_accepts_the_running_venv():
    # 이 테스트는 ai_engine/.venv/bin/python으로만 실행된다(모든 Python 실행 규약).
    verdict = runner.interpreter_verdict(runner.repo_root())
    assert verdict["ok"] is True
    assert verdict["reasons"] == []
    assert verdict["exists"] and verdict["executable"] and verdict["active"]
    assert os.path.isabs(verdict["interpreterPath"])
    assert os.path.realpath(verdict["interpreterPath"]) == os.path.realpath(
        verdict["expectedInterpreterPath"]
    )
    assert verdict["importError"] is None


def test_interpreter_verdict_flags_absent_and_inactive(tmp_path):
    verdict = runner.interpreter_verdict(tmp_path)
    assert verdict["ok"] is False
    assert verdict["reasons"] == [runner.INTERPRETER_ABSENT, runner.INTERPRETER_NOT_ACTIVE]
    assert verdict["expectedInterpreterPath"] == os.path.join(
        str(tmp_path), *runner.INTERPRETER_RELATIVE_PARTS
    )
    assert verdict["exists"] is False and verdict["executable"] is False


def test_interpreter_verdict_flags_missing_execute_permission(tmp_path):
    target = tmp_path.joinpath(*runner.INTERPRETER_RELATIVE_PARTS)
    target.parent.mkdir(parents=True)
    target.write_text("", encoding="utf-8")
    os.chmod(target, 0o644)
    verdict = runner.interpreter_verdict(tmp_path)
    assert verdict["ok"] is False
    assert verdict["exists"] is True and verdict["executable"] is False
    assert runner.INTERPRETER_NOT_EXECUTABLE in verdict["reasons"]
    assert runner.INTERPRETER_ABSENT not in verdict["reasons"]


def test_interpreter_verdict_accepts_prefix_match_without_path_match():
    # venv의 `python`은 보통 symlink이므로 실행 파일 경로 일치만으로는 판정할 수 없다.
    verdict = runner.interpreter_verdict(
        runner.repo_root(), executable="/sym/other/python", prefix=runner.venv_root()
    )
    assert verdict["ok"] is True
    assert verdict["pathMatches"] is False
    assert verdict["prefixMatches"] is True and verdict["active"] is True


def test_with_import_failure_marks_environment_error_without_mutating_input():
    verdict = runner.interpreter_verdict(runner.repo_root())
    failed = runner.with_import_failure(verdict, "SyntaxError: " + "x" * 400)
    assert failed["ok"] is False
    assert runner.INTERPRETER_IMPORT_FAILED in failed["reasons"]
    assert len(failed["importError"]) == 200  # 진단 문자열 200자 절단
    assert verdict["ok"] is True and runner.INTERPRETER_IMPORT_FAILED not in verdict["reasons"]

    message = runner.interpreter_error_message(failed)
    assert runner.INTERPRETER_IMPORT_FAILED in message
    assert failed["expectedInterpreterPath"] in message
    assert failed["interpreterPath"] in message


def test_main_reports_interpreter_environment_error_only(user_data, monkeypatch, capsys):
    failing = dict(
        runner.interpreter_verdict(runner.repo_root()),
        ok=False,
        reasons=[runner.INTERPRETER_NOT_ACTIVE],
    )
    monkeypatch.setattr(runner, "interpreter_verdict", lambda *args, **kwargs: failing)

    code = runner.main([])
    assert code == runner.EXIT_INTERPRETER_ERROR

    captured = capsys.readouterr()
    assert "interpreter 환경 오류" in captured.err
    assert runner.INTERPRETER_NOT_ACTIVE in captured.err

    reports = sorted((user_data / "capability" / "runs").glob("*.json"))
    assert len(reports) == 1
    written = json.loads(reports[0].read_text(encoding="utf-8"))
    # 전송 0건 유지 + interpreter 환경 오류만 보고 — 예산 계획도 본문도 만들지 않는다.
    assert written["gatewayProbeTransmissions"] == 0
    assert "plan" not in written and "reportBody" not in written
    assert written["error"]["category"] == runner.REASON_INTERPRETER_ENVIRONMENT_ERROR
    assert {item["status"] for item in written["stages"][1:]} == {"blocked"}
    assert all(item["transmissions"] == 0 for item in written["stages"])


# ─────────────────────────────────────────────────────────────────
# task 12.1 — 보고서 헤더 (Requirement 11.4, 11.5, 11.6)
# ─────────────────────────────────────────────────────────────────
def test_report_header_records_revision_interpreter_time_environment_seed(user_data):
    report = _run("all", dry_run=True)
    header = report["header"]

    # Current_Revision — 계산 위치는 baseline_inspector 하나뿐이다.
    assert header["revision"] == (
        baseline_inspector.current_revision(runner.repo_root()) or contracts.UNDETERMINED
    )
    # interpreter absolute path.
    assert os.path.isabs(header["interpreterPath"])
    assert os.path.realpath(header["interpreterPath"]) == os.path.realpath(
        header["expectedInterpreterPath"]
    )
    # 시작 시각은 UTC ISO 8601.
    assert contracts.is_utc_iso8601(header["startedAt"])
    # Same_Gateway_Environment identity(기존 GatewayClient 속성에서만 읽는다).
    identity, _notes = runner.gateway_environment(os.environ)
    assert header["environment"] == identity
    assert set(header["environment"]) >= set(contracts.REQUIRED_ENVIRONMENT_FIELDS)
    # PBT seed.
    assert header["pbtSeed"] == AE_PBT_SEED
    # runId는 시작 시각 + 실행 입력의 결정론적 축약이다.
    assert header["runId"].startswith(runner.compact_stamp(header["startedAt"]))
    assert report["runId"] == header["runId"]


def test_report_header_is_deterministic_for_same_inputs(user_data):
    kwargs = dict(
        labels=list(LABELS),
        stage="all",
        stages=runner.STAGE_ORDER,
        dry_run=True,
        root=runner.repo_root(),
        started_at="2026-08-01T00:00:00Z",
    )
    first = runner.report_header(**kwargs)
    second = runner.report_header(**kwargs)
    assert first["runId"] == second["runId"]
    assert first == second


# ─────────────────────────────────────────────────────────────────
# task 12.1 — 고정 probe 입력과 계약 허용 최소 output/token bound
# (Requirement 11.13, 11.14, 11.15, 11.16, 10.13)
# ─────────────────────────────────────────────────────────────────
def test_plan_fixes_probe_input_and_minimal_output_bounds(user_data):
    plan = _run("all", dry_run=True)["plan"]
    assert plan["dryRun"] is True
    assert plan["plannedTransmissions"] == 0
    assert plan["candidateLabels"] == list(LABELS)

    # 예산 한도는 ProbeBudget이 강제하는 값을 그대로 읽는다(runner에 별도 상수 없음).
    budget = ec.ProbeBudget()
    assert plan["budget"]["combinationAxes"] == list(runner.COMBINATION_AXES)
    assert plan["budget"]["maxSuccessesPerCombination"] == budget.max_successes == 1
    assert plan["budget"]["maxCorrectionsPerCombination"] == budget.max_corrections == 1
    assert plan["budget"]["maxPrefixCorrectionsPerRoute"] == budget.max_prefix_corrections == 1
    assert plan["budget"]["transmissionLimitPerCombination"] == budget.transmission_limit()
    assert plan["budget"]["totalTransmissions"] == 0

    probe_input = plan["probeInput"]
    assert probe_input["textLength"] == len(ec.PROBE_INPUT_TEXT)  # 고정된 짧은 입력
    assert probe_input["systemPromptEmpty"] is True
    assert probe_input["effortSelection"] is ec.PROBE_EFFORT_SELECTION is None
    assert probe_input["probeId"] == ec.probe_id("plan", "probe-input")
    assert probe_input["sourceRef"] == "evidence_collector.PROBE_INPUT_TEXT"
    # raw prompt는 계획에도 남지 않는다 — Probe_ID와 Sanitized_Schema만 기록한다.
    assert ec.PROBE_INPUT_TEXT not in json.dumps(plan, ensure_ascii=False)
    assert probe_input["inputSchema"] == runner.store.sanitized_schema(ec.probe_messages())

    assert [item["routeKey"] for item in plan["routes"]] == list(contracts.KNOWN_ROUTES)
    for item in plan["routes"]:
        contract = ec.candidate_route_contract(item["routeKey"])
        assert item["minOutputBound"] == [
            {"fieldPath": list(path), "value": value}
            for path, value in ec.min_output_bound_fields(contract)
        ]
        assert item["plannedTransmissions"] == 0
        for bound in item["minOutputBound"]:
            # production 선례(`GatewayClient.converse_quota_only`)와 같은 최소 비용 bound다.
            assert bound["value"] == 1


# ─────────────────────────────────────────────────────────────────
# task 12.2 — 보고서 본문 투영기: Known_Route 상태·evidence reference,
# model·route별 Effort_Support_Status·evidence reference, probe별 Sanitized_Schema
# (Requirement 11.9, 11.10, 11.12, 10.13, 10.14)
#
# 투영기는 백엔드 결과를 **읽기만** 한다. 아래 테스트는 evidence_collector가 만드는 결과
# 스키마와 같은 형태의 결과를 주입해(전송 없음) 보고서 절이 상태·evidence reference·
# Probe_ID·Sanitized_Schema만 옮기고 raw prompt·raw body·authorization은 옮기지 않는지
# 확인한다. Gateway는 호출하지 않으므로 통과 사실은 지원 근거가 아니다(Requirement 12.22).
# ─────────────────────────────────────────────────────────────────
SYM_RAW_MARKER = "sym-raw-must-not-be-recorded"


def _fake_route_result(route, *, status, transmissions=1, sequence=0, **extra):
    """전송된 route probe 결과 형태(보고 대상 필드 + raw 자리 표식).

    `rawBody`·`rawPrompt`·`authorization`은 투영기가 옮기지 않아야 하는 자리다 —
    표식이 보고서에 나타나면 정제 계약이 깨진 것이다.
    """
    result = {
        "routeKey": route,
        "modelId": SYM_MODEL_ID,
        "invocationModelId": SYM_MODEL_ID,
        "advertised": True,
        "status": status,
        "allowlist": str(contracts.Allowlist_Result.ALLOWED),
        "http": True,
        "validOutput": True,
        "terminalSuccess": True,
        "correctionUsed": False,
        "transmissions": transmissions,
        "productionPath": True,
        "evidenceEligible": True,
        "stateBearing": True,
        "category": None,
        "reason": ec.PROBE_OK,
        "probeId": ec.probe_id("route", SYM_MODEL_ID, route, sequence),
        "sanitizedSchema": runner.store.sanitized_schema({"output": {"text": "sym-out"}}),
        "contract": ec.candidate_route_contract(route, evidence_ref=SYM_EVIDENCE_ID),
        "notes": [],
        "rawBody": SYM_RAW_MARKER,
        "rawPrompt": SYM_RAW_MARKER,
        "authorization": SYM_RAW_MARKER,
    }
    result.update(extra)
    return result


def _fake_effort_summary(route, *, status, transmitted=True, **extra):
    """전송된 effort probe summary 형태(실제 field path·실제 value는 관측값이다)."""
    value_result = {
        "routeKey": route,
        "value": SYM_EFFORT_VALUES[0],
        "fieldPath": [SYM_EFFORT_FIELD],
        "status": status,
        "transmitted": transmitted,
        "http": transmitted,
        "validOutput": transmitted,
        "terminalSuccess": transmitted,
        "category": None,
        "rejection": None,
        "reason": ec.PROBE_OK,
        "probeId": ec.probe_id("effort", SYM_MODEL_ID, route, SYM_EFFORT_VALUES[0], 0, 0),
        "sanitizedSchema": runner.store.sanitized_schema({SYM_EFFORT_FIELD: SYM_EFFORT_VALUES[0]}),
        "rawBody": SYM_RAW_MARKER,
        "rawPrompt": SYM_RAW_MARKER,
    }
    summary = {
        "routeKey": route,
        "modelId": SYM_MODEL_ID,
        "status": status,
        "fieldPath": [SYM_EFFORT_FIELD],
        "valueType": str(contracts.Value_Type.STRING),
        "domainKind": str(contracts.Domain_Kind.ENUM),
        "requiredValues": list(SYM_EFFORT_VALUES),
        "verifiedValues": [SYM_EFFORT_VALUES[0]],
        "baselineSucceeded": True,
        "baselineSource": "route",
        "baseRouteStatus": str(contracts.Route_Support_Status.SUPPORTED),
        "contract": {"modelId": SYM_MODEL_ID, "fieldPath": [SYM_EFFORT_FIELD]},
        "contractMissing": [],
        "transmissions": 1 if transmitted else 0,
        "productionPath": True,
        "evidenceEligible": True,
        "reason": ec.PROBE_OK,
        "probeId": ec.probe_id("effort", SYM_MODEL_ID, route, 0),
        "notes": [],
        "results": [value_result],
        "authorization": SYM_RAW_MARKER,
    }
    summary.update(extra)
    return summary


def test_route_views_project_status_and_evidence_reference(user_data):
    context = _context(user_data)
    supported = str(contracts.Known_Route.CONVERSE)
    not_advertised = str(contracts.Known_Route.SSE_STREAM)
    context["state"]["routeResults"][LABELS[0]] = [
        _fake_route_result(supported, status=str(contracts.Route_Support_Status.SUPPORTED)),
        _fake_route_result(
            not_advertised,
            status=str(contracts.Route_Support_Status.NOT_ADVERTISED),
            transmissions=0,
            http=False,
            validOutput=False,
            terminalSuccess=False,
            allowlist=str(contracts.Allowlist_Result.UNVERIFIED),
            reason=ec.PROBE_NOT_ADVERTISED,
        ),
    ]
    context["state"]["routeRefs"][LABELS[0]] = SYM_EVIDENCE_ID

    views = runner.route_views(context)
    by_route = {item["routeKey"]: item for item in views}
    assert set(by_route) == {supported, not_advertised}

    # Known_Route별 상태와 evidence reference를 기록한다(Requirement 11.9).
    ok = by_route[supported]
    assert ok["status"] == str(contracts.Route_Support_Status.SUPPORTED)
    assert ok["allowlist"] == str(contracts.Allowlist_Result.ALLOWED)
    assert (ok["http"], ok["validOutput"], ok["terminalSuccess"]) == (True, True, True)
    assert ok["evidenceRef"] == SYM_EVIDENCE_ID
    assert ok["candidateLabel"] == LABELS[0]
    assert ok["modelId"] == SYM_MODEL_ID
    assert ok["contractConfirmed"] is True
    assert ok["transmissions"] == 1
    # usage·cost 미제공은 `notProvided`(Requirement 11.19, 11.20).
    assert ok["usage"] == contracts.NOT_PROVIDED and ok["cost"] == contracts.NOT_PROVIDED

    # 전송 0건 결과도 상태 절에는 남는다(`NOT_ADVERTISED`는 catalog가 말해준 근거다).
    absent = by_route[not_advertised]
    assert absent["status"] == str(contracts.Route_Support_Status.NOT_ADVERTISED)
    assert absent["transmissions"] == 0
    assert absent["reason"] == ec.PROBE_NOT_ADVERTISED

    # Probe_ID와 Sanitized_Schema만 옮긴다 — raw 자리는 어디에도 나타나지 않는다.
    assert ok["probeId"].startswith("prb1:sha256:")
    assert ok["sanitizedSchema"]["type"] == "object"
    assert SYM_RAW_MARKER not in json.dumps(views, ensure_ascii=False)


def test_effort_views_project_status_and_evidence_reference(user_data):
    context = _context(user_data)
    route = str(contracts.Known_Route.OPENAI_RESPONSES)
    context["state"]["effortResults"][LABELS[0]] = [
        _fake_effort_summary(route, status=str(contracts.Effort_Support_Status.SUPPORTED))
    ]
    context["state"]["effortRefs"][LABELS[0]] = SYM_EVIDENCE_ID

    views = runner.effort_views(context)
    assert len(views) == 1
    view = views[0]

    # model·route별 Effort_Support_Status와 evidence reference(Requirement 11.10).
    assert view["candidateLabel"] == LABELS[0]
    assert view["modelId"] == SYM_MODEL_ID
    assert view["routeKey"] == route
    assert view["status"] == str(contracts.Effort_Support_Status.SUPPORTED)
    assert view["evidenceRef"] == SYM_EVIDENCE_ID
    # 실제 field path·실제 value는 관측값 그대로다(Requirement 5.12, 5.13).
    assert view["fieldPath"] == [SYM_EFFORT_FIELD]
    assert view["verifiedValues"] == [SYM_EFFORT_VALUES[0]]
    assert view["requiredValues"] == list(SYM_EFFORT_VALUES)
    assert view["baselineSucceeded"] is True
    assert view["baseRouteStatus"] == str(contracts.Route_Support_Status.SUPPORTED)
    assert view["contractConfirmed"] is True

    # value별 결과도 Probe_ID·Sanitized_Schema만 담는다.
    assert len(view["values"]) == 1
    value = view["values"][0]
    assert value["value"] == SYM_EFFORT_VALUES[0]
    assert value["fieldPath"] == [SYM_EFFORT_FIELD]
    assert value["status"] == str(contracts.Effort_Support_Status.SUPPORTED)
    assert value["probeId"].startswith("prb1:sha256:")
    assert value["sanitizedSchema"]["type"] == "object"
    assert value["usage"] == contracts.NOT_PROVIDED and value["cost"] == contracts.NOT_PROVIDED
    assert SYM_RAW_MARKER not in json.dumps(views, ensure_ascii=False)


def test_probe_views_record_only_transmitted_probe_id_and_schema(user_data):
    context = _context(user_data)
    route = str(contracts.Known_Route.CONVERSE)
    effort_route = str(contracts.Known_Route.OPENAI_RESPONSES)
    context["state"]["routeResults"][LABELS[0]] = [
        _fake_route_result(route, status=str(contracts.Route_Support_Status.SUPPORTED)),
        _fake_route_result(
            str(contracts.Known_Route.SSE_STREAM),
            status=str(contracts.Route_Support_Status.NOT_ADVERTISED),
            transmissions=0,
        ),
    ]
    context["state"]["effortResults"][LABELS[0]] = [
        _fake_effort_summary(effort_route, status=str(contracts.Effort_Support_Status.SUPPORTED)),
        _fake_effort_summary(
            str(contracts.Known_Route.INVOKE),
            status=str(contracts.Effort_Support_Status.UNVERIFIED),
            transmitted=False,
        ),
    ]

    probes = runner.probe_views(context)
    # 전송이 발생한 probe만 담는다(전송 0건 결과는 상태 절에만 남는다 — Requirement 11.12).
    assert [(item["kind"], item["routeKey"]) for item in probes] == [
        (runner.PROBE_KIND_ROUTE, route),
        (runner.PROBE_KIND_EFFORT_VALUE, effort_route),
    ]
    for item in probes:
        # 보고서 probe 항목은 Probe_ID와 Sanitized_Schema만 담는다(raw 자리 없음).
        assert set(item) == {
            "probeId",
            "kind",
            "candidateLabel",
            "modelId",
            "routeKey",
            "transmissions",
            "sanitizedSchema",
        }
        assert item["probeId"].startswith("prb1:sha256:")
        assert item["modelId"] == SYM_MODEL_ID
        assert item["sanitizedSchema"]["type"] == "object"
    assert SYM_RAW_MARKER not in json.dumps(probes, ensure_ascii=False)


# ─────────────────────────────────────────────────────────────────
# task 12.2 — usage·cost는 Gateway 제공값만 기록(미제공은 `notProvided`)
# (Requirement 11.17, 11.18, 11.19, 11.20)
# ─────────────────────────────────────────────────────────────────
def test_gateway_usage_cost_defaults_to_not_provided(user_data):
    context = _context(user_data)
    context["state"]["routeResults"][LABELS[0]] = [
        _fake_route_result(
            str(contracts.Known_Route.CONVERSE),
            status=str(contracts.Route_Support_Status.SUPPORTED),
        )
    ]
    # Gateway가 usage·cost를 주지 않았으면 추정하지 않는다.
    assert runner.gateway_usage_cost(context) == (
        contracts.NOT_PROVIDED,
        contracts.NOT_PROVIDED,
    )


def test_gateway_usage_cost_records_only_gateway_provided_values(user_data):
    context = _context(user_data)
    usage = {"sym-usage-field": 3}
    cost = {"sym-cost-field": 0.5}
    context["state"]["routeResults"][LABELS[0]] = [
        _fake_route_result(
            str(contracts.Known_Route.CONVERSE),
            status=str(contracts.Route_Support_Status.SUPPORTED),
            usage=usage,
        )
    ]
    context["state"]["effortResults"][LABELS[0]] = [
        _fake_effort_summary(
            str(contracts.Known_Route.OPENAI_RESPONSES),
            status=str(contracts.Effort_Support_Status.SUPPORTED),
        )
    ]
    context["state"]["effortResults"][LABELS[0]][0]["results"][0]["cost"] = cost

    observed_usage, observed_cost = runner.gateway_usage_cost(context)
    # Gateway가 제공한 값만 그대로 기록한다(집계는 evidence_collector에 위임).
    assert observed_usage == usage
    assert observed_cost == cost
