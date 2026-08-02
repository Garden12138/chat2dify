from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Literal
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field, computed_field


StudioRole = Literal["owner", "admin", "builder", "reviewer", "viewer"]
ProjectKind = Literal["personal", "team"]
LeaseStatus = Literal["pending", "leased", "completed", "failed", "ambiguous"]
BuildOperation = Literal["create", "modify"]
BuildEntrySource = Literal["home", "canvas", "create"]
BuildStatus = Literal["active", "cancelled"]
CandidateStatus = Literal[
    "queued",
    "building",
    "waiting_input",
    "valid",
    "invalid",
    "cancelled",
    "interrupted",
    "conflicted",
]


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def new_id() -> str:
    return str(uuid4())


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class Principal(StrictModel):
    issuer: str = Field(min_length=1, max_length=256)
    subject: str = Field(min_length=1, max_length=256)
    display_name: str = Field(min_length=1, max_length=256)
    email: str | None = Field(default=None, max_length=512)
    dify_tenant_id: str = Field(min_length=1, max_length=256)

    @computed_field
    @property
    def key(self) -> str:
        return f"{self.issuer}:{self.subject}"


class DifyAppSummary(StrictModel):
    id: str = Field(min_length=1, max_length=256)
    name: str = Field(min_length=1, max_length=512)
    mode: str = Field(min_length=1, max_length=64)
    description: str = Field(default="", max_length=2_000)
    updated_at: datetime | None = None
    created_at: datetime | None = None
    icon: str | None = Field(default=None, max_length=2_000)
    icon_background: str | None = Field(default=None, max_length=128)


class VerifiedHostContext(StrictModel):
    principal: Principal
    apps: list[DifyAppSummary] = Field(default_factory=list, max_length=1_000)
    apps_available: bool = True
    apps_error_code: str | None = None
    set_cookie_headers: list[str] = Field(
        default_factory=list,
        max_length=3,
        exclude=True,
    )


class Project(StrictModel):
    id: str = Field(min_length=1, max_length=128)
    slug: str = Field(min_length=1, max_length=128)
    name: str = Field(min_length=1, max_length=256)
    kind: ProjectKind
    dify_tenant_id: str = Field(min_length=1, max_length=256)
    version: int = Field(ge=1)
    created_at: datetime
    updated_at: datetime


class Membership(StrictModel):
    id: str = Field(min_length=1, max_length=128)
    project_id: str = Field(min_length=1, max_length=128)
    principal_key: str = Field(min_length=1, max_length=768)
    role: StudioRole
    version: int = Field(ge=1)
    created_at: datetime
    updated_at: datetime


class StudioSession(StrictModel):
    id: str = Field(min_length=1, max_length=128)
    jti_hash: str = Field(min_length=32, max_length=128)
    principal_key: str = Field(min_length=1, max_length=768)
    project_id: str = Field(min_length=1, max_length=128)
    dify_account_id: str = Field(min_length=1, max_length=256)
    dify_tenant_id: str = Field(min_length=1, max_length=256)
    origin: str = Field(min_length=1, max_length=512)
    nonce_hash: str = Field(min_length=32, max_length=128)
    expires_at: datetime
    created_at: datetime
    revoked_at: datetime | None = None


class Activity(StrictModel):
    id: str
    project_id: str
    principal_key: str
    kind: str
    entity_type: str
    entity_id: str
    summary: dict[str, Any] = Field(default_factory=dict)
    created_at: datetime


class DurableJob(StrictModel):
    id: str
    project_id: str
    kind: str
    payload: dict[str, Any] = Field(default_factory=dict)
    status: LeaseStatus
    attempts: int = Field(ge=0)
    max_attempts: int = Field(ge=1)
    lease_owner: str | None = None
    lease_expires_at: datetime | None = None
    idempotency_key: str
    version: int = Field(ge=1)
    created_at: datetime
    updated_at: datetime


class OutboxMessage(StrictModel):
    id: str
    project_id: str
    topic: str
    payload: dict[str, Any] = Field(default_factory=dict)
    status: LeaseStatus
    attempts: int = Field(ge=0)
    max_attempts: int = Field(ge=1)
    lease_owner: str | None = None
    lease_expires_at: datetime | None = None
    idempotency_key: str
    version: int = Field(ge=1)
    created_at: datetime
    updated_at: datetime


class ExternalReceipt(StrictModel):
    id: str
    project_id: str
    operation: str
    idempotency_key: str
    outcome: Literal["succeeded", "failed", "ambiguous"]
    external_ref: str | None = None
    details: dict[str, Any] = Field(default_factory=dict)
    created_at: datetime


class StudioHomeApp(StrictModel):
    id: str
    name: str
    mode: str
    description: str = ""
    updated_at: datetime | None = None
    build_url: str


class V4WorkItem(StrictModel):
    run_id: str
    session_id: str
    app_id: str
    app_name: str
    app_mode: str
    goal: str
    phase: str
    updated_at: datetime
    resumable: bool
    resume_requires_message: bool = False
    reason_code: str | None = None
    reason: str | None = None
    build_url: str


class HomeSectionState(StrictModel):
    state: Literal["ready", "empty", "partial_error", "permission_denied", "offline"]
    message: str
    recoverable: bool = False


class StudioHome(StrictModel):
    project: Project
    membership: Membership
    apps: list[StudioHomeApp]
    work: list[V4WorkItem]
    drafts: list[dict[str, Any]] = Field(default_factory=list)
    assigned_reviews: list[dict[str, Any]] = Field(default_factory=list)
    releases: list[dict[str, Any]] = Field(default_factory=list)
    quality_regressions: list[dict[str, Any]] = Field(default_factory=list)
    incidents: list[dict[str, Any]] = Field(default_factory=list)
    states: dict[str, HomeSectionState]
    generated_at: datetime = Field(default_factory=utc_now)


class StudioBuild(StrictModel):
    id: str = Field(min_length=1, max_length=128)
    project_id: str = Field(min_length=1, max_length=128)
    created_by: str = Field(min_length=1, max_length=768)
    operation: BuildOperation
    entry_source: BuildEntrySource
    app_id: str | None = Field(default=None, max_length=256)
    app_mode: str = Field(min_length=1, max_length=64)
    app_name: str = Field(min_length=1, max_length=512)
    base_fingerprint: str | None = Field(default=None, max_length=512)
    selected_candidate_id: str | None = Field(default=None, max_length=128)
    status: BuildStatus = "active"
    version: int = Field(ge=1)
    created_at: datetime
    updated_at: datetime


class StudioCandidate(StrictModel):
    id: str = Field(min_length=1, max_length=128)
    project_id: str = Field(min_length=1, max_length=128)
    build_id: str = Field(min_length=1, max_length=128)
    run_id: str = Field(min_length=1, max_length=128)
    label: str = Field(min_length=1, max_length=256)
    intent: str = Field(min_length=1, max_length=4_000)
    source_candidate_ids: list[str] = Field(default_factory=list, max_length=3)
    base_fingerprint: str | None = Field(default=None, max_length=512)
    status: CandidateStatus = "queued"
    ordinal: int = Field(ge=1, le=100)
    version: int = Field(ge=1)
    created_at: datetime
    updated_at: datetime


class CandidatePresentation(StrictModel):
    candidate: StudioCandidate
    phase: str
    workspace_version_id: str | None = None
    business_summary: str
    assumptions: list[str] = Field(default_factory=list)
    changed_path: list[str] = Field(default_factory=list)
    risk: dict[str, Any] = Field(default_factory=dict)
    validation: dict[str, Any] = Field(default_factory=dict)
    side_effects: dict[str, Any] = Field(default_factory=dict)
    unresolved_questions: list[str] = Field(default_factory=list)
    goal_plan: dict[str, Any] = Field(default_factory=dict)
    timeline: list[dict[str, Any]] = Field(default_factory=list)
    technical_detail: dict[str, Any] = Field(default_factory=dict)
    reconstructable: bool = False
    layout_preview: dict[str, Any] | None = None
    error: dict[str, Any] | None = None


class BuildStudioView(StrictModel):
    build: StudioBuild
    candidates: list[CandidatePresentation] = Field(default_factory=list)
    comparison: dict[str, Any] = Field(default_factory=dict)
    selected_context: dict[str, Any] = Field(default_factory=dict)
    generated_at: datetime = Field(default_factory=utc_now)
