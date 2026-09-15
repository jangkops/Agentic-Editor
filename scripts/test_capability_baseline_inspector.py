"""Unit — Repository_Baseline_Inspector의 10 category record·revision·시각·근거 자격.

Feature: gateway-models-effort-support (task 3.2)
대상: ai_engine.capability.baseline_inspector (+ ai_engine.capability.contracts)

검증 범위
  - 10개 Baseline_Category 전체 record 생성 (실제 저장소 + stub 저장소)
  - 미발견은 예외가 아니라 `found: False` + `reason` 기록
  - category 결손 시 `evidenceEligible: False` (Requirement 1.16)
  - 모든 Baseline_Record에 `revision`(Requirement 1.11)과
    UTC ISO 8601 `inspectedAt`(Requirement 1.12) 기록
  - 표시명·주석·Seed_Entry·mock만으로 구성된 근거는 `UNVERIFIED` 유지
    (Requirement 1.17)

Baseline_Record 자체는 Authoritative_Evidence가 아니다. 이 테스트도 Gateway를
호출하지 않으며, 통과 사실이 Gateway 지원 근거가 되지 않는다.

실행: ai_engine/.venv/bin/python -m pytest scripts/test_capability_baseline_inspector.py -q
"""
from __future__ import annotations

import os
import re
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from ai_engine.capability import baseline_inspector as bi  # noqa: E402
from ai_engine.capability import contracts  # noqa: E402

# Baseline_Record의 필드 집합 — 스키마 드리프트를 즉시 잡는다.
RECORD_KEYS = {
    "category",
    "file",
    "symbol",
    "found",
    "line",
    "occurrences",
    "scope",
    "matchKind",
    "detector",
    "reason",
    "revision",
    "inspectedAt",
}

# UTC ISO 8601(초 정밀도, Z 접미사) — Requirement 1.12
UTC_ISO_RE = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z$")

# 진단 문자열 절단 길이(모듈 관례: 200자)
REASON_MAX = 200

# 여섯 Candidate_Label — 검색 라벨일 뿐 model identity가 아니다.
CANDIDATE_LABELS = ("opus 5", "sonnet 5", "gpt 5.6", "sol", "terra", "luna")


# ─────────────────────────────────────────────────────────────────
# 헬퍼
# ─────────────────────────────────────────────────────────────────
def _build_stub_repo(
    root: Path,
    *,
    drop_symbols: tuple[tuple[str, str], ...] = (),
    delete_files: tuple[str, ...] = (),
) -> None:
    """BASELINE_TARGETS 표의 모든 파일·심볼을 갖춘 stub 저장소를 만든다.

    `drop_symbols`의 `(파일, 심볼)`은 선언에서 제외하고, `delete_files`의 파일은
    아예 만들지 않는다 — 결손 시나리오를 만들기 위한 장치다.
    """
    dropped = set(drop_symbols)
    per_file: dict[str, list[str]] = {}
    for _category, rel_path, symbols in bi.BASELINE_TARGETS:
        bucket = per_file.setdefault(rel_path, [])
        for symbol in symbols:
            if (rel_path, symbol) in dropped or symbol in bucket:
                continue
            bucket.append(symbol)

    skip = set(delete_files)
    for rel_path, symbols in per_file.items():
        if rel_path in skip:
            continue
        target = root / rel_path
        target.parent.mkdir(parents=True, exist_ok=True)
        if rel_path.endswith(".py"):
            body = "\n\n".join(f"def {symbol}():\n    return None" for symbol in symbols)
        else:
            body = "\n\n".join(f"function {symbol}() {{\n  return null;\n}}" for symbol in symbols)
        target.write_text(body + "\n", encoding="utf-8")


def _records_of(result: dict, category: str) -> list[dict]:
    return [rec for rec in result["records"] if rec["category"] == category]


@pytest.fixture(scope="module")
def real_result() -> dict:
    """실제 저장소 1회 검사 결과(검사 자체는 읽기 전용)."""
    return bi.inspect(bi.default_repo_root())


# ─────────────────────────────────────────────────────────────────
# 실제 저장소 검사
# ─────────────────────────────────────────────────────────────────
def test_real_repo_produces_records_for_all_ten_categories(real_result):
    assert len(bi.REQUIRED_CATEGORIES) == 10
    assert real_result["categories"] == list(bi.REQUIRED_CATEGORIES)

    # 10 category 전부 record가 존재한다(하나도 비어 있지 않다).
    for category in bi.REQUIRED_CATEGORIES:
        assert _records_of(real_result, category), f"{category} category record 없음"

    # BASELINE_TARGETS 표의 모든 (category, file, symbol) 조합이 record로 남는다.
    produced = {(rec["category"], rec["file"], rec["symbol"]) for rec in real_result["records"]}
    for category, rel_path, symbols in bi.BASELINE_TARGETS:
        for symbol in symbols:
            key = (category, rel_path.replace(os.sep, "/"), symbol)
            assert key in produced, f"record 누락: {key}"

    assert real_result["repoRoot"] == os.path.abspath(bi.default_repo_root())


def test_real_repo_records_carry_revision_and_utc_inspected_at(real_result):
    """Requirement 1.11 / 1.12 — 모든 Baseline_Record에 revision·UTC 시각 기록."""
    assert UTC_ISO_RE.match(real_result["inspectedAt"]), real_result["inspectedAt"]
    assert contracts.is_utc_iso8601(real_result["inspectedAt"])
    parsed = datetime.fromisoformat(real_result["inspectedAt"].replace("Z", "+00:00"))
    assert parsed.utcoffset() == timedelta(0)

    revision = real_result["revision"]
    # git이 없는 환경도 예외 없이 None을 기록한다.
    assert revision is None or (isinstance(revision, str) and revision)

    for rec in real_result["records"]:
        assert set(rec) == RECORD_KEYS, f"record 스키마 변경: {sorted(set(rec) ^ RECORD_KEYS)}"
        assert rec["revision"] == revision
        assert rec["inspectedAt"] == real_result["inspectedAt"]
        assert UTC_ISO_RE.match(rec["inspectedAt"])


def test_real_repo_baseline_is_evidence_eligible(real_result):
    """Requirement 1.16 — 현재 저장소는 10 category 전부 해소되어 근거 자격을 얻는다."""
    missing_symbols = [
        f"{rec['category']}:{rec['file']}::{rec['symbol']} ({rec['reason']})"
        for rec in real_result["records"]
        if not rec["found"]
    ]
    assert real_result["missingCategories"] == []
    assert real_result["unresolvedCategories"] == [], missing_symbols
    assert real_result["evidenceEligible"] is True, missing_symbols
    assert real_result["evidenceEligibleReason"] is None
    assert all(real_result["categoryEligibility"][c] for c in bi.REQUIRED_CATEGORIES)

    # found ↔ reason 상호배타 + 발견 record의 부수 필드 일관성
    for rec in real_result["records"]:
        if rec["found"]:
            assert rec["reason"] is None
            assert rec["occurrences"] and rec["line"] == rec["occurrences"][0]
            assert rec["matchKind"]
        else:
            assert isinstance(rec["reason"], str) and rec["reason"]
            assert rec["line"] is None and rec["occurrences"] == []
        assert rec["detector"] in {"python-ast", "js-declaration-scan"}


# ─────────────────────────────────────────────────────────────────
# revision · UTC 시각
# ─────────────────────────────────────────────────────────────────
def test_utc_now_iso_normalizes_naive_and_offset_datetimes():
    """Requirement 1.12 — 어떤 입력이든 UTC ISO 8601(Z)로 기록한다."""
    assert bi.utc_now_iso(datetime(2026, 8, 3, 1, 53, 36, tzinfo=timezone.utc)) == "2026-08-03T01:53:36Z"
    # naive는 UTC로 간주한다.
    assert bi.utc_now_iso(datetime(2026, 8, 3, 1, 53, 36)) == "2026-08-03T01:53:36Z"
    # offset이 있으면 UTC로 환산한다(KST 10:53:36 → UTC 01:53:36).
    kst = timezone(timedelta(hours=9))
    assert bi.utc_now_iso(datetime(2026, 8, 3, 10, 53, 36, tzinfo=kst)) == "2026-08-03T01:53:36Z"
    assert UTC_ISO_RE.match(bi.utc_now_iso())


def test_revision_and_time_are_injected_into_every_record(tmp_path):
    """Requirement 1.11 — 주어진 revision이 모든 record에 그대로 기록된다."""
    _build_stub_repo(tmp_path)
    moment = datetime(2026, 8, 3, 1, 53, 36, tzinfo=timezone.utc)
    result = bi.inspect(str(tmp_path), revision="bafb45153835c4b6366e877f41c84c658bf99074", now=moment)

    assert result["revision"] == "bafb45153835c4b6366e877f41c84c658bf99074"
    assert result["inspectedAt"] == "2026-08-03T01:53:36Z"
    assert result["records"]
    for rec in result["records"]:
        assert rec["revision"] == "bafb45153835c4b6366e877f41c84c658bf99074"
        assert rec["inspectedAt"] == "2026-08-03T01:53:36Z"


def test_missing_git_revision_is_recorded_as_none(tmp_path):
    """git 저장소가 아니어도 예외 없이 revision None을 기록한다."""
    assert bi.current_revision(str(tmp_path)) is None

    _build_stub_repo(tmp_path)
    result = bi.inspect(str(tmp_path))
    assert result["revision"] is None
    assert all(rec["revision"] is None for rec in result["records"])
    assert UTC_ISO_RE.match(result["inspectedAt"])


# ─────────────────────────────────────────────────────────────────
# 미발견 기록 (예외 아님)
# ─────────────────────────────────────────────────────────────────
def test_missing_file_and_missing_symbol_are_recorded_as_not_found(tmp_path):
    """미발견은 예외가 아니라 found: False + reason으로 남는다."""
    (tmp_path / "ai_engine").mkdir()
    (tmp_path / "ai_engine" / "server.py").write_text("def list_models():\n    return []\n", encoding="utf-8")

    targets = (
        ("catalog", "ai_engine/server.py", ("list_models", "_update_gateway_model_cache")),
        ("retry", "ai_engine/gateway_module.py", ("converse",)),
    )
    result = bi.inspect(str(tmp_path), targets=targets)
    by_symbol = {(rec["file"], rec["symbol"]): rec for rec in result["records"]}

    found = by_symbol[("ai_engine/server.py", "list_models")]
    assert found["found"] is True and found["reason"] is None

    absent_symbol = by_symbol[("ai_engine/server.py", "_update_gateway_model_cache")]
    assert absent_symbol["found"] is False
    assert absent_symbol["reason"].startswith("symbol-not-found: _update_gateway_model_cache")
    assert absent_symbol["line"] is None and absent_symbol["occurrences"] == []
    assert absent_symbol["scope"] is None and absent_symbol["matchKind"] is None

    absent_file = by_symbol[("ai_engine/gateway_module.py", "converse")]
    assert absent_file["found"] is False
    assert absent_file["reason"] == "file-not-found: ai_engine/gateway_module.py"


def test_parse_error_and_empty_symbol_target_are_recorded_as_reasons(tmp_path):
    """파싱 실패·심볼 대상 부재도 예외가 아니라 reason으로 남는다."""
    (tmp_path / "ai_engine").mkdir()
    (tmp_path / "ai_engine" / "server.py").write_text("def broken(:\n", encoding="utf-8")
    (tmp_path / "ai_engine" / "openai_catalog.py").write_text("SEED = []\n", encoding="utf-8")

    targets = (
        ("catalog", "ai_engine/server.py", ("list_models",)),
        ("allowlist", "ai_engine/openai_catalog.py", ()),
    )
    result = bi.inspect(str(tmp_path), targets=targets)
    by_key = {(rec["file"], rec["symbol"]): rec for rec in result["records"]}

    parse_failed = by_key[("ai_engine/server.py", "list_models")]
    assert parse_failed["found"] is False
    assert parse_failed["reason"].startswith("python-parse-error:")

    no_symbol = by_key[("ai_engine/openai_catalog.py", None)]
    assert no_symbol["found"] is False
    assert no_symbol["reason"] == "no-symbol-target: ai_engine/openai_catalog.py"


def test_long_reason_is_truncated(tmp_path):
    """진단 문자열은 200자로 절단한다(로그 관례와 동일)."""
    (tmp_path / "mod.py").write_text("VALUE = 1\n", encoding="utf-8")
    long_symbol = "s" * 400
    result = bi.inspect(str(tmp_path), targets=(("catalog", "mod.py", (long_symbol,)),))

    reason = result["records"][0]["reason"]
    assert reason.startswith("symbol-not-found: ")
    assert len(reason) == REASON_MAX


def test_python_scope_occurrences_and_js_declaration_scan(tmp_path):
    """`.py`는 ast 심볼 색인, `.js`는 선언 패턴 스캔으로 발견한다."""
    (tmp_path / "mod.py").write_text(
        "def dup():\n    pass\n\n\n"
        "def dup():\n    pass\n\n\n"
        "class Holder:\n    def member(self):\n        pass\n\n\n"
        "if True:\n    def conditional():\n        pass\n",
        encoding="utf-8",
    )
    (tmp_path / "front.js").write_text(
        "function declared(a) { return a; }\n"
        "const bound = 1;\n"
        "class Widget {\n  shorthand(x) {\n    return x;\n  }\n}\n",
        encoding="utf-8",
    )

    targets = (
        ("catalog", "mod.py", ("dup", "member", "conditional")),
        ("model-selection", "front.js", ("declared", "bound", "shorthand")),
    )
    result = bi.inspect(str(tmp_path), targets=targets)
    by_symbol = {rec["symbol"]: rec for rec in result["records"]}

    assert by_symbol["dup"]["occurrences"] == [1, 5]
    assert by_symbol["dup"]["line"] == 1
    assert by_symbol["dup"]["scope"] is None
    assert by_symbol["dup"]["matchKind"] == "top-level"

    assert by_symbol["member"]["scope"] == "Holder"
    assert by_symbol["member"]["matchKind"] == "class-member"
    # 모듈 최상단 조건부 블록의 정의도 top-level로 취급한다.
    assert by_symbol["conditional"]["found"] is True
    assert by_symbol["conditional"]["scope"] is None

    for symbol, kind in (
        ("declared", "function-declaration"),
        ("bound", "binding-declaration"),
        ("shorthand", "method-shorthand"),
    ):
        assert by_symbol[symbol]["found"] is True, symbol
        assert by_symbol[symbol]["detector"] == "js-declaration-scan"
        assert by_symbol[symbol]["matchKind"] == kind


# ─────────────────────────────────────────────────────────────────
# evidenceEligible (Requirement 1.16)
# ─────────────────────────────────────────────────────────────────
def test_complete_stub_repo_is_evidence_eligible(tmp_path):
    _build_stub_repo(tmp_path)
    result = bi.inspect(str(tmp_path))

    assert [rec for rec in result["records"] if not rec["found"]] == []
    assert result["evidenceEligible"] is True
    assert result["evidenceEligibleReason"] is None
    assert result["missingCategories"] == [] and result["unresolvedCategories"] == []


def test_category_without_found_symbol_is_not_evidence_eligible(tmp_path):
    """category 하나가 결손되면 evidenceEligible: False로 근거 집합에서 제외된다."""
    drop = tuple(
        (rel_path, symbol)
        for category, rel_path, symbols in bi.BASELINE_TARGETS
        if category == "job-polling"
        for symbol in symbols
    )
    _build_stub_repo(tmp_path, drop_symbols=drop)
    result = bi.inspect(str(tmp_path))

    assert result["unresolvedCategories"] == ["job-polling"]
    assert result["missingCategories"] == []
    assert result["evidenceEligible"] is False
    assert result["evidenceEligibleReason"] == "no-found-symbol-in-category: job-polling"
    assert result["categoryEligibility"]["job-polling"] is False
    # 나머지 category는 그대로 해소된 상태를 유지한다.
    assert all(
        result["categoryEligibility"][c] for c in bi.REQUIRED_CATEGORIES if c != "job-polling"
    )
    # 결손 category의 record는 남아 있고 전부 reason을 갖는다.
    polling = _records_of(result, "job-polling")
    assert polling and all(rec["found"] is False and rec["reason"] for rec in polling)


def test_deleted_file_makes_its_category_ineligible(tmp_path):
    _build_stub_repo(tmp_path, delete_files=("src/main.js",))
    result = bi.inspect(str(tmp_path))

    assert result["evidenceEligible"] is False
    assert result["unresolvedCategories"] == ["model-selection"]
    selection = _records_of(result, "model-selection")
    assert selection
    assert all(rec["reason"] == "file-not-found: src/main.js" for rec in selection)


def test_category_absent_from_targets_is_not_evidence_eligible(tmp_path):
    """record가 아예 없는 category도 근거 자격을 박탈한다."""
    _build_stub_repo(tmp_path)
    targets = tuple(t for t in bi.BASELINE_TARGETS if t[0] != "error-handling")
    result = bi.inspect(str(tmp_path), targets=targets)

    assert result["categories"] == list(bi.REQUIRED_CATEGORIES)
    assert result["missingCategories"] == ["error-handling"]
    assert result["evidenceEligible"] is False
    assert result["evidenceEligibleReason"] == "empty-category: error-handling"
    assert result["categorySummary"]["error-handling"]["recordCount"] == 0
    assert _records_of(result, "error-handling") == []


# ─────────────────────────────────────────────────────────────────
# 표시명·주석·Seed_Entry·mock 근거 (Requirement 1.17)
# ─────────────────────────────────────────────────────────────────
def test_baseline_record_carries_no_verification_status(real_result):
    """Baseline_Record는 Authoritative_Evidence가 아니라 상태 승격 필드를 갖지 않는다."""
    assert "verificationStatus" not in real_result
    for rec in real_result["records"]:
        assert set(rec) == RECORD_KEYS
        assert "verificationStatus" not in rec
        assert "routes" not in rec and "effort" not in rec
        # 상태 enum 값이 baseline record에 실려 나가지 않는다.
        values = {v for v in rec.values() if isinstance(v, str)}
        assert values.isdisjoint(set(contracts.Verification_Status.values()))
        assert values.isdisjoint(set(contracts.Route_Support_Status.values()))


def test_comment_seed_and_mock_mentions_are_not_symbol_evidence(tmp_path):
    """표시명·주석·Seed_Entry 문자열·mock 이름은 심볼 발견 근거가 아니다."""
    (tmp_path / "ai_engine").mkdir()
    (tmp_path / "ai_engine" / "server.py").write_text(
        '"""표시명 "list_models" 는 docstring에만 등장한다."""\n'
        "# _update_gateway_model_cache 는 주석에만 등장한다.\n"
        'DEFAULT_SEED_MODELS = ["list_models", "_update_gateway_model_cache"]\n'
        "mock_list_models = object()\n",
        encoding="utf-8",
    )
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "main.js").write_text(
        "// rebuildModelList 는 주석에만 등장한다\n"
        "/* resolveSelection 도 주석뿐이다 */\n"
        "const mock_rebuildModelList = () => null;\n"
        'const SEED_LABELS = ["rebuildModelList", "resolveSelection"];\n',
        encoding="utf-8",
    )

    targets = (
        ("catalog", "ai_engine/server.py", ("list_models", "_update_gateway_model_cache")),
        ("model-selection", "src/main.js", ("rebuildModelList", "resolveSelection")),
    )
    result = bi.inspect(str(tmp_path), targets=targets)

    for rec in result["records"]:
        assert rec["found"] is False, f"{rec['symbol']} 이 주석·seed·mock으로 발견 처리됨"
        assert rec["reason"].startswith("symbol-not-found: ")
    assert result["evidenceEligible"] is False


def test_seed_sourced_entry_with_display_name_and_mock_refs_stays_unverified(real_result):
    """Requirement 1.17 — 표시명·주석·Seed·mock 근거만으로는 UNVERIFIED를 벗어나지 못한다."""
    # baseline record 참조를 'mock 근거'로 붙여도 승격되지 않는다.
    mock_refs = [
        f"{rec['file']}::{rec['symbol']}" for rec in real_result["records"][:2] if rec["symbol"]
    ]
    assert mock_refs

    for label in CANDIDATE_LABELS:
        entry = contracts.new_entry(label, source_kind=contracts.Source_Kind.SEED)
        entry["displayName"] = f"표시명 {label}"  # 표시명은 근거가 아니다
        entry["evidence"] = list(mock_refs)  # mock·baseline 참조

        assert entry["verificationStatus"] == contracts.Verification_Status.UNVERIFIED
        assert entry["sourceKind"] == contracts.Source_Kind.SEED
        # 라벨·표시명에서 identity를 유도하지 않는다.
        assert entry["modelId"] == contracts.UNDETERMINED
        assert entry["provider"] == contracts.UNDETERMINED
        assert entry["invocationModelIds"] == []
        assert entry["verifiedAt"] == contracts.UNDETERMINED

        for route in contracts.KNOWN_ROUTES:
            assert entry["routes"][route]["status"] == contracts.Route_Support_Status.UNVERIFIED
            assert entry["routes"][route]["contract"] is None
            assert entry["routes"][route]["allowlist"] == contracts.Allowlist_Result.UNVERIFIED
            assert entry["effort"][route]["status"] == contracts.Effort_Support_Status.UNVERIFIED
            assert entry["effort"][route]["contract"] is None
        for field in ("syncSupport", "asyncSupport", "streamingSupport"):
            assert entry[field] == contracts.Route_Support_Status.UNVERIFIED

        # 구조는 유효하지만(스키마 통과) 상태는 UNVERIFIED에 머문다.
        assert contracts.validate_entry(entry) == []
        # 실제 Verification_Record가 없으면 mock 참조는 무결성 검사에서 걸러진다.
        reasons = contracts.validate_entry(entry, known_evidence_ids=())
        assert reasons and all("EVIDENCE_INTEGRITY:evidence[" in r for r in reasons)
        assert contracts.is_malformed(entry, known_evidence_ids=()) is True


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-q"]))
