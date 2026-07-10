"""GraphState reducer 회귀/속성 테스트.

검증 대상:
- _merge_verified_files: absPath 기준 dedup, left 순서 보존, 입력 불변 (Property 3 관련).
- visited_routes / messages reducer 부착 확인.

gateway·네트워크 불필요, 유한 시간(hypothesis 예제 상한).
실행: ai_engine/.venv/bin/python -m pytest scripts/test_langgraph_graph_state.py -q
"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from hypothesis import given, settings, strategies as st

from ai_engine.agent_system.graph_state import _merge_verified_files


def _vf(abs_path: str, tool: str = "generate_pptx"):
    return {"path": abs_path.split("/")[-1], "absPath": abs_path, "tool": tool}


def test_merge_none_inputs():
    assert _merge_verified_files(None, None) == []
    assert _merge_verified_files(None, [_vf("/a.pptx")]) == [_vf("/a.pptx")]
    assert _merge_verified_files([_vf("/a.pptx")], None) == [_vf("/a.pptx")]


def test_merge_dedup_by_abspath():
    left = [_vf("/x/a.png"), _vf("/x/b.png")]
    right = [_vf("/x/b.png"), _vf("/x/c.png")]  # b 중복
    out = _merge_verified_files(left, right)
    paths = [vf["absPath"] for vf in out]
    assert paths == ["/x/a.png", "/x/b.png", "/x/c.png"]  # 순서 보존 + 중복 제거


def test_merge_does_not_mutate_inputs():
    left = [_vf("/x/a.png")]
    right = [_vf("/x/b.png")]
    _merge_verified_files(left, right)
    assert left == [_vf("/x/a.png")]  # 불변
    assert right == [_vf("/x/b.png")]


@settings(max_examples=100, deadline=None)
@given(
    left_paths=st.lists(st.text(min_size=1, max_size=8), max_size=10),
    right_paths=st.lists(st.text(min_size=1, max_size=8), max_size=10),
)
def test_merge_result_abspaths_unique(left_paths, right_paths):
    """Property: 병합 결과의 absPath 는 항상 유일하다(dedup 불변식)."""
    left = [_vf("/" + p) for p in left_paths]
    right = [_vf("/" + p) for p in right_paths]
    out = _merge_verified_files(left, right)
    abs_paths = [vf["absPath"] for vf in out]
    assert len(abs_paths) == len(set(abs_paths))


@settings(max_examples=100, deadline=None)
@given(paths=st.lists(st.text(min_size=1, max_size=6), min_size=1, max_size=8))
def test_merge_superset_and_order(paths):
    """Property: 결과는 left 를 접두로 보존하고 두 입력의 합집합을 담는다."""
    left = [_vf("/" + p) for p in paths]
    out = _merge_verified_files(left, [])
    # left 의 유일 absPath 순서가 그대로 보존
    seen = []
    for vf in left:
        if vf["absPath"] not in seen:
            seen.append(vf["absPath"])
    assert [vf["absPath"] for vf in out] == seen


def test_reducers_attached():
    """GraphState 의 messages/visited_routes/verified_files 에 reducer 가 부착돼 있다."""
    import typing
    from ai_engine.agent_system import graph_state as GS
    # from __future__ import annotations 로 인해 annotation 이 문자열이므로
    # include_extras=True 로 실제 Annotated 객체를 복원해 __metadata__(reducer)를 확인한다.
    hints = typing.get_type_hints(GS.GraphState, include_extras=True)
    for field in ("messages", "visited_routes", "verified_files"):
        assert field in hints, field
        assert hasattr(hints[field], "__metadata__"), f"{field} reducer 미부착"
        assert hints[field].__metadata__, f"{field} reducer 비어있음"


if __name__ == "__main__":
    import pytest
    raise SystemExit(pytest.main([__file__, "-q"]))
