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
            issues.append(
                ValidationIssue(
                    code="PLAN_INVALID",
                    message=str(error.get("msg", error)),
                    path=".".join(str(item) for item in error.get("loc", [])) or None,
                    suggestion="修正 Plan IR 的基础图结构。",
                )
            )

    for node in plan.nodes:
        if node.type not in SUPPORTED_NODE_TYPES:
            issues.append(
                ValidationIssue(
                    code="UNSUPPORTED_NODE_TYPE",
                    message=f"Unsupported node type: {node.type}",
                    node_id=node.id,
                    path=f"nodes.{node.id}.type",
                    suggestion="仅使用第一阶段支持的 7 类节点。",
                )
            )
    issues.extend(_validate_graph_semantics(plan))
    issues.extend(_validate_node_params(plan))
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
        issues.append(
            ValidationIssue(code="DSL_KIND_INVALID", message="DSL kind must be app.", path="kind")
        )

    app = data.get("app")
    if not isinstance(app, dict) or app.get("mode") != "workflow":
        issues.append(
            ValidationIssue(code="DSL_APP_MODE_INVALID", message="DSL app.mode must be workflow.", path="app.mode")
        )

    workflow = data.get("workflow")
    graph: Any = workflow.get("graph") if isinstance(workflow, dict) else None
    if not isinstance(graph, dict):
        issues.append(
            ValidationIssue(code="DSL_GRAPH_MISSING", message="DSL workflow.graph is required.", path="workflow.graph")
        )
        return issues

    nodes = graph.get("nodes")
    edges = graph.get("edges")
    if not isinstance(nodes, list):
        issues.append(
            ValidationIssue(code="DSL_NODES_INVALID", message="workflow.graph.nodes must be a list.", path="workflow.graph.nodes")
        )
    if not isinstance(edges, list):
        issues.append(
            ValidationIssue(code="DSL_EDGES_INVALID", message="workflow.graph.edges must be a list.", path="workflow.graph.edges")
        )

    if isinstance(nodes, list):
        node_ids = {node.get("id") for node in nodes if isinstance(node, dict)}
        node_types = {node.get("id"): node.get("data", {}).get("type") for node in nodes if isinstance(node, dict)}
        if "start" not in set(node_types.values()):
            issues.append(
                ValidationIssue(code="DSL_START_MISSING", message="workflow graph must contain a start node.")
            )
        if "end" not in set(node_types.values()):
            issues.append(
                ValidationIssue(code="DSL_END_MISSING", message="workflow graph must contain an end node.")
            )
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


def has_errors(issues: list[ValidationIssue]) -> bool:
    return any(issue.severity == "error" for issue in issues)


def _validate_graph_semantics(plan: WorkflowPlan) -> list[ValidationIssue]:
    issues: list[ValidationIssue] = []
    node_by_id = {node.id: node for node in plan.nodes}
    incoming: dict[str, list[str]] = {node.id: [] for node in plan.nodes}
    outgoing: dict[str, list[str]] = {node.id: [] for node in plan.nodes}
    for edge in plan.edges:
        outgoing.setdefault(edge.source, []).append(edge.source_handle)
        incoming.setdefault(edge.target, []).append(edge.source)

    for node in plan.nodes:
        if node.type == "start" and incoming.get(node.id):
            issues.append(
                ValidationIssue(
                    code="PLAN_START_HAS_INCOMING_EDGE",
                    message="start node cannot have incoming edges.",
                    node_id=node.id,
                    path=f"nodes.{node.id}",
                    suggestion="删除指向 start 节点的连线。",
                )
            )
        if node.type == "end" and outgoing.get(node.id):
            issues.append(
                ValidationIssue(
                    code="PLAN_END_HAS_OUTGOING_EDGE",
                    message="end node cannot have outgoing edges.",
                    node_id=node.id,
                    path=f"nodes.{node.id}",
                    suggestion="删除 end 节点之后的连线，或改用非 end 节点承接后续步骤。",
                )
            )

    for node in plan.nodes:
        if node.type != "if-else":
            continue
        source_edges = [edge for edge in plan.edges if edge.source == node.id]
        handles = [edge.source_handle for edge in source_edges]
        duplicate_handles = sorted({handle for handle in handles if handles.count(handle) > 1})
        for handle in duplicate_handles:
            issues.append(
                ValidationIssue(
                    code="PLAN_IF_ELSE_DUPLICATE_BRANCH",
                    message=f"if-else branch handle is duplicated: {handle}",
                    node_id=node.id,
                    path=f"edges.{node.id}.{handle}",
                    suggestion="每个 if-else 分支只保留一条出边。",
                )
            )

        case_ids = [str(case.get("case_id")) for case in node.params.get("cases", []) if case.get("case_id")]
        for case_id in case_ids:
            if case_id not in handles:
                issues.append(
                    ValidationIssue(
                        code="PLAN_IF_ELSE_CASE_EDGE_MISSING",
                        message=f"if-else case has no outgoing edge: {case_id}",
                        node_id=node.id,
                        path=f"nodes.{node.id}.params.cases",
                        suggestion=f"添加 source_handle 为 {case_id} 的出边。",
                    )
                )
        if "false" not in handles:
            issues.append(
                ValidationIssue(
                    code="PLAN_IF_ELSE_FALSE_EDGE_MISSING",
                    message="if-else node must have a false outgoing edge.",
                    node_id=node.id,
                    path=f"nodes.{node.id}.edges",
                    suggestion='添加 else 分支出边，并将 source_handle 设置为 "false"。',
                )
            )
        valid_handles = {*case_ids, "false"}
        for edge in source_edges:
            if edge.source_handle not in valid_handles:
                target_type = node_by_id.get(edge.target).type if node_by_id.get(edge.target) else "unknown"
                issues.append(
                    ValidationIssue(
                        code="PLAN_IF_ELSE_BRANCH_INVALID",
                        message=f"if-else edge uses invalid source_handle: {edge.source_handle} -> {target_type}",
                        node_id=node.id,
                        path=f"edges.{edge.source}.{edge.target}.source_handle",
                        suggestion="source_handle 必须匹配 case_id，else 分支必须为 false。",
                    )
                )
    return issues


def _validate_node_params(plan: WorkflowPlan) -> list[ValidationIssue]:
    issues: list[ValidationIssue] = []
    for node in plan.nodes:
        params = node.params
        match node.type:
            case "start":
                variables = params.get("variables")
                if not isinstance(variables, list) or not variables:
                    issues.append(_node_issue("PLAN_START_VARIABLES_MISSING", "start node requires variables.", node.id, "params.variables"))
                else:
                    for idx, item in enumerate(variables):
                        if not isinstance(item, dict) or not (item.get("name") or item.get("variable")):
                            issues.append(_node_issue("PLAN_START_VARIABLE_INVALID", "start variable requires name.", node.id, f"params.variables.{idx}"))
            case "llm":
                if not params.get("user_prompt"):
                    issues.append(_node_issue("PLAN_LLM_PROMPT_MISSING", "llm node requires user_prompt.", node.id, "params.user_prompt"))
            case "code":
                if not params.get("code"):
                    issues.append(_node_issue("PLAN_CODE_MISSING", "code node requires code.", node.id, "params.code"))
                if not isinstance(params.get("outputs"), dict) or not params.get("outputs"):
                    issues.append(_node_issue("PLAN_CODE_OUTPUTS_MISSING", "code node requires outputs.", node.id, "params.outputs"))
            case "if-else":
                cases = params.get("cases")
                if not isinstance(cases, list) or not cases:
                    issues.append(_node_issue("PLAN_IF_ELSE_CASES_MISSING", "if-else node requires cases.", node.id, "params.cases"))
                for idx, case in enumerate(cases or []):
                    if not isinstance(case, dict) or not case.get("case_id"):
                        issues.append(_node_issue("PLAN_IF_ELSE_CASE_INVALID", "if-else case requires case_id.", node.id, f"params.cases.{idx}"))
                    conditions = case.get("conditions") if isinstance(case, dict) else None
                    if not isinstance(conditions, list) or not conditions:
                        issues.append(_node_issue("PLAN_IF_ELSE_CONDITIONS_MISSING", "if-else case requires conditions.", node.id, f"params.cases.{idx}.conditions"))
            case "end":
                outputs = params.get("outputs")
                if not isinstance(outputs, list) or not outputs:
                    issues.append(_node_issue("PLAN_END_OUTPUTS_MISSING", "end node requires outputs.", node.id, "params.outputs"))
                for idx, output in enumerate(outputs or []):
                    if not isinstance(output, dict) or not output.get("variable") or not output.get("value_selector"):
                        issues.append(_node_issue("PLAN_END_OUTPUT_INVALID", "end output requires variable and value_selector.", node.id, f"params.outputs.{idx}"))
            case "http-request":
                if not params.get("url"):
                    issues.append(_node_issue("PLAN_HTTP_URL_MISSING", "http-request node requires url.", node.id, "params.url"))
            case "template-transform":
                if not params.get("template"):
                    issues.append(_node_issue("PLAN_TEMPLATE_MISSING", "template-transform node requires template.", node.id, "params.template"))
    return issues


def _node_issue(code: str, message: str, node_id: str, path: str) -> ValidationIssue:
    return ValidationIssue(
        code=code,
        message=message,
        node_id=node_id,
        path=f"nodes.{node_id}.{path}",
        suggestion="让 normalizer 补齐字段，或让 planner 重新生成该节点参数。",
    )


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
                        path=f"nodes.{node.id}.params",
                        suggestion="修正变量引用中的节点 ID。",
                    )
                )
            elif variable and outputs[node_id] and variable not in outputs[node_id]:
                issues.append(
                    ValidationIssue(
                        code="PLAN_VARIABLE_UNKNOWN",
                        message=f"Variable selector references unknown output: {node_id}.{variable}",
                        node_id=node.id,
                        path=f"nodes.{node.id}.params",
                        suggestion="引用已存在的输出变量，或调整上游节点输出声明。",
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
