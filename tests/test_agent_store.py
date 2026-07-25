from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
import sqlite3

import pytest

from app.agent.state import (
    AgentApproval,
    AgentRun,
    AgentSession,
    ApprovalStatus,
    RunPhase,
    SessionStatus,
    WorkspaceVersion,
    utc_now,
)
from app.agent.store import AgentStore
from app.tasks import TaskRepository


def test_agent_store_reinitializes_without_losing_v3_tasks(tmp_path) -> None:
    path = tmp_path / "tasks.sqlite3"
    task_repository = TaskRepository(path)
    task = task_repository.create("workflow.create", {"message": "keep me"})

    first = AgentStore(path)
    session = first.create_session(AgentSession(app_id="app-1", app_mode="workflow"))
    run = first.create_run(
        AgentRun(
            session_id=session.id,
            goal="Build a reviewed change.",
            base_hash="base-hash",
        )
    )
    version = first.create_workspace_version(
        WorkspaceVersion(
            run_id=run.id,
            base_hash="base-hash",
            snapshot={"name": "safe plan", "nodes": [], "edges": []},
        )
    )
    first.update_workspace_version(version.id, validation={"ok": True})
    approval = first.create_approval(
        AgentApproval(
            run_id=run.id,
            workspace_version_id=version.id,
            action="commit",
            scope={"base_hash": "base-hash"},
            expires_at=utc_now() + timedelta(hours=1),
        )
    )

    second = AgentStore(path)
    second.initialize()

    assert TaskRepository(path).get(task.id).request == {"message": "keep me"}
    assert second.get_session(session.id) == session
    assert second.get_run(run.id) == run
    assert second.get_workspace_version(version.id).validation == {"ok": True}
    assert second.get_approval(approval.id) == approval
    assert second.list_sessions() == [session]
    assert second.list_runs(session_id=session.id) == [run]
    assert [item.id for item in second.list_workspace_versions(run.id)] == [version.id]
    assert second.list_approvals(run.id) == [approval]

    updated_session = AgentSession(
        **{
            **session.model_dump(),
            "status": SessionStatus.CLOSED,
            "updated_at": utc_now(),
        }
    )
    assert second.update_session(updated_session).status == SessionStatus.CLOSED

    with sqlite3.connect(path) as connection:
        assert connection.execute("PRAGMA journal_mode").fetchone()[0].lower() == "wal"
        tables = {
            row[0]
            for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type = 'table'"
            ).fetchall()
        }
    assert {
        "workflow_tasks",
        "agent_sessions",
        "agent_runs",
        "agent_events",
        "agent_workspace_versions",
        "agent_approvals",
    } <= tables

    with pytest.raises(sqlite3.IntegrityError):
        second.create_run(
            AgentRun(session_id="missing-session", goal="Must fail the foreign key.")
        )


def test_events_are_ordered_durable_and_redacted_before_persistence(tmp_path) -> None:
    path = tmp_path / "tasks.sqlite3"
    store = AgentStore(path)
    session = store.create_session(AgentSession(app_id="app-1", app_mode="workflow"))
    run = store.create_run(AgentRun(session_id=session.id, goal="Trace safely."))

    def append(index: int):
        return store.append_event(
            run_id=run.id,
            event_type="tool.completed",
            phase="acting",
            message=f"Tool {index} completed with Bearer raw-token-{index}.",
            data={
                "index": index,
                "api_key": f"secret-{index}",
                "headers": {
                    "Authorization": f"Bearer authorization-{index}",
                    "X-Safe": "visible",
                },
            },
        )

    with ThreadPoolExecutor(max_workers=6) as executor:
        list(executor.map(append, range(12)))

    reconstructed = AgentStore(path)
    events = reconstructed.list_events(run.id)

    assert [event.seq for event in events] == list(range(1, 13))
    assert all(event.data["api_key"] == "[REDACTED]" for event in events)
    assert all(event.data["headers"]["Authorization"] == "[REDACTED]" for event in events)
    assert all(event.data["headers"]["X-Safe"] == "visible" for event in events)
    assert reconstructed.list_events(run.id, after_seq=7)[0].seq == 8
    assert reconstructed.get_event(run.id, 1) == events[0]

    database_text = path.read_bytes().decode("utf-8", errors="ignore")
    assert "secret-" not in database_text
    assert "authorization-" not in database_text
    assert "raw-token-" not in database_text


def test_run_and_approval_updates_are_persisted(tmp_path) -> None:
    store = AgentStore(tmp_path / "tasks.sqlite3")
    session = store.create_session(AgentSession(app_id="app-1", app_mode="advanced-chat"))
    run = store.create_run(AgentRun(session_id=session.id, goal="Pause safely."))
    observing = store.update_run(run.transition_to(RunPhase.OBSERVING))

    assert observing.phase == RunPhase.OBSERVING

    version = store.create_workspace_version(
        WorkspaceVersion(run_id=run.id, snapshot={"nodes": [], "edges": []})
    )
    pending = store.create_approval(
        AgentApproval(
            run_id=run.id,
            workspace_version_id=version.id,
            action="draft_run",
            scope={"allowed_test_runs": 1},
            expires_at=utc_now() + timedelta(minutes=10),
        )
    )
    resolved = AgentApproval(
        **{
            **pending.model_dump(),
            "status": ApprovalStatus.REJECTED,
            "resolved_at": utc_now(),
        }
    )

    assert store.update_approval(resolved).status == ApprovalStatus.REJECTED
