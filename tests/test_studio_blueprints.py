from __future__ import annotations

from datetime import timedelta

import pytest

from app.agent.approval import AgentApprovalService
from app.agent.catalog import NodeCapabilityCatalog
from app.agent.review import WorkflowReviewService
from app.agent.service import AgentApplicationService
from app.agent.state import AgentSession, AgentWorkflowSnapshot, utc_now
from app.agent.store import AgentStore
from app.agent.validation import WorkflowValidationService
from app.agent.workspace import VersionedWorkflowWorkspace
from app.compiler.dify import DifyDslCompiler
from app.studio.blueprints import (
    BlueprintError,
    BlueprintPolicyDenied,
    BlueprintRegistry,
    BlueprintSecretFound,
    StudioBlueprintService,
    _RegisteredBlueprint,
    _evaluate_blueprint_patch_policy,
    _expand_blueprint,
)
from app.studio.build import StudioBuildService
from app.studio.identity import AuthenticatedStudioRequest
from app.studio.models import (
    BlueprintInterfaceField,
    BlueprintSetupValue,
    BlueprintTypedInterface,
    Principal,
    StudioSession,
    VerifiedHostContext,
)
from app.studio.store import StudioAccessDenied, StudioStore
from app.models import WorkflowPlan


class _NoopDispatcher:
    def submit(self, _run_id: str) -> None:
        raise AssertionError("Blueprint application must not dispatch the model Runtime.")

    def close(self) -> None:
        return None


class _StaticSnapshot:
    def __init__(self, catalog: NodeCapabilityCatalog, *, app_mode: str = "workflow") -> None:
        self.catalog = catalog
        self.app_mode = app_mode

    def capture(self, session: AgentSession) -> AgentWorkflowSnapshot:
        assert session.app_mode == self.app_mode
        terminal = (
            {
                "id": "answer",
                "type": "answer",
                "title": "Answer",
                "params": {"answer": "{{#sys.query#}}"},
            }
            if self.app_mode == "advanced-chat"
            else {
                "id": "end",
                "type": "end",
                "title": "End",
                "params": {
                    "outputs": [
                        {
                            "variable": "answer",
                            "value_selector": ["start", "query"],
                        }
                    ]
                },
            }
        )
        include_unrelated_path = (
            self.app_mode == "workflow"
            and "Scheduled Report" not in (session.app_name or "")
        )
        side_nodes = [
            {
                "id": f"side-{index:02d}",
                "type": "code",
                "title": f"Preserved side path {index:02d}",
                "params": {
                    "code_language": "python3",
                    "code": "def main():\n    return {'result': 'unchanged'}",
                    "variables": [],
                    "outputs": {"result": {"type": "string"}},
                },
            }
            for index in range(30)
        ] if include_unrelated_path else []
        side_terminal = {
            "id": "zz-side-end",
            "type": "end",
            "title": "Preserved side terminal",
            "params": {
                "outputs": [
                    {
                        "variable": "side_result",
                        "value_selector": ["side-29", "result"],
                    }
                ]
            },
        }
        side_edges = []
        if include_unrelated_path:
            side_edges = [
                {"source": "start", "target": "side-00"},
                *[
                    {
                        "source": f"side-{index:02d}",
                        "target": f"side-{index + 1:02d}",
                    }
                    for index in range(29)
                ],
                {"source": "side-29", "target": "zz-side-end"},
            ]
        plan = {
            "name": "Blueprint Fixture",
            "description": "Deterministic Blueprint fixture.",
            "app_mode": self.app_mode,
            "nodes": [
                {
                    "id": "start",
                    "type": "start",
                    "title": "Start",
                    "params": {
                        "variables": (
                            []
                            if self.app_mode == "advanced-chat"
                            else [
                                {
                                    "name": "query",
                                    "type": "paragraph",
                                    "required": True,
                                    "label": "Query",
                                }
                            ]
                        )
                    },
                },
                terminal,
                *side_nodes,
                *([side_terminal] if include_unrelated_path else []),
            ],
            "edges": [
                {"source": "start", "target": terminal["id"]},
                *side_edges,
            ],
        }
        capabilities = [
            item.model_dump(mode="json")
            for item in self.catalog.list()
            if self.app_mode in item.supported_app_modes
        ]
        capabilities.extend(
            [
                {
                    "type": "dataset",
                    "id": "dataset-staging",
                    "name": "Staging Support KB",
                    "summary": "untrusted metadata",
                    "untrusted_data": True,
                },
                {
                    "type": "model",
                    "provider": "langgenius/openai/openai",
                    "name": "gpt-4o-mini",
                    "summary": "Compatible model",
                    "status": "active",
                    "untrusted_data": True,
                },
            ]
        )
        return AgentWorkflowSnapshot(
            operation="create",
            app_name=session.app_name or "Blueprint Fixture",
            app_mode=self.app_mode,
            base_plan=plan,
            base_graph={},
            capabilities=capabilities,
            dify_version={"git_describe": "1.14.2", "app_dsl_version": "0.6.0"},
            compatibility={
                "dify_version": "1.14.2",
                "dsl_version": "0.6.0",
                "mutation_supported": True,
                "reason": "deterministic fixture",
            },
        )


def _stack(tmp_path, *, app_mode: str = "workflow"):
    studio_store = StudioStore(f"sqlite:///{tmp_path / f'studio-{app_mode}.sqlite3'}")
    agent_store = AgentStore(tmp_path / f"agent-{app_mode}.sqlite3")
    catalog = NodeCapabilityCatalog()
    compiler = DifyDslCompiler(
        dsl_version="0.6.0",
        default_model_provider="langgenius/openai/openai",
        default_model_name="gpt-4o-mini",
        default_dataset_ids=["dataset-staging"],
    )
    validation = WorkflowValidationService(
        compiler=compiler,
        expected_dsl_version="0.6.0",
    )
    workspace = VersionedWorkflowWorkspace(
        store=agent_store,
        validation=validation,
        catalog=catalog,
    )
    review = WorkflowReviewService(store=agent_store, workspace=workspace)
    agent_service = AgentApplicationService(
        store=agent_store,
        dispatcher=_NoopDispatcher(),
        approval=AgentApprovalService(store=agent_store),
        commit_service=object(),  # type: ignore[arg-type]
    )
    build_service = StudioBuildService(
        store=studio_store,
        agent_store=agent_store,
        agent_service=agent_service,
    )
    blueprint_service = StudioBlueprintService(
        store=studio_store,
        agent_store=agent_store,
        agent_service=agent_service,
        build_service=build_service,
        snapshot_service=_StaticSnapshot(catalog, app_mode=app_mode),  # type: ignore[arg-type]
        workspace=workspace,
        review=review,
        catalog=catalog,
    )
    principal = Principal(
        issuer="chat2dify-studio",
        subject=f"alice-{app_mode}",
        display_name="Alice",
        dify_tenant_id="tenant-1",
    )
    project, membership = studio_store.ensure_personal_project(principal)
    now = utc_now()
    authenticated = AuthenticatedStudioRequest(
        claims={},
        session=StudioSession(
            id=f"session-{app_mode}",
            jti_hash="j" * 32,
            principal_key=principal.key,
            project_id=project.id,
            dify_account_id=principal.subject,
            dify_tenant_id=principal.dify_tenant_id,
            origin="http://dify.local",
            nonce_hash="n" * 32,
            expires_at=now + timedelta(minutes=5),
            created_at=now,
        ),
        principal=principal,
        project=project,
        membership=membership,
        host=VerifiedHostContext(principal=principal, apps=[]),
    )
    build = build_service.create(
        authenticated,
        project_id=project.id,
        operation="create",
        entry_source="create",
        app_id=None,
        app_mode=app_mode,
        app_name="Blueprint Fixture",
    )
    return (
        blueprint_service,
        build_service,
        studio_store,
        agent_store,
        authenticated,
        project,
        build,
    )


def _setup_values(definition) -> list[BlueprintSetupValue]:
    values: list[BlueprintSetupValue] = []
    for field in definition.setup_schema:
        value = field.default
        if field.id == "dataset":
            value = "dataset-staging"
        elif field.id == "model":
            value = "langgenius/openai/openai:gpt-4o-mini"
        assert value is not None
        values.append(
            BlueprintSetupValue(field_id=field.id, kind=field.kind, value=value)
        )
    return values


def test_registry_gallery_and_guided_setup_cover_product_contract(tmp_path) -> None:
    service, _build_service, _store, _agent_store, auth, project, build = _stack(tmp_path)
    registry = BlueprintRegistry()

    assert len(registry.list_current()) == 9
    kinds = {
        field.kind
        for item in registry.list_current()
        for field in item.definition.setup_schema
    }
    assert {"model", "dataset", "tool", "trigger", "prompt", "variable", "policy"} <= kinds

    gallery = service.gallery(
        auth,
        project_id=project.id,
        build_id=build.id,
        search="knowledge retrieval human fallback",
    )
    assert [item.blueprint.id for item in gallery.items] == [
        "builtin-knowledge-human-fallback"
    ]
    item = gallery.items[0]
    assert item.availability.applicable is True
    assert item.availability.available_resources["review_channel"] == [
        {"id": "webapp", "name": "Dify Web App Review Inbox"}
    ]
    assert item.blueprint.preview.nodes
    assert item.blueprint.validators
    assert item.blueprint.scenarios
    assert item.blueprint.provenance.untrusted_metadata is True
    validation = service.validate_setup(
        auth,
        project_id=project.id,
        blueprint_id=item.blueprint.id,
        version="1.0.0",
        build_id=build.id,
        values=_setup_values(item.blueprint.model_copy(update={"version": "1.0.0"})),
    )
    assert validation.ok is True
    assert {result["field_id"] for result in validation.field_results} == {
        "dataset",
        "review_channel",
        "grounding_prompt",
    }
    assert validation.risk["permission_expansion"] is False


def test_initial_nine_blueprints_apply_as_one_valid_transaction(tmp_path) -> None:
    service, _build_service, _studio_store, agent_store, auth, project, _build = _stack(tmp_path)
    successes = 0
    preserved_unrelated = 0
    total_unrelated = 0
    registry = BlueprintRegistry()
    for registered in registry.list_current():
        build = service.build_service.create(
            auth,
            project_id=project.id,
            operation="create",
            entry_source="create",
            app_id=None,
            app_mode="workflow",
            app_name=f"Fixture {registered.definition.name}",
        )
        result = service.apply(
            auth,
            project_id=project.id,
            build_id=build.id,
            blueprint_id=registered.definition.id,
            version=registered.definition.version,
            values=_setup_values(registered.definition),
        )
        candidate = next(
            item
            for item in result.build.candidates
            if item.candidate.id == result.application.candidate_id
        )
        versions = agent_store.list_workspace_versions(candidate.candidate.run_id)
        assert candidate.candidate.status == "valid"
        assert candidate.reconstructable is True
        assert len(versions) == 2
        assert versions[1].patch is not None
        assert result.patch_operation_count == len(versions[1].patch["operations"])
        assert result.dify_write_count == 0
        assert result.source_head_unchanged is True
        before_ids = {node["id"] for node in versions[0].snapshot["nodes"]}
        after_ids = {node["id"] for node in versions[1].snapshot["nodes"]}
        before_unrelated = {
            node["id"]: node
            for node in versions[0].snapshot["nodes"]
            if node["id"].startswith("side-")
        }
        after_by_id = {node["id"]: node for node in versions[1].snapshot["nodes"]}
        for node_id, node in before_unrelated.items():
            after_node = after_by_id.get(node_id, {})
            stable_values = [
                (node.get("id"), after_node.get("id")),
                (node.get("type"), after_node.get("type")),
                (node.get("title"), after_node.get("title")),
                (node.get("desc"), after_node.get("desc")),
                (node.get("params", {}).get("code"), after_node.get("params", {}).get("code")),
                (
                    node.get("params", {}).get("code_language"),
                    after_node.get("params", {}).get("code_language"),
                ),
            ]
            total_unrelated += len(stable_values)
            preserved_unrelated += sum(before == after for before, after in stable_values)
        before_internal_edges = [
            edge
            for edge in versions[0].snapshot["edges"]
            if edge["source"].startswith("side-")
        ]
        after_edges = versions[1].snapshot["edges"]
        total_unrelated += len(before_internal_edges)
        preserved_unrelated += sum(edge in after_edges for edge in before_internal_edges)
        if registered.definition.id not in {
            "builtin-webhook-ingestion",
            "builtin-scheduled-report",
        }:
            assert before_ids <= after_ids
        successes += 1
    assert successes / len(registry.list_current()) >= 0.95
    assert preserved_unrelated / total_unrelated >= 0.99


def test_malicious_blueprint_metadata_cannot_expand_tools_permissions_or_actions(tmp_path) -> None:
    service, _build_service, _studio_store, _agent_store, _auth, _project, _build = _stack(tmp_path)
    snapshot = service.snapshot_service.capture(
        AgentSession(operation="create", app_mode="workflow", app_name="Safe base")
    )
    definition = service.registry.get("builtin-json-extraction").definition
    registered = _RegisteredBlueprint(
        definition=definition,
        template={
            "kind": "extracted",
            "permissions": ["owner", "admin"],
            "tool_visibility": ["*"],
            "approve": True,
            "publish": True,
            "raw_dsl": {"injected": True},
            "nodes": [
                {
                    "temp_ref": "tmp_safe_code",
                    "node_type": "code",
                    "title": "Safe transform",
                    "params": {
                        "language": "python3",
                        "code": "def main(query: str):\n    return {'result': query}",
                        "variables": [
                            {
                                "variable": "query",
                                "value_selector": ["$input", "query"],
                            }
                        ],
                        "outputs": {"result": {"type": "string"}},
                    },
                }
            ],
            "edges": [],
        },
    )
    patch = _expand_blueprint(
        registered,
        plan=WorkflowPlan.model_validate(snapshot.base_plan),
        workspace_version="workspace-v0",
        expected_base_hash=snapshot.base_hash,
        values={},
    )
    serialized = patch.model_dump_json(by_alias=True)

    assert {operation.op for operation in patch.operations} <= {
        "node.add",
        "edge.add",
        "edge.remove",
    }
    assert "permissions" not in serialized
    assert "tool_visibility" not in serialized
    assert '"approve"' not in serialized
    assert '"publish"' not in serialized
    assert "raw_dsl" not in serialized
    with pytest.raises(BlueprintPolicyDenied, match="undeclared capability"):
        _evaluate_blueprint_patch_policy(
            definition,
            patch,
            catalog=service.catalog,
            app_mode="workflow",
        )


def test_apply_preserves_source_and_upgrade_is_explicit(tmp_path) -> None:
    service, _build_service, _studio_store, agent_store, auth, project, build = _stack(tmp_path)
    definition = service.registry.get(
        "builtin-knowledge-human-fallback",
        "1.0.0",
    ).definition
    result = service.apply(
        auth,
        project_id=project.id,
        build_id=build.id,
        blueprint_id=definition.id,
        version="1.0.0",
        values=_setup_values(definition),
    )
    candidate = next(
        item
        for item in result.build.candidates
        if item.candidate.id == result.application.candidate_id
    )
    head_before = agent_store.get_workspace_head(candidate.candidate.run_id).id

    preview = service.upgrade_preview(
        auth,
        project_id=project.id,
        application_id=result.application.id,
    )

    assert preview.source.version == "1.0.0"
    assert preview.target.version == "1.1.0"
    assert preview.automatic is False
    assert preview.action_required == "apply_as_new_candidate"
    assert any(change["field"] == "version" for change in preview.changes)
    assert agent_store.get_workspace_head(candidate.candidate.run_id).id == head_before


def test_extraction_requires_typed_interface_and_removes_ids_and_secrets(tmp_path) -> None:
    service, _build_service, studio_store, agent_store, auth, project, build = _stack(tmp_path)
    definition = service.registry.get("builtin-json-extraction").definition
    applied = service.apply(
        auth,
        project_id=project.id,
        build_id=build.id,
        blueprint_id=definition.id,
        values=_setup_values(definition),
    )
    candidate = next(
        item
        for item in applied.build.candidates
        if item.candidate.id == applied.application.candidate_id
    )
    head = agent_store.get_workspace_head(candidate.candidate.run_id)
    added = next(
        node
        for node in head.snapshot["nodes"]
        if node["type"] == "parameter-extractor"
    )
    typed_interface = BlueprintTypedInterface(
        inputs=[
            BlueprintInterfaceField(
                name="query",
                value_type="string",
                description="Business input",
            )
        ],
        outputs=[
            BlueprintInterfaceField(
                name="result",
                value_type="string",
                description="Structured result",
            )
        ],
    )
    extracted = service.extract(
        auth,
        project_id=project.id,
        build_id=build.id,
        candidate_id=candidate.candidate.id,
        selected_node_ids=[added["id"]],
        name="Private extraction pattern",
        business_outcome="Extract one typed result.",
        category="Team Patterns",
        visibility="private",
        typed_interface=typed_interface,
    )
    record, template = studio_store.get_blueprint_version(
        extracted.blueprint_id,
        "1.0.0",
        project_id=project.id,
        principal_key=auth.principal.key,
    )
    serialized = record.model_dump_json() + str(template)
    assert added["id"] not in serialized
    assert "environment_variables" not in serialized
    assert record.definition.visibility == "private"

    with pytest.raises(BlueprintSecretFound):
        service.extract(
            auth,
            project_id=project.id,
            build_id=build.id,
            candidate_id=candidate.candidate.id,
            selected_node_ids=[added["id"]],
            name="sk-supersecret123",
            business_outcome="Must fail secret scan.",
            category="Unsafe",
            visibility="private",
            typed_interface=typed_interface,
        )


def test_team_blueprint_and_new_versions_require_a_distinct_reviewer(tmp_path) -> None:
    service, _build_service, studio_store, agent_store, auth, project, build = _stack(tmp_path)
    definition = service.registry.get("builtin-json-extraction").definition
    applied = service.apply(
        auth,
        project_id=project.id,
        build_id=build.id,
        blueprint_id=definition.id,
        values=_setup_values(definition),
    )
    candidate = next(
        item
        for item in applied.build.candidates
        if item.candidate.id == applied.application.candidate_id
    )
    head = agent_store.get_workspace_head(candidate.candidate.run_id)
    selected = next(node for node in head.snapshot["nodes"] if node["type"] == "parameter-extractor")
    typed_interface = BlueprintTypedInterface(
        inputs=[
            BlueprintInterfaceField(
                name="query",
                value_type="string",
                description="Team pattern input",
            )
        ],
        outputs=[
            BlueprintInterfaceField(
                name="result",
                value_type="string",
                description="Team pattern output",
            )
        ],
    )
    pending = service.extract(
        auth,
        project_id=project.id,
        build_id=build.id,
        candidate_id=candidate.candidate.id,
        selected_node_ids=[selected["id"]],
        name="Reviewed team extraction",
        business_outcome="Share one reviewed structured extraction path.",
        category="Team Patterns",
        visibility="team",
        typed_interface=typed_interface,
    )
    assert pending.status == "pending_review"
    author_pending = next(
        item
        for item in service.gallery(
            auth,
            project_id=project.id,
            build_id=build.id,
            visibility="team",
        ).items
        if item.blueprint.id == pending.blueprint_id
    )
    assert author_pending.version_status == "pending_review"
    assert author_pending.can_review is False
    assert author_pending.availability.applicable is False

    with pytest.raises(StudioAccessDenied, match="cannot review their own"):
        service.review_version(
            auth,
            project_id=project.id,
            blueprint_id=pending.blueprint_id,
            version="1.0.0",
            approved=True,
            note="Self approval must fail.",
        )

    reviewer = Principal(
        issuer="chat2dify-studio",
        subject="reviewer-1",
        display_name="Riley",
        dify_tenant_id=auth.principal.dify_tenant_id,
    )
    reviewer_membership = studio_store.add_membership(
        project_id=project.id,
        actor_key=auth.principal.key,
        principal_key=reviewer.key,
        role="reviewer",
    )
    reviewer_auth = AuthenticatedStudioRequest(
        claims={},
        session=auth.session.model_copy(
            update={
                "principal_key": reviewer.key,
                "dify_account_id": reviewer.subject,
            }
        ),
        principal=reviewer,
        project=project,
        membership=reviewer_membership,
        host=VerifiedHostContext(principal=reviewer, apps=[]),
    )
    reviewer_pending = next(
        item
        for item in service.gallery(
            reviewer_auth,
            project_id=project.id,
            build_id=build.id,
            visibility="team",
        ).items
        if item.blueprint.id == pending.blueprint_id
    )
    assert reviewer_pending.can_review is True
    published = service.review_version(
        reviewer_auth,
        project_id=project.id,
        blueprint_id=pending.blueprint_id,
        version="1.0.0",
        approved=True,
        note="Typed interface and secret scan reviewed.",
    )
    assert published.status == "published"
    assert published.reviewed_by == reviewer.key

    proposed = service.propose_version(
        auth,
        project_id=project.id,
        blueprint_id=pending.blueprint_id,
        version="1.1.0",
        upgrade_notes=["Clarify expected structured output."],
    )
    assert proposed.status == "pending_review"
    pending_upgrade = next(
        item
        for item in service.gallery(
            reviewer_auth,
            project_id=project.id,
            build_id=build.id,
            visibility="team",
        ).items
        if item.blueprint.id == pending.blueprint_id
    )
    assert pending_upgrade.blueprint.version == "1.1.0"
    assert pending_upgrade.can_review is True
    reviewed_upgrade = service.review_version(
        reviewer_auth,
        project_id=project.id,
        blueprint_id=pending.blueprint_id,
        version="1.1.0",
        approved=True,
        note="Upgrade Diff reviewed.",
    )
    assert reviewed_upgrade.status == "published"
    current, _ = studio_store.get_blueprint_version(
        pending.blueprint_id,
        None,
        project_id=project.id,
        principal_key=reviewer.key,
    )
    assert current.version == "1.1.0"


def test_bad_setup_and_failed_patch_do_not_move_a_source_head(tmp_path) -> None:
    service, _build_service, _studio_store, agent_store, auth, project, build = _stack(tmp_path)
    definition = service.registry.get("builtin-knowledge-human-fallback").definition
    with pytest.raises(BlueprintError):
        service.apply(
            auth,
            project_id=project.id,
            build_id=build.id,
            blueprint_id=definition.id,
            values=[
                BlueprintSetupValue(
                    field_id="dataset",
                    kind="dataset",
                    value="dataset-not-pinned",
                )
            ],
        )
    assert agent_store.list_runs(limit=100) == []

    bad_definition = service.registry.get("builtin-error-retry").definition.model_copy(
        update={
            "id": "builtin-invalid-transaction-fixture",
            "slug": "invalid-transaction-fixture",
            "name": "Invalid transaction fixture",
            "setup_schema": [],
            "resources": [],
        }
    )
    service.registry = BlueprintRegistry(
        [
            _RegisteredBlueprint(
                definition=bad_definition,
                template={
                    "kind": "extracted",
                    "nodes": [
                        {
                            "temp_ref": "tmp_invalid_branch",
                            "node_type": "if-else",
                            "title": "Invalid branch",
                            "params": {},
                        }
                    ],
                    "edges": [],
                },
            )
        ]
    )
    with pytest.raises(BlueprintError):
        service.apply(
            auth,
            project_id=project.id,
            build_id=build.id,
            blueprint_id=bad_definition.id,
            values=[],
        )
    failed_run = agent_store.list_runs(limit=100)[0]
    versions = agent_store.list_workspace_versions(failed_run.id)
    assert failed_run.phase.value == "failed"
    assert len(versions) == 1
    assert failed_run.head_version_id == versions[0].id
    assert service.store.list_candidates(
        build.id,
        project_id=project.id,
        principal_key=auth.principal.key,
    ) == []


def test_store_migration_and_blueprint_visibility_are_project_scoped(tmp_path) -> None:
    service, _build_service, studio_store, _agent_store, auth, project, build = _stack(tmp_path)
    assert studio_store.schema_version() == 4
    gallery = service.gallery(
        auth,
        project_id=project.id,
        build_id=build.id,
    )
    assert len(gallery.items) == 9
