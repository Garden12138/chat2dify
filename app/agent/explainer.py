from __future__ import annotations

from app.models import WorkflowPlan


def explain_plan(plan: WorkflowPlan) -> dict[str, list[str] | str]:
    node_types = {node.id: node.type for node in plan.nodes}
    starts = [node for node in plan.nodes if node.type == "start"]
    ends = [node for node in plan.nodes if node.type == "end"]
    llms = [node for node in plan.nodes if node.type == "llm"]
    branches = [node for node in plan.nodes if node.type in {"if-else", "question-classifier"}]

    inputs = []
    for node in starts:
        variables = node.params.get("variables", [])
        names = [item.get("name") or item.get("variable") for item in variables if isinstance(item, dict)]
        inputs.append(f"{node.id} 接收输入变量：{', '.join(str(name) for name in names if name) or 'query'}")

    branch_lines = []
    for node in branches:
        case_text = []
        if node.type == "if-else":
            for case in node.params.get("cases", []):
                conditions = case.get("conditions", []) if isinstance(case, dict) else []
                values = [str(item.get("value", "")).strip() for item in conditions if isinstance(item, dict)]
                values = [value for value in values if value]
                case_text.append(f"{case.get('case_id')}: {' / '.join(values) or '条件判断'}")
            branch_lines.append(f"{node.id} 根据条件分流：{'; '.join(case_text)}；否则走 false 分支")
        else:
            for item in node.params.get("classes", []):
                if isinstance(item, dict):
                    case_text.append(f"{item.get('id')}: {item.get('name')}")
            branch_lines.append(f"{node.id} 根据语义分类分流：{'; '.join(case_text)}")

    steps = []
    for node in plan.nodes:
        if node.type == "llm":
            prompt = str(node.params.get("user_prompt", "")).replace("\n", " ")
            steps.append(f"{node.id} 生成回复：{prompt[:80]}")
        elif node.type == "question-classifier":
            steps.append(f"{node.id} 识别用户输入所属类别")
        elif node.type == "parameter-extractor":
            names = [str(item.get("name")) for item in node.params.get("parameters", []) if isinstance(item, dict)]
            steps.append(f"{node.id} 提取结构化参数：{', '.join(names) or '未配置'}")
        elif node.type == "variable-aggregator":
            steps.append(f"{node.id} 聚合多个候选变量作为统一输出")
        elif node.type == "document-extractor":
            steps.append(f"{node.id} 从文件变量中提取文本内容")
        elif node.type == "assigner":
            steps.append(f"{node.id} 更新已有变量值")
        elif node.type == "list-operator":
            steps.append(f"{node.id} 对数组变量进行筛选、排序或截取")
        elif node.type in {"code", "http-request", "template-transform"}:
            steps.append(f"{node.id} 执行 {node.type} 节点")

    outputs = [f"{node.id} 返回最终输出" for node in ends]
    edge_summary = [
        f"{edge.source}({node_types.get(edge.source, '?')}) -> {edge.target}({node_types.get(edge.target, '?')})"
        for edge in plan.edges
    ]

    return {
        "summary": f"生成了 {len(plan.nodes)} 个节点、{len(plan.edges)} 条连线的 workflow。",
        "inputs": inputs,
        "branches": branch_lines,
        "steps": steps,
        "outputs": outputs,
        "edges": edge_summary,
    }
