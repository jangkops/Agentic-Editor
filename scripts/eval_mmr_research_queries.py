"""리서치류(다중정답) 질의에서 MMR 다양성의 실제 효과 검증.

실앱(context_builder)은 리서치류 질의에만 use_mmr=True(mmr_lambda=0.4)를 적용한다.
이 조건의 정당성을 다중정답 골든으로 실측한다. specific-lookup GOLDEN(정답 1개)로는
MMR을 공정하게 평가할 수 없기 때문이다.

측정: 정답이 여러 파일에 걸친 질의에서, MMR on이 off 대비 '정답 파일 커버리지
(recall)'와 '결과 다양성(distinct files)'을 높이는지.
"""
import json
import os

from ai_engine.rag.indexer import ProjectIndexer
from ai_engine.rag.hybrid_search import HybridSearcher
from ai_engine.rag.embedder import VectorStore, FastEmbedProvider

# 다중정답 리서치 질의 — 정답이 여러 파일에 분산(실앱 use_mmr=True 조건 반영).
RESEARCH_GOLDEN = [
    ("RAG 검색 파이프라인을 구성하는 여러 모듈",
     {"hybrid_search.py", "retrieval_pipeline.py", "context_builder.py"}),
    ("답변 품질을 검증하는 다양한 컴포넌트",
     {"verifier.py", "citation.py", "cross_verify.py"}),
    ("검색 결과를 재정렬하거나 확장하는 여러 기법",
     {"reranker.py", "query_expand.py"}),
    ("임베딩과 벡터 저장 관련 클래스들",
     {"embedder.py"}),
    ("평가 지표와 추적 스키마 등 관측 관련 모듈 비교",
     {"eval_metrics.py", "trace.py"}),
]


def _bn(p):
    return os.path.basename(p.replace("\\", "/"))


def _build(chunks, emb, alpha=0.6):
    texts = [f"File: {c.file_path}\n{c.content}" for c in chunks]
    vecs = emb.embed_batch(texts)
    store = VectorStore()
    for i, v in enumerate(vecs):
        if v is not None:
            store.add(v, {"chunk_idx": i, "file": chunks[i].file_path})
    s = HybridSearcher(alpha=alpha)
    s.index(chunks)
    s.set_embedder(emb)
    s.set_vector_store(store)
    return s


def _eval(searcher, use_mmr, mmr_lambda, k=8):
    recalls, diversities = [], []
    for q, expected in RESEARCH_GOLDEN:
        res = searcher.search(q, top_k=k, use_mmr=use_mmr, mmr_lambda=mmr_lambda,
                              score_threshold=0.0)
        files = [_bn(c.file_path) for c, _ in res]
        distinct = set(files)
        hit = len(distinct & expected) / max(len(expected), 1)
        recalls.append(hit)
        diversities.append(len(distinct))
    n = len(RESEARCH_GOLDEN)
    return {"coverage_recall": round(sum(recalls) / n, 4),
            "avg_distinct_files": round(sum(diversities) / n, 2)}


def main():
    idx = ProjectIndexer()
    idx.index_project(os.path.join("ai_engine", "rag"))
    chunks = idx.chunks
    emb = FastEmbedProvider()
    if not emb.is_ready:
        print(json.dumps({"status": "neural unavailable"}))
        return 0
    s = _build(chunks, emb)
    out = {
        "n_chunks": len(chunks), "n_queries": len(RESEARCH_GOLDEN), "k": 8,
        "mmr_off":       _eval(s, False, 0.4),
        "mmr_on_l0.4":   _eval(s, True, 0.4),   # 실앱 리서치 조건
        "mmr_on_l0.5":   _eval(s, True, 0.5),
        "mmr_on_l0.7":   _eval(s, True, 0.7),
    }
    print(json.dumps(out, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
