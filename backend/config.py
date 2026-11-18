"""Application settings loaded from the environment (prefix ``DOCTER_``)."""

import shlex
from functools import lru_cache
from pathlib import Path
from typing import ClassVar, Literal

import anyio
from pydantic import AnyHttpUrl, Field, TypeAdapter, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

LogLevel = Literal["CRITICAL", "ERROR", "WARNING", "INFO", "DEBUG"]


def _default_data_dir() -> Path:
    return Path.home() / ".docter"


class Settings(BaseSettings):
    """Runtime configuration for the Docter backend."""

    model_config = SettingsConfigDict(env_prefix="DOCTER_", extra="ignore")

    SETTINGS_ROW_ID: ClassVar[int] = 1
    DEFAULT_PROVIDER: ClassVar[str] = "openai"
    DEFAULT_MODEL: ClassVar[str] = "gpt-4o-mini"
    DEFAULT_EMBEDDING_MODEL: ClassVar[str] = "BAAI/bge-small-en-v1.5"
    DEFAULT_TEMPERATURE: ClassVar[float] = 0.2
    DEFAULT_MAX_AGENT_STEPS: ClassVar[int] = 25
    NON_NULLABLE_SETTINGS_FIELDS: ClassVar[tuple[str, ...]] = (
        "provider",
        "model",
        "embedding_model",
        "temperature",
        "max_agent_steps",
    )
    HTTP_URL: ClassVar[TypeAdapter[AnyHttpUrl]] = TypeAdapter(AnyHttpUrl)
    KEYRING_SERVICE: ClassVar[str] = "docter"
    CONNECTION_TEST_TIMEOUT_S: ClassVar[float] = 15.0
    MAX_ERROR_LENGTH: ClassVar[int] = 500
    DEFAULT_STARTUP_TIMEOUT_S: ClassVar[float] = 30.0
    DEFAULT_REQUEST_TIMEOUT_S: ClassVar[float] = 60.0
    INDEX_TIMEOUT_S: ClassVar[float] = 3600.0
    SHUTDOWN_TIMEOUT_S: ClassVar[float] = 10.0
    CONNECTION_LOST_ERRORS: ClassVar[tuple[type[Exception], ...]] = (
        anyio.ClosedResourceError,
        anyio.BrokenResourceError,
        anyio.EndOfStream,
    )
    LOG_FORMAT: ClassVar[str] = "%(asctime)s %(levelname)-8s %(name)s: %(message)s"
    LOG_FILE_NAME: ClassVar[str] = "docter.log"
    HANDLER_MARKER: ClassVar[str] = "_docter_handler"
    MCP_SERVER_LOG_NAME: ClassVar[str] = "mcp-server.log"
    LOG_MAX_BYTES: ClassVar[int] = 5 * 1024 * 1024
    LOG_BACKUP_COUNT: ClassVar[int] = 3
    DIRTY_WARNING: ClassVar[str] = (
        "The working tree has uncommitted changes. Docter only commits to its own branch, "
        "but you may want to commit or stash these changes first."
    )

    data_dir: Path = Field(default_factory=_default_data_dir)
    host: str = "127.0.0.1"
    port: int = 8000
    log_level: LogLevel = "INFO"
    cors_origins: list[str] = ["http://localhost:5173"]
    frontend_dist_dir: Path = Path(__file__).resolve().parent.parent / "frontend" / "dist"
    mcp_command: str = "uv run docter-mcp"

    @field_validator("data_dir", "frontend_dist_dir", mode="after")
    @classmethod
    def _expand_path(cls, value: Path) -> Path:
        return value.expanduser().resolve()

    @field_validator("log_level", mode="before")
    @classmethod
    def _upper_log_level(cls, value: object) -> object:
        return value.upper() if isinstance(value, str) else value

    @field_validator("mcp_command", mode="after")
    @classmethod
    def _check_mcp_command(cls, value: str) -> str:
        if not shlex.split(value):
            raise ValueError("must not be empty")
        return value

    @property
    def mcp_command_args(self) -> list[str]:
        """The MCP server command split into program and arguments (shell-style quoting)."""
        return shlex.split(self.mcp_command)

    @property
    def db_path(self) -> Path:
        """Path of the SQLite database file."""
        return self.data_dir / "docter.db"

    @property
    def database_url(self) -> str:
        """Async SQLAlchemy URL for the SQLite database."""
        return f"sqlite+aiosqlite:///{self.db_path}"

    @property
    def index_dir(self) -> Path:
        """Directory holding vector indexes."""
        return self.data_dir / "index"

    @property
    def worktrees_dir(self) -> Path:
        """Directory holding git worktrees for sessions."""
        return self.data_dir / "worktrees"

    @property
    def logs_dir(self) -> Path:
        """Directory holding log files."""
        return self.data_dir / "logs"

    def ensure_dirs(self) -> None:
        """Create the data directory and all derived directories if missing."""
        for directory in (self.data_dir, self.index_dir, self.worktrees_dir, self.logs_dir):
            directory.mkdir(parents=True, exist_ok=True)


@lru_cache
def get_settings() -> Settings:
    """Return the process-wide settings instance."""
    return Settings()
