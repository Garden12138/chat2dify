import yaml
import pytest

from app.agent.normalizer import normalize_plan_payload
from app.compiler.dify import DifyDslCompiler
from app.dify.graph import UnsupportedExistingNodeType, compile_plan_to_dify_graph, decompile_dify_graph
from app.models import PlanNode, WorkflowPlan
from app.validator import validate_plan


def _compiler() -> DifyDslCompiler:
    return DifyDslCompiler(
        dsl_version="9.9.9",
        default_model_provider="openai",
        default_model_name="gpt-4o-mini",
    )


def test_external_dependency_node_types_are_valid_plan_nodes() -> None:
    for node_type in (
        "tool",
        "agent",
        "datasource",
        "datasource-empty",
        "knowledge-index",
        "trigger-webhook",
        "trigger-plugin",
        "trigger-schedule",
    ):
        assert PlanNode.model_validate({"id": node_type, "type": node_type}).type == node_type


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


def test_decompile_dify_graph_covers_advanced_chat_answer_and_memory() -> None:
    plan = WorkflowPlan.model_validate(
        {
            "name": "汽车售后多轮客服",
            "app_mode": "advanced-chat",
            "nodes": [
                {"id": "start", "type": "start", "title": "接收客户消息", "params": {"variables": []}},
                {
                    "id": "llm",
                    "type": "llm",
                    "title": "生成售后回复",
                    "params": {
                        "system_prompt": "你是汽车售后客服。",
                        "user_prompt": "本轮消息：{{#sys.query#}}",
                    },
                },
                {
                    "id": "answer",
                    "type": "answer",
                    "title": "回复客户",
                    "params": {"answer": "{{#llm.text#}}"},
                },
            ],
            "edges": [
                {"source": "start", "target": "llm"},
                {"source": "llm", "target": "answer"},
            ],
        }
    )
    graph = yaml.safe_load(_compiler().compile(plan))["workflow"]["graph"]

    decompiled = decompile_dify_graph(
        graph,
        name="Loaded Chatflow",
        app_mode="advanced-chat",
    )
    llm = next(node for node in decompiled.nodes if node.id == "llm")
    answer = next(node for node in decompiled.nodes if node.id == "answer")

    assert decompiled.app_mode == "advanced-chat"
    assert llm.params["user_prompt"] == "本轮消息：{{#sys.query#}}"
    assert llm.params["memory"]["window"] == {"enabled": True, "size": 10}
    assert answer.params["answer"] == "{{#llm.text#}}"


def test_decompile_dify_graph_covers_stable_builtin_nodes() -> None:
    plan = WorkflowPlan.model_validate(
        {
            "name": "stable nodes",
            "nodes": [
                {
                    "id": "start",
                    "type": "start",
                    "params": {"variables": [{"name": "query"}, {"name": "files", "type": "file-list"}, {"name": "items", "type": "json"}]},
                },
                {"id": "doc", "type": "document-extractor", "params": {"variable_selector": ["start", "files"], "is_array_file": True}},
                {
                    "id": "agg",
                    "type": "variable-aggregator",
                    "params": {"variables": [["doc", "text"], ["start", "query"]], "output_type": "string"},
                },
                {
                    "id": "assign",
                    "type": "assigner",
                    "params": {
                        "items": [
                            {
                                "variable_selector": ["start", "query"],
                                "input_type": "variable",
                                "operation": "over-write",
                                "value": ["agg", "output"],
                            }
                        ]
                    },
                },
                {
                    "id": "list",
                    "type": "list-operator",
                    "params": {"variable": ["start", "items", "records"], "var_type": "array[string]", "item_var_type": "string"},
                },
                {"id": "llm", "type": "llm", "params": {"user_prompt": "{{#list.first_record#}} {{#agg.output#}}"}},
                {"id": "end", "type": "end", "params": {"outputs": [{"variable": "answer", "value_selector": ["llm", "text"]}]}},
            ],
            "edges": [
                {"source": "start", "target": "doc"},
                {"source": "doc", "target": "agg"},
                {"source": "agg", "target": "assign"},
                {"source": "assign", "target": "list"},
                {"source": "list", "target": "llm"},
                {"source": "llm", "target": "end"},
            ],
        }
    )
    graph = yaml.safe_load(_compiler().compile(plan))["workflow"]["graph"]
    start_node = next(node for node in graph["nodes"] if node["id"] == "start")
    files_input = next(item for item in start_node["data"]["variables"] if item["variable"] == "files")
    items_input = next(item for item in start_node["data"]["variables"] if item["variable"] == "items")
    for key in ("allowed_file_upload_methods", "allowed_file_types", "allowed_file_extensions", "max_length"):
        files_input.pop(key, None)

    decompiled = decompile_dify_graph(graph, name="Loaded")
    nodes = {node.id: node for node in decompiled.nodes}
    start_input = next(item for item in nodes["start"].params["variables"] if item["name"] == "files")
    items_start_input = next(item for item in nodes["start"].params["variables"] if item["name"] == "items")

    assert items_input["type"] == "json_object"
    assert start_input["allowed_file_upload_methods"] == ["local_file", "remote_url"]
    assert start_input["allowed_file_types"] == ["document", "image"]
    assert start_input["allowed_file_extensions"] == []
    assert start_input["max_length"] == 5
    assert nodes["doc"].params["variable_selector"] == ["start", "files"]
    assert nodes["agg"].params["variables"] == [["doc", "text"], ["start", "query"]]
    assert nodes["assign"].params["items"][0]["value"] == ["agg", "output"]
    assert items_start_input["type"] == "json"
    assert nodes["list"].params["variable"] == ["start", "items", "records"]


def test_decompile_dify_graph_covers_knowledge_retrieval_node() -> None:
    plan = WorkflowPlan.model_validate(
        {
            "name": "knowledge",
            "nodes": [
                {"id": "start", "type": "start", "params": {"variables": [{"name": "query"}]}},
                {
                    "id": "knowledge",
                    "type": "knowledge-retrieval",
                    "params": {
                        "query_variable_selector": ["start", "query"],
                        "dataset_ids": ["dataset-a"],
                        "retrieval_mode": "multiple",
                        "multiple_retrieval_config": {"top_k": 4, "score_threshold": None, "reranking_enable": False},
                    },
                },
                {"id": "llm", "type": "llm", "params": {"user_prompt": "{{#knowledge.result#}}"}},
                {"id": "end", "type": "end", "params": {"outputs": [{"variable": "answer", "value_selector": ["llm", "text"]}]}},
            ],
            "edges": [
                {"source": "start", "target": "knowledge"},
                {"source": "knowledge", "target": "llm"},
                {"source": "llm", "target": "end"},
            ],
        }
    )
    graph = yaml.safe_load(_compiler().compile(plan))["workflow"]["graph"]

    decompiled = decompile_dify_graph(graph, name="Loaded")
    knowledge = next(node for node in decompiled.nodes if node.id == "knowledge")

    assert knowledge.type == "knowledge-retrieval"
    assert knowledge.params["dataset_ids"] == ["dataset-a"]
    assert knowledge.params["query_variable_selector"] == ["start", "query"]


def test_decompile_dify_graph_covers_human_input_node() -> None:
    plan = WorkflowPlan.model_validate(
        {
            "name": "human input",
            "nodes": [
                {"id": "start", "type": "start", "params": {"variables": [{"name": "query"}]}},
                {
                    "id": "review",
                    "type": "human-input",
                    "params": {
                        "delivery_methods": [{"id": "webapp-1", "type": "webapp", "enabled": True, "config": {}}],
                        "form_content": "请审核：{{#start.query#}}",
                        "inputs": [{"type": "paragraph", "output_variable_name": "review_comment", "default": {"type": "constant", "selector": [], "value": ""}}],
                        "user_actions": [
                            {"id": "approve", "title": "通过", "button_style": "primary"},
                            {"id": "reject", "title": "驳回", "button_style": "default"},
                        ],
                        "timeout": 3,
                        "timeout_unit": "day",
                    },
                },
                {"id": "approved", "type": "end", "params": {"outputs": [{"variable": "comment", "value_selector": ["review", "review_comment"]}]}},
                {"id": "rejected", "type": "end", "params": {"outputs": [{"variable": "action", "value_selector": ["review", "selected_action"]}]}},
            ],
            "edges": [
                {"source": "start", "target": "review"},
                {"source": "review", "target": "approved", "source_handle": "approve"},
                {"source": "review", "target": "rejected", "source_handle": "reject"},
            ],
        }
    )
    graph = yaml.safe_load(_compiler().compile(plan))["workflow"]["graph"]

    decompiled = decompile_dify_graph(graph, name="Loaded")
    review = next(node for node in decompiled.nodes if node.id == "review")

    assert review.type == "human-input"
    assert review.params["delivery_methods"][0]["type"] == "webapp"
    assert review.params["user_actions"][0]["id"] == "approve"
    assert review.params["inputs"][0]["output_variable_name"] == "review_comment"
    assert review.params["timeout_unit"] == "day"


def test_decompile_dify_graph_covers_iteration_and_loop_containers() -> None:
    plan = WorkflowPlan.model_validate(
        {
            "name": "containers",
            "nodes": [
                {"id": "start", "type": "start", "params": {"variables": [{"name": "items", "type": "json"}, {"name": "query"}]}},
                {
                    "id": "batch",
                    "type": "iteration",
                    "title": "批量处理记录",
                    "params": {
                        "start_node_id": "batch_start",
                        "iterator_selector": ["start", "items", "records"],
                        "output_selector": ["item_template", "output"],
                        "children": [
                            {"id": "batch_start", "type": "iteration-start", "params": {}},
                            {
                                "id": "item_template",
                                "type": "template-transform",
                                "params": {
                                    "template": "{{ item }}",
                                    "variables": [{"variable": "item", "value_selector": ["batch", "item"]}],
                                },
                            },
                        ],
                        "edges": [{"source": "batch_start", "target": "item_template"}],
                    },
                },
                {
                    "id": "retry",
                    "type": "loop",
                    "title": "循环检查状态",
                    "params": {
                        "start_node_id": "retry_start",
                        "loop_count": 3,
                        "children": [
                            {"id": "retry_start", "type": "loop-start", "params": {}},
                            {"id": "retry_template", "type": "template-transform", "params": {"template": "{{#start.query#}}"}},
                        ],
                        "edges": [{"source": "retry_start", "target": "retry_template"}],
                    },
                },
                {"id": "end", "type": "end", "params": {"outputs": [{"variable": "answers", "value_selector": ["batch", "output"]}]}},
            ],
            "edges": [
                {"source": "start", "target": "batch"},
                {"source": "batch", "target": "retry"},
                {"source": "retry", "target": "end"},
            ],
        }
    )
    graph = yaml.safe_load(_compiler().compile(plan))["workflow"]["graph"]

    decompiled = decompile_dify_graph(graph, name="Loaded")
    nodes = {node.id: node for node in decompiled.nodes}

    assert set(nodes) == {"start", "batch", "retry", "end"}
    assert nodes["batch"].type == "iteration"
    assert [child["id"] for child in nodes["batch"].params["children"]] == ["batch_start", "item_template"]
    assert nodes["batch"].params["edges"][0] == {
        "source": "batch_start",
        "target": "item_template",
        "source_handle": "source",
        "target_handle": "target",
    }
    assert nodes["retry"].type == "loop"
    assert [child["id"] for child in nodes["retry"].params["children"]] == ["retry_start", "retry_template"]
    assert nodes["retry"].params["loop_count"] == 3


def test_chatflow_advanced_containers_round_trip_with_internal_memory() -> None:
    normalized = normalize_plan_payload(
        {
            "name": "高级 Chatflow",
            "app_mode": "advanced-chat",
            "nodes": [
                {
                    "id": "start",
                    "type": "start",
                    "title": "接收批量检查请求",
                    "params": {"variables": [{"name": "items", "type": "json"}]},
                },
                {
                    "id": "batch",
                    "type": "iteration",
                    "title": "批量分析记录",
                    "params": {
                        "iterator_selector": ["start", "items", "records"],
                        "output_selector": ["item_llm", "text"],
                        "children": [
                            {"id": "batch_start", "type": "iteration-start", "params": {}},
                            {
                                "id": "item_llm",
                                "type": "llm",
                                "title": "逐条分析记录",
                                "params": {
                                    "system_prompt": "你是记录分析专员。",
                                    "user_prompt": "分析当前记录：{{#batch.item#}}",
                                },
                            },
                        ],
                        "edges": [{"source": "batch_start", "target": "item_llm"}],
                    },
                },
                {
                    "id": "retry",
                    "type": "loop",
                    "title": "循环检查结果",
                    "params": {
                        "loop_count": 3,
                        "break_conditions": [
                            {
                                "id": "done",
                                "variable_selector": ["check_llm", "text"],
                                "comparison_operator": "contains",
                                "value": "完成",
                                "varType": "string",
                            }
                        ],
                        "children": [
                            {"id": "retry_start", "type": "loop-start", "params": {}},
                            {
                                "id": "check_llm",
                                "type": "llm",
                                "title": "检查处理状态",
                                "params": {
                                    "system_prompt": "你是处理状态检查专员。",
                                    "user_prompt": "检查批量结果：{{#batch.output#}}",
                                },
                            },
                        ],
                        "edges": [{"source": "retry_start", "target": "check_llm"}],
                    },
                },
                {
                    "id": "answer",
                    "type": "answer",
                    "title": "回复检查结果",
                    "params": {"answer": "{{#batch.output#}}"},
                },
            ],
            "edges": [
                {"source": "start", "target": "batch"},
                {"source": "batch", "target": "retry"},
                {"source": "retry", "target": "answer"},
            ],
        },
        app_mode="advanced-chat",
    )
    plan = WorkflowPlan.model_validate(normalized.payload)
    graph = yaml.safe_load(_compiler().compile(plan))["workflow"]["graph"]

    decompiled = decompile_dify_graph(
        graph,
        name="Loaded Chatflow",
        app_mode="advanced-chat",
    )
    nodes = {node.id: node for node in decompiled.nodes}
    batch_llm = next(
        child for child in nodes["batch"].params["children"] if child["id"] == "item_llm"
    )
    retry_llm = next(
        child for child in nodes["retry"].params["children"] if child["id"] == "check_llm"
    )
    retry_assigner = next(
        child for child in nodes["retry"].params["children"] if child["type"] == "assigner"
    )

    assert validate_plan(plan) == []
    assert validate_plan(decompiled) == []
    assert "{{#sys.query#}}" in batch_llm["params"]["memory"]["query_prompt_template"]
    assert "{{#sys.query#}}" in retry_llm["params"]["memory"]["query_prompt_template"]
    assert retry_assigner["params"]["items"][0]["value"] == ["check_llm", "text"]
    assert nodes["answer"].params["answer"] == "{{#batch.output#}}"


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


def test_decompile_and_compile_external_dependency_nodes_as_passthrough() -> None:
    graph = {
        "nodes": [
            {
                "id": "webhook",
                "type": "custom",
                "position": {"x": 10, "y": 20},
                "data": {
                    "type": "trigger-webhook",
                    "title": "Webhook 触发",
                    "desc": "",
                    "webhook_url": "https://example.test/hook",
                    "method": "POST",
                    "content_type": "application/json",
                    "variables": [{"name": "payload", "type": "object"}],
                },
            },
            {
                "id": "tool",
                "type": "custom",
                "position": {"x": 310, "y": 20},
                "data": {
                    "type": "tool",
                    "title": "调用外部工具",
                    "desc": "",
                    "provider_id": "demo-provider",
                    "provider_type": "builtin",
                    "provider_name": "Demo Provider",
                    "tool_name": "lookup",
                    "tool_label": "Lookup",
                    "tool_node_version": "2",
                    "tool_parameters": {"query": {"type": "variable", "value": ["webhook", "payload"]}},
                    "tool_configurations": {},
                    "output_schema": {
                        "type": "object",
                        "properties": {"answer": {"type": "string"}},
                    },
                },
            },
            {
                "id": "end",
                "type": "custom",
                "position": {"x": 610, "y": 20},
                "data": {
                    "type": "end",
                    "title": "结束",
                    "desc": "",
                    "outputs": [{"variable": "answer", "value_selector": ["tool", "answer"]}],
                },
            },
        ],
        "edges": [
            {"id": "webhook-tool", "source": "webhook", "target": "tool", "sourceHandle": "source", "targetHandle": "target", "data": {}},
            {"id": "tool-end", "source": "tool", "target": "end", "sourceHandle": "source", "targetHandle": "target", "data": {}},
        ],
    }

    plan = decompile_dify_graph(graph, name="External")
    nodes = {node.id: node for node in plan.nodes}
    issues = validate_plan(plan)
    compiled = compile_plan_to_dify_graph(plan, compiler=_compiler(), base_graph=graph)
    compiled_nodes = {node["id"]: node for node in compiled["nodes"]}

    assert nodes["webhook"].type == "trigger-webhook"
    assert nodes["webhook"].params["webhook_url"] == "https://example.test/hook"
    assert nodes["webhook"].params["method"] == "POST"
    assert nodes["tool"].type == "tool"
    assert nodes["tool"].params["output_schema"]["properties"]["answer"]["type"] == "string"
    assert not [issue for issue in issues if issue.severity == "error"]
    assert not any(
        issue.code == "PLAN_EXTERNAL_DEPENDENCY_NODE_PASSTHROUGH" and issue.node_id == "webhook"
        for issue in issues
    )
    assert compiled_nodes["tool"]["data"]["provider_id"] == "demo-provider"
    assert compiled_nodes["tool"]["data"]["tool_parameters"]["query"] == {
        "type": "mixed",
        "value": "{{#webhook.payload#}}",
    }
    assert compiled_nodes["tool"]["data"]["output_schema"]["properties"]["answer"]["type"] == "string"
    assert compiled_nodes["webhook"]["data"]["webhook_url"] == "https://example.test/hook"


def test_decompile_and_compile_structured_plugin_trigger() -> None:
    graph = {
        "nodes": [
            {
                "id": "trigger",
                "type": "custom",
                "position": {"x": 10, "y": 20},
                "data": {
                    "type": "trigger-plugin",
                    "title": "Issue 创建",
                    "provider_id": "langgenius/github/github",
                    "provider_type": "trigger",
                    "provider_name": "langgenius/github/github",
                    "plugin_id": "langgenius/github",
                    "plugin_unique_identifier": "langgenius/github:1.0.0",
                    "event_name": "issue_created",
                    "event_label": "Issue 创建",
                    "subscription_id": "sub-1",
                    "event_parameters": {
                        "repository": {"type": "constant", "value": "garden/project"},
                    },
                    "parameters_schema": [
                        {"name": "repository", "type": "string", "required": True},
                    ],
                    "output_schema": {
                        "properties": {"title": {"type": "string"}},
                    },
                },
            },
            {
                "id": "end",
                "type": "custom",
                "position": {"x": 310, "y": 20},
                "data": {
                    "type": "end",
                    "title": "返回结果",
                    "outputs": [{"variable": "answer", "value_selector": ["trigger", "title"]}],
                },
            },
        ],
        "edges": [{"source": "trigger", "target": "end", "sourceHandle": "source", "targetHandle": "target"}],
    }

    plan = decompile_dify_graph(graph, name="Plugin Trigger")
    trigger = next(node for node in plan.nodes if node.type == "trigger-plugin")
    compiled = compile_plan_to_dify_graph(plan, compiler=_compiler(), base_graph=graph)
    compiled_trigger = next(node for node in compiled["nodes"] if node["id"] == "trigger")

    assert "_raw_data" not in trigger.params
    assert trigger.params["subscription_id"] == "sub-1"
    assert not [issue for issue in validate_plan(plan) if issue.severity == "error"]
    assert compiled_trigger["data"]["event_parameters"]["repository"]["value"] == "garden/project"
    assert compiled_trigger["position"] == {"x": 10, "y": 20}


def test_decompile_rejects_unsupported_existing_node_type() -> None:
    graph = {
        "nodes": [{"id": "mystery", "data": {"type": "mystery-node"}}],
        "edges": [],
    }

    with pytest.raises(UnsupportedExistingNodeType):
        decompile_dify_graph(graph)


def test_decompile_and_compile_structured_agent_node() -> None:
    graph = {
        "nodes": [
            {
                "id": "start",
                "type": "custom",
                "position": {"x": 0, "y": 0},
                "data": {"type": "start", "title": "开始", "variables": [{"name": "query", "type": "paragraph"}]},
            },
            {
                "id": "agent",
                "type": "custom",
                "position": {"x": 300, "y": 0},
                "data": {
                    "type": "agent",
                    "title": "调用智能体",
                    "agent_strategy_provider_name": "langgenius/agent/react",
                    "agent_strategy_name": "react",
                    "agent_strategy_label": "ReAct",
                    "agent_parameters": {"query": {"type": "variable", "value": ["start", "query"]}},
                    "output_schema": {"properties": {"answer": {"type": "string"}}},
                    "tool_node_version": "2",
                    "plugin_unique_identifier": "langgenius/agent:1.0.0",
                    "meta": {"version": "1.0.0"},
                },
            },
            {
                "id": "end",
                "type": "custom",
                "position": {"x": 600, "y": 0},
                "data": {"type": "end", "title": "结束", "outputs": [{"variable": "answer", "value_selector": ["agent", "answer"]}]},
            },
        ],
        "edges": [
            {"id": "start-agent", "source": "start", "target": "agent", "sourceHandle": "source", "targetHandle": "target", "data": {}},
            {"id": "agent-end", "source": "agent", "target": "end", "sourceHandle": "source", "targetHandle": "target", "data": {}},
        ],
    }

    plan = decompile_dify_graph(graph, name="Agent")
    nodes = {node.id: node for node in plan.nodes}
    compiled = compile_plan_to_dify_graph(plan, compiler=_compiler(), base_graph=graph)
    compiled_agent = next(node["data"] for node in compiled["nodes"] if node["id"] == "agent")

    assert nodes["agent"].type == "agent"
    assert nodes["agent"].params["agent_strategy_name"] == "react"
    assert nodes["agent"].params["agent_parameters"]["query"] == {"type": "constant", "value": "{{#start.query#}}"}
    assert compiled_agent["agent_strategy_provider_name"] == "langgenius/agent/react"
    assert compiled_agent["agent_strategy_name"] == "react"
    assert compiled_agent["agent_parameters"]["query"] == {"type": "constant", "value": "{{#start.query#}}"}
    assert compiled_agent["output_schema"]["properties"]["answer"]["type"] == "string"


def test_compile_repairs_overlapping_top_level_layout_in_graph_order() -> None:
    plan = WorkflowPlan.model_validate(
        {
            "name": "Schedule",
            "nodes": [
                {
                    "id": "schedule",
                    "type": "trigger-schedule",
                    "params": {
                        "mode": "cron",
                        "cron_expression": "0 18 * * 1-5",
                        "timezone": "Asia/Shanghai",
                    },
                },
                {
                    "id": "format_time",
                    "type": "code",
                    "params": {
                        "code": "def main(timestamp: int) -> dict:\n    return {'date': str(timestamp)}\n",
                        "variables": [
                            {
                                "variable": "timestamp",
                                "value_selector": ["sys", "timestamp"],
                                "value_type": "number",
                            }
                        ],
                        "outputs": {"date": {"type": "string", "children": None}},
                    },
                },
                {
                    "id": "llm",
                    "type": "llm",
                    "params": {"user_prompt": "今天是 {{#format_time.date#}}"},
                },
                {
                    "id": "end",
                    "type": "end",
                    "params": {
                        "outputs": [{"variable": "answer", "value_selector": ["llm", "text"]}]
                    },
                },
            ],
            "edges": [
                {"source": "schedule", "target": "format_time"},
                {"source": "format_time", "target": "llm"},
                {"source": "llm", "target": "end"},
            ],
        }
    )
    base_graph = {
        "nodes": [
            {"id": "schedule", "position": {"x": 80, "y": 282}},
            {"id": "format_time", "position": {"x": 380, "y": 282}},
            {"id": "llm", "position": {"x": 380, "y": 282}},
            {"id": "end", "position": {"x": 680, "y": 282}},
        ],
        "edges": [],
    }

    graph = compile_plan_to_dify_graph(plan, compiler=_compiler(), base_graph=base_graph)
    positions = {node["id"]: node["position"] for node in graph["nodes"]}

    assert positions["schedule"]["x"] < positions["format_time"]["x"]
    assert positions["format_time"]["x"] < positions["llm"]["x"]
    assert positions["llm"]["x"] < positions["end"]["x"]
    assert len({(item["x"], item["y"]) for item in positions.values()}) == 4


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
