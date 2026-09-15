"""deep-research-engine: 검색 제공자 어댑터 — 순수 매핑 (egress 없음).

이기종 웹 검색 제공자(Tavily/Exa/Brave)의 **원시 응답(dict) → 정규
``SearchResult``** 변환만 담당하는 순수 계층이다. 실제 HTTP egress(네트워크
호출)는 전부 ``backend.py``가 수행하며(요구사항 10.4), 이 모듈에는 네트워크·
파일 I/O·전역 상태가 전혀 없다. Python 3.11 표준 라이브러리(``math``/``typing``/
``urllib.parse``)와 사내 재사용(``models.SearchResult``/``dedup.canonical_url``)만
사용한다(신규 의존성 없음).

어댑터 인터페이스(교체 가능성 — 요구사항 16.5):
    모든 웹 제공자는 ``WebSearchAdapter`` 프로토콜(``name`` + ``to_search_results``)
    뒤에 두어 설정만으로 교체·추가·비활성화할 수 있다. 어댑터 조회는
    ``get_web_adapter(name)`` 팩토리로 수행한다.

웹 제공자 → SearchResult 매핑 표 (design.md "Data Models" 절 정합):

    | 정규 필드         | Tavily                 | Exa                     | Brave                          |
    |------------------|------------------------|-------------------------|--------------------------------|
    | title            | results[].title        | results[].title         | web.results[].title            |
    | url              | results[].url          | results[].url           | web.results[].url              |
    | snippet          | results[].content      | results[].text/highlights | web.results[].description    |
    | published_date   | results[].published_date | results[].publishedDate | web.results[].page_age/age   |
    | source_domain    | urlparse(url).netloc   | urlparse(url).netloc    | urlparse(url).netloc           |
    | relevance_score  | results[].score        | results[].score         | 순위 파생 1/(rank+1)            |

    공통: ``source_domain`` = ``urlparse(url).netloc`` 파생, ``source_id`` =
    ``"web:" + canonical_url(url)`` (``dedup.canonical_url`` 재사용 — dedup 키
    규약과 정합, P6), ``provider`` = 어댑터 ``name``.

방어성(P8 / 요구사항 1.3): ``raw``가 dict가 아니거나 결과 배열이 없거나 개별
항목의 텍스트 필드가 누락돼도 예외를 던지지 않는다. 누락 텍스트 필드는 빈
문자열, 관련성 점수는 정렬 가능한 수치(``0.0`` 또는 순위 파생값)로 채운
``SearchResult``를 생성한다(models 기본값 정합).

학술(논문) 어댑터도 동일한 순수 매핑 계층으로 이 모듈에 함께 노출한다. 모든
학술 제공자는 ``AcademicSearchAdapter`` 프로토콜(``name`` + ``to_paper_results``)
뒤에 두어 설정만으로 교체·추가·비활성화할 수 있으며, 어댑터 조회는
``get_academic_adapter(name)`` 팩토리로 수행한다(웹 어댑터와 동일 패턴).

학술 제공자 → PaperResult 매핑 표 (design.md "Data Models" 절 정합):

    | 정규 필드        | Semantic Scholar        | OpenAlex                             | arXiv               | PubMed                |
    |-----------------|-------------------------|--------------------------------------|---------------------|-----------------------|
    | title           | title                   | display_name                         | entry.title         | ArticleTitle          |
    | authors         | authors[].name          | authorships[].author.display_name    | entry.author[].name | AuthorList[].(성명)   |
    | year            | year                    | publication_year                     | published(연도)      | PubDate.Year          |
    | venue           | venue                   | primary_location.source.display_name | "arXiv"             | Journal.Title         |
    | abstract        | abstract                | abstract_inverted_index(재구성)       | entry.summary       | Abstract.AbstractText |
    | doi_or_url      | externalIds.DOI / url   | doi / id                             | entry.id(arXiv URL) | ELocationID(doi)/PMID |
    | citation_count  | citationCount           | cited_by_count                       | 0(미제공)            | 0(미제공)             |
    | relevance_score | 검색 순위/스코어         | relevance_score                      | 순위 파생            | 순위 파생             |

    공통: DOI가 있으면 ``source_id`` = ``"doi:" + canonical_doi(doi)``, 없으면
    ``"web:" + canonical_url(url)`` (``dedup`` 키 규약 재사용 — P6). 명시 관련성
    점수가 없는 제공자(arXiv/PubMed)와 명시 점수 부재 시에는 순위 파생값
    ``1/(rank+1)``로 채워 결정적 정렬을 보장한다(P7). OpenAlex 초록은 역인덱스
    (``abstract_inverted_index``: {단어: [위치…]})를 위치 오름차순으로 재구성한다.

방어성(P8 / 요구사항 2.6): 학술 어댑터도 웹 어댑터와 동일하게 ``raw``가 dict가
아니거나 결과 배열/개별 필드가 누락돼도 예외를 던지지 않는다. 누락 텍스트 필드는
빈 문자열/빈 리스트, 발행연도·피인용수는 정렬 가능한 ``0``으로 채운
``PaperResult``를 생성한다(models 기본값 정합).

Requirements: 1.2, 2.2, 4.1
Design: "Components and Interfaces" 2) providers.py, "Data Models > 웹 제공자 →
        Search_Result 매핑 표" 및 "학술 제공자 → Paper_Result 매핑 표",
        "어댑터 인터페이스"
Property: P8 (오류조건 비차단 — 부분 필드 누락 시 기본값 정규 결과)
"""

import math
import re
from typing import Protocol, runtime_checkable
from urllib.parse import urlparse

from .dedup import canonical_doi, canonical_url
from .models import PaperResult, SearchResult

__all__ = [
    "WebSearchAdapter",
    "TavilyAdapter",
    "ExaAdapter",
    "BraveAdapter",
    "get_web_adapter",
    "available_web_adapters",
    "AcademicSearchAdapter",
    "SemanticScholarAdapter",
    "OpenAlexAdapter",
    "ArxivAdapter",
    "PubMedAdapter",
    "get_academic_adapter",
    "available_academic_adapters",
]


# --------------------------------------------------------------------------- #
# 어댑터 프로토콜 (교체 가능성 — 요구사항 16.5)
# --------------------------------------------------------------------------- #
@runtime_checkable
class WebSearchAdapter(Protocol):
    """웹 검색 제공자 어댑터 프로토콜 (순수 매핑).

    구현체는 고유한 ``name``과, 제공자 원시 응답(dict)을 정규 ``SearchResult``
    목록으로 변환하는 ``to_search_results``를 노출한다. egress는 포함하지 않는다.
    """

    name: str

    def to_search_results(self, raw: dict) -> list[SearchResult]:
        """제공자 원시 응답(dict) → 정규 ``SearchResult`` 목록(순수·예외 없음)."""
        ...


# --------------------------------------------------------------------------- #
# 내부 헬퍼 (순수 · 방어적 — 예외 없음)
# --------------------------------------------------------------------------- #
def _as_str(value, default: str = "") -> str:
    """값을 문자열로 안전 변환한다. ``None``은 기본값, 그 외는 ``str()`` 적용."""
    if value is None:
        return default
    if isinstance(value, str):
        return value
    return str(value)


def _as_float(value, default: float = 0.0) -> float:
    """값을 정렬 가능한 float로 안전 변환한다(결측/비수치/NaN → 기본값).

    ``rank.py``의 결측 처리 규약과 정합한다(P7/P8): 비교가 비결정적인 ``NaN``과
    변환 불가 값은 모두 기본값으로 접어 결정적 정렬 입력을 보장한다.
    """
    if value is None:
        return default
    try:
        f = float(value)
    except (TypeError, ValueError):
        return default
    return default if math.isnan(f) else f


def _domain_of(url) -> str:
    """URL에서 출처 도메인(``urlparse(url).netloc``)을 파생한다(예외 없음).

    스킴이 없거나 파싱 불가한 입력은 빈 문자열로 폴백한다(P8).
    """
    if not isinstance(url, str) or not url.strip():
        return ""
    try:
        return urlparse(url).netloc
    except ValueError:
        return ""


def _first_nonempty(*values):
    """인자 중 첫 번째 "비어있지 않은" 값을 반환한다(모두 비면 ``""``).

    ``None`` 및 공백뿐인 문자열은 비어있는 것으로 간주한다. Brave의
    ``page_age``/``age`` 폴백 등에 사용한다.
    """
    for v in values:
        if v is None:
            continue
        if isinstance(v, str) and not v.strip():
            continue
        return v
    return ""


def _result_list(raw, *path) -> list:
    """``raw``(dict)에서 중첩 경로를 따라가 결과 배열을 안전하게 추출한다.

    경로 중간 노드가 dict가 아니거나 최종 노드가 list가 아니면 빈 리스트를
    반환한다(예외 없음 — P8). 예: Tavily ``_result_list(raw, "results")``,
    Brave ``_result_list(raw, "web", "results")``.
    """
    node = raw
    for key in path:
        if not isinstance(node, dict):
            return []
        node = node.get(key)
    return node if isinstance(node, list) else []


def _build_result(
    *,
    title,
    url,
    snippet,
    published_date,
    relevance_score,
    provider: str,
) -> SearchResult:
    """추출된 필드로 정규 ``SearchResult``를 조립한다(공통 빌더).

    ``source_domain``은 ``urlparse(url).netloc``, ``source_id``는
    ``"web:" + canonical_url(url)``(``dedup`` 키 규약 정합)로 파생한다. 누락
    필드는 models 기본값(빈 문자열/``0.0``)으로 채워 예외 없이 생성한다(P8).
    """
    url_s = _as_str(url)
    return SearchResult(
        title=_as_str(title),
        url=url_s,
        snippet=_as_str(snippet),
        published_date=_as_str(published_date),
        source_domain=_domain_of(url_s),
        relevance_score=_as_float(relevance_score),
        provider=provider,
        source_id="web:" + canonical_url(url_s),
    )


def _exa_snippet(item: dict) -> str:
    """Exa 항목의 발췌문을 파생한다: ``text`` 우선, 없으면 ``highlights``.

    ``highlights``가 문자열 조각 리스트면 공백으로 연결하고, 단일 문자열이면
    그대로 사용한다. 둘 다 없으면 빈 문자열(P8).
    """
    text = item.get("text")
    if isinstance(text, str) and text.strip():
        return text
    highlights = item.get("highlights")
    if isinstance(highlights, list):
        parts = [str(h) for h in highlights if h is not None and str(h).strip()]
        return " ".join(parts)
    if isinstance(highlights, str):
        return highlights
    return ""


# --------------------------------------------------------------------------- #
# 웹 검색 어댑터 (Tavily / Exa / Brave)
# --------------------------------------------------------------------------- #
class TavilyAdapter:
    """Tavily 원시 응답 → ``SearchResult`` 매핑 어댑터 (순수).

    Tavily는 ``results[]`` 배열의 각 항목에 ``title``/``url``/``content``(발췌)/
    ``published_date``(있으면)/``score``(관련성)를 제공한다. 발췌문은 ``content``,
    관련성 점수는 ``score``를 사용한다(design 매핑 표).
    """

    name = "tavily"

    def to_search_results(self, raw: dict) -> list[SearchResult]:
        out: list[SearchResult] = []
        for item in _result_list(raw, "results"):
            if not isinstance(item, dict):
                continue
            out.append(
                _build_result(
                    title=item.get("title"),
                    url=item.get("url"),
                    snippet=item.get("content"),
                    published_date=item.get("published_date"),
                    relevance_score=item.get("score"),
                    provider=self.name,
                )
            )
        return out


class ExaAdapter:
    """Exa 원시 응답 → ``SearchResult`` 매핑 어댑터 (순수).

    Exa는 ``results[]`` 배열의 각 항목에 ``title``/``url``/``text``(본문)
    또는 ``highlights``(하이라이트 조각)/``publishedDate``/``score``를 제공한다.
    발췌문은 ``text`` 우선, 없으면 ``highlights``를 사용한다(design 매핑 표).
    """

    name = "exa"

    def to_search_results(self, raw: dict) -> list[SearchResult]:
        out: list[SearchResult] = []
        for item in _result_list(raw, "results"):
            if not isinstance(item, dict):
                continue
            out.append(
                _build_result(
                    title=item.get("title"),
                    url=item.get("url"),
                    snippet=_exa_snippet(item),
                    published_date=item.get("publishedDate"),
                    relevance_score=item.get("score"),
                    provider=self.name,
                )
            )
        return out


class BraveAdapter:
    """Brave 원시 응답 → ``SearchResult`` 매핑 어댑터 (순수).

    Brave는 ``web.results[]`` 배열의 각 항목에 ``title``/``url``/``description``
    (발췌)/``page_age`` 또는 ``age``(발행 정보)를 제공하나 **네이티브 관련성
    점수가 없다.** 따라서 관련성 점수는 결과 순위(0-based)에서 ``1/(rank+1)``로
    파생한다(design 매핑 표): 첫 결과 ``1.0``, 둘째 ``0.5`` …로 단조 감소한다.
    """

    name = "brave"

    def to_search_results(self, raw: dict) -> list[SearchResult]:
        out: list[SearchResult] = []
        for rank, item in enumerate(_result_list(raw, "web", "results")):
            if not isinstance(item, dict):
                continue
            published = _first_nonempty(item.get("page_age"), item.get("age"))
            out.append(
                _build_result(
                    title=item.get("title"),
                    url=item.get("url"),
                    snippet=item.get("description"),
                    published_date=published,
                    relevance_score=1.0 / (rank + 1),  # 순위 파생(네이티브 점수 부재)
                    provider=self.name,
                )
            )
        return out


# --------------------------------------------------------------------------- #
# 어댑터 레지스트리 / 팩토리
# --------------------------------------------------------------------------- #
# 어댑터는 상태가 없는(stateless) 순수 매핑이므로 인스턴스를 재사용해도 안전하다.
_WEB_ADAPTERS: dict[str, WebSearchAdapter] = {
    TavilyAdapter.name: TavilyAdapter(),
    ExaAdapter.name: ExaAdapter(),
    BraveAdapter.name: BraveAdapter(),
}


def available_web_adapters() -> list[str]:
    """등록된 웹 검색 어댑터 이름 목록을 정렬해 반환한다."""
    return sorted(_WEB_ADAPTERS)


def get_web_adapter(name: str) -> WebSearchAdapter:
    """이름으로 웹 검색 어댑터를 조회한다(대소문자·앞뒤 공백 무시).

    Args:
        name: 제공자 이름(``"tavily"`` | ``"exa"`` | ``"brave"``).

    Returns:
        해당 ``WebSearchAdapter`` 인스턴스(stateless — 재사용 안전).

    Raises:
        ValueError: 등록되지 않은 제공자 이름인 경우(등록 목록을 메시지에 포함).
    """
    key = name.strip().lower() if isinstance(name, str) else ""
    adapter = _WEB_ADAPTERS.get(key)
    if adapter is None:
        available = ", ".join(available_web_adapters())
        raise ValueError(
            f"Unknown web search adapter: {name!r}. Available: {available}"
        )
    return adapter


# =========================================================================== #
# 학술/논문 검색 어댑터 (Semantic Scholar / OpenAlex / arXiv / PubMed)
#
# 웹 어댑터와 동일한 순수 매핑 계층이다: 제공자 원시 응답(dict) → 정규
# ``PaperResult`` 목록 변환만 담당하며 egress·파일 I/O·전역 상태가 없다.
# 매핑 규칙은 모듈 상단 docstring의 "학술 제공자 → PaperResult 매핑 표" 참조.
# =========================================================================== #
_YEAR_RE = re.compile(r"\d{4}")


# --------------------------------------------------------------------------- #
# 어댑터 프로토콜 (교체 가능성 — 요구사항 16.5)
# --------------------------------------------------------------------------- #
@runtime_checkable
class AcademicSearchAdapter(Protocol):
    """학술 검색 제공자 어댑터 프로토콜 (순수 매핑).

    구현체는 고유한 ``name``과, 제공자 원시 응답(dict)을 정규 ``PaperResult``
    목록으로 변환하는 ``to_paper_results``를 노출한다. egress는 포함하지 않는다.
    """

    name: str

    def to_paper_results(self, raw: dict) -> list[PaperResult]:
        """제공자 원시 응답(dict) → 정규 ``PaperResult`` 목록(순수·예외 없음)."""
        ...


# --------------------------------------------------------------------------- #
# 학술 전용 내부 헬퍼 (순수 · 방어적 — 예외 없음)
# --------------------------------------------------------------------------- #
def _as_int(value, default: int = 0) -> int:
    """값을 정렬 가능한 int로 안전 변환한다(결측/비수치/NaN → 기본값).

    피인용수·발행연도 등 수치 필드의 결측을 정렬 가능한 기본값으로 접어 결정적
    정렬 입력을 보장한다(P7/P8). ``bool``은 수치로 취급하지 않는다.
    """
    if isinstance(value, bool):
        return default
    if isinstance(value, int):
        return value
    if isinstance(value, float):
        return default if math.isnan(value) else int(value)
    if isinstance(value, str):
        s = value.strip()
        if s.lstrip("+-").isdigit():
            try:
                return int(s)
            except ValueError:
                return default
    return default


def _extract_year(value) -> int:
    """발행연도를 정렬 가능한 int로 추출한다(미상/파싱 불가 → 0).

    int/float는 그대로(정수화), 문자열은 첫 4자리 연속 숫자를 취한다
    (예: ``"2023-05-01T00:00:00Z"`` → 2023, ``"2020 Jan-Feb"`` → 2020). P8.
    """
    if isinstance(value, bool):
        return 0
    if isinstance(value, int):
        return value
    if isinstance(value, float):
        return 0 if math.isnan(value) else int(value)
    m = _YEAR_RE.search(_as_str(value))
    return int(m.group()) if m else 0


def _get_path(node, *keys):
    """중첩 dict 경로를 안전하게 탐색한다(각 단계가 dict가 아니면 ``None``)."""
    for k in keys:
        if not isinstance(node, dict):
            return None
        node = node.get(k)
    return node


def _xml_text(value) -> str:
    """XML→dict(xmltodict 스타일) 값을 평문 문자열로 환원한다(P8).

    문자열은 그대로, ``{"#text": ...}`` dict는 ``#text``를, list는 각 조각을
    재귀 변환해 공백으로 연결한다. ``None``/기타는 안전 변환한다. PubMed의
    ``AbstractText``가 섹션 리스트로 오는 경우 등을 흡수한다.
    """
    if value is None:
        return ""
    if isinstance(value, str):
        return value
    if isinstance(value, dict):
        return _as_str(value.get("#text"))
    if isinstance(value, list):
        parts = [_xml_text(v) for v in value]
        return " ".join(p for p in parts if p.strip())
    return _as_str(value)


def _result_items(raw, *paths) -> list:
    """여러 후보 컨테이너 경로 중 첫 번째로 발견되는 항목 목록을 반환한다(P8).

    각 경로를 중첩 dict로 따라가 노드를 얻고, list면 그대로, 단일 dict면
    ``[dict]``로 감싸(단일 항목 XML 응답 방어) 반환한다. 모두 실패하면 빈
    리스트. 예: Semantic Scholar ``("data",)``, OpenAlex ``("results",)``,
    arXiv ``("entries",)``/``("feed","entry")``, PubMed
    ``("PubmedArticleSet","PubmedArticle")``.
    """
    for path in paths:
        node = raw
        ok = True
        for key in path:
            if not isinstance(node, dict):
                ok = False
                break
            node = node.get(key)
        if not ok or node is None:
            continue
        if isinstance(node, list):
            return node
        if isinstance(node, dict):
            return [node]
    return []


def _score_or_rank(explicit, rank: int) -> float:
    """명시 관련성 점수가 유효 수치면 사용, 아니면 순위 파생 ``1/(rank+1)``(P7).

    OpenAlex ``relevance_score``·Semantic Scholar ``score`` 등 제공자 점수가
    있으면 그대로 쓰고, 없거나(``None``) 비수치/``NaN``이면 순위(0-based)에서
    단조 감소하는 파생 점수를 부여해 제공자 반환 순서를 결정적으로 보존한다.
    """
    if explicit is not None:
        try:
            f = float(explicit)
        except (TypeError, ValueError):
            f = math.nan
        if not math.isnan(f):
            return f
    return 1.0 / (rank + 1)


def _as_author_names(raw_authors, extract) -> list[str]:
    """제공자별 저자 컨테이너를 정규 저자명 리스트로 변환한다(순서 보존, P8).

    ``raw_authors``는 list(정상), 단일 항목, 또는 ``None``일 수 있다.
    ``extract``는 개별 저자 항목에서 이름 후보를 뽑는 제공자별 콜러블이며,
    결과를 문자열화·strip 후 빈 값은 제외한다.
    """
    if raw_authors is None:
        return []
    items = raw_authors if isinstance(raw_authors, list) else [raw_authors]
    out: list[str] = []
    for a in items:
        name = _as_str(extract(a)).strip()
        if name:
            out.append(name)
    return out


def _reconstruct_inverted_abstract(inv_index) -> str:
    """OpenAlex ``abstract_inverted_index``({단어: [위치…]})를 평문으로 재구성한다.

    각 단어를 등장 위치들에 배치한 뒤 위치 오름차순으로 연결한다. dict가
    아니거나 위치가 정수가 아닌 항목은 건너뛰어 예외 없이 처리한다(P8). 동일
    위치는 삽입(사전) 순서를 안정 유지한다.
    """
    if not isinstance(inv_index, dict) or not inv_index:
        return ""
    positioned: list[tuple[int, str]] = []
    for word, positions in inv_index.items():
        if not isinstance(positions, list):
            continue
        for pos in positions:
            if isinstance(pos, bool):  # bool은 int 서브클래스 — 위치로 배제
                continue
            if isinstance(pos, int):
                positioned.append((pos, _as_str(word)))
    if not positioned:
        return ""
    positioned.sort(key=lambda t: t[0])
    return " ".join(word for _, word in positioned)


def _build_paper(
    *,
    title,
    authors,
    year,
    venue,
    abstract,
    doi_candidate,
    url_candidate,
    citation_count,
    relevance_score,
    provider: str,
) -> PaperResult:
    """추출된 필드로 정규 ``PaperResult``를 조립한다(공통 빌더).

    ``source_id``는 DOI가 있으면 ``"doi:" + canonical_doi(doi)``, 없으면
    ``"web:" + canonical_url(url)``로 파생한다(``dedup`` 키 규약 재사용 — P6).
    ``doi_or_url``에는 사람이 읽는 원본 참조(DOI 우선, 없으면 URL)를 담는다.
    누락 필드는 models 기본값(빈 문자열/빈 리스트/``0``)으로 채운다(P8).
    """
    doi_c = canonical_doi(doi_candidate)
    if doi_c:
        source_id = "doi:" + doi_c
        doi_or_url = _as_str(doi_candidate) or doi_c
    else:
        url_s = _as_str(url_candidate)
        source_id = "web:" + canonical_url(url_s)
        doi_or_url = url_s
    return PaperResult(
        title=_as_str(title),
        authors=list(authors) if isinstance(authors, list) else [],
        year=_extract_year(year),
        venue=_as_str(venue),
        abstract=_as_str(abstract),
        doi_or_url=doi_or_url,
        citation_count=_as_int(citation_count),
        relevance_score=_as_float(relevance_score),
        provider=provider,
        source_id=source_id,
    )


# --- 제공자별 저자 이름 추출기 (개별 항목 → 이름 후보) --- #
def _s2_author(a):
    """Semantic Scholar 저자 항목(``{"name": ...}``)에서 이름을 뽑는다."""
    return a.get("name") if isinstance(a, dict) else a


def _openalex_author(a):
    """OpenAlex authorship(``{"author": {"display_name": ...}}``)에서 이름을 뽑는다."""
    if not isinstance(a, dict):
        return a
    name = _get_path(a, "author", "display_name")
    if name:
        return name
    return a.get("raw_author_name")


def _arxiv_author(a):
    """arXiv 저자 항목(``{"name": ...}`` 또는 문자열)에서 이름을 뽑는다."""
    return a.get("name") if isinstance(a, dict) else a


# --- PubMed 전용 헬퍼 (깊은 XML 중첩 방어) --- #
def _pubmed_article_node(item):
    """PubMed 항목에서 ``Article`` 노드를 얻는다(평탄화된 dict면 그대로)."""
    art = _get_path(item, "MedlineCitation", "Article")
    if isinstance(art, dict):
        return art
    return item if isinstance(item, dict) else {}


def _pubmed_pmid(item, art) -> str:
    """PubMed PMID를 문자열로 추출한다(``MedlineCitation.PMID`` 우선)."""
    pmid = _get_path(item, "MedlineCitation", "PMID")
    if pmid is None and isinstance(item, dict):
        pmid = item.get("PMID")
    if pmid is None and isinstance(art, dict):
        pmid = art.get("PMID")
    return _xml_text(pmid).strip()


def _pubmed_year(art) -> str:
    """PubMed 발행연도 문자열을 추출한다(``PubDate.Year``, 없으면 ``MedlineDate``)."""
    pub = _get_path(art, "Journal", "JournalIssue", "PubDate")
    if isinstance(pub, dict):
        y = _xml_text(pub.get("Year"))
        if not y.strip():
            y = _xml_text(pub.get("MedlineDate"))
        return y
    return ""


def _pubmed_doi(art) -> str:
    """PubMed ``ELocationID`` 중 ``EIdType="doi"`` 값을 추출한다(없으면 "")."""
    if not isinstance(art, dict):
        return ""
    eloc = art.get("ELocationID")
    items = eloc if isinstance(eloc, list) else [eloc]
    for e in items:
        if isinstance(e, dict) and str(e.get("@EIdType", "")).lower() == "doi":
            t = _xml_text(e).strip()
            if t:
                return t
    return ""


def _pubmed_author_name(a):
    """PubMed 저자(``LastName``/``ForeName``/``CollectiveName``)를 정규 이름으로 조합한다."""
    if not isinstance(a, dict):
        return a
    collective = _xml_text(a.get("CollectiveName")).strip()
    if collective:
        return collective
    last = _xml_text(a.get("LastName")).strip()
    fore = _xml_text(a.get("ForeName")).strip() or _xml_text(a.get("Initials")).strip()
    return (fore + " " + last).strip()


# --------------------------------------------------------------------------- #
# 학술 검색 어댑터 (Semantic Scholar / OpenAlex / arXiv / PubMed)
# --------------------------------------------------------------------------- #
class SemanticScholarAdapter:
    """Semantic Scholar 원시 응답 → ``PaperResult`` 매핑 어댑터 (순수).

    S2 검색 응답은 ``data[]`` 배열의 각 항목에 ``title``/``authors[].name``/
    ``year``/``venue``/``abstract``/``externalIds.DOI``/``url``/``citationCount``를
    제공한다(design 매핑 표). DOI는 ``externalIds.DOI``, 없으면 ``url``을 참조로
    쓴다. 관련성 점수는 명시 ``score``가 있으면 사용, 없으면 순위 파생.
    """

    name = "semantic_scholar"

    def to_paper_results(self, raw: dict) -> list[PaperResult]:
        out: list[PaperResult] = []
        for rank, item in enumerate(
            _result_items(raw, ("data",), ("results",), ("papers",))
        ):
            if not isinstance(item, dict):
                continue
            external = item.get("externalIds")
            doi = external.get("DOI") if isinstance(external, dict) else None
            out.append(
                _build_paper(
                    title=item.get("title"),
                    authors=_as_author_names(item.get("authors"), _s2_author),
                    year=item.get("year"),
                    venue=item.get("venue"),
                    abstract=item.get("abstract"),
                    doi_candidate=doi,
                    url_candidate=item.get("url"),
                    citation_count=item.get("citationCount"),
                    relevance_score=_score_or_rank(item.get("score"), rank),
                    provider=self.name,
                )
            )
        return out


class OpenAlexAdapter:
    """OpenAlex 원시 응답 → ``PaperResult`` 매핑 어댑터 (순수).

    OpenAlex ``results[]`` 각 항목은 ``display_name``(제목)/``authorships[]``/
    ``publication_year``/``primary_location.source.display_name``(게재처)/
    ``abstract_inverted_index``(역인덱스 초록)/``doi``/``id``/``cited_by_count``/
    ``relevance_score``를 제공한다(design 매핑 표). 초록은 역인덱스를 위치
    순서로 재구성하고, DOI(``doi``)가 있으면 참조로 쓰고 없으면 ``id``를 쓴다.
    """

    name = "openalex"

    def to_paper_results(self, raw: dict) -> list[PaperResult]:
        out: list[PaperResult] = []
        for rank, item in enumerate(_result_items(raw, ("results",), ("data",))):
            if not isinstance(item, dict):
                continue
            out.append(
                _build_paper(
                    title=item.get("display_name"),
                    authors=_as_author_names(
                        item.get("authorships"), _openalex_author
                    ),
                    year=item.get("publication_year"),
                    venue=_get_path(item, "primary_location", "source", "display_name"),
                    abstract=_reconstruct_inverted_abstract(
                        item.get("abstract_inverted_index")
                    ),
                    doi_candidate=item.get("doi"),
                    url_candidate=item.get("id"),
                    citation_count=item.get("cited_by_count"),
                    relevance_score=_score_or_rank(item.get("relevance_score"), rank),
                    provider=self.name,
                )
            )
        return out


class ArxivAdapter:
    """arXiv(Atom→dict) 원시 응답 → ``PaperResult`` 매핑 어댑터 (순수).

    arXiv 엔트리(``entries[]``)는 ``title``/``author[].name`` 또는
    ``authors[].name``/``summary``(초록)/``published``(발행일)/``id``(arXiv URL)를
    제공한다(design 매핑 표). 게재처는 상수 ``"arXiv"``, 피인용수는 미제공이라
    ``0``, 관련성 점수는 순위 파생이다. DOI는 대개 없으나 ``arxiv_doi``/``doi``가
    있으면 참조로 채택한다.
    """

    name = "arxiv"

    def to_paper_results(self, raw: dict) -> list[PaperResult]:
        out: list[PaperResult] = []
        for rank, item in enumerate(
            _result_items(raw, ("entries",), ("feed", "entry"), ("entry",))
        ):
            if not isinstance(item, dict):
                continue
            raw_authors = item.get("authors")
            if raw_authors is None:
                raw_authors = item.get("author")
            out.append(
                _build_paper(
                    title=item.get("title"),
                    authors=_as_author_names(raw_authors, _arxiv_author),
                    year=item.get("published"),
                    venue="arXiv",
                    abstract=item.get("summary"),
                    doi_candidate=_first_nonempty(
                        item.get("arxiv_doi"), item.get("doi")
                    ),
                    url_candidate=item.get("id"),
                    citation_count=0,
                    relevance_score=1.0 / (rank + 1),  # 순위 파생(네이티브 점수 부재)
                    provider=self.name,
                )
            )
        return out


class PubMedAdapter:
    """PubMed(EFetch XML→dict) 원시 응답 → ``PaperResult`` 매핑 어댑터 (순수).

    PubMed 항목(``PubmedArticle``)은 ``MedlineCitation.Article`` 하위에
    ``ArticleTitle``/``AuthorList``/``Journal.Title``/``Abstract.AbstractText``/
    ``Journal...PubDate.Year``/``ELocationID(doi)``를 제공한다(design 매핑 표).
    ``#text`` 래핑·섹션 리스트 등 XML→dict 변형을 흡수하고, DOI가 없으면 PMID
    URL을 참조로 쓴다. 피인용수는 미제공이라 ``0``, 관련성 점수는 순위 파생이다.
    """

    name = "pubmed"

    def to_paper_results(self, raw: dict) -> list[PaperResult]:
        out: list[PaperResult] = []
        for rank, item in enumerate(
            _result_items(
                raw,
                ("articles",),
                ("PubmedArticleSet", "PubmedArticle"),
                ("PubmedArticle",),
                ("results",),
            )
        ):
            if not isinstance(item, dict):
                continue
            art = _pubmed_article_node(item)
            doi = _pubmed_doi(art)
            pmid = _pubmed_pmid(item, art)
            url = f"https://pubmed.ncbi.nlm.nih.gov/{pmid}/" if pmid else ""
            out.append(
                _build_paper(
                    title=_xml_text(art.get("ArticleTitle")),
                    authors=_as_author_names(
                        _get_path(art, "AuthorList", "Author"), _pubmed_author_name
                    ),
                    year=_pubmed_year(art),
                    venue=_xml_text(_get_path(art, "Journal", "Title")),
                    abstract=_xml_text(_get_path(art, "Abstract", "AbstractText")),
                    doi_candidate=doi,
                    url_candidate=url,
                    citation_count=0,
                    relevance_score=1.0 / (rank + 1),  # 순위 파생(네이티브 점수 부재)
                    provider=self.name,
                )
            )
        return out


def _epmc_author(a):
    """Europe PMC 저자 항목(``{"fullName": ...}``)에서 이름을 뽑는다."""
    if isinstance(a, dict):
        return a.get("fullName") or a.get("lastName")
    return a


class EuropePmcAdapter:
    """Europe PMC 원시 응답 → ``PaperResult`` 매핑 어댑터 (순수).

    왜 추가했나(실측 근거): 학술 검색이 사실상 OpenAlex 하나에 의존하고 있었다.
    ``semantic_scholar`` 는 키 없이 호출하면 HTTP 429 로 막히고(선택 키 미설정이
    기본), ``arxiv`` 는 export API 가 느려 자주 TIMEOUT 이다. Europe PMC 는
    **키가 필요 없고** 생의학 문헌(MEDLINE + PMC + preprint)을 커버해 이 앱 용도
    (전임상·규제 근거)에 정합한다. 실측: HTTP 200 / ~1.5s / 초록·DOI·피인용수 제공.

    ``resultList.result[]`` 각 항목은 ``title``/``authorString`` 또는
    ``authorList.author[].fullName``/``pubYear``/``journalInfo.journal.title``/
    ``abstractText``(``resultType=core`` 일 때)/``doi``/``pmid``/``citedByCount`` 를
    제공한다. DOI 가 없으면 PMID 기반 Europe PMC 문서 URL 을 참조로 쓴다.
    네이티브 관련성 점수가 없으므로 순위 파생 점수를 쓴다(PubMed 어댑터와 동일 규약).
    """

    name = "europepmc"

    def to_paper_results(self, raw: dict) -> list[PaperResult]:
        out: list[PaperResult] = []
        for rank, item in enumerate(
            _result_items(raw, ("resultList", "result"), ("results",), ("result",))
        ):
            if not isinstance(item, dict):
                continue
            # 저자: 구조화 리스트를 우선하고, 없으면 authorString(", " 구분)을 쪼갠다.
            authors = _as_author_names(
                _get_path(item, "authorList", "author"), _epmc_author
            )
            if not authors:
                raw_str = item.get("authorString")
                if isinstance(raw_str, str) and raw_str.strip():
                    authors = [
                        a.strip().rstrip(".")
                        for a in raw_str.split(",")
                        if a.strip().rstrip(".")
                    ]
            pmid = _as_str(item.get("pmid") or item.get("id"))
            url = f"https://europepmc.org/article/MED/{pmid}" if pmid else ""
            out.append(
                _build_paper(
                    title=item.get("title"),
                    authors=authors,
                    year=item.get("pubYear"),
                    venue=_get_path(item, "journalInfo", "journal", "title"),
                    abstract=item.get("abstractText"),
                    doi_candidate=item.get("doi"),
                    url_candidate=url,
                    citation_count=item.get("citedByCount"),
                    relevance_score=1.0 / (rank + 1),  # 순위 파생(네이티브 점수 부재)
                    provider=self.name,
                )
            )
        return out


# --------------------------------------------------------------------------- #
# 학술 어댑터 레지스트리 / 팩토리 (웹 어댑터와 동일 패턴)
# --------------------------------------------------------------------------- #
# 어댑터는 상태가 없는(stateless) 순수 매핑이므로 인스턴스를 재사용해도 안전하다.
_ACADEMIC_ADAPTERS: dict[str, AcademicSearchAdapter] = {
    SemanticScholarAdapter.name: SemanticScholarAdapter(),
    OpenAlexAdapter.name: OpenAlexAdapter(),
    ArxivAdapter.name: ArxivAdapter(),
    PubMedAdapter.name: PubMedAdapter(),
    EuropePmcAdapter.name: EuropePmcAdapter(),
}


def available_academic_adapters() -> list[str]:
    """등록된 학술 검색 어댑터 이름 목록을 정렬해 반환한다."""
    return sorted(_ACADEMIC_ADAPTERS)


def get_academic_adapter(name: str) -> AcademicSearchAdapter:
    """이름으로 학술 검색 어댑터를 조회한다(대소문자·앞뒤 공백 무시).

    Args:
        name: 제공자 이름(``"semantic_scholar"`` | ``"openalex"`` | ``"arxiv"`` |
            ``"pubmed"``).

    Returns:
        해당 ``AcademicSearchAdapter`` 인스턴스(stateless — 재사용 안전).

    Raises:
        ValueError: 등록되지 않은 제공자 이름인 경우(등록 목록을 메시지에 포함).
    """
    key = name.strip().lower() if isinstance(name, str) else ""
    adapter = _ACADEMIC_ADAPTERS.get(key)
    if adapter is None:
        available = ", ".join(available_academic_adapters())
        raise ValueError(
            f"Unknown academic search adapter: {name!r}. Available: {available}"
        )
    return adapter
