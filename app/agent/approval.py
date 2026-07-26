from __future__ import annotations

from datetime import timedelta
from typing import Any, Literal

from app.agent.execution import draft_request_fingerprint
from app.agent.review import WorkflowReview
from app.agent.state import (
    AgentApproval,
    AgentRun,
    ApprovalStatus,
    RunPhase,
    utc_now,
)
from app.agent.store import AgentStore
from app.agent.trace import redact_sensitive_data


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
        allowed_test_runs: int | None = None,
        test_inputs: dict[str, Any] | None = None,
        test_query: str | None = None,
        test_files: list[dict[str, Any]] | None = None,
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
        scope = dict(approval.scope)
        if approval.action == "draft_run" and approved:
            scope = self._resolved_draft_scope(
                run,
                approval,
                allowed_test_runs=allowed_test_runs,
                test_inputs=test_inputs,
                test_query=test_query,
                test_files=test_files,
            )
        resolved = AgentApproval.model_validate(
            {
                **approval.model_dump(),
                "scope": scope,
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
        if not approved and approval.action != "draft_run":
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

    def request_for_draft_run(
        self,
        run_id: str,
        scope: dict[str, Any],
    ) -> AgentApproval:
        run = self.store.get_run(run_id)
        if scope.get("workspace_version_id") != run.head_version_id:
            raise ApprovalServiceError(
                "APPROVAL_WORKSPACE_VERSION_MISMATCH",
                "Draft Run request no longer matches the Workspace head.",
            )
        if scope.get("base_hash") != run.base_hash:
            raise ApprovalServiceError(
                "APPROVAL_BASE_HASH_MISMATCH",
                "Draft Run request no longer matches the Run base Hash.",
            )
        for existing in self.store.list_approvals(run.id):
            if (
                existing.action == "draft_run"
                and existing.workspace_version_id == run.head_version_id
                and existing.status == ApprovalStatus.PENDING
                and existing.expires_at > utc_now()
                and existing.scope.get("request_fingerprint")
                == scope.get("request_fingerprint")
            ):
                return existing
        approval = self.store.create_approval(
            AgentApproval(
                run_id=run.id,
                workspace_version_id=run.head_version_id,
                action="draft_run",
                scope=redact_sensitive_data(scope),
                expires_at=utc_now() + self.approval_ttl,
            )
        )
        event_data = {
            "approval_id": approval.id,
            "action": approval.action,
            "workspace_version_id": approval.workspace_version_id,
            "risk": approval.scope.get("risk"),
            "side_effects": approval.scope.get("side_effects"),
            "input_preview": approval.scope.get("input_preview"),
            "requested_test_runs": approval.scope.get("requested_test_runs"),
            "expires_at": approval.expires_at.isoformat(),
        }
        self.store.append_event(
            run_id=run.id,
            event_type="test.approval_required",
            phase=run.phase.value,
            message="Draft Run approval is required before executing side effects.",
            data=event_data,
        )
        self.store.append_event(
            run_id=run.id,
            event_type="approval.required",
            phase=run.phase.value,
            message="User approval is required for draft_run.",
            data=event_data,
        )
        return approval

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
    def _resolved_draft_scope(
        run: AgentRun,
        approval: AgentApproval,
        *,
        allowed_test_runs: int | None,
        test_inputs: dict[str, Any] | None,
        test_query: str | None,
        test_files: list[dict[str, Any]] | None,
    ) -> dict[str, Any]:
        scope = dict(approval.scope)
        requested = int(scope.get("requested_test_runs") or 1)
        remaining_budget = max(
            0,
            run.budget.max_test_runs - run.budget_usage.test_runs,
        )
        allowed = requested if allowed_test_runs is None else allowed_test_runs
        if allowed < 1:
            raise ApprovalServiceError(
                "DRAFT_TEST_ALLOWANCE_INVALID",
                "Approved Draft Run count must be at least one.",
            )
        if bool(scope.get("per_run")):
            allowed = 1
        if allowed > requested or allowed > remaining_budget:
            raise ApprovalServiceError(
                "DRAFT_TEST_ALLOWANCE_INVALID",
                "Approved Draft Run count exceeds the requested or remaining budget.",
            )
        if test_inputs is not None:
            safe_inputs = redact_sensitive_data(test_inputs)
            if safe_inputs != test_inputs:
                raise ApprovalServiceError(
                    "DRAFT_TEST_SENSITIVE_OVERRIDE_UNSUPPORTED",
                    "Sensitive test-input values cannot be persisted in Draft Run Approval.",
                )
            scope["inputs"] = safe_inputs
        if test_query is not None:
            safe_query = redact_sensitive_data(test_query)
            if safe_query != test_query:
                raise ApprovalServiceError(
                    "DRAFT_TEST_SENSITIVE_OVERRIDE_UNSUPPORTED",
                    "Sensitive test-query values cannot be persisted in Draft Run Approval.",
                )
            scope["query"] = safe_query
        if test_files is not None:
            safe_files = redact_sensitive_data(test_files)
            if safe_files != test_files:
                raise ApprovalServiceError(
                    "DRAFT_TEST_SENSITIVE_OVERRIDE_UNSUPPORTED",
                    "Sensitive file metadata cannot be persisted in Draft Run Approval.",
                )
            scope["files"] = safe_files
        scope.update(
            {
                "allowed_test_runs": allowed,
                "remaining_test_runs": allowed,
                "input_preview": {
                    "inputs": redact_sensitive_data(scope.get("inputs") or {}),
                    "query": redact_sensitive_data(scope.get("query")),
                    "files": [
                        {
                            key: item[key]
                            for key in (
                                "type",
                                "transfer_method",
                                "name",
                                "extension",
                            )
                            if key in item
                        }
                        for item in scope.get("files") or []
                        if isinstance(item, dict)
                    ],
                },
            }
        )
        scope["request_fingerprint"] = draft_request_fingerprint(scope)
        return scope

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
