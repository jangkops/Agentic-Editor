# Feature: langgraph-reasoning-upgrade, Property 8: 평가 결과 파싱 견고성
"""Property 8: 평가 결과 파싱 견고성 — Hypothesis 기반 PBT.

Validates: Requirements 1.2, 1.6

design.md Correctness Property 8 발췌:
    For any Evaluator LLM 응답 형태(tool_calls dict, 텍스트, 필드 누락, 무효 타입)에 대해,
    parse_evaluation 은 항상 {achieved: bool, reason: str, missing_domains: list[str]}
    형태를 반환하며, missing_domains 는 유효 도메인 라벨의 부분집합이고, 파싱 불가 시
    achieved=True 로 안전 종료를 지향한다.

대상 코드(실측):
- ai_engine/agent_system/supervisor.py 의 parse_evaluation(ai_message, valid_domains).

전략:
- Gateway/네트워크 불필요. hypothesis max_examples=100, deadline=None.

실행:
  ai_engine/.venv/bin/python -m pytest scripts/test_parse_evaluation_pbt.py -q
"""
from __future__ import annotations

import json
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from hypothesis import given, settings, strategies as st

from ai_engine.agent_system.supervisor import _ROUTE_LABELS, parse_evaluation

_VALID = _ROUTE_LABELS  # ("coding", "media", "research", "ops", "chat")


class FakeMessage:
    """AIMessage 유사 객체 — tool_calls / content 속성만 노출."""

    def __init__(self, tool_calls=None, content=None):
        self.tool_calls = tool_calls
        self.content = content


# ── 전략(생성기) ─────────────────────────────────────────────────────────────
_achieved_values = st.one_of(
    st.booleans(),
    st.sampled_from(["true", "false", "yes", "no", "달성", "achieved", ""]),
    st.none(),
    st.integers(min_value=-3, max_value=3),
)

_reason_values = st.one_of(
    st.text(max_size=20),
    st.none(),
    st.integers(),
    st.booleans(),
)

# valid + invalid 라벨 섞기
_domain_tokens = list(_VALID) + ["bogus", "xxx", "", "planner", "done"]
_missing_values = st.one_of(
    st.lists(st.sampled_from(_domain_tokens), max_size=8),
    st.none(),
    st.text(max_size=10),               # 무효 타입(문자열)
    st.integers(),                       # 무효 타입(정수)
    st.lists(st.integers(), max_size=4), # 리스트 안 무효 타입
)


@st.composite
def eval_args(draw):
    """평가 인자 dict 생성(필드 일부 누락 가능)."""
    d = {}
    if draw(st.booleans()):
        d["achieved"] = draw(_achieved_values)
    if draw(st.booleans()):
        d["reason"] = draw(_reason_values)
    if draw(st.booleans()):
        d["missing_domains"] = draw(_missing_values)
    return d


@st.composite
def fake_messages(draw):
    """다양한 형태의 Evaluator 응답 메시지 생성."""
    shape = draw(st.integers(min_value=0, max_value=5))
    if shape == 0:
        # tool_calls 에 평가 인자
        return FakeMessage(tool_calls=[{"name": "submit_evaluation", "args": draw(eval_args())}])
    if shape == 1:
        # content 에 JSON 문자열
        return FakeMessage(content=json.dumps(draw(eval_args())))
    if shape == 2:
        # content 에 임의 텍스트(파싱 불가 가능)
        return FakeMessage(content=draw(st.text(max_size=40)))
    if shape == 3:
        # content 가 멀티모달 list
        return FakeMessage(content=[{"text": draw(st.text(max_size=20))}, "extra"])
    if shape == 4:
        # tool_calls 가 비어있거나 무효
        return FakeMessage(tool_calls=draw(st.one_of(st.none(), st.just([]), st.just("bad"))))
    # dict 형태 메시지(속성 없음, key 접근)
    return {"tool_calls": [{"args": draw(eval_args())}], "content": draw(st.text(max_size=10))}


# ── 속성 테스트 ──────────────────────────────────────────────────────────────
@settings(max_examples=100, deadline=None)
@given(msg=fake_messages())
def test_always_returns_contract_shape(msg):
    """Property 8: 항상 {achieved:bool, reason:str, missing_domains:list[str]} 반환."""
    result = parse_evaluation(msg, _VALID)
    assert isinstance(result, dict)
    assert set(result.keys()) == {"achieved", "reason", "missing_domains"}
    assert isinstance(result["achieved"], bool)
    assert isinstance(result["reason"], str)
    assert isinstance(result["missing_domains"], list)
    assert all(isinstance(d, str) for d in result["missing_domains"])


@settings(max_examples=100, deadline=None)
@given(msg=fake_messages())
def test_missing_domains_subset_of_valid(msg):
    """Property 8: missing_domains 는 valid_domains 의 부분집합."""
    result = parse_evaluation(msg, _VALID)
    assert set(result["missing_domains"]).issubset(set(_VALID))


@settings(max_examples=100, deadline=None)
@given(msg=fake_messages())
def test_never_raises(msg):
    """Property 8(불변식): 어떤 입력에도 예외를 전파하지 않는다."""
    parse_evaluation(msg, _VALID)  # 예외 없이 완료되면 통과


@settings(max_examples=100, deadline=None)
@given(text=st.text(max_size=30).filter(lambda t: "{" not in t))
def test_unparseable_text_defaults_achieved_true(text):
    """Property 8: JSON 이 아닌 텍스트(파싱 불가) → achieved=True(안전 종료)."""
    result = parse_evaluation(FakeMessage(content=text), _VALID)
    assert result["achieved"] is True
    assert result["missing_domains"] == []


# ── 예시(단위) 테스트 ─────────────────────────────────────────────────────────
def test_tool_calls_achieved_false_with_missing():
    msg = FakeMessage(
        tool_calls=[{"args": {"achieved": False, "reason": "PPT 누락", "missing_domains": ["media", "bogus"]}}]
    )
    r = parse_evaluation(msg, _VALID)
    assert r["achieved"] is False
    assert r["reason"] == "PPT 누락"
    assert r["missing_domains"] == ["media"]  # bogus 제거


def test_missing_achieved_field_defaults_true():
    msg = FakeMessage(tool_calls=[{"args": {"reason": "부분", "missing_domains": ["coding"]}}])
    r = parse_evaluation(msg, _VALID)
    assert r["achieved"] is True  # achieved 누락 → 비차단


def test_json_text_fallback():
    payload = {"achieved": False, "reason": "미완", "missing_domains": ["ops"]}
    msg = FakeMessage(content="앞부분 설명\n" + json.dumps(payload) + "\n뒷부분")
    r = parse_evaluation(msg, _VALID)
    assert r["achieved"] is False
    assert r["missing_domains"] == ["ops"]


def test_empty_message_achieved_true():
    r = parse_evaluation(FakeMessage(), _VALID)
    assert r == {"achieved": True, "reason": "", "missing_domains": []}
