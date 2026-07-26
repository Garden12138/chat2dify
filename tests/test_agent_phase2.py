from __future__ import annotations

from dataclasses import dataclass

from fastapi import FastAPI
from fastapi.testclient import TestClient
import pytest

from app.agent.approval import AgentApprovalService
from app.agent.catalog import NodeCapabilityCatalog
from app.agent.commit import CommitServiceError, _assert_canvas_commit_safe
from app.agent.context import BuilderContextBuilder
from app.agent.decision import AgentDecisionProvider
from app.agent.patch import PatchDocument
from app.agent.policy import AgentToolPolicy
from app.agent.registry import ToolRegistry
from app.agent.review import WorkflowReviewService
from app.agent.runtime import AgentRuntime
from app.agent.service import AgentApplicationService
from app.agent.state import (
    AgentRun,
    AgentSession,
    AgentWorkflowSnapshot,
    CanvasViewport,
    GoalPlan,
    GoalStep,
    RunConstraints,
    RunPhase,
)
from app.agent.store import AgentStore
from app.agent.undo import AgentUndoService, UndoServiceError
from app.agent.validation import AgentValidationReport
from app.agent.workspace import VersionedWorkflowWorkspace
from app.api.agent_v4 import router
from app.models import WorkflowPlan


class PassingValidation:
    def validate(self, _plan: WorkflowPlan) -> AgentValidationReport:
        return AgentValidationReport(
            ok=True,
            dsl_version="9.9.9",
            roundtrip_ok=True,
            graph_compiled=True,
        )


class RecordingDispatcher:
    def __init__(self) -> None:
        self.submitted: list[str] = []

    def submit(self, run_id: str) -> None:
        self.submitted.append(run_id)

    def close(self) -> None:
        return None


class UnusedCommitService:
    def commit(self, *_args, **_kwargs):
        raise AssertionError("Commit is not used by these Phase 2 service tests.")


@dataclass
class StaticSnapshotService:
    snapshot: AgentWorkflowSnapshot

    def capture(self, _session: AgentSession) -> AgentWorkflowSnapshot:
        return self.snapshot.model_copy(deep=True)


@dataclass
class PausingSnapshotService:
    store: AgentStore
    run_id: str
    snapshot: AgentWorkflowSnapshot

    def capture(self, _session: AgentSession) -> AgentWorkflowSnapshot:
        current = self.store.get_run(self.run_id)
        self.store.update_run(current.transition_to(RunPhase.PAUSED))
        return self.snapshot.model_copy(deep=True)


class UnexpectedDecisionProvider(AgentDecisionProvider):
    def decide(self, context, tools):
        del context, tools
        raise AssertionError("A Run paused during observation must not call the model.")


def _plan(*, prompt: str = "Be helpful.") -> WorkflowPlan:
    return WorkflowPlan.model_validate(
        {
            "name": "Support",
            "app_mode": "workflow",
            "nodes": [
                {
                    "id": "start",
                    "type": "start",
                    "title": "Start",
                    "params": {"variables": [{"name": "query"}]},
                },
                {
                    "id": "llm-1",
                    "type": "llm",
                    "title": "Reply",
                    "params": {
                        "system_prompt": prompt,
                        "user_prompt": "{{#start.query#}}",
                    },
                },
                {
                    "id": "end",
                    "type": "end",
                    "title": "End",
                    "params": {
                        "outputs": [
                            {
                                "variable": "answer",
                                "value_selector": ["llm-1", "text"],
                            }
                        ]
                    },
                },
            ],
            "edges": [
                {"source": "start", "target": "llm-1"},
                {"source": "llm-1", "target": "end"},
            ],
        }
    )


def _snapshot(plan: WorkflowPlan, *, base_hash: str = "hash-v0") -> AgentWorkflowSnapshot:
    return AgentWorkflowSnapshot(
        app_id="app-1",
        app_name=plan.name,
        app_mode="workflow",
        base_hash=base_hash,
        base_plan=plan.model_dump(mode="json"),
        base_graph={
            "nodes": [
                {"id": node.id, "data": {"type": node.type}}
                for node in plan.nodes
            ],
            "edges": [
                {
                    "id": f"edge-{index}",
                    "source": edge.source,
                    "sourceHandle": edge.source_handle,
                    "target": edge.target,
                    "targetHandle": edge.target_handle,
                }
                for index, edge in enumerate(plan.edges, start=1)
            ],
        },
        capabilities=[],
    )


def _goal_plan() -> GoalPlan:
    return GoalPlan(
        goal="Update the selected LLM.",
        success_criteria=["The selected LLM prompt is updated."],
        steps=[GoalStep(id="edit", description="Edit selected LLM.")],
    )


def _workspace(store: AgentStore) -> VersionedWorkflowWorkspace:
    return VersionedWorkflowWorkspace(
        store=store,
        validation=PassingValidation(),  # type: ignore[arg-type]
        catalog=NodeCapabilityCatalog(),
    )


def _initialize(
    store: AgentStore,
    workspace: VersionedWorkflowWorkspace,
    *,
    constraints: RunConstraints | None = None,
) -> tuple[AgentSession, AgentRun, str]:
    session = store.create_session(
        AgentSession(app_id="app-1", app_mode="workflow")
    )
    run = store.create_run(
        AgentRun(
            session_id=session.id,
            goal="Update the selected LLM.",
            constraints=constraints or RunConstraints(),
        )
    )
    observing = store.update_run(run.transition_to(RunPhase.OBSERVING))
    initialized, version = workspace.initialize(
        observing,
        _snapshot(_plan()),
        _goal_plan(),
    )
    planning = store.update_run(initialized.transition_to(RunPhase.PLANNING))
    return session, planning, version.id


def _apply_prompt_patch(
    store: AgentStore,
    workspace: VersionedWorkflowWorkspace,
    run: AgentRun,
) -> tuple[AgentRun, str]:
    acting = store.update_run(run.transition_to(RunPhase.ACTING))
    result = workspace.apply_patch(
        run.id,
        PatchDocument.model_validate(
            {
                "workspace_version": acting.head_version_id,
                "expected_base_hash": "hash-v0",
                "rationale": "Make the selected LLM prompt professional.",
                "operations": [
                    {
                        "op": "node.update",
                        "node_id": "llm-1",
                        "set": {
                            "params.system_prompt": (
                                "Respond professionally and return JSON."
                            )
                        },
                    }
                ],
            }
        ),
    )
    return store.get_run(run.id), result.workspace_version


def _service(
    store: AgentStore,
    workspace: VersionedWorkflowWorkspace,
    snapshot: AgentWorkflowSnapshot,
) -> tuple[AgentApplicationService, RecordingDispatcher]:
    dispatcher = RecordingDispatcher()
    approval = AgentApprovalService(store=store)
    review = WorkflowReviewService(store=store, workspace=workspace)
    undo = AgentUndoService(
        store=store,
        snapshot=StaticSnapshotService(snapshot),  # type: ignore[arg-type]
        workspace=workspace,
        review=review,
        approval=approval,
    )
    return (
        AgentApplicationService(
            store=store,
            dispatcher=dispatcher,
            approval=approval,
            commit_service=UnusedCommitService(),  # type: ignore[arg-type]
            undo_service=undo,
        ),
        dispatcher,
    )


def _api(store: AgentStore, service: AgentApplicationService) -> FastAPI:
    application = FastAPI()
    application.include_router(router)
    application.state.agent_v4_enabled = True
    application.state.agent_store = store
    application.state.agent_service = service
    return application


def test_selected_canvas_context_uses_authoritative_bounded_graph(tmp_path) -> None:
    store = AgentStore(tmp_path / "agent.sqlite3")
    workspace = _workspace(store)
    constraints = RunConstraints(
        selected_node_ids=["llm-1"],
        selected_edge_ids=["edge-1"],
        viewport=CanvasViewport(x=10, y=20, zoom=1.25),
        current_panel="canvas",
        canvas_draft_hash="hash-v0",
        canvas_context_revision=4,
    )
    _session, run, _version_id = _initialize(
        store,
        workspace,
        constraints=constraints,
    )

    context = BuilderContextBuilder(store=store).build(run)

    assert context.selection["selected_nodes"][0]["id"] == "llm-1"
    assert (
        context.selection["selected_nodes"][0]["params"]["system_prompt"]
        == "Be helpful."
    )
    assert {node["id"] for node in context.selection["neighbor_nodes"]} == {
        "start",
        "end",
    }
    assert context.selection["selected_edges"][0]["id"] == "edge-1"
    assert "graph" not in context.selection
    assert context.selection["context_revision"] == 4


def test_context_api_rejects_raw_graph_and_stale_revision(tmp_path) -> None:
    store = AgentStore(tmp_path / "agent.sqlite3")
    workspace = _workspace(store)
    _session, run, _version_id = _initialize(store, workspace)
    service, _dispatcher = _service(store, workspace, _snapshot(_plan()))
    application = _api(store, service)
    payload = {
        "protocol_version": "1.0",
        "revision": 2,
        "selected_node_ids": ["llm-1"],
        "selected_edge_ids": [],
        "viewport": {"x": 0, "y": 0, "zoom": 1},
        "current_panel": "canvas",
        "dirty_state": False,
        "canvas_draft_hash": "hash-v0",
    }

    with TestClient(application) as client:
        accepted = client.post(
            f"/api/v4/agent/runs/{run.id}/context",
            json=payload,
        )
        stale = client.post(
            f"/api/v4/agent/runs/{run.id}/context",
            json=payload,
        )
        raw_graph = client.post(
            f"/api/v4/agent/runs/{run.id}/context",
            json={**payload, "revision": 3, "raw_graph": {"nodes": []}},
        )

    assert accepted.status_code == 200
    assert accepted.json()["constraints"]["selected_node_ids"] == ["llm-1"]
    assert stale.status_code == 409
    assert stale.json()["detail"]["code"] == "AGENT_CANVAS_CONTEXT_INVALID"
    assert raw_graph.status_code == 422
    assert "raw_graph" not in store.get_run(run.id).constraints.model_dump()
    assert store.list_events(run.id)[-1].type == "context.updated"


@pytest.mark.parametrize(
    ("constraints", "code"),
    [
        (
            RunConstraints(
                dirty_state=True,
                canvas_draft_hash="hash-v0",
            ),
            "COMMIT_CANVAS_DIRTY",
        ),
        (
            RunConstraints(
                dirty_state=False,
                canvas_context_revision=1,
            ),
            "COMMIT_CANVAS_HASH_MISSING",
        ),
        (
            RunConstraints(
                dirty_state=False,
                canvas_draft_hash="hash-other",
            ),
            "COMMIT_CANVAS_HASH_MISMATCH",
        ),
    ],
)
def test_dirty_or_changed_canvas_blocks_commit(constraints, code) -> None:
    run = AgentRun(
        session_id="session",
        goal="Commit safely.",
        constraints=constraints,
        base_hash="hash-v0",
    )

    with pytest.raises(CommitServiceError) as exc_info:
        _assert_canvas_commit_safe(run)

    assert exc_info.value.code == code


def test_explicit_pause_and_resume_are_durable_and_do_not_replay_side_effects(
    tmp_path,
) -> None:
    store = AgentStore(tmp_path / "agent.sqlite3")
    workspace = _workspace(store)
    _session, run, _version_id = _initialize(store, workspace)
    service, dispatcher = _service(store, workspace, _snapshot(_plan()))

    paused = service.pause(run.id)
    reconstructed = AgentStore(store.path).get_run(run.id)
    resumed = service.resume(run.id)

    assert paused.phase == RunPhase.PAUSED
    assert reconstructed.phase == RunPhase.PAUSED
    assert resumed.phase == RunPhase.PLANNING
    assert dispatcher.submitted == [run.id]
    events = store.list_events(run.id)
    assert [event.type for event in events[-2:]] == [
        "agent.paused",
        "agent.resumed",
    ]
    assert events[-1].data["side_effect_replay"] is False


def test_pause_during_snapshot_capture_cannot_be_overwritten(tmp_path) -> None:
    store = AgentStore(tmp_path / "agent.sqlite3")
    workspace = _workspace(store)
    session = store.create_session(
        AgentSession(app_id="app-1", app_mode="workflow")
    )
    run = store.create_run(
        AgentRun(session_id=session.id, goal="Pause while observing.")
    )
    runtime = AgentRuntime(
        store=store,
        snapshot=PausingSnapshotService(  # type: ignore[arg-type]
            store,
            run.id,
            _snapshot(_plan()),
        ),
        workspace=workspace,
        review=WorkflowReviewService(store=store, workspace=workspace),
        approval=AgentApprovalService(store=store),
        registry=ToolRegistry(),
        context_builder=BuilderContextBuilder(store=store),
        decision_provider=UnexpectedDecisionProvider(),
        policy=AgentToolPolicy(),
    )

    result = runtime.run(run.id)
    persisted = store.get_run(run.id)

    assert result["phase"] == RunPhase.PAUSED.value
    assert persisted.phase == RunPhase.PAUSED
    assert persisted.head_version_id is None
    assert store.list_workspace_versions(run.id) == []


def test_precommit_undo_moves_only_workspace_head_and_expires_approval(
    tmp_path,
) -> None:
    store = AgentStore(tmp_path / "agent.sqlite3")
    workspace = _workspace(store)
    _session, run, parent_id = _initialize(store, workspace)
    run, changed_id = _apply_prompt_patch(store, workspace, run)
    validating = store.update_run(run.transition_to(RunPhase.VALIDATING))
    waiting = store.update_run(
        validating.transition_to(RunPhase.WAITING_APPROVAL)
    )
    review = WorkflowReviewService(store=store, workspace=workspace).build(run.id)
    approval = AgentApprovalService(store=store).request_for_review(run.id, review)
    service, _dispatcher = _service(store, workspace, _snapshot(_plan()))

    result = service.undo(
        waiting.id,
        workspace_version_id=changed_id,
    )

    assert result.kind == "pre_commit"
    assert result.run.id == run.id
    assert result.run.phase == RunPhase.INTERRUPTED
    assert result.run.head_version_id == parent_id
    assert store.get_approval(approval.id).status.value == "expired"
    assert len(store.list_workspace_versions(run.id)) == 2
    assert store.list_events(run.id)[-1].type == "workspace.head.moved"


def test_undo_api_returns_public_run_without_authoritative_snapshot(tmp_path) -> None:
    store = AgentStore(tmp_path / "agent.sqlite3")
    workspace = _workspace(store)
    _session, run, parent_id = _initialize(store, workspace)
    run, changed_id = _apply_prompt_patch(store, workspace, run)
    validating = store.update_run(run.transition_to(RunPhase.VALIDATING))
    store.update_run(validating.transition_to(RunPhase.WAITING_APPROVAL))
    service, _dispatcher = _service(store, workspace, _snapshot(_plan()))
    application = _api(store, service)

    with TestClient(application) as client:
        response = client.post(
            f"/api/v4/agent/runs/{run.id}/undo",
            json={"workspace_version_id": changed_id},
        )

    assert response.status_code == 200
    data = response.json()
    assert data["kind"] == "pre_commit"
    assert data["workspace_version_id"] == parent_id
    assert "snapshot" not in data["run"]
    assert "observations" not in data["run"]


def test_postcommit_undo_creates_new_reviewed_compensating_run(tmp_path) -> None:
    store = AgentStore(tmp_path / "agent.sqlite3")
    workspace = _workspace(store)
    _session, run, parent_id = _initialize(store, workspace)
    run, changed_id = _apply_prompt_patch(store, workspace, run)
    validating = store.update_run(run.transition_to(RunPhase.VALIDATING))
    waiting = store.update_run(
        validating.transition_to(RunPhase.WAITING_APPROVAL)
    )
    committing = store.update_run(waiting.transition_to(RunPhase.COMMITTING))
    completed = committing.transition_to(RunPhase.COMPLETED)
    completed = AgentRun.model_validate(
        {
            **completed.model_dump(),
            "commit_result": {
                "kind": "modify",
                "run_id": run.id,
                "workspace_version_id": changed_id,
                "approval_id": "approval-1",
                "idempotency_key": "key",
                "status": "committed",
                "write_performed": True,
                "base_hash": "hash-v0",
                "new_hash": "hash-v1",
            },
        }
    )
    store.update_run(completed)
    changed_plan = WorkflowPlan.model_validate(
        store.get_workspace_version(changed_id).snapshot
    )
    current_snapshot = _snapshot(changed_plan, base_hash="hash-v1")
    service, _dispatcher = _service(store, workspace, current_snapshot)

    result = service.undo(
        completed.id,
        workspace_version_id=changed_id,
    )

    assert result.kind == "post_commit"
    assert result.run.id != completed.id
    assert result.run.phase == RunPhase.WAITING_APPROVAL
    assert result.run.review["workspace_version_id"] == result.workspace_version_id
    compensating_plan = WorkflowPlan.model_validate(
        store.get_workspace_version(result.workspace_version_id).snapshot
    )
    assert (
        compensating_plan.model_dump(mode="json")
        == WorkflowPlan.model_validate(
            store.get_workspace_version(parent_id).snapshot
        ).model_dump(mode="json")
    )
    approvals = store.list_approvals(result.run.id)
    assert approvals
    assert approvals[0].workspace_version_id == result.workspace_version_id
    assert store.get_run(completed.id).phase == RunPhase.COMPLETED


def test_postcommit_undo_fails_closed_when_dify_hash_changed(tmp_path) -> None:
    store = AgentStore(tmp_path / "agent.sqlite3")
    workspace = _workspace(store)
    _session, run, _parent_id = _initialize(store, workspace)
    run, changed_id = _apply_prompt_patch(store, workspace, run)
    validating = store.update_run(run.transition_to(RunPhase.VALIDATING))
    waiting = store.update_run(
        validating.transition_to(RunPhase.WAITING_APPROVAL)
    )
    committing = store.update_run(waiting.transition_to(RunPhase.COMMITTING))
    completed = committing.transition_to(RunPhase.COMPLETED)
    completed = AgentRun.model_validate(
        {
            **completed.model_dump(),
            "commit_result": {
                "kind": "modify",
                "run_id": run.id,
                "workspace_version_id": changed_id,
                "approval_id": "approval-1",
                "idempotency_key": "key",
                "status": "committed",
                "write_performed": True,
                "base_hash": "hash-v0",
                "new_hash": "hash-v1",
            },
        }
    )
    store.update_run(completed)
    changed_plan = WorkflowPlan.model_validate(
        store.get_workspace_version(changed_id).snapshot
    )
    service, _dispatcher = _service(
        store,
        workspace,
        _snapshot(changed_plan, base_hash="hash-v2"),
    )

    with pytest.raises(UndoServiceError) as exc_info:
        service.undo(completed.id, workspace_version_id=changed_id)

    assert exc_info.value.code == "UNDO_DIFY_HASH_CONFLICT"
    assert len(store.list_runs(session_id=completed.session_id)) == 1
