from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable, Literal, Protocol, TypeVar
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field, ValidationError


class ToolSpec(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str = Field(pattern=r"^[a-z][a-z0-9_.-]{0,127}$")
    version: str = Field(pattern=r"^[0-9]+\.[0-9]+\.[0-9]+$")
    description: str = Field(min_length=1, max_length=2_000)
    side_effect: Literal["none", "workspace", "draft_run", "dify_write"]
    approval: Literal["never", "policy", "always"]
    input_schema: dict[str, Any]
    output_schema: dict[str, Any]


class ToolError(BaseModel):
    model_config = ConfigDict(extra="forbid")

    code: str = Field(min_length=1, max_length=128)
    message: str = Field(min_length=1, max_length=2_000)
    details: list[dict[str, Any]] = Field(default_factory=list)
    retryable: bool = False


class ToolResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    call_id: str = Field(default_factory=lambda: str(uuid4()))
    ok: bool
    observation: dict[str, Any] = Field(default_factory=dict)
    error: ToolError | None = None
    workspace_version: str | None = None


class ToolExecutor(Protocol):
    def __call__(self, arguments: BaseModel, context: "ToolExecutionContext") -> Any: ...


class ToolExecutionContext(BaseModel):
    model_config = ConfigDict(extra="forbid")

    session_id: str | None = None
    run_id: str | None = None
    call_id: str


InputModelT = TypeVar("InputModelT", bound=BaseModel)
OutputModelT = TypeVar("OutputModelT", bound=BaseModel)


@dataclass(frozen=True)
class RegisteredTool:
    spec: ToolSpec
    input_model: type[BaseModel]
    output_model: type[BaseModel]
    executor: Callable[[BaseModel, ToolExecutionContext], Any]


class ToolRegistry:
    def __init__(self) -> None:
        self._tools: dict[str, RegisteredTool] = {}

    def register(
        self,
        *,
        name: str,
        version: str,
        description: str,
        side_effect: Literal["none", "workspace", "draft_run", "dify_write"],
        approval: Literal["never", "policy", "always"],
        input_model: type[InputModelT],
        output_model: type[OutputModelT],
        executor: Callable[[InputModelT, ToolExecutionContext], OutputModelT | dict[str, Any]],
    ) -> ToolSpec:
        if name in self._tools:
            raise ValueError(f"Tool is already registered: {name}")
        spec = ToolSpec(
            name=name,
            version=version,
            description=description,
            side_effect=side_effect,
            approval=approval,
            input_schema=input_model.model_json_schema(),
            output_schema=output_model.model_json_schema(),
        )
        self._tools[name] = RegisteredTool(
            spec=spec,
            input_model=input_model,
            output_model=output_model,
            executor=executor,
        )
        return spec

    def specs(self) -> list[ToolSpec]:
        return [self._tools[name].spec for name in sorted(self._tools)]

    def get(self, name: str) -> RegisteredTool | None:
        return self._tools.get(name)

    def execute(
        self,
        name: str,
        payload: dict[str, Any],
        *,
        session_id: str | None = None,
        run_id: str | None = None,
        call_id: str | None = None,
    ) -> ToolResult:
        resolved_call_id = call_id or str(uuid4())
        registered = self._tools.get(name)
        if registered is None:
            return _tool_failure(
                resolved_call_id,
                code="TOOL_UNKNOWN",
                message=f"Unknown tool: {name}",
            )
        try:
            arguments = registered.input_model.model_validate(payload)
        except ValidationError as exc:
            return _tool_failure(
                resolved_call_id,
                code="TOOL_INPUT_INVALID",
                message=f"Input validation failed for tool {name}.",
                details=exc.errors(include_url=False, include_input=False),
            )
        context = ToolExecutionContext(
            session_id=session_id,
            run_id=run_id,
            call_id=resolved_call_id,
        )
        try:
            raw_output = registered.executor(arguments, context)
        except Exception as exc:  # noqa: BLE001 - registry returns a stable public failure.
            return _tool_failure(
                resolved_call_id,
                code="TOOL_EXECUTION_FAILED",
                message=f"Tool {name} failed with {exc.__class__.__name__}.",
                retryable=True,
            )
        try:
            output = registered.output_model.model_validate(raw_output)
        except ValidationError as exc:
            return _tool_failure(
                resolved_call_id,
                code="TOOL_OUTPUT_INVALID",
                message=f"Output validation failed for tool {name}.",
                details=exc.errors(include_url=False, include_input=False),
            )
        return ToolResult(
            call_id=resolved_call_id,
            ok=True,
            observation=output.model_dump(mode="json"),
        )


def _tool_failure(
    call_id: str,
    *,
    code: str,
    message: str,
    details: list[dict[str, Any]] | None = None,
    retryable: bool = False,
) -> ToolResult:
    return ToolResult(
        call_id=call_id,
        ok=False,
        error=ToolError(
            code=code,
            message=message,
            details=details or [],
            retryable=retryable,
        ),
    )
