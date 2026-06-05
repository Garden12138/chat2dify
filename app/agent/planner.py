from __future__ import annotations

import json
import re
import time
from dataclasses import dataclass, field
from typing import Any

import httpx

from app.agent.normalizer import normalize_plan_payload
from app.compiler.dify import DifyDslCompiler
from app.config import PlannerRuntime, Settings
from app.models import ValidationIssue, WorkflowPlan
from app.validator import has_errors, validate_dsl, validate_plan


SYSTEM_PROMPT = """You turn a user's workflow request into a compact JSON WorkflowPlan.
Return only JSON. Supported node types are:
start, llm, code, if-else, end, http-request, template-transform,
question-classifier, parameter-extractor, variable-aggregator,
document-extractor, list-operator, knowledge-retrieval, human-input,
iteration, loop. iteration-start, loop-start, and loop-end are internal
container children only; never place them in top-level nodes.
Do not generate datasource, datasource-empty, trigger-webhook, trigger-plugin,
trigger-schedule, or knowledge-index nodes in new workflows.
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
        dsl_version: str,
        tool_selections: list[dict[str, Any]] | None = None,
        agent_selections: list[dict[str, Any]] | None = None,
    ) -> PlannerResult:
        runtime = self.settings.planner_runtime()
        if not runtime.api_key:
            plan = fallback_plan(message, app_name=app_name)
            return PlannerResult(
                plan=plan,
                raw_plan=plan.model_dump(),
                mode="fallback",
                attempts=0,
                used_fallback=True,
                repaired=False,
                provider=runtime.provider,
                model=runtime.model,
            )

        last_error = ""
        errors: list[str] = []
        final_raw_plan: dict[str, Any] | None = None
        for attempt in range(1, 4):
            content = self._call_llm(
                message,
                app_name=app_name,
                last_error=last_error if attempt else "",
                tool_selections=tool_selections or [],
                agent_selections=agent_selections or [],
            )
            try:
                payload = json.loads(_strip_json_fences(content))
                raw_plan = _extract_plan_payload(payload)
                final_raw_plan = raw_plan
                normalized = normalize_plan_payload(
                    raw_plan,
                    app_name=app_name,
                    default_dataset_ids=self.settings.dify_default_dataset_ids,
                    tool_selections=tool_selections or [],
                    agent_selections=agent_selections or [],
                )
                plan = WorkflowPlan.model_validate(normalized.payload)
                issues = _validate_compiled_plan(plan, settings=self.settings, dsl_version=dsl_version)
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
    ) -> str:
        runtime = self.settings.planner_runtime()
        url = _chat_completions_url(runtime.base_url)
        user_content = {
            "app_name": app_name or "Generated Workflow",
            "request": message,
            "selected_tools": _planner_tool_schemas(tool_selections or []),
            "selected_agents": _planner_agent_schemas(agent_selections or []),
        }
        if last_error:
            user_content["previous_validation_error"] = last_error
        messages: list[dict[str, str]] = [
            {"role": "system", "content": SYSTEM_PROMPT},
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
        )


def _post_chat_completion(
    *,
    runtime: PlannerRuntime,
    url: str,
    payload: dict[str, Any],
    error_prefix: str,
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
        try:
            with httpx.Client(timeout=timeout) as client:
                if payload.get("stream"):
                    with client.stream("POST", url, json=payload, headers=headers) as response:
                        response.raise_for_status()
                        return _read_streamed_chat_completion(response)
                response = client.post(url, json=payload, headers=headers)
                response.raise_for_status()
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
            time.sleep(min(2 ** (request_attempt - 1), 4))

    if isinstance(last_error, httpx.ReadTimeout):
        message = f"timed out after {runtime.timeout_seconds:g} seconds while waiting for a response"
    elif isinstance(last_error, httpx.HTTPStatusError):
        message = f"{last_error.response.status_code} {last_error.response.text}"
    else:
        message = str(last_error)
    raise PlannerError(
        f"{error_prefix} request failed after {total_attempts} network attempts: {message}"
    ) from last_error


def _read_streamed_chat_completion(response: httpx.Response) -> str:
    content_parts: list[str] = []
    for raw_line in response.iter_lines():
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


def fallback_plan(message: str, *, app_name: str | None = None) -> WorkflowPlan:
    name = app_name or _title_from_message(message)
    subject = _subject_from_title(name)
    return WorkflowPlan.model_validate(
        {
            "name": name,
            "description": "A simple workflow generated without an LLM planner.",
            "nodes": [
                {
                    "id": "start",
                    "type": "start",
                    "title": f"接收{subject}诉求",
                    "params": {
                        "variables": [
                            {"name": "query", "type": "paragraph", "required": True, "label": "用户输入"}
                        ]
                    },
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
                        "user_prompt": f"请根据以下用户输入完成“{message}”任务：\n{{{{#start.query#}}}}",
                    },
                },
                {
                    "id": "end",
                    "type": "end",
                    "title": f"返回{subject}结果",
                    "params": {
                        "outputs": [
                            {"variable": "answer", "value_selector": ["llm", "text"]}
                        ]
                    },
                },
            ],
            "edges": [
                {"source": "start", "target": "llm"},
                {"source": "llm", "target": "end"},
            ],
        }
    )


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
