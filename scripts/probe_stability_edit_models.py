#!/usr/bin/env python3
"""Probe Stability AI editing models via /invoke gateway with a real
512×512 base image. Confirms each model is callable through the gateway.
"""
import asyncio, base64, io, json, os, sys, time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from ai_engine.gateway_module import GatewayClient

PROFILE = os.environ.get("AWS_PROFILE", "bedrock-gw")
USER = os.environ.get("BEDROCK_USER", "cgjang")


def make_base_image_b64():
    """Generate a 512×512 white PNG with Pillow."""
    from PIL import Image
    img = Image.new("RGB", (512, 512), color=(220, 220, 230))
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return base64.b64encode(buf.getvalue()).decode("ascii")


# Body templates for each Stability edit model (per AWS docs)
def body_for_edit_model(model_id, base_b64):
    raw = model_id.split(".", 1)[1]  # remove "stability." prefix
    common = {"output_format": "png"}
    if raw.startswith("stable-image-inpaint"):
        # Inpaint requires mask but mask=image with alpha works for ping
        return {**common, "image": base_b64, "prompt": "blue circle"}
    if raw.startswith("stable-outpaint"):
        return {**common, "image": base_b64, "prompt": "extend right", "left": 64, "right": 64}
    if raw.startswith("stable-image-erase"):
        return {**common, "image": base_b64, "mode": "search", "search_prompt": "object"}
    if raw.startswith("stable-image-search-replace"):
        return {**common, "image": base_b64, "search_prompt": "blue", "prompt": "red"}
    if raw.startswith("stable-image-search-recolor"):
        return {**common, "image": base_b64, "select_prompt": "blue", "prompt": "red"}
    if raw.startswith("stable-image-control-sketch"):
        return {**common, "image": base_b64, "prompt": "abstract art", "control_strength": 0.5}
    if raw.startswith("stable-image-control-structure"):
        return {**common, "image": base_b64, "prompt": "abstract art", "control_strength": 0.5}
    if raw.startswith("stable-image-style-guide"):
        return {**common, "image": base_b64, "prompt": "abstract art"}
    if raw.startswith("stable-style-transfer"):
        return {**common, "init_image": base_b64, "style_image": base_b64, "prompt": "abstract"}
    if raw.startswith("stable-creative-upscale") or raw.startswith("stable-conservative-upscale"):
        return {**common, "image": base_b64, "prompt": "high quality"}
    if raw.startswith("stable-fast-upscale"):
        return {**common, "image": base_b64}
    if raw.startswith("stable-image-remove-background"):
        return {**common, "image": base_b64}
    return None


EDIT_MODELS = [
    "stability.stable-image-inpaint-v1:0",
    "stability.stable-outpaint-v1:0",
    "stability.stable-image-erase-object-v1:0",
    "stability.stable-image-search-replace-v1:0",
    "stability.stable-image-search-recolor-v1:0",
    "stability.stable-image-control-sketch-v1:0",
    "stability.stable-image-control-structure-v1:0",
    "stability.stable-image-style-guide-v1:0",
    "stability.stable-style-transfer-v1:0",
    "stability.stable-creative-upscale-v1:0",
    "stability.stable-conservative-upscale-v1:0",
    "stability.stable-fast-upscale-v1:0",
    "stability.stable-image-remove-background-v1:0",
]


async def main():
    gw = GatewayClient(aws_profile=PROFILE, bedrock_user=USER)
    base_b64 = make_base_image_b64()
    print(f"Base image: 512×512 PNG, {len(base_b64)} chars b64")
    print("=" * 70)

    results = []
    for mid in EDIT_MODELS:
        body = body_for_edit_model(mid, base_b64)
        if body is None:
            print(f"  [SKIP] {mid} — no body template")
            continue
        # Stability edit models are INFERENCE_PROFILE only — need us. prefix
        callable_id = f"us.{mid}" if not mid.startswith("us.") else mid
        t0 = time.time()
        try:
            r = await gw.invoke_model(callable_id, body, timeout=60)
            dt = round((time.time() - t0) * 1000)
            if "error" in r:
                print(f"  [FAIL] {mid:55s} {dt:5d}ms  {str(r['error'])[:120]}")
                results.append((mid, False, r["error"][:200]))
            else:
                imgs = r.get("images") or r.get("artifacts") or []
                if imgs:
                    print(f"  [OK]   {mid:55s} {dt:5d}ms  {len(imgs)} img(s)")
                    results.append((mid, True, None))
                else:
                    print(f"  [OK?]  {mid:55s} {dt:5d}ms  keys={list(r.keys())[:5]}")
                    results.append((mid, True, None))
        except Exception as e:
            print(f"  [ERR]  {mid:55s} {str(e)[:120]}")
            results.append((mid, False, str(e)[:200]))

    print("=" * 70)
    ok = sum(1 for _, o, _ in results if o)
    print(f"OK: {ok}/{len(results)}")


asyncio.run(main())
