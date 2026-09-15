"""deep-research-engine: 질의 정규화 — 순수·멱등 함수 (부작용 없음).

이 모듈은 검색 질의 문자열을 캐시 키·질의 대조에 안전하게 쓰기 위한 정규화
함수를 제공한다. ``normalize_query``(질의 정규화)와 함께 결과 파서
(``parse_search_result``/``parse_paper_result`` — ``Result_Normalizer``)를 노출하며,
이후 task 2.4에서 캐시 직렬화기/역직렬화기(프린터)를 같은 모듈에 추가한다.

Result_Normalizer(파서) 재사용 원칙(재구현 금지 — task 2.3): 제공자별 필드 매핑은
이미 ``providers.py``의 어댑터(``get_web_adapter``의 ``to_search_results``, 학술
어댑터의 ``to_paper_results``)가 갖고 있다. 아래 파서는 단일 원시 항목을 어댑터가
읽는 응답 봉투로 감싸 어댑터의 리스트 매핑에 **위임**하고 첫 결과를 취하므로
normalize.py에 매핑 로직을 복제하지 않는다.

순수성: 네트워크·파일 I/O·전역 상태에 의존하지 않는 순수 함수다(외부 상태
비의존). 동일 입력에 항상 동일 출력을 반환하므로 단위·속성 테스트가 가능하다
(요구사항 4.3 정합).

멱등성(P11): ``normalize_query(normalize_query(q)) == normalize_query(q)``.
연산은 **유니코드 NFKC → 소문자화 → 유니코드 NFKC(재정규화) → 공백 정규화(연속
공백 축약 + 앞뒤 trim)** 순서로 적용한다(NFKC를 소문자화 앞뒤로 두 번 적용).
결과가 **NFKC 고정점**이 되도록 이 순서를 택했다: 첫 NFKC로 호환 문자(전각/합자/
각종 유니코드 공백)를 표준형으로 접고, 소문자화가 도입할 수 있는 비정규 결합 순서
(예: ``İ``(U+0130)→``i``+U+0307)를 **소문자화 이후 두 번째 NFKC로 다시 정규화**해
정규 결합 순서를 복원한다. 이렇게 하면 재적용 시 NFKC가 더 이상 재정렬하지 않아
결과가 불변이 된다. (소문자화 앞에 NFKC를 한 번만 두면 lower()가 만든 결합 마크가
비정규 순서로 남아, 재적용 시 NFKC가 재정렬해 멱등성이 깨진다.)

방어성(P8 정합): ``None``이나 문자열이 아닌 입력은 예외를 던지지 않고 빈
문자열(``""``)로 처리한다.

Python 3.11 표준 라이브러리(``unicodedata``)만 사용하며 신규 의존성을 도입하지
않는다.

Requirements: 4.7, 5.1 (normalize_query) · 4.1, 4.3, 4.5, 1.3, 2.6
              (parse_search_result / parse_paper_result)
Design: "Components and Interfaces" 3) normalize.py, 2) providers.py, 속성 P8/P11
"""

import unicodedata
from dataclasses import asdict, fields

from . import providers
from .models import PaperResult, SearchResult


def normalize_query(q: str) -> str:
    """검색 질의를 캐시 키·대조용 정규형으로 변환한다 (순수·멱등).

    적용 순서(멱등 보장 — P11):
        1. 유니코드 NFKC 정규화 — 호환 분해/재구성으로 전각·합자·호환 공백 등을
           표준형으로 접는다(소문자화가 올바르게 접히도록 선행).
        2. 소문자화 — 대소문자 차이를 제거한다.
        3. 유니코드 NFKC 재정규화 — 소문자화가 도입할 수 있는 비정규 결합 순서(예:
           ``İ``(U+0130)→``i``+U+0307(ccc 230)가 기존 아래결합 U+0316(ccc 220)보다
           앞서 남는 경우)를 다시 표준 결합 순서로 되돌려 결과를 **NFKC 고정점**으로
           만든다. 이 단계가 없으면 재적용 시 NFKC가 재정렬해 멱등성이 깨진다.
        4. 공백 정규화 — 모든 공백 런(스페이스/탭/개행/유니코드 공백)을 단일
           스페이스로 축약하고 앞뒤 공백을 제거한다(``" ".join(s.split())``). 공백은
           모두 시작자(ccc 0)라 이 단계는 앞선 NFKC 고정점 성질을 보존한다.

    Args:
        q: 정규화할 질의 문자열. ``None`` 또는 문자열이 아닌 값은 방어적으로
           빈 문자열로 처리한다(예외 없음 — P8 정합).

    Returns:
        정규화된 질의 문자열. ``normalize_query(normalize_query(q))`` 는
        ``normalize_query(q)`` 와 동일하다(멱등 — P11).
    """
    if not isinstance(q, str):
        return ""
    # 1) 유니코드 NFKC 정규화 (호환 문자·유니코드 공백을 표준형으로 접음)
    s = unicodedata.normalize("NFKC", q)
    # 2) 소문자화 (대소문자 차이 제거)
    s = s.lower()
    # 3) 유니코드 NFKC 재정규화 — 소문자화가 도입한 비정규 결합 순서를 표준으로
    #    되돌려 NFKC 고정점을 만든다(멱등 보장의 핵심). 예: "\u0130\u0316" 는
    #    1)+2) 후 i·U+0307(230)·U+0316(220) 로 결합 순서가 어긋나는데, 여기서
    #    i·U+0316(220)·U+0307(230) 로 재정렬되어 재적용해도 불변이 된다.
    s = unicodedata.normalize("NFKC", s)
    # 4) 공백 축약 + trim (모든 공백 런 → 단일 스페이스, 앞뒤 제거)
    #    str.split()은 인자 없이 호출 시 임의의 공백 런으로 분리하고 앞뒤 공백을
    #    제거하므로, join과 결합하면 축약+trim이 한 번에 이뤄진다. 공백은 모두
    #    시작자(ccc 0)라 3)의 NFKC 고정점 성질을 깨지 않아 재적용해도 불변이다.
    s = " ".join(s.split())
    return s


# --------------------------------------------------------------------------- #
# Result_Normalizer — 단일 원시 항목 파서 (순수 · 예외 없음, P8)
# --------------------------------------------------------------------------- #
# 제공자별 필드 매핑은 재구현하지 않는다(task 2.3 재사용 원칙). providers.py의
# 어댑터가 이미 "원시 응답 → 정규 스키마" 매핑을 갖고 있으므로, 아래 파서는 단일
# 원시 항목을 어댑터가 읽는 "응답 봉투(envelope)"로 감싸 어댑터의 리스트 매핑
# (to_search_results / to_paper_results)에 위임하고 첫 결과를 취한다.
#
# 봉투 설계: 각 어댑터는 자신의 결과 배열 경로만 읽고 다른 키는 무시하므로, 단일
# 항목을 "알려진 모든 경로"에 동시에 노출해도 호출한 어댑터 하나만 정확히 1건을
# 매핑한다(교차 오염 없음). 새 경로를 쓰는 어댑터가 추가되면 아래 봉투 헬퍼에 그
# 경로를 더한다.


def _web_envelope(raw_item: dict) -> dict:
    """단일 웹 원시 항목을 웹 어댑터들의 결과 배열 경로에 노출하는 봉투로 감싼다.

    경로: Tavily/Exa = ``results``, Brave = ``web.results`` (providers.py 정합).
    각 어댑터는 자신의 경로만 읽으므로 어떤 웹 어댑터에 넘겨도 정확히 1건이 매핑된다.
    """
    return {"results": [raw_item], "web": {"results": [raw_item]}}


def _academic_envelope(raw_item: dict) -> dict:
    """단일 논문 원시 항목을 학술 어댑터들의 결과 배열 경로에 노출하는 봉투로 감싼다.

    각 학술 어댑터의 최우선 추출 경로(providers.py ``_result_items`` 정합):
        - Semantic Scholar = ``data``
        - OpenAlex         = ``results``
        - arXiv            = ``entries``
        - PubMed           = ``articles``
    각 어댑터는 자신의 첫 경로만 읽으므로 어떤 학술 어댑터에 넘겨도 정확히 1건이
    매핑된다(교차 오염 없음).
    """
    return {
        "data": [raw_item],       # Semantic Scholar
        "results": [raw_item],    # OpenAlex (+ S2/PubMed 폴백 경로)
        "entries": [raw_item],    # arXiv
        "articles": [raw_item],   # PubMed
    }


def parse_search_result(provider: str, raw_item: dict) -> SearchResult:
    """단일 웹 원시 항목(dict) → 정규 ``SearchResult`` (순수 · 예외 없음 — P8).

    제공자별 필드 매핑은 재구현하지 않고 ``providers.get_web_adapter(provider)``
    어댑터의 리스트 매핑(``to_search_results``)에 단일 항목을 위임한다(재사용 —
    매핑 로직 복제 금지).

    Args:
        provider: 웹 제공자 이름(``"tavily"`` | ``"exa"`` | ``"brave"``).
        raw_item: 제공자 결과 배열의 단일 원시 항목(dict).

    Returns:
        정규 ``SearchResult``. 다음의 경우 예외 없이 기본값 결과(제공자만 채움)를
        반환한다(P8 / Req 1.3, 4.5): ``raw_item``이 dict가 아님/``None``, 미등록
        제공자, 어댑터 매핑이 항목을 산출하지 못함.
    """
    default = SearchResult(provider=provider if isinstance(provider, str) else "")
    if not isinstance(raw_item, dict):
        return default
    try:
        adapter = providers.get_web_adapter(provider)
        results = adapter.to_search_results(_web_envelope(raw_item))
    except Exception:
        # P8: 파서는 어떤 입력에도 예외를 전파하지 않는다(미등록 제공자·매핑 오류 등).
        return default
    return results[0] if results else default


def parse_paper_result(provider: str, raw_item: dict) -> PaperResult:
    """단일 논문 원시 항목(dict) → 정규 ``PaperResult`` (순수 · 예외 없음 — P8).

    제공자별 필드 매핑은 재구현하지 않고 학술 어댑터
    (``providers.get_academic_adapter(provider)``)의 리스트 매핑
    (``to_paper_results``)에 단일 항목을 위임한다(재사용 — 매핑 로직 복제 금지).
    학술 어댑터는 task 3.2에서 providers.py에 추가되며, 아직 없으면(레지스트리/
    제공자 부재) 기본값 결과로 안전하게 폴백한다(무회귀·비차단).

    Args:
        provider: 학술 제공자 이름(``"semantic_scholar"`` | ``"openalex"`` | ...).
        raw_item: 제공자 결과 배열의 단일 원시 항목(dict).

    Returns:
        정규 ``PaperResult``. 다음의 경우 예외 없이 기본값 결과(제공자만 채움)를
        반환한다(P8 / Req 2.6, 4.5): ``raw_item``이 dict가 아님/``None``, 학술
        어댑터 레지스트리/제공자 부재, 어댑터 매핑이 항목을 산출하지 못함.
    """
    default = PaperResult(provider=provider if isinstance(provider, str) else "")
    if not isinstance(raw_item, dict):
        return default
    get_academic = getattr(providers, "get_academic_adapter", None)
    if not callable(get_academic):
        # 학술 어댑터 레지스트리 미도입(task 3.2 이전) — 비차단 기본값 폴백.
        return default
    try:
        adapter = get_academic(provider)
        results = adapter.to_paper_results(_academic_envelope(raw_item))
    except Exception:
        # P8: 파서는 어떤 입력에도 예외를 전파하지 않는다.
        return default
    return results[0] if results else default


# --------------------------------------------------------------------------- #
# 캐시 직렬화기(프린터) / 역직렬화기(파서) — 순수 · 라운드트립 보존 (P1)
# --------------------------------------------------------------------------- #
# serialize_result(프린터)와 deserialize_result(파서)는 정규 결과
# (SearchResult | PaperResult)를 userData 캐시(JSON)에 안전하게 쓰고 되읽기 위한
# 짝이다. 두 함수 모두 네트워크·파일 I/O·전역 상태에 의존하지 않는 순수 함수다.
#
# 라운드트립 불변(P1 / Req 4.4):
#     deserialize_result(serialize_result(r)) 는 r 의 모든 정규 필드 값과 동등하다.
# 직렬화는 dataclass의 모든 정규 필드를 JSON-호환 기본 타입(str/int/float/list)으로
# 저장하고(발행일 ISO 문자열·점수 float·연도/피인용수 int·저자 list 보존), 역직렬화는
# 타입 태그(``_type``)로 원래 dataclass를 복원한다. models의 필드는 이미 JSON-호환
# 스칼라/리스트뿐이라 별도 인코딩 없이 값이 그대로 보존된다.
#
# 타입 태그: 정규 결과에는 명시적 타입 필드가 없으므로, 직렬화 시 ``_type`` 키
# ("search" | "paper")를 덧붙여 역직렬화가 대상 dataclass를 결정할 수 있게 한다.
# ``_type``은 dataclass 필드가 아니므로 역직렬화 시 자동으로 필터링된다.
#
# 방어성(P8 정합): 알 수 없는 입력 타입/누락 필드/알 수 없는 태그에도 예외를 던지지
# 않는다. 누락 필드는 models 기본값으로 채우고(부분 복원), 태그가 없거나 알 수 없으면
# 논문 고유 필드 유무로 타입을 추론해 안전하게 폴백한다.
#
# Python 3.11 표준 ``dataclasses``(asdict/fields)만 사용하며 신규 의존성이 없다.

_SEARCH_TAG = "search"
_PAPER_TAG = "paper"
_TYPE_KEY = "_type"

# 논문 결과에만 존재하는 정규 필드(웹 결과에는 없음) — 태그 부재 시 타입 추론용.
_PAPER_ONLY_FIELDS = frozenset(
    {f.name for f in fields(PaperResult)} - {f.name for f in fields(SearchResult)}
)


def serialize_result(r):
    """정규 결과(``SearchResult`` | ``PaperResult``) → JSON-호환 dict (프린터, 순수).

    dataclass의 모든 정규 필드를 ``dataclasses.asdict``로 JSON-호환 기본 타입으로
    변환한 뒤, 역직렬화가 대상 타입을 복원할 수 있도록 타입 태그(``_type``)를
    덧붙인다. 발행일 ISO 문자열·점수 float·연도/피인용수 int·저자 list 등 타입이
    그대로 보존된다(P1 / Req 4.2, 4.4).

    Args:
        r: 직렬화할 정규 결과(``SearchResult`` 또는 ``PaperResult``).

    Returns:
        모든 정규 필드 + ``{"_type": "search"|"paper"}``를 담은 JSON-호환 dict.
        ``r``이 정규 결과 dataclass가 아니면 예외 없이 빈 dict(``{}``)를 반환한다
        (P8 정합).
    """
    if isinstance(r, PaperResult):
        tag = _PAPER_TAG
    elif isinstance(r, SearchResult):
        tag = _SEARCH_TAG
    else:
        # P8: 정규 결과가 아닌 입력에도 예외를 전파하지 않는다.
        return {}
    d = asdict(r)          # 모든 필드를 JSON-호환 기본 타입으로(리스트는 복사됨)
    d[_TYPE_KEY] = tag     # 타입 복원용 태그
    return d


def deserialize_result(d):
    """JSON-호환 dict → 정규 결과(``SearchResult`` | ``PaperResult``) (파서, 순수).

    타입 태그(``_type``)로 대상 dataclass를 복원하고, dict에서 해당 dataclass의
    알려진 필드만 취해 인스턴스를 생성한다. 누락 필드는 models 기본값으로 채우며
    (부분 복원), ``_type``처럼 dataclass 필드가 아닌 키는 무시한다.

    ``serialize_result``와 짝을 이뤄 라운드트립을 보존한다(P1 / Req 4.4):
    ``deserialize_result(serialize_result(r))`` 는 ``r``의 모든 정규 필드와 동등하다.

    Args:
        d: ``serialize_result``가 산출한(또는 그와 동형의) JSON-호환 dict.

    Returns:
        복원된 ``SearchResult`` 또는 ``PaperResult``. 다음의 경우에도 예외 없이
        안전하게 복원/폴백한다(P8 정합): ``d``가 dict가 아니면 기본
        ``SearchResult`` 반환, ``_type``이 없거나 알 수 없으면 논문 고유 필드 유무로
        타입 추론, 알 수 없는 키는 무시.
    """
    if not isinstance(d, dict):
        # P8: dict가 아닌 입력에도 예외를 전파하지 않는다.
        return SearchResult()
    tag = d.get(_TYPE_KEY)
    if tag == _PAPER_TAG:
        cls = PaperResult
    elif tag == _SEARCH_TAG:
        cls = SearchResult
    else:
        # 태그 부재/불명: 논문 고유 필드가 하나라도 있으면 PaperResult로 추론.
        cls = PaperResult if _PAPER_ONLY_FIELDS & d.keys() else SearchResult
    known = {f.name for f in fields(cls)}
    kwargs = {k: v for k, v in d.items() if k in known}
    return cls(**kwargs)
