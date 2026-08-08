from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
import sqlite3
from typing import Any
from urllib.parse import urlencode

from app.agent.service import AgentApplicationService
from app.agent.state import AgentRun, AgentSession, RunPhase
from app.agent.store import AgentRecordNotFound, AgentStore
from app.studio.identity import AuthenticatedStudioRequest
from app.studio.models import (
    HomeSectionState,
    Project,
    StudioHome,
    StudioHomeApp,
    V4WorkItem,
)
from app.studio.store import StudioAccessDenied, StudioStore


class V4ContinuityError(RuntimeError):
    code = "STUDIO_V4_CONTINUITY_ERROR"


class V4ContinuityReader:
    """Reads the additive v4 SQLite schema without initializing or mutating it."""

    def __init__(self, path: Path) -> None:
        self.path = Path(path)

    def available(self) -> bool:
        if not self.path.is_file():
            return False
        with self._connect() as connection:
            rows = connection.execute(
                """
                SELECT name FROM sqlite_master
                WHERE type = 'table' AND name IN ('agent_sessions', 'agent_runs')
                """
            ).fetchall()
        return {str(row["name"]) for row in rows} == {
            "agent_sessions",
            "agent_runs",
        }

    def accessible_session_ids(self, app_ids: set[str]) -> list[str]:
        if not app_ids or not self.available():
            return []
        with self._connect() as connection:
            rows = connection.execute(
                """
                SELECT id, app_id FROM agent_sessions
                WHERE app_id IS NOT NULL
                ORDER BY updated_at DESC
                LIMIT 1000
                """
            ).fetchall()
        return [
            str(row["id"])
            for row in rows
            if str(row["app_id"]) in app_ids
        ]

    def work_items(
        self,
        *,
        session_ids: list[str],
        v4_enabled: bool,
        base_path: str,
        limit: int = 30,
    ) -> list[V4WorkItem]:
        if not session_ids or not self.available():
            return []
        allowed = set(session_ids)
        with self._connect() as connection:
            rows = connection.execute(
                """
                SELECT
                    r.id AS run_id, r.session_id, r.goal, r.phase,
                    r.updated_at, s.app_id, s.app_name, s.app_mode
                FROM agent_runs r
                JOIN agent_sessions s ON s.id = r.session_id
                ORDER BY r.updated_at DESC
                LIMIT 1000
                """
            ).fetchall()
        items: list[V4WorkItem] = []
        seen_sessions: set[str] = set()
        for row in rows:
            session_id = str(row["session_id"])
            if session_id not in allowed or session_id in seen_sessions:
                continue
            seen_sessions.add(session_id)
            app_id = str(row["app_id"] or "")
            app_mode = str(row["app_mode"] or "workflow")
            app_name = str(row["app_name"] or "未命名应用")
            phase = str(row["phase"])
            resumable, requires_message, reason_code, reason = _resume_state(
                phase,
                v4_enabled=v4_enabled,
            )
            items.append(
                V4WorkItem(
                    run_id=str(row["run_id"]),
                    session_id=session_id,
                    app_id=app_id,
                    app_name=app_name,
                    app_mode=app_mode,
                    goal=str(row["goal"]),
                    phase=phase,
                    updated_at=_datetime(row["updated_at"]),
                    resumable=resumable,
                    resume_requires_message=requires_message,
                    reason_code=reason_code,
                    reason=reason,
                    build_url=_build_url(
                        base_path=base_path,
                        app_id=app_id,
                        app_mode=app_mode,
                        app_name=app_name,
                        run_id=str(row["run_id"]),
                    ),
                )
            )
            if len(items) >= limit:
                break
        return items

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(
            f"file:{self.path}?mode=ro",
            uri=True,
            timeout=5.0,
        )
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA query_only=ON")
        return connection


class StudioHomeService:
    def __init__(
        self,
        *,
        store: StudioStore,
        v4_reader: V4ContinuityReader,
        public_base_path: str,
    ) -> None:
        self.store = store
        self.v4_reader = v4_reader
        self.public_base_path = public_base_path

    def home(
        self,
        authenticated: AuthenticatedStudioRequest,
        *,
        project_id: str | None,
        search: str | None,
        app_mode: str | None,
        v4_enabled: bool,
    ) -> StudioHome:
        selected_project_id = project_id or authenticated.session.project_id
        project, membership = self.store.get_project_for_principal(
            selected_project_id,
            authenticated.principal.key,
        )
        if project.dify_tenant_id != authenticated.principal.dify_tenant_id:
            raise StudioAccessDenied(
                "This Studio project belongs to another Dify workspace."
            )

        visible_apps = list(authenticated.host.apps)
        if project.kind != "personal":
            linked_ids = self.store.list_project_app_ids(
                project.id,
                authenticated.principal.key,
            )
            visible_apps = [app for app in visible_apps if app.id in linked_ids]
        query = (search or "").strip().casefold()
        if query:
            visible_apps = [
                app
                for app in visible_apps
                if query in app.name.casefold()
                or query in app.description.casefold()
            ]
        if app_mode:
            visible_apps = [app for app in visible_apps if app.mode == app_mode]

        apps = [
            StudioHomeApp(
                id=app.id,
                name=app.name,
                mode=app.mode,
                description=app.description,
                updated_at=app.updated_at,
                build_url=_build_url(
                    base_path=self.public_base_path,
                    app_id=app.id,
                    app_mode=app.mode,
                    app_name=app.name,
                ),
            )
            for app in visible_apps
        ]
        if project.kind == "personal" and authenticated.host.apps_available:
            all_visible_ids = {app.id for app in authenticated.host.apps}
            session_ids = self.v4_reader.accessible_session_ids(all_visible_ids)
            self.store.link_v4_sessions(
                project_id=project.id,
                principal_key=authenticated.principal.key,
                session_ids=session_ids,
            )
        linked_session_ids = self.store.list_v4_session_ids(
            project.id,
            authenticated.principal.key,
        )
        work = self.v4_reader.work_items(
            session_ids=linked_session_ids,
            v4_enabled=v4_enabled,
            base_path=self.public_base_path,
        )
        review_items = self.store.list_change_requests(
            project_id=project.id,
            principal_key=authenticated.principal.key,
        )
        assigned_reviews = [
            {
                "id": item.id,
                "title": item.title,
                "status": item.status,
                "updated_at": item.updated_at.isoformat(),
                "review_url": (
                    f"{self.public_base_path.rstrip('/')}/?"
                    + urlencode(
                        {
                            "studio": "releases",
                            "change_request_id": item.id,
                        }
                    )
                ),
            }
            for item in review_items
            if item.assignee_key == authenticated.principal.key
            and item.status in {"in_review", "changes_requested"}
        ][:10]
        release_records = self.store.list_release_records(
            project_id=project.id,
            principal_key=authenticated.principal.key,
        )
        releases = [
            {
                "id": item.id,
                "action": item.action,
                "outcome": item.outcome,
                "artifact_id": item.artifact_id,
                "environment_id": item.environment_id,
                "created_at": item.created_at.isoformat(),
                "release_url": (
                    f"{self.public_base_path.rstrip('/')}/?"
                    + urlencode(
                        {
                            "studio": "releases",
                            "change_request_id": item.change_request_id,
                        }
                    )
                ),
            }
            for item in release_records[:10]
        ]
        incident_records = self.store.list_run_incidents(
            project_id=project.id,
            principal_key=authenticated.principal.key,
        )
        incidents = [
            {
                "id": item.id,
                "title": item.title,
                "severity": item.severity,
                "status": item.status,
                "stable_error_code": item.stable_error_code,
                "last_seen_at": item.last_seen_at.isoformat(),
                "incident_url": (
                    f"{self.public_base_path.rstrip('/')}/?"
                    + urlencode(
                        {
                            "studio": "runs",
                            "incident_id": item.id,
                        }
                    )
                ),
            }
            for item in incident_records
            if item.status != "resolved"
        ][:10]
        execution_records = self.store.list_execution_observations(
            project_id=project.id,
            principal_key=authenticated.principal.key,
        )
        regression_counts: dict[str, dict[str, int]] = {}
        for item in execution_records:
            if not item.artifact_id or item.correlation_state != "exact":
                continue
            summary = regression_counts.setdefault(
                item.artifact_id,
                {"executions": 0, "failures": 0},
            )
            summary["executions"] += 1
            summary["failures"] += int(
                item.status in {"failed", "partial_succeeded"}
            )
        quality_regressions = [
            {
                "artifact_id": artifact_id,
                "executions": summary["executions"],
                "failures": summary["failures"],
                "failure_rate": round(
                    summary["failures"] / summary["executions"],
                    4,
                ),
                "run_center_url": (
                    f"{self.public_base_path.rstrip('/')}/?"
                    + urlencode({"studio": "runs"})
                ),
            }
            for artifact_id, summary in sorted(regression_counts.items())
            if summary["failures"]
        ][:10]
        states = _home_states(
            apps_available=authenticated.host.apps_available,
            app_count=len(apps),
            work_count=len(work),
            v4_available=self.v4_reader.available(),
            review_count=len(assigned_reviews),
            release_count=len(releases),
            incident_count=len(incidents),
            regression_count=len(quality_regressions),
        )
        return StudioHome(
            project=project,
            membership=membership,
            apps=apps,
            work=work,
            assigned_reviews=assigned_reviews,
            releases=releases,
            quality_regressions=quality_regressions,
            incidents=incidents,
            states=states,
        )

    def resume_v4(
        self,
        authenticated: AuthenticatedStudioRequest,
        *,
        project_id: str,
        run_id: str,
        message: str | None,
        agent_store: AgentStore | None,
        agent_service: AgentApplicationService | None,
    ) -> AgentRun:
        self.store.get_project_for_principal(
            project_id,
            authenticated.principal.key,
        )
        linked_session_ids = set(
            self.store.list_v4_session_ids(
                project_id,
                authenticated.principal.key,
            )
        )
        if agent_store is None or agent_service is None:
            raise V4ContinuityError(
                "v4 Builder is disabled; enable it to resume this work."
            )
        try:
            run = agent_store.get_run(run_id)
            session = agent_store.get_session(run.session_id)
        except AgentRecordNotFound as exc:
            raise V4ContinuityError("The linked v4 work no longer exists.") from exc
        if session.id not in linked_session_ids:
            raise StudioAccessDenied(
                "This v4 work is not linked to the selected Studio project."
            )
        if run.phase not in {
            RunPhase.WAITING_USER,
            RunPhase.PAUSED,
            RunPhase.INTERRUPTED,
        }:
            raise V4ContinuityError(
                _resume_state(run.phase.value, v4_enabled=True)[3]
                or "This v4 work cannot be resumed."
            )
        resumed = agent_service.resume(run.id, message=message)
        self.store.append_activity(
            project_id=project_id,
            principal_key=authenticated.principal.key,
            kind="v4.run.resumed",
            entity_type="agent_run",
            entity_id=run.id,
            summary={"from_phase": run.phase.value},
        )
        return resumed


def _resume_state(
    phase: str,
    *,
    v4_enabled: bool,
) -> tuple[bool, bool, str | None, str | None]:
    if phase in {"paused", "interrupted", "waiting_user"} and not v4_enabled:
        return (
            False,
            False,
            "BUILDER_V4_DISABLED",
            "v4 Builder 当前未启用；启用后可从原检查点继续。",
        )
    if phase in {"paused", "interrupted"}:
        return True, False, None, None
    if phase == "waiting_user":
        return (
            True,
            True,
            "USER_INPUT_REQUIRED",
            "此工作正在等待补充信息；打开 Build 后输入信息即可继续。",
        )
    if phase == "waiting_approval":
        return (
            False,
            False,
            "APPROVAL_REQUIRED",
            "此工作正在等待审批；请在 Build 的审批卡中处理。",
        )
    if phase in {"completed", "conflicted", "cancelled", "failed"}:
        return (
            False,
            False,
            "WORK_FINISHED",
            "此轮工作已结束；可以打开记录或开始新的修改。",
        )
    return (
        False,
        False,
        "WORK_IN_PROGRESS",
        "此工作仍在执行；打开 Build 查看最新进度。",
    )


def _home_states(
    *,
    apps_available: bool,
    app_count: int,
    work_count: int,
    v4_available: bool,
    review_count: int,
    release_count: int,
    incident_count: int,
    regression_count: int,
) -> dict[str, HomeSectionState]:
    if apps_available:
        apps = HomeSectionState(
            state="ready" if app_count else "empty",
            message=(
                f"已加载 {app_count} 个当前账号可访问的 Dify 应用。"
                if app_count
                else "当前筛选下没有可访问的 Dify 应用。"
            ),
        )
    else:
        apps = HomeSectionState(
            state="partial_error",
            message="身份已验证，但 Dify 应用列表暂时不可用。",
            recoverable=True,
        )
    if not v4_available:
        work = HomeSectionState(
            state="empty",
            message="没有检测到可迁移的 v4 Builder 数据。",
        )
    else:
        work = HomeSectionState(
            state="ready" if work_count else "empty",
            message=(
                f"找到 {work_count} 项最近的 Builder 工作。"
                if work_count
                else "当前项目没有可显示的 v4 Builder 工作。"
            ),
        )
    not_started = HomeSectionState(
        state="empty",
        message="此能力将在后续版本启用；当前没有生成任何占位数据。",
    )
    return {
        "apps": apps,
        "work": work,
        "drafts": not_started,
        "assigned_reviews": HomeSectionState(
            state="ready" if review_count else "empty",
            message=(
                f"有 {review_count} 项评审等待当前用户处理。"
                if review_count
                else "当前没有分配给你的待处理评审。"
            ),
        ),
        "releases": HomeSectionState(
            state="ready" if release_count else "empty",
            message=(
                f"已加载 {release_count} 条真实 Release 记录。"
                if release_count
                else "当前没有 Release 回执。"
            ),
        ),
        "quality_regressions": HomeSectionState(
            state="ready" if regression_count else "empty",
            message=(
                f"有 {regression_count} 个精确关联发布版本的生产质量回归。"
                if regression_count
                else "当前没有精确关联的生产质量回归。"
            ),
        ),
        "incidents": HomeSectionState(
            state="ready" if incident_count else "empty",
            message=(
                f"有 {incident_count} 个基于脱敏生产证据的开放事件。"
                if incident_count
                else "当前没有开放的运行事件。"
            ),
        ),
    }


def _build_url(
    *,
    base_path: str,
    app_id: str,
    app_mode: str,
    app_name: str,
    run_id: str | None = None,
) -> str:
    params = {
        "studio": "build",
        "studio_entry": "home",
        "embed": "1",
        "intent": "modify",
        "app_id": app_id,
        "app_mode": app_mode,
        "app_name": app_name,
    }
    if run_id:
        params["run_id"] = run_id
    prefix = base_path.rstrip("/")
    return f"{prefix or ''}/?{urlencode(params)}"


def _datetime(value: Any) -> datetime:
    return datetime.fromtimestamp(float(value), tz=timezone.utc)
