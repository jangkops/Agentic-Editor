"""Regression — (1) run-agent/run-parallel 도구 루프 NameError 방지,
(2) model_denied(미허용 모델) 자동 회피 라우팅.

Feature: 흐름도 PPTX 파이프라인 전 단계 무오류 (사용자: "모든 과정에서 아무 문제 없어야함")

── 버그 1: name 'template_id' is not defined ───────────────────────────────
`run_agent_with_tools`(/api/agents/run-agent)와 `run_agent_parallel`
(/api/agents/run-parallel) 핸들러의 도구 실행 루프가 `_execute_tool(..., template_id)`
를 호출하는데, 두 핸들러 스코프에 `template_id`가 정의돼 있지 않아 모든 도구 호출
(list_directory/run_command/read_file/generate_pptx 등)이 NameError로 실패했다.
→ run-stream(5593)은 정의돼 있었지만 run-agent/run-parallel은 누락.
수정: 두 핸들러에서 body 파싱 직후 `template_id = body.get("templateId", "")` 추가.

── 버그 2: model_denied (us.google.gemma-3-27b-pt-v1:0 not in allowed list) ──
`_specialized_model_for_task`가 general_chat/summarize 등 경량 task에서 후보 1순위로
Gemma `-pt-`(사전학습 base, instruction-tuned 아님 → 게이트웨이 chat 미허용) 모델을
골라 단계 전체가 403 model_denied로 실패했다.
수정: (a) denylist 패턴(`-pt-`)으로 base 변형을 사전 제외, (b) 런타임에 model_denied
관찰 시 해당 id 학습, (c) 전부 걸러지면 Claude로 안전 폴백.

이 테스트는 수정의 의미성을 증명한다(수정 제거 시 실패).

실행: pytest scripts/test_template_id_and_model_denied.py -q
"""
from __future__ import annotations

import ast
import os
import sys

import pytest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

_SERVER_PATH = os.path.abspath(
    os.path.join(os.path.dirname(__file__), "..", "ai_engine", "server.py")
)


# ─────────────────────────────────────────────────────────────────────────
# 버그 1 — 정적 보증: _execute_tool에 template_id를 넘기는 핸들러는
# 같은 함수 스코프(또는 클로저 상위)에서 template_id를 반드시 정의한다.
# ─────────────────────────────────────────────────────────────────────────
def _load_module_ast():
    with open(_SERVER_PATH, "r", encoding="utf-8") as f:
        return ast.parse(f.read())


def _function_defines_name(func_node: ast.AST, name: str) -> bool:
    """func_node 본문(중첩 함수 포함)에서 `name = ...` 할당이 있으면 True."""
    for node in ast.walk(func_node):
        if isinstance(node, ast.Assign):
            for tgt in node.targets:
                if isinstance(tgt, ast.Name) and tgt.id == name:
                    return True
        # 파라미터로 받는 경우도 정의로 인정
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            for arg in list(node.args.args) + list(node.args.kwonlyargs):
                if arg.arg == name:
                    return True
    return False


@pytest.mark.parametrize("handler", ["run_agent_with_tools", "run_agent_parallel", "run_agent_stream"])
def test_tool_loop_handlers_define_template_id(handler):
    """도구 루프를 포함하는 핸들러는 template_id를 스코프에 정의해야 한다."""
    tree = _load_module_ast()
    target = None
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == handler:
            target = node
            break
    assert target is not None, f"핸들러 {handler} 를 찾지 못함"
    assert _function_defines_name(target, "template_id"), (
        f"{handler} 스코프에 template_id 정의 없음 — 도구 루프에서 NameError 위험"
    )


# ─────────────────────────────────────────────────────────────────────────
# 버그 2 — denylist 동작 보증.
# ─────────────────────────────────────────────────────────────────────────
def _server():
    """ai_engine.server import — 의존성(httpx 등) 부재 시 해당 테스트만 skip."""
    return pytest.importorskip("ai_engine.server")


@pytest.mark.parametrize(
    "mid",
    [
        "google.gemma-3-27b-pt-v1:0",
        "us.google.gemma-3-27b-pt-v1:0",
        "GOOGLE.GEMMA-3-27B-PT-V1:0",
    ],
)
def test_pt_base_variant_is_denied(mid):
    """`-pt-`(사전학습 base) 변형은 chat 미허용 → denylist로 차단되어야 한다."""
    server = _server()
    assert server._model_is_denied(mid) is True


@pytest.mark.parametrize(
    "mid",
    [
        "google.gemma-3-12b-it-v1:0",
        "anthropic.claude-sonnet-4-6",
        "amazon.nova-lite-v1:0",
    ],
)
def test_instruction_tuned_models_not_denied_by_default(mid):
    """instruction-tuned/Claude/Nova 등 정상 모델은 기본 denylist에 걸리지 않는다."""
    server = _server()
    assert server._model_is_denied(mid) is False


def test_extract_denied_model_from_error():
    server = _server()
    detail = (
        'data: {"type": "error", "error_code": "model_denied", '
        '"message": "model us.google.gemma-3-27b-pt-v1:0 not in allowed list"}'
    )
    got = server._extract_denied_model_from_error(detail)
    assert got == "us.google.gemma-3-27b-pt-v1:0"


def test_runtime_denylist_learning():
    """model_denied 에러를 관찰하면 해당 id를 denylist에 학습한다."""
    server = _server()
    test_id = "fake.provider.model-xyz-v1:0"
    assert server._model_is_denied(test_id) is False
    server._maybe_record_denied_from_error(
        f"model_denied: model {test_id} not in allowed list"
    )
    assert server._model_is_denied(test_id) is True


def test_general_chat_routing_never_returns_pt_variant():
    """general_chat 라우팅은 어떤 경우에도 `-pt-` base 변형을 반환하지 않는다.

    동적 카탈로그에 gemma `-pt-`가 있어도 denylist 필터로 제외되어야 한다.
    """
    server = _server()
    # 카탈로그 캐시에 -pt- 모델만 넣어 최악 케이스를 강제
    server._GATEWAY_MODEL_CACHE["models"] = [
        {"id": "google.gemma-3-27b-pt-v1:0", "provider": "Google", "name": "g", "capabilities": {}},
        {"id": "google.gemma-3-12b-it-v1:0", "provider": "Google", "name": "g", "capabilities": {}},
    ]
    try:
        picked = server._specialized_model_for_task("general_chat", "")
        assert "-pt-" not in picked.lower(), f"라우팅이 base 변형을 반환: {picked}"
    finally:
        server._GATEWAY_MODEL_CACHE["models"] = []


def test_finalize_route_to_claude_returns_tool_capable():
    """미허용 모델 재라우팅 결과는 도구 호출 가능한 Claude여야 한다."""
    server = _server()
    safe = server._finalize_route_to_claude("us.google.gemma-3-27b-pt-v1:0")
    assert server._module_is_tool_capable(safe), f"재라우팅 결과가 도구 미지원: {safe}"
    assert "claude" in safe.lower()


# ─────────────────────────────────────────────────────────────────────────
# 통합 — /api/agents/run-agent가 스트림 중 model_denied를 만나면
# (1) UnboundLocalError 없이(nonlocal stream_model 보증)
# (2) Claude로 자동 재라우팅하여 같은 turn을 재시도하고 최종 텍스트를 반환한다.
# ─────────────────────────────────────────────────────────────────────────
def test_run_agent_reroutes_on_model_denied(monkeypatch):
    server = _server()
    starlette_testclient = pytest.importorskip("starlette.testclient")

    class _FakeGW:
        def __init__(self):
            self.turn = 0

        def force_refresh_creds(self):
            pass

        async def stream_sse_realtime(self, model_id=None, messages=None, system_prompt="", tool_config=None):
            self.turn += 1
            if self.turn == 1:
                # 1회차 — 미허용 모델 에러 (denylist + 재라우팅 트리거)
                yield {"type": "error",
                       "message": 'model_denied: model us.google.gemma-3-12b-it-v1:0 not in allowed list'}
                return
            # 2회차 이후 — 안전 모델(Claude)로 정상 텍스트 생성
            yield {"type": "content_block_delta", "delta": {"text": "안녕하세요"}}
            yield {"type": "message_stop", "stopReason": "end_turn"}

    fake = _FakeGW()
    monkeypatch.setattr(server, "_get_gw", lambda *a, **k: fake)
    # 핸들러 초기 라우팅이 미허용 모델을 반환하도록 강제 → 재라우팅 경로 검증
    monkeypatch.setattr(server, "_specialized_model_for_task",
                        lambda *a, **k: "us.google.gemma-3-12b-it-v1:0")
    monkeypatch.setattr(server, "_finalize_route_to_claude",
                        lambda *a, **k: "us.anthropic.claude-sonnet-4-6-20250929-v1:0")
    # RAG/메모리 부작용 차단 — 메시지는 최소 형태로
    monkeypatch.setattr(server, "_build_messages",
                        lambda *a, **k: [{"role": "user", "content": [{"text": "안녕"}]}])
    monkeypatch.setattr(server, "_is_code_related", lambda *a, **k: False)

    client = starlette_testclient.TestClient(server.app)
    resp = client.post("/api/agents/run-agent", json={
        "prompt": "안녕", "model": "deepseek.r1-v1:0",
        "awsProfile": "p", "bedrockUser": "u",
    })
    assert resp.status_code == 200
    body = resp.text
    # nonlocal 미적용 시 첫 turn에서 UnboundLocalError가 스트림에 노출됨
    assert "UnboundLocalError" not in body, body[:500]
    # 재라우팅 이벤트 + 최종 텍스트가 모두 존재해야 한다
    assert "model_routing" in body
    assert "안녕하세요" in body
    assert fake.turn >= 2, "재라우팅 후 같은 작업이 재시도되지 않음"


# ─────────────────────────────────────────────────────────────────────────
# heartbeat — 스트림이 thinking/도구 실행으로 idle해도 끊기지 않는다.
# (사용자: "180초 제한없어야함, 오래 걸려도 thinking 중이면 진행 표시")
# ─────────────────────────────────────────────────────────────────────────
def test_stream_with_heartbeat_injects_on_idle():
    """원본 스트림이 heartbeat 임계보다 늦게 이벤트를 내면 heartbeat가 주입된다."""
    import asyncio
    server = _server()

    async def _slow_source():
        await asyncio.sleep(0.25)  # heartbeat_s(0.08)보다 김
        yield {"type": "content_block_delta", "delta": {"text": "hi"}}

    async def _drive():
        out = []
        async for e in server._stream_with_heartbeat(_slow_source, heartbeat_s=0.08):
            out.append(e)
        return out

    events = asyncio.run(_drive())
    types = [e.get("type") for e in events]
    assert "heartbeat" in types, f"idle인데 heartbeat 미주입: {types}"
    # 마지막은 실제 이벤트여야 한다 (원본 스트림 보존)
    assert events[-1].get("type") == "content_block_delta"
    assert events[-1]["delta"]["text"] == "hi"


def test_stream_with_heartbeat_no_heartbeat_when_fast():
    """원본이 즉시 완료되면 heartbeat 없이 그대로 통과한다."""
    import asyncio
    server = _server()

    async def _fast_source():
        yield {"type": "a"}
        yield {"type": "b"}

    async def _drive():
        out = []
        async for e in server._stream_with_heartbeat(_fast_source, heartbeat_s=5.0):
            out.append(e)
        return out

    events = asyncio.run(_drive())
    assert [e.get("type") for e in events] == ["a", "b"]


def test_run_agent_emits_heartbeat_during_slow_thinking(monkeypatch):
    """run-agent는 모델이 오래 thinking 중이어도 끊기지 않고 heartbeat를 송출한다."""
    import asyncio
    server = _server()
    starlette_testclient = pytest.importorskip("starlette.testclient")

    # heartbeat 임계를 짧게 — 테스트 속도
    monkeypatch.setattr(server, "_SSE_HEARTBEAT_SECONDS", 0.1)

    class _SlowGW:
        def force_refresh_creds(self):
            pass

        async def stream_sse_realtime(self, model_id=None, messages=None, system_prompt="", tool_config=None):
            await asyncio.sleep(0.35)  # thinking — 임계(0.1)보다 길어 heartbeat 유발
            yield {"type": "content_block_delta", "delta": {"text": "결과"}}
            yield {"type": "message_stop", "stopReason": "end_turn"}

    fake = _SlowGW()
    monkeypatch.setattr(server, "_get_gw", lambda *a, **k: fake)
    monkeypatch.setattr(server, "_specialized_model_for_task",
                        lambda *a, **k: "us.anthropic.claude-sonnet-4-6-20250929-v1:0")
    monkeypatch.setattr(server, "_build_messages",
                        lambda *a, **k: [{"role": "user", "content": [{"text": "hi"}]}])
    monkeypatch.setattr(server, "_is_code_related", lambda *a, **k: False)

    client = starlette_testclient.TestClient(server.app)
    resp = client.post("/api/agents/run-agent", json={
        "prompt": "hi", "model": "anthropic.claude-sonnet-4-6",
        "awsProfile": "p", "bedrockUser": "u",
    })
    assert resp.status_code == 200
    body = resp.text
    assert "heartbeat" in body, "thinking 중 heartbeat가 송출되지 않음"
    assert "결과" in body, "최종 텍스트가 누락됨"
    assert "무응답" not in body and "끊김" not in body


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-q"]))
