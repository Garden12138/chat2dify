from __future__ import annotations

from typing import Any

from pydantic import Field

from app.agent.diff import diff_plans
from app.agent.guard import guard_plan_change
from app.agent.state import AgentRun, StrictModel, utc_now
from app.agent.store import AgentStore
from app.agent.trace import redact_sensitive_data
from app.agent.validation import AgentValidationReport
from app.agent.workspace import VersionedWorkflowWorkspace
from app.models import WorkflowPlan


class WorkflowReview(StrictModel):
    workspace_version_id: str
    ready: bool
    validation: AgentValidationReport
    business_diff: list[str] = Field(default_factory=list)
    technical_diff: list[dict[str, Any]] = Field(default_factory=list)
    risk: dict[str, Any]


class WorkflowReviewService:
    def __init__(
        self,
        *,
        store: AgentStore,
        workspace: VersionedWorkflowWorkspace,
    ) -> None:
        self.store = store
        self.workspace = workspace

    def build(self, run_id: str) -> WorkflowReview:
        run = self.store.get_run(run_id)
        if run.snapshot is None:
            raise ValueError("Agent Run does not have an authoritative Snapshot.")
        head = self.store.get_workspace_head(run_id)
        before = WorkflowPlan.model_validate(run.snapshot.base_plan)
        after = WorkflowPlan.model_validate(head.snapshot)
        validation = self.workspace.validate_head(run_id)
        changes = diff_plans(before, after)
        guard = guard_plan_change(before, after, changes)
        safe_changes = redact_sensitive_data(changes)
        review = WorkflowReview(
            workspace_version_id=head.id,
            ready=validation.ok,
            validation=validation,
            business_diff=[
                str(change.get("message") or change.get("type") or "changed")
                for change in safe_changes
            ],
            technical_diff=safe_changes,
            risk=redact_sensitive_data(guard.to_dict()),
        )
        updated = AgentRun.model_validate(
            {
                **run.model_dump(),
                "review": review.model_dump(mode="json"),
                "updated_at": utc_now(),
            }
        )
        self.store.update_run(updated)
        return review
