from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Literal
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field, computed_field, model_validator


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
BlueprintVisibility = Literal["builtin", "private", "team"]
BlueprintVersionStatus = Literal[
    "draft",
    "pending_review",
    "published",
    "rejected",
    "deprecated",
]
BlueprintSetupKind = Literal[
    "model",
    "dataset",
    "tool",
    "trigger",
    "prompt",
    "variable",
    "policy",
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


class BlueprintPreviewNode(StrictModel):
    ref: str = Field(min_length=1, max_length=128)
    label: str = Field(min_length=1, max_length=256)
    kind: str = Field(min_length=1, max_length=128)
    tone: Literal["neutral", "model", "resource", "decision", "external"] = (
        "neutral"
    )


class BlueprintPreviewEdge(StrictModel):
    source: str = Field(min_length=1, max_length=128)
    target: str = Field(min_length=1, max_length=128)
    label: str = Field(default="", max_length=256)


class BlueprintPreview(StrictModel):
    nodes: list[BlueprintPreviewNode] = Field(min_length=1, max_length=40)
    edges: list[BlueprintPreviewEdge] = Field(default_factory=list, max_length=80)
    expected_behavior: list[str] = Field(default_factory=list, max_length=20)


class BlueprintSetupField(StrictModel):
    id: str = Field(min_length=1, max_length=128, pattern=r"^[a-z][a-z0-9_]*$")
    kind: BlueprintSetupKind
    label: str = Field(min_length=1, max_length=256)
    help_text: str = Field(default="", max_length=2_000)
    required: bool = True
    multiple: bool = False
    secret: Literal[False] = False
    options: list[dict[str, str]] = Field(default_factory=list, max_length=100)
    default: str | bool | int | float | list[str] | None = None


class BlueprintResourceRequirement(StrictModel):
    kind: Literal["model", "dataset", "tool", "trigger", "capability"]
    setup_field_id: str | None = Field(default=None, max_length=128)
    capability: str | None = Field(default=None, max_length=128)
    optional: bool = False
    reason: str = Field(min_length=1, max_length=1_000)


class BlueprintScenario(StrictModel):
    name: str = Field(min_length=1, max_length=256)
    input_summary: str = Field(min_length=1, max_length=2_000)
    expected: str = Field(min_length=1, max_length=2_000)


class BlueprintProvenance(StrictModel):
    source: Literal["chat2dify", "project", "extracted"]
    author: str = Field(min_length=1, max_length=768)
    source_blueprint_id: str | None = Field(default=None, max_length=128)
    extracted_candidate_id: str | None = Field(default=None, max_length=128)
    untrusted_metadata: bool = True


class BlueprintDefinition(StrictModel):
    id: str = Field(min_length=1, max_length=128)
    slug: str = Field(min_length=1, max_length=128, pattern=r"^[a-z0-9][a-z0-9-]*$")
    name: str = Field(min_length=1, max_length=256)
    business_outcome: str = Field(min_length=1, max_length=4_000)
    description: str = Field(min_length=1, max_length=4_000)
    category: str = Field(min_length=1, max_length=128)
    use_cases: list[str] = Field(min_length=1, max_length=20)
    preview: BlueprintPreview
    supported_app_modes: set[str] = Field(min_length=1)
    dify_version_range: str = Field(min_length=1, max_length=128)
    dsl_versions: set[str] = Field(min_length=1)
    setup_schema: list[BlueprintSetupField] = Field(default_factory=list, max_length=40)
    capabilities: list[str] = Field(default_factory=list, max_length=40)
    resources: list[BlueprintResourceRequirement] = Field(
        default_factory=list,
        max_length=40,
    )
    estimated_cost: Literal["none", "low", "medium", "high", "variable"]
    risk: Literal["low", "medium", "high"]
    risk_reasons: list[str] = Field(default_factory=list, max_length=20)
    validators: list[str] = Field(default_factory=list, max_length=40)
    scenarios: list[BlueprintScenario] = Field(default_factory=list, max_length=20)
    provenance: BlueprintProvenance
    version: str = Field(pattern=r"^\d+\.\d+\.\d+$")
    visibility: BlueprintVisibility
    project_id: str | None = Field(default=None, max_length=128)
    deprecated: bool = False
    deprecation_message: str | None = Field(default=None, max_length=2_000)
    upgrade_notes: list[str] = Field(default_factory=list, max_length=40)
    published_at: datetime | None = None

    @model_validator(mode="after")
    def validate_blueprint_contract(self) -> "BlueprintDefinition":
        field_ids = [field.id for field in self.setup_schema]
        if len(field_ids) != len(set(field_ids)):
            raise ValueError("Blueprint setup field IDs must be unique.")
        known = set(field_ids)
        for requirement in self.resources:
            if (
                requirement.setup_field_id is not None
                and requirement.setup_field_id not in known
            ):
                raise ValueError(
                    "Blueprint resource requirement references an unknown setup field."
                )
        if self.visibility == "builtin" and self.project_id is not None:
            raise ValueError("Builtin Blueprints cannot belong to one Project.")
        if self.visibility != "builtin" and self.project_id is None:
            raise ValueError("Private and Team Blueprints require a Project.")
        return self


class BlueprintAvailability(StrictModel):
    compatible: bool
    applicable: bool
    reasons: list[dict[str, str]] = Field(default_factory=list, max_length=40)
    available_resources: dict[str, list[dict[str, str]]] = Field(
        default_factory=dict
    )


class BlueprintGalleryItem(StrictModel):
    blueprint: BlueprintDefinition
    availability: BlueprintAvailability
    score: int = Field(default=0, ge=0, le=10_000)
    version_status: BlueprintVersionStatus = "published"
    version_created_by: str | None = Field(default=None, max_length=768)
    can_review: bool = False
    can_propose: bool = False


class BlueprintGallery(StrictModel):
    project: Project
    membership: Membership
    items: list[BlueprintGalleryItem]
    categories: list[str]
    filters: dict[str, Any] = Field(default_factory=dict)
    state: Literal["ready", "empty", "partial_error", "permission_denied", "offline"]
    message: str
    generated_at: datetime = Field(default_factory=utc_now)


class BlueprintSetupValue(StrictModel):
    field_id: str = Field(min_length=1, max_length=128)
    kind: BlueprintSetupKind
    value: str | bool | int | float | list[str]


class BlueprintSetupValidation(StrictModel):
    ok: bool
    field_results: list[dict[str, Any]] = Field(default_factory=list)
    preview: BlueprintPreview | None = None
    expected_behavior: list[str] = Field(default_factory=list)
    risk: dict[str, Any] = Field(default_factory=dict)
    normalized_values: dict[str, Any] = Field(default_factory=dict, exclude=True)


class BlueprintApplication(StrictModel):
    id: str
    project_id: str
    build_id: str
    candidate_id: str
    blueprint_id: str
    blueprint_version: str
    setup_hash: str
    applied_by: str
    applied_at: datetime


class BlueprintApplyResult(StrictModel):
    application: BlueprintApplication
    build: BuildStudioView
    patch_operation_count: int = Field(ge=1)
    workspace_version_id: str
    source_head_unchanged: bool
    dify_write_count: Literal[0] = 0


class BlueprintInterfaceField(StrictModel):
    name: str = Field(min_length=1, max_length=128, pattern=r"^[a-z][a-z0-9_]*$")
    value_type: Literal[
        "string",
        "number",
        "boolean",
        "object",
        "array[string]",
        "array[object]",
    ]
    description: str = Field(min_length=1, max_length=1_000)
    required: bool = True


class BlueprintTypedInterface(StrictModel):
    inputs: list[BlueprintInterfaceField] = Field(default_factory=list, max_length=40)
    outputs: list[BlueprintInterfaceField] = Field(default_factory=list, max_length=40)
    resources: list[BlueprintSetupField] = Field(default_factory=list, max_length=40)

    @model_validator(mode="after")
    def require_interface(self) -> "BlueprintTypedInterface":
        if not (self.inputs or self.outputs or self.resources):
            raise ValueError("Extracted Blueprints require an explicit typed interface.")
        return self


class BlueprintVersionRecord(StrictModel):
    id: str
    blueprint_id: str
    project_id: str | None = None
    version: str = Field(pattern=r"^\d+\.\d+\.\d+$")
    status: BlueprintVersionStatus
    definition: BlueprintDefinition
    created_by: str
    reviewed_by: str | None = None
    review_note: str | None = None
    created_at: datetime
    reviewed_at: datetime | None = None


class BlueprintUpgradePreview(StrictModel):
    application: BlueprintApplication
    source: BlueprintDefinition
    target: BlueprintDefinition
    changes: list[dict[str, Any]]
    automatic: Literal[False] = False
    action_required: Literal["apply_as_new_candidate"] = "apply_as_new_candidate"
