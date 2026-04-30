"""
Test: RAG Service
하이브리드 검색 (벡터 + BM25) 테스트
"""

import pytest
from unittest.mock import patch, MagicMock
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "ai_engine"))


class TestRagService:
    """RAG 서비스 통합 테스트"""

    @pytest.mark.unit
    def test_rag_service_initialization(self, mock_rag_documents):
        """RAG 서비스 초기화 테스트"""
        try:
            from rag_service import RagService

            rag = RagService()
            assert rag is not None
        except ImportError:
            pytest.skip("RagService not found")

    @pytest.mark.unit
    def test_vector_embedding_generation(self, mock_rag_documents):
        """벡터 임베딩 생성 테스트"""
        try:
            from rag_service import RagService

            rag = RagService()
            # 문서 텍스트에서 벡터 생성 시도
            embedding = rag.embed_text(mock_rag_documents[0]["content"])

            assert embedding is not None
            assert isinstance(embedding, (list, dict))  # 벡터 형식

        except ImportError:
            pytest.skip("RagService not found")

    @pytest.mark.unit
    def test_bm25_search(self, mock_rag_documents):
        """BM25 검색 테스트"""
        try:
            from rag_service import RagService

            rag = RagService()
            # 문서 색인
            rag.index_documents(mock_rag_documents)

            # BM25 검색
            results = rag.search_bm25("FastAPI", top_k=2)
            assert results is not None
            assert len(results) <= 2

        except ImportError:
            pytest.skip("RagService not found")

    @pytest.mark.unit
    def test_vector_search(self, mock_rag_documents):
        """벡터 검색 테스트"""
        try:
            from rag_service import RagService

            rag = RagService()
            rag.index_documents(mock_rag_documents)

            # 벡터 검색
            results = rag.search_vector("FastAPI", top_k=2)
            assert results is not None
            assert len(results) <= 2

        except ImportError:
            pytest.skip("RagService not found")

    @pytest.mark.unit
    def test_hybrid_search_reranking(self, mock_rag_documents):
        """하이브리드 검색 및 재순위 테스트"""
        try:
            from rag_service import RagService

            rag = RagService()
            rag.index_documents(mock_rag_documents)

            # 하이브리드 검색 (벡터 60% + BM25 40%)
            results = rag.search_hybrid("FastAPI", top_k=2, vector_weight=0.6, bm25_weight=0.4)

            assert results is not None
            assert len(results) <= 2
            # 각 결과에 스코어 있는지 확인
            if results:
                assert "score" in results[0]

        except ImportError:
            pytest.skip("RagService not found")

    @pytest.mark.unit
    def test_search_with_filters(self, mock_rag_documents):
        """필터가 있는 검색 테스트"""
        try:
            from rag_service import RagService

            rag = RagService()
            rag.index_documents(mock_rag_documents)

            # 메타데이터 필터로 검색
            results = rag.search_hybrid(
                "FastAPI",
                filters={"source": "api_docs.md"},
                top_k=2
            )

            assert results is not None
            if results:
                for result in results:
                    assert result.get("metadata", {}).get("source") == "api_docs.md"

        except ImportError:
            pytest.skip("RagService not found")

    @pytest.mark.unit
    def test_document_indexing(self, mock_rag_documents):
        """문서 색인 테스트"""
        try:
            from rag_service import RagService

            rag = RagService()
            result = rag.index_documents(mock_rag_documents)

            assert result is True
            # 색인된 문서 수 확인
            assert rag.get_document_count() == len(mock_rag_documents)

        except ImportError:
            pytest.skip("RagService not found")

    @pytest.mark.unit
    def test_document_update(self, mock_rag_documents):
        """문서 업데이트 테스트"""
        try:
            from rag_service import RagService

            rag = RagService()
            rag.index_documents(mock_rag_documents)

            # 첫 문서 업데이트
            updated_doc = mock_rag_documents[0].copy()
            updated_doc["content"] = "Updated content"
            result = rag.update_document(updated_doc["id"], updated_doc)

            assert result is True

        except ImportError:
            pytest.skip("RagService not found")

    @pytest.mark.unit
    def test_rag_cache_effectiveness(self, mock_rag_documents):
        """RAG 캐시 효율성 테스트"""
        try:
            from rag_service import RagService

            rag = RagService()
            rag.index_documents(mock_rag_documents)

            # 첫 번째 검색
            results1 = rag.search_hybrid("FastAPI", top_k=2)

            # 두 번째 검색 (캐시 히트 기대)
            results2 = rag.search_hybrid("FastAPI", top_k=2)

            # 같은 결과인지 확인
            assert results1 == results2

        except ImportError:
            pytest.skip("RagService not found")

    @pytest.mark.integration
    def test_large_document_indexing_performance(self):
        """대량 문서 색인 성능 테스트"""
        try:
            from rag_service import RagService
            import time

            rag = RagService()

            # 1000개 문서 생성
            large_documents = [
                {
                    "id": f"doc_{i}",
                    "content": f"Document {i} content with keywords related to data processing",
                    "metadata": {"source": f"file_{i}.md"},
                }
                for i in range(1000)
            ]

            start_time = time.time()
            rag.index_documents(large_documents)
            elapsed = time.time() - start_time

            # 1000개 문서를 30초 내에 색인 (성능 기준)
            assert elapsed < 30.0
            assert rag.get_document_count() == 1000

        except ImportError:
            pytest.skip("RagService not found")


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
