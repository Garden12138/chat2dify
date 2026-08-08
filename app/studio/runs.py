from __future__ import annotations

from collections import defaultdict
from contextlib import AbstractContextManager
from datetime import datetime, timezone
import re
from statistics import mean
from typing import Any, Callable, Protocol

from app.agent.diff import diff_plans
from app.agent.store import AgentStore
from app.agent.trace import redact_sensitive_data
from app.dify.client import (
    DifyWorkflowNodeExecution,
    DifyWorkflowRunDetail,
    DifyWorkflowRunSummary,
)
from app.models import WorkflowPlan
from app.studio.artifacts import assert_secret_free, canonical_hash
from app.studio.build import StudioBuildService
from app.studio.identity import AuthenticatedStudioRequest
from app.studio.models import (
    ExecutionNodeSummary,
    ExecutionObservationRecord,
    ExecutionRefreshResult,
    ReleaseEnvironment,
    ReleaseRecord,
    RepairProposal,
    RunCenterErrorCluster,
    RunCenterPathMetric,
    RunCenterTrendPoint,
    RunCenterView,
    RunIncident,
    RunIncidentDetail,
    WorkflowArtifact,
    new_id,
    utc_now,
)
from app.studio.store import StudioAccessDenied, StudioConflict, StudioStore


class RunClient(Protocol):
    def list_workflow_runs(
        self,
        app_id: str,
        *,
        status: str | None = None,
        triggered_from: str = "app-run",
        limit: int = 100,
    ) -> list[DifyWorkflowRunSummary]: ...

    def get_workflow_run(
        self,
        app_id: str,
        run_id: str,
    ) -> DifyWorkflowRunDetail: ...

    def list_workflow_node_executions(
        self,
        app_id: str,
        run_id: str,
    ) -> list[DifyWorkflowNodeExecution]: ...


class RunCenterError(RuntimeError):
    code = "STUDIO_RUN_CENTER_ERROR"


class RunObservationBlocked(RunCenterError):
    code = "STUDIO_RUN_OBSERVATION_BLOCKED"


class RepairProposalBlocked(RunCenterError):
    code = "STUDIO_REPAIR_PROPOSAL_BLOCKED"


_SECRET_TEXT_PATTERNS = (
    re.compile(r"\bsk-[A-Za-z0-9_-]{8,}\b"),
    re.compile(r"(?i)\bBearer\s+[A-Za-z0-9._~+/=-]+"),
    re.compile(
        r"(?i)\b(api[_-]?key|password|client[_-]?secret|access[_-]?token)"
        r"\s*[:=]\s*[^\s,;]+"
    ),
)

_KNOWN_ERRORS: dict[str, tuple[str, str, str]] = {
    "EXECUTION_VARIABLE_REFERENCE_INVALID": (
        "变量引用失效",
        "已发布路径中的节点引用了当前执行中不存在或不可达的变量。",
        "创建修复方案，检查受影响节点的输入选择器，并用 Scenario 覆盖该路径。",
    ),
    "EXECUTION_HTTP_FAILED": (
        "外部请求失败",
        "工作流的 HTTP 路径返回错误或无法完成。",
        "检查脱敏后的状态与受影响节点，再通过修复方案评估超时、失败分支或重试策略。",
    ),
    "EXECUTION_TOOL_FAILED": (
        "工具调用失败",
        "工作流工具没有完成预期调用。",
        "确认工具可用性和输入结构，通过正常 Build 与 Scenario 验证修复。",
    ),
    "EXECUTION_MODEL_FAILED": (
        "模型调用失败",
        "模型节点没有完成本次执行。",
        "检查模型可用性和配额证据；需要改动时创建修复方案并重新走发布流程。",
    ),
    "EXECUTION_TIMEOUT": (
        "执行超时",
        "执行路径超过了可用时间窗口。",
        "定位最慢路径，并通过 Scenario 比较修复前后的时延与成本。",
    ),
    "EXECUTION_CANCELLED": (
        "执行已停止",
        "本次执行在完成前被停止。",
        "确认停止来源；只有可复现的产品缺陷才需要创建修复方案。",
    ),
    "EXECUTION_ERROR_UNKNOWN": (
        "执行失败",
        "Dify 返回了失败结果，但现有证据不足以确定更具体的稳定分类。",
        "检查脱敏节点路径和缺失证据，补充可复现 Scenario 后再提出修改。",
    ),
}


class StudioRunService:
    def __init__(
        self,
        *,
        store: StudioStore,
        build_service: StudioBuildService,
        agent_store: AgentStore,
        client_factory: Callable[[], AbstractContextManager[RunClient]],
        token_cost_microusd_per_1k: int = 5_000,
    ) -> None:
        self.store = store
        self.build_service = build_service
        self.agent_store = agent_store
        self.client_factory = client_factory
        self.token_cost_microusd_per_1k = max(0, token_cost_microusd_per_1k)

    def center(
        self,
        authenticated: AuthenticatedStudioRequest,
        *,
        project_id: str,
        logical_app_id: str | None = None,
        environment_id: str | None = None,
        artifact_id: str | None = None,
        status: str | None = None,
        error_code: str | None = None,
        started_from: datetime | None = None,
        started_to: datetime | None = None,
    ) -> RunCenterView:
        project, membership = self.store.get_project_for_principal(
            project_id,
            authenticated.principal.key,
        )
        executions = self.store.list_execution_observations(
            project_id=project_id,
            principal_key=authenticated.principal.key,
            logical_app_id=logical_app_id,
            environment_id=environment_id,
            artifact_id=artifact_id,
            status=status,
            error_code=error_code,
            started_from=started_from,
            started_to=started_to,
        )
        incidents = self.store.list_run_incidents(
            project_id=project_id,
            principal_key=authenticated.principal.key,
        )
        visible_execution_ids = {item.id for item in executions}
        incidents = [
            item for item in incidents if item.execution_id in visible_execution_ids
        ]
        repairs = self.store.list_repair_proposals(
            project_id=project_id,
            principal_key=authenticated.principal.key,
        )
        logical_apps = self.store.list_logical_apps(
            project_id=project_id,
            principal_key=authenticated.principal.key,
        )
        environments = self.store.list_release_environments(
            project_id=project_id,
            principal_key=authenticated.principal.key,
        )
        missing = _missing_evidence(executions)
        if not authenticated.host.apps_available:
            state = "partial_error"
            message = (
                "已保存的脱敏运行证据仍可查看，但当前无法验证 Dify App 列表，"
                "因此刷新执行被禁用。"
            )
        elif executions:
            state = "ready"
            message = "运行证据已按发布版本和稳定错误分类汇总。"
        else:
            state = "empty"
            message = "尚无已观测的生产执行；刷新一个已配置环境以开始。"
        can_write = membership.role in {"owner", "admin", "builder"}
        return RunCenterView(
            project=project,
            membership=membership,
            logical_apps=logical_apps,
            environments=environments,
            executions=executions,
            incidents=incidents,
            repairs=repairs,
            trend=_trend(executions),
            release_overlays=_release_overlays(
                self.store.list_release_records(
                    project_id=project_id,
                    principal_key=authenticated.principal.key,
                )
            ),
            regressions=_regressions(executions),
            error_clusters=_error_clusters(executions),
            slow_paths=_path_metrics(executions, costly=False),
            costly_paths=_path_metrics(executions, costly=True),
            missing_evidence=missing,
            can_refresh=can_write and authenticated.host.apps_available,
            can_create_repair=can_write,
            state=state,
            message=message,
        )

    def refresh(
        self,
        authenticated: AuthenticatedStudioRequest,
        *,
        project_id: str,
        environment_id: str | None = None,
        limit_per_environment: int = 100,
    ) -> ExecutionRefreshResult:
        project, membership = self.store.get_project_for_principal(
            project_id,
            authenticated.principal.key,
        )
        if membership.role not in {"owner", "admin", "builder"}:
            raise StudioAccessDenied("Your project role cannot refresh Run evidence.")
        if not authenticated.host.apps_available:
            raise RunObservationBlocked(
                "The verified Dify App list is unavailable; Run refresh is disabled."
            )
        environments = self.store.list_release_environments(
            project_id=project_id,
            principal_key=authenticated.principal.key,
        )
        if environment_id is not None:
            environments = [item for item in environments if item.id == environment_id]
            if not environments:
                raise RunObservationBlocked("The selected Environment does not exist.")
        environments = [item for item in environments if item.enabled]
        visible_ids = {item.id for item in authenticated.host.apps}
        if project.kind != "personal":
            visible_ids &= self.store.list_project_app_ids(
                project_id,
                authenticated.principal.key,
            )
        scanned = observed = opened = uncorrelated = 0
        errors: list[dict[str, str]] = []
        with self.client_factory() as client:
            for environment in environments:
                if environment.target_app_ref not in visible_ids:
                    errors.append(
                        {
                            "environment_id": environment.id,
                            "code": "STUDIO_RUN_TARGET_NOT_VISIBLE",
                            "message": "当前可信 Dify 身份无法读取该环境 App。",
                        }
                    )
                    continue
                scanned += 1
                try:
                    summaries = client.list_workflow_runs(
                        environment.target_app_ref,
                        triggered_from="app-run",
                        limit=limit_per_environment,
                    )
                    releases = self.store.list_release_records(
                        project_id=project_id,
                        principal_key=authenticated.principal.key,
                        environment_id=environment.id,
                    )
                    for summary in summaries:
                        detail = client.get_workflow_run(
                            environment.target_app_ref,
                            summary.id,
                        )
                        nodes = client.list_workflow_node_executions(
                            environment.target_app_ref,
                            summary.id,
                        )
                        item = self._normalize(
                            project_id=project_id,
                            environment=environment,
                            detail=detail,
                            nodes=nodes,
                            releases=releases,
                        )
                        stored, _ = self.store.upsert_execution_observation(
                            item=item,
                            principal_key=authenticated.principal.key,
                        )
                        observed += 1
                        if stored.correlation_state != "exact":
                            uncorrelated += 1
                        if stored.status in {"failed", "partial_succeeded"}:
                            incident = _incident_from_execution(stored, environment)
                            _, created = self.store.upsert_run_incident(
                                item=incident,
                                principal_key=authenticated.principal.key,
                            )
                            opened += int(created)
                except Exception as exc:
                    errors.append(
                        {
                            "environment_id": environment.id,
                            "code": getattr(exc, "code", "STUDIO_RUN_REFRESH_FAILED"),
                            "message": _safe_text(str(exc), max_length=500),
                        }
                    )
        return ExecutionRefreshResult(
            environments_scanned=scanned,
            executions_observed=observed,
            incidents_opened=opened,
            uncorrelated=uncorrelated,
            errors=errors,
        )

    def incident(
        self,
        authenticated: AuthenticatedStudioRequest,
        *,
        project_id: str,
        incident_id: str,
    ) -> RunIncidentDetail:
        _, membership = self.store.get_project_for_principal(
            project_id,
            authenticated.principal.key,
        )
        incident = self.store.get_run_incident(
            incident_id,
            project_id=project_id,
            principal_key=authenticated.principal.key,
        )
        execution = self.store.get_execution_observation(
            incident.execution_id,
            project_id=project_id,
            principal_key=authenticated.principal.key,
        )
        artifact: WorkflowArtifact | None = None
        release: ReleaseRecord | None = None
        if execution.artifact_id:
            artifact = self.store.get_workflow_artifact(
                execution.artifact_id,
                project_id=project_id,
                principal_key=authenticated.principal.key,
            )
        if execution.release_record_id:
            release = self.store.get_release_record(
                execution.release_record_id,
                project_id=project_id,
                principal_key=authenticated.principal.key,
            )
        repairs = self.store.list_repair_proposals(
            project_id=project_id,
            principal_key=authenticated.principal.key,
        )
        repair = next(
            (item for item in repairs if item.incident_id == incident.id),
            None,
        )
        return RunIncidentDetail(
            incident=incident,
            execution=execution,
            artifact_summary=_artifact_summary(artifact),
            release_summary=_release_summary(release),
            release_diff=self._release_diff(
                artifact,
                project_id=project_id,
                principal_key=authenticated.principal.key,
            ),
            scenario_coverage=(
                _scenario_coverage(artifact) if artifact is not None else {}
            ),
            affected_path=_affected_path(execution, artifact),
            known_error=_known_error(incident.stable_error_code),
            repair=repair,
            can_create_repair=(
                membership.role in {"owner", "admin", "builder"}
                and execution.correlation_state == "exact"
                and execution.artifact_id is not None
                and execution.release_record_id is not None
            ),
        )

    def create_repair(
        self,
        authenticated: AuthenticatedStudioRequest,
        *,
        project_id: str,
        incident_id: str,
        title: str | None = None,
    ) -> RepairProposal:
        detail = self.incident(
            authenticated,
            project_id=project_id,
            incident_id=incident_id,
        )
        if detail.repair is not None:
            return detail.repair
        if not detail.can_create_repair:
            raise RepairProposalBlocked(
                "Repair requires an execution correlated to an exact released Artifact."
            )
        execution = detail.execution
        environment = self.store.get_release_environment(
            execution.environment_id,
            project_id=project_id,
            principal_key=authenticated.principal.key,
        )
        logical_app = self.store.get_logical_app(
            execution.logical_app_id,
            project_id=project_id,
            principal_key=authenticated.principal.key,
        )
        build = self.build_service.create(
            authenticated,
            project_id=project_id,
            operation="modify",
            entry_source="home",
            app_id=environment.target_app_ref,
            app_mode=logical_app.app_mode,
            app_name=logical_app.name,
        )
        evidence = {
            "execution_id": execution.id,
            "dify_execution_id": execution.dify_execution_id,
            "artifact_id": execution.artifact_id,
            "release_record_id": execution.release_record_id,
            "stable_error_code": execution.stable_error_code,
            "affected_node_id": execution.failed_node_id,
            "affected_node_type": execution.failed_node_type,
            "safe_message": execution.safe_message,
            "input_shape": execution.input_shape,
            "output_shape": execution.output_shape,
            "evidence_hash": execution.evidence_hash,
            "required_flow": [
                "build",
                "scenario",
                "review",
                "apply_draft",
                "explicit_publish",
            ],
            "external_write": False,
        }
        assert_secret_free(evidence)
        now = utc_now()
        proposal = RepairProposal(
            id=new_id(),
            project_id=project_id,
            incident_id=incident_id,
            execution_id=execution.id,
            source_artifact_id=execution.artifact_id,
            source_release_record_id=execution.release_record_id,
            build_id=build.id,
            title=(title or f"修复：{detail.incident.title}").strip(),
            business_summary=(
                f"基于 {detail.incident.stable_error_code} 的脱敏生产证据创建。"
                "请先在 Build Studio 检查受影响路径并仅提出 Typed Patch；"
                "之后必须重新运行 Scenario、Review、Apply Draft 和显式 Publish。"
            ),
            evidence=evidence,
            evidence_hash=canonical_hash(evidence),
            status="draft_build",
            created_by=authenticated.principal.key,
            version=1,
            created_at=now,
            updated_at=now,
        )
        stored, _ = self.store.create_repair_proposal(
            item=proposal,
            principal_key=authenticated.principal.key,
        )
        return stored

    def _normalize(
        self,
        *,
        project_id: str,
        environment: ReleaseEnvironment,
        detail: DifyWorkflowRunDetail,
        nodes: list[DifyWorkflowNodeExecution],
        releases: list[ReleaseRecord],
    ) -> ExecutionObservationRecord:
        matching = [
            item
            for item in releases
            if item.action == "publish"
            and item.outcome == "succeeded"
            and str((item.details.get("published_workflow") or {}).get("version") or "")
            == detail.version
        ]
        if len(matching) == 1 and detail.version:
            correlation_state = "exact"
            correlation_reason = "Dify 执行版本与唯一成功 Publish Receipt 精确匹配。"
            release = matching[0]
        elif len(matching) > 1:
            correlation_state = "ambiguous"
            correlation_reason = "多个 Publish Receipt 声明了同一 Dify 执行版本。"
            release = None
        elif detail.version:
            correlation_state = "uncorrelated"
            correlation_reason = "没有成功 Publish Receipt 与该 Dify 执行版本匹配。"
            release = None
        else:
            correlation_state = "unsupported"
            correlation_reason = "Dify 没有返回可用于 Artifact 关联的执行版本。"
            release = None
        failed = next(
            (
                item
                for item in reversed(nodes)
                if item.status.lower()
                in {"failed", "error", "exception", "partial-succeeded"}
            ),
            None,
        )
        raw_error = (failed.error if failed else None) or detail.error
        stable_error = (
            _classify_error(detail.status, raw_error or "")
            if _normalize_status(detail.status) not in {"succeeded", "running"}
            else None
        )
        safe_message = _safe_error_message(raw_error, detail.inputs)
        node_path = [
            ExecutionNodeSummary(
                node_id=item.node_id,
                predecessor_node_id=item.predecessor_node_id,
                node_type=_safe_text(item.node_type, max_length=128) or None,
                title=_safe_text(item.title, max_length=256) or None,
                status=_safe_text(item.status, max_length=64) or "unknown",
                stable_error_code=(
                    _classify_error(item.status, item.error or "")
                    if item.error or item.status.lower() in {"failed", "error"}
                    else None
                ),
                elapsed_ms=(
                    max(0, round(item.elapsed_time * 1_000))
                    if item.elapsed_time is not None
                    else None
                ),
            )
            for item in nodes[:500]
        ]
        status = _normalize_status(detail.status)
        total_tokens = detail.total_tokens
        cost = (
            round(total_tokens * self.token_cost_microusd_per_1k / 1_000)
            if total_tokens is not None
            else None
        )
        evidence = {
            "dify_execution_id": detail.id,
            "dify_workflow_version": detail.version,
            "status": status,
            "correlation_state": correlation_state,
            "artifact_id": release.artifact_id if release else None,
            "release_record_id": release.id if release else None,
            "failed_node_id": failed.node_id if failed else None,
            "failed_node_type": failed.node_type if failed else None,
            "stable_error_code": stable_error,
            "safe_message": safe_message,
            "latency_ms": (
                max(0, round(detail.elapsed_time * 1_000))
                if detail.elapsed_time is not None
                else None
            ),
            "total_tokens": total_tokens,
            "total_steps": detail.total_steps,
            "input_shape": _value_shape(detail.inputs),
            "output_shape": _value_shape(detail.outputs),
            "node_path": [item.model_dump(mode="json") for item in node_path],
        }
        assert_secret_free(evidence)
        now = utc_now()
        return ExecutionObservationRecord(
            id=new_id(),
            project_id=project_id,
            logical_app_id=environment.logical_app_id,
            environment_id=environment.id,
            artifact_id=release.artifact_id if release else None,
            release_record_id=release.id if release else None,
            dify_app_id=environment.target_app_ref,
            dify_execution_id=detail.id,
            dify_workflow_version=detail.version,
            status=status,
            correlation_state=correlation_state,
            correlation_reason=correlation_reason,
            failed_node_id=failed.node_id if failed else None,
            failed_node_type=failed.node_type if failed else None,
            stable_error_code=stable_error,
            safe_message=safe_message,
            latency_ms=evidence["latency_ms"],
            total_tokens=total_tokens,
            estimated_cost_microusd=cost,
            total_steps=detail.total_steps,
            input_shape=evidence["input_shape"],
            output_shape=evidence["output_shape"],
            node_path=node_path,
            evidence_hash=canonical_hash(evidence),
            started_at=_from_timestamp(detail.created_at),
            finished_at=_from_timestamp(detail.finished_at),
            observed_at=now,
            updated_at=now,
        )

    def _release_diff(
        self,
        artifact: WorkflowArtifact | None,
        *,
        project_id: str,
        principal_key: str,
    ) -> list[dict[str, Any]]:
        if artifact is None:
            return []
        try:
            candidate = self.store.get_candidate_for_project(
                artifact.candidate_id,
                project_id=project_id,
                principal_key=principal_key,
            )
            versions = self.agent_store.list_workspace_versions(candidate.run_id)
            if not versions:
                return []
            selected = next(
                (
                    item
                    for item in versions
                    if item.id == artifact.candidate_workspace_version_id
                ),
                None,
            )
            if selected is None:
                return []
            changes = diff_plans(
                WorkflowPlan.model_validate(versions[0].snapshot),
                WorkflowPlan.model_validate(selected.snapshot),
            )
            return [
                {
                    key: value
                    for key, value in change.items()
                    if key
                    in {
                        "type",
                        "target",
                        "node_type",
                        "title",
                        "field",
                        "message",
                        "source",
                        "target_node",
                    }
                }
                for change in changes
            ]
        except Exception:
            return []


def _normalize_status(value: str) -> str:
    normalized = value.strip().lower().replace("-", "_")
    if normalized in {
        "running",
        "succeeded",
        "failed",
        "stopped",
        "partial_succeeded",
    }:
        return normalized
    if normalized in {"cancelled", "canceled"}:
        return "stopped"
    return "unknown"


def _classify_error(status: str, message: str) -> str:
    text = f"{status} {message}".casefold()
    if any(
        token in text
        for token in (
            "variable reference",
            "variable selector",
            "unknown variable",
            "not found variable",
            "变量引用",
            "变量不存在",
        )
    ):
        return "EXECUTION_VARIABLE_REFERENCE_INVALID"
    if "timeout" in text or "timed out" in text or "超时" in text:
        return "EXECUTION_TIMEOUT"
    if any(token in text for token in ("http", "status code", "connection refused")):
        return "EXECUTION_HTTP_FAILED"
    if any(token in text for token in ("tool", "plugin", "工具", "插件")):
        return "EXECUTION_TOOL_FAILED"
    if any(token in text for token in ("model", "llm", "模型", "quota")):
        return "EXECUTION_MODEL_FAILED"
    if any(token in text for token in ("stopped", "cancelled", "canceled", "停止")):
        return "EXECUTION_CANCELLED"
    return "EXECUTION_ERROR_UNKNOWN"


def _safe_error_message(message: str | None, inputs: Any) -> str | None:
    if not message:
        return None
    result = str(redact_sensitive_data({"message": message}).get("message") or "")
    for secret in _scalar_strings(inputs):
        if len(secret) >= 4:
            result = result.replace(secret, "[REDACTED_INPUT]")
    for pattern in _SECRET_TEXT_PATTERNS:
        result = pattern.sub("[REDACTED]", result)
    return _safe_text(result, max_length=1_000) or None


def _scalar_strings(value: Any, *, limit: int = 100) -> list[str]:
    found: list[str] = []

    def visit(item: Any) -> None:
        if len(found) >= limit:
            return
        if isinstance(item, dict):
            for child in item.values():
                visit(child)
        elif isinstance(item, (list, tuple)):
            for child in item:
                visit(child)
        elif isinstance(item, str):
            found.append(item)

    visit(value)
    return found


def _value_shape(value: Any) -> dict[str, str]:
    if isinstance(value, dict):
        return {
            _safe_shape_key(str(key)): _shape_name(item)
            for key, item in list(value.items())[:100]
            if _safe_shape_key(str(key))
        }
    return {"value": _shape_name(value)} if value is not None else {}


def _shape_name(value: Any) -> str:
    if value is None:
        return "null"
    if isinstance(value, bool):
        return "boolean"
    if isinstance(value, int) and not isinstance(value, bool):
        return "integer"
    if isinstance(value, float):
        return "number"
    if isinstance(value, str):
        return "text"
    if isinstance(value, list):
        return f"list[{len(value)}]"
    if isinstance(value, dict):
        return f"object[{len(value)}]"
    return "value"


def _safe_shape_key(value: str) -> str:
    normalized = value.strip().lower().replace("-", "_")
    if any(
        marker in normalized
        for marker in (
            "api_key",
            "apikey",
            "authorization",
            "cookie",
            "credential",
            "password",
            "private_key",
            "secret",
            "access_token",
            "refresh_token",
        )
    ):
        return "[sensitive_field]"
    return _safe_text(value, max_length=128)


def _safe_text(value: str | None, *, max_length: int) -> str:
    if value is None:
        return ""
    result = str(redact_sensitive_data(value)).strip()
    for pattern in _SECRET_TEXT_PATTERNS:
        result = pattern.sub("[REDACTED]", result)
    return result[:max_length]


def _from_timestamp(value: int | None) -> datetime | None:
    if value is None:
        return None
    return datetime.fromtimestamp(value, tz=timezone.utc)


def _incident_from_execution(
    execution: ExecutionObservationRecord,
    environment: ReleaseEnvironment,
) -> RunIncident:
    code = execution.stable_error_code or "EXECUTION_ERROR_UNKNOWN"
    known = _known_error(code)
    affected = next(
        (
            item
            for item in execution.node_path
            if item.node_id == execution.failed_node_id
        ),
        None,
    )
    now = execution.finished_at or execution.observed_at
    severity = "critical" if environment.classification == "production" else "warning"
    return RunIncident(
        id=new_id(),
        project_id=execution.project_id,
        execution_id=execution.id,
        cluster_key=f"{code}:{execution.failed_node_id or 'unknown'}",
        title=known["title"],
        severity=severity,
        status="open",
        stable_error_code=code,
        affected_node_id=execution.failed_node_id,
        affected_node_title=affected.title if affected else None,
        business_cause=known["cause"],
        next_step=known["next_step"],
        first_seen_at=now,
        last_seen_at=now,
        version=1,
    )


def _known_error(code: str) -> dict[str, str]:
    title, cause, next_step = _KNOWN_ERRORS.get(
        code,
        _KNOWN_ERRORS["EXECUTION_ERROR_UNKNOWN"],
    )
    return {
        "code": code,
        "title": title,
        "cause": cause,
        "next_step": next_step,
    }


def _trend(executions: list[ExecutionObservationRecord]) -> list[RunCenterTrendPoint]:
    buckets: dict[str, dict[str, int]] = defaultdict(
        lambda: {"succeeded": 0, "failed": 0, "other": 0}
    )
    for item in executions:
        when = item.started_at or item.observed_at
        bucket = when.astimezone(timezone.utc).date().isoformat()
        if item.status == "succeeded":
            buckets[bucket]["succeeded"] += 1
        elif item.status in {"failed", "partial_succeeded"}:
            buckets[bucket]["failed"] += 1
        else:
            buckets[bucket]["other"] += 1
    return [
        RunCenterTrendPoint(bucket=key, **buckets[key])
        for key in sorted(buckets)
    ]


def _release_overlays(releases: list[ReleaseRecord]) -> list[dict[str, Any]]:
    return [
        {
            "release_record_id": item.id,
            "artifact_id": item.artifact_id,
            "environment_id": item.environment_id,
            "action": item.action,
            "outcome": item.outcome,
            "at": (item.completed_at or item.created_at).isoformat(),
            "dify_workflow_version": str(
                (item.details.get("published_workflow") or {}).get("version") or ""
            ),
        }
        for item in releases
        if item.outcome == "succeeded"
    ]


def _regressions(
    executions: list[ExecutionObservationRecord],
) -> list[dict[str, Any]]:
    grouped: dict[str, list[ExecutionObservationRecord]] = defaultdict(list)
    for item in executions:
        if item.artifact_id:
            grouped[item.artifact_id].append(item)
    results: list[dict[str, Any]] = []
    for artifact_id, items in sorted(grouped.items()):
        failed = sum(item.status in {"failed", "partial_succeeded"} for item in items)
        if failed:
            results.append(
                {
                    "artifact_id": artifact_id,
                    "executions": len(items),
                    "failures": failed,
                    "failure_rate": round(failed / len(items), 4),
                    "message": "生产失败需要与该 Artifact 的 Scenario 覆盖一起检查。",
                }
            )
    return results


def _error_clusters(
    executions: list[ExecutionObservationRecord],
) -> list[RunCenterErrorCluster]:
    groups: dict[tuple[str, str | None], list[ExecutionObservationRecord]] = defaultdict(list)
    for item in executions:
        if item.stable_error_code:
            groups[(item.stable_error_code, item.failed_node_id)].append(item)
    return sorted(
        [
            RunCenterErrorCluster(
                key=f"{code}:{node_id or 'unknown'}",
                stable_error_code=code,
                failed_node_id=node_id,
                count=len(items),
                latest_at=max(item.finished_at or item.observed_at for item in items),
            )
            for (code, node_id), items in groups.items()
        ],
        key=lambda item: (-item.count, item.key),
    )


def _path_metrics(
    executions: list[ExecutionObservationRecord],
    *,
    costly: bool,
) -> list[RunCenterPathMetric]:
    groups: dict[tuple[str | None, str], list[ExecutionObservationRecord]] = defaultdict(list)
    for item in executions:
        marker = next(
            (
                node
                for node in reversed(item.node_path)
                if node.node_id == item.failed_node_id
            ),
            item.node_path[-1] if item.node_path else None,
        )
        groups[
            (
                marker.node_id if marker else None,
                marker.title if marker and marker.title else "完整执行路径",
            )
        ].append(item)
    values = [
        RunCenterPathMetric(
            node_id=node_id,
            title=title,
            executions=len(items),
            average_latency_ms=(
                round(mean(value.latency_ms for value in items if value.latency_ms is not None))
                if any(value.latency_ms is not None for value in items)
                else None
            ),
            total_tokens=(
                sum(value.total_tokens or 0 for value in items)
                if any(value.total_tokens is not None for value in items)
                else None
            ),
            estimated_cost_microusd=(
                sum(value.estimated_cost_microusd or 0 for value in items)
                if any(value.estimated_cost_microusd is not None for value in items)
                else None
            ),
        )
        for (node_id, title), items in groups.items()
    ]
    if costly:
        return sorted(
            values,
            key=lambda item: (
                item.estimated_cost_microusd is None,
                -(item.estimated_cost_microusd or 0),
                item.title,
            ),
        )[:10]
    return sorted(
        values,
        key=lambda item: (
            item.average_latency_ms is None,
            -(item.average_latency_ms or 0),
            item.title,
        ),
    )[:10]


def _missing_evidence(executions: list[ExecutionObservationRecord]) -> list[str]:
    missing: list[str] = []
    if any(item.correlation_state != "exact" for item in executions):
        missing.append("部分执行缺少与 Publish Receipt 的精确版本关联。")
    if any(item.latency_ms is None for item in executions):
        missing.append("部分执行没有权威时延。")
    if any(item.total_tokens is None for item in executions):
        missing.append("部分执行没有模型 Token 用量，成本汇总不完整。")
    return missing


def _artifact_summary(artifact: WorkflowArtifact | None) -> dict[str, Any] | None:
    if artifact is None:
        return None
    plan = artifact.payload.plan
    return {
        "artifact_id": artifact.id,
        "content_hash": artifact.content_hash,
        "app_mode": artifact.payload.app_mode,
        "node_count": len(plan.get("nodes") or []),
        "edge_count": len(plan.get("edges") or []),
        "capabilities": artifact.payload.capability_requirements,
        "created_at": artifact.created_at.isoformat(),
    }


def _release_summary(release: ReleaseRecord | None) -> dict[str, Any] | None:
    if release is None:
        return None
    return {
        "release_record_id": release.id,
        "action": release.action,
        "outcome": release.outcome,
        "environment_id": release.environment_id,
        "release_note": _safe_text(release.release_note, max_length=2_000),
        "published_workflow": release.details.get("published_workflow") or {},
        "completed_at": (
            release.completed_at.isoformat() if release.completed_at else None
        ),
    }


def _scenario_coverage(artifact: WorkflowArtifact) -> dict[str, Any]:
    evidence = artifact.payload.scenario_evidence
    return {
        "scenario_run_id": evidence.get("scenario_run_id"),
        "pass_rate": evidence.get("pass_rate"),
        "quality_score": evidence.get("quality_score"),
        "cleanup_verified": evidence.get("cleanup_verified"),
        "failure_clusters": evidence.get("failure_clusters") or [],
        "binding_hash": (evidence.get("binding") or {}).get("binding_hash"),
    }


def _affected_path(
    execution: ExecutionObservationRecord,
    artifact: WorkflowArtifact | None,
) -> list[dict[str, Any]]:
    if artifact is None or not execution.failed_node_id:
        return [
            {
                "node_id": item.node_id,
                "title": item.title,
                "node_type": item.node_type,
                "status": item.status,
            }
            for item in execution.node_path
        ]
    plan = artifact.payload.plan
    nodes = {
        str(item.get("id")): item
        for item in plan.get("nodes") or []
        if isinstance(item, dict) and item.get("id")
    }
    predecessors: dict[str, list[str]] = defaultdict(list)
    for edge in plan.get("edges") or []:
        if isinstance(edge, dict) and edge.get("source") and edge.get("target"):
            predecessors[str(edge["target"])].append(str(edge["source"]))
    path_ids: list[str] = []
    current = execution.failed_node_id
    visited: set[str] = set()
    while current and current not in visited and len(path_ids) < 30:
        visited.add(current)
        path_ids.append(current)
        choices = sorted(predecessors.get(current) or [])
        current = choices[0] if choices else ""
    path_ids.reverse()
    return [
        {
            "node_id": node_id,
            "title": _safe_text(
                str(nodes.get(node_id, {}).get("title") or node_id),
                max_length=256,
            ),
            "node_type": _safe_text(
                str(nodes.get(node_id, {}).get("type") or "unknown"),
                max_length=128,
            ),
            "affected": node_id == execution.failed_node_id,
        }
        for node_id in path_ids
    ]
