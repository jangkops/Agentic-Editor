"""Property test — Property 7: citation 검증은 답변을 차단하지 않음.

Validates: Requirements 3.5

대상 코드:
    ai_engine/agent_system/nodes/verify.py
    - make_verify_node(deps) -> async verify_node(state) -> dict
    - _last_ai_text / _classify_citations 를 통해 final_text 확정 + citation 분류(비차단).
    - citation.parse_citations / verify_citations 로 verified / unverified 분류.

검증 속성 (Property 7 / 요구사항 3.5):
    임의의 citation 상태(전부 verified / 전부 unverified / 혼합 / citation 없음)에 대해
    verify 노드는 **항상 final_text(답변)를 보존**하고, None/빈 문자열로 답변을 차단하지
    않는다. unverified 인용이 존재하더라도 답변 텍스트 자체는 제거/변형되지 않는다.
    즉 citation 검증은 표기(verified/unverified 분류)만 수행하고 답변을 막지 않는다.

접근:
    - hypothesis 로 (파일:라인) 인용 스펙과 근거 range 조합을 생성해 다양한 citation
      상태를 만든다. 근거가 인용을 덮으면 verified, 아니면 unverified 로 분류된다.
    - final_text 는 인용 raw 문자열 + 인용에 걸리지 않는 filler(숫자/점/콜론 없음)로
      구성해, 파싱되는 인용이 생성된 스펙과 정확히 일치하도록 통제한다.
    - RAG citation 자산은 **실제 호출**(순수 함수, 네트워크/임베딩 없음).
    - answer_quality 는 AE_ANSWER_QUALITY=0 으로 off → gateway 미접촉(hermetic).
    - 강제 생성 폴백(server._force_generate_from_text)은 가짜 ai_engine.server 모듈을
      sys.modules 에 주입해 네트워크/파일생성 없이 [] 를 반환하도록 stub 한다.

네트워크/임베딩/게이트웨이 호출 없음. hypothesis max_examples 로 유한 시간 종료.

실행:
    ai_engine/.venv/bin/python -m pytest scripts/test_langgraph_verify_nonblocking_pbt.py -q
"""
import asyncio
import os
import sys
import types
from dataclasses import dataclass

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

# ── hermetic: answer_quality off (verify_mode()=="off" → gateway 미접촉) ──
os.environ["AE_ANSWER_QUALITY"] = "0"

from hypothesis import given, settings, strategies as st, HealthCheck

# 강제 생성 폴백을 stub — verify._invoke_force_generate 가 지연 import 하는
# ai_engine.server 를 가짜 모듈로 대체해 네트워크/파일생성을 차단(항상 파일 0건).
# verify.py 는 server 를 **지연 import** 하므로 stub 은 테스트 실행 중에만 존재하면 된다.
# 모듈 로드 시점에 sys.modules 를 영구 치환하면 같은 세션의 후속 테스트가 진짜
# ai_engine.server 를 잃어 회귀(격리 오염)가 발생하므로, autouse fixture +
# monkeypatch.setitem 으로 각 테스트 후 원본 모듈을 자동 복원한다.
_fake_server = types.ModuleType("ai_engine.server")


def _fake_infer_file_intent(prompt, open_content, final_text):
    # (primary_tool, wanted, target_files) — wanted=False 로 폴백을 즉시 종료.
    return ("", False, [])


async def _fake_force_generate(**kwargs):
    return []


_fake_server._infer_file_intent_from_prompt = _fake_infer_file_intent
_fake_server._force_generate_from_text = _fake_force_generate


@pytest.fixture(autouse=True)
def _stub_ai_engine_server(monkeypatch):
    """verify 의 지연 import 대상인 ai_engine.server 를 테스트 동안만 fake 로 치환.

    monkeypatch.setitem 은 테스트 종료 시 원래 sys.modules 값을 자동 복원(없었으면 삭제)
    하므로 세션 전역 오염 없이 격리를 보장한다.
    """
    monkeypatch.setitem(sys.modules, "ai_engine.server", _fake_server)
    yield


from langchain_core.messages import AIMessage, HumanMessage  # noqa: E402

from ai_engine.agent_system.nodes.verify import make_verify_node, _last_ai_text  # noqa: E402


# ─────────────────────────────────────────────────────────────────────────────
# 테스트 헬퍼
# ─────────────────────────────────────────────────────────────────────────────
@dataclass
class _Chunk:
    """evidence.chunks 항목(chunk 부분) — verify 노드가 참조하는 속성만."""
    file_path: str
    start_line: int
    end_line: int
    content: str = ""


class _Deps:
    """make_verify_node(deps) 용 최소 deps — answer_quality off 라 gateway 미사용."""
    gateway = None


def _run_verify(state: dict) -> dict:
    """verify_node 를 동기적으로 1회 실행해 결과 dict 반환."""
    verify_node = make_verify_node(_Deps())
    return asyncio.run(verify_node(state))


# ── 생성기: citation 안전 파일/라인 ──
_SEG = st.text(alphabet="abcdefghijklmnopqrstuvwxyz", min_size=1, max_size=6)
_EXT = st.sampled_from(["py", "js", "ts", "md", "txt"])
# filler: 숫자/점/콜론이 없어 citation 정규식에 걸리지 않는 단어만.
_FILLER = st.lists(
    st.sampled_from(["the", "answer", "내용", "요약", "코드", "hello", "world", "설명"]),
    min_size=0,
    max_size=6,
)


@st.composite
def _citation_specs(draw):
    """[(file, start, end, match), ...] 생성 — match=True 면 근거가 인용을 덮는다."""
    n = draw(st.integers(min_value=0, max_value=5))
    specs = []
    for _ in range(n):
        a = draw(_SEG)
        b = draw(_SEG)
        ext = draw(_EXT)
        file = f"{a}/{b}.{ext}"
        start = draw(st.integers(min_value=1, max_value=300))
        span = draw(st.integers(min_value=0, max_value=40))
        end = start + span
        match = draw(st.booleans())
        specs.append((file, start, end, match))
    return specs


def _raw(file: str, start: int, end: int) -> str:
    """인용 raw 문자열 — start==end 면 단일 라인 형식, 아니면 범위 형식."""
    return f"{file}:{start}" if start == end else f"{file}:{start}-{end}"


def _build_final_text(specs, filler_words) -> str:
    """인용 raw + filler 를 섞어 답변 텍스트를 구성(인용은 생성 스펙과 정확히 일치)."""
    pieces = list(filler_words)
    for file, start, end, _ in specs:
        pieces.append(_raw(file, start, end))
    # 셔플 대신 filler 사이에 인용을 끼워 넣어도 파싱 결과는 동일.
    return " ".join(pieces)


def _build_evidence(specs):
    """match=True 인 인용을 덮는 근거 chunk 목록으로 evidence dict 구성."""
    chunks = []
    for file, start, end, match in specs:
        if match:
            # 인용 라인을 확실히 포함하도록 근거 범위를 넉넉히.
            chunks.append((_Chunk(file_path=file, start_line=start, end_line=end + 5), 0.9))
    if not chunks:
        return None
    return {"context": "근거 컨텍스트", "chunks": chunks}


# ─────────────────────────────────────────────────────────────────────────────
# Property 7 — 핵심: citation 상태와 무관하게 답변 보존 + 비차단
# ─────────────────────────────────────────────────────────────────────────────
@settings(max_examples=80, deadline=None,
          suppress_health_check=[HealthCheck.function_scoped_fixture])
@given(specs=_citation_specs(), filler=_FILLER, has_prior_files=st.booleans())
def test_citation_verification_never_blocks_answer(specs, filler, has_prior_files):
    """임의의 citation 상태에서 verify 노드는 final_text 를 보존하고 차단하지 않는다."""
    final_text = _build_final_text(specs, filler)
    messages = [HumanMessage(content="질문"), AIMessage(content=final_text)]
    evidence = _build_evidence(specs)

    state = {
        "prompt": "일반 질문",
        "messages": messages,
        "evidence": evidence,
    }
    # verified_files 유무를 번갈아 — 있으면 강제 생성 분기 skip, 없으면 stub 로 [].
    if has_prior_files:
        state["verified_files"] = [{"path": "x.txt", "absPath": "/tmp/x.txt", "tool": "t"}]

    out = _run_verify(state)

    # (1) final_text 키가 항상 존재하고, 마지막 AIMessage 텍스트와 정확히 일치(보존).
    assert "final_text" in out, "verify 출력에 final_text 없음(차단)"
    assert out["final_text"] == final_text, (
        f"답변이 변형됨: 기대={final_text!r} 실제={out['final_text']!r}"
    )
    # 입력 답변이 비어있지 않으면 출력도 비어있지 않아야 한다(빈 문자열로 차단 금지).
    if final_text != "":
        assert out["final_text"], "비어있지 않은 답변이 빈 문자열로 차단됨"

    # (2) citation 은 표기만 — verified/unverified 리스트로 분류되며 예외/차단 없음.
    citations = out.get("citations")
    assert isinstance(citations, dict), "citations 가 dict 가 아님"
    assert isinstance(citations.get("verified"), list)
    assert isinstance(citations.get("unverified"), list)

    # (3) 근거(evidence.chunks)가 있으면 모든 파싱 인용이 verified ∪ unverified 로
    #     빠짐없이 계상된다. 근거가 없으면(evidence None) 검증 대상이 없어 분류를
    #     건너뛰지만(설계상 비차단), 답변은 (1)에서 이미 보존됨을 확인했다.
    from ai_engine.rag.citation import parse_citations
    parsed = parse_citations(final_text)
    total_classified = len(citations["verified"]) + len(citations["unverified"])
    if evidence is not None and evidence.get("chunks"):
        assert total_classified == len(parsed), (
            f"인용 계상 불일치: parsed={len(parsed)} classified={total_classified}"
        )
    else:
        # 근거 없음 → 분류 미수행(빈 report). 핵심은 답변 보존(위 (1)에서 검증).
        assert citations["verified"] == [] and citations["unverified"] == [], (
            "근거가 없는데 citation 분류가 수행됨"
        )


# ─────────────────────────────────────────────────────────────────────────────
# 명시적 4개 상태(예시 기반) — 전부 verified / 전부 unverified / 혼합 / 인용 없음
# ─────────────────────────────────────────────────────────────────────────────
def test_all_verified_preserves_answer():
    file, s, e = "src/main.py", 10, 20
    final_text = f"핵심 로직은 {_raw(file, s, e)} 에 있다"
    evidence = {"context": "c", "chunks": [(_Chunk(file, s, e + 3), 0.9)]}
    out = _run_verify({"prompt": "q", "messages": [AIMessage(content=final_text)],
                       "evidence": evidence, "verified_files": [{"absPath": "/x"}]})
    assert out["final_text"] == final_text
    assert out["citations"]["unverified"] == []
    assert len(out["citations"]["verified"]) == 1


def test_all_unverified_preserves_answer():
    final_text = "잘못된 근거 other/file.js:100-120 를 인용"
    # 근거가 전혀 겹치지 않음 → unverified. 그래도 답변 보존.
    evidence = {"context": "c", "chunks": [(_Chunk("unrelated/z.py", 1, 2), 0.5)]}
    out = _run_verify({"prompt": "q", "messages": [AIMessage(content=final_text)],
                       "evidence": evidence, "verified_files": [{"absPath": "/x"}]})
    assert out["final_text"] == final_text
    assert out["citations"]["verified"] == []
    assert len(out["citations"]["unverified"]) == 1


def test_mixed_citations_preserves_answer():
    good = _raw("a/good.py", 5, 9)
    bad = _raw("b/bad.py", 50, 60)
    final_text = f"{good} 는 근거 있고 {bad} 는 없음"
    evidence = {"context": "c", "chunks": [(_Chunk("a/good.py", 5, 12), 0.9)]}
    out = _run_verify({"prompt": "q", "messages": [AIMessage(content=final_text)],
                       "evidence": evidence, "verified_files": [{"absPath": "/x"}]})
    assert out["final_text"] == final_text
    assert len(out["citations"]["verified"]) == 1
    assert len(out["citations"]["unverified"]) == 1


def test_no_citations_preserves_answer():
    final_text = "인용이 전혀 없는 일반 답변입니다"
    out = _run_verify({"prompt": "q", "messages": [AIMessage(content=final_text)],
                       "evidence": None, "verified_files": [{"absPath": "/x"}]})
    assert out["final_text"] == final_text
    assert out["citations"] == {"verified": [], "unverified": []}


def test_empty_answer_not_crashed():
    # AIMessage 가 빈 텍스트여도 예외 없이 final_text="" 로 통과(차단 아님).
    out = _run_verify({"prompt": "q", "messages": [AIMessage(content="")],
                       "evidence": None, "verified_files": [{"absPath": "/x"}]})
    assert out["final_text"] == ""
    assert out["citations"] == {"verified": [], "unverified": []}


if __name__ == "__main__":
    import pytest

    raise SystemExit(pytest.main([__file__, "-q"]))
