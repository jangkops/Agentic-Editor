"""검색 근거 파이프라인 테스트 — 구성/폴백/sync어댑터/trace (Req 5.2, 6.2, 10.1).

실행: ./ai_engine/.venv/bin/python -m pytest scripts/test_retrieval_pipeline_pbt.py -p no:cacheprovider -q
"""
import os
import asyncio
from ai_engine.rag.indexer import ProjectIndexer
from ai_engine.rag.hybrid_search import HybridSearcher
from ai_engine.rag.retrieval_pipeline import (
    RetrievalConfig, retrieve_evidence, retrieve_evidence_sync,
)


def _searcher():
    idx = ProjectIndexer()
    idx.index_project(os.path.join("ai_engine", "rag"))
    s = HybridSearcher(alpha=0.6)
    s.index(idx.chunks)
    return s


def test_basic_retrieve_no_gateway():
    s = _searcher()
    cfg = RetrievalConfig(top_k=5)
    bundle = asyncio.run(retrieve_evidence("reciprocal rank fusion", s, gw=None, config=cfg))
    assert len(bundle.chunks) <= 5
    assert bundle.trace is not None
    assert len(bundle.context_ids) == len(bundle.chunks)
    assert "retrieve" in bundle.trace.latency_ms


def test_sync_adapter_outside_loop():
    s = _searcher()
    bundle = retrieve_evidence_sync("citation verify", s, config=RetrievalConfig(top_k=3))
    assert len(bundle.chunks) <= 3


def test_sync_adapter_inside_running_loop():
    """실행 중 이벤트 루프 안에서도 asyncio.run 충돌 없이 동작(별도 스레드)."""
    s = _searcher()

    async def _inside():
        # 러닝 루프 내부에서 sync 어댑터 호출
        return retrieve_evidence_sync("faithfulness score", s, config=RetrievalConfig(top_k=3))

    bundle = asyncio.run(_inside())
    assert len(bundle.chunks) <= 3


def test_rerank_fallback_on_gateway_error():
    """rerank on + 게이트웨이 오류 → 원 순위 유지(비차단)."""
    s = _searcher()

    class _Boom:
        async def converse(self, **kwargs):
            raise RuntimeError("down")

    cfg = RetrievalConfig(top_k=5, use_rerank=True)
    b_plain = asyncio.run(retrieve_evidence("rrf fusion", s, gw=None, config=RetrievalConfig(top_k=5)))
    b_rerank = asyncio.run(retrieve_evidence("rrf fusion", s, gw=_Boom(), config=cfg))
    # 폴백이므로 상위 결과가 동일해야 함(예외로 죽지 않음)
    assert [c.file_path for c, _ in b_rerank.chunks] == [c.file_path for c, _ in b_plain.chunks]


def test_rerank_success_reorders():
    s = _searcher()

    class _GW:
        async def converse(self, **kwargs):
            # 역순으로 재정렬 지시
            return {"output": {"message": {"content": [{"text": "[4,3,2,1,0]"}]}}}

    cfg = RetrievalConfig(top_k=5, candidate_k=5, use_rerank=True)
    b = asyncio.run(retrieve_evidence("hybrid search", s, gw=_GW(), config=cfg))
    assert len(b.chunks) <= 5
    assert b.trace.reranked  # rerank 단계 기록됨


def test_config_from_env():
    cfg = RetrievalConfig.from_env({"AE_RERANK": "1", "AE_QUERY_EXPAND": "1",
                                    "AE_FUSION": "rrf", "AE_TOP_K": "6"})
    assert cfg.use_rerank and cfg.use_query_expand
    assert cfg.fusion == "rrf" and cfg.top_k == 6


def test_file_filter_applied_single_query():
    """file_filter가 단일 쿼리 경로에서 검색 결과를 제한한다."""
    s = _searcher()
    # embedder.py 파일만 허용
    cfg = RetrievalConfig(top_k=8, file_filter=lambda p: p.endswith("embedder.py"))
    bundle = asyncio.run(retrieve_evidence("embedding provider", s, gw=None, config=cfg))
    assert bundle.chunks, "필터 통과 결과가 있어야 함"
    assert all(c.file_path.endswith("embedder.py") for c, _ in bundle.chunks)


def test_file_filter_applied_multi_query_expand():
    """확장(다중 쿼리) 경로에서도 file_filter가 적용된다."""
    s = _searcher()

    class _ExpandGW:
        async def converse(self, **kwargs):
            # 확장 쿼리 2개 반환
            return {"output": {"message": {"content": [
                {"text": "embedding vector\nsemantic search"}]}}}

    cfg = RetrievalConfig(top_k=8, use_query_expand=True,
                          file_filter=lambda p: p.endswith("hybrid_search.py"))
    bundle = asyncio.run(retrieve_evidence("검색", s, gw=_ExpandGW(), config=cfg))
    # 확장이 동작하지 않아도(should_expand False 등) 필터는 유지되어야 함
    assert all(c.file_path.endswith("hybrid_search.py") for c, _ in bundle.chunks)


def test_file_filter_none_is_noop():
    """file_filter=None이면 기존 동작과 동일(무회귀)."""
    s = _searcher()
    a = asyncio.run(retrieve_evidence("rrf", s, gw=None, config=RetrievalConfig(top_k=5)))
    b = asyncio.run(retrieve_evidence("rrf", s, gw=None,
                                      config=RetrievalConfig(top_k=5, file_filter=None)))
    assert [c.file_path for c, _ in a.chunks] == [c.file_path for c, _ in b.chunks]
