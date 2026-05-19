#!/usr/bin/env python3
"""Patch ai_engine/server.py to resolve model IDs via _resolve_callable_model_id
before each gw.invoke_model() call inside _tool_generate_image and
_tool_edit_image (inpaint + outpaint).

This is idempotent - safe to run multiple times.
"""
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SERVER = ROOT / "ai_engine" / "server.py"

text = SERVER.read_text(encoding="utf-8")
original = text

# Pattern: any indent + result = await gw.invoke_model(model_id, body, timeout=60)
# Replace with: same indent + callable_id = _resolve... + result = await gw.invoke_model(callable_id, body, timeout=60)
pattern = re.compile(
    r"^(?P<indent>[ \t]+)result = await gw\.invoke_model\(model_id, body, timeout=60\)\n",
    re.MULTILINE,
)

def replace(m: re.Match) -> str:
    indent = m.group("indent")
    return (
        f"{indent}callable_id = _resolve_callable_model_id(model_id, aws_profile, bedrock_user)\n"
        f"{indent}result = await gw.invoke_model(callable_id, body, timeout=60)\n"
    )

# Skip if already patched (callable_id already in scope right before invoke_model)
def is_already_patched(snippet: str) -> bool:
    return "callable_id = _resolve_callable_model_id" in snippet

# We'll do a guarded replace: only when the line immediately above is NOT a callable_id assignment.
matches = list(pattern.finditer(text))
print(f"Found {len(matches)} invoke_model call sites")

# Reverse iteration so offsets remain valid
new_text = text
for m in reversed(matches):
    start = m.start()
    # look back ~120 chars for prior line
    look_start = max(0, start - 120)
    prior = new_text[look_start:start]
    if "callable_id = _resolve_callable_model_id(model_id" in prior:
        print(f"  skip already-patched at offset {start}")
        continue
    new_text = new_text[:start] + replace(m) + new_text[m.end():]
    print(f"  patched at offset {start} (indent {len(m.group('indent'))} chars)")

if new_text == original:
    print("No changes made (already patched or no matches)")
else:
    SERVER.write_text(new_text, encoding="utf-8")
    print(f"Wrote {SERVER} (+{len(new_text)-len(original)} bytes)")

# Sanity check: file still parses
import ast
ast.parse(new_text)
print("Syntax OK")
