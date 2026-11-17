"""Environment configuration for the Docter MCP server."""

import os
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True, slots=True)
class Config:
    """Server settings; construction does not create directories or write files."""

    index_dir: Path = Path.home() / ".docter" / "index"
    allowed_roots: tuple[Path, ...] = ()
    allow_exec: bool = False

    @classmethod
    def from_env(cls, env: Mapping[str, str] | None = None) -> "Config":
        """Load DOCTER_* settings, rejecting malformed booleans and roots."""
        values = os.environ if env is None else env
        flag = values.get("DOCTER_ALLOW_EXEC", "false").strip().lower()
        if flag not in {"true", "false", "1", "0"}:
            raise ValueError("DOCTER_ALLOW_EXEC must be true, false, 1, or 0")
        roots_text = values.get("DOCTER_ALLOWED_ROOTS", "").strip()
        roots: list[Path] = []
        if roots_text:
            for item in roots_text.split(","):
                root = Path(item.strip()).expanduser()
                if not item.strip() or not root.is_absolute():
                    raise ValueError("DOCTER_ALLOWED_ROOTS must contain absolute paths")
                roots.append(root.resolve())
        index = values.get("DOCTER_INDEX_DIR", "").strip()
        return cls(
            index_dir=Path(index).expanduser().resolve() if index else cls().index_dir,
            allowed_roots=tuple(roots),
            allow_exec=flag in {"true", "1"},
        )
