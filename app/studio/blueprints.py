from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass
from hashlib import sha256
import json
import re
from typing import Any, Iterable, Literal
from uuid import NAMESPACE_URL, uuid5

from app.agent.catalog import NodeCapabilityCatalog
from app.agent.patch import PatchDocument
from app.agent.review import WorkflowReviewService
from app.agent.service import AgentApplicationService
from app.agent.snapshot import WorkflowSnapshotService
from app.agent.state import (
    AgentRun,
    AgentSession,
    AgentWorkflowSnapshot,
    GoalPlan,
    GoalStep,
    RunConstraints,
    RunPhase,
)
from app.agent.store import AgentStore
from app.agent.trace import redact_sensitive_data
from app.agent.workspace import VersionedWorkflowWorkspace, WorkspaceOperationError
from app.models import PlanEdge, PlanNode, WorkflowPlan
from app.studio.build import StudioBuildService
from app.studio.identity import AuthenticatedStudioRequest
from app.studio.models import (
    BlueprintApplication,
    BlueprintApplyResult,
    BlueprintAvailability,
    BlueprintDefinition,
    BlueprintGallery,
    BlueprintGalleryItem,
    BlueprintInterfaceField,
    BlueprintPreview,
    BlueprintPreviewEdge,
    BlueprintPreviewNode,
    BlueprintProvenance,
    BlueprintResourceRequirement,
    BlueprintScenario,
    BlueprintSetupField,
    BlueprintSetupValidation,
    BlueprintSetupValue,
    BlueprintTypedInterface,
    BlueprintUpgradePreview,
    BlueprintVersionRecord,
    StudioBuild,
    StudioCandidate,
    new_id,
    utc_now,
)
from app.studio.store import (
    StudioAccessDenied,
    StudioConflict,
    StudioRecordNotFound,
    StudioStore,
)


_SEMVER = re.compile(r"^(\d+)\.(\d+)\.(\d+)$")
_SAFE_VARIABLE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]{0,127}$")
_SECRET_PATTERNS = (
    re.compile(r"\bsk-[A-Za-z0-9_-]{8,}\b", re.IGNORECASE),
    re.compile(r"\b(?:api[_-]?key|access[_-]?token|client[_-]?secret|password)\b\s*[:=]", re.IGNORECASE),
    re.compile(r"\bBearer\s+[A-Za-z0-9._~+/-]{8,}", re.IGNORECASE),
    re.compile(r"\$\{[^}]+\}"),
)
_SENSITIVE_KEYS = {
    "authorization",
    "api_key",
    "apikey",
    "access_token",
    "refresh_token",
    "client_secret",
    "password",
    "secret",
    "credential",
    "credentials",
    "environment_variables",
}
_RESOURCE_KINDS = {"dataset", "model", "tool", "trigger"}


class BlueprintError(StudioConflict):
    code = "BLUEPRINT_INVALID"


class BlueprintSetupError(BlueprintError):
    code = "BLUEPRINT_SETUP_INVALID"


class BlueprintUnavailable(BlueprintError):
    code = "BLUEPRINT_UNAVAILABLE"


class BlueprintSecretFound(BlueprintError):
    code = "BLUEPRINT_SECRET_FOUND"


class BlueprintPolicyDenied(BlueprintError):
    code = "BLUEPRINT_POLICY_DENIED"


@dataclass(frozen=True)
class _RegisteredBlueprint:
    definition: BlueprintDefinition
    template: dict[str, Any]
    record: BlueprintVersionRecord | None = None


@dataclass(frozen=True)
class _BlueprintContext:
    build: StudioBuild
    snapshot: AgentWorkflowSnapshot
    plan: WorkflowPlan
    source_candidate: StudioCandidate | None
    source_head_id: str | None


@dataclass(frozen=True)
class _ValidatedSetup:
    values: dict[str, Any]
    field_results: list[dict[str, Any]]


class BlueprintRegistry:
    """Versioned built-ins. Metadata is data; only allowlisted template kinds execute."""

    def __init__(self, items: Iterable[_RegisteredBlueprint] | None = None) -> None:
        registered = list(items) if items is not None else _builtin_blueprints()
        self._items: dict[str, dict[str, _RegisteredBlueprint]] = {}
        for item in registered:
            versions = self._items.setdefault(item.definition.id, {})
            if item.definition.version in versions:
                raise ValueError("Builtin Blueprint versions must be unique.")
            versions[item.definition.version] = item

    def list_current(self) -> list[_RegisteredBlueprint]:
        return [
            self._items[blueprint_id][self.current_version(blueprint_id)]
            for blueprint_id in sorted(self._items)
        ]

    def current_version(self, blueprint_id: str) -> str:
        versions = self._items.get(blueprint_id)
        if not versions:
            raise StudioRecordNotFound("The Blueprint was not found.")
        return max(versions, key=_semver_key)

    def get(self, blueprint_id: str, version: str | None = None) -> _RegisteredBlueprint:
        versions = self._items.get(blueprint_id)
        if not versions:
            raise StudioRecordNotFound("The Blueprint was not found.")
        selected = version or self.current_version(blueprint_id)
        try:
            return versions[selected]
        except KeyError as exc:
            raise StudioRecordNotFound("The Blueprint version was not found.") from exc


class StudioBlueprintService:
    def __init__(
        self,
        *,
        store: StudioStore,
        agent_store: AgentStore,
        agent_service: AgentApplicationService,
        build_service: StudioBuildService,
        snapshot_service: WorkflowSnapshotService,
        workspace: VersionedWorkflowWorkspace,
        review: WorkflowReviewService,
        catalog: NodeCapabilityCatalog,
        registry: BlueprintRegistry | None = None,
    ) -> None:
        self.store = store
        self.agent_store = agent_store
        self.agent_service = agent_service
        self.build_service = build_service
        self.snapshot_service = snapshot_service
        self.workspace = workspace
        self.review = review
        self.catalog = catalog
        self.registry = registry or BlueprintRegistry()

    def gallery(
        self,
        authenticated: AuthenticatedStudioRequest,
        *,
        project_id: str,
        build_id: str | None = None,
        search: str | None = None,
        category: str | None = None,
        app_mode: str | None = None,
        dify_version: str | None = None,
        risk: str | None = None,
        visibility: str | None = None,
        resource_available: bool | None = None,
        compatible_only: bool = True,
    ) -> BlueprintGallery:
        project, membership = self.store.get_project_for_principal(
            project_id,
            authenticated.principal.key,
        )
        if project.dify_tenant_id != authenticated.principal.dify_tenant_id:
            raise StudioAccessDenied("The Blueprint Project is outside the verified Dify Workspace.")
        context = self._context(
            authenticated,
            project_id=project_id,
            build_id=build_id,
            required=False,
        )
        registered = self._visible_blueprints(
            project_id=project_id,
            principal_key=authenticated.principal.key,
        )
        needle = (search or "").strip().lower()
        items: list[BlueprintGalleryItem] = []
        for item in registered:
            blueprint = item.definition
            availability = self._availability(
                blueprint,
                context=context,
                requested_app_mode=app_mode,
                requested_dify_version=dify_version,
            )
            haystack = " ".join(
                [
                    blueprint.name,
                    blueprint.business_outcome,
                    blueprint.description,
                    blueprint.category,
                    *blueprint.use_cases,
                ]
            ).lower()
            if needle and not all(token in haystack for token in needle.split()):
                continue
            if category and blueprint.category != category:
                continue
            if app_mode and app_mode not in blueprint.supported_app_modes:
                continue
            if risk and blueprint.risk != risk:
                continue
            if visibility and blueprint.visibility != visibility:
                continue
            if resource_available is not None:
                resource_ready = context is not None and not any(
                    reason.get("code") == "BLUEPRINT_RESOURCE_MISSING"
                    for reason in availability.reasons
                )
                if resource_ready != resource_available:
                    continue
            if compatible_only and not availability.compatible:
                continue
            score = _gallery_score(blueprint, needle, availability)
            items.append(
                self._gallery_item(
                    registered=item,
                    availability=availability,
                    score=score,
                    membership_role=membership.role,
                    principal_key=authenticated.principal.key,
                )
            )
        items.sort(key=lambda value: (-value.score, value.blueprint.name.lower()))
        categories = sorted({item.definition.category for item in registered})
        if items:
            state: Literal["ready", "empty"] = "ready"
            message = f"找到 {len(items)} 个符合当前筛选的 Blueprint。"
        else:
            state = "empty"
            message = "没有符合当前兼容性与筛选条件的 Blueprint。"
        return BlueprintGallery(
            project=project,
            membership=membership,
            items=items,
            categories=categories,
            filters={
                "search": search or "",
                "category": category or "",
                "app_mode": app_mode or "",
                "dify_version": dify_version or "",
                "risk": risk or "",
                "visibility": visibility or "",
                "resource_available": resource_available,
                "compatible_only": compatible_only,
                "build_id": build_id,
            },
            state=state,
            message=message,
        )

    def detail(
        self,
        authenticated: AuthenticatedStudioRequest,
        *,
        project_id: str,
        blueprint_id: str,
        version: str | None = None,
        build_id: str | None = None,
    ) -> BlueprintGalleryItem:
        _, membership = self.store.get_project_for_principal(
            project_id,
            authenticated.principal.key,
        )
        registered = self._get_blueprint(
            blueprint_id,
            version=version,
            project_id=project_id,
            principal_key=authenticated.principal.key,
            include_unpublished=True,
        )
        context = self._context(
            authenticated,
            project_id=project_id,
            build_id=build_id,
            required=False,
        )
        return self._gallery_item(
            registered=registered,
            availability=self._availability(registered.definition, context=context),
            score=0,
            membership_role=membership.role,
            principal_key=authenticated.principal.key,
        )

    def validate_setup(
        self,
        authenticated: AuthenticatedStudioRequest,
        *,
        project_id: str,
        blueprint_id: str,
        values: list[BlueprintSetupValue],
        build_id: str,
        version: str | None = None,
    ) -> BlueprintSetupValidation:
        registered = self._get_blueprint(
            blueprint_id,
            version=version,
            project_id=project_id,
            principal_key=authenticated.principal.key,
        )
        context = self._context(
            authenticated,
            project_id=project_id,
            build_id=build_id,
            required=True,
        )
        assert context is not None
        availability = self._availability(registered.definition, context=context)
        if not availability.compatible:
            raise BlueprintUnavailable(_reason_message(availability.reasons))
        validated = self._validate_values(
            registered.definition,
            values,
            context.snapshot.capabilities,
        )
        preview = _configured_preview(registered.definition.preview, validated.values)
        return BlueprintSetupValidation(
            ok=True,
            field_results=validated.field_results,
            preview=preview,
            expected_behavior=preview.expected_behavior,
            risk={
                "level": registered.definition.risk,
                "reasons": registered.definition.risk_reasons,
                "estimated_cost": registered.definition.estimated_cost,
                "permission_expansion": False,
                "dify_write": False,
            },
            normalized_values=validated.values,
        )

    def apply(
        self,
        authenticated: AuthenticatedStudioRequest,
        *,
        project_id: str,
        blueprint_id: str,
        values: list[BlueprintSetupValue],
        build_id: str,
        version: str | None = None,
    ) -> BlueprintApplyResult:
        _, membership = self.store.get_project_for_principal(
            project_id,
            authenticated.principal.key,
        )
        if membership.role not in {"owner", "admin", "builder"}:
            raise StudioAccessDenied("Your project role cannot apply Blueprints.")
        registered = self._get_blueprint(
            blueprint_id,
            version=version,
            project_id=project_id,
            principal_key=authenticated.principal.key,
        )
        context = self._context(
            authenticated,
            project_id=project_id,
            build_id=build_id,
            required=True,
        )
        assert context is not None
        availability = self._availability(registered.definition, context=context)
        if not availability.applicable:
            raise BlueprintUnavailable(_reason_message(availability.reasons))
        validated = self._validate_values(
            registered.definition,
            values,
            context.snapshot.capabilities,
        )
        setup_hash = _content_hash(validated.values)
        source_head_before = context.source_head_id
        run: AgentRun | None = None
        try:
            session = self.agent_service.create_session(
                app_id=context.build.app_id,
                app_mode=context.build.app_mode,
                app_name=context.build.app_name,
                allow_config_create=True,
            )
            run = self.agent_store.create_run(
                AgentRun(
                    session_id=session.id,
                    goal=(
                        f"Apply Blueprint {registered.definition.name} "
                        f"{registered.definition.version} as one typed Patch."
                    ),
                    constraints=RunConstraints(
                        workspace_only=True,
                        allow_draft_test=False,
                        read_only=False,
                    ),
                )
            )
            run = self.agent_store.update_run(run.transition_to(RunPhase.OBSERVING))
            self.agent_store.append_event(
                run_id=run.id,
                event_type="context.loaded",
                phase=run.phase.value,
                message="Loaded the authoritative Build base and pinned resource catalog.",
                data={
                    "blueprint_id": blueprint_id,
                    "blueprint_version": registered.definition.version,
                    "untrusted_metadata": True,
                    "permission_expansion": False,
                },
            )
            snapshot = AgentWorkflowSnapshot.model_validate(
                context.snapshot.model_dump(mode="json")
            )
            goal_plan = _blueprint_goal_plan(registered.definition)
            run, root = self.workspace.initialize(run, snapshot, goal_plan)
            run = self.agent_store.update_run(run.transition_to(RunPhase.PLANNING))
            run = self.agent_store.update_run(run.transition_to(RunPhase.ACTING))
            patch = _expand_blueprint(
                registered,
                plan=WorkflowPlan.model_validate(root.snapshot),
                workspace_version=root.id,
                expected_base_hash=run.base_hash,
                values=validated.values,
            )
            policy_evidence = _evaluate_blueprint_patch_policy(
                registered.definition,
                patch,
                catalog=self.catalog,
                app_mode=context.build.app_mode,
            )
            result = self.workspace.apply_patch(run.id, patch)
            self.agent_store.append_event(
                run_id=run.id,
                event_type="tool.completed",
                phase=run.phase.value,
                message="Expanded the Blueprint through one normal transactional Patch.",
                data={
                    "operation_count": len(patch.operations),
                    "workspace_version_id": result.workspace_version,
                    "setup_hash": setup_hash,
                    "policy_code": policy_evidence["code"],
                    "policy": policy_evidence,
                },
            )
            run = self.agent_store.get_run(run.id)
            run = self.agent_store.update_run(run.transition_to(RunPhase.VALIDATING))
            review = self.review.build(run.id)
            if not review.ready:
                raise BlueprintError("The expanded Blueprint did not pass deterministic validation.")
            run = self.agent_store.get_run(run.id)
            run = AgentRun.model_validate(
                {
                    **run.transition_to(RunPhase.WAITING_APPROVAL).model_dump(),
                    "goal_plan": _completed_blueprint_goal_plan(goal_plan).model_dump(mode="json"),
                    "review": review.model_dump(mode="json"),
                }
            )
            run = self.agent_store.update_run(run)
            self.agent_store.append_event(
                run_id=run.id,
                event_type="review.ready",
                phase=run.phase.value,
                message="Blueprint Candidate passed validation and is ready for comparison.",
                data={
                    "workspace_version_id": result.workspace_version,
                    "dify_write_count": 0,
                    "approval_created": False,
                },
            )
            candidate = self.store.add_candidate(
                build_id=build_id,
                project_id=project_id,
                principal_key=authenticated.principal.key,
                run_id=run.id,
                label=registered.definition.name,
                intent=registered.definition.business_outcome,
                source_candidate_ids=(
                    [context.source_candidate.id]
                    if context.source_candidate is not None
                    else []
                ),
            )
            application = self.store.record_blueprint_application(
                project_id=project_id,
                principal_key=authenticated.principal.key,
                build_id=build_id,
                candidate_id=candidate.id,
                blueprint_id=registered.definition.id,
                blueprint_version=registered.definition.version,
                setup_hash=setup_hash,
            )
            source_unchanged = self._source_head_unchanged(context, source_head_before)
            view = self.build_service.get(
                authenticated,
                project_id=project_id,
                build_id=build_id,
            )
            applied = next(
                (
                    item
                    for item in view.candidates
                    if item.candidate.id == candidate.id
                ),
                None,
            )
            if applied is None or applied.candidate.status != "valid" or not applied.reconstructable:
                raise BlueprintError("The Blueprint Candidate is not reconstructable and valid.")
            return BlueprintApplyResult(
                application=application,
                build=view,
                patch_operation_count=len(patch.operations),
                workspace_version_id=result.workspace_version,
                source_head_unchanged=source_unchanged,
                dify_write_count=0,
            )
        except Exception as exc:
            if run is not None:
                self._fail_blueprint_run(run.id, exc)
            if isinstance(exc, (BlueprintError, StudioAccessDenied, StudioRecordNotFound)):
                raise
            if isinstance(exc, WorkspaceOperationError):
                error = BlueprintError(str(exc))
                error.code = exc.code
                raise error from exc
            raise

    def extract(
        self,
        authenticated: AuthenticatedStudioRequest,
        *,
        project_id: str,
        build_id: str,
        candidate_id: str,
        selected_node_ids: list[str],
        name: str,
        business_outcome: str,
        category: str,
        visibility: Literal["private", "team"],
        typed_interface: BlueprintTypedInterface,
    ) -> BlueprintVersionRecord:
        _, membership = self.store.get_project_for_principal(
            project_id,
            authenticated.principal.key,
        )
        if membership.role not in {"owner", "admin", "builder"}:
            raise StudioAccessDenied("Your project role cannot extract Blueprints.")
        candidate = self.store.get_candidate(
            candidate_id,
            build_id=build_id,
            project_id=project_id,
            principal_key=authenticated.principal.key,
        )
        run = self.agent_store.get_run(candidate.run_id)
        if run.snapshot is None or not isinstance(run.snapshot, AgentWorkflowSnapshot):
            raise BlueprintError("Only graph Candidates can be extracted as Blueprints.")
        head = self.agent_store.get_workspace_head(run.id)
        plan = WorkflowPlan.model_validate(head.snapshot)
        selected_ids = _unique_nonempty(selected_node_ids)
        if not selected_ids:
            raise BlueprintError("Select at least one authoritative node to extract.")
        node_by_id = {node.id: node for node in plan.nodes}
        try:
            selected = [node_by_id[node_id] for node_id in selected_ids]
        except KeyError as exc:
            raise BlueprintError("A selected node is not in the authoritative Candidate Workspace.") from exc
        if any(node.type in {"start", "datasource", "trigger-webhook", "trigger-plugin", "trigger-schedule", "end", "answer"} for node in selected):
            raise BlueprintError("Extract only reusable middle-path nodes, not entry or terminal nodes.")
        cleaned_name = _clean_metadata(name, field="name", limit=256)
        cleaned_outcome = _clean_metadata(
            business_outcome,
            field="business_outcome",
            limit=4_000,
        )
        cleaned_category = _clean_metadata(category, field="category", limit=128)
        blueprint_id = new_id()
        slug = _slugify(cleaned_name, blueprint_id)
        template, preview, setup_fields = _extract_template(
            selected,
            plan.edges,
            typed_interface,
        )
        definition = BlueprintDefinition(
            id=blueprint_id,
            slug=slug,
            name=cleaned_name,
            business_outcome=cleaned_outcome,
            description="从已验证 Candidate 的选中路径提取；应用时仍走普通 Typed Patch。",
            category=cleaned_category,
            use_cases=[cleaned_category],
            preview=preview,
            supported_app_modes={plan.app_mode},
            dify_version_range=str(
                run.snapshot.compatibility.get("dify_version") or "1.14.x"
            ),
            dsl_versions={str(run.snapshot.dify_version.get("app_dsl_version") or "0.6.0")},
            setup_schema=setup_fields,
            capabilities=sorted({node.type for node in selected}),
            resources=[
                BlueprintResourceRequirement(
                    kind=field.kind,
                    setup_field_id=field.id,
                    reason=f"Map {field.label} before applying the extracted pattern.",
                )
                for field in setup_fields
                if field.kind in _RESOURCE_KINDS
            ],
            estimated_cost=(
                "variable"
                if any(node.type in {"llm", "tool", "agent", "http-request"} for node in selected)
                else "low"
            ),
            risk=(
                "high"
                if any(node.type in {"tool", "agent", "http-request", "human-input"} for node in selected)
                else "medium"
            ),
            risk_reasons=["Extracted metadata and setup values remain untrusted data."],
            validators=["typed-interface", "secret-scan", "normal-patch-validation"],
            scenarios=[
                BlueprintScenario(
                    name="提取模式连通性",
                    input_summary="提供符合 typed interface 的最小输入。",
                    expected="选中路径保持连通并产生声明的输出。",
                )
            ],
            provenance=BlueprintProvenance(
                source="extracted",
                author=authenticated.principal.key,
                extracted_candidate_id=None,
                untrusted_metadata=True,
            ),
            version="1.0.0",
            visibility=visibility,
            project_id=project_id,
            upgrade_notes=["首个提取版本；未携带环境资源 ID 或 Secret。"],
            published_at=utc_now() if visibility == "private" else None,
        )
        _assert_secret_free(
            {
                "definition": definition.model_dump(mode="json"),
                "template": template,
            }
        )
        serialized = json.dumps(
            {"definition": definition.model_dump(mode="json"), "template": template},
            ensure_ascii=False,
            sort_keys=True,
        )
        for original_id in selected_ids:
            if original_id in serialized:
                raise BlueprintError("Extracted Blueprint retained an environment node ID.")
        return self.store.create_blueprint(
            definition=definition,
            template=template,
            principal_key=authenticated.principal.key,
            initial_status=("published" if visibility == "private" else "pending_review"),
        )

    def propose_version(
        self,
        authenticated: AuthenticatedStudioRequest,
        *,
        project_id: str,
        blueprint_id: str,
        version: str,
        upgrade_notes: list[str],
    ) -> BlueprintVersionRecord:
        current, template = self.store.get_blueprint_version(
            blueprint_id,
            None,
            project_id=project_id,
            principal_key=authenticated.principal.key,
        )
        if _semver_key(version) <= _semver_key(current.version):
            raise BlueprintError("A proposed Blueprint version must be newer than the published version.")
        notes = [
            _clean_metadata(value, field="upgrade_note", limit=2_000)
            for value in upgrade_notes
            if str(value).strip()
        ]
        if not notes:
            raise BlueprintError("A Blueprint version proposal requires upgrade notes.")
        definition = BlueprintDefinition.model_validate(
            {
                **current.definition.model_dump(mode="json"),
                "version": version,
                "upgrade_notes": notes,
                "published_at": None,
            }
        )
        return self.store.propose_blueprint_version(
            definition=definition,
            template=template,
            principal_key=authenticated.principal.key,
        )

    def review_version(
        self,
        authenticated: AuthenticatedStudioRequest,
        *,
        project_id: str,
        blueprint_id: str,
        version: str,
        approved: bool,
        note: str,
    ) -> BlueprintVersionRecord:
        return self.store.review_blueprint_version(
            blueprint_id=blueprint_id,
            semantic_version=version,
            project_id=project_id,
            principal_key=authenticated.principal.key,
            approved=approved,
            note=note,
        )

    def upgrade_preview(
        self,
        authenticated: AuthenticatedStudioRequest,
        *,
        project_id: str,
        application_id: str,
        target_version: str | None = None,
    ) -> BlueprintUpgradePreview:
        application = self.store.get_blueprint_application(
            application_id,
            project_id=project_id,
            principal_key=authenticated.principal.key,
        )
        source = self._get_blueprint(
            application.blueprint_id,
            version=application.blueprint_version,
            project_id=project_id,
            principal_key=authenticated.principal.key,
        ).definition
        target = self._get_blueprint(
            application.blueprint_id,
            version=target_version,
            project_id=project_id,
            principal_key=authenticated.principal.key,
        ).definition
        return BlueprintUpgradePreview(
            application=application,
            source=source,
            target=target,
            changes=_blueprint_upgrade_diff(source, target),
            automatic=False,
            action_required="apply_as_new_candidate",
        )

    def _visible_blueprints(
        self,
        *,
        project_id: str,
        principal_key: str,
    ) -> list[_RegisteredBlueprint]:
        builtins = self.registry.list_current()
        custom = [
            _RegisteredBlueprint(record.definition, template, record)
            for record, template in self.store.list_published_blueprints(
                project_id=project_id,
                principal_key=principal_key,
            )
        ]
        pending = [
            _RegisteredBlueprint(record.definition, template, record)
            for record, template in self.store.list_pending_blueprints(
                project_id=project_id,
                principal_key=principal_key,
            )
        ]
        preferred = {item.definition.id: item for item in custom}
        preferred.update({item.definition.id: item for item in pending})
        return [*builtins, *preferred.values()]

    def _get_blueprint(
        self,
        blueprint_id: str,
        *,
        version: str | None,
        project_id: str,
        principal_key: str,
        include_unpublished: bool = False,
    ) -> _RegisteredBlueprint:
        try:
            return self.registry.get(blueprint_id, version)
        except StudioRecordNotFound:
            record, template = self.store.get_blueprint_version(
                blueprint_id,
                version,
                project_id=project_id,
                principal_key=principal_key,
                include_unpublished=include_unpublished,
            )
            return _RegisteredBlueprint(record.definition, template, record)

    @staticmethod
    def _gallery_item(
        *,
        registered: _RegisteredBlueprint,
        availability: BlueprintAvailability,
        score: int,
        membership_role: str,
        principal_key: str,
    ) -> BlueprintGalleryItem:
        record = registered.record
        status = record.status if record is not None else "published"
        if status != "published":
            availability = availability.model_copy(
                update={
                    "applicable": False,
                    "reasons": [
                        *availability.reasons,
                        {
                            "code": "BLUEPRINT_VERSION_PENDING_REVIEW",
                            "message": "该 Team 版本通过独立评审前不能应用。",
                        },
                    ],
                }
            )
        can_review = bool(
            record is not None
            and status == "pending_review"
            and membership_role in {"owner", "admin", "reviewer"}
            and record.created_by != principal_key
        )
        can_propose = bool(
            record is not None
            and status == "published"
            and (
                record.created_by == principal_key
                or membership_role in {"owner", "admin"}
            )
        )
        return BlueprintGalleryItem(
            blueprint=registered.definition,
            availability=availability,
            score=score,
            version_status=status,
            version_created_by=record.created_by if record is not None else None,
            can_review=can_review,
            can_propose=can_propose,
        )

    def _context(
        self,
        authenticated: AuthenticatedStudioRequest,
        *,
        project_id: str,
        build_id: str | None,
        required: bool,
    ) -> _BlueprintContext | None:
        if not build_id:
            if required:
                raise BlueprintUnavailable("Open the Gallery from a Build to pin compatibility and resources.")
            return None
        build = self.store.get_build(
            build_id,
            project_id=project_id,
            principal_key=authenticated.principal.key,
        )
        if build.app_mode not in {"workflow", "advanced-chat"}:
            if required:
                raise BlueprintUnavailable("The initial Blueprint Gallery supports Workflow and Chatflow graph apps.")
            return None
        candidates = self.store.list_candidates(
            build.id,
            project_id=project_id,
            principal_key=authenticated.principal.key,
        )
        source = next(
            (item for item in candidates if item.id == build.selected_candidate_id),
            None,
        )
        if source is None:
            source = next(
                (
                    item
                    for item in candidates
                    if self.agent_store.get_run(item.run_id).snapshot is not None
                ),
                None,
            )
        source_head_id: str | None = None
        snapshot: AgentWorkflowSnapshot
        if source is not None:
            run = self.agent_store.get_run(source.run_id)
            if not isinstance(run.snapshot, AgentWorkflowSnapshot):
                raise BlueprintUnavailable("Blueprint application requires a graph Candidate base.")
            snapshot = AgentWorkflowSnapshot.model_validate(
                run.snapshot.model_dump(mode="json")
            )
            source_head_id = run.head_version_id
        else:
            if build.entry_source == "canvas":
                if required:
                    raise BlueprintUnavailable(
                        "Canvas-opened Gallery requires an initialized Candidate with verified dirty-state and Hash context."
                    )
                return None
            session = AgentSession(
                operation=build.operation,
                app_id=build.app_id,
                app_mode=build.app_mode,
                app_name=build.app_name,
            )
            snapshot = self.snapshot_service.capture(session)
        plan = WorkflowPlan.model_validate(snapshot.base_plan)
        fingerprint = _snapshot_fingerprint(snapshot)
        if build.base_fingerprint and build.base_fingerprint != fingerprint:
            raise BlueprintUnavailable("The Gallery base no longer matches the pinned Build base.")
        return _BlueprintContext(
            build=build,
            snapshot=snapshot,
            plan=plan,
            source_candidate=source,
            source_head_id=source_head_id,
        )

    def _availability(
        self,
        blueprint: BlueprintDefinition,
        *,
        context: _BlueprintContext | None,
        requested_app_mode: str | None = None,
        requested_dify_version: str | None = None,
    ) -> BlueprintAvailability:
        reasons: list[dict[str, str]] = []
        resources: dict[str, list[dict[str, str]]] = {}
        app_mode = context.build.app_mode if context is not None else requested_app_mode
        dify_version = (
            str(context.snapshot.compatibility.get("dify_version") or "")
            if context is not None
            else (requested_dify_version or "")
        )
        dsl_version = (
            str(context.snapshot.dify_version.get("app_dsl_version") or "")
            if context is not None
            else ""
        )
        if app_mode and app_mode not in blueprint.supported_app_modes:
            reasons.append(
                {
                    "code": "BLUEPRINT_APP_MODE_UNSUPPORTED",
                    "message": f"当前 {app_mode} 不在该 Blueprint 支持范围内。",
                }
            )
        if dify_version and not _version_matches(dify_version, blueprint.dify_version_range):
            reasons.append(
                {
                    "code": "BLUEPRINT_DIFY_VERSION_UNSUPPORTED",
                    "message": f"当前 Dify {dify_version} 不满足 {blueprint.dify_version_range}。",
                }
            )
        if dsl_version and dsl_version not in blueprint.dsl_versions:
            reasons.append(
                {
                    "code": "BLUEPRINT_DSL_VERSION_UNSUPPORTED",
                    "message": f"当前 DSL {dsl_version} 未通过该 Blueprint 验证。",
                }
            )
        if context is None:
            reasons.append(
                {
                    "code": "BLUEPRINT_BUILD_CONTEXT_REQUIRED",
                    "message": "从 Build Studio 打开后可核对资源并应用。",
                }
            )
            return BlueprintAvailability(
                compatible=not any(reason["code"].endswith("UNSUPPORTED") for reason in reasons),
                applicable=False,
                reasons=reasons,
                available_resources=resources,
            )
        if not bool(context.snapshot.compatibility.get("mutation_supported", True)):
            reasons.append(
                {
                    "code": "DIFY_VERSION_MUTATION_UNSUPPORTED",
                    "message": str(
                        context.snapshot.compatibility.get("reason")
                        or "当前 Dify/DSL 组合只允许诊断。"
                    ),
                }
            )
        capability_types = {
            str(item.get("type") or "")
            for item in context.snapshot.capabilities
            if isinstance(item, dict)
        }
        for capability in blueprint.capabilities:
            definition = self.catalog.get(capability)
            if (
                definition is None
                or context.build.app_mode not in definition.supported_app_modes
                or "node.add" not in definition.mutation_operations
            ):
                reasons.append(
                    {
                        "code": "BLUEPRINT_CAPABILITY_UNAVAILABLE",
                        "message": f"缺少可变更能力：{capability}。",
                    }
                )
            elif capability not in capability_types:
                reasons.append(
                    {
                        "code": "BLUEPRINT_CAPABILITY_NOT_PINNED",
                        "message": f"当前 Snapshot 未固定能力：{capability}。",
                    }
                )
        for field in blueprint.setup_schema:
            options = _resource_options(field, context.snapshot.capabilities)
            resources[field.id] = options
            if field.required and field.kind in _RESOURCE_KINDS and not options:
                reasons.append(
                    {
                        "code": "BLUEPRINT_RESOURCE_MISSING",
                        "message": f"缺少 {field.label} 的兼容资源。",
                    }
                )
        incompatible_codes = {
            "BLUEPRINT_APP_MODE_UNSUPPORTED",
            "BLUEPRINT_DIFY_VERSION_UNSUPPORTED",
            "BLUEPRINT_DSL_VERSION_UNSUPPORTED",
            "DIFY_VERSION_MUTATION_UNSUPPORTED",
            "BLUEPRINT_CAPABILITY_UNAVAILABLE",
            "BLUEPRINT_CAPABILITY_NOT_PINNED",
        }
        compatible = not any(reason["code"] in incompatible_codes for reason in reasons)
        applicable = compatible and not reasons and not blueprint.deprecated
        if blueprint.deprecated:
            reasons.append(
                {
                    "code": "BLUEPRINT_DEPRECATED",
                    "message": blueprint.deprecation_message or "该 Blueprint 已弃用。",
                }
            )
        return BlueprintAvailability(
            compatible=compatible,
            applicable=applicable,
            reasons=reasons,
            available_resources=resources,
        )

    def _validate_values(
        self,
        blueprint: BlueprintDefinition,
        submitted: list[BlueprintSetupValue],
        capabilities: list[dict[str, Any]],
    ) -> _ValidatedSetup:
        values_by_id: dict[str, BlueprintSetupValue] = {}
        for item in submitted:
            if item.field_id in values_by_id:
                raise BlueprintSetupError(f"Setup field {item.field_id} was submitted more than once.")
            values_by_id[item.field_id] = item
        schema = {field.id: field for field in blueprint.setup_schema}
        unknown = sorted(set(values_by_id) - set(schema))
        if unknown:
            raise BlueprintSetupError(f"Unknown Blueprint setup fields: {', '.join(unknown)}.")
        normalized: dict[str, Any] = {}
        results: list[dict[str, Any]] = []
        for field in blueprint.setup_schema:
            submitted_value = values_by_id.get(field.id)
            if submitted_value is not None and submitted_value.kind != field.kind:
                raise BlueprintSetupError(f"Setup field {field.id} changed its declared type.")
            value: Any = (
                submitted_value.value
                if submitted_value is not None
                else deepcopy(field.default)
            )
            if field.required and (value is None or value == "" or value == []):
                results.append(
                    {
                        "field_id": field.id,
                        "ok": False,
                        "code": "BLUEPRINT_SETUP_REQUIRED",
                        "message": f"请配置 {field.label}。",
                    }
                )
                continue
            if value is None:
                continue
            _assert_secret_free({field.id: value})
            if field.multiple:
                if not isinstance(value, list) or not all(isinstance(item, str) for item in value):
                    raise BlueprintSetupError(f"Setup field {field.id} requires a string list.")
                clean_value: Any = [str(item).strip() for item in value if str(item).strip()]
            elif isinstance(value, str):
                clean_value = value.strip()
            else:
                clean_value = value
            if field.kind == "variable" and isinstance(clean_value, str) and not _SAFE_VARIABLE.fullmatch(clean_value):
                raise BlueprintSetupError(f"Variable field {field.id} must use a safe identifier.")
            if field.kind in _RESOURCE_KINDS:
                options = _resource_options(field, capabilities)
                allowed = {item["id"] for item in options}
                selected = clean_value if isinstance(clean_value, list) else [clean_value]
                missing = [str(item) for item in selected if str(item) not in allowed]
                if missing:
                    results.append(
                        {
                            "field_id": field.id,
                            "ok": False,
                            "code": "BLUEPRINT_RESOURCE_UNAVAILABLE",
                            "message": f"资源不可用或不在固定 Snapshot 中：{', '.join(missing)}。",
                        }
                    )
                    continue
            if field.options:
                allowed = {str(item.get("id") or "") for item in field.options}
                selected = clean_value if isinstance(clean_value, list) else [clean_value]
                if any(str(item) not in allowed for item in selected):
                    results.append(
                        {
                            "field_id": field.id,
                            "ok": False,
                            "code": "BLUEPRINT_SETUP_OPTION_INVALID",
                            "message": f"{field.label} 使用了未声明的选项。",
                        }
                    )
                    continue
            normalized[field.id] = redact_sensitive_data(clean_value)
            results.append(
                {
                    "field_id": field.id,
                    "ok": True,
                    "code": "BLUEPRINT_SETUP_VALID",
                    "message": f"{field.label} 已通过类型与可用性检查。",
                }
            )
        failures = [item for item in results if not item.get("ok")]
        if failures:
            raise BlueprintSetupError("；".join(str(item["message"]) for item in failures))
        return _ValidatedSetup(values=normalized, field_results=results)

    def _source_head_unchanged(
        self,
        context: _BlueprintContext,
        expected: str | None,
    ) -> bool:
        if context.source_candidate is None:
            return True
        current = self.agent_store.get_run(context.source_candidate.run_id)
        return current.head_version_id == expected

    def _fail_blueprint_run(self, run_id: str, exc: Exception) -> None:
        try:
            run = self.agent_store.get_run(run_id)
            if run.terminal:
                return
            failed = run.transition_to(
                RunPhase.FAILED,
                error={
                    "code": str(getattr(exc, "code", "BLUEPRINT_APPLICATION_FAILED")),
                    "message": "Blueprint application failed without changing its source Candidate.",
                    "retryable": False,
                },
            )
            self.agent_store.update_run(failed)
            self.agent_store.append_event(
                run_id=run_id,
                event_type="agent.failed",
                phase=failed.phase.value,
                message="Blueprint application stopped; no Dify write occurred.",
                data=failed.error or {},
            )
        except Exception:
            return


def _expand_blueprint(
    registered: _RegisteredBlueprint,
    *,
    plan: WorkflowPlan,
    workspace_version: str,
    expected_base_hash: str | None,
    values: dict[str, Any],
) -> PatchDocument:
    template = registered.template
    kind = str(template.get("kind") or "")
    if kind == "builtin":
        operations = _expand_builtin(str(template.get("pattern") or ""), plan, values)
    elif kind == "extracted":
        operations = _expand_extracted(template, plan, values)
    else:
        raise BlueprintError("Blueprint template kind is not allowlisted.")
    return PatchDocument.model_validate(
        {
            "workspace_version": workspace_version,
            "expected_base_hash": expected_base_hash,
            "operations": operations,
            "rationale": (
                f"Apply Blueprint {registered.definition.name} "
                f"{registered.definition.version} as one transaction."
            ),
        }
    )


def _evaluate_blueprint_patch_policy(
    blueprint: BlueprintDefinition,
    patch: PatchDocument,
    *,
    catalog: NodeCapabilityCatalog,
    app_mode: str,
) -> dict[str, Any]:
    allowed_operations = {
        "node.add",
        "node.update",
        "node.remove",
        "entry.replace",
        "edge.add",
        "edge.remove",
        "conversation_variable.add",
        "conversation_variable.update",
        "conversation_variable.remove",
    }
    operation_names = [operation.op for operation in patch.operations]
    unknown = sorted(set(operation_names) - allowed_operations)
    if unknown:
        raise BlueprintPolicyDenied(
            f"Blueprint Patch contains policy-forbidden operations: {', '.join(unknown)}."
        )
    declared = set(blueprint.capabilities)
    added_types: list[str] = []
    for operation in patch.operations:
        node_type = getattr(operation, "node_type", None)
        if not isinstance(node_type, str):
            continue
        added_types.append(node_type)
        definition = catalog.get(node_type)
        if definition is None or app_mode not in definition.supported_app_modes:
            raise BlueprintPolicyDenied(
                f"Blueprint requested unavailable capability {node_type}."
            )
        if node_type not in declared:
            raise BlueprintPolicyDenied(
                f"Blueprint template requested undeclared capability {node_type}."
            )
        required_mutation = "entry.replace" if operation.op == "entry.replace" else "node.add"
        if required_mutation not in definition.mutation_operations:
            raise BlueprintPolicyDenied(
                f"Blueprint capability {node_type} does not allow {required_mutation}."
            )
    if "tool" in added_types and not any(
        requirement.kind == "tool" and requirement.setup_field_id
        for requirement in blueprint.resources
    ):
        raise BlueprintPolicyDenied(
            "Tool nodes require an explicit typed Tool resource mapping."
        )
    return {
        "allowed": True,
        "code": "BLUEPRINT_WORKSPACE_PATCH_ALLOWED",
        "workspace_only": True,
        "permission_expansion": False,
        "tool_visibility_expansion": False,
        "approval_created": False,
        "dify_write": False,
        "operations": operation_names,
        "declared_capabilities": sorted(declared),
    }


def _expand_builtin(pattern: str, plan: WorkflowPlan, values: dict[str, Any]) -> list[dict[str, Any]]:
    entry = _entry_node(plan)
    insertion = _insertion_edge(plan)
    query_selector = _query_selector(plan, entry)
    if pattern == "knowledge-human":
        dataset_ids = _as_string_list(values["dataset"])
        channel = str(values["review_channel"])
        prompt = str(values.get("grounding_prompt") or "仅依据检索资料回答；信息不足时转人工复核。")
        return _linear_operations(
            insertion,
            [
                {
                    "temp_ref": "tmp_blueprint_knowledge",
                    "node_type": "knowledge-retrieval",
                    "title": "检索已映射知识库",
                    "params": {
                        "dataset_ids": dataset_ids,
                        "query_variable_selector": query_selector,
                        "retrieval_mode": "multiple",
                        "multiple_retrieval_config": {"top_k": 4},
                    },
                },
                {
                    "temp_ref": "tmp_blueprint_review",
                    "node_type": "human-input",
                    "title": "低置信度人工复核",
                    "params": _human_input_params(channel, prompt),
                    "out_handle": "continue",
                },
            ],
        )
    if pattern == "human-fallback":
        return _linear_operations(
            insertion,
            [
                {
                    "temp_ref": "tmp_blueprint_human",
                    "node_type": "human-input",
                    "title": "人工接管",
                    "params": _human_input_params(
                        str(values["review_channel"]),
                        str(values.get("handoff_prompt") or "请复核当前处理建议。"),
                    ),
                    "out_handle": "continue",
                }
            ],
        )
    if pattern == "json-extraction":
        output_name = str(values.get("output_variable") or "result")
        return _linear_operations(
            insertion,
            [
                {
                    "temp_ref": "tmp_blueprint_extract",
                    "node_type": "parameter-extractor",
                    "title": "提取结构化字段",
                    "params": {
                        "query": query_selector,
                        "instruction": str(values["extraction_prompt"]),
                        "parameters": [
                            {
                                "name": output_name,
                                "type": "string",
                                "description": "按引导式 Setup 声明的业务字段",
                                "required": True,
                            }
                        ],
                    },
                }
            ],
        )
    if pattern == "document-intake":
        if entry.type != "start":
            raise BlueprintUnavailable("Document Intake requires a Start entry with a typed file input.")
        input_name = str(values.get("file_variable") or "document")
        params = deepcopy(entry.params)
        variables = list(params.get("variables") or [])
        if not any(str(item.get("name") or "") == input_name for item in variables if isinstance(item, dict)):
            variables.append(
                {
                    "name": input_name,
                    "type": "file",
                    "required": True,
                    "label": "待处理文档",
                }
            )
        params["variables"] = variables
        operations = [
            {
                "op": "node.update",
                "node_id": entry.id,
                "set": {"params": params},
                "expected": {"type": "start", "params": entry.params},
            }
        ]
        operations.extend(
            _linear_operations(
                insertion,
                [
                    {
                        "temp_ref": "tmp_blueprint_document",
                        "node_type": "document-extractor",
                        "title": "提取文档文本",
                        "params": {"variable_selector": [entry.id, input_name]},
                    }
                ],
            )
        )
        return operations
    if pattern == "webhook-ingestion":
        if entry.type != "start":
            raise BlueprintUnavailable("Webhook Ingestion can only replace an explicit Start entry.")
        return [
            {
                "op": "entry.replace",
                "node_id": entry.id,
                "expected_type": "start",
                "temp_ref": "tmp_blueprint_webhook",
                "node_type": "trigger-webhook",
                "title": "Webhook 接收业务事件",
                "params": {
                    "method": str(values.get("http_method") or "POST"),
                    "content_type": "application/json",
                    "headers": [],
                    "params": [],
                    "body": [
                        {
                            "name": str(values.get("payload_variable") or "query"),
                            "type": "string",
                            "required": True,
                        }
                    ],
                    "status_code": 202,
                    "response_body": "{\"accepted\":true}",
                    "timeout": 20,
                },
            }
        ]
    if pattern == "scheduled-report":
        if entry.type != "start":
            raise BlueprintUnavailable("Scheduled Report can only replace an explicit Start entry.")
        if insertion.source != entry.id or len(plan.nodes) != 2:
            raise BlueprintUnavailable(
                "Scheduled Report currently requires the minimal Start-to-End base so existing input references remain truthful."
            )
        target = next(node for node in plan.nodes if node.id == insertion.target)
        target_params = deepcopy(target.params)
        target_params["outputs"] = [
            {
                "variable": "scheduled_at",
                "value_selector": ["sys", "timestamp"],
            }
        ]
        return [
            {
                "op": "node.update",
                "node_id": insertion.target,
                "set": {"params": target_params},
                "expected": {"type": "end", "params": target.params},
            },
            {
                "op": "entry.replace",
                "node_id": entry.id,
                "expected_type": "start",
                "temp_ref": "tmp_blueprint_schedule",
                "node_type": "trigger-schedule",
                "title": "定时生成业务报告",
                "params": {
                    "mode": "visual",
                    "frequency": str(values.get("frequency") or "daily"),
                    "visual_config": {
                        "time": str(values.get("report_time") or "09:00 AM"),
                        "weekdays": ["mon"],
                        "on_minute": 0,
                        "monthly_days": [1],
                    },
                    "timezone": str(values.get("timezone") or "Asia/Shanghai"),
                },
            },
        ]
    if pattern == "error-retry":
        case_id = "retryable"
        branch = {
            "temp_ref": "tmp_blueprint_retry_guard",
            "node_type": "if-else",
            "title": "错误与有限重试判断",
            "params": {
                "cases": [
                    {
                        "case_id": case_id,
                        "conditions": [
                            {
                                "variable_selector": query_selector,
                                "comparison_operator": "contains",
                                "value": str(values.get("retry_marker") or "retry"),
                                "varType": "string",
                            }
                        ],
                    }
                ]
            },
        }
        return _branch_operations(insertion, branch, [case_id, "false"])
    if pattern == "model-routing":
        model_ref = str(values["model"])
        provider, model = _split_resource_ref(model_ref)
        return _linear_operations(
            insertion,
            [
                {
                    "temp_ref": "tmp_blueprint_model",
                    "node_type": "llm",
                    "title": "按策略选择模型",
                    "params": {
                        "model": {
                            "provider": provider,
                            "name": model,
                            "mode": "chat",
                            "completion_params": {},
                        },
                        "user_prompt": str(values.get("routing_prompt") or "处理：{{#sys.query#}}"),
                    },
                }
            ],
        )
    if pattern == "support-classification":
        classes = ["question", "complaint", "urgent"]
        branch = {
            "temp_ref": "tmp_blueprint_classifier",
            "node_type": "question-classifier",
            "title": "客户支持分类",
            "params": {
                "query_variable_selector": query_selector,
                "instruction": str(values["classification_prompt"]),
                "classes": [
                    {"id": item, "name": label}
                    for item, label in zip(classes, ["咨询", "投诉", "紧急"], strict=True)
                ],
            },
        }
        return _branch_operations(insertion, branch, classes)
    raise BlueprintError("Builtin Blueprint template pattern is not allowlisted.")


def _linear_operations(edge: PlanEdge, nodes: list[dict[str, Any]]) -> list[dict[str, Any]]:
    operations: list[dict[str, Any]] = [
        {
            "op": "edge.remove",
            "source": edge.source,
            "source_handle": edge.source_handle,
            "target": edge.target,
            "target_handle": edge.target_handle,
        }
    ]
    for item in nodes:
        operations.append(
            {
                "op": "node.add",
                "temp_ref": item["temp_ref"],
                "node_type": item["node_type"],
                "title": item["title"],
                "params": item["params"],
                "after_node_id": edge.source,
            }
        )
    previous = edge.source
    previous_handle = edge.source_handle
    for item in nodes:
        operations.append(
            {
                "op": "edge.add",
                "source": previous,
                "source_handle": previous_handle,
                "target": item["temp_ref"],
                "target_handle": "target",
            }
        )
        previous = item["temp_ref"]
        previous_handle = str(item.get("out_handle") or "source")
    operations.append(
        {
            "op": "edge.add",
            "source": previous,
            "source_handle": previous_handle,
            "target": edge.target,
            "target_handle": edge.target_handle,
        }
    )
    return operations


def _branch_operations(edge: PlanEdge, node: dict[str, Any], handles: list[str]) -> list[dict[str, Any]]:
    operations = [
        {
            "op": "edge.remove",
            "source": edge.source,
            "source_handle": edge.source_handle,
            "target": edge.target,
            "target_handle": edge.target_handle,
        },
        {
            "op": "node.add",
            "temp_ref": node["temp_ref"],
            "node_type": node["node_type"],
            "title": node["title"],
            "params": node["params"],
            "after_node_id": edge.source,
        },
        {
            "op": "edge.add",
            "source": edge.source,
            "source_handle": edge.source_handle,
            "target": node["temp_ref"],
            "target_handle": "target",
        },
    ]
    operations.extend(
        {
            "op": "edge.add",
            "source": node["temp_ref"],
            "source_handle": handle,
            "target": edge.target,
            "target_handle": edge.target_handle,
        }
        for handle in handles
    )
    return operations


def _expand_extracted(template: dict[str, Any], plan: WorkflowPlan, values: dict[str, Any]) -> list[dict[str, Any]]:
    edge = _insertion_edge(plan)
    nodes = template.get("nodes")
    edges = template.get("edges")
    if not isinstance(nodes, list) or not nodes:
        raise BlueprintError("Extracted Blueprint template has no typed nodes.")
    if not isinstance(edges, list):
        raise BlueprintError("Extracted Blueprint template edges are invalid.")
    known_refs = {str(node.get("temp_ref") or "") for node in nodes if isinstance(node, dict)}
    if "" in known_refs or len(known_refs) != len(nodes):
        raise BlueprintError("Extracted Blueprint node references are invalid.")
    operations: list[dict[str, Any]] = [
        {
            "op": "edge.remove",
            "source": edge.source,
            "source_handle": edge.source_handle,
            "target": edge.target,
            "target_handle": edge.target_handle,
        }
    ]
    incoming = {ref: 0 for ref in known_refs}
    outgoing = {ref: 0 for ref in known_refs}
    for node in nodes:
        if not isinstance(node, dict):
            raise BlueprintError("Extracted Blueprint node is invalid.")
        operations.append(
            {
                "op": "node.add",
                "temp_ref": str(node["temp_ref"]),
                "node_type": str(node["node_type"]),
                "title": str(node["title"]),
                "params": _resolve_extracted_value(node.get("params", {}), values, plan),
                "after_node_id": edge.source,
            }
        )
    for item in edges:
        if not isinstance(item, dict):
            raise BlueprintError("Extracted Blueprint edge is invalid.")
        source = str(item.get("source") or "")
        target = str(item.get("target") or "")
        if source not in known_refs or target not in known_refs:
            raise BlueprintError("Extracted Blueprint edge escaped its selected path.")
        outgoing[source] += 1
        incoming[target] += 1
        operations.append(
            {
                "op": "edge.add",
                "source": source,
                "source_handle": str(item.get("source_handle") or "source"),
                "target": target,
                "target_handle": str(item.get("target_handle") or "target"),
            }
        )
    roots = sorted(ref for ref, count in incoming.items() if count == 0)
    leaves = sorted(ref for ref, count in outgoing.items() if count == 0)
    if len(roots) != 1 or not leaves:
        raise BlueprintError("Extracted Blueprint requires one entry and at least one exit.")
    operations.append(
        {
            "op": "edge.add",
            "source": edge.source,
            "source_handle": edge.source_handle,
            "target": roots[0],
            "target_handle": "target",
        }
    )
    for leaf in leaves:
        operations.append(
            {
                "op": "edge.add",
                "source": leaf,
                "source_handle": "source",
                "target": edge.target,
                "target_handle": edge.target_handle,
            }
        )
    return operations


def _resolve_extracted_value(value: Any, setup: dict[str, Any], plan: WorkflowPlan) -> Any:
    if isinstance(value, dict):
        return {key: _resolve_extracted_value(item, setup, plan) for key, item in value.items()}
    if isinstance(value, list):
        if len(value) == 2 and value[0] == "$input":
            return _query_selector(plan, _entry_node(plan))
        return [_resolve_extracted_value(item, setup, plan) for item in value]
    if isinstance(value, str) and value.startswith("$setup:"):
        field_id = value.removeprefix("$setup:")
        if field_id not in setup:
            raise BlueprintSetupError(f"Extracted Blueprint requires setup field {field_id}.")
        return deepcopy(setup[field_id])
    return deepcopy(value)


def _extract_template(
    selected: list[PlanNode],
    edges: list[PlanEdge],
    typed_interface: BlueprintTypedInterface,
) -> tuple[dict[str, Any], BlueprintPreview, list[BlueprintSetupField]]:
    selected_ids = {node.id for node in selected}
    mapping = {
        node.id: f"tmp_saved_{index}"
        for index, node in enumerate(selected, start=1)
    }
    resource_fields = list(typed_interface.resources)
    resource_by_kind = {
        field.kind: field.id
        for field in resource_fields
        if field.kind in _RESOURCE_KINDS
    }
    nodes: list[dict[str, Any]] = []
    for node in selected:
        params = _sanitize_extracted_value(
            node.params,
            selected_ids=selected_ids,
            mapping=mapping,
            resource_by_kind=resource_by_kind,
        )
        nodes.append(
            {
                "temp_ref": mapping[node.id],
                "node_type": node.type,
                "title": _clean_metadata(node.title or node.type, field="node_title", limit=256),
                "params": params,
            }
        )
    internal_edges = [
        {
            "source": mapping[edge.source],
            "source_handle": edge.source_handle,
            "target": mapping[edge.target],
            "target_handle": edge.target_handle,
        }
        for edge in edges
        if edge.source in selected_ids and edge.target in selected_ids
    ]
    preview = BlueprintPreview(
        nodes=[
            BlueprintPreviewNode(
                ref=mapping[node.id],
                label=node.title or node.type,
                kind=node.type,
                tone=(
                    "external"
                    if node.type in {"tool", "agent", "http-request", "human-input"}
                    else "resource"
                    if node.type in {"knowledge-retrieval", "document-extractor"}
                    else "model"
                    if node.type == "llm"
                    else "decision"
                    if node.type in {"if-else", "question-classifier"}
                    else "neutral"
                ),
            )
            for node in selected
        ],
        edges=[
            BlueprintPreviewEdge(
                source=item["source"],
                target=item["target"],
                label=item["source_handle"] if item["source_handle"] != "source" else "",
            )
            for item in internal_edges
        ],
        expected_behavior=[
            f"输入：{item.name}（{item.value_type}）"
            for item in typed_interface.inputs
        ]
        + [
            f"输出：{item.name}（{item.value_type}）"
            for item in typed_interface.outputs
        ],
    )
    template = {
        "kind": "extracted",
        "interface": typed_interface.model_dump(mode="json"),
        "nodes": nodes,
        "edges": internal_edges,
    }
    return template, preview, resource_fields


def _sanitize_extracted_value(
    value: Any,
    *,
    selected_ids: set[str],
    mapping: dict[str, str],
    resource_by_kind: dict[str, str],
    key: str = "",
) -> Any:
    normalized_key = key.lower().replace("-", "_")
    if normalized_key in _SENSITIVE_KEYS or normalized_key.startswith("credential"):
        return "[REDACTED]"
    if normalized_key == "_raw_data":
        return {}
    resource_kind = _resource_kind_for_key(normalized_key)
    if resource_kind and resource_kind in resource_by_kind:
        placeholder = f"$setup:{resource_by_kind[resource_kind]}"
        return [placeholder] if normalized_key.endswith("_ids") else placeholder
    if isinstance(value, dict):
        return {
            str(child_key): _sanitize_extracted_value(
                child,
                selected_ids=selected_ids,
                mapping=mapping,
                resource_by_kind=resource_by_kind,
                key=str(child_key),
            )
            for child_key, child in value.items()
            if str(child_key).lower() not in {"environment_variables", "secrets"}
        }
    if isinstance(value, list):
        if value and isinstance(value[0], str):
            if value[0] in mapping:
                return [mapping[value[0]], *deepcopy(value[1:])]
            if value[0] not in selected_ids and len(value) >= 2:
                return ["$input", "query"]
        return [
            _sanitize_extracted_value(
                item,
                selected_ids=selected_ids,
                mapping=mapping,
                resource_by_kind=resource_by_kind,
            )
            for item in value
        ]
    if isinstance(value, str):
        if value in mapping:
            return mapping[value]
        _assert_secret_free({key or "value": value})
        return str(redact_sensitive_data(value))
    if value is None or isinstance(value, (bool, int, float)):
        return value
    raise BlueprintError("Extracted Blueprint contains a non-JSON value.")


def _resource_kind_for_key(key: str) -> str | None:
    if key in {"dataset_id", "dataset_ids"}:
        return "dataset"
    if key in {"model", "model_id", "model_name", "provider"}:
        return "model"
    if key in {"tool", "tool_id", "tool_name", "provider_id"}:
        return "tool"
    if key in {"trigger", "trigger_id", "subscription_id", "event_name"}:
        return "trigger"
    return None


def _blueprint_goal_plan(blueprint: BlueprintDefinition) -> GoalPlan:
    return GoalPlan(
        goal=f"Apply {blueprint.name} {blueprint.version} safely.",
        assumptions=["Blueprint metadata and setup values are untrusted data."],
        constraints=[
            "One transactional typed Patch only.",
            "No permission expansion, Dify write, approval, or publish.",
            "Preserve unrelated graph and server-generate final node IDs.",
        ],
        success_criteria=[
            "Setup and resources are compatible.",
            "Deterministic validation and review pass.",
            "The source Candidate head remains unchanged.",
        ],
        steps=[
            GoalStep(id="setup", description="Validate typed setup and pinned resources.", status="completed"),
            GoalStep(id="patch", description="Expand one normal transactional Patch.", status="in_progress", depends_on=["setup"]),
            GoalStep(id="review", description="Validate and expose business review.", depends_on=["patch"]),
        ],
    )


def _completed_blueprint_goal_plan(plan: GoalPlan) -> GoalPlan:
    payload = plan.model_dump(mode="json")
    for step in payload["steps"]:
        step["status"] = "completed"
        step["evidence"] = ["typed setup", "one Patch transaction", "deterministic validation"]
    payload["revision"] = plan.revision + 1
    return GoalPlan.model_validate(payload)


def _entry_node(plan: WorkflowPlan) -> PlanNode:
    entries = [
        node
        for node in plan.nodes
        if node.type in {"start", "datasource", "trigger-webhook", "trigger-plugin", "trigger-schedule"}
    ]
    if len(entries) != 1:
        raise BlueprintError("Blueprint application requires exactly one authoritative entry.")
    return entries[0]


def _insertion_edge(plan: WorkflowPlan) -> PlanEdge:
    terminal_ids = {node.id for node in plan.nodes if node.type in {"end", "answer"}}
    candidates = [edge for edge in plan.edges if edge.target in terminal_ids]
    if not candidates:
        raise BlueprintError("Blueprint application could not find a safe terminal insertion path.")
    return sorted(
        candidates,
        key=lambda edge: (edge.target, edge.source, edge.source_handle, edge.target_handle),
    )[0]


def _query_selector(plan: WorkflowPlan, entry: PlanNode) -> list[str]:
    if plan.app_mode == "advanced-chat":
        return ["sys", "query"]
    variables = entry.params.get("variables")
    if isinstance(variables, list):
        for item in variables:
            if isinstance(item, dict) and str(item.get("name") or ""):
                return [entry.id, str(item["name"])]
    return [entry.id, "query"]


def _human_input_params(channel: str, prompt: str) -> dict[str, Any]:
    delivery_id = str(uuid5(NAMESPACE_URL, f"chat2dify:blueprint:review-channel:{channel}"))
    return {
        "delivery_methods": [
            {
                "id": delivery_id,
                "type": "webapp",
                "enabled": True,
                "config": {"channel_ref": channel},
            }
        ],
        "form_content": prompt,
        "inputs": [],
        "user_actions": [
            {"id": "continue", "title": "确认并继续", "button_style": "primary"}
        ],
        "timeout": 1,
        "timeout_unit": "day",
    }


def _resource_options(field: BlueprintSetupField, capabilities: list[dict[str, Any]]) -> list[dict[str, str]]:
    options = [
        {"id": str(item.get("id") or ""), "name": str(item.get("name") or item.get("id") or "")}
        for item in field.options
        if str(item.get("id") or "")
    ]
    if options:
        return options[:100]
    for item in capabilities:
        if not isinstance(item, dict):
            continue
        resource_type = str(item.get("type") or "")
        if field.kind == "dataset" and resource_type == "dataset":
            identifier = str(item.get("id") or "")
            name = str(item.get("name") or identifier)
        elif field.kind == "model" and resource_type == "model":
            provider = str(item.get("provider") or "")
            model = str(item.get("name") or "")
            identifier = f"{provider}:{model}" if provider and model else ""
            name = str(item.get("summary") or model or identifier)
        elif field.kind == "tool" and resource_type == "tool-resource":
            provider = str(item.get("provider_id") or "")
            tool = str(item.get("tool_name") or "")
            identifier = f"{provider}:{tool}" if provider and tool else ""
            name = tool or identifier
        elif field.kind == "trigger" and resource_type == "trigger":
            provider = str(item.get("provider_id") or "")
            event = str(item.get("event_name") or "")
            identifier = f"{provider}:{event}" if provider and event else ""
            name = event or identifier
        else:
            continue
        if identifier and identifier not in {option["id"] for option in options}:
            options.append({"id": identifier, "name": str(redact_sensitive_data(name))[:256]})
    return options[:100]


def _configured_preview(preview: BlueprintPreview, values: dict[str, Any]) -> BlueprintPreview:
    replacements = {
        "{dataset}": _preview_value(values.get("dataset")),
        "{review_channel}": _preview_value(values.get("review_channel")),
        "{model}": _preview_value(values.get("model")),
        "{trigger}": _preview_value(values.get("trigger")),
    }
    payload = preview.model_dump(mode="json")
    for node in payload["nodes"]:
        for token, replacement in replacements.items():
            node["label"] = str(node["label"]).replace(token, replacement)
    payload["expected_behavior"] = [
        _replace_tokens(str(item), replacements)
        for item in payload["expected_behavior"]
    ]
    return BlueprintPreview.model_validate(payload)


def _replace_tokens(value: str, replacements: dict[str, str]) -> str:
    for token, replacement in replacements.items():
        value = value.replace(token, replacement)
    return value


def _preview_value(value: Any) -> str:
    if isinstance(value, list):
        return ", ".join(str(item) for item in value)
    return str(value or "待映射")


def _snapshot_fingerprint(snapshot: AgentWorkflowSnapshot) -> str:
    if snapshot.base_hash:
        return snapshot.base_hash
    plan = WorkflowPlan.model_validate(snapshot.base_plan)
    digest = _content_hash(
        {
            "app_mode": plan.app_mode,
            "name": plan.name,
            "nodes": [node.type for node in plan.nodes],
            "edges": len(plan.edges),
        }
    )
    return f"create:{digest}"


def _content_hash(value: Any) -> str:
    return sha256(
        json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()


def _version_matches(actual: str, constraint: str) -> bool:
    value = constraint.strip()
    if value.endswith(".x"):
        return actual.startswith(value[:-1])
    if value.startswith(">="):
        return _semver_key(actual) >= _semver_key(value.removeprefix(">="))
    return actual == value


def _semver_key(value: str) -> tuple[int, int, int]:
    match = _SEMVER.fullmatch(str(value).strip())
    if not match:
        return (0, 0, 0)
    return tuple(int(part) for part in match.groups())


def _gallery_score(blueprint: BlueprintDefinition, needle: str, availability: BlueprintAvailability) -> int:
    score = 500 if availability.applicable else 200 if availability.compatible else 0
    if needle:
        if blueprint.name.lower() == needle:
            score += 500
        elif needle in blueprint.name.lower():
            score += 300
        elif needle in blueprint.business_outcome.lower():
            score += 150
    if blueprint.visibility == "team":
        score += 30
    elif blueprint.visibility == "private":
        score += 20
    return min(score, 10_000)


def _reason_message(reasons: list[dict[str, str]]) -> str:
    return "；".join(reason.get("message") or reason.get("code") or "不可用" for reason in reasons) or "Blueprint 当前不可用。"


def _as_string_list(value: Any) -> list[str]:
    if isinstance(value, list):
        return [str(item) for item in value]
    return [str(value)]


def _split_resource_ref(value: str) -> tuple[str, str]:
    if ":" not in value:
        raise BlueprintSetupError("Model mapping must include provider and model.")
    provider, model = value.rsplit(":", 1)
    if not provider or not model:
        raise BlueprintSetupError("Model mapping must include provider and model.")
    return provider, model


def _unique_nonempty(values: list[str]) -> list[str]:
    result: list[str] = []
    for value in values:
        item = str(value).strip()
        if item and item not in result:
            result.append(item)
    return result


def _clean_metadata(value: str, *, field: str, limit: int) -> str:
    text = str(redact_sensitive_data(str(value))).strip()
    if not text:
        raise BlueprintError(f"Blueprint {field} is required.")
    if len(text) > limit:
        raise BlueprintError(f"Blueprint {field} is too long.")
    _assert_secret_free({field: text})
    return text


def _assert_secret_free(value: Any) -> None:
    def visit(item: Any, key: str = "") -> None:
        normalized_key = key.lower().replace("-", "_")
        if normalized_key in _SENSITIVE_KEYS or normalized_key.startswith("credential"):
            if not (item is None or item is False or item == "" or item == "[REDACTED]"):
                raise BlueprintSecretFound(f"Secret-like field is not allowed in a Blueprint: {key}.")
        if isinstance(item, dict):
            for child_key, child in item.items():
                visit(child, str(child_key))
        elif isinstance(item, (list, tuple)):
            for child in item:
                visit(child, key)
        elif isinstance(item, str):
            for pattern in _SECRET_PATTERNS:
                if pattern.search(item):
                    raise BlueprintSecretFound("Secret-like content was detected and rejected.")

    visit(value)


def _slugify(name: str, blueprint_id: str) -> str:
    normalized = re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")
    if not normalized:
        normalized = "project-blueprint"
    return f"{normalized[:90]}-{blueprint_id[:8]}"


def _blueprint_upgrade_diff(source: BlueprintDefinition, target: BlueprintDefinition) -> list[dict[str, Any]]:
    changes: list[dict[str, Any]] = []
    fields = [
        "business_outcome",
        "supported_app_modes",
        "dify_version_range",
        "dsl_versions",
        "setup_schema",
        "capabilities",
        "resources",
        "estimated_cost",
        "risk",
        "validators",
        "scenarios",
        "preview",
        "deprecated",
        "deprecation_message",
    ]
    source_payload = source.model_dump(mode="json")
    target_payload = target.model_dump(mode="json")
    for field in fields:
        if source_payload.get(field) != target_payload.get(field):
            changes.append(
                {
                    "field": field,
                    "before": source_payload.get(field),
                    "after": target_payload.get(field),
                }
            )
    if source.version != target.version:
        changes.insert(
            0,
            {"field": "version", "before": source.version, "after": target.version},
        )
    if target.upgrade_notes:
        changes.append({"field": "upgrade_notes", "before": [], "after": target.upgrade_notes})
    return changes


def _field(
    field_id: str,
    kind: str,
    label: str,
    help_text: str,
    *,
    default: Any = None,
    options: list[dict[str, str]] | None = None,
    multiple: bool = False,
) -> BlueprintSetupField:
    return BlueprintSetupField(
        id=field_id,
        kind=kind,
        label=label,
        help_text=help_text,
        default=default,
        options=options or [],
        multiple=multiple,
    )


def _preview(
    nodes: list[tuple[str, str, str, str]],
    edges: list[tuple[str, str, str]],
    expected: list[str],
) -> BlueprintPreview:
    return BlueprintPreview(
        nodes=[
            BlueprintPreviewNode(ref=ref, label=label, kind=kind, tone=tone)
            for ref, label, kind, tone in nodes
        ],
        edges=[
            BlueprintPreviewEdge(source=source, target=target, label=label)
            for source, target, label in edges
        ],
        expected_behavior=expected,
    )


def _builtin_blueprints() -> list[_RegisteredBlueprint]:
    published = utc_now()
    shared_modes = {"workflow", "advanced-chat"}
    base: list[tuple[BlueprintDefinition, str]] = []

    knowledge_preview = _preview(
        [
            ("input", "业务问题", "input", "neutral"),
            ("knowledge", "检索 {dataset}", "knowledge-retrieval", "resource"),
            ("review", "复核 {review_channel}", "human-input", "external"),
            ("result", "可信结果", "output", "neutral"),
        ],
        [("input", "knowledge", ""), ("knowledge", "review", "低置信度"), ("review", "result", "确认")],
        [
            "仅使用 {dataset} 提供检索依据。",
            "证据不足时通过 {review_channel} 请求人工复核。",
            "应用只创建 Candidate，不写入 Dify。",
        ],
    )
    for version, notes in [
        ("1.0.0", ["初始知识检索与人工复核组合。"]),
        ("1.1.0", ["增加 grounded prompt Setup 与明确的低置信度复核说明。"]),
    ]:
        base.append(
            (
                BlueprintDefinition(
                    id="builtin-knowledge-human-fallback",
                    slug="knowledge-retrieval-human-fallback",
                    name="Knowledge Retrieval with Human Fallback",
                    business_outcome="用知识库生成有依据的回答，并在证据不足时交给人工复核。",
                    description="面向客服与内部知识问答的可治理检索模式。",
                    category="Knowledge & Support",
                    use_cases=["知识问答", "售后支持", "人工复核"],
                    preview=knowledge_preview,
                    supported_app_modes=shared_modes,
                    dify_version_range="1.14.x",
                    dsl_versions={"0.6.0", "9.9.9"},
                    setup_schema=[
                        _field("dataset", "dataset", "Staging Dataset", "选择固定 Snapshot 中可用的测试/预发布知识库。"),
                        _field(
                            "review_channel",
                            "tool",
                            "Review Channel",
                            "映射人工复核入口；这里只保存不含凭据的引用。",
                            default="webapp",
                            options=[{"id": "webapp", "name": "Dify Web App Review Inbox"}],
                        ),
                        _field(
                            "grounding_prompt",
                            "prompt",
                            "Grounding Prompt",
                            "声明证据不足时的行为，不接受 Secret。",
                            default="仅依据检索资料回答；证据不足时转人工复核。",
                        ),
                    ],
                    capabilities=["knowledge-retrieval", "human-input"],
                    resources=[
                        BlueprintResourceRequirement(kind="dataset", setup_field_id="dataset", reason="Grounded answer requires one explicit Dataset."),
                        BlueprintResourceRequirement(kind="tool", setup_field_id="review_channel", reason="Human fallback requires one explicit review channel."),
                    ],
                    estimated_cost="variable",
                    risk="high",
                    risk_reasons=["检索可能产生资源成本。", "人工复核具有外部通知副作用。"],
                    validators=["dataset-pinned", "human-action-routed", "secret-scan", "normal-patch-chain"],
                    scenarios=[
                        BlueprintScenario(name="有依据回答", input_summary="知识库中存在明确答案的问题。", expected="回答引用检索语境并进入复核路径。"),
                        BlueprintScenario(name="证据不足", input_summary="知识库无覆盖的问题。", expected="不编造结论并进入人工复核。"),
                    ],
                    provenance=BlueprintProvenance(source="chat2dify", author="Chat2Dify Product Team"),
                    version=version,
                    visibility="builtin",
                    upgrade_notes=notes,
                    published_at=published,
                ),
                "knowledge-human",
            )
        )

    definitions: list[tuple[dict[str, Any], str]] = [
        (
            {
                "id": "builtin-human-fallback",
                "slug": "human-fallback",
                "name": "Human Fallback",
                "outcome": "把无法自动处理的情况交给明确的人工复核入口。",
                "description": "为现有业务路径增加可审计人工接管。",
                "category": "Governance",
                "use_cases": ["人工接管", "高风险复核"],
                "preview": _preview(
                    [("input", "自动路径", "input", "neutral"), ("review", "人工复核", "human-input", "external"), ("result", "继续执行", "output", "neutral")],
                    [("input", "review", "需复核"), ("review", "result", "确认")],
                    ["通过 {review_channel} 发起复核。"],
                ),
                "fields": [
                    _field("review_channel", "tool", "Review Channel", "选择不包含凭据值的复核入口。", default="webapp", options=[{"id": "webapp", "name": "Dify Web App Review Inbox"}]),
                    _field("handoff_prompt", "prompt", "Handoff Prompt", "给复核者的业务说明。", default="请复核当前处理建议。"),
                ],
                "capabilities": ["human-input"],
                "cost": "low",
                "risk": "high",
                "risk_reasons": ["人工复核可能产生外部通知。"],
            },
            "human-fallback",
        ),
        (
            {
                "id": "builtin-json-extraction",
                "slug": "json-extraction",
                "name": "Structured JSON Extraction",
                "outcome": "把自然语言输入转换为有类型的业务字段。",
                "description": "通过 Parameter Extractor 生成受验证结构化结果。",
                "category": "Data Processing",
                "use_cases": ["JSON 提取", "字段结构化"],
                "preview": _preview(
                    [("input", "自然语言", "input", "neutral"), ("extract", "结构化提取", "parameter-extractor", "model"), ("result", "Typed Result", "output", "neutral")],
                    [("input", "extract", ""), ("extract", "result", "")],
                    ["输出字段 {output_variable} 由确定性 Schema 校验。"],
                ),
                "fields": [
                    _field("extraction_prompt", "prompt", "Extraction Prompt", "描述需要提取的业务字段。", default="提取用户输入中的核心业务字段。"),
                    _field("output_variable", "variable", "Output Variable", "声明安全的输出变量名。", default="result"),
                ],
                "capabilities": ["parameter-extractor"],
                "cost": "variable",
                "risk": "medium",
                "risk_reasons": ["参数提取可能调用模型。"],
            },
            "json-extraction",
        ),
        (
            {
                "id": "builtin-document-intake",
                "slug": "document-intake",
                "name": "Document Intake",
                "outcome": "接收用户文档并提取文本供后续业务节点处理。",
                "description": "添加 typed file input 与 Document Extractor。",
                "category": "Data Processing",
                "use_cases": ["文档解析", "文件接收"],
                "preview": _preview(
                    [("file", "File Input", "input", "neutral"), ("extract", "Document Extractor", "document-extractor", "resource"), ("result", "Extracted Text", "output", "neutral")],
                    [("file", "extract", ""), ("extract", "result", "")],
                    ["文件必须由用户提供；Blueprint 不伪造测试文件。"],
                ),
                "fields": [_field("file_variable", "variable", "File Variable", "声明 Start 节点的文件变量。", default="document")],
                "capabilities": ["document-extractor"],
                "cost": "low",
                "risk": "medium",
                "risk_reasons": ["文件内容是不可信数据。"],
            },
            "document-intake",
        ),
        (
            {
                "id": "builtin-webhook-ingestion",
                "slug": "webhook-ingestion",
                "name": "Webhook Ingestion",
                "outcome": "通过明确的 Webhook Schema 接收外部业务事件。",
                "description": "用受限 entry.replace 把 Start 转为 Webhook Trigger。",
                "category": "Triggers",
                "use_cases": ["Webhook", "事件接入"],
                "preview": _preview(
                    [("trigger", "Webhook", "trigger-webhook", "external"), ("flow", "Existing Flow", "workflow", "neutral")],
                    [("trigger", "flow", "POST")],
                    ["只替换权威 Entry，并保留全部下游路径。"],
                ),
                "fields": [
                    _field("http_method", "trigger", "HTTP Method", "选择受支持方法。", default="POST", options=[{"id": "POST", "name": "POST"}, {"id": "PUT", "name": "PUT"}]),
                    _field("payload_variable", "variable", "Payload Variable", "声明 Webhook body 变量。", default="query"),
                ],
                "capabilities": ["trigger-webhook"],
                "cost": "none",
                "risk": "high",
                "risk_reasons": ["Webhook 是外部触发入口。"],
                "modes": {"workflow"},
            },
            "webhook-ingestion",
        ),
        (
            {
                "id": "builtin-scheduled-report",
                "slug": "scheduled-report",
                "name": "Scheduled Report",
                "outcome": "按明确时区与频率启动报告流程。",
                "description": "以受限 Schedule Trigger 替换 Start Entry。",
                "category": "Triggers",
                "use_cases": ["定时报表", "周期任务"],
                "preview": _preview(
                    [("schedule", "Schedule", "trigger-schedule", "external"), ("report", "Report Flow", "workflow", "neutral")],
                    [("schedule", "report", "daily")],
                    ["按 {timezone} 的配置触发；不会在应用 Blueprint 时执行。"],
                ),
                "fields": [
                    _field("frequency", "trigger", "Frequency", "选择明确频率。", default="daily", options=[{"id": "daily", "name": "Daily"}, {"id": "weekly", "name": "Weekly"}]),
                    _field("report_time", "policy", "Report Time", "使用 12 小时时间格式。", default="09:00 AM", options=[{"id": "09:00 AM", "name": "09:00 AM"}, {"id": "06:00 PM", "name": "06:00 PM"}]),
                    _field("timezone", "policy", "Timezone", "使用 IANA 时区。", default="Asia/Shanghai", options=[{"id": "Asia/Shanghai", "name": "Asia/Shanghai"}, {"id": "UTC", "name": "UTC"}]),
                ],
                "capabilities": ["trigger-schedule"],
                "cost": "variable",
                "risk": "high",
                "risk_reasons": ["定时触发可能调用外部资源。"],
                "modes": {"workflow"},
            },
            "scheduled-report",
        ),
        (
            {
                "id": "builtin-error-retry",
                "slug": "error-retry",
                "name": "Error Handling & Retry",
                "outcome": "识别可重试业务信号并走显式有限重试/失败路径。",
                "description": "添加 typed If/Else 重试判断，不自动执行外部重试。",
                "category": "Reliability",
                "use_cases": ["错误处理", "有限重试"],
                "preview": _preview(
                    [("input", "Result", "input", "neutral"), ("guard", "Retry Guard", "if-else", "decision"), ("result", "Continue", "output", "neutral")],
                    [("input", "guard", ""), ("guard", "result", "retryable / false")],
                    ["只创建显式分支；不会自动重放任何外部动作。"],
                ),
                "fields": [_field("retry_marker", "variable", "Retry Marker", "声明可重试业务标记。", default="retry")],
                "capabilities": ["if-else"],
                "cost": "none",
                "risk": "medium",
                "risk_reasons": ["重试必须保持有界且显式。"],
            },
            "error-retry",
        ),
        (
            {
                "id": "builtin-model-routing",
                "slug": "model-routing",
                "name": "Model Routing",
                "outcome": "把业务请求交给明确映射且兼容的模型。",
                "description": "从固定 Resource Catalog 选择模型并添加 LLM 节点。",
                "category": "AI Quality",
                "use_cases": ["模型路由", "模型替换"],
                "preview": _preview(
                    [("input", "Request", "input", "neutral"), ("model", "{model}", "llm", "model"), ("result", "Response", "output", "neutral")],
                    [("input", "model", ""), ("model", "result", "")],
                    ["模型来自固定 Snapshot：{model}。"],
                ),
                "fields": [
                    _field("model", "model", "Model", "选择当前 Dify Workspace 可用模型。"),
                    _field("routing_prompt", "prompt", "Routing Prompt", "声明模型任务，不含 Secret。", default="处理当前业务输入并返回可靠结果。"),
                ],
                "capabilities": ["llm"],
                "cost": "variable",
                "risk": "medium",
                "risk_reasons": ["模型调用产生用量和成本。"],
            },
            "model-routing",
        ),
        (
            {
                "id": "builtin-support-classification",
                "slug": "support-classification",
                "name": "Support Classification",
                "outcome": "把客户请求分为咨询、投诉和紧急事件。",
                "description": "添加显式 Question Classifier 业务分支。",
                "category": "Knowledge & Support",
                "use_cases": ["客服分类", "售后分流"],
                "preview": _preview(
                    [("input", "Customer Request", "input", "neutral"), ("classify", "Support Classifier", "question-classifier", "decision"), ("result", "Business Route", "output", "neutral")],
                    [("input", "classify", ""), ("classify", "result", "咨询 / 投诉 / 紧急")],
                    ["三个业务分类都有明确下游路径。"],
                ),
                "fields": [_field("classification_prompt", "prompt", "Classification Rule", "用业务语言说明分类标准。", default="根据客户诉求区分咨询、投诉和紧急事件。")],
                "capabilities": ["question-classifier"],
                "cost": "variable",
                "risk": "medium",
                "risk_reasons": ["分类器可能调用模型。"],
            },
            "support-classification",
        ),
    ]
    for data, pattern in definitions:
        fields = data["fields"]
        resources = [
            BlueprintResourceRequirement(
                kind=field.kind,
                setup_field_id=field.id,
                reason=f"{field.label} must be explicitly mapped.",
            )
            for field in fields
            if field.kind in _RESOURCE_KINDS
        ]
        definition = BlueprintDefinition(
            id=data["id"],
            slug=data["slug"],
            name=data["name"],
            business_outcome=data["outcome"],
            description=data["description"],
            category=data["category"],
            use_cases=data["use_cases"],
            preview=data["preview"],
            supported_app_modes=data.get("modes", shared_modes),
            dify_version_range="1.14.x",
            dsl_versions={"0.6.0", "9.9.9"},
            setup_schema=fields,
            capabilities=data["capabilities"],
            resources=resources,
            estimated_cost=data["cost"],
            risk=data["risk"],
            risk_reasons=data["risk_reasons"],
            validators=["typed-setup", "secret-scan", "normal-patch-chain"],
            scenarios=[
                BlueprintScenario(
                    name=f"{data['name']} 最小路径",
                    input_summary="使用固定最小业务输入与显式资源映射。",
                    expected="生成一个有效 Candidate，且无关图保持不变。",
                )
            ],
            provenance=BlueprintProvenance(source="chat2dify", author="Chat2Dify Product Team"),
            version="1.0.0",
            visibility="builtin",
            upgrade_notes=["Initial governed Blueprint version."],
            published_at=published,
        )
        base.append((definition, pattern))
    return [
        _RegisteredBlueprint(definition=definition, template={"kind": "builtin", "pattern": pattern})
        for definition, pattern in base
    ]
