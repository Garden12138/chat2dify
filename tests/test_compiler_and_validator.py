import yaml
from pydantic import ValidationError

from app.agent.planner import fallback_plan
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


def test_compiler_normalizes_common_llm_plan_shorthand() -> None:
    plan = WorkflowPlan.model_validate(
        {
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
    )

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
