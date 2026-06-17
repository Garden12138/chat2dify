from __future__ import annotations

from contextlib import asynccontextmanager
from copy import deepcopy
from dataclasses import asdict, replace
from pathlib import Path
import re

from fastapi import FastAPI, HTTPException, Query, Request, status
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from app.agent.diff import diff_plans
from app.agent.editor import WorkflowEditPlanner
from app.agent.explainer import explain_plan
from app.agent.guard import guard_plan_change
from app.agent.normalizer import normalize_plan_payload
from app.agent.planner import PlannerError, WorkflowPlanner
from app.compiler.agent import (
    agent_app_plan_payload,
    chat_app_plan_payload,
    compile_agent_app_dsl,
    compile_chat_app_dsl,
    validate_agent_app_dsl,
    validate_chat_app_dsl,
)
from app.compiler.dify import DifyDslCompiler
from app.config import ConfigurationError, Settings, load_settings
from app.dify.client import DifyAppDetail, DifyClient, DifyClientError, DifyConflictError
from app.dify.graph import (
    DifyGraphAdapterError,
    UnsupportedExistingNodeType,
    compile_plan_to_dify_graph,
    decompile_dify_graph,
)
from app.dify.knowledge_retrieval import apply_dataset_retrieval_settings, knowledge_dataset_ids
from app.dify.preflight import preflight_plan
from app.dify.runtime_models import (
    RuntimeModelSelectionError,
    apply_default_runtime_models,
    load_runtime_model_catalog,
    model_selection_payloads,
    resolve_runtime_model_selections,
    validate_agent_selection_models,
    validate_runtime_model_bindings,
)
from app.dify.version import read_dify_version_info
from app.models import (
    AgentRunDraftRequest,
    ChatbotRunDraftRequest,
    ChatflowRunDraftRequest,
    WorkflowModifyRequest,
    WorkflowPlan,
    WorkflowPublishRequest,
    WorkflowPublishTaskRequest,
    WorkflowRequest,
    WorkflowRunDraftRequest,
    WorkflowTriggerStatusRequest,
)
from app.tasks import TaskContext, TaskManager, TaskNotFound, TaskRepository
from app.validator import has_errors, validate_dsl, validate_plan


STATIC_DIR = Path(__file__).resolve().parent / "static"


@asynccontextmanager
async def lifespan(application: FastAPI):
    settings = load_settings()
    read_dify_version_info(settings.dify_source_path)
    task_manager = TaskManager(TaskRepository(settings.task_db_path), workers=settings.task_workers)
    application.state.task_manager = task_manager
    try:
        yield
    finally:
        task_manager.close()


app = FastAPI(title="chat2dify", version="1.1.0", lifespan=lifespan)
app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")


@app.get("/", include_in_schema=False)
def index() -> FileResponse:
    return FileResponse(STATIC_DIR / "index.html")


@app.get("/health")
def health() -> dict:
    try:
        settings = load_settings()
        version_info = read_dify_version_info(settings.dify_source_path)
    except (ConfigurationError, ValueError) as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc
    planner_runtime = settings.planner_runtime()
    return {
        "status": "ok",
        "configured_dataset_count": len(settings.dify_default_dataset_ids),
        "default_model": {
            "provider": settings.dify_default_model_provider,
            "name": settings.dify_default_model_name,
        },
        "planner": {
            "provider": planner_runtime.provider,
            "model": planner_runtime.model,
            "configured": planner_runtime.configured,
        },
        "dify": {
            "source_dir": settings.dify_source_dir,
            "resolved_source_dir": str(settings.dify_source_path),
            "git_describe": version_info.git_describe,
            "app_dsl_version": version_info.app_dsl_version,
            "configured_dataset_count": len(settings.dify_default_dataset_ids),
            "default_model": {
                "provider": settings.dify_default_model_provider,
                "name": settings.dify_default_model_name,
            },
        },
    }


@app.get("/api/planner/providers")
def list_planner_providers() -> dict:
    settings = load_settings()
    try:
        runtime = settings.planner_runtime()
    except ConfigurationError as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc
    return {
        "default_provider": runtime.provider,
        "default_model": runtime.model,
        "providers": settings.planner_catalog(),
    }


@app.get("/api/dify/datasets")
def list_dify_datasets(
    keyword: str | None = Query(default=None),
    page: int = Query(default=1, ge=1),
    limit: int = Query(default=50, ge=1, le=100),
    include_all: bool = Query(default=True),
) -> dict:
    settings = load_settings()
    try:
        with DifyClient(settings) as client:
            result = client.list_datasets(
                keyword=keyword.strip() if keyword else None,
                page=page,
                limit=limit,
                include_all=include_all,
            )
    except DifyClientError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    return asdict(result)


@app.get("/api/dify/models")
def list_dify_models(
    model_type: str = Query(default="llm", pattern="^llm$"),
    keyword: str | None = Query(default=None),
    feature: list[str] | None = Query(default=None),
) -> dict:
    settings = load_settings()
    try:
        with DifyClient(settings) as client:
            result = client.list_models(
                model_type=model_type,
                keyword=keyword.strip() if keyword else None,
                features=feature,
            )
    except DifyClientError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    return asdict(result)


@app.get("/api/dify/tools")
def list_dify_tools(
    keyword: str | None = Query(default=None),
    provider_type: str = Query(default="all", pattern="^(all|builtin|api|workflow|mcp)$"),
) -> dict:
    settings = load_settings()
    try:
        with DifyClient(settings) as client:
            result = client.list_tools(
                keyword=keyword.strip() if keyword else None,
                provider_type=provider_type,
            )
    except DifyClientError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    return asdict(result)


@app.get("/api/dify/agent-strategies")
def list_dify_agent_strategies(keyword: str | None = Query(default=None)) -> dict:
    settings = load_settings()
    try:
        with DifyClient(settings) as client:
            result = client.list_agent_strategies(keyword=keyword.strip() if keyword else None)
    except DifyClientError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    return asdict(result)


@app.get("/api/dify/trigger-providers")
def list_dify_trigger_providers(keyword: str | None = Query(default=None)) -> dict:
    settings = load_settings()
    try:
        with DifyClient(settings) as client:
            result = client.list_trigger_providers(keyword=keyword.strip() if keyword else None)
    except DifyClientError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    return asdict(result)


@app.get("/api/dify/trigger-subscriptions")
def list_dify_trigger_subscriptions(provider_id: str = Query(min_length=1)) -> dict:
    settings = load_settings()
    try:
        with DifyClient(settings) as client:
            result = client.list_trigger_subscriptions(provider_id.strip())
    except DifyClientError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    return asdict(result)


@app.post("/api/workflows/draft")
def draft_workflow(request: WorkflowRequest) -> dict:
    return _draft_workflow(request)


def _draft_workflow(request: WorkflowRequest, *, task_context: TaskContext | None = None) -> dict:
    if task_context is not None:
        task_context.update("loading-config", 5, "Loading Dify and planner configuration.")
    settings = load_settings()
    if request.app_mode == "chat":
        return _draft_chat_app(request, settings=settings, task_context=task_context)
    if request.app_mode == "agent-chat":
        return _draft_agent_app(request, settings=settings, task_context=task_context)
    effective_settings = _settings_with_request_dataset_ids(settings, request.dataset_ids)
    effective_settings = _settings_with_request_planner(effective_settings, request.planner)
    version_info = read_dify_version_info(settings.dify_source_path)
    _ensure_agent_strategy_selection_for_request(request.message, request.agent_selections)
    _ensure_agent_selections_configured(request.agent_selections)
    if request.app_mode == "advanced-chat" and request.trigger_selection and request.trigger_selection.type != "user-input":
        raise HTTPException(
            status_code=422,
            detail={
                "code": "CHATFLOW_TRIGGER_NOT_SUPPORTED",
                "message": "Chatflow uses a conversational start and cannot use workflow triggers.",
            },
        )
    try:
        model_catalog, runtime_models = _runtime_model_context(
            settings,
            request.model_selections,
        )
        effective_settings = _settings_with_primary_runtime_model(
            effective_settings,
            runtime_models,
        )
        _ensure_agent_runtime_models(
            request.agent_selections,
            model_catalog,
            runtime_models,
        )
        trigger_selection = _hydrate_trigger_selection(settings, request.trigger_selection)
        planner_kwargs = _planner_selection_kwargs(request, trigger_selection=trigger_selection)
        planner_kwargs["model_selections"] = runtime_models
        planner_kwargs["model_catalog"] = model_catalog
        if request.app_mode == "advanced-chat":
            planner_kwargs["app_mode"] = request.app_mode
        if task_context is not None:
            planner_kwargs["task_context"] = task_context
        planner_result = WorkflowPlanner(effective_settings).generate(
            request.message,
            app_name=request.app_name,
            dsl_version=version_info.app_dsl_version,
            **planner_kwargs,
        )
    except RuntimeModelSelectionError as exc:
        raise HTTPException(status_code=422, detail=exc.detail) from exc
    except DifyClientError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    except PlannerError as exc:
        raise HTTPException(
            status_code=502,
            detail=exc.detail if exc.detail is not None else str(exc),
        ) from exc
    if task_context is not None:
        task_context.update("compiling", 70, "Compiling the validated plan into Dify DSL.")
    plan = WorkflowPlan.model_validate(
        apply_default_runtime_models(
            planner_result.plan.model_dump(),
            model_selection_payloads(runtime_models),
        )
    )
    plan = _plan_with_dataset_retrieval_settings(plan, effective_settings)
    compiler = DifyDslCompiler(
        dsl_version=version_info.app_dsl_version,
        default_model_provider=effective_settings.dify_default_model_provider,
        default_model_name=effective_settings.dify_default_model_name,
        default_dataset_ids=effective_settings.dify_default_dataset_ids,
    )
    preflight = preflight_plan(
        plan,
        compiler=compiler,
        expected_dsl_version=version_info.app_dsl_version,
    )
    dsl = preflight.dsl
    issues = [
        *preflight.issues,
        *validate_runtime_model_bindings(
            plan,
            model_catalog,
            allowed_models=runtime_models,
        ),
    ]
    planner_metadata = planner_result.metadata()
    planner_metadata["preflight"] = preflight.metadata()
    return {
        "raw_plan": planner_result.raw_plan,
        "plan": plan.model_dump(),
        "explanation": explain_plan(plan),
        "planner": planner_metadata,
        "dsl": dsl,
        "validation": {
            "ok": not has_errors(issues),
            "issues": [issue.model_dump() for issue in issues],
        },
        "dify": asdict(version_info),
    }


def _draft_agent_app(
    request: WorkflowRequest,
    *,
    settings: Settings,
    task_context: TaskContext | None = None,
) -> dict:
    if task_context is not None:
        task_context.update("loading-models", 20, "Loading Dify model configuration for Agent app.")
    try:
        model_catalog, runtime_models = _runtime_model_context(
            settings,
            request.model_selections,
        )
    except RuntimeModelSelectionError as exc:
        raise HTTPException(status_code=422, detail=exc.detail) from exc
    except DifyClientError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc

    effective_settings = _settings_with_primary_runtime_model(settings, runtime_models)
    version_info = read_dify_version_info(settings.dify_source_path)
    tool_payloads = _tool_selection_payloads(request.tool_selections)
    if task_context is not None:
        task_context.update("compiling", 70, "Compiling Agent app configuration into Dify DSL.")
    dsl = compile_agent_app_dsl(
        message=request.message,
        app_name=request.app_name,
        dsl_version=version_info.app_dsl_version,
        settings=effective_settings,
        model_selections=runtime_models,
        tool_selections=tool_payloads,
    )
    issues = validate_agent_app_dsl(dsl, expected_dsl_version=version_info.app_dsl_version)
    plan = agent_app_plan_payload(
        message=request.message,
        app_name=request.app_name,
        settings=effective_settings,
        model_selections=runtime_models,
        tool_selections=tool_payloads,
    )
    planner_runtime = effective_settings.planner_runtime()
    return {
        "raw_plan": plan,
        "plan": plan,
        "explanation": {
            "summary": "Creates a Dify Agent app using the basic agent-chat app type.",
            "nodes": [],
            "mode": "agent-chat",
        },
        "planner": {
            "mode": "agent-template",
            "attempts": 0,
            "used_fallback": True,
            "repaired": False,
            "provider": planner_runtime.provider,
            "model": planner_runtime.model,
            "normalizations": [],
            "errors": [],
            "repair_actions": [],
            "attempt_diagnostics": [],
            "preflight": {},
            "model_catalog": {"count": model_catalog.count},
        },
        "dsl": dsl,
        "validation": {
            "ok": not issues,
            "issues": issues,
        },
        "dify": asdict(version_info),
    }


def _draft_chat_app(
    request: WorkflowRequest,
    *,
    settings: Settings,
    task_context: TaskContext | None = None,
) -> dict:
    if task_context is not None:
        task_context.update("loading-models", 20, "Loading Dify model configuration for Chatbot app.")
    try:
        model_catalog, runtime_models = _runtime_model_context(
            settings,
            request.model_selections,
        )
    except RuntimeModelSelectionError as exc:
        raise HTTPException(status_code=422, detail=exc.detail) from exc
    except DifyClientError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc

    effective_settings = _settings_with_primary_runtime_model(settings, runtime_models)
    version_info = read_dify_version_info(settings.dify_source_path)
    if task_context is not None:
        task_context.update("compiling", 70, "Compiling Chatbot app configuration into Dify DSL.")
    dsl = compile_chat_app_dsl(
        message=request.message,
        app_name=request.app_name,
        dsl_version=version_info.app_dsl_version,
        settings=effective_settings,
        model_selections=runtime_models,
    )
    issues = validate_chat_app_dsl(dsl, expected_dsl_version=version_info.app_dsl_version)
    plan = chat_app_plan_payload(
        message=request.message,
        app_name=request.app_name,
        settings=effective_settings,
        model_selections=runtime_models,
    )
    planner_runtime = effective_settings.planner_runtime()
    return {
        "raw_plan": plan,
        "plan": plan,
        "explanation": {
            "summary": "Creates a Dify Chatbot app using the basic chat app type.",
            "nodes": [],
            "mode": "chat",
        },
        "planner": {
            "mode": "chat-template",
            "attempts": 0,
            "used_fallback": True,
            "repaired": False,
            "provider": planner_runtime.provider,
            "model": planner_runtime.model,
            "normalizations": [],
            "errors": [],
            "repair_actions": [],
            "attempt_diagnostics": [],
            "preflight": {},
            "model_catalog": {"count": model_catalog.count},
        },
        "dsl": dsl,
        "validation": {
            "ok": not issues,
            "issues": issues,
        },
        "dify": asdict(version_info),
    }


@app.post("/api/workflows/create")
def create_workflow(request: WorkflowRequest) -> dict:
    return _create_workflow(request)


def _create_workflow(request: WorkflowRequest, *, task_context: TaskContext | None = None) -> dict:
    draft = _draft_workflow(request, task_context=task_context)
    if not draft["validation"]["ok"]:
        raise HTTPException(status_code=422, detail=draft["validation"]["issues"])

    settings = load_settings()
    if task_context is not None:
        task_context.update("importing", 85, "Importing the workflow into Dify.")
    try:
        with DifyClient(settings) as client:
            result = client.import_yaml(draft["dsl"], name=request.app_name or draft["plan"]["name"])
            imported_draft = None
            if result.app_id and request.app_mode not in {"chat", "agent-chat"}:
                try:
                    imported_draft = client.get_draft_workflow(result.app_id)
                except (AttributeError, DifyClientError):
                    imported_draft = None
            webhooks = (
                _webhook_details(client, result.app_id, WorkflowPlan.model_validate(draft["plan"]))
                if result.app_id and request.app_mode == "workflow"
                else []
            )
    except DifyClientError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc

    return {
        "status": result.status,
        "app_id": result.app_id,
        "app_mode": result.app_mode,
        "workflow_url": result.workflow_url,
        "base_hash": imported_draft.hash if imported_draft else None,
        "webhooks": webhooks,
        "import": asdict(result),
        "raw_plan": draft["raw_plan"],
        "plan": draft["plan"],
        "explanation": draft["explanation"],
        "planner": draft["planner"],
        "validation": draft["validation"],
        "dsl": draft["dsl"],
    }


@app.post("/api/workflows/{app_id}/publish")
def publish_workflow(app_id: str, request: WorkflowPublishRequest) -> dict:
    return _publish_workflow(app_id, request)


def _publish_workflow(
    app_id: str,
    request: WorkflowPublishRequest,
    *,
    task_context: TaskContext | None = None,
) -> dict:
    settings = load_settings()
    version_info = read_dify_version_info(settings.dify_source_path)
    if task_context is not None:
        task_context.update("loading-draft", 15, "Loading and validating the current Dify draft.")
    try:
        with DifyClient(settings) as client:
            app_detail = _load_app_detail(client, app_id)
            app_mode = _app_mode(app_detail)
            if app_mode in {"chat", "agent-chat"}:
                raise HTTPException(
                    status_code=422,
                    detail={
                        "code": "CONFIGURED_APP_PUBLISH_NOT_SUPPORTED",
                        "message": f"{app_mode} apps are configured apps and do not publish workflow drafts in v1.",
                    },
                )
            draft = client.get_draft_workflow(app_id)
            if request.expected_hash and request.expected_hash != draft.hash:
                raise HTTPException(
                    status_code=409,
                    detail={
                        "code": "DRAFT_HASH_MISMATCH",
                        "message": "Expected hash does not match the current Dify draft hash.",
                        "expected_hash": request.expected_hash,
                        "current_hash": draft.hash,
                    },
                )
            app_mode = _app_mode(app_detail, draft.graph)
            plan = decompile_dify_graph(
                draft.graph,
                name=_draft_plan_name(app_detail, app_id),
                app_mode=app_mode,
                conversation_variables=draft.conversation_variables,
            )
            model_catalog = load_runtime_model_catalog(client, settings)
            compiler = DifyDslCompiler(
                dsl_version=version_info.app_dsl_version,
                default_model_provider=settings.dify_default_model_provider,
                default_model_name=settings.dify_default_model_name,
                default_dataset_ids=settings.dify_default_dataset_ids,
            )
            dsl = compiler.compile(plan)
            issues = [
                *validate_plan(plan),
                *validate_dsl(dsl, expected_dsl_version=version_info.app_dsl_version),
                *validate_runtime_model_bindings(plan, model_catalog),
            ]
            if has_errors(issues):
                raise HTTPException(
                    status_code=422,
                    detail={
                        "code": "WORKFLOW_PUBLISH_VALIDATION_FAILED",
                        "message": "Workflow validation failed before publish.",
                        "validation": {
                            "ok": False,
                            "issues": [issue.model_dump() for issue in issues],
                        },
                    },
                )
            if task_context is not None:
                task_context.update("publishing", 75, "Publishing the validated workflow in Dify.")
                task_context.raise_if_cancelled()
            published = client.publish_workflow(
                app_id,
                marked_name=request.marked_name,
                marked_comment=request.marked_comment,
            )
            triggers = client.list_workflow_triggers(app_id) if app_mode == "workflow" else []
            webhooks = _webhook_details(client, app_id, plan) if app_mode == "workflow" else []
            return {
                "status": "published",
                "app_id": app_id,
                "app_mode": app_mode,
                "workflow_url": settings.workflow_url(app_id),
                "base_hash": draft.hash,
                "publish": asdict(published),
                "triggers": [asdict(trigger) for trigger in triggers],
                "webhooks": webhooks,
                "plan": plan.model_dump(),
                "validation": {
                    "ok": True,
                    "issues": [issue.model_dump() for issue in issues],
                },
            }
    except HTTPException:
        raise
    except DifyGraphAdapterError as exc:
        raise HTTPException(status_code=422, detail={"code": "DIFY_GRAPH_UNSUPPORTED", "message": str(exc)}) from exc
    except DifyClientError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc


@app.get("/api/workflows/{app_id}/triggers")
def list_workflow_triggers(app_id: str) -> dict:
    settings = load_settings()
    try:
        with DifyClient(settings) as client:
            triggers = client.list_workflow_triggers(app_id)
    except DifyClientError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    return {
        "app_id": app_id,
        "workflow_url": settings.workflow_url(app_id),
        "triggers": [asdict(trigger) for trigger in triggers],
    }


@app.post("/api/workflows/{app_id}/triggers/{trigger_id}/status")
def update_workflow_trigger_status(
    app_id: str,
    trigger_id: str,
    request: WorkflowTriggerStatusRequest,
) -> dict:
    settings = load_settings()
    try:
        with DifyClient(settings) as client:
            trigger = client.set_workflow_trigger_status(
                app_id,
                trigger_id,
                enabled=request.enabled,
            )
    except DifyClientError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    return {
        "app_id": app_id,
        "workflow_url": settings.workflow_url(app_id),
        "trigger": asdict(trigger),
    }


@app.get("/api/workflows/{app_id}/triggers/webhook")
def get_workflow_webhook(app_id: str, node_id: str = Query(min_length=1)) -> dict:
    settings = load_settings()
    try:
        with DifyClient(settings) as client:
            webhook = client.get_webhook_trigger(app_id, node_id)
    except DifyClientError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    return {
        "app_id": app_id,
        "workflow_url": settings.workflow_url(app_id),
        **asdict(webhook),
    }


@app.get("/api/workflows/{app_id}/draft")
def get_workflow_draft(app_id: str) -> dict:
    settings = load_settings()
    version_info = read_dify_version_info(settings.dify_source_path)
    try:
        with DifyClient(settings) as client:
            app_detail = _load_app_detail(client, app_id)
            if _app_mode(app_detail) in {"chat", "agent-chat"}:
                raise HTTPException(
                    status_code=422,
                    detail={
                        "code": "CONFIGURED_APP_DRAFT_NOT_SUPPORTED",
                        "message": f"{_app_mode(app_detail)} apps do not have a workflow draft graph in v1.",
                    },
                )
            draft = client.get_draft_workflow(app_id)
            model_catalog = load_runtime_model_catalog(client, settings)

        plan = decompile_dify_graph(
            draft.graph,
            name=_draft_plan_name(app_detail, app_id),
            app_mode=_app_mode(app_detail, draft.graph),
            conversation_variables=draft.conversation_variables,
        )
        issues = [
            *validate_plan(plan),
            *validate_runtime_model_bindings(
                plan,
                model_catalog,
                existing_as_warning=True,
            ),
        ]
        return {
            "app_id": app_id,
            "workflow_url": settings.workflow_url(app_id),
            "base_hash": draft.hash,
            "app": _app_payload(app_detail),
            "plan": plan.model_dump(),
            "webhooks": _load_webhook_details(settings, app_id, plan),
            "explanation": explain_plan(plan),
            "validation": {
                "ok": not has_errors(issues),
                "issues": [issue.model_dump() for issue in issues],
            },
            "dify": asdict(version_info),
        }
    except UnsupportedExistingNodeType as exc:
        raise HTTPException(
            status_code=422,
            detail={
                "code": "UNSUPPORTED_EXISTING_NODE_TYPE",
                "message": str(exc),
                "node_id": exc.node_id,
                "node_type": exc.node_type,
            },
        ) from exc
    except DifyGraphAdapterError as exc:
        raise HTTPException(status_code=422, detail={"code": "DIFY_GRAPH_UNSUPPORTED", "message": str(exc)}) from exc
    except DifyClientError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc


@app.post("/api/workflows/modify/draft")
def draft_workflow_modification(request: WorkflowModifyRequest) -> dict:
    return _modify_workflow(request, apply=False)


@app.post("/api/workflows/modify/apply")
def apply_workflow_modification(request: WorkflowModifyRequest) -> dict:
    return _modify_workflow(request, apply=True)


@app.post("/api/workflows/run/draft")
def run_draft_workflow(request: WorkflowRunDraftRequest) -> dict:
    return _run_draft_workflow(request)


def _run_draft_workflow(
    request: WorkflowRunDraftRequest,
    *,
    task_context: TaskContext | None = None,
) -> dict:
    settings = load_settings()
    if task_context is not None:
        task_context.update("connecting", None, "Connecting to the Dify draft run stream.")
    try:
        with DifyClient(settings) as client:
            try:
                app_detail = _load_app_detail(client, request.app_id)
                if app_detail and app_detail.mode == "advanced-chat":
                    raise HTTPException(
                        status_code=422,
                        detail={
                            "code": "CHATFLOW_USE_CHAT_RUN_API",
                            "message": "Use /api/chatflows/run/draft for advanced-chat apps.",
                        },
                    )
                if app_detail and app_detail.mode == "agent-chat":
                    raise HTTPException(
                        status_code=422,
                        detail={
                            "code": "AGENT_USE_AGENT_RUN_API",
                            "message": "Use /api/agents/run/draft for agent-chat apps.",
                        },
                    )
                if app_detail and app_detail.mode == "chat":
                    raise HTTPException(
                        status_code=422,
                        detail={
                            "code": "CHATBOT_USE_CHATBOT_RUN_API",
                            "message": "Use /api/chatbots/run/draft for chat apps.",
                        },
                    )
                draft = client.get_draft_workflow(request.app_id)
                plan = decompile_dify_graph(draft.graph, name=f"Dify Workflow {request.app_id}")
                trigger_nodes = [
                    node
                    for node in plan.nodes
                    if node.type in {"trigger-webhook", "trigger-plugin", "trigger-schedule"}
                ]
                if trigger_nodes:
                    raise HTTPException(
                        status_code=422,
                        detail={
                            "code": "TRIGGER_WORKFLOW_DRAFT_RUN_UNSUPPORTED",
                            "message": (
                                "This workflow uses a trigger entry. Publish it explicitly, then invoke "
                                "the Webhook URL or wait for the schedule instead of supplying start inputs."
                            ),
                            "triggers": [
                                {
                                    "node_id": node.id,
                                    "type": node.type,
                                    "title": node.title,
                                }
                                for node in trigger_nodes
                            ],
                        },
                    )
            except AttributeError:
                pass
            run_kwargs = {
                "inputs": request.inputs,
                "files": request.files,
                "timeout_seconds": request.timeout_seconds,
            }
            if task_context is not None:
                run_kwargs["cancellation_check"] = task_context.raise_if_cancelled
                run_kwargs["event_callback"] = lambda event, summary: _update_run_task(
                    task_context,
                    event,
                    summary,
                )
            result = client.run_draft_workflow(request.app_id, **run_kwargs)
    except HTTPException:
        raise
    except DifyGraphAdapterError as exc:
        raise HTTPException(
            status_code=422,
            detail={"code": "DIFY_GRAPH_UNSUPPORTED", "message": str(exc)},
        ) from exc
    except DifyClientError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    return asdict(result)


@app.post("/api/chatflows/run/draft")
def run_draft_chatflow(request: ChatflowRunDraftRequest) -> dict:
    return _run_draft_chatflow(request)


@app.post("/api/chatbots/run/draft")
def run_draft_chatbot(request: ChatbotRunDraftRequest) -> dict:
    return _run_draft_chatbot(request)


def _run_draft_chatbot(
    request: ChatbotRunDraftRequest,
    *,
    task_context: TaskContext | None = None,
) -> dict:
    settings = load_settings()
    if task_context is not None:
        task_context.update("connecting", None, "Connecting to the Dify Chatbot chat stream.")
    try:
        with DifyClient(settings) as client:
            app_detail = _load_app_detail(client, request.app_id)
            if app_detail and app_detail.mode != "chat":
                raise HTTPException(
                    status_code=422,
                    detail={
                        "code": "APP_IS_NOT_CHATBOT",
                        "message": "The selected app is not a chat app.",
                        "app_mode": app_detail.mode,
                    },
                )
            model_config = _configured_app_model_config(app_detail)
            if model_config is None:
                raise HTTPException(
                    status_code=422,
                    detail={
                        "code": "CHATBOT_MODEL_CONFIG_MISSING",
                        "message": "Dify app detail did not include model_config for this Chatbot app.",
                    },
                )
            run_kwargs = {
                "query": request.query,
                "inputs": request.inputs,
                "files": request.files,
                "conversation_id": request.conversation_id,
                "parent_message_id": request.parent_message_id,
                "model_config": model_config,
                "timeout_seconds": request.timeout_seconds,
            }
            if task_context is not None:
                run_kwargs["cancellation_check"] = task_context.raise_if_cancelled
                run_kwargs["event_callback"] = lambda event, summary: _update_chatbot_run_task(
                    task_context,
                    event,
                    summary,
                )
            result = client.run_chatbot_chat(request.app_id, **run_kwargs)
    except HTTPException:
        raise
    except DifyClientError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    payload = asdict(result)
    payload["app_mode"] = "chat"
    return payload


def _run_draft_chatflow(
    request: ChatflowRunDraftRequest,
    *,
    task_context: TaskContext | None = None,
) -> dict:
    settings = load_settings()
    if task_context is not None:
        task_context.update("connecting", None, "Connecting to the Dify Chatflow draft stream.")
    try:
        with DifyClient(settings) as client:
            app_detail = _load_app_detail(client, request.app_id)
            if app_detail and app_detail.mode != "advanced-chat":
                raise HTTPException(
                    status_code=422,
                    detail={
                        "code": "APP_IS_NOT_CHATFLOW",
                        "message": "The selected app is not an advanced-chat app.",
                        "app_mode": app_detail.mode,
                    },
                )
            run_kwargs = {
                "query": request.query,
                "inputs": request.inputs,
                "files": request.files,
                "conversation_id": request.conversation_id,
                "parent_message_id": request.parent_message_id,
                "timeout_seconds": request.timeout_seconds,
            }
            if task_context is not None:
                run_kwargs["cancellation_check"] = task_context.raise_if_cancelled
                run_kwargs["event_callback"] = lambda event, summary: _update_chatflow_run_task(
                    task_context,
                    event,
                    summary,
                )
            result = client.run_draft_chatflow(request.app_id, **run_kwargs)
    except HTTPException:
        raise
    except DifyClientError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    return asdict(result)


@app.post("/api/agents/run/draft")
def run_draft_agent(request: AgentRunDraftRequest) -> dict:
    return _run_draft_agent(request)


def _run_draft_agent(
    request: AgentRunDraftRequest,
    *,
    task_context: TaskContext | None = None,
) -> dict:
    settings = load_settings()
    if task_context is not None:
        task_context.update("connecting", None, "Connecting to the Dify Agent chat stream.")
    try:
        with DifyClient(settings) as client:
            app_detail = _load_app_detail(client, request.app_id)
            if app_detail and app_detail.mode != "agent-chat":
                raise HTTPException(
                    status_code=422,
                    detail={
                        "code": "APP_IS_NOT_AGENT",
                        "message": "The selected app is not an agent-chat app.",
                        "app_mode": app_detail.mode,
                    },
                )
            model_config = _agent_model_config(app_detail)
            if model_config is None:
                raise HTTPException(
                    status_code=422,
                    detail={
                        "code": "AGENT_MODEL_CONFIG_MISSING",
                        "message": "Dify app detail did not include model_config for this Agent app.",
                    },
                )
            run_kwargs = {
                "query": request.query,
                "inputs": request.inputs,
                "files": request.files,
                "conversation_id": request.conversation_id,
                "parent_message_id": request.parent_message_id,
                "model_config": model_config,
                "timeout_seconds": request.timeout_seconds,
            }
            if task_context is not None:
                run_kwargs["cancellation_check"] = task_context.raise_if_cancelled
                run_kwargs["event_callback"] = lambda event, summary: _update_agent_run_task(
                    task_context,
                    event,
                    summary,
                )
            result = client.run_agent_chat(request.app_id, **run_kwargs)
    except HTTPException:
        raise
    except DifyClientError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    payload = asdict(result)
    payload["app_mode"] = "agent-chat"
    return payload


def _modify_workflow(
    request: WorkflowModifyRequest,
    *,
    apply: bool,
    task_context: TaskContext | None = None,
) -> dict:
    if task_context is not None:
        task_context.update("loading-draft", 10, "Loading the current Dify draft.")
    settings = load_settings()
    _ensure_agent_strategy_selection_for_request(request.message, request.agent_selections)
    _ensure_agent_selections_configured(request.agent_selections)
    effective_settings = _settings_with_request_dataset_ids(settings, request.dataset_ids)
    effective_settings = _settings_with_request_planner(
        effective_settings,
        request.planner,
        require_configured=not (apply and request.plan is not None),
    )
    version_info = read_dify_version_info(settings.dify_source_path)

    try:
        with DifyClient(settings) as client:
            model_catalog, runtime_models = _runtime_model_context(
                settings,
                request.model_selections,
                client=client,
            )
            effective_settings = _settings_with_primary_runtime_model(
                effective_settings,
                runtime_models,
            )
            _ensure_agent_runtime_models(
                request.agent_selections,
                model_catalog,
                runtime_models,
            )
            compiler = DifyDslCompiler(
                dsl_version=version_info.app_dsl_version,
                default_model_provider=effective_settings.dify_default_model_provider,
                default_model_name=effective_settings.dify_default_model_name,
                default_dataset_ids=effective_settings.dify_default_dataset_ids,
            )
            app_detail = _load_app_detail(client, request.app_id)
            app_mode = _app_mode(app_detail)
            if app_mode == "chat":
                return _modify_chat_app(
                    request,
                    apply=apply,
                    settings=settings,
                    app_detail=app_detail,
                    client=client,
                    task_context=task_context,
                )
            if app_mode == "agent-chat":
                raise HTTPException(
                    status_code=422,
                    detail={
                        "code": "AGENT_MODIFY_NOT_SUPPORTED",
                        "message": "agent-chat apps do not have a workflow draft graph to modify in v1.",
                    },
                )
            _ensure_chatflow_trigger_selection(app_mode, request.trigger_selection)
            draft = client.get_draft_workflow(request.app_id)
            app_mode = _app_mode(app_detail, draft.graph)
            _ensure_chatflow_trigger_selection(app_mode, request.trigger_selection)
            if request.expected_hash and request.expected_hash != draft.hash:
                raise HTTPException(
                    status_code=409,
                    detail={
                        "code": "DRAFT_HASH_MISMATCH",
                        "message": "Expected hash does not match the current Dify draft hash.",
                        "expected_hash": request.expected_hash,
                        "current_hash": draft.hash,
                    },
                )

            before_plan = decompile_dify_graph(
                draft.graph,
                name=_draft_plan_name(app_detail, request.app_id),
                app_mode=app_mode,
                conversation_variables=draft.conversation_variables,
            )
            if task_context is not None:
                task_context.update("decompiling", 25, "Converted the current Dify graph into Plan IR.")
            if apply and request.plan is not None:
                if task_context is not None:
                    task_context.update(
                        "validating-preview",
                        45,
                        "Validating the reviewed preview plan without replanning.",
                    )
                normalized = normalize_plan_payload(
                    request.plan.model_dump(),
                    app_name=before_plan.name,
                    app_mode=before_plan.app_mode,
                    default_dataset_ids=effective_settings.dify_default_dataset_ids,
                    tool_selections=_tool_selection_payloads(request.tool_selections),
                    agent_selections=_agent_selection_payloads(request.agent_selections),
                    model_selections=model_selection_payloads(runtime_models),
                    trigger_selection=None,
                )
                plan = WorkflowPlan.model_validate(normalized.payload)
                raw_plan = plan.model_dump()
                planner_metadata = _preview_plan_planner_metadata(
                    normalized.changes,
                    repair_actions=normalized.repair_actions,
                    settings=effective_settings,
                )
            else:
                trigger_selection = (
                    _hydrate_trigger_selection_with_client(client, request.trigger_selection)
                    if app_mode == "workflow"
                    else None
                )
                edit_kwargs = _planner_selection_kwargs(
                    request,
                    trigger_selection=trigger_selection,
                )
                edit_kwargs["model_selections"] = runtime_models
                edit_kwargs["model_catalog"] = model_catalog
                if task_context is not None:
                    edit_kwargs["task_context"] = task_context
                edit_result = WorkflowEditPlanner(effective_settings).generate(
                    request.message,
                    current_plan=before_plan,
                    dsl_version=version_info.app_dsl_version,
                    **edit_kwargs,
                )
                plan = edit_result.plan
                raw_plan = edit_result.raw_plan
                planner_metadata = edit_result.metadata()

            plan = _plan_with_dataset_retrieval_settings(plan, effective_settings, client=client)
            if task_context is not None:
                task_context.update("validating-change", 72, "Compiling, validating, and checking change risk.")
            response, graph = _build_modify_response(
                settings=settings,
                version_info=version_info,
                compiler=compiler,
                app_id=request.app_id,
                app_detail=app_detail,
                draft_hash=draft.hash,
                base_graph=draft.graph,
                before_plan=before_plan,
                plan=plan,
                raw_plan=raw_plan,
                planner_metadata=planner_metadata,
                model_issues=validate_runtime_model_bindings(
                    plan,
                    model_catalog,
                    allowed_models=runtime_models,
                    baseline_plan=before_plan,
                ),
            )

            if not apply:
                return response
            if not response["validation"]["ok"]:
                raise HTTPException(status_code=422, detail=response["validation"]["issues"])
            if response["guard"]["no_op"] and graph == draft.graph:
                response["new_hash"] = draft.hash
                response["sync"] = {
                    "result": "noop",
                    "hash": draft.hash,
                    "workflow_url": settings.workflow_url(request.app_id),
                }
                return response
            if not response["guard"]["ok"] and not request.allow_destructive:
                raise HTTPException(
                    status_code=422,
                    detail={
                        "code": "PLAN_CHANGE_GUARD_BLOCKED",
                        "message": "修改风险较高，默认安全模式已阻断写回。",
                        "guard": response["guard"],
                    },
                )

            if task_context is not None:
                task_context.update("syncing", 88, "Writing the reviewed draft back to Dify.")
            sync = client.sync_draft_workflow(
                request.app_id,
                graph=graph,
                features=draft.features,
                hash=draft.hash,
                environment_variables=draft.environment_variables,
                conversation_variables=[
                    variable.model_dump()
                    for variable in plan.conversation_variables
                ],
            )
            response["new_hash"] = sync.hash
            response["sync"] = asdict(sync)
            response["webhooks"] = (
                _webhook_details(client, request.app_id, plan)
                if app_mode == "workflow"
                else []
            )
            return response
    except HTTPException:
        raise
    except RuntimeModelSelectionError as exc:
        raise HTTPException(status_code=422, detail=exc.detail) from exc
    except UnsupportedExistingNodeType as exc:
        raise HTTPException(
            status_code=422,
            detail={
                "code": "UNSUPPORTED_EXISTING_NODE_TYPE",
                "message": str(exc),
                "node_id": exc.node_id,
                "node_type": exc.node_type,
            },
        ) from exc
    except DifyGraphAdapterError as exc:
        raise HTTPException(status_code=422, detail={"code": "DIFY_GRAPH_UNSUPPORTED", "message": str(exc)}) from exc
    except DifyConflictError as exc:
        raise HTTPException(status_code=409, detail={"code": "DRAFT_HASH_MISMATCH", "message": str(exc)}) from exc
    except PlannerError as exc:
        raise HTTPException(
            status_code=502,
            detail=exc.detail if exc.detail is not None else str(exc),
        ) from exc
    except DifyClientError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc


def _modify_chat_app(
    request: WorkflowModifyRequest,
    *,
    apply: bool,
    settings: Settings,
    app_detail: DifyAppDetail | None,
    client: DifyClient,
    task_context: TaskContext | None = None,
) -> dict:
    if task_context is not None:
        task_context.update("loading-config", 25, "Loaded Chatbot model configuration.")
    current_config = _configured_app_model_config(app_detail)
    if current_config is None:
        raise HTTPException(
            status_code=422,
            detail={
                "code": "CHATBOT_MODEL_CONFIG_MISSING",
                "message": "Dify app detail did not include model_config for this Chatbot app.",
            },
        )
    base_hash = _model_config_hash(app_detail, current_config)
    if request.expected_hash and base_hash and request.expected_hash != base_hash:
        raise HTTPException(
            status_code=409,
            detail={
                "code": "MODEL_CONFIG_HASH_MISMATCH",
                "message": "Expected hash does not match the current Chatbot model config hash.",
                "expected_hash": request.expected_hash,
                "current_hash": base_hash,
            },
        )

    if task_context is not None:
        task_context.update("revising-config", 55, "Revising Chatbot prompt configuration.")
    revised_config, changes = _revise_chat_model_config(current_config, request.message)
    no_op = revised_config == current_config
    response = {
        "app_id": request.app_id,
        "app_mode": "chat",
        "workflow_url": settings.app_url(request.app_id, "chat"),
        "base_hash": base_hash,
        "app": _app_payload(app_detail),
        "before_model_config": current_config,
        "model_config": revised_config,
        "changes": changes,
        "explanation": {
            "summary": "Updates the Chatbot prompt configuration.",
            "changes": [change["message"] for change in changes],
            "mode": "chat",
        },
        "planner": {
            "mode": "chat-config-template",
            "attempts": 0,
            "used_fallback": True,
            "repaired": False,
            "replanned": False,
            "normalizations": [],
            "errors": [],
            "repair_actions": [],
            "attempt_diagnostics": [],
            "preflight": {},
        },
        "guard": {
            "ok": True,
            "risk": "low",
            "no_op": no_op,
            "issues": [],
        },
        "validation": {
            "ok": True,
            "issues": [],
        },
    }
    if not apply:
        return response
    if no_op:
        response["new_hash"] = base_hash
        response["sync"] = {
            "result": "noop",
            "hash": base_hash,
            "workflow_url": settings.app_url(request.app_id, "chat"),
        }
        return response
    if task_context is not None:
        task_context.update("syncing", 88, "Writing the Chatbot model configuration back to Dify.")
    sync = client.update_model_config(request.app_id, revised_config)
    response["sync"] = sync
    response["new_hash"] = _model_config_hash_from_payload(sync) or base_hash
    return response


@app.post("/api/tasks/workflows/create", status_code=status.HTTP_202_ACCEPTED)
def create_workflow_task(request: WorkflowRequest, http_request: Request) -> dict:
    return _submit_task(
        http_request,
        "workflow.create",
        request.model_dump(mode="json"),
        lambda context: _create_workflow(request, task_context=context),
    )


@app.post("/api/tasks/workflows/modify/draft", status_code=status.HTTP_202_ACCEPTED)
def modify_workflow_draft_task(request: WorkflowModifyRequest, http_request: Request) -> dict:
    return _submit_task(
        http_request,
        "workflow.modify.draft",
        request.model_dump(mode="json"),
        lambda context: _modify_workflow(request, apply=False, task_context=context),
    )


@app.post("/api/tasks/workflows/modify/apply", status_code=status.HTTP_202_ACCEPTED)
def modify_workflow_apply_task(request: WorkflowModifyRequest, http_request: Request) -> dict:
    return _submit_task(
        http_request,
        "workflow.modify.apply",
        request.model_dump(mode="json"),
        lambda context: _modify_workflow(request, apply=True, task_context=context),
    )


@app.post("/api/tasks/workflows/run/draft", status_code=status.HTTP_202_ACCEPTED)
def run_draft_workflow_task(request: WorkflowRunDraftRequest, http_request: Request) -> dict:
    return _submit_task(
        http_request,
        "workflow.run.draft",
        request.model_dump(mode="json"),
        lambda context: _run_draft_workflow(request, task_context=context),
    )


@app.post("/api/tasks/chatflows/run/draft", status_code=status.HTTP_202_ACCEPTED)
def run_draft_chatflow_task(request: ChatflowRunDraftRequest, http_request: Request) -> dict:
    return _submit_task(
        http_request,
        "chatflow.run.draft",
        request.model_dump(mode="json"),
        lambda context: _run_draft_chatflow(request, task_context=context),
    )


@app.post("/api/tasks/chatbots/run/draft", status_code=status.HTTP_202_ACCEPTED)
def run_draft_chatbot_task(request: ChatbotRunDraftRequest, http_request: Request) -> dict:
    return _submit_task(
        http_request,
        "chatbot.run.draft",
        request.model_dump(mode="json"),
        lambda context: _run_draft_chatbot(request, task_context=context),
    )


@app.post("/api/tasks/agents/run/draft", status_code=status.HTTP_202_ACCEPTED)
def run_draft_agent_task(request: AgentRunDraftRequest, http_request: Request) -> dict:
    return _submit_task(
        http_request,
        "agent.run.draft",
        request.model_dump(mode="json"),
        lambda context: _run_draft_agent(request, task_context=context),
    )


@app.post("/api/tasks/workflows/publish", status_code=status.HTTP_202_ACCEPTED)
def publish_workflow_task(request: WorkflowPublishTaskRequest, http_request: Request) -> dict:
    publish_request = WorkflowPublishRequest.model_validate(request.model_dump(exclude={"app_id"}))
    return _submit_task(
        http_request,
        "workflow.publish",
        request.model_dump(mode="json"),
        lambda context: _publish_workflow(request.app_id, publish_request, task_context=context),
    )


@app.get("/api/tasks/{task_id}")
def get_task(task_id: str, request: Request) -> dict:
    try:
        return _task_manager(request).get(task_id).to_dict()
    except TaskNotFound as exc:
        raise HTTPException(status_code=404, detail={"code": "TASK_NOT_FOUND", "task_id": task_id}) from exc


@app.post("/api/tasks/{task_id}/cancel")
def cancel_task(task_id: str, request: Request) -> dict:
    try:
        record, accepted = _task_manager(request).cancel(task_id)
    except TaskNotFound as exc:
        raise HTTPException(status_code=404, detail={"code": "TASK_NOT_FOUND", "task_id": task_id}) from exc
    payload = record.to_dict()
    payload["accepted"] = accepted
    return payload


def _submit_task(request: Request, operation: str, payload: dict, callback) -> dict:
    return _task_manager(request).submit(operation, payload, callback).to_dict()


def _task_manager(request: Request) -> TaskManager:
    manager = getattr(request.app.state, "task_manager", None)
    if manager is None:
        raise HTTPException(status_code=503, detail="Background task manager is not available.")
    return manager


def _update_run_task(task_context: TaskContext, event: dict, summary: dict) -> None:
    event_type = str(event.get("event") or "event")
    node_finished = int(summary.get("node_finished") or 0)
    total_events = int(summary.get("events") or 0)
    task_context.update(
        "running-workflow",
        None,
        f"Dify event {event_type}; {node_finished} nodes finished, {total_events} events received.",
    )


def _update_chatflow_run_task(task_context: TaskContext, event: dict, summary: dict) -> None:
    event_type = str(event.get("event") or "event")
    node_finished = int(summary.get("node_finished") or 0)
    message_chunks = int((summary.get("event_counts") or {}).get("message") or 0)
    task_context.update(
        "running-chatflow",
        None,
        (
            f"Dify event {event_type}; {node_finished} nodes finished, "
            f"{message_chunks} answer chunks received."
        ),
    )


def _update_agent_run_task(task_context: TaskContext, event: dict, summary: dict) -> None:
    event_type = str(event.get("event") or "event")
    message_chunks = int((summary.get("event_counts") or {}).get("message") or 0)
    agent_chunks = int((summary.get("event_counts") or {}).get("agent_message") or 0)
    task_context.update(
        "running-agent",
        None,
        (
            f"Dify event {event_type}; {message_chunks + agent_chunks} "
            "Agent answer chunks received."
        ),
    )


def _update_chatbot_run_task(task_context: TaskContext, event: dict, summary: dict) -> None:
    event_type = str(event.get("event") or "event")
    message_chunks = int((summary.get("event_counts") or {}).get("message") or 0)
    task_context.update(
        "running-chatbot",
        None,
        f"Dify event {event_type}; {message_chunks} Chatbot answer chunks received.",
    )


def _build_modify_response(
    *,
    settings,
    version_info,
    compiler: DifyDslCompiler,
    app_id: str,
    app_detail: DifyAppDetail | None,
    draft_hash: str,
    base_graph: dict,
    before_plan: WorkflowPlan,
    plan: WorkflowPlan,
    raw_plan: dict,
    planner_metadata: dict,
    model_issues: list | None = None,
) -> tuple[dict, dict]:
    preflight = preflight_plan(
        plan,
        compiler=compiler,
        expected_dsl_version=version_info.app_dsl_version,
    )
    dsl = preflight.dsl
    graph = compile_plan_to_dify_graph(plan, compiler=compiler, base_graph=base_graph)
    issues = [
        *preflight.issues,
        *(model_issues or []),
    ]
    changes = diff_plans(before_plan, plan)
    guard = guard_plan_change(before_plan, plan, changes)
    explanation = explain_plan(plan)
    explanation["changes"] = [change["message"] for change in changes]
    explanation["preserved"] = _preserved_node_summary(before_plan, plan, changes)

    final_planner_metadata = dict(planner_metadata)
    final_planner_metadata["preflight"] = preflight.metadata()
    response = {
        "app_id": app_id,
        "app_mode": plan.app_mode,
        "workflow_url": settings.workflow_url(app_id),
        "base_hash": draft_hash,
        "app": _app_payload(app_detail),
        "raw_plan": raw_plan,
        "before_plan": before_plan.model_dump(),
        "plan": plan.model_dump(),
        "changes": changes,
        "explanation": explanation,
        "planner": final_planner_metadata,
        "guard": guard.to_dict(),
        "validation": {
            "ok": not has_errors(issues),
            "issues": [issue.model_dump() for issue in issues],
        },
        "dsl": dsl,
    }
    return response, graph


def _settings_with_request_dataset_ids(settings: Settings, dataset_ids: list[str] | None) -> Settings:
    request_dataset_ids = [str(item).strip() for item in dataset_ids or [] if str(item).strip()]
    if not request_dataset_ids:
        return settings
    return replace(settings, dify_default_dataset_ids=request_dataset_ids)


def _runtime_model_context(
    settings: Settings,
    selections,
    *,
    client: DifyClient | None = None,
):
    if client is not None:
        catalog = load_runtime_model_catalog(client, settings)
    else:
        with DifyClient(settings) as model_client:
            catalog = load_runtime_model_catalog(model_client, settings)
    return catalog, resolve_runtime_model_selections(catalog, settings, selections)


def _settings_with_primary_runtime_model(settings: Settings, models) -> Settings:
    if not models:
        return settings
    primary = models[0]
    return replace(
        settings,
        dify_default_model_provider=primary.provider,
        dify_default_model_name=primary.model,
    )


def _ensure_agent_runtime_models(agent_selections, catalog, runtime_models) -> None:
    issues = validate_agent_selection_models(
        agent_selections,
        catalog,
        runtime_models,
    )
    if issues:
        raise RuntimeModelSelectionError(
            {
                "code": "AGENT_MODEL_SELECTION_INVALID",
                "message": "Agent Strategy runtime model validation failed.",
                "issues": [issue.model_dump() for issue in issues],
            }
        )


def _settings_with_request_planner(
    settings: Settings,
    selection,
    *,
    require_configured: bool = True,
) -> Settings:
    if selection is None:
        return settings
    provider = getattr(selection, "provider", None)
    model = getattr(selection, "model", None)
    try:
        selected = settings.with_planner(provider, model)
    except ConfigurationError as exc:
        raise HTTPException(
            status_code=422,
            detail={"code": "PLANNER_SELECTION_INVALID", "message": str(exc)},
        ) from exc
    runtime = selected.planner_runtime()
    if require_configured and not runtime.configured:
        raise HTTPException(
            status_code=422,
            detail={
                "code": "PLANNER_PROVIDER_NOT_CONFIGURED",
                "message": f"{runtime.label} is not configured on the chat2dify server.",
                "suggestion": f"Set the API key for planner provider {runtime.provider} in .env and restart port 8000.",
            },
        )
    return selected


def _tool_selection_payloads(tool_selections) -> list[dict]:
    result = []
    for item in tool_selections or []:
        if hasattr(item, "model_dump"):
            result.append(item.model_dump(exclude_none=True))
        elif isinstance(item, dict):
            result.append({key: value for key, value in item.items() if value is not None})
    return result


def _agent_selection_payloads(agent_selections) -> list[dict]:
    result = []
    for item in agent_selections or []:
        if hasattr(item, "model_dump"):
            result.append(item.model_dump(exclude_none=True))
        elif isinstance(item, dict):
            result.append({key: value for key, value in item.items() if value is not None})
    return result


def _planner_selection_kwargs(request, *, trigger_selection: dict | None = None) -> dict:
    kwargs: dict = {}
    tool_selections = _tool_selection_payloads(getattr(request, "tool_selections", None))
    agent_selections = _agent_selection_payloads(getattr(request, "agent_selections", None))
    if tool_selections:
        kwargs["tool_selections"] = tool_selections
    if agent_selections:
        kwargs["agent_selections"] = agent_selections
    if trigger_selection is None:
        trigger_selection = _trigger_selection_payload(getattr(request, "trigger_selection", None))
    if trigger_selection:
        kwargs["trigger_selection"] = trigger_selection
    return kwargs


def _trigger_selection_payload(trigger_selection) -> dict | None:
    if trigger_selection is None:
        return None
    if hasattr(trigger_selection, "model_dump"):
        return trigger_selection.model_dump(exclude_none=True)
    if isinstance(trigger_selection, dict):
        return {key: value for key, value in trigger_selection.items() if value is not None}
    return None


def _hydrate_trigger_selection(settings: Settings, trigger_selection) -> dict | None:
    payload = _trigger_selection_payload(trigger_selection)
    if not payload or payload.get("type") != "plugin":
        return payload
    with DifyClient(settings) as client:
        return _hydrate_trigger_selection_with_client(client, payload)


def _hydrate_trigger_selection_with_client(client: DifyClient, trigger_selection) -> dict | None:
    payload = _trigger_selection_payload(trigger_selection)
    if not payload or payload.get("type") != "plugin":
        return payload

    provider_id = str(payload.get("provider_id") or "").strip()
    event_name = str(payload.get("event_name") or "").strip()
    subscription_id = str(payload.get("subscription_id") or "").strip()
    if not provider_id or not event_name or not subscription_id:
        raise HTTPException(
            status_code=422,
            detail={
                "code": "PLUGIN_TRIGGER_SELECTION_INCOMPLETE",
                "message": "Plugin Trigger requires provider_id, event_name, and subscription_id.",
            },
        )

    providers = client.list_trigger_providers()
    event = next(
        (
            item
            for item in providers.data
            if item.provider_id == provider_id and item.event_name == event_name
        ),
        None,
    )
    if event is None:
        raise HTTPException(
            status_code=422,
            detail={
                "code": "PLUGIN_TRIGGER_EVENT_NOT_FOUND",
                "message": "The selected Plugin Trigger provider/event is not installed in Dify.",
                "provider_id": provider_id,
                "event_name": event_name,
            },
        )

    subscriptions = client.list_trigger_subscriptions(provider_id)
    subscription = next((item for item in subscriptions.data if item.id == subscription_id), None)
    if subscription is None or subscription.provider_id != provider_id:
        raise HTTPException(
            status_code=422,
            detail={
                "code": "PLUGIN_TRIGGER_SUBSCRIPTION_NOT_FOUND",
                "message": "The selected subscription does not belong to the selected Trigger Provider.",
                "provider_id": provider_id,
                "subscription_id": subscription_id,
            },
        )

    return {
        "type": "plugin",
        "provider_id": event.provider_id,
        "provider_type": event.provider_type,
        "provider_name": event.provider_name,
        "plugin_id": event.plugin_id,
        "plugin_unique_identifier": event.plugin_unique_identifier,
        "event_name": event.event_name,
        "event_label": event.event_label,
        "subscription_id": subscription.id,
        "event_parameters": payload.get("event_parameters")
        if isinstance(payload.get("event_parameters"), dict)
        else {},
        "parameters_schema": event.parameters,
        "output_schema": event.output_schema,
    }


def _ensure_agent_strategy_selection_for_request(message: str, agent_selections) -> None:
    if not _message_requests_agent_strategy(message):
        return
    if _agent_selection_payloads(agent_selections):
        return
    raise HTTPException(
        status_code=422,
        detail={
            "code": "AGENT_STRATEGY_SELECTION_REQUIRED",
            "message": (
                "This request asks for an Agent/智能体 workflow, but no Dify Agent Strategy was selected. "
                "The Web UI Agent panel lists Agent Strategy plugins, not Dify Agent apps. "
                "Select an installed Agent Strategy plugin first, or rewrite the request to use LLM/Tool nodes."
            ),
        },
    )


def _message_requests_agent_strategy(message: str) -> bool:
    text = (message or "").lower().replace("user agent", "")
    patterns = (
        r"智能体",
        r"agent strategy",
        r"agent策略",
        r"agent 节点",
        r"agent节点",
        r"自主规划",
        r"多步执行",
        r"\bagent\b",
    )
    return any(
        not _agent_term_is_negated(text, match.start())
        for pattern in patterns
        for match in re.finditer(pattern, text)
    )


def _agent_term_is_negated(text: str, start: int) -> bool:
    prefix = text[max(0, start - 36):start]
    return bool(
        re.search(
            r"(?:不需要|无需|无须|不要|不使用|不是|禁止|避免|别|"
            r"do not|don't|without|no need to)"
            r"[^。！？!?；;，,\n]{0,28}$",
            prefix,
        )
    )


def _ensure_agent_selections_configured(agent_selections) -> None:
    payloads = _agent_selection_payloads(agent_selections)
    issues: list[dict] = []
    for selection_index, selection in enumerate(payloads):
        parameters = selection.get("parameters") if isinstance(selection.get("parameters"), list) else []
        values = selection.get("agent_parameters") if isinstance(selection.get("agent_parameters"), dict) else {}
        strategy = selection.get("agent_strategy_label") or selection.get("agent_strategy_name") or f"#{selection_index + 1}"
        for parameter in parameters:
            if not isinstance(parameter, dict) or not parameter.get("required"):
                continue
            name = str(parameter.get("variable") or parameter.get("name") or "").strip()
            if not name:
                continue
            value = values.get(name)
            if value is None and parameter.get("name") != name:
                value = values.get(str(parameter.get("name")))
            parameter_type = str(parameter.get("type") or "").strip()
            if not _agent_parameter_has_value(value, parameter_type):
                issues.append(
                    {
                        "code": "AGENT_REQUIRED_PARAMETER_MISSING",
                        "path": f"agent_selections.{selection_index}.agent_parameters.{name}",
                        "message": f"Agent Strategy {strategy} required parameter is missing: {name}",
                        "suggestion": "在 Web UI 的 Agent Strategies 面板补齐红色必填项后再创建。",
                    }
                )
                continue
            if parameter_type == "model-selector" and not _agent_model_selector_has_value(value):
                issues.append(
                    {
                        "code": "AGENT_MODEL_PARAMETER_INVALID",
                        "path": f"agent_selections.{selection_index}.agent_parameters.{name}",
                        "message": f"Agent Strategy {strategy} model parameter requires provider and model.",
                        "suggestion": "模型参数需要类似 {'type':'constant','value':{'provider':'...','model':'...'}} 的值。",
                    }
                )
            if parameter_type == "array[tools]" and not _agent_tools_parameter_has_value(value):
                issues.append(
                    {
                        "code": "AGENT_TOOLS_PARAMETER_MISSING",
                        "path": f"agent_selections.{selection_index}.agent_parameters.{name}",
                        "message": f"Agent Strategy {strategy} requires at least one enabled tool.",
                        "suggestion": "先在 Tools 面板选择并配置工具，再在 Agent Strategy 中绑定工具列表。",
                    }
                )
    if issues:
        raise HTTPException(
            status_code=422,
            detail={
                "code": "AGENT_SELECTION_REQUIRED_PARAMETER_MISSING",
                "message": "Agent Strategy has missing or invalid required parameters.",
                "issues": issues,
            },
        )


def _agent_parameter_has_value(value, parameter_type: str) -> bool:
    if not isinstance(value, dict):
        return value not in (None, "", [])
    raw_value = value.get("value")
    if parameter_type == "array[tools]":
        return _agent_tools_parameter_has_value(value)
    if parameter_type == "model-selector":
        return _agent_model_selector_has_value(value)
    return raw_value not in (None, "", [])


def _agent_model_selector_has_value(value) -> bool:
    raw_value = value.get("value") if isinstance(value, dict) else value
    if not isinstance(raw_value, dict):
        return False
    provider = str(raw_value.get("provider") or "").strip()
    model = str(raw_value.get("model") or raw_value.get("name") or "").strip()
    return bool(provider and model)


def _agent_tools_parameter_has_value(value) -> bool:
    raw_value = value.get("value") if isinstance(value, dict) else value
    return isinstance(raw_value, list) and any(isinstance(item, dict) and item.get("enabled", True) for item in raw_value)


def _plan_with_dataset_retrieval_settings(
    plan: WorkflowPlan,
    settings: Settings,
    *,
    client: DifyClient | None = None,
) -> WorkflowPlan:
    dataset_ids = knowledge_dataset_ids(plan, settings.dify_default_dataset_ids)
    if not dataset_ids or not (settings.dify_email and settings.dify_password):
        return plan

    try:
        if client is not None:
            dataset_result = client.get_datasets_by_ids(dataset_ids)
        else:
            with DifyClient(settings) as dataset_client:
                dataset_result = dataset_client.get_datasets_by_ids(dataset_ids)
    except (AttributeError, DifyClientError):
        return plan

    datasets_by_id = _datasets_by_id(dataset_result)
    return apply_dataset_retrieval_settings(
        plan,
        datasets_by_id,
        default_dataset_ids=settings.dify_default_dataset_ids,
    )


def _datasets_by_id(dataset_result) -> dict[str, object]:
    data = getattr(dataset_result, "data", dataset_result if isinstance(dataset_result, list) else [])
    result: dict[str, object] = {}
    for item in data or []:
        if isinstance(item, dict):
            dataset_id = str(item.get("id", "")).strip()
        else:
            dataset_id = str(getattr(item, "id", "")).strip()
        if dataset_id:
            result[dataset_id] = item
    return result


def _preview_plan_planner_metadata(
    normalizations: list[str] | None = None,
    *,
    repair_actions: list[dict] | None = None,
    settings: Settings | None = None,
) -> dict:
    metadata = {
        "mode": "preview-plan",
        "attempts": 0,
        "used_fallback": False,
        "repaired": bool(normalizations or repair_actions),
        "replanned": False,
        "normalizations": normalizations or [],
        "errors": [],
        "repair_actions": repair_actions or [],
        "attempt_diagnostics": [],
        "preflight": {},
    }
    if settings is not None:
        runtime = settings.planner_runtime()
        metadata["provider"] = runtime.provider
        metadata["model"] = runtime.model
    return metadata


def _preserved_node_summary(
    before_plan: WorkflowPlan,
    after_plan: WorkflowPlan,
    changes: list[dict],
) -> list[str]:
    changed_ids = {
        str(change.get("target"))
        for change in changes
        if change.get("type") not in {"edge_added", "edge_removed"}
    }
    before_ids = {node.id for node in before_plan.nodes}
    preserved = [
        node
        for node in after_plan.nodes
        if node.id in before_ids and node.id not in changed_ids
    ]
    if not preserved:
        return []
    return [f"保留 {len(preserved)} 个未改动节点：" + "、".join(node.title or node.id for node in preserved[:6])]


def _load_app_detail(client: DifyClient, app_id: str) -> DifyAppDetail | None:
    try:
        return client.get_app_detail(app_id)
    except AttributeError:
        return None
    except DifyClientError:
        return None


def _draft_plan_name(app_detail: DifyAppDetail | None, app_id: str) -> str:
    if app_detail and app_detail.name:
        return app_detail.name
    return f"Dify Workflow {app_id}"


def _app_mode(app_detail: DifyAppDetail | None, graph: dict | None = None) -> str:
    if app_detail and app_detail.mode == "chat":
        return "chat"
    if app_detail and app_detail.mode == "agent-chat":
        return "agent-chat"
    if app_detail and app_detail.mode == "advanced-chat":
        return "advanced-chat"
    if app_detail and app_detail.mode == "workflow":
        return "workflow"
    nodes = graph.get("nodes") if isinstance(graph, dict) else []
    if any(
        isinstance(node, dict)
        and isinstance(node.get("data"), dict)
        and node["data"].get("type") == "answer"
        for node in nodes or []
    ):
        return "advanced-chat"
    return "workflow"


def _configured_app_model_config(app_detail: DifyAppDetail | None) -> dict | None:
    if app_detail is None:
        return None
    raw = app_detail.raw if isinstance(app_detail.raw, dict) else {}
    for key in ("model_config", "model_config_data", "app_model_config"):
        value = raw.get(key)
        if isinstance(value, dict):
            return value
    return None


def _ensure_chatflow_trigger_selection(app_mode: str, trigger_selection) -> None:
    if (
        app_mode == "advanced-chat"
        and trigger_selection is not None
        and trigger_selection.type != "user-input"
    ):
        raise HTTPException(
            status_code=422,
            detail={
                "code": "CHATFLOW_TRIGGER_NOT_SUPPORTED",
                "message": "Chatflow uses a conversational start and cannot use workflow triggers.",
            },
        )


def _app_payload(app_detail: DifyAppDetail | None) -> dict | None:
    if app_detail is None:
        return None
    return {
        "id": app_detail.id,
        "name": app_detail.name,
        "mode": app_detail.mode,
        "description": app_detail.description,
    }


def _agent_model_config(app_detail: DifyAppDetail | None) -> dict | None:
    return _configured_app_model_config(app_detail)


def _model_config_hash(app_detail: DifyAppDetail | None, model_config: dict) -> str | None:
    raw = app_detail.raw if app_detail and isinstance(app_detail.raw, dict) else {}
    for source in (
        model_config,
        raw.get("model_config") if isinstance(raw.get("model_config"), dict) else {},
        raw,
    ):
        for key in ("hash", "updated_at", "version"):
            value = source.get(key) if isinstance(source, dict) else None
            if value not in (None, ""):
                return str(value)
    return None


def _model_config_hash_from_payload(payload: dict) -> str | None:
    for source in (
        payload,
        payload.get("model_config") if isinstance(payload.get("model_config"), dict) else {},
        payload.get("data") if isinstance(payload.get("data"), dict) else {},
    ):
        for key in ("hash", "updated_at", "version"):
            value = source.get(key) if isinstance(source, dict) else None
            if value not in (None, ""):
                return str(value)
    return None


def _revise_chat_model_config(model_config: dict, message: str) -> tuple[dict, list[dict]]:
    revised = deepcopy(model_config)
    changes: list[dict] = []
    request_text = " ".join(str(message or "").split())
    if not request_text:
        return revised, changes

    before_prompt = str(revised.get("pre_prompt") or "")
    addition = f"Additional instruction: {request_text}"
    revised_prompt = f"{before_prompt.rstrip()}\n\n{addition}" if before_prompt.strip() else addition
    if revised_prompt != before_prompt:
        revised["pre_prompt"] = revised_prompt
        changes.append(
            {
                "type": "prompt_changed",
                "target": "pre_prompt",
                "message": "更新聊天助手系统提示词。",
            }
        )

    opening = _extract_chat_opening_statement(request_text)
    if opening is not None and opening != revised.get("opening_statement"):
        revised["opening_statement"] = opening
        changes.append(
            {
                "type": "opening_statement_changed",
                "target": "opening_statement",
                "message": "更新聊天助手开场白。",
            }
        )

    suggested = _extract_suggested_questions(request_text)
    if suggested is not None and suggested != revised.get("suggested_questions"):
        revised["suggested_questions"] = suggested
        changes.append(
            {
                "type": "suggested_questions_changed",
                "target": "suggested_questions",
                "message": "更新聊天助手建议问题。",
            }
        )
    return revised, changes


def _extract_chat_opening_statement(message: str) -> str | None:
    if not re.search(r"(开场白|欢迎语|opening statement|greeting)", message, re.IGNORECASE):
        return None
    quoted = _quoted_segments(message)
    if quoted:
        return quoted[0]
    return message[:160]


def _extract_suggested_questions(message: str) -> list[str] | None:
    if not re.search(r"(建议问题|推荐问题|suggested questions?)", message, re.IGNORECASE):
        return None
    quoted = _quoted_segments(message)
    if quoted:
        return quoted[:5]
    parts = [
        item.strip(" -，,;；。")
        for item in re.split(r"[|｜\n;；]", message)
        if item.strip(" -，,;；。")
    ]
    return parts[:5]


def _quoted_segments(text: str) -> list[str]:
    return [
        match.group(1).strip()
        for match in re.finditer(r"[\"“'‘]([^\"”'’]{1,160})[\"”'’]", text)
        if match.group(1).strip()
    ]


def _webhook_details(client: DifyClient, app_id: str, plan: WorkflowPlan) -> list[dict]:
    details: list[dict] = []
    for node in plan.nodes:
        if node.type != "trigger-webhook":
            continue
        try:
            details.append(asdict(client.get_webhook_trigger(app_id, node.id)))
        except (AttributeError, DifyClientError):
            continue
    return details


def _load_webhook_details(settings: Settings, app_id: str, plan: WorkflowPlan) -> list[dict]:
    try:
        with DifyClient(settings) as client:
            return _webhook_details(client, app_id, plan)
    except (AttributeError, DifyClientError):
        return []
