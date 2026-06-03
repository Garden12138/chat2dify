from __future__ import annotations

import base64
from dataclasses import dataclass
import time
from typing import Any

import httpx

from app.config import Settings
from app.dify.sse import SseParseIssue, iter_sse_events, summarize_events, terminal_event


CSRF_HEADER_NAME = "X-CSRF-Token"
CSRF_COOKIE_NAMES = ("csrf_token", "__Host-csrf_token")


class DifyClientError(RuntimeError):
    """Raised when Dify cannot be reached or rejects a request."""


class DifyConflictError(DifyClientError):
    """Raised when Dify rejects a draft sync because the workflow hash is stale."""


@dataclass(frozen=True)
class DifyImportResult:
    id: str
    status: str
    app_id: str | None = None
    app_mode: str | None = None
    current_dsl_version: str | None = None
    imported_dsl_version: str | None = None
    error: str = ""
    leaked_dependencies: list[dict[str, Any]] | None = None
    workflow_url: str | None = None

    @classmethod
    def from_payload(cls, payload: dict[str, Any], *, workflow_url: str | None = None) -> "DifyImportResult":
        return cls(
            id=str(payload.get("id", "")),
            status=str(payload.get("status", "")),
            app_id=payload.get("app_id"),
            app_mode=payload.get("app_mode"),
            current_dsl_version=payload.get("current_dsl_version"),
            imported_dsl_version=payload.get("imported_dsl_version"),
            error=str(payload.get("error", "")),
            leaked_dependencies=payload.get("leaked_dependencies"),
            workflow_url=workflow_url,
        )


@dataclass(frozen=True)
class DifyAppDetail:
    id: str
    name: str
    mode: str
    description: str
    raw: dict[str, Any]

    @classmethod
    def from_payload(cls, payload: dict[str, Any]) -> "DifyAppDetail":
        return cls(
            id=str(payload.get("id", "")),
            name=str(payload.get("name", "")),
            mode=str(payload.get("mode", "")),
            description=str(payload.get("description", "") or ""),
            raw=payload,
        )


@dataclass(frozen=True)
class DifyDatasetListItem:
    id: str
    name: str
    description: str
    document_count: int | None = None
    total_document_count: int | None = None
    provider: str | None = None
    runtime_mode: str | None = None
    indexing_technique: str | None = None
    embedding_available: bool | None = None
    permission: str | None = None
    updated_at: int | float | str | None = None
    retrieval_model_dict: dict[str, Any] | None = None

    @classmethod
    def from_payload(cls, payload: dict[str, Any]) -> "DifyDatasetListItem":
        retrieval_model_dict = payload.get("retrieval_model_dict")
        return cls(
            id=str(payload.get("id", "")),
            name=str(payload.get("name", "")),
            description=str(payload.get("description", "") or ""),
            document_count=_int_or_none(payload.get("document_count")),
            total_document_count=_int_or_none(payload.get("total_document_count")),
            provider=_string_or_none(payload.get("provider")),
            runtime_mode=_string_or_none(payload.get("runtime_mode")),
            indexing_technique=_string_or_none(payload.get("indexing_technique")),
            embedding_available=_bool_or_none(payload.get("embedding_available")),
            permission=_string_or_none(payload.get("permission")),
            updated_at=payload.get("updated_at"),
            retrieval_model_dict=retrieval_model_dict if isinstance(retrieval_model_dict, dict) else None,
        )


@dataclass(frozen=True)
class DifyDatasetListResult:
    data: list[DifyDatasetListItem]
    has_more: bool
    page: int
    limit: int
    total: int

    @classmethod
    def from_payload(cls, payload: dict[str, Any]) -> "DifyDatasetListResult":
        raw_items = payload.get("data") if isinstance(payload.get("data"), list) else []
        items = [DifyDatasetListItem.from_payload(item) for item in raw_items if isinstance(item, dict)]
        return cls(
            data=items,
            has_more=bool(_bool_or_none(payload.get("has_more"))),
            page=_int_or_none(payload.get("page")) or 1,
            limit=_int_or_none(payload.get("limit")) or len(items),
            total=_int_or_none(payload.get("total")) or len(items),
        )


@dataclass(frozen=True)
class DifyDraftWorkflow:
    id: str
    graph: dict[str, Any]
    features: dict[str, Any]
    hash: str
    version: str
    environment_variables: list[dict[str, Any]]
    conversation_variables: list[dict[str, Any]]
    raw: dict[str, Any]

    @classmethod
    def from_payload(cls, payload: dict[str, Any]) -> "DifyDraftWorkflow":
        return cls(
            id=str(payload.get("id", "")),
            graph=payload.get("graph") if isinstance(payload.get("graph"), dict) else {},
            features=payload.get("features") if isinstance(payload.get("features"), dict) else {},
            hash=str(payload.get("hash", "")),
            version=str(payload.get("version", "")),
            environment_variables=list(payload.get("environment_variables") or []),
            conversation_variables=list(payload.get("conversation_variables") or []),
            raw=payload,
        )


@dataclass(frozen=True)
class DifyDraftSyncResult:
    result: str
    hash: str
    updated_at: str
    workflow_url: str

    @classmethod
    def from_payload(cls, payload: dict[str, Any], *, workflow_url: str) -> "DifyDraftSyncResult":
        return cls(
            result=str(payload.get("result", "")),
            hash=str(payload.get("hash", "")),
            updated_at=str(payload.get("updated_at", "")),
            workflow_url=workflow_url,
        )


@dataclass(frozen=True)
class DifyDraftRunResult:
    ok: bool
    status: str
    app_id: str
    workflow_url: str
    workflow_run_id: str | None = None
    task_id: str | None = None
    outputs: dict[str, Any] | None = None
    error: str | None = None
    elapsed_time: float | None = None
    total_tokens: int | None = None
    total_steps: int | None = None
    events_summary: dict[str, Any] | None = None
    final_event: dict[str, Any] | None = None


class DifyRunTimeoutError(TimeoutError):
    """Raised internally when a Dify draft run stream exceeds the caller timeout."""


class DifyClient:
    def __init__(
        self,
        settings: Settings,
        *,
        transport: httpx.BaseTransport | None = None,
        timeout: float = 30.0,
    ) -> None:
        self.settings = settings
        self._client = httpx.Client(
            base_url=settings.dify_console_api_base,
            transport=transport,
            timeout=timeout,
            follow_redirects=True,
        )

    def close(self) -> None:
        self._client.close()

    def __enter__(self) -> "DifyClient":
        return self

    def __exit__(self, *_: object) -> None:
        self.close()

    @property
    def csrf_token(self) -> str | None:
        for name in CSRF_COOKIE_NAMES:
            token = self._client.cookies.get(name)
            if token:
                return token
        return None

    @staticmethod
    def encode_password(password: str) -> str:
        return base64.b64encode(password.encode("utf-8")).decode("ascii")

    def login(self) -> None:
        if not self.settings.dify_email or not self.settings.dify_password:
            raise DifyClientError("DIFY_EMAIL and DIFY_PASSWORD are required to create workflows in Dify.")

        payload = {
            "email": self.settings.dify_email,
            "password": self.encode_password(self.settings.dify_password),
            "language": self.settings.dify_login_language,
            "remember_me": True,
        }
        response = self._post("/login", json=payload)
        self._raise_for_response(response)
        body = response.json()
        if body.get("result") != "success":
            raise DifyClientError(str(body.get("data") or body.get("message") or "Dify login failed."))

        if not self.csrf_token:
            raise DifyClientError("Dify login succeeded, but no csrf_token cookie was returned.")

    def refresh_token(self) -> bool:
        response = self._post("/refresh-token", headers=self._csrf_headers())
        if response.status_code == 401:
            return False
        self._raise_for_response(response)
        return response.json().get("result") == "success"

    def import_yaml(self, yaml_content: str, *, name: str | None = None) -> DifyImportResult:
        self._ensure_logged_in()
        payload = {
            "mode": "yaml-content",
            "yaml_content": yaml_content,
            "name": name,
        }
        response = self._post_with_auth_retry("/apps/imports", payload)
        result = self._result_from_response(response)
        if result.status == "pending" and result.id:
            result = self.confirm_import(result.id)
        return result

    def confirm_import(self, import_id: str) -> DifyImportResult:
        response = self._post_with_auth_retry(f"/apps/imports/{import_id}/confirm", {})
        return self._result_from_response(response)

    def get_app_detail(self, app_id: str) -> DifyAppDetail:
        self._ensure_logged_in()
        response = self._get_with_auth_retry(f"/apps/{app_id}")
        self._raise_for_response(response)
        try:
            payload = response.json()
        except ValueError as exc:
            raise DifyClientError(f"Invalid Dify JSON response: {response.text}") from exc
        if not isinstance(payload, dict):
            raise DifyClientError("Dify app detail response must be a JSON object.")
        return DifyAppDetail.from_payload(payload)

    def list_datasets(
        self,
        *,
        keyword: str | None = None,
        page: int = 1,
        limit: int = 50,
        include_all: bool = True,
        ids: list[str] | None = None,
    ) -> DifyDatasetListResult:
        self._ensure_logged_in()
        params: list[tuple[str, Any]] = [
            ("page", page),
            ("limit", limit),
            ("include_all", str(include_all).lower()),
        ]
        if keyword:
            params.append(("keyword", keyword))
        for dataset_id in ids or []:
            params.append(("ids", dataset_id))
        response = self._get_with_auth_retry("/datasets", params=params)
        self._raise_for_response(response)
        try:
            payload = response.json()
        except ValueError as exc:
            raise DifyClientError(f"Invalid Dify JSON response: {response.text}") from exc
        if not isinstance(payload, dict):
            raise DifyClientError("Dify datasets response must be a JSON object.")
        return DifyDatasetListResult.from_payload(payload)

    def get_datasets_by_ids(self, dataset_ids: list[str]) -> DifyDatasetListResult:
        ids = [str(item).strip() for item in dataset_ids if str(item).strip()]
        if not ids:
            return DifyDatasetListResult(data=[], has_more=False, page=1, limit=0, total=0)
        return self.list_datasets(page=1, limit=max(len(ids), 50), include_all=True, ids=ids)

    def get_draft_workflow(self, app_id: str) -> DifyDraftWorkflow:
        self._ensure_logged_in()
        response = self._get_with_auth_retry(f"/apps/{app_id}/workflows/draft")
        self._raise_for_response(response)
        try:
            payload = response.json()
        except ValueError as exc:
            raise DifyClientError(f"Invalid Dify JSON response: {response.text}") from exc
        if not isinstance(payload, dict):
            raise DifyClientError("Dify draft workflow response must be a JSON object.")
        return DifyDraftWorkflow.from_payload(payload)

    def sync_draft_workflow(
        self,
        app_id: str,
        *,
        graph: dict[str, Any],
        features: dict[str, Any],
        hash: str,
        environment_variables: list[dict[str, Any]] | None = None,
        conversation_variables: list[dict[str, Any]] | None = None,
    ) -> DifyDraftSyncResult:
        self._ensure_logged_in()
        payload = {
            "graph": graph,
            "features": features,
            "hash": hash,
            "environment_variables": environment_variables or [],
            "conversation_variables": conversation_variables or [],
        }
        response = self._post_with_auth_retry(f"/apps/{app_id}/workflows/draft", payload)
        self._raise_for_response(response)
        try:
            body = response.json()
        except ValueError as exc:
            raise DifyClientError(f"Invalid Dify JSON response: {response.text}") from exc
        if not isinstance(body, dict):
            raise DifyClientError("Dify draft sync response must be a JSON object.")
        return DifyDraftSyncResult.from_payload(body, workflow_url=self.settings.workflow_url(app_id))

    def run_draft_workflow(
        self,
        app_id: str,
        *,
        inputs: dict[str, Any],
        files: list[dict[str, Any]] | None = None,
        timeout_seconds: float = 120,
    ) -> DifyDraftRunResult:
        self._ensure_logged_in()
        payload: dict[str, Any] = {"inputs": inputs}
        if files is not None:
            payload["files"] = files
        result = self._run_draft_workflow_once(app_id, payload=payload, timeout_seconds=timeout_seconds)
        if result is not None:
            return result
        if self.refresh_token():
            result = self._run_draft_workflow_once(app_id, payload=payload, timeout_seconds=timeout_seconds)
            if result is not None:
                return result
        self.login()
        result = self._run_draft_workflow_once(app_id, payload=payload, timeout_seconds=timeout_seconds)
        if result is None:
            raise DifyClientError("Dify draft run authorization failed after login.")
        return result

    def _ensure_logged_in(self) -> None:
        if not self.csrf_token:
            self.login()

    def _csrf_headers(self) -> dict[str, str]:
        token = self.csrf_token
        return {CSRF_HEADER_NAME: token} if token else {}

    def _post_with_auth_retry(self, path: str, payload: dict[str, Any]) -> httpx.Response:
        response = self._post(path, json=payload, headers=self._csrf_headers())
        if response.status_code != 401:
            return response
        if self.refresh_token():
            return self._post(path, json=payload, headers=self._csrf_headers())
        self.login()
        return self._post(path, json=payload, headers=self._csrf_headers())

    def _run_draft_workflow_once(
        self,
        app_id: str,
        *,
        payload: dict[str, Any],
        timeout_seconds: float,
    ) -> DifyDraftRunResult | None:
        path = f"/apps/{app_id}/workflows/draft/run"
        try:
            with self._client.stream(
                "POST",
                path,
                json=payload,
                headers=self._csrf_headers(),
                timeout=timeout_seconds,
            ) as response:
                if response.status_code == 401:
                    response.read()
                    return None
                if response.status_code >= 400:
                    self._raise_for_stream_response(response)
                return self._draft_run_result_from_stream(
                    app_id=app_id,
                    lines=response.iter_lines(),
                    timeout_seconds=timeout_seconds,
                )
        except DifyRunTimeoutError:
            return _timeout_run_result(app_id=app_id, workflow_url=self.settings.workflow_url(app_id))
        except httpx.TimeoutException:
            return _timeout_run_result(app_id=app_id, workflow_url=self.settings.workflow_url(app_id))
        except httpx.RequestError as exc:
            raise DifyClientError(f"Dify request failed: {exc}") from exc

    def _draft_run_result_from_stream(
        self,
        *,
        app_id: str,
        lines: Any,
        timeout_seconds: float,
    ) -> DifyDraftRunResult:
        events: list[dict[str, Any]] = []
        parse_errors: list[SseParseIssue] = []
        final: dict[str, Any] | None = None
        deadline = time.monotonic() + timeout_seconds
        try:
            for parsed in iter_sse_events(_lines_until_deadline(lines, deadline)):
                if isinstance(parsed, SseParseIssue):
                    parse_errors.append(parsed)
                    continue
                events.append(parsed)
                final = terminal_event(events)
                if final is not None:
                    break
        except (DifyRunTimeoutError, httpx.TimeoutException):
            return _timeout_run_result(
                app_id=app_id,
                workflow_url=self.settings.workflow_url(app_id),
                events=events,
                parse_errors=parse_errors,
            )
        if final is None:
            return DifyDraftRunResult(
                ok=False,
                status="error",
                app_id=app_id,
                workflow_url=self.settings.workflow_url(app_id),
                error="Dify draft run stream ended before a terminal event.",
                events_summary=summarize_events(events, parse_errors),
            )
        return _run_result_from_terminal_event(
            app_id=app_id,
            workflow_url=self.settings.workflow_url(app_id),
            events=events,
            parse_errors=parse_errors,
            final_event=final,
        )

    def _get_with_auth_retry(self, path: str, **kwargs: Any) -> httpx.Response:
        response = self._get(path, headers=self._csrf_headers(), **kwargs)
        if response.status_code != 401:
            return response
        if self.refresh_token():
            return self._get(path, headers=self._csrf_headers(), **kwargs)
        self.login()
        return self._get(path, headers=self._csrf_headers(), **kwargs)

    def _get(self, path: str, **kwargs: Any) -> httpx.Response:
        try:
            return self._client.get(path, **kwargs)
        except httpx.RequestError as exc:
            raise DifyClientError(f"Dify request failed: {exc}") from exc

    def _post(self, path: str, **kwargs: Any) -> httpx.Response:
        try:
            return self._client.post(path, **kwargs)
        except httpx.RequestError as exc:
            raise DifyClientError(f"Dify request failed: {exc}") from exc

    def _result_from_response(self, response: httpx.Response) -> DifyImportResult:
        if response.status_code >= 500:
            self._raise_for_response(response)
        try:
            payload = response.json()
        except ValueError as exc:
            raise DifyClientError(f"Invalid Dify JSON response: {response.text}") from exc

        workflow_url = None
        app_id = payload.get("app_id")
        if app_id:
            workflow_url = self.settings.workflow_url(str(app_id))

        if response.status_code >= 400 and payload.get("status") != "failed":
            self._raise_for_response(response)
        return DifyImportResult.from_payload(payload, workflow_url=workflow_url)

    @staticmethod
    def _raise_for_response(response: httpx.Response) -> None:
        if response.status_code == 409:
            raise DifyConflictError(f"Dify request failed: {response.status_code} {response.text}")
        try:
            response.raise_for_status()
        except httpx.HTTPStatusError as exc:
            raise DifyClientError(f"Dify request failed: {response.status_code} {response.text}") from exc

    @staticmethod
    def _raise_for_stream_response(response: httpx.Response) -> None:
        body = response.read().decode("utf-8", errors="replace")
        if response.status_code == 409:
            raise DifyConflictError(f"Dify request failed: {response.status_code} {body}")
        try:
            response.raise_for_status()
        except httpx.HTTPStatusError as exc:
            raise DifyClientError(f"Dify request failed: {response.status_code} {body}") from exc


def _lines_until_deadline(lines: Any, deadline: float) -> Any:
    for line in lines:
        if time.monotonic() > deadline:
            raise DifyRunTimeoutError()
        yield line


def _run_result_from_terminal_event(
    *,
    app_id: str,
    workflow_url: str,
    events: list[dict[str, Any]],
    parse_errors: list[SseParseIssue],
    final_event: dict[str, Any],
) -> DifyDraftRunResult:
    event_type = str(final_event.get("event", "error"))
    data = final_event.get("data") if isinstance(final_event.get("data"), dict) else {}
    status = _status_from_final_event(event_type, data)
    error = _error_from_final_event(final_event, data)
    return DifyDraftRunResult(
        ok=event_type == "workflow_finished" and status == "succeeded",
        status=status,
        app_id=app_id,
        workflow_url=workflow_url,
        workflow_run_id=_string_or_none(final_event.get("workflow_run_id") or data.get("workflow_run_id")),
        task_id=_string_or_none(final_event.get("task_id") or data.get("task_id")),
        outputs=data.get("outputs") if isinstance(data.get("outputs"), dict) else None,
        error=error,
        elapsed_time=_float_or_none(data.get("elapsed_time")),
        total_tokens=_int_or_none(data.get("total_tokens")),
        total_steps=_int_or_none(data.get("total_steps")),
        events_summary=summarize_events(events, parse_errors),
        final_event=final_event,
    )

def _timeout_run_result(
    *,
    app_id: str,
    workflow_url: str,
    events: list[dict[str, Any]] | None = None,
    parse_errors: list[SseParseIssue] | None = None,
) -> DifyDraftRunResult:
    return DifyDraftRunResult(
        ok=False,
        status="timeout",
        app_id=app_id,
        workflow_url=workflow_url,
        error="Dify draft run timed out before a terminal event.",
        events_summary=summarize_events(events or [], parse_errors or []),
    )


def _status_from_final_event(event_type: str, data: dict[str, Any]) -> str:
    if event_type == "workflow_paused":
        return "paused"
    if event_type == "error":
        return "error"
    status = data.get("status")
    return str(status) if status else event_type


def _error_from_final_event(final_event: dict[str, Any], data: dict[str, Any]) -> str | None:
    error = data.get("error") or final_event.get("message") or final_event.get("error")
    return str(error) if error else None


def _string_or_none(value: Any) -> str | None:
    if value is None:
        return None
    return str(value)


def _float_or_none(value: Any) -> float | None:
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _int_or_none(value: Any) -> int | None:
    if value is None:
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _bool_or_none(value: Any) -> bool | None:
    if value is None:
        return None
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        return value.strip().lower() in {"1", "true", "yes", "on"}
    return bool(value)
