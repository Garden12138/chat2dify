from __future__ import annotations

from contextlib import AbstractContextManager
from hashlib import sha256
import threading
from typing import Any, Callable, Literal, Protocol

from app.agent.approval import AgentApprovalService, ApprovalServiceError
from app.agent.commit import CommitServiceError
from app.agent.config_app import (
    CONFIG_APP_MODES,
    VersionedConfigWorkspace,
    config_review_risk,
    diff_config,
    extract_model_config,
    model_config_hash,
    model_config_hash_from_payload,
    validate_config,
)
from app.agent.state import (
    AgentApproval,
    AgentConfigSnapshot,
    AgentRun,
    ApprovalStatus,
    RunPhase,
    StrictModel,
    utc_now,
)
from app.agent.store import AgentStore
from app.agent.trace import redact_sensitive_data
from app.agent.workspace import WorkspaceOperationError
from app.dify.client import DifyAppDetail


class ConfigCommitClient(Protocol):
    def get_app_detail(self, app_id: str) -> DifyAppDetail: ...

    def update_model_config(
        self,
        app_id: str,
        model_config: dict[str, Any],
    ) -> dict[str, Any]: ...


class ConfigCommitResult(StrictModel):
    kind: Literal["config-modify"] = "config-modify"
    run_id: str
    workspace_version_id: str
    approval_id: str
    idempotency_key: str
    app_mode: Literal["chat", "completion", "agent-chat"]
    status: Literal["committed", "noop", "conflicted"]
    write_performed: bool
    base_hash: str
    new_hash: str | None = None
    sync: dict[str, Any] | None = None


class ConfigCommitService:
    def __init__(
        self,
        *,
        store: AgentStore,
        workspace: VersionedConfigWorkspace,
        approval: AgentApprovalService,
        client_factory: Callable[
            [], AbstractContextManager[ConfigCommitClient]
        ],
    ) -> None:
        self.store = store
        self.workspace = workspace
        self.approval = approval
        self.client_factory = client_factory
        self._locks_guard = threading.Lock()
        self._locks: dict[str, threading.Lock] = {}

    def commit(
        self,
        run_id: str,
        *,
        workspace_version_id: str,
        approval_id: str,
    ) -> ConfigCommitResult:
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
    ) -> ConfigCommitResult:
        run = self.store.get_run(run_id)
        session = self.store.get_session(run.session_id)
        if (
            session.operation != "modify"
            or session.app_mode not in CONFIG_APP_MODES
            or not isinstance(run.snapshot, AgentConfigSnapshot)
        ):
            raise CommitServiceError(
                "CONFIG_COMMIT_ADAPTER_MODE_INVALID",
                "Config Commit requires an existing configured-app Run.",
                status_code=409,
            )
        if run.commit_result is not None:
            previous = ConfigCommitResult.model_validate(run.commit_result)
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
        if not bool(run.snapshot.compatibility.get("mutation_supported", True)):
            raise CommitServiceError(
                "DIFY_VERSION_MUTATION_UNSUPPORTED",
                str(
                    run.snapshot.compatibility.get("reason")
                    or "This Dify/DSL version is diagnostic-only."
                ),
                status_code=409,
            )
        try:
            approval = self.approval.assert_commit_approval(
                run,
                approval_id,
                workspace_version_id,
            )
            run, version, config = self.workspace.precommit_config(
                run_id,
                workspace_version_id,
            )
            risk = config_review_risk(
                version,
                diff_config(run.snapshot.base_config, config),
            )
            if risk.get("risk") == "high":
                self.approval.assert_destructive_approval(
                    run,
                    workspace_version_id,
                )
        except (ApprovalServiceError, WorkspaceOperationError) as exc:
            raise CommitServiceError(
                getattr(exc, "code", "COMMIT_PRECONDITION_FAILED"),
                str(exc),
                status_code=409,
            ) from exc
        report = validate_config(run.snapshot.app_mode, config)
        if not report.ok:
            raise CommitServiceError(
                "COMMIT_VALIDATION_FAILED",
                "Config Workspace failed final deterministic validation.",
            )
        committing = self.store.update_run(
            run.transition_to(RunPhase.COMMITTING)
        )
        self.store.append_event(
            run_id=run.id,
            event_type="commit.started",
            phase=committing.phase.value,
            message=(
                "Configured-app Commit started with persisted Workspace and "
                "Approval records."
            ),
            data={
                "workspace_version_id": workspace_version_id,
                "approval_id": approval_id,
                "app_mode": run.snapshot.app_mode,
            },
        )
        try:
            with self.client_factory() as client:
                current_app = client.get_app_detail(run.snapshot.app_id)
                current_mode = str(current_app.mode or "")
                current_config = extract_model_config(current_app)
                if current_mode != run.snapshot.app_mode or current_config is None:
                    raise CommitServiceError(
                        "CONFIG_COMMIT_SNAPSHOT_INVALID",
                        (
                            "Current Dify application mode or model "
                            "configuration no longer matches the Snapshot."
                        ),
                        status_code=409,
                    )
                current_hash = model_config_hash(
                    current_app,
                    current_config,
                )
                if current_hash != run.base_hash:
                    return self._conflict(
                        committing,
                        approval,
                        workspace_version_id,
                        current_hash=current_hash,
                    )
                idempotency_key = _idempotency_key(
                    run.id,
                    workspace_version_id,
                    approval.id,
                )
                if config == current_config:
                    result = ConfigCommitResult(
                        run_id=run.id,
                        workspace_version_id=workspace_version_id,
                        approval_id=approval.id,
                        idempotency_key=idempotency_key,
                        app_mode=run.snapshot.app_mode,
                        status="noop",
                        write_performed=False,
                        base_hash=run.base_hash,
                        new_hash=run.base_hash,
                    )
                else:
                    sync = client.update_model_config(
                        run.snapshot.app_id,
                        config,
                    )
                    refreshed_hash = None
                    try:
                        refreshed_app = client.get_app_detail(
                            run.snapshot.app_id
                        )
                        refreshed_config = extract_model_config(refreshed_app)
                        if refreshed_config is not None:
                            refreshed_hash = model_config_hash(
                                refreshed_app,
                                refreshed_config,
                            )
                    except Exception:  # noqa: BLE001 - update receipt is authoritative.
                        refreshed_hash = None
                    result = ConfigCommitResult(
                        run_id=run.id,
                        workspace_version_id=workspace_version_id,
                        approval_id=approval.id,
                        idempotency_key=idempotency_key,
                        app_mode=run.snapshot.app_mode,
                        status="committed",
                        write_performed=True,
                        base_hash=run.base_hash,
                        new_hash=(
                            model_config_hash_from_payload(sync)
                            or refreshed_hash
                            or model_config_hash(None, config)
                        ),
                        sync=redact_sensitive_data(sync),
                    )
            completed = AgentRun.model_validate(
                {
                    **committing.transition_to(
                        RunPhase.COMPLETED
                    ).model_dump(),
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
                    "Validated configured-app Workspace was committed to Dify."
                    if result.write_performed
                    else "Validated configured-app no-op completed without a write."
                ),
                event_data=result.model_dump(mode="json"),
            )
            self.store.append_event(
                run_id=run.id,
                event_type="agent.completed",
                phase=RunPhase.COMPLETED.value,
                message="Configured-app Agent Run completed.",
                data={"status": result.status},
            )
            return result
        except CommitServiceError:
            self._fail_committing_run(committing)
            raise
        except Exception as exc:
            self._fail_committing_run(committing)
            raise CommitServiceError(
                "CONFIG_COMMIT_EXECUTION_FAILED",
                f"Config Commit failed with {exc.__class__.__name__}.",
                status_code=502,
            ) from exc

    def _conflict(
        self,
        committing: AgentRun,
        approval: AgentApproval,
        workspace_version_id: str,
        *,
        current_hash: str,
    ) -> ConfigCommitResult:
        snapshot = committing.snapshot
        if not isinstance(snapshot, AgentConfigSnapshot):
            raise CommitServiceError(
                "CONFIG_COMMIT_SNAPSHOT_INVALID",
                "Configured-app Snapshot disappeared during Commit.",
            )
        result = ConfigCommitResult(
            run_id=committing.id,
            workspace_version_id=workspace_version_id,
            approval_id=approval.id,
            idempotency_key=_idempotency_key(
                committing.id,
                workspace_version_id,
                approval.id,
            ),
            app_mode=snapshot.app_mode,
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
                        "code": "DIFY_MODEL_CONFIG_HASH_CONFLICT",
                        "message": (
                            "Current Dify model-config Hash differs from the "
                            "pinned base Hash."
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
            message=(
                "Configured-app Commit stopped because the model-config Hash "
                "changed."
            ),
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
                "code": "CONFIG_COMMIT_FAILED",
                "message": (
                    "Configured-app Commit failed before a successful result "
                    "was persisted."
                ),
            },
        )
        self.store.update_run(failed)
        self.store.append_event(
            run_id=failed.id,
            event_type="agent.failed",
            phase=failed.phase.value,
            message="Configured-app Commit failed.",
            data={"code": "CONFIG_COMMIT_FAILED"},
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
        f"config:{run_id}:{workspace_version_id}:{approval_id}".encode(
            "utf-8"
        )
    ).hexdigest()
