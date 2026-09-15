"""deep-research-engine: 관련성 정렬 — 순수/결정적 (부작용 없음).

검색 결과(웹/논문)를 관련성 점수 기준으로 결정적으로 정렬한다. 정렬은 입력을
변경하지 않고 새 리스트를 반환하는 순수 함수이며, Python 3.11 표준 라이브러리
(``math``)만 사용한다(신규 의존성 없음).

결정성(P7)의 핵심은 파이썬 ``sorted``의 **안정 정렬** 보장이다. 안정 정렬은
비교상 동등한 원소의 상대 순서를 바꾸지 않으므로, 정렬 키가 동점인 결과들은
입력(제공자 반환) 순서를 그대로 보존한다. 따라서 동일 입력에 대해 항상 동일한
정렬 결과가 나온다.

- ``sort_by_relevance_web``: 관련성 점수 내림차순. 동점은 제공자 반환 순서 보존
  (요구사항 1.4/1.5, P7).
- ``sort_by_relevance_papers``: 관련성 내림차순 → 동점 시 피인용수 내림차순 →
  그래도 동점이면 제공자 반환 순서 보존(요구사항 2.3/2.4, P7).
- ``apply_recency``: 최신성 창(window) 필터 후 발행일 내림차순 정렬(동점: 관련성
  내림차순 → 제공자 반환 순서). 발행일 미상 항목은 단일 규칙(``exclude``/``last``)으로
  일관 처리하여 동일 입력에 항상 동일 출력을 보장한다(요구사항 9.2/9.3, P12).

점수 결측 처리(models 기본값 정합, P8): 관련성 점수·피인용수가 ``None``이거나
수치가 아니거나 NaN이면 정렬 가능한 기본값(``0.0``/``0``)으로 취급한다. 이는
``SearchResult.relevance_score``/``PaperResult.citation_count``의 기본값과
정합하며, 이상치로 인한 비결정 정렬을 방지한다.

융합·재랭킹(``merge_and_rerank``)과 출처 신뢰도(``source_authority``, 태스크 5.5)는
기존 검증 자산을 **재사용**해 조립한다(재구현 금지). 검색 융합은
``rag.hybrid_search.rrf_fuse``, 재정렬 순열 규약은 ``rag.reranker.parse_rerank_order``,
중복제거는 본 패키지 ``dedup.dedup_sources``/``source_key`` 를 그대로 사용한다.
Gateway(``gw``) 미제공이거나 재사용 자산 import·호출이 실패해도 결정적 폴백으로
비차단 동작하며, 어떤 경로에서도 입력을 변경하지 않는다(순수). 기존
``sort_by_relevance_*``/``apply_recency`` 는 변경하지 않는다.

Requirements: 1.4, 1.5, 2.3, 2.4, 7.5, 7.6, 9.2, 9.3, 9.4, 16.1
Design: "Components and Interfaces" 5) rank.py 절
        (sort_by_relevance_web / sort_by_relevance_papers / apply_recency /
         merge_and_rerank / source_authority)
Property: P7 (관련성 정렬 — 결정적 내림차순), P12 (최신성 필터 — 준동형/결정적),
          P4 (재랭킹 순열 불변식 — parse_rerank_order 규약 계승)
"""

import inspect
import math
from datetime import date, datetime, timedelta, timezone
from urllib.parse import urlsplit

from .config import DeepResearchConfig
from .dedup import dedup_sources, source_key
from .models import PaperResult, SearchResult


def _as_sort_number(value) -> float:
    """정렬 키로 사용할 float로 안전 변환한다.

    결측(``None``)·비수치·``NaN``은 모두 정렬 가능한 기본값 ``0.0``으로
    취급한다(models 기본값 정합, 결정적 정렬 보장 — P7/P8). float 변환은
    int·float·수치 문자열을 허용하며, 실패 시 ``0.0``으로 폴백한다.
    """
    if value is None:
        return 0.0
    try:
        f = float(value)
    except (TypeError, ValueError):
        return 0.0
    # NaN은 비교가 비결정적이므로 결측과 동일하게 0.0으로 취급한다.
    return 0.0 if math.isnan(f) else f


def sort_by_relevance_web(results: list[SearchResult]) -> list[SearchResult]:
    """웹 검색 결과를 관련성 점수 내림차순으로 정렬한다 (요구사항 1.4/1.5, P7).

    안정 정렬을 이용해 관련성 점수가 동일한 결과는 입력(제공자 반환) 순서를
    그대로 보존하므로, 동일 입력에 대해 항상 동일한 결과를 산출한다(결정적).
    입력을 변경하지 않고 새 리스트를 반환하는 순수 함수다.

    Args:
        results: 정렬할 ``SearchResult`` 목록. ``None`` 또는 빈 입력은 빈
            리스트로 방어한다.

    Returns:
        관련성 점수 내림차순으로 정렬된 새 리스트. 동점은 입력 순서 보존.
    """
    if not results:
        return []
    # sorted 는 안정 정렬 → 동점(동일 점수)은 입력 순서 보존(결정적, P7 / Req 1.5).
    # 음수 키로 내림차순을 표현하며 입력 리스트는 변경하지 않는다(순수).
    return sorted(
        results,
        key=lambda r: -_as_sort_number(getattr(r, "relevance_score", 0.0)),
    )


def sort_by_relevance_papers(results: list[PaperResult]) -> list[PaperResult]:
    """논문 검색 결과를 관련성·피인용수 기준으로 정렬한다 (요구사항 2.3/2.4, P7).

    정렬 순서:
        1. 관련성 점수 내림차순
        2. (동점 시) 피인용수(``citation_count``) 내림차순
        3. (그래도 동점 시) 입력(제공자 반환) 순서 보존

    안정 정렬을 이용해 (관련성, 피인용수)가 모두 동일한 결과는 입력 순서를
    보존하므로 결정적이다. 입력을 변경하지 않고 새 리스트를 반환하는 순수
    함수다.

    Args:
        results: 정렬할 ``PaperResult`` 목록. ``None`` 또는 빈 입력은 빈
            리스트로 방어한다.

    Returns:
        관련성 내림차순 → 피인용수 내림차순으로 정렬된 새 리스트. 완전 동점은
        입력 순서 보존.
    """
    if not results:
        return []
    # 다중 키(음수)로 관련성 → 피인용수 내림차순을 표현한다. 두 키가 모두
    # 동점이면 안정 정렬이 입력 순서를 보존한다(결정적, P7 / Req 2.4).
    return sorted(
        results,
        key=lambda r: (
            -_as_sort_number(getattr(r, "relevance_score", 0.0)),
            -_as_sort_number(getattr(r, "citation_count", 0)),
        ),
    )


# ---------------------------------------------------------------------------
# 최신성 필터 (apply_recency) — 요구사항 9.2/9.3, Property 12
# ---------------------------------------------------------------------------
#
# 발행일 파싱·창(window) 해석·미상 처리는 모두 방어적이며 예외를 던지지 않는다.
# 파싱 실패·형식 오류는 "발행일 미상"으로 환원되어 unknown_rule로 일관 처리되고,
# 창 해석 실패는 "창 없음(전체 통과)"으로 폴백한다. Python 3.11 표준 라이브러리
# (``datetime``/``math``)만 사용하며 입력 리스트·항목을 변경하지 않는 순수 함수다.


def _to_aware_utc(dt: datetime) -> datetime:
    """naive datetime은 UTC로 간주하고, aware datetime은 UTC로 환산한다.

    발행일 비교를 항상 aware(UTC) 기준으로 수행해 naive/aware 혼합 비교 예외를
    방지한다(결정성 — P12).
    """
    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def _parse_date(value) -> datetime | None:
    """발행일 값을 aware(UTC) datetime으로 방어적으로 파싱한다.

    ``datetime``/``date`` 객체, ISO8601 문자열(날짜 전용·시간 포함·``Z`` 접미사)을
    허용한다. 빈 값·형식 오류·미상은 예외 없이 ``None``("미상")으로 반환한다.
    """
    if value is None:
        return None
    # datetime 이 date 의 하위 클래스이므로 datetime 을 먼저 판정한다.
    if isinstance(value, datetime):
        return _to_aware_utc(value)
    if isinstance(value, date):
        return datetime(value.year, value.month, value.day, tzinfo=timezone.utc)
    s = str(value).strip()
    if not s:
        return None
    # 말미 Z(zulu)를 명시적 UTC 오프셋으로 정규화(방어적).
    if s[-1] in ("Z", "z"):
        s = s[:-1] + "+00:00"
    try:
        return _to_aware_utc(datetime.fromisoformat(s))
    except ValueError:
        return None


def _published_dt(item) -> datetime | None:
    """항목의 발행일을 aware(UTC) datetime으로 추출한다(없으면 ``None``).

    ``published_date``(SearchResult/EvidenceSource)를 우선 파싱하고, 없거나
    파싱 실패 시 ``year``(PaperResult, 미상=0)로 폴백해 해당 연도 1월 1일(UTC)로
    간주한다. 어느 것도 확정 불가면 "미상"(``None``)이다.
    """
    dt = _parse_date(getattr(item, "published_date", ""))
    if dt is not None:
        return dt
    year = getattr(item, "year", 0)
    try:
        y = int(year)
    except (TypeError, ValueError):
        y = 0
    if y > 0:
        return datetime(y, 1, 1, tzinfo=timezone.utc)
    return None


def _resolve_cutoff(window, now: datetime) -> datetime | None:
    """최신성 창(window)을 하한 기준일(cutoff, aware UTC)로 방어적으로 해석한다.

    지원 형태:
        - ``None`` / 비유한 수 / 0 이하 일수 / 해석 불가 → ``None``(창 없음, 전체 통과).
        - 양의 int/float → 일수(days)로 간주, ``cutoff = now - days``.
        - ``datetime``/``date`` → 기준일 자체(그 시점 이후만 통과).
        - 문자열 → ISO 날짜로 먼저 시도, 실패 시 일수(float)로 해석.

    ``bool`` 은 일수로 오해되지 않도록 명시적으로 창 없음으로 처리한다.
    """
    if window is None or isinstance(window, bool):
        return None
    if isinstance(window, (int, float)):
        days = float(window)
        if not math.isfinite(days) or days <= 0:
            return None
        return now - timedelta(days=days)
    if isinstance(window, datetime):
        return _to_aware_utc(window)
    if isinstance(window, date):
        return datetime(window.year, window.month, window.day, tzinfo=timezone.utc)
    s = str(window).strip()
    if not s:
        return None
    dt = _parse_date(s)
    if dt is not None:
        return dt
    try:
        days = float(s)
    except ValueError:
        return None
    if not math.isfinite(days) or days <= 0:
        return None
    return now - timedelta(days=days)


def _resolve_unknown_rule(unknown_rule) -> str:
    """미상 처리 규칙을 정규화한다. 허용값(exclude|last) 외에는 config 기본값 폴백.

    기본값은 ``DeepResearchConfig`` 의 정적 기본(``recency_unknown`` = "last")과
    정합한다. 런타임 env 기반 값은 호출자가 ``unknown_rule=config.recency_unknown``
    으로 명시 주입하며, 본 함수는 순수 유지를 위해 env 를 읽지 않는다.
    """
    val = str(unknown_rule or "").strip().lower()
    if val in ("exclude", "last"):
        return val
    return DeepResearchConfig().recency_unknown


def apply_recency(results, window=None, unknown_rule=None, *, now=None) -> list:
    """최신성 창 필터 + 발행일 내림차순 정렬 (요구사항 9.2/9.3, P12).

    처리 순서:
        1. 각 항목의 발행일을 방어적으로 파싱한다(파싱 실패·미상 → "미상").
        2. 창(``window``)이 해석되면 발행일이 창 이내(``>= cutoff``)인 확정 항목만 남긴다.
        3. 확정 항목을 **발행일 내림차순 → 관련성 점수 내림차순 → 제공자 반환 순서**로
           정렬한다(안정 정렬로 완전 동점 시 입력 순서 보존 → 결정적).
        4. 발행일 미상 항목은 ``unknown_rule`` 로 일관 처리한다:
           - ``"exclude"``: 창 필터 단계에서 일괄 제외.
           - ``"last"``: 정렬 결과 말미에 입력 순서를 유지한 채 일괄 배치.

    동일 입력에 대해 항상 동일한 출력을 산출한다(P12). 입력 리스트·항목을 변경하지
    않는 순수 함수이며, 발행일 파싱·창 해석 실패는 예외 없이 각각 "미상"·"창 없음"
    으로 환원한다.

    Args:
        results: ``SearchResult``/``PaperResult``/``EvidenceSource`` 등 발행일·관련성
            속성을 갖는 항목 목록. ``None``/빈 입력은 빈 리스트로 방어한다.
        window: 최신성 창. 양의 일수(int/float), 기준일(``datetime``/``date``/ISO
            문자열), 또는 ``None``(창 없음). 해석 불가 값은 창 없음으로 폴백한다.
        unknown_rule: 발행일 미상 처리 규칙(``"exclude"``|``"last"``). ``None``/무효값은
            config 기본값(``recency_unknown`` = "last")으로 폴백한다.
        now: 상대 일수 창의 기준 시각(테스트 주입용, 기본은 현재 UTC). 상대 일수
            창일 때만 사용되며 한 호출 내에서 단일 값으로 고정된다.

    Returns:
        창 이내(확정 발행일) 항목을 발행일 내림차순으로 정렬한 새 리스트. ``last``
        규칙이면 미상 항목이 말미에 입력 순서대로 덧붙는다.
    """
    if not results:
        return []

    rule = _resolve_unknown_rule(unknown_rule)
    if not isinstance(now, datetime):
        now = datetime.now(timezone.utc)
    else:
        now = _to_aware_utc(now)
    cutoff = _resolve_cutoff(window, now)

    known: list[tuple[datetime, object]] = []  # (발행일, 항목) — 입력 순서 유지
    unknown: list = []                          # 발행일 미상 항목 — 입력 순서 유지
    for item in results:
        dt = _published_dt(item)
        if dt is None:
            unknown.append(item)
        else:
            known.append((dt, item))

    # 창 필터: cutoff 이 있으면 창 이내 확정 항목만 남긴다(요구사항 9.2).
    if cutoff is not None:
        known = [(dt, item) for (dt, item) in known if dt >= cutoff]

    # 발행일 내림차순 → 관련성 내림차순 → (완전 동점) 입력 순서 보존(안정 정렬, P12).
    known_sorted = sorted(
        known,
        key=lambda pair: (
            -pair[0].timestamp(),
            -_as_sort_number(getattr(pair[1], "relevance_score", 0.0)),
        ),
    )
    ordered = [item for (_dt, item) in known_sorted]

    # 미상 항목: last=말미 일괄 배치(입력 순서 유지), exclude=이미 제외(요구사항 9.3).
    if rule == "last":
        ordered.extend(unknown)
    return ordered


# ---------------------------------------------------------------------------
# 융합·재랭킹 (merge_and_rerank) + 출처 신뢰도 (source_authority)
# 요구사항 7.5/7.6/9.4/16.1, Property 4 — 기존 자산 재사용(재구현 금지)
# ---------------------------------------------------------------------------
#
# 조립 파이프라인: 다중 제공자 순위 리스트 → (출처 신뢰도 순위 리스트 추가) →
#   ``rag.hybrid_search.rrf_fuse`` 로 융합 → ``dedup.dedup_sources`` 로 중복제거 →
#   (gw 제공 시 옵션) ``rag.reranker.parse_rerank_order`` 순열 규약으로 재정렬.
#
# 결과는 항상 ``dedup_sources(sources)`` 의 순열이다(창작·누락 없음 — P4). 융합·
# 재랭킹 자산 import·호출이 실패하거나 gw 가 없어도 결정적 폴백으로 비차단
# 동작하며, 입력 리스트·항목을 변경하지 않는 순수 조합 함수다. 재사용 자산은
# 지연 import(캐시)로 로딩해 import 실패를 방어한다("import 실패 시 방어적 폴백").


_RRF_FUSE = "unset"          # 지연 import 캐시(sentinel)
_PARSE_RERANK_ORDER = "unset"


def _get_rrf_fuse():
    """``rag.hybrid_search.rrf_fuse`` 를 지연 import 한다(실패 시 ``None``).

    재구현하지 않고 기존 순수 RRF 융합 함수를 그대로 재사용한다(요구사항 16.1).
    import 실패(모듈 부재/의존성 오류)는 예외 없이 ``None`` 으로 폴백한다.
    """
    global _RRF_FUSE
    if _RRF_FUSE == "unset":
        try:
            from ai_engine.rag.hybrid_search import rrf_fuse
            _RRF_FUSE = rrf_fuse
        except Exception:
            _RRF_FUSE = None
    return _RRF_FUSE


def _get_parse_rerank_order():
    """``rag.reranker.parse_rerank_order`` 를 지연 import 한다(실패 시 ``None``).

    ``parse_rerank_order`` 는 "항상 [0,n) 유효 인덱스의 순열"을 보장하므로 재랭킹
    단계의 순열 불변식(P4)이 그대로 계승된다. import 실패는 ``None`` 폴백한다.
    """
    global _PARSE_RERANK_ORDER
    if _PARSE_RERANK_ORDER == "unset":
        try:
            from ai_engine.rag.reranker import parse_rerank_order
            _PARSE_RERANK_ORDER = parse_rerank_order
        except Exception:
            _PARSE_RERANK_ORDER = None
    return _PARSE_RERANK_ORDER


def _get_field(s, name: str, default=""):
    """dataclass(getattr) 또는 dict(.get) 양쪽에서 필드를 안전하게 읽는다(순수)."""
    if s is None:
        return default
    if isinstance(s, dict):
        v = s.get(name, default)
    else:
        v = getattr(s, name, default)
    return v if v is not None else default


def _as_list(x) -> list:
    """임의 입력을 리스트로 방어적으로 변환한다(``None``/비이터러블 → ``[]``)."""
    if x is None:
        return []
    if isinstance(x, list):
        return x
    try:
        return list(x)
    except TypeError:
        return []


# --- 출처 신뢰도 (source_authority) — 요구사항 9.4 ---------------------------

# 도메인 권위 신호용 큐레이션 집합(정규 학술/정부/표준화 기관 등). suffix 매칭으로
# 서브도메인(예: ``pubmed.ncbi.nlm.nih.gov``)도 포함한다.
_HIGH_AUTHORITY_DOMAINS = frozenset({
    "wikipedia.org", "nature.com", "science.org", "sciencemag.org", "cell.com",
    "thelancet.com", "nejm.org", "pnas.org", "springer.com", "link.springer.com",
    "sciencedirect.com", "onlinelibrary.wiley.com", "jstor.org", "ieee.org",
    "ieeexplore.ieee.org", "acm.org", "dl.acm.org", "arxiv.org", "biorxiv.org",
    "medrxiv.org", "aclanthology.org", "openreview.net", "nih.gov",
    "ncbi.nlm.nih.gov", "who.int", "nasa.gov", "nist.gov", "europa.eu",
    "oecd.org", "worldbank.org", "imf.org", "un.org", "doi.org",
    "semanticscholar.org", "openalex.org", "scholar.google.com",
})

# 상위 게재처(venue) 신호용 키워드(부분 일치, 소문자). 저명 저널/학회 약칭 포함.
_TOP_VENUE_KEYWORDS = (
    "nature", "science", "cell", "lancet", "nejm", "pnas",
    "neurips", "nips", "icml", "iclr", "aaai", "ijcai", "cvpr", "iccv",
    "eccv", "acl", "emnlp", "naacl", "sigir", "kdd", "www ", "the web conference",
    "vldb", "sigmod", "icde", "osdi", "sosp", "nsdi", "usenix", "sigcomm",
    "focs", "stoc", "soda", "pods",
)


def _host_from_url(value) -> str:
    """URL 문자열에서 호스트를 추출한다(순수·예외 없음). DOI/비URL은 ``""``."""
    if not isinstance(value, str):
        return ""
    s = value.strip()
    if not s:
        return ""
    low = s.lower()
    # DOI 표기는 호스트가 없다.
    if low.startswith("doi:") or low.startswith("10."):
        return ""
    # 스킴 없는 bare 호스트는 netloc 파싱을 위해 "//" 부여.
    if "://" not in s and not s.startswith("//"):
        s = "//" + s
    try:
        host = urlsplit(s).hostname or ""
    except ValueError:
        return ""
    return host.lower()


def _domain_of(s) -> str:
    """소스의 도메인을 추출한다(순수). ``source_domain`` 우선, 없으면 URL 파생."""
    dom = _get_field(s, "source_domain", "")
    if isinstance(dom, str) and dom.strip():
        d = dom.strip().lower()
        return d[4:] if d.startswith("www.") else d
    for fname in ("url", "url_or_doi", "doi_or_url"):
        host = _host_from_url(_get_field(s, fname, ""))
        if host:
            return host[4:] if host.startswith("www.") else host
    return ""


def _domain_authority(domain: str) -> float:
    """도메인 권위 신호를 ``[0, 1)`` 로 산출한다(순수·결정적).

    큐레이션된 학술/정부/표준화 도메인은 높은 값, ``.gov``/``.edu``/``.ac.*`` 등
    기관 TLD는 준하는 값, ``.org`` 는 중간값, 일반 도메인은 낮은 기본값을 준다.
    """
    d = (domain or "").strip().lower().lstrip(".")
    if not d:
        return 0.0
    if d.startswith("www."):
        d = d[4:]
    for hi in _HIGH_AUTHORITY_DOMAINS:
        if d == hi or d.endswith("." + hi):
            return 0.9
    labels = d.split(".")
    tld = labels[-1] if labels else ""
    if len(labels) >= 2 and labels[-2] in ("ac", "edu", "gov", "go", "mil", "res"):
        return 0.85  # 예: ac.kr, edu.au, gov.uk, go.jp
    if tld in ("gov", "mil", "edu"):
        return 0.9
    if tld == "int":
        return 0.8
    if tld == "org":
        return 0.5
    return 0.3


def _venue_score(venue) -> float:
    """게재처(venue) 신호를 산출한다(순수). 저명 venue 0.8, 그 외 존재 시 0.5."""
    if not isinstance(venue, str):
        return 0.0
    v = venue.strip().lower()
    if not v:
        return 0.0
    for kw in _TOP_VENUE_KEYWORDS:
        if kw in v:
            return 0.8
    return 0.5


def _citation_score(cc) -> float:
    """피인용수 신호를 ``[0, 1)`` 로 산출한다(순수, log-스케일 포화·결정적).

    ``lc / (lc + log1p(50))`` (``lc = log1p(cc)``): cc=0→0, cc=50→0.5, 이후 완만히
    증가하되 1 에 도달하지 않아 noisy-OR 결합에서 포화하지 않는다.
    """
    n = _as_sort_number(cc)
    if n <= 0:
        return 0.0
    lc = math.log1p(n)
    return lc / (lc + math.log1p(50.0))


def source_authority(s) -> float:
    """소스의 출처 신뢰도 신호를 ``[0, 1]`` 로 산출한다 (요구사항 9.4).

    도메인 권위·게재처(venue)·피인용수 중 **사용 가능한 신호(1개 이상)** 를
    noisy-OR(``1 - ∏(1 - signal)``)로 결합한다. 각 신호는 단조적이며 어느 하나만
    있어도 신뢰도가 반영되고, 여러 신호가 함께 있으면 값이 상승한다. 외부 상태에
    의존하지 않는 순수·결정적 함수이며, 입력 타입(dataclass/dict/None)에 방어적이다.
    ``merge_and_rerank`` 는 이 값을 순위 산정 입력(신뢰도 순위 리스트)으로 사용한다.

    Args:
        s: ``SearchResult``/``PaperResult``/``EvidenceSource`` dataclass, ``dict``,
            또는 이에 상응하는 필드를 갖는 객체. ``None`` 은 ``0.0``.

    Returns:
        ``[0.0, 1.0]`` 범위의 신뢰도 신호(높을수록 권위 있음).
    """
    if s is None:
        return 0.0
    dom = _domain_authority(_domain_of(s))
    ven = _venue_score(_get_field(s, "venue", ""))
    cite = _citation_score(_get_field(s, "citation_count", 0))
    # noisy-OR: 어느 단일 신호도 기여하고, 다중 신호는 상승 결합(단조·유계).
    inv = (1.0 - dom) * (1.0 - ven) * (1.0 - cite)
    score = 1.0 - inv
    if score <= 0.0:
        return 0.0
    return 1.0 if score >= 1.0 else score


# --- 융합·재랭킹 (merge_and_rerank) — 요구사항 7.5/7.6, Property 4 -------------


def _resolve_position(item, key_to_pos: dict, sources_list: list):
    """순위 리스트 항목을 dedup 풀 내 위치로 해석한다(없으면 ``None``).

    항목은 소스 객체/dict/URL·DOI 문자열이거나, ``sources`` 인덱스를 가리키는
    정수일 수 있다. 어느 경우든 ``source_key`` 로 풀 위치에 매핑한다(순수).
    """
    if isinstance(item, bool):  # bool 은 인덱스로 오해되지 않도록 배제
        return None
    if isinstance(item, int):
        if 0 <= item < len(sources_list):
            return key_to_pos.get(source_key(sources_list[item]))
        return None
    return key_to_pos.get(source_key(item))


def _fallback_merge(rank_lists: list, n: int) -> list:
    """RRF 재사용 불가 시 결정적 폴백 순서(비-RRF, first-appearance 병합).

    각 순위 리스트를 순서대로 훑어 최초 등장 위치를 보존한다. RRF 점수 계산을
    재구현하지 않는 단순·결정적 병합이며, 누락 위치는 호출부에서 자연순으로 보강한다.
    """
    ordered = []
    seen = set()
    for rl in rank_lists:
        for p in rl:
            if isinstance(p, int) and 0 <= p < n and p not in seen:
                seen.add(p)
                ordered.append(p)
    return ordered


def _fuse_positions(rank_lists: list, n: int) -> list:
    """순위 리스트들을 융합해 ``[0, n)`` 전 위치의 결정적 순열을 반환한다.

    ``rag.hybrid_search.rrf_fuse`` 로 융합하고(재사용), import·호출 실패 시
    ``_fallback_merge`` 로 폴백한다. 어느 경로든 누락 위치를 자연순(0..n-1)으로
    보강하고 중복을 제거해 전 위치 순열을 보장한다(P4 커버리지).
    """
    rrf_fuse = _get_rrf_fuse()
    ordered = None
    if rrf_fuse is not None:
        try:
            fused = rrf_fuse(rank_lists)
            ordered = [
                idx for idx, _score in fused
                if isinstance(idx, int) and 0 <= idx < n
            ]
        except Exception:
            ordered = None
    if ordered is None:
        ordered = _fallback_merge(rank_lists, n)

    out = []
    seen = set()
    for p in ordered:  # 융합 결과(중복 제거)
        if p not in seen:
            seen.add(p)
            out.append(p)
    for p in range(n):  # 누락 위치를 자연순으로 보강 → 전 위치 순열 보장
        if p not in seen:
            seen.add(p)
            out.append(p)
    return out


def _candidate_summary(s) -> str:
    """재랭커 입력용 사람이 읽을 수 있는 후보 요약을 만든다(순수)."""
    title = str(_get_field(s, "title", "")).strip()
    body = str(_get_field(s, "snippet", "") or _get_field(s, "abstract", "")).strip()
    text = (title + " " + body).strip()
    if text:
        return text[:300]
    sid = _get_field(s, "source_id", "") or source_key(s)
    return str(sid)


def _rerank_raw_to_text(raw) -> str:
    """재랭커 원응답(문자열/인덱스 리스트/기타)을 parse 입력 텍스트로 정규화."""
    if isinstance(raw, str):
        return raw
    if isinstance(raw, (list, tuple)):
        return " ".join(str(x) for x in raw)
    return str(raw)


def _invoke_reranker(gw, query, summaries):
    """gw 에서 재랭킹 순서 신호를 **동기적으로** 얻는다(불가/비동기면 ``None``).

    지원 형태(덕 타이핑): 호출 가능한 ``gw(query, summaries)``, 또는
    ``rerank_order``/``rerank_text``/``rerank`` 메서드. 코루틴을 반환하는 비동기
    재랭커는 순수·비차단 계약을 위해 여기서 대기하지 않고 ``None`` 으로 폴백한다
    (실제 LLM 재랭킹의 async 실행은 파이프라인 계층에서 어댑터로 주입).
    """
    fn = gw if callable(gw) else None
    if fn is None:
        for meth in ("rerank_order", "rerank_text", "rerank"):
            cand = getattr(gw, meth, None)
            if callable(cand):
                fn = cand
                break
    if fn is None:
        return None
    res = fn(query, summaries)
    if inspect.isawaitable(res):
        # 비동기 결과는 동기 맥락에서 대기 불가 → 코루틴 정리 후 폴백(비차단).
        if inspect.iscoroutine(res):
            res.close()
        return None
    return res


def _apply_gw_rerank(gw, query, cand: list):
    """후보(cand)를 gw 재랭킹으로 재정렬한다. 실패/불가 시 ``None``(폴백 신호).

    ``rag.reranker.parse_rerank_order`` 규약을 재사용해 gw 가 범위밖/중복/누락
    인덱스를 내도 결과는 항상 ``cand`` 의 순열이 된다(P4 계승). 어떤 예외도
    삼켜 비차단 폴백한다.
    """
    parse_rerank_order = _get_parse_rerank_order()
    if parse_rerank_order is None:
        return None
    n = len(cand)
    if n <= 1:
        return list(cand)
    try:
        summaries = [_candidate_summary(s) for s in cand]
        raw = _invoke_reranker(gw, query, summaries)
        if raw is None:
            return None
        order = parse_rerank_order(_rerank_raw_to_text(raw), n)  # [0,n) 순열 보장
        return [cand[i] for i in order]
    except Exception:
        return None


def merge_and_rerank(query, per_provider_ranklists, sources, *, gw=None) -> list:
    """다중 제공자 순위 리스트를 융합·중복제거·재랭킹한다 (요구사항 7.5/7.6/9.4, P4).

    조립(재구현 금지):
        1. ``dedup_sources(sources)`` 로 순열 대상 풀(pool)을 확정한다(first-wins).
        2. 각 제공자 순위 리스트를 ``source_key`` 로 pool 위치에 매핑한다(정수 항목은
           ``sources`` 인덱스로 해석). pool 밖 항목은 무시한다.
        3. ``source_authority`` 로 정렬한 **신뢰도 순위 리스트**를 추가해 출처 신뢰도
           신호를 순위 산정 입력에 반영한다(요구사항 9.4).
        4. ``rag.hybrid_search.rrf_fuse`` 로 융합한다(재사용). import·호출 실패 시
           결정적 폴백 병합.
        5. ``dedup_sources`` 로 (방어적) 중복제거한다.
        6. ``gw`` 가 주어지면 ``rag.reranker.parse_rerank_order`` 순열 규약으로
           재정렬한다(실패/불가 시 폴백).

    결과는 항상 ``dedup_sources(sources)`` 의 **순열**(창작·누락 없음 — P4)이다.
    ``gw`` 미제공이거나 융합/재랭킹 자산이 실패해도 결정적 폴백으로 비차단 동작하며,
    입력 리스트·항목을 변경하지 않는 순수 조합 함수다.

    Args:
        query: 재랭킹 컨텍스트용 질의 문자열(폴백 경로에서는 사용하지 않음).
        per_provider_ranklists: 제공자별 순위 리스트의 리스트. 각 리스트는 소스
            객체/dict/URL·DOI 문자열 또는 ``sources`` 인덱스(int)의 시퀀스.
            ``None``/빈 값에 방어적이다.
        sources: 순열 대상 후보 집합(제공자 결과의 합집합 등). ``None``/빈 입력은
            빈 리스트로 방어한다.
        gw: (선택) 동기 재랭킹 신호를 제공하는 호출 가능 객체/어댑터. ``None`` 이면
            융합·신뢰도 기반 결정적 순서를 그대로 반환한다.

    Returns:
        ``dedup_sources(sources)`` 를 재정렬한 새 리스트(원본 소스 객체 참조 유지).
    """
    pool = dedup_sources(sources)
    if not pool:
        return []
    n = len(pool)
    if n == 1:
        return list(pool)

    # 각 dedup 키 → pool 최초 위치(dedup 가 first-wins 를 이미 보장).
    key_to_pos: dict = {}
    for pos, s in enumerate(pool):
        key_to_pos.setdefault(source_key(s), pos)

    sources_list = _as_list(sources)

    # 제공자 순위 리스트 → pool 위치 리스트(리스트 내 중복/미해석 항목 제거).
    rank_lists: list = []
    for rl in _as_list(per_provider_ranklists):
        positions = []
        seen = set()
        for item in _as_list(rl):
            p = _resolve_position(item, key_to_pos, sources_list)
            if p is None or p in seen:
                continue
            seen.add(p)
            positions.append(p)
        if positions:
            rank_lists.append(positions)

    # 출처 신뢰도 순위 리스트(신뢰도 내림차순, 동점은 위치 오름차순 — 결정적).
    authorities = [source_authority(pool[p]) for p in range(n)]
    rank_lists.append(sorted(range(n), key=lambda p: (-authorities[p], p)))

    # 융합 → 전 위치 순열 → 소스로 환원 → (방어적) 중복제거.
    fused_positions = _fuse_positions(rank_lists, n)
    cand = dedup_sources([pool[p] for p in fused_positions])

    # (선택) gw 재랭킹 — parse_rerank_order 규약으로 순열 유지(P4). 실패 시 폴백.
    if gw is not None:
        reranked = _apply_gw_rerank(gw, query, cand)
        if reranked is not None:
            return reranked
    return cand
