from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any, Iterable, Iterator


TERMINAL_EVENTS = {"workflow_finished", "workflow_paused", "error"}


@dataclass(frozen=True)
class SseParseIssue:
    message: str
    raw: str


def iter_sse_events(lines: Iterable[str]) -> Iterator[dict[str, Any] | SseParseIssue]:
    data_lines: list[str] = []
    for raw_line in lines:
        line = raw_line.rstrip("\r\n")
        if not line:
            yield from _flush_data_lines(data_lines)
            data_lines = []
            continue
        if line.startswith(":"):
            continue
        if line.startswith("data:"):
            data_lines.append(line[5:].lstrip())
            continue
        if line.startswith("event:") or line.startswith("id:") or line.startswith("retry:"):
            continue
        if line.strip().startswith("{"):
            yield from _parse_payload(line.strip())
            continue
        if line.strip() == "ping":
            continue
        yield SseParseIssue(message="Unsupported SSE line.", raw=line)
    yield from _flush_data_lines(data_lines)


def summarize_events(events: list[dict[str, Any]], parse_errors: list[SseParseIssue]) -> dict[str, Any]:
    event_counts: dict[str, int] = {}
    node_started = 0
    node_finished = 0
    iteration_events = 0
    loop_events = 0
    for event in events:
        event_type = str(event.get("event", "unknown"))
        event_counts[event_type] = event_counts.get(event_type, 0) + 1
        if event_type == "node_started":
            node_started += 1
        elif event_type == "node_finished":
            node_finished += 1
        elif event_type in {"iteration_started", "iteration_next", "iteration_completed"}:
            iteration_events += 1
        elif event_type in {"loop_started", "loop_next", "loop_completed"}:
            loop_events += 1
    return {
        "events": len(events),
        "event_counts": event_counts,
        "node_started": node_started,
        "node_finished": node_finished,
        "iteration_events": iteration_events,
        "loop_events": loop_events,
        "parse_errors": len(parse_errors),
    }


def terminal_event(events: list[dict[str, Any]]) -> dict[str, Any] | None:
    for event in reversed(events):
        if event.get("event") in TERMINAL_EVENTS:
            return event
    return None


def _flush_data_lines(data_lines: list[str]) -> Iterator[dict[str, Any] | SseParseIssue]:
    if not data_lines:
        return
    payload = "\n".join(data_lines).strip()
    if not payload or payload == "ping":
        return
    yield from _parse_payload(payload)


def _parse_payload(payload: str) -> Iterator[dict[str, Any] | SseParseIssue]:
    try:
        data = json.loads(payload)
    except json.JSONDecodeError as exc:
        yield SseParseIssue(message=f"Invalid SSE JSON: {exc.msg}", raw=payload)
        return
    if isinstance(data, dict):
        event = data.get("event")
        if event == "ping" or data == {"event": "ping"}:
            return
        yield data
        return
    yield SseParseIssue(message="SSE JSON payload must be an object.", raw=payload)
