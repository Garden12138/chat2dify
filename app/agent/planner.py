from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from typing import Any

import httpx

from app.agent.normalizer import normalize_plan_payload
from app.compiler.dify import DifyDslCompiler
from app.config import Settings
from app.models import ValidationIssue, WorkflowPlan
from app.validator import has_errors, validate_dsl, validate_plan


SYSTEM_PROMPT = """You turn a user's workflow request into a compact JSON WorkflowPlan.
Return only JSON. Supported node types are:
start, llm, code, if-else, end, http-request, template-transform,
question-classifier, parameter-extractor, variable-aggregator,
document-extractor, list-operator.
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
Use list-operator only when the request explicitly involves filtering/sorting/limiting an array. Its params must include:
{"variable":["start","items"],"var_type":"array[string]","item_var_type":"string","filter_by":{"enabled":false,"conditions":[]},"extract_by":{"enabled":false,"serial":"1"},"order_by":{"enabled":false,"key":"","value":"asc"},"limit":{"enabled":false,"size":10}}.
Do not generate assigner in new workflows; it is reserved for editing existing Dify drafts with explicit variable assignment context.
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
    normalizations: list[str] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)

    def metadata(self) -> dict[str, Any]:
        return {
            "mode": self.mode,
            "attempts": self.attempts,
            "used_fallback": self.used_fallback,
            "repaired": self.repaired,
            "normalizations": self.normalizations,
            "errors": self.errors,
        }


class WorkflowPlanner:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings

    def generate_plan(self, message: str, *, app_name: str | None = None) -> WorkflowPlan:
        return self.generate(message, app_name=app_name, dsl_version="0.0.0").plan

    def generate(self, message: str, *, app_name: str | None = None, dsl_version: str) -> PlannerResult:
        if not self.settings.openai_api_key:
            plan = fallback_plan(message, app_name=app_name)
            return PlannerResult(
                plan=plan,
                raw_plan=plan.model_dump(),
                mode="fallback",
                attempts=0,
                used_fallback=True,
                repaired=False,
            )

        last_error = ""
        errors: list[str] = []
        final_raw_plan: dict[str, Any] | None = None
        for attempt in range(1, 4):
            content = self._call_llm(message, app_name=app_name, last_error=last_error if attempt else "")
            try:
                payload = json.loads(_strip_json_fences(content))
                raw_plan = _extract_plan_payload(payload)
                final_raw_plan = raw_plan
                normalized = normalize_plan_payload(raw_plan, app_name=app_name)
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
                    normalizations=normalized.changes,
                    errors=errors,
                )
            except Exception as exc:  # noqa: BLE001 - error text is fed back to the LLM once.
                last_error = str(exc)
                errors.append(last_error)
        raw_hint = f" Last raw plan: {json.dumps(final_raw_plan, ensure_ascii=False)[:1000]}" if final_raw_plan else ""
        raise PlannerError(f"Could not generate a valid WorkflowPlan after 3 attempts: {last_error}.{raw_hint}")

    def _call_llm(self, message: str, *, app_name: str | None, last_error: str = "") -> str:
        url = _chat_completions_url(self.settings.openai_base_url)
        user_content = {
            "app_name": app_name or "Generated Workflow",
            "request": message,
        }
        messages: list[dict[str, str]] = [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": json.dumps(user_content, ensure_ascii=False)},
        ]
        if last_error:
            messages.append({"role": "user", "content": f"Previous JSON failed validation: {last_error}"})

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
                f"Planner LLM request failed: {exc.response.status_code} {exc.response.text}"
            ) from exc
        except httpx.RequestError as exc:
            raise PlannerError(f"Planner LLM request failed: {exc}") from exc
        data = response.json()
        return str(data["choices"][0]["message"]["content"])


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
