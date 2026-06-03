from __future__ import annotations

from copy import deepcopy
from typing import Any, Mapping

from app.models import WorkflowPlan


def knowledge_dataset_ids(plan: WorkflowPlan, default_dataset_ids: list[str] | None = None) -> list[str]:
    ids: list[str] = []
    for node_type, params in _iter_plan_node_params(plan):
        if node_type != "knowledge-retrieval":
            continue
        node_dataset_ids = _string_list(params.get("dataset_ids")) or list(default_dataset_ids or [])
        for dataset_id in node_dataset_ids:
            if dataset_id not in ids:
                ids.append(dataset_id)
    return ids


def apply_dataset_retrieval_settings(
    plan: WorkflowPlan,
    datasets_by_id: Mapping[str, Any],
    *,
    default_dataset_ids: list[str] | None = None,
) -> WorkflowPlan:
    if not datasets_by_id:
        return plan

    enriched = plan.model_copy(deep=True)
    changed = False
    for node_type, params in _iter_plan_node_params(enriched):
        if node_type != "knowledge-retrieval":
            continue
        dataset_ids = _string_list(params.get("dataset_ids")) or list(default_dataset_ids or [])
        selected_datasets = [datasets_by_id[dataset_id] for dataset_id in dataset_ids if dataset_id in datasets_by_id]
        if not selected_datasets:
            continue

        current_config = params.get("multiple_retrieval_config")
        next_config = multiple_retrieval_config_from_datasets(current_config, selected_datasets)
        if next_config != current_config:
            params["multiple_retrieval_config"] = next_config
            changed = True

    return enriched if changed else plan


def _iter_plan_node_params(plan: WorkflowPlan):
    for node in plan.nodes:
        yield node.type, node.params
        children = node.params.get("children") if isinstance(node.params.get("children"), list) else []
        for child in children:
            if isinstance(child, dict):
                params = child.get("params") if isinstance(child.get("params"), dict) else {}
                yield str(child.get("type") or ""), params


def multiple_retrieval_config_from_datasets(value: Any, datasets: list[Any]) -> dict[str, Any]:
    result = _base_multiple_retrieval_config(value)

    threshold = _first_enabled_score_threshold(datasets)
    if result.get("score_threshold") is None and threshold is not None:
        result["score_threshold"] = threshold

    dataset_rerank = _first_enabled_reranking_model(datasets)
    if dataset_rerank:
        result["reranking_enable"] = True
        result["reranking_mode"] = dataset_rerank["mode"]
        result["reranking_model"] = dataset_rerank["model"]
        return result

    if any(_retrieval_model_dict(dataset) for dataset in datasets):
        result["reranking_enable"] = False
        result.setdefault("reranking_mode", "reranking_model")
        result.pop("reranking_model", None)

    return result


def _base_multiple_retrieval_config(value: Any) -> dict[str, Any]:
    config = value if isinstance(value, dict) else {}
    try:
        top_k = int(config.get("top_k", 4))
    except (TypeError, ValueError):
        top_k = 4
    result = {
        "top_k": max(1, top_k),
        "score_threshold": config.get("score_threshold"),
        "reranking_enable": _bool(config.get("reranking_enable", False)),
        "reranking_mode": str(config.get("reranking_mode") or "reranking_model"),
    }
    reranking_model = _reranking_model(config.get("reranking_model"))
    if result["reranking_enable"] and reranking_model:
        result["reranking_model"] = reranking_model
    if isinstance(config.get("weights"), dict):
        result["weights"] = deepcopy(config["weights"])
    return result


def _first_enabled_reranking_model(datasets: list[Any]) -> dict[str, Any] | None:
    for dataset in datasets:
        retrieval_model = _retrieval_model_dict(dataset)
        if not retrieval_model or not _bool(retrieval_model.get("reranking_enable")):
            continue
        reranking_model = _reranking_model(retrieval_model.get("reranking_model"))
        if not reranking_model:
            continue
        return {
            "mode": str(retrieval_model.get("reranking_mode") or "reranking_model"),
            "model": reranking_model,
        }
    return None


def _first_enabled_score_threshold(datasets: list[Any]) -> float | int | None:
    for dataset in datasets:
        retrieval_model = _retrieval_model_dict(dataset)
        if not retrieval_model or not _bool(retrieval_model.get("score_threshold_enabled")):
            continue
        value = retrieval_model.get("score_threshold")
        if isinstance(value, (int, float)):
            return value
        try:
            return float(value)
        except (TypeError, ValueError):
            continue
    return None


def _retrieval_model_dict(dataset: Any) -> dict[str, Any]:
    if isinstance(dataset, dict):
        retrieval_model = dataset.get("retrieval_model_dict") or dataset.get("retrieval_model")
        if isinstance(retrieval_model, dict):
            return retrieval_model
        if any(key in dataset for key in ("search_method", "reranking_enable", "reranking_model")):
            return dataset
        return {}

    retrieval_model = getattr(dataset, "retrieval_model_dict", None)
    if isinstance(retrieval_model, dict):
        return retrieval_model
    retrieval_model = getattr(dataset, "retrieval_model", None)
    if isinstance(retrieval_model, dict):
        return retrieval_model
    return {}


def _reranking_model(value: Any) -> dict[str, str] | None:
    if not isinstance(value, dict):
        return None
    provider = str(value.get("provider") or value.get("reranking_provider_name") or "").strip()
    model = str(value.get("model") or value.get("reranking_model_name") or "").strip()
    if not provider or not model:
        return None
    return {"provider": provider, "model": model}


def _string_list(value: Any) -> list[str]:
    if isinstance(value, str):
        items = value.split(",")
    elif isinstance(value, list):
        items = value
    else:
        items = []
    return [str(item).strip() for item in items if str(item).strip()]


def _bool(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        return value.strip().lower() in {"1", "true", "yes", "on"}
    return bool(value)
