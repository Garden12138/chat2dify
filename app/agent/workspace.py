from __future__ import annotations

from copy import deepcopy
import re
from typing import Any
from uuid import uuid4

from pydantic import Field, ValidationError

from app.agent.catalog import NodeCapabilityCatalog
from app.agent.normalizer import normalize_plan_payload
from app.agent.patch import (
    AddEdge,
    AddNode,
    ConversationVariableAdd,
    ConversationVariableRemove,
    ConversationVariableUpdate,
    PatchDocument,
    RemoveEdge,
    UpdateNode,
)
from app.agent.state import (
    AgentRun,
    AgentWorkflowSnapshot,
    GoalPlan,
    RunPhase,
    StrictModel,
    WorkspaceVersion,
    utc_now,
)
from app.agent.store import AgentStore, AgentStoreConflict
from app.agent.validation import AgentValidationReport, WorkflowValidationService
from app.models import PlanEdge, PlanNode, WorkflowPlan


class WorkspaceOperationError(RuntimeError):
    def __init__(
        self,
        code: str,
        message: str,
        *,
        details: list[dict[str, Any]] | None = None,
        retryable: bool = False,
    ) -> None:
        super().__init__(message)
        self.code = code
        self.details = details or []
        self.retryable = retryable


class PatchApplyResult(StrictModel):
    workspace_version: str
    parent_version: str
    temp_ref_map: dict[str, str] = Field(default_factory=dict)
    validation: AgentValidationReport
    normalization_changes: list[str] = Field(default_factory=list)


class WorkspaceUndoResult(StrictModel):
    run_id: str
    from_version_id: str
    workspace_version_id: str


class CompensatingPreviewResult(StrictModel):
    run_id: str
    source_run_id: str
    source_version_id: str
    workspace_version_id: str
    parent_version_id: str
    validation: AgentValidationReport


class VersionedWorkflowWorkspace:
    def __init__(
        self,
        *,
        store: AgentStore,
        validation: WorkflowValidationService,
        catalog: NodeCapabilityCatalog,
    ) -> None:
        self.store = store
        self.validation = validation
        self.catalog = catalog

    def initialize(
        self,
        run: AgentRun,
        snapshot: AgentWorkflowSnapshot,
        goal_plan: GoalPlan,
    ) -> tuple[AgentRun, WorkspaceVersion]:
        plan = WorkflowPlan.model_validate(snapshot.base_plan)
        report = self.validation.validate(plan)
        if not report.ok:
            raise WorkspaceOperationError(
                "WORKSPACE_BASE_INVALID",
                "The authoritative Dify draft failed the existing validation chain.",
                details=[
                    issue.model_dump(mode="json")
                    for issue in report.issues
                ],
            )
        version = WorkspaceVersion(
            run_id=run.id,
            base_hash=snapshot.base_hash,
            snapshot=plan.model_dump(mode="json"),
            validation=report.model_dump(mode="json"),
        )
        initialized = AgentRun.model_validate(
            {
                **run.model_dump(),
                "base_hash": snapshot.base_hash,
                "head_version_id": version.id,
                "snapshot": snapshot.model_dump(mode="json"),
                "goal_plan": goal_plan.model_dump(mode="json"),
                "updated_at": utc_now(),
            }
        )
        return self.store.initialize_run_workspace(initialized, version)

    def head(self, run_id: str) -> WorkspaceVersion:
        return self.store.get_workspace_head(run_id)

    def head_plan(self, run_id: str) -> WorkflowPlan:
        return WorkflowPlan.model_validate(self.head(run_id).snapshot)

    def apply_patch(
        self,
        run_id: str,
        patch: PatchDocument,
    ) -> PatchApplyResult:
        run = self.store.get_run(run_id)
        creation_commit = run.commit_result or {}
        if (
            creation_commit.get("kind") == "create"
            and creation_commit.get("status")
            in {
                "import_started",
                "import_succeeded_recovery_pending",
                "import_outcome_unknown",
                "created",
            }
        ):
            raise WorkspaceOperationError(
                "WORKSPACE_CREATE_COMMIT_PENDING",
                (
                    "Workspace cannot change after a creation import may have "
                    "started; recover or reconcile that result first."
                ),
            )
        head = self.store.get_workspace_head(run_id)
        if patch.workspace_version != head.id:
            raise WorkspaceOperationError(
                "WORKSPACE_VERSION_MISMATCH",
                "Patch workspace_version does not match the current Workspace head.",
                details=[
                    {
                        "expected": head.id,
                        "actual": patch.workspace_version,
                    }
                ],
            )
        if run.snapshot is None:
            raise WorkspaceOperationError(
                "AGENT_SNAPSHOT_MISSING",
                "Workspace Patch requires a persisted Run Snapshot.",
            )
        if not bool(
            run.snapshot.compatibility.get("mutation_supported", True)
        ):
            raise WorkspaceOperationError(
                "DIFY_VERSION_MUTATION_UNSUPPORTED",
                str(
                    run.snapshot.compatibility.get("reason")
                    or "This Dify/DSL version is diagnostic-only."
                ),
            )
        if run.snapshot.operation == "modify" and run.base_hash is None:
            raise WorkspaceOperationError(
                "WORKSPACE_BASE_HASH_MISSING",
                "Modify-mode Workspace requires a pinned Dify base Hash.",
            )
        if run.snapshot.operation == "create" and run.base_hash is not None:
            raise WorkspaceOperationError(
                "WORKSPACE_CREATE_BASE_HASH_INVALID",
                "Create-mode Workspace cannot acquire a base Hash before import.",
            )
        if patch.expected_base_hash != run.base_hash:
            raise WorkspaceOperationError(
                "WORKSPACE_BASE_HASH_MISMATCH",
                "Patch expected_base_hash does not match the Run base Hash boundary.",
                details=[
                    {
                        "expected": run.base_hash,
                        "actual": patch.expected_base_hash,
                    }
                ],
            )
        before = WorkflowPlan.model_validate(head.snapshot)
        payload = before.model_dump(mode="json")
        temp_ref_map = _allocate_temp_refs(patch, before)
        try:
            _apply_operations(
                payload,
                patch,
                temp_ref_map=temp_ref_map,
                app_mode=before.app_mode,
                catalog=self.catalog,
            )
            normalized = normalize_plan_payload(
                payload,
                app_name=before.name,
                app_description=before.description,
                app_mode=before.app_mode,
            )
            after = WorkflowPlan.model_validate(normalized.payload)
        except WorkspaceOperationError:
            raise
        except ValidationError as exc:
            raise WorkspaceOperationError(
                "WORKSPACE_PATCH_VALIDATION_FAILED",
                "Patched WorkflowPlan failed deterministic model validation.",
                details=exc.errors(
                    include_url=False,
                    include_input=False,
                ),
                retryable=True,
            ) from exc
        except ValueError as exc:
            raise WorkspaceOperationError(
                "WORKSPACE_PATCH_INVALID",
                "Patch did not produce a valid WorkflowPlan.",
                details=[{"message": str(exc)}],
                retryable=True,
            ) from exc
        report = self.validation.validate(after)
        if not report.ok:
            raise WorkspaceOperationError(
                "WORKSPACE_PATCH_VALIDATION_FAILED",
                "Patched WorkflowPlan failed deterministic validation.",
                details=[
                    issue.model_dump(mode="json")
                    for issue in report.issues
                ],
                retryable=True,
            )
        version = WorkspaceVersion(
            run_id=run_id,
            parent_id=head.id,
            base_hash=run.base_hash,
            patch=patch.model_dump(mode="json", by_alias=True),
            reverse_patch={
                "type": "workspace.snapshot.restore",
                "from_version": head.id,
                "snapshot": before.model_dump(mode="json"),
            },
            snapshot=after.model_dump(mode="json"),
            validation=report.model_dump(mode="json"),
        )
        try:
            self.store.commit_workspace_version(
                version,
                expected_head_id=head.id,
                event_message="Accepted Patch created a new Workspace version.",
                event_data={
                    "operation_count": len(patch.operations),
                    "rationale": patch.rationale,
                    "temp_ref_map": temp_ref_map,
                },
            )
        except AgentStoreConflict as exc:
            raise WorkspaceOperationError(
                "WORKSPACE_VERSION_CONFLICT",
                str(exc),
                retryable=True,
            ) from exc
        return PatchApplyResult(
            workspace_version=version.id,
            parent_version=head.id,
            temp_ref_map=temp_ref_map,
            validation=report,
            normalization_changes=normalized.changes,
        )

    def validate_head(self, run_id: str) -> AgentValidationReport:
        head = self.store.get_workspace_head(run_id)
        plan = WorkflowPlan.model_validate(head.snapshot)
        report = self.validation.validate(plan)
        self.store.update_workspace_version(
            head.id,
            validation=report.model_dump(mode="json"),
        )
        return report

    def undo_head(
        self,
        run_id: str,
        *,
        expected_head_id: str,
    ) -> WorkspaceUndoResult:
        run = self.store.get_run(run_id)
        if run.commit_result is not None:
            raise WorkspaceOperationError(
                "WORKSPACE_UNDO_REQUIRES_COMPENSATION",
                "A committed Run must use a reviewed compensating Undo.",
            )
        if run.phase not in {
            RunPhase.WAITING_USER,
            RunPhase.WAITING_APPROVAL,
            RunPhase.PAUSED,
            RunPhase.INTERRUPTED,
        }:
            raise WorkspaceOperationError(
                "WORKSPACE_UNDO_RUN_STATE_INVALID",
                "Pause the Agent Run before moving its Workspace head.",
            )
        if run.head_version_id != expected_head_id:
            raise WorkspaceOperationError(
                "WORKSPACE_VERSION_MISMATCH",
                "Undo must target the current visible Workspace version.",
            )
        current = self.store.get_workspace_version(expected_head_id)
        if current.run_id != run.id or current.parent_id is None:
            raise WorkspaceOperationError(
                "WORKSPACE_UNDO_ROOT",
                "The current Workspace version has no parent to restore.",
            )
        if run.phase != RunPhase.INTERRUPTED:
            updated = run.transition_to(RunPhase.INTERRUPTED)
        else:
            updated = run
        updated = AgentRun.model_validate(
            {
                **updated.model_dump(),
                "head_version_id": current.parent_id,
                "review": None,
                "error": None,
                "updated_at": utc_now(),
            }
        )
        try:
            self.store.move_workspace_head(
                updated,
                expected_head_id=expected_head_id,
                target_head_id=current.parent_id,
                event_message="Workspace Undo restored the parent version without writing to Dify.",
                event_data={"kind": "pre_commit"},
            )
        except AgentStoreConflict as exc:
            raise WorkspaceOperationError(
                "WORKSPACE_VERSION_CONFLICT",
                str(exc),
                retryable=True,
            ) from exc
        return WorkspaceUndoResult(
            run_id=run.id,
            from_version_id=expected_head_id,
            workspace_version_id=current.parent_id,
        )

    def create_compensating_preview(
        self,
        run_id: str,
        *,
        source_version: WorkspaceVersion,
    ) -> CompensatingPreviewResult:
        run = self.store.get_run(run_id)
        if run.snapshot is None or run.snapshot.operation != "modify":
            raise WorkspaceOperationError(
                "WORKSPACE_COMPENSATION_SNAPSHOT_INVALID",
                "Compensating Undo requires a fresh existing-app Snapshot.",
            )
        head = self.store.get_workspace_head(run_id)
        current_plan = WorkflowPlan.model_validate(head.snapshot)
        committed_plan = WorkflowPlan.model_validate(source_version.snapshot)
        if (
            current_plan.model_dump(mode="json")
            != committed_plan.model_dump(mode="json")
        ):
            raise WorkspaceOperationError(
                "WORKSPACE_COMPENSATION_BASE_CHANGED",
                "The current Dify draft no longer matches the committed Workspace version.",
            )
        target_plan = self.reverse_plan(source_version)
        if (
            target_plan.model_dump(mode="json")
            == current_plan.model_dump(mode="json")
        ):
            raise WorkspaceOperationError(
                "WORKSPACE_UNDO_NO_CHANGES",
                "The committed Workspace version has no change to compensate.",
            )
        report = self.validation.validate(target_plan)
        if not report.ok:
            raise WorkspaceOperationError(
                "WORKSPACE_COMPENSATION_INVALID",
                "The compensating Plan failed deterministic validation.",
                details=[
                    issue.model_dump(mode="json")
                    for issue in report.issues
                ],
            )
        version = WorkspaceVersion(
            run_id=run.id,
            parent_id=head.id,
            base_hash=run.base_hash,
            patch={
                "type": "workspace.compensating.restore",
                "source_run_id": source_version.run_id,
                "source_version_id": source_version.id,
            },
            reverse_patch={
                "type": "workspace.snapshot.restore",
                "from_version": head.id,
                "snapshot": current_plan.model_dump(mode="json"),
            },
            snapshot=target_plan.model_dump(mode="json"),
            validation=report.model_dump(mode="json"),
        )
        try:
            self.store.commit_workspace_version(
                version,
                expected_head_id=head.id,
                event_message="Created a reviewed compensating Workspace preview.",
                event_data={
                    "kind": "post_commit_compensation",
                    "source_run_id": source_version.run_id,
                    "source_version_id": source_version.id,
                },
            )
        except AgentStoreConflict as exc:
            raise WorkspaceOperationError(
                "WORKSPACE_VERSION_CONFLICT",
                str(exc),
                retryable=True,
            ) from exc
        return CompensatingPreviewResult(
            run_id=run.id,
            source_run_id=source_version.run_id,
            source_version_id=source_version.id,
            workspace_version_id=version.id,
            parent_version_id=head.id,
            validation=report,
        )

    def precommit_plan(
        self,
        run_id: str,
        version_id: str,
    ) -> tuple[AgentRun, WorkspaceVersion, WorkflowPlan]:
        run = self.store.get_run(run_id)
        if run.head_version_id != version_id:
            raise WorkspaceOperationError(
                "COMMIT_WORKSPACE_VERSION_MISMATCH",
                "Commit version must be the current persisted Workspace head.",
            )
        version = self.store.get_workspace_version(version_id)
        if version.run_id != run_id or version.base_hash != run.base_hash:
            raise WorkspaceOperationError(
                "COMMIT_WORKSPACE_INVALID",
                "Commit version does not belong to the pinned Run and base Hash.",
            )
        report = AgentValidationReport.model_validate(version.validation or {})
        if not report.ok:
            raise WorkspaceOperationError(
                "COMMIT_REQUIRES_VALIDATED_HEAD",
                "Commit requires a deterministically validated Workspace head.",
            )
        return run, version, WorkflowPlan.model_validate(version.snapshot)

    @staticmethod
    def reverse_plan(version: WorkspaceVersion) -> WorkflowPlan:
        reverse = version.reverse_patch or {}
        if reverse.get("type") != "workspace.snapshot.restore":
            raise WorkspaceOperationError(
                "WORKSPACE_REVERSE_PATCH_MISSING",
                "Workspace version does not contain a reversible snapshot Patch.",
            )
        return WorkflowPlan.model_validate(reverse.get("snapshot"))


def _allocate_temp_refs(
    patch: PatchDocument,
    before: WorkflowPlan,
) -> dict[str, str]:
    existing_ids = {node.id for node in before.nodes}
    mapping: dict[str, str] = {}
    for operation in patch.operations:
        if not isinstance(operation, AddNode):
            continue
        if operation.temp_ref in mapping:
            raise WorkspaceOperationError(
                "PATCH_TEMP_REF_DUPLICATE",
                f"Duplicate temp_ref: {operation.temp_ref}",
            )
        node_id = str(uuid4())
        while node_id in existing_ids:
            node_id = str(uuid4())
        mapping[operation.temp_ref] = node_id
        existing_ids.add(node_id)
    return mapping


def _apply_operations(
    payload: dict[str, Any],
    patch: PatchDocument,
    *,
    temp_ref_map: dict[str, str],
    app_mode: str,
    catalog: NodeCapabilityCatalog,
) -> None:
    nodes = payload["nodes"]
    edges = payload["edges"]
    conversation_variables = payload.setdefault("conversation_variables", [])
    for operation in patch.operations:
        if isinstance(operation, AddNode):
            definition = catalog.get(operation.node_type)
            if definition is None or app_mode not in definition.supported_app_modes:
                raise WorkspaceOperationError(
                    "PATCH_NODE_TYPE_UNSUPPORTED",
                    f"Node type {operation.node_type} is not available for {app_mode}.",
                )
            if operation.after_node_id and operation.after_node_id not in {
                str(node.get("id")) for node in nodes
            }:
                raise WorkspaceOperationError(
                    "PATCH_AFTER_NODE_NOT_FOUND",
                    f"after_node_id does not exist: {operation.after_node_id}",
                )
            nodes.append(
                PlanNode(
                    id=temp_ref_map[operation.temp_ref],
                    type=operation.node_type,
                    title=operation.title,
                    params=_resolve_temp_refs(operation.params, temp_ref_map),
                ).model_dump(mode="json")
            )
        elif isinstance(operation, UpdateNode):
            if operation.node_id.startswith("tmp_"):
                raise WorkspaceOperationError(
                    "PATCH_UPDATE_REQUIRES_EXISTING_NODE",
                    "node.update must reference an existing server node ID.",
                )
            node = _require_node(nodes, operation.node_id)
            node_type = str(node.get("type") or "")
            definition = catalog.get(node_type)
            if node_type != "start" and (
                definition is None
                or app_mode not in definition.supported_app_modes
            ):
                raise WorkspaceOperationError(
                    "PATCH_NODE_TYPE_UNSUPPORTED",
                    f"node.update is not available for {node_type} in {app_mode}.",
                )
            _check_expected(node, operation.expected or {})
            _apply_node_update(
                node,
                _resolve_temp_refs(operation.set_values, temp_ref_map),
            )
        elif isinstance(operation, AddEdge):
            edge = PlanEdge(
                source=_resolve_node_reference(operation.source, temp_ref_map),
                source_handle=operation.source_handle,
                target=_resolve_node_reference(operation.target, temp_ref_map),
                target_handle=operation.target_handle,
            ).model_dump(mode="json")
            _require_node(nodes, edge["source"])
            _require_node(nodes, edge["target"])
            if edge in edges:
                raise WorkspaceOperationError(
                    "PATCH_EDGE_DUPLICATE",
                    "edge.add duplicates an existing edge.",
                )
            edges.append(edge)
        elif isinstance(operation, RemoveEdge):
            edge = PlanEdge(
                source=_resolve_node_reference(operation.source, temp_ref_map),
                source_handle=operation.source_handle,
                target=_resolve_node_reference(operation.target, temp_ref_map),
                target_handle=operation.target_handle,
            ).model_dump(mode="json")
            try:
                edges.remove(edge)
            except ValueError as exc:
                raise WorkspaceOperationError(
                    "PATCH_EDGE_NOT_FOUND",
                    "edge.remove did not match an existing edge.",
                ) from exc
        elif isinstance(operation, ConversationVariableAdd):
            _require_conversation_variable_mode(app_mode)
            if any(
                str(item.get("name") or "") == operation.name
                for item in conversation_variables
            ):
                raise WorkspaceOperationError(
                    "PATCH_CONVERSATION_VARIABLE_NAME_CONFLICT",
                    (
                        "conversation_variable.add requires a name that is not "
                        f"already declared: {operation.name}"
                    ),
                )
            conversation_variables.append(
                {
                    "id": str(uuid4()),
                    "name": operation.name,
                    "value_type": operation.value_type,
                    "value": deepcopy(operation.value),
                    "description": operation.description,
                    "selector": ["conversation", operation.name],
                }
            )
        elif isinstance(operation, ConversationVariableUpdate):
            _require_conversation_variable_mode(app_mode)
            variable = _require_conversation_variable(
                conversation_variables,
                operation.variable_id,
            )
            _check_conversation_variable_expected(
                variable,
                expected_name=operation.expected_name,
                expected_value_type=operation.expected_value_type,
            )
            updates = operation.set_values.model_dump(
                mode="json",
                exclude_none=True,
            )
            next_name = str(updates.get("name") or variable.get("name") or "")
            if any(
                item is not variable
                and str(item.get("name") or "") == next_name
                for item in conversation_variables
            ):
                raise WorkspaceOperationError(
                    "PATCH_CONVERSATION_VARIABLE_NAME_CONFLICT",
                    (
                        "conversation_variable.update would duplicate the name "
                        f"{next_name}."
                    ),
                )
            variable.update(deepcopy(updates))
            variable["selector"] = ["conversation", next_name]
        elif isinstance(operation, ConversationVariableRemove):
            _require_conversation_variable_mode(app_mode)
            variable = _require_conversation_variable(
                conversation_variables,
                operation.variable_id,
            )
            _check_conversation_variable_expected(
                variable,
                expected_name=operation.expected_name,
                expected_value_type=operation.expected_value_type,
            )
            conversation_variables.remove(variable)


def _resolve_node_reference(value: str, mapping: dict[str, str]) -> str:
    if value.startswith("tmp_"):
        try:
            return mapping[value]
        except KeyError as exc:
            raise WorkspaceOperationError(
                "PATCH_TEMP_REF_UNKNOWN",
                f"Unknown temp_ref: {value}",
            ) from exc
    return value


def _resolve_temp_refs(value: Any, mapping: dict[str, str]) -> Any:
    if isinstance(value, dict):
        return {
            key: _resolve_temp_refs(item, mapping)
            for key, item in value.items()
        }
    if isinstance(value, list):
        return [_resolve_temp_refs(item, mapping) for item in value]
    if isinstance(value, str):
        if value.startswith("tmp_") and value in mapping:
            return mapping[value]
        resolved = value
        for temp_ref, node_id in mapping.items():
            resolved = re.sub(
                rf"(?<![A-Za-z0-9_-]){re.escape(temp_ref)}(?![A-Za-z0-9_-])",
                node_id,
                resolved,
            )
        return resolved
    return deepcopy(value)


def _require_node(nodes: list[dict[str, Any]], node_id: str) -> dict[str, Any]:
    for node in nodes:
        if str(node.get("id")) == node_id:
            return node
    raise WorkspaceOperationError(
        "PATCH_NODE_NOT_FOUND",
        f"Patch references an unknown node: {node_id}",
    )


def _require_conversation_variable_mode(app_mode: str) -> None:
    if app_mode != "advanced-chat":
        raise WorkspaceOperationError(
            "PATCH_CONVERSATION_VARIABLE_MODE_UNSUPPORTED",
            "Conversation-variable Patch operations require advanced-chat.",
        )


def _require_conversation_variable(
    variables: list[dict[str, Any]],
    variable_id: str,
) -> dict[str, Any]:
    for variable in variables:
        if str(variable.get("id") or "") == variable_id:
            return variable
    raise WorkspaceOperationError(
        "PATCH_CONVERSATION_VARIABLE_NOT_FOUND",
        f"Patch references an unknown conversation variable: {variable_id}",
    )


def _check_conversation_variable_expected(
    variable: dict[str, Any],
    *,
    expected_name: str | None,
    expected_value_type: str | None,
) -> None:
    if expected_name is not None and variable.get("name") != expected_name:
        raise WorkspaceOperationError(
            "PATCH_PRECONDITION_FAILED",
            "Conversation-variable name precondition failed.",
            details=[
                {
                    "variable_id": variable.get("id"),
                    "field": "name",
                    "expected": expected_name,
                    "actual": variable.get("name"),
                }
            ],
        )
    if (
        expected_value_type is not None
        and variable.get("value_type") != expected_value_type
    ):
        raise WorkspaceOperationError(
            "PATCH_PRECONDITION_FAILED",
            "Conversation-variable type precondition failed.",
            details=[
                {
                    "variable_id": variable.get("id"),
                    "field": "value_type",
                    "expected": expected_value_type,
                    "actual": variable.get("value_type"),
                }
            ],
        )


def _check_expected(node: dict[str, Any], expected: dict[str, Any]) -> None:
    for field, expected_value in expected.items():
        actual = _node_field(node, field)
        if actual != expected_value:
            raise WorkspaceOperationError(
                "PATCH_PRECONDITION_FAILED",
                f"node.update precondition failed for {field}.",
                details=[
                    {
                        "node_id": node.get("id"),
                        "field": field,
                        "expected": expected_value,
                        "actual": actual,
                    }
                ],
            )


def _node_field(node: dict[str, Any], field: str) -> Any:
    if field in {"id", "type", "title", "desc", "params"}:
        return deepcopy(node.get(field))
    if field.startswith("params."):
        value: Any = node.get("params", {})
        for part in field.split(".")[1:]:
            if not isinstance(value, dict):
                return None
            value = value.get(part)
        return deepcopy(value)
    raise WorkspaceOperationError(
        "PATCH_NODE_FIELD_FORBIDDEN",
        f"Unsupported node field: {field}",
    )


def _apply_node_update(node: dict[str, Any], values: dict[str, Any]) -> None:
    for field, value in values.items():
        if field in {"id", "type"}:
            raise WorkspaceOperationError(
                "PATCH_NODE_FIELD_FORBIDDEN",
                f"node.update cannot change {field}.",
            )
        if field in {"title", "desc"}:
            node[field] = deepcopy(value)
            continue
        if field == "params":
            if not isinstance(value, dict):
                raise WorkspaceOperationError(
                    "PATCH_NODE_PARAMS_INVALID",
                    "node.update params must be an object.",
                )
            node["params"] = deepcopy(value)
            continue
        if field.startswith("params."):
            params = node.setdefault("params", {})
            if not isinstance(params, dict):
                raise WorkspaceOperationError(
                    "PATCH_NODE_PARAMS_INVALID",
                    "Existing node params are not an object.",
                )
            parts = field.split(".")[1:]
            target = params
            for part in parts[:-1]:
                child = target.setdefault(part, {})
                if not isinstance(child, dict):
                    raise WorkspaceOperationError(
                        "PATCH_NODE_FIELD_CONFLICT",
                        f"Cannot set nested field {field}.",
                    )
                target = child
            target[parts[-1]] = deepcopy(value)
            continue
        raise WorkspaceOperationError(
            "PATCH_NODE_FIELD_FORBIDDEN",
            f"Unsupported node field: {field}",
        )
