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


def test_compiler_outputs_dify_workflow_dsl() -> None:
    plan = fallback_plan("Summarize the input", app_name="Summary")
    dsl = _compiler().compile(plan)
    data = yaml.safe_load(dsl)

    assert data["version"] == "9.9.9"
    assert data["kind"] == "app"
    assert data["app"]["mode"] == "workflow"
    assert data["dependencies"] == []
    assert data["workflow"]["conversation_variables"] == []
    assert data["workflow"]["environment_variables"] == []
    assert data["workflow"]["graph"]["nodes"]
    assert data["workflow"]["graph"]["edges"]


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
            "name": "seven nodes",
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
                {"id": "llm_yes", "type": "llm", "params": {"user_prompt": "Handle {{#code.result#}} urgently"}},
                {"id": "llm_no", "type": "llm", "params": {"user_prompt": "Handle {{#code.result#}} normally"}},
                {"id": "end_yes", "type": "end", "params": {"outputs": [{"variable": "answer", "value_selector": ["llm_yes", "text"]}]}},
                {"id": "end_no", "type": "end", "params": {"outputs": [{"variable": "answer", "value_selector": ["llm_no", "text"]}]}},
            ],
            "edges": [
                {"source": "start", "target": "http"},
                {"source": "http", "target": "template"},
                {"source": "template", "target": "code"},
                {"source": "code", "target": "branch"},
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

    assert node_types == {"start", "llm", "code", "if-else", "end", "http-request", "template-transform"}
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
