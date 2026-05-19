#!/usr/bin/env python3
"""Seed ModelPricing table with pricing_unit attribute for image/embed/rerank models.

SAFE: Only UPDATES existing rows to ADD pricing_unit attribute (no existing values changed).
For models not yet in the table, PutItem with full pricing data.

Idempotent: re-running produces no change if already seeded.
"""
import boto3
import sys
from decimal import Decimal

TABLE = "bedrock-gw-dev-us-west-2-model-pricing"
PROFILE = "bedrock-gw"
REGION = "us-west-2"

# Models that already have rows — just add pricing_unit + per_image_price_krw
EXISTING_IMAGE_MODELS = {
    "stability.sd3-5-large-v1:0": {"pricing_unit": "per_image", "per_image_price_krw": Decimal("104.00")},
    "stability.stable-image-core-v1:1": {"pricing_unit": "per_image", "per_image_price_krw": Decimal("44.20")},
    "stability.stable-image-ultra-v1:1": {"pricing_unit": "per_image", "per_image_price_krw": Decimal("88.40")},
    "amazon.titan-image-generator-v2:0": {"pricing_unit": "per_image", "per_image_price_krw": Decimal("10.40")},
}

# Models that need new rows entirely
NEW_IMAGE_MODELS = {
    "amazon.nova-canvas-v1:0": {
        "pricing_unit": "per_image",
        "per_image_price_krw": Decimal("10.40"),
        "input_price_per_1k": Decimal("0"),
        "output_price_per_1k": Decimal("0"),
    },
    # Stability editing models (per-image pricing, ~$0.04/image = 52 KRW)
    "stability.stable-image-inpaint-v1:0": {
        "pricing_unit": "per_image",
        "per_image_price_krw": Decimal("52.00"),
        "input_price_per_1k": Decimal("0"),
        "output_price_per_1k": Decimal("0"),
    },
    "stability.stable-outpaint-v1:0": {
        "pricing_unit": "per_image",
        "per_image_price_krw": Decimal("52.00"),
        "input_price_per_1k": Decimal("0"),
        "output_price_per_1k": Decimal("0"),
    },
    "stability.stable-image-erase-object-v1:0": {
        "pricing_unit": "per_image",
        "per_image_price_krw": Decimal("52.00"),
        "input_price_per_1k": Decimal("0"),
        "output_price_per_1k": Decimal("0"),
    },
    "stability.stable-image-search-replace-v1:0": {
        "pricing_unit": "per_image",
        "per_image_price_krw": Decimal("52.00"),
        "input_price_per_1k": Decimal("0"),
        "output_price_per_1k": Decimal("0"),
    },
    "stability.stable-image-search-recolor-v1:0": {
        "pricing_unit": "per_image",
        "per_image_price_krw": Decimal("52.00"),
        "input_price_per_1k": Decimal("0"),
        "output_price_per_1k": Decimal("0"),
    },
    "stability.stable-image-control-sketch-v1:0": {
        "pricing_unit": "per_image",
        "per_image_price_krw": Decimal("52.00"),
        "input_price_per_1k": Decimal("0"),
        "output_price_per_1k": Decimal("0"),
    },
    "stability.stable-image-control-structure-v1:0": {
        "pricing_unit": "per_image",
        "per_image_price_krw": Decimal("52.00"),
        "input_price_per_1k": Decimal("0"),
        "output_price_per_1k": Decimal("0"),
    },
    "stability.stable-image-style-guide-v1:0": {
        "pricing_unit": "per_image",
        "per_image_price_krw": Decimal("52.00"),
        "input_price_per_1k": Decimal("0"),
        "output_price_per_1k": Decimal("0"),
    },
    "stability.stable-style-transfer-v1:0": {
        "pricing_unit": "per_image",
        "per_image_price_krw": Decimal("52.00"),
        "input_price_per_1k": Decimal("0"),
        "output_price_per_1k": Decimal("0"),
    },
    "stability.stable-creative-upscale-v1:0": {
        "pricing_unit": "per_image",
        "per_image_price_krw": Decimal("52.00"),
        "input_price_per_1k": Decimal("0"),
        "output_price_per_1k": Decimal("0"),
    },
    "stability.stable-conservative-upscale-v1:0": {
        "pricing_unit": "per_image",
        "per_image_price_krw": Decimal("52.00"),
        "input_price_per_1k": Decimal("0"),
        "output_price_per_1k": Decimal("0"),
    },
    "stability.stable-fast-upscale-v1:0": {
        "pricing_unit": "per_image",
        "per_image_price_krw": Decimal("52.00"),
        "input_price_per_1k": Decimal("0"),
        "output_price_per_1k": Decimal("0"),
    },
    "stability.stable-image-remove-background-v1:0": {
        "pricing_unit": "per_image",
        "per_image_price_krw": Decimal("26.00"),
        "input_price_per_1k": Decimal("0"),
        "output_price_per_1k": Decimal("0"),
    },
}

# Rerank models — already have rows, add pricing_unit + per_doc_price_krw
EXISTING_RERANK_MODELS = {
    "cohere.rerank-v3-5:0": {"pricing_unit": "rerank_doc", "per_doc_price_krw": Decimal("2.60")},
    "amazon.rerank-v1:0": {"pricing_unit": "rerank_doc", "per_doc_price_krw": Decimal("2.60")},
}

# Embedding models — already have rows, add pricing_unit
EXISTING_EMBED_MODELS = {
    "amazon.titan-embed-text-v1": {"pricing_unit": "embedding_token"},
    "amazon.titan-embed-text-v2:0": {"pricing_unit": "embedding_token"},
    "amazon.titan-embed-image-v1": {"pricing_unit": "embedding_token"},
    "cohere.embed-english-v3": {"pricing_unit": "embedding_token"},
    "cohere.embed-multilingual-v3": {"pricing_unit": "embedding_token"},
}


def main():
    session = boto3.Session(profile_name=PROFILE, region_name=REGION)
    dynamodb = session.resource("dynamodb")
    table = dynamodb.Table(TABLE)

    updated = 0
    created = 0
    skipped = 0

    # Update existing image models — add pricing_unit + per_image_price_krw
    for model_id, attrs in EXISTING_IMAGE_MODELS.items():
        try:
            table.update_item(
                Key={"model_id": model_id},
                UpdateExpression="SET pricing_unit = :pu, per_image_price_krw = :pip",
                ExpressionAttributeValues={
                    ":pu": attrs["pricing_unit"],
                    ":pip": attrs["per_image_price_krw"],
                },
                ConditionExpression="attribute_exists(model_id)",
            )
            print(f"  [UPDATE] {model_id} → pricing_unit={attrs['pricing_unit']}, per_image={attrs['per_image_price_krw']}")
            updated += 1
        except table.meta.client.exceptions.ConditionalCheckFailedException:
            print(f"  [SKIP] {model_id} — row not found (unexpected)")
            skipped += 1

    # Create new image models
    for model_id, attrs in NEW_IMAGE_MODELS.items():
        try:
            item = {"model_id": model_id, **attrs}
            table.put_item(
                Item=item,
                ConditionExpression="attribute_not_exists(model_id)",
            )
            print(f"  [CREATE] {model_id}")
            created += 1
        except table.meta.client.exceptions.ConditionalCheckFailedException:
            # Already exists — just update pricing_unit
            table.update_item(
                Key={"model_id": model_id},
                UpdateExpression="SET pricing_unit = :pu, per_image_price_krw = :pip",
                ExpressionAttributeValues={
                    ":pu": attrs["pricing_unit"],
                    ":pip": attrs["per_image_price_krw"],
                },
            )
            print(f"  [UPDATE] {model_id} (already existed)")
            updated += 1

    # Update existing rerank models
    for model_id, attrs in EXISTING_RERANK_MODELS.items():
        try:
            table.update_item(
                Key={"model_id": model_id},
                UpdateExpression="SET pricing_unit = :pu, per_doc_price_krw = :pdp",
                ExpressionAttributeValues={
                    ":pu": attrs["pricing_unit"],
                    ":pdp": attrs["per_doc_price_krw"],
                },
                ConditionExpression="attribute_exists(model_id)",
            )
            print(f"  [UPDATE] {model_id} → pricing_unit={attrs['pricing_unit']}")
            updated += 1
        except table.meta.client.exceptions.ConditionalCheckFailedException:
            print(f"  [SKIP] {model_id} — row not found")
            skipped += 1

    # Update existing embedding models
    for model_id, attrs in EXISTING_EMBED_MODELS.items():
        try:
            table.update_item(
                Key={"model_id": model_id},
                UpdateExpression="SET pricing_unit = :pu",
                ExpressionAttributeValues={":pu": attrs["pricing_unit"]},
                ConditionExpression="attribute_exists(model_id)",
            )
            print(f"  [UPDATE] {model_id} → pricing_unit={attrs['pricing_unit']}")
            updated += 1
        except table.meta.client.exceptions.ConditionalCheckFailedException:
            print(f"  [SKIP] {model_id} — row not found")
            skipped += 1

    print(f"\nDone. Created: {created}, Updated: {updated}, Skipped: {skipped}")


if __name__ == "__main__":
    main()
