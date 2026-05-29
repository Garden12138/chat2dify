from __future__ import annotations

from contextlib import asynccontextmanager
from dataclasses import asdict
from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from app.agent.diff import diff_plans
from app.agent.editor import WorkflowEditPlanner
from app.agent.explainer import explain_plan
from app.agent.guard import guard_plan_change
from app.agent.planner import PlannerError, WorkflowPlanner
from app.compiler.dify import DifyDslCompiler
from app.config import ConfigurationError, load_settings
from app.dify.client import DifyAppDetail, DifyClient, DifyClientError, DifyConflictError
from app.dify.graph import (
    DifyGraphAdapterError,
    UnsupportedExistingNodeType,
    compile_plan_to_dify_graph,
    decompile_dify_graph,
)
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
    return {
        "status": "ok",
        "dify": {
            "source_dir": settings.dify_source_dir,
            "resolved_source_dir": str(settings.dify_source_path),
            "git_describe": version_info.git_describe,
            "app_dsl_version": version_info.app_dsl_version,
        },
    }


@app.post("/api/workflows/draft")
def draft_workflow(request: WorkflowRequest) -> dict:
    settings = load_settings()
    version_info = read_dify_version_info(settings.dify_source_path)
    try:
        planner_result = WorkflowPlanner(settings).generate(
            request.message,
            app_name=request.app_name,
            dsl_version=version_info.app_dsl_version,
        )
    except PlannerError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    plan = planner_result.plan
    compiler = DifyDslCompiler(
        dsl_version=version_info.app_dsl_version,
        default_model_provider=settings.dify_default_model_provider,
        default_model_name=settings.dify_default_model_name,
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
    version_info = read_dify_version_info(settings.dify_source_path)
    compiler = DifyDslCompiler(
        dsl_version=version_info.app_dsl_version,
        default_model_provider=settings.dify_default_model_provider,
        default_model_name=settings.dify_default_model_name,
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
                plan = request.plan
                raw_plan = plan.model_dump()
                planner_metadata = _preview_plan_planner_metadata()
            else:
                edit_result = WorkflowEditPlanner(settings).generate(
                    request.message,
                    current_plan=before_plan,
                    dsl_version=version_info.app_dsl_version,
                )
                plan = edit_result.plan
                raw_plan = edit_result.raw_plan
                planner_metadata = edit_result.metadata()

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
            if response["guard"]["no_op"]:
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


def _preview_plan_planner_metadata() -> dict:
    return {
        "mode": "preview-plan",
        "attempts": 0,
        "used_fallback": False,
        "repaired": False,
        "replanned": False,
        "normalizations": [],
        "errors": [],
    }


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
