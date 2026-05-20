#!/usr/bin/env python3
"""Accept EULA for all Stability AI image models in us-west-2.
Also checks availability of Titan/Nova Canvas models.

Requires active SSO session: aws sso login --profile bedrock-gw
"""
import boto3
import json
import sys

PROFILE = "bedrock-gw"
REGION = "us-west-2"

# All 16 IMAGE-output models from catalog
IMAGE_MODELS = [
    "stability.sd3-5-large-v1:0",
    "stability.stable-image-core-v1:1",
    "stability.stable-image-ultra-v1:1",
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

# Non-Stability image models to check
OTHER_IMAGE_MODELS = [
    "amazon.titan-image-generator-v2:0",
    "amazon.nova-canvas-v1:0",
]


def main():
    session = boto3.Session(profile_name=PROFILE, region_name=REGION)
    client = session.client("bedrock")

    print("=" * 60)
    print("STEP 1: Accept EULA for Stability AI models")
    print("=" * 60)

    accepted = 0
    already = 0
    failed = 0

    for model_id in IMAGE_MODELS:
        try:
            # Check current availability
            avail = client.get_foundation_model_availability(modelId=model_id)
            agreement_status = avail.get("agreementAvailability", {}).get("status", "UNKNOWN")

            if agreement_status == "AVAILABLE":
                print(f"  [ALREADY] {model_id}")
                already += 1
                continue

            # Get offer
            offers_resp = client.list_foundation_model_agreement_offers(modelId=model_id)
            offers = offers_resp.get("offers", [])
            if not offers:
                print(f"  [NO-OFFER] {model_id} — no agreement offer available")
                failed += 1
                continue

            offer_token = offers[0]["offerToken"]

            # Accept
            client.create_foundation_model_agreement(
                modelId=model_id,
                offerToken=offer_token,
            )
            print(f"  [ACCEPTED] {model_id}")
            accepted += 1

        except Exception as e:
            err = str(e)[:100]
            if "already" in err.lower() or "conflict" in err.lower():
                print(f"  [ALREADY] {model_id} (conflict = already accepted)")
                already += 1
            else:
                print(f"  [ERROR] {model_id}: {err}")
                failed += 1

    print(f"\nStability results: accepted={accepted}, already={already}, failed={failed}")

    print("\n" + "=" * 60)
    print("STEP 2: Check other image model availability")
    print("=" * 60)

    for model_id in OTHER_IMAGE_MODELS:
        try:
            avail = client.get_foundation_model_availability(modelId=model_id)
            print(f"  {model_id}:")
            print(f"    agreement: {avail.get('agreementAvailability', {}).get('status', '?')}")
            print(f"    authorization: {avail.get('authorizationStatus', '?')}")
            print(f"    entitlement: {avail.get('entitlementAvailability', '?')}")
            print(f"    region: {avail.get('regionAvailability', '?')}")
        except Exception as e:
            print(f"  {model_id}: ERROR — {str(e)[:100]}")

    print("\n" + "=" * 60)
    print("STEP 3: Verify SD3.5 is now callable via /invoke")
    print("=" * 60)

    # Quick invoke test
    try:
        bedrock_rt = session.client("bedrock-runtime")
        body = json.dumps({
            "prompt": "test ping",
            "mode": "text-to-image",
            "output_format": "png",
            "aspect_ratio": "1:1",
        }).encode()
        resp = bedrock_rt.invoke_model(
            modelId="stability.sd3-5-large-v1:0",
            body=body,
            contentType="application/json",
            accept="application/json",
        )
        result = json.loads(resp["body"].read())
        if "images" in result:
            print(f"  [SUCCESS] SD3.5 direct invoke: {len(result['images'])} image(s)")
        else:
            print(f"  [UNEXPECTED] keys: {list(result.keys())[:5]}")
    except Exception as e:
        print(f"  [FAIL] SD3.5 direct invoke: {str(e)[:200]}")
        print("  Note: If AccessDenied, the DenyDirectBedrockInference policy blocks direct calls.")
        print("  The /invoke gateway route uses the Lambda role which IS allowed.")


if __name__ == "__main__":
    main()
