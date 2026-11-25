"""SQLite manifests and immutable LanceDB generations, published together."""

import hashlib
from types import TracebackType
from typing import Any
from uuid import uuid4

import lancedb
from lancedb.table import Table

from docter_contract import ErrorCode, GetSymbolOutput
from docter_mcp.config import Config
from docter_mcp.indexing.metadata import IndexGeneration, IndexMetadata, SQLiteIndexMetadata
from docter_mcp.sandbox import Sandbox, fail


class IndexStore:
    """Keep the previous generation readable until a complete refresh is committed."""

    VERSION = "1"

    def __init__(self, sandbox: Sandbox, config: Config, *, create: bool = False) -> None:
        digest = hashlib.sha1(str(sandbox.root).encode(), usedforsecurity=False).hexdigest()
        base = config.index_dir.expanduser().resolve()
        self.path = (base / digest).resolve()
        for path in (
            base,
            self.path,
            self.path / "symbols.sqlite3",
            self.path / "vectors",
            base / "models",
        ):
            resolved = path.resolve()
            if resolved.is_relative_to(sandbox.root) or not resolved.is_relative_to(base):
                raise fail(
                    ErrorCode.PATH_OUTSIDE_REPO,
                    "Index storage must stay under index_dir and outside the target repo.",
                )
        database = self.path / "symbols.sqlite3"
        if not create and not database.is_file():
            raise fail(ErrorCode.NOT_INDEXED, "Run index_repo for this repository first.")
        if create:
            self.path.mkdir(parents=True, exist_ok=True)
        self.metadata: IndexMetadata = SQLiteIndexMetadata(database)
        if not create and self.metadata.active_generation is None:
            self.metadata.close()
            raise fail(ErrorCode.NOT_INDEXED, "No completed index exists; run index_repo.")

    def __enter__(self) -> "IndexStore":
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        self.metadata.close()

    def table(self) -> Table | None:
        """Open the published vectors; a successfully indexed empty repo has no table."""
        generation = self.metadata.active_generation
        if generation is None or not generation.name:
            return None
        return lancedb.connect(self.path / "vectors").open_table(generation.name)

    def publish(
        self,
        hashes: dict[str, str],
        symbols: list[GetSymbolOutput],
        rows: list[dict[str, Any]],
        model: str,
    ) -> None:
        """Write vectors first, then atomically commit their manifest and symbol metadata."""
        connection = lancedb.connect(self.path / "vectors")
        generation = f"chunks_{uuid4().hex}" if rows else ""
        if generation:
            connection.create_table(generation, rows)
        published = False
        try:
            self.metadata.publish(IndexGeneration(generation, model, self.VERSION), hashes, symbols)
            published = True
        finally:
            if generation and not published:
                connection.drop_table(generation)
