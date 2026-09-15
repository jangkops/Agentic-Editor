# Feature: deep-research-engine, Property 1: 정규화 결과 직렬화 라운드트립
"""Property 1 — 정규화 결과 직렬화 라운드트립 property 테스트.

*For any* 정규화된 ``SearchResult`` 또는 ``PaperResult`` ``r`` 에 대해,
``deserialize_result(serialize_result(r))`` 는 ``r`` 의 모든 정규 필드 값과
동등하다(파서/프린터 왕복이 정보를 보존한다). 나아가 캐시는 JSON으로 영속되므로
``json.dumps``/``json.loads`` 왕복을 거쳐도 동등함을 함께 검증해 타입 보존
(발행일 ISO 문자열·점수 float·연도/피인용수 int·저자 list)을 보장한다.

**Validates: Requirements 4.4**

- 검증 컴포넌트: ``ai_engine/research/normalize.py``
  (``serialize_result`` / ``deserialize_result``)
- 생성기: 두 정규 타입의 모든 필드를 임의로 채운다 — 빈/특수문자/유니코드/제어문자
  문자열, 리스트 저자, 정수(음수·0·초대형) 및 실수 경계값.

NaN/±무한대 제외 근거: 이 속성은 "직렬화가 값을 보존하는가"에 관한 것이다. NaN은
``float('nan') != float('nan')`` 이라 (왕복이 값을 온전히 보존해도) dataclass 동등
비교가 성립하지 않고, ±무한대는 엄격 JSON(``allow_nan=False``) 호환성 문제를 부른다.
두 경우 모두 직렬화 정확성이 아니라 float 동등/JSON 엄격성의 문제이므로, 관련성
점수 생성기에서 제외해 속성의 본질(정보 보존)만 결정적으로 검증한다.

Stack: Python 3.11+, hypothesis. 단발 실행(워치 모드 금지):
    python -m pytest scripts/test_research_normalize_roundtrip_pbt.py -q
    python scripts/test_research_normalize_roundtrip_pbt.py
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

# 스크립트를 직접 실행할 때도 ai_engine 패키지를 import 할 수 있게 repo 루트를 경로에 추가.
_REPO_ROOT = Path(__file__).resolve().parent.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from hypothesis import given, settings  # noqa: E402
from hypothesis import strategies as st  # noqa: E402

from ai_engine.research.models import PaperResult, SearchResult  # noqa: E402
from ai_engine.research.normalize import (  # noqa: E402
    deserialize_result,
    serialize_result,
)

# --------------------------------------------------------------------------- #
# 생성기(strategies) — 두 정규 타입의 모든 필드를 임의로 채운다.
# --------------------------------------------------------------------------- #

# 문자열: 빈 문자열 + 특수문자/유니코드/제어문자를 폭넓게 포함한다.
# 짝 없는 서로게이트(Cs)만 제외한다(UTF-8 인코딩 불가 — 직렬화와 무관한 잡음 방지).
_text = st.text(alphabet=st.characters(exclude_categories=("Cs",)), min_size=0, max_size=50)

# 관련성 점수: 유한 실수 경계(0.0/-0.0/초대·초소 크기/음수)를 포함하되 NaN·무한대 제외.
_score = st.floats(allow_nan=False, allow_infinity=False)

# 정수 필드(year/citation_count): 음수·0·초대형을 자연히 아우르는 무경계 정수.
# (Python 정수는 임의정밀이며 JSON 왕복에서도 값이 보존된다.)
_int = st.integers()

# 저자 목록: 임의 개수(0 포함)의 임의 문자열.
_authors = st.lists(_text, min_size=0, max_size=6)


@st.composite
def search_results(draw) -> SearchResult:
    """모든 필드를 임의로 채운 정규 ``SearchResult`` 생성기."""
    return SearchResult(
        title=draw(_text),
        url=draw(_text),
        snippet=draw(_text),
        published_date=draw(_text),
        source_domain=draw(_text),
        relevance_score=draw(_score),
        provider=draw(_text),
        source_id=draw(_text),
    )


@st.composite
def paper_results(draw) -> PaperResult:
    """모든 필드를 임의로 채운 정규 ``PaperResult`` 생성기."""
    return PaperResult(
        title=draw(_text),
        authors=draw(_authors),
        year=draw(_int),
        venue=draw(_text),
        abstract=draw(_text),
        doi_or_url=draw(_text),
        citation_count=draw(_int),
        relevance_score=draw(_score),
        provider=draw(_text),
        source_id=draw(_text),
    )


# 두 정규 타입 중 하나를 임의로 생성.
_results = st.one_of(search_results(), paper_results())


@settings(max_examples=200)
@given(r=_results)
def test_serialize_deserialize_roundtrip(r) -> None:
    """직렬화 후 역직렬화하면 원본과 모든 정규 필드가 동등하다(P1, 타입 보존 포함)."""
    serialized = serialize_result(r)

    # 1) 인메모리 왕복: deserialize(serialize(r)) == r (dataclass 동등 = 클래스+전 필드).
    back = deserialize_result(serialized)
    assert type(back) is type(r), (
        f"타입 미보존: {type(r).__name__} → {type(back).__name__}; serialized={serialized!r}"
    )
    assert back == r, f"인메모리 라운드트립 불일치: back={back!r} r={r!r}"

    # 2) JSON 왕복: 캐시는 JSON으로 영속되므로 dumps/loads를 거쳐도 동등해야 한다
    #    (발행일 str·점수 float·연도/피인용수 int·저자 list 등 타입 보존 검증).
    reloaded = json.loads(json.dumps(serialized))
    back_json = deserialize_result(reloaded)
    assert type(back_json) is type(r), (
        f"JSON 왕복 타입 미보존: {type(r).__name__} → {type(back_json).__name__}"
    )
    assert back_json == r, f"JSON 라운드트립 불일치: back={back_json!r} r={r!r}"


if __name__ == "__main__":
    # 단발 실행 드라이버(워치 모드 금지). 예외 발생 시 hypothesis가 counterexample을 출력한다.
    test_serialize_deserialize_roundtrip()
    print("PASSED: Property 1 — 정규화 결과 직렬화 라운드트립 (max_examples=200)")
