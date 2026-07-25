from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
import json
import time

from fastapi import APIRouter, Depends, Header, HTTPException, Query, Request
from fastapi.responses import StreamingResponse

from app.agent.state import AgentRun, AgentSession
from app.agent.store import AgentRecordNotFound, AgentStore
from app.agent.trace import AgentEvent, public_event_payload


router = APIRouter(prefix="/api/v4/agent", tags=["agent-v4"])


class AgentSessionResponse(AgentSession):
    pass


class AgentRunResponse(AgentRun):
    pass


class AgentEventResponse(AgentEvent):
    pass


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


@router.get("/sessions/{session_id}", response_model=AgentSessionResponse)
def get_session(
    session_id: str,
    store: AgentStore = Depends(require_agent_store),
) -> AgentSession:
    try:
        return store.get_session(session_id)
    except AgentRecordNotFound as exc:
        raise _not_found("AGENT_SESSION_NOT_FOUND", "Agent Session", session_id) from exc


@router.get("/runs/{run_id}", response_model=AgentRunResponse)
def get_run(
    run_id: str,
    store: AgentStore = Depends(require_agent_store),
) -> AgentRun:
    try:
        return store.get_run(run_id)
    except AgentRecordNotFound as exc:
        raise _not_found("AGENT_RUN_NOT_FOUND", "Agent Run", run_id) from exc


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
