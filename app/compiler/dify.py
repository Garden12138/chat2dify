from __future__ import annotations

import re
from copy import deepcopy
from uuid import NAMESPACE_URL, UUID, uuid4, uuid5
from typing import Any

import yaml

from app.models import PlanNode, WorkflowPlan
from app.agent.normalizer import normalize_template_refs
from app.input_variables import file_upload_settings, is_file_input_type
from app.list_operator import normalize_list_comparison_operator, normalize_list_variable_selector


CUSTOM_NODE_TYPE = "custom"
CUSTOM_ITERATION_START_NODE_TYPE = "custom-iteration-start"
CUSTOM_LOOP_START_NODE_TYPE = "custom-loop-start"
CUSTOM_SIMPLE_NODE_TYPE = "custom-simple"
SOURCE_HANDLE = "source"
TARGET_HANDLE = "target"
NODE_WIDTH_X_OFFSET = 300
START_X = 80
START_Y = 282
CONTAINER_CHILD_Z_INDEX = 1002
CONTAINER_PARENT_Z_INDEX = 1
CONTAINER_CHILD_START_X = 24
CONTAINER_CHILD_START_Y = 68
DIFY_REF_PATTERN = re.compile(r"\{\{\s*#([A-Za-z0-9_-]+)\.([A-Za-z0-9_.-]+)#\s*\}\}")
HUMAN_INPUT_DEFAULT_WEBAPP_DELIVERY_ID = "00000000-0000-4000-8000-000000000001"
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
        output_types = _plan_output_types(plan)
        nodes: list[dict[str, Any]] = []
        for index, node in enumerate(plan.nodes):
            graph_node = self._compile_node(node, index, output_types=output_types)
            nodes.append(graph_node)
            if node.type in {"iteration", "loop"}:
                nodes.extend(self._compile_container_child_nodes(node, output_types=output_types))
        type_by_id = {node["id"]: node["data"]["type"] for node in nodes}
        edges = [self._compile_edge(edge.model_dump(), type_by_id) for edge in plan.edges]
        for node in plan.nodes:
            if node.type in {"iteration", "loop"}:
                edges.extend(self._compile_container_edges(node, type_by_id))

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

    def _compile_node(
        self,
        node: PlanNode,
        index: int,
        *,
        output_types: dict[tuple[str, str], str],
    ) -> dict[str, Any]:
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
                data.update(self._llm_data(node, output_types=output_types))
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
            case "iteration":
                data.update(self._iteration_data(node))
            case "iteration-start":
                data.update({"title": "", "desc": "", "isInIteration": True})
            case "loop":
                data.update(self._loop_data(node))
            case "loop-start":
                data.update({"title": "", "desc": "", "isInLoop": True})
            case "loop-end":
                data.update({})
            case "agent":
                data.update(_agent_data(node))
            case "trigger-webhook":
                data.update(self._trigger_webhook_data(node))
            case "trigger-plugin":
                data.update(self._trigger_plugin_data(node))
            case "trigger-schedule":
                data.update(self._trigger_schedule_data(node))
            case node_type if node_type in EXTERNAL_DEPENDENCY_NODE_TYPES:
                data.update(_external_dependency_data(node))

        return {
            "id": node.id,
            "type": _graph_node_type(node.type),
            "position": position,
            "positionAbsolute": position.copy(),
            "height": _node_height(node.type, data),
            "width": _node_width(node.type, data),
            "selected": False,
            "targetPosition": "left",
            "sourcePosition": "right",
            "zIndex": CONTAINER_PARENT_Z_INDEX if node.type in {"iteration", "loop"} else 0,
            "data": data,
        }

    def _compile_edge(
        self,
        edge: dict[str, Any],
        type_by_id: dict[str, str],
        *,
        container: PlanNode | None = None,
    ) -> dict[str, Any]:
        source = str(edge.get("source"))
        target = str(edge.get("target"))
        source_handle = str(edge.get("source_handle") or edge.get("sourceHandle") or SOURCE_HANDLE)
        target_handle = str(edge.get("target_handle") or edge.get("targetHandle") or TARGET_HANDLE)
        data: dict[str, Any] = {
            "isInIteration": False,
            "isInLoop": False,
            "sourceType": type_by_id[source],
            "targetType": type_by_id[target],
        }
        z_index = 0
        if container and container.type == "iteration":
            data.update({"isInIteration": True, "iteration_id": container.id})
            z_index = CONTAINER_CHILD_Z_INDEX
        elif container and container.type == "loop":
            data.update({"isInLoop": True, "loop_id": container.id})
            z_index = CONTAINER_CHILD_Z_INDEX
        return {
            "id": f"{source}-{source_handle}-{target}-{target_handle}",
            "type": "custom",
            "source": source,
            "target": target,
            "sourceHandle": source_handle,
            "targetHandle": target_handle,
            "data": data,
            "zIndex": z_index,
        }

    def _compile_container_child_nodes(
        self,
        node: PlanNode,
        *,
        output_types: dict[tuple[str, str], str],
    ) -> list[dict[str, Any]]:
        children = node.params.get("children") if isinstance(node.params.get("children"), list) else []
        graph_nodes: list[dict[str, Any]] = []
        for index, child in enumerate(children):
            if not isinstance(child, dict):
                continue
            child_node = PlanNode.model_validate(child)
            graph_node = self._compile_node(child_node, index, output_types=output_types)
            position = _child_position(child, index)
            graph_node["position"] = position
            graph_node["positionAbsolute"] = position.copy()
            graph_node["parentId"] = node.id
            graph_node["extent"] = "parent"
            graph_node["zIndex"] = CONTAINER_CHILD_Z_INDEX
            if child_node.type in {"iteration-start", "loop-start"}:
                graph_node["selectable"] = False
                graph_node["draggable"] = False
            if node.type == "iteration":
                graph_node["data"]["isInIteration"] = True
                graph_node["data"]["iteration_id"] = node.id
            else:
                graph_node["data"]["isInLoop"] = True
                graph_node["data"]["loop_id"] = node.id
            graph_nodes.append(graph_node)
        return graph_nodes

    def _compile_container_edges(self, node: PlanNode, type_by_id: dict[str, str]) -> list[dict[str, Any]]:
        raw_edges = node.params.get("edges") if isinstance(node.params.get("edges"), list) else []
        return [
            self._compile_edge(edge, type_by_id, container=node)
            for edge in raw_edges
            if isinstance(edge, dict)
            and str(edge.get("source")) in type_by_id
            and str(edge.get("target")) in type_by_id
        ]

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

    def _llm_data(
        self,
        node: PlanNode,
        *,
        output_types: dict[tuple[str, str], str],
    ) -> dict[str, Any]:
        provider = node.params.get("model_provider") or self.default_model_provider
        name = node.params.get("model_name") or self.default_model_name
        system_prompt = node.params.get("system_prompt", "")
        user_prompt = node.params.get("user_prompt") or "{{#start.query#}}"
        prompt_variables: dict[tuple[str, str], str] = {}
        prompt_template = [
            _compile_prompt_item(
                "system",
                system_prompt,
                output_types=output_types,
                prompt_variables=prompt_variables,
            ),
            _compile_prompt_item(
                "user",
                user_prompt,
                output_types=output_types,
                prompt_variables=prompt_variables,
            ),
        ]
        data = {
            "model": {
                "provider": provider,
                "name": name,
                "mode": node.params.get("model_mode", "chat"),
                "completion_params": node.params.get("completion_params", {"temperature": 0.7}),
            },
            "prompt_template": prompt_template,
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
        if prompt_variables:
            data["prompt_config"] = {
                "jinja2_variables": [
                    {
                        "variable": alias,
                        "value_selector": [node_id, variable],
                    }
                    for (node_id, variable), alias in prompt_variables.items()
                ]
            }
        return data

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

    def _iteration_data(self, node: PlanNode) -> dict[str, Any]:
        children = node.params.get("children") if isinstance(node.params.get("children"), list) else []
        return {
            "start_node_id": str(node.params.get("start_node_id") or f"{node.id}start"),
            "iterator_selector": _selector(node.params.get("iterator_selector"), ["start", "items"]),
            "iterator_input_type": node.params.get("iterator_input_type", "array[string]"),
            "output_selector": _selector(node.params.get("output_selector"), []),
            "output_type": node.params.get("output_type", "array[string]"),
            "_children": _container_children_refs(children),
            "_isShowTips": bool(node.params.get("_isShowTips", False)),
            "is_parallel": bool(node.params.get("is_parallel", False)),
            "parallel_nums": _positive_int(node.params.get("parallel_nums"), default=10),
            "error_handle_mode": _error_handle_mode(node.params.get("error_handle_mode")),
            "flatten_output": bool(node.params.get("flatten_output", True)),
        }

    def _loop_data(self, node: PlanNode) -> dict[str, Any]:
        children = node.params.get("children") if isinstance(node.params.get("children"), list) else []
        return {
            "start_node_id": str(node.params.get("start_node_id") or f"{node.id}start"),
            "break_conditions": _loop_conditions(node.params.get("break_conditions")),
            "loop_count": _positive_int(node.params.get("loop_count"), default=3),
            "logical_operator": node.params.get("logical_operator", "and"),
            "loop_variables": _loop_variables(node.params.get("loop_variables")),
            "error_handle_mode": _error_handle_mode(node.params.get("error_handle_mode")),
            "_children": _container_children_refs(children),
        }

    def _trigger_webhook_data(self, node: PlanNode) -> dict[str, Any]:
        params = node.params
        return {
            "webhook_url": str(params.get("webhook_url") or ""),
            "webhook_debug_url": str(params.get("webhook_debug_url") or ""),
            "method": str(params.get("method") or "POST").upper(),
            "content_type": str(params.get("content_type") or "application/json"),
            "headers": _webhook_parameters(params.get("headers"), header=True),
            "params": _webhook_parameters(params.get("params")),
            "body": _webhook_parameters(params.get("body")),
            "async_mode": True,
            "status_code": _bounded_int(params.get("status_code"), default=200, minimum=100, maximum=599),
            "response_body": str(params.get("response_body") or ""),
            "timeout": _bounded_int(params.get("timeout"), default=30, minimum=1, maximum=300),
            "variables": _webhook_variables(params),
        }

    def _trigger_schedule_data(self, node: PlanNode) -> dict[str, Any]:
        params = node.params
        mode = str(params.get("mode") or "visual")
        if mode == "cron":
            return {
                "mode": "cron",
                "cron_expression": str(params.get("cron_expression") or ""),
                "timezone": str(params.get("timezone") or "Asia/Shanghai"),
            }

        visual = params.get("visual_config") if isinstance(params.get("visual_config"), dict) else {}
        return {
            "mode": "visual",
            "frequency": str(params.get("frequency") or "daily"),
            "visual_config": {
                "time": str(visual.get("time") or "09:00 AM"),
                "weekdays": deepcopy(visual.get("weekdays") or ["mon"]),
                "on_minute": _bounded_int(visual.get("on_minute"), default=0, minimum=0, maximum=59),
                "monthly_days": deepcopy(visual.get("monthly_days") or [1]),
            },
            "timezone": str(params.get("timezone") or "Asia/Shanghai"),
        }

    def _trigger_plugin_data(self, node: PlanNode) -> dict[str, Any]:
        if isinstance(node.params.get("_raw_data"), dict):
            return _external_dependency_data(node)
        params = node.params
        event_parameters = deepcopy(params.get("event_parameters") or {})
        return {
            "provider_id": str(params.get("provider_id") or ""),
            "provider_type": str(params.get("provider_type") or "trigger"),
            "provider_name": str(params.get("provider_name") or params.get("provider_id") or ""),
            "plugin_id": str(params.get("plugin_id") or ""),
            "plugin_unique_identifier": str(params.get("plugin_unique_identifier") or ""),
            "event_name": str(params.get("event_name") or ""),
            "event_label": str(params.get("event_label") or params.get("event_name") or ""),
            "subscription_id": str(params.get("subscription_id") or ""),
            "event_parameters": event_parameters,
            "event_configurations": deepcopy(params.get("event_configurations") or {}),
            "config": deepcopy(params.get("config") or event_parameters),
            "parameters_schema": deepcopy(params.get("parameters_schema") or []),
            "output_schema": deepcopy(params.get("output_schema") or {}),
            "version": str(params.get("version") or "1"),
            "event_node_version": str(params.get("event_node_version") or "1"),
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


def _graph_node_type(node_type: str) -> str:
    if node_type == "iteration-start":
        return CUSTOM_ITERATION_START_NODE_TYPE
    if node_type == "loop-start":
        return CUSTOM_LOOP_START_NODE_TYPE
    if node_type == "loop-end":
        return CUSTOM_SIMPLE_NODE_TYPE
    return CUSTOM_NODE_TYPE


def _external_dependency_data(node: PlanNode) -> dict[str, Any]:
    raw_data = node.params.get("_raw_data") if isinstance(node.params.get("_raw_data"), dict) else None
    if raw_data is not None:
        data = deepcopy(raw_data)
    else:
        data = {key: deepcopy(value) for key, value in node.params.items() if not str(key).startswith("_")}
    data.pop("type", None)
    data.pop("title", None)
    data.pop("desc", None)
    data.pop("selected", None)
    return data


def _agent_data(node: PlanNode) -> dict[str, Any]:
    raw_data = node.params.get("_raw_data") if isinstance(node.params.get("_raw_data"), dict) else None
    if raw_data is not None:
        return _external_dependency_data(node)
    params = node.params
    agent_parameters = deepcopy(params.get("agent_parameters") or {})
    model_input = agent_parameters.get("model") if isinstance(agent_parameters, dict) else None
    if isinstance(model_input, dict) and isinstance(model_input.get("value"), dict):
        model_value = model_input["value"]
        if model_value.get("provider") and model_value.get("model"):
            model_value.setdefault("model_type", "llm")
            if model_value.get("model_type") == "llm":
                model_value.setdefault("mode", "chat")
                if not isinstance(model_value.get("completion_params"), dict):
                    model_value["completion_params"] = {}
    data: dict[str, Any] = {
        "agent_strategy_provider_name": params.get("agent_strategy_provider_name", ""),
        "agent_strategy_name": params.get("agent_strategy_name", ""),
        "agent_strategy_label": params.get("agent_strategy_label", ""),
        "agent_parameters": agent_parameters,
        "output_schema": deepcopy(params.get("output_schema") or {}),
        "tool_node_version": str(params.get("tool_node_version") or "2"),
    }
    for optional_key in ("plugin_unique_identifier", "meta", "memory"):
        if params.get(optional_key) is not None:
            data[optional_key] = deepcopy(params[optional_key])
    return data


def _child_position(child: dict[str, Any], index: int) -> dict[str, int]:
    raw_position = child.get("position")
    if not isinstance(raw_position, dict):
        params = child.get("params") if isinstance(child.get("params"), dict) else {}
        raw_position = params.get("_position")
    if isinstance(raw_position, dict):
        try:
            return {"x": int(raw_position.get("x", 0)), "y": int(raw_position.get("y", 0))}
        except (TypeError, ValueError):
            pass
    return {"x": CONTAINER_CHILD_START_X + index * NODE_WIDTH_X_OFFSET, "y": CONTAINER_CHILD_START_Y}


def _container_children_refs(children: list[Any]) -> list[dict[str, str]]:
    refs = []
    for child in children:
        if not isinstance(child, dict) or not child.get("id") or not child.get("type"):
            continue
        refs.append({"nodeId": str(child["id"]), "nodeType": str(child["type"])})
    return refs


def _loop_conditions(value: Any) -> list[dict[str, Any]]:
    conditions = []
    for item in value if isinstance(value, list) else []:
        if not isinstance(item, dict):
            continue
        condition = deepcopy(item)
        condition.setdefault("id", str(uuid4()))
        condition["variable_selector"] = _selector(condition.get("variable_selector"), [])
        condition.setdefault("comparison_operator", "not empty")
        condition.setdefault("value", "")
        condition.setdefault("varType", "string")
        conditions.append(condition)
    return conditions


def _loop_variables(value: Any) -> list[dict[str, Any]]:
    variables = []
    for item in value if isinstance(value, list) else []:
        if not isinstance(item, dict):
            continue
        label = str(item.get("label") or item.get("name") or item.get("variable") or "")
        if not label:
            continue
        value_type = str(item.get("value_type") or "constant")
        variable_value = item.get("value", "")
        if value_type == "variable":
            variable_value = _selector(variable_value, [])
        variables.append(
            {
                "id": str(item.get("id") or label),
                "label": label,
                "var_type": str(item.get("var_type") or "string"),
                "value_type": value_type if value_type in {"constant", "variable"} else "constant",
                "value": variable_value,
            }
        )
    return variables


def _error_handle_mode(value: Any) -> str:
    normalized = str(value or "terminated").strip().lower().replace("_", "-")
    allowed = {"terminated", "continue-on-error", "remove-abnormal-output"}
    if normalized in {"continue", "continue-error"}:
        return "continue-on-error"
    if normalized in {"remove", "remove-abnormal"}:
        return "remove-abnormal-output"
    return normalized if normalized in allowed else "terminated"


def _variables(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    variables = []
    for item in items:
        variable = item.get("variable")
        selector = item.get("value_selector")
        if variable and selector:
            normalized = {
                "variable": variable,
                "value_selector": selector,
            }
            if item.get("value_type") is not None:
                normalized["value_type"] = item["value_type"]
            variables.append(_normalize_output(normalized))
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
            "id": _human_delivery_method_id(item.get("id"), fallback_key=f"{method_type}-{idx + 1}"),
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


def _human_delivery_method_id(value: Any, *, fallback_key: str) -> str:
    raw = str(value or "").strip()
    if raw:
        try:
            return str(UUID(raw))
        except ValueError:
            return str(uuid5(NAMESPACE_URL, f"chat2dify:human-input:delivery:{raw}"))
    return str(uuid5(NAMESPACE_URL, f"chat2dify:human-input:delivery:{fallback_key}"))


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


def _bounded_int(value: Any, *, default: int, minimum: int, maximum: int) -> int:
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        parsed = default
    return max(minimum, min(maximum, parsed))


def _webhook_parameters(value: Any, *, header: bool = False) -> list[dict[str, Any]]:
    result = []
    for item in value if isinstance(value, list) else []:
        if not isinstance(item, dict) or not item.get("name"):
            continue
        result.append(
            {
                "name": str(item["name"]),
                "type": "string" if header else str(item.get("type") or "string"),
                "required": bool(item.get("required", False)),
            }
        )
    return result


def _webhook_variables(params: dict[str, Any]) -> list[dict[str, Any]]:
    variables = params.get("variables") if isinstance(params.get("variables"), list) else []
    if variables:
        return deepcopy(variables)
    result = [
        {
            "variable": "_webhook_raw",
            "label": "raw",
            "value_type": "object",
            "value_selector": [],
            "required": True,
        }
    ]
    for label, items in (
        ("header", params.get("headers")),
        ("param", params.get("params")),
        ("body", params.get("body")),
    ):
        for item in items if isinstance(items, list) else []:
            if not isinstance(item, dict) or not item.get("name"):
                continue
            name = str(item["name"]).replace("-", "_") if label == "header" else str(item["name"])
            result.append(
                {
                    "variable": name,
                    "label": label,
                    "value_type": str(item.get("type") or "string"),
                    "value_selector": [],
                    "required": bool(item.get("required", False)),
                }
            )
    return result


_BASIC_PROMPT_VAR_TYPES = {
    "string",
    "secret",
    "number",
    "array",
    "array[string]",
    "array[number]",
    "array[object]",
    "file",
    "array[file]",
}


def _compile_prompt_item(
    role: str,
    text: Any,
    *,
    output_types: dict[tuple[str, str], str],
    prompt_variables: dict[tuple[str, str], str],
) -> dict[str, Any]:
    normalized = normalize_template_refs(str(text or ""))
    references = [
        (match.group(1), match.group(2))
        for match in DIFY_REF_PATTERN.finditer(normalized)
    ]
    needs_jinja = any(
        output_types.get(reference) not in _BASIC_PROMPT_VAR_TYPES
        for reference in references
        if output_types.get(reference)
    )
    if not needs_jinja:
        return {"role": role, "text": normalized}

    def replace_reference(match: re.Match[str]) -> str:
        reference = (match.group(1), match.group(2))
        alias = prompt_variables.get(reference)
        if alias is None:
            alias = _unique_prompt_alias(reference, set(prompt_variables.values()))
            prompt_variables[reference] = alias
        return f"{{{{ {alias} }}}}"

    return {
        "role": role,
        "text": normalized,
        "jinja2_text": DIFY_REF_PATTERN.sub(replace_reference, normalized),
        "edition_type": "jinja2",
    }


def _unique_prompt_alias(reference: tuple[str, str], existing: set[str]) -> str:
    raw = re.sub(r"[^A-Za-z0-9_]", "_", f"{reference[0]}_{reference[1]}")
    alias = raw if raw and not raw[0].isdigit() else f"v_{raw}"
    alias = alias or "value"
    candidate = alias
    suffix = 2
    while candidate in existing:
        candidate = f"{alias}_{suffix}"
        suffix += 1
    return candidate


def _plan_output_types(plan: WorkflowPlan) -> dict[tuple[str, str], str]:
    output_types: dict[tuple[str, str], str] = {
        ("sys", "timestamp"): "number",
    }

    def register(node: PlanNode) -> None:
        for variable, variable_type in _node_output_types(node).items():
            output_types[(node.id, variable)] = variable_type
        for child in node.params.get("children") if isinstance(node.params.get("children"), list) else []:
            if isinstance(child, dict):
                register(PlanNode.model_validate(child))

    for node in plan.nodes:
        register(node)
    return output_types


def _node_output_types(node: PlanNode) -> dict[str, str]:
    params = node.params
    match node.type:
        case "start":
            return {
                str(item.get("name") or item.get("variable")): _start_output_type(item.get("type"))
                for item in params.get("variables", [])
                if isinstance(item, dict) and (item.get("name") or item.get("variable"))
            }
        case "trigger-webhook":
            variables = params.get("variables") if isinstance(params.get("variables"), list) else []
            result = {
                str(item.get("variable") or item.get("name")): str(
                    item.get("value_type") or item.get("type") or "string"
                )
                for item in variables
                if isinstance(item, dict) and (item.get("variable") or item.get("name"))
            }
            if result:
                return result
            result = {"_webhook_raw": "object"}
            for group in ("headers", "params", "body"):
                for item in params.get(group) if isinstance(params.get(group), list) else []:
                    if not isinstance(item, dict) or not item.get("name"):
                        continue
                    variable = str(item["name"]).replace("-", "_") if group == "headers" else str(item["name"])
                    result[variable] = str(item.get("type") or "string")
            return result
        case "trigger-plugin":
            schema = params.get("output_schema") if isinstance(params.get("output_schema"), dict) else {}
            properties = schema.get("properties") if isinstance(schema.get("properties"), dict) else {}
            return {
                str(name): str(config.get("type") or "object") if isinstance(config, dict) else "object"
                for name, config in properties.items()
            }
        case "llm":
            return {"text": "string"}
        case "code":
            outputs = params.get("outputs") if isinstance(params.get("outputs"), dict) else {}
            return {
                str(name): str(config.get("type") or "string") if isinstance(config, dict) else "string"
                for name, config in outputs.items()
            }
        case "http-request":
            return {"body": "string", "status_code": "number", "headers": "object"}
        case "template-transform":
            return {"output": "string"}
        case "question-classifier":
            return {"class_name": "string"}
        case "parameter-extractor":
            return {
                str(item.get("name")): str(item.get("type") or "string")
                for item in params.get("parameters", [])
                if isinstance(item, dict) and item.get("name")
            }
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
                for item in params.get("inputs", [])
                if isinstance(item, dict) and item.get("output_variable_name")
            }
            result.update({"selected_action": "string", "submitted_at": "string"})
            return result
        case "tool" | "agent":
            return {"text": "string", "files": "array[file]", "json": "object"}
        case "knowledge-index":
            return {"result": "object", "document_ids": "array[string]"}
        case "datasource" | "datasource-empty":
            return {"datasource_type": "string", "file": "file"}
        case "iteration":
            return {"output": "array", "item": "object", "index": "number"}
        case "loop":
            result = {"loop_round": "number"}
            for item in params.get("loop_variables", []):
                if isinstance(item, dict) and item.get("label"):
                    result[str(item["label"])] = str(item.get("var_type") or item.get("type") or "string")
            return result
    return {}


def _start_output_type(value: Any) -> str:
    input_type = _input_type(value or "paragraph")
    return {
        "number": "number",
        "checkbox": "boolean",
        "json_object": "object",
        "file": "file",
        "file-list": "array[file]",
    }.get(input_type, "string")


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
    if node_type in {"iteration", "loop"}:
        children_count = len(data.get("_children", [])) if isinstance(data.get("_children"), list) else 1
        return 220 + max(0, children_count - 2) * 28
    if node_type in EXTERNAL_DEPENDENCY_NODE_TYPES:
        return 104
    if node_type in {"iteration-start", "loop-start", "loop-end"}:
        return 54
    if node_type == "end":
        return 90 + max(0, len(data.get("outputs", [])) - 1) * 26
    return 90


def _node_width(node_type: str, data: dict[str, Any]) -> int:
    if node_type in {"iteration", "loop"}:
        children_count = max(2, len(data.get("_children", [])) if isinstance(data.get("_children"), list) else 2)
        return max(620, 80 + children_count * NODE_WIDTH_X_OFFSET)
    if node_type == "loop-end":
        return 168
    return 244


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
