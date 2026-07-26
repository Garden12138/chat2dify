from __future__ import annotations

from contextlib import AbstractContextManager
from copy import deepcopy
from typing import Callable, Protocol
from uuid import NAMESPACE_URL, uuid5

from app.agent.catalog import NodeCapabilityCatalog
from app.agent.compatibility import DifyCompatibilityMatrix
from app.agent.state import AgentSession, AgentWorkflowSnapshot
from app.dify.client import DifyAppDetail, DifyDraftWorkflow
from app.dify.graph import decompile_dify_graph
from app.dify.version import DifyVersionInfo
from app.models import WorkflowPlan


class SnapshotClient(Protocol):
    def get_app_detail(self, app_id: str) -> DifyAppDetail: ...

    def get_draft_workflow(self, app_id: str) -> DifyDraftWorkflow: ...

    def list_datasets(self, **kwargs): ...

    def list_models(self, **kwargs): ...

    def list_tools(self, **kwargs): ...

    def list_agent_strategies(self, **kwargs): ...

    def list_trigger_providers(self, **kwargs): ...


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
        compatibility: DifyCompatibilityMatrix | None = None,
    ) -> None:
        self.client_factory = client_factory
        self.catalog = catalog
        self.dify_version = dify_version
        self.compatibility = compatibility

    def capture(self, session: AgentSession) -> AgentWorkflowSnapshot:
        if session.operation == "create":
            return self._create_scaffold_snapshot(session)
        if not session.app_id:
            raise WorkflowSnapshotError(
                "AGENT_EXISTING_APP_REQUIRED",
                "Phase 1A requires an existing Dify app_id.",
            )
        with self.client_factory() as client:
            app = client.get_app_detail(session.app_id)
            draft = client.get_draft_workflow(session.app_id)
            resource_capabilities = _resource_capabilities(client)
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
        capabilities = [
            definition.model_dump(mode="json")
            for definition in self.catalog.list()
            if app_mode in definition.supported_app_modes
        ]
        capabilities.extend(resource_capabilities)
        compatibility = (
            self.compatibility.decide(
                self.dify_version,
                app_mode=app_mode,
            )
            if self.compatibility is not None
            else None
        )
        if compatibility is not None:
            capabilities = self.compatibility.pin_capabilities(
                capabilities,
                decision=compatibility,
            )
        return AgentWorkflowSnapshot(
            operation="modify",
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
            capabilities=capabilities,
            compatibility=(
                compatibility.model_dump(mode="json")
                if compatibility is not None
                else {}
            ),
        )

    def _create_scaffold_snapshot(
        self,
        session: AgentSession,
    ) -> AgentWorkflowSnapshot:
        if session.app_id is not None:
            raise WorkflowSnapshotError(
                "AGENT_CREATE_APP_ALREADY_BOUND",
                "A create Session cannot initialize after it is bound to a Dify app.",
            )
        if session.app_mode not in {"workflow", "advanced-chat"}:
            raise WorkflowSnapshotError(
                "AGENT_APP_MODE_UNSUPPORTED",
                "Phase 1B supports only new Workflow and Chatflow apps.",
            )
        plan = _create_scaffold_plan(session)
        capabilities = [
            definition.model_dump(mode="json")
            for definition in self.catalog.list()
            if session.app_mode in definition.supported_app_modes
        ]
        with self.client_factory() as client:
            capabilities.extend(_resource_capabilities(client))
        compatibility = (
            self.compatibility.decide(
                self.dify_version,
                app_mode=session.app_mode,
            )
            if self.compatibility is not None
            else None
        )
        if compatibility is not None:
            capabilities = self.compatibility.pin_capabilities(
                capabilities,
                decision=compatibility,
            )
        return AgentWorkflowSnapshot(
            operation="create",
            app_id=None,
            app_name=plan.name,
            app_description=plan.description,
            app_mode=session.app_mode,
            base_hash=None,
            base_plan=plan.model_dump(mode="json"),
            base_graph={},
            features={},
            environment_variables=[],
            conversation_variables=[],
            dify_version={
                "source_dir": self.dify_version.source_dir,
                "git_describe": self.dify_version.git_describe,
                "app_dsl_version": self.dify_version.app_dsl_version,
                "draft_version": "not-imported",
            },
            capabilities=capabilities,
            compatibility=(
                compatibility.model_dump(mode="json")
                if compatibility is not None
                else {}
            ),
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


def _resource_capabilities(client: object) -> list[dict]:
    capabilities: list[dict] = []
    datasets = _safe_resource_list(client, "list_datasets", limit=100)
    for item in datasets:
        capabilities.append(
            {
                "type": "dataset",
                "id": str(getattr(item, "id", "")),
                "name": str(getattr(item, "name", "")),
                "summary": str(getattr(item, "description", "") or ""),
                "document_count": getattr(item, "document_count", None),
                "indexing_technique": getattr(
                    item,
                    "indexing_technique",
                    None,
                ),
                "embedding_available": getattr(
                    item,
                    "embedding_available",
                    None,
                ),
                "untrusted_data": True,
            }
        )
    models = _safe_resource_list(client, "list_models")
    for item in models:
        capabilities.append(
            {
                "type": "model",
                "provider": str(getattr(item, "provider", "")),
                "name": str(getattr(item, "model", "")),
                "summary": str(getattr(item, "model_label", "") or ""),
                "status": str(getattr(item, "status", "")),
                "features": list(getattr(item, "features", []) or []),
                "untrusted_data": True,
            }
        )
    tools = _safe_resource_list(client, "list_tools")
    for item in tools:
        capabilities.append(
            {
                "type": "tool-resource",
                "provider_id": str(getattr(item, "provider_id", "")),
                "provider_type": str(getattr(item, "provider_type", "")),
                "tool_name": str(getattr(item, "tool_name", "")),
                "summary": str(getattr(item, "description", "") or ""),
                "requires_configuration": bool(
                    getattr(item, "requires_configuration", False)
                ),
                "untrusted_data": True,
            }
        )
    strategies = _safe_resource_list(client, "list_agent_strategies")
    for item in strategies:
        capabilities.append(
            {
                "type": "agent-strategy",
                "provider": str(
                    getattr(item, "agent_strategy_provider_name", "")
                ),
                "name": str(getattr(item, "agent_strategy_name", "")),
                "summary": str(getattr(item, "description", "") or ""),
                "features": list(getattr(item, "features", []) or []),
                "requires_configuration": bool(
                    getattr(item, "requires_configuration", False)
                ),
                "untrusted_data": True,
            }
        )
    triggers = _safe_resource_list(client, "list_trigger_providers")
    for item in triggers:
        capabilities.append(
            {
                "type": "trigger",
                "provider_id": str(getattr(item, "provider_id", "")),
                "event_name": str(getattr(item, "event_name", "")),
                "summary": str(
                    getattr(item, "event_description", "")
                    or getattr(item, "description", "")
                    or ""
                ),
                "supported_creation_methods": list(
                    getattr(item, "supported_creation_methods", []) or []
                ),
                "untrusted_data": True,
            }
        )
    return capabilities


def _safe_resource_list(
    client: object,
    method_name: str,
    **kwargs,
) -> list[object]:
    method = getattr(client, method_name, None)
    if not callable(method):
        return []
    try:
        result = method(**kwargs)
    except Exception:  # noqa: BLE001 - one unavailable catalog must not block Snapshot.
        return []
    data = getattr(result, "data", None)
    return list(data) if isinstance(data, list) else []


def _create_scaffold_plan(session: AgentSession) -> WorkflowPlan:
    app_mode = session.app_mode
    if app_mode not in {"workflow", "advanced-chat"}:
        raise WorkflowSnapshotError(
            "AGENT_APP_MODE_UNSUPPORTED",
            "Create scaffold requires Workflow or Chatflow mode.",
        )
    start_id = _scaffold_node_id(session.id, "start")
    terminal_kind = "answer" if app_mode == "advanced-chat" else "end"
    terminal_id = _scaffold_node_id(session.id, terminal_kind)
    if app_mode == "advanced-chat":
        terminal = {
            "id": terminal_id,
            "type": "answer",
            "title": "返回用户问题",
            "params": {"answer": "{{#sys.query#}}"},
        }
        start_params = {"variables": []}
        default_name = "New Chatflow"
    else:
        terminal = {
            "id": terminal_id,
            "type": "end",
            "title": "返回用户输入",
            "params": {
                "outputs": [
                    {
                        "variable": "answer",
                        "value_selector": [start_id, "query"],
                    }
                ]
            },
        }
        start_params = {
            "variables": [
                {
                    "name": "query",
                    "type": "paragraph",
                    "required": True,
                    "label": "用户输入",
                }
            ]
        }
        default_name = "New Workflow"
    return WorkflowPlan.model_validate(
        {
            "name": session.app_name or default_name,
            "description": (
                session.app_description
                or "Created from a deterministic Chat2Dify Builder Agent scaffold."
            ),
            "app_mode": app_mode,
            "nodes": [
                {
                    "id": start_id,
                    "type": "start",
                    "title": "接收用户输入",
                    "params": start_params,
                },
                terminal,
            ],
            "edges": [
                {
                    "source": start_id,
                    "target": terminal_id,
                }
            ],
        }
    )


def _scaffold_node_id(session_id: str, role: str) -> str:
    return str(
        uuid5(
            NAMESPACE_URL,
            f"chat2dify:v4:create:{session_id}:{role}",
        )
    )
