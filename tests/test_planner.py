import json

import pytest

from app.agent.planner import PlannerError, WorkflowPlanner, fallback_plan
from app.config import Settings


def _settings(openai_api_key: str | None = "token") -> Settings:
    env = {
        "DIFY_SOURCE_DIR": "../dify",
        "DIFY_DEFAULT_MODEL_PROVIDER": "openai",
        "DIFY_DEFAULT_MODEL_NAME": "gpt-4o-mini",
    }
    if openai_api_key:
        env["OPENAI_API_KEY"] = openai_api_key
    return Settings.from_env(env, validate_dify=False)


class FakePlanner(WorkflowPlanner):
    def __init__(self, responses: list[str]) -> None:
        super().__init__(_settings())
        self.responses = responses
        self.last_errors: list[str] = []

    def _call_llm(self, message: str, *, app_name: str | None, last_error: str = "") -> str:
        self.last_errors.append(last_error)
        if not self.responses:
            raise PlannerError("no fake response")
        return self.responses.pop(0)


def test_planner_fallback_when_no_openai_key() -> None:
    planner = WorkflowPlanner(_settings(openai_api_key=None))

    result = planner.generate("Summarize", app_name="Fallback", dsl_version="9.9.9")

    assert result.used_fallback is True
    assert result.attempts == 0
    assert result.plan.name == "Fallback"
    assert result.metadata()["mode"] == "fallback"


def test_fallback_plan_uses_semantic_titles_and_split_prompts() -> None:
    plan = fallback_plan("理发售后服务工作流")
    titles = [node.title for node in plan.nodes]
    llm = next(node for node in plan.nodes if node.type == "llm")

    assert titles == ["接收理发售后服务诉求", "生成理发售后服务回复", "返回理发售后服务结果"]
    assert "你是理发售后服务专员" in llm.params["system_prompt"]
    assert "{{#start.query#}}" in llm.params["user_prompt"]
    assert "审核标准" in llm.params["system_prompt"]
    assert "审核标准" not in llm.params["user_prompt"]


def test_planner_success_normalizes_shorthand() -> None:
    planner = FakePlanner([json.dumps(_shorthand_plan())])

    result = planner.generate("客服分流", app_name="客服分流", dsl_version="9.9.9")

    assert result.used_fallback is False
    assert result.attempts == 1
    assert result.repaired is True
    assert result.plan.nodes[0].params["variables"][0]["name"] == "question"
    assert result.plan.edges[1].source_handle == "refund"
    assert result.plan.edges[2].source_handle == "false"


def test_planner_accepts_understanding_nodes() -> None:
    planner = FakePlanner([json.dumps(_understanding_plan())])

    result = planner.generate("修车售后分类并提取字段", app_name="修车售后服务工作流", dsl_version="9.9.9")

    assert result.used_fallback is False
    assert {node.type for node in result.plan.nodes} >= {"question-classifier", "parameter-extractor"}
    extractor = next(node for node in result.plan.nodes if node.id == "extract")
    classifier = next(node for node in result.plan.nodes if node.id == "classifier")
    assert extractor.params["parameters"][0]["name"] == "car_model"
    assert classifier.params["classes"][0]["id"] == "complaint"
    assert [edge.source_handle for edge in result.plan.edges if edge.source == "classifier"] == ["complaint", "consult"]


def test_planner_accepts_stable_builtin_nodes() -> None:
    planner = FakePlanner([json.dumps(_stable_builtin_plan())])

    result = planner.generate("维修单附件总结并筛选记录", app_name="维修单处理", dsl_version="9.9.9")

    assert result.used_fallback is False
    assert {node.type for node in result.plan.nodes} >= {
        "document-extractor",
        "variable-aggregator",
        "list-operator",
    }
    doc = next(node for node in result.plan.nodes if node.id == "doc")
    list_node = next(node for node in result.plan.nodes if node.id == "list")
    assert doc.params["variable_selector"] == ["start", "files"]
    assert list_node.params["limit"]["size"] == 1


def test_planner_self_repairs_after_validation_failure() -> None:
    bad = {
        "name": "bad",
        "nodes": [
            {"id": "start", "type": "start", "params": {"variables": [{"name": "query"}]}},
            {"id": "llm", "type": "llm", "params": {"user_prompt": "{{#start.missing#}}"}},
            {"id": "end", "type": "end", "params": {"outputs": [{"variable": "answer", "value_selector": ["llm", "text"]}]}},
        ],
        "edges": [
            {"source": "start", "target": "llm"},
            {"source": "llm", "target": "end"},
        ],
    }
    good = {
        **bad,
        "nodes": [
            {"id": "start", "type": "start", "params": {"variables": [{"name": "query"}]}},
            {"id": "llm", "type": "llm", "params": {"user_prompt": "{{#start.query#}}"}},
            {"id": "end", "type": "end", "params": {"outputs": [{"variable": "answer", "value_selector": ["llm", "text"]}]}},
        ],
    }
    planner = FakePlanner([json.dumps(bad), json.dumps(good)])

    result = planner.generate("fix", dsl_version="9.9.9")

    assert result.attempts == 2
    assert result.repaired is True
    assert "PLAN_VARIABLE_UNKNOWN" in planner.last_errors[1]


def test_planner_fails_after_three_bad_attempts() -> None:
    planner = FakePlanner(["{}", "{}", "{}"])

    with pytest.raises(PlannerError) as exc:
        planner.generate("bad", dsl_version="9.9.9")

    assert "after 3 attempts" in str(exc.value)


def _shorthand_plan() -> dict:
    return {
        "nodes": [
            {"id": "start_1", "type": "start", "params": {"inputs": [{"variable": "question"}]}},
            {
                "id": "if_1",
                "type": "if-else",
                "params": {"cases": [{"id": "refund", "condition": "{{start_1.question}} contains \"退款\""}]},
            },
            {"id": "llm_refund", "type": "llm", "params": {"prompt": "处理 {{start_1.question}}"}},
            {"id": "llm_general", "type": "llm", "params": {"prompt": "通用 {{start_1.question}}"}},
            {"id": "end_refund", "type": "end", "params": {"outputs": [{"variable": "answer", "value_selector": ["llm_refund", "text"]}]}},
            {"id": "end_general", "type": "end", "params": {"outputs": [{"variable": "answer", "value_selector": ["llm_general", "text"]}]}},
        ],
        "edges": [
            {"source": "start_1", "target": "if_1"},
            {"source": "if_1", "target": "llm_refund"},
            {"source": "if_1", "target": "llm_general"},
            {"source": "llm_refund", "target": "end_refund"},
            {"source": "llm_general", "target": "end_general"},
        ],
    }


def _understanding_plan() -> dict:
    return {
        "nodes": [
            {"id": "start", "type": "start", "title": "接收修车售后诉求", "params": {"variables": [{"name": "query"}]}},
            {
                "id": "extract",
                "type": "parameter-extractor",
                "title": "提取修车售后信息",
                "params": {
                    "query": ["start", "query"],
                    "parameters": [
                        {"name": "car_model", "type": "string", "description": "车辆型号", "required": False},
                        {"name": "issue", "type": "string", "description": "用户诉求", "required": True},
                    ],
                },
            },
            {
                "id": "classifier",
                "type": "question-classifier",
                "title": "识别售后类型",
                "params": {
                    "query_variable_selector": ["start", "query"],
                    "classes": [
                        {"id": "complaint", "name": "投诉"},
                        {"id": "consult", "name": "咨询"},
                    ],
                },
            },
            {"id": "llm_complaint", "type": "llm", "title": "生成投诉回复", "params": {"user_prompt": "投诉：{{#extract.issue#}}"}},
            {"id": "llm_consult", "type": "llm", "title": "生成咨询回复", "params": {"user_prompt": "咨询：{{#start.query#}}"}},
            {"id": "end_complaint", "type": "end", "title": "返回投诉结果", "params": {"outputs": [{"variable": "answer", "value_selector": ["llm_complaint", "text"]}]}},
            {"id": "end_consult", "type": "end", "title": "返回咨询结果", "params": {"outputs": [{"variable": "answer", "value_selector": ["llm_consult", "text"]}]}},
        ],
        "edges": [
            {"source": "start", "target": "extract"},
            {"source": "extract", "target": "classifier"},
            {"source": "classifier", "target": "llm_complaint", "source_handle": "complaint"},
            {"source": "classifier", "target": "llm_consult", "source_handle": "consult"},
            {"source": "llm_complaint", "target": "end_complaint"},
            {"source": "llm_consult", "target": "end_consult"},
        ],
    }


def _stable_builtin_plan() -> dict:
    return {
        "nodes": [
            {
                "id": "start",
                "type": "start",
                "title": "接收维修单和售后记录",
                "params": {
                    "variables": [
                        {"name": "query", "type": "paragraph"},
                        {"name": "files", "type": "file-list"},
                        {"name": "items", "type": "json"},
                    ]
                },
            },
            {"id": "doc", "type": "document-extractor", "title": "提取维修单文本", "params": {"variable_selector": ["start", "files"], "is_array_file": True}},
            {
                "id": "aggregate",
                "type": "variable-aggregator",
                "title": "聚合售后上下文",
                "params": {"variables": [["doc", "text"], ["start", "query"]], "output_type": "string"},
            },
            {
                "id": "list",
                "type": "list-operator",
                "title": "筛选投诉记录",
                "params": {
                    "variable": ["start", "items"],
                    "var_type": "array[string]",
                    "item_var_type": "string",
                    "filter_by": {"enabled": True, "conditions": [{"comparison_operator": "contains", "value": "投诉"}]},
                    "limit": {"enabled": True, "size": 1},
                },
            },
            {"id": "llm", "type": "llm", "title": "生成维修单总结", "params": {"user_prompt": "{{#aggregate.output#}}\n{{#list.first_record#}}"}},
            {"id": "end", "type": "end", "title": "返回总结结果", "params": {"outputs": [{"variable": "answer", "value_selector": ["llm", "text"]}]}},
        ],
        "edges": [
            {"source": "start", "target": "doc"},
            {"source": "doc", "target": "aggregate"},
            {"source": "aggregate", "target": "list"},
            {"source": "list", "target": "llm"},
            {"source": "llm", "target": "end"},
        ],
    }
