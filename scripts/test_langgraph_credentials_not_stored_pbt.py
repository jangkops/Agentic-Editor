"""Property 8: 자격증명 미저장 — state/checkpoint 어디에도 AWS 자격증명이 없음.

Task 1.9 산출물.
**Validates: Requirements 8.2, 8.3**

검증 속성(design.md Property 8):
- 8.1: GraphState 스키마에는 자격증명 필드(accessKeyId/secretAccessKey/sessionToken/
       aws_access_key_id 등)가 없고, profile name(aws_profile) / bedrock_user 같은
       문자열 식별자만 존재한다.
- 8.2: 그래프 상태가 직렬화되면 그 결과 어디에도 실제 AWS 자격증명 값이 없다.
- 8.3: 체크포인트 파일이 기록되면 저장된 어떤 파일에도 실제 AWS 자격증명 값이 없다.

접근:
- hypothesis 로 "허용된 필드(profile name / bedrock_user / 벤치 텍스트)"만으로 다양한
  GraphState 채널값을 생성 → JsonFileCheckpointSaver 로 저장 → 저장된 .json 파일을
  **원문(raw)으로 스캔** + **디코드(base64 msgpack)해서 재스캔** 하여 자격증명 값 패턴이
  없음을 확인한다(positive property).
- 네거티브 컨트롤: (a) 스캐너가 실제 AKIA/ASIA Access Key ID 값을 탐지하는지(체커 soundness),
  (b) 실제 자격증명 값이 상태에 섞이면 saver 가 저장을 차단(CredentialLeakError)하는지 확인해
  "검사기가 정말로 유출을 잡아낸다"는 정합성을 증명한다.

fake fs(tmp_path)만 사용, 네트워크·gateway 불필요, 유한 시간(예제 상한).
실행:
  ai_engine/.venv/bin/python -m pytest scripts/test_langgraph_credentials_not_stored_pbt.py -q
"""
import base64
import json
import os
import re
import sys
import typing
import uuid

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import pytest
from hypothesis import given, settings, strategies as st
from langchain_core.messages import HumanMessage, AIMessage

from ai_engine.agent_system import graph_state as GS
from ai_engine.agent_system.checkpoint_store import (
    JsonFileCheckpointSaver,
    CredentialLeakError,
)


# ─────────────────────────── 자격증명 스캐너 (테스트 측) ───────────────────────────
# 저장된 산출물 텍스트에서 "실제 AWS 자격증명 값" 패턴을 찾는다.
#  - Access Key ID: (AKIA|ASIA) + 16 대문자/숫자  — 실제 키 값의 명확한 서명.
_ACCESS_KEY_ID_RE = re.compile(r"(?:AKIA|ASIA)[A-Z0-9]{16}")

# 네거티브 컨트롤에서 상태에 심는 알려진 자격증명 값들(테스트 픽스처, 실제 키 아님).
_KNOWN_ACCESS_KEY_ID = "AKIAIOSFODNN7EXAMPLE"
_KNOWN_SECRET = "wJalrXUtnFEMI/K7MDENG/bPxRfiCYEXAMPLEKEY"
_KNOWN_SESSION_TOKEN = "FQoGZXIvYXdzEExampleSessionTokenValue1234567890"


def _scan_credential_values(text: str) -> list:
    """텍스트에서 실제 자격증명 값 패턴을 모두 찾아 반환(없으면 빈 리스트)."""
    return _ACCESS_KEY_ID_RE.findall(text or "")


def _contains_known_planted(text: str) -> bool:
    """네거티브 컨트롤에서 심은 알려진 값(리터럴)이 텍스트에 그대로 노출됐는지."""
    t = text or ""
    return (
        _KNOWN_ACCESS_KEY_ID in t
        or _KNOWN_SECRET in t
        or _KNOWN_SESSION_TOKEN in t
    )


# ─────────────────────────── 체크포인트/설정 헬퍼 ───────────────────────────
def _mk_config(thread_id: str):
    return {"configurable": {"thread_id": thread_id, "checkpoint_ns": ""}}


def _mk_checkpoint(cid: str, channel_values: dict):
    return {
        "v": 1,
        "id": cid,
        "ts": "2026-01-01T00:00:00+00:00",
        "channel_values": channel_values,
        "channel_versions": {},
        "versions_seen": {},
        "pending_sends": [],
    }


def _read_all_checkpoint_files(base_dir) -> list:
    """base_dir 하위 모든 .json 체크포인트 파일의 (path, raw_text) 목록."""
    out = []
    for root, _dirs, files in os.walk(base_dir):
        for f in files:
            if f.endswith(".json"):
                p = os.path.join(root, f)
                with open(p, "r", encoding="utf-8") as fh:
                    out.append((p, fh.read()))
    return out


def _decode_checkpoint_texts(raw_text: str) -> list:
    """저장 레코드의 base64(msgpack) 인코딩 블록들을 디코드해 원문 바이트/텍스트 목록으로.

    파일 원문은 base64 로 감싸져 있어 평문 자격증명이 그대로 보이지 않으므로, 실제 저장된
    내용(checkpoint/metadata/writes)을 디코드해 그 안에도 자격증명이 없음을 검증한다.
    """
    texts = []
    record = json.loads(raw_text)
    for key in ("checkpoint", "metadata", "new_versions"):
        block = record.get(key)
        if isinstance(block, dict) and "data" in block:
            try:
                texts.append(base64.b64decode(block["data"]).decode("latin-1"))
            except Exception:
                pass
    for w in record.get("writes", []):
        block = w.get("value")
        if isinstance(block, dict) and "data" in block:
            try:
                texts.append(base64.b64decode(block["data"]).decode("latin-1"))
            except Exception:
                pass
    return texts


# ─────────────────────────── 벤치(자격증명 아님) 채널값 생성 ───────────────────────────
# 허용된 문자열 식별자만: profile name / bedrock_user 는 사람이 붙이는 이름 문자열.
_profile_names = st.sampled_from(
    ["default", "bedrock-dev", "prod-sso", "team_a", "jcg", "us-west-2-role"]
)
_bedrock_users = st.sampled_from(
    ["alice", "bob", "jcg", "service-account", "team-bedrock"]
)
_benign_text = st.text(
    alphabet="abcdefghijklmnopqrstuvwxyz ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789 .,_/-",
    min_size=0,
    max_size=60,
)


@st.composite
def _benign_channel_values(draw):
    """자격증명이 전혀 없는, 허용 필드 기반 GraphState 채널값 생성."""
    n_msgs = draw(st.integers(min_value=0, max_value=4))
    messages = []
    for _ in range(n_msgs):
        content = draw(_benign_text)
        if draw(st.booleans()):
            messages.append(HumanMessage(content=content))
        else:
            messages.append(AIMessage(content=content))
    return {
        "prompt": draw(_benign_text),
        "session_id": draw(_benign_text),
        "project_path": draw(_benign_text),
        "aws_profile": draw(_profile_names),      # 프로파일 *이름* 만
        "bedrock_user": draw(_bedrock_users),     # 사용자 *이름* 만
        "system_prompt": draw(_benign_text),
        "messages": messages,
        "visited_routes": draw(st.lists(st.sampled_from(
            ["coding", "media", "research", "ops", "chat"]), max_size=4)),
        "final_text": draw(_benign_text),
    }


# ═══════════════════════════ 8.1 스키마 속성 ═══════════════════════════
def test_graphstate_schema_has_no_credential_fields():
    """8.1: GraphState 필드에 자격증명 이름이 없고, profile/bedrock_user 식별자만 존재."""
    hints = typing.get_type_hints(GS.GraphState, include_extras=True)
    field_names = set(hints.keys())

    forbidden = {
        "accessKeyId", "secretAccessKey", "sessionToken",
        "aws_access_key_id", "aws_secret_access_key", "aws_session_token",
        "access_key", "secret_key", "credentials", "aws_credentials",
    }
    leaked = field_names & forbidden
    assert not leaked, f"GraphState 에 자격증명 필드 존재: {leaked}"

    # 허용된 식별자 필드는 반드시 존재(문자열 이름만 담는 채널)
    assert "aws_profile" in field_names
    assert "bedrock_user" in field_names


# ═══════════════════════════ 네거티브 컨트롤: 스캐너 soundness ═══════════════════════════
@pytest.mark.parametrize("real_key", [_KNOWN_ACCESS_KEY_ID, "ASIAIOSFODNN7EXAMPLE"])
def test_scanner_detects_real_access_key(real_key):
    """체커 정합성: 실제 Access Key ID 값이 텍스트에 있으면 스캐너가 반드시 탐지한다."""
    hits = _scan_credential_values(f"credentials block: {real_key} end")
    assert real_key in hits


def test_scanner_ignores_profile_names_and_field_names():
    """체커 specificity: profile name / 키 '이름' 문자열은 자격증명으로 오탐하지 않는다."""
    benign = "aws_profile=prod-sso bedrock_user=alice accessKeyId secretAccessKey field"
    assert _scan_credential_values(benign) == []
    assert not _contains_known_planted(benign)


# ═══════════════════════ 8.2/8.3 positive property (hypothesis) ═══════════════════════
@settings(max_examples=80, deadline=None)
@given(channel_values=_benign_channel_values())
def test_no_credentials_in_saved_checkpoint(channel_values, tmp_path_factory):
    """8.2/8.3: 허용 필드만으로 만든 상태를 저장하면, 저장된 파일 원문/디코드 결과 어디에도
    실제 자격증명 값이 없다."""
    base_dir = tmp_path_factory.mktemp("ckpt")
    saver = JsonFileCheckpointSaver(str(base_dir))
    thread_id = "t-" + uuid.uuid4().hex[:8]
    cid = "c-" + uuid.uuid4().hex[:8]
    cfg = _mk_config(thread_id)

    saver.put(cfg, _mk_checkpoint(cid, channel_values),
              {"source": "test", "step": 1, "writes": {}}, {})

    files = _read_all_checkpoint_files(base_dir)
    assert files, "체크포인트 파일이 기록되지 않음"

    for path, raw in files:
        # (a) 파일 원문에 자격증명 값 없음
        assert _scan_credential_values(raw) == [], f"원문에 자격증명 값: {path}"
        # (b) 디코드된 실제 저장 내용에도 자격증명 값 없음
        for decoded in _decode_checkpoint_texts(raw):
            assert _scan_credential_values(decoded) == [], (
                f"디코드 내용에 자격증명 값: {path}")


# ═══════════════════ 네거티브 컨트롤: 유출 시 saver 가 차단(guard soundness) ═══════════════════
def test_saver_blocks_when_access_key_leaks_into_state(tmp_path):
    """실제 Access Key ID 값이 상태(messages)에 섞이면 saver 가 저장을 차단한다."""
    saver = JsonFileCheckpointSaver(str(tmp_path))
    cfg = _mk_config("t-leak")
    leaked_state = {
        "aws_profile": "prod-sso",
        "messages": [HumanMessage(
            content=f"여기 자격증명 {_KNOWN_ACCESS_KEY_ID} 이 실수로 포함됨")],
    }
    with pytest.raises(CredentialLeakError):
        saver.put(cfg, _mk_checkpoint("c-leak", leaked_state),
                  {"source": "test", "step": 1}, {})

    # 차단됐으므로 자격증명 값을 담은 파일이 디스크에 남지 않아야 한다.
    for _path, raw in _read_all_checkpoint_files(tmp_path):
        assert not _contains_known_planted(raw)
        assert _scan_credential_values(raw) == []


def test_saver_blocks_when_access_key_leaks_into_metadata(tmp_path):
    """metadata 에 Access Key ID 값이 섞여도 차단한다(이중 방어)."""
    saver = JsonFileCheckpointSaver(str(tmp_path))
    cfg = _mk_config("t-leak-md")
    with pytest.raises(CredentialLeakError):
        saver.put(
            cfg,
            _mk_checkpoint("c-leak-md", {"aws_profile": "default"}),
            {"source": "test", "step": 1, "note": _KNOWN_ACCESS_KEY_ID},
            {},
        )


def test_saver_blocks_when_access_key_leaks_into_writes(tmp_path):
    """put_writes 경로에서도 Access Key ID 값 유출을 차단한다."""
    saver = JsonFileCheckpointSaver(str(tmp_path))
    thread_id = "t-leak-w"
    cid = "c-leak-w"
    cfg = _mk_config(thread_id)
    # 정상 체크포인트 먼저 저장(자격증명 없음)
    saver.put(cfg, _mk_checkpoint(cid, {"aws_profile": "default"}),
              {"source": "test", "step": 1}, {})
    # 이후 write 에 자격증명이 섞이면 차단
    write_cfg = {"configurable": {
        "thread_id": thread_id, "checkpoint_ns": "", "checkpoint_id": cid}}
    with pytest.raises(CredentialLeakError):
        saver.put_writes(
            write_cfg,
            [("messages", f"leaked {_KNOWN_ACCESS_KEY_ID}")],
            task_id="task-1",
        )


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-q"]))
