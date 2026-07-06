"""OpenAI catalog — data models, errors, and default seed (gateway-openai-models).

이 모듈은 게이트웨이의 OpenAI Responses 라우트 모델을 에디터 카탈로그로
통합하기 위한 데이터 모델·예외·기본 시드를 정의한다.

설계 원칙 — 순수 add(추가) 방식:
- 신규 독립 모듈로, 기존 ai_engine 코드에 import 의존이 없다(비침습).
- 직렬화/소스/병합 로직은 후속 작업(1.2~1.4)에서 이 골격 위에 추가한다.

참조: .kiro/specs/gateway-openai-models/design.md
  - Components and Interfaces 1·2절 (OpenAIModelEntry, OpenAICatalogSerializer)
  - Data Models의 OpenAI_Model_Entry 표
Requirements: 2.1, 3.4, 3.5
"""
from __future__ import annotations

import json
import logging
import os
from typing import TYPE_CHECKING, Any, Protocol, TypedDict

if TYPE_CHECKING:  # pragma: no cover - 타입 힌트 전용, 런타임 import 의존 없음
    from ai_engine.gateway_module import GatewayClient

_log = logging.getLogger(__name__)


# ─────────────────────────────────────────────────────────────────
# 데이터 모델 — OpenAI_Model_Entry
#
# design.md의 Data Models > OpenAI_Model_Entry 표를 그대로 따른다.
#   | 필드         | 타입   | 필수 | 기본값           | 설명                          |
#   | id           | str    | 예   | —                | 게이트웨이 모델 식별자(1~256자)|
#   | name         | str    | 예   | —                | 표시명                        |
#   | provider     | str    | 아뇨 | "OpenAI"         | 항상 "OpenAI"로 정규화        |
#   | capabilities | dict   | 아뇨 | {"chat": True}   | 채팅 가능 플래그              |
#   | mode         | str    | 아뇨 | "auto"           | "sync"/"async"/"auto"         |
#
# TypedDict는 정규화 이후의 완전한 형태를 기술한다. 선택 필드의 기본값 보정은
# 후속 작업(1.2 OpenAICatalogSerializer.deserialize)에서 수행한다.
# ─────────────────────────────────────────────────────────────────
class OpenAIModelEntry(TypedDict):
    """단위 OpenAI 모델 항목 (정규화된 형태)."""

    id: str            # 예: "openai.gpt-5.5"  (1~256자)
    name: str          # 예: "GPT 5.5"
    provider: str      # 항상 "OpenAI"
    capabilities: dict  # 예: {"chat": True}
    mode: str          # "sync" | "async" | "auto"  (기본 "auto")


# ─────────────────────────────────────────────────────────────────
# 예외 — CatalogError
#
# 카탈로그 역직렬화 단계에서 발생하는 오류를 표현한다(요구사항 3.4, 3.5).
#   code ∈ {"invalid-json", "invalid-model-entry"}
#   detail 은 항목 식별자 등 진단 정보로, 최대 200자로 절단한다.
# ─────────────────────────────────────────────────────────────────
# 허용되는 에러 코드 집합
CATALOG_ERROR_CODES = ("invalid-json", "invalid-model-entry")

# detail 최대 길이 (요구사항: 원인 ≤ 200자)
_DETAIL_MAX_LEN = 200


class CatalogError(Exception):
    """OpenAI 카탈로그 직렬화/역직렬화 오류.

    Attributes:
        code: 오류 코드. {"invalid-json", "invalid-model-entry"} 중 하나.
        detail: 진단 정보(문제 항목 id 등). 최대 200자로 절단된다.
    """

    def __init__(self, code: str, detail: str = ""):
        self.code = code
        # detail 은 200자 상한을 강제한다(요구사항 3.4/3.5의 ≤200자).
        self.detail = (detail or "")[:_DETAIL_MAX_LEN]
        super().__init__(f"{code}: {self.detail}" if self.detail else code)


# ─────────────────────────────────────────────────────────────────
# 기본 시드 (소스 B — OpenAI_Catalog_File 부재 시 사용)
#
# 게이트웨이가 확정한 두 라우트 모델을 기본 시드로 둔다.
# design.md의 Data Models > OpenAI_Catalog_File 스키마 / "기본 시드" 참조.
# models 는 id 오름차순(5.4 → 5.5)으로 정렬한다(결정론적 직렬화 규칙).
# ─────────────────────────────────────────────────────────────────
DEFAULT_SEED_MODELS: list[OpenAIModelEntry] = [
    {
        "id": "openai.gpt-5.4",
        "name": "GPT 5.4",
        "provider": "OpenAI",
        "capabilities": {"chat": True},
        "mode": "auto",
    },
    {
        "id": "openai.gpt-5.5",
        "name": "GPT 5.5",
        "provider": "OpenAI",
        "capabilities": {"chat": True},
        "mode": "auto",
    },
]


# ─────────────────────────────────────────────────────────────────
# 정규화 — OpenAI_Model_Entry 키 집합/기본값 정규화
#
# 정규화는 직렬화/역직렬화 양쪽에서 공유하는 단일 로직이다. 멱등(idempotent)
# 하도록 설계해, 한 번 정규화된 항목을 다시 정규화해도 동일한 결과를 낸다.
# 이 멱등성이 왕복 보존(Property 5: serialize∘deserialize∘serialize == serialize)
# 의 바이트 항등을 보장한다.
#
# 정규화 대상 키 집합(정확히 5개): id, name, provider, capabilities, mode
#   - provider 기본값 "OpenAI" (없거나 빈 문자열일 때 보정)
#   - capabilities 기본값 {"chat": True} (dict 아닐 때 보정)
#   - mode 기본값 "auto" (없거나 빈 문자열일 때 보정)
# id/name 은 정규화 단계에서는 강제 검증하지 않고(검증은 deserialize의
# _validate_entry 가 담당), 문자열로 안전 변환만 한다.
# ─────────────────────────────────────────────────────────────────
def _normalize_entry(entry: Any) -> OpenAIModelEntry:
    """단일 항목을 정규화된 키 집합/기본값으로 변환한다(비검증·멱등)."""
    if not isinstance(entry, dict):
        entry = {}

    raw_id = entry.get("id")
    raw_name = entry.get("name")
    provider = entry.get("provider")
    capabilities = entry.get("capabilities")
    mode = entry.get("mode")

    return {
        "id": raw_id if isinstance(raw_id, str) else ("" if raw_id is None else str(raw_id)),
        "name": raw_name if isinstance(raw_name, str) else ("" if raw_name is None else str(raw_name)),
        "provider": provider if (isinstance(provider, str) and provider) else "OpenAI",
        "capabilities": capabilities if isinstance(capabilities, dict) else {"chat": True},
        "mode": mode if (isinstance(mode, str) and mode) else "auto",
    }


# id 길이 상한 (요구사항 3.5: 1 <= len(id) <= 256)
_ID_MAX_LEN = 256


def _validate_entry(entry: Any) -> None:
    """역직렬화 시 단일 항목을 검증한다. 위반 시 CatalogError를 raise한다.

    규칙(요구사항 3.5):
      - id 필수, 문자열, 1 <= len(id) <= 256
      - name 필수, 문자열, len >= 1
    위반 시 항상 "invalid-model-entry" 에러(detail=문제 id)를 던지며,
    호출자는 부분 목록을 생성하지 않는다.
    """
    if not isinstance(entry, dict):
        raise CatalogError("invalid-model-entry", detail=str(entry))

    raw_id = entry.get("id")
    raw_name = entry.get("name")

    # id 검증: 문자열이고 1~256자
    if not isinstance(raw_id, str) or not (1 <= len(raw_id) <= _ID_MAX_LEN):
        # 문제 항목을 식별할 수 있도록 가능한 식별자를 detail로 전달
        if isinstance(raw_id, str):
            detail = raw_id
        elif isinstance(raw_name, str):
            detail = raw_name
        else:
            detail = ""
        raise CatalogError("invalid-model-entry", detail=detail)

    # name 검증: 문자열이고 1자 이상
    if not isinstance(raw_name, str) or len(raw_name) < 1:
        raise CatalogError("invalid-model-entry", detail=raw_id)


# ─────────────────────────────────────────────────────────────────
# OpenAI_Catalog_Serializer — 결정론적 직렬화/역직렬화 (요구사항 3)
#
# 핵심 보장:
#   - serialize: id 오름차순 정렬 + 키 정규화 + 결정론적 json.dumps
#     (sort_keys=True, ensure_ascii=False, separators=(",", ":"))
#   - deserialize: json 파싱 실패 → CatalogError("invalid-json")
#     항목 검증 실패 → CatalogError("invalid-model-entry", detail=id)
#     전부 유효해야 목록 반환(부분 목록 생성 금지), 기본값 정규화 1회 수행
#   - 왕복 보존(Property 5): serialize(deserialize(serialize(x))) == serialize(x)
#
# deserialize 입력은 (1) 항목 배열 또는 (2) {"version", "models":[...]} 객체를
# 모두 허용한다. serialize는 항목 배열을 출력하므로 왕복 시 배열로 다시 읽힌다.
# ─────────────────────────────────────────────────────────────────
def serialize(entries: list[OpenAIModelEntry]) -> str:
    """OpenAI_Model_Entry 목록을 결정론적 JSON 문자열로 직렬화한다.

    - 각 항목을 정규화된 키 집합(id,name,provider,capabilities,mode)으로 변환
    - id 오름차순 정렬(안정 정렬)
    - json.dumps(sort_keys=True, ensure_ascii=False, separators=(",", ":"))
    동일 의미 입력 → 동일 UTF-8 바이트열(Property 6).
    """
    normalized = [_normalize_entry(e) for e in entries]
    normalized.sort(key=lambda item: item["id"])
    return json.dumps(
        normalized,
        sort_keys=True,
        ensure_ascii=False,
        separators=(",", ":"),
    )


def deserialize(json_str: str) -> list[OpenAIModelEntry]:
    """JSON 문자열을 OpenAI_Model_Entry 목록으로 역직렬화한다.

    절차:
      1) json.loads 실패 → CatalogError("invalid-json")
      2) 항목 배열 추출(list 또는 {"models":[...]} 객체 허용)
      3) 각 항목 검증(id/name 필수, 1<=len(id)<=256) — 위반 시
         CatalogError("invalid-model-entry", detail=id), 부분 목록 미생성
      4) 기본값 정규화(provider/capabilities/mode)를 1회 수행 후 반환
    """
    try:
        data = json.loads(json_str)
    except (ValueError, TypeError) as exc:
        raise CatalogError("invalid-json", detail=str(exc)) from exc

    if isinstance(data, list):
        raw_entries: Any = data
    elif isinstance(data, dict):
        raw_entries = data.get("models", [])
    else:
        raise CatalogError("invalid-json", detail="top-level must be an array or object")

    if not isinstance(raw_entries, list):
        raise CatalogError("invalid-json", detail="\"models\" must be an array")

    # 전부 유효해야 반환 — 검증 중 위반 발생 시 예외가 전파되어 부분 목록을
    # 반환하지 않는다(result는 지역 변수로 폐기됨).
    result: list[OpenAIModelEntry] = []
    for entry in raw_entries:
        _validate_entry(entry)
        result.append(_normalize_entry(entry))
    return result


class OpenAICatalogSerializer:
    """OpenAI 카탈로그 직렬화/역직렬화 진입점.

    모듈 수준 함수 serialize/deserialize 를 정적 메서드로도 노출해
    `OpenAICatalogSerializer.deserialize(...)` 형태의 참조를 지원한다
    (design.md의 FileCatalogSource → Serializer.deserialize 참조).
    """

    serialize = staticmethod(serialize)
    deserialize = staticmethod(deserialize)


# ═════════════════════════════════════════════════════════════════
# Task 1.3 — OpenAI_Catalog_Source 추상화 + 소스 A/B
#
# design.md Components and Interfaces 1절 참조.
#   - OpenAICatalogSource(Protocol): list_models() -> list[OpenAIModelEntry]
#       · 조회 불가 시 [] 반환(예외 아님)
#   - FileCatalogSource(소스 B, 1차 구현): userData/openai/openai_catalog.json
#       · 파일 부재 → 기본 시드(DEFAULT_SEED_MODELS) 반환
#       · 손상 JSON/검증 실패 → CatalogError를 잡아 안전 폴백(예외 밖으로 미전파)
#       · userData 하위 경로에서만 read/write
#   - GatewayListSource(소스 A 스텁): 게이트웨이 목록 엔드포인트 조회(미구현 시 [])
#   - get_catalog_source(settings): openai_list_endpoint 있으면 SourceA, 없으면 SourceB
# ═════════════════════════════════════════════════════════════════

# OpenAI_Catalog_File 스키마 버전 (소스 B)
CATALOG_FILE_VERSION = 1


class OpenAICatalogSource(Protocol):
    """소스 A/B를 추상화하는 단일 인터페이스.

    활성 소스와 무관하게 동일한 OpenAIModelEntry 구조 목록을 반환한다.
    조회가 불가능한 경우(파일 부재, 엔드포인트 미구현 등) 예외 대신 빈 목록
    또는 안전한 기본값을 반환한다.
    """

    def list_models(self) -> list[OpenAIModelEntry]:
        ...


def _seed_copy() -> list[OpenAIModelEntry]:
    """기본 시드의 깊은 사본을 반환한다(호출자 변형으로부터 상수 보호)."""
    return [
        {
            "id": m["id"],
            "name": m["name"],
            "provider": m["provider"],
            "capabilities": dict(m["capabilities"]),
            "mode": m["mode"],
        }
        for m in DEFAULT_SEED_MODELS
    ]


def _is_within(path: str, root: str) -> bool:
    """path가 root 디렉터리 하위(또는 동일)인지 안전하게 판정한다."""
    try:
        root_abs = os.path.abspath(root)
        path_abs = os.path.abspath(path)
    except (OSError, ValueError):
        return False
    # 경로 구분자를 붙여 prefix 매칭이 형제 디렉터리를 오인하지 않게 한다.
    root_norm = root_abs.rstrip(os.sep) + os.sep
    return path_abs == root_abs or path_abs.startswith(root_norm)


class FileCatalogSource:
    """소스 B — userData 하위 OpenAI_Catalog_File 기반 카탈로그.

    경로: userData/openai/openai_catalog.json
    파일 형식: {"version": 1, "models": [<OpenAIModelEntry>, ...]}

    Args:
        catalog_path: 카탈로그 JSON 파일의 절대 경로.
        user_data_root: (선택) userData 루트. 지정 시 read/write를 이 하위로만
            제한한다(보안 — 요구사항 9.5). catalog_path가 루트 밖이면 안전 폴백.
    """

    def __init__(self, catalog_path: str | None, user_data_root: str | None = None):
        self.catalog_path = catalog_path
        self.user_data_root = user_data_root

    def _path_allowed(self) -> bool:
        """catalog_path가 userData 하위로 제한되는지 검사한다."""
        if not self.catalog_path:
            return False
        if self.user_data_root:
            return _is_within(self.catalog_path, self.user_data_root)
        return True

    def list_models(self) -> list[OpenAIModelEntry]:
        """카탈로그 파일을 읽어 목록을 반환한다(안전 폴백 보장).

        - 경로 미허용/미지정 → 기본 시드
        - 파일 부재 → 기본 시드
        - 손상 JSON/검증 실패(CatalogError) → 기본 시드(예외 미전파)
        - 파일 I/O 오류(OSError) → 기본 시드(예외 미전파)
        """
        if not self._path_allowed():
            return _seed_copy()
        if not os.path.exists(self.catalog_path):  # type: ignore[arg-type]
            return _seed_copy()
        try:
            with open(self.catalog_path, "r", encoding="utf-8") as fh:  # type: ignore[arg-type]
                raw = fh.read()
            return deserialize(raw)
        except CatalogError as exc:
            # 손상/유효하지 않은 카탈로그 → 예외를 밖으로 던지지 않고 시드로 폴백
            _log.warning("OpenAI 카탈로그 폴백(시드 사용): %s", str(exc)[:200])
            return _seed_copy()
        except OSError as exc:
            _log.warning("OpenAI 카탈로그 읽기 실패(시드 사용): %s", str(exc)[:200])
            return _seed_copy()

    def save(self, entries: list[OpenAIModelEntry]) -> bool:
        """카탈로그 파일을 userData 하위에 결정론적으로 기록한다.

        userData 밖 경로면 기록하지 않고 False를 반환한다(보안 — 요구사항 9.5).
        파일 형식: {"version": CATALOG_FILE_VERSION, "models": [...]}
        반환: 기록 성공 시 True, 그 외 False(예외 미전파).
        """
        if not self._path_allowed():
            _log.warning("OpenAI 카탈로그 쓰기 거부(userData 밖 경로)")
            return False
        # serialize로 정규화·정렬된 항목 배열을 얻어 파일 구조로 감싼다.
        normalized = json.loads(serialize(entries))
        payload = json.dumps(
            {"version": CATALOG_FILE_VERSION, "models": normalized},
            sort_keys=True,
            ensure_ascii=False,
            separators=(",", ":"),
        )
        try:
            os.makedirs(os.path.dirname(self.catalog_path), exist_ok=True)  # type: ignore[arg-type]
            with open(self.catalog_path, "w", encoding="utf-8") as fh:  # type: ignore[arg-type]
                fh.write(payload)
            return True
        except OSError as exc:
            _log.warning("OpenAI 카탈로그 쓰기 실패: %s", str(exc)[:200])
            return False


class GatewayListSource:
    """소스 A(스텁) — 게이트웨이 목록 엔드포인트 기반 카탈로그.

    게이트웨이 목록 API가 확정되면 list_models()에서 엔드포인트를 조회해 동일한
    OpenAIModelEntry 구조로 정규화한다. 현재는 미구현 스텁으로 빈 목록을 반환한다
    (조회 불가 시 예외 대신 []).

    Args:
        gw: GatewayClient 인스턴스(향후 엔드포인트 조회에 사용).
        endpoint: OpenAI 모델 목록 엔드포인트 경로/URL.
    """

    def __init__(self, gw: "GatewayClient | None", endpoint: str):
        self.gw = gw
        self.endpoint = endpoint

    def list_models(self) -> list[OpenAIModelEntry]:
        # 스텁: 게이트웨이 목록 API 미확정 → 빈 목록 반환(예외 아님).
        # 구현 시: 엔드포인트 조회 → deserialize/정규화 → 목록 반환.
        return []


def get_catalog_source(settings: dict | None) -> OpenAICatalogSource:
    """활성 OpenAI 카탈로그 소스를 반환한다(무중단 전환 추상화).

    - settings['openai_list_endpoint']가 있으면 GatewayListSource(소스 A)
    - 없으면 FileCatalogSource(소스 B)

    소스 B의 카탈로그 경로는 settings['openai_catalog_path']를 우선 사용하고,
    없으면 settings['user_data_root'] 하위의
    'openai/openai_catalog.json'으로 구성한다. userData 루트도 없으면
    '~/.agentic-editor/openai/openai_catalog.json' 폴백 경로를 사용한다.
    """
    settings = settings or {}
    endpoint = settings.get("openai_list_endpoint")
    if endpoint:
        return GatewayListSource(settings.get("gateway_client"), endpoint)

    user_data_root = settings.get("user_data_root")
    catalog_path = settings.get("openai_catalog_path")
    if not catalog_path:
        if user_data_root:
            catalog_path = os.path.join(
                user_data_root, "openai", "openai_catalog.json"
            )
        else:
            # userData 루트 미지정 → 사용자 홈 하위 기본 폴백 경로.
            # 이 폴백 루트를 user_data_root로도 사용해 read/write를
            # 해당 하위로 제한한다(보안 — 요구사항 9.5).
            user_data_root = os.path.expanduser(
                os.path.join("~", ".agentic-editor")
            )
            catalog_path = os.path.join(
                user_data_root, "openai", "openai_catalog.json"
            )
    return FileCatalogSource(catalog_path, user_data_root=user_data_root)


# ═════════════════════════════════════════════════════════════════
# Task 1.4 — merge_openai_into_catalog 병합 함수
#
# design.md Components and Interfaces 3절 참조.
#   - bedrock_catalog: {provider: [{id, name}, ...]} (기존 구조 불변)
#   - openai_entries == [] → bedrock_catalog 변경 없이 반환(baseline 보존)
#   - 각 entry는 provider "OpenAI" 그룹에 추가, capabilities.chat=True 보장
#   - 기존 카탈로그 어느 provider에든 동일 id(대소문자까지 동일) 존재 시
#     그 entry 스킵(Bedrock 보존, 중복 추가 금지 — 요구사항 1.5)
# ═════════════════════════════════════════════════════════════════
def merge_openai_into_catalog(bedrock_catalog: dict, openai_entries: list) -> dict:
    """Bedrock 카탈로그에 OpenAI 항목을 병합한다.

    Args:
        bedrock_catalog: {provider: [{id, name}, ...]} 구조. 입력은 변형하지 않는다.
        openai_entries: OpenAIModelEntry(또는 그 부분) 목록.

    Returns:
        병합된 카탈로그(dict). openai_entries가 비면 bedrock_catalog를
        변경 없이 그대로 반환한다(baseline 바이트 보존 — 요구사항 1.4/8.1).
    """
    # 빈 목록 → baseline 그대로 반환(변형·복사 없음).
    if not openai_entries:
        return bedrock_catalog

    # 기존 카탈로그 전 provider의 id 집합 수집(대소문자까지 정확히 동일 매칭).
    existing_ids: set[str] = set()
    for models in bedrock_catalog.values():
        if isinstance(models, list):
            for m in models:
                if isinstance(m, dict) and isinstance(m.get("id"), str):
                    existing_ids.add(m["id"])

    # 입력을 변형하지 않도록 얕은 복사 후 OpenAI 그룹만 새 리스트로 구성.
    merged = dict(bedrock_catalog)
    openai_group: list[OpenAIModelEntry] = list(merged.get("OpenAI", []))
    # 기존 OpenAI 그룹 id도 중복 판정 대상에 포함.
    for m in openai_group:
        if isinstance(m, dict) and isinstance(m.get("id"), str):
            existing_ids.add(m["id"])

    for entry in openai_entries:
        norm = _normalize_entry(entry)
        # provider 강제 + capabilities.chat=True 보장(요구사항 1.3).
        norm["provider"] = "OpenAI"
        caps = dict(norm.get("capabilities") or {})
        caps["chat"] = True
        norm["capabilities"] = caps

        # 어느 provider에든 동일 id가 이미 있으면 스킵(중복 추가 금지 — 요구사항 1.5).
        if norm["id"] in existing_ids:
            continue
        openai_group.append(norm)
        existing_ids.add(norm["id"])

    if openai_group:
        merged["OpenAI"] = openai_group
    return merged


# ─────────────────────────────────────────────────────────────────
# Task 1.2 — OpenAICatalogSerializer (결정론적 직렬화 / 역직렬화)
#
# 왕복 보존(요구사항 3.1, 3.3): serialize(deserialize(serialize(x))) == serialize(x)
# 결정론적(요구사항 3.2): 동일 의미 입력 → 동일 UTF-8 바이트
# 검증(요구사항 3.4, 3.5): invalid-json / invalid-model-entry, 부분 목록 금지
# ─────────────────────────────────────────────────────────────────
import json as _json

# 직렬화 키 순서 고정 + 정규화 키 집합
_ENTRY_KEYS = ("id", "name", "provider", "capabilities", "mode")
_ID_MIN, _ID_MAX = 1, 256
_VALID_MODES = ("sync", "async", "auto")


def _normalize_entry(raw: dict) -> OpenAIModelEntry:
    """단일 항목을 OpenAIModelEntry로 정규화한다. 필수 필드 검증 포함.

    위반 시 CatalogError("invalid-model-entry", detail=<문제 id 또는 사유>) 발생.
    선택 필드(provider/capabilities/mode)는 기본값으로 보정한다.
    """
    if not isinstance(raw, dict):
        raise CatalogError("invalid-model-entry", "entry is not an object")
    _id = raw.get("id")
    _name = raw.get("name")
    if not isinstance(_id, str) or not (_ID_MIN <= len(_id) <= _ID_MAX):
        raise CatalogError("invalid-model-entry", f"id invalid: {str(_id)[:80]}")
    if not isinstance(_name, str) or not _name:
        raise CatalogError("invalid-model-entry", f"name missing for id: {_id[:80]}")
    # provider 정규화 — 항상 "OpenAI"
    provider = "OpenAI"
    # capabilities 정규화 — chat=True 보장
    caps = raw.get("capabilities")
    if not isinstance(caps, dict):
        caps = {}
    caps = dict(caps)
    caps["chat"] = True
    # mode 정규화
    mode = raw.get("mode")
    if mode not in _VALID_MODES:
        mode = "auto"
    return {
        "id": _id,
        "name": _name,
        "provider": provider,
        "capabilities": caps,
        "mode": mode,
    }


def deserialize(json_str: str) -> list[OpenAIModelEntry]:
    """OpenAI_Catalog_File JSON 문자열 → 정규화된 OpenAIModelEntry 목록.

    - json 파싱 실패 → CatalogError("invalid-json")
    - 항목 검증 위반 → CatalogError("invalid-model-entry", detail=문제 id) (부분 목록 금지)
    - 입력은 {"version":..,"models":[...]} 또는 [...] 둘 다 허용.
    """
    try:
        data = _json.loads(json_str)
    except (ValueError, TypeError) as e:
        raise CatalogError("invalid-json", str(e)[:_DETAIL_MAX_LEN])
    if isinstance(data, dict):
        models = data.get("models", [])
    elif isinstance(data, list):
        models = data
    else:
        raise CatalogError("invalid-json", "top-level must be object or array")
    if not isinstance(models, list):
        raise CatalogError("invalid-json", "'models' must be an array")
    # 전부 유효해야 반환(부분 목록 생성 금지) — 먼저 전량 정규화
    out: list[OpenAIModelEntry] = [_normalize_entry(m) for m in models]
    return out


def serialize(entries: list[OpenAIModelEntry]) -> str:
    """OpenAIModelEntry 목록 → 결정론적 UTF-8 JSON 문자열.

    - id 오름차순 정렬
    - 각 항목은 정규화 키 집합만 포함
    - sort_keys=True, ensure_ascii=False, 공백 없는 separators 고정
    - 동일 입력(정규화 후) → 동일 바이트
    """
    norm = [_normalize_entry(e) for e in (entries or [])]
    norm.sort(key=lambda e: e["id"])
    payload = {"version": 1, "models": norm}
    return _json.dumps(
        payload, sort_keys=True, ensure_ascii=False, separators=(",", ":")
    )


# ─────────────────────────────────────────────────────────────────
# Task 1.3 — OpenAI_Catalog_Source 추상화 + 소스 A/B
# ─────────────────────────────────────────────────────────────────
import os as _os
from typing import Protocol as _Protocol


def _default_catalog_path() -> str:
    """OpenAI_Catalog_File 경로 — userData 하위에만 (요구사항 9.5).

    _resolve_local_root와 동일 우선순위: AE_GENERATED_ROOT → ~/.agentic-editor.
    경로: <root>/openai/openai_catalog.json
    """
    root = _os.environ.get("AE_GENERATED_ROOT", "").strip()
    if not root:
        root = _os.path.expanduser("~/.agentic-editor")
    return _os.path.join(root, "openai", "openai_catalog.json")


class OpenAICatalogSource(_Protocol):
    """OpenAI 모델 목록을 반환하는 단일 인터페이스 (소스 A/B 공통)."""

    def list_models(self) -> list:  # list[OpenAIModelEntry]
        ...


class FileCatalogSource:
    """소스 B — userData 하위 OpenAI_Catalog_File에서 목록 조회.

    파일 부재/빈 경우 기본 시드(DEFAULT_SEED_MODELS) 반환.
    파싱 실패 시 빈 목록이 아니라 시드로 폴백(요구사항 9 graceful, 사용자 가시성 유지).
    어떤 경우에도 예외를 호출자로 던지지 않는다(조회 불가 시 빈/시드 목록).
    """

    def __init__(self, catalog_path: str | None = None):
        self._path = catalog_path or _default_catalog_path()

    def list_models(self) -> list:
        try:
            if not _os.path.isfile(self._path):
                return [dict(m) for m in DEFAULT_SEED_MODELS]
            with open(self._path, "r", encoding="utf-8") as f:
                txt = f.read()
            if not txt.strip():
                return [dict(m) for m in DEFAULT_SEED_MODELS]
            return deserialize(txt)
        except CatalogError as e:
            print(f"[OpenAICatalog] 파일 파싱 실패 → 시드 폴백: {e.code}:{e.detail}")
            return [dict(m) for m in DEFAULT_SEED_MODELS]
        except OSError as e:
            print(f"[OpenAICatalog] 파일 읽기 실패 → 빈 목록: {str(e)[:120]}")
            return []

    def write_models(self, entries: list) -> None:
        """카탈로그 파일 쓰기(userData 하위). 디렉토리 자동 생성."""
        d = _os.path.dirname(self._path)
        _os.makedirs(d, exist_ok=True)
        with open(self._path, "w", encoding="utf-8") as f:
            f.write(serialize(entries))


class GatewayListSource:
    """소스 A (미래 옵트인) — 게이트웨이 모델 목록 엔드포인트 조회.

    현재 게이트웨이에 모델 목록 API가 없으므로 스텁. 엔드포인트가 추가되면
    여기서 조회→동일 OpenAIModelEntry 구조로 정규화. 조회 불가 시 빈 목록.
    """

    def __init__(self, gw, endpoint: str):
        self._gw = gw
        self._endpoint = endpoint

    def list_models(self) -> list:
        # 게이트웨이 목록 API 미확정 — 안전하게 빈 목록 반환(소스 B가 1차).
        return []


def get_catalog_source(settings: dict | None = None) -> OpenAICatalogSource:
    """소스 선택: settings['openai_list_endpoint'] 있으면 소스 A, 없으면 소스 B.

    무중단 전환 추상화 — 호출 측은 활성 소스를 직접 참조하지 않는다(요구사항 2).
    """
    settings = settings or {}
    endpoint = (settings.get("openai_list_endpoint") or "").strip()
    if endpoint and settings.get("_gateway_client") is not None:
        return GatewayListSource(settings["_gateway_client"], endpoint)
    return FileCatalogSource(settings.get("openai_catalog_path"))


# ─────────────────────────────────────────────────────────────────
# Task 1.4 — merge_openai_into_catalog
# ─────────────────────────────────────────────────────────────────
def merge_openai_into_catalog(bedrock_catalog: dict, openai_entries: list) -> dict:
    """Bedrock 카탈로그({provider: [{id,name,...}]})에 OpenAI 항목 병합.

    규칙(요구사항 1):
      - openai_entries == [] → bedrock_catalog 변경 없이 동일 객체 반환(baseline 보존)
      - 각 entry는 provider "OpenAI" 그룹에 추가, capabilities.chat=True 보장
      - 기존 카탈로그 어느 provider에든 동일 id가 이미 있으면 그 entry 스킵
        (Bedrock 항목 보존, 중복 추가 금지)
    어떤 예외도 던지지 않는다.
    """
    if not openai_entries:
        return bedrock_catalog
    # 기존 전체 id 집합 수집
    existing_ids = set()
    for _prov, ms in (bedrock_catalog or {}).items():
        if isinstance(ms, list):
            for m in ms:
                if isinstance(m, dict) and m.get("id"):
                    existing_ids.add(m["id"])
    merged = dict(bedrock_catalog or {})
    openai_group = list(merged.get("OpenAI", []))
    added_ids = {m.get("id") for m in openai_group if isinstance(m, dict)}
    for raw in openai_entries:
        try:
            entry = _normalize_entry(raw)
        except CatalogError:
            continue
        mid = entry["id"]
        if mid in existing_ids or mid in added_ids:
            continue  # 중복 — Bedrock 보존, 추가 안 함
        openai_group.append({
            "id": entry["id"],
            "name": entry["name"],
            "capabilities": entry["capabilities"],
            "provider": "OpenAI",
            "mode": entry["mode"],
        })
        added_ids.add(mid)
    if openai_group:
        merged["OpenAI"] = openai_group
    return merged
