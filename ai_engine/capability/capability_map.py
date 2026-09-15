"""Capability_Map — 로드·갱신·활성 evidence 선택·상태 전이.

이 모듈은 Capability_Map 파일(`{schemaVersion, updatedAt, entries}`)을 읽고, 정제
Verification_Record를 반영하고, 파생 모드 상태와 Capability_Fingerprint를 재계산하며,
STALE 전이 트리거를 적용한다.

**값 추론 금지** — 특정 model ID·provider·route 지원 여부·effort field path·effort
허용값은 이 모듈에 존재하지 않는다. 전부 Verification_Record(그리고 collector가 함께
넘기는 계약)가 채우는 자리이며, Candidate_Label·Provider_String 문자열에서 유도하지
않는다. route의 execution mode도 Route_Contract(또는 명시적 `mode_hints`)만으로
판정하고 route key 문자열에서 추측하지 않는다.

책임 경계:
  - Malformed 판정·계약 완전성 규칙 → :mod:`.contracts`
  - canonical 정규화·fingerprint 계산 → :mod:`.canonicalizer`
  - 디스크 접근(경로 가드·정제·원자적 쓰기) → :mod:`.store`
  - Active_Model **판정**·duplicate 축약·tie 규칙 → :mod:`.activation_gate` (이 모듈 아님)
  - Active_Model **투영**(`/api/models` 카탈로그 병합·UI payload) → 이 모듈 마지막 절
    (:func:`merge_active_into_catalog`, :func:`to_ui_payload`). 판정을 다시 하지 않고
    activation_gate가 통과시킨 entry만 형태 변환한다.

핵심 규칙:

1. **활성 evidence 선택**(Requirement 3.18, 3.19) — 동일 Exact_Model_ID·동일
   Capability_Fingerprint의 evidence가 복수면 가장 늦은 `verifiedAt`, 동시각이면
   Evidence_Record_ID 오름차순 첫 record가 활성이다. :func:`select_active_evidence`가
   이 규칙을 구현하고, :func:`upsert`는 같은 규칙으로 "이 record가 현재 활성 evidence를
   대체하는가"를 판정한다. 대체하지 못하는 record는 `evidence` 목록에만 기록되고
   상태·계약을 덮어쓰지 않으므로, **어떤 순서로 upsert해도 최종 상태가 같다**(Property 7).

2. **파생 모드 상태**(Requirement 3.20~3.23) — `syncSupport`/`asyncSupport`/
   `streamingSupport`는 execution mode별 route 상태에서 `SUPPORTED > UNVERIFIED >
   UNSUPPORTED > NOT_ADVERTISED` 우선순위로 유도한다(:data:`MODE_PRIORITY`). 어떤
   route도 그 mode에 귀속되지 않으면 미확정(`UNVERIFIED`)이다.

3. **`SUPPORTED` 요구조건**(Requirement 3.3, 3.4, 3.6, 3.7) — route·effort entry가
   `SUPPORTED`로 기록되려면 완전한 계약과 Current_Evidence reference가 모두 있어야
   한다. 하나라도 없으면 :func:`upsert`가 `UNVERIFIED`로 낮춘다(추측으로 계약을
   채우지 않는다).

4. **STALE 전이 트리거**(Requirement 6.15~6.21) — catalog에서 Exact_Model_ID 제거,
   Provider_String 변경, Route_Contract 변경, Effort_Contract 변경, evidence revision ≠
   Current_Revision. 재검증이 현재 production probe를 모두 통과하면 새
   Capability_Fingerprint와 UTC 시각으로 `VERIFIED`로 갱신하고, 미완료면 `STALE`을
   유지한다. 명시적 allowlist 거부로 `REJECTED`가 된 entry는 STALE 전이가 덮어쓰지
   않는다(더 강한 거부 결정을 보존한다).

진단용 선택 필드(스키마 필수 필드가 아니며 fingerprint 입력에서 구조적으로 제외된다):
  ``activeEvidenceRef`` 활성 evidence의 Evidence_Record_ID(동시각 tie 판정 상태)
  ``staleReason``       마지막 STALE 전이 이유 코드

참조: .kiro/specs/gateway-models-effort-support/design.md
  - "Components and Interfaces" 3절 (Capability_Map / Capability_Canonicalizer)
  - "Components and Interfaces" 4절 (Activation_Gate → 서버 노출 seam)
  - "Data Models" → entry·Route_Contract·Effort_Contract·Verification_Record 표
  - "영속 경로 (userData 하위 한정)"
Requirements: 1.13, 3.3, 3.4, 3.6, 3.7, 3.17, 3.18, 3.19, 3.20, 3.21, 3.22, 3.23,
              6.14, 6.15, 6.16, 6.17, 6.18, 6.19, 6.20, 6.21, 6.26, 12.1, 12.2
"""
from __future__ import annotations

import copy
from datetime import datetime
from typing import Any, Iterable, Mapping, Sequence

from . import canonicalizer, contracts, store

#: Capability_Map 파일 스키마 버전(entry 스키마와 동일 버전을 사용한다).
SCHEMA_VERSION = contracts.SCHEMA_VERSION

#: Capability_Map 파일의 최상위 키(design.md 영속 경로 표).
MAP_KEYS: tuple[str, ...] = ("schemaVersion", "updatedAt", "entries")

#: 파생 모드 상태 필드 이름.
DERIVED_MODE_FIELDS: tuple[str, ...] = ("syncSupport", "asyncSupport", "streamingSupport")

#: execution mode → 파생 모드 상태 필드.
MODE_FIELD_BY_EXECUTION_MODE: dict[str, str] = {
    str(contracts.Execution_Mode.SYNC): "syncSupport",
    str(contracts.Execution_Mode.ASYNC): "asyncSupport",
    str(contracts.Execution_Mode.STREAMING): "streamingSupport",
}

#: 모드 유도 우선순위 — Requirement 3.23(첫 일치가 승리).
MODE_PRIORITY: tuple[str, ...] = (
    str(contracts.Route_Support_Status.SUPPORTED),
    str(contracts.Route_Support_Status.UNVERIFIED),
    str(contracts.Route_Support_Status.UNSUPPORTED),
    str(contracts.Route_Support_Status.NOT_ADVERTISED),
)

#: 어떤 route도 해당 mode에 귀속되지 않을 때의 파생 상태(미확정).
DEFAULT_MODE_SUPPORT: str = str(contracts.Route_Support_Status.UNVERIFIED)

#: 같은 route에 복수 routeResult가 있을 때의 집계 우선순위(보수적 우선 — 첫 일치가 승리).
#: `routeResults`는 집합 의미이므로 목록 순서에 의존하지 않는 판정이 필요하다.
ROUTE_AGGREGATION_PRIORITY: tuple[str, ...] = (
    str(contracts.Route_Support_Status.UNSUPPORTED),
    str(contracts.Route_Support_Status.NOT_ADVERTISED),
    str(contracts.Route_Support_Status.UNVERIFIED),
    str(contracts.Route_Support_Status.SUPPORTED),
)

#: 같은 route에 복수 effortResult(enum value별 probe 등)가 있을 때의 집계 우선순위.
#: `SUPPORTED`는 모든 결과가 `SUPPORTED`일 때만 남는다(부분 검증 → `UNVERIFIED`).
EFFORT_AGGREGATION_PRIORITY: tuple[str, ...] = (
    str(contracts.Effort_Support_Status.UNSUPPORTED),
    str(contracts.Effort_Support_Status.STALE),
    str(contracts.Effort_Support_Status.UNVERIFIED),
    str(contracts.Effort_Support_Status.SUPPORTED),
)

# ── STALE 전이 이유 코드(닫힌 집합) — Requirement 6.15~6.19, 6.21 ──────────
STALE_CATALOG_MODEL_REMOVED = "CATALOG_MODEL_REMOVED"
STALE_PROVIDER_CHANGED = "PROVIDER_CHANGED"
STALE_ROUTE_CONTRACT_CHANGED = "ROUTE_CONTRACT_CHANGED"
STALE_EFFORT_CONTRACT_CHANGED = "EFFORT_CONTRACT_CHANGED"
STALE_REVISION_MISMATCH = "REVISION_MISMATCH"
STALE_REVERIFICATION_INCOMPLETE = "REVERIFICATION_INCOMPLETE"

STALE_REASONS: tuple[str, ...] = (
    STALE_CATALOG_MODEL_REMOVED,
    STALE_PROVIDER_CHANGED,
    STALE_ROUTE_CONTRACT_CHANGED,
    STALE_EFFORT_CONTRACT_CHANGED,
    STALE_REVISION_MISMATCH,
    STALE_REVERIFICATION_INCOMPLETE,
)

# ── 승격 차단 이유 코드(닫힌 집합) — 보고서·탈락 이유용 ────────────────────
BLOCK_MALFORMED = "MALFORMED_ENTRY"
BLOCK_INCOMPLETE_RECORD = "INCOMPLETE_RECORD"
BLOCK_EMPTY_MODEL_ID = "EMPTY_MODEL_ID"
BLOCK_EMPTY_PROVIDER = "EMPTY_PROVIDER"
BLOCK_NO_SUPPORTED_ROUTE = "NO_SUPPORTED_ROUTE"
BLOCK_REVISION_UNKNOWN = "REVISION_UNKNOWN"
BLOCK_REVISION_MISMATCH = "REVISION_MISMATCH"
BLOCK_CATALOG_FINGERPRINT_UNKNOWN = "CATALOG_FINGERPRINT_UNKNOWN"
BLOCK_CATALOG_FINGERPRINT_MISMATCH = "CATALOG_FINGERPRINT_MISMATCH"
BLOCK_CATALOG_MODEL_ABSENT = "CATALOG_MODEL_ABSENT"
BLOCK_PROVIDER_MISMATCH = "PROVIDER_MISMATCH"
BLOCK_ENVIRONMENT_UNKNOWN = "ENVIRONMENT_UNKNOWN"
BLOCK_ENVIRONMENT_MISMATCH = "ENVIRONMENT_MISMATCH"

PROMOTION_BLOCKERS: tuple[str, ...] = (
    BLOCK_MALFORMED,
    BLOCK_INCOMPLETE_RECORD,
    BLOCK_EMPTY_MODEL_ID,
    BLOCK_EMPTY_PROVIDER,
    BLOCK_NO_SUPPORTED_ROUTE,
    BLOCK_REVISION_UNKNOWN,
    BLOCK_REVISION_MISMATCH,
    BLOCK_CATALOG_FINGERPRINT_UNKNOWN,
    BLOCK_CATALOG_FINGERPRINT_MISMATCH,
    BLOCK_CATALOG_MODEL_ABSENT,
    BLOCK_PROVIDER_MISMATCH,
    BLOCK_ENVIRONMENT_UNKNOWN,
    BLOCK_ENVIRONMENT_MISMATCH,
)

#: 진단 문자열 최대 길이(프로젝트 로깅 관례와 동일하게 200자 절단).
_REASON_MAX = 200


# ─────────────────────────────────────────────────────────────────
# 예외
# ─────────────────────────────────────────────────────────────────
class CapabilityMapError(Exception):
    """Capability_Map 갱신 계약 위반."""


class InvalidVerificationRecordError(CapabilityMapError):
    """Verification_Record가 스키마를 만족하지 않아 반영할 수 없다.

    ``reasons``는 ``contracts`` 판정 이유(``CODE:path(detail)``) 목록이다.
    """

    def __init__(self, reasons: Sequence[str]) -> None:
        self.reasons = list(reasons)
        super().__init__("Verification_Record 스키마 위반: " + ", ".join(self.reasons)[:_REASON_MAX])


# ─────────────────────────────────────────────────────────────────
# 작은 술어·유틸
# ─────────────────────────────────────────────────────────────────
def _is_dict(value: Any) -> bool:
    return isinstance(value, dict)


def _text(value: Any) -> str:
    """문자열 필드를 안전하게 읽는다(문자열이 아니면 미확정으로 취급)."""
    return value if isinstance(value, str) else contracts.UNDETERMINED


def _truncate(text: Any) -> str:
    out = text if isinstance(text, str) else str(text)
    return out[:_REASON_MAX]


def _instant(value: Any) -> float | None:
    """UTC ISO 8601 문자열을 epoch 초로 바꾼다(형식 위반·빈 문자열은 ``None``)."""
    if not contracts.is_utc_iso8601(value):
        return None
    try:
        return datetime.fromisoformat(value).timestamp()
    except ValueError:  # pragma: no cover - is_utc_iso8601이 선차단한다
        return None


def _recency_rank(verified_at: Any) -> tuple[int, float]:
    """`verifiedAt` 비교 키. 형식 위반·미확정은 항상 확정 시각보다 낮다."""
    moment = _instant(verified_at)
    return (1, moment) if moment is not None else (0, 0.0)


def _evidence_id_of(record: Any) -> str:
    return _text(record.get("evidenceRecordId")) if _is_dict(record) else contracts.UNDETERMINED


def _merge_set_like(existing: Any, additions: Iterable[Any]) -> list:
    """집합 의미 목록을 canonical 표현 기준으로 정렬·중복 제거해 병합한다.

    canonicalizer가 fingerprint 계산 시 적용하는 정렬과 같은 기준을 저장 시점에도
    쓰므로, 저장 바이트열이 canonical 순서와 어긋나지 않는다. 직렬화 불가 값은
    의미 손실을 만들지 않도록 버린다.
    """
    seen: dict[bytes, Any] = {}
    source = list(existing) if isinstance(existing, (list, tuple)) else []
    for item in source + list(additions or []):
        try:
            key = canonicalizer.serialize(item).encode("utf-8", "surrogatepass")
        except canonicalizer.MalformedEntryError:
            continue
        seen.setdefault(key, item)
    return [seen[key] for key in sorted(seen)]


def _canonical_equal(left: Any, right: Any) -> bool:
    return canonicalizer.canonical_equal(left, right)


# ─────────────────────────────────────────────────────────────────
# map 형식 정규화 · 로드 · 저장
# ─────────────────────────────────────────────────────────────────
def new_map(*, now: str | None = None, entries: Iterable[dict] | None = None) -> dict:
    """빈 Capability_Map(`{schemaVersion, updatedAt, entries}`)을 만든다."""
    return {
        "schemaVersion": SCHEMA_VERSION,
        "updatedAt": now if isinstance(now, str) and now else contracts.utc_now_iso(),
        "entries": [copy.deepcopy(entry) for entry in (entries or [])],
    }


def normalize_map(raw: Any, *, now: str | None = None) -> dict:
    """임의 입력을 Capability_Map 형식으로 정규화한다(entry 내용은 보존).

    - 최상위가 dict가 아니거나 `schemaVersion`이 미지원이면 **빈 map**을 반환한다.
      (알 수 없는 형식은 Managed_Segment를 비워 기준선 동작을 유지한다.)
    - `entries`가 목록이 아니면 빈 목록으로 둔다.
    - entry는 검증하거나 수정하지 않는다. Malformed 판정은 :func:`valid_entries`가
      담당하므로, 손상 entry도 그대로 실려 감사 대상으로 남는다.
    """
    if not _is_dict(raw):
        return new_map(now=now)

    version = raw.get("schemaVersion")
    if isinstance(version, bool) or not isinstance(version, int):
        version = SCHEMA_VERSION
    elif version not in contracts.SUPPORTED_SCHEMA_VERSIONS:
        return new_map(now=now)

    entries = raw.get("entries")
    updated_at = raw.get("updatedAt")
    return {
        "schemaVersion": version,
        "updatedAt": updated_at if isinstance(updated_at, str) else contracts.UNDETERMINED,
        "entries": copy.deepcopy(entries) if isinstance(entries, list) else [],
    }


def _store_for(user_data_root: Any, env: Any, store_obj: Any) -> store.CapabilityStore:
    return store_obj if store_obj is not None else store.CapabilityStore(user_data_root, env)


def load(
    user_data_root: Any = None,
    *,
    env: Any = None,
    store_obj: store.CapabilityStore | None = None,
) -> dict:
    """`userData/capability/capability_map.json`을 읽어 정규화된 map을 반환한다.

    파일 부재·JSON 손상·루트 밖 경로 등 어떤 읽기 실패도 예외로 전파하지 않고 빈
    map을 반환한다(server의 `/api/models` 병합 seam이 기준선 동작을 유지하도록).
    """
    st = _store_for(user_data_root, env, store_obj)
    raw = st.read_json_safe(st.capability_map_path(), default=None)
    return normalize_map(raw)


def save(
    map_obj: dict,
    user_data_root: Any = None,
    *,
    env: Any = None,
    store_obj: store.CapabilityStore | None = None,
    sanitize: bool = True,
    now: str | None = None,
):
    """Capability_Map을 `userData/capability/capability_map.json`에 원자적으로 기록한다.

    `updatedAt`이 비어 있으면 현재 UTC 시각으로 채운다(기존 값은 보존해 저장이
    바이트를 흔들지 않게 한다). 반환값은 기록된 절대 경로다.
    """
    st = _store_for(user_data_root, env, store_obj)
    payload = normalize_map(map_obj, now=now)
    if not payload["updatedAt"]:
        payload["updatedAt"] = now if isinstance(now, str) and now else contracts.utc_now_iso()
    return st.write_capability_map(payload, sanitize=sanitize)


def entries_of(map_obj: Any) -> list:
    """map의 entry 목록을 반환한다(형식 위반은 빈 목록)."""
    if not _is_dict(map_obj):
        return []
    entries = map_obj.get("entries")
    return entries if isinstance(entries, list) else []


def find_entry(
    map_obj: Any,
    *,
    candidate_label: str | None = None,
    model_id: str | None = None,
) -> dict | None:
    """`candidateLabel`(우선) 또는 `modelId`로 entry를 찾는다(없으면 ``None``)."""
    index = _find_index(entries_of(map_obj), candidate_label or "", model_id or "")
    return None if index is None else entries_of(map_obj)[index]


def invocation_model_ids_of(entry: Any) -> list[str]:
    """entry에 **기록된** Invocation_Model_ID 목록(빈 값·비문자열 제외).

    이 목록은 성공 probe가 실제로 전송한 ID만 담는다(Requirement 2.17). 접두사를
    붙이거나 떼서 후보를 만들지 않으므로, 여기 없는 ID는 이 entry의 것이 아니다.
    """
    if not _is_dict(entry):
        return []
    observed = entry.get("invocationModelIds")
    if not isinstance(observed, list):
        return []
    return [item for item in observed if isinstance(item, str) and item]


def find_entry_by_model_id(entries: Any, model_id: Any) -> dict | None:
    """전송 ID로 entry를 찾는다 — Exact_Model_ID 우선, 없으면 Invocation_Model_ID 폴백.

    Capability_Map은 Exact_Model_ID(catalog 원문)로 키를 잡고, 실제 전송에 쓰인 ID는
    `invocationModelIds`에 별도로 기록한다(Requirement 2.17). 요청 경로는 production
    prefix 해석을 거친 ID(예: `_resolve_callable_model_id` 결과)를 들고 오는 자리가
    있으므로, Exact_Model_ID 조회가 실패하면 **기록된** Invocation_Model_ID와의 문자
    일치로 한 번 더 찾는다.

    Args:
        entries: Capability_Map(dict) 또는 entry 목록(Active_Model 목록 포함).
        model_id: 조회 키(Exact_Model_ID 또는 Invocation_Model_ID).

    Returns:
        일치한 entry 또는 ``None``.

    결정론 규칙(값 추론 금지):
      - 폴백은 **정확 일치**만 한다. 접두사를 붙이거나 떼서 추측하지 않는다.
      - 같은 Invocation_Model_ID를 서로 다른 entry가 주장하면 모호하므로 폴백하지
        않는다(``None``). 어느 쪽을 고르든 임의 선택이 되기 때문이다.
      - Exact_Model_ID 일치가 항상 우선한다(bare ID가 다른 entry의 invocation 목록에
        들어 있어도 bare ID 소유 entry가 이긴다).
    """
    items = entries_of(entries) if _is_dict(entries) else (
        entries if isinstance(entries, (list, tuple)) else []
    )
    wanted = _text(model_id)
    if not wanted:
        return None

    for entry in items:
        if _is_dict(entry) and _text(entry.get("modelId")) == wanted:
            return entry

    matched = [
        entry
        for entry in items
        if _is_dict(entry) and wanted in invocation_model_ids_of(entry)
    ]
    if len(matched) != 1:
        return None  # 0건은 미발견, 2건 이상은 모호 → 폴백하지 않는다
    return matched[0]


def _find_index(entries: Sequence[Any], label: str, model_id: str) -> int | None:
    """entry 위치를 찾는다. entry 키는 `candidateLabel`, 없으면 `modelId`다.

    라벨 단위로만 매칭하므로 한 라벨의 evidence가 다른 라벨 entry에 섞이지 않는다
    (라벨 간 provider·model family·route·effort 전파 차단).
    """
    for index, entry in enumerate(entries):
        if not _is_dict(entry):
            continue
        entry_label = _text(entry.get("candidateLabel"))
        if label:
            if entry_label == label:
                return index
            continue
        if not entry_label and model_id and _text(entry.get("modelId")) == model_id:
            return index
    return None


# ─────────────────────────────────────────────────────────────────
# Malformed 분리 (Requirement 3.17)
# ─────────────────────────────────────────────────────────────────
def entry_malformed_reasons(entry: Any, *, known_evidence_ids: Iterable[str] | None = None) -> list[str]:
    """entry의 Malformed 판정 이유 목록(빈 목록이면 Malformed_Entry가 아니다).

    fingerprint 재계산 비교를 위해 canonicalizer를 주입한다.
    """
    return contracts.validate_entry(
        entry,
        fingerprint_fn=canonicalizer.capability_fingerprint,
        known_evidence_ids=known_evidence_ids,
    )


def classify_entries(
    map_obj: Any,
    *,
    known_evidence_ids: Iterable[str] | None = None,
) -> tuple[list[dict], list[tuple[Any, list[str]]]]:
    """(유효 entry, [(Malformed entry, 이유 목록)])로 분리한다."""
    valid: list[dict] = []
    malformed: list[tuple[Any, list[str]]] = []
    for entry in entries_of(map_obj):
        reasons = entry_malformed_reasons(entry, known_evidence_ids=known_evidence_ids)
        if reasons:
            malformed.append((entry, reasons))
        else:
            valid.append(entry)
    return valid, malformed


def valid_entries(
    map_obj: Any,
    *,
    known_evidence_ids: Iterable[str] | None = None,
) -> tuple[list, list]:
    """(유효 entry, Malformed_Entry) 분리 — Malformed는 activation 입력에서 제외한다."""
    valid, malformed = classify_entries(map_obj, known_evidence_ids=known_evidence_ids)
    return valid, [entry for entry, _ in malformed]


# ─────────────────────────────────────────────────────────────────
# 파생 모드 상태 (Requirement 3.20~3.23)
# ─────────────────────────────────────────────────────────────────
def route_execution_mode(
    route_entry: Any,
    *,
    route_key: str | None = None,
    mode_hints: Mapping[str, str] | None = None,
) -> str | None:
    """route의 execution mode를 반환한다(미확정은 ``None``).

    판정 순서: Route_Contract의 `executionMode` → 호출자가 준 `mode_hints` →
    :data:`contracts.DEFAULT_ROUTE_EXECUTION_MODES`(Known_Route 정의값).

    계약이 없는 route(상태만 기록된 `NOT_ADVERTISED`/`UNVERIFIED` 등)도 마지막 단계
    덕분에 mode가 확정되므로, **`mode_hints`를 주든 안 주든 유도 결과가 같다**. write
    시점(collector가 hints를 넘긴다)과 read 시점(server seam은 넘기지 않는다)이 갈리면
    파생 모드 상태가 어긋나 Complete_Record 판정이 실패한다 — 그 갈림을 이 함수가
    제거한다. route key 문자열에서 mode를 추측하지는 않는다(닫힌 매핑 조회뿐).
    """
    contract = route_entry.get("contract") if _is_dict(route_entry) else None
    mode = contract.get("executionMode") if _is_dict(contract) else None
    if contracts.Execution_Mode.has(mode):
        return str(mode)
    if route_key is None:
        return None
    if mode_hints:
        hint = mode_hints.get(route_key)
        if contracts.Execution_Mode.has(hint):
            return str(hint)
    return contracts.default_execution_mode(route_key)


def _highest_status(statuses: Sequence[str], priority: Sequence[str], default: str) -> str:
    for candidate in priority:
        if candidate in statuses:
            return str(candidate)
    return default


def derive_mode_support(
    routes: Any,
    *,
    mode_hints: Mapping[str, str] | None = None,
) -> dict:
    """`syncSupport`/`asyncSupport`/`streamingSupport`를 route 상태에서 유도한다.

    execution mode가 확정된 Known_Route의 상태만 집계하고, 같은 mode에 여러 route가
    귀속되면 :data:`MODE_PRIORITY`(`SUPPORTED > UNVERIFIED > UNSUPPORTED >
    NOT_ADVERTISED`) 첫 일치를 취한다. 그 mode에 귀속된 route가 없으면
    :data:`DEFAULT_MODE_SUPPORT`(미확정)이다.

    mode는 :func:`route_execution_mode`가 정하고 모든 Known_Route에 기본값이 있으므로
    `mode_hints`를 주지 않아도 결과가 같다(write·read 유도 일치).
    """
    container = routes if _is_dict(routes) else {}
    buckets: dict[str, list[str]] = {mode: [] for mode in MODE_FIELD_BY_EXECUTION_MODE}
    for route_key in contracts.KNOWN_ROUTES:
        route_entry = container.get(route_key)
        if not _is_dict(route_entry):
            continue
        status = route_entry.get("status")
        if not contracts.Route_Support_Status.has(status):
            continue  # enum 이탈은 Malformed 판정이 담당한다(여기서는 집계 제외)
        mode = route_execution_mode(route_entry, route_key=route_key, mode_hints=mode_hints)
        if mode is None:
            continue
        buckets[mode].append(str(status))
    return {
        field: _highest_status(buckets[mode], MODE_PRIORITY, DEFAULT_MODE_SUPPORT)
        for mode, field in MODE_FIELD_BY_EXECUTION_MODE.items()
    }


def apply_mode_support(entry: dict, *, mode_hints: Mapping[str, str] | None = None) -> dict:
    """entry의 파생 모드 상태를 routes에서 다시 계산해 기록한다(entry를 변경한다)."""
    entry.update(derive_mode_support(entry.get("routes"), mode_hints=mode_hints))
    return entry


def recompute_fingerprint(entry: dict) -> str:
    """entry의 Capability_Fingerprint를 재계산해 기록하고 그 값을 반환한다."""
    fingerprint = canonicalizer.capability_fingerprint(entry)
    entry["capabilityFingerprint"] = fingerprint
    return fingerprint


# ─────────────────────────────────────────────────────────────────
# Complete_Record
# ─────────────────────────────────────────────────────────────────
def complete_record_reasons(
    entry: Any,
    *,
    known_evidence_ids: Iterable[str] | None = None,
    mode_hints: Mapping[str, str] | None = None,
) -> list[str]:
    """Complete_Record가 아닌 이유 목록(빈 목록이면 Complete_Record).

    조건: Malformed_Entry가 아님(필수 schema·타입·enum·fingerprint 일치·evidence 참조
    무결성 + `SUPPORTED` route·effort의 완전한 계약과 Current_Evidence reference) AND
    모든 Known_Route 키가 `routes`·`effort`에 존재 AND 파생 모드 상태가 routes에서
    유도한 값과 일치.
    """
    reasons = list(entry_malformed_reasons(entry, known_evidence_ids=known_evidence_ids))
    if not _is_dict(entry):
        return reasons

    for field in ("routes", "effort"):
        container = entry.get(field)
        if not _is_dict(container):
            continue  # 타입 불일치는 이미 Malformed 이유에 포함된다
        for route_key in contracts.KNOWN_ROUTES:
            if route_key not in container:
                reasons.append(f"{contracts.MISSING_FIELD}:{field}.{route_key}(complete-record)")

    derived = derive_mode_support(entry.get("routes"), mode_hints=mode_hints)
    for field, expected in derived.items():
        if _text(entry.get(field)) != expected:
            reasons.append(f"{contracts.ENUM_VIOLATION}:{field}(derived-mismatch)")
    return sorted(set(reasons))


def is_complete_record(
    entry: Any,
    *,
    known_evidence_ids: Iterable[str] | None = None,
    mode_hints: Mapping[str, str] | None = None,
) -> bool:
    """Complete_Record 여부."""
    return not complete_record_reasons(
        entry, known_evidence_ids=known_evidence_ids, mode_hints=mode_hints
    )


def supported_routes(entry: Any) -> list[str]:
    """`SUPPORTED` route key 목록(Known_Route 순서)."""
    routes = entry.get("routes") if _is_dict(entry) else None
    container = routes if _is_dict(routes) else {}
    out = []
    for route_key in contracts.KNOWN_ROUTES:
        route_entry = container.get(route_key)
        if _is_dict(route_entry) and route_entry.get("status") == contracts.Route_Support_Status.SUPPORTED:
            out.append(route_key)
    return out


# ─────────────────────────────────────────────────────────────────
# 활성 evidence 선택 (Requirement 3.18, 3.19)
# ─────────────────────────────────────────────────────────────────
def select_active_evidence(
    records: Iterable[Any],
    *,
    model_id: str | None = None,
    capability_fingerprint: str | None = None,
) -> dict | None:
    """활성 evidence record를 선택한다(없으면 ``None``).

    `model_id`·`capability_fingerprint`를 주면 동일 Exact_Model_ID·동일
    Capability_Fingerprint record만 후보로 둔다. 후보 중 가장 늦은 `verifiedAt`,
    동시각이면 Evidence_Record_ID 오름차순 첫 record를 고른다. 입력 순서에
    의존하지 않는다(안정 정렬 2단계).
    """
    candidates = [record for record in records if _is_dict(record)]
    if model_id is not None:
        candidates = [r for r in candidates if _text(r.get("modelId")) == model_id]
    if capability_fingerprint is not None:
        candidates = [r for r in candidates if _text(r.get("capabilityFingerprint")) == capability_fingerprint]
    if not candidates:
        return None
    candidates.sort(key=_evidence_id_of)  # 1차: Evidence_Record_ID 오름차순
    candidates.sort(key=lambda r: _recency_rank(r.get("verifiedAt")), reverse=True)  # 2차: 최신 우선
    return candidates[0]


def _record_supersedes(entry: Any, record: Mapping[str, Any]) -> bool:
    """record가 entry의 현재 활성 evidence를 대체하는지 판정한다.

    :func:`select_active_evidence`와 동일한 규칙(최신 `verifiedAt` → 동시각은
    Evidence_Record_ID 오름차순)을 두 record 비교로 적용한다. 이 판정 덕분에
    upsert 순서와 무관하게 최종 상태가 같아진다.
    """
    current_at = _text(entry.get("verifiedAt")) if _is_dict(entry) else contracts.UNDETERMINED
    if not current_at:
        return True  # 활성 evidence가 없다
    incoming_rank = _recency_rank(record.get("verifiedAt"))
    current_rank = _recency_rank(current_at)
    if incoming_rank != current_rank:
        return incoming_rank > current_rank
    current_ref = _text(entry.get("activeEvidenceRef"))
    if not current_ref:
        return True  # 활성 record ID를 모르면 최신 record를 채택한다
    return _evidence_id_of(record) < current_ref


# ─────────────────────────────────────────────────────────────────
# 승격 판정
# ─────────────────────────────────────────────────────────────────
def _environment_blockers(record: Any, ctx: Mapping[str, Any] | None) -> list[str]:
    """Same_Gateway_Environment 판정 — record 환경이 비었거나 ctx와 다르면 차단."""
    environment = record.get("environment") if _is_dict(record) else None
    if not _is_dict(environment):
        return [BLOCK_ENVIRONMENT_UNKNOWN]
    if any(not _text(environment.get(field)) for field in contracts.REQUIRED_ENVIRONMENT_FIELDS):
        return [BLOCK_ENVIRONMENT_UNKNOWN]
    expected = (ctx or {}).get("environment")
    if _is_dict(expected):
        for field in contracts.REQUIRED_ENVIRONMENT_FIELDS:
            wanted = _text(expected.get(field))
            if wanted and _text(environment.get(field)) != wanted:
                return [BLOCK_ENVIRONMENT_MISMATCH]
    return []


def promotion_blockers(
    entry: Any,
    *,
    record: Any = None,
    ctx: Mapping[str, Any] | None = None,
    known_evidence_ids: Iterable[str] | None = None,
    mode_hints: Mapping[str, str] | None = None,
) -> list[str]:
    """`VERIFIED` 승격을 막는 이유 코드 목록(빈 목록이면 승격 가능).

    승격 조건(design "승격"): Exact_Model_ID·Provider_String이 채워지고 `SUPPORTED`
    route가 1개 이상이며 Complete_Record가 Current_Evidence(Same_Gateway_Environment ·
    Current_Revision · 현재 Catalog_Fingerprint · 현재 Capability_Fingerprint)와
    일치할 때만 승격한다.

    `ctx` 키(모두 선택): ``revision``, ``catalogFingerprint``, ``catalogModelIds``,
    ``catalogProviders``, ``environment``. 주지 않은 항목은 검사를 건너뛰지만,
    `revision`·`catalogFingerprint`·환경 identity는 **비어 있으면 승격을 막는다**
    (미확정 evidence를 현재로 간주하지 않는다).
    """
    blockers: list[str] = []
    if complete_record_reasons(entry, known_evidence_ids=known_evidence_ids, mode_hints=mode_hints):
        blockers.append(BLOCK_INCOMPLETE_RECORD)
    if not _is_dict(entry):
        return sorted(set(blockers + [BLOCK_MALFORMED]))

    model_id = _text(entry.get("modelId"))
    provider = _text(entry.get("provider"))
    if not model_id:
        blockers.append(BLOCK_EMPTY_MODEL_ID)
    if not provider:
        blockers.append(BLOCK_EMPTY_PROVIDER)
    if not supported_routes(entry):
        blockers.append(BLOCK_NO_SUPPORTED_ROUTE)

    ctx = ctx or {}

    revision = _text(entry.get("revision"))
    expected_revision = _text(ctx.get("revision"))
    if not revision:
        blockers.append(BLOCK_REVISION_UNKNOWN)
    elif expected_revision and revision != expected_revision:
        blockers.append(BLOCK_REVISION_MISMATCH)

    catalog_fp = _text(entry.get("catalogFingerprint"))
    expected_catalog_fp = _text(ctx.get("catalogFingerprint"))
    if not catalog_fp:
        blockers.append(BLOCK_CATALOG_FINGERPRINT_UNKNOWN)
    elif expected_catalog_fp and catalog_fp != expected_catalog_fp:
        blockers.append(BLOCK_CATALOG_FINGERPRINT_MISMATCH)

    catalog_model_ids = ctx.get("catalogModelIds")
    if catalog_model_ids is not None and model_id and model_id not in set(catalog_model_ids):
        blockers.append(BLOCK_CATALOG_MODEL_ABSENT)

    catalog_providers = ctx.get("catalogProviders")
    if _is_dict(catalog_providers) and model_id in catalog_providers:
        if _text(catalog_providers.get(model_id)) != provider:
            blockers.append(BLOCK_PROVIDER_MISMATCH)

    if record is not None:
        blockers.extend(_environment_blockers(record, ctx))

    return sorted(set(blockers))


# ─────────────────────────────────────────────────────────────────
# STALE 전이 (Requirement 6.15~6.21)
# ─────────────────────────────────────────────────────────────────
def stale_trigger(entry: Any, ctx: Mapping[str, Any] | None) -> str | None:
    """entry에 성립하는 STALE 전이 트리거 이유 코드(없으면 ``None``).

    평가 순서는 결정론적이며, 각 검사는 필요한 ctx 항목이 주어졌을 때만 수행한다
    (미확정을 이유로 STALE로 강등하지 않는다).

    ctx 키:
      ``catalogModelIds``  현재 catalog의 Exact_Model_ID 집합 → 제거 감지(6.15)
      ``catalogProviders`` `{modelId: Provider_String}` → provider 변경 감지(6.16)
      ``routeContracts``   `{modelId: {routeKey: Route_Contract}}` → 계약 변경 감지(6.17)
      ``effortContracts``  `{modelId: {routeKey: Effort_Contract}}` → 계약 변경 감지(6.18)
      ``revision``         Current_Revision → evidence revision 불일치 감지(6.19)
    """
    if not _is_dict(entry):
        return None
    ctx = ctx or {}
    model_id = _text(entry.get("modelId"))
    has_evidence = bool(entry.get("evidence")) or bool(_text(entry.get("verifiedAt")))

    catalog_model_ids = ctx.get("catalogModelIds")
    if catalog_model_ids is not None and model_id and model_id not in set(catalog_model_ids):
        return STALE_CATALOG_MODEL_REMOVED

    catalog_providers = ctx.get("catalogProviders")
    if _is_dict(catalog_providers) and model_id in catalog_providers:
        if _text(catalog_providers.get(model_id)) != _text(entry.get("provider")):
            return STALE_PROVIDER_CHANGED

    if _contracts_changed(entry, ctx.get("routeContracts"), model_id, "routes"):
        return STALE_ROUTE_CONTRACT_CHANGED
    if _contracts_changed(entry, ctx.get("effortContracts"), model_id, "effort"):
        return STALE_EFFORT_CONTRACT_CHANGED

    expected_revision = _text(ctx.get("revision"))
    if expected_revision and has_evidence and _text(entry.get("revision")) != expected_revision:
        return STALE_REVISION_MISMATCH
    return None


def _contracts_changed(entry: dict, expected_by_model: Any, model_id: str, field: str) -> bool:
    """entry가 보유한 계약이 현재 계약(ctx)과 canonical 기준으로 달라졌는지 판정한다.

    entry에 계약이 없는 route(미확정)는 변경으로 보지 않는다. ctx가 해당 route를
    열거하지 않으면 비교하지 않는다(모른다 ≠ 변경됐다).
    """
    if not _is_dict(expected_by_model) or not model_id:
        return False
    expected_routes = expected_by_model.get(model_id)
    if not _is_dict(expected_routes):
        return False
    container = entry.get(field)
    if not _is_dict(container):
        return False
    for route_key in contracts.KNOWN_ROUTES:
        if route_key not in expected_routes:
            continue
        current = container.get(route_key)
        current_contract = current.get("contract") if _is_dict(current) else None
        if current_contract is None:
            continue
        if not _canonical_equal(current_contract, expected_routes.get(route_key)):
            return True
    return False


def mark_stale(map_obj: dict, reason: str, selector: Mapping[str, Any]) -> dict:
    """selector에 일치하는 entry를 `STALE`로 전이한 새 map을 반환한다.

    selector 키(주어진 항목 전부 AND): ``candidateLabel``, ``modelId``, ``provider``,
    ``capabilityFingerprint``, ``verificationStatus``(문자열 또는 문자열 집합).
    빈 selector는 사고성 전체 전이를 막기 위해 ``ValueError``로 거부한다.

    전이는 상태와 진단 이유(`staleReason`)만 바꾸고 계약·route·effort 상태는 그대로
    둔다(Requirement 6.21 — 재검증이 완료될 때까지 `STALE`을 유지한다). 명시적
    allowlist 거부로 `REJECTED`가 된 entry는 전이 대상에서 제외한다.
    """
    if not isinstance(selector, Mapping) or not selector:
        raise ValueError("mark_stale에는 비어 있지 않은 selector가 필요하다")

    out = normalize_map(map_obj)
    stale = str(contracts.Verification_Status.STALE)
    rejected = str(contracts.Verification_Status.REJECTED)
    for entry in out["entries"]:
        if not _is_dict(entry) or not _selector_matches(entry, selector):
            continue
        if _text(entry.get("verificationStatus")) == rejected:
            continue
        entry["verificationStatus"] = stale
        entry["staleReason"] = _truncate(reason)
    out["updatedAt"] = contracts.utc_now_iso()
    return out


def _selector_matches(entry: dict, selector: Mapping[str, Any]) -> bool:
    for key, wanted in selector.items():
        if key == "verificationStatus" and not isinstance(wanted, str):
            if _text(entry.get(key)) not in {str(item) for item in wanted}:
                return False
            continue
        if _text(entry.get(key)) != _text(wanted):
            return False
    return True


def apply_stale_triggers(map_obj: dict, ctx: Mapping[str, Any] | None = None) -> dict:
    """모든 entry에 STALE 전이 트리거를 적용한 새 map을 반환한다.

    :func:`stale_trigger`가 이유를 돌려준 entry를 `STALE`로 바꾸고 `staleReason`에
    이유 코드를 남긴다. `REJECTED` entry는 보존한다(더 강한 거부 결정).
    """
    out = normalize_map(map_obj)
    stale = str(contracts.Verification_Status.STALE)
    rejected = str(contracts.Verification_Status.REJECTED)
    changed = False
    for entry in out["entries"]:
        if not _is_dict(entry) or _text(entry.get("verificationStatus")) == rejected:
            continue
        reason = stale_trigger(entry, ctx)
        if reason is None:
            continue
        entry["verificationStatus"] = stale
        entry["staleReason"] = reason
        changed = True
    if changed:
        out["updatedAt"] = contracts.utc_now_iso()
    return out


# ─────────────────────────────────────────────────────────────────
# upsert
# ─────────────────────────────────────────────────────────────────
def upsert(
    map_obj: dict,
    record: Mapping[str, Any],
    *,
    route_contracts: Mapping[str, Any] | None = None,
    effort_contracts: Mapping[str, Any] | None = None,
    ctx: Mapping[str, Any] | None = None,
    source_kind: str | None = None,
    mode_hints: Mapping[str, str] | None = None,
    now: str | None = None,
) -> dict:
    """정제 Verification_Record를 반영하고 fingerprint를 재계산한 새 map을 반환한다.

    Args:
        map_obj: 현재 Capability_Map(변경하지 않는다).
        record: 정제 Verification_Record(`contracts.validate_verification_record` 통과 필요).
        route_contracts: `{routeKey: Route_Contract|None}` — collector가 baseline과
            probe 결과로 만든 계약. 주지 않은 route의 계약은 그대로 유지한다.
        effort_contracts: `{routeKey: Effort_Contract|None}` — 동일.
        ctx: 현재성 판정 컨텍스트(:func:`promotion_blockers` 참조). 주지 않으면
            record 자신의 `revision`·`catalogFingerprint`·`environment`를 현재로 본다.
        source_kind: entry의 `sourceKind`를 갱신할 때만 지정한다(기본은 기존 값 유지).
        mode_hints: `{routeKey: Execution_Mode}` — 계약에 mode가 없을 때만 사용.
        now: map의 `updatedAt`으로 쓸 UTC ISO 8601 시각.

    Returns:
        갱신된 새 Capability_Map.

    Raises:
        InvalidVerificationRecordError: record가 스키마를 만족하지 않는다.
        CapabilityMapError: entry 키(candidateLabel·modelId)가 모두 비었거나,
            제공된 계약이 route/model 결속을 위반한다.

    동작:
        1. record가 현재 활성 evidence를 대체하지 못하면(더 오래된 evidence)
           `evidence` 목록에만 추가하고 상태·계약은 건드리지 않는다.
        2. 대체하면 identity·route 상태·allowlist·effort 상태·계약·evidence
           reference를 기록하고, `SUPPORTED`인데 계약이 불완전하거나 Current_Evidence
           reference가 없으면 `UNVERIFIED`로 낮춘다.
        3. 파생 모드 상태와 Capability_Fingerprint를 재계산한다.
        4. 승격 조건을 모두 만족하면 `VERIFIED`, 아니면 이전 상태에 따라 `STALE`
           (이전이 `VERIFIED`/`STALE`), `REJECTED`(명시적 거부 보존) 또는
           `DISCOVERED`/`UNVERIFIED`로 둔다. `REJECTED` entry는 승격 조건을 전부
           만족하는 새 evidence가 있을 때만 `VERIFIED`로 바뀐다.
    """
    reasons = contracts.validate_verification_record(record)
    if reasons:
        raise InvalidVerificationRecordError(reasons)

    label = _text(record.get("candidateLabel"))
    record_model_id = _text(record.get("modelId"))
    if not label and not record_model_id:
        raise CapabilityMapError("Verification_Record에 candidateLabel과 modelId가 모두 없다")

    out = normalize_map(map_obj, now=now)
    index = _find_index(out["entries"], label, record_model_id)
    if index is None:
        entry = contracts.new_entry(
            label,
            source_kind=source_kind or contracts.Source_Kind.CATALOG,
        )
        out["entries"].append(entry)
        index = len(out["entries"]) - 1
    else:
        entry = copy.deepcopy(out["entries"][index])
        out["entries"][index] = entry
    if source_kind is not None:
        entry["sourceKind"] = str(source_kind)

    evidence_id = _evidence_id_of(record)
    _ensure_entry_shape(entry, label)

    if not _record_supersedes(entry, record):
        # 더 오래된 evidence: 감사 추적만 남기고 상태·계약은 유지한다.
        entry["evidence"] = _merge_set_like(entry.get("evidence"), [evidence_id] if evidence_id else [])
        recompute_fingerprint(entry)
        out["updatedAt"] = now if isinstance(now, str) and now else contracts.utc_now_iso()
        return out

    prior_status = _text(entry.get("verificationStatus"))

    # 1) identity — 비어 있지 않은 값만 반영한다(미확정으로 되돌리지 않는다).
    for entry_field, record_field in (
        ("modelId", "modelId"),
        ("provider", "provider"),
        ("catalogFingerprint", "catalogFingerprint"),
        ("revision", "revision"),
    ):
        value = _text(record.get(record_field))
        if value:
            entry[entry_field] = value
    entry["verifiedAt"] = _text(record.get("verifiedAt"))
    entry["invocationModelIds"] = _merge_set_like(
        entry.get("invocationModelIds"),
        [item for item in (record.get("invocationModelIds") or []) if isinstance(item, str) and item],
    )
    if evidence_id:
        entry["evidence"] = _merge_set_like(entry.get("evidence"), [evidence_id])
        entry["activeEvidenceRef"] = evidence_id

    model_id = _text(entry.get("modelId"))

    # 2) route 상태·계약·evidence reference
    _apply_route_results(entry, record, route_contracts, evidence_id)

    # 3) effort 상태·계약·verifiedValues (base route 상태는 절대 강등하지 않는다 — 5.20)
    _apply_effort_results(entry, record, effort_contracts, evidence_id, model_id)

    # 4) 파생 모드 상태 → fingerprint 재계산
    apply_mode_support(entry, mode_hints=mode_hints)
    recompute_fingerprint(entry)

    # 5) 승격 / STALE 유지
    effective_ctx = dict(ctx) if _is_dict(ctx) else _ctx_from_record(record)
    blockers = promotion_blockers(
        entry, record=record, ctx=effective_ctx, mode_hints=mode_hints
    )
    entry["verificationStatus"] = _decide_status(prior_status, blockers)
    if entry["verificationStatus"] == str(contracts.Verification_Status.STALE):
        entry["staleReason"] = _stale_reason_from_blockers(blockers)
    else:
        entry.pop("staleReason", None)

    out["updatedAt"] = now if isinstance(now, str) and now else contracts.utc_now_iso()
    return out


def _ctx_from_record(record: Mapping[str, Any]) -> dict:
    """ctx 미지정 시 record 자신의 환경·revision·catalog fingerprint를 현재로 본다."""
    environment = record.get("environment")
    return {
        "revision": _text(record.get("revision")),
        "catalogFingerprint": _text(record.get("catalogFingerprint")),
        "environment": dict(environment) if _is_dict(environment) else None,
    }


def _ensure_entry_shape(entry: dict, label: str) -> None:
    """record 반영 전에 entry 골격을 보정한다(손상 구조는 초기값으로 재구성)."""
    if not isinstance(entry.get("schemaVersion"), int) or isinstance(entry.get("schemaVersion"), bool):
        entry["schemaVersion"] = SCHEMA_VERSION
    elif entry["schemaVersion"] not in contracts.SUPPORTED_SCHEMA_VERSIONS:
        entry["schemaVersion"] = SCHEMA_VERSION
    if not isinstance(entry.get("candidateLabel"), str):
        entry["candidateLabel"] = label
    if "displayName" not in entry:
        entry["displayName"] = None
    if not contracts.Source_Kind.has(entry.get("sourceKind")):
        entry["sourceKind"] = str(contracts.Source_Kind.CATALOG)
    if not isinstance(entry.get("evidence"), list):
        entry["evidence"] = []
    if not isinstance(entry.get("invocationModelIds"), list):
        entry["invocationModelIds"] = []
    for field, factory in (
        ("routes", contracts.new_route_entry),
        ("effort", contracts.new_effort_entry),
    ):
        container = entry.get(field)
        if not _is_dict(container):
            entry[field] = {route: factory() for route in contracts.KNOWN_ROUTES}
            continue
        for route in contracts.KNOWN_ROUTES:
            if not _is_dict(container.get(route)):
                container[route] = factory()


def _aggregate(results: Iterable[Mapping[str, Any]], key: str, priority: Sequence[str], default: str) -> str:
    statuses = [str(item.get(key)) for item in results]
    return _highest_status(statuses, priority, default)


def _results_by_route(record: Mapping[str, Any], field: str) -> dict[str, list[dict]]:
    grouped: dict[str, list[dict]] = {}
    for item in record.get(field) or []:
        if not _is_dict(item):
            continue
        route_key = item.get("routeKey")
        if not contracts.Known_Route.has(route_key):
            continue
        grouped.setdefault(str(route_key), []).append(item)
    return grouped


def _apply_route_results(
    entry: dict,
    record: Mapping[str, Any],
    route_contracts: Mapping[str, Any] | None,
    evidence_id: str,
) -> None:
    """routeResults와 제공된 Route_Contract를 entry에 반영한다."""
    grouped = _results_by_route(record, "routeResults")
    provided = route_contracts if _is_dict(route_contracts) else {}
    touched = set(grouped) | {key for key in provided if contracts.Known_Route.has(key)}

    for route_key in contracts.KNOWN_ROUTES:
        if route_key not in touched:
            continue
        route_entry = entry["routes"][route_key]
        results = grouped.get(route_key, [])
        if results:
            route_entry["status"] = _aggregate(
                results,
                "status",
                ROUTE_AGGREGATION_PRIORITY,
                str(contracts.Route_Support_Status.UNVERIFIED),
            )
            route_entry["allowlist"] = _aggregate(
                results,
                "allowlist",
                (
                    str(contracts.Allowlist_Result.REJECTED),
                    str(contracts.Allowlist_Result.UNVERIFIED),
                    str(contracts.Allowlist_Result.ALLOWED),
                ),
                str(contracts.Allowlist_Result.UNVERIFIED),
            )
            if evidence_id:
                route_entry["evidenceRef"] = evidence_id

        if route_key in provided:
            contract = copy.deepcopy(provided[route_key])
            if contract is not None:
                _reject_bad_route_contract(contract, route_key)
                if contract.get("evidenceRef") is None and evidence_id:
                    contract["evidenceRef"] = evidence_id
            route_entry["contract"] = contract

        # `SUPPORTED`는 완전한 계약 + Current_Evidence reference를 요구한다(3.3, 3.4).
        if route_entry["status"] == contracts.Route_Support_Status.SUPPORTED:
            if not contracts.route_contract_is_complete(route_entry.get("contract")) or not _text(
                route_entry.get("evidenceRef")
            ):
                route_entry["status"] = str(contracts.Route_Support_Status.UNVERIFIED)


def _apply_effort_results(
    entry: dict,
    record: Mapping[str, Any],
    effort_contracts: Mapping[str, Any] | None,
    evidence_id: str,
    model_id: str,
) -> None:
    """effortResults와 제공된 Effort_Contract를 entry에 반영한다.

    effort 결과는 route 상태를 건드리지 않는다(Requirement 5.20).
    """
    grouped = _results_by_route(record, "effortResults")
    provided = effort_contracts if _is_dict(effort_contracts) else {}
    touched = set(grouped) | {key for key in provided if contracts.Known_Route.has(key)}

    for route_key in contracts.KNOWN_ROUTES:
        if route_key not in touched:
            continue
        effort_entry = entry["effort"][route_key]

        if route_key in provided:
            contract = copy.deepcopy(provided[route_key])
            if contract is not None:
                _reject_bad_effort_contract(contract, route_key, model_id)
                if contract.get("evidenceRef") is None and evidence_id:
                    contract["evidenceRef"] = evidence_id
            effort_entry["contract"] = contract

        results = grouped.get(route_key, [])
        if results:
            effort_entry["status"] = _aggregate(
                results,
                "status",
                EFFORT_AGGREGATION_PRIORITY,
                str(contracts.Effort_Support_Status.UNVERIFIED),
            )
            if evidence_id:
                effort_entry["evidenceRef"] = evidence_id
            _merge_verified_values(effort_entry.get("contract"), results)

        # `SUPPORTED`는 완전한 계약 + Current_Evidence reference를 요구한다(3.6, 3.7).
        if effort_entry["status"] == contracts.Effort_Support_Status.SUPPORTED:
            if not contracts.effort_contract_is_complete(effort_entry.get("contract")) or not _text(
                effort_entry.get("evidenceRef")
            ):
                effort_entry["status"] = str(contracts.Effort_Support_Status.UNVERIFIED)


def _reject_bad_route_contract(contract: Any, route_key: str) -> None:
    reasons = contracts.validate_route_contract(contract, route_key=route_key)
    if reasons:
        raise CapabilityMapError(
            f"Route_Contract 결속 위반({route_key}): " + _truncate(", ".join(reasons))
        )


def _reject_bad_effort_contract(contract: Any, route_key: str, model_id: str) -> None:
    reasons = contracts.validate_effort_contract(
        contract, route_key=route_key, model_id=model_id or None
    )
    if reasons:
        raise CapabilityMapError(
            f"Effort_Contract 결속 위반({route_key}): " + _truncate(", ".join(reasons))
        )


def _value_in_domain(contract: Mapping[str, Any], value: Any) -> bool:
    """value가 계약의 verified domain(enum 멤버 또는 inclusive range)에 속하는지."""
    value_type = contract.get("valueType")
    if not contracts.Value_Type.has(value_type) or not contracts.value_matches_type(value, value_type):
        return False
    domain_kind = contract.get("domainKind")
    if domain_kind == contracts.Domain_Kind.ENUM:
        enum_values = contract.get("enumValues")
        if not isinstance(enum_values, list):
            return False
        return any(_canonical_equal(value, allowed) for allowed in enum_values)
    if domain_kind == contracts.Domain_Kind.RANGE:
        lower, upper = contract.get("rangeLowerInclusive"), contract.get("rangeUpperInclusive")
        if not isinstance(value, (int, float)) or isinstance(value, bool):
            return False
        if not isinstance(lower, (int, float)) or isinstance(lower, bool):
            return False
        if not isinstance(upper, (int, float)) or isinstance(upper, bool):
            return False
        return lower <= value <= upper
    return False


def _merge_verified_values(contract: Any, results: Sequence[Mapping[str, Any]]) -> None:
    """성공 probe의 실제 value만 `verifiedValues`에 병합한다(계약 domain 내 값 한정)."""
    if not _is_dict(contract):
        return
    additions = [
        result.get("value")
        for result in results
        if result.get("status") == contracts.Effort_Support_Status.SUPPORTED
        and _value_in_domain(contract, result.get("value"))
    ]
    if additions:
        contract["verifiedValues"] = _merge_set_like(contract.get("verifiedValues"), additions)


def _decide_status(prior_status: str, blockers: Sequence[str]) -> str:
    """승격 결과와 이전 상태로 Verification_Status를 결정한다."""
    if not blockers:
        return str(contracts.Verification_Status.VERIFIED)  # 재검증 통과 포함(6.20)
    if prior_status in (
        str(contracts.Verification_Status.VERIFIED),
        str(contracts.Verification_Status.STALE),
    ):
        return str(contracts.Verification_Status.STALE)  # 재검증 미완료 → STALE 유지(6.21)
    if prior_status == str(contracts.Verification_Status.REJECTED):
        return str(contracts.Verification_Status.REJECTED)  # 명시적 거부 보존
    if BLOCK_EMPTY_MODEL_ID in blockers or BLOCK_EMPTY_PROVIDER in blockers:
        return str(contracts.Verification_Status.UNVERIFIED)
    return str(contracts.Verification_Status.DISCOVERED)


def _stale_reason_from_blockers(blockers: Sequence[str]) -> str:
    """STALE 유지 이유를 승격 차단 코드에서 고른다(가장 구체적인 트리거 우선)."""
    if BLOCK_REVISION_MISMATCH in blockers or BLOCK_REVISION_UNKNOWN in blockers:
        return STALE_REVISION_MISMATCH
    if BLOCK_CATALOG_MODEL_ABSENT in blockers:
        return STALE_CATALOG_MODEL_REMOVED
    if BLOCK_PROVIDER_MISMATCH in blockers:
        return STALE_PROVIDER_CHANGED
    return STALE_REVERIFICATION_INCOMPLETE


# ─────────────────────────────────────────────────────────────────
# Managed_Segment 노출 — `/api/models` 병합과 UI payload
#
# 입력은 :func:`activation_gate.active_models`가 산출한 Active_Model 목록뿐이다.
# 이 절의 함수는 판정을 다시 하지 않으며(게이트는 activation_gate가 담당) 이미 통과한
# entry를 **투영**만 한다. 두 함수 모두 순수 함수이고 입력을 변경하지 않는다.
#
# 무회귀 규칙(Requirement 12.1, 12.2): Managed_Segment가 비면
# :func:`merge_active_into_catalog`는 `catalog`를 **동일 객체로 그대로** 반환하고
# server seam은 `capabilities` 키를 추가하지 않으므로 `/api/models` 응답 바이트가
# 기준선과 같다.
# ─────────────────────────────────────────────────────────────────

#: Managed_Segment 항목이 카탈로그에 노출하는 필드(Baseline 항목과 동일 형태).
CATALOG_ITEM_FIELDS: tuple[str, ...] = ("id", "name")

#: UI payload 스키마 버전(entry 스키마와 동일 버전을 사용한다).
UI_PAYLOAD_SCHEMA_VERSION: int = SCHEMA_VERSION

#: UI payload 최상위 키.
UI_PAYLOAD_KEYS: tuple[str, ...] = ("schemaVersion", "modelIds", "models")

#: UI payload의 route view 필드(계약이 알려준 값만 — 추론 없음).
UI_ROUTE_FIELDS: tuple[str, ...] = (
    "status",
    "allowlist",
    "executionMode",
    "purposes",
    "fallbackRank",
)

#: UI payload의 effort view 공통 필드. domain 필드는 `SUPPORTED`일 때만 덧붙인다.
UI_EFFORT_BASE_FIELDS: tuple[str, ...] = ("status", "supported")


def _catalog_item(entry: Mapping[str, Any]) -> dict:
    """Active_Model을 카탈로그 항목(`{"id","name"}`)으로 투영한다.

    `name`은 Exact_Model_ID를 그대로 쓴다. Candidate_Label과 `displayName`은 검색
    라벨·표시 문자열일 뿐 catalog 근거가 아니므로 모델 항목으로 노출하지 않는다
    (Requirement 6.26 — 라벨 문자열은 어떤 경우에도 모델 항목이 되지 않는다).
    """
    model_id = _text(entry.get("modelId"))
    return {"id": model_id, "name": model_id}


def merge_active_into_catalog(catalog: Any, active: Any) -> Any:
    """Baseline_Catalog_Segment에 Managed_Segment(Active_Model)를 병합한다.

    Args:
        catalog: 기존 `/api/models` 카탈로그(`{provider: [{id, name}, ...]}`).
            변경하지 않는다(얕은 복사 + 대상 provider 목록만 새 리스트).
        active: :func:`activation_gate.active_models` 결과(Active_Model entry 목록).

    Returns:
        병합된 새 카탈로그. 추가할 항목이 없으면 `catalog`를 **동일 객체로 그대로**
        반환한다(응답 바이트 보존 — Requirement 12.1, 12.2).

    규칙:
        - Active_Model은 자신의 Provider_String 그룹에 추가한다. Baseline의 provider
          분류·denylist·uninvokable 필터는 건드리지 않는다.
        - 어느 provider 그룹에든 동일 `id`(대소문자까지 동일)가 이미 있으면 건너뛴다
          (Baseline 항목 보존·중복 금지).
        - `modelId` 또는 `provider`가 비어 있는 entry는 노출하지 않는다(방어적 —
          Activation_Gate가 이미 배제한다).
        - 어떤 예외도 던지지 않는다. 호출자(server seam)는 실패 시 baseline만 반환한다.
    """
    entries = [entry for entry in (active or []) if _is_dict(entry)]
    if not entries:
        return catalog

    base = catalog if _is_dict(catalog) else {}

    existing_ids: set[str] = set()
    for items in base.values():
        if not isinstance(items, list):
            continue
        for item in items:
            if _is_dict(item) and isinstance(item.get("id"), str):
                existing_ids.add(item["id"])

    additions: dict[str, list[dict]] = {}
    for entry in entries:
        model_id = _text(entry.get("modelId"))
        provider = _text(entry.get("provider"))
        if not model_id or not provider or model_id in existing_ids:
            continue
        additions.setdefault(provider, []).append(_catalog_item(entry))
        existing_ids.add(model_id)

    if not additions:
        return catalog

    merged = dict(base)
    for provider, items in additions.items():
        current = merged.get(provider)
        merged[provider] = (list(current) if isinstance(current, list) else []) + items
    return merged


def _route_view(route_entry: Any) -> dict:
    """route 상태와 계약이 알려준 표시 정보만 투영한다(계약이 없으면 `None` 유지)."""
    source = route_entry if _is_dict(route_entry) else {}
    contract = source.get("contract")
    contract = contract if _is_dict(contract) else {}

    purposes = contract.get("purposes")
    fallback_rank = contract.get("fallbackRank")
    execution_mode = contract.get("executionMode")
    return {
        "status": _text(source.get("status")) or str(contracts.Route_Support_Status.UNVERIFIED),
        "allowlist": _text(source.get("allowlist")) or str(contracts.Allowlist_Result.UNVERIFIED),
        "executionMode": execution_mode if contracts.Execution_Mode.has(execution_mode) else None,
        "purposes": [item for item in purposes if isinstance(item, str)] if isinstance(purposes, list) else [],
        "fallbackRank": fallback_rank
        if isinstance(fallback_rank, int) and not isinstance(fallback_rank, bool)
        else 0,
    }


def _effort_view(effort_entry: Any, *, model_id: str, route_key: str) -> dict:
    """effort 상태와 **검증된 domain만** 투영한다.

    `SUPPORTED`이고 계약이 완전하며 계약이 이 `(modelId, routeKey)`에 결속된 경우에만
    `supported: True`와 domain 필드(`valueType` + enum 목록 또는 inclusive range 경계)를
    싣는다. 그 외에는 상태만 남기고 domain 필드를 넣지 않으므로 프론트가 미검증 값을
    선택 후보로 렌더할 수 없다(Requirement 7.2, 7.3).

    domain 값은 전부 Effort_Contract에서 복사한다 — 이 함수는 값을 만들지 않는다.
    """
    source = effort_entry if _is_dict(effort_entry) else {}
    status = _text(source.get("status")) or str(contracts.Effort_Support_Status.UNVERIFIED)
    view: dict[str, Any] = {"status": status, "supported": False}

    contract = source.get("contract")
    if status != contracts.Effort_Support_Status.SUPPORTED or not _is_dict(contract):
        return view
    if not contracts.effort_contract_is_complete(contract):
        return view
    if _text(contract.get("modelId")) != model_id or _text(contract.get("routeKey")) != route_key:
        return view  # 다른 model·route 계약으로는 선택 후보를 만들지 않는다

    view["supported"] = True
    view["valueType"] = _text(contract.get("valueType"))
    domain_kind = _text(contract.get("domainKind"))
    view["domainKind"] = domain_kind
    if domain_kind == contracts.Domain_Kind.ENUM:
        view["enumValues"] = copy.deepcopy(contract.get("enumValues") or [])
    else:  # RANGE — 두 inclusive 경계(상·하한 동일도 유효한 단일 경계다)
        view["rangeLowerInclusive"] = contract.get("rangeLowerInclusive")
        view["rangeUpperInclusive"] = contract.get("rangeUpperInclusive")
    verified_values = contract.get("verifiedValues")
    view["verifiedValues"] = copy.deepcopy(verified_values) if isinstance(verified_values, list) else []
    return view


def _ui_model_view(entry: Mapping[str, Any]) -> dict:
    """Active_Model entry 하나를 UI capability view로 투영한다."""
    routes = entry.get("routes") if _is_dict(entry.get("routes")) else {}
    effort = entry.get("effort") if _is_dict(entry.get("effort")) else {}
    model_id = _text(entry.get("modelId"))

    route_views = {key: _route_view(routes.get(key)) for key in contracts.KNOWN_ROUTES}
    effort_views = {
        key: _effort_view(effort.get(key), model_id=model_id, route_key=key)
        for key in contracts.KNOWN_ROUTES
    }
    return {
        "modelId": model_id,
        "provider": _text(entry.get("provider")),
        "capabilityFingerprint": _text(entry.get("capabilityFingerprint")),
        "verificationStatus": _text(entry.get("verificationStatus")),
        "syncSupport": _text(entry.get("syncSupport")),
        "asyncSupport": _text(entry.get("asyncSupport")),
        "streamingSupport": _text(entry.get("streamingSupport")),
        "routes": route_views,
        "effort": effort_views,
        "effortRoutes": [key for key in contracts.KNOWN_ROUTES if effort_views[key]["supported"]],
    }


def to_ui_payload(active: Any) -> dict:
    """Active_Model 목록을 `/api/models` 응답의 신규 `capabilities` 값으로 투영한다.

    형식(프론트는 `(modelId, route, capabilityFingerprint)` tuple로 조회한다)::

        {
          "schemaVersion": 1,
          "modelIds": ["<Exact_Model_ID>", ...],           # 노출 순서(입력 순서 보존)
          "models": {
            "<Exact_Model_ID>": {
              "modelId": "...", "provider": "...",
              "capabilityFingerprint": "cfp1:sha256:...",  # tuple 3번째 성분
              "verificationStatus": "VERIFIED",
              "syncSupport": ..., "asyncSupport": ..., "streamingSupport": ...,
              "routes": {"<Known_Route>": {"status", "allowlist", "executionMode",
                                            "purposes", "fallbackRank"}, ...},
              "effort": {"<Known_Route>": {"status", "supported",
                                            # supported일 때만:
                                            "valueType", "domainKind",
                                            "enumValues" | ("rangeLowerInclusive",
                                                            "rangeUpperInclusive"),
                                            "verifiedValues"}, ...},
              "effortRoutes": ["<Known_Route>", ...]        # effort가 SUPPORTED인 route
            }
          }
        }

    `routes`·`effort`는 모든 Known_Route 키를 담으므로 프론트는 키 존재를 가정할 수
    있고, effort 선택 UI는 `effort[route].supported === true`일 때만 렌더한다. 허용값과
    value type은 전부 Effort_Contract에서 온 값이며 이 함수는 값을 만들지 않는다.

    Args:
        active: :func:`activation_gate.active_models` 결과(Active_Model entry 목록).

    Returns:
        UI payload dict. `active`가 비면 `{"schemaVersion", "modelIds": [], "models": {}}`
        (server seam은 이 경우 `capabilities` 키 자체를 추가하지 않는다).
    """
    model_ids: list[str] = []
    models: dict[str, dict] = {}
    for entry in active or []:
        if not _is_dict(entry):
            continue
        model_id = _text(entry.get("modelId"))
        if not model_id or model_id in models:
            continue  # Exact_Model_ID 없는 entry·duplicate는 노출하지 않는다
        models[model_id] = _ui_model_view(entry)
        model_ids.append(model_id)
    return {
        "schemaVersion": UI_PAYLOAD_SCHEMA_VERSION,
        "modelIds": model_ids,
        "models": models,
    }


__all__ = [
    "BLOCK_CATALOG_FINGERPRINT_MISMATCH",
    "CATALOG_ITEM_FIELDS",
    "BLOCK_CATALOG_FINGERPRINT_UNKNOWN",
    "BLOCK_CATALOG_MODEL_ABSENT",
    "BLOCK_EMPTY_MODEL_ID",
    "BLOCK_EMPTY_PROVIDER",
    "BLOCK_ENVIRONMENT_MISMATCH",
    "BLOCK_ENVIRONMENT_UNKNOWN",
    "BLOCK_INCOMPLETE_RECORD",
    "BLOCK_MALFORMED",
    "BLOCK_NO_SUPPORTED_ROUTE",
    "BLOCK_PROVIDER_MISMATCH",
    "BLOCK_REVISION_MISMATCH",
    "BLOCK_REVISION_UNKNOWN",
    "CapabilityMapError",
    "DEFAULT_MODE_SUPPORT",
    "DERIVED_MODE_FIELDS",
    "EFFORT_AGGREGATION_PRIORITY",
    "InvalidVerificationRecordError",
    "MAP_KEYS",
    "MODE_FIELD_BY_EXECUTION_MODE",
    "MODE_PRIORITY",
    "PROMOTION_BLOCKERS",
    "ROUTE_AGGREGATION_PRIORITY",
    "SCHEMA_VERSION",
    "STALE_CATALOG_MODEL_REMOVED",
    "STALE_EFFORT_CONTRACT_CHANGED",
    "STALE_PROVIDER_CHANGED",
    "STALE_REASONS",
    "STALE_REVERIFICATION_INCOMPLETE",
    "STALE_REVISION_MISMATCH",
    "STALE_ROUTE_CONTRACT_CHANGED",
    "UI_EFFORT_BASE_FIELDS",
    "UI_PAYLOAD_KEYS",
    "UI_PAYLOAD_SCHEMA_VERSION",
    "UI_ROUTE_FIELDS",
    "apply_mode_support",
    "apply_stale_triggers",
    "classify_entries",
    "complete_record_reasons",
    "derive_mode_support",
    "entries_of",
    "entry_malformed_reasons",
    "find_entry",
    "find_entry_by_model_id",
    "invocation_model_ids_of",
    "is_complete_record",
    "load",
    "mark_stale",
    "merge_active_into_catalog",
    "new_map",
    "normalize_map",
    "promotion_blockers",
    "recompute_fingerprint",
    "route_execution_mode",
    "save",
    "select_active_evidence",
    "stale_trigger",
    "supported_routes",
    "to_ui_payload",
    "upsert",
    "valid_entries",
]
