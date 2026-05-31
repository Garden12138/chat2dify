from fastapi.testclient import TestClient
import yaml

from app.agent.editor import WorkflowEditResult
from app.agent.planner import PlannerResult, fallback_plan
from app.compiler.dify import DifyDslCompiler
from app.config import Settings
from app.dify.client import (
    DifyAppDetail,
    DifyClientError,
    DifyDatasetListItem,
    DifyDatasetListResult,
    DifyDraftRunResult,
    DifyDraftSyncResult,
    DifyDraftWorkflow,
)
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
    assert 'id="knowledge-search"' in index.text
    assert 'id="refresh-datasets"' in index.text
    assert 'id="knowledge-dataset-list"' in index.text
    assert 'id="knowledge-dataset-ids"' in index.text
    assert 'id="result-tabs"' in index.text
    assert 'id="load-draft"' in index.text
    assert script.status_code == 200
    assert "handleCreate" in script.text
    assert "handleLoadDraft" in script.text
    assert "handleReviewedPreviewApply" in script.text
    assert "modifyPreview" in script.text
    assert "DATASET_IDS_KEY" in script.text
    assert "SELECTED_DATASET_IDS_KEY" in script.text
    assert "loadDatasets" in script.text
    assert "currentDatasetIds" in script.text
    assert "Applied reviewed preview" in script.text
    assert "localStorage" in script.text
    assert "renderValidationPanel" in script.text
    assert "planNodeOverview" in script.text
    assert styles.status_code == 200
    assert ".workspace" in styles.text
    assert ".history-item" in styles.text
    assert ".tab-button" in styles.text
    assert ".node-card" in styles.text


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
    assert data["configured_dataset_count"] == 2
    assert data["dify"]["configured_dataset_count"] == 2


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
    }


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


def test_draft_request_dataset_ids_override_env_defaults(monkeypatch) -> None:
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

        def generate(self, _message, *, current_plan, dsl_version):
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


class _KnowledgePlanner:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings

    def generate(self, message: str, *, app_name: str | None = None, dsl_version: str) -> PlannerResult:
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


def _test_settings(dataset_ids: str = "") -> Settings:
    return Settings.from_env(
        {
            "DIFY_SOURCE_DIR": "../dify",
            "OPENAI_API_KEY": "token",
            "DIFY_CONSOLE_WEB_BASE": "http://dify.local",
            "DIFY_DEFAULT_MODEL_PROVIDER": "langgenius/tongyi/tongyi",
            "DIFY_DEFAULT_MODEL_NAME": "qwen3.5-plus",
            "DIFY_DEFAULT_DATASET_IDS": dataset_ids,
        },
        validate_dify=False,
    )
