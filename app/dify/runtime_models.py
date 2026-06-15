from __future__ import annotations

from copy import deepcopy
from dataclasses import asdict
from typing import Any, Iterable

from app.config import Settings
from app.dify.client import (
    DifyClient,
    DifyModelListItem,
    DifyModelListResult,
)
from app.models import ValidationIssue, WorkflowPlan


TOOL_CALL_FEATURES = {"tool-call", "multi-tool-call", "stream-tool-call"}
DIRECT_MODEL_NODE_TYPES = {"llm", "question-classifier", "parameter-extractor"}


class RuntimeModelSelectionError(ValueError):
    def __init__(self, detail: dict[str, Any]) -> None:
        self.detail = detail
        super().__init__(str(detail.get("message") or "Invalid runtime model selection."))


def load_runtime_model_catalog(
    client: DifyClient,
    settings: Settings,
) -> DifyModelListResult:
    list_models = getattr(client, "list_models", None)
    if callable(list_models):
        return list_models(model_type="llm")
    # Lightweight test doubles created before runtime-model discovery can still
    # exercise unrelated endpoint behavior.
    item = DifyModelListItem(
        provider=settings.dify_default_model_provider,
        provider_label=settings.dify_default_model_provider,
        model=settings.dify_default_model_name,
        model_label=settings.dify_default_model_name,
        model_type="llm",
        status="active",
        provider_status="active",
        deprecated=False,
        features=["vision", "tool-call", "multi-tool-call", "stream-tool-call"],
        mode="chat",
    )
    return DifyModelListResult(
        data=[item],
        count=1,
        model_type="llm",
        providers=[item.provider],
        features=list(item.features),
    )


def resolve_runtime_model_selections(
    catalog: DifyModelListResult,
    settings: Settings,
    selections: Iterable[Any] | None,
) -> list[DifyModelListItem]:
    requested: list[tuple[str, str]] = []
    for selection in selections or []:
        if hasattr(selection, "model_dump"):
            selection = selection.model_dump()
        if not isinstance(selection, dict):
            continue
        provider = str(selection.get("provider") or "").strip()
        model = str(selection.get("model") or "").strip()
        if provider and model and (provider, model) not in requested:
            requested.append((provider, model))
    if not requested:
        requested = [
            (
                settings.dify_default_model_provider,
                settings.dify_default_model_name,
            )
        ]

    by_identity = {(item.provider, item.model): item for item in catalog.data}
    resolved: list[DifyModelListItem] = []
    for provider, model in requested:
        item = by_identity.get((provider, model))
        if item is None:
            raise RuntimeModelSelectionError(
                {
                    "code": "DIFY_MODEL_NOT_FOUND",
                    "message": "The selected runtime model is not installed in Dify.",
                    "provider": provider,
                    "model": model,
                }
            )
        if item.deprecated:
            raise RuntimeModelSelectionError(
                {
                    "code": "DIFY_MODEL_DEPRECATED",
                    "message": "The selected runtime model is deprecated in Dify.",
                    "provider": provider,
                    "model": model,
                }
            )
        if not item.available:
            raise RuntimeModelSelectionError(
                {
                    "code": "DIFY_MODEL_UNAVAILABLE",
                    "message": "The selected runtime model is not active in Dify.",
                    "provider": provider,
                    "model": model,
                    "status": item.status,
                    "provider_status": item.provider_status,
                }
            )
        resolved.append(item)
    return resolved


def model_selection_payloads(models: Iterable[DifyModelListItem]) -> list[dict[str, Any]]:
    return [asdict(model) for model in models]


def validate_agent_selection_models(
    agent_selections: Iterable[Any] | None,
    catalog: DifyModelListResult,
    allowed_models: Iterable[DifyModelListItem],
) -> list[ValidationIssue]:
    allowed = {(item.provider, item.model) for item in allowed_models}
    catalog_by_identity = {
        (item.provider, item.model): item for item in catalog.data
    }
    issues: list[ValidationIssue] = []
    for selection_index, selection in enumerate(agent_selections or []):
        if hasattr(selection, "model_dump"):
            selection = selection.model_dump()
        if not isinstance(selection, dict):
            continue
        schemas = (
            selection.get("parameters")
            if isinstance(selection.get("parameters"), list)
            else []
        )
        values = (
            selection.get("agent_parameters")
            if isinstance(selection.get("agent_parameters"), dict)
            else {}
        )
        tool_bound = any(
            isinstance(schema, dict)
            and str(schema.get("type") or "") in {
                "tool-selector",
                "multi-tool-selector",
                "array[tools]",
            }
            and _agent_tool_value_enabled(
                values.get(str(schema.get("variable") or schema.get("name") or ""))
            )
            for schema in schemas
        )
        for schema in schemas:
            if not isinstance(schema, dict) or str(schema.get("type") or "") != "model-selector":
                continue
            name = str(schema.get("variable") or schema.get("name") or "").strip()
            raw_value = values.get(name)
            selected = raw_value.get("value") if isinstance(raw_value, dict) else raw_value
            selected = selected if isinstance(selected, dict) else {}
            binding = {
                "path": f"agent_selections.{selection_index}.agent_parameters.{name}",
                "node_id": None,
                "provider": str(selected.get("provider") or "").strip(),
                "model": str(selected.get("model") or selected.get("name") or "").strip(),
                "required_features": set(TOOL_CALL_FEATURES) if tool_bound else set(),
            }
            identity = (binding["provider"], binding["model"])
            if identity not in allowed:
                issues.append(
                    _model_issue(
                        "AGENT_MODEL_NOT_SELECTED",
                        "The Agent Strategy model is outside model_selections.",
                        binding,
                    )
                )
                continue
            item = catalog_by_identity.get(identity)
            if item is None or not item.available:
                issues.append(
                    _model_issue(
                        "AGENT_MODEL_UNAVAILABLE",
                        "The Agent Strategy model is not active in Dify.",
                        binding,
                    )
                )
                continue
            required_features = set(binding["required_features"])
            if required_features and not _features_satisfied(
                required_features,
                set(item.features),
            ):
                issues.append(
                    _model_issue(
                        "AGENT_MODEL_FEATURE_NOT_SUPPORTED",
                        (
                            "A tool-bound Agent Strategy requires tool-call, "
                            "multi-tool-call, or stream-tool-call."
                        ),
                        binding,
                    )
                )
    return issues


def apply_default_runtime_models(
    payload: dict[str, Any],
    models: list[dict[str, Any]] | None,
) -> dict[str, Any]:
    data = deepcopy(payload)
    if not models:
        return data
    primary = models[0]
    provider = str(primary.get("provider") or "").strip()
    model = str(primary.get("model") or "").strip()
    if not provider or not model:
        return data
    for node in _raw_nodes(data):
        _apply_default_to_raw_node(node, provider=provider, model=model)
    return data


def validate_runtime_model_bindings(
    plan: WorkflowPlan,
    catalog: DifyModelListResult,
    *,
    allowed_models: Iterable[DifyModelListItem] | None = None,
    baseline_plan: WorkflowPlan | None = None,
    existing_as_warning: bool = False,
) -> list[ValidationIssue]:
    catalog_by_identity = {
        (item.provider, item.model): item for item in catalog.data
    }
    allowed = (
        {(item.provider, item.model) for item in allowed_models}
        if allowed_models is not None
        else None
    )
    baseline = {
        binding["path"]: (binding["provider"], binding["model"])
        for binding in collect_runtime_model_bindings(baseline_plan)
    } if baseline_plan is not None else {}

    issues: list[ValidationIssue] = []
    for binding in collect_runtime_model_bindings(plan):
        identity = (binding["provider"], binding["model"])
        unchanged = baseline.get(binding["path"]) == identity
        severity = "warning" if existing_as_warning or unchanged else "error"
        if not all(identity):
            issues.append(
                _model_issue(
                    "PLAN_MODEL_MISSING",
                    "Runtime model provider and model are required.",
                    binding,
                    severity=severity,
                )
            )
            continue
        if allowed is not None and identity not in allowed and not unchanged:
            issues.append(
                _model_issue(
                    "PLAN_MODEL_NOT_SELECTED",
                    "The plan uses a runtime model outside model_selections.",
                    binding,
                )
            )
            continue
        item = catalog_by_identity.get(identity)
        if item is None:
            issues.append(
                _model_issue(
                    "PLAN_MODEL_NOT_FOUND",
                    "The plan references a runtime model that is not installed in Dify.",
                    binding,
                    severity=severity,
                )
            )
            continue
        if item.deprecated:
            issues.append(
                _model_issue(
                    "PLAN_MODEL_DEPRECATED",
                    "The plan references a deprecated Dify runtime model.",
                    binding,
                    severity=severity,
                )
            )
            continue
        if not item.available:
            issues.append(
                _model_issue(
                    "PLAN_MODEL_UNAVAILABLE",
                    (
                        "The plan references a Dify runtime model that is not active "
                        f"(model={item.status or 'unknown'}, provider={item.provider_status or 'unknown'})."
                    ),
                    binding,
                    severity=severity,
                )
            )
            continue
        required_features = set(binding["required_features"])
        if required_features and not _features_satisfied(
            required_features,
            set(item.features),
        ):
            requirement = (
                "one of tool-call, multi-tool-call, or stream-tool-call"
                if required_features == TOOL_CALL_FEATURES
                else ", ".join(sorted(required_features))
            )
            issues.append(
                _model_issue(
                    "PLAN_MODEL_FEATURE_NOT_SUPPORTED",
                    f"The selected runtime model does not support required capability: {requirement}.",
                    binding,
                    severity=severity,
                )
            )
    return issues


def collect_runtime_model_bindings(
    plan: WorkflowPlan | None,
) -> list[dict[str, Any]]:
    if plan is None:
        return []
    bindings: list[dict[str, Any]] = []
    for node in plan.nodes:
        _collect_node_bindings(
            node.type,
            node.id,
            node.params,
            bindings,
        )
    return bindings


def _raw_nodes(payload: dict[str, Any]) -> Iterable[dict[str, Any]]:
    for node in payload.get("nodes") or []:
        if not isinstance(node, dict):
            continue
        yield node
        params = node.get("params") if isinstance(node.get("params"), dict) else {}
        for child in params.get("children") or params.get("_children") or []:
            if isinstance(child, dict):
                yield child


def _apply_default_to_raw_node(
    node: dict[str, Any],
    *,
    provider: str,
    model: str,
) -> None:
    node_type = str(node.get("type") or node.get("node_type") or "")
    params = node.get("params")
    if not isinstance(params, dict):
        params = {}
        node["params"] = params
    if node_type in DIRECT_MODEL_NODE_TYPES:
        nested = params.get("model") if isinstance(params.get("model"), dict) else {}
        if not (
            str(nested.get("provider") or params.get("model_provider") or "").strip()
            and str(
                nested.get("name")
                or nested.get("model")
                or params.get("model_name")
                or ""
            ).strip()
        ):
            params["model_provider"] = provider
            params["model_name"] = model
    if node_type == "agent":
        schemas = params.get("parameters") if isinstance(params.get("parameters"), list) else []
        values = params.get("agent_parameters")
        if not isinstance(values, dict):
            values = {}
            params["agent_parameters"] = values
        for schema in schemas:
            if not isinstance(schema, dict) or str(schema.get("type") or "") != "model-selector":
                continue
            name = str(schema.get("variable") or schema.get("name") or "").strip()
            raw = values.get(name)
            selected = raw.get("value") if isinstance(raw, dict) else raw
            if isinstance(selected, dict) and selected.get("provider") and (
                selected.get("model") or selected.get("name")
            ):
                continue
            values[name] = {
                "type": "constant",
                "value": {
                    "provider": provider,
                    "model": model,
                    "model_type": "llm",
                    "mode": "chat",
                    "completion_params": {},
                },
            }


def _collect_node_bindings(
    node_type: str,
    node_id: str,
    params: dict[str, Any],
    bindings: list[dict[str, Any]],
) -> None:
    if node_type in DIRECT_MODEL_NODE_TYPES:
        model_config = params.get("model") if isinstance(params.get("model"), dict) else {}
        required_features: set[str] = set()
        vision = params.get("vision") if isinstance(params.get("vision"), dict) else {}
        if bool(vision.get("enabled")):
            required_features.add("vision")
        bindings.append(
            {
                "path": node_id,
                "node_id": node_id,
                "provider": str(
                    model_config.get("provider")
                    or params.get("model_provider")
                    or ""
                ).strip(),
                "model": str(
                    model_config.get("name")
                    or model_config.get("model")
                    or params.get("model_name")
                    or ""
                ).strip(),
                "required_features": required_features,
            }
        )
    elif node_type == "agent":
        schemas = params.get("parameters") if isinstance(params.get("parameters"), list) else []
        values = params.get("agent_parameters") if isinstance(params.get("agent_parameters"), dict) else {}
        tool_bound = any(
            isinstance(schema, dict)
            and str(schema.get("type") or "") in {
                "tool-selector",
                "multi-tool-selector",
                "array[tools]",
            }
            and _agent_tool_value_enabled(
                values.get(str(schema.get("variable") or schema.get("name") or ""))
            )
            for schema in schemas
        )
        for schema in schemas:
            if not isinstance(schema, dict) or str(schema.get("type") or "") != "model-selector":
                continue
            name = str(schema.get("variable") or schema.get("name") or "").strip()
            raw_value = values.get(name)
            selected = raw_value.get("value") if isinstance(raw_value, dict) else raw_value
            selected = selected if isinstance(selected, dict) else {}
            bindings.append(
                {
                    "path": f"{node_id}.agent_parameters.{name}",
                    "node_id": node_id,
                    "provider": str(selected.get("provider") or "").strip(),
                    "model": str(
                        selected.get("model") or selected.get("name") or ""
                    ).strip(),
                    "required_features": set(TOOL_CALL_FEATURES) if tool_bound else set(),
                }
            )
    for child in params.get("children") or []:
        if not isinstance(child, dict):
            continue
        child_params = child.get("params") if isinstance(child.get("params"), dict) else {}
        _collect_node_bindings(
            str(child.get("type") or ""),
            f"{node_id}/{child.get('id') or 'child'}",
            child_params,
            bindings,
        )


def _agent_tool_value_enabled(value: Any) -> bool:
    raw = value.get("value") if isinstance(value, dict) else value
    if isinstance(raw, list):
        return any(
            isinstance(item, dict) and bool(item.get("enabled", True))
            for item in raw
        )
    return isinstance(raw, dict) and bool(raw.get("enabled", True))


def _features_satisfied(required: set[str], actual: set[str]) -> bool:
    if required == TOOL_CALL_FEATURES:
        return bool(required & actual)
    return required.issubset(actual)


def _model_issue(
    code: str,
    message: str,
    binding: dict[str, Any],
    *,
    severity: str = "error",
) -> ValidationIssue:
    identity = f"{binding['provider']}/{binding['model']}".strip("/")
    return ValidationIssue(
        code=code,
        message=f"{message} Model: {identity or '<missing>'}.",
        node_id=binding["node_id"],
        severity=severity,
        path=binding["path"],
        suggestion="Choose an active model returned by GET /api/dify/models.",
    )
