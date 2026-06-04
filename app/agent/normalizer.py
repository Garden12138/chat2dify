from __future__ import annotations

import re
from copy import deepcopy
from dataclasses import dataclass, field
from typing import Any
from uuid import NAMESPACE_URL, UUID, uuid5

from app.input_variables import file_upload_settings, is_file_input_type
from app.list_operator import normalize_list_comparison_operator, normalize_list_variable_selector


SOURCE_HANDLE = "source"
FALSE_HANDLE = "false"
HUMAN_INPUT_DEFAULT_WEBAPP_DELIVERY_ID = "00000000-0000-4000-8000-000000000001"
DIFY_REF_PATTERN = re.compile(r"\{\{\s*#([A-Za-z0-9_-]+)\.([A-Za-z0-9_.-]+)#\s*\}\}")
GENERIC_TITLES = {
    "",
    "node",
    "start",
    "begin",
    "input",
    "llm",
    "model",
    "code",
    "end",
    "output",
    "ifelse",
    "if",
    "condition",
    "branch",
    "httprequest",
    "http",
    "template",
    "templatetransform",
    "questionclassifier",
    "classifier",
    "parameterextractor",
    "extractor",
    "variableaggregator",
    "aggregator",
    "variableassigner",
    "assigner",
    "documentextractor",
    "docextractor",
    "listoperator",
    "listfilter",
    "knowledgeretrieval",
    "knowledge",
    "retrieval",
    "rag",
    "humaninput",
    "human",
    "manualinput",
    "approval",
    "iteration",
    "iterate",
    "iterationstart",
    "loop",
    "repeat",
    "while",
    "loopstart",
    "loopend",
    "tool",
    "agent",
    "datasource",
    "datasourceempty",
    "knowledgeindex",
    "triggerwebhook",
    "triggerplugin",
    "triggerschedule",
    "开始",
    "开始节点",
    "输入",
    "大模型",
    "模型",
    "代码",
    "结束",
    "结束节点",
    "输出",
    "判断",
    "条件",
    "分支",
    "接口",
    "模板",
    "问题分类",
    "分类器",
    "参数提取",
    "提取器",
    "变量聚合",
    "变量赋值",
    "文档提取",
    "列表处理",
    "列表过滤",
    "知识库检索",
    "知识检索",
    "检索",
    "人工介入",
    "人工输入",
    "人工审核",
    "人工审批",
    "循环",
    "遍历",
    "批量处理",
    "循环开始",
    "循环结束",
    "工具",
    "智能体",
    "数据源",
    "知识库写入",
    "触发器",
    "webhook触发器",
}

NODE_TYPE_ALIASES = {
    "classifier": "question-classifier",
    "question_classifier": "question-classifier",
    "questionclassifier": "question-classifier",
    "intent-classifier": "question-classifier",
    "intent_classifier": "question-classifier",
    "parameter_extractor": "parameter-extractor",
    "parameterextractor": "parameter-extractor",
    "extractor": "parameter-extractor",
    "param-extractor": "parameter-extractor",
    "param_extractor": "parameter-extractor",
    "variable_aggregator": "variable-aggregator",
    "variableaggregator": "variable-aggregator",
    "aggregator": "variable-aggregator",
    "variable-assigner": "assigner",
    "variable_assigner": "assigner",
    "variableassigner": "assigner",
    "var-assigner": "assigner",
    "var_assigner": "assigner",
    "assigner": "assigner",
    "doc-extractor": "document-extractor",
    "doc_extractor": "document-extractor",
    "docextractor": "document-extractor",
    "document_extractor": "document-extractor",
    "documentextractor": "document-extractor",
    "list-filter": "list-operator",
    "list_filter": "list-operator",
    "listfilter": "list-operator",
    "list_operator": "list-operator",
    "listoperator": "list-operator",
    "knowledge-retrieval": "knowledge-retrieval",
    "knowledge_retrieval": "knowledge-retrieval",
    "knowledgeretrieval": "knowledge-retrieval",
    "knowledge": "knowledge-retrieval",
    "retrieval": "knowledge-retrieval",
    "rag": "knowledge-retrieval",
    "human-input": "human-input",
    "human_input": "human-input",
    "humaninput": "human-input",
    "human": "human-input",
    "manual-input": "human-input",
    "manual_input": "human-input",
    "manualinput": "human-input",
    "approval": "human-input",
    "review": "human-input",
    "iterate": "iteration",
    "iterator": "iteration",
    "list-loop": "iteration",
    "list_loop": "iteration",
    "listloop": "iteration",
    "batch": "iteration",
    "batch-loop": "iteration",
    "batch_loop": "iteration",
    "batchloop": "iteration",
    "for-each": "iteration",
    "for_each": "iteration",
    "foreach": "iteration",
    "iteration_start": "iteration-start",
    "iterationstart": "iteration-start",
    "repeat": "loop",
    "while": "loop",
    "retry-loop": "loop",
    "retry_loop": "loop",
    "retryloop": "loop",
    "loop_start": "loop-start",
    "loopstart": "loop-start",
    "loop_end": "loop-end",
    "loopend": "loop-end",
    "tool": "tool",
    "agent": "agent",
    "data-source": "datasource",
    "data_source": "datasource",
    "datasource": "datasource",
    "data-source-empty": "datasource-empty",
    "data_source_empty": "datasource-empty",
    "datasource-empty": "datasource-empty",
    "datasource_empty": "datasource-empty",
    "datasourceempty": "datasource-empty",
    "knowledge-index": "knowledge-index",
    "knowledge_index": "knowledge-index",
    "knowledgeindex": "knowledge-index",
    "knowledge-base": "knowledge-index",
    "knowledge_base": "knowledge-index",
    "knowledgebase": "knowledge-index",
    "trigger-webhook": "trigger-webhook",
    "trigger_webhook": "trigger-webhook",
    "triggerwebhook": "trigger-webhook",
    "webhook-trigger": "trigger-webhook",
    "webhook_trigger": "trigger-webhook",
    "webhooktrigger": "trigger-webhook",
    "trigger-plugin": "trigger-plugin",
    "trigger_plugin": "trigger-plugin",
    "triggerplugin": "trigger-plugin",
    "plugin-trigger": "trigger-plugin",
    "plugin_trigger": "trigger-plugin",
    "plugintrigger": "trigger-plugin",
    "trigger-schedule": "trigger-schedule",
    "trigger_schedule": "trigger-schedule",
    "triggerschedule": "trigger-schedule",
    "schedule-trigger": "trigger-schedule",
    "schedule_trigger": "trigger-schedule",
    "scheduletrigger": "trigger-schedule",
}

EXTERNAL_DEPENDENCY_NODE_TYPES = {
    "tool",
    "agent",
    "datasource",
    "datasource-empty",
    "knowledge-index",
    "trigger-webhook",
    "trigger-plugin",
    "trigger-schedule",
}


@dataclass(frozen=True)
class NormalizationResult:
    payload: dict[str, Any]
    changed: bool
    changes: list[str] = field(default_factory=list)


def normalize_plan_payload(
    payload: dict[str, Any],
    *,
    app_name: str | None = None,
    default_dataset_ids: list[str] | None = None,
    tool_selections: list[dict[str, Any]] | None = None,
) -> NormalizationResult:
    data = deepcopy(payload)
    changes: list[str] = []
    if not isinstance(data, dict):
        raise ValueError("plan payload must be a JSON object")

    if app_name and data.get("name") != app_name:
        data["name"] = app_name
        changes.append("set workflow name from request app_name")
    data.setdefault("name", "Generated Workflow")
    data.setdefault("description", "Generated by chat2dify.")

    nodes = data.get("nodes")
    edges = data.get("edges")
    if not isinstance(nodes, list):
        raise ValueError("plan.nodes must be a list")
    if not isinstance(edges, list):
        raise ValueError("plan.edges must be a list")

    for node in nodes:
        if not isinstance(node, dict):
            raise ValueError("plan.nodes items must be objects")
        old_type = str(node.get("type", ""))
        node_type = _normalize_node_type(old_type)
        if old_type != node_type:
            node["type"] = node_type
            changes.append(f"normalized node type for {node.get('id', '<unknown>')}: {old_type} -> {node_type}")
        node.setdefault("title", _default_title(str(node.get("type", ""))))
        node.setdefault("desc", "")
        params = node.get("params")
        if not isinstance(params, dict):
            params = {}
            node["params"] = params

        node_type = node.get("type")
        before = deepcopy(params)
        match node_type:
            case "start":
                node["params"] = _normalize_start_params(params)
            case "llm":
                node["params"] = _normalize_llm_params(params, workflow_name=str(data.get("name", "")))
            case "code":
                node["params"] = _normalize_code_params(params)
            case "if-else":
                node["params"] = _normalize_if_else_params(params)
            case "end":
                node["params"] = _normalize_end_params(params)
            case "http-request":
                node["params"] = _normalize_http_params(params)
            case "template-transform":
                node["params"] = _normalize_template_params(params)
            case "question-classifier":
                node["params"] = _normalize_question_classifier_params(params)
            case "parameter-extractor":
                node["params"] = _normalize_parameter_extractor_params(params)
            case "variable-aggregator":
                node["params"] = _normalize_variable_aggregator_params(params)
            case "document-extractor":
                node["params"] = _normalize_document_extractor_params(params)
            case "assigner":
                node["params"] = _normalize_assigner_params(params)
            case "list-operator":
                list_params = _normalize_list_operator_params(params)
                if _list_operator_needs_code_fallback(list_params):
                    node["type"] = "code"
                    node["params"] = _normalize_code_params(_object_list_operator_code_params(list_params))
                else:
                    node["params"] = list_params
            case "knowledge-retrieval":
                node["params"] = _normalize_knowledge_retrieval_params(params, default_dataset_ids or [])
            case "human-input":
                node["params"] = _normalize_human_input_params(params)
            case "iteration":
                node["params"] = _normalize_iteration_params(
                    params,
                    node_id=str(node.get("id") or "iteration"),
                    workflow_name=str(data.get("name", "")),
                    default_dataset_ids=default_dataset_ids or [],
                )
            case "loop":
                node["params"] = _normalize_loop_params(
                    params,
                    node_id=str(node.get("id") or "loop"),
                    workflow_name=str(data.get("name", "")),
                    default_dataset_ids=default_dataset_ids or [],
                )
            case "tool":
                node["params"] = _normalize_tool_params(params, tool_selections or [])
            case node_type if node_type in EXTERNAL_DEPENDENCY_NODE_TYPES:
                node["params"] = _normalize_external_dependency_params(params)
            case "iteration-start" | "loop-start" | "loop-end":
                node["params"] = dict(params)
        if before != node.get("params"):
            changes.append(f"normalized {node.get('id', '<unknown>')} params")

    node_by_id = {str(node.get("id")): node for node in nodes if isinstance(node, dict)}
    for node in nodes:
        if not isinstance(node, dict):
            continue
        old_title = str(node.get("title") or "")
        node_type = str(node.get("type") or "")
        node_params = node.get("params") if isinstance(node.get("params"), dict) else {}
        if node_type in EXTERNAL_DEPENDENCY_NODE_TYPES and not (node_type == "tool" and "_raw_data" not in node_params):
            continue
        if _is_generic_title(old_title, node_type):
            node["title"] = _semantic_title(node, data, node_by_id, edges)
            changes.append(f"normalized generic title for {node.get('id', '<unknown>')}")

    edge_positions = _branch_edge_positions(edges, node_by_id, "if-else")
    classifier_edge_positions = _branch_edge_positions(edges, node_by_id, "question-classifier")
    human_input_edge_positions = _branch_edge_positions(edges, node_by_id, "human-input")
    for edge in edges:
        if not isinstance(edge, dict):
            raise ValueError("plan.edges items must be objects")
        edge.setdefault("source_handle", SOURCE_HANDLE)
        edge.setdefault("target_handle", "target")
        source = node_by_id.get(str(edge.get("source")))
        if source and source.get("type") == "if-else":
            old_handle = edge.get("source_handle")
            new_handle = _infer_if_else_source_handle(source, edge, node_by_id, edge_positions)
            if old_handle != new_handle:
                edge["source_handle"] = new_handle
                changes.append(f"normalized if-else edge handle for {edge.get('source')} -> {edge.get('target')}")
        elif source and source.get("type") == "question-classifier":
            old_handle = edge.get("source_handle")
            new_handle = _infer_question_classifier_source_handle(
                source,
                edge,
                node_by_id,
                classifier_edge_positions,
            )
            if old_handle != new_handle:
                edge["source_handle"] = new_handle
                changes.append(
                    f"normalized question-classifier edge handle for {edge.get('source')} -> {edge.get('target')}"
                )
        elif source and source.get("type") == "human-input":
            old_handle = edge.get("source_handle")
            new_handle = _infer_human_input_source_handle(source, edge, node_by_id, human_input_edge_positions)
            if old_handle != new_handle:
                edge["source_handle"] = new_handle
                changes.append(f"normalized human-input edge handle for {edge.get('source')} -> {edge.get('target')}")

    return NormalizationResult(payload=data, changed=bool(changes), changes=changes)


def normalize_template_refs(text: str) -> str:
    return re.sub(
        r"\{\{\s*(?!#)([A-Za-z0-9_-]+)\.([A-Za-z0-9_.-]+)\s*\}\}",
        r"{{#\1.\2#}}",
        text,
    )


def _normalize_node_type(value: str) -> str:
    normalized = str(value or "").strip().replace(" ", "-")
    normalized = re.sub(r"-+", "-", normalized)
    alias_key = normalized.lower()
    return NODE_TYPE_ALIASES.get(alias_key, normalized)


def _normalize_start_params(params: dict[str, Any]) -> dict[str, Any]:
    raw_variables = params.get("variables") or params.get("inputs") or []
    variables = []
    for item in raw_variables:
        if not isinstance(item, dict):
            continue
        name = item.get("name") or item.get("variable")
        if not name:
            continue
        input_type = _input_type(str(item.get("type", "paragraph")))
        variable = {
            "name": str(name),
            "type": input_type,
            "required": bool(item.get("required", True)),
            "label": item.get("label") or str(name),
        }
        if item.get("max_length") is not None:
            variable["max_length"] = item.get("max_length")
        if isinstance(item.get("options"), list):
            variable["options"] = deepcopy(item.get("options"))
        if is_file_input_type(input_type):
            variable.update(file_upload_settings(item, input_type=input_type))
        variables.append(variable)
    if not variables:
        variables.append({"name": "query", "type": "paragraph", "required": True, "label": "Query"})
    return {"variables": variables}


def _normalize_llm_params(params: dict[str, Any], *, workflow_name: str) -> dict[str, Any]:
    result = dict(params)
    raw_prompt = str(params.get("prompt", "") or "")
    raw_system = str(params.get("system_prompt", "") or "")
    raw_user = str(params.get("user_prompt", "") or raw_prompt or "")
    split_system, split_user = _split_prompt_sections(raw_user or raw_prompt)

    if not raw_system.strip():
        raw_system = split_system or _default_system_prompt(workflow_name)
        raw_user = split_user or raw_user
    elif split_user and not params.get("user_prompt"):
        raw_user = split_user

    result.pop("prompt", None)
    result["system_prompt"] = normalize_template_refs(_clean_prompt(raw_system))
    result["user_prompt"] = normalize_template_refs(
        _clean_prompt(_ensure_user_prompt(raw_user, workflow_name=workflow_name))
    )
    result.setdefault("completion_params", {"temperature": 0.7})
    return result


def _normalize_code_params(params: dict[str, Any]) -> dict[str, Any]:
    result = dict(params)
    if "language" in result and "code_language" not in result:
        result["code_language"] = result.pop("language")
    result.setdefault("code", "def main(query: str) -> dict:\n    return {\"result\": query}\n")
    result.setdefault("code_language", "python3")
    result["variables"] = _normalize_variables(result.get("variables", []), result.get("inputs"))
    if not result.get("outputs"):
        result["outputs"] = _infer_code_outputs(str(result["code"]))
    return result


def _normalize_if_else_params(params: dict[str, Any]) -> dict[str, Any]:
    raw_cases = params.get("cases") or []
    cases = []
    for idx, raw_case in enumerate(raw_cases):
        if not isinstance(raw_case, dict):
            continue
        case_id = str(raw_case.get("case_id") or raw_case.get("id") or ("true" if idx == 0 else f"case_{idx + 1}"))
        raw_conditions = raw_case.get("conditions")
        if not raw_conditions and raw_case.get("condition"):
            raw_conditions = [_condition_from_text(str(raw_case["condition"]))]
        conditions = []
        for condition in raw_conditions or []:
            if not isinstance(condition, dict):
                continue
            condition_copy = dict(condition)
            if isinstance(condition_copy.get("variable_selector"), str):
                condition_copy["variable_selector"] = _selector_from_ref(condition_copy["variable_selector"])
            condition_copy.setdefault("comparison_operator", "not empty")
            condition_copy.setdefault("value", "")
            condition_copy.setdefault("varType", "string")
            conditions.append(condition_copy)
        cases.append(
            {
                "case_id": case_id,
                "logical_operator": raw_case.get("logical_operator", "and"),
                "conditions": conditions or [
                    {
                        "variable_selector": params.get("variable_selector", ["start", "query"]),
                        "comparison_operator": "not empty",
                        "value": "",
                        "varType": "string",
                    }
                ],
            }
        )
    if not cases:
        cases.append(
            {
                "case_id": "true",
                "logical_operator": "and",
                "conditions": [
                    {
                        "variable_selector": params.get("variable_selector", ["start", "query"]),
                        "comparison_operator": "not empty",
                        "value": "",
                        "varType": "string",
                    }
                ],
            }
        )
    return {"cases": cases, "else_case": str(params.get("else_case", FALSE_HANDLE))}


def _normalize_end_params(params: dict[str, Any]) -> dict[str, Any]:
    outputs = params.get("outputs") or [{"variable": "answer", "value_selector": ["llm", "text"]}]
    return {"outputs": [_normalize_output(item) for item in outputs if isinstance(item, dict)]}


def _normalize_http_params(params: dict[str, Any]) -> dict[str, Any]:
    result = dict(params)
    result["variables"] = _normalize_variables(result.get("variables", []))
    result["method"] = str(result.get("method", "GET")).upper()
    result["url"] = normalize_template_refs(str(result.get("url", "https://example.com")))
    result["headers"] = _normalize_key_value_text(result.get("headers", ""))
    result["params"] = _normalize_key_value_text(result.get("params", ""))
    body = result.get("body", {"type": "none", "data": []})
    if isinstance(body, dict) and isinstance(body.get("data"), str):
        body = {**body, "data": normalize_template_refs(body["data"])}
    elif body in (None, "", [], {}):
        body = {"type": "none", "data": []}
    result["body"] = body
    if result.get("timeout") is None:
        result.pop("timeout", None)
    if result.get("retry_config") is None:
        result.pop("retry_config", None)
    return result


def _normalize_template_params(params: dict[str, Any]) -> dict[str, Any]:
    template = normalize_template_refs(str(params.get("template", "{{ query }}")))
    variables = _normalize_variables(params.get("variables", []))
    return {
        **params,
        "template": template,
        "variables": _add_template_ref_variables(template, variables),
    }


def _normalize_question_classifier_params(params: dict[str, Any]) -> dict[str, Any]:
    result = dict(params)
    raw_selector = (
        result.get("query_variable_selector")
        or result.get("query")
        or result.get("variable_selector")
        or result.get("input_selector")
        or ["start", "query"]
    )
    result["query_variable_selector"] = _normalize_selector(raw_selector)
    result["classes"] = _normalize_classifier_classes(
        result.get("classes") or result.get("topics") or result.get("categories") or result.get("intents") or []
    )
    result["instruction"] = normalize_template_refs(str(result.get("instruction") or result.get("prompt") or ""))
    result.pop("query", None)
    result.pop("variable_selector", None)
    result.pop("input_selector", None)
    result.pop("topics", None)
    result.pop("categories", None)
    result.pop("intents", None)
    result.pop("prompt", None)
    result["vision"] = _normalize_vision(result.get("vision"))
    return result


def _normalize_parameter_extractor_params(params: dict[str, Any]) -> dict[str, Any]:
    result = dict(params)
    raw_selector = (
        result.get("query")
        or result.get("query_variable_selector")
        or result.get("variable_selector")
        or result.get("input_selector")
        or ["start", "query"]
    )
    result["query"] = _normalize_selector(raw_selector)
    result["parameters"] = _normalize_extractor_parameters(
        result.get("parameters") or result.get("fields") or result.get("extract_parameters") or []
    )
    result["instruction"] = normalize_template_refs(str(result.get("instruction") or result.get("prompt") or ""))
    result["reasoning_mode"] = str(result.get("reasoning_mode") or "prompt")
    if result["reasoning_mode"] not in {"prompt", "function_call"}:
        result["reasoning_mode"] = "prompt"
    result.pop("query_variable_selector", None)
    result.pop("variable_selector", None)
    result.pop("input_selector", None)
    result.pop("fields", None)
    result.pop("extract_parameters", None)
    result.pop("prompt", None)
    result["vision"] = _normalize_vision(result.get("vision"))
    return result


def _normalize_variable_aggregator_params(params: dict[str, Any]) -> dict[str, Any]:
    result = dict(params)
    variables = _normalize_selector_list(result.get("variables") or result.get("selectors") or result.get("inputs") or [])
    result["variables"] = variables
    result["output_type"] = _var_type(str(result.get("output_type") or result.get("var_type") or "string"))
    advanced = result.get("advanced_settings") if isinstance(result.get("advanced_settings"), dict) else {}
    groups = []
    for idx, group in enumerate(advanced.get("groups") or result.get("groups") or []):
        if not isinstance(group, dict):
            continue
        group_name = str(group.get("group_name") or group.get("name") or f"Group{idx + 1}")
        group_id = str(group.get("groupId") or group.get("group_id") or _safe_branch_id(group_name) or f"group_{idx + 1}")
        groups.append(
            {
                "group_name": group_name,
                "groupId": group_id,
                "output_type": _var_type(str(group.get("output_type") or result["output_type"])),
                "variables": _normalize_selector_list(group.get("variables") or []),
            }
        )
    group_enabled = bool(advanced.get("group_enabled", False) or groups)
    result["advanced_settings"] = {"group_enabled": group_enabled, "groups": groups if group_enabled else []}
    result.pop("selectors", None)
    result.pop("inputs", None)
    result.pop("groups", None)
    result.pop("var_type", None)
    return result


def _normalize_document_extractor_params(params: dict[str, Any]) -> dict[str, Any]:
    result = dict(params)
    raw_selector = (
        result.get("variable_selector")
        or result.get("file_selector")
        or result.get("input_selector")
        or result.get("variable")
        or result.get("file")
        or ["start", "files"]
    )
    result["variable_selector"] = _normalize_selector(raw_selector)
    result["is_array_file"] = bool(result.get("is_array_file", result.get("array", False)))
    result.pop("file_selector", None)
    result.pop("input_selector", None)
    result.pop("variable", None)
    result.pop("file", None)
    result.pop("array", None)
    return result


def _normalize_assigner_params(params: dict[str, Any]) -> dict[str, Any]:
    result = dict(params)
    items = result.get("items") or result.get("operations") or []
    normalized_items = []
    for item in items:
        if not isinstance(item, dict):
            continue
        variable_selector = _normalize_selector(
            item.get("variable_selector")
            or item.get("assigned_variable_selector")
            or item.get("target")
            or item.get("variable")
            or []
        )
        input_type = str(item.get("input_type") or ("variable" if _looks_like_selector(item.get("value")) else "constant"))
        if input_type not in {"variable", "constant"}:
            input_type = "constant"
        operation = str(item.get("operation") or item.get("write_mode") or item.get("mode") or "over-write")
        value = item.get("value")
        if input_type == "variable":
            value = _normalize_selector(value)
        normalized_items.append(
            {
                "variable_selector": variable_selector,
                "input_type": input_type,
                "operation": operation,
                "value": value,
            }
        )
    result["version"] = str(result.get("version") or "2")
    result["items"] = normalized_items
    result.pop("operations", None)
    return result


def _normalize_list_operator_params(params: dict[str, Any]) -> dict[str, Any]:
    result = dict(params)
    raw_variable = result.get("variable") or result.get("variable_selector") or result.get("input_selector") or ["start", "items"]
    result["variable"] = normalize_list_variable_selector(_normalize_selector(raw_variable))
    result["var_type"] = _array_var_type(str(result.get("var_type") or result.get("type") or "array[string]"))
    result["item_var_type"] = _item_var_type(str(result.get("item_var_type") or result.get("item_type") or ""), result["var_type"])
    result["filter_by"] = _normalize_list_filter(result.get("filter_by") or result.get("filter"))
    result["extract_by"] = _normalize_extract_by(result.get("extract_by") or result.get("extract"))
    result["order_by"] = _normalize_order_by(result.get("order_by") or result.get("sort_by") or result.get("sort"))
    result["limit"] = _normalize_limit(result.get("limit"))
    result.pop("variable_selector", None)
    result.pop("input_selector", None)
    result.pop("type", None)
    result.pop("item_type", None)
    result.pop("filter", None)
    result.pop("extract", None)
    result.pop("sort_by", None)
    result.pop("sort", None)
    return result


def _normalize_knowledge_retrieval_params(params: dict[str, Any], default_dataset_ids: list[str]) -> dict[str, Any]:
    result = dict(params)
    raw_query_selector = (
        result.get("query_variable_selector")
        or result.get("query")
        or result.get("variable_selector")
        or result.get("input_selector")
        or ["start", "query"]
    )
    result["query_variable_selector"] = _normalize_selector(raw_query_selector)
    attachment_selector = result.get("query_attachment_selector") or result.get("attachment_selector") or []
    result["query_attachment_selector"] = _normalize_optional_selector(attachment_selector)
    dataset_ids = _normalize_dataset_ids(result.get("dataset_ids") or result.get("datasets"))
    if not dataset_ids:
        dataset_ids = list(default_dataset_ids)
    result["dataset_ids"] = dataset_ids
    result["retrieval_mode"] = _retrieval_mode(str(result.get("retrieval_mode") or result.get("mode") or "multiple"))
    result["multiple_retrieval_config"] = _normalize_multiple_retrieval_config(
        result.get("multiple_retrieval_config") or result.get("retrieval_config")
    )
    if result["retrieval_mode"] == "single":
        single = result.get("single_retrieval_config") if isinstance(result.get("single_retrieval_config"), dict) else {}
        if single:
            result["single_retrieval_config"] = single
    else:
        result.pop("single_retrieval_config", None)
    result["metadata_filtering_mode"] = str(result.get("metadata_filtering_mode") or "disabled")
    if result["metadata_filtering_mode"] not in {"disabled", "automatic", "manual"}:
        result["metadata_filtering_mode"] = "disabled"
    result["vision"] = _normalize_vision(result.get("vision"))
    result.pop("query", None)
    result.pop("variable_selector", None)
    result.pop("input_selector", None)
    result.pop("attachment_selector", None)
    result.pop("datasets", None)
    result.pop("mode", None)
    result.pop("retrieval_config", None)
    return result


def _normalize_human_input_params(params: dict[str, Any]) -> dict[str, Any]:
    result = dict(params)
    result["delivery_methods"] = _normalize_human_delivery_methods(
        result.get("delivery_methods") or result.get("delivery") or result.get("methods") or []
    )
    result["form_content"] = normalize_template_refs(
        str(
            result.get("form_content")
            or result.get("content")
            or result.get("form")
            or "请审核以下 workflow 中间结果，并选择处理动作。"
        )
    )
    result["inputs"] = _normalize_human_form_inputs(result.get("inputs") or result.get("fields") or result.get("form_inputs") or [])
    result["user_actions"] = _normalize_human_actions(
        result.get("user_actions") or result.get("actions") or result.get("buttons") or []
    )
    result["timeout"] = _positive_int(result.get("timeout"), default=3)
    result["timeout_unit"] = _timeout_unit(str(result.get("timeout_unit") or result.get("timeoutUnit") or "day"))
    result.pop("delivery", None)
    result.pop("methods", None)
    result.pop("content", None)
    result.pop("form", None)
    result.pop("fields", None)
    result.pop("form_inputs", None)
    result.pop("actions", None)
    result.pop("buttons", None)
    result.pop("timeoutUnit", None)
    return result


def _normalize_iteration_params(
    params: dict[str, Any],
    *,
    node_id: str,
    workflow_name: str,
    default_dataset_ids: list[str],
) -> dict[str, Any]:
    result = dict(params)
    iterator_selector = (
        result.get("iterator_selector")
        or result.get("iterator")
        or result.get("items")
        or result.get("list")
        or result.get("variable")
        or ["start", "items"]
    )
    result["iterator_selector"] = _normalize_selector(iterator_selector)
    result["iterator_input_type"] = _array_var_type(
        str(result.get("iterator_input_type") or result.get("input_type") or "array[string]")
    )
    result["is_parallel"] = bool(result.get("is_parallel", result.get("parallel", False)))
    result["parallel_nums"] = _positive_int(result.get("parallel_nums") or result.get("parallel_count"), default=10)
    result["error_handle_mode"] = _error_handle_mode(str(result.get("error_handle_mode") or "terminated"))
    result["flatten_output"] = bool(result.get("flatten_output", True))
    result.setdefault("_isShowTips", False)

    start_node_id = str(result.get("start_node_id") or result.get("start") or f"{node_id}start")
    children = _normalize_container_children(
        result.get("children") or result.get("_children") or result.get("nodes") or [],
        container_id=node_id,
        container_type="iteration",
        start_node_id=start_node_id,
        workflow_name=workflow_name,
        default_dataset_ids=default_dataset_ids,
    )
    result["start_node_id"] = str(children[0].get("id") if children else start_node_id)
    result["children"] = children
    result["edges"] = _normalize_container_edges(
        result.get("edges") or result.get("child_edges") or [],
        children,
        start_node_id=result["start_node_id"],
    )

    raw_output_selector = result.get("output_selector") or result.get("output") or result.get("result_selector")
    output_selector = _normalize_optional_selector(raw_output_selector)
    if not output_selector:
        last_child = _last_processing_child(children)
        if last_child:
            output_selector = [str(last_child.get("id")), _default_node_output_name(last_child)]
    result["output_selector"] = output_selector
    result["output_type"] = _array_var_type(str(result.get("output_type") or "array[string]"))

    result.pop("iterator", None)
    result.pop("items", None)
    result.pop("list", None)
    result.pop("variable", None)
    result.pop("input_type", None)
    result.pop("parallel", None)
    result.pop("parallel_count", None)
    result.pop("start", None)
    result.pop("nodes", None)
    result.pop("child_edges", None)
    result.pop("output", None)
    result.pop("result_selector", None)
    return result


def _normalize_loop_params(
    params: dict[str, Any],
    *,
    node_id: str,
    workflow_name: str,
    default_dataset_ids: list[str],
) -> dict[str, Any]:
    result = dict(params)
    result["loop_count"] = _positive_int(
        result.get("loop_count") or result.get("max_iterations") or result.get("times"),
        default=3,
    )
    logical_operator = str(result.get("logical_operator") or result.get("operator") or "and").lower()
    result["logical_operator"] = logical_operator if logical_operator in {"and", "or"} else "and"
    result["error_handle_mode"] = _error_handle_mode(str(result.get("error_handle_mode") or "terminated"))
    result["break_conditions"] = _normalize_loop_break_conditions(
        result.get("break_conditions") or result.get("conditions") or result.get("until") or []
    )
    result["loop_variables"] = _normalize_loop_variables(
        result.get("loop_variables") or result.get("variables") or result.get("state") or []
    )

    start_node_id = str(result.get("start_node_id") or result.get("start") or f"{node_id}start")
    children = _normalize_container_children(
        result.get("children") or result.get("_children") or result.get("nodes") or [],
        container_id=node_id,
        container_type="loop",
        start_node_id=start_node_id,
        workflow_name=workflow_name,
        default_dataset_ids=default_dataset_ids,
    )
    result["start_node_id"] = str(children[0].get("id") if children else start_node_id)
    result["children"] = children
    result["edges"] = _normalize_container_edges(
        result.get("edges") or result.get("child_edges") or [],
        children,
        start_node_id=result["start_node_id"],
    )
    _rewrite_loop_break_conditions_for_dify_checklist(result, node_id=node_id)

    result.pop("max_iterations", None)
    result.pop("times", None)
    result.pop("operator", None)
    result.pop("conditions", None)
    result.pop("until", None)
    result.pop("variables", None)
    result.pop("state", None)
    result.pop("start", None)
    result.pop("nodes", None)
    result.pop("child_edges", None)
    return result


def _normalize_container_children(
    value: Any,
    *,
    container_id: str,
    container_type: str,
    start_node_id: str,
    workflow_name: str,
    default_dataset_ids: list[str],
) -> list[dict[str, Any]]:
    internal_start_type = "iteration-start" if container_type == "iteration" else "loop-start"
    raw_children = value if isinstance(value, list) else []
    children: list[dict[str, Any]] = []
    for idx, item in enumerate(raw_children):
        child = _normalize_container_child(
            item,
            container_id=container_id,
            container_type=container_type,
            workflow_name=workflow_name,
            default_dataset_ids=default_dataset_ids,
            index=idx,
        )
        if child:
            children.append(child)

    start_index = next((idx for idx, child in enumerate(children) if child.get("type") == internal_start_type), None)
    if start_index is None:
        children.insert(
            0,
            {
                "id": start_node_id,
                "type": internal_start_type,
                "title": _default_title(internal_start_type),
                "desc": "",
                "params": {},
            },
        )
    elif start_index != 0:
        children.insert(0, children.pop(start_index))
    if children:
        children[0]["id"] = str(children[0].get("id") or start_node_id)
        children[0]["type"] = internal_start_type

    if len(children) == 1:
        children.append(_default_container_processing_child(container_id, container_type))
    return _dedupe_container_child_ids(children, container_id=container_id)


def _normalize_container_child(
    item: Any,
    *,
    container_id: str,
    container_type: str,
    workflow_name: str,
    default_dataset_ids: list[str],
    index: int,
) -> dict[str, Any] | None:
    if isinstance(item, str):
        item = {"type": item}
    if not isinstance(item, dict):
        return None
    child = deepcopy(item)
    raw_type = str(child.get("type") or child.get("node_type") or "")
    child_type = _normalize_node_type(raw_type)
    internal_start_type = "iteration-start" if container_type == "iteration" else "loop-start"
    if child_type == "start":
        child_type = internal_start_type
    if child_type == "end" and container_type == "loop":
        child_type = "loop-end"
    child["type"] = child_type
    child.setdefault("id", f"{container_id}_{child_type.replace('-', '_')}_{index + 1}")
    child.setdefault("title", _default_title(child_type))
    child.setdefault("desc", "")
    params = child.get("params")
    if not isinstance(params, dict):
        params = {}
    match child_type:
        case "llm":
            child["params"] = _normalize_llm_params(params, workflow_name=workflow_name)
        case "code":
            child["params"] = _normalize_code_params(params)
        case "if-else":
            child["params"] = _normalize_if_else_params(params)
        case "http-request":
            child["params"] = _normalize_http_params(params)
        case "template-transform":
            child["params"] = _normalize_template_params(params)
        case "question-classifier":
            child["params"] = _normalize_question_classifier_params(params)
        case "parameter-extractor":
            child["params"] = _normalize_parameter_extractor_params(params)
        case "variable-aggregator":
            child["params"] = _normalize_variable_aggregator_params(params)
        case "document-extractor":
            child["params"] = _normalize_document_extractor_params(params)
        case "assigner":
            child["params"] = _normalize_assigner_params(params)
        case "list-operator":
            list_params = _normalize_list_operator_params(params)
            if _list_operator_needs_code_fallback(list_params):
                child["type"] = "code"
                child["params"] = _normalize_code_params(_object_list_operator_code_params(list_params))
            else:
                child["params"] = list_params
        case "knowledge-retrieval":
            child["params"] = _normalize_knowledge_retrieval_params(params, default_dataset_ids)
        case "human-input":
            child["params"] = _normalize_human_input_params(params)
        case child_type if child_type in EXTERNAL_DEPENDENCY_NODE_TYPES:
            child["params"] = _normalize_external_dependency_params(params)
        case "iteration-start" | "loop-start" | "loop-end":
            child["params"] = dict(params)
        case _:
            child["params"] = dict(params)
    return child


def _default_container_processing_child(container_id: str, container_type: str) -> dict[str, Any]:
    if container_type == "iteration":
        child_id = f"{container_id}_item_template"
        return {
            "id": child_id,
            "type": "template-transform",
            "title": "处理循环项",
            "desc": "",
            "params": _normalize_template_params(
                {
                    "template": "{{ item }}",
                    "variables": [
                        {"variable": "item", "value_selector": [container_id, "item"], "value_type": "string"}
                    ],
                }
            ),
        }
    child_id = f"{container_id}_loop_step"
    return {
        "id": child_id,
        "type": "template-transform",
        "title": "执行循环步骤",
        "desc": "",
        "params": _normalize_template_params(
            {
                "template": "{{ query }}",
                "variables": [{"variable": "query", "value_selector": ["start", "query"], "value_type": "string"}],
            }
        ),
    }


def _dedupe_container_child_ids(children: list[dict[str, Any]], *, container_id: str) -> list[dict[str, Any]]:
    used: set[str] = set()
    result: list[dict[str, Any]] = []
    for idx, child in enumerate(children):
        child_copy = dict(child)
        child_id = str(child_copy.get("id") or f"{container_id}_child_{idx + 1}")
        if child_id in used:
            base = child_id
            suffix = 2
            while child_id in used:
                child_id = f"{base}_{suffix}"
                suffix += 1
            child_copy["id"] = child_id
        used.add(child_id)
        result.append(child_copy)
    return result


def _normalize_container_edges(value: Any, children: list[dict[str, Any]], *, start_node_id: str) -> list[dict[str, Any]]:
    child_ids = {str(child.get("id")) for child in children if child.get("id")}
    edges: list[dict[str, Any]] = []
    for item in value if isinstance(value, list) else []:
        if not isinstance(item, dict):
            continue
        source = str(item.get("source") or item.get("source_node_id") or "")
        target = str(item.get("target") or item.get("target_node_id") or "")
        if source not in child_ids or target not in child_ids:
            continue
        edges.append(
            {
                "source": source,
                "target": target,
                "source_handle": str(item.get("source_handle") or item.get("sourceHandle") or SOURCE_HANDLE),
                "target_handle": str(item.get("target_handle") or item.get("targetHandle") or "target"),
            }
        )
    if edges:
        return edges

    ordered_ids = [str(child.get("id")) for child in children if child.get("id")]
    processing_ids = [child_id for child_id in ordered_ids if child_id != start_node_id]
    if not processing_ids:
        return []
    chain = [{"source": start_node_id, "target": processing_ids[0], "source_handle": SOURCE_HANDLE, "target_handle": "target"}]
    for source, target in zip(processing_ids, processing_ids[1:], strict=False):
        chain.append({"source": source, "target": target, "source_handle": SOURCE_HANDLE, "target_handle": "target"})
    return chain


def _last_processing_child(children: list[dict[str, Any]]) -> dict[str, Any] | None:
    for child in reversed(children):
        if child.get("type") not in {"iteration-start", "loop-start", "loop-end"}:
            return child
    return None


def _default_node_output_name(node: dict[str, Any]) -> str:
    node_type = str(node.get("type") or "")
    params = node.get("params", {}) if isinstance(node.get("params"), dict) else {}
    if node_type == "llm":
        return "text"
    if node_type == "code":
        outputs = params.get("outputs") if isinstance(params.get("outputs"), dict) else {}
        return next(iter(outputs.keys()), "result")
    if node_type == "parameter-extractor":
        parameters = params.get("parameters") if isinstance(params.get("parameters"), list) else []
        first = next((item for item in parameters if isinstance(item, dict) and item.get("name")), None)
        return str(first.get("name")) if first else "__reason"
    mapping = {
        "template-transform": "output",
        "variable-aggregator": "output",
        "document-extractor": "text",
        "list-operator": "result",
        "knowledge-retrieval": "result",
        "http-request": "body",
        "human-input": "selected_action",
    }
    return mapping.get(node_type, "output")


def _normalize_loop_break_conditions(value: Any) -> list[dict[str, Any]]:
    conditions: list[dict[str, Any]] = []
    if isinstance(value, dict):
        value = [value]
    if isinstance(value, str) and value.strip():
        value = [_condition_from_text(value)]
    for idx, item in enumerate(value or []):
        if isinstance(item, str):
            item = _condition_from_text(item)
        if not isinstance(item, dict):
            continue
        condition = dict(item)
        condition["id"] = str(condition.get("id") or f"condition_{idx + 1}")
        if isinstance(condition.get("variable_selector"), str):
            condition["variable_selector"] = _selector_from_ref(condition["variable_selector"])
        else:
            condition["variable_selector"] = _normalize_optional_selector(condition.get("variable_selector"))
        condition.setdefault("comparison_operator", "not empty")
        condition.setdefault("value", "")
        condition.setdefault("varType", "string")
        conditions.append(condition)
    return conditions


def _rewrite_loop_break_conditions_for_dify_checklist(result: dict[str, Any], *, node_id: str) -> None:
    children = result.get("children") if isinstance(result.get("children"), list) else []
    child_by_id = {
        str(child.get("id")): child
        for child in children
        if isinstance(child, dict) and child.get("id")
    }
    if not child_by_id:
        return

    loop_variables = result.get("loop_variables") if isinstance(result.get("loop_variables"), list) else []
    used_labels = {
        str(item.get("label"))
        for item in loop_variables
        if isinstance(item, dict) and item.get("label")
    }
    child_output_selectors: dict[tuple[str, ...], str] = {}
    for condition in result.get("break_conditions") or []:
        if not isinstance(condition, dict):
            continue
        selector = condition.get("variable_selector")
        if not _looks_like_selector(selector):
            continue
        selector = [str(item) for item in selector]
        child_id = selector[0]
        if child_id not in child_by_id:
            continue
        child_type = str(child_by_id[child_id].get("type") or "")
        if child_type in {"loop-start", "loop-end"}:
            continue
        selector_key = tuple(selector)
        label = child_output_selectors.get(selector_key)
        if not label:
            label = _unique_loop_variable_label("_".join(selector), used_labels)
            used_labels.add(label)
            loop_variables.append(
                {
                    "id": label,
                    "label": label,
                    "var_type": _var_type(str(condition.get("varType") or "string")),
                    "value_type": "constant",
                    "value": "",
                }
            )
            child_output_selectors[selector_key] = label
        condition["variable_selector"] = [node_id, label]

    if not child_output_selectors:
        return

    result["loop_variables"] = loop_variables
    result["children"] = children
    result["edges"] = result.get("edges") if isinstance(result.get("edges"), list) else []
    existing_child_ids = set(child_by_id)
    for selector_key, label in child_output_selectors.items():
        selector = list(selector_key)
        source_child_id = selector[0]
        if _loop_assigner_exists(children, node_id=node_id, variable_label=label, value_selector=selector):
            continue
        assigner_id = _unique_child_id(f"{node_id}_{label}_assigner", existing_child_ids)
        existing_child_ids.add(assigner_id)
        assigner = {
            "id": assigner_id,
            "type": "assigner",
            "title": "更新循环判断变量",
            "desc": "",
            "params": _normalize_assigner_params(
                {
                    "version": "2",
                    "items": [
                        {
                            "variable_selector": [node_id, label],
                            "input_type": "variable",
                            "operation": "over-write",
                            "value": selector,
                        }
                    ],
                }
            ),
        }
        _insert_child_after(children, assigner, source_child_id)
        _insert_assigner_edges(result["edges"], source_child_id=source_child_id, assigner_id=assigner_id)


def _unique_loop_variable_label(value: str, used_labels: set[str]) -> str:
    preferred = _safe_variable_name(value)
    if preferred.startswith("loop_"):
        preferred = preferred.removeprefix("loop_")
    if not preferred:
        preferred = "loop_result"
    candidate = preferred
    index = 2
    while candidate in used_labels:
        candidate = f"{preferred}_{index}"
        index += 1
    return candidate


def _unique_child_id(value: str, used_ids: set[str]) -> str:
    preferred = _safe_variable_name(value)
    candidate = preferred
    index = 2
    while candidate in used_ids:
        candidate = f"{preferred}_{index}"
        index += 1
    return candidate


def _insert_child_after(children: list[dict[str, Any]], child: dict[str, Any], source_child_id: str) -> None:
    for idx, item in enumerate(children):
        if isinstance(item, dict) and str(item.get("id")) == source_child_id:
            children.insert(idx + 1, child)
            return
    children.append(child)


def _loop_assigner_exists(
    children: list[dict[str, Any]],
    *,
    node_id: str,
    variable_label: str,
    value_selector: list[str],
) -> bool:
    for child in children:
        if not isinstance(child, dict) or child.get("type") != "assigner":
            continue
        params = child.get("params") if isinstance(child.get("params"), dict) else {}
        for item in params.get("items") if isinstance(params.get("items"), list) else []:
            if not isinstance(item, dict):
                continue
            if item.get("variable_selector") == [node_id, variable_label] and item.get("value") == value_selector:
                return True
    return False


def _insert_assigner_edges(edges: list[dict[str, Any]], *, source_child_id: str, assigner_id: str) -> None:
    outgoing = [
        edge
        for edge in edges
        if isinstance(edge, dict) and str(edge.get("source")) == source_child_id and str(edge.get("target")) != assigner_id
    ]
    if not any(
        isinstance(edge, dict) and str(edge.get("source")) == source_child_id and str(edge.get("target")) == assigner_id
        for edge in edges
    ):
        edges.append(
            {
                "source": source_child_id,
                "target": assigner_id,
                "source_handle": SOURCE_HANDLE,
                "target_handle": "target",
            }
        )
    for edge in outgoing:
        edge["source"] = assigner_id
        edge["source_handle"] = SOURCE_HANDLE


def _normalize_loop_variables(value: Any) -> list[dict[str, Any]]:
    variables: list[dict[str, Any]] = []
    if isinstance(value, dict):
        value = [{"label": key, **(item if isinstance(item, dict) else {"value": item})} for key, item in value.items()]
    for idx, item in enumerate(value or []):
        if isinstance(item, str):
            item = {"label": item, "var_type": "string", "value_type": "constant", "value": ""}
        if not isinstance(item, dict):
            continue
        label = str(item.get("label") or item.get("name") or item.get("variable") or f"state_{idx + 1}")
        value_type = str(item.get("value_type") or item.get("type") or "constant")
        if value_type not in {"constant", "variable"}:
            value_type = "constant"
        value = item.get("value", "")
        if value_type == "variable":
            value = _normalize_optional_selector(value)
        variables.append(
            {
                "id": str(item.get("id") or _safe_variable_name(label) or f"state_{idx + 1}"),
                "label": _safe_variable_name(label),
                "var_type": _var_type(str(item.get("var_type") or item.get("variable_type") or "string")),
                "value_type": value_type,
                "value": value,
            }
        )
    return variables


def _normalize_tool_params(params: dict[str, Any], tool_selections: list[dict[str, Any]]) -> dict[str, Any]:
    if isinstance(params.get("_raw_data"), dict):
        return _normalize_external_dependency_params(params)

    result = dict(params)
    selected = _find_selected_tool(result, tool_selections)
    if selected:
        result.setdefault("provider_id", selected.get("provider_id"))
        result.setdefault("provider_type", selected.get("provider_type"))
        result.setdefault("provider_name", selected.get("provider_name") or selected.get("provider_id"))
        result.setdefault("tool_name", selected.get("tool_name"))
        result.setdefault("tool_label", selected.get("tool_label") or selected.get("tool_name"))
        if selected.get("description") and not result.get("tool_description"):
            result["tool_description"] = selected.get("description")
        if selected.get("plugin_id") and not result.get("plugin_id"):
            result["plugin_id"] = selected.get("plugin_id")
        if selected.get("plugin_unique_identifier") and not result.get("plugin_unique_identifier"):
            result["plugin_unique_identifier"] = selected.get("plugin_unique_identifier")
        if selected.get("is_team_authorization") is not None and result.get("is_team_authorization") is None:
            result["is_team_authorization"] = selected.get("is_team_authorization")
        if selected.get("output_schema") and not result.get("output_schema"):
            result["output_schema"] = deepcopy(selected.get("output_schema"))

    schemas = _normalize_tool_param_schemas(
        result.get("paramSchemas") or result.get("parameters") or (selected or {}).get("parameters") or []
    )
    if schemas:
        result["paramSchemas"] = schemas
    result.pop("parameters", None)

    result["provider_id"] = str(result.get("provider_id") or "").strip()
    result["provider_type"] = str(result.get("provider_type") or "").strip()
    result["provider_name"] = str(result.get("provider_name") or result.get("provider_id") or "").strip()
    result["tool_name"] = str(result.get("tool_name") or "").strip()
    result["tool_label"] = str(result.get("tool_label") or result.get("tool_name") or "").strip()
    result["tool_node_version"] = str(result.get("tool_node_version") or "2")
    explicit_tool_parameters = (selected or {}).get("tool_parameters") if isinstance(selected, dict) else {}
    explicit_tool_configurations = (selected or {}).get("tool_configurations") if isinstance(selected, dict) else {}
    result["tool_parameters"] = _normalize_tool_runtime_inputs(
        _merge_tool_inputs(
            result.get("tool_parameters") or result.get("tool_inputs") or {},
            explicit_tool_parameters,
        ),
        schemas,
        form="llm",
    )
    result["tool_configurations"] = _normalize_tool_configurations(
        _merge_tool_inputs(
            result.get("tool_configurations") or result.get("tool_settings") or result.get("config") or {},
            explicit_tool_configurations,
        ),
        schemas,
    )
    result.pop("tool_inputs", None)
    result.pop("tool_settings", None)
    result.pop("config", None)
    return result


def _find_selected_tool(params: dict[str, Any], tool_selections: list[dict[str, Any]]) -> dict[str, Any] | None:
    if not tool_selections:
        return None
    provider_id = str(params.get("provider_id") or "").strip()
    provider_type = str(params.get("provider_type") or "").strip()
    tool_name = str(params.get("tool_name") or params.get("name") or "").strip()
    candidates = [item for item in tool_selections if isinstance(item, dict)]
    for item in candidates:
        if (
            provider_id
            and tool_name
            and str(item.get("provider_id") or "").strip() == provider_id
            and str(item.get("tool_name") or "").strip() == tool_name
            and (not provider_type or str(item.get("provider_type") or "").strip() == provider_type)
        ):
            return item
    if tool_name:
        matches = [item for item in candidates if str(item.get("tool_name") or "").strip() == tool_name]
        if len(matches) == 1:
            return matches[0]
    if provider_id:
        matches = [item for item in candidates if str(item.get("provider_id") or "").strip() == provider_id]
        if len(matches) == 1:
            return matches[0]
    return candidates[0] if len(candidates) == 1 else None


def _merge_tool_inputs(generated: Any, explicit: Any) -> dict[str, Any]:
    base = deepcopy(generated) if isinstance(generated, dict) else {}
    if isinstance(explicit, dict):
        for key, value in explicit.items():
            base[str(key)] = deepcopy(value)
    return base


def _normalize_tool_param_schemas(value: Any) -> list[dict[str, Any]]:
    schemas: list[dict[str, Any]] = []
    for item in value or []:
        if not isinstance(item, dict):
            continue
        name = str(item.get("name") or item.get("variable") or "").strip()
        if not name:
            continue
        schema = deepcopy(item)
        schema["name"] = name
        schema["variable"] = str(schema.get("variable") or name)
        schema["form"] = str(schema.get("form") or "llm")
        schema["type"] = str(schema.get("type") or "string")
        schema["required"] = bool(schema.get("required", False))
        if "label" not in schema or schema.get("label") in (None, ""):
            schema["label"] = {"en_US": name, "zh_Hans": name}
        schemas.append(schema)
    return schemas


def _normalize_tool_runtime_inputs(value: Any, schemas: list[dict[str, Any]], *, form: str) -> dict[str, Any]:
    raw = value if isinstance(value, dict) else {}
    result: dict[str, Any] = {}
    schema_by_name = {
        str(schema.get("name") or schema.get("variable")): schema
        for schema in schemas
        if str(schema.get("form") or "") == form and (schema.get("name") or schema.get("variable"))
    }
    for name, schema in schema_by_name.items():
        raw_value = raw.get(name)
        variable_name = str(schema.get("variable") or name)
        if raw_value is None and variable_name != name:
            raw_value = raw.get(variable_name)
        if _is_empty_tool_input(raw_value):
            raw_value = None
        if raw_value is None:
            default = schema.get("default")
            if not _is_empty_tool_input(default):
                raw_value = default
            elif schema.get("required") and _is_query_like_tool_parameter(name):
                raw_value = ["start", "query"]
        if raw_value is not None:
            result[variable_name] = _normalize_tool_runtime_input(raw_value, schema)

    for key, raw_value in raw.items():
        if key in result:
            continue
        if any(str(schema.get("name")) == key or str(schema.get("variable")) == key for schema in schema_by_name.values()):
            continue
        schema = next(
            (item for item in schemas if str(item.get("name")) == str(key) or str(item.get("variable")) == str(key)),
            {},
        )
        result[str(key)] = _normalize_tool_runtime_input(raw_value, schema)
    return result


def _normalize_tool_configurations(value: Any, schemas: list[dict[str, Any]]) -> dict[str, Any]:
    raw = value if isinstance(value, dict) else {}
    result: dict[str, Any] = {}
    config_schemas = [
        schema
        for schema in schemas
        if str(schema.get("form") or "") != "llm" and (schema.get("name") or schema.get("variable"))
    ]
    for schema in config_schemas:
        name = str(schema.get("variable") or schema.get("name"))
        raw_value = raw.get(name)
        if raw_value is None and str(schema.get("name")) != name:
            raw_value = raw.get(str(schema.get("name")))
        if _is_empty_tool_input(raw_value):
            raw_value = None
        if raw_value is None:
            raw_value = _tool_schema_default_value(schema)
        if raw_value is not None:
            result[name] = _normalize_tool_form_input(raw_value, schema)
    for key, raw_value in raw.items():
        if _is_empty_tool_input(raw_value):
            continue
        result.setdefault(str(key), deepcopy(raw_value))
    return result


def _normalize_tool_var_input(value: Any) -> dict[str, Any]:
    if isinstance(value, dict) and "value" in value:
        kind = str(value.get("type") or "mixed")
        if kind not in {"variable", "constant", "mixed"}:
            kind = "mixed"
        raw_value = value.get("value")
        if kind == "variable":
            raw_value = _normalize_selector(raw_value)
        elif isinstance(raw_value, str):
            raw_value = normalize_template_refs(raw_value)
        return {"type": kind, "value": raw_value}
    if isinstance(value, list) and _looks_like_selector(value):
        return {"type": "variable", "value": _normalize_selector(value)}
    if isinstance(value, str):
        text = normalize_template_refs(value)
        match = DIFY_REF_PATTERN.fullmatch(text.strip())
        if match:
            return {"type": "variable", "value": [match.group(1), *[piece for piece in match.group(2).split(".") if piece]]}
        if re.fullmatch(r"[A-Za-z0-9_-]+\.[A-Za-z0-9_.-]+", text.strip()):
            return {"type": "variable", "value": _normalize_selector(text)}
        return {"type": "mixed", "value": text}
    return {"type": "constant", "value": deepcopy(value)}


def _normalize_tool_runtime_input(value: Any, schema: dict[str, Any]) -> dict[str, Any]:
    normalized = _normalize_tool_var_input(value)
    if not _tool_schema_uses_mixed_text(schema):
        return normalized
    raw_value = normalized.get("value")
    if normalized.get("type") == "variable" and _looks_like_selector(raw_value):
        return {"type": "mixed", "value": _selector_to_dify_template(raw_value)}
    if isinstance(raw_value, str):
        return {"type": "mixed", "value": normalize_template_refs(raw_value)}
    return normalized


def _tool_schema_uses_mixed_text(schema: dict[str, Any]) -> bool:
    schema_type = str(schema.get("type") or "").strip().lower()
    return schema_type in {"", "string", "text-input", "secret-input"}


def _selector_to_dify_template(selector: Any) -> str:
    normalized = _normalize_selector(selector)
    if len(normalized) < 2:
        return ""
    return "{{#" + ".".join(normalized) + "#}}"


def _normalize_tool_form_input(value: Any, schema: dict[str, Any]) -> dict[str, Any]:
    if isinstance(value, dict) and "type" in value and "value" in value:
        return _normalize_tool_var_input(value)
    form_type = str(schema.get("type") or "").strip().lower()
    if form_type == "boolean":
        return {"type": "constant", "value": _coerce_tool_bool(value)}
    if form_type in {"number", "number-input"}:
        return {"type": "constant", "value": _coerce_tool_number(value)}
    if form_type in {"select", "checkbox"}:
        return {"type": "constant", "value": deepcopy(value)}
    if form_type in {"model-selector", "app-selector"}:
        return {"type": "constant", "value": deepcopy(value)}
    return {"type": "mixed", "value": str(value) if value is not None else ""}


def _tool_schema_default_value(schema: dict[str, Any]) -> Any:
    default = schema.get("default")
    if not _is_empty_tool_input(default):
        return default
    options = schema.get("options") if isinstance(schema.get("options"), list) else []
    if schema.get("required") and options:
        first = options[0]
        if isinstance(first, dict) and not _is_empty_tool_input(first.get("value")):
            return first.get("value")
    return None


def _is_empty_tool_input(value: Any) -> bool:
    if value is None or value == "":
        return True
    if isinstance(value, dict) and "value" in value:
        raw_value = value.get("value")
        return raw_value is None or raw_value == "" or raw_value == []
    return False


def _coerce_tool_bool(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return value == 1
    if isinstance(value, str):
        return value.strip().lower() in {"true", "1", "yes", "y", "on"}
    return bool(value)


def _coerce_tool_number(value: Any) -> int | float | Any:
    if isinstance(value, (int, float)):
        return value
    if isinstance(value, str):
        stripped = value.strip()
        if not stripped:
            return value
        try:
            parsed = float(stripped)
        except ValueError:
            return value
        return int(parsed) if parsed.is_integer() else parsed
    return value


def _is_query_like_tool_parameter(name: str) -> bool:
    normalized = _safe_variable_name(name)
    return normalized in {"query", "q", "question", "input", "text", "keyword", "keywords", "url"}


def _normalize_external_dependency_params(params: dict[str, Any]) -> dict[str, Any]:
    result = deepcopy(params)
    if "_raw_data" in result and isinstance(result["_raw_data"], dict):
        result["_raw_data"] = deepcopy(result["_raw_data"])
    return result


def _error_handle_mode(value: str) -> str:
    normalized = str(value or "").strip().lower().replace("_", "-")
    allowed = {"terminated", "continue-on-error", "remove-abnormal-output"}
    if normalized in {"continue", "continue-error", "continue-on-error"}:
        return "continue-on-error"
    if normalized in {"remove", "remove-abnormal-output", "remove-abnormal"}:
        return "remove-abnormal-output"
    return normalized if normalized in allowed else "terminated"


def _normalize_human_delivery_methods(value: Any) -> list[dict[str, Any]]:
    methods: list[dict[str, Any]] = []
    if isinstance(value, str):
        value = [{"type": value}]
    for idx, item in enumerate(value or []):
        if isinstance(item, str):
            item = {"type": item}
        if not isinstance(item, dict):
            continue
        method_type = str(item.get("type") or "webapp").strip().lower()
        if method_type not in {"webapp", "email", "slack", "teams", "discord"}:
            method_type = "webapp"
        method = {
            "id": _normalize_human_delivery_method_id(item.get("id"), fallback_key=f"{method_type}-{idx + 1}"),
            "type": method_type,
            "enabled": bool(item.get("enabled", True)),
        }
        if isinstance(item.get("config"), dict):
            method["config"] = deepcopy(item["config"])
        elif method_type == "webapp":
            method["config"] = {}
        methods.append(method)
    if not methods:
        methods.append(
            {
                "id": HUMAN_INPUT_DEFAULT_WEBAPP_DELIVERY_ID,
                "type": "webapp",
                "enabled": True,
                "config": {},
            }
        )
    if not any(method.get("enabled") for method in methods):
        methods[0]["enabled"] = True
    return methods


def _normalize_human_delivery_method_id(value: Any, *, fallback_key: str) -> str:
    raw = str(value or "").strip()
    if raw:
        try:
            return str(UUID(raw))
        except ValueError:
            return str(uuid5(NAMESPACE_URL, f"chat2dify:human-input:delivery:{raw}"))
    return str(uuid5(NAMESPACE_URL, f"chat2dify:human-input:delivery:{fallback_key}"))


def _normalize_human_form_inputs(value: Any) -> list[dict[str, Any]]:
    inputs: list[dict[str, Any]] = []
    if isinstance(value, dict):
        value = [{"output_variable_name": key, **(item if isinstance(item, dict) else {"type": item})} for key, item in value.items()]
    used_names: set[str] = set()
    for idx, item in enumerate(value or []):
        if isinstance(item, str):
            item = {"output_variable_name": item, "type": "paragraph"}
        if not isinstance(item, dict):
            continue
        raw_name = str(item.get("output_variable_name") or item.get("name") or item.get("variable") or f"input_{idx + 1}")
        output_name = _unique_parameter_name(raw_name, used_names)
        default = item.get("default") if isinstance(item.get("default"), dict) else {}
        default_type = str(default.get("type") or item.get("default_type") or "constant")
        if default_type not in {"constant", "variable"}:
            default_type = "constant"
        default_value = default.get("value", item.get("default_value", ""))
        default_selector = default.get("selector") or item.get("selector") or []
        normalized_default: dict[str, Any] = {"type": default_type}
        if default_type == "variable":
            normalized_default["selector"] = _normalize_optional_selector(default_selector)
            normalized_default["value"] = ""
        else:
            normalized_default["selector"] = _normalize_optional_selector(default_selector)
            normalized_default["value"] = str(default_value or "")
        inputs.append(
            {
                "type": _human_input_type(str(item.get("type") or item.get("input_type") or "paragraph")),
                "output_variable_name": output_name,
                "default": normalized_default,
            }
        )
    return inputs


def _normalize_human_actions(value: Any) -> list[dict[str, Any]]:
    actions: list[dict[str, Any]] = []
    if isinstance(value, dict):
        value = [{"id": key, "title": item} for key, item in value.items()]
    used_ids: set[str] = set()
    for idx, item in enumerate(value or []):
        if isinstance(item, str):
            item = {"id": item, "title": item}
        if not isinstance(item, dict):
            continue
        title = str(item.get("title") or item.get("name") or item.get("label") or "").strip()
        raw_id = str(item.get("id") or item.get("value") or title or f"action_{idx + 1}")
        action_id = _safe_branch_id(raw_id) or f"action_{idx + 1}"
        preferred = action_id
        duplicate_index = 2
        while action_id in used_ids:
            action_id = f"{preferred}_{duplicate_index}"
            duplicate_index += 1
        used_ids.add(action_id)
        if not title:
            title = action_id
        style = str(item.get("button_style") or item.get("style") or ("primary" if idx == 0 else "default"))
        if style not in {"primary", "default", "accent", "ghost"}:
            style = "default"
        actions.append({"id": action_id, "title": title, "button_style": style})
    if not actions:
        actions = [
            {"id": "approve", "title": "通过", "button_style": "primary"},
            {"id": "reject", "title": "驳回", "button_style": "default"},
        ]
    return actions


def _human_input_type(value: str) -> str:
    normalized = str(value or "").strip().lower().replace("_", "-")
    mapping = {
        "text": "text-input",
        "string": "text-input",
        "text-input": "text-input",
        "paragraph": "paragraph",
        "textarea": "paragraph",
        "number": "number",
        "boolean": "checkbox",
        "bool": "checkbox",
        "checkbox": "checkbox",
        "select": "select",
    }
    return mapping.get(normalized, "paragraph")


def _timeout_unit(value: str) -> str:
    normalized = str(value or "").strip().lower()
    if normalized in {"hour", "hours", "h", "小时"}:
        return "hour"
    return "day"


def _positive_int(value: Any, *, default: int) -> int:
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        parsed = default
    return max(1, parsed)


def _normalize_classifier_classes(value: Any) -> list[dict[str, str]]:
    classes: list[dict[str, str]] = []
    if isinstance(value, dict):
        value = [{"id": key, "name": item} for key, item in value.items()]
    for idx, item in enumerate(value or []):
        if isinstance(item, str):
            raw_id = _safe_branch_id(item) or f"class_{idx + 1}"
            name = item
            label = f"CLASS {idx + 1}"
        elif isinstance(item, dict):
            name = str(item.get("name") or item.get("label") or item.get("title") or "").strip()
            raw_id = str(item.get("id") or item.get("case_id") or item.get("value") or name or f"class_{idx + 1}")
            label = str(item.get("label") or f"CLASS {idx + 1}")
        else:
            continue
        if not name:
            name = f"类别{idx + 1}"
        classes.append({"id": _safe_branch_id(raw_id) or f"class_{idx + 1}", "name": name, "label": label})
    return classes


def _normalize_extractor_parameters(value: Any) -> list[dict[str, Any]]:
    parameters: list[dict[str, Any]] = []
    used_names: set[str] = set()
    if isinstance(value, dict):
        value = [{"name": key, **(item if isinstance(item, dict) else {"description": str(item)})} for key, item in value.items()]
    for item in value or []:
        if isinstance(item, str):
            name = _unique_parameter_name(item, used_names)
            parameters.append(
                {
                    "name": name,
                    "type": "string",
                    "description": item,
                    "required": True,
                }
            )
            continue
        if not isinstance(item, dict):
            continue
        name = str(item.get("name") or item.get("variable") or item.get("field") or "").strip()
        if not name:
            continue
        param_type = _parameter_type(str(item.get("type") or item.get("value_type") or "string"))
        safe_name = _unique_parameter_name(name, used_names)
        parameter = {
            "name": safe_name,
            "type": param_type,
            "description": str(item.get("description") or item.get("desc") or name),
            "required": bool(item.get("required", True)),
        }
        options = item.get("options")
        if param_type == "select" and isinstance(options, list):
            parameter["options"] = [str(option) for option in options if str(option)]
        parameters.append(parameter)
    return parameters


def _unique_parameter_name(value: str, used_names: set[str]) -> str:
    preferred = _parameter_name(value)
    candidate = preferred
    index = 2
    while candidate in used_names:
        candidate = f"{preferred}_{index}"
        index += 1
    used_names.add(candidate)
    return candidate


def _parameter_name(value: str) -> str:
    raw = str(value or "").strip()
    mapping = {
        "车型": "car_model",
        "车牌": "license_plate",
        "门店": "store",
        "订单": "order_id",
        "订单号": "order_id",
        "手机号": "phone",
        "电话": "phone",
        "时间": "time",
        "预约时间": "appointment_time",
        "诉求": "request",
        "问题": "issue",
        "姓名": "name",
        "客户姓名": "customer_name",
    }
    return mapping.get(raw, _safe_variable_name(raw))


def _normalize_selector(value: Any) -> list[str]:
    if isinstance(value, list):
        return [str(item) for item in value if str(item)]
    if isinstance(value, str):
        return _selector_from_ref(value)
    return ["start", "query"]


def _normalize_optional_selector(value: Any) -> list[str]:
    if value in (None, "", [], {}):
        return []
    selector = _normalize_selector(value)
    return selector if len(selector) >= 2 else []


def _normalize_selector_list(value: Any) -> list[list[str]]:
    selectors: list[list[str]] = []
    if isinstance(value, dict):
        value = list(value.values())
    for item in value or []:
        if isinstance(item, dict):
            raw_selector = item.get("value_selector") or item.get("variable_selector") or item.get("selector") or item.get("value")
        else:
            raw_selector = item
        selector = _normalize_selector(raw_selector)
        if len(selector) >= 2:
            selectors.append(selector)
    return selectors


def _normalize_dataset_ids(value: Any) -> list[str]:
    if isinstance(value, str):
        raw_items = value.split(",")
    elif isinstance(value, list):
        raw_items = value
    else:
        raw_items = []
    dataset_ids = []
    for item in raw_items:
        if isinstance(item, dict):
            item = item.get("id") or item.get("dataset_id")
        text = str(item or "").strip()
        if text:
            dataset_ids.append(text)
    return dataset_ids


def _retrieval_mode(value: str) -> str:
    normalized = str(value or "").strip().lower().replace("_", "-")
    if normalized in {"single", "one-way", "oneway"}:
        return "single"
    return "multiple"


def _normalize_multiple_retrieval_config(value: Any) -> dict[str, Any]:
    config = value if isinstance(value, dict) else {}
    try:
        top_k = int(config.get("top_k", 4))
    except (TypeError, ValueError):
        top_k = 4
    score_threshold = config.get("score_threshold")
    if score_threshold in ("", "none", "None"):
        score_threshold = None
    result = {
        "top_k": max(1, top_k),
        "score_threshold": score_threshold,
        "reranking_enable": bool(config.get("reranking_enable", False)),
        "reranking_mode": str(config.get("reranking_mode") or "reranking_model"),
    }
    reranking_model = _normalize_reranking_model(config.get("reranking_model"))
    if result["reranking_enable"] and reranking_model:
        result["reranking_model"] = reranking_model
    if isinstance(config.get("weights"), dict):
        result["weights"] = deepcopy(config["weights"])
    return result


def _normalize_reranking_model(value: Any) -> dict[str, str] | None:
    if not isinstance(value, dict):
        return None
    provider = str(value.get("provider") or value.get("reranking_provider_name") or "").strip()
    model = str(value.get("model") or value.get("reranking_model_name") or "").strip()
    if not provider or not model:
        return None
    return {"provider": provider, "model": model}


def _looks_like_selector(value: Any) -> bool:
    if isinstance(value, list):
        return len(value) >= 2 and all(isinstance(item, str | int | float | bool) for item in value)
    if isinstance(value, str):
        return bool(DIFY_REF_PATTERN.search(normalize_template_refs(value)) or len([piece for piece in value.split(".") if piece]) >= 2)
    return False


def _normalize_vision(value: Any) -> dict[str, Any]:
    if isinstance(value, dict):
        enabled = bool(value.get("enabled", False))
        configs = value.get("configs") if isinstance(value.get("configs"), dict) else {"variable_selector": []}
        configs.setdefault("variable_selector", [])
        return {"enabled": enabled, "configs": configs}
    return {"enabled": False, "configs": {"variable_selector": []}}


def _var_type(value: str) -> str:
    normalized = str(value or "").strip().lower().replace("_", "-")
    mapping = {
        "str": "string",
        "text": "string",
        "paragraph": "string",
        "integer": "number",
        "int": "number",
        "float": "number",
        "bool": "boolean",
        "boolean": "boolean",
        "object": "object",
        "dict": "object",
        "array-string": "array[string]",
        "string[]": "array[string]",
        "array-number": "array[number]",
        "number[]": "array[number]",
        "array-object": "array[object]",
        "object[]": "array[object]",
        "array-boolean": "array[boolean]",
        "array-bool": "array[boolean]",
        "boolean[]": "array[boolean]",
        "array-file": "array[file]",
        "file[]": "array[file]",
        "file-list": "array[file]",
        "arrayfile": "array[file]",
    }
    allowed = {
        "string",
        "number",
        "boolean",
        "object",
        "array[string]",
        "array[number]",
        "array[object]",
        "array[boolean]",
        "array[file]",
        "file",
        "any",
    }
    return mapping.get(normalized, normalized if normalized in allowed else "string")


def _array_var_type(value: str) -> str:
    var_type = _var_type(value)
    if var_type.startswith("array["):
        return var_type
    return f"array[{var_type if var_type != 'any' else 'string'}]"


def _item_var_type(value: str, array_type: str) -> str:
    explicit = _var_type(value)
    if value and not explicit.startswith("array["):
        return explicit
    if array_type.startswith("array[") and array_type.endswith("]"):
        return array_type.removeprefix("array[").removesuffix("]")
    return "string"


def _normalize_list_filter(value: Any) -> dict[str, Any]:
    if not isinstance(value, dict):
        return {"enabled": False, "conditions": []}
    conditions = []
    for item in value.get("conditions") or []:
        if not isinstance(item, dict):
            continue
        condition = dict(item)
        condition["key"] = str(condition.get("key", ""))
        condition["comparison_operator"] = normalize_list_comparison_operator(
            condition.get("comparison_operator") or condition.get("operator") or "contains"
        )
        condition.setdefault("value", "")
        conditions.append(condition)
    return {"enabled": bool(value.get("enabled", False)), "conditions": conditions}


def _normalize_extract_by(value: Any) -> dict[str, Any]:
    if not isinstance(value, dict):
        return {"enabled": False, "serial": "1"}
    return {"enabled": bool(value.get("enabled", False)), "serial": str(value.get("serial") or value.get("index") or "1")}


def _normalize_order_by(value: Any) -> dict[str, Any]:
    if not isinstance(value, dict):
        return {"enabled": False, "key": "", "value": "asc"}
    order = str(value.get("value") or value.get("order") or "asc").lower()
    if order not in {"asc", "desc"}:
        order = "asc"
    key = value.get("key", "")
    if isinstance(key, str):
        normalized_key: str | list[str] = key
    elif isinstance(key, list):
        normalized_key = _normalize_selector(key)
    else:
        normalized_key = ""
    return {"enabled": bool(value.get("enabled", False)), "key": normalized_key, "value": order}


def _normalize_limit(value: Any) -> dict[str, Any]:
    if isinstance(value, int):
        return {"enabled": True, "size": max(1, value)}
    if not isinstance(value, dict):
        return {"enabled": False, "size": 10}
    size = value.get("size", 10)
    try:
        size_int = int(size)
    except (TypeError, ValueError):
        size_int = 10
    return {"enabled": bool(value.get("enabled", False)), "size": max(1, size_int)}


def _list_operator_needs_code_fallback(params: dict[str, Any]) -> bool:
    return params.get("var_type") == "array[object]" or params.get("item_var_type") == "object"


def _object_list_operator_code_params(params: dict[str, Any]) -> dict[str, Any]:
    filter_by = params.get("filter_by") if isinstance(params.get("filter_by"), dict) else {}
    order_by = params.get("order_by") if isinstance(params.get("order_by"), dict) else {}
    limit = params.get("limit") if isinstance(params.get("limit"), dict) else {}
    code = f"""def main(records: list) -> dict:
    conditions = {repr(filter_by.get("conditions") if filter_by.get("enabled") else [])}
    order_enabled = {repr(bool(order_by.get("enabled")))}
    order_key = {repr(order_by.get("key", ""))}
    order_desc = {repr(str(order_by.get("value") or "asc").lower() == "desc")}
    limit_enabled = {repr(bool(limit.get("enabled")))}
    limit_size = {repr(max(1, int(limit.get("size", 10))))}

    def get_value(item, key):
        current = item
        for part in str(key or "").split("."):
            if not part:
                continue
            if not isinstance(current, dict):
                return None
            current = current.get(part)
        return current

    def as_number(value):
        try:
            return float(value)
        except (TypeError, ValueError):
            return None

    def compare(actual, operator, expected):
        if operator in ("empty", "is null", "null"):
            return actual in (None, "", [], {{}})
        if operator in ("not empty", "is not null", "not null"):
            return actual not in (None, "", [], {{}})
        if operator == "contains":
            return str(expected) in str(actual or "")
        if operator == "not contains":
            return str(expected) not in str(actual or "")
        if operator == "start with":
            return str(actual or "").startswith(str(expected))
        if operator == "end with":
            return str(actual or "").endswith(str(expected))
        if operator in ("=", "is"):
            return actual == expected or str(actual) == str(expected)
        if operator in ("≠", "is not"):
            return not (actual == expected or str(actual) == str(expected))
        if operator in (">", "<", "≥", "≤"):
            left = as_number(actual)
            right = as_number(expected)
            if left is None or right is None:
                return False
            if operator == ">":
                return left > right
            if operator == "<":
                return left < right
            if operator == "≥":
                return left >= right
            return left <= right
        if operator in ("in", "not in"):
            values = expected if isinstance(expected, list) else [part.strip() for part in str(expected).split(",")]
            matched = actual in values or str(actual) in [str(value) for value in values]
            return matched if operator == "in" else not matched
        return True

    def matches(item):
        if not conditions:
            return True
        for condition in conditions:
            key = condition.get("key", "")
            actual = get_value(item, key) if key else item
            operator = condition.get("comparison_operator") or "contains"
            expected = condition.get("value", "")
            if not compare(actual, operator, expected):
                return False
        return True

    source = records if isinstance(records, list) else []
    result = [item for item in source if isinstance(item, dict) and matches(item)]
    if order_enabled and order_key:
        result.sort(key=lambda item: get_value(item, order_key) or "", reverse=order_desc)
    if limit_enabled:
        result = result[:limit_size]
    return {{
        "result": result,
        "first_record": result[0] if result else {{}},
        "last_record": result[-1] if result else {{}},
    }}
"""
    return {
        "code_language": "python3",
        "code": code,
        "variables": [{"variable": "records", "value_selector": params.get("variable"), "value_type": "array[object]"}],
        "outputs": {
            "result": {"type": "array[object]", "children": None},
            "first_record": {"type": "object", "children": None},
            "last_record": {"type": "object", "children": None},
        },
    }


def _parameter_type(value: str) -> str:
    normalized = value.strip().lower().replace("_", "-")
    mapping = {
        "str": "string",
        "text": "string",
        "paragraph": "string",
        "integer": "number",
        "int": "number",
        "float": "number",
        "bool": "boolean",
        "boolean": "boolean",
        "enum": "select",
        "choice": "select",
        "array-string": "array[string]",
        "array_string": "array[string]",
        "string[]": "array[string]",
        "array-number": "array[number]",
        "array_number": "array[number]",
        "number[]": "array[number]",
        "array-object": "array[object]",
        "array_object": "array[object]",
        "object[]": "array[object]",
        "array-boolean": "array[boolean]",
        "array_bool": "array[boolean]",
        "boolean[]": "array[boolean]",
    }
    return mapping.get(normalized, normalized if normalized in _allowed_parameter_types() else "string")


def _allowed_parameter_types() -> set[str]:
    return {
        "string",
        "number",
        "boolean",
        "select",
        "array[string]",
        "array[number]",
        "array[object]",
        "array[boolean]",
    }


def _safe_branch_id(value: str) -> str:
    safe = re.sub(r"[^A-Za-z0-9_-]", "_", str(value or "").strip())
    safe = re.sub(r"_+", "_", safe).strip("_")
    return safe


def _split_prompt_sections(text: str) -> tuple[str, str]:
    normalized = normalize_template_refs(str(text or "").strip())
    if not normalized:
        return "", ""

    explicit = _split_explicit_prompt_sections(normalized)
    if explicit:
        return explicit

    system_lines: list[str] = []
    user_lines: list[str] = []
    for raw_line in normalized.splitlines():
        line = raw_line.strip()
        if not line:
            continue
        if DIFY_REF_PATTERN.search(line) and _looks_like_system_instruction(line):
            mixed_system, mixed_user = _split_mixed_prompt_line(line)
            system_lines.extend(mixed_system)
            user_lines.extend(mixed_user)
        elif DIFY_REF_PATTERN.search(line):
            user_lines.append(line)
        elif _looks_like_system_instruction(line):
            system_lines.append(line)
        else:
            user_lines.append(line)

    if system_lines and user_lines:
        return "\n".join(system_lines), "\n".join(user_lines)
    return "", normalized


def _split_explicit_prompt_sections(text: str) -> tuple[str, str] | None:
    labels = [
        ("system", r"(?:system_prompt|system|系统提示词|系统)\s*[:：]"),
        ("user", r"(?:user_prompt|user|用户提示词|用户)\s*[:：]"),
    ]
    matches: list[tuple[int, int, str]] = []
    for section, pattern in labels:
        for match in re.finditer(pattern, text, flags=re.IGNORECASE):
            matches.append((match.start(), match.end(), section))
    matches.sort(key=lambda item: item[0])
    if len({section for _, _, section in matches}) < 2:
        return None

    sections: dict[str, list[str]] = {"system": [], "user": []}
    for idx, (_, content_start, section) in enumerate(matches):
        content_end = matches[idx + 1][0] if idx + 1 < len(matches) else len(text)
        content = text[content_start:content_end].strip()
        if content:
            sections[section].append(content)
    system_text = "\n".join(sections["system"]).strip()
    user_text = "\n".join(sections["user"]).strip()
    if system_text or user_text:
        return system_text, user_text
    return None


def _split_mixed_prompt_line(line: str) -> tuple[list[str], list[str]]:
    system_parts: list[str] = []
    user_parts: list[str] = []
    parts = [part.strip() for part in re.split(r"(?<=[。；;!！?？])\s*", line) if part.strip()]
    if not parts:
        return [], [line]

    for part in parts:
        if DIFY_REF_PATTERN.search(part):
            user_parts.append(part)
        elif _looks_like_system_instruction(part):
            system_parts.append(part)
        else:
            user_parts.append(part)
    if not system_parts or not user_parts:
        return [], [line]
    return system_parts, user_parts


def _looks_like_system_instruction(line: str) -> bool:
    lowered = line.lower()
    markers = (
        "你是",
        "角色",
        "身份",
        "规则",
        "输出格式",
        "审核",
        "标准",
        "不得",
        "禁止",
        "必须",
        "只输出",
        "不要",
        "保持",
        "tone",
        "role",
        "rules",
        "format",
        "criteria",
        "must",
        "never",
        "do not",
    )
    return any(marker in lowered or marker in line for marker in markers)


def _clean_prompt(text: str) -> str:
    return re.sub(r"\n{3,}", "\n\n", str(text or "").strip())


def _ensure_user_prompt(text: str, *, workflow_name: str) -> str:
    prompt = _clean_prompt(text)
    if DIFY_REF_PATTERN.search(normalize_template_refs(prompt)):
        return prompt
    subject = _workflow_subject(workflow_name)
    if prompt:
        return f"{prompt}\n\n请处理以下用户输入：{{{{#start.query#}}}}"
    return f"请根据以下用户输入完成{subject}任务：{{{{#start.query#}}}}"


def _default_system_prompt(workflow_name: str) -> str:
    subject = _workflow_subject(workflow_name)
    return (
        f"你是{subject}专员，负责根据用户输入生成专业、礼貌、可执行的回复。\n"
        "规则：先理解用户诉求，再给出清晰处理建议；不得编造订单、金额、门店或政策信息；"
        "遇到不确定信息时说明需要进一步核实。\n"
        "输出格式：用自然中文输出，结构清楚，语气友好。\n"
        "审核标准：回复必须贴合用户输入，不推卸责任，不承诺超出权限的赔付或处理结果。"
    )


def _semantic_title(
    node: dict[str, Any],
    data: dict[str, Any],
    node_by_id: dict[str, dict[str, Any]],
    edges: list[Any],
) -> str:
    node_type = str(node.get("type") or "")
    params = node.get("params", {}) if isinstance(node.get("params"), dict) else {}
    subject = _workflow_subject(str(data.get("name") or data.get("description") or "业务"))
    subject = subject[:16]

    match node_type:
        case "start":
            return f"接收{subject}诉求"
        case "llm":
            prompt_text = f"{params.get('system_prompt', '')}\n{params.get('user_prompt', '')}\n{node.get('id', '')}"
            branch = _branch_label_from_text(prompt_text)
            if branch:
                return f"生成{branch}回复"
            return f"生成{subject}回复"
        case "if-else":
            return f"判断{subject}类型"
        case "code":
            return f"处理{subject}数据"
        case "http-request":
            return f"调用{subject}接口"
        case "template-transform":
            return f"整理{subject}内容"
        case "question-classifier":
            return f"识别{subject}类型"
        case "parameter-extractor":
            return f"提取{subject}信息"
        case "variable-aggregator":
            return f"聚合{subject}变量"
        case "document-extractor":
            return f"提取{subject}文档文本"
        case "assigner":
            return f"更新{subject}变量"
        case "list-operator":
            return f"筛选{subject}列表"
        case "knowledge-retrieval":
            return f"检索{subject}知识库"
        case "human-input":
            return f"人工审核{subject}"
        case "iteration":
            return f"批量处理{subject}"
        case "loop":
            return f"循环检查{subject}"
        case "tool":
            tool_label = str(params.get("tool_label") or params.get("tool_name") or "").strip()
            if tool_label:
                return f"调用{tool_label}"
            return f"调用{subject}工具"
        case "iteration-start":
            return "开始遍历"
        case "loop-start":
            return "开始循环"
        case "loop-end":
            return "退出循环"
        case "end":
            upstream = _first_upstream(node, node_by_id, edges)
            if upstream and upstream.get("type") == "llm":
                upstream_title = str(upstream.get("title") or "")
                if not _is_generic_title(upstream_title, "llm") and upstream_title.startswith("生成"):
                    return upstream_title.replace("生成", "返回", 1).replace("回复", "结果")
            return f"返回{subject}结果"
    return f"{subject}节点"


def _first_upstream(
    node: dict[str, Any],
    node_by_id: dict[str, dict[str, Any]],
    edges: list[Any],
) -> dict[str, Any] | None:
    node_id = str(node.get("id") or "")
    for edge in edges:
        if isinstance(edge, dict) and str(edge.get("target") or "") == node_id:
            return node_by_id.get(str(edge.get("source") or ""))
    return None


def _branch_label_from_text(text: str) -> str:
    lowered = text.lower()
    mapping = [
        ("退款", "退款处理"),
        ("refund", "退款处理"),
        ("发票", "发票处理"),
        ("invoice", "发票处理"),
        ("投诉", "投诉安抚"),
        ("complaint", "投诉安抚"),
        ("售后", "售后服务"),
        ("after-sales", "售后服务"),
        ("urgent", "紧急问题"),
        ("紧急", "紧急问题"),
    ]
    for marker, label in mapping:
        if marker in lowered or marker in text:
            return label
    return ""


def _workflow_subject(value: str) -> str:
    text = re.sub(r"\s+", " ", str(value or "")).strip()
    text = re.sub(r"(?i)\b(workflow|flow|assistant|bot)\b", "", text)
    for suffix in ("工作流", "流程", "机器人", "助手", "自动化", "处理"):
        text = text.replace(suffix, "")
    text = text.strip(" -_：:，,。.")
    return text or "业务"


def _is_generic_title(title: str, node_type: str) -> bool:
    normalized = re.sub(r"[\s_\-]+", "", str(title or "").strip().lower())
    default = re.sub(r"[\s_\-]+", "", _default_title(node_type).lower())
    return normalized in GENERIC_TITLES or normalized == default


def _normalize_variables(items: Any, inputs: Any = None) -> list[dict[str, Any]]:
    variables = []
    for item in items or []:
        if not isinstance(item, dict):
            continue
        variable = item.get("variable")
        selector = item.get("value_selector")
        if variable and selector:
            variables.append(_normalize_output({"variable": str(variable), "value_selector": selector}))
    if isinstance(inputs, dict):
        for variable, ref in inputs.items():
            variables.append(_normalize_output({"variable": str(variable), "value_selector": _selector_from_ref(str(ref))}))
    return variables


def _normalize_key_value_text(value: Any) -> str:
    if value in (None, "", [], {}):
        return ""
    if isinstance(value, dict):
        lines = [
            f"{str(key).strip()}:{normalize_template_refs(str(item)).strip()}"
            for key, item in value.items()
            if str(key).strip() and str(item).strip()
        ]
        return "\n".join(lines)
    if isinstance(value, list):
        lines = []
        for item in value:
            if not isinstance(item, dict):
                continue
            key = str(item.get("key", "")).strip()
            raw_value = item.get("value", "")
            item_value = normalize_template_refs(str(raw_value)).strip()
            if key and item_value:
                lines.append(f"{key}:{item_value}")
        return "\n".join(lines)

    text = str(value).strip()
    if text in {"[]", "{}", "null", "None"}:
        return ""
    lines = []
    for line in normalize_template_refs(text).splitlines():
        key, separator, raw_value = line.partition(":")
        key = key.strip()
        item_value = raw_value.strip()
        if separator and key and item_value:
            lines.append(f"{key}:{item_value}")
    return "\n".join(lines)


def _add_template_ref_variables(template: str, variables: list[dict[str, Any]]) -> list[dict[str, Any]]:
    result = [dict(variable) for variable in variables]
    used_names = {str(item.get("variable")) for item in result if item.get("variable")}
    known_selectors = {
        tuple(item.get("value_selector", []))
        for item in result
        if isinstance(item.get("value_selector"), list) and item.get("value_selector")
    }
    for selector in _template_ref_selectors(template):
        selector_key = tuple(selector)
        if selector_key in known_selectors:
            continue
        variable = _unique_template_variable_name(selector, used_names)
        used_names.add(variable)
        known_selectors.add(selector_key)
        result.append({"variable": variable, "value_selector": selector, "value_type": "string"})
    return result


def _template_ref_selectors(template: str) -> list[list[str]]:
    selectors: list[list[str]] = []
    seen: set[tuple[str, str]] = set()
    for match in DIFY_REF_PATTERN.finditer(template):
        selector = (match.group(1), match.group(2))
        if selector in seen:
            continue
        seen.add(selector)
        selectors.append([selector[0], selector[1]])
    return selectors


def _unique_template_variable_name(selector: list[str], used_names: set[str]) -> str:
    preferred = _safe_variable_name(selector[-1] if selector else "value")
    candidates = [preferred, _safe_variable_name("_".join(selector))]
    for candidate in candidates:
        if candidate and candidate not in used_names:
            return candidate
    index = 2
    while f"{preferred}_{index}" in used_names:
        index += 1
    return f"{preferred}_{index}"


def _safe_variable_name(value: str) -> str:
    safe = re.sub(r"[^A-Za-z0-9_]", "_", value).strip("_")
    if not safe:
        return "value"
    if safe[0].isdigit():
        safe = f"var_{safe}"
    return safe


def _normalize_output(item: dict[str, Any]) -> dict[str, Any]:
    output = dict(item)
    if isinstance(output.get("value_selector"), str):
        output["value_selector"] = _selector_from_ref(output["value_selector"])
    output.setdefault("value_type", "string")
    return output


def _infer_code_outputs(code: str) -> dict[str, dict[str, str | None]]:
    names = []
    for match in re.finditer(r"['\"]([A-Za-z_][A-Za-z0-9_]*)['\"]\s*:", code):
        name = match.group(1)
        if name not in names:
            names.append(name)
    if not names:
        names = ["result"]
    return {name: {"type": _guess_code_output_type(name), "children": None} for name in names}


def _guess_code_output_type(name: str) -> str:
    lowered = name.lower()
    if any(marker in lowered for marker in ("count", "total", "amount", "price", "score", "number")):
        return "number"
    return "string"


def _condition_from_text(text: str) -> dict[str, Any]:
    normalized = normalize_template_refs(text)
    selector_match = re.search(r"\{\{#([A-Za-z0-9_-]+)\.([A-Za-z0-9_.-]+)#\}\}", normalized)
    selector = [selector_match.group(1), selector_match.group(2)] if selector_match else ["start", "query"]
    lowered = normalized.lower()
    if "not contains" in lowered or "不包含" in normalized:
        operator = "not contains"
    elif "contains" in lowered or "包含" in normalized:
        operator = "contains"
    elif "!=" in normalized or "not equal" in lowered:
        operator = "not equal"
    elif "==" in normalized or "=" in normalized or "equal" in lowered:
        operator = "is"
    else:
        operator = "not empty"

    value = ""
    quoted = re.search(r'["\']([^"\']+)["\']', normalized)
    if quoted:
        value = quoted.group(1)
    elif "包含" in normalized:
        value = normalized.rsplit("包含", 1)[-1].strip()

    return {
        "variable_selector": selector,
        "comparison_operator": operator,
        "value": value,
        "varType": "string",
    }


def _selector_from_ref(value: str) -> list[str]:
    normalized = normalize_template_refs(value)
    match = re.search(r"\{\{#([A-Za-z0-9_-]+)\.([A-Za-z0-9_.-]+)#\}\}", normalized)
    if match:
        return [match.group(1), *[piece for piece in match.group(2).split(".") if piece]]
    pieces = [piece for piece in value.split(".") if piece]
    return pieces if len(pieces) >= 2 else [value]


def _branch_edge_positions(
    edges: list[Any],
    node_by_id: dict[str, dict[str, Any]],
    node_type: str,
) -> dict[tuple[str, str], int]:
    positions: dict[tuple[str, str], int] = {}
    counters: dict[str, int] = {}
    for edge in edges:
        if not isinstance(edge, dict):
            continue
        source = node_by_id.get(str(edge.get("source")))
        if not source or source.get("type") != node_type:
            continue
        key = (str(edge.get("source")), str(edge.get("target")))
        positions[key] = counters.get(str(edge.get("source")), 0)
        counters[str(edge.get("source"))] = positions[key] + 1
    return positions


def _infer_if_else_source_handle(
    source: dict[str, Any],
    edge: dict[str, Any],
    node_by_id: dict[str, dict[str, Any]],
    positions: dict[tuple[str, str], int],
) -> str:
    params = source.get("params", {})
    case_ids = [str(case.get("case_id")) for case in params.get("cases", []) if case.get("case_id")]
    valid = {*case_ids, FALSE_HANDLE}
    current = str(edge.get("source_handle", SOURCE_HANDLE))
    if current in valid:
        return current

    target = node_by_id.get(str(edge.get("target")), {})
    target_text = f"{edge.get('target')} {target.get('title') or ''}".lower()
    else_case = str(params.get("else_case", FALSE_HANDLE)).lower()
    for case_id in case_ids:
        if case_id.lower() in target_text:
            return case_id
    if else_case and else_case in target_text:
        return FALSE_HANDLE
    if any(marker in target_text for marker in ("general", "default", "else", "other", "fallback")):
        return FALSE_HANDLE
    branch_ids = [*case_ids, FALSE_HANDLE]
    position = positions.get((str(edge.get("source")), str(edge.get("target"))), 0)
    return branch_ids[position] if position < len(branch_ids) else FALSE_HANDLE


def _infer_question_classifier_source_handle(
    source: dict[str, Any],
    edge: dict[str, Any],
    node_by_id: dict[str, dict[str, Any]],
    positions: dict[tuple[str, str], int],
) -> str:
    classes = source.get("params", {}).get("classes", [])
    class_ids = [str(item.get("id")) for item in classes if isinstance(item, dict) and item.get("id")]
    valid = set(class_ids)
    current = str(edge.get("source_handle", SOURCE_HANDLE))
    if current in valid:
        return current

    target = node_by_id.get(str(edge.get("target")), {})
    target_text = f"{edge.get('target')} {target.get('title') or ''}".lower()
    for item in classes:
        if not isinstance(item, dict):
            continue
        class_id = str(item.get("id") or "")
        class_name = str(item.get("name") or "")
        if class_id and (class_id.lower() in target_text or class_name.lower() in target_text or class_name in target_text):
            return class_id
    position = positions.get((str(edge.get("source")), str(edge.get("target"))), 0)
    return class_ids[position] if position < len(class_ids) else current


def _infer_human_input_source_handle(
    source: dict[str, Any],
    edge: dict[str, Any],
    node_by_id: dict[str, dict[str, Any]],
    positions: dict[tuple[str, str], int],
) -> str:
    actions = source.get("params", {}).get("user_actions", [])
    action_ids = [str(item.get("id")) for item in actions if isinstance(item, dict) and item.get("id")]
    valid = set(action_ids)
    current = str(edge.get("source_handle", SOURCE_HANDLE))
    if current in valid:
        return current

    target = node_by_id.get(str(edge.get("target")), {})
    target_text = f"{edge.get('target')} {target.get('title') or ''}".lower()
    for item in actions:
        if not isinstance(item, dict):
            continue
        action_id = str(item.get("id") or "")
        title = str(item.get("title") or "")
        if action_id and (action_id.lower() in target_text or title.lower() in target_text or title in target_text):
            return action_id
    position = positions.get((str(edge.get("source")), str(edge.get("target"))), 0)
    return action_ids[position] if position < len(action_ids) else current


def _input_type(value: str) -> str:
    normalized = value.replace("_", "-")
    mapping = {
        "text": "text-input",
        "string": "paragraph",
        "paragraph": "paragraph",
        "number": "number",
        "integer": "number",
        "boolean": "checkbox",
        "file": "file",
        "image": "file",
        "file-list": "file-list",
        "files": "file-list",
        "json": "json",
        "json-object": "json",
    }
    return mapping.get(normalized, "paragraph")


def _default_title(node_type: str) -> str:
    if not node_type:
        return "Node"
    return re.sub(r"(^|-)([a-z])", lambda m: (" " if m.group(1) else "") + m.group(2).upper(), node_type)
