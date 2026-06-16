from __future__ import annotations

import json
from copy import deepcopy
from dataclasses import dataclass, field
from typing import Any

import yaml

from app.compiler.dify import DifyDslCompiler
from app.dify.graph import decompile_dify_graph
from app.models import PlanNode, ValidationIssue, WorkflowPlan
from app.validator import has_errors, validate_dsl, validate_plan


@dataclass(frozen=True)
class PreflightResult:
    ok: bool
    dsl: str
    dsl_version: str
    roundtrip_ok: bool
    issues: list[ValidationIssue] = field(default_factory=list)

    def metadata(self) -> dict[str, Any]:
        return {
            "ok": self.ok,
            "dsl_version": self.dsl_version,
            "roundtrip_ok": self.roundtrip_ok,
            "issues": [issue.model_dump() for issue in self.issues],
        }


def preflight_plan(
    plan: WorkflowPlan,
    *,
    compiler: DifyDslCompiler,
    expected_dsl_version: str,
) -> PreflightResult:
    issues = list(validate_plan(plan))
    try:
        dsl = compiler.compile(plan)
    except Exception as exc:  # noqa: BLE001 - compilation failures are diagnostics.
        issues.append(
            ValidationIssue(
                code="DIFY_PREFLIGHT_COMPILE_FAILED",
                message=str(exc),
                suggestion="Fix the Plan IR before compiling it to Dify DSL.",
            )
        )
        return PreflightResult(
            ok=False,
            dsl="",
            dsl_version="",
            roundtrip_ok=False,
            issues=issues,
        )

    issues.extend(validate_dsl(dsl, expected_dsl_version=expected_dsl_version))
    data: Any = None
    try:
        data = yaml.safe_load(dsl)
        workflow = data.get("workflow") if isinstance(data, dict) else None
        graph = workflow.get("graph") if isinstance(workflow, dict) else None
        if not isinstance(graph, dict):
            raise ValueError("Compiled DSL does not contain workflow.graph.")
        restored = decompile_dify_graph(
            graph,
            name=plan.name,
            app_mode=plan.app_mode,
            conversation_variables=deepcopy(
                workflow.get("conversation_variables") or []
            ),
        )
        issues.extend(validate_plan(restored))
        expected_signature = execution_signature(plan, compiler=compiler)
        restored_signature = execution_signature(restored, compiler=compiler)
        roundtrip_ok = expected_signature == restored_signature
        if not roundtrip_ok:
            issues.append(
                ValidationIssue(
                    code="DIFY_PREFLIGHT_ROUNDTRIP_MISMATCH",
                    message=(
                        "The compiled Dify DSL changes the workflow execution "
                        "signature when decompiled."
                    ),
                    path="workflow.graph",
                    suggestion=_signature_difference(
                        expected_signature,
                        restored_signature,
                    ),
                )
            )
    except Exception as exc:  # noqa: BLE001 - adapter failures are diagnostics.
        roundtrip_ok = False
        issues.append(
            ValidationIssue(
                code="DIFY_PREFLIGHT_DECOMPILE_FAILED",
                message=str(exc),
                path="workflow.graph",
                suggestion="Fix the compiled graph so it can be loaded back into Plan IR.",
            )
        )

    dsl_version = str(data.get("version") or "") if isinstance(data, dict) else ""
    return PreflightResult(
        ok=not has_errors(issues) and roundtrip_ok,
        dsl=dsl,
        dsl_version=dsl_version,
        roundtrip_ok=roundtrip_ok,
        issues=issues,
    )


def execution_signature(
    plan: WorkflowPlan,
    *,
    compiler: DifyDslCompiler,
) -> dict[str, Any]:
    return {
        "app_mode": plan.app_mode,
        "conversation_variables": sorted(
            (
                variable.id,
                variable.name,
                variable.value_type,
                _stable_value(variable.value),
                tuple(variable.selector),
            )
            for variable in plan.conversation_variables
        ),
        "nodes": sorted(
            (
                node.id,
                node.type,
                _stable_value(
                    _node_execution_signature(
                        node,
                        compiler=compiler,
                    )
                ),
            )
            for node in plan.nodes
        ),
        "edges": sorted(
            (
                edge.source,
                edge.target,
                edge.source_handle,
                edge.target_handle,
            )
            for edge in plan.edges
        ),
    }


def _node_execution_signature(
    node: PlanNode,
    *,
    compiler: DifyDslCompiler,
) -> dict[str, Any]:
    params = node.params
    result: dict[str, Any] = {}
    if node.type in {"llm", "question-classifier", "parameter-extractor"}:
        model = params.get("model") if isinstance(params.get("model"), dict) else {}
        result["model"] = {
            "provider": (
                model.get("provider")
                or params.get("model_provider")
                or compiler.default_model_provider
            ),
            "model": (
                model.get("name")
                or model.get("model")
                or params.get("model_name")
                or compiler.default_model_name
            ),
        }
    if node.type == "agent":
        result["models"] = _agent_model_bindings(params)
    resource = _resource_signature(
        node.type,
        params,
        default_dataset_ids=compiler.default_dataset_ids,
    )
    if resource:
        result["resource"] = resource
    if node.type in {"iteration", "loop"}:
        children = [
            PlanNode.model_validate(child)
            for child in params.get("children") or []
            if isinstance(child, dict)
        ]
        result["children"] = sorted(
            (
                child.id,
                child.type,
                _stable_value(
                    _node_execution_signature(child, compiler=compiler)
                ),
            )
            for child in children
        )
        result["edges"] = sorted(
            (
                str(edge.get("source") or ""),
                str(edge.get("target") or ""),
                str(edge.get("source_handle") or "source"),
                str(edge.get("target_handle") or "target"),
            )
            for edge in params.get("edges") or []
            if isinstance(edge, dict)
        )
        result["start_node_id"] = str(
            params.get("start_node_id") or f"{node.id}start"
        )
    if node.type == "iteration":
        result["output_selector"] = tuple(params.get("output_selector") or [])
        result["output_type"] = str(params.get("output_type") or "array[string]")
        result["iterator_selector"] = tuple(params.get("iterator_selector") or [])
    if node.type == "loop":
        result["loop_variables"] = _stable_value(params.get("loop_variables") or [])
        result["break_conditions"] = _stable_value(
            params.get("break_conditions") or []
        )
    return result


def _resource_signature(
    node_type: str,
    params: dict[str, Any],
    *,
    default_dataset_ids: list[str],
) -> dict[str, Any]:
    raw = params.get("_raw_data") if isinstance(params.get("_raw_data"), dict) else params
    if node_type == "knowledge-retrieval":
        dataset_ids = [
            str(item)
            for item in raw.get("dataset_ids") or default_dataset_ids
            if str(item)
        ]
        return {"dataset_ids": sorted(dataset_ids)}
    if node_type == "tool":
        result = {
            key: raw.get(key)
            for key in (
                "provider_id",
                "provider_type",
                "tool_name",
                "plugin_id",
                "plugin_unique_identifier",
            )
            if raw.get(key) is not None
        }
        result["tool_parameters"] = _stable_value(raw.get("tool_parameters") or {})
        result["tool_configurations"] = _stable_value(
            raw.get("tool_configurations") or {}
        )
        return result
    if node_type == "agent":
        result = {
            key: raw.get(key)
            for key in (
                "agent_strategy_provider_name",
                "agent_strategy_name",
                "plugin_unique_identifier",
            )
            if raw.get(key) is not None
        }
        result["tool_bindings"] = _agent_tool_bindings(
            raw.get("agent_parameters") or {}
        )
        return result
    if node_type in {
        "datasource",
        "datasource-empty",
        "knowledge-index",
        "trigger-webhook",
        "trigger-plugin",
        "trigger-schedule",
    }:
        identity_keys = (
            "provider_id",
            "provider_type",
            "plugin_id",
            "plugin_unique_identifier",
            "event_name",
            "subscription_id",
            "datasource_name",
            "indexing_technique",
            "mode",
            "timezone",
        )
        return {
            key: raw.get(key)
            for key in identity_keys
            if raw.get(key) is not None
        }
    return {}


def _agent_model_bindings(params: dict[str, Any]) -> list[dict[str, str]]:
    values = (
        params.get("agent_parameters")
        if isinstance(params.get("agent_parameters"), dict)
        else {}
    )
    result = []
    for name, raw in values.items():
        value = raw.get("value") if isinstance(raw, dict) else raw
        if not isinstance(value, dict):
            continue
        provider = str(value.get("provider") or "")
        model = str(value.get("model") or value.get("name") or "")
        if provider and model and (
            value.get("model_type") == "llm" or "model" in str(name).lower()
        ):
            result.append(
                {
                    "name": str(name),
                    "provider": provider,
                    "model": model,
                }
            )
    return sorted(result, key=lambda item: (item["name"], item["provider"], item["model"]))


def _agent_tool_bindings(value: Any) -> list[str]:
    bindings: set[str] = set()

    def visit(item: Any) -> None:
        if isinstance(item, dict):
            if item.get("tool_name") and (
                item.get("provider_id")
                or item.get("provider_name")
                or item.get("provider_type")
            ):
                bindings.add(_stable_value(item))
            for child in item.values():
                visit(child)
        elif isinstance(item, list):
            for child in item:
                visit(child)

    visit(value)
    return sorted(bindings)


def _stable_value(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _signature_difference(expected: dict[str, Any], actual: dict[str, Any]) -> str:
    for key in ("app_mode", "conversation_variables", "nodes", "edges"):
        if expected.get(key) != actual.get(key):
            return f"Round-trip mismatch in {key}."
    return "Round-trip execution signature mismatch."
