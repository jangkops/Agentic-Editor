#!/usr/bin/env python3
"""Sanity-check that _resolve_callable_model_id has the expected shape and
covers ON_DEMAND / INFERENCE_PROFILE branching after recent patches.
"""
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SERVER = ROOT / "ai_engine" / "server.py"
text = SERVER.read_text(encoding="utf-8")

needle = (
    'def _resolve_callable_model_id(model_id, aws_profile, bedrock_user):\n'
    '    """모델 ID를 실제 Bedrock 호출 가능한 형태로 변환.\n'
    '    - ON_DEMAND only → prefix 제거 (prefix가 붙어있으면 떼어냄)\n'
)
print("Function header found:", needle in text)
print("Has has_on_demand branch:", "has_on_demand and not has_inference_profile" in text)
print("Has has_inference_profile branch:", "has_inference_profile and not has_on_demand" in text)
print("Falls back to us. prefix:", "f\"us.{raw_id}\"" in text)

# Count call sites that go through the resolver before invoke_model
import re
m = re.findall(
    r"callable_id = _resolve_callable_model_id\(model_id, aws_profile, bedrock_user\)\n\s+result = await gw\.invoke_model\(callable_id,",
    text,
)
print(f"resolver-then-invoke call sites: {len(m)} (expected: 3)")
