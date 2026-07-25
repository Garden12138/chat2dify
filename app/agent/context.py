from __future__ import annotations

from collections import Counter
from typing import Any

from pydantic import Field

from app.agent.state import AgentRun, Observation, StrictModel, utc_now
from app.agent.store import AgentStore
from app.models import WorkflowPlan


class RemainingBudget(StrictModel):
    iterations: int
    model_calls: int
    patch_operations: int
    test_runs: int
    run_seconds: int


class BuilderContext(StrictModel):
    goal: str
    goal_plan: dict[str, Any]
    app: dict[str, Any]
    workspace: dict[str, Any]
    selection: dict[str, Any]
    capabilities: list[dict[str, Any]] = Field(default_factory=list)
    latest_validation: dict[str, Any] | None = None
    recent_observations: list[Observation] = Field(default_factory=list)
    older_observation_summary: dict[str, Any] = Field(default_factory=dict)
    trace_summary: dict[str, int] = Field(default_factory=dict)
    constraints: dict[str, Any]
    remaining_budget: RemainingBudget


class BuilderContextBuilder:
    def __init__(
        self,
        *,
        store: AgentStore,
        max_recent_observations: int = 8,
        max_capabilities: int = 20,
        max_node_summaries: int = 50,
    ) -> None:
        self.store = store
        self.max_recent_observations = max_recent_observations
        self.max_capabilities = max_capabilities
        self.max_node_summaries = max_node_summaries

    def build(self, run: AgentRun) -> BuilderContext:
        if run.snapshot is None or run.goal_plan is None:
            raise ValueError("Builder Context requires Snapshot and Goal Plan checkpoints.")
        head = self.store.get_workspace_head(run.id)
        plan = WorkflowPlan.model_validate(head.snapshot)
        recent = run.observations[-self.max_recent_observations :]
        older = run.observations[: -self.max_recent_observations]
        events = self.store.list_events(run.id, limit=10_000)
        elapsed = max(0, int((utc_now() - run.created_at).total_seconds()))
        return BuilderContext(
            goal=run.goal,
            goal_plan=run.goal_plan.model_dump(mode="json"),
            app={
                "id": run.snapshot.app_id,
                "name": run.snapshot.app_name,
                "description": run.snapshot.app_description,
                "mode": run.snapshot.app_mode,
                "base_hash": run.snapshot.base_hash,
                "dify_version": run.snapshot.dify_version,
            },
            workspace={
                "version": head.id,
                "node_count": len(plan.nodes),
                "edge_count": len(plan.edges),
                "conversation_variable_count": len(plan.conversation_variables),
                "nodes": [
                    {
                        "id": node.id,
                        "type": node.type,
                        "title": node.title,
                    }
                    for node in plan.nodes[: self.max_node_summaries]
                ],
            },
            selection={
                "node_ids": run.constraints.selected_node_ids,
                "edge_ids": run.constraints.selected_edge_ids,
            },
            capabilities=run.snapshot.capabilities[: self.max_capabilities],
            latest_validation=head.validation,
            recent_observations=recent,
            older_observation_summary={
                "count": len(older),
                "kinds": dict(Counter(item.kind for item in older)),
            },
            trace_summary=dict(Counter(event.type for event in events)),
            constraints=run.constraints.model_dump(mode="json"),
            remaining_budget=RemainingBudget(
                iterations=max(
                    0,
                    run.budget.max_iterations - run.budget_usage.iterations,
                ),
                model_calls=max(
                    0,
                    run.budget.max_model_calls - run.budget_usage.model_calls,
                ),
                patch_operations=max(
                    0,
                    run.budget.max_patch_operations
                    - run.budget_usage.patch_operations,
                ),
                test_runs=max(
                    0,
                    run.budget.max_test_runs - run.budget_usage.test_runs,
                ),
                run_seconds=max(0, run.budget.max_run_seconds - elapsed),
            ),
        )
