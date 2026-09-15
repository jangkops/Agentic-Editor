"""EffortBoundClient·effort 주입 단위 테스트.

Feature: gateway-models-effort-support (task 7.3)
대상: ai_engine.capability.request_builder (Request_Builder 부분)

검증 범위
  - effort 미선택 시 5개 route baseline body가 base `GatewayClient` 결과와 **바이트 동일**
    (Requirement 7.15, 7.16, 7.17, 8.13, 8.14, 8.15, 8.17)
  - tuple·status·domain 불일치 시 baseline body를 **동일 객체**로 반환
    (Requirement 8.17, 8.18, 8.19)
  - 전부 일치 시 계약 field path에 값을 **정확히 1회** 기록하고 나머지 body 불변
    (Requirement 8.16)
  - jobs `modelId` 계약: 요구 시 계약 path에 Invocation_Model_ID 1회, 미요구 시 body
    전체 0회, 미확정이면 기존 기본 구현에 위임 (Requirement 8.11, 8.12)
  - credential은 base 인스턴스에만 존재(신규 캐시 없음)하고 서명·retry·polling 메서드는
    오버라이드하지 않음 (Requirement 1.13, 10.1~10.6, 10.19)

이 테스트는 Gateway를 호출하지 않는다. 통과 사실은 Gateway 지원 근거가 아니다
(Gateway 지원 주장은 작업 18의 production path probe로만 확정한다). model ID·provider·
effort field path·effort 허용값은 확정 상수 없이 무작위 심볼 자리표시자만 쓴다.

실행: ai_engine/.venv/bin/python -m pytest scripts/test_capability_effort_bound_client.py -q
"""
from __future__ import annotations

import copy
import json
import os
import sys

import pytest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from ai_engine.capability import contracts, effort_settings  # noqa: E402
from ai_engine.capability import request_builder as rb  # noqa: E402
from ai_engine.gateway_module import GatewayClient  # noqa: E402

# 자리표시자 심볼 — 실제 model identity·field path·허용값이 아니다(evidence만 실제 값을 채운다).
MODEL_ID = "sym-model-a"
INVOCATION_ID = "sym-invocation-a"
FINGERPRINT = "cfp1:sha256:" + "b" * 64
EVIDENCE_ID = "evr1:sha256:" + "a" * 64
EFFORT_PATH = ["sym-outer", "sym-effort"]
EFFORT_VALUES = ["sym-value-1", "sym-value-2"]
PROBE_INPUT = "sym-probe-input"


# ─────────────────────────────────────────────────────────────────
# 헬퍼
# ─────────────────────────────────────────────────────────────────
def _route_contract(route_key: str, **overrides) -> dict:
    contract = contracts.new_route_contract(
        route_key,
        endpoint_ref=f"sym-endpoint-{route_key}",
        http_method="POST",
        execution_mode=contracts.Execution_Mode.SYNC,
        signing_service=contracts.Signing_Service.EXECUTE_API,
        model_id_required=False,
        message_field_path=["sym-messages"],
        output_validator_ref="sym-validator",
        terminal_condition_ref="sym-terminal",
        retry_policy_ref="sym-retry",
        fallback_rank=0,
        purposes=["sym-purpose"],
        min_output_bound={"sym-bound": 1},
        evidence_ref=EVIDENCE_ID,
    )
    contract.update(overrides)
    return contract


def _effort_contract(route_key: str, **overrides) -> dict:
    contract = contracts.new_effort_contract(
        MODEL_ID,
        route_key,
        field_path=list(EFFORT_PATH),
        value_type=contracts.Value_Type.STRING,
        domain_kind=contracts.Domain_Kind.ENUM,
        enum_values=list(EFFORT_VALUES),
        verified_values=list(EFFORT_VALUES),
        evidence_ref=EVIDENCE_ID,
    )
    contract.update(overrides)
    return contract


def _binding(
    route_key: str = contracts.Known_Route.OPENAI_RESPONSES,
    *,
    effort_status: str = contracts.Effort_Support_Status.SUPPORTED,
    effort_contract: dict | None = "default",  # type: ignore[assignment]
    model_id: str = MODEL_ID,
    fingerprint: str = FINGERPRINT,
    invocation_model_id: str = "",
    **contract_overrides,
) -> dict:
    """Request_Binding을 만든다(:func:`request_builder.bind_contract`와 같은 형태)."""
    entry = contracts.new_entry("sym-label-a")
    entry["modelId"] = model_id
    entry["capabilityFingerprint"] = fingerprint
    entry["invocationModelIds"] = [INVOCATION_ID]
    contract = _effort_contract(route_key) if effort_contract == "default" else effort_contract
    entry["effort"][route_key] = {
        "status": str(effort_status),
        "contract": contract,
        "evidenceRef": EVIDENCE_ID,
    }
    return rb.bind_contract(
        entry,
        _route_contract(route_key, **contract_overrides),
        invocation_model_id=invocation_model_id or None,
    )


def _selection(
    route_key: str = contracts.Known_Route.OPENAI_RESPONSES,
    *,
    model_id: str = MODEL_ID,
    fingerprint: str = FINGERPRINT,
    value: object = EFFORT_VALUES[0],
    value_type: str = contracts.Value_Type.STRING,
) -> dict:
    """Effort_Settings.to_selection 형식의 선택 dict."""
    setting = {
        "modelId": model_id,
        "route": str(route_key),
        "capabilityFingerprint": fingerprint,
        "value": value,
        "valueType": str(value_type),
        "updatedAt": "2026-08-03T00:00:00.000000Z",
    }
    selection = effort_settings.to_selection(setting)
    assert selection is not None, "자리표시자 selection이 스키마를 만족해야 한다"
    return selection


def _base() -> GatewayClient:
    """네트워크·자격증명 접근 없이 body 생성만 하는 base 클라이언트."""
    return GatewayClient(gateway_url="https://sym-gateway.invalid/v1", region="us-west-2")


def _wrap(base: GatewayClient, binding=None, selection=None):
    return rb.effort_bound_client(base, binding, selection)


def _raw(body) -> bytes:
    """기존 transport가 실제로 전송하는 바이트열(키 순서까지 비교한다)."""
    return json.dumps(body).encode()


MESSAGES = [{"role": "user", "content": [{"text": PROBE_INPUT}]}]


# ═════════════════════════════════════════════════════════════════
# baseline 동일성 — effort 미선택 (Requirement 7.15~7.17, 8.13~8.15, 8.17)
# ═════════════════════════════════════════════════════════════════
@pytest.mark.parametrize(
    "selection_factory",
    [
        pytest.param(lambda: None, id="no-selection"),
        pytest.param(lambda: {}, id="empty-selection"),
        pytest.param(lambda: _selection(model_id="sym-model-other"), id="model-id-mismatch"),
        pytest.param(lambda: _selection(route_key=contracts.Known_Route.CONVERSE), id="route-mismatch"),
        pytest.param(lambda: _selection(fingerprint="cfp1:sha256:" + "c" * 64), id="fingerprint-mismatch"),
        pytest.param(lambda: _selection(value="sym-value-outside-domain"), id="domain-violation"),
        pytest.param(lambda: _selection(value=1, value_type=contracts.Value_Type.INTEGER), id="value-type-mismatch"),
    ],
)
def test_converse_body_identical_when_effort_not_injected(selection_factory):
    """effort가 주입되지 않는 모든 경우 Converse body가 baseline과 바이트 동일하다."""
    base = _base()
    binding = _binding(contracts.Known_Route.OPENAI_RESPONSES)
    client = _wrap(base, binding, selection_factory())

    expected = base._build_payload(MODEL_ID, MESSAGES, "sym-system")
    actual = client._build_payload(MODEL_ID, MESSAGES, "sym-system")
    assert _raw(actual) == _raw(expected)


@pytest.mark.parametrize(
    "effort_status",
    [
        contracts.Effort_Support_Status.UNVERIFIED,
        contracts.Effort_Support_Status.UNSUPPORTED,
        contracts.Effort_Support_Status.STALE,
    ],
)
def test_body_identical_when_effort_status_not_supported(effort_status):
    """Effort_Support_Status가 `SUPPORTED`가 아니면 baseline과 동일하다(Requirement 8.17)."""
    base = _base()
    route = contracts.Known_Route.OPENAI_RESPONSES
    client = _wrap(base, _binding(route, effort_status=effort_status), _selection(route))

    expected = base._build_openai_payload(MODEL_ID, MESSAGES, "sym-system")
    actual = client._build_openai_payload(MODEL_ID, MESSAGES, "sym-system")
    assert _raw(actual) == _raw(expected)


def test_body_identical_when_effort_contract_incomplete():
    """계약이 불완전하면(domain 결손) 주입하지 않는다."""
    base = _base()
    route = contracts.Known_Route.OPENAI_RESPONSES
    incomplete = _effort_contract(route, enumValues=None)
    client = _wrap(base, _binding(route, effort_contract=incomplete), _selection(route))

    expected = base._build_openai_payload(MODEL_ID, MESSAGES)
    assert _raw(client._build_openai_payload(MODEL_ID, MESSAGES)) == _raw(expected)


def test_baseline_body_is_the_same_object_when_not_injected():
    """주입하지 않을 때 :func:`_inject_effort`는 baseline을 **동일 객체**로 돌려준다."""
    baseline = {"model": MODEL_ID, "input": PROBE_INPUT}
    route = contracts.Known_Route.OPENAI_RESPONSES
    assert rb._inject_effort(baseline, _binding(route), None) is baseline
    assert rb._inject_effort(baseline, None, _selection(route)) is baseline
    assert rb._inject_effort(baseline, _binding(route), _selection(route, model_id="sym-other")) is baseline


def test_effort_field_absent_in_every_nested_path_when_not_injected():
    """주입하지 않으면 body 전체에서 effort field 발생 횟수가 0이다(Requirement 7.15, 7.16)."""
    base = _base()
    route = contracts.Known_Route.OPENAI_RESPONSES
    client = _wrap(base, _binding(route), None)
    body = client._build_openai_payload(MODEL_ID, MESSAGES, "sym-system")
    for part in EFFORT_PATH:
        assert rb.count_key_occurrences(body, part) == 0


def test_five_route_bodies_identical_without_effort():
    """5개 route의 생성 body가 effort 미선택 상태에서 baseline과 바이트 동일하다."""
    base, other = _base(), _base()
    binding = _binding(contracts.Known_Route.OPENAI_RESPONSES)
    client = _wrap(base, binding, None)

    # CONVERSE / SSE_STREAM — 두 route 모두 기존 `_build_payload`를 사용한다.
    for tool_config in (None, {"tools": [{"toolSpec": {"name": "sym-tool"}}]}):
        assert _raw(client._build_payload(MODEL_ID, MESSAGES, "sym-system", tool_config)) == _raw(
            other._build_payload(MODEL_ID, MESSAGES, "sym-system", tool_config)
        )

    # OPENAI_RESPONSES — 동기 body에는 modelId key가 0개다(Requirement 8.4).
    sync_body = client._build_openai_payload(MODEL_ID, MESSAGES, "sym-system")
    assert _raw(sync_body) == _raw(other._build_openai_payload(MODEL_ID, MESSAGES, "sym-system"))
    assert rb.count_key_occurrences(sync_body, rb.MODEL_ID_KEY) == 0

    # OPENAI_RESPONSES_JOBS — jobs 계약이 없는 binding이면 기본 seam 동작 그대로다.
    assert _raw(client._apply_jobs_model_id(sync_body, MODEL_ID)) == _raw(
        other._apply_jobs_model_id(sync_body, MODEL_ID)
    )

    # INVOKE — payload는 호출자가 구성하며 builder seam이 없다(오버라이드 대상 아님).
    assert rb.BUILDER_SEAM_METHODS == ("_build_payload", "_build_openai_payload", "_apply_jobs_model_id")


# ═════════════════════════════════════════════════════════════════
# exact-once 주입 (Requirement 8.16)
# ═════════════════════════════════════════════════════════════════
@pytest.mark.parametrize("value", EFFORT_VALUES)
def test_supported_effort_written_exactly_once(value):
    """verified domain의 값이 계약 field path에 정확히 1회 기록된다."""
    base = _base()
    route = contracts.Known_Route.OPENAI_RESPONSES
    binding = _binding(route)
    client = _wrap(base, binding, _selection(route, value=value))

    baseline = base._build_openai_payload(MODEL_ID, MESSAGES, "sym-system")
    body = client._build_openai_payload(MODEL_ID, MESSAGES, "sym-system")

    assert rb.value_at_path(body, EFFORT_PATH) == value
    assert rb.count_key_occurrences(body, EFFORT_PATH[-1]) == 1
    assert rb.count_key_occurrences(body, EFFORT_PATH[0]) == 1

    # 주입 경로를 제거하면 baseline과 완전히 동일하다(다른 경로 추가·삭제·변형 없음).
    stripped = rb.without_key(body, EFFORT_PATH[0])
    assert _raw(stripped) == _raw(baseline)


def test_converse_route_injection_uses_verified_schema():
    """CONVERSE 계약도 같은 규칙으로 주입한다(기존 model ID·message·inferenceConfig 보존)."""
    base = _base()
    route = contracts.Known_Route.CONVERSE
    client = _wrap(base, _binding(route), _selection(route))

    baseline = base._build_payload(MODEL_ID, MESSAGES, "sym-system")
    body = client._build_payload(MODEL_ID, MESSAGES, "sym-system")

    assert rb.value_at_path(body, EFFORT_PATH) == EFFORT_VALUES[0]
    assert rb.count_key_occurrences(body, EFFORT_PATH[-1]) == 1
    assert _raw(rb.without_key(body, EFFORT_PATH[0])) == _raw(baseline)
    # 기존 builder가 만든 필드는 그대로다(Requirement 8.13, 8.14, 8.15).
    assert body["modelId"] == baseline["modelId"]
    assert body["messages"] == baseline["messages"]
    assert body["inferenceConfig"] == baseline["inferenceConfig"]


def test_range_domain_boundaries_are_injected():
    """`RANGE` 계약의 inclusive 경계값도 1회 기록된다(허용값을 상수로 두지 않는다)."""
    base = _base()
    route = contracts.Known_Route.OPENAI_RESPONSES
    ranged = _effort_contract(
        route,
        valueType=str(contracts.Value_Type.INTEGER),
        domainKind=str(contracts.Domain_Kind.RANGE),
        enumValues=None,
        rangeLowerInclusive=1,
        rangeUpperInclusive=4,
    )
    binding = _binding(route, effort_contract=ranged)

    for value in (1, 4):
        selection = _selection(route, value=value, value_type=contracts.Value_Type.INTEGER)
        body = _wrap(base, binding, selection)._build_openai_payload(MODEL_ID, MESSAGES)
        assert rb.value_at_path(body, EFFORT_PATH) == value
        assert rb.count_key_occurrences(body, EFFORT_PATH[-1]) == 1

    # 경계 밖 값은 주입하지 않는다.
    outside = _selection(route, value=5, value_type=contracts.Value_Type.INTEGER)
    body = _wrap(base, binding, outside)._build_openai_payload(MODEL_ID, MESSAGES)
    assert rb.count_key_occurrences(body, EFFORT_PATH[-1]) == 0


def test_injection_does_not_mutate_baseline_input():
    """주입은 baseline body를 변형하지 않는다(deep copy에 기록한다)."""
    route = contracts.Known_Route.OPENAI_RESPONSES
    baseline = {"model": MODEL_ID, "input": [{"role": "user", "content": [{"text": PROBE_INPUT}]}]}
    snapshot = copy.deepcopy(baseline)
    body = rb._inject_effort(baseline, _binding(route), _selection(route))

    assert body is not baseline
    assert baseline == snapshot
    assert rb.value_at_path(body, EFFORT_PATH) == EFFORT_VALUES[0]


def test_path_conflict_keeps_baseline():
    """중간 노드가 dict가 아니면 baseline을 파괴하지 않고 그대로 반환한다."""
    route = contracts.Known_Route.OPENAI_RESPONSES
    baseline = {"model": MODEL_ID, EFFORT_PATH[0]: PROBE_INPUT}  # 중간 노드가 문자열
    result = rb._inject_effort(baseline, _binding(route), _selection(route))
    assert result is baseline
    assert (
        rb.injection_reason(_binding(route), _selection(route), baseline_body=baseline)
        == rb.EFFORT_PATH_CONFLICT
    )


def test_injection_reason_reports_first_blocking_cause():
    """차단 이유는 :data:`INJECTION_REASONS` 순서의 첫 일치 코드다."""
    route = contracts.Known_Route.OPENAI_RESPONSES
    binding = _binding(route)
    assert rb.injection_reason(binding, None) == rb.EFFORT_NO_SELECTION
    assert rb.injection_reason(binding, _selection(route)) == rb.REASON_OK
    assert (
        rb.injection_reason(_binding(route, model_id=""), _selection(route))
        == rb.EFFORT_BINDING_INCOMPLETE
    )
    assert (
        rb.injection_reason(binding, _selection(route, model_id="sym-other"))
        == rb.EFFORT_MODEL_ID_MISMATCH
    )
    misbound = _effort_contract(route, modelId="sym-other-model")
    assert (
        rb.injection_reason(_binding(route, effort_contract=misbound), _selection(route))
        == rb.EFFORT_CONTRACT_MISBOUND
    )


# ═════════════════════════════════════════════════════════════════
# jobs `modelId` 계약 (Requirement 8.11, 8.12)
# ═════════════════════════════════════════════════════════════════
JOBS = contracts.Known_Route.OPENAI_RESPONSES_JOBS


def test_jobs_model_id_written_once_at_contract_path():
    """계약이 model ID를 요구하면 계약 path에 Invocation_Model_ID가 정확히 1회 기록된다."""
    base = _base()
    binding = _binding(
        JOBS,
        invocation_model_id=INVOCATION_ID,
        modelIdRequired=True,
        modelIdFieldPath=["sym-envelope", rb.MODEL_ID_KEY],
    )
    client = _wrap(base, binding, None)
    body = client._apply_jobs_model_id(base._build_openai_payload(MODEL_ID, MESSAGES), MODEL_ID)

    assert rb.value_at_path(body, ["sym-envelope", rb.MODEL_ID_KEY]) == INVOCATION_ID
    assert rb.count_key_occurrences(body, rb.MODEL_ID_KEY) == 1


def test_jobs_model_id_absent_when_contract_does_not_require_it():
    """계약이 요구하지 않으면 body 전체에서 `modelId` key가 0회다."""
    base = _base()
    client = _wrap(base, _binding(JOBS, modelIdRequired=False), None)
    baseline = base._build_openai_payload(MODEL_ID, MESSAGES)
    body = client._apply_jobs_model_id({**baseline, rb.MODEL_ID_KEY: MODEL_ID}, MODEL_ID)

    assert rb.count_key_occurrences(body, rb.MODEL_ID_KEY) == 0
    assert _raw(body) == _raw(baseline)


def test_jobs_top_level_path_is_byte_identical_to_baseline_seam():
    """계약 path가 기존 위치(top-level)면 기본 구현과 바이트 동일하다."""
    base, other = _base(), _base()
    client = _wrap(
        base,
        _binding(JOBS, invocation_model_id=MODEL_ID, modelIdRequired=True, modelIdFieldPath=[rb.MODEL_ID_KEY]),
        None,
    )
    baseline_body = base._build_openai_payload(MODEL_ID, MESSAGES, "sym-system")
    assert _raw(client._apply_jobs_model_id(baseline_body, MODEL_ID)) == _raw(
        other._apply_jobs_model_id(baseline_body, MODEL_ID)
    )


def test_jobs_model_id_undetermined_delegates_to_base_implementation():
    """`modelIdRequired`가 미확정이면 기존 기본 구현에 위임한다(baseline 보존)."""
    base, other = _base(), _base()
    client = _wrap(base, _binding(JOBS, modelIdRequired=None), None)
    baseline_body = base._build_openai_payload(MODEL_ID, MESSAGES)
    assert _raw(client._apply_jobs_model_id(baseline_body, MODEL_ID)) == _raw(
        other._apply_jobs_model_id(baseline_body, MODEL_ID)
    )
    assert rb.apply_jobs_model_id(baseline_body, MODEL_ID, _binding(JOBS, modelIdRequired=None)) is None


def test_non_jobs_binding_does_not_change_jobs_seam():
    """jobs 계약이 아닌 binding으로는 jobs body를 바꾸지 않는다."""
    base, other = _base(), _base()
    client = _wrap(base, _binding(contracts.Known_Route.CONVERSE, modelIdRequired=False), None)
    baseline_body = base._build_openai_payload(MODEL_ID, MESSAGES)
    assert _raw(client._apply_jobs_model_id(baseline_body, MODEL_ID)) == _raw(
        other._apply_jobs_model_id(baseline_body, MODEL_ID)
    )


def test_transmitted_jobs_bytes_go_through_the_seams():
    """실제 제출 경로(`openai_responses_job_submit`)가 오버라이드된 seam을 사용한다.

    전송 바이트열을 가로채 (1) effort 미선택이면 base와 동일, (2) 선택되면 계약
    field path에 값이 1회 들어간 body가 나가는지 확인한다. 네트워크 호출은 하지 않는다.
    """
    import asyncio

    sent: dict[str, bytes] = {}

    def _capture(tag):
        async def _stub(url, body_bytes, timeout, label=""):
            sent[tag] = body_bytes
            return {"job_id": "sym-job-id"}

        return _stub

    base, other = _base(), _base()
    jobs_path = [rb.MODEL_ID_KEY]
    # prefix 교정이 없어 Invocation_Model_ID == Exact_Model_ID인 경우(기존 경로와 동일).
    same_id = _binding(JOBS, invocation_model_id=MODEL_ID, modelIdRequired=True, modelIdFieldPath=jobs_path)
    # 교정된 Invocation_Model_ID가 기록되는 경우(Requirement 8.11).
    corrected = _binding(JOBS, invocation_model_id=INVOCATION_ID, modelIdRequired=True, modelIdFieldPath=jobs_path)

    plain = _wrap(base, same_id, None)
    plain._openai_post_with_retry = _capture("plain")
    other._openai_post_with_retry = _capture("baseline")
    bound = _wrap(base, same_id, _selection(JOBS))
    bound._openai_post_with_retry = _capture("bound")
    corrected_client = _wrap(base, corrected, None)
    corrected_client._openai_post_with_retry = _capture("corrected")

    for client in (other, plain, bound, corrected_client):
        asyncio.run(client.openai_responses_job_submit(MODEL_ID, MESSAGES, "sym-system"))

    assert sent["plain"] == sent["baseline"]  # effort 미선택 → 전송 바이트 동일

    bound_body = json.loads(sent["bound"].decode())
    assert rb.value_at_path(bound_body, EFFORT_PATH) == EFFORT_VALUES[0]
    assert rb.count_key_occurrences(bound_body, EFFORT_PATH[-1]) == 1
    assert rb.count_key_occurrences(bound_body, rb.MODEL_ID_KEY) == 1
    assert bound_body[rb.MODEL_ID_KEY] == MODEL_ID
    assert rb.without_key(bound_body, EFFORT_PATH[0]) == json.loads(sent["baseline"].decode())

    corrected_body = json.loads(sent["corrected"].decode())
    assert corrected_body[rb.MODEL_ID_KEY] == INVOCATION_ID
    assert rb.count_key_occurrences(corrected_body, rb.MODEL_ID_KEY) == 1


def test_invocation_model_id_is_not_invented():
    """Invocation_Model_ID는 명시값 → 관측된 단일 ID → 인자 순으로만 결정된다."""
    assert rb.invocation_model_id({"invocationModelId": INVOCATION_ID}, MODEL_ID) == INVOCATION_ID
    assert rb.invocation_model_id({"invocationModelIds": [INVOCATION_ID]}, MODEL_ID) == INVOCATION_ID
    assert rb.invocation_model_id({"invocationModelIds": ["sym-a", "sym-b"]}, MODEL_ID) == MODEL_ID
    assert rb.invocation_model_id(None, MODEL_ID) == MODEL_ID
    assert rb.invocation_model_id({}, "") == contracts.UNDETERMINED


# ═════════════════════════════════════════════════════════════════
# credential 위임·오버라이드 화이트리스트 (Requirement 1.13, 10.1~10.6, 10.19)
# ═════════════════════════════════════════════════════════════════
class _RecordingBase(GatewayClient):
    """credential 호출을 기록하는 base(네트워크·STS 접근 없음)."""

    def __init__(self):
        super().__init__(gateway_url="https://sym-gateway.invalid/v1", region="us-west-2")
        self.calls: list[str] = []

    def _get_creds(self):
        self.calls.append("_get_creds")
        return "sym-creds"

    def force_refresh_creds(self):
        self.calls.append("force_refresh_creds")

    def inject_credentials(self, access_key, secret_key, session_token=""):
        self.calls.append("inject_credentials")


def test_credentials_are_delegated_to_base_instance():
    """credential 획득·갱신·주입은 base 인스턴스 하나에만 반영된다(5분 캐시 단일화)."""
    base = _RecordingBase()
    client = _wrap(base, _binding(), None)

    assert client._get_creds() == "sym-creds"
    client.force_refresh_creds()
    client.inject_credentials("sym-key", "sym-secret", "sym-token")

    assert base.calls == ["_get_creds", "force_refresh_creds", "inject_credentials"]
    # wrapper는 자체 credential 상태를 만들지 않는다.
    assert "_creds" not in vars(client)
    assert "_cred_time" not in vars(client)
    assert "_injected_creds" not in vars(client)


def test_transport_configuration_is_copied_not_recreated():
    """gateway_url·region·profile·bedrock_user는 base 값을 그대로 쓴다(신규 URL 없음)."""
    base = GatewayClient(
        gateway_url="https://sym-gateway.invalid/v1",
        aws_profile="sym-profile",
        region="us-west-2",
        bedrock_user="sym-user",
    )
    client = _wrap(base, _binding(), None)
    for name in rb.DELEGATED_BASE_ATTRS:
        assert getattr(client, name) == getattr(base, name)
    assert client.STREAM_URL == base.STREAM_URL  # 상속 — 신규 stream URL 없음


def test_override_surface_is_limited_to_seams_and_credentials():
    """서명·retry·prefix 교정·job polling·응답 변환은 오버라이드하지 않는다."""
    assert rb.unexpected_effort_bound_overrides() == ()
    assert rb.effort_bound_overrides() == rb.EFFORT_BOUND_OVERRIDES
    for name in ("_sign", "converse", "invoke_model", "stream_sse_realtime",
                 "_openai_post_with_retry", "_poll_job_data", "_openai_poll_job",
                 "openai_responses_job_submit", "_is_expired_error"):
        assert name not in rb.EFFORT_BOUND_OVERRIDES
        assert name not in vars(rb.effort_bound_client_class())


def test_effort_bound_client_is_a_gateway_client_subclass():
    """상속 관계 자체가 기존 transport 재사용의 근거다."""
    cls = rb.effort_bound_client_class()
    assert issubclass(cls, GatewayClient)
    assert cls.__mro__[1] is GatewayClient
    assert rb.EffortBoundClient is cls  # PEP 562 지연 해석


# ═════════════════════════════════════════════════════════════════
# Request_Binding
# ═════════════════════════════════════════════════════════════════
def test_bind_contract_carries_identity_without_label_or_provider():
    """binding에는 라벨·provider 문자열이 담기지 않는다(Requirement 8.2, 8.3)."""
    entry = contracts.new_entry("sym-label-a")
    entry["modelId"] = MODEL_ID
    entry["provider"] = "sym-provider-a"
    entry["capabilityFingerprint"] = FINGERPRINT
    route = contracts.Known_Route.OPENAI_RESPONSES
    binding = rb.bind_contract(entry, _route_contract(route))

    assert binding["modelId"] == MODEL_ID
    assert binding["capabilityFingerprint"] == FINGERPRINT
    assert binding["routeKey"] == route
    for field in rb.NON_INPUT_IDENTITY_FIELDS:
        assert field not in binding


def test_bind_contract_does_not_alias_entry_state():
    """binding 수정이 Capability_Map entry로 새지 않는다(깊은 복사)."""
    route = contracts.Known_Route.OPENAI_RESPONSES
    entry = contracts.new_entry("sym-label-a")
    entry["modelId"] = MODEL_ID
    entry["capabilityFingerprint"] = FINGERPRINT
    entry["effort"][route] = {
        "status": str(contracts.Effort_Support_Status.SUPPORTED),
        "contract": _effort_contract(route),
        "evidenceRef": EVIDENCE_ID,
    }
    binding = rb.bind_contract(entry, _route_contract(route))
    binding["effort"]["contract"]["fieldPath"].append("sym-mutated")
    assert entry["effort"][route]["contract"]["fieldPath"] == list(EFFORT_PATH)


def test_effort_view_accepts_entry_and_flat_shapes():
    """Effort_Entry 형태와 평탄 형태를 모두 읽는다."""
    route = contracts.Known_Route.OPENAI_RESPONSES
    nested = {"routeKey": route, "effort": {"status": "SUPPORTED", "contract": {"fieldPath": ["x"]}}}
    flat = {"routeKey": route, "effort": {"status": "SUPPORTED", "fieldPath": ["x"]}}
    assert rb.effort_view(nested) == ("SUPPORTED", {"fieldPath": ["x"]})
    assert rb.effort_view(flat) == ("SUPPORTED", {"fieldPath": ["x"]})
    assert rb.effort_view({"effort": None}) == (contracts.UNDETERMINED, None)


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(pytest.main([__file__, "-q"]))
