from __future__ import annotations

from contextlib import AbstractContextManager
from copy import deepcopy
from hashlib import sha256
import json
from typing import Any, Callable, Protocol

from app.agent.compatibility import DifyCompatibilityMatrix
from app.agent.config_patch import (
    ConfigAgentSet,
    ConfigExperienceSet,
    ConfigModelSet,
    ConfigPatchDocument,
    ConfigPromptSet,
    config_patch_risk,
)
from app.agent.execution import NodeSideEffect, SideEffectSummary
from app.agent.state import (
    AgentConfigSnapshot,
    AgentRun,
    GoalPlan,
    StrictModel,
    WorkspaceVersion,
    utc_now,
)
from app.agent.store import AgentStore, AgentStoreConflict
from app.agent.trace import redact_sensitive_data
from app.agent.validation import AgentValidationIssue, AgentValidationReport
from app.agent.workspace import WorkspaceOperationError
from app.dify.client import DifyAppDetail
from app.dify.version import DifyVersionInfo


CONFIG_APP_MODES = frozenset({"chat", "completion", "agent-chat"})


class ConfigSnapshotClient(Protocol):
    def get_app_detail(self, app_id: str) -> DifyAppDetail: ...


class ConfigAppSnapshotService:
    def __init__(
        self,
        *,
        client_factory: Callable[
            [], AbstractContextManager[ConfigSnapshotClient]
        ],
        dify_version: DifyVersionInfo,
        compatibility: DifyCompatibilityMatrix,
        default_model_provider: str = "openai",
        default_model_name: str = "gpt-4o-mini",
    ) -> None:
        self.client_factory = client_factory
        self.dify_version = dify_version
        self.compatibility = compatibility
        self.default_model_provider = default_model_provider
        self.default_model_name = default_model_name

    def capture(self, session) -> AgentConfigSnapshot:
        if session.operation == "create":
            return self._create_scaffold_snapshot(session)
        if session.operation != "modify" or not session.app_id:
            raise WorkspaceOperationError(
                "CONFIG_APP_CREATE_UNSUPPORTED",
                (
                    "Builder Agent configuration-app scope modifies existing "
                    "apps only; use the preserved v3 configured-app creation "
                    "path for new apps."
                ),
            )
        with self.client_factory() as client:
            app = client.get_app_detail(session.app_id)
        app_mode = str(app.mode or session.app_mode or "")
        if app_mode not in CONFIG_APP_MODES:
            raise WorkspaceOperationError(
                "CONFIG_APP_MODE_UNSUPPORTED",
                "The selected Dify app is not a Chatbot, Completion, or Agent app.",
            )
        if session.app_mode is not None and session.app_mode != app_mode:
            raise WorkspaceOperationError(
                "AGENT_APP_MODE_MISMATCH",
                (
                    f"Session mode {session.app_mode} does not match Dify mode "
                    f"{app_mode}."
                ),
            )
        config = extract_model_config(app)
        if config is None:
            raise WorkspaceOperationError(
                "CONFIG_MODEL_CONFIG_MISSING",
                "Dify app detail did not include a model configuration.",
            )
        decision = self.compatibility.decide(
            self.dify_version,
            app_mode=app_mode,
        )
        capabilities = self.compatibility.pin_capabilities(
            config_capabilities(app_mode),
            decision=decision,
        )
        return AgentConfigSnapshot(
            operation="modify",
            app_id=session.app_id,
            app_name=app.name or f"Dify {app_mode} app",
            app_description=app.description,
            app_mode=app_mode,
            base_hash=model_config_hash(app, config),
            base_config=deepcopy(config),
            dify_version={
                "source_dir": self.dify_version.source_dir,
                "git_describe": self.dify_version.git_describe,
                "app_dsl_version": self.dify_version.app_dsl_version,
            },
            capabilities=capabilities,
            compatibility=decision.model_dump(mode="json"),
        )

    def _create_scaffold_snapshot(self, session) -> AgentConfigSnapshot:
        app_mode = str(session.app_mode or "")
        if app_mode not in CONFIG_APP_MODES:
            raise WorkspaceOperationError(
                "CONFIG_APP_MODE_UNSUPPORTED",
                "Configured-app creation requires Chatbot, Completion, or Agent mode.",
            )
        if session.app_id is not None:
            raise WorkspaceOperationError(
                "CONFIG_APP_CREATE_ALREADY_BOUND",
                "A configured-app create Session cannot already reference a Dify app.",
            )
        decision = self.compatibility.decide(
            self.dify_version,
            app_mode=app_mode,
        )
        config = _create_config_scaffold(
            app_mode,
            provider=self.default_model_provider,
            model_name=self.default_model_name,
        )
        return AgentConfigSnapshot(
            operation="create",
            app_id=None,
            app_name=session.app_name or _default_config_app_name(app_mode),
            app_description=(
                session.app_description
                or "Created from a deterministic Chat2Dify Config Workspace scaffold."
            ),
            app_mode=app_mode,
            base_hash=None,
            base_config=config,
            dify_version={
                "source_dir": self.dify_version.source_dir,
                "git_describe": self.dify_version.git_describe,
                "app_dsl_version": self.dify_version.app_dsl_version,
            },
            capabilities=self.compatibility.pin_capabilities(
                config_capabilities(app_mode),
                decision=decision,
            ),
            compatibility=decision.model_dump(mode="json"),
        )


class ConfigPatchApplyResult(StrictModel):
    workspace_version: str
    parent_version: str
    validation: AgentValidationReport
    risk: str


class ConfigReview(StrictModel):
    workspace_version_id: str
    ready: bool
    validation: AgentValidationReport
    business_diff: list[str]
    technical_diff: list[dict[str, Any]]
    risk: dict[str, Any]
    side_effects: SideEffectSummary
    test_result: dict[str, Any] | None = None


class VersionedConfigWorkspace:
    def __init__(self, *, store: AgentStore) -> None:
        self.store = store

    def initialize(
        self,
        run: AgentRun,
        snapshot: AgentConfigSnapshot,
        goal_plan: GoalPlan,
    ) -> tuple[AgentRun, WorkspaceVersion]:
        report = validate_config(snapshot.app_mode, snapshot.base_config)
        if not report.ok:
            raise WorkspaceOperationError(
                "WORKSPACE_BASE_INVALID",
                "The authoritative Dify model configuration is invalid.",
                details=[
                    issue.model_dump(mode="json")
                    for issue in report.issues
                ],
            )
        version = WorkspaceVersion(
            run_id=run.id,
            base_hash=snapshot.base_hash,
            snapshot=deepcopy(snapshot.base_config),
            validation=report.model_dump(mode="json"),
        )
        initialized = AgentRun.model_validate(
            {
                **run.model_dump(),
                "base_hash": snapshot.base_hash,
                "head_version_id": version.id,
                "snapshot": snapshot.model_dump(mode="json"),
                "goal_plan": goal_plan.model_dump(mode="json"),
                "updated_at": utc_now(),
            }
        )
        return self.store.initialize_run_workspace(initialized, version)

    def apply_patch(
        self,
        run_id: str,
        patch: ConfigPatchDocument,
    ) -> ConfigPatchApplyResult:
        run = self.store.get_run(run_id)
        if not isinstance(run.snapshot, AgentConfigSnapshot):
            raise WorkspaceOperationError(
                "CONFIG_WORKSPACE_REQUIRED",
                "Config Patch requires a configuration-app Snapshot.",
            )
        if not bool(run.snapshot.compatibility.get("mutation_supported", True)):
            raise WorkspaceOperationError(
                "DIFY_VERSION_MUTATION_UNSUPPORTED",
                str(
                    run.snapshot.compatibility.get("reason")
                    or "This Dify/DSL version is diagnostic-only."
                ),
            )
        head = self.store.get_workspace_head(run_id)
        if patch.workspace_version != head.id:
            raise WorkspaceOperationError(
                "WORKSPACE_VERSION_MISMATCH",
                "Config Patch version does not match the Workspace head.",
                details=[
                    {
                        "expected": head.id,
                        "actual": patch.workspace_version,
                    }
                ],
            )
        if patch.expected_base_hash != run.base_hash:
            raise WorkspaceOperationError(
                "WORKSPACE_BASE_HASH_MISMATCH",
                "Config Patch does not match the pinned model-config Hash.",
                details=[
                    {
                        "expected": run.base_hash,
                        "actual": patch.expected_base_hash,
                    }
                ],
            )
        if patch.app_mode != run.snapshot.app_mode:
            raise WorkspaceOperationError(
                "CONFIG_APP_MODE_MISMATCH",
                "Config Patch app_mode does not match the persisted Snapshot.",
            )
        before = deepcopy(head.snapshot)
        after = deepcopy(before)
        for operation in patch.operations:
            _apply_config_operation(after, operation)
        report = validate_config(run.snapshot.app_mode, after)
        if not report.ok:
            raise WorkspaceOperationError(
                "WORKSPACE_PATCH_VALIDATION_FAILED",
                "Patched model configuration failed deterministic validation.",
                details=[
                    issue.model_dump(mode="json")
                    for issue in report.issues
                ],
                retryable=True,
            )
        version = WorkspaceVersion(
            run_id=run.id,
            parent_id=head.id,
            base_hash=run.base_hash,
            patch=patch.model_dump(mode="json"),
            reverse_patch={
                "type": "config.snapshot.restore",
                "from_version": head.id,
                "snapshot": before,
            },
            snapshot=after,
            validation=report.model_dump(mode="json"),
        )
        try:
            self.store.commit_workspace_version(
                version,
                expected_head_id=head.id,
                event_message=(
                    "Accepted Config Patch created a new Workspace version."
                ),
                event_data={
                    "domain": "config",
                    "operation_count": len(patch.operations),
                    "rationale": patch.rationale,
                    "risk": config_patch_risk(patch),
                },
            )
        except AgentStoreConflict as exc:
            raise WorkspaceOperationError(
                "WORKSPACE_VERSION_CONFLICT",
                str(exc),
                retryable=True,
            ) from exc
        return ConfigPatchApplyResult(
            workspace_version=version.id,
            parent_version=head.id,
            validation=report,
            risk=config_patch_risk(patch),
        )

    def validate_head(self, run_id: str) -> AgentValidationReport:
        run = self.store.get_run(run_id)
        if not isinstance(run.snapshot, AgentConfigSnapshot):
            raise WorkspaceOperationError(
                "CONFIG_WORKSPACE_REQUIRED",
                "Config validation requires a configuration-app Snapshot.",
            )
        head = self.store.get_workspace_head(run_id)
        report = validate_config(run.snapshot.app_mode, head.snapshot)
        self.store.update_workspace_version(
            head.id,
            validation=report.model_dump(mode="json"),
        )
        return report

    def precommit_config(
        self,
        run_id: str,
        version_id: str,
    ) -> tuple[AgentRun, WorkspaceVersion, dict[str, Any]]:
        run = self.store.get_run(run_id)
        if run.head_version_id != version_id:
            raise WorkspaceOperationError(
                "COMMIT_WORKSPACE_VERSION_MISMATCH",
                "Commit version must be the current persisted Workspace head.",
            )
        version = self.store.get_workspace_version(version_id)
        if version.run_id != run_id or version.base_hash != run.base_hash:
            raise WorkspaceOperationError(
                "COMMIT_WORKSPACE_INVALID",
                "Commit version does not belong to the pinned Run and base Hash.",
            )
        report = AgentValidationReport.model_validate(version.validation or {})
        if not report.ok:
            raise WorkspaceOperationError(
                "COMMIT_REQUIRES_VALIDATED_HEAD",
                "Commit requires a deterministically validated Config Workspace.",
            )
        return run, version, deepcopy(version.snapshot)


class ConfigReviewService:
    def __init__(
        self,
        *,
        store: AgentStore,
        workspace: VersionedConfigWorkspace,
    ) -> None:
        self.store = store
        self.workspace = workspace

    def build(self, run_id: str) -> ConfigReview:
        run = self.store.get_run(run_id)
        if not isinstance(run.snapshot, AgentConfigSnapshot):
            raise WorkspaceOperationError(
                "CONFIG_WORKSPACE_REQUIRED",
                "Config review requires a configuration-app Snapshot.",
            )
        head = self.store.get_workspace_head(run_id)
        validation = self.workspace.validate_head(run_id)
        changes = diff_config(run.snapshot.base_config, head.snapshot)
        risk = config_review_risk(head, changes)
        review = ConfigReview(
            workspace_version_id=head.id,
            ready=validation.ok,
            validation=validation,
            business_diff=[
                str(change["message"])
                for change in changes
            ],
            technical_diff=redact_sensitive_data(changes),
            risk=risk,
            side_effects=(
                validation.side_effects
                or classify_config_side_effects(
                    run.snapshot.app_mode,
                    head.snapshot,
                )
            ),
            test_result=None,
        )
        updated = AgentRun.model_validate(
            {
                **run.model_dump(),
                "review": review.model_dump(mode="json"),
                "updated_at": utc_now(),
            }
        )
        self.store.update_run(updated)
        return review


def validate_config(
    app_mode: str,
    config: dict[str, Any],
) -> AgentValidationReport:
    issues: list[AgentValidationIssue] = []
    model = config.get("model")
    if not isinstance(model, dict):
        issues.append(
            _config_issue(
                "CONFIG_MODEL_REQUIRED",
                "model",
                "Configured apps require a model object.",
            )
        )
    else:
        for field in ("provider", "name"):
            if not str(model.get(field) or "").strip():
                issues.append(
                    _config_issue(
                        "CONFIG_MODEL_IDENTITY_REQUIRED",
                        f"model.{field}",
                        f"Configured app model.{field} is required.",
                    )
                )
        completion_params = model.get("completion_params", {})
        if not isinstance(completion_params, dict):
            issues.append(
                _config_issue(
                    "CONFIG_COMPLETION_PARAMS_INVALID",
                    "model.completion_params",
                    "model.completion_params must be an object.",
                )
            )
    if "pre_prompt" in config and not isinstance(config.get("pre_prompt"), str):
        issues.append(
            _config_issue(
                "CONFIG_PROMPT_INVALID",
                "pre_prompt",
                "pre_prompt must be a string.",
            )
        )
    for field in ("file_upload", "dataset_configs"):
        if field in config and not isinstance(config.get(field), dict):
            issues.append(
                _config_issue(
                    "CONFIG_FIELD_INVALID",
                    field,
                    f"{field} must be an object.",
                )
            )
    if app_mode == "agent-chat":
        agent_mode = config.get("agent_mode")
        if not isinstance(agent_mode, dict) or not agent_mode.get("enabled"):
            issues.append(
                _config_issue(
                    "CONFIG_AGENT_MODE_REQUIRED",
                    "agent_mode.enabled",
                    "Agent apps must enable agent_mode.",
                )
            )
        elif not str(agent_mode.get("strategy") or "").strip():
            issues.append(
                _config_issue(
                    "CONFIG_AGENT_STRATEGY_REQUIRED",
                    "agent_mode.strategy",
                    "Agent apps require an agent strategy.",
                )
            )
    try:
        json.dumps(
            config,
            ensure_ascii=False,
            separators=(",", ":"),
            allow_nan=False,
        )
    except (TypeError, ValueError):
        issues.append(
            _config_issue(
                "CONFIG_JSON_INVALID",
                "model_config",
                "Model configuration must contain JSON-compatible values.",
            )
        )
    return AgentValidationReport(
        ok=not any(issue.severity == "error" for issue in issues),
        issues=issues,
        dsl_version="config-domain",
        roundtrip_ok=True,
        graph_compiled=False,
        side_effects=classify_config_side_effects(app_mode, config),
    )


def _create_config_scaffold(
    app_mode: str,
    *,
    provider: str,
    model_name: str,
) -> dict[str, Any]:
    config: dict[str, Any] = {
        "model": {
            "provider": provider,
            "name": model_name,
            "mode": "chat",
            "completion_params": {},
        },
        "pre_prompt": "",
        "opening_statement": "",
        "suggested_questions": [],
        "file_upload": {"enabled": False},
        "dataset_configs": {"retrieval_model": "single"},
    }
    if app_mode == "agent-chat":
        config["agent_mode"] = {
            "enabled": True,
            "strategy": "react",
            "tools": [],
        }
    return config


def _default_config_app_name(app_mode: str) -> str:
    return {
        "chat": "New Chatbot",
        "completion": "New Completion App",
        "agent-chat": "New Dify Agent",
    }[app_mode]


def classify_config_side_effects(
    app_mode: str,
    config: dict[str, Any],
) -> SideEffectSummary:
    nodes = [
        NodeSideEffect(
            node_id="model_config.model",
            node_type="configured-model",
            title="Configured model",
            kind="model_cost",
            external=False,
        )
    ]
    agent_mode = config.get("agent_mode")
    tools = (
        agent_mode.get("tools")
        if isinstance(agent_mode, dict)
        and isinstance(agent_mode.get("tools"), list)
        else []
    )
    for index, tool in enumerate(tools):
        tool_name = (
            str(tool.get("tool_name") or tool.get("name") or f"tool-{index}")
            if isinstance(tool, dict)
            else f"tool-{index}"
        )
        nodes.append(
            NodeSideEffect(
                node_id=f"model_config.agent_mode.tools.{index}",
                node_type="configured-tool",
                title=tool_name,
                kind="tool",
                external=True,
            )
        )
    counts: dict[str, int] = {}
    for node in nodes:
        counts[node.kind] = counts.get(node.kind, 0) + 1
    return SideEffectSummary(
        highest_risk="external" if tools else "model_cost",
        counts=counts,
        nodes=nodes,
        requires_per_run_approval=bool(tools),
        trigger_based=False,
    )


def extract_model_config(app: DifyAppDetail | None) -> dict[str, Any] | None:
    if app is None:
        return None
    raw = app.raw if isinstance(app.raw, dict) else {}
    for key in ("model_config", "model_config_data", "app_model_config"):
        value = raw.get(key)
        if isinstance(value, dict):
            return deepcopy(value)
    return None


def model_config_hash(
    app: DifyAppDetail | None,
    config: dict[str, Any],
) -> str:
    raw = app.raw if app and isinstance(app.raw, dict) else {}
    for source in (
        config,
        raw.get("model_config")
        if isinstance(raw.get("model_config"), dict)
        else {},
        raw,
    ):
        for key in ("hash", "updated_at", "version"):
            value = source.get(key) if isinstance(source, dict) else None
            if value not in (None, ""):
                return str(value)
    payload = json.dumps(
        config,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")
    return f"sha256:{sha256(payload).hexdigest()}"


def model_config_hash_from_payload(payload: dict[str, Any]) -> str | None:
    for source in (
        payload,
        payload.get("model_config")
        if isinstance(payload.get("model_config"), dict)
        else {},
        payload.get("data")
        if isinstance(payload.get("data"), dict)
        else {},
    ):
        for key in ("hash", "updated_at", "version"):
            value = source.get(key) if isinstance(source, dict) else None
            if value not in (None, ""):
                return str(value)
    return None


def diff_config(
    before: dict[str, Any],
    after: dict[str, Any],
) -> list[dict[str, Any]]:
    changes: list[dict[str, Any]] = []
    for path in sorted(_changed_paths(before, after)):
        before_value = _path_value(before, path)
        after_value = _path_value(after, path)
        changes.append(
            {
                "type": "config_field_changed",
                "field": path,
                "before": before_value,
                "after": after_value,
                "message": f"Updated configured-app field {path}.",
            }
        )
    return changes


def config_review_risk(
    head: WorkspaceVersion,
    changes: list[dict[str, Any]],
) -> dict[str, Any]:
    patch = head.patch or {}
    risk = "low"
    try:
        if patch:
            risk = config_patch_risk(ConfigPatchDocument.model_validate(patch))
    except ValueError:
        risk = "high"
    no_op = not changes
    return {
        "ok": True,
        "risk": risk,
        "no_op": no_op,
        "issues": [],
    }


def config_capabilities(app_mode: str) -> list[dict[str, Any]]:
    capabilities = [
        {
            "type": "config.prompt",
            "summary": "Set the configured application's pre-prompt.",
            "supported_app_modes": sorted(CONFIG_APP_MODES),
            "operation": "config.prompt.set",
            "risk": "low",
        },
        {
            "type": "config.model",
            "summary": "Select the configured application's model.",
            "supported_app_modes": sorted(CONFIG_APP_MODES),
            "operation": "config.model.set",
            "risk": "medium",
        },
        {
            "type": "config.experience",
            "summary": (
                "Set opening text, suggestions, file upload, or dataset "
                "retrieval configuration."
            ),
            "supported_app_modes": ["chat", "completion"],
            "operation": "config.experience.set",
            "risk": "medium",
        },
    ]
    if app_mode == "agent-chat":
        capabilities.append(
            {
                "type": "config.agent",
                "summary": "Set Agent strategy, prompt, and typed tool bindings.",
                "supported_app_modes": ["agent-chat"],
                "operation": "config.agent.set",
                "risk": "high",
            }
        )
    return capabilities


def _apply_config_operation(
    config: dict[str, Any],
    operation,
) -> None:
    if isinstance(operation, ConfigPromptSet):
        _check_expected(
            config.get("pre_prompt"),
            operation.expected,
            enabled=operation.check_expected,
            field="pre_prompt",
        )
        config["pre_prompt"] = operation.value
        return
    if isinstance(operation, ConfigModelSet):
        current_model = (
            deepcopy(config.get("model"))
            if isinstance(config.get("model"), dict)
            else {}
        )
        expected = (
            operation.expected.model_dump(mode="json")
            if operation.expected is not None
            else None
        )
        _check_expected(
            (
                {
                    key: current_model.get(key)
                    for key in expected
                }
                if expected is not None
                else current_model
            ),
            expected,
            enabled=operation.check_expected,
            field="model",
        )
        current_model.update(operation.value.model_dump(mode="json"))
        config["model"] = current_model
        return
    if isinstance(operation, ConfigExperienceSet):
        selected = {
            key: value
            for key, value in {
                "opening_statement": operation.opening_statement,
                "suggested_questions": operation.suggested_questions,
                "file_upload": operation.file_upload,
                "dataset_configs": operation.dataset_configs,
            }.items()
            if value is not None
        }
        if operation.check_expected:
            for field, expected in (operation.expected or {}).items():
                _check_expected(
                    config.get(field),
                    expected,
                    enabled=True,
                    field=field,
                )
        config.update(deepcopy(selected))
        return
    if isinstance(operation, ConfigAgentSet):
        _check_expected(
            config.get("agent_mode"),
            operation.expected,
            enabled=operation.check_expected,
            field="agent_mode",
        )
        agent_mode = (
            deepcopy(config.get("agent_mode"))
            if isinstance(config.get("agent_mode"), dict)
            else {}
        )
        agent_mode["enabled"] = operation.enabled
        agent_mode["strategy"] = operation.strategy
        if operation.prompt is not None:
            agent_mode["prompt"] = operation.prompt
        if operation.tools is not None:
            agent_mode["tools"] = [
                tool.model_dump(mode="json")
                for tool in operation.tools
            ]
        config["agent_mode"] = agent_mode
        return
    raise WorkspaceOperationError(
        "CONFIG_PATCH_OPERATION_UNKNOWN",
        "Config Patch contains an unsupported operation.",
    )


def _check_expected(
    actual: Any,
    expected: Any,
    *,
    enabled: bool,
    field: str,
) -> None:
    if enabled and actual != expected:
        raise WorkspaceOperationError(
            "CONFIG_PATCH_PRECONDITION_FAILED",
            f"Config Patch precondition failed for {field}.",
            details=[
                {
                    "field": field,
                    "expected": redact_sensitive_data(expected),
                    "actual": redact_sensitive_data(actual),
                }
            ],
            retryable=True,
        )


def _config_issue(
    code: str,
    field: str,
    message: str,
) -> AgentValidationIssue:
    return AgentValidationIssue(
        code=code,
        severity="error",
        field=field,
        message=message,
        repair_hint="Apply a typed Config Patch with a valid field value.",
        retryable=True,
    )


def _changed_paths(
    before: Any,
    after: Any,
    *,
    prefix: str = "",
) -> set[str]:
    if isinstance(before, dict) and isinstance(after, dict):
        result: set[str] = set()
        for key in set(before) | set(after):
            path = f"{prefix}.{key}" if prefix else str(key)
            if key not in before or key not in after:
                result.add(path)
            else:
                result.update(
                    _changed_paths(
                        before[key],
                        after[key],
                        prefix=path,
                    )
                )
        return result
    if before != after:
        return {prefix or "$"}
    return set()


def _path_value(value: Any, path: str) -> Any:
    current = value
    for part in path.split("."):
        if not isinstance(current, dict) or part not in current:
            return None
        current = current[part]
    return deepcopy(current)
