from __future__ import annotations

from typing import Any, Literal

from pydantic import Field

from app.agent.state import StrictModel
from app.models import AppMode, NodeType
from app.node_outputs import node_output_types


SideEffectClass = Literal["none", "model_cost", "external", "unknown"]


class NodeDefinition(StrictModel):
    type: NodeType
    supported_app_modes: set[AppMode] = Field(min_length=1)
    summary: str = Field(min_length=1, max_length=2_000)
    config_schema: dict[str, Any]
    output_schema: dict[str, Any]
    side_effect: SideEffectClass
    examples: list[dict[str, Any]] = Field(default_factory=list, max_length=20)
    dify_version_range: str | None = None


class NodeCapabilityCatalog:
    def __init__(self, definitions: list[NodeDefinition] | None = None) -> None:
        items = definitions or _mvp_definitions()
        self._definitions = {definition.type: definition for definition in items}
        if len(self._definitions) != len(items):
            raise ValueError("Node Capability definitions must use unique node types.")

    def get(self, node_type: str) -> NodeDefinition | None:
        return self._definitions.get(node_type)

    def require(self, node_type: str) -> NodeDefinition:
        definition = self.get(node_type)
        if definition is None:
            raise KeyError(node_type)
        return definition

    def search(
        self,
        query: str = "",
        *,
        app_mode: AppMode | None = None,
        limit: int = 20,
    ) -> list[NodeDefinition]:
        if limit < 1 or limit > 100:
            raise ValueError("Capability search limit must be between 1 and 100.")
        needle = query.strip().lower()
        matches = [
            definition
            for definition in self._definitions.values()
            if (app_mode is None or app_mode in definition.supported_app_modes)
            and (
                not needle
                or needle in definition.type.lower()
                or needle in definition.summary.lower()
            )
        ]
        return sorted(matches, key=lambda item: item.type)[:limit]

    def list(self) -> list[NodeDefinition]:
        return self.search(limit=100)


def _mvp_definitions() -> list[NodeDefinition]:
    shared_modes: set[AppMode] = {"workflow", "advanced-chat"}
    return [
        NodeDefinition(
            type="llm",
            supported_app_modes=shared_modes,
            summary="Invoke a configured language model and expose its generated text.",
            config_schema={
                "type": "object",
                "properties": {
                    "model": {"type": "object"},
                    "prompt_template": {"type": "array"},
                },
                "additionalProperties": True,
            },
            output_schema=_output_schema("llm"),
            side_effect="model_cost",
        ),
        NodeDefinition(
            type="if-else",
            supported_app_modes=shared_modes,
            summary="Route execution through deterministic conditional branches.",
            config_schema={
                "type": "object",
                "properties": {"cases": {"type": "array"}},
                "additionalProperties": True,
            },
            output_schema=_output_schema("if-else"),
            side_effect="none",
        ),
        NodeDefinition(
            type="end",
            supported_app_modes={"workflow"},
            summary="Return terminal outputs from a Workflow.",
            config_schema={
                "type": "object",
                "properties": {"outputs": {"type": "array"}},
                "additionalProperties": True,
            },
            output_schema=_output_schema("end"),
            side_effect="none",
        ),
        NodeDefinition(
            type="answer",
            supported_app_modes={"advanced-chat"},
            summary="Return a final conversational answer from a Chatflow.",
            config_schema={
                "type": "object",
                "properties": {"answer": {"type": "string"}},
                "additionalProperties": True,
            },
            output_schema=_output_schema("answer"),
            side_effect="none",
        ),
        _permissive_definition(
            "http-request",
            modes=shared_modes,
            summary=(
                "Call an explicit HTTP endpoint; execution has external "
                "network side effects and requires conservative Draft approval."
            ),
            side_effect="external",
        ),
        _permissive_definition(
            "document-extractor",
            modes=shared_modes,
            summary=(
                "Extract bounded text from a declared file or file-list input."
            ),
            side_effect="none",
        ),
        _permissive_definition(
            "knowledge-retrieval",
            modes=shared_modes,
            summary=(
                "Retrieve grounded context from explicitly pinned Dify dataset IDs."
            ),
            side_effect="none",
        ),
        _permissive_definition(
            "human-input",
            modes=shared_modes,
            summary=(
                "Pause for explicit human input or review through configured "
                "delivery methods."
            ),
            side_effect="external",
        ),
        _permissive_definition(
            "tool",
            modes=shared_modes,
            summary=(
                "Invoke an explicitly selected Dify Tool binding with reviewed "
                "parameters."
            ),
            side_effect="external",
        ),
    ]


def _permissive_definition(
    node_type: NodeType,
    *,
    modes: set[AppMode],
    summary: str,
    side_effect: SideEffectClass,
) -> NodeDefinition:
    return NodeDefinition(
        type=node_type,
        supported_app_modes=modes,
        summary=summary,
        config_schema={
            "type": "object",
            "properties": {},
            "additionalProperties": True,
        },
        output_schema=_output_schema(node_type),
        side_effect=side_effect,
        dify_version_range="1.14.x",
    )


def _output_schema(node_type: str) -> dict[str, Any]:
    outputs = node_output_types(node_type, {})
    return {
        "type": "object",
        "properties": {
            name: {"type": _json_schema_type(value_type)}
            for name, value_type in outputs.items()
        },
        "additionalProperties": False,
    }


def _json_schema_type(value_type: str) -> str:
    if value_type == "number":
        return "number"
    if value_type == "boolean":
        return "boolean"
    if value_type == "object":
        return "object"
    if value_type.startswith("array"):
        return "array"
    return "string"
