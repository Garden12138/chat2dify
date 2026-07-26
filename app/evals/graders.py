from __future__ import annotations

from app.evals.models import (
    EvaluationCaseResult,
    EvaluationGates,
    EvaluationMetrics,
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
