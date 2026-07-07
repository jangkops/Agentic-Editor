"""합의 교차검증 as_dict() ↔ 프론트(main.js merge 핸들러) 소비 계약 고정.

main.js는 ev.crossVerify에서 다음 키를 읽는다:
  - degraded (bool)        : true면 UI 표시 skip
  - conflictCount (int)    : >0이면 충돌 경고 헤더
  - candidates[] 각 항목의 index(int), score(number), conflict(bool), note(str)

이 계약이 깨지면 배선된 UI가 조용히 오작동하므로 회귀 테스트로 고정한다.
"""
from ai_engine.rag.cross_verify import CrossVerifyReport, CandidateVerdict


def test_as_dict_has_frontend_keys():
    rep = CrossVerifyReport(
        verdicts=[
            CandidateVerdict(index=0, score=0.9, conflict=False, note="OK"),
            CandidateVerdict(index=1, score=0.4, conflict=True, note="근거 없음"),
        ],
        degraded=False,
        conflict_count=1,
    )
    d = rep.as_dict()
    # 최상위 키
    assert set(["degraded", "conflictCount", "candidates"]).issubset(d.keys())
    assert d["degraded"] is False
    assert d["conflictCount"] == 1
    # 후보 항목 키 — 프론트가 c.index/c.score/c.conflict/c.note 로 읽음
    for c in d["candidates"]:
        assert set(["index", "score", "conflict", "note"]).issubset(c.keys())
        assert isinstance(c["index"], int)
        assert isinstance(c["score"], (int, float))
        assert isinstance(c["conflict"], bool)
        assert isinstance(c["note"], str)
    # 충돌 후보가 conflict=True로 노출되는지
    assert d["candidates"][1]["conflict"] is True


def test_degraded_report_still_has_keys():
    """degraded 폴백에서도 프론트가 안전하게 skip 판단할 수 있어야 한다."""
    rep = CrossVerifyReport(degraded=True, error="timeout")
    d = rep.as_dict()
    assert d["degraded"] is True
    assert d["conflictCount"] == 0
    assert d["candidates"] == []
    # error는 있으면 첨부(옵셔널)
    assert d.get("error") == "timeout"
