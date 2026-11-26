"""Vector operations used by indexing and the local LanceDB implementation."""

from collections.abc import Sequence, Set
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol

from lancedb.db import LanceDBConnection
from lancedb.query import LanceVectorQueryBuilder
from mcp.server.mcpserver.exceptions import ToolError

from docter_contract import SearchHit, SymbolKind


@dataclass(frozen=True, slots=True)
class VectorChunk:
    id: str
    hit: SearchHit
    vector: list[float]


@dataclass(frozen=True, slots=True)
class VectorMatch:
    id: str
    hit: SearchHit  # score is cosine similarity, before lexical boosting


class VectorStore(Protocol):
    """Store immutable vector generations; metadata decides which one is active."""

    def stage(self, generation: str, chunks: Sequence[VectorChunk]) -> None: ...

    def reuse(self, generation: str, paths: Set[str]) -> list[VectorChunk]: ...

    def search(
        self, generation: str, vector: list[float], kind: SymbolKind | None, limit: int
    ) -> list[VectorMatch]: ...

    def score_ids(
        self, generation: str, vector: list[float], ids: Sequence[str]
    ) -> list[VectorMatch]: ...

    def discard(self, generation: str) -> None: ...


def create_vector_store(provider: str, path: Path, *, url: str | None = None) -> VectorStore:
    """Create the selected vector store."""
    if provider == "lancedb":
        return LanceVectorStore(path)
    if provider == "pgvector":
        if not url:
            raise ValueError("The pgvector provider requires DOCTER_INDEX_VECTOR_URL.")
        try:
            from docter_mcp.indexing.pgvector import PgVectorStore
        except ModuleNotFoundError as exc:
            if exc.name != "psycopg":
                raise
            raise RuntimeError(
                "pgvector requires the optional psycopg driver. Run `uv sync --group postgres`."
            ) from None
        return PgVectorStore(url)
    raise ValueError(f"Unsupported vector provider {provider!r}; expected 'lancedb' or 'pgvector'.")


def _hit(row: dict[str, Any], score: float) -> SearchHit:
    return SearchHit(
        file=row["file"],
        qualname=row["qualname"] or None,
        kind=row["kind"],
        start_line=row["start_line"],
        end_line=row["end_line"],
        snippet=row["snippet"],
        score=score,
    )


class LanceVectorStore:
    """Read and write the existing LanceDB generation layout."""

    def __init__(self, path: Path) -> None:
        self._path = path

    def stage(self, generation: str, chunks: Sequence[VectorChunk]) -> None:
        connection = LanceDBConnection(self._path)
        if generation:
            connection.create_table(
                generation,
                [
                    {
                        "id": chunk.id,
                        **chunk.hit.model_dump(exclude={"score"}),
                        "qualname": chunk.hit.qualname or "",
                        "vector": chunk.vector,
                    }
                    for chunk in chunks
                ],
            )

    def reuse(self, generation: str, paths: Set[str]) -> list[VectorChunk]:
        table = LanceDBConnection(self._path).open_table(generation)
        return [
            VectorChunk(row["id"], _hit(row, 0), row["vector"])
            for row in table.to_arrow().to_pylist()
            if row["file"] in paths
        ]

    def search(
        self, generation: str, vector: list[float], kind: SymbolKind | None, limit: int
    ) -> list[VectorMatch]:
        condition = f"kind = '{kind.value}'" if kind is not None else "true"
        return self._search(generation, vector, condition, limit)

    def score_ids(
        self, generation: str, vector: list[float], ids: Sequence[str]
    ) -> list[VectorMatch]:
        if not ids:
            return []
        literals = ", ".join("'" + item.replace("'", "''") + "'" for item in ids)
        return self._search(generation, vector, f"id IN ({literals})", len(ids))

    def _search(
        self, generation: str, vector: list[float], condition: str, limit: int
    ) -> list[VectorMatch]:
        table = LanceDBConnection(self._path).open_table(generation)
        builder = table.search(vector, query_type="vector")
        if not isinstance(builder, LanceVectorQueryBuilder):
            raise ToolError("LanceDB did not create a vector search query.")
        rows = builder.distance_type("cosine").where(condition, prefilter=True).limit(limit)
        return [
            VectorMatch(row["id"], _hit(row, 1.0 - float(row["_distance"])))
            for row in rows.to_list()
        ]

    def discard(self, generation: str) -> None:
        LanceDBConnection(self._path).drop_table(generation, ignore_missing=True)
