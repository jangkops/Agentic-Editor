"""Evidence_Collector route probe 오케스트레이션 단위 테스트.

Feature: gateway-models-effort-support (task 11.2)
대상: ai_engine.capability.evidence_collector (route probe 부분)

검증 범위
  - 광고되지 않은 Known_Route는 `NOT_ADVERTISED` 기록 + **전송 0건** (Requirement 4.16, 4.17)
  - HTTP 성공·Valid_Output·Terminal_Success를 각각 독립 결과로 기록하고 **세 조건 AND**일
    때만 route `SUPPORTED` (Requirement 4.5~4.8)
  - 예산 강제: 조합당 성공 generation 1회, 교정 요청 최대 1회, prefix 교정 동일 route 1회
    (Requirement 2.16, 4.24, 4.25)
  - Invocation_Model_ID는 Exact_Model_ID와 별도 필드에 기록 (Requirement 2.17)
  - 명시적 unknown model/unsupported route → `UNSUPPORTED`,
    명시적 allowlist 거부 → `REJECTED`,
    transient·empty·partial·timeout → `UNVERIFIED` 유지 (Requirement 4.18~4.23)
  - Minimal_Request의 output bound는 계약 허용 최소값 (Requirement 4.26)
  - route 상태 완전성 기록과 mock 성공의 activation evidence 배제 (Requirement 4.4, 4.27, 4.28)

이 테스트는 실제 Gateway를 호출하지 않는다. 전송 계층만 대체하고 요청 body는 production
Request_Builder(`_build_payload`·`_build_openai_payload` seam)가 만든 것을 그대로 검사한다.
통과 사실은 Gateway 지원 근거가 **아니다**(Gateway 지원 주장은 작업 18의 production path
probe로만 확정한다). model ID·provider·route 지원 여부는 상수로 두지 않고 무작위 심볼
자리표시자만 사용한다.

실행: ai_engine/.venv/bin/python -m pytest scripts/test_capability_evidence_route_probe.py -q
"""
from __future__ import annotations

import asyncio
import os
import sys
from types import SimpleNamespace

import pytest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from ai_engine.capability import capability_map, contracts, failure_handler  # noqa: E402
from ai_engine.capability import evidence_collector as ec  # noqa: E402
from ai_engine.capability import request_builder as rb  # noqa: E402

# 자리표시자 심볼 — 실제 model identity가 아니다(evidence만 실제 값을 채운다).
LABEL = "sym-label-a"
MODEL_ID = "sym-vendor.sym-model-a"
PROVIDER = "sym-provider-a"
CATALOG_FP = "cat1:sha256:" + "b" * 64
REVISION = "sym-revision-a"
RUN_ID = "sym-run-a"
INTERPRETER = "/sym/path/python"
NOW = "2026-08-03T00:00:00.000000Z"

CONVERSE = str(contracts.Known_Route.CONVERSE)
INVOKE = str(contracts.Known_Route.INVOKE)
RESPONSES = str(contracts.Known_Route.OPENAI_RESPONSES)
JOBS = str(contracts.Known_Route.OPENAI_RESPONSES_JOBS)
SSE = str(contracts.Known_Route.SSE_STREAM)

SUPPORTED = str(contracts.Route_Support_Status.SUPPORTED)
UNSUPPORTED = str(contracts.Route_Support_Status.UNSUPPORTED)
UNVERIFIED = str(contracts.Route_Support_Status.UNVERIFIED)
NOT_ADVERTISED = str(contracts.Route_Support_Status.NOT_ADVERTISED)


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
            self.script: dict[str, list] = {key: list(value) for key, value in (script or {}).items()}

        # -- 스크립트 --------------------------------------------------
        def _next(self, route_key):
            queue = self.script.get(route_key) or []
            item = queue.pop(0) if len(queue) > 1 else (queue[0] if queue else None)
            if isinstance(item, BaseException):
                raise item
            return item

        def _record(self, route_key, model_id, body):
            self.calls.append({"route": route_key, "modelId": model_id, "body": body})

        def sent(self, route_key=None):
            return [
                call for call in self.calls if route_key is None or call["route"] == route_key
            ]

        # -- transport 대체 --------------------------------------------
        async def converse(self, model_id, messages, system_prompt="", tool_config=None):
            self._record(CONVERSE, model_id, self._build_payload(model_id, messages, system_prompt, tool_config))
            return self._next(CONVERSE)

        async def invoke_model(self, model_id, body, timeout=30):
            self._record(INVOKE, model_id, {"modelId": model_id, "body": body})
            return self._next(INVOKE)

        async def openai_responses_call(self, body, timeout=120):
            self._record(RESPONSES, body.get("model") if isinstance(body, dict) else "", body)
            return self._next(RESPONSES)

        async def openai_responses_job_submit(self, model_id, messages, system_prompt="", timeout=30):
            body = self._apply_jobs_model_id(
                self._build_openai_payload(model_id, messages, system_prompt), model_id
            )
            self._record(JOBS, model_id, body)
            return self._next(JOBS)

        async def _openai_poll_job(self, job_id, poll_interval=5, max_wait=300):
            return self._next("JOBS_POLL")

        async def stream_sse_realtime(self, model_id, messages, system_prompt="", tool_config=None):
            self._record(SSE, model_id, self._build_payload(model_id, messages, system_prompt, tool_config))
            for event in self._next(SSE) or []:
                yield event

    return FakeClient


FakeClient = _fake_client_class()


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


def _model(*, routes=(CONVERSE,), model_id=MODEL_ID):
    """discovery 결과 형태(라벨은 검색 라벨일 뿐 identity가 아니다)."""
    return {
        "candidateLabel": LABEL,
        "found": True,
        "modelId": model_id,
        "provider": PROVIDER,
        "sourceKind": str(contracts.Source_Kind.CATALOG),
        "catalogFingerprint": CATALOG_FP,
        "advertisedRoutes": list(routes),
        "probeEligible": True,
    }


def _converse_ok(text="sym-output"):
    return {
        "decision": "ALLOW",
        "output": {"message": {"content": [{"text": text}]}},
        "usage": {"inputTokens": 1, "outputTokens": 1},
        "estimated_cost_krw": 1,
    }


def _responses_ok(text="sym-output"):
    return {
        "object": "response",
        "status": "completed",
        "output_text": text,
        "usage": {"input_tokens": 1, "output_tokens": 1},
    }


def _run(coro):
    return asyncio.run(coro)


# ─────────────────────────────────────────────────────────────────
# Requirement 4.16, 4.17 — advertised 아님 → NOT_ADVERTISED + 전송 0건
# ─────────────────────────────────────────────────────────────────
def test_not_advertised_route_records_status_without_any_transmission():
    client = FakeClient(script={CONVERSE: [_converse_ok()]})
    collector = _collector()
    result = _run(
        collector.probe_route(_model(routes=(RESPONSES,)), CONVERSE, transport=client)
    )
    assert result["status"] == NOT_ADVERTISED
    assert result["reason"] == ec.PROBE_NOT_ADVERTISED
    assert result["transmissions"] == 0
    assert client.calls == []
    # 전송 0건 결과는 catalog 근거이므로 기록 대상이다(route 상태 완전성 유지).
    assert result["evidenceEligible"] is True
    assert collector.probe_budget().snapshot()["totalTransmissions"] == 0


def test_plan_records_runtime_gate_result_when_entry_is_given():
    """이미 `SUPPORTED`인 route를 재검증할 때 runtime 게이트 결과를 감사용으로 남긴다."""
    collector = _collector()
    base_map = collector.initial_map([LABEL], now=NOW)
    entry = capability_map.find_entry(base_map, candidate_label=LABEL)
    entry["modelId"] = MODEL_ID
    entry["provider"] = PROVIDER

    client = FakeClient(script={CONVERSE: [_converse_ok()]})
    # 아직 계약이 없는 entry → runtime 게이트는 전송을 허용하지 않는다(probe 게이트는 별개다).
    plan = collector.probe_transmission_plan(_model(), CONVERSE, entry=entry, transport=client)
    assert plan["runtimeTransmit"] is False
    assert plan["transmit"] is True  # probe 게이트는 광고·계약·예산만 본다
    assert plan["reason"] == ec.PROBE_OK

    # entry를 주지 않으면 runtime 게이트를 평가하지 않는다.
    assert (
        collector.probe_transmission_plan(_model(), CONVERSE, transport=client)["runtimeTransmit"]
        is None
    )
    # transport도 gw도 없으면 전송을 만들지 않는다.
    assert collector.probe_transmission_plan(_model(), CONVERSE)["reason"] == (
        ec.PROBE_TRANSPORT_UNAVAILABLE
    )


def test_model_id_absent_blocks_probe_generation():
    """Exact_Model_ID가 없으면 어떤 Gateway_Probe도 만들지 않는다(Requirement 2.12)."""
    client = FakeClient(script={CONVERSE: [_converse_ok()]})
    model = _model(routes=(CONVERSE,), model_id="")
    result = _run(_collector().probe_route(model, CONVERSE, transport=client))
    assert result["reason"] == ec.PROBE_MODEL_ID_ABSENT
    assert result["status"] == UNVERIFIED
    assert result["transmissions"] == 0
    assert client.calls == []


# ─────────────────────────────────────────────────────────────────
# Requirement 4.5~4.8 — 세 독립 결과와 3조건 AND
# ─────────────────────────────────────────────────────────────────
def test_three_conditions_all_true_yields_supported():
    client = FakeClient(script={CONVERSE: [_converse_ok()]})
    collector = _collector()
    result = _run(collector.probe_route(_model(), CONVERSE, transport=client))
    assert (result["http"], result["validOutput"], result["terminalSuccess"]) == (True, True, True)
    assert result["status"] == SUPPORTED
    assert result["allowlist"] == str(contracts.Allowlist_Result.ALLOWED)
    assert result["transmissions"] == 1
    # 성공 probe가 확정한 계약은 완전하다(SUPPORTED 요건).
    assert contracts.route_contract_is_complete(result["contract"])
    assert result["usage"] == {"inputTokens": 1, "outputTokens": 1}
    assert result["cost"] == {"estimated_cost_krw": 1}


def test_empty_output_keeps_unverified_without_downgrade():
    """empty output은 HTTP 성공이어도 `UNVERIFIED`를 유지한다(Requirement 4.21)."""
    client = FakeClient(script={CONVERSE: [{"decision": "ALLOW", "output": {"message": {"content": []}}}]})
    result = _run(_collector().probe_route(_model(), CONVERSE, transport=client))
    assert result["http"] is True
    assert result["validOutput"] is False
    assert result["terminalSuccess"] is False
    assert result["status"] == UNVERIFIED
    assert result["category"] == failure_handler.TRANSIENT
    assert result["allowlist"] == str(contracts.Allowlist_Result.UNVERIFIED)


def test_partial_async_acceptance_keeps_unverified():
    """`ACCEPTED`만 관측되면 Terminal_Success가 없으므로 `UNVERIFIED`다(Requirement 4.22)."""
    client = FakeClient(script={CONVERSE: [{"decision": "ACCEPTED", "job_id": "sym-job"}]})
    result = _run(_collector().probe_route(_model(), CONVERSE, transport=client))
    assert result["status"] == UNVERIFIED
    assert result["terminalSuccess"] is False


def test_valid_output_without_terminal_event_keeps_unverified_on_stream():
    """content event만 있고 terminal event가 없으면 `SUPPORTED`가 아니다(Requirement 4.15)."""
    events = [{"type": "content_block_delta", "delta": {"text": "sym"}}]
    client = FakeClient(script={SSE: [events]})
    result = _run(_collector().probe_route(_model(routes=(SSE,)), SSE, transport=client))
    assert (result["http"], result["validOutput"], result["terminalSuccess"]) == (True, True, False)
    assert result["status"] == UNVERIFIED
    assert result["observations"] == {"contentEvents": 1, "terminalEvents": 0}


def test_stream_content_and_terminal_events_yield_supported():
    events = [
        {"type": "content_block_delta", "delta": {"text": "sym"}},
        {"type": "message_stop"},
    ]
    client = FakeClient(script={SSE: [events]})
    result = _run(_collector().probe_route(_model(routes=(SSE,)), SSE, transport=client))
    assert result["status"] == SUPPORTED
    assert result["observations"]["terminalEvents"] == 1


def test_jobs_route_records_submission_job_id_and_terminal_separately():
    """제출 성공·job ID·terminal·Valid_Output을 각각 기록한다(Requirement 4.14)."""
    client = FakeClient(script={JOBS: ["sym-job-1"], "JOBS_POLL": [_responses_ok()]})
    result = _run(_collector().probe_route(_model(routes=(JOBS,)), JOBS, transport=client))
    assert result["observations"] == {"submitted": True, "jobIdPresent": True, "polled": True}
    assert result["status"] == SUPPORTED
    # jobs route의 model ID 결속은 성공 probe의 실제 전송 body 관측으로만 확정한다.
    assert result["contract"]["modelIdRequired"] is True
    assert result["contract"]["modelIdFieldPath"] == ["modelId"]
    assert ec.NOTE_MODEL_ID_BINDING_OBSERVED in result["notes"]
    assert contracts.route_contract_is_complete(result["contract"])


def test_transient_timeout_keeps_unverified_and_does_not_downgrade():
    """timeout은 지원 여부를 확정하지 않는다(Requirement 4.20, 4.23)."""
    gateway_module = pytest.importorskip("ai_engine.gateway_module")
    client = FakeClient(script={RESPONSES: [gateway_module.SyncTimeout("sym timeout")]})
    result = _run(_collector().probe_route(_model(routes=(RESPONSES,)), RESPONSES, transport=client))
    assert result["status"] == UNVERIFIED
    assert result["category"] == failure_handler.TRANSIENT
    assert (result["http"], result["validOutput"], result["terminalSuccess"]) == (False, False, False)
    assert result["allowlist"] == str(contracts.Allowlist_Result.UNVERIFIED)


def test_unknown_model_marks_route_unsupported():
    """명시적 unknown model/unsupported route → `UNSUPPORTED`(Requirement 4.18)."""
    gateway_module = pytest.importorskip("ai_engine.gateway_module")
    client = FakeClient(
        script={RESPONSES: [gateway_module.OpenAIModelUnsupported("unsupported model sym")]}
    )
    result = _run(_collector().probe_route(_model(routes=(RESPONSES,)), RESPONSES, transport=client))
    assert result["category"] == failure_handler.ROUTE_CAPABILITY_MISMATCH
    assert result["status"] == UNSUPPORTED


def test_explicit_allowlist_denial_marks_entry_rejected():
    """명시적 allowlist 거부 → Allowlist_Result·Candidate_Model 모두 `REJECTED`(2.14, 4.19)."""
    denial = {"decision": "DENY", "denial_reason": "model_denied: sym not in allowed list"}
    client = FakeClient(script={CONVERSE: [denial]})
    result = _run(_collector().probe_route(_model(), CONVERSE, transport=client))
    assert result["category"] == failure_handler.ALLOWLIST
    assert result["allowlist"] == str(contracts.Allowlist_Result.REJECTED)
    assert result["entryStatus"] == str(contracts.Verification_Status.REJECTED)
    # route 지원 여부는 정책 거부로 확정되지 않는다 → UNVERIFIED 유지.
    assert result["status"] == UNVERIFIED


# ─────────────────────────────────────────────────────────────────
# Requirement 4.26 — Minimal_Request의 최소 output bound
# ─────────────────────────────────────────────────────────────────
def test_minimal_request_uses_contract_minimum_output_bound():
    client = FakeClient(script={CONVERSE: [_converse_ok()]})
    _run(_collector().probe_route(_model(), CONVERSE, transport=client))
    body = client.sent(CONVERSE)[0]["body"]
    contract = ec.candidate_route_contract(CONVERSE)
    assert ec.min_output_bound_fields(contract) == [(["inferenceConfig", "maxTokens"], 1)]
    assert body["inferenceConfig"]["maxTokens"] == 1
    # baseline body 구조는 production builder 그대로다(effort field 0회).
    assert set(body) == {"modelId", "messages", "inferenceConfig"}
    assert body["modelId"] == MODEL_ID
    assert rb.count_key_occurrences(body, "effort") == 0
    assert rb.count_key_occurrences(body, "reasoning_effort") == 0


def test_min_output_bound_is_not_written_when_parent_path_absent():
    """production builder가 만들지 않은 구조에 bound field를 새로 만들지 않는다."""
    converse_contract = ec.candidate_route_contract(CONVERSE)
    openai_body = {"model": MODEL_ID, "input": "sym"}
    assert ec.apply_min_output_bound(openai_body, converse_contract) is openai_body
    # 부모 경로가 있으면 정확히 1회 기록한다.
    converse_body = {"modelId": MODEL_ID, "messages": [], "inferenceConfig": {"maxTokens": 4096}}
    bounded = ec.apply_min_output_bound(converse_body, converse_contract)
    assert bounded["inferenceConfig"]["maxTokens"] == 1
    assert converse_body["inferenceConfig"]["maxTokens"] == 4096  # 입력은 변형하지 않는다
    # bound field가 없는 계약은 body를 그대로 둔다(동기 Responses).
    assert ec.min_output_bound_fields(ec.candidate_route_contract(RESPONSES)) == []


def test_probe_client_inherits_production_builder_and_delegates_credentials():
    """probe client는 production builder·credential 위임을 그대로 상속한다(네트워크 호출 없음)."""
    gateway_module = pytest.importorskip("ai_engine.gateway_module")
    gw = gateway_module.GatewayClient(gateway_url="https://sym-endpoint.invalid/v1", region="sym-region")
    collector = _collector(gw=gw)
    plan = collector.probe_transmission_plan(_model(), CONVERSE)
    assert plan["transmit"] is True
    assert plan["productionPath"] is True

    binding = collector._probe_binding(_model(), None, plan["contract"], MODEL_ID)
    client = ec.probe_client(gw, binding)
    assert isinstance(client, rb.effort_bound_client_class())
    assert client._base is gw  # 5분 credential 캐시 단일화
    body = client._build_payload(MODEL_ID, ec.probe_messages(), "")
    baseline = gateway_module.GatewayClient._build_payload(gw, MODEL_ID, ec.probe_messages(), "")
    assert body["inferenceConfig"]["maxTokens"] == 1
    # 최소 bound 경로 외에는 baseline body와 동일하다.
    assert {key: value for key, value in body.items() if key != "inferenceConfig"} == {
        key: value for key, value in baseline.items() if key != "inferenceConfig"
    }
    assert rb.unexpected_effort_bound_overrides() == ()


def test_sync_responses_body_has_no_gateway_model_id_key():
    """동기 Responses body에는 `modelId` key가 없다(design route별 body 규칙)."""
    client = FakeClient(script={RESPONSES: [_responses_ok()]})
    _run(_collector().probe_route(_model(routes=(RESPONSES,)), RESPONSES, transport=client))
    body = client.sent(RESPONSES)[0]["body"]
    assert rb.count_key_occurrences(body, "modelId") == 0
    assert body["model"] == MODEL_ID
    assert "input" in body


# ─────────────────────────────────────────────────────────────────
# Requirement 4.24, 4.25, 2.16 — 예산 강제
# ─────────────────────────────────────────────────────────────────
def test_successful_generation_is_limited_to_one_per_combination():
    client = FakeClient(script={CONVERSE: [_converse_ok()]})
    collector = _collector()
    budget = ec.ProbeBudget()
    first = _run(collector.probe_route(_model(), CONVERSE, transport=client, budget=budget))
    second = _run(collector.probe_route(_model(), CONVERSE, transport=client, budget=budget))
    assert first["status"] == SUPPORTED
    assert first["transmissions"] == 1
    assert second["transmissions"] == 0
    assert second["reason"] == ec.PROBE_BUDGET_SUCCESS_USED
    assert len(client.sent(CONVERSE)) == 1
    # 예산으로 막힌 결과는 아무것도 주장하지 않으므로 근거로 기록되지 않는다
    # (기존 route 상태를 `UNVERIFIED`로 강등시키지 않는다).
    assert second["evidenceEligible"] is False
    assert ec.evidence_eligible_results([second]) == []
    snapshot = budget.snapshot()
    assert snapshot["totalTransmissions"] == 1
    assert sum(snapshot["successes"].values()) == 1


def test_correction_request_is_limited_to_one_per_combination():
    """명시적·교정 가능 validation error에만 동일 조합 교정 1회(Requirement 4.25).

    교정 수단은 우리가 덧붙인 최소 output bound를 걷어내는 것이다. 교정 후에도 같은
    validation error가 오면 추가 전송을 만들지 않는다.
    """
    invalid = {"decision": "ERROR", "error": "ValidationException: sym invalid request"}
    client = FakeClient(script={CONVERSE: [invalid, invalid, invalid, invalid]})
    budget = ec.ProbeBudget()
    result = _run(_collector().probe_route(_model(), CONVERSE, transport=client, budget=budget))
    assert result["category"] == failure_handler.REQUEST_VALIDATION
    assert result["correctionUsed"] is True
    assert result["transmissions"] == 2  # 최초 1회 + 교정 1회
    assert len(client.sent(CONVERSE)) == 2
    assert sum(budget.snapshot()["corrections"].values()) == 1
    assert result["status"] == UNVERIFIED
    assert ec.NOTE_MIN_OUTPUT_BOUND_DROPPED in result["notes"]
    # 교정 요청은 최소 bound를 제거한 production baseline body를 쓴다.
    assert client.sent(CONVERSE)[0]["body"]["inferenceConfig"]["maxTokens"] == 1
    assert client.sent(CONVERSE)[1]["body"]["inferenceConfig"]["maxTokens"] != 1


def test_no_correction_means_no_additional_transmission():
    """교정 수단이 없는 route는 validation error에도 전송을 늘리지 않는다."""
    gateway_module = pytest.importorskip("ai_engine.gateway_module")
    error = gateway_module.OpenAISurfaceError("ValidationException: sym invalid request")
    client = FakeClient(script={RESPONSES: [error, error]})
    budget = ec.ProbeBudget()
    result = _run(
        _collector().probe_route(
            _model(routes=(RESPONSES,)), RESPONSES, transport=client, budget=budget
        )
    )
    assert result["category"] == failure_handler.REQUEST_VALIDATION
    assert result["correctionUsed"] is False
    assert result["transmissions"] == 1
    assert ec.NOTE_CORRECTION_UNAVAILABLE in result["notes"]
    assert sum(budget.snapshot()["corrections"].values()) == 0


def test_prefix_form_correction_records_invocation_model_id_once():
    """prefix 형태 교정은 동일 route 1회, Invocation_Model_ID는 별도 기록(2.16, 2.17)."""
    gateway_module = pytest.importorskip("ai_engine.gateway_module")
    error = gateway_module.OpenAISurfaceError("model identifier is invalid: sym")
    client = FakeClient(script={RESPONSES: [error, _responses_ok()]})
    budget = ec.ProbeBudget()
    result = _run(
        _collector().probe_route(
            _model(routes=(RESPONSES,)), RESPONSES, transport=client, budget=budget
        )
    )
    assert result["status"] == SUPPORTED
    assert result["transmissions"] == 2
    assert result["correctionUsed"] is True
    assert ec.NOTE_PREFIX_CORRECTION_APPLIED in result["notes"]
    # Exact_Model_ID는 바뀌지 않고 Invocation_Model_ID만 교정 결과를 담는다.
    assert result["modelId"] == MODEL_ID
    assert result["invocationModelId"] == f"us.{MODEL_ID}"
    assert client.sent(RESPONSES)[1]["body"]["model"] == f"us.{MODEL_ID}"
    assert budget.prefix_corrections(MODEL_ID, RESPONSES) == 1


def test_prefix_correction_is_delegated_for_routes_with_internal_fallback():
    """기존 transport가 내부에서 prefix 교정하는 route는 추가 전송을 만들지 않는다.

    `converse`·`stream_sse_realtime`는 prefix 형태 거부를 감지하면 내부에서 정확히 1회
    반대 형태로 재시도한다. collector가 또 전송하면 동일 route 1회 한도를 넘는다.
    """
    prefix_error = {"decision": "ERROR", "error": "ValidationException: model identifier is invalid"}
    client = FakeClient(script={CONVERSE: [prefix_error]})
    budget = ec.ProbeBudget()
    result = _run(_collector().probe_route(_model(), CONVERSE, transport=client, budget=budget))
    assert result["category"] == failure_handler.REQUEST_VALIDATION
    assert ec.NOTE_PREFIX_CORRECTION_DELEGATED in result["notes"]
    assert result["transmissions"] == 1
    assert len(client.sent(CONVERSE)) == 1
    assert budget.prefix_corrections(MODEL_ID, CONVERSE) == 0
    assert result["correctionUsed"] is False
    assert result["invocationModelId"] == MODEL_ID
    assert result["status"] == UNVERIFIED


def test_probe_input_absent_blocks_invoke_transmission():
    """모델별 body가 필요한 route는 입력 없이는 전송하지 않는다."""
    client = FakeClient(script={INVOKE: [{"images": ["sym"]}]})
    result = _run(_collector().probe_route(_model(routes=(INVOKE,)), INVOKE, transport=client))
    assert result["reason"] == ec.PROBE_INPUT_UNAVAILABLE
    assert result["transmissions"] == 0
    assert client.calls == []


def test_invoke_with_operator_probe_input_is_judged_by_production_extractor():
    client = FakeClient(script={INVOKE: [{"images": ["sym-image"]}]})
    result = _run(
        _collector().probe_route(
            _model(routes=(INVOKE,)),
            INVOKE,
            transport=client,
            probe_input={"sym-field": "sym-value"},
        )
    )
    assert result["status"] == SUPPORTED
    assert client.sent(INVOKE)[0]["body"] == {
        "modelId": MODEL_ID,
        "body": {"sym-field": "sym-value"},
    }


# ─────────────────────────────────────────────────────────────────
# Requirement 4.4, 4.27, 4.28 — 기록·완전성·승격
# ─────────────────────────────────────────────────────────────────
def test_probe_routes_covers_every_known_route_and_reports_completeness():
    client = FakeClient(script={CONVERSE: [_converse_ok()]})
    collector = _collector()
    results = _run(collector.probe_routes(_model(), transport=client))
    assert [item["routeKey"] for item in results] == list(contracts.KNOWN_ROUTES)
    assert ec.route_completeness(results) is True
    statuses = {item["routeKey"]: item["status"] for item in results}
    assert statuses[CONVERSE] == SUPPORTED
    assert all(statuses[route] == NOT_ADVERTISED for route in contracts.KNOWN_ROUTES if route != CONVERSE)
    assert len(client.calls) == 1  # 광고된 route 1건만 전송


def test_mock_transport_success_is_excluded_from_activation_evidence():
    """mock 성공은 activation evidence가 아니다(Requirement 4.4, 12.22)."""
    client = FakeClient(script={CONVERSE: [_converse_ok()]})
    collector = _collector()
    results = _run(collector.probe_routes(_model(), transport=client))
    supported = next(item for item in results if item["routeKey"] == CONVERSE)
    assert supported["status"] == SUPPORTED
    assert supported["productionPath"] is False
    assert supported["evidenceEligible"] is False
    assert ec.NOTE_NON_PRODUCTION_TRANSPORT in supported["notes"]

    eligible = ec.evidence_eligible_results(results)
    assert CONVERSE not in {item["routeKey"] for item in eligible}
    assert ec.route_contracts_for_record(results) == {}

    record = collector.route_verification_record(_model(), results, verified_at=NOW)
    assert contracts.validate_verification_record(record) == []
    assert CONVERSE not in {item["routeKey"] for item in record["routeResults"]}
    assert record["routeCompleteness"] is False  # 전송 결과가 제외되어 완전하지 않다

    updated, records = collector.apply_route_probes(
        collector.initial_map([LABEL], now=NOW), _model(), results, now=NOW
    )
    entry = capability_map.find_entry(updated, candidate_label=LABEL)
    assert entry["verificationStatus"] != str(contracts.Verification_Status.VERIFIED)
    assert entry["routes"][CONVERSE]["status"] != SUPPORTED
    assert records and records[0]["evidenceRecordId"].startswith("evr1:sha256:")


def test_transient_result_is_not_state_bearing_and_never_downgrades_a_route():
    """transient 결과는 record에 담지 않으므로 이전 route 상태를 강등하지 않는다."""
    gateway_module = pytest.importorskip("ai_engine.gateway_module")
    client = FakeClient(script={RESPONSES: [gateway_module.SyncTimeout("sym timeout")]})
    collector = _collector()
    result = _run(
        collector.probe_route(_model(routes=(RESPONSES,)), RESPONSES, transport=client)
    )
    assert result["category"] == failure_handler.TRANSIENT
    assert result["stateBearing"] is False
    assert ec.recordable_results([result]) == []
    assert ec.route_contracts_for_record([result]) == {}

    # 이미 `SUPPORTED`로 기록된 route가 transient 결과 반영 후에도 그대로 유지된다.
    base_map = collector.initial_map([LABEL], now=NOW)
    entry = capability_map.find_entry(base_map, candidate_label=LABEL)
    entry["modelId"] = MODEL_ID
    entry["provider"] = PROVIDER
    entry["routes"][RESPONSES]["status"] = SUPPORTED
    capability_map.recompute_fingerprint(entry)
    updated, _ = collector.apply_route_probes(base_map, _model(routes=(RESPONSES,)), [result], now=NOW)
    after = capability_map.find_entry(updated, candidate_label=LABEL)
    assert after["routes"][RESPONSES]["status"] == SUPPORTED


@pytest.mark.parametrize(
    "category,state_bearing",
    [
        (None, True),
        (failure_handler.ALLOWLIST, True),
        (failure_handler.ROUTE_CAPABILITY_MISMATCH, True),
        (failure_handler.TRANSIENT, False),
        (failure_handler.QUOTA, False),
        (failure_handler.AUTHENTICATION, False),
        (failure_handler.REQUEST_VALIDATION, False),
        (failure_handler.UNKNOWN, False),
    ],
)
def test_state_bearing_categories_match_state_preservation_table(category, state_bearing):
    assert (category not in ec.STATE_PRESERVING_CATEGORIES) is state_bearing


def test_record_projection_keeps_only_schema_fields_and_no_raw_body():
    """Verification_Record 투영은 스키마 필드와 비민감 관측치만 남긴다(10.13, 10.14)."""
    client = FakeClient(script={CONVERSE: [_converse_ok()]})
    collector = _collector()
    result = _run(collector.probe_route(_model(), CONVERSE, transport=client))
    projected = ec.route_result_for_record(result)
    assert set(projected) == set(contracts.REQUIRED_ROUTE_RESULT_FIELDS) | {"observations"}
    assert projected["probeId"].startswith(ec.PROBE_ID_PREFIX)
    # Sanitized_Schema는 field name·type·cardinality만 담고 원문 값은 없다.
    schema = projected["sanitizedSchema"]
    assert schema["request"]["type"] == "object"
    assert schema["request"]["fields"]["modelId"] == {"type": "string", "length": len(MODEL_ID)}
    assert ec.sanitization_violations(projected) == []


def test_not_advertised_results_are_recorded_as_catalog_evidence():
    """전송 0건 결과는 근거로 기록되어 route 상태를 채운다(Requirement 4.16, 4.27)."""
    client = FakeClient(script={})
    collector = _collector()
    results = _run(collector.probe_routes(_model(routes=()), transport=client))
    assert client.calls == []
    assert all(item["status"] == NOT_ADVERTISED for item in results)
    assert ec.route_completeness(ec.evidence_eligible_results(results)) is True
    record = collector.route_verification_record(_model(routes=()), results, verified_at=NOW)
    assert record["routeCompleteness"] is True
    assert contracts.validate_verification_record(record) == []
    updated, _ = collector.apply_route_probes(
        collector.initial_map([LABEL], now=NOW), _model(routes=()), results, now=NOW
    )
    entry = capability_map.find_entry(updated, candidate_label=LABEL)
    assert {route: entry["routes"][route]["status"] for route in contracts.KNOWN_ROUTES} == {
        route: NOT_ADVERTISED for route in contracts.KNOWN_ROUTES
    }
    # SUPPORTED route가 없으므로 승격되지 않는다.
    assert entry["verificationStatus"] != str(contracts.Verification_Status.VERIFIED)


# ─────────────────────────────────────────────────────────────────
# 상태 판정 규칙(순수 함수) — 3조건 AND와 강등 금지
# ─────────────────────────────────────────────────────────────────
@pytest.mark.parametrize(
    "http,valid,terminal,expected",
    [
        (True, True, True, SUPPORTED),
        (False, True, True, UNVERIFIED),
        (True, False, True, UNVERIFIED),
        (True, True, False, UNVERIFIED),
        (False, False, False, UNVERIFIED),
    ],
)
def test_route_support_status_requires_all_three_conditions(http, valid, terminal, expected):
    assert (
        ec.route_support_status(
            advertised=True, http=http, valid_output=valid, terminal_success=terminal
        )
        == expected
    )


@pytest.mark.parametrize(
    "category",
    [
        failure_handler.TRANSIENT,
        failure_handler.QUOTA,
        failure_handler.AUTHENTICATION,
        failure_handler.REQUEST_VALIDATION,
        failure_handler.ALLOWLIST,
        failure_handler.UNKNOWN,
    ],
)
def test_only_route_capability_mismatch_marks_unsupported(category):
    status = ec.route_support_status(
        advertised=True, http=True, valid_output=False, terminal_success=False, category=category
    )
    assert status == UNVERIFIED


def test_not_advertised_wins_over_any_judgement():
    assert (
        ec.route_support_status(
            advertised=False, http=True, valid_output=True, terminal_success=True
        )
        == NOT_ADVERTISED
    )


def test_budget_limits_are_one_per_axis():
    budget = ec.ProbeBudget()
    key = ec.ProbeBudget.key(MODEL_ID, CONVERSE)
    assert budget.transmission_limit() == 3  # 성공 1 + 교정 1 + prefix 교정 1
    assert budget.may_transmit(key) is True
    budget.record_transmission(key)
    budget.record_success(key)
    assert budget.may_transmit(key) is False
    assert budget.block_reason(key) == ec.PROBE_BUDGET_SUCCESS_USED
    other = ec.ProbeBudget.key(MODEL_ID, RESPONSES)
    assert budget.may_transmit(other) is True  # 조합별로 독립된 예산


# ─────────────────────────────────────────────────────────────────
# Requirement 2.17 — catalog가 명시한 Invocation_Model_ID로 첫 전송
#
# Exact_Model_ID는 catalog identity이지만 production 요청 경로는 모델에 따라 다른 ID로
# 전송한다(inference profile 전용 모델 등). catalog record가 전송 ID를 명시하면 probe는
# **그 값 그대로** 첫 전송에 쓰고, Exact_Model_ID는 identity로 그대로 유지한다. 접두사를
# 만들지 않으므로 명시가 없으면 Exact_Model_ID를 보낸다.
# ─────────────────────────────────────────────────────────────────
INVOCATION_ID = "sym-scope.sym-vendor.sym-model-a"


def test_declared_invocation_model_id_is_used_for_first_transmission():
    """record가 명시한 전송 ID가 첫 전송 body에 실린다(Exact_Model_ID는 identity 유지)."""
    client = FakeClient(script={CONVERSE: [_converse_ok()]})
    model = dict(_model(), invocationModelId=INVOCATION_ID)
    plan = _collector().probe_transmission_plan(model, CONVERSE, transport=client)
    assert plan["modelId"] == MODEL_ID
    assert plan["invocationModelId"] == INVOCATION_ID

    result = _run(_collector().probe_route(model, CONVERSE, transport=client))
    assert result["transmissions"] == 1
    assert client.sent(CONVERSE)[0]["modelId"] == INVOCATION_ID
    assert client.sent(CONVERSE)[0]["body"]["modelId"] == INVOCATION_ID
    assert result["modelId"] == MODEL_ID  # identity는 바뀌지 않는다
    assert result["invocationModelId"] == INVOCATION_ID
    assert result["status"] == SUPPORTED
    # 전송 ID는 Verification_Record의 별도 field로만 흘러간다.
    assert ec.invocation_model_ids_from([result]) == [INVOCATION_ID]


def test_absent_invocation_model_id_transmits_exact_model_id():
    """명시가 없으면 Exact_Model_ID를 보낸다(접두사를 만들지 않는다)."""
    client = FakeClient(script={CONVERSE: [_converse_ok()]})
    plan = _collector().probe_transmission_plan(_model(), CONVERSE, transport=client)
    assert plan["invocationModelId"] == MODEL_ID

    result = _run(_collector().probe_route(_model(), CONVERSE, transport=client))
    assert client.sent(CONVERSE)[0]["modelId"] == MODEL_ID
    assert result["invocationModelId"] == MODEL_ID


def test_ambiguous_declared_invocation_ids_fall_back_to_exact_model_id():
    """서로 다른 전송 ID가 여럿이면 고르지 않고 Exact_Model_ID를 보낸다(값 추론 금지)."""
    client = FakeClient(script={CONVERSE: [_converse_ok()]})
    model = dict(_model(), invocationModelIds=[INVOCATION_ID, "sym-other." + MODEL_ID])
    plan = _collector().probe_transmission_plan(model, CONVERSE, transport=client)
    assert plan["invocationModelId"] == MODEL_ID
    result = _run(_collector().probe_route(model, CONVERSE, transport=client))
    assert client.sent(CONVERSE)[0]["modelId"] == MODEL_ID
    assert result["invocationModelId"] == MODEL_ID


def test_discovery_reads_declared_invocation_model_id_from_catalog_record():
    """discovery가 catalog record의 전송 ID를 문자 그대로 읽는다(미명시는 미확정)."""
    snapshot = {
        "models": [
            {
                "modelId": MODEL_ID,
                "provider": PROVIDER,
                "candidateLabel": LABEL,
                "invocationModelId": INVOCATION_ID,
                "routes": [CONVERSE],
            }
        ]
    }
    env = {
        "gatewayEnvironmentId": "sym-env",
        "endpointIdentity": "https://sym-endpoint.invalid/v1",
        "region": "sym-region",
    }
    collector = _collector(
        catalog_fetcher=lambda: snapshot, catalog_endpoint="/sym/models", catalog_environment=env
    )
    result = collector.discover([LABEL])[0]
    assert result["modelId"] == MODEL_ID
    assert result["invocationModelId"] == INVOCATION_ID

    plain = {"models": [{"modelId": MODEL_ID, "provider": PROVIDER, "candidateLabel": LABEL}]}
    plain_collector = _collector(
        catalog_fetcher=lambda: plain, catalog_endpoint="/sym/models", catalog_environment=env
    )
    assert plain_collector.discover([LABEL])[0]["invocationModelId"] == ""
