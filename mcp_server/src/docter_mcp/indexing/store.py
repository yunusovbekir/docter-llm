"""Publish index metadata only after staging an immutable vector generation."""

import hashlib
import logging
from types import TracebackType
from uuid import uuid4

from docter_contract import ErrorCode, GetSymbolOutput
from docter_mcp.config import Config
from docter_mcp.indexing.metadata import (
    IndexGeneration,
    MetadataPublishUncertainError,
    create_index_metadata,
)
from docter_mcp.indexing.vectors import VectorChunk, create_vector_store
from docter_mcp.sandbox import Sandbox, fail


class IndexStore:
    """Keep the previous generation readable until a complete refresh is committed."""

    VERSION = "1"

    def __init__(self, sandbox: Sandbox, config: Config, *, create: bool = False) -> None:
        digest = hashlib.sha1(str(sandbox.root).encode(), usedforsecurity=False).hexdigest()
        base = config.index_dir.expanduser().resolve()
        self.path = (base / digest).resolve()
        database = self.path / "symbols.sqlite3"
        sqlite = config.index_metadata_provider == "sqlite"
        paths = [base, self.path, base / "models"]
        if config.index_vector_provider == "lancedb":
            paths.append(self.path / "vectors")
        if sqlite:
            paths.append(database)
        for path in paths:
            resolved = path.resolve()
            if resolved.is_relative_to(sandbox.root) or not resolved.is_relative_to(base):
                raise fail(
                    ErrorCode.PATH_OUTSIDE_REPO,
                    "Index storage must stay under index_dir and outside the target repo.",
                )
        if sqlite and not create and not database.is_file():
            raise fail(ErrorCode.NOT_INDEXED, "Run index_repo for this repository first.")
        if create:
            self.path.mkdir(parents=True, exist_ok=True)
        self.vectors = create_vector_store(
            config.index_vector_provider, self.path / "vectors", url=config.index_vector_url
        )
        self.metadata = create_index_metadata(
            config.index_metadata_provider,
            database,
            url=config.index_metadata_url,
            index_key=hashlib.sha256(str(self.path).encode()).hexdigest(),
            create=create,
        )
        try:
            if not create and self.metadata.active_generation is None:
                raise fail(ErrorCode.NOT_INDEXED, "No completed index exists; run index_repo.")
        except BaseException:
            self.metadata.close()
            raise

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
        try:
            self.vectors.stage(generation, chunks)
            self.metadata.publish(IndexGeneration(generation, model, self.VERSION), hashes, symbols)
        except MetadataPublishUncertainError:
            # A lost commit response cannot tell us whether PostgreSQL points at these vectors.
            raise
        except BaseException:
            if generation:
                try:
                    self.vectors.discard(generation)
                except Exception as cleanup_error:
                    logging.getLogger(__name__).warning(
                        "Could not discard unpublished vector generation %s (%s)",
                        generation,
                        type(cleanup_error).__name__,
                    )
            raise
