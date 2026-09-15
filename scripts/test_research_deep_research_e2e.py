# Feature: deep-research-engine, Task 14.2: 딥리서치 e2e 통합 테스트(mock 제공자)
"""E2E integration test: 딥리서치 파이프라인 1건 실행 (mock 제공자, 네트워크/Gateway 0).

Feature: deep-research-engine, Task 14.2
**Validates: Requirements 5, 6**

이 테스트는 `ai_engine.research.deep_research.run_deep_research(_sync)` 의 전 경로
(분해 → 검색 → 종합 → 평가)를 **실제 네트워크·Gateway 없이** 밀폐(hermetic)하게
1건 실행하고 다음을 검증한다:

- 한 번의 전체 실행이 완료되어 `ResearchReport` 를 반환한다.
- mock 제공자로부터 근거가 수집됐다(`metrics.coverage_sources > 0`, 다중 제공자).
- 리포트 인용이 근거 소스 식별자(`EvidenceSource.source_id`)에 묶여 있다
  (verified ⊆ 근거 source_id 집합 — 인용 참조 무결성 방향, 요구사항 8/P6).
- Planner(`plan_subqueries`)를 ≥1회, Evaluator(`should_deepen`)를 ≥1회 경유한다
  (요구사항 6.5). 두 함수를 스파이로 감싸 호출 횟수를 관측한다.
- 실행이 비차단으로 종료하며(예외 없음) 심화 반복 횟수가 상한 이하다
  (`deepening_count <= Deepening_Cap` — 유한 종료 P13 / 요구사항 5.8).

## 목(mock) 대상과 이유 (밀폐 보장)

**제공자 egress (backend 단일 egress 지점):** deep_research 는 외부 검색·본문 조회를
전부 `ai_engine.research.backend` 를 경유해 수행한다(요구사항 10.4). 따라서 다음
backend seam 만 monkeypatch 하면 네트워크가 전혀 발생하지 않는다:
  - `backend.web_research_enabled` → 항상 True (옵트인 게이트를 켜 웨이브가 실제로
    돌게 함 — 이 게이트가 off면 무회귀로 빈 근거만 나온다, P15).
  - `backend.search_web_with_fallback` / `search_academic_with_fallback` → 첫 성공
    제공자의 canned 원시 응답(`{"ok": True, "provider", "raw"}`)을 반환.
  - `backend.fetch_url_raw` → `ok=True` + canned 본문의 `FetchResult` 를 반환.
정규화(normalize)·융합/재랭킹(rank.merge_and_rerank, 결정적 RRF)·인용 검증
(rag/citation)은 **실제 코드 그대로** 통과시켜 통합 경로를 진짜로 검증한다.

**LLM/Gateway:** 두 가지 접근을 모두 사용한다(각각 다른 경로를 커버).
  1. `deps=None` (기본): Planner 는 단일 하위질의 폴백, Generator 는 결정적 폴백
     리포트(근거를 그 source_id 인용과 함께 나열)를 생성한다. Gateway 를 전혀 거치지
     않으면서도 분해→검색→종합→평가 전 경로를 실행하고, Planner·Evaluator 경유를
     증명한다. 이 접근을 **주 시나리오**로 삼는다(가장 밀폐적·견고).
  2. 경량 가짜 Gateway 주입(`_FakeGatewayChatModel`): `GatewayChatModel` 을 가짜로
     대체해 Planner 가 **다중 하위질의**로 분해하고 Generator 가 **LLM 종합**(근거
     source_id 인용 포함)을 수행하는 경로까지 증명한다.

`AE_ANSWER_QUALITY=0` 을 설정해 종합 후 근거성 메타데이터(enhance_answer)의 로컬
임베딩/faithfulness 를 끈다(모델 다운로드·게이트웨이 호출 회피 — 완전 밀폐). 산출물
경로는 `AE_GENERATED_ROOT=<tmp>` 로 임시 디렉터리에 격리한다(userData 하위 영속 P10을
tmp 로 리다이렉트 — 홈 디렉터리 오염 방지).

Stack: Python 3.11+, pytest. 기존 scripts/test_*.py 관례를 따른다(sys.path 삽입 후
ai_engine import, __main__ 에서 pytest 실행). 실제 소켓·Gateway 호출 0(hermetic).
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

# ai_engine 패키지를 직접 실행 시에도 import 할 수 있도록 저장소 루트를 경로에 추가.
_REPO_ROOT = Path(__file__).resolve().parent.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

import pytest  # noqa: E402

from ai_engine.research import backend, deep_research  # noqa: E402
from ai_engine.research.config import DeepResearchConfig  # noqa: E402
from ai_engine.research.models import ResearchReport  # noqa: E402


# =========================================================================== #
# Canned 제공자 원시 응답 (backend 폴백 체인 성공 계약 형태)
# =========================================================================== #
# search_*_with_fallback 성공 반환 계약: {"ok": True, "provider": <name>, "raw": {...}}.
# provider name 은 backend/providers 어댑터와 정합해야 정규화가 매핑을 찾는다:
#   - 웹 tavily      → raw["results"][] (title/url/content/published_date/score)
#   - 학술 semantic_scholar → raw["data"][] (title/authors[].name/year/venue/abstract/
#     externalIds.DOI/url/citationCount/score)
# 정규화·융합은 실제 코드가 수행하므로 여기서는 어댑터가 읽는 원시 필드만 채운다.

_WEB_RAW = {
    "ok": True,
    "kind": "web",
    "provider": "tavily",
    "raw": {
        "results": [
            {
                "title": "Alpha 개요 문서",
                "url": "https://alpha.example.com/post",
                "content": "Alpha 소스는 조사 주제의 개요와 핵심 개념을 설명한다.",
                "published_date": "2024-03-01",
                "score": 0.95,
            },
            {
                "title": "Beta 심화 기사",
                "url": "https://beta.example.org/news",
                "content": "Beta 소스는 조사 주제의 최신 동향과 쟁점을 다룬다.",
                "published_date": "2024-02-01",
                "score": 0.80,
            },
        ]
    },
    "attempts": [{"provider": "tavily", "ok": True}],
}

_ACAD_RAW = {
    "ok": True,
    "kind": "academic",
    "provider": "semantic_scholar",
    "raw": {
        "data": [
            {
                "title": "Gamma 학술 논문",
                "authors": [{"name": "G. Researcher"}],
                "year": 2023,
                "venue": "Nature",
                "abstract": "Gamma 논문은 조사 주제의 이론적 근거를 제시한다.",
                "externalIds": {"DOI": "10.1000/gamma"},
                "url": "https://doi.org/10.1000/gamma",
                "citationCount": 120,
                "score": 0.90,
            },
            {
                "title": "Delta 학술 논문",
                "authors": [{"name": "D. Scholar"}],
                "year": 2022,
                "venue": "Science",
                "abstract": "Delta 논문은 조사 주제의 실험적 결과를 보고한다.",
                "externalIds": {"DOI": "10.1000/delta"},
                "url": "https://doi.org/10.1000/delta",
                "citationCount": 30,
                "score": 0.70,
            },
        ]
    },
    "attempts": [{"provider": "semantic_scholar", "ok": True}],
}

# 위 canned 응답이 정규화되면 하위질의당 4개 고유 소스가 나온다(웹 2 + 논문 2),
# 제공자는 2개(tavily, semantic_scholar). source_id 스킴: web:<canonical_url> /
# doi:<canonical_doi>. 정확한 정규형은 실제 코드가 결정하므로 테스트는 하드코딩하지
# 않고 report.evidence_snapshot 에서 파생해 대조한다.
_EXPECTED_SOURCES_PER_SUBQUERY = 4
_EXPECTED_PROVIDERS = 2


# =========================================================================== #
# 목(mock)/스파이 설치 헬퍼
# =========================================================================== #
def _install_backend_mocks(monkeypatch, *, web_raw=_WEB_RAW, acad_raw=_ACAD_RAW):
    """backend egress seam 을 canned 응답으로 대체한다(네트워크 0).

    - web_research_enabled → True (옵트인 게이트 ON → 웨이브 실행).
    - search_web/academic_with_fallback → 첫 성공 제공자 canned 원시 응답.
    - fetch_url_raw → ok=True + canned 본문(빈 URL 만 ok=False).
    모든 mock 은 async 시그니처를 맞추고 인자를 무시한다(질의/타임아웃 등).
    """

    async def _fake_web(query, *args, **kwargs):
        return web_raw

    async def _fake_academic(query, *args, **kwargs):
        return acad_raw

    async def _fake_fetch(url, *args, **kwargs):
        u = url if isinstance(url, str) else ""
        if not u.strip():
            return backend.FetchResult(ok=False, url=u, error="invalid_url")
        body = f"[canned body] {u} — 이 소스는 조사 주제에 대한 원문 근거를 담고 있다."
        return backend.FetchResult(
            ok=True, url=u, text=body, chars=len(body), truncated=False
        )

    monkeypatch.setattr(backend, "web_research_enabled", lambda *a, **k: True)
    monkeypatch.setattr(backend, "search_web_with_fallback", _fake_web)
    monkeypatch.setattr(backend, "search_academic_with_fallback", _fake_academic)
    monkeypatch.setattr(backend, "fetch_url_raw", _fake_fetch)


def _install_spies(monkeypatch):
    """Planner(plan_subqueries)·Evaluator(should_deepen)를 스파이로 감싼다.

    run_deep_research 는 두 함수를 deep_research 모듈 전역으로 호출하므로, 모듈
    속성을 래퍼로 교체하면 호출 횟수를 관측할 수 있다(원 함수에 위임 — 동작 보존).

    Returns:
        (plan_calls, eval_count): plan_calls 는 각 Planner 호출의 반환 하위질의
        리스트를 순서대로 담고(다중 분해 관측용), eval_count["n"] 은 Evaluator
        평가 횟수다.
    """
    plan_calls: list = []
    eval_count = {"n": 0}
    orig_plan = deep_research.plan_subqueries
    orig_should = deep_research.should_deepen

    async def _plan_wrapper(*args, **kwargs):
        res = await orig_plan(*args, **kwargs)
        plan_calls.append(list(res) if res else [])
        return res

    def _should_wrapper(*args, **kwargs):
        eval_count["n"] += 1
        return orig_should(*args, **kwargs)

    monkeypatch.setattr(deep_research, "plan_subqueries", _plan_wrapper)
    monkeypatch.setattr(deep_research, "should_deepen", _should_wrapper)
    return plan_calls, eval_count


def _hermetic_env(monkeypatch, tmp_path):
    """산출물 경로를 tmp 로 격리하고 근거성 후처리를 꺼 완전 밀폐를 보장한다.

    - AE_GENERATED_ROOT=<tmp>: 리포트/체크포인트/스토어를 tmp 하위에만 기록(P10을
      tmp 로 리다이렉트, 홈 디렉터리 오염 방지).
    - AE_ANSWER_QUALITY=0: enhance_answer 의 로컬 임베딩·faithfulness 를 꺼
      모델 다운로드·게이트웨이 호출을 회피(밀폐·고속). 인용/커버리지 지표는 순수
      경로라 영향 없음.
    """
    monkeypatch.setenv("AE_GENERATED_ROOT", str(tmp_path))
    monkeypatch.setenv("AE_ANSWER_QUALITY", "0")


def _assert_citations_tied_to_evidence(report: ResearchReport):
    """리포트 인용(verified)이 근거 source_id 집합에 묶여 있음을 검증한다(P6 방향).

    검증된 인용은 모두 수집된 근거 소스 식별자를 참조해야 하고(dangling 없음),
    적어도 하나의 검증된 인용이 존재해야 한다(종합이 근거를 실제 인용).
    """
    evidence_ids = {
        e.source_id for e in report.evidence_snapshot if getattr(e, "source_id", "")
    }
    assert evidence_ids, "근거 스냅샷에 source_id 가 있어야 한다"
    citations = report.citations or {}
    verified = list(citations.get("verified") or [])
    unverified = list(citations.get("unverified") or [])
    assert verified, "검증된 인용이 최소 1개 있어야 한다(근거를 실제로 인용)"
    # 참조 무결성 방향(P6): verified ⊆ 근거 source_id 집합.
    dangling = [sid for sid in verified if sid not in evidence_ids]
    assert not dangling, f"verified 인용이 근거 집합 밖을 참조: {dangling}"
    # 분류 누락 없음: verified ∪ unverified 는 상호 배타(합집합이 전체 인용).
    assert not (set(verified) & set(unverified)), "verified/unverified 가 겹침"


# =========================================================================== #
# Test 1 (주 시나리오) — deps=None 전 경로 1건 실행 + Planner/Evaluator 경유 검증
# =========================================================================== #
def test_deep_research_e2e_mock_providers_completes(monkeypatch, tmp_path):
    """분해→검색→종합→평가 1건 실행: ResearchReport 반환·근거 수집·인용 결속·비차단.

    deps=None 이므로 Planner 는 단일 하위질의 폴백, Generator 는 결정적 폴백 리포트를
    쓴다(Gateway 미경유). 그럼에도 전 경로가 실행되어 mock 제공자 근거를 수집하고,
    Planner·Evaluator 를 각각 최소 1회 경유하며, 심화 없이 비차단 종료한다.
    """
    _hermetic_env(monkeypatch, tmp_path)
    _install_backend_mocks(monkeypatch)
    plan_calls, eval_count = _install_spies(monkeypatch)

    # 커버리지가 충족되도록(min_sources/min_providers ≤ 확보량) 설정 → 심화 불필요.
    config = DeepResearchConfig(
        top_k=10,
        max_subqueries=8,
        fetch_per_subquery=5,
        max_deepening=2,
        min_sources=2,
        min_providers=2,
        unverified_threshold=0.2,
    )

    report = deep_research.run_deep_research_sync(
        "조사 주제에 대한 심층 분석", None, session_id="e2e-primary", config=config
    )

    # 1) 전체 실행 완료 → ResearchReport 반환.
    assert isinstance(report, ResearchReport)
    assert isinstance(report.report_markdown, str) and report.report_markdown.strip()

    # 2) mock 제공자로부터 근거 수집(coverage_sources > 0, 다중 제공자).
    assert report.metrics.coverage_sources == _EXPECTED_SOURCES_PER_SUBQUERY
    assert report.metrics.coverage_sources > 0
    assert report.metrics.coverage_providers == _EXPECTED_PROVIDERS
    assert len(report.evidence_snapshot) == _EXPECTED_SOURCES_PER_SUBQUERY

    # 3) 인용이 근거 source_id 에 묶임(P6 방향) — 폴백 리포트도 근거만 인용.
    _assert_citations_tied_to_evidence(report)
    assert report.citations.get("unverified") == []  # 폴백은 근거만 인용 → 미검증 0
    assert report.unverified_ratio == 0.0
    assert report.metrics.citation_accuracy == pytest.approx(1.0)

    # 4) Planner ≥1회, Evaluator ≥1회 경유(요구사항 6.5).
    assert len(plan_calls) >= 1, "Planner(plan_subqueries) 미경유"
    assert eval_count["n"] >= 1, "Evaluator(should_deepen) 미평가"

    # 5) 비차단 종료 + 심화 반복 유한(P13): deepening_count ≤ cap.
    assert report.deepening_count <= config.max_deepening
    assert report.deepening_count == 0  # 커버리지 충족 → 심화 없음


# =========================================================================== #
# Test 2 — 강제 심화 시 상한에서 유한 종료(P13 / 요구사항 5.8)
# =========================================================================== #
def test_deep_research_e2e_deepening_terminates_at_cap(monkeypatch, tmp_path):
    """커버리지가 절대 충족될 수 없어도 심화 루프는 Deepening_Cap 에서 유한 종료한다.

    min_sources 를 확보 가능량보다 크게 설정하면 should_deepen 이 계속 심화를
    요구하지만, deepening_count 가 cap 에 도달하면 커버리지와 무관하게 False 를
    반환해 루프가 종료한다(P13). 종료 후에도 확보된 근거로 비차단 리포트를 반환한다.
    """
    _hermetic_env(monkeypatch, tmp_path)
    _install_backend_mocks(monkeypatch)
    plan_calls, eval_count = _install_spies(monkeypatch)

    cap = 2
    config = DeepResearchConfig(
        fetch_per_subquery=5,
        max_deepening=cap,
        min_sources=100,       # 확보 불가 → 커버리지 조건이 항상 심화를 요구
        min_providers=2,
        unverified_threshold=0.2,
    )

    report = deep_research.run_deep_research_sync(
        "심화가 강제되는 조사 질의", None, session_id="e2e-cap", config=config
    )

    # 비차단 종료 + 유한 종료(P13): 정확히 cap 에서 멈춘다.
    assert isinstance(report, ResearchReport)
    assert report.deepening_count == cap
    assert report.deepening_count <= config.max_deepening

    # Evaluator 는 cap+1회 평가된다(True × cap → 진입, 마지막 False → 종료).
    assert eval_count["n"] == cap + 1
    # Planner 는 초기 1회 + 심화 cap회 재계획 = cap+1회 호출된다.
    assert len(plan_calls) == cap + 1

    # 심화해도 동일 mock 소스라 선행 근거로 중복 배제 → 확보 근거는 유지(비차단).
    assert report.metrics.coverage_sources == _EXPECTED_SOURCES_PER_SUBQUERY
    _assert_citations_tied_to_evidence(report)


# =========================================================================== #
# Test 3 — 경량 가짜 Gateway: 다중 하위질의 분해 + LLM 종합 경로 증명
# =========================================================================== #
# 프롬프트에 실린 근거 목록에서 source_id 를 뽑아 그대로 인용하는 가짜 LLM. 이렇게
# 하면 LLM 종합 경로에서도 인용이 실제 근거에 묶인다(참조 무결성). 시스템/휴먼 프롬프트의
# 예시 토큰([web:https://example.com/a])은 "source_id:" 접두가 없어 매칭되지 않는다.
_PROMPT_SID_RE = re.compile(r"source_id:\s*((?:web|doi):\S+)")
_FAKE_LLM_MARKER = "종합 리포트 (가짜 LLM)"


class _FakeAIMessage:
    """GatewayChatModel.ainvoke 산출물을 흉내 내는 최소 메시지(tool_calls/content)."""

    def __init__(self, *, content="", tool_calls=None):
        self.content = content
        self.tool_calls = tool_calls or []


class _FakeGatewayChatModel:
    """GatewayChatModel 을 대체하는 가짜(네트워크 0).

    - bind_tools(tool_choice="plan_subqueries") 후 ainvoke → 다중 하위질의 tool_call.
    - bind_tools 없이 ainvoke(Generator) → 프롬프트의 근거 source_id 를 인용한 리포트.
    """

    def __init__(self, *args, gateway=None, model_id="", prefer_streaming=False, **kwargs):
        self._tool_choice = None

    def bind_tools(self, tools, tool_choice=None, **kwargs):
        self._tool_choice = tool_choice
        return self

    async def ainvoke(self, messages, *args, **kwargs):
        if self._tool_choice == "plan_subqueries":
            # 다중 하위질의로 분해(요구사항 5.1) — q3 는 q1 에 의존(웨이브 분할 유도).
            return _FakeAIMessage(
                tool_calls=[
                    {
                        "name": "plan_subqueries",
                        "args": {
                            "subqueries": [
                                {"id": "q1", "subtask": "하위 조사 A", "depends_on": []},
                                {"id": "q2", "subtask": "하위 조사 B", "depends_on": []},
                                {"id": "q3", "subtask": "하위 조사 C", "depends_on": ["q1"]},
                            ]
                        },
                    }
                ]
            )
        # Generator 경로: 프롬프트에서 근거 source_id 를 추출해 그대로 인용.
        text = "\n".join(getattr(m, "content", "") or "" for m in messages)
        seen: set = set()
        sids: list = []
        for m in _PROMPT_SID_RE.finditer(text):
            sid = m.group(1)
            if sid not in seen:
                seen.add(sid)
                sids.append(sid)
        lines = [f"# {_FAKE_LLM_MARKER}"]
        for i, sid in enumerate(sids, 1):
            lines.append(f"핵심 주장 {i}은 수집된 근거에 기반한다 [{sid}].")
        if not sids:
            lines.append("근거를 확인하지 못했습니다.")
        return _FakeAIMessage(content="\n".join(lines))


class _FakeDeps:
    """GraphDeps 유사 주입 객체 — gateway(sentinel)/역할 모델/체크포인트 슬롯."""

    def __init__(self):
        self.gateway = object()  # 비-None → LLM 경로 진입(가짜 GatewayChatModel 사용)
        self.model_planner = "fake-planner-model"
        self.model_generator = "fake-generator-model"
        self.checkpointer = None
        self.store = None


def test_deep_research_e2e_fake_gateway_multi_subquery(monkeypatch, tmp_path):
    """가짜 Gateway 주입: Planner 다중 분해 + Generator LLM 종합 경로를 증명한다.

    GatewayChatModel 을 가짜로 대체하고 deps.gateway 를 주입하면 Planner 는 3개
    하위질의로 분해하고, Generator 는 (폴백이 아니라) LLM 종합 리포트를 생성한다.
    LLM 이 인용한 source_id 는 프롬프트의 근거 목록에서 뽑은 것이라 실제 근거에 묶인다.
    """
    _hermetic_env(monkeypatch, tmp_path)
    _install_backend_mocks(monkeypatch)
    plan_calls, eval_count = _install_spies(monkeypatch)

    # GatewayChatModel 을 가짜로 대체(지연 import 지점이 이 속성을 읽는다).
    import ai_engine.agent_system.chat_model_adapter as cma

    monkeypatch.setattr(cma, "GatewayChatModel", _FakeGatewayChatModel)

    config = DeepResearchConfig(
        top_k=10,
        max_subqueries=8,
        fetch_per_subquery=5,
        max_deepening=1,
        min_sources=2,     # 커버리지 충족 → 심화 없이 단일 종합 확인
        min_providers=2,
        unverified_threshold=0.2,
    )

    report = deep_research.run_deep_research_sync(
        "다면적 심층 조사가 필요한 복잡한 질의", _FakeDeps(), session_id="e2e-fake-gw", config=config
    )

    assert isinstance(report, ResearchReport)

    # Planner 다중 분해(요구사항 5.1): 어느 Planner 호출이든 ≥2개 하위질의를 반환.
    assert plan_calls, "Planner 미경유"
    assert any(len(c) >= 2 for c in plan_calls), (
        f"다중 하위질의 분해 미확인: {[len(c) for c in plan_calls]}"
    )

    # LLM 종합 경로 사용(결정적 폴백이 아님) — 가짜 LLM 마커가 리포트에 존재.
    assert _FAKE_LLM_MARKER in report.report_markdown

    # 근거 수집 + 인용이 근거에 묶임(LLM 이 근거 source_id 를 인용).
    assert report.metrics.coverage_sources > 0
    assert report.metrics.coverage_providers == _EXPECTED_PROVIDERS
    _assert_citations_tied_to_evidence(report)

    # Evaluator ≥1회 경유 + 비차단·유한 종료(P13).
    assert eval_count["n"] >= 1
    assert report.deepening_count <= config.max_deepening


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-q"]))
