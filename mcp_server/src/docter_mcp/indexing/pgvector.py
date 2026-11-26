"""Exact cosine search over immutable PostgreSQL vector generations."""

from collections.abc import Iterator, Sequence, Set
from contextlib import contextmanager
from math import isfinite
from typing import Any, ClassVar

import psycopg

from docter_contract import SearchHit, SymbolKind
from docter_mcp.indexing.vectors import VectorChunk, VectorMatch


class PgVectorStore:
    """Keep vector generations independent of the metadata store that publishes them."""

    _MAX_DIMENSIONS = 16_000
    _SCHEMA: ClassVar[tuple[str, ...]] = (
        """CREATE TABLE IF NOT EXISTS docter_vector_generations (
            generation text COLLATE "C" PRIMARY KEY, dimensions integer NOT NULL
        )""",
        """CREATE TABLE IF NOT EXISTS docter_vector_chunks (
            generation text COLLATE "C" NOT NULL
                REFERENCES docter_vector_generations ON DELETE CASCADE,
            id text COLLATE "C" NOT NULL, file text COLLATE "C" NOT NULL,
            kind text COLLATE "C" NOT NULL, payload text NOT NULL, embedding vector NOT NULL,
            PRIMARY KEY (generation, id)
        )""",
    )

    def __init__(self, url: str) -> None:
        self._url = url

    @contextmanager
    def _connect(self) -> Iterator[psycopg.Connection[tuple[Any, ...]]]:
        try:
            connection = psycopg.connect(self._url, autocommit=True)
        except (psycopg.Error, ValueError) as exc:
            raise self._database_error(exc) from None
        try:
            with connection:
                if (
                    connection.execute(
                        """SELECT 1 FROM pg_extension AS e JOIN pg_type AS t
                         ON t.typnamespace = e.extnamespace
                       WHERE e.extname = 'vector' AND t.typname = 'vector'
                         AND pg_type_is_visible(t.oid)"""
                    ).fetchone()
                    is None
                ):
                    raise RuntimeError(
                        "pgvector requires the PostgreSQL vector extension in "
                        "DOCTER_INDEX_VECTOR_URL's database and search_path."
                    )
                yield connection
        except psycopg.Error as exc:
            raise self._database_error(exc) from None

    @staticmethod
    def _database_error(exc: Exception) -> RuntimeError:
        return RuntimeError(
            f"PostgreSQL vectors failed ({type(exc).__name__}); "
            "check DOCTER_INDEX_VECTOR_URL, connectivity, and database permissions."
        )

    @classmethod
    def _validate(cls, vector: list[float], dimensions: int) -> None:
        if not 1 <= dimensions <= cls._MAX_DIMENSIONS:
            raise ValueError(f"pgvector embedding dimensions must be 1-{cls._MAX_DIMENSIONS}.")
        if len(vector) != dimensions:
            raise ValueError(
                f"pgvector embedding dimension mismatch: expected {dimensions}, got {len(vector)}."
            )
        if not all(isfinite(value) for value in vector) or not any(vector):
            raise ValueError("pgvector cosine search requires finite, nonzero embeddings.")

    def stage(self, generation: str, chunks: Sequence[VectorChunk]) -> None:
        with self._connect() as db:
            if not generation:
                return
            dimensions = len(chunks[0].vector) if chunks else 0
            if not dimensions:
                raise ValueError("A pgvector generation requires at least one nonempty embedding.")
            for chunk in chunks:
                self._validate(chunk.vector, dimensions)
            with db.transaction(), db.cursor() as cursor:
                cursor.execute("SET TRANSACTION ISOLATION LEVEL READ COMMITTED READ WRITE")
                if db.execute("SELECT to_regclass('docter_vector_chunks')").fetchone() == (None,):
                    cursor.execute(
                        "SELECT pg_advisory_xact_lock(hashtextextended('docter_vector_schema', 0))"
                    )
                    for ddl in self._SCHEMA:
                        cursor.execute(ddl)
                cursor.execute(
                    "INSERT INTO docter_vector_generations VALUES (%s, %s)",
                    (generation, dimensions),
                )
                cursor.executemany(
                    """INSERT INTO docter_vector_chunks
                       VALUES (%s, %s, %s, %s, %s, %s::real[]::vector)""",
                    [
                        (
                            generation,
                            chunk.id,
                            chunk.hit.file,
                            chunk.hit.kind.value,
                            chunk.hit.model_copy(update={"score": 0}).model_dump_json(),
                            chunk.vector,
                        )
                        for chunk in chunks
                    ],
                )

    @staticmethod
    def _dimensions(db: psycopg.Connection[tuple[Any, ...]], generation: str) -> int:
        row = db.execute(
            "SELECT dimensions FROM docter_vector_generations WHERE generation = %s", (generation,)
        ).fetchone()
        if row is None:
            raise RuntimeError("pgvector generation is missing; run index_repo with force=true.")
        return int(row[0])

    def reuse(self, generation: str, paths: Set[str]) -> list[VectorChunk]:
        with self._connect() as db:
            self._dimensions(db, generation)
            return [
                VectorChunk(chunk_id, SearchHit.model_validate_json(payload), vector)
                for chunk_id, payload, vector in db.execute(
                    """SELECT id, payload, embedding::real[] FROM docter_vector_chunks
                       WHERE generation = %s AND file = ANY(%s)""",
                    (generation, list(paths)),
                )
            ]

    def search(
        self, generation: str, vector: list[float], kind: SymbolKind | None, limit: int
    ) -> list[VectorMatch]:
        return self._search(generation, vector, kind, None, limit)

    def score_ids(
        self, generation: str, vector: list[float], ids: Sequence[str]
    ) -> list[VectorMatch]:
        if not ids:
            return []
        return self._search(generation, vector, None, list(ids), len(ids))

    def _search(
        self,
        generation: str,
        vector: list[float],
        kind: SymbolKind | None,
        ids: list[str] | None,
        limit: int,
    ) -> list[VectorMatch]:
        with self._connect() as db:
            self._validate(vector, self._dimensions(db, generation))
            return [
                VectorMatch(
                    chunk_id,
                    SearchHit.model_validate_json(payload).model_copy(update={"score": score}),
                )
                for chunk_id, payload, score in db.execute(
                    """SELECT id, payload, 1 - (embedding <=> %s::real[]::vector) AS score
                       FROM docter_vector_chunks
                       WHERE generation = %s AND (%s::text IS NULL OR kind = %s)
                           AND (%s::text[] IS NULL OR id = ANY(%s))
                       ORDER BY score DESC, id LIMIT %s""",
                    (vector, generation, kind, kind, ids, ids, limit),
                )
            ]

    def discard(self, generation: str) -> None:
        with self._connect() as db:
            if db.execute("SELECT to_regclass('docter_vector_generations')").fetchone() != (None,):
                db.execute(
                    "DELETE FROM docter_vector_generations WHERE generation = %s", (generation,)
                )
