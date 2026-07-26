from __future__ import annotations

import argparse
from collections.abc import Iterable
import json
from pathlib import Path
from typing import Protocol

from app.evals.graders import (
    aggregate_metrics,
    evaluate_release_gates,
    grade_case,
)
from app.evals.models import (
    EvaluationCase,
    EvaluationCaseResult,
    EvaluationReport,
)


CASES_DIR = Path(__file__).resolve().parent / "cases"
DEFAULT_REPORT = (
    Path(__file__).resolve().parent
    / "reports"
    / "phase4-release.json"
)


class EvaluationExecutor(Protocol):
    name: str
    live_provider: bool
    reproducible: bool

    def execute(self, case: EvaluationCase) -> EvaluationCaseResult: ...


class FixtureEvaluationExecutor:
    name = "deterministic-fixture-replay"
    live_provider = False
    reproducible = True

    def execute(self, case: EvaluationCase) -> EvaluationCaseResult:
        return grade_case(case)


class EvaluationRunner:
    def __init__(
        self,
        *,
        executor: EvaluationExecutor | None = None,
        allow_live_provider: bool = False,
    ) -> None:
        self.executor = executor or FixtureEvaluationExecutor()
        if self.executor.live_provider and not allow_live_provider:
            raise ValueError(
                "Live-provider evaluation requires explicit opt-in."
            )

    def run(
        self,
        cases: Iterable[EvaluationCase],
        *,
        suite_version: str,
    ) -> EvaluationReport:
        ordered = sorted(cases, key=lambda case: case.id)
        results = [
            self.executor.execute(case)
            for case in ordered
        ]
        metrics = aggregate_metrics(results)
        return EvaluationReport(
            suite_version=suite_version,
            executor=self.executor.name,
            live_provider=self.executor.live_provider,
            reproducible=self.executor.reproducible,
            cases=results,
            metrics=metrics,
            gates=evaluate_release_gates(metrics),
        )


def load_cases(path: Path = CASES_DIR) -> list[EvaluationCase]:
    cases = []
    for case_file in sorted(path.glob("*.json")):
        payload = json.loads(case_file.read_text(encoding="utf-8"))
        cases.append(EvaluationCase.model_validate(payload))
    if not cases:
        raise ValueError(f"No evaluation cases found under {path}.")
    return cases


def write_report(report: EvaluationReport, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = json.dumps(
        report.model_dump(mode="json"),
        ensure_ascii=False,
        sort_keys=True,
        indent=2,
    )
    path.write_text(f"{payload}\n", encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Run deterministic Chat2Dify Builder Agent evaluations."
    )
    parser.add_argument(
        "--cases",
        type=Path,
        default=CASES_DIR,
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=DEFAULT_REPORT,
    )
    parser.add_argument(
        "--suite-version",
        default="phase4-1.0.0",
    )
    arguments = parser.parse_args()
    report = EvaluationRunner().run(
        load_cases(arguments.cases),
        suite_version=arguments.suite_version,
    )
    write_report(report, arguments.output)
    print(
        json.dumps(
            {
                "output": str(arguments.output),
                "gates_passed": report.gates.passed,
                "metrics": report.metrics.model_dump(mode="json"),
            },
            ensure_ascii=False,
            sort_keys=True,
        )
    )
    return 0 if report.gates.passed else 1


if __name__ == "__main__":
    raise SystemExit(main())
