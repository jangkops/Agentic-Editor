"""모델 크기별 품질·비용 트레이드오프 실측 (번들 모델 확정용).

후보(fastembed 다국어, ONNX·CPU·torch 불필요):
  - sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2   0.22GB dim=384
  - sentence-transformers/paraphrase-multilingual-mpnet-base-v2   1.0 GB dim=768
  - intfloat/multilingual-e5-large                                2.24GB dim=1024

각 모델에 대해 동일 golden set(KR↔EN 10범주)에서:
  recall@k / MRR / context_precision (BM25 단독 대비 최적 융합),
  로드 시간 / 인덱스 임베딩 시간 / 쿼리 임베딩 p50 latency, 차원, 디스크 크기.

모델은 최초 1회 다운로드(캐시). 다운로드 실패/미가용 모델은 status로 표기(정직).

실행: PYTHONPATH=. ai_engine/.venv/bin/python scripts/eval_model_size_tradeoff.py
옵션: AE_BENCH_MODELS="a,b" 로 특정 모델만, AE_BENCH_K=10
"""
import os
import json
import time
import statistics

from ai_engine.rag.indexer import ProjectIndexer
from ai_engine.rag.hybrid_search import HybridSearcher
from ai_engine.rag.embedder import VectorStore, FastEmbedProvider
from ai_engine.rag.eval_metrics import recall_at_k, mrr, context_precision
from scripts.rag_benchmark import GOLDEN

CANDIDATES = [
    ("sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2", 0.22, 384),
    ("sentence-transformers/paraphrase-multilingual-mpnet-base-v2", 1.0, 768),
    ("intfloat/multilingual-e5-large", 2.24, 1024),
]


def _bn(p):
    return os.path.basename(p.replace("\\", "/"))


def _score(searcher, k, fusion):
    rec, mr, prec, lat = [], [], [], []
    for q, expected, _c in GOLDEN:
        t0 = time.perf_counter()
        res = searcher.search(q, top_k=k, use_mmr=False, score_threshold=0.0, fusion=fusion)
        lat.append((time.perf_counter() - t0) * 1000)
        got = [_bn(c.file_path) for c, _ in res]
        rec.append(recall_at_k(expected, got, k))
        mr.append(mrr(expected, got))
        prec.append(context_precision(expected, got, k))
    n = len(GOLDEN)
    return {
        "recall_at_k": round(sum(rec) / n, 4),
        "mrr": round(sum(mr) / n, 4),
        "context_precision": round(sum(prec) / n, 4),
        "query_latency_ms_p50": round(statistics.median(lat), 2),
    }


def _lexical_baseline(chunks, k):
    lex = HybridSearcher(alpha=0.6)
    lex.index(chunks)
    return _score(lex, k, "weighted")


def eval_model(model_name, size_gb, chunks, k):
    out = {"model": model_name, "size_GB": size_gb}
    t0 = time.perf_counter()
    try:
        emb = FastEmbedProvider(model_name=model_name)
    except Exception as e:  # noqa: BLE001
        out["status"] = f"init_error: {e}"
        return out
    if not emb.is_ready:
        out["status"] = "unavailable (download/load failed)"
        return out
    out["load_time_s"] = round(time.perf_counter() - t0, 2)
    out["dim"] = emb.dimension

    texts = [f"File: {c.file_path}\n{c.content}" for c in chunks]
    t1 = time.perf_counter()
    vecs = emb.embed_batch(texts)
    out["index_embed_time_s"] = round(time.perf_counter() - t1, 2)

    store = VectorStore()
    for i, v in enumerate(vecs):
        if v is not None:
            store.add(v, {"chunk_idx": i, "file": chunks[i].file_path})

    def _mk(alpha):
        s = HybridSearcher(alpha=alpha)
        s.index(chunks)
        s.set_embedder(emb)
        s.set_vector_store(store)
        return s

    hybrid = _mk(0.7)
    dense = _mk(1.0)
    out["bm25+neural_weighted"] = _score(hybrid, k, "weighted")
    out["bm25+neural_rrf"] = _score(hybrid, k, "rrf")
    out["neural_dense_only"] = _score(dense, k, "weighted")
    out["status"] = "ok"
    return out


def run():
    project = os.environ.get("AE_BENCH_PROJECT", os.path.join("ai_engine", "rag"))
    k = int(os.environ.get("AE_BENCH_K", "10"))
    idx = ProjectIndexer()
    idx.index_project(project)
    chunks = idx.chunks

    sel = os.environ.get("AE_BENCH_MODELS", "").strip()
    cands = CANDIDATES
    if sel:
        want = {s.strip() for s in sel.split(",")}
        cands = [c for c in CANDIDATES if c[0] in want or c[0].split("/")[-1] in want]

    result = {
        "project": project, "n_chunks": len(chunks), "n_queries": len(GOLDEN), "k": k,
        "lexical_bm25": _lexical_baseline(chunks, k),
        "models": [],
    }

    for name, size, _dim in cands:
        print(f"[bench] evaluating {name} ({size}GB) ...", flush=True)
        r = eval_model(name, size, chunks, k)
        result["models"].append(r)
        print(json.dumps(r, ensure_ascii=False), flush=True)

    # 추천: recall 우선, 동률이면 context_precision, 그다음 작은 크기.
    ok = [m for m in result["models"] if m.get("status") == "ok"]

    def _best_cfg(m):
        cfgs = ("bm25+neural_weighted", "bm25+neural_rrf", "neural_dense_only")
        bn = max(cfgs, key=lambda c: (m[c]["recall_at_k"], m[c]["context_precision"], m[c]["mrr"]))
        return bn, m[bn]

    ranked = []
    for m in ok:
        bn, bc = _best_cfg(m)
        ranked.append((m["model"], m["size_GB"], bn, bc))
    ranked.sort(key=lambda x: (-x[3]["recall_at_k"], -x[3]["context_precision"], x[1]))
    if ranked:
        top = ranked[0]
        result["recommendation"] = {
            "model": top[0], "size_GB": top[1], "best_fusion": top[2],
            "metrics": top[3],
            "rationale": "recall 최우선, 동률 시 precision, 그다음 최소 용량. "
                         "품질 동률이면 작은 모델을 번들해 앱 용량·다운로드 비용 절감.",
        }
    return result


if __name__ == "__main__":
    print(json.dumps(run(), ensure_ascii=False, indent=2))
