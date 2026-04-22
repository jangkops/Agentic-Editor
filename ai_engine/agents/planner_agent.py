"""Planner agent — creates structured implementation plans."""
from typing import Dict


class PlannerAgent:
    def __init__(self, bedrock_client, model_id: str = "anthropic.claude-3-opus-20240229-v1:0"):
        self.client = bedrock_client
        self.model_id = model_id

    async def generate_plan(self, prompt: str) -> Dict:
        messages = [{
            "role": "user",
            "content": [{"text": (
                f"Create a structured implementation plan for:\n{prompt}\n\n"
                "Respond with numbered tasks only."
            )}],
        }]
        try:
            result = await self.client.converse(
                model_id=self.model_id,
                messages=messages,
                system_prompt="You are a senior software architect. Create clear, actionable plans.",
            )
            output = result.get("output", {})
            message = output.get("message", {})
            content = message.get("content", [])
            texts = [c.get("text", "") for c in content if "text" in c]
            plan_text = "\n".join(texts)
            return {"tasks": plan_text.split("\n"), "prompt": prompt}
        except Exception:
            return {
                "tasks": ["Task 1: Scaffold project", "Task 2: Implement core logic"],
                "prompt": prompt,
            }
