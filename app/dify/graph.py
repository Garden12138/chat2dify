from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass
from typing import Any

import yaml

from app.agent.normalizer import normalize_plan_payload
from app.compiler.dify import DifyDslCompiler
from app.input_variables import file_upload_settings, is_file_input_type
from app.models import WorkflowPlan


SUPPORTED_DIFY_NODE_TYPES = {
    "start",
    "llm",
    "code",
    "if-else",
    "end",
    "http-request",
    "template-transform",
    "question-classifier",
    "parameter-extractor",
    "variable-aggregator",
    "document-extractor",
    "assigner",
    "list-operator",
    "knowledge-retrieval",
}
LAYOUT_KEYS = ("position", "positionAbsolute", "width", "height", "sourcePosition", "targetPosition")


class DifyGraphAdapterError(RuntimeError):
    """Raised when a Dify graph cannot be adapted into chat2dify's Plan IR."""


@dataclass(frozen=True)
class UnsupportedExistingNodeType(DifyGraphAdapterError):
    node_id: str
    node_type: str

    def __str__(self) -> str:
        return f"Unsupported existing Dify node type: {self.node_type} ({self.node_id})"


def decompile_dify_graph(graph: dict[str, Any], *, name: str = "Existing Workflow") -> WorkflowPlan:
    nodes = graph.get("nodes")
    edges = graph.get("edges")
    if not isinstance(nodes, list) or not isinstance(edges, list):
        raise DifyGraphAdapterError("Dify workflow graph must contain nodes and edges lists.")

    plan_nodes = []
    for node in nodes:
        if not isinstance(node, dict):
            continue
        data = node.get("data") if isinstance(node.get("data"), dict) else {}
        node_type = str(data.get("type", ""))
        node_id = str(node.get("id", ""))
        if node_type not in SUPPORTED_DIFY_NODE_TYPES:
            raise UnsupportedExistingNodeType(node_id=node_id, node_type=node_type or "<missing>")
        plan_nodes.append(
            {
                "id": node_id,
                "type": node_type,
                "title": data.get("title") or node_type.replace("-", " ").title(),
                "desc": data.get("desc") or "",
                "params": _params_from_dify_node_data(node_type, data),
            }
        )

    plan_edges = []
    for edge in edges:
        if not isinstance(edge, dict):
            continue
        plan_edges.append(
            {
                "source": str(edge.get("source", "")),
                "target": str(edge.get("target", "")),
                "source_handle": str(edge.get("sourceHandle") or edge.get("source_handle") or "source"),
                "target_handle": str(edge.get("targetHandle") or edge.get("target_handle") or "target"),
            }
        )

    normalized = normalize_plan_payload(
        {
            "name": name,
            "description": "Workflow draft loaded from Dify.",
            "nodes": plan_nodes,
            "edges": plan_edges,
        }
    )
    return WorkflowPlan.model_validate(normalized.payload)


def compile_plan_to_dify_graph(
    plan: WorkflowPlan,
    *,
    compiler: DifyDslCompiler,
    base_graph: dict[str, Any] | None = None,
) -> dict[str, Any]:
    data = yaml.safe_load(compiler.compile(plan))
    graph = data["workflow"]["graph"]
    if base_graph:
        _merge_existing_layout(graph, base_graph)
    return graph


def _params_from_dify_node_data(node_type: str, data: dict[str, Any]) -> dict[str, Any]:
    match node_type:
        case "start":
            return {"variables": [_start_variable(item) for item in data.get("variables", []) if isinstance(item, dict)]}
        case "llm":
            system_prompt, user_prompt = _prompt_texts(data.get("prompt_template", []))
            model = data.get("model") if isinstance(data.get("model"), dict) else {}
            return {
                "system_prompt": system_prompt,
                "user_prompt": user_prompt,
                "model_provider": model.get("provider"),
                "model_name": model.get("name"),
                "model_mode": model.get("mode", "chat"),
                "completion_params": model.get("completion_params", {"temperature": 0.7}),
            }
        case "code":
            return {
                "code": data.get("code", ""),
                "code_language": data.get("code_language", "python3"),
                "variables": deepcopy(data.get("variables") or []),
                "outputs": deepcopy(data.get("outputs") or {}),
            }
        case "if-else":
            return {"cases": deepcopy(data.get("cases") or [])}
        case "end":
            return {"outputs": deepcopy(data.get("outputs") or [])}
        case "http-request":
            return {
                "variables": deepcopy(data.get("variables") or []),
                "method": data.get("method", "GET"),
                "url": data.get("url", ""),
                "headers": data.get("headers", ""),
                "params": data.get("params", ""),
                "body": deepcopy(data.get("body", {"type": "none", "data": ""})),
                "ssl_verify": data.get("ssl_verify", True),
                "timeout": deepcopy(data.get("timeout")) if data.get("timeout") is not None else None,
                "retry_config": deepcopy(data.get("retry_config")) if data.get("retry_config") is not None else None,
            }
        case "template-transform":
            return {
                "template": data.get("template", ""),
                "variables": deepcopy(data.get("variables") or []),
            }
        case "question-classifier":
            model = data.get("model") if isinstance(data.get("model"), dict) else {}
            return {
                "query_variable_selector": deepcopy(data.get("query_variable_selector") or ["start", "query"]),
                "model_provider": model.get("provider"),
                "model_name": model.get("name"),
                "model_mode": model.get("mode", "chat"),
                "completion_params": model.get("completion_params", {"temperature": 0.7}),
                "classes": deepcopy(data.get("classes") or []),
                "instruction": data.get("instruction", ""),
                "vision": deepcopy(data.get("vision") or {"enabled": False, "configs": {"variable_selector": []}}),
                "memory": deepcopy(data.get("memory")),
            }
        case "parameter-extractor":
            model = data.get("model") if isinstance(data.get("model"), dict) else {}
            return {
                "query": deepcopy(data.get("query") or ["start", "query"]),
                "model_provider": model.get("provider"),
                "model_name": model.get("name"),
                "model_mode": model.get("mode", "chat"),
                "completion_params": model.get("completion_params", {"temperature": 0.7}),
                "parameters": deepcopy(data.get("parameters") or []),
                "instruction": data.get("instruction", ""),
                "reasoning_mode": data.get("reasoning_mode", "prompt"),
                "vision": deepcopy(data.get("vision") or {"enabled": False, "configs": {"variable_selector": []}}),
                "memory": deepcopy(data.get("memory")),
            }
        case "variable-aggregator":
            return {
                "variables": deepcopy(data.get("variables") or []),
                "output_type": data.get("output_type", "string"),
                "advanced_settings": deepcopy(
                    data.get("advanced_settings") or {"group_enabled": False, "groups": []}
                ),
            }
        case "document-extractor":
            return {
                "variable_selector": deepcopy(data.get("variable_selector") or ["start", "files"]),
                "is_array_file": bool(data.get("is_array_file", False)),
            }
        case "assigner":
            return {
                "version": str(data.get("version") or "2"),
                "items": deepcopy(data.get("items") or []),
            }
        case "list-operator":
            return {
                "variable": deepcopy(data.get("variable") or ["start", "items"]),
                "var_type": data.get("var_type", "array[string]"),
                "item_var_type": data.get("item_var_type", "string"),
                "filter_by": deepcopy(data.get("filter_by") or {"enabled": False, "conditions": []}),
                "extract_by": deepcopy(data.get("extract_by") or {"enabled": False, "serial": "1"}),
                "order_by": deepcopy(data.get("order_by") or {"enabled": False, "key": "", "value": "asc"}),
                "limit": deepcopy(data.get("limit") or {"enabled": False, "size": 10}),
            }
        case "knowledge-retrieval":
            return {
                "query_variable_selector": deepcopy(data.get("query_variable_selector") or ["start", "query"]),
                "query_attachment_selector": deepcopy(data.get("query_attachment_selector") or []),
                "dataset_ids": deepcopy(data.get("dataset_ids") or []),
                "retrieval_mode": data.get("retrieval_mode", "multiple"),
                "multiple_retrieval_config": deepcopy(
                    data.get("multiple_retrieval_config")
                    or {"top_k": 4, "score_threshold": None, "reranking_enable": False}
                ),
                "single_retrieval_config": deepcopy(data.get("single_retrieval_config"))
                if data.get("single_retrieval_config") is not None
                else None,
                "metadata_filtering_mode": data.get("metadata_filtering_mode", "disabled"),
                "metadata_filtering_conditions": deepcopy(data.get("metadata_filtering_conditions"))
                if data.get("metadata_filtering_conditions") is not None
                else None,
                "metadata_model_config": deepcopy(data.get("metadata_model_config"))
                if data.get("metadata_model_config") is not None
                else None,
                "vision": deepcopy(data.get("vision") or {"enabled": False, "configs": {"variable_selector": []}}),
            }
    return {}


def _start_variable(item: dict[str, Any]) -> dict[str, Any]:
    input_type = item.get("type", "paragraph")
    variable = {
        "name": item.get("name") or item.get("variable"),
        "type": input_type,
        "required": bool(item.get("required", True)),
        "label": item.get("label") or item.get("variable") or item.get("name"),
    }
    if item.get("max_length") is not None:
        variable["max_length"] = item.get("max_length")
    if isinstance(item.get("options"), list):
        variable["options"] = deepcopy(item.get("options"))
    if is_file_input_type(str(input_type)):
        variable.update(file_upload_settings(item, input_type=str(input_type)))
    return variable


def _prompt_texts(prompt_template: Any) -> tuple[str, str]:
    system_prompt = ""
    user_prompt = ""
    if isinstance(prompt_template, list):
        for item in prompt_template:
            if not isinstance(item, dict):
                continue
            role = item.get("role")
            text = str(item.get("text", ""))
            if role == "system":
                system_prompt = text
            elif role == "user":
                user_prompt = text
    return system_prompt, user_prompt


def _merge_existing_layout(graph: dict[str, Any], base_graph: dict[str, Any]) -> None:
    base_nodes = {
        str(node.get("id")): node
        for node in base_graph.get("nodes", [])
        if isinstance(node, dict) and node.get("id")
    }
    graph_nodes = graph.get("nodes", [])
    positions: dict[str, dict[str, float]] = {}
    for node in graph_nodes:
        if not isinstance(node, dict):
            continue
        node_id = str(node.get("id", ""))
        base_node = base_nodes.get(node_id)
        if base_node:
            for key in LAYOUT_KEYS:
                if key in base_node:
                    node[key] = deepcopy(base_node[key])
        else:
            node["position"] = _new_node_position(node_id, graph, positions, base_nodes)
            node["positionAbsolute"] = deepcopy(node["position"])
        if isinstance(node.get("position"), dict):
            positions[node_id] = {"x": float(node["position"].get("x", 0)), "y": float(node["position"].get("y", 0))}


def _new_node_position(
    node_id: str,
    graph: dict[str, Any],
    positions: dict[str, dict[str, float]],
    base_nodes: dict[str, dict[str, Any]],
) -> dict[str, float]:
    source_position = None
    for edge in graph.get("edges", []):
        if not isinstance(edge, dict) or edge.get("target") != node_id:
            continue
        source_id = str(edge.get("source", ""))
        if source_id in positions:
            source_position = positions[source_id]
            break
        base_position = base_nodes.get(source_id, {}).get("position")
        if isinstance(base_position, dict):
            source_position = {"x": float(base_position.get("x", 0)), "y": float(base_position.get("y", 0))}
            break
    position = {
        "x": (source_position or {"x": 80, "y": 282})["x"] + 300,
        "y": (source_position or {"x": 80, "y": 282})["y"],
    }
    occupied = {(round(item["x"]), round(item["y"])) for item in positions.values()}
    for base_node in base_nodes.values():
        base_position = base_node.get("position")
        if isinstance(base_position, dict):
            occupied.add((round(float(base_position.get("x", 0))), round(float(base_position.get("y", 0)))))
    while (round(position["x"]), round(position["y"])) in occupied:
        position["y"] += 120
    return position
