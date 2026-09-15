# Feature: deep-research-engine, Property 9: 자격증명 비노출
"""Property-based test: 자격증명 비노출 (Provider_Credential 비노출).

Feature: deep-research-engine, Property 9: 자격증명 비노출
**Validates: Requirements 11.1, 11.4, 11.5**

For any key string, the security helper `mask_secret` and the cache serializer
`serialize_result` MUST NOT expose a Provider_Credential in clear text:

  (1) 로그용 마스킹 비노출 — 길이 > 4인 임의 키에 대해 `mask_secret(key)`는 앞 4자만
      남기고 5번째 문자부터는 원문을 노출하지 않는다(나머지 전부 마스킹 문자 `*`).
      길이 <= 4인 키는 전부 마스킹된다(요구사항 11.4).
  (2) 마스킹 결과의 길이·규칙 일관성 — 비어있지 않은 문자열 키는 길이가 보존되고,
      노출 접두는 최대 4자, 나머지는 모두 `*`이며 동일 입력에 동일 출력을 준다.
  (3) 방어성(예외 없음) — None / 빈 문자열 / 짧은 키 / 문자열이 아닌 입력에 대해
      예외를 던지지 않고 항상 문자열을 반환한다(요구사항 11.1/11.5의 무노출 폴백).
  (4) 캐시/직렬화 산출물 비노출 — 제공자 원시 응답에 자격증명을 주입해도, 정규화
      (`parse_search_result`/`parse_paper_result`) → 직렬화(`serialize_result`)를
      거친 캐시 산출물에는 주입한 키 원문이 포함되지 않는다(정규화 화이트리스트가
      자격증명 필드를 탈락시킴 — 요구사항 11.5, P9). "가능 범위에서" 검증.

대상 코드: `ai_engine/research/security.py`(`mask_secret`/`load_credential`) +
`ai_engine/research/normalize.py`(`serialize_result`/`parse_*`).

Stack: Python 3.11+, hypothesis library. (기존 `scripts/test_*_pbt.py` 관례 계승)
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

# ai_engine 패키지를 import 경로에 추가(스크립트 단독 실행 대응).
_REPO_ROOT = Path(__file__).resolve().parent.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from hypothesis import assume, given, settings, strategies as st  # noqa: E402

from ai_engine.research.normalize import (  # noqa: E402
    parse_paper_result,
    parse_search_result,
    serialize_result,
)
from ai_engine.research.security import _VISIBLE_PREFIX, mask_secret  # noqa: E402

# 최소 반복 200회(task 7.2 지정 — @settings(max_examples=200)).
_MAX_EXAMPLES = 200


# --------------------------------------------------------------------------- #
# 생성기(strategies) — 다양한 길이·문자셋 키, 빈/짧은 키, 비문자열 입력
# --------------------------------------------------------------------------- #
# 임의 키: 제어문자·구두점(`*` 포함)·유니코드를 폭넓게 포함해 마스킹 불변식이
# 어떤 문자 구성에도 성립함을 검증한다. NUL(0x00)만 제외(반례 출력 단순화).
_KEY_CHARS = st.characters(min_codepoint=0x01, max_codepoint=0x2FFF)
_ANY_KEY = st.text(alphabet=_KEY_CHARS, min_size=0, max_size=48)

# 짧은 키(길이 1..4) — 전량 마스킹 경계.
_SHORT_KEY = st.text(alphabet=_KEY_CHARS, min_size=1, max_size=_VISIBLE_PREFIX)

# `*`를 포함하지 않는 키 — "원문 전체가 마스킹 결과의 부분문자열이 아님"을
# 반례 없이(coincidence 없이) 단언하기 위한 현실적 API 키 유사 생성기.
_NO_STAR_CHARS = st.characters(
    min_codepoint=0x01, max_codepoint=0x2FFF, blacklist_characters="*"
)
_NO_STAR_KEY = st.text(alphabet=_NO_STAR_CHARS, min_size=1, max_size=48)

# 문자열이 아닌 입력(방어성 검증). NaN/inf float, bytes, bool, 컬렉션 포함.
_NON_STR = st.one_of(
    st.none(),
    st.integers(),
    st.floats(allow_nan=True, allow_infinity=True),
    st.booleans(),
    st.binary(max_size=16),
    st.lists(st.integers(), max_size=4),
    st.dictionaries(st.text(max_size=3), st.integers(), max_size=3),
    st.tuples(st.integers(), st.integers()),
)

# 자격증명 유사 시크릿(캐시 비노출 검증용) — 영숫자 + `-`/`_`, 길이 8..40.
# min_size 8 이상이면 벤치 JSON(수백 바이트)에 우연히 부분문자열로 등장할 확률이
# 사실상 0이며, 남은 우연은 assume 으로 배제한다.
_SECRET_CHARS = st.characters(
    whitelist_categories=("Lu", "Ll", "Nd"), whitelist_characters="-_"
)
_SECRET = st.text(alphabet=_SECRET_CHARS, min_size=8, max_size=40)

# 정규화 매핑(providers.py)에 등장하지 않는 자격증명 필드 이름들.
# 이 키들은 웹/학술 어댑터 어디에서도 읽히지 않으므로 정규화 시 탈락해야 한다.
_CRED_KEYS = ("api_key", "apiKey", "authorization", "auth_token", "secret", "credential")


# --------------------------------------------------------------------------- #
# (1)(2) mask_secret 구조·비노출·길이 규칙 불변식 — 임의 키(제어/유니코드/`*` 포함)
# --------------------------------------------------------------------------- #
@settings(max_examples=_MAX_EXAMPLES)
@given(key=_ANY_KEY)
def test_mask_secret_structure_and_non_exposure(key: str) -> None:
    """임의 키에 대해 마스킹 구조·길이·비노출 규칙이 일관되게 성립한다."""
    masked = mask_secret(key)
    n = len(key)

    # 항상 문자열 반환.
    assert isinstance(masked, str)

    if n == 0:
        # 빈 문자열 → 노출할 것이 없음.
        assert masked == ""
        return

    # 비어있지 않은 문자열은 길이가 보존된다(마스킹은 문자 치환일 뿐 절단이 아님).
    assert len(masked) == n, f"길이 불일치: key len={n}, masked len={len(masked)}"

    if n <= _VISIBLE_PREFIX:
        # 길이 <= 4 → 앞 4자 노출이 곧 전체 노출이므로 전량 마스킹.
        assert masked == "*" * n
        assert set(masked) <= {"*"}
    else:
        # 길이 > 4 → 앞 4자만 원문 유지, 5번째 문자부터 전부 마스킹.
        assert masked[:_VISIBLE_PREFIX] == key[:_VISIBLE_PREFIX], "앞 4자 접두가 원문과 불일치"
        tail = masked[_VISIBLE_PREFIX:]
        assert tail == "*" * (n - _VISIBLE_PREFIX), "5번째 문자부터 마스킹되지 않음(원문 노출)"
        # 노출(원문 유지)되는 위치는 앞 4자로 한정 — 5번째 문자부터는 전부 `*`.
        # (앞 4자에 `*`가 섞여 있을 수 있으므로 전체 `*` 개수로 세지 않는다.)
        assert len(tail.replace("*", "")) == 0


@settings(max_examples=_MAX_EXAMPLES)
@given(key=_ANY_KEY)
def test_mask_secret_idempotent_shape(key: str) -> None:
    """동일 입력은 항상 동일 마스킹 결과를 산출한다(규칙 결정성)."""
    assert mask_secret(key) == mask_secret(key)


@settings(max_examples=_MAX_EXAMPLES)
@given(key=_NO_STAR_KEY)
def test_mask_secret_raw_not_substring(key: str) -> None:
    """`*`를 포함하지 않는 임의 키 원문은 마스킹 결과의 부분문자열이 아니다.

    마스킹 결과는 항상 원문과 동일 길이이므로 "부분문자열"은 곧 "동일"을 의미한다.
    길이 <= 4는 전량 `*`, 길이 > 4는 5번째부터 `*`라 `*`가 없는 원문과 절대 같을 수
    없다 → 원문 전체가 로그(마스킹 결과)에 노출되지 않음을 보장한다.
    """
    masked = mask_secret(key)
    assert key not in masked, f"원문이 마스킹 결과에 노출됨: key={key!r}, masked={masked!r}"

    n = len(key)
    if n > _VISIBLE_PREFIX:
        # 민감한 꼬리(5번째 문자 이후)의 어떤 원문 문자도 자기 위치에 남지 않는다.
        assert masked[_VISIBLE_PREFIX:] == "*" * (n - _VISIBLE_PREFIX)


# --------------------------------------------------------------------------- #
# (3) 방어성 — None/빈/짧은/비문자열 입력에 예외 없음, 항상 문자열 반환
# --------------------------------------------------------------------------- #
@settings(max_examples=_MAX_EXAMPLES)
@given(bad=_NON_STR)
def test_mask_secret_non_string_defense(bad) -> None:
    """문자열이 아닌 입력은 예외 없이 빈 문자열로 폴백한다(노출할 것 없음)."""
    out = mask_secret(bad)  # 예외가 나면 여기서 실패.
    assert isinstance(out, str)
    assert out == "", f"비문자열 입력은 ''로 폴백해야 함: {type(bad).__name__} → {out!r}"


@settings(max_examples=_MAX_EXAMPLES)
@given(key=_SHORT_KEY)
def test_mask_secret_short_key_fully_masked(key: str) -> None:
    """짧은 키(길이 1..4)는 전량 마스킹되어 원문 문자가 남지 않는다."""
    masked = mask_secret(key)
    assert masked == "*" * len(key)
    assert set(masked) <= {"*"}


# --------------------------------------------------------------------------- #
# (4) 캐시/직렬화 산출물 비노출 — 주입한 자격증명 원문이 serialize 결과에 부재
# --------------------------------------------------------------------------- #
def _web_raw_clean() -> dict:
    """벤치(자격증명 없는) 웹 원시 항목 — 모든 매핑 대상 필드는 무해한 상수."""
    snippet = "A benign snippet without credentials."
    return {
        "title": "Benign Example Title",
        "url": "https://example.com/benign-article",
        "content": snippet,       # Tavily → snippet
        "text": snippet,          # Exa → snippet
        "description": snippet,    # Brave → snippet
        "published_date": "2023-01-02",
        "publishedDate": "2023-01-02",
        "page_age": "2023-01-02",
        "score": 0.42,
    }


def _paper_raw_clean() -> dict:
    """벤치(자격증명 없는) 논문 원시 항목 — 모든 매핑 대상 필드는 무해한 상수."""
    return {
        "title": "Benign Paper Title",
        "authors": [{"name": "Jane Benign"}],
        "year": 2021,
        "venue": "Journal of Benign Studies",
        "abstract": "A benign abstract without credentials.",
        "externalIds": {"DOI": "10.1000/benign"},
        "url": "https://example.com/benign-paper",
        "citationCount": 7,
        "score": 0.9,
    }


def _inject_credentials(raw_clean: dict, secret: str) -> dict:
    """벤치 원시 항목에 자격증명 유사 값을 비매핑 필드로 주입한다.

    최상위 여러 자격증명 키 + 중첩 요청 메타데이터(헤더에 Bearer 토큰)로,
    제공자 응답이 인증 정보를 되비추는 상황을 모사한다. 이 필드들은 어떤
    어댑터에서도 읽히지 않으므로 정규화 시 전부 탈락해야 한다.
    """
    raw = dict(raw_clean)
    for k in _CRED_KEYS:
        raw[k] = secret
    raw["authorization"] = "Bearer " + secret
    raw["request"] = {
        "headers": {"authorization": "Bearer " + secret, "x-api-key": secret},
        "api_key": secret,
    }
    return raw


def _dump(obj) -> str:
    return json.dumps(obj, ensure_ascii=False, sort_keys=True)


@settings(max_examples=_MAX_EXAMPLES)
@given(provider=st.sampled_from(("tavily", "exa", "brave")), secret=_SECRET)
def test_web_cache_serialization_excludes_credentials(provider: str, secret: str) -> None:
    """웹 원시 응답에 자격증명을 주입해도 직렬화(캐시) 산출물에 원문이 없다."""
    raw_clean = _web_raw_clean()
    raw_injected = _inject_credentials(raw_clean, secret)

    ser_clean = serialize_result(parse_search_result(provider, raw_clean))
    ser_injected = serialize_result(parse_search_result(provider, raw_injected))

    # (a) 자격증명 필드는 정규화 화이트리스트에서 탈락 → 캐시 산출물에 영향 없음.
    #     주입본이 벤치본과 동일하다는 것은 자격증명이 정규 결과에 유입되지 않았음을
    #     구조적으로 증명한다(주입값은 raw_clean 에 없으므로 유입 시 반드시 차이 발생).
    assert ser_injected == ser_clean, "주입한 자격증명 필드가 정규화 결과에 유입됨"

    # (b) 직렬화 산출물(JSON)에 주입 키 원문이 부분문자열로도 등장하지 않는다.
    blob_clean = _dump(ser_clean)
    assume(secret not in blob_clean)  # 벤치값과의 astronomically-rare 우연 일치 배제
    blob = _dump(ser_injected)
    assert secret not in blob, f"직렬화 산출물에 자격증명 원문 노출: provider={provider}"
    assert ("Bearer " + secret) not in blob


@settings(max_examples=_MAX_EXAMPLES)
@given(provider=st.sampled_from(("semantic_scholar", "openalex")), secret=_SECRET)
def test_paper_cache_serialization_excludes_credentials(provider: str, secret: str) -> None:
    """논문 원시 응답에 자격증명을 주입해도 직렬화(캐시) 산출물에 원문이 없다."""
    raw_clean = _paper_raw_clean()
    raw_injected = _inject_credentials(raw_clean, secret)

    ser_clean = serialize_result(parse_paper_result(provider, raw_clean))
    ser_injected = serialize_result(parse_paper_result(provider, raw_injected))

    assert ser_injected == ser_clean, "주입한 자격증명 필드가 정규화 결과에 유입됨"

    blob_clean = _dump(ser_clean)
    assume(secret not in blob_clean)
    blob = _dump(ser_injected)
    assert secret not in blob, f"직렬화 산출물에 자격증명 원문 노출: provider={provider}"
    assert ("Bearer " + secret) not in blob


# --------------------------------------------------------------------------- #
# 스모크: 문서화된 예시·경계값으로 마스킹 규칙을 구체 검증(속성 테스트 보완)
# --------------------------------------------------------------------------- #
def smoke_known_examples() -> None:
    """설계 문서의 예시와 경계값에 대한 구체 마스킹 결과를 확인한다."""
    assert mask_secret("abcd1234efgh") == "abcd" + "*" * 8   # design 예시
    assert mask_secret("ab") == "**"                          # 짧은 키 전량 마스킹
    assert mask_secret("abcd") == "****"                      # 경계(길이 == 4)
    assert mask_secret("abcde") == "abcd*"                     # 경계(길이 == 5)
    assert mask_secret("") == ""                               # 빈 문자열
    assert mask_secret(None) == ""                             # None 방어
    assert mask_secret(12345) == ""                            # 비문자열 방어


# --------------------------------------------------------------------------- #
# 드라이버 (단독 실행: python scripts/test_research_credential_masking_pbt.py)
# --------------------------------------------------------------------------- #
_PROPERTIES = [
    ("mask_secret 구조·길이·비노출 불변식", test_mask_secret_structure_and_non_exposure),
    ("mask_secret 규칙 결정성", test_mask_secret_idempotent_shape),
    ("원문 전체 비-부분문자열(로그 비노출)", test_mask_secret_raw_not_substring),
    ("비문자열/None 방어(예외 없음)", test_mask_secret_non_string_defense),
    ("짧은 키(1..4) 전량 마스킹", test_mask_secret_short_key_fully_masked),
    ("웹 캐시 직렬화 자격증명 비노출", test_web_cache_serialization_excludes_credentials),
    ("논문 캐시 직렬화 자격증명 비노출", test_paper_cache_serialization_excludes_credentials),
]


def main() -> int:
    print("Property test: deep-research-engine Property 9 (자격증명 비노출)")
    print()

    print("[smoke] 문서 예시·경계 마스킹 규칙 ...", end=" ", flush=True)
    smoke_known_examples()
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
