"""하이브리드 검색 — 벡터 유사도 + BM25 키워드 점수 결합.

점수 = alpha * vector_score + (1 - alpha) * bm25_score
alpha = 0.6 (의미 검색 60%, 키워드 40%)
"""
import math
import re
from typing import List, Tuple, Dict
from ai_engine.rag.indexer import Chunk


class BM25:
    """BM25 키워드 검색."""

    def __init__(self, k1: float = 1.5, b: float = 0.75):
        self.k1 = k1
        self.b = b
        self.doc_freqs: Dict[str, int] = {}
        self.doc_lens: List[int] = []
        self.avg_dl: float = 0
        self.n_docs: int = 0
        self.doc_tokens: List[List[str]] = []

    def index(self, chunks: List[Chunk]):
        """청크 목록으로 BM25 인덱스 구축."""
        self.n_docs = len(chunks)
        self.doc_tokens = [c.tokens for c in chunks]
        self.doc_lens = [len(t) for t in self.doc_tokens]
        self.avg_dl = sum(self.doc_lens) / max(self.n_docs, 1)
        self.doc_freqs = {}
        for tokens in self.doc_tokens:
            seen = set(tokens)
            for t in seen:
                self.doc_freqs[t] = self.doc_freqs.get(t, 0) + 1

    def score(self, query_tokens: List[str], doc_idx: int) -> float:
        """단일 문서의 BM25 점수."""
        doc_tokens = self.doc_tokens[doc_idx]
        dl = self.doc_lens[doc_idx]
        score = 0.0
        tf_map = {}
        for t in doc_tokens:
            tf_map[t] = tf_map.get(t, 0) + 1
        for qt in query_tokens:
            if qt not in tf_map:
                continue
            tf = tf_map[qt]
            df = self.doc_freqs.get(qt, 0)
            idf = math.log((self.n_docs - df + 0.5) / (df + 0.5) + 1)
            tf_norm = (tf * (self.k1 + 1)) / (tf + self.k1 * (1 - self.b + self.b * dl / self.avg_dl))
            score += idf * tf_norm
        return score

    def search(self, query_tokens: List[str], top_k: int = 10) -> List[Tuple[int, float]]:
        """상위 K개 문서 인덱스 + 점수."""
        scores = [(i, self.score(query_tokens, i)) for i in range(self.n_docs)]
        scores.sort(key=lambda x: -x[1])
        return [(i, s) for i, s in scores[:top_k] if s > 0]


def tokenize(text: str) -> List[str]:
    """텍스트를 토큰으로 분할."""
    return re.findall(r'[a-z_][a-z0-9_]*|[가-힣]+', text.lower())


class HybridSearcher:
    """벡터 + BM25 하이브리드 검색."""

    def __init__(self, alpha: float = 0.6):
        """alpha: 벡터 점수 가중치 (0~1). 1-alpha가 BM25 가중치."""
        self.alpha = alpha
        self.bm25 = BM25()
        self.chunks: List[Chunk] = []
        self.vector_store = None  # VectorStore 인스턴스
        self._embedder = None

    def set_embedder(self, embedder):
        self._embedder = embedder

    def set_vector_store(self, store):
        self.vector_store = store

    def index(self, chunks: List[Chunk]):
        """청크 목록으로 BM25 인덱스 구축."""
        self.chunks = chunks
        self.bm25.index(chunks)

    def search(self, query: str, top_k: int = 8) -> List[Tuple[Chunk, float]]:
        """하이브리드 검색."""
        if not self.chunks:
            return []

        query_tokens = tokenize(query)
        scores: Dict[int, float] = {}

        # 1. BM25 검색
        bm25_results = self.bm25.search(query_tokens, top_k=top_k * 2)
        if bm25_results:
            max_bm25 = max(s for _, s in bm25_results) or 1
            for idx, s in bm25_results:
                scores[idx] = scores.get(idx, 0) + (1 - self.alpha) * (s / max_bm25)

        # 2. 벡터 검색 (임베더가 있을 때만)
        if self.vector_store and self._embedder and self.vector_store.size > 0:
            query_vec = self._embedder.embed(query)
            if query_vec is not None:
                vec_results = self.vector_store.search(query_vec, top_k=top_k * 2)
                for meta, s in vec_results:
                    idx = meta.get("chunk_idx", -1)
                    if 0 <= idx < len(self.chunks):
                        scores[idx] = scores.get(idx, 0) + self.alpha * s
        elif not self.vector_store:
            # 벡터 없으면 BM25만 사용 (alpha 무시)
            scores = {}
            for idx, s in bm25_results:
                max_bm25 = max(s2 for _, s2 in bm25_results) or 1
                scores[idx] = s / max_bm25

        # 정렬
        ranked = sorted(scores.items(), key=lambda x: -x[1])
        return [(self.chunks[idx], score) for idx, score in ranked[:top_k] if score > 0.05]
