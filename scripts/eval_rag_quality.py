"""RAG 검색 품질 평가 하네스 (오프라인, 게이트웨이 불필요).

프로젝트를 인덱싱(ProjectIndexer)하고 하이브리드 검색(BM25+TF-IDF)으로 golden
질의를 실행해 recall@k / MRR을 실측한다. baseline 저장 및 회귀 게이트를 지원한다.

실행:
  PYTHONPATH=. ai_engine/.venv/bin/python scripts/eval_rag_quality.py [--project DIR] [--k 3]
  PYTHONPATH=. ai_engine/.venv/bin/python scripts/eval_rag_quality.py --save-baseline
  PYTHONPATH=. ai_engine/.venv/bin/python scripts/eval_rag_quality.py --gate   # baseline 대비 회귀 시 exit 1

Requirements: 8.1, 8.4, 8.5
"""
import argparse
import json
import os
import sys

from ai_engine.rag.indexer import ProjectIndexer
from ai_engine.rag.hybrid_search import HybridSearcher
from ai_engine.rag.eval_metrics import recall_at_k, mrr


# golden set: (질의, 정답 근거 파일의 basename 집합). ai_engine/rag 대상 기준.
DEFAULT_PROJECT = os.path.join("ai_engine", "rag")
GOLDEN = [
    ("reciprocal rank fusion 순위 융합", {"hybrid_search.py"}),
    ("BM25 키워드 점수 계산", {"hybrid_search.py"}),
    ("충실도 점수 파싱 SCORE faithfulness", {"verifier.py"}),
    ("인용 범위 검증 citation verify overlap", {"citation.py"}),
    ("리랭크 인덱스 순열 파싱", {"reranker.py"}),
    ("recall at k mrr 지표 계산", {"eval_metrics.py"}),
    ("프로젝트 파일 청크 분할 인덱싱", {"indexer.py"}),
    ("하이브리드 컨텍스트 시스템 프롬프트 조립", {"context_builder.py"}),
    ("TF-IDF 로컬 임베딩 벡터 저장소", {"embedder.py"}),
    ("쿼리 확장 HyDE recall", {"query_expand.py"}),
]

BASELINE_PATH = os.path.join("scripts", ".rag_eval_baseline.json")


def _basename(path: str) -> str:
    return os.path.basename(path.replace("\\", "/"))


def run_eval(project: str, k: int = 3) -> dict:
    idx = ProjectIndexer()
    idx.index_project(project)
    searcher = HybridSearcher(alpha=0.6)
    searcher.index(idx.chunks)  # 임베더 없이 BM25 경로

    recalls, mrrs, details = [], [], []
    for query, expected in GOLDEN:
        results = searcher.search(query, top_k=k, use_mmr=False, score_threshold=0.0)
        retrieved = [_basename(c.file_path) for c, _ in results]
        r = recall_at_k(expected, retrieved, k)
        m = mrr(expected, retrieved)
        recalls.append(r)
        mrrs.append(m)
        details.append({"query": query, "expected": sorted(expected),
                        "retrieved": retrieved, "recall": r, "mrr": round(m, 3)})

    n = len(GOLDEN) or 1
    return {
        "project": project,
        "k": k,
        "n_chunks": len(idx.chunks),
        "n_queries": len(GOLDEN),
        "recall_at_k": round(sum(recalls) / n, 4),
        "mrr": round(sum(mrrs) / n, 4),
        "details": details,
    }


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--project", default=DEFAULT_PROJECT)
    ap.add_argument("--k", type=int, default=3)
    ap.add_argument("--save-baseline", action="store_true")
    ap.add_argument("--gate", action="store_true")
    ap.add_argument("--eps", type=float, default=0.02)
    args = ap.parse_args()

    report = run_eval(args.project, args.k)
    print(json.dumps({kk: report[kk] for kk in
                      ("project", "k", "n_chunks", "n_queries", "recall_at_k", "mrr")},
                     ensure_ascii=False, indent=2))

    if args.save_baseline:
        with open(BASELINE_PATH, "w") as f:
            json.dump({"recall_at_k": report["recall_at_k"], "mrr": report["mrr"]}, f, indent=2)
        print(f"[baseline saved] {BASELINE_PATH}")
        return 0

    if args.gate:
        if not os.path.exists(BASELINE_PATH):
            print("[gate] baseline 없음 — 먼저 --save-baseline 실행", file=sys.stderr)
            return 2
        with open(BASELINE_PATH) as f:
            base = json.load(f)
        regressed = (report["recall_at_k"] < base["recall_at_k"] - args.eps or
                     report["mrr"] < base["mrr"] - args.eps)
        if regressed:
            print(f"[gate] 회귀 감지 — base={base} now="
                  f"{{'recall_at_k': {report['recall_at_k']}, 'mrr': {report['mrr']}}}",
                  file=sys.stderr)
            return 1
        print("[gate] OK — 회귀 없음")
    return 0


if __name__ == "__main__":
    sys.exit(main())
