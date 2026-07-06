"""인용 검증 속성 테스트 — Property 3.

실행: ./ai_engine/.venv/bin/python -m pytest scripts/test_citation_verify_pbt.py -p no:cacheprovider -q
"""
from hypothesis import given, strategies as st
from ai_engine.rag.citation import (
    parse_citations, verify_citations, Citation, RetrievedRange, CitationReport,
)


def test_parse_basic():
    cites = parse_citations("보세요 src/main.js:120-145 그리고 a/b.py:10 참고")
    raws = {(c.file, c.start_line, c.end_line) for c in cites}
    assert ("src/main.js", 120, 145) in raws
    assert ("a/b.py", 10, 10) in raws


@given(st.text(max_size=500))
def test_parse_arbitrary_text_never_crashes(txt):
    """임의 텍스트에도 예외 없이 안전. 결과는 리스트."""
    out = parse_citations(txt)
    assert isinstance(out, list)


def test_in_range_verified():
    cites = [Citation("src/main.js", 125, 130, "src/main.js:125-130")]
    retrieved = [RetrievedRange("src/main.js", 120, 145)]
    rep = verify_citations(cites, retrieved)
    assert len(rep.verified) == 1 and len(rep.unverified) == 0


def test_out_of_range_unverified():
    cites = [Citation("src/main.js", 500, 510, "src/main.js:500-510")]
    retrieved = [RetrievedRange("src/main.js", 120, 145)]
    rep = verify_citations(cites, retrieved)
    assert len(rep.unverified) == 1 and len(rep.verified) == 0


def test_wrong_file_unverified():
    cites = [Citation("other.js", 125, 130, "other.js:125-130")]
    retrieved = [RetrievedRange("src/main.js", 120, 145)]
    rep = verify_citations(cites, retrieved)
    assert len(rep.unverified) == 1


def test_path_suffix_match():
    """접미 경로 일치 허용 (main.js:125 ↔ src/main.js:120-145)."""
    cites = [Citation("main.js", 125, 130, "main.js:125-130")]
    retrieved = [RetrievedRange("src/main.js", 120, 145)]
    rep = verify_citations(cites, retrieved)
    assert len(rep.verified) == 1


@given(
    st.integers(min_value=1, max_value=1000),
    st.integers(min_value=1, max_value=50),
    st.integers(min_value=1, max_value=1000),
    st.integers(min_value=1, max_value=50),
)
def test_overlap_iff_verified(rs, rlen, cs, clen):
    """겹치면 verified, 안 겹치면 unverified (같은 파일 전제)."""
    re_ = rs + rlen
    ce = cs + clen
    cites = [Citation("f.py", cs, ce, f"f.py:{cs}-{ce}")]
    retrieved = [RetrievedRange("f.py", rs, re_)]
    rep = verify_citations(cites, retrieved)
    overlap = (cs <= re_ and ce >= rs)
    if overlap:
        assert len(rep.verified) == 1
    else:
        assert len(rep.unverified) == 1


def test_no_retrieved_all_unverified():
    cites = [Citation("f.py", 1, 2, "f.py:1-2")]
    rep = verify_citations(cites, [])
    assert len(rep.unverified) == 1
