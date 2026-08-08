from __future__ import annotations

from dataclasses import replace
import os
from pathlib import Path
from urllib.parse import urlparse
from uuid import uuid4

import pytest

from app.agent.catalog import NodeCapabilityCatalog
from app.agent.commit import SafeWorkflowDraftWriter
from app.agent.compatibility import DifyCompatibilityMatrix
from app.agent.snapshot import WorkflowSnapshotService
from app.agent.validation import WorkflowValidationService
from app.config import Settings, load_settings
from app.dify.client import DifyClient
from app.dify.version import read_dify_version_info
from app.studio.models import DifyAppSummary, VerifiedHostContext
from app.studio.releases import StudioReleaseService
from tests.test_agent_phase1a_live import _delete_temporary_app
from tests.test_agent_release_live import _compiler
from tests.test_studio_releases import _approved_corrected, _plan, _stack


LIVE_ACCEPTANCE_ENABLED = (
    os.environ.get("CHAT2DIFY_LIVE_DIFY_ACCEPTANCE", "").strip() == "1"
)
LOCAL_DIFY_HOSTS = {"localhost", "127.0.0.1", "::1"}

pytestmark = [
    pytest.mark.live_dify,
    pytest.mark.skipif(
        not LIVE_ACCEPTANCE_ENABLED,
        reason=(
            "Set CHAT2DIFY_LIVE_DIFY_ACCEPTANCE=1 to verify governed Apply "
            "and explicit Publish against a temporary localhost Dify app."
        ),
    ),
]


@pytest.fixture(scope="module")
def live_settings() -> Settings:
    settings = load_settings()
    if urlparse(settings.dify_console_api_base).hostname not in LOCAL_DIFY_HOSTS:
        pytest.fail("Studio Release live acceptance is restricted to localhost Dify.")
    if not settings.dify_email or not settings.dify_password:
        pytest.fail("DIFY_EMAIL and DIFY_PASSWORD are required for live acceptance.")
    return settings


def test_governed_apply_and_explicit_publish_against_temporary_dify_app(
    tmp_path: Path,
    live_settings: Settings,
) -> None:
    version = read_dify_version_info(live_settings.dify_source_path)
    compiler = _compiler(live_settings, version)
    stack = _stack(tmp_path)
    approved = _approved_corrected(stack)
    app_name = f"chat2dify-studio-release-{uuid4().hex[:10]}"
    app_id: str | None = None
    try:
        with DifyClient(live_settings) as client:
            imported = client.import_yaml(
                compiler.compile(_plan()),
                name=app_name,
                idempotency_key=f"studio-release-live-{uuid4()}",
            )
            assert imported.app_id
            app_id = imported.app_id
            baseline = client.get_draft_workflow(app_id)
        owner = replace(
            stack["owner"],
            host=VerifiedHostContext(
                principal=stack["owner"].principal,
                apps=[DifyAppSummary(id=app_id, name=app_name, mode="workflow")],
            ),
        )
        client_factory = lambda: DifyClient(live_settings)
        releases = StudioReleaseService(
            store=stack["studio"],
            reviews=stack["reviews"],
            snapshot=WorkflowSnapshotService(
                client_factory=client_factory,
                catalog=NodeCapabilityCatalog(),
                dify_version=version,
                compatibility=DifyCompatibilityMatrix(),
            ),
            safe_writer=SafeWorkflowDraftWriter(
                validation=WorkflowValidationService(
                    compiler=compiler,
                    expected_dsl_version=version.app_dsl_version,
                ),
                compiler=compiler,
                client_factory=client_factory,
            ),
            client_factory=client_factory,
        )
        logical = releases.create_logical_app(
            owner,
            project_id=stack["project"].id,
            name="Live governed workflow",
            app_mode="workflow",
        )
        environment = releases.create_environment(
            owner,
            project_id=stack["project"].id,
            logical_app_id=logical.id,
            name="Temporary Staging",
            classification="staging",
            target_app_ref=app_id,
        )
        preview = releases.preview(
            owner,
            project_id=stack["project"].id,
            change_request_id=approved.change_request.id,
            environment_id=environment.id,
        )
        assert preview.target_hash == baseline.hash
        assert preview.blockers == []

        apply_authorization = releases.authorize(
            owner,
            project_id=stack["project"].id,
            change_request_id=approved.change_request.id,
            environment_id=environment.id,
            action="apply_draft",
            confirmation="APPLY_DRAFT",
        )
        applied = releases.execute(
            owner,
            project_id=stack["project"].id,
            authorization_id=apply_authorization.id,
            idempotency_key="live-apply-draft-001",
        )
        assert applied.outcome == "succeeded"
        assert applied.after_hash and applied.after_hash != baseline.hash
        with DifyClient(live_settings) as client:
            assert client.get_draft_workflow(app_id).hash == applied.after_hash
        duplicate = releases.execute(
            owner,
            project_id=stack["project"].id,
            authorization_id=apply_authorization.id,
            idempotency_key="live-apply-draft-001",
        )
        assert duplicate.id == applied.id

        publish_authorization = releases.authorize(
            owner,
            project_id=stack["project"].id,
            change_request_id=approved.change_request.id,
            environment_id=environment.id,
            action="publish",
            confirmation="PUBLISH",
        )
        published = releases.execute(
            owner,
            project_id=stack["project"].id,
            authorization_id=publish_authorization.id,
            idempotency_key="live-publish-001",
        )
        assert published.outcome == "succeeded"
        assert published.authorization_id != applied.authorization_id
        assert published.after_hash == applied.after_hash
    finally:
        if app_id:
            _delete_temporary_app(live_settings, app_id)
