# Feature: deep-research-engine
"""기존 자산 재사용 스모크 테스트 (Task 18.3) — deep-research-engine.

design.md "재사용(재구현 금지)" 원칙과 요구사항 16(기존 자산 재사용)을 실측으로 강제한다.
딥리서치 엔진은 신규 그래프·신규 지표·신규 융합 로직을 만들지 않고, 이미 검증된 자산을
**그대로 재사용**한다. 이 스모크 테스트는 세 축으로 그 계약을 확인한다:

  A) 재사용 자산 존재·호출 가능 — 아래 자산이 정확한 이름으로 import 되고 호출 가능/클래스임:
       - 검색 융합   : ``ai_engine.rag.hybrid_search.rrf_fuse``                      (요구사항 16.1)
       - 재랭킹      : ``ai_engine.rag.reranker.rerank`` / ``parse_rerank_order``    (요구사항 16.1)
       - 인용        : ``ai_engine.rag.citation.verify_citations`` / ``parse_citations`` (요구사항 16.2)
       - 근거성      : ``ai_engine.rag.answer_quality.enhance_answer``
                       + ``ai_engine.agent_system.grounding_gate.grounding_below``   (요구사항 16.2)
       - 지표        : ``ai_engine.rag.eval_metrics.context_precision`` / ``mrr`` /
                       ``recall_at_k`` / ``unverified_ratio``                         (요구사항 16.1)
       - 오케스트레이션: ``ai_engine.agent_system.dag.sanitize_depends_on`` /
                       ``topological_waves``,
                       ``ai_engine.agent_system.checkpoint_store.JsonFileCheckpointSaver``,
                       ``ai_engine.agent_system.store.JsonFileStore``                 (요구사항 16.3)

  B) research 모듈이 이 자산들을 **참조(재사용)** — AST import 스캔으로 확인(재구현이 아님):
       - ``research/rank.py``          → ``rrf_fuse`` + ``parse_rerank_order`` 참조   (요구사항 16.1)
       - ``research/deep_research.py`` → ``dag.sanitize_depends_on``/``topological_waves``
                                          + ``citation.verify_citations``
                                          + ``checkpoint_store``/``store``
                                          + ``answer_quality``/``grounding_gate`` 참조 (요구사항 16.2/16.3)
       - ``research/eval_harness.py``  → ``eval_metrics`` 참조                         (요구사항 16.1)
     (lazy import 도 잡도록 ``ast.walk`` 로 트리 전체를 순회한다.)

  C) 경량 행위 증명 — 재사용 호출 경로가 실제로 동작함(계약 준수):
       - ``rrf_fuse`` 를 작은 순위 리스트로 호출 → 입력 인덱스의 순열/결정적 산출.
       - ``dag.topological_waves`` 를 작은 plan 으로 호출 → 위상 웨이브 분할.
       - ``eval_metrics.mrr``/``context_precision``/``recall_at_k``/``unverified_ratio`` 작은 예.
       - ``reranker.parse_rerank_order`` → 범위밖/garbage 입력에도 순열 보장.
       - ``citation.parse_citations``+``verify_citations`` → 근거 범위 대조.
       - ``grounding_gate.grounding_below`` (순수 판정) → 신호 없으면 통과(False).

  D) 재구현 부재 — research 모듈이 재사용 심볼(rrf_fuse/topological_waves/verify_citations/
     JsonFileCheckpointSaver/JsonFileStore/mrr/…)을 **로컬로 재정의하지 않음**(AST def/class 스캔).
     즉 자체 RRF/지표/융합 로직을 새로 만들지 않고 import 해서 위임한다.

주의: A/C 는 재사용 자산을 실제 import 하므로 langchain_core/langgraph 등 오케스트레이션
스택이 필요하다. 반드시 ``ai_engine/.venv`` 로 실행한다. B/D 는 소스 텍스트만 AST 파싱하므로
research 모듈을 import 하지 않는다(헤르메틱).

Run (venv 필수):
    ai_engine/.venv/bin/python -m pytest scripts/test_research_asset_reuse_smoke.py -q
    ai_engine/.venv/bin/python scripts/test_research_asset_reuse_smoke.py

_Requirements: 16.1, 16.2, 16.3, 16.4, 16.5_
"""
from __future__ import annotations

import ast
import importlib
import inspect
import sys
from pathlib import Path

# 스크립트를 직접 실행할 때도 ai_engine 패키지를 import 할 수 있게 repo 루트를 경로에 추가.
_REPO_ROOT = Path(__file__).resolve().parent.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

_RESEARCH_DIR = _REPO_ROOT / "ai_engine" / "research"


# ===========================================================================
# 재사용 자산 레지스트리 — (module, symbol, kind)
#   kind="func"  → 호출 가능한 함수(동기/비동기 무관, callable 이면 OK)
#   kind="class" → 클래스(callable 이면서 inspect.isclass). 필수 메서드도 함께 검증.
# ===========================================================================
_REUSED_FUNCS = [
    # 검색 융합 (요구사항 16.1)
    ("ai_engine.rag.hybrid_search", "rrf_fuse"),
    # 재랭킹 (요구사항 16.1)
    ("ai_engine.rag.reranker", "rerank"),
    ("ai_engine.rag.reranker", "parse_rerank_order"),
    # 인용 (요구사항 16.2)
    ("ai_engine.rag.citation", "verify_citations"),
    ("ai_engine.rag.citation", "parse_citations"),
    # 근거성 (요구사항 16.2)
    ("ai_engine.rag.answer_quality", "enhance_answer"),
    ("ai_engine.agent_system.grounding_gate", "grounding_below"),
    # 지표 (요구사항 16.1)
    ("ai_engine.rag.eval_metrics", "context_precision"),
    ("ai_engine.rag.eval_metrics", "mrr"),
    ("ai_engine.rag.eval_metrics", "recall_at_k"),
    ("ai_engine.rag.eval_metrics", "unverified_ratio"),
    # 오케스트레이션 — DAG 웨이브 (요구사항 16.3)
    ("ai_engine.agent_system.dag", "sanitize_depends_on"),
    ("ai_engine.agent_system.dag", "topological_waves"),
]

# 오케스트레이션 — 체크포인트/세션 간 store 클래스 (요구사항 16.3). 필수 메서드 계약 포함.
_REUSED_CLASSES = [
    ("ai_engine.agent_system.checkpoint_store", "JsonFileCheckpointSaver",
     ("put", "get_tuple", "aput")),
    ("ai_engine.agent_system.store", "JsonFileStore",
     ("put", "batch", "abatch")),
]


def _import_symbol(module_path: str, symbol: str):
    """``module_path`` 를 import 하고 ``symbol`` 속성을 반환한다(없으면 AssertionError)."""
    mod = importlib.import_module(module_path)
    assert hasattr(mod, symbol), (
        f"재사용 자산 이름 불일치: {module_path}.{symbol} 가 존재하지 않음 "
        "(모듈은 있으나 심볼명이 바뀌었을 수 있음 — 재사용 배선을 확인하라)."
    )
    return getattr(mod, symbol)


# ===========================================================================
# A) 재사용 자산 존재·호출 가능 (요구사항 16.1/16.2/16.3)
# ===========================================================================
def test_reused_functions_import_and_callable():
    """재사용 함수 자산이 정확한 이름으로 import 되고 모두 호출 가능해야 한다."""
    not_callable = []
    for module_path, symbol in _REUSED_FUNCS:
        obj = _import_symbol(module_path, symbol)
        if not callable(obj):
            not_callable.append(f"{module_path}.{symbol}")
    assert not not_callable, (
        "재사용 함수 자산이 호출 가능하지 않음(이름은 있으나 함수가 아님): "
        + ", ".join(not_callable)
    )


def test_reused_orchestration_classes_present():
    """체크포인트/스토어 재사용 클래스가 존재하고 기존 메서드 계약을 갖춰야 한다(요구사항 16.3)."""
    problems = []
    for module_path, symbol, methods in _REUSED_CLASSES:
        obj = _import_symbol(module_path, symbol)
        if not inspect.isclass(obj):
            problems.append(f"{module_path}.{symbol} 가 클래스가 아님")
            continue
        if not callable(obj):
            problems.append(f"{module_path}.{symbol} 가 호출(인스턴스화) 불가")
        missing = [m for m in methods if not hasattr(obj, m)]
        if missing:
            problems.append(f"{module_path}.{symbol} 필수 메서드 누락: {missing}")
    assert not problems, "재사용 오케스트레이션 클래스 계약 위반:\n  " + "\n  ".join(problems)


# ===========================================================================
# C) 경량 행위 증명 — 재사용 호출 경로 동작 확인
# ===========================================================================
def test_rrf_fuse_behavior():
    """``rrf_fuse`` 가 작은 순위 리스트를 융합해 입력 인덱스의 결정적 순열을 산출한다(요구사항 16.1)."""
    from ai_engine.rag.hybrid_search import rrf_fuse

    fused = rrf_fuse([[0, 1, 2], [2, 1, 0]])
    # (idx, score) 튜플의 내림차순 리스트.
    assert isinstance(fused, list) and fused, f"rrf_fuse 결과가 비어있음: {fused!r}"
    idxs = [idx for idx, _score in fused]
    assert sorted(idxs) == [0, 1, 2], f"입력 인덱스의 순열이 아님: {idxs}"
    scores = [s for _idx, s in fused]
    assert scores == sorted(scores, reverse=True), f"RRF 점수 내림차순 아님: {scores}"
    # 결정성: 동일 입력 → 동일 출력.
    assert rrf_fuse([[0, 1, 2], [2, 1, 0]]) == fused


def test_parse_rerank_order_is_permutation():
    """``parse_rerank_order`` 는 범위밖/garbage 입력에도 [0,n) 순열을 보장한다(요구사항 16.1, P4 계승)."""
    from ai_engine.rag.reranker import parse_rerank_order

    assert parse_rerank_order("[2, 0, 1]", 3) == [2, 0, 1]
    # garbage/누락 → 원순서 보강으로 완전 순열.
    assert sorted(parse_rerank_order("nonsense", 3)) == [0, 1, 2]
    # 범위밖(9)·중복(1) 무시 후 누락 보강 → 여전히 순열.
    assert sorted(parse_rerank_order("[9, 1, 1]", 3)) == [0, 1, 2]


def test_topological_waves_behavior():
    """``dag`` 재사용 경로: 작은 plan 을 sanitize 후 위상 웨이브로 분할한다(요구사항 16.3)."""
    from ai_engine.agent_system.dag import sanitize_depends_on, topological_waves

    plan = [
        {"id": "a", "subtask": "overview", "depends_on": []},
        {"id": "b", "subtask": "detail", "depends_on": ["a"]},
    ]
    waves = topological_waves(sanitize_depends_on(plan))
    ids = [[node["id"] for node in wave] for wave in waves]
    assert ids == [["a"], ["b"]], f"위상 웨이브 분할이 기대와 다름: {ids}"


def test_eval_metrics_behavior():
    """``eval_metrics`` 지표 함수가 작은 예에서 정확한 값을 산출한다(요구사항 16.1)."""
    from ai_engine.rag.eval_metrics import (
        context_precision,
        mrr,
        recall_at_k,
        unverified_ratio,
    )

    assert mrr({"x"}, ["x", "y"]) == 1.0
    assert mrr({"y"}, ["x", "y"]) == 0.5
    assert context_precision({"x"}, ["x", "y"], 2) == 0.5
    assert recall_at_k({"x"}, ["x", "y"], 2) == 1.0
    assert unverified_ratio(1, 4) == 0.25
    assert unverified_ratio(0, 0) == 0.0  # 인용 0건 → 0.0


def test_citation_parse_and_verify_behavior():
    """``parse_citations``+``verify_citations`` 재사용 경로가 근거 범위와 대조한다(요구사항 16.2)."""
    from ai_engine.rag.citation import parse_citations, verify_citations, RetrievedRange

    cites = parse_citations("근거는 src/main.py:10-20 참조")
    assert len(cites) == 1, f"인용 파싱 실패: {cites}"
    report = verify_citations(
        cites, [RetrievedRange(file="src/main.py", start_line=1, end_line=100)]
    )
    assert len(report.verified) == 1 and len(report.unverified) == 0
    # 근거 밖 인용은 미검증(비차단).
    report2 = verify_citations(
        cites, [RetrievedRange(file="other.py", start_line=1, end_line=5)]
    )
    assert len(report2.verified) == 0 and len(report2.unverified) == 1


def test_grounding_gate_pure_decision():
    """``grounding_gate.grounding_below`` 순수 판정: 근거 신호가 없으면 통과(False)한다(요구사항 16.2)."""
    from ai_engine.agent_system.grounding_gate import grounding_below

    # 신호(faithfulness/grounding) 부재 → 통과(요구사항 7.4 정합, 비차단).
    assert grounding_below({}, {}) is False
    # grounding.score 가 임계 미만이면 근거 미달(True)로 판정(임계값 명시 주입).
    below = grounding_below(
        {"grounding": {"score": 0.1}}, {"AE_VERIFY_THRESHOLD": "0.7"}
    )
    assert below is True


# ===========================================================================
# B) research 모듈이 재사용 자산을 참조 (AST import 스캔, 헤르메틱)
# D) 재구현 부재 (AST def/class 스캔)
# ===========================================================================
def _parse_research_file(filename: str) -> ast.AST:
    path = _RESEARCH_DIR / filename
    assert path.is_file(), f"research 모듈 누락: {path}"
    return ast.parse(path.read_text(encoding="utf-8"), filename=str(path))


def _imported_targets(tree: ast.AST) -> set[str]:
    """트리 전체(lazy/nested 포함)에서 import 대상을 점 경로 문자열 집합으로 수집한다.

    - ``from M import a, b``  → {M, M.a, M.b}
    - ``from M import x as y`` → {M, M.x}  (원 심볼명 기준)
    - ``import P`` / ``import P as q`` → {P}
    상대 import(``from . import x``)는 root 를 특정할 수 없어 제외한다(외부/내부 절대 참조만).
    """
    targets: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom):
            if node.level and node.level > 0:
                continue  # 상대 import 제외
            module = node.module or ""
            if not module:
                continue
            targets.add(module)
            for alias in node.names:
                targets.add(f"{module}.{alias.name}")
        elif isinstance(node, ast.Import):
            for alias in node.names:
                if alias.name:
                    targets.add(alias.name)
    return targets


def _defined_names(tree: ast.AST) -> set[str]:
    """트리 전체(nested 포함)의 함수/클래스 정의 이름 집합(재구현 탐지용)."""
    names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            names.add(node.name)
    return names


def test_rank_references_fusion_and_reranker_assets():
    """``research/rank.py`` 가 ``rrf_fuse``·``parse_rerank_order`` 를 참조한다(재사용 — 요구사항 16.1)."""
    targets = _imported_targets(_parse_research_file("rank.py"))
    for required in (
        "ai_engine.rag.hybrid_search.rrf_fuse",
        "ai_engine.rag.reranker.parse_rerank_order",
    ):
        assert required in targets, (
            f"research/rank.py 가 재사용 자산 {required} 를 참조하지 않음 "
            f"(재구현 의심). 발견된 ai_engine import: "
            f"{sorted(t for t in targets if t.startswith('ai_engine'))}"
        )


def test_deep_research_references_orchestration_and_citation_assets():
    """``research/deep_research.py`` 가 dag/citation/checkpoint/store/근거성 자산을 참조한다(요구사항 16.2/16.3)."""
    targets = _imported_targets(_parse_research_file("deep_research.py"))
    required = [
        "ai_engine.agent_system.dag.sanitize_depends_on",
        "ai_engine.agent_system.dag.topological_waves",
        "ai_engine.rag.citation.verify_citations",
        "ai_engine.agent_system.checkpoint_store.JsonFileCheckpointSaver",
        "ai_engine.agent_system.store.JsonFileStore",
        "ai_engine.rag.answer_quality.enhance_answer",
        "ai_engine.agent_system.grounding_gate.grounding_below",
    ]
    missing = [r for r in required if r not in targets]
    assert not missing, (
        "research/deep_research.py 가 다음 재사용 자산을 참조하지 않음(재구현 의심): "
        + ", ".join(missing)
        + f"\n발견된 ai_engine import: {sorted(t for t in targets if t.startswith('ai_engine'))}"
    )


def test_eval_harness_references_eval_metrics_asset():
    """``research/eval_harness.py`` 가 ``eval_metrics`` 를 참조한다(재사용 — 요구사항 16.1)."""
    targets = _imported_targets(_parse_research_file("eval_harness.py"))
    assert "ai_engine.rag.eval_metrics" in targets, (
        "research/eval_harness.py 가 재사용 지표 자산 ai_engine.rag.eval_metrics 를 "
        f"참조하지 않음(재구현 의심). 발견된 ai_engine import: "
        f"{sorted(t for t in targets if t.startswith('ai_engine'))}"
    )


def test_no_reimplementation_of_reused_symbols():
    """research 모듈이 재사용 심볼을 **로컬 재정의하지 않음**을 확인한다(재구현 금지 — 요구사항 16.1~16.3).

    재사용 자산은 import 해서 위임할 뿐, 같은 이름의 함수/클래스를 새로 정의하면 안 된다.
    (예: rank.py 가 자체 ``rrf_fuse`` 를 정의하면 RRF 재구현이다.)
    """
    forbidden_defs = {
        "rank.py": {"rrf_fuse"},
        "deep_research.py": {
            "topological_waves",
            "sanitize_depends_on",
            "verify_citations",
            "JsonFileCheckpointSaver",
            "JsonFileStore",
        },
        "eval_harness.py": {
            "context_precision",
            "mrr",
            "recall_at_k",
            "unverified_ratio",
        },
    }
    violations = []
    for filename, forbidden in forbidden_defs.items():
        defined = _defined_names(_parse_research_file(filename))
        clashing = sorted(defined & forbidden)
        if clashing:
            violations.append(f"{filename} 가 재사용 심볼을 재정의함: {clashing}")
    assert not violations, (
        "재구현 금지 위반(재사용 자산과 동일 이름을 로컬 정의):\n  " + "\n  ".join(violations)
    )


# ---------------------------------------------------------------------------
# 단발 실행 지원(pytest 없이) — 워치 모드 금지.
# ---------------------------------------------------------------------------
if __name__ == "__main__":
    import pytest

    raise SystemExit(pytest.main([__file__, "-q"]))
