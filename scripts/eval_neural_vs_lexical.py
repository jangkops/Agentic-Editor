"""신경망 다국어 임베딩(fastembed/e5) vs 어휘(BM25) — KR↔EN 실측.

한국어 질의 → 영문 코드 근거의 교차언어 검색을, BM25 단독 vs (BM25+neural) 하이브리드로
동일 golden set에서 recall@k / MRR / context_precision 비교한다. 모델은 최초 1회
다운로드(fastembed ONNX 캐시).

실행: PYTHONPATH=. ai_engine/.venv/bin/python scripts/eval_neural_vs_lexical.py
"""
import os
import json
from ai_engine.rag.indexer import ProjectIndexer
from ai_engine.rag.hybrid_search import HybridSearcher
from ai_engine.rag.embedder import VectorStore, FastEmbedProvider
from ai_engine.rag.eval_metrics import recall_at_k, mrr, context_precision
from scripts.rag_benchmark import GOLDEN  # KR↔EN 10범주 재사용


def _bn(p):
    return os.path.basename(p.replace("\\", "/"))


def _score(searcher, k, fusion):
    rec, mr, prec = [], [], []
    for q, expected, _c in GOLDEN:
        res = searcher.search(q, top_k=k, use_mmr=False, score_threshold=0.0, fusion=fusion)
        got = [_bn(c.file_path) for c, _ in res]
        rec.append(recall_at_k(expected, got, k))
        mr.append(mrr(expected, got))
        prec.append(context_precision(expected, got, k))
    n = len(GOLDEN)
    return {"recall_at_k": round(sum(rec)/n, 4), "mrr": round(sum(mr)/n, 4),
            "context_precision": round(sum(prec)/n, 4)}


def run(project=os.path.join("ai_engine", "rag"), k=10,
        model="intfloat/multilingual-e5-small"):
    idx = ProjectIndexer()
    idx.index_project(project)
    chunks = idx.chunks

    # lexical baseline
    lex = HybridSearcher(alpha=0.6)
    lex.index(chunks)
    out = {"n_chunks": len(chunks), "n_queries": len(GOLDEN), "k": k, "model": model,
           "lexical_bm25": _score(lex, k, "weighted")}

    emb = FastEmbedProvider(model_name=model)
    if not emb.is_ready:
        out["neural"] = {"status": "unavailable (fastembed/model load failed)"}
        return out
    # 폴백이 일어났으면 실제 로드된 모델명으로 정직하게 갱신
    out["model_requested"] = model
    out["model"] = emb.model_name

    sem = HybridSearcher(alpha=0.7)
    sem.index(chunks)
    texts = [f"File: {c.file_path}\n{c.content}" for c in chunks]
    vecs = emb.embed_batch(texts)
    store = VectorStore()
    for i, v in enumerate(vecs):
        if v is not None:
            store.add(v, {"chunk_idx": i, "file": chunks[i].file_path})
    sem.set_embedder(emb)
    sem.set_vector_store(store)

    out["dim"] = emb.dimension
    out["bm25+neural_weighted"] = _score(sem, k, "weighted")
    out["bm25+neural_rrf"] = _score(sem, k, "rrf")
    # dense-only 근사(alpha=1.0 효과) 확인용
    dense_only = HybridSearcher(alpha=1.0)
    dense_only.index(chunks)
    dense_only.set_embedder(emb)
    dense_only.set_vector_store(store)
    out["neural_dense_only"] = _score(dense_only, k, "weighted")

    lr = out["lexical_bm25"]["recall_at_k"]
    best_name = max(
        [n for n in ("bm25+neural_weighted", "bm25+neural_rrf", "neural_dense_only")],
        key=lambda n: (out[n]["recall_at_k"], out[n]["context_precision"], out[n]["mrr"]),
    )
    out["winner_vs_bm25"] = {
        "config": best_name,
        "recall_gain": round(out[best_name]["recall_at_k"] - lr, 4),
        "precision_gain": round(out[best_name]["context_precision"] - out["lexical_bm25"]["context_precision"], 4),
    }
    return out


if __name__ == "__main__":
    print(json.dumps(run(), ensure_ascii=False, indent=2))
