"""deep-research-engine: 리서치 설정 로더 — 순수/결정적 (부작용은 env 읽기뿐).

design.md "환경변수 / 설정" 표를 단일 ``DeepResearchConfig`` dataclass로 매핑한다.
env 미설정 시 표의 기본값을 사용하며, 파싱 실패(형식 오류·빈 값)는 예외 없이
기본값으로 폴백한다(결정적). Python 3.11 표준 라이브러리(``dataclasses``/``os``)만
사용하고 신규 의존성을 도입하지 않는다.

보안(steering security / 요구사항 11): 제공자 자격증명(API 키) 값은 이 config에
절대 저장하지 않는다. config는 제공자 "이름"만 다루며, 실제 키는 런타임에
backend가 env(예: ``TAVILY_API_KEY``)에서만 로딩한다.

bool 파싱은 기존 rag(``answer_quality``/``retrieval_pipeline``/``grounding_gate``)
관례와 정합한다: ``"1"/"true"/"yes"/"on"`` (대소문자 무시) → True.

Requirements: 10.2, 13.1, 14.2
Design: "환경변수 / 설정" 표 (AE_ENABLE_WEB_RESEARCH … AE_RESEARCH_QUERY_SUMMARY_MAX)
"""

import os
from dataclasses import dataclass, field


def _truthy(v) -> bool:
    """env 값의 boolean 해석 (기존 rag 관례와 정합)."""
    return str(v or "").strip().lower() in ("1", "true", "yes", "on")


def _int(v, default: int) -> int:
    """정수 파싱. 미설정/빈 값/형식 오류는 기본값으로 폴백(예외 없음)."""
    try:
        return int(str(v).strip())
    except (TypeError, ValueError):
        return default


def _float(v, default: float) -> float:
    """실수 파싱. 미설정/빈 값/형식 오류는 기본값으로 폴백(예외 없음)."""
    try:
        return float(str(v).strip())
    except (TypeError, ValueError):
        return default


def _providers(v, default: list[str]) -> list[str]:
    """콤마 분리 제공자 목록 → 정규화된 이름 리스트.

    공백 제거·소문자화 후 빈 항목을 걸러내며, 미설정/빈 결과는 기본 목록으로
    폴백한다. 제공자 "이름"만 취하고 자격증명은 포함하지 않는다.
    """
    if v is None:
        return list(default)
    items = [p.strip().lower() for p in str(v).split(",")]
    items = [p for p in items if p]
    return items if items else list(default)


def _recency_unknown(v, default: str) -> str:
    """발행일 미상 처리 규칙 파싱. 허용값(exclude|last) 외에는 기본값으로 폴백."""
    val = str(v or "").strip().lower()
    return val if val in ("exclude", "last") else default


@dataclass
class DeepResearchConfig:
    """딥리서치/외부 검색 실행 파라미터 (design.md 환경변수 표 매핑).

    모든 필드는 표의 기본값을 가지며, ``from_env``로 os.environ(또는 주입된
    매핑)에서 로딩한다. 자격증명 값은 포함하지 않는다(제공자 이름만).
    """

    # --- 옵트인 / 동의 게이트 (요구사항 10.2/10.3, 14.2) ---
    enable_web_research: bool = False   # AE_ENABLE_WEB_RESEARCH (off → 로컬만, 무회귀)
    consent: bool = False               # AE_RESEARCH_CONSENT (프라이버시 동의)

    # --- 제공자 목록 (이름만 — 자격증명 아님) ---
    web_providers: list[str] = field(
        default_factory=lambda: ["tavily", "exa", "brave"]
    )                                   # AE_RESEARCH_WEB_PROVIDERS
    # 기본 학술 제공자는 **키 없이 실제로 응답하는 것**만 둔다(실측 기준).
    # semantic_scholar 는 선택 키가 없으면 HTTP 429 로 막히고 arxiv 는 export API 가
    # 느려 자주 TIMEOUT 이라, 둘을 기본에 두면 신규 사용자에게는 결과가 거의 없다.
    # openalex + europepmc 는 키리스로 안정 응답하며 europepmc 가 생의학을 커버한다.
    # (semantic_scholar/arxiv/pubmed 는 UI 에서 언제든 추가 선택 가능하다.)
    academic_providers: list[str] = field(
        default_factory=lambda: ["openalex", "europepmc"]
    )                                   # AE_RESEARCH_ACADEMIC_PROVIDERS

    # --- 검색·수집 상한 ---
    top_k: int = 10                     # AE_RESEARCH_TOPK
    max_subqueries: int = 8             # AE_RESEARCH_MAX_SUBQUERIES
    fetch_per_subquery: int = 5         # AE_RESEARCH_FETCH_PER_SUBQUERY

    # --- 딥리서치 심화/커버리지 (P13, 요구사항 5.7/9.6) ---
    max_deepening: int = 3              # AE_MAX_DEEPENING (Deepening_Cap)
    min_sources: int = 5                # AE_RESEARCH_MIN_SOURCES
    min_providers: int = 2              # AE_RESEARCH_MIN_PROVIDERS
    unverified_threshold: float = 0.2   # AE_RESEARCH_UNVERIFIED_THRESHOLD

    # --- 타임아웃 / 크기 상한 (요구사항 13.1, 3.3/3.4) ---
    search_timeout: float = 12.0        # AE_SEARCH_TIMEOUT (초)
    fetch_timeout: float = 10.0         # AE_FETCH_TIMEOUT (초)
    fetch_max_chars: int = 100000       # AE_FETCH_MAX_CHARS (소스당 본문 상한)

    # --- 캐시 / 정렬 규칙 / 품질 게이트 ---
    cache_ttl: int = 86400              # AE_RESEARCH_CACHE_TTL (초, 24h)
    recency_unknown: str = "last"       # AE_RESEARCH_RECENCY_UNKNOWN (exclude|last)
    regression_tolerance: float = 0.05  # AE_RESEARCH_REGRESSION_TOLERANCE

    # --- 검색 진행 이벤트 (요구사항 18.2/18.7) ---
    query_summary_max: int = 80         # AE_RESEARCH_QUERY_SUMMARY_MAX

    @classmethod
    def from_env(cls, env=None) -> "DeepResearchConfig":
        """os.environ(또는 주입 매핑)에서 설정을 로딩한다.

        Args:
            env: 환경변수 매핑. ``None``이면 ``os.environ``을 사용한다. 빈 dict
                 (``{}``)를 넘기면 모든 필드가 기본값으로 채워진다.

        Returns:
            기본값 위에 env 오버라이드를 적용한 ``DeepResearchConfig``. 형식
            오류·빈 값은 기본값으로 폴백하여 항상 유효한 설정을 반환한다.
        """
        env = env if env is not None else os.environ
        d = cls()  # 기본값 스냅샷(표 정합)
        return cls(
            enable_web_research=_truthy(env.get("AE_ENABLE_WEB_RESEARCH")),
            consent=_truthy(env.get("AE_RESEARCH_CONSENT")),
            web_providers=_providers(
                env.get("AE_RESEARCH_WEB_PROVIDERS"), d.web_providers
            ),
            academic_providers=_providers(
                env.get("AE_RESEARCH_ACADEMIC_PROVIDERS"), d.academic_providers
            ),
            top_k=_int(env.get("AE_RESEARCH_TOPK"), d.top_k),
            max_subqueries=_int(
                env.get("AE_RESEARCH_MAX_SUBQUERIES"), d.max_subqueries
            ),
            fetch_per_subquery=_int(
                env.get("AE_RESEARCH_FETCH_PER_SUBQUERY"), d.fetch_per_subquery
            ),
            max_deepening=_int(env.get("AE_MAX_DEEPENING"), d.max_deepening),
            min_sources=_int(env.get("AE_RESEARCH_MIN_SOURCES"), d.min_sources),
            min_providers=_int(
                env.get("AE_RESEARCH_MIN_PROVIDERS"), d.min_providers
            ),
            unverified_threshold=_float(
                env.get("AE_RESEARCH_UNVERIFIED_THRESHOLD"), d.unverified_threshold
            ),
            search_timeout=_float(env.get("AE_SEARCH_TIMEOUT"), d.search_timeout),
            fetch_timeout=_float(env.get("AE_FETCH_TIMEOUT"), d.fetch_timeout),
            fetch_max_chars=_int(env.get("AE_FETCH_MAX_CHARS"), d.fetch_max_chars),
            cache_ttl=_int(env.get("AE_RESEARCH_CACHE_TTL"), d.cache_ttl),
            recency_unknown=_recency_unknown(
                env.get("AE_RESEARCH_RECENCY_UNKNOWN"), d.recency_unknown
            ),
            regression_tolerance=_float(
                env.get("AE_RESEARCH_REGRESSION_TOLERANCE"), d.regression_tolerance
            ),
            query_summary_max=_int(
                env.get("AE_RESEARCH_QUERY_SUMMARY_MAX"), d.query_summary_max
            ),
        )
