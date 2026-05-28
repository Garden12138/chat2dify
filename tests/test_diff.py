from app.agent.diff import diff_plans
from app.agent.planner import fallback_plan
from app.models import WorkflowPlan


def test_diff_plans_reports_node_parameter_and_edge_changes() -> None:
    before = fallback_plan("hello", app_name="Diff")
    after = WorkflowPlan.model_validate(
        {
            "name": "Diff",
            "nodes": [
                {"id": "start", "type": "start", "params": {"variables": [{"name": "query"}]}},
                {"id": "template", "type": "template-transform", "params": {"template": "{{#start.query#}}"}},
                {"id": "llm", "type": "llm", "params": {"user_prompt": "Changed {{#template.output#}}"}},
                {"id": "end", "type": "end", "params": {"outputs": [{"variable": "answer", "value_selector": ["llm", "text"]}]}},
            ],
            "edges": [
                {"source": "start", "target": "template"},
                {"source": "template", "target": "llm"},
                {"source": "llm", "target": "end"},
            ],
        }
    )

    changes = diff_plans(before, after)
    change_types = {change["type"] for change in changes}

    assert "node_added" in change_types
    assert "node_updated" in change_types
    assert "edge_added" in change_types
    assert "edge_removed" in change_types


def test_diff_plans_noop_returns_empty_list() -> None:
    plan = fallback_plan("hello")

    assert diff_plans(plan, plan) == []
