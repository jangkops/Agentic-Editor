# Feature: deep-research-engine
"""캐시 히트 단위 테스트 (Task 8.3) — `ai_engine/research/cache.py`.

`write_cache` / `read_cache` / `cache_key`의 userData 캐시 동작을 검증한다. 요구사항
4.6의 핵심("TTL 이내 동일 정규화 질의 재요청 시 제공자 재호출 0 — 캐시 반환")을
단위 수준에서 확인한다. `cache.py`는 순수 파일 캐시라 제공자를 직접 호출하지 않으므로,
"재호출 0"은 **캐시 히트 시 `read_cache`가 결과를 반환해 호출자가 제공자를 다시 부를
필요가 없음**으로 검증한다(제공자 호출 카운터 시뮬레이션 포함).

검증 축:

  (A) write→read 라운드트립 — 저장한 `SearchResult`/`PaperResult`(및 혼합 목록)가
      `serialize_result`/`deserialize_result`를 거쳐 dataclass 동등으로 복원됨(P1 정합).
  (B) 정규화 동치 히트 — 대소문자·공백만 다른 질의는 `normalize_query`로 같은 키가 되어
      캐시 히트(요구사항 4.6/4.7). 제공자 집합이 다르면 키가 달라 미스.
  (C) TTL 만료 미스 — `now`를 주입해 `(now - cached_at) > ttl`이면 미스, 경계(==ttl)는 히트.
  (D) 비차단 미스 — 파일 부재/손상 엔트리(비-JSON, 비-dict JSON)는 예외 없이 `None`(P8).

임시 `AE_GENERATED_ROOT`(tmp dir)를 `env`로 주입해 hermetic하게 실행한다(실제 userData
미오염). pytest의 tmp fixture 대신 `tempfile`을 직접 써서 단발 실행(`__main__`)과 pytest
수집을 모두 지원한다.

Stack: Python 3.11+ 표준 라이브러리 + pytest 수집. 단발 실행(워치 모드 금지):
    python -m pytest scripts/test_research_cache_hit.py -q
    python scripts/test_research_cache_hit.py

_Requirements: 4.6_
"""
from __future__ import annotations

import json
import os
import sys
import tempfile
from contextlib import contextmanager
from pathlib import Path

# 스크립트를 직접 실행할 때도 ai_engine 패키지를 import 할 수 있게 repo 루트를 경로에 추가.
_REPO_ROOT = Path(__file__).resolve().parent.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from ai_engine.research.cache import (  # noqa: E402
    _safe_cache_path,
    cache_key,
    cache_root,
    read_cache,
    write_cache,
)
from ai_engine.research.models import PaperResult, SearchResult  # noqa: E402


# --------------------------------------------------------------------------- #
# 헬퍼: 임시 AE_GENERATED_ROOT(tmp dir) 주입 + 대표 정규 결과 샘플
# --------------------------------------------------------------------------- #
@contextmanager
def _tmp_env():
    """임시 `AE_GENERATED_ROOT`(tmp dir)를 주입한 env 매핑을 제공한다(hermetic).

    캐시 루트는 이 tmp dir 하위 `research_cache/`가 되며, 컨텍스트 종료 시 정리된다.
    다른 env 키(예: `AE_RESEARCH_CACHE_TTL`)는 부재이므로 cache.py가 기본값으로 폴백한다.
    """
    with tempfile.TemporaryDirectory() as d:
        yield {"AE_GENERATED_ROOT": d}


def _web_results() -> list[SearchResult]:
    """대표 웹 결과 목록(누락 필드/기본값 혼재)."""
    return [
        SearchResult(
            title="Attention Is All You Need",
            url="https://arxiv.org/abs/1706.03762",
            snippet="The dominant sequence transduction models...",
            published_date="2017-06-12",
            source_domain="arxiv.org",
            relevance_score=0.98,
            provider="tavily",
            source_id="web:https://arxiv.org/abs/1706.03762",
        ),
        SearchResult(
            title="BERT",
            url="https://example.com/bert",
            snippet="",                 # 발췌문 결측 → 빈 값 보존
            published_date="",          # 발행일 미상 → 빈 값 보존
            source_domain="example.com",
            relevance_score=0.5,
            provider="exa",
            source_id="web:https://example.com/bert",
        ),
    ]


def _paper_results() -> list[PaperResult]:
    """대표 논문 결과 목록(저자 리스트/수치 필드 포함)."""
    return [
        PaperResult(
            title="Deep Learning",
            authors=["Yann LeCun", "Yoshua Bengio", "Geoffrey Hinton"],
            year=2015,
            venue="Nature",
            abstract="Deep learning allows computational models...",
            doi_or_url="10.1038/nature14539",
            citation_count=50000,
            relevance_score=0.87,
            provider="semantic_scholar",
            source_id="doi:10.1038/nature14539",
        ),
        PaperResult(
            title="No DOI paper",
            authors=[],                 # 저자 결측 → []
            year=0,                     # 연도 미상 → 0(정렬 가능한 기본값)
            venue="",
            abstract="",
            doi_or_url="https://s2.org/p/abc",
            citation_count=0,
            relevance_score=0.0,
            provider="openalex",
            source_id="web:https://s2.org/p/abc",
        ),
    ]


# =========================================================================== #
# (A) write → read 라운드트립
# =========================================================================== #
def test_write_read_roundtrip_search_results() -> None:
    """웹 결과를 저장→조회하면 dataclass 동등으로 복원된다(캐시 히트)."""
    with _tmp_env() as env:
        results = _web_results()
        assert write_cache("Transformer models", ["tavily", "exa"], "web", results, env=env) is True

        got = read_cache("Transformer models", ["tavily", "exa"], env=env)
        assert got is not None                       # 히트
        assert got == results                        # 모든 정규 필드 동등 복원
        assert all(isinstance(r, SearchResult) for r in got)


def test_write_read_roundtrip_paper_results() -> None:
    """논문 결과를 저장→조회하면 저자 리스트·수치 필드까지 동등 복원된다."""
    with _tmp_env() as env:
        results = _paper_results()
        assert write_cache("deep learning survey", ["semantic_scholar"], "academic", results, env=env) is True

        got = read_cache("deep learning survey", ["semantic_scholar"], env=env)
        assert got is not None
        assert got == results
        assert all(isinstance(r, PaperResult) for r in got)


def test_write_read_roundtrip_mixed_and_empty() -> None:
    """혼합(웹+논문) 목록과 빈 목록도 타입 태그로 정확히 복원된다."""
    with _tmp_env() as env:
        mixed = [*_web_results(), *_paper_results()]
        assert write_cache("mixed query", ["tavily"], "web", mixed, env=env) is True
        got = read_cache("mixed query", ["tavily"], env=env)
        assert got == mixed
        assert isinstance(got[0], SearchResult) and isinstance(got[-1], PaperResult)

        # 빈 결과 목록도 히트로 저장/복원(제공자가 0건을 반환한 경우와 정합).
        assert write_cache("empty query", ["exa"], "web", [], env=env) is True
        assert read_cache("empty query", ["exa"], env=env) == []


# =========================================================================== #
# (B) 정규화 동치 히트 / provider_set 상이 미스
# =========================================================================== #
def test_cache_key_equal_for_normalized_equivalent_queries() -> None:
    """대소문자·공백만 다른 질의는 동일 캐시 키를 산출한다(normalize_query 기반)."""
    ps = ["tavily", "exa"]
    k1 = cache_key("Deep  Learning", ps)
    k2 = cache_key("  deep   learning ", ps)
    k3 = cache_key("DEEP LEARNING", ps)
    assert k1 == k2 == k3                            # 정규화 동치 → 같은 키


def test_hit_on_normalized_equivalent_query() -> None:
    """저장 질의와 대소문자·공백만 다른 재요청은 캐시 히트한다(요구사항 4.6/4.7)."""
    with _tmp_env() as env:
        results = _web_results()
        write_cache("Machine Learning", ["tavily", "exa"], "web", results, env=env)

        # 대소문자·공백이 다른 정규화 동치 질의로 재요청 → 히트.
        got = read_cache("   machine   LEARNING  ", ["tavily", "exa"], env=env)
        assert got is not None
        assert got == results


def test_miss_on_different_provider_set() -> None:
    """제공자 집합이 다르면 키가 달라 캐시 미스가 된다."""
    with _tmp_env() as env:
        write_cache("neural nets", ["tavily", "exa"], "web", _web_results(), env=env)

        # 제공자 집합이 다름 → 키 상이 → 미스.
        assert cache_key("neural nets", ["tavily", "exa"]) != cache_key("neural nets", ["brave"])
        assert read_cache("neural nets", ["brave"], env=env) is None
        # 순서만 달라도(콤마 join 순서 보존) 다른 키 → 미스.
        assert read_cache("neural nets", ["exa", "tavily"], env=env) is None
        # 동일 집합이면 히트(대조군).
        assert read_cache("neural nets", ["tavily", "exa"], env=env) is not None


# =========================================================================== #
# (C) TTL 만료 미스 (now 주입)
# =========================================================================== #
def test_ttl_expiry_miss_with_injected_now() -> None:
    """`now` 주입으로 TTL 이내는 히트, 초과는 미스, 경계(==ttl)는 히트임을 검증한다."""
    with _tmp_env() as env:
        results = _web_results()
        # cached_at=1000, ttl=100 로 저장(now/ttl 주입).
        assert write_cache("ttl query", ["tavily"], "web", results, ttl=100, env=env, now=1000.0) is True

        # TTL 이내(경과 50s <= 100) → 히트.
        assert read_cache("ttl query", ["tavily"], env=env, now=1050.0) == results
        # 경계(경과 100s, 조건은 '> ttl') → 히트.
        assert read_cache("ttl query", ["tavily"], env=env, now=1100.0) == results
        # TTL 초과(경과 200s > 100) → 미스.
        assert read_cache("ttl query", ["tavily"], env=env, now=1200.0) is None


# =========================================================================== #
# (D) 비차단 미스 — 파일 부재 / 손상 엔트리
# =========================================================================== #
def test_missing_file_is_miss() -> None:
    """한 번도 저장하지 않은 질의는 파일 부재로 미스(None, 예외 없음)."""
    with _tmp_env() as env:
        assert read_cache("never-written query", ["tavily"], env=env) is None


def test_corrupt_entry_non_json_is_miss() -> None:
    """손상된(비-JSON) 캐시 파일은 예외 없이 미스로 폴백한다(비차단, P8)."""
    with _tmp_env() as env:
        query, ps = "corrupt query", ["tavily"]
        # read_cache가 조회할 정확한 경로에 손상 바이트를 기록.
        path = _safe_cache_path(cache_key(query, ps), env=env)
        os.makedirs(os.path.dirname(str(path)), exist_ok=True)
        with open(str(path), "w", encoding="utf-8") as f:
            f.write("{ this is not valid json ]]")   # JSON 파싱 실패 유발

        assert read_cache(query, ps, env=env) is None  # 예외 없이 미스


def test_corrupt_entry_non_dict_and_bad_results_is_miss() -> None:
    """유효 JSON이라도 비-dict거나 results가 리스트가 아니면 미스로 폴백한다."""
    with _tmp_env() as env:
        # 1) 최상위가 dict가 아닌 JSON(list) → 미스.
        q1, ps = "json-list", ["exa"]
        p1 = _safe_cache_path(cache_key(q1, ps), env=env)
        os.makedirs(os.path.dirname(str(p1)), exist_ok=True)
        with open(str(p1), "w", encoding="utf-8") as f:
            json.dump([1, 2, 3], f)
        assert read_cache(q1, ps, env=env) is None

        # 2) dict지만 results가 리스트가 아님 → 미스.
        q2 = "bad-results"
        p2 = _safe_cache_path(cache_key(q2, ps), env=env)
        with open(str(p2), "w", encoding="utf-8") as f:
            json.dump({"cached_at": 0.0, "ttl": 0, "results": "not-a-list"}, f)
        assert read_cache(q2, ps, env=env) is None


# =========================================================================== #
# 요구사항 4.6 핵심: 캐시 히트 시 제공자 재호출 0
# =========================================================================== #
def test_cache_hit_avoids_provider_recall() -> None:
    """TTL 이내 동일 정규화 질의 재요청 시 제공자 재호출이 0임을 검증한다(요구사항 4.6).

    `cache.py`는 제공자를 직접 부르지 않으므로, "캐시 우선(cache-first)" 호출자를
    시뮬레이션한다: 캐시 미스에서만 제공자를 호출(카운터 +1)하고 결과를 저장한다.
    두 번째 요청이 정규화 동치(대소문자·공백만 상이)면 히트해 카운터가 증가하지 않는다.
    """
    with _tmp_env() as env:
        provider_calls = {"count": 0}
        ps = ["tavily", "exa"]
        now = 1000.0  # 두 요청 모두 동일 시각 → TTL 이내 보장.

        def search_with_cache(query: str) -> list[SearchResult]:
            cached = read_cache(query, ps, env=env, now=now)
            if cached is not None:
                return cached                        # 히트 → 제공자 미호출
            provider_calls["count"] += 1             # 미스에서만 제공자 호출
            results = _web_results()
            write_cache(query, ps, "web", results, ttl=86400, env=env, now=now)
            return results

        first = search_with_cache("Deep Learning")   # 미스 → 제공자 1회 호출
        assert provider_calls["count"] == 1

        # 대소문자·공백만 다른 정규화 동치 질의 → 히트 → 재호출 없음(카운터 불변).
        second = search_with_cache("   deep   LEARNING ")
        assert provider_calls["count"] == 1          # 여전히 1 (제공자 재호출 0)
        assert second == first == _web_results()


if __name__ == "__main__":
    # 단발 실행 드라이버(워치 모드 금지). 모든 테스트 함수를 순차 호출한다.
    _tests = [v for k, v in sorted(globals().items()) if k.startswith("test_") and callable(v)]
    for fn in _tests:
        fn()
    print(f"PASSED: {len(_tests)} cache-hit unit tests (Task 8.3)")
