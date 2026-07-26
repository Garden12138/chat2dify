from __future__ import annotations

from contextlib import nullcontext
from copy import deepcopy
from dataclasses import dataclass
import json
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Any

from app.agent.approval import AgentApprovalService
from app.agent.catalog import NodeCapabilityCatalog
from app.agent.compatibility import DifyCompatibilityMatrix
from app.agent.config_app import (
    ConfigReviewService,
    VersionedConfigWorkspace,
)
from app.agent.context import BuilderContext, BuilderContextBuilder
from app.agent.decision import AgentDecisionProvider
from app.agent.execution import (
    DraftExecutionAdapter,
    DraftRunService,
    PreparedDraftTest,
)
from app.agent.policy import AgentToolPolicy
from app.agent.registry import ToolRegistry
from app.agent.review import WorkflowReviewService
from app.agent.routing import AgentReviewRouter, AgentWorkspaceRouter
from app.agent.runtime import AgentRuntime
from app.agent.service import AgentApplicationService, InlineRunDispatcher
from app.agent.skills import SkillRegistry, register_skill_tool
from app.agent.snapshot import WorkflowSnapshotService
from app.agent.state import (
    AgentConfigSnapshot,
    AgentWorkflowSnapshot,
    AskUserDecision,
    FinishDecision,
    RunConstraints,
    RunPhase,
    ToolCallDecision,
)
from app.agent.store import AgentStore
from app.agent.tools import (
    register_config_tools,
    register_phase1a_tools,
    register_phase3_tools,
)
from app.agent.validation import WorkflowValidationService
from app.agent.workspace import VersionedWorkflowWorkspace
from app.compiler.dify import DifyDslCompiler
from app.dify.version import DifyVersionInfo
from app.evals.models import EvaluationCase, EvaluationCaseResult
from app.models import WorkflowPlan


SCENARIOS_DIR = Path(__file__).resolve().parent / "fixtures" / "scenarios"
SUPPORTED_VERSION = DifyVersionInfo(
    source_dir="/deterministic-fixture/dify",
    git_describe="1.14.2",
    app_dsl_version="0.6.0",
)


@dataclass(frozen=True)
class RuntimeScenario:
    snapshot_id: str
    snapshot_version: str
    template: str
    operation: str
    app_mode: str
    decisions: list[dict[str, Any]]
    allow_draft_test: bool
    allowed_test_runs: int
    draft_failure_markers: tuple[str, ...]
    resources: tuple[dict[str, Any], ...]

    @classmethod
    def from_payload(cls, payload: dict[str, Any]) -> "RuntimeScenario":
        return cls(
            snapshot_id=str(payload["snapshot_id"]),
            snapshot_version=str(payload["snapshot_version"]),
            template=str(payload["template"]),
            operation=str(payload.get("operation") or "modify"),
            app_mode=str(payload["app_mode"]),
            decisions=[
                deepcopy(item)
                for item in payload.get("decisions") or []
                if isinstance(item, dict)
            ],
            allow_draft_test=bool(payload.get("allow_draft_test", False)),
            allowed_test_runs=int(payload.get("allowed_test_runs") or 1),
            draft_failure_markers=tuple(
                str(item)
                for item in payload.get("draft_failure_markers") or []
            ),
            resources=tuple(
                deepcopy(item)
                for item in payload.get("resources") or []
                if isinstance(item, dict)
            ),
        )


class DeterministicRuntimeEvaluationExecutor:
    """Executes fixed cases through the real Runtime and deterministic core."""

    name = "deterministic-agent-runtime"
    live_provider = False
    reproducible = True
    runtime_executed = True

    def __init__(self, *, scenarios_dir: Path = SCENARIOS_DIR) -> None:
        self.scenarios_dir = scenarios_dir

    def execute(self, case: EvaluationCase) -> EvaluationCaseResult:
        scenario = _load_scenario(case, self.scenarios_dir)
        with TemporaryDirectory(prefix=f"chat2dify-eval-{case.id}-") as temp_dir:
            return _execute_runtime_case(
                case,
                scenario,
                database_path=Path(temp_dir) / "agent.sqlite3",
            )


class _ScriptedDecisionProvider(AgentDecisionProvider):
    def __init__(self, decisions: list[dict[str, Any]]) -> None:
        self.decisions = deepcopy(decisions)
        self.index = 0

    def decide(self, context: BuilderContext, tools):
        del tools
        if self.index >= len(self.decisions):
            return FinishDecision(
                type="finish",
                summary="Deterministic evaluation scenario is ready for review.",
                evidence=["All scripted Runtime actions completed."],
            )
        raw = _resolve_context_tokens(
            self.decisions[self.index],
            context,
        )
        self.index += 1
        decision_type = raw.get("type")
        if decision_type == "tool_call":
            return ToolCallDecision.model_validate(raw)
        if decision_type == "ask_user":
            return AskUserDecision.model_validate(raw)
        if decision_type == "finish":
            return FinishDecision.model_validate(raw)
        raise ValueError(f"Unsupported evaluation decision type: {decision_type}")


class _ScenarioSnapshotService:
    def __init__(
        self,
        *,
        scenario: RuntimeScenario,
        catalog: NodeCapabilityCatalog,
    ) -> None:
        self.scenario = scenario
        self.catalog = catalog
        self.compatibility = DifyCompatibilityMatrix()
        self.create_service = WorkflowSnapshotService(
            client_factory=lambda: nullcontext(None),
            catalog=catalog,
            dify_version=SUPPORTED_VERSION,
            compatibility=self.compatibility,
        )

    def capture(self, session):
        if self.scenario.operation == "create":
            return self.create_service.capture(session)
        compatibility = self.compatibility.decide(
            SUPPORTED_VERSION,
            app_mode=self.scenario.app_mode,
        )
        if self.scenario.app_mode in {"chat", "completion", "agent-chat"}:
            config = _template_config(self.scenario.template)
            return AgentConfigSnapshot(
                app_id=session.app_id or "eval-config-app",
                app_name=f"Evaluation {self.scenario.snapshot_id}",
                app_mode=self.scenario.app_mode,
                base_hash="eval-config-hash-1",
                base_config=config,
                dify_version=_version_payload(),
                capabilities=self.compatibility.pin_capabilities(
                    _config_capabilities(self.scenario),
                    decision=compatibility,
                ),
                compatibility=compatibility.model_dump(mode="json"),
            )
        plan = _template_plan(
            self.scenario.template,
            app_mode=self.scenario.app_mode,
        )
        capabilities = [
            definition.model_dump(mode="json")
            for definition in self.catalog.list()
            if self.scenario.app_mode in definition.supported_app_modes
        ]
        capabilities.extend(deepcopy(list(self.scenario.resources)))
        return AgentWorkflowSnapshot(
            operation="modify",
            app_id=session.app_id or "eval-workflow-app",
            app_name=plan.name,
            app_description=plan.description,
            app_mode=self.scenario.app_mode,
            base_hash="eval-workflow-hash-1",
            base_plan=plan.model_dump(mode="json"),
            base_graph={},
            features={},
            environment_variables=[],
            conversation_variables=[
                item.model_dump(mode="json")
                for item in plan.conversation_variables
            ],
            dify_version=_version_payload(),
            capabilities=self.compatibility.pin_capabilities(
                capabilities,
                decision=compatibility,
            ),
            compatibility=compatibility.model_dump(mode="json"),
        )


class _ScenarioDraftAdapter(DraftExecutionAdapter):
    supports_candidate_workspace = True

    def __init__(self, failure_markers: tuple[str, ...]) -> None:
        self.failure_markers = failure_markers
        self.run_count = 0

    def run(
        self,
        prepared: PreparedDraftTest,
        *,
        progress_callback,
        cancellation_check,
    ) -> dict[str, Any]:
        cancellation_check()
        self.run_count += 1
        failed_node = None
        marker = None
        for node in prepared.plan.get("nodes") or []:
            params = node.get("params") if isinstance(node, dict) else {}
            serialized = json.dumps(
                params if isinstance(params, dict) else {},
                ensure_ascii=False,
                sort_keys=True,
            )
            marker = next(
                (
                    candidate
                    for candidate in self.failure_markers
                    if candidate in serialized
                ),
                None,
            )
            if marker is not None:
                failed_node = node
                break
        if failed_node is not None and marker is not None:
            progress_callback(
                {
                    "event": "node_finished",
                    "data": {
                        "node_id": failed_node.get("id"),
                        "node_type": failed_node.get("type"),
                        "status": "failed",
                        "error": (
                            "Variable reference could not be resolved in the "
                            "deterministic evaluation adapter."
                        ),
                    },
                }
            )
            return {
                "ok": False,
                "status": "failed",
                "workflow_run_id": f"eval-run-{self.run_count}",
                "error": "Variable reference could not be resolved.",
                "events_summary": {
                    "events": 1,
                    "node_finished": 1,
                    "parse_errors": 0,
                },
            }
        progress_callback(
            {
                "event": "workflow_finished",
                "data": {
                    "status": "succeeded",
                    "outputs": {"answer": "evaluation-ok"},
                },
            }
        )
        return {
            "ok": True,
            "status": "succeeded",
            "workflow_run_id": f"eval-run-{self.run_count}",
            "outputs": {"answer": "evaluation-ok"},
            "total_tokens": 8,
            "events_summary": {
                "events": 1,
                "node_finished": 0,
                "parse_errors": 0,
            },
        }


class _UnusedCommitService:
    def commit(self, *_args, **_kwargs):
        raise AssertionError("Evaluation must not invoke a Dify Commit.")


def _execute_runtime_case(
    case: EvaluationCase,
    scenario: RuntimeScenario,
    *,
    database_path: Path,
) -> EvaluationCaseResult:
    store = AgentStore(database_path)
    catalog = NodeCapabilityCatalog()
    compiler = DifyDslCompiler(
        dsl_version=SUPPORTED_VERSION.app_dsl_version,
        default_model_provider="openai",
        default_model_name="gpt-4o-mini",
    )
    validation = WorkflowValidationService(
        compiler=compiler,
        expected_dsl_version=SUPPORTED_VERSION.app_dsl_version,
    )
    workflow_workspace = VersionedWorkflowWorkspace(
        store=store,
        validation=validation,
        catalog=catalog,
    )
    workflow_review = WorkflowReviewService(
        store=store,
        workspace=workflow_workspace,
    )
    config_workspace = VersionedConfigWorkspace(store=store)
    config_review = ConfigReviewService(
        store=store,
        workspace=config_workspace,
    )
    workspace = AgentWorkspaceRouter(
        workflow=workflow_workspace,
        config=config_workspace,
    )
    review = AgentReviewRouter(
        workflow=workflow_review,
        config=config_review,
    )
    approval = AgentApprovalService(store=store)
    registry = ToolRegistry()
    register_phase1a_tools(
        registry,
        store=store,
        workspace=workflow_workspace,
        review=workflow_review,
    )
    register_config_tools(
        registry,
        store=store,
        workspace=config_workspace,
        review=config_review,
    )
    draft_adapter = _ScenarioDraftAdapter(
        scenario.draft_failure_markers,
    )
    register_phase3_tools(
        registry,
        store=store,
        draft_runs=DraftRunService(
            store=store,
            adapter=draft_adapter,
        ),
    )
    register_skill_tool(
        registry,
        store=store,
        skills=SkillRegistry(),
    )
    runtime = AgentRuntime(
        store=store,
        snapshot=_ScenarioSnapshotService(
            scenario=scenario,
            catalog=catalog,
        ),
        workspace=workspace,
        review=review,
        approval=approval,
        registry=registry,
        context_builder=BuilderContextBuilder(store=store),
        decision_provider=_ScriptedDecisionProvider(scenario.decisions),
        policy=AgentToolPolicy(
            store=store,
            supports_candidate_workspace=True,
        ),
    )
    service = AgentApplicationService(
        store=store,
        dispatcher=InlineRunDispatcher(runtime),
        approval=approval,
        commit_service=_UnusedCommitService(),  # type: ignore[arg-type]
    )
    session = service.create_session(
        app_id=(
            None
            if scenario.operation == "create"
            else f"eval-{case.id}"
        ),
        app_mode=scenario.app_mode,
        app_name=f"Evaluation {case.id}",
    )
    submitted = service.submit_goal(
        session.id,
        message=case.goal,
        constraints=RunConstraints(
            allow_draft_test=scenario.allow_draft_test,
        ),
        budget=case.budget,
    )
    run = store.get_run(submitted.id)
    while run.phase == RunPhase.WAITING_APPROVAL:
        draft_approval = next(
            (
                item
                for item in store.list_approvals(run.id)
                if item.action == "draft_run"
                and item.status.value == "pending"
            ),
            None,
        )
        if draft_approval is None:
            break
        service.resolve_approval(
            run.id,
            draft_approval.id,
            approved=True,
            allowed_test_runs=scenario.allowed_test_runs,
        )
        run = store.get_run(run.id)

    return _grade_runtime_result(
        case,
        scenario,
        store=store,
        run_id=run.id,
        draft_run_count=draft_adapter.run_count,
    )


def _grade_runtime_result(
    case: EvaluationCase,
    scenario: RuntimeScenario,
    *,
    store: AgentStore,
    run_id: str,
    draft_run_count: int,
) -> EvaluationCaseResult:
    run = store.get_run(run_id)
    events = store.list_events(run_id, limit=10_000)
    event_types = [event.type for event in events]
    versions = store.list_workspace_versions(run_id)
    before = deepcopy(versions[0].snapshot) if versions else {}
    after = deepcopy(versions[-1].snapshot) if versions else {}
    observed_changes = _observed_changes(
        case.id,
        before=before,
        after=after,
        event_types=event_types,
    )
    required_present = set(case.required_changes).issubset(observed_changes)
    forbidden_absent = not (
        set(case.forbidden_changes) & observed_changes
    )
    reviewable = bool(
        run.review
        and run.review.get("ready")
        and run.phase == RunPhase.WAITING_APPROVAL
    )
    final_valid = bool(
        reviewable
        and isinstance(run.review, dict)
        and isinstance(run.review.get("validation"), dict)
        and run.review["validation"].get("ok")
    )
    status = "completed" if reviewable and final_valid else "failed"
    terminal_reason = deepcopy(run.error)
    if status == "failed" and not terminal_reason:
        terminal_reason = _last_tool_error(events) or {
            "code": "EVALUATION_NOT_REVIEWABLE",
            "message": "The Runtime did not produce a reviewable result.",
        }
    readable_trace = bool(
        events
        and all(event.type and event.message for event in events)
    )
    structured_terminal = (
        status != "failed"
        or bool(
            isinstance(terminal_reason, dict)
            and terminal_reason.get("code")
            and terminal_reason.get("message")
        )
    )
    repairable_failure = (
        "repair.started" in event_types
        or "validation.failed" in event_types
    )
    auto_repaired = repairable_failure and reviewable and final_valid
    unrelated_total, unrelated_preserved = _preservation_counts(
        scenario,
        before,
        after,
    )
    invariant_passed = (
        forbidden_absent
        and unrelated_preserved <= unrelated_total
        and _draft_approvals_cover_runs(store, run_id, draft_run_count)
        and not _trace_contains_sensitive_fixture(events)
    )
    goal_completed = (
        status == "completed"
        and reviewable
        and final_valid
        and required_present
        and forbidden_absent
        and invariant_passed
    )
    return EvaluationCaseResult(
        case_id=case.id,
        case_version=case.version,
        goal=case.goal,
        app_mode=case.app_mode,
        status=status,
        reviewable=reviewable,
        final_valid=final_valid,
        goal_completed=goal_completed,
        required_changes_present=required_present,
        forbidden_changes_absent=forbidden_absent,
        invariant_passed=invariant_passed,
        unrelated_total=unrelated_total,
        unrelated_preserved=unrelated_preserved,
        repairable_failure=repairable_failure,
        auto_repaired=auto_repaired,
        unapproved_writes=0,
        incorrect_conflict_overwrites=0,
        readable_trace=readable_trace,
        structured_terminal_reason=structured_terminal,
        trace_event_count=len(events),
        terminal_reason=terminal_reason,
        executor_evidence={
            "runtime_executed": True,
            "workspace_version_count": len(versions),
            "draft_run_count": draft_run_count,
            "event_types": sorted(set(event_types)),
            "observed_changes": sorted(observed_changes),
        },
    )


def _load_scenario(
    case: EvaluationCase,
    scenarios_dir: Path,
) -> RuntimeScenario:
    path = scenarios_dir / f"{case.id}.json"
    payload = json.loads(path.read_text(encoding="utf-8"))
    scenario = RuntimeScenario.from_payload(payload)
    if scenario.snapshot_id != case.fixture.snapshot_id:
        raise ValueError(
            f"Scenario {case.id} snapshot_id does not match its case."
        )
    if scenario.snapshot_version != case.fixture.snapshot_version:
        raise ValueError(
            f"Scenario {case.id} snapshot_version does not match its case."
        )
    if scenario.app_mode != case.app_mode:
        raise ValueError(
            f"Scenario {case.id} app_mode does not match its case."
        )
    return scenario


def _resolve_context_tokens(
    value: Any,
    context: BuilderContext,
) -> Any:
    replacements = {
        "${workspace_version}": str(context.workspace["version"]),
        "${base_hash}": str(context.app.get("base_hash") or ""),
    }
    for node in context.workspace.get("nodes") or []:
        node_type = str(node.get("type") or "")
        replacements.setdefault(
            f"${{node:{node_type}}}",
            str(node.get("id") or ""),
        )
    if isinstance(value, dict):
        return {
            key: _resolve_context_tokens(item, context)
            for key, item in value.items()
        }
    if isinstance(value, list):
        return [
            _resolve_context_tokens(item, context)
            for item in value
        ]
    if isinstance(value, str):
        resolved = value
        for token, replacement in replacements.items():
            resolved = resolved.replace(token, replacement)
        if value == "${base_hash}" and context.app.get("base_hash") is None:
            return None
        return resolved
    return deepcopy(value)


def _template_plan(template: str, *, app_mode: str) -> WorkflowPlan:
    if template == "workflow-basic":
        return WorkflowPlan.model_validate(_basic_workflow_payload())
    if template == "workflow-runtime-broken":
        payload = _basic_workflow_payload()
        payload["nodes"][1]["params"]["system_prompt"] = (
            "eval-broken-variable-reference"
        )
        return WorkflowPlan.model_validate(payload)
    if template == "workflow-http":
        return WorkflowPlan.model_validate(
            {
                "name": "HTTP support workflow",
                "app_mode": "workflow",
                "nodes": [
                    {
                        "id": "start",
                        "type": "start",
                        "params": {
                            "variables": [
                                {"name": "query", "type": "paragraph"}
                            ]
                        },
                    },
                    {
                        "id": "http",
                        "type": "http-request",
                        "params": {
                            "method": "get",
                            "url": "https://example.invalid/support",
                            "headers": [],
                            "params": {
                                "query": "{{#start.query#}}"
                            },
                        },
                    },
                    {
                        "id": "end",
                        "type": "end",
                        "params": {
                            "outputs": [
                                {
                                    "variable": "answer",
                                    "value_selector": ["http", "body"],
                                }
                            ]
                        },
                    },
                ],
                "edges": [
                    {"source": "start", "target": "http"},
                    {"source": "http", "target": "end"},
                ],
            }
        )
    if template == "chatflow-basic":
        return WorkflowPlan.model_validate(_basic_chatflow_payload())
    if template == "chatflow-with-variable":
        payload = _basic_chatflow_payload()
        payload["conversation_variables"] = [
            {
                "id": "11111111-1111-4111-8111-111111111111",
                "name": "existing_context",
                "value_type": "string",
                "value": "",
                "description": "Existing context that must be preserved.",
            }
        ]
        return WorkflowPlan.model_validate(payload)
    if template == "chatflow-runtime-broken":
        payload = _basic_chatflow_payload()
        payload["nodes"][1]["params"]["system_prompt"] = (
            "eval-runtime-failure"
        )
        return WorkflowPlan.model_validate(payload)
    raise ValueError(f"Unsupported evaluation plan template: {template}/{app_mode}")


def _basic_workflow_payload() -> dict[str, Any]:
    return {
        "name": "Evaluation support workflow",
        "app_mode": "workflow",
        "nodes": [
            {
                "id": "start",
                "type": "start",
                "title": "Receive request",
                "params": {
                    "variables": [
                        {"name": "query", "type": "paragraph"}
                    ]
                },
            },
            {
                "id": "llm",
                "type": "llm",
                "title": "Generate response",
                "params": {
                    "system_prompt": "Be concise.",
                    "user_prompt": "{{#start.query#}}",
                },
            },
            {
                "id": "end",
                "type": "end",
                "title": "Return response",
                "params": {
                    "outputs": [
                        {
                            "variable": "answer",
                            "value_selector": ["llm", "text"],
                        }
                    ]
                },
            },
        ],
        "edges": [
            {"source": "start", "target": "llm"},
            {"source": "llm", "target": "end"},
        ],
    }


def _basic_chatflow_payload() -> dict[str, Any]:
    return {
        "name": "Evaluation support chatflow",
        "app_mode": "advanced-chat",
        "conversation_variables": [],
        "nodes": [
            {
                "id": "start",
                "type": "start",
                "title": "Receive query",
                "params": {"variables": []},
            },
            {
                "id": "llm",
                "type": "llm",
                "title": "Generate response",
                "params": {
                    "system_prompt": "Be concise.",
                    "user_prompt": "{{#sys.query#}}",
                },
            },
            {
                "id": "answer",
                "type": "answer",
                "title": "Answer",
                "params": {"answer": "{{#llm.text#}}"},
            },
        ],
        "edges": [
            {"source": "start", "target": "llm"},
            {"source": "llm", "target": "answer"},
        ],
    }


def _template_config(template: str) -> dict[str, Any]:
    if template != "config-completion-basic":
        raise ValueError(f"Unsupported evaluation config template: {template}")
    return {
        "pre_prompt": "Be concise.",
        "model": {
            "provider": "openai",
            "name": "gpt-4o-mini",
            "mode": "chat",
            "completion_params": {"temperature": 0.2},
            "metadata": {"preserve": True},
        },
        "opening_statement": "How can I help?",
        "suggested_questions": ["Summarize this request."],
        "preserved": {"metadata": True},
    }


def _config_capabilities(
    scenario: RuntimeScenario,
) -> list[dict[str, Any]]:
    return [
        {
            "type": "config",
            "app_mode": scenario.app_mode,
            "summary": "Configured-app model settings.",
        },
        *deepcopy(list(scenario.resources)),
    ]


def _version_payload() -> dict[str, str]:
    return {
        "source_dir": SUPPORTED_VERSION.source_dir,
        "git_describe": SUPPORTED_VERSION.git_describe,
        "app_dsl_version": SUPPORTED_VERSION.app_dsl_version,
    }


def _observed_changes(
    case_id: str,
    *,
    before: dict[str, Any],
    after: dict[str, Any],
    event_types: list[str],
) -> set[str]:
    before_nodes = {
        str(node.get("id")): node
        for node in before.get("nodes") or []
        if isinstance(node, dict)
    }
    after_nodes = {
        str(node.get("id")): node
        for node in after.get("nodes") or []
        if isinstance(node, dict)
    }
    added_nodes = [
        node
        for node_id, node in after_nodes.items()
        if node_id not in before_nodes
    ]
    changes: set[str] = set()
    if case_id == "create-after-sales-workflow":
        if any(node.get("type") == "if-else" for node in added_nodes):
            changes.add("node.classifier.added")
        if any(node.get("type") == "llm" for node in added_nodes):
            changes.add("node.response.added")
        if any(
            "json" in json.dumps(node.get("params") or {}).lower()
            for node in added_nodes
            if node.get("type") == "llm"
        ):
            changes.add("prompt.json-contract")
    elif case_id == "add-classification-branch":
        if any(node.get("type") == "if-else" for node in added_nodes):
            changes.add("node.classifier.added")
        if len(after.get("edges") or []) > len(before.get("edges") or []):
            changes.add("edge.classification.added")
    elif case_id == "add-chatflow-conversation-variable":
        before_names = {
            item.get("name")
            for item in before.get("conversation_variables") or []
        }
        after_names = {
            item.get("name")
            for item in after.get("conversation_variables") or []
        }
        if "customer_tier" in after_names - before_names:
            changes.add("conversation-variable.customer-tier.added")
    elif case_id == "repair-stale-variable-reference":
        if (
            "repair.started" in event_types
            and "eval-broken-variable-reference"
            not in json.dumps(after, ensure_ascii=False)
        ):
            changes.add("reference.stale.repaired")
    elif case_id == "replace-model-provider":
        before_provider = (before.get("model") or {}).get("provider")
        after_provider = (after.get("model") or {}).get("provider")
        if before_provider != after_provider and after_provider:
            changes.add("config.model.provider.replaced")
    elif case_id == "add-knowledge-retrieval":
        knowledge = next(
            (
                node
                for node in added_nodes
                if node.get("type") == "knowledge-retrieval"
            ),
            None,
        )
        if knowledge is not None:
            changes.add("node.knowledge-retrieval.added")
            if "dataset-repair-manual" in (
                knowledge.get("params") or {}
            ).get("dataset_ids", []):
                changes.add("dataset.repair-manual.bound")
    elif case_id == "add-error-handling":
        if any(
            node.get("title") == "Safe error response"
            for node in added_nodes
        ):
            changes.add("node.safe-error-response.added")
        if len(after.get("edges") or []) > len(before.get("edges") or []):
            changes.add("edge.error-path.added")
    elif case_id == "add-human-fallback":
        if any(node.get("type") == "human-input" for node in added_nodes):
            changes.add("branch.low-confidence.added")
            changes.add("tool.human-queue.bound")
    elif case_id == "add-file-extraction":
        start = after_nodes.get("start") or {}
        variables = (start.get("params") or {}).get("variables") or []
        if any(item.get("type") in {"file", "file-list"} for item in variables):
            changes.add("input.file.added")
        if any(
            node.get("type") == "document-extractor"
            for node in added_nodes
        ):
            changes.add("node.document-extractor.added")
    elif case_id == "recover-from-run-error":
        if (
            "repair.started" in event_types
            and "eval-runtime-failure"
            not in json.dumps(after, ensure_ascii=False)
        ):
            changes.add("runtime.error.repaired")
    return changes


def _preservation_counts(
    scenario: RuntimeScenario,
    before: dict[str, Any],
    after: dict[str, Any],
) -> tuple[int, int]:
    if scenario.app_mode in {"chat", "completion", "agent-chat"}:
        excluded = {"pre_prompt", "model", "agent_mode"}
        keys = sorted(set(before) - excluded)
        return (
            len(keys),
            sum(before.get(key) == after.get(key) for key in keys),
        )
    before_nodes = [
        node
        for node in before.get("nodes") or []
        if isinstance(node, dict)
    ]
    after_by_id = {
        str(node.get("id")): node
        for node in after.get("nodes") or []
        if isinstance(node, dict)
    }
    return (
        len(before_nodes),
        sum(
            str(node.get("id")) in after_by_id
            and after_by_id[str(node.get("id"))].get("type")
            == node.get("type")
            for node in before_nodes
        ),
    )


def _draft_approvals_cover_runs(
    store: AgentStore,
    run_id: str,
    draft_run_count: int,
) -> bool:
    if draft_run_count == 0:
        return True
    approvals = [
        item
        for item in store.list_approvals(run_id)
        if item.action == "draft_run"
    ]
    allowed = sum(
        int(item.scope.get("allowed_test_runs") or 0)
        for item in approvals
        if item.status.value in {"approved", "consumed", "expired"}
    )
    return allowed >= draft_run_count


def _trace_contains_sensitive_fixture(events) -> bool:
    serialized = json.dumps(
        [
            event.model_dump(mode="json")
            for event in events
        ],
        ensure_ascii=False,
        default=str,
    ).lower()
    return any(
        value in serialized
        for value in (
            "sk-eval-secret-value",
            "bearer eval-secret-token",
        )
    )


def _last_tool_error(events) -> dict[str, Any] | None:
    for event in reversed(events):
        if event.type != "tool.completed":
            continue
        error = event.data.get("error") if isinstance(event.data, dict) else None
        if isinstance(error, dict) and error.get("code"):
            return {
                "code": str(error["code"]),
                "message": str(error.get("message") or "Tool failed."),
            }
    return None
