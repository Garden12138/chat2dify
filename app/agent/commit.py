from __future__ import annotations

from contextlib import AbstractContextManager
from copy import deepcopy
from dataclasses import asdict
from hashlib import sha256
import threading
from typing import Any, Callable, Protocol

from pydantic import Field

from app.agent.approval import AgentApprovalService, ApprovalServiceError
from app.agent.diff import diff_plans
from app.agent.guard import guard_plan_change
from app.agent.normalizer import normalize_plan_payload
from app.agent.state import (
    AgentApproval,
    AgentRun,
    ApprovalStatus,
    RunPhase,
    StrictModel,
    utc_now,
)
from app.agent.store import AgentStore
from app.agent.trace import redact_sensitive_data
from app.agent.validation import WorkflowValidationService
from app.agent.workspace import VersionedWorkflowWorkspace, WorkspaceOperationError
from app.compiler.dify import DifyDslCompiler
from app.dify.client import (
    DifyConflictError,
    DifyDraftSyncResult,
    DifyDraftWorkflow,
)
from app.dify.graph import compile_plan_to_dify_graph
from app.models import WorkflowPlan


class CommitClient(Protocol):
    def get_draft_workflow(self, app_id: str) -> DifyDraftWorkflow: ...

    def sync_draft_workflow(
        self,
        app_id: str,
        *,
        graph: dict[str, Any],
        features: dict[str, Any],
        hash: str,
        environment_variables: list[dict[str, Any]] | None = None,
        conversation_variables: list[dict[str, Any]] | None = None,
    ) -> DifyDraftSyncResult: ...


class CommitServiceError(RuntimeError):
    def __init__(self, code: str, message: str, *, status_code: int = 422) -> None:
        super().__init__(message)
        self.code = code
        self.status_code = status_code


class CommitResult(StrictModel):
    run_id: str
    workspace_version_id: str
    approval_id: str
    idempotency_key: str
    status: str
    write_performed: bool
    base_hash: str
    new_hash: str | None = None
    sync: dict[str, Any] | None = None


class ModificationCommitService:
    def __init__(
        self,
        *,
        store: AgentStore,
        workspace: VersionedWorkflowWorkspace,
        approval: AgentApprovalService,
        validation: WorkflowValidationService,
        compiler: DifyDslCompiler,
        client_factory: Callable[[], AbstractContextManager[CommitClient]],
    ) -> None:
        self.store = store
        self.workspace = workspace
        self.approval = approval
        self.validation = validation
        self.compiler = compiler
        self.client_factory = client_factory
        self._locks_guard = threading.Lock()
        self._locks: dict[str, threading.Lock] = {}

    def commit(
        self,
        run_id: str,
        *,
        workspace_version_id: str,
        approval_id: str,
    ) -> CommitResult:
        with self._run_lock(run_id):
            return self._commit_locked(
                run_id,
                workspace_version_id=workspace_version_id,
                approval_id=approval_id,
            )

    def _commit_locked(
        self,
        run_id: str,
        *,
        workspace_version_id: str,
        approval_id: str,
    ) -> CommitResult:
        run = self.store.get_run(run_id)
        if run.commit_result is not None:
            previous = CommitResult.model_validate(run.commit_result)
            if (
                previous.workspace_version_id == workspace_version_id
                and previous.approval_id == approval_id
            ):
                return previous
            raise CommitServiceError(
                "COMMIT_ALREADY_COMPLETED",
                "Agent Run already completed a different Commit.",
                status_code=409,
            )
        if run.phase != RunPhase.WAITING_APPROVAL:
            raise CommitServiceError(
                "COMMIT_RUN_STATE_INVALID",
                "Agent Run must be waiting for approval before Commit.",
                status_code=409,
            )
        try:
            approval = self.approval.assert_commit_approval(
                run,
                approval_id,
                workspace_version_id,
            )
            run, _version, plan = self.workspace.precommit_plan(
                run_id,
                workspace_version_id,
            )
        except (ApprovalServiceError, WorkspaceOperationError) as exc:
            raise CommitServiceError(
                getattr(exc, "code", "COMMIT_PRECONDITION_FAILED"),
                str(exc),
                status_code=409,
            ) from exc
        if run.snapshot is None or not run.base_hash:
            raise CommitServiceError(
                "COMMIT_SNAPSHOT_MISSING",
                "Commit requires an authoritative existing-app Snapshot.",
            )
        committing = run.transition_to(RunPhase.COMMITTING)
        self.store.update_run(committing)
        self.store.append_event(
            run_id=run.id,
            event_type="commit.started",
            phase=committing.phase.value,
            message="Commit started with persisted Workspace and Approval records.",
            data={
                "workspace_version_id": workspace_version_id,
                "approval_id": approval_id,
            },
        )
        try:
            normalized = normalize_plan_payload(
                plan.model_dump(mode="json"),
                app_name=plan.name,
                app_description=plan.description,
                app_mode=plan.app_mode,
            )
            final_plan = WorkflowPlan.model_validate(normalized.payload)
            report = self.validation.validate(final_plan)
            if not report.ok:
                raise CommitServiceError(
                    "COMMIT_VALIDATION_FAILED",
                    "Workspace head failed the final deterministic validation chain.",
                )
            before = WorkflowPlan.model_validate(run.snapshot.base_plan)
            changes = diff_plans(before, final_plan)
            guard = guard_plan_change(before, final_plan, changes)
            if not guard.ok:
                self.approval.assert_destructive_approval(
                    run,
                    workspace_version_id,
                )
            with self.client_factory() as client:
                current = client.get_draft_workflow(run.snapshot.app_id)
                if current.hash != run.base_hash:
                    return self._conflict(
                        committing,
                        approval,
                        workspace_version_id,
                        current_hash=current.hash,
                    )
                idempotency_key = _idempotency_key(
                    run.id,
                    workspace_version_id,
                    approval.id,
                )
                if guard.no_op:
                    result = CommitResult(
                        run_id=run.id,
                        workspace_version_id=workspace_version_id,
                        approval_id=approval.id,
                        idempotency_key=idempotency_key,
                        status="noop",
                        write_performed=False,
                        base_hash=run.base_hash,
                        new_hash=run.base_hash,
                    )
                else:
                    graph = compile_plan_to_dify_graph(
                        final_plan,
                        compiler=self.compiler,
                        base_graph=deepcopy(run.snapshot.base_graph),
                    )
                    sync = client.sync_draft_workflow(
                        run.snapshot.app_id,
                        graph=graph,
                        features=deepcopy(current.features),
                        hash=current.hash,
                        environment_variables=deepcopy(
                            current.environment_variables
                        ),
                        conversation_variables=[
                            variable.model_dump(mode="json")
                            for variable in final_plan.conversation_variables
                        ],
                    )
                    result = CommitResult(
                        run_id=run.id,
                        workspace_version_id=workspace_version_id,
                        approval_id=approval.id,
                        idempotency_key=idempotency_key,
                        status="committed",
                        write_performed=True,
                        base_hash=run.base_hash,
                        new_hash=sync.hash,
                        sync=asdict(sync),
                    )
            completed = AgentRun.model_validate(
                {
                    **committing.transition_to(RunPhase.COMPLETED).model_dump(),
                    "commit_result": result.model_dump(mode="json"),
                }
            )
            consumed = AgentApproval.model_validate(
                {
                    **approval.model_dump(),
                    "status": ApprovalStatus.CONSUMED,
                    "resolved_at": approval.resolved_at or utc_now(),
                }
            )
            self.store.finish_commit(
                run=completed,
                approval=consumed,
                event_message=(
                    "Validated Workspace was committed to Dify."
                    if result.write_performed
                    else "Validated no-op completed without a Dify write."
                ),
                event_data=result.model_dump(mode="json"),
            )
            self.store.append_event(
                run_id=run.id,
                event_type="agent.completed",
                phase=RunPhase.COMPLETED.value,
                message="Agent Run completed.",
                data={"status": result.status},
            )
            return result
        except DifyConflictError as exc:
            return self._conflict(
                committing,
                approval,
                workspace_version_id,
                current_hash=None,
                message=str(exc),
            )
        except CommitServiceError:
            self._fail_committing_run(committing)
            raise
        except ApprovalServiceError as exc:
            self._fail_committing_run(committing)
            raise CommitServiceError(exc.code, str(exc), status_code=409) from exc
        except Exception as exc:
            self._fail_committing_run(committing)
            raise CommitServiceError(
                "COMMIT_EXECUTION_FAILED",
                f"Commit failed with {exc.__class__.__name__}.",
                status_code=502,
            ) from exc

    def _conflict(
        self,
        committing: AgentRun,
        approval: AgentApproval,
        workspace_version_id: str,
        *,
        current_hash: str | None,
        message: str | None = None,
    ) -> CommitResult:
        result = CommitResult(
            run_id=committing.id,
            workspace_version_id=workspace_version_id,
            approval_id=approval.id,
            idempotency_key=_idempotency_key(
                committing.id,
                workspace_version_id,
                approval.id,
            ),
            status="conflicted",
            write_performed=False,
            base_hash=str(committing.base_hash or ""),
            new_hash=current_hash,
        )
        conflicted = AgentRun.model_validate(
            {
                **committing.transition_to(
                    RunPhase.CONFLICTED,
                    error={
                        "code": "DIFY_DRAFT_HASH_CONFLICT",
                        "message": (
                            str(redact_sensitive_data(message))
                            if message
                            else (
                                "Current Dify draft Hash differs from the "
                                "pinned base Hash."
                            )
                        ),
                        "current_hash": current_hash,
                    },
                ).model_dump(),
                "commit_result": result.model_dump(mode="json"),
            }
        )
        self.store.update_run(conflicted)
        self.store.append_event(
            run_id=committing.id,
            event_type="commit.completed",
            phase=conflicted.phase.value,
            message="Commit stopped because the Dify draft Hash changed.",
            data=result.model_dump(mode="json"),
        )
        return result

    def _fail_committing_run(self, committing: AgentRun) -> None:
        current = self.store.get_run(committing.id)
        if current.phase != RunPhase.COMMITTING:
            return
        failed = current.transition_to(
            RunPhase.FAILED,
            error={
                "code": "COMMIT_FAILED",
                "message": "Commit failed before a successful result was persisted.",
            },
        )
        self.store.update_run(failed)
        self.store.append_event(
            run_id=failed.id,
            event_type="agent.failed",
            phase=failed.phase.value,
            message="Commit failed.",
            data={"code": "COMMIT_FAILED"},
        )

    def _run_lock(self, run_id: str) -> threading.Lock:
        with self._locks_guard:
            return self._locks.setdefault(run_id, threading.Lock())


def _idempotency_key(
    run_id: str,
    workspace_version_id: str,
    approval_id: str,
) -> str:
    return sha256(
        f"{run_id}:{workspace_version_id}:{approval_id}".encode("utf-8")
    ).hexdigest()
