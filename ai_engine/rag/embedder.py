"""로컬 TF-IDF 임베딩 — 외부 API 호출 없이 벡터 검색.

BedrockUser는 Gateway /converse만 호출 가능하므로,
임베딩은 scikit-learn TfidfVectorizer로 로컬 처리.
"""
import json
import os
import numpy as np
from typing import List, Optional, Tuple


class BedrockEmbedder:
    """TF-IDF 기반 로컬 임베딩 — API 호출 없음."""

    DIMENSION = 1024  # TF-IDF max_features

    def __init__(self, gateway_client=None):
        self._gw = gateway_client
        self._vectorizer = None
        self._fitted = False
        self._corpus = []

    def _ensure_vectorizer(self):
        if self._vectorizer is None:
            from sklearn.feature_extraction.text import TfidfVectorizer
            self._vectorizer = TfidfVectorizer(
                max_features=self.DIMENSION,
                sublinear_tf=True,
                dtype=np.float32,
            )

    def fit(self, texts: List[str]):
        """코퍼스로 TF-IDF 학습."""
        self._ensure_vectorizer()
        self._corpus = texts
        if texts:
            self._vectorizer.fit(texts)
            self._fitted = True

    def embed(self, text: str) -> Optional[np.ndarray]:
        """단일 텍스트 임베딩."""
        try:
            self._ensure_vectorizer()
            if not self._fitted:
                # 아직 fit 안 됨 — 단일 텍스트로 fit
                self._vectorizer.fit([text])
                self._fitted = True
            vec = self._vectorizer.transform([text[:8000]]).toarray()[0]
            return vec
        except Exception as e:
            print(f"[Embedder] TF-IDF 임베딩 실패: {e}")
            return None

    def embed_batch(self, texts: List[str], batch_size: int = 50) -> List[Optional[np.ndarray]]:
        """배치 임베딩 — 먼저 전체 코퍼스로 fit 후 transform."""
        self._ensure_vectorizer()
        if not texts:
            return []
        try:
            # 전체 텍스트로 fit
            self.fit(texts)
            # 한번에 transform
            matrix = self._vectorizer.transform(texts).toarray()
            return [matrix[i] for i in range(len(texts))]
        except Exception as e:
            print(f"[Embedder] TF-IDF 배치 임베딩 실패: {e}")
            return [None] * len(texts)


class VectorStore:
    """로컬 numpy 기반 벡터 저장소."""

    def __init__(self, dimension: int = 1024):
        self.dimension = dimension
        self.vectors: Optional[np.ndarray] = None
        self.metadata: List[dict] = []
        self._cache_path: Optional[str] = None

    def add(self, vector: np.ndarray, meta: dict):
        if self.vectors is None:
            self.vectors = vector.reshape(1, -1)
        else:
            self.vectors = np.vstack([self.vectors, vector.reshape(1, -1)])
        self.metadata.append(meta)

    def search(self, query_vec: np.ndarray, top_k: int = 5) -> List[Tuple[dict, float]]:
        if self.vectors is None or len(self.metadata) == 0:
            return []
        q_norm = query_vec / (np.linalg.norm(query_vec) + 1e-10)
        v_norms = self.vectors / (np.linalg.norm(self.vectors, axis=1, keepdims=True) + 1e-10)
        scores = v_norms @ q_norm
        top_indices = np.argsort(scores)[::-1][:top_k]
        return [(self.metadata[i], float(scores[i])) for i in top_indices if scores[i] > 0.1]

    def save(self, path: str):
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path + ".meta.json", "w") as f:
            json.dump({"metadata": self.metadata}, f)
        if self.vectors is not None:
            np.save(path + ".npy", self.vectors)

    def load(self, path: str) -> bool:
        meta_path = path + ".meta.json"
        vec_path = path + ".npy"
        if not os.path.exists(meta_path) or not os.path.exists(vec_path):
            return False
        try:
            with open(meta_path) as f:
                data = json.load(f)
            self.metadata = data["metadata"]
            self.vectors = np.load(vec_path)
            return True
        except Exception:
            return False

    @property
    def size(self) -> int:
        return len(self.metadata)
