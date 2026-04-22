"""Agent workflow state definition for LangGraph."""
from dataclasses import dataclass, field
from typing import Any


@dataclass
class AgentState:
    """Shared state passed between graph nodes."""
    task: str = ""
    messages: list = field(default_factory=list)
    plan: str = ""
    code: str = ""
    review_score: float = 0.0
    review_feedback: str = ""
    execution_result: str = ""
    execution_success: bool = False
    iteration: int = 0
    max_iterations: int = 3
    error: str = ""
    status: str = "pending"  # pending | running | completed | failed
