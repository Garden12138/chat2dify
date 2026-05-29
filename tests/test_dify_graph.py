import yaml
import pytest

from app.compiler.dify import DifyDslCompiler
from app.dify.graph import UnsupportedExistingNodeType, compile_plan_to_dify_graph, decompile_dify_graph
from app.models import WorkflowPlan


def _compiler() -> DifyDslCompiler:
    return DifyDslCompiler(
        dsl_version="9.9.9",
        default_model_provider="openai",
        default_model_name="gpt-4o-mini",
    )


def test_decompile_dify_graph_covers_supported_node_types() -> None:
    plan = _seven_type_plan()
    graph = yaml.safe_load(_compiler().compile(plan))["workflow"]["graph"]

    decompiled = decompile_dify_graph(graph, name="Loaded")

    assert decompiled.name == "Loaded"
    assert {node.type for node in decompiled.nodes} == {
        "start",
        "llm",
        "code",
        "if-else",
        "end",
        "http-request",
        "template-transform",
        "question-classifier",
        "parameter-extractor",
    }
    assert decompiled.nodes[0].params["variables"][0]["name"] == "query"
    assert next(node for node in decompiled.nodes if node.id == "llm_yes").params["user_prompt"]
    assert next(node for node in decompiled.nodes if node.id == "classifier").params["classes"][0]["id"] == "complaint"
    assert next(node for node in decompiled.nodes if node.id == "extract").params["parameters"][0]["name"] == "issue"


def test_compile_plan_to_dify_graph_preserves_existing_layout_and_places_new_nodes() -> None:
    base_plan = WorkflowPlan.model_validate(
        {
            "name": "base",
            "nodes": [
                {"id": "start", "type": "start", "params": {"variables": [{"name": "query"}]}},
                {"id": "llm", "type": "llm", "params": {"user_prompt": "{{#start.query#}}"}},
                {"id": "end", "type": "end", "params": {"outputs": [{"variable": "answer", "value_selector": ["llm", "text"]}]}},
            ],
            "edges": [{"source": "start", "target": "llm"}, {"source": "llm", "target": "end"}],
        }
    )
    base_graph = yaml.safe_load(_compiler().compile(base_plan))["workflow"]["graph"]
    base_graph["nodes"][0]["position"] = {"x": 10, "y": 20}
    base_graph["nodes"][1]["position"] = {"x": 310, "y": 20}
    base_graph["nodes"][2]["position"] = {"x": 610, "y": 20}
    revised = WorkflowPlan.model_validate(
        {
            "name": "base",
            "nodes": [
                {"id": "start", "type": "start", "params": {"variables": [{"name": "query"}]}},
                {"id": "template", "type": "template-transform", "params": {"template": "{{#start.query#}}"}},
                {"id": "llm", "type": "llm", "params": {"user_prompt": "{{#template.output#}}"}},
                {"id": "end", "type": "end", "params": {"outputs": [{"variable": "answer", "value_selector": ["llm", "text"]}]}},
            ],
            "edges": [
                {"source": "start", "target": "template"},
                {"source": "template", "target": "llm"},
                {"source": "llm", "target": "end"},
            ],
        }
    )

    graph = compile_plan_to_dify_graph(revised, compiler=_compiler(), base_graph=base_graph)
    nodes = {node["id"]: node for node in graph["nodes"]}

    assert nodes["start"]["position"] == {"x": 10, "y": 20}
    assert nodes["llm"]["position"] == {"x": 310, "y": 20}
    assert nodes["template"]["position"]["x"] == 310
    assert nodes["template"]["position"]["y"] == 140


def test_decompile_rejects_unsupported_existing_node_type() -> None:
    graph = {
        "nodes": [{"id": "knowledge", "data": {"type": "knowledge-retrieval"}}],
        "edges": [],
    }

    with pytest.raises(UnsupportedExistingNodeType):
        decompile_dify_graph(graph)


def _seven_type_plan() -> WorkflowPlan:
    return WorkflowPlan.model_validate(
        {
            "name": "seven nodes",
            "nodes": [
                {"id": "start", "type": "start", "params": {"variables": [{"name": "query"}]}},
                {"id": "http", "type": "http-request", "params": {"url": "https://example.com?q={{#start.query#}}"}},
                {
                    "id": "template",
                    "type": "template-transform",
                    "params": {"template": "Input: {{#start.query#}}"},
                },
                {
                    "id": "code",
                    "type": "code",
                    "params": {
                        "code": "def main(raw: str) -> dict:\n    return {\"result\": raw}\n",
                        "variables": [{"variable": "raw", "value_selector": ["template", "output"]}],
                        "outputs": {"result": {"type": "string", "children": None}},
                    },
                },
                {
                    "id": "extract",
                    "type": "parameter-extractor",
                    "params": {
                        "query": ["start", "query"],
                        "parameters": [{"name": "issue", "type": "string", "description": "用户诉求"}],
                    },
                },
                {
                    "id": "classifier",
                    "type": "question-classifier",
                    "params": {
                        "query_variable_selector": ["start", "query"],
                        "classes": [
                            {"id": "complaint", "name": "投诉"},
                            {"id": "consult", "name": "咨询"},
                        ],
                    },
                },
                {
                    "id": "branch",
                    "type": "if-else",
                    "params": {
                        "cases": [
                            {
                                "case_id": "urgent",
                                "logical_operator": "and",
                                "conditions": [
                                    {
                                        "variable_selector": ["start", "query"],
                                        "comparison_operator": "contains",
                                        "value": "urgent",
                                        "varType": "string",
                                    }
                                ],
                            }
                        ]
                    },
                },
                {"id": "llm_yes", "type": "llm", "params": {"user_prompt": "Urgent {{#code.result#}}"}},
                {"id": "llm_no", "type": "llm", "params": {"user_prompt": "Normal {{#code.result#}}"}},
                {"id": "end_yes", "type": "end", "params": {"outputs": [{"variable": "answer", "value_selector": ["llm_yes", "text"]}]}},
                {"id": "end_no", "type": "end", "params": {"outputs": [{"variable": "answer", "value_selector": ["llm_no", "text"]}]}},
            ],
            "edges": [
                {"source": "start", "target": "http"},
                {"source": "http", "target": "template"},
                {"source": "template", "target": "code"},
                {"source": "code", "target": "extract"},
                {"source": "extract", "target": "classifier"},
                {"source": "classifier", "target": "branch", "source_handle": "complaint"},
                {"source": "classifier", "target": "llm_no", "source_handle": "consult"},
                {"source": "branch", "target": "llm_yes", "source_handle": "urgent"},
                {"source": "branch", "target": "llm_no", "source_handle": "false"},
                {"source": "llm_yes", "target": "end_yes"},
                {"source": "llm_no", "target": "end_no"},
            ],
        }
    )
