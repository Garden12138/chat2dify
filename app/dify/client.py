from __future__ import annotations

import base64
from dataclasses import dataclass
from typing import Any

import httpx

from app.config import Settings


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

    def _get_with_auth_retry(self, path: str) -> httpx.Response:
        response = self._get(path, headers=self._csrf_headers())
        if response.status_code != 401:
            return response
        if self.refresh_token():
            return self._get(path, headers=self._csrf_headers())
        self.login()
        return self._get(path, headers=self._csrf_headers())

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
