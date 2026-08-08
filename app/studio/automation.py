from __future__ import annotations

from datetime import timedelta
import math
import re
from typing import Iterable

from app.studio.artifacts import assert_secret_free
from app.studio.identity import AuthenticatedStudioRequest
from app.studio.models import (
    RunAlertRule,
    RunAutomationView,
    ScheduledRegression,
    new_id,
    utc_now,
)
from app.studio.store import StudioAccessDenied, StudioConflict, StudioStore


class RunAutomationError(RuntimeError):
    code = "STUDIO_RUN_AUTOMATION_ERROR"


class AutomationConfigurationInvalid(RunAutomationError):
    code = "STUDIO_AUTOMATION_CONFIGURATION_INVALID"


_OPAQUE_ADAPTER_REF = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,255}$")


class StudioRunAutomationService:
    def __init__(
        self,
        *,
        store: StudioStore,
        available_adapter_refs: Iterable[str] = (),
    ) -> None:
        self.store = store
        self.available_adapter_refs = frozenset(available_adapter_refs)

    def view(
        self,
        authenticated: AuthenticatedStudioRequest,
        *,
        project_id: str,
    ) -> RunAutomationView:
        _, membership = self.store.get_project_for_principal(
            project_id,
            authenticated.principal.key,
        )
        rules = self.store.list_run_alert_rules(
            project_id=project_id,
            principal_key=authenticated.principal.key,
        )
        schedules = self.store.list_scheduled_regressions(
            project_id=project_id,
            principal_key=authenticated.principal.key,
        )
        outbox = self.store.list_outbox(
            project_id=project_id,
            principal_key=authenticated.principal.key,
        )
        jobs = self.store.list_jobs(
            project_id=project_id,
            principal_key=authenticated.principal.key,
        )
        schedule_targets: list[dict[str, str]] = []
        seen_artifacts: set[str] = set()
        for release in self.store.list_release_records(
            project_id=project_id,
            principal_key=authenticated.principal.key,
        ):
            if (
                release.action != "publish"
                or release.outcome != "succeeded"
                or release.artifact_id in seen_artifacts
            ):
                continue
            artifact = self.store.get_workflow_artifact(
                release.artifact_id,
                project_id=project_id,
                principal_key=authenticated.principal.key,
            )
            binding = artifact.payload.scenario_evidence.get("binding") or {}
            suite_id = str(binding.get("suite_id") or "")
            if not suite_id:
                continue
            suite = self.store.get_scenario_suite(
                suite_id,
                project_id=project_id,
                principal_key=authenticated.principal.key,
            )
            seen_artifacts.add(artifact.id)
            schedule_targets.append(
                {
                    "artifact_id": artifact.id,
                    "artifact_hash": artifact.content_hash,
                    "suite_id": suite.id,
                    "suite_name": suite.name,
                    "suite_version": suite.semantic_version,
                    "release_note": release.release_note,
                }
            )
        enabled = [item for item in rules if item.enabled]
        missing = [
            item.adapter_ref
            for item in enabled
            if item.adapter_ref not in self.available_adapter_refs
        ]
        if not enabled:
            adapter_state = "disabled"
            message = "尚未启用告警；不会生成虚构通知回执。"
        elif missing:
            adapter_state = "missing"
            message = (
                "告警规则已保存，但通知 Adapter 当前不可用；"
                "阈值事件会保留在 Outbox，等待管理员对账。"
            )
        else:
            adapter_state = "configured"
            message = "告警 Adapter 已配置；发送仍由幂等 Outbox Worker 完成。"
        return RunAutomationView(
            alert_rules=rules,
            scheduled_regressions=schedules,
            schedule_targets=schedule_targets,
            durable_work=[
                {
                    "id": item.id,
                    "entity_type": "job",
                    "kind": item.kind,
                    "status": item.status,
                    "attempts": item.attempts,
                    "max_attempts": item.max_attempts,
                    "updated_at": item.updated_at.isoformat(),
                    "can_cancel": item.status in {"pending", "leased"},
                }
                for item in jobs[:20]
            ]
            + [
                {
                    "id": item.id,
                    "entity_type": "outbox",
                    "kind": item.topic,
                    "status": item.status,
                    "attempts": item.attempts,
                    "max_attempts": item.max_attempts,
                    "updated_at": item.updated_at.isoformat(),
                    "can_cancel": item.status in {"pending", "leased"},
                }
                for item in outbox[:20]
            ],
            pending_notifications=sum(
                item.status in {"pending", "leased"} for item in outbox
            ),
            dead_letters=sum(
                item.status in {"dead_letter", "ambiguous"}
                for item in [*jobs, *outbox]
            ),
            adapter_state=adapter_state,
            message=message,
            can_configure=membership.role in {"owner", "admin"},
        )

    def configure_alert(
        self,
        authenticated: AuthenticatedStudioRequest,
        *,
        project_id: str,
        name: str,
        environment_id: str | None,
        stable_error_code: str | None,
        error_count_threshold: int,
        failure_rate_threshold: float | None,
        window_seconds: int,
        adapter_ref: str,
        enabled: bool,
        rule_id: str | None = None,
        expected_version: int | None = None,
    ) -> RunAlertRule:
        self._require_admin(authenticated, project_id)
        normalized_ref = adapter_ref.strip()
        if not _OPAQUE_ADAPTER_REF.fullmatch(normalized_ref):
            raise AutomationConfigurationInvalid(
                "Adapter Ref must be an opaque configured name, not a URL or credential."
            )
        now = utc_now()
        item = RunAlertRule(
            id=rule_id or new_id(),
            project_id=project_id,
            name=name.strip(),
            environment_id=environment_id,
            stable_error_code=stable_error_code,
            error_count_threshold=error_count_threshold,
            failure_rate_threshold=failure_rate_threshold,
            window_seconds=window_seconds,
            adapter_ref=normalized_ref,
            enabled=enabled,
            created_by=authenticated.principal.key,
            version=expected_version or 1,
            created_at=now,
            updated_at=now,
        )
        assert_secret_free(item.model_dump(mode="json"))
        return self.store.save_run_alert_rule(
            item=item,
            principal_key=authenticated.principal.key,
            expected_version=expected_version,
        )

    def configure_schedule(
        self,
        authenticated: AuthenticatedStudioRequest,
        *,
        project_id: str,
        artifact_id: str,
        suite_id: str,
        interval_seconds: int,
        enabled: bool,
        schedule_id: str | None = None,
        expected_version: int | None = None,
    ) -> ScheduledRegression:
        self._require_admin(authenticated, project_id)
        releases = self.store.list_release_records(
            project_id=project_id,
            principal_key=authenticated.principal.key,
        )
        if not any(
            item.artifact_id == artifact_id
            and item.action == "publish"
            and item.outcome == "succeeded"
            for item in releases
        ):
            raise AutomationConfigurationInvalid(
                "Scheduled regression requires an Artifact with a successful explicit Publish receipt."
            )
        now = utc_now()
        item = ScheduledRegression(
            id=schedule_id or new_id(),
            project_id=project_id,
            artifact_id=artifact_id,
            suite_id=suite_id,
            interval_seconds=interval_seconds,
            next_run_at=now + timedelta(seconds=interval_seconds),
            enabled=enabled,
            created_by=authenticated.principal.key,
            version=expected_version or 1,
            created_at=now,
            updated_at=now,
        )
        return self.store.save_scheduled_regression(
            item=item,
            principal_key=authenticated.principal.key,
            expected_version=expected_version,
        )

    def evaluate_alerts(
        self,
        authenticated: AuthenticatedStudioRequest,
        *,
        project_id: str,
    ) -> int:
        self.store.get_project_for_principal(
            project_id,
            authenticated.principal.key,
        )
        now = utc_now()
        queued = 0
        rules = self.store.list_run_alert_rules(
            project_id=project_id,
            principal_key=authenticated.principal.key,
        )
        for rule in rules:
            if not rule.enabled:
                continue
            observations = self.store.list_execution_observations(
                project_id=project_id,
                principal_key=authenticated.principal.key,
                environment_id=rule.environment_id,
                started_from=now - timedelta(seconds=rule.window_seconds),
            )
            if rule.stable_error_code:
                observations = [
                    item
                    for item in observations
                    if item.stable_error_code == rule.stable_error_code
                ]
            failures = sum(
                item.status in {"failed", "partial_succeeded"}
                for item in observations
            )
            rate = failures / len(observations) if observations else 0.0
            breached = failures >= rule.error_count_threshold
            if rule.failure_rate_threshold is not None:
                breached = breached and rate >= rule.failure_rate_threshold
            if not breached:
                continue
            bucket = math.floor(now.timestamp() / rule.window_seconds)
            payload = {
                "schema": "chat2dify.run-alert/v1",
                "rule_id": rule.id,
                "rule_name": rule.name,
                "adapter_ref": rule.adapter_ref,
                "environment_id": rule.environment_id,
                "stable_error_code": rule.stable_error_code,
                "window_seconds": rule.window_seconds,
                "executions": len(observations),
                "failures": failures,
                "failure_rate": round(rate, 4),
                "window_bucket": bucket,
                "sanitized": True,
            }
            assert_secret_free(payload)
            before = self.store.list_outbox(
                project_id=project_id,
                principal_key=authenticated.principal.key,
            )
            message = self.store.enqueue_outbox(
                project_id=project_id,
                principal_key=authenticated.principal.key,
                topic="notification.run_alert",
                payload=payload,
                idempotency_key=f"run-alert:{rule.id}:{bucket}",
                max_attempts=5,
            )
            queued += int(all(item.id != message.id for item in before))
        return queued

    def enqueue_due_schedules(
        self,
        authenticated: AuthenticatedStudioRequest,
        *,
        project_id: str,
    ) -> int:
        self._require_admin(authenticated, project_id)
        now = utc_now()
        queued = 0
        for schedule in self.store.list_scheduled_regressions(
            project_id=project_id,
            principal_key=authenticated.principal.key,
        ):
            if not schedule.enabled or schedule.next_run_at > now:
                continue
            due_key = schedule.next_run_at.isoformat()
            payload = {
                "schema": "chat2dify.scheduled-regression/v1",
                "schedule_id": schedule.id,
                "artifact_id": schedule.artifact_id,
                "suite_id": schedule.suite_id,
                "scheduled_for": due_key,
                "authorized_by": schedule.created_by,
                "production_write": False,
            }
            assert_secret_free(payload)
            self.store.enqueue_job(
                project_id=project_id,
                principal_key=authenticated.principal.key,
                kind="scenario.scheduled_regression",
                payload=payload,
                idempotency_key=f"scheduled-regression:{schedule.id}:{due_key}",
                max_attempts=3,
            )
            next_item = schedule.model_copy(
                update={
                    "next_run_at": now + timedelta(
                        seconds=schedule.interval_seconds
                    ),
                    "updated_at": now,
                }
            )
            try:
                self.store.save_scheduled_regression(
                    item=next_item,
                    principal_key=authenticated.principal.key,
                    expected_version=schedule.version,
                )
                queued += 1
            except StudioConflict:
                # The durable job is deduplicated. Another scheduler won the
                # schedule CAS and already advanced the same due occurrence.
                continue
        return queued

    def cancel_work(
        self,
        authenticated: AuthenticatedStudioRequest,
        *,
        project_id: str,
        entity_type: str,
        entity_id: str,
        reason: str,
    ) -> RunAutomationView:
        self.store.request_work_cancel(
            project_id=project_id,
            principal_key=authenticated.principal.key,
            entity_type=entity_type,
            entity_id=entity_id,
            reason=reason,
        )
        return self.view(authenticated, project_id=project_id)

    def _require_admin(
        self,
        authenticated: AuthenticatedStudioRequest,
        project_id: str,
    ) -> None:
        _, membership = self.store.get_project_for_principal(
            project_id,
            authenticated.principal.key,
        )
        if membership.role not in {"owner", "admin"}:
            raise StudioAccessDenied(
                "Only a project Admin can configure Run automation."
            )
