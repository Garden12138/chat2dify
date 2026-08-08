from __future__ import annotations

from contextlib import AbstractContextManager
from datetime import timedelta
from threading import Lock
from typing import Any, Callable, Literal, Protocol

from app.agent.commit import (
    CommitServiceError,
    SafeDraftHashConflict,
    SafeWorkflowDraftWriter,
)
from app.agent.snapshot import WorkflowSnapshotError, WorkflowSnapshotService
from app.agent.state import AgentSession
from app.dify.client import DifyConflictError
from app.models import WorkflowPlan
from app.studio.artifacts import (
    ArtifactMappingMismatch,
    assert_secret_free,
    canonical_hash,
    materialize_artifact_plan,
)
from app.studio.identity import AuthenticatedStudioRequest
from app.studio.models import (
    ChangeRequestDetail,
    DifyAppSummary,
    EnvironmentMappingSet,
    LogicalApp,
    Project,
    ReleaseAuthorization,
    ReleaseCenterView,
    ReleaseEnvironment,
    ReleasePreview,
    ReleaseRecord,
    ReleaseResourceMapping,
    new_id,
    utc_now,
)
from app.studio.reviews import ReviewStale, StudioReviewService
from app.studio.store import StudioAccessDenied, StudioConflict, StudioStore


class ReleaseClient(Protocol):
    def publish_workflow(
        self,
        app_id: str,
        *,
        marked_name: str | None = None,
        marked_comment: str | None = None,
    ): ...

    def get_published_workflow(self, app_id: str): ...


class ReleaseError(RuntimeError):
    code = "STUDIO_RELEASE_ERROR"


class ReleaseBlocked(ReleaseError):
    code = "STUDIO_RELEASE_BLOCKED"


class ReleaseAuthorizationInvalid(ReleaseError):
    code = "STUDIO_RELEASE_AUTHORIZATION_INVALID"


class ReleaseReconciliationRequired(ReleaseError):
    code = "STUDIO_RELEASE_RECONCILIATION_REQUIRED"


class StudioReleaseService:
    def __init__(
        self,
        *,
        store: StudioStore,
        reviews: StudioReviewService,
        snapshot: WorkflowSnapshotService,
        safe_writer: SafeWorkflowDraftWriter,
        client_factory: Callable[[], AbstractContextManager[ReleaseClient]],
        durable_jobs: bool = False,
    ) -> None:
        self.store = store
        self.reviews = reviews
        self.snapshot = snapshot
        self.safe_writer = safe_writer
        self.client_factory = client_factory
        self.durable_jobs = durable_jobs
        self._locks_guard = Lock()
        self._environment_locks: dict[str, Lock] = {}

    def center(
        self,
        authenticated: AuthenticatedStudioRequest,
        *,
        project_id: str,
    ) -> ReleaseCenterView:
        project, membership = self.store.get_project_for_principal(
            project_id,
            authenticated.principal.key,
        )
        requests = self.reviews.list(authenticated, project_id=project_id)
        apps = self.store.list_logical_apps(
            project_id=project_id,
            principal_key=authenticated.principal.key,
        )
        environments = self.store.list_release_environments(
            project_id=project_id,
            principal_key=authenticated.principal.key,
        )
        mappings = self.store.list_environment_mappings(
            project_id=project_id,
            principal_key=authenticated.principal.key,
        )
        releases = self.store.list_release_records(
            project_id=project_id,
            principal_key=authenticated.principal.key,
        )
        visible_apps = (
            list(authenticated.host.apps)
            if authenticated.host.apps_available
            else []
        )
        if project.kind != "personal":
            linked_ids = self.store.list_project_app_ids(
                project.id,
                authenticated.principal.key,
            )
            visible_apps = [item for item in visible_apps if item.id in linked_ids]
        count = len(requests) + len(apps) + len(releases)
        if not authenticated.host.apps_available:
            state = "partial_error"
            message = (
                "Reviews and receipts are available, but the verified Dify app list "
                "could not be loaded; target configuration is disabled."
            )
        else:
            state = "ready" if count else "empty"
            message = (
                "Review and release records are ready."
                if count
                else "Create a Change Request from a cleanup-verified Scenario result."
            )
        return ReleaseCenterView(
            project=project,
            membership=membership,
            members=self.store.list_memberships(
                project_id=project_id,
                principal_key=authenticated.principal.key,
            ),
            available_apps=visible_apps,
            change_requests=requests,
            logical_apps=apps,
            environments=environments,
            mappings=mappings,
            releases=releases,
            state=state,
            message=message,
        )

    def create_logical_app(
        self,
        authenticated: AuthenticatedStudioRequest,
        *,
        project_id: str,
        name: str,
        app_mode: Literal["workflow", "advanced-chat"],
    ) -> LogicalApp:
        self._require_releaser(authenticated, project_id)
        now = utc_now()
        return self.store.create_logical_app(
            item=LogicalApp(
                id=new_id(),
                project_id=project_id,
                name=name.strip(),
                app_mode=app_mode,
                created_by=authenticated.principal.key,
                version=1,
                created_at=now,
                updated_at=now,
            ),
            principal_key=authenticated.principal.key,
        )

    def create_environment(
        self,
        authenticated: AuthenticatedStudioRequest,
        *,
        project_id: str,
        logical_app_id: str,
        name: str,
        classification: Literal["development", "staging", "production"],
        target_app_ref: str,
    ) -> ReleaseEnvironment:
        project = self._require_releaser(authenticated, project_id)
        logical_app = self.store.get_logical_app(
            logical_app_id,
            project_id=project_id,
            principal_key=authenticated.principal.key,
        )
        visible_app = self._assert_target_authorized(
            authenticated,
            project=project,
            target_app_ref=target_app_ref,
        )
        if visible_app.mode != logical_app.app_mode:
            raise ReleaseBlocked("The target Dify app mode does not match the logical app.")
        snapshot = self._capture_target(
            target_app_ref=target_app_ref,
            app_mode=logical_app.app_mode,
            name=name,
        )
        now = utc_now()
        environment = self.store.create_release_environment(
            item=ReleaseEnvironment(
                id=new_id(),
                project_id=project_id,
                logical_app_id=logical_app_id,
                name=name.strip(),
                classification=classification,
                target_app_ref=target_app_ref,
                tracked_draft_hash=snapshot.base_hash,
                enabled=True,
                version=1,
                created_by=authenticated.principal.key,
                created_at=now,
                updated_at=now,
            ),
            principal_key=authenticated.principal.key,
        )
        self.store.upsert_environment_mapping(
            item=EnvironmentMappingSet(
                id=new_id(),
                project_id=project_id,
                environment_id=environment.id,
                mappings=[],
                mapping_hash=canonical_hash([]),
                configured_by=authenticated.principal.key,
                version=1,
                created_at=now,
                updated_at=now,
            ),
            principal_key=authenticated.principal.key,
            expected_version=None,
        )
        return environment

    def configure_mapping(
        self,
        authenticated: AuthenticatedStudioRequest,
        *,
        project_id: str,
        environment_id: str,
        mappings: list[ReleaseResourceMapping],
        expected_version: int | None,
    ) -> EnvironmentMappingSet:
        self._require_releaser(authenticated, project_id)
        seen: set[str] = set()
        for mapping in mappings:
            if mapping.logical_ref in seen:
                raise ArtifactMappingMismatch(
                    f"Resource mapping is duplicated: {mapping.logical_ref}."
                )
            seen.add(mapping.logical_ref)
            if mapping.kind == "credential_availability":
                if mapping.target_ref != "available" or not mapping.available:
                    raise ArtifactMappingMismatch(
                        "Credential mappings contain availability only, never values."
                    )
            else:
                assert_secret_free({"target_ref": mapping.target_ref})
        now = utc_now()
        normalized = [
            item.model_dump(mode="json")
            for item in sorted(mappings, key=lambda value: (value.kind, value.logical_ref))
        ]
        current = self.store.get_environment_mapping(
            environment_id=environment_id,
            project_id=project_id,
            principal_key=authenticated.principal.key,
        )
        return self.store.upsert_environment_mapping(
            item=EnvironmentMappingSet(
                id=current.id if current is not None else new_id(),
                project_id=project_id,
                environment_id=environment_id,
                mappings=[ReleaseResourceMapping.model_validate(item) for item in normalized],
                mapping_hash=canonical_hash(normalized),
                configured_by=authenticated.principal.key,
                version=current.version if current else 1,
                created_at=current.created_at if current else now,
                updated_at=now,
            ),
            principal_key=authenticated.principal.key,
            expected_version=expected_version,
        )

    def preview(
        self,
        authenticated: AuthenticatedStudioRequest,
        *,
        project_id: str,
        change_request_id: str,
        environment_id: str,
    ) -> ReleasePreview:
        return self._preview(
            authenticated,
            project_id=project_id,
            change_request_id=change_request_id,
            environment_id=environment_id,
            persisted_target_authorized=False,
        )

    def _preview(
        self,
        authenticated: AuthenticatedStudioRequest,
        *,
        project_id: str,
        change_request_id: str,
        environment_id: str,
        persisted_target_authorized: bool,
    ) -> ReleasePreview:
        detail = self.reviews.detail(
            authenticated,
            project_id=project_id,
            change_request_id=change_request_id,
        )
        environment = self.store.get_release_environment(
            environment_id,
            project_id=project_id,
            principal_key=authenticated.principal.key,
        )
        logical_app = self.store.get_logical_app(
            environment.logical_app_id,
            project_id=project_id,
            principal_key=authenticated.principal.key,
        )
        project, _ = self.store.get_project_for_principal(
            project_id,
            authenticated.principal.key,
        )
        if not persisted_target_authorized:
            self._assert_target_authorized(
                authenticated,
                project=project,
                target_app_ref=environment.target_app_ref,
            )
        mapping_set = self.store.get_environment_mapping(
            environment_id=environment.id,
            project_id=project_id,
            principal_key=authenticated.principal.key,
        )
        blockers: list[dict[str, str]] = []
        if detail.change_request.status != "approved":
            blockers.append(
                {"code": "REVIEW_NOT_APPROVED", "message": "Change Request is not approved."}
            )
        for reason in detail.stale_reasons:
            blockers.append({"code": "REVIEW_STALE", "message": reason})
        if not environment.enabled:
            blockers.append(
                {"code": "ENVIRONMENT_DISABLED", "message": "Release environment is disabled."}
            )
        if logical_app.app_mode != detail.artifact.payload.app_mode:
            blockers.append(
                {
                    "code": "APP_MODE_MISMATCH",
                    "message": "Artifact mode does not match the logical app.",
                }
            )
        mappings = mapping_set.mappings if mapping_set is not None else []
        mapping_hash = mapping_set.mapping_hash if mapping_set else canonical_hash([])
        proposed: WorkflowPlan | None = None
        try:
            proposed = materialize_artifact_plan(detail.artifact, mappings)
        except ArtifactMappingMismatch as exc:
            blockers.append({"code": exc.code, "message": str(exc)})
        target = self._capture_target(
            target_app_ref=environment.target_app_ref,
            app_mode=logical_app.app_mode,
            name=logical_app.name,
        )
        if not bool(target.compatibility.get("mutation_supported", True)):
            blockers.append(
                {
                    "code": "DIFY_VERSION_MUTATION_UNSUPPORTED",
                    "message": str(
                        target.compatibility.get("reason")
                        or "Target Dify compatibility is diagnostic-only."
                    ),
                }
            )
        target_hash = str(target.base_hash or "")
        drift = bool(
            environment.tracked_draft_hash
            and target_hash != environment.tracked_draft_hash
        )
        if drift:
            blockers.append(
                {
                    "code": "TARGET_DRIFT",
                    "message": "Target Dify Draft changed outside this Release history.",
                }
            )
        guard = {
            "risk": "unknown",
            "no_op": False,
            "issues": [],
        }
        if proposed is not None:
            try:
                prepared = self.safe_writer.prepare(
                    before=WorkflowPlan.model_validate(target.base_plan),
                    proposed=proposed,
                )
                guard = prepared.guard.to_dict()
            except CommitServiceError as exc:
                blockers.append({"code": exc.code, "message": str(exc)})
        policy_hash = canonical_hash(
            {
                "review_policy": detail.change_request.policy.model_dump(mode="json"),
                "review_binding_hash": detail.change_request.binding_hash,
                "scenario_binding_hash": detail.change_request.evidence_binding_hash,
                "environment_id": environment.id,
                "environment_version": environment.version,
                "classification": environment.classification,
                "compatibility": target.compatibility,
            }
        )
        base = {
            "change_request_id": detail.change_request.id,
            "artifact_id": detail.artifact.id,
            "artifact_hash": detail.artifact.content_hash,
            "environment_id": environment.id,
            "environment_version": environment.version,
            "mapping_hash": mapping_hash,
            "policy_hash": policy_hash,
            "target_hash": target_hash,
            "release_note": detail.change_request.release_note,
        }
        return ReleasePreview(
            change_request_id=detail.change_request.id,
            artifact_id=detail.artifact.id,
            environment_id=environment.id,
            mapping_hash=mapping_hash,
            policy_hash=policy_hash,
            target_hash=target_hash,
            tracked_hash=environment.tracked_draft_hash,
            target_drift=drift,
            deployed_base={
                "app_name": target.app_name,
                "app_mode": target.app_mode,
                "draft_hash": target_hash,
                "tracked_hash": environment.tracked_draft_hash,
            },
            proposed_artifact={
                "artifact_hash": detail.artifact.content_hash,
                "app_mode": detail.artifact.payload.app_mode,
                "resource_requirements": [
                    item.model_dump(mode="json")
                    for item in detail.artifact.payload.resource_requirements
                ],
                "plan_summary": {
                    "name": detail.artifact.payload.plan.get("name"),
                    "node_count": len(detail.artifact.payload.plan.get("nodes") or []),
                    "edge_count": len(detail.artifact.payload.plan.get("edges") or []),
                },
            },
            scenario_evidence=detail.artifact.payload.scenario_evidence,
            risk=guard,
            release_note=detail.change_request.release_note,
            compatibility=target.compatibility,
            blockers=blockers,
            preview_hash=canonical_hash(base),
        )

    def authorize(
        self,
        authenticated: AuthenticatedStudioRequest,
        *,
        project_id: str,
        change_request_id: str,
        environment_id: str,
        action: Literal["apply_draft", "publish"],
        confirmation: Literal["APPLY_DRAFT", "PUBLISH"],
        expires_in_seconds: int = 600,
    ) -> ReleaseAuthorization:
        self._require_releaser(authenticated, project_id)
        expected = "APPLY_DRAFT" if action == "apply_draft" else "PUBLISH"
        if confirmation != expected:
            raise ReleaseAuthorizationInvalid(
                f"{action} requires its own explicit confirmation."
            )
        preview = self.preview(
            authenticated,
            project_id=project_id,
            change_request_id=change_request_id,
            environment_id=environment_id,
        )
        if preview.blockers:
            raise ReleaseBlocked(
                "Release Preview is blocked: "
                + "; ".join(item["message"] for item in preview.blockers)
            )
        if action == "publish":
            successful_apply = next(
                (
                    item
                    for item in self.store.list_release_records(
                        project_id=project_id,
                        principal_key=authenticated.principal.key,
                        environment_id=environment_id,
                    )
                    if item.action == "apply_draft"
                    and item.artifact_id == preview.artifact_id
                    and item.outcome == "succeeded"
                    and item.after_hash == preview.target_hash
                ),
                None,
            )
            if successful_apply is None:
                raise ReleaseBlocked(
                    "Publish requires a successful exact Apply Draft and current Draft Hash."
                )
        now = utc_now()
        return self.store.create_release_authorization(
            authorization=ReleaseAuthorization(
                id=new_id(),
                project_id=project_id,
                change_request_id=change_request_id,
                artifact_id=preview.artifact_id,
                environment_id=environment_id,
                action=action,
                artifact_hash=self.reviews.detail(
                    authenticated,
                    project_id=project_id,
                    change_request_id=change_request_id,
                ).artifact.content_hash,
                mapping_hash=preview.mapping_hash,
                policy_hash=preview.policy_hash,
                target_hash=preview.target_hash,
                preview_hash=preview.preview_hash,
                authorized_by=authenticated.principal.key,
                status="pending",
                expires_at=now + timedelta(seconds=expires_in_seconds),
                created_at=now,
            ),
            principal_key=authenticated.principal.key,
        )

    def execute(
        self,
        authenticated: AuthenticatedStudioRequest,
        *,
        project_id: str,
        authorization_id: str,
        idempotency_key: str,
    ) -> ReleaseRecord:
        self._require_releaser(authenticated, project_id)
        authorization = self.store.get_release_authorization(
            authorization_id,
            project_id=project_id,
            principal_key=authenticated.principal.key,
        )
        with self._environment_lock(authorization.environment_id):
            return self._execute_locked(
                authenticated,
                project_id=project_id,
                authorization=authorization,
                idempotency_key=idempotency_key,
            )

    def _execute_locked(
        self,
        authenticated: AuthenticatedStudioRequest,
        *,
        project_id: str,
        authorization: ReleaseAuthorization,
        idempotency_key: str,
        resume_record: ReleaseRecord | None = None,
    ) -> ReleaseRecord:
        if authorization.authorized_by != authenticated.principal.key:
            raise ReleaseAuthorizationInvalid(
                "Release authorization cannot be transferred to another Principal."
            )
        if resume_record is not None:
            if (
                resume_record.authorization_id != authorization.id
                or resume_record.actor_key != authenticated.principal.key
                or resume_record.idempotency_key != idempotency_key
            ):
                raise ReleaseAuthorizationInvalid(
                    "Durable Release work does not match its persisted authorization."
                )
            if resume_record.outcome != "intent_recorded":
                return resume_record
            if authorization.status != "consumed":
                raise ReleaseAuthorizationInvalid(
                    "Durable Release requires a consumed exact human authorization."
                )
        elif authorization.status == "consumed":
            previous = next(
                (
                    item
                    for item in self.store.list_release_records(
                        project_id=project_id,
                        principal_key=authenticated.principal.key,
                        environment_id=authorization.environment_id,
                    )
                    if item.authorization_id == authorization.id
                    and item.idempotency_key == idempotency_key
                ),
                None,
            )
            if previous is not None:
                return previous
        if resume_record is None and (
            authorization.status != "pending" or authorization.expires_at <= utc_now()
        ):
            raise ReleaseAuthorizationInvalid("Release authorization is not pending and current.")
        preview = self._preview(
            authenticated,
            project_id=project_id,
            change_request_id=authorization.change_request_id,
            environment_id=authorization.environment_id,
            persisted_target_authorized=resume_record is not None,
        )
        exact = (
            preview.artifact_id == authorization.artifact_id
            and preview.mapping_hash == authorization.mapping_hash
            and preview.policy_hash == authorization.policy_hash
            and preview.target_hash == authorization.target_hash
            and preview.preview_hash == authorization.preview_hash
        )
        if not exact or preview.blockers:
            raise ReleaseAuthorizationInvalid(
                "Artifact, Environment, Mapping, Policy, evidence, or target Hash changed."
            )
        detail = self.reviews.detail(
            authenticated,
            project_id=project_id,
            change_request_id=authorization.change_request_id,
        )
        environment = self.store.get_release_environment(
            authorization.environment_id,
            project_id=project_id,
            principal_key=authenticated.principal.key,
        )
        mapping_set = self.store.get_environment_mapping(
            environment_id=environment.id,
            project_id=project_id,
            principal_key=authenticated.principal.key,
        )
        mappings = mapping_set.mappings if mapping_set else []
        proposed = materialize_artifact_plan(detail.artifact, mappings)
        target = self._capture_target(
            target_app_ref=environment.target_app_ref,
            app_mode=detail.artifact.payload.app_mode,
            name=detail.change_request.title,
        )
        preparation = self.safe_writer.prepare(
            before=WorkflowPlan.model_validate(target.base_plan),
            proposed=proposed,
        )
        if resume_record is None:
            now = utc_now()
            record, created = self.store.create_release_intent(
                record=ReleaseRecord(
                    id=new_id(),
                    project_id=project_id,
                    change_request_id=detail.change_request.id,
                    artifact_id=detail.artifact.id,
                    environment_id=environment.id,
                    authorization_id=authorization.id,
                    action=authorization.action,
                    idempotency_key=idempotency_key,
                    outcome="intent_recorded",
                    actor_key=authenticated.principal.key,
                    before_hash=authorization.target_hash,
                    release_note=detail.change_request.release_note,
                    details={"external_write_started": False},
                    created_at=now,
                ),
                principal_key=authenticated.principal.key,
            )
            if not created:
                return record
            self.store.record_receipt(
                project_id=project_id,
                principal_key=authenticated.principal.key,
                operation=f"release.{authorization.action}",
                idempotency_key=idempotency_key,
                outcome="pending",
                external_ref=environment.target_app_ref,
                details={
                    "record_id": record.id,
                    "authorization_id": authorization.id,
                    "artifact_hash": detail.artifact.content_hash,
                    "target_hash": authorization.target_hash,
                },
            )
            try:
                self.store.consume_release_authorization(
                    authorization_id=authorization.id,
                    project_id=project_id,
                    principal_key=authenticated.principal.key,
                )
            except StudioConflict as exc:
                return self._finish_non_success(
                    authenticated,
                    project_id=project_id,
                    record=record,
                    authorization=authorization,
                    idempotency_key=idempotency_key,
                    outcome="failed",
                    message=str(exc),
                )
            if self.durable_jobs:
                self.store.enqueue_job(
                    project_id=project_id,
                    principal_key=authenticated.principal.key,
                    kind="release.execute",
                    payload={
                        "record_id": record.id,
                        "authorization_id": authorization.id,
                        "authorized_by": authenticated.principal.key,
                        "action": authorization.action,
                        "human_gate_consumed": True,
                    },
                    idempotency_key=f"release-execute:{record.id}",
                    max_attempts=1,
                )
                return record
        else:
            record = resume_record
        try:
            scenario_evidence = detail.artifact.payload.scenario_evidence
            release_evidence = {
                "artifact_hash": detail.artifact.content_hash,
                "review_binding_hash": detail.change_request.binding_hash,
                "scenario_binding_hash": detail.change_request.evidence_binding_hash,
                "scenario": {
                    "pass_rate": scenario_evidence.get("pass_rate"),
                    "quality_score": scenario_evidence.get("quality_score"),
                    "cleanup_verified": scenario_evidence.get("cleanup_verified"),
                },
                "environment_name": environment.name,
            }
            if authorization.action == "apply_draft":
                written = self.safe_writer.apply(
                    app_id=environment.target_app_ref,
                    expected_hash=authorization.target_hash,
                    preparation=preparation,
                )
                readback = self._capture_target(
                    target_app_ref=environment.target_app_ref,
                    app_mode=detail.artifact.payload.app_mode,
                    name=detail.change_request.title,
                )
                if readback.base_hash != written.after_hash:
                    raise ReleaseReconciliationRequired(
                        "Apply returned but authoritative Dify readback does not match."
                    )
                external_ref = environment.target_app_ref
                details = {
                    **release_evidence,
                    "write_performed": written.write_performed,
                    "sync": written.sync,
                    "readback_hash": readback.base_hash,
                }
                after_hash = written.after_hash
            else:
                with self.client_factory() as client:
                    published = client.publish_workflow(
                        environment.target_app_ref,
                        marked_name=f"Studio {detail.artifact.content_hash[:12]}",
                        marked_comment=detail.change_request.release_note,
                    )
                    published_workflow = client.get_published_workflow(
                        environment.target_app_ref
                    )
                if published_workflow is None:
                    raise ReleaseReconciliationRequired(
                        "Publish returned but Dify did not expose a published Workflow identity."
                    )
                readback = self._capture_target(
                    target_app_ref=environment.target_app_ref,
                    app_mode=detail.artifact.payload.app_mode,
                    name=detail.change_request.title,
                )
                if readback.base_hash != authorization.target_hash:
                    raise ReleaseReconciliationRequired(
                        "Publish returned but current Draft Hash changed; reconcile manually."
                    )
                published_data = (
                    published.__dict__
                    if hasattr(published, "__dict__")
                    else {"value": str(published)}
                )
                published_identity = (
                    published_workflow.__dict__
                    if hasattr(published_workflow, "__dict__")
                    else {"value": str(published_workflow)}
                )
                assert_secret_free(published_data)
                assert_secret_free(published_identity)
                external_ref = str(published_identity.get("id") or "")
                if not external_ref or not published_identity.get("version"):
                    raise ReleaseReconciliationRequired(
                        "Published Workflow identity is incomplete; reconcile manually."
                    )
                details = {
                    **release_evidence,
                    "publish": published_data,
                    "published_workflow": published_identity,
                    "readback_hash": readback.base_hash,
                }
                after_hash = authorization.target_hash
            if authorization.action == "apply_draft":
                self.store.update_environment_tracked_hash(
                    project_id=project_id,
                    principal_key=authenticated.principal.key,
                    environment_id=environment.id,
                    tracked_hash=after_hash,
                    expected_version=environment.version,
                )
            receipt = self.store.record_receipt(
                project_id=project_id,
                principal_key=authenticated.principal.key,
                operation=f"release.{authorization.action}",
                idempotency_key=idempotency_key,
                outcome="succeeded",
                external_ref=external_ref,
                details=details,
            )
            completed = self.store.finish_release_record(
                project_id=project_id,
                principal_key=authenticated.principal.key,
                record_id=record.id,
                outcome="succeeded",
                after_hash=after_hash,
                receipt_id=receipt.id,
                external_ref=external_ref,
                details=details,
            )
            self.store.append_activity(
                project_id=project_id,
                principal_key=authenticated.principal.key,
                kind=f"release.{authorization.action}.succeeded",
                entity_type="release",
                entity_id=completed.id,
                summary={
                    "artifact_hash": detail.artifact.content_hash,
                    "environment_id": environment.id,
                    "after_hash": after_hash,
                },
            )
            return completed
        except SafeDraftHashConflict as exc:
            return self._finish_non_success(
                authenticated,
                project_id=project_id,
                record=record,
                authorization=authorization,
                idempotency_key=idempotency_key,
                outcome="conflicted",
                message=str(exc),
                current_hash=exc.current_hash,
            )
        except DifyConflictError as exc:
            return self._finish_non_success(
                authenticated,
                project_id=project_id,
                record=record,
                authorization=authorization,
                idempotency_key=idempotency_key,
                outcome="conflicted",
                message=str(exc),
            )
        except CommitServiceError as exc:
            return self._finish_non_success(
                authenticated,
                project_id=project_id,
                record=record,
                authorization=authorization,
                idempotency_key=idempotency_key,
                outcome="failed",
                message=str(exc),
            )
        except Exception as exc:
            return self._finish_non_success(
                authenticated,
                project_id=project_id,
                record=record,
                authorization=authorization,
                idempotency_key=idempotency_key,
                outcome="ambiguous",
                message=(
                    "External outcome is ambiguous; do not retry automatically "
                    f"({exc.__class__.__name__})."
                ),
            )

    def execute_durable(
        self,
        authenticated: AuthenticatedStudioRequest,
        *,
        project_id: str,
        record_id: str,
    ) -> ReleaseRecord:
        self._require_releaser(authenticated, project_id)
        record = self.store.get_release_record(
            record_id,
            project_id=project_id,
            principal_key=authenticated.principal.key,
        )
        authorization = self.store.get_release_authorization(
            record.authorization_id,
            project_id=project_id,
            principal_key=authenticated.principal.key,
        )
        with self._environment_lock(record.environment_id):
            return self._execute_locked(
                authenticated,
                project_id=project_id,
                authorization=authorization,
                idempotency_key=record.idempotency_key,
                resume_record=record,
            )

    def cancel_durable(
        self,
        authenticated: AuthenticatedStudioRequest,
        *,
        project_id: str,
        record_id: str,
    ) -> ReleaseRecord:
        self._require_releaser(authenticated, project_id)
        record = self.store.get_release_record(
            record_id,
            project_id=project_id,
            principal_key=authenticated.principal.key,
        )
        if record.outcome != "intent_recorded":
            return record
        authorization = self.store.get_release_authorization(
            record.authorization_id,
            project_id=project_id,
            principal_key=authenticated.principal.key,
        )
        return self._finish_non_success(
            authenticated,
            project_id=project_id,
            record=record,
            authorization=authorization,
            idempotency_key=record.idempotency_key,
            outcome="failed",
            message="Release delivery was cancelled before the external write started.",
        )

    def _finish_non_success(
        self,
        authenticated: AuthenticatedStudioRequest,
        *,
        project_id: str,
        record: ReleaseRecord,
        authorization: ReleaseAuthorization,
        idempotency_key: str,
        outcome: Literal["failed", "ambiguous", "conflicted"],
        message: str,
        current_hash: str | None = None,
    ) -> ReleaseRecord:
        details = {
            "message": message,
            "current_hash": current_hash,
            "automatic_retry": False,
        }
        receipt = self.store.record_receipt(
            project_id=project_id,
            principal_key=authenticated.principal.key,
            operation=f"release.{authorization.action}",
            idempotency_key=idempotency_key,
            outcome="failed" if outcome == "conflicted" else outcome,
            external_ref=None,
            details=details,
        )
        return self.store.finish_release_record(
            project_id=project_id,
            principal_key=authenticated.principal.key,
            record_id=record.id,
            outcome=outcome,
            after_hash=current_hash,
            receipt_id=receipt.id,
            external_ref=None,
            details=details,
        )

    def _capture_target(
        self,
        *,
        target_app_ref: str,
        app_mode: str,
        name: str,
    ):
        try:
            snapshot = self.snapshot.capture(
                AgentSession(
                    operation="modify",
                    app_id=target_app_ref,
                    app_mode=app_mode,  # type: ignore[arg-type]
                    app_name=name,
                )
            )
        except WorkflowSnapshotError as exc:
            raise ReleaseBlocked(str(exc)) from exc
        if not snapshot.base_hash:
            raise ReleaseBlocked(
                "The target Dify Draft did not return an authoritative Hash."
            )
        return snapshot

    def _require_releaser(
        self,
        authenticated: AuthenticatedStudioRequest,
        project_id: str,
    ) -> Project:
        project, membership = self.store.get_project_for_principal(
            project_id,
            authenticated.principal.key,
        )
        if membership.role not in {"owner", "admin"}:
            raise StudioAccessDenied(
                "Only a project Owner or Admin can configure or execute releases."
            )
        return project

    def _assert_target_authorized(
        self,
        authenticated: AuthenticatedStudioRequest,
        *,
        project: Project,
        target_app_ref: str,
    ) -> DifyAppSummary:
        if not authenticated.host.apps_available:
            raise ReleaseBlocked(
                "The signed-in Dify app list is unavailable; target authorization cannot be verified."
            )
        visible_app = next(
            (item for item in authenticated.host.apps if item.id == target_app_ref),
            None,
        )
        if visible_app is None:
            raise StudioAccessDenied(
                "The target Dify app is not readable by the verified Dify account."
            )
        if (
            project.kind != "personal"
            and target_app_ref
            not in self.store.list_project_app_ids(
                project.id,
                authenticated.principal.key,
            )
        ):
            raise StudioAccessDenied(
                "The target Dify app is not linked to this team project."
            )
        return visible_app

    def _environment_lock(self, environment_id: str) -> Lock:
        with self._locks_guard:
            return self._environment_locks.setdefault(environment_id, Lock())
