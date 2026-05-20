#!/usr/bin/env python3
"""Patch _load_model_pricing_cache in handler.py to cache ALL DynamoDB attributes,
not just input_price_per_1k and output_price_per_1k. This is required for
_estimate_cost_krw_invoke to read pricing_unit and per_image_price_krw.
"""
import ast
from pathlib import Path

SRC = Path("/tmp/lambda-patch/handler.py.patched")
text = SRC.read_text(encoding="utf-8")

OLD = '''        for item in resp.get("Items", []):
            mid = item.get("model_id", "")
            if mid:
                cache[mid] = {
                    "input_price_per_1k": Decimal(str(item.get("input_price_per_1k", 0))),
                    "output_price_per_1k": Decimal(str(item.get("output_price_per_1k", 0))),
                }'''

NEW = '''        for item in resp.get("Items", []):
            mid = item.get("model_id", "")
            if mid:
                # [INVOKE-ROUTE-v1] Cache ALL attributes for modal-aware pricing
                cache[mid] = {k: v for k, v in item.items() if k != "model_id"}
                # Ensure required numeric fields are Decimal
                for nk in ("input_price_per_1k", "output_price_per_1k", "per_image_price_krw", "per_doc_price_krw"):
                    if nk in cache[mid]:
                        cache[mid][nk] = Decimal(str(cache[mid][nk]))'''

if OLD not in text:
    raise SystemExit("ERROR: pricing cache block not found")

text = text.replace(OLD, NEW, 1)
ast.parse(text)
SRC.write_text(text, encoding="utf-8")
print("Pricing cache patched successfully")
