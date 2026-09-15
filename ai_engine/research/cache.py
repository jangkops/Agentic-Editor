"""deep-research-engine: userData 검색 캐시 — 경로 가드 + TTL read/write (부작용: 파일).

동일 정규화 질의·제공자 집합에 대한 검색 결과를 userData 하위에 JSON으로
캐시해 제공자 재호출을 줄인다(요구사항 4.6). 캐시 파일 경로는 **항상 userData
루트 하위**로 정규화되어 경로 이스케이프(``..``)·절대경로 주입을 차단하며(P10),
엔트리에는 자격증명 원문을 절대 담지 않는다(P9).

경로 규약 (design.md "캐시 엔트리 스키마"):
    userData/research_cache/{sha1(normalize_query(q) + '|' + provider_set)}.json

userData 루트 결정(server.py ``_resolve_local_root``와 정합):
    1. 환경변수 ``AE_GENERATED_ROOT``(Electron이 주입한 userData 경로)가 있으면 그 하위.
    2. 없으면 안전 기본 ``~/.agentic-editor`` (OS user별 격리·항상 쓰기 가능).
그 하위의 ``research_cache/`` 디렉토리를 캐시 루트로 사용한다.

경로 가드(P10 — ``_safe_cache_path``): 임의 key(정상 sha1 hex든, ``..``/절대경로/
특수문자를 포함한 악의적 입력이든)를 받아 **항상 캐시 루트 하위의 안전한 ``.json``
경로**로 정규화한다. 파일명은 basename만 취한 뒤 안전 문자만 남기고, 결과가
비거나(``..`` 등) 정규화 후 루트를 벗어나면 원본 key의 sha1 해시 파일명으로
폴백한다(항상 유효·유일). 최종 경로가 캐시 루트 하위인지 ``os.path.commonpath``로
재확인한다(이중 안전장치).

엔트리 스키마 (design.md / 요구사항 4.6·12):
    {
      "query_normalized": str,              # normalize_query 결과 (P11)
      "provider_set": [str],                # 제공자 "이름"만 (자격증명 아님 — P9)
      "kind": "web" | "academic",
      "results": [ serialize_result(r)... ],  # 프린터 출력 (P1)
      "cached_at": float,                   # epoch 초; TTL 검사 기준
      "ttl": int                            # AE_RESEARCH_CACHE_TTL
    }

TTL 만료(요구사항 4.6): read 시 ``(now - cached_at) > ttl`` 이면 캐시 미스로 처리한다.

비차단(P8 정합): 파일 I/O·JSON 오류는 예외를 전파하지 않고 캐시 미스(read → None)/
실패(write → False)로 폴백해 검색·그래프 진행을 막지 않는다.

자격증명 비노출(P9): 엔트리에는 정규 결과 필드(serialize_result 출력)·제공자
이름·정규화 질의만 저장하며, API 키 등 ``Provider_Credential`` 원문은 어떤 필드에도
포함하지 않는다.

제약: Python 3.11 표준 라이브러리(``hashlib``/``json``/``os``/``time``/``pathlib``)와
``normalize`` 재사용(``normalize_query``/``serialize_result``/``deserialize_result``)만
사용하며 신규 의존성을 도입하지 않는다.

Requirements: 4.6, 12.1, 12.2, 12.3
Design: "Components and Interfaces" 6) cache.py · "캐시 엔트리 스키마" · 속성 P9/P10
"""

import hashlib
import json
import os
import time
from pathlib import Path

from .normalize import deserialize_result, normalize_query, serialize_result

# 캐시 디렉토리 이름(userData 루트 하위) 및 기본 TTL(config.py 표와 정합 — 24h).
_CACHE_DIRNAME = "research_cache"
_DEFAULT_CACHE_TTL = 86400

# 파일명에 허용하는 안전 문자 집합. 그 외 문자는 '_'로 치환해 경로 구분자·제어문자·
# 상위참조 유발 문자를 차단한다(P10). sha1 hex(정상 key)는 이 집합에 완전히 포함된다.
_SAFE_NAME_CHARS = frozenset(
    "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789._-"
)


# --------------------------------------------------------------------------- #
# userData 루트 / 캐시 루트 결정
# --------------------------------------------------------------------------- #
def _user_data_root(env=None) -> Path:
    """userData 루트 경로 결정 (server.py ``_resolve_local_root``와 정합).

    Args:
        env: 환경변수 매핑. ``None``이면 ``os.environ``을 사용한다.

    Returns:
        ``AE_GENERATED_ROOT``가 설정돼 있으면 그 경로, 아니면 안전 기본
        ``~/.agentic-editor``.
    """
    env = env if env is not None else os.environ
    raw = (env.get("AE_GENERATED_ROOT") or "").strip()
    if raw:
        return Path(raw)
    return Path(os.path.expanduser("~/.agentic-editor"))


def cache_root(env=None) -> Path:
    """캐시 루트 디렉토리(``userData/research_cache``) 경로를 반환한다(순수 경로 계산)."""
    return _user_data_root(env) / _CACHE_DIRNAME


# --------------------------------------------------------------------------- #
# 경로 가드 (P10) — 임의 key → 항상 캐시 루트 하위 .json 경로
# --------------------------------------------------------------------------- #
def _hash_name(raw: str) -> str:
    """원본 문자열의 sha1 hex + ``.json`` (안전·유일한 폴백 파일명)."""
    return hashlib.sha1(raw.encode("utf-8")).hexdigest() + ".json"


def _safe_cache_path(key, *, env=None) -> Path:
    """임의 key를 캐시 루트 하위의 안전한 ``.json`` 경로로 정규화한다 (P10).

    ``..``·절대경로·특수문자를 포함한 어떤 key에 대해서도 산출 경로는 항상 캐시
    루트(``userData/research_cache``) 하위다. 정상 흐름에서 key는 ``cache_key``가
    만든 sha1 hex 문자열이며, 이 경우 그대로 ``{hex}.json``이 된다.

    Args:
        key: 캐시 파일 식별자(정상: sha1 hex / 방어: 임의 문자열).
        env: 환경변수 매핑(테스트 주입용). ``None``이면 ``os.environ``.

    Returns:
        캐시 루트 하위의 절대 경로(``Path``).
    """
    root_real = os.path.realpath(str(cache_root(env)))
    raw = key if isinstance(key, str) else str(key)

    # 1) basename만 취해 디렉토리 구분자·상위 경로 세그먼트를 제거한다.
    base = os.path.basename(raw)
    # 2) 안전 문자만 유지(그 외는 '_')하고, 상위참조/숨김을 유발하는 선행 '.' 제거.
    filtered = "".join(c if c in _SAFE_NAME_CHARS else "_" for c in base).lstrip(".")
    # 3) 빈 이름(예: '..')이면 원본 key의 sha1로 폴백해 항상 유효한 파일명을 보장.
    if not filtered:
        filtered = _hash_name(raw)
    elif not filtered.endswith(".json"):
        filtered += ".json"

    cand_real = os.path.realpath(os.path.join(root_real, filtered))

    # 4) 이중 안전장치: 정규화 후에도 루트를 벗어나면 sha1 해시 파일명으로 폴백.
    try:
        if os.path.commonpath([root_real, cand_real]) != root_real:
            cand_real = os.path.join(root_real, _hash_name(raw))
    except ValueError:
        # 서로 다른 드라이브/혼합 경로 등 commonpath 예외 → 안전 폴백.
        cand_real = os.path.join(root_real, _hash_name(raw))

    return Path(cand_real)


# --------------------------------------------------------------------------- #
# 캐시 키 생성
# --------------------------------------------------------------------------- #
def _provider_list(provider_set) -> list[str]:
    """provider_set 입력을 제공자 이름 리스트로 정규화한다(순서 보존).

    list/tuple → 각 항목 str, 콤마 구분 문자열 → 분리·trim, None → 빈 리스트.
    """
    if provider_set is None:
        return []
    if isinstance(provider_set, str):
        return [p for p in (x.strip() for x in provider_set.split(",")) if p]
    try:
        return [str(p) for p in provider_set]
    except TypeError:
        return [str(provider_set)]


def _provider_set_str(provider_set) -> str:
    """캐시 키 구성용 제공자 집합 문자열(콤마 join, 순서 보존)."""
    return ",".join(_provider_list(provider_set))


def cache_key(query, provider_set) -> str:
    """``sha1(normalize_query(q) + '|' + provider_set)`` hex digest를 반환한다.

    동일 정규화 질의 + 동일 제공자 집합이면 항상 동일한 키를 산출한다(캐시 히트
    조건). ``normalize_query``는 비문자열 입력에 ``""``를 반환하므로 안전하다.
    """
    raw = normalize_query(query) + "|" + _provider_set_str(provider_set)
    return hashlib.sha1(raw.encode("utf-8")).hexdigest()


def _resolve_ttl(ttl, env=None) -> int:
    """TTL 결정: 명시 인자 > ``AE_RESEARCH_CACHE_TTL`` > 기본(86400). 형식 오류는 기본."""
    if ttl is not None:
        try:
            return int(ttl)
        except (TypeError, ValueError):
            pass
    env = env if env is not None else os.environ
    try:
        return int(str(env.get("AE_RESEARCH_CACHE_TTL")).strip())
    except (TypeError, ValueError):
        return _DEFAULT_CACHE_TTL


# --------------------------------------------------------------------------- #
# read / write (비차단 — P8)
# --------------------------------------------------------------------------- #
def write_cache(query, provider_set, kind, results, *, ttl=None, env=None, now=None) -> bool:
    """검색 결과를 캐시 엔트리로 저장한다. 성공 시 True, I/O 실패 시 False(비차단).

    엔트리 스키마(query_normalized/provider_set/kind/results/cached_at/ttl)로 직렬화하며,
    ``results``의 각 항목은 ``serialize_result``로 JSON-호환 dict로 변환한다(P1). 자격증명
    원문은 어떤 필드에도 포함하지 않는다(P9). 부분 쓰기를 피하기 위해 임시 파일에 쓴 뒤
    ``os.replace``로 원자적 교체한다.

    Args:
        query: 원 질의 문자열(정규화되어 저장·키 생성에 사용).
        provider_set: 활성 제공자 집합(이름만). list/tuple/콤마문자열 허용.
        kind: 검색 종류(``"web"`` | ``"academic"``).
        results: 정규 결과(``SearchResult`` | ``PaperResult``) 목록.
        ttl: 명시 TTL(초). ``None``이면 env/기본값 사용.
        env: 환경변수 매핑(테스트 주입용).
        now: 저장 시각(epoch 초). ``None``이면 ``time.time()``.

    Returns:
        저장 성공 여부. 파일 I/O·직렬화 실패는 예외 없이 ``False``.
    """
    try:
        entry = {
            "query_normalized": normalize_query(query),
            "provider_set": _provider_list(provider_set),
            "kind": str(kind) if kind is not None else "",
            "results": [serialize_result(r) for r in (results or [])],
            "cached_at": float(now) if now is not None else time.time(),
            "ttl": _resolve_ttl(ttl, env),
        }
        path = _safe_cache_path(cache_key(query, provider_set), env=env)
        os.makedirs(os.path.dirname(str(path)), exist_ok=True)
        tmp = str(path) + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(entry, f, ensure_ascii=False)
        os.replace(tmp, str(path))
        return True
    except Exception:
        # 파일 I/O·직렬화 실패는 비차단 — 캐시 미스처럼 취급(요구사항 12 / P8 정합).
        return False


def read_cache(query, provider_set, *, env=None, now=None):
    """캐시된 검색 결과를 복원한다. 히트 시 결과 리스트, 미스/만료/오류 시 ``None``.

    파일 부재·JSON 오류는 캐시 미스(``None``)로 폴백한다(비차단). TTL이 설정돼 있고
    ``(now - cached_at) > ttl`` 이면 만료로 간주해 ``None``을 반환한다(요구사항 4.6).
    저장된 ``results``는 ``deserialize_result``로 정규 결과로 복원한다(P1).

    Args:
        query: 원 질의 문자열(키 생성에 사용, ``write_cache``와 동일 방식).
        provider_set: 활성 제공자 집합(이름만). ``write_cache``와 동일해야 히트한다.
        env: 환경변수 매핑(테스트 주입용).
        now: 조회 시각(epoch 초). ``None``이면 ``time.time()``.

    Returns:
        복원된 결과 리스트(``list[SearchResult | PaperResult]``) 또는 ``None``.
    """
    path = _safe_cache_path(cache_key(query, provider_set), env=env)
    try:
        with open(str(path), "r", encoding="utf-8") as f:
            entry = json.load(f)
    except (OSError, ValueError):
        # 파일 없음/JSON 파싱 오류 → 캐시 미스(비차단).
        return None
    if not isinstance(entry, dict):
        return None
    # TTL 만료 검사(요구사항 4.6).
    try:
        cached_at = float(entry.get("cached_at", 0.0))
        ttl = float(entry.get("ttl", 0))
    except (TypeError, ValueError):
        return None
    current = float(now) if now is not None else time.time()
    if ttl > 0 and (current - cached_at) > ttl:
        return None  # 만료 → 캐시 미스
    raw_results = entry.get("results", [])
    if not isinstance(raw_results, list):
        return None
    try:
        return [deserialize_result(d) for d in raw_results]
    except Exception:
        # 손상된 엔트리 → 캐시 미스(비차단).
        return None
