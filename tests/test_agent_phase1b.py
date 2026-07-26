from __future__ import annotations

from contextlib import nullcontext
from dataclasses import dataclass
from typing import Any
from uuid import UUID

from fastapi import FastAPI
from fastapi.testclient import TestClient
import pytest
import yaml

from app.agent.approval import AgentApprovalService
from app.agent.catalog import NodeCapabilityCatalog
from app.agent.commit import (
    CommitServiceError,
    CreationCommitService,
    ModificationCommitService,
)
from app.agent.context import BuilderContext, BuilderContextBuilder
from app.agent.decision import AgentDecisionProvider
from app.agent.patch import PatchDocument
from app.agent.policy import AgentToolPolicy
from app.agent.registry import ToolRegistry
from app.agent.review import WorkflowReviewService
from app.agent.runtime import AgentRuntime
from app.agent.service import AgentApplicationService, InlineRunDispatcher
from app.agent.snapshot import WorkflowSnapshotService
from app.agent.state import (
    AgentRun,
    FinishDecision,
    GoalPlan,
    RunConstraints,
    RunPhase,
    ToolCallDecision,
)
from app.agent.store import AgentStore
from app.agent.tools import register_phase1a_tools
from app.agent.validation import WorkflowValidationService
from app.agent.workspace import VersionedWorkflowWorkspace, WorkspaceOperationError
from app.api.agent_v4 import router
from app.compiler.dify import DifyDslCompiler
from app.dify.client import DifyDraftWorkflow, DifyImportResult
from app.dify.version import DifyVersionInfo
from app.models import WorkflowPlan


def _compiler() -> DifyDslCompiler:
    return DifyDslCompiler(
        dsl_version="9.9.9",
        default_model_provider="openai",
        default_model_name="gpt-4o-mini",
    )


class FakeCreateDify:
    def __init__(
        self,
        *,
        failed_imports: int = 0,
        failed_draft_reads: int = 0,
        unknown_import_outcomes: int = 0,
    ) -> None:
        self.failed_imports = failed_imports
        self.failed_draft_reads = failed_draft_reads
        self.unknown_import_outcomes = unknown_import_outcomes
        self.import_count = 0
        self.successful_app_ids: list[str] = []
        self.idempotency_keys: list[str | None] = []
        self.imported_dsls: list[dict[str, Any]] = []
        self.draft_hashes: dict[str, str] = {}

    def __enter__(self) -> "FakeCreateDify":
        return self

    def __exit__(self, *_args: object) -> None:
        return None

    def import_yaml(
        self,
        yaml_content: str,
        *,
        name: str | None = None,
        idempotency_key: str | None = None,
    ) -> DifyImportResult:
        self.import_count += 1
        self.idempotency_keys.append(idempotency_key)
        self.imported_dsls.append(yaml.safe_load(yaml_content))
        if self.import_count <= self.unknown_import_outcomes:
            raise RuntimeError("import response unavailable")
        if self.import_count <= self.failed_imports:
            return DifyImportResult(
                id=f"import-{self.import_count}",
                status="failed",
                error="invalid test import",
            )
        app_id = f"created-app-{len(self.successful_app_ids) + 1}"
        self.successful_app_ids.append(app_id)
        self.draft_hashes[app_id] = f"created-hash-{len(self.successful_app_ids)}"
        app_mode = str(self.imported_dsls[-1]["app"]["mode"])
        return DifyImportResult(
            id=f"import-{self.import_count}",
            status="completed",
            app_id=app_id,
            app_mode=app_mode,
            workflow_url=f"http://dify.local/app/{app_id}/workflow",
        )

    def get_draft_workflow(self, app_id: str) -> DifyDraftWorkflow:
        if self.failed_draft_reads > 0:
            self.failed_draft_reads -= 1
            raise RuntimeError("draft response unavailable")
        return DifyDraftWorkflow(
            id=f"draft-{app_id}",
            graph={"nodes": [], "edges": []},
            features={},
            hash=self.draft_hashes[app_id],
            version="draft",
            environment_variables=[],
            conversation_variables=[],
            raw={},
        )


class CreateDecisionProvider(AgentDecisionProvider):
    def __init__(self, mode: str) -> None:
        self.mode = mode
        self.calls: list[BuilderContext] = []

    def decide(self, context: BuilderContext, tools):
        del tools
        self.calls.append(context)
        index = len(self.calls)
        if index == 1:
            return ToolCallDecision(
                type="tool_call",
                tool_name="workflow.inspect",
                arguments={"view": "summary"},
                goal_step_id="observe",
            )
        if index == 2:
            return ToolCallDecision(
                type="tool_call",
                tool_name="workflow.patch",
                arguments=_after_sales_patch(context, self.mode),
                goal_step_id="patch",
            )
        if index == 3:
            return ToolCallDecision(
                type="tool_call",
                tool_name="workflow.diff",
                arguments={"workspace_version": context.workspace["version"]},
                goal_step_id="review",
            )
        return FinishDecision(
            type="finish",
            summary="After-sales classification app is ready for approval.",
            evidence=["Scaffold patched", "Validation passed", "Review ready"],
        )


@dataclass
class Phase1BStack:
    fake_dify: FakeCreateDify
    store: AgentStore
    snapshot: WorkflowSnapshotService
    workspace: VersionedWorkflowWorkspace
    approval: AgentApprovalService
    provider: CreateDecisionProvider
    runtime: AgentRuntime
    modification_commit: ModificationCommitService
    creation_commit: CreationCommitService
    service: AgentApplicationService


def _stack(
    tmp_path,
    mode: str,
    *,
    failed_imports: int = 0,
    failed_draft_reads: int = 0,
    unknown_import_outcomes: int = 0,
) -> Phase1BStack:
    fake_dify = FakeCreateDify(
        failed_imports=failed_imports,
        failed_draft_reads=failed_draft_reads,
        unknown_import_outcomes=unknown_import_outcomes,
    )
    store = AgentStore(tmp_path / f"phase1b-{mode}.sqlite3")
    compiler = _compiler()
    catalog = NodeCapabilityCatalog()
    validation = WorkflowValidationService(
        compiler=compiler,
        expected_dsl_version="9.9.9",
    )
    workspace = VersionedWorkflowWorkspace(
        store=store,
        validation=validation,
        catalog=catalog,
    )
    review = WorkflowReviewService(store=store, workspace=workspace)
    approval = AgentApprovalService(store=store)
    registry = ToolRegistry()
    register_phase1a_tools(
        registry,
        store=store,
        workspace=workspace,
        review=review,
    )
    snapshot = WorkflowSnapshotService(
        client_factory=lambda: nullcontext(fake_dify),
        catalog=catalog,
        dify_version=DifyVersionInfo(
            source_dir="/dify",
            git_describe="v1.9.0",
            app_dsl_version="9.9.9",
        ),
    )
    provider = CreateDecisionProvider(mode)
    runtime = AgentRuntime(
        store=store,
        snapshot=snapshot,
        workspace=workspace,
        review=review,
        approval=approval,
        registry=registry,
        context_builder=BuilderContextBuilder(store=store),
        decision_provider=provider,
        policy=AgentToolPolicy(),
    )
    modification_commit = ModificationCommitService(
        store=store,
        workspace=workspace,
        approval=approval,
        validation=validation,
        compiler=compiler,
        client_factory=lambda: nullcontext(fake_dify),
    )
    creation_commit = CreationCommitService(
        store=store,
        workspace=workspace,
        approval=approval,
        validation=validation,
        compiler=compiler,
        client_factory=lambda: nullcontext(fake_dify),
    )
    service = AgentApplicationService(
        store=store,
        dispatcher=InlineRunDispatcher(runtime),
        approval=approval,
        commit_service=modification_commit,
        creation_commit_service=creation_commit,
    )
    return Phase1BStack(
        fake_dify=fake_dify,
        store=store,
        snapshot=snapshot,
        workspace=workspace,
        approval=approval,
        provider=provider,
        runtime=runtime,
        modification_commit=modification_commit,
        creation_commit=creation_commit,
        service=service,
    )


def _after_sales_patch(
    context: BuilderContext,
    mode: str,
) -> dict[str, Any]:
    nodes = context.workspace["nodes"]
    start_id = next(node["id"] for node in nodes if node["type"] == "start")
    terminal_type = "answer" if mode == "advanced-chat" else "end"
    terminal_id = next(
        node["id"] for node in nodes if node["type"] == terminal_type
    )
    query_selector = (
        [start_id, "sys.query"]
        if mode == "advanced-chat"
        else [start_id, "query"]
    )
    query_reference = (
        "{{#sys.query#}}"
        if mode == "advanced-chat"
        else f"{{{{#{start_id}.query#}}}}"
    )
    existing_terminal_params = (
        {"answer": "{{#tmp_regular.text#}}"}
        if mode == "advanced-chat"
        else {
            "outputs": [
                {
                    "variable": "answer",
                    "value_selector": ["tmp_regular", "text"],
                }
            ]
        }
    )
    priority_terminal_params = (
        {"answer": "{{#tmp_priority.text#}}"}
        if mode == "advanced-chat"
        else {
            "outputs": [
                {
                    "variable": "priority_answer",
                    "value_selector": ["tmp_priority", "text"],
                }
            ]
        }
    )
    return {
        "workspace_version": context.workspace["version"],
        "expected_base_hash": None,
        "rationale": "Classify after-sales requests and generate professional replies.",
        "operations": [
            {
                "op": "node.add",
                "temp_ref": "tmp_route",
                "node_type": "if-else",
                "title": "分类售后诉求",
                "params": {
                    "cases": [
                        {
                            "case_id": "priority",
                            "logical_operator": "and",
                            "conditions": [
                                {
                                    "variable_selector": query_selector,
                                    "comparison_operator": "contains",
                                    "value": "紧急",
                                    "varType": "string",
                                }
                            ],
                        }
                    ]
                },
            },
            {
                "op": "node.add",
                "temp_ref": "tmp_priority",
                "node_type": "llm",
                "title": "生成紧急售后回复",
                "params": {
                    "system_prompt": "你是专业售后专员，给出清晰、礼貌、可执行的回复。",
                    "user_prompt": f"优先处理以下问题：{query_reference}",
                },
            },
            {
                "op": "node.add",
                "temp_ref": "tmp_regular",
                "node_type": "llm",
                "title": "生成标准售后回复",
                "params": {
                    "system_prompt": "你是专业售后专员，分类后给出可靠回复。",
                    "user_prompt": f"处理以下售后问题：{query_reference}",
                },
            },
            {
                "op": "node.add",
                "temp_ref": "tmp_priority_terminal",
                "node_type": terminal_type,
                "title": "返回紧急处理结果",
                "params": priority_terminal_params,
            },
            {
                "op": "node.update",
                "node_id": terminal_id,
                "set": {
                    "title": "返回标准处理结果",
                    "params": existing_terminal_params,
                },
                "expected": {"type": terminal_type},
            },
            {
                "op": "edge.remove",
                "source": start_id,
                "source_handle": "source",
                "target": terminal_id,
                "target_handle": "target",
            },
            {"op": "edge.add", "source": start_id, "target": "tmp_route"},
            {
                "op": "edge.add",
                "source": "tmp_route",
                "source_handle": "false",
                "target": "tmp_regular",
            },
            {
                "op": "edge.add",
                "source": "tmp_route",
                "source_handle": "priority",
                "target": "tmp_priority",
            },
            {
                "op": "edge.add",
                "source": "tmp_regular",
                "target": terminal_id,
            },
            {
                "op": "edge.add",
                "source": "tmp_priority",
                "target": "tmp_priority_terminal",
            },
        ],
    }


def _initialize_create_workspace(stack: Phase1BStack, mode: str):
    session = stack.service.create_session(
        app_id=None,
        app_mode=mode,
        app_name="售后分析",
    )
    run = stack.store.create_run(
        AgentRun(session_id=session.id, goal="创建售后分析应用。")
    )
    observing = stack.store.update_run(
        run.transition_to(RunPhase.OBSERVING)
    )
    snapshot = stack.snapshot.capture(session)
    initialized, version = stack.workspace.initialize(
        observing,
        snapshot,
        GoalPlan(
            goal=run.goal,
            success_criteria=["Valid scaffold."],
            steps=[{"id": "patch", "description": "Patch scaffold."}],
        ),
    )
    return session, initialized, version


@pytest.mark.parametrize(
    ("mode", "terminal_type"),
    [("workflow", "end"), ("advanced-chat", "answer")],
)
def test_create_scaffold_is_valid_stable_and_rejects_invalid_mutation(
    tmp_path,
    mode,
    terminal_type,
) -> None:
    stack = _stack(tmp_path, mode)
    session, run, version = _initialize_create_workspace(stack, mode)
    snapshot_again = stack.snapshot.capture(session)
    plan = WorkflowPlan.model_validate(version.snapshot)

    assert session.operation == "create"
    assert session.app_id is None
    assert run.base_hash is None
    assert run.snapshot is not None
    assert run.snapshot.operation == "create"
    assert run.snapshot.base_graph == {}
    assert snapshot_again.base_plan == run.snapshot.base_plan
    assert [node.type for node in plan.nodes] == ["start", terminal_type]
    assert len(plan.edges) == 1
    assert plan.edges[0].source == plan.nodes[0].id
    assert plan.edges[0].target == plan.nodes[1].id
    assert all(UUID(node.id) for node in plan.nodes)
    assert version.validation["ok"] is True

    with pytest.raises(WorkspaceOperationError) as exc_info:
        stack.workspace.apply_patch(
            run.id,
            PatchDocument(
                workspace_version=version.id,
                expected_base_hash=None,
                rationale="Break the deterministic scaffold.",
                operations=[
                    {
                        "op": "edge.remove",
                        "source": plan.nodes[0].id,
                        "source_handle": "source",
                        "target": plan.nodes[1].id,
                        "target_handle": "target",
                    }
                ],
            ),
        )
    assert exc_info.value.code == "WORKSPACE_PATCH_VALIDATION_FAILED"
    assert stack.store.get_run(run.id).head_version_id == version.id

    with pytest.raises(WorkspaceOperationError) as hash_error:
        stack.workspace.apply_patch(
            run.id,
            PatchDocument(
                workspace_version=version.id,
                expected_base_hash="modify-only-hash",
                rationale="Use an invalid modification-only Hash.",
                operations=[
                    {
                        "op": "node.update",
                        "node_id": plan.nodes[0].id,
                        "set": {"title": "Changed"},
                    }
                ],
            ),
        )
    assert hash_error.value.code == "WORKSPACE_BASE_HASH_MISMATCH"


@pytest.mark.parametrize("mode", ["workflow", "advanced-chat"])
def test_create_runtime_review_approval_import_and_duplicate_are_safe(
    tmp_path,
    mode,
) -> None:
    stack = _stack(tmp_path, mode)
    session = stack.service.create_session(
        app_id=None,
        app_mode=mode,
        app_name="售后分析",
        app_description="接收、分类并回复售后问题。",
    )
    submitted = stack.service.submit_goal(
        session.id,
        message="创建售后分析应用：接收用户问题，分类后生成专业回复。",
    )
    run = stack.store.get_run(submitted.id)

    assert run.phase == RunPhase.WAITING_APPROVAL
    assert stack.fake_dify.import_count == 0
    assert stack.store.get_session(session.id).app_id is None
    assert run.review is not None
    assert run.review["ready"] is True
    assert run.review["business_diff"]
    assert run.review["validation"]["ok"] is True
    assert stack.provider.calls[0].app["operation"] == "create"
    assert stack.provider.calls[0].app["id"] is None
    assert stack.provider.calls[0].app["base_hash"] is None
    assert stack.provider.calls[0].workspace["node_count"] == 2
    assert any(
        event.type == "review.ready"
        for event in stack.store.list_events(run.id)
    )

    approval = stack.store.list_approvals(run.id)[0]
    assert approval.scope["base_hash"] is None
    assert approval.workspace_version_id == run.head_version_id
    approved, next_approval = stack.service.resolve_approval(
        run.id,
        approval.id,
        approved=True,
    )
    assert next_approval is None
    result = stack.service.commit(
        run.id,
        workspace_version_id=run.head_version_id,
        approval_id=approved.id,
    )

    assert result.status == "created"
    assert result.write_performed is True
    assert result.app_mode == mode
    assert result.draft_hash == "created-hash-1"
    assert stack.fake_dify.import_count == 1
    assert stack.fake_dify.idempotency_keys == [result.idempotency_key]
    persisted_session = stack.store.get_session(session.id)
    assert persisted_session.app_id == result.app_id
    assert persisted_session.operation == "modify"
    completed = stack.store.get_run(run.id)
    assert completed.phase == RunPhase.COMPLETED
    assert completed.base_hash == result.draft_hash
    assert completed.commit_result["app_id"] == result.app_id
    assert completed.commit_result["workflow_url"] == result.workflow_url
    imported = stack.fake_dify.imported_dsls[0]
    assert imported["app"]["mode"] == mode
    imported_types = {
        node["data"]["type"]
        for node in imported["workflow"]["graph"]["nodes"]
    }
    assert {"start", "if-else", "llm"} <= imported_types
    assert ("end" if mode == "workflow" else "answer") in imported_types

    duplicate = stack.service.commit(
        run.id,
        workspace_version_id=run.head_version_id,
        approval_id=approved.id,
    )
    assert duplicate == result
    assert stack.fake_dify.import_count == 1
    assert len(stack.fake_dify.successful_app_ids) == 1


def test_create_session_api_and_policy_reject_existing_canvas_context(
    tmp_path,
) -> None:
    stack = _stack(tmp_path, "workflow")
    application = FastAPI()
    application.include_router(router)
    application.state.agent_v4_enabled = True
    application.state.agent_store = stack.store
    application.state.agent_service = stack.service

    with TestClient(application) as client:
        response = client.post(
            "/api/v4/agent/sessions",
            json={
                "app_mode": "workflow",
                "app_name": "售后分析",
            },
        )
        missing_mode = client.post(
            "/api/v4/agent/sessions",
            json={"app_name": "缺少显式模式"},
        )

    assert response.status_code == 201
    assert missing_mode.status_code == 422
    assert response.json()["operation"] == "create"
    assert response.json()["app_id"] is None
    session_id = response.json()["id"]
    with pytest.raises(ValueError, match="existing-canvas"):
        stack.service.submit_goal(
            session_id,
            message="创建应用。",
            constraints=RunConstraints(
                selected_node_ids=["canvas-node"],
                canvas_draft_hash="modify-only",
            ),
        )
    assert stack.store.list_runs(session_id=session_id) == []
    assert stack.fake_dify.import_count == 0
    first_run = stack.service.submit_goal(
        session_id,
        message="创建售后分析应用。",
    )
    assert stack.store.get_run(first_run.id).phase == RunPhase.WAITING_APPROVAL
    with pytest.raises(ValueError, match="one recoverable Agent Run"):
        stack.service.submit_goal(
            session_id,
            message="不要并发创建第二个应用。",
        )
    assert stack.fake_dify.import_count == 0


def test_create_approval_version_mismatch_and_modify_adapter_are_rejected(
    tmp_path,
) -> None:
    stack = _stack(tmp_path, "workflow")
    session = stack.service.create_session(
        app_id=None,
        app_mode="workflow",
    )
    submitted = stack.service.submit_goal(session.id, message="创建售后分析应用。")
    run = stack.store.get_run(submitted.id)
    old_approval = stack.store.list_approvals(run.id)[0]
    approved, _ = stack.service.resolve_approval(
        run.id,
        old_approval.id,
        approved=True,
    )
    head = stack.store.get_workspace_head(run.id)
    start_id = next(
        node["id"] for node in head.snapshot["nodes"] if node["type"] == "start"
    )
    stack.workspace.apply_patch(
        run.id,
        PatchDocument(
            workspace_version=head.id,
            expected_base_hash=None,
            rationale="Change the Workspace after approval.",
            operations=[
                {
                    "op": "node.update",
                    "node_id": start_id,
                    "set": {"title": "更新后的输入"},
                    "expected": {"type": "start"},
                }
            ],
        ),
    )
    new_head = stack.store.get_run(run.id).head_version_id

    with pytest.raises(CommitServiceError) as version_error:
        stack.service.commit(
            run.id,
            workspace_version_id=new_head,
            approval_id=approved.id,
        )
    assert version_error.value.code in {
        "COMMIT_APPROVAL_NOT_APPROVED",
        "APPROVAL_WORKSPACE_VERSION_MISMATCH",
    }
    with pytest.raises(CommitServiceError) as adapter_error:
        stack.modification_commit.commit(
            run.id,
            workspace_version_id=new_head,
            approval_id=approved.id,
        )
    assert adapter_error.value.code == "COMMIT_ADAPTER_MODE_INVALID"
    assert stack.fake_dify.import_count == 0


def test_failed_import_keeps_workspace_allows_correction_and_new_approval(
    tmp_path,
) -> None:
    stack = _stack(tmp_path, "workflow", failed_imports=1)
    session = stack.service.create_session(
        app_id=None,
        app_mode="workflow",
    )
    submitted = stack.service.submit_goal(session.id, message="创建售后分析应用。")
    run = stack.store.get_run(submitted.id)
    first_approval = stack.store.list_approvals(run.id)[0]
    approved, _ = stack.service.resolve_approval(
        run.id,
        first_approval.id,
        approved=True,
    )

    with pytest.raises(CommitServiceError) as failed:
        stack.service.commit(
            run.id,
            workspace_version_id=run.head_version_id,
            approval_id=approved.id,
        )
    assert failed.value.code == "CREATE_IMPORT_FAILED"
    interrupted = stack.store.get_run(run.id)
    assert interrupted.phase == RunPhase.INTERRUPTED
    assert interrupted.error["code"] == "CREATE_IMPORT_FAILED"
    assert stack.store.get_approval(approved.id).status.value == "expired"
    version_count = len(stack.store.list_workspace_versions(run.id))

    head = stack.store.get_workspace_head(run.id)
    start_id = next(
        node["id"] for node in head.snapshot["nodes"] if node["type"] == "start"
    )
    stack.workspace.apply_patch(
        run.id,
        PatchDocument(
            workspace_version=head.id,
            expected_base_hash=None,
            rationale="Correct the scaffold after the failed import.",
            operations=[
                {
                    "op": "node.update",
                    "node_id": start_id,
                    "set": {"title": "接收售后问题"},
                    "expected": {"type": "start"},
                }
            ],
        ),
    )
    stack.service.resume(run.id)
    reviewed_again = stack.store.get_run(run.id)
    assert reviewed_again.phase == RunPhase.WAITING_APPROVAL
    assert len(stack.store.list_workspace_versions(run.id)) == version_count + 1
    second_approval = next(
        approval
        for approval in stack.store.list_approvals(run.id)
        if approval.status.value == "pending"
    )
    second_approved, _ = stack.service.resolve_approval(
        run.id,
        second_approval.id,
        approved=True,
    )
    result = stack.service.commit(
        run.id,
        workspace_version_id=reviewed_again.head_version_id,
        approval_id=second_approved.id,
    )

    assert result.status == "created"
    assert stack.fake_dify.import_count == 2
    assert len(stack.fake_dify.successful_app_ids) == 1
    assert stack.store.get_run(run.id).phase == RunPhase.COMPLETED


def test_successful_import_result_recovery_never_reimports(
    tmp_path,
) -> None:
    stack = _stack(tmp_path, "advanced-chat", failed_draft_reads=1)
    session = stack.service.create_session(
        app_id=None,
        app_mode="advanced-chat",
    )
    submitted = stack.service.submit_goal(session.id, message="创建售后 Chatflow。")
    run = stack.store.get_run(submitted.id)
    approval = stack.store.list_approvals(run.id)[0]
    approved, _ = stack.service.resolve_approval(
        run.id,
        approval.id,
        approved=True,
    )

    with pytest.raises(CommitServiceError) as recovery_error:
        stack.service.commit(
            run.id,
            workspace_version_id=run.head_version_id,
            approval_id=approved.id,
        )
    assert recovery_error.value.code == "CREATE_RESULT_RECOVERY_FAILED"
    pending = stack.store.get_run(run.id)
    assert pending.phase == RunPhase.INTERRUPTED
    assert pending.commit_result["status"] == "import_succeeded_recovery_pending"
    assert stack.fake_dify.import_count == 1
    assert len(stack.fake_dify.successful_app_ids) == 1

    reconstructed_store = AgentStore(stack.store.path)
    reconstructed_validation = WorkflowValidationService(
        compiler=_compiler(),
        expected_dsl_version="9.9.9",
    )
    reconstructed_workspace = VersionedWorkflowWorkspace(
        store=reconstructed_store,
        validation=reconstructed_validation,
        catalog=NodeCapabilityCatalog(),
    )
    reconstructed_commit = CreationCommitService(
        store=reconstructed_store,
        workspace=reconstructed_workspace,
        approval=AgentApprovalService(store=reconstructed_store),
        validation=reconstructed_validation,
        compiler=_compiler(),
        client_factory=lambda: nullcontext(stack.fake_dify),
    )
    recovered = reconstructed_commit.commit(
        run.id,
        workspace_version_id=run.head_version_id,
        approval_id=approved.id,
    )

    assert recovered.status == "created"
    assert recovered.app_id == stack.fake_dify.successful_app_ids[0]
    assert stack.fake_dify.import_count == 1
    assert reconstructed_store.get_run(run.id).phase == RunPhase.COMPLETED
    assert reconstructed_store.get_session(session.id).app_id == recovered.app_id


def test_unknown_import_outcome_blocks_automatic_duplicate(
    tmp_path,
) -> None:
    stack = _stack(
        tmp_path,
        "workflow",
        unknown_import_outcomes=1,
    )
    session = stack.service.create_session(
        app_id=None,
        app_mode="workflow",
    )
    submitted = stack.service.submit_goal(session.id, message="创建售后分析应用。")
    run = stack.store.get_run(submitted.id)
    approval = stack.store.list_approvals(run.id)[0]
    approved, _ = stack.service.resolve_approval(
        run.id,
        approval.id,
        approved=True,
    )

    with pytest.raises(CommitServiceError) as first_error:
        stack.service.commit(
            run.id,
            workspace_version_id=run.head_version_id,
            approval_id=approved.id,
        )
    assert first_error.value.code == "CREATE_IMPORT_OUTCOME_UNKNOWN"
    assert stack.fake_dify.import_count == 1
    assert stack.store.get_run(run.id).commit_result["status"] == (
        "import_outcome_unknown"
    )

    with pytest.raises(CommitServiceError) as retry_error:
        stack.service.commit(
            run.id,
            workspace_version_id=run.head_version_id,
            approval_id=approved.id,
        )
    assert retry_error.value.code == "CREATE_IMPORT_OUTCOME_UNKNOWN"
    assert stack.fake_dify.import_count == 1
