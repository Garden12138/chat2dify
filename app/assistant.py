from __future__ import annotations

import json
import re
from typing import Any, Literal

from pydantic import BaseModel, Field

from app.models import AppMode


AssistantStatus = Literal["needs_input", "pending_action"]
AssistantOperation = Literal[
    "workflow.create",
    "workflow.modify.draft",
    "workflow.modify.apply",
    "workflow.run.draft",
    "chatflow.run.draft",
    "chatbot.run.draft",
    "completion.run.draft",
    "agent.run.draft",
]


class AssistantContext(BaseModel):
    app_mode: AppMode | None = None
    app_id: str | None = None
    app_name: str | None = None
    app_description: str | None = None
    active_app: dict[str, Any] | None = None
    recent_apps: dict[str, dict[str, Any]] = Field(default_factory=dict)
    create_message: str | None = None
    modify_message: str | None = None
    expected_hash: str | None = None
    allow_destructive: bool = False
    run_query: str | None = None
    run_inputs: dict[str, Any] | None = None
    files: list[dict[str, Any]] | None = None
    conversation_id: str | None = None
    parent_message_id: str | None = None
    timeout_seconds: float | None = Field(default=None, gt=0)
    dataset_ids: list[str] | None = None
    tool_selections: list[dict[str, Any]] | None = None
    agent_selections: list[dict[str, Any]] | None = None
    model_selections: list[dict[str, str]] | None = None
    trigger_selection: dict[str, Any] | None = None
    planner: dict[str, Any] | None = None
    modify_preview: dict[str, Any] | None = None


class AssistantPlanRequest(BaseModel):
    message: str = Field(min_length=1)
    context: AssistantContext = Field(default_factory=AssistantContext)


class AssistantAction(BaseModel):
    operation: AssistantOperation
    app_mode: AppMode | None = None
    panel: Literal["create", "modify", "run"]
    kind: str
    summary: str
    payload: dict[str, Any]


class AssistantPlanResponse(BaseModel):
    status: AssistantStatus
    intent: str
    message: str
    missing_fields: list[str] = Field(default_factory=list)
    action: AssistantAction | None = None


class AssistantExecuteRequest(BaseModel):
    action: AssistantAction


APP_MODE_LABELS = {
    "workflow": "Workflow",
    "advanced-chat": "Chatflow",
    "chat": "聊天助手",
    "agent-chat": "Agent",
    "completion": "文本生成应用",
}

OPERATION_TASK_META = {
    "workflow.create": ("create", "create"),
    "workflow.modify.draft": ("modify", "modify-preview"),
    "workflow.modify.apply": ("modify", "modify-apply"),
    "workflow.run.draft": ("run", "run"),
    "chatflow.run.draft": ("run", "chatflow-run"),
    "chatbot.run.draft": ("run", "chatbot-run"),
    "completion.run.draft": ("run", "completion-run"),
    "agent.run.draft": ("run", "agent-run"),
}


def plan_assistant_action(request: AssistantPlanRequest) -> AssistantPlanResponse:
    message = request.message.strip()
    context = request.context
    operation_intent = _detect_operation(message)
    explicit_app_mode = _detect_app_mode(message)

    if operation_intent == "create":
        return _create_plan(message, context, explicit_app_mode)
    if operation_intent == "modify.apply":
        return _modify_apply_plan(message, context, explicit_app_mode)
    if operation_intent == "modify.preview":
        return _modify_preview_plan(message, context, explicit_app_mode)
    if operation_intent == "run":
        return _run_plan(message, context, explicit_app_mode)

    return AssistantPlanResponse(
        status="needs_input",
        intent="unknown",
        message="Tell me whether to create, modify, or run an app.",
        missing_fields=["operation"],
    )


def assistant_task_meta(operation: str) -> tuple[str, str]:
    try:
        return OPERATION_TASK_META[operation]
    except KeyError as exc:
        raise ValueError(f"Unsupported assistant operation: {operation}") from exc


def _create_plan(
    message: str,
    context: AssistantContext,
    app_mode: str | None,
) -> AssistantPlanResponse:
    missing = []
    if not app_mode:
        missing.append("app_mode")
    create_message = _first_text(message, context.create_message)
    if not create_message:
        missing.append("message")
    app_name = _extract_app_name(message) or context.app_name or _infer_app_name(message, app_mode)
    app_description = (
        _extract_app_description(message)
        or context.app_description
        or _infer_app_description(message, app_mode, app_name)
    )
    if app_mode and not app_name:
        missing.append("app_name")
    if app_mode and not app_description:
        missing.append("app_description")
    if missing:
        return _needs_input(
            "create",
            missing,
            "I need the app type, app name, and creation request before preparing a create action.",
        )

    payload = _shared_create_modify_payload(context)
    payload.update(
        {
            "message": create_message,
            "app_name": app_name,
            "app_description": app_description,
            "app_mode": app_mode,
            "trigger_selection": context.trigger_selection if app_mode == "workflow" else None,
        }
    )
    operation = "workflow.create"
    return _pending(
        intent="create",
        operation=operation,
        app_mode=app_mode,
        payload=payload,
        summary=f"Create {APP_MODE_LABELS.get(app_mode, app_mode)} after confirmation.",
    )


def _modify_preview_plan(
    message: str,
    context: AssistantContext,
    explicit_app_mode: str | None,
) -> AssistantPlanResponse:
    app_ref = _resolve_app_reference(message, context, explicit_app_mode)
    app_id = app_ref.get("app_id")
    app_mode = app_ref.get("app_mode")
    modify_message = _first_text(message, context.modify_message)
    missing = []
    if not app_id:
        missing.append("app_id")
    if not modify_message:
        missing.append("message")
    if missing:
        return _needs_input("modify.preview", missing, "I need an app ID and change request before preparing a modify preview.")

    payload = _shared_create_modify_payload(context)
    explicit_expected_hash = _extract_named_value(message, ["expected_hash", "hash", "哈希"])
    payload.update(
        {
            "app_id": app_id,
            "message": modify_message,
            "expected_hash": explicit_expected_hash,
            "allow_destructive": context.allow_destructive,
            "trigger_selection": context.trigger_selection if app_mode == "workflow" else None,
        }
    )
    return _pending(
        intent="modify.preview",
        operation="workflow.modify.draft",
        app_mode=app_mode,
        payload=payload,
        summary="Preview the requested modification after confirmation.",
    )


def _modify_apply_plan(
    message: str,
    context: AssistantContext,
    explicit_app_mode: str | None,
) -> AssistantPlanResponse:
    preview = context.modify_preview or {}
    app_ref = _resolve_app_reference(message, context, explicit_app_mode)
    app_id = preview.get("app_id") or app_ref.get("app_id")
    app_mode = preview.get("app_mode") or app_ref.get("app_mode")
    expected_hash = preview.get("base_hash") or preview.get("expected_hash") or app_ref.get("expected_hash") or context.expected_hash
    plan = preview.get("plan")
    configured_model_config = preview.get("configured_model_config") or preview.get("model_config")
    missing = []
    if not app_id:
        missing.append("app_id")
    if not plan and not configured_model_config:
        missing.append("modify_preview")
    if not expected_hash:
        missing.append("expected_hash")
    if missing:
        return _needs_input(
            "modify.apply",
            missing,
            "Run a modify preview first, then confirm applying that reviewed preview.",
        )

    payload = {
        "app_id": app_id,
        "message": preview.get("message") or context.modify_message or message,
        "expected_hash": expected_hash,
        "allow_destructive": bool(preview.get("allow_destructive", context.allow_destructive)),
        "dataset_ids": preview.get("dataset_ids") or context.dataset_ids,
        "tool_selections": preview.get("tool_selections") or context.tool_selections,
        "agent_selections": preview.get("agent_selections") or context.agent_selections,
        "model_selections": preview.get("model_selections") or context.model_selections,
        "trigger_selection": preview.get("trigger_selection"),
        "planner": preview.get("planner") or context.planner,
    }
    if plan:
        payload["plan"] = plan
    if configured_model_config:
        payload["configured_model_config"] = configured_model_config
        payload["configured_model_config_changes"] = preview.get("configured_model_config_changes") or preview.get("changes")
    return _pending(
        intent="modify.apply",
        operation="workflow.modify.apply",
        app_mode=app_mode,
        payload=payload,
        summary="Apply the reviewed modification preview after confirmation.",
    )


def _run_plan(
    message: str,
    context: AssistantContext,
    explicit_app_mode: str | None,
) -> AssistantPlanResponse:
    app_ref = _resolve_app_reference(message, context, explicit_app_mode)
    app_id = app_ref.get("app_id")
    app_mode = app_ref.get("app_mode")
    if not app_mode:
        return _needs_input("run", ["app_mode"], "I need the app type before preparing a run action.")
    if not app_id:
        return _needs_input("run", ["app_id"], "I need the app ID before preparing a run action.")

    timeout_seconds = context.timeout_seconds or 120
    extracted_inputs = _extract_json_object(message)
    natural_query = _extract_run_query(message)
    if app_mode == "workflow":
        if extracted_inputs is not None:
            inputs = extracted_inputs
        elif natural_query:
            inputs = {"query": natural_query}
        elif context.run_inputs is not None:
            inputs = context.run_inputs
        elif context.run_query:
            inputs = {"query": context.run_query}
        else:
            inputs = None
        if inputs is None:
            return _needs_input("run", ["inputs"], "Workflow runs need test input text or an inputs JSON object.")
        payload = {
            "app_id": app_id,
            "inputs": inputs,
            "files": context.files,
            "timeout_seconds": timeout_seconds,
        }
        return _pending(
            intent="run",
            operation="workflow.run.draft",
            app_mode=app_mode,
            payload=payload,
            summary="Run the Workflow draft after confirmation.",
        )

    query = natural_query or context.run_query
    if not query:
        return _needs_input("run", ["query"], f"{APP_MODE_LABELS.get(app_mode, app_mode)} runs need a query.")
    payload = {
        "app_id": app_id,
        "query": query,
        "inputs": extracted_inputs if app_mode == "completion" and extracted_inputs is not None else context.run_inputs or {},
        "files": context.files,
        "timeout_seconds": timeout_seconds,
    }
    if app_mode != "completion":
        payload["conversation_id"] = context.conversation_id
        payload["parent_message_id"] = context.parent_message_id
    operation = {
        "advanced-chat": "chatflow.run.draft",
        "chat": "chatbot.run.draft",
        "completion": "completion.run.draft",
        "agent-chat": "agent.run.draft",
    }[app_mode]
    return _pending(
        intent="run",
        operation=operation,
        app_mode=app_mode,
        payload=payload,
        summary=f"Run the {APP_MODE_LABELS.get(app_mode, app_mode)} after confirmation.",
    )


def _pending(
    *,
    intent: str,
    operation: AssistantOperation,
    app_mode: str | None,
    payload: dict[str, Any],
    summary: str,
) -> AssistantPlanResponse:
    panel, kind = assistant_task_meta(operation)
    return AssistantPlanResponse(
        status="pending_action",
        intent=intent,
        message=summary,
        action=AssistantAction(
            operation=operation,
            app_mode=app_mode,
            panel=panel,
            kind=kind,
            summary=summary,
            payload=_drop_none(payload),
        ),
    )


def _needs_input(intent: str, missing: list[str], message: str) -> AssistantPlanResponse:
    return AssistantPlanResponse(
        status="needs_input",
        intent=intent,
        message=message,
        missing_fields=missing,
    )


def _shared_create_modify_payload(context: AssistantContext) -> dict[str, Any]:
    return {
        "dataset_ids": context.dataset_ids,
        "tool_selections": context.tool_selections,
        "agent_selections": context.agent_selections,
        "model_selections": context.model_selections,
        "planner": context.planner,
    }


def _resolve_app_reference(
    message: str,
    context: AssistantContext,
    explicit_app_mode: str | None,
) -> dict[str, Any]:
    explicit_app_id = _extract_app_id(message)
    active = _normalize_app_reference(context.active_app)
    legacy = _normalize_app_reference(
        {
            "app_id": context.app_id,
            "app_mode": context.app_mode,
            "app_name": context.app_name,
            "expected_hash": context.expected_hash,
            "conversation_id": context.conversation_id,
            "parent_message_id": context.parent_message_id,
        }
    )
    recent_by_mode = {
        mode: _normalize_app_reference(reference)
        for mode, reference in (context.recent_apps or {}).items()
    }
    matching_recent = _recent_reference_for_app_id(recent_by_mode, explicit_app_id)

    if explicit_app_id:
        same_active = active if active.get("app_id") == explicit_app_id else {}
        same_legacy = legacy if legacy.get("app_id") == explicit_app_id else {}
        return _drop_none(
            {
                "app_id": explicit_app_id,
                "app_mode": explicit_app_mode
                or matching_recent.get("app_mode")
                or same_active.get("app_mode")
                or same_legacy.get("app_mode")
                or active.get("app_mode")
                or legacy.get("app_mode"),
                "app_name": matching_recent.get("app_name")
                or same_active.get("app_name")
                or same_legacy.get("app_name"),
                "expected_hash": matching_recent.get("expected_hash")
                or same_active.get("expected_hash")
                or same_legacy.get("expected_hash")
                or context.expected_hash,
            }
        )

    if explicit_app_mode:
        by_mode = recent_by_mode.get(explicit_app_mode) or {}
        same_active = active if active.get("app_mode") == explicit_app_mode else {}
        same_legacy = legacy if legacy.get("app_mode") == explicit_app_mode else {}
        return _drop_none(
            {
                "app_id": by_mode.get("app_id")
                or same_active.get("app_id")
                or same_legacy.get("app_id"),
                "app_mode": explicit_app_mode,
                "app_name": by_mode.get("app_name")
                or same_active.get("app_name")
                or same_legacy.get("app_name"),
                "expected_hash": by_mode.get("expected_hash")
                or same_active.get("expected_hash")
                or same_legacy.get("expected_hash")
                or context.expected_hash,
            }
        )

    if active.get("app_id") or active.get("app_mode"):
        return active
    return legacy


def _normalize_app_reference(reference: dict[str, Any] | None) -> dict[str, Any]:
    if not isinstance(reference, dict):
        return {}
    app_mode = reference.get("app_mode") or reference.get("mode")
    if app_mode not in APP_MODE_LABELS:
        app_mode = None
    return _drop_none(
        {
            "app_id": reference.get("app_id") or reference.get("id"),
            "app_mode": app_mode,
            "app_name": reference.get("app_name") or reference.get("name"),
            "expected_hash": reference.get("expected_hash")
            or reference.get("hash")
            or reference.get("new_hash")
            or reference.get("base_hash"),
            "conversation_id": reference.get("conversation_id"),
            "parent_message_id": reference.get("parent_message_id") or reference.get("message_id"),
        }
    )


def _recent_reference_for_app_id(
    recent_by_mode: dict[str, dict[str, Any]],
    app_id: str | None,
) -> dict[str, Any]:
    if not app_id:
        return {}
    for reference in recent_by_mode.values():
        if reference.get("app_id") == app_id:
            return reference
    return {}


def _detect_operation(message: str) -> str:
    text = message.lower()
    if _contains_any(text, ["apply", "confirm modification", "确认修改", "应用修改", "写回", "提交修改"]):
        return "modify.apply"
    if _contains_any(text, ["run", "test", "try", "execute", "测试", "运行", "试跑", "执行"]):
        return "run"
    if _contains_any(text, ["modify", "change", "update", "revise", "调整", "修改", "改", "变更", "优化"]):
        return "modify.preview"
    if _contains_any(text, ["create", "build", "new", "generate", "创建", "生成", "新增", "做一个", "搭建"]):
        return "create"
    return "unknown"


def _detect_app_mode(message: str) -> AppMode | None:
    text = message.lower()
    if _contains_any(text, ["agent-chat", "agent app", " agent", "智能体", "agent"]):
        return "agent-chat"
    if _contains_any(text, ["advanced-chat", "chatflow", "对话流"]):
        return "advanced-chat"
    if _contains_any(text, ["completion", "text generation", "text-generation", "文本生成"]):
        return "completion"
    if _contains_any(text, ["chatbot", "chat app", "聊天助手", "聊天机器人"]):
        return "chat"
    if _contains_any(text, ["workflow", "工作流"]):
        return "workflow"
    return None


def _contains_any(text: str, needles: list[str]) -> bool:
    return any(needle in text for needle in needles)


def _first_text(*values: str | None) -> str:
    for value in values:
        text = str(value or "").strip()
        if text:
            return text
    return ""


def _extract_app_id(message: str) -> str | None:
    named = _extract_named_value(message, ["app_id", "app id", "应用id", "应用 id"])
    if named:
        return named
    url_match = re.search(r"/app/([^/?#\s]+)", message, flags=re.IGNORECASE)
    if url_match:
        return url_match.group(1).strip()
    uuid_match = re.search(
        r"\b[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}\b",
        message,
    )
    if uuid_match:
        return uuid_match.group(0)
    prefixed_match = re.search(
        r"\b((?:app|workflow|chat|chatflow|agent|completion)-[A-Za-z0-9_.:-]{3,})\b",
        message,
        flags=re.IGNORECASE,
    )
    if prefixed_match:
        return prefixed_match.group(1)
    loose = message.strip()
    if re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.:-]{5,}", loose):
        return loose
    return None


def _extract_named_value(message: str, names: list[str]) -> str | None:
    alternatives = "|".join(re.escape(name) for name in names)
    match = re.search(
        rf"(?:{alternatives})\s*[:：=]?\s*([A-Za-z0-9][A-Za-z0-9_.:-]{{2,}})",
        message,
        flags=re.IGNORECASE,
    )
    return match.group(1).strip() if match else None


def _extract_app_name(message: str) -> str | None:
    patterns = [
        r"(?:名字叫|名称为|名称叫|命名为)\s*([^，。,.；;\n]{2,40})",
        r"(?:named|called|name)\s+([A-Za-z0-9 _-]{2,40})",
    ]
    for pattern in patterns:
        match = re.search(pattern, message, flags=re.IGNORECASE)
        if match:
            return match.group(1).strip()
    return None


def _extract_app_description(message: str) -> str | None:
    patterns = [
        r"(?:描述为|描述是|说明为|简介为)\s*([^。\n]{6,120})",
        r"(?:description|desc)\s*[:：=]\s*([^\n]{6,120})",
    ]
    for pattern in patterns:
        match = re.search(pattern, message, flags=re.IGNORECASE)
        if match:
            return _clean_sentence(match.group(1), limit=120)
    return None


def _infer_app_name(message: str, app_mode: str | None) -> str | None:
    subject = _semantic_create_subject(message)
    if not subject:
        return None
    suffix = {
        "workflow": "工作流",
        "advanced-chat": "Chatflow",
        "chat": "聊天助手",
        "agent-chat": "Agent",
        "completion": "文本生成",
    }.get(app_mode or "", "应用")
    if suffix.lower() not in subject.lower():
        subject = f"{subject}{suffix}"
    return subject[:40].strip()


def _infer_app_description(message: str, app_mode: str | None, app_name: str | None) -> str | None:
    subject = _semantic_create_subject(message)
    if not subject and not app_name:
        return None
    label = APP_MODE_LABELS.get(app_mode or "", "应用")
    purpose = subject or app_name or "用户需求"
    return _clean_sentence(f"{label}，用于{purpose}。", limit=120)


def _semantic_create_subject(message: str) -> str:
    text = re.sub(r"\{.*\}", "", message, flags=re.DOTALL)
    text = re.sub(r"```.*?```", "", text, flags=re.DOTALL)
    text = re.sub(r"(?:名字叫|名称为|名称叫|命名为)\s*[^，。,.；;\n]{2,40}", "", text)
    text = re.sub(r"(?i)(?:named|called|name)\s+[A-Za-z0-9 _-]{2,40}", "", text)
    replacements = [
        "创建",
        "生成",
        "新增",
        "做一个",
        "搭建",
        "帮我",
        "请",
        "一个",
        "一款",
        "这个",
        "应用",
        "工作流",
        "聊天助手",
        "聊天机器人",
        "文本生成应用",
        "文本生成",
        "智能体",
        "对话流",
        "处理",
        "用于",
        "用来",
        "create",
        "build",
        "new",
        "generate",
        "workflow",
        "chatflow",
        "chatbot",
        "completion",
        "text generation",
        "text-generation",
        "agent",
        "app",
        "for",
        "to",
    ]
    for phrase in replacements:
        text = re.sub(re.escape(phrase), " ", text, flags=re.IGNORECASE)
    text = _clean_sentence(text, limit=32)
    return text or ""


def _clean_sentence(text: str, *, limit: int) -> str:
    cleaned = " ".join(str(text or "").split())
    cleaned = cleaned.strip(" ：:，。,.；;、-—_\"'“”‘’")
    cleaned = re.sub(r"\s+", " ", cleaned)
    return cleaned[:limit].strip(" ：:，。,.；;、-—_\"'“”‘’")


def _extract_json_object(message: str) -> dict[str, Any] | None:
    candidates: list[str] = []
    fenced = re.findall(r"```(?:json)?\s*(\{.*?\})\s*```", message, flags=re.DOTALL | re.IGNORECASE)
    candidates.extend(fenced)
    start = message.find("{")
    end = message.rfind("}")
    if start >= 0 and end > start:
        candidates.append(message[start : end + 1])
    for candidate in candidates:
        try:
            value = json.loads(candidate)
        except json.JSONDecodeError:
            continue
        if isinstance(value, dict):
            return value
    return None


def _extract_run_query(message: str) -> str | None:
    labelled = re.findall(
        r"(?i)(?:query|input|message|question|问题|内容|测试内容|测试输入|输入)\s*[:：]\s*(.+)",
        message,
    )
    if labelled:
        return _clean_run_query(labelled[-1])
    colon_parts = re.findall(r"[:：]\s*(.+)", message)
    if colon_parts:
        return _clean_run_query(colon_parts[-1])

    text = re.sub(r"\{.*\}", "", message, flags=re.DOTALL)
    text = re.sub(r"https?://\S+", "", text, flags=re.IGNORECASE)
    text = re.sub(r"(?i)(app[_ -]?id|应用\s*id)\s*[:：=]?\s*[A-Za-z0-9_.:-]+", "", text)
    text = re.sub(
        r"\b[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}\b",
        "",
        text,
    )
    text = re.sub(
        r"\b(?:app|workflow|chat|chatflow|agent|completion)-[A-Za-z0-9_.:-]{3,}\b",
        "",
        text,
        flags=re.IGNORECASE,
    )
    for phrase in [
        "运行",
        "运行下",
        "测试",
        "测试运行",
        "试跑",
        "执行",
        "run",
        "test",
        "try",
        "execute",
        "workflow",
        "工作流",
        "chatflow",
        "聊天助手",
        "聊天机器人",
        "文本生成应用",
        "文本生成",
        "agent-chat",
        "agent",
        "智能体",
        "刚才那个",
        "刚才的",
        "当前",
        "这个应用",
        "这个",
        "应用",
    ]:
        text = re.sub(re.escape(phrase), "", text, flags=re.IGNORECASE)
    return _clean_run_query(text)


def _clean_run_query(text: str) -> str | None:
    cleaned = " ".join(str(text or "").split()).strip(" ，。,.；;")
    cleaned = cleaned.strip(" ：:，。,.；;、-—_\"'“”‘’")
    cleaned = re.sub(r"^(请|请你|帮我|用|拿|把|一下|下|试一下|跑一下|测一下)\s*", "", cleaned)
    cleaned = cleaned.strip(" ：:，。,.；;、-—_\"'“”‘’")
    if cleaned in {"", "下", "一下", "试一下", "跑一下", "测一下", "看看"}:
        return None
    return cleaned


def _drop_none(payload: dict[str, Any]) -> dict[str, Any]:
    return {key: value for key, value in payload.items() if value is not None}
