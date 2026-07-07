"""MMR relevance 결함 회귀 방지.

이전 버그: MMR의 relevance 항이 벡터 유사도만 사용해 BM25 신호를 무시 →
exact-keyword 질의에서 정답을 밀어내 recall/mrr이 급락했다. 수정 후 relevance는
하이브리드 최종 점수를 정규화해 쓴다. 이 테스트는 "MMR을 켜도 정확도가 크게
떨어지지 않는다"를 로컬 LSA 임베더(네트워크 불필요, 결정적)로 고정한다.
"""
import os
from ai_engine.rag.indexer import ProjectIndexer
from ai_engine.rag.hybrid_search import HybridSearcher
from ai_engine.rag.embedder import VectorStore, LsaEmbeddingProvider
from ai_engine.rag.eval_metrics import recall_at_k, mrr
from scripts.rag_benchmark import GOLDEN, _bn


def _build_searcher():
    idx = ProjectIndexer()
    idx.index_project(os.path.join("ai_engine", "rag"))
    chunks = idx.chunks
    emb = LsaEmbeddingProvider(n_components=128)
    texts = [f"File: {c.file_path}\n{c.content}" for c in chunks]
    vecs = emb.embed_batch(texts)
    store = VectorStore()
    for i, v in enumerate(vecs):
        if v is not None:
            store.add(v, {"chunk_idx": i, "file": chunks[i].file_path})
    s = HybridSearcher(alpha=0.6)
    s.index(chunks)
    s.set_embedder(emb)
    s.set_vector_store(store)
    return s


def _metrics(searcher, use_mmr):
    rec, mr = [], []
    for q, exp, _cat in GOLDEN:
        res = searcher.search(q, top_k=10, use_mmr=use_mmr, score_threshold=0.0)
        retrieved = [_bn(c.file_path) for c, _ in res]
        rec.append(recall_at_k(exp, retrieved, 10))
        mr.append(mrr(exp, retrieved))
    n = len(GOLDEN)
    return sum(rec) / n, sum(mr) / n


def test_mmr_on_does_not_hurt_recall_or_mrr():
    s = _build_searcher()
    rec_off, mrr_off = _metrics(s, use_mmr=False)
    rec_on, mrr_on = _metrics(s, use_mmr=True)
    # 결함 회귀 방지: MMR을 켜도 recall 손실 없어야 하고, mrr도 크게 떨어지면 안 됨.
    assert rec_on >= rec_off - 1e-9, f"MMR recall 회귀: on={rec_on} off={rec_off}"
    assert mrr_on >= mrr_off * 0.9, f"MMR mrr 급락 회귀: on={mrr_on} off={mrr_off}"
