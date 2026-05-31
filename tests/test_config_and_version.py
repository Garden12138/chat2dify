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


def test_dsl_version_is_read_from_source(tmp_path: Path) -> None:
    version_dir = tmp_path / "api" / "constants"
    version_dir.mkdir(parents=True)
    (version_dir / "dsl_version.py").write_text('CURRENT_APP_DSL_VERSION = "0.7.1"\n', encoding="utf-8")

    assert read_app_dsl_version(tmp_path) == "0.7.1"


def test_missing_dify_source_dir_fails(tmp_path: Path) -> None:
    with pytest.raises(ValueError):
        Settings.from_env({"DIFY_SOURCE_DIR": "../dify"}, project_root=tmp_path / "chat2dify")
