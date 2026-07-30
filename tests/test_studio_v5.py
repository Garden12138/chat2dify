from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
import sqlite3

import httpx
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.agent.state import AgentRun, AgentSession, RunPhase
from app.agent.store import AgentStore
from app.api.studio_v5 import router
from app.config import Settings
from app.studio.home import StudioHomeService, V4ContinuityReader
from app.studio.identity import (
    DifyHostVerifier,
    StudioHostSessionInvalid,
    StudioIdentityService,
)
from app.studio.models import (
    DifyAppSummary,
    Principal,
    VerifiedHostContext,
)
from app.studio.service import StudioApplicationService
from app.studio.store import StudioStore
from app.dify.version import DifyVersionInfo
from app.main import app as main_app


ORIGIN = "https://dify.example"
COOKIE = "access_token=dify-session"


class FakeHostVerifier:
    def __init__(
        self,
        principal: Principal,
        apps: list[DifyAppSummary],
    ) -> None:
        self.principal = principal
        self.apps = apps
        self.apps_available = True

    def verify(
        self,
        cookie_header: str,
        *,
        app_name: str | None = None,
        app_mode: str | None = None,
    ) -> VerifiedHostContext:
        if COOKIE not in cookie_header:
            raise StudioHostSessionInvalid("Invalid Dify session.")
        apps = list(self.apps)
        if app_name:
            query = app_name.casefold()
            apps = [
                app
                for app in apps
                if query in app.name.casefold()
                or query in app.description.casefold()
            ]
        if app_mode:
            apps = [app for app in apps if app.mode == app_mode]
        return VerifiedHostContext(
            principal=self.principal,
            apps=apps if self.apps_available else [],
            apps_available=self.apps_available,
            apps_error_code=(
                None
                if self.apps_available
                else "STUDIO_DIFY_APPS_UNAVAILABLE"
            ),
        )


def _settings(tmp_path: Path) -> Settings:
    return Settings.from_env(
        {
            "DIFY_SOURCE_DIR": "../dify",
            "DIFY_CONSOLE_WEB_BASE": ORIGIN,
            "CHAT2DIFY_AGENT_V4_ENABLED": "true",
            "CHAT2DIFY_AI_STUDIO_V5_ENABLED": "true",
            "CHAT2DIFY_STUDIO_SIGNING_SECRET": "studio-test-signing-secret-" * 2,
            "CHAT2DIFY_STUDIO_ALLOWED_ORIGINS": ORIGIN,
            "CHAT2DIFY_TASK_DB": str(tmp_path / "tasks.sqlite3"),
            "CHAT2DIFY_STUDIO_DATABASE_URL": (
                f"sqlite:///{tmp_path / 'tasks.sqlite3'}"
            ),
        },
        project_root=tmp_path,
        validate_dify=False,
    )


def _principal(subject: str = "account-1", tenant: str = "tenant-1") -> Principal:
    return Principal(
        issuer="chat2dify-studio",
        subject=subject,
        display_name="Ada",
        email="ada@example.com",
        dify_tenant_id=tenant,
    )


def _apps() -> list[DifyAppSummary]:
    return [
        DifyAppSummary(
            id="app-workflow",
            name="Support Workflow",
            mode="workflow",
            description="Routes customer requests.",
            updated_at=datetime(2026, 7, 30, tzinfo=timezone.utc),
        ),
        DifyAppSummary(
            id="app-chat",
            name="Sales Assistant",
            mode="chat",
            description="Answers sales questions.",
            updated_at=datetime(2026, 7, 29, tzinfo=timezone.utc),
        ),
    ]


def _api_app(
    tmp_path: Path,
    *,
    enabled: bool = True,
) -> tuple[FastAPI, StudioStore, FakeHostVerifier]:
    settings = _settings(tmp_path)
    store = StudioStore(settings.studio_database_url)
    verifier = FakeHostVerifier(_principal(), _apps())
    service = StudioApplicationService(
        identity=StudioIdentityService(
            settings=settings,
            store=store,
            host_verifier=verifier,
        ),
        home=StudioHomeService(
            store=store,
            v4_reader=V4ContinuityReader(settings.task_db_path),
            public_base_path="/chat2dify",
        ),
    )
    application = FastAPI()
    application.include_router(router)
    application.state.ai_studio_v5_enabled = enabled
    application.state.studio_service = service if enabled else None
    application.state.agent_v4_enabled = True
    application.state.agent_store = None
    application.state.agent_service = None
    return application, store, verifier


def _issue(client: TestClient, nonce: str = "nonce-value-1234567890") -> dict:
    response = client.post(
        "/api/v5/studio/session",
        headers={"Origin": ORIGIN, "Cookie": COOKIE},
        json={"nonce": nonce},
    )
    assert response.status_code == 200, response.text
    return response.json()


def _auth_headers(token: str, **extra: str) -> dict[str, str]:
    return {
        "Origin": ORIGIN,
        "Cookie": COOKIE,
        "Authorization": f"Bearer {token}",
        **extra,
    }


def test_signed_dify_session_opens_personal_project_and_searches_home(
    tmp_path: Path,
) -> None:
    application, _, _ = _api_app(tmp_path)
    with TestClient(application, base_url=ORIGIN) as client:
        issued = _issue(client)
        home = client.get(
            "/api/v5/studio/home",
            params={
                "project_id": issued["project"]["id"],
                "search": "support",
                "app_mode": "workflow",
            },
            headers=_auth_headers(
                issued["token"],
                **{
                    "X-User": "forged-user",
                    "X-Role": "owner",
                    "X-Project": "forged-project",
                },
            ),
        )

    assert home.status_code == 200
    data = home.json()
    assert data["project"]["kind"] == "personal"
    assert data["membership"]["role"] == "owner"
    assert [app["id"] for app in data["apps"]] == ["app-workflow"]
    assert data["apps"][0]["build_url"].startswith(
        "/chat2dify/?studio=build"
    )
    assert "studio_entry=home" in data["apps"][0]["build_url"]
    assert data["assigned_reviews"] == []
    assert data["quality_regressions"] == []
    assert data["states"]["assigned_reviews"]["state"] == "empty"


def test_identity_forgery_nonce_replay_origin_and_browser_claims_are_rejected(
    tmp_path: Path,
) -> None:
    application, _, _ = _api_app(tmp_path)
    with TestClient(application, base_url=ORIGIN) as client:
        issued = _issue(client)
        replay = client.post(
            "/api/v5/studio/session",
            headers={"Origin": ORIGIN, "Cookie": COOKIE},
            json={"nonce": "nonce-value-1234567890"},
        )
        extra_claims = client.post(
            "/api/v5/studio/session",
            headers={"Origin": ORIGIN, "Cookie": COOKIE},
            json={
                "nonce": "different-nonce-1234567890",
                "user": "admin",
                "role": "owner",
                "project_id": "project-forged",
            },
        )
        token = issued["token"]
        forged = f"{token[:-2]}aa"
        forged_response = client.get(
            "/api/v5/studio/home",
            headers=_auth_headers(forged),
        )
        wrong_origin = client.get(
            "/api/v5/studio/home",
            headers={
                "Origin": "https://evil.example",
                "Cookie": COOKIE,
                "Authorization": f"Bearer {token}",
            },
        )

    assert replay.status_code == 409
    assert replay.json()["error"]["code"] == "STUDIO_IDENTITY_REPLAY"
    assert extra_claims.status_code == 422
    assert forged_response.status_code == 401
    assert forged_response.json()["error"]["code"] == "STUDIO_IDENTITY_INVALID"
    assert wrong_origin.status_code == 403
    assert wrong_origin.json()["error"]["code"] == "STUDIO_ORIGIN_DENIED"


def test_cross_project_read_is_denied_before_any_project_data_is_returned(
    tmp_path: Path,
) -> None:
    application, store, _ = _api_app(tmp_path)
    other = _principal("account-2")
    other_project, _ = store.create_project(
        name="Secret Project",
        dify_tenant_id=other.dify_tenant_id,
        owner=other,
    )
    store.append_activity(
        project_id=other_project.id,
        principal_key=other.key,
        kind="secret",
        entity_type="secret",
        entity_id="secret-1",
        summary={"secret_value": "must-not-leak"},
    )
    with TestClient(application, base_url=ORIGIN) as client:
        issued = _issue(client)
        response = client.get(
            "/api/v5/studio/home",
            params={"project_id": other_project.id},
            headers=_auth_headers(issued["token"]),
        )

    assert response.status_code == 403
    assert response.json()["error"]["code"] == "STUDIO_PROJECT_ACCESS_DENIED"
    assert "Secret Project" not in response.text
    assert "must-not-leak" not in response.text
    assert "Support Workflow" not in response.text


def test_current_dify_account_must_match_the_signed_studio_session(
    tmp_path: Path,
) -> None:
    application, _, verifier = _api_app(tmp_path)
    with TestClient(application, base_url=ORIGIN) as client:
        issued = _issue(client)
        verifier.principal = _principal("account-2")
        response = client.get(
            "/api/v5/studio/home",
            headers=_auth_headers(issued["token"]),
        )

    assert response.status_code == 403
    assert response.json()["error"]["code"] == "STUDIO_PROJECT_ACCESS_DENIED"


def test_home_links_only_v4_work_for_apps_visible_to_verified_dify_user(
    tmp_path: Path,
) -> None:
    settings = _settings(tmp_path)
    agent_store = AgentStore(settings.task_db_path)
    visible_session = agent_store.create_session(
        AgentSession(
            app_id="app-workflow",
            app_mode="workflow",
            app_name="Support Workflow",
        )
    )
    visible_run = agent_store.create_run(
        AgentRun(session_id=visible_session.id, goal="Add a safe fallback.")
    )
    agent_store.update_run(visible_run.transition_to(RunPhase.PAUSED))
    hidden_session = agent_store.create_session(
        AgentSession(
            app_id="hidden-app",
            app_mode="workflow",
            app_name="Hidden Workflow",
        )
    )
    agent_store.create_run(
        AgentRun(session_id=hidden_session.id, goal="Must stay hidden.")
    )
    application, _, _ = _api_app(tmp_path)
    application.state.agent_store = agent_store
    with TestClient(application, base_url=ORIGIN) as client:
        issued = _issue(client)
        response = client.get(
            "/api/v5/studio/home",
            headers=_auth_headers(issued["token"]),
        )

    assert response.status_code == 200
    work = response.json()["work"]
    assert len(work) == 1
    assert work[0]["run_id"] == visible_run.id
    assert work[0]["resumable"] is True
    assert f"run_id={visible_run.id}" in work[0]["build_url"]
    assert "Must stay hidden" not in response.text


def test_truthful_partial_app_state_and_disabled_flag_return_no_studio_data(
    tmp_path: Path,
) -> None:
    application, _, verifier = _api_app(tmp_path)
    verifier.apps_available = False
    with TestClient(application, base_url=ORIGIN) as client:
        issued = _issue(client)
        partial = client.get(
            "/api/v5/studio/home",
            headers=_auth_headers(issued["token"]),
        )
    disabled_app, _, _ = _api_app(tmp_path / "disabled", enabled=False)
    with TestClient(disabled_app, base_url=ORIGIN) as client:
        disabled = client.get("/api/v5/studio/home")

    assert partial.status_code == 200
    assert partial.json()["apps"] == []
    assert partial.json()["states"]["apps"] == {
        "state": "partial_error",
        "message": "身份已验证，但 Dify 应用列表暂时不可用。",
        "recoverable": True,
    }
    assert disabled.status_code == 404
    assert disabled.json()["error"]["code"] == "AI_STUDIO_V5_DISABLED"
    assert "Support Workflow" not in disabled.text


def test_dify_host_verifier_forwards_cookie_and_parses_current_workspace_apps(
    tmp_path: Path,
) -> None:
    settings = _settings(tmp_path)
    seen_cookies: list[str] = []
    seen_csrf: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen_cookies.append(request.headers.get("cookie", ""))
        seen_csrf.append(request.headers.get("x-csrf-token", ""))
        if request.url.path.endswith("/account/profile"):
            return httpx.Response(
                200,
                json={"id": "account-1", "name": "Ada", "email": "ada@example.com"},
            )
        if request.url.path.endswith("/workspaces"):
            return httpx.Response(
                200,
                json={
                    "workspaces": [
                        {
                            "id": "tenant-1",
                            "name": "Team",
                            "current": True,
                        }
                    ]
                },
            )
        if request.url.path.endswith("/apps"):
            return httpx.Response(
                200,
                json={
                    "data": [
                        {
                            "id": "app-1",
                            "name": "Support",
                            "mode": "workflow",
                            "description": "Help",
                            "updated_at": 1785427200,
                        }
                    ],
                    "has_more": False,
                },
            )
        return httpx.Response(404)

    verifier = DifyHostVerifier(
        settings,
        transport=httpx.MockTransport(handler),
    )
    verified = verifier.verify(f"{COOKIE}; csrf_token=dify-csrf")

    assert verified.principal.subject == "account-1"
    assert verified.principal.dify_tenant_id == "tenant-1"
    assert [app.id for app in verified.apps] == ["app-1"]
    assert all(COOKIE in value for value in seen_cookies)
    assert seen_csrf == ["dify-csrf", "dify-csrf", "dify-csrf"]


def test_dify_host_verifier_refreshes_expired_browser_session_and_forwards_cookies(
    tmp_path: Path,
) -> None:
    settings = _settings(tmp_path)
    profile_calls = 0
    seen_cookies: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal profile_calls
        seen_cookies.append(request.headers.get("cookie", ""))
        if request.url.path.endswith("/account/profile"):
            profile_calls += 1
            if profile_calls == 1:
                assert request.headers.get("x-csrf-token") == "old-csrf"
                return httpx.Response(401, json={"code": "unauthorized"})
            assert request.headers.get("x-csrf-token") == "fresh-csrf"
            return httpx.Response(
                200,
                json={"id": "account-1", "name": "Ada", "email": "ada@example.com"},
            )
        if request.url.path.endswith("/refresh-token"):
            return httpx.Response(
                200,
                headers=[
                    (
                        "set-cookie",
                        "access_token=fresh-access; HttpOnly; Path=/; SameSite=Lax",
                    ),
                    (
                        "set-cookie",
                        "refresh_token=fresh-refresh; HttpOnly; Path=/; SameSite=Lax",
                    ),
                    (
                        "set-cookie",
                        "csrf_token=fresh-csrf; Path=/; SameSite=Lax",
                    ),
                    ("set-cookie", "unrelated=must-not-forward; Path=/"),
                ],
                json={"result": "success"},
            )
        if request.url.path.endswith("/workspaces"):
            return httpx.Response(
                200,
                json={
                    "workspaces": [
                        {
                            "id": "tenant-1",
                            "name": "Team",
                            "current": True,
                        }
                    ]
                },
            )
        if request.url.path.endswith("/apps"):
            return httpx.Response(
                200,
                json={"data": [], "has_more": False},
            )
        return httpx.Response(404)

    verifier = DifyHostVerifier(
        settings,
        transport=httpx.MockTransport(handler),
    )
    verified = verifier.verify(
        "access_token=expired; refresh_token=valid-refresh; "
        "csrf_token=old-csrf; unrelated=ignored"
    )

    assert verified.principal.subject == "account-1"
    assert profile_calls == 2
    assert "access_token=fresh-access" in seen_cookies[-1]
    assert len(verified.set_cookie_headers) == 3
    assert all("unrelated=" not in value for value in verified.set_cookie_headers)
    assert "set_cookie_headers" not in verified.model_dump()


def test_main_flag_off_does_not_start_studio_and_schema_survives_rollback(
    tmp_path: Path,
    monkeypatch,
) -> None:
    enabled_settings = _settings(tmp_path)
    disabled_settings = Settings.from_env(
        {
            "DIFY_SOURCE_DIR": "../dify",
            "DIFY_CONSOLE_WEB_BASE": ORIGIN,
            "CHAT2DIFY_AGENT_V4_ENABLED": "false",
            "CHAT2DIFY_AI_STUDIO_V5_ENABLED": "false",
            "CHAT2DIFY_TASK_DB": str(tmp_path / "tasks.sqlite3"),
        },
        project_root=tmp_path,
        validate_dify=False,
    )
    monkeypatch.setattr(
        "app.main.read_dify_version_info",
        lambda _path: DifyVersionInfo(
            source_dir="../dify",
            git_describe="test",
            app_dsl_version="9.9.9",
        ),
    )
    monkeypatch.setattr("app.main.load_settings", lambda: enabled_settings)
    with TestClient(main_app, base_url=ORIGIN) as client:
        enabled_index = client.get("/")
        invalid = client.post(
            "/api/v5/studio/session",
            headers={"Origin": ORIGIN},
            json={"nonce": "short", "role": "owner"},
        )
        assert main_app.state.ai_studio_v5_enabled is True
        assert main_app.state.studio_service is not None
        assert '"studioV5Enabled": true' in enabled_index.text
        assert invalid.status_code == 422
        assert invalid.json()["error"]["code"] == "STUDIO_REQUEST_INVALID"
        assert "role" not in invalid.text

    monkeypatch.setattr("app.main.load_settings", lambda: disabled_settings)
    with TestClient(main_app, base_url=ORIGIN) as client:
        disabled_index = client.get("/")
        disabled_api = client.get("/api/v5/studio/home")
        assert main_app.state.ai_studio_v5_enabled is False
        assert main_app.state.studio_service is None
        assert main_app.state.agent_service is None

    assert '"studioV5Enabled": false' in disabled_index.text
    assert disabled_api.status_code == 404
    assert disabled_api.json()["error"]["code"] == "AI_STUDIO_V5_DISABLED"
    with sqlite3.connect(tmp_path / "tasks.sqlite3") as connection:
        table = connection.execute(
            """
            SELECT name FROM sqlite_master
            WHERE type = 'table' AND name = 'studio_projects'
            """
        ).fetchone()
    assert table == ("studio_projects",)
