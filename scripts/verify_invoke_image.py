#!/usr/bin/env python3
"""End-to-end verification: generate an image via the /invoke gateway route.

Tests the full chain:
  Client (SigV4) → API Gateway → Lambda /invoke → bedrock:InvokeModel → SD3.5
  → response → usage extraction → cost calculation → MonthlyUsage update

Requires active SSO session.
"""
import asyncio
import json
import os
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from ai_engine.gateway_module import GatewayClient

PROFILE = os.environ.get("AWS_PROFILE", "bedrock-gw")
USER = os.environ.get("BEDROCK_USER", "cgjang")


async def main():
    gw = GatewayClient(aws_profile=PROFILE, bedrock_user=USER)

    print("=" * 60)
    print("TEST: Image generation via /invoke gateway route")
    print("=" * 60)

    body = {
        "prompt": "a tiny blue robot mascot, isometric pixel art, white background",
        "mode": "text-to-image",
        "output_format": "png",
        "aspect_ratio": "1:1",
    }

    print(f"  Model: stability.sd3-5-large-v1:0")
    print(f"  Prompt: {body['prompt']}")
    print(f"  Calling /invoke...")

    t0 = time.time()
    result = await gw.invoke_model("stability.sd3-5-large-v1:0", body, timeout=30)
    dt = round((time.time() - t0) * 1000)

    if "error" in result:
        print(f"\n  [FAIL] {dt}ms — {result['error'][:300]}")
        sys.exit(1)

    # Gateway wraps response in {"decision":"ALLOW","output":{...},"usage":{...},"cost_krw":...}
    # But GatewayClient.invoke_model extracts images from response body directly.
    if "images" in result:
        imgs = result["images"]
        print(f"\n  [SUCCESS] {dt}ms — {len(imgs)} image(s)")
        print(f"  Base64 length: {len(imgs[0])} chars")

        # Save to .generated/
        import base64, hashlib
        gen_dir = ROOT / ".generated"
        gen_dir.mkdir(exist_ok=True)
        ts = str(int(time.time() * 1000))
        filename = f"invoke-test-{ts}.png"
        img_bytes = base64.b64decode(imgs[0])
        (gen_dir / filename).write_bytes(img_bytes)
        print(f"  Saved: .generated/{filename} ({len(img_bytes)} bytes)")
    elif "output" in result and isinstance(result["output"], dict):
        out = result["output"]
        if "images" in out:
            imgs = out["images"]
            print(f"\n  [SUCCESS] {dt}ms — {len(imgs)} image(s) (wrapped)")
            import base64
            gen_dir = ROOT / ".generated"
            gen_dir.mkdir(exist_ok=True)
            ts = str(int(time.time() * 1000))
            filename = f"invoke-test-{ts}.png"
            img_bytes = base64.b64decode(imgs[0])
            (gen_dir / filename).write_bytes(img_bytes)
            print(f"  Saved: .generated/{filename} ({len(img_bytes)} bytes)")
        else:
            print(f"\n  [UNEXPECTED] {dt}ms — output keys: {list(out.keys())[:10]}")
    else:
        print(f"\n  [UNEXPECTED] {dt}ms — result keys: {list(result.keys())[:10]}")
        print(f"  Snippet: {str(result)[:200]}")

    # Check cost_krw if present
    if "cost_krw" in result:
        print(f"  Cost: {result['cost_krw']} KRW")
    if "usage" in result:
        print(f"  Usage: {result['usage']}")


asyncio.run(main())
