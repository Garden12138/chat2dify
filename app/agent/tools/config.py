from __future__ import annotations

from typing import Any, Literal

from pydantic import Field

from app.agent.config_app import (
    ConfigReview,
    ConfigReviewService,
    VersionedConfigWorkspace,
)
from app.agent.config_patch import ConfigPatchDocument
from app.agent.registry import (
    ToolExecutionContext,
    ToolPublicError,
    ToolRegistry,
)
from app.agent.state import AgentConfigSnapshot, StrictModel
from app.agent.store import AgentStore
from app.agent.trace import redact_sensitive_data
from app.agent.validation import AgentValidationReport
from app.agent.workspace import WorkspaceOperationError


class ConfigInspectInput(StrictModel):
    view: Literal["summary", "fields", "model", "agent"] = "summary"
    fields: list[str] = Field(default_factory=list, max_length=50)


class ConfigInspectOutput(StrictModel):
    workspace_version: str
    summary: dict[str, Any]
    config: dict[str, Any] = Field(default_factory=dict)


class ConfigPatchOutput(StrictModel):
    workspace_version: str
    parent_version: str
    validation: AgentValidationReport
    risk: str


class ConfigValidateInput(StrictModel):
    workspace_version: str | None = Field(default=None, max_length=128)


class ConfigValidateOutput(StrictModel):
    workspace_version: str
    validation: AgentValidationReport


class ConfigDiffInput(StrictModel):
    workspace_version: str | None = Field(default=None, max_length=128)


class ConfigDiffOutput(ConfigReview):
    pass


def register_config_tools(
    registry: ToolRegistry,
    *,
    store: AgentStore,
    workspace: VersionedConfigWorkspace,
    review: ConfigReviewService,
) -> None:
    registry.register(
        name="config.inspect",
        version="1.0.0",
        description=(
            "Inspect a bounded, sanitized configured-app model configuration."
        ),
        side_effect="none",
        approval="never",
        input_model=ConfigInspectInput,
        output_model=ConfigInspectOutput,
        executor=lambda arguments, context: _inspect(
            arguments,
            context,
            store=store,
        ),
    )
    registry.register(
        name="config.patch",
        version="1.0.0",
        description=(
            "Apply a separate typed ConfigPatchIR transaction to the Config "
            "Workspace."
        ),
        side_effect="workspace",
        approval="never",
        input_model=ConfigPatchDocument,
        output_model=ConfigPatchOutput,
        executor=lambda arguments, context: _patch(
            arguments,
            context,
            workspace=workspace,
        ),
    )
    registry.register(
        name="config.validate",
        version="1.0.0",
        description=(
            "Validate the current configured-app Workspace deterministically."
        ),
        side_effect="none",
        approval="never",
        input_model=ConfigValidateInput,
        output_model=ConfigValidateOutput,
        executor=lambda arguments, context: _validate(
            arguments,
            context,
            workspace=workspace,
        ),
    )
    registry.register(
        name="config.diff",
        version="1.0.0",
        description=(
            "Build the configured-app business Diff, technical Diff, and risk."
        ),
        side_effect="none",
        approval="never",
        input_model=ConfigDiffInput,
        output_model=ConfigDiffOutput,
        executor=lambda arguments, context: _diff(
            arguments,
            context,
            store=store,
            review=review,
        ),
    )


def _inspect(
    arguments: ConfigInspectInput,
    context: ToolExecutionContext,
    *,
    store: AgentStore,
) -> ConfigInspectOutput:
    run = store.get_run(_run_id(context))
    if not isinstance(run.snapshot, AgentConfigSnapshot):
        raise ToolPublicError(
            "CONFIG_WORKSPACE_REQUIRED",
            "config.inspect requires a configured-app Agent Run.",
        )
    head = store.get_workspace_head(run.id)
    config = head.snapshot
    selected: dict[str, Any] = {}
    if arguments.view == "model":
        selected = {"model": config.get("model")}
    elif arguments.view == "agent":
        if run.snapshot.app_mode != "agent-chat":
            raise ToolPublicError(
                "CONFIG_AGENT_VIEW_UNSUPPORTED",
                "The agent view is available only for agent-chat apps.",
            )
        selected = {"agent_mode": config.get("agent_mode")}
    elif arguments.view == "fields":
        allowed = {
            "pre_prompt",
            "opening_statement",
            "suggested_questions",
            "file_upload",
            "dataset_configs",
            "model",
            "agent_mode",
        }
        unknown = sorted(set(arguments.fields) - allowed)
        if unknown:
            raise ToolPublicError(
                "CONFIG_INSPECT_FIELD_UNSUPPORTED",
                "config.inspect requested unsupported fields.",
                details=[{"fields": unknown}],
            )
        selected = {
            field: config.get(field)
            for field in arguments.fields
        }
    model = config.get("model") if isinstance(config.get("model"), dict) else {}
    return ConfigInspectOutput(
        workspace_version=head.id,
        summary={
            "app_mode": run.snapshot.app_mode,
            "field_count": len(config),
            "fields": sorted(str(key) for key in config)[:100],
            "model": {
                "provider": model.get("provider"),
                "name": model.get("name"),
                "mode": model.get("mode"),
            },
            "compatibility": run.snapshot.compatibility,
        },
        config=redact_sensitive_data(selected),
    )


def _patch(
    arguments: ConfigPatchDocument,
    context: ToolExecutionContext,
    *,
    workspace: VersionedConfigWorkspace,
) -> ConfigPatchOutput:
    try:
        result = workspace.apply_patch(_run_id(context), arguments)
    except WorkspaceOperationError as exc:
        raise ToolPublicError(
            exc.code,
            str(exc),
            details=exc.details,
            retryable=exc.retryable,
        ) from exc
    return ConfigPatchOutput.model_validate(
        result.model_dump(mode="json")
    )


def _validate(
    arguments: ConfigValidateInput,
    context: ToolExecutionContext,
    *,
    workspace: VersionedConfigWorkspace,
) -> ConfigValidateOutput:
    run_id = _run_id(context)
    head = workspace.store.get_workspace_head(run_id)
    if (
        arguments.workspace_version is not None
        and arguments.workspace_version != head.id
    ):
        raise ToolPublicError(
            "WORKSPACE_VERSION_MISMATCH",
            "config.validate can validate only the current Workspace head.",
        )
    return ConfigValidateOutput(
        workspace_version=head.id,
        validation=workspace.validate_head(run_id),
    )


def _diff(
    arguments: ConfigDiffInput,
    context: ToolExecutionContext,
    *,
    store: AgentStore,
    review: ConfigReviewService,
) -> ConfigDiffOutput:
    run_id = _run_id(context)
    head = store.get_workspace_head(run_id)
    if (
        arguments.workspace_version is not None
        and arguments.workspace_version != head.id
    ):
        raise ToolPublicError(
            "WORKSPACE_VERSION_MISMATCH",
            "config.diff can review only the current Workspace head.",
        )
    return ConfigDiffOutput.model_validate(
        review.build(run_id).model_dump(mode="json")
    )


def _run_id(context: ToolExecutionContext) -> str:
    if not context.run_id:
        raise ToolPublicError(
            "TOOL_RUN_SCOPE_REQUIRED",
            "This Tool requires an Agent Run scope.",
        )
    return context.run_id
