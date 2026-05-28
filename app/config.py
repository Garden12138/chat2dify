from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from typing import Mapping


PROJECT_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_DIFY_SOURCE_DIR = "../dify"


class ConfigurationError(ValueError):
    """Raised when environment configuration is invalid."""


@dataclass(frozen=True)
class Settings:
    project_root: Path
    dify_source_dir: str
    dify_source_path: Path
    dify_console_api_base: str
    dify_console_web_base: str
    dify_email: str | None
    dify_password: str | None
    dify_login_language: str
    dify_default_model_provider: str
    dify_default_model_name: str
    openai_api_key: str | None
    openai_base_url: str
    openai_model: str

    @classmethod
    def from_env(
        cls,
        env: Mapping[str, str] | None = None,
        *,
        project_root: Path | None = None,
        validate_dify: bool = True,
    ) -> "Settings":
        root = (project_root or PROJECT_ROOT).resolve()
        source = env if env is not None else _load_environment(root)
        dify_source_dir = source.get("DIFY_SOURCE_DIR", DEFAULT_DIFY_SOURCE_DIR)
        dify_source_path = resolve_path_from_project_root(dify_source_dir, root)

        if validate_dify:
            validate_dify_source_path(dify_source_path)

        return cls(
            project_root=root,
            dify_source_dir=dify_source_dir,
            dify_source_path=dify_source_path,
            dify_console_api_base=source.get("DIFY_CONSOLE_API_BASE", "http://localhost:5001/console/api").rstrip("/"),
            dify_console_web_base=source.get("DIFY_CONSOLE_WEB_BASE", "http://localhost:3000").rstrip("/"),
            dify_email=_empty_to_none(source.get("DIFY_EMAIL")),
            dify_password=_empty_to_none(source.get("DIFY_PASSWORD")),
            dify_login_language=source.get("DIFY_LOGIN_LANGUAGE", "en-US"),
            dify_default_model_provider=source.get("DIFY_DEFAULT_MODEL_PROVIDER", "langgenius/openai/openai"),
            dify_default_model_name=source.get("DIFY_DEFAULT_MODEL_NAME", "gpt-4o-mini"),
            openai_api_key=_empty_to_none(source.get("OPENAI_API_KEY")),
            openai_base_url=source.get("OPENAI_BASE_URL", "https://api.openai.com/v1").rstrip("/"),
            openai_model=source.get("OPENAI_MODEL", "gpt-4o-mini"),
        )

    def workflow_url(self, app_id: str) -> str:
        return f"{self.dify_console_web_base}/app/{app_id}/workflow"


def load_settings(*, validate_dify: bool = True) -> Settings:
    return Settings.from_env(validate_dify=validate_dify)


def resolve_path_from_project_root(raw_path: str, project_root: Path) -> Path:
    path = Path(raw_path).expanduser()
    if not path.is_absolute():
        path = project_root / path
    return path.resolve()


def validate_dify_source_path(path: Path) -> None:
    if not path.exists():
        raise ConfigurationError(f"DIFY_SOURCE_DIR does not exist: {path}")
    dsl_version_file = path / "api" / "constants" / "dsl_version.py"
    if not dsl_version_file.is_file():
        raise ConfigurationError(f"Dify DSL version file not found: {dsl_version_file}")


def _empty_to_none(value: str | None) -> str | None:
    if value is None:
        return None
    value = value.strip()
    return value or None


def _load_environment(project_root: Path) -> dict[str, str]:
    file_values = _read_dotenv(project_root / ".env")
    return {**file_values, **os.environ}


def _read_dotenv(path: Path) -> dict[str, str]:
    if not path.is_file():
        return {}

    values: dict[str, str] = {}
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in {"'", '"'}:
            value = value[1:-1]
        if key:
            values[key] = value
    return values
