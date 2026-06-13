from __future__ import annotations

import json
import re
import time
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

import httpx

from app.agent.normalizer import normalize_plan_payload
from app.compiler.dify import DifyDslCompiler
from app.config import PlannerRuntime, Settings
from app.models import ValidationIssue, WorkflowPlan
from app.validator import has_errors, validate_dsl, validate_plan

if TYPE_CHECKING:
    from app.tasks import TaskContext


SYSTEM_PROMPT = """You turn a user's workflow request into a compact JSON WorkflowPlan.
Return only JSON. Supported node types are:
start, llm, code, if-else, end, http-request, template-transform,
question-classifier, parameter-extractor, variable-aggregator,
document-extractor, list-operator, knowledge-retrieval, human-input,
iteration, loop. iteration-start, loop-start, and loop-end are internal
container children only; never place them in top-level nodes.
Do not generate datasource, datasource-empty, or knowledge-index nodes in new
workflows. Generate trigger-webhook, trigger-plugin, or trigger-schedule only
when selected_trigger explicitly contains that trigger type. When
selected_trigger.type is user-input, keep a normal start entry. A trigger entry
replaces the start node; start and trigger nodes must never coexist.
For trigger-webhook, copy the selected method, content_type, headers, params,
body, status_code, response_body, timeout, and variables. Downstream references
may only use declared webhook variables.
For trigger-schedule, copy the selected mode, frequency, cron_expression,
visual_config, and timezone. Schedule workflows have no start.query input and
the trigger-schedule node does not expose a time output. When the workflow needs
the current date or readable local time, add a Python code node immediately
after the trigger. Bind its timestamp input to ["sys","timestamp"], convert it
with zoneinfo.ZoneInfo(selected_trigger.timezone), and output date
(YYYY-MM-DD), datetime (YYYY-MM-DD HH:MM:SS), and weekday strings. The following
LLM must reference that code output, for example {{#format_time.date#}}, instead
of the numeric system timestamp. Never put {{#sys.timestamp#}} directly in an
LLM prompt and never use {{#<schedule_node_id>.time#}} or similar schedule-node
references. In cron mode include only mode, cron_expression, and timezone; do
not include frequency or visual_config. In visual mode include frequency,
visual_config, and timezone; do not include cron_expression.
For trigger-plugin, copy provider_id, provider_type, provider_name, plugin_id,
plugin_unique_identifier, event_name, event_label, subscription_id,
event_parameters, parameters_schema, and output_schema exactly from
selected_trigger. Never invent or change the provider, event, subscription, or
plugin identifiers. Event parameters must use
{"type":"constant","value":...}; Plugin Trigger has no upstream variables.
Downstream nodes may reference only properties declared in
selected_trigger.output_schema, for example {{#trigger_1.issue#}}.
Generate agent nodes only when the user explicitly asks for an Agent, 智能体,
autonomous planning, multi-step execution, or self-directed reasoning, and
selected_agents is non-empty in the user message. Never invent agent strategy
provider names or strategy names. When generating an agent node, copy
agent_strategy_provider_name, agent_strategy_name, agent_strategy_label,
parameters, output_schema, plugin_unique_identifier, and meta from one selected
agent. Use agent_parameters with Dify ToolInput values, for example
{"query":{"type":"constant","value":"{{#start.query#}}"}}. If an agent parameter
has type tool-selector or multi-tool-selector, use only the tool value already
provided in selected_agents[].agent_parameters; do not invent nested tools.
Generate tool nodes only when the user explicitly asks to call/use a selected
tool and selected_tools is non-empty in the user message. Never invent tool
provider IDs or tool names. When generating a tool node, copy provider_id,
provider_type, provider_name, tool_name, tool_label, paramSchemas, output_schema,
plugin_id, and plugin_unique_identifier from one selected tool. Use
tool_parameters for form == "llm" parameters, for example
{"query":{"type":"variable","value":["start","query"]}}. Use
tool_configurations only for non-llm settings that have explicit or default
values.
Use exactly one start node and at least one end node. Keep nodes connected.
For simple requests, use start -> llm -> end.
Use if-else for explicit string or numeric conditions.
Use question-classifier for semantic intent/category routing. Its params must include:
{"query_variable_selector":["start","query"],"classes":[{"id":"complaint","name":"投诉","label":"CLASS 1"}],"instruction":"..."}.
Each outgoing edge from question-classifier must set source_handle to the matching classes[].id.
Use parameter-extractor to extract structured fields from natural language. Its params must include:
{"query":["start","query"],"reasoning_mode":"prompt","parameters":[{"name":"car_model","type":"string","description":"车辆型号","required":false}],"instruction":"..."}.
Prefer English variable-safe parameter names such as order_id, car_model, store, issue.
Use variable-aggregator when multiple upstream variables can supply the same value. Its params must include:
{"variables":[["extract","issue"],["start","query"]],"output_type":"string","advanced_settings":{"group_enabled":false,"groups":[]}}.
Use document-extractor only when the request explicitly involves uploaded files/documents/attachments. Its params must include:
{"variable_selector":["start","files"],"is_array_file":false}. Add a start file or file-list variable named files.
Use list-operator only when the request explicitly involves filtering/sorting/limiting a string, number, or file array. For arrays of objects, use a code node instead because Dify's list-operator runtime does not handle array[object]. Dify workflow start inputs cannot accept a top-level JSON array; for user-provided arrays, create a start json variable such as items and treat it as an object wrapper whose records field is the array. List-operator params must include:
{"variable":["start","items","records"],"var_type":"array[string]","item_var_type":"string","filter_by":{"enabled":false,"conditions":[]},"extract_by":{"enabled":false,"serial":"1"},"order_by":{"enabled":false,"key":"","value":"asc"},"limit":{"enabled":false,"size":10}}.
When using code for object-array filtering, output result as array[object], first_record as object, and last_record as object.
Every Python code node must define def main(...)->dict and return every declared
output. Code params.outputs must use Dify's typed schema, for example
{"date":{"type":"string","children":null}}, never {"date":"string"}.
Use knowledge-retrieval only when the request explicitly asks for knowledge base, document library, RAG, retrieval, or answering from stored materials. Its params must include:
{"query_variable_selector":["start","query"],"retrieval_mode":"multiple","multiple_retrieval_config":{"top_k":4,"score_threshold":null,"reranking_enable":false},"metadata_filtering_mode":"disabled"}.
Do not invent dataset_ids. If dataset_ids are not known, omit them and let chat2dify inject DIFY_DEFAULT_DATASET_IDS.
After knowledge-retrieval, pass {{#knowledge.result#}} or the actual knowledge node id result into an llm node, then end.
Use human-input only when the request explicitly asks for human review, manual approval, manager approval, human confirmation, or human-supplied follow-up information. Its params must include:
{"delivery_methods":[{"id":"00000000-0000-4000-8000-000000000001","type":"webapp","enabled":true,"config":{}}],"form_content":"请审核以下内容。","inputs":[],"user_actions":[{"id":"approve","title":"通过","button_style":"primary"},{"id":"reject","title":"驳回","button_style":"default"}],"timeout":3,"timeout_unit":"day"}.
human-input delivery_methods[].id must be a valid UUID.
Each outgoing edge from human-input must set source_handle to the matching user_actions[].id.
human-input outputs include form input names plus __action_id, __action_value, and __rendered_content.
Use iteration only when the request explicitly asks to batch process, traverse a list, handle each record, or generate one result per item. The top-level node is type "iteration"; put internal nodes in params.children and internal edges in params.edges. Do not put iteration-start in top-level nodes. Minimal params:
{"iterator_selector":["start","items"],"iterator_input_type":"array[string]","output_selector":["iter_item","text"],"output_type":"array[string]","is_parallel":false,"parallel_nums":10,"error_handle_mode":"terminated","flatten_output":true,"children":[{"id":"iter_start","type":"iteration-start","title":"开始遍历","params":{}},{"id":"iter_item","type":"llm","title":"逐条生成处理建议","params":{"system_prompt":"...","user_prompt":"请处理当前记录：{{#iter.item#}}"}}],"edges":[{"source":"iter_start","target":"iter_item"}]}.
When an internal iteration child handles the current item, reference the parent iteration node item as {{#<iteration_node_id>.item#}} and index as {{#<iteration_node_id>.index#}}.
Use loop only when the request explicitly asks to retry, repeat until a condition is met, check repeatedly, or run at most N times. The top-level node is type "loop"; put loop-start and internal processing nodes in params.children. Do not put loop-start or loop-end in top-level nodes. Minimal params:
{"loop_count":3,"logical_operator":"and","break_conditions":[],"loop_variables":[],"error_handle_mode":"terminated","children":[{"id":"retry_start","type":"loop-start","title":"开始循环","params":{}},{"id":"retry_step","type":"llm","title":"执行重试检查","params":{"system_prompt":"...","user_prompt":"请检查当前状态：{{#start.query#}}"}}],"edges":[{"source":"retry_start","target":"retry_step"}]}.
Loop break_conditions must reference loop variables such as ["retry","status_text"], not internal child outputs such as ["retry_step","text"]. If an until condition depends on an internal child output, add a loop_variables item and an internal assigner child that writes the child output into that loop variable, then have break_conditions read the loop variable.
Do not generate assigner in new workflows except for this loop-variable update pattern. Otherwise assigner is reserved for editing existing Dify drafts with explicit variable assignment context.
Every node must have a business-specific title. Do not use generic titles like
Start, LLM, End, Code, Node, 开始, 大模型, 结束. Good Chinese examples:
接收售后诉求, 判断售后类型, 生成理发售后回复, 返回处理结果.
Use start params.variables, not params.inputs.
Use llm params.system_prompt and params.user_prompt, not params.prompt.
For every llm node:
- system_prompt defines role, rules, output format, and review criteria.
- user_prompt contains the specific input, task, and Dify variable references.
Keep Dify variable references in user_prompt whenever possible.
Variable references inside text must use Dify syntax like {{#start_1.query#}}.
If an if-else node has cases, each outgoing edge from it must set source_handle
to the case_id, and the else branch must use source_handle "false".
If-else cases must look like:
{"case_id":"refund","logical_operator":"and","conditions":[{"variable_selector":["start_1","query"],"comparison_operator":"contains","value":"退款","varType":"string"}]}.
End node params.outputs items must include variable and value_selector, for example:
{"variable":"answer","value_selector":["llm_1","text"]}.
"""

CHATFLOW_SYSTEM_PROMPT = """You turn a user's Chatflow request into a compact
JSON WorkflowPlan. Return only JSON and set app_mode to "advanced-chat".

Supported top-level node types for new Chatflows are:
start, llm, code, if-else, answer, http-request, template-transform,
question-classifier, parameter-extractor, variable-aggregator,
document-extractor, list-operator, knowledge-retrieval, human-input,
iteration, loop, tool, agent.
Do not generate end, top-level assigner, datasource, datasource-empty,
knowledge-index, or any trigger node.

Use exactly one start node. Every node must be reachable from start, every
possible response path must finish at an answer node, and answer nodes must not
have outgoing edges. For a simple request use start -> llm -> answer.

The current user message is {{#sys.query#}} and uploaded files are
{{#sys.files#}}. In prompt, template, URL, body, tool, and agent text values,
never use {{#start.query#}}, {{#start.files#}},
{{#<start_id>.sys.query#}}, or {{#<start_id>.sys.files#}}. In selector arrays,
use ["<start_id>","sys.query"] and ["<start_id>","sys.files"]. Custom start
inputs remain selectable as ["<start_id>","<input_name>"].

Every llm node must include a business-specific system_prompt and user_prompt.
The user_prompt must include {{#sys.query#}} and any required upstream output.
Chatflow memory is enabled by chat2dify with a 10-message window.

Use human-input only when the user explicitly asks for human review, manual
approval, manager approval, human confirmation, or human-supplied follow-up
information. human-input is top-level only. Include one enabled webapp delivery
method, valid UUID delivery IDs, form_content, optional inputs, at least one
user action, and a positive hour/day timeout. Every user action must have one
outgoing edge whose source_handle equals the action id, and every action branch
must eventually finish at an answer node. Human actions are completed in Dify.

Use iteration only when the request explicitly asks to process every item in a
list or generate one result per item. The top-level iteration params must
include iterator_selector, iterator_input_type, output_selector, output_type,
is_parallel, parallel_nums, error_handle_mode, flatten_output, children, and
edges. children must form one acyclic processing chain beginning with exactly
one iteration-start. Do not put answer, human-input, if-else,
question-classifier, iteration, or loop inside the container. Internal LLM
prompts must include both {{#sys.query#}} and the current item reference
{{#<iteration_node_id>.item#}}.

Use loop only when the request explicitly asks to retry, repeat until a
condition is met, check repeatedly, or run at most N times. The top-level loop
params must include loop_count, logical_operator, break_conditions,
loop_variables, error_handle_mode, children, and edges. children must form one
acyclic processing chain beginning with exactly one loop-start. Do not put
answer, human-input, if-else, question-classifier, iteration, or another loop
inside the container. Break conditions must read loop variables. When a break
condition depends on an internal child output, include or allow chat2dify to
insert an internal assigner that copies the output to a loop variable.

Use if-else for explicit string or numeric conditions. Every case requires one
outgoing edge whose source_handle equals case_id, and the else edge must use
source_handle "false".
Use question-classifier for semantic routing. Its params include
query_variable_selector, classes, and instruction. Every class requires one
outgoing edge whose source_handle equals classes[].id.
Use parameter-extractor for structured fields. Its params include query,
reasoning_mode "prompt", variable-safe English parameter names, descriptions,
and required flags.

Use document-extractor only for uploaded files or attachments, with
variable_selector ["<start_id>","sys.files"]. Use list-operator only for
filtering, sorting, extracting, or limiting supported arrays; use code for
arrays of objects. Every Python code node must define def main(...)->dict,
return every declared output, and use typed output schemas such as
{"result":{"type":"string","children":null}}.
Use template-transform for deterministic formatting and variable-aggregator
when several upstream values can supply one output. HTTP nodes must include a
real URL and explicit method; references inside URL, headers, params, and body
must use Dify variable syntax.

Use knowledge-retrieval only for explicit knowledge-base, RAG, document-library,
or stored-material requests. Never invent dataset_ids. Omit them so chat2dify
can inject the dataset_ids selected in the request. Feed the retrieval result
to an llm and then an answer node.

Generate tool only when selected_tools is non-empty and the user explicitly
asks to call one. Copy provider_id, provider_type, provider_name, tool_name,
tool_label, parameters, output_schema, plugin_id, and
plugin_unique_identifier from the selected tool. Never invent resource
identity or parameter names.
Generate agent only when selected_agents is non-empty and the user explicitly
asks for an Agent, 智能体, autonomous planning, or multi-step execution. Copy
the selected strategy identity, parameter schema, configured agent_parameters,
output_schema, plugin identifier, and meta. Never invent nested tools or
strategy identifiers.

An answer node uses params such as
{"answer":"{{#<upstream_node_id>.<output_name>#}}"}. A branch may have its own
answer node or converge before one shared answer. Never create end.outputs.
Every node must have a business-specific title; avoid generic titles such as
Start, LLM, Answer, Code, Node, 开始, 大模型, 回复.
"""


class PlannerError(RuntimeError):
    """Raised when the planner cannot produce a valid plan."""


@dataclass(frozen=True)
class PlannerResult:
    plan: WorkflowPlan
    raw_plan: dict[str, Any]
    mode: str
    attempts: int
    used_fallback: bool
    repaired: bool
    provider: str = ""
    model: str = ""
    normalizations: list[str] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)

    def metadata(self) -> dict[str, Any]:
        return {
            "mode": self.mode,
            "attempts": self.attempts,
            "used_fallback": self.used_fallback,
            "repaired": self.repaired,
            "provider": self.provider,
            "model": self.model,
            "normalizations": self.normalizations,
            "errors": self.errors,
        }


class WorkflowPlanner:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings

    def generate_plan(self, message: str, *, app_name: str | None = None) -> WorkflowPlan:
        return self.generate(message, app_name=app_name, dsl_version="0.0.0").plan

    def generate(
        self,
        message: str,
        *,
        app_name: str | None = None,
        app_mode: str = "workflow",
        dsl_version: str,
        tool_selections: list[dict[str, Any]] | None = None,
        agent_selections: list[dict[str, Any]] | None = None,
        trigger_selection: dict[str, Any] | None = None,
        task_context: TaskContext | None = None,
    ) -> PlannerResult:
        runtime = self.settings.planner_runtime()
        if not runtime.api_key:
            if task_context is not None:
                task_context.update("planning", 35, "Using the fallback workflow template.")
            fallback = fallback_plan(message, app_name=app_name, app_mode=app_mode)
            fallback_payload = fallback.model_dump()
            if app_mode == "workflow":
                fallback_payload = _prepare_fallback_for_trigger(
                    fallback_payload,
                    message=message,
                    trigger_selection=trigger_selection,
                )
            normalized = normalize_plan_payload(
                fallback_payload,
                app_name=app_name,
                app_mode=app_mode,
                default_dataset_ids=self.settings.dify_default_dataset_ids,
                tool_selections=tool_selections or [],
                agent_selections=agent_selections or [],
                trigger_selection=trigger_selection,
            )
            plan = WorkflowPlan.model_validate(normalized.payload)
            return PlannerResult(
                plan=plan,
                raw_plan=fallback_payload,
                mode="fallback",
                attempts=0,
                used_fallback=True,
                repaired=False,
                provider=runtime.provider,
                model=runtime.model,
                normalizations=normalized.changes,
            )

        last_error = ""
        errors: list[str] = []
        final_raw_plan: dict[str, Any] | None = None
        for attempt in range(1, 4):
            if task_context is not None:
                task_context.update(
                    "planning",
                    10 + ((attempt - 1) * 12),
                    f"Generating workflow plan, semantic attempt {attempt}/3.",
                )
            call_kwargs = {
                "app_name": app_name,
                "last_error": last_error if attempt else "",
                "tool_selections": tool_selections or [],
                "agent_selections": agent_selections or [],
            }
            if app_mode == "advanced-chat":
                call_kwargs["app_mode"] = app_mode
            if trigger_selection is not None:
                call_kwargs["trigger_selection"] = trigger_selection
            if task_context is not None:
                call_kwargs["task_context"] = task_context
            content = self._call_llm(message, **call_kwargs)
            try:
                if task_context is not None:
                    task_context.update(
                        "validating-plan",
                        48 + ((attempt - 1) * 8),
                        f"Normalizing and validating semantic attempt {attempt}/3.",
                    )
                payload = json.loads(_strip_json_fences(content))
                raw_plan = _extract_plan_payload(payload)
                final_raw_plan = raw_plan
                normalized = normalize_plan_payload(
                    raw_plan,
                    app_name=app_name,
                    app_mode=app_mode,
                    default_dataset_ids=self.settings.dify_default_dataset_ids,
                    tool_selections=tool_selections or [],
                    agent_selections=agent_selections or [],
                    trigger_selection=trigger_selection,
                )
                plan = WorkflowPlan.model_validate(normalized.payload)
                issues = [
                    *_validate_compiled_plan(plan, settings=self.settings, dsl_version=dsl_version),
                    *_validate_creation_resource_bindings(
                        plan,
                        dataset_ids=self.settings.dify_default_dataset_ids,
                        tool_selections=tool_selections or [],
                        agent_selections=agent_selections or [],
                    ),
                ]
                if has_errors(issues):
                    raise ValueError(_issues_to_feedback(issues))
                return PlannerResult(
                    plan=plan,
                    raw_plan=raw_plan,
                    mode="llm",
                    attempts=attempt,
                    used_fallback=False,
                    repaired=attempt > 1 or normalized.changed,
                    provider=runtime.provider,
                    model=runtime.model,
                    normalizations=normalized.changes,
                    errors=errors,
                )
            except Exception as exc:  # noqa: BLE001 - error text is fed back to the LLM once.
                last_error = str(exc)
                errors.append(last_error)
        raw_hint = f" Last raw plan: {json.dumps(final_raw_plan, ensure_ascii=False)[:1000]}" if final_raw_plan else ""
        raise PlannerError(f"Could not generate a valid WorkflowPlan after 3 attempts: {last_error}.{raw_hint}")

    def _call_llm(
        self,
        message: str,
        *,
        app_name: str | None,
        last_error: str = "",
        tool_selections: list[dict[str, Any]] | None = None,
        agent_selections: list[dict[str, Any]] | None = None,
        trigger_selection: dict[str, Any] | None = None,
        task_context: TaskContext | None = None,
        app_mode: str = "workflow",
    ) -> str:
        runtime = self.settings.planner_runtime()
        url = _chat_completions_url(runtime.base_url)
        user_content = {
            "app_name": app_name or "Generated Workflow",
            "request": message,
            "app_mode": app_mode,
            "selected_tools": _planner_tool_schemas(tool_selections or []),
            "selected_agents": _planner_agent_schemas(agent_selections or []),
            "selected_trigger": trigger_selection or {"type": "user-input"},
        }
        if last_error:
            user_content["previous_validation_error"] = last_error
        messages: list[dict[str, str]] = [
            {
                "role": "system",
                "content": CHATFLOW_SYSTEM_PROMPT if app_mode == "advanced-chat" else SYSTEM_PROMPT,
            },
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
            error_prefix="Planner LLM",
            task_context=task_context,
        )


def _post_chat_completion(
    *,
    runtime: PlannerRuntime,
    url: str,
    payload: dict[str, Any],
    error_prefix: str,
    task_context: TaskContext | None = None,
) -> str:
    headers = {
        "Authorization": f"Bearer {runtime.api_key}",
        "Accept": "application/json",
        "Connection": "close",
    }
    timeout = httpx.Timeout(
        connect=min(runtime.timeout_seconds, 15.0),
        read=runtime.timeout_seconds,
        write=min(runtime.timeout_seconds, 30.0),
        pool=min(runtime.timeout_seconds, 15.0),
    )
    total_attempts = runtime.request_retries + 1
    last_error: Exception | None = None
    for request_attempt in range(1, total_attempts + 1):
        if task_context is not None:
            task_context.raise_if_cancelled()
            task_context.update(
                "planner-request",
                None,
                f"Calling {runtime.label}, network attempt {request_attempt}/{total_attempts}.",
            )
        try:
            with httpx.Client(timeout=timeout) as client:
                if payload.get("stream"):
                    with client.stream("POST", url, json=payload, headers=headers) as response:
                        response.raise_for_status()
                        return _read_streamed_chat_completion(response, task_context=task_context)
                response = client.post(url, json=payload, headers=headers)
                response.raise_for_status()
                if task_context is not None:
                    task_context.raise_if_cancelled()
                data = response.json()
                return str(data["choices"][0]["message"]["content"])
        except httpx.HTTPStatusError as exc:
            if exc.response.status_code not in {429, 502, 503, 504}:
                raise PlannerError(
                    f"{error_prefix} request failed: {exc.response.status_code} {exc.response.text}"
                ) from exc
            last_error = exc
        except httpx.RequestError as exc:
            last_error = exc
        except (ValueError, KeyError, IndexError, TypeError) as exc:
            raise PlannerError(f"{error_prefix} returned an invalid chat completion response.") from exc

        if request_attempt < total_attempts:
            delay = min(2 ** (request_attempt - 1), 4)
            if task_context is not None:
                task_context.raise_if_cancelled()
            time.sleep(delay)

    if isinstance(last_error, httpx.ReadTimeout):
        message = f"timed out after {runtime.timeout_seconds:g} seconds while waiting for a response"
    elif isinstance(last_error, httpx.HTTPStatusError):
        message = f"{last_error.response.status_code} {last_error.response.text}"
    else:
        message = str(last_error)
    raise PlannerError(
        f"{error_prefix} request failed after {total_attempts} network attempts: {message}"
    ) from last_error


def _read_streamed_chat_completion(
    response: httpx.Response,
    *,
    task_context: TaskContext | None = None,
) -> str:
    content_parts: list[str] = []
    for raw_line in response.iter_lines():
        if task_context is not None:
            task_context.raise_if_cancelled()
        line = raw_line.strip()
        if not line or line.startswith(":") or not line.startswith("data:"):
            continue
        data = line[5:].strip()
        if data == "[DONE]":
            break
        chunk = json.loads(data)
        choices = chunk.get("choices") or []
        if not choices:
            continue
        delta = choices[0].get("delta") or {}
        content = delta.get("content")
        if isinstance(content, str):
            content_parts.append(content)
    content = "".join(content_parts)
    if not content:
        raise ValueError("stream contained no assistant content")
    return content


def fallback_plan(
    message: str,
    *,
    app_name: str | None = None,
    app_mode: str = "workflow",
) -> WorkflowPlan:
    name = app_name or _title_from_message(message)
    subject = _subject_from_title(name)
    chatflow = app_mode == "advanced-chat"
    query_reference = "{{#sys.query#}}" if chatflow else "{{#start.query#}}"
    terminal_node = (
        {
            "id": "answer",
            "type": "answer",
            "title": f"回复{subject}用户",
            "params": {"answer": "{{#llm.text#}}"},
        }
        if chatflow
        else {
            "id": "end",
            "type": "end",
            "title": f"返回{subject}结果",
            "params": {
                "outputs": [
                    {"variable": "answer", "value_selector": ["llm", "text"]}
                ]
            },
        }
    )
    return WorkflowPlan.model_validate(
        {
            "name": name,
            "description": "A simple workflow generated without an LLM planner.",
            "app_mode": app_mode,
            "nodes": [
                {
                    "id": "start",
                    "type": "start",
                    "title": f"接收{subject}诉求",
                    "params": (
                        {"variables": []}
                        if chatflow
                        else {
                            "variables": [
                                {
                                    "name": "query",
                                    "type": "paragraph",
                                    "required": True,
                                    "label": "用户输入",
                                }
                            ]
                        }
                    ),
                },
                {
                    "id": "llm",
                    "type": "llm",
                    "title": f"生成{subject}回复",
                    "params": {
                        "system_prompt": (
                            f"你是{subject}专员，负责根据用户输入生成专业、礼貌、可执行的回复。\n"
                            "规则：先理解用户诉求，再给出清晰处理建议；不得编造订单、金额、门店或政策信息；"
                            "遇到不确定信息时说明需要进一步核实。\n"
                            "输出格式：用自然中文输出，结构清楚，语气友好。\n"
                            "审核标准：回复必须贴合用户输入，不推卸责任，不承诺超出权限的赔付或处理结果。"
                        ),
                        "user_prompt": f"请根据以下用户输入完成“{message}”任务：\n{query_reference}",
                    },
                },
                terminal_node,
            ],
            "edges": [
                {"source": "start", "target": "llm"},
                {"source": "llm", "target": "answer" if chatflow else "end"},
            ],
        }
    )


def _prepare_fallback_for_trigger(
    payload: dict[str, Any],
    *,
    message: str,
    trigger_selection: dict[str, Any] | None,
) -> dict[str, Any]:
    if not isinstance(trigger_selection, dict):
        return payload
    trigger_type = str(trigger_selection.get("type") or "user-input")
    if trigger_type not in {"webhook", "schedule", "plugin"}:
        return payload

    data = json.loads(json.dumps(payload, ensure_ascii=False))
    llm_node = next(
        (
            node
            for node in data.get("nodes", [])
            if isinstance(node, dict) and node.get("type") == "llm"
        ),
        None,
    )
    if llm_node is None:
        return data

    params = llm_node.setdefault("params", {})
    if trigger_type == "schedule":
        params["user_prompt"] = f"请执行以下定时工作要求，并生成本次执行结果：\n{message}"
        return data
    if trigger_type == "plugin":
        output_schema = (
            trigger_selection.get("output_schema")
            if isinstance(trigger_selection.get("output_schema"), dict)
            else {}
        )
        properties = (
            output_schema.get("properties")
            if isinstance(output_schema.get("properties"), dict)
            else {}
        )
        variable = next(iter(properties), "")
        if variable:
            params["user_prompt"] = (
                f"请根据插件事件本次传入的数据完成任务：{{{{#start.{variable}#}}}}\n"
                f"任务要求：{message}"
            )
        else:
            params["user_prompt"] = f"请处理本次插件事件，并完成任务：\n{message}"
        return data

    declared: list[str] = []
    for group in ("body", "params", "headers"):
        for item in trigger_selection.get(group) or []:
            if not isinstance(item, dict) or not item.get("name"):
                continue
            name = str(item["name"])
            declared.append(name.replace("-", "_") if group == "headers" else name)
    entry = next(
        (
            node
            for node in data.get("nodes", [])
            if isinstance(node, dict) and node.get("type") == "start"
        ),
        {},
    )
    entry_id = str(entry.get("id") or "start")
    variable = declared[0] if declared else "_webhook_raw"
    params["user_prompt"] = (
        f"请根据 Webhook 本次传入的数据完成任务：{{{{#{entry_id}.{variable}#}}}}\n"
        f"任务要求：{message}"
    )
    return data


def _validate_compiled_plan(plan: WorkflowPlan, *, settings: Settings, dsl_version: str) -> list[ValidationIssue]:
    compiler = DifyDslCompiler(
        dsl_version=dsl_version,
        default_model_provider=settings.dify_default_model_provider,
        default_model_name=settings.dify_default_model_name,
        default_dataset_ids=settings.dify_default_dataset_ids,
    )
    dsl = compiler.compile(plan)
    return [
        *validate_plan(plan),
        *validate_dsl(dsl, expected_dsl_version=dsl_version),
    ]


def _validate_creation_resource_bindings(
    plan: WorkflowPlan,
    *,
    dataset_ids: list[str],
    tool_selections: list[dict[str, Any]],
    agent_selections: list[dict[str, Any]],
) -> list[ValidationIssue]:
    issues: list[ValidationIssue] = []
    if plan.app_mode == "advanced-chat":
        for node in plan.nodes:
            if node.type == "assigner":
                issues.append(
                    ValidationIssue(
                        code="PLAN_CHATFLOW_TOP_LEVEL_ASSIGNER_NOT_SUPPORTED",
                        message="Chatflow creation does not support top-level assigner nodes.",
                        node_id=node.id,
                        path=f"nodes.{node.id}.type",
                        suggestion="assigner 仅允许由 loop 规范化器生成，用于更新 loop_variables。",
                    )
                )
            if node.type in {"iteration", "loop"}:
                issues.extend(_validate_chatflow_creation_container(node))

    trusted_dataset_ids = {str(item).strip() for item in dataset_ids if str(item).strip()}
    trusted_tools = {
        (
            str(item.get("provider_id") or "").strip(),
            str(item.get("provider_type") or "").strip(),
            str(item.get("tool_name") or "").strip(),
        )
        for item in tool_selections
        if isinstance(item, dict)
    }
    trusted_agents = {
        (
            str(item.get("agent_strategy_provider_name") or "").strip(),
            str(item.get("agent_strategy_name") or "").strip(),
        )
        for item in agent_selections
        if isinstance(item, dict)
    }

    for node_id, node_type, params, path in _creation_nodes(plan):
        if node_type == "knowledge-retrieval":
            generated_ids = {
                str(item).strip()
                for item in params.get("dataset_ids", [])
                if str(item).strip()
            }
            unknown_ids = sorted(generated_ids - trusted_dataset_ids)
            if unknown_ids:
                issues.append(
                    ValidationIssue(
                        code="PLAN_KNOWLEDGE_DATASET_NOT_SELECTED",
                        message=f"knowledge-retrieval uses unselected dataset_ids: {', '.join(unknown_ids)}",
                        node_id=node_id,
                        path=f"{path}.params.dataset_ids",
                        suggestion="仅使用请求中的 dataset_ids；没有选择知识库时不要生成知识检索节点。",
                    )
                )
        elif node_type == "tool":
            identity = (
                str(params.get("provider_id") or "").strip(),
                str(params.get("provider_type") or "").strip(),
                str(params.get("tool_name") or "").strip(),
            )
            if identity not in trusted_tools:
                issues.append(
                    ValidationIssue(
                        code="PLAN_TOOL_NOT_SELECTED",
                        message="tool node is not bound to a tool selected in the request.",
                        node_id=node_id,
                        path=f"{path}.params",
                        suggestion="从 selected_tools 复制真实 provider 和 tool 标识。",
                    )
                )
        elif node_type == "agent":
            identity = (
                str(params.get("agent_strategy_provider_name") or "").strip(),
                str(params.get("agent_strategy_name") or "").strip(),
            )
            if identity not in trusted_agents:
                issues.append(
                    ValidationIssue(
                        code="PLAN_AGENT_NOT_SELECTED",
                        message="agent node is not bound to an Agent Strategy selected in the request.",
                        node_id=node_id,
                        path=f"{path}.params",
                        suggestion="从 selected_agents 复制真实 Agent Strategy 标识。",
                    )
                )
    return issues


def _creation_nodes(
    plan: WorkflowPlan,
) -> list[tuple[str, str, dict[str, Any], str]]:
    result: list[tuple[str, str, dict[str, Any], str]] = []

    def visit(node_id: str, node_type: str, params: dict[str, Any], path: str) -> None:
        result.append((node_id, node_type, params, path))
        children = params.get("children") if isinstance(params.get("children"), list) else []
        for index, child in enumerate(children):
            if not isinstance(child, dict):
                continue
            child_id = str(child.get("id") or f"{node_id}_child_{index + 1}")
            child_type = str(child.get("type") or "")
            child_params = child.get("params") if isinstance(child.get("params"), dict) else {}
            visit(
                child_id,
                child_type,
                child_params,
                f"{path}.params.children.{index}",
            )

    for node in plan.nodes:
        visit(node.id, node.type, node.params, f"nodes.{node.id}")
    return result


def _validate_chatflow_creation_container(node: Any) -> list[ValidationIssue]:
    issues: list[ValidationIssue] = []
    children = node.params.get("children") if isinstance(node.params.get("children"), list) else []
    child_by_id = {
        str(child.get("id")): child
        for child in children
        if isinstance(child, dict) and child.get("id")
    }
    forbidden_types = {
        "answer",
        "human-input",
        "if-else",
        "question-classifier",
        "iteration",
        "loop",
    }
    for index, child in enumerate(children):
        if not isinstance(child, dict):
            continue
        child_id = str(child.get("id") or f"{node.id}_child_{index + 1}")
        child_type = str(child.get("type") or "")
        if child_type in forbidden_types or (
            child_type == "assigner" and node.type != "loop"
        ):
            issues.append(
                ValidationIssue(
                    code="PLAN_CHATFLOW_CONTAINER_NODE_NOT_SUPPORTED",
                    message=(
                        f"Chatflow {node.type} creation does not support "
                        f"internal node type: {child_type}"
                    ),
                    node_id=child_id,
                    path=f"nodes.{node.id}.params.children.{index}.type",
                    suggestion="容器内部使用无分支处理链；人工输入和 answer 放在顶层。",
                )
            )

    edges = node.params.get("edges") if isinstance(node.params.get("edges"), list) else []
    adjacency: dict[str, list[str]] = {child_id: [] for child_id in child_by_id}
    incoming: dict[str, list[str]] = {child_id: [] for child_id in child_by_id}
    valid_edges = 0
    for edge in edges:
        if not isinstance(edge, dict):
            continue
        source = str(edge.get("source") or "")
        target = str(edge.get("target") or "")
        if source not in child_by_id or target not in child_by_id:
            continue
        adjacency[source].append(target)
        incoming[target].append(source)
        valid_edges += 1

    cyclic = _cyclic_creation_nodes(adjacency)
    if cyclic:
        issues.append(
            ValidationIssue(
                code="PLAN_CHATFLOW_CONTAINER_CYCLE_INVALID",
                message=f"Chatflow {node.type} internal graph contains a cycle: {cyclic[0]}",
                node_id=node.id,
                path=f"nodes.{node.id}.params.edges",
                suggestion="容器内部必须是从内部 start 开始的无环处理链。",
            )
        )

    branched = sorted(
        child_id
        for child_id in child_by_id
        if len(adjacency.get(child_id, [])) > 1 or len(incoming.get(child_id, [])) > 1
    )
    start_node_id = str(node.params.get("start_node_id") or "")
    reachable = _reachable_creation_nodes(start_node_id, adjacency)
    is_chain = (
        bool(child_by_id)
        and start_node_id in child_by_id
        and len(reachable) == len(child_by_id)
        and valid_edges == len(child_by_id) - 1
        and not branched
        and not cyclic
    )
    if not is_chain:
        issues.append(
            ValidationIssue(
                code="PLAN_CHATFLOW_CONTAINER_CHAIN_REQUIRED",
                message=f"Chatflow {node.type} internal graph must be one connected processing chain.",
                node_id=node.id,
                path=f"nodes.{node.id}.params.edges",
                suggestion="让内部 start 依次连接每个处理节点，不要分叉、汇合或形成环。",
            )
        )
    return issues


def _reachable_creation_nodes(
    start_node_id: str,
    adjacency: dict[str, list[str]],
) -> set[str]:
    if start_node_id not in adjacency:
        return set()
    reachable: set[str] = set()
    stack = [start_node_id]
    while stack:
        node_id = stack.pop()
        if node_id in reachable:
            continue
        reachable.add(node_id)
        stack.extend(adjacency.get(node_id, []))
    return reachable


def _cyclic_creation_nodes(adjacency: dict[str, list[str]]) -> list[str]:
    visiting: set[str] = set()
    visited: set[str] = set()
    cyclic: set[str] = set()

    def visit(node_id: str) -> None:
        if node_id in visiting:
            cyclic.add(node_id)
            return
        if node_id in visited:
            return
        visiting.add(node_id)
        for target in adjacency.get(node_id, []):
            visit(target)
        visiting.remove(node_id)
        visited.add(node_id)

    for node_id in adjacency:
        visit(node_id)
    return sorted(cyclic)


def _issues_to_feedback(issues: list[ValidationIssue]) -> str:
    return json.dumps([issue.model_dump() for issue in issues], ensure_ascii=False)


def _extract_plan_payload(payload: dict[str, Any]) -> dict[str, Any]:
    if "nodes" in payload and "edges" in payload:
        return payload
    for key in ("plan", "workflow", "workflow_plan"):
        nested = payload.get(key)
        if isinstance(nested, dict) and "nodes" in nested and "edges" in nested:
            return nested
    raise ValueError("LLM response must contain a WorkflowPlan object with nodes and edges")


def _planner_tool_schemas(tool_selections: list[dict[str, Any]]) -> list[dict[str, Any]]:
    result = []
    for item in tool_selections:
        if not isinstance(item, dict):
            continue
        result.append(
            {
                "provider_id": item.get("provider_id"),
                "provider_type": item.get("provider_type"),
                "provider_name": item.get("provider_name"),
                "tool_name": item.get("tool_name"),
                "tool_label": item.get("tool_label"),
                "description": item.get("description"),
                "parameters": item.get("parameters") or [],
                "output_schema": item.get("output_schema") or {},
                "plugin_id": item.get("plugin_id"),
                "plugin_unique_identifier": item.get("plugin_unique_identifier"),
            }
        )
    return result


def _planner_agent_schemas(agent_selections: list[dict[str, Any]]) -> list[dict[str, Any]]:
    result = []
    for item in agent_selections:
        if not isinstance(item, dict):
            continue
        result.append(
            {
                "agent_strategy_provider_name": item.get("agent_strategy_provider_name"),
                "agent_strategy_name": item.get("agent_strategy_name"),
                "agent_strategy_label": item.get("agent_strategy_label"),
                "description": item.get("description"),
                "parameters": item.get("parameters") or [],
                "features": item.get("features") or [],
                "output_schema": item.get("output_schema") or {},
                "plugin_unique_identifier": item.get("plugin_unique_identifier"),
                "meta": item.get("meta") or {},
                "agent_parameters": item.get("agent_parameters") or {},
            }
        )
    return result


def _title_from_message(message: str) -> str:
    compact = re.sub(r"\s+", " ", message).strip()
    return compact[:30] or "Generated Workflow"


def _subject_from_title(title: str) -> str:
    text = re.sub(r"\s+", " ", title).strip()
    for suffix in ("工作流", "流程", "机器人", "助手", "自动化", "处理"):
        text = text.replace(suffix, "")
    text = text.strip(" -_：:，,。.")
    return text[:16] or "业务"


def _strip_json_fences(content: str) -> str:
    text = content.strip()
    if text.startswith("```"):
        text = re.sub(r"^```(?:json)?\s*", "", text)
        text = re.sub(r"\s*```$", "", text)
    return text.strip()


def _chat_completions_url(base_url: str) -> str:
    if base_url.endswith("/chat/completions"):
        return base_url
    if base_url.endswith("/v1"):
        return f"{base_url}/chat/completions"
    return f"{base_url}/v1/chat/completions"
