from __future__ import annotations

from typing import Any, Literal
from uuid import uuid4

from fastapi import APIRouter, Query, Request, Response
from fastapi.responses import JSONResponse
from pydantic import Field

from app.agent.service import AgentApplicationService
from app.agent.state import CanvasViewport, RunConstraints
from app.agent.store import AgentStore
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
    PreviewFixture,
    PreviewResourceMapping,
    Principal,
    Project,
    RegressionGate,
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
from app.studio.scenarios import ScenarioError
from app.studio.store import (
    StudioAccessDenied,
    StudioConflict,
    StudioRecordNotFound,
    StudioReplayDetected,
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


ERROR_RESPONSES = {
    422: {"model": StudioErrorEnvelope},
    401: {"model": StudioErrorEnvelope},
    403: {"model": StudioErrorEnvelope},
    404: {"model": StudioErrorEnvelope},
    409: {"model": StudioErrorEnvelope},
    503: {"model": StudioErrorEnvelope},
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
    if isinstance(exc, StudioIdentityExpired):
        return 401, exc.code, True
    if isinstance(exc, StudioHostSessionInvalid):
        return 401, exc.code, True
    if isinstance(exc, StudioOriginDenied):
        return 403, exc.code, False
    if isinstance(exc, StudioAccessDenied):
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
    if isinstance(exc, PreviewAdapterError):
        return 503, exc.code, True
    if isinstance(exc, StudioHostUnavailable):
        return 503, exc.code, True
    if isinstance(exc, StudioIdentityError):
        return 401, exc.code, False
    if isinstance(exc, StudioStoreError):
        return 503, exc.code, True
    return 500, "STUDIO_INTERNAL_ERROR", False
