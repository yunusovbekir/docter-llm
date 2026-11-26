from pathlib import Path

import pytest
from pydantic import ValidationError

from backend.config import Settings, get_settings


def test_data_dir_fixture_drives_settings_from_env(data_dir: Path) -> None:
    settings = get_settings()

    assert settings.data_dir == data_dir.resolve()
    assert settings.db_path == data_dir.resolve() / "docter.db"
    assert settings.database_url == f"sqlite+aiosqlite:///{settings.db_path}"
    assert settings.index_dir == data_dir.resolve() / "index"
    assert settings.worktrees_dir == data_dir.resolve() / "worktrees"
    assert settings.logs_dir == data_dir.resolve() / "logs"


def test_database_url_from_env_is_not_dumped(monkeypatch: pytest.MonkeyPatch) -> None:
    url = "postgresql+psycopg://test-user:dummy-password@localhost/docter"
    monkeypatch.setenv("DOCTER_DATABASE_URL", url)

    settings = Settings()

    assert settings.database_url == url
    assert "dummy-password" not in repr(settings)
    assert "database_url" not in settings.model_dump()


@pytest.mark.parametrize(
    "url",
    ["", "not-a-url-with-dummy-password", "postgresql://user:dummy-password@localhost/db"],
)
def test_rejects_invalid_or_unsupported_database_urls(
    monkeypatch: pytest.MonkeyPatch, url: str
) -> None:
    monkeypatch.setenv("DOCTER_DATABASE_URL", url)

    with pytest.raises(ValidationError, match="database_url") as caught:
        Settings()

    assert "dummy-password" not in str(caught.value)


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


@pytest.mark.parametrize(
    ("setting", "value"),
    [("index_metadata_provider", "lancedb"), ("index_vector_provider", "sqlite")],
)
def test_rejects_unsupported_index_providers(
    monkeypatch: pytest.MonkeyPatch, setting: str, value: str
) -> None:
    monkeypatch.setenv(f"DOCTER_{setting.upper()}", value)

    with pytest.raises(ValidationError, match=setting):
        Settings()


def test_postgres_metadata_url_is_separate_and_forwarded_only_for_postgres(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    app_url = "sqlite+aiosqlite:///app.db"
    index_url = "postgresql://docter:test-index-password@localhost/index"
    monkeypatch.setenv("DOCTER_DATABASE_URL", app_url)
    monkeypatch.setenv("DOCTER_INDEX_METADATA_URL", index_url)
    assert "DOCTER_INDEX_METADATA_URL" not in Settings().mcp_env

    monkeypatch.setenv("DOCTER_INDEX_METADATA_PROVIDER", "postgres")
    settings = Settings()
    assert settings.database_url == app_url
    assert settings.mcp_env == {
        "DOCTER_INDEX_METADATA_PROVIDER": "postgres",
        "DOCTER_INDEX_VECTOR_PROVIDER": "lancedb",
        "DOCTER_INDEX_METADATA_URL": index_url,
    }
    assert "test-index-password" not in repr(settings)
    assert "index_metadata_url" not in settings.model_dump()

    monkeypatch.delenv("DOCTER_INDEX_METADATA_URL")
    with pytest.raises(ValidationError, match="requires DOCTER_INDEX_METADATA_URL"):
        Settings()


def test_pgvector_url_is_independent_and_redacted(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DOCTER_DATABASE_URL", "postgresql+psycopg://localhost/app")
    monkeypatch.setenv("DOCTER_INDEX_METADATA_URL", "postgresql://localhost/metadata")
    monkeypatch.setenv("DOCTER_INDEX_VECTOR_PROVIDER", "pgvector")
    with pytest.raises(ValidationError, match="requires DOCTER_INDEX_VECTOR_URL"):
        Settings()

    # An invalid scheme must not leak the URL either.
    secret = "test-vector-password"
    monkeypatch.setenv("DOCTER_INDEX_VECTOR_URL", f"https://role:{secret}@localhost/vectors")
    with pytest.raises(ValidationError, match="requires DOCTER_INDEX_VECTOR_URL") as caught:
        Settings()
    assert secret not in str(caught.value)

    url = f"postgresql://role:{secret}@localhost/vectors"
    monkeypatch.setenv("DOCTER_INDEX_VECTOR_URL", url)
    settings = Settings()
    assert settings.mcp_env == {
        "DOCTER_INDEX_METADATA_PROVIDER": "sqlite",
        "DOCTER_INDEX_VECTOR_PROVIDER": "pgvector",
        "DOCTER_INDEX_VECTOR_URL": url,
    }
    assert secret not in repr(settings)
    assert "index_vector_url" not in settings.model_dump()

    monkeypatch.setenv("DOCTER_INDEX_VECTOR_PROVIDER", "lancedb")
    assert "DOCTER_INDEX_VECTOR_URL" not in Settings().mcp_env


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
