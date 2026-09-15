"""실행 계약 검사 — "선언한 것"과 "실제 일어난 것"을 대조해 불일치를 표면화한다.

── 왜 필요한가 (실측 사고 7건의 공통 형태) ──────────────────────────────────
이 리포에서 반복적으로 발생한 결함은 모두 **"기능이 켜져 있는데 조용히 아무것도
하지 않는다"** 였다:

1. 합의 모델을 골라도 라벨만 바뀌고 실제 호출은 우선순위 1순위 모델
2. 리서치 옵트인·동의를 다 켜도 settings.json 경로를 못 찾아 게이트가 off 유지
3. 웹 검색 제공자 키가 없어 0건인데 도구 결과가 ``error=None``
4. 외부 조사 요청이 도구 없는 워커로 라우팅돼 "조사하겠습니다"만 남기고 종료

공통 원인은 코드베이스 전반의 **비차단 폴백(non-blocking fallback)** 철학이다. 가용성은
지키지만 실패 신호를 전부 삼킨다. 개별 버그를 고치는 것으로는 같은 형태가 계속 재발한다.

그래서 요청이 끝날 때 **선언(설정·플래그·의도)** 과 **관측(도구 호출·제공자·결과 수)** 을
대조해, 불일치가 있으면 사용자에게 올린다. 이 모듈은 그 판정을 담당한다.

── 설계 원칙 ────────────────────────────────────────────────────────────────
- **순수**: I/O·전역상태·LLM 호출 없음. 입력 dict만 보고 판정한다(테스트 용이).
- **예외 없음**: 어떤 입력에도 예외를 전파하지 않는다. 판정 실패는 빈 목록이다
  (감시 장치가 본체를 막으면 안 된다 — 기존 비차단 철학과 정합).
- **무노이즈**: 항상 뜨는 경고는 무시된다. 불일치는 **의도가 있을 때만** 판정한다.
  예를 들어 "리서치가 켜져 있는데 검색 0회"는 코딩 질문에서 정상이므로 경고하지 않고,
  **조사 의도가 있을 때만** 경고한다.
- **자격증명 미포함(P9)**: 제공자 "이름"과 개수만 다룬다. 키·토큰은 입력으로도 받지
  않으며, 출력 어디에도 담기지 않는다.
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

# ── 불일치 코드 → 사용자에게 보여줄 한국어 사유 ──────────────────────────────
# 문구는 "무엇이 안 됐는지 + 다음에 무엇을 할지"만 담는다(원인 추측 금지).
MISMATCH_MESSAGES: Dict[str, str] = {
    "research_intent_no_search": (
        "외부 조사 요청으로 보이는데 이번 응답에서 검색 도구가 한 번도 호출되지 않았습니다. "
        "답변이 학습 지식만으로 작성됐을 수 있습니다."
    ),
    "research_intent_gate_off": (
        "외부 조사 요청으로 보이지만 외부 리서치가 꺼져 있어 검색을 수행하지 않았습니다. "
        "설정 → 리서치에서 사용과 동의를 켜야 외부 검색이 동작합니다."
    ),
    "search_ran_but_no_results": (
        "검색을 실행했지만 결과가 0건입니다. 아래 제공자별 사유를 확인하세요."
    ),
    "search_provider_failed": (
        "일부 검색 제공자가 실패했습니다."
    ),
    "declared_providers_never_called": (
        "설정에서 선택한 검색 제공자가 이번 실행에서 호출되지 않았습니다."
    ),
}

# 제공자 오류 코드 → 조치 가능한 한국어 사유. research/backend.py 의 코드와 정합.
PROVIDER_ERROR_HINTS: Dict[str, str] = {
    "missing_credential": "API 키 미설정",
    "timeout": "응답 시간 초과",
    "http_error": "제공자 오류 응답(레이트리밋·권한 등)",
    "request_error": "연결 실패(네트워크·DNS)",
    "web_research_disabled": "외부 리서치 비활성",
    "invalid_query": "질의가 비었거나 너무 김",
    "no_external_evidence": "모든 제공자 실패",
    "unknown_provider": "지원하지 않는 제공자",
}

# 검색 계열 도구 이름. nodes/tool_node._SEARCH_TOOLS 와 정합해야 한다.
SEARCH_TOOL_NAMES = frozenset(
    {"web_search", "search_papers", "fetch_content", "deep_research"}
)


def _as_int(value: Any, default: int = 0) -> int:
    """정수로 안전 변환(실패 시 default). 순수·예외 없음."""
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _as_bool(value: Any) -> bool:
    """bool 로 안전 변환. 순수·예외 없음."""
    return bool(value)


def _as_name_list(value: Any) -> List[str]:
    """문자열 리스트만 남긴다(순수). dict/None/비문자열 원소는 버린다."""
    if not isinstance(value, (list, tuple)):
        return []
    return [v.strip() for v in value if isinstance(v, str) and v.strip()]


class EffectCollector:
    """그래프 실행 중 관측된 효과를 누적한다(SSE 이벤트 스트림에서 호출).

    상태를 갖지만 로직은 단순 누적뿐이며, 어떤 콜백도 예외를 던지지 않는다. sse_bridge
    가 이벤트를 중계하면서 같은 이벤트를 이 수집기에도 흘려보낸다(중계 계약 무변경).

    수집 대상:
        - 도구 호출 이름·횟수 (``on_tool_start``)
        - 검색 실행의 제공자·결과 수·오류 코드 (``on_search_status``)
        - 방문한 도메인 서브그래프 (``on_agent_start``)
        - 디스크 실측된 산출물 수 (``on_verified_files``)

    자격증명은 수집하지 않는다(P9) — 제공자 이름과 개수만 다룬다.
    """

    def __init__(self) -> None:
        self.tool_calls: Dict[str, int] = {}
        self.domains: List[str] = []
        self.search_providers: List[str] = []      # 실행에 동원된 제공자 이름(중복 제거)
        self.search_results_total: int = 0         # 파싱된 결과 건수 합
        self.search_result_known: bool = False     # count 를 한 번이라도 관측했는가
        self.provider_errors: List[Dict[str, str]] = []   # [{"provider","error"}]
        self.search_runs: int = 0                  # end 이벤트 수(검색 실행 횟수)
        self.verified_files: int = 0

    # ── 이벤트 콜백 (모두 비차단) ───────────────────────────────────────────
    def on_tool_start(self, name: Any) -> None:
        try:
            key = name.strip() if isinstance(name, str) else ""
            if key:
                self.tool_calls[key] = self.tool_calls.get(key, 0) + 1
        except Exception:  # noqa: BLE001 — 관측 실패는 본체를 막지 않는다
            pass

    def on_agent_start(self, domain: Any) -> None:
        try:
            key = domain.strip() if isinstance(domain, str) else ""
            if key and key not in self.domains:
                self.domains.append(key)
        except Exception:  # noqa: BLE001
            pass

    def on_verified_files(self, paths: Any) -> None:
        try:
            if isinstance(paths, (list, tuple)):
                self.verified_files += len(paths)
        except Exception:  # noqa: BLE001
            pass

    def on_search_status(self, payload: Any) -> None:
        """``search_status`` 커스텀 이벤트를 누적한다.

        end 페이즈의 ``count``/``error`` 는 tool_node 가 도구 결과에서 추출해 싣는다
        (없으면 관측 불가로 취급 — 과거 payload 형태와의 호환).
        """
        try:
            if not isinstance(payload, dict):
                return
            for p in _as_name_list(payload.get("providers")):
                if p not in self.search_providers:
                    self.search_providers.append(p)
            if payload.get("phase") != "end":
                return
            self.search_runs += 1
            if "count" in payload:
                self.search_result_known = True
                self.search_results_total += _as_int(payload.get("count"))
            per_provider = [
                item
                for item in (payload.get("provider_errors") or [])
                if isinstance(item, dict) and isinstance(item.get("error"), str)
                and item.get("error")
            ]
            for item in per_provider:
                prov = item.get("provider")
                entry = {
                    "provider": prov if isinstance(prov, str) else "",
                    "error": item["error"],
                }
                if entry not in self.provider_errors:
                    self.provider_errors.append(entry)
            # 최상위 ``error`` 는 제공자별 사유의 **집계값**이다(도구가 전 제공자 실패 시
            # 대표 코드를 싣는다). provider_errors 가 이미 있으면 같은 사실이 두 번
            # 보고되어 사유 문구가 중복되므로, 제공자별 정보가 없을 때만 흡수한다.
            if not per_provider:
                code = payload.get("error")
                if isinstance(code, str) and code:
                    entry = {"provider": "", "error": code}
                    if entry not in self.provider_errors:
                        self.provider_errors.append(entry)
        except Exception:  # noqa: BLE001
            pass

    # ── 산출 ────────────────────────────────────────────────────────────────
    def search_tool_calls(self) -> int:
        """검색 계열 도구 호출 총 횟수."""
        return sum(n for name, n in self.tool_calls.items() if name in SEARCH_TOOL_NAMES)

    def observed(self) -> dict:
        """관측 결과를 JSON 호환 dict 로 환원(자격증명 미포함)."""
        return {
            "toolCalls": dict(self.tool_calls),
            "searchToolCalls": self.search_tool_calls(),
            "searchRuns": self.search_runs,
            "searchProviders": list(self.search_providers),
            "searchResults": self.search_results_total if self.search_result_known else None,
            "providerErrors": [dict(e) for e in self.provider_errors],
            "domains": list(self.domains),
            "verifiedFiles": self.verified_files,
        }


def detect_mismatches(declared: Any, observed: Any) -> List[dict]:
    """선언 vs 관측 불일치 목록을 판정한다(순수·예외 없음).

    Args:
        declared: ``{"researchEnabled","researchConsent","webProviders",
                     "academicProviders","researchIntent"}``
        observed: ``EffectCollector.observed()`` 산출물.

    Returns:
        ``[{"code","message","detail"}]``. 불일치가 없으면 빈 목록.

    무노이즈 규칙: 조사 의도(``researchIntent``)가 없으면 리서치 관련 불일치를 판정하지
    않는다. 코딩 질문에서 "검색 0회"는 정상이므로 경고하면 신호가 죽는다.
    """
    out: List[dict] = []
    try:
        if not isinstance(declared, dict) or not isinstance(observed, dict):
            return out

        intent = _as_bool(declared.get("researchIntent"))
        gate_open = _as_bool(declared.get("researchEnabled")) and _as_bool(
            declared.get("researchConsent")
        )
        search_calls = _as_int(observed.get("searchToolCalls"))
        results = observed.get("searchResults")
        errors = observed.get("providerErrors") or []

        def _add(code: str, detail: str = "") -> None:
            out.append(
                {
                    "code": code,
                    "message": MISMATCH_MESSAGES.get(code, code),
                    "detail": detail,
                }
            )

        # 1) 조사 의도가 있는데 게이트가 닫혀 검색을 아예 못 했다.
        if intent and not gate_open:
            _add("research_intent_gate_off")
            return out   # 게이트가 닫혔으면 이후 판정은 중복 노이즈다

        # 2) 조사 의도 + 게이트 열림인데 검색 도구 호출이 0회.
        if intent and gate_open and search_calls == 0:
            _add(
                "research_intent_no_search",
                f"방문 도메인={', '.join(_as_name_list(observed.get('domains'))) or '없음'}",
            )

        # 3) 검색은 돌았는데 결과가 0건(관측된 경우에만 — count 미제공이면 판정 보류).
        if search_calls > 0 and isinstance(results, int) and results == 0:
            _add("search_ran_but_no_results")

        # 4) 제공자 실패 사유를 조치 가능한 문구로 요약.
        if errors:
            parts = []
            for e in errors:
                if not isinstance(e, dict):
                    continue
                code = e.get("error") or ""
                hint = PROVIDER_ERROR_HINTS.get(code, code)
                prov = e.get("provider") or ""
                parts.append(f"{prov}: {hint}" if prov else hint)
            if parts:
                _add("search_provider_failed", "; ".join(parts))

        # 5) 설정에서 고른 제공자가 하나도 동원되지 않았다(의도 있을 때만).
        if intent and gate_open and search_calls > 0:
            declared_provs = set(_as_name_list(declared.get("webProviders"))) | set(
                _as_name_list(declared.get("academicProviders"))
            )
            used = set(_as_name_list(observed.get("searchProviders")))
            if declared_provs and not (declared_provs & used):
                _add(
                    "declared_providers_never_called",
                    f"선택={', '.join(sorted(declared_provs))} / 실행={', '.join(sorted(used)) or '없음'}",
                )
    except Exception:  # noqa: BLE001 — 판정 실패는 비차단(감시가 본체를 막지 않는다)
        return out
    return out


def build_effect_summary(declared: Any, observed: Any) -> dict:
    """SSE 로 내려보낼 ``effectSummary`` 페이로드를 만든다(순수·예외 없음).

    Returns:
        ``{"declared": {...}, "observed": {...}, "mismatches": [...], "ok": bool}``
        ``ok`` 는 불일치가 없음을 뜻한다(프론트가 경고 표시 여부를 이 값으로 결정).
    """
    try:
        decl = declared if isinstance(declared, dict) else {}
        obs = observed if isinstance(observed, dict) else {}
        mismatches = detect_mismatches(decl, obs)
        return {
            "declared": decl,
            "observed": obs,
            "mismatches": mismatches,
            "ok": not mismatches,
        }
    except Exception:  # noqa: BLE001
        return {"declared": {}, "observed": {}, "mismatches": [], "ok": True}


def should_emit_summary(summary: Any) -> bool:
    """``effectSummary`` 를 실제로 SSE 로 내려보낼지 판정한다(순수·예외 없음).

    무노이즈 원칙을 **프로토콜 수준까지** 적용한다. 처음 구현은 모든 스트림에
    무조건 방출했는데, 그러면

      1) 순수 코딩 질문·빈 스트림에도 페이로드가 실린다 — 사용자에게 보여줄 것이 없는데
         바이트만 늘어난다(프론트는 불일치가 없으면 라이브 로그만 찍고 끝낸다).
      2) SSE 계약 테스트 6건이 깨진다. 그 테스트들은 페이로드 목록을 **정확 동일**로
         단정해 "약속하지 않은 이벤트는 흘리지 않는다" 를 지키고 있었다. 방출을 좁히는
         쪽이 맞다 — 계약을 느슨하게 고치면 다른 유출도 같이 통과하게 된다.

    방출 조건:
      * 불일치가 하나라도 있으면 방출한다 — 이 장치의 존재 이유다.
      * 불일치가 없어도 **외부 조회 활동이 관측되면** 방출한다(검색 호출·제공자·결과 수·
        제공자 오류). "검색이 실제로 돌았는지" 는 이 리포에서 반복적으로 문제였고,
        성공 사례의 근거를 남기는 값이 바이트 비용보다 크다.
      * 그 외(활동 없음 + 불일치 없음)에는 침묵한다.
    """
    try:
        if not isinstance(summary, dict):
            return False
        if summary.get("mismatches"):
            return True
        obs = summary.get("observed")
        if not isinstance(obs, dict):
            return False
        if _as_int(obs.get("searchToolCalls")) > 0 or _as_int(obs.get("searchRuns")) > 0:
            return True
        for key in ("searchProviders", "providerErrors"):
            val = obs.get(key)
            if isinstance(val, (list, tuple, set)) and len(val) > 0:
                return True
        if obs.get("searchResults") is not None:
            return True
        return False
    except Exception:  # noqa: BLE001
        return False


def declared_from_env(prompt: Any, env: Optional[dict] = None) -> dict:
    """현재 게이트 설정 + 프롬프트의 조사 의도를 "선언" dict 로 환원(예외 없음).

    게이트 값은 ``research.config.DeepResearchConfig`` 를 재사용해 읽는다(재구현 금지 —
    게이트 판정과 동일 소스). 조사 의도는 ``supervisor._looks_like_research`` 를 재사용한다.
    어느 쪽이든 import·판정이 실패하면 보수적으로 off/False 로 둔다(오탐 방지).

    자격증명은 읽지 않는다(P9) — 플래그와 제공자 이름만.
    """
    decl = {
        "researchEnabled": False,
        "researchConsent": False,
        "webProviders": [],
        "academicProviders": [],
        "researchIntent": False,
    }
    try:
        from ai_engine.research.config import DeepResearchConfig

        cfg = DeepResearchConfig.from_env(env)
        decl["researchEnabled"] = _as_bool(cfg.enable_web_research)
        decl["researchConsent"] = _as_bool(cfg.consent)
        decl["webProviders"] = _as_name_list(list(cfg.web_providers))
        decl["academicProviders"] = _as_name_list(list(cfg.academic_providers))
    except Exception:  # noqa: BLE001 — 설정 로드 실패 → 보수적으로 off
        pass
    try:
        from ai_engine.agent_system.supervisor import _looks_like_research

        decl["researchIntent"] = _as_bool(_looks_like_research(prompt))
    except Exception:  # noqa: BLE001 — 의도 판정 실패 → False(경고 억제)
        pass
    return decl
