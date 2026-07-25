from __future__ import annotations

from typing import Literal

from app.agent.registry import ToolSpec
from app.agent.state import AgentRun, StrictModel


class ToolAuthorization(StrictModel):
    allowed: bool
    requires_approval: bool = False
    code: str | None = None
    message: str | None = None


class AgentToolPolicy:
    def authorize(
        self,
        spec: ToolSpec,
        run: AgentRun,
    ) -> ToolAuthorization:
        del run
        if spec.side_effect in {"none", "workspace"} and spec.approval == "never":
            return ToolAuthorization(allowed=True)
        if spec.side_effect == "draft_run":
            return ToolAuthorization(
                allowed=False,
                code="TOOL_PHASE_NOT_AVAILABLE",
                message="Draft Run tools are not available before Phase 3.",
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
