from __future__ import annotations

from datetime import timedelta
from typing import Literal

from app.agent.store import AgentStore
from app.studio.artifacts import (
    ArtifactCanonicalMismatch,
    artifact_from_canonical_json,
    artifact_git_files,
    build_workflow_artifact,
    canonical_hash,
)
from app.studio.build import StudioBuildService
from app.studio.identity import AuthenticatedStudioRequest
from app.studio.models import (
    ChangeRequest,
    ChangeRequestDetail,
    GitArtifactBundle,
    ReviewEvent,
    ReviewPolicy,
    WorkflowArtifact,
    new_id,
    utc_now,
)
from app.studio.scenarios import ScenarioStaleEvidence, StudioScenarioService
from app.studio.store import (
    StudioAccessDenied,
    StudioConflict,
    StudioRecordNotFound,
    StudioStore,
)


class ReviewError(RuntimeError):
    code = "STUDIO_REVIEW_ERROR"


class ReviewStale(ReviewError):
    code = "STUDIO_REVIEW_STALE"


class ReviewExpired(ReviewError):
    code = "STUDIO_REVIEW_EXPIRED"


class ReviewSelfApprovalDenied(ReviewError):
    code = "STUDIO_REVIEW_SELF_APPROVAL_DENIED"


class GitArtifactConflict(ReviewError):
    code = "STUDIO_GIT_CONTENT_CONFLICT"


class StudioReviewService:
    def __init__(
        self,
        *,
        store: StudioStore,
        build_service: StudioBuildService,
        scenario_service: StudioScenarioService,
        agent_store: AgentStore,
    ) -> None:
        self.store = store
        self.build_service = build_service
        self.scenario_service = scenario_service
        self.agent_store = agent_store

    def list(
        self,
        authenticated: AuthenticatedStudioRequest,
        *,
        project_id: str,
    ) -> list[ChangeRequest]:
        self.store.get_project_for_principal(project_id, authenticated.principal.key)
        items = self.store.list_change_requests(
            project_id=project_id,
            principal_key=authenticated.principal.key,
        )
        return [self._expire_if_needed(authenticated, item) for item in items]

    def detail(
        self,
        authenticated: AuthenticatedStudioRequest,
        *,
        project_id: str,
        change_request_id: str,
    ) -> ChangeRequestDetail:
        item = self._get_current(authenticated, project_id, change_request_id)
        artifact = self.store.get_workflow_artifact(
            item.artifact_id,
            project_id=project_id,
            principal_key=authenticated.principal.key,
        )
        events = self.store.list_review_events(
            project_id=project_id,
            principal_key=authenticated.principal.key,
            change_request_id=item.id,
        )
        _, membership = self.store.get_project_for_principal(
            project_id,
            authenticated.principal.key,
        )
        role = membership.role
        can_comment = role in {"owner", "admin", "builder", "reviewer"}
        can_decide = self._can_decide(authenticated, item)
        return ChangeRequestDetail(
            change_request=item,
            artifact=artifact,
            events=events,
            can_comment=can_comment,
            can_decide=can_decide,
            can_release=role in {"owner", "admin"} and item.status == "approved",
            stale_reasons=self._stale_reasons(authenticated, item),
        )

    def create(
        self,
        authenticated: AuthenticatedStudioRequest,
        *,
        project_id: str,
        build_id: str,
        candidate_id: str,
        scenario_run_id: str,
        title: str,
        release_note: str,
        assignee_key: str | None,
        require_separation: bool,
        expires_in_seconds: int,
        repair_proposal_id: str | None = None,
        repair_proposal_version: int | None = None,
    ) -> ChangeRequestDetail:
        self._require_author(authenticated, project_id)
        if (
            require_separation
            and assignee_key is not None
            and assignee_key == authenticated.principal.key
        ):
            raise ReviewSelfApprovalDenied(
                "Separation of duties requires a Reviewer other than the Author."
            )
        if assignee_key is not None:
            self._assert_reviewer_member(
                project_id=project_id,
                actor_key=authenticated.principal.key,
                assignee_key=assignee_key,
            )
        artifact, report_binding_hash = self._artifact_for_candidate(
            authenticated,
            project_id=project_id,
            build_id=build_id,
            candidate_id=candidate_id,
            scenario_run_id=scenario_run_id,
        )
        artifact = self.store.create_workflow_artifact(
            artifact=artifact,
            principal_key=authenticated.principal.key,
        )
        now = utc_now()
        expires_at = now + timedelta(seconds=expires_in_seconds)
        policy = ReviewPolicy(
            require_author_approver_separation=require_separation,
        )
        binding_hash = _review_binding_hash(
            artifact=artifact,
            evidence_binding_hash=report_binding_hash,
            policy=policy,
            expires_at=expires_at,
        )
        item = ChangeRequest(
            id=new_id(),
            project_id=project_id,
            build_id=build_id,
            candidate_id=candidate_id,
            scenario_run_id=scenario_run_id,
            artifact_id=artifact.id,
            artifact_hash=artifact.content_hash,
            title=title.strip(),
            release_note=release_note.strip(),
            author_key=authenticated.principal.key,
            assignee_key=assignee_key,
            status="in_review",
            policy=policy,
            evidence_binding_hash=report_binding_hash,
            binding_hash=binding_hash,
            expires_at=expires_at,
            version=1,
            created_at=now,
            updated_at=now,
        )
        event = ReviewEvent(
            id=new_id(),
            project_id=project_id,
            change_request_id=item.id,
            kind="created",
            actor_key=authenticated.principal.key,
            body="Change Request created from exact tested Candidate evidence.",
            assignee_key=assignee_key,
            binding_hash=binding_hash,
            created_at=now,
        )
        self.store.create_change_request(
            change_request=item,
            initial_event=event,
            principal_key=authenticated.principal.key,
            repair_proposal_id=repair_proposal_id,
            repair_proposal_version=repair_proposal_version,
        )
        return self.detail(
            authenticated,
            project_id=project_id,
            change_request_id=item.id,
        )

    def comment(
        self,
        authenticated: AuthenticatedStudioRequest,
        *,
        project_id: str,
        change_request_id: str,
        body: str,
    ) -> ChangeRequestDetail:
        item = self._get_current(authenticated, project_id, change_request_id)
        _, membership = self.store.get_project_for_principal(
            project_id,
            authenticated.principal.key,
        )
        if membership.role not in {
            "owner",
            "admin",
            "builder",
            "reviewer",
        }:
            raise StudioAccessDenied("Your project role cannot comment on reviews.")
        self.store.append_review_event(
            event=ReviewEvent(
                id=new_id(),
                project_id=project_id,
                change_request_id=item.id,
                kind="commented",
                actor_key=authenticated.principal.key,
                body=body.strip(),
                binding_hash=item.binding_hash,
                created_at=utc_now(),
            ),
            principal_key=authenticated.principal.key,
        )
        return self.detail(
            authenticated,
            project_id=project_id,
            change_request_id=item.id,
        )

    def assign(
        self,
        authenticated: AuthenticatedStudioRequest,
        *,
        project_id: str,
        change_request_id: str,
        assignee_key: str,
        expected_version: int,
    ) -> ChangeRequestDetail:
        item = self._get_current(authenticated, project_id, change_request_id)
        _, membership = self.store.get_project_for_principal(
            project_id,
            authenticated.principal.key,
        )
        if (
            authenticated.principal.key != item.author_key
            and membership.role not in {"owner", "admin"}
        ):
            raise StudioAccessDenied("Only the Author, Owner, or Admin can assign review.")
        self._assert_reviewer_member(
            project_id=project_id,
            actor_key=authenticated.principal.key,
            assignee_key=assignee_key,
        )
        if (
            item.policy.require_author_approver_separation
            and assignee_key == item.author_key
        ):
            raise ReviewSelfApprovalDenied(
                "Separation of duties requires a Reviewer other than the Author."
            )
        now = utc_now()
        self.store.assign_change_request(
            project_id=project_id,
            principal_key=authenticated.principal.key,
            change_request_id=item.id,
            assignee_key=assignee_key,
            expected_version=expected_version,
            event=ReviewEvent(
                id=new_id(),
                project_id=project_id,
                change_request_id=item.id,
                kind="assigned",
                actor_key=authenticated.principal.key,
                body="Review assignment changed.",
                assignee_key=assignee_key,
                binding_hash=item.binding_hash,
                created_at=now,
            ),
        )
        return self.detail(
            authenticated,
            project_id=project_id,
            change_request_id=item.id,
        )

    def decide(
        self,
        authenticated: AuthenticatedStudioRequest,
        *,
        project_id: str,
        change_request_id: str,
        decision: Literal["request_changes", "approve", "reject"],
        body: str,
        expected_version: int,
        expected_binding_hash: str,
    ) -> ChangeRequestDetail:
        item = self._get_current(authenticated, project_id, change_request_id)
        if expected_binding_hash != item.binding_hash:
            raise ReviewStale("The review binding changed; reload before deciding.")
        if not self._can_decide(authenticated, item):
            raise StudioAccessDenied("Your project role or assignment cannot decide this review.")
        if (
            decision == "approve"
            and item.policy.require_author_approver_separation
            and item.author_key == authenticated.principal.key
        ):
            raise ReviewSelfApprovalDenied(
                "This Change Request requires an approver other than the Author."
            )
        if decision == "approve":
            stale = self._stale_reasons(authenticated, item)
            if stale:
                raise ReviewStale("Approval is blocked: " + "; ".join(stale))
        status = {
            "request_changes": "changes_requested",
            "approve": "approved",
            "reject": "rejected",
        }[decision]
        kind = {
            "request_changes": "changes_requested",
            "approve": "approved",
            "reject": "rejected",
        }[decision]
        self.store.decide_change_request(
            project_id=project_id,
            principal_key=authenticated.principal.key,
            change_request_id=item.id,
            expected_version=expected_version,
            expected_binding_hash=expected_binding_hash,
            status=status,
            event=ReviewEvent(
                id=new_id(),
                project_id=project_id,
                change_request_id=item.id,
                kind=kind,  # type: ignore[arg-type]
                actor_key=authenticated.principal.key,
                body=body.strip(),
                binding_hash=item.binding_hash,
                created_at=utc_now(),
            ),
        )
        return self.detail(
            authenticated,
            project_id=project_id,
            change_request_id=item.id,
        )

    def supersede(
        self,
        authenticated: AuthenticatedStudioRequest,
        *,
        project_id: str,
        change_request_id: str,
        expected_version: int,
        build_id: str,
        candidate_id: str,
        scenario_run_id: str,
        title: str,
        release_note: str,
        expires_in_seconds: int,
    ) -> ChangeRequestDetail:
        old = self._get_current(authenticated, project_id, change_request_id)
        if (
            authenticated.principal.key != old.author_key
            and self.store.get_project_for_principal(
                project_id,
                authenticated.principal.key,
            )[1].role
            not in {"owner", "admin"}
        ):
            raise StudioAccessDenied(
                "Only the Author, Owner, or Admin can supersede this review."
            )
        artifact, evidence_hash = self._artifact_for_candidate(
            authenticated,
            project_id=project_id,
            build_id=build_id,
            candidate_id=candidate_id,
            scenario_run_id=scenario_run_id,
        )
        artifact = self.store.create_workflow_artifact(
            artifact=artifact,
            principal_key=authenticated.principal.key,
        )
        now = utc_now()
        expires_at = now + timedelta(seconds=expires_in_seconds)
        binding_hash = _review_binding_hash(
            artifact=artifact,
            evidence_binding_hash=evidence_hash,
            policy=old.policy,
            expires_at=expires_at,
        )
        replacement = ChangeRequest(
            id=new_id(),
            project_id=project_id,
            build_id=build_id,
            candidate_id=candidate_id,
            scenario_run_id=scenario_run_id,
            artifact_id=artifact.id,
            artifact_hash=artifact.content_hash,
            title=title.strip(),
            release_note=release_note.strip(),
            author_key=authenticated.principal.key,
            assignee_key=old.assignee_key,
            status="in_review",
            policy=old.policy,
            evidence_binding_hash=evidence_hash,
            binding_hash=binding_hash,
            supersedes_id=old.id,
            expires_at=expires_at,
            version=1,
            created_at=now,
            updated_at=now,
        )
        self.store.supersede_change_request(
            project_id=project_id,
            principal_key=authenticated.principal.key,
            old_request_id=old.id,
            new_request=replacement,
            expected_old_version=expected_version,
            old_event=ReviewEvent(
                id=new_id(),
                project_id=project_id,
                change_request_id=old.id,
                kind="superseded",
                actor_key=authenticated.principal.key,
                body="A corrected tested Candidate superseded this proposal.",
                binding_hash=old.binding_hash,
                created_at=now,
            ),
            new_event=ReviewEvent(
                id=new_id(),
                project_id=project_id,
                change_request_id=replacement.id,
                kind="created",
                actor_key=authenticated.principal.key,
                body="Corrected proposal created; earlier decisions were not carried forward.",
                assignee_key=replacement.assignee_key,
                binding_hash=replacement.binding_hash,
                created_at=now,
            ),
        )
        return self.detail(
            authenticated,
            project_id=project_id,
            change_request_id=replacement.id,
        )

    def propose_rollback(
        self,
        authenticated: AuthenticatedStudioRequest,
        *,
        project_id: str,
        artifact_id: str,
        title: str,
        release_note: str,
        assignee_key: str | None,
        require_separation: bool,
        expires_in_seconds: int,
    ) -> ChangeRequestDetail:
        self._require_author(authenticated, project_id)
        if assignee_key:
            self._assert_reviewer_member(
                project_id=project_id,
                actor_key=authenticated.principal.key,
                assignee_key=assignee_key,
            )
        artifact = self.store.get_workflow_artifact(
            artifact_id,
            project_id=project_id,
            principal_key=authenticated.principal.key,
        )
        evidence_hash = str(
            artifact.payload.scenario_evidence.get("binding", {}).get("binding_hash")
            or ""
        )
        if len(evidence_hash) != 64:
            raise ReviewStale("Rollback Artifact has no exact Scenario evidence binding.")
        now = utc_now()
        expires_at = now + timedelta(seconds=expires_in_seconds)
        policy = ReviewPolicy(
            require_author_approver_separation=require_separation,
        )
        binding_hash = _review_binding_hash(
            artifact=artifact,
            evidence_binding_hash=evidence_hash,
            policy=policy,
            expires_at=expires_at,
        )
        item = ChangeRequest(
            id=new_id(),
            project_id=project_id,
            build_id=None,
            candidate_id=artifact.candidate_id,
            scenario_run_id=str(
                artifact.payload.scenario_evidence.get("scenario_run_id") or ""
            )
            or None,
            artifact_id=artifact.id,
            artifact_hash=artifact.content_hash,
            title=title.strip(),
            release_note=release_note.strip(),
            author_key=authenticated.principal.key,
            assignee_key=assignee_key,
            status="in_review",
            policy=policy,
            evidence_binding_hash=evidence_hash,
            binding_hash=binding_hash,
            expires_at=expires_at,
            version=1,
            created_at=now,
            updated_at=now,
        )
        self.store.create_change_request(
            change_request=item,
            initial_event=ReviewEvent(
                id=new_id(),
                project_id=project_id,
                change_request_id=item.id,
                kind="rollback_proposed",
                actor_key=authenticated.principal.key,
                body="Rollback proposed as a new reviewed release; no overwrite occurred.",
                assignee_key=assignee_key,
                binding_hash=binding_hash,
                created_at=now,
            ),
            principal_key=authenticated.principal.key,
        )
        return self.detail(
            authenticated,
            project_id=project_id,
            change_request_id=item.id,
        )

    def git_bundle(
        self,
        authenticated: AuthenticatedStudioRequest,
        *,
        project_id: str,
        artifact_id: str,
    ) -> GitArtifactBundle:
        artifact = self.store.get_workflow_artifact(
            artifact_id,
            project_id=project_id,
            principal_key=authenticated.principal.key,
        )
        return GitArtifactBundle(
            artifact_id=artifact.id,
            content_hash=artifact.content_hash,
            files=artifact_git_files(artifact),
        )

    def git_pull(
        self,
        authenticated: AuthenticatedStudioRequest,
        *,
        project_id: str,
        base_artifact_id: str,
        expected_base_hash: str,
        canonical: str,
        content_hash: str,
        title: str,
        release_note: str,
        assignee_key: str | None,
        expires_in_seconds: int,
    ) -> ChangeRequestDetail:
        self._require_author(authenticated, project_id)
        base = self.store.get_workflow_artifact(
            base_artifact_id,
            project_id=project_id,
            principal_key=authenticated.principal.key,
        )
        if base.content_hash != expected_base_hash:
            raise GitArtifactConflict("Git base Artifact changed; resolve the conflict explicitly.")
        try:
            pulled = artifact_from_canonical_json(
                canonical=canonical,
                expected_hash=content_hash,
            )
        except ArtifactCanonicalMismatch as exc:
            raise GitArtifactConflict(str(exc)) from exc
        if pulled != base.payload or content_hash != base.content_hash:
            raise GitArtifactConflict(
                "Pulled Artifact differs from the tested Candidate; create a typed Candidate and rerun Scenarios."
            )
        detail = self.propose_rollback(
            authenticated,
            project_id=project_id,
            artifact_id=base.id,
            title=title,
            release_note=release_note,
            assignee_key=assignee_key,
            require_separation=False,
            expires_in_seconds=expires_in_seconds,
        )
        self.store.append_review_event(
            event=ReviewEvent(
                id=new_id(),
                project_id=project_id,
                change_request_id=detail.change_request.id,
                kind="git_pull_created",
                actor_key=authenticated.principal.key,
                body="Explicit Git pull re-entered governance as a Change Request.",
                binding_hash=detail.change_request.binding_hash,
                created_at=utc_now(),
            ),
            principal_key=authenticated.principal.key,
        )
        return self.detail(
            authenticated,
            project_id=project_id,
            change_request_id=detail.change_request.id,
        )

    def _artifact_for_candidate(
        self,
        authenticated: AuthenticatedStudioRequest,
        *,
        project_id: str,
        build_id: str,
        candidate_id: str,
        scenario_run_id: str,
    ) -> tuple[WorkflowArtifact, str]:
        scenario_run = self.store.get_scenario_run(
            scenario_run_id,
            project_id=project_id,
            principal_key=authenticated.principal.key,
        )
        if scenario_run.build_id != build_id:
            raise ReviewStale("Scenario evidence belongs to another Build.")
        if scenario_run.status != "completed" or not scenario_run.cleanup_verified:
            raise ReviewStale("Scenario evidence is not complete or cleanup-verified.")
        report = next(
            (item for item in scenario_run.reports if item.candidate_id == candidate_id),
            None,
        )
        if report is None or not report.cleanup_verified:
            raise ReviewStale("The tested Candidate report is unavailable or not cleanup-verified.")
        self.scenario_service.assert_evidence_current(
            authenticated,
            project_id=project_id,
            binding=report.binding,
        )
        candidate, head_id, plan = self.scenario_service.candidate_plan(
            authenticated,
            project_id=project_id,
            build_id=build_id,
            candidate_id=candidate_id,
        )
        run = self.agent_store.get_run(candidate.run_id)
        return (
            build_workflow_artifact(
                project_id=project_id,
                candidate_id=candidate_id,
                workspace_version_id=head_id,
                source_base_hash=run.base_hash,
                plan=plan,
                run=run,
                scenario_run_id=scenario_run_id,
                report=report,
                created_by=authenticated.principal.key,
            ),
            report.binding.binding_hash,
        )

    def _stale_reasons(
        self,
        authenticated: AuthenticatedStudioRequest,
        item: ChangeRequest,
    ) -> list[str]:
        reasons: list[str] = []
        if item.expires_at <= utc_now():
            reasons.append("Change Request expired")
        try:
            artifact = self.store.get_workflow_artifact(
                item.artifact_id,
                project_id=item.project_id,
                principal_key=authenticated.principal.key,
            )
        except StudioRecordNotFound:
            return [*reasons, "Artifact is unavailable"]
        if artifact.content_hash != item.artifact_hash:
            reasons.append("Artifact Hash changed")
        binding_data = artifact.payload.scenario_evidence.get("binding")
        if not isinstance(binding_data, dict):
            reasons.append("Scenario binding is unavailable")
            return reasons
        try:
            from app.studio.models import ScenarioEvidenceBinding

            binding = ScenarioEvidenceBinding.model_validate(binding_data)
            if binding.binding_hash != item.evidence_binding_hash:
                reasons.append("Scenario binding changed")
            self.scenario_service.assert_evidence_current(
                authenticated,
                project_id=item.project_id,
                binding=binding,
            )
        except (ScenarioStaleEvidence, ValueError) as exc:
            reasons.append(str(exc))
        return reasons

    def _get_current(
        self,
        authenticated: AuthenticatedStudioRequest,
        project_id: str,
        change_request_id: str,
    ) -> ChangeRequest:
        item = self.store.get_change_request(
            change_request_id,
            project_id=project_id,
            principal_key=authenticated.principal.key,
        )
        return self._expire_if_needed(authenticated, item)

    def _expire_if_needed(
        self,
        authenticated: AuthenticatedStudioRequest,
        item: ChangeRequest,
    ) -> ChangeRequest:
        if item.status not in {"in_review", "changes_requested"} or item.expires_at > utc_now():
            return item
        return self.store.expire_change_request(
            project_id=item.project_id,
            principal_key=authenticated.principal.key,
            change_request_id=item.id,
            expected_version=item.version,
            event=ReviewEvent(
                id=new_id(),
                project_id=item.project_id,
                change_request_id=item.id,
                kind="expired",
                actor_key="system:expiry",
                body="Change Request expired; no decision was carried forward.",
                binding_hash=item.binding_hash,
                created_at=utc_now(),
            ),
        )

    def _require_author(
        self,
        authenticated: AuthenticatedStudioRequest,
        project_id: str,
    ) -> None:
        _, membership = self.store.get_project_for_principal(
            project_id,
            authenticated.principal.key,
        )
        if membership.role not in {"owner", "admin", "builder"}:
            raise StudioAccessDenied("Your project role cannot author Change Requests.")

    def _assert_reviewer_member(
        self,
        *,
        project_id: str,
        actor_key: str,
        assignee_key: str,
    ) -> None:
        membership = self.store.get_membership(
            project_id=project_id,
            actor_key=actor_key,
            principal_key=assignee_key,
        )
        if membership.role not in {"owner", "admin", "reviewer"}:
            raise StudioAccessDenied("Review assignment requires a Reviewer, Admin, or Owner.")

    def _can_decide(
        self,
        authenticated: AuthenticatedStudioRequest,
        item: ChangeRequest,
    ) -> bool:
        if item.status not in {"in_review", "changes_requested"}:
            return False
        role = self.store.get_project_for_principal(
            item.project_id,
            authenticated.principal.key,
        )[1].role
        if role not in {"owner", "admin", "reviewer"}:
            return False
        return (
            item.assignee_key is None
            or item.assignee_key == authenticated.principal.key
            or role in {"owner", "admin"}
        )


def _review_binding_hash(
    *,
    artifact: WorkflowArtifact,
    evidence_binding_hash: str,
    policy: ReviewPolicy,
    expires_at,
) -> str:
    return canonical_hash(
        {
            "artifact_id": artifact.id,
            "artifact_hash": artifact.content_hash,
            "candidate_id": artifact.candidate_id,
            "candidate_workspace_version_id": artifact.candidate_workspace_version_id,
            "evidence_binding_hash": evidence_binding_hash,
            "policy": policy.model_dump(mode="json"),
            "expires_at": expires_at.isoformat(),
        }
    )
