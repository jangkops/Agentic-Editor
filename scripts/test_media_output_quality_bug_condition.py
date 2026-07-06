"""Fix-checking / regression guard — spec: media-output-quality (bugfix), Task 1.

ORIGINALLY this file was authored as an INVERTED bug-condition exploration
test (assert the buggy behaviour currently holds → PASS on unfixed code,
FAIL on fixed code). On execution it surfaced that the production code in
``ai_engine/server.py`` ALREADY contains the fix for tasks 2–6:

  * ``_IMAGE_GEN_ATTEMPTS`` deque + ``_record_image_attempt`` helper  (Task 2)
  * short-circuit payload enriched with ``recentAttempts`` / ``actionable`` (Task 4)
  * signal-based ``_looks_structural`` (path / arrow / markdown-table)   (Task 5)
  * ``GET /api/debug/image-gen-status`` diagnostic route                 (Task 6)

Per the operator decision, this file is repurposed into a fix-CHECKING
regression guard: every case now asserts the CORRECT (fixed) behaviour, so it
PASSES on the current code and FAILS if the fix is ever regressed. Tasks 2–6
are treated as verify-only.

Cases:
  1. Diagnostic endpoint (Req 2.4): GET /api/debug/image-gen-status → 200 with
     the 5-key JSON {circuit, models, selectPreview, env, recentAttempts}.
  2. `_looks_structural` generic keywords (Req 2.2): no path/arrow/table signal
     → False, even with "프로젝트"/"구조"/"흐름도".
  3. `_looks_structural` real signals (Req 3.1): path OR arrow OR table → True.
  4. Short-circuit enrichment, denied (Req 2.3, 2.5): broken circuit + recorded
     access-denied attempts → payload carries non-empty `recentAttempts` and an
     `actionable` bilingual message naming the denied model ids.
  5. Short-circuit enrichment, no denied (design unit test): broken circuit +
     only non-access-denied attempts → `recentAttempts` present but `actionable`
     omitted (no noisy empty-ids message).

Run (hermetic — no network, gateway mocked):
  ./venv/bin/python -m pytest scripts/test_media_output_quality_bug_condition.py -p no:cacheprovider -q

_Requirements: 1.1, 1.2, 1.3, 1.4 (mapped to fixed behaviour 2.2, 2.3, 2.4, 2.5, 3.1)_
"""
from __future__ import annotations

import os
import sys
import json
import time
import asyncio
import tempfile
from unittest.mock import patch

# Make ai_engine importable from repo root.
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from fastapi.testclient import TestClient  # noqa: E402

from ai_engine.server import (  # noqa: E402
    app,
    _looks_structural,
    _tool_generate_image,
    _record_image_attempt,
    _IMAGE_GEN_CIRCUIT,
    _IMAGE_GEN_ATTEMPTS,
    IMAGE_MODELS,
)

_ACCESS_DENIED_REASON = (
    "AccessDeniedException: not authorized to perform execute-api:Invoke (HTTP 403)"
)


async def _async_none(*args, **kwargs):
    """Stand-in for _try_vertex_image_single — never returns a Vertex image."""
    return None


# --------------------------------------------------------------------------
# Case 1 — Diagnostic endpoint returns the full 5-key JSON (Req 2.4)
# --------------------------------------------------------------------------
def test_fix_diagnostic_endpoint_returns_full_json():
    client = TestClient(app)
    resp = client.get("/api/debug/image-gen-status")
    assert resp.status_code == 200, f"expected 200, got {resp.status_code}: {resp.text[:300]}"
    body = resp.json()

    for key in ("circuit", "models", "selectPreview", "env", "recentAttempts"):
        assert key in body, f"missing top-level key {key!r} in {sorted(body)}"

    circuit = body["circuit"]
    for ck in ("disabled_at", "ttl", "ttlRemainingSec", "isBroken"):
        assert ck in circuit, f"missing circuit.{ck} in {circuit}"
    assert isinstance(circuit["disabled_at"], (int, float))
    assert isinstance(circuit["ttl"], (int, float))
    assert isinstance(circuit["isBroken"], bool)

    assert isinstance(body["models"], list) and body["models"], "models must be a non-empty list"
    assert isinstance(body["selectPreview"], list)
    assert isinstance(body["recentAttempts"], list)

    env = body["env"]
    for ev in (
        "AE_IMAGE_PARALLEL_N",
        "AE_IMAGE_QUALITY_THRESHOLD",
        "AE_FORCE_NATIVE_DIAGRAM",
        "AE_DISABLE_HTML_SLIDES",
    ):
        assert ev in env, f"missing env.{ev} in {env}"


# --------------------------------------------------------------------------
# Case 2 — Generic keywords without signals → False (Req 2.2)
# --------------------------------------------------------------------------
def test_fix_looks_structural_generic_keywords_false():
    result = _looks_structural("프로젝트 구조 보고서", "흐름도", "이번 분기 변경")
    assert result is False, (
        f"generic keywords (no '/', no '->'/'→', no '|...|') must be False, got {result!r}"
    )
    # English generic keywords likewise must not trigger.
    assert _looks_structural("project architecture diagram", "overview", "") is False


# --------------------------------------------------------------------------
# Case 3 — Real structural signals → True (Req 3.1, preservation)
# --------------------------------------------------------------------------
def test_fix_looks_structural_signals_true():
    # path token
    assert _looks_structural("", "", "src/components/foo.js 를 수정") is True
    # arrow chains
    assert _looks_structural("", "", "A -> B -> C") is True
    assert _looks_structural("", "", "사용자 → 서버 → DB") is True
    assert _looks_structural("", "", "A ⇒ B") is True
    # markdown table row
    assert _looks_structural("", "", "| 항목 | 값 | 비고 |") is True


# --------------------------------------------------------------------------
# Case 4 — Broken-circuit payload is actionable when models were denied
#          (Req 2.3, 2.5)
# --------------------------------------------------------------------------
def test_fix_short_circuit_payload_is_actionable():
    denied_model_a = IMAGE_MODELS[0]
    denied_model_b = IMAGE_MODELS[-1]

    _IMAGE_GEN_ATTEMPTS.clear()
    _record_image_attempt(denied_model_a, "error", _ACCESS_DENIED_REASON, 12)
    _record_image_attempt(denied_model_b, "error", _ACCESS_DENIED_REASON, 15)
    _IMAGE_GEN_CIRCUIT["disabled_at"] = time.time()
    try:
        with tempfile.TemporaryDirectory() as tmp:
            with patch("ai_engine.server._try_vertex_image_single", new=_async_none):
                raw = asyncio.run(_tool_generate_image(
                    {"prompt": "프로젝트 아키텍처 다이어그램", "size": "1024x1024"},
                    project_path=tmp,
                ))
        payload = json.loads(raw)
    finally:
        _IMAGE_GEN_CIRCUIT["disabled_at"] = 0
        _IMAGE_GEN_ATTEMPTS.clear()

    assert "recentAttempts" in payload and payload["recentAttempts"], (
        f"expected non-empty recentAttempts, got: {payload!r}"
    )
    assert "actionable" in payload, f"expected 'actionable' key, got: {payload!r}"
    actionable = payload["actionable"]
    assert denied_model_a in actionable and denied_model_b in actionable, (
        f"actionable must name denied model ids {denied_model_a!r}/{denied_model_b!r}: {actionable!r}"
    )
    # bilingual — Korean + English halves both present.
    assert "권한 필요 모델" in actionable
    assert "admin must grant" in actionable


# --------------------------------------------------------------------------
# Case 5 — Broken-circuit payload omits actionable when nothing was denied
#          (design "Unit Tests": ids-empty actionable is noise)
# --------------------------------------------------------------------------
def test_fix_short_circuit_omits_actionable_when_not_denied():
    _IMAGE_GEN_ATTEMPTS.clear()
    # a non-access-denied failure (e.g. timeout) must NOT produce an actionable.
    _record_image_attempt(IMAGE_MODELS[0], "error", "read timeout after 60s", 60000)
    _IMAGE_GEN_CIRCUIT["disabled_at"] = time.time()
    try:
        with tempfile.TemporaryDirectory() as tmp:
            with patch("ai_engine.server._try_vertex_image_single", new=_async_none):
                raw = asyncio.run(_tool_generate_image(
                    {"prompt": "프로젝트 아키텍처 다이어그램", "size": "1024x1024"},
                    project_path=tmp,
                ))
        payload = json.loads(raw)
    finally:
        _IMAGE_GEN_CIRCUIT["disabled_at"] = 0
        _IMAGE_GEN_ATTEMPTS.clear()

    assert "recentAttempts" in payload, f"recentAttempts must always be present: {payload!r}"
    assert "actionable" not in payload, (
        f"actionable must be omitted when no access-denied ids exist: {payload!r}"
    )
