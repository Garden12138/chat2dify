import yaml
from pydantic import ValidationError
from uuid import UUID

from app.agent.planner import fallback_plan
from app.agent.normalizer import normalize_plan_payload
from app.compiler.dify import DifyDslCompiler
from app.dify.knowledge_retrieval import apply_dataset_retrieval_settings
from app.models import WorkflowPlan
from app.validator import has_errors, validate_dsl, validate_plan


def _compiler() -> DifyDslCompiler:
    return DifyDslCompiler(
        dsl_version="9.9.9",
        default_model_provider="openai",
        default_model_name="gpt-4o-mini",
    )


def _compiler_with_datasets() -> DifyDslCompiler:
    return DifyDslCompiler(
        dsl_version="9.9.9",
        default_model_provider="openai",
        default_model_name="gpt-4o-mini",
        default_dataset_ids=["dataset-a", "dataset-b"],
    )


def test_compiler_outputs_dify_workflow_dsl() -> None:
    plan = fallback_plan("Summarize the input", app_name="Summary")
    dsl = _compiler().compile(plan)
    data = yaml.safe_load(dsl)
    llm = next(node for node in data["workflow"]["graph"]["nodes"] if node["id"] == "llm")

    assert data["version"] == "9.9.9"
    assert data["kind"] == "app"
    assert data["app"]["mode"] == "workflow"
    assert data["dependencies"] == []
    assert data["workflow"]["conversation_variables"] == []
    assert data["workflow"]["environment_variables"] == []
    assert data["workflow"]["graph"]["nodes"]
    assert data["workflow"]["graph"]["edges"]
    assert llm["data"]["title"] == "生成Summary回复"
    assert llm["data"]["prompt_template"][0]["role"] == "system"
    assert llm["data"]["prompt_template"][0]["text"]
    assert llm["data"]["prompt_template"][1]["role"] == "user"
    assert "{{#start.query#}}" in llm["data"]["prompt_template"][1]["text"]


def test_validator_accepts_compiled_fallback_plan() -> None:
    dsl = _compiler().compile(fallback_plan("hello"))

    assert validate_dsl(dsl, expected_dsl_version="9.9.9") == []


def test_plan_rejects_isolated_node() -> None:
    payload = {
        "name": "bad",
        "nodes": [
            {"id": "start", "type": "start"},
            {"id": "llm", "type": "llm"},
            {"id": "end", "type": "end"},
        ],
        "edges": [
            {"source": "start", "target": "end"},
        ],
    }

    try:
        WorkflowPlan.model_validate(payload)
    except ValidationError as exc:
        assert "isolated" in str(exc)
    else:
        raise AssertionError("WorkflowPlan should reject isolated nodes")


def test_plan_rejects_missing_edge_reference() -> None:
    payload = {
        "name": "bad",
        "nodes": [
            {"id": "start", "type": "start"},
            {"id": "end", "type": "end"},
        ],
        "edges": [
            {"source": "start", "target": "missing"},
        ],
    }

    try:
        WorkflowPlan.model_validate(payload)
    except ValidationError as exc:
        assert "unknown target" in str(exc)
    else:
        raise AssertionError("WorkflowPlan should reject missing edge targets")


def test_plan_rejects_missing_end() -> None:
    payload = {
        "name": "bad",
        "nodes": [
            {"id": "start", "type": "start"},
            {"id": "llm", "type": "llm"},
        ],
        "edges": [
            {"source": "start", "target": "llm"},
        ],
    }

    try:
        WorkflowPlan.model_validate(payload)
    except ValidationError as exc:
        assert "at least one end" in str(exc)
    else:
        raise AssertionError("WorkflowPlan should reject missing end nodes")


def test_plan_validator_rejects_unknown_variable_reference() -> None:
    plan = WorkflowPlan.model_validate(
        {
            "name": "bad variable",
            "nodes": [
                {
                    "id": "start",
                    "type": "start",
                    "params": {"variables": [{"name": "query", "type": "paragraph"}]},
                },
                {
                    "id": "llm",
                    "type": "llm",
                    "params": {"user_prompt": "{{#start.missing#}}"},
                },
                {
                    "id": "end",
                    "type": "end",
                    "params": {"outputs": [{"variable": "answer", "value_selector": ["llm", "text"]}]},
                },
            ],
            "edges": [
                {"source": "start", "target": "llm"},
                {"source": "llm", "target": "end"},
            ],
        }
    )

    issues = validate_plan(plan)

    assert any(issue.code == "PLAN_VARIABLE_UNKNOWN" for issue in issues)


def test_plan_validator_accepts_external_dependency_outputs() -> None:
    trigger_plan = WorkflowPlan.model_validate(
        {
            "name": "trigger output",
            "nodes": [
                {
                    "id": "webhook",
                    "type": "trigger-webhook",
                    "title": "Webhook 触发",
                    "params": {"_raw_data": {"variables": [{"name": "payload"}]}},
                },
                {
                    "id": "end",
                    "type": "end",
                    "params": {"outputs": [{"variable": "payload", "value_selector": ["webhook", "payload"]}]},
                },
            ],
            "edges": [{"source": "webhook", "target": "end"}],
        }
    )
    datasource_plan = WorkflowPlan.model_validate(
        {
            "name": "datasource output",
            "nodes": [
                {
                    "id": "source",
                    "type": "datasource",
                    "title": "读取数据源",
                    "params": {"_raw_data": {"provider_type": "local_file"}},
                },
                {
                    "id": "end",
                    "type": "end",
                    "params": {"outputs": [{"variable": "file", "value_selector": ["source", "file"]}]},
                },
            ],
            "edges": [{"source": "source", "target": "end"}],
        }
    )

    trigger_issues = validate_plan(trigger_plan)
    datasource_issues = validate_plan(datasource_plan)

    assert not [issue for issue in trigger_issues if issue.code == "PLAN_VARIABLE_UNKNOWN"]
    assert not [issue for issue in datasource_issues if issue.code == "PLAN_VARIABLE_UNKNOWN"]
    assert any(issue.code == "PLAN_EXTERNAL_DEPENDENCY_NODE_PASSTHROUGH" for issue in trigger_issues)
    assert any(issue.code == "PLAN_EXTERNAL_DEPENDENCY_NODE_PASSTHROUGH" for issue in datasource_issues)


def test_plan_validator_warns_about_generic_titles_and_empty_system_prompt() -> None:
    plan = WorkflowPlan.model_validate(
        {
            "name": "quality warnings",
            "nodes": [
                {"id": "start", "type": "start", "title": "Start", "params": {"variables": [{"name": "query"}]}},
                {"id": "llm", "type": "llm", "title": "LLM", "params": {"system_prompt": "", "user_prompt": "{{#start.query#}}"}},
                {"id": "end", "type": "end", "title": "End", "params": {"outputs": [{"variable": "answer", "value_selector": ["llm", "text"]}]}},
            ],
            "edges": [
                {"source": "start", "target": "llm"},
                {"source": "llm", "target": "end"},
            ],
        }
    )

    issues = validate_plan(plan)

    assert any(issue.code == "PLAN_NODE_TITLE_GENERIC" and issue.severity == "warning" for issue in issues)
    assert any(issue.code == "PLAN_LLM_SYSTEM_PROMPT_EMPTY" and issue.severity == "warning" for issue in issues)


def test_normalizer_canonicalizes_common_llm_plan_shorthand() -> None:
    normalized = normalize_plan_payload(_shorthand_branch_plan(), app_name="客服分流")
    plan = WorkflowPlan.model_validate(normalized.payload)

    data = yaml.safe_load(_compiler().compile(plan))
    start = next(node for node in data["workflow"]["graph"]["nodes"] if node["id"] == "start_1")
    if_node = next(node for node in data["workflow"]["graph"]["nodes"] if node["id"] == "if_1")
    llm = next(node for node in data["workflow"]["graph"]["nodes"] if node["id"] == "llm_refund")
    if_edges = [edge for edge in data["workflow"]["graph"]["edges"] if edge["source"] == "if_1"]

    assert start["data"]["variables"][0]["variable"] == "question"
    assert llm["data"]["prompt_template"][1]["text"] == "处理 {{#start_1.question#}}"
    assert if_node["data"]["cases"][0]["case_id"] == "refund"
    assert if_node["data"]["cases"][0]["conditions"][0]["value"] == "退款"
    assert [edge["sourceHandle"] for edge in if_edges] == ["refund", "invoice", "false"]
    assert normalized.changed


def test_normalizer_repairs_generic_titles_and_splits_llm_prompt() -> None:
    normalized = normalize_plan_payload(
        {
            "name": "理发售后服务工作流",
            "nodes": [
                {"id": "start", "type": "start", "title": "Start", "params": {"variables": [{"name": "query"}]}},
                {
                    "id": "llm",
                    "type": "llm",
                    "title": "LLM",
                    "params": {
                        "prompt": (
                            "你是理发门店售后服务专员。\n"
                            "规则：先安抚，再核实事实。\n"
                            "输出格式：先致歉，再给处理建议。\n"
                            "请根据以下售后诉求生成回复：{{start.query}}"
                        )
                    },
                },
                {"id": "end", "type": "end", "title": "End", "params": {"outputs": [{"variable": "answer", "value_selector": ["llm", "text"]}]}},
            ],
            "edges": [
                {"source": "start", "target": "llm"},
                {"source": "llm", "target": "end"},
            ],
        }
    )

    nodes = {node["id"]: node for node in normalized.payload["nodes"]}

    assert nodes["start"]["title"] == "接收理发售后服务诉求"
    assert nodes["llm"]["title"] == "生成售后服务回复"
    assert nodes["end"]["title"] == "返回售后服务结果"
    assert "你是理发门店售后服务专员" in nodes["llm"]["params"]["system_prompt"]
    assert "输出格式" in nodes["llm"]["params"]["system_prompt"]
    assert nodes["llm"]["params"]["user_prompt"] == "请根据以下售后诉求生成回复：{{#start.query#}}"
    assert "normalized generic title" in " ".join(normalized.changes)


def test_normalizer_splits_mixed_single_line_llm_prompt() -> None:
    normalized = normalize_plan_payload(
        {
            "name": "理发售后服务工作流",
            "nodes": [
                {"id": "start", "type": "start", "params": {"variables": [{"name": "query"}]}},
                {
                    "id": "llm",
                    "type": "llm",
                    "params": {
                        "user_prompt": (
                            "你是理发门店售后服务专员。规则：先安抚，再核实事实。"
                            "输出格式：先致歉，再给处理建议。请根据以下售后诉求生成回复：{{start.query}}"
                        )
                    },
                },
                {"id": "end", "type": "end", "params": {"outputs": [{"variable": "answer", "value_selector": ["llm", "text"]}]}},
            ],
            "edges": [
                {"source": "start", "target": "llm"},
                {"source": "llm", "target": "end"},
            ],
        }
    )

    llm = next(node for node in normalized.payload["nodes"] if node["id"] == "llm")

    assert "你是理发门店售后服务专员" in llm["params"]["system_prompt"]
    assert "输出格式" in llm["params"]["system_prompt"]
    assert llm["params"]["user_prompt"] == "请根据以下售后诉求生成回复：{{#start.query#}}"


def test_normalizer_adds_default_system_prompt_when_only_user_prompt_is_present() -> None:
    normalized = normalize_plan_payload(
        {
            "name": "理发售后服务工作流",
            "nodes": [
                {"id": "start", "type": "start", "params": {"variables": [{"name": "query"}]}},
                {"id": "llm", "type": "llm", "params": {"user_prompt": "请处理 {{#start.query#}}"}},
                {"id": "end", "type": "end", "params": {"outputs": [{"variable": "answer", "value_selector": ["llm", "text"]}]}},
            ],
            "edges": [
                {"source": "start", "target": "llm"},
                {"source": "llm", "target": "end"},
            ],
        }
    )

    llm = next(node for node in normalized.payload["nodes"] if node["id"] == "llm")

    assert "你是理发售后服务专员" in llm["params"]["system_prompt"]
    assert llm["params"]["user_prompt"] == "请处理 {{#start.query#}}"


def test_normalizer_infers_code_outputs_from_return_dict() -> None:
    normalized = normalize_plan_payload(
        {
            "nodes": [
                {"id": "start", "type": "start", "params": {"inputs": [{"variable": "query"}]}},
                {
                    "id": "code",
                    "type": "code",
                    "params": {
                        "language": "python3",
                        "inputs": {"raw": "{{start.query}}"},
                        "code": "def main(raw: str) -> dict:\n    return {\"total_amount\": 12.5, \"item_count\": 3}\n",
                    },
                },
                {"id": "end", "type": "end", "params": {"outputs": [{"variable": "answer", "value_selector": "{{code.total_amount}}"}]}},
            ],
            "edges": [
                {"source": "start", "target": "code"},
                {"source": "code", "target": "end"},
            ],
        }
    )

    code_node = next(node for node in normalized.payload["nodes"] if node["id"] == "code")
    end_node = next(node for node in normalized.payload["nodes"] if node["id"] == "end")

    assert code_node["params"]["code_language"] == "python3"
    assert code_node["params"]["variables"][0]["value_selector"] == ["start", "query"]
    assert set(code_node["params"]["outputs"]) == {"total_amount", "item_count"}
    assert end_node["params"]["outputs"][0]["value_selector"] == ["code", "total_amount"]


def test_template_transform_refs_become_jinja_variables() -> None:
    normalized = normalize_plan_payload(
        {
            "nodes": [
                {"id": "start", "type": "start", "params": {"variables": [{"name": "query"}]}},
                {
                    "id": "template",
                    "type": "template-transform",
                    "params": {"template": "Input: {{#start.query#}}"},
                },
                {"id": "end", "type": "end", "params": {"outputs": [{"variable": "answer", "value_selector": ["template", "output"]}]}},
            ],
            "edges": [
                {"source": "start", "target": "template"},
                {"source": "template", "target": "end"},
            ],
        }
    )
    plan = WorkflowPlan.model_validate(normalized.payload)

    data = yaml.safe_load(_compiler().compile(plan))
    template = next(node for node in data["workflow"]["graph"]["nodes"] if node["id"] == "template")

    assert template["data"]["template"] == "Input: {{ query }}"
    assert template["data"]["variables"] == [
        {"variable": "query", "value_selector": ["start", "query"], "value_type": "string"}
    ]
    assert validate_plan(plan) == []
    assert validate_dsl(_compiler().compile(plan), expected_dsl_version="9.9.9") == []


def test_template_transform_reuses_declared_variable_alias() -> None:
    normalized = normalize_plan_payload(
        {
            "nodes": [
                {"id": "start", "type": "start", "params": {"variables": [{"name": "query"}]}},
                {
                    "id": "template",
                    "type": "template-transform",
                    "params": {
                        "template": "Input: {{#start.query#}}",
                        "variables": [{"variable": "input", "value_selector": ["start", "query"]}],
                    },
                },
                {"id": "end", "type": "end", "params": {"outputs": [{"variable": "answer", "value_selector": ["template", "output"]}]}},
            ],
            "edges": [
                {"source": "start", "target": "template"},
                {"source": "template", "target": "end"},
            ],
        }
    )
    plan = WorkflowPlan.model_validate(normalized.payload)

    data = yaml.safe_load(_compiler().compile(plan))
    template = next(node for node in data["workflow"]["graph"]["nodes"] if node["id"] == "template")

    assert template["data"]["template"] == "Input: {{ input }}"
    assert template["data"]["variables"] == [
        {"variable": "input", "value_selector": ["start", "query"], "value_type": "string"}
    ]


def test_normalizer_canonicalizes_understanding_node_shorthand() -> None:
    normalized = normalize_plan_payload(
        {
            "name": "修车售后服务工作流",
            "nodes": [
                {"id": "start", "type": "start", "params": {"variables": [{"name": "query"}]}},
                {
                    "id": "extract",
                    "type": "parameter_extractor",
                    "title": "Extractor",
                    "params": {
                        "query_variable_selector": "{{start.query}}",
                        "fields": [
                            {"name": "车型", "type": "string", "description": "车辆型号", "required": False},
                            {"name": "诉求", "type": "text", "description": "用户诉求"},
                        ],
                    },
                },
                {
                    "id": "classifier",
                    "type": "classifier",
                    "title": "Classifier",
                    "params": {
                        "query": "{{start.query}}",
                        "categories": ["投诉", "咨询"],
                    },
                },
                {"id": "llm_complaint", "type": "llm", "params": {"user_prompt": "处理 {{#extract.request#}}"}},
                {"id": "llm_consult", "type": "llm", "params": {"user_prompt": "处理 {{#start.query#}}"}},
                {"id": "end_complaint", "type": "end", "params": {"outputs": [{"variable": "answer", "value_selector": ["llm_complaint", "text"]}]}},
                {"id": "end_consult", "type": "end", "params": {"outputs": [{"variable": "answer", "value_selector": ["llm_consult", "text"]}]}},
            ],
            "edges": [
                {"source": "start", "target": "extract"},
                {"source": "extract", "target": "classifier"},
                {"source": "classifier", "target": "llm_complaint"},
                {"source": "classifier", "target": "llm_consult"},
                {"source": "llm_complaint", "target": "end_complaint"},
                {"source": "llm_consult", "target": "end_consult"},
            ],
        }
    )
    plan = WorkflowPlan.model_validate(normalized.payload)
    nodes = {node.id: node for node in plan.nodes}

    assert nodes["extract"].type == "parameter-extractor"
    assert nodes["extract"].params["query"] == ["start", "query"]
    assert [item["name"] for item in nodes["extract"].params["parameters"]] == ["car_model", "request"]
    assert nodes["classifier"].type == "question-classifier"
    assert nodes["classifier"].params["query_variable_selector"] == ["start", "query"]
    assert [edge.source_handle for edge in plan.edges if edge.source == "classifier"] == ["class_1", "class_2"]
    assert validate_plan(plan) == []


def test_http_request_normalizes_empty_headers_params_and_body() -> None:
    normalized = normalize_plan_payload(
        {
            "nodes": [
                {"id": "start", "type": "start", "params": {"variables": [{"name": "query"}]}},
                {"id": "http", "type": "http-request", "params": {"url": "https://example.com", "headers": [], "params": {}}},
                {"id": "end", "type": "end", "params": {"outputs": [{"variable": "status", "value_selector": ["http", "status_code"]}]}},
            ],
            "edges": [
                {"source": "start", "target": "http"},
                {"source": "http", "target": "end"},
            ],
        }
    )
    plan = WorkflowPlan.model_validate(normalized.payload)

    data = yaml.safe_load(_compiler().compile(plan))
    http = next(node for node in data["workflow"]["graph"]["nodes"] if node["id"] == "http")

    assert http["data"]["headers"] == ""
    assert http["data"]["params"] == ""
    assert http["data"]["body"] == {"type": "none", "data": []}


def test_http_request_normalizes_key_value_shorthand() -> None:
    normalized = normalize_plan_payload(
        {
            "nodes": [
                {"id": "start", "type": "start", "params": {"variables": [{"name": "query"}]}},
                {
                    "id": "http",
                    "type": "http-request",
                    "params": {
                        "url": "https://example.com/search",
                        "headers": {"Accept": "application/json"},
                        "params": [{"key": "q", "value": "{{start.query}}"}],
                    },
                },
                {"id": "end", "type": "end", "params": {"outputs": [{"variable": "status", "value_selector": ["http", "status_code"]}]}},
            ],
            "edges": [
                {"source": "start", "target": "http"},
                {"source": "http", "target": "end"},
            ],
        }
    )
    plan = WorkflowPlan.model_validate(normalized.payload)

    data = yaml.safe_load(_compiler().compile(plan))
    http = next(node for node in data["workflow"]["graph"]["nodes"] if node["id"] == "http")

    assert http["data"]["headers"] == "Accept:application/json"
    assert http["data"]["params"] == "q:{{#start.query#}}"


def test_compiler_covers_supported_node_minimum_structure() -> None:
    plan = WorkflowPlan.model_validate(
        {
            "name": "supported nodes",
            "nodes": [
                {"id": "start", "type": "start", "params": {"variables": [{"name": "query"}]}},
                {"id": "http", "type": "http-request", "params": {"url": "https://example.com?q={{#start.query#}}"}},
                {
                    "id": "template",
                    "type": "template-transform",
                    "params": {
                        "template": "Input: {{#start.query#}}",
                        "variables": [{"variable": "query", "value_selector": ["start", "query"]}],
                    },
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
                        "reasoning_mode": "prompt",
                        "parameters": [
                            {
                                "name": "issue",
                                "type": "string",
                                "description": "用户诉求",
                                "required": True,
                            },
                            {
                                "name": "car_model",
                                "type": "string",
                                "description": "车辆型号",
                                "required": False,
                            },
                        ],
                        "instruction": "提取修车售后字段",
                    },
                },
                {
                    "id": "classifier",
                    "type": "question-classifier",
                    "params": {
                        "query_variable_selector": ["start", "query"],
                        "classes": [
                            {"id": "complaint", "name": "投诉", "label": "CLASS 1"},
                            {"id": "consult", "name": "咨询", "label": "CLASS 2"},
                        ],
                        "instruction": "判断用户诉求属于投诉还是咨询",
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
                {"id": "llm_yes", "type": "llm", "params": {"user_prompt": "Handle {{#extract.issue#}} urgently"}},
                {"id": "llm_no", "type": "llm", "params": {"user_prompt": "Handle {{#extract.issue#}} normally"}},
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

    dsl = _compiler().compile(plan)
    data = yaml.safe_load(dsl)
    node_types = {node["data"]["type"] for node in data["workflow"]["graph"]["nodes"]}
    extract = next(node for node in data["workflow"]["graph"]["nodes"] if node["id"] == "extract")
    classifier = next(node for node in data["workflow"]["graph"]["nodes"] if node["id"] == "classifier")

    assert node_types == {
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
    assert extract["data"]["model"]["provider"] == "openai"
    assert extract["data"]["parameters"][0]["name"] == "issue"
    assert classifier["data"]["classes"][0]["id"] == "complaint"
    assert classifier["data"]["query_variable_selector"] == ["start", "query"]
    assert validate_plan(plan) == []
    assert validate_dsl(dsl, expected_dsl_version="9.9.9") == []


def test_validator_rejects_if_else_missing_case_edge() -> None:
    plan = WorkflowPlan.model_validate(
        {
            "name": "missing case edge",
            "nodes": [
                {"id": "start", "type": "start", "params": {"variables": [{"name": "query"}]}},
                {
                    "id": "branch",
                    "type": "if-else",
                    "params": {
                        "cases": [
                            {
                                "case_id": "refund",
                                "logical_operator": "and",
                                "conditions": [
                                    {
                                        "variable_selector": ["start", "query"],
                                        "comparison_operator": "contains",
                                        "value": "退款",
                                        "varType": "string",
                                    }
                                ],
                            },
                            {
                                "case_id": "invoice",
                                "logical_operator": "and",
                                "conditions": [
                                    {
                                        "variable_selector": ["start", "query"],
                                        "comparison_operator": "contains",
                                        "value": "发票",
                                        "varType": "string",
                                    }
                                ],
                            },
                        ]
                    },
                },
                {"id": "llm_refund", "type": "llm", "params": {"user_prompt": "退款 {{#start.query#}}"}},
                {"id": "llm_else", "type": "llm", "params": {"user_prompt": "其他 {{#start.query#}}"}},
                {"id": "end_refund", "type": "end", "params": {"outputs": [{"variable": "answer", "value_selector": ["llm_refund", "text"]}]}},
                {"id": "end_else", "type": "end", "params": {"outputs": [{"variable": "answer", "value_selector": ["llm_else", "text"]}]}},
            ],
            "edges": [
                {"source": "start", "target": "branch"},
                {"source": "branch", "target": "llm_refund", "source_handle": "refund"},
                {"source": "branch", "target": "llm_else", "source_handle": "false"},
                {"source": "llm_refund", "target": "end_refund"},
                {"source": "llm_else", "target": "end_else"},
            ],
        }
    )

    issues = validate_plan(plan)

    assert any(issue.code == "PLAN_IF_ELSE_CASE_EDGE_MISSING" for issue in issues)


def test_validator_rejects_if_else_missing_false_edge() -> None:
    payload = _shorthand_branch_plan()
    payload["nodes"] = [node for node in payload["nodes"] if node.get("id") not in {"llm_general", "end_general"}]
    payload["edges"] = [
        edge
        for edge in payload["edges"]
        if edge.get("target") != "llm_general" and edge.get("source") != "llm_general"
    ]
    normalized = normalize_plan_payload(payload)
    plan = WorkflowPlan.model_validate(normalized.payload)

    issues = validate_plan(plan)

    assert any(issue.code == "PLAN_IF_ELSE_FALSE_EDGE_MISSING" for issue in issues)


def test_validator_rejects_duplicate_if_else_branch_handle() -> None:
    payload = _shorthand_branch_plan()
    payload["edges"][2]["source_handle"] = "refund"
    normalized = normalize_plan_payload(payload)
    plan = WorkflowPlan.model_validate(normalized.payload)

    issues = validate_plan(plan)

    assert any(issue.code == "PLAN_IF_ELSE_DUPLICATE_BRANCH" for issue in issues)


def test_validator_rejects_question_classifier_missing_class_edge() -> None:
    plan = WorkflowPlan.model_validate(
        {
            "name": "classifier missing edge",
            "nodes": [
                {"id": "start", "type": "start", "params": {"variables": [{"name": "query"}]}},
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
                {"id": "llm_complaint", "type": "llm", "params": {"user_prompt": "投诉 {{#start.query#}}"}},
                {"id": "end", "type": "end", "params": {"outputs": [{"variable": "answer", "value_selector": ["llm_complaint", "text"]}]}},
            ],
            "edges": [
                {"source": "start", "target": "classifier"},
                {"source": "classifier", "target": "llm_complaint", "source_handle": "complaint"},
                {"source": "llm_complaint", "target": "end"},
            ],
        }
    )

    issues = validate_plan(plan)

    assert any(issue.code == "PLAN_QUESTION_CLASSIFIER_CLASS_EDGE_MISSING" for issue in issues)


def test_validator_rejects_parameter_extractor_invalid_parameter_type() -> None:
    plan = WorkflowPlan.model_validate(
        {
            "name": "extractor invalid type",
            "nodes": [
                {"id": "start", "type": "start", "params": {"variables": [{"name": "query"}]}},
                {
                    "id": "extract",
                    "type": "parameter-extractor",
                    "params": {
                        "query": ["start", "query"],
                        "parameters": [
                            {
                                "name": "issue",
                                "type": "date",
                                "description": "用户诉求",
                            }
                        ],
                    },
                },
                {"id": "end", "type": "end", "params": {"outputs": [{"variable": "issue", "value_selector": ["extract", "issue"]}]}},
            ],
            "edges": [
                {"source": "start", "target": "extract"},
                {"source": "extract", "target": "end"},
            ],
        }
    )

    issues = validate_plan(plan)

    assert any(issue.code == "PLAN_PARAMETER_EXTRACTOR_PARAMETER_TYPE_INVALID" for issue in issues)


def test_validator_accepts_parameter_extractor_outputs() -> None:
    plan = WorkflowPlan.model_validate(
        {
            "name": "extractor outputs",
            "nodes": [
                {"id": "start", "type": "start", "params": {"variables": [{"name": "query"}]}},
                {
                    "id": "extract",
                    "type": "parameter-extractor",
                    "params": {
                        "query": ["start", "query"],
                        "parameters": [
                            {"name": "issue", "type": "string", "description": "用户诉求"},
                        ],
                    },
                },
                {"id": "end", "type": "end", "params": {"outputs": [{"variable": "issue", "value_selector": ["extract", "issue"]}]}},
            ],
            "edges": [
                {"source": "start", "target": "extract"},
                {"source": "extract", "target": "end"},
            ],
        }
    )

    assert validate_plan(plan) == []


def test_compiler_and_validator_cover_stable_builtin_nodes() -> None:
    plan = WorkflowPlan.model_validate(
        {
            "name": "stable builtins",
            "nodes": [
                {
                    "id": "start",
                    "type": "start",
                    "params": {
                        "variables": [
                            {"name": "query", "type": "paragraph"},
                            {"name": "files", "type": "file-list"},
                            {"name": "items", "type": "json"},
                        ]
                    },
                },
                {
                    "id": "doc",
                    "type": "document-extractor",
                    "params": {"variable_selector": ["start", "files"], "is_array_file": True},
                },
                {
                    "id": "aggregator",
                    "type": "variable-aggregator",
                    "params": {
                        "variables": [["doc", "text"], ["start", "query"]],
                        "output_type": "string",
                        "advanced_settings": {"group_enabled": False, "groups": []},
                    },
                },
                {
                    "id": "assign",
                    "type": "assigner",
                    "params": {
                        "version": "2",
                        "items": [
                            {
                                "variable_selector": ["start", "query"],
                                "input_type": "variable",
                                "operation": "over-write",
                                "value": ["aggregator", "output"],
                            }
                        ],
                    },
                },
                {
                    "id": "list",
                    "type": "list-operator",
                    "params": {
                        "variable": ["start", "items", "records"],
                        "var_type": "array[string]",
                        "item_var_type": "string",
                        "filter_by": {
                            "enabled": True,
                            "conditions": [{"key": "", "comparison_operator": "contains", "value": "投诉"}],
                        },
                        "extract_by": {"enabled": True, "serial": "1"},
                        "order_by": {"enabled": False, "key": "", "value": "asc"},
                        "limit": {"enabled": True, "size": 3},
                    },
                },
                {
                    "id": "llm",
                    "type": "llm",
                    "params": {"user_prompt": "{{#aggregator.output#}}\n{{#list.first_record#}}"},
                },
                {
                    "id": "end",
                    "type": "end",
                    "params": {"outputs": [{"variable": "answer", "value_selector": ["llm", "text"]}]},
                },
            ],
            "edges": [
                {"source": "start", "target": "doc"},
                {"source": "doc", "target": "aggregator"},
                {"source": "aggregator", "target": "assign"},
                {"source": "assign", "target": "list"},
                {"source": "list", "target": "llm"},
                {"source": "llm", "target": "end"},
            ],
        }
    )

    dsl = _compiler().compile(plan)
    data = yaml.safe_load(dsl)
    nodes = {node["id"]: node["data"] for node in data["workflow"]["graph"]["nodes"]}
    files_input = next(item for item in nodes["start"]["variables"] if item["variable"] == "files")
    items_input = next(item for item in nodes["start"]["variables"] if item["variable"] == "items")

    assert nodes["doc"]["type"] == "document-extractor"
    assert nodes["doc"]["variable_selector"] == ["start", "files"]
    assert files_input["type"] == "file-list"
    assert files_input["allowed_file_upload_methods"] == ["local_file", "remote_url"]
    assert files_input["allowed_file_types"] == ["document", "image"]
    assert files_input["allowed_file_extensions"] == []
    assert files_input["max_length"] == 5
    assert items_input["type"] == "json_object"
    assert nodes["aggregator"]["variables"] == [["doc", "text"], ["start", "query"]]
    assert nodes["assign"]["items"][0]["value"] == ["aggregator", "output"]
    assert nodes["list"]["variable"] == ["start", "items", "records"]
    assert nodes["list"]["filter_by"]["conditions"][0]["value"] == "投诉"
    assert validate_plan(plan) == []
    assert validate_dsl(dsl, expected_dsl_version="9.9.9") == []


def test_normalizer_repairs_stable_builtin_node_aliases() -> None:
    normalized = normalize_plan_payload(
        {
            "name": "aliases",
            "nodes": [
                {"id": "start", "type": "start", "params": {"variables": [{"name": "query"}, {"name": "files", "type": "files"}, {"name": "items", "type": "json"}]}},
                {"id": "doc", "type": "doc-extractor", "params": {"file": "{{start.files}}", "array": True}},
                {"id": "agg", "type": "aggregator", "params": {"selectors": ["{{doc.text}}", "{{start.query}}"]}},
                {"id": "list", "type": "list-filter", "params": {"variable_selector": "{{start.items.records}}", "type": "array_string", "limit": 2}},
                {"id": "llm", "type": "llm", "params": {"prompt": "{{agg.output}} {{list.first_record}}"}},
                {"id": "end", "type": "end", "params": {"outputs": [{"variable": "answer", "value_selector": "{{llm.text}}"}]}},
            ],
            "edges": [
                {"source": "start", "target": "doc"},
                {"source": "doc", "target": "agg"},
                {"source": "agg", "target": "list"},
                {"source": "list", "target": "llm"},
                {"source": "llm", "target": "end"},
            ],
        }
    )
    plan = WorkflowPlan.model_validate(normalized.payload)
    node_types = {node.id: node.type for node in plan.nodes}
    start = next(node for node in plan.nodes if node.id == "start")
    files_input = next(item for item in start.params["variables"] if item["name"] == "files")

    assert node_types["doc"] == "document-extractor"
    assert node_types["agg"] == "variable-aggregator"
    assert node_types["list"] == "list-operator"
    assert files_input["allowed_file_upload_methods"] == ["local_file", "remote_url"]
    assert files_input["allowed_file_types"] == ["document", "image"]
    assert files_input["allowed_file_extensions"] == []
    assert next(node for node in plan.nodes if node.id == "doc").params["variable_selector"] == ["start", "files"]
    assert validate_plan(plan) == []


def test_normalizer_repairs_list_operator_filter_aliases_and_nested_array_selector() -> None:
    normalized = normalize_plan_payload(
        {
            "name": "售后记录筛选工作流",
            "nodes": [
                {"id": "start", "type": "start", "params": {"variables": [{"name": "items", "type": "json"}]}},
                {
                    "id": "list",
                    "type": "list-filter",
                    "params": {
                        "variable": ["start", "items", "records"],
                        "type": "array_object",
                        "item_type": "object",
                        "filter": {
                            "enabled": True,
                            "conditions": [{"key": "category", "comparison_operator": "eq", "value": "complaint"}],
                        },
                        "sort": {"enabled": True, "key": "created_at", "value": "desc"},
                        "limit": 1,
                    },
                },
                {"id": "llm", "type": "llm", "params": {"user_prompt": "请处理 {{list.first_record}}"}},
                {"id": "end", "type": "end", "params": {"outputs": [{"variable": "answer", "value_selector": ["llm", "text"]}]}},
            ],
            "edges": [
                {"source": "start", "target": "list"},
                {"source": "list", "target": "llm"},
                {"source": "llm", "target": "end"},
            ],
        }
    )
    plan = WorkflowPlan.model_validate(normalized.payload)
    code_node = next(node for node in plan.nodes if node.id == "list")

    assert code_node.type == "code"
    assert code_node.params["variables"][0]["value_selector"] == ["start", "items", "records"]
    assert code_node.params["outputs"]["first_record"]["type"] == "object"
    assert "'category', 'comparison_operator': '=', 'value': 'complaint'" in code_node.params["code"]
    assert validate_plan(plan) == []

    data = yaml.safe_load(_compiler().compile(plan))
    compiled_start = next(node["data"] for node in data["workflow"]["graph"]["nodes"] if node["id"] == "start")
    compiled_code = next(node["data"] for node in data["workflow"]["graph"]["nodes"] if node["id"] == "list")
    compiled_items = next(item for item in compiled_start["variables"] if item["variable"] == "items")

    assert compiled_items["type"] == "json_object"
    assert compiled_code["type"] == "code"
    assert compiled_code["variables"][0]["value_selector"] == ["start", "items", "records"]
    assert compiled_code["outputs"]["result"]["type"] == "array[object]"


def test_validator_rejects_invalid_stable_builtin_nodes() -> None:
    plan = WorkflowPlan.model_validate(
        {
            "name": "bad builtins",
            "nodes": [
                {"id": "start", "type": "start", "params": {"variables": [{"name": "query"}]}},
                {
                    "id": "list",
                    "type": "list-operator",
                    "params": {
                        "variable": ["start", "query"],
                        "var_type": "string",
                        "filter_by": {
                            "enabled": True,
                            "conditions": [{"comparison_operator": "eq", "value": "complaint"}],
                        },
                    },
                },
                {"id": "assign", "type": "assigner", "params": {"items": [{"input_type": "constant", "operation": "over-write", "value": "x"}]}},
                {"id": "end", "type": "end", "params": {"outputs": [{"variable": "answer", "value_selector": ["start", "query"]}]}},
            ],
            "edges": [
                {"source": "start", "target": "list"},
                {"source": "list", "target": "assign"},
                {"source": "assign", "target": "end"},
            ],
        }
    )

    issues = validate_plan(plan)

    assert any(issue.code == "PLAN_LIST_OPERATOR_VAR_TYPE_INVALID" for issue in issues)
    assert any(issue.code == "PLAN_LIST_OPERATOR_FILTER_OPERATOR_INVALID" for issue in issues)
    assert any(issue.code == "PLAN_ASSIGNER_TARGET_INVALID" for issue in issues)


def test_normalizer_compiler_and_validator_cover_knowledge_retrieval_node() -> None:
    normalized = normalize_plan_payload(
        {
            "name": "知识库问答",
            "nodes": [
                {"id": "start", "type": "start", "params": {"variables": [{"name": "query"}]}},
                {
                    "id": "knowledge",
                    "type": "rag",
                    "params": {
                        "query": "{{start.query}}",
                        "retrieval_config": {"top_k": 3},
                    },
                },
                {
                    "id": "llm",
                    "type": "llm",
                    "params": {"prompt": "根据资料回答：{{knowledge.result}}\n用户问题：{{start.query}}"},
                },
                {"id": "end", "type": "end", "params": {"outputs": [{"variable": "answer", "value_selector": ["llm", "text"]}]}},
            ],
            "edges": [
                {"source": "start", "target": "knowledge"},
                {"source": "knowledge", "target": "llm"},
                {"source": "llm", "target": "end"},
            ],
        },
        default_dataset_ids=["dataset-a", "dataset-b"],
    )
    plan = WorkflowPlan.model_validate(normalized.payload)
    dsl = _compiler_with_datasets().compile(plan)
    data = yaml.safe_load(dsl)
    knowledge = next(node["data"] for node in data["workflow"]["graph"]["nodes"] if node["id"] == "knowledge")

    assert next(node for node in plan.nodes if node.id == "knowledge").type == "knowledge-retrieval"
    assert knowledge["type"] == "knowledge-retrieval"
    assert knowledge["dataset_ids"] == ["dataset-a", "dataset-b"]
    assert knowledge["query_variable_selector"] == ["start", "query"]
    assert knowledge["retrieval_mode"] == "multiple"
    assert knowledge["multiple_retrieval_config"]["top_k"] == 3
    assert validate_plan(plan) == []
    assert validate_dsl(dsl, expected_dsl_version="9.9.9") == []


def test_normalizer_compiler_and_validator_cover_human_input_node() -> None:
    normalized = normalize_plan_payload(
        {
            "name": "售后人工审核",
            "nodes": [
                {"id": "start", "type": "start", "params": {"variables": [{"name": "query"}]}},
                {
                    "id": "review",
                    "type": "human",
                    "params": {
                        "methods": [{"id": "webapp-1", "type": "webapp", "enabled": True, "config": {}}],
                        "content": "请审核售后处理建议：{{start.query}}",
                        "fields": [{"name": "review_comment", "type": "paragraph"}],
                        "actions": ["approve", "reject"],
                        "timeout": "2",
                        "timeoutUnit": "day",
                    },
                },
                {"id": "approved", "type": "end", "params": {"outputs": [{"variable": "comment", "value_selector": ["review", "review_comment"]}]}},
                {"id": "rejected", "type": "end", "params": {"outputs": [{"variable": "action", "value_selector": ["review", "__action_id"]}]}},
            ],
            "edges": [
                {"source": "start", "target": "review"},
                {"source": "review", "target": "approved"},
                {"source": "review", "target": "rejected"},
            ],
        }
    )
    plan = WorkflowPlan.model_validate(normalized.payload)
    dsl = _compiler().compile(plan)
    data = yaml.safe_load(dsl)
    review = next(node["data"] for node in data["workflow"]["graph"]["nodes"] if node["id"] == "review")
    review_edges = [edge for edge in data["workflow"]["graph"]["edges"] if edge["source"] == "review"]

    assert next(node for node in plan.nodes if node.id == "review").type == "human-input"
    assert review["type"] == "human-input"
    assert str(UUID(review["delivery_methods"][0]["id"])) == review["delivery_methods"][0]["id"]
    assert review["delivery_methods"][0]["id"] != "webapp-1"
    assert review["delivery_methods"][0]["type"] == "webapp"
    assert review["delivery_methods"][0]["enabled"] is True
    assert review["user_actions"] == [
        {"id": "approve", "title": "approve", "button_style": "primary"},
        {"id": "reject", "title": "reject", "button_style": "default"},
    ]
    assert review["inputs"][0]["output_variable_name"] == "review_comment"
    assert "{{#start.query#}}" in review["form_content"]
    assert sorted(edge["sourceHandle"] for edge in review_edges) == ["approve", "reject"]
    assert validate_plan(plan) == []
    assert validate_dsl(dsl, expected_dsl_version="9.9.9") == []


def test_knowledge_retrieval_strips_disabled_or_incomplete_rerank_model() -> None:
    normalized = normalize_plan_payload(
        {
            "name": "知识库问答",
            "nodes": [
                {"id": "start", "type": "start", "params": {"variables": [{"name": "query"}]}},
                {
                    "id": "knowledge",
                    "type": "knowledge-retrieval",
                    "params": {
                        "query_variable_selector": ["start", "query"],
                        "dataset_ids": ["dataset-a"],
                        "multiple_retrieval_config": {
                            "top_k": 4,
                            "score_threshold": None,
                            "reranking_enable": False,
                            "reranking_mode": "reranking_model",
                            "reranking_model": {"provider": "openai", "model": "gpt-4o-mini"},
                        },
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
    plan = WorkflowPlan.model_validate(normalized.payload)
    dsl = _compiler().compile(plan)
    knowledge = next(node["data"] for node in yaml.safe_load(dsl)["workflow"]["graph"]["nodes"] if node["id"] == "knowledge")

    assert "reranking_model" not in next(node for node in plan.nodes if node.id == "knowledge").params["multiple_retrieval_config"]
    assert knowledge["multiple_retrieval_config"] == {
        "top_k": 4,
        "score_threshold": None,
        "reranking_enable": False,
        "reranking_mode": "reranking_model",
    }


def test_knowledge_retrieval_keeps_enabled_rerank_model() -> None:
    plan = WorkflowPlan.model_validate(
        {
            "name": "rerank",
            "nodes": [
                {"id": "start", "type": "start", "params": {"variables": [{"name": "query"}]}},
                {
                    "id": "knowledge",
                    "type": "knowledge-retrieval",
                    "params": {
                        "query_variable_selector": ["start", "query"],
                        "dataset_ids": ["dataset-a"],
                        "multiple_retrieval_config": {
                            "top_k": 4,
                            "reranking_enable": True,
                            "reranking_model": {"reranking_provider_name": "cohere", "reranking_model_name": "rerank-v2"},
                        },
                    },
                },
                {"id": "end", "type": "end", "params": {"outputs": [{"variable": "answer", "value_selector": ["knowledge", "result"]}]}},
            ],
            "edges": [
                {"source": "start", "target": "knowledge"},
                {"source": "knowledge", "target": "end"},
            ],
        }
    )

    dsl = _compiler().compile(plan)
    knowledge = next(node["data"] for node in yaml.safe_load(dsl)["workflow"]["graph"]["nodes"] if node["id"] == "knowledge")

    assert knowledge["multiple_retrieval_config"]["reranking_model"] == {"provider": "cohere", "model": "rerank-v2"}


def test_knowledge_retrieval_uses_dataset_rerank_model_setting() -> None:
    plan = WorkflowPlan.model_validate(
        {
            "name": "dataset rerank",
            "nodes": [
                {"id": "start", "type": "start", "params": {"variables": [{"name": "query"}]}},
                {
                    "id": "knowledge",
                    "type": "knowledge-retrieval",
                    "params": {
                        "query_variable_selector": ["start", "query"],
                        "dataset_ids": ["dataset-a"],
                        "retrieval_mode": "multiple",
                        "multiple_retrieval_config": {
                            "top_k": 4,
                            "score_threshold": None,
                            "reranking_enable": False,
                        },
                    },
                },
                {"id": "end", "type": "end", "params": {"outputs": [{"variable": "answer", "value_selector": ["knowledge", "result"]}]}},
            ],
            "edges": [
                {"source": "start", "target": "knowledge"},
                {"source": "knowledge", "target": "end"},
            ],
        }
    )

    enriched = apply_dataset_retrieval_settings(
        plan,
        {
            "dataset-a": {
                "id": "dataset-a",
                "retrieval_model_dict": {
                    "search_method": "semantic_search",
                    "reranking_enable": True,
                    "reranking_mode": None,
                    "reranking_model": {
                        "reranking_provider_name": "langgenius/tongyi/tongyi",
                        "reranking_model_name": "qwen3-rerank",
                    },
                    "top_k": 3,
                    "score_threshold_enabled": False,
                    "score_threshold": 0,
                },
            }
        },
    )

    dsl = _compiler().compile(enriched)
    knowledge = next(node["data"] for node in yaml.safe_load(dsl)["workflow"]["graph"]["nodes"] if node["id"] == "knowledge")

    assert knowledge["multiple_retrieval_config"] == {
        "top_k": 4,
        "score_threshold": None,
        "reranking_enable": True,
        "reranking_mode": "reranking_model",
        "reranking_model": {"provider": "langgenius/tongyi/tongyi", "model": "qwen3-rerank"},
    }


def test_validator_rejects_invalid_knowledge_retrieval_node() -> None:
    plan = WorkflowPlan.model_validate(
        {
            "name": "bad knowledge",
            "nodes": [
                {"id": "start", "type": "start", "params": {"variables": [{"name": "query"}]}},
                {
                    "id": "knowledge",
                    "type": "knowledge-retrieval",
                    "params": {"dataset_ids": [], "retrieval_mode": "hybrid"},
                },
                {"id": "end", "type": "end", "params": {"outputs": [{"variable": "answer", "value_selector": ["start", "query"]}]}},
            ],
            "edges": [
                {"source": "start", "target": "knowledge"},
                {"source": "knowledge", "target": "end"},
            ],
        }
    )

    issues = validate_plan(plan)

    assert any(issue.code == "PLAN_KNOWLEDGE_RETRIEVAL_DATASETS_MISSING" for issue in issues)
    assert any(issue.code == "PLAN_KNOWLEDGE_RETRIEVAL_QUERY_MISSING" for issue in issues)
    assert any(issue.code == "PLAN_KNOWLEDGE_RETRIEVAL_MODE_INVALID" for issue in issues)


def test_validator_rejects_invalid_human_input_node() -> None:
    plan = WorkflowPlan.model_validate(
        {
            "name": "bad human input",
            "nodes": [
                {"id": "start", "type": "start", "params": {"variables": [{"name": "query"}]}},
                {
                    "id": "review",
                    "type": "human-input",
                    "params": {
                        "delivery_methods": [{"id": "webapp-1", "type": "webapp", "enabled": False}],
                        "user_actions": [
                            {"id": "approve", "title": "通过"},
                            {"id": "approve", "title": "再次通过"},
                        ],
                        "timeout": 0,
                        "timeout_unit": "minute",
                    },
                },
                {"id": "end", "type": "end", "params": {"outputs": [{"variable": "answer", "value_selector": ["start", "query"]}]}},
            ],
            "edges": [
                {"source": "start", "target": "review"},
                {"source": "review", "target": "end", "source_handle": "source"},
            ],
        }
    )

    issues = validate_plan(plan)

    assert any(issue.code == "PLAN_HUMAN_INPUT_DELIVERY_METHOD_ENABLED_MISSING" for issue in issues)
    assert any(issue.code == "PLAN_HUMAN_INPUT_ACTION_DUPLICATE" for issue in issues)
    assert any(issue.code == "PLAN_HUMAN_INPUT_BRANCH_INVALID" for issue in issues)
    assert any(issue.code == "PLAN_HUMAN_INPUT_ACTION_EDGE_MISSING" for issue in issues)
    assert any(issue.code == "PLAN_HUMAN_INPUT_TIMEOUT_INVALID" for issue in issues)
    assert any(issue.code == "PLAN_HUMAN_INPUT_TIMEOUT_UNIT_INVALID" for issue in issues)


def test_validator_rejects_start_incoming_and_end_outgoing() -> None:
    plan = WorkflowPlan.model_validate(
        {
            "name": "bad graph",
            "nodes": [
                {"id": "start", "type": "start", "params": {"variables": [{"name": "query"}]}},
                {"id": "llm", "type": "llm", "params": {"user_prompt": "{{#start.query#}}"}},
                {"id": "end", "type": "end", "params": {"outputs": [{"variable": "answer", "value_selector": ["llm", "text"]}]}},
            ],
            "edges": [
                {"source": "start", "target": "llm"},
                {"source": "llm", "target": "start"},
                {"source": "llm", "target": "end"},
                {"source": "end", "target": "llm"},
            ],
        }
    )

    issues = validate_plan(plan)

    assert any(issue.code == "PLAN_START_HAS_INCOMING_EDGE" for issue in issues)
    assert any(issue.code == "PLAN_END_HAS_OUTGOING_EDGE" for issue in issues)


def test_normalizer_validator_and_compiler_cover_structured_tool_node() -> None:
    normalized = normalize_plan_payload(
        {
            "name": "工具查询总结",
            "nodes": [
                {"id": "start", "type": "start", "params": {"variables": [{"name": "query"}]}},
                {
                    "id": "lookup",
                    "type": "tool",
                    "title": "调用搜索工具查询信息",
                    "params": {
                        "provider_id": "provider-1",
                        "provider_type": "builtin",
                        "tool_name": "search",
                    },
                },
                {"id": "end", "type": "end", "params": {"outputs": [{"variable": "answer", "value_selector": ["lookup", "answer"]}]}},
            ],
            "edges": [{"source": "start", "target": "lookup"}, {"source": "lookup", "target": "end"}],
        },
        tool_selections=[
            {
                "provider_id": "provider-1",
                "provider_type": "builtin",
                "provider_name": "websearch",
                "tool_name": "search",
                "tool_label": "搜索",
                "parameters": [{"name": "query", "form": "llm", "type": "string", "required": True}],
                "output_schema": {"properties": {"answer": {"type": "string"}}},
            }
        ],
    )
    plan = WorkflowPlan.model_validate(normalized.payload)
    issues = validate_plan(plan)
    dsl = _compiler().compile(plan)
    graph = yaml.safe_load(dsl)["workflow"]["graph"]
    tool_data = next(node["data"] for node in graph["nodes"] if node["id"] == "lookup")

    assert not has_errors(issues)
    assert tool_data["type"] == "tool"
    assert tool_data["provider_id"] == "provider-1"
    assert tool_data["provider_name"] == "websearch"
    assert tool_data["tool_name"] == "search"
    assert tool_data["tool_parameters"]["query"] == {"type": "mixed", "value": "{{#start.query#}}"}
    assert tool_data["output_schema"]["properties"]["answer"]["type"] == "string"


def test_normalizer_treats_empty_tool_parameter_as_missing_and_uses_schema_defaults() -> None:
    normalized = normalize_plan_payload(
        {
            "name": "网页总结",
            "nodes": [
                {"id": "start", "type": "start", "params": {"variables": [{"name": "query"}]}},
                {
                    "id": "scrape",
                    "type": "tool",
                    "params": {
                        "provider_id": "webscraper",
                        "provider_type": "builtin",
                        "tool_name": "webscraper",
                        "tool_parameters": {"url": ""},
                    },
                },
                {"id": "end", "type": "end", "params": {"outputs": [{"variable": "answer", "value_selector": ["scrape", "text"]}]}},
            ],
            "edges": [{"source": "start", "target": "scrape"}, {"source": "scrape", "target": "end"}],
        },
        tool_selections=[
            {
                "provider_id": "webscraper",
                "provider_type": "builtin",
                "provider_name": "webscraper",
                "tool_name": "webscraper",
                "tool_label": "网页爬虫",
                "parameters": [
                    {"name": "url", "form": "llm", "type": "string", "required": True},
                    {
                        "name": "user_agent",
                        "form": "form",
                        "type": "string",
                        "required": False,
                        "default": "Mozilla/5.0",
                    },
                    {
                        "name": "generate_summary",
                        "form": "form",
                        "type": "boolean",
                        "required": False,
                        "default": "false",
                    },
                ],
            }
        ],
    )
    plan = WorkflowPlan.model_validate(normalized.payload)
    issues = validate_plan(plan)
    dsl = _compiler().compile(plan)
    graph = yaml.safe_load(dsl)["workflow"]["graph"]
    tool_data = next(node["data"] for node in graph["nodes"] if node["id"] == "scrape")

    assert not has_errors(issues)
    assert tool_data["tool_parameters"]["url"] == {"type": "mixed", "value": "{{#start.query#}}"}
    assert tool_data["tool_configurations"]["user_agent"] == {"type": "mixed", "value": "Mozilla/5.0"}
    assert tool_data["tool_configurations"]["generate_summary"] == {"type": "constant", "value": False}


def test_selected_tool_explicit_parameters_override_generated_tool_values() -> None:
    normalized = normalize_plan_payload(
        {
            "name": "网页总结",
            "nodes": [
                {"id": "start", "type": "start", "params": {"variables": [{"name": "query"}]}},
                {
                    "id": "scrape",
                    "type": "tool",
                    "params": {
                        "provider_id": "webscraper",
                        "provider_type": "builtin",
                        "tool_name": "webscraper",
                        "tool_parameters": {"url": {"type": "mixed", "value": "https://llm.example"}},
                        "tool_configurations": {"generate_summary": {"type": "constant", "value": False}},
                    },
                },
                {"id": "end", "type": "end", "params": {"outputs": [{"variable": "answer", "value_selector": ["scrape", "text"]}]}},
            ],
            "edges": [{"source": "start", "target": "scrape"}, {"source": "scrape", "target": "end"}],
        },
        tool_selections=[
            {
                "provider_id": "webscraper",
                "provider_type": "builtin",
                "provider_name": "webscraper",
                "tool_name": "webscraper",
                "tool_label": "网页爬虫",
                "parameters": [
                    {"name": "url", "form": "llm", "type": "string", "required": True},
                    {
                        "name": "generate_summary",
                        "form": "form",
                        "type": "boolean",
                        "required": False,
                        "default": "false",
                    },
                ],
                "tool_parameters": {"url": {"type": "mixed", "value": "{{#start.query#}}"}},
                "tool_configurations": {"generate_summary": {"type": "constant", "value": True}},
            }
        ],
    )
    plan = WorkflowPlan.model_validate(normalized.payload)
    issues = validate_plan(plan)
    dsl = _compiler().compile(plan)
    graph = yaml.safe_load(dsl)["workflow"]["graph"]
    tool_data = next(node["data"] for node in graph["nodes"] if node["id"] == "scrape")

    assert not has_errors(issues)
    assert tool_data["tool_parameters"]["url"] == {"type": "mixed", "value": "{{#start.query#}}"}
    assert tool_data["tool_configurations"]["generate_summary"] == {"type": "constant", "value": True}


def test_normalizer_uses_first_required_tool_configuration_option_when_no_default() -> None:
    normalized = normalize_plan_payload(
        {
            "name": "语音回复",
            "nodes": [
                {"id": "start", "type": "start", "params": {"variables": [{"name": "query"}]}},
                {
                    "id": "tts",
                    "type": "tool",
                    "params": {"provider_id": "audio", "provider_type": "builtin", "tool_name": "tts"},
                },
                {"id": "end", "type": "end", "params": {"outputs": [{"variable": "answer", "value_selector": ["tts", "files"]}]}},
            ],
            "edges": [{"source": "start", "target": "tts"}, {"source": "tts", "target": "end"}],
        },
        tool_selections=[
            {
                "provider_id": "audio",
                "provider_type": "builtin",
                "provider_name": "audio",
                "tool_name": "tts",
                "tool_label": "Text To Speech",
                "parameters": [
                    {"name": "text", "form": "llm", "type": "string", "required": True},
                    {
                        "name": "model",
                        "form": "form",
                        "type": "select",
                        "required": True,
                        "options": [{"value": "langgenius/tongyi/tongyi#qwen3-tts-flash"}],
                    },
                ],
            }
        ],
    )
    plan = WorkflowPlan.model_validate(normalized.payload)
    issues = validate_plan(plan)
    dsl = _compiler().compile(plan)
    graph = yaml.safe_load(dsl)["workflow"]["graph"]
    tool_data = next(node["data"] for node in graph["nodes"] if node["id"] == "tts")

    assert not has_errors(issues)
    assert tool_data["tool_parameters"]["text"] == {"type": "mixed", "value": "{{#start.query#}}"}
    assert tool_data["tool_configurations"]["model"] == {
        "type": "constant",
        "value": "langgenius/tongyi/tongyi#qwen3-tts-flash",
    }


def test_validator_rejects_structured_tool_missing_required_parameter() -> None:
    plan = WorkflowPlan.model_validate(
        {
            "name": "bad tool",
            "nodes": [
                {"id": "start", "type": "start", "params": {"variables": [{"name": "query"}]}},
                {
                    "id": "lookup",
                    "type": "tool",
                    "params": {
                        "provider_id": "provider-1",
                        "provider_type": "builtin",
                        "tool_name": "search",
                        "paramSchemas": [{"name": "keyword", "variable": "keyword", "form": "llm", "required": True}],
                        "tool_parameters": {},
                        "tool_configurations": {},
                    },
                },
                {"id": "end", "type": "end", "params": {"outputs": [{"variable": "answer", "value_selector": ["lookup", "text"]}]}},
            ],
            "edges": [{"source": "start", "target": "lookup"}, {"source": "lookup", "target": "end"}],
        }
    )

    issues = validate_plan(plan)

    assert any(issue.code == "PLAN_TOOL_REQUIRED_PARAMETER_MISSING" for issue in issues)


def test_normalizer_compiler_and_validator_cover_iteration_node() -> None:
    normalized = normalize_plan_payload(
        {
            "name": "批量售后记录处理",
            "nodes": [
                {"id": "start", "type": "start", "params": {"variables": [{"name": "items", "type": "json"}]}},
                {
                    "id": "batch",
                    "type": "batch",
                    "params": {
                        "iterator_selector": ["start", "items", "records"],
                        "children": [
                            {"id": "batch_start", "type": "iteration-start"},
                            {
                                "id": "item_llm",
                                "type": "llm",
                                "title": "逐条生成售后建议",
                                "params": {"user_prompt": "请处理当前售后记录：{{#batch.item#}}"},
                            },
                        ],
                        "edges": [{"source": "batch_start", "target": "item_llm"}],
                    },
                },
                {"id": "end", "type": "end", "params": {"outputs": [{"variable": "answers", "value_selector": ["batch", "output"]}]}},
            ],
            "edges": [{"source": "start", "target": "batch"}, {"source": "batch", "target": "end"}],
        }
    )
    plan = WorkflowPlan.model_validate(normalized.payload)
    dsl = _compiler().compile(plan)
    graph = yaml.safe_load(dsl)["workflow"]["graph"]
    nodes = {node["id"]: node for node in graph["nodes"]}
    internal_edge = next(edge for edge in graph["edges"] if edge["source"] == "batch_start" and edge["target"] == "item_llm")

    assert next(node for node in plan.nodes if node.id == "batch").type == "iteration"
    assert validate_plan(plan) == []
    assert validate_dsl(dsl, expected_dsl_version="9.9.9") == []
    assert nodes["batch"]["data"]["type"] == "iteration"
    assert nodes["batch"]["data"]["_children"] == [
        {"nodeId": "batch_start", "nodeType": "iteration-start"},
        {"nodeId": "item_llm", "nodeType": "llm"},
    ]
    assert nodes["batch_start"]["parentId"] == "batch"
    assert nodes["batch_start"]["type"] == "custom-iteration-start"
    assert nodes["item_llm"]["parentId"] == "batch"
    assert internal_edge["data"]["isInIteration"] is True
    assert internal_edge["data"]["iteration_id"] == "batch"


def test_normalizer_compiler_and_validator_cover_loop_node() -> None:
    normalized = normalize_plan_payload(
        {
            "name": "维修状态重试检查",
            "nodes": [
                {"id": "start", "type": "start", "params": {"variables": [{"name": "query"}]}},
                {"id": "retry", "type": "retry_loop", "params": {"loop_count": 3}},
                {"id": "end", "type": "end", "params": {"outputs": [{"variable": "answer", "value_selector": ["start", "query"]}]}},
            ],
            "edges": [{"source": "start", "target": "retry"}, {"source": "retry", "target": "end"}],
        }
    )
    plan = WorkflowPlan.model_validate(normalized.payload)
    dsl = _compiler().compile(plan)
    graph = yaml.safe_load(dsl)["workflow"]["graph"]
    nodes = {node["id"]: node for node in graph["nodes"]}
    internal_edges = [edge for edge in graph["edges"] if edge["data"].get("isInLoop")]

    assert next(node for node in plan.nodes if node.id == "retry").type == "loop"
    assert validate_plan(plan) == []
    assert nodes["retry"]["data"]["type"] == "loop"
    assert nodes["retry"]["data"]["loop_count"] == 3
    assert nodes["retrystart"]["parentId"] == "retry"
    assert nodes["retrystart"]["type"] == "custom-loop-start"
    assert internal_edges and internal_edges[0]["data"]["loop_id"] == "retry"


def test_loop_break_condition_uses_loop_variable_for_dify_checklist() -> None:
    normalized = normalize_plan_payload(
        {
            "name": "维修状态重试检查",
            "nodes": [
                {"id": "start", "type": "start", "params": {"variables": [{"name": "query"}]}},
                {
                    "id": "retry",
                    "type": "loop",
                    "params": {
                        "loop_count": 3,
                        "start_node_id": "retry_start",
                        "break_conditions": [
                            {
                                "id": "done",
                                "variable_selector": ["check_status", "text"],
                                "comparison_operator": "contains",
                                "value": "已完成",
                                "varType": "string",
                            }
                        ],
                        "children": [
                            {"id": "retry_start", "type": "loop-start", "params": {}},
                            {
                                "id": "check_status",
                                "type": "llm",
                                "title": "检查维修处理状态",
                                "params": {
                                    "system_prompt": "你是维修售后状态检查专员。",
                                    "user_prompt": "请检查当前维修状态：{{#start.query#}}",
                                },
                            },
                        ],
                        "edges": [{"source": "retry_start", "target": "check_status"}],
                    },
                },
                {"id": "end", "type": "end", "params": {"outputs": [{"variable": "answer", "value_selector": ["start", "query"]}]}},
            ],
            "edges": [{"source": "start", "target": "retry"}, {"source": "retry", "target": "end"}],
        }
    )
    plan = WorkflowPlan.model_validate(normalized.payload)
    retry = next(node for node in plan.nodes if node.id == "retry")
    condition_selector = retry.params["break_conditions"][0]["variable_selector"]
    variable_label = condition_selector[1]
    assigner = next(child for child in retry.params["children"] if child["type"] == "assigner")

    assert condition_selector == ["retry", variable_label]
    assert any(item["label"] == variable_label for item in retry.params["loop_variables"])
    assert assigner["params"]["items"][0]["variable_selector"] == ["retry", variable_label]
    assert assigner["params"]["items"][0]["value"] == ["check_status", "text"]
    assert validate_plan(plan) == []

    graph = yaml.safe_load(_compiler().compile(plan))["workflow"]["graph"]
    nodes = {node["id"]: node for node in graph["nodes"]}
    internal_edges = [edge for edge in graph["edges"] if edge["data"].get("isInLoop")]
    assert nodes[assigner["id"]]["parentId"] == "retry"
    assert any(edge["source"] == "check_status" and edge["target"] == assigner["id"] for edge in internal_edges)


def test_validator_rejects_loop_break_condition_internal_child_selector() -> None:
    plan = WorkflowPlan.model_validate(
        {
            "name": "bad loop",
            "nodes": [
                {"id": "start", "type": "start", "params": {"variables": [{"name": "query"}]}},
                {
                    "id": "retry",
                    "type": "loop",
                    "params": {
                        "loop_count": 3,
                        "start_node_id": "retry_start",
                        "break_conditions": [{"variable_selector": ["check_status", "text"]}],
                        "children": [
                            {"id": "retry_start", "type": "loop-start", "params": {}},
                            {
                                "id": "check_status",
                                "type": "llm",
                                "params": {
                                    "system_prompt": "你是维修售后状态检查专员。",
                                    "user_prompt": "请检查：{{#start.query#}}",
                                },
                            },
                        ],
                        "edges": [{"source": "retry_start", "target": "check_status"}],
                    },
                },
                {"id": "end", "type": "end", "params": {"outputs": [{"variable": "answer", "value_selector": ["start", "query"]}]}},
            ],
            "edges": [{"source": "start", "target": "retry"}, {"source": "retry", "target": "end"}],
        }
    )

    issues = validate_plan(plan)

    assert any(issue.code == "PLAN_LOOP_BREAK_CONDITION_INTERNAL_SELECTOR_INVALID" for issue in issues)


def test_validator_rejects_invalid_iteration_container_graph() -> None:
    plan = WorkflowPlan.model_validate(
        {
            "name": "bad iteration",
            "nodes": [
                {"id": "start", "type": "start", "params": {"variables": [{"name": "items", "type": "json"}]}},
                {
                    "id": "batch",
                    "type": "iteration",
                    "params": {
                        "start_node_id": "batch_start",
                        "iterator_selector": ["start", "items"],
                        "output_selector": ["missing", "output"],
                        "children": [
                            {"id": "batch_start", "type": "iteration-start", "params": {}},
                            {"id": "item_template", "type": "template-transform", "params": {"template": "{{#batch.item#}}"}},
                        ],
                        "edges": [],
                    },
                },
                {"id": "end", "type": "end", "params": {"outputs": [{"variable": "answer", "value_selector": ["batch", "output"]}]}},
            ],
            "edges": [{"source": "start", "target": "batch"}, {"source": "batch", "target": "end"}],
        }
    )

    issues = validate_plan(plan)

    assert any(issue.code == "PLAN_CONTAINER_CHILD_UNREACHABLE" for issue in issues)
    assert any(issue.code == "PLAN_CONTAINER_OUTPUT_NODE_UNKNOWN" for issue in issues)


def _shorthand_branch_plan() -> dict:
    return {
        "name": "客服分流",
        "nodes": [
            {
                "id": "start_1",
                "type": "start",
                "params": {"inputs": [{"variable": "question", "type": "string", "required": True}]},
            },
            {
                "id": "if_1",
                "type": "if-else",
                "params": {
                    "cases": [
                        {"id": "refund", "condition": "{{start_1.question}} contains \"退款\""},
                        {"id": "invoice", "condition": "{{start_1.question}} contains \"发票\""},
                    ],
                    "else_case": "general",
                },
            },
            {"id": "llm_refund", "type": "llm", "params": {"prompt": "处理 {{start_1.question}}"}},
            {"id": "llm_invoice", "type": "llm", "params": {"prompt": "处理 {{start_1.question}}"}},
            {"id": "llm_general", "type": "llm", "params": {"prompt": "处理 {{start_1.question}}"}},
            {"id": "end_refund", "type": "end", "params": {"outputs": [{"variable": "answer", "value_selector": ["llm_refund", "text"]}]}},
            {"id": "end_invoice", "type": "end", "params": {"outputs": [{"variable": "answer", "value_selector": ["llm_invoice", "text"]}]}},
            {"id": "end_general", "type": "end", "params": {"outputs": [{"variable": "answer", "value_selector": ["llm_general", "text"]}]}},
        ],
        "edges": [
            {"source": "start_1", "target": "if_1"},
            {"source": "if_1", "target": "llm_refund"},
            {"source": "if_1", "target": "llm_invoice"},
            {"source": "if_1", "target": "llm_general"},
            {"source": "llm_refund", "target": "end_refund"},
            {"source": "llm_invoice", "target": "end_invoice"},
            {"source": "llm_general", "target": "end_general"},
        ],
    }
