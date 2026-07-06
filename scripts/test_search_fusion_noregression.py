"""검색 융합 무회귀 (Property 6) — 기본 weighted 동작 불변 + rrf opt-in 정상.

실행: ./ai_engine/.venv/bin/python -m pytest scripts/test_search_fusion_noregression.py -p no:cacheprovider -q
"""
import os
from ai_engine.rag.indexer import ProjectIndexer
from ai_engine.rag.hybrid_search import HybridSearcher


def _searcher():
    idx = ProjectIndexer()
    idx.index_project(os.path.join("ai_engine", "rag"))
    s = HybridSearcher(alpha=0.6)
    s.index(idx.chunks)
    return s


def test_default_is_weighted_and_stable():
    """fusion 미지정 = weighted. 동일 쿼리 2회 → 동일 결과(결정론)."""
    s = _searcher()
    q = "reciprocal rank fusion 순위 융합"
    a = s.search(q, top_k=3, use_mmr=False, score_threshold=0.0)
    b = s.search(q, top_k=3, use_mmr=False, score_threshold=0.0, fusion="weighted")
    # 기본값 == 명시적 weighted (무회귀)
    assert [c.file_path for c, _ in a] == [c.file_path for c, _ in b]
    # 결정론
    a2 = s.search(q, top_k=3, use_mmr=False, score_threshold=0.0)
    assert [c.file_path for c, _ in a] == [c.file_path for c, _ in a2]


def test_rrf_optin_runs_and_returns_valid():
    """fusion=rrf opt-in — 예외 없이 유효 청크 반환(BM25 단독이면 순위 보존)."""
    s = _searcher()
    q = "충실도 점수 파싱 faithfulness"
    res = s.search(q, top_k=3, use_mmr=False, score_threshold=0.0, fusion="rrf")
    assert isinstance(res, list)
    for c, score in res:
        assert 0.0 <= score <= 1.0  # RRF 정규화 [0,1]
        assert c.file_path  # 유효 청크


def test_rrf_single_ranker_preserves_bm25_order():
    """벡터 미사용(BM25 단독)이면 RRF는 BM25 순위를 그대로 보존."""
    s = _searcher()
    q = "인용 범위 검증 citation"
    weighted = s.search(q, top_k=5, use_mmr=False, score_threshold=0.0, fusion="weighted")
    rrf = s.search(q, top_k=5, use_mmr=False, score_threshold=0.0, fusion="rrf")
    # BM25 단독이므로 두 방식의 순위(파일 순서)가 동일해야 함
    assert [c.file_path for c, _ in weighted] == [c.file_path for c, _ in rrf]
