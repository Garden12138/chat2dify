from __future__ import annotations

from dataclasses import replace
import os
from pathlib import Path
from urllib.parse import urlsplit
from uuid import uuid4

import pytest

from app.config import Settings, load_settings
from app.dify.client import DifyClient
from app.studio.identity import DifyHostVerifier, StudioIdentityService
from app.studio.store import StudioStore


LIVE_ACCEPTANCE_ENABLED = (
    os.environ.get("CHAT2DIFY_LIVE_DIFY_ACCEPTANCE", "").strip() == "1"
)
LOCAL_DIFY_HOSTS = {"localhost", "127.0.0.1", "::1"}

pytestmark = [
    pytest.mark.live_dify,
    pytest.mark.skipif(
        not LIVE_ACCEPTANCE_ENABLED,
        reason=(
            "Set CHAT2DIFY_LIVE_DIFY_ACCEPTANCE=1 to verify the v5 Studio "
            "identity boundary against a signed-in localhost Dify."
        ),
    ),
]


def test_real_dify_session_issues_and_revalidates_studio_principal(
    tmp_path: Path,
) -> None:
    settings = load_settings()
    api_url = urlsplit(settings.dify_console_api_base)
    web_url = urlsplit(settings.dify_console_web_base)
    if api_url.hostname not in LOCAL_DIFY_HOSTS:
        pytest.fail("v5 Studio live acceptance is restricted to localhost Dify.")
    if not settings.dify_email or not settings.dify_password:
        pytest.fail("DIFY_EMAIL and DIFY_PASSWORD are required for live acceptance.")
    origin = f"{web_url.scheme}://{web_url.netloc}"
    studio_settings = replace(
        settings,
        ai_studio_v5_enabled=True,
        studio_database_url=f"sqlite:///{tmp_path / 'studio-live.sqlite3'}",
        studio_signing_secret="studio-live-acceptance-signing-secret-2026",
        studio_allowed_origins=[origin],
    )

    with DifyClient(settings) as client:
        client.login()
        cookie_header = "; ".join(
            f"{item.name}={item.value}"
            for item in client._client.cookies.jar
        )
        identity = StudioIdentityService(
            settings=studio_settings,
            store=StudioStore(studio_settings.studio_database_url),
            host_verifier=DifyHostVerifier(studio_settings),
        )
        issued = identity.issue(
            nonce=f"live-{uuid4().hex}",
            origin_header=origin,
            cookie_header=cookie_header,
        )
        authenticated = identity.authenticate(
            authorization=f"Bearer {issued.token}",
            origin_header=origin,
            referer_header=None,
            cookie_header=cookie_header,
        )

    assert issued.principal.subject
    assert issued.principal.dify_tenant_id
    assert issued.project.kind == "personal"
    assert issued.membership.role == "owner"
    assert authenticated.principal.key == issued.principal.key
    assert authenticated.project.id == issued.project.id
    assert authenticated.host.apps_available is True
