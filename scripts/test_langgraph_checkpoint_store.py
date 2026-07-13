"""JsonFileCheckpointSaver 회귀/속성 테스트.

검증 대상 (요구사항 4.2/4.3/8.3, Property 5/8):
- 자격증명 감지: 키 이름 문자열은 통과(오탐 없음), 실제 AKIA/ASIA 값은 차단(정탐).
- 저장은 .json 파일로만(SQLite 미사용).
- put→get_tuple 왕복으로 checkpoint 복원.
- Property 5(hypothesis): 다양한 thread_id/checkpoint/state 입력에서 put/put_writes/
  get_tuple/list(+ async 위임)가 생성하는 파일은 오직 .json 이며 SQLite(.db/.sqlite)를
  절대 쓰지 않고, 저장 경로가 base_dir(userData/checkpoints/langgraph) 하위로 한정된다.

gateway·네트워크 불필요. 파일시스템은 tempfile 로 격리, 유한 시간.
실행: ai_engine/.venv/bin/python -m pytest scripts/test_langgraph_checkpoint_store.py -q
"""
import os
import re
import sys
import uuid
import asyncio
import tempfile

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import pytest
from hypothesis import given, settings, strategies as st

from ai_engine.agent_system import checkpoint_store as _cp_module
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


# ─────────────────────────────────────────────────────────────────────────────
# Property 5: checkpointer는 JSON 파일만 사용 (SQLite 금지)
# Validates: Requirements 4.2, 4.3
#
# design.md 명세:
#   assert no_sqlite_import(checkpoint_module)
#   assert all(p.endswith(".json") for p in checkpoint_files)
#   assert checkpoint_base_dir.startswith(userdata_dir)
# ─────────────────────────────────────────────────────────────────────────────

# 파일 경로/디렉터리명으로 안전한 식별자(경로 조작/구분자 배제)
_SAFE_ID = st.text(
    alphabet="abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789-_",
    min_size=1,
    max_size=24,
)

# 자격증명 값 오탐(AKIA/ASIA 패턴)을 피하기 위해 소문자·숫자·공백으로 제한한 상태 텍스트
_STATE_TEXT = st.text(
    alphabet="abcdefghijklmnopqrstuvwxyz0123456789 .,:/",
    min_size=0,
    max_size=60,
)

_SQLITE_EXTS = (".sqlite", ".db", ".sqlite3", ".sqlite-journal", ".db-wal", ".db-shm")


def _all_files(base: str) -> list[str]:
    out = []
    for root, _dirs, files in os.walk(base):
        for f in files:
            out.append(os.path.join(root, f))
    return out


def _mk_checkpoint_dyn(cid: str, ts: str, channel_values: dict) -> dict:
    return {
        "v": 1,
        "id": cid,
        "ts": ts,
        "channel_values": channel_values,
        "channel_versions": {},
        "versions_seen": {},
        "pending_sends": [],
    }


def _assert_json_only(base_dir: str):
    """base_dir 하위 산출 파일이 전부 .json 이고 SQLite 계열이 하나도 없음을 검증."""
    files = _all_files(base_dir)
    assert files, "체크포인트 파일이 하나도 생성되지 않음"
    abs_base = os.path.abspath(base_dir)
    for p in files:
        # 저장 경로는 반드시 base_dir(userData/checkpoints/langgraph) 하위로 한정 (요구사항 4.3)
        assert os.path.abspath(p).startswith(abs_base), p
        # 오직 .json 확장자 (요구사항 4.2)
        assert p.endswith(".json"), p
        # SQLite/기타 저장 포맷 절대 미사용
        assert not p.endswith(_SQLITE_EXTS), p


def test_no_sqlite_import_in_module():
    """Property 5: checkpoint 모듈은 sqlite 를 import 하지 않는다.

    docstring/주석의 'SQLite 금지' 문구는 무시하고 실제 import 구문만 검사한다.
    """
    src = ""
    with open(_cp_module.__file__, "r", encoding="utf-8") as f:
        src = f.read()
    # 코드 라인에서 sqlite import 패턴 탐지 (주석/문자열 제외를 위해 라인 앞부분만 검사)
    import_re = re.compile(r"^\s*(?:import|from)\s+[\w.]*sqlite", re.IGNORECASE)
    offenders = [ln for ln in src.splitlines() if import_re.search(ln)]
    assert not offenders, f"sqlite import 발견: {offenders}"
    # 런타임 로드된 모듈 네임스페이스에도 sqlite3 모듈이 붙어있지 않음
    assert not hasattr(_cp_module, "sqlite3")


@settings(max_examples=80, deadline=None)
@given(
    thread_id=_SAFE_ID,
    checkpoint_id=_SAFE_ID,
    ns=st.sampled_from(["", "sub", "ns_1"]),
    step=st.integers(min_value=0, max_value=50),
    messages=st.lists(_STATE_TEXT, max_size=6),
)
def test_property5_put_get_list_only_json(thread_id, checkpoint_id, ns, step, messages):
    """Property 5: put→put_writes→get_tuple→list 전 과정에서 .json 파일만 생성된다."""
    with tempfile.TemporaryDirectory() as base_dir:
        saver = JsonFileCheckpointSaver(base_dir)
        # base_dir 하위로 저장 경로가 한정되는지 (요구사항 4.3)
        assert os.path.abspath(saver.base_dir).startswith(os.path.abspath(base_dir))

        cfg = {"configurable": {"thread_id": thread_id, "checkpoint_ns": ns}}
        checkpoint = _mk_checkpoint_dyn(
            checkpoint_id, "2026-01-01T00:00:00+00:00", {"messages": messages}
        )
        new_cfg = saver.put(cfg, checkpoint, {"source": "test", "step": step}, {})

        # put_writes (중간 write) — put 이 반환한 config(checkpoint_id 포함) 사용
        saver.put_writes(
            new_cfg, [("messages", messages), ("route", "coding")], task_id="task-1"
        )

        # get_tuple / list 는 읽기지만 파일을 추가로 만들지 않아야 함
        tup = saver.get_tuple(new_cfg)
        assert tup is not None
        assert tup.checkpoint["id"] == checkpoint_id
        listed = list(saver.list(cfg))
        assert any(t.checkpoint["id"] == checkpoint_id for t in listed)

        _assert_json_only(base_dir)


@settings(max_examples=50, deadline=None)
@given(
    thread_id=_SAFE_ID,
    checkpoint_id=_SAFE_ID,
    messages=st.lists(_STATE_TEXT, max_size=6),
)
def test_property5_async_delegation_only_json(thread_id, checkpoint_id, messages):
    """Property 5: async 위임(aput/aput_writes/aget_tuple/alist)도 .json 만 생성한다."""

    async def _run(base_dir: str):
        saver = JsonFileCheckpointSaver(base_dir)
        cfg = {"configurable": {"thread_id": thread_id, "checkpoint_ns": ""}}
        checkpoint = _mk_checkpoint_dyn(
            checkpoint_id, "2026-02-02T00:00:00+00:00", {"messages": messages}
        )
        new_cfg = await saver.aput(cfg, checkpoint, {"source": "async", "step": 1}, {})
        await saver.aput_writes(new_cfg, [("messages", messages)], task_id="t-async")
        tup = await saver.aget_tuple(new_cfg)
        assert tup is not None and tup.checkpoint["id"] == checkpoint_id
        collected = [t async for t in saver.alist(cfg)]
        assert any(t.checkpoint["id"] == checkpoint_id for t in collected)

    with tempfile.TemporaryDirectory() as base_dir:
        asyncio.run(_run(base_dir))
        _assert_json_only(base_dir)


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-q"]))
