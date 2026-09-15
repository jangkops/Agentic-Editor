"""Repository_Baseline_Inspector — 10개 Baseline_Category의 file+symbol 기준선 검사.

이 모듈은 `gateway-models-effort-support` 기능의 Baseline_Record를 생성한다.
Baseline_Record는 "지금 저장소의 어느 파일·심볼이 어떤 통합 표면을 담당하는가"를
revision·UTC 시각과 함께 재현 가능하게 남기는 기록이며, **그 자체로는
Authoritative_Evidence가 아니다.** `evidenceEligible: False`인 경우 Evidence_Collector는
해당 baseline을 근거 집합에서 제외한다(Requirement 1.16).

설계 원칙 — 순수 add(추가) 방식:
- stdlib만 사용한다(ast, re, json, os, subprocess, datetime). 신규 파서 의존을 추가하지 않는다.
- 기존 모듈을 import 하지 않는다(비침습). 검사 대상 파일은 **텍스트로만** 읽는다.
- 미발견은 예외가 아니라 `found: False` + `reason`으로 기록한다.

검사 방식:
- `.py` → `ast.parse`로 top-level 및 클래스 멤버 심볼을 확인한다.
- `.js` → 선언 패턴 스캔으로 확인한다(Vanilla JS, 파서 의존 추가 없음).

참조: .kiro/specs/gateway-models-effort-support/design.md
  - Components and Interfaces 1절 (Repository_Baseline_Inspector, BASELINE_TARGETS)
Requirements: 1.1, 1.2, 1.3, 1.4, 1.5, 1.6, 1.7, 1.8, 1.9, 1.10, 1.11, 1.12, 1.16
"""
from __future__ import annotations

import ast
import os
import re
import subprocess
from datetime import datetime, timezone
from typing import Any

# 기록 스키마 버전 — 이후 store/report 계층이 형식 변화를 감지할 때 사용한다.
SCHEMA_VERSION = 1

# 진단 문자열 최대 길이(프로젝트 로깅 관례와 동일하게 200자 절단).
_REASON_MAX = 200


# ─────────────────────────────────────────────────────────────────
# Baseline_Category 목록과 검사 대상
#
# design.md의 BASELINE_TARGETS 표를 그대로 사용한다.
# 각 항목은 (Baseline_Category, 저장소 상대 경로, 확인할 심볼 이름들)이다.
# 하나의 category가 복수 파일을 가질 수 있다(예: catalog).
# ─────────────────────────────────────────────────────────────────
REQUIRED_CATEGORIES: tuple[str, ...] = (
    "catalog",
    "allowlist",
    "gateway-client",
    "route",
    "request-builder",
    "model-selection",
    "response-adapter",
    "job-polling",
    "retry",
    "error-handling",
)

BASELINE_TARGETS: tuple[tuple[str, str, tuple[str, ...]], ...] = (
    ("catalog", "ai_engine/server.py", ("list_models", "_update_gateway_model_cache")),
    ("catalog", "ai_engine/openai_catalog.py", ("get_catalog_source", "FileCatalogSource", "GatewayListSource")),
    ("allowlist", "ai_engine/server.py", ("_model_is_denied", "_record_denied_model", "_maybe_record_denied_from_error")),
    ("gateway-client", "ai_engine/gateway_module.py", ("GatewayClient", "_get_creds", "inject_credentials", "_sign")),
    ("route", "ai_engine/server.py", ("is_openai_model", "route_openai_chat", "_resolve_callable_model_id")),
    ("request-builder", "ai_engine/gateway_module.py", ("_build_payload", "_build_openai_payload", "openai_responses_job_submit")),
    ("model-selection", "src/main.js", ("rebuildModelList", "_fetchFilteredModelCatalog", "refreshModelsPreservingSelection", "resolveSelection")),
    ("response-adapter", "ai_engine/openai_adapter.py", ("to_converse", "extract_text", "extract_tool_calls", "extract_usage")),
    ("job-polling", "ai_engine/gateway_module.py", ("_poll_job_data", "_poll_job_result", "_openai_poll_job")),
    ("retry", "ai_engine/gateway_module.py", ("converse", "converse_stream_live", "stream_sse_realtime", "_openai_post_with_retry")),
    ("error-handling", "ai_engine/gateway_module.py", ("QuotaExceededError", "OpenAISurfaceError", "SyncTimeout", "JobTimeout", "JobFailed", "OpenAIModelUnsupported")),
)


# ─────────────────────────────────────────────────────────────────
# 시각 · revision 유틸
# ─────────────────────────────────────────────────────────────────
def utc_now_iso(now: datetime | None = None) -> str:
    """UTC ISO 8601 문자열(초 정밀도, `Z` 접미사)을 반환한다 — Requirement 1.12."""
    moment = now or datetime.now(timezone.utc)
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=timezone.utc)
    return moment.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def default_repo_root() -> str:
    """이 파일 위치(`ai_engine/capability/`)를 기준으로 저장소 루트를 추정한다."""
    return os.path.abspath(os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", ".."))


def current_revision(repo_root: str) -> str | None:
    """Current_Revision(git HEAD)을 반환한다 — Requirement 1.11.

    git이 없거나 저장소가 아니면 예외를 던지지 않고 `None`을 반환한다.
    """
    try:
        out = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=repo_root,
            capture_output=True,
            text=True,
            timeout=10,
            check=False,
        )
    except Exception:
        return None
    if out.returncode != 0:
        return None
    rev = (out.stdout or "").strip()
    return rev or None


def _truncate(text: str) -> str:
    text = str(text)
    return text if len(text) <= _REASON_MAX else text[:_REASON_MAX]


# ─────────────────────────────────────────────────────────────────
# Python 심볼 색인 — ast.parse 로 top-level · 클래스 멤버 확인
# ─────────────────────────────────────────────────────────────────
# 모듈 최상단의 조건부/예외 처리 블록 안에 있는 정의도 top-level로 취급한다.
_TRANSPARENT_NODES = (ast.If, ast.Try, ast.With, ast.AsyncWith, ast.For, ast.AsyncFor, ast.While)


def python_symbol_index(source: str) -> dict[str, list[tuple[str | None, int]]]:
    """`{심볼명: [(scope, lineno), ...]}` 색인을 만든다.

    scope는 top-level이면 `None`, 클래스 멤버면 클래스 경로(`"GatewayClient"`)다.
    중복 정의는 발견 순서대로 모두 보존한다(동일 이름의 재정의를 숨기지 않는다).
    """
    tree = ast.parse(source)
    index: dict[str, list[tuple[str | None, int]]] = {}

    def record(name: str, scope: str | None, lineno: int) -> None:
        index.setdefault(name, []).append((scope, lineno))

    def walk(node: ast.AST, scope: str | None) -> None:
        for child in ast.iter_child_nodes(node):
            if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef)):
                record(child.name, scope, child.lineno)
            elif isinstance(child, ast.ClassDef):
                record(child.name, scope, child.lineno)
                walk(child, f"{scope}.{child.name}" if scope else child.name)
            elif isinstance(child, ast.Assign):
                for target in child.targets:
                    if isinstance(target, ast.Name):
                        record(target.id, scope, child.lineno)
            elif isinstance(child, ast.AnnAssign):
                if isinstance(child.target, ast.Name):
                    record(child.target.id, scope, child.lineno)
            elif isinstance(child, _TRANSPARENT_NODES):
                walk(child, scope)

    walk(tree, None)
    return index


# ─────────────────────────────────────────────────────────────────
# JavaScript 선언 패턴 스캔 — 파서 의존 추가 없음
# ─────────────────────────────────────────────────────────────────
# (kind, 패턴 템플릿). 템플릿의 `{s}`에 escape된 심볼명을 넣는다.
_JS_DECLARATION_PATTERNS: tuple[tuple[str, str], ...] = (
    ("function-declaration", r"\bfunction\s*\*?\s+{s}\s*\("),
    ("class-declaration", r"\bclass\s+{s}\b"),
    ("binding-declaration", r"\b(?:const|let|var)\s+{s}\s*="),
    ("property-function", r"\b{s}\s*[:=]\s*(?:async\s*)?(?:function\b|\()"),
    ("method-shorthand", r"^[ \t]*(?:static\s+)?(?:async\s+)?\*?\s*{s}\s*\([^\n]*\)\s*\{{"),
)

# 주석 줄에서 나온 매치는 선언으로 인정하지 않는다(문자열/정규식 오탐 최소화).
_JS_COMMENT_LINE = re.compile(r"^\s*(?://|/\*|\*)")


def js_declaration_hits(source: str, symbol: str) -> list[tuple[str, int]]:
    """`.js` 원문에서 심볼 선언 패턴 매치를 `[(kind, lineno), ...]`로 반환한다."""
    lines = source.splitlines()
    hits: dict[int, str] = {}
    for kind, template in _JS_DECLARATION_PATTERNS:
        pattern = re.compile(template.format(s=re.escape(symbol)), re.MULTILINE)
        for match in pattern.finditer(source):
            lineno = source.count("\n", 0, match.start()) + 1
            line = lines[lineno - 1] if 0 < lineno <= len(lines) else ""
            if _JS_COMMENT_LINE.match(line):
                continue
            hits.setdefault(lineno, kind)
    return [(kind, lineno) for lineno, kind in sorted(hits.items())]


# ─────────────────────────────────────────────────────────────────
# 파일 단위 심볼 조회 (읽기·파싱 결과를 캐시)
# ─────────────────────────────────────────────────────────────────
class _FileProbe:
    """검사 대상 파일 1개의 읽기·파싱 상태와 심볼 조회기."""

    def __init__(self, repo_root: str, rel_path: str) -> None:
        self.rel_path = rel_path
        self.abs_path = os.path.join(repo_root, rel_path)
        self.detector = "python-ast" if rel_path.endswith(".py") else "js-declaration-scan"
        self.error: str | None = None
        self._source: str | None = None
        self._index: dict[str, list[tuple[str | None, int]]] | None = None

        if not os.path.isfile(self.abs_path):
            self.error = _truncate(f"file-not-found: {rel_path}")
            return
        try:
            with open(self.abs_path, "r", encoding="utf-8") as handle:
                self._source = handle.read()
        except Exception as exc:  # 읽기 실패도 예외가 아니라 미발견 사유로 기록한다.
            self.error = _truncate(f"read-error: {type(exc).__name__}: {exc}")
            return

        if rel_path.endswith(".py"):
            try:
                self._index = python_symbol_index(self._source)
            except SyntaxError as exc:
                self.error = _truncate(f"python-parse-error: line {exc.lineno}: {exc.msg}")
            except Exception as exc:
                self.error = _truncate(f"python-parse-error: {type(exc).__name__}: {exc}")

    def lookup(self, symbol: str) -> tuple[list[int], str | None, str | None, str | None]:
        """`(occurrences, scope, matchKind, reason)`을 반환한다.

        - `occurrences`가 빈 목록이면 미발견이고 `reason`이 채워진다.
        - `scope`는 `.py`의 클래스 경로(top-level이면 `None`), `.js`는 항상 `None`이다.
        - `matchKind`는 발견 방식(`python-ast` 심볼 종류 또는 JS 선언 패턴 종류)이다.
        """
        if self.error is not None:
            return [], None, None, self.error
        if self.rel_path.endswith(".py"):
            hits = (self._index or {}).get(symbol) or []
            if not hits:
                return [], None, None, _truncate(f"symbol-not-found: {symbol} in {self.rel_path} (python-ast)")
            scope, _ = hits[0]
            kind = "class-member" if scope else "top-level"
            return [lineno for _, lineno in hits], scope, kind, None

        js_hits = js_declaration_hits(self._source or "", symbol)
        if not js_hits:
            return [], None, None, _truncate(f"symbol-not-found: {symbol} in {self.rel_path} (js-declaration-scan)")
        return [lineno for _, lineno in js_hits], None, js_hits[0][0], None


# ─────────────────────────────────────────────────────────────────
# 검사 진입점
# ─────────────────────────────────────────────────────────────────
def inspect(
    repo_root: str | None = None,
    *,
    revision: str | None = None,
    now: datetime | None = None,
    targets: tuple[tuple[str, str, tuple[str, ...]], ...] | None = None,
) -> dict[str, Any]:
    """10개 Baseline_Category 전체의 Baseline_Record를 생성한다.

    반환 구조:
        {
          "schemaVersion": 1,
          "repoRoot": str,
          "revision": str | None,          # Current_Revision (Requirement 1.11)
          "inspectedAt": "YYYY-MM-DDTHH:MM:SSZ",  # UTC ISO 8601 (Requirement 1.12)
          "categories": [...10개...],
          "records": [ {category, file, symbol, found, line, occurrences, scope,
                        matchKind, detector, reason, revision, inspectedAt}, ... ],
          "categorySummary": {category: {...}},
          "categoryEligibility": {category: bool},
          "missingCategories": [...],      # record가 아예 없는 category
          "unresolvedCategories": [...],   # record는 있으나 found가 0건인 category
          "evidenceEligible": bool,        # Requirement 1.16
          "evidenceEligibleReason": str | None,
        }

    미발견은 예외가 아니라 `found: False` + `reason`으로 기록한다.
    `revision`과 `inspectedAt`은 최상단과 **모든 Baseline_Record**에 함께 기록한다.
    """
    root = os.path.abspath(repo_root or default_repo_root())
    target_table = targets if targets is not None else BASELINE_TARGETS
    rev = revision if revision is not None else current_revision(root)
    inspected_at = utc_now_iso(now)

    probes: dict[str, _FileProbe] = {}
    records: list[dict[str, Any]] = []

    for category, rel_path, symbols in target_table:
        probe = probes.get(rel_path)
        if probe is None:
            probe = _FileProbe(root, rel_path)
            probes[rel_path] = probe

        if not symbols:
            # 심볼 목록이 비어 있는 대상은 파일 수준 결과만 기록한다.
            records.append(
                _record(
                    category=category,
                    rel_path=rel_path,
                    symbol=None,
                    occurrences=[],
                    scope=None,
                    match_kind=None,
                    detector=probe.detector,
                    reason=probe.error or _truncate(f"no-symbol-target: {rel_path}"),
                    revision=rev,
                    inspected_at=inspected_at,
                )
            )
            continue

        for symbol in symbols:
            occurrences, scope, match_kind, reason = probe.lookup(symbol)
            records.append(
                _record(
                    category=category,
                    rel_path=rel_path,
                    symbol=symbol,
                    occurrences=occurrences,
                    scope=scope,
                    match_kind=match_kind,
                    detector=probe.detector,
                    reason=reason,
                    revision=rev,
                    inspected_at=inspected_at,
                )
            )

    summary = _summarize(records)
    eligibility = {
        category: bool(
            summary.get(category, {}).get("recordCount", 0) > 0
            and summary.get(category, {}).get("foundCount", 0) > 0
        )
        for category in REQUIRED_CATEGORIES
    }
    missing = [c for c in REQUIRED_CATEGORIES if summary.get(c, {}).get("recordCount", 0) == 0]
    unresolved = [
        c
        for c in REQUIRED_CATEGORIES
        if summary.get(c, {}).get("recordCount", 0) > 0 and summary.get(c, {}).get("foundCount", 0) == 0
    ]
    eligible = not missing and not unresolved

    if eligible:
        eligible_reason = None
    elif missing:
        eligible_reason = _truncate("empty-category: " + ", ".join(missing))
    else:
        eligible_reason = _truncate("no-found-symbol-in-category: " + ", ".join(unresolved))

    return {
        "schemaVersion": SCHEMA_VERSION,
        "repoRoot": root,
        "revision": rev,
        "inspectedAt": inspected_at,
        "categories": list(REQUIRED_CATEGORIES),
        "records": records,
        "categorySummary": summary,
        "categoryEligibility": eligibility,
        "missingCategories": missing,
        "unresolvedCategories": unresolved,
        "evidenceEligible": eligible,
        "evidenceEligibleReason": eligible_reason,
    }


def _record(
    *,
    category: str,
    rel_path: str,
    symbol: str | None,
    occurrences: list[int],
    scope: str | None,
    match_kind: str | None,
    detector: str,
    reason: str | None,
    revision: str | None,
    inspected_at: str,
) -> dict[str, Any]:
    """단위 Baseline_Record를 만든다(경로는 항상 posix 상대 경로로 기록)."""
    found = bool(occurrences)
    return {
        "category": category,
        "file": rel_path.replace(os.sep, "/"),
        "symbol": symbol,
        "found": found,
        "line": occurrences[0] if found else None,
        "occurrences": list(occurrences),
        "scope": scope if found else None,
        "matchKind": match_kind if found else None,
        "detector": detector,
        "reason": None if found else reason,
        "revision": revision,
        "inspectedAt": inspected_at,
    }


def _summarize(records: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    """category별 record 수·발견 수·미발견 심볼 목록을 집계한다."""
    summary: dict[str, dict[str, Any]] = {}
    for category in REQUIRED_CATEGORIES:
        summary[category] = {"recordCount": 0, "foundCount": 0, "missingCount": 0, "files": [], "missingSymbols": []}

    for rec in records:
        bucket = summary.setdefault(
            rec["category"],
            {"recordCount": 0, "foundCount": 0, "missingCount": 0, "files": [], "missingSymbols": []},
        )
        bucket["recordCount"] += 1
        if rec["found"]:
            bucket["foundCount"] += 1
        else:
            bucket["missingCount"] += 1
            bucket["missingSymbols"].append(f"{rec['file']}::{rec['symbol']}")
        if rec["file"] not in bucket["files"]:
            bucket["files"].append(rec["file"])
    return summary


if __name__ == "__main__":  # pragma: no cover - 수동 확인용
    import json

    print(json.dumps(inspect(), ensure_ascii=False, indent=2))
