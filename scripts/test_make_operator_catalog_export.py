"""Operator_Catalog_Export 생성 도구 단위 테스트.

Feature: gateway-models-effort-support (작업 18.3 후속 — Operator_Catalog_Export 경로)
대상: scripts/make_operator_catalog_export.py

검증 범위
  - `--template` 출력이 주석 제거 후 유효한 JSON이고 실제 model ID·provider·effort 값을
    담지 않는다(자리표시자만).
  - 생성한 export가 `evidence_collector.validate_operator_catalog_export`의 4개 검증을
    모두 통과한다(Requirement 2.6~2.10): environment identity·region 일치, UTC 시각 순서,
    Catalog_Fingerprint 재계산 일치, sanitization manifest가 credential 관련 field 한정.
  - 라벨 매칭이 record field·최상위 매핑·`--label-map` 파일로만 성립하고 표시명으로는
    성립하지 않는다(Requirement 1.17).
  - credential 계열 field는 catalog 본문에서 제거되고 manifest에는 **이름만** 남는다
    (Requirement 2.9, 10.7~10.9).
  - 입력이 없으면 모델 record를 만들지 않고 빈 catalog로 만든다.
  - environment 3필드 미충족·루트 밖 `--out`·자체 검증 실패에서는 **파일을 만들지 않는다**
    (Requirement 10.15).

이 테스트는 Gateway를 호출하지 않는다. 통과 사실은 Gateway 지원 근거가 아니다
(Requirement 12.22) — 지원 주장은 production path probe만이 만든다.

실행: ai_engine/.venv/bin/python -m pytest scripts/test_make_operator_catalog_export.py -q
"""
from __future__ import annotations

import json
import os
import sys

import pytest

_HERE = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.dirname(_HERE)
for _path in (_ROOT, _HERE):
    if _path not in sys.path:
        sys.path.insert(0, _path)

import make_operator_catalog_export as tool  # noqa: E402

from ai_engine.capability import canonicalizer  # noqa: E402
from ai_engine.capability import evidence_collector as ec  # noqa: E402

# 무작위 심볼 — model ID·provider·route 지원·effort 값을 이 파일에 상수로 두지 않는다.
SYM_MODEL_A = "sym-model-9c14"
SYM_MODEL_B = "sym-model-2f80"
SYM_MODEL_C = "sym-model-4d22"
SYM_PROVIDER_A = "sym-provider-e7"
SYM_PROVIDER_B = "sym-provider-b3"
SYM_PROVIDER_C = "sym-provider-c9"
SYM_ENV_ID = "sym-env-9a"
SYM_EFFORT_FIELD = "sym_effort_field"
SYM_EFFORT_VALUES = ["sym-lo", "sym-hi"]
LABELS = list(ec.CANDIDATE_LABELS)


@pytest.fixture(autouse=True)
def _capability_loaded():
    """도구의 지연 import를 테스트 시작 시 결속한다."""
    loaded, error = tool.load_capability()
    assert loaded, error
    yield


@pytest.fixture()
def user_data(tmp_path, monkeypatch):
    """userData 루트를 임시 디렉터리로 고정하고 environment 지정을 채운다."""
    root = tmp_path / "userdata"
    root.mkdir()
    monkeypatch.setenv("AE_USERDATA_PATH", str(root))
    monkeypatch.delenv("AE_GENERATED_ROOT", raising=False)
    monkeypatch.setenv("AE_GATEWAY_ENV_ID", SYM_ENV_ID)
    return root


def _write(path, payload):
    path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    return str(path)


@pytest.fixture()
def operator_input(tmp_path):
    """운영자 입력 — record 직접 라벨 · 최상위 매핑 · 표시명만 있는 record를 함께 담는다."""
    snapshot = {
        "models": [
            {
                "modelId": SYM_MODEL_A,
                "provider": SYM_PROVIDER_A,
                "candidateLabel": LABELS[0],
                "routes": ["CONVERSE", "OPENAI_RESPONSES"],
                "effort": {
                    "OPENAI_RESPONSES": {
                        "fieldPath": [SYM_EFFORT_FIELD],
                        "valueType": "STRING",
                        "domainKind": "ENUM",
                        "enumValues": list(SYM_EFFORT_VALUES),
                    }
                },
                "authorization": "REDACT-ME-1",
                "apiToken": "REDACT-ME-2",
            },
            {"modelId": SYM_MODEL_B, "provider": SYM_PROVIDER_B, "routes": ["SSE_STREAM"]},
            {"modelId": SYM_MODEL_C, "provider": SYM_PROVIDER_C, "displayName": LABELS[1]},
        ],
        "candidateLabels": {LABELS[4]: SYM_MODEL_B},
    }
    return _write(tmp_path / "input.json", snapshot)


@pytest.fixture()
def label_map(tmp_path):
    """`--label-map` 파일 — 존재하는 modelId 하나와 존재하지 않는 modelId 하나."""
    return _write(tmp_path / "labels.json", {LABELS[5]: SYM_MODEL_C, LABELS[3]: "sym-absent"})


def _run(argv):
    return tool.main(argv)


def _export_path(root):
    return root / "capability" / "operator_catalog_export.json"


def _environment():
    """검증에 쓸 Same_Gateway_Environment identity(기존 GatewayClient 속성에서 읽는다).

    endpoint identity·region은 :class:`ai_engine.gateway_module.GatewayClient`가 가진 값이며
    이 파일에 URL·region을 상수로 두지 않는다(전송·자격증명 접근 없음).
    """
    identity, missing, _ = tool.resolve_environment()
    assert not missing, missing
    return identity


# ─────────────────────────────────────────────────────────────────
# `--template`
# ─────────────────────────────────────────────────────────────────
def test_template_is_valid_json_without_comments(capsys):
    """주석 줄을 지우면 유효한 JSON이고, 값 자리는 전부 자리표시자다."""
    assert _run(["--template"]) == tool.EXIT_OK
    text = capsys.readouterr().out
    data = json.loads(tool.template_json_text(text))

    assert list(data) == ["models", "candidateLabels"]
    record = data["models"][0]
    assert set(record) == {
        "modelId",
        "invocationModelId",  # 전송 ID 자리(선택) — control-plane 값을 복사해 넣는다
        "provider",
        "candidateLabel",
        "routes",
        "effort",
    }

    # 실제 model ID·provider·effort 값이 없다 — 문자열 값은 모두 <REPLACE...> 자리표시자다.
    def _strings(value):
        if isinstance(value, str):
            return [value]
        if isinstance(value, dict):
            return [item for child in value.values() for item in _strings(child)]
        if isinstance(value, list):
            return [item for child in value for item in _strings(child)]
        return []

    assert all("<REPLACE" in item for item in _strings(data))
    for key in data["models"][0]["effort"]:
        assert "<REPLACE" in key

    # effort 4요소(field path·value type·domain kind·complete domain)를 설명한다.
    declaration = list(record["effort"].values())[0]
    assert {"fieldPath", "valueType", "domainKind"} <= set(declaration)
    assert "enumValues" in declaration
    assert {"rangeLowerInclusive", "rangeUpperInclusive"} <= set(declaration)
    assert "표시명" in text  # 표시명 매칭 불가 사실을 템플릿에도 명시한다


# ─────────────────────────────────────────────────────────────────
# 생성 · 4개 검증 · 라벨 매칭
# ─────────────────────────────────────────────────────────────────
def test_dry_run_passes_validation_and_writes_nothing(user_data, operator_input, label_map, capsys):
    """`--dry-run`은 valid=True를 보고하고 파일을 만들지 않는다."""
    code = _run(["--input", operator_input, "--label-map", label_map, "--dry-run"])
    out = capsys.readouterr().out
    assert code == tool.EXIT_OK
    assert "valid=True" in out
    assert not _export_path(user_data).exists()


def test_export_passes_four_validations(user_data, operator_input, label_map):
    """기록한 export가 4개 검증을 모두 통과하고 fingerprint 재계산이 일치한다."""
    assert _run(["--input", operator_input, "--label-map", label_map]) == tool.EXIT_OK
    export = json.loads(_export_path(user_data).read_text(encoding="utf-8"))

    environment = _environment()
    verdict = ec.validate_operator_catalog_export(export, environment)
    assert verdict["valid"], verdict["reasons"]
    # environment는 추측값이 아니라 AE_GATEWAY_ENV_ID + GatewayClient 속성에서 온다.
    assert export["environment"] == environment
    assert export["environment"]["gatewayEnvironmentId"] == SYM_ENV_ID
    assert export["schemaVersion"] in ec.OPERATOR_EXPORT_SCHEMA_VERSIONS
    assert export["generatedAt"] <= export["collectedAt"]
    payload = export[tool.export_catalog_key()]
    assert export["catalogFingerprint"] == canonicalizer.catalog_fingerprint(payload)
    assert len(ec.catalog_records(payload)) == 3


def test_label_matching_sources(user_data, operator_input, label_map, capsys):
    """record field · 최상위 매핑 · `--label-map` 파일로만 라벨이 연결된다."""
    assert _run(["--input", operator_input, "--label-map", label_map, "--dry-run"]) == tool.EXIT_OK
    capsys.readouterr()

    export = tool.build_plan(
        tool.build_parser().parse_args(
            ["--input", operator_input, "--label-map", label_map, "--dry-run"]
        )
    )
    matched = {
        item["candidateLabel"]: item["modelId"] for item in export["labelMatches"] if item["matched"]
    }
    assert matched == {LABELS[0]: SYM_MODEL_A, LABELS[4]: SYM_MODEL_B, LABELS[5]: SYM_MODEL_C}

    unmatched = {
        item["candidateLabel"]: item["reason"]
        for item in export["labelMatches"]
        if not item["matched"]
    }
    # 표시명만 있는 record는 라벨과 연결되지 않는다(LABELS[1]은 displayName에만 있다).
    assert unmatched[LABELS[1]] == ec.LABEL_ASSOCIATION_ABSENT
    # 매핑이 가리킨 modelId가 catalog에 없으면 관계가 성립하지 않는다.
    assert unmatched[LABELS[3]] == ec.LABEL_ASSOCIATION_ABSENT


def test_credential_fields_removed_and_manifest_is_credential_only(user_data, operator_input):
    """credential 값은 catalog 본문에서 사라지고 manifest에는 이름만 남는다."""
    assert _run(["--input", operator_input]) == tool.EXIT_OK
    export = json.loads(_export_path(user_data).read_text(encoding="utf-8"))

    payload = export[tool.export_catalog_key()]
    assert "REDACT-ME-1" not in json.dumps(payload, ensure_ascii=False)
    assert tool.credential_residue(payload) == []

    removed, modified = ec.manifest_field_names(export["sanitizationManifest"])
    assert sorted(removed) == ["apiToken", "authorization"]
    assert modified == []
    for name in removed:
        assert tool.store.classify_key(name) == "DROP"
        assert not ec.is_catalog_significant_field(name)


def test_effort_declaration_summary_reports_unverified_effort(user_data, operator_input, capsys):
    """effort 선언이 없는 record는 effort가 UNVERIFIED로 남는다는 사실을 요약에 명시한다."""
    assert _run(["--input", operator_input, "--dry-run"]) == tool.EXIT_OK
    out = capsys.readouterr().out
    assert "effort 선언 없는 record: 2건" in out
    assert "UNVERIFIED" in out
    assert "4요소 완전: 1건" in out


def test_empty_input_creates_empty_catalog(user_data, capsys):
    """입력이 없으면 모델 record를 만들지 않고 빈 catalog로 만든다."""
    assert _run([]) == tool.EXIT_OK
    out = capsys.readouterr().out
    assert "빈 catalog" in out

    export = json.loads(_export_path(user_data).read_text(encoding="utf-8"))
    assert ec.catalog_records(export[tool.export_catalog_key()]) == []
    assert ec.validate_operator_catalog_export(export, _environment())["valid"]


# ─────────────────────────────────────────────────────────────────
# 거부 경로 — 어떤 경우에도 기록하지 않는다
# ─────────────────────────────────────────────────────────────────
def test_incomplete_environment_refuses_to_create(user_data, operator_input, monkeypatch, capsys):
    """environment 3필드가 채워지지 않으면 생성하지 않고 무엇이 없는지 알린다."""
    monkeypatch.delenv("AE_GATEWAY_ENV_ID", raising=False)
    code = _run(["--input", operator_input])
    captured = capsys.readouterr()
    assert code == tool.EXIT_ENVIRONMENT_INCOMPLETE
    assert tool.ENVIRONMENT_INCOMPLETE in captured.err
    assert "gatewayEnvironmentId" in captured.err
    assert "AE_GATEWAY_ENV_ID" in captured.err
    assert not _export_path(user_data).exists()


@pytest.mark.parametrize("out_arg", ["/tmp/ae-operator-export-outside.json", "../outside.json"])
def test_out_outside_user_data_root_rejected(user_data, operator_input, out_arg, capsys):
    """userData 루트 밖 `--out`은 거부하고 아무 파일도 만들지 않는다."""
    code = _run(["--input", operator_input, "--out", out_arg])
    captured = capsys.readouterr()
    assert code == tool.EXIT_WRITE_ERROR
    assert tool.OUT_PATH_OUTSIDE_ROOT in captured.err
    assert not _export_path(user_data).exists()
    assert not os.path.exists("/tmp/ae-operator-export-outside.json")
    assert not (user_data.parent / "outside.json").exists()


def test_validation_failure_is_not_written(user_data, operator_input, monkeypatch, capsys):
    """자체 검증이 실패하면 기록하지 않고 실패 이유 코드를 출력한다."""
    monkeypatch.setattr(
        tool.evidence_collector,
        "validate_operator_catalog_export",
        lambda export, environment: {
            "valid": False,
            "reasons": [ec.EXPORT_FINGERPRINT_MISMATCH],
            "snapshot": None,
            "catalogFingerprint": "",
            "environment": None,
            "generatedAt": "",
            "collectedAt": "",
        },
    )
    code = _run(["--input", operator_input])
    captured = capsys.readouterr()
    assert code == tool.EXIT_VALIDATION_FAILED
    assert ec.EXPORT_FINGERPRINT_MISMATCH in captured.err
    assert not _export_path(user_data).exists()


def test_missing_input_file_is_reported(user_data, tmp_path, capsys):
    """없는 입력 파일은 입력 오류로 보고하고 기록하지 않는다."""
    code = _run(["--input", str(tmp_path / "absent.json")])
    captured = capsys.readouterr()
    assert code == tool.EXIT_INPUT_ERROR
    assert tool.INPUT_NOT_FOUND in captured.err
    assert not _export_path(user_data).exists()


# ─────────────────────────────────────────────────────────────────
# 전송 ID(Invocation_Model_ID) 대조 — control-plane 응답만이 근거
#
# 이 절은 control-plane 조회를 대체(주입)해 판정 규칙만 검증한다. 실제 조회는
# `--verify-invocation-ids`가 수행하며, 이 파일은 어떤 AWS 호출도 하지 않는다.
# ─────────────────────────────────────────────────────────────────
SYM_PROFILE_A = "sym-scope-a." + SYM_MODEL_A
SYM_PROFILE_B = "sym-scope-b." + SYM_MODEL_A


def _fake_index(monkeypatch, index):
    monkeypatch.setattr(tool, "inference_profile_index", lambda env=None: (dict(index), []))


@pytest.fixture()
def declared_input(tmp_path):
    """전송 ID를 명시한 운영자 입력(단일 record)."""
    def _make(**overrides):
        record = {
            "modelId": SYM_MODEL_A,
            "provider": SYM_PROVIDER_A,
            "candidateLabel": LABELS[0],
            "routes": ["CONVERSE"],
        }
        record.update(overrides)
        return _write(tmp_path / "declared.json", {"models": [record]})

    return _make


def test_declared_invocation_id_confirmed_by_control_plane(
    user_data, declared_input, monkeypatch, capsys
):
    """명시한 전송 ID가 control-plane 응답에 있으면 그대로 기록한다."""
    _fake_index(monkeypatch, {SYM_MODEL_A: [SYM_PROFILE_A, SYM_PROFILE_B]})
    path = declared_input(invocationModelId=SYM_PROFILE_A)
    assert _run(["--input", path, "--verify-invocation-ids"]) == tool.EXIT_OK
    assert tool.INVOCATION_PROFILE_CONFIRMED in capsys.readouterr().out

    export = json.loads(_export_path(user_data).read_text(encoding="utf-8"))
    record = export["catalog"]["models"][0]
    assert record["invocationModelId"] == SYM_PROFILE_A
    assert record["modelId"] == SYM_MODEL_A  # Exact_Model_ID는 identity로 유지
    assert ec.record_invocation_model_id(record) == SYM_PROFILE_A


def test_declared_invocation_id_not_in_control_plane_is_refused(
    user_data, declared_input, monkeypatch, capsys
):
    """control-plane이 반환하지 않은 전송 ID면 파일을 만들지 않는다."""
    _fake_index(monkeypatch, {SYM_MODEL_A: [SYM_PROFILE_A]})
    path = declared_input(invocationModelId="sym-invented." + SYM_MODEL_A)
    code = _run(["--input", path, "--verify-invocation-ids"])
    assert code == tool.EXIT_INVOCATION_UNVERIFIED
    assert tool.INVOCATION_ID_UNVERIFIED in capsys.readouterr().err
    assert not _export_path(user_data).exists()


def test_unique_control_plane_profile_is_copied_into_record(
    user_data, declared_input, monkeypatch
):
    """후보가 정확히 하나면 control-plane 값을 그대로 채운다(생성이 아니라 복사)."""
    _fake_index(monkeypatch, {SYM_MODEL_A: [SYM_PROFILE_A]})
    assert _run(["--input", declared_input(), "--verify-invocation-ids"]) == tool.EXIT_OK
    export = json.loads(_export_path(user_data).read_text(encoding="utf-8"))
    assert export["catalog"]["models"][0]["invocationModelId"] == SYM_PROFILE_A


def test_ambiguous_control_plane_profiles_are_left_undetermined(
    user_data, declared_input, monkeypatch, capsys
):
    """후보가 여럿이면 자동 선택하지 않고 미확정으로 남긴다(값 추론 금지)."""
    _fake_index(monkeypatch, {SYM_MODEL_A: [SYM_PROFILE_A, SYM_PROFILE_B]})
    assert _run(["--input", declared_input(), "--verify-invocation-ids"]) == tool.EXIT_OK
    out = capsys.readouterr().out
    assert tool.INVOCATION_PROFILE_AMBIGUOUS in out
    export = json.loads(_export_path(user_data).read_text(encoding="utf-8"))
    assert "invocationModelId" not in export["catalog"]["models"][0]


def test_control_plane_unavailable_is_refused(user_data, declared_input, monkeypatch, capsys):
    """control-plane 조회가 실패하면 대조할 수 없으므로 파일을 만들지 않는다."""
    monkeypatch.setattr(tool, "inference_profile_index", lambda env=None: ({}, ["sym-error"]))
    code = _run(["--input", declared_input(), "--verify-invocation-ids"])
    assert code == tool.EXIT_INVOCATION_UNVERIFIED
    assert tool.CONTROL_PLANE_UNAVAILABLE in capsys.readouterr().err
    assert not _export_path(user_data).exists()


def test_without_flag_no_control_plane_call_and_record_is_verbatim(
    user_data, declared_input, monkeypatch
):
    """대조를 요청하지 않으면 control-plane을 호출하지 않고 record 값을 그대로 둔다."""
    def _boom(env=None):
        raise AssertionError("control-plane을 호출하면 안 된다")

    monkeypatch.setattr(tool, "inference_profile_index", _boom)
    declared = "sym-operator-only." + SYM_MODEL_A
    assert _run(["--input", declared_input(invocationModelId=declared)]) == tool.EXIT_OK
    export = json.loads(_export_path(user_data).read_text(encoding="utf-8"))
    assert export["catalog"]["models"][0]["invocationModelId"] == declared


def test_invocation_id_is_not_a_catalog_fingerprint_input(user_data, declared_input, monkeypatch):
    """전송 ID를 채워도 Catalog_Fingerprint는 바뀌지 않는다(Requirement 3.12, 3.13)."""
    without = tool.build_plan(
        tool.build_parser().parse_args(["--input", declared_input(), "--dry-run"])
    )
    _fake_index(monkeypatch, {SYM_MODEL_A: [SYM_PROFILE_A]})
    with_id = tool.build_plan(
        tool.build_parser().parse_args(
            ["--input", declared_input(), "--verify-invocation-ids", "--dry-run"]
        )
    )
    assert with_id["export"]["catalog"]["models"][0]["invocationModelId"] == SYM_PROFILE_A
    assert (
        canonicalizer.catalog_fingerprint(with_id["snapshot"])
        == canonicalizer.catalog_fingerprint(without["snapshot"])
    )
