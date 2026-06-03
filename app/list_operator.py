from __future__ import annotations

from typing import Any


DIFY_LIST_COMPARISON_OPERATORS = {
    "contains",
    "not contains",
    "start with",
    "end with",
    "is",
    "is not",
    "empty",
    "not empty",
    "=",
    "≠",
    ">",
    "<",
    "≥",
    "≤",
    "is null",
    "is not null",
    "null",
    "not null",
    "in",
    "not in",
    "all of",
    "exists",
    "not exists",
}

_COMPARISON_OPERATOR_ALIASES = {
    "==": "=",
    "===": "=",
    "eq": "=",
    "equal": "=",
    "equals": "=",
    "is equal": "=",
    "is_equal": "=",
    "is-equal": "=",
    "!=": "≠",
    "<>": "≠",
    "!==": "≠",
    "neq": "≠",
    "ne": "≠",
    "not equal": "≠",
    "not_equal": "≠",
    "not-equal": "≠",
    "gt": ">",
    "greater than": ">",
    "greater_than": ">",
    "greater-than": ">",
    "lt": "<",
    "less than": "<",
    "less_than": "<",
    "less-than": "<",
    "gte": "≥",
    "ge": "≥",
    ">=": "≥",
    "greater or equal": "≥",
    "greater_or_equal": "≥",
    "greater-than-or-equal": "≥",
    "greater than or equal": "≥",
    "lte": "≤",
    "le": "≤",
    "<=": "≤",
    "less or equal": "≤",
    "less_or_equal": "≤",
    "less-than-or-equal": "≤",
    "less than or equal": "≤",
    "not_contains": "not contains",
    "not-contains": "not contains",
    "does not contain": "not contains",
    "does_not_contain": "not contains",
    "startswith": "start with",
    "starts_with": "start with",
    "starts-with": "start with",
    "start_with": "start with",
    "start-with": "start with",
    "endswith": "end with",
    "ends_with": "end with",
    "ends-with": "end with",
    "end_with": "end with",
    "end-with": "end with",
    "isnot": "is not",
    "is_not": "is not",
    "is-not": "is not",
    "not_is": "is not",
    "not-is": "is not",
    "not_empty": "not empty",
    "not-empty": "not empty",
    "is_null": "is null",
    "is-null": "is null",
    "not_null": "is not null",
    "not-null": "is not null",
    "is_not_null": "is not null",
    "is-not-null": "is not null",
    "not_in": "not in",
    "not-in": "not in",
    "all_of": "all of",
    "all-of": "all of",
    "not_exists": "not exists",
    "not-exists": "not exists",
}

def normalize_list_comparison_operator(value: Any, *, default: str = "contains") -> str:
    text = str(value or "").strip()
    if not text:
        text = default
    if text in DIFY_LIST_COMPARISON_OPERATORS:
        return text

    alias = " ".join(text.lower().replace("_", " ").replace("-", " ").split())
    if alias in DIFY_LIST_COMPARISON_OPERATORS:
        return alias
    return _COMPARISON_OPERATOR_ALIASES.get(alias, _COMPARISON_OPERATOR_ALIASES.get(text.lower(), text))


def normalize_list_variable_selector(value: list[str]) -> list[str]:
    return value
