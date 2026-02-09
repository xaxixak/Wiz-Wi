from enum import Enum
from dataclasses import dataclass
from typing import Optional


class AnalysisTask(str, Enum):
    """Types of LLM analysis tasks."""
    FILE_CLASSIFICATION = "file_classification"     # What does this file do?
    EDGE_DISCOVERY = "edge_discovery"               # What operational edges exist?
    NODE_DISCOVERY = "node_discovery"                # What entities did static analysis miss?
    CONFLICT_RESOLUTION = "conflict_resolution"     # Resolve conflicting edges/types
    DESCRIPTION_GENERATION = "description_generation"  # Generate node descriptions
    INVARIANT_DETECTION = "invariant_detection"      # Detect business rules


@dataclass
class ModelConfig:
    model_id: str
    max_tokens: int
    temperature: float
    cost_per_1m_input: float
    cost_per_1m_output: float


class ModelRouter:
    """Routes analysis tasks to the appropriate Claude model."""

    MODELS = {
        "haiku": ModelConfig("claude-haiku-4-5-20251001", 2048, 0.0, 0.80, 4.0),
        "sonnet": ModelConfig("claude-sonnet-4-5-20250929", 4096, 0.0, 3.0, 15.0),
        "opus": ModelConfig("claude-opus-4-6", 4096, 0.0, 15.0, 75.0),
    }

    # Default routing rules
    TASK_ROUTES = {
        AnalysisTask.FILE_CLASSIFICATION: "haiku",
        AnalysisTask.EDGE_DISCOVERY: "sonnet",
        AnalysisTask.NODE_DISCOVERY: "sonnet",
        AnalysisTask.CONFLICT_RESOLUTION: "opus",
        AnalysisTask.DESCRIPTION_GENERATION: "haiku",
        AnalysisTask.INVARIANT_DETECTION: "sonnet",
    }

    def __init__(self, overrides: dict = None, budget_limit: float = None):
        """
        Args:
            overrides: Override routing rules {AnalysisTask: "haiku"/"sonnet"/"opus"}
            budget_limit: Max spend in dollars. When reached, downgrade models.
        """
        self.overrides = overrides or {}
        self.budget_limit = budget_limit
        self.total_spend = 0.0
        self.task_spend = {task: 0.0 for task in AnalysisTask}

    def route(self, task: AnalysisTask, file_complexity: str = "medium") -> ModelConfig:
        """
        Get the model config for a task.

        Args:
            task: The analysis task type.
            file_complexity: "low"/"medium"/"high" — can upgrade model for complex files.

        Returns:
            ModelConfig for the selected model.
        """
        # Get base model from overrides or default routes
        if task in self.overrides:
            base_model = self.overrides[task]
        else:
            base_model = self.TASK_ROUTES[task]

        # Upgrade for high complexity files
        if file_complexity == "high" and base_model == "haiku":
            base_model = "sonnet"

        # Apply budget-based downgrade if needed
        if self.budget_limit is not None:
            budget_usage = self.total_spend / self.budget_limit

            if budget_usage >= 1.0:
                # 100%+ budget: force haiku
                base_model = "haiku"
            elif budget_usage >= 0.90:
                # 90%+ budget: downgrade to sonnet/haiku
                if base_model == "opus":
                    base_model = "sonnet"
                elif base_model == "sonnet":
                    base_model = "haiku"
            elif budget_usage >= 0.75:
                # 75%+ budget: downgrade opus to sonnet
                if base_model == "opus":
                    base_model = "sonnet"

        return self.MODELS[base_model]

    def estimate_cost(self, task: AnalysisTask, input_tokens: int, output_tokens: int) -> float:
        """
        Estimate cost for a task.

        Args:
            task: The analysis task type.
            input_tokens: Number of input tokens.
            output_tokens: Number of output tokens.

        Returns:
            Estimated cost in dollars.
        """
        model_config = self.route(task)

        # Cost is per 1 million tokens, convert to actual token count
        input_cost = (input_tokens / 1_000_000) * model_config.cost_per_1m_input
        output_cost = (output_tokens / 1_000_000) * model_config.cost_per_1m_output

        return input_cost + output_cost

    def track_spend(self, cost: float, task: AnalysisTask = None) -> None:
        """
        Track actual spend. If over budget, future routes downgrade.

        Args:
            cost: Actual cost in dollars.
            task: Optional task type for per-task tracking.
        """
        self.total_spend += cost

        if task is not None:
            self.task_spend[task] = self.task_spend.get(task, 0.0) + cost

    def get_spend_summary(self) -> dict:
        """
        Return spend tracking summary.

        Returns:
            Dictionary with spend statistics:
            - total_spend: Total spend in dollars
            - budget_limit: Budget limit or None
            - budget_usage: Percentage of budget used (0-100) or None
            - task_breakdown: Per-task spend
        """
        summary = {
            "total_spend": self.total_spend,
            "budget_limit": self.budget_limit,
            "budget_usage": None,
            "task_breakdown": dict(self.task_spend)
        }

        if self.budget_limit is not None:
            summary["budget_usage"] = (self.total_spend / self.budget_limit) * 100

        return summary
