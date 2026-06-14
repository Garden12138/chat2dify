from uuid import UUID

import pytest
import yaml
from pydantic import ValidationError

from app.agent.diff import diff_plans
from app.agent.guard import guard_plan_change
from app.agent.normalizer import normalize_plan_payload
from app.compiler.dify import DifyDslCompiler
from app.dify.graph import decompile_dify_graph
from app.models import WorkflowPlan
from app.validator import has_errors, validate_dsl, validate_plan


def _compiler() -> DifyDslCompiler:
    return DifyDslCompiler(
        dsl_version="9.9.9",
        default_model_provider="langgenius/tongyi/tongyi",
        default_model_name="qwen3.5-plus",
    )


def _stateful_plan_payload() -> dict:
    return {
        "name": "跨轮状态助手",
        "app_mode": "advanced-chat",
        "conversation_variables": [
            {
                "name": "preferred_name",
                "value_type": "string",
                "value": "",
                "description": "用户偏好的称呼",
            },
            {
                "name": "turn_count",
                "value_type": "number",
                "value": 0,
                "description": "累计对话轮数",
            },
            {
                "name": "confirmed",
                "value_type": "boolean",
                "value": False,
            },
            {
                "name": "profile",
                "value_type": "object",
                "value": {},
            },
            {
                "name": "tags",
                "value_type": "array[string]",
                "value": [],
            },
            {
                "name": "scores",
                "value_type": "array[number]",
                "value": [],
            },
            {
                "name": "flags",
                "value_type": "array[boolean]",
                "value": [],
            },
            {
                "name": "records",
                "value_type": "array[object]",
                "value": [],
            },
        ],
        "nodes": [
            {
                "id": "start",
                "type": "start",
                "title": "接收本轮消息",
                "params": {"variables": []},
            },
            {
                "id": "remember",
                "type": "assigner",
                "title": "保存跨轮状态",
                "params": {
                    "items": [
                        {
                            "variable_selector": ["conversation", "preferred_name"],
                            "input_type": "variable",
                            "operation": "over-write",
                            "value": ["sys", "query"],
                        },
                        {
                            "variable_selector": ["conversation", "turn_count"],
                            "input_type": "constant",
                            "operation": "+=",
                            "value": 1,
                        },
                        {
                            "variable_selector": ["conversation", "tags"],
                            "input_type": "constant",
                            "operation": "append",
                            "value": "active",
                        },
                    ]
                },
            },
            {
                "id": "answer",
                "type": "answer",
                "title": "返回记忆结果",
                "params": {
                    "answer": (
                        "你好 {{#conversation.preferred_name#}}，"
                        "这是第 {{#conversation.turn_count#}} 轮。"
                    )
                },
            },
        ],
        "edges": [
            {"source": "start", "target": "remember"},
            {"source": "remember", "target": "answer"},
        ],
    }


def _stateful_plan() -> WorkflowPlan:
    normalized = normalize_plan_payload(
        _stateful_plan_payload(),
        app_mode="advanced-chat",
    )
    return WorkflowPlan.model_validate(normalized.payload)


def test_conversation_variables_normalize_with_stable_ids_and_defaults() -> None:
    first = _stateful_plan()
    second = _stateful_plan()

    assert [item.id for item in first.conversation_variables] == [
        item.id for item in second.conversation_variables
    ]
    assert all(str(UUID(item.id)) == item.id for item in first.conversation_variables)
    assert all(
        item.selector == ["conversation", item.name]
        for item in first.conversation_variables
    )
    assert {item.value_type for item in first.conversation_variables} == {
        "string",
        "number",
        "boolean",
        "object",
        "array[string]",
        "array[number]",
        "array[boolean]",
        "array[object]",
    }


def test_conversation_variables_compile_and_decompile_round_trip() -> None:
    plan = _stateful_plan()
    data = yaml.safe_load(_compiler().compile(plan))

    restored = decompile_dify_graph(
        data["workflow"]["graph"],
        name=plan.name,
        app_mode="advanced-chat",
        conversation_variables=data["workflow"]["conversation_variables"],
    )

    assert restored.conversation_variables == plan.conversation_variables
    assert not has_errors(validate_plan(restored))
    assert validate_dsl(
        yaml.safe_dump(data),
        expected_dsl_version="9.9.9",
    ) == []


@pytest.mark.parametrize(
    ("value_type", "value"),
    [
        ("number", True),
        ("boolean", 1),
        ("object", []),
        ("array[string]", [1]),
        ("array[number]", [False]),
        ("array[boolean]", [1]),
        ("array[object]", ["bad"]),
    ],
)
def test_conversation_variable_rejects_invalid_defaults(
    value_type: str,
    value: object,
) -> None:
    payload = _stateful_plan_payload()
    payload["conversation_variables"] = [
        {
            "name": "invalid",
            "value_type": value_type,
            "value": value,
        }
    ]

    with pytest.raises(ValidationError, match="invalid default"):
        WorkflowPlan.model_validate(
            normalize_plan_payload(payload, app_mode="advanced-chat").payload
        )


def test_conversation_variables_reject_secret_duplicates_and_workflow_mode() -> None:
    payload = _stateful_plan_payload()
    payload["conversation_variables"] = [
        {"name": "token", "value_type": "secret", "value": "hidden"}
    ]
    with pytest.raises(ValueError, match="unsupported type"):
        normalize_plan_payload(payload, app_mode="advanced-chat")

    duplicate = _stateful_plan_payload()
    duplicate["conversation_variables"] = [
        {"name": "name", "value_type": "string", "value": ""},
        {"name": "name", "value_type": "string", "value": ""},
    ]
    with pytest.raises(ValidationError, match="names must be unique"):
        WorkflowPlan.model_validate(
            normalize_plan_payload(duplicate, app_mode="advanced-chat").payload
        )

    workflow = _stateful_plan_payload()
    workflow["app_mode"] = "workflow"
    workflow["nodes"][-1] = {
        "id": "answer",
        "type": "end",
        "title": "返回结果",
        "params": {
            "outputs": [
                {
                    "variable": "answer",
                    "value_selector": ["remember", "result"],
                }
            ]
        },
    }
    with pytest.raises(ValidationError, match="only supported by advanced-chat"):
        WorkflowPlan.model_validate(
            normalize_plan_payload(workflow, app_mode="workflow").payload
        )


def test_chatflow_assigner_rejects_illegal_targets_operations_and_types() -> None:
    payload = _stateful_plan_payload()
    remember = payload["nodes"][1]
    remember["params"]["items"] = [
        {
            "variable_selector": ["sys", "query"],
            "input_type": "constant",
            "operation": "over-write",
            "value": "bad",
        },
        {
            "variable_selector": ["conversation", "preferred_name"],
            "input_type": "constant",
            "operation": "append",
            "value": "bad",
        },
        {
            "variable_selector": ["conversation", "turn_count"],
            "input_type": "constant",
            "operation": "+=",
            "value": "1",
        },
    ]
    plan = WorkflowPlan.model_validate(
        normalize_plan_payload(payload, app_mode="advanced-chat").payload
    )

    codes = {issue.code for issue in validate_plan(plan)}

    assert "PLAN_CHATFLOW_ASSIGNER_TARGET_INVALID" in codes
    assert "PLAN_CHATFLOW_ASSIGNER_OPERATION_INVALID" in codes
    assert "PLAN_CHATFLOW_ASSIGNER_VALUE_TYPE_INVALID" in codes


def test_conversation_variable_diff_and_destructive_guard() -> None:
    before = _stateful_plan()
    after = before.model_copy(deep=True)
    after.conversation_variables = [
        variable
        for variable in after.conversation_variables
        if variable.name != "profile"
    ]
    after.conversation_variables[0].name = "display_name"
    after.conversation_variables[0].selector = ["conversation", "display_name"]

    changes = diff_plans(before, after)
    guard = guard_plan_change(before, after, changes)

    assert any(
        change["type"] == "conversation_variable_removed"
        for change in changes
    )
    assert any(
        change["type"] == "conversation_variable_updated"
        and change["field"] == "name"
        for change in changes
    )
    assert guard.ok is False
    assert guard.risk == "high"
    assert any(
        issue.code == "PLAN_CHANGE_CONVERSATION_VARIABLE_DESTRUCTIVE"
        for issue in guard.issues
    )
