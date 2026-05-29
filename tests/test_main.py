from fastapi.testclient import TestClient
import yaml

from app.agent.editor import WorkflowEditResult
from app.agent.planner import fallback_plan
from app.compiler.dify import DifyDslCompiler
from app.config import Settings
from app.dify.client import DifyDraftRunResult, DifyDraftSyncResult, DifyDraftWorkflow
from app.dify.version import DifyVersionInfo
from app.main import app


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

    assert index.status_code == 200
    assert "chat2dify" in index.text
    assert 'id="create-form"' in index.text
    assert 'id="history-list"' in index.text
    assert 'id="result-tabs"' in index.text
    assert script.status_code == 200
    assert "handleCreate" in script.text
    assert "localStorage" in script.text
    assert "renderValidationPanel" in script.text
    assert styles.status_code == 200
    assert ".workspace" in styles.text
    assert ".history-item" in styles.text
    assert ".tab-button" in styles.text


def test_draft_response_includes_phase2_fields(monkeypatch) -> None:
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

        def generate(self, message, *, current_plan, dsl_version):
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

        def generate(self, _message, *, current_plan, dsl_version):
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

        def generate(self, _message, *, current_plan, dsl_version):
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

        def generate(self, _message, *, current_plan, dsl_version):
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


def _test_settings() -> Settings:
    return Settings.from_env(
        {
            "DIFY_SOURCE_DIR": "../dify",
            "OPENAI_API_KEY": "token",
            "DIFY_CONSOLE_WEB_BASE": "http://dify.local",
            "DIFY_DEFAULT_MODEL_PROVIDER": "langgenius/tongyi/tongyi",
            "DIFY_DEFAULT_MODEL_NAME": "qwen3.5-plus",
        },
        validate_dify=False,
    )
