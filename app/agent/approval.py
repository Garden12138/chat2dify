from __future__ import annotations

from datetime import timedelta
from typing import Literal

from app.agent.review import WorkflowReview
from app.agent.state import (
    AgentApproval,
    AgentRun,
    ApprovalStatus,
    RunPhase,
    utc_now,
)
from app.agent.store import AgentStore


class ApprovalServiceError(RuntimeError):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


class AgentApprovalService:
    def __init__(
        self,
        *,
        store: AgentStore,
        approval_ttl: timedelta = timedelta(minutes=30),
    ) -> None:
        self.store = store
        self.approval_ttl = approval_ttl

    def request_for_review(
        self,
        run_id: str,
        review: WorkflowReview,
    ) -> AgentApproval:
        run = self.store.get_run(run_id)
        if run.head_version_id != review.workspace_version_id:
            raise ApprovalServiceError(
                "APPROVAL_WORKSPACE_VERSION_MISMATCH",
                "Review no longer matches the Workspace head.",
            )
        action: Literal["commit", "destructive_change"] = (
            "destructive_change"
            if review.risk.get("risk") == "high"
            or not bool(review.risk.get("ok", True))
            else "commit"
        )
        return self._create_or_reuse(
            run,
            action=action,
            risk=str(review.risk.get("risk") or "low"),
        )

    def resolve(
        self,
        run_id: str,
        approval_id: str,
        *,
        approved: bool,
    ) -> tuple[AgentApproval, AgentApproval | None]:
        run = self.store.get_run(run_id)
        approval = self.store.get_approval(approval_id)
        self._assert_bound(run, approval)
        now = utc_now()
        if approval.status == ApprovalStatus.APPROVED and approved:
            return approval, None
        if approval.status != ApprovalStatus.PENDING:
            raise ApprovalServiceError(
                "APPROVAL_ALREADY_RESOLVED",
                "Approval has already been resolved.",
            )
        if approval.expires_at <= now:
            expired = AgentApproval.model_validate(
                {
                    **approval.model_dump(),
                    "status": ApprovalStatus.EXPIRED,
                    "resolved_at": now,
                }
            )
            self.store.update_approval(expired)
            self.store.append_event(
                run_id=run.id,
                event_type="approval.resolved",
                phase=run.phase.value,
                message=f"{approval.action} approval expired.",
                data={
                    "approval_id": approval.id,
                    "action": approval.action,
                    "status": "expired",
                },
            )
            raise ApprovalServiceError(
                "APPROVAL_EXPIRED",
                "Approval expired before it was resolved.",
            )
        resolved = AgentApproval.model_validate(
            {
                **approval.model_dump(),
                "status": (
                    ApprovalStatus.APPROVED
                    if approved
                    else ApprovalStatus.REJECTED
                ),
                "resolved_at": now,
            }
        )
        resolved = self.store.update_approval(resolved)
        self.store.append_event(
            run_id=run.id,
            event_type="approval.resolved",
            phase=run.phase.value,
            message=(
                f"{approval.action} approval was approved."
                if approved
                else f"{approval.action} approval was rejected."
            ),
            data={
                "approval_id": approval.id,
                "action": approval.action,
                "approved": approved,
                "workspace_version_id": approval.workspace_version_id,
            },
        )
        if not approved:
            cancelled = run.transition_to(
                RunPhase.CANCELLED,
                error={
                    "code": "APPROVAL_REJECTED",
                    "message": "The user rejected the requested action.",
                },
            )
            self.store.update_run(cancelled)
            self.store.append_event(
                run_id=run.id,
                event_type="agent.completed",
                phase=cancelled.phase.value,
                message="Agent Run was cancelled after approval rejection.",
                data={"status": "cancelled"},
            )
            return resolved, None
        next_approval = None
        if approval.action == "destructive_change":
            next_approval = self._create_or_reuse(
                run,
                action="commit",
                risk=str(approval.scope.get("risk") or "high"),
                destructive_approval_id=approval.id,
            )
        return resolved, next_approval

    def assert_commit_approval(
        self,
        run: AgentRun,
        approval_id: str,
        version_id: str,
    ) -> AgentApproval:
        approval = self.store.get_approval(approval_id)
        self._assert_bound(run, approval)
        if approval.action != "commit":
            raise ApprovalServiceError(
                "COMMIT_APPROVAL_ACTION_INVALID",
                "Commit requires a persisted commit Approval.",
            )
        if approval.status != ApprovalStatus.APPROVED:
            raise ApprovalServiceError(
                "COMMIT_APPROVAL_NOT_APPROVED",
                "Commit Approval has not been approved.",
            )
        if approval.expires_at <= utc_now():
            raise ApprovalServiceError(
                "APPROVAL_EXPIRED",
                "Commit Approval has expired.",
            )
        if approval.workspace_version_id != version_id:
            raise ApprovalServiceError(
                "APPROVAL_WORKSPACE_VERSION_MISMATCH",
                "Approval is not bound to the requested Workspace version.",
            )
        return approval

    def assert_destructive_approval(
        self,
        run: AgentRun,
        version_id: str,
    ) -> AgentApproval:
        for approval in self.store.list_approvals(run.id):
            if (
                approval.action == "destructive_change"
                and approval.workspace_version_id == version_id
                and approval.status == ApprovalStatus.APPROVED
                and approval.expires_at > utc_now()
            ):
                return approval
        raise ApprovalServiceError(
            "DESTRUCTIVE_APPROVAL_REQUIRED",
            "High-risk changes require a separate approved destructive-change record.",
        )

    def _create_or_reuse(
        self,
        run: AgentRun,
        *,
        action: Literal["commit", "destructive_change"],
        risk: str,
        destructive_approval_id: str | None = None,
    ) -> AgentApproval:
        for existing in self.store.list_approvals(run.id):
            if (
                existing.action == action
                and existing.workspace_version_id == run.head_version_id
                and existing.status == ApprovalStatus.PENDING
                and existing.expires_at > utc_now()
            ):
                return existing
        approval = self.store.create_approval(
            AgentApproval(
                run_id=run.id,
                workspace_version_id=run.head_version_id,
                action=action,
                scope={
                    "run_id": run.id,
                    "workspace_version_id": run.head_version_id,
                    "base_hash": run.base_hash,
                    "action": action,
                    "risk": risk,
                    "destructive_approval_id": destructive_approval_id,
                },
                expires_at=utc_now() + self.approval_ttl,
            )
        )
        self.store.append_event(
            run_id=run.id,
            event_type="approval.required",
            phase=run.phase.value,
            message=f"User approval is required for {action}.",
            data={
                "approval_id": approval.id,
                "action": action,
                "workspace_version_id": approval.workspace_version_id,
                "risk": risk,
                "expires_at": approval.expires_at.isoformat(),
            },
        )
        return approval

    @staticmethod
    def _assert_bound(run: AgentRun, approval: AgentApproval) -> None:
        if approval.run_id != run.id:
            raise ApprovalServiceError(
                "APPROVAL_RUN_MISMATCH",
                "Approval does not belong to this Agent Run.",
            )
        if approval.workspace_version_id != run.head_version_id:
            raise ApprovalServiceError(
                "APPROVAL_WORKSPACE_VERSION_MISMATCH",
                "Approval is not bound to the current Workspace head.",
            )
        if approval.scope.get("base_hash") != run.base_hash:
            raise ApprovalServiceError(
                "APPROVAL_BASE_HASH_MISMATCH",
                "Approval is not bound to the Run base Hash.",
            )
