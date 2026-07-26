from __future__ import annotations

import re
from typing import Any, Literal

from pydantic import Field

from app.agent.state import StrictModel
from app.dify.version import DifyVersionInfo


CompatibilityLevel = Literal["supported", "diagnostic_only"]


class DifyCompatibilityRule(StrictModel):
    id: str = Field(min_length=1, max_length=128)
    dify_version_pattern: str = Field(min_length=1, max_length=256)
    dsl_versions: set[str] = Field(min_length=1)
    app_modes: set[str] = Field(min_length=1)
    level: Literal["supported"] = "supported"
    candidate_graph_draft_run_supported: bool = False
    create_import_idempotency_supported: bool = False
    create_import_reconciliation_lookup_supported: bool = False
    notes: str = Field(default="", max_length=2_000)


class DifyCompatibilityDecision(StrictModel):
    matrix_version: str
    rule_id: str | None = None
    dify_version: str
    dsl_version: str
    app_mode: str
    level: CompatibilityLevel
    mutation_supported: bool
    diagnostic_supported: bool = True
    candidate_graph_draft_run_supported: bool = False
    create_import_idempotency_supported: bool = False
    create_import_reconciliation_lookup_supported: bool = False
    reason: str


class DifyCompatibilityMatrix:
    version = "2026-07-26"

    def __init__(
        self,
        rules: list[DifyCompatibilityRule] | None = None,
    ) -> None:
        self.rules = rules or default_compatibility_rules()

    def decide(
        self,
        version: DifyVersionInfo,
        *,
        app_mode: str,
    ) -> DifyCompatibilityDecision:
        dify_version = normalize_dify_version(version.git_describe)
        for rule in self.rules:
            if (
                re.fullmatch(rule.dify_version_pattern, dify_version)
                and version.app_dsl_version in rule.dsl_versions
                and app_mode in rule.app_modes
            ):
                return DifyCompatibilityDecision(
                    matrix_version=self.version,
                    rule_id=rule.id,
                    dify_version=dify_version,
                    dsl_version=version.app_dsl_version,
                    app_mode=app_mode,
                    level="supported",
                    mutation_supported=True,
                    candidate_graph_draft_run_supported=(
                        rule.candidate_graph_draft_run_supported
                    ),
                    create_import_idempotency_supported=(
                        rule.create_import_idempotency_supported
                    ),
                    create_import_reconciliation_lookup_supported=(
                        rule.create_import_reconciliation_lookup_supported
                    ),
                    reason=rule.notes or "Matched a tested compatibility rule.",
                )
        return DifyCompatibilityDecision(
            matrix_version=self.version,
            dify_version=dify_version,
            dsl_version=version.app_dsl_version,
            app_mode=app_mode,
            level="diagnostic_only",
            mutation_supported=False,
            reason=(
                "No tested Dify/DSL/app-mode compatibility rule matched; "
                "read and diagnostic behavior remains available, but mutation "
                "must fail closed."
            ),
        )

    def pin_capabilities(
        self,
        capabilities: list[dict[str, Any]],
        *,
        decision: DifyCompatibilityDecision,
    ) -> list[dict[str, Any]]:
        compatibility = decision.model_dump(mode="json")
        return [
            {
                **capability,
                "compatibility": compatibility,
            }
            for capability in capabilities
        ]


def default_compatibility_rules() -> list[DifyCompatibilityRule]:
    all_modes = {
        "workflow",
        "advanced-chat",
        "chat",
        "completion",
        "agent-chat",
    }
    return [
        DifyCompatibilityRule(
            id="dify-1.14-dsl-0.6",
            dify_version_pattern=r"1\.14(?:\.\d+)?",
            dsl_versions={"0.6.0"},
            app_modes=all_modes,
            candidate_graph_draft_run_supported=False,
            create_import_idempotency_supported=False,
            create_import_reconciliation_lookup_supported=False,
            notes=(
                "Tested against Dify 1.14.2 Console APIs and application DSL "
                "0.6.0. Draft Run cannot receive a candidate Graph, and app "
                "import has neither request idempotency nor a reconciliation "
                "lookup keyed by a client token."
            ),
        ),
        DifyCompatibilityRule(
            id="deterministic-test-fixture",
            dify_version_pattern=r"test",
            dsl_versions={"9.9.9"},
            app_modes=all_modes,
            candidate_graph_draft_run_supported=True,
            create_import_idempotency_supported=True,
            create_import_reconciliation_lookup_supported=True,
            notes=(
                "Repository-only deterministic fixture; never advertised as "
                "a production Dify version."
            ),
        ),
    ]


def normalize_dify_version(git_describe: str) -> str:
    value = str(git_describe or "").strip()
    match = re.search(r"(?<!\d)(\d+\.\d+(?:\.\d+)?)(?!\d)", value)
    if match:
        return match.group(1)
    return value or "unknown"
