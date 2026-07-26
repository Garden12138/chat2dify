from __future__ import annotations

from typing import Any, Literal

from pydantic import Field

from app.agent.execution import SideEffectSummary, classify_plan_side_effects
from app.agent.state import StrictModel
from app.agent.trace import redact_sensitive_data
from app.compiler.dify import DifyDslCompiler
from app.dify.graph import compile_plan_to_dify_graph
from app.dify.preflight import preflight_plan
from app.models import ValidationIssue, WorkflowPlan


class AgentValidationIssue(StrictModel):
    code: str = Field(min_length=1, max_length=128)
    severity: Literal["warning", "error"]
    node_id: str | None = None
    field: str | None = None
    message: str = Field(min_length=1, max_length=8_000)
    expected: Any | None = None
    actual: Any | None = None
    repair_hint: str | None = None
    retryable: bool = False


class AgentValidationReport(StrictModel):
    ok: bool
    issues: list[AgentValidationIssue] = Field(default_factory=list)
    dsl_version: str = ""
    roundtrip_ok: bool = False
    graph_compiled: bool = False
    side_effects: SideEffectSummary | None = None


class WorkflowValidationService:
    def __init__(
        self,
        *,
        compiler: DifyDslCompiler,
        expected_dsl_version: str,
    ) -> None:
        self.compiler = compiler
        self.expected_dsl_version = expected_dsl_version

    def validate(self, plan: WorkflowPlan) -> AgentValidationReport:
        preflight = preflight_plan(
            plan,
            compiler=self.compiler,
            expected_dsl_version=self.expected_dsl_version,
        )
        issues = [_agent_issue(issue) for issue in preflight.issues]
        graph_compiled = False
        try:
            compile_plan_to_dify_graph(plan, compiler=self.compiler)
            graph_compiled = True
        except Exception as exc:  # noqa: BLE001 - expose a stable diagnostic only.
            issues.append(
                AgentValidationIssue(
                    code="DIFY_GRAPH_COMPILE_FAILED",
                    severity="error",
                    field="workflow.graph",
                    message=_safe_text(str(exc)),
                    repair_hint="Fix the Plan before generating the Dify graph.",
                    retryable=True,
                )
            )
        return AgentValidationReport(
            ok=preflight.ok and graph_compiled,
            issues=_deduplicate_issues(issues),
            dsl_version=preflight.dsl_version,
            roundtrip_ok=preflight.roundtrip_ok,
            graph_compiled=graph_compiled,
            side_effects=classify_plan_side_effects(plan),
        )


def _agent_issue(issue: ValidationIssue) -> AgentValidationIssue:
    return AgentValidationIssue(
        code=issue.code,
        severity=issue.severity,
        node_id=issue.node_id,
        field=issue.path,
        message=_safe_text(issue.message),
        repair_hint=_safe_text(issue.suggestion) if issue.suggestion else None,
        retryable=issue.severity == "error",
    )


def _safe_text(value: str) -> str:
    redacted = redact_sensitive_data(value)
    return str(redacted)[:8_000]


def _deduplicate_issues(
    issues: list[AgentValidationIssue],
) -> list[AgentValidationIssue]:
    result: list[AgentValidationIssue] = []
    seen: set[tuple[str, str | None, str | None, str]] = set()
    for issue in issues:
        signature = (issue.code, issue.node_id, issue.field, issue.message)
        if signature in seen:
            continue
        seen.add(signature)
        result.append(issue)
    return result
