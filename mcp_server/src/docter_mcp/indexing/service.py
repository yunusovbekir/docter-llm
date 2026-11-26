"""Incremental indexing, hybrid symbol search, and fresh-source symbol retrieval."""

import hashlib
import logging
import time
from pathlib import PurePosixPath
from typing import Protocol

import anyio
from fastembed import TextEmbedding

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
from docter_mcp.indexing.vectors import VectorChunk
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
        generation = store.metadata.active_generation
        rebuild = (
            request.force
            or generation is None
            or generation.model != config.embedding_model
            or generation.version != IndexStore.VERSION
        )
        previous = {} if rebuild else store.metadata.file_hashes()
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
        embedded = (
            store.vectors.reuse(generation.name, retained)
            if not rebuild and generation is not None and generation.name
            else []
        )
        if not rebuild:
            symbols.extend(store.metadata.symbols_for_files(retained))
        await _progress(ctx, len(paths) + 1, total, "Embedding changed chunks")
        if chunks:
            vectors = await anyio.to_thread.run_sync(
                Embedder(config).documents,
                [f"{chunk.qualname or chunk.file}\n{chunk.snippet}" for chunk in chunks],
            )
            for chunk, vector in zip(chunks, vectors, strict=True):
                identifier = f"{chunk.file}:{chunk.qualname or f'section:{chunk.start_line}'}"
                embedded.append(VectorChunk(identifier, chunk, vector))
        if rebuild or changed or set(previous) != set(hashes):
            await anyio.to_thread.run_sync(
                store.publish, hashes, symbols, embedded, config.embedding_model
            )
        await _progress(ctx, total, total, "Index ready")
        return IndexRepoOutput(
            files_indexed=len(changed),
            symbols=len(symbols),
            chunks=len(embedded),
            duration_ms=int((time.monotonic() - started) * 1000),
        )


def _boost(qualname: str, query: str) -> float:
    name = qualname.casefold()
    if name == query or name.rsplit(".", 1)[-1] == query:
        return 2.0
    return 1.0 if query in name else 0.0


def search_code(request: SearchCodeInput, config: Config) -> SearchCodeOutput:
    """Combine cosine similarity with exact/partial symbol-name candidate boosting."""
    sandbox = Sandbox(request.repo, config.allowed_roots)
    with IndexStore(sandbox, config) as store:
        generation = store.metadata.active_generation
        if generation is None or generation.model != config.embedding_model:
            raise fail(ErrorCode.NOT_INDEXED, "Embedding model changed; run index_repo again.")
        if not generation.name:
            return SearchCodeOutput(results=[])
        vector = Embedder(config).query(request.query)
        query = request.query.casefold()
        matches = store.vectors.search(generation.name, vector, request.kind, request.k * 4)
        # Lexical candidates must be considered even when absent from the vector top-k.
        candidates = store.metadata.lexical_candidates(query, request.kind, request.k * 4)
        if candidates:
            matches.extend(store.vectors.score_ids(generation.name, vector, candidates))
        unique = {match.id: match.hit for match in matches}
        hits = [
            hit.model_copy(update={"score": hit.score + _boost(hit.qualname or "", query)})
            for hit in unique.values()
        ]
        hits.sort(key=lambda hit: (-hit.score, hit.file, hit.start_line, hit.qualname or ""))
        return SearchCodeOutput(results=hits[: request.k])


def get_symbol(request: GetSymbolInput, config: Config) -> GetSymbolOutput:
    """Locate an indexed qualname, then reparse its file for fresh source and metadata."""
    sandbox = Sandbox(request.repo, config.allowed_roots)
    with IndexStore(sandbox, config) as store:
        file = store.metadata.symbol_file(request.qualname)
        if file is None:
            raise fail(ErrorCode.SYMBOL_NOT_FOUND, f"No indexed symbol: {request.qualname}")
        source = sandbox.read_text(file)
        parsed = PythonParser().parse(file, source)
        for warning in parsed.warnings:
            logging.getLogger(__name__).warning("%s: %s", warning.path, warning.message)
        for symbol in parsed.symbols:
            if symbol.qualname == request.qualname:
                return symbol
        raise fail(
            ErrorCode.SYMBOL_NOT_FOUND,
            f"Symbol is no longer present on disk: {request.qualname}; run index_repo again.",
        )
