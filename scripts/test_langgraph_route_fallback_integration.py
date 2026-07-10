"""graph-stream 라우트 디스패치 통합 테스트 — flag on/off + 실패 fallback (요구사항 7.1~7.4).

검증 대상 (ai_engine/server.py):
- `_langgraph_enabled()`        : AE_LANGGRAPH 환경변수 파싱(기본 on, 0/false/off/no 만 비활성).
- `run_agent_graph_stream()`    : 신규 Graph_Endpoint 의 경로 선택/폴백 디스패치.

시나리오 (요구사항 매핑):
- 7.1/7.2  AE_LANGGRAPH on  → graph-stream(신규 그래프) 경로가 선택된다(StreamingResponse 반환,
           기존 run_agent_stream 으로 위임하지 않음).
- 7.1/7.3  AE_LANGGRAPH off → 기존 실행 경로(run_agent_stream)로 위임된다.
- 7.4      그래프 경로 준비(deps/compile)가 예외로 실패하면 기존 경로로 **자동 fallback** 되어
           응답이 끊기지 않는다(run_agent_stream 반환).

접근(제약 준수 — 실제 LLM/네트워크 없음):
- 기존 경로 위임/폴백 여부를 확정적으로 관측하기 위해 `server.run_agent_stream` 을 고유
  sentinel 을 반환하는 async stub 으로 monkeypatch 한다. 반환값이 sentinel 이면 "기존 경로",
  StreamingResponse(그리고 sentinel 아님)면 "그래프 경로 선택".
- 그래프 경로 준비의 무거운/네트워크 지점(`_get_gw`, supervisor 그래프 빌더, MCP 로드,
  멀티턴 messages 구성)은 경량 stub 으로 대체한다. 그래프는 **조립만** 되고 스트림은
  소비하지 않으므로(StreamingResponse 는 lazy) 실제 LLM 호출은 발생하지 않는다.
- 체크포인터/스토어가 홈 디렉토리에 쓰지 않도록 AE_GENERATED_ROOT 를 tmp 로 지정한다.
- 라우트는 async 이므로 asyncio.run 으로 유한 시간에 호출한다(무한대기 없음).

⚠️ monkeypatch 원리: 라우트 내부의 `from ai_engine.agent_system.supervisor import ...` 는
   호출 시점에 `getattr(supervisor_module, name)` 을 수행하므로, 모듈 속성을 미리 교체해두면
   라우트가 교체된 구현을 집어온다. `_get_gw` / `run_agent_stream` 은 server 모듈 전역이라
   server 모듈 속성 교체로 가로챈다.

실행: ai_engine/.venv/bin/python -m pytest scripts/test_langgraph_route_fallback_integration.py -q
"""
from __future__ import annotations

import asyncio
import os
import sys
import tempfile

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from fastapi.responses import StreamingResponse

import ai_engine.server as server_mod
from ai_engine.agent_system import supervisor as supervisor_mod


# ─────────────────────────────────────────────────────────────────────────────
# 최소 mock: Request / gateway / compiled graph
# ─────────────────────────────────────────────────────────────────────────────
class FakeRequest:
    """라우트가 사용하는 인터페이스만 제공하는 최소 Request mock (async json())."""

    def __init__(self, body):
        self._body = dict(body)

    async def json(self):
        await asyncio.sleep(0)
        return self._body


class _SentinelResponse:
    """run_agent_stream 이 반환하는 고유 sentinel — '기존 경로 위임' 관측용."""


class _DummyCompiled:
    """build_*_graph 대체 stub 이 반환하는 더미 compiled graph — 스트림 미소비이므로 미사용."""


def _make_body(**over):
    body = {
        "prompt": "이 코드를 분석해줘",
        "sessionId": "route-test",
        "projectPath": "",
        "awsProfile": "bedrock-gw",
        "bedrockUser": "",
        "model": "anthropic.claude-sonnet-4-5-20250929-v1:0",
    }
    body.update(over)
    return body


def _run(coro):
    return asyncio.run(coro)


# ─────────────────────────────────────────────────────────────────────────────
# monkeypatch 설치/복원 — 원본 저장 후 finally 에서 반드시 되돌린다.
# ─────────────────────────────────────────────────────────────────────────────
class _Patches:
    """그래프 준비의 무거운/네트워크 지점을 경량 stub 으로 교체하고, run_agent_stream 을
    sentinel stub 으로 교체한다. fail_prep=True 면 _get_gw 가 예외를 던져 준비 실패를 강제한다.
    """

    def __init__(self, *, fail_prep=False):
        self.fail_prep = fail_prep
        self._saved = {}
        self.sentinel = _SentinelResponse()
        self.run_agent_stream_calls = 0
        self.graph_build_calls = 0

    def _save(self, obj, name):
        self._saved[(id(obj), name)] = (obj, name, getattr(obj, name))

    def __enter__(self):
        parent = self

        async def _stub_run_agent_stream(request):
            parent.run_agent_stream_calls += 1
            return parent.sentinel

        def _stub_get_gw(aws_profile, bedrock_user):
            if parent.fail_prep:
                raise RuntimeError("forced prep failure (graph path)")
            return object()  # 준비만 통과시키는 더미 gateway

        def _stub_build(deps):
            parent.graph_build_calls += 1
            return _DummyCompiled()

        async def _stub_get_mcp_tools():
            return [], set()

        def _stub_build_messages(chat_history, current_prompt, session_id=""):
            # 디스크(ConversationMemory) 접근 회피 — 라우트 try/except 로 비차단.
            return [{"role": "user", "content": [{"text": current_prompt}]}]

        def _stub_resolve_model_id(model_id, aws_profile, bedrock_user):
            # boto3 list_foundation_models(네트워크) 회피 — 모델 id 그대로 통과.
            return model_id

        # server 모듈 전역(전부 bare-name 호출).
        self._save(server_mod, "run_agent_stream")
        self._save(server_mod, "_get_gw")
        self._save(server_mod, "_build_messages")
        self._save(server_mod, "_resolve_callable_model_id")
        server_mod.run_agent_stream = _stub_run_agent_stream
        server_mod._get_gw = _stub_get_gw
        server_mod._build_messages = _stub_build_messages
        server_mod._resolve_callable_model_id = _stub_resolve_model_id

        # supervisor 모듈 속성(라우트가 호출 시점에 from-import 로 집어옴).
        self._save(supervisor_mod, "build_top_graph")
        self._save(supervisor_mod, "build_parallel_top_graph")
        supervisor_mod.build_top_graph = _stub_build
        supervisor_mod.build_parallel_top_graph = _stub_build

        # MCP 로드 stub — mcp_tools 모듈의 get_mcp_tools 를 교체.
        from ai_engine.agent_system import mcp_tools as mcp_mod
        self._mcp_mod = mcp_mod
        self._save(mcp_mod, "get_mcp_tools")
        mcp_mod.get_mcp_tools = _stub_get_mcp_tools

        return self

    def __exit__(self, *exc):
        for (obj, name, orig) in self._saved.values():
            setattr(obj, name, orig)
        return False


def _set_flag(value):
    """AE_LANGGRAPH 설정(None 이면 삭제해 기본값 경로 검증)."""
    if value is None:
        os.environ.pop("AE_LANGGRAPH", None)
    else:
        os.environ["AE_LANGGRAPH"] = value


# ─────────────────────────────────────────────────────────────────────────────
# 1) _langgraph_enabled() 환경변수 파싱 (요구사항 7.2 / 7.3 근간)
# ─────────────────────────────────────────────────────────────────────────────
def test_flag_default_is_enabled():
    """AE_LANGGRAPH 미설정 시 기본 활성(on) — 무조작 사용자도 그래프 경로 사용."""
    _saved = os.environ.get("AE_LANGGRAPH")
    try:
        _set_flag(None)
        assert server_mod._langgraph_enabled() is True
    finally:
        _set_flag(_saved)


def test_flag_explicit_disable_values():
    """0/false/off/no(대소문자·공백 무관)만 비활성. 그 외 값은 활성."""
    _saved = os.environ.get("AE_LANGGRAPH")
    try:
        for v in ("0", "false", "off", "no", "  OFF ", "False", "NO"):
            _set_flag(v)
            assert server_mod._langgraph_enabled() is False, f"{v!r} 은 비활성이어야 함"
        for v in ("1", "on", "true", "yes", "enabled", "  ON "):
            _set_flag(v)
            assert server_mod._langgraph_enabled() is True, f"{v!r} 은 활성이어야 함"
    finally:
        _set_flag(_saved)


# ─────────────────────────────────────────────────────────────────────────────
# 2) 라우트 디스패치 — flag on / off / 준비 실패 fallback
# ─────────────────────────────────────────────────────────────────────────────
def test_flag_off_delegates_to_legacy_path():
    """AE_LANGGRAPH off → 기존 run_agent_stream 경로로 위임 (요구사항 7.1 / 7.3)."""
    _saved = os.environ.get("AE_LANGGRAPH")
    try:
        _set_flag("0")
        with _Patches() as p:
            req = FakeRequest(_make_body())
            result = _run(server_mod.run_agent_graph_stream(req))
        # 기존 경로로 위임됨(반환값이 sentinel, 호출 1회).
        assert result is p.sentinel, "flag off 인데 기존 경로로 위임되지 않음"
        assert p.run_agent_stream_calls == 1
        # 그래프 빌드는 시도조차 하지 않아야 함.
        assert p.graph_build_calls == 0
    finally:
        _set_flag(_saved)


def test_flag_on_selects_graph_path():
    """AE_LANGGRAPH on + 준비 성공 → graph-stream(신규) 경로 선택 (요구사항 7.1 / 7.2).

    관측: run_agent_stream(기존 경로)으로 위임하지 않고 StreamingResponse 를 반환하며,
    그래프 빌더가 실제로 호출된다. StreamingResponse 는 lazy 이므로 LLM 호출은 없음.
    """
    _saved = os.environ.get("AE_LANGGRAPH")
    with tempfile.TemporaryDirectory(prefix="lg_route_") as tmp:
        _saved_root = os.environ.get("AE_GENERATED_ROOT")
        try:
            _set_flag("on")
            os.environ["AE_GENERATED_ROOT"] = tmp  # 체크포인터/스토어를 tmp 하위로 한정.
            with _Patches() as p:
                req = FakeRequest(_make_body())
                result = _run(server_mod.run_agent_graph_stream(req))
            # 그래프 경로 선택: StreamingResponse 반환, sentinel 아님, 기존 경로 미위임.
            assert isinstance(result, StreamingResponse), f"StreamingResponse 아님: {type(result)}"
            assert result is not p.sentinel
            assert p.run_agent_stream_calls == 0, "flag on 인데 기존 경로로 위임됨"
            # 그래프가 실제로 조립됨(그래프 경로 진입 증거).
            assert p.graph_build_calls == 1
        finally:
            _set_flag(_saved)
            if _saved_root is None:
                os.environ.pop("AE_GENERATED_ROOT", None)
            else:
                os.environ["AE_GENERATED_ROOT"] = _saved_root


def test_openai_model_delegates_to_legacy_path():
    """회귀 수정: OpenAI provider 모델(openai.*)은 flag on 이어도 run-stream 으로 위임.

    graph-stream(GatewayChatModel=converse)에는 OpenAI 전용 라우트(/openai/responses) 분기가
    없으므로, is_openai_model 을 처리하는 검증된 run_agent_stream 경로로 위임되어야 한다
    (GPT 5.x 등 '일시적 오류' 회귀 복구). 그래프 빌드는 시도되지 않아야 한다.
    """
    _saved = os.environ.get("AE_LANGGRAPH")
    try:
        _set_flag("on")  # flag on 이어도 OpenAI 모델은 위임되어야 함이 핵심.
        with _Patches() as p:
            req = FakeRequest(_make_body(model="openai.gpt-5.4"))
            result = _run(server_mod.run_agent_graph_stream(req))
        # OpenAI 모델 → 기존(run-stream) 경로로 위임(sentinel 반환, 호출 1회).
        assert result is p.sentinel, "OpenAI 모델인데 run-stream 으로 위임되지 않음"
        assert p.run_agent_stream_calls == 1
        # 그래프 경로(converse)로 진입하지 않아야 함.
        assert p.graph_build_calls == 0
    finally:
        _set_flag(_saved)


def test_non_openai_model_uses_graph_path():
    """대조군: 비-OpenAI 모델은 OpenAI 위임 분기를 타지 않고 그래프 경로를 사용한다."""
    _saved = os.environ.get("AE_LANGGRAPH")
    with tempfile.TemporaryDirectory(prefix="lg_route_") as tmp:
        _saved_root = os.environ.get("AE_GENERATED_ROOT")
        try:
            _set_flag("on")
            os.environ["AE_GENERATED_ROOT"] = tmp
            with _Patches() as p:
                req = FakeRequest(_make_body(model="anthropic.claude-sonnet-4-5-20250929-v1:0"))
                result = _run(server_mod.run_agent_graph_stream(req))
            assert isinstance(result, StreamingResponse)
            assert p.run_agent_stream_calls == 0, "비-OpenAI 모델인데 run-stream 으로 위임됨"
            assert p.graph_build_calls == 1
        finally:
            _set_flag(_saved)
            if _saved_root is None:
                os.environ.pop("AE_GENERATED_ROOT", None)
            else:
                os.environ["AE_GENERATED_ROOT"] = _saved_root


def test_prep_failure_falls_back_to_legacy_path():
    """그래프 준비(deps/compile) 실패 → 기존 경로로 자동 fallback (요구사항 7.4).

    _get_gw 가 예외를 던져 준비 단계가 실패하면, 라우트는 예외를 삼키고 run_agent_stream
    으로 위임해 응답이 끊기지 않아야 한다.
    """
    _saved = os.environ.get("AE_LANGGRAPH")
    try:
        _set_flag("on")
        with _Patches(fail_prep=True) as p:
            req = FakeRequest(_make_body())
            result = _run(server_mod.run_agent_graph_stream(req))
        # 준비 실패 → 기존 경로 fallback(sentinel 반환).
        assert result is p.sentinel, "준비 실패인데 기존 경로로 fallback 되지 않음"
        assert p.run_agent_stream_calls == 1
        # 준비 단계에서 실패했으므로 그래프 빌드까지 도달하지 않음.
        assert p.graph_build_calls == 0
    finally:
        _set_flag(_saved)


if __name__ == "__main__":
    import pytest

    raise SystemExit(pytest.main([__file__, "-q"]))
