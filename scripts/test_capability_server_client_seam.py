"""Unit — 서버 요청 경로의 capability client seam.

Feature: gateway-models-effort-support (task 15.1)
대상: ai_engine.server.capability_plan_for / capability_client_for /
      capability_gw_for / capability_route_for_transport /
      _capability_effort_selection / _capability_block_sse_response

검증 범위
  - Managed_Segment entry가 아니면 `(None, None)`을 반환하고 전송 클라이언트가 기존
    `gw` **동일 객체**로 유지된다 (Requirement 1.13, 12.2)
  - `effort`가 요청 body에 없으면 selection이 `None`으로 전달되어 생성 body가
    Baseline_Request_Body와 **바이트 동일**하다 (Requirement 7.16, 7.17, 12.3)
  - tuple(modelId·route·Capability_Fingerprint)이 모두 일치하는 유효 effort만
    계약 field path에 **정확히 1회** 기록된다 (Requirement 8.16)
  - tuple 불일치·domain 이탈 effort는 baseline body를 바꾸지 않는다 (Requirement 8.18, 8.19)
  - 선택 route가 `SUPPORTED`가 아니거나 Eligible_Contract가 없으면 Gateway 전송을
    생성하지 않고(전송 0건) Failure_Handler 경로로 종료한다 (Requirement 8.20, 8.22)
  - `is_openai_model`·`route_openai_chat`·`_resolve_callable_model_id`의
    Baseline_Catalog_Segment 동작(시그니처·판정)이 불변이다 (Requirement 12.2)
  - 영속 경로는 `userData/capability/` 하위만 사용한다 (Requirement 10.15)

이 테스트의 fixture 계약값(route·effort field path·허용값)은 **합성 값**이며 Gateway
지원 근거가 아니다. 이 파일은 Gateway를 호출하지 않으므로 통과 사실이 어떤 모델·route·
effort의 지원 근거가 되지 않는다(Requirement 12.22).

실행: ai_engine/.venv/bin/python -m pytest scripts/test_capability_server_client_seam.py -q
"""
from __future__ import annotations

import inspect
import json
import os
import sys

import pytest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from ai_engine import server  # noqa: E402  (import 시 배너 출력 — 수집 단계에서 1회)
from ai_engine.capability import activation_gate as ag  # noqa: E402
from ai_engine.capability import capability_map as cm  # noqa: E402
from ai_engine.capability import contracts  # noqa: E402
from ai_engine.capability import failure_handler as fh  # noqa: E402
from ai_engine.capability import request_builder as rb  # noqa: E402
from ai_engine.gateway_module import GatewayClient  # noqa: E402

SSE_STREAM = str(contracts.Known_Route.SSE_STREAM)
OPENAI_RESPONSES = str(contracts.Known_Route.OPENAI_RESPONSES)

MODEL_ID = "managed.model.seam"
EVIDENCE_REF = "evr1:sha256:seam0001"
EFFORT_FIELD_PATH = ["fieldAlpha", "fieldBeta"]
EFFORT_VALUES = ["sym-a", "sym-b"]

MESSAGES = [{"role": "user", "content": [{"text": "고정 비민감 입력"}]}]


# ─────────────────────────────────────────────────────────────────
# fixture 빌더 — 합성 계약값으로 Activation_Gate 통과 entry를 만든다
# ─────────────────────────────────────────────────────────────────
def _route_contract(route_key: str, *, purposes) -> dict:
    return contracts.new_route_contract(
        route_key,
        endpoint_ref="stream-route-ref",
        http_method="POST",
        execution_mode=contracts.Execution_Mode.STREAMING,
        signing_service=contracts.Signing_Service.LAMBDA,
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
        evidence_ref=EVIDENCE_REF,
    )


def _effort_contract(route_key: str) -> dict:
    return contracts.new_effort_contract(
        MODEL_ID,
        route_key,
        field_path=list(EFFORT_FIELD_PATH),
        value_type=contracts.Value_Type.STRING,
        domain_kind=contracts.Domain_Kind.ENUM,
        enum_values=list(EFFORT_VALUES),
        verified_values=list(EFFORT_VALUES),
        evidence_ref=EVIDENCE_REF,
    )


def make_active_entry(
    *,
    route_key: str = SSE_STREAM,
    purposes=("stream", "chat"),
    with_effort: bool = True,
) -> dict:
    """Activation_Gate를 통과하는 유효 entry(합성 계약값)."""
    entry = contracts.new_entry("candidate-seam", source_kind=contracts.Source_Kind.CATALOG)
    entry["modelId"] = MODEL_ID
    entry["provider"] = "ManagedProvider"
    entry["catalogFingerprint"] = "cat1:sha256:seam1234"
    entry["revision"] = "rev-fixture"
    entry["verifiedAt"] = "2026-08-03T01:00:00Z"
    entry["evidence"] = [EVIDENCE_REF]
    entry["activeEvidenceRef"] = EVIDENCE_REF
    entry["routes"][route_key] = {
        "status": str(contracts.Route_Support_Status.SUPPORTED),
        "allowlist": str(contracts.Allowlist_Result.ALLOWED),
        "contract": _route_contract(route_key, purposes=purposes),
        "evidenceRef": EVIDENCE_REF,
    }
    if with_effort:
        entry["effort"][route_key] = {
            "status": str(contracts.Effort_Support_Status.SUPPORTED),
            "contract": _effort_contract(route_key),
            "evidenceRef": EVIDENCE_REF,
        }
    entry["verificationStatus"] = str(contracts.Verification_Status.VERIFIED)
    cm.apply_mode_support(entry)
    cm.recompute_fingerprint(entry)
    return entry


def _write_map(tmp_path, monkeypatch, entry_list) -> None:
    """`userData/capability/capability_map.json`에 map을 기록한다(경로 격리)."""
    monkeypatch.setenv("AE_USERDATA_PATH", str(tmp_path))
    monkeypatch.delenv("AE_GENERATED_ROOT", raising=False)
    path = cm.save(cm.new_map(entries=entry_list))
    assert str(tmp_path) in str(path), path
    assert os.path.join("capability", "capability_map.json") in str(path), path


@pytest.fixture()
def gw() -> GatewayClient:
    """실제 GatewayClient(생성만 — 네트워크·자격증명 접근 없음)."""
    return GatewayClient(gateway_url="https://example.invalid/v1", aws_profile="p", bedrock_user="")


def _effort_body(entry, *, route=SSE_STREAM, value=EFFORT_VALUES[0], **overrides) -> dict:
    """프론트가 body에 담아 보내는 effort 형태(작업 14.2)."""
    payload = {
        "modelId": entry["modelId"],
        "route": route,
        "capabilityFingerprint": entry["capabilityFingerprint"],
        "value": value,
    }
    payload.update(overrides)
    return payload


def _canon(body) -> str:
    return json.dumps(body, sort_keys=True, ensure_ascii=False, separators=(",", ":"))


# ─────────────────────────────────────────────────────────────────
# fixture 전제 — 병합·라우팅 입력이 Activation_Gate 산출물이어야 한다
# ─────────────────────────────────────────────────────────────────
def test_fixture_entry_passes_activation_gate():
    entry = make_active_entry()
    assert cm.entry_malformed_reasons(entry) == []
    active, reason = ag.is_active(entry, {})
    assert active is True, reason


# ─────────────────────────────────────────────────────────────────
# Managed_Segment 아님 → 기존 경로 (Requirement 1.13, 12.2)
# ─────────────────────────────────────────────────────────────────
def test_unmanaged_model_returns_none_pair(tmp_path, monkeypatch, gw):
    """Capability_Map이 없으면 `(None, None)` → 호출자는 기존 `gw`를 그대로 쓴다."""
    monkeypatch.setenv("AE_USERDATA_PATH", str(tmp_path))
    plan = server.capability_plan_for("anthropic.claude-sonnet-4-5-20250929-v1:0")
    assert plan["managed"] is False
    assert plan["transmit"] is False
    assert plan["blocked"] is False

    client, binding = server.capability_client_for(
        gw, "anthropic.claude-sonnet-4-5-20250929-v1:0", plan=plan
    )
    assert (client, binding) == (None, None)
    assert server.capability_gw_for(gw, client, binding, "anthropic.claude-sonnet-4-5") is gw


def test_managed_map_present_but_other_model_is_unmanaged(tmp_path, monkeypatch, gw):
    """Managed_Segment에 없는 model은 관리 대상이 아니다(기존 경로 유지)."""
    _write_map(tmp_path, monkeypatch, [make_active_entry()])
    plan = server.capability_plan_for("some.other.model")
    assert plan["managed"] is False
    assert server.capability_client_for(gw, "some.other.model", plan=plan) == (None, None)


def test_capability_path_failure_falls_back_to_existing_gw(tmp_path, monkeypatch, gw, capsys):
    """capability 경로가 예외를 던지면 기존 `gw`로 폴백한다(전송 경로 보존)."""
    _write_map(tmp_path, monkeypatch, [make_active_entry()])

    def _boom(*_a, **_k):
        raise RuntimeError("x" * 400)

    monkeypatch.setattr(ag, "active_models", _boom)
    plan = server.capability_plan_for(MODEL_ID, purpose=server.CAPABILITY_PURPOSE_STREAM)
    assert plan["managed"] is False
    assert server.capability_client_for(gw, MODEL_ID, plan=plan) == (None, None)
    logged = capsys.readouterr().out
    assert "[Capability] 요청 경로 seam 생략" in logged
    assert "x" * 201 not in logged  # 원인 문자열 200자 절단


# ─────────────────────────────────────────────────────────────────
# effort 없음 → baseline body 바이트 동일 (Requirement 7.16, 7.17, 12.3)
# ─────────────────────────────────────────────────────────────────
def test_managed_without_effort_builds_baseline_bytes(tmp_path, monkeypatch, gw):
    entry = make_active_entry()
    _write_map(tmp_path, monkeypatch, [entry])

    plan = server.capability_plan_for(
        MODEL_ID,
        purpose=server.CAPABILITY_PURPOSE_STREAM,
        effort=None,
        route=SSE_STREAM,
        invocation_model_id=MODEL_ID,
    )
    assert plan["managed"] is True
    assert plan["transmit"] is True
    assert plan["blocked"] is False
    assert plan["routeKey"] == SSE_STREAM
    assert plan["selection"] is None  # effort 미제공 → 미선택

    client, binding = server.capability_client_for(
        gw, MODEL_ID, purpose=server.CAPABILITY_PURPOSE_STREAM, effort=None, plan=plan
    )
    assert client is not None and binding is not None
    assert server.capability_gw_for(gw, client, binding, MODEL_ID) is client

    baseline = gw._build_payload(MODEL_ID, MESSAGES, "system-text")
    produced = client._build_payload(MODEL_ID, MESSAGES, "system-text")
    assert _canon(produced) == _canon(baseline)
    assert rb.count_key_occurrences(produced, EFFORT_FIELD_PATH[0]) == 0

    baseline_openai = gw._build_openai_payload(MODEL_ID, MESSAGES, "system-text")
    produced_openai = client._build_openai_payload(MODEL_ID, MESSAGES, "system-text")
    assert _canon(produced_openai) == _canon(baseline_openai)


def test_credentials_and_transport_config_delegated(tmp_path, monkeypatch, gw):
    """신규 credential 캐시·URL을 만들지 않는다(base 위임 — Requirement 10.1~10.6)."""
    entry = make_active_entry()
    _write_map(tmp_path, monkeypatch, [entry])
    client, _binding = server.capability_client_for(
        gw, MODEL_ID, purpose=server.CAPABILITY_PURPOSE_STREAM, effort=None
    )
    assert client is not None
    assert client.gateway_url == gw.gateway_url
    assert client.region == gw.region
    assert client.STREAM_URL == gw.STREAM_URL
    assert rb.unexpected_effort_bound_overrides() == ()

    calls = []
    monkeypatch.setattr(gw, "_get_creds", lambda: calls.append("creds") or "creds-token")
    assert client._get_creds() == "creds-token"
    assert calls == ["creds"]


# ─────────────────────────────────────────────────────────────────
# 유효 effort → exact path 1회 (Requirement 8.16)
# ─────────────────────────────────────────────────────────────────
def test_valid_effort_injected_exactly_once(tmp_path, monkeypatch, gw):
    entry = make_active_entry()
    _write_map(tmp_path, monkeypatch, [entry])

    effort = _effort_body(entry)
    plan = server.capability_plan_for(
        MODEL_ID,
        purpose=server.CAPABILITY_PURPOSE_STREAM,
        effort=effort,
        route=SSE_STREAM,
        invocation_model_id=MODEL_ID,
    )
    assert plan["selection"] == {
        "modelId": MODEL_ID,
        "route": SSE_STREAM,
        "capabilityFingerprint": entry["capabilityFingerprint"],
        "value": EFFORT_VALUES[0],
        "valueType": str(contracts.Value_Type.STRING),
    }

    client, _binding = server.capability_client_for(
        gw, MODEL_ID, purpose=server.CAPABILITY_PURPOSE_STREAM, effort=effort, plan=plan
    )
    baseline = gw._build_payload(MODEL_ID, MESSAGES, "system-text")
    produced = client._build_payload(MODEL_ID, MESSAGES, "system-text")

    assert rb.value_at_path(produced, EFFORT_FIELD_PATH) == EFFORT_VALUES[0]
    assert rb.count_key_occurrences(produced, EFFORT_FIELD_PATH[-1]) == 1
    # 주입 경로만 다르고 나머지는 baseline과 동일하다.
    stripped = {k: v for k, v in produced.items() if k != EFFORT_FIELD_PATH[0]}
    assert _canon(stripped) == _canon(baseline)


@pytest.mark.parametrize(
    "overrides",
    [
        {"modelId": "other.model"},                    # modelId 불일치 (7.7)
        {"route": OPENAI_RESPONSES},                   # route 불일치 (7.8)
        {"capabilityFingerprint": "cfp1:sha256:dead"},  # fingerprint 불일치 (7.9)
        {"value": "sym-not-verified"},                 # verified domain 이탈 (7.10)
    ],
)
def test_mismatched_effort_keeps_baseline_bytes(tmp_path, monkeypatch, gw, overrides):
    """tuple 불일치·domain 이탈 effort는 생성 body를 바꾸지 않는다."""
    entry = make_active_entry()
    _write_map(tmp_path, monkeypatch, [entry])

    effort = _effort_body(entry, **overrides)
    plan = server.capability_plan_for(
        MODEL_ID, purpose=server.CAPABILITY_PURPOSE_STREAM, effort=effort, route=SSE_STREAM
    )
    assert plan["transmit"] is True
    assert plan["selection"] is None

    client, _binding = server.capability_client_for(
        gw, MODEL_ID, purpose=server.CAPABILITY_PURPOSE_STREAM, effort=effort, plan=plan
    )
    baseline = gw._build_payload(MODEL_ID, MESSAGES, "system-text")
    produced = client._build_payload(MODEL_ID, MESSAGES, "system-text")
    assert _canon(produced) == _canon(baseline)
    assert rb.count_key_occurrences(produced, EFFORT_FIELD_PATH[0]) == 0


# ─────────────────────────────────────────────────────────────────
# 미지원 route·Eligible_Contract 부재 → 전송 0건 (Requirement 8.20, 8.22)
# ─────────────────────────────────────────────────────────────────
@pytest.mark.parametrize(
    "route,purposes,expected_reason",
    [
        # 요청 route가 `SUPPORTED`가 아니다 → 그 route의 전송 건수 0 (8.20)
        (OPENAI_RESPONSES, ("stream", "chat"), rb.ROUTE_NOT_SUPPORTED),
        # 요청 목적을 충족하는 route가 없다 → 그 route의 전송 건수 0 (8.20)
        (SSE_STREAM, ("chat",), rb.ROUTE_PURPOSE_UNMET),
        # Eligible_Contract 자체가 없다 → fallback 전송도 생성하지 않는다 (8.22)
        (None, ("chat",), rb.NO_ELIGIBLE_CONTRACT),
    ],
)
def test_blocked_route_produces_zero_transmissions(
    tmp_path, monkeypatch, gw, route, purposes, expected_reason
):
    entry = make_active_entry(purposes=purposes)
    _write_map(tmp_path, monkeypatch, [entry])

    plan = server.capability_plan_for(
        MODEL_ID, purpose=server.CAPABILITY_PURPOSE_STREAM, effort=None, route=route
    )
    assert plan["managed"] is True
    assert plan["transmit"] is False
    assert plan["blocked"] is True
    assert plan["binding"] is None
    assert plan["reason"] == expected_reason

    # Gateway 전송을 생성할 수 없다(client 부재 → 전송 건수 0).
    assert server.capability_client_for(
        gw, MODEL_ID, purpose=server.CAPABILITY_PURPOSE_STREAM, plan=plan
    ) == (None, None)

    # Failure_Handler가 복구 계획을 산출한다. 이 seam은 endpoint transport를 바꾸지
    # 않으므로 fallback을 **수행하지 않고** 사실만 보고하며 종료한다.
    recovery = plan["recovery"]
    assert recovery["reasonCode"] in fh.RECOVERY_REASONS
    assert recovery["retryTransient"] is False
    assert recovery["retryWithoutEffort"] is False

    note = plan["notification"]
    assert set(note) == set(fh.NOTIFICATION_FIELDS)
    assert note["modelId"] == MODEL_ID
    assert note["category"] in contracts.Failure_Category.values()
    assert note["retryCount"] == 0
    assert note["fallback"] == fh.NO_FALLBACK  # fallback 미수행을 명시


def test_no_eligible_contract_terminates_without_fallback(tmp_path, monkeypatch, gw):
    """Eligible_Contract가 없으면 fallback 후보도 없어 오류 상태로 종료한다(8.22)."""
    entry = make_active_entry(purposes=("chat",))
    _write_map(tmp_path, monkeypatch, [entry])

    plan = server.capability_plan_for(MODEL_ID, purpose=server.CAPABILITY_PURPOSE_STREAM)
    assert plan["blocked"] is True
    assert plan["reason"] == rb.NO_ELIGIBLE_CONTRACT
    assert plan["recovery"]["terminate"] is True
    assert plan["recovery"]["fallbackContract"] is None
    assert server.capability_client_for(gw, MODEL_ID, plan=plan) == (None, None)


def test_entry_without_eligible_contract_is_not_managed(tmp_path, monkeypatch, gw):
    """allowlist가 `ALLOWED`가 아니면 Activation_Gate가 탈락시켜 Managed_Segment에 없다.

    이때 seam은 관리 대상이 아니라고 판정하므로 기존 `gw` 경로가 그대로 유지된다.
    """
    entry = make_active_entry()
    entry["routes"][SSE_STREAM]["allowlist"] = str(contracts.Allowlist_Result.UNVERIFIED)
    cm.recompute_fingerprint(entry)
    _write_map(tmp_path, monkeypatch, [entry])

    assert ag.active_model_ids(cm.load(), {}) == []
    plan = server.capability_plan_for(MODEL_ID, purpose=server.CAPABILITY_PURPOSE_STREAM)
    assert plan["managed"] is False
    assert plan["blocked"] is False
    client, binding = server.capability_client_for(gw, MODEL_ID, plan=plan)
    assert (client, binding) == (None, None)
    assert server.capability_gw_for(gw, client, binding, MODEL_ID) is gw


def test_block_sse_response_is_event_stream_without_secrets(tmp_path, monkeypatch):
    """차단 응답은 User_Notification 화이트리스트만 담은 SSE로 종료한다."""
    entry = make_active_entry(purposes=("chat",))
    _write_map(tmp_path, monkeypatch, [entry])
    plan = server.capability_plan_for(
        MODEL_ID, purpose=server.CAPABILITY_PURPOSE_STREAM, route=SSE_STREAM
    )
    assert plan["blocked"] is True

    response = server._capability_block_sse_response(plan)
    assert response.media_type == "text/event-stream"

    async def _collect():
        return [chunk async for chunk in response.body_iterator]

    import asyncio

    chunks = asyncio.run(_collect())
    text = "".join(chunks)
    assert text.endswith("data: [DONE]\n\n")
    payload = json.loads(text.split("\n\n")[0][len("data: "):])
    assert set(payload["capability"]) == set(fh.NOTIFICATION_FIELDS)
    for secret in ("authorization", "cookie", "signature", "secret", "accessKey"):
        assert secret.lower() not in text.lower()


# ─────────────────────────────────────────────────────────────────
# route 매핑과 Baseline_Catalog_Segment 불변 (Requirement 12.2)
# ─────────────────────────────────────────────────────────────────
def test_route_for_transport_follows_existing_branch(monkeypatch):
    """`capability_route_for_transport`는 기존 `is_openai_model` 분기를 그대로 따른다."""
    monkeypatch.setattr(server, "_GATEWAY_MODEL_CACHE", {"models": [], "last_fetched": 0, "ttl": 300})
    assert server.capability_route_for_transport("openai.gpt-x") == OPENAI_RESPONSES
    assert server.capability_route_for_transport("anthropic.claude-x") == SSE_STREAM
    # 기존 판정 함수 자체는 변하지 않는다.
    assert server.is_openai_model("openai.gpt-x") is True
    assert server.is_openai_model("anthropic.claude-x") is False


def test_baseline_route_symbols_signatures_unchanged():
    """seam이 기존 route 심볼의 시그니처를 바꾸지 않았다."""
    assert str(inspect.signature(server.is_openai_model)) == (
        "(model_id: str, openai_ids: set | None = None) -> bool"
    )
    assert str(inspect.signature(server.route_openai_chat)) == (
        "(gw, model_id, messages, system_prompt='', timeout=120)"
    )
    assert str(inspect.signature(server._resolve_callable_model_id)) == (
        "(model_id, aws_profile, bedrock_user)"
    )
    assert str(inspect.signature(server.capability_client_for)) == (
        "(gw, model, purpose='chat', effort=None, plan=None)"
    )


# ─────────────────────────────────────────────────────────────────
# 재라우팅 방어 — 다른 model로 보낼 때는 기존 `gw`를 쓴다
# ─────────────────────────────────────────────────────────────────
def test_gw_for_rejects_unbound_model_id(tmp_path, monkeypatch, gw):
    """`model_denied` 재라우팅 등으로 model ID가 바뀌면 capability client를 쓰지 않는다."""
    entry = make_active_entry()
    _write_map(tmp_path, monkeypatch, [entry])
    client, binding = server.capability_client_for(
        gw, MODEL_ID, purpose=server.CAPABILITY_PURPOSE_STREAM, effort=_effort_body(entry)
    )
    assert client is not None
    assert server.capability_gw_for(gw, client, binding, MODEL_ID) is client
    assert server.capability_gw_for(gw, client, binding, "us." + MODEL_ID) is gw
    assert server.capability_gw_for(gw, client, binding, "anthropic.claude-x") is gw


# ─────────────────────────────────────────────────────────────────
# 엔드포인트 seam 실행 — 실제 전송 클라이언트와 생성 body 확인
# ─────────────────────────────────────────────────────────────────
class _FakeRequest:
    """`await request.json()`만 사용하는 핸들러용 최소 stub."""

    def __init__(self, payload: dict) -> None:
        self._payload = payload

    async def json(self) -> dict:
        return self._payload


def _run_stream_endpoint(monkeypatch, base_gw, payload: dict) -> dict:
    """`/api/agents/run-stream`을 실행하고 실제 전송 클라이언트·body를 캡처한다."""
    import asyncio

    seen: dict = {"clients": [], "payloads": [], "calls": []}

    def _fake_stream_sse_realtime(self, model_id, messages, system_prompt="", tool_config=None):
        seen["clients"].append(self)
        seen["calls"].append((model_id, messages, system_prompt, tool_config))
        seen["payloads"].append(self._build_payload(model_id, messages, system_prompt, tool_config))

        async def _events():
            yield {"type": "content_block_delta", "delta": {"text": "ok"}}
            yield {"type": "message_stop", "stopReason": "end_turn"}

        return _events()

    monkeypatch.setattr(server, "_get_gw", lambda *_a, **_k: base_gw)
    monkeypatch.setattr(GatewayClient, "stream_sse_realtime", _fake_stream_sse_realtime)
    monkeypatch.setattr(server, "_resolve_callable_model_id", lambda mid, *_a, **_k: mid)
    monkeypatch.setattr(server, "_maybe_summarize", lambda *_a, **_k: asyncio.sleep(0))

    async def _drive():
        response = await server.run_agent_stream(_FakeRequest(payload))
        return "".join([chunk async for chunk in response.body_iterator])

    seen["sse"] = asyncio.run(_drive())
    return seen


def test_endpoint_uses_base_gw_when_unmanaged(tmp_path, monkeypatch, gw):
    """Managed_Segment가 비면 전송 클라이언트가 기존 `gw` 동일 객체다(기준선 동일)."""
    monkeypatch.setenv("AE_USERDATA_PATH", str(tmp_path))
    seen = _run_stream_endpoint(
        monkeypatch, gw, {"prompt": "고정 입력", "model": "anthropic.claude-x"}
    )
    assert seen["clients"] and all(client is gw for client in seen["clients"])
    assert all(
        rb.count_key_occurrences(body, EFFORT_FIELD_PATH[0]) == 0 for body in seen["payloads"]
    )
    assert "data: [DONE]" in seen["sse"]


def test_endpoint_injects_effort_for_managed_model(tmp_path, monkeypatch, gw):
    """tuple 일치 effort는 계약 field path에 1회 실려 전송된다."""
    entry = make_active_entry()
    _write_map(tmp_path, monkeypatch, [entry])
    seen = _run_stream_endpoint(
        monkeypatch,
        gw,
        {"prompt": "고정 입력", "model": MODEL_ID, "effort": _effort_body(entry)},
    )
    assert seen["clients"] and all(client is not gw for client in seen["clients"])
    for body in seen["payloads"]:
        assert rb.value_at_path(body, EFFORT_FIELD_PATH) == EFFORT_VALUES[0]
        assert rb.count_key_occurrences(body, EFFORT_FIELD_PATH[-1]) == 1


def test_endpoint_without_effort_sends_baseline_body(tmp_path, monkeypatch, gw):
    """Managed_Segment 모델이라도 effort가 없으면 생성 body가 baseline과 바이트 동일하다."""
    entry = make_active_entry()
    _write_map(tmp_path, monkeypatch, [entry])
    seen = _run_stream_endpoint(monkeypatch, gw, {"prompt": "고정 입력", "model": MODEL_ID})
    assert seen["payloads"]
    for body, call in zip(seen["payloads"], seen["calls"]):
        baseline = GatewayClient._build_payload(gw, *call)
        assert _canon(body) == _canon(baseline)


def test_endpoint_blocked_route_sends_nothing(tmp_path, monkeypatch, gw):
    """선택 route가 `SUPPORTED`가 아니면 Gateway 전송 0건으로 종료한다."""
    entry = make_active_entry(purposes=("chat",))  # stream 목적 미충족
    _write_map(tmp_path, monkeypatch, [entry])
    seen = _run_stream_endpoint(monkeypatch, gw, {"prompt": "고정 입력", "model": MODEL_ID})
    assert seen["clients"] == []  # 전송 건수 0
    assert "data: [DONE]" in seen["sse"]
    assert "capability" in seen["sse"]


def _run_agent_endpoint(monkeypatch, base_gw, payload: dict, *, routed_model: str = "") -> dict:
    """`/api/agents/run-agent`을 실행하고 실제 전송 클라이언트·body를 캡처한다.

    `routed_model`을 주면 라우팅 단계가 그 ID를 반환한다(실제 경로의
    `_resolve_callable_model_id` 해석 결과가 seam에 들어오는 상황 재현).
    """
    import asyncio

    seen: dict = {"clients": [], "payloads": [], "calls": []}

    def _fake_stream_sse_realtime(self, model_id, messages, system_prompt="", tool_config=None):
        seen["clients"].append(self)
        seen["calls"].append((model_id, messages, system_prompt, tool_config))
        seen["payloads"].append(self._build_payload(model_id, messages, system_prompt, tool_config))

        async def _events():
            yield {"type": "content_block_delta", "delta": {"text": "ok"}}
            yield {"type": "message_stop", "stopReason": "end_turn"}

        return _events()

    monkeypatch.setattr(server, "_get_gw", lambda *_a, **_k: base_gw)
    monkeypatch.setattr(GatewayClient, "stream_sse_realtime", _fake_stream_sse_realtime)
    # 자동 라우팅은 이 seam의 대상이 아니므로 기본은 사용자 선택을 그대로 유지한다.
    monkeypatch.setattr(
        server, "_specialized_model_for_task", lambda _task, model, **_k: routed_model or model
    )

    async def _drive():
        response = await server.run_agent_with_tools(_FakeRequest(payload))
        return "".join([chunk async for chunk in response.body_iterator])

    seen["sse"] = asyncio.run(_drive())
    return seen


def test_agent_endpoint_uses_base_gw_when_unmanaged(tmp_path, monkeypatch, gw):
    """에이전트 경로도 Managed_Segment가 비면 기존 `gw` 동일 객체로 전송한다."""
    monkeypatch.setenv("AE_USERDATA_PATH", str(tmp_path))
    seen = _run_agent_endpoint(
        monkeypatch, gw, {"prompt": "고정 입력", "model": "anthropic.claude-x"}
    )
    assert seen["clients"] and all(client is gw for client in seen["clients"])
    assert "data: [DONE]" in seen["sse"]


def test_agent_endpoint_injects_effort_for_managed_model(tmp_path, monkeypatch, gw):
    """에이전트 경로에서도 tuple 일치 effort가 exact path에 1회 실린다."""
    entry = make_active_entry()
    _write_map(tmp_path, monkeypatch, [entry])
    seen = _run_agent_endpoint(
        monkeypatch,
        gw,
        {"prompt": "고정 입력", "model": MODEL_ID, "effort": _effort_body(entry)},
    )
    assert seen["clients"] and all(client is not gw for client in seen["clients"])
    for body in seen["payloads"]:
        assert rb.value_at_path(body, EFFORT_FIELD_PATH) == EFFORT_VALUES[0]
        assert rb.count_key_occurrences(body, EFFORT_FIELD_PATH[-1]) == 1


def test_agent_endpoint_blocked_route_sends_nothing(tmp_path, monkeypatch, gw):
    """에이전트 경로도 미지원 route면 Gateway 전송 0건으로 종료한다."""
    entry = make_active_entry(purposes=("chat",))
    _write_map(tmp_path, monkeypatch, [entry])
    seen = _run_agent_endpoint(monkeypatch, gw, {"prompt": "고정 입력", "model": MODEL_ID})
    assert seen["clients"] == []
    assert "capability" in seen["sse"]


# ─────────────────────────────────────────────────────────────────
# 조회 키 — Exact_Model_ID 우선 · 기록된 Invocation_Model_ID 폴백
#
# Capability_Map은 Exact_Model_ID(catalog 원문)로 키를 잡고 실제 전송 ID는
# `invocationModelIds`에 별도 기록한다(Requirement 2.17). 채팅 seam은 bare `model`을,
# 에이전트 seam은 `_resolve_callable_model_id`가 해석한 ID를 조회 키로 들고 오므로 두
# 자리가 같은 entry에 도달해야 한다. 폴백은 **정확 일치**만 하고 접두사를 만들지 않는다.
# ─────────────────────────────────────────────────────────────────
PREFIXED_MODEL_ID = "us." + MODEL_ID


def _with_invocation_ids(entry: dict, ids) -> dict:
    """entry에 관측된 Invocation_Model_ID를 기록한다(fingerprint 입력이 아니다)."""
    before = entry["capabilityFingerprint"]
    entry["invocationModelIds"] = list(ids)
    assert cm.recompute_fingerprint(entry) == before  # Requirement 3.12, 3.13
    assert cm.entry_malformed_reasons(entry) == []
    return entry


def test_lookup_uses_exact_model_id_first():
    """bare Exact_Model_ID 조회는 그 ID를 소유한 entry가 이긴다."""
    owner = {"modelId": MODEL_ID, "invocationModelIds": []}
    claimer = {"modelId": "other.model", "invocationModelIds": [MODEL_ID]}
    assert cm.find_entry_by_model_id([claimer, owner], MODEL_ID) is owner


def test_lookup_falls_back_to_recorded_invocation_model_id():
    """Exact_Model_ID 조회가 실패하면 기록된 전송 ID와의 정확 일치로 찾는다."""
    entry = {"modelId": MODEL_ID, "invocationModelIds": [PREFIXED_MODEL_ID]}
    assert cm.find_entry_by_model_id([entry], PREFIXED_MODEL_ID) is entry
    assert cm.invocation_model_ids_of(entry) == [PREFIXED_MODEL_ID]


def test_lookup_does_not_fall_back_when_ambiguous():
    """같은 전송 ID를 여러 entry가 주장하면 폴백하지 않는다(결정론)."""
    first = {"modelId": MODEL_ID, "invocationModelIds": [PREFIXED_MODEL_ID]}
    second = {"modelId": "other.model", "invocationModelIds": [PREFIXED_MODEL_ID]}
    assert cm.find_entry_by_model_id([first, second], PREFIXED_MODEL_ID) is None


def test_lookup_never_guesses_prefix_forms():
    """기록되지 않은 ID는 접두사를 붙이거나 떼서 추측하지 않는다(값 추론 금지)."""
    entry = {"modelId": MODEL_ID, "invocationModelIds": []}
    assert cm.find_entry_by_model_id([entry], PREFIXED_MODEL_ID) is None
    prefixed_only = {"modelId": PREFIXED_MODEL_ID, "invocationModelIds": []}
    assert cm.find_entry_by_model_id([prefixed_only], MODEL_ID) is None


def test_plan_resolves_entry_by_invocation_model_id(tmp_path, monkeypatch, gw):
    """에이전트 seam이 쓰는 `us.` 접두 ID로도 같은 entry에 도달한다(managed=True)."""
    entry = _with_invocation_ids(make_active_entry(), [PREFIXED_MODEL_ID])
    _write_map(tmp_path, monkeypatch, [entry])

    bare_plan = server.capability_plan_for(
        MODEL_ID,
        purpose=server.CAPABILITY_PURPOSE_STREAM,
        route=SSE_STREAM,
        invocation_model_id=PREFIXED_MODEL_ID,
    )
    prefixed_plan = server.capability_plan_for(
        PREFIXED_MODEL_ID,
        purpose=server.CAPABILITY_PURPOSE_STREAM,
        route=SSE_STREAM,
        invocation_model_id=PREFIXED_MODEL_ID,
    )
    for plan in (bare_plan, prefixed_plan):
        assert plan["managed"] is True
        assert plan["transmit"] is True
        assert plan["blocked"] is False
        assert plan["entry"]["modelId"] == MODEL_ID  # identity는 Exact_Model_ID다
        assert plan["binding"]["invocationModelId"] == PREFIXED_MODEL_ID

    client, binding = server.capability_client_for(
        gw, PREFIXED_MODEL_ID, purpose=server.CAPABILITY_PURPOSE_STREAM, plan=prefixed_plan
    )
    assert client is not None
    assert server.capability_gw_for(gw, client, binding, PREFIXED_MODEL_ID) is client


def test_plan_is_unmanaged_when_invocation_id_is_ambiguous(tmp_path, monkeypatch, gw):
    """같은 전송 ID를 두 entry가 주장하면 관리 대상으로 보지 않는다(기존 `gw` 유지)."""
    first = _with_invocation_ids(make_active_entry(), [PREFIXED_MODEL_ID])
    second = make_active_entry()
    second["candidateLabel"] = "candidate-seam-2"
    second["modelId"] = "other.managed.model"
    second["effort"][SSE_STREAM]["contract"]["modelId"] = second["modelId"]
    cm.recompute_fingerprint(second)
    _with_invocation_ids(second, [PREFIXED_MODEL_ID])
    assert ag.is_active(second, {})[0] is True  # 둘 다 Active_Model이어야 모호 조건이다
    _write_map(tmp_path, monkeypatch, [first, second])

    plan = server.capability_plan_for(
        PREFIXED_MODEL_ID, purpose=server.CAPABILITY_PURPOSE_STREAM, route=SSE_STREAM
    )
    assert plan["managed"] is False
    assert plan["blocked"] is False
    assert server.capability_client_for(gw, PREFIXED_MODEL_ID, plan=plan) == (None, None)


def test_agent_endpoint_resolves_prefixed_stream_model(tmp_path, monkeypatch, gw):
    """에이전트 seam이 `us.` 접두 ID를 들고 와도 capability client로 전송한다."""
    entry = _with_invocation_ids(make_active_entry(), [PREFIXED_MODEL_ID])
    _write_map(tmp_path, monkeypatch, [entry])
    # 실제 경로처럼 라우팅 단계에서 prefix가 해석된 ID가 seam에 들어온다.
    seen = _run_agent_endpoint(
        monkeypatch,
        gw,
        {"prompt": "고정 입력", "model": MODEL_ID, "effort": _effort_body(entry)},
        routed_model=PREFIXED_MODEL_ID,
    )
    assert seen["clients"] and all(client is not gw for client in seen["clients"])
    assert all(model_id == PREFIXED_MODEL_ID for model_id, *_ in seen["calls"])
    for body in seen["payloads"]:
        assert rb.value_at_path(body, EFFORT_FIELD_PATH) == EFFORT_VALUES[0]
        assert rb.count_key_occurrences(body, EFFORT_FIELD_PATH[-1]) == 1
