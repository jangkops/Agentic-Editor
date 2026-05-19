#!/usr/bin/env python3
"""Add a user-friendly hint to image-generation failure responses when the
underlying error is a gateway/IAM policy block. Idempotent."""
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SERVER = ROOT / "ai_engine" / "server.py"
text = SERVER.read_text(encoding="utf-8")

MARKER = "# [hint-image-route]"
if MARKER in text:
    print("already patched")
    raise SystemExit(0)

old = (
    '    # Req 1.2: cap final error detail at 200 chars total\n'
    '    detail = (last_error or "all image models failed")[:200]\n'
    '    return json.dumps({"error": "model-unavailable", "detail": detail})\n'
)
new = (
    '    # Req 1.2: cap final error detail at 200 chars total\n'
    '    detail = (last_error or "all image models failed")[:200]\n'
    '    # [hint-image-route] 게이트웨이 라우트/IAM 차단 시 사용자에게 명확한 안내 제공\n'
    '    hint = ""\n'
    '    if any(t in detail for t in ("execute-api:Invoke", "principal identity", "HTTP 403", "HTTP 404")):\n'
    '        hint = "현재 게이트웨이가 이미지 생성 라우트(/invoke-model)를 지원하지 않습니다. 관리자에게 활성화를 요청하세요."\n'
    '    payload = {"error": "model-unavailable", "detail": detail}\n'
    '    if hint:\n'
    '        payload["hint"] = hint\n'
    '    return json.dumps(payload)\n'
)

if old not in text:
    raise SystemExit("expected block not found — server.py shape changed")

text = text.replace(old, new, 1)

# Same hint logic for inpaint and outpaint final returns.
inpaint_old = (
    '        return json.dumps({"error": "model-unavailable", "detail": last_error or "all inpaint models failed"})\n'
)
inpaint_new = (
    '        # [hint-image-route]\n'
    '        _det = (last_error or "all inpaint models failed")[:200]\n'
    '        _payload = {"error": "model-unavailable", "detail": _det}\n'
    '        if any(t in _det for t in ("execute-api:Invoke", "principal identity", "HTTP 403", "HTTP 404")):\n'
    '            _payload["hint"] = "현재 게이트웨이가 이미지 편집(inpaint) 라우트(/invoke-model)를 지원하지 않습니다. 관리자에게 활성화를 요청하세요."\n'
    '        return json.dumps(_payload)\n'
)
if inpaint_old in text:
    text = text.replace(inpaint_old, inpaint_new, 1)
    print("inpaint patched")

outpaint_old = (
    '    return json.dumps({"error": "model-unavailable", "detail": last_error or "all outpaint models failed"})\n'
)
outpaint_new = (
    '    # [hint-image-route]\n'
    '    _det = (last_error or "all outpaint models failed")[:200]\n'
    '    _payload = {"error": "model-unavailable", "detail": _det}\n'
    '    if any(t in _det for t in ("execute-api:Invoke", "principal identity", "HTTP 403", "HTTP 404")):\n'
    '        _payload["hint"] = "현재 게이트웨이가 이미지 편집(outpaint) 라우트(/invoke-model)를 지원하지 않습니다. 관리자에게 활성화를 요청하세요."\n'
    '    return json.dumps(_payload)\n'
)
if outpaint_old in text:
    text = text.replace(outpaint_old, outpaint_new, 1)
    print("outpaint patched")

import ast
ast.parse(text)
SERVER.write_text(text, encoding="utf-8")
print("done")
