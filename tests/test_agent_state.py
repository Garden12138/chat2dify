from __future__ import annotations

from datetime import datetime, timezone

import pytest
from pydantic import TypeAdapter, ValidationError

from app.agent.state import (
    AgentDecision,
    AgentRun,
    AgentSession,
    GoalPlan,
    IllegalRunTransition,
    RunPhase,
    RunStatus,
)


def test_decision_protocol_accepts_only_three_discriminated_types() -> None:
    adapter = TypeAdapter(AgentDecision)

    tool_call = adapter.validate_python(
        {
            "type": "tool_call",
            "tool_name": "workflow.inspect",
            "arguments": {"node_id": "llm-1"},
            "goal_step_id": "inspect",
        }
    )
    ask_user = adapter.validate_python(
        {
            "type": "ask_user",
            "question": "Which branch should be the fallback?",
            "missing": ["fallback behavior"],
        }
    )
    finish = adapter.validate_python(
        {
            "type": "finish",
            "summary": "The workspace is ready for review.",
            "evidence": ["validation passed"],
        }
    )

    assert tool_call.type == "tool_call"
    assert ask_user.type == "ask_user"
    assert finish.type == "finish"
    with pytest.raises(ValidationError):
        adapter.validate_python({"type": "commit", "workspace_version": "v1"})
    with pytest.raises(ValidationError):
        adapter.validate_python(
            {
                "type": "finish",
                "summary": "done",
                "evidence": [],
                "unexpected": True,
            }
        )


def test_goal_plan_validates_dependencies_and_unique_steps() -> None:
    plan = GoalPlan(
        goal="Add a safe fallback.",
        success_criteria=["Validation passes."],
        steps=[
            {"id": "inspect", "description": "Inspect the selected node."},
            {
                "id": "patch",
                "description": "Apply a bounded Patch.",
                "depends_on": ["inspect"],
            },
        ],
    )

    assert plan.revision == 1
    with pytest.raises(ValidationError, match="unknown steps"):
        GoalPlan(
            goal="bad",
            success_criteria=["never"],
            steps=[
                {
                    "id": "patch",
                    "description": "Apply.",
                    "depends_on": ["missing"],
                }
            ],
        )
    with pytest.raises(ValidationError, match="must be unique"):
        GoalPlan(
            goal="bad",
            success_criteria=["never"],
            steps=[
                {"id": "same", "description": "First."},
                {"id": "same", "description": "Second."},
            ],
        )


def test_run_state_machine_rejects_illegal_transitions() -> None:
    session = AgentSession(app_id="app-1", app_mode="workflow")
    run = AgentRun(session_id=session.id, goal="Inspect and review.")

    assert run.status == RunStatus.QUEUED
    observing = run.transition_to(RunPhase.OBSERVING)
    planning = observing.transition_to(RunPhase.PLANNING)
    waiting_user = planning.transition_to(RunPhase.WAITING_USER)
    resumed = waiting_user.transition_to(RunPhase.PLANNING)

    assert waiting_user.paused is True
    assert waiting_user.recoverable is True
    assert resumed.status == RunStatus.RUNNING
    with pytest.raises(IllegalRunTransition) as exc_info:
        resumed.transition_to(RunPhase.COMPLETED)
    assert exc_info.value.code == "AGENT_RUN_TRANSITION_INVALID"

    committing = (
        resumed.transition_to(RunPhase.ACTING)
        .transition_to(RunPhase.VALIDATING)
        .transition_to(RunPhase.WAITING_APPROVAL)
        .transition_to(RunPhase.COMMITTING)
    )
    completed = committing.transition_to(
        RunPhase.COMPLETED,
        now=datetime(2026, 7, 25, tzinfo=timezone.utc),
    )
    assert completed.terminal is True
    assert completed.finished_at is not None
    with pytest.raises(IllegalRunTransition):
        completed.transition_to(RunPhase.ACTING)


def test_run_status_and_terminal_timestamp_are_consistent() -> None:
    with pytest.raises(ValidationError, match="does not match phase"):
        AgentRun(
            session_id="session-1",
            goal="bad",
            status="running",
            phase="queued",
        )
    with pytest.raises(ValidationError, match="require finished_at"):
        AgentRun(
            session_id="session-1",
            goal="bad",
            status="completed",
            phase="completed",
        )
