"""Incremental indexing, hybrid symbol search, and fresh-source symbol retrieval."""

import hashlib
import logging
import time
from collections.abc import Iterable
from pathlib import PurePosixPath
from typing import Any, Protocol

import anyio
from fastembed import TextEmbedding
from lancedb.query import LanceVectorQueryBuilder
from lancedb.table import Table
from mcp.server.mcpserver.exceptions import ToolError

from docter_contract import (
    ErrorCode,
    GetSymbolInput,
    GetSymbolOutput,
    IndexProgress,
    IndexRepoInput,
    IndexRepoOutput,
    SearchCodeInput,
    SearchCodeOutput,
    SearchHit,
)
from docter_mcp.config import Config
from docter_mcp.indexing.chunks import doc_chunks
from docter_mcp.indexing.store import IndexStore
from docter_mcp.parsing.python import PythonParser
from docter_mcp.sandbox import Sandbox, fail


class ProgressContext(Protocol):
    """The subset of MCP Context needed to emit contract progress events."""

    async def report_progress(
        self, progress: float, total: float | None = None, message: str | None = None
    ) -> None: ...


class Embedder:
    """Lazy local fastembed adapter; model artifacts live only in index storage."""

    def __init__(self, config: Config) -> None:
        self.config = config
        self._model: TextEmbedding | None = None

    def _load(self) -> TextEmbedding:
        if self._model is None:
            self._model = TextEmbedding(
                model_name=self.config.embedding_model,
                cache_dir=str(self.config.index_dir.expanduser().resolve() / "models"),
            )
        return self._model

    def documents(self, texts: list[str]) -> list[list[float]]:
        return [vector.tolist() for vector in self._load().passage_embed(texts)]

    def query(self, text: str) -> list[float]:
        return [float(value) for value in next(iter(self._load().query_embed(text)))]


def _chunks(path: str, source: str) -> tuple[list[GetSymbolOutput], list[SearchHit]]:
    if PurePosixPath(path).suffix.lower() != ".py":
        return [], doc_chunks(path, source)
    parsed = PythonParser().parse(path, source)
    for warning in parsed.warnings:
        logging.getLogger(__name__).warning(
            "%s:%s: %s", warning.path, warning.line, warning.message
        )
    return list(parsed.symbols), [
        SearchHit(
            file=symbol.file,
            qualname=symbol.qualname,
            kind=symbol.kind,
            start_line=symbol.start_line,
            end_line=symbol.end_line,
            snippet=symbol.source,
            score=0,
        )
        for symbol in parsed.symbols
    ]


async def _progress(ctx: ProgressContext | None, progress: int, total: int, message: str) -> None:
    event = IndexProgress(progress=progress, total=total, message=message)
    if ctx is not None:
        await ctx.report_progress(event.progress, event.total, event.message)


async def index_repo(
    request: IndexRepoInput, config: Config, ctx: ProgressContext | None = None
) -> IndexRepoOutput:
    """Index changed files only, publishing a complete generation after all work succeeds."""
    started = time.monotonic()
    sandbox = Sandbox(request.repo, config.allowed_roots)
    with IndexStore(sandbox, config, create=True) as store:
        paths = [
            path
            for path in sandbox.files(respect_gitignore=True)
            if PurePosixPath(path).suffix.lower() in {".py", ".md", ".rst"}
        ]
        total = len(paths) + 2
        await _progress(ctx, 0, total, "Scanning repository")
        rebuild = (
            request.force
            or store.metadata.get("model") != config.embedding_model
            or store.metadata.get("version") != IndexStore.VERSION
        )
        previous = {} if rebuild else dict(store.sql.execute("SELECT path, hash FROM files"))
        hashes: dict[str, str] = {}
        changed: set[str] = set()
        symbols: list[GetSymbolOutput] = []
        chunks: list[SearchHit] = []
        for number, path in enumerate(paths, 1):
            source = sandbox.read_text(path)
            digest = hashlib.sha256(source.encode()).hexdigest()
            hashes[path] = digest
            if previous.get(path) != digest:
                changed.add(path)
                parsed, pieces = _chunks(path, source)
                symbols.extend(parsed)
                chunks.extend(pieces)
            await _progress(ctx, number, total, f"Scanned {path}")
        retained = set(hashes) - changed
        old_table = None if rebuild else store.table()
        rows: list[dict[str, Any]] = (
            [row for row in old_table.to_arrow().to_pylist() if row["file"] in retained]
            if old_table is not None
            else []
        )
        if not rebuild:
            symbols.extend(
                GetSymbolOutput.model_validate_json(payload)
                for file, payload in store.sql.execute("SELECT file, payload FROM symbols")
                if file in retained
            )
        await _progress(ctx, len(paths) + 1, total, "Embedding changed chunks")
        if chunks:
            vectors = await anyio.to_thread.run_sync(
                Embedder(config).documents,
                [f"{chunk.qualname or chunk.file}\n{chunk.snippet}" for chunk in chunks],
            )
            for chunk, vector in zip(chunks, vectors, strict=True):
                rows.append(
                    {
                        "id": f"{chunk.file}:{chunk.qualname or f'section:{chunk.start_line}'}",
                        **chunk.model_dump(exclude={"score"}),
                        "qualname": chunk.qualname or "",
                        "vector": vector,
                    }
                )
        if rebuild or changed or set(previous) != set(hashes):
            await anyio.to_thread.run_sync(
                store.publish, hashes, symbols, rows, config.embedding_model
            )
        await _progress(ctx, total, total, "Index ready")
        return IndexRepoOutput(
            files_indexed=len(changed),
            symbols=len(symbols),
            chunks=len(rows),
            duration_ms=int((time.monotonic() - started) * 1000),
        )


def _boost(qualname: str, query: str) -> float:
    name = qualname.casefold()
    if name == query or name.rsplit(".", 1)[-1] == query:
        return 2.0
    return 1.0 if query in name else 0.0


def _hits(rows: Iterable[dict[str, Any]], query: str) -> list[SearchHit]:
    unique = {row["id"]: row for row in rows}
    return [
        SearchHit(
            file=row["file"],
            qualname=row["qualname"] or None,
            kind=row["kind"],
            start_line=row["start_line"],
            end_line=row["end_line"],
            snippet=row["snippet"],
            score=1.0 - float(row["_distance"]) + _boost(row["qualname"], query),
        )
        for row in unique.values()
    ]


def _vector_search(table: Table, vector: list[float]) -> LanceVectorQueryBuilder:
    builder = table.search(vector, query_type="vector")
    if not isinstance(builder, LanceVectorQueryBuilder):
        raise ToolError("LanceDB did not create a vector search query.")
    return builder.distance_type("cosine")


def search_code(request: SearchCodeInput, config: Config) -> SearchCodeOutput:
    """Combine cosine similarity with exact/partial symbol-name candidate boosting."""
    sandbox = Sandbox(request.repo, config.allowed_roots)
    with IndexStore(sandbox, config) as store:
        if store.metadata.get("model") != config.embedding_model:
            raise fail(ErrorCode.NOT_INDEXED, "Embedding model changed; run index_repo again.")
        table = store.table()
        if table is None:
            return SearchCodeOutput(results=[])
        vector = Embedder(config).query(request.query)
        query = request.query.casefold()
        condition = f"kind = '{request.kind.value}'" if request.kind is not None else "true"
        rows = (
            _vector_search(table, vector)
            .where(condition, prefilter=True)
            .limit(request.k * 4)
            .to_list()
        )
        # Lexical candidates must be considered even when absent from the vector top-k.
        candidates = store.sql.execute(
            """SELECT id FROM symbols WHERE instr(lower(qualname), ?) > 0
               AND (? IS NULL OR kind = ?)
               ORDER BY lower(qualname) = ? DESC, length(qualname), qualname LIMIT ?""",
            (query, request.kind, request.kind, query, request.k * 4),
        ).fetchall()
        if candidates:
            ids = ", ".join("'" + item[0].replace("'", "''") + "'" for item in candidates)
            rows.extend(
                _vector_search(table, vector)
                .where(f"id IN ({ids})", prefilter=True)
                .limit(len(candidates))
                .to_list()
            )
        hits = _hits(rows, query)
        hits.sort(key=lambda hit: (-hit.score, hit.file, hit.start_line, hit.qualname or ""))
        return SearchCodeOutput(results=hits[: request.k])


def get_symbol(request: GetSymbolInput, config: Config) -> GetSymbolOutput:
    """Locate a qualname in SQLite, then reparse its file to return fresh source/metadata."""
    sandbox = Sandbox(request.repo, config.allowed_roots)
    with IndexStore(sandbox, config) as store:
        found = store.sql.execute(
            "SELECT file FROM symbols WHERE qualname = ? ORDER BY file LIMIT 1",
            (request.qualname,),
        ).fetchone()
        if found is None:
            raise fail(ErrorCode.SYMBOL_NOT_FOUND, f"No indexed symbol: {request.qualname}")
        source = sandbox.read_text(found[0])
        parsed = PythonParser().parse(found[0], source)
        for warning in parsed.warnings:
            logging.getLogger(__name__).warning("%s: %s", warning.path, warning.message)
        for symbol in parsed.symbols:
            if symbol.qualname == request.qualname:
                return symbol
        raise fail(
            ErrorCode.SYMBOL_NOT_FOUND,
            f"Symbol is no longer present on disk: {request.qualname}; run index_repo again.",
        )
