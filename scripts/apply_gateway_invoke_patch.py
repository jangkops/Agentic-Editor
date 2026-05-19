#!/usr/bin/env python3
"""Apply /invoke route patch to Gateway Lambda handler.py.

Reads:  /tmp/lambda-patch/handler.py.new (original copy)
Writes: /tmp/lambda-patch/handler.py.patched

Idempotent: checks for marker before patching.
"""
import ast
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))

from gateway_invoke_patch_code import INVOKE_FUNCTIONS, ROUTER_BLOCK

SRC = Path("/tmp/lambda-patch/handler.py.new")
OUT = Path("/tmp/lambda-patch/handler.py.patched")

text = SRC.read_text(encoding="utf-8")

MARKER = "[INVOKE-ROUTE-v1]"
if MARKER in text:
    print("already patched — skipping")
    OUT.write_text(text, encoding="utf-8")
    sys.exit(0)

# 1) Insert functions before def lambda_handler
ANCHOR_FN = "def lambda_handler(event: dict, context) -> dict:"
if ANCHOR_FN not in text:
    raise SystemExit("ERROR: could not find lambda_handler anchor")

text = text.replace(ANCHOR_FN, INVOKE_FUNCTIONS + "\n\n" + ANCHOR_FN, 1)

# 2) Insert router branch before "# --- Inference pipeline"
ANCHOR_ROUTE = "    # --- Inference pipeline (POST /converse or root) ---\n"
if ANCHOR_ROUTE not in text:
    raise SystemExit("ERROR: could not find router anchor")

text = text.replace(ANCHOR_ROUTE, ROUTER_BLOCK + ANCHOR_ROUTE, 1)

# 3) Syntax check
try:
    ast.parse(text)
except SyntaxError as e:
    raise SystemExit(f"SYNTAX ERROR in patched handler: {e}")

OUT.write_text(text, encoding="utf-8")
print(f"Patched handler written to {OUT}")
print(f"  Original: {SRC.stat().st_size} bytes")
print(f"  Patched:  {OUT.stat().st_size} bytes (+{OUT.stat().st_size - SRC.stat().st_size})")
