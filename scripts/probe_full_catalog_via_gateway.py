#!/usr/bin/env python3
"""Probe every ACTIVE Bedrock model in us-west-2 via the gateway:
  - TEXT models → /converse (converse_quota_only, maxTokens=1)
  - IMAGE models → /invoke (smallest text-to-image body)
  - EMBEDDING models → /invoke
  - RERANK models → /invoke

Output: scripts/probe_full_catalog_result.json
"""
import asyncio, json, os, sys, time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from ai_engine.gateway_module import GatewayClient

PROFILE = os.environ.get("AWS_PROFILE", "bedrock-gw")
USER = os.environ.get("BEDROCK_USER", "cgjang")

CATALOG = json.loads(Path("/tmp/aws-models.json").read_text())


def callable_id(model_id, types):
    if any(model_id.startswith(p) for p in ("us.", "eu.", "global.")):
        return model_id
    if "INFERENCE_PROFILE" in types and "ON_DEMAND" not in types:
        return f"us.{model_id}"
    return model_id


def classify(model_id, output_modalities):
    raw = model_id
    for p in ("us.", "eu.", "global."):
        if raw.startswith(p):
            raw = raw[len(p):]
            break
    if "EMBEDDING" in output_modalities:
        return "embedding"
    if "IMAGE" in output_modalities:
        return "image"
    if "rerank" in raw:
        return "rerank"
    if "VIDEO" in output_modalities:
        return "video"
    if "TEXT" in output_modalities:
        return "text"
    return "unknown"


async def probe_text(gw, model_id, types):
    cid = callable_id(model_id, types)
    t0 = time.time()
    try:
        r = await gw.converse_quota_only(cid, [{"role": "user", "content": [{"text": "ping"}]}], "")
        dt = round((time.time() - t0) * 1000)
        if isinstance(r, dict) and r.get("error"):
            return {"ok": False, "ms": dt, "reason": str(r["error"])[:160]}
        return {"ok": True, "ms": dt}
    except Exception as e:
        return {"ok": False, "ms": round((time.time() - t0) * 1000), "reason": str(e)[:160]}


def image_body(raw_id):
    if raw_id.startswith("stability.sd3-5") or raw_id.startswith("stability.stable-image-core") or raw_id.startswith("stability.stable-image-ultra"):
        return {"prompt": "ping", "mode": "text-to-image", "output_format": "png", "aspect_ratio": "1:1"}
    if raw_id.startswith("amazon.titan-image"):
        return {"textToImageParams": {"text": "ping"},
                "imageGenerationConfig": {"numberOfImages": 1, "width": 512, "height": 512, "quality": "standard"}}
    if raw_id.startswith("amazon.nova-canvas"):
        return {"taskType": "TEXT_IMAGE", "textToImageParams": {"text": "ping"},
                "imageGenerationConfig": {"numberOfImages": 1, "width": 512, "height": 512, "quality": "standard"}}
    return None  # editing-only models need source image


async def probe_invoke(gw, model_id, types, modal):
    cid = callable_id(model_id, types)
    raw = model_id
    for p in ("us.", "eu.", "global."):
        if raw.startswith(p):
            raw = raw[len(p):]
            break

    if modal == "image":
        body = image_body(raw)
        if body is None:
            return {"ok": False, "ms": 0, "skipped": True, "reason": "edit-only (needs source image)"}
    elif modal == "embedding":
        if raw.startswith("cohere.embed"):
            body = {"texts": ["ping"], "input_type": "search_document"}
        elif raw.startswith("amazon.titan-embed-image"):
            body = {"inputText": "ping"}  # text-only mode
        else:
            body = {"inputText": "ping"}
    elif modal == "rerank":
        # Cohere Rerank v3.5: documents must be string array, not [{text:...}]
        if raw.startswith("cohere"):
            body = {"query": "AI", "documents": ["machine learning", "cats"], "top_n": 2, "api_version": 2}
        else:
            body = {"query": "AI", "documents": [{"text": "machine learning"}, {"text": "cats"}], "top_n": 2}
    elif modal == "video":
        return {"ok": False, "ms": 0, "skipped": True, "reason": "video model — async only"}
    else:
        return {"ok": False, "ms": 0, "skipped": True, "reason": f"unknown modal {modal}"}

    t0 = time.time()
    try:
        r = await gw.invoke_model(cid, body, timeout=20)
        dt = round((time.time() - t0) * 1000)
        if "error" in r:
            return {"ok": False, "ms": dt, "reason": str(r["error"])[:160]}
        return {"ok": True, "ms": dt}
    except Exception as e:
        return {"ok": False, "ms": round((time.time() - t0) * 1000), "reason": str(e)[:160]}


async def main():
    gw = GatewayClient(aws_profile=PROFILE, bedrock_user=USER)
    results = []
    sem = asyncio.Semaphore(3)

    async def worker(m):
        async with sem:
            mid = m["id"]
            types = m.get("types", [])
            mods = m.get("output", [])
            modal = classify(mid, mods)

            if modal == "text":
                r = await probe_text(gw, mid, types)
            elif modal in ("image", "embedding", "rerank", "video"):
                r = await probe_invoke(gw, mid, types, modal)
            else:
                r = {"ok": False, "ms": 0, "skipped": True, "reason": f"modal={modal}"}

            mark = "SKIP" if r.get("skipped") else ("OK" if r["ok"] else "FAIL")
            print(f"  [{mark:4s}] {mid:55s} ({modal:9s}) {r.get('ms', 0):5d}ms  {r.get('reason', '')[:100]}")
            r["model_id"] = mid
            r["modal"] = modal
            r["types"] = types
            return r

    tasks = [worker(m) for m in CATALOG]
    results = await asyncio.gather(*tasks)

    print("\n" + "=" * 70)
    print("SUMMARY by modal")
    print("=" * 70)
    by_modal = {}
    for r in results:
        m = r["modal"]
        by_modal.setdefault(m, {"ok": 0, "fail": 0, "skip": 0})
        if r.get("skipped"):
            by_modal[m]["skip"] += 1
        elif r["ok"]:
            by_modal[m]["ok"] += 1
        else:
            by_modal[m]["fail"] += 1
    for m, c in sorted(by_modal.items()):
        print(f"  {m:10s}  OK={c['ok']:3d}  FAIL={c['fail']:3d}  SKIP={c['skip']:3d}")

    total_ok = sum(1 for r in results if r.get("ok"))
    total = len(results)
    print(f"\nTOTAL: {total_ok}/{total} OK")

    out = ROOT / "scripts" / "probe_full_catalog_result.json"
    out.write_text(json.dumps({
        "timestamp": time.time(),
        "total": total,
        "ok": total_ok,
        "by_modal": by_modal,
        "results": results,
    }, indent=2, ensure_ascii=False))
    print(f"\nResult: {out}")


asyncio.run(main())
