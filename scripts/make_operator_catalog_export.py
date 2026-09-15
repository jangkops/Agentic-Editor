#!/usr/bin/env python3
"""Operator_Catalog_Export 생성 CLI — 운영자 입력만으로 4개 검증을 통과하는 export 작성.

작업 18.3에서 Gateway catalog 조회 경로가 미구현 스텁임이 확인됐다
(`ai_engine/openai_catalog.py::GatewayListSource.list_models` → `return []`). 그래서
Candidate_Label과 exact model record의 관계를 입증할 수 있는 유일한 대체 근거는
**Operator_Catalog_Export**다(Requirement 2.5). 이 스크립트는 운영자가 자신의
Same_Gateway_Environment에서 직접 확인한 catalog 정보를 받아
:func:`evidence_collector.validate_operator_catalog_export`의 **4개 검증을 모두 통과하는**
export를 `userData/capability/` 하위에 원자적으로 만든다.

4개 검증(Requirement 2.6~2.10)을 이 스크립트가 보장하는 방법:

1. **environment identity·region 정확 일치** — export의 `environment`를 추측하지 않고
   :func:`evidence_collector.environment_identity`로 읽는다(`AE_GATEWAY_ENV_ID` +
   기존 :class:`ai_engine.gateway_module.GatewayClient`의 `gateway_url`·`region`).
   3필드 중 하나라도 비면 **파일을 만들지 않고** 무엇이 없는지 보고한다(신규 URL 하드코딩 없음).
2. **UTC 시각 형식과 순서** — `generatedAt`·`collectedAt`을 `contracts.utc_now_iso`로만
   채우고 `generatedAt <= collectedAt`을 보장한다.
3. **Catalog_Fingerprint 재계산 일치** — 저장할 catalog payload 그 자체에
   :func:`canonicalizer.catalog_fingerprint`를 적용해 `catalogFingerprint`에 넣는다.
   저장은 `sanitize=False`로 한다 — store의 저장 직전 정제는 prompt/body 계열 **키 이름**
   (`input`·`instructions`·`payload` 등)을 재작성하므로 catalog 본문에 적용하면 fingerprint
   재계산이 깨진다. 대신 이 스크립트가 credential 키를 먼저 제거하고, 제거 후 남은
   credential 키가 0개임을 다시 확인한 뒤에만 기록한다.
4. **sanitization manifest는 credential 관련 field만** — 입력에서 실제로 제거한 field 중
   :func:`store.classify_key`가 ``DROP``으로 분류한 이름만 manifest에 담는다. 그 이름이
   catalog identity·provider·advertised route·capability field이면
   (:func:`evidence_collector.is_catalog_significant_field`) 기록을 거부한다 — 그런 이름을
   지우면 catalog 본문이 변경되어 export가 근거에서 제외되기 때문이다(Requirement 2.10).

값 추론 금지(핵심 불변식):
  - model ID·provider·advertised route·effort field path·effort 허용값을 이 파일에 상수로
    두지 않는다. 전부 `--input`·`--label-map`(또는 snapshot 최상위 `candidateLabels`)에서만
    온다. Candidate_Label 문자열에서 어떤 identity도 유도하지 않는다.
  - 기본값 모델 record를 만들지 않는다. 입력이 없으면 **빈 catalog**로 만들고 그 사실을
    요약에 명시한다.
  - 라벨 매칭은 :func:`evidence_collector.find_label_records`가 인정하는 형태만 성립한다 —
    record의 `candidateLabel`/`label`/`alias` 계열 field 또는 snapshot 최상위
    `{label: modelId}` 매핑. **표시명(`name`·`modelName`·`displayName`) 기반 매칭은 성립하지
    않는다**(Requirement 1.17).
  - effort 선언이 없는 record는 effort가 `UNVERIFIED`로 남는다. 이 스크립트는 effort 값을
    만들어 채우지 않는다(Requirement 5.6, 5.18, 5.19).

전송 ID(Invocation_Model_ID) — `--verify-invocation-ids`:
  Exact_Model_ID는 catalog identity이지만 production 요청 경로는 inference profile 전용
  모델을 profile ID로 호출한다(`server._resolve_callable_model_id`). record가 그 전송 ID를
  `invocationModelId`로 실을 수 있고, `--verify-invocation-ids`를 주면 이 도구가 Bedrock
  control-plane `list_inference_profiles` **읽기 전용 조회**로 값을 대조한다. 응답에 없는 값이
  있으면 파일을 만들지 않는다(종료 코드 8). 값이 비어 있고 후보 profile이 정확히 하나면 그
  문자열을 그대로 채우고, 후보가 여럿이면 자동 선택하지 않고 비워 둔다(값 추론 금지).
  이 자리는 Catalog_Fingerprint 입력이 아니고(모델 view 화이트리스트 밖) Capability_Fingerprint
  입력도 아니다(Requirement 3.12, 3.13) — 기존 fingerprint를 흔들지 않는다.

보안(steering · Requirement 10):
  - 자격증명을 읽거나 저장하지 않고 Gateway로 아무 것도 전송하지 않는다. GatewayClient는
    endpoint identity·region을 **읽기 위해서만** 생성한다(`_get_creds`는 전송 시점에만 호출된다).
    `--verify-invocation-ids`는 Bedrock control-plane 조회 1건을 수행하지만(모델 호출 아님,
    Gateway 전송 0건) 자격증명은 환경의 AWS profile에서 런타임으로만 쓰고 저장하지 않는다.
  - 입력에서 발견한 credential 계열 field는 값이 아니라 **이름·경로만** 보고한다.
  - 기록은 :class:`store.CapabilityStore`를 통해 `userData` 하위로만 하고, 루트 밖 `--out`은
    거부한다(Requirement 10.15). 쓰기는 임시 파일 + `os.replace`로 원자적이다.

재사용: interpreter guard·경로 해석·GatewayClient 구성·store 구성은
`scripts/validate_gateway_model_capabilities.py`의 구현을 그대로 호출한다(판정 규칙을 이
파일에 복제하지 않는다). export 검증·catalog 읽기·라벨 매칭·effort 계약 판정은
`ai_engine/capability/`의 `evidence_collector`·`contracts`·`canonicalizer`·`store`가 한다.

실행(모든 Python 실행은 이 interpreter만 사용한다)::

    ai_engine/.venv/bin/python scripts/make_operator_catalog_export.py --template > /tmp/in.json
    ai_engine/.venv/bin/python scripts/make_operator_catalog_export.py --input /tmp/in.json --dry-run
    AE_GATEWAY_ENV_ID=... ai_engine/.venv/bin/python scripts/make_operator_catalog_export.py \\
        --input /tmp/in.json --label-map /tmp/labels.json

종료 코드: 0 성공, 1 interpreter 환경 오류, 2 인자 오류(argparse), 3 입력 읽기 오류,
4 environment 미충족(생성 거부), 5 export 자체 검증 실패(미기록), 6 기록 실패(루트 밖 경로 포함),
7 sanitization 충돌(credential 이름이 catalog 본문 field와 겹침 — 미기록),
8 전송 ID 대조 실패(control-plane 응답에 없는 invocationModelId 또는 조회 불가 — 미기록).

참조: .kiro/specs/gateway-models-effort-support/design.md "Components and Interfaces" 2절
Requirements: 2.5, 2.6, 2.7, 2.8, 2.9, 2.10, 2.11, 5.6, 10.7~10.10, 10.15, 11.1~11.3
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from typing import Any, Iterable, Sequence

# repo 루트와 scripts/를 import 경로에 추가한다(scripts/ 하위 실행 관행 재사용).
_HERE = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.dirname(_HERE)
for _path in (_ROOT, _HERE):
    if _path not in sys.path:
        sys.path.insert(0, _path)

# interpreter guard·경로 해석·GatewayClient·store 구성을 재사용한다. 이 모듈은 import
# 시점에 capability 패키지를 불러오지 않으므로(지연 import) guard 판정을 앞지르지 않는다.
import validate_gateway_model_capabilities as runner  # noqa: E402

# ─────────────────────────────────────────────────────────────────
# capability 모듈 지연 결속 (interpreter guard 이후)
# ─────────────────────────────────────────────────────────────────
canonicalizer: Any = None
contracts: Any = None
evidence_collector: Any = None
store: Any = None


def load_capability() -> tuple[bool, str | None]:
    """`ai_engine.capability` 모듈을 결속한다(import 목록은 runner 정의를 재사용).

    Returns:
        ``(loaded, error)``. 실패해도 예외를 올리지 않는다 — 호출자는 interpreter 환경
        오류만 보고하고 어떤 파일도 만들지 않는다(Requirement 11.2, 11.3).
    """
    global canonicalizer, contracts, evidence_collector, store
    if contracts is not None:
        return True, None
    loaded, error = runner.load_capability()
    if not loaded:
        return False, error
    canonicalizer = runner.canonicalizer
    contracts = runner.contracts
    evidence_collector = runner.evidence_collector
    store = runner.store
    return True, None


# ─────────────────────────────────────────────────────────────────
# 상수
# ─────────────────────────────────────────────────────────────────

#: 기본 기록 경로(userData 루트 기준 상대 경로).
DEFAULT_OUT_PATH = "capability/operator_catalog_export.json"

#: runner가 export 경로를 읽는 환경변수 이름(정의 위치 재사용).
ENV_OPERATOR_EXPORT = runner.ENV_OPERATOR_EXPORT

#: snapshot에서 모델 record 목록을 담는 키(`evidence_collector._MODEL_LIST_KEYS`에 포함).
SNAPSHOT_MODELS_KEY = "models"

#: snapshot 최상위 `{label: modelId}` 매핑 키
#: (:data:`evidence_collector.LABEL_MAP_KEYS`의 `candidatelabels`와 정규화 일치).
SNAPSHOT_LABEL_MAP_KEY = "candidateLabels"

#: sanitization manifest의 제거·변경 field 목록 키
#: (:data:`evidence_collector._MANIFEST_REMOVED_KEYS`/`_MANIFEST_MODIFIED_KEYS`와 정규화 일치).
MANIFEST_REMOVED_KEY = "removedFields"
MANIFEST_MODIFIED_KEY = "modifiedFields"

#: 입력 JSON의 최대 중첩 깊이(초과분은 입력 오류로 거부한다).
MAX_INPUT_DEPTH = 32

#: 진단 문자열 최대 길이(프로젝트 로깅 관례와 동일하게 200자 절단).
_REASON_MAX = 200

#: 템플릿 주석 접두사(전부 줄 단위 주석이므로 이 접두사 줄만 지우면 유효한 JSON이다).
TEMPLATE_COMMENT_PREFIX = "//"

# ── 이유 코드(닫힌 집합) ──────────────────────────────────────────────────
REASON_OK = "OK"
INPUT_UNREADABLE = "INPUT_UNREADABLE"
INPUT_NOT_FOUND = "INPUT_NOT_FOUND"
INPUT_TOO_DEEP = "INPUT_TOO_DEEP"
INPUT_SHAPE_INVALID = "INPUT_SHAPE_INVALID"
LABEL_MAP_SHAPE_INVALID = "LABEL_MAP_SHAPE_INVALID"
ENVIRONMENT_INCOMPLETE = "ENVIRONMENT_INCOMPLETE"
STORE_UNAVAILABLE = "STORE_UNAVAILABLE"
OUT_PATH_OUTSIDE_ROOT = "OUT_PATH_OUTSIDE_ROOT"
SANITIZATION_CONFLICT = "SANITIZATION_CONFLICT"
CREDENTIAL_RESIDUE = "CREDENTIAL_RESIDUE"
EXPORT_VALIDATION_FAILED = "EXPORT_VALIDATION_FAILED"
WRITE_FAILED = "WRITE_FAILED"
INVOCATION_ID_UNVERIFIED = "INVOCATION_ID_UNVERIFIED"
CONTROL_PLANE_UNAVAILABLE = "CONTROL_PLANE_UNAVAILABLE"

#: 비차단 note 코드.
NOTE_EMPTY_CATALOG = "EMPTY_CATALOG"
NOTE_NO_LABEL_ASSOCIATION = "NO_LABEL_ASSOCIATION"
NOTE_EFFORT_DECLARATION_ABSENT = "EFFORT_DECLARATION_ABSENT"
NOTE_EFFORT_ROUTE_NOT_INJECTABLE = "EFFORT_ROUTE_NOT_INJECTABLE"
NOTE_CREDENTIAL_FIELDS_REMOVED = "CREDENTIAL_FIELDS_REMOVED"
NOTE_INVOCATION_ID_ABSENT = "INVOCATION_ID_ABSENT"
NOTE_INVOCATION_ID_RESOLVED = "INVOCATION_ID_RESOLVED"
NOTE_INVOCATION_ID_AMBIGUOUS = "INVOCATION_ID_AMBIGUOUS"

#: Invocation_Model_ID 대조 판정(닫힌 집합 — control-plane 응답만이 근거다).
INVOCATION_MATCHES_EXACT = "MATCHES_EXACT_MODEL_ID"
INVOCATION_PROFILE_CONFIRMED = "PROFILE_CONFIRMED"
INVOCATION_PROFILE_RESOLVED = "PROFILE_RESOLVED"
INVOCATION_PROFILE_AMBIGUOUS = "PROFILE_AMBIGUOUS"
INVOCATION_PROFILE_ABSENT = "PROFILE_ABSENT"
INVOCATION_NOT_A_PROFILE = "NOT_A_CONTROL_PLANE_PROFILE"
INVOCATION_NOT_CHECKED = "NOT_CHECKED"

#: control-plane inference profile 조회 상한(페이지 수 — 무한 루프 방지).
_PROFILE_PAGE_LIMIT = 20

#: control-plane ARN에서 model ID가 시작되는 구분자(ARN 파싱 — 접두사 생성이 아니다).
_FOUNDATION_MODEL_MARKER = "foundation-model/"

#: 활성 inference profile 상태 값(control-plane이 반환하는 문자 그대로 비교).
_PROFILE_ACTIVE_STATUS = "ACTIVE"

#: environment 3필드를 채우는 출처(운영자 안내용 — 신규 URL을 만들지 않는다).
ENVIRONMENT_SOURCES: dict[str, str] = {
    "gatewayEnvironmentId": "환경변수 AE_GATEWAY_ENV_ID (운영자 지정 값)",
    "endpointIdentity": "GatewayClient.gateway_url (기존 기본값 또는 GATEWAY_URL 환경변수)",
    "region": "GatewayClient.region (AWS_REGION 환경변수)",
}

#: 종료 코드.
EXIT_OK = 0
EXIT_INTERPRETER_ERROR = 1
EXIT_INPUT_ERROR = 3
EXIT_ENVIRONMENT_INCOMPLETE = 4
EXIT_VALIDATION_FAILED = 5
EXIT_WRITE_ERROR = 6
EXIT_SANITIZATION_CONFLICT = 7
EXIT_INVOCATION_UNVERIFIED = 8

_MISSING: Any = object()


# ─────────────────────────────────────────────────────────────────
# 예외
# ─────────────────────────────────────────────────────────────────
class ToolError(Exception):
    """이 도구의 중단 조건(이유 코드 + 종료 코드).

    어떤 ToolError도 파일을 만들지 않는다 — 기록은 모든 검증을 통과한 뒤 한 번만 한다.
    """

    def __init__(self, reason: str, message: str, *, exit_code: int, detail: Any = None) -> None:
        super().__init__(message)
        self.reason = reason
        self.message = message
        self.exit_code = exit_code
        self.detail = detail


# ─────────────────────────────────────────────────────────────────
# 작은 유틸
# ─────────────────────────────────────────────────────────────────
def _truncate(value: Any) -> str:
    text = value if isinstance(value, str) else str(value)
    return text[:_REASON_MAX]


def _text(value: Any) -> str:
    return value if isinstance(value, str) else ""


def _is_dict(value: Any) -> bool:
    return isinstance(value, dict)


# ─────────────────────────────────────────────────────────────────
# 입력 읽기 (userData 상대 경로 또는 절대 경로)
# ─────────────────────────────────────────────────────────────────
def read_json_input(path: str, store_obj: Any, *, kind: str) -> tuple[Any, str]:
    """입력 JSON을 읽는다(절대 경로는 그대로, 상대 경로는 userData 루트 기준).

    읽기 전용 경로이므로 절대 경로는 userData 루트 밖도 허용한다(예: `/tmp` 임시 입력).
    상대 경로는 :class:`store.CapabilityStore`가 루트 기준으로 해석하며 ``..`` 이스케이프는
    거부된다.

    Returns:
        ``(data, resolvedPath)``.

    Raises:
        ToolError: 파일 부재·JSON 손상·I/O 오류·루트 밖 상대 경로.
    """
    raw = os.path.expanduser(str(path))
    if os.path.isabs(raw):
        target = os.path.abspath(raw)
        if not os.path.isfile(target):
            raise ToolError(
                INPUT_NOT_FOUND, f"{kind} 파일이 없다: {target}", exit_code=EXIT_INPUT_ERROR
            )
        try:
            with open(target, "r", encoding="utf-8") as handle:
                return json.load(handle), target
        except (OSError, ValueError) as exc:
            raise ToolError(
                INPUT_UNREADABLE,
                f"{kind} 읽기 실패: {target} — {_truncate(exc)}",
                exit_code=EXIT_INPUT_ERROR,
            ) from exc

    if store_obj is None:
        raise ToolError(
            STORE_UNAVAILABLE,
            f"{kind} 상대 경로를 해석할 userData 루트를 만들 수 없다: {raw}",
            exit_code=EXIT_INPUT_ERROR,
        )
    try:
        target_path = store_obj.resolve(raw)
        data = store_obj.read_json(target_path, default=_MISSING)
    except store.StoreError as exc:
        raise ToolError(
            INPUT_UNREADABLE,
            f"{kind} 읽기 실패(userData 루트 기준 {raw}): {_truncate(exc)}",
            exit_code=EXIT_INPUT_ERROR,
        ) from exc
    if data is _MISSING:
        raise ToolError(
            INPUT_NOT_FOUND,
            f"{kind} 파일이 없다: {target_path}",
            exit_code=EXIT_INPUT_ERROR,
        )
    return data, str(target_path)


def input_records(data: Any) -> list[dict]:
    """입력에서 모델 record 목록을 읽는다(:func:`evidence_collector.catalog_records` 위임).

    record 목록·``{"models": [...]}``·``{modelId: record}`` 매핑·단일 record를 모두
    지원한다. 값은 어떤 문자도 변경하지 않는다.
    """
    if data is None:
        return []
    if not isinstance(data, (list, tuple, dict)):
        raise ToolError(
            INPUT_SHAPE_INVALID,
            f"입력 JSON은 record 목록 또는 object여야 한다: {type(data).__name__}",
            exit_code=EXIT_INPUT_ERROR,
        )
    return evidence_collector.catalog_records(data)


def input_label_map(data: Any) -> dict[str, list[str]]:
    """입력 snapshot 최상위 `candidateLabels` 매핑을 읽는다(없으면 빈 dict)."""
    return evidence_collector.label_map_entries(data) if _is_dict(data) else {}


def label_map_from_file(data: Any) -> dict[str, list[str]]:
    """`--label-map` 파일에서 `{label: [modelId, ...]}`를 읽는다.

    두 형태를 지원한다.
      - ``{"candidateLabels": {label: modelId}}`` — snapshot과 같은 형태
      - ``{label: modelId}`` — 최상위가 곧 매핑인 형태(인정 키로 감싸 재사용한다)

    판별·정규화는 :func:`evidence_collector.label_map_entries`가 하므로 이 파일에 매핑
    해석 규칙을 만들지 않는다.
    """
    if not _is_dict(data):
        raise ToolError(
            LABEL_MAP_SHAPE_INVALID,
            f"--label-map 파일은 {{label: modelId}} object여야 한다: {type(data).__name__}",
            exit_code=EXIT_INPUT_ERROR,
        )
    nested = evidence_collector.label_map_entries(data)
    if nested:
        return nested
    return evidence_collector.label_map_entries({SNAPSHOT_LABEL_MAP_KEY: data})


def merge_label_maps(*sources: dict[str, list[str]]) -> dict[str, list[str]]:
    """여러 라벨 매핑을 합친다(라벨 정렬 · modelId 순서 보존 중복 제거)."""
    combined: dict[str, list[str]] = {}
    for source in sources:
        for label, model_ids in (source or {}).items():
            bucket = combined.setdefault(label, [])
            for model_id in model_ids:
                if model_id not in bucket:
                    bucket.append(model_id)
    return {label: combined[label] for label in sorted(combined)}


def label_map_payload(label_map: dict[str, list[str]]) -> dict[str, Any]:
    """snapshot에 넣을 `{label: modelId}` 형태로 만든다(복수는 목록 그대로)."""
    return {
        label: (model_ids[0] if len(model_ids) == 1 else list(model_ids))
        for label, model_ids in label_map.items()
        if model_ids
    }


# ─────────────────────────────────────────────────────────────────
# credential field 제거 (Requirement 2.9, 10.7~10.9, 10.12)
# ─────────────────────────────────────────────────────────────────
def strip_credentials(value: Any, *, path: str = "", depth: int = 0) -> tuple[Any, list[dict]]:
    """:func:`store.classify_key`가 ``DROP``으로 분류한 키를 제거한 새 구조를 만든다.

    catalog identity·provider·route·capability field는 이 분류에 걸리지 않으므로 그대로
    보존된다. 제거 기록은 **이름과 경로만** 담는다(값은 담지 않는다).

    Returns:
        ``(cleaned, removals)``. ``removals``의 각 항목은 ``{"field", "path"}``다.

    Raises:
        ToolError: 중첩 깊이가 :data:`MAX_INPUT_DEPTH`를 넘는다.
    """
    if depth > MAX_INPUT_DEPTH:
        raise ToolError(
            INPUT_TOO_DEEP,
            f"입력 중첩이 너무 깊다(>{MAX_INPUT_DEPTH}): {path or '<root>'}",
            exit_code=EXIT_INPUT_ERROR,
        )
    if isinstance(value, dict):
        cleaned: dict[Any, Any] = {}
        removals: list[dict] = []
        for key, item in value.items():
            name = key if isinstance(key, str) else str(key)
            child = f"{path}.{name}" if path else name
            if store.classify_key(key) == "DROP":
                removals.append({"field": name, "path": child})
                continue
            sub, sub_removals = strip_credentials(item, path=child, depth=depth + 1)
            cleaned[key] = sub
            removals.extend(sub_removals)
        return cleaned, removals
    if isinstance(value, (list, tuple)):
        items: list[Any] = []
        removals = []
        for index, item in enumerate(value):
            sub, sub_removals = strip_credentials(item, path=f"{path}[{index}]", depth=depth + 1)
            items.append(sub)
            removals.extend(sub_removals)
        return items, removals
    return value, []


def sanitization_manifest(removals: Sequence[dict]) -> dict:
    """제거한 credential field 이름만 담은 sanitization manifest를 만든다.

    manifest에는 이 실행이 **실제로 제거한** 이름만 담고, 변경 field는 없다(catalog 본문의
    어떤 값도 바꾸지 않는다). 이름은 정렬해 결정론적으로 만든다.
    """
    names = sorted({_text(item.get("field")) for item in removals if _text(item.get("field"))})
    return {MANIFEST_REMOVED_KEY: names, MANIFEST_MODIFIED_KEY: []}


def manifest_conflicts(manifest: Any) -> list[str]:
    """manifest에 담긴 이름 중 credential 계열이 아니거나 catalog 본문 field인 이름 목록.

    자체 검사(Requirement 2.9, 2.10): 이름이 :func:`store.classify_key` ``DROP``이 아니거나
    :func:`evidence_collector.is_catalog_significant_field`가 참이면 그 export는 근거에서
    제외되므로 기록하지 않는다.
    """
    removed, modified = evidence_collector.manifest_field_names(manifest)
    conflicts: list[str] = []
    for name in list(removed) + list(modified):
        if store.classify_key(name) != "DROP" or evidence_collector.is_catalog_significant_field(name):
            if name not in conflicts:
                conflicts.append(name)
    return conflicts


def credential_residue(value: Any) -> list[str]:
    """구조에 남은 credential 키 경로 목록(빈 목록이면 기록해도 안전하다)."""
    _, removals = strip_credentials(value)
    return [_text(item.get("path")) for item in removals]


# ─────────────────────────────────────────────────────────────────
# environment identity (Requirement 2.6)
# ─────────────────────────────────────────────────────────────────
def resolve_environment(env: Any = None) -> tuple[dict, list[str], list[str]]:
    """Same_Gateway_Environment identity를 읽는다(전송·자격증명 접근 없음).

    Returns:
        ``(identity, missingFields, notes)``. `identity`는
        :func:`evidence_collector.environment_identity` 결과의 3필드만 담는다.
    """
    environ = os.environ if env is None else env
    gw, notes = runner.gateway_client(environ)
    identity = evidence_collector.environment_identity(gw, env=environ)
    identity = {
        field: _text(identity.get(field)) for field in contracts.REQUIRED_ENVIRONMENT_FIELDS
    }
    missing = [field for field in contracts.REQUIRED_ENVIRONMENT_FIELDS if not identity[field]]
    return identity, missing, list(notes)


# ─────────────────────────────────────────────────────────────────
# export 조립 (Requirement 2.7, 2.8, 스키마 버전)
# ─────────────────────────────────────────────────────────────────
def export_catalog_key() -> str:
    """export에서 catalog 본문을 담을 키(검증기 우선순위 첫 값)."""
    return evidence_collector.OPERATOR_EXPORT_CATALOG_KEYS[0]


def export_schema_version() -> int:
    """지원되는 Operator_Catalog_Export 스키마 버전 중 최신값."""
    return max(evidence_collector.OPERATOR_EXPORT_SCHEMA_VERSIONS)


def utc_time_pair(now_fn: Any = None) -> tuple[str, str]:
    """`(generatedAt, collectedAt)` — UTC ISO 8601이며 `generatedAt <= collectedAt`.

    시각은 :func:`contracts.utc_now_iso`만 쓴다. 두 번째 호출이 더 이르게 관측되는 일은
    없지만(단조 벽시계 가정), 순서 검증(Requirement 2.7)을 시각 소스에 의존하지 않도록
    역전 시 `collectedAt`을 `generatedAt`으로 맞춘다.
    """
    now = now_fn or contracts.utc_now_iso
    generated = now()
    collected = now()
    if collected < generated:
        collected = generated
    return generated, collected


def build_snapshot(records: Sequence[dict], labels: dict[str, Any]) -> dict:
    """catalog payload를 만든다(모델 record 목록 + 선택적 라벨 매핑).

    `models`는 :func:`evidence_collector.catalog_records`가 읽는 키이고,
    `candidateLabels`는 :func:`evidence_collector.label_map_entries`가 읽는 키다. 라벨
    매핑은 Catalog_Fingerprint 입력이 아니므로(fingerprint 입력은 모델 view뿐) 매핑을
    추가해도 fingerprint 재계산 일치가 유지된다.
    """
    snapshot: dict[str, Any] = {SNAPSHOT_MODELS_KEY: [dict(record) for record in records]}
    if labels:
        snapshot[SNAPSHOT_LABEL_MAP_KEY] = dict(labels)
    return snapshot


def build_export(
    *,
    snapshot: dict,
    environment: dict,
    manifest: dict,
    generated_at: str,
    collected_at: str,
) -> dict:
    """Operator_Catalog_Export를 조립한다(fingerprint는 저장할 payload에서 계산)."""
    return {
        "schemaVersion": export_schema_version(),
        "environment": dict(environment),
        "generatedAt": generated_at,
        "collectedAt": collected_at,
        "catalogFingerprint": canonicalizer.catalog_fingerprint(snapshot),
        "sanitizationManifest": dict(manifest),
        export_catalog_key(): snapshot,
    }


# ─────────────────────────────────────────────────────────────────
# 요약 투영 (라벨 매칭 · record 통계 · effort 선언)
# ─────────────────────────────────────────────────────────────────
def label_match_views(snapshot: Any, labels: Sequence[str]) -> list[dict]:
    """라벨별 매칭 결과(어느 라벨이 어느 modelId에 연결됐는지).

    판정은 :func:`evidence_collector.find_label_records`와 collector의 discovery 규칙을
    그대로 따른다(record 직접 명시 또는 최상위 매핑, 라벨 정규화는 대소문자·공백만).
    한 라벨이 서로 다른 model record를 가리키면 관계가 확정되지 않은 것으로 본다.
    """
    out: list[dict] = []
    for label in labels:
        records, match_kind = evidence_collector.find_label_records(snapshot, label)
        view: dict[str, Any] = {
            "candidateLabel": label,
            "matched": False,
            "modelId": contracts.UNDETERMINED,
            "provider": contracts.UNDETERMINED,
            "matchKind": match_kind,
            "advertisedRoutes": [],
            "effortRoutes": [],
            "reason": evidence_collector.LABEL_ASSOCIATION_ABSENT,
        }
        if not records:
            out.append(view)
            continue

        model_ids = {evidence_collector.record_model_id(record) for record in records}
        model_ids.discard(contracts.UNDETERMINED)
        if len(model_ids) > 1:
            view["reason"] = evidence_collector.LABEL_ASSOCIATION_AMBIGUOUS
            out.append(view)
            continue

        record = records[0]
        model_id = evidence_collector.record_model_id(record)
        if not model_id:
            view["reason"] = evidence_collector.MODEL_ID_ABSENT
            out.append(view)
            continue

        provider = evidence_collector.record_provider(record)
        view.update(
            {
                "matched": True,
                "modelId": model_id,
                "provider": provider,
                "advertisedRoutes": evidence_collector.record_advertised_routes(record),
                "effortRoutes": list(evidence_collector.record_advertised_effort(record)),
                "reason": REASON_OK if provider else evidence_collector.PROVIDER_ABSENT,
            }
        )
        out.append(view)
    return out


def effort_declaration_views(record: Any, model_id: str) -> list[dict]:
    """record의 effort 선언별 완전성(4요소)과 주입 가능 여부.

    완전성 판정은 :func:`evidence_collector.candidate_effort_contract` +
    :func:`contracts.effort_contract_missing`을 그대로 쓴다 — collector가 전송 전에 요구하는
    조건과 같은 판정이다(Requirement 5.6).
    """
    out: list[dict] = []
    for route, declaration in evidence_collector.record_advertised_effort(record).items():
        view: dict[str, Any] = {
            "routeKey": route,
            "complete": False,
            "missing": [],
            "fieldPath": None,
            "valueType": contracts.UNDETERMINED,
            "domainKind": contracts.UNDETERMINED,
            "injectable": route in evidence_collector.EFFORT_INJECTABLE_ROUTES,
        }
        if not model_id:
            view["missing"] = ["modelId"]
            out.append(view)
            continue
        contract = evidence_collector.candidate_effort_contract(
            declaration, model_id=model_id, route_key=route
        )
        missing = (
            contracts.effort_contract_missing(contract)
            if contract is not None
            else list(contracts.REQUIRED_EFFORT_CONTRACT_FIELDS)
        )
        view["missing"] = list(missing)
        view["complete"] = not missing
        if _is_dict(contract):
            view["fieldPath"] = contract.get("fieldPath")
            view["valueType"] = _text(contract.get("valueType"))
            view["domainKind"] = _text(contract.get("domainKind"))
        out.append(view)
    return out


def record_views(records: Sequence[dict]) -> list[dict]:
    """record별 요약(identity·provider·advertised route·effort 선언 완전성)."""
    out: list[dict] = []
    for record in records:
        model_id = evidence_collector.record_model_id(record)
        out.append(
            {
                "modelId": model_id,
                "invocationModelId": evidence_collector.record_invocation_model_id(record),
                "provider": evidence_collector.record_provider(record),
                "labels": evidence_collector.record_labels(record),
                "advertisedRoutes": evidence_collector.record_advertised_routes(record),
                "effort": effort_declaration_views(record, model_id),
            }
        )
    return out


# ─────────────────────────────────────────────────────────────────
# Invocation_Model_ID 대조 (control-plane 읽기 전용 — `--verify-invocation-ids`)
#
# Exact_Model_ID는 catalog identity이지만, production 요청 경로는 모델에 따라 inference
# profile ID로 전송한다(`server._resolve_callable_model_id`). probe가 production과 다른 ID를
# 보내지 않도록 record가 전송 ID(`invocationModelId`)를 실을 수 있고, 이 절은 그 값이
# **control-plane이 실제로 반환한 inference profile ID인지**만 확인한다.
#
# 값 추론 금지 규약:
#   - 접두사를 문자열로 만들지 않는다. 후보는 `list_inference_profiles` 응답의
#     `inferenceProfileId` 문자 그대로이며, ARN에서 model ID를 읽는 것은 profile↔model
#     연결을 확인하기 위한 **파싱**이다(ID 생성이 아니다).
#   - 후보가 여럿이면(예: 지역 범위가 다른 복수 profile) 자동 선택하지 않는다 — 운영자가
#     명시한 값이 후보 집합에 있는지 확인만 한다. 명시 값이 없으면 미확정으로 남긴다.
#   - 명시 값이 후보 집합에 없으면 **파일을 만들지 않는다**(근거 없는 전송 ID 차단).
# ─────────────────────────────────────────────────────────────────
def inference_profile_index(env: Any = None) -> tuple[dict[str, list[str]], list[str]]:
    """control-plane이 반환한 `{Exact_Model_ID: [inferenceProfileId, ...]}`(읽기 전용).

    기존 관례대로 profile·region은 환경변수(`AWS_PROFILE`·`AWS_REGION`)에서만 읽고,
    자격증명은 어떤 파일에도 저장하지 않는다. Gateway로는 아무 것도 전송하지 않는다
    (Bedrock control-plane 조회 1건뿐이며 모델 호출이 아니다).

    Returns:
        ``(index, notes)``. 조회할 수 없으면 index는 빈 dict이고 note에 원인이 남는다.
    """
    environ = os.environ if env is None else env
    notes: list[str] = []
    index: dict[str, list[str]] = {}
    try:
        import boto3  # 지연 import — 조회를 요청하지 않으면 의존이 생기지 않는다

        kwargs: dict[str, Any] = {}
        profile = (environ.get("AWS_PROFILE") or "").strip()
        if profile:
            kwargs["profile_name"] = profile
        region = (environ.get("AWS_REGION") or "").strip()
        if region:
            kwargs["region_name"] = region
        client = boto3.Session(**kwargs).client("bedrock")

        token: str | None = None
        for _ in range(_PROFILE_PAGE_LIMIT):
            params: dict[str, Any] = {"nextToken": token} if token else {}
            response = client.list_inference_profiles(**params)
            for summary in response.get("inferenceProfileSummaries") or []:
                if not _is_dict(summary):
                    continue
                profile_id = _text(summary.get("inferenceProfileId"))
                if not profile_id:
                    continue
                if _text(summary.get("status")) != _PROFILE_ACTIVE_STATUS:
                    continue
                for model in summary.get("models") or []:
                    arn = _text(model.get("modelArn")) if _is_dict(model) else ""
                    marker = arn.find(_FOUNDATION_MODEL_MARKER)
                    if marker < 0:
                        continue
                    model_id = arn[marker + len(_FOUNDATION_MODEL_MARKER):]
                    bucket = index.setdefault(model_id, [])
                    if profile_id not in bucket:
                        bucket.append(profile_id)
            token = response.get("nextToken") or None
            if not token:
                break
    except Exception as exc:
        notes.append(_truncate(f"control-plane-error:{type(exc).__name__}:{exc}"))
        return {}, notes
    return index, notes


def invocation_id_views(records: Sequence[dict], index: dict[str, list[str]]) -> list[dict]:
    """record별 Invocation_Model_ID 대조 결과(값은 control-plane 응답과의 문자 일치만).

    Returns:
        ``[{modelId, declared, resolved, verdict, candidates}]``. ``resolved``는 record에
        기록할 값이며, 확정할 수 없으면 빈 문자열이다(추측하지 않는다).
    """
    out: list[dict] = []
    for record in records:
        model_id = evidence_collector.record_model_id(record)
        declared = evidence_collector.record_invocation_model_id(record)
        candidates = list(index.get(model_id) or [])
        view: dict[str, Any] = {
            "modelId": model_id,
            "declared": declared,
            "resolved": declared,
            "verdict": INVOCATION_NOT_CHECKED,
            "candidates": candidates,
        }
        if declared and declared == model_id:
            # 전송 ID가 Exact_Model_ID와 같다 → profile 간접이 없다(대조 불필요).
            view["verdict"] = INVOCATION_MATCHES_EXACT
        elif declared:
            view["verdict"] = (
                INVOCATION_PROFILE_CONFIRMED if declared in candidates else INVOCATION_NOT_A_PROFILE
            )
        elif len(candidates) == 1:
            # 후보가 정확히 하나면 control-plane 값을 그대로 채운다(생성이 아니라 복사).
            view["resolved"] = candidates[0]
            view["verdict"] = INVOCATION_PROFILE_RESOLVED
        elif candidates:
            view["verdict"] = INVOCATION_PROFILE_AMBIGUOUS  # 자동 선택하지 않는다
        else:
            view["verdict"] = INVOCATION_PROFILE_ABSENT
        out.append(view)
    return out


def apply_invocation_ids(records: Sequence[dict], views: Sequence[dict]) -> list[dict]:
    """대조로 확정된 Invocation_Model_ID를 record에 반영한 새 목록을 만든다.

    이미 값이 있는 record는 문자 그대로 유지하고, `PROFILE_RESOLVED`인 record에만
    control-plane이 반환한 값을 채운다. 다른 field는 건드리지 않는다.
    """
    out: list[dict] = []
    for record, view in zip(records, views):
        resolved = _text(view.get("resolved"))
        if view.get("verdict") == INVOCATION_PROFILE_RESOLVED and resolved:
            item = dict(record)
            item["invocationModelId"] = resolved
            out.append(item)
            continue
        out.append(record)
    return out


def record_summary(views: Sequence[dict]) -> dict:
    """record 통계 — advertised route·effort 선언이 있는 record 수 등."""
    with_effort = [view for view in views if view["effort"]]
    complete_effort = [
        view
        for view in with_effort
        if all(item["complete"] for item in view["effort"])
    ]
    return {
        "records": len(views),
        "withModelId": sum(1 for view in views if view["modelId"]),
        "withProvider": sum(1 for view in views if view["provider"]),
        "withAdvertisedRoutes": sum(1 for view in views if view["advertisedRoutes"]),
        "withEffortDeclaration": len(with_effort),
        "withCompleteEffortDeclaration": len(complete_effort),
        "withoutEffortDeclaration": len(views) - len(with_effort),
    }


# ─────────────────────────────────────────────────────────────────
# 기록 경로 (Requirement 10.15 — userData 루트 밖 거부)
# ─────────────────────────────────────────────────────────────────
def resolve_out_path(store_obj: Any, out: str) -> str:
    """`--out`을 userData 루트 기준으로 해석하고 루트 밖이면 거부한다.

    상대 경로는 루트 기준으로 결합하고(``..`` 이스케이프는 store가 거부한다), 절대 경로는
    루트 하위인지 :func:`store.is_within`으로 확인한다. 이 검사는 `--dry-run`에서도 수행해
    잘못된 경로를 미리 알린다.
    """
    if store_obj is None:
        raise ToolError(
            STORE_UNAVAILABLE, "userData 루트를 만들 수 없다", exit_code=EXIT_WRITE_ERROR
        )
    raw = os.path.expanduser(str(out))
    if os.path.isabs(raw):
        target = os.path.abspath(raw)
        if not store.is_within(target, store_obj.user_data_root):
            raise ToolError(
                OUT_PATH_OUTSIDE_ROOT,
                f"userData 루트 밖 경로 거부: root={store_obj.user_data_root} path={target}",
                exit_code=EXIT_WRITE_ERROR,
            )
        return target
    try:
        return str(store_obj.resolve(raw))
    except store.StoreError as exc:
        raise ToolError(
            OUT_PATH_OUTSIDE_ROOT,
            f"userData 루트 밖 경로 거부: {_truncate(exc)}",
            exit_code=EXIT_WRITE_ERROR,
        ) from exc


def env_value_for(store_obj: Any, target: str) -> str:
    """`AE_CAPABILITY_OPERATOR_EXPORT`에 넣을 값(userData 루트 기준 상대 경로)."""
    try:
        return os.path.relpath(target, str(store_obj.user_data_root))
    except ValueError:  # pragma: no cover - 드라이브가 다른 경우(Windows)
        return target


# ─────────────────────────────────────────────────────────────────
# 계획 수립 (전부 검증 후에만 기록한다)
# ─────────────────────────────────────────────────────────────────
def build_plan(args: argparse.Namespace, *, env: Any = None, now_fn: Any = None) -> dict:
    """입력 → 정제 → export 조립 → 자체 검증까지 수행한 계획을 만든다(기록하지 않는다).

    Raises:
        ToolError: 입력 오류·environment 미충족·루트 밖 경로·sanitization 충돌.
            어떤 경우에도 파일을 만들지 않는다.
    """
    environ = os.environ if env is None else env
    notes: list[str] = []

    store_obj, store_error = runner.store_or_none(environ)
    if store_obj is None:
        raise ToolError(
            STORE_UNAVAILABLE,
            f"userData 루트를 만들 수 없다: {_truncate(store_error)}",
            exit_code=EXIT_WRITE_ERROR,
        )

    out_path = resolve_out_path(store_obj, args.out)

    environment, missing, env_notes = resolve_environment(environ)
    notes.extend(env_notes)
    if missing:
        raise ToolError(
            ENVIRONMENT_INCOMPLETE,
            "Same_Gateway_Environment identity가 완전하지 않아 export를 만들지 않는다",
            exit_code=EXIT_ENVIRONMENT_INCOMPLETE,
            detail={"environment": environment, "missing": missing},
        )

    input_data: Any = None
    input_path: str | None = None
    if args.input:
        input_data, input_path = read_json_input(args.input, store_obj, kind="--input")

    raw_records = input_records(input_data)
    if not raw_records:
        notes.append(NOTE_EMPTY_CATALOG)

    records, removals = strip_credentials(raw_records)
    if removals:
        notes.append(NOTE_CREDENTIAL_FIELDS_REMOVED)

    # Invocation_Model_ID 대조(요청 시에만) — control-plane 응답과 문자 일치하지 않는
    # 전송 ID는 export에 남기지 않는다. 대조를 요청하지 않으면 record 값은 그대로다.
    invocation_index: dict[str, list[str]] = {}
    invocations = invocation_id_views(records, invocation_index)
    if getattr(args, "verify_invocation_ids", False):
        invocation_index, cp_notes = inference_profile_index(environ)
        notes.extend(cp_notes)
        if not invocation_index:
            raise ToolError(
                CONTROL_PLANE_UNAVAILABLE,
                "control-plane inference profile 조회에 실패해 전송 ID를 대조할 수 없다",
                exit_code=EXIT_INVOCATION_UNVERIFIED,
                detail={"notes": cp_notes},
            )
        invocations = invocation_id_views(records, invocation_index)
        unverified = [
            view for view in invocations if view["verdict"] == INVOCATION_NOT_A_PROFILE
        ]
        if unverified:
            raise ToolError(
                INVOCATION_ID_UNVERIFIED,
                "control-plane이 반환하지 않은 전송 ID가 있어 export를 만들지 않는다",
                exit_code=EXIT_INVOCATION_UNVERIFIED,
                detail={
                    "models": [
                        {
                            "modelId": view["modelId"],
                            "declared": view["declared"],
                            "candidates": view["candidates"],
                        }
                        for view in unverified
                    ]
                },
            )
        if any(view["verdict"] == INVOCATION_PROFILE_RESOLVED for view in invocations):
            notes.append(NOTE_INVOCATION_ID_RESOLVED)
        if any(view["verdict"] == INVOCATION_PROFILE_AMBIGUOUS for view in invocations):
            notes.append(NOTE_INVOCATION_ID_AMBIGUOUS)
        records = apply_invocation_ids(records, invocations)
    if any(not _text(view["resolved"]) for view in invocations):
        notes.append(NOTE_INVOCATION_ID_ABSENT)

    label_sources = [input_label_map(input_data)]
    label_map_path: str | None = None
    if args.label_map:
        label_data, label_map_path = read_json_input(
            args.label_map, store_obj, kind="--label-map"
        )
        label_sources.append(label_map_from_file(label_data))
    label_map = merge_label_maps(*label_sources)

    manifest = sanitization_manifest(removals)
    conflicts = manifest_conflicts(manifest)
    if conflicts:
        raise ToolError(
            SANITIZATION_CONFLICT,
            "credential로 분류된 field 이름이 catalog 본문 field와 겹쳐 export를 만들지 않는다",
            exit_code=EXIT_SANITIZATION_CONFLICT,
            detail={"fields": conflicts},
        )

    snapshot = build_snapshot(records, label_map_payload(label_map))
    generated_at, collected_at = utc_time_pair(now_fn)
    export = build_export(
        snapshot=snapshot,
        environment=environment,
        manifest=manifest,
        generated_at=generated_at,
        collected_at=collected_at,
    )

    residue = credential_residue(export)
    if residue:  # pragma: no cover - strip_credentials가 선차단한다
        raise ToolError(
            CREDENTIAL_RESIDUE,
            "credential field가 남아 있어 기록하지 않는다",
            exit_code=EXIT_SANITIZATION_CONFLICT,
            detail={"paths": residue},
        )

    verdict = evidence_collector.validate_operator_catalog_export(export, environment)

    views = record_views(records)
    matches = label_match_views(snapshot, args.labels)
    if not any(item["matched"] for item in matches):
        notes.append(NOTE_NO_LABEL_ASSOCIATION)
    if any(not view["effort"] for view in views):
        notes.append(NOTE_EFFORT_DECLARATION_ABSENT)
    if any(
        not item["injectable"] for view in views for item in view["effort"]
    ):
        notes.append(NOTE_EFFORT_ROUTE_NOT_INJECTABLE)

    return {
        "store": store_obj,
        "outPath": out_path,
        "envValue": env_value_for(store_obj, out_path),
        "inputPath": input_path,
        "labelMapPath": label_map_path,
        "environment": environment,
        "export": export,
        "snapshot": snapshot,
        "manifest": manifest,
        "removals": removals,
        "labelMap": label_map,
        "labelMatches": matches,
        "recordViews": views,
        "invocationViews": invocations,
        "recordSummary": record_summary(views),
        "validation": verdict,
        "generatedAt": generated_at,
        "collectedAt": collected_at,
        "notes": notes,
    }


def write_export(plan: dict) -> str:
    """export를 `userData` 하위에 원자적으로 기록한다(정제는 이미 끝났다).

    ``sanitize=False``인 이유: store의 저장 직전 정제는 prompt/body 계열 **키 이름**을
    Probe_ID·Sanitized_Schema로 재작성하므로 catalog 본문에 적용하면
    Catalog_Fingerprint 재계산이 깨진다(Requirement 2.8). credential 제거는 이 스크립트가
    먼저 수행하고 잔존 0건을 확인했다.
    """
    try:
        return str(plan["store"].write_json(plan["outPath"], plan["export"], sanitize=False))
    except store.StoreError as exc:
        raise ToolError(
            WRITE_FAILED, f"기록 실패: {_truncate(exc)}", exit_code=EXIT_WRITE_ERROR
        ) from exc


# ─────────────────────────────────────────────────────────────────
# 템플릿 (`--template`) — 자리표시자 주석만, 실제 값 없음
# ─────────────────────────────────────────────────────────────────
def template_text() -> str:
    """`--input`에 넣을 입력 파일 템플릿(JSON + 줄 단위 `//` 주석).

    실제 model ID·provider·route·effort 값을 담지 않는다. 열거하는 토큰은 이 기능의 닫힌
    집합(:data:`contracts.KNOWN_ROUTES`, :class:`contracts.Value_Type`,
    :class:`contracts.Domain_Kind`)이며 특정 모델의 capability가 아니다.
    """
    routes = ", ".join(contracts.KNOWN_ROUTES)
    value_types = " | ".join(contracts.Value_Type.values())
    domain_kinds = " | ".join(contracts.Domain_Kind.values())
    label_keys = " | ".join(
        ("candidateLabel", "candidateLabels", "searchLabel", "label", "labels", "alias", "aliases")
    )
    injectable = ", ".join(sorted(evidence_collector.EFFORT_INJECTABLE_ROUTES))
    return f"""// Operator_Catalog_Export 입력 템플릿 — scripts/make_operator_catalog_export.py --input
//
// 모든 <REPLACE ...> 자리에는 운영자가 Same_Gateway_Environment에서 **직접 확인한 값**만
// 넣는다. 이 도구는 model ID·provider·route·effort 값을 만들어내지 않으며, 검색 라벨
// 문자열에서 어떤 identity도 유도하지 않는다.
//
// 이 파일의 주석은 모두 줄 단위(`//`)다. `//`로 시작하는 줄을 지우면 그대로 유효한 JSON이다.
{{
  // 모델 record 목록. record 하나가 catalog의 모델 하나다.
  // 목록을 비워 두면 빈 catalog가 만들어진다(모델 record를 대신 만들어 주지 않는다).
  "models": [
    {{
      // [필수] Exact_Model_ID — catalog가 반환한 문자 그대로(대소문자·접두사 변경 금지).
      //   읽는 키: modelId | exactModelId | modelIdentifier | id | model
      "modelId": "<REPLACE: catalog가 반환한 exact model ID 문자열>",

      // [필수] Provider_String — catalog가 반환한 문자 그대로.
      //   비면 Activation_Gate를 통과할 수 없어 VERIFIED 승격이 불가능하다.
      //   읽는 키: provider | providerName | providerId | vendor | owner | ownedBy
      "provider": "<REPLACE: catalog가 반환한 provider 문자열>",

      // [전송 ID · 선택] Invocation_Model_ID — production 요청 경로가 **실제 전송에 쓰는** ID.
      //   inference profile 전용 모델은 Exact_Model_ID가 아니라 profile ID로 호출되므로,
      //   probe가 production과 같은 ID를 보내도록 이 자리에 적는다(없으면 Exact_Model_ID를 보낸다).
      //   읽는 키: invocationModelId | invocationModelIds | inferenceProfileId
      //   값은 control-plane list_inference_profiles 응답의 inferenceProfileId 문자 그대로여야
      //   하며, --verify-invocation-ids 로 대조한다(응답에 없는 값이면 파일을 만들지 않는다).
      //   접두사를 직접 조립하지 말고 control-plane이 돌려준 문자열을 복사한다.
      "invocationModelId": "<REPLACE: control-plane inferenceProfileId 또는 이 줄을 지운다>",

      // [라벨 연결 · 선택] 이 record가 어느 검색 라벨에 해당하는지 **명시**한다.
      //   읽는 키: {label_keys}
      //   표시명(name·modelName·displayName)으로는 라벨이 연결되지 않는다.
      //   record에 넣지 않으려면 파일 맨 아래 candidateLabels 매핑을 쓴다.
      "candidateLabel": "<REPLACE: 검색 라벨 문자열(예: --labels 로 넘기는 값 중 하나)>",

      // [advertised route · 선택] catalog가 이 모델에 광고한 Known_Route 토큰 목록.
      //   허용 토큰(정확히 일치해야 인정): {routes}
      //   여기에 없는 route는 NOT_ADVERTISED로 기록되고 probe를 전송하지 않는다.
      //   읽는 키: route | routes | routeKey | routeKeys | advertisedRoutes | supportedRoutes
      "routes": ["<REPLACE: 위 토큰 중 catalog가 광고한 것만>"],

      // [effort 선언 · 선택] route별 effort 계약. 아래 4요소가 **모두** 있어야 effort probe가
      // 전송된다. 하나라도 없으면 Effort_Support_Status는 UNVERIFIED로 남고 전송은 0건이다.
      //   1) fieldPath  : request body의 exact field path(문자열 목록 또는 단일 문자열)
      //   2) valueType  : 값의 타입 — {value_types}
      //   3) domainKind : 값 집합의 종류 — {domain_kinds}
      //   4) complete domain:
      //        domainKind=ENUM  → enumValues 에 catalog가 명시한 **허용값 전체**
      //                           (일부만 넣으면 부분 검증이 되어 UNVERIFIED로 남는다)
      //        domainKind=RANGE → rangeLowerInclusive / rangeUpperInclusive (양 끝 포함)
      //   effort 선언 자체가 없는 record는 effort가 UNVERIFIED로 남는다(정상 상태).
      //   effort 주입 seam이 있는 route: {injectable}
      //   그 밖의 route는 effort probe를 전송하지 않는다(수동 body 조립은 근거가 아니다).
      //   route key는 위 Known_Route 토큰 그대로 쓴다.
      "effort": {{
        "<REPLACE_ROUTE_KEY: 위 Known_Route 토큰 중 하나>": {{
          "fieldPath": ["<REPLACE: catalog가 명시한 field path segment>"],
          "valueType": "<REPLACE: {value_types} 중 하나>",
          "domainKind": "<REPLACE: {domain_kinds} 중 하나>",
          // domainKind=ENUM일 때만 채운다(허용값 전체). RANGE면 이 줄을 지운다.
          "enumValues": ["<REPLACE: catalog가 명시한 허용값 전체>"],
          // domainKind=RANGE일 때만 숫자로 채운다(양 끝 포함). ENUM이면 null로 둔다.
          "rangeLowerInclusive": null,
          "rangeUpperInclusive": null
        }}
      }}
    }}
  ],

  // [라벨 매핑 · 선택] record 밖에서 라벨을 연결한다: {{"<검색 라벨>": "<Exact_Model_ID>"}}.
  //   값은 위 models[].modelId 와 **정확히 같은 문자열**이어야 한다.
  //   이 매핑만 따로 담은 파일을 --label-map 으로 넘겨도 된다(도구가 합쳐 넣는다).
  //   매핑을 쓰지 않으면 이 블록을 지운다.
  "candidateLabels": {{
    "<REPLACE: 검색 라벨>": "<REPLACE: 위 modelId 와 같은 문자열>"
  }}
}}"""


def template_json_text(text: str) -> str:
    """템플릿에서 주석 줄을 제거한 JSON 본문(유효성 확인·프로그램 사용에 쓴다)."""
    return "\n".join(
        line
        for line in text.splitlines()
        if not line.strip().startswith(TEMPLATE_COMMENT_PREFIX)
    )


# ─────────────────────────────────────────────────────────────────
# 출력
# ─────────────────────────────────────────────────────────────────
def format_summary(plan: dict, *, written: str | None, dry_run: bool) -> str:
    """생성 결과 요약(경로 · 환경변수 값 · 라벨 매칭 · route·effort 선언 수)."""
    store_obj = plan["store"]
    verdict = plan["validation"]
    summary = plan["recordSummary"]
    lines = [
        "[Operator_Catalog_Export] " + ("생성 완료" if written else "생성 계획(미기록)"),
        f"  userData 루트     : {store_obj.user_data_root}",
        f"  기록 경로         : {plan['outPath']}",
        f"  {ENV_OPERATOR_EXPORT} : {plan['envValue']}",
        f"  입력(--input)     : {plan['inputPath'] or '(없음 — 빈 catalog)'}",
        f"  라벨 매핑(--label-map): {plan['labelMapPath'] or '(없음)'}",
        f"  schemaVersion     : {plan['export']['schemaVersion']}",
        "  environment       : "
        f"envId={plan['environment']['gatewayEnvironmentId']} "
        f"endpoint={plan['environment']['endpointIdentity']} "
        f"region={plan['environment']['region']}",
        f"  generatedAt(UTC)  : {plan['generatedAt']}",
        f"  collectedAt(UTC)  : {plan['collectedAt']}",
        f"  catalogFingerprint: {plan['export']['catalogFingerprint']}",
    ]

    removed = plan["manifest"].get(MANIFEST_REMOVED_KEY) or []
    lines.append(
        "  sanitizationManifest.removedFields: "
        + (", ".join(removed) if removed else "(제거한 credential field 없음)")
    )
    if plan["removals"]:
        lines.append(
            "    제거 위치(이름·경로만 기록, 값은 기록하지 않음): "
            + ", ".join(item["path"] for item in plan["removals"][:12])
        )

    lines.extend(
        [
            "",
            "[record 통계]",
            f"  모델 record            : {summary['records']}건"
            + ("  ← 입력이 없어 빈 catalog로 만들었다" if not summary["records"] else ""),
            f"  modelId 있는 record    : {summary['withModelId']}건",
            f"  provider 있는 record   : {summary['withProvider']}건",
            f"  advertised route 선언  : {summary['withAdvertisedRoutes']}건",
            f"  effort 선언            : {summary['withEffortDeclaration']}건"
            f" (4요소 완전: {summary['withCompleteEffortDeclaration']}건)",
            f"  effort 선언 없는 record: {summary['withoutEffortDeclaration']}건"
            " → 해당 모델의 effort는 UNVERIFIED로 남는다",
        ]
    )

    lines.extend(["", "[라벨 매칭]"])
    for item in plan["labelMatches"]:
        lines.append(
            f"  {str(item['candidateLabel']):<10} "
            f"{'MATCHED  ' if item['matched'] else 'UNMATCHED'} "
            f"modelId={item['modelId'] or '(미확정)'} "
            f"provider={item['provider'] or '(미확정)'} "
            f"match={item['matchKind'] or '-'} "
            f"routes={','.join(item['advertisedRoutes']) or '-'} "
            f"effort={','.join(item['effortRoutes']) or '-'} "
            f"reason={item['reason']}"
        )
    unmatched = [item["candidateLabel"] for item in plan["labelMatches"] if not item["matched"]]
    lines.append(f"  미연결 라벨: {', '.join(unmatched) if unmatched else '(없음)'}")
    invocations = plan.get("invocationViews") or []
    if invocations:
        lines.extend(["", "[전송 ID(Invocation_Model_ID) 대조]"])
        for item in invocations:
            lines.append(
                f"  modelId={item['modelId'] or '(미확정)'} "
                f"전송ID={item['resolved'] or '(미확정 → Exact_Model_ID로 전송)'} "
                f"verdict={item['verdict']} "
                f"후보={','.join(item['candidates']) or '-'}"
            )
        lines.append(
            "  * 후보는 control-plane list_inference_profiles 응답의 inferenceProfileId "
            "문자 그대로다(접두사를 만들지 않는다)."
        )
    lines.append(
        "  * 라벨 매칭은 record의 candidateLabel/label/alias 계열 field 또는 최상위 "
        "candidateLabels 매핑만 본다."
    )
    lines.append(
        "    표시명(name·modelName·displayName) 기반 매칭은 성립하지 않는다 — 표시명만 "
        "넣으면 라벨이 연결되지 않는다."
    )

    effort_rows = [
        (view["modelId"], item)
        for view in plan["recordViews"]
        for item in view["effort"]
    ]
    if effort_rows:
        lines.extend(["", "[effort 선언]"])
        for model_id, item in effort_rows:
            lines.append(
                f"  modelId={model_id or '(미확정)'} route={item['routeKey']} "
                f"complete={item['complete']} "
                f"fieldPath={item['fieldPath']} "
                f"valueType={item['valueType'] or '(미확정)'} "
                f"domainKind={item['domainKind'] or '(미확정)'} "
                f"주입가능={item['injectable']} "
                f"결손={','.join(item['missing']) or '-'}"
            )
    lines.append(
        "  * effort 선언이 없거나 4요소(field path·value type·domain kind·complete domain) 중 "
        "하나라도 없으면"
    )
    lines.append("    effort probe는 전송되지 않고 Effort_Support_Status는 UNVERIFIED로 남는다.")

    lines.extend(
        [
            "",
            f"[자체 검증] valid={verdict['valid']} "
            f"reasons={','.join(verdict.get('reasons') or []) or '-'}",
            "  검증 항목: environment identity·region 일치 / UTC 시각 순서 / "
            "Catalog_Fingerprint 재계산 일치 / sanitization manifest credential 한정",
        ]
    )
    if plan["notes"]:
        lines.append(f"  notes: {', '.join(str(note) for note in plan['notes'])}")

    lines.append("")
    if written:
        lines.append(f"[기록] {written}")
        lines.append(
            f"  다음 실행에 사용: {ENV_OPERATOR_EXPORT}={plan['envValue']} "
            "ai_engine/.venv/bin/python scripts/validate_gateway_model_capabilities.py "
            "--stage discover"
        )
    elif dry_run:
        lines.append("[기록] --dry-run — 파일을 만들지 않았다")
    return "\n".join(lines)


def format_error(error: ToolError) -> str:
    """중단 사유를 사람이 읽는 형태로 만든다(비민감 — 이름·경로·이유 코드만)."""
    lines = [f"[Operator_Catalog_Export] {error.reason}: {error.message}"]
    detail = error.detail
    if error.reason == ENVIRONMENT_INCOMPLETE and _is_dict(detail):
        identity = detail.get("environment") or {}
        lines.append("  현재 environment identity:")
        for field in contracts.REQUIRED_ENVIRONMENT_FIELDS:
            value = _text(identity.get(field))
            lines.append(f"    {field:<22} = {value or '(비어 있음)'}")
        lines.append(f"  비어 있는 필드: {', '.join(detail.get('missing') or [])}")
        lines.append("  채우는 방법(값을 추측해 넣지 않는다):")
        for field in detail.get("missing") or []:
            lines.append(f"    {field:<22} ← {ENVIRONMENT_SOURCES.get(field, '(출처 미정)')}")
        lines.append("  environment가 완전해지기 전에는 export를 만들지 않는다.")
    elif error.reason == SANITIZATION_CONFLICT and _is_dict(detail):
        lines.append(
            "  충돌 field: " + ", ".join(str(name) for name in detail.get("fields") or [])
        )
        lines.append(
            "  이 이름은 catalog identity·provider·route·capability field이므로 제거하면 "
            "export가 근거에서 제외된다."
        )
        lines.append("  입력에서 해당 field 이름을 바꾸거나 직접 제거한 뒤 다시 실행한다.")
    elif error.reason == CREDENTIAL_RESIDUE and _is_dict(detail):
        lines.append("  잔존 경로: " + ", ".join(str(item) for item in detail.get("paths") or []))
    return "\n".join(lines)


# ─────────────────────────────────────────────────────────────────
# CLI
# ─────────────────────────────────────────────────────────────────
def build_parser() -> argparse.ArgumentParser:
    """CLI 파서 — `--input`, `--label-map`, `--labels`, `--out`, `--dry-run`, `--template`."""
    parser = argparse.ArgumentParser(
        prog="make_operator_catalog_export",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        description=(
            "운영자가 제공한 Same_Gateway_Environment catalog 정보로 Operator_Catalog_Export를\n"
            "userData/capability/ 하위에 원자적으로 만든다. 생성 직후 4개 검증(environment\n"
            "identity·region 일치 / UTC 시각 순서 / Catalog_Fingerprint 재계산 일치 /\n"
            "sanitization manifest credential 한정)을 스스로 실행하고, 통과하지 못하면 기록하지\n"
            "않고 이유 코드를 출력한다. 반드시 ai_engine/.venv/bin/python으로 실행한다."
        ),
        epilog=(
            "라벨 매칭 규칙(중요)\n"
            "  라벨은 record의 candidateLabel/label/alias 계열 field 또는 snapshot 최상위\n"
            "  candidateLabels 매핑으로만 연결된다. **표시명(name·modelName·displayName) 기반\n"
            "  매칭은 성립하지 않는다** — 표시명만 넣으면 라벨이 연결되지 않고 해당 라벨은\n"
            "  UNVERIFIED로 남는다.\n"
            "\n"
            "effort 선언\n"
            "  effort는 field path·value type·domain kind·complete domain 4요소가 모두 있어야\n"
            "  검증 대상이 된다. 선언이 없거나 결손이면 effort probe는 전송되지 않고\n"
            "  Effort_Support_Status는 UNVERIFIED로 남는다(이 도구는 effort 값을 만들지 않는다).\n"
            "\n"
            "값 추론 금지\n"
            "  model ID·provider·advertised route·effort field path·effort 허용값은 전부\n"
            "  --input/--label-map 에서만 온다. 입력이 없으면 빈 catalog로 만든다.\n"
            "\n"
            "예시\n"
            "  ai_engine/.venv/bin/python scripts/make_operator_catalog_export.py --template\n"
            "  ai_engine/.venv/bin/python scripts/make_operator_catalog_export.py \\\n"
            "      --input capability/operator_input.json --dry-run\n"
            "  AE_GATEWAY_ENV_ID=... ai_engine/.venv/bin/python \\\n"
            "      scripts/make_operator_catalog_export.py --input /tmp/catalog.json\n"
        ),
    )
    parser.add_argument(
        "--input",
        default=None,
        metavar="PATH",
        help=(
            "운영자 catalog record 목록 JSON. userData 루트 기준 상대 경로 또는 절대 경로. "
            "record 목록 / {\"models\": [...]} / {modelId: record} 매핑을 모두 지원한다. "
            "생략하면 빈 catalog로 만든다(모델 record를 만들어 주지 않는다)."
        ),
    )
    parser.add_argument(
        "--label-map",
        default=None,
        metavar="PATH",
        help=(
            "{\"<검색 라벨>\": \"<Exact_Model_ID>\"} 매핑 JSON. userData 상대 또는 절대 경로. "
            "입력 snapshot 최상위 candidateLabels 매핑과 함께 사용할 수 있다."
        ),
    )
    parser.add_argument(
        "--labels",
        nargs="*",
        default=list(evidence_collector.CANDIDATE_LABELS),
        metavar="LABEL",
        help=(
            "매칭 결과를 보고할 검색 라벨 목록(검색 라벨일 뿐 model identity가 아니다). "
            "기본값은 evidence_collector.CANDIDATE_LABELS."
        ),
    )
    parser.add_argument(
        "--out",
        default=DEFAULT_OUT_PATH,
        metavar="PATH",
        help=(
            f"기록 경로(userData 루트 기준 상대 경로 또는 루트 하위 절대 경로). "
            f"기본값 {DEFAULT_OUT_PATH}. 루트 밖 경로는 거부한다."
        ),
    )
    parser.add_argument(
        "--verify-invocation-ids",
        action="store_true",
        help=(
            "record의 invocationModelId(전송 ID)를 Bedrock control-plane "
            "list_inference_profiles 응답과 대조한다(읽기 전용 조회 1건, Gateway 전송 0건). "
            "응답에 없는 값이 있으면 파일을 만들지 않는다. 값이 비어 있고 후보 profile이 "
            "정확히 하나면 그 값을 채우고, 후보가 여럿이면 자동 선택하지 않고 비워 둔다."
        ),
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="파일을 만들지 않고 자체 검증 결과와 요약만 출력한다.",
    )
    parser.add_argument(
        "--template",
        action="store_true",
        help=(
            "--input에 넣을 입력 파일 템플릿을 stdout으로 출력한다(자리표시자 주석만 담고 "
            "실제 model ID·provider·effort 값은 담지 않는다)."
        ),
    )
    return parser


def normalize_labels(labels: Iterable[str]) -> list[str]:
    """라벨 목록을 정규화한다(빈 값 제거 · 순서 보존 중복 제거)."""
    out: list[str] = []
    for label in labels or []:
        if not isinstance(label, str) or not label.strip():
            continue
        if label not in out:
            out.append(label)
    return out


def main(argv: Sequence[str] | None = None) -> int:
    """CLI 엔트리 — interpreter 확인 → 계획 수립·자체 검증 → (기록) → 요약 출력.

    실행 순서가 중요하다. capability 모듈 import는 interpreter guard **다음**이며, guard가
    실패하면 어떤 파일도 만들지 않고 interpreter 환경 오류만 보고한다.
    """
    root = runner.repo_root()
    verdict = runner.interpreter_verdict(root)

    loaded, load_error = load_capability()
    if not loaded:
        print(
            "[Operator_Catalog_Export] "
            + runner.interpreter_error_message(runner.with_import_failure(verdict, load_error)),
            file=sys.stderr,
        )
        return EXIT_INTERPRETER_ERROR

    args = build_parser().parse_args(argv)

    if not verdict["ok"]:
        print(
            f"[Operator_Catalog_Export] {runner.interpreter_error_message(verdict)}",
            file=sys.stderr,
        )
        return EXIT_INTERPRETER_ERROR

    if args.template:
        print(template_text())
        return EXIT_OK

    args.labels = normalize_labels(args.labels) or list(evidence_collector.CANDIDATE_LABELS)

    try:
        plan = build_plan(args)
    except ToolError as error:
        print(format_error(error), file=sys.stderr)
        return error.exit_code

    if not plan["validation"]["valid"]:
        print(format_summary(plan, written=None, dry_run=bool(args.dry_run)))
        print(
            "[Operator_Catalog_Export] "
            f"{EXPORT_VALIDATION_FAILED}: 자체 검증 실패로 기록하지 않았다 — "
            f"reasons={','.join(plan['validation'].get('reasons') or [])}",
            file=sys.stderr,
        )
        return EXIT_VALIDATION_FAILED

    if args.dry_run:
        print(format_summary(plan, written=None, dry_run=True))
        return EXIT_OK

    try:
        written = write_export(plan)
    except ToolError as error:
        print(format_error(error), file=sys.stderr)
        return error.exit_code

    print(format_summary(plan, written=written, dry_run=False))
    return EXIT_OK


if __name__ == "__main__":
    sys.exit(main())
