# Feature: reasoning-perf-reliability, Property 2: 분류 결과는 항상 두 값 중 하나다
"""Property-based test: Depth_Router 복잡도 분류의 이진성(`classify_complexity`).

Feature: reasoning-perf-reliability, Property 2: 분류 결과는 항상 두 값 중 하나다
**Validates: Requirements 4.1**

For any 프롬프트에 대해, `classify_complexity` 의 반환값은 반드시 `{'simple', 'complex'}`
중 하나다(미정의 값 없음, 예외 전파 없음).

대상 코드(실측):
- ai_engine/agent_system/depth_router.py 의 classify_complexity(prompt, deps, *, use_llm):
    · 휴리스틱 우선(classify_heuristic) → complex 면 즉시 'complex'
    · simple & use_llm=True & gateway 존재 → GatewayChatModel(sonnet, prefer_streaming=True)
      .bind_tools(select_depth, toolChoice) 로 1회 확인. 개별 ainvoke 하나만
      asyncio.wait_for 로 감싼다.
    · TimeoutError / GatewayModelError / 기타 예외 / 불확실 라벨 → 'complex' fail-safe

이 테스트는 두 경로를 모두 검증한다:
    1. use_llm=False — 순수 휴리스틱 경로.
    2. use_llm=True + Gateway 확인 경로. mock deps.gateway 가 (a) 예외를 던지거나,
       (b) ERROR 결과를 반환하거나, (c) 유효/무효 라벨을 반환하는 등 다양한 상황을 만든다.
       어떤 경우에도 반환값이 {'simple','complex'} 중 하나이며 예외가 전파되지 않음을 단언한다.

생성기(hypothesis strategies)는 다음 edge case 를 포함한다:
    - 빈 문자열 / 공백만 / 탭·개행
    - 한/영 혼합 텍스트
    - 장문(길이 임계 초과)
    - 형식 키워드(pptx/pdf/보고서 등) 포함
    - 임의 유니코드 텍스트

실행: ai_engine/.venv/bin/python -m pytest scripts/test_depth_router_binary_pbt.py -q
Stack: Python 3.11+, hypothesis library.
"""
from __future__ import annotations

import asyncio
import os
import sys
from types import SimpleNamespace

# repo 루트를 import 경로에 추가해 ai_engine 패키지를 로드한다
# (test_grounding_below_pbt.py 와 동일한 패턴).
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from hypothesis import given, settings, strategies as st  # noqa: E402

from ai_engine.agent_system.depth_router import classify_complexity  # noqa: E402

_VALID = {"simple", "complex"}

_SONNET = "us.anthropic.claude-sonnet-4-5-20250929-v1:0"


# ─────────────────────────────────────────────────────────────────────────────
# Mock Gateway — GatewayChatModel(prefer_streaming=True) 가 호출하는
# converse_stream_live / converse 를 구현하는 결정론적 스텁. 실제 네트워크·비용 없음.
# behavior 에 따라 예외/에러결과/유효라벨/무효응답을 방출해 fail-safe 경로를 폭넓게 자극한다.
# ─────────────────────────────────────────────────────────────────────────────
class _MockGateway:
    """behavior 로 응답 유형을 결정하는 결정론적 게이트웨이 스텁."""

    def __init__(self, behavior: str) -> None:
        self.behavior = behavior

    def _respond(self) -> dict:
        b = self.behavior
        if b == "raise":
            # 게이트웨이 호출이 예외를 던지는 상황 → classify 는 'complex' 로 폴백해야 한다.
            raise RuntimeError("mock gateway boom")
        if b == "error_result":
            # decision=ERROR → _raise_on_gateway_error 가 GatewayModelError 발생 → 'complex'.
            return {"decision": "ERROR", "error": "mock gateway error"}
        if b in ("valid_simple", "valid_complex"):
            label = "simple" if b == "valid_simple" else "complex"
            return {
                "decision": "ALLOW",
                "output": {
                    "message": {
                        "content": [
                            {
                                "toolUse": {
                                    "toolUseId": "tu-1",
                                    "name": "select_depth",
                                    "input": {"depth": label},
                                }
                            }
                        ]
                    }
                },
            }
        # garbage: 유효 라벨이 없는 텍스트 응답 → 불확실 → 'complex'.
        return {
            "decision": "ALLOW",
            "output": {"message": {"content": [{"text": "no label here 123"}]}},
        }

    async def converse_stream_live(self, **_kwargs) -> dict:
        return self._respond()

    async def converse(self, **_kwargs) -> dict:
        return self._respond()


_GATEWAY_BEHAVIORS = st.sampled_from(
    ["raise", "error_result", "valid_simple", "valid_complex", "garbage"]
)


# ── 프롬프트 생성기: 빈/공백·한영 혼합·장문·형식 키워드·임의 유니코드 포섭 ──
_FORMAT_KEYWORDS = [
    "pptx", "ppt", "파워포인트", "슬라이드", "pdf", "보고서", "리포트",
    "xlsx", "엑셀", "docx", "워드", "이미지", "png",
    "근거", "출처", "조사", "왜", "분석", "검색", "그리고", "그 결과로",
]

_prompts = st.one_of(
    st.sampled_from(["", "   ", "\t\n", "\u3000"]),  # 빈/공백/탭·개행/전각공백
    st.text(),  # 임의 유니코드
    st.text(  # 한/영 혼합
        alphabet="가나다라마바사아자차카타파하 abcdefghijklmnop0123456789",
        min_size=0,
        max_size=60,
    ),
    st.text(min_size=201, max_size=350),  # 장문(길이 임계 초과)
    st.lists(st.sampled_from(_FORMAT_KEYWORDS), min_size=1, max_size=6).map(
        " ".join
    ),  # 형식/근거/다도메인 키워드 조합
)


# deadline=None: 지연 import(GatewayChatModel 등)와 per-example asyncio.run 이벤트 루프
# 생성으로 첫 호출 타이밍이 크게 변동한다. 이 테스트는 지연이 아니라 반환값의 이진성을
# 검증하므로 hypothesis 의 per-example 데드라인을 끈다.
@settings(max_examples=200, deadline=None)
@given(prompt=_prompts, use_llm=st.booleans(), behavior=_GATEWAY_BEHAVIORS)
def test_classify_complexity_is_binary(prompt, use_llm, behavior):
    """classify_complexity 반환값은 항상 {'simple','complex'} 중 하나이며 예외를 전파하지 않는다."""
    deps = SimpleNamespace(gateway=_MockGateway(behavior), model_coding=_SONNET)

    # 개별 실행마다 새 이벤트 루프로 async 분류 실행. classify_complexity 는 어떤 게이트웨이
    # 상황(예외/에러/불확실)에서도 예외를 전파하지 않도록 설계됐다 — 여기서 예외가 새어나오면
    # 테스트가 실패해 "예외 전파 없음" 위반을 드러낸다.
    result = asyncio.run(classify_complexity(prompt, deps, use_llm=use_llm))

    assert result in _VALID, (
        f"분류 결과가 이진 집합을 벗어남: result={result!r} "
        f"(prompt={prompt!r}, use_llm={use_llm}, behavior={behavior})"
    )
