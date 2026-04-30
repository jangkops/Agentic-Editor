"""
Pytest configuration and fixtures
"""

import pytest
import json
import os
from pathlib import Path
from unittest.mock import Mock, MagicMock, patch

# ========================================
# Fixtures
# ========================================


@pytest.fixture
def sample_project_dir(tmp_path):
    """임시 프로젝트 디렉터리 생성"""
    project_root = tmp_path / "sample_project"
    project_root.mkdir()

    # 파이썬 파일 생성
    (project_root / "main.py").write_text(
        """import os
import sys

def main():
    # TODO: 실제 구현 필요
    print("Hello")

if __name__ == "__main__":
    main()
"""
    )

    # requirements.txt
    (project_root / "requirements.txt").write_text("requests==2.28.0\nnumpy>=1.20\n")

    # package.json
    (project_root / "package.json").write_text(
        json.dumps(
            {
                "name": "test-project",
                "version": "1.0.0",
                "dependencies": {"express": "^4.18.0"},
                "devDependencies": {"jest": "^29.0.0"},
            }
        )
    )

    # .gitignore
    (project_root / ".gitignore").write_text("node_modules/\n__pycache__/\n")

    return str(project_root)


@pytest.fixture
def mock_bedrock_response():
    """Bedrock 응답 mock"""
    return {
        "content": [{"text": "This is a test response"}],
        "usage": {"inputTokens": 100, "outputTokens": 50},
    }


@pytest.fixture
def mock_aws_credentials():
    """AWS 자격증명 mock"""
    return {
        "accessKeyId": "AKIA...",
        "secretAccessKey": "secret",
        "sessionToken": "token",
        "expiration": "2025-05-01T12:00:00Z",
    }


@pytest.fixture
def mock_rag_documents():
    """RAG 문서 mock"""
    return [
        {
            "id": "doc_1",
            "content": "FastAPI는 최신 Python 웹 프레임워크입니다.",
            "metadata": {"source": "api_docs.md"},
        },
        {
            "id": "doc_2",
            "content": "Pydantic을 사용하여 데이터 검증을 합니다.",
            "metadata": {"source": "tutorial.md"},
        },
    ]


# ========================================
# Test Configuration
# ========================================


def pytest_configure(config):
    """Pytest 커스텀 config"""
    config.addinivalue_line(
        "markers", "unit: unit test (빠름)"
    )
    config.addinivalue_line(
        "markers", "integration: integration test (느림)"
    )


# ========================================
# Test Markers
# ========================================

pytestmark = [
    pytest.mark.unit,  # 기본값: unit test
]
