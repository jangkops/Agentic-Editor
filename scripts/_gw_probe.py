"""Reproduce the editor's real gateway call path to see where it breaks.

Mirrors reset-cache: bedrock-gw SSO -> assume BedrockUser-cgjang -> inject ->
SigV4 -> /converse quota-only (maxTokens=1, minimal cost)."""
import asyncio, sys
sys.path.insert(0, ".")
import boto3
from ai_engine.gateway_module import GatewayClient

PROFILE = "bedrock-gw"
USER = "cgjang"

async def main():
    sess = boto3.Session(profile_name=PROFILE)
    sts = sess.client("sts")
    acct = sts.get_caller_identity()["Account"]
    print("account:", acct)
    assumed = sts.assume_role(
        RoleArn=f"arn:aws:iam::{acct}:role/BedrockUser-{USER}",
        RoleSessionName="gw-probe",
    )
    c = assumed["Credentials"]
    print("assume-role OK:", c["AccessKeyId"][:8], "...")

    gw = GatewayClient(aws_profile=PROFILE, region="us-west-2", bedrock_user="")
    gw.inject_credentials(c["AccessKeyId"], c["SecretAccessKey"], c["SessionToken"])

    model = "anthropic.claude-3-5-sonnet-20241022-v2:0"
    msgs = [{"role": "user", "content": [{"text": "ping"}]}]
    res = await gw.converse_quota_only(model, msgs)
    print("GATEWAY RESULT:", res)

asyncio.run(main())
