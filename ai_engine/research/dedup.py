"""deep-research-engine: 중복 제거(Deduplicator) — 순수 함수 (부작용 없음).

병합된 다중 제공자·다중 질의 검색 결과에서 **정규 URL 또는 DOI가 동일한**
소스를 제거한다. 각 정규 키에 대해 병합 목록에서 **최초로 등장한** 소스를
보존한다(first-wins). 네트워크·파일 I/O·전역 상태가 없는 순수 계층이며
Python 3.11 표준 라이브러리(``urllib.parse``)만 사용한다(신규 의존성 없음).

정규 키 스킴 (models.py의 ``source_id`` 규약과 정합):
    - DOI가 있으면  ``doi:<canonical_doi>``
    - 없으면        ``web:<canonical_url>``
이로써 URL과 DOI가 우연히 같은 문자열이어도 키가 교차 충돌하지 않는다.

입력 방어 (P8 정합): 모든 함수는 ``None``·빈 값·비정상 입력(잘못된 URL,
제어문자, dict/dataclass 혼재)에 대해 예외를 던지지 않고 결정적으로 동작한다.
소스는 ``SearchResult``/``PaperResult``/``EvidenceSource`` dataclass 또는 이에
상응하는 ``dict`` 모두를 수용한다(getattr/``.get`` 방어).

Correctness Properties:
    - P2 (불변식): ``len(dedup_sources(xs)) <= len(xs)``, 결과 ⊆ 입력(창작 없음),
      각 키는 결과에서 유일, 각 키의 최초 등장 소스 보존.
    - P3 (멱등성): ``dedup_sources(dedup_sources(xs)) == dedup_sources(xs)``.
    - P5 (준동형, 커버리지 단조): ``A``가 ``A + B``의 접두이므로 고유 키 집합이
      단조 증가하여 ``len(dedup_sources(A + B)) >= len(dedup_sources(A))``.

Requirements: 7.1, 7.2, 7.3
Design: "Components and Interfaces > 4) dedup.py"
"""

from urllib.parse import urlsplit, urlunsplit, parse_qsl, urlencode

__all__ = ["canonical_url", "canonical_doi", "source_key", "dedup_sources"]


# --- 추적 쿼리 파라미터 (dedup 시 제거) ---
_TRACKING_PREFIXES = ("utm_",)  # utm_source/medium/campaign/term/content/id ...
_TRACKING_EXACT = frozenset({
    "fbclid", "gclid", "gclsrc", "dclid", "wbraid", "gbraid", "msclkid",
    "yclid", "twclid", "igshid", "mc_eid", "mc_cid", "_ga", "_gl",
    "vero_id", "oly_enc_id", "oly_anon_id", "spm", "scm",
})

# 스킴별 기본 포트 (dedup 시 제거)
_DEFAULT_PORTS = {"http": 80, "https": 443, "ftp": 21, "ws": 80, "wss": 443}

# DOI URL 프리픽스 (canonical_doi 시 제거)
_DOI_URL_PREFIXES = (
    "https://doi.org/", "http://doi.org/",
    "https://dx.doi.org/", "http://dx.doi.org/",
)


def _is_tracking(key: str) -> bool:
    """쿼리 파라미터 key가 추적용인지 판정(대소문자 무시)."""
    k = key.lower()
    if k in _TRACKING_EXACT:
        return True
    return any(k.startswith(p) for p in _TRACKING_PREFIXES)


def canonical_url(url) -> str:
    """URL을 dedup용 정규 형태로 변환한다(순수·예외 없음).

    정규화: 스킴·호스트 소문자, 기본 포트(80/443 등) 제거, 추적 쿼리
    (``utm_*``/``fbclid``/``gclid`` 등) 제거, fragment 제거, 말미 슬래시 정리,
    userinfo(자격증명) 제거. 파싱 불가 입력은 소문자 strip 문자열로 폴백한다.

    Args:
        url: 원본 URL 문자열(그 외 타입/``None``은 ``""``).

    Returns:
        정규화된 URL 문자열. 멱등:
        ``canonical_url(canonical_url(u)) == canonical_url(u)``.
    """
    if not isinstance(url, str):
        return ""
    s = url.strip()
    if not s:
        return ""
    # 스킴 없는 bare 호스트(예: "Example.com/p")는 netloc 파싱을 위해 "//" 부여.
    if "://" not in s and not s.startswith("//") and not s.startswith("/"):
        head = s.split("/", 1)[0]
        if "." in head and ":" not in head:
            s = "//" + s
    try:
        parts = urlsplit(s)
    except ValueError:
        return s.lower()

    scheme = parts.scheme.lower()
    host = (parts.hostname or "").lower()
    try:
        port = parts.port
    except ValueError:
        port = None

    if host:
        host_disp = f"[{host}]" if ":" in host else host  # IPv6 브래킷 보존
        if port is not None and _DEFAULT_PORTS.get(scheme) != port:
            netloc = f"{host_disp}:{port}"
        else:
            netloc = host_disp
    else:
        netloc = parts.netloc.lower()

    # 추적 파라미터 제거(순서 보존) + 인코딩 정규화
    kept = [
        (k, v)
        for (k, v) in parse_qsl(parts.query, keep_blank_values=True)
        if not _is_tracking(k)
    ]
    query = urlencode(kept)

    # 말미 슬래시 정리("/foo/" → "/foo", "/" → "")
    path = parts.path
    if path.endswith("/") and path != "/":
        path = path.rstrip("/")
    if path == "/":
        path = ""

    return urlunsplit((scheme, netloc, path, query, ""))  # fragment 제거


def canonical_doi(doi) -> str:
    """DOI를 dedup용 정규 형태로 변환한다(순수·예외 없음).

    정규화: strip → 소문자 → ``https://doi.org/``·``doi:`` 등 프리픽스 제거 →
    말미 슬래시·공백 정리.

    Args:
        doi: 원본 DOI 문자열(그 외 타입/``None``은 ``""``).

    Returns:
        프리픽스가 벗겨진 정규 DOI(``10.xxxx/...`` 형태).
    """
    if not isinstance(doi, str):
        return ""
    s = doi.strip().lower()
    if not s:
        return ""
    for pref in _DOI_URL_PREFIXES:
        if s.startswith(pref):
            s = s[len(pref):]
            break
    if s.startswith("doi:"):
        s = s[4:]
    return s.strip().rstrip("/").strip()


def _get(s, name: str, default: str = ""):
    """dataclass(getattr) 또는 dict(.get) 양쪽에서 필드를 안전하게 읽는다."""
    if isinstance(s, dict):
        v = s.get(name, default)
    else:
        v = getattr(s, name, default)
    return v if v is not None else default


def _looks_like_doi(v: str) -> bool:
    """문자열이 DOI로 보이는지 판정(``10.`` 프리픽스 / ``doi:`` / ``doi.org/``)."""
    t = v.strip().lower()
    if not t:
        return False
    return t.startswith("doi:") or ("doi.org/" in t) or t.startswith("10.")


def _extract_doi(s) -> str:
    """소스에서 정규 DOI를 추출한다(없으면 "").

    우선순위: ``source_id``의 ``doi:`` 프리픽스 → 명시 ``doi`` 필드 →
    ``doi_or_url``/``url_or_doi`` 중 DOI로 보이는 값.
    """
    sid = _get(s, "source_id", "")
    if isinstance(sid, str) and sid.strip().lower().startswith("doi:"):
        d = canonical_doi(sid)
        if d:
            return d
    doi = _get(s, "doi", "")
    if isinstance(doi, str) and doi.strip():
        d = canonical_doi(doi)
        if d:
            return d
    for fname in ("doi_or_url", "url_or_doi"):
        v = _get(s, fname, "")
        if isinstance(v, str) and _looks_like_doi(v):
            d = canonical_doi(v)
            if d:
                return d
    return ""


def _extract_url(s) -> str:
    """소스에서 URL 후보를 추출한다(없으면 "").

    우선순위: ``source_id``의 ``web:`` 프리픽스 → ``url`` 필드 →
    ``url_or_doi``/``doi_or_url`` 값.
    """
    sid = _get(s, "source_id", "")
    if isinstance(sid, str) and sid.strip().lower().startswith("web:"):
        return sid.strip()[4:]
    url = _get(s, "url", "")
    if isinstance(url, str) and url.strip():
        return url
    for fname in ("url_or_doi", "doi_or_url"):
        v = _get(s, fname, "")
        if isinstance(v, str) and v.strip():
            return v
    return ""


def source_key(s) -> str:
    """소스의 dedup 키를 반환한다(순수·예외 없음).

    DOI가 있으면 ``doi:<canonical_doi>``, 없으면 ``web:<canonical_url>``.
    ``SearchResult``/``PaperResult``/``EvidenceSource`` dataclass, ``dict``,
    또는 URL/DOI 문자열을 수용한다.
    """
    if s is None:
        return "web:"
    if isinstance(s, str):
        if _looks_like_doi(s):
            d = canonical_doi(s)
            if d:
                return "doi:" + d
        return "web:" + canonical_url(s)
    doi = _extract_doi(s)
    if doi:
        return "doi:" + doi
    return "web:" + canonical_url(_extract_url(s))


def dedup_sources(sources) -> list:
    """정규 URL/DOI 키 기준으로 중복 소스를 제거한다(first-wins, 순수).

    각 ``source_key``에 대해 입력에서 **최초로 등장한** 소스만 보존하며, 결과
    순서는 입력의 등장 순서를 따른다. 결과는 입력의 부분집합이고(창작 없음),
    크기는 입력 이하이며(P2), 멱등이다(P3).

    Args:
        sources: 소스 리스트(``None``/비이터러블은 빈 리스트로 방어).

    Returns:
        중복이 제거된 새 리스트(원본 소스 객체를 그대로 참조).
    """
    if sources is None:
        return []
    try:
        items = list(sources)
    except TypeError:
        return []
    seen = set()
    out = []
    for s in items:
        k = source_key(s)
        if k in seen:
            continue
        seen.add(k)
        out.append(s)
    return out
