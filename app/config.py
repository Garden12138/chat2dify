from __future__ import annotations

import os
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Mapping


PROJECT_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_DIFY_SOURCE_DIR = "../dify"
DEFAULT_NVIDIA_BASE_URL = "https://integrate.api.nvidia.com/v1"
DEFAULT_NVIDIA_MODEL = "deepseek-ai/deepseek-v4-flash"
NVIDIA_DEEPSEEK_V4_PRO_MODEL = "deepseek-ai/deepseek-v4-pro"
NVIDIA_PLANNER_MODELS = {
    DEFAULT_NVIDIA_MODEL: {
        "label": "DeepSeek V4 Flash",
        "max_tokens": 8192,
    },
    NVIDIA_DEEPSEEK_V4_PRO_MODEL: {
        "label": "DeepSeek V4 Pro",
        "max_tokens": 16384,
    },
}
DEFAULT_OPENROUTER_BASE_URL = "https://openrouter.ai/api/v1"
DEFAULT_OPENROUTER_MODEL = "nvidia/nemotron-3-ultra-550b-a55b:free"
DEFAULT_OPENAI_COMPATIBLE_BASE_URL = "http://localhost:4000/v1"
DEFAULT_OPENAI_COMPATIBLE_MODEL = "gpt-4o-mini"


class ConfigurationError(ValueError):
    """Raised when environment configuration is invalid."""


@dataclass(frozen=True)
class PlannerRuntime:
    provider: str
    label: str
    api_key: str | None
    base_url: str
    model: str
    timeout_seconds: float
    request_retries: int

    @property
    def configured(self) -> bool:
        return bool(self.api_key)


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
    dify_default_dataset_ids: list[str]
    planner_default_provider: str
    planner_fallback_providers: list[str]
    planner_timeout_seconds: float
    planner_request_retries: int
    openai_api_key: str | None
    openai_base_url: str
    openai_model: str
    openai_compatible_api_key: str | None
    openai_compatible_base_url: str
    openai_compatible_model: str
    openai_compatible_label: str
    openai_compatible_max_tokens: int
    openai_compatible_response_format: bool
    nvidia_api_key: str | None
    nvidia_base_url: str
    nvidia_model: str
    nvidia_thinking: bool
    nvidia_reasoning_effort: str
    nvidia_max_tokens: int
    openrouter_api_key: str | None
    openrouter_base_url: str
    openrouter_model: str
    openrouter_max_tokens: int
    chat2dify_public_base_path: str
    task_db_path: Path
    task_workers: int

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

        nvidia_model = source.get("NVIDIA_MODEL", DEFAULT_NVIDIA_MODEL).strip()
        nvidia_max_tokens_default = str(_nvidia_model_max_tokens(nvidia_model))

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
            dify_default_dataset_ids=_csv_list(source.get("DIFY_DEFAULT_DATASET_IDS", "")),
            planner_default_provider=_normalize_planner_provider(
                source.get("PLANNER_DEFAULT_PROVIDER", "nvidia")
            ),
            planner_fallback_providers=_csv_list(
                source.get("PLANNER_FALLBACK_PROVIDERS", "openrouter,openai-compatible,openai")
            ),
            planner_timeout_seconds=_positive_float(
                source.get("PLANNER_TIMEOUT_SECONDS", "600"),
                name="PLANNER_TIMEOUT_SECONDS",
            ),
            planner_request_retries=_non_negative_int(
                source.get("PLANNER_REQUEST_RETRIES", "2"),
                name="PLANNER_REQUEST_RETRIES",
            ),
            openai_api_key=_empty_to_none(source.get("OPENAI_API_KEY")),
            openai_base_url=source.get("OPENAI_BASE_URL", "https://api.openai.com/v1").rstrip("/"),
            openai_model=source.get("OPENAI_MODEL", "gpt-4o-mini"),
            openai_compatible_api_key=_empty_to_none(source.get("OPENAI_COMPATIBLE_API_KEY")),
            openai_compatible_base_url=(
                source.get("OPENAI_COMPATIBLE_BASE_URL", DEFAULT_OPENAI_COMPATIBLE_BASE_URL).strip()
                or DEFAULT_OPENAI_COMPATIBLE_BASE_URL
            ).rstrip("/"),
            openai_compatible_model=(
                source.get("OPENAI_COMPATIBLE_MODEL", DEFAULT_OPENAI_COMPATIBLE_MODEL).strip()
                or DEFAULT_OPENAI_COMPATIBLE_MODEL
            ),
            openai_compatible_label=source.get(
                "OPENAI_COMPATIBLE_LABEL",
                "OpenAI-compatible",
            ).strip() or "OpenAI-compatible",
            openai_compatible_max_tokens=_positive_int(
                source.get("OPENAI_COMPATIBLE_MAX_TOKENS", "8192"),
                name="OPENAI_COMPATIBLE_MAX_TOKENS",
            ),
            openai_compatible_response_format=_boolean(
                source.get("OPENAI_COMPATIBLE_RESPONSE_FORMAT", "true"),
                name="OPENAI_COMPATIBLE_RESPONSE_FORMAT",
            ),
            nvidia_api_key=_empty_to_none(source.get("NVIDIA_API_KEY")),
            nvidia_base_url=source.get("NVIDIA_BASE_URL", DEFAULT_NVIDIA_BASE_URL).rstrip("/"),
            nvidia_model=nvidia_model,
            nvidia_thinking=_boolean(source.get("NVIDIA_THINKING", "false"), name="NVIDIA_THINKING"),
            nvidia_reasoning_effort=_choice(
                source.get("NVIDIA_REASONING_EFFORT", "low"),
                name="NVIDIA_REASONING_EFFORT",
                allowed={"low", "medium", "high"},
            ),
            nvidia_max_tokens=_positive_int(
                source.get("NVIDIA_MAX_TOKENS", nvidia_max_tokens_default),
                name="NVIDIA_MAX_TOKENS",
            ),
            openrouter_api_key=_empty_to_none(source.get("OPENROUTER_API_KEY")),
            openrouter_base_url=source.get("OPENROUTER_BASE_URL", DEFAULT_OPENROUTER_BASE_URL).rstrip("/"),
            openrouter_model=source.get("OPENROUTER_MODEL", DEFAULT_OPENROUTER_MODEL),
            openrouter_max_tokens=_positive_int(
                source.get("OPENROUTER_MAX_TOKENS", "8192"),
                name="OPENROUTER_MAX_TOKENS",
            ),
            chat2dify_public_base_path=normalize_public_base_path(
                source.get("CHAT2DIFY_PUBLIC_BASE_PATH", "")
            ),
            task_db_path=resolve_path_from_project_root(
                source.get("CHAT2DIFY_TASK_DB", "data/tasks.sqlite3"),
                root,
            ),
            task_workers=_positive_int(
                source.get("CHAT2DIFY_TASK_WORKERS", "2"),
                name="CHAT2DIFY_TASK_WORKERS",
            ),
        )

    def workflow_url(self, app_id: str) -> str:
        return f"{self.dify_console_web_base}/app/{app_id}/workflow"

    def app_url(self, app_id: str, mode: str | None = None) -> str:
        if mode in {"chat", "agent-chat", "completion"}:
            return f"{self.dify_console_web_base}/app/{app_id}/configuration"
        return self.workflow_url(app_id)

    def planner_runtime(self) -> PlannerRuntime:
        return self._planner_runtime_for_provider(_normalize_planner_provider(self.planner_default_provider))

    def planner_runtime_candidates(self) -> list[PlannerRuntime]:
        providers = [self.planner_default_provider, *self.planner_fallback_providers]
        seen: set[str] = set()
        runtimes: list[PlannerRuntime] = []
        for provider in providers:
            normalized = _normalize_planner_provider(provider)
            if not normalized or normalized in seen:
                continue
            seen.add(normalized)
            runtimes.append(self._planner_runtime_for_provider(normalized))
        return runtimes

    def _planner_runtime_for_provider(self, provider: str) -> PlannerRuntime:
        if provider == "openai":
            return PlannerRuntime(
                provider="openai",
                label="OpenAI",
                api_key=self.openai_api_key,
                base_url=self.openai_base_url,
                model=self.openai_model,
                timeout_seconds=self.planner_timeout_seconds,
                request_retries=self.planner_request_retries,
            )
        if provider == "openai-compatible":
            return PlannerRuntime(
                provider="openai-compatible",
                label=self.openai_compatible_label,
                api_key=self.openai_compatible_api_key,
                base_url=self.openai_compatible_base_url,
                model=self.openai_compatible_model,
                timeout_seconds=self.planner_timeout_seconds,
                request_retries=self.planner_request_retries,
            )
        if provider == "nvidia":
            return PlannerRuntime(
                provider="nvidia",
                label="NVIDIA NIM",
                api_key=self.nvidia_api_key,
                base_url=self.nvidia_base_url,
                model=self.nvidia_model,
                timeout_seconds=self.planner_timeout_seconds,
                request_retries=self.planner_request_retries,
            )
        if provider == "openrouter":
            return PlannerRuntime(
                provider="openrouter",
                label="OpenRouter",
                api_key=self.openrouter_api_key,
                base_url=self.openrouter_base_url,
                model=self.openrouter_model,
                timeout_seconds=self.planner_timeout_seconds,
                request_retries=self.planner_request_retries,
            )
        raise ConfigurationError(f"Unsupported planner provider: {provider}")

    def with_planner(self, provider: str | None, model: str | None = None) -> "Settings":
        selected_provider = _normalize_planner_provider(provider or self.planner_default_provider)
        if selected_provider == "openai":
            return replace(
                self,
                planner_default_provider="openai",
                openai_model=(model or self.openai_model).strip(),
            )
        if selected_provider == "openai-compatible":
            return replace(
                self,
                planner_default_provider="openai-compatible",
                openai_compatible_model=(model or self.openai_compatible_model).strip(),
            )
        if selected_provider == "nvidia":
            selected_model = (model or self.nvidia_model).strip()
            if selected_model not in NVIDIA_PLANNER_MODELS and selected_model != self.nvidia_model:
                raise ConfigurationError(f"Unsupported NVIDIA planner model: {selected_model}")
            nvidia_max_tokens = self.nvidia_max_tokens
            if self.nvidia_model in NVIDIA_PLANNER_MODELS and selected_model in NVIDIA_PLANNER_MODELS:
                current_default = _nvidia_model_max_tokens(self.nvidia_model)
                if self.nvidia_max_tokens == current_default:
                    nvidia_max_tokens = _nvidia_model_max_tokens(selected_model)
            return replace(
                self,
                planner_default_provider="nvidia",
                nvidia_model=selected_model,
                nvidia_max_tokens=nvidia_max_tokens,
            )
        if selected_provider == "openrouter":
            selected_model = (model or self.openrouter_model).strip()
            if selected_model != self.openrouter_model:
                raise ConfigurationError(f"Unsupported OpenRouter planner model: {selected_model}")
            return replace(self, planner_default_provider="openrouter", openrouter_model=selected_model)
        raise ConfigurationError(f"Unsupported planner provider: {selected_provider}")

    def planner_catalog(self) -> list[dict[str, object]]:
        return [
            {
                "id": "openai",
                "label": "OpenAI",
                "configured": bool(self.openai_api_key),
                "models": [{"id": self.openai_model, "label": self.openai_model}],
            },
            {
                "id": "openai-compatible",
                "label": self.openai_compatible_label,
                "configured": bool(self.openai_compatible_api_key),
                "models": [
                    {
                        "id": self.openai_compatible_model,
                        "label": self.openai_compatible_model,
                    }
                ],
            },
            {
                "id": "nvidia",
                "label": "NVIDIA NIM",
                "configured": bool(self.nvidia_api_key),
                "models": _nvidia_catalog_models(self.nvidia_model),
            },
            {
                "id": "openrouter",
                "label": "OpenRouter",
                "configured": bool(self.openrouter_api_key),
                "models": [{"id": self.openrouter_model, "label": "Nemotron 3 Ultra 550B (free)"}],
            },
        ]


def load_settings(*, validate_dify: bool = True) -> Settings:
    return Settings.from_env(validate_dify=validate_dify)


def load_public_base_path(*, project_root: Path | None = None) -> str:
    root = (project_root or PROJECT_ROOT).resolve()
    return normalize_public_base_path(_load_environment(root).get("CHAT2DIFY_PUBLIC_BASE_PATH", ""))


def normalize_public_base_path(value: str | None) -> str:
    if not value:
        return ""
    normalized = value.strip()
    if normalized in {"", "/"}:
        return ""
    if "://" in normalized:
        raise ConfigurationError("CHAT2DIFY_PUBLIC_BASE_PATH must be a URL path, not a full URL.")
    if not normalized.startswith("/"):
        normalized = f"/{normalized}"
    return normalized.rstrip("/")


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


def _positive_float(value: str, *, name: str) -> float:
    try:
        parsed = float(value)
    except ValueError as exc:
        raise ConfigurationError(f"{name} must be a number.") from exc
    if parsed <= 0:
        raise ConfigurationError(f"{name} must be greater than zero.")
    return parsed


def _non_negative_int(value: str, *, name: str) -> int:
    try:
        parsed = int(value)
    except ValueError as exc:
        raise ConfigurationError(f"{name} must be an integer.") from exc
    if parsed < 0:
        raise ConfigurationError(f"{name} must be zero or greater.")
    return parsed


def _positive_int(value: str, *, name: str) -> int:
    parsed = _non_negative_int(value, name=name)
    if parsed == 0:
        raise ConfigurationError(f"{name} must be greater than zero.")
    return parsed


def _boolean(value: str, *, name: str) -> bool:
    normalized = value.strip().lower()
    if normalized in {"1", "true", "yes", "on"}:
        return True
    if normalized in {"0", "false", "no", "off"}:
        return False
    raise ConfigurationError(f"{name} must be true or false.")


def _choice(value: str, *, name: str, allowed: set[str]) -> str:
    normalized = value.strip().lower()
    if normalized not in allowed:
        choices = ", ".join(sorted(allowed))
        raise ConfigurationError(f"{name} must be one of: {choices}.")
    return normalized


def _normalize_planner_provider(value: str | None) -> str:
    if value is None:
        return ""
    normalized = value.strip().lower().replace("_", "-")
    aliases = {
        "openai-compatible": "openai-compatible",
        "openai-compat": "openai-compatible",
        "compatible-openai": "openai-compatible",
        "compatible": "openai-compatible",
    }
    return aliases.get(normalized, normalized)


def _nvidia_model_max_tokens(model: str) -> int:
    entry = NVIDIA_PLANNER_MODELS.get(model)
    if entry is None:
        return NVIDIA_PLANNER_MODELS[DEFAULT_NVIDIA_MODEL]["max_tokens"]
    return int(entry["max_tokens"])


def _nvidia_catalog_models(selected_model: str) -> list[dict[str, str]]:
    models = [
        {"id": model, "label": str(config["label"])}
        for model, config in NVIDIA_PLANNER_MODELS.items()
    ]
    if selected_model and selected_model not in NVIDIA_PLANNER_MODELS:
        models.append({"id": selected_model, "label": selected_model})
    return models


def _csv_list(value: str | None) -> list[str]:
    if not value:
        return []
    return [item.strip() for item in value.split(",") if item.strip()]


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
