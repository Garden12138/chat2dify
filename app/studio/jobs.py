from __future__ import annotations

from dataclasses import dataclass, field
from datetime import timedelta
from hashlib import sha256
from threading import Event, Lock, Thread
import time
from typing import Any, Callable, Mapping, Protocol

from app.studio.artifacts import assert_secret_free
from app.studio.identity import AuthenticatedStudioRequest
from app.studio.models import (
    DurableJob,
    ExternalReceipt,
    OutboxMessage,
    Principal,
    StudioSession,
    VerifiedHostContext,
    utc_now,
)
from app.studio.store import StudioConflict, StudioStore


class DurableWorkerError(RuntimeError):
    code = "STUDIO_DURABLE_WORKER_ERROR"


class DefiniteWorkerFailure(DurableWorkerError):
    code = "STUDIO_WORKER_DEFINITE_FAILURE"


class AmbiguousWorkerOutcome(DurableWorkerError):
    code = "STUDIO_WORKER_AMBIGUOUS_OUTCOME"


@dataclass(frozen=True)
class WorkerResult:
    outcome: str = "succeeded"
    external_ref: str | None = None
    details: dict[str, Any] = field(default_factory=dict)


class WorkHandler(Protocol):
    def __call__(
        self,
        payload: dict[str, Any],
        context: "WorkContext",
    ) -> WorkerResult: ...


@dataclass(frozen=True)
class WorkContext:
    project_id: str
    entity_type: str
    entity_id: str
    idempotency_key: str
    attempt: int
    cancelled: Callable[[], bool]


class StudioDurableWorker:
    def __init__(
        self,
        *,
        store: StudioStore,
        worker_id: str,
        job_handlers: Mapping[str, WorkHandler] | None = None,
        notification_adapters: Mapping[str, WorkHandler] | None = None,
        lease_seconds: int = 30,
        heartbeat_seconds: float = 5.0,
    ) -> None:
        self.store = store
        self.worker_id = worker_id
        self.job_handlers = dict(job_handlers or {})
        self.notification_adapters = dict(notification_adapters or {})
        self.lease_seconds = max(2, lease_seconds)
        self.heartbeat_seconds = max(
            0.2,
            min(heartbeat_seconds, self.lease_seconds / 2),
        )

    def run_once(self) -> bool:
        self.store.reconcile_exhausted_work()
        job = self.store.claim_job(
            worker_id=self.worker_id,
            lease_seconds=self.lease_seconds,
        )
        if job is not None:
            self._process_job(job)
            return True
        message = self.store.claim_outbox(
            worker_id=self.worker_id,
            lease_seconds=self.lease_seconds,
        )
        if message is not None:
            self._process_outbox(message)
            return True
        return False

    def _process_job(self, job: DurableJob) -> None:
        handler = self.job_handlers.get(job.kind)
        self._process(
            entity_type="job",
            item=job,
            handler=handler,
        )

    def _process_outbox(self, message: OutboxMessage) -> None:
        adapter_ref = str(message.payload.get("adapter_ref") or "")
        handler = self.notification_adapters.get(adapter_ref)
        self._process(
            entity_type="outbox",
            item=message,
            handler=handler,
        )

    def _process(
        self,
        *,
        entity_type: str,
        item: DurableJob | OutboxMessage,
        handler: WorkHandler | None,
    ) -> None:
        if self._cancelled(entity_type, item):
            self._finish(entity_type, item, "cancelled")
            return
        receipt, created = self.store.begin_worker_receipt(
            entity_type=entity_type,
            entity_id=item.id,
            worker_id=self.worker_id,
            expected_version=item.version,
        )
        if not created:
            terminal = "completed" if receipt.outcome == "succeeded" else "ambiguous"
            self._finish(entity_type, item, terminal)
            return
        if handler is None:
            self.store.complete_worker_receipt(
                receipt_id=receipt.id,
                outcome="failed",
                external_ref=None,
                details={
                    "code": "STUDIO_WORKER_HANDLER_UNAVAILABLE",
                    "message": "No configured handler can perform this bounded work.",
                },
            )
            self._finish(entity_type, item, "dead_letter")
            return
        heartbeat = _LeaseHeartbeat(
            store=self.store,
            entity_type=entity_type,
            item=item,
            worker_id=self.worker_id,
            lease_seconds=self.lease_seconds,
            interval_seconds=self.heartbeat_seconds,
        )
        heartbeat.start()
        try:
            if self._cancelled(entity_type, item):
                self.store.complete_worker_receipt(
                    receipt_id=receipt.id,
                    outcome="failed",
                    external_ref=None,
                    details={"code": "STUDIO_WORK_CANCELLED_BEFORE_EXTERNAL_CALL"},
                )
                heartbeat.stop()
                item = heartbeat.item
                self._finish(entity_type, item, "cancelled")
                return
            context = WorkContext(
                project_id=item.project_id,
                entity_type=entity_type,
                entity_id=item.id,
                idempotency_key=item.idempotency_key,
                attempt=item.attempts,
                cancelled=lambda: self._cancelled(entity_type, item),
            )
            result = handler(dict(item.payload), context)
            if result.outcome not in {"succeeded", "failed", "ambiguous"}:
                raise AmbiguousWorkerOutcome(
                    "The handler returned an unsupported external outcome."
                )
            assert_secret_free(result.details)
            self.store.complete_worker_receipt(
                receipt_id=receipt.id,
                outcome=result.outcome,
                external_ref=result.external_ref,
                details=result.details,
            )
            heartbeat.stop()
            item = heartbeat.item
            if result.outcome == "succeeded":
                self._finish(entity_type, item, "completed")
            elif result.outcome == "failed":
                self._retry(entity_type, item)
            else:
                self._finish(entity_type, item, "ambiguous")
        except DefiniteWorkerFailure as exc:
            heartbeat.stop()
            item = heartbeat.item
            self.store.complete_worker_receipt(
                receipt_id=receipt.id,
                outcome="failed",
                external_ref=None,
                details={"code": exc.code, "message": str(exc)[:500]},
            )
            self._retry(entity_type, item)
        except Exception as exc:
            heartbeat.stop()
            item = heartbeat.item
            try:
                self.store.complete_worker_receipt(
                    receipt_id=receipt.id,
                    outcome="ambiguous",
                    external_ref=None,
                    details={
                        "code": getattr(
                            exc,
                            "code",
                            "STUDIO_WORKER_EXTERNAL_OUTCOME_UNKNOWN",
                        ),
                        "message": "External work did not return a definite receipt.",
                    },
                )
            except StudioConflict:
                pass
            self._finish(entity_type, item, "ambiguous")
        finally:
            heartbeat.stop()

    def _cancelled(
        self,
        entity_type: str,
        item: DurableJob | OutboxMessage,
    ) -> bool:
        return self.store.work_cancel_requested(
            project_id=item.project_id,
            entity_type=entity_type,
            entity_id=item.id,
        )

    def _finish(
        self,
        entity_type: str,
        item: DurableJob | OutboxMessage,
        outcome: str,
    ) -> None:
        if entity_type == "job":
            self.store.finish_job(
                job_id=item.id,
                worker_id=self.worker_id,
                expected_version=item.version,
                outcome=outcome,
            )
        else:
            self.store.finish_outbox(
                message_id=item.id,
                worker_id=self.worker_id,
                expected_version=item.version,
                outcome=outcome,
            )

    def _retry(
        self,
        entity_type: str,
        item: DurableJob | OutboxMessage,
    ) -> None:
        if entity_type == "job":
            self.store.retry_or_dead_letter_job(
                job_id=item.id,
                worker_id=self.worker_id,
                expected_version=item.version,
            )
        else:
            self.store.retry_or_dead_letter_outbox(
                message_id=item.id,
                worker_id=self.worker_id,
                expected_version=item.version,
            )


class StudioWorkerLoop:
    def __init__(
        self,
        *,
        worker: StudioDurableWorker,
        poll_seconds: float = 0.5,
    ) -> None:
        self.worker = worker
        self.poll_seconds = max(0.1, poll_seconds)
        self._stop = Event()
        self._thread = Thread(
            target=self._run,
            name=f"studio-worker-{worker.worker_id}",
            daemon=True,
        )

    def start(self) -> None:
        self._thread.start()

    def close(self, timeout: float = 10.0) -> None:
        self._stop.set()
        self._thread.join(timeout=max(0.1, timeout))

    def _run(self) -> None:
        while not self._stop.is_set():
            try:
                did_work = self.worker.run_once()
            except Exception:
                did_work = False
            if not did_work:
                self._stop.wait(self.poll_seconds)


class _LeaseHeartbeat:
    def __init__(
        self,
        *,
        store: StudioStore,
        entity_type: str,
        item: DurableJob | OutboxMessage,
        worker_id: str,
        lease_seconds: int,
        interval_seconds: float,
    ) -> None:
        self.store = store
        self.entity_type = entity_type
        self._item = item
        self.worker_id = worker_id
        self.lease_seconds = lease_seconds
        self.interval_seconds = interval_seconds
        self._stop = Event()
        self._lock = Lock()
        self._thread = Thread(target=self._run, daemon=True)
        self._started = False

    @property
    def item(self) -> DurableJob | OutboxMessage:
        with self._lock:
            return self._item

    def start(self) -> None:
        self._started = True
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        if self._started:
            self._thread.join(timeout=max(1.0, self.interval_seconds * 2))

    def _run(self) -> None:
        while not self._stop.wait(self.interval_seconds):
            try:
                with self._lock:
                    current = self._item
                if self.entity_type == "job":
                    updated = self.store.heartbeat_job(
                        job_id=current.id,
                        worker_id=self.worker_id,
                        expected_version=current.version,
                        lease_seconds=self.lease_seconds,
                    )
                else:
                    updated = self.store.heartbeat_outbox(
                        message_id=current.id,
                        worker_id=self.worker_id,
                        expected_version=current.version,
                        lease_seconds=self.lease_seconds,
                    )
                with self._lock:
                    self._item = updated
            except Exception:
                return


def local_audit_notification(
    payload: dict[str, Any],
    context: WorkContext,
) -> WorkerResult:
    if context.cancelled():
        raise DefiniteWorkerFailure("Notification was cancelled before delivery.")
    return WorkerResult(
        outcome="succeeded",
        external_ref=f"local-audit:{context.idempotency_key}",
        details={
            "adapter": "audit:local",
            "delivered": True,
            "sanitized": bool(payload.get("sanitized")),
            "stable_error_code": payload.get("stable_error_code"),
            "failures": payload.get("failures"),
        },
    )


def scheduled_regression_handler(
    *,
    store: StudioStore,
    scenario_service: Any,
) -> WorkHandler:
    def handle(
        payload: dict[str, Any],
        context: WorkContext,
    ) -> WorkerResult:
        if payload.get("production_write") is not False:
            raise DefiniteWorkerFailure(
                "Scheduled regression payload did not preserve the Preview-only boundary."
            )
        if context.cancelled():
            raise DefiniteWorkerFailure(
                "Scheduled regression was cancelled before Preview work."
            )
        authorized_by = str(payload.get("authorized_by") or "")
        if not authorized_by:
            raise DefiniteWorkerFailure(
                "Scheduled regression has no persisted human configuration actor."
            )
        authenticated = project_service_request(
            store=store,
            project_id=context.project_id,
            principal_key=authorized_by,
            purpose=f"scheduled-regression:{context.entity_id}",
        )
        try:
            run = scenario_service.run_released_artifact(
                authenticated,
                project_id=context.project_id,
                artifact_id=str(payload.get("artifact_id") or ""),
                suite_id=str(payload.get("suite_id") or ""),
            )
        except Exception as exc:
            code = str(getattr(exc, "code", ""))
            if code.startswith("STUDIO_") or code.startswith("SCENARIO_"):
                raise DefiniteWorkerFailure(str(exc)) from exc
            raise
        details = {
            "scenario_run_id": run.id,
            "status": run.status,
            "cleanup_verified": run.cleanup_verified,
            "candidate_count": len(run.candidate_ids),
            "report_count": len(run.reports),
            "production_write": False,
        }
        if run.status == "completed" and run.cleanup_verified:
            return WorkerResult(
                outcome="succeeded",
                external_ref=run.id,
                details=details,
            )
        if run.status == "reconciliation_required":
            return WorkerResult(
                outcome="ambiguous",
                external_ref=run.id,
                details=details,
            )
        return WorkerResult(
            outcome="failed",
            external_ref=run.id,
            details=details,
        )

    return handle


def scenario_run_handler(
    *,
    store: StudioStore,
    scenario_service: Any,
) -> WorkHandler:
    def handle(
        payload: dict[str, Any],
        context: WorkContext,
    ) -> WorkerResult:
        if payload.get("production_write") is not False:
            raise DefiniteWorkerFailure(
                "Scenario job did not preserve the Preview-only boundary."
            )
        authorized_by = str(payload.get("authorized_by") or "")
        versions = payload.get("candidate_versions")
        if not authorized_by or not isinstance(versions, dict):
            raise DefiniteWorkerFailure(
                "Scenario job is missing its persisted actor or Candidate versions."
            )
        authenticated = project_service_request(
            store=store,
            project_id=context.project_id,
            principal_key=authorized_by,
            purpose=f"scenario-run:{context.entity_id}",
        )
        try:
            run = scenario_service.execute_durable_run(
                authenticated,
                project_id=context.project_id,
                run_id=str(payload.get("scenario_run_id") or ""),
                candidate_versions={str(key): str(value) for key, value in versions.items()},
            )
        except Exception as exc:
            code = str(getattr(exc, "code", ""))
            if code.startswith("STUDIO_") or code.startswith("SCENARIO_"):
                raise DefiniteWorkerFailure(str(exc)) from exc
            raise
        details = {
            "scenario_run_id": run.id,
            "status": run.status,
            "cleanup_verified": run.cleanup_verified,
            "report_count": len(run.reports),
            "production_write": False,
        }
        if run.status == "completed" and run.cleanup_verified:
            return WorkerResult(
                outcome="succeeded",
                external_ref=run.id,
                details=details,
            )
        if run.status == "reconciliation_required":
            return WorkerResult(
                outcome="ambiguous",
                external_ref=run.id,
                details=details,
            )
        return WorkerResult(
            outcome="failed",
            external_ref=run.id,
            details=details,
        )

    return handle


def build_agent_run_handler(*, agent_service: Any) -> WorkHandler:
    def handle(
        payload: dict[str, Any],
        context: WorkContext,
    ) -> WorkerResult:
        if (
            payload.get("workspace_only") is not True
            or payload.get("production_write") is not False
        ):
            raise DefiniteWorkerFailure(
                "Durable Build job did not preserve its Workspace-only boundary."
            )
        run_id = str(payload.get("run_id") or "")
        if not run_id:
            raise DefiniteWorkerFailure("Durable Build job has no Agent Run.")
        if context.cancelled():
            try:
                agent_service.cancel(run_id)
            except Exception:
                pass
            raise DefiniteWorkerFailure("Durable Build job was cancelled.")
        run = agent_service.store.get_run(run_id)
        if run.constraints.workspace_only is not True:
            raise DefiniteWorkerFailure(
                "Agent Run is not constrained to a Workspace-only Candidate."
            )
        try:
            completed = agent_service.execute_run(run_id)
        except Exception:
            # Agent Runtime persists its own stable failure state. If the
            # process did not return, the outer worker marks the receipt
            # ambiguous and will not replay it blindly.
            raise
        return WorkerResult(
            outcome="succeeded",
            external_ref=run_id,
            details={
                "run_id": run_id,
                "candidate_id": payload.get("candidate_id"),
                "phase": completed.phase.value,
                "workspace_only": True,
                "production_write": False,
            },
        )

    return handle


def release_execute_handler(
    *,
    store: StudioStore,
    release_service: Any,
) -> WorkHandler:
    def handle(
        payload: dict[str, Any],
        context: WorkContext,
    ) -> WorkerResult:
        authorized_by = str(payload.get("authorized_by") or "")
        record_id = str(payload.get("record_id") or "")
        action = str(payload.get("action") or "")
        if (
            not authorized_by
            or not record_id
            or action not in {"apply_draft", "publish"}
            or payload.get("human_gate_consumed") is not True
        ):
            raise DefiniteWorkerFailure(
                "Release job is not bound to a consumed exact human authorization."
            )
        authenticated = project_service_request(
            store=store,
            project_id=context.project_id,
            principal_key=authorized_by,
            purpose=f"release-delivery:{record_id}",
        )
        if context.cancelled():
            release_service.cancel_durable(
                authenticated,
                project_id=context.project_id,
                record_id=record_id,
            )
            raise DefiniteWorkerFailure(
                "Release delivery was cancelled before the external write."
            )
        try:
            record = release_service.execute_durable(
                authenticated,
                project_id=context.project_id,
                record_id=record_id,
            )
        except Exception as exc:
            code = str(getattr(exc, "code", ""))
            if code.startswith("STUDIO_"):
                raise DefiniteWorkerFailure(str(exc)) from exc
            raise
        details = {
            "release_record_id": record.id,
            "action": record.action,
            "outcome": record.outcome,
            "human_gate_consumed": True,
            "automatic_publish": False,
        }
        if record.outcome == "succeeded":
            return WorkerResult(
                outcome="succeeded",
                external_ref=record.external_ref,
                details=details,
            )
        if record.outcome == "ambiguous":
            return WorkerResult(
                outcome="ambiguous",
                external_ref=record.external_ref,
                details=details,
            )
        return WorkerResult(
            outcome="failed",
            external_ref=record.external_ref,
            details=details,
        )

    return handle


def project_service_request(
    *,
    store: StudioStore,
    project_id: str,
    principal_key: str,
    purpose: str,
) -> AuthenticatedStudioRequest:
    project, membership = store.get_project_for_principal(
        project_id,
        principal_key,
    )
    issuer, _, subject = principal_key.partition(":")
    principal = Principal(
        issuer=issuer or "chat2dify-studio",
        subject=subject or principal_key,
        display_name=f"Durable worker: {purpose[:80]}",
        dify_tenant_id=project.dify_tenant_id,
    )
    now = utc_now()
    digest = sha256(
        f"{project_id}:{principal_key}:{purpose}".encode("utf-8")
    ).hexdigest()
    session = StudioSession(
        id=f"worker:{digest[:32]}",
        jti_hash=digest,
        principal_key=principal.key,
        project_id=project.id,
        dify_account_id=principal.subject,
        dify_tenant_id=project.dify_tenant_id,
        origin="worker://studio-durable-job",
        nonce_hash=digest,
        expires_at=now + timedelta(hours=1),
        created_at=now,
    )
    return AuthenticatedStudioRequest(
        claims={"worker_purpose": purpose},
        session=session,
        principal=principal,
        project=project,
        membership=membership,
        host=VerifiedHostContext(
            principal=principal,
            apps=[],
            apps_available=False,
            apps_error_code="WORKER_HOST_CONTEXT_NOT_FORWARDED",
        ),
    )
