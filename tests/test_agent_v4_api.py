from __future__ import annotations

from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.agent.state import AgentRun, AgentSession
from app.agent.store import AgentStore
from app.api.agent_v4 import router
from app.config import Settings
from app.dify.version import DifyVersionInfo
from app.main import app as main_app


def _api_app(store: AgentStore, *, enabled: bool) -> FastAPI:
    application = FastAPI()
    application.include_router(router)
    application.state.agent_v4_enabled = enabled
    application.state.agent_store = store if enabled else None
    return application


def test_v4_router_is_registered_but_disabled_by_default(tmp_path) -> None:
    store = AgentStore(tmp_path / "tasks.sqlite3")
    application = _api_app(store, enabled=False)

    with TestClient(application) as client:
        response = client.get("/api/v4/agent/runs/missing")

    assert response.status_code == 404
    assert response.json()["detail"]["code"] == "AGENT_V4_DISABLED"
    assert "/api/v4/agent/runs/{run_id}" in main_app.openapi()["paths"]


def test_v4_polling_reads_persisted_session_and_run(tmp_path) -> None:
    store = AgentStore(tmp_path / "tasks.sqlite3")
    session = store.create_session(AgentSession(app_id="app-1", app_mode="workflow"))
    run = store.create_run(AgentRun(session_id=session.id, goal="Read only."))
    application = _api_app(store, enabled=True)

    with TestClient(application) as client:
        session_response = client.get(f"/api/v4/agent/sessions/{session.id}")
        run_response = client.get(f"/api/v4/agent/runs/{run.id}")
        missing_response = client.get("/api/v4/agent/runs/missing")

    assert session_response.status_code == 200
    assert session_response.json()["id"] == session.id
    assert run_response.status_code == 200
    assert run_response.json()["phase"] == "queued"
    assert missing_response.status_code == 404
    assert missing_response.json()["detail"]["code"] == "AGENT_RUN_NOT_FOUND"


def test_main_lifespan_initializes_agent_store_only_when_enabled(
    tmp_path,
    monkeypatch,
) -> None:
    settings = Settings.from_env(
        {
            "DIFY_SOURCE_DIR": "../dify",
            "CHAT2DIFY_AGENT_V4_ENABLED": "true",
            "CHAT2DIFY_TASK_DB": str(tmp_path / "tasks.sqlite3"),
            "CHAT2DIFY_TASK_WORKERS": "1",
        },
        project_root=tmp_path,
        validate_dify=False,
    )
    monkeypatch.setattr("app.main.load_settings", lambda: settings)
    monkeypatch.setattr(
        "app.main.read_dify_version_info",
        lambda _path: DifyVersionInfo(
            source_dir="../dify",
            git_describe="test",
            app_dsl_version="9.9.9",
        ),
    )

    with TestClient(main_app) as client:
        session = main_app.state.agent_store.create_session(
            AgentSession(app_id="app-1", app_mode="workflow")
        )
        run = main_app.state.agent_store.create_run(
            AgentRun(session_id=session.id, goal="Read through the main app.")
        )
        response = client.get(f"/api/v4/agent/runs/{run.id}")

    assert main_app.state.agent_v4_enabled is True
    assert response.status_code == 200
    assert response.json()["id"] == run.id


def test_sse_reconnect_resumes_after_sequence_without_duplicates(tmp_path) -> None:
    store = AgentStore(tmp_path / "tasks.sqlite3")
    session = store.create_session(AgentSession(app_id="app-1", app_mode="workflow"))
    run = store.create_run(AgentRun(session_id=session.id, goal="Stream safely."))
    store.append_event(
        run_id=run.id,
        event_type="agent.started",
        phase="observing",
        message="Started.",
        data={"safe": "first"},
    )
    store.append_event(
        run_id=run.id,
        event_type="tool.completed",
        phase="acting",
        message="Inspected.",
        data={"password": "must-not-stream", "safe": "second"},
    )
    store.append_event(
        run_id=run.id,
        event_type="validation.passed",
        phase="validating",
        message="Valid.",
        data={"safe": "third"},
    )
    application = _api_app(store, enabled=True)

    with TestClient(application) as client:
        response = client.get(
            f"/api/v4/agent/runs/{run.id}/events?follow=false",
            headers={"Last-Event-ID": "1"},
        )
        invalid = client.get(
            f"/api/v4/agent/runs/{run.id}/events?follow=false",
            headers={"Last-Event-ID": "not-a-sequence"},
        )

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/event-stream")
    assert "id: 1\n" not in response.text
    assert response.text.count("id: 2\n") == 1
    assert response.text.count("id: 3\n") == 1
    assert response.text.index("id: 2\n") < response.text.index("id: 3\n")
    assert ": heartbeat\n\n" in response.text
    assert "must-not-stream" not in response.text
    assert "[REDACTED]" in response.text
    assert invalid.status_code == 400
    assert invalid.json()["detail"]["code"] == "AGENT_EVENT_CURSOR_INVALID"
