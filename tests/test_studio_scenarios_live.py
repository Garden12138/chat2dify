from __future__ import annotations

import os
from urllib.parse import urlsplit
from uuid import uuid4

import pytest

from app.compiler.dify import DifyDslCompiler
from app.config import Settings, load_settings
from app.dify.version import read_dify_version_info
from app.models import WorkflowPlan
from app.studio.models import ScenarioCase
from app.studio.preview import DifyPreviewAdapter


LIVE_PREVIEW_ENABLED = (
    os.environ.get("CHAT2DIFY_STUDIO_PREVIEW_LIVE", "").strip() == "1"
)
LOCAL_DIFY_HOSTS = {"localhost", "127.0.0.1", "::1"}

pytestmark = [
    pytest.mark.live_dify,
    pytest.mark.skipif(
        not LIVE_PREVIEW_ENABLED,
        reason=(
            "Set CHAT2DIFY_STUDIO_PREVIEW_LIVE=1 with an explicit isolated "
            "Preview target to import, execute, delete, and verify a temporary app."
        ),
    ),
]


def test_isolated_preview_import_run_cleanup_and_absence_readback() -> None:
    settings = _live_preview_settings()
    version = read_dify_version_info(settings.dify_source_path)
    compiler = DifyDslCompiler(
        dsl_version=version.app_dsl_version,
        default_model_provider=settings.dify_default_model_provider,
        default_model_name=settings.dify_default_model_name,
        default_dataset_ids=settings.dify_default_dataset_ids,
    )
    adapter = DifyPreviewAdapter(settings)
    label = f"c2-preview-live-{uuid4().hex[:8]}-ttl-test"
    plan = WorkflowPlan.model_validate(
        {
            "name": label,
            "description": "Bounded live Scenario Preview acceptance fixture.",
            "app_mode": "workflow",
            "nodes": [
                {
                    "id": "start",
                    "type": "start",
                    "title": "Input",
                    "params": {
                        "variables": [
                            {
                                "name": "query",
                                "type": "paragraph",
                                "required": True,
                                "label": "User input",
                            }
                        ]
                    },
                },
                {
                    "id": "end",
                    "type": "end",
                    "title": "Output",
                    "params": {
                        "outputs": [
                            {
                                "variable": "answer",
                                "value_selector": ["start", "query"],
                            }
                        ]
                    },
                },
            ],
            "edges": [{"source": "start", "target": "end"}],
        }
    )
    scenario = ScenarioCase.model_validate(
        {
            "id": str(uuid4()),
            "name": "Local Preview round trip",
            "source": {"kind": "manual"},
            "inputs": {"query": "phase-3-live-preview"},
            "expected_output": {"kind": "status", "value": "succeeded"},
            "expected_behavior": "Echo the bounded business input without side effects.",
            "invariants": [
                {
                    "kind": "status_is",
                    "target": "succeeded",
                    "description": "The draft run must complete.",
                }
            ],
            "rubric": [
                {
                    "name": "Draft completion",
                    "description": "The deterministic status invariant passes.",
                    "weight": 100,
                    "invariant_indexes": [0],
                }
            ],
            "tags": ["live-preview"],
        }
    )
    app_id: str | None = None

    try:
        imported = adapter.import_candidate(
            yaml_content=compiler.compile(plan),
            label=label,
            idempotency_key=f"scenario-live-{uuid4()}",
        )
        app_id = imported.app_id
        assert adapter.verify_absent(app_id) is False
        result = adapter.execute_case(
            app_id=app_id,
            app_mode="workflow",
            scenario=scenario,
            timeout_seconds=120,
            cancellation_check=lambda: None,
        )
        assert result.ok is True
        assert result.status == "succeeded"
    finally:
        if app_id is not None:
            adapter.delete_fixture(app_id)
            assert adapter.verify_absent(app_id) is True


def _live_preview_settings() -> Settings:
    settings = load_settings()
    if not settings.studio_preview_enabled:
        pytest.fail("CHAT2DIFY_STUDIO_PREVIEW_ENABLED=true is required.")
    if not settings.studio_preview_console_api_base:
        pytest.fail("An explicit Preview API base is required.")
    if urlsplit(settings.studio_preview_console_api_base).hostname not in LOCAL_DIFY_HOSTS:
        pytest.fail("Live Scenario Preview acceptance is restricted to localhost Dify.")
    if not settings.studio_preview_email or not settings.studio_preview_password:
        pytest.fail("Explicit Preview email and password are required.")
    return settings
