"""BaseChatModel adapter that routes through the Gateway."""
from typing import Any


class GatewayChatModel:
    """LangChain-compatible chat model that calls the Bedrock Gateway."""

    def __init__(self, gateway_client, model_id: str):
        self.gateway = gateway_client
        self.model_id = model_id

    async def ainvoke(self, messages: list, system_prompt: str = "") -> str:
        """Call the gateway and return the assistant text."""
        formatted = []
        for msg in messages:
            if isinstance(msg, dict):
                formatted.append(msg)
            elif hasattr(msg, 'content'):
                formatted.append({
                    "role": getattr(msg, 'role', 'user'),
                    "content": [{"text": msg.content}],
                })
            else:
                formatted.append({"role": "user", "content": [{"text": str(msg)}]})

        result = await self.gateway.converse(
            model_id=self.model_id,
            messages=formatted,
            system_prompt=system_prompt,
        )

        # Extract text from response
        output = result.get("output", {})
        message = output.get("message", {})
        content = message.get("content", [])
        texts = [c.get("text", "") for c in content if "text" in c]
        return "\n".join(texts)

    async def astream(self, messages: list, system_prompt: str = ""):
        """Stream from the gateway, yielding text chunks."""
        formatted = []
        for msg in messages:
            if isinstance(msg, dict):
                formatted.append(msg)
            elif hasattr(msg, 'content'):
                formatted.append({
                    "role": getattr(msg, 'role', 'user'),
                    "content": [{"text": msg.content}],
                })
            else:
                formatted.append({"role": "user", "content": [{"text": str(msg)}]})

        async for chunk in self.gateway.stream_converse(
            model_id=self.model_id,
            messages=formatted,
            system_prompt=system_prompt,
        ):
            yield chunk
