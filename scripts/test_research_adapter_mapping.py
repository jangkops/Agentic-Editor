# Feature: deep-research-engine
"""어댑터 매핑 단위 테스트 (Task 3.3) — `ai_engine/research/providers.py`.

각 검색 제공자의 **대표 원시 응답 샘플**로 `providers.py` 순수 매핑 어댑터의
필드 매핑 정확성을 design.md 매핑 표대로 검증한다.

  - 웹 3종:   Tavily / Exa / Brave        → `SearchResult`
  - 학술 4종: Semantic Scholar / OpenAlex / arXiv / PubMed → `PaperResult`

검증 축:

  (A) 매핑 정확성 — 대표 완전 샘플에서 title / url|doi / snippet|abstract /
      relevance_score / source_id / source_domain / authors / year / venue /
      citation_count 등 정규 필드가 design 매핑 표대로 채워짐.
  (B) 부분 필드 누락 시 기본값 채움(예외 없음, P8) — 텍스트 필드 누락 → "",
      수치 필드 누락 → 0 / 0.0, 저자 누락 → []. 어떤 결측·비정상 입력에도
      예외를 던지지 않는다(raw가 dict가 아님/결과 배열 없음/항목이 비-dict 포함).
  (C) 특수 재구성 로직 — OpenAlex `abstract_inverted_index` 위치 재구성 정확성,
      Brave/arXiv/PubMed 순위 파생 점수 `1/(rank+1)`.

`source_id`(dedup 키 규약, P6) 기대값은 `dedup.canonical_url`/`canonical_doi`
정규화를 반영한 리터럴로 대조한다.

Stack: Python 3.11+ 표준 라이브러리 + pytest 수집(순수·hermetic — 네트워크/파일
I/O 없음). 단발 실행(워치 모드 금지):
    python -m pytest scripts/test_research_adapter_mapping.py -q
    python scripts/test_research_adapter_mapping.py

_Requirements: 1.3, 2.6_
"""
from __future__ import annotations

import math
import sys
from pathlib import Path

# 스크립트를 직접 실행할 때도 ai_engine 패키지를 import 할 수 있게 repo 루트를 경로에 추가.
_REPO_ROOT = Path(__file__).resolve().parent.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from ai_engine.research.models import PaperResult, SearchResult  # noqa: E402
from ai_engine.research.providers import (  # noqa: E402
    AcademicSearchAdapter,
    ArxivAdapter,
    BraveAdapter,
    EuropePmcAdapter,
    ExaAdapter,
    OpenAlexAdapter,
    PubMedAdapter,
    SemanticScholarAdapter,
    TavilyAdapter,
    WebSearchAdapter,
    available_academic_adapters,
    available_web_adapters,
    get_academic_adapter,
    get_web_adapter,
)


# =========================================================================== #
# 웹 어댑터 — Tavily
# =========================================================================== #
def test_tavily_full_mapping() -> None:
    """Tavily 완전 샘플: results[].title/url/content/published_date/score 매핑."""
    raw = {
        "results": [
            {
                "title": "Attention Is All You Need",
                "url": "https://arxiv.org/abs/1706.03762",
                "content": "The dominant sequence transduction models...",
                "published_date": "2017-06-12",
                "score": 0.98,
            },
            {
                "title": "BERT",
                "url": "https://example.com/bert",
                "content": "Bidirectional encoder representations...",
                "published_date": "2018-10-11",
                "score": 0.91,
            },
        ]
    }
    results = TavilyAdapter().to_search_results(raw)

    assert len(results) == 2
    first = results[0]
    assert isinstance(first, SearchResult)
    assert first.title == "Attention Is All You Need"
    assert first.url == "https://arxiv.org/abs/1706.03762"
    assert first.snippet == "The dominant sequence transduction models..."
    assert first.published_date == "2017-06-12"
    assert first.source_domain == "arxiv.org"       # urlparse(url).netloc 파생
    assert first.relevance_score == 0.98            # results[].score 그대로
    assert first.provider == "tavily"
    assert first.source_id == "web:https://arxiv.org/abs/1706.03762"  # web:<canonical_url>
    assert results[1].source_domain == "example.com"


def test_tavily_partial_missing_defaults() -> None:
    """Tavily 부분 누락: 텍스트 → "", score(수치) 누락 → 0.0, 예외 없음(P8)."""
    raw = {"results": [{"url": "https://ex.com/x"}]}  # title/content/date/score 누락
    results = TavilyAdapter().to_search_results(raw)

    assert len(results) == 1
    r = results[0]
    assert r.title == ""
    assert r.snippet == ""
    assert r.published_date == ""
    assert r.relevance_score == 0.0                  # 수치 결측 → 정렬 가능한 0.0
    assert r.url == "https://ex.com/x"
    assert r.source_domain == "ex.com"
    assert r.provider == "tavily"
    assert r.source_id == "web:https://ex.com/x"


# =========================================================================== #
# 웹 어댑터 — Exa
# =========================================================================== #
def test_exa_text_preferred_over_highlights() -> None:
    """Exa: snippet은 text 우선(publishedDate/score 매핑)."""
    raw = {
        "results": [
            {
                "title": "Exa Doc",
                "url": "https://exa.ai/a",
                "text": "full body text",
                "highlights": ["frag-should-not-be-used"],
                "publishedDate": "2020-01-01",
                "score": 0.5,
            }
        ]
    }
    r = ExaAdapter().to_search_results(raw)[0]
    assert r.snippet == "full body text"             # text가 highlights보다 우선
    assert r.published_date == "2020-01-01"          # publishedDate → published_date
    assert r.relevance_score == 0.5
    assert r.provider == "exa"
    assert r.source_id == "web:https://exa.ai/a"


def test_exa_highlights_fallback_joins_fragments() -> None:
    """Exa: text 부재 시 highlights 조각을 공백으로 연결, date 결측 → ""."""
    raw = {
        "results": [
            {
                "title": "No text",
                "url": "https://exa.ai/b",
                "highlights": ["frag1", "frag2", "frag3"],
                "score": 0.3,
            }
        ]
    }
    r = ExaAdapter().to_search_results(raw)[0]
    assert r.snippet == "frag1 frag2 frag3"          # highlights 조각 공백 연결
    assert r.published_date == ""                    # 결측 텍스트 → ""


# =========================================================================== #
# 웹 어댑터 — Brave (순위 파생 점수)
# =========================================================================== #
def test_brave_rank_derived_score_and_age_fallback() -> None:
    """Brave: web.results[] 매핑 + 순위 파생 점수 1/(rank+1) + page_age/age 폴백."""
    raw = {
        "web": {
            "results": [
                {
                    "title": "A",
                    "url": "https://a.com",
                    "description": "desc-a",
                    "page_age": "2021-01-01",
                },
                {"title": "B", "url": "https://b.com", "description": "desc-b"},
                {
                    "title": "C",
                    "url": "https://c.com",
                    "description": "desc-c",
                    "age": "3 days ago",
                },
            ]
        }
    }
    results = BraveAdapter().to_search_results(raw)

    assert len(results) == 3
    # 순위 파생 점수: 네이티브 점수 부재 → 1/(rank+1), 순위대로 단조 감소.
    assert results[0].relevance_score == 1.0
    assert results[1].relevance_score == 0.5
    assert results[2].relevance_score == 1.0 / 3

    # snippet ← description, published_date ← page_age 우선, 없으면 age 폴백.
    assert results[0].snippet == "desc-a"
    assert results[0].published_date == "2021-01-01"  # page_age
    assert results[1].published_date == ""            # page_age/age 모두 결측
    assert results[2].published_date == "3 days ago"  # age 폴백
    assert results[0].provider == "brave"
    assert results[0].source_id == "web:https://a.com"


# =========================================================================== #
# 웹 어댑터 — 방어성(예외 없음, P8)
# =========================================================================== #
def test_web_adapters_no_exception_on_malformed() -> None:
    """웹 어댑터: 비정상 raw(빈/비-dict/결과 배열 없음/항목 혼재)에도 예외 없음."""
    malformed_inputs = [
        {},                                   # 빈 dict
        {"results": "not-a-list"},            # 결과가 list 아님
        {"results": [None, "str", 123]},      # 결과 항목이 모두 비-dict
        {"web": {}},                          # Brave 중첩 경로 결측
        None,                                  # dict 자체가 아님
    ]
    for adapter in (TavilyAdapter(), ExaAdapter(), BraveAdapter()):
        for raw in malformed_inputs:
            out = adapter.to_search_results(raw)  # 예외 전파 금지(P8)
            assert isinstance(out, list)
            assert all(isinstance(x, SearchResult) for x in out)


def test_web_adapter_coerces_nonstring_and_bad_score() -> None:
    """웹 어댑터: 비문자열 필드·비수치 score도 안전 변환(예외 없음)."""
    raw = {"results": [{"title": 123, "url": None, "score": "not-a-number"}]}
    r = TavilyAdapter().to_search_results(raw)[0]
    assert r.title == "123"                  # 비문자열 → str()
    assert r.url == ""                       # None → ""
    assert r.relevance_score == 0.0          # 비수치 → 0.0
    assert r.source_domain == ""
    assert r.source_id == "web:"             # canonical_url("") == ""


# =========================================================================== #
# 학술 어댑터 — Semantic Scholar
# =========================================================================== #
def test_semantic_scholar_full_mapping() -> None:
    """S2 완전 샘플: title/authors[].name/year/venue/abstract/DOI/citationCount."""
    raw = {
        "data": [
            {
                "title": "Deep Learning",
                "authors": [{"name": "Yann LeCun"}, {"name": "Yoshua Bengio"}],
                "year": 2015,
                "venue": "Nature",
                "abstract": "Deep learning allows computational models...",
                "externalIds": {"DOI": "10.1038/nature14539"},
                "url": "https://www.semanticscholar.org/paper/x",
                "citationCount": 50000,
                "score": 0.87,
            }
        ]
    }
    r = SemanticScholarAdapter().to_paper_results(raw)[0]

    assert isinstance(r, PaperResult)
    assert r.title == "Deep Learning"
    assert r.authors == ["Yann LeCun", "Yoshua Bengio"]  # authors[].name 순서 보존
    assert r.year == 2015
    assert r.venue == "Nature"
    assert r.abstract == "Deep learning allows computational models..."
    assert r.citation_count == 50000
    assert r.relevance_score == 0.87                     # 명시 score 사용
    assert r.provider == "semantic_scholar"
    # DOI 존재 → source_id = doi:<canonical_doi>, doi_or_url = 원본 DOI
    assert r.source_id == "doi:10.1038/nature14539"
    assert r.doi_or_url == "10.1038/nature14539"


def test_semantic_scholar_url_fallback_and_defaults() -> None:
    """S2: DOI 부재 → url 참조/web: source_id, 결측 필드 기본값, score 부재 → 순위 파생."""
    raw = {"data": [{"title": "No DOI paper", "url": "https://s2.org/p/abc", "year": 2019}]}
    r = SemanticScholarAdapter().to_paper_results(raw)[0]

    assert r.title == "No DOI paper"
    assert r.authors == []                # 저자 결측 → []
    assert r.abstract == ""               # 텍스트 결측 → ""
    assert r.venue == ""
    assert r.citation_count == 0          # 수치 결측 → 0
    assert r.year == 2019
    assert r.doi_or_url == "https://s2.org/p/abc"
    assert r.source_id == "web:https://s2.org/p/abc"   # DOI 없음 → web:<canonical_url>
    assert r.relevance_score == 1.0       # score 부재 → 순위 파생 1/(0+1)


# =========================================================================== #
# 학술 어댑터 — OpenAlex (역인덱스 초록 재구성)
# =========================================================================== #
def test_openalex_inverted_abstract_reconstruction() -> None:
    """OpenAlex: abstract_inverted_index를 위치 오름차순으로 정확히 재구성."""
    raw = {
        "results": [
            {
                "display_name": "Graph Neural Networks",
                "authorships": [
                    {"author": {"display_name": "Alice A"}},
                    {"author": {"display_name": "Bob B"}},
                ],
                "publication_year": 2020,
                "primary_location": {"source": {"display_name": "ICML"}},
                "abstract_inverted_index": {
                    "Graph": [0],
                    "neural": [1],
                    "networks": [2, 5],
                    "are": [3],
                    "powerful": [4],
                    "deep": [6],
                },
                "doi": "https://doi.org/10.5555/gnn2020",
                "id": "https://openalex.org/W123",
                "cited_by_count": 1200,
                "relevance_score": 12.34,
            }
        ]
    }
    r = OpenAlexAdapter().to_paper_results(raw)[0]

    # 위치 재구성: 0:Graph 1:neural 2:networks 3:are 4:powerful 5:networks 6:deep
    assert r.abstract == "Graph neural networks are powerful networks deep"
    assert r.title == "Graph Neural Networks"          # display_name → title
    assert r.authors == ["Alice A", "Bob B"]           # authorships[].author.display_name
    assert r.year == 2020                              # publication_year → year
    assert r.venue == "ICML"                           # primary_location.source.display_name
    assert r.citation_count == 1200                    # cited_by_count → citation_count
    assert r.relevance_score == 12.34                  # relevance_score 그대로
    assert r.provider == "openalex"
    # doi 정규화(https://doi.org/ 프리픽스 제거) → source_id, 원본은 doi_or_url 보존
    assert r.source_id == "doi:10.5555/gnn2020"
    assert r.doi_or_url == "https://doi.org/10.5555/gnn2020"


def test_openalex_empty_inverted_index_defaults() -> None:
    """OpenAlex: 역인덱스/게재처/DOI 결측 시 기본값(예외 없음)."""
    raw = {
        "results": [
            {
                "display_name": "Minimal",
                "id": "https://openalex.org/W999",
                "abstract_inverted_index": None,   # 역인덱스 결측
            }
        ]
    }
    r = OpenAlexAdapter().to_paper_results(raw)[0]
    assert r.abstract == ""              # 역인덱스 결측 → ""
    assert r.venue == ""                 # primary_location 결측 → ""
    assert r.authors == []
    assert r.year == 0                   # publication_year 결측 → 0
    assert r.citation_count == 0
    assert r.doi_or_url == "https://openalex.org/W999"  # doi 없음 → id 참조
    assert r.source_id == "web:https://openalex.org/W999"


# =========================================================================== #
# 학술 어댑터 — arXiv (순위 파생 점수, venue 상수)
# =========================================================================== #
def test_arxiv_mapping_rank_score_and_author_key_variants() -> None:
    """arXiv: entries[] 매핑, venue="arXiv", citation 0, 순위 파생 점수, author/authors 키."""
    raw = {
        "entries": [
            {
                "title": "Quantum Computing",
                "authors": [{"name": "Charlie C"}],
                "summary": "We present a quantum approach...",
                "published": "2022-03-15T00:00:00Z",
                "id": "http://arxiv.org/abs/2203.00001",
            },
            {
                "title": "Second Paper",
                "author": [{"name": "Dana D"}],   # 'author' 키 변형
                "summary": "Second summary",
                "published": "2021",
                "id": "http://arxiv.org/abs/2101.00002",
            },
        ]
    }
    results = ArxivAdapter().to_paper_results(raw)

    assert len(results) == 2
    first = results[0]
    assert first.title == "Quantum Computing"
    assert first.authors == ["Charlie C"]         # authors[].name
    assert first.year == 2022                      # published(ISO)에서 연도 추출
    assert first.venue == "arXiv"                  # 상수
    assert first.abstract == "We present a quantum approach..."
    assert first.citation_count == 0               # arXiv 미제공 → 0
    assert first.relevance_score == 1.0            # 순위 파생 1/(0+1)
    assert first.provider == "arxiv"
    assert first.doi_or_url == "http://arxiv.org/abs/2203.00001"  # id(arXiv URL) 참조
    assert first.source_id == "web:http://arxiv.org/abs/2203.00001"

    # 두 번째: 'author' 키 변형에서도 저자 추출 + 순위 파생 점수 1/(1+1)
    assert results[1].authors == ["Dana D"]
    assert results[1].year == 2021
    assert results[1].relevance_score == 0.5


# =========================================================================== #
# 학술 어댑터 — PubMed (XML→dict 흡수, DOI/PMID)
# =========================================================================== #
def test_pubmed_full_mapping_with_doi() -> None:
    """PubMed: MedlineCitation.Article 하위 필드 매핑 + ELocationID(doi) 추출."""
    raw = {
        "PubmedArticleSet": {
            "PubmedArticle": [
                {
                    "MedlineCitation": {
                        "PMID": "12345678",
                        "Article": {
                            "ArticleTitle": "CRISPR gene editing",
                            "AuthorList": {
                                "Author": [
                                    {"LastName": "Doudna", "ForeName": "Jennifer"},
                                    {"LastName": "Charpentier", "ForeName": "Emmanuelle"},
                                ]
                            },
                            "Journal": {
                                "Title": "Science",
                                "JournalIssue": {"PubDate": {"Year": "2012"}},
                            },
                            "Abstract": {"AbstractText": "CRISPR-Cas9 is a tool..."},
                            "ELocationID": {
                                "@EIdType": "doi",
                                "#text": "10.1126/science.1225829",
                            },
                        },
                    }
                }
            ]
        }
    }
    r = PubMedAdapter().to_paper_results(raw)[0]

    assert r.title == "CRISPR gene editing"                # ArticleTitle
    assert r.authors == ["Jennifer Doudna", "Emmanuelle Charpentier"]  # ForeName+LastName
    assert r.year == 2012                                  # PubDate.Year
    assert r.venue == "Science"                            # Journal.Title
    assert r.abstract == "CRISPR-Cas9 is a tool..."        # Abstract.AbstractText
    assert r.citation_count == 0                           # PubMed 미제공 → 0
    assert r.relevance_score == 1.0                        # 순위 파생 1/(0+1)
    assert r.provider == "pubmed"
    assert r.source_id == "doi:10.1126/science.1225829"    # ELocationID(doi)
    assert r.doi_or_url == "10.1126/science.1225829"


def test_pubmed_pmid_url_fallback_single_dict() -> None:
    """PubMed: 단일 dict 응답 + DOI 부재 → PMID URL 참조/web: source_id, 결측 기본값."""
    raw = {
        "PubmedArticleSet": {
            "PubmedArticle": {  # list가 아닌 단일 dict
                "MedlineCitation": {
                    "PMID": "999",
                    "Article": {"ArticleTitle": "No DOI"},
                }
            }
        }
    }
    r = PubMedAdapter().to_paper_results(raw)[0]

    assert r.title == "No DOI"
    assert r.authors == []          # AuthorList 결측 → []
    assert r.year == 0              # PubDate 결측 → 0
    assert r.venue == ""           # Journal 결측 → ""
    assert r.abstract == ""        # Abstract 결측 → ""
    # DOI 없음 → PMID URL을 참조로, source_id는 web:<canonical_url>(말미 슬래시 정리)
    assert r.doi_or_url == "https://pubmed.ncbi.nlm.nih.gov/999/"
    assert r.source_id == "web:https://pubmed.ncbi.nlm.nih.gov/999"


def test_pubmed_abstract_section_list_join() -> None:
    """PubMed: AbstractText가 섹션 리스트(#text 래핑)여도 평문으로 흡수(P8)."""
    raw = {
        "PubmedArticleSet": {
            "PubmedArticle": {
                "MedlineCitation": {
                    "PMID": "42",
                    "Article": {
                        "ArticleTitle": "Sectioned",
                        "Abstract": {
                            "AbstractText": [
                                {"@Label": "BACKGROUND", "#text": "bg text"},
                                {"@Label": "METHODS", "#text": "methods text"},
                            ]
                        },
                    },
                }
            }
        }
    }
    r = PubMedAdapter().to_paper_results(raw)[0]
    assert r.abstract == "bg text methods text"   # 섹션 조각 공백 연결


# =========================================================================== #
# 학술 어댑터 — 방어성(예외 없음, P8)
# =========================================================================== #
def test_academic_adapters_no_exception_on_malformed() -> None:
    """학술 어댑터: 비정상 raw(빈/비-dict/결과 배열 없음/항목 혼재)에도 예외 없음."""
    malformed_inputs = [
        {},
        {"data": "not-a-list"},
        {"results": [None, "str", 123]},
        {"entries": None},
        None,
    ]
    for adapter in (
        SemanticScholarAdapter(),
        OpenAlexAdapter(),
        ArxivAdapter(),
        PubMedAdapter(),
    ):
        for raw in malformed_inputs:
            out = adapter.to_paper_results(raw)   # 예외 전파 금지(P8)
            assert isinstance(out, list)
            assert all(isinstance(x, PaperResult) for x in out)


def test_academic_adapter_coerces_bad_numeric_fields() -> None:
    """학술 어댑터: 비수치 year/citationCount도 정렬 가능한 기본값으로 안전 변환."""
    raw = {
        "data": [
            {"title": "Bad numerics", "year": "not-a-year", "citationCount": None}
        ]
    }
    r = SemanticScholarAdapter().to_paper_results(raw)[0]
    assert r.year == 0                # 파싱 불가 연도 → 0
    assert r.citation_count == 0      # None 피인용수 → 0
    assert isinstance(r.relevance_score, float)
    assert not math.isnan(r.relevance_score)   # 결정적 정렬 보장


# =========================================================================== #
# 레지스트리 / 팩토리 / 프로토콜 준수
# =========================================================================== #
def test_web_factory_and_protocol_conformance() -> None:
    """웹 어댑터 팩토리 조회(대소문자·공백 무시) + WebSearchAdapter 프로토콜 준수."""
    assert available_web_adapters() == ["brave", "exa", "tavily"]
    for name, cls in (("tavily", TavilyAdapter), ("exa", ExaAdapter), ("brave", BraveAdapter)):
        adapter = get_web_adapter(name.upper() + " ")   # 대소문자·공백 무시
        assert isinstance(adapter, cls)
        assert isinstance(adapter, WebSearchAdapter)     # 런타임 프로토콜 준수


def test_academic_factory_and_protocol_conformance() -> None:
    """학술 어댑터 팩토리 조회 + AcademicSearchAdapter 프로토콜 준수."""
    # europepmc 는 키리스 생의학 제공자로 추가되었다(semantic_scholar 키 없이 429 /
    # arxiv 빈번한 TIMEOUT 으로 학술 검색이 openalex 단독에 의존하던 문제 해소).
    assert available_academic_adapters() == [
        "arxiv", "europepmc", "openalex", "pubmed", "semantic_scholar",
    ]
    for name, cls in (
        ("semantic_scholar", SemanticScholarAdapter),
        ("openalex", OpenAlexAdapter),
        ("arxiv", ArxivAdapter),
        ("pubmed", PubMedAdapter),
        ("europepmc", EuropePmcAdapter),
    ):
        adapter = get_academic_adapter(name)
        assert isinstance(adapter, cls)
        assert isinstance(adapter, AcademicSearchAdapter)


def test_factory_unknown_name_raises_valueerror() -> None:
    """팩토리: 등록되지 않은 이름은 ValueError(등록 목록 포함 메시지)."""
    for getter in (get_web_adapter, get_academic_adapter):
        try:
            getter("does-not-exist")
        except ValueError as e:
            assert "does-not-exist" in str(e)
        else:
            raise AssertionError(f"{getter.__name__}가 미등록 이름에 ValueError를 던지지 않음")


if __name__ == "__main__":
    # 단발 실행 드라이버(워치 모드 금지). 모든 테스트 함수를 순차 호출한다.
    _tests = [v for k, v in sorted(globals().items()) if k.startswith("test_") and callable(v)]
    for fn in _tests:
        fn()
    print(f"PASSED: {len(_tests)} adapter-mapping unit tests (Task 3.3)")


# =========================================================================== #
# 학술 어댑터 — Europe PMC (키리스)
# =========================================================================== #
def test_europepmc_full_mapping() -> None:
    """EPMC 완전 샘플: resultList.result[] → title/authors/pubYear/journal/abstract/DOI."""
    raw = {
        "resultList": {
            "result": [
                {
                    "id": "42311917",
                    "pmid": "42311917",
                    "title": "Liraglutide-Induced Acute Hepatocellular Injury",
                    "authorList": {
                        "author": [
                            {"fullName": "Adi M"},
                            {"fullName": "Kim J"},
                        ]
                    },
                    "authorString": "Adi M, Kim J.",
                    "pubYear": "2026",
                    "journalInfo": {"journal": {"title": "Clin Med Insights Case Rep"}},
                    "abstractText": "A case of hepatocellular injury after liraglutide.",
                    "doi": "10.1177/11795476261461221",
                    "citedByCount": 7,
                }
            ]
        }
    }
    results = get_academic_adapter("europepmc").to_paper_results(raw)
    assert len(results) == 1
    r = results[0]
    assert r.title == "Liraglutide-Induced Acute Hepatocellular Injury"
    assert r.authors == ["Adi M", "Kim J"]
    assert r.year == 2026
    assert r.venue == "Clin Med Insights Case Rep"
    assert r.abstract.startswith("A case of hepatocellular injury")
    assert r.citation_count == 7
    assert r.provider == "europepmc"
    # DOI 존재 → dedup 키가 doi: 스킴이어야 한다(제공자 간 중복제거가 성립).
    assert r.source_id == "doi:10.1177/11795476261461221"
    # 네이티브 관련성 점수가 없으므로 순위 파생(1위 → 1.0).
    assert r.relevance_score == 1.0


def test_europepmc_author_string_fallback_and_url_reference() -> None:
    """EPMC: authorList 부재 → authorString 분해, DOI 부재 → PMID URL 참조."""
    raw = {
        "resultList": {
            "result": [
                {
                    "pmid": "12345",
                    "title": "No DOI paper",
                    "authorString": "Lee S, Park H, Choi Y.",
                    "pubYear": "2019",
                }
            ]
        }
    }
    r = get_academic_adapter("europepmc").to_paper_results(raw)[0]
    # 구조화 리스트가 없으면 authorString 을 ", " 로 분해하고 끝 마침표를 제거한다.
    assert r.authors == ["Lee S", "Park H", "Choi Y"]
    # DOI 가 없으면 Europe PMC 문서 URL 을 참조로 쓴다(web: 스킴).
    assert r.doi_or_url == "https://europepmc.org/article/MED/12345"
    assert r.source_id.startswith("web:")
    # 결측 필드는 기본값(P8).
    assert r.venue == "" and r.abstract == "" and r.citation_count == 0


def test_europepmc_defensive_inputs() -> None:
    """EPMC: 비-dict/빈 컨테이너/이상 항목에도 예외 없이 안전하게 처리한다(P8)."""
    adapter = get_academic_adapter("europepmc")
    assert adapter.to_paper_results({}) == []
    assert adapter.to_paper_results({"resultList": {}}) == []
    assert adapter.to_paper_results({"resultList": {"result": []}}) == []
    # 오류 dict(egress 실패)는 결과 컨테이너가 없어 빈 목록이 된다.
    assert adapter.to_paper_results({"error": "timeout", "detail": "x"}) == []
    # 항목에 dict 가 아닌 값이 섞여도 건너뛴다.
    out = adapter.to_paper_results(
        {"resultList": {"result": [None, "junk", {"title": "ok"}]}}
    )
    assert len(out) == 1 and out[0].title == "ok"
