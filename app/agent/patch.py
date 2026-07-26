from __future__ import annotations

import json
from typing import Annotated, Any, Literal

from pydantic import ConfigDict, Field, StringConstraints, field_validator

from app.agent.state import StrictModel
from app.models import NodeType


MAX_PATCH_OPERATIONS = 50
MAX_PATCH_VALUE_BYTES = 32_768
MAX_PATCH_VALUE_DEPTH = 8
MAX_PATCH_CONTAINER_ITEMS = 200
_DANGEROUS_KEYS = {"__proto__", "constructor", "prototype"}

NodeReference = Annotated[
    str,
    StringConstraints(strip_whitespace=True, min_length=1, max_length=128),
]
TempReference = Annotated[
    str,
    StringConstraints(
        strip_whitespace=True,
        min_length=5,
        max_length=68,
        pattern=r"^tmp_[A-Za-z0-9_-]+$",
    ),
]


class PatchOperationBase(StrictModel):
    op: str


class AddNode(PatchOperationBase):
    op: Literal["node.add"]
    temp_ref: TempReference
    node_type: NodeType
    title: str = Field(min_length=1, max_length=256)
    params: dict[str, Any] = Field(default_factory=dict)
    after_node_id: NodeReference | None = None

    @field_validator("params")
    @classmethod
    def validate_params(cls, value: dict[str, Any]) -> dict[str, Any]:
        _validate_bounded_json(value, field_name="params")
        return value


class UpdateNode(PatchOperationBase):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    op: Literal["node.update"]
    node_id: NodeReference
    set_values: dict[str, Any] = Field(alias="set", min_length=1)
    expected: dict[str, Any] | None = None

    @field_validator("set_values", "expected")
    @classmethod
    def validate_update_payload(
        cls,
        value: dict[str, Any] | None,
    ) -> dict[str, Any] | None:
        if value is not None:
            _validate_bounded_json(value, field_name="node.update")
        return value


class AddEdge(PatchOperationBase):
    op: Literal["edge.add"]
    source: NodeReference
    source_handle: str = Field(default="source", min_length=1, max_length=128)
    target: NodeReference
    target_handle: str = Field(default="target", min_length=1, max_length=128)


class RemoveEdge(PatchOperationBase):
    op: Literal["edge.remove"]
    source: NodeReference
    source_handle: str = Field(min_length=1, max_length=128)
    target: NodeReference
    target_handle: str = Field(min_length=1, max_length=128)


PatchOperation = Annotated[
    AddNode | UpdateNode | AddEdge | RemoveEdge,
    Field(discriminator="op"),
]


class PatchDocument(StrictModel):
    workspace_version: str = Field(min_length=1, max_length=128)
    expected_base_hash: str | None = Field(
        min_length=1,
        max_length=512,
    )
    operations: list[PatchOperation] = Field(min_length=1, max_length=MAX_PATCH_OPERATIONS)
    rationale: str = Field(min_length=1, max_length=2_000)


def _validate_bounded_json(value: Any, *, field_name: str) -> None:
    item_count = 0

    def visit(item: Any, depth: int) -> None:
        nonlocal item_count
        if depth > MAX_PATCH_VALUE_DEPTH:
            raise ValueError(
                f"{field_name} exceeds the maximum nesting depth of {MAX_PATCH_VALUE_DEPTH}."
            )
        if isinstance(item, dict):
            item_count += len(item)
            for key, child in item.items():
                key_text = str(key)
                if len(key_text) > 256:
                    raise ValueError(f"{field_name} contains a key longer than 256 characters.")
                if key_text in _DANGEROUS_KEYS or key_text.startswith("/"):
                    raise ValueError(f"{field_name} contains a forbidden key: {key_text}.")
                visit(child, depth + 1)
        elif isinstance(item, (list, tuple)):
            item_count += len(item)
            for child in item:
                visit(child, depth + 1)
        elif item is not None and not isinstance(item, (str, int, float, bool)):
            raise ValueError(f"{field_name} must contain JSON-compatible values.")
        if item_count > MAX_PATCH_CONTAINER_ITEMS:
            raise ValueError(
                f"{field_name} exceeds the maximum container size of "
                f"{MAX_PATCH_CONTAINER_ITEMS} items."
            )

    visit(value, 0)
    try:
        encoded = json.dumps(
            value,
            ensure_ascii=False,
            separators=(",", ":"),
            allow_nan=False,
        ).encode("utf-8")
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{field_name} must be valid finite JSON.") from exc
    if len(encoded) > MAX_PATCH_VALUE_BYTES:
        raise ValueError(
            f"{field_name} exceeds the maximum encoded size of {MAX_PATCH_VALUE_BYTES} bytes."
        )
