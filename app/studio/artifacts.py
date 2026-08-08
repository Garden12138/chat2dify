from __future__ import annotations

from copy import deepcopy
from hashlib import sha256
import json
import re
from typing import Any, Iterable

from app.agent.state import AgentRun
from app.models import WorkflowPlan
from app.studio.models import (
    ArtifactResourceRequirement,
    CandidateScenarioReport,
    ReleaseResourceMapping,
    WorkflowArtifact,
    WorkflowArtifactPayload,
    new_id,
    utc_now,
)


class ArtifactError(RuntimeError):
    code = "STUDIO_ARTIFACT_ERROR"


class ArtifactSecretFound(ArtifactError):
    code = "STUDIO_ARTIFACT_SECRET_FOUND"


class ArtifactMappingMismatch(ArtifactError):
    code = "STUDIO_ARTIFACT_MAPPING_MISMATCH"


class ArtifactCanonicalMismatch(ArtifactError):
    code = "STUDIO_ARTIFACT_CANONICAL_MISMATCH"


_SENSITIVE_KEYS = {
    "access_token",
    "api_key",
    "apikey",
    "authorization",
    "client_secret",
    "cookie",
    "credential",
    "credential_id",
    "credential_value",
    "password",
    "private_key",
    "refresh_token",
    "secret",
    "secret_key",
    "token",
}
_SECRET_PATTERNS = (
    re.compile(r"\bsk-[A-Za-z0-9_-]{16,}\b"),
    re.compile(r"\bBearer\s+[A-Za-z0-9._~+/=-]{12,}\b", re.IGNORECASE),
    re.compile(r"-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----"),
    re.compile(r"\b(?:api[_-]?key|client[_-]?secret|password)\s*[:=]\s*[^\s,;]{8,}", re.IGNORECASE),
)
_PLACEHOLDER = re.compile(
    r"^c2d-resource://(model|dataset|tool|strategy|trigger)/([a-f0-9]{16})(?:#([a-z]+))?$"
)


def build_workflow_artifact(
    *,
    project_id: str,
    candidate_id: str,
    workspace_version_id: str,
    source_base_hash: str | None,
    plan: WorkflowPlan,
    run: AgentRun,
    scenario_run_id: str,
    report: CandidateScenarioReport,
    created_by: str,
) -> WorkflowArtifact:
    if report.candidate_id != candidate_id:
        raise ArtifactError("Scenario report does not belong to the Candidate.")
    if report.binding.candidate_workspace_version_id != workspace_version_id:
        raise ArtifactError("Scenario evidence is bound to another Workspace version.")
    logical_plan, requirements = logicalize_plan(plan)
    availability = [
        ArtifactResourceRequirement(
            kind="credential_availability",
            logical_ref=f"credential_availability:{item.logical_ref.split(':', 1)[1]}",
            label=f"{item.label} 凭据可用性",
        )
        for item in requirements
        if item.kind in {"model", "tool", "strategy", "trigger"}
    ]
    requirements = sorted(
        [*requirements, *availability],
        key=lambda item: (item.kind, item.logical_ref),
    )
    payload = WorkflowArtifactPayload(
        app_mode=plan.app_mode,
        plan=logical_plan,
        compatibility=deepcopy(run.snapshot.compatibility if run.snapshot else {}),
        capability_requirements=sorted({node.type for node in plan.nodes}),
        resource_requirements=requirements,
        scenario_evidence={
            "scenario_run_id": scenario_run_id,
            "candidate_id": candidate_id,
            "binding": report.binding.model_dump(mode="json"),
            "pass_rate": report.pass_rate,
            "quality_score": report.quality_score,
            "latency_ms": report.latency_ms,
            "total_tokens": report.total_tokens,
            "estimated_cost_microusd": report.estimated_cost_microusd,
            "human_escalations": report.human_escalations,
            "side_effects": report.side_effects,
            "failure_clusters": report.failure_clusters,
            "cleanup_verified": report.cleanup_verified,
        },
        provenance={
            "candidate_id": candidate_id,
            "candidate_workspace_version_id": workspace_version_id,
            "candidate_hash": report.binding.candidate_hash,
            "source_base_hash": source_base_hash,
            "scenario_binding_hash": report.binding.binding_hash,
            "generator": "chat2dify-artifact-v1",
        },
    )
    payload_data = payload.model_dump(mode="json")
    assert_secret_free(payload_data)
    canonical = canonical_json(payload_data)
    content_hash = sha256(canonical.encode("utf-8")).hexdigest()
    return WorkflowArtifact(
        id=new_id(),
        project_id=project_id,
        candidate_id=candidate_id,
        candidate_workspace_version_id=workspace_version_id,
        source_base_hash=source_base_hash,
        content_hash=content_hash,
        canonical_json=canonical,
        payload=payload,
        created_by=created_by,
        created_at=utc_now(),
    )


def logicalize_plan(
    plan: WorkflowPlan,
) -> tuple[dict[str, Any], list[ArtifactResourceRequirement]]:
    payload = plan.model_dump(mode="json")
    requirements: dict[str, ArtifactResourceRequirement] = {}
    for node_index, node in enumerate(payload.get("nodes") or []):
        if not isinstance(node, dict):
            continue
        node_id = str(node.get("id") or f"node-{node_index}")
        node_type = str(node.get("type") or "")
        title = str(node.get("title") or node_id)
        node["params"] = _logicalize_value(
            node.get("params") or {},
            path=f"nodes.{node_index}.params",
            node_type=node_type,
            label=title,
            requirements=requirements,
        )
    assert_secret_free(payload)
    return payload, sorted(
        requirements.values(),
        key=lambda item: (item.kind, item.logical_ref),
    )


def materialize_artifact_plan(
    artifact: WorkflowArtifact,
    mappings: Iterable[ReleaseResourceMapping],
) -> WorkflowPlan:
    mapping_by_ref: dict[str, ReleaseResourceMapping] = {}
    for mapping in mappings:
        if mapping.logical_ref in mapping_by_ref:
            raise ArtifactMappingMismatch(
                f"Resource mapping is duplicated: {mapping.logical_ref}."
            )
        mapping_by_ref[mapping.logical_ref] = mapping
    required = {
        item.logical_ref: item
        for item in artifact.payload.resource_requirements
        if item.required
    }
    missing = sorted(
        logical_ref
        for logical_ref in required
        if logical_ref not in mapping_by_ref or not mapping_by_ref[logical_ref].available
    )
    if missing:
        raise ArtifactMappingMismatch(
            "Required release mappings are unavailable: " + ", ".join(missing)
        )
    for logical_ref, mapping in mapping_by_ref.items():
        requirement = next(
            (
                item
                for item in artifact.payload.resource_requirements
                if item.logical_ref == logical_ref
            ),
            None,
        )
        if requirement is None:
            raise ArtifactMappingMismatch(
                f"Release mapping is not required by the Artifact: {logical_ref}."
            )
        if mapping.kind != requirement.kind:
            raise ArtifactMappingMismatch(
                f"Release mapping kind does not match {logical_ref}."
            )
        if mapping.kind == "credential_availability" and mapping.target_ref != "available":
            raise ArtifactMappingMismatch(
                f"Credential availability is not confirmed for {logical_ref}."
            )
    materialized = _materialize_value(
        deepcopy(artifact.payload.plan),
        mapping_by_ref=mapping_by_ref,
    )
    assert_secret_free(materialized)
    return WorkflowPlan.model_validate(materialized)


def artifact_from_canonical_json(
    *,
    canonical: str,
    expected_hash: str,
) -> WorkflowArtifactPayload:
    try:
        parsed = json.loads(canonical)
    except json.JSONDecodeError as exc:
        raise ArtifactCanonicalMismatch("Git Artifact JSON is invalid.") from exc
    if not isinstance(parsed, dict):
        raise ArtifactCanonicalMismatch("Git Artifact must be a JSON object.")
    normalized = canonical_json(parsed)
    actual_hash = sha256(normalized.encode("utf-8")).hexdigest()
    if normalized != canonical or actual_hash != expected_hash:
        raise ArtifactCanonicalMismatch(
            "Git Artifact bytes or content Hash are not canonical."
        )
    assert_secret_free(parsed)
    return WorkflowArtifactPayload.model_validate(parsed)


def artifact_git_files(artifact: WorkflowArtifact) -> dict[str, str]:
    assert_secret_free(artifact.payload.model_dump(mode="json"))
    metadata = canonical_json(
        {
            "content_hash": artifact.content_hash,
            "schema_version": artifact.payload.schema_version,
        }
    )
    return {
        "artifact.json": artifact.canonical_json + "\n",
        "artifact.meta.json": metadata + "\n",
    }


def canonical_json(value: Any) -> str:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    )


def canonical_hash(value: Any) -> str:
    return sha256(canonical_json(value).encode("utf-8")).hexdigest()


def assert_secret_free(value: Any, *, path: str = "root") -> None:
    if isinstance(value, dict):
        for key, item in value.items():
            normalized = str(key).lower().replace("-", "_")
            if (
                normalized in _SENSITIVE_KEYS
                and item is not None
                and item != ""
                and item is not False
                and item != []
                and item != {}
            ):
                raise ArtifactSecretFound(
                    f"Secret-like field is forbidden in an Artifact: {path}.{key}."
                )
            assert_secret_free(item, path=f"{path}.{key}")
        return
    if isinstance(value, list):
        for index, item in enumerate(value):
            assert_secret_free(item, path=f"{path}.{index}")
        return
    if isinstance(value, str) and any(pattern.search(value) for pattern in _SECRET_PATTERNS):
        raise ArtifactSecretFound(
            f"Secret-like value is forbidden in an Artifact: {path}."
        )


def _logicalize_value(
    value: Any,
    *,
    path: str,
    node_type: str,
    label: str,
    requirements: dict[str, ArtifactResourceRequirement],
) -> Any:
    if isinstance(value, list):
        return [
            _logicalize_value(
                item,
                path=f"{path}.{index}",
                node_type=node_type,
                label=label,
                requirements=requirements,
            )
            for index, item in enumerate(value)
        ]
    if not isinstance(value, dict):
        return value

    result = deepcopy(value)
    if _plain_resource_string(result.get("model_provider")) and _plain_resource_string(
        result.get("model_name")
    ):
        _replace_composite(
            result,
            kind="model",
            parts={"model_provider": "provider", "model_name": "name"},
            path=path,
            label=f"{label} 模型",
            requirements=requirements,
        )
    model = result.get("model")
    if isinstance(model, dict):
        provider_key = "provider" if _plain_resource_string(model.get("provider")) else None
        name_key = (
            "name"
            if _plain_resource_string(model.get("name"))
            else "model"
            if _plain_resource_string(model.get("model"))
            else None
        )
        if provider_key and name_key:
            _replace_composite(
                model,
                kind="model",
                parts={provider_key: "provider", name_key: "name"},
                path=f"{path}.model",
                label=f"{label} 模型",
                requirements=requirements,
            )
    if _plain_resource_string(
        result.get("agent_strategy_provider_name")
    ) and _plain_resource_string(result.get("agent_strategy_name")):
        _replace_composite(
            result,
            kind="strategy",
            parts={
                "agent_strategy_provider_name": "provider",
                "agent_strategy_name": "name",
            },
            path=path,
            label=f"{label} Agent Strategy",
            requirements=requirements,
        )
    if _plain_resource_string(result.get("provider_id")) and _plain_resource_string(
        result.get("tool_name")
    ):
        kind = "trigger" if node_type.startswith("trigger-") else "tool"
        _replace_composite(
            result,
            kind=kind,
            parts={"provider_id": "provider", "tool_name": "name"},
            path=path,
            label=f"{label} {'Trigger' if kind == 'trigger' else 'Tool'}",
            requirements=requirements,
        )
    elif node_type == "trigger-plugin" and _plain_resource_string(
        result.get("provider_id")
    ):
        token = _resource_token("trigger", path, result["provider_id"])
        logical_ref = f"trigger:{token}"
        requirements[logical_ref] = ArtifactResourceRequirement(
            kind="trigger",
            logical_ref=logical_ref,
            label=f"{label} Trigger",
        )
        result["provider_id"] = f"c2d-resource://trigger/{token}#provider"

    for key in ("dataset_id", "knowledge_base_id"):
        if isinstance(result.get(key), str) and result[key]:
            result[key] = _replace_single_resource(
                kind="dataset",
                source=result[key],
                path=f"{path}.{key}",
                label=f"{label} Dataset",
                requirements=requirements,
            )
    for key in ("dataset_ids", "knowledge_base_ids"):
        items = result.get(key)
        if isinstance(items, list):
            result[key] = [
                _replace_single_resource(
                    kind="dataset",
                    source=item,
                    path=f"{path}.{key}.{index}",
                    label=f"{label} Dataset {index + 1}",
                    requirements=requirements,
                )
                if isinstance(item, str) and item
                else item
                for index, item in enumerate(items)
            ]

    for key, item in list(result.items()):
        result[key] = _logicalize_value(
            item,
            path=f"{path}.{key}",
            node_type=node_type,
            label=label,
            requirements=requirements,
        )
    return result


def _replace_composite(
    payload: dict[str, Any],
    *,
    kind: str,
    parts: dict[str, str],
    path: str,
    label: str,
    requirements: dict[str, ArtifactResourceRequirement],
) -> None:
    source = "::".join(str(payload[key]) for key in parts)
    token = _resource_token(kind, path, source)
    logical_ref = f"{kind}:{token}"
    requirements[logical_ref] = ArtifactResourceRequirement(
        kind=kind,  # type: ignore[arg-type]
        logical_ref=logical_ref,
        label=label,
    )
    for key, part in parts.items():
        payload[key] = f"c2d-resource://{kind}/{token}#{part}"


def _replace_single_resource(
    *,
    kind: str,
    source: str,
    path: str,
    label: str,
    requirements: dict[str, ArtifactResourceRequirement],
) -> str:
    token = _resource_token(kind, path, source)
    logical_ref = f"{kind}:{token}"
    requirements[logical_ref] = ArtifactResourceRequirement(
        kind=kind,  # type: ignore[arg-type]
        logical_ref=logical_ref,
        label=label,
    )
    return f"c2d-resource://{kind}/{token}"


def _resource_token(kind: str, path: str, source: str) -> str:
    return sha256(f"{kind}|{path}|{source}".encode("utf-8")).hexdigest()[:16]


def _plain_resource_string(value: Any) -> bool:
    return isinstance(value, str) and bool(value) and _PLACEHOLDER.match(value) is None


def _materialize_value(
    value: Any,
    *,
    mapping_by_ref: dict[str, ReleaseResourceMapping],
) -> Any:
    if isinstance(value, dict):
        return {
            key: _materialize_value(item, mapping_by_ref=mapping_by_ref)
            for key, item in value.items()
        }
    if isinstance(value, list):
        return [
            _materialize_value(item, mapping_by_ref=mapping_by_ref)
            for item in value
        ]
    if not isinstance(value, str):
        return value
    matched = _PLACEHOLDER.match(value)
    if matched is None:
        return value
    kind, token, part = matched.groups()
    logical_ref = f"{kind}:{token}"
    mapping = mapping_by_ref.get(logical_ref)
    if mapping is None or not mapping.available:
        raise ArtifactMappingMismatch(f"Missing mapping for {logical_ref}.")
    if part is None:
        return mapping.target_ref
    pieces = mapping.target_ref.split("::", 1)
    if len(pieces) != 2 or not all(piece.strip() for piece in pieces):
        raise ArtifactMappingMismatch(
            f"{logical_ref} requires an opaque provider::name target reference."
        )
    return pieces[0] if part == "provider" else pieces[1]
