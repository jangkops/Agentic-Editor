"""deep-research-engine: 품질 평가 하네스 — 지표 산출 (순수/결정적, 네트워크 없음).

golden 질의 세트에 대해 "최고 품질 티어"를 **측정 가능한 수치**로 산출한다
(요구사항 9.1/9.7/16.1). 산출 지표는 design.md ``ResearchMetrics`` 정의와 정합한다:

    - precision@k         : 상위 k개 중 관련 소스 비율
    - MRR                 : 첫 관련 소스의 역순위 평균
    - recall@k            : 관련 소스 재현율(부가 지표, dict에만 포함)
    - 최신성(recency)      : 최신성 창 이내(또는 발행일 확정) 결과 비율
    - 출처 신뢰도(authority): 소스별 신뢰도 신호 평균
    - 인용 정확도          : 1 − 미검증 인용 비율
    - 커버리지(coverage)   : 고유 소스 수 / 고유 제공자 수
    - 중복제거(dedup)      : 병합 대비 감소율 (1 − 고유/원본)

재사용(재구현 금지 — 요구사항 9.1/16.1):
    - precision@k·MRR·recall@k : ``ai_engine.rag.eval_metrics``
      (``context_precision`` / ``mrr`` / ``recall_at_k``), 미검증 비율은
      ``eval_metrics.unverified_ratio`` 를 그대로 호출한다.
    - 중복제거·고유 소스     : ``research.dedup.dedup_sources`` / ``source_key``
      (정규 URL/DOI 키, first-wins).
    - 출처 신뢰도            : ``research.rank.source_authority``.
    - 최신성 필터            : ``research.rank.apply_recency`` (창 이내 결정적 필터).
    - 정규 데이터 모델        : ``research.models.ResearchMetrics`` 등.

설계 원칙:
    - **순수/결정적**: 네트워크·파일 I/O·전역 상태·난수가 없다. ``datetime.now()`` 는
      ``apply_recency`` 가 상대 일수 창을 해석할 때만 내부적으로 쓰이며, 결정적
      평가가 필요하면 ``now`` 를 명시 주입한다(절대 날짜 창은 ``now`` 무관).
    - **방어적**: golden 항목은 dataclass 또는 dict(예: JSON 로드) 결과를 모두
      수용한다. dict 결과는 속성 접근이 필요한 재사용 함수(``apply_recency``)를 위해
      경량 객체로 코어싱한다. 누락 필드는 예외 없이 기본값으로 처리한다(P8 정합).

이 모듈은 **지표 산출만** 담당한다. baseline 회귀 게이트·golden 스냅샷·실행
스크립트(``scripts/eval_research_quality.py``)는 후속 태스크(16.2)에서 이 하네스를
호출해 구현한다.

golden 항목 스키마(dict, 키는 모두 선택적이며 별칭 허용):
    {
      "query": str,                         # 질의(표시/집계 라벨용)
      "results": [SearchResult|PaperResult|EvidenceSource|dict, ...],
                                            # 검색/재랭킹된 결과(순위 순서). 별칭: "retrieved"
      "relevant_ids": [str, ...],           # 정답(관련) 소스 식별자. 별칭: "relevant"/"expected"
                                            #   "web:<url>"/"doi:<doi>" 또는 원시 url/doi 모두 허용
      "raw_count": int,                     # (선택) dedup 이전 병합 후보 수. 없으면 len(results)
                                            #   별칭: "raw_results"(리스트) → 길이 사용
      "recency_window": <days|date|iso>,    # (선택) 최신성 창. 별칭: "recency_days"
      "report": ResearchReport|dict|None,   # (선택) 인용 정확도 산출용
      "n_citations"/"n_unverified": int,    # (선택) 인용 정확도 직접 지정
      "unverified_ratio": float,            # (선택) 미검증 비율 직접 지정
    }

Requirements: 9.1, 9.7, 16.1
Design: "품질 평가 하네스 + baseline 회귀 게이트 (요구사항 9)", ``eval_harness.py`` 행
"""

from types import SimpleNamespace

from ai_engine.rag import eval_metrics as em

from .config import DeepResearchConfig
from .dedup import canonical_doi, canonical_url, dedup_sources, source_key
from .models import ResearchMetrics
from .rank import apply_recency, source_authority

__all__ = [
    "DEFAULT_K",
    "HIGHER_IS_BETTER_METRICS",
    "result_id",
    "evaluate_query",
    "evaluate_golden_set",
    "aggregate_metrics",
    "to_research_metrics",
    "check_regression",
]

DEFAULT_K = 10

# 집계·per-query dict 에 공통으로 나타나는 수치 지표 키(집계는 이들의 산술평균).
_METRIC_KEYS = (
    "precision_at_k",
    "mrr",
    "recall_at_k",
    "recency_score",
    "authority_score",
    "citation_accuracy",
    "coverage_sources",
    "coverage_providers",
    "dedup_ratio",
)

# 회귀 게이트가 감시하는 지표는 **모두 높을수록 좋은(higher-is-better)** 지표다.
# precision@k·MRR·recall@k(관련성), recency_score(최신성), authority_score(출처
# 신뢰도), citation_accuracy(= 1 − 미검증 비율, 인용 정확도), coverage_*(커버리지),
# dedup_ratio(중복제거 감소율) — 어느 하나라도 baseline 대비 하락하면 품질 저하다.
# (미검증 비율처럼 낮을수록 좋은 지표는 이미 citation_accuracy 로 반전되어 있어
#  이 집합에는 lower-is-better 지표가 존재하지 않는다 — 방향 혼동 방지.)
HIGHER_IS_BETTER_METRICS = _METRIC_KEYS


# ---------------------------------------------------------------------------
# 입력 코어싱/필드 접근 (dict ↔ dataclass 양쪽 방어)
# ---------------------------------------------------------------------------
def _as_obj(item):
    """dict 결과를 속성 접근이 가능한 경량 객체로 코어싱한다(순수).

    ``rank.apply_recency`` 는 발행일·관련성을 ``getattr`` 로 읽으므로 dict 결과는
    ``SimpleNamespace`` 로 감싸 속성 접근을 가능하게 한다. dataclass·기타 객체는
    그대로 반환한다. 비식별자 키 등으로 코어싱이 실패하면 원본 dict 를 반환한다
    (``dedup``/``source_authority`` 는 dict 도 처리하므로 비차단).
    """
    if isinstance(item, dict):
        try:
            return SimpleNamespace(**item)
        except TypeError:
            return item
    return item


def _field(obj, name: str, default=None):
    """dict(.get) 또는 객체(getattr) 양쪽에서 필드를 안전하게 읽는다(순수)."""
    if obj is None:
        return default
    if isinstance(obj, dict):
        v = obj.get(name, default)
    else:
        v = getattr(obj, name, default)
    return default if v is None else v


def _first(item: dict, *names, default=None):
    """dict 에서 여러 별칭 키 중 먼저 존재하는(그리고 None 이 아닌) 값을 반환한다."""
    for n in names:
        if n in item and item[n] is not None:
            return item[n]
    return default


def _clamp01(x: float) -> float:
    """값을 ``[0.0, 1.0]`` 로 클램프한다(비수치는 0.0)."""
    try:
        f = float(x)
    except (TypeError, ValueError):
        return 0.0
    if f < 0.0:
        return 0.0
    return 1.0 if f > 1.0 else f


# ---------------------------------------------------------------------------
# 소스 식별자 정규화 (precision/MRR/recall 대조용)
# ---------------------------------------------------------------------------
def result_id(item) -> str:
    """결과/식별자 문자열을 정규 ``source_id`` 키로 변환한다 (dedup 규약 계승).

    - 결과 객체(dataclass/dict): ``dedup.source_key`` 로 ``web:<canonical_url>`` /
      ``doi:<canonical_doi>`` 키를 산출한다.
    - 문자열: ``web:``/``doi:`` 프리픽스가 있으면 각각 ``canonical_url``/``canonical_doi``
      로 재정규화해 결과 객체 경로와 **동일한** 정규화를 적용한다(트레일링 슬래시 등
      차이로 인한 대조 불일치 방지). 프리픽스가 없으면 ``source_key`` 로 판정한다.

    관련(golden) 식별자와 검색 결과 식별자를 같은 규칙으로 정규화하므로, golden
    세트를 정규 ``source_id`` 로 저장하든 원시 url/doi 로 저장하든 일관되게 대조된다.
    """
    if isinstance(item, str):
        s = item.strip()
        low = s.lower()
        if low.startswith("doi:"):
            return "doi:" + canonical_doi(s[4:])
        if low.startswith("web:"):
            return "web:" + canonical_url(s[4:])
        return source_key(s)
    return source_key(item)


def _relevant_set(item: dict) -> set:
    """golden 항목의 관련 식별자 집합을 정규 키 집합으로 반환한다."""
    rel = _first(item, "relevant_ids", "relevant", "expected", default=[])
    if isinstance(rel, (str, bytes)):
        rel = [rel]
    out = set()
    for r in rel or []:
        rid = result_id(r)
        if rid:
            out.add(rid)
    return out


# ---------------------------------------------------------------------------
# 개별 지표 산출 (모두 기존 자산 재사용)
# ---------------------------------------------------------------------------
def _recency_score(objs: list, window, now) -> float:
    """최신성 점수 = 창 이내(또는 발행일 확정) 결과 비율 (rank.apply_recency 재사용).

    ``window`` 가 주어지면 창 이내 결과 비율, ``None`` 이면 발행일이 확정된 결과
    비율을 산출한다. 어느 경우든 발행일 미상 항목은 ``exclude`` 규칙으로 분모에서
    제외하지 않고(분모는 전체 결과 수) 분자에서만 빠지므로, "최신 근거를 얼마나
    확보했는가"를 ``[0,1]`` 로 나타낸다.
    """
    n = len(objs)
    if n == 0:
        return 0.0
    kept = apply_recency(objs, window, "exclude", now=now)
    return _clamp01(len(kept) / n)


def _authority_score(objs: list) -> float:
    """출처 신뢰도 점수 = 소스별 ``rank.source_authority`` 신호의 산술평균."""
    n = len(objs)
    if n == 0:
        return 0.0
    total = 0.0
    for o in objs:
        total += source_authority(o)
    return _clamp01(total / n)


def _citation_accuracy(item: dict) -> float:
    """인용 정확도 = 1 − 미검증 인용 비율 (eval_metrics.unverified_ratio 재사용).

    산출 우선순위:
        1. ``report`` 의 ``citations`` dict → verified/unverified 개수로 비율 산출.
        2. ``report``/항목의 명시적 ``unverified_ratio`` 값.
        3. 항목의 ``n_citations``/``n_unverified`` 개수.
        4. 정보 없음 → 인용 0건으로 간주(미검증 비율 0.0 → 정확도 1.0).

    미검증 비율 계산은 ``eval_metrics.unverified_ratio`` 를 그대로 호출한다.
    """
    rep = item.get("report")
    if rep is not None:
        cits = _field(rep, "citations", None)
        if isinstance(cits, dict):
            verified = cits.get("verified") or []
            unverified = cits.get("unverified") or []
            n_cit = len(verified) + len(unverified)
            ratio = em.unverified_ratio(len(unverified), n_cit)
            return _clamp01(1.0 - ratio)
        ur = _field(rep, "unverified_ratio", None)
        if isinstance(ur, (int, float)):
            return _clamp01(1.0 - float(ur))

    if "n_citations" in item:
        n_cit = int(item.get("n_citations") or 0)
        n_unv = int(item.get("n_unverified") or 0)
        ratio = em.unverified_ratio(n_unv, n_cit)
        return _clamp01(1.0 - ratio)

    if "unverified_ratio" in item:
        return _clamp01(1.0 - float(item.get("unverified_ratio") or 0.0))

    # 인용 정보 없음: unverified_ratio(0, 0) == 0.0 → 정확도 1.0 (design 정합).
    return _clamp01(1.0 - em.unverified_ratio(0, 0))


def _coverage_providers(objs: list) -> int:
    """고유 제공자 수 = 비어있지 않은 ``provider`` 필드의 고유 개수."""
    seen = set()
    for o in objs:
        p = _field(o, "provider", "")
        if isinstance(p, str):
            p = p.strip()
        if p:
            seen.add(p)
    return len(seen)


def _dedup_ratio(objs: list, raw_count) -> float:
    """중복제거 감소율 = 1 − (고유 소스 수 / 원본 수). (dedup_sources 재사용)

    ``raw_count`` 는 병합(중복 포함) 후보 수다. 미지정이면 결과 수를 사용하므로
    "결과 내 중복 비율"을 나타낸다. 값은 ``[0, 1]`` 로 클램프한다.
    """
    unique = len(dedup_sources(objs))
    if isinstance(raw_count, int) and raw_count > 0:
        raw = raw_count
    else:
        raw = len(objs)
    if raw <= 0:
        return 0.0
    return _clamp01(1.0 - (unique / raw))


def _raw_count_of(item: dict, objs: list) -> int:
    """golden 항목에서 병합 원본 수를 해석한다(``raw_count`` 또는 ``raw_results`` 길이)."""
    rc = item.get("raw_count")
    if isinstance(rc, int) and rc > 0:
        return rc
    raw_results = item.get("raw_results")
    if isinstance(raw_results, (list, tuple)):
        return len(raw_results)
    return len(objs)


# ---------------------------------------------------------------------------
# per-query / 집계 평가
# ---------------------------------------------------------------------------
def evaluate_query(golden_item: dict, *, k: int = DEFAULT_K, now=None,
                   recency_unknown: str = "last") -> dict:
    """golden 질의 1건의 품질 지표를 산출한다(순수/결정적).

    Args:
        golden_item: golden 항목 dict(모듈 docstring 스키마 참조). ``None``/비dict 는
            빈 항목으로 방어한다.
        k: precision@k / recall@k 의 k(1 이상 권장, 비정상 값은 eval_metrics 규약 적용).
        now: 상대 일수 최신성 창의 기준 시각(결정적 평가용 주입). 절대 날짜 창·창
            미지정 시에는 사용되지 않는다.
        recency_unknown: (예약) 현재 최신성 점수는 창 이내 비율 산출에 ``exclude``
            규칙을 사용하며, 이 인자는 호출 호환성을 위해 유지한다.

    Returns:
        지표 dict. 키: ``query``, ``k``, ``n_results`` 및 ``_METRIC_KEYS`` 전체.
        ``recall_at_k`` 를 포함해 재사용 지표를 모두 노출한다(``ResearchMetrics`` 는
        recall 필드가 없으므로 dict 에만 존재).
    """
    if not isinstance(golden_item, dict):
        golden_item = {}
    k = int(k) if isinstance(k, (int, float)) else DEFAULT_K

    query = str(golden_item.get("query", "") or "")
    raw_results = _first(golden_item, "results", "retrieved", default=[]) or []
    objs = [_as_obj(r) for r in raw_results]

    # --- 관련성: precision@k / MRR / recall@k (eval_metrics 재사용) ---
    retrieved_ids = [source_key(o) for o in objs]
    relevant = _relevant_set(golden_item)
    precision = em.context_precision(relevant, retrieved_ids, k)
    mrr_val = em.mrr(relevant, retrieved_ids)
    recall = em.recall_at_k(relevant, retrieved_ids, k)

    # --- 최신성 / 신뢰도 / 인용 정확도 / 커버리지 / 중복제거 ---
    window = _first(golden_item, "recency_window", "recency_days", default=None)
    recency = _recency_score(objs, window, now)
    authority = _authority_score(objs)
    citation_acc = _citation_accuracy(golden_item)
    coverage_sources = len(dedup_sources(objs))
    coverage_providers = _coverage_providers(objs)
    dedup_ratio = _dedup_ratio(objs, _raw_count_of(golden_item, objs))

    return {
        "query": query,
        "k": k,
        "n_results": len(objs),
        "precision_at_k": float(precision),
        "mrr": float(mrr_val),
        "recall_at_k": float(recall),
        "recency_score": float(recency),
        "authority_score": float(authority),
        "citation_accuracy": float(citation_acc),
        "coverage_sources": int(coverage_sources),
        "coverage_providers": int(coverage_providers),
        "dedup_ratio": float(dedup_ratio),
    }


def aggregate_metrics(per_query: list) -> dict:
    """per-query 지표들의 산술평균 집계를 산출한다(순수).

    ``_METRIC_KEYS`` 각 지표의 평균을 계산한다. 커버리지(정수) 지표도 평균이므로
    집계값은 float 다(질의당 평균 고유 소스/제공자 수). 질의가 없으면 모든 값 0.0.

    Args:
        per_query: ``evaluate_query`` 결과 dict 의 리스트.

    Returns:
        ``n_queries`` 와 ``_METRIC_KEYS`` 평균을 담은 집계 dict.
    """
    items = [m for m in (per_query or []) if isinstance(m, dict)]
    n = len(items)
    agg = {"n_queries": n}
    for key in _METRIC_KEYS:
        if n == 0:
            agg[key] = 0.0
        else:
            total = 0.0
            for m in items:
                try:
                    total += float(m.get(key, 0.0) or 0.0)
                except (TypeError, ValueError):
                    total += 0.0
            agg[key] = total / n
    return agg


def evaluate_golden_set(golden_set, *, k: int = DEFAULT_K, now=None,
                        recency_unknown: str = "last") -> dict:
    """golden 질의 세트 전체를 평가해 per-query + 집계 지표를 산출한다(순수/결정적).

    Args:
        golden_set: golden 항목 dict 의 리스트, 또는 ``{"queries": [...]}`` dict.
        k: precision@k / recall@k 의 k.
        now: 상대 일수 최신성 창 기준 시각(결정적 평가용 주입).
        recency_unknown: 최신성 미상 처리 규칙(호출 호환용).

    Returns:
        ``{"k", "n_queries", "per_query": [dict...], "aggregate": dict}``.
    """
    if isinstance(golden_set, dict):
        items = golden_set.get("queries") or golden_set.get("golden") or []
    else:
        items = golden_set or []

    per_query = [
        evaluate_query(it, k=k, now=now, recency_unknown=recency_unknown)
        for it in items
        if isinstance(it, dict)
    ]
    aggregate = aggregate_metrics(per_query)
    return {
        "k": int(k) if isinstance(k, (int, float)) else DEFAULT_K,
        "n_queries": len(per_query),
        "per_query": per_query,
        "aggregate": aggregate,
    }


def to_research_metrics(metrics: dict) -> ResearchMetrics:
    """지표 dict(per-query 또는 집계)를 정규 ``ResearchMetrics`` dataclass 로 변환한다.

    ``recall_at_k`` 는 ``ResearchMetrics`` 에 대응 필드가 없어 매핑에서 제외된다.
    누락 키는 dataclass 기본값(0/0.0)으로 채운다.
    """
    m = metrics or {}
    return ResearchMetrics(
        precision_at_k=float(m.get("precision_at_k", 0.0) or 0.0),
        mrr=float(m.get("mrr", 0.0) or 0.0),
        recency_score=float(m.get("recency_score", 0.0) or 0.0),
        authority_score=float(m.get("authority_score", 0.0) or 0.0),
        citation_accuracy=float(m.get("citation_accuracy", 0.0) or 0.0),
        coverage_sources=int(m.get("coverage_sources", 0) or 0),
        coverage_providers=int(m.get("coverage_providers", 0) or 0),
        dedup_ratio=float(m.get("dedup_ratio", 0.0) or 0.0),
    )


# ---------------------------------------------------------------------------
# baseline 회귀 게이트 (요구사항 9.7)
# ---------------------------------------------------------------------------
def _num(v) -> float:
    """지표 값을 float 로 안전 변환한다(결측·비수치 → 0.0). 카운트/비율 공용."""
    try:
        return float(v)
    except (TypeError, ValueError):
        return 0.0


def _aggregate_of(metrics) -> dict:
    """집계 지표 dict 를 방어적으로 추출한다(순수).

    ``evaluate_golden_set`` 의 전체 결과(``{"aggregate": {...}, ...}``)를 넘겨도,
    이미 뽑아낸 집계 dict 를 넘겨도 동일하게 동작하도록 한다. ``ResearchMetrics``
    dataclass 를 넘기면 그 ``__dict__`` 를 사용한다. 그 외/``None`` 은 빈 dict.
    """
    if metrics is None:
        return {}
    if isinstance(metrics, ResearchMetrics):
        return dict(vars(metrics))
    if isinstance(metrics, dict):
        agg = metrics.get("aggregate")
        if isinstance(agg, dict):
            return agg
        return metrics
    # dataclass/객체 방어(속성 접근 가능한 경우).
    if hasattr(metrics, "__dict__"):
        return dict(vars(metrics))
    return {}


def _resolve_tolerance(tolerance) -> float:
    """허용 하락폭(tolerance)을 해석한다.

    ``None`` 이면 ``AE_RESEARCH_REGRESSION_TOLERANCE``(env, 기본 0.05)에서 로딩한다.
    수치 파싱 실패 시에도 env 기본값으로 폴백하며, 음수는 0.0 으로 클램프한다
    (음수 tolerance 는 모든 하락을 회귀로 만드는 무의미 값이므로).
    """
    if tolerance is None:
        t = DeepResearchConfig.from_env().regression_tolerance
    else:
        try:
            t = float(tolerance)
        except (TypeError, ValueError):
            t = DeepResearchConfig.from_env().regression_tolerance
    return t if t >= 0.0 else 0.0


def check_regression(current, baseline, tolerance=None) -> dict:
    """현재 집계 지표를 baseline 과 비교해 품질 회귀 여부를 판정한다(순수/결정적).

    요구사항 9.7: 관련성·최신성·출처 신뢰도·인용 정확도·커버리지·중복제거 지표가
    각각 baseline 대비 **구성된 허용 하락폭**(``AE_RESEARCH_REGRESSION_TOLERANCE``,
    기본 0.05 == 5%)을 **초과하여 낮아지면** 회귀로 판정한다.

    방향(direction): 감시 대상(``HIGHER_IS_BETTER_METRICS``)은 모두 높을수록 좋은
    지표이므로 "하락 = 나쁨"이다. 지표별 상대 하락폭
    ``relative_drop = (baseline - current) / baseline`` 이 ``tolerance`` 를 **엄격히
    초과**(``>``)하면 해당 지표를 회귀로 표기한다. 상대 하락폭을 쓰므로 [0,1] 비율
    지표와 개수형 커버리지 지표(질의당 평균 소스/제공자 수)에 동일 임계값을 일관
    적용할 수 있다("baseline 대비 %"). 절대 하락폭도 함께 보고한다.

    경계 처리:
        - ``baseline <= 0`` 인 지표는 회귀 불가로 본다. 지표는 음수가 될 수 없어
          더 낮아질 수 없고, baseline 이 0 이면 상대 하락폭이 정의되지 않는다.
        - ``current >= baseline``(개선·동일)이면 하락폭이 0 이하라 회귀가 아니다.
        - ``current == baseline`` 이면 모든 하락폭 0 → ``regressed=False`` (자기
          자신 대비 무회귀 — 실행기가 자신의 baseline 을 통과하는 근거).

    Args:
        current: 현재 집계 dict, ``evaluate_golden_set`` 결과 dict, 또는
            ``ResearchMetrics``. ``_aggregate_of`` 로 방어적 추출한다.
        baseline: 비교 기준 집계(형태는 ``current`` 와 동일하게 허용).
        tolerance: 허용 상대 하락폭. ``None`` 이면 env(config)에서 로딩.

    Returns:
        {
          "regressed": bool,              # 하나라도 회귀했으면 True
          "tolerance": float,             # 적용된 허용 하락폭
          "regressed_metrics": [str],     # 회귀한 지표 키(정렬)
          "details": {                    # 지표별 상세
             key: {"baseline", "current", "drop", "relative_drop", "regressed"},
             ...
          }
        }
    """
    tol = _resolve_tolerance(tolerance)
    cur_agg = _aggregate_of(current)
    base_agg = _aggregate_of(baseline)

    details: dict = {}
    regressed_metrics: list[str] = []
    for key in HIGHER_IS_BETTER_METRICS:
        base_v = _num(base_agg.get(key))
        cur_v = _num(cur_agg.get(key))
        drop = base_v - cur_v                     # 양수면 하락(나쁨), 음수면 개선
        if base_v > 0.0:
            rel_drop = drop / base_v
        else:
            rel_drop = 0.0                         # baseline 0 이하 → 회귀 불가
        is_reg = rel_drop > tol                    # "초과"(엄격히 큼)만 회귀
        details[key] = {
            "baseline": base_v,
            "current": cur_v,
            "drop": drop,
            "relative_drop": rel_drop,
            "regressed": is_reg,
        }
        if is_reg:
            regressed_metrics.append(key)

    return {
        "regressed": len(regressed_metrics) > 0,
        "tolerance": tol,
        "regressed_metrics": sorted(regressed_metrics),
        "details": details,
    }
