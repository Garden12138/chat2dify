from __future__ import annotations

from app.evals.models import (
    EvaluationCase,
    EvaluationCaseResult,
    EvaluationGates,
    EvaluationMetrics,
)


def grade_case(case: EvaluationCase) -> EvaluationCaseResult:
    result = case.expected_result
    observed_changes = set(result.changes)
    required_present = set(case.required_changes).issubset(observed_changes)
    forbidden_absent = not (
        set(case.forbidden_changes) & observed_changes
    )
    readable_trace = bool(
        result.trace
        and all(
            isinstance(event.get("type"), str)
            and isinstance(event.get("message"), str)
            and event.get("type")
            and event.get("message")
            for event in result.trace
        )
    )
    structured_terminal = (
        result.status != "failed"
        or bool(
            isinstance(result.terminal_reason, dict)
            and result.terminal_reason.get("code")
            and result.terminal_reason.get("message")
        )
    )
    invariant_passed = (
        result.unapproved_writes == 0
        and result.incorrect_conflict_overwrites == 0
        and result.unrelated_preserved <= result.unrelated_total
        and forbidden_absent
    )
    goal_completed = (
        result.status == "completed"
        and result.reviewable
        and result.final_valid
        and required_present
        and forbidden_absent
        and invariant_passed
    )
    return EvaluationCaseResult(
        case_id=case.id,
        case_version=case.version,
        goal=case.goal,
        app_mode=case.app_mode,
        status=result.status,
        reviewable=result.reviewable,
        final_valid=result.final_valid,
        goal_completed=goal_completed,
        required_changes_present=required_present,
        forbidden_changes_absent=forbidden_absent,
        invariant_passed=invariant_passed,
        unrelated_total=result.unrelated_total,
        unrelated_preserved=result.unrelated_preserved,
        repairable_failure=result.repairable_failure,
        auto_repaired=result.auto_repaired,
        unapproved_writes=result.unapproved_writes,
        incorrect_conflict_overwrites=(
            result.incorrect_conflict_overwrites
        ),
        readable_trace=readable_trace,
        structured_terminal_reason=structured_terminal,
        trace_event_count=len(result.trace),
        terminal_reason=result.terminal_reason,
    )


def aggregate_metrics(
    results: list[EvaluationCaseResult],
) -> EvaluationMetrics:
    reviewable = [result for result in results if result.reviewable]
    repairable = [
        result
        for result in results
        if result.repairable_failure
    ]
    failed = [result for result in results if result.status == "failed"]
    unrelated_total = sum(
        result.unrelated_total
        for result in results
    )
    unrelated_preserved = sum(
        result.unrelated_preserved
        for result in results
    )
    return EvaluationMetrics(
        case_count=len(results),
        completed_count=sum(
            result.status == "completed"
            for result in results
        ),
        failed_count=len(failed),
        reviewable_count=len(reviewable),
        final_validity_rate=_rate(
            sum(result.final_valid for result in reviewable),
            len(reviewable),
        ),
        goal_completion_rate=_rate(
            sum(result.goal_completed for result in results),
            len(results),
        ),
        unrelated_preservation_rate=_rate(
            unrelated_preserved,
            unrelated_total,
        ),
        auto_repair_rate=_rate(
            sum(result.auto_repaired for result in repairable),
            len(repairable),
        ),
        failures_with_readable_trace_rate=_rate(
            sum(
                result.readable_trace
                and result.structured_terminal_reason
                for result in failed
            ),
            len(failed),
        ),
        unapproved_writes=sum(
            result.unapproved_writes
            for result in results
        ),
        incorrect_conflict_overwrites=sum(
            result.incorrect_conflict_overwrites
            for result in results
        ),
    )


def evaluate_release_gates(metrics: EvaluationMetrics) -> EvaluationGates:
    values = {
        "final_validity": metrics.final_validity_rate == 1,
        "goal_completion": metrics.goal_completion_rate >= 0.8,
        "unrelated_preservation": (
            metrics.unrelated_preservation_rate >= 0.95
        ),
        "auto_repair": metrics.auto_repair_rate >= 0.6,
        "readable_failures": (
            metrics.failures_with_readable_trace_rate == 1
        ),
        "unapproved_writes": metrics.unapproved_writes == 0,
        "conflict_overwrites": (
            metrics.incorrect_conflict_overwrites == 0
        ),
    }
    return EvaluationGates(
        **values,
        passed=all(values.values()),
    )


def _rate(numerator: int, denominator: int) -> float:
    if denominator == 0:
        return 1.0
    return round(numerator / denominator, 6)
