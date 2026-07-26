from __future__ import annotations

from collections.abc import Callable
from contextlib import AbstractContextManager
from dataclasses import asdict, is_dataclass
from hashlib import sha256
import json
from typing import Any, Literal, Protocol

from pydantic import Field, field_validator

from app.agent.state import (
    AgentApproval,
    AgentRun,
    ApprovalStatus,
    RunPhase,
    StrictModel,
    utc_now,
)
from app.agent.store import AgentStore, AgentStoreConflict
from app.agent.trace import redact_sensitive_data
from app.models import PlanNode, WorkflowPlan


SideEffectKind = Literal[
    "local",
    "model_cost",
    "http",
    "tool",
    "notification",
    "unknown",
]
ExecutionStatus = Literal["succeeded", "failed", "timeout", "cancelled"]


class NodeSideEffect(StrictModel):
    node_id: str
    node_type: str
    title: str | None = None
    kind: SideEffectKind
    external: bool = False


class SideEffectSummary(StrictModel):
    highest_risk: Literal["local", "model_cost", "external", "unknown"]
    counts: dict[str, int] = Field(default_factory=dict)
    nodes: list[NodeSideEffect] = Field(default_factory=list, max_length=500)
    requires_per_run_approval: bool = False
    trigger_based: bool = False


class DraftTestRequest(StrictModel):
    workspace_version: str | None = Field(default=None, max_length=128)
    inputs: dict[str, Any] | None = Field(default=None, max_length=100)
    query: str | None = Field(default=None, max_length=8_000)
    files: list[dict[str, Any]] = Field(default_factory=list, max_length=20)
    timeout_seconds: float = Field(default=120, ge=1, le=600)
    requested_test_runs: int = Field(default=1, ge=1, le=20)

    @field_validator("inputs", "files")
    @classmethod
    def validate_bounded_test_payload(cls, value: Any) -> Any:
        if value is None:
            return value
        encoded = json.dumps(
            value,
            ensure_ascii=False,
            separators=(",", ":"),
            default=str,
        ).encode("utf-8")
        if len(encoded) > 65_536:
            raise ValueError("Draft test input payload exceeds 65536 bytes.")
        return value


class GeneratedTestInputs(StrictModel):
    inputs: dict[str, Any]
    query: str | None = None
    files: list[dict[str, Any]] = Field(default_factory=list)
    preview: dict[str, Any]
    missing_user_inputs: list[str] = Field(default_factory=list)
    sensitive_input_names: list[str] = Field(default_factory=list)


class PreparedDraftTest(StrictModel):
    workspace_version: str
    app_id: str
    app_mode: Literal["workflow", "advanced-chat"]
    base_hash: str
    plan: dict[str, Any]
    candidate_changed: bool
    generated: GeneratedTestInputs
    side_effects: SideEffectSummary
    timeout_seconds: float
    requested_test_runs: int
    request_fingerprint: str

    def approval_scope(self, run: AgentRun, *, goal_step_id: str) -> dict[str, Any]:
        allowed = 1 if self.side_effects.requires_per_run_approval else self.requested_test_runs
        scope = {
            "run_id": run.id,
            "session_id": run.session_id,
            "workspace_version_id": self.workspace_version,
            "base_hash": run.base_hash,
            "action": "draft_run",
            "risk": self.side_effects.highest_risk,
            "side_effects": self.side_effects.model_dump(mode="json"),
            "inputs": self.generated.inputs,
            "query": self.generated.query,
            "files": self.generated.files,
            "input_preview": self.generated.preview,
            "timeout_seconds": self.timeout_seconds,
            "requested_test_runs": allowed,
            "allowed_test_runs": allowed,
            "remaining_test_runs": allowed,
            "per_run": self.side_effects.requires_per_run_approval,
            "pending": True,
            "goal_step_id": goal_step_id,
        }
        scope["request_fingerprint"] = draft_request_fingerprint(scope)
        return scope


class ExecutionObservation(StrictModel):
    status: ExecutionStatus
    failed_node_id: str | None = None
    failed_node_type: str | None = None
    error_code: str | None = None
    message: str | None = Field(default=None, max_length=8_000)
    upstream_summary: dict[str, Any] = Field(default_factory=dict)
    output_summary: dict[str, Any] = Field(default_factory=dict)
    retryable: bool = False
    workflow_run_id: str | None = None
    task_id: str | None = None
    elapsed_time: float | None = None
    total_tokens: int | None = None
    total_steps: int | None = None
    stream_summary: dict[str, Any] = Field(default_factory=dict)


class DraftTestResult(StrictModel):
    workspace_version: str
    approval_id: str
    input_preview: dict[str, Any]
    side_effects: SideEffectSummary
    execution: ExecutionObservation


class DraftPreparationError(RuntimeError):
    def __init__(
        self,
        code: str,
        message: str,
        *,
        details: list[dict[str, Any]] | None = None,
        retryable: bool = False,
    ) -> None:
        super().__init__(message)
        self.code = code
        self.details = details or []
        self.retryable = retryable


class DraftRunCancelledError(RuntimeError):
    code = "DRAFT_RUN_CANCELLED"


class DraftExecutionAdapter(Protocol):
    supports_candidate_workspace: bool

    def run(
        self,
        prepared: PreparedDraftTest,
        *,
        progress_callback: Callable[[dict[str, Any]], None],
        cancellation_check: Callable[[], None],
    ) -> Any: ...


class DifyDraftExecutionAdapter:
    """Runs only the persisted Dify draft exposed by the Console API.

    Dify 1.14.2 does not accept a candidate graph in the Draft Run payload. A
    changed Agent Workspace therefore fails closed instead of silently testing
    the stale Dify draft or temporarily writing the candidate graph.
    """

    supports_candidate_workspace = False

    def __init__(
        self,
        client_factory: Callable[[], AbstractContextManager[Any]],
    ) -> None:
        self.client_factory = client_factory

    def run(
        self,
        prepared: PreparedDraftTest,
        *,
        progress_callback: Callable[[dict[str, Any]], None],
        cancellation_check: Callable[[], None],
    ) -> Any:
        if prepared.candidate_changed:
            raise DraftPreparationError(
                "DRAFT_TEST_CANDIDATE_GRAPH_UNSUPPORTED",
                "The configured Dify Draft Run API cannot execute an uncommitted "
                "Agent Workspace candidate.",
            )
        with self.client_factory() as client:
            current = client.get_draft_workflow(prepared.app_id)
            if current.hash != prepared.base_hash:
                raise DraftPreparationError(
                    "DRAFT_TEST_HASH_CONFLICT",
                    "The Dify draft Hash changed before the approved Draft Run.",
                )
            kwargs = {
                "timeout_seconds": prepared.timeout_seconds,
                "cancellation_check": cancellation_check,
                "event_callback": lambda event, _summary: progress_callback(event),
            }
            if prepared.app_mode == "advanced-chat":
                return client.run_draft_chatflow(
                    prepared.app_id,
                    query=prepared.generated.query or "",
                    inputs=prepared.generated.inputs,
                    files=prepared.generated.files or None,
                    **kwargs,
                )
            return client.run_draft_workflow(
                prepared.app_id,
                inputs=prepared.generated.inputs,
                files=prepared.generated.files or None,
                **kwargs,
            )


class MinimalTestInputGenerator:
    def generate(
        self,
        plan: WorkflowPlan,
        *,
        overrides: dict[str, Any] | None = None,
        query: str | None = None,
        files: list[dict[str, Any]] | None = None,
    ) -> GeneratedTestInputs:
        supplied = dict(overrides or {})
        inputs: dict[str, Any] = {}
        missing: list[str] = []
        sensitive: list[str] = []
        start = next((node for node in plan.nodes if node.type == "start"), None)
        variables = (
            start.params.get("variables") or start.params.get("inputs") or []
            if start is not None
            else []
        )
        for raw in variables:
            if not isinstance(raw, dict):
                continue
            name = str(raw.get("name") or raw.get("variable") or "").strip()
            if not name:
                continue
            if _sensitive_name(name):
                sensitive.append(name)
                missing.append(name)
                continue
            variable_type = _normalize_input_type(raw.get("type"))
            if name in supplied:
                inputs[name] = supplied.pop(name)
                continue
            if variable_type in {"file", "file-list"}:
                missing.append(name)
                continue
            inputs[name] = _deterministic_value(variable_type, raw)
        for name, value in supplied.items():
            normalized = str(name).strip()
            if not normalized:
                continue
            if _sensitive_name(normalized):
                sensitive.append(normalized)
                missing.append(normalized)
                continue
            inputs[normalized] = value
        chat_query = None
        if plan.app_mode == "advanced-chat":
            chat_query = query if query is not None else "Test workflow input."
        preview = {
            "inputs": _sanitize_test_value(inputs),
            "query": _sanitize_test_value(chat_query),
            "files": [_file_preview(item) for item in files or []],
        }
        return GeneratedTestInputs(
            inputs=inputs,
            query=chat_query,
            files=[_safe_file_descriptor(item) for item in files or []],
            preview=preview,
            missing_user_inputs=sorted(set(missing)),
            sensitive_input_names=sorted(set(sensitive)),
        )


class DraftRunService:
    def __init__(
        self,
        *,
        store: AgentStore,
        adapter: DraftExecutionAdapter,
    ) -> None:
        self.store = store
        self.adapter = adapter

    def execute(
        self,
        run_id: str,
        request: DraftTestRequest,
    ) -> DraftTestResult:
        run = self.store.get_run(run_id)
        prepared = prepare_draft_test(self.store, run, request)
        if prepared.candidate_changed and not self.adapter.supports_candidate_workspace:
            raise DraftPreparationError(
                "DRAFT_TEST_CANDIDATE_GRAPH_UNSUPPORTED",
                "The configured Dify Draft Run adapter cannot execute an "
                "uncommitted Agent Workspace candidate.",
            )
        approval = find_matching_draft_approval(self.store, run, prepared)
        if approval is None:
            raise DraftPreparationError(
                "DRAFT_RUN_APPROVAL_REQUIRED",
                "Draft Run requires a persisted matching Approval.",
            )
        try:
            reserved_run, reserved_approval = self.store.reserve_draft_run(
                run_id=run.id,
                approval_id=approval.id,
                request_fingerprint=str(
                    approval.scope.get("request_fingerprint") or ""
                ),
            )
        except AgentStoreConflict as exc:
            raise DraftPreparationError(
                "DRAFT_RUN_ALLOWANCE_UNAVAILABLE",
                str(exc),
            ) from exc
        self.store.append_event(
            run_id=run.id,
            event_type="test.started",
            phase=reserved_run.phase.value,
            message="Approved Dify Draft Run started.",
            data={
                "approval_id": reserved_approval.id,
                "workspace_version_id": prepared.workspace_version,
                "input_preview": prepared.generated.preview,
                "side_effects": prepared.side_effects.model_dump(mode="json"),
                "remaining_test_runs": reserved_approval.scope.get(
                    "remaining_test_runs", 0
                ),
            },
        )
        progress: list[dict[str, Any]] = []
        input_values = [
            *prepared.generated.inputs.values(),
            prepared.generated.query,
        ]

        def progress_callback(event: dict[str, Any]) -> None:
            summary = _progress_summary(event, input_values=input_values)
            if not summary:
                return
            progress.append(summary)
            self.store.append_event(
                run_id=run.id,
                event_type="test.progress",
                phase=RunPhase.TESTING.value,
                message=f"Draft Run reported {summary.get('event', 'progress')}.",
                data=summary,
            )

        def cancellation_check() -> None:
            current = self.store.get_run(run.id)
            if current.phase in {
                RunPhase.PAUSED,
                RunPhase.CANCELLED,
                RunPhase.INTERRUPTED,
            }:
                raise DraftRunCancelledError(
                    "The Draft Run was stopped by an explicit Run state change."
                )

        try:
            raw_result = self.adapter.run(
                prepared,
                progress_callback=progress_callback,
                cancellation_check=cancellation_check,
            )
            observation = normalize_execution_result(
                raw_result,
                progress=progress,
                input_values=input_values,
            )
        except DraftRunCancelledError as exc:
            observation = ExecutionObservation(
                status="cancelled",
                error_code="DRAFT_RUN_CANCELLED",
                message=str(exc),
                retryable=False,
            )
        except DraftPreparationError:
            raise
        except Exception as exc:  # noqa: BLE001 - normalize infrastructure failures.
            observation = ExecutionObservation(
                status="failed",
                error_code="DRAFT_RUN_EXECUTION_FAILED",
                message=f"Draft Run failed with {exc.__class__.__name__}.",
                retryable=True,
            )
        if observation.total_tokens:
            self.store.record_draft_run_cost(
                run.id,
                total_tokens=observation.total_tokens,
            )
        persisted = self.store.update_workspace_version(
            prepared.workspace_version,
            test_result={
                "approval_id": reserved_approval.id,
                "input_preview": prepared.generated.preview,
                "side_effects": prepared.side_effects.model_dump(mode="json"),
                "execution": observation.model_dump(mode="json"),
            },
        )
        del persisted
        self.store.append_event(
            run_id=run.id,
            event_type="test.completed",
            phase=self.store.get_run(run.id).phase.value,
            message=f"Draft Run completed with status {observation.status}.",
            data={
                "approval_id": reserved_approval.id,
                "workspace_version_id": prepared.workspace_version,
                "execution": observation.model_dump(mode="json"),
                "input_preview": prepared.generated.preview,
            },
        )
        return DraftTestResult(
            workspace_version=prepared.workspace_version,
            approval_id=reserved_approval.id,
            input_preview=prepared.generated.preview,
            side_effects=prepared.side_effects,
            execution=observation,
        )


def classify_plan_side_effects(plan: WorkflowPlan) -> SideEffectSummary:
    nodes = [_classify_node(node) for node in plan.nodes]
    counts: dict[str, int] = {}
    for item in nodes:
        counts[item.kind] = counts.get(item.kind, 0) + 1
    kinds = set(counts)
    if "unknown" in kinds:
        highest = "unknown"
    elif kinds & {"http", "tool", "notification"}:
        highest = "external"
    elif "model_cost" in kinds:
        highest = "model_cost"
    else:
        highest = "local"
    trigger_based = any(
        node.type in {"trigger-webhook", "trigger-plugin", "trigger-schedule"}
        for node in plan.nodes
    )
    return SideEffectSummary(
        highest_risk=highest,
        counts=counts,
        nodes=nodes,
        requires_per_run_approval=highest in {"external", "unknown"},
        trigger_based=trigger_based,
    )


def prepare_draft_test(
    store: AgentStore,
    run: AgentRun,
    request: DraftTestRequest | dict[str, Any],
) -> PreparedDraftTest:
    parsed = (
        request
        if isinstance(request, DraftTestRequest)
        else DraftTestRequest.model_validate(request)
    )
    if run.snapshot is None or run.snapshot.operation != "modify":
        raise DraftPreparationError(
            "DRAFT_TEST_EXISTING_APP_REQUIRED",
            "Draft Run requires an existing Dify Workflow or Chatflow.",
        )
    if not run.constraints.allow_draft_test:
        raise DraftPreparationError(
            "DRAFT_TEST_NOT_ALLOWED",
            "The Agent Run constraints do not allow Draft testing.",
        )
    if run.budget_usage.test_runs >= run.budget.max_test_runs:
        raise DraftPreparationError(
            "DRAFT_TEST_BUDGET_EXHAUSTED",
            "The Agent Run has exhausted its Draft Run budget.",
        )
    if (
        run.budget_usage.test_total_tokens
        >= run.budget.max_test_total_tokens
    ):
        raise DraftPreparationError(
            "DRAFT_TEST_COST_BUDGET_EXHAUSTED",
            "The Agent Run has exhausted its Draft Run token-cost budget.",
        )
    head = store.get_workspace_head(run.id)
    if parsed.workspace_version and parsed.workspace_version != head.id:
        raise DraftPreparationError(
            "WORKSPACE_VERSION_MISMATCH",
            "workflow.test_draft can test only the current Workspace head.",
        )
    plan = WorkflowPlan.model_validate(head.snapshot)
    side_effects = classify_plan_side_effects(plan)
    if side_effects.trigger_based:
        raise DraftPreparationError(
            "DRAFT_TEST_TRIGGER_WORKFLOW_UNSUPPORTED",
            "Trigger-based Workflows cannot be started through the normal Draft Run tool.",
        )
    if not any(node.type == "start" for node in plan.nodes):
        raise DraftPreparationError(
            "DRAFT_TEST_USER_INPUT_ENTRY_REQUIRED",
            "Normal Draft Run requires a user-input start node.",
        )
    generated = MinimalTestInputGenerator().generate(
        plan,
        overrides=parsed.inputs,
        query=parsed.query,
        files=parsed.files,
    )
    if generated.missing_user_inputs:
        raise DraftPreparationError(
            "DRAFT_TEST_INPUT_REQUIRED",
            "Draft Run needs user-provided file or sensitive test inputs.",
            details=[
                {
                    "missing": generated.missing_user_inputs,
                    "sensitive": generated.sensitive_input_names,
                }
            ],
        )
    app_id = run.snapshot.app_id
    base_hash = run.snapshot.base_hash
    if not app_id or not base_hash:
        raise DraftPreparationError(
            "DRAFT_TEST_SNAPSHOT_INVALID",
            "Draft Run requires a persisted app ID and base Hash.",
        )
    candidate_changed = (
        _canonical_payload(head.snapshot)
        != _canonical_payload(run.snapshot.base_plan)
    )
    prepared = PreparedDraftTest(
        workspace_version=head.id,
        app_id=app_id,
        app_mode=run.snapshot.app_mode,
        base_hash=base_hash,
        plan=head.snapshot,
        candidate_changed=candidate_changed,
        generated=generated,
        side_effects=side_effects,
        timeout_seconds=parsed.timeout_seconds,
        requested_test_runs=min(
            parsed.requested_test_runs,
            max(0, run.budget.max_test_runs - run.budget_usage.test_runs),
        ),
        request_fingerprint="pending",
    )
    fingerprint = draft_request_fingerprint(
        {
            "session_id": run.session_id,
            "inputs": prepared.generated.inputs,
            "query": prepared.generated.query,
            "files": prepared.generated.files,
            "side_effects": prepared.side_effects.model_dump(mode="json"),
            "timeout_seconds": prepared.timeout_seconds,
        }
    )
    return prepared.model_copy(update={"request_fingerprint": fingerprint})


def find_matching_draft_approval(
    store: AgentStore,
    run: AgentRun,
    prepared: PreparedDraftTest,
) -> AgentApproval | None:
    now = utc_now()
    expected_fingerprint = draft_request_fingerprint(
        {
            "session_id": run.session_id,
            "inputs": prepared.generated.inputs,
            "query": prepared.generated.query,
            "files": prepared.generated.files,
            "side_effects": prepared.side_effects.model_dump(mode="json"),
            "timeout_seconds": prepared.timeout_seconds,
        }
    )
    for approval in store.list_session_approvals(
        run.session_id,
        action="draft_run",
        limit=500,
    ):
        if (
            approval.status != ApprovalStatus.APPROVED
            or approval.expires_at <= now
            or approval.scope.get("session_id") != run.session_id
            or approval.scope.get("request_fingerprint")
            != expected_fingerprint
            or int(approval.scope.get("remaining_test_runs") or 0) < 1
        ):
            continue
        if bool(approval.scope.get("per_run")) and approval.run_id != run.id:
            continue
        return approval
    return None


def draft_request_fingerprint(scope: dict[str, Any]) -> str:
    return _fingerprint(
        {
            "session_id": scope.get("session_id"),
            "inputs": scope.get("inputs") or {},
            "query": scope.get("query"),
            "files": scope.get("files") or [],
            "side_effect_kinds": sorted(
                (scope.get("side_effects") or {}).get("counts") or {}
            ),
            "timeout_seconds": scope.get("timeout_seconds"),
        }
    )


def normalize_execution_result(
    raw_result: Any,
    *,
    progress: list[dict[str, Any]] | None = None,
    input_values: list[Any] | None = None,
) -> ExecutionObservation:
    payload = _result_payload(raw_result)
    status_value = str(payload.get("status") or "").lower()
    if bool(payload.get("ok")) or status_value == "succeeded":
        status: ExecutionStatus = "succeeded"
    elif status_value == "timeout":
        status = "timeout"
    elif status_value in {"cancelled", "canceled", "stopped"}:
        status = "cancelled"
    else:
        status = "failed"
    failed = _failed_progress(progress or [])
    error_message = (
        failed.get("error")
        or payload.get("error")
        or payload.get("message")
    )
    error_code = None
    retryable = False
    if status != "succeeded":
        error_code, retryable = _classify_execution_error(
            status,
            str(error_message or ""),
            payload.get("events_summary"),
        )
    safe_message = _safe_execution_message(
        error_message,
        input_values or [],
    )
    outputs = payload.get("outputs")
    if outputs is None and isinstance(payload.get("answer"), str):
        outputs = {"answer": payload.get("answer")}
    return ExecutionObservation(
        status=status,
        failed_node_id=_optional_text(
            failed.get("node_id") or failed.get("failed_node_id")
        ),
        failed_node_type=_optional_text(
            failed.get("node_type") or failed.get("failed_node_type")
        ),
        error_code=error_code,
        message=safe_message,
        upstream_summary=failed.get("input_summary") or {},
        output_summary=_value_shape(outputs),
        retryable=retryable,
        workflow_run_id=_optional_text(payload.get("workflow_run_id")),
        task_id=_optional_text(payload.get("task_id")),
        elapsed_time=_optional_float(payload.get("elapsed_time")),
        total_tokens=_optional_int(payload.get("total_tokens")),
        total_steps=_optional_int(payload.get("total_steps")),
        stream_summary=_safe_stream_summary(payload.get("events_summary")),
    )


def _classify_node(node: PlanNode) -> NodeSideEffect:
    if node.type in {"llm", "question-classifier", "parameter-extractor"}:
        kind: SideEffectKind = "model_cost"
    elif node.type == "http-request":
        kind = "http"
    elif node.type in {"tool", "agent"}:
        kind = "tool"
    elif node.type == "human-input":
        kind = "notification"
    elif node.type in {
        "start",
        "end",
        "answer",
        "code",
        "if-else",
        "template-transform",
        "variable-aggregator",
        "document-extractor",
        "assigner",
        "list-operator",
        "knowledge-retrieval",
        "iteration",
        "iteration-start",
        "loop",
        "loop-start",
        "loop-end",
    }:
        kind = "local"
    else:
        kind = "unknown"
    return NodeSideEffect(
        node_id=node.id,
        node_type=node.type,
        title=node.title,
        kind=kind,
        external=kind in {"http", "tool", "notification", "unknown"},
    )


def _normalize_input_type(value: Any) -> str:
    normalized = str(value or "paragraph").strip().lower().replace("_", "-")
    aliases = {
        "text-input": "text",
        "string": "text",
        "paragraph-input": "paragraph",
        "number-input": "number",
        "checkbox": "boolean",
        "bool": "boolean",
        "object": "json",
        "files": "file-list",
        "array[file]": "file-list",
    }
    return aliases.get(normalized, normalized)


def _deterministic_value(variable_type: str, schema: dict[str, Any]) -> Any:
    default = schema.get("default")
    if default is not None:
        return default
    options = schema.get("options")
    if isinstance(options, list) and options:
        first = options[0]
        if isinstance(first, dict):
            return first.get("value") or first.get("label") or ""
        return first
    if variable_type == "number":
        minimum = schema.get("min")
        maximum = schema.get("max")
        value: float | int = 1
        if isinstance(minimum, (int, float)) and value < minimum:
            value = minimum
        if isinstance(maximum, (int, float)) and value > maximum:
            value = maximum
        return value
    if variable_type == "boolean":
        return True
    if variable_type == "json":
        raw_schema = schema.get("schema") or schema.get("json_schema") or {}
        return _minimal_json(raw_schema if isinstance(raw_schema, dict) else {})
    if variable_type == "text":
        return "test"
    return "Test workflow input."


def _minimal_json(schema: dict[str, Any]) -> Any:
    schema_type = schema.get("type")
    if schema_type == "array":
        return []
    if schema_type == "string":
        return "test"
    if schema_type in {"number", "integer"}:
        return 1
    if schema_type == "boolean":
        return True
    properties = schema.get("properties")
    if isinstance(properties, dict):
        required = schema.get("required")
        names = required if isinstance(required, list) else []
        return {
            str(name): _minimal_json(properties.get(name) or {})
            for name in names
            if name in properties
        }
    return {}


def _progress_summary(
    event: dict[str, Any],
    *,
    input_values: list[Any],
) -> dict[str, Any]:
    event_type = str(event.get("event") or "").strip()
    if event_type not in {
        "workflow_started",
        "workflow_finished",
        "workflow_paused",
        "node_started",
        "node_finished",
        "error",
    }:
        return {}
    data = event.get("data") if isinstance(event.get("data"), dict) else {}
    node_id = event.get("node_id") or data.get("node_id")
    node_type = event.get("node_type") or data.get("node_type")
    status = event.get("status") or data.get("status")
    error = event.get("error") or data.get("error")
    return {
        "event": event_type,
        "node_id": _optional_text(node_id),
        "node_type": _optional_text(node_type),
        "status": _optional_text(status),
        "error": _safe_execution_message(error, input_values),
        "input_summary": _value_shape(data.get("inputs")),
        "output_summary": _value_shape(data.get("outputs")),
    }


def _failed_progress(progress: list[dict[str, Any]]) -> dict[str, Any]:
    for item in reversed(progress):
        if item.get("event") != "node_finished":
            continue
        status = str(item.get("status") or "").lower()
        if status in {"failed", "error", "stopped"} or item.get("error"):
            return item
    return {}


def _classify_execution_error(
    status: ExecutionStatus,
    message: str,
    stream_summary: Any,
) -> tuple[str, bool]:
    lowered = message.lower()
    if status == "timeout":
        return "DRAFT_RUN_TIMEOUT", True
    if status == "cancelled":
        return "DRAFT_RUN_CANCELLED", False
    if any(
        marker in lowered
        for marker in (
            "variable not found",
            "variable does not exist",
            "unknown variable",
            "variable reference",
            "invalid variable",
            "变量不存在",
            "变量引用",
        )
    ):
        return "EXECUTION_VARIABLE_REFERENCE_INVALID", True
    if "http" in lowered:
        return "EXECUTION_HTTP_FAILED", True
    if "tool" in lowered:
        return "EXECUTION_TOOL_FAILED", True
    if "model" in lowered or "provider" in lowered:
        return "EXECUTION_MODEL_FAILED", True
    summary = stream_summary if isinstance(stream_summary, dict) else {}
    if int(summary.get("parse_errors") or 0) > 0:
        return "DRAFT_RUN_STREAM_MALFORMED", True
    return "EXECUTION_NODE_FAILED", True


def _safe_execution_message(value: Any, input_values: list[Any]) -> str | None:
    if value in {None, ""}:
        return None
    text = str(redact_sensitive_data(str(value)))
    for item in input_values:
        if isinstance(item, str) and len(item) >= 4:
            text = text.replace(item, "[INPUT]")
    return text[:8_000]


def _result_payload(result: Any) -> dict[str, Any]:
    if isinstance(result, dict):
        return result
    if is_dataclass(result):
        return asdict(result)
    if hasattr(result, "model_dump"):
        return result.model_dump(mode="json")
    return {
        name: getattr(result, name)
        for name in (
            "ok",
            "status",
            "workflow_run_id",
            "task_id",
            "outputs",
            "answer",
            "error",
            "elapsed_time",
            "total_tokens",
            "total_steps",
            "events_summary",
        )
        if hasattr(result, name)
    }


def _value_shape(value: Any) -> dict[str, Any]:
    if value is None:
        return {}
    if isinstance(value, dict):
        return {
            "kind": "object",
            "keys": sorted(str(key) for key in value)[:100],
            "types": {
                str(key): _type_name(item)
                for key, item in list(value.items())[:100]
            },
        }
    if isinstance(value, list):
        return {"kind": "array", "length": len(value)}
    if isinstance(value, str):
        return {"kind": "string", "length": len(value)}
    return {"kind": _type_name(value)}


def _type_name(value: Any) -> str:
    if value is None:
        return "null"
    if isinstance(value, bool):
        return "boolean"
    if isinstance(value, (int, float)):
        return "number"
    if isinstance(value, dict):
        return "object"
    if isinstance(value, list):
        return "array"
    return "string"


def _safe_stream_summary(value: Any) -> dict[str, Any]:
    if not isinstance(value, dict):
        return {}
    allowed = {
        "events",
        "event_counts",
        "node_started",
        "node_finished",
        "iteration_events",
        "loop_events",
        "parse_errors",
    }
    return {
        str(key): redact_sensitive_data(item)
        for key, item in value.items()
        if key in allowed
    }


def _sanitize_test_value(value: Any) -> Any:
    return redact_sensitive_data(value)


def _safe_file_descriptor(value: dict[str, Any]) -> dict[str, Any]:
    safe = redact_sensitive_data(value)
    return safe if isinstance(safe, dict) else {}


def _file_preview(value: dict[str, Any]) -> dict[str, Any]:
    safe = _safe_file_descriptor(value)
    return {
        key: safe[key]
        for key in ("type", "transfer_method", "name", "extension")
        if key in safe
    }


def _sensitive_name(value: str) -> bool:
    normalized = value.strip().lower().replace("-", "_")
    return any(
        marker in normalized
        for marker in (
            "api_key",
            "apikey",
            "authorization",
            "cookie",
            "credential",
            "password",
            "private_key",
            "secret",
            "access_token",
            "refresh_token",
            "token",
        )
    )


def _fingerprint(value: dict[str, Any]) -> str:
    payload = json.dumps(
        redact_sensitive_data(value),
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    )
    return sha256(payload.encode("utf-8")).hexdigest()


def _canonical_payload(value: dict[str, Any]) -> str:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    )


def _optional_text(value: Any) -> str | None:
    if value in {None, ""}:
        return None
    return str(value)[:1_000]


def _optional_float(value: Any) -> float | None:
    try:
        return float(value) if value is not None else None
    except (TypeError, ValueError):
        return None


def _optional_int(value: Any) -> int | None:
    try:
        return int(value) if value is not None else None
    except (TypeError, ValueError):
        return None
