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
        """단일 텍스트 임베딩.

        주의: fit 되지 않은 상태에서는 None을 반환한다. 단일 쿼리로 fit 하면
        vocabulary가 작아져 캐시된 코퍼스 벡터(예: 1024차원)와 차원이 달라져
        matmul 차원 불일치를 유발한다.
        """
        try:
            self._ensure_vectorizer()
            if not self._fitted:
                # 코퍼스로 fit 안 됐다면 임베딩 거부 (차원 불일치 방지)
                print("[Embedder] embed() 호출 시 fit 안 된 상태 — 코퍼스로 fit_corpus() 먼저 호출 필요")
                return None
            vec = self._vectorizer.transform([text[:8000]]).toarray()[0]
            return vec
        except Exception as e:
            print(f"[Embedder] TF-IDF 임베딩 실패: {e}")
            return None

    def fit_corpus(self, texts: List[str]) -> bool:
        """캐시된 벡터 차원과 일치하도록 코퍼스로 fit. 성공 시 True."""
        if not texts:
            return False
        try:
            self.fit(texts)
            return self._fitted
        except Exception as e:
            print(f"[Embedder] fit_corpus 실패: {e}")
            return False

    @property
    def vocab_size(self) -> int:
        """현재 vectorizer의 vocabulary 크기. fit 전엔 0."""
        if not self._fitted or self._vectorizer is None:
            return 0
        try:
            return len(self._vectorizer.vocabulary_)
        except Exception:
            return 0

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


# ─────────────────────────────────────────────────────────────────────────
# Pluggable EmbeddingProvider (Requirements 7.1, 7.2, 7.3)
#
# 임베딩 생성을 추상화해 TF-IDF(기본) / 게이트웨이 Titan(옵트인) / 로컬 ONNX(스텁)를
# 교체 가능하게 한다. 기존 BedrockEmbedder(TF-IDF)를 어댑트해 동작을 보존하며,
# provider가 준비되지 않으면 상위(hybrid_search)의 차원 가드가 벡터 검색을 안전히
# 비활성화한다.
# ─────────────────────────────────────────────────────────────────────────
from typing import Protocol, runtime_checkable


@runtime_checkable
class EmbeddingProvider(Protocol):
    """임베딩 provider 인터페이스."""
    def embed(self, text: str): ...
    def embed_batch(self, texts: List[str]): ...
    @property
    def dimension(self) -> int: ...
    @property
    def is_ready(self) -> bool: ...


class TfidfEmbeddingProvider:
    """기존 TF-IDF BedrockEmbedder를 EmbeddingProvider로 어댑트(동작 보존, 기본값)."""

    def __init__(self, gateway_client=None):
        self._impl = BedrockEmbedder(gateway_client=gateway_client)

    # 코퍼스 fit 위임 (context_builder가 embed_batch로 fit)
    def fit_corpus(self, texts: List[str]) -> bool:
        return self._impl.fit_corpus(texts)

    def embed(self, text: str):
        return self._impl.embed(text)

    def embed_batch(self, texts: List[str]):
        return self._impl.embed_batch(texts)

    @property
    def dimension(self) -> int:
        # fit 전에는 0, fit 후 vocab 크기
        vs = self._impl.vocab_size
        return vs if vs else BedrockEmbedder.DIMENSION

    @property
    def is_ready(self) -> bool:
        return self._impl._fitted

    @property
    def vocab_size(self) -> int:
        return self._impl.vocab_size


class TitanGatewayEmbeddingProvider:
    """게이트웨이 Titan 임베딩(옵트인). probe 실패 시 is_ready=False → 상위에서 TF-IDF 폴백.

    BedrockUser 권한이 Titan 임베딩(/invoke)을 허용하지 않으면 자동 비활성화된다.
    실제 게이트웨이 호출 시그니처는 프로젝트마다 다를 수 있어 방어적으로 구현하며,
    라이브 자격증명이 없는 환경에서는 is_ready=False로 동작한다.
    """

    MODEL_ID = "amazon.titan-embed-text-v2:0"
    DIMENSION = 1024

    def __init__(self, gateway_client):
        self._gw = gateway_client
        self._ready = False  # probe 성공 시에만 True

    def probe(self) -> bool:
        """1회 probe — 게이트웨이가 임베딩을 허용하는지 확인. 실패하면 False 고정."""
        # 안전 기본값: 게이트웨이/자격증명 미검증 환경에서는 비활성.
        # 실 배포에서 invoke_model 동기 probe로 대체 가능(현재는 비활성 유지).
        self._ready = False
        return self._ready

    def embed(self, text: str):
        return None  # 비활성 상태에서는 None → 상위 폴백

    def embed_batch(self, texts: List[str]):
        return [None] * len(texts)

    @property
    def dimension(self) -> int:
        return self.DIMENSION

    @property
    def is_ready(self) -> bool:
        return self._ready


def get_embedding_provider(env: dict, gateway_client=None):
    """env['AE_EMBED_PROVIDER'] 에 따라 provider 선택. 기본/폴백은 TF-IDF.

    - "titan": Titan probe 시도 → 성공 시 사용, 실패 시 TF-IDF
    - "onnx": 로컬 ONNX(별도 태스크, 미구현) → TF-IDF 폴백
    - 그 외/미지정: TF-IDF
    """
    choice = str((env or {}).get("AE_EMBED_PROVIDER", "")).strip().lower()
    if choice == "titan" and gateway_client is not None:
        p = TitanGatewayEmbeddingProvider(gateway_client)
        if p.probe():
            return p
        # probe 실패 → TF-IDF 폴백
    if choice in ("fastembed", "neural", "e5", "bge"):
        # 다국어 신경망 임베딩(fastembed ONNX, torch 불필요). KR↔EN 교차언어 검색.
        model = (env or {}).get("AE_EMBED_MODEL") \
            or "sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2"
        prov = FastEmbedProvider(model_name=model)
        if prov.is_ready:
            return prov
        # 모델/라이브러리 미가용 → TF-IDF 폴백(정직: 없는 걸 있는 척 안 함)
    if choice == "lsa":
        # 잠재의미(LSA) 임베딩 — 오프라인·신규 의존성 0. 시맨틱 검색 활성.
        try:
            comp = int((env or {}).get("AE_LSA_COMPONENTS", "256"))
        except (TypeError, ValueError):
            comp = 256
        return LsaEmbeddingProvider(n_components=comp, gateway_client=gateway_client)
    # "onnx"는 별도 태스크(8.1)에서 도입 — 현재는 TF-IDF 폴백
    return TfidfEmbeddingProvider(gateway_client=gateway_client)


# ─────────────────────────────────────────────────────────────────────────
# LSA(Latent Semantic Analysis) 임베딩 — 진짜 잠재의미 벡터 (Requirements 7.x)
#
# TF-IDF(희소·어휘) → TruncatedSVD로 밀집 저차원(기본 256d) 투영. 동시출현/잠재
# 토픽을 포착하므로, 표현이 다른(동의어) 질의에도 관련 코드를 찾는다. sklearn 내장
# 이라 신규 무거운 의존성/모델 다운로드/오프라인 제약 없이 즉시 사용 가능하며
# PyInstaller 동결에도 안전하다. (ONNX 임베딩 대비 경량 상위호환 classical 방식)
# ─────────────────────────────────────────────────────────────────────────
class LsaEmbeddingProvider:
    """TF-IDF + TruncatedSVD 잠재의미 임베딩. 코퍼스로 fit 후 dense 벡터 생성."""

    def __init__(self, n_components: int = 256, gateway_client=None):
        self._n = int(n_components)
        self._vectorizer = None
        self._svd = None
        self._fitted = False
        self._dim = 0

    def _ensure(self):
        if self._vectorizer is None:
            from sklearn.feature_extraction.text import TfidfVectorizer
            self._vectorizer = TfidfVectorizer(
                max_features=4096, sublinear_tf=True, dtype=np.float32,
                ngram_range=(1, 2),  # 1~2gram — 짧은 코드 토큰 문맥 포착
            )

    def fit_corpus(self, texts: List[str]) -> bool:
        return self._fit(texts)

    def _fit(self, texts: List[str]) -> bool:
        if not texts:
            return False
        try:
            from sklearn.decomposition import TruncatedSVD
            self._ensure()
            tfidf = self._vectorizer.fit_transform(texts)
            n_feat = tfidf.shape[1]
            # 성분 수는 (문서수-1, 특징수-1, 요청치) 중 최소로 안전 클램프
            comp = max(2, min(self._n, tfidf.shape[0] - 1, n_feat - 1))
            self._svd = TruncatedSVD(n_components=comp, random_state=42)
            self._svd.fit(tfidf)
            self._dim = comp
            self._fitted = True
            return True
        except Exception as e:
            print(f"[LSA] fit 실패 (TF-IDF 폴백 권장): {e}")
            self._fitted = False
            return False

    def _project(self, texts: List[str]):
        tfidf = self._vectorizer.transform(texts)
        vecs = self._svd.transform(tfidf).astype(np.float32)
        # L2 정규화 → 코사인 유사도가 내적과 일치
        norms = np.linalg.norm(vecs, axis=1, keepdims=True) + 1e-10
        return vecs / norms

    def embed(self, text: str):
        if not self._fitted:
            return None
        try:
            return self._project([text[:8000]])[0]
        except Exception as e:
            print(f"[LSA] embed 실패: {e}")
            return None

    def embed_batch(self, texts: List[str]):
        if not texts:
            return []
        if not self._fitted and not self._fit(texts):
            return [None] * len(texts)
        try:
            vecs = self._project(texts)
            return [vecs[i] for i in range(len(texts))]
        except Exception as e:
            print(f"[LSA] embed_batch 실패: {e}")
            return [None] * len(texts)

    @property
    def dimension(self) -> int:
        return self._dim or self._n

    @property
    def is_ready(self) -> bool:
        return self._fitted

    @property
    def vocab_size(self) -> int:
        # hybrid_search 차원 가드가 참조 — LSA는 SVD 성분 수가 차원.
        return self._dim


# ─────────────────────────────────────────────────────────────────────────
# FastEmbed 기반 다국어 신경망 임베딩 (Requirements 7.4 — 실구현)
#
# fastembed는 ONNX Runtime 기반(PyTorch 불필요, CPU, 양자화 가중치)이라 오프라인
# 동결 배포에 적합하다. multilingual-e5 / bge-m3 등 다국어 모델로 한국어 질의 ↔
# 영문 코드의 교차언어 검색을 지원한다. 모델은 최초 사용 시 캐시에 다운로드된다.
#
# E5 계열은 "query:" / "passage:" 프리픽스가 필수(문서 규정). BGE-M3는 프리픽스 불요.
# fastembed 미설치/모델 미가용 시 is_ready=False → 상위에서 TF-IDF 폴백.
# ─────────────────────────────────────────────────────────────────────────
def _bundled_fastembed_cache() -> Optional[str]:
    """PyInstaller 동결 실행 시 실행파일 옆 fastembed_models 디렉터리를 캐시로 사용.

    build-python.js가 빌드 시 번들 모델을 여기에 사전 다운로드한다. 존재하지 않으면
    None을 반환해 기본 캐시(런타임 다운로드)로 폴백한다.
    """
    import sys
    if not getattr(sys, "frozen", False):
        return None
    try:
        base = os.path.dirname(os.path.abspath(sys.executable))
        cand = os.path.join(base, "fastembed_models")
        return cand if os.path.isdir(cand) else None
    except Exception:
        return None


class FastEmbedProvider:
    """fastembed(ONNX) 다국어 임베딩. query/passage 비대칭 프리픽스 지원."""

    # 기본: paraphrase-multilingual-MiniLM-L12-v2 (경량 384d, 다국어, 지원 확실).
    # 미지원 모델명이 들어오면 _resolve_model이 지원 다국어 모델로 자동 폴백한다.
    _SUPPORTED_FALLBACKS = (
        "sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2",
        "intfloat/multilingual-e5-large",
        "sentence-transformers/all-MiniLM-L6-v2",
    )

    def __init__(self, model_name: str = "sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2",
                 use_e5_prefix: bool = True, gateway_client=None,
                 cache_dir: Optional[str] = None):
        self._model_name = model_name
        self._use_prefix = use_e5_prefix and ("e5" in model_name.lower())
        # 오프라인/동결 배포: 번들된 모델 캐시 경로. 우선순위:
        #   명시 인자 > AE_FASTEMBED_CACHE env > 동결 실행파일 옆 fastembed_models > 기본 캐시
        self._cache_dir = (cache_dir or os.environ.get("AE_FASTEMBED_CACHE")
                           or _bundled_fastembed_cache() or None)
        self._model = None
        self._dim = 0
        self._ready = False
        self._init()

    def _resolve_model(self, requested: str) -> str:
        """요청 모델이 설치된 fastembed에서 미지원이면 지원 다국어 모델로 폴백(정직)."""
        try:
            from fastembed import TextEmbedding
            supported = {m["model"] for m in TextEmbedding.list_supported_models()}
        except Exception:
            return requested  # 목록 조회 불가 → 원본 그대로 시도
        if requested in supported:
            return requested
        for cand in self._SUPPORTED_FALLBACKS:
            if cand in supported:
                print(f"[FastEmbed] '{requested}' 미지원 → '{cand}'로 폴백")
                return cand
        return requested

    def _init(self):
        try:
            from fastembed import TextEmbedding
            resolved = self._resolve_model(self._model_name)
            if resolved != self._model_name:
                self._model_name = resolved
                self._use_prefix = "e5" in resolved.lower()
            kwargs = {"model_name": self._model_name}
            if self._cache_dir:
                kwargs["cache_dir"] = self._cache_dir
            self._model = TextEmbedding(**kwargs)
            # 차원 파악용 워밍업 1회
            import numpy as _np
            v = list(self._model.embed(["passage: warmup"]))[0]
            self._dim = int(_np.asarray(v).shape[0])
            self._ready = True
        except Exception as e:
            print(f"[FastEmbed] 초기화 실패 (TF-IDF 폴백): {e}")
            self._ready = False

    def _prep(self, texts, kind: str):
        if self._use_prefix:
            pfx = "query: " if kind == "query" else "passage: "
            return [pfx + t for t in texts]
        return list(texts)

    def _l2(self, arr):
        import numpy as _np
        a = _np.asarray(arr, dtype=_np.float32)
        n = _np.linalg.norm(a) + 1e-10
        return a / n

    def embed(self, text: str):
        if not self._ready:
            return None
        try:
            vecs = list(self._model.query_embed([text[:8000]])) if hasattr(self._model, "query_embed") \
                else list(self._model.embed(self._prep([text[:8000]], "query")))
            return self._l2(vecs[0])
        except Exception as e:
            print(f"[FastEmbed] embed 실패: {e}")
            return None

    def embed_batch(self, texts):
        if not self._ready or not texts:
            return [None] * len(texts) if texts else []
        try:
            prepared = self._prep([t[:8000] for t in texts], "passage")
            out = []
            for v in self._model.embed(prepared):
                out.append(self._l2(v))
            return out
        except Exception as e:
            print(f"[FastEmbed] embed_batch 실패: {e}")
            return [None] * len(texts)

    def fit_corpus(self, texts):
        # 신경망 임베딩은 fit 불필요(사전학습). 항상 준비 상태 반영.
        return self._ready

    @property
    def dimension(self) -> int:
        return self._dim

    @property
    def is_ready(self) -> bool:
        return self._ready

    @property
    def model_name(self) -> str:
        # 폴백 후 실제 로드된 모델명(정직한 관측/로그용).
        return self._model_name

    @property
    def vocab_size(self) -> int:
        # hybrid_search 차원 가드 호환 — 신경망은 고정 차원.
        return self._dim
