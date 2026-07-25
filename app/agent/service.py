from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from typing import Protocol

from app.agent.approval import AgentApprovalService
from app.agent.commit import CommitResult, ModificationCommitService
from app.agent.runtime import AgentRuntime
from app.agent.state import (
    AgentBudget,
    AgentRun,
    AgentSession,
    Observation,
    RunConstraints,
    RunPhase,
    SessionStatus,
    utc_now,
)
from app.agent.store import AgentStore
from app.agent.trace import redact_sensitive_data


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
    ) -> None:
        self.store = store
        self.dispatcher = dispatcher
        self.approval = approval
        self.commit_service = commit_service

    def close(self) -> None:
        self.dispatcher.close()

    def create_session(
        self,
        *,
        app_id: str,
        app_mode: str,
    ) -> AgentSession:
        if app_mode not in {"workflow", "advanced-chat"}:
            raise ValueError("Phase 1A supports only workflow and advanced-chat.")
        return self.store.create_session(
            AgentSession(app_id=app_id, app_mode=app_mode)
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
        run = self.store.create_run(
            AgentRun(
                session_id=session.id,
                goal=str(redact_sensitive_data(message)),
                constraints=constraints or RunConstraints(),
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

    def resume(
        self,
        run_id: str,
        *,
        message: str | None = None,
    ) -> AgentRun:
        run = self.store.get_run(run_id)
        if run.phase not in {RunPhase.WAITING_USER, RunPhase.INTERRUPTED}:
            raise ValueError("Only waiting_user or interrupted Runs can resume.")
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
            event_type="agent.started",
            phase=resumed.phase.value,
            message="Agent Run resumed explicitly.",
            data={"from_phase": run.phase.value},
        )
        self.dispatcher.submit(run.id)
        return resumed

    def resolve_approval(
        self,
        run_id: str,
        approval_id: str,
        *,
        approved: bool,
    ):
        return self.approval.resolve(
            run_id,
            approval_id,
            approved=approved,
        )

    def commit(
        self,
        run_id: str,
        *,
        workspace_version_id: str,
        approval_id: str,
    ) -> CommitResult:
        return self.commit_service.commit(
            run_id,
            workspace_version_id=workspace_version_id,
            approval_id=approval_id,
        )
