from app.compiler.dify import _node_output_types
from app.node_outputs import node_output_types, repair_plan_references
from app.validator import _outputs_for_node


def test_shared_output_registry_keeps_compiler_and_validator_consistent() -> None:
    cases = [
        ("llm", {}, {"text": "string"}),
        (
            "code",
            {"outputs": {"score": {"type": "number", "children": None}}},
            {"score": "number"},
        ),
        (
            "parameter-extractor",
            {"parameters": [{"name": "order_id", "type": "string"}]},
            {
                "order_id": "string",
                "__is_success": "boolean",
                "__reason": "string",
                "__usage": "object",
            },
        ),
        (
            "tool",
            {
                "output_schema": {
                    "properties": {"summary": {"type": "string"}}
                }
            },
            {
                "text": "string",
                "files": "array[file]",
                "json": "object",
                "summary": "string",
            },
        ),
        ("question-classifier", {}, {}),
    ]

    for node_type, params, expected in cases:
        assert node_output_types(node_type, params) == expected
        assert _node_output_types(
            type("Node", (), {"type": node_type, "params": params})()
        ) == expected
        assert _outputs_for_node(node_type, params) == set(expected)


def test_reference_repair_covers_text_selectors_containers_and_nested_inputs() -> None:
    payload = {
        "nodes": [
            {"id": "llm", "type": "llm", "params": {}},
            {"id": "format", "type": "template-transform", "params": {}},
            {"id": "classify", "type": "question-classifier", "params": {}},
            {
                "id": "code",
                "type": "code",
                "params": {
                    "outputs": {"result": {"type": "string", "children": None}}
                },
            },
            {
                "id": "tool",
                "type": "tool",
                "params": {
                    "tool_parameters": {
                        "query": {"type": "variable", "value": ["llm", "answer"]}
                    }
                },
            },
            {
                "id": "agent",
                "type": "agent",
                "params": {
                    "agent_parameters": {
                        "instruction": {
                            "type": "constant",
                            "value": "格式化结果：{{#format.text#}}",
                        }
                    }
                },
            },
            {
                "id": "batch",
                "type": "iteration",
                "params": {
                    "children": [
                        {
                            "id": "child_llm",
                            "type": "llm",
                            "params": {"user_prompt": "{{#llm.output#}}"},
                        }
                    ],
                    "edges": [],
                    "output_selector": ["child_llm", "answer"],
                },
            },
            {
                "id": "answer",
                "type": "answer",
                "params": {
                    "answer": (
                        "{{#llm.answer#}} "
                        "{{#classify.class_name#}} "
                        "{{#code.text#}}"
                    )
                },
            },
        ]
    }

    repaired = repair_plan_references(payload)
    by_id = {node["id"]: node for node in repaired.payload["nodes"]}

    assert by_id["tool"]["params"]["tool_parameters"]["query"]["value"] == [
        "llm",
        "text",
    ]
    assert (
        by_id["agent"]["params"]["agent_parameters"]["instruction"]["value"]
        == "格式化结果：{{#format.output#}}"
    )
    assert (
        by_id["batch"]["params"]["children"][0]["params"]["user_prompt"]
        == "{{#llm.text#}}"
    )
    assert by_id["batch"]["params"]["output_selector"] == [
        "child_llm",
        "text",
    ]
    answer = by_id["answer"]["params"]["answer"]
    assert "{{#llm.text#}}" in answer
    assert "{{#classify.class_name#}}" in answer
    assert "{{#code.text#}}" in answer
    assert len(repaired.actions) == 5


def test_reference_repair_does_not_guess_dynamic_or_unknown_outputs() -> None:
    payload = {
        "nodes": [
            {
                "id": "http",
                "type": "http-request",
                "params": {"url": "https://example.com/{{#missing.text#}}"},
            },
            {
                "id": "answer",
                "type": "answer",
                "params": {"answer": "{{#http.text#}}"},
            },
        ]
    }

    repaired = repair_plan_references(payload)

    assert repaired.payload == payload
    assert repaired.actions == []
