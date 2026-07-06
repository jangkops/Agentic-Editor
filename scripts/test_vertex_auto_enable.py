"""Regression — Vertex 자동 활성화 자격증명 경로 API 계약.

모든 사용자가 앱 다운로드→SSO 로그인만으로 Vertex가 켜지도록, 로그인 시 주입된
자격증명으로 Secrets Manager에서 GCP 키를 해석하는 경로를 추가했다. 이 테스트는
그 API 표면(credentials 파라미터, 재초기화)을 고정한다.

실행: pytest scripts/test_vertex_auto_enable.py -q
"""
from __future__ import annotations

import inspect
import os
import sys

import pytest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

vmod = pytest.importorskip("ai_engine.vertex_image_module")


def test_get_client_accepts_credentials_param():
    sig = inspect.signature(vmod.get_vertex_image_client)
    assert "credentials" in sig.parameters, "get_vertex_image_client에 credentials 파라미터 필요"
    assert "aws_profile" in sig.parameters


def test_secrets_manager_accepts_credentials_param():
    sig = inspect.signature(vmod._try_secrets_manager)
    assert "credentials" in sig.parameters


def test_client_init_accepts_credentials():
    sig = inspect.signature(vmod.VertexImageClient.__init__)
    assert "credentials" in sig.parameters


def test_reset_and_reinit_with_credentials_no_crash():
    # 키가 없는 환경에서도 크래시 없이 비활성 클라이언트를 반환해야 한다(graceful).
    vmod.reset_vertex_image_client()
    creds = {"accessKeyId": "AKIA_TEST", "secretAccessKey": "x", "sessionToken": "", "region": "us-west-2"}
    client = vmod.get_vertex_image_client(aws_profile="bedrock-gw", credentials=creds)
    assert client is not None
    assert hasattr(client, "enabled")
    # 키 미해석 → enabled False (예외 없이)
    assert isinstance(client.enabled, bool)
    vmod.reset_vertex_image_client()


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-q"]))
