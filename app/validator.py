from __future__ import annotations

import re
from typing import Any

import yaml
from pydantic import ValidationError

from app.models import ValidationIssue, WorkflowPlan


SUPPORTED_NODE_TYPES = {"start", "llm", "code", "if-else", "end", "http-request", "template-transform"}
TEMPLATE_REF_RE = re.compile(r"\{\{#([A-Za-z0-9_-]+)\.([A-Za-z0-9_.-]+)#\}\}")
BARE_TEMPLATE_REF_RE = re.compile(r"\{\{\s*(?!#)([A-Za-z0-9_-]+)\.([A-Za-z0-9_.-]+)\s*\}\}")


def validate_plan(plan: WorkflowPlan) -> list[ValidationIssue]:
    issues: list[ValidationIssue] = []
    try:
        WorkflowPlan.model_validate(plan.model_dump())
    except ValidationError as exc:
        for error in exc.errors():
            issues.append(ValidationIssue(code="PLAN_INVALID", message=str(error.get("msg", error))))

    for node in plan.nodes:
        if node.type not in SUPPORTED_NODE_TYPES:
            issues.append(
                ValidationIssue(code="UNSUPPORTED_NODE_TYPE", message=f"Unsupported node type: {node.type}", node_id=node.id)
            )
    issues.extend(_validate_plan_variable_references(plan))
    return issues


def validate_dsl(yaml_content: str, *, expected_dsl_version: str | None = None) -> list[ValidationIssue]:
    issues: list[ValidationIssue] = []
    try:
        data = yaml.safe_load(yaml_content)
    except yaml.YAMLError as exc:
        return [ValidationIssue(code="DSL_YAML_INVALID", message=str(exc))]

    if not isinstance(data, dict):
        return [ValidationIssue(code="DSL_INVALID", message="DSL root must be a mapping.")]

    if expected_dsl_version and str(data.get("version")) != expected_dsl_version:
        issues.append(
            ValidationIssue(
                code="DSL_VERSION_MISMATCH",
                message=f"Expected DSL version {expected_dsl_version}, got {data.get('version')}.",
            )
        )

    if data.get("kind") != "app":
        issues.append(ValidationIssue(code="DSL_KIND_INVALID", message="DSL kind must be app."))

    app = data.get("app")
    if not isinstance(app, dict) or app.get("mode") != "workflow":
        issues.append(ValidationIssue(code="DSL_APP_MODE_INVALID", message="DSL app.mode must be workflow."))

    workflow = data.get("workflow")
    graph: Any = workflow.get("graph") if isinstance(workflow, dict) else None
    if not isinstance(graph, dict):
        issues.append(ValidationIssue(code="DSL_GRAPH_MISSING", message="DSL workflow.graph is required."))
        return issues

    nodes = graph.get("nodes")
    edges = graph.get("edges")
    if not isinstance(nodes, list):
        issues.append(ValidationIssue(code="DSL_NODES_INVALID", message="workflow.graph.nodes must be a list."))
    if not isinstance(edges, list):
        issues.append(ValidationIssue(code="DSL_EDGES_INVALID", message="workflow.graph.edges must be a list."))

    if isinstance(nodes, list):
        node_ids = {node.get("id") for node in nodes if isinstance(node, dict)}
        node_types = {node.get("id"): node.get("data", {}).get("type") for node in nodes if isinstance(node, dict)}
        if "start" not in set(node_types.values()):
            issues.append(ValidationIssue(code="DSL_START_MISSING", message="workflow graph must contain a start node."))
        if "end" not in set(node_types.values()):
            issues.append(ValidationIssue(code="DSL_END_MISSING", message="workflow graph must contain an end node."))
        if isinstance(edges, list):
            for edge in edges:
                if not isinstance(edge, dict):
                    continue
                if edge.get("source") not in node_ids or edge.get("target") not in node_ids:
                    issues.append(
                        ValidationIssue(
                            code="DSL_EDGE_INVALID",
                            message=f"Edge references missing node: {edge.get('id')}",
                        )
                    )
    return issues


def _validate_plan_variable_references(plan: WorkflowPlan) -> list[ValidationIssue]:
    outputs = _known_outputs(plan)
    issues: list[ValidationIssue] = []
    for node in plan.nodes:
        for selector in _selectors_in_value(node.params):
            node_id = selector[0]
            variable = selector[1] if len(selector) > 1 else ""
            if node_id not in outputs:
                issues.append(
                    ValidationIssue(
                        code="PLAN_VARIABLE_NODE_UNKNOWN",
                        message=f"Variable selector references unknown node: {node_id}",
                        node_id=node.id,
                    )
                )
            elif variable and outputs[node_id] and variable not in outputs[node_id]:
                issues.append(
                    ValidationIssue(
                        code="PLAN_VARIABLE_UNKNOWN",
                        message=f"Variable selector references unknown output: {node_id}.{variable}",
                        node_id=node.id,
                    )
                )
    return issues


def _known_outputs(plan: WorkflowPlan) -> dict[str, set[str]]:
    outputs: dict[str, set[str]] = {}
    for node in plan.nodes:
        match node.type:
            case "start":
                names = {
                    str(item.get("name") or item.get("variable"))
                    for item in node.params.get("variables") or node.params.get("inputs", [])
                    if item.get("name") or item.get("variable")
                }
                outputs[node.id] = names or {"query"}
            case "llm":
                outputs[node.id] = {"text"}
            case "code":
                declared = node.params.get("outputs") or {"result": {"type": "string", "children": None}}
                outputs[node.id] = set(declared.keys()) if isinstance(declared, dict) else set()
            case "http-request":
                outputs[node.id] = {"body", "status_code", "headers"}
            case "template-transform":
                outputs[node.id] = {"output"}
            case "end" | "if-else":
                outputs[node.id] = set()
    return outputs


def _selectors_in_value(value: Any) -> list[list[str]]:
    selectors: list[list[str]] = []
    if isinstance(value, dict):
        for key, child in value.items():
            if key in {"value_selector", "variable_selector"} and isinstance(child, list) and child:
                selectors.append([str(item) for item in child])
            else:
                selectors.extend(_selectors_in_value(child))
    elif isinstance(value, list):
        for item in value:
            selectors.extend(_selectors_in_value(item))
    elif isinstance(value, str):
        selectors.extend([[match.group(1), match.group(2)] for match in TEMPLATE_REF_RE.finditer(value)])
        selectors.extend([[match.group(1), match.group(2)] for match in BARE_TEMPLATE_REF_RE.finditer(value)])
    return selectors
