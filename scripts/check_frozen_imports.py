#!/usr/bin/env python3
"""Pre-build import gate for release-critical Python modules.

This script guards the PyInstaller freeze step in the release pipeline
(see ``.github/workflows/release.yml`` and ``ai-engine-server.spec``).

Rationale (see .kiro/specs/app-deployment-readiness/design.md §1
Build_Pipeline + Frozen_Backend):
- The spec's ``_THIRD_PARTY`` list is collected with PyInstaller's
  ``collect_all``, which SILENTLY skips packages that are not installed
  (``except Exception: pass``). A missing release-critical dependency would
  therefore produce a frozen build that boots but breaks at runtime when the
  corresponding feature (diagrams/charts, scientific compute, agent graph,
  PPTX generation) is exercised.
- To make that failure loud and cheap, this script imports the four
  RELEASE-CRITICAL modules (Req 1.3) in the SAME interpreter that will be used
  for the freeze, BEFORE running the expensive PyInstaller build (Req 1.6).

Behavior:
- Exit code 0 when all four modules import successfully (prints an OK line).
- Exit code 1 (non-zero) when one or more modules are missing, printing a
  clear message naming each missing module so the CI log pinpoints the cause.

The inline ``python -c`` form is intentionally avoided in the workflow; this
committed script is invoked instead so the check is versioned and testable
locally via ``./venv/bin/python scripts/check_frozen_imports.py``.
"""

from __future__ import annotations

import importlib
import sys

# RELEASE-CRITICAL modules (Req 1.3). Keep in sync with the RELEASE-CRITICAL
# entries annotated in ``ai-engine-server.spec`` (_THIRD_PARTY).
REQUIRED_MODULES = ("matplotlib", "scipy", "langgraph", "pptx")

# OPTIONAL modules — nice-to-have for enhanced features but NOT build-blocking.
# fastembed/onnxruntime power the multilingual neural RAG embedding; when absent,
# the retriever safely falls back to TF-IDF (see embedder.get_embedding_provider).
# We report their presence for CI visibility but never fail the build on them.
OPTIONAL_MODULES = ("fastembed", "onnxruntime")


def check_modules(module_names):
    """Attempt to import each module name.

    Returns a tuple ``(present, missing)`` where each element is a list of
    module names. Pure with respect to its inputs aside from the imports it
    triggers, so it is safe to import and exercise from a test.
    """
    present = []
    missing = []
    for name in module_names:
        try:
            importlib.import_module(name)
            present.append(name)
        except Exception as exc:  # ImportError and any import-time failure
            missing.append((name, str(exc)))
    return present, missing


def main():
    present, missing = check_modules(REQUIRED_MODULES)

    # Optional modules — soft report only (never affects exit code).
    opt_present, opt_missing = check_modules(OPTIONAL_MODULES)
    if opt_present:
        print("[import-gate] optional present: " + ", ".join(opt_present))
    for name, reason in opt_missing:
        print(f"[import-gate] optional missing (TF-IDF fallback active): {name} ({reason})")

    if present:
        print("[import-gate] present: " + ", ".join(present))

    if missing:
        for name, reason in missing:
            print(f"[import-gate] MISSING module: {name} ({reason})")
        names = ", ".join(name for name, _ in missing)
        print(
            "[import-gate] FAIL — release-critical module(s) not importable: "
            f"{names}"
        )
        return 1

    print(
        "[import-gate] OK — all release-critical modules importable: "
        + ", ".join(REQUIRED_MODULES)
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
