from __future__ import annotations

from typing import Any, Literal

from pydantic import Field, model_validator

from app.agent.state import AgentBudget, StrictModel


class EvaluationSideEffectPolicy(StrictModel):
    allow_draft_run: bool = False
    allowed_kinds: set[str] = Field(default_factory=set)
    max_draft_runs: int = Field(default=0, ge=0, le=20)


class EvaluationFixture(StrictModel):
    snapshot_id: str = Field(min_length=1, max_length=256)
    snapshot_version: str = Field(min_length=1, max_length=64)
    allowed_resources: list[str] = Field(default_factory=list, max_length=100)
    prompt_injection_fixture: str | None = Field(
        default=None,
        max_length=256,
    )


class EvaluationExpectedResult(StrictModel):
    status: Literal["completed", "failed"]
    reviewable: bool
    final_valid: bool
    changes: list[str] = Field(default_factory=list, max_length=100)
    unrelated_total: int = Field(default=0, ge=0)
    unrelated_preserved: int = Field(default=0, ge=0)
    repairable_failure: bool = False
    auto_repaired: bool = False
    unapproved_writes: int = Field(default=0, ge=0)
    incorrect_conflict_overwrites: int = Field(default=0, ge=0)
    trace: list[dict[str, Any]] = Field(min_length=1, max_length=500)
    terminal_reason: dict[str, Any] | None = None

    @model_validator(mode="after")
    def validate_result_consistency(self) -> "EvaluationExpectedResult":
        if self.unrelated_preserved > self.unrelated_total:
            raise ValueError(
                "unrelated_preserved cannot exceed unrelated_total."
            )
        if self.status == "failed" and self.terminal_reason is None:
            raise ValueError(
                "Failed evaluation fixtures require a terminal reason."
            )
        if self.status == "completed" and not self.reviewable:
            raise ValueError(
                "Completed evaluation fixtures must be reviewable."
            )
        return self


class EvaluationCase(StrictModel):
    id: str = Field(pattern=r"^[a-z0-9][a-z0-9-]{0,127}$")
    version: str = Field(pattern=r"^[0-9]+\.[0-9]+\.[0-9]+$")
    goal: str = Field(min_length=1, max_length=8_000)
    app_mode: Literal[
        "workflow",
        "advanced-chat",
        "chat",
        "completion",
        "agent-chat",
    ]
    fixture: EvaluationFixture
    allowed_capabilities: list[str] = Field(min_length=1, max_length=100)
    invariants: list[str] = Field(min_length=1, max_length=100)
    required_changes: list[str] = Field(default_factory=list, max_length=100)
    forbidden_changes: list[str] = Field(default_factory=list, max_length=100)
    budget: AgentBudget = Field(default_factory=AgentBudget)
    side_effect_policy: EvaluationSideEffectPolicy = Field(
        default_factory=EvaluationSideEffectPolicy
    )
    expected_validation: Literal["valid", "blocked"]
    expected_result: EvaluationExpectedResult
    required_skill: str | None = Field(default=None, max_length=128)


class EvaluationCaseResult(StrictModel):
    case_id: str
    case_version: str
    goal: str
    app_mode: str
    status: Literal["completed", "failed"]
    reviewable: bool
    final_valid: bool
    goal_completed: bool
    required_changes_present: bool
    forbidden_changes_absent: bool
    invariant_passed: bool
    unrelated_total: int
    unrelated_preserved: int
    repairable_failure: bool
    auto_repaired: bool
    unapproved_writes: int
    incorrect_conflict_overwrites: int
    readable_trace: bool
    structured_terminal_reason: bool
    trace_event_count: int
    terminal_reason: dict[str, Any] | None = None
    executor_evidence: dict[str, Any] = Field(default_factory=dict)


class EvaluationMetrics(StrictModel):
    case_count: int = Field(ge=0)
    completed_count: int = Field(ge=0)
    failed_count: int = Field(ge=0)
    reviewable_count: int = Field(ge=0)
    final_validity_rate: float = Field(ge=0, le=1)
    goal_completion_rate: float = Field(ge=0, le=1)
    unrelated_preservation_rate: float = Field(ge=0, le=1)
    auto_repair_rate: float = Field(ge=0, le=1)
    failures_with_readable_trace_rate: float = Field(ge=0, le=1)
    unapproved_writes: int = Field(ge=0)
    incorrect_conflict_overwrites: int = Field(ge=0)


class EvaluationGates(StrictModel):
    final_validity: bool
    goal_completion: bool
    unrelated_preservation: bool
    auto_repair: bool
    readable_failures: bool
    unapproved_writes: bool
    conflict_overwrites: bool
    passed: bool


class EvaluationReport(StrictModel):
    report_version: Literal["1.0.0"] = "1.0.0"
    suite_version: str
    executor: str
    live_provider: bool
    reproducible: bool
    runtime_executed: bool
    cases: list[EvaluationCaseResult]
    metrics: EvaluationMetrics
    gates: EvaluationGates
