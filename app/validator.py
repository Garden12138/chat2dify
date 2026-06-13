from __future__ import annotations

import re
from typing import Any
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

import yaml
from pydantic import ValidationError

from app.list_operator import DIFY_LIST_COMPARISON_OPERATORS
from app.models import ENTRY_NODE_TYPES, PlanNode, ValidationIssue, WorkflowPlan


SUPPORTED_NODE_TYPES = {
    "start",
    "llm",
    "code",
    "if-else",
    "end",
    "answer",
    "http-request",
    "template-transform",
    "question-classifier",
    "parameter-extractor",
    "variable-aggregator",
    "document-extractor",
    "assigner",
    "list-operator",
    "knowledge-retrieval",
    "human-input",
    "iteration",
    "iteration-start",
    "loop",
    "loop-start",
    "loop-end",
    "tool",
    "agent",
    "datasource",
    "datasource-empty",
    "knowledge-index",
    "trigger-webhook",
    "trigger-plugin",
    "trigger-schedule",
}
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
TEMPLATE_REF_RE = re.compile(r"\{\{#([A-Za-z0-9_-]+)\.([A-Za-z0-9_.-]+)#\}\}")
BARE_TEMPLATE_REF_RE = re.compile(r"\{\{\s*(?!#)([A-Za-z0-9_-]+)\.([A-Za-z0-9_.-]+)\s*\}\}")
SYSTEM_OUTPUTS = {
    "app_id",
    "batch",
    "conversation_id",
    "dataset_id",
    "datasource_info",
    "datasource_type",
    "dialogue_count",
    "document_id",
    "files",
    "invoke_from",
    "original_document_id",
    "query",
    "timestamp",
    "user_id",
    "workflow_id",
    "workflow_run_id",
}
GENERIC_TITLE_RE = re.compile(r"[\s_\-]+")
GENERIC_TITLES = {
    "",
    "node",
    "start",
    "begin",
    "input",
    "llm",
    "model",
    "code",
    "end",
    "answer",
    "output",
    "ifelse",
    "if",
    "condition",
    "branch",
    "httprequest",
    "http",
    "template",
    "templatetransform",
    "questionclassifier",
    "classifier",
    "parameterextractor",
    "extractor",
    "variableaggregator",
    "aggregator",
    "variableassigner",
    "assigner",
    "documentextractor",
    "docextractor",
    "listoperator",
    "listfilter",
    "knowledgeretrieval",
    "knowledge",
    "retrieval",
    "rag",
    "humaninput",
    "human",
    "manualinput",
    "approval",
    "iteration",
    "iterationstart",
    "loop",
    "loopstart",
    "loopend",
    "tool",
    "agent",
    "datasource",
    "datasourceempty",
    "knowledgeindex",
    "triggerwebhook",
    "triggerplugin",
    "triggerschedule",
    "开始",
    "开始节点",
    "输入",
    "大模型",
    "模型",
    "代码",
    "结束",
    "结束节点",
    "输出",
    "判断",
    "条件",
    "分支",
    "接口",
    "模板",
    "问题分类",
    "分类器",
    "参数提取",
    "提取器",
    "变量聚合",
    "变量赋值",
    "文档提取",
    "列表处理",
    "列表过滤",
    "知识库检索",
    "知识检索",
    "检索",
    "人工介入",
    "人工输入",
    "人工审核",
    "人工审批",
    "循环",
    "遍历",
    "批量处理",
    "循环开始",
    "循环结束",
    "工具",
    "智能体",
    "数据源",
    "知识库写入",
    "触发器",
    "webhook触发器",
}

PARAMETER_EXTRACTOR_TYPES = {
    "string",
    "number",
    "boolean",
    "select",
    "array[string]",
    "array[number]",
    "array[object]",
    "array[boolean]",
}


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
                    suggestion="仅使用当前支持的 workflow 节点；tool 节点需要 Web UI/API 传入 tool_selections，其余外部依赖节点只用于已有 Dify 草稿兼容。",
                )
            )
    issues.extend(_validate_graph_semantics(plan))
    issues.extend(_validate_node_params(plan))
    issues.extend(_validate_node_quality(plan))
    issues.extend(_validate_chatflow_system_references(plan))
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
    app_mode = app.get("mode") if isinstance(app, dict) else None
    if app_mode not in {"workflow", "advanced-chat"}:
        issues.append(
            ValidationIssue(
                code="DSL_APP_MODE_INVALID",
                message="DSL app.mode must be workflow or advanced-chat.",
                path="app.mode",
            )
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
        types = set(node_types.values())
        entry_types = {"start", "datasource", "trigger-webhook", "trigger-plugin", "trigger-schedule"}
        if app_mode == "advanced-chat":
            if list(node_types.values()).count("start") != 1:
                issues.append(
                    ValidationIssue(
                        code="DSL_CHATFLOW_START_INVALID",
                        message="advanced-chat graph must contain exactly one start node.",
                    )
                )
            if not types.isdisjoint({"end", "trigger-webhook", "trigger-plugin", "trigger-schedule", "datasource"}):
                issues.append(
                    ValidationIssue(
                        code="DSL_CHATFLOW_NODE_INVALID",
                        message="advanced-chat graph contains a workflow-only entry or end node.",
                    )
                )
            if "answer" not in types:
                issues.append(
                    ValidationIssue(
                        code="DSL_ANSWER_MISSING",
                        message="advanced-chat graph must contain an answer node.",
                    )
                )
        else:
            if not (types & entry_types):
                issues.append(
                    ValidationIssue(code="DSL_ENTRY_MISSING", message="workflow graph must contain an entry node.")
                )
            if "end" not in types:
                issues.append(
                    ValidationIssue(code="DSL_END_MISSING", message="workflow graph must contain an end node.")
                )
            if "answer" in types:
                issues.append(
                    ValidationIssue(
                        code="DSL_WORKFLOW_ANSWER_INVALID",
                        message="workflow graph cannot contain answer nodes.",
                    )
                )
        if isinstance(edges, list):
            edge_pairs: list[tuple[str, str]] = []
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
                    continue
                edge_pairs.append((str(edge.get("source")), str(edge.get("target"))))
            if app_mode == "advanced-chat":
                answer_ids = {
                    str(node_id)
                    for node_id, node_type in node_types.items()
                    if node_type == "answer"
                }
                answer_sources = {source for source, _target in edge_pairs if source in answer_ids}
                for answer_id in sorted(answer_sources):
                    issues.append(
                        ValidationIssue(
                            code="DSL_ANSWER_HAS_OUTGOING_EDGE",
                            message="advanced-chat answer nodes cannot have outgoing edges.",
                            node_id=answer_id,
                            path=f"workflow.graph.nodes.{answer_id}",
                        )
                    )
                unresolved = _nodes_without_answer_path(
                    {str(node_id) for node_id in node_ids if node_id is not None},
                    answer_ids,
                    edge_pairs,
                )
                for node_id in _response_path_issue_nodes(unresolved, edge_pairs):
                    issues.append(
                        ValidationIssue(
                            code="DSL_CHATFLOW_PATH_WITHOUT_ANSWER",
                            message=f"advanced-chat response path cannot reach an answer node: {node_id}",
                            node_id=node_id,
                            path=f"workflow.graph.nodes.{node_id}",
                        )
                    )
                for node_id in sorted(_cyclic_nodes(edge_pairs))[:1]:
                    issues.append(
                        ValidationIssue(
                            code="DSL_CHATFLOW_CYCLE_INVALID",
                            message=f"advanced-chat top-level graph contains a cycle: {node_id}",
                            node_id=node_id,
                            path=f"workflow.graph.nodes.{node_id}",
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
    edge_pairs: list[tuple[str, str]] = []
    for edge in plan.edges:
        outgoing.setdefault(edge.source, []).append(edge.source_handle)
        incoming.setdefault(edge.target, []).append(edge.source)
        edge_pairs.append((edge.source, edge.target))

    start_nodes = [node for node in plan.nodes if node.type == "start"]
    trigger_nodes = [
        node
        for node in plan.nodes
        if node.type in {"trigger-webhook", "trigger-plugin", "trigger-schedule"}
    ]
    if start_nodes and trigger_nodes:
        issues.append(
            ValidationIssue(
                code="PLAN_START_TRIGGER_CONFLICT",
                message="start node cannot coexist with trigger entry nodes.",
                path="nodes",
                suggestion="保留普通 start，或改为 Webhook/定时触发入口，二者只能选一种。",
            )
        )

    for node in plan.nodes:
        if node.type in ENTRY_NODE_TYPES and incoming.get(node.id):
            issues.append(
                ValidationIssue(
                    code="PLAN_START_HAS_INCOMING_EDGE" if node.type == "start" else "PLAN_ENTRY_HAS_INCOMING_EDGE",
                    message=f"{node.type} entry node cannot have incoming edges.",
                    node_id=node.id,
                    path=f"nodes.{node.id}",
                    suggestion="删除指向 entry 节点的连线。",
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
        if node.type == "answer" and outgoing.get(node.id):
            issues.append(
                ValidationIssue(
                    code="PLAN_ANSWER_HAS_OUTGOING_EDGE",
                    message="answer node cannot have outgoing edges.",
                    node_id=node.id,
                    path=f"nodes.{node.id}",
                    suggestion="删除 answer 节点之后的连线；Chatflow 回复路径必须在 answer 结束。",
                )
            )
        if plan.app_mode == "advanced-chat" and node.type in {
            "end",
            "trigger-webhook",
            "trigger-plugin",
            "trigger-schedule",
            "datasource",
            "datasource-empty",
            "knowledge-index",
        }:
            issues.append(
                ValidationIssue(
                    code="PLAN_CHATFLOW_NODE_INVALID",
                    message=f"advanced-chat does not support node type: {node.type}",
                    node_id=node.id,
                    path=f"nodes.{node.id}.type",
                    suggestion="使用 start 作为入口，并使用 answer 输出对话回复。",
                )
            )

    if plan.app_mode == "advanced-chat":
        answer_ids = {node.id for node in plan.nodes if node.type == "answer"}
        unresolved = _nodes_without_answer_path(
            {node.id for node in plan.nodes},
            answer_ids,
            edge_pairs,
        )
        for node_id in _response_path_issue_nodes(unresolved, edge_pairs):
            issues.append(
                ValidationIssue(
                    code="PLAN_CHATFLOW_PATH_WITHOUT_ANSWER",
                    message=f"Chatflow response path cannot reach an answer node: {node_id}",
                    node_id=node_id,
                    path=f"nodes.{node_id}",
                    suggestion="将该路径连接到 answer 节点，或删除无响应的分支。",
                )
            )
        for node_id in sorted(_cyclic_nodes(edge_pairs))[:1]:
            issues.append(
                ValidationIssue(
                    code="PLAN_CHATFLOW_CYCLE_INVALID",
                    message=f"Chatflow top-level graph contains a cycle: {node_id}",
                    node_id=node_id,
                    path=f"nodes.{node_id}",
                    suggestion="删除普通节点环；需要重复执行时使用 Dify loop 容器。",
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

    for node in plan.nodes:
        if node.type != "question-classifier":
            continue
        source_edges = [edge for edge in plan.edges if edge.source == node.id]
        handles = [edge.source_handle for edge in source_edges]
        duplicate_handles = sorted({handle for handle in handles if handles.count(handle) > 1})
        for handle in duplicate_handles:
            issues.append(
                ValidationIssue(
                    code="PLAN_QUESTION_CLASSIFIER_DUPLICATE_BRANCH",
                    message=f"question-classifier branch handle is duplicated: {handle}",
                    node_id=node.id,
                    path=f"edges.{node.id}.{handle}",
                    suggestion="每个分类分支只保留一条出边。",
                )
            )

        classes = [item for item in node.params.get("classes", []) if isinstance(item, dict)]
        class_ids = [str(item.get("id")) for item in classes if item.get("id")]
        for class_id in class_ids:
            if class_id not in handles:
                issues.append(
                    ValidationIssue(
                        code="PLAN_QUESTION_CLASSIFIER_CLASS_EDGE_MISSING",
                        message=f"question-classifier class has no outgoing edge: {class_id}",
                        node_id=node.id,
                        path=f"nodes.{node.id}.params.classes",
                        suggestion=f"添加 source_handle 为 {class_id} 的出边。",
                    )
                )
        valid_handles = set(class_ids)
        for edge in source_edges:
            if edge.source_handle not in valid_handles:
                issues.append(
                    ValidationIssue(
                        code="PLAN_QUESTION_CLASSIFIER_BRANCH_INVALID",
                        message=f"question-classifier edge uses invalid source_handle: {edge.source_handle}",
                        node_id=node.id,
                        path=f"edges.{edge.source}.{edge.target}.source_handle",
                        suggestion="source_handle 必须匹配 classes[].id。",
                    )
                )
    for node in plan.nodes:
        if node.type != "human-input":
            continue
        source_edges = [edge for edge in plan.edges if edge.source == node.id]
        handles = [edge.source_handle for edge in source_edges]
        duplicate_handles = sorted({handle for handle in handles if handles.count(handle) > 1})
        for handle in duplicate_handles:
            issues.append(
                ValidationIssue(
                    code="PLAN_HUMAN_INPUT_DUPLICATE_BRANCH",
                    message=f"human-input action branch handle is duplicated: {handle}",
                    node_id=node.id,
                    path=f"edges.{node.id}.{handle}",
                    suggestion="每个人工动作分支只保留一条出边。",
                )
            )

        actions = [item for item in node.params.get("user_actions", []) if isinstance(item, dict)]
        action_ids = [str(item.get("id")) for item in actions if item.get("id")]
        for action_id in action_ids:
            if action_id not in handles:
                issues.append(
                    ValidationIssue(
                        code="PLAN_HUMAN_INPUT_ACTION_EDGE_MISSING",
                        message=f"human-input action has no outgoing edge: {action_id}",
                        node_id=node.id,
                        path=f"nodes.{node.id}.params.user_actions",
                        suggestion=f"添加 source_handle 为 {action_id} 的出边。",
                    )
                )
        valid_handles = set(action_ids)
        for edge in source_edges:
            if edge.source_handle not in valid_handles:
                issues.append(
                    ValidationIssue(
                        code="PLAN_HUMAN_INPUT_BRANCH_INVALID",
                        message=f"human-input edge uses invalid source_handle: {edge.source_handle}",
                        node_id=node.id,
                        path=f"edges.{edge.source}.{edge.target}.source_handle",
                        suggestion="source_handle 必须匹配 user_actions[].id。",
                    )
                )
    return issues


def _nodes_without_answer_path(
    node_ids: set[str],
    answer_ids: set[str],
    edge_pairs: list[tuple[str, str]],
) -> set[str]:
    reverse: dict[str, list[str]] = {}
    for source, target in edge_pairs:
        reverse.setdefault(target, []).append(source)

    can_reach_answer = set(answer_ids)
    stack = list(answer_ids)
    while stack:
        node_id = stack.pop()
        for source in reverse.get(node_id, []):
            if source not in can_reach_answer:
                can_reach_answer.add(source)
                stack.append(source)
    return node_ids - can_reach_answer


def _response_path_issue_nodes(
    unresolved: set[str],
    edge_pairs: list[tuple[str, str]],
) -> list[str]:
    if not unresolved:
        return []
    outgoing_targets: dict[str, list[str]] = {}
    for source, target in edge_pairs:
        outgoing_targets.setdefault(source, []).append(target)
    dead_ends = sorted(
        node_id
        for node_id in unresolved
        if not outgoing_targets.get(node_id)
    )
    return dead_ends or [sorted(unresolved)[0]]


def _cyclic_nodes(edge_pairs: list[tuple[str, str]]) -> set[str]:
    graph: dict[str, list[str]] = {}
    for source, target in edge_pairs:
        graph.setdefault(source, []).append(target)

    state: dict[str, int] = {}
    stack: list[str] = []
    stack_index: dict[str, int] = {}
    cyclic: set[str] = set()

    def visit(node_id: str) -> None:
        state[node_id] = 1
        stack_index[node_id] = len(stack)
        stack.append(node_id)
        for target in graph.get(node_id, []):
            if state.get(target, 0) == 0:
                visit(target)
            elif state.get(target) == 1:
                cyclic.update(stack[stack_index[target]:])
        stack.pop()
        stack_index.pop(node_id, None)
        state[node_id] = 2

    for node_id in {item for edge in edge_pairs for item in edge}:
        if state.get(node_id, 0) == 0:
            visit(node_id)
    return cyclic


def _validate_node_params(plan: WorkflowPlan) -> list[ValidationIssue]:
    issues: list[ValidationIssue] = []
    for node in plan.nodes:
        params = node.params
        match node.type:
            case "start":
                variables = params.get("variables")
                if not isinstance(variables, list) or (
                    plan.app_mode == "workflow" and not variables
                ):
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
                variables = params.get("variables")
                if not isinstance(variables, list):
                    issues.append(
                        _node_issue(
                            "PLAN_CODE_VARIABLES_INVALID",
                            "code node variables must be a list.",
                            node.id,
                            "params.variables",
                        )
                    )
                else:
                    for idx, variable in enumerate(variables):
                        selector = variable.get("value_selector") if isinstance(variable, dict) else None
                        if (
                            not isinstance(variable, dict)
                            or not variable.get("variable")
                            or not isinstance(selector, list)
                            or len(selector) < 2
                            or not all(isinstance(part, str) and part for part in selector)
                        ):
                            issues.append(
                                _node_issue(
                                    "PLAN_CODE_VARIABLE_SELECTOR_INVALID",
                                    "code variable requires a valid value_selector.",
                                    node.id,
                                    f"params.variables.{idx}.value_selector",
                                    suggestion='使用 ["node_id", "output_name"] 形式绑定已有节点输出。',
                                )
                            )
                outputs = params.get("outputs")
                if not isinstance(outputs, dict) or not outputs:
                    issues.append(_node_issue("PLAN_CODE_OUTPUTS_MISSING", "code node requires outputs.", node.id, "params.outputs"))
                else:
                    for output_name, output_config in outputs.items():
                        if not isinstance(output_config, dict) or not output_config.get("type"):
                            issues.append(
                                _node_issue(
                                    "PLAN_CODE_OUTPUT_INVALID",
                                    "code output requires a typed Dify output schema.",
                                    node.id,
                                    f"params.outputs.{output_name}",
                                    suggestion=(
                                        'Use {"type":"string","children":null} '
                                        "instead of a bare type value."
                                    ),
                                )
                            )
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
            case "answer":
                if not str(params.get("answer") or "").strip():
                    issues.append(
                        _node_issue(
                            "PLAN_ANSWER_CONTENT_MISSING",
                            "answer node requires answer content.",
                            node.id,
                            "params.answer",
                        )
                    )
            case "http-request":
                if not params.get("url"):
                    issues.append(_node_issue("PLAN_HTTP_URL_MISSING", "http-request node requires url.", node.id, "params.url"))
            case "template-transform":
                if not params.get("template"):
                    issues.append(_node_issue("PLAN_TEMPLATE_MISSING", "template-transform node requires template.", node.id, "params.template"))
            case "question-classifier":
                if not isinstance(params.get("query_variable_selector"), list) or not params.get("query_variable_selector"):
                    issues.append(
                        _node_issue(
                            "PLAN_QUESTION_CLASSIFIER_QUERY_MISSING",
                            "question-classifier node requires query_variable_selector.",
                            node.id,
                            "params.query_variable_selector",
                        )
                    )
                classes = params.get("classes")
                if not isinstance(classes, list) or not classes:
                    issues.append(
                        _node_issue(
                            "PLAN_QUESTION_CLASSIFIER_CLASSES_MISSING",
                            "question-classifier node requires classes.",
                            node.id,
                            "params.classes",
                        )
                    )
                seen_class_ids: set[str] = set()
                for idx, item in enumerate(classes or []):
                    if not isinstance(item, dict) or not item.get("id") or not item.get("name"):
                        issues.append(
                            _node_issue(
                                "PLAN_QUESTION_CLASSIFIER_CLASS_INVALID",
                                "question-classifier class requires id and name.",
                                node.id,
                                f"params.classes.{idx}",
                            )
                        )
                        continue
                    class_id = str(item.get("id"))
                    if class_id in seen_class_ids:
                        issues.append(
                            _node_issue(
                                "PLAN_QUESTION_CLASSIFIER_CLASS_DUPLICATE",
                                f"question-classifier class id is duplicated: {class_id}",
                                node.id,
                                f"params.classes.{idx}.id",
                            )
                        )
                    seen_class_ids.add(class_id)
            case "parameter-extractor":
                if not isinstance(params.get("query"), list) or not params.get("query"):
                    issues.append(
                        _node_issue(
                            "PLAN_PARAMETER_EXTRACTOR_QUERY_MISSING",
                            "parameter-extractor node requires query.",
                            node.id,
                            "params.query",
                        )
                    )
                parameters = params.get("parameters")
                if not isinstance(parameters, list) or not parameters:
                    issues.append(
                        _node_issue(
                            "PLAN_PARAMETER_EXTRACTOR_PARAMETERS_MISSING",
                            "parameter-extractor node requires parameters.",
                            node.id,
                            "params.parameters",
                        )
                    )
                seen_names: set[str] = set()
                for idx, item in enumerate(parameters or []):
                    if not isinstance(item, dict) or not item.get("name") or not item.get("description"):
                        issues.append(
                            _node_issue(
                                "PLAN_PARAMETER_EXTRACTOR_PARAMETER_INVALID",
                                "parameter-extractor parameter requires name and description.",
                                node.id,
                                f"params.parameters.{idx}",
                            )
                        )
                        continue
                    name = str(item.get("name"))
                    if name in seen_names:
                        issues.append(
                            _node_issue(
                                "PLAN_PARAMETER_EXTRACTOR_PARAMETER_DUPLICATE",
                                f"parameter-extractor parameter name is duplicated: {name}",
                                node.id,
                                f"params.parameters.{idx}.name",
                            )
                        )
                    seen_names.add(name)
                    if str(item.get("type", "string")) not in PARAMETER_EXTRACTOR_TYPES:
                        issues.append(
                            _node_issue(
                                "PLAN_PARAMETER_EXTRACTOR_PARAMETER_TYPE_INVALID",
                                f"parameter-extractor parameter type is invalid: {item.get('type')}",
                                node.id,
                                f"params.parameters.{idx}.type",
                            )
                        )
            case "variable-aggregator":
                variables = params.get("variables")
                advanced = params.get("advanced_settings") if isinstance(params.get("advanced_settings"), dict) else {}
                groups = advanced.get("groups") if isinstance(advanced.get("groups"), list) else []
                if not variables and not groups:
                    issues.append(
                        _node_issue(
                            "PLAN_VARIABLE_AGGREGATOR_VARIABLES_MISSING",
                            "variable-aggregator node requires variables or groups.",
                            node.id,
                            "params.variables",
                        )
                    )
                for idx, selector in enumerate(variables or []):
                    if not _is_selector(selector):
                        issues.append(
                            _node_issue(
                                "PLAN_VARIABLE_AGGREGATOR_VARIABLE_INVALID",
                                "variable-aggregator variable must be a value selector.",
                                node.id,
                                f"params.variables.{idx}",
                            )
                        )
                for group_idx, group in enumerate(groups):
                    if not isinstance(group, dict) or not group.get("group_name") or not group.get("groupId"):
                        issues.append(
                            _node_issue(
                                "PLAN_VARIABLE_AGGREGATOR_GROUP_INVALID",
                                "variable-aggregator group requires group_name and groupId.",
                                node.id,
                                f"params.advanced_settings.groups.{group_idx}",
                            )
                        )
            case "document-extractor":
                if not _is_selector(params.get("variable_selector")):
                    issues.append(
                        _node_issue(
                            "PLAN_DOCUMENT_EXTRACTOR_SELECTOR_MISSING",
                            "document-extractor node requires variable_selector.",
                            node.id,
                            "params.variable_selector",
                        )
                    )
            case "assigner":
                items = params.get("items")
                if not isinstance(items, list) or not items:
                    issues.append(_node_issue("PLAN_ASSIGNER_ITEMS_MISSING", "assigner node requires items.", node.id, "params.items"))
                for idx, item in enumerate(items or []):
                    if not isinstance(item, dict) or not _is_selector(item.get("variable_selector")):
                        issues.append(
                            _node_issue(
                                "PLAN_ASSIGNER_TARGET_INVALID",
                                "assigner item requires variable_selector.",
                                node.id,
                                f"params.items.{idx}.variable_selector",
                            )
                        )
                    if isinstance(item, dict) and item.get("input_type") == "variable" and not _is_selector(item.get("value")):
                        issues.append(
                            _node_issue(
                                "PLAN_ASSIGNER_VALUE_INVALID",
                                "assigner variable input requires value selector.",
                                node.id,
                                f"params.items.{idx}.value",
                            )
                        )
            case "list-operator":
                if not _is_selector(params.get("variable")):
                    issues.append(
                        _node_issue(
                            "PLAN_LIST_OPERATOR_VARIABLE_MISSING",
                            "list-operator node requires variable.",
                            node.id,
                            "params.variable",
                        )
                    )
                if not str(params.get("var_type", "")).startswith("array["):
                    issues.append(
                        _node_issue(
                            "PLAN_LIST_OPERATOR_VAR_TYPE_INVALID",
                            "list-operator var_type must be an array type.",
                            node.id,
                            "params.var_type",
                        )
                    )
                filter_by = params.get("filter_by") if isinstance(params.get("filter_by"), dict) else {}
                if filter_by.get("enabled"):
                    conditions = filter_by.get("conditions")
                    if not isinstance(conditions, list) or not conditions:
                        issues.append(
                            _node_issue(
                                "PLAN_LIST_OPERATOR_FILTER_CONDITIONS_MISSING",
                                "list-operator filter_by.conditions is required when filtering is enabled.",
                                node.id,
                                "params.filter_by.conditions",
                            )
                        )
                    for idx, condition in enumerate(conditions or []):
                        if not isinstance(condition, dict) or not condition.get("comparison_operator"):
                            issues.append(
                                _node_issue(
                                    "PLAN_LIST_OPERATOR_FILTER_CONDITION_INVALID",
                                    "list-operator filter condition requires comparison_operator.",
                                    node.id,
                                    f"params.filter_by.conditions.{idx}",
                                )
                            )
                        elif str(condition.get("comparison_operator")) not in DIFY_LIST_COMPARISON_OPERATORS:
                            issues.append(
                                _node_issue(
                                    "PLAN_LIST_OPERATOR_FILTER_OPERATOR_INVALID",
                                    "list-operator filter condition uses an unsupported comparison_operator.",
                                    node.id,
                                    f"params.filter_by.conditions.{idx}.comparison_operator",
                                    suggestion="使用 Dify 支持的操作符，例如 contains、not contains、=、≠、>、<、≥、≤、in。",
                                )
                            )
            case "knowledge-retrieval":
                dataset_ids = params.get("dataset_ids")
                if not isinstance(dataset_ids, list) or not [item for item in dataset_ids if str(item).strip()]:
                    issues.append(
                        _node_issue(
                            "PLAN_KNOWLEDGE_RETRIEVAL_DATASETS_MISSING",
                            "knowledge-retrieval node requires dataset_ids.",
                            node.id,
                            "params.dataset_ids",
                        )
                    )
                query_selector = params.get("query_variable_selector")
                attachment_selector = params.get("query_attachment_selector")
                if not _is_selector(query_selector) and not _is_selector(attachment_selector):
                    issues.append(
                        _node_issue(
                            "PLAN_KNOWLEDGE_RETRIEVAL_QUERY_MISSING",
                            "knowledge-retrieval node requires query_variable_selector or query_attachment_selector.",
                            node.id,
                            "params.query_variable_selector",
                        )
                    )
                if str(params.get("retrieval_mode", "multiple")) not in {"multiple", "single"}:
                    issues.append(
                        _node_issue(
                            "PLAN_KNOWLEDGE_RETRIEVAL_MODE_INVALID",
                            "knowledge-retrieval retrieval_mode must be multiple or single.",
                            node.id,
                            "params.retrieval_mode",
                        )
                    )
            case "iteration":
                issues.extend(_validate_iteration_node(node))
            case "loop":
                issues.extend(_validate_loop_node(node))
            case "iteration-start" | "loop-start" | "loop-end":
                issues.append(
                    _node_issue(
                        "PLAN_INTERNAL_NODE_TOP_LEVEL",
                        f"{node.type} is an internal container node and cannot be used as a top-level node.",
                        node.id,
                        "type",
                        suggestion="将内部 start/end 放到 iteration/loop 的 params.children 中。",
                    )
                )
            case "human-input":
                delivery_methods = params.get("delivery_methods")
                if not isinstance(delivery_methods, list) or not delivery_methods:
                    issues.append(
                        _node_issue(
                            "PLAN_HUMAN_INPUT_DELIVERY_METHODS_MISSING",
                            "human-input node requires delivery_methods.",
                            node.id,
                            "params.delivery_methods",
                        )
                    )
                elif not any(isinstance(item, dict) and item.get("enabled") for item in delivery_methods):
                    issues.append(
                        _node_issue(
                            "PLAN_HUMAN_INPUT_DELIVERY_METHOD_ENABLED_MISSING",
                            "human-input node requires at least one enabled delivery method.",
                            node.id,
                            "params.delivery_methods",
                        )
                    )
                actions = params.get("user_actions")
                if not isinstance(actions, list) or not actions:
                    issues.append(
                        _node_issue(
                            "PLAN_HUMAN_INPUT_ACTIONS_MISSING",
                            "human-input node requires user_actions.",
                            node.id,
                            "params.user_actions",
                        )
                    )
                seen_action_ids: set[str] = set()
                for idx, action in enumerate(actions or []):
                    if not isinstance(action, dict) or not action.get("id") or not action.get("title"):
                        issues.append(
                            _node_issue(
                                "PLAN_HUMAN_INPUT_ACTION_INVALID",
                                "human-input action requires id and title.",
                                node.id,
                                f"params.user_actions.{idx}",
                            )
                        )
                        continue
                    action_id = str(action.get("id"))
                    if action_id in seen_action_ids:
                        issues.append(
                            _node_issue(
                                "PLAN_HUMAN_INPUT_ACTION_DUPLICATE",
                                f"human-input action id is duplicated: {action_id}",
                                node.id,
                                f"params.user_actions.{idx}.id",
                            )
                        )
                    seen_action_ids.add(action_id)
                for idx, item in enumerate(params.get("inputs") or []):
                    if not isinstance(item, dict) or not item.get("output_variable_name"):
                        issues.append(
                            _node_issue(
                                "PLAN_HUMAN_INPUT_INPUT_INVALID",
                                "human-input form input requires output_variable_name.",
                                node.id,
                                f"params.inputs.{idx}",
                            )
                        )
                if str(params.get("timeout_unit", "day")) not in {"hour", "day"}:
                    issues.append(
                        _node_issue(
                            "PLAN_HUMAN_INPUT_TIMEOUT_UNIT_INVALID",
                            "human-input timeout_unit must be hour or day.",
                            node.id,
                            "params.timeout_unit",
                        )
                    )
                try:
                    timeout = int(params.get("timeout", 0))
                except (TypeError, ValueError):
                    timeout = 0
                if timeout < 1:
                    issues.append(
                        _node_issue(
                            "PLAN_HUMAN_INPUT_TIMEOUT_INVALID",
                            "human-input timeout must be a positive integer.",
                            node.id,
                            "params.timeout",
                        )
                    )
            case "tool":
                issues.extend(_validate_tool_node(node))
            case "agent":
                issues.extend(_validate_agent_node(node))
            case "trigger-webhook":
                issues.extend(_validate_trigger_webhook_node(node))
            case "trigger-plugin":
                issues.extend(_validate_trigger_plugin_node(node))
            case "trigger-schedule":
                issues.extend(_validate_trigger_schedule_node(node))
            case node_type if node_type in EXTERNAL_DEPENDENCY_NODE_TYPES:
                issues.append(
                    _node_issue(
                        "PLAN_EXTERNAL_DEPENDENCY_NODE_PASSTHROUGH",
                        f"{node.type} node is preserved as an external-dependency Dify node.",
                        node.id,
                        "type",
                        suggestion="该节点依赖 Dify 插件、触发器或外部配置；chat2dify 会尽量原样保留，但不会替你校验外部资源是否可用。",
                    ).model_copy(update={"severity": "warning"})
                )
    return issues


def _validate_trigger_webhook_node(node: PlanNode) -> list[ValidationIssue]:
    params = node.params
    if isinstance(params.get("_raw_data"), dict):
        return [
            _node_issue(
                "PLAN_EXTERNAL_DEPENDENCY_NODE_PASSTHROUGH",
                "trigger-webhook node is preserved from an existing Dify draft.",
                node.id,
                "type",
            ).model_copy(update={"severity": "warning"})
        ]
    issues: list[ValidationIssue] = []
    if str(params.get("method") or "").upper() not in {"GET", "POST", "HEAD", "PATCH", "PUT", "DELETE"}:
        issues.append(_node_issue("PLAN_WEBHOOK_METHOD_INVALID", "Webhook method is invalid.", node.id, "params.method"))
    if str(params.get("content_type") or "") not in {
        "application/json",
        "multipart/form-data",
        "application/x-www-form-urlencoded",
        "text/plain",
        "application/octet-stream",
    }:
        issues.append(
            _node_issue(
                "PLAN_WEBHOOK_CONTENT_TYPE_INVALID",
                "Webhook content_type is invalid.",
                node.id,
                "params.content_type",
            )
        )

    allowed_by_group = {
        "headers": {"string"},
        "params": {"string", "number", "boolean"},
        "body": {
            "string",
            "number",
            "boolean",
            "object",
            "array[string]",
            "array[number]",
            "array[boolean]",
            "array[object]",
            "file",
        },
    }
    seen: dict[str, str] = {}
    for group, allowed in allowed_by_group.items():
        items = params.get(group)
        if not isinstance(items, list):
            issues.append(
                _node_issue(
                    "PLAN_WEBHOOK_PARAMETERS_INVALID",
                    f"Webhook {group} must be a list.",
                    node.id,
                    f"params.{group}",
                )
            )
            continue
        for idx, item in enumerate(items):
            if not isinstance(item, dict):
                issues.append(
                    _node_issue(
                        "PLAN_WEBHOOK_PARAMETER_INVALID",
                        f"Webhook {group} parameter must be an object.",
                        node.id,
                        f"params.{group}.{idx}",
                    )
                )
                continue
            name = str(item.get("name") or "").strip()
            variable_name = name.replace("-", "_") if group == "headers" else name
            if not name or not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_-]*", name):
                issues.append(
                    _node_issue(
                        "PLAN_WEBHOOK_PARAMETER_NAME_INVALID",
                        f"Webhook parameter name is invalid: {name or '<empty>'}",
                        node.id,
                        f"params.{group}.{idx}.name",
                        suggestion="参数名使用英文字母或下划线开头，并只包含字母、数字、下划线或连字符。",
                    )
                )
            if variable_name in seen:
                issues.append(
                    _node_issue(
                        "PLAN_WEBHOOK_PARAMETER_DUPLICATE",
                        f"Webhook output variable is duplicated: {variable_name}",
                        node.id,
                        f"params.{group}.{idx}.name",
                        suggestion=f"该名称已在 {seen[variable_name]} 中使用，请改成唯一变量名。",
                    )
                )
            else:
                seen[variable_name] = group
            parameter_type = str(item.get("type") or "string")
            if parameter_type not in allowed:
                issues.append(
                    _node_issue(
                        "PLAN_WEBHOOK_PARAMETER_TYPE_INVALID",
                        f"Webhook {group} parameter type is invalid: {parameter_type}",
                        node.id,
                        f"params.{group}.{idx}.type",
                    )
                )
    try:
        timeout = int(params.get("timeout", 0))
    except (TypeError, ValueError):
        timeout = 0
    if timeout < 1 or timeout > 300:
        issues.append(
            _node_issue(
                "PLAN_WEBHOOK_TIMEOUT_INVALID",
                "Webhook timeout must be between 1 and 300 seconds.",
                node.id,
                "params.timeout",
            )
        )
    return issues


def _validate_trigger_plugin_node(node: PlanNode) -> list[ValidationIssue]:
    params = node.params
    if isinstance(params.get("_raw_data"), dict):
        return [
            _node_issue(
                "PLAN_EXTERNAL_DEPENDENCY_NODE_PASSTHROUGH",
                "trigger-plugin node is preserved from an existing Dify draft.",
                node.id,
                "type",
                suggestion="该节点依赖 Dify 已安装的 Trigger Provider 和 subscription，chat2dify 只原样保留旧配置。",
            ).model_copy(update={"severity": "warning"})
        ]

    issues: list[ValidationIssue] = []
    required_fields = {
        "provider_id": "provider",
        "event_name": "event",
        "subscription_id": "subscription",
        "plugin_id": "plugin",
        "plugin_unique_identifier": "plugin unique identifier",
    }
    for field, label in required_fields.items():
        if not str(params.get(field) or "").strip():
            issues.append(
                _node_issue(
                    f"PLAN_PLUGIN_TRIGGER_{field.upper()}_MISSING",
                    f"Plugin Trigger requires a {label}.",
                    node.id,
                    f"params.{field}",
                )
            )

    event_parameters = params.get("event_parameters")
    if not isinstance(event_parameters, dict):
        issues.append(
            _node_issue(
                "PLAN_PLUGIN_TRIGGER_PARAMETERS_INVALID",
                "Plugin Trigger event_parameters must be an object.",
                node.id,
                "params.event_parameters",
            )
        )
        event_parameters = {}

    schemas = params.get("parameters_schema")
    if not isinstance(schemas, list):
        issues.append(
            _node_issue(
                "PLAN_PLUGIN_TRIGGER_SCHEMA_INVALID",
                "Plugin Trigger parameters_schema must be a list.",
                node.id,
                "params.parameters_schema",
            )
        )
        schemas = []

    for idx, schema in enumerate(schemas):
        if not isinstance(schema, dict):
            continue
        name = str(schema.get("name") or schema.get("variable") or "").strip()
        if not name:
            continue
        value = event_parameters.get(name)
        if schema.get("required") and _plugin_trigger_value_missing(value):
            issues.append(
                _node_issue(
                    "PLAN_PLUGIN_TRIGGER_PARAMETER_REQUIRED",
                    f"Plugin Trigger parameter is required: {name}",
                    node.id,
                    f"params.event_parameters.{name}",
                )
            )
        if value is not None and (
            not isinstance(value, dict) or value.get("type") != "constant"
        ):
            issues.append(
                _node_issue(
                    "PLAN_PLUGIN_TRIGGER_PARAMETER_NOT_CONSTANT",
                    f"Plugin Trigger parameter must use a constant binding: {name}",
                    node.id,
                    f"params.event_parameters.{name}",
                    suggestion="Plugin Trigger 是入口节点，没有上游变量；请在 Web UI 中填写常量。",
                )
            )

    for name, value in event_parameters.items():
        if not isinstance(value, dict) or value.get("type") != "constant":
            if not any(
                issue.path == f"nodes.{node.id}.params.event_parameters.{name}"
                for issue in issues
            ):
                issues.append(
                    _node_issue(
                        "PLAN_PLUGIN_TRIGGER_PARAMETER_NOT_CONSTANT",
                        f"Plugin Trigger parameter must use a constant binding: {name}",
                        node.id,
                        f"params.event_parameters.{name}",
                    )
                )
    return issues


def _plugin_trigger_value_missing(value: Any) -> bool:
    if not isinstance(value, dict) or value.get("type") != "constant":
        return True
    raw = value.get("value")
    return raw is None or raw == "" or (isinstance(raw, list) and not raw)


def _validate_trigger_schedule_node(node: PlanNode) -> list[ValidationIssue]:
    params = node.params
    if isinstance(params.get("_raw_data"), dict):
        return [
            _node_issue(
                "PLAN_EXTERNAL_DEPENDENCY_NODE_PASSTHROUGH",
                "trigger-schedule node is preserved from an existing Dify draft.",
                node.id,
                "type",
            ).model_copy(update={"severity": "warning"})
        ]
    issues: list[ValidationIssue] = []
    mode = str(params.get("mode") or "")
    if mode not in {"visual", "cron"}:
        issues.append(_node_issue("PLAN_SCHEDULE_MODE_INVALID", "Schedule mode must be visual or cron.", node.id, "params.mode"))
    timezone = str(params.get("timezone") or "")
    try:
        ZoneInfo(timezone)
    except (ZoneInfoNotFoundError, ValueError):
        issues.append(
            _node_issue(
                "PLAN_SCHEDULE_TIMEZONE_INVALID",
                f"Schedule timezone is invalid: {timezone or '<empty>'}",
                node.id,
                "params.timezone",
                suggestion="使用 IANA 时区，例如 Asia/Shanghai。",
            )
        )
    if mode == "cron":
        cron = str(params.get("cron_expression") or "").strip()
        if len(cron.split()) != 5:
            issues.append(
                _node_issue(
                    "PLAN_SCHEDULE_CRON_INVALID",
                    "Schedule cron_expression must contain five fields.",
                    node.id,
                    "params.cron_expression",
                )
            )
        return issues

    frequency = str(params.get("frequency") or "")
    if frequency not in {"hourly", "daily", "weekly", "monthly"}:
        issues.append(
            _node_issue(
                "PLAN_SCHEDULE_FREQUENCY_INVALID",
                "Schedule frequency must be hourly, daily, weekly, or monthly.",
                node.id,
                "params.frequency",
            )
        )
    visual = params.get("visual_config") if isinstance(params.get("visual_config"), dict) else {}
    if frequency == "hourly":
        try:
            on_minute = int(visual.get("on_minute", -1))
        except (TypeError, ValueError):
            on_minute = -1
        if not 0 <= on_minute <= 59:
            issues.append(
                _node_issue(
                    "PLAN_SCHEDULE_MINUTE_INVALID",
                    "Hourly schedule on_minute must be between 0 and 59.",
                    node.id,
                    "params.visual_config.on_minute",
                )
            )
    else:
        time_value = str(visual.get("time") or "")
        if not re.fullmatch(r"(?:0?[1-9]|1[0-2]):[0-5][0-9] (?:AM|PM)", time_value):
            issues.append(
                _node_issue(
                    "PLAN_SCHEDULE_TIME_INVALID",
                    "Schedule time must use 12-hour format such as 09:00 AM.",
                    node.id,
                    "params.visual_config.time",
                )
            )
    if frequency == "weekly":
        weekdays = visual.get("weekdays")
        valid_days = {"sun", "mon", "tue", "wed", "thu", "fri", "sat"}
        if not isinstance(weekdays, list) or not weekdays or any(str(item) not in valid_days for item in weekdays):
            issues.append(
                _node_issue(
                    "PLAN_SCHEDULE_WEEKDAYS_INVALID",
                    "Weekly schedule requires valid weekdays.",
                    node.id,
                    "params.visual_config.weekdays",
                )
            )
    if frequency == "monthly":
        days = visual.get("monthly_days")
        valid = isinstance(days, list) and bool(days)
        for item in days if isinstance(days, list) else []:
            if item == "last":
                continue
            try:
                valid = valid and 1 <= int(item) <= 31
            except (TypeError, ValueError):
                valid = False
        if not valid:
            issues.append(
                _node_issue(
                    "PLAN_SCHEDULE_MONTHLY_DAYS_INVALID",
                    "Monthly schedule requires days from 1 to 31 or last.",
                    node.id,
                    "params.visual_config.monthly_days",
                )
            )
    return issues


def _node_issue(
    code: str,
    message: str,
    node_id: str,
    path: str,
    *,
    suggestion: str = "让 normalizer 补齐字段，或让 planner 重新生成该节点参数。",
) -> ValidationIssue:
    return ValidationIssue(
        code=code,
        message=message,
        node_id=node_id,
        path=f"nodes.{node_id}.{path}",
        suggestion=suggestion,
    )


def _validate_tool_node(node: PlanNode) -> list[ValidationIssue]:
    params = node.params
    if isinstance(params.get("_raw_data"), dict):
        return [
            _node_issue(
                "PLAN_EXTERNAL_DEPENDENCY_NODE_PASSTHROUGH",
                "tool node is preserved as an external-dependency Dify node.",
                node.id,
                "type",
                suggestion="该 tool 节点来自 Dify 既有草稿；chat2dify 会尽量原样保留，但不会替你校验插件鉴权是否可用。",
            ).model_copy(update={"severity": "warning"})
        ]

    issues: list[ValidationIssue] = []
    for field in ("provider_id", "provider_type", "tool_name"):
        if not str(params.get(field) or "").strip():
            issues.append(
                _node_issue(
                    f"PLAN_TOOL_{field.upper()}_MISSING",
                    f"tool node requires {field}.",
                    node.id,
                    f"params.{field}",
                    suggestion="在 Web UI 选择已安装工具，或让 planner 使用 tool_selections 中的真实工具元数据。",
                )
            )
    if str(params.get("provider_type") or "") not in {"builtin", "api", "workflow", "mcp"}:
        issues.append(
            _node_issue(
                "PLAN_TOOL_PROVIDER_TYPE_INVALID",
                "tool provider_type must be builtin, api, workflow, or mcp.",
                node.id,
                "params.provider_type",
            )
        )
    schemas = params.get("paramSchemas") if isinstance(params.get("paramSchemas"), list) else []
    tool_parameters = params.get("tool_parameters") if isinstance(params.get("tool_parameters"), dict) else {}
    tool_configurations = params.get("tool_configurations") if isinstance(params.get("tool_configurations"), dict) else {}
    for idx, schema in enumerate(schemas):
        if not isinstance(schema, dict):
            continue
        name = str(schema.get("variable") or schema.get("name") or "").strip()
        if not name:
            continue
        if not schema.get("required"):
            continue
        if str(schema.get("form") or "") == "llm":
            value = tool_parameters.get(name)
            if not _tool_var_input_has_value(value):
                issues.append(
                    _node_issue(
                        "PLAN_TOOL_REQUIRED_PARAMETER_MISSING",
                        f"tool required parameter is missing: {name}",
                        node.id,
                        f"params.tool_parameters.{name}",
                        suggestion="为必填 llm 参数提供 Dify ToolInput，例如字符串参数使用 {'type':'mixed','value':'{{#start.query#}}'}。",
                    )
                )
        else:
            value = tool_configurations.get(name)
            if value in (None, "") or (isinstance(value, dict) and not _tool_var_input_has_value(value)):
                issues.append(
                    _node_issue(
                        "PLAN_TOOL_REQUIRED_CONFIGURATION_MISSING",
                        f"tool required configuration is missing: {name}",
                        node.id,
                        f"params.tool_configurations.{name}",
                        suggestion="该字段需要 Dify 工具配置或显式默认值；chat2dify 不会猜测鉴权/配置。",
                    )
                )
        if not schema.get("name") and not schema.get("variable"):
            issues.append(
                _node_issue(
                    "PLAN_TOOL_PARAMETER_SCHEMA_INVALID",
                    "tool parameter schema requires name or variable.",
                    node.id,
                    f"params.paramSchemas.{idx}",
                )
            )
    return issues


def _validate_agent_node(node: PlanNode) -> list[ValidationIssue]:
    params = node.params
    if isinstance(params.get("_raw_data"), dict):
        return [
            _node_issue(
                "PLAN_EXTERNAL_DEPENDENCY_NODE_PASSTHROUGH",
                "agent node is preserved as an external-dependency Dify node.",
                node.id,
                "type",
                suggestion="该 agent 节点来自 Dify 既有草稿；chat2dify 会尽量原样保留，但不会替你校验插件策略或鉴权是否可用。",
            ).model_copy(update={"severity": "warning"})
        ]

    issues: list[ValidationIssue] = []
    for field in ("agent_strategy_provider_name", "agent_strategy_name"):
        if not str(params.get(field) or "").strip():
            issues.append(
                _node_issue(
                    f"PLAN_AGENT_{field.upper()}_MISSING",
                    f"agent node requires {field}.",
                    node.id,
                    f"params.{field}",
                    suggestion="在 Web UI 选择已安装 Agent Strategy，或让 planner 使用 agent_selections 中的真实策略元数据。",
                )
            )

    agent_parameters = params.get("agent_parameters") if isinstance(params.get("agent_parameters"), dict) else {}
    schemas = params.get("parameters") if isinstance(params.get("parameters"), list) else []
    for idx, schema in enumerate(schemas):
        if not isinstance(schema, dict):
            continue
        name = str(schema.get("variable") or schema.get("name") or "").strip()
        if not name:
            continue
        if not schema.get("required"):
            continue
        value = agent_parameters.get(name)
        if not _tool_var_input_has_value(value):
            issues.append(
                _node_issue(
                    "PLAN_AGENT_REQUIRED_PARAMETER_MISSING",
                    f"agent required parameter is missing: {name}",
                    node.id,
                    f"params.agent_parameters.{name}",
                    suggestion="为 Agent Strategy 必填参数提供 Dify 输入；字符串变量绑定使用 {'type':'constant','value':'{{#start.query#}}'}。",
                )
            )
            continue
        parameter_type = str(schema.get("type") or "").strip()
        if parameter_type in {"tool-selector", "multi-tool-selector"} and not _agent_tool_selector_has_value(value):
            issues.append(
                _node_issue(
                    "PLAN_AGENT_TOOL_SELECTOR_MISSING",
                    f"agent tool selector parameter is not bound to a selected tool: {name}",
                    node.id,
                    f"params.agent_parameters.{name}",
                    suggestion="在 Web UI 中为该 Agent 参数绑定已选择且已配置的 Tool；chat2dify 不会猜测嵌套工具。",
                )
            )
        if not schema.get("name") and not schema.get("variable"):
            issues.append(
                _node_issue(
                    "PLAN_AGENT_PARAMETER_SCHEMA_INVALID",
                    "agent parameter schema requires name or variable.",
                    node.id,
                    f"params.parameters.{idx}",
                )
            )
    return issues


def _agent_tool_selector_has_value(value: Any) -> bool:
    if not isinstance(value, dict):
        return False
    raw = value.get("value")
    if isinstance(raw, list):
        return bool(raw) and all(_agent_tool_selector_has_value({"value": item}) for item in raw)
    if not isinstance(raw, dict):
        return False
    if raw.get("enabled") is False:
        return False
    schemas = raw.get("schemas") if isinstance(raw.get("schemas"), list) else []
    settings = raw.get("settings") if isinstance(raw.get("settings"), dict) else {}
    parameters = raw.get("parameters") if isinstance(raw.get("parameters"), dict) else {}
    for schema in schemas:
        if not isinstance(schema, dict) or not schema.get("required"):
            continue
        name = str(schema.get("variable") or schema.get("name") or "").strip()
        if not name:
            continue
        if str(schema.get("form") or "") == "llm":
            nested = parameters.get(name)
            nested_value = nested.get("value") if isinstance(nested, dict) and "value" in nested else nested
            if isinstance(nested, dict) and nested.get("auto") == 1:
                continue
            if not _tool_var_input_has_value(nested_value):
                return False
        else:
            nested = settings.get(name)
            nested_value = nested.get("value") if isinstance(nested, dict) and "value" in nested else nested
            if not _tool_var_input_has_value(nested_value):
                return False
    return bool(raw.get("provider_id") or raw.get("tool_name") or raw.get("name"))


def _tool_var_input_has_value(value: Any) -> bool:
    if value in (None, ""):
        return False
    if isinstance(value, dict):
        raw = value.get("value")
        if raw in (None, ""):
            return False
        if isinstance(raw, list) and not raw:
            return False
    return True


def _validate_iteration_node(node: PlanNode) -> list[ValidationIssue]:
    issues: list[ValidationIssue] = []
    params = node.params
    if not _is_selector(params.get("iterator_selector")):
        issues.append(_node_issue("PLAN_ITERATION_ITERATOR_MISSING", "iteration node requires iterator_selector.", node.id, "params.iterator_selector"))
    if not _is_selector(params.get("output_selector")):
        issues.append(_node_issue("PLAN_ITERATION_OUTPUT_SELECTOR_MISSING", "iteration node requires output_selector.", node.id, "params.output_selector"))
    try:
        parallel_nums = int(params.get("parallel_nums", 10))
    except (TypeError, ValueError):
        parallel_nums = 0
    if parallel_nums < 1:
        issues.append(_node_issue("PLAN_ITERATION_PARALLEL_NUMS_INVALID", "iteration parallel_nums must be positive.", node.id, "params.parallel_nums"))
    if str(params.get("error_handle_mode", "terminated")) not in {"terminated", "continue-on-error", "remove-abnormal-output"}:
        issues.append(_node_issue("PLAN_ITERATION_ERROR_HANDLE_MODE_INVALID", "iteration error_handle_mode is invalid.", node.id, "params.error_handle_mode"))
    issues.extend(
        _validate_container_graph(
            node,
            start_type="iteration-start",
            output_selector=params.get("output_selector"),
        )
    )
    return issues


def _validate_loop_node(node: PlanNode) -> list[ValidationIssue]:
    issues: list[ValidationIssue] = []
    params = node.params
    children = params.get("children") if isinstance(params.get("children"), list) else []
    child_ids = {
        str(child.get("id"))
        for child in children
        if isinstance(child, dict) and child.get("id")
    }
    try:
        loop_count = int(params.get("loop_count", 0))
    except (TypeError, ValueError):
        loop_count = 0
    if loop_count < 1:
        issues.append(_node_issue("PLAN_LOOP_COUNT_INVALID", "loop node requires a positive loop_count.", node.id, "params.loop_count"))
    if str(params.get("logical_operator", "and")) not in {"and", "or"}:
        issues.append(_node_issue("PLAN_LOOP_LOGICAL_OPERATOR_INVALID", "loop logical_operator must be and or or.", node.id, "params.logical_operator"))
    if str(params.get("error_handle_mode", "terminated")) not in {"terminated", "continue-on-error", "remove-abnormal-output"}:
        issues.append(_node_issue("PLAN_LOOP_ERROR_HANDLE_MODE_INVALID", "loop error_handle_mode is invalid.", node.id, "params.error_handle_mode"))
    for idx, condition in enumerate(params.get("break_conditions") or []):
        if not isinstance(condition, dict):
            issues.append(_node_issue("PLAN_LOOP_BREAK_CONDITION_INVALID", "loop break condition must be an object.", node.id, f"params.break_conditions.{idx}"))
            continue
        if condition.get("variable_selector") and not _is_selector(condition.get("variable_selector")):
            issues.append(_node_issue("PLAN_LOOP_BREAK_CONDITION_SELECTOR_INVALID", "loop break condition variable_selector is invalid.", node.id, f"params.break_conditions.{idx}.variable_selector"))
        selector = condition.get("variable_selector")
        if _is_selector(selector) and str(selector[0]) in child_ids:
            issues.append(
                _node_issue(
                    "PLAN_LOOP_BREAK_CONDITION_INTERNAL_SELECTOR_INVALID",
                    "loop break_conditions must reference loop variables, not internal child outputs.",
                    node.id,
                    f"params.break_conditions.{idx}.variable_selector",
                    suggestion="先用 assigner 将内部节点输出写入 loop_variables，再让 break_conditions 引用 [loop_node_id, variable_label]。",
                )
            )
    seen_labels: set[str] = set()
    for idx, variable in enumerate(params.get("loop_variables") or []):
        if not isinstance(variable, dict) or not variable.get("label"):
            issues.append(_node_issue("PLAN_LOOP_VARIABLE_INVALID", "loop variable requires label.", node.id, f"params.loop_variables.{idx}"))
            continue
        label = str(variable.get("label"))
        if label in seen_labels:
            issues.append(_node_issue("PLAN_LOOP_VARIABLE_DUPLICATE", f"loop variable label is duplicated: {label}", node.id, f"params.loop_variables.{idx}.label"))
        seen_labels.add(label)
        if variable.get("value_type") == "variable" and not _is_selector(variable.get("value")):
            issues.append(_node_issue("PLAN_LOOP_VARIABLE_VALUE_INVALID", "loop variable value requires selector when value_type is variable.", node.id, f"params.loop_variables.{idx}.value"))
    issues.extend(_validate_container_graph(node, start_type="loop-start", output_selector=None))
    return issues


def _validate_container_graph(
    node: PlanNode,
    *,
    start_type: str,
    output_selector: Any,
) -> list[ValidationIssue]:
    issues: list[ValidationIssue] = []
    children = node.params.get("children")
    if not isinstance(children, list) or not children:
        return [
            _node_issue(
                "PLAN_CONTAINER_CHILDREN_MISSING",
                f"{node.type} node requires params.children.",
                node.id,
                "params.children",
            )
        ]
    child_by_id = {str(child.get("id")): child for child in children if isinstance(child, dict) and child.get("id")}
    start_node_id = str(node.params.get("start_node_id") or "")
    start_child = child_by_id.get(start_node_id)
    if not start_node_id or not isinstance(start_child, dict) or start_child.get("type") != start_type:
        issues.append(
            _node_issue(
                "PLAN_CONTAINER_START_MISSING",
                f"{node.type} node requires a {start_type} child matching start_node_id.",
                node.id,
                "params.start_node_id",
            )
        )
    processing_children = [
        child
        for child in children
        if isinstance(child, dict) and child.get("type") not in {"iteration-start", "loop-start", "loop-end"}
    ]
    if not processing_children:
        issues.append(
            _node_issue(
                "PLAN_CONTAINER_PROCESSING_CHILD_MISSING",
                f"{node.type} node requires at least one internal processing node.",
                node.id,
                "params.children",
            )
        )
    for idx, child in enumerate(children):
        if not isinstance(child, dict) or not child.get("id") or not child.get("type"):
            issues.append(_node_issue("PLAN_CONTAINER_CHILD_INVALID", "container child requires id and type.", node.id, f"params.children.{idx}"))
            continue
        child_type = str(child.get("type"))
        if child_type not in SUPPORTED_NODE_TYPES:
            issues.append(_node_issue("PLAN_CONTAINER_CHILD_TYPE_UNSUPPORTED", f"unsupported container child type: {child_type}", node.id, f"params.children.{idx}.type"))
        if child_type in {"start", "end", "iteration", "loop"}:
            issues.append(
                _node_issue(
                    "PLAN_CONTAINER_CHILD_TYPE_INVALID",
                    f"{child_type} cannot be used as an internal child in this stage.",
                    node.id,
                    f"params.children.{idx}.type",
                    suggestion="循环内部使用非插件处理节点；start/end 由容器自身表达。",
                )
            )

    edges = node.params.get("edges") if isinstance(node.params.get("edges"), list) else []
    adjacency: dict[str, list[str]] = {child_id: [] for child_id in child_by_id}
    for idx, edge in enumerate(edges):
        if not isinstance(edge, dict):
            issues.append(_node_issue("PLAN_CONTAINER_EDGE_INVALID", "container edge must be an object.", node.id, f"params.edges.{idx}"))
            continue
        source = str(edge.get("source") or "")
        target = str(edge.get("target") or "")
        if source not in child_by_id or target not in child_by_id:
            issues.append(_node_issue("PLAN_CONTAINER_EDGE_NODE_UNKNOWN", "container edge references unknown child node.", node.id, f"params.edges.{idx}"))
            continue
        adjacency.setdefault(source, []).append(target)

    if start_node_id in child_by_id:
        reachable = _reachable_from(start_node_id, adjacency)
        for child in processing_children:
            child_id = str(child.get("id"))
            if child_id not in reachable:
                issues.append(
                    _node_issue(
                        "PLAN_CONTAINER_CHILD_UNREACHABLE",
                        f"container child is not reachable from internal start: {child_id}",
                        node.id,
                        "params.edges",
                    )
                )

    if _is_selector(output_selector):
        output_node_id = str(output_selector[0])
        output_name = str(output_selector[1]) if len(output_selector) > 1 else ""
        child = child_by_id.get(output_node_id)
        if not child:
            issues.append(_node_issue("PLAN_CONTAINER_OUTPUT_NODE_UNKNOWN", "container output_selector references unknown child.", node.id, "params.output_selector"))
        elif output_name:
            available_outputs = _outputs_for_node(str(child.get("type")), child.get("params") if isinstance(child.get("params"), dict) else {})
            if available_outputs and output_name not in available_outputs:
                issues.append(
                    _node_issue(
                        "PLAN_CONTAINER_OUTPUT_UNKNOWN",
                        f"container output_selector references unknown output: {output_node_id}.{output_name}",
                        node.id,
                        "params.output_selector",
                    )
                )
    return issues


def _reachable_from(start_node_id: str, adjacency: dict[str, list[str]]) -> set[str]:
    reachable: set[str] = set()
    stack = [start_node_id]
    while stack:
        node_id = stack.pop()
        if node_id in reachable:
            continue
        reachable.add(node_id)
        stack.extend(adjacency.get(node_id, []))
    return reachable


def _is_selector(value: Any) -> bool:
    return isinstance(value, list) and len(value) >= 2 and all(str(item) for item in value)


def _validate_node_quality(plan: WorkflowPlan) -> list[ValidationIssue]:
    issues: list[ValidationIssue] = []
    for node in plan.nodes:
        if node.title is not None and _is_generic_title(node.title, node.type):
            issues.append(
                ValidationIssue(
                    code="PLAN_NODE_TITLE_GENERIC",
                    message=f"node title is too generic: {node.title}",
                    node_id=node.id,
                    severity="warning",
                    path=f"nodes.{node.id}.title",
                    suggestion="使用业务语义名称，例如“接收售后诉求”“生成理发售后回复”。",
                )
            )
        if node.type == "llm" and "system_prompt" in node.params and not str(node.params.get("system_prompt") or "").strip():
            issues.append(
                ValidationIssue(
                    code="PLAN_LLM_SYSTEM_PROMPT_EMPTY",
                    message="llm node has an empty system_prompt.",
                    node_id=node.id,
                    severity="warning",
                    path=f"nodes.{node.id}.params.system_prompt",
                    suggestion="system_prompt 应定义模型身份、规则、输出格式和审核标准。",
                )
            )
    return issues


def _is_generic_title(title: str, node_type: str) -> bool:
    normalized = GENERIC_TITLE_RE.sub("", str(title or "").strip().lower())
    default = GENERIC_TITLE_RE.sub("", node_type.replace("-", " ").title().lower())
    return normalized in GENERIC_TITLES or normalized == default


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


def _validate_chatflow_system_references(plan: WorkflowPlan) -> list[ValidationIssue]:
    if plan.app_mode != "advanced-chat":
        return []

    start_ids = {node.id for node in plan.nodes if node.type == "start"}
    issues: list[ValidationIssue] = []
    for node in plan.nodes:
        invalid_references = {
            (match.group(1), match.group(2))
            for text in _strings_in_value(node.params)
            for match in TEMPLATE_REF_RE.finditer(text)
            if match.group(1) in start_ids
            and match.group(2) in {"sys.query", "sys.files"}
        }
        for start_id, variable in sorted(invalid_references):
            reference = f"{{{{#{start_id}.{variable}#}}}}"
            issues.append(
                ValidationIssue(
                    code="PLAN_CHATFLOW_SYSTEM_REFERENCE_INVALID",
                    message=(
                        "Chatflow system variables cannot use a start-node "
                        f"prefix: {reference}"
                    ),
                    node_id=node.id,
                    path=f"nodes.{node.id}.params",
                    suggestion=f"在文本中改用 {{{{#{variable}#}}}}。",
                )
            )
    return issues


def _known_outputs(plan: WorkflowPlan) -> dict[str, set[str]]:
    outputs: dict[str, set[str]] = {"sys": set(SYSTEM_OUTPUTS)}
    for node in plan.nodes:
        match node.type:
            case "start":
                names = {
                    str(item.get("name") or item.get("variable"))
                    for item in node.params.get("variables") or node.params.get("inputs", [])
                    if item.get("name") or item.get("variable")
                }
                if plan.app_mode == "advanced-chat":
                    outputs[node.id] = {*names, "sys.query", "sys.files"}
                else:
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
            case "question-classifier":
                outputs[node.id] = set()
            case "parameter-extractor":
                names = {
                    str(item.get("name"))
                    for item in node.params.get("parameters", [])
                    if isinstance(item, dict) and item.get("name")
                }
                outputs[node.id] = {*names, "__is_success", "__reason", "__usage"}
            case "variable-aggregator":
                outputs[node.id] = {"output"}
            case "document-extractor":
                outputs[node.id] = {"text"}
            case "list-operator":
                outputs[node.id] = {"result", "first_record", "last_record"}
            case "knowledge-retrieval":
                outputs[node.id] = {"result"}
            case "human-input":
                names = {
                    str(item.get("output_variable_name"))
                    for item in node.params.get("inputs", [])
                    if isinstance(item, dict) and item.get("output_variable_name")
                }
                outputs[node.id] = {*names, "selected_action", "submitted_at", "__action_id", "__action_value", "__rendered_content"}
            case "iteration":
                outputs[node.id] = {"output", "item", "index"}
            case "loop":
                labels = {
                    str(item.get("label"))
                    for item in node.params.get("loop_variables", [])
                    if isinstance(item, dict) and item.get("label")
                }
                outputs[node.id] = {*labels, "loop_round"}
            case "tool" | "agent":
                outputs[node.id] = {"text", "files", "json", *_schema_output_names(node.params)}
            case "datasource" | "datasource-empty":
                outputs[node.id] = {"datasource_type", "file", *_schema_output_names(node.params)}
            case "knowledge-index":
                outputs[node.id] = {"result", "document_ids", *_schema_output_names(node.params)}
            case "trigger-webhook":
                outputs[node.id] = _webhook_outputs(node.params)
            case "trigger-schedule":
                outputs[node.id] = {"sys.timestamp"}
            case "trigger-plugin":
                outputs[node.id] = _external_trigger_outputs(node.params)
            case "iteration-start" | "loop-start" | "loop-end":
                outputs[node.id] = set()
            case "assigner":
                outputs[node.id] = set()
            case "end" | "answer" | "if-else":
                outputs[node.id] = set()
        children = node.params.get("children") if isinstance(node.params.get("children"), list) else []
        for child in children:
            if not isinstance(child, dict) or not child.get("id") or not child.get("type"):
                continue
            outputs[str(child["id"])] = _outputs_for_node(
                str(child["type"]),
                child.get("params") if isinstance(child.get("params"), dict) else {},
            )
    return outputs


def _strings_in_value(value: Any) -> list[str]:
    strings: list[str] = []
    if isinstance(value, str):
        strings.append(value)
    elif isinstance(value, dict):
        for key, child in value.items():
            if key != "_raw_data":
                strings.extend(_strings_in_value(child))
    elif isinstance(value, list):
        for child in value:
            strings.extend(_strings_in_value(child))
    return strings


def _outputs_for_node(node_type: str, params: dict[str, Any]) -> set[str]:
    match node_type:
        case "llm":
            return {"text"}
        case "code":
            declared = params.get("outputs") or {"result": {"type": "string", "children": None}}
            return set(declared.keys()) if isinstance(declared, dict) else set()
        case "http-request":
            return {"body", "status_code", "headers"}
        case "template-transform":
            return {"output"}
        case "parameter-extractor":
            names = {
                str(item.get("name"))
                for item in params.get("parameters", [])
                if isinstance(item, dict) and item.get("name")
            }
            return {*names, "__is_success", "__reason", "__usage"}
        case "variable-aggregator":
            return {"output"}
        case "document-extractor":
            return {"text"}
        case "list-operator":
            return {"result", "first_record", "last_record"}
        case "knowledge-retrieval":
            return {"result"}
        case "human-input":
            names = {
                str(item.get("output_variable_name"))
                for item in params.get("inputs", [])
                if isinstance(item, dict) and item.get("output_variable_name")
            }
            return {*names, "selected_action", "submitted_at", "__action_id", "__action_value", "__rendered_content"}
        case "iteration":
            return {"output", "item", "index"}
        case "loop":
            labels = {
                str(item.get("label"))
                for item in params.get("loop_variables", [])
                if isinstance(item, dict) and item.get("label")
            }
            return {*labels, "loop_round"}
        case "tool" | "agent":
            return {"text", "files", "json", *_schema_output_names(params)}
        case "datasource" | "datasource-empty":
            return {"datasource_type", "file", *_schema_output_names(params)}
        case "knowledge-index":
            return {"result", "document_ids", *_schema_output_names(params)}
        case "trigger-webhook":
            return _webhook_outputs(params)
        case "trigger-schedule":
            return {"sys.timestamp"}
        case "trigger-plugin":
            return _external_trigger_outputs(params)
    return set()


def _schema_output_names(params: dict[str, Any]) -> set[str]:
    raw_data = params.get("_raw_data") if isinstance(params.get("_raw_data"), dict) else params
    schema = raw_data.get("output_schema") if isinstance(raw_data, dict) else None
    properties = schema.get("properties") if isinstance(schema, dict) and isinstance(schema.get("properties"), dict) else {}
    return {str(key) for key in properties if str(key)}


def _external_trigger_outputs(params: dict[str, Any]) -> set[str]:
    passthrough = isinstance(params.get("_raw_data"), dict)
    raw_data = params.get("_raw_data") if passthrough else params
    schema_names = _schema_output_names(params)
    if schema_names:
        return schema_names
    outputs = {"payload"} if passthrough else set()
    variables = raw_data.get("variables") if isinstance(raw_data, dict) else None
    for item in variables if isinstance(variables, list) else []:
        if isinstance(item, dict):
            name = item.get("name") or item.get("variable")
            if name:
                outputs.add(str(name))
    return outputs


def _webhook_outputs(params: dict[str, Any]) -> set[str]:
    raw_data = params.get("_raw_data") if isinstance(params.get("_raw_data"), dict) else params
    outputs = {"_webhook_raw"}
    for group in ("headers", "params", "body"):
        for item in raw_data.get(group) if isinstance(raw_data.get(group), list) else []:
            if not isinstance(item, dict) or not item.get("name"):
                continue
            name = str(item["name"]).replace("-", "_") if group == "headers" else str(item["name"])
            outputs.add(name)
    for item in raw_data.get("variables") if isinstance(raw_data.get("variables"), list) else []:
        if isinstance(item, dict) and (item.get("variable") or item.get("name")):
            outputs.add(str(item.get("variable") or item.get("name")))
    return outputs


def _selectors_in_value(value: Any) -> list[list[str]]:
    selectors: list[list[str]] = []
    if isinstance(value, dict):
        if value.get("type") == "variable" and _is_selector(value.get("value")):
            selectors.append([str(item) for item in value["value"]])
        for key, child in value.items():
            if key == "_raw_data":
                continue
            if (
                key
                in {
                    "value_selector",
                    "variable_selector",
                    "query",
                    "query_variable_selector",
                    "query_attachment_selector",
                    "iterator_selector",
                    "output_selector",
                    "variable",
                }
                and _is_selector(child)
            ):
                selectors.append([str(item) for item in child])
            elif key == "variables" and isinstance(child, list):
                for item in child:
                    if _is_selector(item):
                        selectors.append([str(part) for part in item])
                    elif isinstance(item, dict):
                        selectors.extend(_selectors_in_value(item))
            elif key in {"value", "key"} and _is_selector(child):
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
