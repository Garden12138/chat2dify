from __future__ import annotations

from app.agent.config_app import (
    CONFIG_APP_MODES,
    ConfigAppSnapshotService,
    ConfigReviewService,
    VersionedConfigWorkspace,
)
from app.agent.review import WorkflowReviewService
from app.agent.snapshot import WorkflowSnapshotService
from app.agent.state import AgentConfigSnapshot
from app.agent.workspace import VersionedWorkflowWorkspace


class AgentSnapshotRouter:
    def __init__(
        self,
        *,
        workflow: WorkflowSnapshotService,
        config: ConfigAppSnapshotService,
    ) -> None:
        self.workflow = workflow
        self.config = config

    def capture(self, session):
        if session.app_mode in CONFIG_APP_MODES:
            return self.config.capture(session)
        return self.workflow.capture(session)


class AgentWorkspaceRouter:
    def __init__(
        self,
        *,
        workflow: VersionedWorkflowWorkspace,
        config: VersionedConfigWorkspace,
    ) -> None:
        self.workflow = workflow
        self.config = config

    def initialize(self, run, snapshot, goal_plan):
        if isinstance(snapshot, AgentConfigSnapshot):
            return self.config.initialize(run, snapshot, goal_plan)
        return self.workflow.initialize(run, snapshot, goal_plan)


class AgentReviewRouter:
    def __init__(
        self,
        *,
        workflow: WorkflowReviewService,
        config: ConfigReviewService,
    ) -> None:
        self.workflow = workflow
        self.config = config

    def build(self, run_id: str):
        run = self.workflow.store.get_run(run_id)
        if isinstance(run.snapshot, AgentConfigSnapshot):
            return self.config.build(run_id)
        return self.workflow.build(run_id)
