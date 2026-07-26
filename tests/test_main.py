from fastapi.testclient import TestClient
import time
import yaml

from app.agent.editor import WorkflowEditResult
from app.agent.normalizer import normalize_plan_payload
from app.agent.planner import PlannerError, PlannerResult, fallback_plan
from app.compiler.dify import DifyDslCompiler
from app.config import Settings
from app.dify.client import (
    DifyAgentStrategyListItem,
    DifyAgentStrategyListResult,
    DifyAppDetail,
    DifyChatflowRunResult,
    DifyClientError,
    DifyDatasetListItem,
    DifyDatasetListResult,
    DifyDraftRunResult,
    DifyDraftSyncResult,
    DifyDraftWorkflow,
    DifyImportResult,
    DifyModelListItem,
    DifyModelListResult,
    DifyPublishResult,
    DifyToolListItem,
    DifyToolListResult,
    DifyTriggerProviderListItem,
    DifyTriggerProviderListResult,
    DifyTriggerSubscriptionListItem,
    DifyTriggerSubscriptionListResult,
    DifyWebhookTrigger,
    DifyWorkflowTrigger,
)
from app.dify.version import DifyVersionInfo
from app.language import LANGUAGE_RESPONSE_INSTRUCTION
from app.main import _message_requests_agent_strategy, app
from app.models import WorkflowPlan


def test_web_ui_index_and_static_assets(monkeypatch) -> None:
    monkeypatch.setattr("app.main.load_settings", _test_settings)
    monkeypatch.setattr(
        "app.main.read_dify_version_info",
        lambda _: DifyVersionInfo(source_dir="../dify", git_describe="test", app_dsl_version="9.9.9"),
    )

    with TestClient(app) as client:
        index = client.get("/")
        script = client.get("/static/app.js")
        styles = client.get("/static/styles.css")
        agent_script = client.get("/static/agent-workbench.js")
        agent_core = client.get("/static/agent-workbench-core.mjs")

    assert index.status_code == 200
    assert "chat2dify" in index.text
    assert "chat-v4.0.0" in index.text
    assert 'href="static/styles.css?v=chat-v4.0.0"' in index.text
    assert "[hidden]" in styles.text
    assert "display: none !important;" in styles.text
    assert 'src="static/app.js?v=chat-v4.0.0"' in index.text
    assert '"version": "4.0.0"' in index.text
    assert index.headers["cache-control"] == "no-store"
    assert 'id="chat-log"' in index.text
    assert 'id="chat-form"' in index.text
    assert 'id="chat-input"' in index.text
    assert 'id="chat-submit"' in index.text
    assert 'id="planner-select"' in index.text
    assert 'id="agent-workbench"' in index.text
    assert 'id="agent-timeline"' in index.text
    assert 'id="agent-goal-plan"' in index.text
    assert 'id="agent-approvals"' in index.text
    assert 'static/agent-workbench.js?v=chat-v4.0.0' in index.text
    assert 'id="create-form"' not in index.text
    assert 'id="modify-form"' not in index.text
    assert 'id="run-form"' not in index.text
    assert 'id="planner-form"' not in index.text
    assert 'id="knowledge-form"' not in index.text
    assert 'id="tools-form"' not in index.text
    assert 'id="agents-form"' not in index.text
    assert "initializeFromUrl" in script.text
    assert "create_app_mode" in script.text
    assert "body.embedded" in styles.text
    assert 'id="trigger-form"' not in index.text
    assert script.status_code == 200
    assert script.headers["cache-control"] == "no-store"
    assert "handleUserMessage" in script.text
    assert "CHAT2DIFY_AGENT_WORKBENCH" in script.text
    assert "executePendingAction" in script.text
    assert "/api/assistant/plan" in script.text
    assert "/api/assistant/execute" in script.text
    assert "/api/planner/providers" in script.text
    assert "/api/tasks/" in script.text
    assert "state.context" in script.text
    assert "actionWithCurrentPlanner" in script.text
    assert "pendingAction" in script.text
    assert "addActionCard" in script.text
    assert "addResultMessage" in script.text
    assert "active_app" in script.text
    assert "recent_apps" in script.text
    assert "configured_model_config" in script.text
    assert "prepareApplyActionFromPreview" in script.text
    assert "clearAppScopedContext" in script.text
    assert "switchingApp" in script.text
    assert "workflow.modify.apply" in script.text
    assert "尚未写回 Dify" in script.text
    assert "BASE_PATH" in script.text
    assert "apiUrl(path)" in script.text
    assert "applyContextHints(text)" not in script.text
    assert app.version == "4.0.0"
    assert styles.status_code == 200
    assert ".chat-shell" in styles.text
    assert ".chat-composer" in styles.text
    assert ".planner-picker" in styles.text
    assert ".message-user" in styles.text
    assert ".message-card" in styles.text
    assert ".result-rows" in styles.text
    assert ".agent-workbench" in styles.text
    assert ".agent-timeline" in styles.text
    assert agent_script.status_code == 200
    assert agent_core.status_code == 200
    assert "CanvasContextChannel" in agent_core.text
    assert "/api/v4/agent" in agent_script.text


def test_health_returns_configured_dataset_count(monkeypatch) -> None:
    monkeypatch.setattr("app.main.load_settings", lambda: _test_settings(dataset_ids="dataset-a,dataset-b"))
    monkeypatch.setattr(
        "app.main.read_dify_version_info",
        lambda _: DifyVersionInfo(source_dir="../dify", git_describe="test", app_dsl_version="9.9.9"),
    )

    with TestClient(app) as client:
        response = client.get("/health")

    assert response.status_code == 200
    data = response.json()
    assert data["version"] == "4.0.0"
    assert data["component"]["kind"] == "dify-panel-component"
    assert data["component"]["panel_url"] == "/"
    assert data["configured_dataset_count"] == 2
    assert data["default_model"] == {"provider": "langgenius/tongyi/tongyi", "name": "qwen3.5-plus"}
    assert data["dify"]["configured_dataset_count"] == 2
    assert data["dify"]["default_model"] == {"provider": "langgenius/tongyi/tongyi", "name": "qwen3.5-plus"}
    assert data["planner"] == {"provider": "openai", "model": "gpt-4o-mini", "configured": True}
    assert data["features"] == {"agent_v4": False}


def test_panel_manifest_uses_public_base_path(monkeypatch) -> None:
    monkeypatch.setattr("app.main.load_public_base_path", lambda: "/chat2dify")

    with TestClient(app) as client:
        response = client.get("/api/panel/manifest")

    assert response.status_code == 200
    assert response.json() == {
        "name": "chat2dify",
        "version": "4.0.0",
        "kind": "dify-panel-component",
        "mount_path": "/chat2dify",
        "panel_url": "/chat2dify/",
        "health_url": "/chat2dify/health",
        "api_prefix": "/chat2dify/api",
    }


def test_list_planner_providers_returns_nvidia_without_api_key(monkeypatch) -> None:
    monkeypatch.setattr("app.main.load_settings", lambda: _test_settings(nvidia_api_key="nvapi-test"))

    with TestClient(app) as client:
        response = client.get("/api/planner/providers")

    assert response.status_code == 200
    data = response.json()
    nvidia = next(item for item in data["providers"] if item["id"] == "nvidia")
    assert nvidia["configured"] is True
    assert nvidia["models"] == [
        {"id": "deepseek-ai/deepseek-v4-flash", "label": "DeepSeek V4 Flash"},
        {"id": "deepseek-ai/deepseek-v4-pro", "label": "DeepSeek V4 Pro"},
    ]
    assert "nvapi-test" not in response.text


def test_draft_rejects_unconfigured_planner_provider(monkeypatch) -> None:
    monkeypatch.setattr("app.main.load_settings", _test_settings)

    with TestClient(app) as client:
        response = client.post(
            "/api/workflows/draft",
            json={
                "message": "生成简单工作流",
                "planner": {
                    "provider": "nvidia",
                    "model": "deepseek-ai/deepseek-v4-flash",
                },
            },
        )

    assert response.status_code == 422
    assert response.json()["detail"]["code"] == "PLANNER_PROVIDER_NOT_CONFIGURED"


def test_planner_invalid_detail_is_preserved_by_sync_and_task_apis(
    monkeypatch,
) -> None:
    settings = _test_settings()
    _patch_runtime_model_context(monkeypatch, settings)
    detail = {
        "code": "PLANNER_PLAN_INVALID",
        "provider": "nvidia",
        "model": "deepseek-ai/deepseek-v4-flash",
        "attempts": 2,
        "repair_actions": [],
        "attempt_diagnostics": [],
        "validation_issues": [],
        "preflight": {
            "ok": False,
            "dsl_version": "9.9.9",
            "roundtrip_ok": False,
            "issues": [],
        },
        "raw_plan_excerpt": "{}",
    }

    class FailingPlanner:
        def __init__(self, _settings):
            pass

        def generate(self, *_args, **_kwargs):
            raise PlannerError("invalid plan", detail=detail)

    monkeypatch.setattr("app.main.load_settings", lambda: settings)
    monkeypatch.setattr(
        "app.main.read_dify_version_info",
        lambda _: DifyVersionInfo(
            source_dir="../dify",
            git_describe="test",
            app_dsl_version="9.9.9",
        ),
    )
    monkeypatch.setattr("app.main.WorkflowPlanner", FailingPlanner)

    with TestClient(app) as client:
        response = client.post(
            "/api/workflows/draft",
            json={"message": "生成复杂 Chatflow", "app_mode": "advanced-chat"},
        )
        task_response = client.post(
            "/api/tasks/workflows/create",
            json={"message": "生成复杂 Chatflow", "app_mode": "advanced-chat"},
        )
        task_id = task_response.json()["task_id"]
        deadline = time.monotonic() + 2
        while time.monotonic() < deadline:
            task = client.get(f"/api/tasks/{task_id}").json()
            if task["status"] == "failed":
                break
            time.sleep(0.01)

    assert response.status_code == 502
    assert response.json()["detail"] == detail
    assert task["error"]["status_code"] == 502
    assert task["error"]["detail"] == detail


def test_draft_uses_requested_nvidia_planner(monkeypatch) -> None:
    seen = {}
    _patch_runtime_model_context(monkeypatch, _test_settings())

    class CapturingPlanner:
        def __init__(self, settings):
            runtime = settings.planner_runtime()
            seen["provider"] = runtime.provider
            seen["model"] = runtime.model

        def generate(self, message, *, app_name=None, dsl_version, **_kwargs):
            plan = fallback_plan(message, app_name=app_name)
            return PlannerResult(
                plan=plan,
                raw_plan=plan.model_dump(),
                mode="llm",
                attempts=1,
                used_fallback=False,
                repaired=False,
                provider="nvidia",
                model="deepseek-ai/deepseek-v4-flash",
            )

    monkeypatch.setattr("app.main.load_settings", lambda: _test_settings(nvidia_api_key="nvapi-test"))
    monkeypatch.setattr(
        "app.main.read_dify_version_info",
        lambda _: DifyVersionInfo(source_dir="../dify", git_describe="test", app_dsl_version="9.9.9"),
    )
    monkeypatch.setattr("app.main.WorkflowPlanner", CapturingPlanner)

    with TestClient(app) as client:
        response = client.post(
            "/api/workflows/draft",
            json={
                "message": "生成简单工作流",
                "planner": {
                    "provider": "nvidia",
                    "model": "deepseek-ai/deepseek-v4-flash",
                },
            },
        )

    assert response.status_code == 200
    assert seen == {"provider": "nvidia", "model": "deepseek-ai/deepseek-v4-flash"}
    assert response.json()["planner"]["provider"] == "nvidia"


def test_draft_uses_requested_openrouter_planner(monkeypatch) -> None:
    seen = {}
    _patch_runtime_model_context(monkeypatch, _test_settings())

    class CapturingPlanner:
        def __init__(self, settings):
            runtime = settings.planner_runtime()
            seen["provider"] = runtime.provider
            seen["model"] = runtime.model

        def generate(self, message, *, app_name=None, dsl_version, **_kwargs):
            plan = fallback_plan(message, app_name=app_name)
            return PlannerResult(
                plan=plan,
                raw_plan=plan.model_dump(),
                mode="llm",
                attempts=1,
                used_fallback=False,
                repaired=False,
                provider="openrouter",
                model="nvidia/nemotron-3-ultra-550b-a55b:free",
            )

    monkeypatch.setattr(
        "app.main.load_settings",
        lambda: _test_settings(openrouter_api_key="sk-or-test"),
    )
    monkeypatch.setattr(
        "app.main.read_dify_version_info",
        lambda _: DifyVersionInfo(source_dir="../dify", git_describe="test", app_dsl_version="9.9.9"),
    )
    monkeypatch.setattr("app.main.WorkflowPlanner", CapturingPlanner)

    with TestClient(app) as client:
        response = client.post(
            "/api/workflows/draft",
            json={
                "message": "生成简单工作流",
                "planner": {
                    "provider": "openrouter",
                    "model": "nvidia/nemotron-3-ultra-550b-a55b:free",
                },
            },
        )

    assert response.status_code == 200
    assert seen == {
        "provider": "openrouter",
        "model": "nvidia/nemotron-3-ultra-550b-a55b:free",
    }
    assert response.json()["planner"]["provider"] == "openrouter"


def test_draft_creates_advanced_chat_plan(monkeypatch) -> None:
    seen = {}
    _patch_runtime_model_context(monkeypatch, _test_settings())

    class CapturingPlanner:
        def __init__(self, _settings):
            pass

        def generate(self, message, *, app_name=None, dsl_version, app_mode="workflow", **_kwargs):
            seen["app_mode"] = app_mode
            plan = fallback_plan(message, app_name=app_name, app_mode=app_mode)
            return PlannerResult(
                plan=plan,
                raw_plan=plan.model_dump(),
                mode="fallback",
                attempts=0,
                used_fallback=True,
                repaired=False,
            )

    monkeypatch.setattr("app.main.load_settings", _test_settings)
    monkeypatch.setattr(
        "app.main.read_dify_version_info",
        lambda _: DifyVersionInfo(source_dir="../dify", git_describe="test", app_dsl_version="9.9.9"),
    )
    monkeypatch.setattr("app.main.WorkflowPlanner", CapturingPlanner)

    with TestClient(app) as client:
        response = client.post(
            "/api/workflows/draft",
            json={
                "app_mode": "advanced-chat",
                "message": "创建汽车售后多轮客服",
                "app_name": "汽车售后多轮客服",
            },
        )

    assert response.status_code == 200
    data = response.json()
    assert seen["app_mode"] == "advanced-chat"
    assert data["plan"]["app_mode"] == "advanced-chat"
    assert {node["type"] for node in data["plan"]["nodes"]} == {"start", "llm", "answer"}
    assert data["validation"]["ok"] is True
    assert yaml.safe_load(data["dsl"])["app"]["mode"] == "advanced-chat"


def test_draft_creates_chatbot_app_dsl(monkeypatch) -> None:
    settings = _test_settings()
    _patch_runtime_model_context(monkeypatch, settings)
    monkeypatch.setattr("app.main.load_settings", lambda: settings)
    monkeypatch.setattr(
        "app.main.read_dify_version_info",
        lambda _: DifyVersionInfo(source_dir="../dify", git_describe="test", app_dsl_version="9.9.9"),
    )

    with TestClient(app) as client:
        response = client.post(
            "/api/workflows/draft",
            json={
                "app_mode": "chat",
                "message": "创建汽车售后聊天助手",
                "app_name": "汽车售后聊天助手",
            },
        )

    assert response.status_code == 200
    data = response.json()
    dsl = yaml.safe_load(data["dsl"])
    assert data["plan"]["app_mode"] == "chat"
    assert data["validation"]["ok"] is True
    assert dsl["app"]["mode"] == "chat"
    assert dsl["model_config"]["pre_prompt"]
    assert dsl["model_config"]["agent_mode"]["enabled"] is False


def test_draft_creates_completion_app_dsl(monkeypatch) -> None:
    settings = _test_settings()
    _patch_runtime_model_context(monkeypatch, settings)
    monkeypatch.setattr("app.main.load_settings", lambda: settings)
    monkeypatch.setattr(
        "app.main.read_dify_version_info",
        lambda _: DifyVersionInfo(source_dir="../dify", git_describe="test", app_dsl_version="9.9.9"),
    )

    with TestClient(app) as client:
        response = client.post(
            "/api/workflows/draft",
            json={
                "app_mode": "completion",
                "message": "创建维修单总结文本生成应用",
                "app_name": "维修单总结",
            },
        )

    assert response.status_code == 200
    data = response.json()
    dsl = yaml.safe_load(data["dsl"])
    assert data["plan"]["app_mode"] == "completion"
    assert data["validation"]["ok"] is True
    assert dsl["app"]["mode"] == "completion"
    assert dsl["model_config"]["pre_prompt"]
    assert dsl["model_config"]["model"]["name"] == "qwen3.5-plus"


def test_draft_creates_agent_app_dsl(monkeypatch) -> None:
    settings = _test_settings()
    _patch_runtime_model_context(monkeypatch, settings)
    monkeypatch.setattr("app.main.load_settings", lambda: settings)
    monkeypatch.setattr(
        "app.main.read_dify_version_info",
        lambda _: DifyVersionInfo(source_dir="../dify", git_describe="test", app_dsl_version="9.9.9"),
    )

    with TestClient(app) as client:
        response = client.post(
            "/api/workflows/draft",
            json={
                "app_mode": "agent-chat",
                "message": "创建一个能自主分析售后问题的 Agent",
                "app_name": "售后 Agent",
            },
        )

    assert response.status_code == 200
    data = response.json()
    dsl = yaml.safe_load(data["dsl"])
    assert data["plan"]["app_mode"] == "agent-chat"
    assert data["validation"]["ok"] is True
    assert dsl["app"]["mode"] == "agent-chat"
    assert dsl["model_config"]["agent_mode"]["enabled"] is True
    assert dsl["model_config"]["agent_mode"]["strategy"] == "react"


def test_create_agent_app_imports_and_links_configuration(monkeypatch) -> None:
    settings = _test_settings()
    _patch_runtime_model_context(monkeypatch, settings)
    seen = {}

    class FakeDifyClient:
        def __init__(self, _settings):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *_):
            pass

        def import_yaml(self, yaml_content, *, name=None):
            seen["name"] = name
            seen["dsl"] = yaml.safe_load(yaml_content)
            return DifyImportResult(
                id="import-1",
                status="completed",
                app_id="agent-app-1",
                app_mode="agent-chat",
                workflow_url=settings.app_url("agent-app-1", "agent-chat"),
            )

    monkeypatch.setattr("app.main.load_settings", lambda: settings)
    monkeypatch.setattr(
        "app.main.read_dify_version_info",
        lambda _: DifyVersionInfo(source_dir="../dify", git_describe="test", app_dsl_version="9.9.9"),
    )
    monkeypatch.setattr("app.main.DifyClient", FakeDifyClient)

    with TestClient(app) as client:
        response = client.post(
            "/api/workflows/create",
            json={
                "app_mode": "agent-chat",
                "message": "创建 Agent",
                "app_name": "售后 Agent",
            },
        )

    assert response.status_code == 200
    data = response.json()
    assert seen["name"] == "售后 Agent"
    assert seen["dsl"]["app"]["mode"] == "agent-chat"
    assert data["app_mode"] == "agent-chat"
    assert data["workflow_url"] == "http://dify.local/app/agent-app-1/configuration"
    assert data["base_hash"] is None


def test_create_chatbot_app_imports_and_links_configuration(monkeypatch) -> None:
    settings = _test_settings()
    _patch_runtime_model_context(monkeypatch, settings)
    seen = {}

    class FakeDifyClient:
        def __init__(self, _settings):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *_):
            pass

        def import_yaml(self, yaml_content, *, name=None):
            seen["name"] = name
            seen["dsl"] = yaml.safe_load(yaml_content)
            return DifyImportResult(
                id="import-1",
                status="completed",
                app_id="chat-app-1",
                app_mode="chat",
                workflow_url=settings.app_url("chat-app-1", "chat"),
            )

    monkeypatch.setattr("app.main.load_settings", lambda: settings)
    monkeypatch.setattr(
        "app.main.read_dify_version_info",
        lambda _: DifyVersionInfo(source_dir="../dify", git_describe="test", app_dsl_version="9.9.9"),
    )
    monkeypatch.setattr("app.main.DifyClient", FakeDifyClient)

    with TestClient(app) as client:
        response = client.post(
            "/api/workflows/create",
            json={
                "app_mode": "chat",
                "message": "创建聊天助手",
                "app_name": "售后聊天助手",
            },
        )

    assert response.status_code == 200
    data = response.json()
    assert seen["name"] == "售后聊天助手"
    assert seen["dsl"]["app"]["mode"] == "chat"
    assert data["app_mode"] == "chat"
    assert data["workflow_url"] == "http://dify.local/app/chat-app-1/configuration"
    assert data["base_hash"] is None


def test_create_completion_app_imports_and_links_configuration(monkeypatch) -> None:
    settings = _test_settings()
    _patch_runtime_model_context(monkeypatch, settings)
    seen = {}

    class FakeDifyClient:
        def __init__(self, _settings):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *_):
            pass

        def import_yaml(self, yaml_content, *, name=None):
            seen["name"] = name
            seen["dsl"] = yaml.safe_load(yaml_content)
            return DifyImportResult(
                id="import-1",
                status="completed",
                app_id="completion-app-1",
                app_mode="completion",
                workflow_url=settings.app_url("completion-app-1", "completion"),
            )

        def get_draft_workflow(self, app_id):  # pragma: no cover - should never be called
            raise AssertionError(f"Completion app should not load draft workflow: {app_id}")

    monkeypatch.setattr("app.main.load_settings", lambda: settings)
    monkeypatch.setattr(
        "app.main.read_dify_version_info",
        lambda _: DifyVersionInfo(source_dir="../dify", git_describe="test", app_dsl_version="9.9.9"),
    )
    monkeypatch.setattr("app.main.DifyClient", FakeDifyClient)

    with TestClient(app) as client:
        response = client.post(
            "/api/workflows/create",
            json={
                "app_mode": "completion",
                "message": "创建维修单总结文本生成应用",
                "app_name": "维修单总结",
            },
        )

    assert response.status_code == 200
    data = response.json()
    assert seen["name"] == "维修单总结"
    assert seen["dsl"]["app"]["mode"] == "completion"
    assert data["app_mode"] == "completion"
    assert data["workflow_url"] == "http://dify.local/app/completion-app-1/configuration"
    assert data["base_hash"] is None


def test_draft_returns_valid_advanced_chat_iteration(monkeypatch) -> None:
    _patch_runtime_model_context(monkeypatch, _test_settings())

    class AdvancedNodePlanner:
        def __init__(self, _settings):
            pass

        def generate(self, _message, *, app_name=None, dsl_version, app_mode="workflow", **_kwargs):
            normalized = normalize_plan_payload(
                {
                    "name": app_name or "批量售后处理",
                    "app_mode": app_mode,
                    "nodes": [
                        {
                            "id": "start",
                            "type": "start",
                            "title": "接收批量处理要求",
                            "params": {"variables": [{"name": "items", "type": "json"}]},
                        },
                        {
                            "id": "batch",
                            "type": "iteration",
                            "title": "批量分析售后记录",
                            "params": {
                                "iterator_selector": ["start", "items", "records"],
                                "output_selector": ["item_llm", "text"],
                                "children": [
                                    {"id": "batch_start", "type": "iteration-start", "params": {}},
                                    {
                                        "id": "item_llm",
                                        "type": "llm",
                                        "title": "逐条生成售后建议",
                                        "params": {
                                            "system_prompt": "你是售后记录分析专员。",
                                            "user_prompt": "分析当前记录：{{#batch.item#}}",
                                        },
                                    },
                                ],
                                "edges": [{"source": "batch_start", "target": "item_llm"}],
                            },
                        },
                        {
                            "id": "answer",
                            "type": "answer",
                            "title": "回复批量分析结果",
                            "params": {"answer": "{{#batch.output#}}"},
                        },
                    ],
                    "edges": [
                        {"source": "start", "target": "batch"},
                        {"source": "batch", "target": "answer"},
                    ],
                },
                app_mode=app_mode,
            )
            plan = WorkflowPlan.model_validate(normalized.payload)
            return PlannerResult(
                plan=plan,
                raw_plan=plan.model_dump(),
                mode="llm",
                attempts=1,
                used_fallback=False,
                repaired=normalized.changed,
            )

    monkeypatch.setattr("app.main.load_settings", _test_settings)
    monkeypatch.setattr(
        "app.main.read_dify_version_info",
        lambda _: DifyVersionInfo(source_dir="../dify", git_describe="test", app_dsl_version="9.9.9"),
    )
    monkeypatch.setattr("app.main.WorkflowPlanner", AdvancedNodePlanner)

    with TestClient(app) as client:
        response = client.post(
            "/api/workflows/draft",
            json={
                "app_mode": "advanced-chat",
                "message": "批量分析售后记录",
                "app_name": "批量售后处理",
            },
        )

    assert response.status_code == 200
    data = response.json()
    graph_nodes = yaml.safe_load(data["dsl"])["workflow"]["graph"]["nodes"]
    item_llm = next(node for node in graph_nodes if node["id"] == "item_llm")

    assert data["validation"]["ok"] is True
    assert data["plan"]["app_mode"] == "advanced-chat"
    assert item_llm["parentId"] == "batch"
    assert "{{#sys.query#}}" in item_llm["data"]["memory"]["query_prompt_template"]


def test_chatflow_draft_passes_existing_resource_selections_to_planner(monkeypatch) -> None:
    seen = {}
    _patch_runtime_model_context(monkeypatch, _test_settings())

    class ResourceAwarePlanner:
        def __init__(self, settings):
            seen["dataset_ids"] = settings.dify_default_dataset_ids

        def generate(
            self,
            message,
            *,
            app_name=None,
            dsl_version,
            app_mode="workflow",
            tool_selections=None,
            agent_selections=None,
            trigger_selection=None,
            **_kwargs,
        ):
            seen["app_mode"] = app_mode
            seen["tool_selections"] = tool_selections
            seen["agent_selections"] = agent_selections
            seen["trigger_selection"] = trigger_selection
            plan = fallback_plan(message, app_name=app_name, app_mode=app_mode)
            return PlannerResult(
                plan=plan,
                raw_plan=plan.model_dump(),
                mode="fallback",
                attempts=0,
                used_fallback=True,
                repaired=False,
            )

    monkeypatch.setattr("app.main.load_settings", _test_settings)
    monkeypatch.setattr(
        "app.main.read_dify_version_info",
        lambda _: DifyVersionInfo(source_dir="../dify", git_describe="test", app_dsl_version="9.9.9"),
    )
    monkeypatch.setattr("app.main.WorkflowPlanner", ResourceAwarePlanner)

    with TestClient(app) as client:
        response = client.post(
            "/api/workflows/draft",
            json={
                "app_mode": "advanced-chat",
                "message": "创建复杂售后客服",
                "dataset_ids": ["dataset-1"],
                "tool_selections": [
                    {
                        "provider_id": "provider-1",
                        "provider_type": "builtin",
                        "tool_name": "search",
                    }
                ],
                "agent_selections": [
                    {
                        "agent_strategy_provider_name": "langgenius/agent/react",
                        "agent_strategy_name": "react",
                    }
                ],
            },
        )

    assert response.status_code == 200
    assert seen["app_mode"] == "advanced-chat"
    assert seen["dataset_ids"] == ["dataset-1"]
    assert seen["tool_selections"][0]["tool_name"] == "search"
    assert seen["agent_selections"][0]["agent_strategy_name"] == "react"
    assert seen["trigger_selection"] is None


def test_list_dify_datasets_api_returns_slim_dataset_list(monkeypatch) -> None:
    settings = _test_settings()
    seen = {}

    class FakeDifyClient:
        def __init__(self, _settings):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *_):
            pass

        def list_datasets(self, *, keyword=None, page=1, limit=50, include_all=True):
            seen["keyword"] = keyword
            seen["page"] = page
            seen["limit"] = limit
            seen["include_all"] = include_all
            return DifyDatasetListResult(
                data=[
                    DifyDatasetListItem(
                        id="dataset-1",
                        name="售后政策",
                        description="门店售后政策",
                        document_count=3,
                        total_document_count=5,
                        provider="vendor",
                        runtime_mode="general",
                        indexing_technique="high_quality",
                        embedding_available=True,
                        permission="all_team_members",
                        updated_at=123,
                    )
                ],
                has_more=True,
                page=2,
                limit=10,
                total=21,
            )

    monkeypatch.setattr("app.main.load_settings", lambda: settings)
    monkeypatch.setattr("app.main.DifyClient", FakeDifyClient)

    with TestClient(app) as client:
        response = client.get("/api/dify/datasets?keyword=%E5%94%AE%E5%90%8E&page=2&limit=10&include_all=true")

    assert response.status_code == 200
    data = response.json()
    assert seen == {"keyword": "售后", "page": 2, "limit": 10, "include_all": True}
    assert data["has_more"] is True
    assert data["total"] == 21
    assert data["data"][0] == {
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
        "retrieval_model_dict": None,
    }


def test_list_dify_models_api_returns_runtime_capabilities(monkeypatch) -> None:
    seen = {}
    model = DifyModelListItem(
        provider="langgenius/tongyi/tongyi",
        provider_label="Tongyi",
        model="qwen-plus",
        model_label="Qwen Plus",
        model_type="llm",
        status="active",
        provider_status="active",
        deprecated=False,
        features=["tool-call", "vision"],
        context_length=131072,
        mode="chat",
        fetch_from="predefined-model",
    )

    class FakeDifyClient:
        def __init__(self, _settings):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *_):
            pass

        def list_models(self, *, model_type, keyword=None, features=None):
            seen.update(
                model_type=model_type,
                keyword=keyword,
                features=features,
            )
            return DifyModelListResult(
                data=[model],
                count=1,
                model_type="llm",
                providers=[model.provider],
                features=list(model.features),
            )

    monkeypatch.setattr("app.main.load_settings", _test_settings)
    monkeypatch.setattr("app.main.DifyClient", FakeDifyClient)

    with TestClient(app) as client:
        response = client.get(
            "/api/dify/models?model_type=llm&keyword=qwen&feature=vision&feature=tool-call"
        )

    assert response.status_code == 200
    assert seen == {
        "model_type": "llm",
        "keyword": "qwen",
        "features": ["vision", "tool-call"],
    }
    assert response.json()["data"][0]["context_length"] == 131072
    assert response.json()["data"][0]["features"] == ["tool-call", "vision"]


def test_list_dify_datasets_api_wraps_dify_errors(monkeypatch) -> None:
    class FakeDifyClient:
        def __init__(self, _settings):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *_):
            pass

        def list_datasets(self, **_kwargs):
            raise DifyClientError("Dify unavailable")

    monkeypatch.setattr("app.main.load_settings", _test_settings)
    monkeypatch.setattr("app.main.DifyClient", FakeDifyClient)

    with TestClient(app) as client:
        response = client.get("/api/dify/datasets")

    assert response.status_code == 502
    assert response.json()["detail"] == "Dify unavailable"


def test_list_dify_tools_api_returns_slim_tool_list(monkeypatch) -> None:
    seen = {}

    class FakeDifyClient:
        def __init__(self, _settings):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *_):
            pass

        def list_tools(self, *, keyword=None, provider_type=None):
            seen["keyword"] = keyword
            seen["provider_type"] = provider_type
            return DifyToolListResult(
                data=[
                    DifyToolListItem(
                        provider_id="provider-1",
                        provider_type="builtin",
                        provider_name="websearch",
                        tool_name="search",
                        tool_label="搜索",
                        description="搜索网页",
                        parameters=[{"name": "query", "form": "llm", "type": "string", "required": True}],
                        output_schema={"properties": {"answer": {"type": "string"}}},
                        plugin_id="plugin-1",
                        plugin_unique_identifier="unique-1",
                        is_team_authorization=True,
                        requires_configuration=False,
                    )
                ],
                count=1,
                types=["builtin"],
            )

    monkeypatch.setattr("app.main.load_settings", _test_settings)
    monkeypatch.setattr("app.main.DifyClient", FakeDifyClient)

    with TestClient(app) as client:
        response = client.get("/api/dify/tools?keyword=%E6%90%9C%E7%B4%A2&provider_type=builtin")

    assert response.status_code == 200
    data = response.json()
    assert seen == {"keyword": "搜索", "provider_type": "builtin"}
    assert data["count"] == 1
    assert data["types"] == ["builtin"]
    assert data["data"][0]["tool_name"] == "search"
    assert data["data"][0]["output_schema"] == {"properties": {"answer": {"type": "string"}}}


def test_list_dify_tools_api_wraps_dify_errors(monkeypatch) -> None:
    class FakeDifyClient:
        def __init__(self, _settings):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *_):
            pass

        def list_tools(self, **_kwargs):
            raise DifyClientError("Tool list unavailable")

    monkeypatch.setattr("app.main.load_settings", _test_settings)
    monkeypatch.setattr("app.main.DifyClient", FakeDifyClient)

    with TestClient(app) as client:
        response = client.get("/api/dify/tools")

    assert response.status_code == 502
    assert response.json()["detail"] == "Tool list unavailable"


def test_list_dify_agent_strategies_api_returns_slim_strategy_list(monkeypatch) -> None:
    seen = {}

    class FakeDifyClient:
        def __init__(self, _settings):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *_):
            pass

        def list_agent_strategies(self, *, keyword=None):
            seen["keyword"] = keyword
            return DifyAgentStrategyListResult(
                data=[
                    DifyAgentStrategyListItem(
                        agent_strategy_provider_name="langgenius/agent/react",
                        agent_strategy_name="react",
                        agent_strategy_label="ReAct",
                        description="多步推理",
                        parameters=[{"name": "query", "type": "text-input", "required": True}],
                        features=["history-messages"],
                        output_schema={"properties": {"answer": {"type": "string"}}},
                        plugin_unique_identifier="langgenius/agent:1.0.0",
                        meta={"version": "1.0.0"},
                    )
                ],
                count=1,
                providers=["langgenius/agent/react"],
            )

    monkeypatch.setattr("app.main.load_settings", _test_settings)
    monkeypatch.setattr("app.main.DifyClient", FakeDifyClient)

    with TestClient(app) as client:
        response = client.get("/api/dify/agent-strategies?keyword=react")

    assert response.status_code == 200
    data = response.json()
    assert seen == {"keyword": "react"}
    assert data["count"] == 1
    assert data["providers"] == ["langgenius/agent/react"]
    assert data["data"][0]["agent_strategy_name"] == "react"
    assert data["data"][0]["output_schema"] == {"properties": {"answer": {"type": "string"}}}


def test_list_dify_trigger_providers_and_subscriptions_api(monkeypatch) -> None:
    seen = {}

    class FakeDifyClient:
        def __init__(self, _settings):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *_):
            pass

        def list_trigger_providers(self, *, keyword=None):
            seen["keyword"] = keyword
            return DifyTriggerProviderListResult(
                data=[
                    DifyTriggerProviderListItem(
                        provider_id="langgenius/github/github",
                        provider_type="trigger",
                        provider_name="langgenius/github/github",
                        provider_label="GitHub",
                        description="GitHub events",
                        event_name="issue_created",
                        event_label="Issue created",
                        event_description="New issue",
                        parameters=[{"name": "repository", "type": "string", "required": True}],
                        output_schema={"properties": {"title": {"type": "string"}}},
                        plugin_id="langgenius/github",
                        plugin_unique_identifier="langgenius/github:1.0.0",
                    )
                ],
                count=1,
                providers=["langgenius/github/github"],
            )

        def list_trigger_subscriptions(self, provider_id):
            seen["provider_id"] = provider_id
            return DifyTriggerSubscriptionListResult(
                data=[
                    DifyTriggerSubscriptionListItem(
                        id="sub-1",
                        name="GitHub",
                        provider_id=provider_id,
                        credential_type="oauth2",
                        endpoint="https://example.test/hook",
                        parameters={},
                        properties={},
                        workflows_in_use=0,
                    )
                ],
                count=1,
                provider_id=provider_id,
            )

    monkeypatch.setattr("app.main.load_settings", _test_settings)
    monkeypatch.setattr("app.main.DifyClient", FakeDifyClient)

    with TestClient(app) as client:
        providers = client.get("/api/dify/trigger-providers?keyword=issue")
        subscriptions = client.get(
            "/api/dify/trigger-subscriptions?provider_id=langgenius%2Fgithub%2Fgithub"
        )

    assert providers.status_code == 200
    assert subscriptions.status_code == 200
    assert seen == {
        "keyword": "issue",
        "provider_id": "langgenius/github/github",
    }
    assert providers.json()["data"][0]["event_name"] == "issue_created"
    assert subscriptions.json()["data"][0]["id"] == "sub-1"


def test_draft_workflow_hydrates_plugin_trigger_before_planning(monkeypatch) -> None:
    seen = {}

    class FakeDifyClient:
        def __init__(self, _settings):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *_):
            pass

        def list_trigger_providers(self, *, keyword=None):
            return DifyTriggerProviderListResult(
                data=[
                    DifyTriggerProviderListItem(
                        provider_id="provider-1",
                        provider_type="trigger",
                        provider_name="provider-1",
                        provider_label="Provider",
                        description="",
                        event_name="created",
                        event_label="Created",
                        event_description="",
                        parameters=[{"name": "scope", "type": "string", "required": True}],
                        output_schema={"properties": {"title": {"type": "string"}}},
                        plugin_id="trusted-plugin",
                        plugin_unique_identifier="trusted-plugin:1",
                    )
                ],
                count=1,
                providers=["provider-1"],
            )

        def list_trigger_subscriptions(self, provider_id):
            return DifyTriggerSubscriptionListResult(
                data=[
                    DifyTriggerSubscriptionListItem(
                        id="sub-1",
                        name="Configured",
                        provider_id=provider_id,
                        credential_type="oauth2",
                        endpoint="",
                        parameters={},
                        properties={},
                        workflows_in_use=0,
                    )
                ],
                count=1,
                provider_id=provider_id,
            )

    class TriggerAwarePlanner:
        def __init__(self, _settings):
            pass

        def generate(self, message, *, app_name=None, dsl_version, trigger_selection=None, **_kwargs):
            seen["trigger_selection"] = trigger_selection
            normalized = normalize_plan_payload(
                fallback_plan(message, app_name=app_name).model_dump(),
                trigger_selection=trigger_selection,
            )
            plan = WorkflowPlan.model_validate(normalized.payload)
            return PlannerResult(
                plan=plan,
                raw_plan=plan.model_dump(),
                mode="llm",
                attempts=1,
                used_fallback=False,
                repaired=False,
            )

    monkeypatch.setattr("app.main.load_settings", _test_settings)
    monkeypatch.setattr(
        "app.main.read_dify_version_info",
        lambda _: DifyVersionInfo(source_dir="../dify", git_describe="test", app_dsl_version="9.9.9"),
    )
    monkeypatch.setattr("app.main.DifyClient", FakeDifyClient)
    monkeypatch.setattr("app.main.WorkflowPlanner", TriggerAwarePlanner)

    with TestClient(app) as client:
        response = client.post(
            "/api/workflows/draft",
            json={
                "message": "Handle the selected plugin event",
                "trigger_selection": {
                    "type": "plugin",
                    "provider_id": "provider-1",
                    "event_name": "created",
                    "subscription_id": "sub-1",
                    "plugin_id": "untrusted-plugin",
                    "event_parameters": {
                        "scope": {"type": "constant", "value": "support"},
                    },
                },
            },
        )

    assert response.status_code == 200
    assert seen["trigger_selection"]["plugin_id"] == "trusted-plugin"
    assert seen["trigger_selection"]["parameters_schema"][0]["name"] == "scope"
    assert seen["trigger_selection"]["subscription_id"] == "sub-1"
    assert any(node["type"] == "trigger-plugin" for node in response.json()["plan"]["nodes"])


def test_draft_workflow_rejects_plugin_subscription_from_another_provider(monkeypatch) -> None:
    class FakeDifyClient:
        def __init__(self, _settings):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *_):
            pass

        def list_trigger_providers(self, *, keyword=None):
            return DifyTriggerProviderListResult(
                data=[
                    DifyTriggerProviderListItem(
                        provider_id="provider-1",
                        provider_type="trigger",
                        provider_name="provider-1",
                        provider_label="Provider",
                        description="",
                        event_name="created",
                        event_label="Created",
                        event_description="",
                        parameters=[],
                        output_schema={},
                        plugin_id="plugin",
                        plugin_unique_identifier="plugin:1",
                    )
                ],
                count=1,
                providers=["provider-1"],
            )

        def list_trigger_subscriptions(self, provider_id):
            return DifyTriggerSubscriptionListResult(
                data=[
                    DifyTriggerSubscriptionListItem(
                        id="sub-1",
                        name="Wrong provider",
                        provider_id="provider-2",
                        credential_type="oauth2",
                        endpoint="",
                        parameters={},
                        properties={},
                        workflows_in_use=0,
                    )
                ],
                count=1,
                provider_id=provider_id,
            )

    monkeypatch.setattr("app.main.load_settings", _test_settings)
    monkeypatch.setattr(
        "app.main.read_dify_version_info",
        lambda _: DifyVersionInfo(source_dir="../dify", git_describe="test", app_dsl_version="9.9.9"),
    )
    monkeypatch.setattr("app.main.DifyClient", FakeDifyClient)

    with TestClient(app) as client:
        response = client.post(
            "/api/workflows/draft",
            json={
                "message": "Handle event",
                "trigger_selection": {
                    "type": "plugin",
                    "provider_id": "provider-1",
                    "event_name": "created",
                    "subscription_id": "sub-1",
                },
            },
        )

    assert response.status_code == 422
    assert response.json()["detail"]["code"] == "PLUGIN_TRIGGER_SUBSCRIPTION_NOT_FOUND"


def test_draft_workflow_passes_tool_selections_to_planner(monkeypatch) -> None:
    seen = {}
    _patch_runtime_model_context(monkeypatch, _test_settings())

    class ToolAwarePlanner:
        def __init__(self, settings):
            self.settings = settings

        def generate(self, message, *, app_name=None, dsl_version, tool_selections=None, **_kwargs):
            seen["tool_selections"] = tool_selections
            plan = fallback_plan(message, app_name=app_name)
            return PlannerResult(
                plan=plan,
                raw_plan=plan.model_dump(),
                mode="llm",
                attempts=1,
                used_fallback=False,
                repaired=False,
            )

    monkeypatch.setattr("app.main.load_settings", _test_settings)
    monkeypatch.setattr(
        "app.main.read_dify_version_info",
        lambda _: DifyVersionInfo(source_dir="../dify", git_describe="test", app_dsl_version="9.9.9"),
    )
    monkeypatch.setattr("app.main.WorkflowPlanner", ToolAwarePlanner)

    with TestClient(app) as client:
        response = client.post(
            "/api/workflows/draft",
            json={
                "message": "调用搜索工具后总结",
                "tool_selections": [
                    {
                        "provider_id": "provider-1",
                        "provider_type": "builtin",
                        "provider_name": "websearch",
                        "tool_name": "search",
                        "tool_label": "搜索",
                        "parameters": [{"name": "query", "form": "llm", "type": "string", "required": True}],
                    }
                ],
            },
        )

    assert response.status_code == 200
    assert seen["tool_selections"][0]["tool_name"] == "search"


def test_draft_workflow_passes_agent_selections_to_planner(monkeypatch) -> None:
    seen = {}
    _patch_runtime_model_context(monkeypatch, _test_settings())

    class AgentAwarePlanner:
        def __init__(self, settings):
            self.settings = settings

        def generate(self, message, *, app_name=None, dsl_version, agent_selections=None, **_kwargs):
            seen["agent_selections"] = agent_selections
            plan = fallback_plan(message, app_name=app_name)
            return PlannerResult(
                plan=plan,
                raw_plan=plan.model_dump(),
                mode="llm",
                attempts=1,
                used_fallback=False,
                repaired=False,
            )

    monkeypatch.setattr("app.main.load_settings", _test_settings)
    monkeypatch.setattr(
        "app.main.read_dify_version_info",
        lambda _: DifyVersionInfo(source_dir="../dify", git_describe="test", app_dsl_version="9.9.9"),
    )
    monkeypatch.setattr("app.main.WorkflowPlanner", AgentAwarePlanner)

    with TestClient(app) as client:
        response = client.post(
            "/api/workflows/draft",
            json={
                "message": "用智能体分析问题",
                "agent_selections": [
                    {
                        "agent_strategy_provider_name": "langgenius/agent/react",
                        "agent_strategy_name": "react",
                        "agent_strategy_label": "ReAct",
                        "parameters": [{"name": "query", "type": "text-input", "required": True}],
                        "agent_parameters": {"query": {"type": "variable", "value": ["start", "query"]}},
                    }
                ],
            },
        )

    assert response.status_code == 200
    assert seen["agent_selections"][0]["agent_strategy_name"] == "react"


def test_draft_workflow_requires_agent_strategy_selection_for_agent_requests(monkeypatch) -> None:
    class FailingPlanner:
        def __init__(self, _settings):
            pass

        def generate(self, *_args, **_kwargs):
            raise AssertionError("planner should not run without agent selections")

    monkeypatch.setattr("app.main.load_settings", _test_settings)
    monkeypatch.setattr(
        "app.main.read_dify_version_info",
        lambda _: DifyVersionInfo(source_dir="../dify", git_describe="test", app_dsl_version="9.9.9"),
    )
    monkeypatch.setattr("app.main.WorkflowPlanner", FailingPlanner)

    with TestClient(app) as client:
        response = client.post(
            "/api/workflows/draft",
            json={"message": "使用 Agent 智能体分析售后问题并返回建议"},
        )

    assert response.status_code == 422
    detail = response.json()["detail"]
    assert detail["code"] == "AGENT_STRATEGY_SELECTION_REQUIRED"
    assert "Agent Strategy" in detail["message"]


def test_agent_strategy_request_detection_ignores_negated_mentions() -> None:
    assert _message_requests_agent_strategy("使用 Agent 智能体分析售后问题") is True
    assert _message_requests_agent_strategy("需要自主规划和多步执行") is True
    assert _message_requests_agent_strategy("不要使用 Tool、Agent、人工审核或循环") is False
    assert _message_requests_agent_strategy("不需要智能体，只使用普通大模型") is False
    assert _message_requests_agent_strategy("Do not use an agent; use an LLM only.") is False


def test_draft_workflow_validates_agent_selection_required_parameters_before_planner(monkeypatch) -> None:
    class FailingPlanner:
        def __init__(self, _settings):
            pass

        def generate(self, *_args, **_kwargs):
            raise AssertionError("planner should not run with invalid agent selections")

    monkeypatch.setattr("app.main.load_settings", _test_settings)
    monkeypatch.setattr(
        "app.main.read_dify_version_info",
        lambda _: DifyVersionInfo(source_dir="../dify", git_describe="test", app_dsl_version="9.9.9"),
    )
    monkeypatch.setattr("app.main.WorkflowPlanner", FailingPlanner)

    with TestClient(app) as client:
        response = client.post(
            "/api/workflows/draft",
            json={
                "message": "用智能体分析问题",
                "agent_selections": [
                    {
                        "agent_strategy_provider_name": "langgenius/agent/agent",
                        "agent_strategy_name": "function_calling",
                        "agent_strategy_label": "FunctionCalling",
                        "parameters": [
                            {"name": "model", "type": "model-selector", "required": True},
                            {"name": "instruction", "type": "string", "required": True},
                        ],
                        "agent_parameters": {
                            "instruction": {"type": "mixed", "value": ""},
                        },
                    }
                ],
            },
        )

    assert response.status_code == 422
    detail = response.json()["detail"]
    assert detail["code"] == "AGENT_SELECTION_REQUIRED_PARAMETER_MISSING"
    assert detail["issues"][0]["path"] == "agent_selections.0.agent_parameters.model"


def test_draft_response_includes_phase2_fields(monkeypatch) -> None:
    _patch_runtime_model_context(
        monkeypatch,
        Settings.from_env(
            {
                "DIFY_SOURCE_DIR": "../dify",
                "DIFY_DEFAULT_MODEL_PROVIDER": "openai",
                "DIFY_DEFAULT_MODEL_NAME": "gpt-4o-mini",
            },
            validate_dify=False,
        ),
    )
    monkeypatch.setattr(
        "app.main.load_settings",
        lambda: Settings.from_env(
            {
                "DIFY_SOURCE_DIR": "../dify",
                "OPENAI_API_KEY": "",
                "DIFY_DEFAULT_MODEL_PROVIDER": "openai",
                "DIFY_DEFAULT_MODEL_NAME": "gpt-4o-mini",
            },
            validate_dify=False,
        ),
    )
    monkeypatch.setattr(
        "app.main.read_dify_version_info",
        lambda _: DifyVersionInfo(source_dir="../dify", git_describe="test", app_dsl_version="9.9.9"),
    )

    with TestClient(app) as client:
        response = client.post(
            "/api/workflows/draft",
            json={"message": "Summarize the user input", "app_name": "Draft API"},
        )

    assert response.status_code == 200
    data = response.json()
    assert data["raw_plan"]
    assert data["plan"]["name"] == "Draft API"
    assert data["explanation"]["summary"]
    assert data["planner"]["mode"] == "fallback"
    assert data["planner"]["used_fallback"] is True
    assert data["validation"]["ok"] is True


def test_draft_request_dataset_ids_override_env_defaults(monkeypatch) -> None:
    _patch_runtime_model_context(monkeypatch, _test_settings())
    monkeypatch.setattr("app.main.load_settings", lambda: _test_settings(dataset_ids="env-dataset"))
    monkeypatch.setattr(
        "app.main.read_dify_version_info",
        lambda _: DifyVersionInfo(source_dir="../dify", git_describe="test", app_dsl_version="9.9.9"),
    )
    monkeypatch.setattr("app.main.WorkflowPlanner", _KnowledgePlanner)

    with TestClient(app) as client:
        response = client.post(
            "/api/workflows/draft",
            json={
                "message": "根据知识库回答修车售后问题",
                "app_name": "知识库问答",
                "dataset_ids": ["request-dataset"],
            },
        )

    assert response.status_code == 200
    data = response.json()
    knowledge = next(node for node in data["plan"]["nodes"] if node["id"] == "knowledge")
    assert knowledge["params"]["dataset_ids"] == ["request-dataset"]
    assert data["validation"]["ok"] is True


def test_create_knowledge_workflow_without_any_dataset_ids_returns_422(monkeypatch) -> None:
    _patch_runtime_model_context(monkeypatch, _test_settings())
    monkeypatch.setattr("app.main.load_settings", _test_settings)
    monkeypatch.setattr(
        "app.main.read_dify_version_info",
        lambda _: DifyVersionInfo(source_dir="../dify", git_describe="test", app_dsl_version="9.9.9"),
    )
    monkeypatch.setattr("app.main.WorkflowPlanner", _KnowledgePlanner)

    with TestClient(app) as client:
        response = client.post(
            "/api/workflows/create",
            json={"message": "根据知识库回答修车售后问题", "app_name": "知识库问答"},
        )

    assert response.status_code == 422
    assert any(issue["code"] == "PLAN_KNOWLEDGE_RETRIEVAL_DATASETS_MISSING" for issue in response.json()["detail"])


def test_get_workflow_draft_returns_app_detail_and_plan(monkeypatch) -> None:
    settings = _test_settings()
    compiler = DifyDslCompiler(
        dsl_version="9.9.9",
        default_model_provider="openai",
        default_model_name="gpt-4o-mini",
    )
    graph = yaml.safe_load(compiler.compile(fallback_plan("修车售后服务工作流", app_name="修车售后服务工作流")))["workflow"]["graph"]
    seen = {}

    class FakeDifyClient:
        def __init__(self, _settings):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *_):
            pass

        def get_app_detail(self, app_id):
            seen["app_id"] = app_id
            return DifyAppDetail(
                id=app_id,
                name="修车售后服务工作流",
                mode="workflow",
                description="处理修车售后诉求",
                raw={},
            )

        def get_draft_workflow(self, app_id):
            seen["draft_app_id"] = app_id
            return DifyDraftWorkflow(
                id="workflow-1",
                graph=graph,
                features={},
                hash="hash-1",
                version="draft",
                environment_variables=[],
                conversation_variables=[],
                raw={},
            )

    monkeypatch.setattr("app.main.load_settings", lambda: settings)
    monkeypatch.setattr(
        "app.main.read_dify_version_info",
        lambda _: DifyVersionInfo(source_dir="../dify", git_describe="test", app_dsl_version="9.9.9"),
    )
    monkeypatch.setattr("app.main.DifyClient", FakeDifyClient)

    with TestClient(app) as client:
        response = client.get("/api/workflows/app-1/draft")

    assert response.status_code == 200
    data = response.json()
    assert data["app_id"] == "app-1"
    assert data["workflow_url"] == "http://dify.local/app/app-1/workflow"
    assert data["base_hash"] == "hash-1"
    assert data["app"]["name"] == "修车售后服务工作流"
    assert data["plan"]["name"] == "修车售后服务工作流"
    assert data["validation"]["ok"] is True
    assert seen == {"app_id": "app-1", "draft_app_id": "app-1"}


def test_get_workflow_draft_falls_back_when_app_detail_fails(monkeypatch) -> None:
    settings = _test_settings()
    compiler = DifyDslCompiler(
        dsl_version="9.9.9",
        default_model_provider="openai",
        default_model_name="gpt-4o-mini",
    )
    graph = yaml.safe_load(compiler.compile(fallback_plan("hello", app_name="Loaded")))["workflow"]["graph"]

    class FakeDifyClient:
        def __init__(self, _settings):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *_):
            pass

        def get_app_detail(self, _app_id):
            raise DifyClientError("app detail unavailable")

        def get_draft_workflow(self, _app_id):
            return DifyDraftWorkflow(
                id="workflow-1",
                graph=graph,
                features={},
                hash="hash-1",
                version="draft",
                environment_variables=[],
                conversation_variables=[],
                raw={},
            )

    monkeypatch.setattr("app.main.load_settings", lambda: settings)
    monkeypatch.setattr(
        "app.main.read_dify_version_info",
        lambda _: DifyVersionInfo(source_dir="../dify", git_describe="test", app_dsl_version="9.9.9"),
    )
    monkeypatch.setattr("app.main.DifyClient", FakeDifyClient)

    with TestClient(app) as client:
        response = client.get("/api/workflows/app-1/draft")

    assert response.status_code == 200
    data = response.json()
    assert data["app"] is None
    assert data["plan"]["name"] == "Dify Workflow app-1"
    assert data["base_hash"] == "hash-1"


def test_apply_workflow_modification_syncs_dify_draft(monkeypatch) -> None:
    settings = _test_settings()
    compiler = DifyDslCompiler(
        dsl_version="9.9.9",
        default_model_provider="openai",
        default_model_name="gpt-4o-mini",
    )
    graph = yaml.safe_load(compiler.compile(fallback_plan("hello", app_name="Loaded")))["workflow"]["graph"]
    seen = {}

    class FakeDifyClient:
        def __init__(self, _settings):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *_):
            pass

        def get_draft_workflow(self, app_id):
            seen["app_id"] = app_id
            return DifyDraftWorkflow(
                id="workflow-1",
                graph=graph,
                features={"file_upload": {"enabled": False}},
                hash="hash-1",
                version="draft",
                environment_variables=[],
                conversation_variables=[],
                raw={},
            )

        def sync_draft_workflow(self, app_id, **kwargs):
            seen["sync_app_id"] = app_id
            seen["sync_hash"] = kwargs["hash"]
            seen["sync_graph"] = kwargs["graph"]
            return DifyDraftSyncResult(
                result="success",
                hash="hash-2",
                updated_at="123",
                workflow_url=settings.workflow_url(app_id),
            )

    class FakeEditPlanner:
        def __init__(self, _settings):
            pass

        def generate(self, message, *, current_plan, dsl_version, **_kwargs):
            revised = current_plan.model_copy(deep=True)
            revised.nodes[1].params["user_prompt"] = f"Changed: {message} {{{{#start.query#}}}}"
            return WorkflowEditResult(
                plan=revised,
                raw_plan=revised.model_dump(),
                attempts=1,
                repaired=False,
            )

    monkeypatch.setattr("app.main.load_settings", lambda: settings)
    monkeypatch.setattr(
        "app.main.read_dify_version_info",
        lambda _: DifyVersionInfo(source_dir="../dify", git_describe="test", app_dsl_version="9.9.9"),
    )
    monkeypatch.setattr("app.main.DifyClient", FakeDifyClient)
    monkeypatch.setattr("app.main.WorkflowEditPlanner", FakeEditPlanner)

    with TestClient(app) as client:
        response = client.post(
            "/api/workflows/modify/apply",
            json={"app_id": "app-1", "message": "use a warmer tone", "expected_hash": "hash-1"},
        )

    assert response.status_code == 200
    data = response.json()
    assert data["base_hash"] == "hash-1"
    assert data["new_hash"] == "hash-2"
    assert data["validation"]["ok"] is True
    assert data["guard"]["ok"] is True
    assert data["sync"]["result"] == "success"
    assert seen["sync_hash"] == "hash-1"
    assert seen["sync_graph"]["nodes"]


def test_modify_draft_request_dataset_ids_fill_added_knowledge_node(monkeypatch) -> None:
    settings = _test_settings(dataset_ids="env-dataset")
    compiler = DifyDslCompiler(
        dsl_version="9.9.9",
        default_model_provider="openai",
        default_model_name="gpt-4o-mini",
    )
    graph = yaml.safe_load(compiler.compile(fallback_plan("hello", app_name="Loaded")))["workflow"]["graph"]

    class FakeDifyClient:
        def __init__(self, _settings):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *_):
            pass

        def get_draft_workflow(self, _app_id):
            return DifyDraftWorkflow(
                id="workflow-1",
                graph=graph,
                features={},
                hash="hash-1",
                version="draft",
                environment_variables=[],
                conversation_variables=[],
                raw={},
            )

    class FakeEditPlanner:
        def __init__(self, planner_settings):
            self.settings = planner_settings

        def generate(self, _message, *, current_plan, dsl_version, **_kwargs):
            revised = _knowledge_plan(current_plan.name, self.settings.dify_default_dataset_ids)
            return WorkflowEditResult(plan=revised, raw_plan=revised.model_dump(), attempts=1, repaired=False)

    monkeypatch.setattr("app.main.load_settings", lambda: settings)
    monkeypatch.setattr(
        "app.main.read_dify_version_info",
        lambda _: DifyVersionInfo(source_dir="../dify", git_describe="test", app_dsl_version="9.9.9"),
    )
    monkeypatch.setattr("app.main.DifyClient", FakeDifyClient)
    monkeypatch.setattr("app.main.WorkflowEditPlanner", FakeEditPlanner)

    with TestClient(app) as client:
        response = client.post(
            "/api/workflows/modify/draft",
            json={
                "app_id": "app-1",
                "message": "增加知识库检索",
                "dataset_ids": ["request-dataset"],
                "allow_destructive": True,
            },
        )

    assert response.status_code == 200
    data = response.json()
    knowledge = next(node for node in data["plan"]["nodes"] if node["id"] == "knowledge")
    assert knowledge["params"]["dataset_ids"] == ["request-dataset"]


def test_apply_workflow_modification_with_preview_plan_does_not_replan(monkeypatch) -> None:
    settings = _test_settings()
    compiler = DifyDslCompiler(
        dsl_version="9.9.9",
        default_model_provider="openai",
        default_model_name="gpt-4o-mini",
    )
    current_plan = fallback_plan("hello", app_name="Loaded")
    graph = yaml.safe_load(compiler.compile(current_plan))["workflow"]["graph"]
    preview_plan = current_plan.model_copy(deep=True)
    preview_plan.nodes[1].params["user_prompt"] = "Reviewed preview {{#start.query#}}"
    seen = {}

    class FakeDifyClient:
        def __init__(self, _settings):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *_):
            pass

        def get_draft_workflow(self, _app_id):
            return DifyDraftWorkflow(
                id="workflow-1",
                graph=graph,
                features={},
                hash="hash-1",
                version="draft",
                environment_variables=[],
                conversation_variables=[],
                raw={},
            )

        def sync_draft_workflow(self, app_id, **kwargs):
            seen["sync_app_id"] = app_id
            seen["sync_hash"] = kwargs["hash"]
            seen["sync_graph"] = kwargs["graph"]
            return DifyDraftSyncResult(
                result="success",
                hash="hash-2",
                updated_at="123",
                workflow_url=settings.workflow_url(app_id),
            )

    class FailingEditPlanner:
        def __init__(self, _settings):
            raise AssertionError("apply with plan must not instantiate edit planner")

    monkeypatch.setattr("app.main.load_settings", lambda: settings)
    monkeypatch.setattr(
        "app.main.read_dify_version_info",
        lambda _: DifyVersionInfo(source_dir="../dify", git_describe="test", app_dsl_version="9.9.9"),
    )
    monkeypatch.setattr("app.main.DifyClient", FakeDifyClient)
    monkeypatch.setattr("app.main.WorkflowEditPlanner", FailingEditPlanner)

    with TestClient(app) as client:
        response = client.post(
            "/api/workflows/modify/apply",
            json={
                "app_id": "app-1",
                "message": "apply reviewed preview",
                "expected_hash": "hash-1",
                "plan": preview_plan.model_dump(),
                "trigger_selection": {
                    "type": "plugin",
                    "provider_id": "stale-provider",
                    "event_name": "stale-event",
                    "subscription_id": "stale-subscription",
                },
            },
        )

    assert response.status_code == 200
    data = response.json()
    assert data["planner"]["mode"] == "preview-plan"
    assert data["planner"]["attempts"] == 0
    assert data["planner"]["replanned"] is False
    assert data["new_hash"] == "hash-2"
    assert data["sync"]["result"] == "success"
    assert seen["sync_hash"] == "hash-1"
    assert seen["sync_graph"]["nodes"]
    assert any(node["data"]["type"] == "start" for node in seen["sync_graph"]["nodes"])


def test_apply_workflow_modification_with_preview_plan_preserves_preview_dataset_ids(monkeypatch) -> None:
    settings = _test_settings(dataset_ids="env-dataset")
    compiler = DifyDslCompiler(
        dsl_version="9.9.9",
        default_model_provider="openai",
        default_model_name="gpt-4o-mini",
    )
    current_plan = fallback_plan("hello", app_name="Loaded")
    graph = yaml.safe_load(compiler.compile(current_plan))["workflow"]["graph"]
    preview_plan = _knowledge_plan("Loaded", ["preview-dataset"])
    seen = {}

    class FakeDifyClient:
        def __init__(self, _settings):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *_):
            pass

        def get_draft_workflow(self, _app_id):
            return DifyDraftWorkflow(
                id="workflow-1",
                graph=graph,
                features={},
                hash="hash-1",
                version="draft",
                environment_variables=[],
                conversation_variables=[],
                raw={},
            )

        def sync_draft_workflow(self, app_id, **kwargs):
            seen["sync_graph"] = kwargs["graph"]
            return DifyDraftSyncResult(
                result="success",
                hash="hash-2",
                updated_at="123",
                workflow_url=settings.workflow_url(app_id),
            )

    class FailingEditPlanner:
        def __init__(self, _settings):
            raise AssertionError("apply with plan must not instantiate edit planner")

    monkeypatch.setattr("app.main.load_settings", lambda: settings)
    monkeypatch.setattr(
        "app.main.read_dify_version_info",
        lambda _: DifyVersionInfo(source_dir="../dify", git_describe="test", app_dsl_version="9.9.9"),
    )
    monkeypatch.setattr("app.main.DifyClient", FakeDifyClient)
    monkeypatch.setattr("app.main.WorkflowEditPlanner", FailingEditPlanner)

    with TestClient(app) as client:
        response = client.post(
            "/api/workflows/modify/apply",
            json={
                "app_id": "app-1",
                "message": "apply reviewed preview",
                "expected_hash": "hash-1",
                "allow_destructive": True,
                "dataset_ids": ["request-dataset"],
                "plan": preview_plan.model_dump(),
            },
        )

    assert response.status_code == 200
    data = response.json()
    assert data["planner"]["mode"] == "preview-plan"
    knowledge = next(node["data"] for node in seen["sync_graph"]["nodes"] if node["id"] == "knowledge")
    assert knowledge["dataset_ids"] == ["preview-dataset"]


def test_apply_workflow_modification_with_preview_plan_hash_conflict(monkeypatch) -> None:
    settings = _test_settings()
    compiler = DifyDslCompiler(
        dsl_version="9.9.9",
        default_model_provider="openai",
        default_model_name="gpt-4o-mini",
    )
    plan = fallback_plan("hello", app_name="Loaded")
    graph = yaml.safe_load(compiler.compile(plan))["workflow"]["graph"]

    class FakeDifyClient:
        def __init__(self, _settings):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *_):
            pass

        def get_draft_workflow(self, _app_id):
            return DifyDraftWorkflow(
                id="workflow-1",
                graph=graph,
                features={},
                hash="hash-current",
                version="draft",
                environment_variables=[],
                conversation_variables=[],
                raw={},
            )

        def sync_draft_workflow(self, _app_id, **_kwargs):
            raise AssertionError("hash conflict should block before sync")

    class FailingEditPlanner:
        def __init__(self, _settings):
            raise AssertionError("apply with plan must not instantiate edit planner")

    monkeypatch.setattr("app.main.load_settings", lambda: settings)
    monkeypatch.setattr(
        "app.main.read_dify_version_info",
        lambda _: DifyVersionInfo(source_dir="../dify", git_describe="test", app_dsl_version="9.9.9"),
    )
    monkeypatch.setattr("app.main.DifyClient", FakeDifyClient)
    monkeypatch.setattr("app.main.WorkflowEditPlanner", FailingEditPlanner)

    with TestClient(app) as client:
        response = client.post(
            "/api/workflows/modify/apply",
            json={
                "app_id": "app-1",
                "message": "apply reviewed preview",
                "expected_hash": "stale",
                "plan": plan.model_dump(),
            },
        )

    assert response.status_code == 409
    assert response.json()["detail"]["current_hash"] == "hash-current"


def test_apply_workflow_modification_with_invalid_preview_plan_returns_422(monkeypatch) -> None:
    monkeypatch.setattr("app.main.load_settings", _test_settings)

    with TestClient(app) as client:
        response = client.post(
            "/api/workflows/modify/apply",
            json={
                "app_id": "app-1",
                "message": "apply invalid preview",
                "plan": {"name": "bad", "nodes": [], "edges": []},
            },
        )

    assert response.status_code == 422


def test_apply_workflow_modification_with_destructive_preview_plan_is_blocked(monkeypatch) -> None:
    settings = _test_settings()
    compiler = DifyDslCompiler(
        dsl_version="9.9.9",
        default_model_provider="openai",
        default_model_name="gpt-4o-mini",
    )
    current_plan = fallback_plan("hello", app_name="Loaded")
    graph = yaml.safe_load(compiler.compile(current_plan))["workflow"]["graph"]
    destructive_plan = current_plan.model_copy(deep=True)
    destructive_plan.nodes = [destructive_plan.nodes[0], destructive_plan.nodes[2]]
    destructive_plan.edges = [type(destructive_plan.edges[0])(source=destructive_plan.nodes[0].id, target=destructive_plan.nodes[1].id)]
    destructive_plan.nodes[1].params["outputs"] = [{"variable": "answer", "value_selector": ["start", "query"]}]
    seen = {"synced": False}

    class FakeDifyClient:
        def __init__(self, _settings):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *_):
            pass

        def get_draft_workflow(self, _app_id):
            return DifyDraftWorkflow(
                id="workflow-1",
                graph=graph,
                features={},
                hash="hash-1",
                version="draft",
                environment_variables=[],
                conversation_variables=[],
                raw={},
            )

        def sync_draft_workflow(self, _app_id, **_kwargs):
            seen["synced"] = True
            raise AssertionError("destructive preview plan should be blocked before sync")

    class FailingEditPlanner:
        def __init__(self, _settings):
            raise AssertionError("apply with plan must not instantiate edit planner")

    monkeypatch.setattr("app.main.load_settings", lambda: settings)
    monkeypatch.setattr(
        "app.main.read_dify_version_info",
        lambda _: DifyVersionInfo(source_dir="../dify", git_describe="test", app_dsl_version="9.9.9"),
    )
    monkeypatch.setattr("app.main.DifyClient", FakeDifyClient)
    monkeypatch.setattr("app.main.WorkflowEditPlanner", FailingEditPlanner)

    with TestClient(app) as client:
        response = client.post(
            "/api/workflows/modify/apply",
            json={
                "app_id": "app-1",
                "message": "apply destructive preview",
                "expected_hash": "hash-1",
                "plan": destructive_plan.model_dump(),
            },
        )

    assert response.status_code == 422
    assert response.json()["detail"]["code"] == "PLAN_CHANGE_GUARD_BLOCKED"
    assert seen["synced"] is False


def test_apply_workflow_modification_blocks_destructive_change(monkeypatch) -> None:
    settings = _test_settings()
    compiler = DifyDslCompiler(
        dsl_version="9.9.9",
        default_model_provider="openai",
        default_model_name="gpt-4o-mini",
    )
    graph = yaml.safe_load(compiler.compile(fallback_plan("hello", app_name="Loaded")))["workflow"]["graph"]
    seen = {"synced": False}

    class FakeDifyClient:
        def __init__(self, _settings):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *_):
            pass

        def get_draft_workflow(self, _app_id):
            return DifyDraftWorkflow(
                id="workflow-1",
                graph=graph,
                features={},
                hash="hash-1",
                version="draft",
                environment_variables=[],
                conversation_variables=[],
                raw={},
            )

        def sync_draft_workflow(self, _app_id, **_kwargs):
            seen["synced"] = True
            raise AssertionError("destructive changes should be blocked before sync")

    class FakeEditPlanner:
        def __init__(self, _settings):
            pass

        def generate(self, _message, *, current_plan, dsl_version, **_kwargs):
            revised = current_plan.model_copy(deep=True)
            revised.nodes = [revised.nodes[0], revised.nodes[2]]
            revised.edges = [type(revised.edges[0])(source=revised.nodes[0].id, target=revised.nodes[1].id)]
            revised.nodes[1].params["outputs"] = [{"variable": "answer", "value_selector": ["start", "query"]}]
            return WorkflowEditResult(plan=revised, raw_plan=revised.model_dump(), attempts=1, repaired=False)

    monkeypatch.setattr("app.main.load_settings", lambda: settings)
    monkeypatch.setattr(
        "app.main.read_dify_version_info",
        lambda _: DifyVersionInfo(source_dir="../dify", git_describe="test", app_dsl_version="9.9.9"),
    )
    monkeypatch.setattr("app.main.DifyClient", FakeDifyClient)
    monkeypatch.setattr("app.main.WorkflowEditPlanner", FakeEditPlanner)

    with TestClient(app) as client:
        response = client.post(
            "/api/workflows/modify/apply",
            json={"app_id": "app-1", "message": "make it shorter"},
        )

    assert response.status_code == 422
    detail = response.json()["detail"]
    assert detail["code"] == "PLAN_CHANGE_GUARD_BLOCKED"
    assert detail["guard"]["risk"] == "high"
    assert seen["synced"] is False


def test_apply_workflow_modification_allows_destructive_change_when_confirmed(monkeypatch) -> None:
    settings = _test_settings()
    compiler = DifyDslCompiler(
        dsl_version="9.9.9",
        default_model_provider="openai",
        default_model_name="gpt-4o-mini",
    )
    graph = yaml.safe_load(compiler.compile(fallback_plan("hello", app_name="Loaded")))["workflow"]["graph"]
    seen = {"synced": False}

    class FakeDifyClient:
        def __init__(self, _settings):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *_):
            pass

        def get_draft_workflow(self, _app_id):
            return DifyDraftWorkflow(
                id="workflow-1",
                graph=graph,
                features={},
                hash="hash-1",
                version="draft",
                environment_variables=[],
                conversation_variables=[],
                raw={},
            )

        def sync_draft_workflow(self, app_id, **kwargs):
            seen["synced"] = True
            return DifyDraftSyncResult(
                result="success",
                hash="hash-2",
                updated_at="123",
                workflow_url=settings.workflow_url(app_id),
            )

    class FakeEditPlanner:
        def __init__(self, _settings):
            pass

        def generate(self, _message, *, current_plan, dsl_version, **_kwargs):
            revised = current_plan.model_copy(deep=True)
            revised.nodes = [revised.nodes[0], revised.nodes[2]]
            revised.edges = [type(revised.edges[0])(source=revised.nodes[0].id, target=revised.nodes[1].id)]
            revised.nodes[1].params["outputs"] = [{"variable": "answer", "value_selector": ["start", "query"]}]
            return WorkflowEditResult(plan=revised, raw_plan=revised.model_dump(), attempts=1, repaired=False)

    monkeypatch.setattr("app.main.load_settings", lambda: settings)
    monkeypatch.setattr(
        "app.main.read_dify_version_info",
        lambda _: DifyVersionInfo(source_dir="../dify", git_describe="test", app_dsl_version="9.9.9"),
    )
    monkeypatch.setattr("app.main.DifyClient", FakeDifyClient)
    monkeypatch.setattr("app.main.WorkflowEditPlanner", FakeEditPlanner)

    with TestClient(app) as client:
        response = client.post(
            "/api/workflows/modify/apply",
            json={"app_id": "app-1", "message": "rebuild it", "allow_destructive": True},
        )

    assert response.status_code == 200
    data = response.json()
    assert data["guard"]["risk"] == "high"
    assert data["new_hash"] == "hash-2"
    assert seen["synced"] is True


def test_apply_workflow_modification_noop_does_not_sync(monkeypatch) -> None:
    settings = _test_settings()
    compiler = DifyDslCompiler(
        dsl_version="9.9.9",
        default_model_provider="openai",
        default_model_name="gpt-4o-mini",
    )
    graph = yaml.safe_load(compiler.compile(fallback_plan("hello", app_name="Loaded")))["workflow"]["graph"]
    seen = {"synced": False}

    class FakeDifyClient:
        def __init__(self, _settings):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *_):
            pass

        def get_draft_workflow(self, _app_id):
            return DifyDraftWorkflow(
                id="workflow-1",
                graph=graph,
                features={},
                hash="hash-1",
                version="draft",
                environment_variables=[],
                conversation_variables=[],
                raw={},
            )

        def sync_draft_workflow(self, _app_id, **_kwargs):
            seen["synced"] = True
            raise AssertionError("no-op changes should not be synced")

    class FakeEditPlanner:
        def __init__(self, _settings):
            pass

        def generate(self, _message, *, current_plan, dsl_version, **_kwargs):
            return WorkflowEditResult(
                plan=current_plan,
                raw_plan=current_plan.model_dump(),
                attempts=1,
                repaired=False,
            )

    monkeypatch.setattr("app.main.load_settings", lambda: settings)
    monkeypatch.setattr(
        "app.main.read_dify_version_info",
        lambda _: DifyVersionInfo(source_dir="../dify", git_describe="test", app_dsl_version="9.9.9"),
    )
    monkeypatch.setattr("app.main.DifyClient", FakeDifyClient)
    monkeypatch.setattr("app.main.WorkflowEditPlanner", FakeEditPlanner)

    with TestClient(app) as client:
        response = client.post(
            "/api/workflows/modify/apply",
            json={"app_id": "app-1", "message": "do nothing"},
        )

    assert response.status_code == 200
    data = response.json()
    assert data["guard"]["no_op"] is True
    assert data["new_hash"] == "hash-1"
    assert data["sync"]["result"] == "noop"
    assert seen["synced"] is False


def test_apply_workflow_modification_syncs_noop_when_graph_is_normalized(monkeypatch) -> None:
    settings = _test_settings()
    compiler = DifyDslCompiler(
        dsl_version="9.9.9",
        default_model_provider="openai",
        default_model_name="gpt-4o-mini",
    )
    normalized = normalize_plan_payload(
        {
            "name": "Loaded",
            "nodes": [
                {"id": "start", "type": "start", "params": {"variables": [{"name": "query"}]}},
                {
                    "id": "review",
                    "type": "human-input",
                    "title": "人工审核",
                    "params": {
                        "delivery_methods": [{"id": "webapp-1", "type": "webapp", "enabled": True, "config": {}}],
                        "form_content": "请审核：{{#start.query#}}",
                        "inputs": [],
                        "user_actions": [
                            {"id": "approve", "title": "通过", "button_style": "primary"},
                            {"id": "reject", "title": "驳回", "button_style": "default"},
                        ],
                        "timeout": 3,
                        "timeout_unit": "day",
                    },
                },
                {"id": "approved", "type": "end", "params": {"outputs": [{"variable": "action", "value_selector": ["review", "__action_id"]}]}},
                {"id": "rejected", "type": "end", "params": {"outputs": [{"variable": "action", "value_selector": ["review", "__action_id"]}]}},
            ],
            "edges": [
                {"source": "start", "target": "review"},
                {"source": "review", "target": "approved", "source_handle": "approve"},
                {"source": "review", "target": "rejected", "source_handle": "reject"},
            ],
        }
    )
    graph = yaml.safe_load(compiler.compile(WorkflowPlan.model_validate(normalized.payload)))["workflow"]["graph"]
    review = next(node["data"] for node in graph["nodes"] if node["id"] == "review")
    review["delivery_methods"][0]["id"] = "webapp-1"
    seen = {"synced": False, "delivery_id": None}

    class FakeDifyClient:
        def __init__(self, _settings):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *_):
            pass

        def get_draft_workflow(self, _app_id):
            return DifyDraftWorkflow(
                id="workflow-1",
                graph=graph,
                features={},
                hash="hash-1",
                version="draft",
                environment_variables=[],
                conversation_variables=[],
                raw={},
            )

        def sync_draft_workflow(self, _app_id, **kwargs):
            seen["synced"] = True
            synced_review = next(node["data"] for node in kwargs["graph"]["nodes"] if node["id"] == "review")
            seen["delivery_id"] = synced_review["delivery_methods"][0]["id"]
            return DifyDraftSyncResult(result="success", hash="hash-2", updated_at="", workflow_url="/app/app-1/workflow")

    class FakeEditPlanner:
        def __init__(self, _settings):
            pass

        def generate(self, _message, *, current_plan, dsl_version, **_kwargs):
            return WorkflowEditResult(
                plan=current_plan,
                raw_plan=current_plan.model_dump(),
                attempts=1,
                repaired=False,
            )

    monkeypatch.setattr("app.main.load_settings", lambda: settings)
    monkeypatch.setattr(
        "app.main.read_dify_version_info",
        lambda _: DifyVersionInfo(source_dir="../dify", git_describe="test", app_dsl_version="9.9.9"),
    )
    monkeypatch.setattr("app.main.DifyClient", FakeDifyClient)
    monkeypatch.setattr("app.main.WorkflowEditPlanner", FakeEditPlanner)

    with TestClient(app) as client:
        response = client.post(
            "/api/workflows/modify/apply",
            json={"app_id": "app-1", "message": "normalize graph only"},
        )

    assert response.status_code == 200
    data = response.json()
    assert data["guard"]["no_op"] is True
    assert data["new_hash"] == "hash-2"
    assert seen["synced"] is True
    assert seen["delivery_id"] != "webapp-1"


def test_workflow_modification_expected_hash_conflict(monkeypatch) -> None:
    settings = _test_settings()
    compiler = DifyDslCompiler(
        dsl_version="9.9.9",
        default_model_provider="openai",
        default_model_name="gpt-4o-mini",
    )
    graph = yaml.safe_load(compiler.compile(fallback_plan("hello", app_name="Loaded")))["workflow"]["graph"]

    class FakeDifyClient:
        def __init__(self, _settings):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *_):
            pass

        def get_draft_workflow(self, _app_id):
            return DifyDraftWorkflow(
                id="workflow-1",
                graph=graph,
                features={},
                hash="hash-current",
                version="draft",
                environment_variables=[],
                conversation_variables=[],
                raw={},
            )

    monkeypatch.setattr("app.main.load_settings", lambda: settings)
    monkeypatch.setattr(
        "app.main.read_dify_version_info",
        lambda _: DifyVersionInfo(source_dir="../dify", git_describe="test", app_dsl_version="9.9.9"),
    )
    monkeypatch.setattr("app.main.DifyClient", FakeDifyClient)

    with TestClient(app) as client:
        response = client.post(
            "/api/workflows/modify/draft",
            json={"app_id": "app-1", "message": "change", "expected_hash": "stale"},
        )

    assert response.status_code == 409
    assert response.json()["detail"]["code"] == "DRAFT_HASH_MISMATCH"


def test_run_draft_workflow_api_returns_summary(monkeypatch) -> None:
    settings = _test_settings()
    seen = {}

    class FakeDifyClient:
        def __init__(self, _settings):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *_):
            pass

        def run_draft_workflow(self, app_id, *, inputs, files=None, timeout_seconds=120):
            seen["app_id"] = app_id
            seen["inputs"] = inputs
            seen["files"] = files
            seen["timeout_seconds"] = timeout_seconds
            return DifyDraftRunResult(
                ok=True,
                status="succeeded",
                app_id=app_id,
                workflow_url=settings.workflow_url(app_id),
                workflow_run_id="run-1",
                task_id="task-1",
                outputs={"answer": "ok"},
                elapsed_time=1.0,
                total_tokens=2,
                total_steps=3,
                events_summary={"events": 2},
                final_event={"event": "workflow_finished", "data": {"status": "succeeded"}},
            )

    monkeypatch.setattr("app.main.load_settings", lambda: settings)
    monkeypatch.setattr("app.main.DifyClient", FakeDifyClient)

    with TestClient(app) as client:
        response = client.post(
            "/api/workflows/run/draft",
            json={"app_id": "app-1", "inputs": {"query": "hi"}, "files": [], "timeout_seconds": 5},
        )

    assert response.status_code == 200
    data = response.json()
    assert data["ok"] is True
    assert data["status"] == "succeeded"
    assert data["outputs"] == {"answer": "ok"}
    assert seen == {"app_id": "app-1", "inputs": {"query": "hi"}, "files": [], "timeout_seconds": 5.0}


def test_run_draft_workflow_api_requires_inputs(monkeypatch) -> None:
    monkeypatch.setattr("app.main.load_settings", _test_settings)

    with TestClient(app) as client:
        response = client.post("/api/workflows/run/draft", json={"app_id": "app-1"})

    assert response.status_code == 422


def test_run_draft_workflow_rejects_agent_app(monkeypatch) -> None:
    class FakeDifyClient:
        def __init__(self, _settings):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *_):
            pass

        def get_app_detail(self, app_id):
            return DifyAppDetail(
                id=app_id,
                name="Agent",
                mode="agent-chat",
                description="",
                raw={},
            )

    monkeypatch.setattr("app.main.load_settings", _test_settings)
    monkeypatch.setattr("app.main.DifyClient", FakeDifyClient)

    with TestClient(app) as client:
        response = client.post(
            "/api/workflows/run/draft",
            json={"app_id": "agent-app-1", "inputs": {"query": "hi"}},
        )

    assert response.status_code == 422
    assert response.json()["detail"]["code"] == "AGENT_USE_AGENT_RUN_API"


def test_run_draft_workflow_rejects_chatbot_app(monkeypatch) -> None:
    class FakeDifyClient:
        def __init__(self, _settings):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *_):
            pass

        def get_app_detail(self, app_id):
            return DifyAppDetail(
                id=app_id,
                name="售后聊天助手",
                mode="chat",
                description="",
                raw={},
            )

        def get_draft_workflow(self, _app_id):
            raise AssertionError("Chatbot workflow run should reject before loading draft.")

    monkeypatch.setattr("app.main.load_settings", _test_settings)
    monkeypatch.setattr("app.main.DifyClient", FakeDifyClient)

    with TestClient(app) as client:
        response = client.post(
            "/api/workflows/run/draft",
            json={"app_id": "chat-app-1", "inputs": {"query": "hi"}},
        )

    assert response.status_code == 422
    assert response.json()["detail"]["code"] == "CHATBOT_USE_CHATBOT_RUN_API"


def test_run_draft_workflow_rejects_completion_app(monkeypatch) -> None:
    class FakeDifyClient:
        def __init__(self, _settings):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *_):
            pass

        def get_app_detail(self, app_id):
            return DifyAppDetail(
                id=app_id,
                name="文本生成",
                mode="completion",
                description="",
                raw={},
            )

        def get_draft_workflow(self, _app_id):
            raise AssertionError("Completion workflow run should reject before loading draft.")

    monkeypatch.setattr("app.main.load_settings", _test_settings)
    monkeypatch.setattr("app.main.DifyClient", FakeDifyClient)

    with TestClient(app) as client:
        response = client.post(
            "/api/workflows/run/draft",
            json={"app_id": "completion-app-1", "inputs": {"query": "hi"}},
        )

    assert response.status_code == 422
    assert response.json()["detail"]["code"] == "COMPLETION_USE_COMPLETION_RUN_API"


def test_run_draft_workflow_api_can_return_timeout(monkeypatch) -> None:
    settings = _test_settings()

    class FakeDifyClient:
        def __init__(self, _settings):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *_):
            pass

        def run_draft_workflow(self, app_id, *, inputs, files=None, timeout_seconds=120):
            return DifyDraftRunResult(
                ok=False,
                status="timeout",
                app_id=app_id,
                workflow_url=settings.workflow_url(app_id),
                error="timed out",
                events_summary={"events": 1},
            )

    monkeypatch.setattr("app.main.load_settings", lambda: settings)
    monkeypatch.setattr("app.main.DifyClient", FakeDifyClient)

    with TestClient(app) as client:
        response = client.post(
            "/api/workflows/run/draft",
            json={"app_id": "app-1", "inputs": {"query": "hi"}, "timeout_seconds": 1},
        )

    assert response.status_code == 200
    data = response.json()
    assert data["ok"] is False
    assert data["status"] == "timeout"


def test_run_draft_chatflow_api_reuses_conversation(monkeypatch) -> None:
    settings = _test_settings()
    seen = {}

    class FakeDifyClient:
        def __init__(self, _settings):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *_):
            pass

        def get_app_detail(self, app_id):
            return DifyAppDetail(
                id=app_id,
                name="汽车售后多轮客服",
                mode="advanced-chat",
                description="",
                raw={},
            )

        def run_draft_chatflow(self, app_id, **kwargs):
            seen["app_id"] = app_id
            seen.update(kwargs)
            return DifyChatflowRunResult(
                ok=True,
                status="succeeded",
                app_id=app_id,
                workflow_url=settings.workflow_url(app_id),
                answer="你刚才提到发动机抖动。",
                conversation_id="conversation-1",
                message_id="message-2",
                workflow_run_id="run-2",
                task_id="task-2",
                total_tokens=32,
                total_steps=3,
                events_summary={"events": 4},
                final_event={"event": "workflow_finished", "data": {"status": "succeeded"}},
            )

    monkeypatch.setattr("app.main.load_settings", lambda: settings)
    monkeypatch.setattr("app.main.DifyClient", FakeDifyClient)

    with TestClient(app) as client:
        response = client.post(
            "/api/chatflows/run/draft",
            json={
                "app_id": "chat-app-1",
                "query": "我刚才说的故障是什么",
                "inputs": {},
                "files": [],
                "conversation_id": "conversation-1",
                "parent_message_id": "message-1",
                "timeout_seconds": 15,
            },
        )

    assert response.status_code == 200
    data = response.json()
    assert data["answer"] == "你刚才提到发动机抖动。"
    assert data["conversation_id"] == "conversation-1"
    assert data["message_id"] == "message-2"
    assert seen == {
        "app_id": "chat-app-1",
        "query": "我刚才说的故障是什么",
        "inputs": {},
        "files": [],
        "conversation_id": "conversation-1",
        "parent_message_id": "message-1",
        "timeout_seconds": 15.0,
    }


def test_run_draft_chatbot_api_reuses_conversation(monkeypatch) -> None:
    settings = _test_settings()
    seen = {}
    model_config = {
        "model": {"provider": "openai", "name": "gpt-4o", "mode": "chat"},
        "pre_prompt": "help",
    }

    class FakeDifyClient:
        def __init__(self, _settings):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *_):
            pass

        def get_app_detail(self, app_id):
            return DifyAppDetail(
                id=app_id,
                name="售后聊天助手",
                mode="chat",
                description="",
                raw={"model_config": model_config},
            )

        def run_chatbot_chat(self, app_id, **kwargs):
            seen["app_id"] = app_id
            seen.update(kwargs)
            return DifyChatflowRunResult(
                ok=True,
                status="succeeded",
                app_id=app_id,
                workflow_url=settings.app_url(app_id, "chat"),
                answer="请提供维修单号。",
                conversation_id="conv-next",
                message_id="msg-next",
                events_summary={"events": 3},
            )

    monkeypatch.setattr("app.main.load_settings", lambda: settings)
    monkeypatch.setattr("app.main.DifyClient", FakeDifyClient)

    with TestClient(app) as client:
        response = client.post(
            "/api/chatbots/run/draft",
            json={
                "app_id": "chat-app-1",
                "query": "发动机抖动",
                "inputs": {"store": "城西"},
                "conversation_id": "conv-prev",
                "parent_message_id": "msg-prev",
            },
        )

    assert response.status_code == 200
    data = response.json()
    assert data["app_mode"] == "chat"
    assert data["answer"] == "请提供维修单号。"
    assert data["conversation_id"] == "conv-next"
    assert seen["app_id"] == "chat-app-1"
    assert seen["query"] == "发动机抖动"
    assert seen["inputs"] == {"store": "城西"}
    assert seen["conversation_id"] == "conv-prev"
    assert seen["parent_message_id"] == "msg-prev"
    assert seen["model_config"]["pre_prompt"] == "help"


def test_run_draft_completion_api_uses_model_config(monkeypatch) -> None:
    settings = _test_settings()
    seen = {}
    model_config = {
        "model": {"provider": "openai", "name": "gpt-4o", "mode": "chat"},
        "pre_prompt": "summarize",
    }

    class FakeDifyClient:
        def __init__(self, _settings):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *_):
            pass

        def get_app_detail(self, app_id):
            return DifyAppDetail(
                id=app_id,
                name="维修单总结",
                mode="completion",
                description="",
                raw={"model_config": model_config},
            )

        def run_completion(self, app_id, **kwargs):
            seen["app_id"] = app_id
            seen.update(kwargs)
            return DifyChatflowRunResult(
                ok=True,
                status="succeeded",
                app_id=app_id,
                workflow_url=settings.app_url(app_id, "completion"),
                answer="故障：发动机抖动。",
                message_id="msg-completion",
                events_summary={"events": 3},
            )

    monkeypatch.setattr("app.main.load_settings", lambda: settings)
    monkeypatch.setattr("app.main.DifyClient", FakeDifyClient)

    with TestClient(app) as client:
        response = client.post(
            "/api/completions/run/draft",
            json={
                "app_id": "completion-app-1",
                "query": "维修单原文",
                "inputs": {"style": "short"},
                "timeout_seconds": 15,
            },
        )

    assert response.status_code == 200
    data = response.json()
    assert data["app_mode"] == "completion"
    assert data["answer"] == "故障：发动机抖动。"
    assert seen == {
        "app_id": "completion-app-1",
        "query": "维修单原文",
        "inputs": {"style": "short"},
        "files": None,
        "model_config": model_config,
        "timeout_seconds": 15.0,
    }


def test_run_draft_agent_api_uses_model_config(monkeypatch) -> None:
    settings = _test_settings()
    seen = {}
    model_config = {
        "model": {"provider": "langgenius/tongyi/tongyi", "name": "qwen3.5-plus", "mode": "chat"},
        "agent_mode": {"enabled": True, "strategy": "react", "tools": [], "prompt": "help"},
    }

    class FakeDifyClient:
        def __init__(self, _settings):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *_):
            pass

        def get_app_detail(self, app_id):
            return DifyAppDetail(
                id=app_id,
                name="售后 Agent",
                mode="agent-chat",
                description="",
                raw={"model_config": model_config},
            )

        def run_agent_chat(self, app_id, **kwargs):
            seen["app_id"] = app_id
            seen.update(kwargs)
            return DifyChatflowRunResult(
                ok=True,
                status="succeeded",
                app_id=app_id,
                workflow_url=settings.app_url(app_id, "agent-chat"),
                answer="已分析。",
                conversation_id="conversation-1",
                message_id="message-2",
                task_id="task-2",
                total_tokens=12,
                events_summary={"events": 3},
                final_event={"event": "message_end", "id": "message-2"},
            )

    monkeypatch.setattr("app.main.load_settings", lambda: settings)
    monkeypatch.setattr("app.main.DifyClient", FakeDifyClient)

    with TestClient(app) as client:
        response = client.post(
            "/api/agents/run/draft",
            json={
                "app_id": "agent-app-1",
                "query": "分析这个售后问题",
                "inputs": {"priority": "high"},
                "conversation_id": "conversation-1",
                "parent_message_id": "message-1",
                "timeout_seconds": 20,
            },
        )

    assert response.status_code == 200
    data = response.json()
    assert data["app_mode"] == "agent-chat"
    assert data["answer"] == "已分析。"
    assert data["workflow_url"] == "http://dify.local/app/agent-app-1/configuration"
    assert seen == {
        "app_id": "agent-app-1",
        "query": "分析这个售后问题",
        "inputs": {"priority": "high"},
        "files": None,
        "conversation_id": "conversation-1",
        "parent_message_id": "message-1",
        "model_config": model_config,
        "timeout_seconds": 20.0,
    }


def test_run_draft_agent_rejects_non_agent_app(monkeypatch) -> None:
    class FakeDifyClient:
        def __init__(self, _settings):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *_):
            pass

        def get_app_detail(self, app_id):
            return DifyAppDetail(
                id=app_id,
                name="Workflow",
                mode="workflow",
                description="",
                raw={},
            )

    monkeypatch.setattr("app.main.load_settings", _test_settings)
    monkeypatch.setattr("app.main.DifyClient", FakeDifyClient)

    with TestClient(app) as client:
        response = client.post(
            "/api/agents/run/draft",
            json={"app_id": "workflow-app-1", "query": "hi"},
        )

    assert response.status_code == 422
    assert response.json()["detail"]["code"] == "APP_IS_NOT_AGENT"


def test_agent_modify_draft_updates_model_config_preview(monkeypatch) -> None:
    settings = _test_settings()
    model_config = {
        "model": {"provider": "langgenius/tongyi/tongyi", "name": "qwen-plus", "mode": "chat"},
        "pre_prompt": "你是售后问题分析 Agent。",
        "agent_mode": {
            "enabled": True,
            "strategy": "router",
            "tools": [{"enabled": True, "provider_type": "builtin", "provider_id": "old", "tool_name": "old"}],
            "prompt": "分析售后投诉。",
        },
        "updated_at": "hash-1",
    }
    selected_model = DifyModelListItem(
        provider="langgenius/tongyi/tongyi",
        provider_label="Tongyi",
        model="qwen-max",
        model_label="Qwen Max",
        model_type="llm",
        status="active",
        provider_status="active",
        deprecated=False,
        features=["tool-call"],
        mode="chat",
    )
    catalog = DifyModelListResult(
        data=[selected_model],
        count=1,
        model_type="llm",
        providers=[selected_model.provider],
        features=list(selected_model.features),
    )

    class FakeDifyClient:
        def __init__(self, _settings):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *_):
            pass

        def get_app_detail(self, app_id):
            return DifyAppDetail(
                id=app_id,
                name="售后 Agent",
                mode="agent-chat",
                description="",
                raw={"model_config": model_config},
            )

        def get_draft_workflow(self, _app_id):
            raise AssertionError("Agent modify must not load a workflow draft.")

        def update_model_config(self, _app_id, _payload):
            raise AssertionError("Preview must not write model_config.")

    monkeypatch.setattr("app.main.load_settings", lambda: settings)
    monkeypatch.setattr("app.main.read_dify_version_info", lambda _: DifyVersionInfo("../dify", "test", "9.9.9"))
    monkeypatch.setattr("app.main.DifyClient", FakeDifyClient)
    monkeypatch.setattr(
        "app.main._runtime_model_context",
        lambda _settings, _selections, client=None: (catalog, [selected_model]),
    )

    with TestClient(app) as client:
        response = client.post(
            "/api/workflows/modify/draft",
            json={
                "app_id": "agent-app-1",
                "message": "调整回复风格，并使用新的工具",
                "model_selections": [{"provider": selected_model.provider, "model": selected_model.model}],
                "tool_selections": [
                    {
                        "provider_id": "langgenius/web/web",
                        "provider_type": "builtin",
                        "tool_name": "web_search",
                        "plugin_unique_identifier": "langgenius-web",
                        "tool_parameters": {"query": {"type": "mixed", "value": "{{#sys.query#}}"}},
                    }
                ],
            },
        )

    assert response.status_code == 200
    data = response.json()
    assert data["app_mode"] == "agent-chat"
    assert data["workflow_url"] == "http://dify.local/app/agent-app-1/configuration"
    assert data["base_hash"] == "hash-1"
    assert data["model_config"]["model"]["name"] == "qwen-max"
    assert data["model_config"]["agent_mode"]["enabled"] is True
    assert data["model_config"]["agent_mode"]["strategy"] == "router"
    assert data["model_config"]["agent_mode"]["tools"] == [
        {
            "enabled": True,
            "provider_type": "builtin",
            "provider_id": "langgenius/web/web",
            "tool_name": "web_search",
            "tool_parameters": {"query": {"type": "mixed", "value": "{{#sys.query#}}"}},
            "plugin_unique_identifier": "langgenius-web",
        }
    ]
    assert "Additional instruction" in data["model_config"]["pre_prompt"]
    assert "Additional instruction" in data["model_config"]["agent_mode"]["prompt"]
    assert LANGUAGE_RESPONSE_INSTRUCTION in data["model_config"]["pre_prompt"]
    assert LANGUAGE_RESPONSE_INSTRUCTION in data["model_config"]["agent_mode"]["prompt"]
    assert {change["type"] for change in data["changes"]} == {
        "prompt_changed",
        "model_changed",
        "agent_prompt_changed",
        "agent_tools_changed",
    }


def test_agent_modify_apply_preserves_tools_when_empty_selection(monkeypatch) -> None:
    settings = _test_settings()
    existing_tools = [
        {
            "enabled": True,
            "provider_type": "builtin",
            "provider_id": "langgenius/web/web",
            "tool_name": "web_search",
            "tool_parameters": {},
        }
    ]
    model_config = {
        "model": {"provider": "langgenius/tongyi/tongyi", "name": "qwen-plus", "mode": "chat"},
        "pre_prompt": "你是售后问题分析 Agent。",
        "agent_mode": {"tools": existing_tools, "prompt": "分析售后投诉。"},
        "updated_at": "hash-1",
    }
    seen = {}

    class FakeDifyClient:
        def __init__(self, _settings):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *_):
            pass

        def get_app_detail(self, app_id):
            raw_config = model_config
            if seen.get("payload") is not None:
                raw_config = {**seen["payload"], "updated_at": "hash-2"}
            return DifyAppDetail(
                id=app_id,
                name="售后 Agent",
                mode="agent-chat",
                description="",
                raw={"model_config": raw_config},
            )

        def get_draft_workflow(self, _app_id):
            raise AssertionError("Agent modify must not load a workflow draft.")

        def update_model_config(self, app_id, payload):
            seen["app_id"] = app_id
            seen["payload"] = payload
            return {"result": "success"}

    monkeypatch.setattr("app.main.load_settings", lambda: settings)
    monkeypatch.setattr("app.main.read_dify_version_info", lambda _: DifyVersionInfo("../dify", "test", "9.9.9"))
    monkeypatch.setattr("app.main.DifyClient", FakeDifyClient)
    _patch_runtime_model_context(monkeypatch, settings)

    with TestClient(app) as client:
        response = client.post(
            "/api/workflows/modify/apply",
            json={
                "app_id": "agent-app-1",
                "message": "把回复改得更温暖",
                "expected_hash": "hash-1",
                "tool_selections": [],
            },
        )

    assert response.status_code == 200
    data = response.json()
    assert data["app_mode"] == "agent-chat"
    assert data["new_hash"] == "hash-2"
    assert seen["app_id"] == "agent-app-1"
    assert seen["payload"]["agent_mode"]["enabled"] is True
    assert seen["payload"]["agent_mode"]["strategy"] == "react"
    assert seen["payload"]["agent_mode"]["tools"] == existing_tools
    assert "Additional instruction" in seen["payload"]["agent_mode"]["prompt"]
    assert LANGUAGE_RESPONSE_INSTRUCTION in seen["payload"]["pre_prompt"]
    assert LANGUAGE_RESPONSE_INSTRUCTION in seen["payload"]["agent_mode"]["prompt"]
    assert data["model_config"]["updated_at"] == "hash-2"


def test_chatbot_modify_apply_updates_model_config(monkeypatch) -> None:
    settings = _test_settings()
    model_config = {
        "model": {"provider": "openai", "name": "gpt-4o", "mode": "chat"},
        "pre_prompt": "你是汽车售后客服。",
        "opening_statement": "",
        "suggested_questions": [],
        "dataset_configs": {"retrieval_model": "multiple", "datasets": {"datasets": ["keep"]}},
        "updated_at": "hash-1",
    }
    seen = {}

    class FakeDifyClient:
        def __init__(self, _settings):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *_):
            pass

        def get_app_detail(self, app_id):
            raw_config = model_config
            if seen.get("payload") is not None:
                raw_config = {**seen["payload"], "updated_at": "hash-2"}
            return DifyAppDetail(
                id=app_id,
                name="售后聊天助手",
                mode="chat",
                description="",
                raw={"model_config": raw_config},
            )

        def get_draft_workflow(self, _app_id):
            raise AssertionError("Chatbot modify must not load a workflow draft.")

        def update_model_config(self, app_id, payload):
            seen["app_id"] = app_id
            seen["payload"] = payload
            return {"result": "success"}

    monkeypatch.setattr("app.main.load_settings", lambda: settings)
    monkeypatch.setattr("app.main.DifyClient", FakeDifyClient)

    with TestClient(app) as client:
        response = client.post(
            "/api/workflows/modify/apply",
            json={
                "app_id": "chat-app-1",
                "message": "把回复改得更温暖，开场白设为“您好，我是售后助手。”，建议问题“如何预约维修”“如何查询订单”",
                "expected_hash": "hash-1",
            },
        )

    assert response.status_code == 200
    data = response.json()
    assert data["app_mode"] == "chat"
    assert data["base_hash"] == "hash-1"
    assert data["new_hash"] == "hash-2"
    assert seen["app_id"] == "chat-app-1"
    assert seen["payload"]["dataset_configs"] == model_config["dataset_configs"]
    assert "Additional instruction" in seen["payload"]["pre_prompt"]
    assert LANGUAGE_RESPONSE_INSTRUCTION in seen["payload"]["pre_prompt"]
    assert seen["payload"]["opening_statement"] == "您好，我是售后助手。"
    assert seen["payload"]["suggested_questions"] == ["如何预约维修", "如何查询订单"]
    assert data["model_config"]["updated_at"] == "hash-2"


def test_completion_modify_apply_updates_model_config(monkeypatch) -> None:
    settings = _test_settings()
    model_config = {
        "model": {"provider": "openai", "name": "gpt-4o", "mode": "chat"},
        "pre_prompt": "总结维修单。",
        "dataset_configs": {"retrieval_model": "multiple", "datasets": {"datasets": []}},
        "updated_at": "hash-1",
    }
    seen = {}

    class FakeDifyClient:
        def __init__(self, _settings):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *_):
            pass

        def get_app_detail(self, app_id):
            raw_config = model_config
            if seen.get("payload") is not None:
                raw_config = {**seen["payload"], "updated_at": "hash-2"}
            return DifyAppDetail(
                id=app_id,
                name="维修单总结",
                mode="completion",
                description="",
                raw={"model_config": raw_config},
            )

        def get_draft_workflow(self, _app_id):
            raise AssertionError("Completion modify must not load a workflow draft.")

        def update_model_config(self, app_id, payload):
            seen["app_id"] = app_id
            seen["payload"] = payload
            return {"result": "success"}

    monkeypatch.setattr("app.main.load_settings", lambda: settings)
    monkeypatch.setattr("app.main.DifyClient", FakeDifyClient)

    with TestClient(app) as client:
        response = client.post(
            "/api/workflows/modify/apply",
            json={
                "app_id": "completion-app-1",
                "message": "输出更短，只保留故障、建议、待补充信息",
                "expected_hash": "hash-1",
            },
        )

    assert response.status_code == 200
    data = response.json()
    assert data["app_mode"] == "completion"
    assert data["workflow_url"] == "http://dify.local/app/completion-app-1/configuration"
    assert data["base_hash"] == "hash-1"
    assert data["new_hash"] == "hash-2"
    assert seen["app_id"] == "completion-app-1"
    assert seen["payload"]["dataset_configs"] == model_config["dataset_configs"]
    assert "Additional instruction" in seen["payload"]["pre_prompt"]
    assert LANGUAGE_RESPONSE_INSTRUCTION in seen["payload"]["pre_prompt"]
    assert "opening_statement" not in seen["payload"]
    assert "suggested_questions" not in seen["payload"]
    assert data["model_config"]["updated_at"] == "hash-2"


def test_chatflow_modify_preview_uses_advanced_chat_plan(monkeypatch) -> None:
    settings = _test_settings()
    current_plan = fallback_plan(
        "创建汽车售后多轮客服",
        app_name="汽车售后多轮客服",
        app_mode="advanced-chat",
    )
    graph = yaml.safe_load(
        DifyDslCompiler(
            dsl_version="9.9.9",
            default_model_provider="openai",
            default_model_name="gpt-4o-mini",
        ).compile(current_plan)
    )["workflow"]["graph"]

    class FakeDifyClient:
        def __init__(self, _settings):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *_):
            pass

        def get_app_detail(self, app_id):
            return DifyAppDetail(
                id=app_id,
                name="汽车售后多轮客服",
                mode="advanced-chat",
                description="",
                raw={},
            )

        def get_draft_workflow(self, _app_id):
            return DifyDraftWorkflow(
                id="workflow-1",
                graph=graph,
                features={"file_upload": {"enabled": False}},
                hash="hash-1",
                version="draft",
                environment_variables=[],
                conversation_variables=[{"name": "topic", "value_type": "string", "value": ""}],
                raw={},
            )

    class FakeEditPlanner:
        def __init__(self, _settings):
            pass

        def generate(self, message, *, current_plan, dsl_version, trigger_selection=None, **_kwargs):
            assert current_plan.app_mode == "advanced-chat"
            assert trigger_selection is None
            revised = current_plan.model_copy(deep=True)
            revised.nodes[1].params["system_prompt"] = f"温暖地回复：{message}"
            return WorkflowEditResult(
                plan=revised,
                raw_plan=revised.model_dump(),
                attempts=1,
                repaired=False,
            )

    monkeypatch.setattr("app.main.load_settings", lambda: settings)
    monkeypatch.setattr("app.main.read_dify_version_info", lambda _: DifyVersionInfo("../dify", "test", "9.9.9"))
    monkeypatch.setattr("app.main.DifyClient", FakeDifyClient)
    monkeypatch.setattr("app.main.WorkflowEditPlanner", FakeEditPlanner)

    with TestClient(app) as client:
        response = client.post(
            "/api/workflows/modify/draft",
            json={"app_id": "chat-app-1", "message": "把回复改得更温暖"},
        )

    assert response.status_code == 200
    data = response.json()
    assert data["app_mode"] == "advanced-chat"
    assert data["plan"]["app_mode"] == "advanced-chat"
    assert data["validation"]["ok"] is True
    assert data["base_hash"] == "hash-1"
    assert any(change["type"] == "prompt_changed" for change in data["changes"])


def test_chatflow_modify_rejects_workflow_trigger_before_loading_draft(monkeypatch) -> None:
    settings = _test_settings()

    class FakeDifyClient:
        def __init__(self, _settings):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *_):
            pass

        def get_app_detail(self, app_id):
            return DifyAppDetail(
                id=app_id,
                name="汽车售后多轮客服",
                mode="advanced-chat",
                description="",
                raw={},
            )

        def get_draft_workflow(self, _app_id):
            raise AssertionError("Invalid Chatflow trigger should be rejected before loading the draft.")

    monkeypatch.setattr("app.main.load_settings", lambda: settings)
    monkeypatch.setattr("app.main.read_dify_version_info", lambda _: DifyVersionInfo("../dify", "test", "9.9.9"))
    monkeypatch.setattr("app.main.DifyClient", FakeDifyClient)

    with TestClient(app) as client:
        response = client.post(
            "/api/workflows/modify/draft",
            json={
                "app_id": "chat-app-1",
                "message": "把回复改得更温暖",
                "trigger_selection": {"type": "webhook"},
            },
        )

    assert response.status_code == 422
    assert response.json()["detail"]["code"] == "CHATFLOW_TRIGGER_NOT_SUPPORTED"


def test_chatflow_modify_rejects_trigger_when_mode_is_inferred_from_graph(monkeypatch) -> None:
    settings = _test_settings()
    plan = fallback_plan(
        "创建汽车售后多轮客服",
        app_name="汽车售后多轮客服",
        app_mode="advanced-chat",
    )
    graph = yaml.safe_load(
        DifyDslCompiler(
            dsl_version="9.9.9",
            default_model_provider=settings.dify_default_model_provider,
            default_model_name=settings.dify_default_model_name,
        ).compile(plan)
    )["workflow"]["graph"]

    class FakeDifyClient:
        def __init__(self, _settings):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *_):
            pass

        def get_app_detail(self, _app_id):
            raise DifyClientError("metadata temporarily unavailable")

        def get_draft_workflow(self, _app_id):
            return DifyDraftWorkflow(
                id="workflow-1",
                graph=graph,
                features={},
                hash="hash-1",
                version="draft",
                environment_variables=[],
                conversation_variables=[],
                raw={},
            )

    monkeypatch.setattr("app.main.load_settings", lambda: settings)
    monkeypatch.setattr("app.main.read_dify_version_info", lambda _: DifyVersionInfo("../dify", "test", "9.9.9"))
    monkeypatch.setattr("app.main.DifyClient", FakeDifyClient)

    with TestClient(app) as client:
        response = client.post(
            "/api/workflows/modify/draft",
            json={
                "app_id": "chat-app-1",
                "message": "把回复改得更温暖",
                "trigger_selection": {"type": "schedule"},
            },
        )

    assert response.status_code == 422
    assert response.json()["detail"]["code"] == "CHATFLOW_TRIGGER_NOT_SUPPORTED"


def test_chatflow_apply_reviewed_preview_preserves_draft_metadata(monkeypatch) -> None:
    settings = _test_settings()
    current_plan = fallback_plan(
        "创建汽车售后多轮客服",
        app_name="汽车售后多轮客服",
        app_mode="advanced-chat",
    )
    current_payload = current_plan.model_dump()
    current_payload["conversation_variables"] = [
        {
            "id": "94d72d3d-b957-45cd-aea7-6cf9d7ec3020",
            "name": "topic",
            "value_type": "string",
            "value": "",
            "description": "当前话题",
            "selector": ["conversation", "topic"],
        }
    ]
    current_plan = WorkflowPlan.model_validate(current_payload)
    preview_plan = current_plan.model_copy(deep=True)
    preview_plan.nodes[1].params["system_prompt"] = "你是温暖、简洁的汽车售后客服。"
    preview_plan.conversation_variables.append(
        preview_plan.conversation_variables[0].model_copy(
            update={
                "id": "ce952b9f-6458-5c21-8500-f87d8df29459",
                "name": "turn_count",
                "value_type": "number",
                "value": 0,
                "description": "对话轮数",
                "selector": ["conversation", "turn_count"],
            }
        )
    )
    graph = yaml.safe_load(
        DifyDslCompiler(
            dsl_version="9.9.9",
            default_model_provider="openai",
            default_model_name="gpt-4o-mini",
        ).compile(current_plan)
    )["workflow"]["graph"]
    seen = {}

    class FakeDifyClient:
        def __init__(self, _settings):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *_):
            pass

        def get_app_detail(self, app_id):
            return DifyAppDetail(
                id=app_id,
                name=current_plan.name,
                mode="advanced-chat",
                description="",
                raw={},
            )

        def get_draft_workflow(self, _app_id):
            return DifyDraftWorkflow(
                id="workflow-1",
                graph=graph,
                features={"file_upload": {"enabled": True}},
                hash="hash-1",
                version="draft",
                environment_variables=[{"name": "store", "value_type": "string", "value": "west"}],
                conversation_variables=[
                    variable.model_dump()
                    for variable in current_plan.conversation_variables
                ],
                raw={},
            )

        def sync_draft_workflow(self, app_id, **kwargs):
            seen.update(kwargs)
            return DifyDraftSyncResult(
                result="success",
                hash="hash-2",
                updated_at="123",
                workflow_url=settings.workflow_url(app_id),
            )

    monkeypatch.setattr("app.main.load_settings", lambda: settings)
    monkeypatch.setattr("app.main.read_dify_version_info", lambda _: DifyVersionInfo("../dify", "test", "9.9.9"))
    monkeypatch.setattr("app.main.DifyClient", FakeDifyClient)

    with TestClient(app) as client:
        response = client.post(
            "/api/workflows/modify/apply",
            json={
                "app_id": "chat-app-1",
                "message": "应用已审核预览",
                "expected_hash": "hash-1",
                "plan": preview_plan.model_dump(),
            },
        )

    assert response.status_code == 200
    data = response.json()
    assert data["app_mode"] == "advanced-chat"
    assert data["new_hash"] == "hash-2"
    assert data["webhooks"] == []
    assert seen["features"] == {"file_upload": {"enabled": True}}
    assert seen["environment_variables"][0]["name"] == "store"
    assert seen["conversation_variables"][0]["name"] == "topic"
    assert seen["conversation_variables"][0]["id"] == "94d72d3d-b957-45cd-aea7-6cf9d7ec3020"
    assert seen["conversation_variables"][1]["name"] == "turn_count"
    assert any(node["data"]["type"] == "answer" for node in seen["graph"]["nodes"])


def test_chatflow_publish_validates_and_skips_workflow_triggers(monkeypatch) -> None:
    settings = _test_settings()
    plan = fallback_plan(
        "创建汽车售后多轮客服",
        app_name="汽车售后多轮客服",
        app_mode="advanced-chat",
    )
    graph = yaml.safe_load(
        DifyDslCompiler(
            dsl_version="9.9.9",
            default_model_provider=settings.dify_default_model_provider,
            default_model_name=settings.dify_default_model_name,
        ).compile(plan)
    )["workflow"]["graph"]
    seen = {}

    class FakeDifyClient:
        def __init__(self, _settings):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *_):
            pass

        def get_draft_workflow(self, _app_id):
            return DifyDraftWorkflow(
                id="workflow-1",
                graph=graph,
                features={},
                hash="hash-1",
                version="draft",
                environment_variables=[],
                conversation_variables=[],
                raw={},
            )

        def get_app_detail(self, app_id):
            return DifyAppDetail(
                id=app_id,
                name=plan.name,
                mode="advanced-chat",
                description="",
                raw={},
            )

        def publish_workflow(self, app_id, *, marked_name=None, marked_comment=None):
            seen["publish"] = (app_id, marked_name, marked_comment)
            return DifyPublishResult(result="success", created_at="2026-06-12T12:00:00")

        def list_workflow_triggers(self, _app_id):
            raise AssertionError("Chatflow publish must not list workflow triggers.")

        def get_webhook_trigger(self, _app_id, _node_id):
            raise AssertionError("Chatflow publish must not load workflow webhooks.")

    monkeypatch.setattr("app.main.load_settings", lambda: settings)
    monkeypatch.setattr("app.main.read_dify_version_info", lambda _: DifyVersionInfo("../dify", "test", "9.9.9"))
    monkeypatch.setattr("app.main.DifyClient", FakeDifyClient)

    with TestClient(app) as client:
        response = client.post(
            "/api/workflows/chat-app-1/publish",
            json={
                "expected_hash": "hash-1",
                "marked_name": "v1.1",
                "marked_comment": "Chatflow release",
            },
        )

    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "published"
    assert data["app_mode"] == "advanced-chat"
    assert data["plan"]["app_mode"] == "advanced-chat"
    assert data["validation"]["ok"] is True
    assert data["triggers"] == []
    assert data["webhooks"] == []
    assert seen["publish"] == ("chat-app-1", "v1.1", "Chatflow release")


def test_old_chatflow_draft_warns_for_unavailable_runtime_model(monkeypatch) -> None:
    settings = _test_settings()
    plan, graph = _chatflow_graph_with_model("legacy/provider", "retired-model")

    class FakeDifyClient:
        def __init__(self, _settings):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *_):
            pass

        def get_app_detail(self, app_id):
            return DifyAppDetail(
                id=app_id,
                name=plan.name,
                mode="advanced-chat",
                description="",
                raw={},
            )

        def get_draft_workflow(self, _app_id):
            return DifyDraftWorkflow(
                id="workflow-1",
                graph=graph,
                features={},
                hash="hash-1",
                version="draft",
                environment_variables=[],
                conversation_variables=[],
                raw={},
            )

        def list_models(self, *, model_type="llm"):
            return _active_default_model_catalog(settings)

    monkeypatch.setattr("app.main.load_settings", lambda: settings)
    monkeypatch.setattr(
        "app.main.read_dify_version_info",
        lambda _: DifyVersionInfo("../dify", "test", "9.9.9"),
    )
    monkeypatch.setattr("app.main.DifyClient", FakeDifyClient)

    with TestClient(app) as client:
        response = client.get("/api/workflows/chat-app-1/draft")

    assert response.status_code == 200
    validation = response.json()["validation"]
    assert validation["ok"] is True
    assert validation["issues"][0]["code"] == "PLAN_MODEL_NOT_FOUND"
    assert validation["issues"][0]["severity"] == "warning"


def test_publish_blocks_chatflow_with_unavailable_runtime_model(monkeypatch) -> None:
    settings = _test_settings()
    plan, graph = _chatflow_graph_with_model("legacy/provider", "retired-model")

    class FakeDifyClient:
        def __init__(self, _settings):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *_):
            pass

        def get_app_detail(self, app_id):
            return DifyAppDetail(
                id=app_id,
                name=plan.name,
                mode="advanced-chat",
                description="",
                raw={},
            )

        def get_draft_workflow(self, _app_id):
            return DifyDraftWorkflow(
                id="workflow-1",
                graph=graph,
                features={},
                hash="hash-1",
                version="draft",
                environment_variables=[],
                conversation_variables=[],
                raw={},
            )

        def list_models(self, *, model_type="llm"):
            return _active_default_model_catalog(settings)

        def publish_workflow(self, *_args, **_kwargs):
            raise AssertionError("Unavailable runtime models must block publish.")

    monkeypatch.setattr("app.main.load_settings", lambda: settings)
    monkeypatch.setattr(
        "app.main.read_dify_version_info",
        lambda _: DifyVersionInfo("../dify", "test", "9.9.9"),
    )
    monkeypatch.setattr("app.main.DifyClient", FakeDifyClient)

    with TestClient(app) as client:
        response = client.post(
            "/api/workflows/chat-app-1/publish",
            json={"expected_hash": "hash-1"},
        )

    assert response.status_code == 422
    detail = response.json()["detail"]
    assert detail["code"] == "WORKFLOW_PUBLISH_VALIDATION_FAILED"
    assert detail["validation"]["issues"][-1]["code"] == "PLAN_MODEL_NOT_FOUND"


def test_publish_and_trigger_management_apis(monkeypatch) -> None:
    settings = _test_settings()
    selected = normalize_plan_payload(
        fallback_plan("处理售后 Webhook", app_name="Webhook 售后").model_dump(),
        trigger_selection={
            "type": "webhook",
            "body": [{"name": "query", "type": "string", "required": True}],
        },
    )
    plan = WorkflowPlan.model_validate(selected.payload)
    graph = yaml.safe_load(
        DifyDslCompiler(
            dsl_version="9.9.9",
            default_model_provider="langgenius/tongyi/tongyi",
            default_model_name="qwen3.5-plus",
        ).compile(plan)
    )["workflow"]["graph"]
    seen = {}

    class FakeDifyClient:
        def __init__(self, _settings):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *_):
            pass

        def get_draft_workflow(self, _app_id):
            return DifyDraftWorkflow(
                id="workflow-1",
                graph=graph,
                features={},
                hash="hash-1",
                version="draft",
                environment_variables=[],
                conversation_variables=[],
                raw={},
            )

        def get_app_detail(self, app_id):
            return DifyAppDetail(
                id=app_id,
                name="Webhook 售后",
                mode="workflow",
                description="",
                raw={},
            )

        def publish_workflow(self, app_id, *, marked_name=None, marked_comment=None):
            seen["publish"] = (app_id, marked_name, marked_comment)
            return DifyPublishResult(result="success", created_at="2026-06-09T09:00:00")

        def list_workflow_triggers(self, _app_id):
            return [
                DifyWorkflowTrigger(
                    id="trigger-1",
                    trigger_type="trigger-webhook",
                    title="接收 Webhook 请求",
                    node_id="start",
                    provider_name="",
                    icon="",
                    status="enabled",
                )
            ]

        def set_workflow_trigger_status(self, app_id, trigger_id, *, enabled):
            seen["status"] = (app_id, trigger_id, enabled)
            return DifyWorkflowTrigger(
                id=trigger_id,
                trigger_type="trigger-webhook",
                title="接收 Webhook 请求",
                node_id="start",
                provider_name="",
                icon="",
                status="enabled" if enabled else "disabled",
            )

        def get_webhook_trigger(self, _app_id, node_id):
            return DifyWebhookTrigger(
                id="webhook-1",
                webhook_id="hook-1",
                webhook_url="http://dify.local/hook-1",
                webhook_debug_url="http://dify.local/debug/hook-1",
                node_id=node_id,
            )

    monkeypatch.setattr("app.main.load_settings", lambda: settings)
    monkeypatch.setattr(
        "app.main.read_dify_version_info",
        lambda _: DifyVersionInfo(source_dir="../dify", git_describe="test", app_dsl_version="9.9.9"),
    )
    monkeypatch.setattr("app.main.DifyClient", FakeDifyClient)

    with TestClient(app) as client:
        publish = client.post(
            "/api/workflows/app-1/publish",
            json={"expected_hash": "hash-1", "marked_name": "v1", "marked_comment": "Webhook release"},
        )
        listed = client.get("/api/workflows/app-1/triggers")
        updated = client.post(
            "/api/workflows/app-1/triggers/trigger-1/status",
            json={"enabled": False},
        )
        webhook = client.get("/api/workflows/app-1/triggers/webhook", params={"node_id": "start"})

    assert publish.status_code == 200
    assert publish.json()["status"] == "published"
    assert publish.json()["webhooks"][0]["webhook_url"] == "http://dify.local/hook-1"
    assert listed.json()["triggers"][0]["status"] == "enabled"
    assert updated.json()["trigger"]["status"] == "disabled"
    assert webhook.json()["webhook_debug_url"] == "http://dify.local/debug/hook-1"
    assert seen["publish"] == ("app-1", "v1", "Webhook release")
    assert seen["status"] == ("app-1", "trigger-1", False)


def test_publish_rejects_stale_draft_hash(monkeypatch) -> None:
    settings = _test_settings()

    class FakeDifyClient:
        def __init__(self, _settings):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *_):
            pass

        def get_draft_workflow(self, _app_id):
            return DifyDraftWorkflow(
                id="workflow-1",
                graph={},
                features={},
                hash="current-hash",
                version="draft",
                environment_variables=[],
                conversation_variables=[],
                raw={},
            )

    monkeypatch.setattr("app.main.load_settings", lambda: settings)
    monkeypatch.setattr("app.main.DifyClient", FakeDifyClient)

    with TestClient(app) as client:
        response = client.post(
            "/api/workflows/app-1/publish",
            json={"expected_hash": "stale-hash"},
        )

    assert response.status_code == 409
    assert response.json()["detail"]["code"] == "DRAFT_HASH_MISMATCH"


def test_background_create_task_returns_202_and_can_be_polled(monkeypatch, tmp_path) -> None:
    settings = _test_settings()
    settings = Settings.from_env(
        {
            "DIFY_SOURCE_DIR": "../dify",
            "OPENAI_API_KEY": "token",
            "CHAT2DIFY_TASK_DB": str(tmp_path / "tasks.sqlite3"),
        },
        validate_dify=False,
    )
    monkeypatch.setattr("app.main.load_settings", lambda: settings)
    monkeypatch.setattr(
        "app.main.read_dify_version_info",
        lambda _: DifyVersionInfo(source_dir="../dify", git_describe="test", app_dsl_version="9.9.9"),
    )

    def fake_create(request, *, task_context=None):
        assert task_context is not None
        assert request.app_mode == "advanced-chat"
        task_context.update("planning", 50, "Halfway")
        return {
            "status": "completed",
            "app_id": "app-background",
            "workflow_url": "http://dify.local/app/app-background/workflow",
            "plan": {"name": request.app_name},
        }

    monkeypatch.setattr("app.main._create_workflow", fake_create)

    with TestClient(app) as client:
        response = client.post(
            "/api/tasks/workflows/create",
            json={
                "app_mode": "advanced-chat",
                "message": "create an advanced Chatflow",
                "app_name": "Background",
            },
        )
        assert response.status_code == 202
        task_id = response.json()["task_id"]
        deadline = time.monotonic() + 2
        while time.monotonic() < deadline:
            task_response = client.get(f"/api/tasks/{task_id}")
            if task_response.json()["status"] == "succeeded":
                break
            time.sleep(0.01)

    assert task_response.status_code == 200
    task = task_response.json()
    assert task["status"] == "succeeded"
    assert task["progress"] == 100
    assert task["result"]["app_id"] == "app-background"


def test_background_publish_task_returns_202_and_can_be_polled(monkeypatch, tmp_path) -> None:
    settings = Settings.from_env(
        {
            "DIFY_SOURCE_DIR": "../dify",
            "OPENAI_API_KEY": "token",
            "CHAT2DIFY_TASK_DB": str(tmp_path / "publish-tasks.sqlite3"),
        },
        validate_dify=False,
    )
    monkeypatch.setattr("app.main.load_settings", lambda: settings)
    monkeypatch.setattr(
        "app.main.read_dify_version_info",
        lambda _: DifyVersionInfo(source_dir="../dify", git_describe="test", app_dsl_version="9.9.9"),
    )

    def fake_publish(app_id, request, *, task_context=None):
        assert task_context is not None
        task_context.update("publishing", 75, "Publishing")
        return {
            "status": "published",
            "app_id": app_id,
            "base_hash": request.expected_hash,
            "triggers": [],
        }

    monkeypatch.setattr("app.main._publish_workflow", fake_publish)

    with TestClient(app) as client:
        response = client.post(
            "/api/tasks/workflows/publish",
            json={"app_id": "app-1", "expected_hash": "hash-1"},
        )
        assert response.status_code == 202
        task_id = response.json()["task_id"]
        deadline = time.monotonic() + 2
        while time.monotonic() < deadline:
            task_response = client.get(f"/api/tasks/{task_id}")
            if task_response.json()["status"] == "succeeded":
                break
            time.sleep(0.01)

    task = task_response.json()
    assert task["status"] == "succeeded"
    assert task["result"]["status"] == "published"
    assert task["result"]["app_id"] == "app-1"


def test_background_chatflow_run_task_returns_202_and_can_be_polled(monkeypatch, tmp_path) -> None:
    settings = Settings.from_env(
        {
            "DIFY_SOURCE_DIR": "../dify",
            "OPENAI_API_KEY": "token",
            "CHAT2DIFY_TASK_DB": str(tmp_path / "chatflow-tasks.sqlite3"),
        },
        validate_dify=False,
    )
    monkeypatch.setattr("app.main.load_settings", lambda: settings)
    monkeypatch.setattr(
        "app.main.read_dify_version_info",
        lambda _: DifyVersionInfo(source_dir="../dify", git_describe="test", app_dsl_version="9.9.9"),
    )

    def fake_run(request, *, task_context=None):
        assert task_context is not None
        task_context.update("running-chatflow", None, "Received 2 answer chunks.")
        return {
            "ok": True,
            "status": "succeeded",
            "app_id": request.app_id,
            "answer": "请提供维修单号。",
            "conversation_id": "conversation-1",
            "message_id": "message-1",
        }

    monkeypatch.setattr("app.main._run_draft_chatflow", fake_run)

    with TestClient(app) as client:
        response = client.post(
            "/api/tasks/chatflows/run/draft",
            json={"app_id": "chat-app-1", "query": "发动机抖动"},
        )
        assert response.status_code == 202
        task_id = response.json()["task_id"]
        deadline = time.monotonic() + 2
        while time.monotonic() < deadline:
            task_response = client.get(f"/api/tasks/{task_id}")
            if task_response.json()["status"] == "succeeded":
                break
            time.sleep(0.01)

    task = task_response.json()
    assert task["operation"] == "chatflow.run.draft"
    assert task["result"]["conversation_id"] == "conversation-1"
    assert task["result"]["answer"] == "请提供维修单号。"


def test_background_chatbot_run_task_returns_202_and_can_be_polled(monkeypatch, tmp_path) -> None:
    settings = Settings.from_env(
        {
            "DIFY_SOURCE_DIR": "../dify",
            "OPENAI_API_KEY": "token",
            "CHAT2DIFY_TASK_DB": str(tmp_path / "chatbot-tasks.sqlite3"),
        },
        validate_dify=False,
    )
    monkeypatch.setattr("app.main.load_settings", lambda: settings)
    monkeypatch.setattr(
        "app.main.read_dify_version_info",
        lambda _: DifyVersionInfo(source_dir="../dify", git_describe="test", app_dsl_version="9.9.9"),
    )

    def fake_run(request, *, task_context=None):
        assert task_context is not None
        task_context.update("running-chatbot", None, "Received 2 answer chunks.")
        return {
            "ok": True,
            "status": "succeeded",
            "app_id": request.app_id,
            "app_mode": "chat",
            "answer": "请提供维修单号。",
            "conversation_id": "conversation-1",
            "message_id": "message-1",
        }

    monkeypatch.setattr("app.main._run_draft_chatbot", fake_run)

    with TestClient(app) as client:
        response = client.post(
            "/api/tasks/chatbots/run/draft",
            json={"app_id": "chat-app-1", "query": "发动机抖动"},
        )
        assert response.status_code == 202
        task_id = response.json()["task_id"]
        deadline = time.monotonic() + 2
        while time.monotonic() < deadline:
            task_response = client.get(f"/api/tasks/{task_id}")
            if task_response.json()["status"] == "succeeded":
                break
            time.sleep(0.01)

    task = task_response.json()
    assert task["operation"] == "chatbot.run.draft"
    assert task["result"]["app_mode"] == "chat"
    assert task["result"]["conversation_id"] == "conversation-1"


class _KnowledgePlanner:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings

    def generate(self, message: str, *, app_name: str | None = None, dsl_version: str, **_kwargs) -> PlannerResult:
        plan = _knowledge_plan(app_name or "知识库问答", self.settings.dify_default_dataset_ids)
        return PlannerResult(
            plan=plan,
            raw_plan=plan.model_dump(),
            mode="llm",
            attempts=1,
            used_fallback=False,
            repaired=False,
        )


def _knowledge_plan(name: str, dataset_ids: list[str]) -> object:
    from app.models import WorkflowPlan

    return WorkflowPlan.model_validate(
        {
            "name": name,
            "nodes": [
                {"id": "start", "type": "start", "title": "接收售后问题", "params": {"variables": [{"name": "query"}]}},
                {
                    "id": "knowledge",
                    "type": "knowledge-retrieval",
                    "title": "检索售后知识库",
                    "params": {
                        "query_variable_selector": ["start", "query"],
                        "dataset_ids": dataset_ids,
                        "retrieval_mode": "multiple",
                    },
                },
                {
                    "id": "llm",
                    "type": "llm",
                    "title": "生成知识库回复",
                    "params": {"user_prompt": "资料：{{#knowledge.result#}}\n问题：{{#start.query#}}"},
                },
                {
                    "id": "end",
                    "type": "end",
                    "title": "返回回复",
                    "params": {"outputs": [{"variable": "answer", "value_selector": ["llm", "text"]}]},
                },
            ],
            "edges": [
                {"source": "start", "target": "knowledge"},
                {"source": "knowledge", "target": "llm"},
                {"source": "llm", "target": "end"},
            ],
        }
    )


def _test_settings(
    dataset_ids: str = "",
    *,
    nvidia_api_key: str = "",
    openrouter_api_key: str = "",
) -> Settings:
    env = {
        "DIFY_SOURCE_DIR": "../dify",
        "OPENAI_API_KEY": "token",
        "PLANNER_DEFAULT_PROVIDER": "openai",
        "DIFY_CONSOLE_WEB_BASE": "http://dify.local",
        "DIFY_DEFAULT_MODEL_PROVIDER": "langgenius/tongyi/tongyi",
        "DIFY_DEFAULT_MODEL_NAME": "qwen3.5-plus",
        "DIFY_DEFAULT_DATASET_IDS": dataset_ids,
    }
    if nvidia_api_key:
        env["NVIDIA_API_KEY"] = nvidia_api_key
    if openrouter_api_key:
        env["OPENROUTER_API_KEY"] = openrouter_api_key
    return Settings.from_env(env, validate_dify=False)


def _patch_runtime_model_context(monkeypatch, settings: Settings) -> None:
    item = DifyModelListItem(
        provider=settings.dify_default_model_provider,
        provider_label=settings.dify_default_model_provider,
        model=settings.dify_default_model_name,
        model_label=settings.dify_default_model_name,
        model_type="llm",
        status="active",
        provider_status="active",
        deprecated=False,
        features=["vision", "tool-call"],
        mode="chat",
    )
    catalog = DifyModelListResult(
        data=[item],
        count=1,
        model_type="llm",
        providers=[item.provider],
        features=list(item.features),
    )
    monkeypatch.setattr(
        "app.main._runtime_model_context",
        lambda _settings, _selections, client=None: (catalog, [item]),
    )


def _active_default_model_catalog(settings: Settings) -> DifyModelListResult:
    item = DifyModelListItem(
        provider=settings.dify_default_model_provider,
        provider_label=settings.dify_default_model_provider,
        model=settings.dify_default_model_name,
        model_label=settings.dify_default_model_name,
        model_type="llm",
        status="active",
        provider_status="active",
        deprecated=False,
        features=["vision", "tool-call"],
        mode="chat",
    )
    return DifyModelListResult(
        data=[item],
        count=1,
        model_type="llm",
        providers=[item.provider],
        features=list(item.features),
    )


def _chatflow_graph_with_model(provider: str, model: str):
    plan = fallback_plan(
        "创建模型认证客服",
        app_name="模型认证客服",
        app_mode="advanced-chat",
    )
    graph = yaml.safe_load(
        DifyDslCompiler(
            dsl_version="9.9.9",
            default_model_provider=provider,
            default_model_name=model,
        ).compile(plan)
    )["workflow"]["graph"]
    return plan, graph
