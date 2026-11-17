"""Application settings loaded from the environment (prefix ``DOCTER_``)."""

import shlex
from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

LogLevel = Literal["CRITICAL", "ERROR", "WARNING", "INFO", "DEBUG"]

_REPO_ROOT = Path(__file__).resolve().parent.parent


def _default_data_dir() -> Path:
    return Path.home() / ".docter"


class Settings(BaseSettings):
    """Runtime configuration for the Docter backend."""

    model_config = SettingsConfigDict(env_prefix="DOCTER_", extra="ignore")

    data_dir: Path = Field(default_factory=_default_data_dir)
    host: str = "127.0.0.1"
    port: int = 8000
    log_level: LogLevel = "INFO"
    cors_origins: list[str] = ["http://localhost:5173"]
    frontend_dist_dir: Path = _REPO_ROOT / "frontend" / "dist"
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
