from pathlib import Path

import pytest

from backend.config import Settings, get_settings


def test_data_dir_fixture_drives_settings_from_env(data_dir: Path) -> None:
    settings = get_settings()

    assert settings.data_dir == data_dir.resolve()
    assert settings.db_path == data_dir.resolve() / "docter.db"
    assert settings.index_dir == data_dir.resolve() / "index"
    assert settings.worktrees_dir == data_dir.resolve() / "worktrees"
    assert settings.logs_dir == data_dir.resolve() / "logs"


def test_data_dir_defaults_to_home(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.delenv("DOCTER_DATA_DIR", raising=False)
    monkeypatch.setenv("HOME", str(tmp_path))

    assert Settings().data_dir == (tmp_path / ".docter").resolve()


def test_log_level_from_env_is_case_insensitive(
    data_dir: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("DOCTER_LOG_LEVEL", "debug")

    assert Settings().log_level == "DEBUG"


def test_ensure_dirs_creates_all_directories(settings: Settings) -> None:
    settings.ensure_dirs()

    for directory in (settings.index_dir, settings.worktrees_dir, settings.logs_dir):
        assert directory.is_dir()
