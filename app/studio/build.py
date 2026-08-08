from __future__ import annotations

from copy import deepcopy
from hashlib import sha256
import json
import re
from typing import Any, Literal

from app.agent.service import AgentApplicationService
from app.agent.state import (
    AgentConfigSnapshot,
    AgentRun,
    AgentWorkflowSnapshot,
    RunConstraints,
    RunPhase,
)
from app.agent.store import AgentRecordNotFound, AgentStore
from app.agent.trace import redact_sensitive_data
from app.models import WorkflowPlan
from app.studio.identity import AuthenticatedStudioRequest
from app.studio.models import (
    BuildStudioView,
    CandidatePresentation,
    StudioBuild,
    StudioCandidate,
)
from app.studio.store import (
    StudioAccessDenied,
    StudioConflict,
    StudioRecordNotFound,
    StudioStore,
)


BuildCommandMode = Literal["explain", "alternatives", "synthesize"]
ContextCommand = Literal[
    "explain_selection",
    "explain_variable_flow",
    "safer_fallback",
    "generate_scenarios",
    "suggest_resources",
]


class StudioBuildService:
    """Project-scoped product facade over the v4 versioned safety core."""

    def __init__(
        self,
        *,
        store: StudioStore,
        agent_store: AgentStore,
        agent_service: AgentApplicationService,
        durable_jobs: bool = False,
    ) -> None:
        self.store = store
        self.agent_store = agent_store
        self.agent_service = agent_service
        self.durable_jobs = durable_jobs

    def create(
        self,
        authenticated: AuthenticatedStudioRequest,
        *,
        project_id: str,
        operation: Literal["create", "modify"],
        entry_source: Literal["home", "canvas", "create"],
        app_id: str | None,
        app_mode: str,
        app_name: str,
    ) -> StudioBuild:
        project, membership = self.store.get_project_for_principal(
            project_id,
            authenticated.principal.key,
        )
        if project.dify_tenant_id != authenticated.principal.dify_tenant_id:
            raise StudioAccessDenied("The Build project is outside the verified Dify Workspace.")
        if membership.role not in {"owner", "admin", "builder"}:
            raise StudioAccessDenied("Your project role cannot create Build candidates.")
        supported_modes = {
            "workflow",
            "advanced-chat",
            "chat",
            "completion",
            "agent-chat",
        }
        if app_mode not in supported_modes:
            raise StudioConflict("Build Studio received an unsupported Dify application mode.")
        if operation == "create" and entry_source != "create":
            raise StudioConflict("New-app Build must use the explicit create entry.")
        if operation == "modify" and entry_source not in {"home", "canvas"}:
            raise StudioConflict("Existing-app Build must originate from Home or Dify canvas.")
        normalized_app_id = (app_id or "").strip() or None
        authoritative_app_name = app_name.strip()
        if operation == "modify":
            if normalized_app_id is None:
                raise StudioConflict("Existing-app Build requires an app ID.")
            app = next(
                (item for item in authenticated.host.apps if item.id == normalized_app_id),
                None,
            )
            if app is None:
                raise StudioAccessDenied(
                    "The current verified Dify account cannot access this application."
                )
            if app.mode != app_mode:
                raise StudioConflict("The Build app mode no longer matches Dify.")
            authoritative_app_name = app.name
            self.store.link_project_app(
                project_id=project_id,
                principal_key=authenticated.principal.key,
                app_id=normalized_app_id,
            )
        elif normalized_app_id is not None:
            raise StudioConflict("New-app Build cannot use an existing app ID.")
        return self.store.create_build(
            project_id=project_id,
            principal_key=authenticated.principal.key,
            operation=operation,
            entry_source=entry_source,
            app_id=normalized_app_id,
            app_mode=app_mode,
            app_name=authoritative_app_name,
        )

    def command(
        self,
        authenticated: AuthenticatedStudioRequest,
        *,
        project_id: str,
        build_id: str,
        mode: BuildCommandMode,
        message: str,
        candidate_count: int = 2,
        source_candidate_ids: list[str] | None = None,
        constraints: RunConstraints | None = None,
    ) -> list[StudioCandidate]:
        build = self._writable_build(authenticated, project_id, build_id)
        if build.status != "active":
            raise StudioConflict("This Build Studio work item is no longer active.")
        if build.operation == "modify" and build.entry_source == "canvas":
            if constraints is None or constraints.canvas_context_revision < 1:
                raise StudioConflict(
                    "Canvas-opened Build requires a verified Dify context handshake."
                )
            if constraints.dirty_state:
                raise StudioConflict(
                    "Save or discard the current Dify canvas changes before building candidates."
                )
            if not constraints.canvas_draft_hash:
                raise StudioConflict(
                    "Dify canvas did not provide a verifiable Draft Hash."
                )
        goal = str(redact_sensitive_data(message)).strip()
        if not goal:
            raise StudioConflict("Build Studio requires a non-empty business goal.")
        if mode == "alternatives":
            if candidate_count < 2 or candidate_count > 3:
                raise StudioConflict("Alternative generation supports two or three candidates.")
            strategies = _candidate_strategies(goal, candidate_count)
            return [
                self._start_candidate(
                    authenticated,
                    build=build,
                    label=label,
                    intent=intent,
                    goal=_alternative_goal(goal, label, intent, index, candidate_count),
                    constraints=constraints,
                )
                for index, (label, intent) in enumerate(strategies, start=1)
            ]
        if mode == "explain":
            return [
                self._start_candidate(
                    authenticated,
                    build=build,
                    label="先解释（无变更）",
                    intent="读取权威状态并解释目标、选区与变量流，不应用 Patch。",
                    goal=(
                        "只解释，不修改。先使用只读 Inspect 能力理解权威 Workspace，"
                        "说明目标、当前路径、假设和风险；不要调用 workflow.patch 或 "
                        f"config.patch。用户目标：{goal}"
                    ),
                    constraints=constraints,
                    read_only=True,
                )
            ]
        source_ids = source_candidate_ids or []
        if len(source_ids) < 2 or len(source_ids) > 3:
            raise StudioConflict("Candidate synthesis requires two or three source candidates.")
        if len(set(source_ids)) != len(source_ids):
            raise StudioConflict("Candidate synthesis sources must be distinct.")
        sources = [
            self.store.get_candidate(
                candidate_id,
                build_id=build.id,
                project_id=project_id,
                principal_key=authenticated.principal.key,
            )
            for candidate_id in source_ids
        ]
        source_evidence: list[dict[str, Any]] = []
        for source in sources:
            presentation = self._present_candidate(build, source)
            if (
                presentation.candidate.status != "valid"
                or not presentation.reconstructable
            ):
                raise StudioConflict("Only valid reconstructable candidates can be synthesized.")
            source_evidence.append(
                {
                    "label": source.label,
                    "business_summary": presentation.business_summary,
                    "risk": presentation.risk,
                    "side_effects": presentation.side_effects,
                }
            )
        evidence = json.dumps(
            redact_sensitive_data(source_evidence),
            ensure_ascii=False,
            separators=(",", ":"),
        )
        return [
            self._start_candidate(
                authenticated,
                build=build,
                label="综合方案",
                intent="基于已比较方案生成新的独立候选，不修改来源候选。",
                goal=(
                    f"{goal}\n基于以下业务级方案证据生成一个新的独立方案：{evidence}。"
                    "必须从原始 Base 开始，仅通过显式 Typed Patch 操作实现；不得复制"
                    "或替换完整 Graph/Config，也不得修改来源候选。"
                ),
                constraints=constraints,
                source_candidate_ids=source_ids,
            )
        ]

    def get(
        self,
        authenticated: AuthenticatedStudioRequest,
        *,
        project_id: str,
        build_id: str,
    ) -> BuildStudioView:
        build = self.store.get_build(
            build_id,
            project_id=project_id,
            principal_key=authenticated.principal.key,
        )
        candidates = self.store.list_candidates(
            build.id,
            project_id=project_id,
            principal_key=authenticated.principal.key,
        )
        presentations: list[CandidatePresentation] = []
        for candidate in candidates:
            presentation = self._present_candidate(build, candidate)
            presentations.append(presentation)
        build = self.store.get_build(
            build_id,
            project_id=project_id,
            principal_key=authenticated.principal.key,
        )
        selected_context = self._selected_context(build, presentations)
        return BuildStudioView(
            build=build,
            candidates=presentations,
            comparison=_comparison(presentations),
            selected_context=selected_context,
        )

    def select(
        self,
        authenticated: AuthenticatedStudioRequest,
        *,
        project_id: str,
        build_id: str,
        candidate_id: str,
    ) -> BuildStudioView:
        build = self._writable_build(authenticated, project_id, build_id)
        candidate = self.store.get_candidate(
            candidate_id,
            build_id=build_id,
            project_id=project_id,
            principal_key=authenticated.principal.key,
        )
        self._present_candidate(build, candidate)
        self.store.select_candidate(
            candidate_id,
            build_id=build_id,
            project_id=project_id,
            principal_key=authenticated.principal.key,
        )
        return self.get(authenticated, project_id=project_id, build_id=build_id)

    def cancel_candidate(
        self,
        authenticated: AuthenticatedStudioRequest,
        *,
        project_id: str,
        build_id: str,
        candidate_id: str,
    ) -> BuildStudioView:
        self._writable_build(authenticated, project_id, build_id)
        candidate = self.store.get_candidate(
            candidate_id,
            build_id=build_id,
            project_id=project_id,
            principal_key=authenticated.principal.key,
        )
        self.agent_service.cancel(candidate.run_id)
        self.store.reconcile_candidate(
            candidate.id,
            status="cancelled",
            base_fingerprint=candidate.base_fingerprint,
        )
        return self.get(authenticated, project_id=project_id, build_id=build_id)

    def resume_candidate(
        self,
        authenticated: AuthenticatedStudioRequest,
        *,
        project_id: str,
        build_id: str,
        candidate_id: str,
        message: str | None,
    ) -> BuildStudioView:
        self._writable_build(authenticated, project_id, build_id)
        candidate = self.store.get_candidate(
            candidate_id,
            build_id=build_id,
            project_id=project_id,
            principal_key=authenticated.principal.key,
        )
        try:
            self.agent_service.resume(candidate.run_id, message=message)
        except AgentRecordNotFound as exc:
            raise StudioRecordNotFound("The Candidate Runtime record is unavailable.") from exc
        except ValueError as exc:
            raise StudioConflict(str(exc)) from exc
        return self.get(authenticated, project_id=project_id, build_id=build_id)

    def contextual_command(
        self,
        authenticated: AuthenticatedStudioRequest,
        *,
        project_id: str,
        build_id: str,
        candidate_id: str,
        command: ContextCommand,
        selected_node_ids: list[str],
    ) -> dict[str, Any]:
        build = self._writable_build(authenticated, project_id, build_id)
        candidate = self.store.get_candidate(
            candidate_id,
            build_id=build_id,
            project_id=project_id,
            principal_key=authenticated.principal.key,
        )
        run = self.agent_store.get_run(candidate.run_id)
        if run.snapshot is None or isinstance(run.snapshot, AgentConfigSnapshot):
            if command in {"explain_selection", "explain_variable_flow", "safer_fallback"}:
                raise StudioConflict("This contextual command requires a graph application.")
            return self._config_context_result(run, command)
        head = self.agent_store.get_workspace_head(run.id)
        plan = WorkflowPlan.model_validate(head.snapshot)
        node_by_id = {node.id: node for node in plan.nodes}
        selected = []
        for node_id in selected_node_ids[:20]:
            node = node_by_id.get(node_id)
            if node is None:
                raise StudioConflict(
                    "The selected node is not present in the authoritative candidate Workspace."
                )
            selected.append(node)
        if command == "safer_fallback":
            if not selected:
                raise StudioConflict("Select at least one authoritative node first.")
            labels = ", ".join(node.title or node.id for node in selected)
            created = self._start_candidate(
                authenticated,
                build=build,
                label="更安全的兜底",
                intent=f"围绕 {labels} 增加显式失败或低置信度兜底。",
                goal=(
                    f"为选中节点 {labels} 提供更安全的兜底或错误路径。"
                    "保留无关节点和布局，仅使用显式 Typed Patch，并解释副作用。"
                ),
                constraints=RunConstraints(
                    workspace_only=True,
                    selected_node_ids=[node.id for node in selected],
                    viewport=run.constraints.viewport,
                    current_panel=run.constraints.current_panel,
                    canvas_draft_hash=run.constraints.canvas_draft_hash,
                    dirty_state=run.constraints.dirty_state,
                    canvas_context_revision=run.constraints.canvas_context_revision,
                ),
                source_candidate_ids=[candidate.id],
            )
            return {"kind": "candidate_started", "candidate": created.model_dump(mode="json")}
        if command == "suggest_resources":
            resources = [
                _resource_public_view(item)
                for item in run.snapshot.capabilities
                if str(item.get("type") or "")
                in {"dataset", "model", "tool-resource", "agent-strategy", "trigger"}
            ][:20]
            return {
                "kind": command,
                "summary": "仅显示固定在该 Candidate Snapshot 的兼容资源；元数据是不可信数据。",
                "items": redact_sensitive_data(resources),
            }
        if command == "generate_scenarios":
            return {
                "kind": command,
                "summary": "已生成可供后续 Scenario Lab 使用的业务建议；Phase 1 不执行候选。",
                "items": _scenario_suggestions(plan, selected),
            }
        if not selected:
            raise StudioConflict("Select at least one authoritative node first.")
        if command == "explain_variable_flow":
            return {
                "kind": command,
                "summary": "变量流来自服务器工作区，不来自浏览器 Raw Graph。",
                "items": [_variable_flow(plan, node.id) for node in selected],
            }
        return {
            "kind": command,
            "summary": "节点说明来自固定 Capability 和服务器工作区。",
            "items": [_node_explanation(plan, node.id, run.snapshot.capabilities) for node in selected],
        }

    def _start_candidate(
        self,
        authenticated: AuthenticatedStudioRequest,
        *,
        build: StudioBuild,
        label: str,
        intent: str,
        goal: str,
        constraints: RunConstraints | None,
        source_candidate_ids: list[str] | None = None,
        read_only: bool = False,
    ) -> StudioCandidate:
        session = self.agent_service.create_session(
            app_id=build.app_id,
            app_mode=build.app_mode,
            app_name=build.app_name,
            allow_config_create=True,
        )
        effective = constraints or RunConstraints()
        effective = RunConstraints.model_validate(
            {
                **effective.model_dump(mode="json"),
                "allow_draft_test": False,
                "workspace_only": True,
                "read_only": read_only,
            }
        )
        if self.durable_jobs:
            run = self.agent_service.submit_goal(
                session.id,
                message=goal,
                constraints=effective,
                dispatch=False,
            )
        else:
            run = self.agent_service.submit_goal(
                session.id,
                message=goal,
                constraints=effective,
            )
        try:
            candidate = self.store.add_candidate(
                build_id=build.id,
                project_id=build.project_id,
                principal_key=authenticated.principal.key,
                run_id=run.id,
                label=label,
                intent=intent,
                source_candidate_ids=source_candidate_ids,
            )
            if self.durable_jobs:
                self.store.enqueue_job(
                    project_id=build.project_id,
                    principal_key=authenticated.principal.key,
                    kind="build.agent_run",
                    payload={
                        "run_id": run.id,
                        "candidate_id": candidate.id,
                        "authorized_by": authenticated.principal.key,
                        "workspace_only": True,
                        "production_write": False,
                    },
                    idempotency_key=f"build-agent-run:{run.id}",
                    max_attempts=3,
                )
            return candidate
        except Exception:
            try:
                self.agent_service.cancel(run.id)
            except Exception:
                pass
            raise

    def _present_candidate(
        self,
        build: StudioBuild,
        candidate: StudioCandidate,
    ) -> CandidatePresentation:
        try:
            run = self.agent_store.get_run(candidate.run_id)
        except AgentRecordNotFound:
            candidate = self.store.reconcile_candidate(
                candidate.id,
                status="invalid",
                base_fingerprint=candidate.base_fingerprint,
            )
            return CandidatePresentation(
                candidate=candidate,
                phase="missing",
                business_summary="Candidate Runtime 记录不可用。",
                error={"code": "CANDIDATE_RUN_MISSING"},
            )
        fingerprint = _base_fingerprint(run)
        status = _candidate_status(run)
        if fingerprint is not None:
            same_base = self.store.bind_build_base(
                build.id,
                base_fingerprint=fingerprint,
            )
            if not same_base:
                status = "conflicted"
        candidate = self.store.reconcile_candidate(
            candidate.id,
            status=status,
            base_fingerprint=fingerprint,
        )
        review = redact_sensitive_data(run.review or {})
        if not isinstance(review, dict):
            review = {}
        business_diff = [
            str(item)[:2_000]
            for item in review.get("business_diff", [])
            if str(item).strip()
        ]
        technical_diff = [
            item
            for item in review.get("technical_diff", [])
            if isinstance(item, dict)
        ]
        assumptions = (
            list(run.goal_plan.assumptions)
            if run.goal_plan is not None
            else []
        )
        unresolved = []
        if run.phase == RunPhase.WAITING_USER:
            unresolved = [
                item.summary
                for item in run.observations
                if item.kind == "user.input.required"
            ][-5:]
            if not unresolved:
                unresolved = ["Builder 需要补充业务信息后才能继续。"]
        head_snapshot = None
        if run.head_version_id is not None:
            try:
                head_snapshot = self.agent_store.get_workspace_head(run.id).snapshot
            except AgentRecordNotFound:
                head_snapshot = None
        layout = _layout_preview(run, technical_diff, head_snapshot)
        events = self.agent_store.list_events(run.id, limit=500)
        versions = self.agent_store.list_workspace_versions(run.id)
        return CandidatePresentation(
            candidate=candidate,
            phase=run.phase.value,
            workspace_version_id=run.head_version_id,
            business_summary=(
                "；".join(business_diff[:6])
                if business_diff
                else _phase_summary(run)
            ),
            assumptions=assumptions[:20],
            changed_path=_changed_path(technical_diff),
            risk=review.get("risk") if isinstance(review.get("risk"), dict) else {},
            validation=(
                review.get("validation")
                if isinstance(review.get("validation"), dict)
                else {}
            ),
            side_effects=(
                review.get("side_effects")
                if isinstance(review.get("side_effects"), dict)
                else {}
            ),
            unresolved_questions=unresolved,
            goal_plan=(
                run.goal_plan.model_dump(mode="json")
                if run.goal_plan is not None
                else {}
            ),
            timeline=[
                {
                    "seq": event.seq,
                    "type": event.type,
                    "phase": event.phase,
                    "message": event.message,
                    "timestamp": event.timestamp.isoformat(),
                }
                for event in events[-100:]
            ],
            technical_detail={
                "domain": (
                    "config" if isinstance(run.snapshot, AgentConfigSnapshot) else "graph"
                ),
                "workspace_versions": [
                    {
                        "id": version.id,
                        "parent_id": version.parent_id,
                        "operations": [
                            str(operation.get("op") or "")
                            for operation in (version.patch or {}).get("operations", [])
                            if isinstance(operation, dict)
                        ],
                        "validated": bool((version.validation or {}).get("ok")),
                    }
                    for version in versions
                ],
                "raw_plan_exposed": False,
                "raw_dsl_exposed": False,
            },
            reconstructable=_reconstructable(self.agent_store, run),
            layout_preview=layout,
            error=(
                redact_sensitive_data(run.error)
                if isinstance(run.error, dict)
                else None
            ),
        )

    def _selected_context(
        self,
        build: StudioBuild,
        presentations: list[CandidatePresentation],
    ) -> dict[str, Any]:
        selected_id = build.selected_candidate_id
        selected = next(
            (item for item in presentations if item.candidate.id == selected_id),
            None,
        )
        if selected is None and presentations:
            selected = presentations[0]
        if selected is None:
            return {"state": "empty", "message": "生成 Candidate 后可查看节点与变量流。"}
        run = self.agent_store.get_run(selected.candidate.run_id)
        if run.head_version_id is None or isinstance(run.snapshot, AgentConfigSnapshot):
            return {
                "state": "ready",
                "domain": "config" if isinstance(run.snapshot, AgentConfigSnapshot) else "initializing",
                "selected_node_ids": [],
                "message": "配置型应用没有伪造的画布选区。",
            }
        head = self.agent_store.get_workspace_head(run.id)
        plan = WorkflowPlan.model_validate(head.snapshot)
        node_by_id = {node.id: node for node in plan.nodes}
        nodes = [
            {
                "id": node_id,
                "type": node_by_id[node_id].type,
                "title": node_by_id[node_id].title,
            }
            for node_id in run.constraints.selected_node_ids
            if node_id in node_by_id
        ]
        return {
            "state": "ready",
            "domain": "graph",
            "selected_node_ids": [item["id"] for item in nodes],
            "nodes": nodes,
            "canvas_context_revision": run.constraints.canvas_context_revision,
            "dirty_state": run.constraints.dirty_state,
            "authoritative_source": "server_workspace",
        }

    def _config_context_result(self, run: AgentRun, command: ContextCommand) -> dict[str, Any]:
        if command == "generate_scenarios":
            return {
                "kind": command,
                "summary": "配置型应用场景建议基于应用模式；Phase 1 不执行候选。",
                "items": [
                    {"name": "正常输入", "expected": "返回符合业务提示词的结果"},
                    {"name": "信息不足", "expected": "明确要求补充必要信息"},
                    {"name": "提示注入", "expected": "不扩大 Tool 或数据权限"},
                ],
            }
        resources = [
            _resource_public_view(item)
            for item in (run.snapshot.capabilities if run.snapshot else [])
        ][:20]
        return {
            "kind": command,
            "summary": "资源来自固定 Config Snapshot，且不包含凭据值。",
            "items": redact_sensitive_data(resources),
        }

    def _writable_build(
        self,
        authenticated: AuthenticatedStudioRequest,
        project_id: str,
        build_id: str,
    ) -> StudioBuild:
        _, membership = self.store.get_project_for_principal(
            project_id,
            authenticated.principal.key,
        )
        if membership.role not in {"owner", "admin", "builder"}:
            raise StudioAccessDenied("Your project role cannot change Build candidates.")
        return self.store.get_build(
            build_id,
            project_id=project_id,
            principal_key=authenticated.principal.key,
        )


def _candidate_status(run: AgentRun) -> str:
    if run.phase == RunPhase.CANCELLED:
        return "cancelled"
    if run.phase == RunPhase.INTERRUPTED:
        return "interrupted"
    if run.phase == RunPhase.CONFLICTED:
        return "conflicted"
    if run.phase == RunPhase.WAITING_USER:
        return "waiting_input"
    if run.phase == RunPhase.FAILED:
        return "invalid"
    if run.review and bool(run.review.get("ready")):
        return "valid"
    if run.phase in {RunPhase.QUEUED, RunPhase.OBSERVING}:
        return "queued"
    return "building"


def _base_fingerprint(run: AgentRun) -> str | None:
    snapshot = run.snapshot
    if snapshot is None:
        return None
    if snapshot.base_hash:
        return snapshot.base_hash
    if isinstance(snapshot, AgentConfigSnapshot):
        base = snapshot.base_config
    else:
        plan = WorkflowPlan.model_validate(snapshot.base_plan)
        base = {
            "app_mode": plan.app_mode,
            "name": plan.name,
            "nodes": [node.type for node in plan.nodes],
            "edges": len(plan.edges),
        }
    encoded = json.dumps(
        base,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return f"create:{sha256(encoded).hexdigest()}"


def _reconstructable(store: AgentStore, run: AgentRun) -> bool:
    if run.head_version_id is None:
        return False
    versions = store.list_workspace_versions(run.id)
    by_id = {version.id: version for version in versions}
    current_id: str | None = run.head_version_id
    visited: set[str] = set()
    while current_id is not None:
        if current_id in visited or current_id not in by_id:
            return False
        visited.add(current_id)
        version = by_id[current_id]
        if version.run_id != run.id:
            return False
        if version.parent_id is not None and not isinstance(version.patch, dict):
            return False
        current_id = version.parent_id
    roots = [version for version in versions if version.parent_id is None]
    return len(roots) == 1 and len(visited) == len(versions)


def _comparison(items: list[CandidatePresentation]) -> dict[str, Any]:
    return {
        "business_behavior": {
            item.candidate.id: item.business_summary for item in items
        },
        "nodes_edges": {
            item.candidate.id: item.changed_path for item in items
        },
        "model_resources": {
            item.candidate.id: _resource_change_summary(item)
            for item in items
        },
        "side_effects": {
            item.candidate.id: item.side_effects for item in items
        },
        "estimated_cost_inputs": {
            item.candidate.id: _cost_inputs(item) for item in items
        },
        "validation": {
            item.candidate.id: {
                "status": item.candidate.status,
                "ok": item.validation.get("ok"),
                "issue_count": len(item.validation.get("issues", [])),
                "reconstructable": item.reconstructable,
            }
            for item in items
        },
        "unresolved_questions": {
            item.candidate.id: item.unresolved_questions for item in items
        },
    }


def _resource_change_summary(item: CandidatePresentation) -> dict[str, Any]:
    risk = item.risk
    return {
        "model_changed": any("模型" in part or "model" in part.lower() for part in item.changed_path),
        "resource_risk": risk.get("risk"),
        "requires_mapping": bool(
            any(
                keyword in part.lower()
                for keyword in ("dataset", "tool", "knowledge", "model")
                for part in item.changed_path
            )
        ),
    }


def _cost_inputs(item: CandidatePresentation) -> dict[str, Any]:
    counts = item.side_effects.get("counts")
    return {
        "model_nodes": (counts or {}).get("model", (counts or {}).get("model_cost", 0))
        if isinstance(counts, dict)
        else 0,
        "external_nodes": sum(
            int(value)
            for key, value in (counts or {}).items()
            if key in {"http", "tool", "human", "notification"}
            and isinstance(value, int)
        )
        if isinstance(counts, dict)
        else 0,
        "note": "Phase 1 reports cost inputs, not fabricated price estimates.",
    }


def _candidate_strategies(goal: str, count: int) -> list[tuple[str, str]]:
    known: list[tuple[str, str]] = []
    if "人工接管" in goal or "人工兜底" in goal:
        known.append(("人工接管", "低置信度时暂停自动回答并转交明确的人工处理路径。"))
    if "二次追问" in goal or "澄清" in goal:
        known.append(("二次追问", "低置信度时向用户提出一次有边界的澄清问题。"))
    if "模型兜底" in goal or "模型切换" in goal:
        known.append(("模型兜底", "低置信度时切换到固定兼容模型并保留失败出口。"))
    defaults = [
        ("保守方案", "优先减少外部副作用并保留当前主路径。"),
        ("体验方案", "优先减少用户中断并提供清晰的恢复路径。"),
        ("治理方案", "优先增强可观察性、校验与人工控制。"),
    ]
    for item in defaults:
        if len(known) >= count:
            break
        known.append(item)
    return known[:count]


def _alternative_goal(
    goal: str,
    label: str,
    intent: str,
    index: int,
    count: int,
) -> str:
    return (
        f"为同一固定 Base 生成第 {index}/{count} 个独立 Candidate：{label}。"
        f"方案意图：{intent} 用户目标：{goal}。"
        "只修改当前 Candidate Workspace；使用最小显式 Typed Patch，保留无关节点、"
        "边、配置、容器元数据和布局。不要写 Dify，不要输出或替换 Raw DSL/Graph。"
    )


def _phase_summary(run: AgentRun) -> str:
    if run.phase == RunPhase.WAITING_USER:
        return "该方案需要补充业务信息。"
    if run.phase == RunPhase.FAILED:
        return "该方案未通过 Builder Runtime。"
    if run.phase == RunPhase.CANCELLED:
        return "该方案已取消，未写入 Dify。"
    if run.phase == RunPhase.INTERRUPTED:
        return "服务中断后已保留 Workspace，可显式恢复。"
    return "正在生成并确定性校验 Candidate。"


def _changed_path(changes: list[dict[str, Any]]) -> list[str]:
    path: list[str] = []
    for change in changes:
        message = str(change.get("message") or "").strip()
        if message and message not in path:
            path.append(message[:1_000])
    return path[:30]


def _layout_preview(
    run: AgentRun,
    changes: list[dict[str, Any]],
    head_snapshot: dict[str, Any] | None,
) -> dict[str, Any] | None:
    if not isinstance(run.snapshot, AgentWorkflowSnapshot) or run.head_version_id is None:
        return None
    try:
        head = WorkflowPlan.model_validate(head_snapshot or run.snapshot.base_plan)
        # Avoid accepting browser graph state: only persisted Snapshot layout is read.
        base_graph = run.snapshot.base_graph if isinstance(run.snapshot.base_graph, dict) else {}
        base_positions = {
            str(node.get("id")): deepcopy(node.get("position"))
            for node in base_graph.get("nodes", [])
            if isinstance(node, dict)
            and isinstance(node.get("position"), dict)
            and node.get("id")
        }
        changed_ids = {
            str(change.get("target"))
            for change in changes
            if str(change.get("type") or "").startswith("node_")
        }
        nodes = []
        for index, node in enumerate(head.nodes):
            position = base_positions.get(node.id)
            if position is None:
                position = {"x": 120 + (index % 4) * 280, "y": 100 + (index // 4) * 180}
            nodes.append(
                {
                    "id": node.id,
                    "type": node.type,
                    "title": node.title,
                    "x": float(position.get("x", 0)),
                    "y": float(position.get("y", 0)),
                    "changed": node.id in changed_ids,
                    "preserved": node.id in base_positions,
                }
            )
        return {
            "nodes": nodes,
            "focus_node_ids": sorted(changed_ids),
            "commands": ["focus_changed_path", "fit_candidate"],
            "authoritative_source": "persisted_workspace_layout",
            "mutates_layout": False,
        }
    except Exception:
        return None


def _node_explanation(
    plan: WorkflowPlan,
    node_id: str,
    capabilities: list[dict[str, Any]],
) -> dict[str, Any]:
    node = next(node for node in plan.nodes if node.id == node_id)
    definition = next(
        (item for item in capabilities if item.get("type") == node.type),
        {},
    )
    incoming = [edge.source for edge in plan.edges if edge.target == node_id]
    outgoing = [edge.target for edge in plan.edges if edge.source == node_id]
    return {
        "id": node.id,
        "type": node.type,
        "title": node.title,
        "summary": str(definition.get("summary") or "该节点参与当前业务路径。")[:2_000],
        "incoming": incoming,
        "outgoing": outgoing,
        "side_effect": definition.get("side_effect", "unknown"),
    }


def _variable_flow(plan: WorkflowPlan, node_id: str) -> dict[str, Any]:
    node = next(node for node in plan.nodes if node.id == node_id)
    serialized = json.dumps(node.params, ensure_ascii=False)
    selectors = []
    for source, variable in re.findall(r"\{\{#([^.#}]+)\.([^#}]+)#\}\}", serialized):
        item = {"source_node_id": source, "variable": variable}
        if item not in selectors:
            selectors.append(item)
    return {
        "node_id": node.id,
        "title": node.title,
        "inputs": selectors[:50],
        "upstream_nodes": [edge.source for edge in plan.edges if edge.target == node_id],
        "downstream_nodes": [edge.target for edge in plan.edges if edge.source == node_id],
    }


def _scenario_suggestions(plan: WorkflowPlan, selected: list[Any]) -> list[dict[str, str]]:
    focus = "、".join(node.title or node.id for node in selected) or "主路径"
    return [
        {"name": "正常售后请求", "expected": f"{focus} 完成预期业务处理"},
        {"name": "低置信度输入", "expected": "进入明确兜底且不产生未经说明的副作用"},
        {"name": "缺失关键信息", "expected": "提出有边界的澄清问题或安全转人工"},
        {"name": "提示注入文本", "expected": "不扩大 Tool、资源或写入权限"},
    ]


def _resource_public_view(item: dict[str, Any]) -> dict[str, Any]:
    allowed = {
        "type",
        "id",
        "name",
        "provider",
        "provider_id",
        "provider_type",
        "tool_name",
        "event_name",
        "summary",
        "status",
        "features",
        "requires_configuration",
        "supported_creation_methods",
        "untrusted_data",
    }
    return {key: deepcopy(value) for key, value in item.items() if key in allowed}
