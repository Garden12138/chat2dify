from __future__ import annotations

from pydantic import Field

from app.agent.execution import (
    DraftPreparationError,
    DraftRunService,
    DraftTestRequest,
    DraftTestResult,
    ExecutionObservation,
)
from app.agent.registry import (
    ToolExecutionContext,
    ToolPublicError,
    ToolRegistry,
)
from app.agent.state import StrictModel
from app.agent.store import AgentStore


class ExecutionInspectInput(StrictModel):
    workspace_version: str | None = Field(default=None, max_length=128)


class ExecutionInspectOutput(StrictModel):
    workspace_version: str
    execution: ExecutionObservation


def register_phase3_tools(
    registry: ToolRegistry,
    *,
    store: AgentStore,
    draft_runs: DraftRunService,
) -> None:
    registry.register(
        name="workflow.test_draft",
        version="1.0.0",
        description=(
            "Run an approved Workflow or Chatflow draft with bounded inputs and "
            "return only a sanitized execution summary."
        ),
        side_effect="draft_run",
        approval="policy",
        input_model=DraftTestRequest,
        output_model=DraftTestResult,
        executor=lambda arguments, context: _test_draft(
            arguments,
            context,
            draft_runs=draft_runs,
        ),
    )
    registry.register(
        name="execution.inspect",
        version="1.0.0",
        description=(
            "Inspect the latest normalized Draft Run result for the current "
            "Workspace version."
        ),
        side_effect="none",
        approval="never",
        input_model=ExecutionInspectInput,
        output_model=ExecutionInspectOutput,
        executor=lambda arguments, context: _inspect_execution(
            arguments,
            context,
            store=store,
        ),
    )


def _test_draft(
    arguments: DraftTestRequest,
    context: ToolExecutionContext,
    *,
    draft_runs: DraftRunService,
) -> DraftTestResult:
    try:
        return draft_runs.execute(_run_id(context), arguments)
    except DraftPreparationError as exc:
        raise ToolPublicError(
            exc.code,
            str(exc),
            details=exc.details,
            retryable=exc.retryable,
        ) from exc


def _inspect_execution(
    arguments: ExecutionInspectInput,
    context: ToolExecutionContext,
    *,
    store: AgentStore,
) -> ExecutionInspectOutput:
    head = store.get_workspace_head(_run_id(context))
    if arguments.workspace_version and arguments.workspace_version != head.id:
        raise ToolPublicError(
            "WORKSPACE_VERSION_MISMATCH",
            "execution.inspect can inspect only the current Workspace head.",
        )
    if head.test_result is None:
        raise ToolPublicError(
            "EXECUTION_OBSERVATION_MISSING",
            "The current Workspace version has no Draft Run observation.",
        )
    raw_execution = (
        head.test_result.get("execution")
        if isinstance(head.test_result.get("execution"), dict)
        else head.test_result
    )
    return ExecutionInspectOutput(
        workspace_version=head.id,
        execution=ExecutionObservation.model_validate(raw_execution),
    )


def _run_id(context: ToolExecutionContext) -> str:
    if not context.run_id:
        raise ToolPublicError(
            "TOOL_RUN_SCOPE_REQUIRED",
            "This Tool requires an Agent Run scope.",
        )
    return context.run_id
