"""품질 저장소 + deferred 검증 + verify_mode 검증.

실행: ./ai_engine/.venv/bin/python -m pytest scripts/test_quality_store_deferred_pbt.py -p no:cacheprovider -q
"""
import asyncio
import os
from ai_engine.rag import quality_store as qs
from ai_engine.rag.answer_quality import verify_mode, run_deferred_verification


def _env(tmp_path):
    return {"AE_QUALITY_ROOT": str(tmp_path), "AE_ANSWER_QUALITY": "1"}


def test_save_and_load_roundtrip(tmp_path):
    env = _env(tmp_path)
    assert qs.save_quality("sess1", "m1", {"citation": {"verified": 2}}, env=env)
    got = qs.get_quality("sess1", "m1", env=env)
    assert got and got["citation"]["verified"] == 2
    assert "updatedAt" in got


def test_missing_message_id_not_saved(tmp_path):
    env = _env(tmp_path)
    assert qs.save_quality("sess1", "", {"x": 1}, env=env) is False
    assert qs.load_quality("sess1", env=env) == {}


def test_session_isolation_and_pathsafety(tmp_path):
    env = _env(tmp_path)
    qs.save_quality("a/b/../c", "m1", {"v": 1}, env=env)   # 경로 이탈 문자 포함
    qs.save_quality("other", "m2", {"v": 2}, env=env)
    # 파일이 quality_dir 안에만 생성돼야 함
    files = os.listdir(str(tmp_path))
    assert all(f.endswith(".json") for f in files)
    assert qs.get_quality("a/b/../c", "m1", env=env)["v"] == 1
    assert qs.get_quality("other", "m1", env=env) is None


def test_load_corrupt_file_returns_empty(tmp_path):
    env = _env(tmp_path)
    p = os.path.join(str(tmp_path), "sess1.json")
    with open(p, "w") as f:
        f.write("{ not json")
    assert qs.load_quality("sess1", env=env) == {}


def test_verify_mode_off_when_master_off():
    assert verify_mode({}) == "off"
    assert verify_mode({"AE_VERIFY_MODE": "deferred"}) == "off"  # 마스터 off


def test_verify_mode_values():
    assert verify_mode({"AE_ANSWER_QUALITY": "1"}) == "inline"  # 기본 inline
    assert verify_mode({"AE_ANSWER_QUALITY": "1", "AE_VERIFY_MODE": "deferred"}) == "deferred"
    assert verify_mode({"AE_ANSWER_QUALITY": "1", "AE_VERIFY_MODE": "junk"}) == "inline"


def test_run_deferred_persists_result(tmp_path):
    env = _env(tmp_path)  # 마스터 on, AE_VERIFY off → citation만(게이트웨이 불요)
    meta = asyncio.run(run_deferred_verification(
        answer="rrf_fuse는 k=60 (ai_engine/rag/hybrid_search.py:200-220)",
        context_text="", retrieved_chunks=None, gw=None,
        session_id="s1", message_id="msg1", env=env))
    assert meta.get("mode") == "deferred"
    # 저장소에 기록됐는지
    stored = qs.get_quality("s1", "msg1", env=env)
    assert stored is not None and stored.get("mode") == "deferred"


def test_run_deferred_never_raises_on_bad_gw(tmp_path):
    env = _env(tmp_path)
    env["AE_VERIFY"] = "1"

    class _BoomGW:
        async def converse(self, model_id, messages, system_prompt="", tool_config=None):
            raise RuntimeError("down")

    meta = asyncio.run(run_deferred_verification(
        answer="답변", context_text="근거", retrieved_chunks=None, gw=_BoomGW(),
        session_id="s2", message_id="m2", env=env))
    # 예외로 죽지 않고 저장까지 완료
    assert qs.get_quality("s2", "m2", env=env) is not None
