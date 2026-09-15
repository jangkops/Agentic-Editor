"""deep-research-engine: 정규 데이터 모델 — 순수 데이터 정의 (부작용 없음).

이기종 검색 제공자(웹/논문) 응답을 단일 정규 스키마로 표현하고, 딥리서치
산출물(리포트·근거 스냅샷·품질 지표)을 담는 dataclass를 정의한다. 네트워크
호출·파일 I/O·전역 상태가 없는 순수 데이터 계층이며 Python 3.11 표준
``dataclasses``만 사용한다(신규 무거운 의존성 없음).

설계 원칙(P8 정합): 모든 필드는 정렬 가능한 기본값을 가져 부분 파싱 시에도
예외 없이 정규 결과를 생성할 수 있다. 누락 텍스트 필드는 빈 문자열(``""``),
누락 수치 필드는 ``0``/``0.0``, 컬렉션은 빈 컨테이너로 기본값을 채운다.

source_id 스킴 (인용 참조 무결성 P6 정합):
    - 웹 소스:  ``web:<canonical_url>``   (정규화된 URL 기반)
    - 논문 소스: ``doi:<canonical_doi>``   (정규 DOI가 있을 때)
    - DOI가 없는 논문/웹 소스는 ``web:<canonical_url>``로 폴백한다.
자격증명(API 키 등)은 어떤 모델 필드에도 저장하지 않는다(steering security).

Requirements: 1.2, 2.2, 5.6, 15.1
Design: "Data Models" 절 (SearchResult / PaperResult / EvidenceSource /
        ResearchMetrics / ResearchReport)
"""

from dataclasses import dataclass, field


@dataclass
class SearchResult:
    """웹 검색 결과 1건의 정규 스키마 (요구사항 1.2).

    Web_Search_Provider(Tavily/Exa/Brave 등)의 이기종 응답을 이 6개 정규
    필드 + 계측 필드(provider/source_id)로 변환한다. 누락 텍스트 필드는
    ``""``, 관련성 점수는 정렬 가능한 ``0.0``으로 채운다(요구사항 1.3, P8).
    """

    title: str = ""              # 제목 (누락 시 "")
    url: str = ""                # URL (누락 시 "")
    snippet: str = ""            # 발췌문 (누락 시 "")
    published_date: str = ""     # 발행일 ISO8601 (미상 시 "")
    source_domain: str = ""      # 출처 도메인 (url에서 파생, 누락 시 "")
    relevance_score: float = 0.0  # 관련성 점수 (정렬 가능한 수치, 누락 시 0.0)
    provider: str = ""           # 출처 제공자 name (계측/신뢰도용)
    source_id: str = ""          # "web:<canonical_url>" (인용 참조 무결성 P6)


@dataclass
class PaperResult:
    """논문 검색 결과 1건의 정규 스키마 (요구사항 2.2).

    Academic_Search_Provider(Semantic Scholar/OpenAlex/arXiv/PubMed 등)의
    응답을 이 7개 정규 필드 + 계측 필드로 변환한다. 누락 텍스트 필드는 ``""``,
    저자 목록은 빈 리스트, 발행연도·피인용수는 정렬 가능한 ``0``으로 채운다
    (요구사항 2.6, P8).
    """

    title: str = ""                              # 제목 (누락 시 "")
    authors: list[str] = field(default_factory=list)  # 저자 목록 (누락 시 [])
    year: int = 0                                # 발행연도 (미상 시 0 — 정렬 가능)
    venue: str = ""                              # 게재처 (누락 시 "")
    abstract: str = ""                           # 초록 (누락 시 "")
    doi_or_url: str = ""                         # DOI 또는 URL (누락 시 "")
    citation_count: int = 0                      # 피인용수 (미상 시 0 — 정렬 가능)
    relevance_score: float = 0.0                 # 관련성 점수 (누락 시 0.0)
    provider: str = ""                           # 출처 제공자 name (계측/신뢰도용)
    source_id: str = ""          # "doi:<canonical_doi>" 또는 "web:<canonical_url>"


@dataclass
class EvidenceSource:
    """딥리서치가 수집·중복제거·재랭킹한 근거 소스 1건 (요구사항 5.6 / 8).

    인용 검증은 이 ``source_id`` 존재성으로 판정한다(참조 무결성 P6). 본문
    ``content``는 크기 상한이 적용된 수집 텍스트다.
    """

    source_id: str = ""          # "web:<canonical_url>" | "doi:<canonical_doi>"
    title: str = ""
    url_or_doi: str = ""
    provider: str = ""
    content: str = ""            # 수집 본문(크기 상한 적용)
    published_date: str = ""
    authority: float = 0.0       # 출처 신뢰도 신호 (요구사항 9.4)


@dataclass
class ResearchMetrics:
    """딥리서치 품질 지표 (요구사항 9.7 / 평가 하네스).

    "최고 품질 티어"를 측정 가능한 수치로 규정한다: 관련성(precision@k·MRR),
    최신성, 출처 신뢰도, 인용 정확도, 커버리지, 중복제거. 모든 값은 정렬·비교
    가능한 기본값(0/0.0)을 가진다.
    """

    precision_at_k: float = 0.0
    mrr: float = 0.0
    recency_score: float = 0.0
    authority_score: float = 0.0
    citation_accuracy: float = 0.0   # 1 - unverified_ratio
    coverage_sources: int = 0
    coverage_providers: int = 0
    dedup_ratio: float = 0.0
    grounding_score: float = 0.0     # 로컬 임베딩 근거성 점수 [0,1] (요구사항 8.4 / Task 13.2)


@dataclass
class ResearchReport:
    """딥리서치 최종 산출물 — 인용 포함 종합 답변 (요구사항 5.6 / 8).

    ``citations``는 검증/미검증 인용 목록을 담는 dict
    (예: ``{"verified": [source_id...], "unverified": [raw...]}``)이며,
    ``evidence_snapshot``은 종합에 사용된 근거 소스 스냅샷이다. 심화 반복
    횟수는 Deepening_Cap 이하로 유지된다(P13).

    ``answer_quality``는 근거성 메타데이터(Task 13.2)를 프론트 ``answerQuality``
    SSE 규약과 동일한 형태로 담는다(요구사항 9.5 / 8.4):
    ``{"citation": {"citations_total": int, "verified": int, "unverified": [raw...]},
       "unverified_ratio": float, "grounding": {"score": float, ...},
       "faithfulness": {...}, "grounding_gate": {...}}``.
    ``rag/answer_quality.enhance_answer``(local_grounding_score + faithfulness)와
    ``agent_system/grounding_gate`` 를 재사용해 병합하며, 자격증명은 담지 않는다(P9).
    downstream(research-panel · 품질 하네스)이 그대로 소비할 수 있고, graph-stream
    SSE 방출 배선은 Task 18.1 이 담당한다.
    """

    query: str = ""
    report_markdown: str = ""    # 인용 포함 종합 본문 (Generator 산출)
    citations: dict = field(default_factory=dict)  # {"verified":[...], "unverified":[...]}
    evidence_snapshot: list[EvidenceSource] = field(default_factory=list)  # 근거 소스
    metrics: ResearchMetrics = field(default_factory=ResearchMetrics)      # 품질 지표
    deepening_count: int = 0     # 수행된 심화 반복 횟수 (≤ Deepening_Cap, P13)
    unverified_ratio: float = 0.0  # 미검증 인용 비율 [0,1] (요구사항 9.5)
    created_at: str = ""
    # answerQuality 형태 근거성 메타데이터(요구사항 9.5/8.4 · Task 13.2). 자격증명 미포함(P9).
    answer_quality: dict = field(default_factory=dict)
