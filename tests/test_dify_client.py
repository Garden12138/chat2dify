import json

import pytest
import httpx

from app.config import Settings
from app.dify.client import CSRF_HEADER_NAME, DifyClient, DifyClientError, DifyConflictError
from app.tasks import TaskCancelled


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


def test_get_app_detail_returns_console_app_metadata() -> None:
    seen: dict[str, str] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/console/api/login":
            return httpx.Response(200, json={"result": "success"}, headers=[("set-cookie", "csrf_token=csrf123; Path=/")])
        if request.url.path == "/console/api/apps/app-1":
            seen["csrf"] = request.headers.get(CSRF_HEADER_NAME, "")
            return httpx.Response(
                200,
                json={
                    "id": "app-1",
                    "name": "修车售后服务工作流",
                    "mode": "workflow",
                    "description": "Handle repair after-sales requests.",
                },
            )
        raise AssertionError(f"Unexpected request: {request.method} {request.url}")

    client = DifyClient(_settings(), transport=httpx.MockTransport(handler))
    detail = client.get_app_detail("app-1")

    assert seen["csrf"] == "csrf123"
    assert detail.id == "app-1"
    assert detail.name == "修车售后服务工作流"
    assert detail.mode == "workflow"
    assert detail.description == "Handle repair after-sales requests."


def test_list_datasets_sends_query_and_returns_slim_metadata() -> None:
    seen: dict[str, object] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/console/api/login":
            return httpx.Response(200, json={"result": "success"}, headers=[("set-cookie", "csrf_token=csrf123; Path=/")])
        if request.url.path == "/console/api/datasets":
            seen["csrf"] = request.headers.get(CSRF_HEADER_NAME, "")
            seen["page"] = request.url.params.get("page")
            seen["limit"] = request.url.params.get("limit")
            seen["keyword"] = request.url.params.get("keyword")
            seen["include_all"] = request.url.params.get("include_all")
            return httpx.Response(
                200,
                json={
                    "data": [
                        {
                            "id": "dataset-1",
                            "name": "售后政策",
                            "description": "门店售后政策",
                            "document_count": 3,
                            "total_document_count": 5,
                            "provider": "vendor",
                            "runtime_mode": "general",
                            "indexing_technique": "high_quality",
                            "embedding_available": True,
                            "permission": "all_team_members",
                            "updated_at": 123,
                            "retrieval_model_dict": {
                                "reranking_enable": True,
                                "reranking_model": {
                                    "reranking_provider_name": "langgenius/tongyi/tongyi",
                                    "reranking_model_name": "qwen3-rerank",
                                },
                            },
                            "unused": "ignored",
                        }
                    ],
                    "has_more": True,
                    "page": 2,
                    "limit": 10,
                    "total": 21,
                },
            )
        raise AssertionError(f"Unexpected request: {request.method} {request.url}")

    client = DifyClient(_settings(), transport=httpx.MockTransport(handler))
    result = client.list_datasets(keyword="售后", page=2, limit=10, include_all=True)

    assert seen == {"csrf": "csrf123", "page": "2", "limit": "10", "keyword": "售后", "include_all": "true"}
    assert result.has_more is True
    assert result.page == 2
    assert result.limit == 10
    assert result.total == 21
    assert result.data[0].id == "dataset-1"
    assert result.data[0].name == "售后政策"
    assert result.data[0].document_count == 3
    assert result.data[0].embedding_available is True
    assert result.data[0].retrieval_model_dict == {
        "reranking_enable": True,
        "reranking_model": {
            "reranking_provider_name": "langgenius/tongyi/tongyi",
            "reranking_model_name": "qwen3-rerank",
        },
    }


def test_get_datasets_by_ids_sends_repeated_ids_and_returns_retrieval_model() -> None:
    seen: dict[str, object] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/console/api/login":
            return httpx.Response(200, json={"result": "success"}, headers=[("set-cookie", "csrf_token=csrf123; Path=/")])
        if request.url.path == "/console/api/datasets":
            seen["ids"] = request.url.params.get_list("ids")
            seen["page"] = request.url.params.get("page")
            seen["limit"] = request.url.params.get("limit")
            return httpx.Response(
                200,
                json={
                    "data": [
                        {
                            "id": "dataset-1",
                            "name": "售后政策",
                            "description": "",
                            "retrieval_model_dict": {
                                "reranking_enable": True,
                                "reranking_model": {
                                    "reranking_provider_name": "langgenius/tongyi/tongyi",
                                    "reranking_model_name": "qwen3-rerank",
                                },
                            },
                        }
                    ],
                    "has_more": False,
                    "page": 1,
                    "limit": 50,
                    "total": 1,
                },
            )
        raise AssertionError(f"Unexpected request: {request.method} {request.url}")

    client = DifyClient(_settings(), transport=httpx.MockTransport(handler))
    result = client.get_datasets_by_ids(["dataset-1", "dataset-2"])

    assert seen == {"ids": ["dataset-1", "dataset-2"], "page": "1", "limit": "50"}
    assert result.data[0].retrieval_model_dict == {
        "reranking_enable": True,
        "reranking_model": {
            "reranking_provider_name": "langgenius/tongyi/tongyi",
            "reranking_model_name": "qwen3-rerank",
        },
    }


def test_list_datasets_refreshes_after_401() -> None:
    calls: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(f"{request.method} {request.url.path}")
        if request.url.path == "/console/api/login":
            return httpx.Response(200, json={"result": "success"}, headers=[("set-cookie", "csrf_token=csrf123; Path=/")])
        if request.url.path == "/console/api/refresh-token":
            return httpx.Response(200, json={"result": "success"})
        if request.url.path == "/console/api/datasets":
            if calls.count("GET /console/api/datasets") == 1:
                return httpx.Response(401, json={"message": "expired"})
            return httpx.Response(200, json={"data": [], "has_more": False, "page": 1, "limit": 50, "total": 0})
        raise AssertionError(f"Unexpected request: {request.method} {request.url}")

    client = DifyClient(_settings(), transport=httpx.MockTransport(handler))
    result = client.list_datasets()

    assert result.data == []
    assert calls.count("GET /console/api/datasets") == 2
    assert "POST /console/api/refresh-token" in calls


def test_list_datasets_invalid_json_is_wrapped() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/console/api/login":
            return httpx.Response(200, json={"result": "success"}, headers=[("set-cookie", "csrf_token=csrf123; Path=/")])
        if request.url.path == "/console/api/datasets":
            return httpx.Response(200, text="not json")
        raise AssertionError(f"Unexpected request: {request.method} {request.url}")

    client = DifyClient(_settings(), transport=httpx.MockTransport(handler))

    with pytest.raises(DifyClientError, match="Invalid Dify JSON response"):
        client.list_datasets()


def test_list_tools_sends_csrf_and_returns_flattened_tools() -> None:
    seen: dict[str, object] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/console/api/login":
            return httpx.Response(200, json={"result": "success"}, headers=[("set-cookie", "csrf_token=csrf123; Path=/")])
        if request.url.path == "/console/api/workspaces/current/tools/builtin":
            seen["csrf"] = request.headers.get(CSRF_HEADER_NAME, "")
            return httpx.Response(
                200,
                json=[
                    {
                        "id": "provider-1",
                        "name": "websearch",
                        "label": {"zh_Hans": "网页搜索", "en_US": "Web Search"},
                        "type": "builtin",
                        "is_team_authorization": True,
                        "allow_delete": True,
                        "plugin_id": "plugin-1",
                        "plugin_unique_identifier": "unique-1",
                        "tools": [
                            {
                                "name": "search",
                                "label": {"zh_Hans": "搜索"},
                                "description": {"zh_Hans": "搜索网页"},
                                "parameters": [
                                    {"name": "query", "form": "llm", "type": "string", "required": True}
                                ],
                                "output_schema": {"properties": {"answer": {"type": "string"}}},
                            }
                        ],
                    }
                ],
            )
        raise AssertionError(f"Unexpected request: {request.method} {request.url}")

    client = DifyClient(_settings(), transport=httpx.MockTransport(handler))
    result = client.list_tools(provider_type="builtin", keyword="搜索")

    assert seen["csrf"] == "csrf123"
    assert result.count == 1
    assert result.types == ["builtin"]
    assert result.data[0].provider_id == "provider-1"
    assert result.data[0].provider_type == "builtin"
    assert result.data[0].tool_name == "search"
    assert result.data[0].tool_label == "搜索"
    assert result.data[0].parameters[0]["name"] == "query"
    assert result.data[0].output_schema == {"properties": {"answer": {"type": "string"}}}


def test_list_tools_refreshes_after_401() -> None:
    calls: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(f"{request.method} {request.url.path}")
        if request.url.path == "/console/api/login":
            return httpx.Response(200, json={"result": "success"}, headers=[("set-cookie", "csrf_token=csrf123; Path=/")])
        if request.url.path == "/console/api/refresh-token":
            return httpx.Response(200, json={"result": "success"})
        if request.url.path == "/console/api/workspaces/current/tools/api":
            if calls.count("GET /console/api/workspaces/current/tools/api") == 1:
                return httpx.Response(401, json={"message": "expired"})
            return httpx.Response(200, json=[])
        raise AssertionError(f"Unexpected request: {request.method} {request.url}")

    client = DifyClient(_settings(), transport=httpx.MockTransport(handler))
    result = client.list_tools(provider_type="api")

    assert result.data == []
    assert calls.count("GET /console/api/workspaces/current/tools/api") == 2
    assert "POST /console/api/refresh-token" in calls


def test_list_tools_invalid_json_is_wrapped() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/console/api/login":
            return httpx.Response(200, json={"result": "success"}, headers=[("set-cookie", "csrf_token=csrf123; Path=/")])
        if request.url.path == "/console/api/workspaces/current/tools/builtin":
            return httpx.Response(200, text="not json")
        raise AssertionError(f"Unexpected request: {request.method} {request.url}")

    client = DifyClient(_settings(), transport=httpx.MockTransport(handler))

    with pytest.raises(DifyClientError, match="Invalid Dify JSON response"):
        client.list_tools(provider_type="builtin")


def test_list_agent_strategies_returns_flattened_strategies() -> None:
    seen: dict[str, str] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/console/api/login":
            return httpx.Response(200, json={"result": "success"}, headers=[("set-cookie", "csrf_token=csrf123; Path=/")])
        if request.url.path == "/console/api/workspaces/current/agent-providers":
            seen["csrf"] = request.headers.get(CSRF_HEADER_NAME, "")
            return httpx.Response(
                200,
                json=[
                    {
                        "provider": "langgenius/agent/react",
                        "plugin_id": "langgenius/agent",
                        "plugin_unique_identifier": "langgenius/agent:1.0.0",
                        "declaration": {"identity": {"name": "langgenius/agent/react"}, "strategies": []},
                    }
                ],
            )
        if request.url.path == "/console/api/workspaces/current/agent-provider/langgenius/agent/react":
            return httpx.Response(
                200,
                json={
                    "provider": "react",
                    "plugin_id": "langgenius/agent",
                    "plugin_unique_identifier": "langgenius/agent:1.0.0",
                    "meta": {"version": "1.0.0"},
                    "declaration": {
                        "identity": {"name": "langgenius/agent/react", "label": {"zh_Hans": "智能体"}},
                        "strategies": [
                            {
                                "identity": {
                                    "provider": "langgenius/agent/react",
                                    "name": "react",
                                    "label": {"zh_Hans": "ReAct"},
                                },
                                "description": {"zh_Hans": "多步推理"},
                                "parameters": [
                                    {"name": "query", "type": "text-input", "required": True},
                                    {"name": "tools", "type": "array[tools]", "required": True},
                                ],
                                "features": ["history-messages"],
                                "output_schema": {"properties": {"answer": {"type": "string"}}},
                            }
                        ],
                    },
                },
            )
        raise AssertionError(f"Unexpected request: {request.method} {request.url}")

    client = DifyClient(_settings(), transport=httpx.MockTransport(handler))
    result = client.list_agent_strategies(keyword="react")

    assert seen["csrf"] == "csrf123"
    assert result.count == 1
    assert result.providers == ["langgenius/agent/react"]
    assert result.data[0].agent_strategy_name == "react"
    assert result.data[0].agent_strategy_label == "ReAct"
    assert result.data[0].parameters[1]["type"] == "array[tools]"
    assert result.data[0].output_schema == {"properties": {"answer": {"type": "string"}}}


def test_list_agent_strategies_refreshes_after_401() -> None:
    calls: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(f"{request.method} {request.url.path}")
        if request.url.path == "/console/api/login":
            return httpx.Response(200, json={"result": "success"}, headers=[("set-cookie", "csrf_token=csrf123; Path=/")])
        if request.url.path == "/console/api/refresh-token":
            return httpx.Response(200, json={"result": "success"})
        if request.url.path == "/console/api/workspaces/current/agent-providers":
            if calls.count("GET /console/api/workspaces/current/agent-providers") == 1:
                return httpx.Response(401, json={"message": "expired"})
            return httpx.Response(200, json=[])
        raise AssertionError(f"Unexpected request: {request.method} {request.url}")

    client = DifyClient(_settings(), transport=httpx.MockTransport(handler))
    result = client.list_agent_strategies()

    assert result.data == []
    assert calls.count("GET /console/api/workspaces/current/agent-providers") == 2
    assert "POST /console/api/refresh-token" in calls


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


def test_run_draft_workflow_consumes_sse_and_sends_csrf_cookie() -> None:
    seen: dict[str, object] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/console/api/login":
            return httpx.Response(200, json={"result": "success"}, headers=[("set-cookie", "csrf_token=csrf123; Path=/")])
        if request.url.path == "/console/api/apps/app-1/workflows/draft/run":
            body = json.loads(request.content)
            seen["inputs"] = body["inputs"]
            seen["files"] = body["files"]
            seen["csrf"] = request.headers.get(CSRF_HEADER_NAME, "")
            seen["cookie"] = request.headers.get("cookie", "")
            return httpx.Response(
                200,
                content=(
                    'data: {"event":"workflow_started","task_id":"task-1","workflow_run_id":"run-1"}\n\n'
                    'data: {"event":"node_started","workflow_run_id":"run-1"}\n\n'
                    'data: {"event":"node_finished","workflow_run_id":"run-1"}\n\n'
                    'data: {"event":"workflow_finished","task_id":"task-1","workflow_run_id":"run-1",'
                    '"data":{"status":"succeeded","outputs":{"answer":"ok"},"elapsed_time":1.2,'
                    '"total_tokens":12,"total_steps":3}}\n\n'
                ),
                headers={"content-type": "text/event-stream"},
            )
        raise AssertionError(f"Unexpected request: {request.method} {request.url}")

    client = DifyClient(_settings(), transport=httpx.MockTransport(handler))
    result = client.run_draft_workflow(
        "app-1",
        inputs={"query": "hi"},
        files=[{"type": "image"}],
    )

    assert result.ok is True
    assert result.status == "succeeded"
    assert result.outputs == {"answer": "ok"}
    assert result.workflow_run_id == "run-1"
    assert result.task_id == "task-1"
    assert result.total_tokens == 12
    assert result.total_steps == 3
    assert result.events_summary["node_started"] == 1
    assert seen["inputs"] == {"query": "hi"}
    assert seen["files"] == [{"type": "image"}]
    assert seen["csrf"] == "csrf123"
    assert "csrf_token=csrf123" in seen["cookie"]


def test_run_draft_workflow_reports_events_and_honors_cancellation() -> None:
    seen_events: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/console/api/login":
            return httpx.Response(200, json={"result": "success"}, headers=[("set-cookie", "csrf_token=csrf123; Path=/")])
        if request.url.path == "/console/api/apps/app-1/workflows/draft/run":
            return httpx.Response(
                200,
                content=(
                    'data: {"event":"workflow_started","task_id":"task-1"}\n\n'
                    'data: {"event":"node_started","data":{"node_id":"llm"}}\n\n'
                ),
                headers={"content-type": "text/event-stream"},
            )
        raise AssertionError(f"Unexpected request: {request.method} {request.url}")

    def on_event(event, _summary):
        seen_events.append(event["event"])

    def cancellation_check():
        if seen_events:
            raise TaskCancelled("cancelled")

    client = DifyClient(_settings(), transport=httpx.MockTransport(handler))
    with pytest.raises(TaskCancelled):
        client.run_draft_workflow(
            "app-1",
            inputs={},
            cancellation_check=cancellation_check,
            event_callback=on_event,
        )

    assert seen_events == ["workflow_started"]


def test_run_draft_workflow_failed_status_is_not_ok() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/console/api/login":
            return httpx.Response(200, json={"result": "success"}, headers=[("set-cookie", "csrf_token=csrf123; Path=/")])
        if request.url.path == "/console/api/apps/app-1/workflows/draft/run":
            return httpx.Response(
                200,
                content=(
                    'data: {"event":"workflow_finished","workflow_run_id":"run-1",'
                    '"data":{"status":"failed","error":"bad input","outputs":{}}}\n\n'
                ),
                headers={"content-type": "text/event-stream"},
            )
        raise AssertionError(f"Unexpected request: {request.method} {request.url}")

    client = DifyClient(_settings(), transport=httpx.MockTransport(handler))
    result = client.run_draft_workflow("app-1", inputs={"query": "hi"})

    assert result.ok is False
    assert result.status == "failed"
    assert result.error == "bad input"


def test_run_draft_workflow_paused_status_is_not_ok() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/console/api/login":
            return httpx.Response(200, json={"result": "success"}, headers=[("set-cookie", "csrf_token=csrf123; Path=/")])
        if request.url.path == "/console/api/apps/app-1/workflows/draft/run":
            return httpx.Response(
                200,
                content=(
                    'data: {"event":"workflow_paused","workflow_run_id":"run-1","task_id":"task-1",'
                    '"data":{"reason":{"node_id":"review","node_type":"human-input","form_content":"Approve?"}}}\n\n'
                ),
                headers={"content-type": "text/event-stream"},
            )
        raise AssertionError(f"Unexpected request: {request.method} {request.url}")

    client = DifyClient(_settings(), transport=httpx.MockTransport(handler))
    result = client.run_draft_workflow("app-1", inputs={"query": "hi"})

    assert result.ok is False
    assert result.status == "paused"
    assert result.workflow_run_id == "run-1"
    assert result.task_id == "task-1"
    assert result.final_event and result.final_event["event"] == "workflow_paused"
    assert result.events_summary and result.events_summary["event_counts"]["workflow_paused"] == 1


def test_run_draft_workflow_refreshes_after_401() -> None:
    calls: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(f"{request.method} {request.url.path}")
        if request.url.path == "/console/api/login":
            return httpx.Response(200, json={"result": "success"}, headers=[("set-cookie", "csrf_token=csrf123; Path=/")])
        if request.url.path == "/console/api/refresh-token":
            return httpx.Response(200, json={"result": "success"})
        if request.url.path == "/console/api/apps/app-1/workflows/draft/run":
            if calls.count("POST /console/api/apps/app-1/workflows/draft/run") == 1:
                return httpx.Response(401, json={"message": "expired"})
            return httpx.Response(
                200,
                content='data: {"event":"workflow_finished","workflow_run_id":"run-1","data":{"status":"succeeded"}}\n\n',
                headers={"content-type": "text/event-stream"},
            )
        raise AssertionError(f"Unexpected request: {request.method} {request.url}")

    client = DifyClient(_settings(), transport=httpx.MockTransport(handler))
    result = client.run_draft_workflow("app-1", inputs={})

    assert result.ok is True
    assert calls.count("POST /console/api/apps/app-1/workflows/draft/run") == 2
    assert "POST /console/api/refresh-token" in calls


def test_run_draft_workflow_dify_error_is_wrapped() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/console/api/login":
            return httpx.Response(200, json={"result": "success"}, headers=[("set-cookie", "csrf_token=csrf123; Path=/")])
        if request.url.path == "/console/api/apps/app-1/workflows/draft/run":
            return httpx.Response(500, text="boom")
        raise AssertionError(f"Unexpected request: {request.method} {request.url}")

    client = DifyClient(_settings(), transport=httpx.MockTransport(handler))

    with pytest.raises(Exception) as exc:
        client.run_draft_workflow("app-1", inputs={})

    assert exc.value.__class__.__name__ == "DifyClientError"
    assert "boom" in str(exc.value)


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


def test_publish_and_manage_workflow_triggers() -> None:
    seen: list[tuple[str, str, dict | None]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content) if request.content else None
        seen.append((request.method, request.url.path, body))
        if request.url.path == "/console/api/login":
            return httpx.Response(
                200,
                json={"result": "success"},
                headers=[("set-cookie", "csrf_token=csrf123; Path=/")],
            )
        if request.url.path == "/console/api/apps/app-1/workflows/publish":
            return httpx.Response(200, json={"result": "success", "created_at": "2026-06-09T09:00:00"})
        if request.url.path == "/console/api/apps/app-1/triggers":
            return httpx.Response(
                200,
                json={
                    "data": [
                        {
                            "id": "trigger-1",
                            "trigger_type": "trigger-webhook",
                            "title": "接收售后请求",
                            "node_id": "entry",
                            "provider_name": "",
                            "icon": "",
                            "status": "enabled",
                        }
                    ]
                },
            )
        if request.url.path == "/console/api/apps/app-1/trigger-enable":
            return httpx.Response(
                200,
                json={
                    "id": "trigger-1",
                    "trigger_type": "trigger-webhook",
                    "title": "接收售后请求",
                    "node_id": "entry",
                    "provider_name": "",
                    "icon": "",
                    "status": "disabled",
                },
            )
        if request.url.path == "/console/api/apps/app-1/workflows/triggers/webhook":
            assert request.url.params["node_id"] == "entry"
            return httpx.Response(
                200,
                json={
                    "id": "webhook-trigger-1",
                    "webhook_id": "hook-1",
                    "webhook_url": "http://dify.local/hook-1",
                    "webhook_debug_url": "http://dify.local/debug/hook-1",
                    "node_id": "entry",
                },
            )
        raise AssertionError(f"Unexpected request: {request.method} {request.url}")

    client = DifyClient(_settings(), transport=httpx.MockTransport(handler))
    published = client.publish_workflow("app-1", marked_name="v1", marked_comment="trigger release")
    triggers = client.list_workflow_triggers("app-1")
    updated = client.set_workflow_trigger_status("app-1", "trigger-1", enabled=False)
    webhook = client.get_webhook_trigger("app-1", "entry")

    assert published.result == "success"
    assert triggers[0].status == "enabled"
    assert updated.status == "disabled"
    assert webhook.webhook_url == "http://dify.local/hook-1"
    assert (
        "POST",
        "/console/api/apps/app-1/workflows/publish",
        {"marked_name": "v1", "marked_comment": "trigger release"},
    ) in seen
    assert (
        "POST",
        "/console/api/apps/app-1/trigger-enable",
        {"trigger_id": "trigger-1", "enable_trigger": False},
    ) in seen
