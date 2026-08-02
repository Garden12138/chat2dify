from __future__ import annotations

from typing import get_args

import pytest
from pydantic import BaseModel, ConfigDict, Field, ValidationError

from app.agent.catalog import NodeCapabilityCatalog
from app.agent.patch import AddNode, PatchDocument, RemoveNode
from app.agent.registry import ToolExecutionContext, ToolRegistry
from app.models import NodeType


class InspectInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    node_id: str = Field(min_length=1)


class InspectOutput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    title: str


def test_registry_rejects_unknown_and_invalid_input_before_execution() -> None:
    calls: list[str] = []
    registry = ToolRegistry()

    def executor(arguments: InspectInput, context: ToolExecutionContext):
        calls.append(context.call_id)
        return InspectOutput(title=f"Node {arguments.node_id}")

    spec = registry.register(
        name="workflow.inspect",
        version="1.0.0",
        description="Inspect one workspace node.",
        side_effect="none",
        approval="never",
        input_model=InspectInput,
        output_model=InspectOutput,
        executor=executor,
    )

    unknown = registry.execute("workflow.missing", {"node_id": "llm-1"})
    invalid = registry.execute("workflow.inspect", {"unexpected": True})
    valid = registry.execute("workflow.inspect", {"node_id": "llm-1"})

    assert unknown.error is not None
    assert unknown.error.code == "TOOL_UNKNOWN"
    assert invalid.error is not None
    assert invalid.error.code == "TOOL_INPUT_INVALID"
    assert calls == [valid.call_id]
    assert valid.ok is True
    assert valid.observation == {"title": "Node llm-1"}
    assert spec.side_effect == "none"
    assert spec.approval == "never"
    assert spec.input_schema["additionalProperties"] is False
    with pytest.raises(ValueError, match="already registered"):
        registry.register(
            name="workflow.inspect",
            version="1.0.0",
            description="Duplicate.",
            side_effect="none",
            approval="never",
            input_model=InspectInput,
            output_model=InspectOutput,
            executor=executor,
        )


def test_registry_validates_output_and_hides_executor_errors() -> None:
    registry = ToolRegistry()
    registry.register(
        name="workflow.inspect",
        version="1.0.0",
        description="Inspect one workspace node.",
        side_effect="none",
        approval="never",
        input_model=InspectInput,
        output_model=InspectOutput,
        executor=lambda _arguments, _context: {"wrong": True},
    )
    invalid_output = registry.execute("workflow.inspect", {"node_id": "llm-1"})

    assert invalid_output.error is not None
    assert invalid_output.error.code == "TOOL_OUTPUT_INVALID"


def test_node_capability_catalog_covers_v3_top_level_node_families() -> None:
    catalog = NodeCapabilityCatalog()

    assert [item.type for item in catalog.list()] == [
        "agent",
        "answer",
        "assigner",
        "code",
        "datasource",
        "datasource-empty",
        "document-extractor",
        "end",
        "http-request",
        "human-input",
        "if-else",
        "iteration",
        "knowledge-index",
        "knowledge-retrieval",
        "list-operator",
        "llm",
        "loop",
        "parameter-extractor",
        "question-classifier",
        "start",
        "template-transform",
        "tool",
        "trigger-plugin",
        "trigger-schedule",
        "trigger-webhook",
        "variable-aggregator",
    ]
    assert all(item.capability_version == "1.0.0" for item in catalog.list())
    assert {item.type for item in catalog.list()} == (
        set(get_args(NodeType)) - {"iteration-start", "loop-start", "loop-end"}
    )
    assert catalog.require("llm").side_effect == "model_cost"
    assert catalog.require("human-input").side_effect == "external"
    assert catalog.require("knowledge-retrieval").dify_version_range == "1.14.x"
    assert catalog.require("llm").output_schema["properties"]["text"]["type"] == "string"
    assert [item.type for item in catalog.search("conditional")] == ["if-else"]
    assert "end" in [item.type for item in catalog.search(app_mode="workflow")]
    assert "answer" not in [item.type for item in catalog.search(app_mode="workflow")]
    assert "answer" in [item.type for item in catalog.search(app_mode="advanced-chat")]
    assert "end" not in [item.type for item in catalog.search(app_mode="advanced-chat")]
    assert catalog.require("start").removable is False
    assert catalog.require("iteration").container is True
    assert catalog.require("trigger-webhook").mutation_operations == {
        "node.add",
        "node.update",
    }
    with pytest.raises(KeyError):
        catalog.require("iteration-start")


def test_patch_schema_is_explicit_bounded_and_supports_temp_refs() -> None:
    patch = PatchDocument.model_validate(
        {
            "workspace_version": "v0",
            "expected_base_hash": "hash-0",
            "rationale": "Add a reviewed LLM branch.",
            "operations": [
                {
                    "op": "node.add",
                    "temp_ref": "tmp_llm",
                    "node_type": "llm",
                    "title": "Generate response",
                    "params": {"prompt_template": [{"role": "system", "text": "Be concise."}]},
                },
                {
                    "op": "edge.add",
                    "source": "start-1",
                    "target": "tmp_llm",
                },
                {
                    "op": "node.update",
                    "node_id": "end-1",
                    "set": {"params": {"outputs": [{"value_selector": ["tmp_llm", "text"]}]}},
                    "expected": {"type": "end"},
                },
            ],
        }
    )

    assert isinstance(patch.operations[0], AddNode)
    assert patch.operations[0].temp_ref == "tmp_llm"
    assert patch.model_dump(mode="json", by_alias=True)["operations"][2]["set"] == {
        "params": {"outputs": [{"value_selector": ["tmp_llm", "text"]}]}
    }
    removal = PatchDocument.model_validate(
        {
            "workspace_version": "v0",
            "expected_base_hash": "hash-0",
            "rationale": "Remove an explicitly matched obsolete node.",
            "operations": [
                {
                    "op": "node.remove",
                    "node_id": "obsolete-1",
                    "expected_type": "code",
                    "expected_title": "旧转换",
                }
            ],
        }
    )
    assert isinstance(removal.operations[0], RemoveNode)
    variable_patch = PatchDocument.model_validate(
        {
            "workspace_version": "v0",
            "expected_base_hash": "hash-0",
            "rationale": "Add and update a typed conversation variable.",
            "operations": [
                {
                    "op": "conversation_variable.add",
                    "name": "customer_tier",
                    "value_type": "string",
                    "value": "standard",
                },
                {
                    "op": "conversation_variable.update",
                    "variable_id": "11111111-1111-4111-8111-111111111111",
                    "set": {"description": "Preserved customer tier."},
                    "expected_name": "customer_tier",
                },
                {
                    "op": "conversation_variable.remove",
                    "variable_id": "11111111-1111-4111-8111-111111111111",
                    "expected_name": "customer_tier",
                },
            ],
        }
    )
    assert [
        operation.op for operation in variable_patch.operations
    ] == [
        "conversation_variable.add",
        "conversation_variable.update",
        "conversation_variable.remove",
    ]

    with pytest.raises(ValidationError):
        PatchDocument.model_validate(
            {
                "workspace_version": "v0",
                "expected_base_hash": "hash-0",
                "rationale": "Unknown operation.",
                "operations": [{"op": "json.patch", "path": "/nodes/0", "value": {}}],
            }
        )
    with pytest.raises(ValidationError, match="forbidden key"):
        PatchDocument.model_validate(
            {
                "workspace_version": "v0",
                "expected_base_hash": "hash-0",
                "rationale": "Dangerous shape.",
                "operations": [
                    {
                        "op": "node.add",
                        "temp_ref": "tmp_bad",
                        "node_type": "llm",
                        "title": "Bad",
                        "params": {"__proto__": {"polluted": True}},
                    }
                ],
            }
        )
    with pytest.raises(ValidationError):
        PatchDocument.model_validate(
            {
                "workspace_version": "v0",
                "expected_base_hash": "hash-0",
                "rationale": "Too many operations.",
                "operations": [
                    {
                        "op": "node.add",
                        "temp_ref": f"tmp_{index}",
                        "node_type": "llm",
                        "title": f"Node {index}",
                        "params": {},
                    }
                    for index in range(51)
                ],
            }
        )
