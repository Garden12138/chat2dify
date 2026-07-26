from __future__ import annotations

from dataclasses import dataclass
import json
from typing import Any

import pytest

from app.agent.approval import AgentApprovalService, ApprovalServiceError
from app.agent.catalog import NodeCapabilityCatalog
from app.agent.context import BuilderContext, BuilderContextBuilder
from app.agent.decision import AgentDecisionProvider
from app.agent.execution import (
    DraftExecutionAdapter,
    DraftRunService,
    DraftTestRequest,
    MinimalTestInputGenerator,
    PreparedDraftTest,
    classify_plan_side_effects,
    normalize_execution_result,
)
from app.agent.policy import AgentToolPolicy
from app.agent.registry import ToolRegistry
from app.agent.review import WorkflowReviewService
from app.agent.runtime import AgentRuntime
from app.agent.service import AgentApplicationService, InlineRunDispatcher
from app.agent.state import (
    AgentBudget,
    AgentWorkflowSnapshot,
    ApprovalStatus,
    FinishDecision,
    RunConstraints,
    RunPhase,
    ToolCallDecision,
)
from app.agent.store import AgentStore
from app.agent.tools import register_phase1a_tools, register_phase3_tools
from app.agent.validation import AgentValidationReport
from app.agent.workspace import VersionedWorkflowWorkspace
from app.models import WorkflowPlan


class PassingValidation:
    def validate(self, plan: WorkflowPlan) -> AgentValidationReport:
        return AgentValidationReport(
            ok=True,
            dsl_version="9.9.9",
            roundtrip_ok=True,
            graph_compiled=True,
            side_effects=classify_plan_side_effects(plan),
        )


@dataclass
class StaticSnapshotService:
    snapshot: AgentWorkflowSnapshot

    def capture(self, _session) -> AgentWorkflowSnapshot:
        return self.snapshot.model_copy(deep=True)


class UnusedCommitService:
    def commit(self, *_args, **_kwargs):
        raise AssertionError("Commit is outside Phase 3 Draft Run tests.")


class RepairDecisionProvider(AgentDecisionProvider):
    def __init__(self) -> None:
        self.calls = 0

    def decide(self, context: BuilderContext, tools):
        del tools
        self.calls += 1
        if self.calls == 1:
            return _test_decision(context, requested_test_runs=2)
        if self.calls == 2:
            return ToolCallDecision(
                type="tool_call",
                tool_name="workflow.patch",
                arguments={
                    "workspace_version": context.workspace["version"],
                    "expected_base_hash": context.app["base_hash"],
                    "operations": [
                        {
                            "op": "node.update",
                            "node_id": "llm-1",
                            "set": {"params.system_prompt": "repaired"},
                            "expected": {
                                "params.system_prompt": "broken-variable-reference"
                            },
                        }
                    ],
                    "rationale": "Repair the execution variable reference failure.",
                },
                goal_step_id="repair",
            )
        if self.calls == 3:
            return _test_decision(context)
        return FinishDecision(
            type="finish",
            summary="Draft execution succeeded after one bounded repair.",
            evidence=["Second approved Draft Run succeeded."],
        )


class RepeatFailureProvider(AgentDecisionProvider):
    def __init__(self) -> None:
        self.calls = 0

    def decide(self, context: BuilderContext, tools):
        del tools
        self.calls += 1
        return _test_decision(
            context,
            requested_test_runs=3 if self.calls == 1 else 1,
        )


class StopThenFinishProvider(AgentDecisionProvider):
    def __init__(self) -> None:
        self.calls = 0

    def decide(self, context: BuilderContext, tools):
        del tools
        self.calls += 1
        if self.calls == 1:
            return _test_decision(context)
        return FinishDecision(
            type="finish",
            summary="User stopped automatic Draft testing.",
            evidence=["Workspace remains reviewable without a Draft Run."],
        )


class OneTestPerRunProvider(AgentDecisionProvider):
    def decide(self, context: BuilderContext, tools):
        del tools
        if context.latest_execution is None:
            return _test_decision(context, requested_test_runs=2)
        return FinishDecision(
            type="finish",
            summary="One approved Draft Run was observed.",
            evidence=["Draft execution observation is persisted."],
        )


class RecordingCandidateAdapter(DraftExecutionAdapter):
    supports_candidate_workspace = True

    def __init__(self, *, always_fail: bool = False) -> None:
        self.always_fail = always_fail
        self.runs: list[PreparedDraftTest] = []

    def run(
        self,
        prepared: PreparedDraftTest,
        *,
        progress_callback,
        cancellation_check,
    ) -> dict[str, Any]:
        cancellation_check()
        self.runs.append(prepared)
        llm = next(node for node in prepared.plan["nodes"] if node["id"] == "llm-1")
        repaired = str(llm["params"].get("system_prompt") or "").startswith(
            "repaired"
        )
        if self.always_fail or not repaired:
            progress_callback(
                {
                    "event": "node_finished",
                    "data": {
                        "node_id": "llm-1",
                        "node_type": "llm",
                        "status": "failed",
                        "error": (
                            "Variable reference does not exist for "
                            f"{prepared.generated.inputs['query']}"
                        ),
                        "inputs": {"query": prepared.generated.inputs["query"]},
                    },
                }
            )
            return {
                "ok": False,
                "status": "failed",
                "workflow_run_id": f"workflow-{len(self.runs)}",
                "error": (
                    "Variable reference does not exist for "
                    f"{prepared.generated.inputs['query']}"
                ),
                "events_summary": {
                    "events": 2,
                    "node_finished": 1,
                    "parse_errors": 0,
                },
            }
        progress_callback(
            {
                "event": "workflow_finished",
                "data": {"status": "succeeded", "outputs": {"answer": "ok"}},
            }
        )
        return {
            "ok": True,
            "status": "succeeded",
            "workflow_run_id": f"workflow-{len(self.runs)}",
            "outputs": {"answer": "ok"},
            "total_tokens": 12,
            "events_summary": {
                "events": 2,
                "node_finished": 1,
                "parse_errors": 0,
            },
        }


@dataclass
class Phase3Stack:
    store: AgentStore
    registry: ToolRegistry
    adapter: RecordingCandidateAdapter
    service: AgentApplicationService


def _stack(
    tmp_path,
    provider: AgentDecisionProvider,
    *,
    always_fail: bool = False,
) -> Phase3Stack:
    store = AgentStore(tmp_path / "agent-phase3.sqlite3")
    workspace = VersionedWorkflowWorkspace(
        store=store,
        validation=PassingValidation(),  # type: ignore[arg-type]
        catalog=NodeCapabilityCatalog(),
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
    adapter = RecordingCandidateAdapter(always_fail=always_fail)
    register_phase3_tools(
        registry,
        store=store,
        draft_runs=DraftRunService(store=store, adapter=adapter),
    )
    runtime = AgentRuntime(
        store=store,
        snapshot=StaticSnapshotService(_snapshot(_plan())),  # type: ignore[arg-type]
        workspace=workspace,
        review=review,
        approval=approval,
        registry=registry,
        context_builder=BuilderContextBuilder(store=store),
        decision_provider=provider,
        policy=AgentToolPolicy(
            store=store,
            supports_candidate_workspace=True,
        ),
    )
    service = AgentApplicationService(
        store=store,
        dispatcher=InlineRunDispatcher(runtime),
        approval=approval,
        commit_service=UnusedCommitService(),  # type: ignore[arg-type]
    )
    return Phase3Stack(
        store=store,
        registry=registry,
        adapter=adapter,
        service=service,
    )


def test_side_effect_classification_is_conservative_and_reviewable() -> None:
    plan = WorkflowPlan.model_validate(
        {
            "name": "Side effects",
            "app_mode": "workflow",
            "nodes": [
                {"id": "start", "type": "start"},
                {"id": "llm", "type": "llm"},
                {"id": "http", "type": "http-request"},
                {"id": "tool", "type": "tool"},
                {"id": "human", "type": "human-input"},
                {"id": "unknown", "type": "datasource-empty"},
                {"id": "end", "type": "end"},
            ],
            "edges": [
                {"source": "start", "target": "llm"},
                {"source": "llm", "target": "http"},
                {"source": "http", "target": "tool"},
                {"source": "tool", "target": "human"},
                {"source": "human", "target": "unknown"},
                {"source": "unknown", "target": "end"},
            ],
        }
    )
    summary = classify_plan_side_effects(plan)
    assert summary.highest_risk == "unknown"
    assert summary.counts == {
        "local": 2,
        "model_cost": 1,
        "http": 1,
        "tool": 1,
        "notification": 1,
        "unknown": 1,
    }
    assert summary.requires_per_run_approval is True
    assert summary.trigger_based is False
    trigger_plan = WorkflowPlan.model_validate(
        {
            "name": "Trigger",
            "app_mode": "workflow",
            "nodes": [
                {"id": "trigger", "type": "trigger-plugin"},
                {"id": "end", "type": "end"},
            ],
            "edges": [{"source": "trigger", "target": "end"}],
        }
    )
    assert classify_plan_side_effects(trigger_plan).trigger_based is True


def test_minimal_input_generator_covers_types_and_requires_user_files() -> None:
    plan = _plan(
        variables=[
            {"name": "short", "type": "text"},
            {"name": "long", "type": "paragraph"},
            {"name": "count", "type": "number", "min": 5},
            {"name": "enabled", "type": "boolean"},
            {
                "name": "payload",
                "type": "json",
                "schema": {
                    "type": "object",
                    "required": ["name"],
                    "properties": {"name": {"type": "string"}},
                },
            },
            {"name": "document", "type": "file"},
            {"name": "attachments", "type": "file-list"},
        ]
    )
    generated = MinimalTestInputGenerator().generate(plan)
    assert generated.inputs == {
        "short": "test",
        "long": "Test workflow input.",
        "count": 5,
        "enabled": True,
        "payload": {"name": "test"},
    }
    assert generated.missing_user_inputs == ["attachments", "document"]
    assert generated.file_input_names == ["attachments", "document"]
    overridden = MinimalTestInputGenerator().generate(
        plan,
        overrides={
            "document": {"upload_file_id": "file-1"},
            "attachments": [{"upload_file_id": "file-2"}],
        },
    )
    assert overridden.missing_user_inputs == []
    assert overridden.file_input_names == ["attachments", "document"]
    assert overridden.inputs["document"]["upload_file_id"] == "file-1"


def test_chatflow_generator_uses_sys_query_semantics() -> None:
    plan = _plan(mode="advanced-chat")
    generated = MinimalTestInputGenerator().generate(
        plan,
        query="How can I return this order?",
    )
    assert generated.query == "How can I return this order?"
    assert generated.inputs == {"query": "Test workflow input."}


@pytest.mark.parametrize(
    ("payload", "expected_status", "expected_code"),
    [
        (
            {"ok": True, "status": "succeeded", "outputs": {"answer": "ok"}},
            "succeeded",
            None,
        ),
        (
            {"ok": False, "status": "timeout", "error": "timed out"},
            "timeout",
            "DRAFT_RUN_TIMEOUT",
        ),
        (
            {"ok": False, "status": "cancelled", "error": "stopped"},
            "cancelled",
            "DRAFT_RUN_CANCELLED",
        ),
        (
            {
                "ok": False,
                "status": "error",
                "error": "stream ended",
                "events_summary": {"parse_errors": 2},
            },
            "failed",
            "DRAFT_RUN_STREAM_MALFORMED",
        ),
    ],
)
def test_execution_normalization_handles_terminal_outcomes(
    payload,
    expected_status,
    expected_code,
) -> None:
    observation = normalize_execution_result(payload)
    assert observation.status == expected_status
    assert observation.error_code == expected_code


def test_execution_normalization_identifies_failed_node_and_redacts_input() -> None:
    observation = normalize_execution_result(
        {
            "ok": False,
            "status": "failed",
            "error": "Variable reference missing for private-value",
        },
        progress=[
            {
                "event": "node_finished",
                "node_id": "llm-1",
                "node_type": "llm",
                "status": "failed",
                "error": "Variable reference missing for private-value",
                "input_summary": {"kind": "object", "keys": ["query"]},
            }
        ],
        input_values=["private-value"],
    )
    assert observation.failed_node_id == "llm-1"
    assert observation.failed_node_type == "llm"
    assert observation.error_code == "EXECUTION_VARIABLE_REFERENCE_INVALID"
    assert observation.message and "private-value" not in observation.message
    assert observation.upstream_summary["keys"] == ["query"]


def test_draft_tool_cannot_execute_without_approved_allowance(tmp_path) -> None:
    stack = _stack(tmp_path, StopThenFinishProvider())
    session = stack.service.create_session(app_id="app-1", app_mode="workflow")
    run = stack.service.submit_goal(
        session.id,
        message="Run the workflow.",
        constraints=RunConstraints(allow_draft_test=True),
    )
    pending = stack.store.get_run(run.id)
    assert pending.phase == RunPhase.WAITING_APPROVAL
    result = stack.registry.execute(
        "workflow.test_draft",
        {
            "workspace_version": pending.head_version_id,
            "inputs": {"query": "Test workflow input."},
        },
        session_id=session.id,
        run_id=run.id,
    )
    assert result.ok is False
    assert result.error and result.error.code == "DRAFT_RUN_APPROVAL_REQUIRED"
    assert stack.adapter.runs == []


def test_draft_approval_exposes_external_risk_and_forces_one_run(tmp_path) -> None:
    store = AgentStore(tmp_path / "external.sqlite3")
    workspace = VersionedWorkflowWorkspace(
        store=store,
        validation=PassingValidation(),  # type: ignore[arg-type]
        catalog=NodeCapabilityCatalog(),
    )
    session = store.create_session(
        _session()
    )
    run = store.create_run(
        _run(session.id, constraints=RunConstraints(allow_draft_test=True))
    )
    external_plan = _plan(extra_node={"id": "http-1", "type": "http-request"})
    initialized, _ = workspace.initialize(
        run,
        _snapshot(external_plan),
        _goal_plan(),
    )
    policy = AgentToolPolicy(store=store, supports_candidate_workspace=True)
    registry = ToolRegistry()
    register_phase3_tools(
        registry,
        store=store,
        draft_runs=DraftRunService(
            store=store,
            adapter=RecordingCandidateAdapter(),
        ),
    )
    spec = registry.get("workflow.test_draft").spec  # type: ignore[union-attr]
    authorization = policy.authorize(
        spec,
        initialized,
        {
            "workspace_version": initialized.head_version_id,
            "requested_test_runs": 3,
        },
    )
    assert authorization.requires_approval is True
    assert authorization.approval_scope["risk"] == "external"
    assert authorization.approval_scope["per_run"] is True
    assert authorization.approval_scope["allowed_test_runs"] == 1


def test_non_external_draft_allowance_is_reused_within_session(tmp_path) -> None:
    stack = _stack(tmp_path, OneTestPerRunProvider(), always_fail=True)
    session = stack.service.create_session(app_id="app-1", app_mode="workflow")
    first = stack.service.submit_goal(
        session.id,
        message="Run one Draft test.",
        constraints=RunConstraints(allow_draft_test=True),
    )
    approval = next(
        item
        for item in stack.store.list_approvals(first.id)
        if item.action == "draft_run"
    )
    stack.service.resolve_approval(
        first.id,
        approval.id,
        approved=True,
        allowed_test_runs=2,
    )
    assert stack.store.get_approval(approval.id).scope["remaining_test_runs"] == 1
    second = stack.service.submit_goal(
        session.id,
        message="Run one more Draft test under the Session allowance.",
        constraints=RunConstraints(allow_draft_test=True),
    )
    second_run = stack.store.get_run(second.id)
    assert second_run.phase == RunPhase.WAITING_APPROVAL
    assert len(stack.adapter.runs) == 2
    assert not any(
        item.action == "draft_run"
        for item in stack.store.list_approvals(second.id)
    )
    assert stack.store.get_approval(approval.id).status == ApprovalStatus.CONSUMED


def test_repair_loop_uses_patch_validation_and_second_approved_run(tmp_path) -> None:
    provider = RepairDecisionProvider()
    stack = _stack(tmp_path, provider)
    session = stack.service.create_session(app_id="app-1", app_mode="workflow")
    run = stack.service.submit_goal(
        session.id,
        message="Run the workflow and repair the variable reference until it succeeds.",
        constraints=RunConstraints(allow_draft_test=True),
    )
    waiting = stack.store.get_run(run.id)
    assert waiting.phase == RunPhase.WAITING_APPROVAL
    draft_approval = next(
        approval
        for approval in stack.store.list_approvals(run.id)
        if approval.action == "draft_run"
    )
    stack.service.resolve_approval(
        run.id,
        draft_approval.id,
        approved=True,
        allowed_test_runs=2,
    )
    reviewed = stack.store.get_run(run.id)
    assert reviewed.phase == RunPhase.WAITING_APPROVAL
    assert len(stack.adapter.runs) == 2
    versions = stack.store.list_workspace_versions(run.id)
    assert len(versions) == 2
    assert versions[-1].patch["operations"][0]["op"] == "node.update"
    assert versions[-1].test_result["execution"]["status"] == "succeeded"
    inspected = stack.registry.execute(
        "execution.inspect",
        {"workspace_version": reviewed.head_version_id},
        session_id=session.id,
        run_id=run.id,
    )
    assert inspected.ok is True
    assert inspected.observation["execution"]["status"] == "succeeded"
    assert reviewed.budget_usage.test_runs == 2
    event_types = [event.type for event in stack.store.list_events(run.id)]
    assert "repair.started" in event_types
    assert event_types.count("test.started") == 2
    assert event_types.count("test.completed") == 2
    execution_events = json.dumps(
        [
            event.model_dump(mode="json")
            for event in stack.store.list_events(run.id)
            if event.type in {"test.progress", "test.completed"}
        ]
    )
    assert "Variable reference does not exist for Test workflow input." not in (
        execution_events
    )
    assert any(
        approval.action == "commit"
        for approval in stack.store.list_approvals(run.id)
    )


def test_same_execution_error_stops_after_configured_retries_with_partial_review(
    tmp_path,
) -> None:
    provider = RepeatFailureProvider()
    stack = _stack(tmp_path, provider, always_fail=True)
    session = stack.service.create_session(app_id="app-1", app_mode="workflow")
    run = stack.service.submit_goal(
        session.id,
        message="Run and repair.",
        constraints=RunConstraints(allow_draft_test=True),
        budget=AgentBudget(
            max_test_runs=3,
            max_same_error_retries=2,
            max_iterations=8,
            max_model_calls=8,
        ),
    )
    approval = next(
        approval
        for approval in stack.store.list_approvals(run.id)
        if approval.action == "draft_run"
    )
    stack.service.resolve_approval(
        run.id,
        approval.id,
        approved=True,
        allowed_test_runs=3,
    )
    failed = stack.store.get_run(run.id)
    assert failed.phase == RunPhase.FAILED
    assert failed.error["code"] == "AGENT_BUDGET_EXHAUSTED"
    assert failed.error["reason"] == "max_same_error_retries"
    assert failed.error["partial_review"]["workspace_version_id"]
    assert failed.error["attempts"]["test_runs"] == 3
    assert len(stack.adapter.runs) == 3


def test_draft_token_cost_budget_returns_partial_review(tmp_path) -> None:
    stack = _stack(tmp_path, RepairDecisionProvider())
    session = stack.service.create_session(app_id="app-1", app_mode="workflow")
    run = stack.service.submit_goal(
        session.id,
        message="Run and repair within a strict test-token budget.",
        constraints=RunConstraints(allow_draft_test=True),
        budget=AgentBudget(max_test_runs=2, max_test_total_tokens=10),
    )
    approval = next(
        approval
        for approval in stack.store.list_approvals(run.id)
        if approval.action == "draft_run"
    )
    stack.service.resolve_approval(
        run.id,
        approval.id,
        approved=True,
        allowed_test_runs=2,
    )
    failed = stack.store.get_run(run.id)
    assert failed.phase == RunPhase.FAILED
    assert failed.error["reason"] == "max_test_total_tokens"
    assert failed.budget_usage.test_total_tokens == 12
    assert failed.error["partial_review"]["workspace_version_id"]


def test_rejecting_draft_approval_stops_testing_without_losing_workspace(
    tmp_path,
) -> None:
    stack = _stack(tmp_path, StopThenFinishProvider())
    session = stack.service.create_session(app_id="app-1", app_mode="workflow")
    run = stack.service.submit_goal(
        session.id,
        message="Test if allowed, otherwise keep the review.",
        constraints=RunConstraints(allow_draft_test=True),
    )
    approval = next(
        approval
        for approval in stack.store.list_approvals(run.id)
        if approval.action == "draft_run"
    )
    stack.service.resolve_approval(run.id, approval.id, approved=False)
    reviewed = stack.store.get_run(run.id)
    assert reviewed.phase == RunPhase.WAITING_APPROVAL
    assert reviewed.head_version_id is not None
    assert reviewed.constraints.allow_draft_test is False
    assert stack.adapter.runs == []
    assert stack.store.get_approval(approval.id).status == ApprovalStatus.REJECTED


def test_sensitive_approval_override_is_rejected_before_persistence(tmp_path) -> None:
    stack = _stack(tmp_path, StopThenFinishProvider())
    session = stack.service.create_session(app_id="app-1", app_mode="workflow")
    run = stack.service.submit_goal(
        session.id,
        message="Run the workflow.",
        constraints=RunConstraints(allow_draft_test=True),
    )
    approval = next(
        approval
        for approval in stack.store.list_approvals(run.id)
        if approval.action == "draft_run"
    )
    with pytest.raises(
        ApprovalServiceError,
        match="Sensitive test-input values",
    ):
        stack.service.resolve_approval(
            run.id,
            approval.id,
            approved=True,
            test_inputs={"api_key": "never-persist-this"},
        )
    stored = json.dumps(stack.store.get_approval(approval.id).model_dump(mode="json"))
    assert "never-persist-this" not in stored
    assert stack.adapter.runs == []


def test_provider_context_budget_is_enforced_before_model_call(tmp_path) -> None:
    provider = StopThenFinishProvider()
    stack = _stack(tmp_path, provider)
    session = stack.service.create_session(app_id="app-1", app_mode="workflow")
    run = stack.service.submit_goal(
        session.id,
        message="Inspect without exceeding the provider context budget.",
        budget=AgentBudget(max_context_tokens=256),
    )
    failed = stack.store.get_run(run.id)
    assert failed.phase == RunPhase.FAILED
    assert failed.error["reason"] == "max_context_tokens"
    assert provider.calls == 0


def _test_decision(
    context: BuilderContext,
    *,
    requested_test_runs: int = 1,
) -> ToolCallDecision:
    return ToolCallDecision(
        type="tool_call",
        tool_name="workflow.test_draft",
        arguments={
            "workspace_version": context.workspace["version"],
            "requested_test_runs": requested_test_runs,
        },
        goal_step_id="test",
    )


def _plan(
    *,
    mode: str = "workflow",
    variables: list[dict[str, Any]] | None = None,
    extra_node: dict[str, Any] | None = None,
) -> WorkflowPlan:
    terminal = (
        {
            "id": "answer",
            "type": "answer",
            "params": {"answer": "{{#llm-1.text#}}"},
        }
        if mode == "advanced-chat"
        else {
            "id": "end",
            "type": "end",
            "params": {
                "outputs": [
                    {
                        "variable": "answer",
                        "value_selector": ["llm-1", "text"],
                    }
                ]
            },
        }
    )
    nodes = [
        {
            "id": "start",
            "type": "start",
            "params": {
                "variables": variables
                if variables is not None
                else [{"name": "query", "type": "paragraph"}]
            },
        },
        {
            "id": "llm-1",
            "type": "llm",
            "params": {
                "system_prompt": "broken-variable-reference",
                "user_prompt": (
                    "{{#sys.query#}}"
                    if mode == "advanced-chat"
                    else "{{#start.query#}}"
                ),
            },
        },
    ]
    edges = [{"source": "start", "target": "llm-1"}]
    if extra_node is not None:
        nodes.append(extra_node)
        edges.append({"source": "llm-1", "target": extra_node["id"]})
        edges.append({"source": extra_node["id"], "target": terminal["id"]})
    else:
        edges.append({"source": "llm-1", "target": terminal["id"]})
    nodes.append(terminal)
    return WorkflowPlan.model_validate(
        {
            "name": "Phase 3",
            "app_mode": mode,
            "nodes": nodes,
            "edges": edges,
        }
    )


def _snapshot(plan: WorkflowPlan) -> AgentWorkflowSnapshot:
    return AgentWorkflowSnapshot(
        app_id="app-1",
        app_name=plan.name,
        app_mode=plan.app_mode,
        base_hash="hash-v0",
        base_plan=plan.model_dump(mode="json"),
        base_graph={},
        capabilities=[],
    )


def _session():
    from app.agent.state import AgentSession

    return AgentSession(app_id="app-1", app_mode="workflow")


def _run(session_id: str, *, constraints: RunConstraints):
    from app.agent.state import AgentRun

    return AgentRun(
        session_id=session_id,
        goal="Test the draft.",
        constraints=constraints,
    )


def _goal_plan():
    from app.agent.state import GoalPlan, GoalStep

    return GoalPlan(
        goal="Test the draft.",
        success_criteria=["Draft observation is available."],
        steps=[GoalStep(id="test", description="Test the Draft.")],
    )
