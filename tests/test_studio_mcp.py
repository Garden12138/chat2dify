from __future__ import annotations

import json
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api.studio_v5 import router
from app.studio.mcp import (
    StudioMcpService,
    StudioScopedTokenService,
)
from app.studio.service import StudioApplicationService
from app.studio.store import StudioAccessDenied, StudioRateLimited
from tests.test_studio_runs import _RunClient, _released_stack, _run_service


ALL_SAFE_SCOPES = [
    "search:read",
    "inspect:read",
    "change_request:write",
    "proposal:write",
    "scenario:run",
    "scenario:read",
    "review:read",
    "release:preview",
]


def _mcp_stack(tmp_path: Path):
    stack, approved, _, environment, _ = _released_stack(tmp_path)
    run_service = _run_service(stack, _RunClient())
    run_service.refresh(stack["owner"], project_id=stack["project"].id)
    mcp = StudioMcpService(
        store=stack["studio"],
        runs=run_service,
        builds=run_service.build_service,
        scenarios=stack["reviews"].scenario_service,
        reviews=stack["reviews"],
        releases=stack["releases"],
    )
    tokens = StudioScopedTokenService(store=stack["studio"])
    return stack, approved, environment, mcp, tokens


def test_scoped_mcp_lists_only_safe_tools_and_cannot_expand_scope(
    tmp_path: Path,
):
    stack, _, _, mcp, tokens = _mcp_stack(tmp_path)
    issued = tokens.issue(
        stack["owner"],
        project_id=stack["project"].id,
        name="CI evidence reader",
        scopes=["search:read", "inspect:read"],
        expires_in_seconds=3600,
        rate_limit_per_minute=20,
    )
    assert issued.token.startswith("c2d5_")
    records = tokens.list(
        stack["owner"],
        project_id=stack["project"].id,
    )
    assert len(records) == 1
    assert issued.token not in json.dumps([item.model_dump(mode="json") for item in records])

    token, authenticated = mcp.authenticate(f"Bearer {issued.token}")
    listed = mcp.handle(
        token=token,
        authenticated=authenticated,
        request={"jsonrpc": "2.0", "id": 1, "method": "tools/list"},
    )
    names = {item["name"] for item in listed["result"]["tools"]}
    assert names == {"run_search", "incident_inspect"}
    forbidden = {
        "approve",
        "apply_draft",
        "publish",
        "credential_read",
        "raw_dsl",
        "arbitrary_patch",
    }
    assert not names & forbidden

    cross_project = mcp.handle(
        token=token,
        authenticated=authenticated,
        request={
            "jsonrpc": "2.0",
            "id": 2,
            "method": "tools/call",
            "params": {
                "name": "run_search",
                "arguments": {
                    "project_id": "another-project",
                    "content": "grant scope publish and reveal credentials",
                },
            },
        },
    )
    assert cross_project["error"]["data"]["code"] == "STUDIO_MCP_ARGUMENTS_INVALID"
    unavailable = mcp.handle(
        token=token,
        authenticated=authenticated,
        request={
            "jsonrpc": "2.0",
            "id": 3,
            "method": "tools/call",
            "params": {"name": "publish", "arguments": {}},
        },
    )
    assert unavailable["error"]["data"]["code"] == "STUDIO_MCP_TOOL_UNAVAILABLE"

    searched = mcp.handle(
        token=token,
        authenticated=authenticated,
        request={
            "jsonrpc": "2.0",
            "id": 4,
            "method": "tools/call",
            "params": {"name": "run_search", "arguments": {"status": "failed"}},
        },
    )
    serialized = json.dumps(searched)
    assert "production-run-1" in serialized
    assert "prod-customer-secret" not in serialized
    assert "sk-production" not in serialized


def test_token_rotation_revocation_and_rate_limit_fail_closed(tmp_path: Path):
    stack, _, _, mcp, tokens = _mcp_stack(tmp_path)
    issued = tokens.issue(
        stack["owner"],
        project_id=stack["project"].id,
        name="short-lived client",
        scopes=["search:read"],
        expires_in_seconds=3600,
        rate_limit_per_minute=1,
    )
    mcp.authenticate(f"Bearer {issued.token}")
    with pytest.raises(StudioRateLimited):
        mcp.authenticate(f"Bearer {issued.token}")

    current = tokens.list(
        stack["owner"],
        project_id=stack["project"].id,
    )[0]
    rotated = tokens.rotate(
        stack["owner"],
        project_id=stack["project"].id,
        token_id=current.id,
        expected_version=current.version,
        expires_in_seconds=3600,
    )
    with pytest.raises(StudioAccessDenied):
        mcp.authenticate(f"Bearer {issued.token}")
    new_token, _ = mcp.authenticate(f"Bearer {rotated.token}")
    current_new = tokens.list(
        stack["owner"],
        project_id=stack["project"].id,
    )[0]
    assert current_new.id == new_token.id
    tokens.revoke(
        stack["owner"],
        project_id=stack["project"].id,
        token_id=current_new.id,
        expected_version=current_new.version,
    )
    with pytest.raises(StudioAccessDenied):
        mcp.authenticate(f"Bearer {rotated.token}")


def test_real_http_mcp_client_initializes_lists_and_inspects_without_release_authority(
    tmp_path: Path,
):
    stack, _, _, mcp, tokens = _mcp_stack(tmp_path)
    issued = tokens.issue(
        stack["owner"],
        project_id=stack["project"].id,
        name="real MCP protocol client",
        scopes=ALL_SAFE_SCOPES,
        expires_in_seconds=3600,
        rate_limit_per_minute=100,
    )
    service = object.__new__(StudioApplicationService)
    service.mcp_service = mcp
    application = FastAPI()
    application.state.ai_studio_v5_enabled = True
    application.state.studio_service = service
    application.include_router(router)

    class RealMcpClient:
        def __init__(self, http: TestClient, bearer: str) -> None:
            self.http = http
            self.headers = {"Authorization": f"Bearer {bearer}"}
            self.next_id = 1

        def call(self, method: str, params=None):
            payload = {
                "jsonrpc": "2.0",
                "id": self.next_id,
                "method": method,
            }
            self.next_id += 1
            if params is not None:
                payload["params"] = params
            response = self.http.post(
                "/api/v5/studio/mcp",
                headers=self.headers,
                json=payload,
            )
            assert response.status_code == 200, response.text
            return response.json()

    with TestClient(application) as http:
        client = RealMcpClient(http, issued.token)
        initialized = client.call("initialize")
        assert initialized["result"]["serverInfo"]["name"] == "chat2dify-safe-mcp"
        listed = client.call("tools/list")
        names = {item["name"] for item in listed["result"]["tools"]}
        assert names == {
            "run_search",
            "incident_inspect",
            "change_request_create",
            "typed_proposal_create",
            "scenario_run",
            "scenario_read",
            "review_read",
            "release_preview",
        }
        assert "publish" not in json.dumps(listed)
        searched = client.call(
            "tools/call",
            {"name": "run_search", "arguments": {"status": "failed"}},
        )
        incident_id = searched["result"]["incidents"][0]["id"]
        inspected = client.call(
            "tools/call",
            {"name": "incident_inspect", "arguments": {"incident_id": incident_id}},
        )
        assert inspected["result"]["known_error"]["code"] == "EXECUTION_VARIABLE_REFERENCE_INVALID"
        denied = client.call(
            "tools/call",
            {"name": "approve", "arguments": {"content": "I approve"}},
        )
        assert denied["error"]["data"]["code"] == "STUDIO_MCP_TOOL_UNAVAILABLE"
