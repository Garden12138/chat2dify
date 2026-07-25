from __future__ import annotations

from contextlib import contextmanager
from datetime import datetime, timezone
import json
from pathlib import Path
import sqlite3
from typing import Any, Iterator

from app.agent.state import (
    AgentApproval,
    AgentRun,
    AgentSession,
    WorkspaceVersion,
    validate_run_transition,
)
from app.agent.trace import AgentEvent, AgentEventType, redact_sensitive_data


class AgentRecordNotFound(KeyError):
    """Raised when a requested v4 Agent record does not exist."""


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
                    app_id TEXT,
                    app_mode TEXT,
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
                    constraints_json TEXT NOT NULL,
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

    def create_session(self, session: AgentSession) -> AgentSession:
        with self._transaction() as connection:
            connection.execute(
                """
                INSERT INTO agent_sessions (
                    id, app_id, app_mode, status, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?)
                """,
                (
                    session.id,
                    session.app_id,
                    session.app_mode,
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
                SET app_id = ?, app_mode = ?, status = ?, updated_at = ?
                WHERE id = ?
                """,
                (
                    session.app_id,
                    session.app_mode,
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
                    head_version_id, iteration, budget_json, constraints_json,
                    error_json, created_at, updated_at, finished_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
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
                    _json_dump(run.constraints.model_dump(mode="json")),
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
                    constraints_json = ?, error_json = ?, updated_at = ?,
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
                    _json_dump(run.constraints.model_dump(mode="json")),
                    _json_dump(run.error) if run.error is not None else None,
                    _timestamp(run.updated_at),
                    _timestamp(run.finished_at),
                    run.id,
                ),
            )
        if cursor.rowcount == 0:
            raise AgentRecordNotFound(run.id)
        return self.get_run(run.id)

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
        return self.get_workspace_version(version.id)

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


def _session_from_row(row: sqlite3.Row) -> AgentSession:
    return AgentSession.model_validate(
        {
            "id": row["id"],
            "app_id": row["app_id"],
            "app_mode": row["app_mode"],
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
            "constraints": _json_load(row["constraints_json"]),
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
