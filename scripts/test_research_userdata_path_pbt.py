# Feature: deep-research-engine, Property 10: 영속 경로 불변식
"""Property-based test: 영속 경로 불변식 (userData 루트 하위 정규화).

Feature: deep-research-engine, Property 10: 영속 경로 불변식
**Validates: Requirements 12.1, 12.2, 12.3, 12.4**

Research_Engine이 기록하는 산출물(검색 캐시 등)의 파일 경로는 **항상 userData
캐시 루트(`userData/research_cache`) 하위**로 정규화되어야 한다. 경로 이스케이프
(`..`)·절대경로 주입(`/etc/passwd`, `C:\\...`)·특수문자·널바이트·유니코드·빈
문자열·과도하게 긴 경로 등 어떤 악의적/비정상 key에 대해서도 경로 가드
`ai_engine/research/cache.py::_safe_cache_path` 는:

  (1) 산출 경로가 항상 캐시 루트 하위다(`os.path.commonpath` 기준). — Req 12.1/12.3/12.4
  (2) 산출 경로의 부모 디렉토리는 정확히 캐시 루트다(하위 디렉토리 traversal 없음).
      basename만 취하고 경로 구분자를 안전 문자로 치환하므로 세그먼트가 생기지 않는다.
  (3) 산출 파일의 확장자는 항상 `.json`이다(캐시 엔트리 규약).
  (4) 어떤 입력에도 예외를 전파하지 않는다(널바이트·긴 경로 포함 — 비차단 P8 정합).
  (5) `cache_root`는 `AE_GENERATED_ROOT`(Electron이 주입한 userData 경로) 하위의
      `research_cache` 디렉토리로 결정된다(userData 외부 저장 금지 — Req 12.2/12.3).

검증 방법: 임시 `AE_GENERATED_ROOT`(tmp dir)를 `env`로 주입해 캐시 루트를 결정적으로
고정하고, 임의 key(및 질의→`cache_key`)에 대해 위 불변식을 확인한다. `_safe_cache_path`
는 파일을 생성하지 않는 순수 경로 계산(realpath 정규화)이므로 부작용 없이 결정적이다.

대상 코드: `ai_engine/research/cache.py`(`_safe_cache_path`/`cache_root`/`cache_key`).

Stack: Python 3.11+, hypothesis library. (기존 `scripts/test_*_pbt.py` 관례 계승)
"""
from __future__ import annotations

import atexit
import os
import shutil
import sys
import tempfile
from pathlib import Path

# ai_engine 패키지를 import 경로에 추가(스크립트 단독 실행 대응).
_REPO_ROOT = Path(__file__).resolve().parent.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from hypothesis import given, settings, strategies as st  # noqa: E402

from ai_engine.research.cache import (  # noqa: E402
    _safe_cache_path,
    cache_key,
    cache_root,
)

# 최소 반복 200회(task 8.2 지정 — @settings(max_examples=200)).
_MAX_EXAMPLES = 200

# --------------------------------------------------------------------------- #
# 결정적 검증용 임시 userData 루트 — AE_GENERATED_ROOT 로 주입
# --------------------------------------------------------------------------- #
# _safe_cache_path 는 파일을 만들지 않으므로(경로 문자열 계산 + realpath 정규화),
# 모듈 수준 tmp dir 하나를 예제 전반에서 재사용해도 안전하고 빠르다.
_TMP_ROOT = tempfile.mkdtemp(prefix="ae_research_cache_pbt_")
atexit.register(lambda: shutil.rmtree(_TMP_ROOT, ignore_errors=True))
_ENV = {"AE_GENERATED_ROOT": _TMP_ROOT}


def _root_real() -> str:
    """캐시 루트의 realpath (내부 _safe_cache_path 와 동일 정규화 — macOS /var 심링크 대응)."""
    return os.path.realpath(str(cache_root(_ENV)))


# --------------------------------------------------------------------------- #
# 생성기(strategies) — 경로 이스케이프·절대경로·특수문자·유니코드·빈/긴 경로 집중
# --------------------------------------------------------------------------- #
# (a) 경로 이스케이프(상위참조) 리터럴 — `..` 계열 다양한 변형.
_ESCAPE = st.sampled_from([
    "..",
    "../",
    "../..",
    "../../..",
    "../../../etc/passwd",
    "..\\..\\..\\windows\\system32",
    "./../../secret",
    "foo/../../../../bar",
    "....//....//",
    "%2e%2e/%2e%2e",
    "..%2f..%2fetc%2fpasswd",
    "a/../../b",
    "....",
    "./.",
])

# (b) 절대경로 주입(POSIX + Windows + UNC) 리터럴.
_ABSOLUTE = st.sampled_from([
    "/etc/passwd",
    "/",
    "//",
    "/root/.ssh/id_rsa",
    "/var/log/syslog",
    "/dev/null",
    "/proc/self/environ",
    "C:\\Windows\\System32\\config\\SAM",
    "C:/Windows/System32",
    "D:\\secret.json",
    "\\\\server\\share\\evil",
    "\\\\?\\C:\\very\\deep",
])

# (c) 특수문자 + 널바이트 + 제어문자 + 경로 구분자(0x00..0xFF 전 구간).
_SPECIAL = st.text(
    alphabet=st.characters(min_codepoint=0x00, max_codepoint=0xFF),
    min_size=0,
    max_size=64,
)

# (d) 유니코드(다국어·기호·결합문자). surrogate(Cs)는 UTF-8 인코딩 불가라 제외.
_UNICODE = st.text(
    alphabet=st.characters(
        min_codepoint=0x00, max_codepoint=0x10FFFF, blacklist_categories=("Cs",)
    ),
    min_size=0,
    max_size=64,
)

# (e) 빈 문자열(경계).
_EMPTY = st.just("")

# (f) 긴 경로 — 상위참조/구분자/확장자/널바이트를 섞어 과길이 입력을 스트레스.
_LONG_FUZZ = st.text(
    alphabet=st.characters(min_codepoint=0x01, max_codepoint=0x2FFF),
    min_size=256,
    max_size=2048,
)
_LONG_PATHY = st.lists(
    st.sampled_from(["../", "..\\", "/etc/", "\\", "a", ".json", "%2e%2e/", "\x00", "。", "~"]),
    min_size=64,
    max_size=256,
).map("".join)

# (g) 세그먼트 조합(정/역슬래시) — traversal·빈 세그먼트·제어문자 혼합.
_SEG = st.sampled_from(["..", ".", "", "etc", "passwd", "~", "a", "%2e%2e", "\x00", "\n", "\t", "。"])
_PATH_LIKE_FWD = st.lists(_SEG, min_size=1, max_size=10).map("/".join)
_PATH_LIKE_BWD = st.lists(_SEG, min_size=1, max_size=10).map("\\".join)

# 통합 key 생성기 — 위 카테고리를 폭넓게 커버.
_ANY_KEY = st.one_of(
    _ESCAPE,
    _ABSOLUTE,
    _SPECIAL,
    _UNICODE,
    _EMPTY,
    _LONG_FUZZ,
    _LONG_PATHY,
    _PATH_LIKE_FWD,
    _PATH_LIKE_BWD,
)

# 질의(query) 생성기 — 정규화·해시를 거쳐 key 로 쓰이는 경로(production path).
_ANY_QUERY = st.one_of(_ESCAPE, _ABSOLUTE, _SPECIAL, _UNICODE, _EMPTY, _PATH_LIKE_FWD)

# provider_set 생성기 — list/tuple/콤마문자열/None/특수문자 포함.
_PROVIDER_TOKEN = st.text(
    alphabet=st.characters(min_codepoint=0x00, max_codepoint=0x2FFF), max_size=12
)
_ANY_PROVIDERS = st.one_of(
    st.none(),
    st.lists(_PROVIDER_TOKEN, max_size=5),
    st.builds(tuple, st.lists(_PROVIDER_TOKEN, max_size=5)),
    _PROVIDER_TOKEN,  # 콤마 구분/단일 문자열
)


# --------------------------------------------------------------------------- #
# 공통 단언 — 산출 경로가 캐시 루트 하위 .json 이며 부모가 정확히 루트임을 검증
# --------------------------------------------------------------------------- #
def _assert_under_cache_root(result_path) -> None:
    """산출 경로 불변식(루트 하위 · 부모==루트 · .json 확장자)을 확인한다."""
    root_real = _root_real()
    result = str(result_path)

    # (1) commonpath 기준으로 항상 캐시 루트 하위(경로 이스케이프/절대경로 무력화).
    common = os.path.commonpath([root_real, result])
    assert common == root_real, (
        f"산출 경로가 캐시 루트를 벗어남: root={root_real!r}, result={result!r}, "
        f"common={common!r}"
    )

    # (2) 부모 디렉토리는 정확히 캐시 루트 — 하위 디렉토리 traversal 자체가 없음.
    parent = os.path.dirname(result)
    assert parent == root_real, (
        f"부모 디렉토리가 캐시 루트와 불일치(경로 세그먼트 유입): "
        f"parent={parent!r}, root={root_real!r}"
    )

    # (3) 확장자는 항상 .json (캐시 엔트리 규약).
    assert result.endswith(".json"), f"확장자가 .json 이 아님: {result!r}"

    # (4) 파일명에 경로 구분자·널바이트가 남지 않음(파일시스템 안전).
    name = os.path.basename(result)
    assert "/" not in name and "\\" not in name and "\x00" not in name, (
        f"파일명에 구분자/널바이트 잔존: {name!r}"
    )


# --------------------------------------------------------------------------- #
# (P10-1) 임의 key → 항상 캐시 루트 하위 .json (경로 가드 핵심)
# --------------------------------------------------------------------------- #
@settings(max_examples=_MAX_EXAMPLES, deadline=None)
@given(key=_ANY_KEY)
def test_safe_cache_path_always_under_root(key: str) -> None:
    """임의 key(이스케이프/절대경로/특수문자/널바이트/유니코드/빈/긴 경로)에 대해
    `_safe_cache_path`는 예외 없이 캐시 루트 하위의 `.json` 경로를 산출한다."""
    result = _safe_cache_path(key, env=_ENV)  # 예외가 나면 여기서 실패(비차단 위반).
    _assert_under_cache_root(result)


# --------------------------------------------------------------------------- #
# (P10-2) 산출 경로 결정성 — 동일 key 는 항상 동일 경로
# --------------------------------------------------------------------------- #
@settings(max_examples=_MAX_EXAMPLES, deadline=None)
@given(key=_ANY_KEY)
def test_safe_cache_path_deterministic(key: str) -> None:
    """동일 key 는 항상 동일한 산출 경로를 준다(캐시 히트/재현성 전제)."""
    assert _safe_cache_path(key, env=_ENV) == _safe_cache_path(key, env=_ENV)


# --------------------------------------------------------------------------- #
# (P10-3) 질의→cache_key→경로(production path) — 임의 질의/제공자도 루트 하위
# --------------------------------------------------------------------------- #
@settings(max_examples=_MAX_EXAMPLES, deadline=None)
@given(query=_ANY_QUERY, providers=_ANY_PROVIDERS)
def test_query_key_path_under_root(query, providers) -> None:
    """임의 질의/제공자 집합 → `cache_key` → `_safe_cache_path` 경로가 루트 하위 .json.

    실제 write_cache/read_cache 가 쓰는 경로 조합(질의→키→경로)을 그대로 검증한다.
    """
    key = cache_key(query, providers)
    result = _safe_cache_path(key, env=_ENV)
    _assert_under_cache_root(result)


# --------------------------------------------------------------------------- #
# (P10-4) 비-str key 방어 — 임의 타입 입력에도 루트 하위 .json (예외 없음)
# --------------------------------------------------------------------------- #
_NON_STR_KEY = st.one_of(
    st.none(),
    st.integers(),
    st.floats(allow_nan=True, allow_infinity=True),
    st.booleans(),
    st.binary(max_size=16),
    st.lists(st.integers(), max_size=4),
    st.tuples(st.integers(), st.text(max_size=4)),
)


@settings(max_examples=_MAX_EXAMPLES, deadline=None)
@given(key=_NON_STR_KEY)
def test_safe_cache_path_non_string_key(key) -> None:
    """문자열이 아닌 key(None/숫자/bytes/컬렉션 등)도 예외 없이 루트 하위 .json 로 폴백."""
    result = _safe_cache_path(key, env=_ENV)
    _assert_under_cache_root(result)


# --------------------------------------------------------------------------- #
# 스모크: 문서화된 경계·리터럴로 경로 가드/루트 규약을 구체 검증(속성 보완)
# --------------------------------------------------------------------------- #
def smoke_known_cases() -> None:
    """대표 악의적 입력과 cache_root 규약을 구체적으로 확인한다."""
    root_real = _root_real()

    # cache_root 는 AE_GENERATED_ROOT 하위 research_cache 로 결정(userData 외부 금지).
    assert str(cache_root(_ENV)) == os.path.join(_TMP_ROOT, "research_cache")

    # 대표 이스케이프/절대경로 — 모두 루트 하위 .json 으로 봉인.
    for bad in ["..", "../../etc/passwd", "/etc/passwd", "C:\\Windows\\SAM", "", "\x00", "//"]:
        p = str(_safe_cache_path(bad, env=_ENV))
        assert os.path.commonpath([root_real, p]) == root_real, f"루트 이탈: {bad!r} → {p!r}"
        assert os.path.dirname(p) == root_real, f"부모 불일치: {bad!r} → {p!r}"
        assert p.endswith(".json"), f".json 아님: {bad!r} → {p!r}"

    # 정상 흐름(sha1 hex key)은 {hex}.json 그대로 — 루트 직속.
    hexkey = cache_key("hello world", ["tavily", "exa"])
    p = str(_safe_cache_path(hexkey, env=_ENV))
    assert os.path.basename(p) == hexkey + ".json"
    assert os.path.dirname(p) == root_real

    # 동일 정규화 질의 + 동일 제공자 → 동일 키/경로(캐시 히트 전제).
    assert cache_key("  Hello   World ", "tavily,exa") == cache_key("hello world", ["tavily", "exa"])


# --------------------------------------------------------------------------- #
# 드라이버 (단독 실행: python scripts/test_research_userdata_path_pbt.py)
# --------------------------------------------------------------------------- #
_PROPERTIES = [
    ("임의 key → 항상 캐시 루트 하위 .json", test_safe_cache_path_always_under_root),
    ("산출 경로 결정성(동일 key 동일 경로)", test_safe_cache_path_deterministic),
    ("질의→cache_key→경로 루트 하위 .json", test_query_key_path_under_root),
    ("비-str key 방어(예외 없음)", test_safe_cache_path_non_string_key),
]


def main() -> int:
    print("Property test: deep-research-engine Property 10 (영속 경로 불변식)")
    print(f"  cache_root = {cache_root(_ENV)}")
    print()

    print("[smoke] 문서 경계·리터럴 경로 가드/루트 규약 ...", end=" ", flush=True)
    smoke_known_cases()
    print("OK")

    failures = []
    for label, fn in _PROPERTIES:
        print(f"[prop] {label} ...", end=" ", flush=True)
        try:
            fn()
            print("OK")
        except Exception as e:  # noqa: BLE001
            print("FAIL")
            failures.append((label, e))

    print()
    if failures:
        print(f"FAILED: {len(failures)} of {len(_PROPERTIES)} properties")
        for label, e in failures:
            print(f"  - {label}: {type(e).__name__}: {e}")
        return 1
    print(f"PASSED: all {len(_PROPERTIES)} properties")
    return 0


if __name__ == "__main__":
    sys.exit(main())
