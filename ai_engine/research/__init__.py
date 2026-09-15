# deep-research-engine: research 패키지
#
# 외부 리서치 능력(웹 검색 + 논문 검색 + 딥리서치)의 백엔드 모듈 루트.
#
# 공개 API 배선(Task 18.1): `from ai_engine.research import X` 가 깔끔히 동작하도록
# 핵심 심볼(정규 데이터 모델·설정·단일 egress 백엔드/게이트·딥리서치 파이프라인 진입점·
# 품질 평가 하네스)을 이 패키지 루트에서 재노출한다. 하위 모듈 직접 import
# (`from ai_engine.research.backend import ...` 등)도 그대로 동작한다(무회귀).
#
# import 안전성: 하위 모듈을 **leaf-first** 순서로 import 한다(models→config→security→
# dedup→providers→normalize→rank→cache→backend→deep_research→eval_harness). deep_research
# 는 내부에서 `from ai_engine.research import backend, normalize, rank` 를 하므로, 이들을
# 먼저 import 해 부분 초기화 상태의 순환 참조를 피한다. deep_research 의 LLM 스택
# (langchain/게이트웨이 어댑터)은 함수 내부 지연 import 라 패키지 import 는 가볍고 부작용이
# 없다(순수 데이터/순수 함수 계층 + httpx 는 기존 core 의존성).
#
# 보안(steering security / 요구사항 11): 이 패키지는 자격증명(API 키) 원문을 어떤 심볼·
# 상태에도 보관하지 않는다. 키는 런타임에 backend 가 env 에서만 로딩하며(security 모듈),
# 로그에는 마스킹만 남긴다.

# ── 순수 데이터 모델 (부작용 없음) ──
from .models import (
    EvidenceSource,
    PaperResult,
    ResearchMetrics,
    ResearchReport,
    SearchResult,
)

# ── 설정 로더 (env 읽기만) ──
from .config import DeepResearchConfig

# ── 자격증명 보안 헬퍼 (env 로딩 + 마스킹) ──
from .security import load_credential, mask_secret

# ── 중복제거 / 정규화 / 정렬·재랭킹 (순수) ──
from .dedup import canonical_doi, canonical_url, dedup_sources, source_key
from .normalize import (
    deserialize_result,
    normalize_query,
    parse_paper_result,
    parse_search_result,
    serialize_result,
)
from .rank import (
    apply_recency,
    merge_and_rerank,
    sort_by_relevance_papers,
    sort_by_relevance_web,
    source_authority,
)

# ── userData 검색 캐시 (파일 I/O) ──
from . import cache

# ── 단일 외부 egress 백엔드 + 옵트인/동의 게이트 ──
from .backend import (
    FetchResult,
    academic_search_raw,
    fetch_url_raw,
    search_academic_with_fallback,
    search_web_with_fallback,
    web_research_enabled,
    web_search_raw,
)

# ── 딥리서치 멀티에이전트 파이프라인 진입점 (축 B) ──
from .deep_research import (
    deep_research_report_path,
    plan_subqueries,
    plan_waves,
    run_deep_research,
    run_deep_research_sync,
    run_wave,
    should_deepen,
)

# ── 품질 평가 하네스 + baseline 회귀 게이트 ──
from .eval_harness import (
    aggregate_metrics,
    check_regression,
    evaluate_golden_set,
    evaluate_query,
    to_research_metrics,
)

# 하위 모듈도 패키지 속성으로 노출(명시적 재노출 — `from ai_engine.research import backend`).
from . import (  # noqa: E402  (심볼 재노출 이후 모듈 재노출)
    backend,
    config,
    dedup,
    deep_research,
    eval_harness,
    models,
    normalize,
    providers,
    rank,
    security,
)

__all__ = [
    # 하위 모듈
    "models",
    "config",
    "security",
    "dedup",
    "providers",
    "normalize",
    "rank",
    "cache",
    "backend",
    "deep_research",
    "eval_harness",
    # 데이터 모델
    "SearchResult",
    "PaperResult",
    "EvidenceSource",
    "ResearchMetrics",
    "ResearchReport",
    # 설정
    "DeepResearchConfig",
    # 보안
    "load_credential",
    "mask_secret",
    # dedup / normalize / rank
    "canonical_url",
    "canonical_doi",
    "source_key",
    "dedup_sources",
    "normalize_query",
    "parse_search_result",
    "parse_paper_result",
    "serialize_result",
    "deserialize_result",
    "sort_by_relevance_web",
    "sort_by_relevance_papers",
    "apply_recency",
    "merge_and_rerank",
    "source_authority",
    # backend (egress + 게이트)
    "web_research_enabled",
    "web_search_raw",
    "academic_search_raw",
    "fetch_url_raw",
    "FetchResult",
    "search_web_with_fallback",
    "search_academic_with_fallback",
    # deep_research 진입점
    "run_deep_research",
    "run_deep_research_sync",
    "deep_research_report_path",
    "plan_subqueries",
    "plan_waves",
    "run_wave",
    "should_deepen",
    # eval_harness
    "evaluate_golden_set",
    "evaluate_query",
    "aggregate_metrics",
    "to_research_metrics",
    "check_regression",
]
