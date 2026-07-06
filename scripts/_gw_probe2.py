"""Probe which model IDs the gateway actually allows right now.
Tests both bare and us.-prefixed IDs for the two steering-mandated models."""
import asyncio, sys
sys.path.insert(0, ".")
import boto3
from ai_engine.gateway_module import GatewayClient

PROFILE = "bedrock-gw"
USER = "cgjang"

CANDIDATES = [
    "anthropic.claude-3-5-sonnet-20241022-v2:0",
    "us.anthropic.claude-3-5-sonnet-20241022-v2:0",
    "anthropic.claude-3-opus-20240229-v1:0",
    "us.anthropic.claude-3-opus-20240229-v1:0",
    "anthropic.claude-3-haiku-20240307-v1:0",
    "us.anthropic.claude-3-haiku-20240307-v1:0",
]

async def main():
    sess = boto3.Session(profile_name=PROFILE)
    sts = sess.client("sts")
    acct = sts.get_caller_identity()["Account"]
    assumed = sts.assume_role(
        RoleArn=f"arn:aws:iam::{acct}:role/BedrockUser-{USER}",
        RoleSessionName="gw-probe2",
    )
    c = assumed["Credentials"]
    gw = GatewayClient(aws_profile=PROFILE, region="us-west-2", bedrock_user="")
    gw.inject_credentials(c["AccessKeyId"], c["SecretAccessKey"], c["SessionToken"])
    msgs = [{"role": "user", "content": [{"text": "ping"}]}]
    for m in CANDIDATES:
        res = await gw.converse_quota_only(m, msgs)
        dec = res.get("decision")
        err = (res.get("error") or res.get("denial_reason") or "")[:90]
        print(f"{dec:8} | {m}  {('-> ' + err) if dec != 'ALLOW' else '✓ ALLOWED'}")

asyncio.run(main())
