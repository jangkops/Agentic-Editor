#!/usr/bin/env python3
"""Thread aws_profile/bedrock_user from the request context all the way down to
multimedia tool functions (_tool_generate_image, _tool_edit_image, _tool_generate_pptx).

Why: BEDROCK_USER env var is unset on the dev server, so when a chat request
specifies bedrockUser=cgjang in the body, the body value is honored at the top
of the request handler (gw is built with cgjang creds for chat) but the
multimedia tools each construct their own GatewayClient via os.environ. They
fall back to '' for bedrock_user, which means the gateway gets raw SSO creds
and replies with 'unable to determine principal identity'.

Fix: 
  1. Add aws_profile / bedrock_user keyword args to the three async tool
     coroutines.
  2. Have _execute_tool accept and forward those args, defaulting to the
     existing env-var lookups for backward compatibility.
  3. Update both call sites (run-agent stream and parallel) to pass
     aws_profile / bedrock_user into _execute_tool.

Idempotent: re-running detects already-patched markers and skips.
"""
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SERVER = ROOT / "ai_engine" / "server.py"

text = SERVER.read_text(encoding="utf-8")
original = text

MARKER = "# [patched-credentials]"  # idempotency marker we sprinkle in

if MARKER in text:
    print("server.py already patched — nothing to do")
    sys.exit(0)

# ---------------------------------------------------------------------------
# 1) Extend tool function signatures.
# ---------------------------------------------------------------------------

def patch_signature(src: str, fn_name: str, ext: str = "_image") -> str:
    """Insert aws_profile/bedrock_user kw-args into an async def signature."""
    old = f"async def {fn_name}(tool_input: dict, project_path: str) -> str:"
    new = (
        f"async def {fn_name}(tool_input: dict, project_path: str, "
        f"aws_profile: str = '', bedrock_user: str = '') -> str:  {MARKER}"
    )
    if old not in src:
        raise SystemExit(f"could not find signature for {fn_name}")
    return src.replace(old, new, 1)

text = patch_signature(text, "_tool_generate_image")
text = patch_signature(text, "_tool_edit_image")
text = patch_signature(text, "_tool_generate_pptx")

# ---------------------------------------------------------------------------
# 2) Replace inline env-var lookups inside those three functions to prefer
#    the explicit kw-arg, falling back to env. Two sites:
#      - _tool_generate_image  (exact 3-line block once)
#      - _tool_edit_image      (exact 3-line block once)
# We do the replace with a block-scope guard so we don't accidentally mutate
# unrelated occurrences.
# ---------------------------------------------------------------------------

old_block = (
    "    aws_profile = os.environ.get(\"AWS_PROFILE\", \"bedrock-gw\")\n"
    "    bedrock_user = os.environ.get(\"BEDROCK_USER\", \"\")\n"
    "    gw = _get_gw(aws_profile, bedrock_user)\n"
)
new_block = (
    "    # [patched-credentials] honor explicit kw-args, fall back to env\n"
    "    aws_profile = aws_profile or os.environ.get(\"AWS_PROFILE\", \"bedrock-gw\")\n"
    "    bedrock_user = bedrock_user or os.environ.get(\"BEDROCK_USER\", \"\")\n"
    "    gw = _get_gw(aws_profile, bedrock_user)\n"
)
count = text.count(old_block)
print(f"env-fallback blocks found: {count}")
if count == 0:
    raise SystemExit("expected at least one env-fallback block")
text = text.replace(old_block, new_block)

# ---------------------------------------------------------------------------
# 3) _tool_generate_pptx may call _tool_generate_image internally for slide
#    images. Forward credentials there too.
# ---------------------------------------------------------------------------

# Locate calls of _tool_generate_image inside the pptx body. We forward
# aws_profile/bedrock_user explicitly when the call is positional.
pptx_call_pattern = re.compile(
    r"await _tool_generate_image\(([^)]*?)\)",
    re.DOTALL,
)

def fix_pptx_call(m: re.Match) -> str:
    args = m.group(1).strip()
    # Strip trailing comma to keep the rendered call valid Python.
    args = args.rstrip(",").strip()
    # Skip if already passing aws_profile=
    if "aws_profile" in args:
        return m.group(0)
    return (
        f"await _tool_generate_image({args}, "
        f"aws_profile=aws_profile, bedrock_user=bedrock_user)  {MARKER}"
    )

# Bound the search to inside _tool_generate_pptx body. Find function start.
pptx_start = text.find("async def _tool_generate_pptx(")
if pptx_start == -1:
    raise SystemExit("could not locate _tool_generate_pptx")
# Find next top-level async def OR def to bound the body.
next_fn = re.search(r"\n(?:async def |def )", text[pptx_start + 30:])
pptx_end = pptx_start + 30 + (next_fn.start() if next_fn else len(text))
pptx_body = text[pptx_start:pptx_end]
new_pptx_body = pptx_call_pattern.sub(fix_pptx_call, pptx_body)
if new_pptx_body != pptx_body:
    text = text[:pptx_start] + new_pptx_body + text[pptx_end:]
    print("forwarded credentials to _tool_generate_image inside pptx body")
else:
    print("(no internal _tool_generate_image call found in pptx; ok)")

# ---------------------------------------------------------------------------
# 4) Extend _execute_tool to accept and forward credentials.
# ---------------------------------------------------------------------------

et_old_sig = (
    "def _execute_tool(tool_name: str, tool_input: dict, project_path: str = \"\""
)
et_new_sig = (
    "def _execute_tool(tool_name: str, tool_input: dict, project_path: str = \"\", "
    "aws_profile: str = \"\", bedrock_user: str = \"\""  # [patched-credentials]
)
if et_old_sig not in text:
    raise SystemExit("could not find _execute_tool signature")
text = text.replace(et_old_sig, et_new_sig, 1)

# Forward the args at the multimedia routing branches inside _execute_tool.
# The three async branches use _asyncio.run(_tool_xxx(tool_input, project_path)).
# We want them to pass aws_profile/bedrock_user too.
multimedia_old = (
    "            if tool_name == \"generate_image\":\n"
    "                return _asyncio.run(_tool_generate_image(tool_input, project_path))\n"
    "            if tool_name == \"generate_pdf\":\n"
    "                return _asyncio.run(_tool_generate_pdf(tool_input, project_path))\n"
    "            if tool_name == \"generate_pptx\":\n"
    "                return _asyncio.run(_tool_generate_pptx(tool_input, project_path))\n"
    "            if tool_name == \"edit_image\":\n"
    "                return _asyncio.run(_tool_edit_image(tool_input, project_path))\n"
)
multimedia_new = (
    "            if tool_name == \"generate_image\":\n"
    "                return _asyncio.run(_tool_generate_image(tool_input, project_path, aws_profile=aws_profile, bedrock_user=bedrock_user))\n"
    "            if tool_name == \"generate_pdf\":\n"
    "                return _asyncio.run(_tool_generate_pdf(tool_input, project_path))\n"
    "            if tool_name == \"generate_pptx\":\n"
    "                return _asyncio.run(_tool_generate_pptx(tool_input, project_path, aws_profile=aws_profile, bedrock_user=bedrock_user))\n"
    "            if tool_name == \"edit_image\":\n"
    "                return _asyncio.run(_tool_edit_image(tool_input, project_path, aws_profile=aws_profile, bedrock_user=bedrock_user))\n"
)
if multimedia_old not in text:
    # Fall-back: try the simpler block style we know exists
    print("WARN: multimedia routing block changed shape; manual review needed")
else:
    text = text.replace(multimedia_old, multimedia_new, 1)

# Also forward credentials at the pre-multimedia dispatch line (single-line
# variants). Replace any standalone calls in _execute_tool body where we run a
# multimedia tool without forwarding.
solo_patterns = [
    (
        "return asyncio.run(_tool_generate_image(tool_input, project_path))",
        "return asyncio.run(_tool_generate_image(tool_input, project_path, aws_profile=aws_profile, bedrock_user=bedrock_user))",
    ),
    (
        "return asyncio.run(_tool_generate_pptx(tool_input, project_path))",
        "return asyncio.run(_tool_generate_pptx(tool_input, project_path, aws_profile=aws_profile, bedrock_user=bedrock_user))",
    ),
    (
        "return asyncio.run(_tool_edit_image(tool_input, project_path))",
        "return asyncio.run(_tool_edit_image(tool_input, project_path, aws_profile=aws_profile, bedrock_user=bedrock_user))",
    ),
]
for old, new in solo_patterns:
    if old in text:
        text = text.replace(old, new)
        print(f"replaced solo dispatch: {old[:60]}…")

# ---------------------------------------------------------------------------
# 5) Update the two call sites of _execute_tool inside the request handlers
#    so they pass the request-scoped aws_profile / bedrock_user along.
# ---------------------------------------------------------------------------

callsite_a = (
    "tool_output = await asyncio.to_thread(_execute_tool, tool_name, tool_input, project_path)"
)
callsite_a_new = (
    "tool_output = await asyncio.to_thread(_execute_tool, tool_name, tool_input, project_path, aws_profile, bedrock_user)  # [patched-credentials]"
)
if callsite_a not in text:
    raise SystemExit("could not locate run-agent _execute_tool call site")
text = text.replace(callsite_a, callsite_a_new, 1)

callsite_b = (
    "tout = await asyncio.to_thread(_execute_tool, tname, tinput, project_path)"
)
callsite_b_new = (
    "tout = await asyncio.to_thread(_execute_tool, tname, tinput, project_path, aws_profile, bedrock_user)  # [patched-credentials]"
)
if callsite_b not in text:
    raise SystemExit("could not locate parallel _execute_tool call site")
text = text.replace(callsite_b, callsite_b_new, 1)

# ---------------------------------------------------------------------------
# Persist + sanity-check.
# ---------------------------------------------------------------------------

if text == original:
    print("no changes (nothing matched)")
    sys.exit(1)

import ast
ast.parse(text)
SERVER.write_text(text, encoding="utf-8")
diff_bytes = len(text) - len(original)
print(f"wrote {SERVER} ({'+' if diff_bytes >= 0 else ''}{diff_bytes} bytes), syntax OK")
