from __future__ import annotations

from typing import Literal

from app.agent.approval import AgentApprovalService
from app.agent.review import WorkflowReviewService
from app.agent.snapshot import WorkflowSnapshotService
from app.agent.state import (
    AgentRun,
    GoalPlan,
    GoalStep,
    RunConstraints,
    RunPhase,
    StrictModel,
    utc_now,
)
from app.agent.store import AgentStore
from app.agent.workspace import (
    VersionedWorkflowWorkspace,
    WorkspaceOperationError,
)


class UndoServiceError(RuntimeError):
    def __init__(self, code: str, message: str, *, status_code: int = 409) -> None:
        super().__init__(message)
        self.code = code
        self.status_code = status_code


class UndoResult(StrictModel):
    kind: Literal["pre_commit", "post_commit"]
    source_run_id: str
    run: AgentRun
    from_version_id: str
    workspace_version_id: str


class AgentUndoService:
    def __init__(
        self,
        *,
        store: AgentStore,
        snapshot: WorkflowSnapshotService,
        workspace: VersionedWorkflowWorkspace,
        review: WorkflowReviewService,
        approval: AgentApprovalService,
    ) -> None:
        self.store = store
        self.snapshot = snapshot
        self.workspace = workspace
        self.review = review
        self.approval = approval

    def undo(
        self,
        run_id: str,
        *,
        workspace_version_id: str,
    ) -> UndoResult:
        source_run = self.store.get_run(run_id)
        if source_run.head_version_id != workspace_version_id:
            raise UndoServiceError(
                "UNDO_WORKSPACE_VERSION_MISMATCH",
                "Undo must target the exact visible Workspace version.",
            )
        if source_run.commit_result is None:
            return self._undo_pre_commit(source_run, workspace_version_id)
        return self._create_compensating_preview(source_run, workspace_version_id)

    def _undo_pre_commit(
        self,
        run: AgentRun,
        workspace_version_id: str,
    ) -> UndoResult:
        try:
            result = self.workspace.undo_head(
                run.id,
                expected_head_id=workspace_version_id,
            )
        except WorkspaceOperationError as exc:
            raise UndoServiceError(exc.code, str(exc)) from exc
        updated = self.store.get_run(run.id)
        return UndoResult(
            kind="pre_commit",
            source_run_id=run.id,
            run=updated,
            from_version_id=result.from_version_id,
            workspace_version_id=result.workspace_version_id,
        )

    def _create_compensating_preview(
        self,
        source_run: AgentRun,
        workspace_version_id: str,
    ) -> UndoResult:
        if source_run.phase != RunPhase.COMPLETED:
            raise UndoServiceError(
                "UNDO_COMMITTED_RUN_STATE_INVALID",
                "Post-commit Undo requires a successfully completed Run.",
            )
        expected_hash = _committed_hash(source_run)
        if expected_hash is None:
            raise UndoServiceError(
                "UNDO_COMMIT_RESULT_INVALID",
                "The completed Run has no Dify write to compensate.",
            )
        source_version = self.store.get_workspace_version(workspace_version_id)
        if source_version.run_id != source_run.id or source_version.parent_id is None:
            raise UndoServiceError(
                "UNDO_SOURCE_VERSION_INVALID",
                "The committed Workspace version is not reversible.",
            )
        session = self.store.get_session(source_run.session_id)
        if session.operation != "modify" or not session.app_id:
            raise UndoServiceError(
                "UNDO_SESSION_NOT_MODIFIABLE",
                "Post-commit Undo requires a Session bound to the imported or modified app.",
            )
        snapshot = self.snapshot.capture(session)
        if snapshot.base_hash != expected_hash:
            raise UndoServiceError(
                "UNDO_DIFY_HASH_CONFLICT",
                "The Dify draft changed after Commit; a compensating preview was not created.",
            )
        goal = (
            "Create a compensating preview for committed Workspace version "
            f"{workspace_version_id}."
        )
        run = self.store.create_run(
            AgentRun(
                session_id=session.id,
                goal=goal,
                constraints=RunConstraints(
                    canvas_draft_hash=snapshot.base_hash,
                    dirty_state=False,
                ),
            )
        )
        observing = self.store.update_run(run.transition_to(RunPhase.OBSERVING))
        self.store.append_event(
            run_id=run.id,
            event_type="agent.started",
            phase=observing.phase.value,
            message="Started a deterministic post-commit compensating Undo.",
            data={
                "source_run_id": source_run.id,
                "source_version_id": source_version.id,
            },
        )
        goal_plan = _compensating_goal_plan(goal)
        initialized, base_version = self.workspace.initialize(
            observing,
            snapshot,
            goal_plan,
        )
        self.store.append_event(
            run_id=run.id,
            event_type="context.loaded",
            phase=initialized.phase.value,
            message="Loaded the current authoritative Dify draft for compensating Undo.",
            data={
                "app_id": snapshot.app_id,
                "base_hash": snapshot.base_hash,
                "workspace_version_id": base_version.id,
                "source_run_id": source_run.id,
            },
        )
        self.store.append_event(
            run_id=run.id,
            event_type="goal_plan.created",
            phase=initialized.phase.value,
            message="Created the deterministic compensating Undo Goal Plan.",
            data=goal_plan.model_dump(mode="json"),
        )
        planning = self.store.update_run(
            initialized.transition_to(RunPhase.PLANNING)
        )
        acting = self.store.update_run(planning.transition_to(RunPhase.ACTING))
        try:
            preview = self.workspace.create_compensating_preview(
                run.id,
                source_version=source_version,
            )
        except WorkspaceOperationError as exc:
            failed = self.store.get_run(run.id).transition_to(
                RunPhase.FAILED,
                error={"code": exc.code, "message": str(exc)},
            )
            self.store.update_run(failed)
            self.store.append_event(
                run_id=run.id,
                event_type="agent.failed",
                phase=failed.phase.value,
                message="Compensating Undo preview could not be created.",
                data=failed.error or {},
            )
            raise UndoServiceError(exc.code, str(exc)) from exc
        validating = self.store.update_run(
            self.store.get_run(acting.id).transition_to(RunPhase.VALIDATING)
        )
        self.store.append_event(
            run_id=run.id,
            event_type="validation.passed",
            phase=validating.phase.value,
            message="Compensating Undo passed deterministic validation.",
            data=preview.validation.model_dump(mode="json"),
        )
        review = self.review.build(run.id)
        ready = AgentRun.model_validate(
            {
                **self.store.get_run(run.id)
                .transition_to(RunPhase.WAITING_APPROVAL)
                .model_dump(),
                "review": review.model_dump(mode="json"),
                "updated_at": utc_now(),
            }
        )
        ready = self.store.update_run(ready)
        self.store.append_event(
            run_id=run.id,
            event_type="review.ready",
            phase=ready.phase.value,
            message="Compensating Undo Diff is ready for review.",
            data=review.model_dump(mode="json"),
        )
        self.approval.request_for_review(run.id, review)
        self.store.append_event(
            run_id=run.id,
            event_type="agent.paused",
            phase=ready.phase.value,
            message="Compensating Undo is paused for persisted user approval.",
            data={"source_run_id": source_run.id},
        )
        return UndoResult(
            kind="post_commit",
            source_run_id=source_run.id,
            run=self.store.get_run(run.id),
            from_version_id=workspace_version_id,
            workspace_version_id=preview.workspace_version_id,
        )


def _committed_hash(run: AgentRun) -> str | None:
    result = run.commit_result or {}
    if result.get("kind") == "create":
        value = result.get("draft_hash")
        return str(value) if value else None
    if not bool(result.get("write_performed")):
        return None
    value = result.get("new_hash")
    return str(value) if value else None


def _compensating_goal_plan(goal: str) -> GoalPlan:
    return GoalPlan(
        goal=goal,
        constraints=[
            "Use the persisted reverse Workspace snapshot.",
            "Do not write Dify before a new version-bound approval.",
            "Fail closed when the current Dify Hash changed.",
        ],
        success_criteria=[
            "The compensating Plan passes deterministic validation.",
            "A business Diff and risk review are available before Commit.",
        ],
        steps=[
            GoalStep(
                id="observe",
                description="Read the current authoritative Dify draft.",
                status="completed",
                evidence=["Current Dify Hash matches the completed Commit."],
            ),
            GoalStep(
                id="restore",
                description="Create a compensating Workspace version.",
                status="completed",
                depends_on=["observe"],
                evidence=["Persisted reverse snapshot was applied."],
            ),
            GoalStep(
                id="review",
                description="Validate and review the compensating Diff.",
                status="completed",
                depends_on=["restore"],
                evidence=["Deterministic validation and review completed."],
            ),
        ],
    )
