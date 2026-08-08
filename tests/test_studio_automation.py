from __future__ import annotations

from datetime import timedelta
import json
from pathlib import Path

import pytest

from app.studio.automation import (
    AutomationConfigurationInvalid,
    StudioRunAutomationService,
)
from app.studio.models import utc_now
from app.studio.jobs import StudioDurableWorker, scheduled_regression_handler
from app.studio.store import StudioAccessDenied
from tests.test_studio_runs import _RunClient, _released_stack, _run_service
from tests.test_studio_scenarios import _FakePreview


def test_alert_threshold_is_redacted_deduplicated_and_adapter_truthful(
    tmp_path: Path,
):
    stack, _, _, environment, _ = _released_stack(tmp_path)
    owner = stack["owner"]
    project = stack["project"]
    _run_service(stack, _RunClient()).refresh(owner, project_id=project.id)
    service = StudioRunAutomationService(
        store=stack["studio"],
        available_adapter_refs={"audit:local"},
    )
    rule = service.configure_alert(
        owner,
        project_id=project.id,
        name="生产变量引用失败",
        environment_id=environment.id,
        stable_error_code="EXECUTION_VARIABLE_REFERENCE_INVALID",
        error_count_threshold=1,
        failure_rate_threshold=0.5,
        window_seconds=2_592_000,
        adapter_ref="audit:local",
        enabled=True,
    )
    assert rule.version == 1
    assert service.evaluate_alerts(owner, project_id=project.id) == 1
    assert service.evaluate_alerts(owner, project_id=project.id) == 0
    messages = stack["studio"].list_outbox(
        project_id=project.id,
        principal_key=owner.principal.key,
    )
    assert len(messages) == 1
    assert messages[0].topic == "notification.run_alert"
    assert messages[0].payload["sanitized"] is True
    assert messages[0].payload["failures"] == 1
    serialized = json.dumps(messages[0].payload)
    assert "prod-customer-secret" not in serialized
    assert "sk-production" not in serialized
    view = service.view(owner, project_id=project.id)
    assert view.adapter_state == "configured"
    assert view.pending_notifications == 1

    with pytest.raises(AutomationConfigurationInvalid):
        service.configure_alert(
            owner,
            project_id=project.id,
            name="Unsafe adapter",
            environment_id=None,
            stable_error_code=None,
            error_count_threshold=1,
            failure_rate_threshold=None,
            window_seconds=3600,
            adapter_ref="https://hooks.example/secret",
            enabled=True,
        )
    with pytest.raises(StudioAccessDenied):
        service.configure_alert(
            stack["reviewer"],
            project_id=project.id,
            name="Reviewer cannot configure",
            environment_id=None,
            stable_error_code=None,
            error_count_threshold=1,
            failure_rate_threshold=None,
            window_seconds=3600,
            adapter_ref="audit:local",
            enabled=True,
        )


def test_published_artifact_schedule_enqueues_one_durable_occurrence(
    tmp_path: Path,
):
    stack, approved, _, _, _ = _released_stack(tmp_path)
    owner = stack["owner"]
    project = stack["project"]
    service = StudioRunAutomationService(store=stack["studio"])
    schedule = service.configure_schedule(
        owner,
        project_id=project.id,
        artifact_id=approved.artifact.id,
        suite_id=stack["scenario_run"].suite_id,
        interval_seconds=900,
        enabled=True,
    )
    due = schedule.model_copy(
        update={
            "next_run_at": utc_now() - timedelta(seconds=1),
            "updated_at": utc_now(),
        }
    )
    schedule = stack["studio"].save_scheduled_regression(
        item=due,
        principal_key=owner.principal.key,
        expected_version=schedule.version,
    )
    assert schedule.version == 2
    assert service.enqueue_due_schedules(owner, project_id=project.id) == 1
    assert service.enqueue_due_schedules(owner, project_id=project.id) == 0
    jobs = stack["studio"].list_jobs(
        project_id=project.id,
        principal_key=owner.principal.key,
    )
    assert len(jobs) == 1
    assert jobs[0].kind == "scenario.scheduled_regression"
    assert jobs[0].payload["production_write"] is False
    assert jobs[0].status == "pending"
    view = service.view(owner, project_id=project.id)
    assert view.schedule_targets[0]["suite_name"] == "Release regression"

    scenarios = stack["reviews"].scenario_service
    preview = _FakePreview()
    scenarios.preview = preview
    worker = StudioDurableWorker(
        store=stack["studio"],
        worker_id="scheduled-regression-worker",
        job_handlers={
            "scenario.scheduled_regression": scheduled_regression_handler(
                store=stack["studio"],
                scenario_service=scenarios,
            )
        },
        lease_seconds=5,
        heartbeat_seconds=0.2,
    )
    assert worker.run_once() is True
    completed = stack["studio"].list_jobs(
        project_id=project.id,
        principal_key=owner.principal.key,
    )[0]
    assert completed.status == "completed"
    assert len(preview.import_calls) == 1
    assert len(preview.execute_calls) == 1
    assert len(preview.delete_calls) == 1
