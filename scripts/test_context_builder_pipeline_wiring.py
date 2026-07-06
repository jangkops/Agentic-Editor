"""context_builder 근거 파이프라인 배선 검증 (Req 5.2, 6, 10.1).

- 기본(AE_RETRIEVAL_PIPELINE off): 기존 searcher.search 경로 사용(무회귀)
- on + 게이트웨이 제공: retrieve_evidence_sync 경로 사용
- 파이프라인 예외: 기존 검색으로 안전 폴백

실행: ./ai_engine/.venv/bin/python -m pytest scripts/test_context_builder_pipeline_wiring.py -p no:cacheprovider -q
"""
import os
import ai_engine.rag.context_builder as cb

PROJ = os.path.join(os.getcwd(), "ai_engine", "rag")


class _GW:
    """리랭크/확장을 부르지 않는 기본 경로용 최소 게이트웨이 스텁."""
    async def converse(self, **kwargs):
        return {"output": {"message": {"content": [{"text": "[]"}]}}}


def _clear_flags():
    for k in ("AE_RETRIEVAL_PIPELINE", "AE_RERANK", "AE_QUERY_EXPAND", "AE_FUSION"):
        os.environ.pop(k, None)


def _header_label(ctx: str) -> str:
    """'## 관련 코드 (<label>...' 헤더의 라벨 부분만 추출(검색된 소스코드 오탐 방지)."""
    for line in ctx.splitlines():
        if line.startswith("## 관련 코드 ("):
            return line
    return ""


def test_default_off_uses_legacy_search():
    _clear_flags()
    ctx = cb.build_context(PROJ, "hybrid search rrf fusion", gateway_client=_GW())
    header = _header_label(ctx)
    assert header, "관련 코드 섹션 헤더가 있어야 함"
    assert "Pipeline(" not in header  # 파이프라인 미사용
    assert ("MMR" in header) or ("Similarity" in header)


def test_flag_on_uses_pipeline():
    _clear_flags()
    os.environ["AE_RETRIEVAL_PIPELINE"] = "1"
    try:
        ctx = cb.build_context(PROJ, "reciprocal rank fusion", gateway_client=_GW())
        header = _header_label(ctx)
        assert "Pipeline(fusion=" in header  # 파이프라인 라벨
    finally:
        _clear_flags()


def test_flag_on_without_gateway_falls_back():
    """게이트웨이 없으면 파이프라인 미가동 → 기존 경로(무회귀)."""
    _clear_flags()
    os.environ["AE_RETRIEVAL_PIPELINE"] = "1"
    try:
        ctx = cb.build_context(PROJ, "reciprocal rank fusion", gateway_client=None)
        header = _header_label(ctx)
        assert header and "Pipeline(" not in header
    finally:
        _clear_flags()


def test_pipeline_failure_falls_back(monkeypatch):
    """파이프라인 내부 예외 시 기존 검색으로 안전 폴백."""
    _clear_flags()
    os.environ["AE_RETRIEVAL_PIPELINE"] = "1"

    def _boom(*a, **k):
        raise RuntimeError("pipeline down")

    import ai_engine.rag.retrieval_pipeline as rp
    monkeypatch.setattr(rp, "retrieve_evidence_sync", _boom)
    try:
        ctx = cb.build_context(PROJ, "hybrid search", gateway_client=_GW())
        # 폴백으로 여전히 관련 코드 섹션이 나와야 함
        header = _header_label(ctx)
        assert header and "Pipeline(" not in header
    finally:
        _clear_flags()
