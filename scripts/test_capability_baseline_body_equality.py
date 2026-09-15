"""5개 Known_Route의 Baseline_Request_Body 바이트 동일성 회귀 테스트.

Feature: gateway-models-effort-support (task 16.1)
대상: ai_engine/gateway_module.py(`GatewayClient`),
      ai_engine/capability/request_builder.py(`effort_bound_client`, `apply_jobs_model_id`)

무엇을 고정하는가
-----------------
effort가 **선택되지 않은** 상태에서 `EffortBoundClient`를 거친 요청 body가 base
`GatewayClient` body와 바이트 수준으로 같음을 5개 Known_Route 전체에서 고정한다
(Requirement 12.3 — `Managed_Segment` 도입이 기존 route 동작을 바꾸지 않는다).

  1. **builder 수준** — `_build_payload`·`_build_openai_payload`를 인자 조합별로
     원본 전송 바이트(`json.dumps(...).encode()`, 키 순서 포함)와 canonical
     직렬화 바이트로 비교한다.
  2. **transport 수준** — 실제 전송 지점(urllib·httpx·`_openai_request_blocking`)을
     stub으로 가로채 `CONVERSE`·`INVOKE`·`OPENAI_RESPONSES`·
     `OPENAI_RESPONSES_JOBS`·`SSE_STREAM` 5개 route가 내보내는 바이트열을 포착해
     비교한다. route 목록은 `contracts.KNOWN_ROUTES`로 parametrize하므로 신규
     Known_Route가 추가되면 이 테스트가 먼저 실패한다.
  3. **jobs seam** — `_apply_jobs_model_id` 기본 구현이 seam 추출 이전 인라인
     구현(`{**body, "modelId": model_id}`)과 바이트 동일함을 단정한다.

경계
----
- Gateway를 호출하지 않는다. 서명은 stub 자격증명으로 오프라인 계산만 하고,
  네트워크 전송 지점은 전부 stub이다. 통과 사실은 Gateway 지원 근거가 아니다
  (Gateway 지원 주장은 작업 18의 production path probe로만 확정한다).
- model ID·provider·effort field path·effort 허용값은 확정 상수 없이 무작위 심볼
  자리표시자만 사용한다. 실제 값은 evidence만이 채운다.
- tuple·status·domain 불일치 시의 비주입 규칙은
  `scripts/test_capability_effort_bound_client.py`와 Property 3·4가 덮으므로 여기서는
  **effort 미선택**만 다룬다.

실행: ai_engine/.venv/bin/python -m pytest scripts/test_capability_baseline_body_equality.py -q
"""
from __future__ import annotations

import asyncio
import json
import os
import sys
import urllib.request

import httpx
import pytest
from botocore.credentials import Credentials

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from ai_engine.capability import canonicalizer, contracts  # noqa: E402
from ai_engine.capability import request_builder as rb  # noqa: E402
from ai_engine.gateway_module import GatewayClient  # noqa: E402

# ─────────────────────────────────────────────────────────────────
# 자리표시자 심볼 — 실제 model identity·field path·허용값이 아니다.
# ─────────────────────────────────────────────────────────────────
GATEWAY_URL = "https://sym-gateway.invalid/v1"
REGION = "us-west-2"
MODEL_ID = "sym-model-a"
FINGERPRINT = "cfp1:sha256:" + "b" * 64
EVIDENCE_ID = "evr1:sha256:" + "a" * 64
EFFORT_PATH = ["sym-outer", "sym-effort"]
EFFORT_VALUES = ["sym-value-1", "sym-value-2"]
JOB_ID = "sym-job-id"
PROBE_INPUT = "sym-probe-input"
SYSTEM_PROMPT = "sym-system"

MESSAGES = [{"role": "user", "content": [{"text": PROBE_INPUT}]}]
TOOL_CONFIG = {"tools": [{"toolSpec": {"name": "sym-tool"}}]}
INVOKE_BODY = {"sym-field": "sym-value", "sym-count": 1}

#: `_build_payload` 인자 조합 — (model_id, messages, system_prompt, tool_config)
CONVERSE_ARGS = [
    pytest.param(MODEL_ID, MESSAGES, "", None, id="bare-id-minimal"),
    pytest.param(MODEL_ID, MESSAGES, SYSTEM_PROMPT, None, id="bare-id-system"),
    pytest.param(MODEL_ID, MESSAGES, SYSTEM_PROMPT, TOOL_CONFIG, id="bare-id-system-tools"),
    pytest.param("us." + MODEL_ID, MESSAGES, SYSTEM_PROMPT, TOOL_CONFIG, id="us-prefixed-id"),
    pytest.param("eu." + MODEL_ID, MESSAGES, "", TOOL_CONFIG, id="eu-prefixed-id"),
    pytest.param(
        MODEL_ID,
        [
            {"role": "user", "content": [{"text": PROBE_INPUT}]},
            {"role": "assistant", "content": [{"text": "sym-answer"}]},
            {"role": "user", "content": []},
        ],
        SYSTEM_PROMPT,
        None,
        id="multi-turn",
    ),
]

#: `_build_openai_payload` 인자 조합 — (model_id, messages, system_prompt)
OPENAI_ARGS = [
    pytest.param(MODEL_ID, MESSAGES, "", id="list-input-minimal"),
    pytest.param(MODEL_ID, MESSAGES, SYSTEM_PROMPT, id="list-input-instructions"),
    pytest.param(MODEL_ID, PROBE_INPUT, SYSTEM_PROMPT, id="str-input"),
    pytest.param(
        MODEL_ID,
        [
            {"role": "user", "content": PROBE_INPUT},
            {"role": "assistant", "content": [{"text": "sym-answer"}]},
            {"role": "user", "content": []},
            "sym-loose-string",
        ],
        "",
        id="mixed-shapes",
    ),
    pytest.param(MODEL_ID, [], SYSTEM_PROMPT, id="empty-messages"),
]


# ─────────────────────────────────────────────────────────────────
# 비교 유틸
# ─────────────────────────────────────────────────────────────────
def _raw(body) -> bytes:
    """기존 transport가 실제로 전송하는 바이트열(키 순서까지 비교한다)."""
    return json.dumps(body).encode()


def _canonical(body) -> bytes:
    """canonical 직렬화 바이트(키 순서 무관 의미 비교)."""
    return canonicalizer.canonical_bytes(body)


def _assert_body_bytes_equal(actual, expected, label: str) -> None:
    """원본 전송 바이트와 canonical 직렬화 바이트를 모두 비교한다."""
    assert _raw(actual) == _raw(expected), f"{label}: 전송 바이트가 기준선과 다르다"
    assert _canonical(actual) == _canonical(expected), f"{label}: canonical 직렬화가 기준선과 다르다"


def _assert_no_effort_field(body, label: str) -> None:
    """body의 모든 중첩 경로에서 effort 계약 field 발생 횟수가 0이다."""
    for part in EFFORT_PATH:
        assert rb.count_key_occurrences(body, part) == 0, f"{label}: effort field '{part}'가 body에 있다"


# ─────────────────────────────────────────────────────────────────
# 클라이언트 준비 — 네트워크·STS 접근 없음
# ─────────────────────────────────────────────────────────────────
def _base_client() -> GatewayClient:
    """body 생성·서명만 하는 base 클라이언트(자격증명은 stub, 저장하지 않는다)."""
    client = GatewayClient(gateway_url=GATEWAY_URL, region=REGION)
    client._get_creds = lambda: Credentials("SYMACCESSKEYID", "sym-secret-key", "sym-session-token")
    return client


def _effort_contract(route_key: str) -> dict:
    return contracts.new_effort_contract(
        MODEL_ID,
        route_key,
        field_path=list(EFFORT_PATH),
        value_type=contracts.Value_Type.STRING,
        domain_kind=contracts.Domain_Kind.ENUM,
        enum_values=list(EFFORT_VALUES),
        verified_values=list(EFFORT_VALUES),
        evidence_ref=EVIDENCE_ID,
    )


def _binding(route_key: str, **contract_overrides) -> dict:
    """검증된 Route_Contract + `SUPPORTED` effort 계약이 결속된 Request_Binding."""
    entry = contracts.new_entry("sym-label-a")
    entry["modelId"] = MODEL_ID
    entry["capabilityFingerprint"] = FINGERPRINT
    entry["invocationModelIds"] = [MODEL_ID]
    entry["effort"][route_key] = {
        "status": str(contracts.Effort_Support_Status.SUPPORTED),
        "contract": _effort_contract(route_key),
        "evidenceRef": EVIDENCE_ID,
    }
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
    contract.update(contract_overrides)
    return rb.bind_contract(entry, contract, invocation_model_id=MODEL_ID)


def _wrapped_variants(base: GatewayClient, route_key: str) -> list[tuple[str, object]]:
    """effort 미선택 상태의 `EffortBoundClient` 변형 목록.

    계약이 없는 경우와 검증된 계약이 결속된 경우 모두 baseline과 같아야 한다.
    jobs route는 계약이 기존 위치(top-level `modelId`)를 지정한 경우와 계약이
    미확정인 경우(기본 구현 위임)를 함께 확인한다.
    """
    variants: list[tuple[str, object]] = [("no-contract", rb.effort_bound_client(base, None, None))]
    if route_key == contracts.Known_Route.OPENAI_RESPONSES_JOBS:
        variants.append((
            "jobs-contract-top-level",
            rb.effort_bound_client(
                base,
                _binding(route_key, modelIdRequired=True, modelIdFieldPath=[rb.MODEL_ID_KEY]),
                None,
            ),
        ))
        variants.append((
            "jobs-contract-undetermined",
            rb.effort_bound_client(base, _binding(route_key, modelIdRequired=None), None),
        ))
    else:
        variants.append(("bound-contract", rb.effort_bound_client(base, _binding(route_key), None)))
    return variants


# ═════════════════════════════════════════════════════════════════
# transport stub — 전송 바이트만 포착한다(네트워크 없음)
# ═════════════════════════════════════════════════════════════════
CONVERSE_OK = {
    "decision": "ALLOW",
    "output": {"message": {"content": [{"text": "sym-ok"}]}},
}
INVOKE_OK = {"images": ["sym-image"]}
RESPONSES_OK = {"sym-result": "sym-ok"}
JOBS_OK = {"job_id": JOB_ID}
SSE_EVENTS = [{"type": "sym-content", "text": "sym-ok"}]


def _capturing_async_client(sink: list, *, post_json: dict, sse_events: list):
    """`httpx.AsyncClient` 대체 — content 바이트를 sink에 담고 고정 응답을 돌려준다."""

    class _PostResponse:
        status_code = 200

        def json(self):
            return post_json

        @property
        def text(self):
            return json.dumps(post_json)

    class _StreamResponse:
        status_code = 200

        async def aiter_text(self):
            for event in sse_events:
                yield "data: " + json.dumps(event) + "\n"

    class _StreamContext:
        async def __aenter__(self):
            return _StreamResponse()

        async def __aexit__(self, *exc):
            return False

    class _Client:
        def __init__(self, *args, **kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *exc):
            return False

        async def post(self, url, content=None, headers=None, **kwargs):
            sink.append(content)
            return _PostResponse()

        def stream(self, method, url, content=None, headers=None, **kwargs):
            sink.append(content)
            return _StreamContext()

    return _Client


def _capture_converse(monkeypatch, client) -> bytes:
    """`/converse` 전송 바이트(urllib 경계에서 포착)."""
    sink: list[bytes] = []

    class _Response:
        def __init__(self, payload):
            self._raw = json.dumps(payload).encode()

        def read(self):
            return self._raw

    def _fake_urlopen(request, timeout=None):
        sink.append(request.data)
        return _Response(CONVERSE_OK)

    monkeypatch.setattr(urllib.request, "urlopen", _fake_urlopen)
    asyncio.run(client.converse(MODEL_ID, MESSAGES, SYSTEM_PROMPT, TOOL_CONFIG))
    assert len(sink) == 1, "converse는 요청을 정확히 1건 전송해야 한다"
    return sink[0]


def _capture_invoke(monkeypatch, client) -> bytes:
    """`/invoke` 전송 바이트(httpx 경계에서 포착)."""
    sink: list[bytes] = []
    monkeypatch.setattr(
        httpx, "AsyncClient", _capturing_async_client(sink, post_json=INVOKE_OK, sse_events=[])
    )
    asyncio.run(client.invoke_model(MODEL_ID, INVOKE_BODY))
    assert len(sink) == 1, "invoke_model은 요청을 정확히 1건 전송해야 한다"
    return sink[0]


def _capture_sse(monkeypatch, client) -> bytes:
    """SSE_Stream_Route 전송 바이트(httpx stream 경계에서 포착)."""
    sink: list[bytes] = []
    monkeypatch.setattr(
        httpx, "AsyncClient", _capturing_async_client(sink, post_json={}, sse_events=SSE_EVENTS)
    )

    async def _drain():
        events = []
        async for event in client.stream_sse_realtime(MODEL_ID, MESSAGES, SYSTEM_PROMPT, TOOL_CONFIG):
            events.append(event)
        return events

    events = asyncio.run(_drain())
    assert events == SSE_EVENTS, "stub SSE 이벤트가 그대로 전달되어야 한다"
    assert len(sink) == 1, "SSE 경로는 요청을 정확히 1건 전송해야 한다"
    return sink[0]


def _capture_openai(monkeypatch, client, *, jobs: bool) -> bytes:
    """OpenAI Responses 동기·잡 제출 전송 바이트(`_openai_request_blocking` 경계)."""
    sink: list[bytes] = []
    payload = JOBS_OK if jobs else RESPONSES_OK

    def _fake_blocking(method, url, body_bytes, timeout):
        sink.append(body_bytes)
        return {"status": 200, "body": json.dumps(payload), "json": payload}

    monkeypatch.setattr(client, "_openai_request_blocking", _fake_blocking)
    if jobs:
        job_id = asyncio.run(client.openai_responses_job_submit(MODEL_ID, MESSAGES, SYSTEM_PROMPT))
        assert job_id == JOB_ID
    else:
        asyncio.run(client.openai_responses_sync(MODEL_ID, MESSAGES, SYSTEM_PROMPT))
    assert len(sink) == 1, "OpenAI 경로는 요청을 정확히 1건 전송해야 한다"
    return sink[0]


def _capture_responses_sync(monkeypatch, client) -> bytes:
    return _capture_openai(monkeypatch, client, jobs=False)


def _capture_jobs_submit(monkeypatch, client) -> bytes:
    return _capture_openai(monkeypatch, client, jobs=True)


#: Known_Route → 전송 바이트 포착 함수. 5개 route 전체를 덮는다.
CAPTURES = {
    contracts.Known_Route.CONVERSE: _capture_converse,
    contracts.Known_Route.INVOKE: _capture_invoke,
    contracts.Known_Route.OPENAI_RESPONSES: _capture_responses_sync,
    contracts.Known_Route.OPENAI_RESPONSES_JOBS: _capture_jobs_submit,
    contracts.Known_Route.SSE_STREAM: _capture_sse,
}


# ═════════════════════════════════════════════════════════════════
# builder 수준 바이트 동일성 (Requirement 12.3)
# ═════════════════════════════════════════════════════════════════
@pytest.mark.parametrize("model_id, messages, system_prompt, tool_config", CONVERSE_ARGS)
@pytest.mark.parametrize("env_cap", [None, "1024"])
def test_build_payload_bytes_identical_without_effort(
    monkeypatch, model_id, messages, system_prompt, tool_config, env_cap
):
    """`_build_payload` 결과가 base와 바이트 동일하다(Converse·SSE 공용 builder)."""
    if env_cap is None:
        monkeypatch.delenv("AE_MAX_TOKENS", raising=False)
    else:
        monkeypatch.setenv("AE_MAX_TOKENS", env_cap)

    base = _base_client()
    expected = base._build_payload(model_id, messages, system_prompt, tool_config)
    for label, client in _wrapped_variants(_base_client(), contracts.Known_Route.CONVERSE):
        actual = client._build_payload(model_id, messages, system_prompt, tool_config)
        _assert_body_bytes_equal(actual, expected, f"_build_payload/{label}")
        _assert_no_effort_field(actual, f"_build_payload/{label}")


@pytest.mark.parametrize("model_id, messages, system_prompt", OPENAI_ARGS)
def test_build_openai_payload_bytes_identical_without_effort(model_id, messages, system_prompt):
    """`_build_openai_payload` 결과가 base와 바이트 동일하고 `modelId` key가 0개다."""
    base = _base_client()
    expected = base._build_openai_payload(model_id, messages, system_prompt)
    assert rb.count_key_occurrences(expected, rb.MODEL_ID_KEY) == 0

    for label, client in _wrapped_variants(_base_client(), contracts.Known_Route.OPENAI_RESPONSES):
        actual = client._build_openai_payload(model_id, messages, system_prompt)
        _assert_body_bytes_equal(actual, expected, f"_build_openai_payload/{label}")
        _assert_no_effort_field(actual, f"_build_openai_payload/{label}")
        assert rb.count_key_occurrences(actual, rb.MODEL_ID_KEY) == 0


# ═════════════════════════════════════════════════════════════════
# jobs seam 기본 구현 = 기존 인라인 동작 (Requirement 12.3)
# ═════════════════════════════════════════════════════════════════
def _legacy_jobs_body(base: GatewayClient, model_id, messages, system_prompt="") -> dict:
    """seam 추출 이전 `openai_responses_job_submit`의 인라인 구현 재현.

        body = self._build_openai_payload(model_id, messages, system_prompt)
        body = {**body, "modelId": model_id}
    """
    body = base._build_openai_payload(model_id, messages, system_prompt)
    return {**body, "modelId": model_id}


@pytest.mark.parametrize("model_id, messages, system_prompt", OPENAI_ARGS)
def test_apply_jobs_model_id_default_is_byte_identical_to_legacy_inline(model_id, messages, system_prompt):
    """`_apply_jobs_model_id` 기본 구현이 기존 인라인 동작과 바이트 동일하다."""
    base = _base_client()
    body = base._build_openai_payload(model_id, messages, system_prompt)
    snapshot = _raw(body)

    applied = base._apply_jobs_model_id(body, model_id)
    _assert_body_bytes_equal(applied, _legacy_jobs_body(base, model_id, messages, system_prompt), "jobs-seam")
    assert rb.count_key_occurrences(applied, rb.MODEL_ID_KEY) == 1
    assert applied[rb.MODEL_ID_KEY] == model_id
    # 입력 body는 변형하지 않는다(기존 인라인 구현과 동일한 성질).
    assert _raw(body) == snapshot
    assert rb.count_key_occurrences(body, rb.MODEL_ID_KEY) == 0


def test_jobs_submit_transmits_legacy_inline_bytes(monkeypatch):
    """jobs 제출 전송 바이트가 seam 추출 이전 구현의 바이트와 같다."""
    base = _base_client()
    expected = _raw(_legacy_jobs_body(_base_client(), MODEL_ID, MESSAGES, SYSTEM_PROMPT))

    assert _capture_jobs_submit(monkeypatch, base) == expected
    for label, client in _wrapped_variants(_base_client(), contracts.Known_Route.OPENAI_RESPONSES_JOBS):
        assert _capture_jobs_submit(monkeypatch, client) == expected, f"jobs 전송 바이트 불일치: {label}"


# ═════════════════════════════════════════════════════════════════
# transport 수준 — 5개 Known_Route 전체 (Requirement 12.3)
# ═════════════════════════════════════════════════════════════════
def test_capture_table_covers_every_known_route():
    """포착 표가 5개 Known_Route를 모두 덮는다(신규 route 추가 시 실패한다)."""
    assert tuple(sorted(CAPTURES)) == tuple(sorted(contracts.KNOWN_ROUTES))
    assert len(CAPTURES) == 5


@pytest.mark.parametrize("route_key", contracts.KNOWN_ROUTES)
def test_route_transmits_baseline_bytes_without_effort(monkeypatch, route_key):
    """effort 미선택 시 route별 전송 바이트가 base `GatewayClient`와 동일하다."""
    capture = CAPTURES[route_key]
    baseline = capture(monkeypatch, _base_client())
    baseline_body = json.loads(baseline.decode())

    for label, client in _wrapped_variants(_base_client(), route_key):
        actual = capture(monkeypatch, client)
        tag = f"{route_key}/{label}"
        assert actual == baseline, f"{tag}: 전송 바이트가 기준선과 다르다"
        body = json.loads(actual.decode())
        assert _canonical(body) == _canonical(baseline_body), f"{tag}: canonical 직렬화 불일치"
        _assert_no_effort_field(body, tag)


def test_sse_body_keeps_inference_config_override(monkeypatch):
    """SSE 경로의 maxTokens 축소 규칙이 wrapper에서도 그대로 유지된다."""
    base_bytes = _capture_sse(monkeypatch, _base_client())
    base_body = json.loads(base_bytes.decode())
    assert "inferenceConfig" in base_body and "maxTokens" in base_body["inferenceConfig"]

    binding = _binding(contracts.Known_Route.SSE_STREAM)
    wrapped = rb.effort_bound_client(_base_client(), binding, None)
    assert _capture_sse(monkeypatch, wrapped) == base_bytes


def test_converse_body_matches_builder_output(monkeypatch):
    """`/converse` 전송 바이트가 `_build_payload` 결과와 정확히 같다(중간 변형 없음)."""
    base = _base_client()
    expected = _raw(base._build_payload(MODEL_ID, MESSAGES, SYSTEM_PROMPT, TOOL_CONFIG))
    assert _capture_converse(monkeypatch, base) == expected
    assert _capture_converse(monkeypatch, rb.effort_bound_client(_base_client(), None, None)) == expected


def test_responses_sync_body_matches_builder_output(monkeypatch):
    """`/openai/responses` 전송 바이트가 `_build_openai_payload` 결과와 같다."""
    base = _base_client()
    expected = _raw(base._build_openai_payload(MODEL_ID, MESSAGES, SYSTEM_PROMPT))
    assert _capture_responses_sync(monkeypatch, base) == expected
    wrapped = rb.effort_bound_client(_base_client(), _binding(contracts.Known_Route.OPENAI_RESPONSES), None)
    assert _capture_responses_sync(monkeypatch, wrapped) == expected


def test_invoke_body_matches_inline_payload(monkeypatch):
    """`/invoke` 전송 바이트가 기존 인라인 payload 구성과 같다."""
    expected = _raw({"modelId": MODEL_ID, "body": INVOKE_BODY})
    assert _capture_invoke(monkeypatch, _base_client()) == expected
    wrapped = rb.effort_bound_client(_base_client(), _binding(contracts.Known_Route.INVOKE), None)
    assert _capture_invoke(monkeypatch, wrapped) == expected


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(pytest.main([__file__, "-q"]))
