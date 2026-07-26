from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from typing import Any, Protocol

from app.agent.approval import AgentApprovalService
from app.agent.commit import (
    CommitResult,
    CommitServiceError,
    CreationCommitResult,
    CreationCommitService,
    ModificationCommitService,
)
from app.agent.config_app import CONFIG_APP_MODES
from app.agent.config_commit import ConfigCommitResult, ConfigCommitService
from app.agent.runtime import AgentRuntime
from app.agent.state import (
    AgentBudget,
    AgentRun,
    AgentSession,
    CanvasViewport,
    Observation,
    RunConstraints,
    RunPhase,
    SessionStatus,
    utc_now,
)
from app.agent.store import AgentStore, AgentStoreConflict
from app.agent.trace import redact_sensitive_data
from app.agent.undo import AgentUndoService, UndoResult, UndoServiceError


class RunDispatcher(Protocol):
    def submit(self, run_id: str) -> None: ...

    def close(self) -> None: ...


class ThreadedRunDispatcher:
    def __init__(self, runtime: AgentRuntime, *, workers: int = 2) -> None:
        self.runtime = runtime
        self.executor = ThreadPoolExecutor(
            max_workers=workers,
            thread_name_prefix="chat2dify-agent-v4",
        )

    def submit(self, run_id: str) -> None:
        self.executor.submit(self.runtime.run, run_id)

    def close(self) -> None:
        self.executor.shutdown(wait=False, cancel_futures=False)


class InlineRunDispatcher:
    def __init__(self, runtime: AgentRuntime) -> None:
        self.runtime = runtime

    def submit(self, run_id: str) -> None:
        self.runtime.run(run_id)

    def close(self) -> None:
        return None


class AgentApplicationService:
    def __init__(
        self,
        *,
        store: AgentStore,
        dispatcher: RunDispatcher,
        approval: AgentApprovalService,
        commit_service: ModificationCommitService,
        creation_commit_service: CreationCommitService | None = None,
        config_commit_service: ConfigCommitService | None = None,
        undo_service: AgentUndoService | None = None,
    ) -> None:
        self.store = store
        self.dispatcher = dispatcher
        self.approval = approval
        self.commit_service = commit_service
        self.creation_commit_service = creation_commit_service
        self.config_commit_service = config_commit_service
        self.undo_service = undo_service

    def close(self) -> None:
        self.dispatcher.close()

    def create_session(
        self,
        *,
        app_id: str | None,
        app_mode: str,
        app_name: str | None = None,
        app_description: str = "",
    ) -> AgentSession:
        supported_modes = {
            "workflow",
            "advanced-chat",
            "chat",
            "completion",
            "agent-chat",
        }
        if app_mode not in supported_modes:
            raise ValueError(
                "Builder Agent received an unsupported Dify application mode."
            )
        normalized_app_id = (app_id or "").strip() or None
        if app_mode in CONFIG_APP_MODES and normalized_app_id is None:
            raise ValueError(
                "Configured-app Builder sessions require an existing app_id; "
                "new configured apps remain on the v3 fallback path."
            )
        return self.store.create_session(
            AgentSession(
                operation="modify" if normalized_app_id else "create",
                app_id=normalized_app_id,
                app_mode=app_mode,
                app_name=app_name,
                app_description=app_description,
            )
        )

    def submit_goal(
        self,
        session_id: str,
        *,
        message: str,
        constraints: RunConstraints | None = None,
        budget: AgentBudget | None = None,
    ) -> AgentRun:
        session = self.store.get_session(session_id)
        if session.status != SessionStatus.ACTIVE:
            raise ValueError("Agent Session is closed.")
        effective_constraints = constraints or RunConstraints()
        if session.app_mode in CONFIG_APP_MODES and (
            effective_constraints.allow_draft_test
            or effective_constraints.selected_node_ids
            or effective_constraints.selected_edge_ids
            or effective_constraints.viewport is not None
            or effective_constraints.canvas_draft_hash is not None
            or effective_constraints.dirty_state
        ):
            raise ValueError(
                "Configured-app Runs do not accept Graph canvas context or "
                "Workflow Draft testing."
            )
        if (
            session.operation == "create"
            and self.store.list_runs(session_id=session.id, limit=1)
        ):
            raise ValueError(
                "Create Sessions use one recoverable Agent Run; resume the "
                "existing Run instead of submitting another creation goal."
            )
        if session.operation == "create" and (
            effective_constraints.selected_node_ids
            or effective_constraints.selected_edge_ids
            or effective_constraints.canvas_draft_hash is not None
            or effective_constraints.dirty_state
        ):
            raise ValueError(
                "Create Sessions cannot use existing-canvas selection, dirty state, "
                "or draft Hash constraints."
            )
        run = self.store.create_run(
            AgentRun(
                session_id=session.id,
                goal=str(redact_sensitive_data(message)),
                constraints=effective_constraints,
                budget=budget or AgentBudget(),
            )
        )
        self.dispatcher.submit(run.id)
        return run

    def cancel(self, run_id: str) -> AgentRun:
        run = self.store.get_run(run_id)
        if run.terminal:
            return run
        if run.phase == RunPhase.COMMITTING:
            raise ValueError("A Commit already in progress cannot be cancelled.")
        cancelled = run.transition_to(
            RunPhase.CANCELLED,
            error={
                "code": "AGENT_RUN_CANCELLED",
                "message": "The user cancelled the Agent Run.",
            },
        )
        cancelled = self.store.update_run(cancelled)
        self.store.append_event(
            run_id=run.id,
            event_type="agent.completed",
            phase=cancelled.phase.value,
            message="Agent Run was cancelled.",
            data={"status": "cancelled"},
        )
        return cancelled

    def pause(self, run_id: str) -> AgentRun:
        run = self.store.get_run(run_id)
        if run.terminal or run.phase == RunPhase.PAUSED:
            return run
        if run.phase in {
            RunPhase.WAITING_USER,
            RunPhase.WAITING_APPROVAL,
        }:
            return run
        if run.phase == RunPhase.COMMITTING:
            raise ValueError("A Commit already in progress cannot be paused.")
        paused = self.store.update_run(run.transition_to(RunPhase.PAUSED))
        self.store.append_event(
            run_id=run.id,
            event_type="agent.paused",
            phase=paused.phase.value,
            message="Agent Run was paused explicitly.",
            data={"from_phase": run.phase.value, "side_effect_replay": False},
        )
        return paused

    def resume(
        self,
        run_id: str,
        *,
        message: str | None = None,
    ) -> AgentRun:
        run = self.store.get_run(run_id)
        if run.phase not in {
            RunPhase.WAITING_USER,
            RunPhase.PAUSED,
            RunPhase.INTERRUPTED,
        }:
            raise ValueError(
                "Only waiting_user, paused, or interrupted Runs can resume."
            )
        if run.phase == RunPhase.WAITING_USER and not (message or "").strip():
            raise ValueError("Resuming a waiting_user Run requires a message.")
        observations = list(run.observations)
        if message and message.strip():
            observations.append(
                Observation(
                    kind="user.input",
                    summary=str(
                        redact_sensitive_data(message.strip())
                    )[:8_000],
                )
            )
        target = (
            RunPhase.PLANNING
            if run.head_version_id is not None
            else RunPhase.OBSERVING
        )
        resumed = run.transition_to(target)
        resumed = AgentRun.model_validate(
            {
                **resumed.model_dump(),
                "observations": [
                    item.model_dump(mode="json")
                    for item in observations[-200:]
                ],
                "updated_at": utc_now(),
            }
        )
        resumed = self.store.update_run(resumed)
        self.store.append_event(
            run_id=run.id,
            event_type="agent.resumed",
            phase=resumed.phase.value,
            message="Agent Run resumed explicitly.",
            data={
                "from_phase": run.phase.value,
                "side_effect_replay": False,
            },
        )
        self.dispatcher.submit(run.id)
        return resumed

    def update_canvas_context(
        self,
        run_id: str,
        *,
        selected_node_ids: list[str],
        selected_edge_ids: list[str],
        viewport: CanvasViewport | None,
        current_panel: str | None,
        dirty_state: bool,
        canvas_draft_hash: str | None,
        revision: int,
    ) -> AgentRun:
        run = self.store.get_run(run_id)
        session = self.store.get_session(run.session_id)
        if (
            session.operation != "modify"
            or session.app_mode not in {"workflow", "advanced-chat"}
        ):
            raise ValueError("Create Runs cannot consume existing-canvas context.")
        constraints = RunConstraints(
            allow_draft_test=run.constraints.allow_draft_test,
            allow_destructive=run.constraints.allow_destructive,
            selected_node_ids=selected_node_ids,
            selected_edge_ids=selected_edge_ids,
            viewport=viewport,
            current_panel=current_panel,
            canvas_draft_hash=canvas_draft_hash,
            dirty_state=dirty_state,
            canvas_context_revision=revision,
        )
        try:
            updated = self.store.update_run_canvas_constraints(
                run.id,
                constraints,
            )
        except AgentStoreConflict as exc:
            raise ValueError(str(exc)) from exc
        self.store.append_event(
            run_id=run.id,
            event_type="context.updated",
            phase=updated.phase.value,
            message="Updated bounded Dify canvas context.",
            data={
                "selected_node_count": len(constraints.selected_node_ids),
                "selected_edge_count": len(constraints.selected_edge_ids),
                "current_panel": constraints.current_panel,
                "dirty_state": constraints.dirty_state,
                "canvas_draft_hash": constraints.canvas_draft_hash,
                "revision": constraints.canvas_context_revision,
            },
        )
        return updated

    def undo(
        self,
        run_id: str,
        *,
        workspace_version_id: str,
    ) -> UndoResult:
        if self.undo_service is None:
            raise UndoServiceError(
                "UNDO_SERVICE_UNAVAILABLE",
                "The Agent Undo service is unavailable.",
                status_code=503,
            )
        return self.undo_service.undo(
            run_id,
            workspace_version_id=workspace_version_id,
        )

    def resolve_approval(
        self,
        run_id: str,
        approval_id: str,
        *,
        approved: bool,
        allowed_test_runs: int | None = None,
        test_inputs: dict[str, Any] | None = None,
        test_query: str | None = None,
        test_files: list[dict[str, Any]] | None = None,
    ):
        resolved, next_approval = self.approval.resolve(
            run_id,
            approval_id,
            approved=approved,
            allowed_test_runs=allowed_test_runs,
            test_inputs=test_inputs,
            test_query=test_query,
            test_files=test_files,
        )
        if resolved.action == "draft_run":
            run = self.store.get_run(run_id)
            if run.phase == RunPhase.WAITING_APPROVAL:
                constraints = run.constraints
                if not approved:
                    constraints = RunConstraints.model_validate(
                        {
                            **constraints.model_dump(),
                            "allow_draft_test": False,
                        }
                    )
                observations = [
                    *run.observations,
                    Observation(
                        kind=(
                            "test.approval.approved"
                            if approved
                            else "test.approval.rejected"
                        ),
                        summary=(
                            "User approved the bounded Draft Run."
                            if approved
                            else "User stopped automatic Draft testing."
                        ),
                        data={
                            "approval_id": resolved.id,
                            "input_preview": resolved.scope.get("input_preview"),
                            "remaining_test_runs": resolved.scope.get(
                                "remaining_test_runs", 0
                            ),
                        },
                    ),
                ][-200:]
                resumed = run.transition_to(RunPhase.PLANNING)
                resumed = AgentRun.model_validate(
                    {
                        **resumed.model_dump(),
                        "constraints": constraints.model_dump(),
                        "observations": [
                            item.model_dump(mode="json")
                            for item in observations
                        ],
                        "updated_at": utc_now(),
                    }
                )
                resumed = self.store.update_run(resumed)
                self.store.append_event(
                    run_id=run.id,
                    event_type="agent.resumed",
                    phase=resumed.phase.value,
                    message=(
                        "Agent Run resumed with an approved Draft Run allowance."
                        if approved
                        else "Agent Run resumed with automatic Draft testing disabled."
                    ),
                    data={
                        "approval_id": resolved.id,
                        "approved": approved,
                        "side_effect_replay": False,
                    },
                )
                self.dispatcher.submit(run.id)
        return resolved, next_approval

    def commit(
        self,
        run_id: str,
        *,
        workspace_version_id: str,
        approval_id: str,
    ) -> CommitResult | CreationCommitResult | ConfigCommitResult:
        run = self.store.get_run(run_id)
        session = self.store.get_session(run.session_id)
        if session.app_mode in CONFIG_APP_MODES:
            if self.config_commit_service is None:
                raise CommitServiceError(
                    "CONFIG_COMMIT_ADAPTER_UNAVAILABLE",
                    "The configured-app Commit adapter is unavailable.",
                    status_code=503,
                )
            return self.config_commit_service.commit(
                run_id,
                workspace_version_id=workspace_version_id,
                approval_id=approval_id,
            )
        if (
            session.operation == "create"
            or (
                run.snapshot is not None
                and run.snapshot.operation == "create"
            )
        ):
            if self.creation_commit_service is None:
                raise CommitServiceError(
                    "CREATE_COMMIT_ADAPTER_UNAVAILABLE",
                    "The new-app creation Commit adapter is unavailable.",
                    status_code=503,
                )
            return self.creation_commit_service.commit(
                run_id,
                workspace_version_id=workspace_version_id,
                approval_id=approval_id,
            )
        return self.commit_service.commit(
            run_id,
            workspace_version_id=workspace_version_id,
            approval_id=approval_id,
        )
