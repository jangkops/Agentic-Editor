"""회귀 방지: RAG 모듈이 GatewayClient.converse의 실제 시그니처로만 호출하는지 검증.

라이브 스모크에서 잡힌 실버그: verifier/reranker/query_expand/cross_verify가
`inference_config=` 를 넘겼으나 실제 converse는 (model_id, messages, system_prompt,
tool_config)만 받아 TypeError → 항상 degraded 폴백. 단위 테스트가 converse(**kwargs)
스텁을 써서 놓쳤다. 여기서는 실제 시그니처를 그대로 모방한 엄격 스텁으로 호출부를 검증한다.

실행: ./ai_engine/.venv/bin/python -m pytest scripts/test_gateway_converse_signature_contract.py -p no:cacheprovider -q
"""
import asyncio
import inspect
from ai_engine.gateway_module import GatewayClient
from ai_engine.rag.verifier import verify_faithfulness
from ai_engine.rag.reranker import rerank
from ai_engine.rag.query_expand import expand_query
from ai_engine.rag.cross_verify import cross_verify_consensus


class StrictGW:
    """실제 GatewayClient.converse 시그니처만 허용(초과 kwarg면 TypeError)."""
    def __init__(self, text="SCORE: 0.9\nFEEDBACK: OK"):
        self._text = text
        self.calls = 0

    async def converse(self, model_id, messages, system_prompt="", tool_config=None):
        self.calls += 1
        return {"decision": "ALLOW",
                "output": {"message": {"content": [{"text": self._text}]}}}


def test_real_converse_signature_has_no_inference_config():
    sig = inspect.signature(GatewayClient.converse)
    assert "inference_config" not in sig.parameters
    assert set(["model_id", "messages"]).issubset(sig.parameters)


def test_verifier_calls_with_real_signature():
    gw = StrictGW("SCORE: 0.8\nFEEDBACK: OK")
    res = asyncio.run(verify_faithfulness(gw, "m", "답변", "근거", timeout=5))
    assert gw.calls == 1
    assert not res.degraded and res.score == 0.8


def test_reranker_calls_with_real_signature():
    gw = StrictGW("[1,0]")
    order = asyncio.run(rerank(gw, "m", "q", ["a", "b"], timeout=5))
    assert gw.calls == 1 and sorted(order) == [0, 1]


def test_query_expand_calls_with_real_signature():
    gw = StrictGW("확장1\n확장2")
    out = asyncio.run(expand_query(gw, "m", "원쿼리", timeout=5))
    assert gw.calls == 1 and out and out[0] == "원쿼리"


def test_cross_verify_calls_with_real_signature():
    gw = StrictGW("AGENT 0: SCORE=0.9 CONFLICT=no\nAGENT 1: SCORE=0.5 CONFLICT=yes")
    agents = [{"role": "a", "title": "t", "summary": "s"},
              {"role": "b", "title": "t", "summary": "s"}]
    rep = asyncio.run(cross_verify_consensus(gw, "m", "q", agents, timeout=5))
    assert gw.calls == 1 and not rep.degraded and rep.conflict_count == 1


def test_gateway_error_decision_is_degraded():
    """decision != ALLOW(에러/거부)면 가짜 점수 대신 degraded로 처리."""
    class ErrGW:
        async def converse(self, model_id, messages, system_prompt="", tool_config=None):
            return {"decision": "ERROR", "error": "HTTP 403"}
    res = asyncio.run(verify_faithfulness(ErrGW(), "m", "답변", "근거", timeout=5))
    assert res.degraded and res.score is None
    rep = asyncio.run(cross_verify_consensus(
        ErrGW(), "m", "q",
        [{"role": "a", "title": "t", "summary": "s"}], timeout=5))
    assert rep.degraded
