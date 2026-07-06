"""인용 파싱·검증 — 순수 함수 (LLM 호출 없음).

응답에서 `파일경로:시작-끝라인` 형식의 인용을 추출하고, 그 인용이 실제
검색된 청크의 (file, start~end) 범위에 포함되는지 대조한다. 허위 인용을 표기해
할루시네이션을 억제한다. 응답 자체는 차단하지 않는다(가용성 우선).

Requirements: 2.1, 2.2, 2.4  /  Property 3
"""
import re
from dataclasses import dataclass, field
from typing import List, Sequence


# 예: src/main.js:120-145 / ai_engine/rag/indexer.py:1 / a/b.py:10-10
_CITATION_RE = re.compile(
    r'([A-Za-z0-9_./\-]+\.[A-Za-z0-9_]+):(\d+)(?:\s*[-~]\s*(\d+))?'
)


@dataclass
class Citation:
    file: str
    start_line: int
    end_line: int
    raw: str


@dataclass
class RetrievedRange:
    """검색된 청크의 근거 범위."""
    file: str
    start_line: int
    end_line: int


@dataclass
class CitationReport:
    verified: List[Citation] = field(default_factory=list)
    unverified: List[Citation] = field(default_factory=list)

    @property
    def total(self) -> int:
        return len(self.verified) + len(self.unverified)


def parse_citations(answer: str) -> List[Citation]:
    """응답 텍스트에서 인용을 추출한다. 임의 텍스트는 안전하게 무시된다."""
    if not answer:
        return []
    out: List[Citation] = []
    for m in _CITATION_RE.finditer(answer):
        file = m.group(1)
        try:
            start = int(m.group(2))
            end = int(m.group(3)) if m.group(3) else start
        except (TypeError, ValueError):
            continue
        if end < start:
            start, end = end, start
        out.append(Citation(file=file, start_line=start, end_line=end, raw=m.group(0)))
    return out


def _norm(path: str) -> str:
    """경로 정규화 — 선행 ./ 제거, 역슬래시 통일."""
    p = str(path or "").replace("\\", "/")
    while p.startswith("./"):
        p = p[2:]
    return p


def verify_citations(
    citations: Sequence[Citation],
    retrieved: Sequence[RetrievedRange],
) -> CitationReport:
    """각 인용이 검색 범위에 포함되는지 대조.

    포함 규칙: 같은 파일(경로 접미 일치 허용) AND 인용 라인 범위가 검색 청크
    범위와 겹치면 verified. 겹치지 않으면 unverified.
    """
    report = CitationReport()
    norm_ranges = [
        (_norm(r.file), min(r.start_line, r.end_line), max(r.start_line, r.end_line))
        for r in retrieved
    ]
    for c in citations:
        cf = _norm(c.file)
        cs, ce = min(c.start_line, c.end_line), max(c.start_line, c.end_line)
        ok = False
        for rf, rs, re_ in norm_ranges:
            # 파일 일치: 완전 일치 또는 접미(basename 경로) 일치
            if rf == cf or rf.endswith("/" + cf) or cf.endswith("/" + rf):
                # 라인 범위 겹침
                if cs <= re_ and ce >= rs:
                    ok = True
                    break
        (report.verified if ok else report.unverified).append(c)
    return report
