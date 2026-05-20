#!/usr/bin/env python3
"""Add missing models to all BedrockUser principal_policy allow_lists.

Adds: cohere.embed-v4:0, writer.palmyra-x4/x5, llama 3.1/3.3 variants,
      Stability edit models (already EULA-accepted)
"""
import boto3

PROFILE = "bedrock-gw"
TABLE = "bedrock-gw-dev-us-west-2-principal-policy"

# Models to add to allow_list
NEW_MODELS = [
    # Embed (now EULA accepted, just need allow_list)
    "cohere.embed-v4:0",
    # Writer (now Lambda has marketplace perms)
    "writer.palmyra-x4-v1:0",
    "writer.palmyra-x5-v1:0",
    # Llama 3.1+ family
    "meta.llama3-1-8b-instruct-v1:0",
    "meta.llama3-1-70b-instruct-v1:0",
    # Stability editing models — INFERENCE_PROFILE only requires us. prefix
    "stability.stable-image-inpaint-v1:0",
    "us.stability.stable-image-inpaint-v1:0",
    "stability.stable-outpaint-v1:0",
    "us.stability.stable-outpaint-v1:0",
    "stability.stable-image-erase-object-v1:0",
    "us.stability.stable-image-erase-object-v1:0",
    "stability.stable-image-search-replace-v1:0",
    "us.stability.stable-image-search-replace-v1:0",
    "stability.stable-image-search-recolor-v1:0",
    "us.stability.stable-image-search-recolor-v1:0",
    "stability.stable-image-control-sketch-v1:0",
    "us.stability.stable-image-control-sketch-v1:0",
    "stability.stable-image-control-structure-v1:0",
    "us.stability.stable-image-control-structure-v1:0",
    "stability.stable-image-style-guide-v1:0",
    "us.stability.stable-image-style-guide-v1:0",
    "stability.stable-style-transfer-v1:0",
    "us.stability.stable-style-transfer-v1:0",
    "stability.stable-creative-upscale-v1:0",
    "us.stability.stable-creative-upscale-v1:0",
    "stability.stable-conservative-upscale-v1:0",
    "us.stability.stable-conservative-upscale-v1:0",
    "stability.stable-fast-upscale-v1:0",
    "us.stability.stable-fast-upscale-v1:0",
    "stability.stable-image-remove-background-v1:0",
    "us.stability.stable-image-remove-background-v1:0",
    # Cohere v4 with us. prefix (INFERENCE_PROFILE only)
    "us.cohere.embed-v4:0",
    # Writer with us. prefix (INFERENCE_PROFILE only)
    "us.writer.palmyra-x4-v1:0",
    "us.writer.palmyra-x5-v1:0",
]


def main():
    session = boto3.Session(profile_name=PROFILE, region_name="us-west-2")
    ddb = session.resource("dynamodb")
    table = ddb.Table(TABLE)

    # Scan all principals
    resp = table.scan()
    principals = [item for item in resp.get("Items", []) if item.get("principal_id", "").startswith("107650139384#BedrockUser-")]
    print(f"Found {len(principals)} BedrockUser principals")

    for p in principals:
        pid = p["principal_id"]
        current = list(p.get("allowed_models", []))
        added = []
        for m in NEW_MODELS:
            if m not in current:
                current.append(m)
                added.append(m)
        if added:
            current.sort()
            table.update_item(
                Key={"principal_id": pid},
                UpdateExpression="SET allowed_models = :am",
                ExpressionAttributeValues={":am": current},
            )
            print(f"  [{pid.split('#')[1]}] +{len(added)} models")
        else:
            print(f"  [{pid.split('#')[1]}] already up-to-date")


if __name__ == "__main__":
    main()
