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
        try:
            response.raise_for_status()
        except httpx.HTTPStatusError as exc:
            raise DifyClientError(f"Dify request failed: {response.status_code} {response.text}") from exc
