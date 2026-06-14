from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal

from app.models import WorkflowPlan


RiskLevel = Literal["low", "medium", "high"]
DESTRUCTIVE_DELETE_RATIO = 0.3


@dataclass(frozen=True)
class ChangeGuardIssue:
    code: str
    message: str
    severity: Literal["warning", "error"] = "warning"
    suggestion: str | None = None
    details: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "code": self.code,
            "message": self.message,
            "severity": self.severity,
            "suggestion": self.suggestion,
            "details": self.details,
        }


@dataclass(frozen=True)
class ChangeGuardResult:
    ok: bool
    risk: RiskLevel
    no_op: bool
    issues: list[ChangeGuardIssue] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "ok": self.ok,
            "risk": self.risk,
            "no_op": self.no_op,
            "issues": [issue.to_dict() for issue in self.issues],
        }


def guard_plan_change(before: WorkflowPlan, after: WorkflowPlan, changes: list[dict[str, Any]]) -> ChangeGuardResult:
    issues: list[ChangeGuardIssue] = []
    if not changes:
        return ChangeGuardResult(
            ok=True,
            risk="low",
            no_op=True,
            issues=[
                ChangeGuardIssue(
                    code="PLAN_CHANGE_NOOP",
                    message="修改请求没有造成 workflow 实质变化。",
                    suggestion="如果需要写回 Dify，请补充更明确的修改要求。",
                )
            ],
        )

    before_nodes = {node.id: node for node in before.nodes}
    after_nodes = {node.id: node for node in after.nodes}
    removed_ids = sorted(before_nodes.keys() - after_nodes.keys())
    added_ids = sorted(after_nodes.keys() - before_nodes.keys())
    removed_ratio = len(removed_ids) / max(1, len(before_nodes))

    start_before = {node.id for node in before.nodes if node.type == "start"}
    start_after = {node.id for node in after.nodes if node.type == "start"}
    if start_before != start_after:
        issues.append(
            ChangeGuardIssue(
                code="PLAN_CHANGE_START_CHANGED",
                message="修改改变了 start 节点，可能破坏调用方输入契约。",
                severity="error",
                suggestion="确认确实要变更输入契约后，使用 allow_destructive=true。",
                details={"before": sorted(start_before), "after": sorted(start_after)},
            )
        )

    removed_terminal_ids = [
        node_id
        for node_id in removed_ids
        if before_nodes[node_id].type in {"start", "end", "answer"}
    ]
    if removed_terminal_ids:
        issues.append(
            ChangeGuardIssue(
                code="PLAN_CHANGE_TERMINAL_NODE_REMOVED",
                message="修改删除了 start/end/answer 关键节点。",
                severity="error",
                suggestion="确认确实要删除关键节点后，使用 allow_destructive=true。",
                details={"removed_node_ids": removed_terminal_ids},
            )
        )

    if removed_ids and removed_ratio >= DESTRUCTIVE_DELETE_RATIO:
        issues.append(
            ChangeGuardIssue(
                code="PLAN_CHANGE_MASS_NODE_REMOVAL",
                message=f"修改删除了 {len(removed_ids)} 个节点，占原 workflow 的 {removed_ratio:.0%}。",
                severity="error",
                suggestion="默认安全模式会阻断大比例删除；确认后可传 allow_destructive=true。",
                details={"removed_node_ids": removed_ids, "removed_ratio": removed_ratio},
            )
        )

    edge_removed = [change for change in changes if change.get("type") == "edge_removed"]
    edge_added = [change for change in changes if change.get("type") == "edge_added"]
    if edge_removed and len(edge_removed) >= max(2, len(before.edges) // 2):
        issues.append(
            ChangeGuardIssue(
                code="PLAN_CHANGE_MANY_EDGES_REMOVED",
                message=f"修改删除了 {len(edge_removed)} 条连线，可能是整图重写。",
                severity="error",
                suggestion="确认确实要重构连线后，使用 allow_destructive=true。",
                details={"removed_edges": [change.get("target") for change in edge_removed]},
            )
        )
    elif edge_removed or edge_added:
        issues.append(
            ChangeGuardIssue(
                code="PLAN_CHANGE_EDGES_UPDATED",
                message="修改调整了 workflow 连线。",
                severity="warning",
                details={
                    "removed_count": len(edge_removed),
                    "added_count": len(edge_added),
                },
            )
        )

    if added_ids:
        issues.append(
            ChangeGuardIssue(
                code="PLAN_CHANGE_NODES_ADDED",
                message=f"修改新增了 {len(added_ids)} 个节点。",
                severity="warning",
                details={"added_node_ids": added_ids},
            )
        )

    variable_removed = [
        change
        for change in changes
        if change.get("type") == "conversation_variable_removed"
    ]
    variable_destructive_updates = [
        change
        for change in changes
        if change.get("type") == "conversation_variable_updated"
        and change.get("field") in {"name", "value_type"}
    ]
    if variable_removed or variable_destructive_updates:
        issues.append(
            ChangeGuardIssue(
                code="PLAN_CHANGE_CONVERSATION_VARIABLE_DESTRUCTIVE",
                message="修改删除、重命名或改变了会话变量类型，可能破坏已有跨轮状态。",
                severity="error",
                suggestion="确认需要迁移或丢弃已有状态后，使用 allow_destructive=true。",
                details={
                    "removed": [
                        change.get("name")
                        for change in variable_removed
                    ],
                    "updated": [
                        {
                            "name": change.get("name"),
                            "field": change.get("field"),
                            "before": change.get("before"),
                            "after": change.get("after"),
                        }
                        for change in variable_destructive_updates
                    ],
                },
            )
        )

    variable_added = [
        change
        for change in changes
        if change.get("type") == "conversation_variable_added"
    ]
    variable_safe_updates = [
        change
        for change in changes
        if change.get("type") == "conversation_variable_updated"
        and change.get("field") not in {"name", "value_type"}
    ]
    if variable_added or variable_safe_updates:
        issues.append(
            ChangeGuardIssue(
                code="PLAN_CHANGE_CONVERSATION_VARIABLES_UPDATED",
                message="修改新增或更新了 Chatflow 会话变量。",
                severity="warning",
                details={
                    "added": [
                        change.get("name")
                        for change in variable_added
                    ],
                    "updated": [
                        {
                            "name": change.get("name"),
                            "field": change.get("field"),
                        }
                        for change in variable_safe_updates
                    ],
                },
            )
        )

    high_risk = any(issue.severity == "error" for issue in issues)
    medium_risk = any(issue.severity == "warning" for issue in issues)
    risk: RiskLevel = "high" if high_risk else "medium" if medium_risk else "low"
    return ChangeGuardResult(ok=not high_risk, risk=risk, no_op=False, issues=issues)
