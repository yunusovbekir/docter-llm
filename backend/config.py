"""Application settings loaded from the environment (prefix ``DOCTER_``)."""

import shlex
from functools import lru_cache
from pathlib import Path
from typing import Any, ClassVar, Literal

import anyio
from pydantic import AnyHttpUrl, Field, TypeAdapter, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict
from sqlalchemy.engine import make_url
from sqlalchemy.exc import ArgumentError

LogLevel = Literal["CRITICAL", "ERROR", "WARNING", "INFO", "DEBUG"]


def _default_data_dir() -> Path:
    return Path.home() / ".docter"


def _default_database_url(data: dict[str, Any]) -> str:
    return f"sqlite+aiosqlite:///{Path(data['data_dir']) / 'docter.db'}"


class Settings(BaseSettings):
    """Runtime configuration for the Docter backend."""

    model_config = SettingsConfigDict(
        env_prefix="DOCTER_", extra="ignore", hide_input_in_errors=True
    )

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
    LLM_RETRIES: ClassVar[int] = 2
    LLM_RETRY_BACKOFF_S: ClassVar[float] = 1.0
    INDEX_TIMEOUT_S: ClassVar[float] = 3600.0
    CHAT_EVENT_BUFFER_SIZE: ClassVar[int] = 100
    REQUEST_ID_PATTERN: ClassVar[str] = r"[A-Za-z0-9._-]{1,128}"
    SHUTDOWN_TIMEOUT_S: ClassVar[float] = 10.0
    GIT_RATIONALE_LENGTH: ClassVar[int] = 72
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
    database_url: str = Field(default_factory=_default_database_url, repr=False, exclude=True)
    host: str = "127.0.0.1"
    port: int = 8000
    log_level: LogLevel = "INFO"
    cors_origins: list[str] = ["http://localhost:5173"]
    frontend_dist_dir: Path = Path(__file__).resolve().parent.parent / "frontend" / "dist"
    mcp_command: str = "uv run docter-mcp"
    index_metadata_provider: Literal["sqlite"] = "sqlite"
    index_vector_provider: Literal["lancedb"] = "lancedb"

    @field_validator("data_dir", "frontend_dist_dir", mode="after")
    @classmethod
    def _expand_path(cls, value: Path) -> Path:
        return value.expanduser().resolve()

    @field_validator("database_url")
    @classmethod
    def _check_database_url(cls, value: str) -> str:
        try:
            driver = make_url(value).drivername
        except (ArgumentError, ValueError):
            raise ValueError("must be a valid async SQLAlchemy database URL") from None
        if driver not in {"sqlite+aiosqlite", "postgresql+psycopg"}:
            raise ValueError("must use sqlite+aiosqlite or postgresql+psycopg")
        return value

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
    def mcp_env(self) -> dict[str, str]:
        """Additional environment variables explicitly forwarded to the MCP process."""
        return {
            "DOCTER_INDEX_METADATA_PROVIDER": self.index_metadata_provider,
            "DOCTER_INDEX_VECTOR_PROVIDER": self.index_vector_provider,
        }

    @property
    def db_path(self) -> Path:
        """Path of the default SQLite database file."""
        return self.data_dir / "docter.db"

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
