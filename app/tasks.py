from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from datetime import datetime, timezone
import json
from pathlib import Path
import sqlite3
import threading
import time
from typing import Any, Callable
from uuid import uuid4

from fastapi import HTTPException


ACTIVE_STATUSES = {"queued", "running", "cancel_requested"}
TERMINAL_STATUSES = {"succeeded", "failed", "cancelled", "interrupted"}


class TaskCancelled(RuntimeError):
    """Raised when a background workflow task acknowledges cancellation."""


class TaskNotFound(KeyError):
    """Raised when a task id does not exist."""


@dataclass(frozen=True)
class TaskRecord:
    id: str
    operation: str
    status: str
    phase: str
    progress: int | None
    message: str
    request: dict[str, Any]
    result: dict[str, Any] | None
    error: dict[str, Any] | None
    cancel_requested: bool
    created_at: float
    started_at: float | None
    updated_at: float
    finished_at: float | None

    def to_dict(self) -> dict[str, Any]:
        return {
            "task_id": self.id,
            "operation": self.operation,
            "status": self.status,
            "phase": self.phase,
            "progress": self.progress,
            "message": self.message,
            "request": self.request,
            "result": self.result,
            "error": self.error,
            "cancel_requested": self.cancel_requested,
            "created_at": _iso_time(self.created_at),
            "started_at": _iso_time(self.started_at),
            "updated_at": _iso_time(self.updated_at),
            "finished_at": _iso_time(self.finished_at),
        }


class TaskRepository:
    def __init__(self, path: Path) -> None:
        self.path = path
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._initialize()

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.path, timeout=5.0)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA busy_timeout=5000")
        return connection

    def _initialize(self) -> None:
        with self._connect() as connection:
            connection.execute("PRAGMA journal_mode=WAL")
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS workflow_tasks (
                    id TEXT PRIMARY KEY,
                    operation TEXT NOT NULL,
                    status TEXT NOT NULL,
                    phase TEXT NOT NULL,
                    progress INTEGER,
                    message TEXT NOT NULL,
                    request_json TEXT NOT NULL,
                    result_json TEXT,
                    error_json TEXT,
                    cancel_requested INTEGER NOT NULL DEFAULT 0,
                    created_at REAL NOT NULL,
                    started_at REAL,
                    updated_at REAL NOT NULL,
                    finished_at REAL
                )
                """
            )
            connection.execute(
                "CREATE INDEX IF NOT EXISTS idx_workflow_tasks_status ON workflow_tasks(status)"
            )
            connection.execute(
                "CREATE INDEX IF NOT EXISTS idx_workflow_tasks_created_at ON workflow_tasks(created_at DESC)"
            )

    def create(self, operation: str, request: dict[str, Any]) -> TaskRecord:
        now = time.time()
        task_id = str(uuid4())
        with self._connect() as connection:
            connection.execute(
                """
                INSERT INTO workflow_tasks (
                    id, operation, status, phase, progress, message, request_json,
                    created_at, updated_at
                ) VALUES (?, ?, 'queued', 'queued', 0, 'Waiting for a worker.', ?, ?, ?)
                """,
                (task_id, operation, _json_dump(request), now, now),
            )
        return self.get(task_id)

    def get(self, task_id: str) -> TaskRecord:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT * FROM workflow_tasks WHERE id = ?",
                (task_id,),
            ).fetchone()
        if row is None:
            raise TaskNotFound(task_id)
        return _record_from_row(row)

    def update(
        self,
        task_id: str,
        *,
        status: str | None = None,
        phase: str | None = None,
        progress: int | None | object = ...,
        message: str | None = None,
        result: dict[str, Any] | None | object = ...,
        error: dict[str, Any] | None | object = ...,
        started_at: float | None | object = ...,
        finished_at: float | None | object = ...,
        cancel_requested: bool | None = None,
    ) -> TaskRecord:
        values: dict[str, Any] = {"updated_at": time.time()}
        if status is not None:
            values["status"] = status
        if phase is not None:
            values["phase"] = phase
        if progress is not ...:
            values["progress"] = progress
        if message is not None:
            values["message"] = message
        if result is not ...:
            values["result_json"] = _json_dump(result) if result is not None else None
        if error is not ...:
            values["error_json"] = _json_dump(error) if error is not None else None
        if started_at is not ...:
            values["started_at"] = started_at
        if finished_at is not ...:
            values["finished_at"] = finished_at
        if cancel_requested is not None:
            values["cancel_requested"] = int(cancel_requested)
        assignments = ", ".join(f"{column} = ?" for column in values)
        with self._connect() as connection:
            cursor = connection.execute(
                f"UPDATE workflow_tasks SET {assignments} WHERE id = ?",
                (*values.values(), task_id),
            )
        if cursor.rowcount == 0:
            raise TaskNotFound(task_id)
        return self.get(task_id)

    def request_cancel(self, task_id: str) -> tuple[TaskRecord, bool]:
        record = self.get(task_id)
        if record.status in TERMINAL_STATUSES:
            return record, False
        updated = self.update(
            task_id,
            status="cancel_requested",
            phase="cancelling",
            message="Cancellation requested. Waiting for the current operation to stop.",
            cancel_requested=True,
        )
        return updated, True

    def cancellation_requested(self, task_id: str) -> bool:
        return self.get(task_id).cancel_requested

    def interrupt_active(self) -> int:
        now = time.time()
        with self._connect() as connection:
            cursor = connection.execute(
                """
                UPDATE workflow_tasks
                SET status = 'interrupted',
                    phase = 'interrupted',
                    message = 'Service restarted before the task completed.',
                    finished_at = ?,
                    updated_at = ?
                WHERE status IN ('queued', 'running', 'cancel_requested')
                """,
                (now, now),
            )
        return cursor.rowcount

    def cleanup(self, *, max_age_days: int = 7, max_terminal_records: int = 200) -> None:
        cutoff = time.time() - (max_age_days * 86400)
        terminal_placeholders = ", ".join("?" for _ in TERMINAL_STATUSES)
        statuses = tuple(sorted(TERMINAL_STATUSES))
        with self._connect() as connection:
            connection.execute(
                f"""
                DELETE FROM workflow_tasks
                WHERE status IN ({terminal_placeholders})
                  AND COALESCE(finished_at, updated_at) < ?
                """,
                (*statuses, cutoff),
            )
            rows = connection.execute(
                f"""
                SELECT id FROM workflow_tasks
                WHERE status IN ({terminal_placeholders})
                ORDER BY COALESCE(finished_at, updated_at) DESC
                LIMIT -1 OFFSET ?
                """,
                (*statuses, max_terminal_records),
            ).fetchall()
            if rows:
                connection.executemany(
                    "DELETE FROM workflow_tasks WHERE id = ?",
                    [(row["id"],) for row in rows],
                )


class TaskContext:
    def __init__(self, repository: TaskRepository, task_id: str) -> None:
        self.repository = repository
        self.task_id = task_id

    def update(self, phase: str, progress: int | None, message: str) -> None:
        self.raise_if_cancelled()
        self.repository.update(
            self.task_id,
            status="running",
            phase=phase,
            progress=progress,
            message=message,
        )

    def raise_if_cancelled(self) -> None:
        if self.repository.cancellation_requested(self.task_id):
            raise TaskCancelled("Task cancellation requested.")


TaskCallable = Callable[[TaskContext], dict[str, Any]]


class TaskManager:
    def __init__(self, repository: TaskRepository, *, workers: int = 2) -> None:
        self.repository = repository
        self.executor = ThreadPoolExecutor(max_workers=workers, thread_name_prefix="chat2dify-task")
        self._closed = False
        self._lock = threading.Lock()
        self.repository.interrupt_active()
        self.repository.cleanup()

    def submit(self, operation: str, request: dict[str, Any], callback: TaskCallable) -> TaskRecord:
        with self._lock:
            if self._closed:
                raise RuntimeError("Task manager is closed.")
            record = self.repository.create(operation, request)
            self.executor.submit(self._run, record.id, callback)
            return record

    def get(self, task_id: str) -> TaskRecord:
        return self.repository.get(task_id)

    def cancel(self, task_id: str) -> tuple[TaskRecord, bool]:
        return self.repository.request_cancel(task_id)

    def close(self) -> None:
        with self._lock:
            self._closed = True
        self.executor.shutdown(wait=False, cancel_futures=False)

    def _run(self, task_id: str, callback: TaskCallable) -> None:
        now = time.time()
        context = TaskContext(self.repository, task_id)
        try:
            context.raise_if_cancelled()
            self.repository.update(
                task_id,
                status="running",
                phase="starting",
                progress=0,
                message="Task started.",
                started_at=now,
            )
            result = callback(context)
            self.repository.update(
                task_id,
                status="succeeded",
                phase="completed",
                progress=100,
                message="Task completed.",
                result=result,
                finished_at=time.time(),
            )
        except TaskCancelled as exc:
            self.repository.update(
                task_id,
                status="cancelled",
                phase="cancelled",
                message=str(exc),
                finished_at=time.time(),
            )
        except HTTPException as exc:
            self.repository.update(
                task_id,
                status="failed",
                phase="failed",
                message="Task failed.",
                error={"status_code": exc.status_code, "detail": exc.detail},
                finished_at=time.time(),
            )
        except Exception as exc:  # noqa: BLE001 - task failures must be persisted for polling clients.
            self.repository.update(
                task_id,
                status="failed",
                phase="failed",
                message="Task failed.",
                error={"status_code": 500, "detail": str(exc), "type": exc.__class__.__name__},
                finished_at=time.time(),
            )


def _record_from_row(row: sqlite3.Row) -> TaskRecord:
    return TaskRecord(
        id=str(row["id"]),
        operation=str(row["operation"]),
        status=str(row["status"]),
        phase=str(row["phase"]),
        progress=int(row["progress"]) if row["progress"] is not None else None,
        message=str(row["message"]),
        request=_json_load(row["request_json"]) or {},
        result=_json_load(row["result_json"]),
        error=_json_load(row["error_json"]),
        cancel_requested=bool(row["cancel_requested"]),
        created_at=float(row["created_at"]),
        started_at=float(row["started_at"]) if row["started_at"] is not None else None,
        updated_at=float(row["updated_at"]),
        finished_at=float(row["finished_at"]) if row["finished_at"] is not None else None,
    )


def _json_dump(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"))


def _json_load(value: str | None) -> Any:
    if value is None:
        return None
    return json.loads(value)


def _iso_time(value: float | None) -> str | None:
    if value is None:
        return None
    return datetime.fromtimestamp(value, tz=timezone.utc).isoformat().replace("+00:00", "Z")
