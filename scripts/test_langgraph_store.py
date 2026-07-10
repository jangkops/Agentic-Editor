"""JsonFileStore(세션 간 장기 메모리) 회귀 테스트.

검증: put→JSON 파일 영속→새 인스턴스 재로드(재시작 시뮬), .json 전용 저장, search/get.
gateway·네트워크 불필요, tmp_path 격리, 유한 시간.
실행: ai_engine/.venv/bin/python -m pytest scripts/test_langgraph_store.py -q
"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from ai_engine.agent_system.store import JsonFileStore


def test_put_get_roundtrip(tmp_path):
    s = JsonFileStore(base_dir=str(tmp_path))
    s.put(("memories", "u1"), "name", {"text": "홍길동"})
    it = s.get(("memories", "u1"), "name")
    assert it is not None and it.value["text"] == "홍길동"


def test_persist_and_reload_across_restart(tmp_path):
    s1 = JsonFileStore(base_dir=str(tmp_path))
    s1.put(("memories", "u1"), "a", {"text": "이름은 김철수"})
    s1.put(("memories", "u1"), "b", {"text": "선호 언어 러스트"})
    # 새 인스턴스(프로세스 재시작 시뮬) — 파일에서 로드
    s2 = JsonFileStore(base_dir=str(tmp_path))
    res = s2.search(("memories", "u1"))
    texts = sorted(r.value["text"] for r in res)
    assert texts == ["선호 언어 러스트", "이름은 김철수"], texts


def test_json_only_no_sqlite(tmp_path):
    s = JsonFileStore(base_dir=str(tmp_path))
    s.put(("memories", "u1"), "k", {"text": "v"})
    files = []
    for _root, _dirs, fs in os.walk(tmp_path):
        files.extend(fs)
    assert files, "저장 파일 없음"
    assert all(f.endswith(".json") for f in files), files
    assert not any(f.endswith((".sqlite", ".db", ".sqlite3")) for f in files)


def test_missing_namespace_returns_empty(tmp_path):
    s = JsonFileStore(base_dir=str(tmp_path))
    assert s.search(("memories", "nobody")) == []
    assert s.get(("memories", "nobody"), "x") is None


if __name__ == "__main__":
    import pytest
    raise SystemExit(pytest.main([__file__, "-q"]))
