"""RAG 검색 파이프라인 A/B 벤치마크 (오프라인, 게이트웨이 불필요).

여러 검색 파이프라인을 동일 golden set에서 recall@k / MRR / context_precision으로
비교해 '승리 파이프라인'을 데이터로 결정한다. 신경망 임베딩(Titan/E5/BGE)은 사용
가능해질 때 provider만 끼우면 동일 하네스로 비교된다(현재는 bm25/lsa 실측).

핵심 원칙(다른 AI 리뷰 반영):
  - recall만 보지 않는다 (recall@k + MRR + context_precision 동시).
  - LSA가 지면 채택하지 않는다 — 하네스가 그 판정을 데이터로 내린다.
  - 신경망 임베딩 도입 전까지 RRF/dense 기본전환 금지.

실행: PYTHONPATH=. ai_engine/.venv/bin/python scripts/rag_benchmark.py [--k 10]
"""
import argparse
import json
import os

from ai_engine.rag.indexer import ProjectIndexer
from ai_engine.rag.hybrid_search import HybridSearcher
from ai_engine.rag.embedder import VectorStore, LsaEmbeddingProvider, get_embedding_provider
from ai_engine.rag.eval_metrics import recall_at_k, mrr, context_precision


# KR↔EN 10범주 golden set — 어휘 일치/패러프레이즈/한글질의-영문코드/유사함수 등.
# (query, 정답 근거 파일 basename 집합, category)
GOLDEN = [
    ("reciprocal rank fusion rrf_fuse", {"hybrid_search.py"}, "exact_keyword"),
    ("BM25 score k1 b idf", {"hybrid_search.py"}, "exact_keyword"),
    ("여러 검색 결과를 순위로 합치는 융합 로직", {"hybrid_search.py"}, "kr_paraphrase"),
    ("답변이 근거에서 벗어났는지 점수로 판정하는 검증기", {"verifier.py"}, "kr_to_encode"),
    ("출처 표기가 실제 자료 범위에 포함되는지 대조", {"citation.py"}, "kr_to_encode"),
    ("후보 목록을 관련성 순으로 재배열", {"reranker.py"}, "kr_paraphrase"),
    ("recall at k 와 mrr 계산 함수는 어디", {"eval_metrics.py"}, "similar_funcs"),
    ("소스 파일을 청크로 나눠 색인 구성", {"indexer.py"}, "kr_to_encode"),
    ("모델에 줄 배경 컨텍스트와 시스템 프롬프트 조립", {"context_builder.py"}, "kr_to_encode"),
    ("파이프라인 전체를 추적하는 trace 스키마", {"trace.py"}, "kr_to_encode"),
]


def _bn(p):
    return os.path.basename(p.replace("\\", "/"))


def _make_searcher(chunks, embedder=None, alpha=0.6):
    s = HybridSearcher(alpha=alpha)
    s.index(chunks)
    if embedder is not None:
        texts = [f"File: {c.file_path}\n{c.content}" for c in chunks]
        vecs = embedder.embed_batch(texts)
        store = VectorStore()
        for i, v in enumerate(vecs):
            if v is not None:
                store.add(v, {"chunk_idx": i, "file": chunks[i].file_path})
        if store.size > 0:
            s.set_embedder(embedder)
            s.set_vector_store(store)
    return s


def _score(searcher, k, fusion):
    rec, mr, prec = [], [], []
    for q, expected, _cat in GOLDEN:
        res = searcher.search(q, top_k=k, use_mmr=False, score_threshold=0.0, fusion=fusion)
        retrieved = [_bn(c.file_path) for c, _ in res]
        rec.append(recall_at_k(expected, retrieved, k))
        mr.append(mrr(expected, retrieved))
        prec.append(context_precision(expected, retrieved, k))
    n = len(GOLDEN)
    return {
        "recall_at_k": round(sum(rec) / n, 4),
        "mrr": round(sum(mr) / n, 4),
        "context_precision": round(sum(prec) / n, 4),
    }


def run(project=os.path.join("ai_engine", "rag"), k=10):
    idx = ProjectIndexer()
    idx.index_project(project)
    chunks = idx.chunks

    configs = {}
    # baseline: BM25 단독(어휘)
    configs["bm25"] = _score(_make_searcher(chunks), k, "weighted")
    # LSA hybrid (weighted / rrf)
    lsa = LsaEmbeddingProvider(n_components=128)
    sem = _make_searcher(chunks, embedder=lsa, alpha=0.7)
    configs["bm25+lsa_weighted"] = _score(sem, k, "weighted")
    configs["bm25+lsa_rrf"] = _score(sem, k, "rrf")

    # 신경망 provider가 활성화되면(env) 동일 하네스로 자동 비교.
    # (Titan은 게이트웨이/자격증명, ONNX는 모델 번들 필요 — 미가용 시 스킵)
    for name, env in (("bm25+titan", {"AE_EMBED_PROVIDER": "titan"}),
                      ("bm25+onnx", {"AE_EMBED_PROVIDER": "onnx"})):
        prov = get_embedding_provider(env, gateway_client=None)
        # TF-IDF 폴백(=미가용)이면 벤치에서 제외(정직: 없는 걸 있는 척 안 함)
        from ai_engine.rag.embedder import TfidfEmbeddingProvider
        if isinstance(prov, TfidfEmbeddingProvider):
            configs[name] = {"status": "unavailable (provider not installed/authorized)"}
            continue
        sm = _make_searcher(chunks, embedder=prov, alpha=0.7)
        configs[name] = _score(sm, k, "rrf")

    # 승자 선택: recall@k 우선 → context_precision → mrr (측정 가능한 것만)
    scored = {n: c for n, c in configs.items() if "recall_at_k" in c}
    best = max(scored.items(),
              key=lambda kv: (kv[1]["recall_at_k"], kv[1]["context_precision"], kv[1]["mrr"]))
    return {
        "project": project, "k": k, "n_chunks": len(chunks), "n_queries": len(GOLDEN),
        "configs": configs,
        "winner": best[0],
        "winner_metrics": best[1],
        "verdict": (
            "neural embedding 미도입 — 현재 최적은 %s. 다국어 neural(E5/BGE-M3) 도입 후 "
            "재벤치가 다음 단계." % best[0]
        ),
    }


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--project", default=os.path.join("ai_engine", "rag"))
    ap.add_argument("--k", type=int, default=10)
    args = ap.parse_args()
    print(json.dumps(run(args.project, args.k), ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
