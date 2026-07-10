"""JsonFileCheckpointSaver 회귀/속성 테스트.

검증 대상 (요구사항 4.2/4.3/8.3, Property 5/8):
- 자격증명 감지: 키 이름 문자열은 통과(오탐 없음), 실제 AKIA/ASIA 값은 차단(정탐).
- 저장은 .json 파일로만(SQLite 미사용).
- put→get_tuple 왕복으로 checkpoint 복원.

gateway·네트워크 불필요. 파일시스템은 tmp_path 로 격리, 유한 시간.
실행: ai_engine/.venv/bin/python -m pytest scripts/test_langgraph_checkpoint_store.py -q
"""
import os
import sys
import uuid

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import pytest
from hypothesis import given, settings, strategies as st

from ai_engine.agent_system.checkpoint_store import (
    JsonFileCheckpointSaver,
    CredentialLeakError,
)


# ── 자격증명 감지: 오탐 방지 ──
def test_key_name_strings_pass():
    """코드/문서에 흔한 키 '이름' 문자열은 차단하지 않는다(오탐 방지)."""
    JsonFileCheckpointSaver._assert_no_credentials(
        {"msg": "const accessKeyId = cfg.accessKeyId; // secretAccessKey field"}
    )
    JsonFileCheckpointSaver._assert_no_credentials(
        {"doc": "settings.json 에는 accessKeyId/secretAccessKey 를 저장하지 않는다"}
    )


# ── 자격증명 감지: 정탐 ──
@pytest.mark.parametrize("real_key", [
    "AKIAIOSFODNN7EXAMPLE",
    "ASIAIOSFODNN7EXAMPLE",
])
def test_real_access_key_blocked(real_key):
    with pytest.raises(CredentialLeakError):
        JsonFileCheckpointSaver._assert_no_credentials({"k": real_key})


@settings(max_examples=60, deadline=None)
@given(word=st.text(alphabet="abcdefghijklmnopqrstuvwxyz_ ", min_size=1, max_size=40))
def test_arbitrary_text_never_false_positive(word):
    """Property: 소문자/언더스코어 텍스트는 절대 자격증명으로 오탐되지 않는다."""
    # 예외가 없어야 정상
    JsonFileCheckpointSaver._assert_no_credentials({"text": word + " accessKeyId secretAccessKey"})


# ── .json 저장 + 왕복 ──
def _mk_config(thread_id: str):
    return {"configurable": {"thread_id": thread_id, "checkpoint_ns": ""}}


def _mk_checkpoint(cid: str):
    return {
        "v": 1,
        "id": cid,
        "ts": "2026-01-01T00:00:00+00:00",
        "channel_values": {"messages": []},
        "channel_versions": {},
        "versions_seen": {},
        "pending_sends": [],
    }


def test_saves_only_json_and_roundtrips(tmp_path):
    saver = JsonFileCheckpointSaver(str(tmp_path))
    thread_id = "t-" + uuid.uuid4().hex[:8]
    cid = "c-" + uuid.uuid4().hex[:8]
    cfg = _mk_config(thread_id)
    saver.put(cfg, _mk_checkpoint(cid), {"source": "test", "step": 1}, {})

    # 저장된 파일이 전부 .json (SQLite/기타 확장자 없음)
    written = []
    for root, _dirs, files in os.walk(tmp_path):
        for f in files:
            written.append(f)
    assert written, "체크포인트 파일이 하나도 없음"
    assert all(f.endswith(".json") for f in written), written
    assert not any(f.endswith((".sqlite", ".db", ".sqlite3")) for f in written)

    # 왕복 복원
    tup = saver.get_tuple(cfg)
    assert tup is not None
    assert tup.checkpoint["id"] == cid


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-q"]))
