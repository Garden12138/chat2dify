from __future__ import annotations

from contextlib import nullcontext
from datetime import timedelta
from pathlib import Path

import pytest

from app.dify.client import (
    DifyWorkflowNodeExecution,
    DifyWorkflowRunDetail,
    DifyWorkflowRunSummary,
)
from app.studio.build import StudioBuildService
from app.studio.artifacts import canonical_hash
from app.studio.models import Principal, RepairProposal, new_id, utc_now
from app.studio.runs import RepairProposalBlocked, StudioRunService
from app.studio.store import StudioAccessDenied, StudioConflict
from tests.test_studio_releases import _approved_corrected, _stack


class _RunClient:
    def __init__(self, *, version: str = "2026-08-05.000001") -> None:
        self.version = version
        self.raw_secret = "prod-customer-secret"
        self.list_calls = 0
        self.detail_calls = 0
        self.node_calls = 0

    def list_workflow_runs(
        self,
        app_id: str,
        *,
        status: str | None = None,
        triggered_from: str = "app-run",
        limit: int = 100,
    ) -> list[DifyWorkflowRunSummary]:
        assert app_id == "target-app"
        assert status is None
        assert triggered_from == "app-run"
        assert limit == 100
        self.list_calls += 1
        return [
            DifyWorkflowRunSummary(
                id="production-run-1",
                version=self.version,
                status="failed",
                elapsed_time=1.25,
                total_tokens=240,
                total_steps=2,
                created_at=1785889000,
                finished_at=1785889002,
                exceptions_count=1,
            )
        ]

    def get_workflow_run(
        self,
        app_id: str,
        run_id: str,
    ) -> DifyWorkflowRunDetail:
        assert app_id == "target-app"
        assert run_id == "production-run-1"
        self.detail_calls += 1
        return DifyWorkflowRunDetail(
            id=run_id,
            version=self.version,
            status="failed",
            elapsed_time=1.25,
            total_tokens=240,
            total_steps=2,
            created_at=1785889000,
            finished_at=1785889002,
            exceptions_count=1,
            inputs={
                "query": self.raw_secret,
                "api_key": "sk-production-should-never-persist",
            },
            outputs={"answer": None},
            error=(
                "Variable reference not found for prod-customer-secret; "
                "Authorization: Bearer abcdefghijklmnop"
            ),
        )

    def list_workflow_node_executions(
        self,
        app_id: str,
        run_id: str,
    ) -> list[DifyWorkflowNodeExecution]:
        assert app_id == "target-app"
        assert run_id == "production-run-1"
        self.node_calls += 1
        return [
            DifyWorkflowNodeExecution(
                id="node-execution-start",
                predecessor_node_id=None,
                node_id="start",
                node_type="start",
                title="Input",
                status="succeeded",
                error=None,
                elapsed_time=0.01,
                inputs={"query": self.raw_secret},
                outputs={"query": self.raw_secret},
            ),
            DifyWorkflowNodeExecution(
                id="node-execution-llm",
                predecessor_node_id="start",
                node_id="llm",
                node_type="llm",
                title="Support reply",
                status="failed",
                error=f"Unknown variable {self.raw_secret}",
                elapsed_time=1.2,
                inputs={"prompt": self.raw_secret},
                outputs=None,
            ),
        ]


def _released_stack(tmp_path: Path):
    stack = _stack(tmp_path)
    approved = _approved_corrected(stack)
    releases = stack["releases"]
    owner = stack["owner"]
    project = stack["project"]
    logical = releases.create_logical_app(
        owner,
        project_id=project.id,
        name="Production after-sales",
        app_mode="workflow",
    )
    environment = releases.create_environment(
        owner,
        project_id=project.id,
        logical_app_id=logical.id,
        name="Production",
        classification="production",
        target_app_ref="target-app",
    )
    apply_authorization = releases.authorize(
        owner,
        project_id=project.id,
        change_request_id=approved.change_request.id,
        environment_id=environment.id,
        action="apply_draft",
        confirmation="APPLY_DRAFT",
    )
    releases.execute(
        owner,
        project_id=project.id,
        authorization_id=apply_authorization.id,
        idempotency_key="run-center-apply-001",
    )
    publish_authorization = releases.authorize(
        owner,
        project_id=project.id,
        change_request_id=approved.change_request.id,
        environment_id=environment.id,
        action="publish",
        confirmation="PUBLISH",
    )
    published = releases.execute(
        owner,
        project_id=project.id,
        authorization_id=publish_authorization.id,
        idempotency_key="run-center-publish-001",
    )
    assert published.details["published_workflow"]["version"] == (
        "2026-08-05.000001"
    )
    return stack, approved, logical, environment, published


def _run_service(stack, client: _RunClient) -> StudioRunService:
    return StudioRunService(
        store=stack["studio"],
        build_service=StudioBuildService(
            store=stack["studio"],
            agent_store=stack["agent"],
            agent_service=object(),  # type: ignore[arg-type]
        ),
        agent_store=stack["agent"],
        client_factory=lambda: nullcontext(client),
    )


def test_run_correlation_redaction_incident_and_repair_handoff(tmp_path: Path):
    stack, approved, logical, environment, published = _released_stack(tmp_path)
    client = _RunClient()
    service = _run_service(stack, client)
    owner = stack["owner"]
    project = stack["project"]

    refreshed = service.refresh(owner, project_id=project.id)
    assert refreshed.environments_scanned == 1
    assert refreshed.executions_observed == 1
    assert refreshed.incidents_opened == 1
    assert refreshed.uncorrelated == 0
    assert refreshed.errors == []

    center = service.center(owner, project_id=project.id)
    assert center.state == "ready"
    assert len(center.executions) == 1
    execution = center.executions[0]
    assert execution.logical_app_id == logical.id
    assert execution.environment_id == environment.id
    assert execution.artifact_id == approved.artifact.id
    assert execution.release_record_id == published.id
    assert execution.correlation_state == "exact"
    assert execution.status == "failed"
    assert execution.stable_error_code == "EXECUTION_VARIABLE_REFERENCE_INVALID"
    assert execution.failed_node_id == "llm"
    assert execution.latency_ms == 1250
    assert execution.total_tokens == 240
    assert execution.estimated_cost_microusd == 1200
    assert execution.input_shape == {
        "query": "text",
        "[sensitive_field]": "text",
    }
    assert client.raw_secret not in (execution.safe_message or "")
    assert "Bearer abcdefghijklmnop" not in (execution.safe_message or "")
    serialized = execution.model_dump_json()
    assert client.raw_secret not in serialized
    assert "sk-production-should-never-persist" not in serialized
    assert len(center.error_clusters) == 1
    assert center.regressions[0]["artifact_id"] == approved.artifact.id
    assert center.slow_paths[0].average_latency_ms == 1250

    incident = center.incidents[0]
    detail = service.incident(
        owner,
        project_id=project.id,
        incident_id=incident.id,
    )
    assert detail.execution.id == execution.id
    assert detail.artifact_summary["content_hash"] == approved.artifact.content_hash
    assert detail.release_summary["release_record_id"] == published.id
    assert detail.scenario_coverage["pass_rate"] == 1
    assert detail.known_error["code"] == "EXECUTION_VARIABLE_REFERENCE_INVALID"
    assert detail.affected_path[-1]["node_id"] == "llm"
    assert detail.can_create_repair is True

    publish_calls_before = stack["client"].publish_calls
    sync_calls_before = stack["client"].sync_calls
    repair = service.create_repair(
        owner,
        project_id=project.id,
        incident_id=incident.id,
    )
    assert repair.status == "draft_build"
    assert repair.source_artifact_id == approved.artifact.id
    assert repair.source_release_record_id == published.id
    assert repair.evidence["external_write"] is False
    assert repair.evidence["required_flow"] == [
        "build",
        "scenario",
        "review",
        "apply_draft",
        "explicit_publish",
    ]
    repair_build = stack["studio"].get_build(
        repair.build_id,
        project_id=project.id,
        principal_key=owner.principal.key,
    )
    assert repair_build.operation == "modify"
    assert repair_build.app_id == "target-app"
    assert stack["client"].publish_calls == publish_calls_before
    assert stack["client"].sync_calls == sync_calls_before
    assert service.create_repair(
        owner,
        project_id=project.id,
        incident_id=incident.id,
    ).id == repair.id


def test_uncorrelated_execution_cannot_create_repair_and_cross_project_denied(
    tmp_path: Path,
):
    stack, _, _, _, _ = _released_stack(tmp_path)
    client = _RunClient(version="untracked-version")
    service = _run_service(stack, client)
    owner = stack["owner"]
    project = stack["project"]
    refreshed = service.refresh(owner, project_id=project.id)
    assert refreshed.uncorrelated == 1
    center = service.center(owner, project_id=project.id)
    execution = center.executions[0]
    assert execution.correlation_state == "uncorrelated"
    detail = service.incident(
        owner,
        project_id=project.id,
        incident_id=center.incidents[0].id,
    )
    assert detail.artifact_summary is None
    assert detail.can_create_repair is False
    with pytest.raises(RepairProposalBlocked):
        service.create_repair(
            owner,
            project_id=project.id,
            incident_id=detail.incident.id,
        )

    other = Principal(
        issuer="chat2dify-studio",
        subject="mallory",
        display_name="Mallory",
        email="mallory@example.com",
        dify_tenant_id="tenant-1",
    )
    other_project, _ = stack["studio"].ensure_personal_project(other)
    with pytest.raises(StudioAccessDenied):
        service.center(owner, project_id=other_project.id)


def test_repair_proposal_links_to_review_atomically_without_external_write(
    tmp_path: Path,
):
    stack, approved, _, _, published = _released_stack(tmp_path)
    service = _run_service(stack, _RunClient())
    owner = stack["owner"]
    project = stack["project"]
    service.refresh(owner, project_id=project.id)
    center = service.center(owner, project_id=project.id)
    execution = center.executions[0]
    incident = center.incidents[0]
    now = utc_now()
    evidence = {
        "execution_id": execution.id,
        "stable_error_code": execution.stable_error_code,
        "required_flow": [
            "build",
            "scenario",
            "review",
            "apply_draft",
            "explicit_publish",
        ],
        "external_write": False,
    }
    proposal = RepairProposal(
        id=new_id(),
        project_id=project.id,
        incident_id=incident.id,
        execution_id=execution.id,
        source_artifact_id=approved.artifact.id,
        source_release_record_id=published.id,
        build_id=stack["build"].id,
        title="Repair review handoff",
        business_summary="A repaired Candidate must re-enter normal review.",
        evidence=evidence,
        evidence_hash=canonical_hash(evidence),
        status="scenario_ready",
        created_by=owner.principal.key,
        version=1,
        created_at=now,
        updated_at=now,
    )
    stored, created = stack["studio"].create_repair_proposal(
        item=proposal,
        principal_key=owner.principal.key,
    )
    assert created is True
    sync_before = stack["client"].sync_calls
    publish_before = stack["client"].publish_calls

    detail = stack["reviews"].create(
        owner,
        project_id=project.id,
        build_id=stack["build"].id,
        candidate_id=stack["candidates"][1].id,
        scenario_run_id=stack["scenario_run"].id,
        title="Repair review",
        release_note="Re-test and review the repaired Candidate.",
        assignee_key=stack["reviewer"].principal.key,
        require_separation=True,
        expires_in_seconds=86_400,
        repair_proposal_id=stored.id,
        repair_proposal_version=stored.version,
    )
    linked = stack["studio"].get_repair_proposal(
        stored.id,
        project_id=project.id,
        principal_key=owner.principal.key,
    )
    assert linked.change_request_id == detail.change_request.id
    assert linked.status == "in_review"
    assert linked.version == 2
    assert stack["client"].sync_calls == sync_before
    assert stack["client"].publish_calls == publish_before

    before = len(
        stack["studio"].list_change_requests(
            project_id=project.id,
            principal_key=owner.principal.key,
        )
    )
    with pytest.raises(StudioConflict):
        stack["reviews"].create(
            owner,
            project_id=project.id,
            build_id=stack["build"].id,
            candidate_id=stack["candidates"][1].id,
            scenario_run_id=stack["scenario_run"].id,
            title="Stale duplicate repair review",
            release_note="Must not create a second review.",
            assignee_key=stack["reviewer"].principal.key,
            require_separation=True,
            expires_in_seconds=86_400,
            repair_proposal_id=stored.id,
            repair_proposal_version=1,
        )
    after = len(
        stack["studio"].list_change_requests(
            project_id=project.id,
            principal_key=owner.principal.key,
        )
    )
    assert after == before
