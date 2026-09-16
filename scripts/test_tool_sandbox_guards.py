"""도구 실행 경계 (원장 #8·#16·#17).

- run_command 자식 프로세스 env 에서 브리지 토큰·비밀류 변수를 제거한다.
- write_file 은 허용 루트(프로젝트 폴더·생성 루트·임시 디렉터리) 밖으로 쓰지 않는다.
- read_file 은 자격증명 파일로 보이는 경로(.env, *.pem, id_rsa 등)를 읽지 않는다.
- CORS 는 렌더러 원본(null)과 로컬 개발 페이지만 허용한다.
- /api/debug/* 진단 엔드포인트는 AE_DEBUG_ENDPOINTS=1 일 때만 열린다(/api/debug/cwd 제외 — 렌더러가 사용).
"""
import os

import pytest
from starlette.testclient import TestClient

import ai_engine.server as s


def test_subprocess_env_drops_bridge_token_and_secrets(monkeypatch):
    monkeypatch.delenv("AE_TOOL_ENV_PASSTHROUGH", raising=False)
    monkeypatch.setenv("AE_BRIDGE_TOKEN", "t0k")
    monkeypatch.setenv("AE_BRIDGE_URL", "http://127.0.0.1:1")
    monkeypatch.setenv("MY_API_KEY", "k")
    monkeypatch.setenv("DB_PASSWORD", "p")
    monkeypatch.setenv("GITHUB_TOKEN", "g")
    monkeypatch.setenv("AWS_PROFILE", "bedrock-gw")
    env = s._tool_subprocess_env()
    for k in ("AE_BRIDGE_TOKEN", "AE_BRIDGE_URL", "MY_API_KEY", "DB_PASSWORD", "GITHUB_TOKEN"):
        assert k not in env
    assert env.get("AWS_PROFILE") == "bedrock-gw"          # 비밀이 아닌 설정은 유지
    assert "PATH" in env
    monkeypatch.setenv("AE_TOOL_ENV_PASSTHROUGH", "1")
    assert "AE_BRIDGE_TOKEN" in s._tool_subprocess_env()   # 명시 해제


def test_write_file_confined_to_project(tmp_path, monkeypatch):
    import tempfile as _tf
    monkeypatch.delenv("AE_TOOL_WRITE_ANYWHERE", raising=False)
    monkeypatch.setenv("AE_GENERATED_ROOT", str(tmp_path / "gen"))
    # pytest 의 tmp_path 는 시스템 임시 디렉터리(허용 루트) 아래에 있으므로, 검사용으로 임시 루트를 다른 곳으로 돌린다.
    systmp = tmp_path / "systmp"; systmp.mkdir()
    monkeypatch.setattr(_tf, "gettempdir", lambda: str(systmp))
    proj = tmp_path / "proj"; proj.mkdir()
    outside = tmp_path / "outside"; outside.mkdir()
    ok = s._execute_tool("write_file", {"path": "notes/a.md", "content": "hi"}, project_path=str(proj))
    assert "완료" in ok and (proj / "notes" / "a.md").read_text(encoding="utf-8") == "hi"
    ok2 = s._execute_tool("write_file", {"path": str(tmp_path / "gen" / "out.txt"), "content": "g"}, project_path=str(proj))
    assert "완료" in ok2                                     # 생성 루트(AE_GENERATED_ROOT)는 허용
    refused = s._execute_tool("write_file", {"path": str(outside / "pwned.txt"), "content": "x"}, project_path=str(proj))
    assert "거부" in refused and not (outside / "pwned.txt").exists()
    traversal = s._execute_tool("write_file", {"path": "../outside/t.txt", "content": "x"}, project_path=str(proj))
    assert "거부" in traversal and not (outside / "t.txt").exists()
    monkeypatch.setenv("AE_TOOL_WRITE_ANYWHERE", "1")
    anywhere = s._execute_tool("write_file", {"path": str(outside / "ok.txt"), "content": "x"}, project_path=str(proj))
    assert "완료" in anywhere                                # 명시 해제


def test_read_file_refuses_credential_like_paths(tmp_path, monkeypatch):
    monkeypatch.delenv("AE_TOOL_READ_SECRETS", raising=False)
    proj = tmp_path / "proj"; proj.mkdir()
    (proj / ".env").write_text("SECRET=1", encoding="utf-8")
    (proj / "id_rsa").write_text("key", encoding="utf-8")
    (proj / "server.pem").write_text("pem", encoding="utf-8")
    (proj / "README.md").write_text("hello", encoding="utf-8")
    for name in (".env", "id_rsa", "server.pem"):
        out = s._execute_tool("read_file", {"path": name}, project_path=str(proj))
        assert "거부" in out and "SECRET=1" not in out and "key" != out
    assert s._execute_tool("read_file", {"path": "README.md"}, project_path=str(proj)) == "hello"
    monkeypatch.setenv("AE_TOOL_READ_SECRETS", "1")
    assert s._execute_tool("read_file", {"path": ".env"}, project_path=str(proj)) == "SECRET=1"


def test_cors_allows_renderer_and_localhost_only():
    client = TestClient(s.app)
    r = client.get("/health", headers={"Origin": "null"})                       # Electron 렌더러(file://)
    assert r.headers.get("access-control-allow-origin") == "null"
    r = client.get("/health", headers={"Origin": "http://127.0.0.1:8099"})      # 로컬 개발 페이지
    assert r.headers.get("access-control-allow-origin") == "http://127.0.0.1:8099"
    r = client.get("/health", headers={"Origin": "https://evil.example"})
    assert r.headers.get("access-control-allow-origin") is None


def test_debug_endpoints_gated(monkeypatch):
    client = TestClient(s.app)
    monkeypatch.delenv("AE_DEBUG_ENDPOINTS", raising=False)
    assert client.get("/api/debug/bridge").status_code == 404
    assert client.get("/api/debug/image-gen-status").status_code == 404
    assert client.get("/api/debug/cwd").status_code == 200                       # 렌더러가 쓰는 경로는 항상 열림
    monkeypatch.setenv("AE_DEBUG_ENDPOINTS", "1")
    assert client.get("/api/debug/bridge").status_code == 200
