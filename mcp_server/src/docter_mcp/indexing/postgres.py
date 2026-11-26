"""PostgreSQL metadata for indexes whose vector generations are stored separately."""

from collections.abc import Iterator, Mapping, Sequence, Set
from contextlib import contextmanager

import psycopg

from docter_contract import GetSymbolOutput, SymbolKind
from docter_mcp.indexing.metadata import IndexGeneration, MetadataPublishUncertainError

_SCHEMA = (
    """CREATE TABLE IF NOT EXISTS docter_index_manifest (
        index_key text COLLATE "C" PRIMARY KEY,
        generation text NOT NULL, model text, version text
    )""",
    """CREATE TABLE IF NOT EXISTS docter_index_files (
        index_key text COLLATE "C" NOT NULL, path text COLLATE "C" NOT NULL, hash text NOT NULL,
        PRIMARY KEY (index_key, path)
    )""",
    """CREATE TABLE IF NOT EXISTS docter_index_symbols (
        index_key text COLLATE "C" NOT NULL, id text COLLATE "C" NOT NULL,
        file text COLLATE "C" NOT NULL, qualname text COLLATE "C" NOT NULL,
        kind text COLLATE "C" NOT NULL, payload text NOT NULL,
        PRIMARY KEY (index_key, id)
    )""",
    """CREATE INDEX IF NOT EXISTS docter_index_symbol_names
       ON docter_index_symbols (index_key, qualname, file)""",
)


@contextmanager
def _redact_database_errors() -> Iterator[None]:
    try:
        yield
    except psycopg.Error as exc:
        raise RuntimeError(
            f"PostgreSQL index metadata failed ({type(exc).__name__}); "
            "check DOCTER_INDEX_METADATA_URL, connectivity, and database permissions."
        ) from None


class PostgresIndexMetadata:
    """Read a consistent snapshot and replace one index's metadata atomically."""

    def __init__(self, url: str, index_key: str, *, create: bool = False) -> None:
        self._key = index_key
        self._snapshot_open = False
        self._active: IndexGeneration | None = None
        with _redact_database_errors():
            self._sql = psycopg.connect(url, autocommit=True)
            if create:
                try:
                    self._ensure_schema()
                except BaseException:
                    self.close()
                    raise

    def _ensure_schema(self) -> None:
        if self._has_schema():
            return
        with self._sql.transaction():
            self._sql.execute("SET TRANSACTION ISOLATION LEVEL READ COMMITTED READ WRITE")
            self._sql.execute(
                "SELECT pg_advisory_xact_lock(hashtextextended('docter_index_schema_v1', 0))"
            )
            for ddl in _SCHEMA:
                self._sql.execute(ddl)

    def _has_schema(self) -> bool:
        row = self._sql.execute("SELECT to_regclass('docter_index_manifest')").fetchone()
        return row is not None and row[0] is not None

    def _begin_snapshot(self) -> None:
        if self._snapshot_open:
            return
        # The manifest and later lookups must see the same committed generation.
        self._sql.execute("BEGIN ISOLATION LEVEL REPEATABLE READ READ ONLY")
        self._snapshot_open = True
        row = (
            self._sql.execute(
                "SELECT generation, model, version FROM docter_index_manifest WHERE index_key = %s",
                (self._key,),
            ).fetchone()
            if self._has_schema()
            else None
        )
        self._active = IndexGeneration(*row) if row is not None else None

    @property
    def active_generation(self) -> IndexGeneration | None:
        with _redact_database_errors():
            self._begin_snapshot()
            return self._active

    def file_hashes(self) -> dict[str, str]:
        with _redact_database_errors():
            self._begin_snapshot()
            if self._active is None:
                return {}
            return dict(
                self._sql.execute(
                    "SELECT path, hash FROM docter_index_files WHERE index_key = %s", (self._key,)
                )
            )

    def symbols_for_files(self, paths: Set[str]) -> list[GetSymbolOutput]:
        if not paths:
            return []
        with _redact_database_errors():
            self._begin_snapshot()
            if self._active is None:
                return []
            return [
                GetSymbolOutput.model_validate_json(payload)
                for (payload,) in self._sql.execute(
                    "SELECT payload FROM docter_index_symbols "
                    "WHERE index_key = %s AND file = ANY(%s)",
                    (self._key, list(paths)),
                )
            ]

    def symbol_names(self) -> set[str]:
        with _redact_database_errors():
            self._begin_snapshot()
            if self._active is None:
                return set()
            return {
                name
                for (name,) in self._sql.execute(
                    "SELECT qualname FROM docter_index_symbols WHERE index_key = %s", (self._key,)
                )
            }

    def symbol_file(self, qualname: str) -> str | None:
        if "\0" in qualname:
            return None
        with _redact_database_errors():
            self._begin_snapshot()
            if self._active is None:
                return None
            row = self._sql.execute(
                """SELECT file FROM docter_index_symbols
                   WHERE index_key = %s AND qualname = %s ORDER BY file LIMIT 1""",
                (self._key, qualname),
            ).fetchone()
            return str(row[0]) if row is not None else None

    def lexical_candidates(self, query: str, kind: SymbolKind | None, limit: int) -> list[str]:
        if "\0" in query:
            return []
        with _redact_database_errors():
            self._begin_snapshot()
            if self._active is None:
                return []
            # SQLite lower() folds ASCII only; strpos is a literal, not a LIKE pattern.
            return [
                symbol_id
                for (symbol_id,) in self._sql.execute(
                    """SELECT id FROM (
                        SELECT id, qualname, kind, translate(
                            qualname, 'ABCDEFGHIJKLMNOPQRSTUVWXYZ', 'abcdefghijklmnopqrstuvwxyz'
                        ) AS folded
                        FROM docter_index_symbols WHERE index_key = %s
                    ) AS symbols
                    WHERE strpos(folded, %s) > 0 AND (%s::text IS NULL OR kind = %s)
                    ORDER BY (folded = %s) DESC, length(qualname), qualname LIMIT %s""",
                    (self._key, query, kind, kind, query, None if limit < 0 else limit),
                )
            ]

    def publish(
        self,
        generation: IndexGeneration,
        hashes: Mapping[str, str],
        symbols: Sequence[GetSymbolOutput],
    ) -> None:
        with _redact_database_errors():
            if self._snapshot_open:
                self._sql.execute("ROLLBACK")
                self._snapshot_open = False
            committing = False
            try:
                with self._sql.transaction(), self._sql.cursor() as cursor:
                    cursor.execute("SET TRANSACTION ISOLATION LEVEL READ COMMITTED READ WRITE")
                    # Serialize full replacements even before an index has its first manifest.
                    cursor.execute(
                        "SELECT pg_advisory_xact_lock(hashtextextended(%s, 0))", (self._key,)
                    )
                    cursor.execute(
                        "DELETE FROM docter_index_files WHERE index_key = %s", (self._key,)
                    )
                    cursor.execute(
                        "DELETE FROM docter_index_symbols WHERE index_key = %s", (self._key,)
                    )
                    cursor.executemany(
                        "INSERT INTO docter_index_files VALUES (%s, %s, %s)",
                        [(self._key, path, digest) for path, digest in hashes.items()],
                    )
                    cursor.executemany(
                        "INSERT INTO docter_index_symbols VALUES (%s, %s, %s, %s, %s, %s)",
                        [
                            (
                                self._key,
                                f"{symbol.file}:{symbol.qualname}",
                                symbol.file,
                                symbol.qualname,
                                symbol.kind.value,
                                symbol.model_dump_json(),
                            )
                            for symbol in symbols
                        ],
                    )
                    cursor.execute(
                        """INSERT INTO docter_index_manifest VALUES (%s, %s, %s, %s)
                           ON CONFLICT (index_key) DO UPDATE SET generation = EXCLUDED.generation,
                               model = EXCLUDED.model, version = EXCLUDED.version""",
                        (self._key, generation.name, generation.model, generation.version),
                    )
                    committing = True
            except (psycopg.OperationalError, psycopg.InterfaceError):
                if committing:
                    raise MetadataPublishUncertainError(
                        "PostgreSQL index commit could not be confirmed; staged vectors retained."
                    ) from None
                raise

    def close(self) -> None:
        self._sql.close()
