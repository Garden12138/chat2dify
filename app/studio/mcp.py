from __future__ import annotations

from datetime import timedelta
from hashlib import sha256
import secrets
from typing import Any, Callable, Literal

from pydantic import Field, ValidationError

from app.agent.state import RunConstraints
from app.studio.build import StudioBuildService
from app.studio.identity import AuthenticatedStudioRequest
from app.studio.models import (
    PreviewResourceMapping,
    Principal,
    ScenarioRunPolicy,
    ScopedTokenIssued,
    ScopedTokenRecord,
    ScopedTokenScope,
    StrictModel,
    StudioSession,
    VerifiedHostContext,
    new_id,
    utc_now,
)
from app.studio.releases import StudioReleaseService
from app.studio.reviews import StudioReviewService
from app.studio.runs import StudioRunService
from app.studio.scenarios import StudioScenarioService
from app.studio.store import StudioAccessDenied, StudioStore


class McpError(RuntimeError):
    code = "STUDIO_MCP_ERROR"


class McpAuthenticationRequired(McpError):
    code = "STUDIO_MCP_AUTHENTICATION_REQUIRED"


class McpScopeDenied(McpError):
    code = "STUDIO_MCP_SCOPE_DENIED"


class McpToolUnavailable(McpError):
    code = "STUDIO_MCP_TOOL_UNAVAILABLE"


class McpArgumentsInvalid(McpError):
    code = "STUDIO_MCP_ARGUMENTS_INVALID"


class RunSearchArguments(StrictModel):
    logical_app_id: str | None = Field(default=None, max_length=128)
    environment_id: str | None = Field(default=None, max_length=128)
    artifact_id: str | None = Field(default=None, max_length=128)
    status: Literal[
        "running",
        "succeeded",
        "failed",
        "stopped",
        "partial_succeeded",
        "unknown",
    ] | None = None
    error_code: str | None = Field(default=None, max_length=128)


class IncidentInspectArguments(StrictModel):
    incident_id: str = Field(min_length=1, max_length=128)


class ChangeRequestCreateArguments(StrictModel):
    build_id: str = Field(min_length=1, max_length=128)
    candidate_id: str = Field(min_length=1, max_length=128)
    scenario_run_id: str = Field(min_length=1, max_length=128)
    title: str = Field(min_length=1, max_length=256)
    release_note: str = Field(min_length=1, max_length=8_000)
    assignee_key: str | None = Field(default=None, max_length=768)
    require_separation: bool = False
    repair_proposal_id: str | None = Field(default=None, max_length=128)
    repair_proposal_version: int | None = Field(default=None, ge=1)


class TypedProposalArguments(StrictModel):
    build_id: str = Field(min_length=1, max_length=128)
    goal: str = Field(min_length=1, max_length=4_000)


class ScenarioRunArguments(StrictModel):
    build_id: str = Field(min_length=1, max_length=128)
    suite_id: str = Field(min_length=1, max_length=128)
    environment_id: str = Field(min_length=1, max_length=128)
    candidate_ids: list[str] = Field(min_length=1, max_length=20)
    mappings: list[PreviewResourceMapping] = Field(default_factory=list, max_length=100)
    policy: ScenarioRunPolicy


class ScenarioReadArguments(StrictModel):
    run_id: str = Field(min_length=1, max_length=128)


class ReviewReadArguments(StrictModel):
    change_request_id: str = Field(min_length=1, max_length=128)


class ReleasePreviewArguments(StrictModel):
    change_request_id: str = Field(min_length=1, max_length=128)
    environment_id: str = Field(min_length=1, max_length=128)


_TOOL_SCOPES: dict[str, ScopedTokenScope] = {
    "run_search": "search:read",
    "incident_inspect": "inspect:read",
    "change_request_create": "change_request:write",
    "typed_proposal_create": "proposal:write",
    "scenario_run": "scenario:run",
    "scenario_read": "scenario:read",
    "review_read": "review:read",
    "release_preview": "release:preview",
}

_TOOL_DESCRIPTIONS = {
    "run_search": "Search sanitized, Artifact-correlated Run evidence in this token's Project.",
    "incident_inspect": "Inspect one sanitized incident, affected path, Scenario coverage, and safe next step.",
    "change_request_create": "Create a normal evidence-bound Change Request; this cannot approve or release it.",
    "typed_proposal_create": "Ask Builder to create two Workspace-only typed Candidate proposals for an existing Build.",
    "scenario_run": "Run an existing Suite in its configured isolated Preview boundary.",
    "scenario_read": "Read sanitized Scenario result and cleanup evidence.",
    "review_read": "Read review status and audit events without Artifact Plan or raw DSL.",
    "release_preview": "Read an exact Release Preview; this cannot authorize Apply or Publish.",
}

_TOOL_MODELS = {
    "run_search": RunSearchArguments,
    "incident_inspect": IncidentInspectArguments,
    "change_request_create": ChangeRequestCreateArguments,
    "typed_proposal_create": TypedProposalArguments,
    "scenario_run": ScenarioRunArguments,
    "scenario_read": ScenarioReadArguments,
    "review_read": ReviewReadArguments,
    "release_preview": ReleasePreviewArguments,
}


class StudioScopedTokenService:
    def __init__(self, *, store: StudioStore) -> None:
        self.store = store

    def issue(
        self,
        authenticated: AuthenticatedStudioRequest,
        *,
        project_id: str,
        name: str,
        scopes: list[ScopedTokenScope],
        expires_in_seconds: int,
        rate_limit_per_minute: int,
    ) -> ScopedTokenIssued:
        normalized_scopes = _normalized_scopes(scopes)
        now = utc_now()
        token_id = new_id()
        plaintext = f"c2d5_{token_id}_{secrets.token_urlsafe(32)}"
        item = ScopedTokenRecord(
            id=token_id,
            project_id=project_id,
            name=name.strip(),
            token_prefix=plaintext[:16],
            scopes=normalized_scopes,
            created_by=authenticated.principal.key,
            rate_limit_per_minute=rate_limit_per_minute,
            expires_at=now + timedelta(seconds=expires_in_seconds),
            version=1,
            created_at=now,
        )
        stored = self.store.create_scoped_token(
            item=item,
            token_hash=_token_hash(plaintext),
            principal_key=authenticated.principal.key,
        )
        return ScopedTokenIssued(token=plaintext, record=stored)

    def list(
        self,
        authenticated: AuthenticatedStudioRequest,
        *,
        project_id: str,
    ) -> list[ScopedTokenRecord]:
        return self.store.list_scoped_tokens(
            project_id=project_id,
            principal_key=authenticated.principal.key,
        )

    def revoke(
        self,
        authenticated: AuthenticatedStudioRequest,
        *,
        project_id: str,
        token_id: str,
        expected_version: int,
    ) -> ScopedTokenRecord:
        return self.store.revoke_scoped_token(
            token_id=token_id,
            project_id=project_id,
            principal_key=authenticated.principal.key,
            expected_version=expected_version,
        )

    def rotate(
        self,
        authenticated: AuthenticatedStudioRequest,
        *,
        project_id: str,
        token_id: str,
        expected_version: int,
        expires_in_seconds: int,
    ) -> ScopedTokenIssued:
        old = self.store.get_scoped_token(
            token_id,
            project_id=project_id,
            principal_key=authenticated.principal.key,
        )
        now = utc_now()
        new_id_value = new_id()
        plaintext = f"c2d5_{new_id_value}_{secrets.token_urlsafe(32)}"
        item = ScopedTokenRecord(
            id=new_id_value,
            project_id=project_id,
            name=old.name,
            token_prefix=plaintext[:16],
            scopes=old.scopes,
            created_by=authenticated.principal.key,
            rate_limit_per_minute=old.rate_limit_per_minute,
            expires_at=now + timedelta(seconds=expires_in_seconds),
            rotated_from_id=old.id,
            version=1,
            created_at=now,
        )
        stored = self.store.rotate_scoped_token(
            old_token_id=old.id,
            old_expected_version=expected_version,
            new_item=item,
            new_token_hash=_token_hash(plaintext),
            principal_key=authenticated.principal.key,
        )
        return ScopedTokenIssued(token=plaintext, record=stored)


class StudioMcpService:
    def __init__(
        self,
        *,
        store: StudioStore,
        runs: StudioRunService | None,
        builds: StudioBuildService | None,
        scenarios: StudioScenarioService | None,
        reviews: StudioReviewService | None,
        releases: StudioReleaseService | None,
    ) -> None:
        self.store = store
        self.runs = runs
        self.builds = builds
        self.scenarios = scenarios
        self.reviews = reviews
        self.releases = releases

    def authenticate(self, authorization: str | None) -> tuple[ScopedTokenRecord, AuthenticatedStudioRequest]:
        if not authorization or not authorization.startswith("Bearer "):
            raise McpAuthenticationRequired("A scoped Bearer token is required.")
        plaintext = authorization.removeprefix("Bearer ").strip()
        if not plaintext.startswith("c2d5_"):
            raise McpAuthenticationRequired("A scoped Bearer token is required.")
        token = self.store.authenticate_scoped_token(
            token_hash=_token_hash(plaintext),
        )
        return token, self._delegated_request(token)

    def handle(
        self,
        *,
        token: ScopedTokenRecord,
        authenticated: AuthenticatedStudioRequest,
        request: dict[str, Any],
    ) -> dict[str, Any]:
        request_id = request.get("id")
        method = str(request.get("method") or "")
        try:
            if method == "initialize":
                result = {
                    "protocolVersion": "2025-06-18",
                    "serverInfo": {"name": "chat2dify-safe-mcp", "version": "5.0.0"},
                    "capabilities": {"tools": {}},
                }
            elif method == "tools/list":
                result = {"tools": self._tools(token)}
            elif method == "tools/call":
                params = request.get("params") or {}
                result = self._call(
                    token,
                    authenticated,
                    name=str(params.get("name") or ""),
                    arguments=params.get("arguments") or {},
                )
            else:
                raise McpToolUnavailable("The MCP method is not available.")
            return {"jsonrpc": "2.0", "id": request_id, "result": result}
        except Exception as exc:
            return {
                "jsonrpc": "2.0",
                "id": request_id,
                "error": {
                    "code": -32000,
                    "message": str(exc) or "MCP request failed.",
                    "data": {"code": getattr(exc, "code", "STUDIO_MCP_REQUEST_FAILED")},
                },
            }

    def _tools(self, token: ScopedTokenRecord) -> list[dict[str, Any]]:
        scopes = set(token.scopes)
        return [
            {
                "name": name,
                "description": _TOOL_DESCRIPTIONS[name],
                "inputSchema": _TOOL_MODELS[name].model_json_schema(),
            }
            for name, scope in _TOOL_SCOPES.items()
            if scope in scopes and self._service_available(name)
        ]

    def _service_available(self, name: str) -> bool:
        return {
            "run_search": self.runs,
            "incident_inspect": self.runs,
            "change_request_create": self.reviews,
            "typed_proposal_create": self.builds,
            "scenario_run": self.scenarios,
            "scenario_read": self.scenarios,
            "review_read": self.reviews,
            "release_preview": self.releases,
        }.get(name) is not None

    def _call(
        self,
        token: ScopedTokenRecord,
        authenticated: AuthenticatedStudioRequest,
        *,
        name: str,
        arguments: dict[str, Any],
    ) -> dict[str, Any]:
        required = _TOOL_SCOPES.get(name)
        if required is None or not self._service_available(name):
            raise McpToolUnavailable("The requested tool is structurally unavailable.")
        if required not in token.scopes:
            raise McpScopeDenied(f"The token does not include scope {required}.")
        try:
            return self._dispatch(name, token, authenticated, arguments)
        except ValidationError as exc:
            raise McpArgumentsInvalid(
                "Tool arguments contain unsupported or invalid fields."
            ) from exc

    def _dispatch(
        self,
        name: str,
        token: ScopedTokenRecord,
        authenticated: AuthenticatedStudioRequest,
        arguments: dict[str, Any],
    ) -> dict[str, Any]:
        project_id = token.project_id
        if name == "run_search":
            args = RunSearchArguments.model_validate(arguments)
            assert self.runs is not None
            view = self.runs.center(
                authenticated,
                project_id=project_id,
                **args.model_dump(),
            )
            return {
                "state": view.state,
                "message": view.message,
                "executions": [item.model_dump(mode="json") for item in view.executions],
                "incidents": [item.model_dump(mode="json") for item in view.incidents],
                "missing_evidence": view.missing_evidence,
            }
        if name == "incident_inspect":
            args = IncidentInspectArguments.model_validate(arguments)
            assert self.runs is not None
            return self.runs.incident(
                authenticated,
                project_id=project_id,
                incident_id=args.incident_id,
            ).model_dump(mode="json")
        if name == "change_request_create":
            args = ChangeRequestCreateArguments.model_validate(arguments)
            assert self.reviews is not None
            detail = self.reviews.create(
                authenticated,
                project_id=project_id,
                build_id=args.build_id,
                candidate_id=args.candidate_id,
                scenario_run_id=args.scenario_run_id,
                title=args.title,
                release_note=args.release_note,
                assignee_key=args.assignee_key,
                require_separation=args.require_separation,
                expires_in_seconds=604_800,
                repair_proposal_id=args.repair_proposal_id,
                repair_proposal_version=args.repair_proposal_version,
            )
            return {
                "change_request": detail.change_request.model_dump(mode="json"),
                "can_approve": False,
                "can_release": False,
            }
        if name == "typed_proposal_create":
            args = TypedProposalArguments.model_validate(arguments)
            assert self.builds is not None
            candidates = self.builds.command(
                authenticated,
                project_id=project_id,
                build_id=args.build_id,
                mode="alternatives",
                message=args.goal,
                candidate_count=2,
                constraints=RunConstraints(workspace_only=True),
            )
            return {
                "candidates": [
                    {
                        "id": item.id,
                        "label": item.label,
                        "status": item.status,
                        "external_write": False,
                    }
                    for item in candidates
                ]
            }
        if name == "scenario_run":
            args = ScenarioRunArguments.model_validate(arguments)
            assert self.scenarios is not None
            run = self.scenarios.run_suite(
                authenticated,
                project_id=project_id,
                build_id=args.build_id,
                suite_id=args.suite_id,
                environment_id=args.environment_id,
                candidate_ids=args.candidate_ids,
                mappings=args.mappings,
                policy=args.policy,
            )
            return _scenario_summary(run)
        if name == "scenario_read":
            args = ScenarioReadArguments.model_validate(arguments)
            assert self.scenarios is not None
            run = self.scenarios.get_run(
                authenticated,
                project_id=project_id,
                run_id=args.run_id,
            )
            return _scenario_summary(run)
        if name == "review_read":
            args = ReviewReadArguments.model_validate(arguments)
            assert self.reviews is not None
            detail = self.reviews.detail(
                authenticated,
                project_id=project_id,
                change_request_id=args.change_request_id,
            )
            return {
                "change_request": detail.change_request.model_dump(mode="json"),
                "events": [item.model_dump(mode="json") for item in detail.events],
                "stale_reasons": detail.stale_reasons,
            }
        if name == "release_preview":
            args = ReleasePreviewArguments.model_validate(arguments)
            assert self.releases is not None
            return self.releases.preview(
                authenticated,
                project_id=project_id,
                change_request_id=args.change_request_id,
                environment_id=args.environment_id,
            ).model_dump(mode="json")
        raise McpToolUnavailable("The requested tool is structurally unavailable.")

    def _delegated_request(
        self,
        token: ScopedTokenRecord,
    ) -> AuthenticatedStudioRequest:
        project, membership = self.store.get_project_for_principal(
            token.project_id,
            token.created_by,
        )
        issuer, _, subject = token.created_by.partition(":")
        principal = Principal(
            issuer=issuer or "chat2dify-studio",
            subject=subject or token.created_by,
            display_name=f"Scoped client: {token.name}",
            dify_tenant_id=project.dify_tenant_id,
        )
        digest = sha256(token.id.encode("utf-8")).hexdigest()
        session = StudioSession(
            id=f"scoped:{token.id}",
            jti_hash=digest,
            principal_key=principal.key,
            project_id=project.id,
            dify_account_id=principal.subject,
            dify_tenant_id=project.dify_tenant_id,
            origin="mcp://scoped-token",
            nonce_hash=digest,
            expires_at=token.expires_at,
            created_at=token.created_at,
        )
        return AuthenticatedStudioRequest(
            claims={"scoped_token_id": token.id, "scopes": token.scopes},
            session=session,
            principal=principal,
            project=project,
            membership=membership,
            host=VerifiedHostContext(
                principal=principal,
                apps=[],
                apps_available=False,
                apps_error_code="MCP_HOST_CONTEXT_NOT_FORWARDED",
            ),
        )


def _scenario_summary(run: Any) -> dict[str, Any]:
    return {
        "id": run.id,
        "status": run.status,
        "candidate_ids": run.candidate_ids,
        "reports": [item.model_dump(mode="json") for item in run.reports],
        "comparison": run.comparison,
        "cleanup_verified": run.cleanup_verified,
        "failure": run.failure,
        "created_at": run.created_at.isoformat(),
        "updated_at": run.updated_at.isoformat(),
    }


def _normalized_scopes(scopes: list[ScopedTokenScope]) -> list[ScopedTokenScope]:
    allowed = set(_TOOL_SCOPES.values())
    normalized = sorted(set(scopes))
    if not normalized or any(item not in allowed for item in normalized):
        raise McpScopeDenied("Scoped tokens require one or more supported scopes.")
    return normalized  # type: ignore[return-value]


def _token_hash(plaintext: str) -> str:
    return sha256(plaintext.encode("utf-8")).hexdigest()
