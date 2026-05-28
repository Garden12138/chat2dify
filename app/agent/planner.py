from __future__ import annotations

import json
import re
from typing import Any

import httpx

from app.config import Settings
from app.models import WorkflowPlan


SYSTEM_PROMPT = """You turn a user's workflow request into a compact JSON WorkflowPlan.
Return only JSON. Supported node types are:
start, llm, code, if-else, end, http-request, template-transform.
Use exactly one start node and at least one end node. Keep nodes connected.
For simple requests, use start -> llm -> end.
Use start params.variables, not params.inputs.
Use llm params.user_prompt, not params.prompt.
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


class WorkflowPlanner:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings

    def generate_plan(self, message: str, *, app_name: str | None = None) -> WorkflowPlan:
        if not self.settings.openai_api_key:
            return fallback_plan(message, app_name=app_name)

        last_error = ""
        for attempt in range(2):
            content = self._call_llm(message, app_name=app_name, last_error=last_error if attempt else "")
            try:
                payload = json.loads(_strip_json_fences(content))
                return WorkflowPlan.model_validate(payload)
            except Exception as exc:  # noqa: BLE001 - error text is fed back to the LLM once.
                last_error = str(exc)
        raise PlannerError(f"Could not generate a valid WorkflowPlan: {last_error}")

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
    return WorkflowPlan.model_validate(
        {
            "name": name,
            "description": "A simple workflow generated without an LLM planner.",
            "nodes": [
                {
                    "id": "start",
                    "type": "start",
                    "title": "Start",
                    "params": {
                        "variables": [
                            {"name": "query", "type": "paragraph", "required": True, "label": "Query"}
                        ]
                    },
                },
                {
                    "id": "llm",
                    "type": "llm",
                    "title": "LLM",
                    "params": {
                        "system_prompt": "You are a helpful workflow assistant.",
                        "user_prompt": f"User request: {message}\n\nInput: {{{{#start.query#}}}}",
                    },
                },
                {
                    "id": "end",
                    "type": "end",
                    "title": "End",
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


def _title_from_message(message: str) -> str:
    compact = re.sub(r"\s+", " ", message).strip()
    return compact[:30] or "Generated Workflow"


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
