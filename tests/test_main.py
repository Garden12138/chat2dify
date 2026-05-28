from fastapi.testclient import TestClient
import yaml

from app.agent.editor import WorkflowEditResult
from app.agent.planner import fallback_plan
from app.compiler.dify import DifyDslCompiler
from app.config import Settings
from app.dify.client import DifyDraftSyncResult, DifyDraftWorkflow
from app.dify.version import DifyVersionInfo
from app.main import app


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
    assert data["sync"]["result"] == "success"
    assert seen["sync_hash"] == "hash-1"
    assert seen["sync_graph"]["nodes"]


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


def _test_settings() -> Settings:
    return Settings.from_env(
        {
            "DIFY_SOURCE_DIR": "../dify",
            "OPENAI_API_KEY": "token",
            "DIFY_CONSOLE_WEB_BASE": "http://dify.local",
            "DIFY_DEFAULT_MODEL_PROVIDER": "openai",
            "DIFY_DEFAULT_MODEL_NAME": "gpt-4o-mini",
        },
        validate_dify=False,
    )
