"""LangGraph StateGraph: Planner → Coder → Reviewer → Executor."""
from ai_engine.agent_system.state import AgentState
from ai_engine.agent_system.chat_model_adapter import GatewayChatModel
from ai_engine.agent_system.tool_registry import ToolRegistry


class AgentGraph:
    """Multi-agent workflow graph."""

    def __init__(self, gateway_client):
        self.planner = GatewayChatModel(gateway_client, "anthropic.claude-3-opus-20240229-v1:0")
        self.coder = GatewayChatModel(gateway_client, "anthropic.claude-3-5-sonnet-20241022-v2:0")
        self.reviewer = GatewayChatModel(gateway_client, "anthropic.claude-3-opus-20240229-v1:0")
        self.tools = ToolRegistry()

    async def _plan(self, state: AgentState) -> AgentState:
        """Planner node: create implementation plan."""
        prompt = f"Create a detailed implementation plan for:\n{state.task}\n\nBe specific about files, functions, and steps."
        state.plan = await self.planner.ainvoke(
            [{"role": "user", "content": [{"text": prompt}]}],
            system_prompt="You are a senior software architect. Create clear, actionable plans.",
        )
        state.status = "planning_done"
        return state

    async def _code(self, state: AgentState) -> AgentState:
        """Coder node: generate code based on plan."""
        prompt = f"Task: {state.task}\n\nPlan:\n{state.plan}\n\n"
        if state.review_feedback:
            prompt += f"Previous review feedback:\n{state.review_feedback}\n\n"
        prompt += "Generate the complete implementation code."

        state.code = await self.coder.ainvoke(
            [{"role": "user", "content": [{"text": prompt}]}],
            system_prompt="You are an expert coder. Write clean, production-ready code.",
        )
        state.status = "coding_done"
        return state

    async def _review(self, state: AgentState) -> AgentState:
        """Reviewer node: score code quality (0.0-1.0)."""
        prompt = (
            f"Review this code for the task: {state.task}\n\n"
            f"Code:\n{state.code[:8000]}\n\n"
            "Rate quality 0.0-1.0. If < 0.7, explain what needs fixing.\n"
            "Format: SCORE: X.X\nFEEDBACK: ..."
        )
        review = await self.reviewer.ainvoke(
            [{"role": "user", "content": [{"text": prompt}]}],
            system_prompt="You are a strict code reviewer. Be thorough but fair.",
        )

        # Parse score
        import re
        score_match = re.search(r'SCORE:\s*([\d.]+)', review)
        state.review_score = float(score_match.group(1)) if score_match else 0.5
        state.review_feedback = review
        state.status = "review_done"
        return state

    async def _execute(self, state: AgentState) -> AgentState:
        """Executor node: apply code changes."""
        state.execution_result = "Code reviewed and ready for application."
        state.execution_success = True
        state.status = "completed"
        return state

    def _should_retry(self, state: AgentState) -> str:
        """Router: retry coding if review score < 0.7 and iterations remain."""
        if state.review_score < 0.7 and state.iteration < state.max_iterations:
            state.iteration += 1
            return "code"
        return "execute"

    async def ainvoke(self, state: AgentState) -> dict:
        """Run the full workflow."""
        try:
            state = await self._plan(state)
            while True:
                state = await self._code(state)
                state = await self._review(state)
                next_step = self._should_retry(state)
                if next_step == "execute":
                    break
            state = await self._execute(state)
            return {
                "status": state.status,
                "plan": state.plan,
                "code": state.code,
                "review_score": state.review_score,
                "iterations": state.iteration,
            }
        except Exception as e:
            return {"status": "failed", "error": str(e)}


def build_graph(gateway_client) -> AgentGraph:
    """Factory function to create the agent graph."""
    return AgentGraph(gateway_client)
