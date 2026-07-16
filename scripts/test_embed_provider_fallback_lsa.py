"""임베딩 provider 폴백 체인 회귀 테스트 — fastembed 미가용 시 LSA(의미) 폴백.

배경: 동결(DMG) 환경에서 fastembed(ONNX)가 로드 실패하면, 과거엔 약한 어휘 TF-IDF 로
조용히 폴백해 RAG 품질이 저하됐다. 이제 fastembed 미가용 시 LSA(잠재의미, sklearn only,
오프라인·동결안전)로 폴백해 의미검색을 유지한다.

검증:
- fastembed 요청 + 가용 → FastEmbedProvider 사용.
- fastembed 요청 + 미가용 → LsaEmbeddingProvider 폴백(어휘 TF-IDF 아님).
- AE_EMBED_FALLBACK=tfidf 명시 시 → TfidfEmbeddingProvider 폴백(옵트아웃 존중).
- 명시적 tfidf 선택 → TfidfEmbeddingProvider(기존 동작 보존).
- LSA 는 코퍼스 fit 후 dense 의미벡터 생성(차원>0).

네트워크/모델 다운로드 불필요 — FastEmbedProvider 를 미가용 스텁으로 대체.
실행: ai_engine/.venv/bin/python -m pytest scripts/test_embed_provider_fallback_lsa.py -q
"""
from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import ai_engine.rag.embedder as emb
from ai_engine.rag.embedder import (
    get_embedding_provider,
    LsaEmbeddingProvider,
    TfidfEmbeddingProvider,
    FastEmbedProvider,
)


class _UnavailableFastEmbed:
    """fastembed 로드 실패(동결 DMG ONNX 실패)를 흉내내는 스텁 — is_ready=False."""

    def __init__(self, *a, **k):
        self._ready = False

    @property
    def is_ready(self):
        return False


def _with_stub_fastembed(fn):
    _orig = emb.FastEmbedProvider
    emb.FastEmbedProvider = _UnavailableFastEmbed
    try:
        return fn()
    finally:
        emb.FastEmbedProvider = _orig


def test_fastembed_unavailable_falls_back_to_lsa():
    prov = _with_stub_fastembed(
        lambda: get_embedding_provider({"AE_EMBED_PROVIDER": "fastembed"})
    )
    assert isinstance(prov, LsaEmbeddingProvider), f"LSA 폴백 기대, got {type(prov).__name__}"


def test_fastembed_unavailable_respects_tfidf_optout():
    prov = _with_stub_fastembed(
        lambda: get_embedding_provider(
            {"AE_EMBED_PROVIDER": "fastembed", "AE_EMBED_FALLBACK": "tfidf"}
        )
    )
    assert isinstance(prov, TfidfEmbeddingProvider), "명시적 tfidf 폴백 존중"


def test_explicit_tfidf_choice_preserved():
    prov = get_embedding_provider({"AE_EMBED_PROVIDER": "tfidf"})
    assert isinstance(prov, TfidfEmbeddingProvider)


def test_explicit_lsa_choice():
    prov = get_embedding_provider({"AE_EMBED_PROVIDER": "lsa", "AE_LSA_COMPONENTS": "32"})
    assert isinstance(prov, LsaEmbeddingProvider)


def test_lsa_produces_semantic_vectors():
    corpus = [
        "def factorial(n): return 1 if n<=1 else n*factorial(n-1)",
        "REST API uses HTTP verbs for resources",
        "GraphQL provides a single endpoint query language",
        "파이썬 재귀 함수로 팩토리얼을 계산한다",
        "벡터 데이터베이스는 코사인 유사도로 검색한다",
    ]
    p = LsaEmbeddingProvider(n_components=32)
    assert p.fit_corpus(corpus) is True
    assert p.is_ready is True
    assert p.dimension > 0
    v = p.embed("재귀 함수 팩토리얼 파이썬")
    assert v is not None and len(v) == p.dimension


if __name__ == "__main__":
    import pytest

    raise SystemExit(pytest.main([__file__, "-q"]))
