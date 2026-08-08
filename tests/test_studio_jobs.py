from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
import os
import time
from pathlib import Path
from threading import Lock
from types import SimpleNamespace
from uuid import uuid4

import pytest

from app.agent.state import RunConstraints, RunPhase
from app.studio.jobs import (
    DefiniteWorkerFailure,
    StudioDurableWorker,
    WorkerResult,
    build_agent_run_handler,
    local_audit_notification,
    scenario_run_handler,
)
from app.studio.models import ScenarioRunPolicy
from app.studio.scenarios import StudioScenarioService
from app.studio.store import StudioStore
from tests.test_studio_store import _principal
from tests.test_studio_scenarios import _stack as _scenario_stack, _suite


def _store(tmp_path: Path):
    store = StudioStore(f"sqlite:///{tmp_path / 'worker.sqlite3'}")
    owner = _principal("worker-owner")
    project, _ = store.ensure_personal_project(owner)
    return store, owner, project


def test_build_agent_run_is_workspace_only_and_delivered_once(tmp_path: Path):
    store, owner, project = _store(tmp_path)

    class FakeAgentService:
        def __init__(self):
            self.store = self
            self.calls = 0

        def get_run(self, run_id):
            assert run_id == "agent-run-1"
            return SimpleNamespace(constraints=RunConstraints(workspace_only=True))

        def execute_run(self, run_id):
            assert run_id == "agent-run-1"
            self.calls += 1
            return SimpleNamespace(phase=RunPhase.COMPLETED)

    agent = FakeAgentService()
    store.enqueue_job(
        project_id=project.id,
        principal_key=owner.key,
        kind="build.agent_run",
        payload={
            "run_id": "agent-run-1",
            "candidate_id": "candidate-1",
            "workspace_only": True,
            "production_write": False,
        },
        idempotency_key="build-agent-run:agent-run-1",
        max_attempts=1,
    )
    worker = StudioDurableWorker(
        store=store,
        worker_id="build-worker",
        job_handlers={"build.agent_run": build_agent_run_handler(agent_service=agent)},
        lease_seconds=5,
        heartbeat_seconds=0.2,
    )
    assert worker.run_once() is True
    assert agent.calls == 1
    assert store.list_jobs(
        project_id=project.id,
        principal_key=owner.key,
    )[0].status == "completed"
    assert worker.run_once() is False
    assert agent.calls == 1


@pytest.mark.skipif(
    not os.getenv("CHAT2DIFY_TEST_POSTGRES_URL"),
    reason="CHAT2DIFY_TEST_POSTGRES_URL is not configured",
)
def test_postgresql_workers_claim_one_external_operation_once():
    store = StudioStore(os.environ["CHAT2DIFY_TEST_POSTGRES_URL"])
    suffix = uuid4().hex
    owner = _principal(f"multi-worker-{suffix}")
    project, _ = store.ensure_personal_project(owner)
    calls: list[str] = []
    calls_guard = Lock()

    def external_once(_payload, context):
        with calls_guard:
            calls.append(context.idempotency_key)
        time.sleep(0.25)
        return WorkerResult(external_ref="postgres-receipt", details={"safe": True})

    store.enqueue_job(
        project_id=project.id,
        principal_key=owner.key,
        kind="postgres.external_once",
        payload={"safe": True},
        idempotency_key=f"postgres-worker-{suffix}",
        max_attempts=1,
    )
    workers = [
        StudioDurableWorker(
            store=store,
            worker_id=f"postgres-worker-{index}-{suffix}",
            job_handlers={"postgres.external_once": external_once},
            lease_seconds=5,
            heartbeat_seconds=0.2,
        )
        for index in range(2)
    ]
    with ThreadPoolExecutor(max_workers=2) as executor:
        claimed = list(executor.map(lambda worker: worker.run_once(), workers))
    assert any(claimed)
    assert calls == [f"postgres-worker-{suffix}"]
    assert store.list_jobs(
        project_id=project.id,
        principal_key=owner.key,
    )[0].status == "completed"


@pytest.mark.skipif(
    not os.getenv("CHAT2DIFY_TEST_POSTGRES_URL"),
    reason="CHAT2DIFY_TEST_POSTGRES_URL is not configured",
)
def test_postgresql_restart_reconciles_job_and_outbox_without_replay():
    store = StudioStore(os.environ["CHAT2DIFY_TEST_POSTGRES_URL"])
    suffix = uuid4().hex
    owner = _principal(f"postgres-recovery-{suffix}")
    project, _ = store.ensure_personal_project(owner)
    job = store.enqueue_job(
        project_id=project.id,
        principal_key=owner.key,
        kind="postgres.ambiguous_job",
        payload={"safe": True},
        idempotency_key=f"postgres-ambiguous-job-{suffix}",
        max_attempts=3,
    )
    claimed_job = store.claim_job(
        worker_id=f"crashed-job-{suffix}",
        lease_seconds=-1,
    )
    assert claimed_job is not None and claimed_job.id == job.id
    job_receipt, created = store.begin_worker_receipt(
        entity_type="job",
        entity_id=claimed_job.id,
        worker_id=f"crashed-job-{suffix}",
        expected_version=claimed_job.version,
    )
    assert created is True and job_receipt.outcome == "pending"

    outbox = store.enqueue_outbox(
        project_id=project.id,
        principal_key=owner.key,
        topic="notification.release_gate",
        payload={"adapter_ref": "audit:postgres-release-gate", "safe": True},
        idempotency_key=f"postgres-ambiguous-outbox-{suffix}",
        max_attempts=3,
    )
    claimed_outbox = store.claim_outbox(
        worker_id=f"crashed-outbox-{suffix}",
        lease_seconds=-1,
    )
    assert claimed_outbox is not None and claimed_outbox.id == outbox.id
    outbox_receipt, created = store.begin_worker_receipt(
        entity_type="outbox",
        entity_id=claimed_outbox.id,
        worker_id=f"crashed-outbox-{suffix}",
        expected_version=claimed_outbox.version,
    )
    assert created is True and outbox_receipt.outcome == "pending"

    calls = 0

    def must_not_replay(_payload, _context):
        nonlocal calls
        calls += 1
        return WorkerResult()

    restarted = StudioDurableWorker(
        store=store,
        worker_id=f"restarted-{suffix}",
        job_handlers={"postgres.ambiguous_job": must_not_replay},
        notification_adapters={
            "audit:postgres-release-gate": must_not_replay,
        },
        lease_seconds=5,
    )
    assert restarted.run_once() is True
    assert restarted.run_once() is True
    assert calls == 0
    assert store.list_jobs(
        project_id=project.id,
        principal_key=owner.key,
    )[0].status == "ambiguous"
    assert store.list_outbox(
        project_id=project.id,
        principal_key=owner.key,
    )[0].status == "ambiguous"


def test_worker_success_has_operation_receipt_and_outbox_delivery(tmp_path: Path):
    store, owner, project = _store(tmp_path)
    calls: list[str] = []

    def handler(payload, context):
        calls.append(context.idempotency_key)
        return WorkerResult(
            external_ref="result-1",
            details={"safe": payload["safe"]},
        )

    job = store.enqueue_job(
        project_id=project.id,
        principal_key=owner.key,
        kind="build.execute",
        payload={"safe": True},
        idempotency_key="build-1",
    )
    outbox = store.enqueue_outbox(
        project_id=project.id,
        principal_key=owner.key,
        topic="notification.run_alert",
        payload={
            "adapter_ref": "audit:local",
            "sanitized": True,
            "stable_error_code": "EXECUTION_TIMEOUT",
            "failures": 2,
        },
        idempotency_key="alert-1",
    )
    worker = StudioDurableWorker(
        store=store,
        worker_id="worker-1",
        job_handlers={"build.execute": handler},
        notification_adapters={"audit:local": local_audit_notification},
        lease_seconds=5,
        heartbeat_seconds=0.2,
    )
    assert worker.run_once() is True
    assert worker.run_once() is True
    assert worker.run_once() is False
    assert calls == ["build-1"]
    jobs = store.list_jobs(project_id=project.id, principal_key=owner.key)
    messages = store.list_outbox(project_id=project.id, principal_key=owner.key)
    assert jobs[0].id == job.id and jobs[0].status == "completed"
    assert messages[0].id == outbox.id and messages[0].status == "completed"
    receipts = store.list_receipts(
        project_id=project.id,
        principal_key=owner.key,
        operation_prefix="worker:",
    )
    assert [item.outcome for item in receipts] == ["succeeded", "succeeded"]


def test_restart_after_intent_never_replays_ambiguous_external_write(tmp_path: Path):
    store, owner, project = _store(tmp_path)
    job = store.enqueue_job(
        project_id=project.id,
        principal_key=owner.key,
        kind="release.external",
        payload={"safe": True},
        idempotency_key="release-write-1",
        max_attempts=3,
    )
    first = store.claim_job(worker_id="crashed-worker", lease_seconds=-1)
    assert first is not None and first.id == job.id
    receipt, created = store.begin_worker_receipt(
        entity_type="job",
        entity_id=first.id,
        worker_id="crashed-worker",
        expected_version=first.version,
    )
    assert created is True and receipt.outcome == "pending"
    calls = 0

    def must_not_run(_payload, _context):
        nonlocal calls
        calls += 1
        return WorkerResult()

    restarted = StudioDurableWorker(
        store=store,
        worker_id="restarted-worker",
        job_handlers={"release.external": must_not_run},
        lease_seconds=5,
    )
    assert restarted.run_once() is True
    assert calls == 0
    stored = store.list_jobs(project_id=project.id, principal_key=owner.key)[0]
    assert stored.status == "ambiguous"
    assert stored.attempts == 2


def test_heartbeat_prevents_competing_claim_and_definite_failures_dead_letter(
    tmp_path: Path,
):
    store, owner, project = _store(tmp_path)
    competing_claims = []

    def bounded_handler(_payload, _context):
        time.sleep(0.65)
        competing_claims.append(
            store.claim_job(worker_id="worker-2", lease_seconds=2)
        )
        return WorkerResult(details={"bounded": True})

    store.enqueue_job(
        project_id=project.id,
        principal_key=owner.key,
        kind="scenario.preview",
        payload={"safe": True},
        idempotency_key="preview-1",
        max_attempts=2,
    )
    worker = StudioDurableWorker(
        store=store,
        worker_id="worker-1",
        job_handlers={"scenario.preview": bounded_handler},
        lease_seconds=2,
        heartbeat_seconds=0.2,
    )
    assert worker.run_once() is True
    assert competing_claims == [None]

    attempts = 0

    def definite_failure(_payload, _context):
        nonlocal attempts
        attempts += 1
        raise DefiniteWorkerFailure("Adapter rejected before accepting work.")

    store.enqueue_job(
        project_id=project.id,
        principal_key=owner.key,
        kind="notification.definite_failure",
        payload={"safe": True},
        idempotency_key="failure-1",
        max_attempts=2,
    )
    failing = StudioDurableWorker(
        store=store,
        worker_id="worker-fail",
        job_handlers={"notification.definite_failure": definite_failure},
        lease_seconds=2,
        heartbeat_seconds=0.2,
    )
    assert failing.run_once() is True
    assert failing.run_once() is True
    assert attempts == 2
    failed = next(
        item
        for item in store.list_jobs(project_id=project.id, principal_key=owner.key)
        if item.kind == "notification.definite_failure"
    )
    assert failed.status == "dead_letter"


def test_cancellation_and_expired_exhausted_lease_are_terminal(tmp_path: Path):
    store, owner, project = _store(tmp_path)
    cancelled = store.enqueue_job(
        project_id=project.id,
        principal_key=owner.key,
        kind="scenario.cancelled",
        payload={"safe": True},
        idempotency_key="cancel-1",
    )
    store.request_work_cancel(
        project_id=project.id,
        principal_key=owner.key,
        entity_type="job",
        entity_id=cancelled.id,
        reason="Operator stopped the scheduled check.",
    )
    calls = 0

    def must_not_run(_payload, _context):
        nonlocal calls
        calls += 1
        return WorkerResult()

    worker = StudioDurableWorker(
        store=store,
        worker_id="worker-cancel",
        job_handlers={"scenario.cancelled": must_not_run},
    )
    assert worker.run_once() is True
    assert calls == 0
    assert store.list_jobs(
        project_id=project.id,
        principal_key=owner.key,
    )[0].status == "cancelled"

    exhausted = store.enqueue_job(
        project_id=project.id,
        principal_key=owner.key,
        kind="cleanup.exhausted",
        payload={"safe": True},
        idempotency_key="exhausted-1",
        max_attempts=1,
    )
    claimed = store.claim_job(worker_id="lost-worker", lease_seconds=-1)
    assert claimed is not None and claimed.id == exhausted.id
    assert store.reconcile_exhausted_work() == {
        "dead_letter": 1,
        "ambiguous": 0,
    }


def test_normal_scenario_preview_runs_through_durable_worker(tmp_path: Path):
    (
        base_service,
        store,
        agent_store,
        preview,
        authenticated,
        project,
        build,
        candidate_ids,
    ) = _scenario_stack(tmp_path)
    _, suite = _suite(
        base_service,
        authenticated,
        project,
        build,
        candidate_ids,
    )
    service = StudioScenarioService(
        store=store,
        build_service=base_service.build_service,
        agent_store=agent_store,
        compiler=base_service.compiler,
        catalog=base_service.catalog,
        preview=preview,
        background_workers=0,
        durable_jobs=True,
    )
    lab = service.lab(
        authenticated,
        project_id=project.id,
        build_id=build.id,
    )
    assert lab.environment is not None
    run = service.run_suite(
        authenticated,
        project_id=project.id,
        build_id=build.id,
        suite_id=suite.id,
        environment_id=lab.environment.id,
        candidate_ids=candidate_ids,
        mappings=[],
        policy=ScenarioRunPolicy(),
    )
    assert run.status == "pending"
    jobs = store.list_jobs(
        project_id=project.id,
        principal_key=authenticated.principal.key,
    )
    assert len(jobs) == 1
    assert jobs[0].kind == "scenario.run"
    worker = StudioDurableWorker(
        store=store,
        worker_id="scenario-worker",
        job_handlers={
            "scenario.run": scenario_run_handler(
                store=store,
                scenario_service=service,
            )
        },
        lease_seconds=5,
        heartbeat_seconds=0.2,
    )
    assert worker.run_once() is True
    completed = service.get_run(
        authenticated,
        project_id=project.id,
        run_id=run.id,
    )
    assert completed.status == "completed"
    assert completed.cleanup_verified is True
    assert len(preview.import_calls) == len(candidate_ids)
    assert store.list_jobs(
        project_id=project.id,
        principal_key=authenticated.principal.key,
    )[0].status == "completed"
