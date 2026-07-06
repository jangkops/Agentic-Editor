"""시맨틱(LSA) vs 어휘(BM25/TF-IDF) 검색 비교 — 실측 하네스 (오프라인).

어휘가 겹치지 않는 '패러프레이즈' 질의로 recall@k를 비교해, LSA 잠재의미 임베딩이
순수 어휘 검색 대비 개선됨을 수치로 증명한다. 게이트웨이 불필요.

실행: PYTHONPATH=. ai_engine/.venv/bin/python scripts/eval_semantic_vs_lexical.py
"""
import os
import numpy as np
from ai_engine.rag.indexer import ProjectIndexer
from ai_engine.rag.hybrid_search import HybridSearcher
from ai_engine.rag.embedder import VectorStore, LsaEmbeddingProvider
from ai_engine.rag.eval_metrics import recall_at_k, mrr

# 패러프레이즈 golden: 대상 파일의 실제 토큰(reciprocal/faithfulness 등)을 피하고
# 동의어·상위개념으로만 질의 → 어휘 검색이 놓치기 쉬운 케이스.
PARAPHRASE_GOLDEN = [
    ("검색 결과 여러 개를 순위로 합쳐 하나로 만드는 방법", {"hybrid_search.py"}),
    ("답변이 근거에서 벗어났는지 점수로 판정", {"verifier.py"}),
    ("출처 표기가 실제 자료 범위 안에 있는지 대조", {"citation.py"}),
    ("후보 목록을 관련성 높은 순서로 다시 배열", {"reranker.py"}),
    ("정답을 상위 몇 개 안에 얼마나 담았는지 측정하는 계산", {"eval_metrics.py"}),
    ("소스 파일을 조각으로 나눠 색인 구성", {"indexer.py"}),
    ("모델에게 줄 배경 설명을 조립해 지시문 완성", {"context_builder.py"}),
    ("짧고 모호한 물음을 더 풍부하게 바꿔 검색 적중률 향상", {"query_expand.py"}),
]


def _basename(p):
    return os.path.basename(p.replace("\\", "/"))


def build_lexical(chunks):
    s = HybridSearcher(alpha=0.6)
    s.index(chunks)
    return s  # 임베더 없음 → BM25 단독(어휘)


def build_semantic(chunks):
    s = HybridSearcher(alpha=0.7)  # 시맨틱 비중 ↑
    s.index(chunks)
    emb = LsaEmbeddingProvider(n_components=128)
    texts = [f"File: {c.file_path}\n{c.content}" for c in chunks]
    vecs = emb.embed_batch(texts)
    store = VectorStore()
    for i, v in enumerate(vecs):
        if v is not None:
            store.add(v, {"chunk_idx": i, "file": chunks[i].file_path})
    s.set_embedder(emb)
    s.set_vector_store(store)
    return s


def score(searcher, k, fusion="weighted"):
    recalls, mrrs = [], []
    for q, expected in PARAPHRASE_GOLDEN:
        res = searcher.search(q, top_k=k, use_mmr=False, score_threshold=0.0, fusion=fusion)
        retrieved = [_basename(c.file_path) for c, _ in res]
        recalls.append(recall_at_k(expected, retrieved, k))
        mrrs.append(mrr(expected, retrieved))
    n = len(PARAPHRASE_GOLDEN)
    return round(sum(recalls) / n, 4), round(sum(mrrs) / n, 4)


def run(project=os.path.join("ai_engine", "rag"), k=3):
    idx = ProjectIndexer()
    idx.index_project(project)
    lex = build_lexical(idx.chunks)
    sem = build_semantic(idx.chunks)

    lr, lm = score(lex, k)                       # 어휘 단독(BM25)
    swr, swm = score(sem, k, fusion="weighted")  # BM25 + LSA 가중합
    shr, shm = score(sem, k, fusion="rrf")       # BM25 + LSA RRF 융합

    configs = {
        "lexical_bm25":   {"recall_at_k": lr,  "mrr": lm},
        "hybrid_weighted": {"recall_at_k": swr, "mrr": swm},
        "hybrid_rrf":     {"recall_at_k": shr, "mrr": shm},
    }
    # recall 우선, 동률이면 mrr로 최적 구성 선택
    best = max(configs.items(), key=lambda kv: (kv[1]["recall_at_k"], kv[1]["mrr"]))
    return {
        "n_chunks": len(idx.chunks), "n_queries": len(PARAPHRASE_GOLDEN), "k": k,
        "configs": configs,
        "best": best[0],
        "best_vs_lexical_recall_gain": round(best[1]["recall_at_k"] - lr, 4),
        "best_vs_lexical_mrr_gain": round(best[1]["mrr"] - lm, 4),
    }


if __name__ == "__main__":
    import json
    print(json.dumps(run(), ensure_ascii=False, indent=2))
