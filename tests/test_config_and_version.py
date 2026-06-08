from pathlib import Path

import pytest

from app.config import Settings
from app.dify.version import read_app_dsl_version


def test_relative_dify_source_dir_resolves_from_project_root(tmp_path: Path) -> None:
    project_root = tmp_path / "chat2dify"
    dify_root = tmp_path / "dify"
    version_dir = dify_root / "api" / "constants"
    version_dir.mkdir(parents=True)
    (version_dir / "dsl_version.py").write_text('CURRENT_APP_DSL_VERSION = "9.9.9"\n', encoding="utf-8")
    project_root.mkdir()

    settings = Settings.from_env({"DIFY_SOURCE_DIR": "../dify"}, project_root=project_root)

    assert settings.dify_source_path == dify_root.resolve()


def test_dotenv_is_loaded_relative_to_project_root(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    project_root = tmp_path / "chat2dify"
    dify_root = tmp_path / "dify"
    version_dir = dify_root / "api" / "constants"
    version_dir.mkdir(parents=True)
    (version_dir / "dsl_version.py").write_text('CURRENT_APP_DSL_VERSION = "9.9.9"\n', encoding="utf-8")
    project_root.mkdir()
    (project_root / ".env").write_text(
        "DIFY_SOURCE_DIR=../dify\nDIFY_CONSOLE_WEB_BASE=http://dify.example\n",
        encoding="utf-8",
    )
    monkeypatch.delenv("DIFY_SOURCE_DIR", raising=False)
    monkeypatch.delenv("DIFY_CONSOLE_WEB_BASE", raising=False)

    settings = Settings.from_env(project_root=project_root)

    assert settings.dify_source_path == dify_root.resolve()
    assert settings.dify_console_web_base == "http://dify.example"


def test_default_dataset_ids_are_parsed_from_env(tmp_path: Path) -> None:
    project_root = tmp_path / "chat2dify"
    dify_root = tmp_path / "dify"
    version_dir = dify_root / "api" / "constants"
    version_dir.mkdir(parents=True)
    (version_dir / "dsl_version.py").write_text('CURRENT_APP_DSL_VERSION = "9.9.9"\n', encoding="utf-8")
    project_root.mkdir()

    settings = Settings.from_env(
        {"DIFY_SOURCE_DIR": "../dify", "DIFY_DEFAULT_DATASET_IDS": "dataset-a, dataset-b,,"},
        project_root=project_root,
    )

    assert settings.dify_default_dataset_ids == ["dataset-a", "dataset-b"]


def test_background_task_settings_resolve_from_project_root(tmp_path: Path) -> None:
    settings = Settings.from_env(
        {
            "DIFY_SOURCE_DIR": "../dify",
            "CHAT2DIFY_TASK_DB": "runtime/workflow-tasks.sqlite3",
            "CHAT2DIFY_TASK_WORKERS": "3",
        },
        project_root=tmp_path,
        validate_dify=False,
    )

    assert settings.task_db_path == (tmp_path / "runtime" / "workflow-tasks.sqlite3").resolve()
    assert settings.task_workers == 3


def test_nvidia_planner_configuration_and_catalog(tmp_path: Path) -> None:
    settings = Settings.from_env(
        {
            "DIFY_SOURCE_DIR": "../dify",
            "PLANNER_DEFAULT_PROVIDER": "nvidia",
            "PLANNER_TIMEOUT_SECONDS": "240",
            "PLANNER_REQUEST_RETRIES": "4",
            "NVIDIA_API_KEY": "nvapi-test",
            "NVIDIA_THINKING": "true",
            "NVIDIA_REASONING_EFFORT": "medium",
            "NVIDIA_MAX_TOKENS": "4096",
        },
        project_root=tmp_path,
        validate_dify=False,
    )

    runtime = settings.planner_runtime()
    catalog = settings.planner_catalog()

    assert runtime.provider == "nvidia"
    assert runtime.base_url == "https://integrate.api.nvidia.com/v1"
    assert runtime.model == "deepseek-ai/deepseek-v4-flash"
    assert runtime.timeout_seconds == 240
    assert runtime.request_retries == 4
    assert runtime.configured is True
    assert settings.nvidia_thinking is True
    assert settings.nvidia_reasoning_effort == "medium"
    assert settings.nvidia_max_tokens == 4096
    assert next(item for item in catalog if item["id"] == "nvidia") == {
        "id": "nvidia",
        "label": "NVIDIA NIM",
        "configured": True,
        "models": [{"id": "deepseek-ai/deepseek-v4-flash", "label": "DeepSeek V4 Flash"}],
    }
    assert "nvapi-test" not in str(catalog)


def test_planner_timeout_must_be_positive(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="PLANNER_TIMEOUT_SECONDS must be greater than zero"):
        Settings.from_env(
            {"DIFY_SOURCE_DIR": "../dify", "PLANNER_TIMEOUT_SECONDS": "0"},
            project_root=tmp_path,
            validate_dify=False,
        )


def test_planner_request_retries_must_not_be_negative(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="PLANNER_REQUEST_RETRIES must be zero or greater"):
        Settings.from_env(
            {"DIFY_SOURCE_DIR": "../dify", "PLANNER_REQUEST_RETRIES": "-1"},
            project_root=tmp_path,
            validate_dify=False,
        )


def test_nvidia_thinking_must_be_boolean(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="NVIDIA_THINKING must be true or false"):
        Settings.from_env(
            {"DIFY_SOURCE_DIR": "../dify", "NVIDIA_THINKING": "sometimes"},
            project_root=tmp_path,
            validate_dify=False,
        )


def test_with_planner_rejects_unknown_nvidia_model(tmp_path: Path) -> None:
    settings = Settings.from_env({"DIFY_SOURCE_DIR": "../dify"}, project_root=tmp_path, validate_dify=False)

    with pytest.raises(ValueError, match="Unsupported NVIDIA planner model"):
        settings.with_planner("nvidia", "made-up-model")


def test_dsl_version_is_read_from_source(tmp_path: Path) -> None:
    version_dir = tmp_path / "api" / "constants"
    version_dir.mkdir(parents=True)
    (version_dir / "dsl_version.py").write_text('CURRENT_APP_DSL_VERSION = "0.7.1"\n', encoding="utf-8")

    assert read_app_dsl_version(tmp_path) == "0.7.1"


def test_missing_dify_source_dir_fails(tmp_path: Path) -> None:
    with pytest.raises(ValueError):
        Settings.from_env({"DIFY_SOURCE_DIR": "../dify"}, project_root=tmp_path / "chat2dify")
