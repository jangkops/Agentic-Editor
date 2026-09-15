"""Evidence_Collector 단위 테스트 — 예산·상태 보존·정제·mock 배제.

Feature: gateway-models-effort-support (task 11.4)
대상: ai_engine.capability.evidence_collector (discovery·route probe·effort probe 오케스트레이션)

검증 범위
  - 예산: `NOT_ADVERTISED` route 전송 0건(Requirement 4.17), 조합당 성공 generation 1회
    (4.24), 명시적·교정 가능 validation error에만 교정 요청 최대 1회(4.25)
  - 상태 보존: transient·empty·partial·timeout 결과가 route·effort 상태를 강등하지 않음
    (4.20~4.23, 5.16), 부분 domain 검증은 `UNVERIFIED` 유지(5.17), effort 실패가 base
    Route_Support_Status를 강등하지 않음(5.20)
  - 무-effort Baseline_Request_Body 성공을 확인하지 못하면 effort probe 전송 0건(5.8)
  - 정제: 저장 record에 credential·authorization·cookie·signature·raw prompt·raw body가
    없고 Probe_ID와 Sanitized_Schema만 남음(10.12, 10.13, 10.14)
  - mock 성공은 activation evidence로 승격되지 않음(4.4, 12.22)

이 테스트는 실제 Gateway를 호출하지 않는다. 전송 계층만 mock으로 대체하고 요청 body는
production Request_Builder seam(`_build_payload`·effort 주입)이 만든 것을 그대로 검사한다.
통과 사실은 Gateway 지원 근거가 **아니다** — Gateway 지원 주장은 작업 18의 production path
probe로만 확정한다. model ID·provider·effort field path·effort 허용값은 확정 상수로 두지 않고
무작위 심볼 자리표시자만 사용한다. 영속 쓰기는 `tmp_path`를 userData 루트로 하는
`store.CapabilityStore`로 격리한다.

실행: ai_engine/.venv/bin/python -m pytest scripts/test_capability_evidence_collector.py -q
"""
from __future__ import annotations

import asyncio
import json
import os
import sys
from types import SimpleNamespace

import pytest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from ai_engine.capability import capability_map, contracts, failure_handler  # noqa: E402
from ai_engine.capability import evidence_collector as ec  # noqa: E402
from ai_engine.capability import request_builder as rb  # noqa: E402
from ai_engine.capability import store as capability_store  # noqa: E402

gateway_module = pytest.importorskip("ai_engine.gateway_module")

# ─────────────────────────────────────────────────────────────────
# 자리표시자 심볼 — 실제 model identity·field path·허용값이 아니다.
# (evidence만 실제 값을 채운다 — 라벨·상수에서 유도하지 않는다.)
# ─────────────────────────────────────────────────────────────────
LABEL = "sym-label-e"
MODEL_ID = "sym-vendor.sym-model-e"
PROVIDER = "sym-provider-e"
CATALOG_FP = "cat1:sha256:" + "e" * 64
REVISION = "sym-revision-e"
RUN_ID = "sym-run-e"
INTERPRETER = "/sym/venv/bin/python"
NOW = "2026-08-03T00:00:00.000000Z"

EFFORT_PATH = ["sym-outer", "sym-effort-field"]
EFFORT_VALUES = ["sym-effort-1", "sym-effort-2", "sym-effort-3"]

# 정제 검증용 마커 — 저장 record에 남아 있으면 안 되는 값들.
SECRET_AUTHZ = "sym-secret-authorization-value"
SECRET_COOKIE = "sym-secret-cookie-value"
SECRET_SIGNATURE = "sym-secret-signature-value"
SECRET_TOKEN = "sym-secret-apitoken-value"
RAW_PROMPT = "sym-raw-prompt-marker"
RAW_BODY_MARKER = "sym-raw-body-marker"

CONVERSE = str(contracts.Known_Route.CONVERSE)
RESPONSES = str(contracts.Known_Route.OPENAI_RESPONSES)
JOBS = str(contracts.Known_Route.OPENAI_RESPONSES_JOBS)
SSE_STREAM = str(contracts.Known_Route.SSE_STREAM)

#: transport가 내부에서 교정해 실제로 전송한 ID를 모사하는 자리표시자(실제 prefix 아님).
SENT_MODEL_ID = "sym-corrected.sym-vendor.sym-model-e"

SUPPORTED = str(contracts.Route_Support_Status.SUPPORTED)
UNVERIFIED = str(contracts.Route_Support_Status.UNVERIFIED)
NOT_ADVERTISED = str(contracts.Route_Support_Status.NOT_ADVERTISED)

E_SUPPORTED = str(contracts.Effort_Support_Status.SUPPORTED)
E_UNSUPPORTED = str(contracts.Effort_Support_Status.UNSUPPORTED)
E_UNVERIFIED = str(contracts.Effort_Support_Status.UNVERIFIED)


# ─────────────────────────────────────────────────────────────────
# mock transport — 전송 계층만 대체하고 body 생성은 production seam 그대로
# ─────────────────────────────────────────────────────────────────
def _fake_client_class():
    """production probe client를 상속해 transport 메서드만 대체한 클래스."""
    base_cls = ec.probe_client_class()

    class FakeClient(base_cls):  # type: ignore[misc, valid-type]
        def __init__(self, binding=None, script=None):
            base = SimpleNamespace(
                gateway_url="https://sym-endpoint.invalid/v1",
                region="sym-region",
                aws_profile="sym-profile",
                bedrock_user="",
            )
            super().__init__(base, binding, None)
            self.calls: list[dict] = []
            self.script: dict[str, list] = {
                key: list(value) for key, value in (script or {}).items()
            }

        def _next(self, route_key):
            queue = self.script.get(route_key) or []
            item = queue.pop(0) if len(queue) > 1 else (queue[0] if queue else None)
            if isinstance(item, BaseException):
                raise item
            return item

        def _record(self, route_key, model_id, body):
            self.calls.append({"route": route_key, "modelId": model_id, "body": body})

        def sent(self, route_key=None):
            return [c for c in self.calls if route_key is None or c["route"] == route_key]

        def bodies(self, route_key=None):
            return [c["body"] for c in self.sent(route_key)]

        # -- transport 대체 (기존 메서드 시그니처 그대로) ----------------
        async def converse(self, model_id, messages, system_prompt="", tool_config=None):
            self._record(
                CONVERSE,
                model_id,
                self._build_payload(model_id, messages, system_prompt, tool_config),
            )
            return self._next(CONVERSE)

        async def openai_responses_call(self, body, timeout=120):
            self._record(RESPONSES, body.get("model") if isinstance(body, dict) else "", body)
            return self._next(RESPONSES)

        async def stream_sse_realtime(
            self, model_id, messages, system_prompt="", tool_config=None
        ):
            self._record(
                SSE_STREAM,
                model_id,
                self._build_payload(model_id, messages, system_prompt, tool_config),
            )
            item = self._next(SSE_STREAM)
            events = item.get("events") if isinstance(item, dict) else item
            for event in events or []:
                yield event

    return FakeClient


FakeClient = _fake_client_class()


# ─────────────────────────────────────────────────────────────────
# 헬퍼
# ─────────────────────────────────────────────────────────────────
def _collector(**kwargs):
    return ec.EvidenceCollector(
        env_identity={
            "gatewayEnvironmentId": "sym-env",
            "endpointIdentity": "https://sym-endpoint.invalid/v1",
            "region": "sym-region",
        },
        revision=REVISION,
        run_id=RUN_ID,
        interpreter_path=INTERPRETER,
        now_fn=lambda: NOW,
        **kwargs,
    )


def _effort_declaration(values=EFFORT_VALUES, *, route=CONVERSE):
    """catalog가 **명시한** effort 선언(field path·value type·complete domain)."""
    return {
        route: {
            "fieldPath": list(EFFORT_PATH),
            "valueType": str(contracts.Value_Type.STRING),
            "domainKind": str(contracts.Domain_Kind.ENUM),
            "enumValues": list(values),
        }
    }


def _model(*, routes=(CONVERSE,), effort=None, model_id=MODEL_ID):
    """discovery 결과 형태(라벨은 검색 라벨일 뿐 identity가 아니다)."""
    return {
        "candidateLabel": LABEL,
        "found": True,
        "modelId": model_id,
        "provider": PROVIDER,
        "sourceKind": str(contracts.Source_Kind.CATALOG),
        "catalogFingerprint": CATALOG_FP,
        "advertisedRoutes": list(routes),
        "advertisedEffort": {} if effort is None else effort,
        "probeEligible": True,
    }


def _converse_ok(text="sym-output"):
    return {
        "decision": "ALLOW",
        "output": {"message": {"content": [{"text": text}]}},
        "usage": {"inputTokens": 1, "outputTokens": 1},
        "estimated_cost_krw": 1,
    }


def _converse_ok_with_secrets():
    """Gateway 응답에 credential 계열 field가 섞여 있어도 record에 남지 않아야 한다."""
    body = _converse_ok()
    body.update(
        {
            "authorization": SECRET_AUTHZ,
            "cookie": SECRET_COOKIE,
            "x-amz-signature": SECRET_SIGNATURE,
        }
    )
    return body


def _converse_empty():
    return {"decision": "ALLOW", "output": {"message": {"content": []}}}


def _converse_partial():
    return {"decision": "ACCEPTED", "job_id": "sym-job-e"}


def _run(coro):
    return asyncio.run(coro)


def _supported_entry_map(collector, *, route_status=None, effort_status=None):
    """이미 검증된 상태를 가진 map(강등 여부를 관측하기 위한 기준선)."""
    base_map = collector.initial_map([LABEL], now=NOW)
    entry = capability_map.find_entry(base_map, candidate_label=LABEL)
    entry["modelId"] = MODEL_ID
    entry["provider"] = PROVIDER
    if route_status:
        entry["routes"][CONVERSE]["status"] = route_status
    if effort_status:
        entry["effort"][CONVERSE]["status"] = effort_status
    capability_map.recompute_fingerprint(entry)
    return base_map


def _keys_of(value):
    """중첩 구조의 모든 dict 키를 모은다(정제 검증용)."""
    keys: list[str] = []
    if isinstance(value, dict):
        for key, item in value.items():
            keys.append(key)
            keys.extend(_keys_of(item))
    elif isinstance(value, list):
        for item in value:
            keys.extend(_keys_of(item))
    return keys


def _with_secrets(result):
    """probe 결과에 정제 대상(credential·raw prompt·raw body)을 섞는다."""
    out = dict(result)
    out.update(
        {
            "authorization": SECRET_AUTHZ,
            "Cookie": SECRET_COOKIE,
            "x-amz-signature": SECRET_SIGNATURE,
            "apitoken": SECRET_TOKEN,
            "prompt": RAW_PROMPT,
            "messages": [{"role": "user", "content": [{"text": RAW_PROMPT}]}],
            "requestBody": {"sym-field": RAW_BODY_MARKER},
            "rawResponse": {"sym-out": RAW_BODY_MARKER},
        }
    )
    return out


def _probe_route_and_effort(collector, client, *, budget=None, model=None):
    """route baseline 성공 → effort 전체 domain 검증(mock)까지 한 번에 수행한다."""
    budget = budget or ec.ProbeBudget()
    model = model or _model(effort=_effort_declaration())
    route_result = _run(collector.probe_route(model, CONVERSE, transport=client, budget=budget))
    summary = _run(
        collector.probe_effort(
            model, CONVERSE, route_result=route_result, transport=client, budget=budget
        )
    )
    return model, route_result, summary


# ═════════════════════════════════════════════════════════════════
# 예산 (Requirement 4.17, 4.24, 4.25)
# ═════════════════════════════════════════════════════════════════
def test_not_advertised_routes_never_transmit_and_keep_budget_at_zero():
    """광고되지 않은 Known_Route에는 Gateway_Probe를 전송하지 않는다(Requirement 4.17)."""
    client = FakeClient(script={CONVERSE: [_converse_ok()]})
    collector = _collector()
    budget = ec.ProbeBudget()
    results = _run(collector.probe_routes(_model(routes=()), transport=client, budget=budget))

    assert [item["routeKey"] for item in results] == list(contracts.KNOWN_ROUTES)
    assert {item["status"] for item in results} == {NOT_ADVERTISED}
    assert {item["reason"] for item in results} == {ec.PROBE_NOT_ADVERTISED}
    assert all(item["transmissions"] == 0 for item in results)
    assert client.calls == []
    assert budget.snapshot()["totalTransmissions"] == 0


def test_effort_probe_is_not_transmitted_for_unadvertised_route():
    """광고되지 않은 route는 effort probe도 만들지 않는다(전송 0건)."""
    client = FakeClient(script={CONVERSE: [_converse_ok()]})
    summary = _run(
        _collector().probe_effort(
            _model(routes=(RESPONSES,), effort=_effort_declaration()),
            CONVERSE,
            transport=client,
        )
    )
    assert summary["reason"] == ec.EFFORT_PROBE_NOT_ADVERTISED
    assert summary["transmissions"] == 0
    assert summary["status"] == E_UNVERIFIED
    assert client.calls == []


def test_success_generation_is_limited_to_one_per_combination():
    """조합(model·route·effort value)당 성공 generation은 1회다(Requirement 4.24)."""
    client = FakeClient(script={CONVERSE: [_converse_ok()]})
    collector = _collector()
    budget = ec.ProbeBudget()
    model, route_result, summary = _probe_route_and_effort(collector, client, budget=budget)

    # 같은 조합 재검증은 전송을 만들지 않는다.
    again = _run(collector.probe_route(model, CONVERSE, transport=client, budget=budget))
    repeat = _run(
        collector.probe_effort(
            model, CONVERSE, route_result=route_result, transport=client, budget=budget
        )
    )

    assert route_result["status"] == SUPPORTED
    assert route_result["transmissions"] == 1
    assert again["transmissions"] == 0
    assert again["reason"] == ec.PROBE_BUDGET_SUCCESS_USED
    assert summary["status"] == E_SUPPORTED
    assert summary["transmissions"] == len(EFFORT_VALUES)
    assert repeat["transmissions"] == 0
    assert repeat["reason"] == ec.EFFORT_PROBE_BUDGET_EXHAUSTED

    snapshot = budget.snapshot()
    # ∅(무-effort) 조합 1개 + effort value 조합 3개, 각각 성공 1회씩.
    assert len(snapshot["successes"]) == 1 + len(EFFORT_VALUES)
    assert set(snapshot["successes"].values()) == {1}
    assert snapshot["totalTransmissions"] == 1 + len(EFFORT_VALUES)
    assert len(client.sent(CONVERSE)) == 1 + len(EFFORT_VALUES)


def test_each_domain_value_is_probed_exactly_once_through_production_seam():
    """domain이 요구하는 각 value가 production 주입 seam으로 정확히 1회 검증된다(5.9, 5.12)."""
    client = FakeClient(script={CONVERSE: [_converse_ok()]})
    _, _, summary = _probe_route_and_effort(_collector(), client)

    bodies = client.bodies(CONVERSE)
    baseline_body, effort_bodies = bodies[0], bodies[1:]
    # 무-effort baseline body에는 effort field가 없다.
    assert rb.count_key_occurrences(baseline_body, EFFORT_PATH[-1]) == 0
    # effort 전송은 계약 field path에 값을 정확히 1회 기록한다.
    assert [rb.count_key_occurrences(body, EFFORT_PATH[-1]) for body in effort_bodies] == [1] * len(
        EFFORT_VALUES
    )
    assert {rb.value_at_path(body, EFFORT_PATH) for body in effort_bodies} == set(EFFORT_VALUES)
    # 성공 결과는 실제 전송 body에서 관측한 field path·value만 기록한다.
    assert {tuple(item["fieldPath"]) for item in summary["results"]} == {tuple(EFFORT_PATH)}
    assert set(summary["verifiedValues"]) == set(EFFORT_VALUES)
    assert set(summary["contract"]["verifiedValues"]) == set(EFFORT_VALUES)


def test_correction_request_is_limited_to_one_per_combination():
    """명시적·교정 가능 validation error에만 동일 조합 교정 1회(Requirement 4.25)."""
    invalid = {"decision": "ERROR", "error": "ValidationException: sym invalid request"}
    client = FakeClient(script={CONVERSE: [invalid]})
    budget = ec.ProbeBudget()
    result = _run(_collector().probe_route(_model(), CONVERSE, transport=client, budget=budget))

    assert result["category"] == failure_handler.REQUEST_VALIDATION
    assert result["correctionUsed"] is True
    assert result["transmissions"] == 2  # 최초 1회 + 교정 1회
    assert len(client.sent(CONVERSE)) == 2
    assert sum(budget.snapshot()["corrections"].values()) == 1
    assert result["status"] == UNVERIFIED  # 교정 후에도 지원은 확정되지 않는다


def test_transient_failure_does_not_consume_correction_budget():
    """교정 가능 범주가 아니면 교정 전송을 만들지 않는다(예산 소모 0)."""
    client = FakeClient(script={CONVERSE: [gateway_module.SyncTimeout("sym timeout")]})
    budget = ec.ProbeBudget()
    result = _run(_collector().probe_route(_model(), CONVERSE, transport=client, budget=budget))

    assert result["category"] == failure_handler.TRANSIENT
    assert result["correctionUsed"] is False
    assert result["transmissions"] == 1
    assert sum(budget.snapshot()["corrections"].values()) == 0


# ═════════════════════════════════════════════════════════════════
# 상태 보존 — transient·empty·partial·timeout (Requirement 4.20~4.23)
# ═════════════════════════════════════════════════════════════════
@pytest.mark.parametrize(
    "scenario,script_item",
    [
        ("transient", gateway_module.SyncTimeout("sym transient timeout")),
        ("empty", _converse_empty()),
        ("partial", _converse_partial()),
        ("timeout", gateway_module.JobTimeout("sym job timeout")),
    ],
)
def test_transient_empty_partial_timeout_never_downgrade_a_verified_route(scenario, script_item):
    """이 네 결과는 `UNVERIFIED`로 남고 이전 `SUPPORTED` route를 강등하지 않는다."""
    client = FakeClient(script={CONVERSE: [script_item]})
    collector = _collector()
    result = _run(collector.probe_route(_model(), CONVERSE, transport=client, budget=ec.ProbeBudget()))

    assert result["status"] == UNVERIFIED, scenario
    assert result["terminalSuccess"] is False
    assert result["category"] in ec.STATE_PRESERVING_CATEGORIES
    assert result["stateBearing"] is False
    # 상태를 바꾸지 않는 범주는 record에 담지 않는다 → 이전 상태가 유지된다.
    assert ec.recordable_results([result]) == []
    assert ec.route_contracts_for_record([result]) == {}

    updated, _ = collector.apply_route_probes(
        _supported_entry_map(collector, route_status=SUPPORTED), _model(), [result], now=NOW
    )
    entry = capability_map.find_entry(updated, candidate_label=LABEL)
    assert entry["routes"][CONVERSE]["status"] == SUPPORTED, scenario


@pytest.mark.parametrize(
    "scenario,script_item,expected_http,expected_decision",
    [
        ("transient", gateway_module.SyncTimeout("sym transient timeout"), False, ""),
        ("empty", _converse_empty(), True, "ALLOW"),
        ("partial", _converse_partial(), True, "ACCEPTED"),
        ("timeout", gateway_module.JobTimeout("sym job timeout"), False, ""),
    ],
)
def test_each_preserving_scenario_is_recorded_as_distinct_independent_outcomes(
    scenario, script_item, expected_http, expected_decision
):
    """네 결과를 HTTP·Valid_Output·Terminal_Success 독립 결과로 구분해 기록한다.

    transport 실패(transient·timeout)는 HTTP 성공조차 없고, empty·partial은 HTTP는
    성공했으나 Valid_Output이 없다 — 서로 다른 관측이 같은 `UNVERIFIED`로 수렴하는지
    확인한다(Requirement 4.5, 4.6, 4.7, 4.20, 4.21, 4.22, 4.23).
    """
    client = FakeClient(script={CONVERSE: [script_item]})
    result = _run(
        _collector().probe_route(_model(), CONVERSE, transport=client, budget=ec.ProbeBudget())
    )

    assert result["http"] is expected_http, scenario
    assert result["validOutput"] is False, scenario
    assert result["terminalSuccess"] is False, scenario
    assert result["observations"]["decision"] == expected_decision, scenario
    assert result["observations"]["contentBlocks"] == 0, scenario
    assert result["allowlist"] == str(contracts.Allowlist_Result.UNVERIFIED), scenario
    assert result["transmissions"] == 1, scenario  # 실패가 추가 전송을 만들지 않는다
    assert result["status"] == UNVERIFIED, scenario
    assert result["contract"] is None, scenario  # 확정 계약은 성공만 만든다


def test_effort_transient_keeps_status_and_never_downgrades_base_route():
    """effort transient는 effort 상태를 유지하고 base route도 강등하지 않는다(5.16, 5.20)."""
    client = FakeClient(
        script={CONVERSE: [_converse_ok(), gateway_module.SyncTimeout("sym effort timeout")]}
    )
    collector = _collector()
    model = _model(effort=_effort_declaration(values=[EFFORT_VALUES[0]]))
    budget = ec.ProbeBudget()
    route_result = _run(collector.probe_route(model, CONVERSE, transport=client, budget=budget))
    summary = _run(
        collector.probe_effort(
            model, CONVERSE, route_result=route_result, transport=client, budget=budget
        )
    )

    assert route_result["status"] == SUPPORTED
    assert summary["baselineSucceeded"] is True
    assert summary["baselineSource"] == ec.BASELINE_FROM_ROUTE_RESULT
    assert summary["transmissions"] == 1
    assert summary["status"] == E_UNVERIFIED
    assert summary["results"][0]["category"] == failure_handler.TRANSIENT
    assert summary["results"][0]["stateBearing"] is False
    assert ec.recordable_effort_results([summary]) == []
    assert ec.effort_contracts_for_record([summary]) == {}
    # effort 결과는 base route 상태를 담지 않는다(강등 경로 부재).
    assert summary["baseRouteStatus"] == SUPPORTED
    assert ec.NOTE_BASE_ROUTE_STATUS_PRESERVED in summary["notes"]

    base_map = _supported_entry_map(collector, route_status=SUPPORTED, effort_status=E_SUPPORTED)
    updated, _ = collector.apply_effort_probes(base_map, model, [summary], now=NOW)
    entry = capability_map.find_entry(updated, candidate_label=LABEL)
    assert entry["routes"][CONVERSE]["status"] == SUPPORTED
    assert entry["effort"][CONVERSE]["status"] == E_SUPPORTED


def test_partial_domain_verification_keeps_effort_unverified():
    """일부 value만 검증되면 `UNVERIFIED`를 유지하고 성공 결과도 승격 근거가 아니다(5.17)."""
    client = FakeClient(
        script={
            CONVERSE: [
                _converse_ok(),  # 무-effort baseline
                _converse_ok(),  # effort value 1 성공
                gateway_module.SyncTimeout("sym timeout"),  # effort value 2 transient
            ]
        }
    )
    collector = _collector()
    model = _model(effort=_effort_declaration())
    budget = ec.ProbeBudget()
    route_result = _run(collector.probe_route(model, CONVERSE, transport=client, budget=budget))
    summary = _run(
        collector.probe_effort(
            model, CONVERSE, route_result=route_result, transport=client, budget=budget
        )
    )

    assert summary["status"] == E_UNVERIFIED
    assert summary["transmissions"] == 2  # 세 번째 value는 전송하지 않는다
    assert set(summary["requiredValues"]) == set(EFFORT_VALUES)
    assert set(summary["verifiedValues"]) == {EFFORT_VALUES[0]}
    assert summary["contract"] is None  # 확정 계약은 전체 성공에만 채운다
    assert ec.NOTE_EFFORT_DOMAIN_PARTIAL in summary["notes"]
    assert ec.NOTE_EFFORT_PROBE_STOPPED in summary["notes"]

    statuses = [item["status"] for item in summary["results"]]
    assert statuses == [E_SUPPORTED, E_UNVERIFIED, E_UNVERIFIED]
    assert summary["results"][2]["transmitted"] is False
    assert summary["results"][2]["reason"] == ec.EFFORT_PROBE_NOT_PROBED
    # 부분 검증 상태에서는 성공 결과조차 record에 담지 않는다(승격 차단).
    assert ec.recordable_effort_results([summary]) == []
    assert ec.effort_contracts_for_record([summary]) == {}

    base_map = _supported_entry_map(collector, route_status=SUPPORTED, effort_status=E_SUPPORTED)
    updated, _ = collector.apply_effort_probes(base_map, model, [summary], now=NOW)
    entry = capability_map.find_entry(updated, candidate_label=LABEL)
    assert entry["effort"][CONVERSE]["status"] == E_SUPPORTED  # 이전 상태 유지
    assert entry["routes"][CONVERSE]["status"] == SUPPORTED


@pytest.mark.parametrize(
    "statuses,expected",
    [
        ((E_SUPPORTED, E_SUPPORTED, E_SUPPORTED), E_SUPPORTED),
        ((E_SUPPORTED, E_UNVERIFIED, E_UNVERIFIED), E_UNVERIFIED),
        ((E_SUPPORTED, E_UNSUPPORTED, E_SUPPORTED), E_UNSUPPORTED),
        ((E_UNVERIFIED, E_UNVERIFIED, E_UNVERIFIED), E_UNVERIFIED),
    ],
)
def test_aggregate_effort_status_requires_every_required_value(statuses, expected):
    """집계 규칙: unknown field 우선, 전체 성공만 `SUPPORTED`(5.13, 5.14, 5.17)."""
    results = [
        {"routeKey": CONVERSE, "value": value, "status": status}
        for value, status in zip(EFFORT_VALUES, statuses)
    ]
    assert ec.aggregate_effort_status(EFFORT_VALUES, results) == expected


# ═════════════════════════════════════════════════════════════════
# 무-effort baseline 미확인 → effort 전송 0건 (Requirement 5.8)
# ═════════════════════════════════════════════════════════════════
def test_failed_route_result_blocks_every_effort_transmission():
    """주어진 route 결과가 실패면 effort probe를 아예 만들지 않는다."""
    collector = _collector()
    model = _model(effort=_effort_declaration())
    budget = ec.ProbeBudget()
    baseline_client = FakeClient(script={CONVERSE: [_converse_empty()]})
    route_result = _run(
        collector.probe_route(model, CONVERSE, transport=baseline_client, budget=budget)
    )
    assert route_result["status"] == UNVERIFIED

    effort_client = FakeClient(script={CONVERSE: [_converse_ok()]})
    summary = _run(
        collector.probe_effort(
            model, CONVERSE, route_result=route_result, transport=effort_client, budget=budget
        )
    )

    assert summary["baselineSucceeded"] is False
    assert summary["baselineSource"] == ec.BASELINE_FROM_ROUTE_RESULT
    assert summary["reason"] == ec.EFFORT_PROBE_BASELINE_UNVERIFIED
    assert summary["transmissions"] == 0
    assert summary["results"] == []
    assert summary["status"] == E_UNVERIFIED
    assert effort_client.calls == []


def test_effort_probe_stops_after_failed_baseline_probe():
    """route 결과가 없으면 무-effort baseline만 1회 전송하고 effort는 전송하지 않는다."""
    client = FakeClient(script={CONVERSE: [_converse_empty()]})
    summary = _run(
        _collector().probe_effort(
            _model(effort=_effort_declaration()),
            CONVERSE,
            transport=client,
            budget=ec.ProbeBudget(),
        )
    )

    assert summary["baselineSource"] == ec.BASELINE_FROM_PROBE
    assert summary["baselineSucceeded"] is False
    assert summary["reason"] == ec.EFFORT_PROBE_BASELINE_UNVERIFIED
    assert summary["transmissions"] == 0
    assert summary["baselineResult"]["transmissions"] == 1
    # 전송된 body는 무-effort baseline 하나뿐이다(effort field 0회).
    assert len(client.sent(CONVERSE)) == 1
    assert all(
        rb.count_key_occurrences(body, EFFORT_PATH[-1]) == 0 for body in client.bodies(CONVERSE)
    )


# ═════════════════════════════════════════════════════════════════
# mock 성공은 activation evidence가 아니다 (Requirement 4.4, 12.22)
# ═════════════════════════════════════════════════════════════════
def test_mock_success_is_never_promoted_to_activation_evidence():
    """mock transport 성공은 route·effort 모두 activation evidence에서 제외된다."""
    client = FakeClient(script={CONVERSE: [_converse_ok()]})
    collector = _collector()
    model, route_result, summary = _probe_route_and_effort(collector, client)

    # 결과 자체는 성공이지만 production 경로가 아니다.
    assert route_result["status"] == SUPPORTED
    assert summary["status"] == E_SUPPORTED
    for item in (route_result, summary):
        assert item["productionPath"] is False
        assert item["evidenceEligible"] is False
        assert ec.NOTE_NON_PRODUCTION_TRANSPORT in item["notes"]
    assert all(child["evidenceEligible"] is False for child in summary["results"])

    assert ec.evidence_eligible_results([route_result]) == []
    assert ec.recordable_results([route_result]) == []
    assert ec.recordable_effort_results([summary]) == []
    assert ec.route_contracts_for_record([route_result]) == {}
    assert ec.effort_contracts_for_record([summary]) == {}

    record = collector.effort_verification_record(
        model, [summary], route_results=[route_result], verified_at=NOW
    )
    assert contracts.validate_verification_record(record) == []
    assert record["routeResults"] == []
    assert record["effortResults"] == []
    assert record["invocationModelIds"] == []
    assert record["routeCompleteness"] is False
    assert record["usage"] == contracts.NOT_PROVIDED
    assert record["cost"] == contracts.NOT_PROVIDED

    updated, records = collector.apply_effort_probes(
        collector.initial_map([LABEL], now=NOW),
        model,
        [summary],
        route_results=[route_result],
        now=NOW,
    )
    entry = capability_map.find_entry(updated, candidate_label=LABEL)
    assert entry["verificationStatus"] != str(contracts.Verification_Status.VERIFIED)
    assert entry["routes"][CONVERSE]["status"] != SUPPORTED
    assert entry["effort"][CONVERSE]["status"] == E_UNVERIFIED
    assert entry["effort"][CONVERSE]["contract"] is None
    assert records and records[0]["evidenceRecordId"].startswith("evr1:sha256:")


# ═════════════════════════════════════════════════════════════════
# 정제 (Requirement 10.12, 10.13, 10.14)
# ═════════════════════════════════════════════════════════════════
def _record_with_secrets(collector, client):
    """credential·raw prompt·raw body가 섞인 probe 결과로 record를 만든다."""
    model, route_result, summary = _probe_route_and_effort(collector, client)
    return model, collector.to_verification_record(
        candidate_label=model["candidateLabel"],
        model_id=model["modelId"],
        provider=model["provider"],
        catalog_fingerprint=model["catalogFingerprint"],
        route_results=[_with_secrets(route_result)],
        effort_results=[_with_secrets(summary["results"][0])],
        verified_at=NOW,
    )


def test_persisted_record_has_no_credentials_raw_prompt_or_raw_body(tmp_path):
    """저장 record에 비밀정보·원문이 남지 않고 Probe_ID·Sanitized_Schema만 남는다."""
    store_obj = capability_store.CapabilityStore(user_data_root=tmp_path)
    collector = _collector(store_obj=store_obj)
    client = FakeClient(script={CONVERSE: [_converse_ok_with_secrets()]})
    _, record = _record_with_secrets(collector, client)

    assert contracts.validate_verification_record(record) == []
    assert ec.sanitization_violations(record) == []

    path = collector.persist_record(record)
    assert store_obj.list_evidence_ids()
    assert str(path).startswith(str(tmp_path))
    text = path.read_text(encoding="utf-8")

    # 1) credential·authorization·cookie·signature 값과 raw 원문이 전부 사라졌다.
    for secret in (
        SECRET_AUTHZ,
        SECRET_COOKIE,
        SECRET_SIGNATURE,
        SECRET_TOKEN,
        RAW_PROMPT,
        RAW_BODY_MARKER,
    ):
        assert secret not in text
    # Minimal_Request의 입력 원문도 기록되지 않는다(Probe_ID로만 남는다).
    assert ec.PROBE_INPUT_TEXT not in text

    persisted = json.loads(text)
    assert ec.sanitization_violations(persisted) == []
    dropped = [key for key in _keys_of(persisted) if capability_store.classify_key(key) == "DROP"]
    assert dropped == []

    # 2) raw prompt 자리는 Probe_ID, raw body 자리는 Sanitized_Schema다.
    route_item = persisted["routeResults"][0]
    assert route_item["probeId"].startswith(ec.PROBE_ID_PREFIX)
    assert route_item["prompt"] == route_item["probeId"]
    assert route_item["messages"] == route_item["probeId"]
    schema = route_item["sanitizedSchema"]
    assert schema["requestBody"]["type"] == "object"
    assert list(schema["requestBody"]["fields"]) == ["sym-field"]
    assert schema["rawResponse"]["type"] == "object"
    # probe가 만든 request·response schema는 field 이름·타입만 담는다.
    assert schema["request"]["fields"]["modelId"] == {"type": "string", "length": len(MODEL_ID)}
    assert "authorization" not in schema["response"]["fields"]
    assert "cookie" not in schema["response"]["fields"]

    effort_item = persisted["effortResults"][0]
    assert effort_item["probeId"].startswith(ec.PROBE_ID_PREFIX)
    assert set(contracts.REQUIRED_EFFORT_RESULT_FIELDS) <= set(effort_item)
    assert effort_item["fieldPath"] == EFFORT_PATH
    assert effort_item["value"] == EFFORT_VALUES[0]


def test_persist_record_refuses_unsanitized_record(tmp_path):
    """정제되지 않은 값이 남아 있으면 기록하지 않고 거부한다."""
    store_obj = capability_store.CapabilityStore(user_data_root=tmp_path)
    collector = _collector(store_obj=store_obj)
    client = FakeClient(script={CONVERSE: [_converse_ok()]})
    _, record = _record_with_secrets(collector, client)
    record["routeResults"][0]["authorization"] = SECRET_AUTHZ

    with pytest.raises(ec.SanitizationError) as excinfo:
        collector.persist_record(record)
    assert any("credential-field" in item for item in excinfo.value.violations)
    assert store_obj.list_evidence_ids() == []


def test_effort_result_projection_keeps_only_schema_fields():
    """Verification_Record 투영은 스키마 필드와 비민감 관측치만 남긴다(10.13, 10.14)."""
    client = FakeClient(script={CONVERSE: [_converse_ok()]})
    _, _, summary = _probe_route_and_effort(_collector(), client)
    projected = ec.effort_result_for_record(summary["results"][0])

    assert set(projected) == set(contracts.REQUIRED_EFFORT_RESULT_FIELDS) | {"observations"}
    assert projected["probeId"].startswith(ec.PROBE_ID_PREFIX)
    assert projected["sanitizedSchema"]["request"]["type"] == "object"
    assert projected["observations"]["effortOccurrences"] == 1
    assert ec.sanitization_violations(projected) == []


def test_catalog_snapshot_persist_is_isolated_to_user_data_root(tmp_path):
    """catalog snapshot도 userData 루트 하위에만 정제 상태로 기록된다(10.15)."""
    store_obj = capability_store.CapabilityStore(user_data_root=tmp_path)
    collector = _collector(store_obj=store_obj)
    catalog = {
        "available": True,
        "sourceKind": str(contracts.Source_Kind.CATALOG),
        "snapshot": {
            "models": [
                {
                    "modelId": MODEL_ID,
                    "provider": PROVIDER,
                    "candidateLabel": LABEL,
                    "apitoken": SECRET_TOKEN,
                }
            ]
        },
        "catalogFingerprint": CATALOG_FP,
        "records": 1,
        "environment": dict(collector.environment),
    }
    path = collector.persist_catalog_snapshot(catalog)
    assert str(path).startswith(str(tmp_path))
    text = path.read_text(encoding="utf-8")
    assert SECRET_TOKEN not in text
    stored = json.loads(text)
    assert stored["snapshot"]["models"][0]["modelId"] == MODEL_ID
    assert "apitoken" not in stored["snapshot"]["models"][0]


# ─────────────────────────────────────────────────────────────────
# execution mode 단일 출처 — profile과 contracts 기본 매핑이 갈리지 않는다
# ─────────────────────────────────────────────────────────────────
def test_route_profiles_execution_mode_is_the_contracts_default_mapping():
    """profile의 `executionMode`는 `contracts.DEFAULT_ROUTE_EXECUTION_MODES` 그 자체다.

    두 곳에 값이 복제되면 write 시점(collector가 hints를 넘긴다)과 read 시점(server
    seam은 넘기지 않는다)의 파생 모드 유도가 다시 갈린다(Requirement 3.20~3.23).
    """
    assert set(ec.ROUTE_PROFILES) == set(contracts.KNOWN_ROUTES)
    assert set(contracts.DEFAULT_ROUTE_EXECUTION_MODES) == set(contracts.KNOWN_ROUTES)
    for route_key, profile in ec.ROUTE_PROFILES.items():
        expected = contracts.DEFAULT_ROUTE_EXECUTION_MODES[route_key]
        assert profile["executionMode"] == expected, route_key
        assert contracts.default_execution_mode(route_key) == expected
    assert ec.route_mode_hints() == dict(contracts.DEFAULT_ROUTE_EXECUTION_MODES)


def test_route_mode_hints_are_redundant_for_mode_derivation():
    """hints를 주든 안 주든 파생 모드 상태가 같다(write·read 유도 일치)."""
    entry = contracts.new_entry(LABEL)
    entry["routes"][SSE_STREAM]["status"] = SUPPORTED
    entry["routes"][JOBS]["status"] = NOT_ADVERTISED

    with_hints = capability_map.derive_mode_support(
        entry["routes"], mode_hints=ec.route_mode_hints()
    )
    without_hints = capability_map.derive_mode_support(entry["routes"])
    assert with_hints == without_hints
    assert without_hints["streamingSupport"] == SUPPORTED
    assert without_hints["asyncSupport"] == NOT_ADVERTISED


# ─────────────────────────────────────────────────────────────────
# SSE 응답이 알려준 실제 전송 model ID (Requirement 2.17)
# ─────────────────────────────────────────────────────────────────
def _sse_events(*, model_ids=(), text="sym-output"):
    """SSE 이벤트 열 — 주어진 전송 ID마다 stream_start를 하나 만든다."""
    events = [
        {"type": "stream_start", ec.SSE_MODEL_ID_FIELD: value, "request_id": "sym-request-e"}
        for value in model_ids
    ]
    events.append({"type": "content_block_delta", "delta": {"text": text}})
    events.append({"type": "message_stop"})
    return events


def _probe_sse(collector, client, *, model=None):
    """SSE_Stream_Route 단일 probe(mock transport — activation evidence 아님)."""
    return _run(
        collector.probe_route(
            model or _model(routes=(SSE_STREAM,)),
            SSE_STREAM,
            transport=client,
            budget=ec.ProbeBudget(),
        )
    )


def test_observe_sse_model_id_reads_last_observed_value():
    """transport가 내부에서 재연결하면 마지막 관측값이 실제 전송 ID다."""
    events = [
        {"type": "stream_start", ec.SSE_MODEL_ID_FIELD: MODEL_ID},
        {"type": "error", "message": "sym-rejected"},
        {"type": "stream_start", ec.SSE_MODEL_ID_FIELD: SENT_MODEL_ID},
        {"type": "content_block_delta", "delta": {"text": "sym-output"}},
        {"type": "message_stop"},
    ]
    assert ec.observe_sse_model_id(events) == SENT_MODEL_ID
    assert ec.judge_sse(events)["observations"]["invocationModelId"] == SENT_MODEL_ID


def test_observe_sse_model_id_makes_no_value_when_absent():
    """이벤트에 model ID가 없으면 값을 만들지 않는다(관측 공백은 공백으로 남긴다)."""
    events = _sse_events()
    assert ec.observe_sse_model_id(events) == contracts.UNDETERMINED
    assert "invocationModelId" not in ec.judge_sse(events)["observations"]


def test_sse_route_probe_records_observed_invocation_model_id():
    """SSE probe는 응답이 알려준 전송 ID를 `invocationModelId`로 기록한다(2.17).

    prefix 교정이 transport 내부에서 일어나면 collector가 넘긴 ID와 실제 전송 ID가
    다르다. 관측이 있으면 관측값을 기록한다.
    """
    client = FakeClient(script={SSE_STREAM: [_sse_events(model_ids=(MODEL_ID, SENT_MODEL_ID))]})
    result = _probe_sse(_collector(), client)

    assert result["status"] == SUPPORTED
    assert result["modelId"] == MODEL_ID  # collector가 넘긴 ID는 그대로 남는다
    assert result["invocationModelId"] == SENT_MODEL_ID
    assert client.sent(SSE_STREAM)[0]["modelId"] == MODEL_ID  # 전송은 1건뿐


def test_sse_route_probe_keeps_sent_id_when_response_is_silent():
    """관측이 없으면 기존 동작 그대로 전송에 쓴 ID를 남긴다."""
    client = FakeClient(script={SSE_STREAM: [_sse_events()]})
    result = _probe_sse(_collector(), client)

    assert result["status"] == SUPPORTED
    assert result["invocationModelId"] == MODEL_ID


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(pytest.main([__file__, "-q"]))
