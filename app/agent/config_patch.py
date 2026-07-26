from __future__ import annotations

import json
from typing import Annotated, Any, Literal

from pydantic import Field, field_validator, model_validator

from app.agent.state import StrictModel


ConfigAppMode = Literal["chat", "completion", "agent-chat"]
ConfigRisk = Literal["low", "medium", "high"]


class ConfigModelValue(StrictModel):
    provider: str = Field(min_length=1, max_length=512)
    name: str = Field(min_length=1, max_length=512)
    mode: str = Field(default="chat", min_length=1, max_length=128)
    completion_params: dict[str, Any] = Field(default_factory=dict)

    @field_validator("completion_params")
    @classmethod
    def validate_completion_params(
        cls,
        value: dict[str, Any],
    ) -> dict[str, Any]:
        _validate_json_payload(value, field_name="completion_params")
        return value


class ConfigToolBinding(StrictModel):
    provider_id: str = Field(min_length=1, max_length=512)
    tool_name: str = Field(min_length=1, max_length=512)
    parameters: dict[str, Any] = Field(default_factory=dict)

    @field_validator("parameters")
    @classmethod
    def validate_parameters(
        cls,
        value: dict[str, Any],
    ) -> dict[str, Any]:
        _validate_json_payload(value, field_name="parameters")
        return value


class ConfigPromptSet(StrictModel):
    op: Literal["config.prompt.set"]
    value: str = Field(max_length=65_536)
    expected: str | None = Field(default=None, max_length=65_536)
    check_expected: bool = False


class ConfigModelSet(StrictModel):
    op: Literal["config.model.set"]
    value: ConfigModelValue
    expected: ConfigModelValue | None = None
    check_expected: bool = False


class ConfigExperienceSet(StrictModel):
    op: Literal["config.experience.set"]
    opening_statement: str | None = Field(default=None, max_length=16_384)
    suggested_questions: list[str] | None = Field(
        default=None,
        max_length=20,
    )
    file_upload: dict[str, Any] | None = None
    dataset_configs: dict[str, Any] | None = None
    expected: dict[str, Any] | None = None
    check_expected: bool = False

    @field_validator(
        "suggested_questions",
    )
    @classmethod
    def validate_suggested_questions(
        cls,
        value: list[str] | None,
    ) -> list[str] | None:
        if value is None:
            return value
        normalized = []
        for question in value:
            item = question.strip()
            if not item or len(item) > 2_000:
                raise ValueError(
                    "Suggested questions must contain 1 to 2000 characters."
                )
            normalized.append(item)
        return normalized

    @field_validator("file_upload", "dataset_configs", "expected")
    @classmethod
    def validate_json_fields(
        cls,
        value: dict[str, Any] | None,
        info,
    ) -> dict[str, Any] | None:
        if value is not None:
            _validate_json_payload(value, field_name=info.field_name)
        return value

    @model_validator(mode="after")
    def require_update(self) -> "ConfigExperienceSet":
        if (
            self.opening_statement is None
            and self.suggested_questions is None
            and self.file_upload is None
            and self.dataset_configs is None
        ):
            raise ValueError(
                "config.experience.set requires at least one explicit field."
            )
        return self


class ConfigAgentSet(StrictModel):
    op: Literal["config.agent.set"]
    enabled: bool = True
    strategy: str = Field(default="react", min_length=1, max_length=256)
    prompt: str | None = Field(default=None, max_length=65_536)
    tools: list[ConfigToolBinding] | None = Field(default=None, max_length=100)
    expected: dict[str, Any] | None = None
    check_expected: bool = False

    @field_validator("expected")
    @classmethod
    def validate_expected(
        cls,
        value: dict[str, Any] | None,
    ) -> dict[str, Any] | None:
        if value is not None:
            _validate_json_payload(value, field_name="expected")
        return value


ConfigPatchOperation = Annotated[
    ConfigPromptSet
    | ConfigModelSet
    | ConfigExperienceSet
    | ConfigAgentSet,
    Field(discriminator="op"),
]


class ConfigPatchDocument(StrictModel):
    workspace_version: str = Field(min_length=1, max_length=128)
    expected_base_hash: str = Field(min_length=1, max_length=512)
    app_mode: ConfigAppMode
    operations: list[ConfigPatchOperation] = Field(
        min_length=1,
        max_length=50,
    )
    rationale: str = Field(min_length=1, max_length=8_000)

    @model_validator(mode="after")
    def validate_mode_operations(self) -> "ConfigPatchDocument":
        if self.app_mode != "agent-chat" and any(
            isinstance(operation, ConfigAgentSet)
            for operation in self.operations
        ):
            raise ValueError(
                "config.agent.set is valid only for agent-chat applications."
            )
        return self


def config_operation_risk(operation: ConfigPatchOperation) -> ConfigRisk:
    if isinstance(operation, ConfigAgentSet):
        return "high" if operation.tools is not None else "medium"
    if isinstance(operation, ConfigModelSet):
        return "medium"
    if isinstance(operation, ConfigExperienceSet):
        if operation.dataset_configs is not None:
            return "medium"
        return "low"
    return "low"


def config_patch_risk(patch: ConfigPatchDocument) -> ConfigRisk:
    risks = [config_operation_risk(operation) for operation in patch.operations]
    if "high" in risks:
        return "high"
    if "medium" in risks:
        return "medium"
    return "low"


def _validate_json_payload(value: Any, *, field_name: str) -> None:
    try:
        encoded = json.dumps(
            value,
            ensure_ascii=False,
            separators=(",", ":"),
            allow_nan=False,
        ).encode("utf-8")
    except (TypeError, ValueError) as exc:
        raise ValueError(
            f"{field_name} must contain JSON-compatible values."
        ) from exc
    if len(encoded) > 131_072:
        raise ValueError(f"{field_name} exceeds the 131072-byte limit.")
