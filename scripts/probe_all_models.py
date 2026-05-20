#!/usr/bin/env python3
"""Probe every ACTIVE TEXT-output Bedrock model through the gateway.
For each model, send a 1-token ping via converse_quota_only and record
which ones actually respond OK from the BedrockUser-cgjang principal.

Output: scripts/probe_all_models_result.json
  - ok_models: live-confirmed callable
  - failed_models: with reason
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
REGION = os.environ.get("AWS_REGION", "us-west-2")

CATALOG_PATH = Path("/tmp/aws-models.json")
if not CATALOG_PATH.exists():
    print("ERR: /tmp/aws-models.json missing — run aws bedrock list-foundation-models first")
    sys.exit(1)

catalog = json.loads(CATALOG_PATH.read_text())

# Gather TEXT models only.
text_models = []
for m in catalog:
    out = m.get("outputModalities", [])
    if "TEXT" not in out:
        continue
    types = m.get("inferenceTypesSupported", [])
    text_models.append({
        "id": m["modelId"],
        "provider": m.get("providerName", ""),
        "types": types,
    })
print(f"TEXT models to probe: {len(text_models)}")


def callable_id(model_id, types):
    """Build the actual id sent to the gateway. INFERENCE_PROFILE only → us. prefix."""
    if any(model_id.startswith(p) for p in ("us.", "eu.", "global.")):
        return model_id
    if "INFERENCE_PROFILE" in types and "ON_DEMAND" not in types:
        return f"us.{model_id}"
    return model_id


async def probe(gw, item):
    cid = callable_id(item["id"], item["types"])
    messages = [{"role": "user", "content": [{"text": "ping"}]}]
    t0 = time.time()
    try:
        r = await gw.converse_quota_only(cid, messages, "")
        dt = round((time.time() - t0) * 1000)
        # converse_quota_only may return {"error": ...} on failure
        if isinstance(r, dict) and r.get("error"):
            return {"id": item["id"], "callable_id": cid, "ok": False,
                    "reason": str(r.get("error"))[:200], "ms": dt,
                    "provider": item["provider"], "types": item["types"]}
        # Success: response shape varies but no "error" key
        return {"id": item["id"], "callable_id": cid, "ok": True,
                "ms": dt, "provider": item["provider"], "types": item["types"]}
    except Exception as e:
        dt = round((time.time() - t0) * 1000)
        return {"id": item["id"], "callable_id": cid, "ok": False,
                "reason": str(e)[:200], "ms": dt,
                "provider": item["provider"], "types": item["types"]}


async def main():
    gw = GatewayClient(aws_profile=PROFILE, bedrock_user=USER, region=REGION)
    results = []
    sem = asyncio.Semaphore(4)  # gateway throttling guard

    async def worker(item):
        async with sem:
            r = await probe(gw, item)
            mark = "OK" if r["ok"] else "FAIL"
            print(f"  [{mark:4s}] {r['id']:55s} {r['callable_id']:55s} {r['ms']:5d}ms  {r.get('reason','')[:80]}")
            return r

    tasks = [worker(it) for it in text_models]
    results = await asyncio.gather(*tasks)

    ok = [r for r in results if r["ok"]]
    fail = [r for r in results if not r["ok"]]
    print()
    print(f"OK:   {len(ok)} / {len(text_models)}")
    print(f"FAIL: {len(fail)}")

    out_path = ROOT / "scripts" / "probe_all_models_result.json"
    out_path.write_text(json.dumps({
        "timestamp": time.time(),
        "profile": PROFILE,
        "user": USER,
        "region": REGION,
        "ok": ok,
        "fail": fail,
    }, indent=2, ensure_ascii=False))
    print(f"\nResult written: {out_path}")


asyncio.run(main())
