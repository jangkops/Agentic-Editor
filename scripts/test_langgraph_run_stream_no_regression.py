"""Phase 5 회귀 테스트 — 기존 `run_agent_stream`(`/api/agents/run-stream`) 무회귀.

Spec: langgraph-hierarchical-orchestrator (Task 9.4, Requirements 9.3)

배경
────
design.md 5단계 마이그레이션의 Phase 5(정리)에서 dead code를 제거했다:
  - `agent_system/agent_graph.py`의 수동 while 루프(파일 자체 삭제)
  - 구버전 `run_workflow`(`/api/agents/workflow`) 핸들러
  - 미참조 레거시 `CheckpointStore`(checkpoint_store.py 에서 삭제, JsonFileCheckpointSaver만 잔존)

이 정리가 기존 실행 경로 `run_agent_stream` 을 깨뜨리지 않았음(무회귀)을 보증한다.
세 층위로 실측한다:

  1. import/모듈 로드 smoke:
     `ai_engine.server` 가 정상 import 되고(제거된 dead code 참조로 인한 ImportError 부재),
     `run_agent_stream`/`app` 및 핸들러가 의존하는 헬퍼 심볼이 모두 존재한다.

  2. 정적 무회귀:
     제거된 심볼(`agent_graph` import, `run_workflow`/`/api/agents/workflow` 라우트,
     레거시 `CheckpointStore`)이 서버 소스에서 **실제 코드로 참조되지 않는다**(주석/docstring 제외).

  3. 최소 동작 경로(네트워크 없음):
     Gateway 를 mock 으로 대체하고 `/api/agents/run-stream` 을 호출하면 SSE `{text}`
     이벤트가 방출되고 `data: [DONE]` 으로 종료된다. 모든 SSE 소비는 유한(TestClient 동기).

제약: LLM 직접 SDK 금지 → mock Gateway 만 사용. server.py 는 수정하지 않고 테스트만 추가.

실행: ai_engine/.venv/bin/python -m pytest scripts/test_langgraph_run_stream_no_regression.py -q
"""
from __future__ import annotations

import ast
import os
import re
import sys

import pytest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

_SERVER_PATH = os.path.abspath(
    os.path.join(os.path.dirname(__file__), "..", "ai_engine", "server.py")
)


def _server():
    """ai_engine.server import — 의존성 부재 시 해당 테스트만 skip.

    이 import 자체가 layer 1(모듈 로드 smoke)의 핵심 검증이다. 제거된 dead code
    (agent_graph 등)를 참조하는 잔여 import 가 있으면 여기서 ImportError 로 실패한다.
    """
    return pytest.importorskip("ai_engine.server")


# ─────────────────────────────────────────────────────────────────────────
# Layer 1 — import / 모듈 로드 smoke
# ─────────────────────────────────────────────────────────────────────────
def test_server_module_imports_without_dead_code_error():
    """서버 모듈이 dead code 제거 후에도 ImportError 없이 로드된다."""
    server = _server()
    assert hasattr(server, "run_agent_stream"), "run_agent_stream 핸들러 소실"
    assert hasattr(server, "app"), "FastAPI app 소실"


@pytest.mark.parametrize(
    "symbol",
    [
        # run_agent_stream 본문이 직접 참조하는 헬퍼들 — 하나라도 없으면 NameError 회귀.
        "_get_gw",
        "_is_code_related",
        "_active_template_prompt_context",
        "_build_messages",
        "_resolve_callable_model_id",
        "is_openai_model",
        "_infer_file_intent_from_prompt",
        "_force_generate_from_text",
        "_maybe_summarize",
        "_stream_with_heartbeat",
    ],
)
def test_run_agent_stream_dependencies_present(symbol):
    """run_agent_stream 이 의존하는 심볼이 모듈 스코프에 존재한다."""
    server = _server()
    assert hasattr(server, symbol), f"의존 심볼 소실(회귀): {symbol}"


def test_checkpoint_store_module_only_exposes_json_saver():
    """레거시 CheckpointStore 제거 후에도 JsonFileCheckpointSaver 는 정상 import."""
    mod = pytest.importorskip("ai_engine.agent_system.checkpoint_store")
    assert hasattr(mod, "JsonFileCheckpointSaver"), "JsonFileCheckpointSaver 소실"
    # 레거시 dead code 는 완전히 사라져야 한다.
    assert not hasattr(mod, "CheckpointStore"), "레거시 CheckpointStore 가 아직 노출됨"


# ─────────────────────────────────────────────────────────────────────────
# Layer 2 — 정적 무회귀: 제거된 심볼이 실제 코드로 참조되지 않는다.
# ─────────────────────────────────────────────────────────────────────────
def _load_server_source() -> str:
    with open(_SERVER_PATH, "r", encoding="utf-8") as f:
        return f.read()


def _strip_comments_and_strings(source: str) -> str:
    """AST 기반으로 주석/docstring/문자열 리터럴을 제거해 '실제 코드'만 남긴다.

    주석·docstring 에는 정리 이력 설명이 남아 있으므로(예: "구버전 run_workflow ... 제거"),
    단순 grep 은 오탐한다. 토큰 스트림에서 STRING/COMMENT 토큰을 제거한다.
    """
    import io
    import tokenize

    out_tokens = []
    try:
        toks = tokenize.generate_tokens(io.StringIO(source).readline)
        for tok in toks:
            if tok.type in (tokenize.COMMENT, tokenize.STRING):
                continue
            out_tokens.append(tok)
        return tokenize.untokenize(out_tokens)
    except Exception:
        # 토큰화 실패 시 원본 반환(보수적).
        return source


@pytest.mark.parametrize(
    "pattern,desc",
    [
        (r"\bimport\s+agent_graph\b", "agent_graph 모듈 import"),
        (r"\bfrom\s+\S*agent_graph\b", "agent_graph from-import"),
        (r"\brun_workflow\s*\(", "구버전 run_workflow 호출"),
        (r"/api/agents/workflow", "구버전 workflow 라우트 등록"),
        (r"\bCheckpointStore\b", "레거시 CheckpointStore 참조"),
    ],
)
def test_removed_dead_code_not_referenced_in_code(pattern, desc):
    """제거된 dead code 심볼이 실제 코드(주석/문자열 제외)에서 참조되지 않는다."""
    code_only = _strip_comments_and_strings(_load_server_source())
    m = re.search(pattern, code_only)
    assert m is None, f"제거된 dead code 가 코드에서 참조됨: {desc} ({pattern})"


def test_agent_graph_module_file_removed():
    """agent_graph.py 파일이 실제로 삭제되었다(Phase 5)."""
    path = os.path.join(
        os.path.dirname(_SERVER_PATH), "agent_system", "agent_graph.py"
    )
    assert not os.path.exists(path), "agent_graph.py 가 아직 존재함(제거 미완)"


def test_run_agent_stream_scope_defines_template_id():
    """run_agent_stream 스코프에 template_id 정의 유지(도구 루프 NameError 무회귀)."""
    tree = ast.parse(_load_server_source())
    target = None
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == "run_agent_stream":
            target = node
            break
    assert target is not None, "run_agent_stream 핸들러를 찾지 못함"
    defines = False
    for node in ast.walk(target):
        if isinstance(node, ast.Assign):
            for tgt in node.targets:
                if isinstance(tgt, ast.Name) and tgt.id == "template_id":
                    defines = True
    assert defines, "run_agent_stream 스코프에 template_id 정의 없음(회귀)"


# ─────────────────────────────────────────────────────────────────────────
# Layer 3 — 최소 동작 경로(mock Gateway, 네트워크 없음).
# `/api/agents/run-stream` 이 SSE {text} 를 방출하고 [DONE] 으로 종료한다.
# ─────────────────────────────────────────────────────────────────────────
def test_run_stream_emits_text_and_terminates_with_done(monkeypatch):
    """mock Gateway 로 run-stream 호출 시 텍스트 방출 + [DONE] 종료(무회귀)."""
    server = _server()
    starlette_testclient = pytest.importorskip("starlette.testclient")

    class _FakeGW:
        def force_refresh_creds(self):
            pass

        async def stream_sse_realtime(self, model_id=None, messages=None, system_prompt="", tool_config=None):
            # Bedrock 실시간 SSE 를 흉내 — 텍스트 델타 후 정상 종료.
            yield {"type": "content_block_delta", "delta": {"text": "안녕하세요"}}
            yield {"type": "message_stop", "stopReason": "end_turn"}

    fake = _FakeGW()
    monkeypatch.setattr(server, "_get_gw", lambda *a, **k: fake)
    # 모델 해석은 네트워크(list_foundation_models 캐시)를 탈 수 있으므로 identity 로 고정.
    monkeypatch.setattr(server, "_resolve_callable_model_id", lambda m, *a, **k: m)
    # RAG/메모리 부작용 차단 — 최소 메시지.
    monkeypatch.setattr(server, "_build_messages",
                        lambda *a, **k: [{"role": "user", "content": [{"text": "안녕"}]}])
    monkeypatch.setattr(server, "_is_code_related", lambda *a, **k: False)
    # 요약 fire-and-forget 부작용 차단.
    import asyncio as _asyncio
    monkeypatch.setattr(server, "_maybe_summarize",
                        lambda *a, **k: _asyncio.sleep(0))

    client = starlette_testclient.TestClient(server.app)
    resp = client.post("/api/agents/run-stream", json={
        "prompt": "안녕", "model": "anthropic.claude-sonnet-4-5-20250929-v1:0",
        "awsProfile": "p", "bedrockUser": "u",
    })
    assert resp.status_code == 200
    body = resp.text
    # SSE {text} 이벤트가 방출되어야 한다.
    assert "안녕하세요" in body, f"텍스트 델타 미방출: {body[:400]}"
    assert '"text"' in body, "SSE text 이벤트 키 누락"
    # 반드시 [DONE] 으로 종료.
    assert "[DONE]" in body, f"[DONE] 종료 이벤트 누락: {body[-400:]}"
    # dead code 제거로 인한 예외가 스트림에 노출되면 안 된다.
    assert "ImportError" not in body and "NameError" not in body, body[:600]


def test_run_stream_surfaces_gateway_error_then_done(monkeypatch):
    """Gateway error 이벤트도 {error} 로 전달되고 [DONE] 으로 종료(경로 견고성)."""
    server = _server()
    starlette_testclient = pytest.importorskip("starlette.testclient")

    class _ErrGW:
        def force_refresh_creds(self):
            pass

        async def stream_sse_realtime(self, model_id=None, messages=None, system_prompt="", tool_config=None):
            yield {"type": "error", "message": "gateway boom"}
            yield {"type": "message_stop", "stopReason": "end_turn"}

    fake = _ErrGW()
    monkeypatch.setattr(server, "_get_gw", lambda *a, **k: fake)
    monkeypatch.setattr(server, "_resolve_callable_model_id", lambda m, *a, **k: m)
    monkeypatch.setattr(server, "_build_messages",
                        lambda *a, **k: [{"role": "user", "content": [{"text": "안녕"}]}])
    monkeypatch.setattr(server, "_is_code_related", lambda *a, **k: False)
    import asyncio as _asyncio
    monkeypatch.setattr(server, "_maybe_summarize", lambda *a, **k: _asyncio.sleep(0))

    client = starlette_testclient.TestClient(server.app)
    resp = client.post("/api/agents/run-stream", json={
        "prompt": "안녕", "model": "anthropic.claude-sonnet-4-5-20250929-v1:0",
        "awsProfile": "p", "bedrockUser": "u",
    })
    assert resp.status_code == 200
    body = resp.text
    assert "gateway boom" in body, f"error 이벤트 미전달: {body[:400]}"
    assert "[DONE]" in body, "error 후 [DONE] 종료 누락"


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-q"]))
