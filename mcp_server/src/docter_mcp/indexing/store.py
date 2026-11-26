"""Publish index metadata only after staging an immutable vector generation."""

import hashlib
from types import TracebackType
from uuid import uuid4

from docter_contract import ErrorCode, GetSymbolOutput
from docter_mcp.config import Config
from docter_mcp.indexing.metadata import IndexGeneration, IndexMetadata, SQLiteIndexMetadata
from docter_mcp.indexing.vectors import LanceVectorStore, VectorChunk, VectorStore
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
        self.vectors: VectorStore = LanceVectorStore(self.path / "vectors")

    def __enter__(self) -> "IndexStore":
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        self.metadata.close()

    def publish(
        self,
        hashes: dict[str, str],
        symbols: list[GetSymbolOutput],
        chunks: list[VectorChunk],
        model: str,
    ) -> None:
        """Write vectors first, then atomically commit their manifest and symbol metadata."""
        generation = f"chunks_{uuid4().hex}" if chunks else ""
        published = False
        try:
            self.vectors.stage(generation, chunks)
            self.metadata.publish(IndexGeneration(generation, model, self.VERSION), hashes, symbols)
            published = True
        finally:
            if generation and not published:
                self.vectors.discard(generation)
