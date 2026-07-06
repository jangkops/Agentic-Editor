"""EmbeddingProvider 어댑터 무회귀 + 팩토리 폴백 테스트 (Req 7.2, 7.3, 7.5).

실행: ./ai_engine/.venv/bin/python -m pytest scripts/test_embedding_provider_pbt.py -p no:cacheprovider -q
"""
import numpy as np
from ai_engine.rag.embedder import (
    BedrockEmbedder, TfidfEmbeddingProvider, TitanGatewayEmbeddingProvider,
    get_embedding_provider,
)

CORPUS = [
    "def add(a, b): return a + b",
    "class User: def __init__(self): pass",
    "import os\nprint(os.getcwd())",
    "async function fetchData(url) { return await fetch(url); }",
]


def test_tfidf_adapter_matches_raw_embedder():
    """어댑터 경유 임베딩이 원 BedrockEmbedder와 동일(무회귀)."""
    raw = BedrockEmbedder()
    raw_vecs = raw.embed_batch(CORPUS)

    prov = TfidfEmbeddingProvider()
    prov_vecs = prov.embed_batch(CORPUS)

    assert len(raw_vecs) == len(prov_vecs)
    for a, b in zip(raw_vecs, prov_vecs):
        assert (a is None) == (b is None)
        if a is not None:
            assert np.allclose(a, b)


def test_adapter_ready_and_dimension():
    prov = TfidfEmbeddingProvider()
    assert prov.is_ready is False  # fit 전
    prov.embed_batch(CORPUS)       # fit 수행
    assert prov.is_ready is True
    assert prov.dimension > 0
    # 단일 임베딩 차원이 dimension과 일치
    v = prov.embed(CORPUS[0])
    assert v is not None and v.shape[0] == prov.dimension


def test_factory_defaults_to_tfidf():
    prov = get_embedding_provider({}, gateway_client=None)
    assert isinstance(prov, TfidfEmbeddingProvider)


def test_factory_titan_falls_back_when_not_ready():
    """titan 선택이라도 probe 실패(자격증명/권한 없음) 시 TF-IDF 폴백."""
    class _GW: pass
    prov = get_embedding_provider({"AE_EMBED_PROVIDER": "titan"}, gateway_client=_GW())
    assert isinstance(prov, TfidfEmbeddingProvider)  # 폴백


def test_titan_provider_disabled_returns_none():
    class _GW: pass
    p = TitanGatewayEmbeddingProvider(_GW())
    assert p.is_ready is False
    assert p.embed("x") is None
    assert p.embed_batch(["a", "b"]) == [None, None]


def test_factory_onnx_falls_back():
    prov = get_embedding_provider({"AE_EMBED_PROVIDER": "onnx"}, gateway_client=None)
    assert isinstance(prov, TfidfEmbeddingProvider)
