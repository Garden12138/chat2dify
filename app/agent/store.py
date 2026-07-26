from __future__ import annotations

from contextlib import contextmanager
from datetime import datetime, timezone
import json
from pathlib import Path
import sqlite3
from typing import Any, Iterator

from app.agent.state import (
    AgentApproval,
    AgentBudgetUsage,
    AgentRun,
    AgentSession,
    ApprovalStatus,
    RunConstraints,
    RunPhase,
    WorkspaceVersion,
    validate_run_transition,
)
from app.agent.trace import AgentEvent, AgentEventType, redact_sensitive_data


class AgentRecordNotFound(KeyError):
    """Raised when a requested v4 Agent record does not exist."""


class AgentStoreConflict(RuntimeError):
    code = "AGENT_STORE_CONFLICT"


_UNSET = object()


class AgentStore:
    def __init__(self, path: Path) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.initialize()

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.path, timeout=5.0)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA busy_timeout=5000")
        connection.execute("PRAGMA foreign_keys=ON")
        return connection

    @contextmanager
    def _transaction(self, *, immediate: bool = False) -> Iterator[sqlite3.Connection]:
        connection = self._connect()
        try:
            connection.execute("BEGIN IMMEDIATE" if immediate else "BEGIN")
            yield connection
            connection.commit()
        except Exception:
            connection.rollback()
            raise
        finally:
            connection.close()

    @contextmanager
    def _reader(self) -> Iterator[sqlite3.Connection]:
        connection = self._connect()
        try:
            yield connection
        finally:
            connection.close()

    def initialize(self) -> None:
        with self._connect() as connection:
            connection.execute("PRAGMA journal_mode=WAL")
            connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS agent_sessions (
                    id TEXT PRIMARY KEY,
                    operation TEXT NOT NULL DEFAULT 'modify',
                    app_id TEXT,
                    app_mode TEXT,
                    app_name TEXT,
                    app_description TEXT NOT NULL DEFAULT '',
                    status TEXT NOT NULL,
                    created_at REAL NOT NULL,
                    updated_at REAL NOT NULL
                );

                CREATE TABLE IF NOT EXISTS agent_runs (
                    id TEXT PRIMARY KEY,
                    session_id TEXT NOT NULL,
                    task_id TEXT,
                    goal TEXT NOT NULL,
                    status TEXT NOT NULL,
                    phase TEXT NOT NULL,
                    base_hash TEXT,
                    head_version_id TEXT,
                    iteration INTEGER NOT NULL,
                    budget_json TEXT NOT NULL,
                    budget_usage_json TEXT NOT NULL DEFAULT '{}',
                    constraints_json TEXT NOT NULL,
                    snapshot_json TEXT,
                    goal_plan_json TEXT,
                    observations_json TEXT NOT NULL DEFAULT '[]',
                    review_json TEXT,
                    commit_result_json TEXT,
                    error_json TEXT,
                    created_at REAL NOT NULL,
                    updated_at REAL NOT NULL,
                    finished_at REAL,
                    FOREIGN KEY(session_id) REFERENCES agent_sessions(id) ON DELETE CASCADE
                );

                CREATE TABLE IF NOT EXISTS agent_events (
                    id TEXT PRIMARY KEY,
                    run_id TEXT NOT NULL,
                    seq INTEGER NOT NULL,
                    event_type TEXT NOT NULL,
                    payload_json TEXT NOT NULL,
                    created_at REAL NOT NULL,
                    UNIQUE(run_id, seq),
                    FOREIGN KEY(run_id) REFERENCES agent_runs(id) ON DELETE CASCADE
                );

                CREATE TABLE IF NOT EXISTS agent_workspace_versions (
                    id TEXT PRIMARY KEY,
                    run_id TEXT NOT NULL,
                    parent_id TEXT,
                    base_hash TEXT,
                    patch_json TEXT,
                    reverse_patch_json TEXT,
                    snapshot_json TEXT NOT NULL,
                    validation_json TEXT,
                    test_result_json TEXT,
                    created_at REAL NOT NULL,
                    FOREIGN KEY(run_id) REFERENCES agent_runs(id) ON DELETE CASCADE,
                    FOREIGN KEY(parent_id) REFERENCES agent_workspace_versions(id)
                );

                CREATE TABLE IF NOT EXISTS agent_approvals (
                    id TEXT PRIMARY KEY,
                    run_id TEXT NOT NULL,
                    workspace_version_id TEXT,
                    action TEXT NOT NULL,
                    scope_json TEXT NOT NULL,
                    status TEXT NOT NULL,
                    expires_at REAL NOT NULL,
                    created_at REAL NOT NULL,
                    resolved_at REAL,
                    FOREIGN KEY(run_id) REFERENCES agent_runs(id) ON DELETE CASCADE,
                    FOREIGN KEY(workspace_version_id)
                        REFERENCES agent_workspace_versions(id)
                );

                CREATE INDEX IF NOT EXISTS idx_agent_sessions_updated
                    ON agent_sessions(updated_at DESC);
                CREATE INDEX IF NOT EXISTS idx_agent_runs_session
                    ON agent_runs(session_id, created_at DESC);
                CREATE INDEX IF NOT EXISTS idx_agent_runs_status
                    ON agent_runs(status, updated_at DESC);
                CREATE INDEX IF NOT EXISTS idx_agent_events_run_seq
                    ON agent_events(run_id, seq);
                CREATE INDEX IF NOT EXISTS idx_agent_workspace_versions_run
                    ON agent_workspace_versions(run_id, created_at);
                CREATE INDEX IF NOT EXISTS idx_agent_approvals_run
                    ON agent_approvals(run_id, created_at DESC);
                """
            )
            _ensure_column(
                connection,
                "agent_sessions",
                "operation",
                "TEXT NOT NULL DEFAULT 'modify'",
            )
            _ensure_column(connection, "agent_sessions", "app_name", "TEXT")
            _ensure_column(
                connection,
                "agent_sessions",
                "app_description",
                "TEXT NOT NULL DEFAULT ''",
            )
            _ensure_column(
                connection,
                "agent_runs",
                "budget_usage_json",
                "TEXT NOT NULL DEFAULT '{}'",
            )
            _ensure_column(connection, "agent_runs", "snapshot_json", "TEXT")
            _ensure_column(connection, "agent_runs", "goal_plan_json", "TEXT")
            _ensure_column(
                connection,
                "agent_runs",
                "observations_json",
                "TEXT NOT NULL DEFAULT '[]'",
            )
            _ensure_column(connection, "agent_runs", "review_json", "TEXT")
            _ensure_column(connection, "agent_runs", "commit_result_json", "TEXT")

    def create_session(self, session: AgentSession) -> AgentSession:
        with self._transaction() as connection:
            connection.execute(
                """
                INSERT INTO agent_sessions (
                    id, operation, app_id, app_mode, app_name, app_description,
                    status, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    session.id,
                    session.operation,
                    session.app_id,
                    session.app_mode,
                    session.app_name,
                    session.app_description,
                    session.status.value,
                    _timestamp(session.created_at),
                    _timestamp(session.updated_at),
                ),
            )
        return self.get_session(session.id)

    def get_session(self, session_id: str) -> AgentSession:
        with self._reader() as connection:
            row = connection.execute(
                "SELECT * FROM agent_sessions WHERE id = ?",
                (session_id,),
            ).fetchone()
        if row is None:
            raise AgentRecordNotFound(session_id)
        return _session_from_row(row)

    def update_session(self, session: AgentSession) -> AgentSession:
        with self._transaction() as connection:
            cursor = connection.execute(
                """
                UPDATE agent_sessions
                SET operation = ?, app_id = ?, app_mode = ?, app_name = ?,
                    app_description = ?, status = ?, updated_at = ?
                WHERE id = ?
                """,
                (
                    session.operation,
                    session.app_id,
                    session.app_mode,
                    session.app_name,
                    session.app_description,
                    session.status.value,
                    _timestamp(session.updated_at),
                    session.id,
                ),
            )
        if cursor.rowcount == 0:
            raise AgentRecordNotFound(session.id)
        return self.get_session(session.id)

    def list_sessions(self, *, limit: int = 100) -> list[AgentSession]:
        _validate_limit(limit)
        with self._reader() as connection:
            rows = connection.execute(
                "SELECT * FROM agent_sessions ORDER BY created_at DESC LIMIT ?",
                (limit,),
            ).fetchall()
        return [_session_from_row(row) for row in rows]

    def create_run(self, run: AgentRun) -> AgentRun:
        with self._transaction() as connection:
            connection.execute(
                """
                INSERT INTO agent_runs (
                    id, session_id, task_id, goal, status, phase, base_hash,
                    head_version_id, iteration, budget_json, budget_usage_json,
                    constraints_json, snapshot_json, goal_plan_json,
                    observations_json, review_json, commit_result_json,
                    error_json, created_at, updated_at, finished_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    run.id,
                    run.session_id,
                    run.task_id,
                    run.goal,
                    run.status.value,
                    run.phase.value,
                    run.base_hash,
                    run.head_version_id,
                    run.iteration,
                    _json_dump(run.budget.model_dump(mode="json")),
                    _json_dump(run.budget_usage.model_dump(mode="json")),
                    _json_dump(run.constraints.model_dump(mode="json")),
                    _optional_json_dump(
                        run.snapshot.model_dump(mode="json")
                        if run.snapshot is not None
                        else None
                    ),
                    _optional_json_dump(
                        run.goal_plan.model_dump(mode="json")
                        if run.goal_plan is not None
                        else None
                    ),
                    _json_dump(
                        [item.model_dump(mode="json") for item in run.observations]
                    ),
                    _optional_json_dump(run.review),
                    _optional_json_dump(run.commit_result),
                    _json_dump(run.error) if run.error is not None else None,
                    _timestamp(run.created_at),
                    _timestamp(run.updated_at),
                    _timestamp(run.finished_at),
                ),
            )
        return self.get_run(run.id)

    def get_run(self, run_id: str) -> AgentRun:
        with self._reader() as connection:
            row = connection.execute(
                "SELECT * FROM agent_runs WHERE id = ?",
                (run_id,),
            ).fetchone()
        if row is None:
            raise AgentRecordNotFound(run_id)
        return _run_from_row(row)

    def update_run(self, run: AgentRun) -> AgentRun:
        current = self.get_run(run.id)
        validate_run_transition(current.phase, run.phase)
        with self._transaction() as connection:
            cursor = connection.execute(
                """
                UPDATE agent_runs
                SET task_id = ?, goal = ?, status = ?, phase = ?, base_hash = ?,
                    head_version_id = ?, iteration = ?, budget_json = ?,
                    budget_usage_json = ?, constraints_json = ?,
                    snapshot_json = ?, goal_plan_json = ?, observations_json = ?,
                    review_json = ?, commit_result_json = ?, error_json = ?,
                    updated_at = ?, finished_at = ?
                WHERE id = ?
                """,
                (
                    run.task_id,
                    run.goal,
                    run.status.value,
                    run.phase.value,
                    run.base_hash,
                    run.head_version_id,
                    run.iteration,
                    _json_dump(run.budget.model_dump(mode="json")),
                    _json_dump(run.budget_usage.model_dump(mode="json")),
                    _json_dump(run.constraints.model_dump(mode="json")),
                    _optional_json_dump(
                        run.snapshot.model_dump(mode="json")
                        if run.snapshot is not None
                        else None
                    ),
                    _optional_json_dump(
                        run.goal_plan.model_dump(mode="json")
                        if run.goal_plan is not None
                        else None
                    ),
                    _json_dump(
                        [item.model_dump(mode="json") for item in run.observations]
                    ),
                    _optional_json_dump(run.review),
                    _optional_json_dump(run.commit_result),
                    _json_dump(run.error) if run.error is not None else None,
                    _timestamp(run.updated_at),
                    _timestamp(run.finished_at),
                    run.id,
                ),
            )
        if cursor.rowcount == 0:
            raise AgentRecordNotFound(run.id)
        return self.get_run(run.id)

    def update_run_canvas_constraints(
        self,
        run_id: str,
        constraints: RunConstraints,
    ) -> AgentRun:
        with self._transaction(immediate=True) as connection:
            row = connection.execute(
                "SELECT * FROM agent_runs WHERE id = ?",
                (run_id,),
            ).fetchone()
            if row is None:
                raise AgentRecordNotFound(run_id)
            current = _run_from_row(row)
            if current.terminal or current.phase == RunPhase.COMMITTING:
                raise AgentStoreConflict(
                    "Canvas context cannot change for this Agent Run state."
                )
            if (
                constraints.canvas_context_revision
                <= current.constraints.canvas_context_revision
            ):
                raise AgentStoreConflict(
                    "Canvas context revision is stale or duplicated."
                )
            connection.execute(
                """
                UPDATE agent_runs
                SET constraints_json = ?, updated_at = ?
                WHERE id = ?
                """,
                (
                    _json_dump(constraints.model_dump(mode="json")),
                    _timestamp(datetime.now(timezone.utc)),
                    run_id,
                ),
            )
        return self.get_run(run_id)

    def list_runs(
        self,
        *,
        session_id: str | None = None,
        limit: int = 100,
    ) -> list[AgentRun]:
        _validate_limit(limit)
        with self._reader() as connection:
            if session_id is None:
                rows = connection.execute(
                    "SELECT * FROM agent_runs ORDER BY created_at DESC LIMIT ?",
                    (limit,),
                ).fetchall()
            else:
                rows = connection.execute(
                    """
                    SELECT * FROM agent_runs
                    WHERE session_id = ?
                    ORDER BY created_at DESC
                    LIMIT ?
                    """,
                    (session_id, limit),
                ).fetchall()
        return [_run_from_row(row) for row in rows]

    def interrupt_active_runs(self) -> int:
        interrupted = 0
        with self._transaction(immediate=True) as connection:
            rows = connection.execute(
                """
                SELECT * FROM agent_runs
                WHERE phase IN (?, ?, ?, ?, ?, ?, ?)
                ORDER BY created_at ASC
                """,
                (
                    RunPhase.QUEUED.value,
                    RunPhase.OBSERVING.value,
                    RunPhase.PLANNING.value,
                    RunPhase.ACTING.value,
                    RunPhase.VALIDATING.value,
                    RunPhase.TESTING.value,
                    RunPhase.COMMITTING.value,
                ),
            ).fetchall()
            for row in rows:
                current = _run_from_row(row)
                updated = current.transition_to(
                    RunPhase.INTERRUPTED,
                    error={
                        "code": "AGENT_RUN_INTERRUPTED",
                        "message": "Service restarted before the Agent Run completed.",
                    },
                )
                _update_run_row(connection, updated)
                _append_event_in_connection(
                    connection,
                    run_id=updated.id,
                    event_type="agent.paused",
                    phase=updated.phase.value,
                    message="Agent Run was interrupted by a service restart.",
                    data={"reason": "service_restart"},
                )
                interrupted += 1
        return interrupted

    def append_event(
        self,
        *,
        run_id: str,
        event_type: AgentEventType,
        phase: str,
        message: str,
        data: dict[str, Any] | None = None,
    ) -> AgentEvent:
        with self._transaction(immediate=True) as connection:
            row = connection.execute(
                "SELECT COALESCE(MAX(seq), 0) + 1 AS next_seq FROM agent_events WHERE run_id = ?",
                (run_id,),
            ).fetchone()
            seq = int(row["next_seq"])
            event = AgentEvent(
                seq=seq,
                run_id=run_id,
                type=event_type,
                phase=phase,
                message=message,
                data=redact_sensitive_data(data or {}),
            )
            payload = redact_sensitive_data(event.model_dump(mode="json"))
            persisted = AgentEvent.model_validate(payload)
            connection.execute(
                """
                INSERT INTO agent_events (
                    id, run_id, seq, event_type, payload_json, created_at
                ) VALUES (?, ?, ?, ?, ?, ?)
                """,
                (
                    persisted.id,
                    persisted.run_id,
                    persisted.seq,
                    persisted.type,
                    _json_dump(persisted.model_dump(mode="json")),
                    _timestamp(persisted.timestamp),
                ),
            )
        return persisted

    def get_event(self, run_id: str, seq: int) -> AgentEvent:
        with self._reader() as connection:
            row = connection.execute(
                "SELECT payload_json FROM agent_events WHERE run_id = ? AND seq = ?",
                (run_id, seq),
            ).fetchone()
        if row is None:
            raise AgentRecordNotFound(f"{run_id}:{seq}")
        return AgentEvent.model_validate(_json_load(row["payload_json"]))

    def list_events(
        self,
        run_id: str,
        *,
        after_seq: int = 0,
        limit: int = 1_000,
    ) -> list[AgentEvent]:
        if after_seq < 0:
            raise ValueError("after_seq must be zero or greater.")
        _validate_limit(limit, maximum=10_000)
        with self._reader() as connection:
            rows = connection.execute(
                """
                SELECT payload_json FROM agent_events
                WHERE run_id = ? AND seq > ?
                ORDER BY seq ASC
                LIMIT ?
                """,
                (run_id, after_seq, limit),
            ).fetchall()
        return [AgentEvent.model_validate(_json_load(row["payload_json"])) for row in rows]

    def create_workspace_version(self, version: WorkspaceVersion) -> WorkspaceVersion:
        with self._transaction() as connection:
            _insert_workspace_version(connection, version)
        return self.get_workspace_version(version.id)

    def initialize_run_workspace(
        self,
        run: AgentRun,
        version: WorkspaceVersion,
    ) -> tuple[AgentRun, WorkspaceVersion]:
        if run.id != version.run_id:
            raise ValueError("Workspace v0 must belong to the initialized Agent Run.")
        if version.parent_id is not None or version.patch is not None:
            raise ValueError("Workspace v0 cannot have a parent or Patch.")
        if run.head_version_id != version.id:
            raise ValueError("Initialized Agent Run head must reference Workspace v0.")
        with self._transaction(immediate=True) as connection:
            current_row = connection.execute(
                "SELECT * FROM agent_runs WHERE id = ?",
                (run.id,),
            ).fetchone()
            if current_row is None:
                raise AgentRecordNotFound(run.id)
            current = _run_from_row(current_row)
            if current.head_version_id is not None:
                raise AgentStoreConflict("Agent Run Workspace is already initialized.")
            if current.phase != run.phase:
                raise AgentStoreConflict(
                    "Agent Run phase changed before Workspace initialization."
                )
            validate_run_transition(current.phase, run.phase)
            _insert_workspace_version(connection, version)
            _update_run_row(connection, run)
        return self.get_run(run.id), self.get_workspace_version(version.id)

    def commit_workspace_version(
        self,
        version: WorkspaceVersion,
        *,
        expected_head_id: str,
        event_message: str,
        event_data: dict[str, Any] | None = None,
    ) -> tuple[AgentRun, WorkspaceVersion, AgentEvent]:
        if version.parent_id != expected_head_id:
            raise ValueError("Workspace version parent must match expected_head_id.")
        with self._transaction(immediate=True) as connection:
            run_row = connection.execute(
                "SELECT * FROM agent_runs WHERE id = ?",
                (version.run_id,),
            ).fetchone()
            if run_row is None:
                raise AgentRecordNotFound(version.run_id)
            run = _run_from_row(run_row)
            if run.terminal:
                raise AgentStoreConflict(
                    "Terminal Agent Runs cannot create new Workspace versions."
                )
            if run.phase == RunPhase.COMMITTING:
                raise AgentStoreConflict(
                    "Workspace cannot change while a Dify Commit is in progress."
                )
            if run.head_version_id != expected_head_id:
                raise AgentStoreConflict(
                    "Workspace head changed before the Patch transaction committed."
                )
            if run.base_hash != version.base_hash:
                raise AgentStoreConflict(
                    "Workspace base Hash changed before the Patch transaction committed."
                )
            _insert_workspace_version(connection, version)
            updated_run = AgentRun.model_validate(
                {
                    **run.model_dump(),
                    "head_version_id": version.id,
                    "review": None,
                    "updated_at": version.created_at,
                }
            )
            _update_run_row(connection, updated_run)
            invalidated = connection.execute(
                """
                UPDATE agent_approvals
                SET status = ?, resolved_at = ?
                WHERE run_id = ? AND status IN (?, ?)
                  AND (action != ? OR status = ?)
                """,
                (
                    ApprovalStatus.EXPIRED.value,
                    _timestamp(version.created_at),
                    version.run_id,
                    ApprovalStatus.PENDING.value,
                    ApprovalStatus.APPROVED.value,
                    "draft_run",
                    ApprovalStatus.PENDING.value,
                ),
            )
            event = _append_event_in_connection(
                connection,
                run_id=version.run_id,
                event_type="workspace.version.created",
                phase=run.phase.value,
                message=event_message,
                data={
                    "workspace_version_id": version.id,
                    "parent_id": expected_head_id,
                    "invalidated_approval_count": invalidated.rowcount,
                    **(event_data or {}),
                },
            )
        return (
            self.get_run(version.run_id),
            self.get_workspace_version(version.id),
            event,
        )

    def move_workspace_head(
        self,
        run: AgentRun,
        *,
        expected_head_id: str,
        target_head_id: str,
        event_message: str,
        event_data: dict[str, Any] | None = None,
    ) -> tuple[AgentRun, WorkspaceVersion, AgentEvent]:
        if run.head_version_id != target_head_id:
            raise ValueError("Updated Agent Run must reference the target Workspace head.")
        with self._transaction(immediate=True) as connection:
            run_row = connection.execute(
                "SELECT * FROM agent_runs WHERE id = ?",
                (run.id,),
            ).fetchone()
            if run_row is None:
                raise AgentRecordNotFound(run.id)
            current = _run_from_row(run_row)
            if current.head_version_id != expected_head_id:
                raise AgentStoreConflict(
                    "Workspace head changed before Undo moved it."
                )
            if current.phase == RunPhase.COMMITTING or current.terminal:
                raise AgentStoreConflict(
                    "Workspace head cannot move during Commit or after a terminal result."
                )
            current_version_row = connection.execute(
                "SELECT * FROM agent_workspace_versions WHERE id = ?",
                (expected_head_id,),
            ).fetchone()
            target_version_row = connection.execute(
                "SELECT * FROM agent_workspace_versions WHERE id = ?",
                (target_head_id,),
            ).fetchone()
            if current_version_row is None:
                raise AgentRecordNotFound(expected_head_id)
            if target_version_row is None:
                raise AgentRecordNotFound(target_head_id)
            current_version = _workspace_version_from_row(current_version_row)
            target_version = _workspace_version_from_row(target_version_row)
            if (
                current_version.run_id != run.id
                or target_version.run_id != run.id
                or current_version.parent_id != target_head_id
            ):
                raise AgentStoreConflict(
                    "Undo target must be the current Workspace version's parent."
                )
            validate_run_transition(current.phase, run.phase)
            _update_run_row(connection, run)
            invalidated = connection.execute(
                """
                UPDATE agent_approvals
                SET status = ?, resolved_at = ?
                WHERE run_id = ? AND status IN (?, ?)
                  AND (action != ? OR status = ?)
                """,
                (
                    ApprovalStatus.EXPIRED.value,
                    _timestamp(run.updated_at),
                    run.id,
                    ApprovalStatus.PENDING.value,
                    ApprovalStatus.APPROVED.value,
                    "draft_run",
                    ApprovalStatus.PENDING.value,
                ),
            )
            event = _append_event_in_connection(
                connection,
                run_id=run.id,
                event_type="workspace.head.moved",
                phase=run.phase.value,
                message=event_message,
                data={
                    "from_version_id": expected_head_id,
                    "workspace_version_id": target_head_id,
                    "invalidated_approval_count": invalidated.rowcount,
                    **(event_data or {}),
                },
            )
        return self.get_run(run.id), self.get_workspace_version(target_head_id), event

    def get_workspace_version(self, version_id: str) -> WorkspaceVersion:
        with self._reader() as connection:
            row = connection.execute(
                "SELECT * FROM agent_workspace_versions WHERE id = ?",
                (version_id,),
            ).fetchone()
        if row is None:
            raise AgentRecordNotFound(version_id)
        return _workspace_version_from_row(row)

    def update_workspace_version(
        self,
        version_id: str,
        *,
        validation: dict[str, Any] | None | object = _UNSET,
        test_result: dict[str, Any] | None | object = _UNSET,
    ) -> WorkspaceVersion:
        values: dict[str, Any] = {}
        if validation is not _UNSET:
            values["validation_json"] = _optional_json_dump(validation)
        if test_result is not _UNSET:
            values["test_result_json"] = _optional_json_dump(test_result)
        if not values:
            return self.get_workspace_version(version_id)
        assignments = ", ".join(f"{column} = ?" for column in values)
        with self._transaction() as connection:
            cursor = connection.execute(
                f"UPDATE agent_workspace_versions SET {assignments} WHERE id = ?",
                (*values.values(), version_id),
            )
        if cursor.rowcount == 0:
            raise AgentRecordNotFound(version_id)
        return self.get_workspace_version(version_id)

    def list_workspace_versions(
        self,
        run_id: str,
        *,
        limit: int = 1_000,
    ) -> list[WorkspaceVersion]:
        _validate_limit(limit, maximum=10_000)
        with self._reader() as connection:
            rows = connection.execute(
                """
                SELECT * FROM agent_workspace_versions
                WHERE run_id = ?
                ORDER BY created_at ASC
                LIMIT ?
                """,
                (run_id, limit),
            ).fetchall()
        return [_workspace_version_from_row(row) for row in rows]

    def get_workspace_head(self, run_id: str) -> WorkspaceVersion:
        run = self.get_run(run_id)
        if run.head_version_id is None:
            raise AgentRecordNotFound(f"{run_id}:workspace-head")
        version = self.get_workspace_version(run.head_version_id)
        if version.run_id != run.id:
            raise AgentStoreConflict("Workspace head does not belong to the Agent Run.")
        return version

    def create_approval(self, approval: AgentApproval) -> AgentApproval:
        with self._transaction() as connection:
            connection.execute(
                """
                INSERT INTO agent_approvals (
                    id, run_id, workspace_version_id, action, scope_json,
                    status, expires_at, created_at, resolved_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    approval.id,
                    approval.run_id,
                    approval.workspace_version_id,
                    approval.action,
                    _json_dump(approval.scope),
                    approval.status.value,
                    _timestamp(approval.expires_at),
                    _timestamp(approval.created_at),
                    _timestamp(approval.resolved_at),
                ),
            )
        return self.get_approval(approval.id)

    def get_approval(self, approval_id: str) -> AgentApproval:
        with self._reader() as connection:
            row = connection.execute(
                "SELECT * FROM agent_approvals WHERE id = ?",
                (approval_id,),
            ).fetchone()
        if row is None:
            raise AgentRecordNotFound(approval_id)
        return _approval_from_row(row)

    def update_approval(self, approval: AgentApproval) -> AgentApproval:
        with self._transaction() as connection:
            cursor = connection.execute(
                """
                UPDATE agent_approvals
                SET workspace_version_id = ?, action = ?, scope_json = ?,
                    status = ?, expires_at = ?, resolved_at = ?
                WHERE id = ?
                """,
                (
                    approval.workspace_version_id,
                    approval.action,
                    _json_dump(approval.scope),
                    approval.status.value,
                    _timestamp(approval.expires_at),
                    _timestamp(approval.resolved_at),
                    approval.id,
                ),
            )
        if cursor.rowcount == 0:
            raise AgentRecordNotFound(approval.id)
        return self.get_approval(approval.id)

    def list_approvals(
        self,
        run_id: str,
        *,
        limit: int = 100,
    ) -> list[AgentApproval]:
        _validate_limit(limit)
        with self._reader() as connection:
            rows = connection.execute(
                """
                SELECT * FROM agent_approvals
                WHERE run_id = ?
                ORDER BY created_at DESC
                LIMIT ?
                """,
                (run_id, limit),
            ).fetchall()
        return [_approval_from_row(row) for row in rows]

    def list_session_approvals(
        self,
        session_id: str,
        *,
        action: str | None = None,
        limit: int = 100,
    ) -> list[AgentApproval]:
        _validate_limit(limit, maximum=10_000)
        with self._reader() as connection:
            if action is None:
                rows = connection.execute(
                    """
                    SELECT approvals.*
                    FROM agent_approvals AS approvals
                    JOIN agent_runs AS runs ON runs.id = approvals.run_id
                    WHERE runs.session_id = ?
                    ORDER BY approvals.created_at DESC
                    LIMIT ?
                    """,
                    (session_id, limit),
                ).fetchall()
            else:
                rows = connection.execute(
                    """
                    SELECT approvals.*
                    FROM agent_approvals AS approvals
                    JOIN agent_runs AS runs ON runs.id = approvals.run_id
                    WHERE runs.session_id = ? AND approvals.action = ?
                    ORDER BY approvals.created_at DESC
                    LIMIT ?
                    """,
                    (session_id, action, limit),
                ).fetchall()
        return [_approval_from_row(row) for row in rows]

    def reserve_draft_run(
        self,
        *,
        run_id: str,
        approval_id: str,
        request_fingerprint: str,
    ) -> tuple[AgentRun, AgentApproval]:
        now = datetime.now(timezone.utc)
        with self._transaction(immediate=True) as connection:
            run_row = connection.execute(
                "SELECT * FROM agent_runs WHERE id = ?",
                (run_id,),
            ).fetchone()
            approval_row = connection.execute(
                "SELECT * FROM agent_approvals WHERE id = ?",
                (approval_id,),
            ).fetchone()
            if run_row is None:
                raise AgentRecordNotFound(run_id)
            if approval_row is None:
                raise AgentRecordNotFound(approval_id)
            run = _run_from_row(run_row)
            approval = _approval_from_row(approval_row)
            if run.terminal or run.phase != RunPhase.TESTING:
                raise AgentStoreConflict(
                    "Draft Run allowance can be reserved only in the testing phase."
                )
            if approval.action != "draft_run":
                raise AgentStoreConflict("Approval is not a Draft Run allowance.")
            if approval.status != ApprovalStatus.APPROVED:
                raise AgentStoreConflict("Draft Run Approval is not approved.")
            if approval.expires_at <= now:
                raise AgentStoreConflict("Draft Run Approval has expired.")
            if approval.scope.get("session_id") != run.session_id:
                raise AgentStoreConflict(
                    "Draft Run Approval is not bound to this Agent Session."
                )
            if (
                approval.scope.get("request_fingerprint")
                != request_fingerprint
            ):
                raise AgentStoreConflict(
                    "Draft Run Approval does not match the effective test request."
                )
            if bool(approval.scope.get("per_run")) and approval.run_id != run.id:
                raise AgentStoreConflict(
                    "External-side-effect Approval is bound to another Agent Run."
                )
            remaining = int(approval.scope.get("remaining_test_runs") or 0)
            if remaining < 1:
                raise AgentStoreConflict("Draft Run Approval allowance is exhausted.")
            if run.budget_usage.test_runs >= run.budget.max_test_runs:
                raise AgentStoreConflict("Agent Draft Run budget is exhausted.")
            usage = AgentBudgetUsage.model_validate(
                {
                    **run.budget_usage.model_dump(),
                    "test_runs": run.budget_usage.test_runs + 1,
                }
            )
            updated_run = AgentRun.model_validate(
                {
                    **run.model_dump(),
                    "budget_usage": usage.model_dump(),
                    "updated_at": now,
                }
            )
            scope = {
                **approval.scope,
                "remaining_test_runs": remaining - 1,
                "pending": False,
                "last_consumed_run_id": run.id,
                "last_consumed_at": now.isoformat(),
            }
            updated_approval = AgentApproval.model_validate(
                {
                    **approval.model_dump(),
                    "scope": scope,
                    "status": (
                        ApprovalStatus.CONSUMED
                        if remaining == 1
                        else ApprovalStatus.APPROVED
                    ),
                }
            )
            _update_run_row(connection, updated_run)
            _update_approval_row(connection, updated_approval)
        return self.get_run(run_id), self.get_approval(approval_id)

    def record_draft_run_cost(
        self,
        run_id: str,
        *,
        total_tokens: int,
    ) -> AgentRun:
        if total_tokens < 0:
            raise ValueError("Draft Run token usage cannot be negative.")
        if total_tokens == 0:
            return self.get_run(run_id)
        with self._transaction(immediate=True) as connection:
            row = connection.execute(
                "SELECT * FROM agent_runs WHERE id = ?",
                (run_id,),
            ).fetchone()
            if row is None:
                raise AgentRecordNotFound(run_id)
            run = _run_from_row(row)
            usage = AgentBudgetUsage.model_validate(
                {
                    **run.budget_usage.model_dump(),
                    "test_total_tokens": (
                        run.budget_usage.test_total_tokens + total_tokens
                    ),
                }
            )
            updated = AgentRun.model_validate(
                {
                    **run.model_dump(),
                    "budget_usage": usage.model_dump(),
                    "updated_at": datetime.now(timezone.utc),
                }
            )
            _update_run_row(connection, updated)
        return self.get_run(run_id)

    def finish_commit(
        self,
        *,
        run: AgentRun,
        approval: AgentApproval,
        event_message: str,
        event_data: dict[str, Any],
    ) -> tuple[AgentRun, AgentApproval, AgentEvent]:
        if approval.status != ApprovalStatus.CONSUMED:
            raise ValueError("A successful Commit must consume its Approval.")
        with self._transaction(immediate=True) as connection:
            current_run_row = connection.execute(
                "SELECT * FROM agent_runs WHERE id = ?",
                (run.id,),
            ).fetchone()
            current_approval_row = connection.execute(
                "SELECT * FROM agent_approvals WHERE id = ?",
                (approval.id,),
            ).fetchone()
            if current_run_row is None:
                raise AgentRecordNotFound(run.id)
            if current_approval_row is None:
                raise AgentRecordNotFound(approval.id)
            current_run = _run_from_row(current_run_row)
            current_approval = _approval_from_row(current_approval_row)
            validate_run_transition(current_run.phase, run.phase)
            if current_approval.status != ApprovalStatus.APPROVED:
                raise AgentStoreConflict("Commit Approval is no longer approved.")
            if current_approval.workspace_version_id != run.head_version_id:
                raise AgentStoreConflict("Commit Approval does not match the Workspace head.")
            _update_run_row(connection, run)
            _update_approval_row(connection, approval)
            event = _append_event_in_connection(
                connection,
                run_id=run.id,
                event_type="commit.completed",
                phase=run.phase.value,
                message=event_message,
                data=event_data,
            )
        return self.get_run(run.id), self.get_approval(approval.id), event

    def finish_creation_commit(
        self,
        *,
        run: AgentRun,
        approval: AgentApproval,
        session: AgentSession,
        event_message: str,
        event_data: dict[str, Any],
    ) -> tuple[AgentRun, AgentApproval, AgentSession, AgentEvent]:
        if approval.status != ApprovalStatus.CONSUMED:
            raise ValueError("A successful Commit must consume its Approval.")
        if session.id != run.session_id or session.operation != "modify":
            raise ValueError(
                "A creation Commit must promote its originating Session to modify mode."
            )
        if not session.app_id:
            raise ValueError("A completed creation Commit requires a Dify app_id.")
        with self._transaction(immediate=True) as connection:
            current_run_row = connection.execute(
                "SELECT * FROM agent_runs WHERE id = ?",
                (run.id,),
            ).fetchone()
            current_approval_row = connection.execute(
                "SELECT * FROM agent_approvals WHERE id = ?",
                (approval.id,),
            ).fetchone()
            current_session_row = connection.execute(
                "SELECT * FROM agent_sessions WHERE id = ?",
                (session.id,),
            ).fetchone()
            if current_run_row is None:
                raise AgentRecordNotFound(run.id)
            if current_approval_row is None:
                raise AgentRecordNotFound(approval.id)
            if current_session_row is None:
                raise AgentRecordNotFound(session.id)
            current_run = _run_from_row(current_run_row)
            current_approval = _approval_from_row(current_approval_row)
            current_session = _session_from_row(current_session_row)
            validate_run_transition(current_run.phase, run.phase)
            if current_approval.status != ApprovalStatus.APPROVED:
                raise AgentStoreConflict("Commit Approval is no longer approved.")
            if current_run.head_version_id != run.head_version_id:
                raise AgentStoreConflict(
                    "Creation Workspace head changed before result persistence."
                )
            if current_approval.workspace_version_id != run.head_version_id:
                raise AgentStoreConflict(
                    "Commit Approval does not match the Workspace head."
                )
            checkpoint = current_run.commit_result or {}
            final_result = run.commit_result or {}
            if (
                checkpoint.get("kind") != "create"
                or checkpoint.get("status")
                != "import_succeeded_recovery_pending"
                or checkpoint.get("idempotency_key")
                != final_result.get("idempotency_key")
            ):
                raise AgentStoreConflict(
                    "Creation result checkpoint changed before final persistence."
                )
            if current_session.operation != "create":
                raise AgentStoreConflict(
                    "Creation Commit Session changed operation mode."
                )
            if current_session.app_id not in {None, session.app_id}:
                raise AgentStoreConflict(
                    "Creation Session is already bound to a different Dify app."
                )
            _update_run_row(connection, run)
            _update_approval_row(connection, approval)
            _update_session_row(connection, session)
            event = _append_event_in_connection(
                connection,
                run_id=run.id,
                event_type="commit.completed",
                phase=run.phase.value,
                message=event_message,
                data=event_data,
            )
        return (
            self.get_run(run.id),
            self.get_approval(approval.id),
            self.get_session(session.id),
            event,
        )


def _session_from_row(row: sqlite3.Row) -> AgentSession:
    return AgentSession.model_validate(
        {
            "id": row["id"],
            "operation": row["operation"],
            "app_id": row["app_id"],
            "app_mode": row["app_mode"],
            "app_name": row["app_name"],
            "app_description": row["app_description"],
            "status": row["status"],
            "created_at": _datetime(row["created_at"]),
            "updated_at": _datetime(row["updated_at"]),
        }
    )


def _run_from_row(row: sqlite3.Row) -> AgentRun:
    return AgentRun.model_validate(
        {
            "id": row["id"],
            "session_id": row["session_id"],
            "task_id": row["task_id"],
            "goal": row["goal"],
            "status": row["status"],
            "phase": row["phase"],
            "base_hash": row["base_hash"],
            "head_version_id": row["head_version_id"],
            "iteration": row["iteration"],
            "budget": _json_load(row["budget_json"]),
            "budget_usage": _json_load(row["budget_usage_json"]) or {},
            "constraints": _json_load(row["constraints_json"]),
            "snapshot": _json_load(row["snapshot_json"]),
            "goal_plan": _json_load(row["goal_plan_json"]),
            "observations": _json_load(row["observations_json"]) or [],
            "review": _json_load(row["review_json"]),
            "commit_result": _json_load(row["commit_result_json"]),
            "error": _json_load(row["error_json"]),
            "created_at": _datetime(row["created_at"]),
            "updated_at": _datetime(row["updated_at"]),
            "finished_at": _datetime(row["finished_at"]),
        }
    )


def _workspace_version_from_row(row: sqlite3.Row) -> WorkspaceVersion:
    return WorkspaceVersion.model_validate(
        {
            "id": row["id"],
            "run_id": row["run_id"],
            "parent_id": row["parent_id"],
            "base_hash": row["base_hash"],
            "patch": _json_load(row["patch_json"]),
            "reverse_patch": _json_load(row["reverse_patch_json"]),
            "snapshot": _json_load(row["snapshot_json"]),
            "validation": _json_load(row["validation_json"]),
            "test_result": _json_load(row["test_result_json"]),
            "created_at": _datetime(row["created_at"]),
        }
    )


def _approval_from_row(row: sqlite3.Row) -> AgentApproval:
    return AgentApproval.model_validate(
        {
            "id": row["id"],
            "run_id": row["run_id"],
            "workspace_version_id": row["workspace_version_id"],
            "action": row["action"],
            "scope": _json_load(row["scope_json"]),
            "status": row["status"],
            "expires_at": _datetime(row["expires_at"]),
            "created_at": _datetime(row["created_at"]),
            "resolved_at": _datetime(row["resolved_at"]),
        }
    )


def _json_dump(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"), sort_keys=True)


def _optional_json_dump(value: Any | None) -> str | None:
    return _json_dump(value) if value is not None else None


def _json_load(value: str | None) -> Any:
    if value is None:
        return None
    return json.loads(value)


def _timestamp(value: datetime | None) -> float | None:
    return value.timestamp() if value is not None else None


def _datetime(value: float | None) -> datetime | None:
    if value is None:
        return None
    return datetime.fromtimestamp(float(value), tz=timezone.utc)


def _validate_limit(limit: int, *, maximum: int = 1_000) -> None:
    if limit < 1 or limit > maximum:
        raise ValueError(f"limit must be between 1 and {maximum}.")


def _ensure_column(
    connection: sqlite3.Connection,
    table: str,
    column: str,
    declaration: str,
) -> None:
    columns = {
        str(row["name"])
        for row in connection.execute(f"PRAGMA table_info({table})").fetchall()
    }
    if column not in columns:
        connection.execute(f"ALTER TABLE {table} ADD COLUMN {column} {declaration}")


def _insert_workspace_version(
    connection: sqlite3.Connection,
    version: WorkspaceVersion,
) -> None:
    connection.execute(
        """
        INSERT INTO agent_workspace_versions (
            id, run_id, parent_id, base_hash, patch_json,
            reverse_patch_json, snapshot_json, validation_json,
            test_result_json, created_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            version.id,
            version.run_id,
            version.parent_id,
            version.base_hash,
            _optional_json_dump(version.patch),
            _optional_json_dump(version.reverse_patch),
            _json_dump(version.snapshot),
            _optional_json_dump(version.validation),
            _optional_json_dump(version.test_result),
            _timestamp(version.created_at),
        ),
    )


def _update_run_row(connection: sqlite3.Connection, run: AgentRun) -> None:
    cursor = connection.execute(
        """
        UPDATE agent_runs
        SET task_id = ?, goal = ?, status = ?, phase = ?, base_hash = ?,
            head_version_id = ?, iteration = ?, budget_json = ?,
            budget_usage_json = ?, constraints_json = ?, snapshot_json = ?,
            goal_plan_json = ?, observations_json = ?, review_json = ?,
            commit_result_json = ?, error_json = ?, updated_at = ?,
            finished_at = ?
        WHERE id = ?
        """,
        (
            run.task_id,
            run.goal,
            run.status.value,
            run.phase.value,
            run.base_hash,
            run.head_version_id,
            run.iteration,
            _json_dump(run.budget.model_dump(mode="json")),
            _json_dump(run.budget_usage.model_dump(mode="json")),
            _json_dump(run.constraints.model_dump(mode="json")),
            _optional_json_dump(
                run.snapshot.model_dump(mode="json")
                if run.snapshot is not None
                else None
            ),
            _optional_json_dump(
                run.goal_plan.model_dump(mode="json")
                if run.goal_plan is not None
                else None
            ),
            _json_dump([item.model_dump(mode="json") for item in run.observations]),
            _optional_json_dump(run.review),
            _optional_json_dump(run.commit_result),
            _optional_json_dump(run.error),
            _timestamp(run.updated_at),
            _timestamp(run.finished_at),
            run.id,
        ),
    )
    if cursor.rowcount == 0:
        raise AgentRecordNotFound(run.id)


def _update_session_row(
    connection: sqlite3.Connection,
    session: AgentSession,
) -> None:
    cursor = connection.execute(
        """
        UPDATE agent_sessions
        SET operation = ?, app_id = ?, app_mode = ?, app_name = ?,
            app_description = ?, status = ?, updated_at = ?
        WHERE id = ?
        """,
        (
            session.operation,
            session.app_id,
            session.app_mode,
            session.app_name,
            session.app_description,
            session.status.value,
            _timestamp(session.updated_at),
            session.id,
        ),
    )
    if cursor.rowcount == 0:
        raise AgentRecordNotFound(session.id)


def _update_approval_row(
    connection: sqlite3.Connection,
    approval: AgentApproval,
) -> None:
    cursor = connection.execute(
        """
        UPDATE agent_approvals
        SET workspace_version_id = ?, action = ?, scope_json = ?,
            status = ?, expires_at = ?, resolved_at = ?
        WHERE id = ?
        """,
        (
            approval.workspace_version_id,
            approval.action,
            _json_dump(approval.scope),
            approval.status.value,
            _timestamp(approval.expires_at),
            _timestamp(approval.resolved_at),
            approval.id,
        ),
    )
    if cursor.rowcount == 0:
        raise AgentRecordNotFound(approval.id)


def _append_event_in_connection(
    connection: sqlite3.Connection,
    *,
    run_id: str,
    event_type: AgentEventType,
    phase: str,
    message: str,
    data: dict[str, Any] | None = None,
) -> AgentEvent:
    row = connection.execute(
        "SELECT COALESCE(MAX(seq), 0) + 1 AS next_seq FROM agent_events WHERE run_id = ?",
        (run_id,),
    ).fetchone()
    event = AgentEvent(
        seq=int(row["next_seq"]),
        run_id=run_id,
        type=event_type,
        phase=phase,
        message=message,
        data=redact_sensitive_data(data or {}),
    )
    payload = redact_sensitive_data(event.model_dump(mode="json"))
    persisted = AgentEvent.model_validate(payload)
    connection.execute(
        """
        INSERT INTO agent_events (
            id, run_id, seq, event_type, payload_json, created_at
        ) VALUES (?, ?, ?, ?, ?, ?)
        """,
        (
            persisted.id,
            persisted.run_id,
            persisted.seq,
            persisted.type,
            _json_dump(persisted.model_dump(mode="json")),
            _timestamp(persisted.timestamp),
        ),
    )
    return persisted
