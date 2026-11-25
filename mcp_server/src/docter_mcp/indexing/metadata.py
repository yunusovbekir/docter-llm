"""Metadata interface and its local SQLite implementation."""

import sqlite3
from collections.abc import Mapping, Sequence, Set
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

from docter_contract import GetSymbolOutput, SymbolKind


@dataclass(frozen=True, slots=True)
class IndexGeneration:
    """A published generation; an empty name denotes a completed index without vectors."""

    name: str
    model: str | None
    version: str | None


class IndexMetadata(Protocol):
    """Read index metadata and atomically replace it after vectors have been staged."""

    @property
    def active_generation(self) -> IndexGeneration | None: ...

    def file_hashes(self) -> dict[str, str]: ...

    def symbols_for_files(self, paths: Set[str]) -> list[GetSymbolOutput]: ...

    def symbol_names(self) -> set[str]: ...

    def symbol_file(self, qualname: str) -> str | None: ...

    def lexical_candidates(self, query: str, kind: SymbolKind | None, limit: int) -> list[str]: ...

    def publish(
        self,
        generation: IndexGeneration,
        hashes: Mapping[str, str],
        symbols: Sequence[GetSymbolOutput],
    ) -> None: ...

    def close(self) -> None: ...


class SQLiteIndexMetadata:
    """Store metadata in the existing version-1 SQLite layout."""

    def __init__(self, database: Path) -> None:
        self._sql = sqlite3.connect(database, check_same_thread=False)
        try:
            self._sql.executescript(
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
            manifest: dict[str, str] = dict(self._sql.execute("SELECT key, value FROM metadata"))
        except sqlite3.Error:
            self._sql.close()
            raise
        self._active = (
            IndexGeneration(manifest["generation"], manifest.get("model"), manifest.get("version"))
            if "generation" in manifest
            else None
        )

    @property
    def active_generation(self) -> IndexGeneration | None:
        return self._active

    def file_hashes(self) -> dict[str, str]:
        return dict(self._sql.execute("SELECT path, hash FROM files"))

    def symbols_for_files(self, paths: Set[str]) -> list[GetSymbolOutput]:
        return [
            GetSymbolOutput.model_validate_json(payload)
            for file, payload in self._sql.execute("SELECT file, payload FROM symbols")
            if file in paths
        ]

    def symbol_names(self) -> set[str]:
        return {name for (name,) in self._sql.execute("SELECT qualname FROM symbols")}

    def symbol_file(self, qualname: str) -> str | None:
        row = self._sql.execute(
            "SELECT file FROM symbols WHERE qualname = ? ORDER BY file LIMIT 1",
            (qualname,),
        ).fetchone()
        if row is None:
            return None
        file: str = row[0]
        return file

    def lexical_candidates(self, query: str, kind: SymbolKind | None, limit: int) -> list[str]:
        return [
            symbol_id
            for (symbol_id,) in self._sql.execute(
                """SELECT id FROM symbols WHERE instr(lower(qualname), ?) > 0
                   AND (? IS NULL OR kind = ?)
                   ORDER BY lower(qualname) = ? DESC, length(qualname), qualname LIMIT ?""",
                (query, kind, kind, query, limit),
            )
        ]

    def publish(
        self,
        generation: IndexGeneration,
        hashes: Mapping[str, str],
        symbols: Sequence[GetSymbolOutput],
    ) -> None:
        with self._sql:
            self._sql.execute("DELETE FROM files")
            self._sql.execute("DELETE FROM symbols")
            self._sql.executemany("INSERT INTO files VALUES (?, ?)", hashes.items())
            self._sql.executemany(
                "INSERT INTO symbols VALUES (?, ?, ?, ?, ?)",
                (
                    (
                        f"{symbol.file}:{symbol.qualname}",
                        symbol.file,
                        symbol.qualname,
                        symbol.kind.value,
                        symbol.model_dump_json(),
                    )
                    for symbol in symbols
                ),
            )
            self._sql.executemany(
                "INSERT OR REPLACE INTO metadata VALUES (?, ?)",
                [
                    ("generation", generation.name),
                    ("model", generation.model),
                    ("version", generation.version),
                ],
            )
        self._active = generation

    def close(self) -> None:
        self._sql.close()
