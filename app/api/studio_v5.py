from __future__ import annotations

from datetime import datetime
from typing import Any, Literal
from uuid import uuid4

from fastapi import APIRouter, Query, Request, Response
from fastapi.responses import JSONResponse
from pydantic import Field

from app.agent.service import AgentApplicationService
from app.agent.state import CanvasViewport, RunConstraints
from app.agent.store import AgentStore
from app.studio.artifacts import ArtifactError
from app.studio.home import V4ContinuityError
from app.studio.identity import (
    StudioHostSessionInvalid,
    StudioHostUnavailable,
    StudioIdentityError,
    StudioIdentityExpired,
    StudioIdentityRequired,
    StudioOriginDenied,
)
from app.studio.models import (
    BlueprintApplyResult,
    BlueprintGallery,
    BlueprintGalleryItem,
    BlueprintSetupValidation,
    BlueprintSetupValue,
    BlueprintTypedInterface,
    BlueprintUpgradePreview,
    BlueprintVersionRecord,
    Membership,
    BuildStudioView,
    ChangeRequest,
    ChangeRequestDetail,
    EnvironmentMappingSet,
    ExecutionRefreshResult,
    GitArtifactBundle,
    LogicalApp,
    PreviewFixture,
    PreviewResourceMapping,
    Principal,
    Project,
    RegressionGate,
    RepairProposal,
    ReleaseAuthorization,
    ReleaseCenterView,
    ReleaseEnvironment,
    ReleasePreview,
    ReleaseRecord,
    ReleaseResourceMapping,
    RunCenterView,
    RunAlertRule,
    RunAutomationView,
    RunIncidentDetail,
    ScheduledRegression,
    ScopedTokenIssued,
    ScopedTokenRecord,
    ScopedTokenScope,
    ScenarioBaseline,
    ScenarioCase,
    ScenarioExpectedOutput,
    ScenarioFileFixture,
    ScenarioFileReference,
    ScenarioInputSchema,
    ScenarioInvariant,
    ScenarioLabView,
    ScenarioRubricCriterion,
    ScenarioRun,
    ScenarioRunPolicy,
    ScenarioSanitizedRunApproval,
    ScenarioSuite,
    StrictModel,
    StudioHome,
)
from app.studio.service import StudioApplicationService
from app.studio.preview import PreviewAdapterError
from app.studio.releases import ReleaseError
from app.studio.reviews import ReviewError, ReviewSelfApprovalDenied
from app.studio.runs import RunCenterError
from app.studio.automation import RunAutomationError
from app.studio.mcp import McpAuthenticationRequired, McpError
from app.studio.scenarios import ScenarioError
from app.studio.store import (
    StudioAccessDenied,
    StudioConflict,
    StudioRecordNotFound,
    StudioReplayDetected,
    StudioRateLimited,
    StudioStoreError,
)


router = APIRouter(prefix="/api/v5/studio", tags=["studio-v5"])


class StudioErrorDetail(StrictModel):
    code: str
    message: str
    retryable: bool = False
    request_id: str


class StudioErrorEnvelope(StrictModel):
    error: StudioErrorDetail


class StudioRequestInvalid(ValueError):
    code = "STUDIO_REQUEST_INVALID"


class StudioSessionRequest(StrictModel):
    nonce: str = Field(min_length=20, max_length=128)


class StudioSessionResponse(StrictModel):
    token: str
    token_type: Literal["Bearer"] = "Bearer"
    expires_at: str
    principal: Principal
    project: Project
    membership: Membership
    apps_available: bool
    apps_error_code: str | None = None


class ResumeV4Request(StrictModel):
    project_id: str = Field(min_length=1, max_length=128)
    run_id: str = Field(min_length=1, max_length=128)
    message: str | None = Field(default=None, max_length=8_000)


class CreateBuildRequest(StrictModel):
    project_id: str = Field(min_length=1, max_length=128)
    operation: Literal["create", "modify"]
    entry_source: Literal["home", "canvas", "create"]
    app_id: str | None = Field(default=None, max_length=256)
    app_mode: Literal["workflow", "advanced-chat", "chat", "completion", "agent-chat"]
    app_name: str = Field(min_length=1, max_length=512)


class BuildCanvasContext(StrictModel):
    selected_node_ids: list[str] = Field(default_factory=list, max_length=100)
    selected_edge_ids: list[str] = Field(default_factory=list, max_length=100)
    viewport: CanvasViewport | None = None
    current_panel: str | None = Field(default=None, max_length=128)
    dirty_state: bool = False
    canvas_draft_hash: str | None = Field(default=None, max_length=512)
    revision: int = Field(default=0, ge=0)


class BuildCommandRequest(StrictModel):
    project_id: str = Field(min_length=1, max_length=128)
    mode: Literal["explain", "alternatives", "synthesize"]
    message: str = Field(min_length=1, max_length=8_000)
    candidate_count: int = Field(default=2, ge=2, le=3)
    source_candidate_ids: list[str] = Field(default_factory=list, max_length=3)
    canvas_context: BuildCanvasContext | None = None


class CandidateActionRequest(StrictModel):
    project_id: str = Field(min_length=1, max_length=128)
    candidate_id: str = Field(min_length=1, max_length=128)


class ResumeCandidateRequest(CandidateActionRequest):
    message: str | None = Field(default=None, max_length=8_000)


class BuildContextCommandRequest(CandidateActionRequest):
    command: Literal[
        "explain_selection",
        "explain_variable_flow",
        "safer_fallback",
        "generate_scenarios",
        "suggest_resources",
    ]
    selected_node_ids: list[str] = Field(default_factory=list, max_length=20)


class BlueprintSetupRequest(StrictModel):
    project_id: str = Field(min_length=1, max_length=128)
    build_id: str = Field(min_length=1, max_length=128)
    version: str | None = Field(default=None, pattern=r"^\d+\.\d+\.\d+$")
    values: list[BlueprintSetupValue] = Field(default_factory=list, max_length=40)


class ExtractBlueprintRequest(StrictModel):
    project_id: str = Field(min_length=1, max_length=128)
    build_id: str = Field(min_length=1, max_length=128)
    candidate_id: str = Field(min_length=1, max_length=128)
    selected_node_ids: list[str] = Field(min_length=1, max_length=40)
    name: str = Field(min_length=1, max_length=256)
    business_outcome: str = Field(min_length=1, max_length=4_000)
    category: str = Field(min_length=1, max_length=128)
    visibility: Literal["private", "team"]
    typed_interface: BlueprintTypedInterface


class ProposeBlueprintVersionRequest(StrictModel):
    project_id: str = Field(min_length=1, max_length=128)
    version: str = Field(pattern=r"^\d+\.\d+\.\d+$")
    upgrade_notes: list[str] = Field(min_length=1, max_length=40)


class ReviewBlueprintVersionRequest(StrictModel):
    project_id: str = Field(min_length=1, max_length=128)
    approved: bool
    note: str = Field(min_length=1, max_length=2_000)


class ScenarioSourceRequest(StrictModel):
    kind: Literal["manual", "generated", "fixture", "approved_sanitized_run"]
    input_schema_hash: str | None = Field(default=None, min_length=64, max_length=64)
    fixture_id: str | None = Field(default=None, max_length=128)
    source_run_id: str | None = Field(default=None, max_length=128)
    evidence_hash: str | None = Field(default=None, min_length=64, max_length=64)


class ScenarioCaseRequest(StrictModel):
    name: str = Field(min_length=1, max_length=256)
    source: ScenarioSourceRequest
    inputs: dict[str, Any] = Field(default_factory=dict)
    files: list[ScenarioFileReference] = Field(default_factory=list, max_length=20)
    expected_output: ScenarioExpectedOutput
    expected_behavior: str = Field(min_length=1, max_length=4_000)
    invariants: list[ScenarioInvariant] = Field(min_length=1, max_length=30)
    rubric: list[ScenarioRubricCriterion] = Field(default_factory=list, max_length=20)
    tags: list[str] = Field(default_factory=list, max_length=20)


class CreateScenarioSuiteRequest(StrictModel):
    project_id: str = Field(min_length=1, max_length=128)
    build_id: str = Field(min_length=1, max_length=128)
    candidate_ids: list[str] = Field(min_length=1, max_length=20)
    name: str = Field(min_length=1, max_length=256)
    description: str = Field(min_length=1, max_length=4_000)
    retention_days: int = Field(default=30, ge=1, le=365)
    semantic_version: str = Field(default="1.0.0", pattern=r"^\d+\.\d+\.\d+$")
    input_schema_hash: str = Field(min_length=64, max_length=64)
    cases: list[ScenarioCaseRequest] = Field(min_length=1, max_length=100)


class GenerateScenarioCasesRequest(StrictModel):
    project_id: str = Field(min_length=1, max_length=128)
    build_id: str = Field(min_length=1, max_length=128)
    candidate_ids: list[str] = Field(min_length=1, max_length=20)
    input_schema_hash: str = Field(min_length=64, max_length=64)


class ApproveScenarioFixtureRequest(StrictModel):
    project_id: str = Field(min_length=1, max_length=128)
    name: str = Field(min_length=1, max_length=256)
    opaque_ref: str = Field(min_length=1, max_length=512)
    media_type: str = Field(min_length=1, max_length=128)
    size_bytes: int = Field(ge=1, le=50_000_000)
    content_hash: str = Field(pattern=r"^[a-fA-F0-9]{64}$")
    ttl_seconds: int = Field(default=86_400, ge=60, le=2_592_000)


class RunScenarioSuiteRequest(StrictModel):
    project_id: str = Field(min_length=1, max_length=128)
    build_id: str = Field(min_length=1, max_length=128)
    suite_id: str = Field(min_length=1, max_length=128)
    environment_id: str = Field(min_length=1, max_length=128)
    candidate_ids: list[str] = Field(min_length=1, max_length=20)
    mappings: list[PreviewResourceMapping] = Field(default_factory=list, max_length=100)
    policy: ScenarioRunPolicy


class ApproveSanitizedRunSourceRequest(StrictModel):
    project_id: str = Field(min_length=1, max_length=128)
    ttl_seconds: int = Field(default=604_800, ge=60, le=2_592_000)


class ProjectActionRequest(StrictModel):
    project_id: str = Field(min_length=1, max_length=128)


class SaveScenarioBaselineRequest(ProjectActionRequest):
    candidate_id: str = Field(min_length=1, max_length=128)


class ConfigureRegressionGateRequest(ProjectActionRequest):
    build_id: str = Field(min_length=1, max_length=128)
    suite_id: str = Field(min_length=1, max_length=128)
    min_pass_rate: float = Field(default=1.0, ge=0, le=1)
    min_quality_score: float = Field(default=80, ge=0, le=100)
    max_latency_regression_percent: float = Field(default=20, ge=0, le=1_000)
    max_cost_regression_percent: float = Field(default=20, ge=0, le=1_000)
    evidence_ttl_seconds: int = Field(default=604_800, ge=60, le=2_592_000)
    required_policy: ScenarioRunPolicy


class CreateChangeRequestRequest(StrictModel):
    project_id: str = Field(min_length=1, max_length=128)
    build_id: str = Field(min_length=1, max_length=128)
    candidate_id: str = Field(min_length=1, max_length=128)
    scenario_run_id: str = Field(min_length=1, max_length=128)
    title: str = Field(min_length=1, max_length=256)
    release_note: str = Field(min_length=1, max_length=8_000)
    assignee_key: str | None = Field(default=None, max_length=768)
    require_author_approver_separation: bool = False
    expires_in_seconds: int = Field(default=604_800, ge=3_600, le=2_592_000)
    repair_proposal_id: str | None = Field(default=None, max_length=128)
    repair_proposal_version: int | None = Field(default=None, ge=1)


class CommentChangeRequestRequest(ProjectActionRequest):
    body: str = Field(min_length=1, max_length=8_000)


class AssignChangeRequestRequest(ProjectActionRequest):
    assignee_key: str = Field(min_length=1, max_length=768)
    expected_version: int = Field(ge=1)


class DecideChangeRequestRequest(ProjectActionRequest):
    decision: Literal["request_changes", "approve", "reject"]
    body: str = Field(min_length=1, max_length=8_000)
    expected_version: int = Field(ge=1)
    expected_binding_hash: str = Field(min_length=64, max_length=64)


class SupersedeChangeRequestRequest(ProjectActionRequest):
    expected_version: int = Field(ge=1)
    build_id: str = Field(min_length=1, max_length=128)
    candidate_id: str = Field(min_length=1, max_length=128)
    scenario_run_id: str = Field(min_length=1, max_length=128)
    title: str = Field(min_length=1, max_length=256)
    release_note: str = Field(min_length=1, max_length=8_000)
    expires_in_seconds: int = Field(default=604_800, ge=3_600, le=2_592_000)


class CreateLogicalAppRequest(ProjectActionRequest):
    name: str = Field(min_length=1, max_length=256)
    app_mode: Literal["workflow", "advanced-chat"]


class CreateReleaseEnvironmentRequest(ProjectActionRequest):
    logical_app_id: str = Field(min_length=1, max_length=128)
    name: str = Field(min_length=1, max_length=256)
    classification: Literal["development", "staging", "production"]
    target_app_ref: str = Field(min_length=1, max_length=512)


class ConfigureReleaseMappingRequest(ProjectActionRequest):
    mappings: list[ReleaseResourceMapping] = Field(default_factory=list, max_length=200)
    expected_version: int | None = Field(default=None, ge=1)


class ReleasePreviewRequest(ProjectActionRequest):
    change_request_id: str = Field(min_length=1, max_length=128)
    environment_id: str = Field(min_length=1, max_length=128)


class AuthorizeReleaseRequest(ReleasePreviewRequest):
    action: Literal["apply_draft", "publish"]
    confirmation: Literal["APPLY_DRAFT", "PUBLISH"]
    expires_in_seconds: int = Field(default=600, ge=60, le=1_800)


class ExecuteReleaseRequest(ProjectActionRequest):
    authorization_id: str = Field(min_length=1, max_length=128)
    idempotency_key: str = Field(min_length=8, max_length=256)


class ProposeRollbackRequest(ProjectActionRequest):
    artifact_id: str = Field(min_length=1, max_length=128)
    title: str = Field(min_length=1, max_length=256)
    release_note: str = Field(min_length=1, max_length=8_000)
    assignee_key: str | None = Field(default=None, max_length=768)
    require_author_approver_separation: bool = False
    expires_in_seconds: int = Field(default=604_800, ge=3_600, le=2_592_000)


class GitPullArtifactRequest(ProjectActionRequest):
    base_artifact_id: str = Field(min_length=1, max_length=128)
    expected_base_hash: str = Field(min_length=64, max_length=64)
    canonical_json: str = Field(min_length=2, max_length=2_000_000)
    content_hash: str = Field(min_length=64, max_length=64)
    title: str = Field(min_length=1, max_length=256)
    release_note: str = Field(min_length=1, max_length=8_000)
    assignee_key: str | None = Field(default=None, max_length=768)
    expires_in_seconds: int = Field(default=604_800, ge=3_600, le=2_592_000)


class RefreshRunEvidenceRequest(ProjectActionRequest):
    environment_id: str | None = Field(default=None, max_length=128)
    limit_per_environment: int = Field(default=100, ge=1, le=100)


class CreateRepairProposalRequest(ProjectActionRequest):
    title: str | None = Field(default=None, min_length=1, max_length=256)


class ConfigureRunAlertRequest(ProjectActionRequest):
    rule_id: str | None = Field(default=None, max_length=128)
    expected_version: int | None = Field(default=None, ge=1)
    name: str = Field(min_length=1, max_length=256)
    environment_id: str | None = Field(default=None, max_length=128)
    stable_error_code: str | None = Field(default=None, max_length=128)
    error_count_threshold: int = Field(default=1, ge=1, le=10_000)
    failure_rate_threshold: float | None = Field(default=None, ge=0, le=1)
    window_seconds: int = Field(default=3600, ge=60, le=2_592_000)
    adapter_ref: str = Field(min_length=1, max_length=256)
    enabled: bool = True


class ConfigureScheduledRegressionRequest(ProjectActionRequest):
    schedule_id: str | None = Field(default=None, max_length=128)
    expected_version: int | None = Field(default=None, ge=1)
    artifact_id: str = Field(min_length=1, max_length=128)
    suite_id: str = Field(min_length=1, max_length=128)
    interval_seconds: int = Field(default=86_400, ge=900, le=2_592_000)
    enabled: bool = True


class CancelDurableWorkRequest(ProjectActionRequest):
    entity_type: Literal["job", "outbox"]
    entity_id: str = Field(min_length=1, max_length=128)
    reason: str = Field(min_length=1, max_length=500)


class CreateScopedTokenRequest(ProjectActionRequest):
    name: str = Field(min_length=1, max_length=256)
    scopes: list[ScopedTokenScope] = Field(min_length=1, max_length=8)
    expires_in_seconds: int = Field(default=2_592_000, ge=3_600, le=7_776_000)
    rate_limit_per_minute: int = Field(default=60, ge=1, le=1_000)


class RotateScopedTokenRequest(ProjectActionRequest):
    expected_version: int = Field(ge=1)
    expires_in_seconds: int = Field(default=2_592_000, ge=3_600, le=7_776_000)


class RevokeScopedTokenRequest(ProjectActionRequest):
    expected_version: int = Field(ge=1)


ERROR_RESPONSES = {
    422: {"model": StudioErrorEnvelope},
    401: {"model": StudioErrorEnvelope},
    403: {"model": StudioErrorEnvelope},
    404: {"model": StudioErrorEnvelope},
    409: {"model": StudioErrorEnvelope},
    503: {"model": StudioErrorEnvelope},
    429: {"model": StudioErrorEnvelope},
}


@router.post(
    "/session",
    response_model=StudioSessionResponse,
    responses=ERROR_RESPONSES,
)
def create_studio_session(
    payload: StudioSessionRequest,
    request: Request,
    response: Response,
):
    try:
        service = require_studio_service(request)
        issued = service.issue_session(
            nonce=payload.nonce,
            origin_header=request.headers.get("origin"),
            cookie_header=request.headers.get("cookie"),
        )
        _forward_dify_cookies(response, issued.set_cookie_headers)
        return StudioSessionResponse(
            token=issued.token,
            expires_at=issued.expires_at.isoformat(),
            principal=issued.principal,
            project=issued.project,
            membership=issued.membership,
            apps_available=issued.apps_available,
            apps_error_code=issued.apps_error_code,
        )
    except Exception as exc:
        return studio_error_response(exc)


@router.get(
    "/home",
    response_model=StudioHome,
    responses=ERROR_RESPONSES,
)
def get_studio_home(
    request: Request,
    response: Response,
    project_id: str | None = Query(default=None, max_length=128),
    search: str | None = Query(default=None, max_length=256),
    app_mode: str | None = Query(
        default=None,
        pattern="^(workflow|advanced-chat|chat|agent-chat|completion)$",
    ),
):
    try:
        service = require_studio_service(request)
        authenticated = service.authenticate(
            authorization=request.headers.get("authorization"),
            origin_header=request.headers.get("origin"),
            referer_header=request.headers.get("referer"),
            cookie_header=request.headers.get("cookie"),
            app_name=search,
            app_mode=app_mode,
        )
        _forward_dify_cookies(
            response,
            authenticated.host.set_cookie_headers,
        )
        return service.home(
            authenticated,
            project_id=project_id,
            search=search,
            app_mode=app_mode,
            v4_enabled=bool(
                getattr(request.app.state, "agent_v4_enabled", False)
            ),
        )
    except Exception as exc:
        return studio_error_response(exc)


@router.post(
    "/home/resume-v4",
    status_code=202,
    responses=ERROR_RESPONSES,
)
def resume_v4_work(
    payload: ResumeV4Request,
    request: Request,
    response: Response,
):
    try:
        service = require_studio_service(request)
        authenticated = service.authenticate(
            authorization=request.headers.get("authorization"),
            origin_header=request.headers.get("origin"),
            referer_header=request.headers.get("referer"),
            cookie_header=request.headers.get("cookie"),
        )
        _forward_dify_cookies(
            response,
            authenticated.host.set_cookie_headers,
        )
        agent_store = getattr(request.app.state, "agent_store", None)
        agent_service = getattr(request.app.state, "agent_service", None)
        resumed = service.resume_v4(
            authenticated,
            project_id=payload.project_id,
            run_id=payload.run_id,
            message=payload.message,
            agent_store=agent_store if isinstance(agent_store, AgentStore) else None,
            agent_service=(
                agent_service
                if isinstance(agent_service, AgentApplicationService)
                else None
            ),
        )
        return resumed.model_dump(mode="json")
    except Exception as exc:
        return studio_error_response(exc)


@router.post(
    "/builds",
    status_code=201,
    responses=ERROR_RESPONSES,
)
def create_build(
    payload: CreateBuildRequest,
    request: Request,
    response: Response,
):
    try:
        service, authenticated = _authenticated_service(request, response)
        return service.create_build(
            authenticated,
            project_id=payload.project_id,
            operation=payload.operation,
            entry_source=payload.entry_source,
            app_id=payload.app_id,
            app_mode=payload.app_mode,
            app_name=payload.app_name,
        ).model_dump(mode="json")
    except Exception as exc:
        return studio_error_response(exc)


@router.get(
    "/builds/{build_id}",
    response_model=BuildStudioView,
    responses=ERROR_RESPONSES,
)
def get_build(
    build_id: str,
    request: Request,
    response: Response,
    project_id: str = Query(min_length=1, max_length=128),
):
    try:
        service, authenticated = _authenticated_service(request, response)
        return service.get_build(
            authenticated,
            project_id=project_id,
            build_id=build_id,
        )
    except Exception as exc:
        return studio_error_response(exc)


@router.post(
    "/builds/{build_id}/commands",
    response_model=BuildStudioView,
    status_code=202,
    responses=ERROR_RESPONSES,
)
def command_build(
    build_id: str,
    payload: BuildCommandRequest,
    request: Request,
    response: Response,
):
    try:
        service, authenticated = _authenticated_service(request, response)
        context = payload.canvas_context
        constraints = RunConstraints(
            workspace_only=True,
            selected_node_ids=context.selected_node_ids if context else [],
            selected_edge_ids=context.selected_edge_ids if context else [],
            viewport=context.viewport if context else None,
            current_panel=context.current_panel if context else None,
            dirty_state=context.dirty_state if context else False,
            canvas_draft_hash=context.canvas_draft_hash if context else None,
            canvas_context_revision=context.revision if context else 0,
        )
        service.command_build(
            authenticated,
            project_id=payload.project_id,
            build_id=build_id,
            mode=payload.mode,
            message=payload.message,
            candidate_count=payload.candidate_count,
            source_candidate_ids=payload.source_candidate_ids,
            constraints=constraints,
        )
        return service.get_build(
            authenticated,
            project_id=payload.project_id,
            build_id=build_id,
        )
    except Exception as exc:
        return studio_error_response(exc)


@router.post(
    "/builds/{build_id}/select",
    response_model=BuildStudioView,
    responses=ERROR_RESPONSES,
)
def select_candidate(
    build_id: str,
    payload: CandidateActionRequest,
    request: Request,
    response: Response,
):
    try:
        service, authenticated = _authenticated_service(request, response)
        return service.select_candidate(
            authenticated,
            project_id=payload.project_id,
            build_id=build_id,
            candidate_id=payload.candidate_id,
        )
    except Exception as exc:
        return studio_error_response(exc)


@router.post(
    "/builds/{build_id}/cancel",
    response_model=BuildStudioView,
    responses=ERROR_RESPONSES,
)
def cancel_candidate(
    build_id: str,
    payload: CandidateActionRequest,
    request: Request,
    response: Response,
):
    try:
        service, authenticated = _authenticated_service(request, response)
        return service.cancel_candidate(
            authenticated,
            project_id=payload.project_id,
            build_id=build_id,
            candidate_id=payload.candidate_id,
        )
    except Exception as exc:
        return studio_error_response(exc)


@router.post(
    "/builds/{build_id}/resume",
    response_model=BuildStudioView,
    status_code=202,
    responses=ERROR_RESPONSES,
)
def resume_candidate(
    build_id: str,
    payload: ResumeCandidateRequest,
    request: Request,
    response: Response,
):
    try:
        service, authenticated = _authenticated_service(request, response)
        return service.resume_candidate(
            authenticated,
            project_id=payload.project_id,
            build_id=build_id,
            candidate_id=payload.candidate_id,
            message=payload.message,
        )
    except Exception as exc:
        return studio_error_response(exc)


@router.post(
    "/builds/{build_id}/context",
    responses=ERROR_RESPONSES,
)
def contextual_command(
    build_id: str,
    payload: BuildContextCommandRequest,
    request: Request,
    response: Response,
):
    try:
        service, authenticated = _authenticated_service(request, response)
        return service.contextual_command(
            authenticated,
            project_id=payload.project_id,
            build_id=build_id,
            candidate_id=payload.candidate_id,
            command=payload.command,
            selected_node_ids=payload.selected_node_ids,
        )
    except Exception as exc:
        return studio_error_response(exc)


@router.get(
    "/blueprints",
    response_model=BlueprintGallery,
    responses=ERROR_RESPONSES,
)
def list_blueprints(
    request: Request,
    response: Response,
    project_id: str = Query(min_length=1, max_length=128),
    build_id: str | None = Query(default=None, max_length=128),
    search: str | None = Query(default=None, max_length=256),
    category: str | None = Query(default=None, max_length=128),
    app_mode: str | None = Query(
        default=None,
        pattern="^(workflow|advanced-chat|chat|agent-chat|completion)$",
    ),
    dify_version: str | None = Query(default=None, max_length=128),
    risk: str | None = Query(default=None, pattern="^(low|medium|high)$"),
    visibility: str | None = Query(
        default=None,
        pattern="^(builtin|private|team)$",
    ),
    resource_available: bool | None = Query(default=None),
    compatible_only: bool = Query(default=True),
):
    try:
        service, authenticated = _authenticated_service(request, response)
        return service.blueprint_gallery(
            authenticated,
            project_id=project_id,
            build_id=build_id,
            search=search,
            category=category,
            app_mode=app_mode,
            dify_version=dify_version,
            risk=risk,
            visibility=visibility,
            resource_available=resource_available,
            compatible_only=compatible_only,
        )
    except Exception as exc:
        return studio_error_response(exc)


@router.post(
    "/blueprints/extract",
    response_model=BlueprintVersionRecord,
    status_code=201,
    responses=ERROR_RESPONSES,
)
def extract_blueprint(
    payload: ExtractBlueprintRequest,
    request: Request,
    response: Response,
):
    try:
        service, authenticated = _authenticated_service(request, response)
        return service.extract_blueprint(
            authenticated,
            project_id=payload.project_id,
            build_id=payload.build_id,
            candidate_id=payload.candidate_id,
            selected_node_ids=payload.selected_node_ids,
            name=payload.name,
            business_outcome=payload.business_outcome,
            category=payload.category,
            visibility=payload.visibility,
            typed_interface=payload.typed_interface,
        )
    except Exception as exc:
        return studio_error_response(exc)


@router.get(
    "/blueprints/{blueprint_id}",
    response_model=BlueprintGalleryItem,
    responses=ERROR_RESPONSES,
)
def get_blueprint(
    blueprint_id: str,
    request: Request,
    response: Response,
    project_id: str = Query(min_length=1, max_length=128),
    build_id: str | None = Query(default=None, max_length=128),
    version: str | None = Query(default=None, pattern=r"^\d+\.\d+\.\d+$"),
):
    try:
        service, authenticated = _authenticated_service(request, response)
        return service.blueprint_detail(
            authenticated,
            project_id=project_id,
            blueprint_id=blueprint_id,
            version=version,
            build_id=build_id,
        )
    except Exception as exc:
        return studio_error_response(exc)


@router.post(
    "/blueprints/{blueprint_id}/validate",
    response_model=BlueprintSetupValidation,
    responses=ERROR_RESPONSES,
)
def validate_blueprint_setup(
    blueprint_id: str,
    payload: BlueprintSetupRequest,
    request: Request,
    response: Response,
):
    try:
        service, authenticated = _authenticated_service(request, response)
        return service.validate_blueprint_setup(
            authenticated,
            project_id=payload.project_id,
            blueprint_id=blueprint_id,
            values=payload.values,
            build_id=payload.build_id,
            version=payload.version,
        )
    except Exception as exc:
        return studio_error_response(exc)


@router.post(
    "/blueprints/{blueprint_id}/apply",
    response_model=BlueprintApplyResult,
    status_code=201,
    responses=ERROR_RESPONSES,
)
def apply_blueprint(
    blueprint_id: str,
    payload: BlueprintSetupRequest,
    request: Request,
    response: Response,
):
    try:
        service, authenticated = _authenticated_service(request, response)
        return service.apply_blueprint(
            authenticated,
            project_id=payload.project_id,
            blueprint_id=blueprint_id,
            values=payload.values,
            build_id=payload.build_id,
            version=payload.version,
        )
    except Exception as exc:
        return studio_error_response(exc)


@router.post(
    "/blueprints/{blueprint_id}/versions",
    response_model=BlueprintVersionRecord,
    status_code=202,
    responses=ERROR_RESPONSES,
)
def propose_blueprint_version(
    blueprint_id: str,
    payload: ProposeBlueprintVersionRequest,
    request: Request,
    response: Response,
):
    try:
        service, authenticated = _authenticated_service(request, response)
        return service.propose_blueprint_version(
            authenticated,
            project_id=payload.project_id,
            blueprint_id=blueprint_id,
            version=payload.version,
            upgrade_notes=payload.upgrade_notes,
        )
    except Exception as exc:
        return studio_error_response(exc)


@router.post(
    "/blueprints/{blueprint_id}/versions/{version}/review",
    response_model=BlueprintVersionRecord,
    responses=ERROR_RESPONSES,
)
def review_blueprint_version(
    blueprint_id: str,
    version: str,
    payload: ReviewBlueprintVersionRequest,
    request: Request,
    response: Response,
):
    try:
        service, authenticated = _authenticated_service(request, response)
        return service.review_blueprint_version(
            authenticated,
            project_id=payload.project_id,
            blueprint_id=blueprint_id,
            version=version,
            approved=payload.approved,
            note=payload.note,
        )
    except Exception as exc:
        return studio_error_response(exc)


@router.get(
    "/blueprint-applications/{application_id}/upgrade",
    response_model=BlueprintUpgradePreview,
    responses=ERROR_RESPONSES,
)
def preview_blueprint_upgrade(
    application_id: str,
    request: Request,
    response: Response,
    project_id: str = Query(min_length=1, max_length=128),
    target_version: str | None = Query(default=None, pattern=r"^\d+\.\d+\.\d+$"),
):
    try:
        service, authenticated = _authenticated_service(request, response)
        return service.blueprint_upgrade_preview(
            authenticated,
            project_id=project_id,
            application_id=application_id,
            target_version=target_version,
        )
    except Exception as exc:
        return studio_error_response(exc)


@router.get(
    "/scenario-lab",
    response_model=ScenarioLabView,
    responses=ERROR_RESPONSES,
)
def get_scenario_lab(
    request: Request,
    response: Response,
    project_id: str = Query(min_length=1, max_length=128),
    build_id: str = Query(min_length=1, max_length=128),
):
    try:
        service, authenticated = _authenticated_service(request, response)
        return service.scenario_lab(
            authenticated,
            project_id=project_id,
            build_id=build_id,
        )
    except Exception as exc:
        return studio_error_response(exc)


@router.get(
    "/scenario-lab/input-schema",
    response_model=ScenarioInputSchema,
    responses=ERROR_RESPONSES,
)
def get_scenario_input_schema(
    request: Request,
    response: Response,
    project_id: str = Query(min_length=1, max_length=128),
    build_id: str = Query(min_length=1, max_length=128),
    candidate_ids: list[str] = Query(min_length=1, max_length=20),
):
    try:
        service, authenticated = _authenticated_service(request, response)
        return service.discover_scenario_input_schema(
            authenticated,
            project_id=project_id,
            build_id=build_id,
            candidate_ids=candidate_ids,
        )
    except Exception as exc:
        return studio_error_response(exc)


@router.post(
    "/scenario-suites",
    response_model=ScenarioSuite,
    status_code=201,
    responses=ERROR_RESPONSES,
)
def create_scenario_suite(
    payload: CreateScenarioSuiteRequest,
    request: Request,
    response: Response,
):
    try:
        service, authenticated = _authenticated_service(request, response)
        return service.create_scenario_suite(
            authenticated,
            project_id=payload.project_id,
            build_id=payload.build_id,
            candidate_ids=payload.candidate_ids,
            name=payload.name,
            description=payload.description,
            retention_days=payload.retention_days,
            semantic_version=payload.semantic_version,
            input_schema_hash=payload.input_schema_hash,
            case_specs=[item.model_dump(mode="json") for item in payload.cases],
        )
    except Exception as exc:
        return studio_error_response(exc)


@router.post(
    "/scenario-suites/generate-edge-cases",
    response_model=list[ScenarioCase],
    responses=ERROR_RESPONSES,
)
def generate_scenario_edge_cases(
    payload: GenerateScenarioCasesRequest,
    request: Request,
    response: Response,
):
    try:
        service, authenticated = _authenticated_service(request, response)
        return service.generate_scenario_edge_cases(
            authenticated,
            project_id=payload.project_id,
            build_id=payload.build_id,
            candidate_ids=payload.candidate_ids,
            input_schema_hash=payload.input_schema_hash,
        )
    except Exception as exc:
        return studio_error_response(exc)


@router.post(
    "/scenario-file-fixtures",
    response_model=ScenarioFileFixture,
    status_code=201,
    responses=ERROR_RESPONSES,
)
def approve_scenario_file_fixture(
    payload: ApproveScenarioFixtureRequest,
    request: Request,
    response: Response,
):
    try:
        service, authenticated = _authenticated_service(request, response)
        return service.approve_scenario_file_fixture(
            authenticated,
            project_id=payload.project_id,
            name=payload.name,
            opaque_ref=payload.opaque_ref,
            media_type=payload.media_type,
            size_bytes=payload.size_bytes,
            content_hash=payload.content_hash.lower(),
            ttl_seconds=payload.ttl_seconds,
        )
    except Exception as exc:
        return studio_error_response(exc)


@router.post(
    "/scenario-runs",
    response_model=ScenarioRun,
    status_code=202,
    responses=ERROR_RESPONSES,
)
def run_scenario_suite(
    payload: RunScenarioSuiteRequest,
    request: Request,
    response: Response,
):
    try:
        service, authenticated = _authenticated_service(request, response)
        return service.run_scenario_suite(
            authenticated,
            project_id=payload.project_id,
            build_id=payload.build_id,
            suite_id=payload.suite_id,
            environment_id=payload.environment_id,
            candidate_ids=payload.candidate_ids,
            mappings=payload.mappings,
            policy=payload.policy,
        )
    except Exception as exc:
        return studio_error_response(exc)


@router.post(
    "/scenario-runs/{run_id}/approve-sanitized-source",
    response_model=ScenarioSanitizedRunApproval,
    status_code=201,
    responses=ERROR_RESPONSES,
)
def approve_sanitized_run_source(
    run_id: str,
    payload: ApproveSanitizedRunSourceRequest,
    request: Request,
    response: Response,
):
    try:
        service, authenticated = _authenticated_service(request, response)
        return service.approve_sanitized_run_source(
            authenticated,
            project_id=payload.project_id,
            run_id=run_id,
            ttl_seconds=payload.ttl_seconds,
        )
    except Exception as exc:
        return studio_error_response(exc)


@router.get(
    "/scenario-runs/{run_id}",
    response_model=ScenarioRun,
    responses=ERROR_RESPONSES,
)
def get_scenario_run(
    run_id: str,
    request: Request,
    response: Response,
    project_id: str = Query(min_length=1, max_length=128),
):
    try:
        service, authenticated = _authenticated_service(request, response)
        return service.get_scenario_run(
            authenticated,
            project_id=project_id,
            run_id=run_id,
        )
    except Exception as exc:
        return studio_error_response(exc)


@router.post(
    "/scenario-runs/{run_id}/cancel",
    response_model=ScenarioRun,
    responses=ERROR_RESPONSES,
)
def cancel_scenario_run(
    run_id: str,
    payload: ProjectActionRequest,
    request: Request,
    response: Response,
):
    try:
        service, authenticated = _authenticated_service(request, response)
        return service.cancel_scenario_run(
            authenticated,
            project_id=payload.project_id,
            run_id=run_id,
        )
    except Exception as exc:
        return studio_error_response(exc)


@router.post(
    "/scenario-runs/{run_id}/baseline",
    response_model=ScenarioBaseline,
    responses=ERROR_RESPONSES,
)
def save_scenario_baseline(
    run_id: str,
    payload: SaveScenarioBaselineRequest,
    request: Request,
    response: Response,
):
    try:
        service, authenticated = _authenticated_service(request, response)
        return service.save_scenario_baseline(
            authenticated,
            project_id=payload.project_id,
            run_id=run_id,
            candidate_id=payload.candidate_id,
        )
    except Exception as exc:
        return studio_error_response(exc)


@router.post(
    "/preview-fixtures/{fixture_id}/cleanup",
    response_model=PreviewFixture,
    responses=ERROR_RESPONSES,
)
def cleanup_preview_fixture(
    fixture_id: str,
    payload: ProjectActionRequest,
    request: Request,
    response: Response,
):
    try:
        service, authenticated = _authenticated_service(request, response)
        return service.cleanup_preview_fixture(
            authenticated,
            project_id=payload.project_id,
            fixture_id=fixture_id,
        )
    except Exception as exc:
        return studio_error_response(exc)


@router.post(
    "/preview-environments/reap",
    response_model=list[PreviewFixture],
    responses=ERROR_RESPONSES,
)
def reap_preview_fixtures(
    payload: ProjectActionRequest,
    request: Request,
    response: Response,
):
    try:
        service, authenticated = _authenticated_service(request, response)
        return service.reap_preview_fixtures(
            authenticated,
            project_id=payload.project_id,
        )
    except Exception as exc:
        return studio_error_response(exc)


@router.put(
    "/regression-gates",
    response_model=RegressionGate,
    responses=ERROR_RESPONSES,
)
def configure_regression_gate(
    payload: ConfigureRegressionGateRequest,
    request: Request,
    response: Response,
):
    try:
        service, authenticated = _authenticated_service(request, response)
        return service.configure_regression_gate(
            authenticated,
            project_id=payload.project_id,
            build_id=payload.build_id,
            suite_id=payload.suite_id,
            min_pass_rate=payload.min_pass_rate,
            min_quality_score=payload.min_quality_score,
            max_latency_regression_percent=payload.max_latency_regression_percent,
            max_cost_regression_percent=payload.max_cost_regression_percent,
            evidence_ttl_seconds=payload.evidence_ttl_seconds,
            required_policy=payload.required_policy,
        )
    except Exception as exc:
        return studio_error_response(exc)


@router.get(
    "/reviews",
    response_model=list[ChangeRequest],
    responses=ERROR_RESPONSES,
)
def list_change_requests(
    request: Request,
    response: Response,
    project_id: str = Query(min_length=1, max_length=128),
):
    try:
        service, authenticated = _authenticated_service(request, response)
        return service.review_list(authenticated, project_id=project_id)
    except Exception as exc:
        return studio_error_response(exc)


@router.get(
    "/reviews/{change_request_id}",
    response_model=ChangeRequestDetail,
    responses=ERROR_RESPONSES,
)
def get_change_request(
    change_request_id: str,
    request: Request,
    response: Response,
    project_id: str = Query(min_length=1, max_length=128),
):
    try:
        service, authenticated = _authenticated_service(request, response)
        return service.review_detail(
            authenticated,
            project_id=project_id,
            change_request_id=change_request_id,
        )
    except Exception as exc:
        return studio_error_response(exc)


@router.post(
    "/reviews",
    response_model=ChangeRequestDetail,
    status_code=201,
    responses=ERROR_RESPONSES,
)
def create_change_request(
    payload: CreateChangeRequestRequest,
    request: Request,
    response: Response,
):
    try:
        service, authenticated = _authenticated_service(request, response)
        return service.create_change_request(
            authenticated,
            project_id=payload.project_id,
            build_id=payload.build_id,
            candidate_id=payload.candidate_id,
            scenario_run_id=payload.scenario_run_id,
            title=payload.title,
            release_note=payload.release_note,
            assignee_key=payload.assignee_key,
            require_separation=payload.require_author_approver_separation,
            expires_in_seconds=payload.expires_in_seconds,
            repair_proposal_id=payload.repair_proposal_id,
            repair_proposal_version=payload.repair_proposal_version,
        )
    except Exception as exc:
        return studio_error_response(exc)


@router.post(
    "/reviews/{change_request_id}/comments",
    response_model=ChangeRequestDetail,
    responses=ERROR_RESPONSES,
)
def comment_change_request(
    change_request_id: str,
    payload: CommentChangeRequestRequest,
    request: Request,
    response: Response,
):
    try:
        service, authenticated = _authenticated_service(request, response)
        return service.comment_change_request(
            authenticated,
            project_id=payload.project_id,
            change_request_id=change_request_id,
            body=payload.body,
        )
    except Exception as exc:
        return studio_error_response(exc)


@router.post(
    "/reviews/{change_request_id}/assignment",
    response_model=ChangeRequestDetail,
    responses=ERROR_RESPONSES,
)
def assign_change_request(
    change_request_id: str,
    payload: AssignChangeRequestRequest,
    request: Request,
    response: Response,
):
    try:
        service, authenticated = _authenticated_service(request, response)
        return service.assign_change_request(
            authenticated,
            project_id=payload.project_id,
            change_request_id=change_request_id,
            assignee_key=payload.assignee_key,
            expected_version=payload.expected_version,
        )
    except Exception as exc:
        return studio_error_response(exc)


@router.post(
    "/reviews/{change_request_id}/decision",
    response_model=ChangeRequestDetail,
    responses=ERROR_RESPONSES,
)
def decide_change_request(
    change_request_id: str,
    payload: DecideChangeRequestRequest,
    request: Request,
    response: Response,
):
    try:
        service, authenticated = _authenticated_service(request, response)
        return service.decide_change_request(
            authenticated,
            project_id=payload.project_id,
            change_request_id=change_request_id,
            decision=payload.decision,
            body=payload.body,
            expected_version=payload.expected_version,
            expected_binding_hash=payload.expected_binding_hash,
        )
    except Exception as exc:
        return studio_error_response(exc)


@router.post(
    "/reviews/{change_request_id}/supersede",
    response_model=ChangeRequestDetail,
    status_code=201,
    responses=ERROR_RESPONSES,
)
def supersede_change_request(
    change_request_id: str,
    payload: SupersedeChangeRequestRequest,
    request: Request,
    response: Response,
):
    try:
        service, authenticated = _authenticated_service(request, response)
        return service.supersede_change_request(
            authenticated,
            project_id=payload.project_id,
            change_request_id=change_request_id,
            expected_version=payload.expected_version,
            build_id=payload.build_id,
            candidate_id=payload.candidate_id,
            scenario_run_id=payload.scenario_run_id,
            title=payload.title,
            release_note=payload.release_note,
            expires_in_seconds=payload.expires_in_seconds,
        )
    except Exception as exc:
        return studio_error_response(exc)


@router.get(
    "/release-center",
    response_model=ReleaseCenterView,
    responses=ERROR_RESPONSES,
)
def get_release_center(
    request: Request,
    response: Response,
    project_id: str = Query(min_length=1, max_length=128),
):
    try:
        service, authenticated = _authenticated_service(request, response)
        return service.release_center(authenticated, project_id=project_id)
    except Exception as exc:
        return studio_error_response(exc)


@router.post(
    "/logical-apps",
    response_model=LogicalApp,
    status_code=201,
    responses=ERROR_RESPONSES,
)
def create_logical_app(
    payload: CreateLogicalAppRequest,
    request: Request,
    response: Response,
):
    try:
        service, authenticated = _authenticated_service(request, response)
        return service.create_logical_app(
            authenticated,
            project_id=payload.project_id,
            name=payload.name,
            app_mode=payload.app_mode,
        )
    except Exception as exc:
        return studio_error_response(exc)


@router.post(
    "/release-environments",
    response_model=ReleaseEnvironment,
    status_code=201,
    responses=ERROR_RESPONSES,
)
def create_release_environment(
    payload: CreateReleaseEnvironmentRequest,
    request: Request,
    response: Response,
):
    try:
        service, authenticated = _authenticated_service(request, response)
        return service.create_release_environment(
            authenticated,
            project_id=payload.project_id,
            logical_app_id=payload.logical_app_id,
            name=payload.name,
            classification=payload.classification,
            target_app_ref=payload.target_app_ref,
        )
    except Exception as exc:
        return studio_error_response(exc)


@router.put(
    "/release-environments/{environment_id}/mappings",
    response_model=EnvironmentMappingSet,
    responses=ERROR_RESPONSES,
)
def configure_release_mapping(
    environment_id: str,
    payload: ConfigureReleaseMappingRequest,
    request: Request,
    response: Response,
):
    try:
        service, authenticated = _authenticated_service(request, response)
        return service.configure_release_mapping(
            authenticated,
            project_id=payload.project_id,
            environment_id=environment_id,
            mappings=payload.mappings,
            expected_version=payload.expected_version,
        )
    except Exception as exc:
        return studio_error_response(exc)


@router.post(
    "/release-preview",
    response_model=ReleasePreview,
    responses=ERROR_RESPONSES,
)
def create_release_preview(
    payload: ReleasePreviewRequest,
    request: Request,
    response: Response,
):
    try:
        service, authenticated = _authenticated_service(request, response)
        return service.release_preview(
            authenticated,
            project_id=payload.project_id,
            change_request_id=payload.change_request_id,
            environment_id=payload.environment_id,
        )
    except Exception as exc:
        return studio_error_response(exc)


@router.post(
    "/release-authorizations",
    response_model=ReleaseAuthorization,
    status_code=201,
    responses=ERROR_RESPONSES,
)
def authorize_release(
    payload: AuthorizeReleaseRequest,
    request: Request,
    response: Response,
):
    try:
        service, authenticated = _authenticated_service(request, response)
        return service.authorize_release(
            authenticated,
            project_id=payload.project_id,
            change_request_id=payload.change_request_id,
            environment_id=payload.environment_id,
            action=payload.action,
            confirmation=payload.confirmation,
            expires_in_seconds=payload.expires_in_seconds,
        )
    except Exception as exc:
        return studio_error_response(exc)


@router.post(
    "/release-executions",
    response_model=ReleaseRecord,
    status_code=202,
    responses=ERROR_RESPONSES,
)
def execute_release(
    payload: ExecuteReleaseRequest,
    request: Request,
    response: Response,
):
    try:
        service, authenticated = _authenticated_service(request, response)
        return service.execute_release(
            authenticated,
            project_id=payload.project_id,
            authorization_id=payload.authorization_id,
            idempotency_key=payload.idempotency_key,
        )
    except Exception as exc:
        return studio_error_response(exc)


@router.post(
    "/rollback-proposals",
    response_model=ChangeRequestDetail,
    status_code=201,
    responses=ERROR_RESPONSES,
)
def propose_rollback(
    payload: ProposeRollbackRequest,
    request: Request,
    response: Response,
):
    try:
        service, authenticated = _authenticated_service(request, response)
        return service.propose_rollback(
            authenticated,
            project_id=payload.project_id,
            artifact_id=payload.artifact_id,
            title=payload.title,
            release_note=payload.release_note,
            assignee_key=payload.assignee_key,
            require_separation=payload.require_author_approver_separation,
            expires_in_seconds=payload.expires_in_seconds,
        )
    except Exception as exc:
        return studio_error_response(exc)


@router.get(
    "/artifacts/{artifact_id}/git",
    response_model=GitArtifactBundle,
    responses=ERROR_RESPONSES,
)
def get_artifact_git_bundle(
    artifact_id: str,
    request: Request,
    response: Response,
    project_id: str = Query(min_length=1, max_length=128),
):
    try:
        service, authenticated = _authenticated_service(request, response)
        return service.artifact_git_bundle(
            authenticated,
            project_id=project_id,
            artifact_id=artifact_id,
        )
    except Exception as exc:
        return studio_error_response(exc)


@router.post(
    "/git/pull",
    response_model=ChangeRequestDetail,
    status_code=201,
    responses=ERROR_RESPONSES,
)
def pull_git_artifact(
    payload: GitPullArtifactRequest,
    request: Request,
    response: Response,
):
    try:
        service, authenticated = _authenticated_service(request, response)
        return service.pull_git_artifact(
            authenticated,
            project_id=payload.project_id,
            base_artifact_id=payload.base_artifact_id,
            expected_base_hash=payload.expected_base_hash,
            canonical=payload.canonical_json,
            content_hash=payload.content_hash,
            title=payload.title,
            release_note=payload.release_note,
            assignee_key=payload.assignee_key,
            expires_in_seconds=payload.expires_in_seconds,
        )
    except Exception as exc:
        return studio_error_response(exc)


@router.get(
    "/run-center",
    response_model=RunCenterView,
    responses=ERROR_RESPONSES,
)
def get_run_center(
    request: Request,
    response: Response,
    project_id: str = Query(min_length=1, max_length=128),
    logical_app_id: str | None = Query(default=None, max_length=128),
    environment_id: str | None = Query(default=None, max_length=128),
    artifact_id: str | None = Query(default=None, max_length=128),
    status: str | None = Query(
        default=None,
        pattern="^(running|succeeded|failed|stopped|partial_succeeded|unknown)$",
    ),
    error_code: str | None = Query(default=None, max_length=128),
    started_from: datetime | None = Query(default=None),
    started_to: datetime | None = Query(default=None),
):
    try:
        service, authenticated = _authenticated_service(request, response)
        return service.run_center(
            authenticated,
            project_id=project_id,
            logical_app_id=logical_app_id,
            environment_id=environment_id,
            artifact_id=artifact_id,
            status=status,
            error_code=error_code,
            started_from=started_from,
            started_to=started_to,
        )
    except Exception as exc:
        return studio_error_response(exc)


@router.post(
    "/run-center/refresh",
    response_model=ExecutionRefreshResult,
    responses=ERROR_RESPONSES,
)
def refresh_run_center(
    payload: RefreshRunEvidenceRequest,
    request: Request,
    response: Response,
):
    try:
        service, authenticated = _authenticated_service(request, response)
        return service.refresh_run_evidence(
            authenticated,
            project_id=payload.project_id,
            environment_id=payload.environment_id,
            limit_per_environment=payload.limit_per_environment,
        )
    except Exception as exc:
        return studio_error_response(exc)


@router.get(
    "/run-incidents/{incident_id}",
    response_model=RunIncidentDetail,
    responses=ERROR_RESPONSES,
)
def get_run_incident(
    incident_id: str,
    request: Request,
    response: Response,
    project_id: str = Query(min_length=1, max_length=128),
):
    try:
        service, authenticated = _authenticated_service(request, response)
        return service.run_incident(
            authenticated,
            project_id=project_id,
            incident_id=incident_id,
        )
    except Exception as exc:
        return studio_error_response(exc)


@router.post(
    "/run-incidents/{incident_id}/repair-proposals",
    response_model=RepairProposal,
    status_code=201,
    responses=ERROR_RESPONSES,
)
def create_run_repair_proposal(
    incident_id: str,
    payload: CreateRepairProposalRequest,
    request: Request,
    response: Response,
):
    try:
        service, authenticated = _authenticated_service(request, response)
        return service.create_repair_proposal(
            authenticated,
            project_id=payload.project_id,
            incident_id=incident_id,
            title=payload.title,
        )
    except Exception as exc:
        return studio_error_response(exc)


@router.get(
    "/run-automation",
    response_model=RunAutomationView,
    responses=ERROR_RESPONSES,
)
def get_run_automation(
    request: Request,
    response: Response,
    project_id: str = Query(min_length=1, max_length=128),
):
    try:
        service, authenticated = _authenticated_service(request, response)
        return service.run_automation(
            authenticated,
            project_id=project_id,
        )
    except Exception as exc:
        return studio_error_response(exc)


@router.post(
    "/run-alerts",
    response_model=RunAlertRule,
    status_code=201,
    responses=ERROR_RESPONSES,
)
def configure_run_alert(
    payload: ConfigureRunAlertRequest,
    request: Request,
    response: Response,
):
    try:
        service, authenticated = _authenticated_service(request, response)
        return service.configure_run_alert(
            authenticated,
            project_id=payload.project_id,
            name=payload.name,
            environment_id=payload.environment_id,
            stable_error_code=payload.stable_error_code,
            error_count_threshold=payload.error_count_threshold,
            failure_rate_threshold=payload.failure_rate_threshold,
            window_seconds=payload.window_seconds,
            adapter_ref=payload.adapter_ref,
            enabled=payload.enabled,
            rule_id=payload.rule_id,
            expected_version=payload.expected_version,
        )
    except Exception as exc:
        return studio_error_response(exc)


@router.post(
    "/scheduled-regressions",
    response_model=ScheduledRegression,
    status_code=201,
    responses=ERROR_RESPONSES,
)
def configure_scheduled_regression(
    payload: ConfigureScheduledRegressionRequest,
    request: Request,
    response: Response,
):
    try:
        service, authenticated = _authenticated_service(request, response)
        return service.configure_scheduled_regression(
            authenticated,
            project_id=payload.project_id,
            artifact_id=payload.artifact_id,
            suite_id=payload.suite_id,
            interval_seconds=payload.interval_seconds,
            enabled=payload.enabled,
            schedule_id=payload.schedule_id,
            expected_version=payload.expected_version,
        )
    except Exception as exc:
        return studio_error_response(exc)


@router.post(
    "/run-automation/tick",
    response_model=dict[str, int],
    responses=ERROR_RESPONSES,
)
def tick_run_automation(
    payload: ProjectActionRequest,
    request: Request,
    response: Response,
):
    try:
        service, authenticated = _authenticated_service(request, response)
        return service.tick_run_automation(
            authenticated,
            project_id=payload.project_id,
        )
    except Exception as exc:
        return studio_error_response(exc)


@router.post(
    "/durable-work/cancel",
    response_model=RunAutomationView,
    responses=ERROR_RESPONSES,
)
def cancel_durable_work(
    payload: CancelDurableWorkRequest,
    request: Request,
    response: Response,
):
    try:
        service, authenticated = _authenticated_service(request, response)
        return service.cancel_durable_work(
            authenticated,
            project_id=payload.project_id,
            entity_type=payload.entity_type,
            entity_id=payload.entity_id,
            reason=payload.reason,
        )
    except Exception as exc:
        return studio_error_response(exc)


@router.get(
    "/scoped-tokens",
    response_model=list[ScopedTokenRecord],
    responses=ERROR_RESPONSES,
)
def list_scoped_tokens(
    request: Request,
    response: Response,
    project_id: str = Query(min_length=1, max_length=128),
):
    try:
        service, authenticated = _authenticated_service(request, response)
        return service.list_scoped_tokens(
            authenticated,
            project_id=project_id,
        )
    except Exception as exc:
        return studio_error_response(exc)


@router.post(
    "/scoped-tokens",
    response_model=ScopedTokenIssued,
    status_code=201,
    responses=ERROR_RESPONSES,
)
def create_scoped_token(
    payload: CreateScopedTokenRequest,
    request: Request,
    response: Response,
):
    try:
        service, authenticated = _authenticated_service(request, response)
        return service.issue_scoped_token(
            authenticated,
            project_id=payload.project_id,
            name=payload.name,
            scopes=payload.scopes,
            expires_in_seconds=payload.expires_in_seconds,
            rate_limit_per_minute=payload.rate_limit_per_minute,
        )
    except Exception as exc:
        return studio_error_response(exc)


@router.post(
    "/scoped-tokens/{token_id}/rotate",
    response_model=ScopedTokenIssued,
    status_code=201,
    responses=ERROR_RESPONSES,
)
def rotate_scoped_token(
    token_id: str,
    payload: RotateScopedTokenRequest,
    request: Request,
    response: Response,
):
    try:
        service, authenticated = _authenticated_service(request, response)
        return service.rotate_scoped_token(
            authenticated,
            project_id=payload.project_id,
            token_id=token_id,
            expected_version=payload.expected_version,
            expires_in_seconds=payload.expires_in_seconds,
        )
    except Exception as exc:
        return studio_error_response(exc)


@router.post(
    "/scoped-tokens/{token_id}/revoke",
    response_model=ScopedTokenRecord,
    responses=ERROR_RESPONSES,
)
def revoke_scoped_token(
    token_id: str,
    payload: RevokeScopedTokenRequest,
    request: Request,
    response: Response,
):
    try:
        service, authenticated = _authenticated_service(request, response)
        return service.revoke_scoped_token(
            authenticated,
            project_id=payload.project_id,
            token_id=token_id,
            expected_version=payload.expected_version,
        )
    except Exception as exc:
        return studio_error_response(exc)


@router.post(
    "/mcp",
    responses=ERROR_RESPONSES,
)
def scoped_mcp(
    payload: dict[str, Any],
    request: Request,
):
    try:
        service = require_studio_service(request)
        mcp = service.require_mcp()
        token, authenticated = mcp.authenticate(
            request.headers.get("authorization")
        )
        return mcp.handle(
            token=token,
            authenticated=authenticated,
            request=payload,
        )
    except Exception as exc:
        return studio_error_response(exc)


def _authenticated_service(
    request: Request,
    response: Response,
) -> tuple[StudioApplicationService, Any]:
    service = require_studio_service(request)
    authenticated = service.authenticate(
        authorization=request.headers.get("authorization"),
        origin_header=request.headers.get("origin"),
        referer_header=request.headers.get("referer"),
        cookie_header=request.headers.get("cookie"),
    )
    _forward_dify_cookies(response, authenticated.host.set_cookie_headers)
    return service, authenticated


def require_studio_service(request: Request) -> StudioApplicationService:
    if not bool(getattr(request.app.state, "ai_studio_v5_enabled", False)):
        raise StudioRecordNotFound("The v5 AI Workflow Studio is disabled.")
    service = getattr(request.app.state, "studio_service", None)
    if not isinstance(service, StudioApplicationService):
        raise StudioHostUnavailable("The Studio service is not available.")
    return service


def studio_error_response(exc: Exception) -> JSONResponse:
    status_code, code, retryable = _error_shape(exc)
    if code == "STUDIO_INTERNAL_ERROR":
        message = "Studio could not complete this request."
    elif status_code >= 500 and isinstance(exc, StudioStoreError):
        message = "Studio persistence is temporarily unavailable."
    else:
        message = str(exc).strip() or "Studio could not complete this request."
    payload = StudioErrorEnvelope(
        error=StudioErrorDetail(
            code=code,
            message=message,
            retryable=retryable,
            request_id=str(uuid4()),
        )
    )
    return JSONResponse(
        status_code=status_code,
        content=payload.model_dump(mode="json"),
    )


def _forward_dify_cookies(
    response: Response,
    set_cookie_headers: list[str],
) -> None:
    for value in set_cookie_headers:
        response.headers.append("set-cookie", value)


def _error_shape(exc: Exception) -> tuple[int, str, bool]:
    if isinstance(exc, StudioRequestInvalid):
        return 422, exc.code, False
    if isinstance(exc, StudioReplayDetected):
        return 409, exc.code, False
    if isinstance(exc, StudioIdentityRequired):
        return 401, exc.code, False
    if isinstance(exc, McpAuthenticationRequired):
        return 401, exc.code, False
    if isinstance(exc, StudioIdentityExpired):
        return 401, exc.code, True
    if isinstance(exc, StudioHostSessionInvalid):
        return 401, exc.code, True
    if isinstance(exc, StudioOriginDenied):
        return 403, exc.code, False
    if isinstance(exc, StudioAccessDenied):
        return 403, exc.code, False
    if isinstance(exc, StudioRateLimited):
        return 429, exc.code, True
    if isinstance(exc, ReviewSelfApprovalDenied):
        return 403, exc.code, False
    if isinstance(exc, StudioRecordNotFound):
        code = (
            "AI_STUDIO_V5_DISABLED"
            if "disabled" in str(exc).lower()
            else exc.code
        )
        return 404, code, False
    if isinstance(exc, (StudioConflict, V4ContinuityError)):
        return 409, getattr(exc, "code", "STUDIO_CONFLICT"), False
    if isinstance(exc, ScenarioError):
        return 409, exc.code, False
    if isinstance(
        exc,
        (ArtifactError, ReviewError, ReleaseError, RunCenterError, RunAutomationError),
    ):
        return 409, exc.code, False
    if isinstance(exc, McpError):
        return 409, exc.code, False
    if isinstance(exc, PreviewAdapterError):
        return 503, exc.code, True
    if isinstance(exc, StudioHostUnavailable):
        return 503, exc.code, True
    if isinstance(exc, StudioIdentityError):
        return 401, exc.code, False
    if isinstance(exc, StudioStoreError):
        return 503, exc.code, True
    return 500, "STUDIO_INTERNAL_ERROR", False
