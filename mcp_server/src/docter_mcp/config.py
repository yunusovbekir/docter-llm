"""Environment configuration for the Docter MCP server."""

import os
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import ClassVar

from docter_contract import ToolName


@dataclass(frozen=True, slots=True)
class Config:
    """Server settings; construction does not create directories or write files."""

    TOOL_NAMES: ClassVar[tuple[ToolName, ...]] = (
        ToolName.GET_SERVER_INFO,
        ToolName.INDEX_REPO,
        ToolName.SEARCH_CODE,
        ToolName.GET_SYMBOL,
        ToolName.READ_FILE,
        ToolName.LIST_DOC_FILES,
        ToolName.FIND_DOC_GAPS,
        ToolName.CHECK_DOC_DRIFT,
        ToolName.RUN_DOC_EXAMPLES,
        ToolName.PROPOSE_PATCH,
        ToolName.TRANSLATE_FILE,
    )
    EXAMPLE_TIMEOUT: ClassVar[int] = 20
    TRANSLATION_MAX_TOKENS: ClassVar[int] = 4096
    DEFAULT_STYLE_GUIDE: ClassVar[str] = """# Documentation style

Use the project's existing docstring convention and terminology.
Describe public parameters, return values, and directly raised exceptions.
Keep examples concise and accurate. Do not invent behavior or reference missing symbols.
"""

    index_dir: Path = Path.home() / ".docter" / "index"
    embedding_model: str = "BAAI/bge-small-en-v1.5"
    allowed_roots: tuple[Path, ...] = ()
    allow_exec: bool = False
    index_metadata_provider: str = "sqlite"
    index_vector_provider: str = "lancedb"
    index_metadata_url: str | None = field(default=None, repr=False)

    def __post_init__(self) -> None:
        if self.index_metadata_provider not in {"sqlite", "postgres"}:
            raise ValueError(
                f"Unsupported DOCTER_INDEX_METADATA_PROVIDER {self.index_metadata_provider!r}; "
                "expected 'sqlite' or 'postgres'."
            )
        if self.index_metadata_provider == "postgres" and (
            not self.index_metadata_url
            or not self.index_metadata_url.startswith(("postgresql://", "postgres://"))
        ):
            raise ValueError(
                "The postgres metadata provider requires DOCTER_INDEX_METADATA_URL "
                "with a postgresql:// or postgres:// scheme."
            )
        if self.index_vector_provider != "lancedb":
            raise ValueError(
                f"Unsupported DOCTER_INDEX_VECTOR_PROVIDER {self.index_vector_provider!r}; "
                "expected 'lancedb'."
            )

    @classmethod
    def from_env(cls, env: Mapping[str, str] | None = None) -> "Config":
        """Load environment settings and reject invalid values."""
        values = os.environ if env is None else env
        defaults = cls()
        flag = (
            values.get("DOCSMITH_ALLOW_EXEC", values.get("DOCTER_ALLOW_EXEC", "false"))
            .strip()
            .lower()
        )
        if flag not in {"true", "false", "1", "0"}:
            raise ValueError("DOCSMITH_ALLOW_EXEC/DOCTER_ALLOW_EXEC must be true, false, 1, or 0")
        roots_text = values.get("DOCTER_ALLOWED_ROOTS", "").strip()
        roots: list[Path] = []
        if roots_text:
            for item in roots_text.split(","):
                root = Path(item.strip()).expanduser()
                if not item.strip() or not root.is_absolute():
                    raise ValueError("DOCTER_ALLOWED_ROOTS must contain absolute paths")
                roots.append(root.resolve())
        index = values.get("DOCTER_INDEX_DIR", values.get("DOCSMITH_INDEX_DIR", "")).strip()
        model = values.get("DOCTER_EMBEDDING_MODEL", defaults.embedding_model).strip()
        if not model:
            raise ValueError("DOCTER_EMBEDDING_MODEL must not be empty")
        return cls(
            index_dir=Path(index).expanduser().resolve() if index else defaults.index_dir,
            embedding_model=model,
            allowed_roots=tuple(roots),
            allow_exec=flag in {"true", "1"},
            index_metadata_provider=values.get(
                "DOCTER_INDEX_METADATA_PROVIDER", defaults.index_metadata_provider
            ),
            index_vector_provider=values.get(
                "DOCTER_INDEX_VECTOR_PROVIDER", defaults.index_vector_provider
            ),
            index_metadata_url=values.get("DOCTER_INDEX_METADATA_URL"),
        )
