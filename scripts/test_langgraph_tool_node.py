"""GatewayToolNode 순수 로직 회귀 테스트.

검증 대상 (요구사항 3.7/6.2, Property 3):
- _extract_rel_paths: JSON {path} / {images:[{path}]} / write_file(args.path) 추출, error 는 무시.
- 미디어 도구 timeout 분기: media 도구는 긴 상한(media_timeout), 일반 도구는 timeout.
- _verify_files: 디스크 실측(존재+size>0) 통과 항목만 verified 로 반환(fake fs).

gateway·네트워크 불필요. 파일시스템은 tmp_path 로 격리, 유한 시간.
실행: ai_engine/.venv/bin/python -m pytest scripts/test_langgraph_tool_node.py -q
"""
import os
import sys
import json
import time
import asyncio

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from ai_engine.agent_system.nodes import tool_node as TN


# ── _extract_rel_paths ──
def test_extract_path_from_json():
    raw = json.dumps({"path": ".generated/a.pptx", "sizeBytes": 100})
    assert TN._extract_rel_paths("generate_pptx", {}, raw) == [".generated/a.pptx"]


def test_extract_images_list():
    raw = json.dumps({"images": [{"path": ".generated/x.png"}, {"path": ".generated/y.png"}]})
    out = TN._extract_rel_paths("generate_image", {}, raw)
    assert out == [".generated/x.png", ".generated/y.png"]


def test_extract_error_ignored():
    raw = json.dumps({"error": "tool-failed", "path": ".generated/should_ignore.pptx"})
    assert TN._extract_rel_paths("generate_pptx", {}, raw) == []


def test_extract_write_file_uses_args_path():
    assert TN._extract_rel_paths("write_file", {"path": "src/x.py"}, "File saved") == ["src/x.py"]


def test_extract_read_tool_no_paths():
    assert TN._extract_rel_paths("read_file", {"path": "x"}, "content...") == []


# ── media timeout 분기 ──
def test_media_tools_get_longer_timeout():
    node = TN.GatewayToolNode(tools=[], deps=None, timeout=120.0)
    assert "generate_pptx" in TN._MEDIA_TOOLS
    assert node.media_timeout >= node.timeout
    # 기본 media 상한이 일반보다 크다
    assert node.media_timeout >= 600.0 or node.media_timeout >= node.timeout


# ── _verify_files 디스크 실측 ──
def test_verify_files_only_real_files(tmp_path):
    node = TN.GatewayToolNode(tools=[], deps=None)
    # 실제 파일 생성
    real = tmp_path / ".generated"
    real.mkdir()
    good = real / "good.pptx"
    good.write_bytes(b"x" * 50)
    empty = real / "empty.pptx"
    empty.write_bytes(b"")  # size 0 → 제외돼야

    state = {"project_path": str(tmp_path)}
    raw_good = json.dumps({"path": ".generated/good.pptx"})
    raw_empty = json.dumps({"path": ".generated/empty.pptx"})
    raw_missing = json.dumps({"path": ".generated/nope.pptx"})

    out_good = node._verify_files("generate_pptx", {}, raw_good, state)
    out_empty = node._verify_files("generate_pptx", {}, raw_empty, state)
    out_missing = node._verify_files("generate_pptx", {}, raw_missing, state)

    assert len(out_good) == 1 and out_good[0]["path"] == ".generated/good.pptx"
    assert out_empty == []      # size 0 제외
    assert out_missing == []    # 미존재 제외


# ── 검색 진행 이벤트 방출 (요구사항 18.1/18.2/18.3/18.5/18.6, P14/P9/P8) ──
# design.md "9-1) 방출 — GatewayToolNode 경계": 검색 도구군에 한해 실행 경계에서
# search_status(start 1회 → end 1회)를 방출한다. 자격증명 미포함(P9), 방출 실패는 비차단(P8).
class _FakeAI:
    """tool_calls 만 갖는 최소 AIMessage 대역."""

    def __init__(self, tool_calls):
        self.tool_calls = tool_calls


def _tc(name, args=None, tid="tc1"):
    return {"name": name, "args": args or {}, "id": tid}


def _run_node(node, state):
    return asyncio.run(node(state))


def _capture(monkeypatch):
    """TN.adispatch_custom_event 를 비동기 레코더로 교체하고 이벤트 리스트를 반환."""
    events = []

    async def _rec(name, data, *, config=None):
        events.append((name, data))

    monkeypatch.setattr(TN, "adispatch_custom_event", _rec)
    return events


def test_non_search_tool_emits_no_search_status(monkeypatch):
    # 비검색 도구는 방출하지 않는다(기존 동작 불변) + ToolMessage 1/호출 보존.
    events = _capture(monkeypatch)
    node = TN.GatewayToolNode(tools=[], deps=None, timeout=5.0)
    node._run_local = lambda name, args, state: "content..."
    state = {"messages": [_FakeAI([_tc("read_file", {"path": "x"})])], "project_path": "/tmp"}
    out = _run_node(node, state)
    assert events == []
    assert len(out["messages"]) == 1
    assert out["messages"][0].tool_call_id == "tc1"


def test_search_tool_success_emits_start_then_end_ok(monkeypatch):
    events = _capture(monkeypatch)
    node = TN.GatewayToolNode(tools=[], deps=None, timeout=5.0)
    node._run_local = lambda name, args, state: json.dumps({"results": []})
    state = {"messages": [_FakeAI([_tc("web_search", {"query": "langgraph custom events"})])]}
    out = _run_node(node, state)

    assert len(out["messages"]) == 1  # ToolMessage 1/호출 보존
    phases = [d["phase"] for (n, d) in events if n == "search_status"]
    assert phases == ["start", "end"]  # P14: start 1회 → end 1회
    start, end = events[0][1], events[1][1]
    assert start["kind"] == "web" and end["kind"] == "web"
    assert "status" not in start  # start 에는 status 없음
    assert end["status"] == "ok"  # 성공 → ok
    assert isinstance(start["providers"], list)
    # P9: 자격증명 미포함 — 제공자 "이름" 목록뿐, key/secret/token 키 없음
    for d in (start, end):
        assert not any(
            s in k.lower() for k in d for s in ("key", "secret", "token")
        )
        blob = json.dumps(d).lower()
        assert "secret" not in blob and "apikey" not in blob


def test_search_tool_kind_mapping(monkeypatch):
    # 도구명 → kind: web_search=web / search_papers=academic / deep_research=deep (요구사항 18.2).
    events = _capture(monkeypatch)
    node = TN.GatewayToolNode(tools=[], deps=None, timeout=5.0)
    node._run_local = lambda name, args, state: "{}"
    for tool, kind in [
        ("web_search", "web"),
        ("search_papers", "academic"),
        ("deep_research", "deep"),
    ]:
        events.clear()
        state = {"messages": [_FakeAI([_tc(tool, {"query": "q"})])]}
        _run_node(node, state)
        assert {d["kind"] for (n, d) in events} == {kind}
        assert [d["phase"] for (n, d) in events] == ["start", "end"]


def test_search_tool_exception_still_emits_end_error(monkeypatch):
    # 도구 예외에도 end 를 1회 방출(status=error) — P14 실패 분기, 예외는 ToolMessage 로 비차단 전달.
    events = _capture(monkeypatch)
    node = TN.GatewayToolNode(tools=[], deps=None, timeout=5.0)

    def _boom(name, args, state):
        raise RuntimeError("provider down")

    node._run_local = _boom
    state = {"messages": [_FakeAI([_tc("web_search", {"query": "q"})])]}
    out = _run_node(node, state)

    assert [(d["phase"], d.get("status")) for (n, d) in events] == [
        ("start", None),
        ("end", "error"),
    ]
    assert len(out["messages"]) == 1
    assert "오류" in out["messages"][0].content


def test_search_tool_timeout_still_emits_end_error(monkeypatch):
    # 타임아웃에도 end 1회(status=error) — P14 타임아웃 분기.
    events = _capture(monkeypatch)
    node = TN.GatewayToolNode(tools=[], deps=None, timeout=0.05)
    node._run_local = lambda name, args, state: (time.sleep(0.3), "{}")[1]
    state = {"messages": [_FakeAI([_tc("web_search", {"query": "q"})])]}
    out = _run_node(node, state)
    assert [(d["phase"], d.get("status")) for (n, d) in events] == [
        ("start", None),
        ("end", "error"),
    ]
    assert "시간 초과" in out["messages"][0].content


def test_emit_failure_is_non_blocking(monkeypatch):
    # adispatch 가 항상 예외를 던져도 도구 실행/답변이 진행되어야 한다(P8 / 요구사항 18.5).
    async def _raise(name, data, *, config=None):
        raise RuntimeError("dispatch failed")

    monkeypatch.setattr(TN, "adispatch_custom_event", _raise)
    node = TN.GatewayToolNode(tools=[], deps=None, timeout=5.0)
    node._run_local = lambda name, args, state: json.dumps({"results": [1]})
    state = {"messages": [_FakeAI([_tc("web_search", {"query": "q"})])]}
    out = _run_node(node, state)  # 예외 전파 없이 완료돼야 한다
    assert len(out["messages"]) == 1
    assert out["messages"][0].tool_call_id == "tc1"


def test_query_summary_truncated_and_no_credentials(monkeypatch):
    # query_summary 는 AE_RESEARCH_QUERY_SUMMARY_MAX 로 절단(요구사항 18.2/18.7).
    events = _capture(monkeypatch)
    monkeypatch.setenv("AE_RESEARCH_QUERY_SUMMARY_MAX", "10")
    node = TN.GatewayToolNode(tools=[], deps=None, timeout=5.0)
    node._run_local = lambda name, args, state: "{}"
    state = {"messages": [_FakeAI([_tc("web_search", {"query": "x" * 200})])]}
    _run_node(node, state)
    assert len(events[0][1]["query_summary"]) == 10


if __name__ == "__main__":
    import pytest
    raise SystemExit(pytest.main([__file__, "-q"]))
