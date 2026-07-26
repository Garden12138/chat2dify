from __future__ import annotations

from datetime import datetime
import re
from typing import Any, Literal

from pydantic import Field

from app.agent.state import StrictModel, new_id, utc_now


AgentEventType = Literal[
    "agent.started",
    "context.loaded",
    "context.updated",
    "goal_plan.created",
    "goal_plan.updated",
    "agent.decision",
    "tool.started",
    "tool.completed",
    "workspace.version.created",
    "workspace.head.moved",
    "validation.started",
    "validation.failed",
    "validation.passed",
    "repair.started",
    "test.approval_required",
    "test.started",
    "test.progress",
    "test.completed",
    "review.ready",
    "approval.required",
    "approval.resolved",
    "commit.started",
    "commit.completed",
    "agent.paused",
    "agent.resumed",
    "agent.completed",
    "agent.failed",
]

AGENT_EVENT_TYPES = frozenset(AgentEventType.__args__)
REDACTED = "[REDACTED]"
_SENSITIVE_KEY_MARKERS = (
    "api_key",
    "apikey",
    "authorization",
    "cookie",
    "credential",
    "password",
    "private_key",
    "secret",
    "access_token",
    "refresh_token",
    "environment_value",
)
_BEARER_PATTERN = re.compile(r"(?i)\bBearer\s+[A-Za-z0-9._~+/=-]+")


class AgentEvent(StrictModel):
    id: str = Field(default_factory=new_id, min_length=1, max_length=128)
    seq: int = Field(ge=1)
    run_id: str = Field(min_length=1, max_length=128)
    type: AgentEventType
    timestamp: datetime = Field(default_factory=utc_now)
    phase: str = Field(min_length=1, max_length=128)
    message: str = Field(min_length=1, max_length=8_000)
    data: dict[str, Any] = Field(default_factory=dict)


def redact_sensitive_data(value: Any) -> Any:
    if isinstance(value, dict):
        redacted: dict[str, Any] = {}
        for key, item in value.items():
            key_text = str(key)
            normalized = key_text.strip().lower().replace("-", "_")
            if any(marker in normalized for marker in _SENSITIVE_KEY_MARKERS):
                redacted[key_text] = REDACTED
            else:
                redacted[key_text] = redact_sensitive_data(item)
        return redacted
    if isinstance(value, list):
        return [redact_sensitive_data(item) for item in value]
    if isinstance(value, tuple):
        return [redact_sensitive_data(item) for item in value]
    if isinstance(value, str):
        return _BEARER_PATTERN.sub(f"Bearer {REDACTED}", value)
    if value is None or isinstance(value, (int, float, bool)):
        return value
    return str(value)


def public_event_payload(event: AgentEvent) -> dict[str, Any]:
    return redact_sensitive_data(event.model_dump(mode="json"))
