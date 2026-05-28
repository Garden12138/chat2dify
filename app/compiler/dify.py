from __future__ import annotations

import re
from copy import deepcopy
from uuid import uuid4
from typing import Any

import yaml

from app.models import PlanNode, WorkflowPlan


CUSTOM_NODE_TYPE = "custom"
SOURCE_HANDLE = "source"
TARGET_HANDLE = "target"
NODE_WIDTH_X_OFFSET = 300
START_X = 80
START_Y = 282


class DifyDslCompiler:
    def __init__(
        self,
        *,
        dsl_version: str,
        default_model_provider: str,
        default_model_name: str,
    ) -> None:
        self.dsl_version = dsl_version
        self.default_model_provider = default_model_provider
        self.default_model_name = default_model_name

    def compile(self, plan: WorkflowPlan) -> str:
        nodes = [self._compile_node(node, index) for index, node in enumerate(plan.nodes)]
        plan_nodes = {node.id: node for node in plan.nodes}
        type_by_id = {node["id"]: node["data"]["type"] for node in nodes}
        if_else_edge_positions = _if_else_edge_positions(plan)
        edges = [
            {
                "id": f"{edge.source}-{_source_handle(plan_nodes, if_else_edge_positions, edge)}-{edge.target}-{edge.target_handle}",
                "type": "custom",
                "source": edge.source,
                "target": edge.target,
                "sourceHandle": _source_handle(plan_nodes, if_else_edge_positions, edge),
                "targetHandle": edge.target_handle,
                "data": {
                    "isInIteration": False,
                    "isInLoop": False,
                    "sourceType": type_by_id[edge.source],
                    "targetType": type_by_id[edge.target],
                },
                "zIndex": 0,
            }
            for edge in plan.edges
        ]

        data = {
            "version": self.dsl_version,
            "kind": "app",
            "app": {
                "name": plan.name,
                "mode": "workflow",
                "icon": "🤖",
                "icon_type": "emoji",
                "icon_background": "#FFEAD5",
                "description": plan.description,
                "use_icon_as_answer_icon": False,
            },
            "dependencies": [],
            "workflow": {
                "conversation_variables": [],
                "environment_variables": [],
                "features": _default_features(),
                "graph": {
                    "edges": edges,
                    "nodes": nodes,
                    "viewport": {"x": 0, "y": 0, "zoom": 0.7},
                },
            },
        }
        return yaml.safe_dump(data, allow_unicode=True, sort_keys=False)

    def _compile_node(self, node: PlanNode, index: int) -> dict[str, Any]:
        position = {"x": START_X + index * NODE_WIDTH_X_OFFSET, "y": START_Y}
        data = {
            "title": node.title or _default_title(node.type),
            "desc": node.desc,
            "selected": False,
            "type": node.type,
        }
        match node.type:
            case "start":
                data.update(self._start_data(node))
            case "llm":
                data.update(self._llm_data(node))
            case "code":
                data.update(self._code_data(node))
            case "if-else":
                data.update(self._if_else_data(node))
            case "end":
                data.update(self._end_data(node))
            case "http-request":
                data.update(self._http_request_data(node))
            case "template-transform":
                data.update(self._template_transform_data(node))

        return {
            "id": node.id,
            "type": CUSTOM_NODE_TYPE,
            "position": position,
            "positionAbsolute": position.copy(),
            "height": _node_height(node.type, data),
            "width": 244,
            "selected": False,
            "targetPosition": "left",
            "sourcePosition": "right",
            "data": data,
        }

    def _start_data(self, node: PlanNode) -> dict[str, Any]:
        variables = []
        for item in node.params.get("variables") or node.params.get("inputs", []):
            name = item.get("name") or item.get("variable")
            if not name:
                continue
            variables.append(
                {
                    "variable": name,
                    "label": item.get("label") or name,
                    "type": _input_type(item.get("type", "paragraph")),
                    "required": bool(item.get("required", True)),
                    "max_length": item.get("max_length", 1000),
                    "options": item.get("options", []),
                }
            )
        if not variables:
            variables.append(
                {
                    "variable": "query",
                    "label": "Query",
                    "type": "paragraph",
                    "required": True,
                    "max_length": 1000,
                    "options": [],
                }
            )
        return {"variables": variables}

    def _llm_data(self, node: PlanNode) -> dict[str, Any]:
        provider = node.params.get("model_provider") or self.default_model_provider
        name = node.params.get("model_name") or self.default_model_name
        system_prompt = node.params.get("system_prompt", "")
        user_prompt = node.params.get("user_prompt") or node.params.get("prompt") or "{{#start.query#}}"
        return {
            "model": {
                "provider": provider,
                "name": name,
                "mode": node.params.get("model_mode", "chat"),
                "completion_params": node.params.get("completion_params", {"temperature": 0.7}),
            },
            "prompt_template": [
                {"role": "system", "text": _normalize_template_refs(system_prompt)},
                {"role": "user", "text": _normalize_template_refs(user_prompt)},
            ],
            "variables": [],
            "context": {"enabled": False, "variable_selector": []},
            "vision": {"enabled": False, "configs": {"variable_selector": []}},
            "memory": {"enabled": False, "window": {"enabled": False, "size": 50}},
            "structured_output": {"enabled": False},
            "retry_config": {
                "enabled": False,
                "max_retries": 1,
                "retry_interval": 1000,
                "exponential_backoff": {"enabled": False, "multiplier": 2, "max_interval": 10000},
            },
        }

    def _code_data(self, node: PlanNode) -> dict[str, Any]:
        outputs = node.params.get("outputs") or {"result": {"type": "string", "children": None}}
        return {
            "code": node.params.get("code", "def main(query: str) -> dict:\n    return {\"result\": query}\n"),
            "code_language": node.params.get("code_language", "python3"),
            "variables": _variables(node.params.get("variables", [])),
            "outputs": outputs,
        }

    def _if_else_data(self, node: PlanNode) -> dict[str, Any]:
        cases = node.params.get("cases") or [
            {
                "case_id": "true",
                "id": "true",
                "logical_operator": "and",
                "conditions": [
                    {
                        "id": str(uuid4()),
                        "variable_selector": node.params.get("variable_selector", ["start", "query"]),
                        "comparison_operator": "not empty",
                        "value": "",
                        "varType": "string",
                    }
                ],
            }
        ]
        normalized_cases = []
        for idx, case in enumerate(cases):
            case_copy = dict(case)
            case_copy.setdefault("case_id", case_copy.get("id") or ("true" if idx == 0 else str(uuid4())))
            case_copy.setdefault("id", case_copy["case_id"])
            case_copy.setdefault("logical_operator", "and")
            normalized_conditions = []
            raw_conditions = case_copy.get("conditions")
            if not raw_conditions and case_copy.get("condition"):
                raw_conditions = [_condition_from_text(str(case_copy["condition"]))]
            for condition in raw_conditions or []:
                condition_copy = dict(condition)
                condition_copy.setdefault("id", str(uuid4()))
                condition_copy.setdefault("varType", "string")
                normalized_conditions.append(condition_copy)
            case_copy["conditions"] = normalized_conditions
            normalized_cases.append(case_copy)
        return {"cases": normalized_cases}

    def _end_data(self, node: PlanNode) -> dict[str, Any]:
        outputs = node.params.get("outputs") or [
            {"variable": "answer", "value_selector": ["llm", "text"], "value_type": "string"}
        ]
        return {"outputs": [_normalize_output(output) for output in outputs]}

    def _http_request_data(self, node: PlanNode) -> dict[str, Any]:
        return {
            "variables": _variables(node.params.get("variables", [])),
            "method": str(node.params.get("method", "GET")).upper(),
            "url": _normalize_template_refs(node.params.get("url", "https://example.com")),
            "authorization": {"type": "no-auth"},
            "headers": node.params.get("headers", ""),
            "params": node.params.get("params", ""),
            "body": node.params.get("body", {"type": "none", "data": ""}),
            "ssl_verify": node.params.get("ssl_verify", True),
            "timeout": node.params.get(
                "timeout",
                {"connect": 10, "read": 30, "write": 30},
            ),
            "retry_config": node.params.get(
                "retry_config",
                {
                    "enabled": False,
                    "max_retries": 1,
                    "retry_interval": 1000,
                    "exponential_backoff": {"enabled": False, "multiplier": 2, "max_interval": 10000},
                },
            ),
        }

    def _template_transform_data(self, node: PlanNode) -> dict[str, Any]:
        return {
            "template": _normalize_template_refs(node.params.get("template", "{{ query }}")),
            "variables": _variables(node.params.get("variables", [])),
        }


def _variables(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    variables = []
    for item in items:
        variable = item.get("variable")
        selector = item.get("value_selector")
        if variable and selector:
            variables.append(_normalize_output({"variable": variable, "value_selector": selector}))
    return variables


def _normalize_output(item: dict[str, Any]) -> dict[str, Any]:
    output = dict(item)
    output.setdefault("value_type", "string")
    return output


def _input_type(value: str) -> str:
    normalized = value.replace("_", "-")
    mapping = {
        "text": "text-input",
        "string": "text-input",
        "paragraph": "paragraph",
        "number": "number",
        "integer": "number",
        "boolean": "checkbox",
        "file": "file",
        "image": "file",
        "file-list": "file-list",
        "files": "file-list",
        "json": "json",
    }
    return mapping.get(normalized, "paragraph")


def _default_title(node_type: str) -> str:
    return re.sub(r"(^|-)([a-z])", lambda m: (" " if m.group(1) else "") + m.group(2).upper(), node_type)


def _normalize_template_refs(text: str) -> str:
    return re.sub(
        r"\{\{\s*(?!#)([A-Za-z0-9_-]+)\.([A-Za-z0-9_.-]+)\s*\}\}",
        r"{{#\1.\2#}}",
        text,
    )


def _condition_from_text(text: str) -> dict[str, Any]:
    normalized = _normalize_template_refs(text)
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


def _if_else_edge_positions(plan: WorkflowPlan) -> dict[tuple[str, str], int]:
    positions: dict[tuple[str, str], int] = {}
    counters: dict[str, int] = {}
    node_types = {node.id: node.type for node in plan.nodes}
    for edge in plan.edges:
        if node_types.get(edge.source) != "if-else":
            continue
        key = (edge.source, edge.target)
        positions[key] = counters.get(edge.source, 0)
        counters[edge.source] = positions[key] + 1
    return positions


def _source_handle(
    plan_nodes: dict[str, PlanNode],
    if_else_edge_positions: dict[tuple[str, str], int],
    edge: Any,
) -> str:
    source = plan_nodes.get(edge.source)
    if not source or source.type != "if-else" or edge.source_handle != SOURCE_HANDLE:
        return edge.source_handle

    target = plan_nodes.get(edge.target)
    target_text = f"{edge.target} {(target.title or '') if target else ''}".lower()
    case_ids = [str(case.get("case_id") or case.get("id")) for case in source.params.get("cases", [])]
    else_case = str(source.params.get("else_case", "false")).lower()
    for case_id in case_ids:
        if case_id and case_id.lower() in target_text:
            return case_id
    if else_case and else_case in target_text:
        return "false"
    if any(marker in target_text for marker in ("general", "default", "else", "other", "fallback")):
        return "false"

    branch_ids = [*case_ids, "false"]
    position = if_else_edge_positions.get((edge.source, edge.target), 0)
    return branch_ids[position] if position < len(branch_ids) else "false"


def _node_height(node_type: str, data: dict[str, Any]) -> int:
    if node_type == "template-transform":
        return 54
    if node_type == "if-else":
        return 126
    if node_type == "end":
        return 90 + max(0, len(data.get("outputs", [])) - 1) * 26
    return 90


def _default_features() -> dict[str, Any]:
    return deepcopy(
        {
            "file_upload": {
                "allowed_file_extensions": [".JPG", ".JPEG", ".PNG", ".GIF", ".WEBP", ".SVG"],
                "allowed_file_types": ["image"],
                "allowed_file_upload_methods": ["local_file", "remote_url"],
                "enabled": False,
                "fileUploadConfig": {
                    "audio_file_size_limit": 50,
                    "batch_count_limit": 5,
                    "file_size_limit": 15,
                    "image_file_size_limit": 10,
                    "video_file_size_limit": 100,
                    "workflow_file_upload_limit": 10,
                },
                "image": {"enabled": False, "number_limits": 3, "transfer_methods": ["local_file", "remote_url"]},
                "number_limits": 3,
            },
            "opening_statement": "",
            "retriever_resource": {"enabled": True},
            "sensitive_word_avoidance": {"enabled": False},
            "speech_to_text": {"enabled": False},
            "suggested_questions": [],
            "suggested_questions_after_answer": {"enabled": False},
            "text_to_speech": {"enabled": False, "language": "", "voice": ""},
        }
    )
