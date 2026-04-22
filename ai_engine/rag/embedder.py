"""Bedrock Titan 임베딩 + 로컬 벡터 저장소.

amazon.titan-embed-text-v2:0 사용.
numpy로 코사인 유사도 계산 — 외부 벡터 DB 불필요.
"""
import json
import os
import hashlib
import numpy as np
from typing import List, Optional, Tuple


class BedrockEmbedder:
    """Bedrock Titan 임베딩 클라이언트."""

    MODEL_ID = "amazon.titan-embed-text-v2:0"
    DIMENSION = 1024  # Titan v2 기본 차원

    def __init__(self, aws_profile: str = "default", region: str = "us-west-2",
                 bedrock_user: str = ""):
        self.aws_profile = aws_profile
        self.region = region
        self.bedrock_user = bedrock_user
        self._client = None

    def _get_client(self):
        if self._client:
            return self._client
        import boto3
        session = boto3.Session(
            profile_name=self.aws_profile,
            region_name=self.region,
        )
        self._client = session.client("bedrock-runtime")
        return self._client

    def embed(self, text: str) -> Optional[np.ndarray]:
        """단일 텍스트 임베딩. 실패 시 None."""
        try:
            client = self._get_client()
            body = json.dumps({
                "inputText": text[:8000],  # Titan 최대 8K 토큰
                "dimensions": self.DIMENSION,
            })
            resp = client.invoke_model(
                modelId=self.MODEL_ID,
                body=body,
                contentType="application/json",
                accept="application/json",
            )
            result = json.loads(resp["body"].read())
            return np.array(result["embedding"], dtype=np.float32)
        except Exception as e:
            print(f"[Embedder] 임베딩 실패: {e}")
            return None

    def embed_batch(self, texts: List[str], batch_size: int = 5) -> List[Optional[np.ndarray]]:
        """배치 임베딩. rate limit 대응으로 순차 처리."""
        results = []
        for i in range(0, len(texts), batch_size):
            batch = texts[i:i + batch_size]
            for text in batch:
                results.append(self.embed(text))
        return results


class VectorStore:
    """로컬 numpy 기반 벡터 저장소."""

    def __init__(self, dimension: int = 1024):
        self.dimension = dimension
        self.vectors: Optional[np.ndarray] = None  # (N, D) 행렬
        self.metadata: List[dict] = []  # 각 벡터의 메타데이터
        self._cache_path: Optional[str] = None

    def add(self, vector: np.ndarray, meta: dict):
        """벡터 + 메타데이터 추가."""
        if self.vectors is None:
            self.vectors = vector.reshape(1, -1)
        else:
            self.vectors = np.vstack([self.vectors, vector.reshape(1, -1)])
        self.metadata.append(meta)

    def search(self, query_vec: np.ndarray, top_k: int = 5) -> List[Tuple[dict, float]]:
        """코사인 유사도 검색."""
        if self.vectors is None or len(self.metadata) == 0:
            return []
        # 정규화
        q_norm = query_vec / (np.linalg.norm(query_vec) + 1e-10)
        v_norms = self.vectors / (np.linalg.norm(self.vectors, axis=1, keepdims=True) + 1e-10)
        # 코사인 유사도
        scores = v_norms @ q_norm
        # 상위 K개
        top_indices = np.argsort(scores)[::-1][:top_k]
        return [(self.metadata[i], float(scores[i])) for i in top_indices if scores[i] > 0.1]

    def save(self, path: str):
        """디스크에 저장."""
        os.makedirs(os.path.dirname(path), exist_ok=True)
        data = {
            "metadata": self.metadata,
        }
        with open(path + ".meta.json", "w") as f:
            json.dump(data, f)
        if self.vectors is not None:
            np.save(path + ".npy", self.vectors)

    def load(self, path: str) -> bool:
        """디스크에서 로드. 성공 시 True."""
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
