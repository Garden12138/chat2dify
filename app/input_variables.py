from __future__ import annotations

from copy import deepcopy
from typing import Any


FILE_INPUT_TYPES = {"file", "file-list"}
DEFAULT_FILE_UPLOAD_SETTING = {
    "allowed_file_upload_methods": ["local_file", "remote_url"],
    "allowed_file_types": ["document", "image"],
    "allowed_file_extensions": [],
}


def is_file_input_type(input_type: str) -> bool:
    return input_type in FILE_INPUT_TYPES


def file_upload_settings(item: dict[str, Any], *, input_type: str) -> dict[str, Any]:
    settings = {
        "allowed_file_upload_methods": _list_setting(
            item,
            "allowed_file_upload_methods",
            DEFAULT_FILE_UPLOAD_SETTING["allowed_file_upload_methods"],
            "allow_file_upload_methods",
            "allowed_upload_methods",
        ),
        "allowed_file_types": _list_setting(
            item,
            "allowed_file_types",
            DEFAULT_FILE_UPLOAD_SETTING["allowed_file_types"],
        ),
        "allowed_file_extensions": _list_setting(
            item,
            "allowed_file_extensions",
            DEFAULT_FILE_UPLOAD_SETTING["allowed_file_extensions"],
            "allow_file_extension",
        ),
    }
    if item.get("max_length") is not None:
        settings["max_length"] = item.get("max_length")
    elif input_type == "file-list":
        settings["max_length"] = 5
    else:
        settings["max_length"] = 1
    return settings


def _list_setting(item: dict[str, Any], key: str, default: list[str], *legacy_keys: str) -> list[str]:
    value = item.get(key)
    if value is None:
        for legacy_key in legacy_keys:
            value = item.get(legacy_key)
            if value is not None:
                break
    if isinstance(value, list):
        return deepcopy(value)
    return deepcopy(default)
