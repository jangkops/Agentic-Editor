"""Multi-agent coordinator: Planner → Generator → Evaluator loop."""
from typing import Dict, Any


class AgentCoordinator:
    def __init__(self, planner, generator, evaluator):
        self.planner = planner
        self.generator = generator
        self.evaluator = evaluator
        self.max_iterations = 3

    async def run(
        self,
        prompt: str,
        checkpoint_callback=None,
        iteration: int = 0,
    ) -> Dict[str, Any]:
        plan = await self.planner.generate_plan(prompt)
        if checkpoint_callback:
            await checkpoint_callback({
                "step": "planned",
                "plan": plan,
                "iteration": iteration,
            })

        code = await self.generator.generate_code(plan)
        if checkpoint_callback:
            await checkpoint_callback({
                "step": "generated",
                "code": code,
                "iteration": iteration,
            })

        evaluation = await self.evaluator.evaluate(code)
        if checkpoint_callback:
            await checkpoint_callback({
                "step": "evaluated",
                "evaluation": evaluation,
                "iteration": iteration,
            })

        if evaluation["score"] < 0.8 and iteration < self.max_iterations:
            refined = f"{prompt}\n\nPrevious attempt feedback: {evaluation['feedback']}"
            return await self.run(refined, checkpoint_callback, iteration + 1)

        return {
            "result": code,
            "evaluation": evaluation,
            "iterations": iteration + 1,
            "plan": plan,
        }
