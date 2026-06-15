from __future__ import annotations

from app.agent.normalizer import normalize_plan_payload
from app.agent.planner import PlannerError, WorkflowPlanner, fallback_plan
from app.config import Settings
from app.dify.client import DifyModelListItem, DifyModelListResult
from app.dify.runtime_models import (
    apply_default_runtime_models,
    collect_runtime_model_bindings,
    resolve_runtime_model_selections,
    validate_agent_selection_models,
    validate_runtime_model_bindings,
)
from app.models import WorkflowPlan


def _settings() -> Settings:
    return Settings.from_env(
        {
            "DIFY_SOURCE_DIR": "../dify",
            "DIFY_DEFAULT_MODEL_PROVIDER": "provider-a",
            "DIFY_DEFAULT_MODEL_NAME": "model-a",
            "OPENAI_API_KEY": "token",
            "PLANNER_DEFAULT_PROVIDER": "openai",
        },
        validate_dify=False,
    )


def _model(
    provider: str,
    name: str,
    *,
    features: list[str] | None = None,
    status: str = "active",
    provider_status: str = "active",
    deprecated: bool = False,
) -> DifyModelListItem:
    return DifyModelListItem(
        provider=provider,
        provider_label=provider,
        model=name,
        model_label=name,
        model_type="llm",
        status=status,
        provider_status=provider_status,
        deprecated=deprecated,
        features=features or [],
        context_length=32768,
        mode="chat",
    )


def _catalog(*models: DifyModelListItem) -> DifyModelListResult:
    return DifyModelListResult(
        data=list(models),
        count=len(models),
        model_type="llm",
        providers=sorted({model.provider for model in models}),
        features=sorted({feature for model in models for feature in model.features}),
    )


def test_default_and_multiple_runtime_model_selections_are_server_hydrated() -> None:
    first = _model("provider-a", "model-a", features=["vision"])
    second = _model("provider-b", "model-b", features=["tool-call"])
    catalog = _catalog(first, second)

    default = resolve_runtime_model_selections(catalog, _settings(), None)
    multiple = resolve_runtime_model_selections(
        catalog,
        _settings(),
        [
            {"provider": "provider-b", "model": "model-b", "status": "fake"},
            {"provider": "provider-a", "model": "model-a", "features": ["fake"]},
        ],
    )

    assert default == [first]
    assert multiple == [second, first]
    assert multiple[0].status == "active"
    assert multiple[0].features == ["tool-call"]


def test_default_model_is_filled_recursively_for_direct_nodes_and_agent() -> None:
    payload = {
        "name": "模型递归",
        "app_mode": "advanced-chat",
        "nodes": [
            {"id": "start", "type": "start", "title": "接收问题", "params": {}},
            {
                "id": "iteration",
                "type": "iteration",
                "title": "批量判断",
                "params": {
                    "iterator_selector": ["start", "items"],
                    "output_selector": ["classify", "class_name"],
                    "children": [
                        {"id": "inside", "type": "iteration-start", "params": {}},
                        {
                            "id": "classify",
                            "type": "question-classifier",
                            "title": "判断类型",
                            "params": {
                                "classes": [{"id": "a", "name": "A"}],
                            },
                        },
                    ],
                    "edges": [{"source": "inside", "target": "classify"}],
                },
            },
            {
                "id": "agent",
                "type": "agent",
                "title": "执行工具智能体",
                "params": {
                    "parameters": [
                        {"name": "model", "type": "model-selector", "required": True},
                    ],
                    "agent_parameters": {},
                },
            },
            {
                "id": "answer",
                "type": "answer",
                "title": "回复结果",
                "params": {"answer": "{{#agent.text#}}"},
            },
        ],
        "edges": [
            {"source": "start", "target": "iteration"},
            {"source": "iteration", "target": "agent"},
            {"source": "agent", "target": "answer"},
        ],
    }
    normalized = normalize_plan_payload(
        payload,
        app_mode="advanced-chat",
        model_selections=[{"provider": "provider-a", "model": "model-a"}],
    )
    plan = WorkflowPlan.model_validate(normalized.payload)
    bindings = collect_runtime_model_bindings(plan)

    assert {
        (binding["path"], binding["provider"], binding["model"])
        for binding in bindings
    } == {
        ("iteration/classify", "provider-a", "model-a"),
        ("agent.agent_parameters.model", "provider-a", "model-a"),
    }


def test_runtime_model_validation_rejects_allowlist_and_capability_violations() -> None:
    plan = fallback_plan("多模态客服", app_mode="advanced-chat")
    raw = apply_default_runtime_models(
        plan.model_dump(),
        [{"provider": "provider-b", "model": "model-b"}],
    )
    raw["nodes"][1]["params"]["vision"] = {
        "enabled": True,
        "configs": {"variable_selector": ["start", "sys.files"]},
    }
    plan = WorkflowPlan.model_validate(raw)
    allowed = _model("provider-a", "model-a", features=["vision"])
    outside = _model("provider-b", "model-b", features=[])

    issues = validate_runtime_model_bindings(
        plan,
        _catalog(allowed, outside),
        allowed_models=[allowed],
    )

    assert [issue.code for issue in issues] == ["PLAN_MODEL_NOT_SELECTED"]

    issues = validate_runtime_model_bindings(
        plan,
        _catalog(outside),
        allowed_models=[outside],
    )
    assert [issue.code for issue in issues] == ["PLAN_MODEL_FEATURE_NOT_SUPPORTED"]


def test_tool_bound_agent_requires_tool_call_capability() -> None:
    plan = WorkflowPlan.model_validate(
        {
            "name": "Agent 模型",
            "app_mode": "advanced-chat",
            "nodes": [
                {"id": "start", "type": "start", "title": "接收任务", "params": {}},
                {
                    "id": "agent",
                    "type": "agent",
                    "title": "执行搜索任务",
                    "params": {
                        "parameters": [
                            {"name": "model", "type": "model-selector"},
                            {"name": "tools", "type": "array[tools]"},
                        ],
                        "agent_parameters": {
                            "model": {
                                "type": "constant",
                                "value": {
                                    "provider": "provider-a",
                                    "model": "model-a",
                                },
                            },
                            "tools": {
                                "type": "constant",
                                "value": [{"enabled": True, "tool_name": "search"}],
                            },
                        },
                    },
                },
                {
                    "id": "answer",
                    "type": "answer",
                    "title": "回复搜索结果",
                    "params": {"answer": "{{#agent.text#}}"},
                },
            ],
            "edges": [
                {"source": "start", "target": "agent"},
                {"source": "agent", "target": "answer"},
            ],
        }
    )
    no_tools = _model("provider-a", "model-a", features=["vision"])
    supports_tools = _model("provider-a", "model-a", features=["multi-tool-call"])

    rejected = validate_runtime_model_bindings(
        plan,
        _catalog(no_tools),
        allowed_models=[no_tools],
    )
    accepted = validate_runtime_model_bindings(
        plan,
        _catalog(supports_tools),
        allowed_models=[supports_tools],
    )

    assert [issue.code for issue in rejected] == [
        "PLAN_MODEL_FEATURE_NOT_SUPPORTED"
    ]
    assert accepted == []


def test_agent_selection_rejects_whitelist_and_tool_call_mismatch() -> None:
    selected = _model("provider-a", "model-a", features=["vision"])
    agent_selection = {
        "parameters": [
            {"name": "model", "type": "model-selector"},
            {"name": "tools", "type": "array[tools]"},
        ],
        "agent_parameters": {
            "model": {
                "type": "constant",
                "value": {"provider": "provider-a", "model": "model-a"},
            },
            "tools": {
                "type": "constant",
                "value": [{"enabled": True, "tool_name": "search"}],
            },
        },
    }

    capability_issues = validate_agent_selection_models(
        [agent_selection],
        _catalog(selected),
        [selected],
    )
    agent_selection["agent_parameters"]["model"]["value"] = {
        "provider": "provider-b",
        "model": "model-b",
    }
    whitelist_issues = validate_agent_selection_models(
        [agent_selection],
        _catalog(selected, _model("provider-b", "model-b", features=["tool-call"])),
        [selected],
    )

    assert capability_issues[0].code == "AGENT_MODEL_FEATURE_NOT_SUPPORTED"
    assert whitelist_issues[0].code == "AGENT_MODEL_NOT_SELECTED"


def test_existing_unavailable_model_is_warning_but_new_binding_is_error() -> None:
    before = fallback_plan("客服", app_mode="advanced-chat")
    before = WorkflowPlan.model_validate(
        apply_default_runtime_models(
            before.model_dump(),
            [{"provider": "legacy", "model": "old"}],
        )
    )
    after = before.model_copy(deep=True)
    current = _model(
        "legacy",
        "old",
        status="no-configure",
        provider_status="active",
    )
    replacement = _model(
        "provider-a",
        "model-a",
        status="no-configure",
        provider_status="active",
    )

    unchanged = validate_runtime_model_bindings(
        after,
        _catalog(current, replacement),
        allowed_models=[replacement],
        baseline_plan=before,
    )
    after.nodes[1].params["model_provider"] = "provider-a"
    after.nodes[1].params["model_name"] = "model-a"
    changed = validate_runtime_model_bindings(
        after,
        _catalog(current, replacement),
        allowed_models=[replacement],
        baseline_plan=before,
    )

    assert unchanged[0].severity == "warning"
    assert unchanged[0].code == "PLAN_MODEL_UNAVAILABLE"
    assert changed[0].severity == "error"
    assert changed[0].code == "PLAN_MODEL_UNAVAILABLE"


class _RetryingPlanner(WorkflowPlanner):
    def __init__(self, responses: list[str]) -> None:
        super().__init__(_settings())
        self.responses = responses
        self.errors: list[str] = []

    def _call_llm(
        self,
        message,
        *,
        app_name,
        last_error="",
        tool_selections=None,
        agent_selections=None,
        model_selections=None,
        app_mode="workflow",
    ):
        self.errors.append(last_error)
        if not self.responses:
            raise PlannerError("no response")
        return self.responses.pop(0)


def test_planner_retries_invented_model_and_fallback_uses_primary_model() -> None:
    bad = fallback_plan("客服", app_mode="advanced-chat").model_dump()
    bad["nodes"][1]["params"]["model_provider"] = "invented"
    bad["nodes"][1]["params"]["model_name"] = "invented"
    good = fallback_plan("客服", app_mode="advanced-chat").model_dump()
    model = _model("provider-a", "model-a")
    catalog = _catalog(model)
    planner = _RetryingPlanner([__import__("json").dumps(bad), __import__("json").dumps(good)])

    result = planner.generate(
        "客服",
        app_mode="advanced-chat",
        dsl_version="9.9.9",
        model_selections=[model],
        model_catalog=catalog,
    )

    assert result.attempts == 2
    assert "PLAN_MODEL_NOT_SELECTED" in planner.errors[1]
    assert result.plan.nodes[1].params["model_provider"] == "provider-a"
    assert result.plan.nodes[1].params["model_name"] == "model-a"
