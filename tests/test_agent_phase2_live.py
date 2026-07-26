from __future__ import annotations

from copy import deepcopy
import os
from pathlib import Path
from typing import Any
from urllib.parse import urlparse
from uuid import uuid4

import pytest

from app.agent.context import BuilderContext
from app.agent.decision import AgentDecisionProvider
from app.agent.planner import fallback_plan
from app.agent.state import (
    CanvasViewport,
    FinishDecision,
    RunConstraints,
    RunPhase,
    ToolCallDecision,
)
from app.config import Settings, load_settings
from app.dify.client import DifyClient
from app.dify.version import read_dify_version_info
from tests.test_agent_phase1a_live import (
    _compiler,
    _delete_temporary_app,
    _stack,
)


LIVE_ACCEPTANCE_ENABLED = (
    os.environ.get("CHAT2DIFY_LIVE_DIFY_ACCEPTANCE", "").strip() == "1"
)
LOCAL_DIFY_HOSTS = {"localhost", "127.0.0.1", "::1"}
PROFESSIONAL_JSON_PROMPT = (
    "你是专业的售后支持专家。先核对事实，再给出简洁、可执行的建议。"
    '仅输出 JSON 对象，必须符合 {"answer": "string", "confidence": 0.0}；'
    "不要输出 Markdown 或 JSON 之外的文字。"
)

pytestmark = [
    pytest.mark.live_dify,
    pytest.mark.skipif(
        not LIVE_ACCEPTANCE_ENABLED,
        reason=(
            "Set CHAT2DIFY_LIVE_DIFY_ACCEPTANCE=1 to create and delete "
            "temporary apps in a localhost Dify instance."
        ),
    ),
]


class SelectedLlmDecisionProvider(AgentDecisionProvider):
    def __init__(self) -> None:
        self.calls = 0
        self.selected_node_id: str | None = None

    def decide(self, context: BuilderContext, tools):
        del tools
        self.calls += 1
        selected_nodes = context.selection.get("selected_nodes") or []
        assert len(selected_nodes) == 1
        selected = selected_nodes[0]
        assert selected["type"] == "llm"
        self.selected_node_id = selected["id"]
        if self.calls == 1:
            return ToolCallDecision(
                type="tool_call",
                tool_name="workflow.inspect",
                arguments={
                    "view": "nodes",
                    "node_ids": [self.selected_node_id],
                },
                goal_step_id="observe",
            )
        if self.calls == 2:
            return ToolCallDecision(
                type="tool_call",
                tool_name="workflow.patch",
                arguments={
                    "workspace_version": context.workspace["version"],
                    "expected_base_hash": context.app["base_hash"],
                    "rationale": (
                        "Make the selected LLM response professional and constrain "
                        "it to a documented JSON object."
                    ),
                    "operations": [
                        {
                            "op": "node.update",
                            "node_id": self.selected_node_id,
                            "set": {
                                "params.system_prompt": PROFESSIONAL_JSON_PROMPT,
                            },
                            "expected": {"type": "llm"},
                        }
                    ],
                },
                goal_step_id="patch",
            )
        if self.calls == 3:
            return ToolCallDecision(
                type="tool_call",
                tool_name="workflow.diff",
                arguments={"workspace_version": context.workspace["version"]},
                goal_step_id="review",
            )
        return FinishDecision(
            type="finish",
            summary="The selected LLM Prompt and JSON constraint are ready.",
            evidence=[
                "The selected node was resolved from bounded canvas context.",
                "The Prompt Patch passed deterministic validation.",
                "The exact Workspace version has a reviewable Diff.",
            ],
        )


@pytest.fixture(scope="module")
def live_settings() -> Settings:
    settings = load_settings()
    hostname = urlparse(settings.dify_console_api_base).hostname
    if hostname not in LOCAL_DIFY_HOSTS:
        pytest.fail(
            "Live Phase 2 acceptance is restricted to a localhost Dify instance."
        )
    if not settings.dify_email or not settings.dify_password:
        pytest.fail(
            "DIFY_EMAIL and DIFY_PASSWORD are required for live Phase 2 acceptance."
        )
    return settings


@pytest.mark.parametrize("mode", ["workflow", "advanced-chat"])
def test_selected_llm_commit_and_reviewed_compensating_undo_against_local_dify(
    tmp_path: Path,
    live_settings: Settings,
    mode: str,
) -> None:
    version = read_dify_version_info(live_settings.dify_source_path)
    assert version.git_describe.startswith("1.14.2")
    assert version.app_dsl_version == "0.6.0"
    compiler = _compiler(live_settings, version)
    name = f"chat2dify-p2-live-{mode}-{uuid4().hex[:10]}"
    app_id: str | None = None

    try:
        baseline_plan = fallback_plan(
            "处理用户问题",
            app_name=name,
            app_mode=mode,
        )
        with DifyClient(live_settings) as client:
            imported = client.import_yaml(
                compiler.compile(baseline_plan),
                name=name,
                idempotency_key=f"p2-live-{uuid4()}",
            )
            assert imported.app_id
            app_id = imported.app_id
            baseline = client.get_draft_workflow(app_id)
        llm_node_id = _single_llm_node_id(baseline.graph)
        baseline_prompt = _llm_system_prompt(baseline.graph, llm_node_id)
        baseline_non_llm_nodes = _nodes_except(baseline.graph, llm_node_id)
        baseline_edges = deepcopy(baseline.graph.get("edges") or [])

        decision = SelectedLlmDecisionProvider()
        stack = _stack(
            tmp_path / f"{mode}.sqlite3",
            settings=live_settings,
            version=version,
            compiler=compiler,
            decision_provider=decision,
        )
        session = stack.service.create_session(app_id=app_id, app_mode=mode)
        submitted = stack.service.submit_goal(
            session.id,
            message="把选中的 LLM 节点 Prompt 改得更专业，并增加 JSON 输出约束。",
            constraints=RunConstraints(
                selected_node_ids=[llm_node_id],
                viewport=CanvasViewport(x=8, y=16, zoom=1.1),
                current_panel="canvas",
                canvas_draft_hash=baseline.hash,
                dirty_state=False,
                canvas_context_revision=1,
            ),
        )
        run = stack.store.get_run(submitted.id)

        assert decision.selected_node_id == llm_node_id
        assert llm_node_id not in run.goal
        assert run.phase == RunPhase.WAITING_APPROVAL
        assert run.review is not None
        assert run.review["workspace_version_id"] == run.head_version_id
        assert any(
            change.get("type") == "prompt_changed"
            and change.get("target") == llm_node_id
            for change in run.review["technical_diff"]
        )

        with DifyClient(live_settings) as client:
            before_approval = client.get_draft_workflow(app_id)
        assert before_approval.hash == baseline.hash
        assert before_approval.graph == baseline.graph

        approved = _approve_commit(stack, run.id)
        committed = stack.service.commit(
            run.id,
            workspace_version_id=run.head_version_id,
            approval_id=approved.id,
        )
        assert committed.status == "committed"
        assert committed.write_performed is True
        with DifyClient(live_settings) as client:
            changed = client.get_draft_workflow(app_id)
        assert changed.hash == committed.new_hash
        changed_prompt = _llm_system_prompt(changed.graph, llm_node_id)
        assert changed_prompt.startswith(PROFESSIONAL_JSON_PROMPT)
        assert "语言策略：" in changed_prompt
        assert _nodes_except(changed.graph, llm_node_id) == baseline_non_llm_nodes
        assert (changed.graph.get("edges") or []) == baseline_edges
        assert changed.features == baseline.features
        assert changed.environment_variables == baseline.environment_variables
        assert changed.conversation_variables == baseline.conversation_variables

        undo = stack.service.undo(
            run.id,
            workspace_version_id=run.head_version_id,
        )
        assert undo.kind == "post_commit"
        assert undo.run.id != run.id
        assert undo.run.phase == RunPhase.WAITING_APPROVAL
        assert undo.run.review["workspace_version_id"] == undo.workspace_version_id
        with DifyClient(live_settings) as client:
            before_compensation = client.get_draft_workflow(app_id)
        assert before_compensation.hash == changed.hash
        assert _llm_system_prompt(before_compensation.graph, llm_node_id) == changed_prompt

        compensating_approval = _approve_commit(stack, undo.run.id)
        restored = stack.service.commit(
            undo.run.id,
            workspace_version_id=undo.workspace_version_id,
            approval_id=compensating_approval.id,
        )
        assert restored.status == "committed"
        assert restored.write_performed is True
        with DifyClient(live_settings) as client:
            compensated = client.get_draft_workflow(app_id)
        assert compensated.hash == restored.new_hash
        assert compensated.hash == baseline.hash
        assert compensated.hash != changed.hash
        assert _llm_system_prompt(compensated.graph, llm_node_id) == baseline_prompt
        assert _nodes_except(compensated.graph, llm_node_id) == baseline_non_llm_nodes
        assert (compensated.graph.get("edges") or []) == baseline_edges
        assert compensated.features == baseline.features
        assert compensated.environment_variables == baseline.environment_variables
        assert compensated.conversation_variables == baseline.conversation_variables
    finally:
        if app_id is not None:
            _delete_temporary_app(live_settings, app_id)


def _approve_commit(stack, run_id: str):
    pending = stack.store.list_approvals(run_id)[0]
    approved, next_approval = stack.service.resolve_approval(
        run_id,
        pending.id,
        approved=True,
    )
    if next_approval is None:
        return approved
    approved, final_approval = stack.service.resolve_approval(
        run_id,
        next_approval.id,
        approved=True,
    )
    assert final_approval is None
    return approved


def _single_llm_node_id(graph: dict[str, Any]) -> str:
    ids = [
        str(node.get("id"))
        for node in graph.get("nodes") or []
        if isinstance(node, dict)
        and isinstance(node.get("data"), dict)
        and node["data"].get("type") == "llm"
    ]
    assert len(ids) == 1
    return ids[0]


def _llm_system_prompt(graph: dict[str, Any], node_id: str) -> str:
    for node in graph.get("nodes") or []:
        if not isinstance(node, dict) or str(node.get("id")) != node_id:
            continue
        data = node.get("data") if isinstance(node.get("data"), dict) else {}
        for item in data.get("prompt_template") or []:
            if isinstance(item, dict) and item.get("role") == "system":
                return str(item.get("text") or "")
    raise AssertionError(f"LLM system Prompt not found for node {node_id}.")


def _nodes_except(graph: dict[str, Any], node_id: str) -> list[dict[str, Any]]:
    return [
        deepcopy(node)
        for node in graph.get("nodes") or []
        if isinstance(node, dict) and str(node.get("id")) != node_id
    ]
