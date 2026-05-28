from __future__ import annotations

import re
import subprocess
from dataclasses import dataclass
from pathlib import Path


DSL_VERSION_PATTERN = re.compile(r'CURRENT_APP_DSL_VERSION\s*=\s*["\']([^"\']+)["\']')


@dataclass(frozen=True)
class DifyVersionInfo:
    source_dir: str
    git_describe: str
    app_dsl_version: str


def read_app_dsl_version(dify_source_path: Path) -> str:
    version_file = dify_source_path / "api" / "constants" / "dsl_version.py"
    text = version_file.read_text(encoding="utf-8")
    match = DSL_VERSION_PATTERN.search(text)
    if not match:
        raise ValueError(f"Could not find CURRENT_APP_DSL_VERSION in {version_file}")
    return match.group(1)


def read_git_describe(dify_source_path: Path) -> str:
    try:
        result = subprocess.run(
            ["git", "describe", "--tags", "--always"],
            cwd=dify_source_path,
            check=True,
            capture_output=True,
            text=True,
        )
    except (OSError, subprocess.CalledProcessError):
        return "unknown"
    return result.stdout.strip() or "unknown"


def read_dify_version_info(dify_source_path: Path) -> DifyVersionInfo:
    return DifyVersionInfo(
        source_dir=str(dify_source_path),
        git_describe=read_git_describe(dify_source_path),
        app_dsl_version=read_app_dsl_version(dify_source_path),
    )

