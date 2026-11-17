"""SQLite manifests and immutable LanceDB generations, published together."""

import hashlib
import sqlite3
from types import TracebackType
from typing import Any
from uuid import uuid4

import lancedb
from lancedb.table import Table

from docter_contract import ErrorCode, GetSymbolOutput
from docter_mcp.config import Config
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
        self.sql = sqlite3.connect(database, check_same_thread=False)
        self.sql.executescript(
            """
            CREATE TABLE IF NOT EXISTS metadata (key TEXT PRIMARY KEY, value TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS files (path TEXT PRIMARY KEY, hash TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS symbols (
                id TEXT PRIMARY KEY, file TEXT NOT NULL, qualname TEXT NOT NULL,
                kind TEXT NOT NULL, payload TEXT NOT NULL
            );
            CREATE INDEX IF NOT EXISTS symbol_names ON symbols(qualname);
            """
        )
        self.metadata = dict(self.sql.execute("SELECT key, value FROM metadata"))
        if not create and "generation" not in self.metadata:
            self.sql.close()
            raise fail(ErrorCode.NOT_INDEXED, "No completed index exists; run index_repo.")

    def __enter__(self) -> "IndexStore":
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        self.sql.close()

    def table(self) -> Table | None:
        """Open the published vectors; a successfully indexed empty repo has no table."""
        name = self.metadata.get("generation", "")
        if not name:
            return None
        return lancedb.connect(self.path / "vectors").open_table(name)

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
        try:
            with self.sql:
                self.sql.execute("DELETE FROM files")
                self.sql.execute("DELETE FROM symbols")
                self.sql.executemany("INSERT INTO files VALUES (?, ?)", hashes.items())
                self.sql.executemany(
                    "INSERT INTO symbols VALUES (?, ?, ?, ?, ?)",
                    [
                        (
                            f"{symbol.file}:{symbol.qualname}",
                            symbol.file,
                            symbol.qualname,
                            symbol.kind.value,
                            symbol.model_dump_json(),
                        )
                        for symbol in symbols
                    ],
                )
                self.sql.executemany(
                    "INSERT OR REPLACE INTO metadata VALUES (?, ?)",
                    [("generation", generation), ("model", model), ("version", self.VERSION)],
                )
        except sqlite3.Error:
            if generation:
                connection.drop_table(generation)
            raise
        # Published generations are retained so concurrent readers can finish safely.
        self.metadata = {"generation": generation, "model": model, "version": self.VERSION}
