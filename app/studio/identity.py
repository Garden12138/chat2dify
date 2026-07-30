from __future__ import annotations

import base64
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
import hashlib
import hmac
from http.cookies import SimpleCookie
import json
import logging
import re
from typing import Any
from urllib.parse import urlsplit
from uuid import uuid4

import httpx

from app.config import Settings
from app.studio.models import (
    DifyAppSummary,
    Membership,
    Principal,
    Project,
    StudioSession,
    VerifiedHostContext,
    utc_now,
)
from app.studio.store import StudioAccessDenied, StudioStore


_NONCE_PATTERN = re.compile(r"^[A-Za-z0-9_-]{20,128}$")
_DIFY_COOKIE_NAMES = frozenset(
    {
        "access_token",
        "refresh_token",
        "csrf_token",
        "__Host-access_token",
        "__Host-refresh_token",
        "__Host-csrf_token",
    }
)
logger = logging.getLogger(__name__)


class StudioIdentityError(RuntimeError):
    code = "STUDIO_IDENTITY_INVALID"


class StudioIdentityRequired(StudioIdentityError):
    code = "STUDIO_IDENTITY_REQUIRED"


class StudioIdentityExpired(StudioIdentityError):
    code = "STUDIO_IDENTITY_EXPIRED"


class StudioOriginDenied(StudioIdentityError):
    code = "STUDIO_ORIGIN_DENIED"


class StudioHostUnavailable(StudioIdentityError):
    code = "STUDIO_DIFY_HOST_UNAVAILABLE"


class StudioHostSessionInvalid(StudioIdentityError):
    code = "STUDIO_DIFY_SESSION_INVALID"


@dataclass(frozen=True)
class IssuedStudioSession:
    token: str
    expires_at: datetime
    session: StudioSession
    principal: Principal
    project: Project
    membership: Membership
    apps_available: bool
    apps_error_code: str | None
    set_cookie_headers: list[str]


@dataclass(frozen=True)
class AuthenticatedStudioRequest:
    claims: dict[str, Any]
    session: StudioSession
    principal: Principal
    project: Project
    membership: Membership
    host: VerifiedHostContext


class DifyHostVerifier:
    """Verifies the current browser session against Dify without storing cookies."""

    def __init__(
        self,
        settings: Settings,
        *,
        transport: httpx.BaseTransport | None = None,
        timeout: float = 10.0,
    ) -> None:
        self.settings = settings
        self._transport = transport
        self._timeout = timeout

    def verify(
        self,
        cookie_header: str,
        *,
        app_name: str | None = None,
        app_mode: str | None = None,
    ) -> VerifiedHostContext:
        if not cookie_header or len(cookie_header) > 32_768 or "\n" in cookie_header:
            raise StudioHostSessionInvalid(
                "A signed-in Dify browser session is required."
            )
        cookies = _dify_cookies(cookie_header)
        if not cookies:
            raise StudioHostSessionInvalid(
                "A signed-in Dify browser session is required."
            )
        headers = {
            "Accept": "application/json",
            "Cookie": _serialize_cookies(cookies),
            **_csrf_header_from_cookies(cookies),
        }
        try:
            with httpx.Client(
                base_url=self.settings.dify_console_api_base,
                headers=headers,
                transport=self._transport,
                timeout=self._timeout,
                follow_redirects=False,
            ) as client:
                profile_response = client.get("/account/profile")
                set_cookie_headers: list[str] = []
                if profile_response.status_code in {401, 403}:
                    refresh_response = client.post("/refresh-token")
                    if not 200 <= refresh_response.status_code < 300:
                        raise StudioHostSessionInvalid(
                            "The Dify browser session expired and could not be refreshed."
                        )
                    set_cookie_headers = _dify_set_cookie_headers(
                        refresh_response
                    )
                    cookies.update(
                        _dify_cookies_from_set_cookie(set_cookie_headers)
                    )
                    client.headers["Cookie"] = _serialize_cookies(cookies)
                    client.headers.update(_csrf_header_from_cookies(cookies))
                    profile_response = client.get("/account/profile")
                profile = self._json_object(profile_response, "profile")
                workspace = self._current_workspace(client)
                principal = Principal(
                    issuer=self.settings.studio_token_issuer,
                    subject=_required_string(profile, "id"),
                    display_name=_required_string(profile, "name"),
                    email=_optional_string(profile.get("email")),
                    dify_tenant_id=_required_string(workspace, "id"),
                )
                try:
                    apps = self._list_apps(
                        client,
                        name=app_name,
                        mode=app_mode,
                    )
                    return VerifiedHostContext(
                        principal=principal,
                        apps=apps,
                        set_cookie_headers=set_cookie_headers,
                    )
                except (httpx.HTTPError, StudioHostUnavailable):
                    return VerifiedHostContext(
                        principal=principal,
                        apps=[],
                        apps_available=False,
                        apps_error_code="STUDIO_DIFY_APPS_UNAVAILABLE",
                        set_cookie_headers=set_cookie_headers,
                    )
        except StudioIdentityError as exc:
            logger.info(
                "Dify Studio identity verification rejected code=%s cookie_names=%s",
                exc.code,
                _cookie_names(cookie_header),
            )
            raise
        except httpx.HTTPError as exc:
            raise StudioHostUnavailable(
                "Dify could not be reached to verify this Studio session."
            ) from exc

    def _current_workspace(self, client: httpx.Client) -> dict[str, Any]:
        response = client.get("/workspaces", params={"page": 1, "limit": 100})
        payload = self._json_object(response, "workspaces")
        rows = payload.get("workspaces", payload.get("data"))
        if not isinstance(rows, list):
            raise StudioHostUnavailable("Dify returned an invalid workspace list.")
        for item in rows:
            if isinstance(item, dict) and item.get("current") is True:
                return item
        raise StudioHostSessionInvalid(
            "The Dify session has no active workspace."
        )

    def _list_apps(
        self,
        client: httpx.Client,
        *,
        name: str | None,
        mode: str | None,
    ) -> list[DifyAppSummary]:
        apps: list[DifyAppSummary] = []
        page = 1
        while page <= 10 and len(apps) < 1_000:
            params: dict[str, Any] = {"page": page, "limit": 100, "mode": mode or "all"}
            if name:
                params["name"] = name
            payload = self._json_object(client.get("/apps", params=params), "apps")
            rows = payload.get("data")
            if not isinstance(rows, list):
                raise StudioHostUnavailable("Dify returned an invalid app list.")
            for item in rows:
                if not isinstance(item, dict):
                    continue
                app_id = _optional_string(item.get("id"))
                app_name = _optional_string(item.get("name"))
                app_mode = _optional_string(item.get("mode"))
                if not app_id or not app_name or not app_mode:
                    continue
                apps.append(
                    DifyAppSummary(
                        id=app_id,
                        name=app_name,
                        mode=app_mode,
                        description=_optional_string(item.get("description")) or "",
                        updated_at=_dify_datetime(item.get("updated_at")),
                        created_at=_dify_datetime(item.get("created_at")),
                        icon=_optional_string(item.get("icon_url") or item.get("icon")),
                        icon_background=_optional_string(item.get("icon_background")),
                    )
                )
            if payload.get("has_more") is not True:
                break
            page += 1
        apps.sort(
            key=lambda item: item.updated_at or item.created_at or datetime.min.replace(tzinfo=timezone.utc),
            reverse=True,
        )
        return apps

    @staticmethod
    def _json_object(response: httpx.Response, resource: str) -> dict[str, Any]:
        if response.status_code in {401, 403}:
            raise StudioHostSessionInvalid(
                "The Dify browser session is missing, expired, or unauthorized."
            )
        if response.status_code < 200 or response.status_code >= 300:
            raise StudioHostUnavailable(
                f"Dify could not provide the required {resource} context."
            )
        try:
            payload = response.json()
        except ValueError as exc:
            raise StudioHostUnavailable(
                f"Dify returned invalid {resource} context."
            ) from exc
        if not isinstance(payload, dict):
            raise StudioHostUnavailable(
                f"Dify returned invalid {resource} context."
            )
        return payload


class StudioIdentityService:
    def __init__(
        self,
        *,
        settings: Settings,
        store: StudioStore,
        host_verifier: DifyHostVerifier,
    ) -> None:
        if not settings.studio_signing_secret:
            raise ValueError("A Studio signing secret is required.")
        self.settings = settings
        self.store = store
        self.host_verifier = host_verifier
        self._secret = settings.studio_signing_secret.encode("utf-8")

    def issue(
        self,
        *,
        nonce: str,
        origin_header: str | None,
        cookie_header: str | None,
    ) -> IssuedStudioSession:
        if not _NONCE_PATTERN.fullmatch(nonce):
            raise StudioIdentityError(
                "A cryptographically random Dify-host nonce is required."
            )
        origin = self._validate_origin(origin_header)
        host = self.host_verifier.verify(cookie_header or "")
        project, membership = self.store.ensure_personal_project(host.principal)
        now = utc_now()
        expires_at = now + timedelta(seconds=self.settings.studio_token_ttl_seconds)
        nonce_hash = self.store.consume_identity_nonce(
            issuer=self.settings.studio_token_issuer,
            nonce=nonce,
            origin=origin,
            expires_at=expires_at,
        )
        jti = str(uuid4())
        claims = {
            "iss": self.settings.studio_token_issuer,
            "aud": self.settings.studio_token_audience,
            "sub": host.principal.subject,
            "ten": host.principal.dify_tenant_id,
            "prj": project.id,
            "ori": origin,
            "non": nonce_hash,
            "jti": jti,
            "iat": int(now.timestamp()),
            "exp": int(expires_at.timestamp()),
        }
        session = self.store.create_identity_session(
            jti=jti,
            principal=host.principal,
            project_id=project.id,
            origin=origin,
            nonce_hash=nonce_hash,
            expires_at=expires_at,
        )
        return IssuedStudioSession(
            token=self._encode(claims),
            expires_at=expires_at,
            session=session,
            principal=host.principal,
            project=project,
            membership=membership,
            apps_available=host.apps_available,
            apps_error_code=host.apps_error_code,
            set_cookie_headers=host.set_cookie_headers,
        )

    def authenticate(
        self,
        *,
        authorization: str | None,
        origin_header: str | None,
        referer_header: str | None,
        cookie_header: str | None,
        app_name: str | None = None,
        app_mode: str | None = None,
    ) -> AuthenticatedStudioRequest:
        token = _bearer_token(authorization)
        claims = self._decode(token)
        request_origin = self._validate_origin(
            origin_header or _origin_from_referer(referer_header)
        )
        if not hmac.compare_digest(str(claims["ori"]), request_origin):
            raise StudioOriginDenied(
                "This Studio session is bound to a different Dify origin."
            )
        session = self.store.get_identity_session(str(claims["jti"]))
        now = utc_now()
        if session.revoked_at is not None or session.expires_at <= now:
            raise StudioIdentityExpired("The Studio session expired or was revoked.")
        expected = {
            "principal": f"{self.settings.studio_token_issuer}:{claims['sub']}",
            "project": str(claims["prj"]),
            "tenant": str(claims["ten"]),
            "origin": str(claims["ori"]),
            "nonce": str(claims["non"]),
        }
        actual = {
            "principal": session.principal_key,
            "project": session.project_id,
            "tenant": session.dify_tenant_id,
            "origin": session.origin,
            "nonce": session.nonce_hash,
        }
        if any(
            not hmac.compare_digest(expected[key], actual[key])
            for key in expected
        ):
            raise StudioIdentityError("The Studio session binding is invalid.")
        host = self.host_verifier.verify(
            cookie_header or "",
            app_name=app_name,
            app_mode=app_mode,
        )
        if (
            not hmac.compare_digest(host.principal.key, session.principal_key)
            or not hmac.compare_digest(
                host.principal.dify_tenant_id,
                session.dify_tenant_id,
            )
        ):
            raise StudioAccessDenied(
                "The current Dify account or workspace does not match this Studio session."
            )
        project, membership = self.store.get_project_for_principal(
            session.project_id,
            host.principal.key,
        )
        if not hmac.compare_digest(
            project.dify_tenant_id,
            host.principal.dify_tenant_id,
        ):
            raise StudioAccessDenied(
                "This Studio project belongs to another Dify workspace."
            )
        return AuthenticatedStudioRequest(
            claims=claims,
            session=session,
            principal=host.principal,
            project=project,
            membership=membership,
            host=host,
        )

    def _validate_origin(self, value: str | None) -> str:
        if not value:
            raise StudioOriginDenied("A Dify host origin is required.")
        parsed = urlsplit(value)
        if (
            parsed.scheme not in {"http", "https"}
            or not parsed.netloc
            or parsed.username is not None
            or parsed.password is not None
            or parsed.path not in {"", "/"}
            or parsed.query
            or parsed.fragment
        ):
            raise StudioOriginDenied("The Dify host origin is invalid.")
        origin = f"{parsed.scheme.lower()}://{parsed.netloc.lower()}"
        if origin not in self.settings.studio_allowed_origins:
            raise StudioOriginDenied("This origin is not allowed to open Studio.")
        return origin

    def _encode(self, claims: dict[str, Any]) -> str:
        header = {"alg": "HS256", "typ": "JWT"}
        header_part = _base64url(_canonical_json(header))
        payload_part = _base64url(_canonical_json(claims))
        signing_input = f"{header_part}.{payload_part}".encode("ascii")
        signature = hmac.new(self._secret, signing_input, hashlib.sha256).digest()
        return f"{header_part}.{payload_part}.{_base64url(signature)}"

    def _decode(self, token: str) -> dict[str, Any]:
        try:
            header_part, payload_part, signature_part = token.split(".")
            signing_input = f"{header_part}.{payload_part}".encode("ascii")
            supplied = _base64url_decode(signature_part)
            expected = hmac.new(self._secret, signing_input, hashlib.sha256).digest()
            if not hmac.compare_digest(supplied, expected):
                raise StudioIdentityError("The Studio session signature is invalid.")
            header = json.loads(_base64url_decode(header_part))
            claims = json.loads(_base64url_decode(payload_part))
        except StudioIdentityError:
            raise
        except (ValueError, TypeError, json.JSONDecodeError) as exc:
            raise StudioIdentityError("The Studio session token is malformed.") from exc
        if header != {"alg": "HS256", "typ": "JWT"} or not isinstance(claims, dict):
            raise StudioIdentityError("The Studio session token is unsupported.")
        required = {"iss", "aud", "sub", "ten", "prj", "ori", "non", "jti", "iat", "exp"}
        if set(claims) != required:
            raise StudioIdentityError("The Studio session claims are incomplete.")
        if (
            not hmac.compare_digest(str(claims["iss"]), self.settings.studio_token_issuer)
            or not hmac.compare_digest(
                str(claims["aud"]),
                self.settings.studio_token_audience,
            )
        ):
            raise StudioIdentityError("The Studio session issuer or audience is invalid.")
        try:
            issued_at = int(claims["iat"])
            expires_at = int(claims["exp"])
        except (TypeError, ValueError) as exc:
            raise StudioIdentityError("The Studio session time claims are invalid.") from exc
        now = int(utc_now().timestamp())
        if issued_at > now + 30 or expires_at <= now:
            raise StudioIdentityExpired("The Studio session has expired.")
        if expires_at - issued_at > self.settings.studio_token_ttl_seconds:
            raise StudioIdentityError("The Studio session lifetime is invalid.")
        return claims


def _bearer_token(value: str | None) -> str:
    if not value or not value.startswith("Bearer "):
        raise StudioIdentityRequired("A Studio bearer session is required.")
    token = value.removeprefix("Bearer ").strip()
    if not token or len(token) > 8_192:
        raise StudioIdentityRequired("A valid Studio bearer session is required.")
    return token


def _origin_from_referer(value: str | None) -> str | None:
    if not value:
        return None
    parsed = urlsplit(value)
    if not parsed.scheme or not parsed.netloc:
        return None
    return f"{parsed.scheme}://{parsed.netloc}"


def _base64url(value: bytes) -> str:
    return base64.urlsafe_b64encode(value).rstrip(b"=").decode("ascii")


def _base64url_decode(value: str) -> bytes:
    padding = "=" * (-len(value) % 4)
    return base64.urlsafe_b64decode(f"{value}{padding}".encode("ascii"))


def _canonical_json(value: dict[str, Any]) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")


def _required_string(payload: dict[str, Any], key: str) -> str:
    value = _optional_string(payload.get(key))
    if not value:
        raise StudioHostUnavailable(f"Dify returned an invalid {key}.")
    return value


def _optional_string(value: Any) -> str | None:
    if value is None:
        return None
    normalized = str(value).strip()
    return normalized or None


def _dify_datetime(value: Any) -> datetime | None:
    if value is None:
        return None
    try:
        if isinstance(value, (int, float)) or str(value).isdigit():
            return datetime.fromtimestamp(float(value), tz=timezone.utc)
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)
    except (ValueError, TypeError, OverflowError):
        return None


def csrf_header_from_cookie(cookie_header: str) -> dict[str, str]:
    """Kept for future Dify POST verification without exposing cookie values."""
    cookie = SimpleCookie()
    try:
        cookie.load(cookie_header)
    except Exception:
        return {}
    for name in ("csrf_token", "__Host-csrf_token"):
        morsel = cookie.get(name)
        if morsel and morsel.value:
            return {"X-CSRF-Token": morsel.value}
    return {}


def _cookie_names(cookie_header: str) -> list[str]:
    """Return cookie names for authentication diagnostics without logging values."""
    cookie = SimpleCookie()
    try:
        cookie.load(cookie_header)
    except Exception:
        return []
    return sorted(cookie.keys())


def _dify_cookies(cookie_header: str) -> dict[str, str]:
    cookie = SimpleCookie()
    try:
        cookie.load(cookie_header)
    except Exception as exc:
        raise StudioHostSessionInvalid(
            "The Dify browser session cookie is invalid."
        ) from exc
    return {
        name: morsel.value
        for name, morsel in cookie.items()
        if name in _DIFY_COOKIE_NAMES and morsel.value
    }


def _dify_set_cookie_headers(response: httpx.Response) -> list[str]:
    forwarded: list[str] = []
    for value in response.headers.get_list("set-cookie"):
        cookie = SimpleCookie()
        try:
            cookie.load(value)
        except Exception:
            continue
        if len(cookie) != 1:
            continue
        name = next(iter(cookie))
        if name in _DIFY_COOKIE_NAMES:
            forwarded.append(value)
    return forwarded[:3]


def _dify_cookies_from_set_cookie(
    set_cookie_headers: list[str],
) -> dict[str, str]:
    cookies: dict[str, str] = {}
    for value in set_cookie_headers:
        cookie = SimpleCookie()
        try:
            cookie.load(value)
        except Exception:
            continue
        for name, morsel in cookie.items():
            if name in _DIFY_COOKIE_NAMES and morsel.value:
                cookies[name] = morsel.value
    return cookies


def _serialize_cookies(cookies: dict[str, str]) -> str:
    return "; ".join(
        f"{name}={value}"
        for name, value in sorted(cookies.items())
    )


def _csrf_header_from_cookies(cookies: dict[str, str]) -> dict[str, str]:
    for name in ("csrf_token", "__Host-csrf_token"):
        value = cookies.get(name)
        if value:
            return {"X-CSRF-Token": value}
    return {}
