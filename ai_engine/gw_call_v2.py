"""Gateway call v2 — SigV4-signed async calls to Bedrock Gateway.

This module is kept as a reference/utility. The main client is in gateway_module.py.
"""
import httpx
import boto3
from httpx_auth_awssigv4 import SigV4Auth


async def call_gateway(
    prompt: str,
    model_id: str = "anthropic.claude-3-opus-20240229-v1:0",
    aws_profile: str = "default",
    region: str = "us-west-2",
    gateway_url: str = "https://5l764dh7y9.execute-api.us-west-2.amazonaws.com/v1",
    max_tokens: int = 4096,
) -> dict:
    """One-shot gateway call with SigV4 auth."""
    session = boto3.Session(profile_name=aws_profile)
    credentials = session.get_credentials().get_frozen_credentials()
    auth = SigV4Auth(
        access_key=credentials.access_key,
        secret_key=credentials.secret_key,
        token=credentials.token,
        service="execute-api",
        region=region,
    )

    payload = {
        "modelId": model_id,
        "messages": [{"role": "user", "content": [{"text": prompt}]}],
        "inferenceConfig": {"maxTokens": max_tokens},
    }

    async with httpx.AsyncClient(auth=auth, timeout=120) as client:
        resp = await client.post(f"{gateway_url}/converse", json=payload)
        resp.raise_for_status()
        return resp.json()
