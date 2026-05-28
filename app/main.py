from __future__ import annotations

from contextlib import asynccontextmanager
from dataclasses import asdict

from fastapi import FastAPI, HTTPException

from app.agent.explainer import explain_plan
from app.agent.planner import PlannerError, WorkflowPlanner
from app.compiler.dify import DifyDslCompiler
from app.config import ConfigurationError, load_settings
from app.dify.client import DifyClient, DifyClientError
from app.dify.version import read_dify_version_info
from app.models import WorkflowRequest
from app.validator import has_errors, validate_dsl, validate_plan


@asynccontextmanager
async def lifespan(_: FastAPI):
    settings = load_settings()
    read_dify_version_info(settings.dify_source_path)
    yield


app = FastAPI(title="chat2dify", version="0.1.0", lifespan=lifespan)


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
