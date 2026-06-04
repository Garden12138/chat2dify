from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any

import httpx

from app.agent.normalizer import normalize_plan_payload
from app.agent.planner import (
    PlannerError,
    _chat_completions_url,
    _extract_plan_payload,
    _issues_to_feedback,
    _planner_tool_schemas,
    _strip_json_fences,
    _validate_compiled_plan,
)
from app.config import Settings
from app.models import WorkflowPlan
from app.validator import has_errors


EDIT_SYSTEM_PROMPT = """You revise an existing Dify WorkflowPlan.
Return only JSON. Return the full revised WorkflowPlan, not a patch.
Supported node types are only:
start, llm, code, if-else, end, http-request, template-transform,
question-classifier, parameter-extractor, variable-aggregator,
document-extractor, assigner, list-operator, knowledge-retrieval, human-input,
iteration, loop, tool, agent, datasource, datasource-empty, knowledge-index,
trigger-webhook, trigger-plugin, trigger-schedule. iteration-start, loop-start,
and loop-end are internal
container children only; never place them in top-level nodes.
Prefer the smallest safe change that satisfies the request.
Preserve existing node ids when a node keeps the same purpose.
Preserve existing tool, agent, datasource, datasource-empty, knowledge-index,
trigger-webhook, trigger-plugin, and trigger-schedule nodes exactly unless the
user explicitly asks to remove them. Do not add agent, datasource, trigger, or
knowledge-index nodes; they require Dify-side configuration. Add a new tool
node only when selected_tools is non-empty and the user explicitly asks to call
or use one of those selected tools. Never invent provider IDs or tool names.
Use if-else for explicit string or numeric conditions.
Use question-classifier for semantic intent/category routing; each class needs an outgoing edge with source_handle equal to classes[].id.
Use parameter-extractor for structured field extraction; default reasoning_mode is "prompt" and parameter names should be variable-safe English names.
Use variable-aggregator for fallback/merge of multiple upstream variables.
Use document-extractor only for file/document/attachment text extraction.
Use list-operator only for filtering/sorting/limiting arrays.
Use knowledge-retrieval only for explicit knowledge base, document library, RAG, retrieval, or stored-material Q&A requests.
Use human-input only for explicit human review, manual approval, manager approval, human confirmation, or human-supplied follow-up information. delivery_methods[].id must be a valid UUID. Each action needs an outgoing edge with source_handle equal to user_actions[].id.
human-input outputs include form input names plus __action_id, __action_value, and __rendered_content.
Use iteration only for explicit batch/list traversal requirements. Keep internal children in the iteration node params.children and internal edges in params.edges; internal item references should use {{#<iteration_node_id>.item#}}.
Use loop only for explicit retry/repeat/until/max-N-times requirements. Keep loop-start/loop-end inside the loop node params.children and internal edges in params.edges.
Loop break_conditions must reference loop variables such as ["retry","status_text"], not internal child outputs such as ["retry_step","text"]. If an until condition depends on an internal child output, add or keep an internal assigner child that writes the child output into a loop variable, then have break_conditions read the loop variable.
Do not invent dataset_ids. Keep existing dataset_ids, or omit them so chat2dify can inject DIFY_DEFAULT_DATASET_IDS for newly added knowledge nodes.
Keep existing assigner nodes when present, but do not add assigner unless the request explicitly asks to update an existing variable, the target variable is unambiguous, or it is needed for the loop-variable update pattern above.
Every node must keep or receive a business-specific title. Do not use generic
titles like Start, LLM, End, Code, Node, 开始, 大模型, 结束.
For every llm node, split prompts clearly:
- system_prompt defines role, rules, output format, and review criteria.
- user_prompt contains the specific input, task, and Dify variable references.
Preserve existing start inputs unless the user explicitly asks to change them.
Preserve existing end outputs unless the user explicitly asks to change them.
Preserve all unrelated node params and edges exactly.
Do not delete, rename, or rebuild nodes unless the user explicitly asks for removal or restructuring.
When adding a node, connect it at the nearest relevant upstream/downstream position and keep the rest of the graph unchanged.
Use exactly one start node and at least one end node. Keep all nodes connected.
Use Dify variable syntax like {{#start_1.query#}} inside prompts/templates/URLs.
If an if-else node has cases, every case needs an outgoing edge whose source_handle equals case_id,
and the else branch must use source_handle "false".
"""


@dataclass(frozen=True)
class WorkflowEditResult:
    plan: WorkflowPlan
    raw_plan: dict[str, Any]
    attempts: int
    repaired: bool
    normalizations: list[str] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)

    def metadata(self) -> dict[str, Any]:
        return {
            "mode": "llm-edit",
            "attempts": self.attempts,
            "used_fallback": False,
            "repaired": self.repaired,
            "normalizations": self.normalizations,
            "errors": self.errors,
        }


class WorkflowEditPlanner:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings

    def generate(
        self,
        message: str,
        *,
        current_plan: WorkflowPlan,
        dsl_version: str,
        tool_selections: list[dict[str, Any]] | None = None,
    ) -> WorkflowEditResult:
        if not self.settings.openai_api_key:
            raise PlannerError("OPENAI_API_KEY is required to modify an existing workflow.")

        last_error = ""
        errors: list[str] = []
        final_raw_plan: dict[str, Any] | None = None
        for attempt in range(1, 4):
            content = self._call_llm(
                message,
                current_plan=current_plan,
                last_error=last_error,
                tool_selections=tool_selections or [],
            )
            try:
                payload = json.loads(_strip_json_fences(content))
                raw_plan = _extract_plan_payload(payload)
                final_raw_plan = raw_plan
                normalized = normalize_plan_payload(
                    raw_plan,
                    app_name=current_plan.name,
                    default_dataset_ids=self.settings.dify_default_dataset_ids,
                    tool_selections=tool_selections or [],
                )
                plan = WorkflowPlan.model_validate(normalized.payload)
                issues = _validate_compiled_plan(plan, settings=self.settings, dsl_version=dsl_version)
                if has_errors(issues):
                    raise ValueError(_issues_to_feedback(issues))
                return WorkflowEditResult(
                    plan=plan,
                    raw_plan=raw_plan,
                    attempts=attempt,
                    repaired=attempt > 1 or normalized.changed,
                    normalizations=normalized.changes,
                    errors=errors,
                )
            except Exception as exc:  # noqa: BLE001 - error text is intentionally fed back to the LLM.
                last_error = str(exc)
                errors.append(last_error)
        raw_hint = f" Last raw plan: {json.dumps(final_raw_plan, ensure_ascii=False)[:1000]}" if final_raw_plan else ""
        raise PlannerError(f"Could not generate a valid revised WorkflowPlan after 3 attempts: {last_error}.{raw_hint}")

    def _call_llm(
        self,
        message: str,
        *,
        current_plan: WorkflowPlan,
        last_error: str = "",
        tool_selections: list[dict[str, Any]] | None = None,
    ) -> str:
        url = _chat_completions_url(self.settings.openai_base_url)
        user_content = {
            "request": message,
            "current_plan": current_plan.model_dump(),
            "selected_tools": _planner_tool_schemas(tool_selections or []),
            "edit_policy": {
                "output": "Return the complete revised WorkflowPlan JSON.",
                "default": "Make a minimal targeted edit.",
                "must_preserve": [
                    "Existing node ids for unchanged purposes.",
                    "Business-specific node titles; never return generic Start/LLM/End titles.",
                    "LLM system_prompt for identity/rules/output format/review criteria.",
                    "LLM user_prompt for the current input/task and Dify variables.",
                    "Existing start inputs unless explicitly requested.",
                    "Existing end outputs unless explicitly requested.",
                    "Unrelated node params and edges.",
                    "Existing iteration/loop params.children and params.edges unless explicitly requested.",
                ],
                "avoid": [
                    "Rebuilding the whole graph.",
                    "Deleting nodes unrelated to the request.",
                    "Renaming nodes without a functional reason.",
                ],
            },
        }
        messages: list[dict[str, str]] = [
            {"role": "system", "content": EDIT_SYSTEM_PROMPT},
            {"role": "user", "content": json.dumps(user_content, ensure_ascii=False)},
        ]
        if last_error:
            messages.append({"role": "user", "content": f"Previous revised plan failed validation: {last_error}"})

        payload: dict[str, Any] = {
            "model": self.settings.openai_model,
            "messages": messages,
            "temperature": 0.2,
            "response_format": {"type": "json_object"},
        }
        headers = {"Authorization": f"Bearer {self.settings.openai_api_key}"}
        try:
            with httpx.Client(timeout=60) as client:
                response = client.post(url, json=payload, headers=headers)
                response.raise_for_status()
        except httpx.HTTPStatusError as exc:
            raise PlannerError(
                f"Workflow edit LLM request failed: {exc.response.status_code} {exc.response.text}"
            ) from exc
        except httpx.RequestError as exc:
            raise PlannerError(f"Workflow edit LLM request failed: {exc}") from exc
        data = response.json()
        return str(data["choices"][0]["message"]["content"])
