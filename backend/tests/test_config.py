from pathlib import Path

import pytest
from pydantic import ValidationError

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


def test_mcp_command_defaults_to_uv_run(data_dir: Path) -> None:
    assert Settings().mcp_command_args == ["uv", "run", "docter-mcp"]


def test_mcp_command_from_env_is_split_shell_style(
    data_dir: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("DOCTER_MCP_COMMAND", "'/opt/my tools/python' -m docter_mcp --verbose")

    assert Settings().mcp_command_args == ["/opt/my tools/python", "-m", "docter_mcp", "--verbose"]


@pytest.mark.parametrize("command", ["", "   ", "'unclosed"])
def test_mcp_command_rejects_invalid_values(data_dir: Path, command: str) -> None:
    with pytest.raises(ValidationError):
        Settings(mcp_command=command)


def test_fixed_configuration_is_not_loaded_from_environment(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("DOCTER_DEFAULT_MODEL", "not-a-runtime-setting")
    monkeypatch.setenv("DOCTER_CONNECTION_TEST_TIMEOUT_S", "0.01")

    settings = Settings()

    assert settings.DEFAULT_MODEL == "gpt-4o-mini"
    assert settings.CONNECTION_TEST_TIMEOUT_S == 15.0
    assert not any(name.isupper() for name in Settings.model_fields)
    assert "DEFAULT_MODEL" not in settings.model_dump()
