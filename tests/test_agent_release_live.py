from __future__ import annotations

from copy import deepcopy
import os
from pathlib import Path
from typing import Any
from urllib.parse import urlparse
from uuid import uuid4

import pytest

from app.agent.approval import AgentApprovalService
from app.agent.catalog import NodeCapabilityCatalog
from app.agent.commit import CreationCommitService, ModificationCommitService
from app.agent.compatibility import DifyCompatibilityMatrix
from app.agent.config_app import (
    ConfigAppSnapshotService,
    ConfigReviewService,
    VersionedConfigWorkspace,
    extract_model_config,
)
from app.agent.config_commit import ConfigCommitService
from app.agent.context import BuilderContext, BuilderContextBuilder
from app.agent.decision import (
    AgentDecisionProvider,
    OpenAICompatibleDecisionProvider,
)
from app.agent.planner import fallback_plan
from app.agent.policy import AgentToolPolicy
from app.agent.registry import ToolRegistry
from app.agent.review import WorkflowReviewService
from app.agent.runtime import AgentRuntime
from app.agent.service import AgentApplicationService, InlineRunDispatcher
from app.agent.snapshot import WorkflowSnapshotService
from app.agent.state import AgentBudget, FinishDecision, RunPhase, ToolCallDecision
from app.agent.store import AgentStore
from app.agent.tools import register_config_tools, register_phase1a_tools
from app.agent.validation import WorkflowValidationService
from app.agent.workspace import VersionedWorkflowWorkspace
from app.compiler.agent import (
    compile_agent_app_dsl,
    compile_chat_app_dsl,
    compile_completion_app_dsl,
)
from app.compiler.dify import DifyDslCompiler
from app.config import Settings, load_settings
from app.dify.client import DifyClient, DifyModelListItem
from app.dify.version import DifyVersionInfo, read_dify_version_info
from app.models import WorkflowPlan
from tests.test_agent_phase1a_live import (
    _delete_temporary_app,
    _stack as _phase1a_stack,
)
from tests.test_agent_phase1b import CreateDecisionProvider


LIVE_ACCEPTANCE_ENABLED = (
    os.environ.get("CHAT2DIFY_LIVE_DIFY_ACCEPTANCE", "").strip() == "1"
)
LOCAL_DIFY_HOSTS = {"localhost", "127.0.0.1", "::1"}
LIVE_PROVIDER_ACCEPTANCE_ENABLED = (
    os.environ.get("CHAT2DIFY_LIVE_PROVIDER_ACCEPTANCE", "").strip() == "1"
)

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


@pytest.fixture(scope="module")
def live_settings() -> Settings:
    settings = load_settings()
    hostname = urlparse(settings.dify_console_api_base).hostname
    if hostname not in LOCAL_DIFY_HOSTS:
        pytest.fail(
            "Live Release Gate acceptance is restricted to localhost Dify."
        )
    if not settings.dify_email or not settings.dify_password:
        pytest.fail(
            "DIFY_EMAIL and DIFY_PASSWORD are required for live acceptance."
        )
    return settings


@pytest.mark.parametrize("mode", ["workflow", "advanced-chat"])
def test_creation_commit_service_imports_and_recovers_real_dify_app(
    tmp_path: Path,
    live_settings: Settings,
    mode: str,
) -> None:
    version = read_dify_version_info(live_settings.dify_source_path)
    stack = _creation_stack(
        tmp_path,
        settings=live_settings,
        version=version,
        mode=mode,
    )
    app_name = f"chat2dify-release-create-{mode}-{uuid4().hex[:10]}"
    app_id: str | None = None
    try:
        session = stack["service"].create_session(
            app_id=None,
            app_mode=mode,
            app_name=app_name,
            app_description="Release Gate creation acceptance.",
        )
        submitted = stack["service"].submit_goal(
            session.id,
            message=(
                "Create an after-sales workflow that classifies priority and "
                "returns a professional response."
            ),
        )
        run = stack["store"].get_run(submitted.id)
        assert run.phase == RunPhase.WAITING_APPROVAL
        assert stack["store"].get_session(session.id).app_id is None
        approval = next(
            item
            for item in stack["store"].list_approvals(run.id)
            if item.action == "commit"
        )
        approved, next_approval = stack["service"].resolve_approval(
            run.id,
            approval.id,
            approved=True,
        )
        assert next_approval is None
        result = stack["service"].commit(
            run.id,
            workspace_version_id=run.head_version_id,
            approval_id=approved.id,
        )
        app_id = result.app_id
        assert result.status == "created"
        assert result.app_mode == mode
        assert result.draft_hash
        with DifyClient(live_settings) as client:
            detail = client.get_app_detail(app_id)
            draft = client.get_draft_workflow(app_id)
        assert detail.mode == mode
        assert draft.hash == result.draft_hash
        assert stack["store"].get_session(session.id).operation == "modify"
        duplicate = stack["service"].commit(
            run.id,
            workspace_version_id=run.head_version_id,
            approval_id=approved.id,
        )
        assert duplicate == result
    finally:
        stack["service"].close()
        if app_id:
            _delete_temporary_app(live_settings, app_id)


@pytest.mark.parametrize("mode", ["chat", "completion", "agent-chat"])
def test_config_runtime_approval_and_commit_against_real_dify(
    tmp_path: Path,
    live_settings: Settings,
    mode: str,
) -> None:
    version = read_dify_version_info(live_settings.dify_source_path)
    app_name = f"chat2dify-release-config-{mode}-{uuid4().hex[:10]}"
    marker = f"Release Gate prompt {uuid4().hex}"
    app_id: str | None = None
    service: AgentApplicationService | None = None
    try:
        with DifyClient(live_settings) as client:
            model = next(
                item
                for item in client.list_models(
                    model_type="llm",
                ).data
                if item.available
            )
            dsl = _configured_app_dsl(
                mode,
                settings=live_settings,
                version=version,
                app_name=app_name,
                model=model,
            )
            imported = client.import_yaml(
                dsl,
                name=app_name,
                idempotency_key=f"release-config-{uuid4()}",
            )
            assert imported.app_id
            app_id = imported.app_id
            baseline_app = client.get_app_detail(app_id)
        baseline = extract_model_config(baseline_app)
        assert baseline is not None
        service, store = _config_stack(
            tmp_path,
            settings=live_settings,
            version=version,
            mode=mode,
            marker=marker,
        )
        session = service.create_session(app_id=app_id, app_mode=mode)
        submitted = service.submit_goal(
            session.id,
            message="Set the bounded Release Gate prompt and preserve all other configuration.",
        )
        run = store.get_run(submitted.id)
        assert run.phase == RunPhase.WAITING_APPROVAL
        with DifyClient(live_settings) as client:
            before_approval = extract_model_config(
                client.get_app_detail(app_id)
            )
        assert before_approval == baseline
        approval = next(
            item
            for item in store.list_approvals(run.id)
            if item.action == "commit"
        )
        approved, next_approval = service.resolve_approval(
            run.id,
            approval.id,
            approved=True,
        )
        assert next_approval is None
        result = service.commit(
            run.id,
            workspace_version_id=run.head_version_id,
            approval_id=approved.id,
        )
        assert result.status == "committed"
        assert result.write_performed is True
        with DifyClient(live_settings) as client:
            changed = extract_model_config(client.get_app_detail(app_id))
        assert changed is not None
        assert changed["pre_prompt"] == marker
        preserved = deepcopy(baseline)
        preserved.pop("pre_prompt", None)
        for metadata_key in (
            "created_at",
            "created_by",
            "updated_at",
            "updated_by",
        ):
            preserved.pop(metadata_key, None)
        if mode == "completion":
            # Dify 1.14.2 does not accept retriever_resource in Completion
            # config validation and re-materializes its own inapplicable
            # default on readback.
            preserved.pop("retriever_resource", None)
        _assert_deep_subset(preserved, changed)
        duplicate = service.commit(
            run.id,
            workspace_version_id=run.head_version_id,
            approval_id=approved.id,
        )
        assert duplicate == result
    finally:
        if service is not None:
            service.close()
        if app_id:
            _delete_temporary_app(live_settings, app_id)


def test_real_dify_import_does_not_deduplicate_idempotency_key(
    live_settings: Settings,
) -> None:
    version = read_dify_version_info(live_settings.dify_source_path)
    compiler = _compiler(live_settings, version)
    app_name = f"chat2dify-release-import-retry-{uuid4().hex[:10]}"
    idempotency_key = f"release-import-retry-{uuid4()}"
    app_ids: list[str] = []
    try:
        plan = fallback_plan(
            "Return the workflow input.",
            app_name=app_name,
            app_mode="workflow",
        )
        dsl = compiler.compile(plan)
        with DifyClient(live_settings) as client:
            first = client.import_yaml(
                dsl,
                name=app_name,
                idempotency_key=idempotency_key,
            )
            second = client.import_yaml(
                dsl,
                name=app_name,
                idempotency_key=idempotency_key,
            )
            assert first.app_id
            assert second.app_id
            app_ids.extend([first.app_id, second.app_id])
            assert second.app_id != first.app_id
            assert client.get_app_detail(first.app_id).mode == "workflow"
            assert client.get_app_detail(second.app_id).mode == "workflow"
    finally:
        for app_id in sorted(set(app_ids)):
            _delete_temporary_app(live_settings, app_id)


def test_real_dify_draft_run_does_not_execute_candidate_graph(
    live_settings: Settings,
) -> None:
    version = read_dify_version_info(live_settings.dify_source_path)
    compiler = _compiler(live_settings, version)
    app_name = f"chat2dify-release-candidate-run-{uuid4().hex[:10]}"
    app_id: str | None = None
    try:
        baseline_plan = WorkflowPlan.model_validate(
            {
                "name": app_name,
                "app_mode": "workflow",
                "nodes": [
                    {
                        "id": "start",
                        "type": "start",
                        "title": "Start",
                        "params": {
                            "variables": [
                                {
                                    "name": "query",
                                    "type": "paragraph",
                                    "required": True,
                                    "label": "Query",
                                }
                            ]
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
                                    "value_selector": ["start", "query"],
                                }
                            ]
                        },
                    },
                ],
                "edges": [{"source": "start", "target": "end"}],
            }
        )
        with DifyClient(live_settings) as client:
            imported = client.import_yaml(
                compiler.compile(baseline_plan),
                name=app_name,
                idempotency_key=f"release-candidate-run-{uuid4()}",
            )
            assert imported.app_id
            app_id = imported.app_id
            baseline = client.get_draft_workflow(app_id)
            persisted_run = client.run_draft_workflow(
                app_id,
                inputs={"query": "persisted-baseline"},
                timeout_seconds=60,
            )
            assert persisted_run.ok is True
            assert persisted_run.outputs == {"answer": "persisted-baseline"}

            candidate_graph = deepcopy(baseline.graph)
            candidate_end = next(
                node
                for node in candidate_graph["nodes"]
                if node.get("data", {}).get("type") == "end"
            )
            candidate_end["data"]["outputs"][0]["variable"] = (
                "candidate_answer"
            )
            candidate_probe = client._run_draft_workflow_once(
                app_id,
                payload={
                    "inputs": {"query": "persisted-baseline"},
                    "graph": candidate_graph,
                },
                timeout_seconds=60,
            )
            assert candidate_probe is not None
            assert candidate_probe.ok is True
            assert candidate_probe.outputs == {"answer": "persisted-baseline"}
            assert "candidate_answer" not in candidate_probe.outputs

            after_probe = client.get_draft_workflow(app_id)
        assert after_probe.hash == baseline.hash
        assert after_probe.graph == baseline.graph
    finally:
        if app_id:
            _delete_temporary_app(live_settings, app_id)


@pytest.mark.skipif(
    not LIVE_PROVIDER_ACCEPTANCE_ENABLED,
    reason=(
        "Set CHAT2DIFY_LIVE_PROVIDER_ACCEPTANCE=1 to allow bounded real "
        "Provider calls for Release Gate acceptance."
    ),
)
def test_real_provider_drives_runtime_to_review_without_dify_write(
    tmp_path: Path,
    live_settings: Settings,
) -> None:
    version = read_dify_version_info(live_settings.dify_source_path)
    compiler = _compiler(live_settings, version)
    app_name = f"chat2dify-release-provider-{uuid4().hex[:10]}"
    app_id: str | None = None
    stack = None
    try:
        baseline_plan = fallback_plan(
            "Answer the user's support question.",
            app_name=app_name,
            app_mode="workflow",
        )
        with DifyClient(live_settings) as client:
            imported = client.import_yaml(
                compiler.compile(baseline_plan),
                name=app_name,
                idempotency_key=f"release-provider-{uuid4()}",
            )
            assert imported.app_id
            app_id = imported.app_id
            baseline = client.get_draft_workflow(app_id)
        stack = _phase1a_stack(
            tmp_path / "provider.sqlite3",
            settings=live_settings,
            version=version,
            compiler=compiler,
            decision_provider=OpenAICompatibleDecisionProvider(
                live_settings
            ),
        )
        session = stack.service.create_session(
            app_id=app_id,
            app_mode="workflow",
        )
        submitted = stack.service.submit_goal(
            session.id,
                message=(
                    "Inspect the existing workflow, change only node id 'llm' "
                    "title to 'Release Gate Provider Verified', validate and "
                    "diff it, then finish for review. Do not ask questions, "
                    "test, commit, or publish."
                ),
                budget=AgentBudget(
                    max_iterations=12,
                    max_model_calls=8,
                ),
        )
        run = stack.store.get_run(submitted.id)
        assert run.phase == RunPhase.WAITING_APPROVAL
        assert run.budget_usage.model_calls >= 2
        head = stack.store.get_workspace_head(run.id)
        llm = next(
            node
            for node in head.snapshot["nodes"]
            if node["type"] == "llm"
        )
        assert llm["title"] == "Release Gate Provider Verified"
        assert any(
            event.type == "review.ready"
            for event in stack.store.list_events(run.id)
        )
        with DifyClient(live_settings) as client:
            after_review = client.get_draft_workflow(app_id)
        assert after_review.hash == baseline.hash
        assert after_review.graph == baseline.graph
    finally:
        if stack is not None:
            stack.service.close()
        if app_id:
            _delete_temporary_app(live_settings, app_id)


class _LiveConfigDecisionProvider(AgentDecisionProvider):
    def __init__(self, *, mode: str, marker: str) -> None:
        self.mode = mode
        self.marker = marker
        self.calls = 0

    def decide(self, context: BuilderContext, tools):
        del tools
        self.calls += 1
        version = context.workspace["version"]
        if self.calls == 1:
            return ToolCallDecision(
                type="tool_call",
                tool_name="config.inspect",
                arguments={"view": "summary"},
                goal_step_id="observe",
            )
        if self.calls == 2:
            return ToolCallDecision(
                type="tool_call",
                tool_name="config.patch",
                arguments={
                    "workspace_version": version,
                    "expected_base_hash": context.app["base_hash"],
                    "app_mode": self.mode,
                    "operations": [
                        {
                            "op": "config.prompt.set",
                            "value": self.marker,
                        }
                    ],
                    "rationale": "Apply one bounded prompt change.",
                },
                goal_step_id="patch",
            )
        if self.calls == 3:
            return ToolCallDecision(
                type="tool_call",
                tool_name="config.validate",
                arguments={"workspace_version": version},
                goal_step_id="validate",
            )
        if self.calls == 4:
            return ToolCallDecision(
                type="tool_call",
                tool_name="config.diff",
                arguments={"workspace_version": version},
                goal_step_id="review",
            )
        return FinishDecision(
            type="finish",
            summary="The configured-app prompt change is ready for approval.",
            evidence=["Config validation and Diff passed."],
        )


def _creation_stack(
    tmp_path: Path,
    *,
    settings: Settings,
    version: DifyVersionInfo,
    mode: str,
) -> dict[str, Any]:
    store = AgentStore(tmp_path / f"create-{mode}.sqlite3")
    compiler = _compiler(settings, version)
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
    client_factory = lambda: DifyClient(settings)
    runtime = AgentRuntime(
        store=store,
        snapshot=WorkflowSnapshotService(
            client_factory=client_factory,
            catalog=catalog,
            dify_version=version,
            compatibility=DifyCompatibilityMatrix(),
        ),
        workspace=workspace,
        review=review,
        approval=approval,
        registry=registry,
        context_builder=BuilderContextBuilder(store=store),
        decision_provider=CreateDecisionProvider(mode),
        policy=AgentToolPolicy(store=store),
    )
    modification = ModificationCommitService(
        store=store,
        workspace=workspace,
        approval=approval,
        validation=validation,
        compiler=compiler,
        client_factory=client_factory,
    )
    creation = CreationCommitService(
        store=store,
        workspace=workspace,
        approval=approval,
        validation=validation,
        compiler=compiler,
        client_factory=client_factory,
    )
    return {
        "store": store,
        "service": AgentApplicationService(
            store=store,
            dispatcher=InlineRunDispatcher(runtime),
            approval=approval,
            commit_service=modification,
            creation_commit_service=creation,
        ),
    }


def _config_stack(
    tmp_path: Path,
    *,
    settings: Settings,
    version: DifyVersionInfo,
    mode: str,
    marker: str,
) -> tuple[AgentApplicationService, AgentStore]:
    store = AgentStore(tmp_path / f"config-{mode}.sqlite3")
    workspace = VersionedConfigWorkspace(store=store)
    review = ConfigReviewService(store=store, workspace=workspace)
    approval = AgentApprovalService(store=store)
    registry = ToolRegistry()
    register_config_tools(
        registry,
        store=store,
        workspace=workspace,
        review=review,
    )
    client_factory = lambda: DifyClient(settings)
    runtime = AgentRuntime(
        store=store,
        snapshot=ConfigAppSnapshotService(
            client_factory=client_factory,
            dify_version=version,
            compatibility=DifyCompatibilityMatrix(),
        ),
        workspace=workspace,
        review=review,
        approval=approval,
        registry=registry,
        context_builder=BuilderContextBuilder(store=store),
        decision_provider=_LiveConfigDecisionProvider(
            mode=mode,
            marker=marker,
        ),
        policy=AgentToolPolicy(store=store),
    )
    service = AgentApplicationService(
        store=store,
        dispatcher=InlineRunDispatcher(runtime),
        approval=approval,
        commit_service=object(),  # type: ignore[arg-type]
        config_commit_service=ConfigCommitService(
            store=store,
            workspace=workspace,
            approval=approval,
            client_factory=client_factory,
        ),
    )
    return service, store


def _configured_app_dsl(
    mode: str,
    *,
    settings: Settings,
    version: DifyVersionInfo,
    app_name: str,
    model: DifyModelListItem,
) -> str:
    kwargs = {
        "message": "Provide a concise professional response.",
        "app_name": app_name,
        "app_description": "Release Gate configured-app acceptance.",
        "dsl_version": version.app_dsl_version,
        "settings": settings,
        "model_selections": [model],
    }
    if mode == "chat":
        return compile_chat_app_dsl(**kwargs)
    if mode == "completion":
        return compile_completion_app_dsl(**kwargs)
    return compile_agent_app_dsl(**kwargs)


def _compiler(
    settings: Settings,
    version: DifyVersionInfo,
) -> DifyDslCompiler:
    return DifyDslCompiler(
        dsl_version=version.app_dsl_version,
        default_model_provider=settings.dify_default_model_provider,
        default_model_name=settings.dify_default_model_name,
        default_dataset_ids=deepcopy(settings.dify_default_dataset_ids),
    )


def _assert_deep_subset(
    expected: Any,
    actual: Any,
    *,
    path: str = "$",
) -> None:
    if expected is None:
        return
    if expected == "" and actual is None:
        return
    if isinstance(expected, dict):
        assert isinstance(actual, dict)
        for key, value in expected.items():
            assert key in actual, f"{path}.{key} is missing"
            _assert_deep_subset(
                value,
                actual[key],
                path=f"{path}.{key}",
            )
        return
    assert actual == expected, f"{path} changed"
