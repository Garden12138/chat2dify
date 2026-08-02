from __future__ import annotations

import os
from pathlib import Path
import sqlite3

import pytest

from app.agent.state import AgentRun, AgentSession
from app.agent.store import AgentStore
from app.studio.models import Principal
from app.studio.store import (
    StudioAccessDenied,
    StudioConflict,
    StudioStore,
)


def _principal(subject: str, tenant: str = "tenant-1") -> Principal:
    return Principal(
        issuer="chat2dify-studio",
        subject=subject,
        display_name=subject,
        email=f"{subject}@example.com",
        dify_tenant_id=tenant,
    )


def test_populated_v4_sqlite_migrates_additively_without_data_loss(
    tmp_path: Path,
) -> None:
    path = tmp_path / "populated.sqlite3"
    v4 = AgentStore(path)
    session = v4.create_session(
        AgentSession(app_id="app-1", app_mode="workflow", app_name="Support")
    )
    run = v4.create_run(AgentRun(session_id=session.id, goal="Keep me."))
    with sqlite3.connect(path) as connection:
        before_tables = {
            row[0]
            for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type = 'table'"
            )
        }
        before_session = connection.execute(
            "SELECT * FROM agent_sessions WHERE id = ?",
            (session.id,),
        ).fetchone()
        before_run_count = connection.execute(
            "SELECT COUNT(*) FROM agent_runs"
        ).fetchone()[0]

    studio = StudioStore(f"sqlite:///{path}")

    with sqlite3.connect(path) as connection:
        after_session = connection.execute(
            "SELECT * FROM agent_sessions WHERE id = ?",
            (session.id,),
        ).fetchone()
        after_run_count = connection.execute(
            "SELECT COUNT(*) FROM agent_runs"
        ).fetchone()[0]
        after_tables = {
            row[0]
            for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type = 'table'"
            )
        }
    assert studio.schema_version() == 2
    assert before_session == after_session
    assert before_run_count == after_run_count == 1
    assert before_tables <= after_tables
    assert "studio_projects" in after_tables
    assert "studio_jobs" in after_tables
    assert "studio_outbox" in after_tables
    assert "studio_receipts" in after_tables
    assert "studio_builds" in after_tables
    assert "studio_candidates" in after_tables
    assert v4.get_run(run.id).goal == "Keep me."


def test_personal_project_is_idempotent_and_cross_project_reads_are_denied(
    tmp_path: Path,
) -> None:
    store = StudioStore(f"sqlite:///{tmp_path / 'studio.sqlite3'}")
    alice = _principal("alice")
    bob = _principal("bob")
    alice_project, alice_membership = store.ensure_personal_project(alice)
    repeated_project, repeated_membership = store.ensure_personal_project(alice)
    bob_project, _ = store.ensure_personal_project(bob)

    assert repeated_project.id == alice_project.id
    assert repeated_membership.id == alice_membership.id
    assert alice_membership.role == "owner"
    assert bob_project.id != alice_project.id
    with pytest.raises(StudioAccessDenied):
        store.get_project_for_principal(bob_project.id, alice.key)
    with pytest.raises(StudioAccessDenied):
        store.list_activity(
            project_id=bob_project.id,
            principal_key=alice.key,
        )


def test_optimistic_project_version_activity_redaction_and_membership_roles(
    tmp_path: Path,
) -> None:
    store = StudioStore(f"sqlite:///{tmp_path / 'studio.sqlite3'}")
    owner = _principal("owner")
    reviewer = _principal("reviewer")
    project, _ = store.create_project(
        name="Team Studio",
        dify_tenant_id=owner.dify_tenant_id,
        owner=owner,
    )
    membership = store.add_membership(
        project_id=project.id,
        actor_key=owner.key,
        principal_key=reviewer.key,
        role="reviewer",
    )
    renamed = store.rename_project(
        project_id=project.id,
        principal_key=owner.key,
        name="Renamed Studio",
        expected_version=project.version,
    )
    store.append_activity(
        project_id=project.id,
        principal_key=owner.key,
        kind="test.activity",
        entity_type="test",
        entity_id="test-1",
        summary={"api_key": "must-not-persist", "safe": "visible"},
    )

    assert membership.role == "reviewer"
    assert renamed.version == project.version + 1
    with pytest.raises(StudioConflict):
        store.rename_project(
            project_id=project.id,
            principal_key=owner.key,
            name="Stale",
            expected_version=project.version,
        )
    activity = store.list_activity(
        project_id=project.id,
        principal_key=owner.key,
    )
    assert "must-not-persist" not in str(activity)
    assert "[REDACTED]" in str(activity)


def test_build_candidates_are_project_scoped_base_bound_and_selectable(
    tmp_path: Path,
) -> None:
    store = StudioStore(f"sqlite:///{tmp_path / 'studio.sqlite3'}")
    alice = _principal("alice")
    bob = _principal("bob")
    project, _ = store.ensure_personal_project(alice)
    store.ensure_personal_project(bob)
    build = store.create_build(
        project_id=project.id,
        principal_key=alice.key,
        operation="modify",
        entry_source="home",
        app_id="app-1",
        app_mode="advanced-chat",
        app_name="售后 Chatflow",
    )
    first = store.add_candidate(
        build_id=build.id,
        project_id=project.id,
        principal_key=alice.key,
        run_id="run-1",
        label="人工接管",
        intent="低置信度时转人工",
    )
    second = store.add_candidate(
        build_id=build.id,
        project_id=project.id,
        principal_key=alice.key,
        run_id="run-2",
        label="二次追问",
        intent="低置信度时继续澄清",
        source_candidate_ids=[first.id],
    )

    assert [item.ordinal for item in store.list_candidates(
        build.id,
        project_id=project.id,
        principal_key=alice.key,
    )] == [1, 2]
    assert store.bind_build_base(build.id, base_fingerprint="hash-1") is True
    assert store.bind_build_base(build.id, base_fingerprint="hash-2") is False
    first = store.reconcile_candidate(
        first.id,
        status="valid",
        base_fingerprint="hash-1",
    )
    selected = store.select_candidate(
        first.id,
        build_id=build.id,
        project_id=project.id,
        principal_key=alice.key,
    )

    assert first.status == "valid"
    assert selected.selected_candidate_id == first.id
    assert second.source_candidate_ids == [first.id]
    with pytest.raises(StudioAccessDenied):
        store.get_build(
            build.id,
            project_id=project.id,
            principal_key=bob.key,
        )


def test_job_outbox_lease_and_receipt_primitives_are_idempotent(
    tmp_path: Path,
) -> None:
    store = StudioStore(f"sqlite:///{tmp_path / 'studio.sqlite3'}")
    owner = _principal("owner")
    project, _ = store.ensure_personal_project(owner)
    first = store.enqueue_job(
        project_id=project.id,
        principal_key=owner.key,
        kind="preview.cleanup",
        payload={"fixture": "fixture-1", "authorization": "secret"},
        idempotency_key="job-key",
    )
    duplicate = store.enqueue_job(
        project_id=project.id,
        principal_key=owner.key,
        kind="preview.cleanup",
        payload={"fixture": "different"},
        idempotency_key="job-key",
    )
    claimed = store.claim_job(worker_id="worker-1", lease_seconds=30)

    assert first.id == duplicate.id
    assert "secret" not in str(first.payload)
    assert claimed is not None
    assert claimed.attempts == 1
    assert store.claim_job(worker_id="worker-2", lease_seconds=30) is None
    heartbeat = store.heartbeat_job(
        job_id=claimed.id,
        worker_id="worker-1",
        expected_version=claimed.version,
        lease_seconds=60,
    )
    completed = store.finish_job(
        job_id=heartbeat.id,
        worker_id="worker-1",
        expected_version=heartbeat.version,
        outcome="completed",
    )
    assert completed.status == "completed"

    outbox = store.enqueue_outbox(
        project_id=project.id,
        principal_key=owner.key,
        topic="review.assigned",
        payload={"review_id": "review-1"},
        idempotency_key="outbox-key",
    )
    assert store.enqueue_outbox(
        project_id=project.id,
        principal_key=owner.key,
        topic="review.assigned",
        payload={"review_id": "review-2"},
        idempotency_key="outbox-key",
    ).id == outbox.id
    claimed_outbox = store.claim_outbox(worker_id="worker-1", lease_seconds=30)
    assert claimed_outbox is not None
    assert claimed_outbox.id == outbox.id
    finished_outbox = store.finish_outbox(
        message_id=claimed_outbox.id,
        worker_id="worker-1",
        expected_version=claimed_outbox.version,
        outcome="completed",
    )
    assert finished_outbox.status == "completed"

    receipt = store.record_receipt(
        project_id=project.id,
        principal_key=owner.key,
        operation="dify.import",
        idempotency_key="receipt-key",
        outcome="succeeded",
        external_ref="app-1",
        details={"hash": "hash-1"},
    )
    assert store.record_receipt(
        project_id=project.id,
        principal_key=owner.key,
        operation="dify.import",
        idempotency_key="receipt-key",
        outcome="succeeded",
        external_ref="app-1",
        details={"hash": "hash-1"},
    ).id == receipt.id
    with pytest.raises(StudioConflict):
        store.record_receipt(
            project_id=project.id,
            principal_key=owner.key,
            operation="dify.import",
            idempotency_key="receipt-key",
            outcome="succeeded",
            external_ref="app-2",
            details={},
        )


@pytest.mark.skipif(
    not os.getenv("CHAT2DIFY_TEST_POSTGRES_URL"),
    reason="CHAT2DIFY_TEST_POSTGRES_URL is not configured",
)
def test_postgresql_repository_contract() -> None:
    store = StudioStore(os.environ["CHAT2DIFY_TEST_POSTGRES_URL"])
    principal = _principal(f"postgres-{os.getpid()}")
    project, membership = store.ensure_personal_project(principal)
    job = store.enqueue_job(
        project_id=project.id,
        principal_key=principal.key,
        kind="contract.test",
        payload={"safe": True},
        idempotency_key=f"contract-{os.getpid()}",
    )
    claimed = store.claim_job(worker_id="postgres-worker", lease_seconds=30)
    assert claimed is not None
    completed = store.finish_job(
        job_id=claimed.id,
        worker_id="postgres-worker",
        expected_version=claimed.version,
        outcome="completed",
    )
    outbox = store.enqueue_outbox(
        project_id=project.id,
        principal_key=principal.key,
        topic="contract.message",
        payload={"safe": True},
        idempotency_key=f"outbox-{os.getpid()}",
    )
    claimed_outbox = store.claim_outbox(
        worker_id="postgres-worker",
        lease_seconds=30,
    )
    assert claimed_outbox is not None
    finished_outbox = store.finish_outbox(
        message_id=claimed_outbox.id,
        worker_id="postgres-worker",
        expected_version=claimed_outbox.version,
        outcome="completed",
    )
    receipt = store.record_receipt(
        project_id=project.id,
        principal_key=principal.key,
        operation="contract.external",
        idempotency_key=f"receipt-{os.getpid()}",
        outcome="succeeded",
        external_ref="external-1",
        details={"safe": True},
    )
    renamed = store.rename_project(
        project_id=project.id,
        principal_key=principal.key,
        name="PostgreSQL Contract",
        expected_version=project.version,
    )

    assert store.dialect == "postgresql"
    assert store.schema_version() == 1
    assert membership.role == "owner"
    assert job.project_id == project.id
    assert completed.status == "completed"
    assert outbox.project_id == project.id
    assert finished_outbox.status == "completed"
    assert receipt.outcome == "succeeded"
    assert renamed.version == project.version + 1
