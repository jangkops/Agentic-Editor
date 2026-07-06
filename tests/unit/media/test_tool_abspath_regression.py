"""Regression — 생성 도구 응답의 absPath 정확성 (TASK 8 근본수정).

근본 버그: 채팅 미디어 카드가 파일 경로를 `state.folderPath + 상대경로`로
추측 조립해, 백엔드가 _resolve_local_root() 정책(원격/폴더미오픈 시 ~/.agentic-editor)
으로 저장한 실제 위치와 갈렸다. 결과: 채팅엔 파일이 보이는데 다운로드하면 없음.

수정: 모든 생성 도구(pdf/pptx/xlsx/docx/image) 응답에 실제 저장 절대경로
`absPath`를 포함시키고, 프론트 카드(resolveItemPath)가 absPath를 최우선 사용.

Correctness property:
  P1. 성공한 생성 도구 응답은 절대경로 `absPath` 필드를 포함한다.
  P2. `absPath`는 디스크에 실재하고 크기 > 0 인 파일을 가리킨다.
  P3. `absPath`는 `path`(상대) 와 동일 파일명으로 끝난다 (응답 일관성).
  P4. resolveItemPath(absPath 우선) 미러는, frontend folderPath가
      백엔드 저장 위치와 무관(원격/빈값)해도 항상 실제 파일을 가리킨다.

이 property들은 도구가 어느 _resolve_local_root 후보에 저장하든 성립해야 한다.
"""
import os
import sys
import json
import asyncio
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[3]))
sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "ai_engine"))

import server  # noqa: E402


# --- 프론트 resolveItemPath() 의 파이썬 미러 (main.js 수정본과 1:1 대응) -------
def resolve_item_path(item: dict, folder_path: str = "", workstation_cwd: str = "") -> str:
    abs_p = item.get("absPath")
    if isinstance(abs_p, str) and abs_p:
        return abs_p
    rel = item.get("path", "")
    if folder_path:
        return folder_path.rstrip("/") + "/" + rel
    if workstation_cwd:
        return workstation_cwd.rstrip("/") + "/" + rel
    return rel


# --- 각 도구를 호출하는 헬퍼 (project_path는 호출자가 지정) --------------------
def _call(tool_coro):
    return json.loads(asyncio.run(tool_coro))


def _gen_pptx(pp):
    return _call(server._tool_generate_pptx(
        {"title": "t", "slides": [{"title": "s", "bullets": ["a"]}]}, pp))


def _gen_pdf(pp):
    return _call(server._tool_generate_pdf(
        {"title": "t", "sections": [{"heading": "h", "body": "b"}]}, pp))


def _gen_xlsx(pp):
    return _call(server._tool_generate_xlsx(
        {"title": "t", "sheets": [{"name": "S1", "headers": ["a"], "rows": [["1"]]}]}, pp))


def _gen_docx(pp):
    return _call(server._tool_generate_docx(
        {"title": "t", "sections": [{"heading": "h", "body": "b"}]}, pp))


TOOLS = {
    "pptx": _gen_pptx,
    "pdf": _gen_pdf,
    "xlsx": _gen_xlsx,
    "docx": _gen_docx,
}


def _cleanup(res):
    ap = res.get("absPath")
    if ap and os.path.isfile(ap):
        try:
            os.remove(ap)
            if os.path.isfile(ap + ".meta.json"):
                os.remove(ap + ".meta.json")
        except OSError:
            pass


@pytest.fixture
def local_project(tmp_path):
    """쓰기 가능한 로컬 프로젝트 폴더 — _resolve_local_root이 1순위로 채택."""
    d = tmp_path / "proj"
    d.mkdir()
    return str(d)


@pytest.mark.unit
@pytest.mark.parametrize("kind", list(TOOLS.keys()))
def test_response_includes_abspath_pointing_to_real_file(kind, local_project):
    """P1+P2+P3 — 응답 absPath가 실재 파일(크기>0)을 가리키고 path와 파일명 일치."""
    res = TOOLS[kind](local_project)
    assert "error" not in res, f"{kind} 생성 실패: {res}"
    try:
        # P1
        assert "absPath" in res and res["absPath"], f"{kind}: absPath 누락"
        ap = res["absPath"]
        assert os.path.isabs(ap), f"{kind}: absPath가 절대경로 아님: {ap}"
        # P2
        assert os.path.isfile(ap), f"{kind}: absPath 파일 미존재: {ap}"
        assert os.path.getsize(ap) > 0, f"{kind}: absPath 0바이트: {ap}"
        # P3
        assert os.path.basename(ap) == os.path.basename(res.get("path", "")), \
            f"{kind}: absPath/path 파일명 불일치"
    finally:
        _cleanup(res)


@pytest.mark.unit
@pytest.mark.parametrize("kind", list(TOOLS.keys()))
@pytest.mark.parametrize("frontend_folder,workstation_cwd", [
    ("/Users/jcg/agentic-editor", "/Users/jcg/.agentic-editor"),  # 로컬폴더 일치
    ("/fsx/home/cgjang/proj", "/Users/jcg/.agentic-editor"),      # 원격세션 (수정전 깨짐)
    ("", "/Users/jcg/.agentic-editor"),                           # 폴더미오픈 (수정전 깨짐)
    ("", ""),                                                     # 최악: 정보 전무
])
def test_card_resolves_to_real_file_regardless_of_frontend_path(
        kind, frontend_folder, workstation_cwd, local_project):
    """P4 — frontend folderPath가 백엔드 저장 위치와 갈려도 카드가 실제 파일을 가리킴.

    백엔드는 local_project(쓰기가능 로컬)에 저장하므로 absPath는 local_project 하위.
    frontend_folder가 전혀 다른 값(원격/빈값)이어도 resolveItemPath는 absPath 우선
    이므로 항상 실재 파일을 가리켜야 한다. 이것이 수정 전 깨지던 핵심 케이스다.
    """
    res = TOOLS[kind](local_project)
    assert "error" not in res, f"{kind} 생성 실패: {res}"
    try:
        resolved = resolve_item_path(res, frontend_folder, workstation_cwd)
        assert os.path.isfile(resolved), (
            f"{kind}: 카드 경로가 실재 파일을 못 가리킴 "
            f"(frontend={frontend_folder!r}): {resolved}"
        )
        assert os.path.getsize(resolved) > 0
        # 핵심: absPath 우선이므로 frontend_folder와 무관하게 동일 경로
        assert resolved == res["absPath"]
    finally:
        _cleanup(res)


@pytest.mark.unit
def test_legacy_response_without_abspath_falls_back(local_project):
    """하위호환 — absPath 없는 (구버전/외부) 응답도 folderPath 폴백으로 동작."""
    # absPath를 일부러 제거한 응답 시뮬레이션
    legacy = {"path": ".generated/foo.pptx", "model": "python-pptx"}
    # folderPath 폴백
    r1 = resolve_item_path(legacy, "/some/folder", "/ws")
    assert r1 == "/some/folder/.generated/foo.pptx"
    # workstation_cwd 폴백
    r2 = resolve_item_path(legacy, "", "/ws/root")
    assert r2 == "/ws/root/.generated/foo.pptx"
    # 둘 다 없으면 상대경로 그대로 (최후)
    r3 = resolve_item_path(legacy, "", "")
    assert r3 == ".generated/foo.pptx"


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
