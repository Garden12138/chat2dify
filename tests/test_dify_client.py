import json

import pytest
import httpx

from app.config import Settings
from app.dify.client import CSRF_HEADER_NAME, DifyClient, DifyConflictError


def _settings() -> Settings:
    return Settings.from_env(
        {
            "DIFY_SOURCE_DIR": "../dify",
            "DIFY_CONSOLE_API_BASE": "http://dify.local/console/api",
            "DIFY_CONSOLE_WEB_BASE": "http://dify.local",
            "DIFY_EMAIL": "user@example.com",
            "DIFY_PASSWORD": "secret",
        },
        validate_dify=False,
    )


def test_login_encodes_password_and_import_sends_csrf_cookie() -> None:
    seen: dict[str, str] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/console/api/login":
            body = json.loads(request.content)
            seen["password"] = body["password"]
            return httpx.Response(
                200,
                json={"result": "success"},
                headers=[
                    ("set-cookie", "csrf_token=csrf123; Path=/"),
                    ("set-cookie", "access_token=access123; Path=/; HttpOnly"),
                    ("set-cookie", "refresh_token=refresh123; Path=/; HttpOnly"),
                ],
            )
        if request.url.path == "/console/api/apps/imports":
            seen["csrf"] = request.headers.get(CSRF_HEADER_NAME, "")
            seen["cookie"] = request.headers.get("cookie", "")
            return httpx.Response(
                200,
                json={"id": "import-1", "status": "completed", "app_id": "app-1", "app_mode": "workflow"},
            )
        raise AssertionError(f"Unexpected request: {request.method} {request.url}")

    client = DifyClient(_settings(), transport=httpx.MockTransport(handler))
    result = client.import_yaml("kind: app")

    assert seen["password"] == DifyClient.encode_password("secret")
    assert seen["csrf"] == "csrf123"
    assert "csrf_token=csrf123" in seen["cookie"]
    assert result.workflow_url == "http://dify.local/app/app-1/workflow"


def test_pending_import_is_confirmed() -> None:
    calls: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request.url.path)
        if request.url.path == "/console/api/login":
            return httpx.Response(200, json={"result": "success"}, headers=[("set-cookie", "csrf_token=csrf123; Path=/")])
        if request.url.path == "/console/api/apps/imports":
            return httpx.Response(202, json={"id": "import-1", "status": "pending"})
        if request.url.path == "/console/api/apps/imports/import-1/confirm":
            return httpx.Response(
                200,
                json={"id": "import-1", "status": "completed", "app_id": "app-2", "app_mode": "workflow"},
            )
        raise AssertionError(f"Unexpected request: {request.method} {request.url}")

    client = DifyClient(_settings(), transport=httpx.MockTransport(handler))
    result = client.import_yaml("kind: app")

    assert result.status == "completed"
    assert result.app_id == "app-2"
    assert "/console/api/apps/imports/import-1/confirm" in calls


def test_completed_with_warnings_is_returned_without_confirm() -> None:
    calls: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request.url.path)
        if request.url.path == "/console/api/login":
            return httpx.Response(200, json={"result": "success"}, headers=[("set-cookie", "csrf_token=csrf123; Path=/")])
        if request.url.path == "/console/api/apps/imports":
            return httpx.Response(
                200,
                json={
                    "id": "import-1",
                    "status": "completed-with-warnings",
                    "app_id": "app-3",
                    "app_mode": "workflow",
                    "leaked_dependencies": [{"type": "plugin"}],
                },
            )
        raise AssertionError(f"Unexpected request: {request.method} {request.url}")

    client = DifyClient(_settings(), transport=httpx.MockTransport(handler))
    result = client.import_yaml("kind: app")

    assert result.status == "completed-with-warnings"
    assert result.app_id == "app-3"
    assert result.leaked_dependencies == [{"type": "plugin"}]
    assert "/console/api/apps/imports/import-1/confirm" not in calls


def test_failed_import_payload_is_returned() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/console/api/login":
            return httpx.Response(200, json={"result": "success"}, headers=[("set-cookie", "csrf_token=csrf123; Path=/")])
        if request.url.path == "/console/api/apps/imports":
            return httpx.Response(400, json={"id": "import-1", "status": "failed", "error": "bad yaml"})
        raise AssertionError(f"Unexpected request: {request.method} {request.url}")

    client = DifyClient(_settings(), transport=httpx.MockTransport(handler))
    result = client.import_yaml("bad")

    assert result.status == "failed"
    assert result.error == "bad yaml"


def test_get_and_sync_draft_workflow_send_csrf_and_hash() -> None:
    seen: dict[str, object] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/console/api/login":
            return httpx.Response(200, json={"result": "success"}, headers=[("set-cookie", "csrf_token=csrf123; Path=/")])
        if request.url.path == "/console/api/apps/app-1/workflows/draft" and request.method == "GET":
            seen["get_csrf"] = request.headers.get(CSRF_HEADER_NAME, "")
            return httpx.Response(
                200,
                json={
                    "id": "workflow-1",
                    "graph": {"nodes": [], "edges": []},
                    "features": {"file_upload": {"enabled": False}},
                    "hash": "hash-1",
                    "version": "draft",
                    "environment_variables": [{"name": "ENV"}],
                    "conversation_variables": [{"name": "topic"}],
                },
            )
        if request.url.path == "/console/api/apps/app-1/workflows/draft" and request.method == "POST":
            body = json.loads(request.content)
            seen["post_csrf"] = request.headers.get(CSRF_HEADER_NAME, "")
            seen["post_hash"] = body["hash"]
            seen["environment_variables"] = body["environment_variables"]
            seen["conversation_variables"] = body["conversation_variables"]
            return httpx.Response(200, json={"result": "success", "hash": "hash-2", "updated_at": "123"})
        raise AssertionError(f"Unexpected request: {request.method} {request.url}")

    client = DifyClient(_settings(), transport=httpx.MockTransport(handler))
    draft = client.get_draft_workflow("app-1")
    result = client.sync_draft_workflow(
        "app-1",
        graph=draft.graph,
        features=draft.features,
        hash=draft.hash,
        environment_variables=draft.environment_variables,
        conversation_variables=draft.conversation_variables,
    )

    assert draft.hash == "hash-1"
    assert seen["get_csrf"] == "csrf123"
    assert seen["post_csrf"] == "csrf123"
    assert seen["post_hash"] == "hash-1"
    assert seen["environment_variables"] == [{"name": "ENV"}]
    assert seen["conversation_variables"] == [{"name": "topic"}]
    assert result.hash == "hash-2"
    assert result.workflow_url == "http://dify.local/app/app-1/workflow"


def test_sync_draft_workflow_conflict_is_typed() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/console/api/login":
            return httpx.Response(200, json={"result": "success"}, headers=[("set-cookie", "csrf_token=csrf123; Path=/")])
        if request.url.path == "/console/api/apps/app-1/workflows/draft":
            return httpx.Response(409, json={"code": "draft_workflow_not_sync"})
        raise AssertionError(f"Unexpected request: {request.method} {request.url}")

    client = DifyClient(_settings(), transport=httpx.MockTransport(handler))

    with pytest.raises(DifyConflictError):
        client.sync_draft_workflow("app-1", graph={"nodes": [], "edges": []}, features={}, hash="old")


def test_connection_error_is_wrapped() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("connection refused", request=request)

    client = DifyClient(_settings(), transport=httpx.MockTransport(handler))

    try:
        client.import_yaml("kind: app")
    except Exception as exc:
        assert exc.__class__.__name__ == "DifyClientError"
        assert "connection refused" in str(exc)
    else:
        raise AssertionError("DifyClient should wrap connection errors")
