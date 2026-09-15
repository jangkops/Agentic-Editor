"""Regression — `/api/models` 엔드포인트 무회귀와 Managed_Segment 폴백.

Feature: gateway-models-effort-support (task 16.4)
대상: ai_engine.server.list_models (`/api/models`), ai_engine.server._merge_managed_segment,
      ai_engine.capability.capability_map.merge_active_into_catalog / to_ui_payload

검증 범위 (_Requirements: 12.1, 12.2_)
  - `Managed_Segment`가 빈 상태에서 `/api/models` 응답이 기준선(= 이번 기능 도입 이전
    동작)과 **바이트 동일**하다: 모델 구성·provider 분류·item capability·5개 count 필드·
    응답 구조가 모두 같고 신규 최상위 `capabilities` 키는 부재한다.
  - Capability_Map 파일 부재, `UNVERIFIED` entry만 존재, 병합 예외 중 어떤 경우에도
    Baseline_Catalog_Segment만 반환한다(graceful 폴백, 원인 로그 ≤200자).
  - Existing_Model의 model ID·provider·verified route 선택 불변은 기존 회귀 자산이
    담당한다(중복 구현 금지):
      scripts/test_gateway_converse_signature_contract.py
      scripts/test_nonopenai_chat_preserved.py
      scripts/test_openai_model_id_passthrough.py

기존 자산과의 경계
  scripts/test_capability_managed_segment_merge.py는 `_merge_managed_segment` seam과
  UI payload 투영을 **함수 수준**으로 검증한다. 이 파일은 중복을 피하고 `/api/models`
  **엔드포인트 수준**(control-plane 분류 → 필터 → OpenAI 병합 → Managed_Segment 병합 →
  count 산출 → JSON 직렬화)의 무회귀만 덮는다.

Gateway 호출 없음
  Bedrock control-plane(`list_foundation_models`, `list_inference_profiles`)은 결정론적
  fake client로 대체하고 OpenAI 카탈로그 소스도 합성 항목을 반환하는 stub으로 대체한다.
  네트워크·자격증명 없이 통과하며, 통과 사실이 어떤 모델·route·effort의 Gateway 지원
  근거가 되지 않는다(Requirement 12.22).

실행: ai_engine/.venv/bin/python -m pytest scripts/test_capability_api_models_baseline.py -q
"""
from __future__ import annotations

import asyncio
import json
import os
import sys

import boto3
import pytest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from ai_engine import openai_catalog as oc  # noqa: E402
from ai_engine import server  # noqa: E402  (import 시 배너 출력 — 수집 단계 1회)
from ai_engine.capability import activation_gate as ag  # noqa: E402
from ai_engine.capability import capability_map as cm  # noqa: E402
from ai_engine.capability import contracts  # noqa: E402

#: 진단 문자열 절단 길이(server seam 관례).
REASON_MAX = 200

#: fake control-plane이 광고하는 CRIS(inference profile) 목록.
#: `meta.fixture-b-v1:0`만 profile이 존재해 INFERENCE_PROFILE 전용 모델로 살아남는다.
_INFERENCE_PROFILES = {
    "inferenceProfileSummaries": [
        {
            "inferenceProfileId": "us.meta.fixture-b-v1:0",
            "inferenceProfileName": "Fixture B (US)",
            "status": "ACTIVE",
        },
        {
            "inferenceProfileId": "us.retired.fixture-v1:0",
            "inferenceProfileName": "Retired",
            "status": "INACTIVE",  # ACTIVE 아님 → 후보에서 제외
        },
    ]
}


def _text_model(model_id, name, provider, inference_types, **extra):
    """control-plane text 모델 summary 하나를 만든다."""
    summary = {
        "modelId": model_id,
        "modelName": name,
        "providerName": provider,
        "inputModalities": ["TEXT"],
        "outputModalities": ["TEXT"],
        "inferenceTypesSupported": list(inference_types),
        "responseStreamingSupported": True,
    }
    summary.update(extra)
    return summary


def _media_model(model_id, name, provider, output_modalities):
    """image/video/embedding/rerank 계열 summary 하나를 만든다(전부 ON_DEMAND)."""
    return {
        "modelId": model_id,
        "modelName": name,
        "providerName": provider,
        "inputModalities": ["TEXT"],
        "outputModalities": list(output_modalities),
        "inferenceTypesSupported": ["ON_DEMAND"],
        "responseStreamingSupported": True,
    }


#: 기준선 분류·필터 경로를 모두 통과하는 결정론적 control-plane fixture.
#:   유지    : anthropic.claude-fixture-a-v1:0 (ON_DEMAND)
#:   유지    : meta.fixture-b-v1:0 (INFERENCE_PROFILE + CRIS profile 존재)
#:   제외    : mistral.fixture-c-v1:0 (INFERENCE_PROFILE인데 profile 없음)
#:   제외    : amazon.fixture-d-v1:0 (streaming 미지원)
#:   제외    : amazon.fixture-e-v1:0 (EOL)
#:   제외    : cohere.command-r-v1:0 (_UNINVOKABLE_MODEL_IDS → provider 그룹까지 사라짐)
#:   제외    : us.anthropic.claude-fixture-a-v1:0 (리전 prefix 중복)
#:   제외    : amazon.titan-image-generator-v2:0 (image 카테고리 denylist)
_FOUNDATION_MODELS = {
    "modelSummaries": [
        _text_model("anthropic.claude-fixture-a-v1:0", "Fixture Claude A", "Anthropic", ["ON_DEMAND"]),
        _text_model("us.anthropic.claude-fixture-a-v1:0", "Fixture Claude A (US)", "Anthropic", ["ON_DEMAND"]),
        _text_model("meta.fixture-b-v1:0", "Fixture Llama B", "Meta", ["INFERENCE_PROFILE"]),
        _text_model("mistral.fixture-c-v1:0", "Fixture Mistral C", "Mistral AI", ["INFERENCE_PROFILE"]),
        _text_model(
            "amazon.fixture-d-v1:0", "Fixture Nova D", "Amazon", ["ON_DEMAND"],
            responseStreamingSupported=False,
        ),
        _text_model(
            "amazon.fixture-e-v1:0", "Fixture Nova E", "Amazon", ["ON_DEMAND"],
            modelLifecycle={"status": "EOL"},
        ),
        _text_model("cohere.command-r-v1:0", "Command R", "Cohere", ["ON_DEMAND"]),
        _media_model("amazon.fixture-image-v1:0", "Fixture Image", "Amazon", ["IMAGE"]),
        _media_model("amazon.titan-image-generator-v2:0", "Titan Image v2", "Amazon", ["IMAGE"]),
        _media_model("amazon.fixture-video-v1:0", "Fixture Video", "Amazon", ["VIDEO"]),
        _media_model("cohere.fixture-embed-v3:0", "Fixture Embed", "Cohere", ["EMBEDDING"]),
        _media_model("amazon.fixture-rerank-v1:0", "Fixture Rerank", "Amazon", ["TEXT"]),
    ]
}

#: OpenAI 카탈로그 stub 항목(합성 id — Gateway 지원 근거가 아니다).
_OPENAI_ENTRIES = [
    {"id": "openai.fixture-x", "name": "Fixture X", "capabilities": {"chat": True}, "mode": "auto"},
    {"id": "openai.fixture-y", "name": "Fixture Y", "capabilities": {"chat": True}, "mode": "sync"},
]

#: 기준선 `/api/models` 응답 전체(구조·provider 분류·capability·count 포함).
EXPECTED_BASELINE_PAYLOAD = {
    "models": {
        "Anthropic": [{"id": "anthropic.claude-fixture-a-v1:0", "name": "Fixture Claude A"}],
        "Meta": [{"id": "meta.fixture-b-v1:0", "name": "Fixture Llama B"}],
        "OpenAI": [
            {
                "id": "openai.fixture-x",
                "name": "Fixture X",
                "capabilities": {"chat": True},
                "provider": "OpenAI",
                "mode": "auto",
            },
            {
                "id": "openai.fixture-y",
                "name": "Fixture Y",
                "capabilities": {"chat": True},
                "provider": "OpenAI",
                "mode": "sync",
            },
        ],
    },
    "image_models": {"Amazon": [{"id": "amazon.fixture-image-v1:0", "name": "Fixture Image"}]},
    "video_models": {"Amazon": [{"id": "amazon.fixture-video-v1:0", "name": "Fixture Video"}]},
    "embed_models": {"Cohere": [{"id": "cohere.fixture-embed-v3:0", "name": "Fixture Embed"}]},
    "rerank_models": {"Amazon": [{"id": "amazon.fixture-rerank-v1:0", "name": "Fixture Rerank"}]},
    "count": 8,
    "text_count": 4,
    "image_count": 1,
    "video_count": 1,
    "embed_count": 1,
    "rerank_count": 1,
}


# ─────────────────────────────────────────────────────────────────
# fake control-plane / stub 카탈로그 소스 / 요청 stub
# ─────────────────────────────────────────────────────────────────
class FakeBedrockClient:
    """`list_foundation_models`·`list_inference_profiles`만 제공하는 결정론적 client."""

    def __init__(self):
        self.calls = []
        self.session_kwargs = []  # `boto3.Session`에 전달된 인자(자격증명 부재 확인용)

    def list_inference_profiles(self):
        self.calls.append("list_inference_profiles")
        return json.loads(json.dumps(_INFERENCE_PROFILES))

    def list_foundation_models(self):
        self.calls.append("list_foundation_models")
        return json.loads(json.dumps(_FOUNDATION_MODELS))


class FakeSession:
    """`boto3.Session` 대체 — 실제 자격증명 해석·네트워크 없음."""

    def __init__(self, client, **kwargs):
        self._client = client
        client.session_kwargs.append(dict(kwargs))

    def client(self, service_name, **_kwargs):
        assert service_name == "bedrock", f"예상치 못한 서비스 호출: {service_name}"
        return self._client


class StubOpenAISource:
    """OpenAI 카탈로그 소스 stub(파일·게이트웨이 접근 없음)."""

    def list_models(self):
        return [dict(entry) for entry in _OPENAI_ENTRIES]


class StubRequest:
    """`list_models`가 GET 경로에서 사용하는 최소 Request 표면.

    `profile` query param을 주지 않아 기존 기본 경로(`AWS_PROFILE` 환경변수)를 탄다.
    """

    method = "GET"
    query_params: dict = {}


def _identity_merge(catalog):
    """이번 기능 도입 이전 동작(= Managed_Segment 병합 자체가 없는 상태)."""
    return catalog, None


def _call_api_models(fake_client):
    """`/api/models`를 호출하고 `(응답 바이트, 파싱 dict)`를 반환한다.

    호출마다 fake control-plane이 실제로 사용됐는지 확인해, 어떤 경로로도 실제 AWS
    control-plane에 접근하지 않았음을 각 테스트에서 보장한다.
    """
    before = len(fake_client.calls)
    response = asyncio.run(server.list_models(StubRequest()))
    assert response.status_code == 200
    assert fake_client.calls[before:] == ["list_inference_profiles", "list_foundation_models"]
    body = response.body
    return body, json.loads(body.decode("utf-8"))


@pytest.fixture()
def hermetic_api(tmp_path, monkeypatch):
    """control-plane·OpenAI 소스·userData를 전부 테스트 소유 자원으로 고정한다."""
    fake_client = FakeBedrockClient()

    def _no_direct_client(*_a, **_kw):  # 자격증명 직접 전달 경로도 차단
        raise AssertionError("실제 boto3.client 호출 시도 — 테스트는 네트워크를 쓰지 않는다")

    monkeypatch.setattr(boto3, "Session", lambda **kwargs: FakeSession(fake_client, **kwargs))
    monkeypatch.setattr(boto3, "client", _no_direct_client)
    monkeypatch.setattr(oc, "get_catalog_source", lambda *_a, **_kw: StubOpenAISource())

    # 영속 경로는 tmp_path 하위만 — 실제 userData를 오염시키지 않는다(Requirement 10.15).
    monkeypatch.setenv("AE_USERDATA_PATH", str(tmp_path))
    monkeypatch.delenv("AE_GENERATED_ROOT", raising=False)
    monkeypatch.setenv("AWS_REGION", "us-west-2")
    monkeypatch.setenv("AWS_PROFILE", "fixture-profile")

    # `_update_gateway_model_cache`가 만지는 프로세스 전역 캐시를 복원한다.
    original_cache = dict(server._GATEWAY_MODEL_CACHE)
    yield {"client": fake_client, "user_data": tmp_path}
    server._GATEWAY_MODEL_CACHE.clear()
    server._GATEWAY_MODEL_CACHE.update(original_cache)


@pytest.fixture()
def baseline_bytes(hermetic_api):
    """Managed_Segment 병합 seam이 없던 시점의 응답 바이트(기준선).

    seam 교체는 독립 `MonkeyPatch` context로 되돌려 `hermetic_api`의 격리 패치가
    함께 풀리지 않게 한다(공유 monkeypatch를 `undo`하면 실제 AWS 경로가 열린다).
    """
    with pytest.MonkeyPatch.context() as mp:
        mp.setattr(server, "_merge_managed_segment", _identity_merge)
        body, payload = _call_api_models(hermetic_api["client"])
        assert payload == EXPECTED_BASELINE_PAYLOAD  # 기준선 자체가 흔들리지 않았는지 확인
    return body


# ─────────────────────────────────────────────────────────────────
# 기준선 구조 — 모델 구성·provider 분류·capability·count·구조
# ─────────────────────────────────────────────────────────────────
def test_api_models_payload_matches_baseline_structure(hermetic_api):
    """Managed_Segment가 비면 응답 전체가 기준선 구조와 정확히 같다."""
    _body, payload = _call_api_models(hermetic_api["client"])

    assert payload == EXPECTED_BASELINE_PAYLOAD
    assert list(payload) == list(EXPECTED_BASELINE_PAYLOAD)  # 키 순서까지 동일
    assert "capabilities" not in payload  # 신규 최상위 키 부재

    # provider 분류: 필터로 비워진 그룹은 제거되고 카테고리 간 혼입도 없다.
    assert "Cohere" not in payload["models"]  # denylist로 텍스트 그룹 소멸
    assert "Mistral AI" not in payload["models"]  # CRIS profile 없음 → 미노출
    assert "Amazon" not in payload["models"]  # streaming 미지원·EOL만 있었다
    assert "amazon.titan-image-generator-v2:0" not in [
        item["id"] for item in payload["image_models"]["Amazon"]
    ]

    # item capability: Bedrock 항목은 `{id,name}`, OpenAI 항목만 capability를 싣는다.
    for provider, items in payload["models"].items():
        for item in items:
            if provider == "OpenAI":
                assert item["capabilities"]["chat"] is True
                assert item["provider"] == "OpenAI"
            else:
                assert set(item) == {"id", "name"}

    # count: 5개 카테고리 합계와 각 카테고리 크기가 일치한다.
    assert payload["count"] == (
        payload["text_count"]
        + payload["image_count"]
        + payload["video_count"]
        + payload["embed_count"]
        + payload["rerank_count"]
    )
    assert payload["text_count"] == sum(len(v) for v in payload["models"].values())

    # control-plane은 fake만 호출됐고, session에는 profile name만 전달된다(자격증명 부재).
    client = hermetic_api["client"]
    assert client.calls == ["list_inference_profiles", "list_foundation_models"]
    assert client.session_kwargs == [{"profile_name": "fixture-profile", "region_name": "us-west-2"}]


# ─────────────────────────────────────────────────────────────────
# 바이트 동일성 — Requirement 12.1, 12.2
# ─────────────────────────────────────────────────────────────────
def test_response_bytes_identical_to_pre_feature_baseline(hermetic_api, baseline_bytes, capsys):
    """Capability_Map 파일 부재 시 응답 바이트가 기준선과 동일하다."""
    capsys.readouterr()  # 기준선 호출 로그 비우기
    body, payload = _call_api_models(hermetic_api["client"])

    assert body == baseline_bytes
    assert "capabilities" not in payload
    assert "[Capability]" not in capsys.readouterr().out  # 정상 경로는 진단 로그 없음


def test_unverified_only_map_keeps_baseline_bytes(hermetic_api, baseline_bytes):
    """`UNVERIFIED` entry만 있는 map은 Managed_Segment를 비워 기준선을 유지한다."""
    user_data = hermetic_api["user_data"]
    cm.save(cm.new_map(entries=[contracts.new_entry("candidate-omega")]), str(user_data))

    body, payload = _call_api_models(hermetic_api["client"])
    assert body == baseline_bytes
    assert "capabilities" not in payload


# ─────────────────────────────────────────────────────────────────
# 병합 예외 → Baseline_Catalog_Segment 폴백
# ─────────────────────────────────────────────────────────────────
def test_activation_exception_falls_back_to_baseline_segment(
    hermetic_api, baseline_bytes, monkeypatch, capsys
):
    """Activation_Gate 평가가 실패해도 응답은 기준선 바이트를 유지한다."""

    def _boom(*_args, **_kwargs):
        raise RuntimeError("activation 실패 " + "y" * 400)

    monkeypatch.setattr(ag, "active_models", _boom)
    capsys.readouterr()

    body, payload = _call_api_models(hermetic_api["client"])
    assert body == baseline_bytes
    assert "capabilities" not in payload

    lines = [ln for ln in capsys.readouterr().out.splitlines() if "[Capability]" in ln]
    assert len(lines) == 1
    reason = lines[0].split("Managed_Segment 병합 생략: ", 1)[1]
    assert len(reason) <= REASON_MAX


def test_merge_helper_exception_falls_back_to_baseline_segment(
    hermetic_api, baseline_bytes, monkeypatch, capsys
):
    """Managed_Segment가 비지 않아도 병합 실패 시 부분 병합 결과를 흘리지 않는다."""
    monkeypatch.setattr(ag, "active_models", lambda *_a, **_kw: [
        {"modelId": "managed.fixture.zeta", "provider": "ManagedProvider"}
    ])

    def _boom(*_args, **_kwargs):
        raise ValueError("병합 실패")

    monkeypatch.setattr(cm, "merge_active_into_catalog", _boom)
    capsys.readouterr()

    body, payload = _call_api_models(hermetic_api["client"])
    assert body == baseline_bytes
    assert "capabilities" not in payload
    assert "managed.fixture.zeta" not in body.decode("utf-8")
    assert any("[Capability]" in ln for ln in capsys.readouterr().out.splitlines())


# ─────────────────────────────────────────────────────────────────
# 무회귀 단정의 의미성 — Managed_Segment가 있으면 응답은 달라져야 한다
# ─────────────────────────────────────────────────────────────────
def test_non_empty_managed_segment_changes_response(hermetic_api, baseline_bytes, monkeypatch):
    """Active_Model이 있으면 카탈로그·count·`capabilities`가 변한다(위 단정이 공허하지 않음)."""
    monkeypatch.setattr(ag, "active_models", lambda *_a, **_kw: [
        {"modelId": "managed.fixture.zeta", "provider": "ManagedProvider"}
    ])

    body, payload = _call_api_models(hermetic_api["client"])
    assert body != baseline_bytes
    assert payload["models"]["ManagedProvider"] == [
        {"id": "managed.fixture.zeta", "name": "managed.fixture.zeta"}
    ]
    assert payload["text_count"] == EXPECTED_BASELINE_PAYLOAD["text_count"] + 1
    assert payload["count"] == EXPECTED_BASELINE_PAYLOAD["count"] + 1
    assert payload["capabilities"]["modelIds"] == ["managed.fixture.zeta"]
    # Baseline_Catalog_Segment는 그대로 보존된다.
    for key in ("image_models", "video_models", "embed_models", "rerank_models"):
        assert payload[key] == EXPECTED_BASELINE_PAYLOAD[key]
    assert payload["models"]["Anthropic"] == EXPECTED_BASELINE_PAYLOAD["models"]["Anthropic"]


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-q"]))
