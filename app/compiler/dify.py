from __future__ import annotations

import re
from copy import deepcopy
from uuid import uuid4
from typing import Any

import yaml

from app.models import PlanNode, WorkflowPlan
from app.agent.normalizer import normalize_template_refs
from app.input_variables import file_upload_settings, is_file_input_type
from app.list_operator import normalize_list_comparison_operator, normalize_list_variable_selector


CUSTOM_NODE_TYPE = "custom"
SOURCE_HANDLE = "source"
TARGET_HANDLE = "target"
NODE_WIDTH_X_OFFSET = 300
START_X = 80
START_Y = 282
DIFY_REF_PATTERN = re.compile(r"\{\{\s*#([A-Za-z0-9_-]+)\.([A-Za-z0-9_.-]+)#\s*\}\}")


class DifyDslCompiler:
    def __init__(
        self,
        *,
        dsl_version: str,
        default_model_provider: str,
        default_model_name: str,
        default_dataset_ids: list[str] | None = None,
    ) -> None:
        self.dsl_version = dsl_version
        self.default_model_provider = default_model_provider
        self.default_model_name = default_model_name
        self.default_dataset_ids = default_dataset_ids or []

    def compile(self, plan: WorkflowPlan) -> str:
        nodes = [self._compile_node(node, index) for index, node in enumerate(plan.nodes)]
        type_by_id = {node["id"]: node["data"]["type"] for node in nodes}
        edges = [
            {
                "id": f"{edge.source}-{edge.source_handle}-{edge.target}-{edge.target_handle}",
                "type": "custom",
                "source": edge.source,
                "target": edge.target,
                "sourceHandle": edge.source_handle,
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
            case "question-classifier":
                data.update(self._question_classifier_data(node))
            case "parameter-extractor":
                data.update(self._parameter_extractor_data(node))
            case "variable-aggregator":
                data.update(self._variable_aggregator_data(node))
            case "document-extractor":
                data.update(self._document_extractor_data(node))
            case "assigner":
                data.update(self._assigner_data(node))
            case "list-operator":
                data.update(self._list_operator_data(node))
            case "knowledge-retrieval":
                data.update(self._knowledge_retrieval_data(node))
            case "human-input":
                data.update(self._human_input_data(node))

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
        for item in node.params.get("variables", []):
            name = item.get("name") or item.get("variable")
            if not name:
                continue
            input_type = _input_type(item.get("type", "paragraph"))
            variable = {
                "variable": name,
                "label": item.get("label") or name,
                "type": input_type,
                "required": bool(item.get("required", True)),
                "max_length": item.get("max_length", 1000),
                "options": item.get("options", []),
            }
            if input_type == "json_object" and item.get("json_schema") is not None:
                variable["json_schema"] = item.get("json_schema")
            if is_file_input_type(input_type):
                variable.update(file_upload_settings(item, input_type=input_type))
            variables.append(
                variable
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
        user_prompt = node.params.get("user_prompt") or "{{#start.query#}}"
        return {
            "model": {
                "provider": provider,
                "name": name,
                "mode": node.params.get("model_mode", "chat"),
                "completion_params": node.params.get("completion_params", {"temperature": 0.7}),
            },
            "prompt_template": [
                {"role": "system", "text": normalize_template_refs(system_prompt)},
                {"role": "user", "text": normalize_template_refs(user_prompt)},
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
            case_copy.setdefault("case_id", "true" if idx == 0 else str(uuid4()))
            case_copy.setdefault("logical_operator", "and")
            normalized_conditions = []
            for condition in case_copy.get("conditions", []):
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
            "url": normalize_template_refs(node.params.get("url", "https://example.com")),
            "authorization": {"type": "no-auth"},
            "headers": _key_value_text(node.params.get("headers", "")),
            "params": _key_value_text(node.params.get("params", "")),
            "body": _http_body(node.params.get("body")),
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
        variables = _variables(node.params.get("variables", []))
        return {
            "template": _jinja_template(node.params.get("template", "{{ query }}"), variables),
            "variables": variables,
        }

    def _question_classifier_data(self, node: PlanNode) -> dict[str, Any]:
        classes = _classifier_classes(node.params.get("classes", []))
        return {
            "query_variable_selector": _selector(node.params.get("query_variable_selector"), ["start", "query"]),
            "model": self._model_config(node),
            "classes": classes,
            "_targetBranches": [{"id": item["id"], "name": item["name"]} for item in classes],
            "instruction": normalize_template_refs(str(node.params.get("instruction", ""))),
            "vision": _vision(node.params.get("vision")),
            "memory": node.params.get("memory"),
        }

    def _parameter_extractor_data(self, node: PlanNode) -> dict[str, Any]:
        return {
            "query": _selector(node.params.get("query"), ["start", "query"]),
            "model": self._model_config(node),
            "parameters": _extractor_parameters(node.params.get("parameters", [])),
            "instruction": normalize_template_refs(str(node.params.get("instruction", ""))),
            "reasoning_mode": node.params.get("reasoning_mode", "prompt"),
            "vision": _vision(node.params.get("vision")),
            "memory": node.params.get("memory"),
        }

    def _variable_aggregator_data(self, node: PlanNode) -> dict[str, Any]:
        advanced = node.params.get("advanced_settings") if isinstance(node.params.get("advanced_settings"), dict) else {}
        return {
            "variables": _selector_list(node.params.get("variables", [])),
            "output_type": node.params.get("output_type", "string"),
            "advanced_settings": {
                "group_enabled": bool(advanced.get("group_enabled", False)),
                "groups": deepcopy(advanced.get("groups") or []),
            },
        }

    def _document_extractor_data(self, node: PlanNode) -> dict[str, Any]:
        return {
            "variable_selector": _selector(node.params.get("variable_selector"), ["start", "files"]),
            "is_array_file": bool(node.params.get("is_array_file", False)),
        }

    def _assigner_data(self, node: PlanNode) -> dict[str, Any]:
        return {
            "version": str(node.params.get("version") or "2"),
            "items": _assigner_items(node.params.get("items", [])),
        }

    def _list_operator_data(self, node: PlanNode) -> dict[str, Any]:
        return {
            "variable": normalize_list_variable_selector(_selector(node.params.get("variable"), ["start", "items"])),
            "var_type": node.params.get("var_type", "array[string]"),
            "item_var_type": node.params.get("item_var_type", "string"),
            "filter_by": _list_filter(node.params.get("filter_by")),
            "extract_by": _extract_by(node.params.get("extract_by")),
            "order_by": _order_by(node.params.get("order_by")),
            "limit": _limit(node.params.get("limit")),
        }

    def _knowledge_retrieval_data(self, node: PlanNode) -> dict[str, Any]:
        return {
            "query_variable_selector": _selector(node.params.get("query_variable_selector"), ["start", "query"]),
            "query_attachment_selector": _selector(node.params.get("query_attachment_selector"), []),
            "dataset_ids": _dataset_ids(node.params.get("dataset_ids"), self.default_dataset_ids),
            "retrieval_mode": node.params.get("retrieval_mode", "multiple"),
            "multiple_retrieval_config": _multiple_retrieval_config(node.params.get("multiple_retrieval_config")),
            "metadata_filtering_mode": node.params.get("metadata_filtering_mode", "disabled"),
            "metadata_filtering_conditions": deepcopy(node.params.get("metadata_filtering_conditions"))
            if node.params.get("metadata_filtering_conditions") is not None
            else None,
            "metadata_model_config": deepcopy(node.params.get("metadata_model_config"))
            if node.params.get("metadata_model_config") is not None
            else None,
            "vision": _vision(node.params.get("vision")),
        }

    def _human_input_data(self, node: PlanNode) -> dict[str, Any]:
        return {
            "delivery_methods": _human_delivery_methods(node.params.get("delivery_methods")),
            "form_content": normalize_template_refs(
                str(node.params.get("form_content") or "请审核以下内容，并选择处理动作。")
            ),
            "inputs": _human_inputs(node.params.get("inputs")),
            "user_actions": _human_actions(node.params.get("user_actions")),
            "timeout": _positive_int(node.params.get("timeout"), default=3),
            "timeout_unit": _timeout_unit(node.params.get("timeout_unit")),
        }

    def _model_config(self, node: PlanNode) -> dict[str, Any]:
        model = node.params.get("model") if isinstance(node.params.get("model"), dict) else {}
        return {
            "provider": model.get("provider") or node.params.get("model_provider") or self.default_model_provider,
            "name": model.get("name") or node.params.get("model_name") or self.default_model_name,
            "mode": model.get("mode") or node.params.get("model_mode", "chat"),
            "completion_params": model.get("completion_params")
            or node.params.get("completion_params", {"temperature": 0.7}),
        }


def _variables(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    variables = []
    for item in items:
        variable = item.get("variable")
        selector = item.get("value_selector")
        if variable and selector:
            variables.append(_normalize_output({"variable": variable, "value_selector": selector}))
    return variables


def _selector(value: Any, default: list[str]) -> list[str]:
    if isinstance(value, list):
        selector = [str(item) for item in value if str(item)]
        return selector or default
    if isinstance(value, str):
        normalized = normalize_template_refs(value)
        match = DIFY_REF_PATTERN.search(normalized)
        if match:
            return [match.group(1), *[piece for piece in match.group(2).split(".") if piece]]
        pieces = [piece for piece in value.split(".") if piece]
        return pieces if len(pieces) >= 2 else default
    return default


def _selector_list(value: Any) -> list[list[str]]:
    selectors = []
    for item in value or []:
        selector = _selector(item, [])
        if len(selector) >= 2:
            selectors.append(selector)
    return selectors


def _classifier_classes(items: Any) -> list[dict[str, str]]:
    classes = []
    for idx, item in enumerate(items or []):
        if not isinstance(item, dict):
            continue
        class_id = str(item.get("id") or item.get("case_id") or f"class_{idx + 1}")
        name = str(item.get("name") or item.get("label") or class_id)
        classes.append({"id": class_id, "name": name, "label": str(item.get("label") or f"CLASS {idx + 1}")})
    return classes


def _extractor_parameters(items: Any) -> list[dict[str, Any]]:
    parameters = []
    for item in items or []:
        if not isinstance(item, dict):
            continue
        parameter = {
            "name": str(item.get("name", "")),
            "type": str(item.get("type", "string")),
            "description": str(item.get("description", "")),
            "required": bool(item.get("required", True)),
        }
        if parameter["type"] == "select" and isinstance(item.get("options"), list):
            parameter["options"] = [str(option) for option in item["options"]]
        parameters.append(parameter)
    return parameters


def _assigner_items(items: Any) -> list[dict[str, Any]]:
    result = []
    for item in items or []:
        if not isinstance(item, dict):
            continue
        input_type = str(item.get("input_type") or "constant")
        value = item.get("value")
        if input_type == "variable":
            value = _selector(value, [])
        result.append(
            {
                "variable_selector": _selector(item.get("variable_selector"), []),
                "input_type": input_type,
                "operation": str(item.get("operation") or "over-write"),
                "value": value,
            }
        )
    return result


def _list_filter(value: Any) -> dict[str, Any]:
    if not isinstance(value, dict):
        return {"enabled": False, "conditions": []}
    conditions = []
    for item in value.get("conditions") if isinstance(value.get("conditions"), list) else []:
        if not isinstance(item, dict):
            continue
        condition = deepcopy(item)
        condition["comparison_operator"] = normalize_list_comparison_operator(
            condition.get("comparison_operator") or condition.get("operator") or "contains"
        )
        condition.setdefault("key", "")
        condition.setdefault("value", "")
        conditions.append(condition)
    return {
        "enabled": bool(value.get("enabled", False)),
        "conditions": conditions,
    }


def _extract_by(value: Any) -> dict[str, Any]:
    if not isinstance(value, dict):
        return {"enabled": False, "serial": "1"}
    return {"enabled": bool(value.get("enabled", False)), "serial": str(value.get("serial") or "1")}


def _order_by(value: Any) -> dict[str, Any]:
    if not isinstance(value, dict):
        return {"enabled": False, "key": "", "value": "asc"}
    return {
        "enabled": bool(value.get("enabled", False)),
        "key": deepcopy(value.get("key", "")),
        "value": str(value.get("value") or "asc"),
    }


def _limit(value: Any) -> dict[str, Any]:
    if not isinstance(value, dict):
        return {"enabled": False, "size": 10}
    try:
        size = int(value.get("size", 10))
    except (TypeError, ValueError):
        size = 10
    return {"enabled": bool(value.get("enabled", False)), "size": max(1, size)}


def _dataset_ids(value: Any, default: list[str]) -> list[str]:
    if isinstance(value, str):
        items = value.split(",")
    elif isinstance(value, list):
        items = value
    else:
        items = []
    dataset_ids = [str(item).strip() for item in items if str(item).strip()]
    return dataset_ids or list(default)


def _human_delivery_methods(value: Any) -> list[dict[str, Any]]:
    methods = []
    for idx, item in enumerate(value or []):
        if not isinstance(item, dict):
            continue
        method_type = str(item.get("type") or "webapp")
        method = {
            "id": str(item.get("id") or f"{method_type}-{idx + 1}"),
            "type": method_type,
            "enabled": bool(item.get("enabled", True)),
        }
        config = item.get("config")
        if isinstance(config, dict):
            method["config"] = deepcopy(config)
        elif method_type == "webapp":
            method["config"] = {}
        methods.append(method)
    if not methods:
        methods.append({"id": "webapp-1", "type": "webapp", "enabled": True, "config": {}})
    if not any(method.get("enabled") for method in methods):
        methods[0]["enabled"] = True
    return methods


def _human_inputs(value: Any) -> list[dict[str, Any]]:
    inputs = []
    for item in value or []:
        if not isinstance(item, dict):
            continue
        name = str(item.get("output_variable_name") or item.get("name") or item.get("variable") or "")
        if not name:
            continue
        default = item.get("default") if isinstance(item.get("default"), dict) else {}
        default_type = str(default.get("type") or "constant")
        normalized_default = {
            "type": default_type if default_type in {"constant", "variable"} else "constant",
            "selector": _selector(default.get("selector"), []),
            "value": str(default.get("value") or ""),
        }
        inputs.append(
            {
                "type": str(item.get("type") or "paragraph"),
                "output_variable_name": name,
                "default": normalized_default,
            }
        )
    return inputs


def _human_actions(value: Any) -> list[dict[str, Any]]:
    actions = []
    for idx, item in enumerate(value or []):
        if not isinstance(item, dict):
            continue
        action_id = str(item.get("id") or f"action_{idx + 1}")
        title = str(item.get("title") or action_id)
        style = str(item.get("button_style") or ("primary" if idx == 0 else "default"))
        if style not in {"primary", "default", "accent", "ghost"}:
            style = "default"
        actions.append({"id": action_id, "title": title, "button_style": style})
    return actions or [
        {"id": "approve", "title": "通过", "button_style": "primary"},
        {"id": "reject", "title": "驳回", "button_style": "default"},
    ]


def _timeout_unit(value: Any) -> str:
    normalized = str(value or "day").strip().lower()
    return "hour" if normalized in {"hour", "hours", "h", "小时"} else "day"


def _positive_int(value: Any, *, default: int) -> int:
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        parsed = default
    return max(1, parsed)


def _multiple_retrieval_config(value: Any) -> dict[str, Any]:
    config = value if isinstance(value, dict) else {}
    try:
        top_k = int(config.get("top_k", 4))
    except (TypeError, ValueError):
        top_k = 4
    result = {
        "top_k": max(1, top_k),
        "score_threshold": config.get("score_threshold"),
        "reranking_enable": bool(config.get("reranking_enable", False)),
        "reranking_mode": str(config.get("reranking_mode") or "reranking_model"),
    }
    reranking_model = _reranking_model(config.get("reranking_model"))
    if result["reranking_enable"] and reranking_model:
        result["reranking_model"] = reranking_model
    if isinstance(config.get("weights"), dict):
        result["weights"] = deepcopy(config["weights"])
    return result


def _reranking_model(value: Any) -> dict[str, str] | None:
    if not isinstance(value, dict):
        return None
    provider = str(value.get("provider") or value.get("reranking_provider_name") or "").strip()
    model = str(value.get("model") or value.get("reranking_model_name") or "").strip()
    if not provider or not model:
        return None
    return {"provider": provider, "model": model}


def _vision(value: Any) -> dict[str, Any]:
    if isinstance(value, dict):
        enabled = bool(value.get("enabled", False))
        configs = deepcopy(value.get("configs")) if isinstance(value.get("configs"), dict) else {"variable_selector": []}
        configs.setdefault("variable_selector", [])
        return {"enabled": enabled, "configs": configs}
    return {"enabled": False, "configs": {"variable_selector": []}}


def _normalize_output(item: dict[str, Any]) -> dict[str, Any]:
    output = dict(item)
    output.setdefault("value_type", "string")
    return output


def _key_value_text(value: Any) -> str:
    if value in (None, "", [], {}):
        return ""
    if isinstance(value, dict):
        return "\n".join(
            f"{str(key).strip()}:{normalize_template_refs(str(item)).strip()}"
            for key, item in value.items()
            if str(key).strip() and str(item).strip()
        )
    if isinstance(value, list):
        lines = []
        for item in value:
            if not isinstance(item, dict):
                continue
            key = str(item.get("key", "")).strip()
            item_value = normalize_template_refs(str(item.get("value", ""))).strip()
            if key and item_value:
                lines.append(f"{key}:{item_value}")
        return "\n".join(lines)

    text = normalize_template_refs(str(value)).strip()
    if text in {"[]", "{}", "null", "None"}:
        return ""
    lines = []
    for line in text.splitlines():
        key, separator, raw_value = line.partition(":")
        key = key.strip()
        item_value = raw_value.strip()
        if separator and key and item_value:
            lines.append(f"{key}:{item_value}")
    return "\n".join(lines)


def _http_body(value: Any) -> dict[str, Any]:
    if value in (None, "", [], {}):
        return {"type": "none", "data": []}
    if not isinstance(value, dict):
        return {"type": "raw-text", "data": normalize_template_refs(str(value))}
    body = deepcopy(value)
    body_type = body.get("type") or "none"
    body["type"] = body_type
    if body_type == "none":
        body["data"] = []
    elif isinstance(body.get("data"), str):
        body["data"] = normalize_template_refs(body["data"])
    elif "data" not in body:
        body["data"] = []
    return body


def _jinja_template(template: Any, variables: list[dict[str, Any]]) -> str:
    normalized = normalize_template_refs(str(template))
    variable_by_selector = {
        tuple(item.get("value_selector", [])): str(item.get("variable"))
        for item in variables
        if item.get("variable") and isinstance(item.get("value_selector"), list)
    }

    def replace(match: re.Match[str]) -> str:
        selector = (match.group(1), *[piece for piece in match.group(2).split(".") if piece])
        variable = variable_by_selector.get(selector) or _safe_variable_name(selector[-1])
        return "{{ " + variable + " }}"

    return DIFY_REF_PATTERN.sub(replace, normalized)


def _safe_variable_name(value: str) -> str:
    safe = re.sub(r"[^A-Za-z0-9_]", "_", value).strip("_")
    if not safe:
        return "value"
    if safe[0].isdigit():
        safe = f"var_{safe}"
    return safe


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
        "json": "json_object",
        "json-object": "json_object",
    }
    return mapping.get(normalized, "paragraph")


def _default_title(node_type: str) -> str:
    return node_type.replace("-", " ").title()


def _node_height(node_type: str, data: dict[str, Any]) -> int:
    if node_type == "template-transform":
        return 54
    if node_type == "if-else":
        return 126
    if node_type == "question-classifier":
        return 112 + max(0, len(data.get("classes", [])) - 2) * 26
    if node_type == "parameter-extractor":
        return 112 + max(0, len(data.get("parameters", [])) - 2) * 22
    if node_type == "variable-aggregator":
        return 92 + max(0, len(data.get("variables", [])) - 1) * 20
    if node_type == "document-extractor":
        return 92
    if node_type == "assigner":
        return 92 + max(0, len(data.get("items", [])) - 1) * 24
    if node_type == "list-operator":
        return 112
    if node_type == "knowledge-retrieval":
        return 116
    if node_type == "human-input":
        return 116 + max(0, len(data.get("user_actions", [])) - 2) * 24
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
