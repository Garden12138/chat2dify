from __future__ import annotations

from contextlib import AbstractContextManager
from copy import deepcopy
from dataclasses import asdict
from hashlib import sha256
import threading
from typing import Any, Callable, Literal, Protocol

from app.agent.approval import AgentApprovalService, ApprovalServiceError
from app.agent.diff import diff_plans
from app.agent.guard import guard_plan_change
from app.agent.normalizer import normalize_plan_payload
from app.agent.state import (
    AgentApproval,
    AgentRun,
    AgentSession,
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
    DifyImportResult,
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


class CreationCommitClient(Protocol):
    def import_yaml(
        self,
        yaml_content: str,
        *,
        name: str | None = None,
        idempotency_key: str | None = None,
    ) -> DifyImportResult: ...

    def get_draft_workflow(self, app_id: str) -> DifyDraftWorkflow: ...


class CommitServiceError(RuntimeError):
    def __init__(self, code: str, message: str, *, status_code: int = 422) -> None:
        super().__init__(message)
        self.code = code
        self.status_code = status_code


class CommitResult(StrictModel):
    kind: Literal["modify"] = "modify"
    run_id: str
    workspace_version_id: str
    approval_id: str
    idempotency_key: str
    status: str
    write_performed: bool
    base_hash: str
    new_hash: str | None = None
    sync: dict[str, Any] | None = None


class CreationCommitCheckpoint(StrictModel):
    kind: Literal["create"] = "create"
    run_id: str
    workspace_version_id: str
    approval_id: str
    idempotency_key: str
    status: Literal[
        "import_started",
        "import_failed",
        "import_succeeded_recovery_pending",
        "import_outcome_unknown",
    ]
    import_result: dict[str, Any] | None = None
    error: dict[str, Any] | None = None


class CreationCommitResult(StrictModel):
    kind: Literal["create"] = "create"
    run_id: str
    workspace_version_id: str
    approval_id: str
    idempotency_key: str
    status: Literal["created"] = "created"
    write_performed: bool = True
    app_id: str
    app_mode: Literal["workflow", "advanced-chat"]
    workflow_url: str | None = None
    draft_hash: str
    import_result: dict[str, Any]


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
        session = self.store.get_session(run.session_id)
        if session.operation != "modify":
            raise CommitServiceError(
                "COMMIT_ADAPTER_MODE_INVALID",
                "Modification Commit adapter cannot import a new Dify app.",
                status_code=409,
            )
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
        _assert_canvas_commit_safe(run)
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
        if (
            run.snapshot is None
            or run.snapshot.operation != "modify"
            or not run.base_hash
        ):
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


class CreationCommitService:
    def __init__(
        self,
        *,
        store: AgentStore,
        workspace: VersionedWorkflowWorkspace,
        approval: AgentApprovalService,
        validation: WorkflowValidationService,
        compiler: DifyDslCompiler,
        client_factory: Callable[
            [], AbstractContextManager[CreationCommitClient]
        ],
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
    ) -> CreationCommitResult:
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
    ) -> CreationCommitResult:
        run = self.store.get_run(run_id)
        session = self.store.get_session(run.session_id)
        if (
            session.operation != "create"
            and not (
                run.snapshot is not None
                and run.snapshot.operation == "create"
            )
        ):
            raise CommitServiceError(
                "CREATE_COMMIT_ADAPTER_MODE_INVALID",
                "Creation Commit adapter requires a new-app Session.",
                status_code=409,
            )
        existing = run.commit_result
        if isinstance(existing, dict) and existing.get("kind") == "create":
            status = str(existing.get("status") or "")
            if status == "created":
                result = CreationCommitResult.model_validate(existing)
                self._assert_same_request(
                    result,
                    workspace_version_id=workspace_version_id,
                    approval_id=approval_id,
                )
                return result
            checkpoint = CreationCommitCheckpoint.model_validate(existing)
            if status == "import_succeeded_recovery_pending":
                self._assert_same_request(
                    checkpoint,
                    workspace_version_id=workspace_version_id,
                    approval_id=approval_id,
                )
                return self._recover_known_import(
                    run,
                    session,
                    checkpoint,
                )
            if status in {"import_started", "import_outcome_unknown"}:
                raise CommitServiceError(
                    "CREATE_IMPORT_OUTCOME_UNKNOWN",
                    (
                        "A previous import may have reached Dify, so this Run "
                        "will not automatically issue another import."
                    ),
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
        if (
            run.snapshot is None
            or run.snapshot.operation != "create"
            or run.snapshot.app_id is not None
            or run.base_hash is not None
        ):
            raise CommitServiceError(
                "CREATE_COMMIT_SNAPSHOT_INVALID",
                "Creation Commit requires an unimported create-mode Snapshot.",
            )
        committing = self.store.update_run(
            run.transition_to(RunPhase.COMMITTING)
        )
        self.store.append_event(
            run_id=run.id,
            event_type="commit.started",
            phase=committing.phase.value,
            message="Creation Commit started from persisted Workspace and Approval records.",
            data={
                "operation": "create",
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
            dsl = self.compiler.compile(final_plan)
            idempotency_key = _idempotency_key(
                run.id,
                workspace_version_id,
                approval.id,
            )
            checkpoint = CreationCommitCheckpoint(
                run_id=run.id,
                workspace_version_id=workspace_version_id,
                approval_id=approval.id,
                idempotency_key=idempotency_key,
                status="import_started",
            )
            committing = self._persist_checkpoint(committing, checkpoint)
            try:
                with self.client_factory() as client:
                    imported = client.import_yaml(
                        dsl,
                        name=final_plan.name,
                        idempotency_key=idempotency_key,
                    )
            except Exception as exc:  # noqa: BLE001 - outcome can be ambiguous.
                unknown = CreationCommitCheckpoint.model_validate(
                    {
                        **checkpoint.model_dump(),
                        "status": "import_outcome_unknown",
                        "error": {
                            "code": "CREATE_IMPORT_OUTCOME_UNKNOWN",
                            "message": (
                                "Dify import ended without a reliable response; "
                                f"client raised {exc.__class__.__name__}."
                            ),
                        },
                    }
                )
                self._pause_creation_run(
                    committing,
                    approval,
                    unknown,
                    code="CREATE_IMPORT_OUTCOME_UNKNOWN",
                    message=(
                        "Dify import outcome is unknown; automatic retry is "
                        "blocked to prevent a duplicate app."
                    ),
                    expire_approval=True,
                )
                raise CommitServiceError(
                    "CREATE_IMPORT_OUTCOME_UNKNOWN",
                    (
                        "Dify import outcome is unknown; inspect Dify before "
                        "starting another creation Run."
                    ),
                    status_code=502,
                ) from exc
            safe_import = redact_sensitive_data(asdict(imported))
            if (
                imported.status not in {"completed", "completed-with-warnings"}
                or not imported.app_id
            ):
                failed = CreationCommitCheckpoint.model_validate(
                    {
                        **checkpoint.model_dump(),
                        "status": "import_failed",
                        "import_result": safe_import,
                        "error": {
                            "code": "CREATE_IMPORT_FAILED",
                            "message": str(
                                redact_sensitive_data(
                                    imported.error
                                    or f"Dify returned status {imported.status or 'unknown'}."
                                )
                            )[:8_000],
                        },
                    }
                )
                self._pause_creation_run(
                    committing,
                    approval,
                    failed,
                    code="CREATE_IMPORT_FAILED",
                    message="Dify rejected the import; Workspace remains recoverable.",
                    expire_approval=True,
                )
                raise CommitServiceError(
                    "CREATE_IMPORT_FAILED",
                    str(failed.error.get("message") if failed.error else ""),
                    status_code=502,
                )
            pending = CreationCommitCheckpoint.model_validate(
                {
                    **checkpoint.model_dump(),
                    "status": "import_succeeded_recovery_pending",
                    "import_result": safe_import,
                }
            )
            committing = self._persist_checkpoint(
                self.store.get_run(run.id),
                pending,
            )
            return self._recover_known_import(
                committing,
                session,
                pending,
            )
        except CommitServiceError as exc:
            current = self.store.get_run(run.id)
            if (
                current.phase == RunPhase.COMMITTING
                and not self._pause_pending_result_recovery(
                    current,
                    message=str(exc),
                )
            ):
                self._fail_committing_run(current)
            raise
        except ApprovalServiceError as exc:
            current = self.store.get_run(run.id)
            if (
                current.phase == RunPhase.COMMITTING
                and not self._pause_pending_result_recovery(
                    current,
                    message=str(exc),
                )
            ):
                self._fail_committing_run(current)
            raise CommitServiceError(exc.code, str(exc), status_code=409) from exc
        except Exception as exc:
            current = self.store.get_run(run.id)
            recovery_paused = (
                current.phase == RunPhase.COMMITTING
                and self._pause_pending_result_recovery(
                    current,
                    message=(
                        "Local result persistence failed after Dify import "
                        f"({exc.__class__.__name__})."
                    ),
                )
            )
            if current.phase == RunPhase.COMMITTING and not recovery_paused:
                self._fail_committing_run(current)
            if recovery_paused:
                raise CommitServiceError(
                    "CREATE_RESULT_RECOVERY_FAILED",
                    (
                        "Dify app import succeeded, but local result recovery "
                        "failed; retry the same Commit request without re-importing."
                    ),
                    status_code=502,
                ) from exc
            raise CommitServiceError(
                "CREATE_COMMIT_EXECUTION_FAILED",
                f"Creation Commit failed with {exc.__class__.__name__}.",
                status_code=502,
            ) from exc

    def _recover_known_import(
        self,
        run: AgentRun,
        session: AgentSession,
        checkpoint: CreationCommitCheckpoint,
    ) -> CreationCommitResult:
        current = self.store.get_run(run.id)
        if current.head_version_id != checkpoint.workspace_version_id:
            raise CommitServiceError(
                "CREATE_RESULT_WORKSPACE_CHANGED",
                "Imported app result cannot bind to a changed Workspace head.",
                status_code=409,
            )
        approval = self.store.get_approval(checkpoint.approval_id)
        if (
            approval.workspace_version_id != checkpoint.workspace_version_id
            or approval.status != ApprovalStatus.APPROVED
        ):
            raise CommitServiceError(
                "CREATE_RESULT_APPROVAL_CHANGED",
                "Imported app result no longer has its approved Workspace binding.",
                status_code=409,
            )
        if current.phase == RunPhase.INTERRUPTED:
            current = self.store.update_run(
                current.transition_to(RunPhase.COMMITTING)
            )
            self.store.append_event(
                run_id=current.id,
                event_type="commit.started",
                phase=current.phase.value,
                message="Resuming result recovery for an already imported Dify app.",
                data={
                    "operation": "create_result_recovery",
                    "idempotency_key": checkpoint.idempotency_key,
                },
            )
        elif current.phase != RunPhase.COMMITTING:
            raise CommitServiceError(
                "CREATE_RESULT_RECOVERY_STATE_INVALID",
                "Known import result can recover only from committing or interrupted state.",
                status_code=409,
            )
        import_result = checkpoint.import_result or {}
        app_id = str(import_result.get("app_id") or "")
        if not app_id:
            raise CommitServiceError(
                "CREATE_RESULT_APP_ID_MISSING",
                "Known successful import checkpoint does not contain an app_id.",
            )
        snapshot = current.snapshot
        if snapshot is None or snapshot.operation != "create":
            raise CommitServiceError(
                "CREATE_COMMIT_SNAPSHOT_INVALID",
                "Creation result recovery requires the original create Snapshot.",
            )
        imported_mode = str(import_result.get("app_mode") or snapshot.app_mode)
        if imported_mode != snapshot.app_mode:
            self._pause_creation_run(
                current,
                approval,
                checkpoint,
                code="CREATE_IMPORTED_MODE_MISMATCH",
                message="Imported Dify app mode does not match the approved create mode.",
                expire_approval=False,
            )
            raise CommitServiceError(
                "CREATE_IMPORTED_MODE_MISMATCH",
                "Imported Dify app mode does not match the approved create mode.",
                status_code=502,
            )
        try:
            with self.client_factory() as client:
                draft = client.get_draft_workflow(app_id)
            if not draft.hash:
                raise ValueError("Imported Dify draft did not provide a Hash.")
        except Exception as exc:  # noqa: BLE001 - the import itself already succeeded.
            self._pause_creation_run(
                current,
                approval,
                checkpoint,
                code="CREATE_RESULT_RECOVERY_FAILED",
                message=(
                    "Dify app was imported, but its draft result could not be "
                    f"recovered ({exc.__class__.__name__})."
                ),
                expire_approval=False,
            )
            raise CommitServiceError(
                "CREATE_RESULT_RECOVERY_FAILED",
                (
                    "Dify app import succeeded, but result recovery failed; "
                    "retry this same Commit request to recover without re-importing."
                ),
                status_code=502,
            ) from exc
        result = CreationCommitResult(
            run_id=current.id,
            workspace_version_id=checkpoint.workspace_version_id,
            approval_id=checkpoint.approval_id,
            idempotency_key=checkpoint.idempotency_key,
            app_id=app_id,
            app_mode=snapshot.app_mode,
            workflow_url=(
                str(import_result.get("workflow_url"))
                if import_result.get("workflow_url")
                else None
            ),
            draft_hash=draft.hash,
            import_result=import_result,
        )
        completed = AgentRun.model_validate(
            {
                **current.transition_to(RunPhase.COMPLETED).model_dump(),
                "base_hash": draft.hash,
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
        updated_session = AgentSession.model_validate(
            {
                **session.model_dump(),
                "operation": "modify",
                "app_id": app_id,
                "app_name": snapshot.app_name,
                "app_description": snapshot.app_description,
                "updated_at": utc_now(),
            }
        )
        self.store.finish_creation_commit(
            run=completed,
            approval=consumed,
            session=updated_session,
            event_message="Validated Workspace was imported as a new Dify app.",
            event_data=result.model_dump(mode="json"),
        )
        self.store.append_event(
            run_id=current.id,
            event_type="agent.completed",
            phase=RunPhase.COMPLETED.value,
            message="New-app Agent Run completed.",
            data={
                "status": result.status,
                "app_id": result.app_id,
                "draft_hash": result.draft_hash,
            },
        )
        return result

    def _persist_checkpoint(
        self,
        run: AgentRun,
        checkpoint: CreationCommitCheckpoint,
    ) -> AgentRun:
        current = self.store.get_run(run.id)
        if current.phase != RunPhase.COMMITTING:
            raise CommitServiceError(
                "CREATE_CHECKPOINT_STATE_INVALID",
                "Creation import checkpoint requires committing state.",
                status_code=409,
            )
        updated = AgentRun.model_validate(
            {
                **current.model_dump(),
                "commit_result": checkpoint.model_dump(mode="json"),
                "updated_at": utc_now(),
            }
        )
        return self.store.update_run(updated)

    def _pause_creation_run(
        self,
        run: AgentRun,
        approval: AgentApproval,
        checkpoint: CreationCommitCheckpoint,
        *,
        code: str,
        message: str,
        expire_approval: bool,
    ) -> AgentRun:
        current = self.store.get_run(run.id)
        if current.phase == RunPhase.COMMITTING:
            paused = current.transition_to(
                RunPhase.INTERRUPTED,
                error={
                    "code": code,
                    "message": str(redact_sensitive_data(message))[:8_000],
                },
            )
        elif current.phase == RunPhase.INTERRUPTED:
            paused = AgentRun.model_validate(
                {
                    **current.model_dump(),
                    "error": {
                        "code": code,
                        "message": str(redact_sensitive_data(message))[:8_000],
                    },
                    "updated_at": utc_now(),
                }
            )
        else:
            raise CommitServiceError(
                "CREATE_PAUSE_STATE_INVALID",
                "Creation Commit could not enter a recoverable state.",
                status_code=409,
            )
        paused = AgentRun.model_validate(
            {
                **paused.model_dump(),
                "commit_result": checkpoint.model_dump(mode="json"),
            }
        )
        paused = self.store.update_run(paused)
        if expire_approval:
            self._expire_approval(approval, code=code)
        self.store.append_event(
            run_id=paused.id,
            event_type="agent.paused",
            phase=paused.phase.value,
            message=message,
            data={
                "code": code,
                "workspace_version_id": checkpoint.workspace_version_id,
                "idempotency_key": checkpoint.idempotency_key,
                "import_status": checkpoint.status,
            },
        )
        return paused

    def _expire_approval(
        self,
        approval: AgentApproval,
        *,
        code: str,
    ) -> None:
        current = self.store.get_approval(approval.id)
        if current.status not in {
            ApprovalStatus.PENDING,
            ApprovalStatus.APPROVED,
        }:
            return
        expired = AgentApproval.model_validate(
            {
                **current.model_dump(),
                "status": ApprovalStatus.EXPIRED,
                "resolved_at": utc_now(),
            }
        )
        self.store.update_approval(expired)
        self.store.append_event(
            run_id=current.run_id,
            event_type="approval.resolved",
            phase=RunPhase.INTERRUPTED.value,
            message="Creation Commit approval expired after an unsuccessful import.",
            data={
                "approval_id": current.id,
                "status": "expired",
                "reason": code,
            },
        )

    def _pause_pending_result_recovery(
        self,
        run: AgentRun,
        *,
        message: str,
    ) -> bool:
        payload = run.commit_result
        if (
            not isinstance(payload, dict)
            or payload.get("kind") != "create"
            or payload.get("status") != "import_succeeded_recovery_pending"
        ):
            return False
        checkpoint = CreationCommitCheckpoint.model_validate(payload)
        approval = self.store.get_approval(checkpoint.approval_id)
        self._pause_creation_run(
            run,
            approval,
            checkpoint,
            code="CREATE_RESULT_RECOVERY_FAILED",
            message=(
                "Dify app was imported, but its persisted result still needs "
                f"recovery: {str(redact_sensitive_data(message))[:4_000]}"
            ),
            expire_approval=False,
        )
        return True

    def _fail_committing_run(self, committing: AgentRun) -> None:
        current = self.store.get_run(committing.id)
        if current.phase != RunPhase.COMMITTING:
            return
        failed = current.transition_to(
            RunPhase.FAILED,
            error={
                "code": "CREATE_COMMIT_FAILED",
                "message": "Creation Commit failed before a recoverable result was persisted.",
            },
        )
        self.store.update_run(failed)
        self.store.append_event(
            run_id=failed.id,
            event_type="agent.failed",
            phase=failed.phase.value,
            message="Creation Commit failed.",
            data={"code": "CREATE_COMMIT_FAILED"},
        )

    @staticmethod
    def _assert_same_request(
        record: CreationCommitCheckpoint | CreationCommitResult,
        *,
        workspace_version_id: str,
        approval_id: str,
    ) -> None:
        if (
            record.workspace_version_id != workspace_version_id
            or record.approval_id != approval_id
        ):
            raise CommitServiceError(
                "COMMIT_ALREADY_COMPLETED",
                "Agent Run already imported a different approved Workspace version.",
                status_code=409,
            )

    def _run_lock(self, run_id: str) -> threading.Lock:
        with self._locks_guard:
            return self._locks.setdefault(run_id, threading.Lock())


def _assert_canvas_commit_safe(run: AgentRun) -> None:
    if run.constraints.dirty_state:
        raise CommitServiceError(
            "COMMIT_CANVAS_DIRTY",
            "The Dify canvas has unsynchronized changes; sync it before Commit.",
            status_code=409,
        )
    canvas_hash = run.constraints.canvas_draft_hash
    if run.constraints.canvas_context_revision > 0 and canvas_hash is None:
        raise CommitServiceError(
            "COMMIT_CANVAS_HASH_MISSING",
            "The visible canvas context has no draft Hash; refresh it before Commit.",
            status_code=409,
        )
    if canvas_hash is not None and canvas_hash != run.base_hash:
        raise CommitServiceError(
            "COMMIT_CANVAS_HASH_MISMATCH",
            "The visible canvas Hash does not match the Run's pinned base Hash.",
            status_code=409,
        )


def _idempotency_key(
    run_id: str,
    workspace_version_id: str,
    approval_id: str,
) -> str:
    return sha256(
        f"{run_id}:{workspace_version_id}:{approval_id}".encode("utf-8")
    ).hexdigest()
