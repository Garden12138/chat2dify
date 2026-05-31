from __future__ import annotations

from typing import Any

from app.models import PlanEdge, PlanNode, WorkflowPlan


def diff_plans(before: WorkflowPlan, after: WorkflowPlan) -> list[dict[str, Any]]:
    changes: list[dict[str, Any]] = []
    before_nodes = {node.id: node for node in before.nodes}
    after_nodes = {node.id: node for node in after.nodes}

    for node_id in sorted(after_nodes.keys() - before_nodes.keys()):
        node = after_nodes[node_id]
        changes.append(
            {
                "type": "node_added",
                "target": node_id,
                "node_type": node.type,
                "title": node.title or node.id,
                "message": f"新增 {node.type} 节点 {node.title or node.id}。",
            }
        )
    for node_id in sorted(before_nodes.keys() - after_nodes.keys()):
        node = before_nodes[node_id]
        changes.append(
            {
                "type": "node_removed",
                "target": node_id,
                "node_type": node.type,
                "title": node.title or node.id,
                "message": f"删除 {node.type} 节点 {node.title or node.id}。",
            }
        )
    for node_id in sorted(before_nodes.keys() & after_nodes.keys()):
        before_node = before_nodes[node_id]
        after_node = after_nodes[node_id]
        changes.extend(_node_changes(before_node, after_node))

    before_edges = {_edge_key(edge) for edge in before.edges}
    after_edges = {_edge_key(edge) for edge in after.edges}
    for edge in sorted(after_edges - before_edges):
        changes.append(
            {
                "type": "edge_added",
                "target": f"{edge[0]}->{edge[2]}",
                "source": edge[0],
                "source_handle": edge[1],
                "target_node": edge[2],
                "target_handle": edge[3],
                "message": f"新增连线 {edge[0]}[{edge[1]}] -> {edge[2]}。",
            }
        )
    for edge in sorted(before_edges - after_edges):
        changes.append(
            {
                "type": "edge_removed",
                "target": f"{edge[0]}->{edge[2]}",
                "source": edge[0],
                "source_handle": edge[1],
                "target_node": edge[2],
                "target_handle": edge[3],
                "message": f"删除连线 {edge[0]}[{edge[1]}] -> {edge[2]}。",
            }
        )
    return changes


def _node_changes(before: PlanNode, after: PlanNode) -> list[dict[str, Any]]:
    changes: list[dict[str, Any]] = []
    base = {
        "target": after.id,
        "node_type": after.type,
        "title": after.title or after.id,
    }
    for field in ("type", "title", "desc"):
        before_value = getattr(before, field)
        after_value = getattr(after, field)
        if before_value != after_value:
            changes.append(
                {
                    **base,
                    "type": "node_updated",
                    "field": field,
                    "before": before_value,
                    "after": after_value,
                    "message": f"更新 {after.type} 节点 {after.title or after.id} 的 {field}。",
                }
            )

    before_params = before.params or {}
    after_params = after.params or {}
    for field in sorted(set(before_params) | set(after_params)):
        before_value = before_params.get(field)
        after_value = after_params.get(field)
        if before_value == after_value:
            continue
        changes.append(
            {
                **base,
                "type": _param_change_type(after.type, field),
                "field": f"params.{field}",
                "before": before_value,
                "after": after_value,
                "message": _param_change_message(after, field),
            }
        )
    return changes


def _param_change_type(node_type: str, field: str) -> str:
    if node_type == "llm" and field in {"system_prompt", "user_prompt"}:
        return "prompt_changed"
    if node_type == "llm" and field in {"model_provider", "model_name", "model_mode", "completion_params"}:
        return "model_changed"
    if node_type == "template-transform" and field == "template":
        return "template_changed"
    if node_type == "code" and field in {"code", "code_language"}:
        return "code_changed"
    if node_type == "http-request" and field in {"method", "url", "headers", "params", "body"}:
        return "http_changed"
    if node_type == "question-classifier" and field in {"classes", "instruction", "query_variable_selector"}:
        return "classifier_changed"
    if node_type == "parameter-extractor" and field in {"parameters", "instruction", "query", "reasoning_mode"}:
        return "extractor_changed"
    if node_type == "variable-aggregator" and field in {"variables", "output_type", "advanced_settings"}:
        return "aggregator_changed"
    if node_type == "document-extractor" and field in {"variable_selector", "is_array_file"}:
        return "document_extractor_changed"
    if node_type == "assigner" and field in {"items", "version"}:
        return "assigner_changed"
    if node_type == "list-operator" and field in {"variable", "filter_by", "extract_by", "order_by", "limit"}:
        return "list_operator_changed"
    if node_type == "knowledge-retrieval" and field in {
        "query_variable_selector",
        "query_attachment_selector",
        "dataset_ids",
        "retrieval_mode",
        "multiple_retrieval_config",
        "metadata_filtering_mode",
    }:
        return "knowledge_retrieval_changed"
    return "node_params_updated"


def _param_change_message(node: PlanNode, field: str) -> str:
    title = node.title or node.id
    match _param_change_type(node.type, field):
        case "prompt_changed":
            return f"更新 LLM 节点 {title} 的提示词。"
        case "model_changed":
            return f"更新 LLM 节点 {title} 的模型配置。"
        case "template_changed":
            return f"更新模板节点 {title} 的模板内容。"
        case "code_changed":
            return f"更新代码节点 {title} 的代码配置。"
        case "http_changed":
            return f"更新 HTTP 节点 {title} 的 {field}。"
        case "classifier_changed":
            return f"更新分类节点 {title} 的 {field}。"
        case "extractor_changed":
            return f"更新参数提取节点 {title} 的 {field}。"
        case "aggregator_changed":
            return f"更新变量聚合节点 {title} 的 {field}。"
        case "document_extractor_changed":
            return f"更新文档提取节点 {title} 的 {field}。"
        case "assigner_changed":
            return f"更新变量赋值节点 {title} 的 {field}。"
        case "list_operator_changed":
            return f"更新列表处理节点 {title} 的 {field}。"
        case "knowledge_retrieval_changed":
            return f"更新知识库检索节点 {title} 的 {field}。"
    return f"更新 {node.type} 节点 {title} 的 {field}。"


def _edge_key(edge: PlanEdge) -> tuple[str, str, str, str]:
    return (edge.source, edge.source_handle, edge.target, edge.target_handle)
