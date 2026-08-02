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
    Membership,
    BuildStudioView,
    Principal,
    Project,
    StrictModel,
    StudioHome,
)
from app.studio.service import StudioApplicationService
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
    if isinstance(exc, StudioHostUnavailable):
        return 503, exc.code, True
    if isinstance(exc, StudioIdentityError):
        return 401, exc.code, False
    if isinstance(exc, StudioStoreError):
        return 503, exc.code, True
    return 500, "STUDIO_INTERNAL_ERROR", False
