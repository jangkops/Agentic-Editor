#!/usr/bin/env python3
"""Seed ModelPricing with us.* prefix duplicates of existing rows for
INFERENCE_PROFILE-only models. The gateway resolves to us.* before pricing
lookup, so both keys must exist."""
import boto3
from decimal import Decimal

PROFILE = "bedrock-gw"
TABLE = "bedrock-gw-dev-us-west-2-model-pricing"

# (raw_id, pricing_unit, per_image_price_krw_or_None)
US_VARIANTS = [
    ("stability.stable-image-inpaint-v1:0", "per_image", Decimal("52")),
    ("stability.stable-outpaint-v1:0", "per_image", Decimal("52")),
    ("stability.stable-image-erase-object-v1:0", "per_image", Decimal("52")),
    ("stability.stable-image-search-replace-v1:0", "per_image", Decimal("52")),
    ("stability.stable-image-search-recolor-v1:0", "per_image", Decimal("52")),
    ("stability.stable-image-control-sketch-v1:0", "per_image", Decimal("52")),
    ("stability.stable-image-control-structure-v1:0", "per_image", Decimal("52")),
    ("stability.stable-image-style-guide-v1:0", "per_image", Decimal("52")),
    ("stability.stable-style-transfer-v1:0", "per_image", Decimal("52")),
    ("stability.stable-creative-upscale-v1:0", "per_image", Decimal("52")),
    ("stability.stable-conservative-upscale-v1:0", "per_image", Decimal("52")),
    ("stability.stable-fast-upscale-v1:0", "per_image", Decimal("52")),
    ("stability.stable-image-remove-background-v1:0", "per_image", Decimal("26")),
    # Cohere v4 (token-based, AWS price ~$0.00012/1K = 0.178 KRW)
    ("cohere.embed-v4:0", "embedding_token", None),
    # Writer Palmyra (token-based)
    ("writer.palmyra-x4-v1:0", "token", None),
    ("writer.palmyra-x5-v1:0", "token", None),
]


def main():
    session = boto3.Session(profile_name=PROFILE, region_name="us-west-2")
    table = session.resource("dynamodb").Table(TABLE)

    for raw_id, unit, per_image in US_VARIANTS:
        us_id = f"us.{raw_id}"
        # Try to copy existing raw row, else create minimal entry
        try:
            existing = table.get_item(Key={"model_id": raw_id}).get("Item")
        except Exception:
            existing = None

        if existing:
            # Copy attrs to us. variant
            new_item = {k: v for k, v in existing.items() if k != "model_id"}
            new_item["model_id"] = us_id
            table.put_item(Item=new_item)
            print(f"  [COPY]   {us_id}")
        else:
            # Create minimal entry
            item = {
                "model_id": us_id,
                "pricing_unit": unit,
                "input_price_per_1k": Decimal("0"),
                "output_price_per_1k": Decimal("0"),
            }
            if per_image is not None:
                item["per_image_price_krw"] = per_image
            table.put_item(Item=item)
            print(f"  [CREATE] {us_id}")


if __name__ == "__main__":
    main()
