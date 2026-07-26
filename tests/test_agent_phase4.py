from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from contextlib import nullcontext
from copy import deepcopy
import json
from pathlib import Path
from typing import Any

from fastapi import FastAPI
from fastapi.testclient import TestClient
import pytest

from app.agent.approval import AgentApprovalService
from app.agent.compatibility import DifyCompatibilityMatrix
from app.agent.commit import CommitServiceError
from app.agent.config_app import (
    ConfigAppSnapshotService,
    ConfigReviewService,
    VersionedConfigWorkspace,
)
from app.agent.config_commit import ConfigCommitService
from app.agent.config_patch import (
    ConfigPatchDocument,
    config_patch_risk,
)
from app.agent.context import BuilderContextBuilder
from app.agent.decision import DecisionOutcome
from app.agent.patch import PatchDocument
from app.agent.policy import AgentToolPolicy
from app.agent.registry import ToolRegistry
from app.agent.runtime import AgentRuntime
from app.agent.service import AgentApplicationService
from app.agent.skills import (
    SkillRegistry,
    register_skill_tool,
    visible_tool_specs_for_mode,
)
from app.agent.state import (
    AgentBudget,
    AgentRun,
    AgentSession,
    GoalPlan,
    GoalStep,
    Observation,
    RunPhase,
    RunStatus,
    StrictModel,
    ToolCallDecision,
)
from app.agent.store import AgentStore
from app.agent.tools import register_config_tools
from app.agent.trace import redact_sensitive_data
from app.agent.workspace import WorkspaceOperationError
from app.dify.client import DifyAppDetail
from app.dify.version import DifyVersionInfo
from app.evals.models import EvaluationCaseResult
from app.evals.runner import (
    EvaluationRunner,
    load_cases,
)
from app.api.agent_v4 import router as agent_v4_router


class FakeConfigDify:
    def __init__(self, app_mode: str = "completion") -> None:
        self.app_mode = app_mode
        self.write_count = 0
        self.config = {
            "pre_prompt": "Be concise.",
            "model": {
                "provider": "openai",
                "name": "gpt-4o-mini",
                "mode": "chat",
                "completion_params": {"temperature": 0.2},
                "metadata": {"preserve": True},
            },
            "updated_at": "hash-1",
            "preserved": {"metadata": True},
        }
        if app_mode == "agent-chat":
            self.config["agent_mode"] = {
                "enabled": True,
                "strategy": "react",
                "prompt": "Use tools carefully.",
                "tools": [],
            }

    def __enter__(self) -> "FakeConfigDify":
        return self

    def __exit__(self, *_args: object) -> None:
        return None

    def get_app_detail(self, app_id: str) -> DifyAppDetail:
        return DifyAppDetail(
            id=app_id,
            name="Configured Service",
            mode=self.app_mode,
            description="Configured app fixture",
            raw={"model_config": deepcopy(self.config)},
        )

    def update_model_config(
        self,
        _app_id: str,
        model_config: dict[str, Any],
    ) -> dict[str, Any]:
        self.write_count += 1
        self.config = deepcopy(model_config)
        self.config["updated_at"] = f"hash-{self.write_count + 1}"
        return {
            "result": "success",
            "updated_at": self.config["updated_at"],
        }


class ConfigDecisionProvider:
    def __init__(self) -> None:
        self.calls = 0
        self.visible_tools: list[list[str]] = []

    def decide(self, context, tools):
        self.calls += 1
        self.visible_tools.append([tool.name for tool in tools])
        version = context.workspace["version"]
        if self.calls == 1:
            decision = ToolCallDecision(
                type="tool_call",
                tool_name="config.inspect",
                arguments={"view": "summary"},
                goal_step_id="observe",
            )
        elif self.calls == 2:
            decision = ToolCallDecision(
                type="tool_call",
                tool_name="config.patch",
                arguments={
                    "workspace_version": version,
                    "expected_base_hash": context.app["base_hash"],
                    "app_mode": "completion",
                    "operations": [
                        {
                            "op": "config.prompt.set",
                            "value": "Return a professional JSON response.",
                            "expected": "Be concise.",
                            "check_expected": True,
                        }
                    ],
                    "rationale": "Add the requested response contract.",
                },
                goal_step_id="patch",
            )
        elif self.calls == 3:
            decision = ToolCallDecision(
                type="tool_call",
                tool_name="config.validate",
                arguments={"workspace_version": version},
                goal_step_id="validate",
            )
        elif self.calls == 4:
            decision = ToolCallDecision(
                type="tool_call",
                tool_name="config.diff",
                arguments={"workspace_version": version},
                goal_step_id="review",
            )
        else:
            from app.agent.state import FinishDecision

            decision = FinishDecision(
                type="finish",
                summary="Configured-app change is ready.",
                evidence=["Config validation and Diff passed."],
            )
        return DecisionOutcome(decision=decision, model_calls=1)


def _version() -> DifyVersionInfo:
    return DifyVersionInfo(
        source_dir="../dify",
        git_describe="test",
        app_dsl_version="9.9.9",
    )


def _goal_plan() -> GoalPlan:
    return GoalPlan(
        goal="Update configuration",
        success_criteria=["Configuration remains valid."],
        steps=[
            GoalStep(id="observe", description="Inspect."),
            GoalStep(id="patch", description="Patch."),
            GoalStep(id="validate", description="Validate."),
            GoalStep(id="review", description="Review."),
        ],
    )


def _config_fixture(
    tmp_path: Path,
    *,
    app_mode: str = "completion",
    compatibility: DifyCompatibilityMatrix | None = None,
):
    store = AgentStore(tmp_path / "agent.sqlite3")
    dify = FakeConfigDify(app_mode)
    matrix = compatibility or DifyCompatibilityMatrix()
    snapshot_service = ConfigAppSnapshotService(
        client_factory=lambda: nullcontext(dify),
        dify_version=_version(),
        compatibility=matrix,
    )
    session = store.create_session(
        AgentSession(
            app_id="configured-1",
            app_mode=app_mode,
            operation="modify",
        )
    )
    run = store.create_run(
        AgentRun(
            session_id=session.id,
            goal="Update the configured app.",
        )
    )
    snapshot = snapshot_service.capture(session)
    workspace = VersionedConfigWorkspace(store=store)
    run, version = workspace.initialize(run, snapshot, _goal_plan())
    review = ConfigReviewService(store=store, workspace=workspace)
    return store, dify, session, run, version, workspace, review


def _advance_to_waiting_approval(
    store: AgentStore,
    run: AgentRun,
) -> AgentRun:
    current = run
    for phase in (
        RunPhase.OBSERVING,
        RunPhase.PLANNING,
        RunPhase.ACTING,
        RunPhase.VALIDATING,
        RunPhase.WAITING_APPROVAL,
    ):
        if current.phase == phase:
            continue
        current = store.update_run(current.transition_to(phase))
    return current


def test_config_patch_ir_is_separate_typed_domain() -> None:
    config_patch = ConfigPatchDocument.model_validate(
        {
            "workspace_version": "v1",
            "expected_base_hash": "hash-1",
            "app_mode": "completion",
            "operations": [
                {
                    "op": "config.model.set",
                    "value": {
                        "provider": "anthropic",
                        "name": "claude-3-5-sonnet",
                    },
                }
            ],
            "rationale": "Replace provider.",
        }
    )
    assert config_patch_risk(config_patch) == "medium"
    with pytest.raises(ValueError):
        PatchDocument.model_validate(config_patch.model_dump(mode="json"))
    with pytest.raises(ValueError):
        ConfigPatchDocument.model_validate(
            {
                "workspace_version": "v1",
                "expected_base_hash": "hash-1",
                "app_mode": "completion",
                "operations": [
                    {
                        "op": "node.add",
                        "temp_ref": "tmp_1",
                        "node_type": "llm",
                        "title": "LLM",
                        "params": {},
                    }
                ],
                "rationale": "Domain confusion.",
            }
        )


def test_v4_api_accepts_existing_config_apps_and_keeps_create_on_v3(
    tmp_path: Path,
) -> None:
    class NoopDispatcher:
        def submit(self, _run_id: str) -> None:
            return None

        def close(self) -> None:
            return None

    store = AgentStore(tmp_path / "api.sqlite3")
    service = AgentApplicationService(
        store=store,
        dispatcher=NoopDispatcher(),
        approval=AgentApprovalService(store=store),
        commit_service=object(),
    )
    application = FastAPI()
    application.include_router(agent_v4_router)
    application.state.agent_v4_enabled = True
    application.state.agent_store = store
    application.state.agent_service = service
    with TestClient(application) as client:
        existing = client.post(
            "/api/v4/agent/sessions",
            json={
                "app_id": "completion-1",
                "app_mode": "completion",
            },
        )
        create = client.post(
            "/api/v4/agent/sessions",
            json={"app_mode": "completion"},
        )
    assert existing.status_code == 201
    assert existing.json()["operation"] == "modify"
    assert create.status_code == 422
    assert create.json()["detail"]["code"] == "AGENT_SESSION_INVALID"
    assert "v3 fallback" in create.json()["detail"]["message"]


@pytest.mark.parametrize("app_mode", ["chat", "completion", "agent-chat"])
def test_config_workspace_preserves_unrelated_fields_and_preconditions(
    tmp_path: Path,
    app_mode: str,
) -> None:
    store, _dify, _session, run, version, workspace, _review = (
        _config_fixture(tmp_path, app_mode=app_mode)
    )
    operation = (
        {
            "op": "config.agent.set",
            "enabled": True,
            "strategy": "react",
            "prompt": "Ask before external actions.",
        }
        if app_mode == "agent-chat"
        else {
            "op": "config.prompt.set",
            "value": "Use a professional tone.",
            "expected": "Be concise.",
            "check_expected": True,
        }
    )
    result = workspace.apply_patch(
        run.id,
        ConfigPatchDocument.model_validate(
            {
                "workspace_version": version.id,
                "expected_base_hash": run.base_hash,
                "app_mode": app_mode,
                "operations": [operation],
                "rationale": "Apply the requested bounded change.",
            }
        ),
    )
    head = store.get_workspace_version(result.workspace_version)
    assert head.snapshot["preserved"] == {"metadata": True}
    bad_head = store.get_workspace_head(run.id)
    with pytest.raises(
        WorkspaceOperationError,
        match="precondition failed",
    ):
        workspace.apply_patch(
            run.id,
            ConfigPatchDocument.model_validate(
                {
                    "workspace_version": bad_head.id,
                    "expected_base_hash": run.base_hash,
                    "app_mode": app_mode,
                    "operations": [
                        {
                            "op": "config.prompt.set",
                            "value": "new",
                            "expected": "wrong",
                            "check_expected": True,
                        }
                    ],
                    "rationale": "Exercise the precondition.",
                }
            ),
        )
    assert store.get_workspace_head(run.id).id == bad_head.id


def test_config_runtime_review_approval_commit_and_tool_visibility(
    tmp_path: Path,
) -> None:
    store = AgentStore(tmp_path / "runtime.sqlite3")
    dify = FakeConfigDify("completion")
    matrix = DifyCompatibilityMatrix()
    snapshot = ConfigAppSnapshotService(
        client_factory=lambda: nullcontext(dify),
        dify_version=_version(),
        compatibility=matrix,
    )
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
    register_skill_tool(
        registry,
        store=store,
        skills=SkillRegistry(),
    )
    provider = ConfigDecisionProvider()
    runtime = AgentRuntime(
        store=store,
        snapshot=snapshot,
        workspace=workspace,
        review=review,
        approval=approval,
        registry=registry,
        context_builder=BuilderContextBuilder(store=store),
        decision_provider=provider,
        policy=AgentToolPolicy(store=store),
    )
    session = store.create_session(
        AgentSession(
            app_id="configured-1",
            app_mode="completion",
            operation="modify",
        )
    )
    run = store.create_run(
        AgentRun(
            session_id=session.id,
            goal="Return a professional JSON response.",
        )
    )
    result = runtime.run(run.id)
    assert result["phase"] == "waiting_approval"
    assert dify.write_count == 0
    assert all(
        all(
            name.startswith(("config.", "skill."))
            for name in tool_names
        )
        for tool_names in provider.visible_tools
    )
    approval_record = next(
        item
        for item in store.list_approvals(run.id)
        if item.action == "commit"
    )
    approval.resolve(run.id, approval_record.id, approved=True)
    commit_service = ConfigCommitService(
        store=store,
        workspace=workspace,
        approval=approval,
        client_factory=lambda: nullcontext(dify),
    )
    commit = commit_service.commit(
        run.id,
        workspace_version_id=store.get_run(run.id).head_version_id,
        approval_id=approval_record.id,
    )
    assert commit.status == "committed"
    assert commit.new_hash == "hash-2"
    assert dify.write_count == 1
    assert dify.config["pre_prompt"] == (
        "Return a professional JSON response."
    )
    assert dify.config["preserved"] == {"metadata": True}
    assert dify.config["model"]["metadata"] == {"preserve": True}
    duplicate = commit_service.commit(
        run.id,
        workspace_version_id=commit.workspace_version_id,
        approval_id=approval_record.id,
    )
    assert duplicate == commit
    assert dify.write_count == 1


def test_config_model_patch_preserves_unrelated_model_metadata(
    tmp_path: Path,
) -> None:
    store, _dify, _session, run, version, workspace, _review = (
        _config_fixture(tmp_path)
    )
    result = workspace.apply_patch(
        run.id,
        ConfigPatchDocument.model_validate(
            {
                "workspace_version": version.id,
                "expected_base_hash": run.base_hash,
                "app_mode": "completion",
                "operations": [
                    {
                        "op": "config.model.set",
                        "value": {
                            "provider": "anthropic",
                            "name": "claude-3-5-sonnet",
                            "mode": "chat",
                            "completion_params": {
                                "temperature": 0.1
                            },
                        },
                        "expected": {
                            "provider": "openai",
                            "name": "gpt-4o-mini",
                            "mode": "chat",
                            "completion_params": {
                                "temperature": 0.2
                            },
                        },
                        "check_expected": True,
                    }
                ],
                "rationale": "Replace only the model selection.",
            }
        ),
    )
    config = store.get_workspace_version(
        result.workspace_version
    ).snapshot
    assert config["model"]["provider"] == "anthropic"
    assert config["model"]["metadata"] == {"preserve": True}


def test_config_commit_hash_conflict_never_writes(
    tmp_path: Path,
) -> None:
    store, dify, _session, run, version, workspace, review = (
        _config_fixture(tmp_path)
    )
    patched = workspace.apply_patch(
        run.id,
        ConfigPatchDocument.model_validate(
            {
                "workspace_version": version.id,
                "expected_base_hash": run.base_hash,
                "app_mode": "completion",
                "operations": [
                    {
                        "op": "config.prompt.set",
                        "value": "Changed.",
                    }
                ],
                "rationale": "Change the prompt.",
            }
        ),
    )
    run = _advance_to_waiting_approval(
        store,
        store.get_run(run.id),
    )
    approval_service = AgentApprovalService(store=store)
    approval = approval_service.request_for_review(
        run.id,
        review.build(run.id),
    )
    approval_service.resolve(run.id, approval.id, approved=True)
    dify.config["updated_at"] = "external-hash"
    result = ConfigCommitService(
        store=store,
        workspace=workspace,
        approval=approval_service,
        client_factory=lambda: nullcontext(dify),
    ).commit(
        run.id,
        workspace_version_id=patched.workspace_version,
        approval_id=approval.id,
    )
    assert result.status == "conflicted"
    assert result.write_performed is False
    assert dify.write_count == 0
    assert store.get_run(run.id).error["code"] == (
        "DIFY_MODEL_CONFIG_HASH_CONFLICT"
    )


def test_configured_agent_tool_binding_requires_destructive_then_commit_approval(
    tmp_path: Path,
) -> None:
    store, dify, _session, run, version, workspace, review = (
        _config_fixture(tmp_path, app_mode="agent-chat")
    )
    patched = workspace.apply_patch(
        run.id,
        ConfigPatchDocument.model_validate(
            {
                "workspace_version": version.id,
                "expected_base_hash": run.base_hash,
                "app_mode": "agent-chat",
                "operations": [
                    {
                        "op": "config.agent.set",
                        "enabled": True,
                        "strategy": "react",
                        "tools": [
                            {
                                "provider_id": "tools/support",
                                "tool_name": "human_queue",
                                "parameters": {},
                            }
                        ],
                    }
                ],
                "rationale": "Add the explicitly requested human queue.",
            }
        ),
    )
    run = _advance_to_waiting_approval(
        store,
        store.get_run(run.id),
    )
    approvals = AgentApprovalService(store=store)
    destructive = approvals.request_for_review(
        run.id,
        review.build(run.id),
    )
    assert destructive.action == "destructive_change"
    bypass = approvals._create_or_reuse(
        run,
        action="commit",
        risk="low",
    )
    approvals.resolve(run.id, bypass.id, approved=True)
    commit_service = ConfigCommitService(
        store=store,
        workspace=workspace,
        approval=approvals,
        client_factory=lambda: nullcontext(dify),
    )
    with pytest.raises(CommitServiceError) as bypass_error:
        commit_service.commit(
            run.id,
            workspace_version_id=patched.workspace_version,
            approval_id=bypass.id,
        )
    assert bypass_error.value.code == "DESTRUCTIVE_APPROVAL_REQUIRED"
    assert dify.write_count == 0
    _, commit_approval = approvals.resolve(
        run.id,
        destructive.id,
        approved=True,
    )
    assert commit_approval is not None
    assert commit_approval.action == "commit"
    approvals.resolve(
        run.id,
        commit_approval.id,
        approved=True,
    )
    result = commit_service.commit(
        run.id,
        workspace_version_id=patched.workspace_version,
        approval_id=commit_approval.id,
    )
    assert result.status == "committed"
    assert dify.write_count == 1
    assert dify.config["agent_mode"]["tools"][0]["tool_name"] == (
        "human_queue"
    )


def test_skill_registry_is_deterministic_and_cannot_expand_tools(
    tmp_path: Path,
) -> None:
    store, _dify, _session, run, _version_id, workspace, review = (
        _config_fixture(tmp_path)
    )

    class Empty(StrictModel):
        pass

    registry = ToolRegistry()
    register_config_tools(
        registry,
        store=store,
        workspace=workspace,
        review=review,
    )
    registry.register(
        name="admin.write",
        version="1.0.0",
        description="Test-only forbidden Dify write.",
        side_effect="dify_write",
        approval="always",
        input_model=Empty,
        output_model=Empty,
        executor=lambda _arguments, _context: {},
    )
    skills = SkillRegistry()
    register_skill_tool(registry, store=store, skills=skills)
    assert [skill.name for skill in skills.list()] == [
        "error-handling",
        "file-upload-extraction",
        "human-fallback",
        "json-output",
        "knowledge-retrieval",
    ]
    visible = visible_tool_specs_for_mode(registry, "completion")
    visible_names = {spec.name for spec in visible}
    assert "admin.write" not in visible_names
    loaded = skills.load(
        "json-output",
        app_mode="completion",
        visible_tool_names=visible_names,
    )
    assert loaded.name == "json-output"
    assert "admin.write" not in {
        requirement.tool_name
        for requirement in loaded.required_tools
    }
    with pytest.raises(ValueError):
        skills.load(
            "human-fallback",
            app_mode="completion",
            visible_tool_names=visible_names,
        )
    result = registry.execute(
        "skill.search",
        {"names": ["json-output"]},
        run_id=run.id,
        session_id=run.session_id,
    )
    assert result.ok
    assert "admin.write" not in result.observation["visible_tools"]


def test_compatibility_matrix_fails_closed_but_keeps_diagnostics(
    tmp_path: Path,
) -> None:
    store = AgentStore(tmp_path / "unsupported.sqlite3")
    dify = FakeConfigDify("chat")
    matrix = DifyCompatibilityMatrix()
    snapshot_service = ConfigAppSnapshotService(
        client_factory=lambda: nullcontext(dify),
        dify_version=DifyVersionInfo(
            source_dir="../dify",
            git_describe="v2.0.0",
            app_dsl_version="1.0.0",
        ),
        compatibility=matrix,
    )
    session = store.create_session(
        AgentSession(
            app_id="chat-1",
            app_mode="chat",
            operation="modify",
        )
    )
    run = store.create_run(
        AgentRun(session_id=session.id, goal="Inspect then update.")
    )
    snapshot = snapshot_service.capture(session)
    assert snapshot.compatibility["diagnostic_supported"] is True
    assert snapshot.compatibility["mutation_supported"] is False
    workspace = VersionedConfigWorkspace(store=store)
    run, version = workspace.initialize(run, snapshot, _goal_plan())
    assert workspace.validate_head(run.id).ok
    with pytest.raises(
        WorkspaceOperationError,
        match="No tested Dify/DSL",
    ):
        workspace.apply_patch(
            run.id,
            ConfigPatchDocument.model_validate(
                {
                    "workspace_version": version.id,
                    "expected_base_hash": run.base_hash,
                    "app_mode": "chat",
                    "operations": [
                        {
                            "op": "config.prompt.set",
                            "value": "blocked",
                        }
                    ],
                    "rationale": "Unsupported mutation.",
                }
            ),
        )


def test_compatibility_fixtures_match_the_version_matrix() -> None:
    fixture_dir = (
        Path(__file__).parents[1]
        / "app"
        / "evals"
        / "fixtures"
        / "compatibility"
    )
    matrix = DifyCompatibilityMatrix()
    for fixture_path in sorted(fixture_dir.glob("*.json")):
        fixture = json.loads(fixture_path.read_text(encoding="utf-8"))
        decision = matrix.decide(
            DifyVersionInfo(
                source_dir="../dify",
                git_describe=fixture["dify_version"],
                app_dsl_version=fixture["dsl_version"],
            ),
            app_mode="workflow",
        )
        assert decision.rule_id == fixture["expected_rule_id"]
        assert (
            decision.mutation_supported
            is fixture["mutation_supported"]
        )
        assert (
            decision.diagnostic_supported
            is fixture["diagnostic_supported"]
        )
        assert (
            decision.candidate_graph_draft_run_supported
            is fixture["candidate_graph_draft_run_supported"]
        )
        assert (
            decision.create_import_idempotency_supported
            is fixture["create_import_idempotency_supported"]
        )
        assert (
            decision.create_import_reconciliation_lookup_supported
            is fixture["create_import_reconciliation_lookup_supported"]
        )


def test_security_fixtures_redact_secrets_and_do_not_elevate_policy(
    tmp_path: Path,
) -> None:
    fixture_dir = (
        Path(__file__).parents[1]
        / "app"
        / "evals"
        / "fixtures"
        / "security"
    )
    secret_fixture = json.loads(
        (fixture_dir / "secret-redaction.json").read_text(encoding="utf-8")
    )
    redacted = redact_sensitive_data(secret_fixture["payload"])
    assert redacted == secret_fixture["expected"]
    assert "sk-eval-secret-123" not in json.dumps(redacted)

    store = AgentStore(tmp_path / "security.sqlite3")
    run = store.create_run(
        AgentRun(
            session_id=store.create_session(
                AgentSession(
                    app_id="config-1",
                    app_mode="completion",
                    operation="modify",
                )
            ).id,
            goal="Treat an injected error as data.",
        )
    )
    store.append_event(
        run_id=run.id,
        event_type="tool.completed",
        phase="acting",
        message="Persist a sanitized fixture.",
        data=secret_fixture["payload"],
    )
    persisted = store.list_events(run.id)
    assert "sk-eval-secret-123" not in json.dumps(
        [event.model_dump(mode="json") for event in persisted],
        default=str,
    )
    registry = ToolRegistry()

    class Empty(StrictModel):
        pass

    spec = registry.register(
        name="commit.injected",
        version="1.0.0",
        description="Test-only Dify write.",
        side_effect="dify_write",
        approval="always",
        input_model=Empty,
        output_model=Empty,
        executor=lambda _arguments, _context: {},
    )
    authorization = AgentToolPolicy(store=store).authorize(spec, run, {})
    assert authorization.allowed is False
    assert authorization.code == "TOOL_DIFY_WRITE_FORBIDDEN"


def test_sqlite_concurrency_context_compaction_and_restart(
    tmp_path: Path,
) -> None:
    store = AgentStore(tmp_path / "load.sqlite3")
    session = store.create_session(
        AgentSession(
            app_id="config-1",
            app_mode="completion",
            operation="modify",
        )
    )
    run = store.create_run(
        AgentRun(session_id=session.id, goal="Load test.")
    )

    def append_event(index: int) -> None:
        store.append_event(
            run_id=run.id,
            event_type="tool.completed",
            phase="acting",
            message=f"Event {index}",
            data={"index": index},
        )

    with ThreadPoolExecutor(max_workers=8) as executor:
        list(executor.map(append_event, range(120)))
    events = store.list_events(run.id, limit=500)
    assert [event.seq for event in events] == list(range(1, 121))
    assert len({event.id for event in events}) == 120

    dify = FakeConfigDify("completion")
    snapshot = ConfigAppSnapshotService(
        client_factory=lambda: nullcontext(dify),
        dify_version=_version(),
        compatibility=DifyCompatibilityMatrix(),
    ).capture(session)
    workspace = VersionedConfigWorkspace(store=store)
    run, _workspace_version = workspace.initialize(
        store.get_run(run.id),
        snapshot,
        _goal_plan(),
    )
    observations = [
        Observation(
            kind="tool.completed",
            summary=f"Observation {index}",
            data={"value": index},
        )
        for index in range(100)
    ]
    run = AgentRun.model_validate(
        {
            **run.model_dump(),
            "observations": [
                observation.model_dump(mode="json")
                for observation in observations
            ],
        }
    )
    store.update_run(run)
    context = BuilderContextBuilder(
        store=store,
        max_recent_observations=8,
    ).build(store.get_run(run.id))
    assert len(context.recent_observations) == 8
    assert context.older_observation_summary["count"] == 92
    assert context.trace_summary["tool.completed"] == 120
    _advance_to_waiting_approval(store, store.get_run(run.id))

    in_flight = AgentRun(
        session_id=session.id,
        goal="In-flight side effect.",
        phase=RunPhase.TESTING,
        status=RunStatus.RUNNING,
    )
    in_flight = store.create_run(in_flight)
    before = len(store.list_events(in_flight.id))
    reconstructed = AgentStore(tmp_path / "load.sqlite3")
    assert reconstructed.interrupt_active_runs() == 1
    interrupted = reconstructed.get_run(in_flight.id)
    assert interrupted.phase == RunPhase.INTERRUPTED
    after_events = reconstructed.list_events(in_flight.id)
    assert len(after_events) == before + 1
    assert all(event.type != "tool.started" for event in after_events)


def test_fixed_evaluation_suite_is_reproducible_and_meets_gates() -> None:
    cases = load_cases()
    assert len(cases) == 10
    assert {
        case.required_skill
        for case in cases
        if case.required_skill is not None
    } == {
        "error-handling",
        "file-upload-extraction",
        "human-fallback",
        "json-output",
        "knowledge-retrieval",
    }
    report = EvaluationRunner().run(
        cases,
        suite_version="phase4-1.0.0",
    )
    second = EvaluationRunner().run(
        reversed(cases),
        suite_version="phase4-1.0.0",
    )
    assert report.model_dump(mode="json") == second.model_dump(mode="json")
    assert report.gates.passed
    assert report.runtime_executed is True
    assert report.executor == "deterministic-agent-runtime"
    assert all(
        result.executor_evidence.get("runtime_executed") is True
        for result in report.cases
    )
    assert report.metrics.final_validity_rate == 1
    assert report.metrics.goal_completion_rate == 0.9
    assert report.metrics.unrelated_preservation_rate == 1
    assert report.metrics.auto_repair_rate == 1
    assert report.metrics.unapproved_writes == 0
    assert report.metrics.incorrect_conflict_overwrites == 0
    committed = json.loads(
        (
            Path(__file__).parents[1]
            / "app"
            / "evals"
            / "reports"
            / "phase4-release.json"
        ).read_text(encoding="utf-8")
    )
    assert report.model_dump(mode="json") == committed


def test_live_provider_evaluation_requires_explicit_opt_in() -> None:
    class FakeLiveExecutor:
        name = "fake-live-provider"
        live_provider = True
        reproducible = False
        runtime_executed = True

        def execute(self, case):
            return EvaluationCaseResult(
                case_id=case.id,
                case_version=case.version,
                goal=case.goal,
                app_mode=case.app_mode,
                status="failed",
                reviewable=False,
                final_valid=False,
                goal_completed=False,
                required_changes_present=False,
                forbidden_changes_absent=True,
                invariant_passed=True,
                unrelated_total=0,
                unrelated_preserved=0,
                repairable_failure=False,
                auto_repaired=False,
                unapproved_writes=0,
                incorrect_conflict_overwrites=0,
                readable_trace=True,
                structured_terminal_reason=True,
                trace_event_count=1,
                terminal_reason={
                    "code": "LIVE_FIXTURE",
                    "message": "Explicit live-provider fixture.",
                },
            )

    with pytest.raises(ValueError, match="explicit opt-in"):
        EvaluationRunner(executor=FakeLiveExecutor())
    assert EvaluationRunner(
        executor=FakeLiveExecutor(),
        allow_live_provider=True,
    ).executor.live_provider
