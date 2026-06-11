from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from app.agent.normalizer import normalize_plan_payload
from app.agent.planner import (
    PlannerError,
    _chat_completions_url,
    _extract_plan_payload,
    _planner_agent_schemas,
    _issues_to_feedback,
    _planner_tool_schemas,
    _post_chat_completion,
    _strip_json_fences,
    _validate_compiled_plan,
)
from app.config import Settings
from app.models import WorkflowPlan
from app.validator import has_errors

if TYPE_CHECKING:
    from app.tasks import TaskContext


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
user explicitly asks to remove them. Do not add datasource, trigger-plugin, or
knowledge-index nodes. Add trigger-webhook or trigger-schedule only when
selected_trigger explicitly contains that type; it replaces start and copies
the selected configuration exactly. When selected_trigger.type is user-input,
use a normal start entry instead. A trigger-schedule node has no time output;
when readable date/time is needed, format {{#sys.timestamp#}} in a code node
using the configured timezone, then give the LLM the code node's date/datetime
output. Never put the numeric timestamp directly in an LLM prompt or reference
{{#<schedule_node_id>.time#}}. Cron mode keeps only cron_expression/timezone;
visual mode keeps only frequency/visual_config/timezone. Add a new agent
node only when selected_agents is non-empty and the user explicitly asks for an
Agent, 智能体, autonomous planning, or multi-step execution. Never invent agent
strategy provider names or strategy names. Use selected_agents[].agent_parameters
for agent parameters, especially tool-selector or multi-tool-selector values.
Add a new tool node only when selected_tools is non-empty and the user
explicitly asks to call or use one of those selected tools. Never invent
provider IDs or tool names.
Use if-else for explicit string or numeric conditions.
Use question-classifier for semantic intent/category routing; each class needs an outgoing edge with source_handle equal to classes[].id.
Use parameter-extractor for structured field extraction; default reasoning_mode is "prompt" and parameter names should be variable-safe English names.
Use variable-aggregator for fallback/merge of multiple upstream variables.
Use document-extractor only for file/document/attachment text extraction.
Use list-operator only for filtering/sorting/limiting arrays.
Every Python code node must define def main(...)->dict and return every declared
output. Code params.outputs must use Dify's typed schema, for example
{"date":{"type":"string","children":null}}, never {"date":"string"}.
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
    provider: str = ""
    model: str = ""
    normalizations: list[str] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)

    def metadata(self) -> dict[str, Any]:
        return {
            "mode": "llm-edit",
            "attempts": self.attempts,
            "used_fallback": False,
            "repaired": self.repaired,
            "provider": self.provider,
            "model": self.model,
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
        agent_selections: list[dict[str, Any]] | None = None,
        trigger_selection: dict[str, Any] | None = None,
        task_context: TaskContext | None = None,
    ) -> WorkflowEditResult:
        runtime = self.settings.planner_runtime()
        if not runtime.api_key:
            key_name = "NVIDIA_API_KEY" if runtime.provider == "nvidia" else "OPENAI_API_KEY"
            raise PlannerError(f"{key_name} is required to modify an existing workflow.")

        last_error = ""
        errors: list[str] = []
        final_raw_plan: dict[str, Any] | None = None
        for attempt in range(1, 4):
            if task_context is not None:
                task_context.update(
                    "planning-revision",
                    35 + ((attempt - 1) * 10),
                    f"Generating workflow revision, semantic attempt {attempt}/3.",
                )
            call_kwargs = {
                "current_plan": current_plan,
                "last_error": last_error,
                "tool_selections": tool_selections or [],
                "agent_selections": agent_selections or [],
            }
            if trigger_selection is not None:
                call_kwargs["trigger_selection"] = trigger_selection
            if task_context is not None:
                call_kwargs["task_context"] = task_context
            content = self._call_llm(message, **call_kwargs)
            try:
                if task_context is not None:
                    task_context.update(
                        "validating-revision",
                        58 + ((attempt - 1) * 7),
                        f"Normalizing and validating revision attempt {attempt}/3.",
                    )
                payload = json.loads(_strip_json_fences(content))
                raw_plan = _extract_plan_payload(payload)
                final_raw_plan = raw_plan
                normalized = normalize_plan_payload(
                    raw_plan,
                    app_name=current_plan.name,
                    default_dataset_ids=self.settings.dify_default_dataset_ids,
                    tool_selections=tool_selections or [],
                    agent_selections=agent_selections or [],
                    trigger_selection=trigger_selection,
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
                    provider=runtime.provider,
                    model=runtime.model,
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
        agent_selections: list[dict[str, Any]] | None = None,
        trigger_selection: dict[str, Any] | None = None,
        task_context: TaskContext | None = None,
    ) -> str:
        runtime = self.settings.planner_runtime()
        url = _chat_completions_url(runtime.base_url)
        user_content = {
            "request": message,
            "current_plan": current_plan.model_dump(),
            "selected_tools": _planner_tool_schemas(tool_selections or []),
            "selected_agents": _planner_agent_schemas(agent_selections or []),
            "selected_trigger": trigger_selection or {"type": "user-input"},
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
        if last_error:
            user_content["previous_validation_error"] = last_error
        messages: list[dict[str, str]] = [
            {"role": "system", "content": EDIT_SYSTEM_PROMPT},
            {"role": "user", "content": json.dumps(user_content, ensure_ascii=False)},
        ]

        payload: dict[str, Any] = {
            "model": runtime.model,
            "messages": messages,
            "temperature": 0.2,
            "response_format": {"type": "json_object"},
        }
        if runtime.provider == "nvidia":
            chat_template_kwargs: dict[str, Any] = {
                "thinking": self.settings.nvidia_thinking,
            }
            if self.settings.nvidia_thinking:
                chat_template_kwargs["reasoning_effort"] = self.settings.nvidia_reasoning_effort
            payload.update(
                {
                    "top_p": 0.95,
                    "max_tokens": self.settings.nvidia_max_tokens,
                    "chat_template_kwargs": chat_template_kwargs,
                    "stream": True,
                }
            )
        return _post_chat_completion(
            runtime=runtime,
            url=url,
            payload=payload,
            error_prefix="Workflow edit LLM",
            task_context=task_context,
        )
