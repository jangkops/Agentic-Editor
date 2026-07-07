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
    ("Maximal Marginal Relevance 다양성 재정렬 선택", {"hybrid_search.py"}, "exact_keyword"),
    ("답변이 근거에서 벗어났는지 점수로 판정하는 검증기", {"verifier.py"}, "kr_to_encode"),
    ("로컬 임베딩으로 답변의 근거 기반성을 점수화", {"verifier.py"}, "kr_paraphrase"),
    ("충실도 점수 SCORE 파싱 함수", {"verifier.py"}, "similar_funcs"),
    ("출처 표기가 실제 자료 범위에 포함되는지 대조", {"citation.py"}, "kr_to_encode"),
    ("parse citations from answer text", {"citation.py"}, "exact_keyword"),
    ("후보 목록을 관련성 순으로 재배열", {"reranker.py"}, "kr_paraphrase"),
    ("LLM 리랭크 프롬프트 구성과 순서 파싱", {"reranker.py"}, "kr_paraphrase"),
    ("recall at k 와 mrr 계산 함수는 어디", {"eval_metrics.py"}, "similar_funcs"),
    ("context precision 지표 계산", {"eval_metrics.py"}, "exact_keyword"),
    ("소스 파일을 청크로 나눠 색인 구성", {"indexer.py"}, "kr_to_encode"),
    ("모델에 줄 배경 컨텍스트와 시스템 프롬프트 조립", {"context_builder.py"}, "kr_to_encode"),
    ("근거 계약 프롬프트로 검색기 빌드", {"context_builder.py"}, "kr_paraphrase"),
    ("파이프라인 전체를 추적하는 trace 스키마", {"trace.py"}, "kr_to_encode"),
    ("검색 단계 소요시간 stopwatch 측정", {"trace.py"}, "kr_paraphrase"),
    ("짧거나 모호한 질의를 확장해 recall 높이기", {"query_expand.py"}, "kr_to_encode"),
    ("HyDE 가상 답변 생성 쿼리 확장", {"query_expand.py"}, "exact_keyword"),
    ("합의 후보들을 교차 검증하고 사실 충돌 표기", {"cross_verify.py"}, "kr_to_encode"),
    ("여러 임베딩 provider 선택 TF-IDF neural", {"embedder.py"}, "similar_funcs"),
    ("FastEmbed 다국어 ONNX 신경망 임베딩", {"embedder.py"}, "exact_keyword"),
    ("벡터 저장소 코사인 유사도 검색", {"embedder.py"}, "kr_paraphrase"),
    ("deferred 응답 품질 결과 저장소", {"quality_store.py"}, "kr_to_encode"),
    ("응답 품질 검증 모드 off inline deferred", {"answer_quality.py"}, "exact_keyword"),
    ("인용과 충실도 검증을 묶는 오케스트레이터", {"answer_quality.py"}, "kr_paraphrase"),
    ("게이트웨이 스트리밍 텍스트 수집 헬퍼", {"gw_text.py"}, "kr_to_encode"),
    ("쿼리확장 하이브리드 리랭크 검색 파이프라인", {"retrieval_pipeline.py"}, "kr_paraphrase"),
    ("대화 히스토리 메모리 관리", {"conversation_memory.py"}, "kr_to_encode"),
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
