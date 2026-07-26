from __future__ import annotations

from datetime import datetime, timezone
from enum import Enum
from typing import Annotated, Any, Literal
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from app.models import AppMode


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def new_id() -> str:
    return str(uuid4())


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class SessionStatus(str, Enum):
    ACTIVE = "active"
    CLOSED = "closed"


AgentOperation = Literal["modify", "create"]


class RunStatus(str, Enum):
    QUEUED = "queued"
    RUNNING = "running"
    PAUSED = "paused"
    COMPLETED = "completed"
    CONFLICTED = "conflicted"
    CANCELLED = "cancelled"
    FAILED = "failed"
    INTERRUPTED = "interrupted"


class RunPhase(str, Enum):
    QUEUED = "queued"
    OBSERVING = "observing"
    PLANNING = "planning"
    ACTING = "acting"
    VALIDATING = "validating"
    TESTING = "testing"
    PAUSED = "paused"
    WAITING_USER = "waiting_user"
    WAITING_APPROVAL = "waiting_approval"
    COMMITTING = "committing"
    COMPLETED = "completed"
    CONFLICTED = "conflicted"
    CANCELLED = "cancelled"
    FAILED = "failed"
    INTERRUPTED = "interrupted"


TERMINAL_RUN_PHASES = frozenset(
    {
        RunPhase.COMPLETED,
        RunPhase.CONFLICTED,
        RunPhase.CANCELLED,
        RunPhase.FAILED,
    }
)
PAUSED_RUN_PHASES = frozenset(
    {
        RunPhase.WAITING_USER,
        RunPhase.WAITING_APPROVAL,
        RunPhase.PAUSED,
        RunPhase.INTERRUPTED,
    }
)
RECOVERABLE_RUN_PHASES = frozenset(
    {
        RunPhase.WAITING_USER,
        RunPhase.WAITING_APPROVAL,
        RunPhase.PAUSED,
        RunPhase.INTERRUPTED,
    }
)

_RUN_PHASE_TRANSITIONS: dict[RunPhase, frozenset[RunPhase]] = {
    RunPhase.QUEUED: frozenset(
        {
            RunPhase.OBSERVING,
            RunPhase.PAUSED,
            RunPhase.CANCELLED,
            RunPhase.FAILED,
            RunPhase.INTERRUPTED,
        }
    ),
    RunPhase.OBSERVING: frozenset(
        {
            RunPhase.PLANNING,
            RunPhase.PAUSED,
            RunPhase.CANCELLED,
            RunPhase.FAILED,
            RunPhase.INTERRUPTED,
        }
    ),
    RunPhase.PLANNING: frozenset(
        {
            RunPhase.ACTING,
            RunPhase.PAUSED,
            RunPhase.WAITING_USER,
            RunPhase.CANCELLED,
            RunPhase.FAILED,
            RunPhase.INTERRUPTED,
        }
    ),
    RunPhase.ACTING: frozenset(
        {
            RunPhase.VALIDATING,
            RunPhase.PAUSED,
            RunPhase.WAITING_USER,
            RunPhase.CANCELLED,
            RunPhase.FAILED,
            RunPhase.INTERRUPTED,
        }
    ),
    RunPhase.VALIDATING: frozenset(
        {
            RunPhase.ACTING,
            RunPhase.TESTING,
            RunPhase.PAUSED,
            RunPhase.WAITING_APPROVAL,
            RunPhase.CANCELLED,
            RunPhase.FAILED,
            RunPhase.INTERRUPTED,
        }
    ),
    RunPhase.TESTING: frozenset(
        {
            RunPhase.ACTING,
            RunPhase.PAUSED,
            RunPhase.WAITING_APPROVAL,
            RunPhase.CANCELLED,
            RunPhase.FAILED,
            RunPhase.INTERRUPTED,
        }
    ),
    RunPhase.WAITING_USER: frozenset(
        {RunPhase.PLANNING, RunPhase.CANCELLED, RunPhase.FAILED, RunPhase.INTERRUPTED}
    ),
    RunPhase.WAITING_APPROVAL: frozenset(
        {RunPhase.COMMITTING, RunPhase.CANCELLED, RunPhase.FAILED, RunPhase.INTERRUPTED}
    ),
    RunPhase.PAUSED: frozenset(
        {
            RunPhase.OBSERVING,
            RunPhase.PLANNING,
            RunPhase.CANCELLED,
            RunPhase.FAILED,
            RunPhase.INTERRUPTED,
        }
    ),
    RunPhase.COMMITTING: frozenset(
        {
            RunPhase.COMPLETED,
            RunPhase.CONFLICTED,
            RunPhase.CANCELLED,
            RunPhase.FAILED,
            RunPhase.INTERRUPTED,
        }
    ),
    RunPhase.INTERRUPTED: frozenset(
        {
            RunPhase.OBSERVING,
            RunPhase.PLANNING,
            RunPhase.COMMITTING,
            RunPhase.CANCELLED,
            RunPhase.FAILED,
        }
    ),
    RunPhase.COMPLETED: frozenset(),
    RunPhase.CONFLICTED: frozenset(),
    RunPhase.CANCELLED: frozenset(),
    RunPhase.FAILED: frozenset(),
}


class IllegalRunTransition(ValueError):
    code = "AGENT_RUN_TRANSITION_INVALID"

    def __init__(self, current: RunPhase, target: RunPhase) -> None:
        super().__init__(f"Agent Run cannot transition from {current.value} to {target.value}.")
        self.current = current
        self.target = target


def run_status_for_phase(phase: RunPhase) -> RunStatus:
    if phase == RunPhase.QUEUED:
        return RunStatus.QUEUED
    if phase in {
        RunPhase.PAUSED,
        RunPhase.WAITING_USER,
        RunPhase.WAITING_APPROVAL,
    }:
        return RunStatus.PAUSED
    if phase == RunPhase.INTERRUPTED:
        return RunStatus.INTERRUPTED
    if phase == RunPhase.COMPLETED:
        return RunStatus.COMPLETED
    if phase == RunPhase.CONFLICTED:
        return RunStatus.CONFLICTED
    if phase == RunPhase.CANCELLED:
        return RunStatus.CANCELLED
    if phase == RunPhase.FAILED:
        return RunStatus.FAILED
    return RunStatus.RUNNING


def validate_run_transition(current: RunPhase, target: RunPhase) -> None:
    if current == target:
        return
    if target not in _RUN_PHASE_TRANSITIONS[current]:
        raise IllegalRunTransition(current, target)


class GoalStep(StrictModel):
    id: str = Field(min_length=1, max_length=128)
    description: str = Field(min_length=1, max_length=2_000)
    status: Literal["pending", "in_progress", "completed", "blocked", "skipped"] = "pending"
    depends_on: list[str] = Field(default_factory=list, max_length=50)
    evidence: list[str] = Field(default_factory=list, max_length=100)


class GoalPlan(StrictModel):
    goal: str = Field(min_length=1, max_length=8_000)
    assumptions: list[str] = Field(default_factory=list, max_length=100)
    constraints: list[str] = Field(default_factory=list, max_length=100)
    success_criteria: list[str] = Field(min_length=1, max_length=100)
    steps: list[GoalStep] = Field(min_length=1, max_length=100)
    revision: int = Field(default=1, ge=1)

    @model_validator(mode="after")
    def validate_step_graph(self) -> "GoalPlan":
        step_ids = [step.id for step in self.steps]
        if len(step_ids) != len(set(step_ids)):
            raise ValueError("Goal Plan step ids must be unique.")
        known = set(step_ids)
        for step in self.steps:
            unknown = sorted(set(step.depends_on) - known)
            if unknown:
                raise ValueError(
                    f"Goal Plan step {step.id} depends on unknown steps: {', '.join(unknown)}."
                )
            if step.id in step.depends_on:
                raise ValueError(f"Goal Plan step {step.id} cannot depend on itself.")
        return self


class ToolCallDecision(StrictModel):
    type: Literal["tool_call"]
    tool_name: str = Field(min_length=1, max_length=128)
    arguments: dict[str, Any]
    goal_step_id: str = Field(min_length=1, max_length=128)


class AskUserDecision(StrictModel):
    type: Literal["ask_user"]
    question: str = Field(min_length=1, max_length=8_000)
    missing: list[str] = Field(default_factory=list, max_length=100)


class FinishDecision(StrictModel):
    type: Literal["finish"]
    summary: str = Field(min_length=1, max_length=8_000)
    evidence: list[str] = Field(default_factory=list, max_length=100)


AgentDecision = Annotated[
    ToolCallDecision | AskUserDecision | FinishDecision,
    Field(discriminator="type"),
]


class Observation(StrictModel):
    kind: str = Field(min_length=1, max_length=128)
    summary: str = Field(min_length=1, max_length=8_000)
    data: dict[str, Any] = Field(default_factory=dict)
    created_at: datetime = Field(default_factory=utc_now)


class AgentBudget(StrictModel):
    max_iterations: int = Field(default=8, ge=1, le=100)
    max_model_calls: int = Field(default=6, ge=1, le=100)
    max_patch_operations: int = Field(default=50, ge=1, le=500)
    max_test_runs: int = Field(default=3, ge=0, le=100)
    max_same_error_retries: int = Field(default=2, ge=0, le=20)
    max_run_seconds: int = Field(default=600, ge=1, le=86_400)


class AgentBudgetUsage(StrictModel):
    iterations: int = Field(default=0, ge=0)
    model_calls: int = Field(default=0, ge=0)
    patch_operations: int = Field(default=0, ge=0)
    test_runs: int = Field(default=0, ge=0)
    same_error_retries: int = Field(default=0, ge=0)
    latest_error_signature: str | None = Field(default=None, max_length=1_000)


class CanvasViewport(StrictModel):
    x: float = Field(ge=-10_000_000, le=10_000_000)
    y: float = Field(ge=-10_000_000, le=10_000_000)
    zoom: float = Field(gt=0, le=100)


class RunConstraints(StrictModel):
    allow_draft_test: bool = False
    allow_destructive: bool = False
    selected_node_ids: list[str] = Field(default_factory=list, max_length=100)
    selected_edge_ids: list[str] = Field(default_factory=list, max_length=100)
    viewport: CanvasViewport | None = None
    current_panel: str | None = Field(default=None, max_length=128)
    canvas_draft_hash: str | None = Field(default=None, max_length=512)
    dirty_state: bool = False
    canvas_context_revision: int = Field(default=0, ge=0)

    @field_validator("selected_node_ids", "selected_edge_ids")
    @classmethod
    def validate_canvas_ids(cls, values: list[str]) -> list[str]:
        normalized: list[str] = []
        seen: set[str] = set()
        for value in values:
            item = value.strip()
            if not item or len(item) > 256:
                raise ValueError("Canvas selection IDs must contain 1 to 256 characters.")
            if item not in seen:
                seen.add(item)
                normalized.append(item)
        return normalized


class AgentWorkflowSnapshot(StrictModel):
    operation: AgentOperation = "modify"
    app_id: str | None = Field(default=None, min_length=1, max_length=256)
    app_name: str = Field(min_length=1, max_length=512)
    app_description: str = Field(default="", max_length=8_000)
    app_mode: Literal["workflow", "advanced-chat"]
    base_hash: str | None = Field(default=None, min_length=1, max_length=512)
    base_plan: dict[str, Any]
    base_graph: dict[str, Any] = Field(default_factory=dict)
    features: dict[str, Any] = Field(default_factory=dict)
    environment_variables: list[dict[str, Any]] = Field(default_factory=list)
    conversation_variables: list[dict[str, Any]] = Field(default_factory=list)
    dify_version: dict[str, str] = Field(default_factory=dict)
    capabilities: list[dict[str, Any]] = Field(default_factory=list)
    created_at: datetime = Field(default_factory=utc_now)

    @model_validator(mode="after")
    def validate_operation_boundary(self) -> "AgentWorkflowSnapshot":
        if self.operation == "modify" and (not self.app_id or not self.base_hash):
            raise ValueError(
                "Modify Snapshots require an existing app_id and base Hash."
            )
        if self.operation == "create" and (
            self.app_id is not None or self.base_hash is not None
        ):
            raise ValueError(
                "Create Snapshots must not contain an app_id or base Hash before import."
            )
        return self


class AgentSession(StrictModel):
    id: str = Field(default_factory=new_id, min_length=1, max_length=128)
    operation: AgentOperation = "modify"
    app_id: str | None = Field(default=None, min_length=1, max_length=256)
    app_mode: AppMode | None = None
    app_name: str | None = Field(default=None, min_length=1, max_length=512)
    app_description: str = Field(default="", max_length=8_000)
    status: SessionStatus = SessionStatus.ACTIVE
    created_at: datetime = Field(default_factory=utc_now)
    updated_at: datetime = Field(default_factory=utc_now)

    @model_validator(mode="after")
    def validate_operation_boundary(self) -> "AgentSession":
        if self.operation == "modify" and not self.app_id:
            raise ValueError("Modify Sessions require an existing app_id.")
        if self.operation == "create" and self.app_mode is None:
            raise ValueError("Create Sessions require an explicit app_mode.")
        if self.operation == "create" and self.app_id is not None:
            raise ValueError(
                "Create Sessions cannot contain an app_id before import."
            )
        return self


class AgentRun(StrictModel):
    id: str = Field(default_factory=new_id, min_length=1, max_length=128)
    session_id: str = Field(min_length=1, max_length=128)
    task_id: str | None = Field(default=None, max_length=128)
    goal: str = Field(min_length=1, max_length=8_000)
    status: RunStatus = RunStatus.QUEUED
    phase: RunPhase = RunPhase.QUEUED
    base_hash: str | None = Field(default=None, max_length=512)
    head_version_id: str | None = Field(default=None, max_length=128)
    iteration: int = Field(default=0, ge=0)
    budget: AgentBudget = Field(default_factory=AgentBudget)
    budget_usage: AgentBudgetUsage = Field(default_factory=AgentBudgetUsage)
    constraints: RunConstraints = Field(default_factory=RunConstraints)
    snapshot: AgentWorkflowSnapshot | None = None
    goal_plan: GoalPlan | None = None
    observations: list[Observation] = Field(default_factory=list, max_length=200)
    review: dict[str, Any] | None = None
    commit_result: dict[str, Any] | None = None
    error: dict[str, Any] | None = None
    created_at: datetime = Field(default_factory=utc_now)
    updated_at: datetime = Field(default_factory=utc_now)
    finished_at: datetime | None = None

    @model_validator(mode="after")
    def validate_status_matches_phase(self) -> "AgentRun":
        expected = run_status_for_phase(self.phase)
        if self.status != expected:
            raise ValueError(
                f"Run status {self.status.value} does not match phase {self.phase.value}; "
                f"expected {expected.value}."
            )
        if self.phase in TERMINAL_RUN_PHASES and self.finished_at is None:
            raise ValueError("Terminal Agent Runs require finished_at.")
        if self.phase not in TERMINAL_RUN_PHASES and self.finished_at is not None:
            raise ValueError("Non-terminal Agent Runs cannot have finished_at.")
        return self

    @property
    def terminal(self) -> bool:
        return self.phase in TERMINAL_RUN_PHASES

    @property
    def paused(self) -> bool:
        return self.phase in PAUSED_RUN_PHASES

    @property
    def recoverable(self) -> bool:
        return self.phase in RECOVERABLE_RUN_PHASES

    def transition_to(
        self,
        phase: RunPhase,
        *,
        now: datetime | None = None,
        error: dict[str, Any] | None = None,
    ) -> "AgentRun":
        validate_run_transition(self.phase, phase)
        timestamp = now or utc_now()
        payload = self.model_dump()
        payload.update(
            {
                "phase": phase,
                "status": run_status_for_phase(phase),
                "updated_at": timestamp,
                "finished_at": timestamp if phase in TERMINAL_RUN_PHASES else None,
                "error": error,
            }
        )
        return AgentRun.model_validate(payload)


class WorkspaceVersion(StrictModel):
    id: str = Field(default_factory=new_id, min_length=1, max_length=128)
    run_id: str = Field(min_length=1, max_length=128)
    parent_id: str | None = Field(default=None, max_length=128)
    base_hash: str | None = Field(default=None, max_length=512)
    patch: dict[str, Any] | None = None
    reverse_patch: dict[str, Any] | None = None
    snapshot: dict[str, Any]
    validation: dict[str, Any] | None = None
    test_result: dict[str, Any] | None = None
    created_at: datetime = Field(default_factory=utc_now)


class ApprovalStatus(str, Enum):
    PENDING = "pending"
    APPROVED = "approved"
    REJECTED = "rejected"
    EXPIRED = "expired"
    CONSUMED = "consumed"


class AgentApproval(StrictModel):
    id: str = Field(default_factory=new_id, min_length=1, max_length=128)
    run_id: str = Field(min_length=1, max_length=128)
    workspace_version_id: str | None = Field(default=None, max_length=128)
    action: Literal["commit", "draft_run", "destructive_change"]
    scope: dict[str, Any] = Field(default_factory=dict)
    status: ApprovalStatus = ApprovalStatus.PENDING
    expires_at: datetime
    created_at: datetime = Field(default_factory=utc_now)
    resolved_at: datetime | None = None

    @model_validator(mode="after")
    def validate_resolution(self) -> "AgentApproval":
        resolved = self.status != ApprovalStatus.PENDING
        if resolved != (self.resolved_at is not None):
            raise ValueError("Resolved approvals require resolved_at; pending approvals must omit it.")
        return self
