#!/usr/bin/env python3
"""Smoke harness for the agentic-editor backend (dev or frozen).

This harness verifies that a backend build boots and that one representative
call per feature path succeeds. It is designed to run identically against the
development server (``scripts/start_server.py``) and against the frozen
PyInstaller binary (``ai_engine_dist/ai-engine-server/...``).

Design references (see .kiro/specs/app-deployment-readiness/design.md):
- Components §1 Smoke_Harness (endpoint table)
- Data Models: SmokePathResult / SmokeVerdict
- Correctness Property 7: 스모크 판정 집계 불변

Usage:
    smoke_frozen_backend.py --target <dev|frozen> [--binary <path>] \
        [--base http://127.0.0.1:8765]

Behavior:
1. If ``--base`` is already reachable (GET /health), the harness attaches to
   the running backend and does NOT spawn a new process.
2. Otherwise it starts the target:
     - ``frozen``: spawns the binary given by ``--binary`` (or the default
       resource path).
     - ``dev``: spawns ``scripts/start_server.py`` via the current Python.
3. Polls ``GET /health`` for up to 30 seconds (Req 1.4).
4. Runs ONE representative call per feature path (Req 1.5), mapped to REAL
   endpoints verified on disk in ``ai_engine/server.py`` (via grep of
   ``@app.get``/``@app.post``/``@app.api_route``):

     | 경로            | 실 엔드포인트                                          |
     |-----------------|--------------------------------------------------------|
     | 부팅/모듈       | GET  /health (200 + status)                            |
     | LLM 채팅        | POST /api/models(creds 주입) -> POST /api/agents/run-stream |
     | PPTX            | POST /api/media/pptx-render (기존 .pptx 파싱)          |
     | 다이어그램      | POST /api/agents/run-stream (다이어그램 프롬프트)      |
     | 이미지 생성     | GET  /api/debug/image-gen-status                       |
     | 하이브리드 렌더 | GET  /api/debug/bridge (HTML->PNG 브리지 상태)         |

   Credential-requiring paths (LLM 채팅, 다이어그램) are marked "skipped"
   (with a reason) when no AWS credentials are present in the environment
   (Req 5.6). The PPTX path uses the creds-free ``/api/media/pptx-render``
   parser against an existing ``.pptx`` — this doubles as a check that
   ``python-pptx`` is bundled (Req 1.3); it is skipped when no sample exists.
   The image + hybrid diagnostic routes never require credentials.
5. ``evaluate_smoke`` (a PURE function) aggregates the per-path results into a
   verdict. On failure the process exits with a non-zero code and prints a
   readable report (Req 1.7).

Note: ``evaluate_smoke`` is intentionally import-safe and side-effect free so
that the property test (task 7.2) can import and exercise it directly. The
CLI entry point is guarded under ``if __name__ == "__main__"``.
"""

from __future__ import annotations

import argparse
import glob
import json
import os
import subprocess
import sys
import time
import urllib.error
import urllib.request

# ─────────────────────────────────────────────────────────────────────────────
# Constants
# ─────────────────────────────────────────────────────────────────────────────

PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
DEFAULT_BASE = "http://127.0.0.1:8765"
HEALTH_TIMEOUT_SECONDS = 30
HEALTH_POLL_INTERVAL_SECONDS = 1.0

# Default location of the frozen binary produced by PyInstaller (onedir).
DEFAULT_FROZEN_BINARY = os.path.join(
    PROJECT_ROOT, "ai_engine_dist", "ai-engine-server", "ai-engine-server"
)


# ─────────────────────────────────────────────────────────────────────────────
# PURE aggregation function — the hermetic core (Correctness Property 7)
# ─────────────────────────────────────────────────────────────────────────────


def evaluate_smoke(results: list[dict]) -> dict:
    """Aggregate per-path smoke results into a verdict. PURE function.

    Args:
        results: list of ``{"path": str, "ok": bool, "error": str | None}``.
            An entry may optionally carry ``"skipped": True`` (e.g. a
            credential-requiring path skipped because credentials are absent).
            Skipped entries do NOT affect the pass/fail verdict but ARE
            recorded in ``skipped_paths``.

    Returns:
        ``{"passed": bool, "failed_paths": [...], "skipped_paths": [...]}``
        where:
          - ``passed`` is ``True`` iff every result is ``ok`` OR ``skipped``
            (vacuously ``True`` for an empty list).
          - ``failed_paths`` lists the non-skipped failures, each as
            ``{"path", "error"}``.
          - ``skipped_paths`` lists the skipped entries, each as
            ``{"path", "error"}`` (``error`` holds the skip reason if present).

    This function performs no IO and has no side effects.
    """
    failed_paths: list[dict] = []
    skipped_paths: list[dict] = []

    for r in results:
        path = r.get("path")
        error = r.get("error")
        if r.get("skipped"):
            skipped_paths.append({"path": path, "error": error})
            continue
        if not bool(r.get("ok")):
            failed_paths.append({"path": path, "error": error})

    # passed == all(r is ok or skipped) == no non-skipped failures.
    passed = len(failed_paths) == 0
    return {
        "passed": passed,
        "failed_paths": failed_paths,
        "skipped_paths": skipped_paths,
    }


# ─────────────────────────────────────────────────────────────────────────────
# HTTP helpers (impure — used only at runtime, not by the property test)
# ─────────────────────────────────────────────────────────────────────────────


def _http_get(url: str, timeout: float = 10.0) -> tuple[int, str]:
    """GET a URL. Returns (status_code, body_text). Raises urllib errors."""
    req = urllib.request.Request(url, method="GET")
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        body = resp.read().decode("utf-8", errors="replace")
        return resp.getcode(), body


def _http_post(url: str, payload: dict, timeout: float = 30.0) -> tuple[int, str]:
    """POST JSON to a URL. Returns (status_code, body_text)."""
    data = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(
        url, data=data, method="POST", headers={"Content-Type": "application/json"}
    )
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        body = resp.read().decode("utf-8", errors="replace")
        return resp.getcode(), body


def _http_post_status(url: str, payload: dict, timeout: float = 30.0) -> int:
    """POST JSON and return only the status code WITHOUT draining the body.

    Used for SSE streaming routes (``/api/agents/run-stream``) so we confirm
    the stream opened (HTTP 200) without blocking on a full LLM response.
    """
    data = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(
        url, data=data, method="POST", headers={"Content-Type": "application/json"}
    )
    resp = urllib.request.urlopen(req, timeout=timeout)
    try:
        return resp.getcode()
    finally:
        resp.close()


def is_reachable(base: str) -> bool:
    """Return True if GET {base}/health responds with HTTP 200."""
    try:
        code, _ = _http_get(base.rstrip("/") + "/health", timeout=3.0)
        return code == 200
    except (urllib.error.URLError, OSError, ValueError):
        return False


def has_aws_credentials() -> bool:
    """Detect whether AWS credentials are present in the environment.

    The real credential path is runtime injection via ``/api/reset-cache`` /
    ``/api/models``; for smoke purposes we only need to know whether
    credential-requiring paths can be exercised at all.
    """
    return bool(os.environ.get("AWS_ACCESS_KEY_ID"))


def _env_creds_body() -> dict:
    """Build the credential injection body accepted by POST /api/models.

    Mirrors the env-var key shape the app injects at runtime. Never persisted.
    """
    return {
        "accessKeyId": os.environ.get("AWS_ACCESS_KEY_ID", ""),
        "secretAccessKey": os.environ.get("AWS_SECRET_ACCESS_KEY", ""),
        "sessionToken": os.environ.get("AWS_SESSION_TOKEN", ""),
        "region": (
            os.environ.get("AWS_DEFAULT_REGION")
            or os.environ.get("AWS_REGION")
            or "us-west-2"
        ),
    }


def _find_sample_pptx() -> str | None:
    """Locate an existing ``.pptx`` to exercise the pptx-render parser.

    Searches ``.generated/`` (the app write root) then the project root.
    """
    for root in (os.path.join(PROJECT_ROOT, ".generated"), PROJECT_ROOT):
        matches = sorted(glob.glob(os.path.join(root, "*.pptx")))
        if matches:
            return matches[0]
    return None


# ─────────────────────────────────────────────────────────────────────────────
# Target lifecycle
# ─────────────────────────────────────────────────────────────────────────────


def start_target(target: str, binary: str | None, base: str):
    """Spawn the backend for the given target. Returns a Popen or None.

    Returns ``None`` when the backend is already reachable at ``base`` (no new
    process is spawned in that case — the harness attaches instead).
    """
    if is_reachable(base):
        print(f"[smoke] backend already reachable at {base}; attaching (no spawn).")
        return None

    if target == "frozen":
        bin_path = binary or DEFAULT_FROZEN_BINARY
        if not os.path.isfile(bin_path):
            raise FileNotFoundError(f"frozen binary not found: {bin_path}")
        print(f"[smoke] spawning frozen binary: {bin_path}")
        return subprocess.Popen(
            [bin_path],
            cwd=PROJECT_ROOT,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
        )

    if target == "dev":
        start_script = os.path.join(PROJECT_ROOT, "scripts", "start_server.py")
        print(f"[smoke] spawning dev server: {start_script}")
        return subprocess.Popen(
            [sys.executable, start_script],
            cwd=PROJECT_ROOT,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
        )

    raise ValueError(f"unknown target: {target!r} (expected 'dev' or 'frozen')")


def wait_for_health(base: str, timeout: int = HEALTH_TIMEOUT_SECONDS) -> bool:
    """Poll GET {base}/health until 200 or timeout. Returns True on success."""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if is_reachable(base):
            return True
        time.sleep(HEALTH_POLL_INTERVAL_SECONDS)
    return False


# ─────────────────────────────────────────────────────────────────────────────
# Feature-path probes — each returns a SmokePathResult dict
# ─────────────────────────────────────────────────────────────────────────────


def _result(path: str, ok: bool, error: str | None = None, skipped: bool = False) -> dict:
    r = {"path": path, "ok": ok, "error": error}
    if skipped:
        r["skipped"] = True
    return r


def probe_boot(base: str) -> dict:
    """부팅/모듈 — GET /health returns 200 with a status field."""
    path = "부팅/모듈"
    try:
        code, body = _http_get(base.rstrip("/") + "/health", timeout=10.0)
        if code != 200:
            return _result(path, ok=False, error=f"HTTP {code}")
        data = json.loads(body)
        if data.get("status") != "ok":
            return _result(path, ok=False, error=f"status={data.get('status')!r}")
        return _result(path, ok=True)
    except Exception as e:  # noqa: BLE001
        return _result(path, ok=False, error=str(e))


def probe_llm_chat(base: str, creds: bool) -> dict:
    """LLM 채팅 — POST /api/models(creds 주입) 200 -> POST /api/agents/run-stream."""
    path = "LLM 채팅"
    if not creds:
        return _result(path, ok=False, error="AWS 자격증명 부재로 skip", skipped=True)
    try:
        # 1) Inject credentials + confirm model listing (POST /api/models).
        code, _ = _http_post(
            base.rstrip("/") + "/api/models", _env_creds_body(), timeout=30.0
        )
        if code != 200:
            return _result(path, ok=False, error=f"/api/models HTTP {code}")
        # 2) Open a short streaming completion (run-stream reads body["prompt"]).
        stream_code = _http_post_status(
            base.rstrip("/") + "/api/agents/run-stream",
            {"prompt": "ping"},
            timeout=60.0,
        )
        return _result(
            path,
            ok=(stream_code == 200),
            error=None if stream_code == 200 else f"run-stream HTTP {stream_code}",
        )
    except Exception as e:  # noqa: BLE001
        return _result(path, ok=False, error=str(e))


def probe_pptx(base: str, creds: bool) -> dict:
    """PPTX — POST /api/media/pptx-render against an existing .pptx (creds-free).

    Doubles as a python-pptx bundling check (Req 1.3): the route imports and
    parses with python-pptx, so a 200 with a ``slides`` array proves the
    dependency is present in the (frozen) build.
    """
    path = "PPTX"
    sample = _find_sample_pptx()
    if not sample:
        return _result(path, ok=False, error="샘플 .pptx 없음 — skip", skipped=True)
    try:
        code, body = _http_post(
            base.rstrip("/") + "/api/media/pptx-render",
            {"path": sample},
            timeout=60.0,
        )
        if code != 200:
            return _result(path, ok=False, error=f"HTTP {code}")
        data = json.loads(body)
        if "slides" not in data:
            return _result(path, ok=False, error="응답에 'slides' 없음")
        return _result(path, ok=True)
    except Exception as e:  # noqa: BLE001
        return _result(path, ok=False, error=str(e))


def probe_diagram(base: str, creds: bool) -> dict:
    """다이어그램 — POST /api/agents/run-stream with a diagram prompt (creds)."""
    path = "다이어그램"
    if not creds:
        return _result(path, ok=False, error="AWS 자격증명 부재로 skip", skipped=True)
    try:
        code = _http_post_status(
            base.rstrip("/") + "/api/agents/run-stream",
            {"prompt": "간단한 트리 다이어그램을 만들어줘"},
            timeout=60.0,
        )
        return _result(path, ok=(code == 200), error=None if code == 200 else f"HTTP {code}")
    except Exception as e:  # noqa: BLE001
        return _result(path, ok=False, error=str(e))


def probe_image(base: str, creds: bool) -> dict:
    """이미지 생성 — GET /api/debug/image-gen-status (routing state, creds-free)."""
    path = "이미지 생성"
    try:
        code, body = _http_get(
            base.rstrip("/") + "/api/debug/image-gen-status", timeout=10.0
        )
        if code != 200:
            return _result(path, ok=False, error=f"HTTP {code}")
        json.loads(body)  # response must be valid JSON
        return _result(path, ok=True)
    except Exception as e:  # noqa: BLE001
        return _result(path, ok=False, error=str(e))


def probe_hybrid_render(base: str, creds: bool) -> dict:
    """하이브리드 렌더 — GET /api/debug/bridge (HTML->PNG bridge state, creds-free)."""
    path = "하이브리드 렌더"
    try:
        code, body = _http_get(base.rstrip("/") + "/api/debug/bridge", timeout=10.0)
        if code != 200:
            return _result(path, ok=False, error=f"HTTP {code}")
        json.loads(body)
        return _result(path, ok=True)
    except Exception as e:  # noqa: BLE001
        return _result(path, ok=False, error=str(e))


def run_feature_probes(base: str) -> list[dict]:
    """Run one representative call per feature path. Returns SmokePathResults."""
    creds = has_aws_credentials()
    return [
        probe_boot(base),
        probe_llm_chat(base, creds),
        probe_pptx(base, creds),
        probe_diagram(base, creds),
        probe_image(base, creds),
        probe_hybrid_render(base, creds),
    ]


# ─────────────────────────────────────────────────────────────────────────────
# Report + CLI
# ─────────────────────────────────────────────────────────────────────────────


def format_report(verdict: dict, results: list[dict]) -> str:
    lines = ["", "=" * 60, "SMOKE REPORT", "=" * 60]
    for r in results:
        if r.get("skipped"):
            status = "SKIP"
        elif r.get("ok"):
            status = "PASS"
        else:
            status = "FAIL"
        suffix = f"  ({r['error']})" if r.get("error") else ""
        lines.append(f"  [{status}] {r['path']}{suffix}")
    lines.append("-" * 60)
    lines.append(f"  PASSED: {verdict['passed']}")
    if verdict["failed_paths"]:
        lines.append(f"  FAILED: {[f['path'] for f in verdict['failed_paths']]}")
    if verdict["skipped_paths"]:
        lines.append(f"  SKIPPED: {[s['path'] for s in verdict['skipped_paths']]}")
    lines.append("=" * 60)
    return "\n".join(lines)


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Smoke harness for the agentic-editor backend.")
    parser.add_argument("--target", choices=["dev", "frozen"], required=True, help="Which backend to start.")
    parser.add_argument("--binary", default=None, help="Path to the frozen backend binary (frozen target).")
    parser.add_argument("--base", default=DEFAULT_BASE, help=f"Backend base URL (default: {DEFAULT_BASE}).")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    proc = None
    try:
        proc = start_target(args.target, args.binary, args.base)
        if not wait_for_health(args.base):
            boot_fail = [{"path": "부팅/모듈", "ok": False, "error": "30초 내 /health 미응답"}]
            verdict = evaluate_smoke(boot_fail)
            print(format_report(verdict, boot_fail))
            return 1

        results = run_feature_probes(args.base)
        verdict = evaluate_smoke(results)
        print(format_report(verdict, results))
        return 0 if verdict["passed"] else 2
    finally:
        if proc is not None:
            proc.terminate()
            try:
                proc.wait(timeout=10)
            except subprocess.TimeoutExpired:
                proc.kill()


if __name__ == "__main__":
    sys.exit(main())
