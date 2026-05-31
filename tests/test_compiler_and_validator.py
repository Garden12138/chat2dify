import yaml
from pydantic import ValidationError

from app.agent.planner import fallback_plan
from app.agent.normalizer import normalize_plan_payload
from app.compiler.dify import DifyDslCompiler
from app.models import WorkflowPlan
from app.validator import validate_dsl, validate_plan


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
                        "variable": ["start", "items"],
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

    assert nodes["doc"]["type"] == "document-extractor"
    assert nodes["doc"]["variable_selector"] == ["start", "files"]
    assert nodes["aggregator"]["variables"] == [["doc", "text"], ["start", "query"]]
    assert nodes["assign"]["items"][0]["value"] == ["aggregator", "output"]
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
                {"id": "list", "type": "list-filter", "params": {"variable_selector": "{{start.items}}", "type": "array_string", "limit": 2}},
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

    assert node_types["doc"] == "document-extractor"
    assert node_types["agg"] == "variable-aggregator"
    assert node_types["list"] == "list-operator"
    assert next(node for node in plan.nodes if node.id == "doc").params["variable_selector"] == ["start", "files"]
    assert validate_plan(plan) == []


def test_validator_rejects_invalid_stable_builtin_nodes() -> None:
    plan = WorkflowPlan.model_validate(
        {
            "name": "bad builtins",
            "nodes": [
                {"id": "start", "type": "start", "params": {"variables": [{"name": "query"}]}},
                {"id": "list", "type": "list-operator", "params": {"variable": ["start", "query"], "var_type": "string"}},
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
