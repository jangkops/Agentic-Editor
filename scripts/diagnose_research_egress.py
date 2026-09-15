#!/usr/bin/env python3
"""외부 리서치 egress 실측 진단 — "설정을 다 켰는데 검색이 안 된다"의 원인을 특정한다.

UI 토글(옵트인·동의)을 강제로 on 으로 세팅한 뒤, 제공자별로 실제 egress 경로를
호출해 다음 세 가지를 구분한다:

  1. gate_off          — 옵트인/동의 게이트에서 막힘 (네트워크 미호출)
  2. missing_credential— 키가 없어 네트워크 호출 전에 반환 (구조적 불가)
  3. network           — 실제 HTTP 호출까지 도달 (성공/타임아웃/HTTP 오류)

자격증명 값은 출력하지 않는다. 키 존재 여부만 SET/unset 으로 표기한다.

사용:
    ai_engine/.venv/bin/python scripts/diagnose_research_egress.py
    ai_engine/.venv/bin/python scripts/diagnose_research_egress.py --no-network
"""
import argparse
import asyncio
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

QUERY = "transformer attention mechanism"

WEB = ("tavily", "exa", "brave")
ACADEMIC = ("semantic_scholar", "openalex", "arxiv", "pubmed", "europepmc")


def _bar(title: str) -> None:
    print(f"\n{'=' * 72}\n{title}\n{'=' * 72}")


def _classify(raw: dict) -> str:
    """원시 반환 dict를 진단 카테고리로 환원한다."""
    if not isinstance(raw, dict):
        return f"?? non-dict: {type(raw).__name__}"
    err = raw.get("error")
    if not err:
        return "OK (네트워크 도달, 응답 파싱 가능)"
    return {
        "web_research_disabled": "GATE OFF (옵트인/동의에서 차단 — 네트워크 미호출)",
        "missing_credential": "KEY 없음 (네트워크 미호출 — 구조적 불가)",
        "timeout": "네트워크 도달 → TIMEOUT",
        "http_error": f"네트워크 도달 → HTTP {raw.get('status_code')}",
        "request_error": f"네트워크 오류: {raw.get('detail')}",
        "unknown_provider": "제공자 이름 불일치",
        "invalid_query": "질의 검증 실패",
    }.get(err, f"기타: {err} / {raw.get('detail')}")


async def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--no-network", action="store_true",
                    help="게이트/키 판정만 확인하고 실제 HTTP는 피한다")
    args = ap.parse_args()

    from ai_engine.research import backend, security
    from ai_engine.research.config import DeepResearchConfig

    # ── 0) 실제 런타임과 동일한 설정 동기화 ──────────────────────────────
    # graph-stream 진입부가 하는 것과 같다. UI 설정이 게이트에 도달하는지 확인.
    _bar("0) UI 설정 → 게이트 env 동기화 (graph-stream 진입부와 동일)")
    from ai_engine.server import (
        _sync_research_env_from_settings,
        _userdata_settings_path_candidates,
    )
    for i, c in enumerate(_userdata_settings_path_candidates(), 1):
        print(f"  {i}. {'있음' if os.path.isfile(c) else '없음':4s}  {c}")
    print()
    applied = _sync_research_env_from_settings()
    print(f"  → 적용됨: {applied}   web_research_enabled()={backend.web_research_enabled()}")
    if not applied:
        print("  ⚠️ UI 설정이 게이트에 도달하지 않았습니다 — 위 후보 경로를 확인하세요.")

    # ── 1) 현재 환경 상태 ────────────────────────────────────────────────
    _bar("1) 게이트 환경변수 (동기화 후 실제 값)")
    for k in ("AE_ENABLE_WEB_RESEARCH", "AE_RESEARCH_CONSENT",
              "AE_RESEARCH_WEB_PROVIDERS", "AE_RESEARCH_ACADEMIC_PROVIDERS",
              "AE_GENERATED_ROOT", "AE_SETTINGS_PATH"):
        print(f"  {k:34s} = {os.environ.get(k, '(unset)')}")
    print(f"  web_research_enabled()             = {backend.web_research_enabled()}")

    _bar("2) 제공자 자격증명 존재 여부 (값 미출력)")
    for name in WEB + ACADEMIC:
        key = security.load_credential(name)
        required = name in backend._REQUIRES_KEY
        env_var = security._env_var_for(name) if hasattr(security, "_env_var_for") else "?"
        flag = "필수" if required else "선택/불요"
        print(f"  {name:18s} {flag:8s} env={env_var:28s} {'SET' if key else 'unset'}")

    # ── 3) 게이트를 강제로 on (UI에서 다 켠 상태를 재현) ──────────────────
    os.environ["AE_ENABLE_WEB_RESEARCH"] = "1"
    os.environ["AE_RESEARCH_CONSENT"] = "1"
    _bar("3) 게이트 강제 ON — UI에서 전부 켠 상태 재현")
    print(f"  web_research_enabled() = {backend.web_research_enabled()}")
    cfg = DeepResearchConfig.from_env()
    print(f"  cfg.web_providers      = {list(cfg.web_providers)}")
    print(f"  cfg.academic_providers = {list(cfg.academic_providers)}")
    print(f"  cfg.search_timeout     = {cfg.search_timeout}s")

    if args.no_network:
        print("\n--no-network: 실제 호출 생략")
        return 0

    # ── 4) 웹 제공자 실측 ────────────────────────────────────────────────
    _bar(f"4) 웹 검색 실측 — query={QUERY!r}")
    web_ok = 0
    for name in WEB:
        raw = await backend.web_search_raw(
            name, QUERY, top_k=3, timeout=cfg.search_timeout
        )
        verdict = _classify(raw)
        if raw.get("error") is None:
            web_ok += 1
        print(f"  {name:18s} → {verdict}")

    # ── 5) 학술 제공자 실측 ──────────────────────────────────────────────
    _bar(f"5) 논문 검색 실측 — query={QUERY!r}")
    acad_ok = 0
    for name in ACADEMIC:
        raw = await backend.academic_search_raw(
            name, QUERY, top_k=3, timeout=cfg.search_timeout
        )
        verdict = _classify(raw)
        if raw.get("error") is None:
            acad_ok += 1
        print(f"  {name:18s} → {verdict}")

    # ── 6) 도구 레벨 실측 (LLM이 실제로 호출하는 진입점) ─────────────────
    _bar("6) 도구 진입점 실측 — server._execute_tool가 부르는 함수")
    from ai_engine.agent_system.subgraphs.research import (
        run_web_search, run_search_papers,
    )
    w = run_web_search({"query": QUERY, "top_k": 3})
    print(f"  run_web_search    → count={w.get('count')} "
          f"disabled={w.get('disabled')} error={w.get('error')} "
          f"providers={w.get('providers')}")
    p = run_search_papers({"query": QUERY, "top_k": 3})
    print(f"  run_search_papers → count={p.get('count')} "
          f"disabled={p.get('disabled')} error={p.get('error')} "
          f"providers={p.get('providers')}")

    # ── 7) 판정 ──────────────────────────────────────────────────────────
    _bar("7) 판정")
    print(f"  웹 제공자 응답 성공   : {web_ok}/{len(WEB)}")
    print(f"  논문 제공자 응답 성공 : {acad_ok}/{len(ACADEMIC)}")
    print(f"  도구 결과 건수        : web={w.get('count')} papers={p.get('count')}")
    if web_ok == 0 and acad_ok > 0:
        print("\n  → 웹만 불가. 원인은 게이트가 아니라 웹 제공자 3종의 API 키 부재.")
    elif web_ok == 0 and acad_ok == 0:
        print("\n  → 웹·논문 모두 불가. 게이트/네트워크/구현 중 상위 출력에서 원인 확인.")
    else:
        print("\n  → 일부 이상 동작. 상위 출력의 제공자별 판정 참조.")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
