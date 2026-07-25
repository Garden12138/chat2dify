from __future__ import annotations

from contextlib import AbstractContextManager
from copy import deepcopy
from typing import Callable, Protocol

from app.agent.catalog import NodeCapabilityCatalog
from app.agent.state import AgentSession, AgentWorkflowSnapshot
from app.dify.client import DifyAppDetail, DifyDraftWorkflow
from app.dify.graph import decompile_dify_graph
from app.dify.version import DifyVersionInfo


class SnapshotClient(Protocol):
    def get_app_detail(self, app_id: str) -> DifyAppDetail: ...

    def get_draft_workflow(self, app_id: str) -> DifyDraftWorkflow: ...


class WorkflowSnapshotError(RuntimeError):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


class WorkflowSnapshotService:
    def __init__(
        self,
        *,
        client_factory: Callable[[], AbstractContextManager[SnapshotClient]],
        catalog: NodeCapabilityCatalog,
        dify_version: DifyVersionInfo,
    ) -> None:
        self.client_factory = client_factory
        self.catalog = catalog
        self.dify_version = dify_version

    def capture(self, session: AgentSession) -> AgentWorkflowSnapshot:
        if not session.app_id:
            raise WorkflowSnapshotError(
                "AGENT_EXISTING_APP_REQUIRED",
                "Phase 1A requires an existing Dify app_id.",
            )
        with self.client_factory() as client:
            app = client.get_app_detail(session.app_id)
            draft = client.get_draft_workflow(session.app_id)
        app_mode = _resolve_graph_app_mode(app, draft)
        if app_mode not in {"workflow", "advanced-chat"}:
            raise WorkflowSnapshotError(
                "AGENT_APP_MODE_UNSUPPORTED",
                "Phase 1A supports only existing Workflow and Chatflow apps.",
            )
        if session.app_mode is not None and session.app_mode != app_mode:
            raise WorkflowSnapshotError(
                "AGENT_APP_MODE_MISMATCH",
                f"Session mode {session.app_mode} does not match Dify mode {app_mode}.",
            )
        if not draft.hash:
            raise WorkflowSnapshotError(
                "AGENT_DRAFT_HASH_MISSING",
                "The current Dify draft did not provide a base Hash.",
            )
        plan = decompile_dify_graph(
            draft.graph,
            name=app.name or f"Dify Workflow {session.app_id}",
            app_mode=app_mode,
            conversation_variables=deepcopy(draft.conversation_variables),
        )
        return AgentWorkflowSnapshot(
            app_id=session.app_id,
            app_name=plan.name,
            app_description=app.description,
            app_mode=app_mode,
            base_hash=draft.hash,
            base_plan=plan.model_dump(mode="json"),
            base_graph=deepcopy(draft.graph),
            features=deepcopy(draft.features),
            environment_variables=deepcopy(draft.environment_variables),
            conversation_variables=deepcopy(draft.conversation_variables),
            dify_version={
                "source_dir": self.dify_version.source_dir,
                "git_describe": self.dify_version.git_describe,
                "app_dsl_version": self.dify_version.app_dsl_version,
                "draft_version": draft.version,
            },
            capabilities=[
                definition.model_dump(mode="json")
                for definition in self.catalog.list()
                if app_mode in definition.supported_app_modes
            ],
        )


def _resolve_graph_app_mode(
    app: DifyAppDetail,
    draft: DifyDraftWorkflow,
) -> str:
    if app.mode in {"workflow", "advanced-chat"}:
        return app.mode
    nodes = draft.graph.get("nodes")
    if isinstance(nodes, list) and any(
        isinstance(node, dict)
        and isinstance(node.get("data"), dict)
        and node["data"].get("type") == "answer"
        for node in nodes
    ):
        return "advanced-chat"
    return app.mode or "workflow"
