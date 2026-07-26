from __future__ import annotations

from collections import Counter
from typing import Any

from pydantic import Field

from app.agent.state import AgentRun, Observation, StrictModel, utc_now
from app.agent.store import AgentStore
from app.agent.trace import redact_sensitive_data
from app.models import PlanEdge, WorkflowPlan


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
                "operation": run.snapshot.operation,
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
                "viewport": (
                    run.constraints.viewport.model_dump(mode="json")
                    if run.constraints.viewport is not None
                    else None
                ),
                "current_panel": run.constraints.current_panel,
                "context_revision": run.constraints.canvas_context_revision,
                **_selected_graph_context(
                    plan,
                    run,
                    max_nodes=min(self.max_node_summaries, 20),
                    max_edges=40,
                ),
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


def _selected_graph_context(
    plan: WorkflowPlan,
    run: AgentRun,
    *,
    max_nodes: int,
    max_edges: int,
) -> dict[str, Any]:
    node_by_id = {node.id: node for node in plan.nodes}
    selected_ids = [
        node_id
        for node_id in run.constraints.selected_node_ids
        if node_id in node_by_id
    ]
    missing_node_ids = [
        node_id
        for node_id in run.constraints.selected_node_ids
        if node_id not in node_by_id
    ]
    selected_edges = _selected_edges_from_authoritative_graph(run, plan)
    adjacent_ids: list[str] = []
    seen_adjacent = set(selected_ids)
    for edge in plan.edges:
        if edge.source in selected_ids and edge.target not in seen_adjacent:
            adjacent_ids.append(edge.target)
            seen_adjacent.add(edge.target)
        if edge.target in selected_ids and edge.source not in seen_adjacent:
            adjacent_ids.append(edge.source)
            seen_adjacent.add(edge.source)
    adjacent_ids = adjacent_ids[: max(0, max_nodes - len(selected_ids))]
    neighborhood_ids = set(selected_ids) | set(adjacent_ids)
    neighborhood_edges = [
        _edge_summary(edge)
        for edge in plan.edges
        if edge.source in neighborhood_ids or edge.target in neighborhood_ids
    ][:max_edges]
    safe_selected_nodes = [
        redact_sensitive_data(
            node_by_id[node_id].model_dump(mode="json")
        )
        for node_id in selected_ids[:max_nodes]
    ]
    safe_adjacent_nodes = [
        {
            "id": node_by_id[node_id].id,
            "type": node_by_id[node_id].type,
            "title": node_by_id[node_id].title,
        }
        for node_id in adjacent_ids
    ]
    return {
        "selected_nodes": safe_selected_nodes,
        "selected_edges": selected_edges[:max_edges],
        "neighbor_nodes": safe_adjacent_nodes,
        "neighborhood_edges": neighborhood_edges,
        "missing_node_ids": missing_node_ids,
        "missing_edge_ids": [
            edge_id
            for edge_id in run.constraints.selected_edge_ids
            if edge_id
            not in {str(edge.get("id") or "") for edge in selected_edges}
        ],
    }


def _selected_edges_from_authoritative_graph(
    run: AgentRun,
    plan: WorkflowPlan,
) -> list[dict[str, Any]]:
    if run.snapshot is None:
        return []
    plan_edge_keys = {
        (edge.source, edge.source_handle, edge.target, edge.target_handle)
        for edge in plan.edges
    }
    selected = set(run.constraints.selected_edge_ids)
    result: list[dict[str, Any]] = []
    for raw_edge in run.snapshot.base_graph.get("edges") or []:
        if not isinstance(raw_edge, dict):
            continue
        edge_id = str(raw_edge.get("id") or "")
        if edge_id not in selected:
            continue
        source = str(raw_edge.get("source") or "")
        target = str(raw_edge.get("target") or "")
        source_handle = str(
            raw_edge.get("sourceHandle")
            or raw_edge.get("source_handle")
            or "source"
        )
        target_handle = str(
            raw_edge.get("targetHandle")
            or raw_edge.get("target_handle")
            or "target"
        )
        if (source, source_handle, target, target_handle) not in plan_edge_keys:
            continue
        result.append(
            {
                "id": edge_id,
                "source": source,
                "source_handle": source_handle,
                "target": target,
                "target_handle": target_handle,
            }
        )
    return result


def _edge_summary(edge: PlanEdge) -> dict[str, str]:
    return {
        "source": edge.source,
        "source_handle": edge.source_handle,
        "target": edge.target,
        "target_handle": edge.target_handle,
    }
