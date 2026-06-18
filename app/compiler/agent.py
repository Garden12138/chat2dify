from __future__ import annotations

from typing import Any

import yaml

from app.config import Settings
from app.dify.client import DifyModelListItem
from app.language import ensure_language_response_instruction


def compile_chat_app_dsl(
    *,
    message: str,
    app_name: str | None,
    dsl_version: str,
    settings: Settings,
    app_description: str | None = None,
    model_selections: list[DifyModelListItem] | None = None,
) -> str:
    data = _configured_app_dsl(
        message=message,
        app_name=app_name,
        app_description=app_description,
        dsl_version=dsl_version,
        settings=settings,
        app_mode="chat",
        model_config=_chat_model_config(message, settings, model_selections),
    )
    return yaml.safe_dump(data, allow_unicode=True, sort_keys=False)


def compile_agent_app_dsl(
    *,
    message: str,
    app_name: str | None,
    dsl_version: str,
    settings: Settings,
    app_description: str | None = None,
    model_selections: list[DifyModelListItem] | None = None,
    tool_selections: list[dict[str, Any]] | None = None,
) -> str:
    data = _configured_app_dsl(
        message=message,
        app_name=app_name,
        app_description=app_description,
        dsl_version=dsl_version,
        settings=settings,
        app_mode="agent-chat",
        model_config=_agent_model_config(message, settings, model_selections, tool_selections or []),
    )
    return yaml.safe_dump(data, allow_unicode=True, sort_keys=False)


def compile_completion_app_dsl(
    *,
    message: str,
    app_name: str | None,
    dsl_version: str,
    settings: Settings,
    app_description: str | None = None,
    model_selections: list[DifyModelListItem] | None = None,
) -> str:
    data = _configured_app_dsl(
        message=message,
        app_name=app_name,
        app_description=app_description,
        dsl_version=dsl_version,
        settings=settings,
        app_mode="completion",
        model_config=_completion_model_config(message, settings, model_selections),
    )
    return yaml.safe_dump(data, allow_unicode=True, sort_keys=False)


def chat_app_plan_payload(
    *,
    message: str,
    app_name: str | None,
    settings: Settings,
    app_description: str | None = None,
    model_selections: list[DifyModelListItem] | None = None,
) -> dict[str, Any]:
    model = (model_selections or [None])[0]
    return {
        "name": app_name or _title_from_message(message),
        "description": app_description or _description_from_message(message),
        "app_mode": "chat",
        "model": {
            "provider": getattr(model, "provider", None) or settings.dify_default_model_provider,
            "name": getattr(model, "model", None) or settings.dify_default_model_name,
            "mode": "chat",
        },
        "pre_prompt": _chat_pre_prompt(message),
        "opening_statement": "",
        "suggested_questions": [],
    }


def completion_app_plan_payload(
    *,
    message: str,
    app_name: str | None,
    settings: Settings,
    app_description: str | None = None,
    model_selections: list[DifyModelListItem] | None = None,
) -> dict[str, Any]:
    model = (model_selections or [None])[0]
    return {
        "name": app_name or _title_from_message(message),
        "description": app_description or _description_from_message(message),
        "app_mode": "completion",
        "model": {
            "provider": getattr(model, "provider", None) or settings.dify_default_model_provider,
            "name": getattr(model, "model", None) or settings.dify_default_model_name,
            "mode": "chat",
        },
        "pre_prompt": _completion_pre_prompt(message),
    }


def agent_app_plan_payload(
    *,
    message: str,
    app_name: str | None,
    settings: Settings,
    app_description: str | None = None,
    model_selections: list[DifyModelListItem] | None = None,
    tool_selections: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    model = (model_selections or [None])[0]
    return {
        "name": app_name or _title_from_message(message),
        "description": app_description or _description_from_message(message),
        "app_mode": "agent-chat",
        "agent_mode": {
            "enabled": True,
            "strategy": "react",
        "tools": agent_tool_configs(tool_selections or []),
            "prompt": _agent_prompt(message),
        },
        "model": {
            "provider": getattr(model, "provider", None) or settings.dify_default_model_provider,
            "name": getattr(model, "model", None) or settings.dify_default_model_name,
            "mode": "chat",
        },
    }


def validate_chat_app_dsl(dsl: str, *, expected_dsl_version: str) -> list[dict[str, Any]]:
    return _validate_configured_app_dsl(
        dsl,
        expected_dsl_version=expected_dsl_version,
        app_mode="chat",
        label="Chatbot",
        require_agent_mode=False,
    )


def validate_agent_app_dsl(dsl: str, *, expected_dsl_version: str) -> list[dict[str, Any]]:
    return _validate_configured_app_dsl(
        dsl,
        expected_dsl_version=expected_dsl_version,
        app_mode="agent-chat",
        label="Agent",
        require_agent_mode=True,
    )


def validate_completion_app_dsl(dsl: str, *, expected_dsl_version: str) -> list[dict[str, Any]]:
    return _validate_configured_app_dsl(
        dsl,
        expected_dsl_version=expected_dsl_version,
        app_mode="completion",
        label="Completion",
        require_agent_mode=False,
    )


def _configured_app_dsl(
    *,
    message: str,
    app_name: str | None,
    dsl_version: str,
    settings: Settings,
    app_mode: str,
    model_config: dict[str, Any],
    app_description: str | None = None,
) -> dict[str, Any]:
    return {
        "version": dsl_version,
        "kind": "app",
        "app": {
            "name": app_name or _title_from_message(message),
            "mode": app_mode,
            "icon": "🤖",
            "icon_type": "emoji",
            "icon_background": "#FFEAD5",
            "description": app_description or _description_from_message(message),
            "use_icon_as_answer_icon": False,
        },
        "model_config": model_config,
        "dependencies": [],
    }


def _chat_model_config(
    message: str,
    settings: Settings,
    model_selections: list[DifyModelListItem] | None,
) -> dict[str, Any]:
    return _base_model_config(message, settings, model_selections, pre_prompt=_chat_pre_prompt(message))


def _completion_model_config(
    message: str,
    settings: Settings,
    model_selections: list[DifyModelListItem] | None,
) -> dict[str, Any]:
    return _base_model_config(message, settings, model_selections, pre_prompt=_completion_pre_prompt(message))


def _agent_model_config(
    message: str,
    settings: Settings,
    model_selections: list[DifyModelListItem] | None,
    tool_selections: list[dict[str, Any]],
) -> dict[str, Any]:
    model_config = _base_model_config(message, settings, model_selections, pre_prompt=_agent_pre_prompt(message))
    model_config["agent_mode"] = {
        "enabled": True,
        "strategy": "react",
        "tools": agent_tool_configs(tool_selections),
        "prompt": _agent_prompt(message),
    }
    return model_config


def _base_model_config(
    message: str,
    settings: Settings,
    model_selections: list[DifyModelListItem] | None,
    *,
    pre_prompt: str,
) -> dict[str, Any]:
    model = (model_selections or [None])[0]
    model_provider = getattr(model, "provider", None) or settings.dify_default_model_provider
    model_name = getattr(model, "model", None) or settings.dify_default_model_name
    return {
        "model": {
            "provider": model_provider,
            "name": model_name,
            "mode": "chat",
            "completion_params": {"temperature": 0.7},
        },
        "pre_prompt": pre_prompt,
        "prompt_type": "simple",
        "chat_prompt_config": {},
        "completion_prompt_config": {},
        "user_input_form": [],
        "dataset_query_variable": "",
        "dataset_configs": {"retrieval_model": "multiple", "datasets": {"datasets": []}},
        "agent_mode": {"enabled": False, "strategy": None, "tools": [], "prompt": None},
        "opening_statement": "",
        "suggested_questions": [],
        "suggested_questions_after_answer": {"enabled": False},
        "speech_to_text": {"enabled": False},
        "text_to_speech": {"enabled": False},
        "more_like_this": {"enabled": False},
        "sensitive_word_avoidance": {"enabled": False, "type": "", "config": {}},
        "retriever_resource": {"enabled": False},
        "external_data_tools": [],
        "file_upload": {
            "image": {
                "enabled": False,
                "number_limits": 3,
                "detail": "high",
                "transfer_methods": ["local_file", "remote_url"],
            }
        },
    }


def _validate_configured_app_dsl(
    dsl: str,
    *,
    expected_dsl_version: str,
    app_mode: str,
    label: str,
    require_agent_mode: bool,
) -> list[dict[str, Any]]:
    issues: list[dict[str, Any]] = []
    try:
        data = yaml.safe_load(dsl)
    except yaml.YAMLError as exc:
        return [{"code": "DSL_YAML_INVALID", "message": str(exc), "severity": "error"}]
    if not isinstance(data, dict):
        return [{"code": "DSL_INVALID", "message": "DSL root must be a mapping.", "severity": "error"}]
    if str(data.get("version")) != expected_dsl_version:
        issues.append(
            {
                "code": "DSL_VERSION_MISMATCH",
                "message": f"Expected DSL version {expected_dsl_version}, got {data.get('version')}.",
                "severity": "error",
            }
        )
    app = data.get("app") if isinstance(data.get("app"), dict) else {}
    if app.get("mode") != app_mode:
        issues.append(
            {
                "code": "DSL_APP_MODE_INVALID",
                "message": f"{label} app DSL must use app.mode {app_mode}.",
                "path": "app.mode",
                "severity": "error",
            }
        )
    model_config = data.get("model_config") if isinstance(data.get("model_config"), dict) else {}
    agent_mode = model_config.get("agent_mode") if isinstance(model_config.get("agent_mode"), dict) else {}
    if require_agent_mode and not agent_mode.get("enabled"):
        issues.append(
            {
                "code": "AGENT_MODE_DISABLED",
                "message": "Agent app DSL must enable model_config.agent_mode.",
                "path": "model_config.agent_mode.enabled",
                "severity": "error",
            }
        )
    model = model_config.get("model") if isinstance(model_config.get("model"), dict) else {}
    if not model.get("provider") or not model.get("name"):
        issues.append(
            {
                "code": "CONFIGURED_APP_MODEL_INVALID",
                "message": f"{label} app DSL requires model provider and name.",
                "path": "model_config.model",
                "severity": "error",
            }
        )
    return issues


def _chat_pre_prompt(message: str) -> str:
    return ensure_language_response_instruction(
        "You are a helpful conversational assistant. Follow the task below, ask "
        "clarifying questions when needed, and answer clearly.\n\nTask:\n"
        f"{message}"
    )


def _completion_pre_prompt(message: str) -> str:
    return ensure_language_response_instruction(
        "You are a text generation assistant. Use the user's input as source "
        "material and follow the generation task below.\n\nTask:\n"
        f"{message}"
    )


def _agent_pre_prompt(message: str) -> str:
    return ensure_language_response_instruction(
        "You are an autonomous assistant. Understand the user's request, plan the "
        "steps needed, use configured tools when helpful, and return a concise, "
        f"actionable answer.\n\nTask:\n{message}"
    )


def _agent_prompt(message: str) -> str:
    return ensure_language_response_instruction(
        "Reason step by step internally. When a tool is available and relevant, "
        "use it before finalizing. Do not invent facts, IDs, prices, policies, or "
        f"tool results.\n\nPrimary task:\n{message}"
    )


def agent_tool_configs(tool_selections: list[dict[str, Any]]) -> list[dict[str, Any]]:
    tools: list[dict[str, Any]] = []
    for item in tool_selections:
        if not isinstance(item, dict):
            continue
        provider_type = str(item.get("provider_type") or "").strip()
        provider_id = str(item.get("provider_id") or "").strip()
        tool_name = str(item.get("tool_name") or "").strip()
        if not provider_type or not provider_id or not tool_name:
            continue
        tool: dict[str, Any] = {
            "enabled": True,
            "provider_type": provider_type,
            "provider_id": provider_id,
            "tool_name": tool_name,
            "tool_parameters": item.get("tool_parameters")
            if isinstance(item.get("tool_parameters"), dict)
            else {},
        }
        plugin_unique_identifier = item.get("plugin_unique_identifier")
        if plugin_unique_identifier:
            tool["plugin_unique_identifier"] = plugin_unique_identifier
        tools.append(tool)
    return tools


def _title_from_message(message: str) -> str:
    title = " ".join(message.strip().split())
    if not title:
        return "Generated Agent"
    return title[:40]


def _description_from_message(message: str) -> str:
    text = " ".join(message.strip().split())
    if not text:
        return "Created from the user's natural-language request."
    return f"根据用户需求创建：{text[:90]}"
