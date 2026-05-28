import json

import pytest

from app.agent.planner import PlannerError, WorkflowPlanner
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


def test_planner_success_normalizes_shorthand() -> None:
    planner = FakePlanner([json.dumps(_shorthand_plan())])

    result = planner.generate("客服分流", app_name="客服分流", dsl_version="9.9.9")

    assert result.used_fallback is False
    assert result.attempts == 1
    assert result.repaired is True
    assert result.plan.nodes[0].params["variables"][0]["name"] == "question"
    assert result.plan.edges[1].source_handle == "refund"
    assert result.plan.edges[2].source_handle == "false"


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
