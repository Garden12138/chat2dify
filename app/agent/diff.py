from __future__ import annotations

from typing import Any

from app.models import PlanEdge, PlanNode, WorkflowPlan


def diff_plans(before: WorkflowPlan, after: WorkflowPlan) -> list[dict[str, str]]:
    changes: list[dict[str, str]] = []
    before_nodes = {node.id: node for node in before.nodes}
    after_nodes = {node.id: node for node in after.nodes}

    for node_id in sorted(after_nodes.keys() - before_nodes.keys()):
        node = after_nodes[node_id]
        changes.append(
            {
                "type": "node_added",
                "target": node_id,
                "message": f"新增 {node.type} 节点 {node.title or node.id}。",
            }
        )
    for node_id in sorted(before_nodes.keys() - after_nodes.keys()):
        node = before_nodes[node_id]
        changes.append(
            {
                "type": "node_removed",
                "target": node_id,
                "message": f"删除 {node.type} 节点 {node.title or node.id}。",
            }
        )
    for node_id in sorted(before_nodes.keys() & after_nodes.keys()):
        before_node = before_nodes[node_id]
        after_node = after_nodes[node_id]
        if _node_signature(before_node) != _node_signature(after_node):
            changes.append(
                {
                    "type": "node_updated",
                    "target": node_id,
                    "message": f"更新 {after_node.type} 节点 {after_node.title or after_node.id}。",
                }
            )

    before_edges = {_edge_key(edge) for edge in before.edges}
    after_edges = {_edge_key(edge) for edge in after.edges}
    for edge in sorted(after_edges - before_edges):
        changes.append(
            {
                "type": "edge_added",
                "target": f"{edge[0]}->{edge[2]}",
                "message": f"新增连线 {edge[0]}[{edge[1]}] -> {edge[2]}。",
            }
        )
    for edge in sorted(before_edges - after_edges):
        changes.append(
            {
                "type": "edge_removed",
                "target": f"{edge[0]}->{edge[2]}",
                "message": f"删除连线 {edge[0]}[{edge[1]}] -> {edge[2]}。",
            }
        )
    return changes


def _node_signature(node: PlanNode) -> dict[str, Any]:
    return {
        "type": node.type,
        "title": node.title,
        "desc": node.desc,
        "params": node.params,
    }


def _edge_key(edge: PlanEdge) -> tuple[str, str, str, str]:
    return (edge.source, edge.source_handle, edge.target, edge.target_handle)
