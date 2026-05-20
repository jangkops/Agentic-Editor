#!/usr/bin/env python3
"""Probe IMAGE-output models through gateway.invoke_model — same path used by
_tool_generate_image / _tool_edit_image. Captures principal/IAM blocks too.
"""
import asyncio, json, os, sys, time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from ai_engine.gateway_module import GatewayClient

PROFILE = os.environ.get("AWS_PROFILE", "bedrock-gw")
USER = os.environ.get("BEDROCK_USER", "cgjang")
REGION = os.environ.get("AWS_REGION", "us-west-2")

catalog = json.loads(Path("/tmp/aws-models.json").read_text())
imgs = [m for m in catalog if "IMAGE" in m.get("outputModalities", [])]
print(f"IMAGE models to probe: {len(imgs)}")


def cid(model_id, types):
    if any(model_id.startswith(p) for p in ("us.", "eu.", "global.")):
        return model_id
    if "INFERENCE_PROFILE" in types and "ON_DEMAND" not in types:
        return f"us.{model_id}"
    return model_id


def body_for(model_id):
    """Smallest valid prompt body per provider."""
    if model_id.startswith("stability.sd3-5") or model_id.startswith("stability.stable-image-core") or model_id.startswith("stability.stable-image-ultra"):
        return {"prompt": "ping", "mode": "text-to-image", "output_format": "png", "aspect_ratio": "1:1"}
    if model_id.startswith("amazon.titan-image"):
        return {"textToImageParams": {"text": "ping"},
                "imageGenerationConfig": {"numberOfImages": 1, "width": 512, "height": 512, "quality": "standard"}}
    if model_id.startswith("amazon.nova-canvas"):
        return {"taskType": "TEXT_IMAGE", "textToImageParams": {"text": "ping"},
                "imageGenerationConfig": {"numberOfImages": 1, "width": 512, "height": 512, "quality": "standard"}}
    # editing-only stability variants need an image; skip for now
    return None


async def probe(gw, m):
    model = m["modelId"]
    types = m.get("inferenceTypesSupported", [])
    body = body_for(model)
    target = cid(model, types)
    if body is None:
        return {"id": model, "callable_id": target, "ok": False,
                "skipped": True, "reason": "edit-only (needs source image)",
                "types": types, "ms": 0}
    t0 = time.time()
    try:
        r = await gw.invoke_model(target, body, timeout=20)
        dt = round((time.time() - t0) * 1000)
        if isinstance(r, dict) and r.get("error"):
            return {"id": model, "callable_id": target, "ok": False,
                    "reason": str(r["error"])[:200], "ms": dt, "types": types}
        return {"id": model, "callable_id": target, "ok": True,
                "ms": dt, "types": types}
    except Exception as e:
        return {"id": model, "callable_id": target, "ok": False,
                "reason": str(e)[:200], "ms": round((time.time() - t0)*1000), "types": types}


async def main():
    gw = GatewayClient(aws_profile=PROFILE, bedrock_user=USER, region=REGION)
    results = []
    for m in imgs:
        r = await probe(gw, m)
        mark = "SKIP" if r.get("skipped") else ("OK" if r["ok"] else "FAIL")
        print(f"  [{mark:4s}] {r['id']:55s} {r['callable_id']:55s} {r.get('ms',0):5d}ms  {r.get('reason','')[:120]}")
        results.append(r)
    ok = [r for r in results if r["ok"]]
    fail = [r for r in results if not r["ok"] and not r.get("skipped")]
    skip = [r for r in results if r.get("skipped")]
    print(f"\nOK: {len(ok)}  FAIL: {len(fail)}  SKIP: {len(skip)}")

    out = ROOT / "scripts" / "probe_image_models_result.json"
    out.write_text(json.dumps({
        "timestamp": time.time(), "profile": PROFILE, "user": USER,
        "ok": ok, "fail": fail, "skip": skip,
    }, indent=2, ensure_ascii=False))
    print(f"Result: {out}")


asyncio.run(main())
