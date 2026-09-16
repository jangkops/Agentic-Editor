"""deep_research 근거성 메타데이터(answer_quality) 병합 — 임시 스모크 `scripts/_smoke_task_13_2.py`(2026-07-28,
Task 13.2)를 pytest 로 옮긴 회귀 테스트.

임베더 기반 grounding/faithfulness(`_enhance_grounding_metadata`)는 monkeypatch 로 대체한다 — 모델 로딩·네트워크 없이
결정적으로 돌고, 인용 집계·unverified_ratio·리포트 병합·0-근거 폴백만 검증한다.
"""
import asyncio
from dataclasses import asdict

import pytest

import ai_engine.research.deep_research as dr
from ai_engine.research.models import EvidenceSource, ResearchReport


def _evidence():
    return [
        EvidenceSource(source_id="web:https://example.com/a", title="A", url_or_doi="https://example.com/a",
                       provider="tavily", content="Deep research merges evidence sources for grounding.", authority=0.8),
        EvidenceSource(source_id="doi:10.1000/xyz", title="B", url_or_doi="10.1000/xyz",
                       provider="semantic_scholar", content="Faithfulness and grounding scores quantify support.", authority=0.9),
    ]


@pytest.fixture
def no_embedder(monkeypatch):
    async def _fake(*_a, **_k):
        return {}
    monkeypatch.setattr(dr, "_enhance_grounding_metadata", _fake)


@pytest.fixture
def stub_grounding(monkeypatch):
    async def _fake(*_a, **_k):
        return {"grounding": {"score": 0.5}}
    monkeypatch.setattr(dr, "_enhance_grounding_metadata", _fake)


def test_unverified_ratio_is_bounded_and_zero_without_citations():
    assert dr._unverified_ratio({}) == 0.0
    assert dr._unverified_ratio({"verified": [], "unverified": []}) == 0.0
    assert abs(dr._unverified_ratio({"verified": ["a", "b", "c"], "unverified": ["d"]}) - 0.25) < 1e-9
    assert dr._unverified_ratio({"verified": [], "unverified": ["x", "y"]}) == 1.0
    r = dr._unverified_ratio({"verified": ["a"], "unverified": ["b", "c", "d"]})
    assert 0.0 <= r <= 1.0 and abs(r - 0.75) < 1e-9


def test_answer_quality_metadata_shape_without_gateway(no_embedder):
    evidence = _evidence()
    citations = {"verified": ["web:https://example.com/a"], "unverified": ["doi:10.1000/zzz"]}
    meta = asyncio.run(dr._build_answer_quality_metadata(
        "# Report\nClaim [web:https://example.com/a] and bad [doi:10.1000/zzz].",
        evidence, citations, dr._unverified_ratio(citations), deps_or_gw=None, env=None,
    ))
    c = meta["citation"]
    assert isinstance(c, dict)
    assert c["citations_total"] == 2
    assert c["verified"] == 1
    assert c["unverified"] == ["doi:10.1000/zzz"]
    assert abs(meta["unverified_ratio"] - 0.5) < 1e-9
    assert "faithfulness" not in meta          # gateway 없음 → faithfulness 자동 skip
    assert "grounding" not in meta             # 임베더 없음(대체) → grounding 없음
    if "grounding_gate" in meta:
        assert isinstance(meta["grounding_gate"].get("below_threshold"), bool)


def test_synthesize_report_merges_answer_quality_and_metrics(stub_grounding):
    rep = asyncio.run(dr._synthesize_report("What is deep research?", _evidence(), None, None))
    assert isinstance(rep, ResearchReport)
    assert isinstance(rep.answer_quality, dict) and "citation" in rep.answer_quality
    assert rep.answer_quality["citation"]["citations_total"] >= 1
    assert abs(rep.unverified_ratio - 0.0) < 1e-9                       # 결정적 폴백은 근거 id 만 인용한다
    assert abs(rep.metrics.grounding_score - 0.5) < 1e-9                # grounding.score → metrics 반영
    assert abs(rep.metrics.citation_accuracy - (1.0 - rep.unverified_ratio)) < 1e-9
    d = asdict(rep)                                                     # report.json 저장 경로 정합
    assert isinstance(d["answer_quality"], dict) and "grounding_score" in d["metrics"]


def test_synthesize_report_without_evidence_reports_zero(no_embedder):
    rep0 = asyncio.run(dr._synthesize_report("no evidence query", [], None, None))
    assert rep0.answer_quality["citation"]["citations_total"] == 0
    assert rep0.unverified_ratio == 0.0
    assert rep0.answer_quality["unverified_ratio"] == 0.0
    assert "외부 근거 미확보" in rep0.report_markdown
    assert rep0.metrics.grounding_score == 0.0
