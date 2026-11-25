"""M4 uses real SQLite/LanceDB and deterministic local embeddings, without model downloads."""

import hashlib
import os
import re
import sqlite3
from collections.abc import Callable, Iterator
from contextlib import closing
from dataclasses import replace
from itertools import pairwise
from pathlib import Path

import numpy as np
import numpy.typing as npt
import pytest
from mcp.server.mcpserver.exceptions import ToolError
from test_foundation import connected, error_text

from docter_contract import (
    GetSymbolInput,
    GetSymbolOutput,
    IndexProgress,
    IndexRepoInput,
    IndexRepoOutput,
    SearchCodeInput,
    SearchCodeOutput,
    SymbolKind,
)
from docter_mcp.config import Config
from docter_mcp.indexing import service
from docter_mcp.indexing.chunks import doc_chunks
from docter_mcp.indexing.metadata import IndexGeneration, SQLiteIndexMetadata
from docter_mcp.indexing.service import Embedder, get_symbol, index_repo, search_code
from docter_mcp.indexing.store import IndexStore
from docter_mcp.parsing.python import PythonParser
from docter_mcp.sandbox import Sandbox


def vector(text: str) -> list[float]:
    result = [0.0] * 64
    for token in re.findall(r"[a-z]+", text.lower()):
        bucket = int(hashlib.sha256(token.encode()).hexdigest()[:8], 16) % len(result)
        result[bucket] += 1
    result[0] += 0.01
    return result


@pytest.fixture
def embeddings(monkeypatch: pytest.MonkeyPatch) -> list[list[str]]:
    calls: list[list[str]] = []

    def documents(self: Embedder, texts: list[str]) -> list[list[float]]:
        calls.append(texts)
        return [vector(text) for text in texts]

    def query(self: Embedder, text: str) -> list[float]:
        return vector(text)

    monkeypatch.setattr(Embedder, "documents", documents)
    monkeypatch.setattr(Embedder, "query", query)
    return calls


@pytest.fixture
def config(tmp_path: Path, embeddings: list[list[str]]) -> Config:
    return Config(index_dir=tmp_path / "index")


def snapshot(repo: Path) -> dict[str, bytes]:
    return {
        path.relative_to(repo).as_posix(): path.read_bytes()
        for path in repo.rglob("*")
        if path.is_file()
    }


async def test_full_index_is_persistent_and_read_only(
    repo: Path, config: Config, embeddings: list[list[str]], caplog: pytest.LogCaptureFixture
) -> None:
    before = snapshot(repo)
    output = await index_repo(IndexRepoInput(repo=str(repo)), config)
    sandbox = Sandbox(repo)
    expected_symbols = [
        symbol
        for path in sandbox.files()
        if path.endswith(".py")
        for symbol in PythonParser().parse(path, sandbox.read_text(path)).symbols
    ]
    expected_docs = [
        chunk
        for path in sandbox.files()
        if path.endswith((".md", ".rst"))
        for chunk in doc_chunks(path, sandbox.read_text(path))
    ]
    assert output.files_indexed == 8
    assert output.symbols == len(expected_symbols)
    assert output.chunks == output.symbols + len(expected_docs)
    assert output.duration_ms >= 0
    assert len(embeddings) == 1
    assert len(embeddings[0]) == output.chunks
    assert "broken.py" in caplog.text
    digest = hashlib.sha1(str(repo).encode(), usedforsecurity=False).hexdigest()
    assert (config.index_dir / digest / "symbols.sqlite3").is_file()
    assert (config.index_dir / digest / "vectors").is_dir()
    with IndexStore(sandbox, config) as store:
        assert len(store.metadata.symbols_for_files(store.metadata.file_hashes().keys())) == (
            output.symbols
        )
        table = store.table()
        assert table is not None
        assert table.count_rows() == output.chunks
    symbol = get_symbol(GetSymbolInput(repo=str(repo), qualname="mylib.client.Client.send"), config)
    assert symbol.signature.startswith("def send(")
    assert symbol.source == next(
        expected.source for expected in expected_symbols if expected.qualname == symbol.qualname
    )
    assert snapshot(repo) == before


async def test_incremental_hashes_changes_deletions_and_force(
    repo: Path, config: Config, embeddings: list[list[str]]
) -> None:
    request = IndexRepoInput(repo=str(repo))
    first = await index_repo(request, config)
    with IndexStore(Sandbox(repo), config) as store:
        generation = store.metadata.active_generation
    second = await index_repo(request, config)
    assert second.files_indexed == 0
    assert (second.symbols, second.chunks) == (first.symbols, first.chunks)
    assert len(embeddings) == 1
    with IndexStore(Sandbox(repo), config) as store:
        assert store.metadata.active_generation == generation
    path = repo / "src" / "mylib" / "added.py"
    path.write_text("def added():\n    return 42\n")
    added = await index_repo(request, config)
    assert added.files_indexed == 1
    assert added.symbols == first.symbols + 2
    assert len(embeddings[-1]) == 2
    assert all("mylib.added" in text for text in embeddings[-1])
    stat = path.stat()
    path.write_text("def other():\n    return 42\n")
    # Same size and timestamps must not defeat content-hash detection.
    os.utime(path, ns=(stat.st_atime_ns, stat.st_mtime_ns))
    changed = await index_repo(request, config)
    assert changed.files_indexed == 1
    with pytest.raises(ToolError, match="SYMBOL_NOT_FOUND"):
        get_symbol(GetSymbolInput(repo=str(repo), qualname="mylib.added.added"), config)
    assert get_symbol(
        GetSymbolInput(repo=str(repo), qualname="mylib.added.other"), config
    ).source.startswith("def other")
    path.unlink()
    calls = len(embeddings)
    removed = await index_repo(request, config)
    assert removed.files_indexed == 0
    assert (removed.symbols, removed.chunks) == (first.symbols, first.chunks)
    assert len(embeddings) == calls
    forced = await index_repo(request.model_copy(update={"force": True}), config)
    assert forced.files_indexed == first.files_indexed
    assert len(embeddings[-1]) == first.chunks
    assert forced.chunks == first.chunks


async def test_ignore_rules_nested_negation_and_removed_ignored_files(
    repo: Path, config: Config
) -> None:
    (repo / ".gitignore").write_text("*.py\n!src/\n!src/mylib/\n!src/mylib/client.py\ncache/\n")
    (repo / "src" / "mylib" / ".gitignore").write_text("!legacy.py\n/client.py\n")
    cache = repo / "cache"
    cache.mkdir()
    (cache / "invalid.py").write_bytes(b"\xff")
    (repo / ".git" / "secret.md").write_bytes(b"\xff")
    result = await index_repo(IndexRepoInput(repo=str(repo)), config)
    assert result.files_indexed == 4  # Three doc files and the re-included legacy module.
    with IndexStore(Sandbox(repo), config) as store:
        assert set(store.metadata.file_hashes()) == {
            "README.md",
            "docs/index.md",
            "docs/reference.rst",
            "src/mylib/legacy.py",
        }
    (repo / "src" / "mylib" / ".gitignore").write_text("*.py\n")
    await index_repo(IndexRepoInput(repo=str(repo)), config)
    with IndexStore(Sandbox(repo), config) as store:
        assert not store.metadata.symbol_names()


@pytest.mark.parametrize(
    ("source", "suffix", "expected"),
    [
        ("# A\nintro\n## B\nbody\n", ".md", [(1, 2), (3, 4)]),
        ("Intro\n\nTitle\n=====\nbody\n", ".md", [(1, 2), (3, 5)]),
        ("Title\n=====\n\nPart\n----\nbody\n", ".rst", [(1, 3), (4, 6)]),
        ("=====\nTitle\n=====\nbody\n", ".rst", [(1, 4)]),
        ("# A\n```python\n# not a heading\n```\n## B\n", ".md", [(1, 4), (5, 5)]),
        ("# A\n~~~~\n## fenced\n~~~\n## still fenced\n~~~~\n", ".md", [(1, 6)]),
        ("no heading\n", ".md", [(1, 1)]),
        ("", ".md", []),
    ],
)
def test_document_heading_chunks(source: str, suffix: str, expected: list[tuple[int, int]]) -> None:
    chunks = doc_chunks(f"docs/page{suffix}", source)
    assert [(chunk.start_line, chunk.end_line) for chunk in chunks] == expected
    assert "".join(chunk.snippet for chunk in chunks) == source
    assert all(chunk.qualname is None and chunk.kind is SymbolKind.MODULE for chunk in chunks)


async def test_search_relevance_kind_and_symbol_boost(repo: Path, config: Config) -> None:
    await index_repo(IndexRepoInput(repo=str(repo)), config)
    for query in ["mylib.client.Client.send", "send", "Client.sen"]:
        results = search_code(SearchCodeInput(repo=str(repo), query=query, k=3), config).results
        assert results[0].qualname == "mylib.client.Client.send"
        assert results[0].score > results[-1].score
        assert len(results) == 3
    hits = search_code(
        SearchCodeInput(repo=str(repo), query="Fetch a resource", kind=SymbolKind.METHOD, k=3),
        config,
    ).results
    assert hits[0].qualname == "mylib.client.Client.fetch"
    assert all(hit.kind is SymbolKind.METHOD for hit in hits)
    assert hits[0].snippet.startswith("    async def fetch")
    docs = search_code(
        SearchCodeInput(repo=str(repo), query="API reference", kind=SymbolKind.MODULE, k=50),
        config,
    ).results
    assert any(hit.qualname is None and hit.file == "docs/reference.rst" for hit in docs)
    assert all(hit.kind is SymbolKind.MODULE for hit in docs)


async def test_lexical_candidates_outside_vector_top_k(
    repo: Path, config: Config, monkeypatch: pytest.MonkeyPatch
) -> None:
    (repo / "many.py").write_text(
        "".join(f"def irrelevant_{index}():\n    pass\n" for index in range(30))
        + "def needle():\n    pass\n"
    )
    await index_repo(IndexRepoInput(repo=str(repo)), config)

    def opposite(self: Embedder, text: str) -> list[float]:
        return [-value for value in vector("many.needle\ndef needle():\n    pass\n")]

    monkeypatch.setattr(Embedder, "query", opposite)
    hits = search_code(SearchCodeInput(repo=str(repo), query="needle", k=1), config).results
    assert hits[0].qualname == "many.needle"


async def test_get_symbol_reads_fresh_source_and_metadata(repo: Path, config: Config) -> None:
    await index_repo(IndexRepoInput(repo=str(repo)), config)
    request = GetSymbolInput(repo=str(repo), qualname="mylib.client.ping")
    path = repo / "src" / "mylib" / "client.py"
    source = path.read_text()
    path.write_text(
        "\n\n" + source.replace("def ping(host, timeout=None):", "def ping(host: str):")
    )
    symbol = get_symbol(request, config)
    assert symbol.signature == "def ping(host: str)"
    assert symbol.parameters[0].annotation == "str"
    assert symbol.source == "def ping(host: str):\n    return True\n"
    assert symbol.start_line == source[: source.index("def ping(")].count("\n") + 3
    path.write_text(source.replace("def ping(", "def renamed("))
    with pytest.raises(ToolError, match="SYMBOL_NOT_FOUND"):
        get_symbol(request, config)
    path.unlink()
    with pytest.raises(ToolError, match="FILE_NOT_FOUND"):
        get_symbol(request, config)


async def test_empty_repo_and_not_indexed(repo: Path, config: Config, tmp_path: Path) -> None:
    actions: list[Callable[[], object]] = [
        lambda: search_code(SearchCodeInput(repo=str(repo), query="send"), config),
        lambda: get_symbol(GetSymbolInput(repo=str(repo), qualname="missing"), config),
    ]
    for action in actions:
        with pytest.raises(ToolError, match="NOT_INDEXED"):
            action()
    assert not config.index_dir.exists()
    (repo / ".gitignore").write_text("*\n")
    output = await index_repo(IndexRepoInput(repo=str(repo)), config)
    assert output.files_indexed == output.symbols == output.chunks == 0
    assert search_code(SearchCodeInput(repo=str(repo), query="anything"), config).results == []
    with pytest.raises(ToolError, match="SYMBOL_NOT_FOUND"):
        get_symbol(GetSymbolInput(repo=str(repo), qualname="missing"), config)


async def test_model_change_rebuilds(
    repo: Path, config: Config, embeddings: list[list[str]]
) -> None:
    first = await index_repo(IndexRepoInput(repo=str(repo)), config)
    changed = replace(config, embedding_model="another-model")
    with pytest.raises(ToolError, match="NOT_INDEXED"):
        search_code(SearchCodeInput(repo=str(repo), query="send"), changed)
    second = await index_repo(IndexRepoInput(repo=str(repo)), changed)
    assert second.files_indexed == first.files_indexed
    assert len(embeddings) == 2


async def test_failed_refresh_preserves_published_index(
    repo: Path, config: Config, monkeypatch: pytest.MonkeyPatch
) -> None:
    await index_repo(IndexRepoInput(repo=str(repo)), config)
    (repo / "new.py").write_text("def new(): pass\n")

    def broken(self: Embedder, texts: list[str]) -> list[list[float]]:
        raise RuntimeError("Embedding failed")

    monkeypatch.setattr(Embedder, "documents", broken)
    with pytest.raises(RuntimeError, match="Embedding failed"):
        await index_repo(IndexRepoInput(repo=str(repo)), config)
    assert get_symbol(
        GetSymbolInput(repo=str(repo), qualname="mylib.client.Client.send"), config
    ).qualname.endswith("Client.send")
    assert search_code(SearchCodeInput(repo=str(repo), query="send", k=1), config).results
    with pytest.raises(ToolError, match="SYMBOL_NOT_FOUND"):
        get_symbol(GetSymbolInput(repo=str(repo), qualname="new.new"), config)


async def test_failed_manifest_commit_keeps_previous_generation(repo: Path, config: Config) -> None:
    await index_repo(IndexRepoInput(repo=str(repo)), config)
    with IndexStore(Sandbox(repo), config) as store:
        generation = store.metadata.active_generation
        assert generation is not None
        with closing(sqlite3.connect(store.path / "symbols.sqlite3")) as db:
            db.execute(
                """CREATE TRIGGER reject_refresh BEFORE INSERT ON metadata
                   BEGIN SELECT RAISE(FAIL, 'manifest write failed'); END"""
            )
            db.commit()
        table = store.table()
        assert table is not None
        rows = table.to_arrow().to_pylist()
        hashes = store.metadata.file_hashes()
        names = store.metadata.symbol_names()
        vector_dir = store.path / "vectors"
        tables = set(vector_dir.glob("*.lance"))
        assert vector_dir / f"{generation.name}.lance" in tables
        with pytest.raises(sqlite3.IntegrityError, match="manifest write failed"):
            store.publish({"new.py": "replacement"}, [], rows, config.embedding_model)
        assert store.metadata.active_generation == generation
        assert set(vector_dir.glob("*.lance")) == tables
    with IndexStore(Sandbox(repo), config) as store:
        assert store.metadata.active_generation == generation
        assert store.metadata.file_hashes() == hashes
        assert store.metadata.symbol_names() == names


def test_metadata_reads_and_updates_version_one_database(tmp_path: Path) -> None:
    database = tmp_path / "symbols.sqlite3"
    symbol = next(
        symbol
        for symbol in PythonParser().parse("a.py", "def send(): pass\n").symbols
        if symbol.kind is SymbolKind.FUNCTION
    )
    with closing(sqlite3.connect(database)) as db:
        db.executescript(
            """
            CREATE TABLE metadata (key TEXT PRIMARY KEY, value TEXT NOT NULL);
            CREATE TABLE files (path TEXT PRIMARY KEY, hash TEXT NOT NULL);
            CREATE TABLE symbols (
                id TEXT PRIMARY KEY, file TEXT NOT NULL, qualname TEXT NOT NULL,
                kind TEXT NOT NULL, payload TEXT NOT NULL
            );
            CREATE INDEX symbol_names ON symbols(qualname);
            INSERT INTO metadata VALUES ('generation', 'chunks_existing'), ('model', 'm'),
                ('version', '1');
            INSERT INTO files VALUES ('a.py', 'hash');
            """
        )
        payload = symbol.model_dump_json()
        db.executemany(
            "INSERT INTO symbols VALUES (?, ?, ?, ?, ?)",
            [
                ("b.py:a.send", "b.py", "a.send", "function", payload),
                ("a.py:a.send", "a.py", "a.send", "function", payload),
                ("c.py:send", "c.py", "send", "class", payload),
            ],
        )
        db.commit()
        schema = db.execute("SELECT * FROM sqlite_schema ORDER BY name").fetchall()

    metadata = SQLiteIndexMetadata(database)
    try:
        assert metadata.active_generation == IndexGeneration("chunks_existing", "m", "1")
        assert metadata.file_hashes() == {"a.py": "hash"}
        assert metadata.symbol_names() == {"a.send", "send"}
        assert metadata.symbol_file("a.send") == "a.py"
        assert metadata.symbol_file("A.SEND") is None
        assert metadata.symbols_for_files({"a.py"}) == [symbol]
        assert metadata.lexical_candidates("send", None, 1) == ["c.py:send"]
        assert set(metadata.lexical_candidates("send", SymbolKind.FUNCTION, 3)) == {
            "a.py:a.send",
            "b.py:a.send",
        }
        metadata.publish(IndexGeneration("", "m", "1"), {}, [])
    finally:
        metadata.close()

    with closing(sqlite3.connect(database)) as db:
        assert db.execute("SELECT * FROM sqlite_schema ORDER BY name").fetchall() == schema
        assert dict(db.execute("SELECT key, value FROM metadata")) == {
            "generation": "",
            "model": "m",
            "version": "1",
        }
        assert db.execute("SELECT * FROM files").fetchall() == []
        assert db.execute("SELECT * FROM symbols").fetchall() == []


async def test_first_index_failure_is_not_indexed(
    repo: Path, config: Config, monkeypatch: pytest.MonkeyPatch
) -> None:
    def broken(self: Embedder, texts: list[str]) -> list[list[float]]:
        raise RuntimeError("Embedding failed")

    monkeypatch.setattr(Embedder, "documents", broken)
    with pytest.raises(RuntimeError, match="Embedding failed"):
        await index_repo(IndexRepoInput(repo=str(repo)), config)
    with pytest.raises(ToolError, match="NOT_INDEXED"):
        search_code(SearchCodeInput(repo=str(repo), query="send"), config)
    with pytest.raises(ToolError, match="NOT_INDEXED"):
        get_symbol(GetSymbolInput(repo=str(repo), qualname="mylib.client.Client.send"), config)


async def test_sandbox_rejections_and_ignored_escaping_symlink(
    repo: Path, config: Config, tmp_path: Path
) -> None:
    outside = tmp_path / "outside.py"
    outside.write_text("private\n")
    (repo / "escape.py").symlink_to(outside)
    with pytest.raises(ToolError, match="PATH_OUTSIDE_REPO"):
        await index_repo(IndexRepoInput(repo=str(repo)), config)
    (repo / ".gitignore").write_text("escape.py\n")
    await index_repo(IndexRepoInput(repo=str(repo)), config)
    with pytest.raises(ToolError, match="PATH_OUTSIDE_REPO"):
        await index_repo(IndexRepoInput(repo=str(repo)), replace(config, index_dir=repo / "index"))
    with pytest.raises(ToolError, match="PATH_OUTSIDE_REPO"):
        search_code(
            SearchCodeInput(repo=str(repo), query="send"),
            replace(config, allowed_roots=(tmp_path / "elsewhere",)),
        )


async def test_mcp_tools_and_progress_events(repo: Path, config: Config) -> None:
    events: list[IndexProgress] = []

    async def progress(progress: float, total: float | None, message: str | None) -> None:
        events.append(IndexProgress(progress=progress, total=total, message=message))

    async with connected(repo, config) as session:
        not_indexed = await session.call_tool("search_code", {"repo": str(repo), "query": "send"})
        assert "NOT_INDEXED" in error_text(not_indexed)
        result = await session.call_tool(
            "index_repo", {"repo": str(repo)}, progress_callback=progress
        )
        assert not result.is_error, result
        indexed = IndexRepoOutput.model_validate(result.structured_content)
        assert indexed.symbols > 0
        found = await session.call_tool(
            "search_code", {"repo": str(repo), "query": "send", "kind": "method", "k": 1}
        )
        assert not found.is_error, found
        assert SearchCodeOutput.model_validate(found.structured_content).results[0].qualname == (
            "mylib.client.Client.send"
        )
        symbol = await session.call_tool(
            "get_symbol", {"repo": str(repo), "qualname": "mylib.client.Client.send"}
        )
        assert not symbol.is_error, symbol
        assert GetSymbolOutput.model_validate(symbol.structured_content).kind is SymbolKind.METHOD
    assert len(events) == indexed.files_indexed + 3
    assert events[0].progress == 0
    assert events[-1].progress == events[-1].total
    assert all(event.message for event in events)
    assert all(a.progress < b.progress for a, b in pairwise(events))


def test_fastembed_adapter_uses_configured_model_and_cache(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls: list[tuple[str, str]] = []

    class LocalModel:
        def __init__(self, *, model_name: str, cache_dir: str) -> None:
            calls.append((model_name, cache_dir))

        def passage_embed(self, texts: list[str]) -> Iterator[npt.NDArray[np.float32]]:
            for text in texts:
                yield np.asarray(vector(text), dtype=np.float32)

        def query_embed(self, text: str) -> Iterator[npt.NDArray[np.float32]]:
            yield np.asarray(vector(text), dtype=np.float32)

    monkeypatch.setattr(service, "TextEmbedding", LocalModel)
    config = Config(index_dir=tmp_path / "index", embedding_model="local-model")
    embedder = Embedder(config)
    assert calls == []
    assert len(embedder.documents(["send", "fetch"])) == 2
    assert len(embedder.query("send")) == 64
    assert calls == [("local-model", str(config.index_dir / "models"))]


def test_config_embedding_and_index_alias(tmp_path: Path) -> None:
    config = Config.from_env(
        {"DOCSMITH_INDEX_DIR": str(tmp_path), "DOCTER_EMBEDDING_MODEL": "custom-model"}
    )
    assert config.index_dir == tmp_path
    assert config.embedding_model == "custom-model"
    assert (
        Config.from_env(
            {"DOCSMITH_INDEX_DIR": str(tmp_path), "DOCTER_INDEX_DIR": str(tmp_path / "preferred")}
        ).index_dir
        == tmp_path / "preferred"
    )
    with pytest.raises(ValueError, match="DOCTER_EMBEDDING_MODEL"):
        Config.from_env({"DOCTER_EMBEDDING_MODEL": " "})
