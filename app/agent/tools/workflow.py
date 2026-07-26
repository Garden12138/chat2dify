from __future__ import annotations

from collections import deque
from typing import Any, Literal

from pydantic import Field

from app.agent.patch import PatchDocument
from app.agent.registry import (
    ToolExecutionContext,
    ToolPublicError,
    ToolRegistry,
)
from app.agent.review import WorkflowReview, WorkflowReviewService
from app.agent.state import StrictModel
from app.agent.store import AgentStore
from app.agent.trace import redact_sensitive_data
from app.agent.validation import AgentValidationReport
from app.agent.workspace import (
    PatchApplyResult,
    VersionedWorkflowWorkspace,
    WorkspaceOperationError,
)
from app.models import WorkflowPlan


class WorkflowInspectInput(StrictModel):
    view: Literal["summary", "nodes", "neighborhood", "variables"] = "summary"
    node_ids: list[str] = Field(default_factory=list, max_length=20)
    depth: int = Field(default=1, ge=0, le=2)
    limit: int = Field(default=20, ge=1, le=50)


class WorkflowInspectOutput(StrictModel):
    workspace_version: str
    summary: dict[str, Any]
    nodes: list[dict[str, Any]] = Field(default_factory=list)
    edges: list[dict[str, Any]] = Field(default_factory=list)
    conversation_variables: list[dict[str, Any]] = Field(default_factory=list)
    untrusted_data: bool = True


class CapabilitySearchInput(StrictModel):
    query: str = Field(default="", max_length=256)
    limit: int = Field(default=10, ge=1, le=20)


class CapabilitySearchOutput(StrictModel):
    capabilities: list[dict[str, Any]]
    pinned: bool = True
    untrusted_data: bool = True


class NodeSchemaGetInput(StrictModel):
    node_type: str = Field(min_length=1, max_length=128)


class NodeSchemaGetOutput(StrictModel):
    definition: dict[str, Any]
    pinned: bool = True


class WorkflowPatchOutput(PatchApplyResult):
    pass


class WorkflowValidateInput(StrictModel):
    workspace_version: str | None = Field(default=None, max_length=128)


class WorkflowValidateOutput(StrictModel):
    workspace_version: str
    validation: AgentValidationReport


class WorkflowDiffInput(StrictModel):
    workspace_version: str | None = Field(default=None, max_length=128)


class WorkflowDiffOutput(WorkflowReview):
    pass


def register_phase1a_tools(
    registry: ToolRegistry,
    *,
    store: AgentStore,
    workspace: VersionedWorkflowWorkspace,
    review: WorkflowReviewService,
) -> None:
    registry.register(
        name="workflow.inspect",
        version="1.0.0",
        description="Inspect a bounded summary, node set, neighborhood, or variables.",
        side_effect="none",
        approval="never",
        input_model=WorkflowInspectInput,
        output_model=WorkflowInspectOutput,
        executor=lambda arguments, context: _inspect(
            arguments,
            context,
            store=store,
        ),
    )
    registry.register(
        name="capability.search",
        version="1.0.0",
        description="Search the capability definitions pinned to this Agent Run.",
        side_effect="none",
        approval="never",
        input_model=CapabilitySearchInput,
        output_model=CapabilitySearchOutput,
        executor=lambda arguments, context: _capability_search(
            arguments,
            context,
            store=store,
        ),
    )
    registry.register(
        name="node.schema.get",
        version="1.0.0",
        description="Read one node definition pinned to this Agent Run.",
        side_effect="none",
        approval="never",
        input_model=NodeSchemaGetInput,
        output_model=NodeSchemaGetOutput,
        executor=lambda arguments, context: _node_schema(
            arguments,
            context,
            store=store,
        ),
    )
    registry.register(
        name="workflow.patch",
        version="1.0.0",
        description="Apply one transactional typed Patch to the versioned Workspace.",
        side_effect="workspace",
        approval="never",
        input_model=PatchDocument,
        output_model=WorkflowPatchOutput,
        executor=lambda arguments, context: _patch(
            arguments,
            context,
            workspace=workspace,
        ),
    )
    registry.register(
        name="workflow.validate",
        version="1.0.0",
        description="Run the existing deterministic validation and preflight chain.",
        side_effect="none",
        approval="never",
        input_model=WorkflowValidateInput,
        output_model=WorkflowValidateOutput,
        executor=lambda arguments, context: _validate(
            arguments,
            context,
            workspace=workspace,
        ),
    )
    registry.register(
        name="workflow.diff",
        version="1.0.0",
        description="Build business, technical, validation, and risk review data.",
        side_effect="none",
        approval="never",
        input_model=WorkflowDiffInput,
        output_model=WorkflowDiffOutput,
        executor=lambda arguments, context: _diff(
            arguments,
            context,
            workspace=workspace,
            review=review,
        ),
    )


def _inspect(
    arguments: WorkflowInspectInput,
    context: ToolExecutionContext,
    *,
    store: AgentStore,
) -> WorkflowInspectOutput:
    run_id = _run_id(context)
    head = store.get_workspace_head(run_id)
    plan = WorkflowPlan.model_validate(head.snapshot)
    node_by_id = {node.id: node for node in plan.nodes}
    selected_ids: set[str] = set()
    if arguments.view in {"nodes", "neighborhood"}:
        selected_ids = set(arguments.node_ids)
        unknown = sorted(selected_ids - node_by_id.keys())
        if unknown:
            raise ToolPublicError(
                "WORKFLOW_INSPECT_NODE_NOT_FOUND",
                "workflow.inspect references unknown node IDs.",
                details=[{"node_ids": unknown}],
            )
    if arguments.view == "neighborhood":
        selected_ids = _neighborhood(
            selected_ids,
            [edge.model_dump(mode="json") for edge in plan.edges],
            arguments.depth,
        )
    nodes = []
    if arguments.view in {"nodes", "neighborhood"}:
        nodes = [
            redact_sensitive_data(node_by_id[node_id].model_dump(mode="json"))
            for node_id in sorted(selected_ids)[: arguments.limit]
        ]
    edges = [
        edge.model_dump(mode="json")
        for edge in plan.edges
        if not selected_ids
        or (edge.source in selected_ids and edge.target in selected_ids)
    ][: arguments.limit]
    variables = (
        [
            redact_sensitive_data(variable.model_dump(mode="json"))
            for variable in plan.conversation_variables[: arguments.limit]
        ]
        if arguments.view == "variables"
        else []
    )
    return WorkflowInspectOutput(
        workspace_version=head.id,
        summary={
            "name": plan.name,
            "app_mode": plan.app_mode,
            "node_count": len(plan.nodes),
            "edge_count": len(plan.edges),
            "conversation_variable_count": len(plan.conversation_variables),
        },
        nodes=nodes,
        edges=edges,
        conversation_variables=variables,
    )


def _capability_search(
    arguments: CapabilitySearchInput,
    context: ToolExecutionContext,
    *,
    store: AgentStore,
) -> CapabilitySearchOutput:
    run = store.get_run(_run_id(context))
    if run.snapshot is None:
        raise ToolPublicError(
            "AGENT_SNAPSHOT_MISSING",
            "Agent Run does not have a pinned capability Snapshot.",
        )
    needle = arguments.query.strip().lower()
    matches = [
        item
        for item in run.snapshot.capabilities
        if not needle
        or needle in str(item.get("type") or "").lower()
        or needle in str(item.get("summary") or "").lower()
    ]
    return CapabilitySearchOutput(
        capabilities=matches[: arguments.limit],
    )


def _node_schema(
    arguments: NodeSchemaGetInput,
    context: ToolExecutionContext,
    *,
    store: AgentStore,
) -> NodeSchemaGetOutput:
    run = store.get_run(_run_id(context))
    if run.snapshot is None:
        raise ToolPublicError(
            "AGENT_SNAPSHOT_MISSING",
            "Agent Run does not have a pinned capability Snapshot.",
        )
    for item in run.snapshot.capabilities:
        if item.get("type") == arguments.node_type:
            return NodeSchemaGetOutput(definition=item)
    raise ToolPublicError(
        "NODE_SCHEMA_NOT_FOUND",
        f"No pinned node schema exists for {arguments.node_type}.",
    )


def _patch(
    arguments: PatchDocument,
    context: ToolExecutionContext,
    *,
    workspace: VersionedWorkflowWorkspace,
) -> WorkflowPatchOutput:
    try:
        result = workspace.apply_patch(_run_id(context), arguments)
    except WorkspaceOperationError as exc:
        raise ToolPublicError(
            exc.code,
            str(exc),
            details=exc.details,
            retryable=exc.retryable,
        ) from exc
    return WorkflowPatchOutput.model_validate(result.model_dump(mode="json"))


def _validate(
    arguments: WorkflowValidateInput,
    context: ToolExecutionContext,
    *,
    workspace: VersionedWorkflowWorkspace,
) -> WorkflowValidateOutput:
    run_id = _run_id(context)
    head = workspace.head(run_id)
    if arguments.workspace_version and arguments.workspace_version != head.id:
        raise ToolPublicError(
            "WORKSPACE_VERSION_MISMATCH",
            "workflow.validate can validate only the current Workspace head.",
        )
    return WorkflowValidateOutput(
        workspace_version=head.id,
        validation=workspace.validate_head(run_id),
    )


def _diff(
    arguments: WorkflowDiffInput,
    context: ToolExecutionContext,
    *,
    workspace: VersionedWorkflowWorkspace,
    review: WorkflowReviewService,
) -> WorkflowDiffOutput:
    run_id = _run_id(context)
    head = workspace.head(run_id)
    if arguments.workspace_version and arguments.workspace_version != head.id:
        raise ToolPublicError(
            "WORKSPACE_VERSION_MISMATCH",
            "workflow.diff can review only the current Workspace head.",
        )
    return WorkflowDiffOutput.model_validate(
        review.build(run_id).model_dump(mode="json")
    )


def _run_id(context: ToolExecutionContext) -> str:
    if not context.run_id:
        raise ToolPublicError(
            "TOOL_RUN_SCOPE_REQUIRED",
            "This Tool requires an Agent Run scope.",
        )
    return context.run_id


def _neighborhood(
    initial: set[str],
    edges: list[dict[str, Any]],
    depth: int,
) -> set[str]:
    selected = set(initial)
    queue = deque((node_id, 0) for node_id in initial)
    adjacency: dict[str, set[str]] = {}
    for edge in edges:
        source = str(edge["source"])
        target = str(edge["target"])
        adjacency.setdefault(source, set()).add(target)
        adjacency.setdefault(target, set()).add(source)
    while queue:
        node_id, level = queue.popleft()
        if level >= depth:
            continue
        for neighbor in adjacency.get(node_id, set()):
            if neighbor in selected:
                continue
            selected.add(neighbor)
            queue.append((neighbor, level + 1))
    return selected
