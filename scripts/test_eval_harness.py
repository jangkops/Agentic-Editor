"""평가 하네스 스모크 — run_eval이 오프라인으로 동작하고 지표가 유효한지 (Req 8.1, 8.4).

실행: ./ai_engine/.venv/bin/python -m pytest scripts/test_eval_harness.py -p no:cacheprovider -q
"""
import os
from scripts.eval_rag_quality import run_eval, GOLDEN


def test_harness_runs_offline_and_metrics_valid():
    project = os.path.join("ai_engine", "rag")
    report = run_eval(project, k=3)
    assert report["n_chunks"] > 0
    assert report["n_queries"] == len(GOLDEN)
    assert 0.0 <= report["recall_at_k"] <= 1.0
    assert 0.0 <= report["mrr"] <= 1.0
    # 자체 모듈 대상이므로 최소 품질 하한(회귀 방지용 느슨한 게이트)
    assert report["recall_at_k"] >= 0.7
    assert report["mrr"] >= 0.6


def test_details_shape():
    report = run_eval(os.path.join("ai_engine", "rag"), k=3)
    assert len(report["details"]) == len(GOLDEN)
    for d in report["details"]:
        assert "query" in d and "retrieved" in d and 0.0 <= d["recall"] <= 1.0
