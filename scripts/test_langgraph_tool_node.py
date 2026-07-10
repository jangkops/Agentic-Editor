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


if __name__ == "__main__":
    import pytest
    raise SystemExit(pytest.main([__file__, "-q"]))
