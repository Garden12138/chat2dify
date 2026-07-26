from __future__ import annotations

from collections import Counter
from typing import Any

from app.agent.approval import AgentApprovalService
from app.agent.context import BuilderContextBuilder
from app.agent.decision import (
    AgentDecisionProvider,
    DecisionOutcome,
    DecisionProviderError,
)
from app.agent.policy import AgentToolPolicy
from app.agent.registry import ToolRegistry, ToolResult
from app.agent.review import WorkflowReviewService
from app.agent.snapshot import WorkflowSnapshotService
from app.agent.state import (
    AgentBudgetUsage,
    AgentRun,
    GoalPlan,
    GoalStep,
    Observation,
    RunPhase,
    utc_now,
)
from app.agent.store import AgentStore, AgentStoreConflict
from app.agent.trace import redact_sensitive_data
from app.agent.workspace import VersionedWorkflowWorkspace


class RepeatedAgentError(RuntimeError):
    code = "AGENT_REPEATED_ERROR"


class AgentRuntime:
    def __init__(
        self,
        *,
        store: AgentStore,
        snapshot: WorkflowSnapshotService,
        workspace: VersionedWorkflowWorkspace,
        review: WorkflowReviewService,
        approval: AgentApprovalService,
        registry: ToolRegistry,
        context_builder: BuilderContextBuilder,
        decision_provider: AgentDecisionProvider,
        policy: AgentToolPolicy,
    ) -> None:
        self.store = store
        self.snapshot = snapshot
        self.workspace = workspace
        self.review = review
        self.approval = approval
        self.registry = registry
        self.context_builder = context_builder
        self.decision_provider = decision_provider
        self.policy = policy

    def run(self, run_id: str) -> dict[str, Any]:
        try:
            run = self.store.get_run(run_id)
            if run.terminal or run.paused:
                return _run_summary(run)
            run = self._observe_if_needed(run)
            return self._decision_loop(run)
        except Exception as exc:  # noqa: BLE001 - persist a structured terminal state.
            return self._fail(run_id, exc)

    def _observe_if_needed(self, run: AgentRun) -> AgentRun:
        if run.phase == RunPhase.QUEUED:
            run = self.store.update_run(run.transition_to(RunPhase.OBSERVING))
            self.store.append_event(
                run_id=run.id,
                event_type="agent.started",
                phase=run.phase.value,
                message="Builder Agent started initializing its Workflow context.",
                data={"goal": run.goal},
            )
        if run.phase != RunPhase.OBSERVING:
            return run
        session = self.store.get_session(run.session_id)
        snapshot = self.snapshot.capture(session)
        goal_plan = _initial_goal_plan(run.goal, operation=snapshot.operation)
        try:
            run, version = self.workspace.initialize(run, snapshot, goal_plan)
        except AgentStoreConflict:
            current = self.store.get_run(run.id)
            if current.terminal or current.paused:
                return current
            raise
        self.store.append_event(
            run_id=run.id,
            event_type="context.loaded",
            phase=run.phase.value,
            message=(
                "Loaded the authoritative Dify Snapshot and pinned capabilities."
                if snapshot.operation == "modify"
                else (
                    "Initialized the deterministic new-app scaffold and pinned "
                    "capabilities."
                )
            ),
            data={
                "operation": snapshot.operation,
                "app_id": snapshot.app_id,
                "app_mode": snapshot.app_mode,
                "base_hash": snapshot.base_hash,
                "workspace_version_id": version.id,
                "node_count": len(snapshot.base_plan.get("nodes") or []),
                "edge_count": len(snapshot.base_plan.get("edges") or []),
                "capability_count": len(snapshot.capabilities),
            },
        )
        self.store.append_event(
            run_id=run.id,
            event_type="goal_plan.created",
            phase=run.phase.value,
            message="Created the initial Goal Plan.",
            data=goal_plan.model_dump(mode="json"),
        )
        return self.store.update_run(run.transition_to(RunPhase.PLANNING))

    def _decision_loop(self, initial: AgentRun) -> dict[str, Any]:
        run = initial
        while True:
            run = self.store.get_run(run.id)
            if run.terminal or run.paused:
                return _run_summary(run)
            exhaustion = _budget_exhaustion(run)
            if exhaustion:
                return self._budget_failed(run, exhaustion)
            context = self.context_builder.build(run)
            run = _with_usage(
                run,
                iterations=1,
            )
            run = AgentRun.model_validate(
                {
                    **run.model_dump(),
                    "iteration": run.iteration + 1,
                    "updated_at": utc_now(),
                }
            )
            run = self.store.update_run(run)
            try:
                raw_decision = self.decision_provider.decide(
                    context,
                    self.registry.visible_specs(),
                )
            except DecisionProviderError as exc:
                used_calls = max(1, exc.model_calls)
                run = self.store.update_run(
                    _with_usage(
                        self.store.get_run(run.id),
                        model_calls=used_calls,
                    )
                )
                raise
            if isinstance(raw_decision, DecisionOutcome):
                decision = raw_decision.decision
                used_calls = raw_decision.model_calls
            else:
                decision = raw_decision
                used_calls = 1
            run = self.store.update_run(
                _with_usage(
                    self.store.get_run(run.id),
                    model_calls=used_calls,
                )
            )
            self.store.append_event(
                run_id=run.id,
                event_type="agent.decision",
                phase=run.phase.value,
                message=f"Agent selected decision type {decision.type}.",
                data=redact_sensitive_data(decision.model_dump(mode="json")),
            )
            if run.phase == RunPhase.PAUSED:
                return _run_summary(run)
            if decision.type == "ask_user":
                paused = run.transition_to(RunPhase.WAITING_USER)
                paused = _with_observation(
                    paused,
                    Observation(
                        kind="ask_user",
                        summary=str(
                            redact_sensitive_data(decision.question)
                        ),
                        data=redact_sensitive_data(
                            {"missing": decision.missing}
                        ),
                    ),
                )
                paused = self.store.update_run(paused)
                self.store.append_event(
                    run_id=run.id,
                    event_type="agent.paused",
                    phase=paused.phase.value,
                    message="Agent Run paused for user input.",
                    data=decision.model_dump(mode="json"),
                )
                return _run_summary(paused)
            if decision.type == "finish":
                return self._finish_for_review(run, decision.model_dump(mode="json"))
            run = self._execute_tool(run, decision)

    def _execute_tool(self, run: AgentRun, decision) -> AgentRun:
        current = self.store.get_run(run.id)
        if current.terminal or current.phase == RunPhase.PAUSED:
            return current
        run = current
        if run.phase == RunPhase.PLANNING:
            run = self.store.update_run(run.transition_to(RunPhase.ACTING))
        registered = self.registry.get(decision.tool_name)
        if registered is not None:
            authorization = self.policy.authorize(registered.spec, run)
            if not authorization.allowed:
                result = ToolResult(
                    ok=False,
                    tool_name=decision.tool_name,
                    tool_version=registered.spec.version,
                    error={
                        "code": authorization.code or "TOOL_POLICY_DENIED",
                        "message": authorization.message
                        or "Tool was denied by server policy.",
                        "details": [],
                        "retryable": False,
                    },
                )
                return self._record_tool_result(run, decision, result)
        patch_operation_count = (
            len(decision.arguments.get("operations") or [])
            if decision.tool_name == "workflow.patch"
            else 0
        )
        if (
            run.budget_usage.patch_operations + patch_operation_count
            > run.budget.max_patch_operations
        ):
            return self._budget_failed_run(run, "max_patch_operations")
        self.store.append_event(
            run_id=run.id,
            event_type="tool.started",
            phase=run.phase.value,
            message=f"Tool {decision.tool_name} started.",
            data={
                "tool_name": decision.tool_name,
                "goal_step_id": decision.goal_step_id,
                "arguments": redact_sensitive_data(decision.arguments),
            },
        )
        result = self.registry.execute(
            decision.tool_name,
            decision.arguments,
            session_id=run.session_id,
            run_id=run.id,
        )
        run = self.store.get_run(run.id)
        if patch_operation_count:
            run = _with_usage(run, patch_operations=patch_operation_count)
            run = self.store.update_run(run)
        return self._record_tool_result(run, decision, result)

    def _record_tool_result(self, run: AgentRun, decision, result: ToolResult) -> AgentRun:
        run = self.store.get_run(run.id)
        previous_goal_revision = (
            run.goal_plan.revision
            if run.goal_plan is not None
            else None
        )
        self.store.append_event(
            run_id=run.id,
            event_type="tool.completed",
            phase=run.phase.value,
            message=(
                f"Tool {decision.tool_name} completed."
                if result.ok
                else f"Tool {decision.tool_name} failed with {result.error.code}."
            ),
            data=result.model_dump(mode="json"),
        )
        observation = Observation(
            kind=(
                f"tool.{decision.tool_name}.completed"
                if result.ok
                else f"tool.{decision.tool_name}.failed"
            ),
            summary=(
                f"{decision.tool_name} completed."
                if result.ok
                else str(result.error.message)
            ),
            data=(
                result.observation
                if result.ok
                else result.error.model_dump(mode="json")
            ),
        )
        run = _with_observation(run, observation)
        run = _update_goal_step(
            run,
            decision.goal_step_id,
            evidence=observation.summary,
            completed=result.ok,
        )
        if result.ok:
            run = _reset_same_error(run)
        else:
            run = _record_same_error(run, _tool_error_signature(result))
        run = self.store.update_run(run)
        if run.phase == RunPhase.PAUSED:
            return run
        if (
            run.goal_plan is not None
            and previous_goal_revision is not None
            and run.goal_plan.revision > previous_goal_revision
        ):
            self.store.append_event(
                run_id=run.id,
                event_type="goal_plan.updated",
                phase=run.phase.value,
                message="Updated Goal Plan evidence from the Tool result.",
                data=run.goal_plan.model_dump(mode="json"),
            )
        if decision.tool_name == "workflow.patch":
            if result.ok:
                validating = self.store.update_run(
                    run.transition_to(RunPhase.VALIDATING)
                )
                self.store.append_event(
                    run_id=run.id,
                    event_type="validation.started",
                    phase=validating.phase.value,
                    message="Running deterministic validation after accepted Patch.",
                    data={"workspace_version_id": result.workspace_version},
                )
                self.store.append_event(
                    run_id=run.id,
                    event_type="validation.passed",
                    phase=validating.phase.value,
                    message="Accepted Patch passed deterministic validation.",
                    data=result.observation.get("validation") or {},
                )
                run = self.store.update_run(
                    validating.transition_to(RunPhase.ACTING)
                )
            elif result.error.code == "WORKSPACE_PATCH_VALIDATION_FAILED":
                self.store.append_event(
                    run_id=run.id,
                    event_type="validation.failed",
                    phase=run.phase.value,
                    message="Rejected Patch failed deterministic validation; head unchanged.",
                    data={"issues": result.error.details},
                )
        if (
            not result.ok
            and run.budget_usage.same_error_retries
            > run.budget.max_same_error_retries
        ):
            self._raise_repeated_error(result.error.code)
        return run

    def _finish_for_review(
        self,
        run: AgentRun,
        finish_data: dict[str, Any],
    ) -> dict[str, Any]:
        if run.phase == RunPhase.PLANNING:
            run = self.store.update_run(run.transition_to(RunPhase.ACTING))
        validating = self.store.update_run(run.transition_to(RunPhase.VALIDATING))
        self.store.append_event(
            run_id=run.id,
            event_type="validation.started",
            phase=validating.phase.value,
            message="Running the final validation and review chain.",
            data={},
        )
        review = self.review.build(run.id)
        if not review.ready:
            self.store.append_event(
                run_id=run.id,
                event_type="validation.failed",
                phase=validating.phase.value,
                message="Final Workspace validation failed; Agent may repair it.",
                data=review.validation.model_dump(mode="json"),
            )
            acting = self.store.update_run(
                validating.transition_to(RunPhase.ACTING)
            )
            acting = _with_observation(
                acting,
                Observation(
                    kind="validation.failed",
                    summary="Final Workspace validation failed.",
                    data=review.validation.model_dump(mode="json"),
                ),
            )
            self.store.update_run(acting)
            return self._decision_loop(acting)
        self.store.append_event(
            run_id=run.id,
            event_type="validation.passed",
            phase=validating.phase.value,
            message="Final Workspace validation passed.",
            data=review.validation.model_dump(mode="json"),
        )
        ready = _complete_goal_plan(validating, finish_data)
        if ready.goal_plan is not None:
            self.store.append_event(
                run_id=run.id,
                event_type="goal_plan.updated",
                phase=validating.phase.value,
                message="Completed Goal Plan steps for Review.",
                data=ready.goal_plan.model_dump(mode="json"),
            )
        ready = AgentRun.model_validate(
            {
                **ready.transition_to(RunPhase.WAITING_APPROVAL).model_dump(),
                "review": review.model_dump(mode="json"),
            }
        )
        ready = self.store.update_run(ready)
        self.store.append_event(
            run_id=run.id,
            event_type="review.ready",
            phase=ready.phase.value,
            message="Business and technical review is ready.",
            data=review.model_dump(mode="json"),
        )
        approval = self.approval.request_for_review(run.id, review)
        self.store.append_event(
            run_id=run.id,
            event_type="agent.paused",
            phase=ready.phase.value,
            message="Agent Run paused for persisted user approval.",
            data={"approval_id": approval.id, "action": approval.action},
        )
        return _run_summary(ready)

    def _budget_failed(self, run: AgentRun, reason: str) -> dict[str, Any]:
        return _run_summary(self._budget_failed_run(run, reason))

    def _budget_failed_run(self, run: AgentRun, reason: str) -> AgentRun:
        review_data = None
        if run.head_version_id:
            try:
                review_data = self.review.build(run.id).model_dump(mode="json")
            except Exception:  # noqa: BLE001 - partial review is best effort.
                review_data = None
        failed = run.transition_to(
            RunPhase.FAILED,
            error={
                "code": "AGENT_BUDGET_EXHAUSTED",
                "message": f"Agent budget exhausted: {reason}.",
                "reason": reason,
                "partial_review": review_data,
            },
        )
        failed = self.store.update_run(failed)
        self.store.append_event(
            run_id=run.id,
            event_type="agent.failed",
            phase=failed.phase.value,
            message=f"Agent budget exhausted: {reason}.",
            data=failed.error or {},
        )
        return failed

    def _fail(self, run_id: str, exc: Exception) -> dict[str, Any]:
        run = self.store.get_run(run_id)
        if run.terminal:
            return _run_summary(run)
        code = getattr(exc, "code", "AGENT_RUNTIME_FAILED")
        failed = run.transition_to(
            RunPhase.FAILED,
            error={
                "code": str(code),
                "message": f"Agent Runtime failed with {exc.__class__.__name__}.",
            },
        )
        failed = self.store.update_run(failed)
        self.store.append_event(
            run_id=run.id,
            event_type="agent.failed",
            phase=failed.phase.value,
            message="Agent Runtime failed.",
            data=failed.error or {},
        )
        return _run_summary(failed)

    @staticmethod
    def _raise_repeated_error(code: str) -> None:
        raise RepeatedAgentError(f"Repeated identical Tool error: {code}")


def _initial_goal_plan(
    goal: str,
    *,
    operation: str = "modify",
) -> GoalPlan:
    create_mode = operation == "create"
    return GoalPlan(
        goal=goal,
        constraints=[
            "Use only registered Typed Tools.",
            (
                "Do not import a Dify app before persisted approval."
                if create_mode
                else "Keep Dify unchanged before persisted approval."
            ),
            "Preserve unrelated nodes, edges, metadata, features, and variables.",
        ],
        success_criteria=[
            "The requested relevant nodes and edges are changed.",
            "The deterministic validation chain passes.",
            (
                "Review and risk data are ready before any Dify app exists."
                if create_mode
                else "Review and risk data are ready before Commit."
            ),
        ],
        steps=[
            GoalStep(
                id="observe",
                description=(
                    "Inspect the deterministic new-app scaffold."
                    if create_mode
                    else "Inspect the authoritative Workflow Snapshot."
                ),
                status="in_progress",
            ),
            GoalStep(
                id="patch",
                description="Apply the smallest transactional Patch.",
                depends_on=["observe"],
            ),
            GoalStep(
                id="validate",
                description="Run deterministic validation and repair if needed.",
                depends_on=["patch"],
            ),
            GoalStep(
                id="review",
                description="Prepare business Diff, technical Diff, and risk.",
                depends_on=["validate"],
            ),
        ],
    )


def _with_usage(
    run: AgentRun,
    *,
    iterations: int = 0,
    model_calls: int = 0,
    patch_operations: int = 0,
) -> AgentRun:
    usage = run.budget_usage
    updated_usage = AgentBudgetUsage.model_validate(
        {
            **usage.model_dump(),
            "iterations": usage.iterations + iterations,
            "model_calls": usage.model_calls + model_calls,
            "patch_operations": usage.patch_operations + patch_operations,
        }
    )
    return AgentRun.model_validate(
        {
            **run.model_dump(),
            "budget_usage": updated_usage.model_dump(),
            "updated_at": utc_now(),
        }
    )


def _with_observation(run: AgentRun, observation: Observation) -> AgentRun:
    observations = [*run.observations, observation][-200:]
    return AgentRun.model_validate(
        {
            **run.model_dump(),
            "observations": [
                item.model_dump(mode="json")
                for item in observations
            ],
            "updated_at": utc_now(),
        }
    )


def _update_goal_step(
    run: AgentRun,
    step_id: str,
    *,
    evidence: str,
    completed: bool,
) -> AgentRun:
    if run.goal_plan is None:
        return run
    steps = []
    matched = False
    for step in run.goal_plan.steps:
        payload = step.model_dump()
        if step.id == step_id:
            matched = True
            payload["status"] = "completed" if completed else "in_progress"
            payload["evidence"] = [*step.evidence, evidence][-100:]
        elif step.status == "pending" and not matched:
            payload["status"] = step.status
        steps.append(GoalStep.model_validate(payload))
    if not matched:
        return run
    goal_plan = GoalPlan.model_validate(
        {
            **run.goal_plan.model_dump(),
            "steps": [step.model_dump() for step in steps],
            "revision": run.goal_plan.revision + 1,
        }
    )
    return AgentRun.model_validate(
        {
            **run.model_dump(),
            "goal_plan": goal_plan.model_dump(),
            "updated_at": utc_now(),
        }
    )


def _complete_goal_plan(run: AgentRun, finish_data: dict[str, Any]) -> AgentRun:
    if run.goal_plan is None:
        return run
    evidence = [
        str(redact_sensitive_data(item))
        for item in finish_data.get("evidence") or []
    ]
    steps = [
        GoalStep.model_validate(
            {
                **step.model_dump(),
                "status": "completed",
                "evidence": [*step.evidence, *evidence][-100:],
            }
        )
        for step in run.goal_plan.steps
    ]
    goal_plan = GoalPlan.model_validate(
        {
            **run.goal_plan.model_dump(),
            "steps": [step.model_dump() for step in steps],
            "revision": run.goal_plan.revision + 1,
        }
    )
    return AgentRun.model_validate(
        {
            **run.model_dump(),
            "goal_plan": goal_plan.model_dump(),
            "updated_at": utc_now(),
        }
    )


def _record_same_error(run: AgentRun, signature: str) -> AgentRun:
    usage = run.budget_usage
    retries = (
        usage.same_error_retries + 1
        if usage.latest_error_signature == signature
        else 1
    )
    updated = AgentBudgetUsage.model_validate(
        {
            **usage.model_dump(),
            "same_error_retries": retries,
            "latest_error_signature": signature,
        }
    )
    return AgentRun.model_validate(
        {
            **run.model_dump(),
            "budget_usage": updated.model_dump(),
            "updated_at": utc_now(),
        }
    )


def _tool_error_signature(result: ToolResult) -> str:
    if result.error is None:
        return "TOOL_ERROR_UNKNOWN"
    detail = result.error.details[0] if result.error.details else {}
    return ":".join(
        [
            result.error.code,
            str(detail.get("code") or ""),
            str(detail.get("node_id") or ""),
            str(detail.get("field") or detail.get("path") or ""),
        ]
    ).rstrip(":")


def _reset_same_error(run: AgentRun) -> AgentRun:
    usage = AgentBudgetUsage.model_validate(
        {
            **run.budget_usage.model_dump(),
            "same_error_retries": 0,
            "latest_error_signature": None,
        }
    )
    return AgentRun.model_validate(
        {
            **run.model_dump(),
            "budget_usage": usage.model_dump(),
            "updated_at": utc_now(),
        }
    )


def _budget_exhaustion(run: AgentRun) -> str | None:
    usage = run.budget_usage
    if usage.iterations >= run.budget.max_iterations:
        return "max_iterations"
    if usage.model_calls >= run.budget.max_model_calls:
        return "max_model_calls"
    elapsed = (utc_now() - run.created_at).total_seconds()
    if elapsed >= run.budget.max_run_seconds:
        return "max_run_seconds"
    return None


def _run_summary(run: AgentRun) -> dict[str, Any]:
    return {
        "run_id": run.id,
        "status": run.status.value,
        "phase": run.phase.value,
        "head_version_id": run.head_version_id,
        "review": run.review,
        "error": run.error,
    }
