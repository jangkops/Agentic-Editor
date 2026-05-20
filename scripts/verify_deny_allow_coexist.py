#!/usr/bin/env python3
"""Verify that DenyDirectBedrockInference still blocks direct Bedrock calls,
while AllowDevGatewayInvoke permits gateway-mediated calls.

Tests three scenarios from the BedrockUser-cgjang principal perspective:
  1. Direct bedrock-runtime.invoke_model() → MUST FAIL (Deny still works)
  2. Direct bedrock-runtime.converse() → MUST FAIL (Deny still works)
  3. Gateway /invoke → MUST PASS (Allow works)
  4. Gateway /converse → MUST PASS (Allow works, unchanged)
"""
import asyncio
import boto3
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from ai_engine.gateway_module import GatewayClient


def assume_bedrock_user(user="cgjang"):
    """Assume the BedrockUser-{user} role so we test as that principal,
    not as the SSO Administrator."""
    session = boto3.Session(profile_name="bedrock-gw")
    sts = session.client("sts")
    account = sts.get_caller_identity()["Account"]
    resp = sts.assume_role(
        RoleArn=f"arn:aws:iam::{account}:role/BedrockUser-{user}",
        RoleSessionName="deny-allow-test",
    )
    creds = resp["Credentials"]
    return boto3.Session(
        aws_access_key_id=creds["AccessKeyId"],
        aws_secret_access_key=creds["SecretAccessKey"],
        aws_session_token=creds["SessionToken"],
        region_name="us-west-2",
    )


def test_direct_invoke_model(session):
    """TEST 1: Direct invoke_model — MUST FAIL with AccessDenied."""
    print("--- TEST 1: Direct bedrock-runtime.invoke_model() ---")
    try:
        client = session.client("bedrock-runtime")
        client.invoke_model(
            modelId="amazon.titan-embed-text-v2:0",
            body=json.dumps({"inputText": "test"}),
            contentType="application/json",
            accept="application/json",
        )
        print("  [FAIL] ❌ Direct call SUCCEEDED — Deny policy is broken!")
        return False
    except Exception as e:
        msg = str(e)[:200]
        if "AccessDenied" in msg or "explicit deny" in msg.lower():
            print(f"  [OK] ✅ Blocked as expected: {msg[:120]}")
            return True
        else:
            print(f"  [UNCLEAR] Failed but for different reason: {msg}")
            return False


def test_direct_converse(session):
    """TEST 2: Direct converse — MUST FAIL with AccessDenied."""
    print("--- TEST 2: Direct bedrock-runtime.converse() ---")
    try:
        client = session.client("bedrock-runtime")
        client.converse(
            modelId="us.anthropic.claude-haiku-4-5-20251001-v1:0",
            messages=[{"role": "user", "content": [{"text": "ping"}]}],
            inferenceConfig={"maxTokens": 1},
        )
        print("  [FAIL] ❌ Direct converse SUCCEEDED — Deny policy is broken!")
        return False
    except Exception as e:
        msg = str(e)[:200]
        if "AccessDenied" in msg or "explicit deny" in msg.lower():
            print(f"  [OK] ✅ Blocked as expected: {msg[:120]}")
            return True
        else:
            print(f"  [UNCLEAR] Failed but for different reason: {msg}")
            return False


async def test_gateway_invoke():
    """TEST 3: Gateway /invoke — MUST PASS."""
    print("--- TEST 3: Gateway /invoke (Titan Embed) ---")
    gw = GatewayClient(aws_profile="bedrock-gw", bedrock_user="cgjang")
    r = await gw.invoke_model(
        "amazon.titan-embed-text-v2:0",
        {"inputText": "test embedding via gateway", "dimensions": 1024, "normalize": True},
        timeout=15,
    )
    if "error" in r:
        print(f"  [FAIL] ❌ Gateway /invoke blocked: {r['error'][:120]}")
        return False
    if "embedding" in r:
        print(f"  [OK] ✅ Gateway /invoke succeeded, dim={len(r['embedding'])}")
        return True
    print(f"  [UNCLEAR] keys={list(r.keys())[:5]}")
    return False


async def test_gateway_converse():
    """TEST 4: Gateway /converse — MUST PASS (existing flow)."""
    print("--- TEST 4: Gateway /converse (Claude Haiku) ---")
    gw = GatewayClient(aws_profile="bedrock-gw", bedrock_user="cgjang")
    r = await gw.converse(
        "us.anthropic.claude-haiku-4-5-20251001-v1:0",
        [{"role": "user", "content": [{"text": "ping. one word reply only."}]}],
        "",
    )
    if isinstance(r, dict) and "output" in r:
        print(f"  [OK] ✅ Gateway /converse succeeded")
        return True
    if isinstance(r, dict) and "error" in r:
        print(f"  [FAIL] ❌ Gateway /converse blocked: {r['error'][:120]}")
        return False
    print(f"  [UNCLEAR] response: {str(r)[:200]}")
    return False


async def main():
    print("=" * 70)
    print("Verify: DenyDirectBedrockInference + AllowDevGatewayInvoke coexist")
    print("Principal: BedrockUser-cgjang")
    print("=" * 70)

    session = assume_bedrock_user("cgjang")

    results = []
    results.append(("Direct invoke_model blocked", test_direct_invoke_model(session)))
    results.append(("Direct converse blocked", test_direct_converse(session)))
    results.append(("Gateway /invoke allowed", await test_gateway_invoke()))
    results.append(("Gateway /converse allowed", await test_gateway_converse()))

    print("\n" + "=" * 70)
    print("SUMMARY")
    print("=" * 70)
    for name, ok in results:
        mark = "✅" if ok else "❌"
        print(f"  {mark}  {name}")
    all_ok = all(ok for _, ok in results)
    print(f"\n{'ALL CHECKS PASSED — Deny+Allow coexist correctly' if all_ok else 'SOME CHECKS FAILED — review needed'}")
    return 0 if all_ok else 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
