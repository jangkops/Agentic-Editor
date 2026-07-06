"""build_context/build_system_prompt 근거 반환 계약 검증 (스트리밍 answer_quality 배선용).

- return_chunks/return_evidence 기본 off → 기존 문자열 반환(무회귀)
- on → (문자열, chunks/evidence) 튜플, chunks는 (chunk, score) 형태

실행: ./ai_engine/.venv/bin/python -m pytest scripts/test_context_builder_evidence.py -p no:cacheprovider -q
"""
import os
import ai_engine.rag.context_builder as cb

PROJ = os.path.join(os.getcwd(), "ai_engine", "rag")


def test_build_context_default_returns_str():
    ctx = cb.build_context(PROJ, "hybrid search")
    assert isinstance(ctx, str) and "## 관련 코드" in ctx


def test_build_context_return_chunks_tuple():
    ctx, chunks = cb.build_context(PROJ, "reciprocal rank fusion", return_chunks=True)
    assert isinstance(ctx, str)
    assert isinstance(chunks, list)
    assert chunks, "관련 청크가 있어야 함"
    chunk, score = chunks[0]
    assert hasattr(chunk, "file_path") and isinstance(score, float)


def test_build_system_prompt_default_returns_str():
    p = cb.build_system_prompt(PROJ, "hybrid search")
    assert isinstance(p, str) and len(p) > 0


def test_build_system_prompt_return_evidence_tuple():
    p, ev = cb.build_system_prompt(PROJ, "hybrid search", return_evidence=True)
    assert isinstance(p, str)
    assert isinstance(ev, dict)
    assert "context" in ev and "chunks" in ev
    assert isinstance(ev["chunks"], list)
    # context 문자열이 prompt에 포함되어야 함(동일 근거 재사용)
    if ev["context"]:
        assert ev["context"] in p


def test_empty_project_path_evidence_shape():
    ctx, chunks = cb.build_context("", "q", return_chunks=True)
    assert ctx == "" and chunks == []
    p, ev = cb.build_system_prompt("", "q", return_evidence=True)
    assert ev == {"context": "", "chunks": []} or (ev["chunks"] == [])
