from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from datetime import datetime
import json
import time
from typing import Literal

from fastapi import APIRouter, Depends, Header, HTTPException, Query, Request
from fastapi.responses import StreamingResponse
from pydantic import Field

from app.agent.approval import ApprovalServiceError
from app.agent.commit import CommitServiceError
from app.agent.service import AgentApplicationService
from app.agent.state import (
    AgentApproval,
    AgentBudget,
    AgentBudgetUsage,
    AgentRun,
    AgentSession,
    CanvasViewport,
    GoalPlan,
    RunConstraints,
    RunPhase,
    RunStatus,
    StrictModel,
)
from app.agent.store import AgentRecordNotFound, AgentStore
from app.agent.trace import AgentEvent, public_event_payload
from app.agent.undo import UndoServiceError


router = APIRouter(prefix="/api/v4/agent", tags=["agent-v4"])


class AgentSessionResponse(AgentSession):
    pass


class AgentRunResponse(StrictModel):
    id: str
    session_id: str
    task_id: str | None = None
    goal: str
    status: RunStatus
    phase: RunPhase
    base_hash: str | None = None
    head_version_id: str | None = None
    iteration: int
    budget: AgentBudget
    budget_usage: AgentBudgetUsage
    constraints: RunConstraints
    goal_plan: GoalPlan | None = None
    review: dict | None = None
    commit_result: dict | None = None
    error: dict | None = None
    created_at: datetime
    updated_at: datetime
    finished_at: datetime | None = None


class AgentEventResponse(AgentEvent):
    pass


class CreateAgentSessionRequest(StrictModel):
    app_id: str | None = Field(default=None, min_length=1, max_length=256)
    app_mode: Literal["workflow", "advanced-chat"]
    app_name: str | None = Field(default=None, min_length=1, max_length=512)
    app_description: str = Field(default="", max_length=8_000)


class SubmitAgentGoalRequest(StrictModel):
    message: str = Field(min_length=1, max_length=8_000)
    constraints: RunConstraints = Field(default_factory=RunConstraints)
    budget: AgentBudget | None = None


class ResumeAgentRunRequest(StrictModel):
    message: str | None = Field(default=None, max_length=8_000)


class CanvasContextUpdateRequest(StrictModel):
    protocol_version: Literal["1.0"] = "1.0"
    revision: int = Field(ge=1)
    selected_node_ids: list[str] = Field(default_factory=list, max_length=100)
    selected_edge_ids: list[str] = Field(default_factory=list, max_length=100)
    viewport: CanvasViewport | None = None
    current_panel: str | None = Field(default=None, max_length=128)
    dirty_state: bool = False
    canvas_draft_hash: str | None = Field(default=None, max_length=512)


class ResolveApprovalRequest(StrictModel):
    approved: bool


class ResolveApprovalResponse(StrictModel):
    approval: AgentApproval
    next_approval: AgentApproval | None = None


class CommitAgentRunRequest(StrictModel):
    workspace_version_id: str = Field(min_length=1, max_length=128)
    approval_id: str = Field(min_length=1, max_length=128)


class UndoAgentRunRequest(StrictModel):
    workspace_version_id: str = Field(min_length=1, max_length=128)


class UndoAgentRunResponse(StrictModel):
    kind: Literal["pre_commit", "post_commit"]
    source_run_id: str
    run: AgentRunResponse
    from_version_id: str
    workspace_version_id: str


def require_agent_store(request: Request) -> AgentStore:
    if not bool(getattr(request.app.state, "agent_v4_enabled", False)):
        raise HTTPException(
            status_code=404,
            detail={
                "code": "AGENT_V4_DISABLED",
                "message": "The v4 Builder Agent API is disabled.",
            },
        )
    store = getattr(request.app.state, "agent_store", None)
    if not isinstance(store, AgentStore):
        raise HTTPException(
            status_code=503,
            detail={
                "code": "AGENT_STORE_UNAVAILABLE",
                "message": "The v4 Builder Agent store is unavailable.",
            },
        )
    return store


def require_agent_service(request: Request) -> AgentApplicationService:
    require_agent_store(request)
    service = getattr(request.app.state, "agent_service", None)
    if not isinstance(service, AgentApplicationService):
        raise HTTPException(
            status_code=503,
            detail={
                "code": "AGENT_SERVICE_UNAVAILABLE",
                "message": "The v4 Builder Agent service is unavailable.",
            },
        )
    return service


@router.post("/sessions", response_model=AgentSessionResponse, status_code=201)
def create_session(
    payload: CreateAgentSessionRequest,
    service: AgentApplicationService = Depends(require_agent_service),
) -> AgentSession:
    try:
        return service.create_session(
            app_id=payload.app_id,
            app_mode=payload.app_mode,
            app_name=payload.app_name,
            app_description=payload.app_description,
        )
    except ValueError as exc:
        raise _api_error(422, "AGENT_SESSION_INVALID", str(exc)) from exc


@router.get("/sessions/{session_id}", response_model=AgentSessionResponse)
def get_session(
    session_id: str,
    store: AgentStore = Depends(require_agent_store),
) -> AgentSession:
    try:
        return store.get_session(session_id)
    except AgentRecordNotFound as exc:
        raise _not_found("AGENT_SESSION_NOT_FOUND", "Agent Session", session_id) from exc


@router.post(
    "/sessions/{session_id}/messages",
    response_model=AgentRunResponse,
    status_code=202,
)
def submit_goal(
    session_id: str,
    payload: SubmitAgentGoalRequest,
    service: AgentApplicationService = Depends(require_agent_service),
) -> AgentRun:
    try:
        return service.submit_goal(
            session_id,
            message=payload.message,
            constraints=payload.constraints,
            budget=payload.budget,
        )
    except AgentRecordNotFound as exc:
        raise _not_found(
            "AGENT_SESSION_NOT_FOUND",
            "Agent Session",
            session_id,
        ) from exc
    except ValueError as exc:
        raise _api_error(409, "AGENT_SESSION_STATE_INVALID", str(exc)) from exc


@router.get("/runs/{run_id}", response_model=AgentRunResponse)
def get_run(
    run_id: str,
    store: AgentStore = Depends(require_agent_store),
) -> AgentRun:
    try:
        return store.get_run(run_id)
    except AgentRecordNotFound as exc:
        raise _not_found("AGENT_RUN_NOT_FOUND", "Agent Run", run_id) from exc


@router.get("/runs/{run_id}/diff")
def get_run_diff(
    run_id: str,
    store: AgentStore = Depends(require_agent_store),
) -> dict:
    try:
        run = store.get_run(run_id)
    except AgentRecordNotFound as exc:
        raise _not_found("AGENT_RUN_NOT_FOUND", "Agent Run", run_id) from exc
    if run.review is None:
        raise _api_error(
            409,
            "AGENT_REVIEW_NOT_READY",
            "Agent Run does not have a persisted review yet.",
        )
    return run.review


@router.post("/runs/{run_id}/cancel", response_model=AgentRunResponse)
def cancel_run(
    run_id: str,
    service: AgentApplicationService = Depends(require_agent_service),
) -> AgentRun:
    try:
        return service.cancel(run_id)
    except AgentRecordNotFound as exc:
        raise _not_found("AGENT_RUN_NOT_FOUND", "Agent Run", run_id) from exc
    except ValueError as exc:
        raise _api_error(409, "AGENT_RUN_CANCEL_INVALID", str(exc)) from exc


@router.post("/runs/{run_id}/pause", response_model=AgentRunResponse)
def pause_run(
    run_id: str,
    service: AgentApplicationService = Depends(require_agent_service),
) -> AgentRun:
    try:
        return service.pause(run_id)
    except AgentRecordNotFound as exc:
        raise _not_found("AGENT_RUN_NOT_FOUND", "Agent Run", run_id) from exc
    except ValueError as exc:
        raise _api_error(409, "AGENT_RUN_PAUSE_INVALID", str(exc)) from exc


@router.post("/runs/{run_id}/resume", response_model=AgentRunResponse, status_code=202)
def resume_run(
    run_id: str,
    payload: ResumeAgentRunRequest,
    service: AgentApplicationService = Depends(require_agent_service),
) -> AgentRun:
    try:
        return service.resume(run_id, message=payload.message)
    except AgentRecordNotFound as exc:
        raise _not_found("AGENT_RUN_NOT_FOUND", "Agent Run", run_id) from exc
    except ValueError as exc:
        raise _api_error(409, "AGENT_RUN_RESUME_INVALID", str(exc)) from exc


@router.post("/runs/{run_id}/context", response_model=AgentRunResponse)
def update_run_canvas_context(
    run_id: str,
    payload: CanvasContextUpdateRequest,
    service: AgentApplicationService = Depends(require_agent_service),
) -> AgentRun:
    try:
        return service.update_canvas_context(
            run_id,
            selected_node_ids=payload.selected_node_ids,
            selected_edge_ids=payload.selected_edge_ids,
            viewport=payload.viewport,
            current_panel=payload.current_panel,
            dirty_state=payload.dirty_state,
            canvas_draft_hash=payload.canvas_draft_hash,
            revision=payload.revision,
        )
    except AgentRecordNotFound as exc:
        raise _not_found("AGENT_RUN_NOT_FOUND", "Agent Run", run_id) from exc
    except ValueError as exc:
        raise _api_error(409, "AGENT_CANVAS_CONTEXT_INVALID", str(exc)) from exc


@router.get(
    "/runs/{run_id}/approvals",
    response_model=list[AgentApproval],
)
def list_run_approvals(
    run_id: str,
    store: AgentStore = Depends(require_agent_store),
) -> list[AgentApproval]:
    try:
        store.get_run(run_id)
    except AgentRecordNotFound as exc:
        raise _not_found("AGENT_RUN_NOT_FOUND", "Agent Run", run_id) from exc
    return store.list_approvals(run_id)


@router.post(
    "/runs/{run_id}/approvals/{approval_id}",
    response_model=ResolveApprovalResponse,
)
def resolve_approval(
    run_id: str,
    approval_id: str,
    payload: ResolveApprovalRequest,
    service: AgentApplicationService = Depends(require_agent_service),
) -> ResolveApprovalResponse:
    try:
        approval, next_approval = service.resolve_approval(
            run_id,
            approval_id,
            approved=payload.approved,
        )
        return ResolveApprovalResponse(
            approval=approval,
            next_approval=next_approval,
        )
    except AgentRecordNotFound as exc:
        raise _not_found(
            "AGENT_APPROVAL_NOT_FOUND",
            "Agent Approval",
            approval_id,
        ) from exc
    except ApprovalServiceError as exc:
        raise _api_error(409, exc.code, str(exc)) from exc


@router.post("/runs/{run_id}/commit")
def commit_run(
    run_id: str,
    payload: CommitAgentRunRequest,
    service: AgentApplicationService = Depends(require_agent_service),
) -> dict:
    try:
        return service.commit(
            run_id,
            workspace_version_id=payload.workspace_version_id,
            approval_id=payload.approval_id,
        ).model_dump(mode="json")
    except AgentRecordNotFound as exc:
        raise _not_found("AGENT_RUN_NOT_FOUND", "Agent Run", run_id) from exc
    except CommitServiceError as exc:
        raise _api_error(exc.status_code, exc.code, str(exc)) from exc


@router.post("/runs/{run_id}/undo", response_model=UndoAgentRunResponse)
def undo_run(
    run_id: str,
    payload: UndoAgentRunRequest,
    service: AgentApplicationService = Depends(require_agent_service),
) -> UndoAgentRunResponse:
    try:
        result = service.undo(
            run_id,
            workspace_version_id=payload.workspace_version_id,
        )
        run_payload = result.run.model_dump(
            include=set(AgentRunResponse.model_fields),
        )
        return UndoAgentRunResponse(
            kind=result.kind,
            source_run_id=result.source_run_id,
            run=AgentRunResponse.model_validate(run_payload),
            from_version_id=result.from_version_id,
            workspace_version_id=result.workspace_version_id,
        )
    except AgentRecordNotFound as exc:
        raise _not_found("AGENT_RUN_NOT_FOUND", "Agent Run", run_id) from exc
    except UndoServiceError as exc:
        raise _api_error(exc.status_code, exc.code, str(exc)) from exc


@router.get("/runs/{run_id}/events")
def stream_run_events(
    request: Request,
    run_id: str,
    after_seq: int = Query(default=0, ge=0),
    follow: bool = Query(default=True),
    heartbeat_seconds: float = Query(default=15.0, ge=1.0, le=60.0),
    last_event_id: str | None = Header(default=None, alias="Last-Event-ID"),
    store: AgentStore = Depends(require_agent_store),
) -> StreamingResponse:
    try:
        store.get_run(run_id)
    except AgentRecordNotFound as exc:
        raise _not_found("AGENT_RUN_NOT_FOUND", "Agent Run", run_id) from exc
    cursor = max(after_seq, _parse_last_event_id(last_event_id))
    return StreamingResponse(
        iter_event_stream(
            request,
            store,
            run_id,
            after_seq=cursor,
            follow=follow,
            heartbeat_seconds=heartbeat_seconds,
        ),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


async def iter_event_stream(
    request: Request,
    store: AgentStore,
    run_id: str,
    *,
    after_seq: int,
    follow: bool,
    heartbeat_seconds: float,
) -> AsyncIterator[str]:
    cursor = after_seq
    next_heartbeat = time.monotonic() + heartbeat_seconds
    while True:
        events = store.list_events(run_id, after_seq=cursor)
        for event in events:
            cursor = event.seq
            yield format_sse_event(event)
        if not events and store.get_run(run_id).terminal:
            return
        if not follow:
            yield format_sse_heartbeat()
            return
        if await request.is_disconnected():
            return
        now = time.monotonic()
        if now >= next_heartbeat:
            yield format_sse_heartbeat()
            next_heartbeat = now + heartbeat_seconds
        await asyncio.sleep(min(0.25, max(0.01, next_heartbeat - now)))


def format_sse_event(event: AgentEvent) -> str:
    payload = json.dumps(
        public_event_payload(event),
        ensure_ascii=False,
        separators=(",", ":"),
    )
    return f"id: {event.seq}\nevent: {event.type}\ndata: {payload}\n\n"


def format_sse_heartbeat() -> str:
    return ": heartbeat\n\n"


def _parse_last_event_id(value: str | None) -> int:
    if value is None or not value.strip():
        return 0
    try:
        parsed = int(value)
    except ValueError as exc:
        raise HTTPException(
            status_code=400,
            detail={
                "code": "AGENT_EVENT_CURSOR_INVALID",
                "message": "Last-Event-ID must be a non-negative event sequence.",
            },
        ) from exc
    if parsed < 0:
        raise HTTPException(
            status_code=400,
            detail={
                "code": "AGENT_EVENT_CURSOR_INVALID",
                "message": "Last-Event-ID must be a non-negative event sequence.",
            },
        )
    return parsed


def _not_found(code: str, label: str, record_id: str) -> HTTPException:
    return HTTPException(
        status_code=404,
        detail={
            "code": code,
            "message": f"{label} not found: {record_id}",
        },
    )


def _api_error(status_code: int, code: str, message: str) -> HTTPException:
    return HTTPException(
        status_code=status_code,
        detail={"code": code, "message": message},
    )
