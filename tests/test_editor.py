import json

import pytest

from app.agent.editor import WorkflowEditPlanner
from app.agent.planner import PlannerError, fallback_plan
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


class FakeEditPlanner(WorkflowEditPlanner):
    def __init__(self, responses: list[str]) -> None:
        super().__init__(_settings())
        self.responses = responses
        self.last_errors: list[str] = []

    def _call_llm(self, message, *, current_plan, last_error="", tool_selections=None) -> str:
        self.last_errors.append(last_error)
        if not self.responses:
            raise PlannerError("no fake response")
        return self.responses.pop(0)


def test_edit_planner_requires_openai_key() -> None:
    planner = WorkflowEditPlanner(_settings(openai_api_key=None))

    with pytest.raises(PlannerError) as exc:
        planner.generate("修改 prompt", current_plan=fallback_plan("hello"), dsl_version="9.9.9")

    assert "OPENAI_API_KEY" in str(exc.value)


def test_edit_planner_success_returns_full_revised_plan() -> None:
    current = fallback_plan("hello", app_name="Existing")
    revised = current.model_dump()
    revised["nodes"][1]["params"]["user_prompt"] = "请用中文回答 {{#start.query#}}"
    planner = FakeEditPlanner([json.dumps(revised)])

    result = planner.generate("改成中文", current_plan=current, dsl_version="9.9.9")

    assert result.attempts == 1
    assert result.plan.name == "Existing"
    assert result.plan.nodes[1].params["user_prompt"] == "请用中文回答 {{#start.query#}}"
    assert result.metadata()["mode"] == "llm-edit"


def test_edit_planner_normalizes_generic_titles_and_empty_system_prompt() -> None:
    current = fallback_plan("理发售后服务工作流")
    revised = current.model_dump()
    revised["nodes"][0]["title"] = "Start"
    revised["nodes"][1]["title"] = "LLM"
    revised["nodes"][1]["params"].pop("system_prompt")
    revised["nodes"][1]["params"]["user_prompt"] = "请根据以下售后诉求生成回复：{{#start.query#}}"
    revised["nodes"][2]["title"] = "End"
    planner = FakeEditPlanner([json.dumps(revised)])

    result = planner.generate("修复节点名称", current_plan=current, dsl_version="9.9.9")

    assert [node.title for node in result.plan.nodes] == [
        "接收理发售后服务诉求",
        "生成售后服务回复",
        "返回售后服务结果",
    ]
    assert "你是理发售后服务专员" in result.plan.nodes[1].params["system_prompt"]
    assert result.plan.nodes[1].params["user_prompt"] == "请根据以下售后诉求生成回复：{{#start.query#}}"
    assert result.repaired is True


def test_edit_planner_self_repairs_after_validation_failure() -> None:
    current = fallback_plan("hello", app_name="Existing")
    bad = current.model_dump()
    bad["nodes"][1]["params"]["user_prompt"] = "{{#start.missing#}}"
    good = current.model_dump()
    good["nodes"][1]["params"]["user_prompt"] = "{{#start.query#}}"
    planner = FakeEditPlanner([json.dumps(bad), json.dumps(good)])

    result = planner.generate("fix", current_plan=current, dsl_version="9.9.9")

    assert result.attempts == 2
    assert result.repaired is True
    assert "PLAN_VARIABLE_UNKNOWN" in planner.last_errors[1]


def test_edit_planner_fails_after_three_bad_attempts() -> None:
    current = fallback_plan("hello")
    planner = FakeEditPlanner(["{}", "{}", "{}"])

    with pytest.raises(PlannerError) as exc:
        planner.generate("bad", current_plan=current, dsl_version="9.9.9")

    assert "after 3 attempts" in str(exc.value)
