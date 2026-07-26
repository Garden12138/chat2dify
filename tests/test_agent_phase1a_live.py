from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass
import os
from pathlib import Path
from typing import Any
from urllib.parse import urlparse
from uuid import uuid4

import pytest

from app.agent.approval import AgentApprovalService
from app.agent.catalog import NodeCapabilityCatalog
from app.agent.commit import ModificationCommitService
from app.agent.context import BuilderContext, BuilderContextBuilder
from app.agent.decision import AgentDecisionProvider
from app.agent.planner import fallback_plan
from app.agent.policy import AgentToolPolicy
from app.agent.registry import ToolRegistry
from app.agent.review import WorkflowReviewService
from app.agent.runtime import AgentRuntime
from app.agent.service import AgentApplicationService, InlineRunDispatcher
from app.agent.snapshot import WorkflowSnapshotService
from app.agent.state import FinishDecision, RunPhase, ToolCallDecision
from app.agent.store import AgentStore
from app.agent.tools import register_phase1a_tools
from app.agent.validation import WorkflowValidationService
from app.agent.workspace import VersionedWorkflowWorkspace
from app.compiler.dify import DifyDslCompiler
from app.config import Settings, load_settings
from app.dify.client import DifyClient, DifyClientError
from app.dify.version import DifyVersionInfo, read_dify_version_info


LIVE_ACCEPTANCE_ENABLED = (
    os.environ.get("CHAT2DIFY_LIVE_DIFY_ACCEPTANCE", "").strip() == "1"
)
LOCAL_DIFY_HOSTS = {"localhost", "127.0.0.1", "::1"}

pytestmark = [
    pytest.mark.live_dify,
    pytest.mark.skipif(
        not LIVE_ACCEPTANCE_ENABLED,
        reason=(
            "Set CHAT2DIFY_LIVE_DIFY_ACCEPTANCE=1 to create and delete "
            "temporary apps in a localhost Dify instance."
        ),
    ),
]


class LiveBranchDecisionProvider(AgentDecisionProvider):
    def __init__(self, mode: str) -> None:
        self.mode = mode
        self.calls = 0

    def decide(self, context: BuilderContext, tools):
        del tools
        self.calls += 1
        if self.calls == 1:
            return ToolCallDecision(
                type="tool_call",
                tool_name="workflow.inspect",
                arguments={"view": "summary"},
                goal_step_id="observe",
            )
        if self.calls == 2:
            return ToolCallDecision(
                type="tool_call",
                tool_name="workflow.patch",
                arguments=_classification_patch(context, self.mode),
                goal_step_id="patch",
            )
        if self.calls == 3:
            return ToolCallDecision(
                type="tool_call",
                tool_name="workflow.diff",
                arguments={"workspace_version": context.workspace["version"]},
                goal_step_id="review",
            )
        return FinishDecision(
            type="finish",
            summary="The localhost Dify change is ready for approval.",
            evidence=[
                "The Patch was accepted.",
                "Deterministic validation passed.",
                "The business and technical Diffs were reviewed.",
            ],
        )


class LiveUpdateDecisionProvider(AgentDecisionProvider):
    def __init__(self) -> None:
        self.calls = 0

    def decide(self, context: BuilderContext, tools):
        del tools
        self.calls += 1
        if self.calls == 1:
            return ToolCallDecision(
                type="tool_call",
                tool_name="workflow.inspect",
                arguments={"view": "summary"},
                goal_step_id="observe",
            )
        if self.calls == 2:
            return ToolCallDecision(
                type="tool_call",
                tool_name="workflow.patch",
                arguments={
                    "workspace_version": context.workspace["version"],
                    "expected_base_hash": context.app["base_hash"],
                    "rationale": "Update one existing node title.",
                    "operations": [
                        {
                            "op": "node.update",
                            "node_id": "llm",
                            "set": {"title": "默认回复（冲突验证）"},
                            "expected": {"type": "llm"},
                        }
                    ],
                },
                goal_step_id="patch",
            )
        if self.calls == 3:
            return ToolCallDecision(
                type="tool_call",
                tool_name="workflow.diff",
                arguments={"workspace_version": context.workspace["version"]},
                goal_step_id="review",
            )
        return FinishDecision(
            type="finish",
            summary="The title update is ready for conflict validation.",
            evidence=["The title-only Patch passed deterministic validation."],
        )


@dataclass
class LivePhase1AStack:
    store: AgentStore
    service: AgentApplicationService


@pytest.fixture(scope="module")
def live_settings() -> Settings:
    settings = load_settings()
    hostname = urlparse(settings.dify_console_api_base).hostname
    if hostname not in LOCAL_DIFY_HOSTS:
        pytest.fail(
            "Live Phase 1A acceptance is restricted to a localhost Dify instance."
        )
    if not settings.dify_email or not settings.dify_password:
        pytest.fail(
            "DIFY_EMAIL and DIFY_PASSWORD are required for live Phase 1A acceptance."
        )
    return settings


@pytest.mark.parametrize("mode", ["workflow", "advanced-chat"])
def test_local_dify_observe_patch_validate_review_approval_commit_and_conflict(
    tmp_path: Path,
    live_settings: Settings,
    mode: str,
) -> None:
    version = read_dify_version_info(live_settings.dify_source_path)
    compiler = _compiler(live_settings, version)
    name = f"chat2dify-p1a-live-{mode}-{uuid4().hex[:10]}"
    app_id: str | None = None

    try:
        baseline_plan = fallback_plan(
            "处理用户问题",
            app_name=name,
            app_mode=mode,
        )
        with DifyClient(live_settings) as client:
            imported = client.import_yaml(
                compiler.compile(baseline_plan),
                name=name,
                idempotency_key=f"p1a-live-{uuid4()}",
            )
            assert imported.app_id, (
                f"Dify did not return an app ID: status={imported.status}, "
                f"error={imported.error}"
            )
            app_id = imported.app_id
            detail = client.get_app_detail(app_id)
            baseline = client.get_draft_workflow(app_id)

        assert detail.mode == mode
        assert baseline.hash
        assert len(baseline.graph.get("nodes", [])) == 3

        happy = _stack(
            tmp_path / "happy.sqlite3",
            settings=live_settings,
            version=version,
            compiler=compiler,
            decision_provider=LiveBranchDecisionProvider(mode),
        )
        session = happy.service.create_session(app_id=app_id, app_mode=mode)
        submitted = happy.service.submit_goal(
            session.id,
            message="增加紧急诉求分类分支，并保持原有分支不变。",
        )
        run = happy.store.get_run(submitted.id)

        assert run.phase == RunPhase.WAITING_APPROVAL
        assert run.review is not None
        assert run.review["ready"] is True
        assert run.review["validation"]["ok"] is True
        assert run.review["business_diff"]
        assert run.review["technical_diff"]
        assert run.review["risk"]["risk"] == "medium"

        with DifyClient(live_settings) as client:
            before_approval = client.get_draft_workflow(app_id)
        assert before_approval.hash == baseline.hash
        assert before_approval.graph == baseline.graph

        approval = happy.store.list_approvals(run.id)[0]
        approved, next_approval = happy.service.resolve_approval(
            run.id,
            approval.id,
            approved=True,
        )
        assert next_approval is None
        committed = happy.service.commit(
            run.id,
            workspace_version_id=run.head_version_id,
            approval_id=approved.id,
        )
        assert committed.status == "committed"
        assert committed.write_performed is True
        assert committed.new_hash
        assert committed.new_hash != baseline.hash

        with DifyClient(live_settings) as client:
            after_commit = client.get_draft_workflow(app_id)
        assert after_commit.hash == committed.new_hash
        assert len(after_commit.graph.get("nodes", [])) == 6
        assert not _contains_temp_ref(after_commit.graph)

        duplicate = happy.service.commit(
            run.id,
            workspace_version_id=run.head_version_id,
            approval_id=approved.id,
        )
        assert duplicate == committed
        with DifyClient(live_settings) as client:
            after_duplicate = client.get_draft_workflow(app_id)
        assert after_duplicate.hash == after_commit.hash
        assert after_duplicate.graph == after_commit.graph

        conflict = _stack(
            tmp_path / "conflict.sqlite3",
            settings=live_settings,
            version=version,
            compiler=compiler,
            decision_provider=LiveUpdateDecisionProvider(),
        )
        conflict_session = conflict.service.create_session(
            app_id=app_id,
            app_mode=mode,
        )
        conflict_submitted = conflict.service.submit_goal(
            conflict_session.id,
            message="再次增加一个紧急诉求分类分支。",
        )
        conflict_run = conflict.store.get_run(conflict_submitted.id)
        assert conflict_run.phase == RunPhase.WAITING_APPROVAL
        conflict_approval = conflict.store.list_approvals(conflict_run.id)[0]
        conflict_approved, _ = conflict.service.resolve_approval(
            conflict_run.id,
            conflict_approval.id,
            approved=True,
        )

        external_graph = deepcopy(after_commit.graph)
        viewport = external_graph.setdefault("viewport", {})
        viewport["x"] = float(viewport.get("x", 0)) + 17.0
        with DifyClient(live_settings) as client:
            external_sync = client.sync_draft_workflow(
                app_id,
                graph=external_graph,
                features=after_commit.features,
                hash=after_commit.hash,
                environment_variables=after_commit.environment_variables,
                conversation_variables=after_commit.conversation_variables,
            )
            external_draft = client.get_draft_workflow(app_id)
        assert external_sync.hash != after_commit.hash
        assert external_draft.hash == external_sync.hash

        conflicted = conflict.service.commit(
            conflict_run.id,
            workspace_version_id=conflict_run.head_version_id,
            approval_id=conflict_approved.id,
        )
        assert conflicted.status == "conflicted"
        assert conflicted.write_performed is False
        with DifyClient(live_settings) as client:
            after_conflict = client.get_draft_workflow(app_id)
        assert after_conflict.hash == external_sync.hash
        assert after_conflict.graph == external_draft.graph
    finally:
        if app_id is not None:
            _delete_temporary_app(live_settings, app_id)


def _stack(
    database_path: Path,
    *,
    settings: Settings,
    version: DifyVersionInfo,
    compiler: DifyDslCompiler,
    decision_provider: AgentDecisionProvider,
) -> LivePhase1AStack:
    store = AgentStore(database_path)
    catalog = NodeCapabilityCatalog()
    validation = WorkflowValidationService(
        compiler=compiler,
        expected_dsl_version=version.app_dsl_version,
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

    def client_factory():
        return DifyClient(settings)

    snapshot = WorkflowSnapshotService(
        client_factory=client_factory,
        catalog=catalog,
        dify_version=version,
    )
    runtime = AgentRuntime(
        store=store,
        snapshot=snapshot,
        workspace=workspace,
        review=review,
        approval=approval,
        registry=registry,
        context_builder=BuilderContextBuilder(store=store),
        decision_provider=decision_provider,
        policy=AgentToolPolicy(),
    )
    commit = ModificationCommitService(
        store=store,
        workspace=workspace,
        approval=approval,
        validation=validation,
        compiler=compiler,
        client_factory=client_factory,
    )
    service = AgentApplicationService(
        store=store,
        dispatcher=InlineRunDispatcher(runtime),
        approval=approval,
        commit_service=commit,
    )
    return LivePhase1AStack(store=store, service=service)


def _compiler(
    settings: Settings,
    version: DifyVersionInfo,
) -> DifyDslCompiler:
    return DifyDslCompiler(
        dsl_version=version.app_dsl_version,
        default_model_provider=settings.dify_default_model_provider,
        default_model_name=settings.dify_default_model_name,
        default_dataset_ids=settings.dify_default_dataset_ids,
    )


def _classification_patch(
    context: BuilderContext,
    mode: str,
) -> dict[str, Any]:
    query_selector = (
        ["start", "sys.query"]
        if mode == "advanced-chat"
        else ["start", "query"]
    )
    query_reference = (
        "{{#sys.query#}}"
        if mode == "advanced-chat"
        else "{{#start.query#}}"
    )
    terminal_type = "answer" if mode == "advanced-chat" else "end"
    terminal_params = (
        {"answer": "{{#tmp_priority_llm.text#}}"}
        if mode == "advanced-chat"
        else {
            "outputs": [
                {
                    "variable": "priority_answer",
                    "value_selector": ["tmp_priority_llm", "text"],
                }
            ]
        }
    )
    return {
        "workspace_version": context.workspace["version"],
        "expected_base_hash": context.app["base_hash"],
        "rationale": (
            "Add one priority classification branch and preserve the "
            "original branch."
        ),
        "operations": [
            {
                "op": "node.add",
                "temp_ref": "tmp_route",
                "node_type": "if-else",
                "title": "识别优先诉求",
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
                "temp_ref": "tmp_priority_llm",
                "node_type": "llm",
                "title": "生成优先处理回复",
                "params": {
                    "system_prompt": (
                        "你是优先级售后专员，给出专业且可执行的建议。"
                    ),
                    "user_prompt": f"请优先处理：{query_reference}",
                },
            },
            {
                "op": "node.add",
                "temp_ref": "tmp_priority_terminal",
                "node_type": terminal_type,
                "title": "返回优先处理结果",
                "params": terminal_params,
            },
            {
                "op": "edge.remove",
                "source": "start",
                "source_handle": "source",
                "target": "llm",
                "target_handle": "target",
            },
            {"op": "edge.add", "source": "start", "target": "tmp_route"},
            {
                "op": "edge.add",
                "source": "tmp_route",
                "source_handle": "false",
                "target": "llm",
            },
            {
                "op": "edge.add",
                "source": "tmp_route",
                "source_handle": "priority",
                "target": "tmp_priority_llm",
            },
            {
                "op": "edge.add",
                "source": "tmp_priority_llm",
                "target": "tmp_priority_terminal",
            },
        ],
    }


def _contains_temp_ref(value: Any) -> bool:
    if isinstance(value, dict):
        return any(
            _contains_temp_ref(key) or _contains_temp_ref(item)
            for key, item in value.items()
        )
    if isinstance(value, list):
        return any(_contains_temp_ref(item) for item in value)
    return isinstance(value, str) and value.startswith("tmp_")


def _delete_temporary_app(settings: Settings, app_id: str) -> None:
    with DifyClient(settings) as client:
        client.login()
        response = client._client.delete(  # noqa: SLF001 - live test cleanup
            f"/apps/{app_id}",
            headers=client._csrf_headers(),  # noqa: SLF001
        )
        if response.status_code == 401 and client.refresh_token():
            response = client._client.delete(  # noqa: SLF001
                f"/apps/{app_id}",
                headers=client._csrf_headers(),  # noqa: SLF001
            )
        if response.status_code != 204:
            raise DifyClientError(
                "Dify temporary app cleanup failed with "
                f"HTTP {response.status_code}."
            )
        missing = client._get_with_auth_retry(  # noqa: SLF001
            f"/apps/{app_id}"
        )
        if missing.status_code != 404:
            raise DifyClientError(
                "Dify temporary app cleanup could not be verified."
            )
