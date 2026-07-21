# Feature: reasoning-perf-reliability, Property 1: 복잡 질의는 절대 Fast_Path 로 라우팅되지 않는다
"""Property-based test: Depth_Router 복잡 질의 라우팅 안전성(`classify_complexity`).

Feature: reasoning-perf-reliability, Property 1: 복잡 질의는 절대 Fast_Path 로 라우팅되지 않는다
**Validates: Requirements 4.2, 4.3, 6.1**

For any 프롬프트에 대해, `complexity_signals` 중 하나 이상(multi_domain / needs_tool /
needs_evidence)이 True 이거나 LLM 분류가 실패·불확실하면, `classify_complexity` 는 반드시
`'complex'` 를 반환한다(따라서 Full_Graph 로 진행하며 Fast_Path 로 가지 않는다).

두 개의 검증 가능한 각도로 이 속성을 실측한다:

  (a) 휴리스틱 각도 — 근거/도구/다도메인 신호가 하나라도 잡히면(즉 `classify_heuristic` 가
      'complex' 를 반환하면), `classify_complexity(..., use_llm=False)` 도 반드시 'complex'.

  (b) LLM 실패 각도 — gateway 가 예외/타임아웃으로 실패하는 mock deps 로
      `classify_complexity(..., use_llm=True)` 를 호출하면, 어떤 프롬프트든 반드시 'complex'.
      (휴리스틱이 complex 면 즉시 complex, 휴리스틱이 simple 이면 LLM 확인이 실패 → fail-safe
      로 complex.) 근거 미달·불확실은 절대 Fast_Path 로 새지 않는다(요구사항 4.3).

`classify_complexity` 는 async 이므로 asyncio.run 으로 구동한다.

대상 코드(실측):
- ai_engine/agent_system/depth_router.py
    · complexity_signals(prompt) → {multi_domain, needs_tool, needs_evidence, long}
    · classify_heuristic(prompt) → 신호 중 하나라도 True 면 'complex'
    · classify_complexity(prompt, deps, *, use_llm) → 'simple'|'complex'
        (휴리스틱 우선, simple & use_llm 이면 Gateway LLM 1회 확인, 실패/불확실 → 'complex')

생성기(hypothesis strategies)는 다음 edge case 를 포함한다:
    - 빈 문자열 / 공백 / 탭·개행
    - 한/영 혼합 텍스트
    - 장문(길이 임계 초과)
    - 다도메인 접속 표현('그리고', '그 결과로')
    - 형식 키워드(pptx / pdf / 파워포인트)
    - 알려진 complex 유발 토큰 주입(근거/조사/검색 등)

실행: ai_engine/.venv/bin/python -m pytest scripts/test_depth_router_complex_pbt.py -q
Stack: Python 3.11+, hypothesis library.
"""
from __future__ import annotations

import asyncio
import os
import sys

# repo 루트를 import 경로에 추가해 ai_engine 패키지를 로드한다
# (depth_router 가 ai_engine.server / chat_model_adapter 를 지연 import 하므로 루트가 필요).
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from hypothesis import given, settings, strategies as st  # noqa: E402

from ai_engine.agent_system.depth_router import (  # noqa: E402
    classify_complexity,
    complexity_signals,
)
from ai_engine.agent_system.deps import GraphDeps  # noqa: E402


# ─────────────────────────────────────────────────────────────────────────────
# 실패하는 mock Gateway — LLM 확인 경로가 반드시 예외/타임아웃으로 끝나도록 한다.
# 직접 SDK(boto3/anthropic/openai) 를 쓰지 않으며, GatewayChatModel 이 호출하는
# converse / converse_stream_live 인터페이스만 흉내 낸다.
# ─────────────────────────────────────────────────────────────────────────────
class _RaisingGateway:
    """converse / converse_stream_live 가 즉시 예외를 던지는 게이트웨이 스텁."""

    async def converse(self, **kwargs):  # noqa: ANN003
        raise RuntimeError("mock gateway failure (converse)")

    async def converse_stream_live(self, **kwargs):  # noqa: ANN003
        raise RuntimeError("mock gateway failure (stream)")


class _TimingOutGateway:
    """converse / converse_stream_live 가 타임아웃을 유발하도록 지연되는 스텁.

    테스트는 AE_DEPTH_ROUTER_TIMEOUT 을 매우 작게 설정해 asyncio.wait_for 가
    TimeoutError 를 던지게 만든다(개별 ainvoke await 하나만 감싸는 규약 실측).
    """

    async def converse(self, **kwargs):  # noqa: ANN003
        await asyncio.sleep(0.05)
        return {"decision": "ALLOW", "output": {"message": {"content": []}}}

    async def converse_stream_live(self, **kwargs):  # noqa: ANN003
        await asyncio.sleep(0.05)
        return {"decision": "ALLOW", "output": {"message": {"content": []}}}


# ── 프롬프트 생성기(hand-roll 금지 — hypothesis strategies 조합) ──
_WHITESPACE = st.sampled_from(["", " ", "   ", "\t", "\n", " \t \n "])
_PLAIN = st.text(max_size=40)
# 한/영 혼합(+한글 자모/알파벳/숫자/공백).
_MIXED_KO_EN = st.text(alphabet="가나다라마바사아ABCDEFabcdef0123 ", max_size=40)
# 장문 — 길이 임계(200자) 초과를 강제해 'long' 신호를 유발.
_LONG = st.text(alphabet="가나다 ABC ", min_size=201, max_size=260)
# 알려진 complex 유발 토큰: 근거/조사/도구/미디어/다도메인 접속 표현.
_COMPLEX_TOKEN = st.sampled_from(
    [
        "근거", "출처", "조사", "왜", "분석", "이유", "reference", "source",
        "검색", "찾아", "실행", "터미널", "install",
        "pptx", "pdf", "파워포인트", "프레젠테이션", "엑셀", "이미지",
        "그리고", "그 결과로", "그런 다음", "이어서",
    ]
)
_FRAGMENT = st.one_of(_WHITESPACE, _PLAIN, _MIXED_KO_EN, _LONG, _COMPLEX_TOKEN)
# 조각들을 공백으로 이어 붙여 프롬프트를 구성(빈/공백/혼합/장문/토큰 주입 모두 포섭).
_PROMPTS = st.builds(lambda parts: " ".join(parts), st.lists(_FRAGMENT, max_size=4))

# LLM 실패 모드: 즉시 예외 / 타임아웃 두 경로 모두 fail-safe(complex) 로 귀결됨을 검증.
_FAILURE_MODE = st.sampled_from(["raise", "timeout"])


@settings(max_examples=200, deadline=None)
@given(prompt=_PROMPTS, failure_mode=_FAILURE_MODE)
def test_complex_query_never_routed_to_fast_path(prompt, failure_mode):
    """복잡 신호가 있거나 LLM 확인이 실패하면 classify_complexity 는 항상 'complex' 다."""
    # 실패 게이트웨이를 주입한 deps 구성(자격증명 미포함 — GraphDeps 계약 유지).
    if failure_mode == "raise":
        deps = GraphDeps(gateway=_RaisingGateway())
    else:
        deps = GraphDeps(gateway=_TimingOutGateway())

    # 타임아웃 모드에서는 wait_for 상한을 매우 작게 설정(개별 await 하나만 감싸는 경로 실측).
    original_timeout = os.environ.get("AE_DEPTH_ROUTER_TIMEOUT")
    if failure_mode == "timeout":
        os.environ["AE_DEPTH_ROUTER_TIMEOUT"] = "0.001"

    async def _run():
        # (a) 휴리스틱 각도: LLM 미사용. gateway 는 건드리지 않는다.
        heuristic_result = await classify_complexity(prompt, deps, use_llm=False)
        # (b) LLM 실패 각도: gateway 확인이 반드시 실패 → fail-safe.
        llm_result = await classify_complexity(prompt, deps, use_llm=True)
        return heuristic_result, llm_result

    try:
        heuristic_result, llm_result = asyncio.run(_run())
    finally:
        if failure_mode == "timeout":
            if original_timeout is None:
                os.environ.pop("AE_DEPTH_ROUTER_TIMEOUT", None)
            else:
                os.environ["AE_DEPTH_ROUTER_TIMEOUT"] = original_timeout

    signals = complexity_signals(prompt)
    core_complex = bool(
        signals["multi_domain"] or signals["needs_tool"] or signals["needs_evidence"]
    )

    # (a) 핵심 복잡 신호(multi_domain/needs_tool/needs_evidence)가 하나라도 있으면
    #     휴리스틱 분류는 반드시 'complex' 여야 한다(Fast_Path 배제).
    if core_complex:
        assert heuristic_result == "complex", (
            f"복잡 신호 존재인데 Fast_Path 로 새 나감: prompt={prompt!r}, "
            f"signals={signals}, result={heuristic_result!r}"
        )

    # (b) LLM 확인이 실패/타임아웃하면 결과는 항상 'complex'(fail-safe, 요구사항 4.3).
    #     휴리스틱이 complex 든 simple 이든 어느 경로에서도 Fast_Path 로 가지 않는다.
    assert llm_result == "complex", (
        f"LLM 실패({failure_mode}) 시 fail-safe 위반: prompt={prompt!r}, "
        f"signals={signals}, result={llm_result!r}"
    )

    # 모든 반환값은 유효 라벨 집합 안에 있어야 한다(예외 전파 없음).
    assert heuristic_result in ("simple", "complex")
    assert llm_result in ("simple", "complex")
