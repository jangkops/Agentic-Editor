"""Regression — 기존 GatewayClient 메서드 시그니처 불변 (요구사항 8.3, 8.4).

Feature: gateway-openai-models
대상: ai_engine.gateway_module.GatewayClient

순수 add 원칙: OpenAI 메서드 추가가 기존 converse/invoke/스트리밍 메서드의
시그니처를 변경하지 않음을 inspect.signature로 못박는다.

확장 (gateway-models-effort-support task 16.2, Requirement 12.4)
--------------------------------------------------------------
`ai_engine.capability.request_builder.EffortBoundClient`가 `GatewayClient`를
상속하므로, 이 스냅샷은 seam을 통과해도 그대로여야 한다. 아래 절에서
오버라이드 이름 집합이 **builder seam + credential 위임 + 생성자**의 닫힌 목록과
정확히 일치하는지, 그리고 서명·retry·prefix 교정·job polling·응답 변환이 상속
구현 그대로인지 검사한다. probe 전용 `MinimalRequestClient`도 같은 검사를 받는다.

실행: pytest scripts/test_gateway_signature_introspection.py -q
"""
from __future__ import annotations

import inspect
import os
import sys

import pytest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from ai_engine.capability import evidence_collector as ec  # noqa: E402
from ai_engine.capability import request_builder as rb  # noqa: E402
from ai_engine.gateway_module import GatewayClient  # noqa: E402

# {메서드명: 기대 파라미터 목록(순서 포함)}
_EXPECTED = {
    "converse": ["self", "model_id", "messages", "system_prompt", "tool_config"],
    "converse_quota_only": ["self", "model_id", "messages", "system_prompt"],
    "converse_stream_live": ["self", "model_id", "messages", "system_prompt", "tool_config"],
    "stream_sse_realtime": ["self", "model_id", "messages", "system_prompt", "tool_config"],
    "stream_converse": ["self", "model_id", "messages", "system_prompt"],
    "invoke_model": ["self", "model_id", "body", "timeout"],
}


@pytest.mark.parametrize("name,expected", list(_EXPECTED.items()))
def test_existing_method_signature_unchanged(name, expected):
    assert hasattr(GatewayClient, name), f"기존 메서드 {name} 사라짐"
    sig = inspect.signature(getattr(GatewayClient, name))
    assert list(sig.parameters.keys()) == expected, f"{name} 시그니처 변경됨"


def test_openai_methods_added():
    # 신규 OpenAI 메서드가 추가되었는지(순수 add 확인)
    for m in (
        "openai_responses_sync",
        "openai_responses_job_submit",
        "openai_responses_job_submit_and_poll",
        "_openai_poll_job",
        "_build_openai_payload",
    ):
        assert hasattr(GatewayClient, m), f"OpenAI 메서드 {m} 누락"


# ═════════════════════════════════════════════════════════════════
# EffortBoundClient 오버라이드 화이트리스트 (task 16.2, Requirement 12.4)
# ═════════════════════════════════════════════════════════════════

#: builder seam — effort 주입과 jobs model ID 부착이 일어나는 유일한 지점.
_ALLOWED_BUILDER_SEAM = frozenset({"_build_payload", "_build_openai_payload", "_apply_jobs_model_id"})

#: credential 위임 — base 인스턴스 하나로 5분 캐시·강제 갱신·runtime 주입을 단일화한다.
_ALLOWED_CREDENTIAL_DELEGATION = frozenset({"_get_creds", "force_refresh_creds", "inject_credentials"})

#: 허용 오버라이드의 **닫힌 목록** = builder seam ∪ credential 위임 ∪ 생성자.
_EFFORT_BOUND_WHITELIST = frozenset({"__init__"}) | _ALLOWED_BUILDER_SEAM | _ALLOWED_CREDENTIAL_DELEGATION

#: probe 전용 client가 오버라이드해도 되는 이름(builder seam 중 body 생성 2개만).
_PROBE_WHITELIST = frozenset({"_build_payload", "_build_openai_payload"})

#: 상속 구현이 그대로 쓰여야 하는 transport 메서드
#: (서명 · retry · prefix 교정 · job polling · 응답 변환 · 공개 호출 진입점).
_INHERITED_TRANSPORT = (
    "_sign",
    "_openai_post_with_retry",
    "_openai_request_blocking",
    "_looks_unsupported_model",
    "_is_expired_error",
    "_is_async_accepted",
    "_openai_poll_job",
    "_poll_job_result",
    "_poll_job_data",
    "_poll_invoke_job",
    "_cancel_job",
    "_extract_job_id",
    "_extract_job_status",
    "_extract_invoke_result",
    "_job_data_to_text",
    "_to_openai_input",
    "_converse_stream_live_once",
    "converse",
    "converse_quota_only",
    "converse_stream_live",
    "stream_sse_realtime",
    "stream_converse",
    "invoke_model",
    "openai_responses_sync",
    "openai_responses_call",
    "openai_responses_job_submit",
    "openai_responses_job_submit_and_poll",
    "close",
)


def _overrides(cls: type) -> frozenset[str]:
    """`cls`가 직속 부모의 이름을 실제로 덮어쓴 callable 이름 집합.

    모듈 헬퍼(:func:`rb.effort_bound_overrides`)를 쓰지 않고 독립 계산한다 —
    헬퍼가 틀리면 이 테스트도 함께 틀리는 상황을 만들지 않는다.
    """
    parent = cls.__mro__[1]
    return frozenset(
        name
        for name, value in vars(cls).items()
        if (callable(value) or isinstance(value, (property, staticmethod, classmethod)))
        and hasattr(parent, name)
    )


def _param_names(func) -> list:
    return list(inspect.signature(func).parameters.keys())


def test_effort_bound_client_overrides_match_whitelist():
    """builder seam · credential 위임 · 생성자 외의 오버라이드가 없다."""
    cls = rb.effort_bound_client_class()
    assert cls.__mro__[1] is GatewayClient, "EffortBoundClient가 GatewayClient를 직접 상속하지 않음"

    actual = _overrides(cls)
    unexpected = actual - _EFFORT_BOUND_WHITELIST
    assert not unexpected, f"허용되지 않은 오버라이드: {sorted(unexpected)}"
    assert actual == _EFFORT_BOUND_WHITELIST, (
        f"오버라이드 집합 변경됨 — 누락: {sorted(_EFFORT_BOUND_WHITELIST - actual)}, "
        f"추가: {sorted(actual - _EFFORT_BOUND_WHITELIST)}"
    )
    # 모듈이 선언한 닫힌 목록도 같은 집합이어야 한다(선언이 조용히 넓어지지 않게).
    assert frozenset(rb.EFFORT_BOUND_OVERRIDES) == _EFFORT_BOUND_WHITELIST
    assert frozenset(rb.BUILDER_SEAM_METHODS) == _ALLOWED_BUILDER_SEAM
    assert frozenset(rb.CREDENTIAL_DELEGATED_METHODS) == _ALLOWED_CREDENTIAL_DELEGATION
    # 모듈 헬퍼도 독립 계산과 일치한다.
    assert frozenset(rb.effort_bound_overrides()) == actual
    assert rb.unexpected_effort_bound_overrides() == ()


def test_effort_bound_client_public_overrides_are_credential_delegation_only():
    """공개 이름 오버라이드는 credential 위임뿐 — 공개 transport API는 손대지 않는다."""
    public = {name for name in _overrides(rb.effort_bound_client_class()) if not name.startswith("_")}
    assert public <= _ALLOWED_CREDENTIAL_DELEGATION, f"공개 메서드 오버라이드 발견: {sorted(public)}"


def test_effort_bound_client_inherits_transport_implementations():
    """서명·retry·prefix 교정·job polling·응답 변환은 상속 구현 그대로(동일 함수 객체)."""
    cls = rb.effort_bound_client_class()
    for name in _INHERITED_TRANSPORT:
        assert hasattr(GatewayClient, name), f"기존 메서드 {name} 사라짐"
        assert getattr(cls, name) is getattr(GatewayClient, name), f"{name}이 seam에서 재정의됨"


@pytest.mark.parametrize("name,expected", list(_EXPECTED.items()))
def test_effort_bound_client_preserves_public_signature(name, expected):
    """기존 public method 시그니처 스냅샷이 seam을 통과해도 동일하다."""
    cls = rb.effort_bound_client_class()
    assert list(inspect.signature(getattr(cls, name)).parameters.keys()) == expected


@pytest.mark.parametrize("name", sorted(_ALLOWED_CREDENTIAL_DELEGATION))
def test_credential_delegation_signature_matches_base(name):
    """credential 위임 메서드는 base와 파라미터 이름·순서가 같다(호출 계약 보존)."""
    cls = rb.effort_bound_client_class()
    assert _param_names(getattr(cls, name)) == _param_names(getattr(GatewayClient, name))


def test_probe_client_overrides_builder_seam_only():
    """probe 전용 MinimalRequestClient도 body builder 2개만 오버라이드한다."""
    probe = ec.probe_client_class()
    assert probe.__mro__[1] is rb.effort_bound_client_class(), "probe client가 EffortBoundClient를 상속하지 않음"

    actual = _overrides(probe)
    assert actual == _PROBE_WHITELIST, f"probe client 오버라이드 집합 변경됨: {sorted(actual)}"
    assert actual <= _EFFORT_BOUND_WHITELIST, "probe client가 화이트리스트 밖을 오버라이드"

    for name in _INHERITED_TRANSPORT:
        assert getattr(probe, name) is getattr(GatewayClient, name), f"probe client가 {name}을 재정의함"

    for name in sorted(_ALLOWED_CREDENTIAL_DELEGATION):
        assert getattr(probe, name) is getattr(rb.effort_bound_client_class(), name), (
            f"probe client가 credential 위임 {name}을 우회함"
        )


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-q"]))
