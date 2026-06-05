from __future__ import annotations

from contextlib import asynccontextmanager
from dataclasses import asdict, replace
from pathlib import Path
import re

from fastapi import FastAPI, HTTPException, Query
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from app.agent.diff import diff_plans
from app.agent.editor import WorkflowEditPlanner
from app.agent.explainer import explain_plan
from app.agent.guard import guard_plan_change
from app.agent.normalizer import normalize_plan_payload
from app.agent.planner import PlannerError, WorkflowPlanner
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
from app.dify.version import read_dify_version_info
from app.models import WorkflowModifyRequest, WorkflowPlan, WorkflowRequest, WorkflowRunDraftRequest
from app.validator import has_errors, validate_dsl, validate_plan


STATIC_DIR = Path(__file__).resolve().parent / "static"


@asynccontextmanager
async def lifespan(_: FastAPI):
    settings = load_settings()
    read_dify_version_info(settings.dify_source_path)
    yield


app = FastAPI(title="chat2dify", version="0.1.0", lifespan=lifespan)
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


@app.post("/api/workflows/draft")
def draft_workflow(request: WorkflowRequest) -> dict:
    settings = load_settings()
    effective_settings = _settings_with_request_dataset_ids(settings, request.dataset_ids)
    effective_settings = _settings_with_request_planner(effective_settings, request.planner)
    version_info = read_dify_version_info(settings.dify_source_path)
    _ensure_agent_strategy_selection_for_request(request.message, request.agent_selections)
    _ensure_agent_selections_configured(request.agent_selections)
    try:
        planner_kwargs = _planner_selection_kwargs(request)
        planner_result = WorkflowPlanner(effective_settings).generate(
            request.message,
            app_name=request.app_name,
            dsl_version=version_info.app_dsl_version,
            **planner_kwargs,
        )
    except PlannerError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    plan = _plan_with_dataset_retrieval_settings(planner_result.plan, effective_settings)
    compiler = DifyDslCompiler(
        dsl_version=version_info.app_dsl_version,
        default_model_provider=effective_settings.dify_default_model_provider,
        default_model_name=effective_settings.dify_default_model_name,
        default_dataset_ids=effective_settings.dify_default_dataset_ids,
    )
    dsl = compiler.compile(plan)
    issues = [*validate_plan(plan), *validate_dsl(dsl, expected_dsl_version=version_info.app_dsl_version)]
    return {
        "raw_plan": planner_result.raw_plan,
        "plan": plan.model_dump(),
        "explanation": explain_plan(plan),
        "planner": planner_result.metadata(),
        "dsl": dsl,
        "validation": {
            "ok": not has_errors(issues),
            "issues": [issue.model_dump() for issue in issues],
        },
        "dify": asdict(version_info),
    }


@app.post("/api/workflows/create")
def create_workflow(request: WorkflowRequest) -> dict:
    draft = draft_workflow(request)
    if not draft["validation"]["ok"]:
        raise HTTPException(status_code=422, detail=draft["validation"]["issues"])

    settings = load_settings()
    try:
        with DifyClient(settings) as client:
            result = client.import_yaml(draft["dsl"], name=request.app_name or draft["plan"]["name"])
    except DifyClientError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc

    return {
        "status": result.status,
        "app_id": result.app_id,
        "app_mode": result.app_mode,
        "workflow_url": result.workflow_url,
        "import": asdict(result),
        "raw_plan": draft["raw_plan"],
        "plan": draft["plan"],
        "explanation": draft["explanation"],
        "planner": draft["planner"],
        "validation": draft["validation"],
        "dsl": draft["dsl"],
    }


@app.get("/api/workflows/{app_id}/draft")
def get_workflow_draft(app_id: str) -> dict:
    settings = load_settings()
    version_info = read_dify_version_info(settings.dify_source_path)
    try:
        with DifyClient(settings) as client:
            app_detail = _load_app_detail(client, app_id)
            draft = client.get_draft_workflow(app_id)

        plan = decompile_dify_graph(draft.graph, name=_draft_plan_name(app_detail, app_id))
        issues = validate_plan(plan)
        return {
            "app_id": app_id,
            "workflow_url": settings.workflow_url(app_id),
            "base_hash": draft.hash,
            "app": _app_payload(app_detail),
            "plan": plan.model_dump(),
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
    settings = load_settings()
    try:
        with DifyClient(settings) as client:
            result = client.run_draft_workflow(
                request.app_id,
                inputs=request.inputs,
                files=request.files,
                timeout_seconds=request.timeout_seconds,
            )
    except DifyClientError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    return asdict(result)


def _modify_workflow(request: WorkflowModifyRequest, *, apply: bool) -> dict:
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
    compiler = DifyDslCompiler(
        dsl_version=version_info.app_dsl_version,
        default_model_provider=effective_settings.dify_default_model_provider,
        default_model_name=effective_settings.dify_default_model_name,
        default_dataset_ids=effective_settings.dify_default_dataset_ids,
    )

    try:
        with DifyClient(settings) as client:
            app_detail = _load_app_detail(client, request.app_id)
            draft = client.get_draft_workflow(request.app_id)
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

            before_plan = decompile_dify_graph(draft.graph, name=_draft_plan_name(app_detail, request.app_id))
            if apply and request.plan is not None:
                normalized = normalize_plan_payload(
                    request.plan.model_dump(),
                    app_name=before_plan.name,
                    default_dataset_ids=effective_settings.dify_default_dataset_ids,
                    tool_selections=_tool_selection_payloads(request.tool_selections),
                    agent_selections=_agent_selection_payloads(request.agent_selections),
                )
                plan = WorkflowPlan.model_validate(normalized.payload)
                raw_plan = plan.model_dump()
                planner_metadata = _preview_plan_planner_metadata(
                    normalized.changes,
                    settings=effective_settings,
                )
            else:
                edit_kwargs = _planner_selection_kwargs(request)
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

            sync = client.sync_draft_workflow(
                request.app_id,
                graph=graph,
                features=draft.features,
                hash=draft.hash,
                environment_variables=draft.environment_variables,
                conversation_variables=draft.conversation_variables,
            )
            response["new_hash"] = sync.hash
            response["sync"] = asdict(sync)
            return response
    except HTTPException:
        raise
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
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    except DifyClientError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc


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
) -> tuple[dict, dict]:
    dsl = compiler.compile(plan)
    graph = compile_plan_to_dify_graph(plan, compiler=compiler, base_graph=base_graph)
    issues = [*validate_plan(plan), *validate_dsl(dsl, expected_dsl_version=version_info.app_dsl_version)]
    changes = diff_plans(before_plan, plan)
    guard = guard_plan_change(before_plan, plan, changes)
    explanation = explain_plan(plan)
    explanation["changes"] = [change["message"] for change in changes]
    explanation["preserved"] = _preserved_node_summary(before_plan, plan, changes)

    response = {
        "app_id": app_id,
        "workflow_url": settings.workflow_url(app_id),
        "base_hash": draft_hash,
        "app": _app_payload(app_detail),
        "raw_plan": raw_plan,
        "before_plan": before_plan.model_dump(),
        "plan": plan.model_dump(),
        "changes": changes,
        "explanation": explanation,
        "planner": planner_metadata,
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


def _planner_selection_kwargs(request) -> dict:
    kwargs: dict = {}
    tool_selections = _tool_selection_payloads(getattr(request, "tool_selections", None))
    agent_selections = _agent_selection_payloads(getattr(request, "agent_selections", None))
    if tool_selections:
        kwargs["tool_selections"] = tool_selections
    if agent_selections:
        kwargs["agent_selections"] = agent_selections
    return kwargs


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
    if any(keyword in text for keyword in ("智能体", "agent strategy", "agent策略", "agent 节点", "agent节点")):
        return True
    if any(keyword in text for keyword in ("自主规划", "多步执行")):
        return True
    return bool(re.search(r"\bagent\b", text))


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
    settings: Settings | None = None,
) -> dict:
    metadata = {
        "mode": "preview-plan",
        "attempts": 0,
        "used_fallback": False,
        "repaired": False,
        "replanned": False,
        "normalizations": normalizations or [],
        "errors": [],
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


def _app_payload(app_detail: DifyAppDetail | None) -> dict | None:
    if app_detail is None:
        return None
    return {
        "id": app_detail.id,
        "name": app_detail.name,
        "mode": app_detail.mode,
        "description": app_detail.description,
    }
