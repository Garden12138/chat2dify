from __future__ import annotations

import re
from copy import deepcopy
from dataclasses import dataclass, field
from typing import Any, Iterable


TEMPLATE_REF_RE = re.compile(
    r"\{\{\s*#([A-Za-z0-9_-]+)\.([A-Za-z0-9_.-]+)#\s*\}\}"
)
SAFE_OUTPUT_ALIASES = {
    "llm": "text",
    "template-transform": "output",
    "variable-aggregator": "output",
    "iteration": "output",
    "knowledge-retrieval": "result",
    "document-extractor": "text",
}
SYSTEM_OUTPUT_TYPES = {
    "app_id": "string",
    "batch": "string",
    "conversation_id": "string",
    "dataset_id": "string",
    "datasource_info": "object",
    "datasource_type": "string",
    "dialogue_count": "number",
    "document_id": "string",
    "files": "array[file]",
    "invoke_from": "string",
    "original_document_id": "string",
    "query": "string",
    "timestamp": "number",
    "user_id": "string",
    "workflow_id": "string",
    "workflow_run_id": "string",
}


@dataclass(frozen=True)
class ReferenceRepairResult:
    payload: dict[str, Any]
    actions: list[dict[str, Any]] = field(default_factory=list)


def node_output_types(node_type: str, params: dict[str, Any]) -> dict[str, str]:
    match node_type:
        case "start":
            return {
                str(item.get("name") or item.get("variable")): input_variable_type(
                    str(item.get("type") or "paragraph")
                )
                for item in params.get("variables") or params.get("inputs") or []
                if isinstance(item, dict)
                and (item.get("name") or item.get("variable"))
            }
        case "trigger-webhook":
            raw = (
                params.get("_raw_data")
                if isinstance(params.get("_raw_data"), dict)
                else params
            )
            variables = (
                raw.get("variables")
                if isinstance(raw.get("variables"), list)
                else []
            )
            result = {
                str(item.get("variable") or item.get("name")): str(
                    item.get("value_type") or item.get("type") or "string"
                )
                for item in variables
                if isinstance(item, dict)
                and (item.get("variable") or item.get("name"))
            }
            if result:
                return result
            result = {"_webhook_raw": "object"}
            for group in ("headers", "params", "body"):
                for item in raw.get(group) or []:
                    if not isinstance(item, dict) or not item.get("name"):
                        continue
                    name = str(item["name"])
                    if group == "headers":
                        name = name.replace("-", "_")
                    result[name] = str(item.get("type") or "string")
            return result
        case "trigger-plugin":
            return schema_output_types(params)
        case "trigger-schedule":
            return {}
        case "llm":
            return {"text": "string"}
        case "code":
            outputs = params.get("outputs") if isinstance(params.get("outputs"), dict) else {}
            return {
                str(name): str(config.get("type") or "string")
                if isinstance(config, dict)
                else "string"
                for name, config in outputs.items()
            }
        case "http-request":
            return {"body": "string", "status_code": "number", "headers": "object"}
        case "template-transform":
            return {"output": "string"}
        case "question-classifier":
            return {}
        case "parameter-extractor":
            result = {
                str(item.get("name")): str(item.get("type") or "string")
                for item in params.get("parameters") or []
                if isinstance(item, dict) and item.get("name")
            }
            result.update(
                {
                    "__is_success": "boolean",
                    "__reason": "string",
                    "__usage": "object",
                }
            )
            return result
        case "variable-aggregator":
            return {"output": str(params.get("output_type") or "string")}
        case "document-extractor":
            return {"text": "string"}
        case "list-operator":
            return {
                "result": str(params.get("var_type") or "array"),
                "first_record": str(params.get("item_var_type") or "string"),
                "last_record": str(params.get("item_var_type") or "string"),
            }
        case "knowledge-retrieval":
            return {"result": "array[object]"}
        case "human-input":
            result = {
                str(item.get("output_variable_name")): str(item.get("type") or "string")
                for item in params.get("inputs") or []
                if isinstance(item, dict) and item.get("output_variable_name")
            }
            result.update(
                {
                    "selected_action": "string",
                    "submitted_at": "string",
                    "__action_id": "string",
                    "__action_value": "string",
                    "__rendered_content": "string",
                }
            )
            return result
        case "tool" | "agent":
            return {
                "text": "string",
                "files": "array[file]",
                "json": "object",
                **schema_output_types(params),
            }
        case "datasource" | "datasource-empty":
            return {
                "datasource_type": "string",
                "file": "file",
                **schema_output_types(params),
            }
        case "knowledge-index":
            return {
                "result": "object",
                "document_ids": "array[string]",
                **schema_output_types(params),
            }
        case "iteration":
            return {
                "output": str(params.get("output_type") or "array"),
                "item": _iteration_item_type(
                    str(params.get("iterator_input_type") or "array[object]")
                ),
                "index": "number",
            }
        case "loop":
            result = {"loop_round": "number"}
            for item in params.get("loop_variables") or []:
                if isinstance(item, dict) and item.get("label"):
                    result[str(item["label"])] = str(
                        item.get("var_type") or item.get("type") or "string"
                    )
            return result
    return {}


def plan_output_types(plan: Any) -> dict[tuple[str, str], str]:
    result = {
        ("sys", name): value_type
        for name, value_type in SYSTEM_OUTPUT_TYPES.items()
    }
    for variable in getattr(plan, "conversation_variables", []) or []:
        result[("conversation", str(variable.name))] = str(variable.value_type)

    def register(node: Any) -> None:
        node_id = str(getattr(node, "id", "") or node.get("id"))
        node_type = str(getattr(node, "type", "") or node.get("type"))
        params = getattr(node, "params", None)
        if not isinstance(params, dict):
            params = node.get("params") if isinstance(node, dict) else {}
        for name, value_type in node_output_types(node_type, params).items():
            result[(node_id, name)] = value_type
        if (
            node_type == "start"
            and getattr(plan, "app_mode", "workflow") == "advanced-chat"
        ):
            result[(node_id, "sys.query")] = "string"
            result[(node_id, "sys.files")] = "array[file]"
        for child in params.get("children") or []:
            if isinstance(child, dict):
                register(child)

    for node in getattr(plan, "nodes", []) or []:
        register(node)
    return result


def known_outputs(plan: Any) -> dict[str, set[str]]:
    result: dict[str, set[str]] = {}
    def register(node: Any) -> None:
        node_id = str(getattr(node, "id", "") or node.get("id"))
        result.setdefault(node_id, set())
        params = getattr(node, "params", None)
        if not isinstance(params, dict):
            params = node.get("params") if isinstance(node, dict) else {}
        for child in params.get("children") or []:
            if isinstance(child, dict):
                register(child)

    for node in getattr(plan, "nodes", []) or []:
        register(node)
    for (node_id, output_name), _value_type in plan_output_types(plan).items():
        result.setdefault(node_id, set()).add(output_name)
    return result


def output_catalog(plan: Any) -> dict[str, list[dict[str, str]]]:
    result: dict[str, list[dict[str, str]]] = {
        node_id: []
        for node_id in known_outputs(plan)
    }
    for (node_id, output_name), value_type in sorted(plan_output_types(plan).items()):
        result.setdefault(node_id, []).append(
            {"name": output_name, "type": value_type}
        )
    return result


def repair_plan_references(payload: dict[str, Any]) -> ReferenceRepairResult:
    data = deepcopy(payload)
    top_level_nodes = [
        node
        for node in data.get("nodes") or []
        if isinstance(node, dict)
    ]
    nodes = list(_payload_nodes(top_level_nodes))
    node_by_id = {
        str(node.get("id")): node
        for node in nodes
        if isinstance(node, dict) and node.get("id")
    }
    actions: list[dict[str, Any]] = []

    for node in top_level_nodes:
        params = node.get("params") if isinstance(node.get("params"), dict) else {}
        node_id = str(node.get("id") or "")
        node["params"] = _repair_value(
            params,
            node_by_id=node_by_id,
            actions=actions,
            path=f"nodes.{node_id}.params",
        )
    return ReferenceRepairResult(payload=data, actions=actions)


def input_variable_type(value: str) -> str:
    normalized = str(value or "").strip().lower().replace("_", "-")
    return {
        "text": "string",
        "text-input": "string",
        "string": "string",
        "paragraph": "string",
        "number": "number",
        "integer": "number",
        "boolean": "boolean",
        "checkbox": "boolean",
        "json": "object",
        "json-object": "object",
        "json_object": "object",
        "file": "file",
        "image": "file",
        "file-list": "array[file]",
        "files": "array[file]",
    }.get(normalized, "string")


def schema_output_types(params: dict[str, Any]) -> dict[str, str]:
    raw_data = params.get("_raw_data") if isinstance(params.get("_raw_data"), dict) else params
    schema = raw_data.get("output_schema") if isinstance(raw_data, dict) else None
    properties = (
        schema.get("properties")
        if isinstance(schema, dict) and isinstance(schema.get("properties"), dict)
        else {}
    )
    return {
        str(name): str(config.get("type") or "object")
        if isinstance(config, dict)
        else "object"
        for name, config in properties.items()
    }


def _payload_nodes(nodes: Iterable[Any]) -> Iterable[dict[str, Any]]:
    for node in nodes:
        if not isinstance(node, dict):
            continue
        yield node
        params = node.get("params") if isinstance(node.get("params"), dict) else {}
        yield from _payload_nodes(params.get("children") or [])


def _repair_value(
    value: Any,
    *,
    node_by_id: dict[str, dict[str, Any]],
    actions: list[dict[str, Any]],
    path: str,
) -> Any:
    if isinstance(value, str):
        return TEMPLATE_REF_RE.sub(
            lambda match: _repair_template_match(
                match,
                node_by_id=node_by_id,
                actions=actions,
                path=path,
            ),
            value,
        )
    if isinstance(value, list):
        result = [
            _repair_value(
                item,
                node_by_id=node_by_id,
                actions=actions,
                path=f"{path}.{index}",
            )
            for index, item in enumerate(value)
        ]
        if len(result) >= 2 and isinstance(result[0], str) and isinstance(result[1], str):
            replacement = _safe_replacement(
                result[0],
                result[1],
                node_by_id=node_by_id,
            )
            if replacement and replacement != result[1]:
                old = result[1]
                result[1] = replacement
                actions.append(
                    {
                        "code": "SAFE_OUTPUT_ALIAS_REPAIRED",
                        "path": path,
                        "from": [result[0], old],
                        "to": [result[0], replacement],
                    }
                )
        return result
    if isinstance(value, dict):
        return {
            key: child
            if key == "_raw_data"
            else _repair_value(
                child,
                node_by_id=node_by_id,
                actions=actions,
                path=f"{path}.{key}",
            )
            for key, child in value.items()
        }
    return value


def _repair_template_match(
    match: re.Match[str],
    *,
    node_by_id: dict[str, dict[str, Any]],
    actions: list[dict[str, Any]],
    path: str,
) -> str:
    node_id, output_name = match.group(1), match.group(2)
    replacement = _safe_replacement(
        node_id,
        output_name,
        node_by_id=node_by_id,
    )
    if not replacement or replacement == output_name:
        return match.group(0)
    repaired = f"{{{{#{node_id}.{replacement}#}}}}"
    actions.append(
        {
            "code": "SAFE_OUTPUT_ALIAS_REPAIRED",
            "path": path,
            "from": match.group(0),
            "to": repaired,
        }
    )
    return repaired


def _safe_replacement(
    node_id: str,
    output_name: str,
    *,
    node_by_id: dict[str, dict[str, Any]],
) -> str | None:
    node = node_by_id.get(node_id)
    if node is None:
        return None
    node_type = str(node.get("type") or "")
    canonical = SAFE_OUTPUT_ALIASES.get(node_type)
    if not canonical:
        return None
    outputs = node_output_types(
        node_type,
        node.get("params") if isinstance(node.get("params"), dict) else {},
    )
    if output_name in outputs or canonical not in outputs:
        return None
    return canonical


def _iteration_item_type(iterator_type: str) -> str:
    if iterator_type.startswith("array[") and iterator_type.endswith("]"):
        return iterator_type[6:-1]
    return "object"
