"""응답 품질 오케스트레이터 — 무회귀(플래그 off) + 게이트 동작 (Req 2.2, 3.x, 10.4).

실행: ./ai_engine/.venv/bin/python -m pytest scripts/test_answer_quality_orchestrator_pbt.py -p no:cacheprovider -q
"""
import asyncio
from dataclasses import dataclass
from ai_engine.rag.answer_quality import (
    quality_enabled, build_citation_metadata, enhance_answer,
    faithfulness_below_threshold,
)


@dataclass
class _Chunk:
    file_path: str
    start_line: int
    end_line: int


def test_explicit_off_passthrough():
    """AE_ANSWER_QUALITY=0 명시 → 응답 그대로, metadata 비어있음(opt-out)."""
    out = asyncio.run(enhance_answer("ans", "ctx", [], gw=None, env={"AE_ANSWER_QUALITY": "0"}))
    assert out["answer"] == "ans"
    assert out["metadata"] == {}


def test_auto_on_by_default():
    """기본(미설정) → 자동 ON. 인용 검증(무료)은 gw 없이도 수행되어 metadata 존재."""
    out = asyncio.run(enhance_answer("근거(a.py:1-2)", "ctx", [], gw=None, env={}))
    assert out["answer"].startswith("근거")
    assert "citation" in (out["metadata"] or {})  # 자동 ON → 인용 메타 부착


def test_quality_enabled_flag():
    assert quality_enabled({}) is True                       # 기본 자동 ON
    assert quality_enabled({"AE_ANSWER_QUALITY": ""}) is True  # 빈값도 ON
    assert quality_enabled({"AE_ANSWER_QUALITY": "1"}) is True
    assert quality_enabled({"AE_ANSWER_QUALITY": "0"}) is False  # 명시 opt-out


def test_citation_metadata_detects_unverified():
    chunks = [_Chunk("src/main.js", 100, 200)]
    md = build_citation_metadata(
        "보세요 src/main.js:150-160 그리고 other.py:999-1000", chunks
    )
    assert md["verified"] == 1
    assert "other.py:999-1000" in md["unverified"]


def test_enabled_runs_citation_without_gateway():
    chunks = [_Chunk("a.py", 1, 50)]
    out = asyncio.run(enhance_answer(
        "answer a.py:10-20", "ctx", chunks, gw=None,
        env={"AE_ANSWER_QUALITY": "1"},
    ))
    assert "citation" in out["metadata"]
    assert out["metadata"]["citation"]["verified"] == 1


def test_verify_runs_with_mock_gateway():
    class _GW:
        async def converse(self, **kwargs):
            return {"output": {"message": {"content": [{"text": "SCORE: 0.9\nFEEDBACK: OK"}]}}}
    chunks = [_Chunk("a.py", 1, 50)]
    out = asyncio.run(enhance_answer(
        "answer a.py:10", "some context", chunks, gw=_GW(),
        env={"AE_ANSWER_QUALITY": "1", "AE_VERIFY": "1"},
    ))
    assert out["metadata"]["faithfulness"]["score"] == 0.9
    assert out["metadata"]["faithfulness"]["degraded"] is False


def test_verify_fallback_on_gateway_error():
    class _Boom:
        async def converse(self, **kwargs):
            raise RuntimeError("down")
    out = asyncio.run(enhance_answer(
        "ans", "ctx", [], gw=_Boom(),
        env={"AE_ANSWER_QUALITY": "1", "AE_VERIFY": "1"},
    ))
    f = out["metadata"]["faithfulness"]
    assert f["degraded"] is True and f["score"] is None


def test_threshold_trigger_logic():
    assert faithfulness_below_threshold(
        {"faithfulness": {"score": 0.5, "degraded": False}}, {"AE_VERIFY_THRESHOLD": "0.7"}
    ) is True
    assert faithfulness_below_threshold(
        {"faithfulness": {"score": 0.9, "degraded": False}}, {}
    ) is False
    # degraded/None이면 재생성 안 함(비차단)
    assert faithfulness_below_threshold(
        {"faithfulness": {"score": None, "degraded": True}}, {}
    ) is False
