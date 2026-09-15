#!/usr/bin/env python3
"""키 없이(가입 없이) 호출 가능한 외부 리서치 소스 실측 프로브.

웹 검색 제공자(Tavily/Exa/Brave)는 전부 가입·API 키가 필수다. 이 스크립트는 그
대안으로 **키리스로 실제 동작하는 소스**를 실측해 어떤 것을 붙일 가치가 있는지
판단할 근거를 만든다.

각 소스에 대해: HTTP 상태 / 응답 시간 / 결과 건수 / 첫 결과 제목을 출력한다.
자격증명을 쓰지 않으며, 전송하는 것은 일반 검색어뿐이다.

사용:
    ai_engine/.venv/bin/python scripts/probe_keyless_research_sources.py
"""
import asyncio
import time

import httpx

UA = "MogamWorks-DeepResearch/1.0 (Agentic-Editor; research evaluation)"
TIMEOUT = 20.0

# 이 앱의 실제 용도(전임상·생의학·규제)에 맞춘 질의.
QUERY = "GLP-1 receptor agonist hepatotoxicity"
DRUG = "semaglutide"


def _n(v):
    return len(v) if isinstance(v, list) else 0


async def probe(client, name, method, url, *, params=None, json_body=None, extract=None):
    """단일 소스 호출 후 (이름, 상태, 경과, 건수, 샘플) 출력."""
    t0 = time.time()
    try:
        if method == "GET":
            r = await client.get(url, params=params)
        else:
            r = await client.post(url, json=json_body)
        el = time.time() - t0
        if r.status_code != 200:
            print(f"  {name:22s} HTTP {r.status_code:<4} {el:5.1f}s  —")
            return False
        count, sample = 0, ""
        try:
            count, sample = extract(r) if extract else (0, "")
        except Exception as e:
            print(f"  {name:22s} HTTP 200  {el:5.1f}s  파싱 실패: {type(e).__name__}")
            return False
        print(f"  {name:22s} HTTP 200  {el:5.1f}s  결과 {count:<4} {sample[:52]}")
        return count > 0
    except httpx.TimeoutException:
        print(f"  {name:22s} TIMEOUT   {TIMEOUT:5.1f}s  —")
        return False
    except Exception as e:
        print(f"  {name:22s} ERROR     {'':5s}  {type(e).__name__}")
        return False


async def main():
    ok = {}
    async with httpx.AsyncClient(
        timeout=TIMEOUT, headers={"User-Agent": UA}, follow_redirects=True
    ) as c:
        print("=" * 78)
        print("A) 현재 코드가 이미 쓰는 키리스 학술 소스")
        print("=" * 78)
        ok["openalex"] = await probe(
            c, "OpenAlex", "GET", "https://api.openalex.org/works",
            params={"search": QUERY, "per-page": 3},
            extract=lambda r: (
                _n(r.json().get("results")),
                (r.json().get("results") or [{}])[0].get("title") or "",
            ),
        )
        ok["pubmed"] = await probe(
            c, "PubMed ESearch", "GET",
            "https://eutils.ncbi.nlm.nih.gov/entrez/eutils/esearch.fcgi",
            params={"db": "pubmed", "term": QUERY, "retmode": "json", "retmax": 3},
            extract=lambda r: (
                _n(r.json().get("esearchresult", {}).get("idlist")),
                ",".join(r.json().get("esearchresult", {}).get("idlist", [])),
            ),
        )
        ok["arxiv"] = await probe(
            c, "arXiv", "GET", "https://export.arxiv.org/api/query",
            params={"search_query": f"all:{QUERY}", "max_results": 3},
            extract=lambda r: (r.text.count("<entry>"), "atom feed"),
        )
        ok["s2_nokey"] = await probe(
            c, "SemanticScholar(키X)", "GET",
            "https://api.semanticscholar.org/graph/v1/paper/search",
            params={"query": QUERY, "limit": 3, "fields": "title"},
            extract=lambda r: (
                _n(r.json().get("data")),
                (r.json().get("data") or [{}])[0].get("title") or "",
            ),
        )

        print()
        print("=" * 78)
        print("B) 미사용 키리스 소스 — 이 앱 용도(전임상·규제)에 직결")
        print("=" * 78)
        ok["europepmc"] = await probe(
            c, "Europe PMC", "GET",
            "https://www.ebi.ac.uk/europepmc/webservices/rest/search",
            params={"query": QUERY, "format": "json", "pageSize": 3},
            extract=lambda r: (
                _n(r.json().get("resultList", {}).get("result")),
                (r.json().get("resultList", {}).get("result") or [{}])[0].get("title") or "",
            ),
        )
        ok["crossref"] = await probe(
            c, "Crossref", "GET", "https://api.crossref.org/works",
            params={"query": QUERY, "rows": 3},
            extract=lambda r: (
                _n(r.json().get("message", {}).get("items")),
                ((r.json().get("message", {}).get("items") or [{}])[0].get("title") or [""])[0],
            ),
        )
        ok["openfda_label"] = await probe(
            c, "openFDA 라벨", "GET",
            "https://api.fda.gov/drug/label.json",
            params={"search": f'openfda.generic_name:"{DRUG}"', "limit": 3},
            extract=lambda r: (
                _n(r.json().get("results")),
                str((r.json().get("results") or [{}])[0].get("openfda", {}).get("brand_name", "")),
            ),
        )
        ok["openfda_ae"] = await probe(
            c, "openFDA 이상반응", "GET",
            "https://api.fda.gov/drug/event.json",
            params={"search": f'patient.drug.medicinalproduct:"{DRUG}"', "limit": 3},
            extract=lambda r: (_n(r.json().get("results")), "FAERS 리포트"),
        )
        ok["clinicaltrials"] = await probe(
            c, "ClinicalTrials v2", "GET",
            "https://clinicaltrials.gov/api/v2/studies",
            params={"query.term": DRUG, "pageSize": 3},
            extract=lambda r: (
                _n(r.json().get("studies")),
                (r.json().get("studies") or [{}])[0]
                .get("protocolSection", {}).get("identificationModule", {})
                .get("briefTitle", "") or "",
            ),
        )

        print()
        print("=" * 78)
        print("C) 키리스 '일반 웹 검색' 후보 — 대체 가능성 확인")
        print("=" * 78)
        ok["ddg_ia"] = await probe(
            c, "DuckDuckGo IA", "GET", "https://api.duckduckgo.com/",
            params={"q": QUERY, "format": "json", "no_html": 1},
            extract=lambda r: (
                _n(r.json().get("RelatedTopics")),
                (r.json().get("AbstractText") or "")[:50] or "(초록 없음)",
            ),
        )
        ok["wikipedia"] = await probe(
            c, "Wikipedia 검색", "GET", "https://en.wikipedia.org/w/api.php",
            params={"action": "query", "list": "search", "srsearch": QUERY,
                    "format": "json", "srlimit": 3},
            extract=lambda r: (
                _n(r.json().get("query", {}).get("search")),
                (r.json().get("query", {}).get("search") or [{}])[0].get("title") or "",
            ),
        )

    print()
    print("=" * 78)
    print("판정")
    print("=" * 78)
    good = [k for k, v in ok.items() if v]
    bad = [k for k, v in ok.items() if not v]
    print(f"  키 없이 결과 반환: {', '.join(good) if good else '없음'}")
    print(f"  실패/빈 결과     : {', '.join(bad) if bad else '없음'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
