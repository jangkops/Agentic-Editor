"""실제 검색 경로(HybridSearcher, MMR 포함) 파라미터 스윕 — neural 실측 최적화.

rag_benchmark.py의 GOLDEN 셋을 재사용해, neural(fastembed) 임베딩으로
alpha × fusion × MMR 조합을 recall@k / MRR / context_precision으로 비교한다.
목적: context_builder 기본 파라미터를 추측이 아닌 실측으로 확정.

실행: PYTHONPATH=. ai_engine/.venv/bin/python scripts/eval_rag_param_sweep.py [--k 10]
"""
import argparse
import json
import os

from ai_engine.rag.indexer import ProjectIndexer
from ai_engine.rag.hybrid_search import HybridSearcher
from ai_engine.rag.embedder import VectorStore, FastEmbedProvider
from ai_engine.rag.eval_metrics import recall_at_k, mrr, context_precision
from scripts.rag_benchmark import GOLDEN, _bn


def _build_store(chunks, embedder):
    texts = [f"File: {c.file_path}\n{c.content}" for c in chunks]
    vecs = embedder.embed_batch(texts)
    store = VectorStore()
    for i, v in enumerate(vecs):
        if v is not None:
            store.add(v, {"chunk_idx": i, "file": chunks[i].file_path})
    return store


def _score(searcher, k, fusion, use_mmr, mmr_lambda):
    rec, mr, prec = [], [], []
    for q, expected, _cat in GOLDEN:
        res = searcher.search(q, top_k=k, use_mmr=use_mmr, mmr_lambda=mmr_lambda,
                              score_threshold=0.0, fusion=fusion)
        retrieved = [_bn(c.file_path) for c, _ in res]
        rec.append(recall_at_k(expected, retrieved, k))
        mr.append(mrr(expected, retrieved))
        prec.append(context_precision(expected, retrieved, k))
    n = len(GOLDEN)
    return {"recall_at_k": round(sum(rec) / n, 4),
            "mrr": round(sum(mr) / n, 4),
            "context_precision": round(sum(prec) / n, 4)}


def run(project=os.path.join("ai_engine", "rag"), k=10):
    idx = ProjectIndexer()
    idx.index_project(project)
    chunks = idx.chunks

    emb = FastEmbedProvider()  # 기본 다국어 MiniLM (지원 확실)
    if not emb.is_ready:
        return {"status": "neural embedder unavailable"}
    store = _build_store(chunks, emb)

    results = {}
    for alpha in (0.5, 0.6, 0.7, 0.8):
        s = HybridSearcher(alpha=alpha)
        s.index(chunks)
        s.set_embedder(emb)
        s.set_vector_store(store)
        for fusion in ("weighted", "rrf"):
            for use_mmr, mlam in ((False, 0.5), (True, 0.5), (True, 0.7)):
                tag = f"a{alpha}_{fusion}_mmr{'off' if not use_mmr else mlam}"
                results[tag] = _score(s, k, fusion, use_mmr, mlam)

    # 승자: recall → context_precision → mrr
    best = max(results.items(),
               key=lambda kv: (kv[1]["recall_at_k"], kv[1]["context_precision"], kv[1]["mrr"]))
    return {"project": project, "k": k, "n_chunks": len(chunks),
            "n_queries": len(GOLDEN), "model": emb.model_name, "dim": emb.dimension,
            "configs": results, "winner": best[0], "winner_metrics": best[1]}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--project", default=os.path.join("ai_engine", "rag"))
    ap.add_argument("--k", type=int, default=10)
    args = ap.parse_args()
    print(json.dumps(run(args.project, args.k), ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
