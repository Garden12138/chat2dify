from app.agent.diff import diff_plans
from app.agent.guard import guard_plan_change
from app.agent.planner import fallback_plan
from app.models import WorkflowPlan


def test_guard_allows_prompt_only_change() -> None:
    before = fallback_plan("hello")
    after = before.model_copy(deep=True)
    after.nodes[1].params["user_prompt"] = "更温和地回答 {{#start.query#}}"
    changes = diff_plans(before, after)

    guard = guard_plan_change(before, after, changes)

    assert guard.ok is True
    assert guard.risk == "low"
    assert guard.no_op is False


def test_guard_marks_noop() -> None:
    plan = fallback_plan("hello")

    guard = guard_plan_change(plan, plan, diff_plans(plan, plan))

    assert guard.ok is True
    assert guard.risk == "low"
    assert guard.no_op is True
    assert guard.issues[0].code == "PLAN_CHANGE_NOOP"


def test_guard_blocks_mass_node_removal() -> None:
    before = WorkflowPlan.model_validate(
        {
            "name": "Guard",
            "nodes": [
                {"id": "start", "type": "start", "params": {"variables": [{"name": "query"}]}},
                {"id": "template", "type": "template-transform", "params": {"template": "{{#start.query#}}"}},
                {"id": "llm", "type": "llm", "params": {"user_prompt": "{{#template.output#}}"}},
                {"id": "end", "type": "end", "params": {"outputs": [{"variable": "answer", "value_selector": ["llm", "text"]}]}},
            ],
            "edges": [
                {"source": "start", "target": "template"},
                {"source": "template", "target": "llm"},
                {"source": "llm", "target": "end"},
            ],
        }
    )
    after = WorkflowPlan.model_validate(
        {
            "name": "Guard",
            "nodes": [
                {"id": "start", "type": "start", "params": {"variables": [{"name": "query"}]}},
                {"id": "end", "type": "end", "params": {"outputs": [{"variable": "answer", "value_selector": ["start", "query"]}]}},
            ],
            "edges": [{"source": "start", "target": "end"}],
        }
    )
    changes = diff_plans(before, after)

    guard = guard_plan_change(before, after, changes)

    assert guard.ok is False
    assert guard.risk == "high"
    assert any(issue.code == "PLAN_CHANGE_MASS_NODE_REMOVAL" for issue in guard.issues)
