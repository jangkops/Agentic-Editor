"""deep-research-engine: 품질 회귀 게이트 실행기 (오프라인, 네트워크 불필요).

``scripts/golden_research.json`` 스냅샷(질의 + 검색 결과 + relevant_ids + 선택적
최신성/인용 정보)을 로드해 ``ai_engine.research.eval_harness.evaluate_golden_set``
으로 품질 지표를 산출하고, 스냅샷에 동봉된 **baseline 집계**와 회귀 게이트
(``check_regression``)로 비교한다. 어떤 지표라도 baseline 대비 허용 하락폭
(``AE_RESEARCH_REGRESSION_TOLERANCE``, 기본 0.05 == 5%)을 초과해 낮아지면 회귀로
판정하고 **비영(非0) 종료 코드**로 종료한다(CI 품질 게이트).

모든 검색 결과가 스냅샷에 인라인되어 있어 외부 검색 API·게이트웨이·네트워크를
호출하지 않는다(재현 가능·결정적). 최신성 창은 절대 날짜라 벽시계에 무관하며,
``now`` 필드는 상대 일수 해석을 고정해 결정성을 보장한다.

실행:
    ai_engine/.venv/bin/python scripts/eval_research_quality.py
    ai_engine/.venv/bin/python scripts/eval_research_quality.py --update-baseline
    ai_engine/.venv/bin/python scripts/eval_research_quality.py --tolerance 0.1

종료 코드:
    0  회귀 없음(PASS) 또는 --update-baseline 성공
    1  회귀 감지(FAIL)
    2  사용법/로드 오류(스냅샷 없음, baseline 없음 등)

Requirements: 9.7
Design: "품질 게이트(eval_metrics 재사용) + baseline 회귀", eval_harness.py 행
"""
import argparse
import json
import os
import sys
from datetime import datetime, timezone

# 응답 경로와 분리된 오프라인 도구이지만, 지표·게이트는 프로덕션과 동일한 순수
# 함수를 재사용한다(재구현 금지 — 요구사항 9.1/9.7). `import ai_engine.research...`
# 가 PYTHONPATH 설정 없이도 동작하도록 저장소 루트와 엔진 루트를 sys.path 에 둔다
# (부작용·자격증명 없는 경로 삽입).
_REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), os.pardir))
_ENGINE_ROOT = os.path.join(_REPO_ROOT, "ai_engine")
for _p in (_REPO_ROOT, _ENGINE_ROOT):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from ai_engine.research.eval_harness import (  # noqa: E402  (경로 주입 후 import)
    DEFAULT_K,
    HIGHER_IS_BETTER_METRICS,
    check_regression,
    evaluate_golden_set,
)

DEFAULT_GOLDEN_PATH = os.path.join("scripts", "golden_research.json")
# now 필드가 없거나 파싱 불가할 때의 고정 기준 시각(벽시계 미사용 → 결정성 유지).
_FALLBACK_NOW = datetime(2025, 6, 1, tzinfo=timezone.utc)


def _parse_now(value) -> datetime:
    """스냅샷 ``now`` 문자열을 aware(UTC) datetime 으로 파싱한다(실패 시 고정 폴백).

    ``evaluate_golden_set`` 은 datetime 객체일 때만 ``now`` 를 사용하므로(문자열은
    무시됨), 상대 일수 최신성 창의 결정성을 위해 반드시 datetime 으로 변환한다.
    """
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=timezone.utc)
    if isinstance(value, str) and value.strip():
        s = value.strip()
        if s[-1] in ("Z", "z"):
            s = s[:-1] + "+00:00"
        try:
            dt = datetime.fromisoformat(s)
            return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)
        except ValueError:
            return _FALLBACK_NOW
    return _FALLBACK_NOW


def _load_snapshot(path: str) -> dict:
    """golden 스냅샷 JSON 을 로드·검증한다.

    Raises:
        ValueError: 최상위가 객체가 아니거나 ``queries`` 가 리스트가 아닐 때.
    """
    with open(path, "r", encoding="utf-8") as f:
        data = json.load(f)
    if not isinstance(data, dict):
        raise ValueError("golden 스냅샷 최상위는 객체(dict)여야 합니다.")
    if not isinstance(data.get("queries"), list):
        raise ValueError("golden 스냅샷 'queries' 는 리스트여야 합니다.")
    return data


def _baseline_aggregate(snapshot: dict) -> dict | None:
    """스냅샷에서 baseline 집계 dict 를 추출한다(없으면 None).

    ``baseline.aggregate`` 형태(권장) 또는 ``baseline`` 자체가 집계 dict 인 형태를
    모두 허용한다.
    """
    base = snapshot.get("baseline")
    if not isinstance(base, dict):
        return None
    agg = base.get("aggregate")
    if isinstance(agg, dict):
        return agg
    # baseline 자체가 집계 지표 dict 인 경우(선택 스키마).
    if any(k in base for k in HIGHER_IS_BETTER_METRICS):
        return base
    return None


def _fmt(v) -> str:
    """지표 값을 폭 고정 문자열로 포맷한다."""
    try:
        return f"{float(v):+.4f}"
    except (TypeError, ValueError):
        return str(v)


def _print_report(snapshot_path: str, k: int, now: datetime, result: dict,
                  gate: dict) -> None:
    """사람이 읽기 쉬운 회귀 게이트 리포트를 stdout 에 출력한다."""
    agg = result["aggregate"]
    tol = gate["tolerance"]
    details = gate["details"]

    print("=" * 78)
    print("Deep Research 품질 회귀 게이트")
    print(f"  snapshot : {snapshot_path}")
    print(f"  queries  : {result['n_queries']}    k={k}    "
          f"now={now.strftime('%Y-%m-%dT%H:%M:%SZ')}")
    print(f"  tolerance: {tol:.4f} ({tol * 100:.1f}% 허용 하락폭, "
          f"AE_RESEARCH_REGRESSION_TOLERANCE)")
    print("-" * 78)
    header = f"  {'metric':<20}{'baseline':>12}{'current':>12}{'drop':>11}{'rel_drop':>10}  status"
    print(header)
    print("-" * 78)
    for key in HIGHER_IS_BETTER_METRICS:
        d = details.get(key, {})
        base_v = d.get("baseline", 0.0)
        cur_v = d.get("current", agg.get(key, 0.0))
        drop = d.get("drop", 0.0)
        rel = d.get("relative_drop", 0.0)
        status = "REGRESSED" if d.get("regressed") else "ok"
        print(f"  {key:<20}{base_v:>12.4f}{cur_v:>12.4f}"
              f"{drop:>+11.4f}{rel * 100:>+9.1f}%  {status}")
    print("-" * 78)
    if gate["regressed"]:
        print(f"  RESULT: FAIL — 회귀 감지: {', '.join(gate['regressed_metrics'])}")
    else:
        print("  RESULT: PASS — baseline 대비 회귀 없음")
    print("=" * 78)


def _run_gate(args) -> int:
    """게이트 실행: 스냅샷 평가 → baseline 비교 → 리포트 → 종료 코드."""
    snapshot = _load_snapshot(args.golden)
    k = int(snapshot.get("k", DEFAULT_K))
    now = _parse_now(snapshot.get("now"))
    queries = snapshot["queries"]

    result = evaluate_golden_set(queries, k=k, now=now)

    baseline_agg = _baseline_aggregate(snapshot)
    if baseline_agg is None:
        print(f"[gate] baseline 없음 — 먼저 다음을 실행하세요:\n"
              f"       {sys.executable} {sys.argv[0]} --update-baseline",
              file=sys.stderr)
        return 2

    gate = check_regression(result["aggregate"], baseline_agg,
                            tolerance=args.tolerance)
    _print_report(args.golden, k, now, result, gate)
    return 1 if gate["regressed"] else 0


def _update_baseline(args) -> int:
    """현재 golden 스냅샷을 평가해 baseline 집계를 (재)기록한다.

    의도적으로 baseline 을 갱신할 때만 사용한다(예: golden 세트 변경 후). 실행기가
    이후 자신의 baseline 을 정확히 통과하도록 현재 집계를 그대로 동결한다.
    """
    snapshot = _load_snapshot(args.golden)
    k = int(snapshot.get("k", DEFAULT_K))
    now = _parse_now(snapshot.get("now"))
    result = evaluate_golden_set(snapshot["queries"], k=k, now=now)

    snapshot["baseline"] = {
        "generated_by": "scripts/eval_research_quality.py --update-baseline",
        "n_queries": result["n_queries"],
        "k": k,
        "aggregate": result["aggregate"],
    }
    with open(args.golden, "w", encoding="utf-8") as f:
        json.dump(snapshot, f, ensure_ascii=False, indent=2)
        f.write("\n")
    print(f"[baseline] 기록 완료: {args.golden} "
          f"(queries={result['n_queries']}, k={k})")
    print(json.dumps(result["aggregate"], ensure_ascii=False, indent=2))
    return 0


def _parse_args(argv=None) -> argparse.Namespace:
    p = argparse.ArgumentParser(
        prog="eval_research_quality",
        description=(
            "deep-research-engine 품질 회귀 게이트 — golden 스냅샷을 평가해 "
            "baseline 대비 회귀 시 비영 종료(요구사항 9.7)."
        ),
    )
    p.add_argument(
        "--golden", default=DEFAULT_GOLDEN_PATH,
        help=f"golden 스냅샷 JSON 경로(기본 {DEFAULT_GOLDEN_PATH}).",
    )
    p.add_argument(
        "--tolerance", type=float, default=None,
        help="허용 상대 하락폭 오버라이드(미지정 시 AE_RESEARCH_REGRESSION_TOLERANCE).",
    )
    p.add_argument(
        "--update-baseline", action="store_true",
        help="현재 golden 스냅샷 평가값으로 baseline 집계를 (재)기록하고 종료.",
    )
    return p.parse_args(argv)


def main(argv=None) -> int:
    """CLI 엔트리 — 게이트 실행(기본) 또는 baseline 갱신(--update-baseline)."""
    args = _parse_args(argv)
    try:
        if args.update_baseline:
            return _update_baseline(args)
        return _run_gate(args)
    except FileNotFoundError:
        print(f"[gate] golden 스냅샷을 찾을 수 없습니다: {args.golden}", file=sys.stderr)
        return 2
    except (ValueError, json.JSONDecodeError) as e:
        print(f"[gate] 스냅샷 로드 오류: {e}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
