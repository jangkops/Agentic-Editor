"""deep-research-engine: 단일 외부 egress 백엔드 — 옵트인/동의 게이트 (골격).

이 모듈은 외부 리서치(웹/논문 검색 + 본문 조회)의 **유일한 네트워크 egress
지점**이 될 파일이다. steering의 Gateway-only 정책상 외부 HTTP 호출(httpx)은
반드시 이 모듈 안에서만 일어나야 하며, 그 밖의 어떤 research 모듈에서도 HTTP
egress를 두지 않는다(요구사항 10.4, 정적 가드 대상 — task 18.2). LLM·추론 호출은
여기서 수행하지 않고 전부 Bedrock Gateway 경유를 유지한다(요구사항 10.1 / 10.5).

이 모듈이 노출하는 함수:

- ``web_research_enabled(env=None)`` : Search_Provider_Flag. 옵트인
  (``AE_ENABLE_WEB_RESEARCH``)과 사용자 동의(``AE_RESEARCH_CONSENT``)가 **모두
  참**일 때만 True를 반환한다. 기본값은 둘 다 off이므로 False(무회귀). (task 9.1)
- ``web_search_raw(provider, query, *, top_k, timeout, recency=None)`` : 웹 제공자
  1곳(Tavily/Exa/Brave)을 HTTPX로 원시 검색해 어댑터가 파싱할 원시 dict를
  반환한다. (task 9.3)
- ``academic_search_raw(provider, query, *, top_k, timeout)`` : 논문 제공자
  1곳(Semantic Scholar/OpenAlex/arXiv/PubMed)을 HTTPX로 원시 검색한다. arXiv/
  PubMed의 XML 응답은 어댑터가 기대하는 xmltodict 스타일 dict로 환원한다. (task 9.3)
- ``fetch_url_raw(url, *, timeout, max_chars)`` : Content_Fetcher. **http/https 스킴만**
  허용하고 개별 타임아웃(``AE_FETCH_TIMEOUT``)·본문 크기 상한(``AE_FETCH_MAX_CHARS``)을
  적용해 단일 소스 URL을 HTTPX로 조회한다. HTML 응답은 기존 의존성 ``lxml``로 본문
  텍스트를 추출(script/style 제거·공백 정규화)해 구조화 결과 ``FetchResult``로 반환하며,
  실패/타임아웃/4xx·5xx는 예외 없이 ``ok=False``로 폴백한다(P8 / 요구사항 3.5). (task 9.4)
- ``search_web_with_fallback(query, providers=None, *, ...)`` /
  ``search_academic_with_fallback(query, providers=None, *, ...)`` : 다중 제공자 폴백
  체인. 설정된 제공자 순서(1차→보조→폴백)대로 위 egress 함수를 시도해 첫 성공의 원시
  응답을 반환하고, 전 제공자가 실패하면 예외 없이 "외부 근거 미확보"
  (``no_external_evidence``) 구조화 신호로 비차단 폴백한다(요구사항 13.2 / 13.3 / P8).
  진입 시 옵트인 게이트와 질의 입력 검증을 먼저 수행한다. (task 9.5)

입력 검증(task 9.5, 요구사항 1.8 / P8): 모든 egress 함수와 폴백 체인은 질의를 진입
시점에 단일 헬퍼(``_validate_query``)로 검증한다 — ``strip()`` 후 비어 있지 않고 최대
길이(``_MAX_QUERY_LEN`` = 2,048자) 이하여야 하며, 위반 시 제공자를 호출하지 않고
``invalid_query`` 구조화 오류로 폴백한다(예외 없음, 중복 로직 통합).

egress 계약(요구사항 10 / 11 / 13, 정확성 속성 P8·P9·P15):

- **옵트인 게이트 우선.** 모든 egress 함수는 진입 시 ``web_research_enabled()``를
  먼저 확인하고, False면 네트워크를 호출하지 않고 즉시 구조화 오류 dict로
  폴백한다(무회귀 — 요구사항 10.3 / 14.2 / P15).
- **자격증명 주입·마스킹.** 키는 ``security.load_credential(provider)``로 env에서만
  로딩하며(파일 미저장 — 11.1 / 11.2), 로그에는 ``mask_secret``(앞 4자 + ``*``)만
  기록한다(11.4). 오류 dict·반환 값 어디에도 자격증명 원문을 담지 않는다(P9). 오류
  ``detail``에는 요청 URL(질의 파라미터에 키가 실릴 수 있음)을 넣지 않고 예외 종류/
  상태코드 등 비민감 정보만 담는다.
- **개별 타임아웃·비차단 폴백.** 각 호출은 인자로 받은 개별 ``timeout``(제공자별
  ``AE_SEARCH_TIMEOUT``)을 적용한다. httpx 예외·타임아웃·HTTP 4xx/5xx·JSON/XML 파싱
  실패는 모두 잡아 예외를 전파하지 않고 ``{"error": code, "detail": ...}`` 구조화
  오류 dict로 폴백한다(P8 / 요구사항 13.1 / 13.2). 반환된 오류 dict를 어댑터에
  넘겨도 결과 배열이 없어 빈 리스트가 나오므로 파이프라인은 차단되지 않는다.

스택 제약(요구사항 15 / 10.5): Python 3.11 표준 라이브러리 + ``httpx``(기존 의존성)
+ 사내 ``config``/``security`` 재사용만 사용한다. ``boto3``/``anthropic``/``openai``
등 직접 모델 SDK는 import하지 않는다(정적 가드 대상 — task 18.2). 외부 HTTP egress는
research 패키지에서 **이 파일에만** 존재한다(요구사항 10.4). 본문 수집 egress
(``fetch_url_raw``, Content_Fetcher)도 이 파일에 함께 존재한다(task 9.4).

Requirements: 1.1, 2.1, 3.1, 3.2, 3.3, 3.4, 3.5, 3.6, 10.2, 10.3, 10.4, 11.4, 13.1, 13.2, 14.2
Design: "Components and Interfaces" > "1) backend.py — 단일 외부 egress"
"""

import ipaddress
import logging
import re
import socket
from dataclasses import dataclass
from typing import Optional
from urllib.parse import urljoin, urlparse
from xml.etree import ElementTree as ET

import httpx

from .config import DeepResearchConfig
from .security import load_credential, mask_secret

logger = logging.getLogger(__name__)

__all__ = [
    "web_research_enabled",
    "web_search_raw",
    "academic_search_raw",
    "fetch_url_raw",
    "FetchResult",
    "search_web_with_fallback",
    "search_academic_with_fallback",
]


def web_research_enabled(env=None) -> bool:
    """외부 리서치 egress 활성 여부(Search_Provider_Flag) — 옵트인 AND 동의.

    ``DeepResearchConfig.from_env``를 재사용해 두 플래그를 읽고, **둘 다 참**일
    때만 True를 반환한다:

    - ``AE_ENABLE_WEB_RESEARCH`` (옵트인, ``cfg.enable_web_research``)
    - ``AE_RESEARCH_CONSENT``    (프라이버시 동의, ``cfg.consent``)

    둘 중 하나라도 off면 외부 검색·본문 조회를 수행하지 않고 기존 로컬 검색
    동작만 유지해야 한다(요구사항 10.3 / 14.2, 무회귀). 기본값은 둘 다 off이므로
    반환값 기본은 False다.

    Args:
        env: 환경변수 매핑. ``None``이면 ``os.environ``을 사용한다. 빈 dict
             (``{}``)를 넘기면 두 플래그가 모두 기본 off로 해석되어 False.

    Returns:
        옵트인과 동의가 모두 참이면 ``True``, 그 외에는 ``False`` (``bool`` 싱글턴).
    """
    cfg = DeepResearchConfig.from_env(env)
    return bool(cfg.enable_web_research and cfg.consent)


# =========================================================================== #
# 외부 검색 egress (task 9.3) — research 패키지에서 HTTP egress가 허용되는 유일한 곳
# =========================================================================== #
# 제공자 name은 providers.py 어댑터(get_web_adapter/get_academic_adapter)와 정합한다.
# 반환하는 원시 dict는 해당 어댑터(to_search_results/to_paper_results)가 그대로 파싱할
# 수 있는 형태다. 실제 HTTP 호출은 오직 이 함수들 안에서만 일어난다(요구사항 10.4).

_UA = "MogamWorks-DeepResearch/1.0 (Agentic-Editor)"

# 웹 검색 제공자 (providers.WebSearchAdapter.name 정합)
_WEB_PROVIDERS = ("tavily", "exa", "brave")
# 학술 검색 제공자 (providers.AcademicSearchAdapter.name 정합)
# europepmc: 키리스 생의학 문헌(MEDLINE+PMC). semantic_scholar 가 키 없이 429 로
# 막히고 arxiv 가 자주 TIMEOUT 이라 학술 검색이 openalex 단독에 의존하던 문제를
# 해소하기 위해 추가했다(실측 근거는 EuropePmcAdapter docstring 참조).
_ACADEMIC_PROVIDERS = ("semantic_scholar", "openalex", "arxiv", "pubmed", "europepmc")
# 자격증명이 **필수**인 제공자 — 키가 없으면 네트워크 호출 없이 missing_credential.
#
# tavily 는 여기서 제외한다: Tavily 가 키리스 모드
# (``X-Tavily-Access-Mode: keyless``)를 제공하므로 키 없이도 동일 스키마로 검색이
# 된다. 30명 배포에서 개인별 키 발급을 강제하지 않기 위한 기본 경로다. 키가 있으면
# Bearer 로 승격되어 레이트리밋만 올라간다(_search_tavily 참조).
# exa/brave 는 키리스 경로가 없어 그대로 필수다.
# 그 외(semantic_scholar/openalex/arxiv/pubmed/europepmc)는 키리스 또는 선택 키이므로
# 미설정이어도 호출한다(S2/PubMed 키는 있으면 레이트리밋 완화).
_REQUIRES_KEY = frozenset({"exa", "brave"})

# 제공자별 엔드포인트 (design "제공자 선정" / 표준 공개 API).
_TAVILY_URL = "https://api.tavily.com/search"
_EXA_URL = "https://api.exa.ai/search"
_BRAVE_URL = "https://api.search.brave.com/res/v1/web/search"
_S2_URL = "https://api.semanticscholar.org/graph/v1/paper/search"
_S2_FIELDS = "title,authors,year,venue,abstract,externalIds,url,citationCount"
_OPENALEX_URL = "https://api.openalex.org/works"
_ARXIV_URL = "https://export.arxiv.org/api/query"
_EPMC_URL = "https://www.ebi.ac.uk/europepmc/webservices/rest/search"
_PUBMED_ESEARCH_URL = "https://eutils.ncbi.nlm.nih.gov/entrez/eutils/esearch.fcgi"
_PUBMED_EFETCH_URL = "https://eutils.ncbi.nlm.nih.gov/entrez/eutils/efetch.fcgi"


class _EgressError(Exception):
    """내부 egress 실패 신호 — 공개 경계에서 구조화 오류 dict로 변환된다.

    ``code``는 구조화 오류 dict의 ``error`` 값이 되고, ``detail``은 자격증명을
    포함하지 않는 안전한 설명 문자열이다(P9). 요청 URL(키가 파라미터로 실릴 수
    있음)이나 예외 원문은 detail에 넣지 않는다.
    """

    def __init__(self, code: str, detail: str = ""):
        super().__init__(code)
        self.code = code
        self.detail = detail


def _error(code: str, detail: str = "", **extra) -> dict:
    """구조화 오류 dict를 만든다: ``{"error": code, "detail": detail, ...extra}``.

    자격증명 원문은 어떤 필드에도 포함하지 않는다(P9). ``extra``에는 provider나
    status_code 같은 비민감 컨텍스트만 담는다. 이 dict를 어댑터에 넘겨도 결과
    배열이 없어 빈 리스트가 반환되므로 파이프라인은 비차단으로 진행한다(P8).
    """
    err = {"error": code, "detail": detail}
    err.update(extra)
    return err


# 최대 질의 길이(문자 수). strip 후 이 값을 초과하는 질의는 제공자를 호출하지 않고
# invalid_query로 폴백한다(design "Error Handling > 입력 검증" / 요구사항 1.8, P8).
_MAX_QUERY_LEN = 2048

# 제공자별 최소 타임아웃(초) — 전역 기본(AE_SEARCH_TIMEOUT, 12s)이 너무 짧은 곳만 올린다.
#
# 실측 근거: arXiv export API 는 `all:` 전체 필드 질의에서 12s 를 넘기는 일이 흔해,
# 제공자를 선택해도 매번 TIMEOUT 으로 떨어져 결과가 0건이었다. PubMed 는 ESearch +
# EFetch 두 번 왕복이라 같은 이유로 여유가 필요하다. 상한이 아니라 **하한**이므로
# 사용자가 AE_SEARCH_TIMEOUT 을 더 크게 잡으면 그 값을 그대로 존중한다.
_MIN_TIMEOUT_BY_PROVIDER = {
    "arxiv": 25.0,
    "pubmed": 20.0,
}


def _effective_timeout(provider: str, timeout) -> float:
    """제공자별 하한을 적용한 타임아웃(순수). 잘못된 입력은 하한/기본으로 방어."""
    floor = _MIN_TIMEOUT_BY_PROVIDER.get(provider, 0.0)
    try:
        t = float(timeout)
    except (TypeError, ValueError):
        t = 0.0
    return max(t, floor) or 12.0


def _validate_query(query, *, provider: str = ""):
    """질의 입력 검증(순수, 예외 없음). ``(정제질의, None)`` 또는 ``(None, 오류dict)``.

    egress 진입(``_prepare``)과 폴백 체인이 공유하는 **단일 검증 지점**이다(중복 로직
    통합 — task 9.5). 규칙(요구사항 1.8 / P8 / design "입력 검증"):

    - ``str``이 아니거나 ``strip()`` 후 비어 있으면 ``invalid_query``.
    - ``strip()`` 후 문자 수가 ``_MAX_QUERY_LEN``(2,048)을 초과하면 ``invalid_query``.

    위반 시 제공자를 호출하지 않도록 구조화 오류 dict를 돌려주고(예외 없음), 통과 시
    앞뒤 공백을 제거한 정제 질의 문자열을 돌려준다.

    Args:
        query: 검증할 질의(문자열이 아니어도 방어적으로 처리).
        provider: 오류 dict에 부착할 제공자 이름(있을 때만 포함). 폴백 체인 진입처럼
            제공자가 아직 특정되지 않은 경우 생략한다.

    Returns:
        ``(query, None)`` (검증 통과) 또는 ``(None, error_dict)`` (검증 실패).
    """
    extra = {"provider": provider} if provider else {}
    q = query.strip() if isinstance(query, str) else ""
    if not q:
        return None, _error("invalid_query", "query is empty after trim", **extra)
    if len(q) > _MAX_QUERY_LEN:
        return None, _error(
            "invalid_query", f"query exceeds max length {_MAX_QUERY_LEN}", **extra
        )
    return q, None


def _safe_top_k(top_k, default: int = 10) -> int:
    """top_k를 양의 정수로 정규화한다(형식 오류/0/음수 → 기본값)."""
    try:
        v = int(top_k)
    except (TypeError, ValueError):
        return default
    return v if v > 0 else default


def _recency_to_days(recency) -> Optional[int]:
    """recency 힌트에서 선행 정수 '일수'를 추출한다(추출 불가 시 None).

    provider-side 최신성은 best-effort 힌트이며, 결정적 최신성 필터는
    ``rank.apply_recency``가 담당한다(P12). 예: ``"7"`` / ``"7d"`` / ``"last 30 days"``
    → 첫 숫자. 숫자가 없으면 None(제공자 기본 동작 유지).
    """
    if recency is None:
        return None
    m = re.search(r"\d+", str(recency))
    if not m:
        return None
    try:
        days = int(m.group())
    except ValueError:
        return None
    return days if days > 0 else None


def _brave_freshness(days: Optional[int]) -> Optional[str]:
    """일수를 Brave ``freshness`` 코드(pd/pw/pm/py)로 매핑한다(범위 밖/None → None)."""
    if days is None:
        return None
    if days <= 1:
        return "pd"
    if days <= 7:
        return "pw"
    if days <= 31:
        return "pm"
    if days <= 366:
        return "py"
    return None


# --------------------------------------------------------------------------- #
# HTTP 실행 헬퍼 (성공 응답만 반환, 실패는 _EgressError로 승격 — 공개 경계에서 폴백)
# --------------------------------------------------------------------------- #
def _parse_json(resp: httpx.Response) -> dict:
    """httpx 응답을 JSON dict로 파싱한다(파싱 실패/비-object → parse_error)."""
    try:
        data = resp.json()
    except ValueError as exc:  # json.JSONDecodeError ⊂ ValueError
        raise _EgressError("parse_error", "response body was not valid JSON") from exc
    if not isinstance(data, dict):
        raise _EgressError("parse_error", "response JSON was not an object")
    return data


async def _get_json(client, url, *, params=None, headers=None) -> dict:
    """GET → JSON dict. HTTP 4xx/5xx는 raise_for_status가 HTTPStatusError로 승격."""
    resp = await client.get(url, params=params, headers=headers)
    resp.raise_for_status()
    return _parse_json(resp)


async def _post_json(client, url, *, json_body=None, headers=None) -> dict:
    """POST(JSON 본문) → JSON dict. 상동."""
    resp = await client.post(url, json=json_body, headers=headers)
    resp.raise_for_status()
    return _parse_json(resp)


async def _get_xml(client, url, *, params=None, headers=None) -> dict:
    """GET → XML을 어댑터가 기대하는 xmltodict 스타일 dict로 환원한다.

    루트 태그를 키로 감싸(``{root_tag: {...}}``) providers.py의 arXiv/PubMed
    어댑터 경로(``("feed","entry")`` / ``("PubmedArticleSet","PubmedArticle")``)와
    정합시킨다. 파싱 실패는 parse_error로 승격한다.
    """
    resp = await client.get(url, params=params, headers=headers)
    resp.raise_for_status()
    try:
        root = ET.fromstring(resp.content)
    except ET.ParseError as exc:
        raise _EgressError("parse_error", "response body was not valid XML") from exc
    return {_strip_ns(root.tag): _elem_to_obj(root)}


def _strip_ns(tag) -> str:
    """XML 태그/속성명에서 ``{namespace}`` 접두를 제거한다(예: Atom/arXiv 네임스페이스)."""
    if not isinstance(tag, str):
        return ""
    return tag.rsplit("}", 1)[-1]


def _elem_to_obj(elem):
    """ElementTree Element → xmltodict 스타일 값(순수, 예외 없음).

    규칙(providers.py의 ``_xml_text``/``@EIdType`` 소비 방식과 정합):
        - 속성은 ``@name`` 키로 담는다.
        - 텍스트가 자식/속성과 공존하면 ``#text`` 키로 담는다.
        - 자식·속성이 없는 텍스트 전용 요소는 문자열을 직접 반환한다.
        - 같은 태그가 반복되면 list로 모은다(단일이면 dict/문자열 그대로).
    """
    obj: dict = {}
    for key, val in elem.attrib.items():
        obj["@" + _strip_ns(key)] = val
    for child in elem:
        tag = _strip_ns(child.tag)
        val = _elem_to_obj(child)
        if tag in obj:
            if not isinstance(obj[tag], list):
                obj[tag] = [obj[tag]]
            obj[tag].append(val)
        else:
            obj[tag] = val
    text = (elem.text or "").strip()
    if obj:
        if text:
            obj["#text"] = text
        return obj
    return text


# --------------------------------------------------------------------------- #
# 제공자별 요청 빌더 (각 함수는 성공 시 원시 dict 반환, 실패는 _EgressError 승격)
# --------------------------------------------------------------------------- #
async def _search_tavily(client, query, top_k, recency, key) -> dict:
    """Tavily ``POST /search`` — 키가 있으면 Bearer, 없으면 **키리스 모드**.

    키리스 모드(``X-Tavily-Access-Mode: keyless``)는 계정·API 키 없이 동일한 응답
    스키마로 검색을 제공한다. 30명 배포에서 개인별 키 발급을 요구하지 않기 위해
    기본 경로로 쓴다. 키를 넣으면 그대로 Bearer 인증으로 승격되어 레이트리밋이
    올라가며, 응답 스키마가 같아 어댑터·파서는 변경이 없다.
    출처: https://docs.tavily.com/documentation/keyless (/search·/extract 지원)
    """
    body = {"query": query, "max_results": top_k, "search_depth": "advanced"}
    days = _recency_to_days(recency)
    if days is not None:
        # Tavily는 news 토픽에서만 days 창을 적용한다(최신성 best-effort 힌트).
        body["topic"] = "news"
        body["days"] = days
    if key:
        headers = {"Authorization": f"Bearer {key}"}
    else:
        headers = {"X-Tavily-Access-Mode": "keyless"}
    return await _post_json(client, _TAVILY_URL, json_body=body, headers=headers)


async def _search_exa(client, query, top_k, recency, key) -> dict:
    """Exa ``POST /search`` — ``x-api-key`` 헤더 + JSON 본문(본문/하이라이트 요청)."""
    body = {
        "query": query,
        "numResults": top_k,
        "contents": {"text": True, "highlights": True},
    }
    headers = {"x-api-key": key}
    return await _post_json(client, _EXA_URL, json_body=body, headers=headers)


async def _search_brave(client, query, top_k, recency, key) -> dict:
    """Brave ``GET /web/search`` — ``X-Subscription-Token`` 헤더 + 쿼리 파라미터."""
    params = {"q": query, "count": top_k}
    freshness = _brave_freshness(_recency_to_days(recency))
    if freshness:
        params["freshness"] = freshness
    headers = {"Accept": "application/json", "X-Subscription-Token": key}
    return await _get_json(client, _BRAVE_URL, params=params, headers=headers)


async def _search_semantic_scholar(client, query, top_k, key) -> dict:
    """Semantic Scholar ``GET /paper/search`` — 선택 ``x-api-key`` 헤더 + 필드 지정."""
    params = {"query": query, "limit": top_k, "fields": _S2_FIELDS}
    headers = {"x-api-key": key} if key else None
    return await _get_json(client, _S2_URL, params=params, headers=headers)


async def _search_openalex(client, query, top_k) -> dict:
    """OpenAlex ``GET /works`` — 키리스(``search`` + ``per-page``)."""
    params = {"search": query, "per-page": top_k}
    return await _get_json(client, _OPENALEX_URL, params=params)


async def _search_arxiv(client, query, top_k) -> dict:
    """arXiv ``GET /api/query`` — 키리스, Atom(XML) 응답을 dict로 환원."""
    params = {"search_query": f"all:{query}", "start": 0, "max_results": top_k}
    return await _get_xml(client, _ARXIV_URL, params=params)


async def _search_europepmc(client, query, top_k) -> dict:
    """Europe PMC ``GET /search`` — 키리스, JSON.

    ``resultType=core`` 를 쓴다. 기본값(``lite``)은 ``abstractText`` 를 생략해
    근거 문장을 만들 수 없기 때문이다(초록이 없으면 인용·검증 단계에서 쓸모가 없다).
    """
    params = {
        "query": query,
        "format": "json",
        "pageSize": top_k,
        "resultType": "core",
    }
    return await _get_json(client, _EPMC_URL, params=params)


async def _search_pubmed(client, query, top_k, key) -> dict:
    """PubMed E-utilities — ESearch(JSON)로 PMID 조회 후 EFetch(XML)로 본문 조회.

    ESearch가 PMID를 반환하지 않으면 빈 결과 컨테이너를 반환한다(비차단). 선택
    ``api_key``는 파라미터로만 전달되며 로그/오류에는 노출하지 않는다.
    """
    es_params = {"db": "pubmed", "term": query, "retmax": top_k, "retmode": "json"}
    if key:
        es_params["api_key"] = key
    es = await _get_json(client, _PUBMED_ESEARCH_URL, params=es_params)

    idlist: list[str] = []
    result = es.get("esearchresult") if isinstance(es, dict) else None
    if isinstance(result, dict) and isinstance(result.get("idlist"), list):
        idlist = [str(i).strip() for i in result["idlist"] if str(i).strip()]
    if not idlist:
        # 어댑터가 기대하는 빈 컨테이너(항목 0건) — 예외 없이 정상 폴백.
        return {"PubmedArticleSet": {"PubmedArticle": []}}

    ef_params = {"db": "pubmed", "id": ",".join(idlist), "retmode": "xml"}
    if key:
        ef_params["api_key"] = key
    return await _get_xml(client, _PUBMED_EFETCH_URL, params=ef_params)


# --------------------------------------------------------------------------- #
# 공개 egress 함수 (옵트인 게이트 → 검증 → HTTPX 호출 → 비차단 폴백)
# --------------------------------------------------------------------------- #
def _prepare(providers, provider, query):
    """egress 공통 전처리: 게이트/제공자/질의 검증. (정규화된 name, 정제 질의) 또는 오류 dict.

    반환이 dict면 즉시 그 구조화 오류를 돌려줘야 한다(네트워크 미호출). 반환이
    튜플이면 ``(name, query)``로 진행한다.
    """
    if not web_research_enabled():
        # 옵트인/동의 off → 네트워크 미호출·즉시 폴백(무회귀, P15 / 요구사항 10.3·14.2).
        return _error("web_research_disabled", "external research is opt-in/consent gated")
    name = provider.strip().lower() if isinstance(provider, str) else ""
    if name not in providers:
        return _error("unknown_provider", "unsupported provider", provider=name)
    # 질의 검증(빈/공백/과길이)은 단일 헬퍼로 통합한다(요구사항 1.8, P8 — task 9.5).
    q, err = _validate_query(query, provider=name)
    if err is not None:
        return err
    return name, q


async def web_search_raw(
    provider: str,
    query: str,
    *,
    top_k: int,
    timeout: float,
    recency: Optional[str] = None,
) -> dict:
    """웹 제공자 1곳(Tavily/Exa/Brave)을 HTTPX로 원시 검색한다(단일 egress 지점).

    옵트인 게이트를 먼저 확인하고(off면 네트워크 미호출·즉시 오류 폴백, P15),
    ``security.load_credential``로 키를 주입한 뒤 제공자 엔드포인트를 호출한다.
    반환 dict는 ``providers.get_web_adapter(provider).to_search_results``가 그대로
    파싱할 수 있는 원시 형태다.

    Args:
        provider: 제공자 name(``"tavily"`` | ``"exa"`` | ``"brave"``, 대소문자 무시).
        query: 검색 질의(앞뒤 공백은 제거되며, 비면 오류 폴백).
        top_k: 요청 결과 상한(양의 정수로 정규화).
        timeout: 이 호출에 적용할 개별 타임아웃(초).
        recency: 최신성 힌트(선택). best-effort로 제공자 파라미터에 반영하며,
            결정적 최신성 필터는 ``rank.apply_recency``가 담당한다.

    Returns:
        제공자 원시 응답 dict, 또는 ``{"error": code, "detail": ...}`` 구조화 오류
        dict(옵트인 off/미지원 제공자/빈 질의/자격증명 없음/타임아웃/HTTP 오류/
        파싱 실패). 어느 경우에도 예외를 전파하지 않는다(P8 / 요구사항 13).
    """
    prepared = _prepare(_WEB_PROVIDERS, provider, query)
    if isinstance(prepared, dict):
        return prepared
    name, q = prepared

    key = load_credential(name)
    if name in _REQUIRES_KEY and not key:
        return _error("missing_credential", "no API credential configured", provider=name)

    k = _safe_top_k(top_k)
    logger.debug(
        "web_search_raw provider=%s key=%s top_k=%s timeout=%ss",
        name, mask_secret(key), k, timeout,
    )
    try:
        async with httpx.AsyncClient(
            timeout=timeout, headers={"User-Agent": _UA}, follow_redirects=True
        ) as client:
            if name == "tavily":
                return await _search_tavily(client, q, k, recency, key)
            if name == "exa":
                return await _search_exa(client, q, k, recency, key)
            return await _search_brave(client, q, k, recency, key)
    except _EgressError as exc:
        logger.warning("web_search_raw provider=%s egress_error=%s", name, exc.code)
        return _error(exc.code, exc.detail, provider=name)
    except httpx.TimeoutException:
        logger.warning("web_search_raw provider=%s timeout after %ss", name, timeout)
        return _error("timeout", f"request exceeded {timeout}s", provider=name)
    except httpx.HTTPStatusError as exc:
        status = exc.response.status_code if exc.response is not None else 0
        logger.warning("web_search_raw provider=%s http_error status=%s", name, status)
        return _error(
            "http_error", "provider returned an error status",
            provider=name, status_code=status,
        )
    except httpx.HTTPError as exc:
        logger.warning("web_search_raw provider=%s request_error=%s", name, type(exc).__name__)
        return _error("request_error", type(exc).__name__, provider=name)
    except Exception as exc:  # noqa: BLE001 — 최후 방어, 예외 전파 금지(P8)
        logger.warning("web_search_raw provider=%s unexpected=%s", name, type(exc).__name__)
        return _error("request_error", type(exc).__name__, provider=name)


async def academic_search_raw(
    provider: str,
    query: str,
    *,
    top_k: int,
    timeout: float,
) -> dict:
    """논문 제공자 1곳을 HTTPX로 원시 검색한다(단일 egress 지점).

    지원 제공자: ``semantic_scholar``(선택 키) / ``openalex``(키리스) /
    ``arxiv``(키리스, XML) / ``pubmed``(선택 키, ESearch+EFetch, XML). 옵트인
    게이트를 먼저 확인하고(off면 네트워크 미호출·즉시 오류 폴백, P15), 반환 dict는
    ``providers.get_academic_adapter(provider).to_paper_results``가 그대로 파싱할 수
    있는 원시 형태다(arXiv/PubMed는 xmltodict 스타일 dict로 환원).

    Args:
        provider: 제공자 name(대소문자 무시).
        query: 검색 질의(앞뒤 공백 제거, 비면 오류 폴백).
        top_k: 요청 결과 상한(양의 정수로 정규화).
        timeout: 이 호출에 적용할 개별 타임아웃(초).

    Returns:
        제공자 원시 응답 dict, 또는 ``{"error": code, "detail": ...}`` 구조화 오류
        dict. 예외는 전파하지 않는다(P8 / 요구사항 13).
    """
    prepared = _prepare(_ACADEMIC_PROVIDERS, provider, query)
    if isinstance(prepared, dict):
        return prepared
    name, q = prepared

    # 학술 제공자는 키리스 또는 선택 키다(미설정이어도 호출). 있으면 주입·마스킹.
    key = load_credential(name)
    k = _safe_top_k(top_k)
    # 느린 공개 API(arXiv/PubMed)는 제공자별 하한을 적용한다 — 전역 12s 로는 항상 TIMEOUT.
    timeout = _effective_timeout(name, timeout)
    logger.debug(
        "academic_search_raw provider=%s key=%s top_k=%s timeout=%ss",
        name, mask_secret(key), k, timeout,
    )
    try:
        async with httpx.AsyncClient(
            timeout=timeout, headers={"User-Agent": _UA}, follow_redirects=True
        ) as client:
            if name == "semantic_scholar":
                return await _search_semantic_scholar(client, q, k, key)
            if name == "openalex":
                return await _search_openalex(client, q, k)
            if name == "arxiv":
                return await _search_arxiv(client, q, k)
            if name == "europepmc":
                return await _search_europepmc(client, q, k)
            return await _search_pubmed(client, q, k, key)
    except _EgressError as exc:
        logger.warning("academic_search_raw provider=%s egress_error=%s", name, exc.code)
        return _error(exc.code, exc.detail, provider=name)
    except httpx.TimeoutException:
        logger.warning("academic_search_raw provider=%s timeout after %ss", name, timeout)
        return _error("timeout", f"request exceeded {timeout}s", provider=name)
    except httpx.HTTPStatusError as exc:
        status = exc.response.status_code if exc.response is not None else 0
        logger.warning("academic_search_raw provider=%s http_error status=%s", name, status)
        return _error(
            "http_error", "provider returned an error status",
            provider=name, status_code=status,
        )
    except httpx.HTTPError as exc:
        logger.warning(
            "academic_search_raw provider=%s request_error=%s", name, type(exc).__name__
        )
        return _error("request_error", type(exc).__name__, provider=name)
    except Exception as exc:  # noqa: BLE001 — 최후 방어, 예외 전파 금지(P8)
        logger.warning("academic_search_raw provider=%s unexpected=%s", name, type(exc).__name__)
        return _error("request_error", type(exc).__name__, provider=name)


# =========================================================================== #
# 다중 제공자 폴백 체인 (task 9.5) — 1차→보조→폴백 순차 시도, 첫 성공 반환
# =========================================================================== #
# design.md "Error Handling > 폴백 체인"을 backend 계층에서 구현한 것이다. 설정된 제공자
# 순서(config.web_providers / academic_providers = 1차/보조/폴백)대로 web_search_raw /
# academic_search_raw 를 시도하고, 각 호출이 구조화 오류 dict를 내면 다음 제공자로
# 넘어간다(부분 실패 비차단 — 요구사항 13.2). 전 제공자가 실패하면 예외 없이 "외부 근거
# 미확보"(no_external_evidence) 구조화 신호를 반환한다(요구사항 13.3 / P8). 로컬 검색
# 폴백은 backend egress 범위 밖이므로, 상위 계층(도구/파이프라인)이 이 신호를 받아 로컬
# 결과 사용 또는 "외부 근거 미확보" 표기로 비차단 종료한다.
#
# 이 함수들은 정규화/융합(providers.py / normalize.py / rank.py)을 수행하지 않는다 —
# backend는 egress만 담당하고 config/security만 재사용한다(단일 egress 계약, 요구사항
# 10.4 / 15). 성공 시 반환하는 raw dict는 해당 제공자 어댑터가 그대로 파싱할 수 있는
# 원시 형태이며, 정규화/RRF 융합/재랭킹은 상위 rank 계층이 담당한다.


async def _search_with_fallback(
    kind: str,
    providers,
    query: str,
    *,
    top_k: Optional[int] = None,
    timeout: Optional[float] = None,
    recency: Optional[str] = None,
) -> dict:
    """웹/학술 공통 폴백 체인 구현. 첫 성공(비오류) 제공자의 원시 응답을 반환한다.

    진입 시 (1) 옵트인/동의 게이트, (2) 질의 입력 검증을 먼저 수행한다. 게이트가 off면
    어떤 제공자도 호출하지 않고 즉시 폴백하며(무회귀 P15 / 요구사항 10.3·14.2), 질의가
    유효하지 않으면(빈/공백/과길이) 제공자를 호출하지 않고 ``invalid_query``로
    폴백한다(요구사항 1.8 / P8). 통과하면 제공자 순서대로 egress를 시도해 첫 성공을
    반환하고, 전 제공자가 실패하면 ``no_external_evidence`` 구조화 신호를 반환한다
    (요구사항 13.3). 어느 경우에도 예외를 전파하지 않는다(P8).

    Args:
        kind: ``"web"`` 또는 ``"academic"``(호출할 egress 함수·기본 제공자 선택).
        providers: 시도할 제공자 순서. ``None``이면 config 기본 순서(1차/보조/폴백),
            단일 문자열이면 1개 제공자, 그 외에는 반복 가능한 이름 목록.
        query: 검색 질의(진입 시 검증).
        top_k: 결과 상한. ``None``이면 config ``top_k``.
        timeout: 제공자별 개별 타임아웃(초). ``None``이면 config ``search_timeout``.
        recency: 최신성 힌트(웹 전용, best-effort).

    Returns:
        성공: ``{"ok": True, "kind", "provider", "raw", "attempts": [...]}``.
        실패: ``{"ok": False, "error", "detail", "kind", "attempts": [...]}``
        (게이트 off → ``web_research_disabled``, 불량 질의 → ``invalid_query``,
        전 제공자 실패 → ``no_external_evidence``).
    """
    # 1) 옵트인/동의 게이트 우선 — off면 어떤 제공자도 호출하지 않는다(무회귀 P15).
    if not web_research_enabled():
        return {
            "ok": False,
            "error": "web_research_disabled",
            "detail": "external research is opt-in/consent gated",
            "kind": kind,
            "attempts": [],
        }

    # 2) 질의 입력 검증 — 위반 시 제공자 미호출·즉시 폴백(요구사항 1.8 / P8).
    q, err = _validate_query(query)
    if err is not None:
        return {"ok": False, "kind": kind, "attempts": [], **err}

    # 3) 제공자 순서·상한·타임아웃 결정(미지정은 config 기본으로 폴백).
    cfg = DeepResearchConfig.from_env()
    if providers is None:
        provider_list = list(cfg.web_providers if kind == "web" else cfg.academic_providers)
    elif isinstance(providers, str):
        provider_list = [providers]
    else:
        provider_list = [p for p in providers]
    k = _safe_top_k(top_k if top_k is not None else cfg.top_k)
    t = timeout if timeout is not None else cfg.search_timeout

    # 4) 제공자 순서대로 시도 — 첫 성공(비오류 dict)을 반환, 실패는 다음으로(13.2).
    attempts: list[dict] = []
    for name in provider_list:
        if kind == "web":
            raw = await web_search_raw(name, q, top_k=k, timeout=t, recency=recency)
        else:
            raw = await academic_search_raw(name, q, top_k=k, timeout=t)
        if isinstance(raw, dict) and "error" not in raw:
            attempts.append({"provider": name, "ok": True})
            return {
                "ok": True,
                "kind": kind,
                "provider": name,
                "raw": raw,
                "attempts": attempts,
            }
        code = raw.get("error", "unknown") if isinstance(raw, dict) else "unknown"
        attempts.append({"provider": name, "error": code})

    # 5) 전 제공자 실패 → "외부 근거 미확보" 비차단 신호(요구사항 13.3 / P8).
    logger.info(
        "%s fallback chain exhausted: no external evidence (attempts=%d)",
        kind, len(attempts),
    )
    return {
        "ok": False,
        "error": "no_external_evidence",
        "detail": "all providers failed or returned errors",
        "kind": kind,
        "attempts": attempts,
    }


async def search_web_with_fallback(
    query: str,
    providers=None,
    *,
    top_k: Optional[int] = None,
    timeout: Optional[float] = None,
    recency: Optional[str] = None,
) -> dict:
    """웹 검색 다중 제공자 폴백 체인(Tavily 1차 → Exa 보조 → Brave 폴백).

    ``providers``가 ``None``이면 ``AE_RESEARCH_WEB_PROVIDERS`` 순서(기본
    ``tavily,exa,brave``)로 시도한다. 첫 성공 제공자의 원시 응답을 ``raw``로 반환하며,
    전 제공자가 실패하면 ``no_external_evidence``로 비차단 폴백한다. 옵트인/동의 off이거나
    질의가 유효하지 않으면 어떤 제공자도 호출하지 않는다(무회귀 P15 / 요구사항 1.8).
    예외를 전파하지 않는다(P8).

    Args:
        query: 검색 질의.
        providers: 제공자 순서(미지정 시 config 기본).
        top_k: 결과 상한(미지정 시 config ``top_k``).
        timeout: 제공자별 타임아웃(미지정 시 config ``search_timeout``).
        recency: 최신성 힌트(best-effort).

    Returns:
        ``_search_with_fallback`` 계약의 구조화 결과 dict.
    """
    return await _search_with_fallback(
        "web", providers, query, top_k=top_k, timeout=timeout, recency=recency
    )


async def search_academic_with_fallback(
    query: str,
    providers=None,
    *,
    top_k: Optional[int] = None,
    timeout: Optional[float] = None,
) -> dict:
    """논문 검색 다중 제공자 폴백 체인(Semantic Scholar 1차 → OpenAlex 보조 → …).

    ``providers``가 ``None``이면 ``AE_RESEARCH_ACADEMIC_PROVIDERS`` 순서(기본
    ``semantic_scholar,openalex``)로 시도한다. 계약은 ``search_web_with_fallback``과
    동일하되 최신성 힌트가 없다.

    Args:
        query: 검색 질의.
        providers: 제공자 순서(미지정 시 config 기본).
        top_k: 결과 상한(미지정 시 config ``top_k``).
        timeout: 제공자별 타임아웃(미지정 시 config ``search_timeout``).

    Returns:
        ``_search_with_fallback`` 계약의 구조화 결과 dict.
    """
    return await _search_with_fallback(
        "academic", providers, query, top_k=top_k, timeout=timeout
    )


# =========================================================================== #
# 본문 수집 egress (task 9.4) — Content_Fetcher (단일 egress 지점, 요구사항 10.4)
# =========================================================================== #
# fetch_url_raw는 선택된 소스 URL 1건의 본문을 HTTPX로 조회해 텍스트로 환원한다.
# http/https 스킴만 허용하고(요구사항 3.1), HTML은 기존 의존성 lxml로 본문 텍스트를
# 추출하며(요구사항 3.2 — Python 백엔드 수행, CSP 변경 불필요 3.6), 개별 타임아웃
# (AE_FETCH_TIMEOUT)·크기 상한(AE_FETCH_MAX_CHARS)을 적용한다(3.3/3.4). 조회 실패·
# 타임아웃·4xx/5xx·파싱 실패는 예외를 전파하지 않고 ok=False로 폴백한다(3.5 / P8).

# 본문 조회를 허용하는 URL 스킴. 그 외(ftp/file/data/javascript/빈 값 등)는 네트워크를
# 호출하지 않고 즉시 ok=False로 폴백한다(요구사항 3.1).
_ALLOWED_FETCH_SCHEMES = frozenset({"http", "https"})

# ── SSRF 방어 ─────────────────────────────────────────────────────────────────
# fetch_url_raw 의 URL 은 검색 provider 응답과 LLM 도구 인자에서 온다. 예전에는 스킴만
# 검사하고 follow_redirects=True 였으므로 http://127.0.0.1:8765(이 앱의 사이드카)·
# http://169.254.169.254(클라우드 메타데이터)·사설망 주소로도 요청이 나갔다. 여기서는
# (1) 호스트가 공개 주소로만 해석될 때만 허용하고 (2) 리다이렉트를 수동으로 따라가며
# 매 hop 마다 다시 검사한다. DNS 조회 실패는 "알 수 없음"으로 두어 허용한다(그 경우
# httpx 도 같은 이유로 실패하므로 가용성만 잃고 우회는 열리지 않는다).
_FETCH_MAX_REDIRECTS = 5
_BLOCKED_HOST_SUFFIXES = (".localhost", ".local", ".internal")
# 테스트·오프라인 환경이 결정적 리졸버를 주입할 수 있게 모듈 전역으로 둔다.
_RESOLVER = socket.getaddrinfo


def _is_public_address(ip_str: str) -> bool:
    """공개 라우팅 가능한 유니캐스트 주소만 True(사설·루프백·링크로컬·멀티캐스트·예약·미지정 제외)."""
    try:
        ip = ipaddress.ip_address(ip_str)
    except ValueError:
        return False
    if ip.version == 6 and ip.ipv4_mapped is not None:
        return _is_public_address(str(ip.ipv4_mapped))
    return not (
        ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_multicast
        or ip.is_reserved or ip.is_unspecified
    )


def url_egress_allowed(url: str, *, resolver=None) -> tuple[bool, str]:
    """URL 이 외부 공개 호스트로만 향하는지 판정한다(순수·예외 없음).

    Returns:
        ``(allowed, reason)``. reason ∈ {"", "invalid_url", "unsupported_scheme",
        "blocked_host", "blocked_address"}. DNS 실패는 허용(위 설명 참조).
    """
    try:
        parsed = urlparse(url)
    except Exception:  # noqa: BLE001
        return False, "invalid_url"
    scheme = (parsed.scheme or "").lower()
    host = parsed.hostname
    if scheme not in _ALLOWED_FETCH_SCHEMES or not host:
        return False, "unsupported_scheme"
    host_l = host.lower().rstrip(".")
    if host_l == "localhost" or host_l.endswith(_BLOCKED_HOST_SUFFIXES):
        return False, "blocked_host"
    try:
        literal = ipaddress.ip_address(host_l)
    except ValueError:
        literal = None
    if literal is not None:
        return (True, "") if _is_public_address(str(literal)) else (False, "blocked_address")
    port = parsed.port or (443 if scheme == "https" else 80)
    resolve = resolver or _RESOLVER
    try:
        infos = resolve(host_l, port, proto=socket.IPPROTO_TCP)
    except Exception:  # noqa: BLE001 — DNS 실패는 "알 수 없음" → 허용(httpx 도 실패한다)
        return True, ""
    addrs = {info[4][0] for info in infos if info and len(info) >= 5 and info[4]}
    if addrs and not all(_is_public_address(a) for a in addrs):
        return False, "blocked_address"
    return True, ""

# 본문이 아닌 요소(스크립트/스타일/메타/비가시 영역) — 텍스트 추출에서 제거한다.
_HTML_NON_CONTENT_XPATH = (
    "//script | //style | //noscript | //template | //head | //svg | //iframe"
)

# 블록 레벨 요소 — 경계에 개행을 주입해 인접 블록 텍스트가 한 단어로 뭉치는 것을
# 방지한다(예: 제목과 문단). 인라인 요소(a/b/span 등)는 건드리지 않아 과분할을 피한다.
_HTML_BLOCK_TAGS = frozenset({
    "address", "article", "aside", "blockquote", "br", "dd", "div", "dl", "dt",
    "figcaption", "figure", "footer", "h1", "h2", "h3", "h4", "h5", "h6",
    "header", "hr", "li", "main", "nav", "ol", "p", "pre", "section", "table",
    "tbody", "td", "tfoot", "th", "thead", "tr", "ul",
})


@dataclass
class FetchResult:
    """단일 소스 본문 조회 결과(Content_Fetcher 반환 계약, 요구사항 3).

    모든 실패 경로(옵트인 off·비 http/https 스킴·타임아웃·HTTP 오류·파싱 실패)는
    예외 없이 ``ok=False``로 표현되므로, 딥리서치 파이프라인은 실패 소스를 결과에서
    제외하고 나머지 수집을 계속할 수 있다(P8). 자격증명 원문은 어떤 필드에도 담지
    않는다(P9) — ``url``은 조회 대상 콘텐츠 URL이며 제공자 API 키를 포함하지 않는다.

    Attributes:
        ok: 조회·추출 성공 여부. 실패 시 ``False``(예외 전파 없음).
        url: 조회를 시도한 원본 URL(trim 적용). 실패 시에도 진단을 위해 채운다.
        text: 추출·정규화된 본문 텍스트(상한 적용 후). 실패 시 ``""``.
        chars: ``text``의 문자 수(상한 적용 후 길이).
        truncated: 본문이 ``max_chars`` 상한으로 절단되었는지 여부.
        error: 실패 사유 코드(비민감, 예: ``timeout`` / ``http_error`` /
            ``unsupported_scheme`` / ``web_research_disabled``). 성공 시 ``""``.
    """

    ok: bool = False
    url: str = ""
    text: str = ""
    chars: int = 0
    truncated: bool = False
    error: str = ""


def _safe_max_chars(max_chars, default: int = 100000) -> int:
    """max_chars를 양의 정수로 정규화한다(형식 오류/0/음수 → 기본 상한)."""
    try:
        v = int(max_chars)
    except (TypeError, ValueError):
        return default
    return v if v > 0 else default


def _normalize_text(raw: str) -> str:
    """추출 텍스트의 공백을 정규화한다(줄별 축약·trim + 빈 줄 접기, 순수·결정적).

    같은 입력에는 항상 같은 출력을 내며(P8 정합), 문단 경계는 단일 빈 줄로 보존해
    원문 근거로서 가독성을 유지한다. 예외를 던지지 않는다.
    """
    if not raw:
        return ""
    lines: list[str] = []
    prev_blank = False
    for line in raw.splitlines():
        collapsed = re.sub(r"[ \t\u00a0\u200b\u3000]+", " ", line).strip()
        if collapsed:
            lines.append(collapsed)
            prev_blank = False
        elif not prev_blank:
            lines.append("")
            prev_blank = True
    return "\n".join(lines).strip()


def _html_to_text(content) -> str:
    """HTML(바이트/문자열)에서 본문 텍스트를 추출한다(script/style 제거 + 정규화).

    기존 의존성 ``lxml``로 파싱하며(신규 무거운 의존성 없음), 파싱 실패·``lxml``
    부재 등은 예외 없이 빈 문자열로 폴백한다(P8). 본문 조회·추출은 Python 백엔드
    에서 수행되므로 Electron 렌더러 CSP를 변경하지 않는다(요구사항 3.2 / 3.6).

    Args:
        content: HTTP 응답 본문(``resp.content`` 바이트 또는 문자열).

    Returns:
        script/style/head 등 비본문 요소를 제거하고 공백을 정규화한 텍스트.
        추출 불가 시 ``""``.
    """
    if not content:
        return ""
    try:
        from lxml import html as lxml_html  # 지연 import (기존 lxml 재사용 관례)
    except Exception:  # noqa: BLE001 — lxml 부재 등, 비차단 폴백
        return ""
    try:
        doc = lxml_html.fromstring(content)
    except Exception:  # noqa: BLE001 — 빈/불량 마크업 파싱 실패, 비차단 폴백
        return ""
    try:
        for node in doc.xpath(_HTML_NON_CONTENT_XPATH):
            parent = node.getparent()
            if parent is not None:
                parent.remove(node)
        # 블록 요소 시작/끝에 개행을 주입해 경계를 보존한다(인라인은 그대로 둔다).
        for el in doc.iter():
            tag = el.tag if isinstance(el.tag, str) else ""
            if tag in _HTML_BLOCK_TAGS:
                if el.text:
                    el.text = "\n" + el.text
                el.tail = "\n" + (el.tail or "")
        raw = doc.text_content()
    except Exception:  # noqa: BLE001 — 예기치 못한 트리 조작 오류, 비차단 폴백
        return ""
    return _normalize_text(raw)


async def fetch_url_raw(
    url: str,
    *,
    timeout: float,
    max_chars: int,
) -> FetchResult:
    """단일 소스 URL의 본문을 조회·추출한다(Content_Fetcher egress, 단일 egress 지점).

    옵트인/동의 게이트를 **가장 먼저** 확인하고(off면 네트워크 미호출·즉시
    ``ok=False``, 무회귀 P15 / 요구사항 10.3·14.2), ``http``/``https`` 스킴만
    허용한다(그 외 스킴·빈·비정상 URL은 네트워크 미호출·``ok=False`` — 요구사항 3.1).
    통과하면 개별 ``timeout``으로 HTTPX GET 후, HTML 응답은 ``lxml``로 본문 텍스트를
    추출(script/style 제거·공백 정규화)하고 그 외 텍스트 응답은 원문을 정규화한다
    (요구사항 3.2). 본문은 ``max_chars`` 이하로 절단하며(요구사항 3.3), 초과 시
    ``truncated=True``로 표시한다.

    조회 실패·타임아웃·HTTP 4xx/5xx·파싱 실패는 예외를 전파하지 않고 ``ok=False``인
    ``FetchResult``로 폴백해, 딥리서치 파이프라인이 실패 소스를 제외하고 나머지 수집을
    계속할 수 있게 한다(요구사항 3.5 / P8). 자격증명을 사용·기록하지 않는다(P9).

    Args:
        url: 조회할 소스 URL. ``http``/``https`` 스킴만 허용(대소문자 무시).
        timeout: 이 조회에 적용할 개별 타임아웃(초, ``AE_FETCH_TIMEOUT``).
        max_chars: 반환 본문의 문자 수 상한(``AE_FETCH_MAX_CHARS``, 양의 정수로 정규화).

    Returns:
        ``FetchResult``. 성공 시 ``ok=True``와 정규화·절단된 ``text``, 실패 시
        ``ok=False``와 비민감 ``error`` 코드. 어느 경우에도 예외를 전파하지 않는다.
    """
    url_str = url.strip() if isinstance(url, str) else ""

    # 1) 옵트인/동의 게이트 우선 — off면 네트워크 미호출·즉시 ok=False (무회귀 P15).
    if not web_research_enabled():
        return FetchResult(ok=False, url=url_str, error="web_research_disabled")

    # 2) 스킴 검증 — http/https만 허용(요구사항 3.1). 그 외/빈/비정상 → 네트워크 미호출.
    try:
        parsed = urlparse(url_str)
    except Exception:  # noqa: BLE001 — urlparse는 관대하지만 최후 방어(예외 금지)
        return FetchResult(ok=False, url=url_str, error="invalid_url")
    if parsed.scheme.lower() not in _ALLOWED_FETCH_SCHEMES or not parsed.netloc:
        return FetchResult(ok=False, url=url_str, error="unsupported_scheme")

    # 2b) SSRF 방어 — 사설·루프백·메타데이터 주소로는 네트워크 미호출(위 url_egress_allowed).
    allowed, why = url_egress_allowed(url_str)
    if not allowed:
        logger.warning("fetch_url_raw egress blocked reason=%s", why)
        return FetchResult(ok=False, url=url_str, error="egress_blocked")

    limit = _safe_max_chars(max_chars)
    logger.debug("fetch_url_raw url=%s timeout=%ss max_chars=%s", url_str, timeout, limit)

    try:
        async with httpx.AsyncClient(
            timeout=timeout, headers={"User-Agent": _UA}, follow_redirects=False
        ) as client:
            # 리다이렉트는 수동으로 따라가며 hop 마다 egress 검사(공개 URL → 내부 주소 우회 차단).
            current = url_str
            resp = None
            for _hop in range(_FETCH_MAX_REDIRECTS + 1):
                resp = await client.get(current)
                location = resp.headers.get("location", "") if resp.status_code in (301, 302, 303, 307, 308) else ""
                if not location:
                    break
                nxt = urljoin(current, location)
                ok_hop, why_hop = url_egress_allowed(nxt)
                if not ok_hop:
                    logger.warning("fetch_url_raw redirect blocked reason=%s", why_hop)
                    return FetchResult(ok=False, url=url_str, error="egress_blocked")
                current = nxt
            else:
                logger.warning("fetch_url_raw too many redirects url=%s", url_str)
                return FetchResult(ok=False, url=url_str, error="too_many_redirects")
            resp.raise_for_status()
            ctype = resp.headers.get("content-type", "").lower()
            if "html" in ctype:
                text = _html_to_text(resp.content)
            elif (not ctype) or ctype.startswith("text/") or "json" in ctype or "xml" in ctype:
                # HTML이 아닌 텍스트(plain/json/xml) 또는 미상 타입은 원문을 정규화.
                text = _normalize_text(resp.text)
            else:
                # 비텍스트(이미지/바이너리 등) 본문은 근거 텍스트가 없다 — 빈 텍스트로 성공.
                text = ""
            truncated = len(text) > limit
            if truncated:
                text = text[:limit]
            return FetchResult(
                ok=True,
                url=url_str,
                text=text,
                chars=len(text),
                truncated=truncated,
            )
    except httpx.TimeoutException:
        logger.warning("fetch_url_raw timeout after %ss url=%s", timeout, url_str)
        return FetchResult(ok=False, url=url_str, error="timeout")
    except httpx.HTTPStatusError as exc:
        status = exc.response.status_code if exc.response is not None else 0
        logger.warning("fetch_url_raw http_error status=%s url=%s", status, url_str)
        return FetchResult(ok=False, url=url_str, error="http_error")
    except httpx.HTTPError as exc:
        logger.warning("fetch_url_raw request_error=%s url=%s", type(exc).__name__, url_str)
        return FetchResult(ok=False, url=url_str, error="request_error")
    except Exception as exc:  # noqa: BLE001 — 최후 방어, 예외 전파 금지(P8)
        logger.warning("fetch_url_raw unexpected=%s url=%s", type(exc).__name__, url_str)
        return FetchResult(ok=False, url=url_str, error="request_error")
