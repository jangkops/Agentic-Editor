# Feature: reasoning-perf-reliability, Property 9: Baseline_Record 는 자격증명·프롬프트 전문을 포함하지 않는다
"""Property 9 — Baseline_Record 자격증명·프롬프트 전문 부재 property 테스트.

*For any* Query_Set 과 플래그 구성으로 조립한 Baseline_Record 에 대해:
    (a) 재귀적으로 스캔했을 때 `accessKeyId`/`secretAccessKey`/`sessionToken` 키가
        어떤 깊이에도 존재하지 않는다.
    (b) 입력 프롬프트 전문 문자열이 직렬화(JSON) 결과에 등장하지 않는다
        (질의 id·지표만 저장 — 요구사항 3.4).

`build_baseline_record` 의 계약(참조: `scripts/eval_reasoning_perf.py`):
    - per-query 항목은 허용 키 화이트리스트(id·지표·status·error)만 통과시켜
      프롬프트 전문·대화 원문·자격증명 키가 새어나가지 않게 한다.
    - 조립된 레코드에 자격증명 키가 어떤 깊이에라도 남으면 `_assert_no_credentials`
      가 `ValueError` 를 던진다. `active_flags` 는 화이트리스트 없이 그대로 실리므로
      자격증명 키를 포함하면 조립이 거부된다.

따라서 본 테스트는 두 경우를 모두 검증한다:
    - active_flags 에 자격증명 키가 (어떤 깊이든) 있으면 → ValueError 로 거부.
    - 없으면 → 레코드는 자격증명 키가 전무하고, 프롬프트 전문이 직렬화 결과에 부재.

Validates: Requirements 3.3, 3.4, 11.2
"""
from __future__ import annotations

import json
import string
from typing import Any

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from eval_reasoning_perf import build_baseline_record

# Baseline_Record 어디에도 등장해선 안 되는 자격증명 키(요구사항 3.3/11.2).
_CREDENTIAL_KEYS = frozenset({"accessKeyId", "secretAccessKey", "sessionToken"})

# 프롬프트 전문 식별용 센티넬. 레코드의 고정 구조 키/지표 값과 충돌하지 않도록
# 통상적인 텍스트에 등장하지 않는 유니코드 마커로 감싼다.
_SENTINEL = "\u2603PROMPT_FULLTEXT_\u2603"

# id/error 등 화이트리스트로 보존되는 필드는 센티넬과 충돌하지 않는 안전 문자만 사용.
_safe_alphabet = string.ascii_letters + string.digits + "-_ ."
_safe_text = st.text(alphabet=_safe_alphabet, min_size=0, max_size=24)
_id_text = st.text(alphabet=string.ascii_letters + string.digits + "-_", min_size=1, max_size=12)

_score = st.floats(min_value=0.0, max_value=1.0, allow_nan=False, allow_infinity=False)
_latency = st.floats(min_value=0.0, max_value=100_000.0, allow_nan=False, allow_infinity=False)


def _scan_credential_keys(obj: Any) -> bool:
    """어떤 깊이에서든 자격증명 키가 dict 키로 등장하면 True(재귀 스캔)."""
    if isinstance(obj, dict):
        for key, val in obj.items():
            if key in _CREDENTIAL_KEYS:
                return True
            if _scan_credential_keys(val):
                return True
    elif isinstance(obj, (list, tuple)):
        return any(_scan_credential_keys(x) for x in obj)
    return False


# per-query 항목: 정상 지표 + 프롬프트 전문(deliberate) + 자격증명 키/원문(누출 유도).
@st.composite
def _per_query_item(draw: Any) -> dict:
    prompt_body = draw(st.text(max_size=40))
    item: dict[str, Any] = {
        "id": draw(_id_text),
        "prompt": _SENTINEL + prompt_body,  # ← 반드시 스트립되어야 하는 전문
        "latency_ms": draw(_latency),
        "grounding": draw(_score),
        "accuracy": draw(_score),
        "recall_at_k": draw(_score),
        "mrr": draw(_score),
        "k": draw(st.integers(min_value=0, max_value=50)),
        "status": draw(st.sampled_from(["ok", "failed"])),
        "error": draw(st.none() | _safe_text),
        # 누출 유도: 자격증명 키·값 및 대화 원문 — 화이트리스트로 제거되어야 한다.
        "accessKeyId": "AKIA" + draw(_id_text),
        "secretAccessKey": draw(_id_text),
        "sessionToken": draw(_id_text),
        "aws_profile": draw(_safe_text),
        "bedrock_user": draw(_safe_text),
        "messages": [{"role": "user", "content": _SENTINEL + prompt_body}],
    }
    return item


_per_query = st.lists(_per_query_item(), min_size=0, max_size=6)

# active_flags: 정상 플래그 + (때때로) 자격증명 키를 포함하도록 생성.
_clean_flag_key = st.sampled_from(
    [
        "AE_ENABLE_ADAPTIVE_DEPTH",
        "AE_ENABLE_GROUNDING_GATE",
        "AE_LANGGRAPH_PARALLEL",
        "AE_MAX_REFINE",
        "AE_VERIFY_THRESHOLD",
    ]
)
_flag_value = st.one_of(
    st.booleans(),
    st.integers(min_value=-8, max_value=8),
    st.floats(min_value=0.0, max_value=1.0, allow_nan=False, allow_infinity=False),
    _safe_text,
)
_active_flags = st.dictionaries(
    keys=st.one_of(_clean_flag_key, st.sampled_from(sorted(_CREDENTIAL_KEYS))),
    values=_flag_value,
    max_size=6,
)


@settings(max_examples=200)
@given(active_flags=_active_flags, per_query=_per_query)
def test_baseline_record_has_no_credentials_or_prompt(
    active_flags: dict, per_query: list
) -> None:
    now = "2026-07-01T12:00:00Z"

    # active_flags 는 화이트리스트 없이 그대로 실리므로, 자격증명 키가 (어떤 깊이든)
    # 존재하면 조립이 ValueError 로 거부되어야 한다(_assert_no_credentials 계약).
    if _scan_credential_keys(active_flags):
        with pytest.raises(ValueError):
            build_baseline_record(active_flags, per_query, now)
        return

    record = build_baseline_record(active_flags, per_query, now)

    # (a) 자격증명 키가 어떤 깊이에도 없다.
    assert not _scan_credential_keys(record), "Baseline_Record 에 자격증명 키가 잔존함"

    # (b) 프롬프트 전문(센티넬)이 직렬화 결과에 등장하지 않는다 — id·지표만 저장.
    blob = json.dumps(record, ensure_ascii=False)
    assert _SENTINEL not in blob, "프롬프트 전문이 Baseline_Record 직렬화에 누출됨"

    # per_query 는 화이트리스트 키만 유지한다(구조적 확인).
    allowed = {
        "id",
        "latency_ms",
        "grounding",
        "accuracy",
        "recall_at_k",
        "mrr",
        "k",
        "status",
        "error",
    }
    for entry in record["per_query"]:
        assert set(entry).issubset(allowed)
        assert "prompt" not in entry and "messages" not in entry
