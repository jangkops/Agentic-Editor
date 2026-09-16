"""대화 요약 체크포인트 영속화 (원장 #2).

예전에는 server.py 가 `get_memory()` 를 인자 없이 불러 `storage_dir=""` → 요약이 프로세스 메모리에만 있었고
사이드카 재기동 때 사라졌다. 이제 `_conversation_memory_dir()` 가 저장 위치를 정하고, 저장 전 비밀 패턴을 지우고,
0600 권한으로 원자적으로 기록한다.

테스트용 가짜 비밀은 리터럴로 두지 않고 조각을 이어 만든다(시크릿 스캐너 오탐 방지 — 실제 값이 아니다).
"""
import json
import os
import stat

import pytest

import ai_engine.server as s
from ai_engine.rag import conversation_memory as cm
from ai_engine.rag.conversation_memory import ConversationCheckpoint, ConversationMemory, scrub_secrets

FAKE_AWS_KEY = "AKIA" + "ABCDEFGHIJKLMNOP"                       # 형태만 맞춘 가짜
FAKE_GH_TOKEN = "ghp_" + "abcdefghijklmnopqrstuvwxyz0123456789"  # 형태만 맞춘 가짜
FAKE_PEM = "-----BEGIN " + "PRIVATE KEY-----\nMIIE\n-----END " + "PRIVATE KEY-----"
FAKE_API_KEY_LINE = "api_key = '" + "sk_" + "live_abcdefghijklmnopqrstuv" + "'"


def test_memory_dir_resolution(monkeypatch):
    monkeypatch.delenv("AE_MEMORY_DIR", raising=False)
    monkeypatch.setenv("AE_GENERATED_ROOT", "/tmp/ud/generated")
    assert s._conversation_memory_dir() == os.path.join("/tmp/ud", "memory")          # Electron: userData/memory
    monkeypatch.setenv("AE_GENERATED_ROOT", "/tmp/ud/generated/")
    assert s._conversation_memory_dir() == os.path.join("/tmp/ud", "memory")          # 후행 슬래시 무시
    monkeypatch.delenv("AE_GENERATED_ROOT", raising=False)
    assert s._conversation_memory_dir() == os.path.join(os.path.expanduser("~/.agentic-editor"), "memory")
    monkeypatch.setenv("AE_MEMORY_DIR", "/tmp/custom-mem")
    assert s._conversation_memory_dir() == "/tmp/custom-mem"                           # 명시 env 최우선


def test_build_messages_passes_storage_dir(monkeypatch, tmp_path):
    seen = []

    def _recorder(storage_dir=""):
        seen.append(storage_dir)
        return ConversationMemory(storage_dir=storage_dir)
    monkeypatch.setattr(cm, "get_memory", _recorder)
    monkeypatch.setenv("AE_MEMORY_DIR", str(tmp_path / "mem"))
    msgs = s._build_messages([{"role": "user", "content": "이전 질문"}], "지금 질문", "sess1")
    assert seen == [str(tmp_path / "mem")]
    assert msgs and msgs[-1]["role"] == "user"


def test_checkpoint_round_trip_survives_a_new_instance(tmp_path):
    d = str(tmp_path / "memory")
    m1 = ConversationMemory(storage_dir=d)
    cp = ConversationCheckpoint(session_id="s1", summary="요약 본문", message_count=12, last_updated=1.0, key_facts=["사실 A"])
    m1.save_checkpoint(cp)
    p = os.path.join(d, "conv_s1.json")
    assert os.path.exists(p) and not os.path.exists(p + ".tmp")
    assert stat.S_IMODE(os.stat(p).st_mode) == 0o600
    m2 = ConversationMemory(storage_dir=d)                      # 재기동 시뮬레이션
    loaded = m2.load_checkpoint("s1")
    assert loaded is not None
    assert loaded.summary == "요약 본문" and loaded.key_facts == ["사실 A"] and loaded.message_count == 12


def test_secrets_are_scrubbed_before_persisting(tmp_path):
    d = str(tmp_path / "memory")
    m = ConversationMemory(storage_dir=d)
    leaky = f"사용자가 {FAKE_AWS_KEY} 와 {FAKE_GH_TOKEN} 를 붙였고 {FAKE_PEM} 도 공유했다. {FAKE_API_KEY_LINE}"
    m.save_checkpoint(ConversationCheckpoint(session_id="s2", summary=leaky, key_facts=["token: abcdefghijklmnop1234567890"]))
    raw = open(os.path.join(d, "conv_s2.json"), encoding="utf-8").read()
    for secret in (FAKE_AWS_KEY, FAKE_GH_TOKEN, "BEGIN " + "PRIVATE KEY", "abcdefghijklmnop1234567890", "live_abcdefghijklmnopqrstuv"):
        assert secret not in raw
    data = json.loads(raw)
    assert "[REDACTED]" in data["summary"] and data["key_facts"] == ["[REDACTED]"]
    assert "사용자가" in data["summary"]                          # 비밀이 아닌 본문은 보존


@pytest.mark.parametrize("text", ["", "평범한 문장", "짧은 키 sk-abc"])
def test_scrub_leaves_ordinary_text_alone(text):
    assert scrub_secrets(text) == text


def test_memory_without_storage_dir_stays_in_memory_only(tmp_path):
    m = ConversationMemory(storage_dir="")
    m.save_checkpoint(ConversationCheckpoint(session_id="x", summary="s"))
    assert m.load_checkpoint("x").summary == "s"
    assert not list(tmp_path.iterdir())
