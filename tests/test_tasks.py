from __future__ import annotations

from pathlib import Path
import threading
import time

from fastapi import HTTPException

from app.tasks import TaskManager, TaskRepository


def _wait_for_terminal(manager: TaskManager, task_id: str, *, timeout: float = 2.0):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        record = manager.get(task_id)
        if record.status in {"succeeded", "failed", "cancelled", "interrupted"}:
            return record
        time.sleep(0.01)
    raise AssertionError(f"Task {task_id} did not finish before timeout.")


def test_task_repository_interrupts_active_records_on_restart(tmp_path: Path) -> None:
    repository = TaskRepository(tmp_path / "tasks.sqlite3")
    queued = repository.create("workflow.create", {"message": "hello"})
    repository.update(
        queued.id,
        status="running",
        phase="planning",
        progress=20,
        started_at=time.time(),
    )

    manager = TaskManager(TaskRepository(tmp_path / "tasks.sqlite3"), workers=1)
    try:
        record = manager.get(queued.id)
    finally:
        manager.close()

    assert record.status == "interrupted"
    assert record.phase == "interrupted"
    assert record.finished_at is not None


def test_task_manager_persists_success_and_http_error(tmp_path: Path) -> None:
    manager = TaskManager(TaskRepository(tmp_path / "tasks.sqlite3"), workers=1)
    try:
        success = manager.submit(
            "workflow.create",
            {"message": "hello"},
            lambda context: {"ok": True, "task_id": context.task_id},
        )
        success_record = _wait_for_terminal(manager, success.id)

        def fail(_context):
            raise HTTPException(status_code=422, detail={"code": "BAD_PLAN"})

        failure = manager.submit("workflow.create", {"message": "bad"}, fail)
        failure_record = _wait_for_terminal(manager, failure.id)
    finally:
        manager.close()

    assert success_record.status == "succeeded"
    assert success_record.progress == 100
    assert success_record.result == {"ok": True, "task_id": success.id}
    assert failure_record.status == "failed"
    assert failure_record.error == {"status_code": 422, "detail": {"code": "BAD_PLAN"}}


def test_task_manager_cooperatively_cancels_running_task(tmp_path: Path) -> None:
    entered = threading.Event()
    release = threading.Event()
    manager = TaskManager(TaskRepository(tmp_path / "tasks.sqlite3"), workers=1)

    def work(context):
        entered.set()
        while not release.wait(0.01):
            context.raise_if_cancelled()
        context.raise_if_cancelled()
        return {"ok": True}

    try:
        submitted = manager.submit("workflow.run.draft", {"app_id": "app-1"}, work)
        assert entered.wait(1)
        cancelling, accepted = manager.cancel(submitted.id)
        record = _wait_for_terminal(manager, submitted.id)
        terminal, accepted_again = manager.cancel(submitted.id)
    finally:
        release.set()
        manager.close()

    assert accepted is True
    assert cancelling.status == "cancel_requested"
    assert record.status == "cancelled"
    assert accepted_again is False
    assert terminal.status == "cancelled"
