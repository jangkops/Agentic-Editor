"""Capability_Canonicalizer — canonical 정규화·직렬화·fingerprint 계산.

이 모듈은 Capability_Map entry·Verification_Record·catalog snapshot을 **결정론적
바이트열**로 환원하고 그 바이트열에서 fingerprint를 만든다. 순수 함수만 있으며
디스크·네트워크·시각·난수에 접근하지 않는다(같은 입력 → 항상 같은 출력).

특정 model ID, provider, route 지원 여부, effort field path, effort 허용값은 이
모듈에 존재하지 않는다. 이 모듈은 "그 값들이 어디에 놓이고 어떻게 정렬되는가"만
정의한다(값 추론 금지).

design.md "canonical serialization과 fingerprint 계산 규칙" 8단계 구현:

1. **정규화** (`canonical`) — ``dict`` key를 UTF-8 바이트 순으로 정렬한다. 집합 의미
   collection(:data:`SET_LIKE_PATHS`)은 canonical 표현 기준으로 정렬·중복 제거한다.
   순서 의미 collection(:data:`ORDER_BEARING_PATHS`, 대표적으로 field path)은 원
   순서를 보존한다. ``null``은 제거하지 않고 명시적으로 유지한다(미확정과 미존재 구분).
2. **직렬화** (`serialize`) — ``json.dumps(value, sort_keys=True, ensure_ascii=False,
   separators=(",", ":"))``. 동일 의미 입력은 동일 UTF-8 바이트를 만든다.
3. **Capability_Fingerprint 입력** — ``schemaVersion``, ``modelId``, ``provider``,
   ``catalogFingerprint``, 모든 Known_Route의 ``{status, allowlist, contract}``,
   모든 effort entry의 ``{status, contract}``.
4. **Capability_Fingerprint 제외 입력** — ``candidateLabel``, ``displayName``, 모든 UTC
   시각, ``revision``, evidence 저장 위치, ``evidenceRecordId``/``evidence``, 로그,
   Sanitized_Schema. 구현은 3단계의 **닫힌 화이트리스트**만 읽으므로 그 밖의 필드는
   구조적으로 fingerprint에 도달할 수 없다(계약 하위의 ``evidenceRef``도 제거한다).
5. **계산** — ``"cfp1:sha256:" + sha256(canonical_bytes).hexdigest()``.
6. **Catalog_Fingerprint** — 입력은 catalog snapshot의 model identity·provider·
   advertised route·capability 필드. 수집 시각·수집자·로그는 제외. ``"cat1:sha256:<hex>"``.
7. **Evidence_Record_ID** — 입력은 정제 Verification_Record의 불변 필드(``runId``,
   ``environment``, ``revision``, ``modelId``, ``provider``, ``catalogFingerprint``,
   ``capabilityFingerprint``, ``routeResults``의 판정 결과, ``effortResults``의 판정
   결과, ``verifiedAt``). ``"evr1:sha256:<hex>"``.
8. **멱등성** — ``canonical(canonical(x)) == canonical(x)``,
   ``serialize(deserialize(serialize(x))) == serialize(x)``.

field path 매칭 규약: :data:`SET_LIKE_PATHS`·:data:`ORDER_BEARING_PATHS`의 패턴은
``.``로 구분된 경로이며 ``*``는 임의의 한 성분(route key·목록 원소 자리)에 대응한다.
패턴은 경로의 **뒷부분(suffix)** 과 비교하므로 entry 단위 정규화
(``routes.CONVERSE.contract.optionalFields``), map 단위 정규화
(``entries.*.routes.CONVERSE.contract.optionalFields``), fingerprint 입력 단위 정규화가
같은 규칙을 공유한다. 두 목록이 겹치면 **순서 의미가 우선**한다(field path는 절대
재배열하지 않는다). 어느 목록에도 없는 목록은 기본적으로 원 순서를 보존한다.

의미 손실은 예외로 만든다: JSON으로 표현할 수 없는 값, 중복 key를 가진 JSON 텍스트,
정규화 후 충돌하는 dict key는 :class:`MalformedEntryError`(Malformed_Entry 신호)로
보고한다. ``contracts.validate_entry(fingerprint_fn=...)`` 는 이 예외를
``FINGERPRINT_MISMATCH`` 판정으로 흡수한다.

참조: .kiro/specs/gateway-models-effort-support/design.md
  - "Data Models" → "canonical serialization과 fingerprint 계산 규칙"
  - "Components and Interfaces" 3절 (Capability_Map / Capability_Canonicalizer)
  - Correctness Properties 5(멱등성)·6(round-trip)·7(ordering invariance)
Requirements: 3.12, 3.13, 3.14, 3.15, 3.16
"""
from __future__ import annotations

import hashlib
import json
from typing import Any, Iterable, Sequence

from . import contracts

# ─────────────────────────────────────────────────────────────────
# fingerprint 형식 (design.md 5·6·7단계)
# ─────────────────────────────────────────────────────────────────

#: Capability_Fingerprint 접두사.
CAPABILITY_FINGERPRINT_PREFIX = "cfp1:sha256:"

#: Catalog_Fingerprint 접두사.
CATALOG_FINGERPRINT_PREFIX = "cat1:sha256:"

#: Evidence_Record_ID 접두사.
EVIDENCE_RECORD_ID_PREFIX = "evr1:sha256:"

#: 경로 패턴의 와일드카드 성분(route key 또는 목록 원소 자리).
WILDCARD = "*"

#: 경로 성분 구분자.
PATH_SEPARATOR = "."


# ─────────────────────────────────────────────────────────────────
# 집합 의미 / 순서 의미 경로
#
# 집합 의미(SET_LIKE_PATHS): 원소 순서에 의미가 없다 → canonical 표현 기준 정렬 후
#   중복 제거. Property 7(ordering invariance)이 요구하는 불변성의 근거다.
# 순서 의미(ORDER_BEARING_PATHS): field path처럼 순서가 곧 의미다 → 원 순서 보존.
#   SET_LIKE와 겹치면 이쪽이 우선한다.
# ─────────────────────────────────────────────────────────────────
SET_LIKE_PATHS: tuple[str, ...] = (
    # design.md canonicalizer 코드블록이 열거한 자리
    "invocationModelIds",
    "routes.*.contract.optionalFields",
    "effort.*.contract.enumValues",
    "evidence",
    # design.md Data Models 표가 "집합 의미"로 표시한 나머지 자리
    # (짧은 형태는 계약이 단독으로 정규화될 때도 같은 의미를 유지한다)
    "optionalFields",
    "purposes",
    "enumValues",
    "verifiedValues",
    # catalog snapshot의 모델 목록 — 나열 순서는 catalog 응답 순서일 뿐 의미가 없다
    "models",
    # Verification_Record 판정 결과 목록 — probe 실행 순서일 뿐 의미가 없다
    "routeResults",
    "effortResults",
)

ORDER_BEARING_PATHS: tuple[str, ...] = (
    # design.md canonicalizer 코드블록이 열거한 자리
    "routes.*.contract.modelIdFieldPath",
    "routes.*.contract.messageFieldPath",
    "routes.*.contract.inferenceConfigFieldPath",
    "effort.*.contract.fieldPath",
    # 짧은 형태 — 계약 단독 정규화, effortResults[].fieldPath 등에서도 순서를 보존한다
    "modelIdFieldPath",
    "messageFieldPath",
    "inferenceConfigFieldPath",
    "fieldPath",
)


def _compile_patterns(patterns: Iterable[str]) -> tuple[tuple[str, ...], ...]:
    return tuple(tuple(pattern.split(PATH_SEPARATOR)) for pattern in patterns)


_SET_LIKE_PATTERNS = _compile_patterns(SET_LIKE_PATHS)
_ORDER_BEARING_PATTERNS = _compile_patterns(ORDER_BEARING_PATHS)


# ─────────────────────────────────────────────────────────────────
# fingerprint 입력 화이트리스트 (design.md 3·6·7단계)
# ─────────────────────────────────────────────────────────────────

#: Capability_Fingerprint에 포함하는 entry 최상위 필드(3단계).
CAPABILITY_FINGERPRINT_ENTRY_FIELDS: tuple[str, ...] = (
    "schemaVersion",
    "modelId",
    "provider",
    "catalogFingerprint",
)

#: Capability_Fingerprint에 포함하는 Route_Entry 필드(3단계).
CAPABILITY_FINGERPRINT_ROUTE_FIELDS: tuple[str, ...] = ("status", "allowlist", "contract")

#: Capability_Fingerprint에 포함하는 Effort_Entry 필드(3단계).
CAPABILITY_FINGERPRINT_EFFORT_FIELDS: tuple[str, ...] = ("status", "contract")

#: 계약 하위에서 fingerprint 입력에서 제거하는 필드(4단계 — evidence 참조).
CONTRACT_EXCLUDED_FIELDS: frozenset[str] = frozenset({"evidenceRef"})

#: 4단계 제외 목록(문서·테스트용. 구현은 화이트리스트 방식이라 이 목록에 의존하지 않는다).
CAPABILITY_FINGERPRINT_EXCLUDED_FIELDS: tuple[str, ...] = (
    "candidateLabel",
    "displayName",
    "verifiedAt",
    "revision",
    "evidence",
    "evidenceRecordId",
    "evidenceRef",
    "sanitizedSchema",
    "logs",
)

#: Evidence_Record_ID에 포함하는 Verification_Record 최상위 필드(7단계).
EVIDENCE_ID_RECORD_FIELDS: tuple[str, ...] = (
    "runId",
    "revision",
    "modelId",
    "provider",
    "catalogFingerprint",
    "capabilityFingerprint",
    "verifiedAt",
)

#: Evidence_Record_ID에 포함하는 routeResults 판정 필드(7단계 — probeId·schema 제외).
EVIDENCE_ID_ROUTE_RESULT_FIELDS: tuple[str, ...] = (
    "routeKey",
    "http",
    "validOutput",
    "terminalSuccess",
    "allowlist",
    "status",
    "correctionUsed",
)

#: Evidence_Record_ID에 포함하는 effortResults 판정 필드(7단계 — probeId·schema 제외).
EVIDENCE_ID_EFFORT_RESULT_FIELDS: tuple[str, ...] = (
    "routeKey",
    "fieldPath",
    "value",
    "status",
    "baselineSucceeded",
)


# ─────────────────────────────────────────────────────────────────
# Catalog_Fingerprint 입력 분류 (design.md 6단계)
#
# 입력은 model identity·provider·advertised route·capability 필드다.
# 수집 시각·수집자·로그·fingerprint 자기참조는 제외한다(재계산 비교가 성립하도록).
# ─────────────────────────────────────────────────────────────────

#: catalog snapshot에서 모델 목록을 담을 수 있는 key(정규화 형태).
_CATALOG_MODEL_LIST_KEYS: frozenset[str] = frozenset({
    "models", "entries", "modelsummaries", "data", "items", "catalog",
})

#: model identity 필드(정규화 형태).
_CATALOG_IDENTITY_KEYS: frozenset[str] = frozenset({
    "modelid", "id", "model", "modelidentifier", "modelarn", "arn",
    "name", "modelname", "exactmodelid",
})

#: provider 필드(정규화 형태).
_CATALOG_PROVIDER_KEYS: frozenset[str] = frozenset({
    "provider", "providername", "providerid", "vendor", "owner", "ownedby",
})

#: advertised route 필드(정규화 형태).
_CATALOG_ROUTE_KEYS: frozenset[str] = frozenset({
    "route", "routes", "routekey", "routekeys", "advertisedroutes", "supportedroutes",
    "endpoint", "endpoints", "inferencetypes", "inferencetypessupported",
    "responsestreamingsupported", "streamingsupported",
})

#: capability 필드(정규화 형태).
_CATALOG_CAPABILITY_KEYS: frozenset[str] = frozenset({
    "capability", "capabilities", "mode", "modes", "executionmode", "executionmodes",
    "effort", "effortvalues", "syncsupport", "asyncsupport", "streamingsupport",
    "inputmodalities", "outputmodalities", "modalities", "customizationssupported",
    "lifecyclestatus", "status", "family", "type", "kind",
})

#: 수집 메타데이터·로그·fingerprint 자기참조(정규화 형태 정확 일치 → 제외).
_CATALOG_METADATA_KEYS: frozenset[str] = frozenset({
    "collectedat", "generatedat", "fetchedat", "retrievedat", "exportedat",
    "createdat", "updatedat", "verifiedat", "inspectedat", "timestamp", "time", "date",
    "collector", "collectedby", "generatedby", "exportedby", "operator",
    "log", "logs", "logline", "loglines", "message", "messages", "note", "notes",
    "fingerprint", "catalogfingerprint", "capabilityfingerprint",
    "sanitizationmanifest", "manifest", "sanitizedschema",
    "requestid", "runid", "revision", "interpreter", "interpreterpath",
    "probeid", "elapsedms", "durationms", "latencyms",
})

_CATALOG_KEPT_KEYS: frozenset[str] = (
    _CATALOG_IDENTITY_KEYS
    | _CATALOG_PROVIDER_KEYS
    | _CATALOG_ROUTE_KEYS
    | _CATALOG_CAPABILITY_KEYS
)


# ─────────────────────────────────────────────────────────────────
# 예외
# ─────────────────────────────────────────────────────────────────
class MalformedEntryError(ValueError):
    """canonical 정규화·직렬화에서 의미 손실이 발생했다(Malformed_Entry 신호).

    ``code``는 ``contracts.MALFORMED_CODES`` 중 하나이며, ``reason``은
    ``contracts`` 판정 이유와 같은 ``CODE:path(detail)`` 형식이다.
    """

    def __init__(
        self,
        message: str,
        *,
        code: str = contracts.TYPE_MISMATCH,
        path: str = "",
        detail: str = "",
    ) -> None:
        super().__init__(message)
        self.code = code
        self.path = path
        self.detail = detail

    @property
    def reason(self) -> str:
        """``contracts`` 판정 이유와 동일한 형식의 문자열."""
        path = self.path or "value"
        return f"{self.code}:{path}({self.detail})" if self.detail else f"{self.code}:{path}"


# ─────────────────────────────────────────────────────────────────
# 경로 판정
# ─────────────────────────────────────────────────────────────────
def _path_text(path: Sequence[str]) -> str:
    return PATH_SEPARATOR.join(path) if path else "<root>"


def _matches(path: Sequence[str], pattern: Sequence[str]) -> bool:
    """``pattern``이 ``path``의 뒷부분과 일치하는지(``*``는 임의의 한 성분)."""
    if len(pattern) > len(path):
        return False
    tail = path[len(path) - len(pattern):]
    return all(seg == WILDCARD or seg == actual for seg, actual in zip(pattern, tail))


def is_set_like_path(path: Sequence[str]) -> bool:
    """해당 경로의 collection이 집합 의미인지(정렬·중복 제거 대상)."""
    return any(_matches(path, pattern) for pattern in _SET_LIKE_PATTERNS)


def is_order_bearing_path(path: Sequence[str]) -> bool:
    """해당 경로의 collection이 순서 의미인지(원 순서 보존 대상)."""
    return any(_matches(path, pattern) for pattern in _ORDER_BEARING_PATTERNS)


# ─────────────────────────────────────────────────────────────────
# 1단계: 정규화
# ─────────────────────────────────────────────────────────────────
def _sort_bytes(text: str) -> bytes:
    """UTF-8 바이트 순 정렬 키(대용 surrogate도 비교 가능하게 통과시킨다)."""
    return text.encode("utf-8", "surrogatepass")


def _dict_key(key: Any, path: Sequence[str]) -> str:
    """dict key를 JSON 문자열 key로 정규화한다(``json.dumps``와 동일한 규칙)."""
    if isinstance(key, str):
        return str(key)
    if key is True:
        return "true"
    if key is False:
        return "false"
    if key is None:
        return "null"
    if isinstance(key, (int, float)):
        return json.dumps(key)
    raise MalformedEntryError(
        f"JSON key로 표현할 수 없는 dict key: {type(key).__name__} at {_path_text(path)}",
        code=contracts.TYPE_MISMATCH,
        path=_path_text(path),
        detail=f"unsupported-key:{type(key).__name__}",
    )


def _dumps(value: Any) -> str:
    """2단계 직렬화(정규화된 값 전용 — 내부 호출)."""
    return json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(",", ":"))


def _sorted_unique(items: Iterable[Any]) -> list:
    """집합 의미 collection: canonical 표현 기준 정렬 + 중복 제거."""
    seen: dict[bytes, Any] = {}
    for item in items:
        key = _sort_bytes(_dumps(item))
        if key not in seen:
            seen[key] = item
    return [seen[key] for key in sorted(seen)]


def canonical(value: Any, *, _path: tuple[str, ...] = ()) -> Any:
    """값을 canonical form으로 정규화한다(design.md 1단계).

    - ``dict``: key를 UTF-8 바이트 순으로 정렬하고, key는 JSON 문자열 key로 정규화한다.
    - 집합 의미 목록(:data:`SET_LIKE_PATHS`): canonical 표현 기준 정렬 + 중복 제거.
    - 순서 의미 목록(:data:`ORDER_BEARING_PATHS`): 원 순서 보존(집합 의미보다 우선).
    - 그 밖의 목록: 원 순서 보존. ``set``/``frozenset``은 순서가 없으므로 항상 정렬·중복 제거.
    - ``None``(``null``)은 제거하지 않고 유지한다(미확정과 미존재 구분).
    - ``str``/``int``/``float``/``bool`` 하위 타입(``StrEnum`` 등)은 기본 타입으로 환원한다.

    Args:
        value: 정규화할 임의 구조.
        _path: 내부 재귀용 경로(호출자는 지정하지 않는다).

    Returns:
        JSON 기본 타입만으로 구성된 새 구조(입력을 변경하지 않는다).

    Raises:
        MalformedEntryError: JSON으로 표현할 수 없는 값 또는 정규화 후 충돌하는 dict key.
    """
    if isinstance(value, dict):
        out: dict[str, Any] = {}
        for raw_key, raw_value in sorted(
            value.items(), key=lambda kv: _sort_bytes(_dict_key(kv[0], _path))
        ):
            key = _dict_key(raw_key, _path)
            if key in out:
                raise MalformedEntryError(
                    f"정규화 후 dict key 충돌: {key} at {_path_text(_path)}",
                    code=contracts.TYPE_MISMATCH,
                    path=_path_text(_path + (key,)),
                    detail="duplicate-key",
                )
            out[key] = canonical(raw_value, _path=_path + (key,))
        return out

    if isinstance(value, (list, tuple)):
        items = [canonical(item, _path=_path + (WILDCARD,)) for item in value]
        if is_order_bearing_path(_path):  # 순서 의미가 집합 의미보다 우선한다
            return items
        if is_set_like_path(_path):
            return _sorted_unique(items)
        return items

    if isinstance(value, (set, frozenset)):
        # 집합에는 원 순서가 없으므로 경로와 무관하게 정렬·중복 제거한다.
        return _sorted_unique(canonical(item, _path=_path + (WILDCARD,)) for item in value)

    if value is None:
        return None
    if isinstance(value, bool):
        return bool(value)
    if isinstance(value, int):
        return int(value)
    if isinstance(value, float):
        return float(value)
    if isinstance(value, str):
        return str(value)

    raise MalformedEntryError(
        f"JSON으로 표현할 수 없는 값: {type(value).__name__} at {_path_text(_path)}",
        code=contracts.TYPE_MISMATCH,
        path=_path_text(_path),
        detail=f"unsupported-type:{type(value).__name__}",
    )


# ─────────────────────────────────────────────────────────────────
# 2단계: 직렬화 / 역직렬화
# ─────────────────────────────────────────────────────────────────
def serialize(entry_or_map: Any) -> str:
    """canonical form을 결정론적 JSON 문자열로 직렬화한다(design.md 2단계).

    ``json.dumps(sort_keys=True, ensure_ascii=False, separators=(",", ":"))`` 를
    canonical form에 적용하므로 ``serialize(canonical(x)) == serialize(x)`` 가 성립한다.

    Raises:
        MalformedEntryError: 정규화 불가 값 또는 직렬화 실패(의미 손실).
    """
    normalized = canonical(entry_or_map)
    try:
        return _dumps(normalized)
    except (TypeError, ValueError) as exc:  # pragma: no cover - canonical이 선차단한다
        raise MalformedEntryError(
            f"canonical 직렬화 실패: {str(exc)[:200]}",
            code=contracts.TYPE_MISMATCH,
            detail="serialize-failed",
        ) from exc


def canonical_bytes(entry_or_map: Any) -> bytes:
    """`serialize` 결과의 UTF-8 바이트열(fingerprint 입력)."""
    return _sort_bytes(serialize(entry_or_map))


def _no_duplicate_keys(pairs: list[tuple[str, Any]]) -> dict:
    """JSON object의 중복 key를 의미 손실로 판정한다(마지막 값만 남는 손실 차단)."""
    seen: dict[str, Any] = {}
    for key, value in pairs:
        if key in seen:
            raise MalformedEntryError(
                f"JSON object 중복 key: {key}",
                code=contracts.TYPE_MISMATCH,
                path=key,
                detail="duplicate-key",
            )
        seen[key] = value
    return seen


def deserialize(text: str) -> Any:
    """canonical JSON 문자열을 의미 보존 역직렬화한다(design.md 8단계 round-trip).

    반환값은 다시 정규화된 구조이므로 ``serialize(deserialize(serialize(x))) ==
    serialize(x)`` 가 성립한다. 일반적으로 Capability_Map 또는 entry ``dict``를
    반환하지만, 최상위가 dict가 아닌 JSON도 그대로 정규화해 반환한다.

    Raises:
        MalformedEntryError: 문자열이 아님, JSON 파싱 실패, object 중복 key(의미 손실).
    """
    if not isinstance(text, str):
        raise MalformedEntryError(
            f"JSON 문자열이 필요하다: {type(text).__name__}",
            code=contracts.TYPE_MISMATCH,
            detail=f"not-str:{type(text).__name__}",
        )
    try:
        parsed = json.loads(text, object_pairs_hook=_no_duplicate_keys)
    except MalformedEntryError:
        raise
    except ValueError as exc:
        raise MalformedEntryError(
            f"JSON 파싱 실패: {str(exc)[:200]}",
            code=contracts.TYPE_MISMATCH,
            detail="parse-failed",
        ) from exc
    return canonical(parsed)


def canonical_equal(left: Any, right: Any) -> bool:
    """두 값의 canonical serialization이 동일한지(duplicate 축약 판정용).

    정규화 불가 입력은 동일하지 않은 것으로 취급한다(예외를 전파하지 않는다).
    """
    try:
        return serialize(left) == serialize(right)
    except MalformedEntryError:
        return False


# ─────────────────────────────────────────────────────────────────
# 3~5단계: Capability_Fingerprint
# ─────────────────────────────────────────────────────────────────
def _contract_view(contract: Any) -> Any:
    """계약에서 evidence 참조를 제거한 fingerprint 입력 view(4단계)."""
    if not isinstance(contract, dict):
        return None if contract is None else contract
    return {key: value for key, value in contract.items() if key not in CONTRACT_EXCLUDED_FIELDS}


def _sub_entry_view(value: Any, fields: Sequence[str]) -> Any:
    """Route_Entry/Effort_Entry에서 화이트리스트 필드만 취한다(3단계)."""
    if not isinstance(value, dict):
        return None
    view: dict[str, Any] = {}
    for field in fields:
        raw = value.get(field)
        view[field] = _contract_view(raw) if field == "contract" else raw
    return view


def capability_fingerprint_input(entry: Any) -> dict:
    """Capability_Fingerprint 입력 구조를 만든다(3·4단계 화이트리스트).

    화이트리스트 밖의 필드(``candidateLabel``, ``displayName``, UTC 시각, ``revision``,
    ``evidence``/``evidenceRecordId``, 로그, Sanitized_Schema, 계약의 ``evidenceRef``)는
    구조적으로 포함될 수 없다. 모든 Known_Route 키를 항상 채우므로 route 누락과
    ``null`` route가 같은 입력을 만든다(둘 다 미확정).
    """
    source = entry if isinstance(entry, dict) else {}
    routes = source.get("routes") if isinstance(source.get("routes"), dict) else {}
    effort = source.get("effort") if isinstance(source.get("effort"), dict) else {}
    return {
        **{field: source.get(field) for field in CAPABILITY_FINGERPRINT_ENTRY_FIELDS},
        "routes": {
            route: _sub_entry_view(routes.get(route), CAPABILITY_FINGERPRINT_ROUTE_FIELDS)
            for route in contracts.KNOWN_ROUTES
        },
        "effort": {
            route: _sub_entry_view(effort.get(route), CAPABILITY_FINGERPRINT_EFFORT_FIELDS)
            for route in contracts.KNOWN_ROUTES
        },
    }


def capability_fingerprint(entry: Any) -> str:
    """Capability_Fingerprint(``cfp1:sha256:<hex>``)를 계산한다(3~5단계).

    입력은 model identity·provider·Catalog_Fingerprint·schema version과 모든
    Known_Route 상태·계약, 모든 effort 상태·계약뿐이다. 제외 입력만 달라진 entry는
    항상 같은 값을 만든다(Requirement 3.13, Property 7).
    """
    return CAPABILITY_FINGERPRINT_PREFIX + hashlib.sha256(
        canonical_bytes(capability_fingerprint_input(entry))
    ).hexdigest()


# ─────────────────────────────────────────────────────────────────
# 6단계: Catalog_Fingerprint
# ─────────────────────────────────────────────────────────────────
def _normalize_key(key: Any) -> str:
    """키를 소문자 영숫자만 남긴 형태로 정규화한다(``Model-Id`` → ``modelid``)."""
    text = key if isinstance(key, str) else str(key)
    return "".join(char for char in text.lower() if char.isalnum())


def _catalog_model_view(record: Any) -> Any:
    """모델 record에서 identity·provider·route·capability 필드만 취한다(6단계).

    화이트리스트 결과가 비면(모르는 catalog 형태) 수집 메타데이터·로그만 제거한
    view로 폴백해, 변경 감지 능력을 잃지 않도록 한다.
    """
    if not isinstance(record, dict):
        return record
    kept = {
        key: value
        for key, value in record.items()
        if _normalize_key(key) in _CATALOG_KEPT_KEYS
    }
    if kept:
        return kept
    return {
        key: value
        for key, value in record.items()
        if _normalize_key(key) not in _CATALOG_METADATA_KEYS
    }


def catalog_model_views(catalog_snapshot: Any) -> list:
    """catalog snapshot에서 fingerprint 입력이 되는 모델 view 목록을 뽑는다.

    지원 형태: 모델 record 목록, ``{"models": [...]}`` 계열 dict,
    ``{modelId: record}`` 매핑, 단일 record dict.
    """
    if catalog_snapshot is None:
        return []
    if isinstance(catalog_snapshot, (list, tuple)):
        return [_catalog_model_view(item) for item in catalog_snapshot]

    if isinstance(catalog_snapshot, dict):
        for key, value in catalog_snapshot.items():
            if _normalize_key(key) in _CATALOG_MODEL_LIST_KEYS and isinstance(value, (list, tuple)):
                return [_catalog_model_view(item) for item in value]
        # {modelId: record} 매핑 — 키가 identity이므로 view에 보존한다.
        values = list(catalog_snapshot.values())
        if values and all(isinstance(item, dict) for item in values):
            views = []
            for key, record in catalog_snapshot.items():
                view = _catalog_model_view(record)
                if isinstance(view, dict) and "modelId" not in view:
                    view = {"modelId": key, **view}
                views.append(view)
            return views
        return [_catalog_model_view(catalog_snapshot)]

    return [catalog_snapshot]


def catalog_fingerprint_input(catalog_snapshot: Any) -> dict:
    """Catalog_Fingerprint 입력 구조(모델 view 목록만 — 수집 메타데이터 제외)."""
    return {"models": catalog_model_views(catalog_snapshot)}


def catalog_fingerprint(catalog_snapshot: Any) -> str:
    """Catalog_Fingerprint(``cat1:sha256:<hex>``)를 계산한다(6단계).

    ``models``는 집합 의미이므로 catalog 응답 순서를 바꿔도 값이 변하지 않는다.
    """
    return CATALOG_FINGERPRINT_PREFIX + hashlib.sha256(
        canonical_bytes(catalog_fingerprint_input(catalog_snapshot))
    ).hexdigest()


# ─────────────────────────────────────────────────────────────────
# 7단계: Evidence_Record_ID
# ─────────────────────────────────────────────────────────────────
def _results_view(items: Any, fields: Sequence[str]) -> Any:
    """판정 결과 목록에서 화이트리스트 필드만 취한다(probeId·Sanitized_Schema 제외)."""
    if not isinstance(items, (list, tuple)):
        return None
    views = []
    for item in items:
        if isinstance(item, dict):
            views.append({field: item.get(field) for field in fields})
        else:
            views.append(None)
    return views


def evidence_record_id_input(record: Any) -> dict:
    """Evidence_Record_ID 입력 구조를 만든다(7단계 화이트리스트).

    ``candidateLabel``·``interpreterPath``·``usage``·``cost``·``routeCompleteness``·
    ``probeId``·Sanitized_Schema·``evidenceRecordId`` 자기참조는 포함하지 않는다.
    """
    source = record if isinstance(record, dict) else {}
    environment = source.get("environment")
    return {
        **{field: source.get(field) for field in EVIDENCE_ID_RECORD_FIELDS},
        "environment": (
            {field: environment.get(field) for field in contracts.REQUIRED_ENVIRONMENT_FIELDS}
            if isinstance(environment, dict)
            else None
        ),
        "routeResults": _results_view(source.get("routeResults"), EVIDENCE_ID_ROUTE_RESULT_FIELDS),
        "effortResults": _results_view(source.get("effortResults"), EVIDENCE_ID_EFFORT_RESULT_FIELDS),
    }


def evidence_record_id(record: Any) -> str:
    """Evidence_Record_ID(``evr1:sha256:<hex>``)를 계산한다(7단계).

    정제 Verification_Record의 불변 필드만 입력으로 쓰므로, 같은 판정 결과는 저장
    위치·로그·Sanitized_Schema와 무관하게 같은 ID를 만든다.
    """
    return EVIDENCE_RECORD_ID_PREFIX + hashlib.sha256(
        canonical_bytes(evidence_record_id_input(record))
    ).hexdigest()


__all__ = [
    "CAPABILITY_FINGERPRINT_ENTRY_FIELDS",
    "CAPABILITY_FINGERPRINT_EFFORT_FIELDS",
    "CAPABILITY_FINGERPRINT_EXCLUDED_FIELDS",
    "CAPABILITY_FINGERPRINT_PREFIX",
    "CAPABILITY_FINGERPRINT_ROUTE_FIELDS",
    "CATALOG_FINGERPRINT_PREFIX",
    "CONTRACT_EXCLUDED_FIELDS",
    "EVIDENCE_ID_EFFORT_RESULT_FIELDS",
    "EVIDENCE_ID_RECORD_FIELDS",
    "EVIDENCE_ID_ROUTE_RESULT_FIELDS",
    "EVIDENCE_RECORD_ID_PREFIX",
    "MalformedEntryError",
    "ORDER_BEARING_PATHS",
    "PATH_SEPARATOR",
    "SET_LIKE_PATHS",
    "WILDCARD",
    "canonical",
    "canonical_bytes",
    "canonical_equal",
    "capability_fingerprint",
    "capability_fingerprint_input",
    "catalog_fingerprint",
    "catalog_fingerprint_input",
    "catalog_model_views",
    "deserialize",
    "evidence_record_id",
    "evidence_record_id_input",
    "is_order_bearing_path",
    "is_set_like_path",
    "serialize",
]
