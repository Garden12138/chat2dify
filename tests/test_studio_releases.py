from __future__ import annotations

from contextlib import nullcontext
from datetime import timedelta
import json
from pathlib import Path
import subprocess

import pytest

from app.agent.catalog import NodeCapabilityCatalog
from app.agent.commit import SafeWorkflowDraftWriter
from app.agent.state import (
    AgentRun,
    AgentSession,
    AgentWorkflowSnapshot,
    GoalPlan,
    GoalStep,
    utc_now,
)
from app.agent.store import AgentStore
from app.agent.validation import AgentValidationReport
from app.agent.workspace import VersionedWorkflowWorkspace
from app.compiler.dify import DifyDslCompiler
from app.dify.client import (
    DifyConflictError,
    DifyDraftSyncResult,
    DifyDraftWorkflow,
    DifyPublishResult,
    DifyPublishedWorkflow,
)
from app.dify.graph import compile_plan_to_dify_graph, decompile_dify_graph
from app.models import WorkflowPlan
from app.studio.artifacts import (
    ArtifactMappingMismatch,
    ArtifactSecretFound,
    artifact_git_files,
    assert_secret_free,
    build_workflow_artifact,
    canonical_hash,
    logicalize_plan,
    materialize_artifact_plan,
)
from app.studio.build import StudioBuildService
from app.studio.identity import AuthenticatedStudioRequest
from app.studio.jobs import StudioDurableWorker, release_execute_handler
from app.studio.models import (
    CandidateScenarioReport,
    DifyAppSummary,
    ManualScenarioSource,
    Principal,
    PreviewResourceMapping,
    ReleaseResourceMapping,
    ReleaseRecord,
    ScenarioCase,
    ScenarioCaseEvidence,
    ScenarioEvidenceBinding,
    ScenarioExpectedOutput,
    ScenarioInvariant,
    ScenarioRun,
    ScenarioRunPolicy,
    ScenarioSuite,
    StudioSession,
    VerifiedHostContext,
    new_id,
)
from app.studio.releases import (
    ReleaseAuthorizationInvalid,
    ReleaseBlocked,
    StudioReleaseService,
)
from app.studio.reviews import (
    GitArtifactConflict,
    ReviewSelfApprovalDenied,
    ReviewStale,
    StudioReviewService,
)
from app.studio.scenarios import StudioScenarioService
from app.studio.store import StudioAccessDenied, StudioStore
from tests.test_agent_phase1a import (
    NoopDecisionProvider,
    _stack as _agent_tool_stack,
)


class _PassingValidation:
    def validate(self, _plan):
        return AgentValidationReport(
            ok=True,
            issues=[],
            dsl_version="0.6.0",
            roundtrip_ok=True,
            graph_compiled=True,
        )


class _UnusedPreview:
    available = False
    target_key = "unused"
    target_name = "Unused"
    default_ttl_seconds = 600


class _ReleaseClient:
    def __init__(self, plan: WorkflowPlan, compiler: DifyDslCompiler) -> None:
        self.plan = plan
        self.compiler = compiler
        self.hash = "target-hash-1"
        self.graph = compile_plan_to_dify_graph(plan, compiler=compiler)
        self.sync_calls = 0
        self.publish_calls = 0
        self.publish_ambiguous = False
        self.sync_conflicted = False
        self.mutation_supported = True

    def get_draft_workflow(self, app_id: str) -> DifyDraftWorkflow:
        assert app_id == "target-app"
        return DifyDraftWorkflow(
            id="draft-1",
            graph=self.graph,
            features={"opening_statement": "preserve me"},
            hash=self.hash,
            version="draft-v1",
            environment_variables=[],
            conversation_variables=[],
            raw={},
        )

    def sync_draft_workflow(
        self,
        app_id: str,
        *,
        graph,
        features,
        hash,
        environment_variables=None,
        conversation_variables=None,
    ) -> DifyDraftSyncResult:
        assert app_id == "target-app"
        assert hash == self.hash
        assert features == {"opening_statement": "preserve me"}
        self.sync_calls += 1
        if self.sync_conflicted:
            raise DifyConflictError("Dify rejected the stale Draft Hash.")
        self.graph = graph
        self.plan = decompile_dify_graph(
            graph,
            name="Staging",
            app_mode="workflow",
            conversation_variables=conversation_variables or [],
        )
        self.hash = f"target-hash-{self.sync_calls + 1}"
        return DifyDraftSyncResult(
            result="success",
            hash=self.hash,
            updated_at="2026-08-05T00:00:00Z",
            workflow_url="http://dify.local/app/target-app/workflow",
        )

    def publish_workflow(self, app_id, *, marked_name=None, marked_comment=None):
        assert app_id == "target-app"
        assert marked_name and marked_comment
        self.publish_calls += 1
        if self.publish_ambiguous:
            raise TimeoutError("publish response lost")
        return DifyPublishResult(result="success", created_at="2026-08-05T00:00:00Z")

    def get_published_workflow(self, app_id):
        assert app_id == "target-app"
        return DifyPublishedWorkflow(
            id=f"published-{self.publish_calls}",
            hash=self.hash,
            version=f"2026-08-05.00000{self.publish_calls}",
            created_at=1785888000,
        )


class _Snapshot:
    def __init__(self, client: _ReleaseClient) -> None:
        self.client = client

    def capture(self, session: AgentSession) -> AgentWorkflowSnapshot:
        assert session.app_id == "target-app"
        return AgentWorkflowSnapshot(
            operation="modify",
            app_id="target-app",
            app_name="Staging",
            app_mode="workflow",
            base_hash=self.client.hash,
            base_plan=self.client.plan.model_dump(mode="json"),
            base_graph=self.client.graph,
            features={"opening_statement": "preserve me"},
            dify_version={"app_dsl_version": "0.6.0"},
            capabilities=[],
            compatibility={
                "mutation_supported": self.client.mutation_supported,
                "rule_id": "dify-1.14-dsl-0.6",
                "reason": (
                    None
                    if self.client.mutation_supported
                    else "Unsupported live Dify compatibility fixture."
                ),
            },
        )


def _plan(*, corrected: bool = False, model: bool = False) -> WorkflowPlan:
    middle = []
    if corrected:
        middle.append(
            {
                "id": "template",
                "type": "template-transform",
                "title": "Business fallback",
                "params": {"template": "已受理 {{ query }}"},
            }
        )
    if model:
        middle.append(
            {
                "id": "llm",
                "type": "llm",
                "title": "Answer",
                "params": {
                    "model_provider": "langgenius/openai/openai",
                    "model_name": "gpt-test",
                    "prompt": "Answer safely.",
                },
            }
        )
    node_ids = ["start", *[item["id"] for item in middle], "end"]
    return WorkflowPlan.model_validate(
        {
            "name": "After-sales",
            "description": "Governed release fixture.",
            "app_mode": "workflow",
            "nodes": [
                {
                    "id": "start",
                    "type": "start",
                    "title": "Input",
                    "params": {
                        "variables": [
                            {
                                "name": "query",
                                "type": "paragraph",
                                "required": True,
                            }
                        ]
                    },
                },
                *middle,
                {
                    "id": "end",
                    "type": "end",
                    "title": "Output",
                    "params": {
                        "outputs": [
                            {"variable": "answer", "value_selector": ["start", "query"]}
                        ]
                    },
                },
            ],
            "edges": [
                {"source": source, "target": target}
                for source, target in zip(node_ids, node_ids[1:])
            ],
        }
    )


def _authenticated(
    store: StudioStore,
    project,
    principal: Principal,
    *,
    target_visible: bool = True,
    apps_available: bool = True,
) -> AuthenticatedStudioRequest:
    _, membership = store.get_project_for_principal(project.id, principal.key)
    now = utc_now()
    return AuthenticatedStudioRequest(
        claims={},
        session=StudioSession(
            id=f"session-{principal.subject}",
            jti_hash=(principal.subject[0] * 32),
            principal_key=principal.key,
            project_id=project.id,
            dify_account_id=principal.subject,
            dify_tenant_id=principal.dify_tenant_id,
            origin="http://dify.local",
            nonce_hash="n" * 32,
            expires_at=now + timedelta(hours=1),
            created_at=now,
        ),
        principal=principal,
        project=project,
        membership=membership,
        host=VerifiedHostContext(
            principal=principal,
            apps=(
                [
                    DifyAppSummary(
                        id="target-app",
                        name="Staging",
                        mode="workflow",
                    )
                ]
                if target_visible
                else []
            ),
            apps_available=apps_available,
            apps_error_code=None if apps_available else "STUDIO_DIFY_APPS_UNAVAILABLE",
        ),
    )


def _stack(tmp_path: Path):
    studio = StudioStore(f"sqlite:///{tmp_path / 'studio.sqlite3'}")
    agent = AgentStore(tmp_path / "agent.sqlite3")
    owner = Principal(
        issuer="chat2dify-studio",
        subject="alice",
        display_name="Alice",
        email="alice@example.com",
        dify_tenant_id="tenant-1",
    )
    reviewer = Principal(
        issuer="chat2dify-studio",
        subject="bob",
        display_name="Bob",
        email="bob@example.com",
        dify_tenant_id="tenant-1",
    )
    builder = Principal(
        issuer="chat2dify-studio",
        subject="carol",
        display_name="Carol",
        email="carol@example.com",
        dify_tenant_id="tenant-1",
    )
    project, _ = studio.ensure_personal_project(owner)
    studio.add_membership(
        project_id=project.id,
        actor_key=owner.key,
        principal_key=reviewer.key,
        role="reviewer",
    )
    studio.add_membership(
        project_id=project.id,
        actor_key=owner.key,
        principal_key=builder.key,
        role="builder",
    )
    auth_owner = _authenticated(studio, project, owner)
    auth_reviewer = _authenticated(studio, project, reviewer)
    auth_builder = _authenticated(studio, project, builder)
    build = studio.create_build(
        project_id=project.id,
        principal_key=builder.key,
        operation="modify",
        entry_source="home",
        app_id="source-app",
        app_mode="workflow",
        app_name="After-sales",
    )
    workspace = VersionedWorkflowWorkspace(
        store=agent,
        validation=_PassingValidation(),  # type: ignore[arg-type]
        catalog=NodeCapabilityCatalog(),
    )
    candidates = []
    bindings = []
    reports = []
    for ordinal, plan in enumerate((_plan(), _plan(corrected=True)), start=1):
        session = agent.create_session(
            AgentSession(
                operation="modify",
                app_id="source-app",
                app_mode="workflow",
                app_name="After-sales",
            )
        )
        run = agent.create_run(AgentRun(session_id=session.id, goal=f"Candidate {ordinal}"))
        snapshot = AgentWorkflowSnapshot(
            operation="modify",
            app_id="source-app",
            app_name="After-sales",
            app_mode="workflow",
            base_hash="source-hash-1",
            base_plan=plan.model_dump(mode="json"),
            base_graph={},
            capabilities=[],
            compatibility={"mutation_supported": True, "rule_id": "dify-1.14-dsl-0.6"},
        )
        run, head = workspace.initialize(
            run,
            snapshot,
            GoalPlan(
                goal=run.goal,
                success_criteria=["validated"],
                steps=[GoalStep(id="build", description="Build it")],
            ),
        )
        agent.update_run(
            run.model_copy(
                update={
                    "review": {
                        "ready": True,
                        "business_diff": [f"Candidate {ordinal}"],
                        "validation": {"ok": True, "issues": []},
                    }
                }
            )
        )
        candidate = studio.add_candidate(
            build_id=build.id,
            project_id=project.id,
            principal_key=builder.key,
            run_id=run.id,
            label=f"Candidate {ordinal}",
            intent="Governed release",
        )
        binding_base = {
            "candidate_id": candidate.id,
            "candidate_workspace_version_id": head.id,
            "candidate_hash": canonical_hash(head.snapshot),
            "mapping_hash": canonical_hash([]),
            "suite_id": "suite-1",
            "suite_version": "1.0.0",
            "suite_hash": "a" * 64,
            "policy_hash": canonical_hash(ScenarioRunPolicy().model_dump(mode="json")),
            "environment_id": "preview-1",
            "expires_at": (utc_now() + timedelta(days=7)).isoformat(),
        }
        binding = ScenarioEvidenceBinding(
            **binding_base,
            binding_hash=canonical_hash(binding_base),
        )
        report = CandidateScenarioReport(
            candidate_id=candidate.id,
            candidate_label=candidate.label,
            binding=binding,
            cases=[
                ScenarioCaseEvidence(
                    scenario_id="case-1",
                    scenario_name="After-sales",
                    status="passed",
                    passed=True,
                    quality_score=100,
                )
            ],
            pass_rate=1,
            quality_score=100,
            cleanup_verified=True,
        )
        candidates.append(candidate)
        bindings.append(binding)
        reports.append(report)
    suite = ScenarioSuite(
        id="suite-1",
        project_id=project.id,
        build_id=build.id,
        name="Release regression",
        description="Fixed release evidence.",
        owner_key=builder.key,
        retention_days=30,
        semantic_version="1.0.0",
        input_schema_hash="b" * 64,
        cases=[
            ScenarioCase(
                id="case-1",
                name="After-sales",
                source=ManualScenarioSource(),
                inputs={"query": "退货"},
                expected_output=ScenarioExpectedOutput(kind="contains_text", value="已受理"),
                expected_behavior="Return a governed result.",
                invariants=[
                    ScenarioInvariant(
                        kind="status_is",
                        target="succeeded",
                        description="Completes successfully.",
                    )
                ],
            )
        ],
        content_hash="a" * 64,
        created_at=utc_now(),
    )
    studio.create_scenario_suite(suite, principal_key=builder.key)
    preview_env = studio.ensure_preview_environment(
        project_id=project.id,
        principal_key=owner.key,
        target_key="preview-test",
        name="Preview",
        enabled=True,
        default_ttl_seconds=600,
    )
    scenario_run = ScenarioRun(
        id="scenario-run-1",
        project_id=project.id,
        build_id=build.id,
        suite_id=suite.id,
        environment_id=preview_env.id,
        candidate_ids=[item.id for item in candidates],
        mappings=[],
        policy=ScenarioRunPolicy(),
        authorized_by=builder.key,
        status="completed",
        reports=reports,
        cleanup_verified=True,
        version=1,
        created_at=utc_now(),
        updated_at=utc_now(),
    )
    studio.create_scenario_run(scenario_run, principal_key=builder.key)
    build_service = StudioBuildService(
        store=studio,
        agent_store=agent,
        agent_service=object(),  # type: ignore[arg-type]
    )
    compiler = DifyDslCompiler(
        dsl_version="0.6.0",
        default_model_provider="openai",
        default_model_name="gpt-test",
    )
    scenarios = StudioScenarioService(
        store=studio,
        build_service=build_service,
        agent_store=agent,
        compiler=compiler,
        catalog=NodeCapabilityCatalog(),
        preview=_UnusedPreview(),  # type: ignore[arg-type]
        background_workers=0,
    )
    reviews = StudioReviewService(
        store=studio,
        build_service=build_service,
        scenario_service=scenarios,
        agent_store=agent,
    )
    client = _ReleaseClient(_plan(), compiler)
    releases = StudioReleaseService(
        store=studio,
        reviews=reviews,
        snapshot=_Snapshot(client),  # type: ignore[arg-type]
        safe_writer=SafeWorkflowDraftWriter(
            validation=_PassingValidation(),  # type: ignore[arg-type]
            compiler=compiler,
            client_factory=lambda: nullcontext(client),
        ),
        client_factory=lambda: nullcontext(client),
    )
    return {
        "studio": studio,
        "agent": agent,
        "project": project,
        "owner": auth_owner,
        "reviewer": auth_reviewer,
        "builder": auth_builder,
        "build": build,
        "candidates": candidates,
        "scenario_run": scenario_run,
        "reviews": reviews,
        "releases": releases,
        "client": client,
    }


def _approved_corrected(stack):
    reviews = stack["reviews"]
    owner = stack["owner"]
    reviewer = stack["reviewer"]
    project = stack["project"]
    build = stack["build"]
    first, corrected = stack["candidates"]
    initial = reviews.create(
        owner,
        project_id=project.id,
        build_id=build.id,
        candidate_id=first.id,
        scenario_run_id="scenario-run-1",
        title="After-sales release",
        release_note="Ship the tested fallback.",
        assignee_key=reviewer.principal.key,
        require_separation=True,
        expires_in_seconds=86_400,
    )
    changed = reviews.decide(
        reviewer,
        project_id=project.id,
        change_request_id=initial.change_request.id,
        decision="request_changes",
        body="Use the explicit business fallback.",
        expected_version=initial.change_request.version,
        expected_binding_hash=initial.change_request.binding_hash,
    )
    replacement = reviews.supersede(
        owner,
        project_id=project.id,
        change_request_id=changed.change_request.id,
        expected_version=changed.change_request.version,
        build_id=build.id,
        candidate_id=corrected.id,
        scenario_run_id="scenario-run-1",
        title="Corrected after-sales release",
        release_note="Ship the explicit tested fallback.",
        expires_in_seconds=86_400,
    )
    with pytest.raises(ReviewSelfApprovalDenied):
        reviews.decide(
            owner,
            project_id=project.id,
            change_request_id=replacement.change_request.id,
            decision="approve",
            body="Self approval should fail.",
            expected_version=replacement.change_request.version,
            expected_binding_hash=replacement.change_request.binding_hash,
        )
    with pytest.raises(ReviewStale):
        reviews.decide(
            reviewer,
            project_id=project.id,
            change_request_id=replacement.change_request.id,
            decision="approve",
            body="A stale browser binding must fail.",
            expected_version=replacement.change_request.version,
            expected_binding_hash="f" * 64,
        )
    return reviews.decide(
        reviewer,
        project_id=project.id,
        change_request_id=replacement.change_request.id,
        decision="approve",
        body="Corrected Candidate and evidence approved.",
        expected_version=replacement.change_request.version,
        expected_binding_hash=replacement.change_request.binding_hash,
    )


def test_review_request_change_approval_apply_publish_and_duplicate(tmp_path: Path):
    stack = _stack(tmp_path)
    approved = _approved_corrected(stack)
    releases = stack["releases"]
    owner = stack["owner"]
    reviewer = stack["reviewer"]
    project = stack["project"]
    commented = stack["reviews"].comment(
        reviewer,
        project_id=project.id,
        change_request_id=approved.change_request.id,
        body="Verified corrected business fallback and Scenario evidence.",
    )
    assert commented.events[-1].kind == "commented"
    reopened = StudioStore(f"sqlite:///{tmp_path / 'studio.sqlite3'}")
    durable_events = reopened.list_review_events(
        project_id=project.id,
        principal_key=reviewer.principal.key,
        change_request_id=approved.change_request.id,
    )
    assert durable_events[-1].body.startswith("Verified corrected")
    logical = releases.create_logical_app(
        owner,
        project_id=project.id,
        name="After-sales",
        app_mode="workflow",
    )
    environment = releases.create_environment(
        owner,
        project_id=project.id,
        logical_app_id=logical.id,
        name="Staging",
        classification="staging",
        target_app_ref="target-app",
    )
    preview = releases.preview(
        owner,
        project_id=project.id,
        change_request_id=approved.change_request.id,
        environment_id=environment.id,
    )
    assert preview.blockers == []
    apply_authorization = releases.authorize(
        owner,
        project_id=project.id,
        change_request_id=approved.change_request.id,
        environment_id=environment.id,
        action="apply_draft",
        confirmation="APPLY_DRAFT",
    )
    applied = releases.execute(
        owner,
        project_id=project.id,
        authorization_id=apply_authorization.id,
        idempotency_key="apply-corrected-001",
    )
    assert applied.outcome == "succeeded"
    assert applied.details["artifact_hash"] == approved.artifact.content_hash
    assert applied.details["scenario"]["cleanup_verified"] is True
    assert applied.details["environment_name"] == "Staging"
    assert stack["client"].sync_calls == 1
    duplicate = releases.execute(
        owner,
        project_id=project.id,
        authorization_id=apply_authorization.id,
        idempotency_key="apply-corrected-001",
    )
    assert duplicate.id == applied.id
    assert stack["client"].sync_calls == 1
    with pytest.raises(ReleaseAuthorizationInvalid):
        releases.authorize(
            owner,
            project_id=project.id,
            change_request_id=approved.change_request.id,
            environment_id=environment.id,
            action="publish",
            confirmation="APPLY_DRAFT",  # type: ignore[arg-type]
        )
    publish_authorization = releases.authorize(
        owner,
        project_id=project.id,
        change_request_id=approved.change_request.id,
        environment_id=environment.id,
        action="publish",
        confirmation="PUBLISH",
    )
    assert publish_authorization.id != apply_authorization.id
    published = releases.execute(
        owner,
        project_id=project.id,
        authorization_id=publish_authorization.id,
        idempotency_key="publish-corrected-001",
    )
    assert published.outcome == "succeeded"
    assert stack["client"].publish_calls == 1
    assert [item.action for item in releases.center(owner, project_id=project.id).releases][:2] == [
        "publish",
        "apply_draft",
    ]


def test_explicit_release_is_delivered_once_by_durable_worker(tmp_path: Path):
    stack = _stack(tmp_path)
    approved = _approved_corrected(stack)
    base = stack["releases"]
    releases = StudioReleaseService(
        store=stack["studio"],
        reviews=stack["reviews"],
        snapshot=base.snapshot,
        safe_writer=base.safe_writer,
        client_factory=base.client_factory,
        durable_jobs=True,
    )
    owner = stack["owner"]
    project = stack["project"]
    logical = releases.create_logical_app(
        owner,
        project_id=project.id,
        name="Durable after-sales",
        app_mode="workflow",
    )
    environment = releases.create_environment(
        owner,
        project_id=project.id,
        logical_app_id=logical.id,
        name="Staging",
        classification="staging",
        target_app_ref="target-app",
    )
    authorization = releases.authorize(
        owner,
        project_id=project.id,
        change_request_id=approved.change_request.id,
        environment_id=environment.id,
        action="apply_draft",
        confirmation="APPLY_DRAFT",
    )
    intent = releases.execute(
        owner,
        project_id=project.id,
        authorization_id=authorization.id,
        idempotency_key="durable-apply-001",
    )
    assert intent.outcome == "intent_recorded"
    assert stack["client"].sync_calls == 0
    jobs = stack["studio"].list_jobs(
        project_id=project.id,
        principal_key=owner.principal.key,
    )
    assert [item.kind for item in jobs] == ["release.execute"]
    worker = StudioDurableWorker(
        store=stack["studio"],
        worker_id="release-worker",
        job_handlers={
            "release.execute": release_execute_handler(
                store=stack["studio"],
                release_service=releases,
            )
        },
        lease_seconds=5,
        heartbeat_seconds=0.2,
    )
    assert worker.run_once() is True
    completed = stack["studio"].get_release_record(
        intent.id,
        project_id=project.id,
        principal_key=owner.principal.key,
    )
    assert completed.outcome == "succeeded"
    assert stack["client"].sync_calls == 1
    assert worker.run_once() is False
    assert stack["client"].sync_calls == 1


def test_review_expiry_and_release_role_permissions(tmp_path: Path) -> None:
    stack = _stack(tmp_path)
    first = stack["candidates"][0]
    with pytest.raises(ReviewSelfApprovalDenied):
        stack["reviews"].create(
            stack["owner"],
            project_id=stack["project"].id,
            build_id=stack["build"].id,
            candidate_id=first.id,
            scenario_run_id="scenario-run-1",
            title="Invalid separated assignment",
            release_note="The Author cannot be the separated Reviewer.",
            assignee_key=stack["owner"].principal.key,
            require_separation=True,
            expires_in_seconds=86_400,
        )
    separated = stack["reviews"].create(
        stack["owner"],
        project_id=stack["project"].id,
        build_id=stack["build"].id,
        candidate_id=first.id,
        scenario_run_id="scenario-run-1",
        title="Separated review",
        release_note="Keep author and reviewer distinct.",
        assignee_key=stack["reviewer"].principal.key,
        require_separation=True,
        expires_in_seconds=86_400,
    )
    with pytest.raises(ReviewSelfApprovalDenied):
        stack["reviews"].assign(
            stack["owner"],
            project_id=stack["project"].id,
            change_request_id=separated.change_request.id,
            assignee_key=stack["owner"].principal.key,
            expected_version=separated.change_request.version,
        )
    with pytest.raises(StudioAccessDenied, match="Only the Author"):
        stack["reviews"].supersede(
            stack["builder"],
            project_id=stack["project"].id,
            change_request_id=separated.change_request.id,
            expected_version=separated.change_request.version,
            build_id=stack["build"].id,
            candidate_id=first.id,
            scenario_run_id="scenario-run-1",
            title="Unauthorized replacement",
            release_note="A different Builder cannot replace the Author's review.",
            expires_in_seconds=86_400,
        )
    admin = Principal(
        issuer="chat2dify-studio",
        subject="dave",
        display_name="Dave",
        email="dave@example.com",
        dify_tenant_id="tenant-1",
    )
    stack["studio"].add_membership(
        project_id=stack["project"].id,
        actor_key=stack["owner"].principal.key,
        principal_key=admin.key,
        role="admin",
    )
    assigned = stack["reviews"].assign(
        stack["owner"],
        project_id=stack["project"].id,
        change_request_id=separated.change_request.id,
        assignee_key=admin.key,
        expected_version=separated.change_request.version,
    )
    assert assigned.change_request.assignee_key == admin.key
    assert assigned.events[-1].kind == "assigned"
    rejected = stack["reviews"].decide(
        _authenticated(stack["studio"], stack["project"], admin),
        project_id=stack["project"].id,
        change_request_id=assigned.change_request.id,
        decision="reject",
        body="Reject the exact assigned Artifact and evidence.",
        expected_version=assigned.change_request.version,
        expected_binding_hash=assigned.change_request.binding_hash,
    )
    assert rejected.change_request.status == "rejected"
    assert rejected.events[-1].kind == "rejected"
    individual = stack["reviews"].create(
        stack["owner"],
        project_id=stack["project"].id,
        build_id=stack["build"].id,
        candidate_id=first.id,
        scenario_run_id="scenario-run-1",
        title="Individual review",
        release_note="Optional separation keeps a personal project usable.",
        assignee_key=None,
        require_separation=False,
        expires_in_seconds=86_400,
    )
    individual_approved = stack["reviews"].decide(
        stack["owner"],
        project_id=stack["project"].id,
        change_request_id=individual.change_request.id,
        decision="approve",
        body="Approve this individual project Artifact.",
        expected_version=individual.change_request.version,
        expected_binding_hash=individual.change_request.binding_hash,
    )
    assert individual_approved.change_request.status == "approved"
    expired = stack["reviews"].create(
        stack["owner"],
        project_id=stack["project"].id,
        build_id=stack["build"].id,
        candidate_id=first.id,
        scenario_run_id="scenario-run-1",
        title="Expired review",
        release_note="This proposal must not remain actionable.",
        assignee_key=stack["reviewer"].principal.key,
        require_separation=True,
        expires_in_seconds=0,
    )
    assert expired.change_request.status == "expired"
    assert expired.can_decide is False
    assert expired.events[-1].kind == "expired"
    with pytest.raises(StudioAccessDenied):
        stack["releases"].create_logical_app(
            stack["builder"],
            project_id=stack["project"].id,
            name="Builder-owned release target",
            app_mode="workflow",
        )


def test_drift_rollback_git_and_project_boundaries(tmp_path: Path):
    stack = _stack(tmp_path)
    approved = _approved_corrected(stack)
    releases = stack["releases"]
    reviews = stack["reviews"]
    owner = stack["owner"]
    project = stack["project"]
    logical = releases.create_logical_app(
        owner,
        project_id=project.id,
        name="After-sales",
        app_mode="workflow",
    )
    environment = releases.create_environment(
        owner,
        project_id=project.id,
        logical_app_id=logical.id,
        name="Staging",
        classification="staging",
        target_app_ref="target-app",
    )
    stack["client"].hash = "external-drift"
    preview = releases.preview(
        owner,
        project_id=project.id,
        change_request_id=approved.change_request.id,
        environment_id=environment.id,
    )
    assert preview.target_drift is True
    assert "TARGET_DRIFT" in {item["code"] for item in preview.blockers}
    with pytest.raises(ReleaseBlocked):
        releases.authorize(
            owner,
            project_id=project.id,
            change_request_id=approved.change_request.id,
            environment_id=environment.id,
            action="apply_draft",
            confirmation="APPLY_DRAFT",
        )
    rollback = reviews.propose_rollback(
        owner,
        project_id=project.id,
        artifact_id=approved.artifact.id,
        title="Rollback proposal",
        release_note="Return through a new reviewed release.",
        assignee_key=stack["reviewer"].principal.key,
        require_separation=True,
        expires_in_seconds=86_400,
    )
    assert rollback.change_request.status == "in_review"
    assert stack["client"].hash == "external-drift"
    bundle = reviews.git_bundle(
        owner,
        project_id=project.id,
        artifact_id=approved.artifact.id,
    )
    assert bundle.files["artifact.json"].endswith("\n")
    assert "secret" not in bundle.files["artifact.json"].lower()
    pulled = reviews.git_pull(
        owner,
        project_id=project.id,
        base_artifact_id=approved.artifact.id,
        expected_base_hash=approved.artifact.content_hash,
        canonical=bundle.files["artifact.json"].rstrip("\n"),
        content_hash=approved.artifact.content_hash,
        title="Explicit Git pull",
        release_note="Re-enter review.",
        assignee_key=stack["reviewer"].principal.key,
        expires_in_seconds=86_400,
    )
    assert pulled.change_request.status == "in_review"
    with pytest.raises(GitArtifactConflict):
        reviews.git_pull(
            owner,
            project_id=project.id,
            base_artifact_id=approved.artifact.id,
            expected_base_hash="f" * 64,
            canonical=bundle.files["artifact.json"].rstrip("\n"),
            content_hash=approved.artifact.content_hash,
            title="Conflicted pull",
            release_note="Must fail.",
            assignee_key=None,
            expires_in_seconds=86_400,
        )
    outsider = Principal(
        issuer="chat2dify-studio",
        subject="mallory",
        display_name="Mallory",
        dify_tenant_id="tenant-1",
    )
    outsider_project, _ = stack["studio"].ensure_personal_project(outsider)
    outsider_auth = _authenticated(stack["studio"], outsider_project, outsider, target_visible=False)
    with pytest.raises(StudioAccessDenied):
        reviews.detail(
            outsider_auth,
            project_id=project.id,
            change_request_id=approved.change_request.id,
        )


def test_target_authorization_fails_closed_and_conflict_has_terminal_failed_receipt(
    tmp_path: Path,
) -> None:
    stack = _stack(tmp_path)
    approved = _approved_corrected(stack)
    releases = stack["releases"]
    owner = stack["owner"]
    project = stack["project"]
    logical = releases.create_logical_app(
        owner,
        project_id=project.id,
        name="Conflict-safe",
        app_mode="workflow",
    )
    unavailable = _authenticated(
        stack["studio"],
        project,
        owner.principal,
        apps_available=False,
    )
    partial_center = releases.center(unavailable, project_id=project.id)
    assert partial_center.state == "partial_error"
    assert partial_center.change_requests
    assert partial_center.available_apps == []
    assert "target configuration is disabled" in partial_center.message
    with pytest.raises(ReleaseBlocked):
        releases.create_environment(
            unavailable,
            project_id=project.id,
            logical_app_id=logical.id,
            name="Unverified",
            classification="staging",
            target_app_ref="target-app",
        )

    environment = releases.create_environment(
        owner,
        project_id=project.id,
        logical_app_id=logical.id,
        name="Verified",
        classification="staging",
        target_app_ref="target-app",
    )
    with pytest.raises(ReleaseBlocked):
        releases.preview(
            unavailable,
            project_id=project.id,
            change_request_id=approved.change_request.id,
            environment_id=environment.id,
        )
    hidden_target = _authenticated(
        stack["studio"],
        project,
        owner.principal,
        target_visible=False,
    )
    with pytest.raises(StudioAccessDenied):
        releases.preview(
            hidden_target,
            project_id=project.id,
            change_request_id=approved.change_request.id,
            environment_id=environment.id,
        )
    authorization = releases.authorize(
        owner,
        project_id=project.id,
        change_request_id=approved.change_request.id,
        environment_id=environment.id,
        action="apply_draft",
        confirmation="APPLY_DRAFT",
    )
    stack["client"].sync_conflicted = True
    record = releases.execute(
        owner,
        project_id=project.id,
        authorization_id=authorization.id,
        idempotency_key="apply-conflict-001",
    )
    assert record.outcome == "conflicted"
    receipts = stack["studio"].list_receipts(
        project_id=project.id,
        principal_key=owner.principal.key,
        operation_prefix="release.apply_draft",
    )
    assert receipts[0].outcome == "failed"
    duplicate = releases.execute(
        owner,
        project_id=project.id,
        authorization_id=authorization.id,
        idempotency_key="apply-conflict-001",
    )
    assert duplicate.id == record.id
    assert stack["client"].sync_calls == 1

    restart_authorization = releases.authorize(
        owner,
        project_id=project.id,
        change_request_id=approved.change_request.id,
        environment_id=environment.id,
        action="apply_draft",
        confirmation="APPLY_DRAFT",
    )
    interrupted_intent, created = stack["studio"].create_release_intent(
        record=ReleaseRecord(
            id=new_id(),
            project_id=project.id,
            change_request_id=approved.change_request.id,
            artifact_id=approved.artifact.id,
            environment_id=environment.id,
            authorization_id=restart_authorization.id,
            action="apply_draft",
            idempotency_key="apply-interrupted-001",
            outcome="intent_recorded",
            actor_key=owner.principal.key,
            before_hash=restart_authorization.target_hash,
            release_note="Interrupted acceptance.",
            created_at=utc_now(),
        ),
        principal_key=owner.principal.key,
    )
    assert created is True
    assert stack["studio"].interrupt_active_release_records() == 1
    interrupted = next(
        item
        for item in stack["studio"].list_release_records(
            project_id=project.id,
            principal_key=owner.principal.key,
        )
        if item.id == interrupted_intent.id
    )
    assert interrupted.outcome == "ambiguous"
    assert interrupted.receipt_id
    restart_receipt = next(
        item
        for item in stack["studio"].list_receipts(
            project_id=project.id,
            principal_key=owner.principal.key,
            operation_prefix="release.apply_draft",
        )
        if item.id == interrupted.receipt_id
    )
    assert restart_receipt.outcome == "ambiguous"


def test_team_release_target_must_be_linked_to_the_project(tmp_path: Path) -> None:
    stack = _stack(tmp_path)
    store = stack["studio"]
    releases = stack["releases"]
    principal = stack["owner"].principal
    project, _ = store.create_project(
        name="Team release boundary",
        dify_tenant_id=principal.dify_tenant_id,
        owner=principal,
    )
    authenticated = _authenticated(store, project, principal)
    logical = releases.create_logical_app(
        authenticated,
        project_id=project.id,
        name="Team workflow",
        app_mode="workflow",
    )

    center = releases.center(authenticated, project_id=project.id)
    assert center.available_apps == []
    with pytest.raises(StudioAccessDenied, match="not linked to this team project"):
        releases.create_environment(
            authenticated,
            project_id=project.id,
            logical_app_id=logical.id,
            name="Staging",
            classification="staging",
            target_app_ref="target-app",
        )

    store.link_project_app(
        project_id=project.id,
        principal_key=principal.key,
        app_id="target-app",
    )
    assert [
        item.id
        for item in releases.center(
            authenticated,
            project_id=project.id,
        ).available_apps
    ] == ["target-app"]
    environment = releases.create_environment(
        authenticated,
        project_id=project.id,
        logical_app_id=logical.id,
        name="Staging",
        classification="staging",
        target_app_ref="target-app",
    )
    assert environment.target_app_ref == "target-app"


def test_release_authorization_claim_race_fails_before_external_write(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    stack = _stack(tmp_path)
    approved = _approved_corrected(stack)
    releases = stack["releases"]
    store = stack["studio"]
    owner = stack["owner"]
    project = stack["project"]
    logical = releases.create_logical_app(
        owner,
        project_id=project.id,
        name="Claim race",
        app_mode="workflow",
    )
    environment = releases.create_environment(
        owner,
        project_id=project.id,
        logical_app_id=logical.id,
        name="Staging",
        classification="staging",
        target_app_ref="target-app",
    )
    authorization = releases.authorize(
        owner,
        project_id=project.id,
        change_request_id=approved.change_request.id,
        environment_id=environment.id,
        action="apply_draft",
        confirmation="APPLY_DRAFT",
    )
    create_intent = store.create_release_intent

    def create_after_other_process_claimed(*, record, principal_key):
        result = create_intent(record=record, principal_key=principal_key)
        store.consume_release_authorization(
            authorization_id=authorization.id,
            project_id=project.id,
            principal_key=owner.principal.key,
        )
        return result

    monkeypatch.setattr(store, "create_release_intent", create_after_other_process_claimed)
    record = releases.execute(
        owner,
        project_id=project.id,
        authorization_id=authorization.id,
        idempotency_key="apply-claim-race-001",
    )

    assert record.outcome == "failed"
    assert record.details["automatic_retry"] is False
    assert "already claimed" in record.details["message"]
    assert stack["client"].sync_calls == 0
    receipt = next(
        item
        for item in store.list_receipts(
            project_id=project.id,
            principal_key=owner.principal.key,
            operation_prefix="release.apply_draft",
        )
        if item.id == record.receipt_id
    )
    assert receipt.outcome == "failed"


def test_unsupported_dify_mutation_fails_closed_before_authorization(
    tmp_path: Path,
) -> None:
    stack = _stack(tmp_path)
    approved = _approved_corrected(stack)
    releases = stack["releases"]
    owner = stack["owner"]
    project = stack["project"]
    logical = releases.create_logical_app(
        owner,
        project_id=project.id,
        name="Compatibility boundary",
        app_mode="workflow",
    )
    environment = releases.create_environment(
        owner,
        project_id=project.id,
        logical_app_id=logical.id,
        name="Staging",
        classification="staging",
        target_app_ref="target-app",
    )
    stack["client"].mutation_supported = False
    preview = releases.preview(
        owner,
        project_id=project.id,
        change_request_id=approved.change_request.id,
        environment_id=environment.id,
    )
    assert "DIFY_VERSION_MUTATION_UNSUPPORTED" in {
        item["code"] for item in preview.blockers
    }
    with pytest.raises(ReleaseBlocked):
        releases.authorize(
            owner,
            project_id=project.id,
            change_request_id=approved.change_request.id,
            environment_id=environment.id,
            action="apply_draft",
            confirmation="APPLY_DRAFT",
        )
    assert stack["client"].sync_calls == 0


def test_mapping_change_invalidates_exact_authorization_before_write(
    tmp_path: Path,
) -> None:
    stack = _stack(tmp_path)
    approved = _approved_corrected(stack)
    releases = stack["releases"]
    owner = stack["owner"]
    project = stack["project"]
    logical = releases.create_logical_app(
        owner,
        project_id=project.id,
        name="Mapping-bound",
        app_mode="workflow",
    )
    environment = releases.create_environment(
        owner,
        project_id=project.id,
        logical_app_id=logical.id,
        name="Staging",
        classification="staging",
        target_app_ref="target-app",
    )
    authorization = releases.authorize(
        owner,
        project_id=project.id,
        change_request_id=approved.change_request.id,
        environment_id=environment.id,
        action="apply_draft",
        confirmation="APPLY_DRAFT",
    )
    releases.configure_mapping(
        owner,
        project_id=project.id,
        environment_id=environment.id,
        mappings=[
            ReleaseResourceMapping(
                kind="dataset",
                logical_ref="dataset:unrelated-environment-entry",
                target_ref="staging-dataset",
            )
        ],
        expected_version=1,
    )
    with pytest.raises(ReleaseAuthorizationInvalid):
        releases.execute(
            owner,
            project_id=project.id,
            authorization_id=authorization.id,
            idempotency_key="mapping-changed-001",
        )
    assert stack["client"].sync_calls == 0


def test_approval_for_release_a_cannot_release_superseding_candidate_b(
    tmp_path: Path,
) -> None:
    stack = _stack(tmp_path)
    approved_a = _approved_corrected(stack)
    releases = stack["releases"]
    reviews = stack["reviews"]
    owner = stack["owner"]
    reviewer = stack["reviewer"]
    project = stack["project"]
    logical = releases.create_logical_app(
        owner,
        project_id=project.id,
        name="Candidate binding",
        app_mode="workflow",
    )
    environment = releases.create_environment(
        owner,
        project_id=project.id,
        logical_app_id=logical.id,
        name="Staging",
        classification="staging",
        target_app_ref="target-app",
    )
    candidate_b = stack["candidates"][0]
    proposal_b = reviews.create(
        owner,
        project_id=project.id,
        build_id=stack["build"].id,
        candidate_id=candidate_b.id,
        scenario_run_id="scenario-run-1",
        title="Candidate B release",
        release_note="Candidate B has its own exact evidence and approval.",
        assignee_key=reviewer.principal.key,
        require_separation=True,
        expires_in_seconds=86_400,
    )
    approved_b = reviews.decide(
        reviewer,
        project_id=project.id,
        change_request_id=proposal_b.change_request.id,
        decision="approve",
        body="Approve only Candidate B.",
        expected_version=proposal_b.change_request.version,
        expected_binding_hash=proposal_b.change_request.binding_hash,
    )
    authorization_a = releases.authorize(
        owner,
        project_id=project.id,
        change_request_id=approved_a.change_request.id,
        environment_id=environment.id,
        action="apply_draft",
        confirmation="APPLY_DRAFT",
    )
    stale_authorization_b = releases.authorize(
        owner,
        project_id=project.id,
        change_request_id=approved_b.change_request.id,
        environment_id=environment.id,
        action="apply_draft",
        confirmation="APPLY_DRAFT",
    )
    released_a = releases.execute(
        owner,
        project_id=project.id,
        authorization_id=authorization_a.id,
        idempotency_key="authorization-a-release",
    )
    assert released_a.outcome == "succeeded"
    assert released_a.artifact_id == approved_a.artifact.id
    assert released_a.artifact_id != approved_b.artifact.id
    with pytest.raises(ReleaseAuthorizationInvalid):
        releases.execute(
            owner,
            project_id=project.id,
            authorization_id=stale_authorization_b.id,
            idempotency_key="stale-authorization-b",
        )
    assert stack["client"].sync_calls == 1

    authorization_b = releases.authorize(
        owner,
        project_id=project.id,
        change_request_id=approved_b.change_request.id,
        environment_id=environment.id,
        action="apply_draft",
        confirmation="APPLY_DRAFT",
    )
    released_b = releases.execute(
        owner,
        project_id=project.id,
        authorization_id=authorization_b.id,
        idempotency_key="authorization-b-release",
    )
    assert released_b.outcome == "succeeded"
    assert released_b.artifact_id == approved_b.artifact.id
    assert released_b.artifact_id != approved_a.artifact.id


def test_artifact_secret_canonical_mapping_and_ambiguous_publish(tmp_path: Path):
    stack = _stack(tmp_path)
    candidate = stack["candidates"][0]
    run = stack["agent"].get_run(candidate.run_id)
    head = stack["agent"].get_workspace_head(run.id)
    report = stack["scenario_run"].reports[0]
    artifact_a = build_workflow_artifact(
        project_id=stack["project"].id,
        candidate_id=candidate.id,
        workspace_version_id=head.id,
        source_base_hash=run.base_hash,
        plan=WorkflowPlan.model_validate(head.snapshot),
        run=run,
        scenario_run_id="scenario-run-1",
        report=report,
        created_by=stack["owner"].principal.key,
    )
    artifact_b = build_workflow_artifact(
        project_id=stack["project"].id,
        candidate_id=candidate.id,
        workspace_version_id=head.id,
        source_base_hash=run.base_hash,
        plan=WorkflowPlan.model_validate(head.snapshot),
        run=run,
        scenario_run_id="scenario-run-1",
        report=report,
        created_by=stack["builder"].principal.key,
    )
    assert artifact_a.canonical_json == artifact_b.canonical_json
    assert artifact_a.content_hash == artifact_b.content_hash
    assert artifact_git_files(artifact_a) == artifact_git_files(artifact_b)
    with pytest.raises(ArtifactSecretFound):
        assert_secret_free({"api_key": "sk-1234567890abcdefghijkl"})

    model_artifact = build_workflow_artifact(
        project_id=stack["project"].id,
        candidate_id=candidate.id,
        workspace_version_id=head.id,
        source_base_hash=run.base_hash,
        plan=_plan(model=True),
        run=run,
        scenario_run_id="scenario-run-1",
        report=report,
        created_by=stack["owner"].principal.key,
    )
    with pytest.raises(ArtifactMappingMismatch):
        materialize_artifact_plan(model_artifact, [])
    mappings = []
    for requirement in model_artifact.payload.resource_requirements:
        mappings.append(
            ReleaseResourceMapping(
                kind=requirement.kind,
                logical_ref=requirement.logical_ref,
                target_ref=(
                    "available"
                    if requirement.kind == "credential_availability"
                    else "langgenius/openai/openai::gpt-staging"
                ),
            )
        )
    materialized = materialize_artifact_plan(model_artifact, mappings)
    llm = next(item for item in materialized.nodes if item.type == "llm")
    assert llm.params["model_name"] == "gpt-staging"

    resource_plan = WorkflowPlan.model_validate(
        {
            "name": "Mapped resources",
            "description": "Exercise every release mapping domain.",
            "app_mode": "workflow",
            "nodes": [
                {
                    "id": "start",
                    "type": "start",
                    "title": "Input",
                    "params": {
                        "input_variables": [
                            {"name": "query", "type": "text-input", "required": True}
                        ]
                    },
                },
                {
                    "id": "knowledge",
                    "type": "knowledge-retrieval",
                    "title": "Knowledge",
                    "params": {
                        "dataset_ids": ["dataset-production-id"],
                        "query_variable_selector": ["start", "query"],
                    },
                },
                {
                    "id": "tool",
                    "type": "tool",
                    "title": "Search",
                    "params": {
                        "provider_id": "langgenius/search/search",
                        "tool_name": "search",
                        "tool_parameters": {},
                    },
                },
                {
                    "id": "agent",
                    "type": "agent",
                    "title": "Agent",
                    "params": {
                        "agent_strategy_provider_name": "langgenius/agent/react",
                        "agent_strategy_name": "react",
                        "instruction": "Use mapped resources safely.",
                        "query": ["start", "query"],
                        "maximum_iterations": 3,
                        "tools": [],
                    },
                },
                {
                    "id": "end",
                    "type": "end",
                    "title": "Output",
                    "params": {
                        "outputs": [
                            {
                                "variable": "answer",
                                "value_selector": ["start", "query"],
                            }
                        ]
                    },
                },
            ],
            "edges": [
                {"source": source, "target": target}
                for source, target in zip(
                    ["start", "knowledge", "tool", "agent"],
                    ["knowledge", "tool", "agent", "end"],
                )
            ],
        }
    )
    logical_plan, logical_requirements = logicalize_plan(resource_plan)
    assert {item.kind for item in logical_requirements} == {
        "dataset",
        "tool",
        "strategy",
    }
    logical_json = json.dumps(logical_plan, sort_keys=True)
    for environment_id in (
        "dataset-production-id",
        "langgenius/search/search",
        "langgenius/agent/react",
    ):
        assert environment_id not in logical_json

    resource_artifact = build_workflow_artifact(
        project_id=stack["project"].id,
        candidate_id=candidate.id,
        workspace_version_id=head.id,
        source_base_hash=run.base_hash,
        plan=resource_plan,
        run=run,
        scenario_run_id="scenario-run-1",
        report=report,
        created_by=stack["owner"].principal.key,
    )
    assert {item.kind for item in resource_artifact.payload.resource_requirements} == {
        "dataset",
        "tool",
        "strategy",
        "credential_availability",
    }
    resource_mappings = [
        ReleaseResourceMapping(
            kind=requirement.kind,
            logical_ref=requirement.logical_ref,
            target_ref=(
                "available"
                if requirement.kind == "credential_availability"
                else "dataset-staging-id"
                if requirement.kind == "dataset"
                else "provider-staging::name-staging"
            ),
        )
        for requirement in resource_artifact.payload.resource_requirements
    ]
    materialized_resources = materialize_artifact_plan(
        resource_artifact,
        resource_mappings,
    )
    by_id = {item.id: item for item in materialized_resources.nodes}
    assert by_id["knowledge"].params["dataset_ids"] == ["dataset-staging-id"]
    assert by_id["tool"].params["provider_id"] == "provider-staging"
    assert by_id["tool"].params["tool_name"] == "name-staging"
    assert by_id["agent"].params["agent_strategy_provider_name"] == (
        "provider-staging"
    )
    assert by_id["agent"].params["agent_strategy_name"] == "name-staging"

    trigger_plan = WorkflowPlan.model_validate(
        {
            "name": "Mapped trigger",
            "description": "Exercise the trigger release mapping domain.",
            "app_mode": "workflow",
            "nodes": [
                {
                    "id": "trigger",
                    "type": "trigger-plugin",
                    "title": "GitHub Trigger",
                    "params": {"provider_id": "langgenius/github/github"},
                },
                {
                    "id": "end",
                    "type": "end",
                    "title": "Output",
                    "params": {
                        "outputs": [
                            {
                                "variable": "answer",
                                "value_selector": ["trigger", "title"],
                            }
                        ]
                    },
                },
            ],
            "edges": [{"source": "trigger", "target": "end"}],
        }
    )
    trigger_artifact = build_workflow_artifact(
        project_id=stack["project"].id,
        candidate_id=candidate.id,
        workspace_version_id=head.id,
        source_base_hash=run.base_hash,
        plan=trigger_plan,
        run=run,
        scenario_run_id="scenario-run-1",
        report=report,
        created_by=stack["owner"].principal.key,
    )
    assert {item.kind for item in trigger_artifact.payload.resource_requirements} == {
        "trigger",
        "credential_availability",
    }
    materialized_trigger = materialize_artifact_plan(
        trigger_artifact,
        [
            ReleaseResourceMapping(
                kind=requirement.kind,
                logical_ref=requirement.logical_ref,
                target_ref=(
                    "available"
                    if requirement.kind == "credential_availability"
                    else "provider-staging::trigger-staging"
                ),
            )
            for requirement in trigger_artifact.payload.resource_requirements
        ],
    )
    trigger = next(item for item in materialized_trigger.nodes if item.id == "trigger")
    assert trigger.params["provider_id"] == "provider-staging"

    approved = _approved_corrected(stack)
    releases = stack["releases"]
    owner = stack["owner"]
    project = stack["project"]
    logical = releases.create_logical_app(
        owner,
        project_id=project.id,
        name="Ambiguous",
        app_mode="workflow",
    )
    environment = releases.create_environment(
        owner,
        project_id=project.id,
        logical_app_id=logical.id,
        name="Staging",
        classification="staging",
        target_app_ref="target-app",
    )
    apply_auth = releases.authorize(
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
        authorization_id=apply_auth.id,
        idempotency_key="apply-before-ambiguous",
    )
    publish_auth = releases.authorize(
        owner,
        project_id=project.id,
        change_request_id=approved.change_request.id,
        environment_id=environment.id,
        action="publish",
        confirmation="PUBLISH",
    )
    stack["client"].publish_ambiguous = True
    result = releases.execute(
        owner,
        project_id=project.id,
        authorization_id=publish_auth.id,
        idempotency_key="publish-ambiguous-001",
    )
    assert result.outcome == "ambiguous"
    duplicate = releases.execute(
        owner,
        project_id=project.id,
        authorization_id=publish_auth.id,
        idempotency_key="publish-ambiguous-001",
    )
    assert duplicate.id == result.id
    assert stack["client"].publish_calls == 1


def test_git_bundle_is_stable_in_a_real_local_repository(tmp_path: Path) -> None:
    stack = _stack(tmp_path)
    approved = _approved_corrected(stack)
    bundle = stack["reviews"].git_bundle(
        stack["owner"],
        project_id=stack["project"].id,
        artifact_id=approved.artifact.id,
    )
    repository = tmp_path / "artifact-repository"
    repository.mkdir()
    subprocess.run(["git", "init", "-q"], cwd=repository, check=True)
    for name, content in bundle.files.items():
        (repository / name).write_text(content, encoding="utf-8")
    subprocess.run(["git", "add", "."], cwd=repository, check=True)
    subprocess.run(
        [
            "git",
            "-c",
            "user.name=Chat2Dify Acceptance",
            "-c",
            "user.email=acceptance@example.invalid",
            "commit",
            "-qm",
            "Store governed Workflow Artifact",
        ],
        cwd=repository,
        check=True,
    )
    repeated = stack["reviews"].git_bundle(
        stack["owner"],
        project_id=stack["project"].id,
        artifact_id=approved.artifact.id,
    )
    assert repeated.files == bundle.files
    for name, content in repeated.files.items():
        (repository / name).write_text(content, encoding="utf-8")
    subprocess.run(["git", "diff", "--exit-code"], cwd=repository, check=True)


def test_model_visible_builder_tools_cannot_approve_apply_or_publish(
    tmp_path: Path,
) -> None:
    agent = _agent_tool_stack(tmp_path, "workflow", NoopDecisionProvider())
    visible = agent.registry.visible_specs()
    names = {item.name for item in visible}
    assert all(item.side_effect != "dify_write" for item in visible)
    assert not any(
        forbidden in name
        for name in names
        for forbidden in ("approve", "approval", "apply", "commit", "publish", "release")
    )
