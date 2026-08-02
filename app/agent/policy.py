from __future__ import annotations

from typing import Any, Literal

from pydantic import Field

from app.agent.execution import (
    DraftPreparationError,
    find_matching_draft_approval,
    prepare_draft_test,
)
from app.agent.registry import ToolSpec
from app.agent.state import AgentRun, StrictModel
from app.agent.store import AgentStore


class ToolAuthorization(StrictModel):
    allowed: bool
    requires_approval: bool = False
    code: str | None = None
    message: str | None = None
    details: list[dict[str, Any]] = Field(default_factory=list)
    approval_scope: dict[str, Any] = Field(default_factory=dict)


class AgentToolPolicy:
    def __init__(
        self,
        *,
        store: AgentStore | None = None,
        supports_candidate_workspace: bool = True,
    ) -> None:
        self.store = store
        self.supports_candidate_workspace = supports_candidate_workspace

    def authorize(
        self,
        spec: ToolSpec,
        run: AgentRun,
        arguments: dict[str, Any] | None = None,
        *,
        goal_step_id: str = "test",
    ) -> ToolAuthorization:
        if run.constraints.read_only and spec.side_effect != "none":
            return ToolAuthorization(
                allowed=False,
                code="READ_ONLY_TOOL_FORBIDDEN",
                message="This Run may inspect and explain, but cannot mutate a Workspace or execute side effects.",
            )
        if spec.side_effect in {"none", "workspace"} and spec.approval == "never":
            return ToolAuthorization(allowed=True)
        if spec.side_effect == "draft_run":
            if self.store is None:
                return ToolAuthorization(
                    allowed=False,
                    code="DRAFT_TEST_POLICY_UNAVAILABLE",
                    message="Draft Run policy storage is unavailable.",
                )
            try:
                prepared = prepare_draft_test(
                    self.store,
                    run,
                    arguments or {},
                )
            except DraftPreparationError as exc:
                return ToolAuthorization(
                    allowed=False,
                    code=exc.code,
                    message=str(exc),
                    details=exc.details,
                )
            if prepared.candidate_changed and not self.supports_candidate_workspace:
                return ToolAuthorization(
                    allowed=False,
                    code="DRAFT_TEST_CANDIDATE_GRAPH_UNSUPPORTED",
                    message=(
                        "The configured Dify Draft Run API cannot execute an "
                        "uncommitted Agent Workspace candidate."
                    ),
                )
            if find_matching_draft_approval(self.store, run, prepared) is not None:
                return ToolAuthorization(allowed=True)
            return ToolAuthorization(
                allowed=False,
                requires_approval=True,
                code="DRAFT_RUN_APPROVAL_REQUIRED",
                message="Draft Run requires persisted user approval.",
                approval_scope=prepared.approval_scope(
                    run,
                    goal_step_id=goal_step_id,
                ),
            )
        if spec.side_effect == "dify_write":
            return ToolAuthorization(
                allowed=False,
                code="TOOL_DIFY_WRITE_FORBIDDEN",
                message="Dify writes are execution-service actions, not model tools.",
            )
        return ToolAuthorization(
            allowed=False,
            code="TOOL_APPROVAL_POLICY_DENIED",
            message="The Tool is not authorized by the current server policy.",
        )


ToolSideEffect = Literal["none", "workspace", "draft_run", "dify_write"]
