#!/usr/bin/env python3
"""Fix _extract_invoke_usage for Cohere Embed models which return
meta.billed_units.input_tokens instead of inputTextTokenCount."""
import ast
from pathlib import Path

SRC = Path("/tmp/lambda-patch/handler.py.patched")
text = SRC.read_text(encoding="utf-8")

OLD = '''    if "embed" in raw:
        if isinstance(response_body, dict):
            n = response_body.get("inputTextTokenCount")
            if isinstance(n, (int, float)):
                usage["input_tokens"] = int(n)
                return usage
        raise ValueError("embedding response missing inputTextTokenCount")'''

NEW = '''    if "embed" in raw:
        if isinstance(response_body, dict):
            # Titan Embed: inputTextTokenCount at top level
            n = response_body.get("inputTextTokenCount")
            if isinstance(n, (int, float)):
                usage["input_tokens"] = int(n)
                return usage
            # Cohere Embed: meta.billed_units.input_tokens
            meta = response_body.get("meta", {})
            bu = meta.get("billed_units", {}) if isinstance(meta, dict) else {}
            cn = bu.get("input_tokens")
            if isinstance(cn, (int, float)):
                usage["input_tokens"] = int(cn)
                return usage
            # Fallback: count texts in request × avg 256 tokens (rough estimate)
            # But fail-closed is safer — refuse
        raise ValueError("embedding response missing token count (inputTextTokenCount or meta.billed_units.input_tokens)")'''

if OLD not in text:
    raise SystemExit("embed block not found")

text = text.replace(OLD, NEW, 1)
ast.parse(text)
SRC.write_text(text, encoding="utf-8")
print("Embed usage extraction patched")
