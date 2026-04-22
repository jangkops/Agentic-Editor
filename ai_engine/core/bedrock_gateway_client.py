"""Bedrock Gateway client with SigV4 auth and retry logic."""
import asyncio
import httpx
import boto3
from httpx_auth_awssigv4 import SigV4Auth
from typing import Dict, Any, AsyncIterator, Optional


class QuotaExceededError(Exception):
    pass


class InvalidPayloadError(Exception):
    pass


class GatewayError(Exception):
    pass


class BedrockGatewayClient:
    """Async client for Bedrock Gateway with SigV4 authentication."""

    def __init__(
        self,
        gateway_base_url: str = "",
        aws_profile: str = "default",
        region: str = "us-west-2",
    ):
        self.gateway_url = (gateway_base_url or
            "https://5l764dh7y9.execute-api.us-west-2.amazonaws.com/v1").rstrip("/")
        self.aws_profile = aws_profile
        self.region = region
        self._client: Optional[httpx.AsyncClient] = None

    async def _ensure_client(self) -> httpx.AsyncClient:
        if self._client is None or self._client.is_closed:
            session = boto3.Session(profile_name=self.aws_profile)
            creds = session.get_credentials().get_frozen_credentials()
            auth = SigV4Auth(
                access_key=creds.access_key,
                secret_key=creds.secret_key,
                token=creds.token,
                service="execute-api",
                region=self.region,
            )
            self._client = httpx.AsyncClient(auth=auth, timeout=120)
        return self._client

    def _build_payload(
        self, model_id: str, messages: list, max_tokens: int = 4096
    ) -> dict:
        return {
            "modelId": model_id,
            "messages": messages,
            "inferenceConfig": {"maxTokens": max_tokens},
        }

    async def converse(
        self,
        model_id: str,
        messages: list,
        max_tokens: int = 4096,
        system_prompt: str = "",
    ) -> Dict[str, Any]:
        """Non-streaming converse call with retry."""
        client = await self._ensure_client()
        payload = self._build_payload(model_id, messages, max_tokens)
        if system_prompt:
            payload["system"] = [{"text": system_prompt}]

        for attempt in range(3):
            try:
                resp = await client.post(
                    f"{self.gateway_url}/converse", json=payload
                )
                if resp.status_code == 403:
                    raise QuotaExceededError("Daily quota exceeded")
                elif resp.status_code == 422:
                    raise InvalidPayloadError(f"Invalid payload: {resp.text}")
                elif resp.status_code >= 500:
                    if attempt < 2:
                        await asyncio.sleep(2 ** attempt)
                        continue
                    raise GatewayError(
                        f"Gateway error after retries: {resp.status_code}"
                    )
                return resp.json()
            except (QuotaExceededError, InvalidPayloadError):
                raise
            except Exception as e:
                if attempt < 2:
                    await asyncio.sleep(2 ** attempt)
                    continue
                raise GatewayError(str(e))

    async def converse_stream(
        self,
        model_id: str,
        messages: list,
        max_tokens: int = 4096,
        system_prompt: str = "",
    ) -> AsyncIterator[str]:
        """Streaming converse call — yields text chunks."""
        client = await self._ensure_client()
        payload = self._build_payload(model_id, messages, max_tokens)
        if system_prompt:
            payload["system"] = [{"text": system_prompt}]

        async with client.stream(
            "POST", f"{self.gateway_url}/converse-stream", json=payload
        ) as resp:
            if resp.status_code == 403:
                raise QuotaExceededError("Daily quota exceeded")
            resp.raise_for_status()
            async for line in resp.aiter_lines():
                if line.startswith("data:"):
                    import json
                    data = line[5:].strip()
                    if data and data != "[DONE]":
                        try:
                            chunk = json.loads(data)
                            text = (chunk.get("contentBlockDelta", {})
                                    .get("delta", {}).get("text", ""))
                            if text:
                                yield text
                        except json.JSONDecodeError:
                            yield data

    async def close(self):
        if self._client and not self._client.is_closed:
            await self._client.aclose()
