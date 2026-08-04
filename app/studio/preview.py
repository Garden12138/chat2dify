from __future__ import annotations

from dataclasses import dataclass, replace
from typing import Any, Callable, Protocol

from app.config import Settings
from app.dify.client import (
    DifyChatflowRunResult,
    DifyClient,
    DifyClientError,
    DifyDraftRunResult,
)
from app.studio.models import ScenarioCase


class PreviewAdapterError(RuntimeError):
    code = "PREVIEW_ADAPTER_ERROR"


class PreviewImportAmbiguous(PreviewAdapterError):
    code = "PREVIEW_IMPORT_AMBIGUOUS"


class PreviewTargetUnavailable(PreviewAdapterError):
    code = "PREVIEW_TARGET_UNAVAILABLE"


@dataclass(frozen=True)
class PreviewImportReceipt:
    app_id: str
    import_id: str
    status: str


@dataclass(frozen=True)
class PreviewExecutionResult:
    ok: bool
    status: str
    output: Any
    workflow_run_id: str | None = None
    failed_node_id: str | None = None
    error: str | None = None
    elapsed_time: float | None = None
    total_tokens: int | None = None
    total_steps: int | None = None


class PreviewExecutionAdapter(Protocol):
    target_key: str
    target_name: str
    default_ttl_seconds: int

    @property
    def available(self) -> bool: ...

    def import_candidate(
        self,
        *,
        yaml_content: str,
        label: str,
        idempotency_key: str,
    ) -> PreviewImportReceipt: ...

    def execute_case(
        self,
        *,
        app_id: str,
        app_mode: str,
        scenario: ScenarioCase,
        timeout_seconds: int,
        cancellation_check: Callable[[], None],
    ) -> PreviewExecutionResult: ...

    def delete_fixture(self, app_id: str) -> None: ...

    def verify_absent(self, app_id: str) -> bool: ...

    def find_apps_by_label(self, label: str) -> list[str]: ...


class DisabledPreviewAdapter:
    target_key = "not-configured"
    target_name = "Preview target not configured"
    default_ttl_seconds = 1_800
    available = False

    def _fail(self) -> None:
        raise PreviewTargetUnavailable(
            "Configure an explicit non-production Preview target before running candidates."
        )

    def import_candidate(self, **_kwargs: Any) -> PreviewImportReceipt:
        self._fail()
        raise AssertionError("unreachable")

    def execute_case(self, **_kwargs: Any) -> PreviewExecutionResult:
        self._fail()
        raise AssertionError("unreachable")

    def delete_fixture(self, _app_id: str) -> None:
        self._fail()

    def verify_absent(self, _app_id: str) -> bool:
        self._fail()
        return False

    def find_apps_by_label(self, _label: str) -> list[str]:
        self._fail()
        return []


class DifyPreviewAdapter:
    """Explicit adapter for one configured non-production Dify target."""

    def __init__(self, settings: Settings) -> None:
        if not settings.studio_preview_enabled:
            raise PreviewTargetUnavailable("The isolated Preview target is disabled.")
        required = {
            "target_key": settings.studio_preview_target_id,
            "api": settings.studio_preview_console_api_base,
            "web": settings.studio_preview_console_web_base,
            "email": settings.studio_preview_email,
            "password": settings.studio_preview_password,
        }
        if any(value is None for value in required.values()):
            raise PreviewTargetUnavailable(
                "The isolated Preview target configuration is incomplete."
            )
        self.target_key = str(settings.studio_preview_target_id)
        self.target_name = settings.studio_preview_target_name
        self.default_ttl_seconds = settings.studio_preview_ttl_seconds
        self._settings = replace(
            settings,
            dify_console_api_base=str(settings.studio_preview_console_api_base),
            dify_console_web_base=str(settings.studio_preview_console_web_base),
            dify_email=settings.studio_preview_email,
            dify_password=settings.studio_preview_password,
        )

    @property
    def available(self) -> bool:
        return True

    def import_candidate(
        self,
        *,
        yaml_content: str,
        label: str,
        idempotency_key: str,
    ) -> PreviewImportReceipt:
        try:
            with DifyClient(self._settings) as client:
                result = client.import_yaml(
                    yaml_content,
                    name=label,
                    idempotency_key=idempotency_key,
                )
        except DifyClientError as exc:
            # The request may have reached Dify. Retrying could create a second app.
            raise PreviewImportAmbiguous(
                "The Preview import outcome is unknown and requires reconciliation."
            ) from exc
        if result.status in {"failed", "error"}:
            raise PreviewAdapterError(
                result.error or "Dify rejected the isolated Preview import."
            )
        if not result.app_id or not result.id:
            raise PreviewImportAmbiguous(
                "Dify did not return both an App ID and Import ID; reconciliation is required."
            )
        return PreviewImportReceipt(
            app_id=str(result.app_id),
            import_id=str(result.id),
            status=str(result.status),
        )

    def execute_case(
        self,
        *,
        app_id: str,
        app_mode: str,
        scenario: ScenarioCase,
        timeout_seconds: int,
        cancellation_check: Callable[[], None],
    ) -> PreviewExecutionResult:
        files = [_dify_file(item) for item in scenario.files]
        try:
            with DifyClient(self._settings) as client:
                if app_mode == "workflow":
                    result = client.run_draft_workflow(
                        app_id,
                        inputs=scenario.inputs,
                        files=files,
                        timeout_seconds=timeout_seconds,
                        cancellation_check=cancellation_check,
                    )
                elif app_mode == "advanced-chat":
                    query = str(
                        scenario.inputs.get("sys.query")
                        or scenario.inputs.get("query")
                        or ""
                    ).strip()
                    if not query:
                        raise PreviewAdapterError(
                            "Chatflow Scenario requires the discovered sys.query input."
                        )
                    inputs = {
                        key: value
                        for key, value in scenario.inputs.items()
                        if key not in {"sys.query", "query"}
                    }
                    result = client.run_draft_chatflow(
                        app_id,
                        query=query,
                        inputs=inputs,
                        files=files,
                        timeout_seconds=timeout_seconds,
                        cancellation_check=cancellation_check,
                    )
                else:
                    raise PreviewAdapterError(
                        "Scenario Preview currently supports Workflow and Chatflow candidates."
                    )
        except PreviewAdapterError:
            raise
        except DifyClientError as exc:
            raise PreviewAdapterError("The isolated Preview execution failed.") from exc
        return _execution_result(result)

    def delete_fixture(self, app_id: str) -> None:
        try:
            with DifyClient(self._settings) as client:
                client.delete_app(app_id)
        except DifyClientError as exc:
            raise PreviewAdapterError("Preview fixture cleanup could not be confirmed.") from exc

    def verify_absent(self, app_id: str) -> bool:
        try:
            with DifyClient(self._settings) as client:
                return not client.app_exists(app_id)
        except DifyClientError as exc:
            raise PreviewAdapterError(
                "Preview fixture absence could not be independently verified."
            ) from exc

    def find_apps_by_label(self, label: str) -> list[str]:
        try:
            with DifyClient(self._settings) as client:
                return [
                    item.id
                    for item in client.list_apps(name=label, limit=100)
                    if item.name == label
                ]
        except DifyClientError as exc:
            raise PreviewAdapterError(
                "Preview reconciliation could not list temporary apps."
            ) from exc


def preview_adapter_from_settings(settings: Settings) -> PreviewExecutionAdapter:
    if not settings.studio_preview_enabled:
        return DisabledPreviewAdapter()
    return DifyPreviewAdapter(settings)


def _execution_result(
    result: DifyDraftRunResult | DifyChatflowRunResult,
) -> PreviewExecutionResult:
    output: Any = (
        result.outputs
        if isinstance(result, DifyDraftRunResult)
        else result.answer
    )
    failed_node_id = _failed_node_id(result.final_event)
    return PreviewExecutionResult(
        ok=bool(result.ok),
        status=str(result.status),
        output=output,
        workflow_run_id=result.workflow_run_id,
        failed_node_id=failed_node_id,
        error=result.error,
        elapsed_time=result.elapsed_time,
        total_tokens=result.total_tokens,
        total_steps=result.total_steps,
    )


def _failed_node_id(event: dict[str, Any] | None) -> str | None:
    if not isinstance(event, dict):
        return None
    data = event.get("data") if isinstance(event.get("data"), dict) else {}
    node_id = data.get("node_id") or event.get("node_id")
    return str(node_id) if node_id else None


def _dify_file(reference: Any) -> dict[str, Any]:
    media_type = str(reference.media_type)
    file_type = "image" if media_type.startswith("image/") else "document"
    return {
        "type": file_type,
        "transfer_method": "local_file",
        "upload_file_id": reference.opaque_ref,
    }
